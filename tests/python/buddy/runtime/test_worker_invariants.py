"""Worker invariants that must survive the role seam (ADR-025 step 1-C).

The seam routes the runtime's prepare/start/collect/cancel through
``hey_my_buddy.buddy.roles.controller``. A spawn-marker write failure after
start must stop the live child and produce an honest failed receipt; preparation
results keep their exact receipt mapping. Real private state, real ``command``
children, no board daemon, no harness and no model.
"""
from __future__ import annotations

from contextlib import nullcontext
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

from hey_my_buddy.errors import BoardError
from hey_my_buddy.protocol.client import BoardClient
from hey_my_buddy.buddy.harnesses.base import Adapter, ProcessHandle
from hey_my_buddy.buddy.runtime import worker as worker_module
from hey_my_buddy.buddy.runtime.worker import TERMINATION_COMPLETED, TERMINATION_HARNESS_ERROR, Worker

#: Inherited variables that would point a test subprocess at a production
#: runtime, a Worker identity or an agent credential instead of this private root.
SANITIZED_VARIABLES = (
    "HMB_STATE_DIR", "HMB_RUNTIME_ROOT", "HMB_RUNTIME", "HMB_RUNTIME_IDENTITY",
    "HMB_WORKER_STATE", "HMB_WORKER_ID", "HMB_AGENT_CREDENTIAL", "HMB_AGENT_CREDENTIAL_FILE",
    "VIRTUAL_ENV", "UV_PROJECT_ENVIRONMENT",
)


def silent(_message: str) -> None:
    """A private worker log sink; the tests assert state, not log prose."""


