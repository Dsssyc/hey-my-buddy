"""One execution endpoint stays available across native run transitions."""
from __future__ import annotations

import os
import json
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from process_observation import wait_for_unreaped_child

from hey_my_buddy.buddy.runtime.execution import Execution, ModelResponseWait, _ExecutionLive, validate_claim_turn
from hey_my_buddy.buddy.harnesses.base import ExecutionContext, AdapterOutcome
from hey_my_buddy.errors import BoardError
from hey_my_buddy.json_codec import canonical_json
from hey_my_buddy.protocol import execution as launch_protocol
from hey_my_buddy.protocol.execution import read_execution_endpoint
from hey_my_buddy.protocol.process_identity import boot_id, _creation_time
from hey_my_buddy.protocol import process_identity
from hey_my_buddy.buddy.runtime import command
from hey_my_buddy.buddy.harnesses.live import LiveRequest, InquiryPayload
from hey_my_buddy.paths import native_path


def self_identity_for_endpoint() -> dict:
    """Describe this test host in the platform's current identity shape."""
    pid = os.getpid()
    started_at = _creation_time(pid)
    if os.name == "nt":
        return {"pid": pid, "startedAt": started_at,
                "jobName": "Local\\HeyMyBuddy-00000000-0000-4000-8000-000000000001"}
    return {"bootId": boot_id(), "pid": pid, "startedAt": started_at, "jobName": None}


