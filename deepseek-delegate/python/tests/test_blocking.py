"""Fast isolated tests for the CLI blocking delegation contract (no service, no dsh, no model)."""
import json
import os
import shlex
import threading
import unittest
from unittest import mock

from buddy.blocking import (
    MAX_WAIT_SECONDS,
    SHUTDOWN_GRACE_SECONDS,
    WaitAbandoned,
    await_run,
    default_wait_seconds,
    recovery_commands,
    run_blocking,
    validate_run_params,
    validate_wait_seconds,
)
from buddy.transport import ServiceError


class VirtualClock:
    def __init__(self, now: float = 1000.0):
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class FakeService:
    """Injectable stand-in for buddy.transport.call_service."""

    def __init__(self, clock, *, complete_after_waits=None, fail_waits=0, stop_event=None,
                 terminal_without_result=False, recover_to=None, listed=False, fail_status=False,
                 fail_result=0, result_response=None):
        self.clock = clock
        self.calls = []
        self.start_count = 0
        self.complete_after_waits = complete_after_waits
        self.fail_waits = fail_waits
        self.stop_event = stop_event
        # True -> "reconciliation-needed"; a status string -> that terminal status, no persisted result.
        self.terminal_without_result = terminal_without_result
        self.recover_to = recover_to
        self.listed = listed
        self.fail_status = fail_status
        self.fail_result = fail_result
        self.result_response = result_response
        self.run = {
            "runId": "run-fixture", "requestId": "fixture", "status": "running", "revision": 1,
            "resultAvailable": False, "shutdownConfirmed": False, "createdAt": "t0", "updatedAt": "t0",
            "cwd": "/tmp", "logPaths": {"stdout": "/tmp/stdout.log", "stderr": "/tmp/stderr.log"},
        }

    def __call__(self, method, params, state_dir=None):
        self.calls.append(method)
        if method == "start":
            self.start_count += 1
            if self.recover_to and self.start_count > 1:
                return {**self.run, "runId": self.recover_to, "requestId": "other"}
            return dict(self.run)
        if method == "list":
            runs = [dict(self.run)] if self.listed else []
            return {"runs": runs, "total": len(runs)}
        if method == "status":
            if self.fail_status:
                raise ServiceError("NOT_FOUND", "Unknown runId")
            return dict(self.run)
        if method == "wait":
            if self.fail_waits > 0:
                self.fail_waits -= 1
                raise ServiceError("SERVICE_UNAVAILABLE", "fixture outage")
            self.clock.advance(params["timeoutMs"] / 1000)
            if self.complete_after_waits is not None and self.calls.count("wait") >= self.complete_after_waits:
                self.run = {**self.run, "status": "completed", "resultAvailable": True,
                            "shutdownConfirmed": True, "revision": self.run["revision"] + 1}
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


BASE = {"requestId": "fixture", "task": "do the thing", "cwd": "/tmp", "workspace": False}


