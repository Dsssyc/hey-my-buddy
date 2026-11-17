"""Host edits between sealed turns do not become Worker output at conclusion."""
from hey_my_buddy.protocol.workspace_manifest import turn_manifest
from pathlib import Path
from unittest.mock import patch

from blackboard.tasks.test_workflow_real import BUDDY
from blackboard.tasks.workspace_lifecycle_case import LifecycleTestCase, git
from hey_my_buddy.blackboard.tasks import workspace
from hey_my_buddy.errors import BoardError


class ExistingConclusionTests(LifecycleTestCase):
    def sealed_existing(self):
        (self.repo / ".gitignore").write_text("cache-a/\ncache-b/\n", encoding="utf-8", newline="\n")
        git(self.repo, "add", ".gitignore")
        git(self.repo, "commit", "-qm", "Ignore the two writable directories")
        for directory in ("cache-a", "cache-b"):
            (self.repo / directory).mkdir()
            (self.repo / directory / "result.txt").write_text("input\n", encoding="utf-8", newline="\n")
        board = self.board()
        submitted = self.submit_run(board, {
            "title": "existing-sealed", "task": "Work in the ignored directories",
            "cwd": str(self.repo), "checkout": "existing", "access": "full",
            "accessNote": "Use the private fixture", "writeScope": ["cache-a", "cache-b"],
            "inputs": ["cache-a", "cache-b"],
        })
        run_id = submitted["runId"]
        claimed = self.claim(board, run_id)
        manifest = turn_manifest(self.directory, claimed["claim"]["turn"]["input"])
        (self.repo / "cache-a/result.txt").write_text("sealed output\n", encoding="utf-8", newline="\n")
        sealed = workspace.seal(self.directory, manifest, run_id, claimed["claim"]["attempt"]["attemptId"])
        self.finish_turn(board, claimed, sealed, disposition="attention")
        return board, run_id, sealed

    def continue_run(self, board, run_id):
        view = self.view(board, run_id)
        origin = ({"requestId": view["activeRequest"]["requestId"]} if view["activeRequest"]
                  else {"artifactId": view["finalArtifactId"]} if view["finalArtifactId"]
                  else {"turnId": view["currentTurn"]["turnId"]})
        board.call("workflow_continue", {"runId": run_id, **origin, "input": "Continue the work"})

    def test_host_commits_after_seal_can_conclude_and_release_the_existing_checkout(self):
        board, run_id, sealed = self.sealed_existing()
        paths = [f"host-{index}.txt" for index in range(7)]
        for index in range(4):
            for path in paths[index * 2:index * 2 + 2]:
                (self.repo / path).write_text("Host work outside the scope\n", encoding="utf-8", newline="\n")
            git(self.repo, "add", ".")
            git(self.repo, "commit", "-qm", f"Host commit {index}")
        self.continue_run(board, run_id)
        board.store.workflow.prepare_continuation_workspace({"runId": run_id})
        refused = self.view(board, run_id)
        error = refused["activeRequest"]["preparationError"]
        self.assertEqual(error["code"], "WORKSPACE_BASE_MISMATCH")
        self.assertEqual(error["details"], {"handoffCommit": sealed["commit"], "paths": paths,
                                             "pathsCount": 7, "pathsTruncated": False})
        self.assertIn("conclude this micro task and start a new one", error["message"])
        self.assertEqual(refused["counts"]["turns"], 1)
        board.call("workflow_cancel", {"runId": run_id})
        before = self.view(board, run_id)["artifacts"]
        with patch.object(workspace, "host_seal", side_effect=AssertionError("The Worker has no unsealed turn")):
            concluded = board.call("workflow_conclude", {
                "runId": run_id, "note": "Restart from the Host's current checkout",
            })
        self.assertEqual(concluded["state"], "concluded")
        self.assertNotIn("artifactId", concluded)
        self.assertEqual(self.view(board, run_id)["artifacts"], before)
        with board.store.db.read() as connection:
            self.assertEqual(connection.execute(
                "SELECT COUNT(*) FROM workspace_reservations WHERE holder_task_id=? AND state IN ('held','transferred')",
                (run_id,),
            ).fetchone()[0], 0)
        replacement = self.submit_run(board, {
            "title": "existing-replacement", "task": "Start from the new current state",
            "cwd": str(self.repo), "checkout": "existing", "access": "write",
            "writeScope": ["cache-a", "cache-b"], "inputs": ["cache-a", "cache-b"],
        })
        claimed = self.claim(board, replacement["runId"], request_id="replacement-claim")
        self.assertIsNotNone(claimed["claim"])
        manifest = turn_manifest(self.directory, claimed["claim"]["turn"]["input"])
        self.assertEqual(manifest["snapshot"]["sourceHead"], git(self.repo, "rev-parse", "HEAD").strip())
        self.assertEqual((self.repo / paths[-1]).read_text(encoding="utf-8"), "Host work outside the scope\n")
        final = workspace.seal(self.directory, manifest, replacement["runId"], claimed["claim"]["attempt"]["attemptId"])
        self.finish_turn(board, claimed, final)

    def test_an_unsealed_later_turn_still_checks_the_write_scope_at_conclusion(self):
        board, run_id, _sealed = self.sealed_existing()
        self.continue_run(board, run_id)
        claimed = self.claim(board, run_id, request_id="unsealed-next-turn")
        self.assertIsNotNone(claimed["claim"])
        (self.repo / "tracked.txt").write_text("Worker wrote outside the scope\n", encoding="utf-8", newline="\n")
        board.call("workflow_cancel", {"runId": run_id})
        attempt = claimed["claim"]["attempt"]
        board.call("execution_result", {
            "attemptId": attempt["attemptId"], "generation": attempt["generation"], "nonce": "n" * 16,
            "status": "cancelled", "terminationReason": "forced-stop", "shutdownConfirmed": True,
            "exitCode": 1, "result": {"status": "failed", "processState": {"shutdownConfirmed": True}},
        })
        with self.assertRaises(BoardError) as raised:
            board.call("workflow_conclude", {
                "runId": run_id, "note": "An unsealed Worker turn must still be checked",
            })
        self.assertEqual(raised.exception.code, "WORKSPACE_SCOPE_VIOLATION")
        self.assertEqual(raised.exception.details["paths"], ["tracked.txt"])
        self.assertIsNone(self.view(board, run_id)["hostConclusion"])

    def test_first_submission_rejects_the_retired_base_selector_before_creating_a_task(self):
        base = git(self.repo, "rev-parse", "HEAD").strip()
        (self.repo / "tracked.txt").write_text("Host commit before submission\n", encoding="utf-8", newline="\n")
        git(self.repo, "add", "tracked.txt")
        git(self.repo, "commit", "-qm", "Host selected a different base")
        board = self.board()
        with self.assertRaises(BoardError) as raised:
            self.submit_run(board, {
                "title": "initial-base-mismatch", "task": "Start from a selected base",
                "cwd": str(self.repo), "checkout": "existing", "access": "write",
                "writeScope": ["src"], "base": {"kind": "working-tree", "ref": base},
            })
        self.assertEqual(raised.exception.code, "INVALID_ARGUMENT")
        self.assertIn("base", str(raised.exception))
        with board.store.db.read() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM workflow_runs").fetchone()[0], 0)

    def test_prepared_managed_continuation_seals_against_its_current_input(self):
        board = self.board()
        submitted = self.submit_run(board, {
            "title": "adopted-handoff", "task": "Scoped work with an adopted output",
            "cwd": str(self.repo), "checkout": "worktree", "access": "write", "writeScope": ["src"],
        })
        run_id = submitted["runId"]
        claimed = self.claim(board, run_id)
        claim = claimed["claim"]
        manifest = turn_manifest(self.directory, claim["turn"]["input"])
        checkout = Path(manifest["path"])
        (checkout / "tracked.txt").write_text("Host will adopt this path\n", encoding="utf-8", newline="\n")
        with self.assertRaises(BoardError):
            workspace.seal(self.directory, manifest, run_id, claim["attempt"]["attemptId"])
        attempt = claim["attempt"]
        seal_error = "WORKSPACE_SCOPE_VIOLATION: fixture refused seal"
        board.call("execution_result", {
            "attemptId": attempt["attemptId"], "generation": attempt["generation"], "nonce": "n" * 16,
            "status": "failed", "shutdownConfirmed": True, "exitCode": 1, "error": seal_error,
            "result": {"status": "failed", "workspaceSealError": seal_error,
                       "turn": self.turn_record(claim), "turnResultPath": "/tmp/turn-result.json",
                       "processState": {"shutdownConfirmed": True}},
        })
        view = self.view(board, run_id)
        conflict = view["workspaceConflicts"][0]
        adopted = board.call("workflow_continue", {
            "runId": run_id, "requestId": view["activeRequest"]["requestId"],
            "resolution": "adopt", "note": "Keep the exact stopped output",
            "observedFingerprint": conflict["observedFingerprint"],
        })
        self.assertEqual(self.view(board, run_id)["state"], "delivered")
        git(checkout, "add", ".")
        git(checkout, "commit", "-qm", "Host commits the adopted tree")
        self.continue_run(board, run_id)
        board.store.workflow.prepare_continuation_workspace({"runId": run_id})
        board.call("workflow_cancel", {"runId": run_id})
        concluded = board.call("workflow_conclude", {
            "runId": run_id, "note": "No turn ran after adoption",
        })
        self.assertTrue(any(row["kind"] == "partial-output" for row in self.view(board, run_id)["artifacts"]))
        self.assertTrue(concluded["reclaim"]["removed"], concluded["reclaim"])
        self.assertFalse(checkout.exists())

    def test_managed_parent_seals_after_a_same_checkout_helper_without_a_new_parent_turn(self):
        board = self.board()
        submitted, _, _, checkout, _, view, _ = self.run_worktree(board, disposition="assistance")
        run_id = submitted["runId"]
        board.call("workflow_continue", {
            "runId": run_id, "requestId": view["activeRequest"]["requestId"],
            "decision": "approve", "autoContinue": False,
            "helpers": [{"title": "borrow-checkout", "task": "Finish in the parent checkout",
                         "cwd": str(checkout), "buddy": " / ".join(BUDDY.values()),
                         "checkout": "existing", "access": "write", "writeScope": ["."],
                         "inputs": [], "expectedSeconds": 1800}],
        })
        helper_id = self.view(board, run_id)["children"][0]["taskId"]
        claimed = self.claim(board, helper_id, request_id="same-checkout-helper-claim")
        manifest = turn_manifest(self.directory, claimed["claim"]["turn"]["input"])
        (checkout / "helper.txt").write_text("The helper's sealed work\n", encoding="utf-8", newline="\n")
        seal = workspace.seal(self.directory, manifest, helper_id, claimed["claim"]["attempt"]["attemptId"])
        self.finish_turn(board, claimed, seal)
        board.call("workflow_cancel", {"runId": run_id})
        concluded = board.call("workflow_conclude", {
            "runId": run_id, "note": "No further parent turn",
        })
        self.assertTrue(any(row["kind"] == "partial-output" for row in self.view(board, run_id)["artifacts"]))
        self.assertTrue(concluded["reclaim"]["removed"], concluded["reclaim"])
        self.assertFalse(checkout.exists())

