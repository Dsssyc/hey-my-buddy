"""Fast isolated tests for the CLI blocking wait (no service, no dsh, no model).

``hey-my-buddy await`` is the only blocking CLI call. It may
issue ``status``/``events``/``watch``/``wait``/``result`` and consume one reminder
through ``wait``. It must never start, resume, retry or cancel a run.
"""
import json
import os
from pathlib import Path
import shlex
import threading
import unittest

import hey_my_buddy.cli.blocking as blocking
from hey_my_buddy.cli.blocking import (
    MAX_WAIT_SECONDS,
    WaitAbandoned,
    await_run,
    recovery_commands,
    validate_wait_seconds,
)
from hey_my_buddy.protocol.transport import ServiceError


class VirtualClock:
    def __init__(self, now: float = 1000.0):
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class FakeService:
    """Injectable stand-in for ``hey_my_buddy.protocol.transport.call_service``.

    Only observation operations await may use are implemented; ``wait`` may
    consume one reminder. Anything else fails the no-start/no-cancel contract.
    """

    READ_ONLY = frozenset({"status", "events", "watch", "wait", "result"})

    def __init__(self, clock, *, complete_after_waits=None, fail_waits=0, fail_status=0,
                 status_queue=None, stop_event=None, terminal_without_result=False,
                 listed=False, fail_result=0, result_response=None):
        self.clock = clock
        self.calls = []
        self.complete_after_waits = complete_after_waits
        self.fail_waits = fail_waits
        self.fail_status = fail_status
        self.status_queue = list(status_queue or [])
        self.stop_event = stop_event
        # True -> "reconciliation-needed"; a status string -> that terminal status, no persisted result.
        self.terminal_without_result = terminal_without_result
        self.listed = listed
        self.fail_result = fail_result
        self.result_response = result_response
        self.run = {
            "runId": "run-fixture", "requestId": "fixture", "status": "running", "revision": 1,
            "resultAvailable": False, "shutdownConfirmed": False, "createdAt": "t0", "updatedAt": "t0",
            "cwd": "/tmp", "logPaths": {"stdout": "/tmp/stdout.log", "stderr": "/tmp/stderr.log"},
        }

    def __call__(self, method, params, state_dir=None):
        assert method in self.READ_ONLY, f"await must stay read-only, but called {method!r}"
        self.calls.append(method)
        if method == "status":
            if "requestId" in params:
                if not self.listed or params["requestId"] != self.run["requestId"]:
                    raise ServiceError("NOT_FOUND", "Unknown requestId")
                return dict(self.run)
            if self.fail_status > 0:
                self.fail_status -= 1
                raise ServiceError("SERVICE_UNAVAILABLE", "fixture outage")
            if self.status_queue:
                return dict(self.status_queue.pop(0))
            return dict(self.run)
        if method == "wait":
            if self.fail_waits > 0:
                self.fail_waits -= 1
                raise ServiceError("SERVICE_UNAVAILABLE", "fixture outage")
            self.clock.advance(params["timeoutMs"] / 1000)
            if self.complete_after_waits is not None and self.calls.count("wait") >= self.complete_after_waits:
                self.run = {**self.run, "status": "completed", "resultAvailable": True,
                            "shutdownConfirmed": True, "revision": self.run["revision"] + 1}
                if "workflowState" in self.run:
                    self.run["workflowState"] = "delivered"
            elif self.terminal_without_result:
                status = self.terminal_without_result if isinstance(self.terminal_without_result, str) else "reconciliation-needed"
                self.run = {**self.run, "status": status, "revision": self.run["revision"] + 1}
            if self.stop_event is not None:
                self.stop_event.set()
            return dict(self.run)
        if method == "result":
            if self.fail_result > 0:
                self.fail_result -= 1
                raise ServiceError("RESULT_READ_FAILED", "fixture result read failure")
            if self.result_response is not None:
                return self.result_response(self.run) if callable(self.result_response) else self.result_response
            if not self.run["resultAvailable"]:
                raise ServiceError("NOT_READY", "no result yet")
            return {**self.run, "result": {"status": "ok", "finalText": "fixture result", "processState": {"shutdownConfirmed": True}}}
        raise AssertionError(f"unexpected method {method}")