class WaitWindowTests(unittest.TestCase):
    """The wait window is CLI-owned: runner deadline + grace, capped by the 24 h maximum."""

    def test_default_window_covers_the_runner_deadline(self):
        self.assertEqual(default_wait_seconds(1800), 1800 + SHUTDOWN_GRACE_SECONDS)
        self.assertEqual(default_wait_seconds(7200), 7200 + SHUTDOWN_GRACE_SECONDS)
        # The 24 h CLI wait maximum still bounds the window.
        self.assertEqual(default_wait_seconds(86400), MAX_WAIT_SECONDS)

    def test_no_mcp_derived_budget_cap_or_env_coupling_remains(self):
        # A multi-hour run must get a covering window, not the old ~55 minute cap,
        # and the removed MCP wait-budget variable must have no effect at all.
        for poisoned in ("3300", "60", "not-a-number", ""):
            with self.subTest(env=poisoned), mock.patch.dict(os.environ, {"BUDDY_TOOL_WAIT_BUDGET_SECONDS": poisoned}):
                _, wait_seconds, timeout = validate_run_params({**BASE, "timeoutSeconds": 28800})
                self.assertEqual((timeout, wait_seconds), (28800, 28800 + SHUTDOWN_GRACE_SECONDS))
        _, wait_seconds, _ = validate_run_params(BASE)
        self.assertEqual(wait_seconds, 1800 + SHUTDOWN_GRACE_SECONDS)
        # ...while a multi-hour run gets a window far beyond the old ~55 minute cap.
        _, long_wait, _ = validate_run_params({**BASE, "timeoutSeconds": 28800})
        self.assertGreater(long_wait, 3300)

    def test_explicit_wait_seconds_is_supported_and_bounded_by_24h(self):
        _, wait_seconds, _ = validate_run_params({**BASE, "timeoutSeconds": 28800, "waitSeconds": 30})
        self.assertEqual(wait_seconds, 30)
        _, wait_seconds, _ = validate_run_params({**BASE, "waitSeconds": MAX_WAIT_SECONDS})
        self.assertEqual(wait_seconds, MAX_WAIT_SECONDS)
        with self.assertRaises(ServiceError):
            validate_wait_seconds(MAX_WAIT_SECONDS + 1)
        with self.assertRaises(ServiceError):
            validate_wait_seconds(0)
        with self.assertRaises(ServiceError):
            validate_wait_seconds(True)

    def test_execution_deadline_is_separate_and_unchanged(self):
        start, wait_seconds, timeout = validate_run_params({**BASE, "timeoutSeconds": 7200})
        self.assertEqual((timeout, start["timeoutSeconds"]), (7200, 7200))
        self.assertEqual(wait_seconds, 7260)
        start, _, timeout = validate_run_params(BASE)
        self.assertEqual((timeout, start.get("timeoutSeconds")), (1800, None))
        self.assertEqual(start, {"requestId": "fixture", "task": "do the thing", "cwd": "/tmp", "workspace": False})

    def test_validation_rejects_bad_input_before_any_service_contact(self):
        def forbidden(*_args, **_kwargs):
            raise AssertionError("validation must not contact the service")

        cases = [
            ({**BASE, "requestId": " "}, "INVALID_ARGUMENT"),
            ({**BASE, "task": ""}, "INVALID_ARGUMENT"),
            ({**BASE, "cwd": "relative/path"}, "INVALID_ARGUMENT"),
            ({**BASE, "timeoutSeconds": 5}, "INVALID_ARGUMENT"),
            ({**BASE, "timeoutSeconds": 86401}, "INVALID_ARGUMENT"),
            ({**BASE, "workspace": "yes"}, "INVALID_ARGUMENT"),
            ({**BASE, "waitSeconds": 0}, "INVALID_ARGUMENT"),
            ({**BASE, "waitSeconds": 99999}, "INVALID_ARGUMENT"),
            ({**BASE, "surprise": True}, "INVALID_ARGUMENT"),
            ({**BASE, "model": "x" * 300}, "INVALID_ARGUMENT"),
        ]
        for params, code in cases:
            with self.subTest(params=params):
                with self.assertRaises(ServiceError) as failure:
                    run_blocking(params, service=forbidden)
                self.assertEqual(failure.exception.code, code)

    def test_recovery_envelopes_name_real_cli_commands_and_run_ids(self):
        clock = VirtualClock()
        service = FakeService(clock)
        envelope = run_blocking({**BASE, "timeoutSeconds": 7200, "waitSeconds": 1}, service=service, clock=clock)
        commands = " ".join(envelope["recovery"]["commands"])
        self.assertIn("buddy status", commands)
        self.assertIn("buddy await", commands)
        self.assertIn("buddy result", commands)
        self.assertIn("buddy cancel", commands)
        self.assertIn('"runId":"run-fixture"', commands)
        for removed_tool in ("buddy_run", "buddy_status", "buddy_result", "buddy_cancel", "buddy_acknowledge"):
            self.assertNotIn(removed_tool, commands)
            self.assertNotIn(removed_tool, envelope["limitation"])
            self.assertNotIn(removed_tool, envelope["recovery"]["action"])

    def test_recovery_commands_round_trip_json_and_shell_quoting(self):
        """Quotes/apostrophes/unicode must survive shlex.split + json.loads in every suggested command."""
        tricky_request = 'req-"quoted"-\'apostrophe\'-ünïcode'

        # Without a runId only `await` accepts a requestId selector: status/result/cancel need a runId.
        request_only = recovery_commands(tricky_request, None)
        self.assertEqual(len(request_only), 1)
        self.assertEqual(shlex.split(request_only[0])[1], "await")
        for command in request_only:
            argv = shlex.split(command)
            self.assertEqual(argv[0], "buddy")
            self.assertEqual(json.loads(argv[-1]), {"requestId": tricky_request})

        # With a known runId every command carries a parseable runId selector.
        clock = VirtualClock()
        with_run = run_blocking({**BASE, "waitSeconds": 1}, service=FakeService(clock), clock=clock)["recovery"]
        self.assertEqual(
            [shlex.split(command)[1] for command in with_run["commands"]],
            ["status", "await", "result", "cancel"],
        )
        for command in with_run["commands"]:
            argv = shlex.split(command)
            self.assertEqual(argv[0], "buddy")
            self.assertEqual(json.loads(argv[-1]), {"runId": "run-fixture"})

        # A runId with shell-active characters is quoted and parsed identically.
        tricky_run = 'run-"quoted"-\'apostrophe\'-ünïcode'
        for command in recovery_commands(tricky_request, tricky_run):
            argv = shlex.split(command)
            self.assertEqual(argv[0], "buddy")
            self.assertEqual(json.loads(argv[-1]), {"runId": tricky_run})

        # The WAIT_ABANDONED envelope built by the CLI uses the same quoted commands.
        from buddy.cli import _abandoned

        abandoned = WaitAbandoned(tricky_request, None)
        payload = _abandoned(abandoned, recovery_commands(abandoned.request_id, abandoned.run_id))
        argv = shlex.split(payload["recovery"]["commands"][0])
        self.assertEqual(json.loads(argv[-1]), {"requestId": tricky_request})


