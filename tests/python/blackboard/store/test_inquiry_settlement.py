"""Queued Host inquiries settle when their attempt actually finishes."""
from pathlib import Path
from unittest.mock import patch

from admission import admit_execution
from support import BoardTestCase
from hey_my_buddy.blackboard.tasks import interruption
from hey_my_buddy.protocol import process_identity as processes
from blackboard.store.test_interruption_recovery import IDENTITY


class InquirySettlementTests(BoardTestCase):
    def claimed(self):
        board = self.board()
        client = board.client()
        task = board.submit_execution(requestId="inquiry-settlement", task="work",
            cwd=str(self.workdir()), adapter="command", argv=["true"])["task"]
        attempt = admit_execution(client, "n" * 32, task_id=task["taskId"])["claim"]["attempt"]
        client.post_question("q-pending", "Status?", runId=task["taskId"])
        return board, client, task, attempt

    def assert_settled_once(self, board, client, task, attempt):
        message = client.get_message("q-pending", runId=task["taskId"])
        self.assertEqual(message["state"], "unavailable")
        self.assertIn("attempt ended", message["reason"])
        events = client.read_events(taskId=task["taskId"])["events"]
        updates = [event for event in events if event["kind"] == "message.updated"
                   and event["payload"].get("state") == "unavailable"]
        self.assertEqual(len(updates), 1)
        self.assertEqual(updates[0]["attemptId"], attempt["attemptId"])
        self.assertEqual(updates[0]["payload"]["actor"], {"role": "service", "id": "attempt-finished"})

    def test_cancelled_before_process_creation_settles_on_release(self):
        board, client, task, attempt = self.claimed()
        board.store.task_cancel({"runId": task["taskId"], "reason": "service stop"}, initiator="service")
        self.assertEqual(client.get_message("q-pending", runId=task["taskId"])["state"], "queued")
        board.store.release_execution(attempt["attemptId"])
        self.assertEqual(client.get(runId=task["taskId"])["state"], "cancelled")
        self.assert_settled_once(board, client, task, attempt)
        head = board.store.head()
        board.store.release_execution(attempt["attemptId"])
        self.assertEqual(board.store.head(), head)

    def test_identity_absent_recovery_settles_without_execution_receipt(self):
        board, client, task, attempt = self.claimed()
        actor = {"attemptId": attempt["attemptId"], "generation": attempt["generation"], "nonce": "n" * 32}
        board.call("execution_progress", {**actor, "processIdentity": IDENTITY})
        with board.store.db.read() as connection:
            stored = board.store._attempt_row(connection, attempt["attemptId"])
            directory = interruption._directory(board.store, stored)
        journal = processes.native_process_journal_path(directory, IDENTITY)
        Path(journal).parent.mkdir(parents=True, exist_ok=True)
        processes.NativeProcessJournal(Path(journal), {"taskId": task["taskId"],
            "attemptId": attempt["attemptId"], "generation": attempt["generation"]})
        with patch.object(processes, "inspect_process_identity", return_value="identity-absent"):
            self.assertEqual(len(board.store.recover_executions()), 1)
        self.assertEqual(client.get(runId=task["taskId"])["attemptState"], "finished")
        self.assert_settled_once(board, client, task, attempt)

    def test_unconfirmed_result_keeps_question_queued_until_shutdown_is_proven(self):
        board, client, task, attempt = self.claimed()
        report = {"status": "failed", "shutdownConfirmed": False}
        client.submit_result(attempt["attemptId"], attempt["generation"], "n" * 32, report)
        self.assertEqual(client.get(runId=task["taskId"])["attemptState"], "uncertain")
        self.assertEqual(client.get_message("q-pending", runId=task["taskId"])["state"], "queued")
        board.store.confirm_execution_shutdown(attempt["attemptId"], {"kind": "identity-absent"})
        self.assertEqual(client.get(runId=task["taskId"])["attemptState"], "finished")
        self.assert_settled_once(board, client, task, attempt)
        head = board.store.head()
        board.store.confirm_execution_shutdown(attempt["attemptId"], {"kind": "identity-absent"})
        self.assertEqual(board.store.head(), head)

    def test_confirmed_result_from_uncertain_state_settles_question(self):
        board, client, task, attempt = self.claimed()
        board.store.execution_connection(attempt["attemptId"], connected=False)
        self.assertEqual(client.get(runId=task["taskId"])["attemptState"], "uncertain")
        self.assertEqual(client.get_message("q-pending", runId=task["taskId"])["state"], "queued")
        client.submit_result(attempt["attemptId"], attempt["generation"], "n" * 32,
                             {"status": "ok", "shutdownConfirmed": True})
        self.assertEqual(client.get(runId=task["taskId"])["attemptState"], "finished")
        self.assert_settled_once(board, client, task, attempt)