class WaitWindowTests(unittest.TestCase):
    """The wait window is CLI-owned, explicit and bounded; nothing else is a deadline."""

    def test_host_await_keeps_waiting_while_lead_is_parked_on_owned_work(self):
        clock = VirtualClock()
        service = FakeService(clock, complete_after_waits=1)
        service.run.update(status="queued", workflowState="waiting-owned", resultAvailable=True,
                           shutdownConfirmed=True)
        envelope = await_run({"runId": "run-fixture", "waitSeconds": 60},
                             state_dir=Path(os.environ["HMB_STATE_DIR"]), service=service, clock=clock)
        self.assertEqual(service.calls.count("wait"), 1)
        self.assertEqual(envelope["workflowState"], "delivered")

    def test_wait_seconds_defaults_to_24h_and_is_bounded_by_it(self):
        clock = VirtualClock()
        service = FakeService(clock, listed=True, complete_after_waits=1)
        envelope = await_run({"requestId": "fixture"}, state_dir=Path(os.environ["HMB_STATE_DIR"]), service=service, clock=clock)
        self.assertEqual(envelope["waitSeconds"], MAX_WAIT_SECONDS)
        self.assertEqual(envelope["maxWaitSeconds"], MAX_WAIT_SECONDS)
        self.assertFalse(hasattr(blocking, "default_wait_seconds"))
        with self.assertRaises(ServiceError):
            validate_wait_seconds(MAX_WAIT_SECONDS + 1)
        with self.assertRaises(ServiceError):
            validate_wait_seconds(0)
        with self.assertRaises(ServiceError):
            validate_wait_seconds(True)

    def test_validation_rejects_bad_input_before_any_service_contact(self):
        def forbidden(*_args, **_kwargs):
            raise AssertionError("validation must not contact the service")

        cases = [
            ({"requestId": " "}, "INVALID_ARGUMENT"),
            ({"runId": ""}, "INVALID_ARGUMENT"),
            ({"requestId": "x" * 129}, "INVALID_ARGUMENT"),
            ({}, "INVALID_ARGUMENT"),
            ({"requestId": "fixture", "waitSeconds": 0}, "INVALID_ARGUMENT"),
            ({"requestId": "fixture", "waitSeconds": 99999}, "INVALID_ARGUMENT"),
            ({"requestId": "fixture", "surprise": True}, "INVALID_ARGUMENT"),
            ({"requestId": "fixture", "controlFile": "/tmp/x"}, "INVALID_ARGUMENT"),
        ]
        for params, code in cases:
            with self.subTest(params=params):
                with self.assertRaises(ServiceError) as failure:
                    await_run(params, state_dir=Path(os.environ["HMB_STATE_DIR"]), service=forbidden)
                self.assertEqual(failure.exception.code, code)

    def test_the_run_blocking_convenience_is_gone(self):
        # The old one-call start-and-wait path does not remain as a compatibility shim.
        self.assertFalse(hasattr(blocking, "run_blocking"))
        self.assertFalse(hasattr(blocking, "validate_run_params"))
        self.assertFalse(hasattr(blocking, "START_FIELDS"))
        self.assertFalse(hasattr(blocking, "SHUTDOWN_GRACE_SECONDS"))


class RecoveryCommandTests(unittest.TestCase):
    def test_recovery_commands_are_current_and_never_imply_an_implicit_start(self):
        commands = " ".join(recovery_commands("fixture", "run-fixture"))
        for current in ("hey-my-buddy status", "hey-my-buddy get", "hey-my-buddy await", "hey-my-buddy result"):
            self.assertIn(current, commands)
        for removed in ("buddy run", "buddy start", "buddy retry", "workflow-", "hey-my-buddy cancel '"):
            self.assertNotIn(removed, commands)

    def test_recovery_commands_round_trip_json_and_shell_quoting(self):
        """Quotes/apostrophes/unicode must survive shlex.split + json.loads in every suggested command."""
        tricky_request = 'req-"quoted"-\'apostrophe\'-ünïcode'

        # Without a runId only `await` accepts a requestId selector: the other
        # commands need a runId, so no invented one is suggested.
        request_only = recovery_commands(tricky_request, None)
        self.assertEqual(len(request_only), 1)
        self.assertEqual(shlex.split(request_only[0])[1], "await")
        for command in request_only:
            argv = shlex.split(command)
            self.assertEqual(argv[0], "hey-my-buddy")
            self.assertEqual(json.loads(argv[-1]), {"requestId": tricky_request})

        # With a known runId every command carries a parseable runId selector.
        with_run = recovery_commands(tricky_request, "run-fixture")
        self.assertEqual(
            [shlex.split(command)[1] for command in with_run],
            ["status", "get", "await", "result"],
        )
        for command in with_run:
            argv = shlex.split(command)
            self.assertEqual(argv[0], "hey-my-buddy")
            self.assertEqual(json.loads(argv[-1]), {"runId": "run-fixture"})

        # A runId with shell-active characters is quoted and parsed identically.
        tricky_run = 'run-"quoted"-\'apostrophe\'-ünïcode'
        for command in recovery_commands(tricky_request, tricky_run):
            argv = shlex.split(command)
            self.assertEqual(argv[0], "hey-my-buddy")
            self.assertEqual(json.loads(argv[-1]), {"runId": tricky_run})

        # The WAIT_ABANDONED envelope built by the CLI uses the same quoted commands.
        from hey_my_buddy.cli.main import _abandoned

        abandoned = WaitAbandoned(tricky_request, None)
        payload = _abandoned(abandoned, recovery_commands(abandoned.request_id, abandoned.run_id))
        argv = shlex.split(payload["recovery"]["commands"][0])
        self.assertEqual(json.loads(argv[-1]), {"requestId": tricky_request})


