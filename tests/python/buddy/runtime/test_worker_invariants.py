"""Execution and Worker invariants across the ADR-025 role seam.

An execution collects native stop proof and writes one durable receipt. The
Worker records the isolated execution's identity before releasing its handle.
The tests use private state and offline command children.
"""
from __future__ import annotations

from contextlib import nullcontext
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from types import SimpleNamespace
from unittest import mock

from support import wait_for

from hey_my_buddy.errors import BoardError
from hey_my_buddy.protocol.client import BoardClient
from hey_my_buddy.buddy.harnesses.base import ProcessHandle
from hey_my_buddy.buddy.runtime import worker as worker_module
from hey_my_buddy.buddy.runtime import execution as execution_module
from hey_my_buddy.buddy.runtime.execution import Execution
from hey_my_buddy.buddy.runtime.worker import TERMINATION_COMPLETED, TERMINATION_HARNESS_ERROR, Worker

#: Inherited variables that would point a test subprocess at a production
#: runtime, a Worker identity or an agent credential instead of this private root.
SANITIZED_VARIABLES = (
    "HMB_STATE_DIR", "HMB_RUNTIME_ROOT", "HMB_RUNTIME", "HMB_RUNTIME_IDENTITY",
    "HMB_WORKER_STATE", "HMB_WORKER_ID", "HMB_AGENT_CREDENTIAL_FILE",
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

    def execute_direct(self, claim: dict) -> dict:
        execution = Execution(claim, self.state, self.worker.worker_id, "fixture-nonce",
                              client=mock.Mock(), log=silent)
        return execution.execute()

    def test_marker_failure_after_spawn_never_drops_the_live_handle(self):
        attempt_id, task_id = "attempt-marker", "task-marker"
        directory = self.state / "attempts" / task_id / attempt_id
        original = worker_module.fsync_json
        handle = mock.Mock()
        handle.identity.return_value = {"bootId": "test-boot", "pid": 11, "startedAt": 12,
                                          "processGroupId": 11, "jobName": None,
                                          "worker": {"bootId": "test-boot", "pid": 10,
                                                     "startedAt": 9}}
        handle.wait.return_value = 0
        handle.process.returncode = 1
        self.worker.client.submit_result = mock.Mock(return_value={"taskState": "failed"})

        def failing(path, value):
            if Path(path).name == "spawn.marker":
                raise OSError("injected marker write failure")
            return original(path, value)

        with mock.patch("hey_my_buddy.buddy.runtime.worker.fsync_json", side_effect=failing), \
             mock.patch("hey_my_buddy.buddy.runtime.execution.launch_execution", return_value=handle), \
             mock.patch("hey_my_buddy.buddy.runtime.execution.stop_execution") as stopped, \
             mock.patch("hey_my_buddy.buddy.roles.live.release_live_binding"):
            receipt = self.worker.execute(self.claim(attempt_id, task_id))
        self.assertEqual(receipt["report"]["status"], "failed")
        self.assertIs(receipt["report"]["shutdownConfirmed"], False)
        self.worker.client.submit_result.assert_called_once()
        # The durable intent exists, but failure to record a complete identity
        # must stop the execution process before any completion claim is made.
        self.assertTrue((directory / "spawn.intent").is_file())
        self.assertFalse((directory / "spawn.marker").exists())
        stopped.assert_called_once_with(handle, force=True)

    def test_direct_execution_writes_a_durable_receipt(self):
        receipt = self.execute_direct(self.claim("attempt-clean", "task-clean"))
        directory = self.state / "attempts" / "task-clean" / "attempt-clean"
        report = receipt["report"]
        self.assertEqual(json.loads((directory / "result-receipt.json").read_text()), receipt)
        self.assertEqual(report["status"], "ok")
        self.assertEqual(report["terminationReason"], TERMINATION_COMPLETED)

    def test_fast_shell_command_completes(self):
        claim = self.claim("attempt-fast", "task-fast")
        claim["task"]["spec"]["argv"] = ["/bin/sh", "-c", "echo fast"]
        receipt = self.execute_direct(claim)
        self.assertEqual(receipt["report"]["status"], "ok", receipt["report"])

    def test_preparation_refusals_keep_their_exact_receipts(self):
        unavailable = self.claim("attempt-unavailable", "task-unavailable")
        unavailable["task"]["spec"]["argv"] = ["/hey-my-buddy-tests/no-such-executable"]
        receipt = self.execute_direct(unavailable)
        report = receipt["report"]
        self.assertEqual(report["status"], "failed")
        self.assertEqual(report['result']['code'], 'ADAPTER_UNAVAILABLE')
        self.assertEqual(report['result']['stopEvidence'], {
            'shutdownConfirmed': True, 'native': {'shutdownConfirmed': True}})
        self.assertIn("not an executable this worker can find", report["error"])
        self.assertEqual(report["terminationReason"], TERMINATION_HARNESS_ERROR)
        directory = self.state / "attempts" / "task-unavailable" / "attempt-unavailable"
        self.assertEqual(json.loads((directory / "result-receipt.json").read_text()), receipt)

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

    def test_empty_wait_windows_never_poll_orphan_recovery(self):
        calls = []
        def wait(**params):
            calls.append(params)
            if len(calls) == 4:
                self.worker.stop.set()
            return {"events": [], "head": 0, "recoveryNeeded": False}
        with mock.patch.object(self.worker.client, "event_waiter", return_value=nullcontext(wait)), \
             mock.patch.object(self.worker, "recover_orphans") as recover:
            self.worker.wait_for_work()
        self.assertEqual(len(calls), 4)
        recover.assert_not_called()

    def test_unknown_event_wakes_one_recovery_per_batch(self):
        for needed in (False, True):
            with self.subTest(recoveryNeeded=needed):
                wait = mock.Mock(side_effect=[
                    {"events": [], "head": 0, "recoveryNeeded": False},
                    {"events": [{"kind": "worker.registered"}], "head": 12,
                     "recoveryNeeded": needed}])
                with mock.patch.object(self.worker.client, "event_waiter", return_value=nullcontext(wait)), \
                     mock.patch.object(self.worker, "recover_orphans", return_value=True) as recover:
                    self.worker.wait_for_work()
                self.assertEqual(recover.call_count, int(needed))
                self.assertEqual(self.worker._idle_cursor, 12)

    def test_failed_recovery_keeps_event_trigger_and_backs_off(self):
        self.worker._idle_cursor = 5
        wait = mock.Mock(return_value={"events": [{"kind": "attempt.uncertain"}],
                                       "head": 12, "recoveryNeeded": True})
        with mock.patch.object(self.worker.client, "event_waiter", side_effect=lambda **_: nullcontext(wait)), \
             mock.patch.object(self.worker, "recover_orphans", side_effect=[False, True]) as recover, \
             mock.patch.object(self.worker.stop, "wait") as backoff:
            self.worker.wait_for_work()
            self.assertEqual(self.worker._idle_cursor, 5)
            backoff.assert_called_once_with(worker_module.RECOVERY_RETRY_SECONDS)
            self.worker.wait_for_work()
            self.assertEqual(self.worker._idle_cursor, 12)
        self.assertEqual(recover.call_count, 2)
        self.assertEqual([call.kwargs['after'] for call in wait.call_args_list], [5, 5])

    def test_replacement_with_an_old_claim_waits_for_unknown_events(self):
        with mock.patch.object(self.worker.client, "claim", side_effect=BoardError(
                "WORKER_BUSY", "the previous Worker still owns an unresolved attempt")):
            self.assertEqual(self.worker.run_once(), "idle")

    def test_empty_claim_does_not_skip_events_since_last_wait(self):
        self.worker._idle_cursor = 8
        with mock.patch.object(self.worker.client, "claim", return_value={"claim": None}):
            self.assertEqual(self.worker.run_once(), "idle")
        self.assertEqual(self.worker._idle_cursor, 8)

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
        with mock.patch.object(execution_module.role_seam, "worker_executor", return_value=executor), \
                mock.patch.object(execution_module.role_seam, "worker_cancel") as forced:
            report = self.execute_direct(self.claim("attempt-grace", "task-grace"))["report"]
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
        with mock.patch.object(execution_module.role_seam, "worker_executor", return_value=executor):
            report = self.execute_direct(self.claim("attempt-timely", "task-timely"))["report"]
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

    def test_host_deadline_is_durable_and_console_escalation_forces_stop(self):
        import threading
        from hey_my_buddy.buddy.runtime.execution import stop_execution

        with tempfile.TemporaryDirectory() as directory:
            handle = SimpleNamespace(
                stop_lock=threading.RLock(), execution_directory=Path(directory),
                execution_binding={"taskId": "task", "attemptId": "attempt", "generation": 1},
                stop_initiator=None, cancel_requested=False, wrap_up_started=False,
                grace_deadline=None, force_stop_requested=False, signal_group=mock.Mock())
            stop_execution(handle, initiator="host", grace_deadline=123.0)
            request = json.loads((Path(directory) / "execution-stop.json").read_text())
            self.assertEqual((request["initiator"], request["graceDeadline"], request["force"]),
                             ("host", 123.0, False))
            stop_execution(handle, initiator="host", grace_deadline=456.0)
            self.assertEqual(handle.grace_deadline, 123.0)
            handle.signal_group.assert_called_once()
            stop_execution(handle, initiator="console")
            request = json.loads((Path(directory) / "execution-stop.json").read_text())
            self.assertEqual((request["initiator"], request["graceDeadline"], request["force"]),
                             ("console", None, True))
            self.assertEqual(handle.signal_group.call_count, 2)

    def test_lead_stop_keeps_its_deadline(self):
        import threading
        from hey_my_buddy.buddy.runtime.execution import stop_execution

        with tempfile.TemporaryDirectory() as directory:
            handle = SimpleNamespace(
                stop_lock=threading.RLock(), execution_directory=Path(directory),
                execution_binding={"taskId": "task", "attemptId": "attempt", "generation": 1},
                stop_initiator=None, cancel_requested=False, wrap_up_started=False,
                grace_deadline=None, force_stop_requested=False, signal_group=mock.Mock())
            stop_execution(handle, initiator="lead", grace_deadline=789.0)
            request = json.loads((Path(directory) / "execution-stop.json").read_text())
            self.assertEqual((request["initiator"], request["graceDeadline"], request["force"]),
                             ("lead", 789.0, False))


class PreparationBoardErrorTests(unittest.TestCase):
    """The captured prepare() BoardError keeps its object, code and retry boundary."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="hey-my-buddy-worker-prep-",
                                                dir=os.environ.get("HMB_CHECKS_TMPDIR", "/tmp"))
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.state = self.root / "state"

    def execution(self, claim, client=None):
        return Execution(claim, self.state, "w-prep", "fixture-nonce",
                         client=client or mock.Mock(), log=silent)

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
        with mock.patch.object(execution_module.role_seam, "worker_executor", return_value=executor):
            claim = self.claim()
            execution = self.execution(claim)
            directory = self.state / "attempts" / claim["attempt"]["taskId"] / claim["attempt"]["attemptId"]
            directory.mkdir(mode=0o700, parents=True)
            holder = {"harnessHistory": [{"retry": False, "harness": {"harness": "dsh", "revision": 3}}],
                      "preflightRenewal": execution.control}
            with self.assertRaises(BoardError) as caught:
                execution._execute_selected(claim, claim["task"], claim["task"]["spec"],
                                         claim["attempt"], directory, holder)
        # The very exception object the adapter raised reaches the guarded retry:
        # same identity and code, never a bare-raise RuntimeError.
        self.assertIs(caught.exception, error)
        self.assertEqual(caught.exception.code, "HARNESS_UNAVAILABLE")
        self.assertEqual(executor.prepared, 1)

    def test_without_a_harness_history_the_refusal_becomes_its_exact_receipt(self):
        error = BoardError("HARNESS_UNAVAILABLE", "the native binary is missing")
        executor = self.FailingExecutor(error)
        with mock.patch.object(execution_module.role_seam, "worker_executor", return_value=executor):
            claim = self.claim()
            execution = self.execution(claim)
            directory = self.state / "attempts" / claim["attempt"]["taskId"] / claim["attempt"]["attemptId"]
            directory.mkdir(mode=0o700, parents=True)
            receipt = execution._execute_selected(claim, claim["task"], claim["task"]["spec"],
                                               claim["attempt"], directory,
                                               {'preflightRenewal': execution.control})
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
        claim = self.claim("task-retry")
        with mock.patch.object(execution_module.role_seam, "worker_executor", return_value=executor):
            receipt = self.execution(claim, client).execute()
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

    def control(self, *, with_request=True, harness="zcode", task_id="task-live",
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
        claim = {"attempt": {"attemptId": attempt_id, "taskId": task_id, "generation": generation},
                 "task": {"taskId": task_id,
                          "spec": {"adapter": harness, "cwd": str(self.work), "task": "live activity",
                                   "expectedSeconds": 30}}}
        execution = Execution(claim, self.state, "w-live", "fixture-nonce",
                              client=self.RecordingClient(), log=silent)
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
        descriptor = LiveEndpointDescriptor(
            address="fixture-address", name="Fixture Activity", instance_id="c" * 64,
            host_pid=42, endpoint_credential="opaque-native-credential")
        live_file = directory / "execution-live.json"
        live_file.write_text(json.dumps({"identity": identity.model_dump(by_alias=True),
                                        "harness": harness, "descriptor": descriptor.to_payload(),
                                        "token": "d" * 64}))
        control = {"operation": "worker", "harness": harness, "requestFile": str(request_file),
                   "directory": str(directory),
                   "live": {"readyFile": str(live_file), "instanceId": "c" * 64, "token": "d" * 64}}
        handle = types.SimpleNamespace(role_run_control=control, role_run_identity=identity,
                                       execution_live_file=str(live_file), pid=42, cancel_requested=False)
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
        control = execution.control
        control.attach(handle, object())
        self.addCleanup(lambda: (control.stop(), control.join(2)) if control.ident is not None else None)
        return control, directory

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
        control, directory = self.control()
        control.start()
        self.publish_activity(directory, event_seq=3)
        calls = control.execution.client.progress_calls
        self.assertTrue(wait_for(lambda: len(calls) == 1, 3))
        self.assertEqual(calls[0]["data"]["activity"]["eventSeq"], 3)
        time.sleep(0.5)
        self.assertEqual(len(calls), 1, "the same publication never repeats")
        self.publish_activity(directory, event_seq=4)
        self.assertTrue(wait_for(lambda: len(calls) == 2, 3))

    def test_a_foreign_attempts_endpoint_is_never_forwarded(self):
        control, directory = self.control()
        self.publish_activity(directory, attempt_id="another-attempt", event_seq=9)
        control.start()
        time.sleep(0.5)
        self.assertEqual(control.execution.client.progress_calls, [])

    def test_a_live_record_of_another_invocation_never_binds_or_bypasses(self):
        control, directory = self.control()
        self.publish_activity(directory, event_seq=3)
        live_path = Path(control.handle.execution_live_file)
        original = live_path.read_text()
        request = json.loads(original)
        request["identity"]["invocationId"] = "foreign-invocation"
        live_path.write_text(json.dumps(request))
        control.start()
        time.sleep(0.5)
        self.assertEqual(control.execution.client.progress_calls, [])
        live_path.write_text(original)
        self.assertTrue(wait_for(lambda: len(control.execution.client.progress_calls) == 1, 3))
        self.assertEqual(control.execution.client.progress_calls[0]["data"]["activity"]["eventSeq"], 3)

    def test_an_unextracted_command_has_no_live_activity_or_file_fallback(self):
        control, directory = self.control(with_request=False, harness="command")
        control.handle.role_run_control = {"operation": "worker", "harness": "command"}
        (directory / "activity.json").write_text('{"phase":"streaming-model","eventSeq":3}')
        control.start()
        time.sleep(0.5)
        self.assertEqual(control.execution.client.progress_calls, [])

    def test_a_channel_unavailability_is_not_a_stop(self):
        control, directory = self.control()
        self.publish_activity(directory, event_seq=3)
        endpoint = self.endpoints[str(directory)]
        with mock.patch.object(endpoint, "observe", side_effect=BoardError("INTERNAL", "unreadable")):
            control.start()
            time.sleep(0.5)
            self.assertEqual(control.execution.client.progress_calls, [])
            self.assertTrue(control.is_alive())
