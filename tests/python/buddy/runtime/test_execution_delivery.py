"""Durable delivery and stop facts owned by one execution process."""
from __future__ import annotations

import os
import json
import signal
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from hey_my_buddy.buddy.runtime import execution as entry
from hey_my_buddy.buddy.runtime.receipts import deliver_receipt
from hey_my_buddy.buddy.harnesses.base import AdapterOutcome
from hey_my_buddy.errors import BoardError
from hey_my_buddy.protocol.execution import read_execution_receipt, receipt_reference


CLAIM = {"task": {"taskId": "task-1", "spec": {"adapter": "command"}},
         "attempt": {"taskId": "task-1", "attemptId": "attempt-1", "generation": 1}}


class WindowsOSProxy:
    """Pretend only this module's ``os.name`` is Windows; other modules stay POSIX."""

    name = "nt"

    def __getattr__(self, attribute):
        return getattr(os, attribute)


class ExecutionDeliveryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.state = Path(temporary.name)
        self.execution = entry.Execution(CLAIM, self.state, "n" * 64, client=mock.Mock())
        self.execution.directory.mkdir(parents=True, mode=0o700)

    def budget_execution(self, client, suffix=""):
        task_id, attempt_id = "router-task" + suffix, "router-attempt" + suffix
        claim = {"task": {"taskId": task_id, "spec": {
            "adapter": "decision", "routerBudgetSeconds": 60, "cwd": str(self.state)}},
            "attempt": {"taskId": task_id, "attemptId": attempt_id, "generation": 1},
            "decisionInput": {"buddy": {"harness": "codex"}, "routingMode": "fast",
                              "budget": {"timeoutSeconds": 60}}}
        execution = entry.Execution(claim, self.state, "n" * 64, client=client)
        execution.directory.mkdir(parents=True, mode=0o700)
        return execution, claim

    def execute_budget(self, execution, claim, on_start):
        handle = mock.Mock(cancel_requested=False, stop_initiator=None, grace_expired=False,
                           result=None, evidence_retention_failure=None)
        handle.wait.return_value = 0
        def start(*_args):
            on_start()
            return handle
        with mock.patch("hey_my_buddy.install.runtime.resolve_runtime", return_value={"identity": "fixture"}), \
             mock.patch.object(entry.role_seam, "worker_executor"), \
             mock.patch.object(entry.role_seam, "prepare_worker_run", return_value=SimpleNamespace(available=True, error=None)), \
             mock.patch.object(entry.role_seam, "worker_start", side_effect=start), \
             mock.patch.object(entry.role_seam, "worker_collect", return_value=AdapterOutcome("ok", shutdown_confirmed=True)):
            return execution._execute_selected(claim, claim["task"], claim["task"]["spec"],
                claim["attempt"], execution.directory, {"preflightControl": mock.Mock()})

    def test_budget_is_notified_before_native_start_and_offline_notice_is_retained(self):
        for offline in (False, True):
            with self.subTest(offline=offline):
                client = mock.Mock()
                execution, claim = self.budget_execution(client, suffix=f"-{int(offline)}")
                delivered = []
                def progress(*_args, **kwargs):
                    budget = json.loads((execution.directory / entry.BUDGET).read_bytes())
                    self.assertEqual(budget["binding"], {"taskId": claim["task"]["taskId"],
                        "attemptId": claim["attempt"]["attemptId"], "generation": 1})
                    self.assertEqual(kwargs["data"], {"waitingReason": None})
                    if offline and client.progress.call_count == 1:
                        raise BoardError("SERVICE_UNAVAILABLE", "offline")
                    delivered.append(budget["deadline"])
                client.progress.side_effect = progress
                def start():
                    self.assertEqual(client.progress.call_count, 1)
                    if offline:
                        self.assertIn("waitingReason", execution._hints)
                        with mock.patch.object(entry.time, "monotonic", return_value=execution._next_hint_at):
                            self.assertTrue(execution.flush_hints())
                    self.assertEqual(len(delivered), 1)
                receipt = self.execute_budget(execution, claim, start)
                self.assertEqual(receipt["report"]["status"], "ok")

    def test_an_older_identical_hint_ack_cannot_erase_the_budget_notification(self):
        client = mock.Mock()
        execution, claim = self.budget_execution(client)
        entered, release, published = threading.Event(), threading.Event(), threading.Event()
        seen_budget, errors = [], []
        def progress(*_args, **_kwargs):
            seen_budget.append((execution.directory / entry.BUDGET).exists())
            if client.progress.call_count == 1:
                entered.set()
                if not release.wait(2):
                    raise RuntimeError("fixture hint acknowledgement remained gated")
        client.progress.side_effect = progress
        execution.hint(waitingReason=None)
        older = threading.Thread(target=execution.flush_hints, daemon=True)
        original_publish = entry.fsync_json
        def publish(path, *args, **kwargs):
            original_publish(path, *args, **kwargs)
            if path.name == entry.BUDGET:
                published.set()
        def start():
            self.assertEqual(seen_budget, [False, True])
        def run():
            try:
                self.execute_budget(execution, claim, start)
            except BaseException as error:
                errors.append(error)
        current = threading.Thread(target=run, daemon=True)
        with mock.patch.object(entry, "fsync_json", side_effect=publish):
            older.start()
            try:
                self.assertTrue(entered.wait(2))
                current.start()
                self.assertTrue(published.wait(2))
                release.set()
                older.join(2)
                current.join(2)
                self.assertFalse(older.is_alive())
                self.assertFalse(current.is_alive())
                self.assertEqual(errors, [])
                self.assertEqual(seen_budget, [False, True])
            finally:
                release.set()
                older.join(2)
                if current.ident is not None:
                    current.join(2)

    def test_receipt_is_private_durable_and_has_no_authority_secret(self):
        report = {"status": "failed", "shutdownConfirmed": True,
                  "terminationReason": "harness-error"}
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

        def flush_hints():
            calls.append(("hint", None))
            return True

        with mock.patch("hey_my_buddy.buddy.runtime.receipts.time.sleep"):
            accepted = deliver_receipt(Client(), receipt, self.execution.directory, nonce="n" * 64,
                                       flush_hints=flush_hints)
        self.assertTrue(accepted)
        self.assertEqual([name for name, _ in calls], ["hint", "result", "hint", "result"])
        self.assertEqual(calls[1][1], calls[3][1])
        self.assertEqual(calls[1][1], ("attempt-1", 1, "n" * 64, reference))
        self.assertFalse((self.execution.directory / "result-receipt.json").exists())
        self.assertTrue((self.execution.directory / "result-delivery.json").exists())

    def test_uncertain_delivery_fails_once_and_preserves_exact_receipt(self):
        from hey_my_buddy.buddy.runtime.receipts import write_receipt, DELIVERY, RECEIPT
        receipt = {"attemptId": "attempt-1", "taskId": "task-1", "generation": 1,
                   "report": {"status": "ok"}, "createdAt": "2026-10-16T00:00:00+00:00"}
        write_receipt(self.execution.directory, receipt)
        original = (self.execution.directory / RECEIPT).read_bytes()
        for error in (BoardError("INTERNAL_ERROR", "commit failed"),
                      BoardError("INVALID_RESPONSE", "reply lost"), RuntimeError("unexpected defect")):
            with self.subTest(error=error):
                client = mock.Mock()
                client.submit_result.side_effect = error
                with self.assertRaises(type(error)) as caught, \
                     mock.patch("hey_my_buddy.buddy.runtime.receipts.time.sleep") as pause:
                    deliver_receipt(client, receipt, self.execution.directory, nonce="n" * 64)
                self.assertIs(caught.exception, error)
                client.submit_result.assert_called_once()
                pause.assert_not_called()
                self.assertEqual((self.execution.directory / RECEIPT).read_bytes(), original)
                self.assertFalse((self.execution.directory / DELIVERY).exists())

    def test_explicit_refusal_records_reason_and_retains_receipt_as_evidence(self):
        from hey_my_buddy.buddy.runtime.receipts import write_receipt, DELIVERY
        for code in ("ATTEMPT_FINISHED", "STALE_GENERATION", "UNAUTHORIZED", "CONFLICT",
                     "INVALID_ARGUMENT", "RECEIPT_MISMATCH", "ARTIFACT_MISSING", "ARTIFACT_MISMATCH"):
            with self.subTest(code=code):
                directory = self.execution.directory / code
                directory.mkdir()
                receipt = {"attemptId": "attempt-1", "taskId": "task-1", "generation": 1,
                           "report": {"status": "ok"}, "createdAt": "2026-10-16T00:00:00+00:00"}
                write_receipt(directory, receipt)
                client = mock.Mock()
                client.submit_result.side_effect = BoardError(code, "attempt refused")
                self.assertFalse(deliver_receipt(client, receipt, directory, nonce="n" * 64))
                self.assertEqual(read_execution_receipt(directory), receipt)
                self.assertEqual(json.loads((directory / DELIVERY).read_text()),
                                 {"taskId": "task-1", "attemptId": "attempt-1", "generation": 1,
                                  "accepted": False, "error": {"code": code, "message": "attempt refused"}})

    def test_result_waits_for_hint_already_in_backoff(self):
        receipt = {"attemptId": "attempt-1", "taskId": "task-1", "generation": 1,
                   "report": {"status": "ok"},
                   "createdAt": "2026-10-16T00:00:00+00:00"}
        from hey_my_buddy.buddy.runtime.receipts import write_receipt
        write_receipt(self.execution.directory, receipt)
        client = self.execution.client
        client.progress.side_effect = [BoardError("SERVICE_UNAVAILABLE", "offline"), None]
        self.execution.hint(activity={"phase": "completed"})
        clock = [10.0]
        submitted = []

        def advance(seconds):
            client.submit_result.assert_not_called()
            self.assertEqual(read_execution_receipt(self.execution.directory), receipt)
            clock[0] += seconds

        def submit_result(*_args):
            submitted.append((client.progress.call_count, dict(self.execution._hints)))

        client.submit_result.side_effect = submit_result
        with mock.patch.object(entry.time, "monotonic", side_effect=lambda: clock[0]), \
             mock.patch.object(entry.time, "sleep", side_effect=advance) as pause:
            self.assertFalse(self.execution.flush_hints())
            clock[0] = 10.1
            self.assertTrue(deliver_receipt(client, receipt, self.execution.directory,
                                           nonce="n" * 64,
                                           flush_hints=self.execution._flush_delivery_hints))
        self.assertEqual(submitted, [(2, {})])
        pause.assert_called_once_with(0.25)
        self.assertEqual(client.progress.call_args.kwargs["data"],
                         {"activity": {"phase": "completed"}})
        client.submit_result.assert_called_once()
        self.assertIsNone(read_execution_receipt(self.execution.directory))

    def test_refusal_is_durable_before_a_log_failure_can_exit_delivery(self):
        from hey_my_buddy.buddy.runtime.receipts import write_receipt
        from hey_my_buddy.protocol.execution import read_execution_delivery
        receipt = {'taskId': 'task-1', 'attemptId': 'attempt-1', 'generation': 1,
                   'report': {'status': 'ok'}, 'createdAt': 'fixture'}
        write_receipt(self.execution.directory, receipt)
        self.execution.client.submit_result.side_effect = BoardError('INVALID_ARGUMENT', 'report rejected')
        with self.assertRaisesRegex(OSError, 'log failed'):
            deliver_receipt(self.execution.client, receipt, self.execution.directory, nonce='n' * 64,
                            log=mock.Mock(side_effect=OSError('log failed')))
        decision = read_execution_delivery(self.execution.directory,
                    binding={'taskId': 'task-1', 'attemptId': 'attempt-1', 'generation': 1})
        self.assertEqual(decision['error'], {'code': 'INVALID_ARGUMENT', 'message': 'report rejected'})
        self.assertEqual(read_execution_receipt(self.execution.directory), receipt)

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
        # A Windows main reads the published root from the marker before it can
        # replay the deferred stop; POSIX never reads it.
        evidence = self.state / "attempts/task-1/attempt-1"
        evidence.mkdir(parents=True, exist_ok=True)
        fsync_json(evidence / "spawn.marker",
                   {"taskId": "task-1", "attemptId": "attempt-1", "generation": 1,
                    "processIdentity": {"pid": 301, "startedAt": 1339247086400000000,
                                        "jobName": "Local\\HeyMyBuddy-00000000-0000-4000-8000-0000000000de"}})
        seen = []

        def run(execution):
            seen.append(execution.stop_requested())

        entry._early_signals.append(signal.SIGTERM)
        self.addCleanup(entry._early_signals.clear)
        with mock.patch.object(sys, "argv", ["execution", "--startup", str(startup)]), \
             mock.patch.object(entry.execution_protocol, "self_process_identity", return_value={}), \
             mock.patch.object(entry.Execution, "run", run), \
             mock.patch.object(entry._ExecutionLive, "start"), \
             mock.patch.object(entry._ExecutionLive, "finish"), \
             mock.patch("hey_my_buddy.protocol.process_identity.install_native_process_journal"):
            self.assertEqual(entry.main(), 0)
        self.assertEqual(seen, [True])

    @unittest.skipIf(os.name == "nt", "POSIX session identity")
    def test_execution_can_capture_its_identity_without_a_parent_marker(self):
        command = ("import json; from hey_my_buddy.protocol.execution import "
                   "self_process_identity; "
                   "print(json.dumps(self_process_identity(None)))")
        child = subprocess.run([sys.executable, "-c", command], start_new_session=True,
                               capture_output=True, text=True, timeout=10, encoding="utf-8")
        self.assertEqual(child.returncode, 0, child.stderr)
        identity = json.loads(child.stdout)
        self.assertEqual(identity["pid"], identity["processGroupId"])
        self.assertIsNone(identity["jobName"])

    @unittest.skipIf(os.name == "nt", "POSIX reports its own identity without a marker")
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
            return {"report": {"status": "ok", "shutdownConfirmed": True}}

        with mock.patch.object(sys, "argv", ["execution", "--startup", str(startup)]), \
             mock.patch.object(entry.execution_protocol, "self_process_identity",
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

    def test_main_reports_the_marker_root_and_carries_its_own_windows_identity(self):
        from hey_my_buddy.protocol.execution import fsync_json
        job_name = "Local\\HeyMyBuddy-00000000-0000-4000-8000-0000000000dd"
        root = {"pid": 301, "startedAt": 1339247086400000000, "jobName": job_name}
        own = {"pid": 402, "startedAt": 815, "jobName": job_name}
        startup = self.execution.directory / "execution-start.json"
        fsync_json(startup, {"claim": CLAIM, "stateDir": str(self.state), "nonce": "n" * 64,
                             "jobName": job_name, "sessionRecordPath": None})
        evidence = self.state / "attempts/task-1/attempt-1"
        evidence.mkdir(parents=True, exist_ok=True)
        fsync_json(evidence / "spawn.marker",
                   {"taskId": "task-1", "attemptId": "attempt-1", "generation": 1, "processIdentity": root})
        client = mock.Mock()
        started = []
        reported = []

        def execute(_execution):
            reported.append(client.progress.call_args)
            return {"report": {"status": "ok", "shutdownConfirmed": True}}

        windows_os = WindowsOSProxy()
        with mock.patch.object(sys, "argv", ["execution", "--startup", str(startup)]), \
             mock.patch.object(entry, "os", windows_os), \
             mock.patch.object(entry.execution_protocol, "self_process_identity", return_value=own), \
             mock.patch.object(entry, "BoardClient", return_value=client), \
             mock.patch.object(entry.Execution, "execute", execute), \
             mock.patch.object(entry, "deliver_receipt", return_value=True), \
             mock.patch.object(entry._ExecutionLive, "start", side_effect=lambda identity: started.append(identity)), \
             mock.patch.object(entry._ExecutionLive, "finish"), \
             mock.patch.object(signal, "signal"), \
             mock.patch("hey_my_buddy.protocol.process_identity.install_native_process_journal") as journal:
            self.assertEqual(entry.main(), 0)
        # The live descriptor binds the marker's root; progress reports the
        # interpreter itself, and no native journal is written on Windows.
        self.assertEqual(started, [root])
        self.assertEqual(reported[0].kwargs["process_identity"], own)
        journal.assert_not_called()

    def test_native_work_waits_for_initial_identity_acknowledgement(self):
        client = mock.Mock()
        journal_installed = []
        def progress(*_args, **_kwargs):
            # POSIX installs the empty journal before the first progress; a
            # Windows execution writes no journal and still gates native work.
            if os.name != "nt":
                self.assertEqual(journal_installed, [True])
            if client.progress.call_count == 1:
                raise BoardError("SERVICE_UNAVAILABLE", "offline")
            return {}
        client.progress.side_effect = progress
        execution = entry.Execution(CLAIM, self.state, "n" * 64, client=client)
        execution._identity_pending = {"pid": 301}

        def execute():
            self.assertEqual(client.progress.call_count, 2)
            return {"report": {"status": "ok", "shutdownConfirmed": True}}

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
        if os.name == "nt":
            journal.assert_not_called()
        else:
            journal.assert_called_once()
        pause.assert_called_once_with(0.25)

    def test_identity_refusal_prevents_native_work(self):
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
                if os.name == "nt":
                    journal.assert_not_called()
                else:
                    journal.assert_called_once()

    @unittest.skipIf(os.name == "nt", "POSIX installs the native journal before acknowledging")
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
        with mock.patch.object(execution, "execute", return_value={"report": {"shutdownConfirmed": True}}), \
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

    def test_unknown_progress_failure_keeps_a_running_control_alive_and_retries(self):
        for error in (BoardError('INTERNAL_ERROR', 'store defect'),
                      BoardError('INVALID_RESPONSE', 'reply lost'), RuntimeError('transport defect')):
            with self.subTest(error=error):
                logs = []
                client = mock.Mock()
                client.progress.side_effect = [error, {}]
                execution = entry.Execution(CLAIM, self.state, 'n' * 64, client=client, log=logs.append)
                execution.hint(activity={'phase': 'running'})
                control = execution.control
                control.handle = SimpleNamespace(live_endpoint=None)
                with mock.patch.object(entry.time, 'monotonic', return_value=10.0), \
                     mock.patch.object(control._wake, 'wait', side_effect=lambda _timeout: control.stop()), \
                     mock.patch.object(entry.role_seam, 'worker_cancel') as cancel, \
                     mock.patch.object(entry.role_seam, 'worker_stop') as stop:
                    control.run()
                self.assertIsNone(control.fatal)
                self.assertIsNone(control.cancel_view)
                cancel.assert_not_called()
                stop.assert_not_called()
                self.assertEqual(execution._hints, {'activity': {'phase': 'running'}})
                self.assertTrue(any('execution progress pending' in line for line in logs))
                with mock.patch.object(entry.time, 'monotonic', return_value=10.25):
                    self.assertTrue(execution.flush_hints())
                self.assertEqual(client.progress.call_count, 2)
                self.assertEqual(execution._hints, {})

    def test_unknown_progress_failure_during_delivery_leaves_receipt_and_exits(self):
        from hey_my_buddy.buddy.runtime.receipts import write_receipt, DELIVERY, RECEIPT
        receipt = {'taskId': 'task-1', 'attemptId': 'attempt-1', 'generation': 1,
                   'report': {'status': 'ok', 'shutdownConfirmed': True}, 'createdAt': 'fixture'}
        write_receipt(self.execution.directory, receipt)
        original = (self.execution.directory / RECEIPT).read_bytes()
        self.execution.hint(waitingReason=None)
        for error in (BoardError('INTERNAL_ERROR', 'store defect'),
                      BoardError('INVALID_RESPONSE', 'reply lost'), RuntimeError('transport defect')):
            with self.subTest(error=error):
                self.execution.client.progress.side_effect = error
                with self.assertRaises(type(error)) as caught:
                    deliver_receipt(self.execution.client, receipt, self.execution.directory, nonce='n' * 64,
                                    flush_hints=self.execution._flush_delivery_hints)
                self.assertIs(caught.exception, error)
                self.execution.client.submit_result.assert_not_called()
                self.assertEqual((self.execution.directory / RECEIPT).read_bytes(), original)
                self.assertEqual(self.execution._hints, {'waitingReason': None})
                self.assertFalse((self.execution.directory / DELIVERY).exists())

    def test_budget_cleanup_failure_exits_with_the_original_durable_receipt(self):
        from hey_my_buddy.protocol.execution import BUDGET, RECEIPT
        report = {'status': 'ok', 'shutdownConfirmed': True}
        receipt = self.execution.receipt(CLAIM, report, self.execution.directory)
        original = (self.execution.directory / RECEIPT).read_bytes()
        budget = self.execution.directory / BUDGET
        budget.write_text('{}')
        unlink = Path.unlink
        def fail_budget(path, **kwargs):
            if path == budget:
                raise OSError('budget cleanup failed')
            return unlink(path, **kwargs)
        with mock.patch.object(self.execution, 'execute', return_value=receipt), \
             mock.patch.object(Path, 'unlink', autospec=True, side_effect=fail_budget):
            with self.assertRaisesRegex(OSError, 'budget cleanup failed'):
                self.execution.run()
        self.assertEqual((self.execution.directory / RECEIPT).read_bytes(), original)
        self.execution.client.submit_result.assert_not_called()
