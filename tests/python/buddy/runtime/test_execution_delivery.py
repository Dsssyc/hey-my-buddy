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
from hey_my_buddy.protocol.execution import read_execution_receipt


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
        self.assertEqual(receipt["commandId"], "result:attempt-1")
        self.assertEqual(read_execution_receipt(self.execution.directory), receipt)
        raw = (self.execution.directory / "result-receipt.json").read_text()
        self.assertNotIn("nonce", raw)
        self.assertNotIn("workerId", raw)
        if os.name != "nt":
            self.assertEqual((self.execution.directory / "result-receipt.json").stat().st_mode & 0o777, 0o600)

    def test_offline_delivery_retries_same_command_after_flushing_hint(self):
        receipt = {"attemptId": "attempt-1", "taskId": "task-1", "generation": 1,
                   "commandId": "result:attempt-1", "report": {"status": "ok"},
                   "createdAt": "2026-10-16T00:00:00+00:00"}
        from hey_my_buddy.buddy.runtime.receipts import write_receipt
        write_receipt(self.execution.directory, receipt)
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
        fsync_json(startup, {"claim": CLAIM, "stateDir": str(self.state), "nonce": "n" * 64})
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
                               capture_output=True, text=True, timeout=10)
        self.assertEqual(child.returncode, 0, child.stderr)
        identity = json.loads(child.stdout)
        self.assertEqual(identity["pid"], identity["processGroupId"])
        self.assertIsNone(identity["jobName"])

    def test_main_reports_identity_without_a_parent_marker(self):
        from hey_my_buddy.protocol.execution import fsync_json
        startup = self.execution.directory / "execution-start.json"
        fsync_json(startup, {"claim": CLAIM, "stateDir": str(self.state), "nonce": "n" * 64,
                             "jobName": None})
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
            "attempt-1", 1, "n" * 64, process_identity=identity, phase="running", data=None)

    def test_native_work_waits_for_initial_identity_acknowledgement(self):
        client = mock.Mock()
        client.progress.side_effect = [BoardError("SERVICE_UNAVAILABLE", "offline"), {}]
        execution = entry.Execution(CLAIM, self.state, "n" * 64, client=client)
        execution._identity_pending = {"pid": 301}

        def execute():
            self.assertEqual(client.progress.call_count, 2)
            return {"report": {"status": "ok"}}

        with mock.patch.object(execution, "execute", side_effect=execute), \
             mock.patch.object(entry, "deliver_receipt", return_value=True), \
             mock.patch.object(entry.time, "sleep") as pause:
            execution.run()
        pause.assert_called_once_with(0.25)
