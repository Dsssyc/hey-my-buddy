"""Host edits between sealed turns do not become Worker output at conclusion."""
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
        submitted = board.call("workflow_submit", {
            **BUDDY, "requestId": "existing-sealed", "hostId": "host-1", "task": "Work in the ignored directories",
            "cwd": str(self.repo), "submissionToken": "t" * 32,
            "executionWorkspace": {"kind": "existing", "access": "full", "accessNote": "Use the private fixture",
                                   "base": {"kind": "working-tree"}, "writeScope": ["cache-a", "cache-b"],
                                   "includeUntracked": ["cache-a", "cache-b"]},
        })
        run_id = submitted["runId"]
        self.controls[run_id] = submitted["control"]
        claimed = self.claim(board, run_id)
        manifest = claimed["claim"]["turn"]["input"]["executionWorkspace"]
        (self.repo / "cache-a/result.txt").write_text("sealed output\n", encoding="utf-8", newline="\n")
        sealed = workspace.seal(self.directory, manifest, run_id, claimed["claim"]["attempt"]["attemptId"])
        self.finish_turn(board, claimed, sealed, disposition="attention")
        return board, run_id, sealed

    def continue_run(self, board, run_id):
        view = self.view(board, run_id)
        board.call("workflow_continue", {
            "runId": run_id, "commandId": "next-turn", "expectedRevision": view["revision"],
            "input": "Continue the work", **self.control(view),
        })

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
        board.call("workflow_cancel", {"runId": run_id, **self.control(refused)})
        before = self.view(board, run_id)["artifacts"]
        with patch.object(workspace, "host_seal", side_effect=AssertionError("The Worker has no unsealed turn")):
            concluded = board.call("workflow_conclude", {
                "runId": run_id, "note": "Restart from the Host's current checkout", **self.control(refused),
            })
        self.assertEqual(concluded["state"], "concluded")
        self.assertIsNone(concluded["partialArtifactId"])
        self.assertEqual(self.view(board, run_id)["artifacts"], before)
        with board.store.db.read() as connection:
            self.assertEqual(connection.execute(
                "SELECT COUNT(*) FROM workspace_reservations WHERE holder_task_id=? AND state IN ('held','transferred')",
                (run_id,),
            ).fetchone()[0], 0)
        replacement = board.call("workflow_submit", {
            **BUDDY, "requestId": "existing-replacement", "hostId": "host-1", "task": "Start from the new current state",
            "cwd": str(self.repo), "submissionToken": "r" * 32,
            "executionWorkspace": {"kind": "existing", "access": "write", "writeScope": ["cache-a", "cache-b"],
                                   "includeUntracked": ["cache-a", "cache-b"]},
        })
        claimed = self.claim(board, replacement["runId"], request_id="replacement-claim")
        self.assertIsNotNone(claimed["claim"])
        manifest = claimed["claim"]["turn"]["input"]["executionWorkspace"]
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
        board.call("workflow_cancel", {"runId": run_id, **self.control(self.view(board, run_id))})
        attempt = claimed["claim"]["attempt"]
        board.call("execution_result", {
            "attemptId": attempt["attemptId"], "generation": attempt["generation"], "nonce": "n" * 16,
            "status": "cancelled", "terminationReason": "forced-stop", "shutdownConfirmed": True,
            "exitCode": 1, "result": {"status": "failed", "processState": {"shutdownConfirmed": True}},
        })
        with self.assertRaises(BoardError) as raised:
            board.call("workflow_conclude", {
                "runId": run_id, "note": "An unsealed Worker turn must still be checked", **self.control(self.view(board, run_id)),
            })
        self.assertEqual(raised.exception.code, "WORKSPACE_SCOPE_VIOLATION")
        self.assertEqual(raised.exception.details["paths"], ["tracked.txt"])
        self.assertIsNone(self.view(board, run_id)["hostConclusion"])

    def test_first_submission_base_mismatch_does_not_ask_to_conclude_a_task(self):
        base = git(self.repo, "rev-parse", "HEAD").strip()
        (self.repo / "tracked.txt").write_text("Host commit before submission\n", encoding="utf-8", newline="\n")
        git(self.repo, "add", "tracked.txt")
        git(self.repo, "commit", "-qm", "Host selected a different base")
        board = self.board()
        with self.assertRaises(BoardError) as raised:
            board.call("workflow_submit", {
                **BUDDY, "requestId": "initial-base-mismatch", "hostId": "host-1", "task": "Start from a selected base",
                "cwd": str(self.repo), "submissionToken": "t" * 32,
                "executionWorkspace": {"kind": "existing", "access": "write", "writeScope": ["src"],
                                       "base": {"kind": "working-tree", "ref": base}},
            })
        self.assertEqual(raised.exception.code, "WORKSPACE_BASE_MISMATCH")
        self.assertNotIn("conclude", str(raised.exception))
        self.assertIn("before submitting", str(raised.exception))
        self.assertEqual(raised.exception.details["baseCommit"], base)
        with board.store.db.read() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM workflow_runs").fetchone()[0], 0)

    def test_prepared_managed_continuation_seals_against_its_current_input(self):
        board = self.board()
        submitted = board.call("workflow_submit", {
            **BUDDY, "requestId": "adopted-handoff", "hostId": "host-1", "task": "Scoped work with an adopted output",
            "cwd": str(self.repo), "submissionToken": "t" * 32,
            "executionWorkspace": {"kind": "worktree", "access": "write", "writeScope": ["src"]},
        })
        run_id = submitted["runId"]
        self.controls[run_id] = submitted["control"]
        claimed = self.claim(board, run_id)
        claim = claimed["claim"]
        manifest = claim["turn"]["input"]["executionWorkspace"]
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
        adopted = board.call("workflow_workspace_resolve", {
            "runId": run_id, "commandId": "adopt-handoff", "expectedRevision": view["revision"],
            "conflictId": conflict["conflictId"], "action": "adopt", "note": "Keep the exact stopped output",
            "observedFingerprint": conflict["observedFingerprint"], **self.control(view),
        })
        self.assertTrue(adopted["delivered"])
        git(checkout, "add", ".")
        git(checkout, "commit", "-qm", "Host commits the adopted tree")
        self.continue_run(board, run_id)
        board.store.workflow.prepare_continuation_workspace({"runId": run_id})
        board.call("workflow_cancel", {"runId": run_id, **self.control(view)})
        concluded = board.call("workflow_conclude", {
            "runId": run_id, "note": "No turn ran after adoption", **self.control(view),
        })
        self.assertIsNotNone(concluded["partialArtifactId"])
        self.assertTrue(concluded["reclaim"]["removed"], concluded["reclaim"])
        self.assertFalse(checkout.exists())

    def test_managed_parent_seals_after_a_same_checkout_helper_without_a_new_parent_turn(self):
        board = self.board()
        submitted, _, _, checkout, _, view, _ = self.run_worktree(board, disposition="assistance")
        run_id = submitted["runId"]
        decided = board.call("workflow_decide", {
            "runId": run_id, "requestId": view["activeRequest"]["requestId"], "commandId": "same-checkout-helper",
            "expectedRevision": view["revision"], "decision": "approve", "autoContinue": False,
            "helpers": [{**BUDDY, "requestId": "borrow-checkout", "task": "Finish in the parent checkout",
                         "cwd": str(checkout), "executionWorkspace": {"kind": "existing", "access": "write", "writeScope": ["."]}}],
            **self.control(view),
        })
        helper_id = decided["children"][0]["taskId"]
        claimed = self.claim(board, helper_id, request_id="same-checkout-helper-claim")
        manifest = claimed["claim"]["turn"]["input"]["executionWorkspace"]
        (checkout / "helper.txt").write_text("The helper's sealed work\n", encoding="utf-8", newline="\n")
        seal = workspace.seal(self.directory, manifest, helper_id, claimed["claim"]["attempt"]["attemptId"])
        self.finish_turn(board, claimed, seal)
        board.call("workflow_cancel", {"runId": run_id, **self.control(view)})
        concluded = board.call("workflow_conclude", {
            "runId": run_id, "note": "No further parent turn", **self.control(view),
        })
        self.assertIsNotNone(concluded["partialArtifactId"])
        self.assertTrue(concluded["reclaim"]["removed"], concluded["reclaim"])
        self.assertFalse(checkout.exists())