class MarkerWriteFailureTests(unittest.TestCase):
    """A marker write failure cannot leave an unrecorded child running."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="hey-my-buddy-worker-marker-",
                                                dir=os.environ.get("HMB_CHECKS_TMPDIR", "/tmp"))
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.state = self.root / "state"
        self.work = self.root / "work"
        self.work.mkdir()
        self.worker = Worker("w-marker", self.state, client=BoardClient(self.state, autostart=False), log=silent)

    def claim(self, attempt_id: str, task_id: str) -> dict:
        return {
            "attempt": {"attemptId": attempt_id, "taskId": task_id, "generation": 1},
            "task": {"taskId": task_id,
                     "spec": {"adapter": "command", "cwd": str(self.work), "task": "marker invariant",
                              "expectedSeconds": 30,
                              "argv": [sys.executable, "-c", "print('marker-ok')"]}},
        }

    def test_marker_failure_after_spawn_never_drops_the_live_handle(self):
        attempt_id, task_id = "attempt-marker", "task-marker"
        directory = self.state / "attempts" / task_id / attempt_id
        original = worker_module.fsync_json

        def failing(path, value):
            if Path(path).name == "spawn.marker":
                raise OSError("injected marker write failure")
            return original(path, value)

        with mock.patch("hey_my_buddy.buddy.runtime.worker.fsync_json", side_effect=failing):
            receipt = self.worker.execute(self.claim(attempt_id, task_id))
        report = receipt["report"]
        # The durable intent exists, but failure to record a complete identity
        # must stop the child before any completion claim is made.
        self.assertTrue((directory / "spawn.intent").is_file())
        self.assertFalse((directory / "spawn.marker").exists())
        self.assertEqual(report["status"], "failed")
        self.assertEqual(report["terminationReason"], TERMINATION_HARNESS_ERROR)
        self.assertIs(report["shutdownConfirmed"], True)

    def test_a_clean_attempt_still_writes_both_marker_files(self):
        receipt = self.worker.execute(self.claim("attempt-clean", "task-clean"))
        directory = self.state / "attempts" / "task-clean" / "attempt-clean"
        report = receipt["report"]
        self.assertTrue((directory / "spawn.intent").is_file())
        self.assertTrue((directory / "spawn.marker").is_file())
        identity = json.loads((directory / "spawn.marker").read_text())["processIdentity"]
        self.assertEqual(identity["processGroupId"], identity["pid"])
        self.assertEqual(identity["worker"]["pid"], os.getpid())
        self.assertEqual(report["status"], "ok")
        self.assertEqual(report["terminationReason"], TERMINATION_COMPLETED)

    def test_fast_shell_command_records_identity_and_completes(self):
        claim = self.claim("attempt-fast", "task-fast")
        claim["task"]["spec"]["argv"] = ["/bin/sh", "-c", "echo fast"]
        receipt = self.worker.execute(claim)
        marker = self.state / "attempts" / "task-fast" / "attempt-fast" / "spawn.marker"
        self.assertTrue(marker.is_file(), receipt["report"])
        self.assertEqual(receipt["report"]["status"], "ok", receipt["report"])

    def test_preparation_refusals_keep_their_exact_receipts(self):
        unavailable = self.claim("attempt-unavailable", "task-unavailable")
        unavailable["task"]["spec"]["argv"] = ["/hey-my-buddy-tests/no-such-executable"]
        receipt = self.worker.execute(unavailable)
        report = receipt["report"]
        self.assertEqual(report["status"], "failed")
        self.assertIsNone(report["result"])
        self.assertIn("not an executable this worker can find", report["error"])
        self.assertEqual(report["terminationReason"], TERMINATION_HARNESS_ERROR)
        directory = self.state / "attempts" / "task-unavailable" / "attempt-unavailable"
        self.assertTrue((directory / "spawn.intent").is_file())
        self.assertFalse((directory / "spawn.marker").exists())

    def test_replacement_worker_uses_primitive_evidence_and_does_not_report_unknown(self):
        from hey_my_buddy.protocol import process_identity

        identity = {"bootId": "boot", "pid": 42, "startedAt": 10,
                    "processGroupId": 42, "jobName": None,
                    "worker": {"bootId": "boot", "pid": 41, "startedAt": 9}}

        class Client:
            def __init__(self):
                self.calls = []

            def call(self, operation, params):
                self.calls.append((operation, params))
                if "attemptId" not in params:
                    return {"candidates": [{"attemptId": "attempt-orphan", "processIdentity": identity}]}
                return {"settled": True}

        client = Client()
        worker = Worker("w-orphan", self.state, client=client, log=silent)
        with mock.patch.object(process_identity, "stop_orphan_process", return_value="unknown") as stop:
            worker.recover_orphans()
            stop.assert_called_once_with(identity)
        self.assertEqual([params for _, params in client.calls if "evidence" in params], [])
        with mock.patch.object(process_identity, "stop_orphan_process", return_value="worker-stopped") as stop:
            worker.recover_orphans()
            stop.assert_called_once_with(identity)
        self.assertEqual(client.calls[-1], ("worker_recover", {
            "workerId": "w-orphan", "attemptId": "attempt-orphan", "evidence": "worker-stopped"}))

    def test_replacement_worker_publishes_durable_identity_from_orphan_marker(self):
        from hey_my_buddy.protocol import process_identity

        identity = {"bootId": "boot", "pid": 42, "startedAt": 10,
                    "processGroupId": 42, "jobName": None,
                    "worker": {"bootId": "boot", "pid": 41, "startedAt": 9}}
        intent = {"workerId": "w-orphan", "instanceId": "old-instance", "attemptId": "attempt-orphan",
                  "taskId": "task-orphan", "generation": 1, "nonce": "n" * 32,
                  "claimRequestId": "claim-orphan"}

        class Client:
            def __init__(self):
                self.calls = []

            def call(self, operation, params):
                self.calls.append((operation, params))
                if operation == "worker_recover":
                    return {"candidates": []}
                return {}

        client = Client()
        worker = Worker("w-orphan", self.state, client=client, log=silent)
        worker.spool.ensure()
        worker.spool.write_startup(intent)
        marker = worker.spawn_marker(intent)
        fsync = worker_module.fsync_json
        fsync(marker, {"processIdentity": identity})
        worker.reattach()
        self.assertIsNone(worker.spool.read_startup())
        with mock.patch.object(process_identity, "worker_identity_alive", return_value=False):
            worker.recover_orphans()
            worker.recover_orphans()
        self.assertEqual(client.calls[0], ("worker_progress", {
            "workerId": "w-orphan", "attemptId": "attempt-orphan", "generation": 1,
            "nonce": "n" * 32, "processIdentity": identity, "previousProcessStopped": False}))
        self.assertEqual([operation for operation, _ in client.calls].count("worker_progress"), 1,
                         "orphan identity replay must not keep extending the old lease")

    def test_retirement_waits_for_an_unresolved_orphan(self):
        worker = Worker("w-orphan", self.state, client=mock.Mock(), log=silent)
        worker.spool.ensure()
        worker_module.fsync_json(worker.spool.directory / "orphaned.json", {"attemptId": "attempt-orphan"})
        with mock.patch.object(worker, "recover_orphans") as recover:
            self.assertEqual(worker.settle_retirement(), ["orphaned-attempt:attempt-orphan"])
        recover.assert_called_once_with()

    def test_native_journal_must_prove_every_child_stopped_before_settlement(self):
        from hey_my_buddy.protocol import process_identity

        outer = {"bootId": "boot", "pid": 42, "startedAt": 10,
                 "processGroupId": 42, "jobName": None,
                 "worker": {"bootId": "boot", "pid": 41, "startedAt": 9}}
        native = {"bootId": "boot", "pid": 52, "startedAt": 11,
                  "processGroupId": 52, "jobName": None,
                  "worker": {"bootId": "boot", "pid": 42, "startedAt": 10}}
        binding = {"taskId": "task-orphan", "attemptId": "attempt-orphan", "generation": 1}

        class Client:
            def __init__(self):
                self.calls = []

            def call(self, operation, params):
                self.calls.append((operation, params))
                if "attemptId" not in params:
                    return {"candidates": [{"attemptId": "attempt-orphan", "processIdentity": outer,
                                            "nativeJournal": {"path": "/private-fixture/journal", "binding": binding}}]}
                return {"settled": True}

        client = Client()
        worker = Worker("w-orphan", self.state, client=client, log=silent)
        with mock.patch.object(process_identity, "stop_orphan_process", return_value="identity-absent"), \
                mock.patch.object(process_identity, "read_native_process_journal", return_value=None):
            worker.recover_orphans()
        self.assertEqual([params for _, params in client.calls if "evidence" in params], [])
        with mock.patch.object(process_identity, "stop_orphan_process", side_effect=["identity-absent", "worker-stopped"]), \
                mock.patch.object(process_identity, "read_native_process_journal",
                                  return_value={"identities": [native], "pending": 0}):
            worker.recover_orphans()
        self.assertEqual(client.calls[-1], ("worker_recover", {
            "workerId": "w-orphan", "attemptId": "attempt-orphan", "evidence": "worker-stopped"}))

    def test_service_settled_orphan_does_not_block_worker_retirement(self):
        worker = Worker('w-settled-orphan', self.state, client=SimpleNamespace(call=lambda *args: {
            'candidates': [], 'observedAttempt': {'attemptId': 'settled-attempt',
                                                'executionState': 'finished', 'shutdownConfirmed': True}}))
        worker.spool.ensure()
        orphan_path = worker.spool.directory / 'orphaned.json'
        worker_module.fsync_json(orphan_path, {'workerId': worker.worker_id, 'attemptId': 'settled-attempt',
                                               'identityPublished': True})
        worker.recover_orphans()
        self.assertFalse(orphan_path.exists())
        self.assertEqual(worker.recovery_pending(), [])

    def test_host_grace_deadline_forces_stop_and_cannot_be_reported_as_a_summary(self):
        from hey_my_buddy.buddy.harnesses.base import AdapterOutcome
        from hey_my_buddy.buddy.runtime.worker import TERMINATION_GRACE_EXPIRED

        handle = ProcessHandle(mock.Mock(pid=None), own_group=False, log_paths={})
        handle.identity = lambda: {"bootId": "test-boot", "pid": 1, "startedAt": 1,
                                   "processGroupId": 1, "jobName": None,
                                   "worker": {"bootId": "test-boot", "pid": 2, "startedAt": 2}}
        handle.cancel_requested, handle.stop_initiator = True, "host"
        handle.wrap_up_started, handle.grace_deadline = True, 0.0
        handle.wait = mock.Mock(side_effect=[None, 0])
        executor = SimpleNamespace(
            name="command", available=lambda: (True, None), prepare=lambda context: None,
            start=lambda context: handle,
            collect=lambda owned, context: AdapterOutcome("cancelled", {},
                                                          shutdown_confirmed=True),
        )
        with mock.patch.object(worker_module.role_seam, "worker_executor", return_value=executor), \
                mock.patch.object(worker_module.role_seam, "worker_cancel") as forced:
            report = self.worker.execute(self.claim("attempt-grace", "task-grace"))["report"]
        forced.assert_called_once_with(executor, handle)
        self.assertTrue(handle.grace_expired)
        self.assertEqual(report["terminationReason"], TERMINATION_GRACE_EXPIRED)
        self.assertNotIn("stopInitiator", report)

    def test_timely_wrap_up_keeps_its_reason_and_partial_reason_when_collected_late(self):
        from hey_my_buddy.buddy.harnesses.base import AdapterOutcome
        handle = ProcessHandle(mock.Mock(pid=None), own_group=False, log_paths={})
        handle.identity = lambda: {"bootId": "test-boot", "pid": 1, "startedAt": 1,
                                   "processGroupId": 1, "jobName": None,
                                   "worker": {"bootId": "test-boot", "pid": 2, "startedAt": 2}}
        handle.cancel_requested, handle.stop_initiator = True, "console"
        handle.grace_deadline, handle.grace_expired = 0.0, True
        handle.wait = lambda timeout: 0
        executor = SimpleNamespace(name="command", available=lambda: (True, None),
            prepare=lambda context: None, start=lambda context: handle,
            collect=lambda owned, context: AdapterOutcome("cancelled", {
                "wrapUpDelivered": True, "graceExpired": False,
                "partialOutput": {"partial": True, "verified": False, "final": False},
            }, shutdown_confirmed=True))
        with mock.patch.object(worker_module.role_seam, "worker_executor", return_value=executor):
            report = self.worker.execute(self.claim("attempt-timely", "task-timely"))["report"]
        self.assertEqual(report["terminationReason"], "wrap-up")
        self.assertEqual(report["result"]["partialOutput"]["reason"], report["terminationReason"])


class DurableStopObservationTests(unittest.TestCase):
    def test_immediate_stop_keeps_each_executors_reaping_grace(self):
        from hey_my_buddy.buddy.harnesses.registry import adapter, run_seam
        from hey_my_buddy.buddy.roles.run_execution import WorkerRunExecutor
        from hey_my_buddy.buddy.roles.router import DecisionAdapter
        from hey_my_buddy.buddy.runtime.command import CommandAdapter
        executors = ((CommandAdapter(), 3.0),
                     (WorkerRunExecutor(adapter("codex"), run_seam("codex")), 8.0),
                     (DecisionAdapter(), 12.0))
        for executor, grace in executors:
            with self.subTest(executor=executor.name):
                handle = ProcessHandle(mock.Mock(pid=None), own_group=False, log_paths={})
                handle.terminate = mock.Mock()
                worker_module.role_seam.worker_stop(executor, handle, stop_initiator="console", grace_deadline=None)
                handle.terminate.assert_called_once_with(grace_seconds=grace)

    def renewal(self, view):
        client = SimpleNamespace(call=lambda *args: {"task": view}, renew=lambda *args: view)
        worker = SimpleNamespace(client=client, log=lambda message: None, _nonce=lambda: "nonce", worker_id="worker")
        handle = ProcessHandle(mock.Mock(pid=None), own_group=False, log_paths={})
        executor = Adapter()
        executor.cancel = mock.Mock(side_effect=lambda owned: setattr(owned, "cancel_requested", True))
        renewal = worker_module._Renewal(worker, {"attempt": {"taskId": "task", "attemptId": "attempt",
                                                             "generation": 1}}, handle, executor)
        return renewal, handle, executor

    def test_host_summary_keeps_lease_and_original_deadline(self):
        view = {"cancelRequested": True, "stopInitiator": "host", "graceDeadline":
                (datetime.now(timezone.utc) + timedelta(seconds=300)).isoformat()}
        renewal, handle, executor = self.renewal(view)
        def stopped(*args, stop_initiator, grace_deadline):
            handle.cancel_requested = True
            handle.stop_initiator = stop_initiator
            handle.grace_deadline = grace_deadline
        with mock.patch.object(worker_module.role_seam, "worker_stop", side_effect=stopped) as stop, \
                mock.patch.object(renewal, "_refresh_live_binding"):
            self.assertFalse(renewal._cancel_observed())
            deadline = handle.grace_deadline
            view["graceDeadline"] = (datetime.now(timezone.utc) + timedelta(seconds=600)).isoformat()
            self.assertTrue(renewal._renew())
            self.assertFalse(renewal._cancel_observed())
        self.assertEqual(handle.grace_deadline, deadline)
        stop.assert_called_once()

    def test_lead_stop_passes_its_durable_deadline_to_the_role(self):
        view = {"cancelRequested": True, "stopInitiator": "lead", "graceDeadline":
                (datetime.now(timezone.utc) + timedelta(seconds=300)).isoformat()}
        renewal, handle, _executor = self.renewal(view)
        with mock.patch.object(worker_module.role_seam, "worker_stop") as stop:
            self.assertFalse(renewal._cancel_observed())
        self.assertEqual(stop.call_args.kwargs["stop_initiator"], "lead")
        self.assertGreater(stop.call_args.kwargs["grace_deadline"], 0)

    def test_console_escalation_stops_immediately_using_the_executor_default(self):
        view = {"cancelRequested": True, "stopInitiator": "console", "graceDeadline": "old deadline"}
        renewal, handle, executor = self.renewal(view)
        handle.cancel_requested, handle.stop_initiator = True, "host"
        handle.wrap_up_started = True
        handle.grace_deadline, handle.grace_expired = 123.0, True
        self.assertFalse(renewal._cancel_observed())
        executor.cancel.assert_called_once_with(handle)
        self.assertEqual(handle.stop_initiator, "console")
        self.assertEqual(handle.grace_deadline, 123.0)
        self.assertTrue(handle.force_stop_requested)

    def test_host_without_confirmed_execution_has_no_summary_deadline(self):
        renewal, handle, executor = self.renewal({"cancelRequested": True, "stopInitiator": "host",
                                                "graceDeadline": None})
        self.assertFalse(renewal._cancel_observed())
        executor.cancel.assert_called_once_with(handle)
        self.assertIsNone(handle.grace_deadline)

    def test_runtime_stop_before_host_stop_keeps_renewal_alive_without_wrap_up(self):
        view = {"cancelRequested": True, "stopInitiator": "host", "graceDeadline":
                (datetime.now(timezone.utc) + timedelta(seconds=300)).isoformat()}
        renewal, handle, executor = self.renewal(view)
        worker_module.role_seam.worker_cancel(executor, handle)
        with mock.patch.object(worker_module.role_seam, "start_wrap_up") as wrap_up, \
                mock.patch.object(renewal, "_refresh_live_binding"):
            self.assertFalse(renewal._cancel_observed())
            self.assertTrue(renewal._renew())
        executor.cancel.assert_called_once_with(handle)
        wrap_up.assert_not_called()
        self.assertEqual(handle.stop_initiator, "unknown")
        self.assertTrue(handle.force_stop_requested)


class PreparationBoardErrorTests(unittest.TestCase):
    """The captured prepare() BoardError keeps its object, code and retry boundary."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="hey-my-buddy-worker-prep-",
                                                dir=os.environ.get("HMB_CHECKS_TMPDIR", "/tmp"))
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.state = self.root / "state"

    def worker(self, client=None):
        return Worker("w-prep", self.state, client=client, log=silent)

    @staticmethod
    def claim(task_id: str = "task-prep") -> dict:
        return {
            "attempt": {"attemptId": "attempt-prep", "taskId": task_id, "generation": 1},
            "task": {"taskId": task_id,
                     "spec": {"adapter": "dsh", "cwd": "/hey-my-buddy-tests/nowhere", "task": "prep",
                              "expectedSeconds": 30}},
        }

    def test_native_journals_are_distinct_for_two_controller_roots(self):
        from hey_my_buddy.protocol.process_identity import (
            NativeProcessJournal, _creation_time, native_process_journal_path,
        )

        binding = {"taskId": "task-prep", "attemptId": "attempt-prep", "generation": 1}
        self.assertEqual(native_process_journal_path(self.root), native_process_journal_path(
            self.root, {"pid": os.getpid(), "startedAt": _creation_time(os.getpid())}))
        first = native_process_journal_path(self.root, {"pid": 101, "startedAt": 1})
        second = native_process_journal_path(self.root, {"pid": 102, "startedAt": 2})
        self.assertNotEqual(first, second)
        NativeProcessJournal(first, binding)
        NativeProcessJournal(second, binding)
        self.assertTrue(first.is_file())
        self.assertTrue(second.is_file())

    class FailingExecutor:
        name = "fake"

        def __init__(self, error):
            self.error = error
            self.prepared = 0

        def available(self):
            return True, None

        def prepare(self, context):
            self.prepared += 1
            raise self.error

    def test_a_harness_history_reraises_the_original_board_error_object(self):
        error = BoardError("HARNESS_UNAVAILABLE", "the native binary is missing")
        executor = self.FailingExecutor(error)
        with mock.patch.object(worker_module.role_seam, "worker_executor", return_value=executor):
            worker = self.worker()
            claim = self.claim()
            directory = self.state / "attempts" / claim["attempt"]["taskId"] / claim["attempt"]["attemptId"]
            directory.mkdir(mode=0o700, parents=True)
            holder = {"harnessHistory": [{"retry": False, "harness": {"harness": "dsh", "revision": 3}}]}
            with self.assertRaises(BoardError) as caught:
                worker._execute_selected(claim, claim["task"], claim["task"]["spec"],
                                         claim["attempt"], directory, holder)
        # The very exception object the adapter raised reaches the guarded retry:
        # same identity and code, never a bare-raise RuntimeError.
        self.assertIs(caught.exception, error)
        self.assertEqual(caught.exception.code, "HARNESS_UNAVAILABLE")
        self.assertEqual(executor.prepared, 1)

    def test_without_a_harness_history_the_refusal_becomes_its_exact_receipt(self):
        error = BoardError("HARNESS_UNAVAILABLE", "the native binary is missing")
        executor = self.FailingExecutor(error)
        with mock.patch.object(worker_module.role_seam, "worker_executor", return_value=executor):
            worker = self.worker()
            claim = self.claim()
            directory = self.state / "attempts" / claim["attempt"]["taskId"] / claim["attempt"]["attemptId"]
            directory.mkdir(mode=0o700, parents=True)
            receipt = worker._execute_selected(claim, claim["task"], claim["task"]["spec"],
                                               claim["attempt"], directory, {})
        report = receipt["report"]
        self.assertEqual(report["status"], "failed")
        self.assertEqual(report["error"], "HARNESS_UNAVAILABLE: the native binary is missing")
        self.assertEqual(report["terminationReason"], TERMINATION_HARNESS_ERROR)

    def test_the_guarded_retry_reruns_a_flaky_prepare_and_recovers(self):
        from hey_my_buddy.buddy.harnesses.base import AdapterOutcome

        record = {"harness": "dsh", "available": True, "revision": 7, "status": "ready"}

        class StubClient:
            def __init__(self):
                self.calls = []

            def call(self, method, params):
                self.calls.append((method, params))
                if method == "harness_prepare":
                    return {"harness": record}
                return {"task": {}}

            def renew(self, *args, **kwargs):
                return {}

        class Handle(ProcessHandle):
            def __init__(self):
                super().__init__(mock.Mock(pid=None), own_group=False, log_paths={})

            def identity(self):
                return {"bootId": "test-boot", "pid": 1, "startedAt": 1,
                        "processGroupId": 1, "jobName": None,
                        "worker": {"bootId": "test-boot", "pid": 2, "startedAt": 2}}

            def wait(self, timeout=None):
                return 0

            def shutdown_confirmed(self):
                return True

        class FlakyExecutor:
            name = "fake"

            def __init__(self):
                self.prepares = 0
                self.handle = Handle()

            def available(self):
                return True, None

            def prepare(self, context):
                self.prepares += 1
                if self.prepares == 1:
                    raise BoardError("HARNESS_UNAVAILABLE", "first attempt fails")
                return None

            def start(self, context):
                return self.handle

            def collect(self, handle, context):
                return AdapterOutcome(status="ok", result={"status": "ok", "modelStarted": True},
                                      shutdown_confirmed=True)

            def cancel(self, handle, *, grace_seconds=None):
                self.handle.cancel_requested = True

        executor = FlakyExecutor()
        client = StubClient()
        with mock.patch.object(worker_module.role_seam, "worker_executor", return_value=executor):
            receipt = self.worker(client).execute(self.claim("task-retry"))
        report = receipt["report"]
        # The re-raised BoardError from the first prepare is exactly what sends
        # _execute_guarded around its second harness_prepare attempt, where the
        # same executor's prepare succeeds and the attempt completes.
        self.assertEqual(executor.prepares, 2)
        prepare_calls = [params for method, params in client.calls if method == "harness_prepare"]
        self.assertEqual(len(prepare_calls), 2)
        self.assertTrue(prepare_calls[1]["commandId"].endswith("-1"))
        self.assertTrue(prepare_calls[1]["retry"] is True)
        self.assertEqual(report["status"], "ok")
        self.assertEqual(report["terminationReason"], TERMINATION_COMPLETED)
        self.assertEqual(len(report["result"]["harnessAttempts"]), 2)

    def test_confirmed_premodel_retry_publishes_distinct_roots_in_one_attempt(self):
        from hey_my_buddy.buddy.harnesses.base import AdapterOutcome

        record = {"harness": "dsh", "available": True, "revision": 7, "status": "ready"}

        class Client:
            def __init__(self):
                self.calls = []
                self.first_progress_unavailable = True

            def call(self, method, params):
                self.calls.append((method, params))
                if method == "harness_prepare":
                    return {"harness": record}
                if method == "worker_progress" and self.first_progress_unavailable:
                    self.first_progress_unavailable = False
                    raise BoardError("SERVICE_UNAVAILABLE", "injected first root publication gap")
                return {"task": {}}

            def renew(self, *args, **kwargs):
                return {}

        class Handle(ProcessHandle):
            def __init__(self, pid):
                super().__init__(mock.Mock(pid=None), own_group=False, log_paths={})
                self.root_pid = pid

            def identity(self):
                return {"bootId": "test-boot", "pid": self.root_pid, "startedAt": self.root_pid,
                        "processGroupId": self.root_pid, "jobName": None,
                        "worker": {"bootId": "test-boot", "pid": 2, "startedAt": 2}}

            def wait(self, timeout=None):
                return 0

            def shutdown_confirmed(self):
                return True

        class Executor:
            name = "fake"

            def __init__(self):
                self.starts = 0

            def available(self):
                return True, None

            def prepare(self, context):
                return None

            def start(self, context):
                self.starts += 1
                return Handle(10 + self.starts)

            def collect(self, handle, context):
                if handle.root_pid == 11:
                    return AdapterOutcome("failed", {"modelStarted": False, "code": "invalid-native-result"},
                                          shutdown_confirmed=True)
                return AdapterOutcome("ok", {"modelStarted": True}, shutdown_confirmed=True)

        client = Client()
        executor = Executor()
        worker = self.worker(client)
        with mock.patch.object(worker_module.role_seam, "worker_executor", return_value=executor):
            receipt = worker.execute(self.claim("task-two-roots"))
        roots = [params for method, params in client.calls
                 if method == "worker_progress" and "processIdentity" in params]
        self.assertEqual([entry["processIdentity"]["pid"] for entry in roots], [11, 12])
        self.assertEqual([entry["previousProcessStopped"] for entry in roots], [False, True])
        marker = (self.state / "attempts" / "task-two-roots" / "attempt-prep" / "spawn.marker")
        self.assertEqual(json.loads(marker.read_text())["previousProcessStopped"], True)
        retired = marker.with_name("harness-prestart-failure.json")
        self.assertEqual(json.loads(retired.read_text())["processIdentity"]["pid"], 11)
        orphan_path = worker.spool.directory / "orphaned.json"
        worker_module.fsync_json(orphan_path, {"workerId": worker.worker_id,
            "attemptId": "attempt-prep", "taskId": "task-two-roots", "generation": 1,
            "nonce": "n" * 32, "identityPublished": False})
        from hey_my_buddy.protocol import process_identity
        with mock.patch.object(process_identity, "worker_identity_alive", return_value=False):
            worker._publish_orphan_identity()
        replay = [params for method, params in client.calls
                  if method == "worker_progress" and "processIdentity" in params][2:]
        self.assertEqual([entry["processIdentity"]["pid"] for entry in replay], [11, 12])
        self.assertEqual([entry["previousProcessStopped"] for entry in replay], [False, True])
        self.assertTrue(json.loads(orphan_path.read_text())["identityPublished"])
        self.assertEqual(executor.starts, 2)
        self.assertEqual(receipt["report"]["status"], "ok")


