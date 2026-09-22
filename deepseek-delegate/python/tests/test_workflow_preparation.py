"""Continuation admission and fixed handoffs with disposable real Git checkouts."""
import itertools
import json
import os
import subprocess
import uuid
from pathlib import Path
from unittest.mock import patch

from support import FakeClock
from test_workflow import WorkflowTestCase
from buddy import workflow as workflow_module, workspace
from buddy.db import canonical_json


class PreparationTests(WorkflowTestCase):
    def setUp(self):
        super().setUp()
        self.workspace = workspace
        workflow_module._workspace_module = workspace
        self.repo = self.repository("repo")

    def git(self, path, *args):
        env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
        result = subprocess.run(["git", "-C", str(path), *args], env=env, capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout.strip()

    def repository(self, name):
        path = self.workdir(name).resolve()
        self.git(path, "init", "-q")
        self.git(path, "config", "user.name", "Preparation Test")
        self.git(path, "config", "user.email", "preparation@example.invalid")
        (path / "tracked.txt").write_text("base\n")
        self.git(path, "add", "tracked.txt")
        self.git(path, "commit", "-qm", "base")
        return path

    def intent(self, path=None):
        return {"kind": "existing", "cwd": str(path or self.repo), "access": "write",
                "base": {"kind": "working-tree"}, "includeUntracked": [], "writeScope": ["."], "integrator": "host-1"}

    def pending(self, *, clock=None, kind="existing"):
        board = self.board(clock=clock or FakeClock())
        self.register(board)
        submitted = self.submit(board, cwd=str(self.repo), executionWorkspace={**self.intent(), "kind": kind})
        first = self.claim(board)
        (Path(first["claim"]["task"]["cwd"]) / "tracked.txt").write_text("sealed first output\n")
        self.finish_turn(board, first, disposition="assistance")
        view = board.call("workflow_get", {"runId": submitted["runId"]})
        continuation = self.continue_run(board, view)
        return board, submitted["runId"], first, continuation["continuationId"]

    def rows(self, board, run_id, continuation_id):
        with board.store.db.read() as connection:
            run = dict(connection.execute("SELECT * FROM workflow_runs WHERE run_id=?", (run_id,)).fetchone())
            task = dict(connection.execute("SELECT * FROM tasks WHERE task_id=?", (run_id,)).fetchone())
            continuation = dict(connection.execute("SELECT * FROM workflow_continuations WHERE continuation_id=?", (continuation_id,)).fetchone())
            reservation = dict(connection.execute("SELECT * FROM workspace_reservations WHERE holder_task_id=? ORDER BY rowid DESC LIMIT 1", (run_id,)).fetchone())
        return run, task, continuation, reservation

    def test_success_updates_current_reservation_without_rewriting_original_input(self):
        board, run_id, first, cont = self.pending()
        before = self.rows(board, run_id, cont)
        board.store.workflow.prepare_continuation_workspace({"runId": run_id})
        after = self.rows(board, run_id, cont)
        manifest = json.loads(after[2]["workspace_manifest_json"])
        self.assertEqual(manifest["checkoutId"], json.loads(before[0]["workspace_manifest_json"])["checkoutId"])
        self.assertEqual(after[3]["manifest_sha256"], manifest["manifestSha256"])
        self.assertEqual(after[3]["workspace_id"], manifest["workspaceId"])
        for key in ("goal_json", "goal_fingerprint", "request_fingerprint", "execution_workspace_json"):
            self.assertEqual(after[0][key], before[0][key])
        self.assertEqual(after[1]["cwd"], before[1]["cwd"])
        self.assertEqual(after[1]["spec_json"], before[1]["spec_json"])
        for key in ("input_text", "input_bytes", "helper_outcomes_json", "expected_revision"):
            self.assertEqual(after[2][key], before[2][key])
        with board.store.db.read() as connection:
            saved = connection.execute("SELECT input_json FROM workflow_turns WHERE attempt_id=?", (first["claim"]["attempt"]["attemptId"],)).fetchone()[0]
        self.assertEqual(json.loads(saved), first["claim"]["turn"]["input"])
        self.assertEqual(self.claim(board, claim_request_id="next")["claim"]["turn"]["input"]["executionWorkspace"], manifest)

    def test_workspace_drift_becomes_attention_and_another_task_can_be_claimed(self):
        board, run_id, _, cont = self.pending()
        (self.repo / "tracked.txt").write_text("unreviewed post-seal drift\n")
        ordinary = board.call("task_submit", {"requestId": "ordinary", "adapter": "command", "argv": ["/bin/true"], "task": "independent", "cwd": str(self.workdir("ordinary"))})["task"]
        claimed = self.claim(board, claim_request_id="scan")
        self.assertEqual(claimed["claim"]["task"]["taskId"], ordinary["taskId"])
        view = board.call("workflow_get", {"runId": run_id, "includeAudit": True})
        self.assertEqual(view["state"], "awaiting-host")
        self.assertEqual(view["activeRequest"]["kind"], "attention")
        self.assertEqual(view["activeRequest"]["preparationError"]["code"], "WORKSPACE_BASE_MISMATCH")
        self.assertEqual(self.rows(board, run_id, cont)[2]["state"], "invalidated")
        self.assertEqual(self.rows(board, run_id, cont)[1]["state"], "queued")
        self.assertEqual((self.repo / "tracked.txt").read_text(), "unreviewed post-seal drift\n")
        with board.store.db.read() as connection:
            event = connection.execute("SELECT payload_json FROM events WHERE task_id=? AND kind='workflow.workspace_preparation_failed'", (run_id,)).fetchone()
        self.assertEqual(json.loads(event[0])["errorCode"], "WORKSPACE_BASE_MISMATCH")
        self.assertTrue(list((self.directory / "workspaces").glob("*/request.json")))
        # The attention boundary is actionable: after an explicit repair and new
        # Host input the same goal can run, with the failed preparation preserved.
        (self.repo / "tracked.txt").write_text("sealed first output\n")
        self.continue_run(board, view, command_id="recover-preparation", input="Use the repaired fixed output")
        self.register(board, worker_id="recovery-worker")
        resumed = self.claim(board, worker_id="recovery-worker", claim_request_id="recovery-claim", run_id=run_id)
        self.assertIsNotNone(resumed["claim"], resumed)
        self.assertEqual(resumed["claim"]["turn"]["turnIndex"], 2)
        self.assertEqual(self.rows(board, run_id, cont)[2]["state"], "invalidated")

    def test_concurrent_preparation_lock_retains_continuation_and_allows_recovery(self):
        board, run_id, _, cont = self.pending(kind="worktree")
        before = self.rows(board, run_id, cont)
        _, directory = workspace._workspace_directory(self.directory, f"{run_id}:{cont}")
        # The same real nonblocking flock used by competing worker RPCs. Its holder
        # may publish a manifest or die before publishing; neither case is a Host
        # decision, and no worker may claim the old input while it is held.
        with workspace._lock(directory):
            blocked = self.claim(board, claim_request_id="contending", run_id=run_id)
            self.assertIsNone(blocked["claim"])
            self.assertEqual(blocked["reason"], "awaiting-workspace-preparation")
            during = self.rows(board, run_id, cont)
            self.assertEqual(during[0]["state"], "executing")
            self.assertEqual(during[0]["revision"], before[0]["revision"])
            self.assertEqual(during[2]["state"], "queued")
            self.assertIsNone(during[2]["workspace_manifest_json"])
            self.assertIsNone(during[0]["active_request_id"])
        resumed = self.claim(board, claim_request_id="recovered", run_id=run_id)
        self.assertIsNotNone(resumed["claim"])
        self.assertEqual(resumed["claim"]["turn"]["turnIndex"], 2)
        self.assertEqual(resumed["claim"]["task"]["cwd"], before[1]["cwd"])
        self.assertEqual((Path(before[1]["cwd"]) / "tracked.txt").read_text(), "sealed first output\n")

    def test_real_worktree_continuation_retains_checkout_and_source_files(self):
        board, run_id, first, cont = self.pending(kind="worktree")
        previous = first["claim"]["turn"]["input"]["executionWorkspace"]
        resumed = self.claim(board, claim_request_id="worktree-continuation", run_id=run_id)
        self.assertIsNotNone(resumed["claim"], resumed)
        current = resumed["claim"]["turn"]["input"]["executionWorkspace"]
        for field in ("checkoutId", "checkoutRoot", "repositoryId", "path"):
            self.assertEqual(current[field], previous[field])
        self.assertEqual(resumed["claim"]["task"]["cwd"], previous["path"])
        self.assertEqual((self.repo / "tracked.txt").read_text(), "base\n")
        self.assertEqual((Path(current["path"]) / "tracked.txt").read_text(), "sealed first output\n")

    def test_claim_gate_cannot_fall_back_to_original_manifest(self):
        board, run_id, _, _ = self.pending()
        claim = board.store.worker_claim({"workerId": "w1", "claimRequestId": "without-prepare", "nonce": "n" * 16, "runId": run_id})
        self.assertIsNone(claim["claim"])
        self.assertEqual(claim["reason"], "awaiting-workspace-preparation")

    def test_cancel_during_git_cannot_activate_prepared_files(self):
        self.preparation_race("cancel")

    def test_takeover_during_git_cannot_activate_old_owner_preparation(self):
        self.preparation_race("takeover")

    def test_manual_override_during_git_cannot_activate_old_input(self):
        self.preparation_race("continue")

    def test_checkout_change_during_git_cannot_overwrite_current_allocation(self):
        self.preparation_race("checkout")

    def preparation_race(self, operation):
        board, run_id, _, cont = self.pending()
        before = self.rows(board, run_id, cont)
        replacement = workspace.prepare(self.directory, "replacement", self.intent(self.repository("replacement"))) if operation == "checkout" else None
        prepared = []
        original = workspace.prepare

        def race(*args):
            manifest = original(*args)
            prepared.append(manifest)
            view = board.call("workflow_get", {"runId": run_id})
            if operation == "cancel":
                board.call("workflow_cancel", {"runId": run_id, "commandId": "cancel-during-prepare", **self.control(view)})
            elif operation == "takeover":
                board.call("workflow_takeover", {"runId": run_id, "commandId": "takeover-during-prepare", "expectedOwnerGeneration": view["ownerGeneration"], "newHostId": "host-2", **self.control(view)})
            elif operation == "continue":
                self.continue_run(board, view, command_id="override-during-prepare", input="new Host input")
            else:
                # A coordinator allocation replacement, including its reservation.
                # Deliberately retain revision to require actual manifest comparison.
                with board.store.db.write() as connection:
                    connection.execute("UPDATE workflow_runs SET workspace_manifest_json=?, workspace_id=?, workspace_manifest_sha256=? WHERE run_id=?", (canonical_json(replacement), replacement["workspaceId"], replacement["manifestSha256"], run_id))
                    connection.execute("UPDATE tasks SET cwd=? WHERE task_id=?", (replacement["path"], run_id))
                    connection.execute("UPDATE workspace_reservations SET workspace_id=?, checkout_id=?, repository_id=?, path=?, manifest_sha256=? WHERE holder_task_id=? AND state='held'", (replacement["workspaceId"], replacement["checkoutId"], replacement["repositoryId"], replacement["path"], replacement["manifestSha256"], run_id))
            return manifest

        with patch.object(workspace, "prepare", side_effect=race):
            board.store.workflow.prepare_continuation_workspace({"runId": run_id})
        after = self.rows(board, run_id, cont)
        self.assertIsNone(after[2]["workspace_manifest_json"])
        self.assertEqual(after[0]["workspace_manifest_json"], canonical_json(replacement) if replacement else before[0]["workspace_manifest_json"])
        self.assertEqual(after[1]["cwd"], replacement["path"] if replacement else before[1]["cwd"])
        self.assertEqual(after[2]["input_text"], before[2]["input_text"])
        self.assertEqual(after[0]["goal_json"], before[0]["goal_json"])
        self.assertTrue((self.directory / "workspaces" / prepared[0]["workspaceId"] / "manifest.json").exists(), "fenced preparation remains recoverable")
        with board.store.db.read() as connection:
            self.assertIsNotNone(board.store.workflow.claim_blocker(connection, connection.execute("SELECT * FROM tasks WHERE task_id=?", (run_id,)).fetchone()))

    def test_wrong_prepared_checkout_becomes_attention(self):
        board, run_id, _, cont = self.pending()
        replacement = workspace.prepare(self.directory, "wrong-checkout", self.intent(self.repository("wrong-checkout")))
        before = self.rows(board, run_id, cont)
        with patch.object(workspace, "prepare", return_value=replacement), patch.object(workspace, "verify") as verify:
            board.store.workflow.prepare_continuation_workspace({"runId": run_id})
        verify.assert_not_called()
        after = self.rows(board, run_id, cont)
        self.assertEqual(after[0]["state"], "awaiting-host")
        self.assertEqual(after[0]["workspace_manifest_json"], before[0]["workspace_manifest_json"])
        self.assertEqual(after[1]["cwd"], before[1]["cwd"])
        self.assertIsNone(after[2]["workspace_manifest_json"])

    def test_transferred_checkout_is_not_scanned_by_preparation(self):
        board = self.board()
        self.register(board)
        submitted = self.submit(board, cwd=str(self.repo), executionWorkspace=self.intent())
        self.finish_turn(board, self.claim(board), disposition="assistance")
        view = board.call("workflow_get", {"runId": submitted["runId"]})
        self.decide(board, view, view["activeRequest"]["requestId"], helpers=[{"requestId": "helper", "task": "shared helper", "cwd": str(self.repo), "executionWorkspace": self.intent()}])
        view = board.call("workflow_get", {"runId": submitted["runId"]})
        self.continue_run(board, view, helper_policy="keep")
        with patch.object(workspace, "prepare", side_effect=AssertionError("must not scan a transferred checkout")) as prepare:
            board.store.workflow.prepare_continuation_workspace({"runId": submitted["runId"]})
        prepare.assert_not_called()

    def test_latest_seal_uses_publication_order_with_equal_timestamps(self):
        self.seal_order(False)

    def test_latest_seal_uses_publication_order_after_clock_rollback(self):
        self.seal_order(True)

    def seal_order(self, rollback):
        clock = FakeClock()
        board, run_id, _, _ = self.pending(clock=clock)
        second = self.claim(board, claim_request_id="second")
        (self.repo / "tracked.txt").write_text("newest second output\n")
        if rollback:
            clock.value = "2000-01-01T00:00:00.000Z"
        # A newer UUID that sorts before the old artifact makes the timestamp
        # tie regression deterministic, independently of random UUID luck.
        ids = (uuid.UUID(int=value) for value in itertools.count(1))
        with patch.object(workflow_module.uuid, "uuid4", side_effect=lambda: next(ids)):
            self.finish_turn(board, second, disposition="assistance")
        view = board.call("workflow_get", {"runId": run_id})
        self.continue_run(board, view, command_id="third")
        third = self.claim(board, claim_request_id="claim-third")
        self.assertIsNotNone(third["claim"])
        with board.store.db.read() as connection:
            latest = connection.execute("SELECT manifest_json FROM workflow_artifacts WHERE run_id=? AND kind='output' ORDER BY rowid DESC LIMIT 1", (run_id,)).fetchone()
        self.assertEqual(third["claim"]["turn"]["input"]["executionWorkspace"]["baseCommit"], json.loads(latest[0])["commit"])

    def test_helper_output_reference_never_impersonates_a_new_failed_attempt(self):
        board = self.board()
        self.register(board)
        submitted = self.submit(board, cwd=str(self.repo), executionWorkspace=self.intent())
        self.finish_turn(board, self.claim(board), disposition="assistance")
        helper_repo = self.repository("helper")
        view = board.call("workflow_get", {"runId": submitted["runId"]})
        approved = self.decide(board, view, view["activeRequest"]["requestId"], autoContinue=False,
                               helpers=[{"requestId": "helper", "task": "independent helper", "cwd": str(helper_repo), "executionWorkspace": self.intent(helper_repo)}])
        child = approved["children"][0]["taskId"]
        first = self.claim(board, claim_request_id="helper-first", run_id=child)
        self.finish_turn(board, first, disposition="attention")
        with board.store.db.read() as connection:
            handed = board.store.workflow._helper_outcomes(connection, submitted["runId"])[0]
        self.assertEqual(handed["artifact"]["attemptId"], first["claim"]["attempt"]["attemptId"])
        child_view = board.call("workflow_get", {"runId": child})
        board.store.workflow.continue_run({"runId": child, "commandId": "helper-second", "expectedRevision": child_view["revision"], "input": "new attempt", "helperPolicy": "keep"}, console_authority={"sessionId": "private-test-console"})
        second = self.claim(board, claim_request_id="claim-helper-second", run_id=child)
        self.assertIsNotNone(second["claim"], second)
        self.finish_turn(board, second, runner_status="failed", exit_code=1, seal=False)
        with board.store.db.read() as connection:
            handed = board.store.workflow._helper_outcomes(connection, submitted["runId"])[0]
        self.assertEqual(handed["attemptId"], second["claim"]["attempt"]["attemptId"])
        self.assertEqual(handed["generation"], 2)
        self.assertNotIn("artifact", handed)

    def test_handoff_ignores_outputs_from_other_actual_checkouts(self):
        board, run_id, first, cont = self.pending()
        original = self.rows(board, run_id, cont)[0]
        other = workspace.prepare(self.directory, "other-input", self.intent(self.repository("other")))
        attempt_id = first["claim"]["attempt"]["attemptId"]
        seal = workspace.seal(self.directory, other, run_id, attempt_id)
        with board.store.db.write() as connection:
            board.store.workflow._pin_artifact(connection, run_id=run_id, kind="output", manifest=seal, attempt_id=attempt_id, turn_id=first["claim"]["turn"]["turnId"], source_task_id=run_id, now=board.store.now())
        with board.store.db.read() as connection:
            chosen = board.store.workflow._latest_handoff(connection, original, json.loads(original["workspace_manifest_json"]))
        self.assertEqual(chosen["manifestSha256"], original["workspace_manifest_sha256"])
        self.assertNotEqual(chosen["manifestSha256"], other["manifestSha256"])

    def test_authorized_helper_seal_becomes_the_same_checkout_handoff(self):
        board = self.board(clock=FakeClock())
        self.register(board)
        submitted = self.submit(board, cwd=str(self.repo), executionWorkspace=self.intent())
        parent = self.claim(board)
        (self.repo / "tracked.txt").write_text("parent output\n")
        self.finish_turn(board, parent, disposition="assistance")
        view = board.call("workflow_get", {"runId": submitted["runId"]})
        approved = self.decide(board, view, view["activeRequest"]["requestId"], helpers=[{"requestId": "helper", "task": "shared checkout helper", "cwd": str(self.repo), "executionWorkspace": self.intent()}])
        helper = approved["children"][0]["taskId"]
        claim = self.claim(board, claim_request_id="helper-claim", run_id=helper)
        (self.repo / "tracked.txt").write_text("newer helper output\n")
        self.finish_turn(board, claim)
        with board.store.db.read() as connection:
            artifact = connection.execute("SELECT manifest_json FROM workflow_artifacts WHERE run_id=? AND kind='output'", (helper,)).fetchone()
        resumed = self.claim(board, claim_request_id="resumed", run_id=submitted["runId"])
        self.assertIsNotNone(resumed["claim"], resumed)
        self.assertEqual(resumed["claim"]["turn"]["input"]["executionWorkspace"]["baseCommit"], json.loads(artifact[0])["commit"])