class AwaitTests(unittest.TestCase):
    def test_waiting_counts_use_the_latest_result_at_each_boundary(self):
        for boundary in ('awaiting-host', 'delivered'):
            for latest in ({'host': 1, 'review': 3}, None):
                with self.subTest(boundary=boundary, latest=latest):
                    clock = VirtualClock()

                    def result_response(run):
                        result = {key: value for key, value in run.items() if key != 'waitingForHost'}
                        if latest is not None:
                            result['waitingForHost'] = latest
                        return {**result, 'result': {'status': 'ok'}}

                    service = FakeService(clock, result_response=result_response)
                    service.run.update(status='completed', resultAvailable=True, shutdownConfirmed=True,
                                       workflowState=boundary, waitingForHost={'host': 1, 'review': 2})
                    response = await_run({'runId': 'run-fixture', 'waitSeconds': 1},
                                         Path(os.environ['HMB_STATE_DIR']), clock=clock, service=service)
                    self.assertEqual(response.get('waitingForHost'), latest)
                    if latest is None:
                        self.assertNotIn('waitingForHost', response)

    def test_await_resolves_an_existing_run_by_request_id_and_never_starts(self):
        clock = VirtualClock()
        service = FakeService(clock, listed=True, complete_after_waits=1)
        envelope = await_run({"requestId": "fixture"}, state_dir=Path(os.environ["HMB_STATE_DIR"]), service=service, clock=clock)
        self.assertEqual(envelope["outcome"], "completed")
        self.assertEqual(envelope["runId"], "run-fixture")
        self.assertEqual(envelope["result"]["finalText"], "fixture result")
        self.assertEqual(service.calls, ["status", "wait", "result"])

    def test_await_accepts_a_run_id_and_checks_a_mismatched_request(self):
        clock = VirtualClock()
        service = FakeService(clock, complete_after_waits=1)
        envelope = await_run({"runId": "run-fixture", "requestId": "fixture"}, state_dir=Path(os.environ["HMB_STATE_DIR"]), service=service, clock=clock)
        self.assertEqual(envelope["outcome"], "completed")
        self.assertEqual(service.calls[0], "status")
        with self.assertRaises(ServiceError) as failure:
            await_run({"runId": "run-fixture", "requestId": "other"}, state_dir=Path(os.environ["HMB_STATE_DIR"]), service=service, clock=clock)
        self.assertEqual(failure.exception.code, "CONFLICT")

    def test_await_never_starts_missing_work_and_names_current_commands(self):
        clock = VirtualClock()
        service = FakeService(clock)
        with self.assertRaises(ServiceError) as failure:
            await_run({"requestId": "missing"}, state_dir=Path(os.environ["HMB_STATE_DIR"]), service=service, clock=clock)
        self.assertEqual(failure.exception.code, "NOT_FOUND")
        message = str(failure.exception)
        self.assertIn("hey-my-buddy submit", message)
        self.assertNotIn("execution-submit", message)
        self.assertNotIn("buddy run", message)
        self.assertEqual(service.calls, ["status"])

    def test_await_timeout_is_a_wait_limit_and_never_cancels(self):
        clock = VirtualClock()
        service = FakeService(clock, listed=True)
        envelope = await_run({"requestId": "fixture", "waitSeconds": 1}, state_dir=Path(os.environ["HMB_STATE_DIR"]), service=service, clock=clock)
        self.assertEqual(envelope["outcome"], "wait-timeout")
        self.assertEqual(envelope["status"], "running")
        self.assertTrue(envelope["timedOut"])
        self.assertFalse(envelope["ok"])
        self.assertEqual(
            set(service.calls) - FakeService.READ_ONLY, set(),
            "a wait timeout must not start, retry or cancel anything",
        )
        self.assertIn("hey-my-buddy await", envelope["recovery"]["action"])
        self.assertIn("hey-my-buddy get", envelope["recovery"]["action"])
        self.assertNotIn("buddy run", envelope["recovery"]["action"])
        commands = " ".join(envelope["recovery"]["commands"])
        self.assertIn("hey-my-buddy status", commands)
        self.assertNotIn("buddy start", commands)

    def test_disconnect_or_stop_abandons_only_the_wait(self):
        clock = VirtualClock()
        stop = threading.Event()
        service = FakeService(clock, listed=True, stop_event=stop)
        with self.assertRaises(WaitAbandoned) as failure:
            await_run({"requestId": "fixture"}, state_dir=Path(os.environ["HMB_STATE_DIR"]), service=service, clock=clock, stop=stop)
        self.assertEqual(failure.exception.run_id, "run-fixture")
        self.assertEqual(failure.exception.request_id, "fixture")
        # list + one event wait; the durable run is never cancelled by losing the wait.
        self.assertEqual(service.calls, ["status", "wait"])


