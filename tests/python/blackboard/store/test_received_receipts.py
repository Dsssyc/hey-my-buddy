"""Reception survives execution/service crashes; settlement waits for full stop."""
from pathlib import Path
import hashlib
from unittest.mock import patch

from admission import admit_execution
from support import BoardTestCase, FakeClock, publish_result_receipt
from blackboard.store.test_interruption_recovery import IDENTITY
from hey_my_buddy.blackboard.tasks import interruption, reminders
from hey_my_buddy.blackboard.service.execution_scheduler import (
    ExecutionScheduler, _ExecutionObservation, _RecoveredExecution,
)
from hey_my_buddy.errors import BoardError
from hey_my_buddy.protocol import process_identity as processes
from hey_my_buddy.protocol.execution import RECEIPT, read_execution_receipt, fsync_json
from hey_my_buddy.protocol.run_identity import RunIdentity
from hey_my_buddy import private_dirs
from hey_my_buddy.private_files import publish_bytes, read_bytes


class ReceivedReceiptTests(BoardTestCase):
    def test_service_verifies_a_long_artifact_and_keeps_an_ordinary_location(self):
        board = self.board()
        root = self.directory / 'long-artifacts'
        self.addCleanup(private_dirs.remove_tree, root)
        directory = root
        while len(str(directory)) < 310:
            directory /= 'directory-' + 'a' * 35
        target = private_dirs.ensure_private_dir(directory) / 'result.bin'
        content = b'long artifact bytes'
        publish_bytes(target, content)
        claimed = {'kind': 'file', 'location': str(target), 'contentHash': hashlib.sha256(content).hexdigest(), 'sizeBytes': len(content)}
        verified = board.store._verify_artifacts([claimed])
        self.assertEqual(verified, [claimed])
        self.assertNotIn('\\\\?\\', verified[0]['location'])

    def claimed(self, report=None):
        self.clock = FakeClock()
        board = self.board(max_concurrent=1, clock=self.clock)
        task = board.submit_execution(requestId="receipt", task="work", cwd=str(self.workdir()),
                                      adapter="command", argv=["echo", "work"], expectedSeconds=10)["task"]
        claim = admit_execution(board.client(), "n" * 32)["claim"]
        actor = {"attemptId": claim["attempt"]["attemptId"], "generation": 1, "nonce": "n" * 32}
        board.call("execution_progress", {**actor, "phase": "executing", "processIdentity": IDENTITY})
        reference = publish_result_receipt(board.directory, {
            **actor, **(report or {"status": "ok", "shutdownConfirmed": True, "result": {"text": "done"}})})
        self.attempt_id = actor["attemptId"]
        self.run_id = task["runId"]
        self.receipt_path = Path(reference["receipt"]["path"])
        self.original = read_bytes(self.receipt_path)
        return board, claim, reference

    def assert_settlement(self, board, *, pending):
        """The four exactly-once facts at both sides of the final transaction."""
        with board.store.db.read() as connection:
            completions = connection.execute(
                "SELECT COUNT(*) FROM events WHERE attempt_id=? AND kind IN"
                " ('task.completed','task.failed','task.cancelled')", (self.attempt_id,)).fetchone()[0]
            received = connection.execute("SELECT COUNT(*) FROM attempt_received_receipts WHERE attempt_id=?",
                                          (self.attempt_id,)).fetchone()[0]
            occupied = connection.execute(
                f"SELECT COUNT(*) FROM attempts WHERE attempt_id=? AND ({board.store.UNRESOLVED_SQL})",
                (self.attempt_id,)).fetchone()[0]
        self.assertEqual(completions, 0 if pending else 1)
        self.assertEqual(received, 1 if pending else 0)
        self.assertEqual(occupied, int(pending))
        self.assertEqual(bool(board.store.resource_claims()), pending)
        self.assertEqual(read_bytes(self.receipt_path), self.original)

    def recover(self, board):
        with patch.object(processes, "inspect_process_identity", return_value="identity-absent"):
            return board.store.recover_executions(self.attempt_id)

    def assert_final(self, board):
        self.assert_settlement(board, pending=False)
        self.assertEqual(self.recover(board), [])
        self.assert_settlement(board, pending=False)

    def test_execution_crashes_after_receipt_before_submission(self):
        board, _claim, _reference = self.claimed()
        finalize = board.store.finalize_execution_receipt

        def after_reception(*args):
            self.assert_settlement(board, pending=True)
            return finalize(*args)

        with patch.object(board.store, "finalize_execution_receipt", side_effect=after_reception):
            self.assertEqual(len(self.recover(board)), 1)
        self.assert_final(board)

    def test_reception_commits_but_reply_is_lost_then_service_restarts(self):
        board, _claim, reference = self.claimed()
        self.assertEqual(board.call("execution_result", reference), {"received": True})
        self.assertEqual(board.call("execution_result", reference), {"received": True})
        self.assert_settlement(board, pending=True)
        board._resources.close()
        with patch.object(processes, "inspect_process_identity", return_value="alive"):
            restarted = self.board(max_concurrent=1, clock=self.clock)
            self.assertEqual(restarted.store.reconcile_startup()["uncertain"], 0)
        self.assertEqual(restarted.call("execution_result", reference), {"received": True})
        self.assertEqual(restarted.client().get(runId=self.run_id)["attemptState"], "finalizing")
        self.assert_settlement(restarted, pending=True)
        self.recover(restarted)
        self.assert_final(restarted)

    def test_acknowledged_exit_never_reconnects_or_records_unknown_connection(self):
        board, claim, reference = self.claimed()
        board.call("execution_result", reference)
        scheduler = ExecutionScheduler(board.service, live_registry=board.live_registry)
        scheduler._unknown(self.attempt_id, BoardError("LIVE_UNAVAILABLE", "acknowledged peer exited"))
        scheduler.store.execution_connection(self.attempt_id, connected=True)
        location = _RecoveredExecution(self.receipt_path.parent,
            {"taskId": self.run_id, "attemptId": self.attempt_id, "generation": 1},
            RunIdentity(task_id=self.run_id, attempt_id=self.attempt_id, generation=1, invocation_id="execution"))
        scheduler._observed[self.attempt_id] = _ExecutionObservation(self.run_id, scheduler._condition)
        with patch("hey_my_buddy.blackboard.service.execution_scheduler.inspect_process_identity",
                   side_effect=["alive", "identity-absent"]), patch.object(
                   scheduler, "_endpoint", side_effect=AssertionError("received execution must not reconnect")):
            scheduler._monitor(claim, location, IDENTITY)
        with board.store.db.read() as connection:
            self.assertEqual(connection.execute(
                "SELECT COUNT(*) FROM events WHERE kind='execution.unknown'").fetchone()[0], 0)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM execution_issues").fetchone()[0], 0)
        with self.assertRaises(FileNotFoundError):
            read_bytes(self.receipt_path.parent / "result-delivery.json")
        self.assert_final(board)

    def test_force_and_unknown_stop_keep_capacity_claims_and_private_material(self):
        board, _claim, reference = self.claimed({"status": "failed", "shutdownConfirmed": False})
        board.call("execution_result", reference)
        credential = self.receipt_path.parent / "agent-credential.json"
        publish_bytes(credential, b"private fixture")
        native = {**IDENTITY, "pid": 456, "processGroupId": 456}
        journal = processes.native_process_journal_path(self.receipt_path.parent, IDENTITY)
        fsync_json(journal, {"binding": {"taskId": self.run_id, "attemptId": self.attempt_id, "generation": 1},
                             "pending": [], "identities": [native]})
        with patch("hey_my_buddy.blackboard.service.execution_scheduler.kill_verified_process_group") as kill:
            ExecutionScheduler._force_if_required(IDENTITY)
            kill.assert_called_once_with(IDENTITY)
        for observation in ("alive", "unknown"):
            with patch.object(processes, "inspect_process_identity", return_value=observation):
                self.assertEqual(board.store.recover_executions(self.attempt_id), [])
            self.assert_settlement(board, pending=True)
        with patch.object(processes, "stop_orphan_process", return_value="unknown"):
            self.assertEqual(self.recover(board), [])
        self.assert_settlement(board, pending=True)
        self.assertEqual(read_bytes(credential), b"private fixture")
        queued = board.submit_execution(requestId="conflict", task="conflict", cwd=str(self.workdir()),
                                         adapter="command", argv=["echo", "conflict"])["task"]
        self.assertIsNone(board.store.admit_execution("x" * 32, queued["runId"])["claim"])
        with patch.object(processes, "stop_orphan_process", return_value="orphan-stopped"):
            self.assertEqual(len(self.recover(board)), 1)
        with self.assertRaises(FileNotFoundError):
            read_bytes(credential)
        self.assert_final(board)

    def test_final_import_crashes_before_commit_and_retries_the_same_receipt(self):
        board, _claim, reference = self.claimed()
        board.call("execution_result", reference)
        board.store._message_post(self.run_id, "pending", "status?")
        with patch.object(board.store.workflow, "child_settled", side_effect=RuntimeError("import interrupted")):
            self.assertEqual(self.recover(board), [])
        self.assert_settlement(board, pending=True)
        self.assertEqual(board.client().get_message("pending", runId=self.run_id)["state"], "queued")
        self.recover(board)
        self.assertEqual(board.client().get_message("pending", runId=self.run_id)["state"], "unavailable")
        self.assert_final(board)

    def test_final_commit_survives_service_loss_before_observer_returns(self):
        board, _claim, reference = self.claimed()
        board.call("execution_result", reference)
        receipt = read_execution_receipt(self.receipt_path.parent)
        with patch.object(board.store, "_notify", side_effect=RuntimeError("service died after commit")):
            with self.assertRaisesRegex(RuntimeError, "after commit"):
                board.store.finalize_execution_receipt(self.attempt_id, receipt, {"kind": "identity-absent"})
        self.assert_final(board)
        board._resources.close()
        restarted = self.board(max_concurrent=1, clock=self.clock)
        with patch.object(processes, "inspect_process_identity", return_value="identity-absent"):
            restarted.store.reconcile_startup()
        self.assert_final(restarted)

    def test_deterministic_error_after_ack_settles_failed_in_the_same_recovery(self):
        board, _claim, reference = self.claimed({"status": "ok", "shutdownConfirmed": True,
            "artifacts": [{"location": str(self.directory / "missing"), "contentHash": "a" * 64}]})
        with patch.object(board.store, "_verify_artifacts", side_effect=AssertionError("hashing during reception")):
            self.assertEqual(board.call("execution_result", reference), {"received": True})
        self.assert_settlement(board, pending=True)
        self.assertEqual(len(self.recover(board)), 1)
        result = board.client().result(runId=self.run_id)["selectedAttempt"]["result"]
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["result"]["microTaskError"]["code"], "ARTIFACT_MISSING")
        self.assertNotIn("receiptRejection", result["result"])
        self.assertFalse((self.receipt_path.parent / "receipt-rejection.json").exists())
        self.assert_final(board)

    def test_received_attempt_has_no_new_or_pending_reminders(self):
        board, _claim, reference = self.claimed()
        self.clock.advance(10)
        reminders.collect(board.store)
        board.call("execution_result", reference)
        self.clock.advance(100)
        with patch.object(board.store.db, "write", side_effect=AssertionError("new reminder during finalization")):
            self.assertIsNone(reminders.collect(board.store))
        self.assertIsNone(reminders.consume(board.store, {"runId": self.run_id, "reminderWaitId": "wait"}))
        self.assertFalse(board.client().get(runId=self.run_id)["selectedAttempt"]["reminders"]["overdue"])

    def test_stop_source_preserves_unknown_and_allows_immediate_wrap_up_escalation(self):
        board, _claim, _reference = self.claimed()
        with board.store.db.write() as connection:
            request = board.store.workflow._request_attempt_stop
            request(connection, self.run_id, board.store.now(), initiator="unknown")
            request(connection, self.run_id, board.store.now(), initiator="console", wrap_up=False)
            self.assertEqual(board.store._attempt_row(connection, self.attempt_id)["stop_initiator"], "unknown")
            connection.execute("UPDATE attempts SET cancel_requested_at=NULL,stop_initiator=NULL WHERE attempt_id=?",
                               (self.attempt_id,))
            request(connection, self.run_id, board.store.now(), initiator="host")
            request(connection, self.run_id, board.store.now(), initiator="console", wrap_up=False)
            self.assertEqual(board.store._attempt_row(connection, self.attempt_id)["stop_initiator"], "console")
