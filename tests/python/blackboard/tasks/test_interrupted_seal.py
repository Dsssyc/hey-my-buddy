"""A stopped orphan's deterministic seal failure follows the ordinary failed-turn path."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from blackboard.tasks.test_workflow_real import BUDDY, RealWorkspaceTestCase
from hey_my_buddy.blackboard.tasks import interruption, workflow, workspace
from hey_my_buddy.errors import BoardError
from hey_my_buddy.protocol import process_identity as processes


IDENTITY = {"bootId": "macos:00000000-0000-0000-0000-000000000001", "pid": 123,
            "startedAt": 123250000, "processGroupId": 123, "jobName": None,
            "worker": {"bootId": "macos:00000000-0000-0000-0000-000000000001", "pid": 122,
                       "startedAt": 120500000}}


class InterruptedSealTests(RealWorkspaceTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.enterContext(patch.object(workflow, "_workspace_module", workspace))

    def claimed(self, *, access="write", scope=None):
        board = self.board()
        board.call("worker_register", {"workerId": "orphan-worker", "capabilities": ["dsh"]})
        submitted = board.call("workflow_submit", {
            **BUDDY, "requestId": f"interrupted-seal-{access}", "hostId": "host-1",
            "task": "recover stopped workspace", "cwd": str(self.repo),
            "submissionToken": "t" * 32,
            "executionWorkspace": {"kind": "existing", "access": access,
                                   **({"writeScope": scope} if scope is not None else {})},
        })
        claim = board.call("worker_claim", {
            "workerId": "orphan-worker", "claimRequestId": "claim-orphan", "nonce": "n" * 16,
            "runId": submitted["runId"],
        })["claim"]
        attempt_id = claim["attempt"]["attemptId"]
        board.call("worker_progress", {
            "workerId": "orphan-worker", "attemptId": attempt_id,
            "generation": claim["attempt"]["generation"], "nonce": "n" * 16,
            "processIdentity": IDENTITY,
        })
        with board.store.db.read() as connection:
            attempt = dict(board.store._attempt_row(connection, attempt_id))
        journal = interruption._native_journal(board.store, attempt, IDENTITY)
        journal_path = Path(journal["path"])
        journal_path.parent.mkdir(parents=True, exist_ok=True)
        processes.NativeProcessJournal(journal_path, journal["binding"])
        return board, submitted, claim, Path(claim["turn"]["input"]["executionWorkspace"]["path"])

    def recover(self, board):
        with patch.object(interruption.processes, "worker_identity_alive", return_value=False), \
             patch.object(interruption.processes, "inspect_process_identity", return_value="identity-absent"):
            return board.store.reconcile_startup()

    def assert_stopped_receipt(self, board, attempt_id, code):
        with board.store.db.read() as connection:
            attempt = connection.execute("SELECT * FROM attempts WHERE attempt_id=?", (attempt_id,)).fetchone()
            payload = json.loads(attempt["result_json"])
            self.assertEqual(attempt["execution_state"], "finished")
            self.assertEqual(attempt["shutdown_confirmed"], 1)
            self.assertEqual(payload["terminationReason"], "interrupted")
            self.assertTrue(payload["result"]["workspaceSealError"].startswith(code + ": "))
            self.assertEqual(payload["error"], payload["result"]["workspaceSealError"])
            self.assertEqual(connection.execute(
                "SELECT state FROM attempt_interruptions WHERE attempt_id=?", (attempt_id,)
            ).fetchone()[0], "settled")
        self.assertEqual(board.store.resource_claims(), [])

    def test_outside_scope_opens_host_resolution_after_confirmed_stop(self):
        board, submitted, claim, checkout = self.claimed(scope=["src"])
        (checkout / "src" / "feature.py").write_text("value = 2\n")
        (checkout / "tracked.txt").write_text("outside scope\n")
        self.assertEqual(self.recover(board)["interrupted"], 1)
        attempt_id = claim["attempt"]["attemptId"]
        self.assert_stopped_receipt(board, attempt_id, "WORKSPACE_SCOPE_VIOLATION")
        view = board.call("workflow_get", {"runId": submitted["runId"]})
        self.assertEqual(view["state"], "awaiting-host")
        self.assertEqual(view["activeRequest"]["kind"], "attention")
        conflict = view["workspaceConflicts"][0]
        self.assertEqual(conflict["blockingPaths"], ["tracked.txt"])
        resolved = board.call("workflow_workspace_resolve", {
            "runId": submitted["runId"], "commandId": "restore-orphan", "expectedRevision": view["revision"],
            "conflictId": conflict["conflictId"], "action": "restore", "paths": ["tracked.txt"],
            "observedFingerprint": conflict["observedFingerprint"], "reason": "restore outside change",
            **submitted["control"],
        })
        self.assertTrue(resolved["resolved"])
        self.assertEqual(resolved["resolutionState"], "restored")
        self.assertEqual((checkout / "tracked.txt").read_text(), "base\n")
        self.assertEqual((checkout / "src" / "feature.py").read_text(), "value = 2\n")

    def test_changed_readonly_checkout_finishes_as_failed(self):
        board, submitted, claim, checkout = self.claimed(access="read")
        (checkout / "tracked.txt").write_text("changed during turn\n")
        self.assertEqual(self.recover(board)["interrupted"], 1)
        self.assert_stopped_receipt(board, claim["attempt"]["attemptId"], "WORKSPACE_CHANGED")
        view = board.call("workflow_get", {"runId": submitted["runId"]})
        self.assertEqual(view["state"], "failed")
        self.assertEqual(view["workspaceConflicts"], [])
        self.assertEqual((checkout / "tracked.txt").read_text(), "changed during turn\n")

    def test_transient_git_failure_keeps_stop_pending_for_retry(self):
        board, _submitted, claim, _checkout = self.claimed()
        attempt_id = claim["attempt"]["attemptId"]
        with patch.object(board.store.workflow, "seal_interrupted_turn",
                          side_effect=BoardError("GIT_FAILED", "temporary checkout failure")):
            self.assertEqual(self.recover(board)["interrupted"], 0)
        with board.store.db.read() as connection:
            attempt = connection.execute("SELECT execution_state,shutdown_confirmed FROM attempts"
                                         " WHERE attempt_id=?", (attempt_id,)).fetchone()
            pending = connection.execute("SELECT state,seal_error FROM attempt_interruptions"
                                         " WHERE attempt_id=?", (attempt_id,)).fetchone()
        self.assertEqual(tuple(attempt), ("uncertain", 0))
        self.assertEqual(pending["state"], "sealing")
        self.assertIn("GIT_FAILED", pending["seal_error"])
        self.assertEqual(len(board.store.resource_claims()), 1)
        self.assertEqual(self.recover(board)["interrupted"], 1)
        with board.store.db.read() as connection:
            attempt = connection.execute("SELECT execution_state,shutdown_confirmed FROM attempts"
                                         " WHERE attempt_id=?", (attempt_id,)).fetchone()
        self.assertEqual(tuple(attempt), ("finished", 1))
        self.assertEqual(board.store.resource_claims(), [])