class ReconnectTests(unittest.TestCase):
    """A transient outage is recovered by re-reading the same run, never by replaying a submission."""

    def test_transient_wait_failure_reattaches_by_run_id_read_only(self):
        clock = VirtualClock()
        service = FakeService(clock, complete_after_waits=1, fail_waits=1)
        envelope = await_run({"runId": "run-fixture", "waitSeconds": 60}, state_dir=Path(os.environ["HMB_STATE_DIR"]), service=service, clock=clock)
        self.assertEqual(envelope["outcome"], "completed")
        self.assertEqual(envelope["reconnects"], 1)
        self.assertEqual(envelope["runId"], "run-fixture")
        self.assertEqual(service.calls, ["status", "wait", "status", "wait", "result"])
        self.assertEqual(
            set(service.calls) - FakeService.READ_ONLY, set(),
            "recovery must re-read the same run, not submit it again",
        )

    def test_reattach_mismatch_is_reported_not_hidden(self):
        clock = VirtualClock()
        service = FakeService(
            clock, listed=True, fail_waits=1, complete_after_waits=99,
            status_queue=[{"runId": "run-someone-else", "requestId": "other", "status": "running", "revision": 1}],
        )
        envelope = await_run({"requestId": "fixture", "waitSeconds": 60}, state_dir=Path(os.environ["HMB_STATE_DIR"]), service=service, clock=clock)
        self.assertEqual(envelope["outcome"], "unavailable")
        self.assertEqual(envelope["error"]["code"], "RECOVERY_MISMATCH")
        self.assertEqual(envelope["reconnects"], 1)

    def test_failed_reattach_reports_unavailable_with_recovery(self):
        clock = VirtualClock()
        service = FakeService(clock, listed=True, fail_waits=1, fail_status=1)
        envelope = await_run({"requestId": "fixture", "waitSeconds": 60}, state_dir=Path(os.environ["HMB_STATE_DIR"]), service=service, clock=clock)
        self.assertEqual(envelope["outcome"], "unavailable")
        self.assertEqual(envelope["error"]["code"], "SERVICE_UNAVAILABLE")
        self.assertEqual(envelope["recovery"]["runId"], "run-fixture")
        self.assertFalse(envelope["ok"])
        self.assertEqual(service.calls, ["status", "wait", "status"])

    def test_exhausted_reconnects_return_unavailable_bounded(self):
        clock = VirtualClock()
        service = FakeService(clock, fail_waits=99)
        envelope = await_run({"runId": "run-fixture", "waitSeconds": 60}, state_dir=Path(os.environ["HMB_STATE_DIR"]), service=service, clock=clock)
        self.assertEqual(envelope["outcome"], "unavailable")
        self.assertEqual(envelope["error"]["code"], "SERVICE_UNAVAILABLE")
        self.assertEqual(envelope["recovery"]["runId"], "run-fixture")
        self.assertFalse(envelope["ok"])
        # Bounded: exactly one initial wait plus MAX_RECONNECTS recovered waits,
        # each followed by one read-only re-attach.
        self.assertEqual(service.calls.count("wait"), 4)
        self.assertEqual(service.calls.count("status"), 4)
        self.assertEqual(service.calls[0], "status")

    def test_concluded_without_an_attempt_result_finishes_the_wait(self):
        clock = VirtualClock()
        service = FakeService(clock, listed=True, terminal_without_result="concluded")
        envelope = await_run({"requestId": "fixture", "waitSeconds": 60},
                             state_dir=Path(os.environ["HMB_STATE_DIR"]), service=service, clock=clock)
        self.assertEqual(envelope["status"], "concluded")
        self.assertFalse(envelope["resultAvailable"])
        self.assertEqual(service.calls.count("wait"), 1)

    def test_terminal_without_persisted_result_is_reported(self):
        clock = VirtualClock()
        service = FakeService(clock, complete_after_waits=None, fail_waits=0, terminal_without_result=True)
        envelope = await_run({"runId": "run-fixture", "waitSeconds": 60}, state_dir=Path(os.environ["HMB_STATE_DIR"]), service=service, clock=clock)
        self.assertEqual(envelope["outcome"], "reconciliation-needed")
        self.assertFalse(envelope["resultAvailable"])
        self.assertIsNone(envelope["result"])
        self.assertIn("hey-my-buddy result", envelope["recovery"]["reason"])