class ExecutionEndpointTests(unittest.TestCase):
    def test_harness_retry_requires_proof_the_native_request_was_not_sent(self):
        from hey_my_buddy.blackboard.routing.statistics import round_classification
        from hey_my_buddy.buddy.harnesses.run_contract import RunEnd, WorkingDirectoryRejection
        from hey_my_buddy.buddy.roles import controller
        from hey_my_buddy.install import runtime
        from hey_my_buddy.protocol.owned_process import WorkingDirectoryRejected

        cases = [("transport-error", sent, started)
                 for sent, started in ((True, False), (None, False), (False, False), (False, None))]
        cases.append((WorkingDirectoryRejected.code, False, False))
        for code, sent, started in cases:
            with self.subTest(code=code, nativeRequestSent=sent, modelResponded=started), \
                 tempfile.TemporaryDirectory(prefix="premodel-retry-") as directory:
                state = Path(directory)
                spec = {"adapter": "decision", "cwd": directory, "modelResponseWaitSeconds": 60, "decision": {"kind": "maintain"}}
                claim = {"task": {"taskId": "task", "spec": spec},
                         "attempt": {"taskId": "task", "attemptId": "attempt", "generation": 1},
                         "decisionInput": {"kind": "maintain", "buddy": {"harness": "dsh"}}}
                execution = Execution(claim, state, "n" * 32, client=mock.Mock())
                execution.directory.mkdir(parents=True, mode=0o700)
                execution.evidence_directory.mkdir(parents=True, mode=0o700, exist_ok=True)
                execution.receipt = lambda claim, report, directory: report
                handle = SimpleNamespace(wait=lambda timeout: 0, cancel_requested=False, result=None, model_responded=False,
                                         stop_initiator=None, grace_expired=False)
                holder = {"preflightControl": mock.Mock(), "harnessHistory": [{"harness": "dsh"}]}
                outcome = AdapterOutcome(status="failed", shutdown_confirmed=True,
                    result={"code": code, "nativeRequestSent": sent, "modelResponded": started})
                directory_rejected = code == WorkingDirectoryRejected.code
                if directory_rejected:
                    rejection = WorkingDirectoryRejection(cwd=directory, system_reason="WinError 267")
                    handle.result = SimpleNamespace(activity=None, end=RunEnd(
                        status="error", reason_code=code, working_directory_rejection=rejection))
                with mock.patch.object(runtime, "resolve_runtime", return_value={}), \
                     mock.patch.object(controller, "worker_executor", return_value=object()), \
                     mock.patch.object(controller, "prepare_worker_run", return_value=controller.WorkerPreparation(True)), \
                     mock.patch.object(controller, "worker_start", return_value=handle) as start, \
                     mock.patch.object(controller, "worker_collect", return_value=outcome):
                    if sent is False and not directory_rejected:
                        with self.assertRaises(BoardError) as refused:
                            execution._execute_selected(claim, claim["task"], spec, claim["attempt"], state, holder)
                        self.assertEqual(refused.exception.code, "HARNESS_PREMODEL_FAILED")
                        self.assertNotIn("handle", holder)
                    else:
                        report = execution._execute_selected(claim, claim["task"], spec, claim["attempt"], state, holder)
                        self.assertEqual(report["result"]["nativeRequestSent"], sent)
                        self.assertIs(holder["handle"], handle)
                        if directory_rejected:
                            self.assertEqual(report["terminationReason"], "workspace-error")
                            self.assertEqual(round_classification(report), "neither")
                            self.assertEqual(report["result"]["reason"], {"code": code, "params": {
                                "cwd": directory, "systemReason": "WinError 267"}})
                    start.assert_called_once()

    def test_harness_prepare_preflight_replays_the_same_command_across_ipc_tear_down(self):
        # A same-contract hot swap tears the old daemon down mid-call; the
        # idempotent preflight must replay its exact commandId, not fail.
        from hey_my_buddy.blackboard.service import harness_health
        from hey_my_buddy.buddy.harnesses import runtime_selection
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            spec = {"adapter": "decision", "cwd": directory, "modelResponseWaitSeconds": 60,
                    "decision": {"kind": "maintain"}}
            claim = {"task": {"taskId": "task", "spec": spec},
                     "attempt": {"taskId": "task", "attemptId": "attempt", "generation": 1},
                     "decisionInput": {"kind": "maintain", "routingMode": "fast",
                                       "buddy": {"harness": "dsh"}}}
            client = mock.Mock()
            execution = Execution(claim, state, "n" * 32, client=client)
            calls = []

            def harness_prepare(operation, params):
                calls.append((operation, dict(params)))
                if len(calls) == 1:
                    raise BoardError("C_TWO_AT_CLIENT_CALLING_RESOURCE", "IPC client closed",
                                     category='transport', transport_kind='ipc', transport_phase='dispatch_uncertain')
                return {"harness": {"status": "ready", "revision": 1}}

            client.call.side_effect = harness_prepare
            waits = []
            with mock.patch.object(harness_health, "HARNESSES", {"dsh"}), \
                 mock.patch.object(runtime_selection, "bound") as bound, \
                 mock.patch.object(execution.control, "wait",
                                   side_effect=lambda seconds: waits.append(seconds)), \
                 mock.patch.object(execution, "_execute_selected",
                                   return_value={"report": {"status": "ok"}}) as selected:
                receipt = execution._execute_guarded(claim, claim["task"], spec,
                                                     claim["attempt"], execution.directory, {})
            self.assertEqual(receipt["report"]["status"], "ok")
            selected.assert_called_once()
            self.assertEqual(len(calls), 2)
            self.assertEqual(calls[0], calls[1])
            self.assertEqual(calls[0][0], "harness_prepare")
            self.assertEqual(calls[0][1]["commandId"], "harness-attempt-0")
            self.assertEqual(waits, [15.0])
            bound.assert_called_once()

    def test_forced_preflight_replays_only_before_dispatch(self):
        from hey_my_buddy.buddy.harnesses import runtime_selection

        spec = {'adapter': 'codex'}
        claim = {'task': {'taskId': 'task', 'spec': spec},
                 'attempt': {'taskId': 'task', 'attemptId': 'attempt', 'generation': 1}}
        for phase, unknown in (('pre_dispatch', False), ('dispatch_uncertain', False),
                               ('dispatch_uncertain', True)):
            with self.subTest(phase=phase, unknown=unknown), tempfile.TemporaryDirectory() as directory:
                client = mock.Mock()
                execution = Execution(claim, Path(directory), 'n' * 32, client=client)
                failure = BoardError('C_TWO_AT_CLIENT_CALLING_RESOURCE', 'IPC client closed',
                                     category='transport', transport_kind='ipc', transport_phase=phase)
                replies = [{'harness': {'status': 'unhealthy', 'revision': 1}}]
                if unknown:
                    replies.append({'harness': {'status': 'unknown', 'revision': 2}})
                client.call.side_effect = [*replies, failure, {'harness': {'status': 'ready', 'revision': 3}}]
                with mock.patch.object(runtime_selection, 'bound'), \
                     mock.patch.object(execution.control, 'wait') as wait, \
                     mock.patch.object(execution, '_execute_selected', return_value={'status': 'ok'}) as selected:
                    if phase == 'dispatch_uncertain' and not unknown:
                        with self.assertRaises(BoardError) as caught:
                            execution._execute_guarded(claim, claim['task'], spec, claim['attempt'], execution.directory, {})
                        self.assertIs(caught.exception, failure)
                        wait.assert_not_called()
                        selected.assert_not_called()
                        self.assertEqual(client.call.call_count, 2)
                    else:
                        self.assertEqual(execution._execute_guarded(
                            claim, claim['task'], spec, claim['attempt'], execution.directory, {}), {'status': 'ok'})
                        selected.assert_called_once()
                        self.assertEqual(client.call.call_count, len(replies) + 2)
                        self.assertEqual(client.call.call_args_list[-2], client.call.call_args_list[-1])
                        self.assertEqual(wait.call_count, 2 if unknown else 1)
                self.assertTrue(client.call.call_args_list[1].args[1]['retry'])
                self.assertEqual(client.call.call_args.args[1]['retry'], not unknown)

        from c_two.error import CoreError
        from hey_my_buddy.buddy.runtime.receipts import is_retryable
        from hey_my_buddy.protocol import transport
        from hey_my_buddy.protocol.client import BoardClient

        self.assertFalse(is_retryable(
            BoardError('SERVICE_UNAVAILABLE', 'uncertain', transport_phase='dispatch_uncertain'),
            pre_dispatch_only=True))
        direct_uncertain = CoreError('transport', 'peer closed mid-call')
        direct_uncertain.transport_kind = 'ipc'
        direct_uncertain.transport_phase = 'dispatch_uncertain'
        direct_uncertain.io_kind = 'connection_refused'
        self.assertTrue(is_retryable(transport.local_rpc_error(direct_uncertain), pre_dispatch_only=False))
        self.assertFalse(is_retryable(transport.local_rpc_error(direct_uncertain), pre_dispatch_only=True))
        endpoint = {'address': 'ipc://fixture', 'token': 'fixture'}
        torn = transport.ServiceError('C_TWO_AT_CLIENT_CALLING_RESOURCE', 'IPC client closed',
                                      category='transport', transport_kind='ipc', transport_phase='dispatch_uncertain')
        for fail_on in ('ping', 'prepare'):
            with self.subTest(fail_on=fail_on), tempfile.TemporaryDirectory() as directory:
                state = Path(directory)
                board_client = BoardClient(state, autostart=False)
                execution = Execution(claim, state, 'n' * 32, client=board_client)
                forced_calls = []
                pings = 0

                def request(_endpoint, operation, params, resource='control', *, state_dir):
                    nonlocal pings
                    if operation == 'ping':
                        pings += 1
                        if fail_on == 'ping' and pings == 2:
                            raise torn
                        return {'ok': True}
                    if not params['retry']:
                        return {'harness': {'status': 'unhealthy', 'revision': 1}}
                    forced_calls.append(dict(params))
                    if fail_on == 'prepare' and len(forced_calls) == 1:
                        raise torn
                    return {'harness': {'status': 'ready', 'revision': 2}}

                with mock.patch.object(transport, '_read_endpoint', return_value=endpoint), \
                     mock.patch.object(transport.private_dirs, 'linked', return_value=True), \
                     mock.patch.object(transport, '_request', side_effect=request), \
                     mock.patch.object(runtime_selection, 'bound'), \
                     mock.patch.object(execution.control, 'wait') as wait, \
                     mock.patch.object(execution, '_execute_selected', return_value={'status': 'ok'}) as selected:
                    if fail_on == 'ping':
                        self.assertEqual(execution._execute_guarded(
                            claim, claim['task'], spec, claim['attempt'], execution.directory, {}), {'status': 'ok'})
                        self.assertEqual(len(forced_calls), 1)
                        wait.assert_called_once()
                        selected.assert_called_once()
                    else:
                        with self.assertRaises(BoardError) as caught:
                            execution._execute_guarded(claim, claim['task'], spec, claim['attempt'], execution.directory, {})
                        self.assertIs(caught.exception, torn)
                        self.assertEqual(len(forced_calls), 1)
                        wait.assert_not_called()
                        selected.assert_not_called()

    def test_signal_stop_is_lock_free_and_wakes_wait_even_while_progress_blocks(self):
        with tempfile.TemporaryDirectory() as directory:
            spec = {'adapter': 'codex'}
            claim = {'task': {'taskId': 'task', 'spec': spec},
                     'attempt': {'taskId': 'task', 'attemptId': 'attempt', 'generation': 1}}
            client = mock.Mock()
            execution = Execution(claim, Path(directory), 'n' * 32, client=client)
            control = execution.control
            with mock.patch.object(control._interrupted, 'set',
                                   side_effect=AssertionError('signal handler called _interrupted.set')), \
                 mock.patch.object(control._wake, 'set',
                                   side_effect=AssertionError('signal handler called _wake.set')):
                execution.request_stop('unknown', None, force=True, from_signal=True)
            self.assertFalse(control._interrupted.is_set())
            self.assertEqual(control.cancel_view, {'cancelRequested': True, 'stopInitiator': 'unknown'})
            control.cancel_view = None
            control.requests = 0

            progress_entered = threading.Event()
            release_progress = threading.Event()

            def blocked_progress(*_args, **_kwargs):
                progress_entered.set()
                release_progress.wait(5)
                return {}

            client.progress.side_effect = blocked_progress
            execution.hint(waitingReason={'code': 'router-input-unknown', 'params': {}})
            flusher = execution._flusher
            flusher.start()
            control.start()
            thread = None
            try:
                self.assertTrue(progress_entered.wait(2), 'hint flusher did not enter progress')
                entered = threading.Event()
                finished = threading.Event()

                def waiter():
                    entered.set()
                    control.wait(180.0)
                    finished.set()

                thread = threading.Thread(target=waiter)
                thread.start()
                self.assertTrue(entered.wait(2))
                time.sleep(0.05)
                execution.request_stop('unknown', None, force=True, from_signal=True)
                self.assertTrue(finished.wait(1.0), 'wait did not observe signal stop while progress blocked')
            finally:
                flusher.stop()
                release_progress.set()
                control.stop()
                flusher.join(2)
                control.join(2)
                if thread is not None:
                    thread.join(2)

    def test_a_late_stop_never_rewrites_an_already_ended_native_failure(self):
        from hey_my_buddy.buddy.harnesses import run_contract as rc
        from hey_my_buddy.buddy.roles import controller
        from hey_my_buddy.buddy.roles.run_execution import InlineRunHandle
        from hey_my_buddy.install import runtime

        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            spec = {"adapter": "decision", "cwd": directory, "modelResponseWaitSeconds": 60,
                    "decision": {"kind": "profile"}}
            claim = {"task": {"taskId": "task", "spec": spec},
                     "attempt": {"taskId": "task", "attemptId": "attempt", "generation": 1},
                     "decisionInput": {"kind": "profile", "buddy": {"harness": "codex"}}}
            execution = Execution(claim, state, "n" * 32, client=mock.Mock())
            execution.directory.mkdir(parents=True, mode=0o700)
            execution.evidence_directory.mkdir(parents=True, mode=0o700, exist_ok=True)
            execution.receipt = lambda claim, report, directory: report
            control = execution.control
            identity = rc.RunIdentity(task_id="task", attempt_id="attempt", generation=1, invocation_id="call")
            handle = InlineRunHandle({"timeoutSeconds": None}, identity)
            # The native run already failed and its evidence is settled: the
            # handle reports ended and only the main thread's detach has not
            # run yet — exactly the window a late stop request lands in.
            handle.result = rc.RunResult(identity=identity, harness="codex",
                end=rc.RunEnd(status="error", reason_code="native-turn-failed",
                              message="Codex turn did not complete successfully"),
                stop_evidence=rc.StopEvidence(native=rc.StopLayer(group_state="gone")))
            handle.exit_code = 1
            handle.done.set()
            consumed = threading.Event()

            class Executor:
                """The executor's flag effects on an inline handle, as the registry applies them."""

                def stop(self, owned, *, stop_initiator, grace_deadline):
                    owned.force_stop_requested = True
                    self.cancel(owned)

                def cancel(self, owned, **_kwargs):
                    owned.terminate()

            def wait(timeout):
                # The stop is requested while the handle is attached and
                # finished, and the control thread consumes it before the
                # main thread leaves this wait and detaches the handle.
                execution.request_stop("console", None, force=True)
                deadline = time.monotonic() + 2
                while control.applied != control.requests and time.monotonic() < deadline:
                    time.sleep(0.01)
                consumed.set()
                return handle.exit_code

            handle.wait = wait
            outcome = AdapterOutcome(status="failed", shutdown_confirmed=True,
                result={"code": "native-error", "nativeRequestSent": True})
            control.start()
            try:
                with mock.patch.object(runtime, "resolve_runtime", return_value={}), \
                     mock.patch.object(controller, "worker_executor", return_value=Executor()), \
                     mock.patch.object(controller, "prepare_worker_run",
                                       return_value=controller.WorkerPreparation(True)), \
                     mock.patch.object(controller, "worker_start", return_value=handle), \
                     mock.patch.object(controller, "worker_collect", return_value=outcome):
                    report = execution._execute_selected(
                        claim, claim["task"], spec, claim["attempt"], execution.directory,
                        {"preflightControl": control})
            finally:
                control.stop()
            control.join(2)
            self.assertFalse(control.is_alive())
            self.assertTrue(consumed.is_set())
            self.assertEqual(control.applied, control.requests)
            # The settled native failure keeps its own reason: the late stop
            # neither cancelled the attempt nor named its initiator.
            self.assertEqual(report["status"], "failed")
            self.assertEqual(report["terminationReason"], "harness-error")
            self.assertFalse(handle.cancel_requested)
            self.assertFalse(handle.force_stop_requested)
            self.assertIsNone(handle.stop_initiator)

    def test_a_late_stop_still_stops_a_process_handle_with_living_descendants(self):
        from hey_my_buddy.buddy.roles import controller
        from hey_my_buddy.install import runtime
        from hey_my_buddy.protocol.process_handle import ProcessHandle

        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            spec = {"adapter": "command", "cwd": directory, "command": ["true"]}
            claim = {"task": {"taskId": "task", "spec": spec},
                     "attempt": {"taskId": "task", "attemptId": "attempt", "generation": 1}}
            execution = Execution(claim, state, "n" * 32, client=mock.Mock())
            execution.directory.mkdir(parents=True, mode=0o700)
            execution.evidence_directory.mkdir(parents=True, mode=0o700, exist_ok=True)
            execution.receipt = lambda claim, report, directory: report
            control = execution.control

            class LivingJob:
                def __init__(self):
                    self.active_count = 1
                    self.closed = False

                def active(self):
                    return self.active_count

                def close(self, *, confirmed=False):
                    self.closed = confirmed

            job = LivingJob()
            process = SimpleNamespace(pid=4242, returncode=7, poll=lambda: 7, _buddy_job=job)
            handle = ProcessHandle(process, log_paths={}, stop_channel="signal")
            # Root process already exited with code 7, so handle.finished is
            # True, while a child in the Job is still alive.
            self.assertTrue(handle.finished)
            self.assertFalse(handle.shutdown_confirmed(settle_seconds=0))

            calls = []

            class Executor:
                def stop(self, owned, *, stop_initiator, grace_deadline):
                    calls.append(("stop", stop_initiator))
                    owned.cancel_requested = True
                    owned.stop_initiator = stop_initiator

                def cancel(self, owned, **_kwargs):
                    calls.append(("cancel", owned.stop_initiator))
                    owned.cancel_requested = True

            def wait(timeout):
                execution.request_stop("console", None, force=True)
                deadline = time.monotonic() + 2
                while control.applied != control.requests and time.monotonic() < deadline:
                    time.sleep(0.01)
                return 7

            handle.wait = wait
            outcome = AdapterOutcome(status="cancelled", shutdown_confirmed=True, exit_code=7)
            control.start()
            try:
                with mock.patch.object(runtime, "resolve_runtime", return_value={}), \
                     mock.patch.object(controller, "worker_executor", return_value=Executor()), \
                     mock.patch.object(controller, "prepare_worker_run",
                                       return_value=controller.WorkerPreparation(True)), \
                     mock.patch.object(controller, "worker_start", return_value=handle), \
                     mock.patch.object(controller, "worker_collect", return_value=outcome):
                    report = execution._execute_selected(
                        claim, claim["task"], spec, claim["attempt"], execution.directory,
                        {"preflightControl": control})
            finally:
                control.stop()
            control.join(2)
            self.assertFalse(control.is_alive())
            self.assertEqual(len(calls), 1, "living Job descendants must still receive the stop")
            self.assertEqual(report["status"], "cancelled")
            self.assertEqual(report["terminationReason"], "console-stop")

    def test_detach_dispatches_pending_stop_when_wait_beats_control_thread(self):
        from hey_my_buddy.buddy.roles import controller
        from hey_my_buddy.install import runtime
        from hey_my_buddy.protocol.process_handle import ProcessHandle

        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            spec = {"adapter": "command", "cwd": directory, "command": ["true"]}
            claim = {"task": {"taskId": "task", "spec": spec},
                     "attempt": {"taskId": "task", "attemptId": "attempt", "generation": 1}}
            execution = Execution(claim, state, "n" * 32, client=mock.Mock())
            execution.directory.mkdir(parents=True, mode=0o700)
            execution.evidence_directory.mkdir(parents=True, mode=0o700, exist_ok=True)
            execution.receipt = lambda claim, report, directory: report
            control = execution.control

            class LivingJob:
                def active(self):
                    return 1

                def close(self, *, confirmed=False):
                    pass

            process = SimpleNamespace(pid=4243, returncode=7, poll=lambda: 7, _buddy_job=LivingJob())
            handle = ProcessHandle(process, log_paths={}, stop_channel="signal")
            calls = []

            class Executor:
                def stop(self, owned, *, stop_initiator, grace_deadline):
                    calls.append(("stop", stop_initiator))
                    owned.cancel_requested = True
                    owned.stop_initiator = stop_initiator

                def cancel(self, owned, **_kwargs):
                    calls.append(("cancel", owned.stop_initiator))
                    owned.cancel_requested = True

            # Request a force stop while the control thread is NOT started, so
            # main-thread wait()->detach() is guaranteed to win the race.
            def wait(timeout):
                execution.request_stop("console", None, force=True)
                return 7

            handle.wait = wait
            outcome = AdapterOutcome(status="cancelled", shutdown_confirmed=True, exit_code=7)
            with mock.patch.object(runtime, "resolve_runtime", return_value={}), \
                 mock.patch.object(controller, "worker_executor", return_value=Executor()), \
                 mock.patch.object(controller, "prepare_worker_run",
                                   return_value=controller.WorkerPreparation(True)), \
                 mock.patch.object(controller, "worker_start", return_value=handle), \
                 mock.patch.object(controller, "worker_collect", return_value=outcome):
                report = execution._execute_selected(
                    claim, claim["task"], spec, claim["attempt"], execution.directory,
                    {"preflightControl": control})
            self.assertEqual(calls, [("cancel", "console")])
            self.assertEqual(control.applied, control.requests)
            self.assertEqual(report["status"], "cancelled")
            self.assertEqual(report["terminationReason"], "console-stop")

    def test_model_wait_begin_clears_previous_response_atomically_under_lock(self):
        from hey_my_buddy.buddy.runtime import execution as entry
        with tempfile.TemporaryDirectory() as directory:
            clock = [0.0]
            wait = entry.ModelResponseWait(
                Path(directory) / "budget.json",
                {"taskId": "task", "attemptId": "attempt", "generation": 1}, 1)
            handle = SimpleNamespace(model_responded=True)
            with mock.patch.object(entry, "time", SimpleNamespace(monotonic=lambda: clock[0])):
                wait.begin(handle)
                self.assertFalse(handle.model_responded)
                self.assertFalse(wait.expired(handle))
                clock[0] = 2.0
                self.assertTrue(wait.expired(handle))
                self.assertTrue(wait.timed_out)

    def test_non_profile_inline_execute_aborts_before_harness_and_preserves_inflight_wrap_up(self):
        from hey_my_buddy.buddy.harnesses import run_contract as rc
        from hey_my_buddy.buddy.roles import controller, inline_run, run_execution

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            control = {
                "operation": "fast", "harness": "dsh", "privateRoot": str(root / "fast"),
                "nativeRoot": str(root / "native"), "stateDir": str(root),
                "sessionRecordPath": None, "cwd": str(root),
                "taskId": "task", "attemptId": "attempt", "generation": 1,
                "spec": {"provider": "deepseek-official", "model": "deepseek-v4-flash", "effort": "max"},
            }
            identity = rc.RunIdentity(task_id="task", attempt_id="attempt", generation=1, invocation_id="fast-1")
            handle = run_execution.InlineRunHandle(control, identity)
            handle.accepted_stop = lambda: {"stopInitiator": "console"}
            fake_request = SimpleNamespace(identity=identity, harness="dsh", buddy=rc.RunBuddy(
                provider="deepseek-official", model="deepseek-v4-flash", effort="max"))
            fake_correction = SimpleNamespace(stop_reason=None)
            parts = SimpleNamespace()
            with mock.patch.object(inline_run, "run_seam", return_value=object()), \
                 mock.patch.object(run_execution, "fast_request",
                                   return_value=(fake_request, None, lambda _f: None, fake_correction, parts)), \
                 mock.patch.object(inline_run, "selected", return_value=None), \
                 mock.patch.object(inline_run, "select_input_limit", return_value=None), \
                 mock.patch.object(inline_run, "check_prompt_input",
                                   return_value=SimpleNamespace(blocked=False)), \
                 mock.patch.object(inline_run, "run_harness", side_effect=AssertionError("must not enter harness")):
                result, _request, _verdict, code = inline_run.execute(control, handle)
            self.assertEqual(code, 1)
            self.assertEqual(result.end.status, "cancelled")
            self.assertIs(result.native_request_sent, False)
            self.assertIs(handle.cancel_requested, True)
            self.assertEqual(handle.stop_initiator, "console")

            # While a Worker is already running inside run_harness, polling the
            # in-flight cancelled callback never sets cancel_requested ahead of
            # controller.worker_stop, so Host wrap-up grace stays intact.
            worker_handle = run_execution.InlineRunHandle(control, identity)
            worker_handle.accepted_stop = lambda: {"stopInitiator": "host"}
            worker_control = {**control, "operation": "worker", "harness": "codex"}
            stop_calls = []

            class WorkerExecutor:
                def stop(self, owned, *, stop_initiator, grace_deadline):
                    stop_calls.append((stop_initiator, grace_deadline))
                    controller.start_wrap_up(owned, grace_deadline, stop_initiator=stop_initiator)

            def fake_worker_run(_module, request, *, observer, services, cancelled):
                # Polling cancelled before worker_stop runs must stay read-only.
                self.assertFalse(cancelled())
                self.assertFalse(worker_handle.cancel_requested)
                controller.worker_stop(WorkerExecutor(), worker_handle,
                                       stop_initiator="host", grace_deadline=time.monotonic() + 30)
                self.assertTrue(cancelled())
                self.assertTrue(worker_handle.wrap_up_started)
                self.assertIsNotNone(worker_handle.grace_deadline)
                return rc.RunResult(identity=request.identity, harness=request.harness,
                    end=rc.RunEnd(status="cancelled"), buddy=rc.ResultBuddy(requested=request.buddy),
                    stop_evidence=rc.StopEvidence(native=rc.StopLayer(group_state="gone")))

            worker_request = SimpleNamespace(identity=identity, harness="codex", buddy=fake_request.buddy,
                                             input_text="task")
            # Clear accepted_stop during pre-launch so worker enters run_harness,
            # then enable it right before run_harness to mimic an in-flight stop.
            in_flight = [None]
            worker_handle.accepted_stop = lambda: in_flight[0]
            def enter_worker(_module, request, *, observer, services, cancelled):
                in_flight[0] = {"stopInitiator": "host"}
                return fake_worker_run(_module, request, observer=observer, services=services, cancelled=cancelled)

            with mock.patch.object(inline_run, "run_seam", return_value=object()), \
                 mock.patch.object(run_execution, "worker_request",
                                   return_value=(worker_request, None, lambda _f: None, None, parts)), \
                 mock.patch.object(inline_run, "selected", return_value=None), \
                 mock.patch.object(inline_run, "select_input_limit", return_value=None), \
                 mock.patch.object(inline_run, "check_prompt_input",
                                   return_value=SimpleNamespace(blocked=False)), \
                 mock.patch.object(inline_run, "_summary_run",
                                   side_effect=lambda c, r, res, m, d, h: (c, r, res, False, False)) as summary_run, \
                 mock.patch.object(inline_run, "run_harness", side_effect=enter_worker):
                inline_run.execute(worker_control, worker_handle)
            self.assertEqual(len(stop_calls), 1)
            summary_run.assert_called_once()

    def test_stop_arriving_during_collection_reaches_unconfirmed_process_handle(self):
        from hey_my_buddy.buddy.roles import controller
        from hey_my_buddy.install import runtime
        from hey_my_buddy.protocol.process_handle import ProcessHandle

        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            spec = {"adapter": "command", "cwd": directory, "command": ["true"]}
            claim = {"task": {"taskId": "task", "spec": spec},
                     "attempt": {"taskId": "task", "attemptId": "attempt", "generation": 1}}
            execution = Execution(claim, state, "n" * 32, client=mock.Mock())
            execution.directory.mkdir(parents=True, mode=0o700)
            execution.evidence_directory.mkdir(parents=True, mode=0o700, exist_ok=True)
            execution.receipt = lambda claim, report, directory: report
            control = execution.control

            class LivingJob:
                def active(self):
                    return 1

                def close(self, *, confirmed=False):
                    pass

            process = SimpleNamespace(pid=4244, returncode=7, poll=lambda: 7, _buddy_job=LivingJob())
            handle = ProcessHandle(process, log_paths={}, stop_channel="signal")
            handle.wait = lambda timeout=None: 7
            calls = []

            class Executor:
                def stop(self, owned, *, stop_initiator, grace_deadline):
                    calls.append(("stop", stop_initiator))
                    owned.cancel_requested = True
                    owned.stop_initiator = stop_initiator

                def cancel(self, owned, **_kwargs):
                    calls.append(("cancel", owned.stop_initiator))
                    owned.cancel_requested = True

            def collect(_impl, owned, _context):
                # Console force-stop arrives AFTER wait() returned 7 while
                # worker_collect is running against the still-active Job.
                execution.request_stop("console", None, force=True)
                deadline = time.monotonic() + 2
                while not calls and time.monotonic() < deadline:
                    time.sleep(0.01)
                return AdapterOutcome(status="cancelled", shutdown_confirmed=True, exit_code=7)

            control.start()
            try:
                with mock.patch.object(runtime, "resolve_runtime", return_value={}), \
                     mock.patch.object(controller, "worker_executor", return_value=Executor()), \
                     mock.patch.object(controller, "prepare_worker_run",
                                       return_value=controller.WorkerPreparation(True)), \
                     mock.patch.object(controller, "worker_start", return_value=handle), \
                     mock.patch.object(controller, "worker_collect", side_effect=collect):
                    report = execution._execute_selected(
                        claim, claim["task"], spec, claim["attempt"], execution.directory,
                        {"preflightControl": control})
            finally:
                control.stop()
            control.join(2)
            self.assertIsNone(control.handle)
            self.assertEqual(calls, [("cancel", "console")])
            self.assertEqual(report["status"], "cancelled")
            self.assertEqual(report["terminationReason"], "console-stop")

    def test_prelaunch_stop_beats_input_rejection_live_bind_and_dsh_prompt(self):
        from contextlib import contextmanager
        import queue
        from hey_my_buddy.buddy.harnesses import run_contract as rc
        from hey_my_buddy.buddy.harnesses.dsh import native_run as dsh_run
        from hey_my_buddy.buddy.roles import inline_run, run_execution

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            control = {
                "operation": "worker", "harness": "codex", "privateRoot": str(root / "worker"),
                "nativeRoot": str(root / "native"), "stateDir": str(root),
                "sessionRecordPath": None, "cwd": str(root),
                "taskId": "task", "attemptId": "attempt", "generation": 1,
                "spec": {"provider": "openai", "model": "gpt-6.2-sol", "effort": "high"},
            }
            identity = rc.RunIdentity(task_id="task", attempt_id="attempt", generation=1, invocation_id="w-1")
            buddy = rc.RunBuddy(provider="openai", model="gpt-6.2-sol", effort="high")
            req = SimpleNamespace(identity=identity, harness="codex", buddy=buddy, input_text="x")
            parts = SimpleNamespace()

            # (a) Accepted console stop beats preflight input_error/blocked.
            h1 = run_execution.InlineRunHandle(control, identity)
            h1.accepted_stop = lambda: {"stopInitiator": "console"}
            with mock.patch.object(inline_run, "run_seam", return_value=object()), \
                 mock.patch.object(run_execution, "worker_request",
                                   return_value=(req, None, lambda _f: None, None, parts)), \
                 mock.patch.object(inline_run, "selected", return_value=None), \
                 mock.patch.object(inline_run, "select_input_limit", return_value=None), \
                 mock.patch.object(inline_run, "check_prompt_input",
                                   return_value=SimpleNamespace(blocked=True)), \
                 mock.patch.object(inline_run, "run_harness", side_effect=AssertionError("must not run")):
                res1, _, _, code1 = inline_run.execute(control, h1)
            self.assertEqual((code1, res1.end.status), (1, "cancelled"))
            self.assertIs(h1.cancel_requested, True)
            self.assertEqual(h1.stop_initiator, "console")

            # (b) Stop landing during _inline_live binding still blocks run_harness.
            h2 = run_execution.InlineRunHandle(control, identity)
            @contextmanager
            def stopping_live(_req, _services, owned, **_kwargs):
                owned.cancel_requested = True
                owned.stop_initiator = "console"
                owned.cancelled.set()
                yield None

            with mock.patch.object(inline_run, "run_seam", return_value=object()), \
                 mock.patch.object(run_execution, "worker_request",
                                   return_value=(req, None, lambda _f: None, None, parts)), \
                 mock.patch.object(inline_run, "selected", return_value=None), \
                 mock.patch.object(inline_run, "select_input_limit", return_value=None), \
                 mock.patch.object(inline_run, "check_prompt_input",
                                   return_value=SimpleNamespace(blocked=False)), \
                 mock.patch.object(inline_run, "_inline_live", side_effect=stopping_live), \
                 mock.patch.object(inline_run, "run_harness", side_effect=AssertionError("must not run")):
                res2, _, _, code2 = inline_run.execute(control, h2)
            self.assertEqual((code2, res2.end.status), (1, "cancelled"))
            self.assertIs(res2.native_request_sent, False)

            # (c) DSH _prompt_and_observe never sends a prompt when cancel is already set.
            client = mock.Mock()
            cancel = dsh_run.CancelFlag(lambda: True)
            state = dsh_run._RunState()
            activity = dsh_run.DshActivity()
            with self.assertRaises(dsh_run.NativeError) as caught:
                dsh_run._prompt_and_observe(
                    client=client, pump=queue.Queue(), session_id="sess", input_text="hi",
                    deadline=time.monotonic() + 60, cancel=cancel, state=state,
                    fold=lambda _m: False, notify=lambda: None, activity=activity,
                    denied=lambda: 0, send_cancel=lambda _s: None, session=None)
            self.assertEqual(caught.exception.code, "cancelled")
            client.prompt.assert_not_called()
            self.assertIs(state.native_request_sent, False)

    def test_preflight_cancels_when_control_consumed_the_wake_before_rpc_failed(self):
        with tempfile.TemporaryDirectory() as directory:
            spec = {'adapter': 'codex'}
            claim = {'task': {'taskId': 'task', 'spec': spec},
                     'attempt': {'taskId': 'task', 'attemptId': 'attempt', 'generation': 1}}
            client = mock.Mock()
            execution = Execution(claim, Path(directory), 'n' * 32, client=client)
            native_path(execution.directory).mkdir(parents=True, mode=0o700)
            wake = execution.control._wake
            original_clear, original_wait = wake.clear, wake.wait
            consumed = threading.Event()

            def clear():
                stopped = execution.stop_requested() and wake.is_set()
                original_clear()
                if stopped:
                    consumed.set()

            def wait(seconds):
                self.assertLess(seconds, 1, 'preflight waited on the cleared control wake')
                return original_wait(seconds)

            def prepare(*_args):
                execution.request_stop('console', force=True)
                self.assertTrue(consumed.wait(2), 'control did not consume the stop wake')
                raise BoardError('SERVICE_UNAVAILABLE', 'listener stopped')

            client.call.side_effect = prepare
            with mock.patch.object(wake, 'clear', side_effect=clear), \
                 mock.patch.object(wake, 'wait', side_effect=wait), \
                 mock.patch.object(execution, '_execute_selected') as selected:
                receipt = execution._execute_guarded(claim, claim['task'], spec, claim['attempt'], execution.directory, {})
            self.assertEqual(receipt['report']['status'], 'cancelled')
            self.assertEqual(receipt['report']['terminationReason'], 'console-stop')
            self.assertTrue(receipt['report']['shutdownConfirmed'])
            client.call.assert_called_once()
            selected.assert_not_called()

    def test_review_unavailable_result_uses_frozen_request_without_copying_budget(self):
        execution = object.__new__(Execution)
        claim = {'decisionInput': {'routingMode': 'review', 'modelResponseWaitSeconds': 600}}
        execution.receipt = mock.Mock(side_effect=lambda claim, report, directory: report)
        report = execution._review_unavailable(claim, None, [], 'router-unavailable')
        self.assertNotIn('budget', report['result'])
        self.assertEqual(report['result']['routingMode'], 'review')
        self.assertEqual(report['result']['code'], 'router-review-unavailable')
        self.assertTrue(report['shutdownConfirmed'])
        self.assertEqual(claim['decisionInput']['modelResponseWaitSeconds'], 600)

    def test_absent_registered_server_credential_fails_without_polling(self):
        live = _ExecutionLive(SimpleNamespace(claim={
            "task": {"taskId": "task"}, "attempt": {"attemptId": "attempt", "generation": 1}}))
        with mock.patch("hey_my_buddy.buddy.runtime.execution.cc.register"), \
             mock.patch("hey_my_buddy.buddy.runtime.execution.cc.server_address", return_value="ipc://fixture"), \
             mock.patch("hey_my_buddy.buddy.runtime.execution.cc.server_credential", return_value=None) as credential, \
             self.assertRaises(BoardError) as caught:
            live.start({"pid": 123})
        self.assertEqual(caught.exception.code, "LIVE_UNAVAILABLE")
        self.assertIn("credential is absent", caught.exception.message)
        credential.assert_called_once_with()

    def test_service_claim_turn_shape_is_checked_at_process_boundary(self):
        base = {"task": {"taskId": "task", "spec": {"adapter": "command"}},
                "attempt": {"taskId": "task", "attemptId": "attempt", "generation": 1},
                "agentCredential": "private-fixture"}
        claim = {**base, "turn": {"turnId": "turn-1", "input": {
            "version": 4, "executionWorkspace": {"workspaceId": "ws-" + "a" * 32,
                                                     "manifestSha256": "b" * 64}}}}
        validate_claim_turn(base)  # Internal command executions have no turn.
        validate_claim_turn(claim)
        for bad in (None, [], {"turnId": "", "input": claim["turn"]["input"]},
                    {"turnId": "turn-1", "input": []},
                    {"turnId": "turn-1", "input": {"version": 4, "executionWorkspace": []}},
                    {"turnId": "turn-1", "input": {**claim["turn"]["input"], "version": 4.0}},
                    {"turnId": "turn-1", "input": {"version": 3, "executionWorkspace": {"path": "/workspace"}}},
                    {"turnId": "turn-1", "input": {"version": 4, "executionWorkspace": {"path": ""}}}):
            with self.subTest(turn=bad), self.assertRaises(BoardError) as refused:
                validate_claim_turn({**base, "turn": bad})
            self.assertEqual(refused.exception.code, "INVALID_ARGUMENT")
        with self.assertRaises(BoardError):
            validate_claim_turn({**claim, "agentCredential": None})

    def test_none_claim_requires_explicit_folder_context(self):
        base = {"task": {"taskId": "task", "spec": {"adapter": "codex"}},
                "attempt": {"taskId": "task", "attemptId": "attempt", "generation": 1},
                "agentCredential": "private-fixture"}
        workspace = {"kind": "none", "path": str(Path.cwd()), "projectId": "folder-project",
                     "projectKind": "folder", "access": "read", "integrator": {"role": "host"}}
        claim = {**base, "turn": {"turnId": "turn-1", "input": {
            "version": 5, "executionWorkspace": workspace}}}
        validate_claim_turn(claim)
        for invalid in ({**workspace, "workspaceId": "ws-fake"},
                        {**workspace, "path": "relative"},
                        {**workspace, "access": "full"},
                        {**workspace, "projectKind": "unknown"}):
            with self.subTest(workspace=invalid), self.assertRaises(BoardError) as caught:
                validate_claim_turn({**claim, "turn": {"turnId": "turn-1", "input": {
                    "version": 5, "executionWorkspace": invalid}}})
            self.assertEqual(caught.exception.code, "INVALID_ARGUMENT")

    def test_decision_claim_requires_explicit_mode_buddy_and_wait(self):
        base = {"task": {"taskId": "task", "spec": {"adapter": "decision", "modelResponseWaitSeconds": 60}},
                "attempt": {"taskId": "task", "attemptId": "attempt", "generation": 1}}
        decision = {"routingMode": "review", "buddy": {"harness": "codex"}}
        validate_claim_turn({**base, "decisionInput": decision})
        for bad in (None, {}, {**decision, "routingMode": None}, {**decision, "buddy": {}}):
            with self.subTest(decision=bad), self.assertRaises(BoardError) as caught:
                validate_claim_turn({**base, "decisionInput": bad})
            self.assertEqual(caught.exception.code, "INVALID_ARGUMENT")

    def test_maintenance_claim_has_no_budget_and_keeps_its_identity_checks(self):
        base = {"task": {"taskId": "task", "spec": {"adapter": "decision", "modelResponseWaitSeconds": 60,
                "decision": {"decisionId": "maint-1", "kind": "maintain"}}},
                "attempt": {"taskId": "task", "attemptId": "attempt", "generation": 1}}
        document = {"kind": "maintain", "maintenanceRunId": "maint-1", "routingMode": "fast",
                    "buddy": {"harness": "dsh"}, "input": {"sha256": "a" * 64}}
        validate_claim_turn({**base, "decisionInput": document})
        for change in ({"maintenanceRunId": "wrong"}, {"routingMode": "review"}, {"input": None}):
            with self.subTest(change=change), self.assertRaises(BoardError):
                validate_claim_turn({**base, "decisionInput": {**document, **change}})

    def test_all_router_planes_cancel_only_before_the_model_responds(self):
        from hey_my_buddy.buddy.runtime import execution as module
        from hey_my_buddy.buddy.roles import controller
        from hey_my_buddy.install import runtime
        for kind in ("maintain", "select-fast", "select-review", "profile"):
            for scenario in ("waiting", "responded", "finished"):
                responded = scenario == "responded"
                finished = scenario == "finished"
                with self.subTest(kind=kind, scenario=scenario), tempfile.TemporaryDirectory() as directory:
                    state = Path(directory)
                    spec = {"adapter": "decision", "cwd": directory, "modelResponseWaitSeconds": 60,
                            "decision": {"kind": kind}}
                    # Codex stands for a harness whose protocol can show the
                    # model's start; DSH cannot and is covered by its own test.
                    claim = {"task": {"taskId": "task", "spec": spec},
                             "attempt": {"taskId": "task", "attemptId": "attempt", "generation": 1},
                             "decisionInput": {"kind": kind, "buddy": {"harness": "codex"}}}
                    execution = Execution(claim, state, 'n' * 32, client=mock.Mock())
                    execution.directory.mkdir(parents=True, mode=0o700)
                    execution.evidence_directory.mkdir(parents=True, mode=0o700, exist_ok=True)
                    clock = [0.0]
                    waits = []
                    def wait(timeout):
                        waits.append(timeout)
                        handle.model_responded = responded
                        clock[0] = 10_000.0
                        return None if len(waits) == 1 and not finished else 0
                    handle = SimpleNamespace(wait=wait, grace_deadline=None, force_stop_requested=False,
                                             cancel_requested=False, stop_initiator=None, grace_expired=False,
                                             result=None, model_responded=False)
                    outcome = AdapterOutcome(status='failed' if finished else 'ok', shutdown_confirmed=True,
                        result={'code': 'native-error'} if finished else {'modelResponded': responded})
                    with mock.patch.object(runtime, 'resolve_runtime', return_value={}), \
                         mock.patch.object(controller, 'worker_executor', return_value=object()), \
                         mock.patch.object(controller, 'prepare_worker_run', return_value=controller.WorkerPreparation(True)), \
                         mock.patch.object(controller, 'worker_start', return_value=handle), \
                         mock.patch.object(controller, 'worker_collect', return_value=outcome), \
                         mock.patch.object(controller, 'worker_cancel') as cancel, \
                         mock.patch.object(module, 'time', SimpleNamespace(monotonic=lambda: clock[0])):
                        receipt = execution._execute_selected(claim, claim['task'], spec, claim['attempt'],
                                                              execution.directory, {'preflightControl': mock.Mock()})
                    report = receipt['report']
                    self.assertEqual(report['elapsedSeconds'], 10_000)
                    self.assertEqual((execution.directory / module.BUDGET).exists(), not responded and not finished)
                    if finished:
                        cancel.assert_not_called()
                        self.assertEqual(report['result']['code'], 'native-error')
                        self.assertEqual(report['terminationReason'], 'harness-error')
                    elif responded:
                        cancel.assert_not_called()
                        self.assertEqual((report['status'], report['terminationReason']), ('ok', 'completed'))
                    else:
                        cancel.assert_called_once()
                        self.assertEqual(report['terminationReason'], 'deadline')
                        self.assertEqual(report['result']['code'], 'router-not-responding')
                        self.assertNotIn('seconds', report['result'])
                        self.assertIs(report['result']['modelResponded'], False)

    def test_a_declared_harness_runs_every_router_plane_without_the_start_wait(self):
        from hey_my_buddy.buddy.runtime import execution as module
        from hey_my_buddy.buddy.roles import controller
        from hey_my_buddy.install import runtime
        for kind in ("maintain", "select-fast", "select-review", "profile"):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as directory:
                state = Path(directory)
                spec = {"adapter": "decision", "cwd": directory, "modelResponseWaitSeconds": 60,
                        "decision": {"kind": kind}}
                # DSH's ACP channel cannot show the model's start as it happens,
                # so silence within the wait proves nothing: no wait is applied,
                # no fallback budget file appears and a model that never reports
                # activity still reaches its own end uncancelled.
                claim = {"task": {"taskId": "task", "spec": spec},
                         "attempt": {"taskId": "task", "attemptId": "attempt", "generation": 1},
                         "decisionInput": {"kind": kind, "buddy": {"harness": "dsh"}}}
                execution = Execution(claim, state, 'n' * 32, client=mock.Mock())
                execution.directory.mkdir(parents=True, mode=0o700)
                execution.evidence_directory.mkdir(parents=True, mode=0o700, exist_ok=True)
                clock = [0.0]

                def wait(timeout):
                    clock[0] = 10_000.0
                    return 0

                handle = SimpleNamespace(wait=wait, grace_deadline=None, force_stop_requested=False,
                                         cancel_requested=False, stop_initiator=None, grace_expired=False,
                                         result=None, model_responded=False)
                outcome = AdapterOutcome(status='ok', shutdown_confirmed=True, result={'modelResponded': False})
                with mock.patch.object(runtime, 'resolve_runtime', return_value={}), \
                        mock.patch.object(controller, 'worker_executor', return_value=object()), \
                        mock.patch.object(controller, 'prepare_worker_run', return_value=controller.WorkerPreparation(True)), \
                        mock.patch.object(controller, 'worker_start', return_value=handle), \
                        mock.patch.object(controller, 'worker_collect', return_value=outcome), \
                        mock.patch.object(controller, 'worker_cancel') as cancel, \
                        mock.patch.object(module, 'time', SimpleNamespace(monotonic=lambda: clock[0])):
                    receipt = execution._execute_selected(claim, claim['task'], spec, claim['attempt'],
                                                          execution.directory, {'preflightControl': mock.Mock()})
                report = receipt['report']
                cancel.assert_not_called()
                self.assertFalse((execution.directory / module.BUDGET).exists())
                self.assertEqual((report['status'], report['terminationReason']), ('ok', 'completed'))
                self.assertNotEqual(report['result'].get('code'), 'router-not-responding')

    def test_model_response_removes_wait_file_and_rearms_each_session(self):
        from hey_my_buddy.buddy.runtime import execution as module
        from hey_my_buddy.blackboard.service.execution_scheduler import ExecutionScheduler
        binding = {"taskId": "task", "attemptId": "attempt", "generation": 1}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / module.BUDGET
            clock = [10.0]
            notified = mock.Mock()
            wait = ModelResponseWait(path, binding, 60, notify=notified)
            handle = SimpleNamespace(model_responded=False)
            scheduler_handle = SimpleNamespace(budget_path=path, execution_binding=binding)
            with mock.patch.object(module, 'time', SimpleNamespace(monotonic=lambda: clock[0])):
                wait.begin()
                self.assertEqual(ExecutionScheduler._budget_deadline(scheduler_handle), 70 + module.TIMEOUT_GRACE_SECONDS)
                clock[0] = 30
                handle.model_responded = True
                wait.responded(handle)
                self.assertFalse(path.exists())
                self.assertIsNone(ExecutionScheduler._budget_deadline(scheduler_handle))
                clock[0] = 10_000
                self.assertFalse(wait.expired(handle))
                handle.model_responded = False
                wait.begin()
                self.assertEqual(ExecutionScheduler._budget_deadline(scheduler_handle), 10_060 + module.TIMEOUT_GRACE_SECONDS)
                clock[0] = 10_059
                self.assertFalse(wait.expired(handle))
                clock[0] = 10_060
                self.assertTrue(wait.expired(handle))
                self.assertEqual(notified.call_count, 2)
                wait.responded(handle)
                self.assertFalse(handle.model_responded, "model output after the timeout decision is not adopted")
                self.assertTrue(path.exists(), "the fallback deadline remains after timeout")

    def test_wait_publication_and_notification_do_not_hold_the_deadline_lock(self):
        from hey_my_buddy.buddy.runtime import execution as module
        with tempfile.TemporaryDirectory() as directory:
            observed = threading.Event()
            def read_deadline():
                with wait.lock:
                    self.assertIsNotNone(wait.deadline)
                    observed.set()
            def notify():
                reader = threading.Thread(target=read_deadline, daemon=True)
                reader.start()
                self.assertTrue(observed.wait(1), "notification held the deadline lock")
                reader.join(1)
            wait = ModelResponseWait(Path(directory) / module.BUDGET,
                {"taskId": "task", "attemptId": "attempt", "generation": 1}, 60, notify=notify)
            publish = module.fsync_json
            def publish_unlocked(*args, **kwargs):
                notify()
                observed.clear()
                publish(*args, **kwargs)
            with mock.patch.object(module, 'fsync_json', side_effect=publish_unlocked):
                wait.begin()

    def test_model_response_wait_file_removal_failure_is_not_swallowed(self):
        from hey_my_buddy.buddy.runtime import execution as module
        binding = {"taskId": "task", "attemptId": "attempt", "generation": 1}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / module.BUDGET
            wait = ModelResponseWait(path, binding, 60)
            wait.begin()
            with mock.patch.object(type(path), 'unlink', side_effect=PermissionError('refused')):
                with self.assertRaises(PermissionError):
                    wait.responded(SimpleNamespace(model_responded=True))
            self.assertTrue(path.exists())
            self.assertIsNotNone(wait.deadline)

    def test_a_timeout_keeps_an_earlier_rounds_model_response(self):
        from hey_my_buddy.buddy.runtime import execution as module
        binding = {"taskId": "task", "attemptId": "attempt", "generation": 1}
        wait = ModelResponseWait(Path("~/fixture/unused-budget") / module.BUDGET, binding, 60)
        answered = AdapterOutcome(status="ok", shutdown_confirmed=True,
                                  result={"modelResponded": True, "correctionCount": 0})
        wait.mark_timeout(answered)
        # A later round's expired start wait fails that round without erasing
        # the answer an earlier round of the same attempt already delivered.
        self.assertEqual(answered.status, "failed")
        self.assertEqual(answered.result["code"], "router-not-responding")
        self.assertIs(answered.result["modelResponded"], True)
        silent = AdapterOutcome(status="ok", shutdown_confirmed=True, result={"nativeRequestSent": False})
        wait.mark_timeout(silent)
        self.assertIs(silent.result["modelResponded"], False)

    def test_normalized_native_observer_error_stays_an_execution_failure(self):
        from hey_my_buddy.buddy.runtime import execution as module
        from hey_my_buddy.buddy.roles import controller, run_execution
        from hey_my_buddy.buddy.harnesses import run_contract as rc
        from hey_my_buddy.install import runtime
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            spec = {"adapter": "decision", "cwd": directory, "modelResponseWaitSeconds": 60,
                    "decision": {"kind": "maintain"}}
            claim = {"task": {"taskId": "task", "spec": spec},
                     "attempt": {"taskId": "task", "attemptId": "attempt", "generation": 1},
                     "decisionInput": {"kind": "maintain", "buddy": {"harness": "codex"}}}
            execution = Execution(claim, state, "n" * 32, client=mock.Mock())
            execution.directory.mkdir(parents=True, mode=0o700)
            execution.evidence_directory.mkdir(parents=True, mode=0o700, exist_ok=True)
            identity = rc.RunIdentity(task_id="task", attempt_id="attempt", generation=1, invocation_id="call")
            handle = run_execution.InlineRunHandle({"timeoutSeconds": None}, identity)
            budget_path = native_path(execution.directory / module.BUDGET)
            original_unlink = type(budget_path).unlink
            removals = []
            def unlink(path, *args, **kwargs):
                if path == budget_path:
                    removals.append(path)
                    if len(removals) == 1:
                        raise PermissionError("wait file removal refused")
                return original_unlink(path, *args, **kwargs)
            def start(_implementation, context):
                handle.model_response_wait = context.model_response_wait
                try:
                    handle.observe(lambda facts: rc.FEEDBACK_CONTINUE, {"modelResponded": True})
                except OSError:
                    # A native driver's normal error mapping loses the exception.
                    handle.result = rc.RunResult(identity=identity, harness="codex",
                        end=rc.RunEnd(status="error", reason_code="invalid-native-result"),
                        stop_evidence=rc.StopEvidence(native=rc.StopLayer(group_state="gone")))
                handle.exit_code = 1
                handle.done.set()
                return handle
            def selected(claim, task, spec, attempt, directory, holder):
                holder["preflightControl"] = mock.Mock()
                return execution._execute_selected(claim, task, spec, attempt, directory, holder)
            with mock.patch.object(runtime, "resolve_runtime", return_value={}), \
                 mock.patch.object(controller, "worker_executor", return_value=object()), \
                 mock.patch.object(controller, "prepare_worker_run", return_value=controller.WorkerPreparation(True)), \
                 mock.patch.object(controller, "worker_start", side_effect=start), \
                 mock.patch.object(controller, "worker_cancel"), \
                 mock.patch.object(execution, "_execute_guarded", side_effect=selected), \
                 mock.patch.object(type(budget_path), "unlink", new=unlink):
                receipt = execution.execute()
            self.assertEqual(receipt["report"]["status"], "failed")
            self.assertIn("execution failure", receipt["report"]["error"])
            self.assertIn("wait file removal refused", receipt["report"]["error"])
            self.assertTrue(receipt["report"]["shutdownConfirmed"])
            self.assertEqual(len(removals), 1, "a transient deletion failure must remain an execution failure")
            self.assertTrue(budget_path.exists())

    def test_endpoint_connection_and_call_errors_retain_structured_cause(self):
        identity = launch_protocol.RunIdentity(task_id="task", attempt_id="attempt", generation=1,
                                              invocation_id="execution")
        details = {"io_kind": "permission_denied", "raw_os_error": 13, "io_operation": "connect"}
        for stage in ("connection", "call"):
            with self.subTest(stage=stage):
                client = launch_protocol.ExecutionLiveClient(identity, address="private-address", name="fixture",
                    instance_id="i" * 64, token="t" * 64, state_dir=Path("/private/fixture"))
                error = RuntimeError("native IPC failed")
                for field, value in details.items():
                    setattr(error, field, value)
                peer = mock.Mock()
                bounded = mock.Mock()
                bounded.stop.side_effect = error
                call_view = mock.MagicMock()
                call_view.__enter__.return_value = bounded
                with mock.patch.object(launch_protocol.rpc_config, "configure_client"), \
                     mock.patch.object(launch_protocol.cc, "connect", return_value=peer,
                                       side_effect=error if stage == "connection" else None), \
                     mock.patch.object(launch_protocol.cc, "connection_observation", return_value=object()), \
                     mock.patch.object(launch_protocol.cc, "with_call_options", return_value=call_view), \
                     mock.patch.object(launch_protocol.cc, "close") as close:
                    try:
                        with self.assertRaises(BoardError) as caught:
                            client.stop("service", force=True)
                    finally:
                        client.close()
                    if stage == "call":
                        close.assert_called_once_with(peer)
                    else:
                        close.assert_not_called()
                self.assertEqual(caught.exception.code, "LIVE_UNAVAILABLE")
                for field, value in details.items():
                    self.assertEqual(caught.exception.details[field], value)
                self.assertIs(caught.exception.__cause__, error)

    def test_one_held_proxy_serves_all_live_calls(self):
        identity = launch_protocol.RunIdentity(task_id="task", attempt_id="attempt", generation=1,
                                              invocation_id="execution")
        client = launch_protocol.ExecutionLiveClient(identity, address="private-address", name="fixture",
            instance_id="i" * 64, token="t" * 64, state_dir=Path("/private/fixture"))
        peer = mock.Mock()
        observation = object()
        bounded = mock.Mock()
        bounded.observe.return_value = canonical_json({"observed": True})
        bounded.stop.return_value = canonical_json({"accepted": True})
        bounded.send.return_value = canonical_json({"status": "unavailable"})
        bounded.capabilities.return_value = canonical_json({"inquiryDelivery": "unsupported"})
        call_view = mock.MagicMock()
        call_view.__enter__.return_value = bounded
        with mock.patch.object(launch_protocol.rpc_config, "configure_client"), \
             mock.patch.object(launch_protocol.cc, "connect", return_value=peer) as connect, \
             mock.patch.object(launch_protocol.cc, "connection_observation", return_value=observation) as observe_connection, \
             mock.patch.object(launch_protocol.cc, "with_call_options", return_value=call_view) as options, \
             mock.patch.object(launch_protocol.cc, "close") as close:
            try:
                client.connect()
                self.assertIs(client.connection_observation, observation)
                self.assertEqual(client.stop("service", force=True), {"accepted": True})
                self.assertEqual(client.request({"kind": "inquiry"}, timeout_ms=500), {"status": "unavailable"})
                self.assertEqual(client.observe(limit=1), {"observed": True})
                self.assertEqual(client.capabilities(), {"inquiryDelivery": "unsupported"})
                connect.assert_called_once()
                observe_connection.assert_called_once_with(peer)
                self.assertEqual(options.call_count, 5)
                self.assertTrue(all(call.args == (peer,) for call in options.call_args_list))
            finally:
                client.close()
            close.assert_called_once_with(peer)

    def test_context_uses_validated_turn_and_service_adapter(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            context = ExecutionContext("task", "attempt", 1, {"adapter": "command", "cwd": directory},
                root / "attempt", {}, {"HMB_STATE_DIR": directory})
            self.assertIsNone(context.turn_input)
            self.assertIsNone(context.turn_id)
            self.assertEqual(context.cwd, directory)
            context.turn = {"turnId": "turn-1", "input": {
                "version": 4, "executionWorkspace": {"workspaceId": "ws-" + "a" * 32,
                                                        "manifestSha256": "b" * 64}}}
            context.effective_workspace = {"path": "/workspace"}
            self.assertEqual(context.turn_id, "turn-1")
            self.assertEqual(context.turn_input, context.turn["input"])
            self.assertEqual(context.cwd, "/workspace")
            self.assertEqual(context.credential_file().name, "agent-credential.json")
            context.spec["adapter"] = "codex"
            self.assertIn("codex", context.credential_file().parts)

    @unittest.skipUnless(sys.platform in {"darwin", "linux"}, "POSIX unreaped child failure")
    def test_missing_native_creation_time_cannot_prove_session_shutdown(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            claim = {"task": {"taskId": "task-identity", "spec": {
                "adapter": "command", "task": "echo", "cwd": directory,
                "argv": ["/bin/echo", "hello"]}},
                "attempt": {"taskId": "task-identity", "attemptId": "attempt-identity", "generation": 1}}
            execution = Execution(claim, state, "n" * 64, client=object())
            native_path(execution.directory).mkdir(parents=True, mode=0o700)
            native_path(execution.evidence_directory).mkdir(parents=True, mode=0o700)
            execution.control = mock.Mock(cancel_view=None)
            journal = process_identity.NativeProcessJournal(state / "native-journal.json", {
                "taskId": "task-identity", "attemptId": "attempt-identity", "generation": 1})
            spawn = command.owned_popen
            children = []

            def exited_before_handle(*args, **kwargs):
                child = spawn(*args, **kwargs)
                children.append(child)
                wait_for_unreaped_child(child.pid)
                return child

            try:
                # Reproduce the former reader's None while retaining the real
                # spawn, journal capture, handle cleanup and receipt path.
                with mock.patch.object(command, "owned_popen", side_effect=exited_before_handle), \
                     mock.patch.object(process_identity, "_active_native_journal", journal), \
                     mock.patch.object(process_identity, "_creation_time", return_value=None):
                    report = execution.execute()["report"]
                self.assertEqual(len(children), 1)
                self.assertEqual(report["status"], "failed")
                self.assertEqual(report["terminationReason"], "harness-error")
                self.assertIn("owned child shutdown is unconfirmed", report["error"])
                self.assertFalse(report["shutdownConfirmed"])
                self.assertEqual(children[0].returncode, 0)
            finally:
                for child in children:
                    if child.poll() is None:
                        child.kill()
                    child.wait(timeout=5)

    def test_launch_persists_intent_and_private_startup_before_child_creation(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            claim = {"task": {"taskId": "task-3", "spec": {"adapter": "command"}},
                     "attempt": {"taskId": "task-3", "attemptId": "attempt-3", "generation": 1}}
            handle = SimpleNamespace(pid=301, process=object(), job=object() if os.name == "nt" else None)
            if os.name == "nt":
                identity = {"pid": 301, "startedAt": 1339247086400000000}
            else:
                identity = {"bootId": "linux:00000000-0000-4000-8000-000000000001",
                            "pid": 301, "startedAt": 105, "jobName": None}

            def launched(**_kwargs):
                evidence = state / "attempts/task-3/attempt-3"
                self.assertTrue((evidence / "spawn.intent").exists())
                private = launch_protocol.execution_directory(claim, state)
                self.assertTrue((private / "execution-start.json").exists())
                self.assertEqual(json.loads((evidence / "spawn.intent").read_text(encoding="utf-8")),
                                 {"taskId": "task-3", "attemptId": "attempt-3", "generation": 1})
                startup = json.loads((private / "execution-start.json").read_text(encoding="utf-8"))
                self.assertEqual(startup["claim"], claim)
                self.assertEqual(startup["stateDir"], str(state))
                self.assertEqual(startup["nonce"], "n" * 64)
                self.assertEqual(startup["sessionRecordPath"], None)
                self.assertEqual(startup["jobName"] is not None, os.name == "nt")
                if os.name == "nt":
                    self.assertEqual(startup["jobName"], _kwargs["job_name"])
                    identity["jobName"] = _kwargs["job_name"]
                return handle

            with mock.patch.object(launch_protocol, "launch_controller",
                            side_effect=launched) as controller, \
                 mock.patch.object(launch_protocol, "capture_process_identity", return_value=identity), \
                 mock.patch.object(launch_protocol, "transfer_job_to_child") as transfer:
                launched_handle = launch_protocol.launch_execution(claim, state, "n" * 64)
            self.assertIs(launched_handle, handle)
            self.assertEqual(handle.process_identity, identity)
            self.assertEqual((state / "attempts/task-3/attempt-3/spawn.marker").is_file(), True)
            job_name = controller.call_args.kwargs.get("job_name")
            if os.name == "nt":
                self.assertTrue(job_name.startswith("Local\\HeyMyBuddy-"))
                transfer.assert_called_once_with(handle.process, handle.job)
            else:
                self.assertIsNone(job_name)
                transfer.assert_not_called()
            self.assertEqual(json.loads(native_path(handle.execution_directory / "execution-start.json").read_text(encoding="utf-8")),
                             {"claim": claim, "stateDir": str(state), "nonce": "n" * 64,
                              "jobName": job_name, "sessionRecordPath": None})
            marker = json.loads((state / "attempts/task-3/attempt-3/spawn.marker").read_text(encoding="utf-8"))
            self.assertEqual(marker["processIdentity"], identity)

    def test_startup_write_failure_never_creates_a_child(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            claim = {"task": {"taskId": "task-5", "spec": {"adapter": "command"}},
                     "attempt": {"taskId": "task-5", "attemptId": "attempt-5", "generation": 1}}
            original = launch_protocol.fsync_json

            def write(path, value):
                if path.name == "execution-start.json":
                    raise OSError("startup write failed")
                original(path, value)

            with mock.patch.object(launch_protocol, "fsync_json", side_effect=write), \
                 mock.patch.object(launch_protocol, "launch_controller") as spawn:
                with self.assertRaises(OSError) as caught:
                    launch_protocol.launch_execution(claim, state, "n" * 64)
            spawn.assert_not_called()
            self.assertFalse(caught.exception.spawn_created)
            self.assertTrue((state / "attempts/task-5/attempt-5/spawn.intent").is_file())

    def test_windows_launch_publishes_the_marker_before_resume_and_transfer(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            claim = {"task": {"taskId": "task-7", "spec": {"adapter": "command"}},
                     "attempt": {"taskId": "task-7", "attemptId": "attempt-7", "generation": 1}}
            order = []
            handle = mock.Mock()
            identity = {"pid": 301, "startedAt": 1339247086400000000,
                        "jobName": "Local\\HeyMyBuddy-00000000-0000-4000-8000-000000000007"}
            original = launch_protocol.fsync_json

            def write(path, value):
                order.append(path.name)
                original(path, value)

            windows_os = SimpleNamespace(name="nt", environ=dict(os.environ))
            with mock.patch.object(launch_protocol, "os", windows_os), \
                 mock.patch.object(launch_protocol, "launch_controller", return_value=handle) as controller, \
                 mock.patch.object(launch_protocol, "capture_process_identity", return_value=identity), \
                 mock.patch.object(launch_protocol, "transfer_job_to_child",
                                   side_effect=lambda *_args: order.append("transfer")) as transfer, \
                 mock.patch.object(launch_protocol, "fsync_json", side_effect=write):
                launched = launch_protocol.launch_execution(claim, state, "n" * 64)
            self.assertIs(launched, handle)
            self.assertEqual(order, ["spawn.intent", "execution-start.json", "spawn.marker", "transfer"])
            transfer.assert_called_once_with(handle.process, handle.job)
            job_name = controller.call_args.kwargs["job_name"]
            self.assertTrue(job_name.startswith("Local\\HeyMyBuddy-"))
            marker = json.loads((state / "attempts/task-7/attempt-7/spawn.marker").read_text(encoding="utf-8"))
            self.assertEqual(marker["processIdentity"], identity)
            startup = json.loads((launch_protocol.execution_directory(claim, state)
                                  / "execution-start.json").read_text(encoding="utf-8"))
            self.assertEqual(startup["jobName"], job_name)

    def test_spawn_marker_is_read_once_at_its_file_boundary(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            evidence = state / "attempts/task-9/attempt-9"
            evidence.mkdir(parents=True)
            binding = {"taskId": "task-9", "attemptId": "attempt-9", "generation": 1}
            self.assertIsNone(launch_protocol.read_spawn_marker(state, binding))
            windows = {"pid": 301, "startedAt": 1339247086400000000,
                       "jobName": "Local\\HeyMyBuddy-00000000-0000-4000-8000-000000000009"}
            launch_protocol.fsync_json(evidence / "spawn.marker", {**binding, "processIdentity": windows})
            self.assertEqual(launch_protocol.read_spawn_marker(state, binding), windows)
            for value in ({**binding, "processIdentity": windows, "extra": 1},
                          {**binding, "processIdentity": windows, "generation": 2},
                          {**binding, "processIdentity": {"pid": 301, "startedAt": 105, "jobName": None}}):
                with self.subTest(value=value):
                    launch_protocol.fsync_json(evidence / "spawn.marker", value)
                    with self.assertRaises(BoardError):
                        launch_protocol.read_spawn_marker(state, binding)
            launch_protocol.fsync_json(evidence / "spawn.marker", {**binding, "processIdentity": windows})
            material = {"claim": {"task": {"taskId": "task-9"},
                                  "attempt": {"attemptId": "attempt-9", "generation": 1}},
                        "stateDir": str(state)}
            self.assertEqual(launch_protocol.execution_root_identity(material), windows)
            (evidence / "spawn.marker").unlink()
            with self.assertRaises(BoardError) as caught:
                launch_protocol.execution_root_identity(material)
            self.assertEqual(caught.exception.code, "PROCESS_IDENTITY_UNKNOWN")

    def test_private_endpoint_authenticates_and_accepts_stop(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            claim = {"task": {"taskId": "task-1", "spec": {"adapter": "command"}},
                     "attempt": {"taskId": "task-1", "attemptId": "attempt-1", "generation": 1}}
            execution = Execution(claim, state, "a" * 64, client=object())
            native_path(execution.directory).mkdir(parents=True, mode=0o700)
            process_identity = self_identity_for_endpoint()
            execution.live.start(process_identity)
            try:
                endpoint_file = execution.directory / "execution-live.json"
                material = json.loads(native_path(endpoint_file).read_text(encoding="utf-8"))
                credential = launch_protocol.cc.server_credential()
                self.assertIsNotNone(credential)
                self.assertEqual(material["descriptor"]["endpointCredential"], credential.to_json())
                if os.name != "nt":
                    self.assertEqual(native_path(endpoint_file).stat().st_mode & 0o777, 0o600)
                client = read_execution_endpoint(execution.directory, execution.live.identity,
                    state_dir=state, process_identity=process_identity)
                self.assertIsNotNone(client)
                try:
                    client.connect()
                    self.assertIsNotNone(client.connection_observation)
                    response = client.stop("console", force=True)
                    self.assertEqual(response, {"accepted": True})
                    self.assertNotIn(material["token"], json.dumps(response))
                    self.assertTrue(execution.stop_requested())
                    self.assertEqual(client.observe(limit=1)["unavailable"], "source-unavailable")
                    native = mock.Mock()
                    native.capabilities_local.return_value = canonical_json({"inquiryDelivery": "unsupported"})
                    native.observe_local.return_value = canonical_json({"observed": True})
                    native.request_local.return_value = canonical_json({"status": "unavailable"})
                    execution.live.bind_native(native)
                    self.assertEqual(client.capabilities(), {"inquiryDelivery": "unsupported"})
                    self.assertEqual(client.observe(limit=1), {"observed": True})
                    request = LiveRequest(identity=execution.live.identity, request_id="request-1",
                        kind="inquiry", payload=InquiryPayload(question_id="question-1", question="Ready?"))
                    self.assertEqual(client.request(request, timeout_ms=3000), {"status": "unavailable"})
                    native.request_local.assert_called_once()
                    native.request.assert_not_called()
                    native.capabilities.assert_not_called()
                    native.observe.assert_not_called()
                    execution.live.unbind_native(native)
                finally:
                    client.close()
                rejected = {**client.identity.to_payload()}
                with self.assertRaises(BoardError) as refused:
                    execution.live.stop(canonical_json({"identity": rejected,
                        "instanceId": client.instance_id, "token": "0" * 64,
                        "initiator": "console", "graceDeadline": None, "force": False}))
                self.assertNotIn(material["token"], str(refused.exception))
                if os.name != "nt":
                    os.chmod(endpoint_file, 0o644)
                    reopened = read_execution_endpoint(execution.directory, execution.live.identity,
                        state_dir=state, process_identity=process_identity)
                    self.assertEqual((reopened.identity, reopened.token), (client.identity, material["token"]))
                    os.chmod(endpoint_file, 0o600)
                with self.assertRaises(BoardError):
                    read_execution_endpoint(execution.directory, execution.live.identity,
                        state_dir=state, process_identity={**process_identity, "pid": 1})
                with mock.patch.object(launch_protocol.cc, "reap_endpoint") as reap:
                    self.assertEqual(launch_protocol.cleanup_execution_endpoint(
                        execution.directory, process_identity, state_dir=state,
                        stopped_evidence="alive")["status"], "unverified")
                    reap.assert_not_called()
                    self.assertTrue(native_path(execution.directory / "execution-live.json").exists())
                status = "not-applicable" if os.name == "nt" else "reaped"
                with mock.patch.object(launch_protocol.cc, "reap_endpoint",
                                       return_value={"status": status}) as reap:
                    self.assertEqual(launch_protocol.cleanup_execution_endpoint(
                        execution.directory, process_identity, state_dir=state,
                        stopped_evidence="identity-absent")["status"],
                        status)
                    reap.assert_called_once()
                    self.assertFalse(native_path(execution.directory / "execution-live.json").exists())
            finally:
                execution.live.finish()

    def test_closing_one_proxy_does_not_stop_shared_physical_connection(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            claim = {"task": {"taskId": "task-shared", "spec": {"adapter": "command"}},
                     "attempt": {"taskId": "task-shared", "attemptId": "attempt-shared", "generation": 1}}
            execution = Execution(claim, state, "c" * 64, client=object())
            native_path(execution.directory).mkdir(parents=True, mode=0o700)
            process_identity = self_identity_for_endpoint()
            execution.live.start(process_identity)
            clients = []
            try:
                for _ in range(2):
                    client = read_execution_endpoint(execution.directory, execution.live.identity,
                        state_dir=state, process_identity=process_identity)
                    self.assertIsNotNone(client)
                    clients.append(client)
                    client.connect()
                first, second = clients
                observation = first.connection_observation
                self.assertEqual(observation.connection_id, second.connection_observation.connection_id)
                first.close()
                self.assertIsNone(observation.snapshot())
                self.assertEqual(second.observe(limit=1)["unavailable"], "source-unavailable")
                self.assertFalse(execution.stop_requested())
            finally:
                for client in clients:
                    client.close()
                execution.live.finish()

    def test_endpoint_material_survives_native_stop_while_receipt_waits_for_service(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            claim = {"task": {"taskId": "task-2", "spec": {"adapter": "command"}},
                     "attempt": {"taskId": "task-2", "attemptId": "attempt-2", "generation": 1}}
            offline = threading.Event()
            online = threading.Event()

            class Client:
                def submit_result(self, *_args):
                    offline.set()
                    if not online.is_set():
                        raise BoardError("SERVICE_UNAVAILABLE", "private service is offline")

            execution = Execution(claim, state, "b" * 64, client=Client())
            native_path(execution.directory).mkdir(parents=True, mode=0o700)
            process_identity = self_identity_for_endpoint()
            execution.live.start(process_identity)
            self.assertTrue(native_path(execution.directory / "execution-live.json").exists())
            receipt = execution.receipt(claim, {"status": "ok", "shutdownConfirmed": True},
                                        execution.directory)
            with mock.patch.object(execution, "execute", return_value=receipt):
                thread = threading.Thread(target=execution.run, daemon=True)
                thread.start()
                try:
                    self.assertTrue(offline.wait(5))
                    self.assertTrue(native_path(execution.directory / "execution-live.json").exists())
                    self.assertTrue(native_path(execution.directory / "result-receipt.json").exists())
                    client = read_execution_endpoint(execution.directory, execution.live.identity,
                        state_dir=state, process_identity=process_identity)
                    try:
                        client.connect()
                        self.assertIsNotNone(client.connection_observation)
                    finally:
                        client.close()
                    self.assertTrue(native_path(execution.directory / "execution-live.json").exists())
                finally:
                    online.set()
                    thread.join(10)
                    execution.live.finish()
            self.assertFalse(thread.is_alive())
            self.assertFalse(native_path(execution.directory / "execution-live.json").exists())
            self.assertFalse(native_path(execution.directory / "result-delivery.json").exists())
            self.assertTrue(native_path(execution.directory / "result-receipt.json").exists())
