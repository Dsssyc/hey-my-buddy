"""Queued Host inquiries settle when their attempt actually finishes."""
import hashlib
import json
from pathlib import Path
from unittest.mock import patch

from admission import admit_execution
from support import BoardTestCase, settle_fixture_receipt
from hey_my_buddy.blackboard.tasks import interruption
from hey_my_buddy.protocol import process_identity as processes
from blackboard.store.test_interruption_recovery import IDENTITY


class InquirySettlementTests(BoardTestCase):
    def claimed(self):
        board = self.board()
        client = board.client()
        task = board.submit_execution(requestId="inquiry-settlement", task="work",
            cwd=str(self.workdir()), adapter="command", argv=["true"])["task"]
        attempt = admit_execution(client, "n" * 32, task_id=task["runId"])["claim"]["attempt"]
        board.store._message_post(task["runId"], "q-pending", "Status?")
        return board, client, task, attempt

    def journal_facts(self, board, task, attempt):
        """Source facts may outrun the service's last queued snapshot."""
        records = []
        binding = {"version": 1, "taskId": task["runId"], "attemptId": attempt["attemptId"],
                   "generation": attempt["generation"], "turnId": None}
        for inquiry_id in ("q-answered", "q-delivered"):
            board.store._message_post(task["runId"], inquiry_id, "Status?")
            records.append({**binding, "inquiryId": inquiry_id, "state": "queued", "question": "Status?",
                            "questionSha256": hashlib.sha256(b"Status?").hexdigest(),
                            "delivery": {"admittedDelivery": "cooperative-checkpoint"}})
            records.append({**binding, "inquiryId": inquiry_id, "state": "delivered",
                            "deliveredAt": "2026-10-22T00:00:00Z", "toolCallId": "checkpoint"})
            records.append({**binding, "inquiryId": inquiry_id,
                            **({"state": "answered", "answer": {"text": "already durable"}}
                               if inquiry_id == "q-answered" else {"state": "unavailable"})})
        directory = board.directory / "attempts" / task["runId"] / attempt["attemptId"]
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "inquiry.results.jsonl").write_text("".join(json.dumps(record) + "\n" for record in records), encoding="utf-8")

    def assert_imported(self, client, task):
        answered = client.get_message("q-answered", runId=task["runId"])
        self.assertEqual(answered["state"], "answered")
        self.assertEqual(answered["answer"]["text"], "already durable")
        delivered = client.get_message("q-delivered", runId=task["runId"])
        self.assertEqual(delivered["state"], "unavailable")
        self.assertEqual(delivered["reason"]["code"], "inquiry-delivered-at-attempt-end")
        self.assertEqual(delivered["delivery"]["deliveredAt"], "2026-10-22T00:00:00Z")

    def assert_settled_once(self, board, client, task, attempt):
        message = client.get_message("q-pending", runId=task["runId"])
        self.assertEqual(message["state"], "unavailable")
        self.assertEqual(message["reason"], {"code": "inquiry-queued-at-attempt-end", "params": {}})
        events = client.read_events(runId=task["runId"])["events"]
        updates = [event for event in events if event["kind"] == "message.updated"
                   and event["payload"].get("state") == "unavailable"
                   and event["payload"].get("inquiryId") == "q-pending"]
        self.assertEqual(len(updates), 1)
        self.assertEqual(updates[0]["attemptId"], attempt["attemptId"])
        self.assertEqual(updates[0]["payload"]["actor"], {"role": "service", "id": "attempt-finished"})

    def test_cancelled_before_process_creation_settles_on_release(self):
        board, client, task, attempt = self.claimed()
        board.store.task_cancel({"runId": task["runId"], "note": "service stop"}, initiator="service")
        self.assertEqual(client.get_message("q-pending", runId=task["runId"])["state"], "queued")
        board.store.release_execution(attempt["attemptId"], reason="execution-process-not-created")
        self.assertEqual(client.get(runId=task["runId"])["state"], "cancelled")
        self.assert_settled_once(board, client, task, attempt)
        head = board.store.head()
        board.store.release_execution(attempt["attemptId"], reason="execution-process-not-created")
        self.assertEqual(board.store.head(), head)

    def test_identity_absent_recovery_settles_without_execution_receipt(self):
        board, client, task, attempt = self.claimed()
        self.journal_facts(board, task, attempt)
        actor = {"attemptId": attempt["attemptId"], "generation": attempt["generation"], "nonce": "n" * 32}
        board.call("execution_progress", {**actor, "processIdentity": IDENTITY})
        with board.store.db.read() as connection:
            stored = board.store._attempt_row(connection, attempt["attemptId"])
            directory = interruption._directory(board.store, stored)
        journal = processes.native_process_journal_path(directory, IDENTITY)
        Path(journal).parent.mkdir(parents=True, exist_ok=True)
        processes.NativeProcessJournal(Path(journal), {"taskId": task["runId"],
            "attemptId": attempt["attemptId"], "generation": attempt["generation"]})
        with patch.object(processes, "inspect_process_identity", return_value="identity-absent"):
            self.assertEqual(len(board.store.recover_executions()), 1)
        self.assertEqual(client.get(runId=task["runId"])["attemptState"], "finished")
        self.assert_settled_once(board, client, task, attempt)
        self.assert_imported(client, task)

    def test_unconfirmed_result_keeps_question_queued_until_shutdown_is_proven(self):
        board, client, task, attempt = self.claimed()
        self.journal_facts(board, task, attempt)
        report = {"status": "failed", "shutdownConfirmed": False}
        client.submit_result(attempt["attemptId"], attempt["generation"], "n" * 32, report)
        self.assertEqual(client.get(runId=task["runId"])["attemptState"], "finalizing")
        self.assertEqual(client.get_message("q-pending", runId=task["runId"])["state"], "queued")
        settle_fixture_receipt(board, attempt["attemptId"], native_stopped=True)
        self.assertEqual(client.get(runId=task["runId"])["attemptState"], "finished")
        self.assert_settled_once(board, client, task, attempt)
        self.assert_imported(client, task)
        board.store.recover_executions(attempt["attemptId"], stopped_evidence={"kind": "process-reaped"})
        head = board.store.head()
        board.store.recover_executions(attempt["attemptId"], stopped_evidence={"kind": "process-reaped"})
        self.assertEqual(board.store.head(), head)

    def test_confirmed_result_from_uncertain_state_settles_question(self):
        board, client, task, attempt = self.claimed()
        self.journal_facts(board, task, attempt)
        board.store.execution_connection(attempt["attemptId"], connected=False)
        self.assertEqual(client.get(runId=task["runId"])["attemptState"], "uncertain")
        self.assertEqual(client.get_message("q-pending", runId=task["runId"])["state"], "queued")
        client.submit_result(attempt["attemptId"], attempt["generation"], "n" * 32,
                             {"status": "ok", "shutdownConfirmed": True})
        self.assertEqual(client.get(runId=task["runId"])["attemptState"], "finished")
        self.assert_settled_once(board, client, task, attempt)
        self.assert_imported(client, task)

    def test_forced_stop_confirmation_imports_answers_before_settlement(self):
        board, client, task, attempt = self.claimed()
        self.journal_facts(board, task, attempt)
        board.store.record_execution_deadline(attempt["attemptId"], "forced-stop")
        client.submit_result(attempt["attemptId"], attempt["generation"], "n" * 32,
                             {"status": "cancelled", "shutdownConfirmed": False})
        settle_fixture_receipt(board, attempt["attemptId"], native_stopped=True)
        self.assert_settled_once(board, client, task, attempt)
        self.assert_imported(client, task)

    def test_cancelled_result_imports_answers_before_settlement(self):
        board, client, task, attempt = self.claimed()
        self.journal_facts(board, task, attempt)
        board.store.task_cancel({"runId": task["runId"], "note": "cancel"})
        client.submit_result(attempt["attemptId"], attempt["generation"], "n" * 32,
                             {"status": "cancelled", "shutdownConfirmed": True})
        self.assert_settled_once(board, client, task, attempt)
        self.assert_imported(client, task)