class ResultTruthTests(unittest.TestCase):
    """A completed run never claims success unless its promised result is delivered.

    ``status`` keeps the underlying execution status (the worker may really have
    completed), while ``ok``/``outcome`` must stay honest about result delivery.
    """

    def test_completed_without_persisted_result_cannot_claim_success(self):
        clock = VirtualClock()
        service = FakeService(clock, listed=True, terminal_without_result="completed")
        envelope = await_run({"requestId": "fixture", "waitSeconds": 60}, state_dir=Path(os.environ["HMB_STATE_DIR"]), service=service, clock=clock)
        # Execution status and identity are preserved...
        self.assertEqual(envelope["status"], "completed")
        self.assertEqual(envelope["runId"], "run-fixture")
        self.assertEqual(envelope["requestId"], "fixture")
        self.assertIsNotNone(envelope["recovery"])
        # ...but this is not a successful complete result.
        self.assertFalse(envelope["ok"])
        self.assertEqual(envelope["outcome"], "completed-no-result")
        self.assertNotEqual(envelope["outcome"], "completed")
        self.assertFalse(envelope["resultAvailable"])
        self.assertFalse(envelope["resultDelivered"])
        self.assertIsNone(envelope["result"])
        self.assertEqual(envelope["error"]["code"], "RESULT_NOT_AVAILABLE")
        self.assertIn("hey-my-buddy result", envelope["recovery"]["reason"])
        self.assertEqual(envelope["recovery"]["runId"], "run-fixture")
        self.assertEqual(service.calls, ["status", "wait"])

    def test_result_read_service_error_cannot_claim_success(self):
        clock = VirtualClock()
        service = FakeService(clock, listed=True, complete_after_waits=1, fail_result=1)
        envelope = await_run({"requestId": "fixture", "waitSeconds": 60}, state_dir=Path(os.environ["HMB_STATE_DIR"]), service=service, clock=clock)
        self.assertEqual(envelope["status"], "completed")
        self.assertFalse(envelope["ok"])
        self.assertEqual(envelope["outcome"], "completed-no-result")
        # The engine claims a persisted result, but this envelope could not deliver it.
        self.assertTrue(envelope["resultAvailable"])
        self.assertFalse(envelope["resultDelivered"])
        self.assertIsNone(envelope["result"])
        self.assertEqual(envelope["error"]["code"], "RESULT_READ_FAILED")
        self.assertEqual(envelope["runId"], "run-fixture")
        self.assertIn("do not relaunch", envelope["recovery"]["reason"])

    def test_malformed_or_missing_result_response_cannot_claim_success(self):
        cases = [
            ("non-dict-response", "not-a-dict", "INVALID_RESPONSE"),
            ("missing-result-key", {"runId": "run-fixture", "status": "completed"}, "RESULT_MISSING"),
            ("null-result", {"runId": "run-fixture", "status": "completed", "result": None}, "RESULT_MISSING"),
            ("empty-result", {"runId": "run-fixture", "status": "completed", "result": {}}, "INVALID_RESPONSE"),
            ("non-dict-result", {"runId": "run-fixture", "status": "completed", "result": "oops"}, "INVALID_RESPONSE"),
            ("wrong-run", {"runId": "run-other", "status": "completed", "result": {"finalText": "other"}}, "RESULT_MISMATCH"),
        ]
        for name, response, code in cases:
            with self.subTest(response=name):
                clock = VirtualClock()
                service = FakeService(clock, listed=True, complete_after_waits=1, result_response=response)
                envelope = await_run({"requestId": "fixture", "waitSeconds": 60}, state_dir=Path(os.environ["HMB_STATE_DIR"]), service=service, clock=clock)
                # The failure is attributed to this run even when the response was unusable.
                self.assertEqual(envelope["runId"], "run-fixture")
                self.assertEqual(envelope["requestId"], "fixture")
                self.assertEqual(envelope["status"], "completed")
                self.assertFalse(envelope["ok"])
                self.assertEqual(envelope["outcome"], "completed-no-result")
                self.assertFalse(envelope["resultDelivered"])
                self.assertIsNone(envelope["result"])
                self.assertEqual(envelope["error"]["code"], code)
                self.assertIsNotNone(envelope["recovery"])
                self.assertEqual(envelope["recovery"]["runId"], "run-fixture")

    def test_successful_result_control_keeps_completed_semantics(self):
        clock = VirtualClock()
        service = FakeService(clock, listed=True, complete_after_waits=1)
        envelope = await_run({"requestId": "fixture", "waitSeconds": 60}, state_dir=Path(os.environ["HMB_STATE_DIR"]), service=service, clock=clock)
        self.assertTrue(envelope["ok"])
        self.assertEqual(envelope["outcome"], "completed")
        self.assertEqual(envelope["status"], "completed")
        self.assertTrue(envelope["resultAvailable"])
        self.assertTrue(envelope["resultDelivered"])
        self.assertEqual(envelope["result"]["finalText"], "fixture result")
        self.assertIsNone(envelope["error"])
        self.assertIsNone(envelope["recovery"])

    def test_failed_terminal_without_result_stays_honest(self):
        clock = VirtualClock()
        service = FakeService(clock, listed=True, terminal_without_result="failed")
        envelope = await_run({"requestId": "fixture", "waitSeconds": 60}, state_dir=Path(os.environ["HMB_STATE_DIR"]), service=service, clock=clock)
        self.assertEqual(envelope["status"], "failed")
        self.assertEqual(envelope["outcome"], "failed")
        self.assertFalse(envelope["ok"])
        self.assertIsNone(envelope["result"])
        self.assertFalse(envelope["resultDelivered"])
        self.assertEqual(envelope["error"]["code"], "RESULT_NOT_AVAILABLE")
        self.assertIsNotNone(envelope["recovery"])