class BlockingLoopTests(unittest.TestCase):
    def test_waits_through_bounded_slices_until_terminal_and_returns_envelope(self):
        clock = VirtualClock()
        service = FakeService(clock, complete_after_waits=2)
        envelope = run_blocking(BASE, service=service, clock=clock)
        self.assertEqual(envelope["outcome"], "completed")
        self.assertTrue(envelope["ok"])
        self.assertEqual(envelope["runId"], "run-fixture")
        self.assertTrue(envelope["shutdownConfirmed"])
        self.assertEqual(envelope["result"]["finalText"], "fixture result")
        self.assertTrue(envelope["resultDelivered"])
        self.assertIsNone(envelope["error"])
        self.assertEqual(envelope["reconnects"], 0)
        self.assertEqual(envelope["timedOut"], False)
        self.assertTrue(envelope["waitCoversRunnerDeadline"])
        self.assertIsNone(envelope["limitation"])
        self.assertEqual(envelope["waitSeconds"], 1800 + SHUTDOWN_GRACE_SECONDS)
        self.assertEqual(envelope["maxWaitSeconds"], MAX_WAIT_SECONDS)
        # Two event waits, then one result read: no busy polling, no cancel.
        self.assertEqual(service.calls, ["start", "wait", "wait", "result"])
        self.assertEqual(envelope["waitedSeconds"], 60.0)

    def test_multi_hour_run_is_covered_by_one_cli_call(self):
        clock = VirtualClock()
        service = FakeService(clock, complete_after_waits=1)
        envelope = run_blocking({**BASE, "timeoutSeconds": 28800}, service=service, clock=clock)
        self.assertTrue(envelope["ok"])
        self.assertEqual(envelope["waitSeconds"], 28800 + SHUTDOWN_GRACE_SECONDS)
        self.assertTrue(envelope["waitCoversRunnerDeadline"])
        self.assertIsNone(envelope["limitation"])

    def test_wait_timeout_is_a_wait_limit_not_an_execution_failure(self):
        clock = VirtualClock()
        service = FakeService(clock)
        envelope = run_blocking({**BASE, "timeoutSeconds": 7200, "waitSeconds": 1}, service=service, clock=clock)
        self.assertEqual(envelope["outcome"], "wait-timeout")
        self.assertFalse(envelope["ok"])
        self.assertTrue(envelope["timedOut"])
        self.assertEqual(envelope["status"], "running")
        self.assertIsNone(envelope["result"])
        self.assertFalse(envelope["resultAvailable"])
        self.assertFalse(envelope["waitCoversRunnerDeadline"])
        self.assertEqual(envelope["runnerDeadlineSeconds"], 7200)
        self.assertIn("buddy await", envelope["limitation"])
        self.assertIn("not an execution failure", envelope["recovery"]["reason"])
        self.assertIn("buddy await", envelope["recovery"]["action"])
        self.assertNotIn("cancel", service.calls)

    def test_disconnect_or_stop_abandons_only_the_wait(self):
        clock = VirtualClock()
        stop = threading.Event()
        service = FakeService(clock, stop_event=stop)
        with self.assertRaises(WaitAbandoned) as failure:
            run_blocking(BASE, service=service, clock=clock, stop=stop)
        self.assertEqual(failure.exception.run_id, "run-fixture")
        self.assertEqual(failure.exception.request_id, "fixture")
        # start + one event wait; the owned job is never cancelled by losing the wait.
        self.assertEqual(service.calls, ["start", "wait"])

    def test_reconnect_with_identical_request_recovers_the_same_run(self):
        clock = VirtualClock()
        service = FakeService(clock, complete_after_waits=1, fail_waits=1)
        envelope = run_blocking(BASE, service=service, clock=clock)
        self.assertEqual(envelope["outcome"], "completed")
        self.assertEqual(envelope["reconnects"], 1)
        self.assertEqual(envelope["runId"], "run-fixture")
        # A failed wait is recovered by an idempotent start, never by a new run.
        self.assertEqual(service.calls, ["start", "wait", "start", "wait", "result"])
        self.assertEqual(service.start_count, 2)

    def test_recovery_mismatch_is_reported_not_hidden(self):
        clock = VirtualClock()
        service = FakeService(clock, fail_waits=1, complete_after_waits=1, recover_to="run-someone-else")
        envelope = run_blocking(BASE, service=service, clock=clock)
        self.assertEqual(envelope["outcome"], "unavailable")
        self.assertEqual(envelope["error"]["code"], "RECOVERY_MISMATCH")
        self.assertEqual(envelope["runId"], "run-someone-else")

    def test_exhausted_reconnects_return_unavailable_with_recovery(self):
        clock = VirtualClock()
        service = FakeService(clock, fail_waits=99)
        envelope = run_blocking(BASE, service=service, clock=clock)
        self.assertEqual(envelope["outcome"], "unavailable")
        self.assertEqual(envelope["error"]["code"], "SERVICE_UNAVAILABLE")
        self.assertEqual(envelope["recovery"]["runId"], "run-fixture")
        self.assertFalse(envelope["ok"])
        # Bounded: exactly one initial wait plus MAX_RECONNECTS recovered waits.
        self.assertEqual(service.calls.count("wait"), 4)
        self.assertEqual(service.calls.count("start"), 4)

    def test_terminal_without_persisted_result_is_reported(self):
        clock = VirtualClock()
        service = FakeService(clock, terminal_without_result=True)
        envelope = run_blocking(BASE, service=service, clock=clock)
        self.assertEqual(envelope["outcome"], "reconciliation-needed")
        self.assertFalse(envelope["resultAvailable"])
        self.assertIsNone(envelope["result"])
        self.assertIn("buddy result", envelope["recovery"]["reason"])


