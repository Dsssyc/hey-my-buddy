"""Durable delivery and stop facts owned by one execution process."""
from __future__ import annotations

import os
import json
import signal
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from hey_my_buddy.buddy.runtime import execution as entry
from hey_my_buddy.buddy.runtime.receipts import deliver_receipt
from hey_my_buddy.errors import BoardError
from hey_my_buddy.protocol.execution import read_execution_receipt, receipt_reference


CLAIM = {"task": {"taskId": "task-1", "spec": {"adapter": "command"}},
         "attempt": {"taskId": "task-1", "attemptId": "attempt-1", "generation": 1}}


class ExecutionDeliveryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.state = Path(temporary.name)
        self.execution = entry.Execution(CLAIM, self.state, "n" * 64, client=mock.Mock())
        self.execution.directory.mkdir(parents=True, mode=0o700)

    def test_receipt_is_private_durable_and_has_no_authority_secret(self):
        report = {"status": "failed", "shutdownConfirmed": True,
                  "terminationReason": "harness-error"}
        with mock.patch.object(self.execution, "_cleanup_attempt_credentials"):
            receipt = self.execution.receipt(CLAIM, report, self.execution.directory)
        self.assertEqual(set(receipt), {"attemptId", "taskId", "generation", "report", "createdAt"})
        self.assertEqual(read_execution_receipt(self.execution.directory), receipt)
        raw = (self.execution.directory / "result-receipt.json").read_text(encoding="utf-8")
        self.assertNotIn("nonce", raw)
        self.assertNotIn("workerId", raw)
        if os.name != "nt":
            self.assertEqual((self.execution.directory / "result-receipt.json").stat().st_mode & 0o777, 0o600)

    def test_receipt_reader_refuses_foreign_binding_and_malformed_envelope(self):
        from hey_my_buddy.protocol.execution import fsync_json, RECEIPT
        binding = {"taskId": "task-1", "attemptId": "attempt-1", "generation": 1}
        receipt = {**binding, "report": {},
                   "createdAt": "2026-10-16T00:00:00+00:00"}
        fsync_json(self.execution.directory / RECEIPT, receipt)
        self.assertEqual(read_execution_receipt(self.execution.directory, binding=binding), receipt)
        with self.assertRaises(BoardError):
            read_execution_receipt(self.execution.directory,
                                   binding={**binding, "generation": 2})
        for changed in ({"taskId": ""}, {"report": []}, {"extra": True}):
            with self.subTest(changed=changed):
                fsync_json(self.execution.directory / RECEIPT, {**receipt, **changed})
                with self.assertRaises(BoardError):
                    read_execution_receipt(self.execution.directory, binding=binding)

    def test_offline_delivery_retries_same_attempt_result_after_flushing_hint(self):
        receipt = {"attemptId": "attempt-1", "taskId": "task-1", "generation": 1,
                   "report": {"status": "ok"},
                   "createdAt": "2026-10-16T00:00:00+00:00"}
        from hey_my_buddy.buddy.runtime.receipts import write_receipt
        write_receipt(self.execution.directory, receipt)
        reference = receipt_reference(self.execution.directory)
        calls = []

        class Client:
            def submit_result(self, *args):
                calls.append(("result", args))
                if len([item for item in calls if item[0] == "result"]) == 1:
                    raise BoardError("SERVICE_UNAVAILABLE", "offline")

        with mock.patch("hey_my_buddy.buddy.runtime.receipts.time.sleep"):
            accepted = deliver_receipt(Client(), receipt, self.execution.directory, nonce="n" * 64,
                                       flush_hints=lambda: calls.append(("hint", None)))
        self.assertTrue(accepted)
        self.assertEqual([name for name, _ in calls], ["hint", "result", "hint", "result"])
        self.assertEqual(calls[1][1], calls[3][1])
        self.assertEqual(calls[1][1], ("attempt-1", 1, "n" * 64, reference))
        self.assertFalse((self.execution.directory / "result-receipt.json").exists())
        self.assertTrue((self.execution.directory / "result-delivery.json").exists())

    def test_repeated_wrap_up_does_not_extend_deadline_and_force_escalates(self):
        control = self.execution.control
        control.request_stop("host", 100.0)
        control.request_stop("host", 200.0)
        self.assertEqual((control.requests, control.deadline), (1, 100.0))
        control.request_stop("service", None, force=True)
        self.assertGreater(control.requests, 1)
        self.assertEqual(control.cancel_view["stopInitiator"], "service")

    def test_deferred_signal_is_replayed_after_private_startup_is_read(self):
        from hey_my_buddy.protocol.execution import fsync_json
        startup = self.execution.directory / "execution-start.json"
        fsync_json(startup, {"claim": CLAIM, "stateDir": str(self.state), "nonce": "n" * 64, "sessionRecordPath": None})
        seen = []

        def run(execution):
            seen.append(execution.stop_requested())

        entry._early_signals.append(signal.SIGTERM)
        self.addCleanup(entry._early_signals.clear)
        with mock.patch.object(sys, "argv", ["execution", "--startup", str(startup)]), \
             mock.patch.object(entry.execution_protocol, "self_execution_process_identity", return_value={}), \
             mock.patch.object(entry.Execution, "run", run), \
             mock.patch.object(entry._ExecutionLive, "start"), \
             mock.patch.object(entry._ExecutionLive, "finish"), \
             mock.patch("hey_my_buddy.protocol.process_identity.install_native_process_journal"):
            self.assertEqual(entry.main(), 0)
        self.assertEqual(seen, [True])

    @unittest.skipIf(os.name == "nt", "POSIX session identity")
    def test_execution_can_capture_its_identity_without_a_parent_marker(self):
        command = ("import json; from hey_my_buddy.protocol.execution import "
                   "self_execution_process_identity; "
                   "print(json.dumps(self_execution_process_identity(None)))")
        child = subprocess.run([sys.executable, "-c", command], start_new_session=True,
                               capture_output=True, text=True, timeout=10, encoding="utf-8")
        self.assertEqual(child.returncode, 0, child.stderr)
        identity = json.loads(child.stdout)
        self.assertEqual(identity["pid"], identity["processGroupId"])
        self.assertIsNone(identity["jobName"])

    def test_main_reports_identity_without_a_parent_marker(self):
        from hey_my_buddy.protocol.execution import fsync_json
        startup = self.execution.directory / "execution-start.json"
        fsync_json(startup, {"claim": CLAIM, "stateDir": str(self.state), "nonce": "n" * 64,
                             "jobName": None, "sessionRecordPath": None})
        self.assertFalse((self.state / "attempts/task-1/attempt-1/spawn.marker").exists())
        identity = {"bootId": "linux:00000000-0000-4000-8000-000000000001",
                    "pid": 301, "startedAt": 105, "processGroupId": 301, "jobName": None}
        client = mock.Mock()

        def execute(_execution):
            client.progress.assert_called_once()
            return {"report": {"status": "ok"}}

        with mock.patch.object(sys, "argv", ["execution", "--startup", str(startup)]), \
             mock.patch.object(entry.execution_protocol, "self_execution_process_identity",
                               return_value=identity), \
             mock.patch.object(entry, "BoardClient", return_value=client), \
             mock.patch.object(entry.Execution, "execute", execute), \
             mock.patch.object(entry, "deliver_receipt", return_value=True), \
             mock.patch.object(entry._ExecutionLive, "start"), \
             mock.patch.object(entry._ExecutionLive, "finish"), \
             mock.patch.object(signal, "signal"), \
             mock.patch("hey_my_buddy.protocol.process_identity.install_native_process_journal"):
            self.assertEqual(entry.main(), 0)
        client.progress.assert_called_once_with(
            "attempt-1", 1, "n" * 64, process_identity=identity, phase="executing", data=None)

    def test_native_work_waits_for_initial_identity_acknowledgement(self):
        client = mock.Mock()
        journal_installed = []
        def progress(*_args, **_kwargs):
            self.assertEqual(journal_installed, [True])
            if client.progress.call_count == 1:
                raise BoardError("SERVICE_UNAVAILABLE", "offline")
            return {}
        client.progress.side_effect = progress
        execution = entry.Execution(CLAIM, self.state, "n" * 64, client=client)
        execution._identity_pending = {"pid": 301}

        def execute():
            self.assertEqual(client.progress.call_count, 2)
            return {"report": {"status": "ok"}}

        def install(*_args):
            self.assertEqual(client.progress.call_count, 0)
            journal_installed.append(True)

        clock = [0.0]
        def advance(seconds):
            clock[0] += seconds
        with mock.patch.object(execution, "execute", side_effect=execute), \
             mock.patch.object(entry, "deliver_receipt", return_value=True), \
             mock.patch("hey_my_buddy.protocol.process_identity.install_native_process_journal",
                        side_effect=install) as journal, \
             mock.patch.object(entry.time, "monotonic", side_effect=lambda: clock[0]), \
             mock.patch.object(entry.time, "sleep", side_effect=advance) as pause:
            execution.run()
        journal.assert_called_once()
        pause.assert_called_once_with(0.25)

    def test_identity_refusal_prevents_native_work_after_empty_journal(self):
        for code in ("INVALID_ARGUMENT", "CONFLICT"):
            with self.subTest(code=code):
                client = mock.Mock()
                client.progress.side_effect = BoardError(code, "identity refused")
                execution = entry.Execution(CLAIM, self.state, "n" * 64, client=client)
                execution._identity_pending = {"pid": 301}
                with mock.patch.object(execution, "execute") as native, \
                     mock.patch("hey_my_buddy.protocol.process_identity.install_native_process_journal") as journal:
                    with self.assertRaises(BoardError) as caught:
                        execution.run()
                self.assertEqual(caught.exception.code, code)
                native.assert_not_called()
                journal.assert_called_once()

    def test_journal_failure_prevents_identity_acknowledgement_and_native_work(self):
        client = mock.Mock()
        execution = entry.Execution(CLAIM, self.state, "n" * 64, client=client)
        execution._identity_pending = {"pid": 301}
        with mock.patch.object(execution, "execute") as native, \
             mock.patch("hey_my_buddy.protocol.process_identity.install_native_process_journal",
                        side_effect=OSError("journal is unavailable")):
            with self.assertRaises(OSError):
                execution.run()
        client.progress.assert_not_called()
        native.assert_not_called()

    def test_identity_acknowledgement_uses_bounded_backoff_while_service_is_offline(self):
        client = mock.Mock()
        client.progress.side_effect = [BoardError("SERVICE_UNAVAILABLE", "offline"),
                                       BoardError("SERVICE_UNAVAILABLE", "offline"), {}]
        execution = entry.Execution(CLAIM, self.state, "n" * 64, client=client)
        execution._identity_pending = {"pid": 301}
        clock = [0.0]
        def advance(seconds):
            clock[0] += seconds
        with mock.patch.object(execution, "execute", return_value={"report": {}}), \
             mock.patch.object(entry, "deliver_receipt", return_value=True), \
             mock.patch("hey_my_buddy.protocol.process_identity.install_native_process_journal"), \
             mock.patch.object(entry.time, "monotonic", side_effect=lambda: clock[0]), \
             mock.patch.object(entry.time, "sleep", side_effect=advance) as pause:
            execution.run()
        self.assertEqual([call.args[0] for call in pause.call_args_list], [0.25, 0.5])

    def test_fatal_hint_refusal_ends_receipt_delivery_with_durable_receipt(self):
        receipt = {"attemptId": "attempt-1", "taskId": "task-1", "generation": 1,
                   "report": {"status": "ok"},
                   "createdAt": "2026-10-16T00:00:00+00:00"}
        from hey_my_buddy.buddy.runtime.receipts import write_receipt
        write_receipt(self.execution.directory, receipt)
        with self.assertRaises(BoardError) as caught:
            deliver_receipt(mock.Mock(), receipt, self.execution.directory, nonce="n" * 64,
                            flush_hints=lambda: (_ for _ in ()).throw(BoardError("CONFLICT", "wrong attempt")))
        self.assertEqual(caught.exception.code, "CONFLICT")
        self.assertEqual(read_execution_receipt(self.execution.directory), receipt)

    def test_actual_codex_native_session_survives_execution_receipt(self):
        # The retired worker_sessions test exercised this real native fixture;
        # the execution receipt is now the boundary that must retain its facts.
        from buddy.harnesses.codex.test_codex import CodexAdapterTests
        fixture = CodexAdapterTests(
            "test_completed_native_turn_has_structured_provenance_and_preserved_final_activity")
        fixture.setUp()
        try:
            with mock.patch.dict(os.environ, fixture.environment):
                outcome = fixture.execute(fixture.context())
            self.assertTrue(outcome.shutdown_confirmed)
            claim = {"task": {"taskId": "micro-task-1", "spec": {"adapter": "codex"}},
                     "attempt": {"taskId": "micro-task-1", "attemptId": "attempt-1", "generation": 1}}
            execution = entry.Execution(claim, self.state, "n" * 64, client=mock.Mock())
            execution.directory.mkdir(parents=True, mode=0o700)
            receipt = execution.receipt(claim, {"status": outcome.status,
                "result": outcome.result, "shutdownConfirmed": outcome.shutdown_confirmed},
                execution.directory)
            saved = read_execution_receipt(execution.directory, binding={
                "taskId": "micro-task-1", "attemptId": "attempt-1", "generation": 1})
            self.assertEqual(saved, receipt)
            session = saved["report"]["result"]["nativeSession"]
            self.assertEqual(session["storageScope"], "harness-user-store")
            self.assertEqual(session["storageOwner"], "harness-user-store")
            self.assertEqual(saved["report"]["result"]["nativeCheckpoint"]["attemptId"], "attempt-1")
            self.assertEqual(saved["report"]["result"]["turn"]["taskId"], "micro-task-1")
        finally:
            fixture.doCleanups()

    def test_offline_hints_back_off_and_keep_only_the_latest_value(self):
        client = self.execution.client
        client.progress.side_effect = [BoardError('SERVICE_UNAVAILABLE', 'offline'), None]
        self.execution.hint(waitingReason={'code': 'router-input-unknown', 'params': {}})
        with mock.patch.object(entry.time, 'monotonic', return_value=10.0):
            self.assertFalse(self.execution.flush_hints())
        self.execution.hint(waitingReason=None)
        with mock.patch.object(entry.time, 'monotonic', return_value=10.1):
            self.assertFalse(self.execution.flush_hints())
            self.assertEqual(client.progress.call_count, 1)
        with mock.patch.object(entry.time, 'monotonic', return_value=10.25):
            self.assertTrue(self.execution.flush_hints())
        self.assertEqual(client.progress.call_count, 2)
        self.assertEqual(client.progress.call_args.kwargs['data'], {'waitingReason': None})
        self.assertEqual(self.execution._hints, {})