class MicroTaskBoundaryTests(unittest.TestCase):
    def test_routing_boundary_without_a_turn_has_actionable_resume_commands(self):
        clock = VirtualClock()
        service = FakeService(clock)
        service.run.update(
            status="queued", workflowState="awaiting-host", resultAvailable=False,
            workflow={"revision": 3, "activeRequestId": "route-boundary", "requestKind": "attention",
                      "requestSummary": "Configure a selector", "requestRouting": True,
                      "requestTargetRunId": "authorized-child"},
        )
        envelope = await_run({"runId": "run-fixture"}, state_dir=Path(os.environ["HMB_STATE_DIR"]), service=service, clock=clock)
        self.assertEqual(envelope["outcome"], "waiting-host")
        self.assertFalse(envelope["microTaskComplete"])
        self.assertIsNone(envelope["turn"])
        self.assertIsNone(envelope["error"])
        self.assertNotIn("result", service.calls)
        commands = [shlex.split(command) for command in envelope["nextCommands"]]
        self.assertTrue(all(command[:2] == ["hey-my-buddy", "continue"] for command in commands))
        params = [json.loads(command[2]) for command in commands]
        self.assertEqual({item["targetRunId"] for item in params}, {"authorized-child"})
        self.assertTrue(params[0]["reroute"])
        self.assertEqual(set(params[1]["buddy"]), {"harness", "provider", "model", "effort"})

    """A structured yield is a Host decision boundary, never a fake completion."""

    def _boundary_service(self):
        clock = VirtualClock()
        service = FakeService(clock, complete_after_waits=99)
        turn = {
            "turnId": "turn-1",
            "resumeMode": "initial",
            "disposition": "assistance",
            "summary": "need a Host decision",
            "request": {"requestId": "req-1", "kind": "assistance", "summary": "review the approach"},
        }
        service.run = {
            **service.run,
            "status": "queued",
            "revision": 7,
            "resultAvailable": True,
            "workflowState": "awaiting-host",
            "awaitingHost": True,
            "workflow": {
                "state": "awaiting-host",
                "awaitingHost": True,
                "revision": 7,
                "activeRequestId": "req-1",
                "requestKind": "assistance",
                "requestSummary": "review the approach",
            },
        }
        service.result_response = lambda run: {
            **run,
            "result": {"status": "ok", "turn": turn},
            "resultMeta": {"status": "ok", "shutdownConfirmed": True},
        }
        return service, clock

    def test_await_returns_the_structured_boundary_immediately(self):
        service, clock = self._boundary_service()
        envelope = await_run({"runId": "run-fixture", "waitSeconds": 60}, state_dir=Path(os.environ["HMB_STATE_DIR"]), service=service, clock=clock)
        self.assertEqual(envelope["outcome"], "waiting-host")
        self.assertFalse(envelope["ok"])
        self.assertFalse(envelope["microTaskComplete"])
        self.assertEqual(envelope["turn"]["turnId"], "turn-1")
        self.assertEqual(envelope["request"]["requestId"], "req-1")
        self.assertEqual(envelope["workflowState"], "awaiting-host")
        self.assertEqual(service.calls.count("wait"), 0, "a Host boundary must not block the wait")

    def test_next_commands_use_current_names_and_a_named_control_file(self):
        service, clock = self._boundary_service()
        envelope = await_run({"runId": "run-fixture", "waitSeconds": 60}, state_dir=Path(os.environ["HMB_STATE_DIR"]), service=service, clock=clock)
        commands = envelope["nextCommands"]
        self.assertEqual([shlex.split(command)[1] for command in commands], ["decide", "decide", "continue"])
        for command in commands:
            params = json.loads(shlex.split(command)[-1])
            self.assertEqual(params["controlFile"], "<saved-controlFile>")
        # The retired CLI aliases never appear in a suggested command.
        joined = " ".join(commands)
        self.assertNotIn("workflow-decide", joined)
        self.assertNotIn("workflow-continue", joined)
        self.assertNotIn("buddy run", joined)

    def test_waiting_follows_authorized_helpers_to_the_next_completion(self):
        clock = VirtualClock()
        service = FakeService(clock, complete_after_waits=1)
        service.run = {
            **service.run,
            "status": "queued",
            "revision": 3,
            "resultAvailable": True,
            "workflowState": "waiting-helpers",
            "awaitingHost": False,
        }
        envelope = await_run({"runId": "run-fixture", "waitSeconds": 60}, state_dir=Path(os.environ["HMB_STATE_DIR"]), service=service, clock=clock)
        # The stale turn result of the previous attempt never becomes a fake success;
        # the wait follows the same durable task until it really completes.
        self.assertEqual(envelope["outcome"], "completed")
        self.assertTrue(envelope["ok"])
        self.assertTrue(envelope["resultDelivered"])
        self.assertGreaterEqual(service.calls.count("wait"), 1)