class ResultTruthTests(unittest.TestCase):
    """A completed run never claims success unless its promised result is delivered.

    ``status`` keeps the underlying execution status (the worker may really have
    completed), while ``ok``/``outcome`` must stay honest about result delivery.
    """

    def test_completed_without_persisted_result_cannot_claim_success(self):
        clock = VirtualClock()
        service = FakeService(clock, terminal_without_result="completed")
        envelope = run_blocking(BASE, service=service, clock=clock)
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
        self.assertIn("buddy result", envelope["recovery"]["reason"])
        self.assertEqual(envelope["recovery"]["runId"], "run-fixture")
        self.assertEqual(service.calls, ["start", "wait"])

    def test_result_read_service_error_cannot_claim_success(self):
        clock = VirtualClock()
        service = FakeService(clock, complete_after_waits=1, fail_result=1)
        envelope = run_blocking(BASE, service=service, clock=clock)
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
        self.assertEqual(service.calls, ["start", "wait", "result"])

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
                service = FakeService(clock, complete_after_waits=1, result_response=response)
                envelope = run_blocking(BASE, service=service, clock=clock)
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
                self.assertEqual(service.calls, ["start", "wait", "result"])

    def test_successful_result_control_keeps_completed_semantics(self):
        clock = VirtualClock()
        service = FakeService(clock, complete_after_waits=1)
        envelope = run_blocking(BASE, service=service, clock=clock)
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
        service = FakeService(clock, terminal_without_result="failed")
        envelope = run_blocking(BASE, service=service, clock=clock)
        self.assertEqual(envelope["status"], "failed")
        self.assertEqual(envelope["outcome"], "failed")
        self.assertFalse(envelope["ok"])
        self.assertIsNone(envelope["result"])
        self.assertFalse(envelope["resultDelivered"])
        self.assertEqual(envelope["error"]["code"], "RESULT_NOT_AVAILABLE")
        self.assertIsNotNone(envelope["recovery"])

    def test_await_completed_without_result_is_not_success_either(self):
        clock = VirtualClock()
        service = FakeService(clock, listed=True, terminal_without_result="completed")
        envelope = await_run({"requestId": "fixture"}, service=service, clock=clock)
        self.assertEqual(envelope["runId"], "run-fixture")
        self.assertEqual(envelope["status"], "completed")
        self.assertFalse(envelope["ok"])
        self.assertEqual(envelope["outcome"], "completed-no-result")
        self.assertFalse(envelope["resultDelivered"])
        self.assertNotIn("start", service.calls)