class LiveActivityForwardTests(unittest.TestCase):
    """Activity forwarding through the registered live seam (ADR-025 step 2-C2).

    A registered harness's attempt activity reaches the board through its live
    channel, bound to the stored public run request's identity; the same
    published activity is never forwarded twice, a foreign endpoint is refused,
    and channel loss says nothing about the native process. An unextracted
    command has no live activity channel or file fallback.
    """

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="hey-my-buddy-worker-live-",
                                                dir=os.environ.get("HMB_CHECKS_TMPDIR", "/tmp"))
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.state = self.root / "state"
        self.state.mkdir(mode=0o700)
        self.work = self.root / "work"
        self.work.mkdir()

    class RecordingClient:
        def __init__(self):
            self.progress_calls = []

        def progress(self, worker_id, attempt_id, generation, nonce, message=None, phase=None, data=None):
            self.progress_calls.append({"attemptId": attempt_id, "generation": generation, "data": data})
            return {}

        def call(self, method, params):
            return {"task": {}}

        def renew(self, *args, **kwargs):
            return {}

    def renewal(self, *, with_request=True, harness="zcode", task_id="task-live",
                attempt_id="attempt-live", generation=1):
        import types

        from hey_my_buddy.buddy.harnesses.run_contract import (
            FrozenJson,
            PrivateStatePaths,
            RunBudget,
            RunBuddy,
            RunIdentity,
            RunRequest,
            encode_run_request,
        )
        worker = Worker("w-live", self.state, client=self.RecordingClient(), log=silent)
        self.addCleanup(worker.live.stop)
        claim = {"attempt": {"attemptId": attempt_id, "taskId": task_id, "generation": generation},
                 "task": {"taskId": task_id,
                          "spec": {"adapter": harness, "cwd": str(self.work), "task": "live activity",
                                   "expectedSeconds": 30}}}
        directory = self.state / "attempts" / task_id / attempt_id
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        identity = RunIdentity(task_id=task_id, attempt_id=attempt_id, generation=generation,
                               invocation_id="invocation-live", turn_id="turn-live")
        request_file = directory / "role-run-request.json"
        if with_request:
            request = RunRequest(
                identity=identity, harness=harness,
                buddy=RunBuddy(provider="fixture-zcode", model="fixture-glm", effort="low"),
                cwd=str(self.work),
                private_state=PrivateStatePaths(invocation_root=str(directory),
                                                native_root=str(directory / "native")),
                input_text="the micro task turn input", tool_scope="write",
                output_schema=FrozenJson({"type": "object"}),
                budget=RunBudget(timeout_seconds=600))
            worker_module.fsync_json(request_file, json.loads(encode_run_request(request)))
        from hey_my_buddy.buddy.harnesses.c_two_live import CTwoLiveChannel, CTwoLiveEndpoint, LiveEndpointDescriptor
        from hey_my_buddy.buddy.harnesses.live import LiveCapabilities
        from hey_my_buddy.protocol.contracts import HarnessRunLive
        endpoint = CTwoLiveEndpoint(identity, LiveCapabilities(inquiry_delivery="unsupported"), HarnessRunLive,
                                    instance_id="c" * 64, token="d" * 64, state_dir=Path(os.environ["HMB_STATE_DIR"]))
        self.addCleanup(endpoint.close, reason="fixture-ended")
        self.endpoints = getattr(self, "endpoints", {})
        self.endpoints[str(directory)] = endpoint
        ready_file = directory / "live-ready.json"
        ready_file.write_text(json.dumps(LiveEndpointDescriptor(
            address="fixture-address", name="Fixture Activity", instance_id="c" * 64, host_pid=42, endpoint_credential="opaque-native-credential").to_payload()))
        control = {"operation": "worker", "harness": harness, "requestFile": str(request_file),
                   "directory": str(directory),
                   "live": {"readyFile": str(ready_file), "instanceId": "c" * 64, "token": "d" * 64}}
        handle = types.SimpleNamespace(role_run_control=control, role_run_identity=identity,
                                       pid=42, cancel_requested=False)
        import c_two as cc
        self.enterContext(mock.patch.object(cc.EndpointCredential, "from_json", return_value=types.SimpleNamespace(
            address="fixture-address", context=cc.local_endpoint_context())))
        # Readiness/held-request validation, wire admission and forwarding all
        # run. Both SDK boundaries are local; no native PID is proven here.
        def connect(*args, timeout, **kwargs):
            self.assertGreaterEqual(timeout, 0)
            return nullcontext(self.endpoints[str(directory)])
        def with_call_options(peer, *, timeout):
            self.assertIs(peer, self.endpoints[str(directory)])
            self.assertGreaterEqual(timeout, 0)
            return peer
        self.enterContext(mock.patch.object(cc, "connect", side_effect=connect))
        self.enterContext(mock.patch.object(cc, "with_call_options", side_effect=with_call_options))
        return worker_module._Renewal(worker, claim, handle, implementation=object()), directory

    def publish_activity(self, directory: Path, *, task_id="task-live", attempt_id="attempt-live",
                         generation=1, event_seq=1):
        from hey_my_buddy.buddy.harnesses.c_two_live import CTwoLiveEndpoint
        from hey_my_buddy.buddy.harnesses.live import LiveCapabilities
        from hey_my_buddy.protocol.contracts import HarnessRunLive
        from hey_my_buddy.protocol.run_identity import RunIdentity
        endpoint = self.endpoints[str(directory)]
        source_identity = RunIdentity(**{**endpoint.identity.model_dump(), "task_id": task_id,
                                         "attempt_id": attempt_id, "generation": generation})
        if source_identity != endpoint.identity:
            endpoint = CTwoLiveEndpoint(source_identity, LiveCapabilities(inquiry_delivery="unsupported"), HarnessRunLive,
                                        instance_id="c" * 64, token="d" * 64, state_dir=Path(os.environ["HMB_STATE_DIR"]))
            self.endpoints[str(directory)] = endpoint
            self.addCleanup(endpoint.close, reason="fixture-ended")
        self.assertTrue(endpoint.publish_activity({"phase": "streaming-model", "eventSeq": event_seq,
                                                   "counts": {"modelTurns": 1, "toolCalls": 0}}))

    def test_activity_flows_through_the_channel_and_never_repeats(self):
        renewal, directory = self.renewal()
        renewal._forward_activity()
        self.assertEqual(renewal.worker.client.progress_calls, [], "nothing is forwarded before native activity")
        self.publish_activity(directory, event_seq=3)
        renewal._forward_activity()
        calls = renewal.worker.client.progress_calls
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["attemptId"], "attempt-live")
        self.assertEqual(calls[0]["data"]["activity"]["eventSeq"], 3)
        renewal._forward_activity()
        self.assertEqual(len(renewal.worker.client.progress_calls), 1, "the same publication never repeats")
        self.publish_activity(directory, event_seq=4)
        renewal._forward_activity()
        self.assertEqual(len(renewal.worker.client.progress_calls), 2)

    def test_a_foreign_attempts_endpoint_is_never_forwarded(self):
        renewal, directory = self.renewal()
        self.publish_activity(directory, attempt_id="another-attempt", event_seq=9)
        renewal._forward_activity()
        self.assertEqual(renewal.worker.client.progress_calls, [])

    def test_a_stored_request_of_another_invocation_never_binds_or_bypasses(self):
        renewal, directory = self.renewal()
        self.publish_activity(directory, event_seq=3)
        request_path = Path(renewal.handle.role_run_control["requestFile"])
        original = request_path.read_text()
        # Tamper the stored request after the handle was issued: its invocation
        # no longer matches the held identity, so no channel binds — and the
        # extracted live path has no fallback: nothing is forwarded this tick.
        request = json.loads(original)
        request["identity"]["invocationId"] = "foreign-invocation"
        request_path.write_text(json.dumps(request))
        renewal._forward_activity()
        self.assertEqual(renewal.worker.client.progress_calls, [])
        self.assertIsNone(renewal._activity_channel)
        # The binding is retried, not cached as absent: once the stored request
        # is whole again, the very next tick binds and forwards.
        request_path.write_text(original)
        renewal._forward_activity()
        calls = renewal.worker.client.progress_calls
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["data"]["activity"]["eventSeq"], 3)

    def test_an_unextracted_command_has_no_live_activity_or_file_fallback(self):
        renewal, directory = self.renewal(with_request=False, harness="command")
        renewal.handle.role_run_control = {"operation": "worker", "harness": "command"}
        state, channel = renewal._attempt_activity_binding()
        self.assertEqual(state, "unextracted")
        self.assertIsNone(channel)
        (directory / "activity.json").write_text('{"phase":"streaming-model","eventSeq":3}')
        with mock.patch("os.open", wraps=os.open) as opened:
            renewal._forward_activity()
        opened.assert_not_called()
        self.assertEqual(renewal.worker.client.progress_calls, [])

    def test_a_channel_unavailability_is_not_a_stop(self):
        renewal, directory = self.renewal()
        self.publish_activity(directory, event_seq=3)
        # A channel whose reads fail is an availability fact, never a stop: the
        # forwarding loop swallows it, forwards nothing and keeps running.
        state, channel = renewal._attempt_activity_binding()
        self.assertEqual(state, "bound")
        with mock.patch.object(channel, "observe", side_effect=BoardError("INTERNAL", "unreadable")):
            renewal._forward_activity()
        self.assertEqual(renewal.worker.client.progress_calls, [])
        # The same live fact still forwards once the channel reads again.
        renewal._forward_activity()
        self.assertEqual(len(renewal.worker.client.progress_calls), 1)


if __name__ == "__main__":
    unittest.main()