class CompactMicroTaskEnvelopeTests(unittest.TestCase):
    def test_micro_task_final_envelope_is_compact_and_points_at_the_full_result(self):
        clock = VirtualClock()
        service = FakeService(clock, complete_after_waits=1)
        huge = "x" * 200_000
        service.run = {
            **service.run,
            "status": "queued",
            "resultAvailable": True,
            "workflowState": "executing",
            "awaitingHost": False,
        }
        service.result_response = lambda run: {
            **run,
            "status": "completed",
            "result": {
                "status": "ok",
                "finalText": huge,
                "logPaths": {"stdout": "/tmp/out", "stderr": "/tmp/err"},
                "processState": {"shutdownConfirmed": True},
                "turn": {
                    "turnId": "turn-1",
                    "resumeMode": "initial",
                    "sessionId": "sess-1",
                    "outcome": {"disposition": "completed", "summary": "done", "remaining": []},
                    "provenance": {"tool": "hey_my_buddy_finish_turn"},
                },
                "workspaceSeal": {
                    "snapshotSha256": "a" * 64,
                    "manifestSha256": "b" * 64,
                    "commit": "c" * 40,
                    "diffPath": "/tmp/output.patch",
                },
            },
            "resultMeta": {"status": "ok", "shutdownConfirmed": True},
        }
        envelope = await_run({"runId": "run-fixture", "waitSeconds": 60}, state_dir=Path(os.environ["HMB_STATE_DIR"]), service=service, clock=clock)
        self.assertEqual(envelope["outcome"], "completed")
        self.assertTrue(envelope["ok"])
        self.assertTrue(envelope["resultCompact"])
        self.assertLess(len(json.dumps(envelope)), 8000, "the micro-task envelope must stay bounded")
        self.assertEqual(envelope["turn"]["turnId"], "turn-1")
        self.assertEqual(envelope["artifacts"][0]["commit"], "c" * 40)
        self.assertIn("hey-my-buddy result", envelope["resultCommand"])
        self.assertTrue(envelope["result"]["finalTextTruncated"])

    def test_execution_envelope_delivers_the_full_result(self):
        clock = VirtualClock()
        service = FakeService(clock, complete_after_waits=1)
        envelope = await_run({"runId": "run-fixture", "waitSeconds": 60}, state_dir=Path(os.environ["HMB_STATE_DIR"]), service=service, clock=clock)
        self.assertEqual(envelope["outcome"], "completed")
        self.assertNotIn("resultCompact", envelope)
        self.assertEqual(envelope["result"]["finalText"], "fixture result")

    def test_scoped_await_waits_on_owned_run_events_without_addressing_internal_executions(self):
        clock = VirtualClock()
        run = {"runId": "owned", "requestId": "owned-request", "status": "running", "workflowState": "executing",
               "workflowShutdown": {"selfConfirmed": False, "descendantsConfirmed": False,
                                    "unconfirmedRunIds": ["internal-router"]}, "revision": 1, "resultAvailable": False}
        watched = []
        def service(method, params, state_dir):
            self.assertEqual(params["runId"], "owned")
            if method == "status":
                return dict(run)
            if method == "events":
                return {"head": 10}
            if method == "watch":
                watched.append(params["runId"])
                run.update(status="cancelled", workflowState="cancelled",
                           workflowShutdown={"selfConfirmed": True, "descendantsConfirmed": True, "unconfirmedRunIds": []})
                return {"head": 11}
            self.fail("Unexpected operation: " + method)
        result = await_run({"runId": "owned", "waitSeconds": 1}, state_dir=Path(os.environ["HMB_STATE_DIR"]),
                           service=service, clock=clock, scoped=True)
        self.assertEqual(watched, ["owned"])
        self.assertEqual(result["outcome"], "cancelled")