class AwaitTests(unittest.TestCase):
    def test_await_resolves_an_existing_run_and_never_starts_one(self):
        clock = VirtualClock()
        service = FakeService(clock, listed=True, complete_after_waits=1)
        envelope = await_run({"requestId": "fixture"}, service=service, clock=clock)
        self.assertEqual(envelope["outcome"], "completed")
        self.assertEqual(envelope["runId"], "run-fixture")
        self.assertEqual(envelope["waitSeconds"], MAX_WAIT_SECONDS)
        self.assertEqual(service.calls, ["list", "wait", "result"])
        self.assertNotIn("start", service.calls)

    def test_await_accepts_a_run_id_and_checks_a_mismatched_request(self):
        clock = VirtualClock()
        service = FakeService(clock, complete_after_waits=1)
        envelope = await_run({"runId": "run-fixture", "requestId": "fixture"}, service=service, clock=clock)
        self.assertEqual(envelope["outcome"], "completed")
        self.assertEqual(service.calls[0], "status")
        with self.assertRaises(ServiceError) as failure:
            await_run({"runId": "run-fixture", "requestId": "other"}, service=service, clock=clock)
        self.assertEqual(failure.exception.code, "CONFLICT")

    def test_await_never_starts_missing_work(self):
        clock = VirtualClock()
        service = FakeService(clock)
        with self.assertRaises(ServiceError) as failure:
            await_run({"requestId": "missing"}, service=service, clock=clock)
        self.assertEqual(failure.exception.code, "NOT_FOUND")
        self.assertIn("buddy run", str(failure.exception))
        self.assertNotIn("start", service.calls)
        with self.assertRaises(ServiceError):
            await_run({}, service=service, clock=clock)
        with self.assertRaises(ServiceError):
            await_run({"requestId": "fixture", "waitSeconds": 0}, service=service, clock=clock)
        with self.assertRaises(ServiceError):
            await_run({"requestId": "fixture", "waitSeconds": MAX_WAIT_SECONDS + 1}, service=service, clock=clock)

    def test_await_reports_wait_timeout_without_restarting(self):
        clock = VirtualClock()
        service = FakeService(clock, listed=True)
        envelope = await_run({"requestId": "fixture", "waitSeconds": 1}, service=service, clock=clock)
        self.assertEqual(envelope["outcome"], "wait-timeout")
        self.assertEqual(envelope["status"], "running")
        self.assertNotIn("start", service.calls)
        self.assertIn("buddy await", envelope["recovery"]["action"])


if __name__ == "__main__":
    unittest.main()