class ReminderReturnTests(unittest.TestCase):
    def test_lost_reminder_reply_recovers_with_the_same_delivery_id(self):
        clock = VirtualClock()
        service = FakeService(clock)
        service.run.update(workflowState="executing", attemptState="executing")
        first_wait_id = None
        original_call = service.__call__

        def call(method, params, state_dir=None):
            nonlocal first_wait_id
            if method == "wait":
                self.assertTrue(params["consumeReminders"])
                service.calls.append(method)
                if first_wait_id is None:
                    first_wait_id = params["reminderWaitId"]
                    raise ServiceError("SERVICE_UNAVAILABLE", "reply lost after durable consumption")
                self.assertEqual(params["reminderWaitId"], first_wait_id)
                return {**service.run, "reminder": {
                    "reminderId": 10, "kind": "expected-duration", "elapsedSeconds": 1800.0,
                    "lastNativeEventAt": None, "toolInProgress": False,
                    "createdAt": "2026-01-01T00:30:00Z", "expectedMultiple": 1,
                }}
            return original_call(method, params, state_dir)

        result = await_run({"runId": "run-fixture", "waitSeconds": 60},
                           state_dir=Path(os.environ["HMB_STATE_DIR"]), service=call, clock=clock)
        self.assertEqual(result["outcome"], "expected-duration")
        self.assertEqual(result["reminder"]["reminderId"], 10)
        self.assertEqual(result["reconnects"], 1)
        self.assertEqual(service.calls, ["status", "wait", "status", "wait"])

    def test_active_micro_task_with_unconfirmed_self_waits_for_a_reminder(self):
        clock = VirtualClock()
        service = FakeService(clock)
        service.run.update(
            workflowState="executing", attemptState="executing",
            workflowShutdown={"selfConfirmed": False, "descendantsConfirmed": False,
                              "unconfirmedRunIds": ["run-fixture"]},
        )
        original_call = service.__call__

        def call(method, params, state_dir=None):
            if method in ("events", "watch"):
                self.fail("an active attempt must use task_wait so the reminder can wake await")
            if method == "wait":
                self.assertTrue(params["consumeReminders"])
                service.calls.append(method)
                clock.advance(1)
                return {**service.run, "reminder": {
                    "reminderId": 6, "kind": "native-silence", "elapsedSeconds": 901.0,
                    "lastNativeEventAt": None, "toolInProgress": False,
                    "createdAt": "2026-01-01T00:15:01Z", "expectedMultiple": None,
                }}
            return original_call(method, params, state_dir)

        envelope = await_run({"runId": "run-fixture", "waitSeconds": 60},
                             state_dir=Path(os.environ["HMB_STATE_DIR"]), service=call, clock=clock)
        self.assertEqual(envelope["outcome"], "native-silence")
        self.assertEqual(envelope["waitedSeconds"], 1.0)
        self.assertEqual(service.calls, ["status", "wait"])

    def test_pending_reminders_return_once_per_await_before_any_blocking_wait(self):
        clock = VirtualClock()
        service = FakeService(clock)
        service.run.update(workflowState="executing", attemptState="executing")
        pending = [
            {"reminderId": 7, "kind": "expected-duration", "elapsedSeconds": 12.0,
             "lastNativeEventAt": None, "toolInProgress": False,
             "createdAt": "2026-01-01T00:00:12Z", "expectedMultiple": 1},
            {"reminderId": 8, "kind": "native-silence", "elapsedSeconds": 15.0,
             "lastNativeEventAt": "2026-01-01T00:00:01Z", "toolInProgress": True,
             "createdAt": "2026-01-01T00:00:15Z", "expectedMultiple": None},
        ]
        original_call = service.__call__

        def call(method, params, state_dir=None):
            if method == "wait" and pending:
                self.assertGreater(params["timeoutMs"], 0)
                self.assertTrue(params["consumeReminders"])
                service.calls.append(method)
                return {**service.run, "reminder": pending.pop(0)}
            return original_call(method, params, state_dir)

        first = await_run({"runId": "run-fixture", "waitSeconds": 60},
                          state_dir=Path(os.environ["HMB_STATE_DIR"]), service=call, clock=clock)
        second = await_run({"runId": "run-fixture", "waitSeconds": 60},
                           state_dir=Path(os.environ["HMB_STATE_DIR"]), service=call, clock=clock)
        self.assertEqual([first["outcome"], second["outcome"]], ["expected-duration", "native-silence"])
        self.assertEqual([first["reminder"]["reminderId"], second["reminder"]["reminderId"]], [7, 8])
        self.assertEqual(second["reminder"]["lastNativeEventAt"], "2026-01-01T00:00:01Z")
        self.assertTrue(second["reminder"]["toolInProgress"])
        for envelope in (first, second):
            self.assertFalse(envelope["ok"])
            self.assertFalse(envelope["microTaskComplete"])
            self.assertFalse(envelope["timedOut"])
            self.assertEqual(envelope["status"], "running")
            self.assertEqual(envelope["recovery"]["runId"], "run-fixture")
            self.assertEqual(envelope["waitedSeconds"], 0.0)
        self.assertEqual(service.calls, ["status", "wait", "status", "wait"])

    def test_new_reminder_ends_a_blocking_wait_without_stopping_the_run(self):
        clock = VirtualClock()
        service = FakeService(clock)
        service.run.update(workflowState="executing", attemptState="executing")
        original_call = service.__call__

        def call(method, params, state_dir=None):
            if method == "wait" and params["timeoutMs"] > 0:
                self.assertTrue(params["consumeReminders"])
                service.calls.append(method)
                clock.advance(2)
                return {**service.run, "reminder": {
                    "reminderId": 9, "kind": "expected-duration", "elapsedSeconds": 1800.0,
                    "lastNativeEventAt": None, "toolInProgress": False,
                    "createdAt": "2026-01-01T00:30:00Z", "expectedMultiple": 1,
                }}
            return original_call(method, params, state_dir)

        result = await_run({"runId": "run-fixture", "waitSeconds": 60},
                           state_dir=Path(os.environ["HMB_STATE_DIR"]), service=call, clock=clock)
        self.assertEqual(result["outcome"], "expected-duration")
        self.assertEqual(result["waitedSeconds"], 2.0)
        self.assertEqual(service.calls, ["status", "wait"])


class ResultPathCountTests(unittest.TestCase):
    def test_await_preserves_service_path_totals(self):
        patch = {"path": "/private/cumulative.patch", "changedPaths": [f"file-{index}" for index in range(32)],
                 "changedPathsCount": 2000, "changedPathsTruncated": True}
        full = {"workflowState": "delivered", "result": {"workspaceSeal": {"cumulativePatch": patch}}}
        view = blocking._micro_task_result_view(full, "run")
        self.assertEqual(view["artifacts"][0]["cumulativePatch"], patch)


if __name__ == "__main__":
    unittest.main()
