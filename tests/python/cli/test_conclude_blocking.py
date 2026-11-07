"""Conclude waits for owned shutdown without losing the original response."""
from __future__ import annotations

import contextlib
import io
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest import mock

from hey_my_buddy.cli import blocking, main as cli
from hey_my_buddy.cli.blocking import conclude_run
from hey_my_buddy.protocol.transport import ServiceError


class Clock:
    def __init__(self):
        self.value = 0.0

    def __call__(self):
        return self.value

    def advance(self, seconds):
        self.value += seconds


class ConcludeTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.state_dir = Path(directory.name)
        self.clock = Clock()
        self.packet = {"runId": "lead-1", "note": "Finish", "waitSeconds": 65}

    def view(self, confirmed=False):
        return {"runId": "lead-1", "state": "cancelling", "workflowState": "cancelled",
                "actions": ["await", "inquire", "cancel", "conclude", "get"],
                "revision": 8, "workflowShutdown": {"selfConfirmed": True,
                "descendantsConfirmed": confirmed,
                "unconfirmedRunIds": [] if confirmed else ["child-1"]}}

    def test_already_stopped_concludes_with_one_service_call(self):
        calls = []
        def service(method, params, directory):
            calls.append((method, dict(params)))
            return {"runId": "lead-1", "conclusionId": "conclusion-1"}
        answer = conclude_run(self.packet, self.state_dir, service=service, clock=self.clock)
        self.assertEqual(answer["conclusionId"], "conclusion-1")
        self.assertEqual(calls, [("conclude", self.packet)])

    def test_descendant_only_stop_retries_identical_packet_without_consuming_reminders(self):
        calls = []
        attempts = 0
        def service(method, params, directory):
            nonlocal attempts
            calls.append((method, dict(params)))
            if method == "conclude":
                attempts += 1
                if attempts == 1:
                    raise ServiceError("SHUTDOWN_UNCONFIRMED", "Child still stopping",
                                       cancellationRecorded=True, conclusionRecorded=False,
                                       unconfirmedRunIds=["child-1"])
                return {"runId": "lead-1", "conclusionId": "conclusion-1"}
            if method == "task_get":
                return self.view()
            if method == "task_wait":
                return self.view(confirmed=True)  # same revision; child stop alone wakes it
            self.fail(method)
        answer = conclude_run(self.packet, self.state_dir, service=service, clock=self.clock)
        self.assertEqual(answer["conclusionId"], "conclusion-1")
        self.assertEqual([method for method, _ in calls],
                         ["conclude", "task_get", "task_wait", "conclude"])
        self.assertEqual(calls[2][1]["afterRevision"], 8)
        self.assertFalse(calls[2][1]["consumeReminders"])
        self.assertNotIn("reminderWaitId", calls[2][1])
        self.assertEqual(calls[0][1], calls[3][1])

    def test_target_run_is_observed_but_owner_packet_is_retried(self):
        packet = {**self.packet, "targetRunId": "child-1"}
        calls = []
        def service(method, params, directory):
            calls.append((method, dict(params)))
            if method == "conclude" and len(calls) == 1:
                raise ServiceError("SHUTDOWN_UNCONFIRMED", "Child still stopping",
                                   cancellationRecorded=True)
            if method == "task_get":
                return {**self.view(confirmed=True), "runId": "child-1"}
            return {"runId": "lead-1", "targetRunId": "child-1", "conclusionId": "done"}
        answer = conclude_run(packet, self.state_dir, service=service, clock=self.clock)
        self.assertEqual(answer["conclusionId"], "done")
        self.assertEqual(calls[1], ("task_get", {"runId": "child-1"}))
        self.assertEqual(calls[0], calls[2])

    def test_timeout_uses_bounded_slices_and_reports_recorded_cancellation(self):
        calls = []
        def service(method, params, directory):
            calls.append((method, dict(params)))
            if method == "conclude":
                raise ServiceError("SHUTDOWN_UNCONFIRMED", "Still stopping",
                                   cancellationRecorded=True)
            if method == "task_get":
                return self.view()
            self.clock.advance(params["timeoutMs"] / 1000)
            return self.view()
        answer = conclude_run(self.packet, self.state_dir, service=service, clock=self.clock)
        self.assertEqual([params["timeoutMs"] for method, params in calls if method == "task_wait"],
                         [30000, 30000, 5000])
        self.assertEqual(answer["error"]["code"], "SHUTDOWN_UNCONFIRMED")
        self.assertEqual(answer["waitedSeconds"], 65)
        self.assertTrue(answer["timedOut"])
        self.assertTrue(answer["cancellationRecorded"])
        self.assertFalse(answer["conclusionRecorded"])
        self.assertFalse(answer["shutdownConfirmed"])
        self.assertEqual(answer["shutdown"]["unconfirmedRunIds"], ["child-1"])
        self.assertEqual([method for method, _ in calls].count("conclude"), 1)
        self.assertIn("conclude", answer["actions"])

    def test_stop_observed_at_the_deadline_is_concluded(self):
        attempts = 0
        def service(method, params, directory):
            nonlocal attempts
            if method == "conclude":
                attempts += 1
                if attempts == 1:
                    raise ServiceError("SHUTDOWN_UNCONFIRMED", "Still stopping", cancellationRecorded=True)
                return {"conclusionId": "done"}
            if method == "task_get":
                return self.view()
            self.clock.advance(params["timeoutMs"] / 1000)
            return self.view(confirmed=True)
        answer = conclude_run({**self.packet, "waitSeconds": 1}, self.state_dir,
                              service=service, clock=self.clock)
        self.assertEqual(answer["conclusionId"], "done")
        self.assertEqual(self.clock(), 1)

    def test_upgrade_before_first_observation_reuses_the_handover_poll(self):
        (self.state_dir / "upgrade.json").write_text("{}", encoding="utf-8")
        reads = 0
        attempts = 0
        def service(method, params, directory):
            nonlocal reads, attempts
            if method == "conclude":
                attempts += 1
                if attempts == 1:
                    raise ServiceError("SHUTDOWN_UNCONFIRMED", "Still stopping", cancellationRecorded=True)
                return {"conclusionId": "done"}
            self.assertEqual(method, "task_get")
            reads += 1
            if reads == 1:
                raise ServiceError("SERVICE_UNAVAILABLE", "Handover before observation")
            return self.view(confirmed=True)
        with mock.patch.object(blocking.time, "sleep", side_effect=self.clock.advance):
            answer = conclude_run(self.packet, self.state_dir, service=service, clock=self.clock)
        self.assertEqual(answer["conclusionId"], "done")
        self.assertEqual(reads, 2)
        self.assertEqual(self.clock(), blocking.UPGRADE_HANDOVER_POLL_SECONDS)

    def test_unavailable_service_keeps_cancellation_fact(self):
        def service(method, params, directory):
            if method == "conclude":
                raise ServiceError("SHUTDOWN_UNCONFIRMED", "Still stopping",
                                   cancellationRecorded=True)
            if method == "task_get":
                return self.view()
            raise ServiceError("SERVICE_UNAVAILABLE", "Daemon is gone")
        answer = conclude_run(self.packet, self.state_dir, service=service, clock=self.clock)
        self.assertEqual(answer["error"]["code"], "SERVICE_UNAVAILABLE")
        self.assertTrue(answer["cancellationRecorded"])
        self.assertFalse(answer["conclusionRecorded"])

    def test_unconfirmed_without_recorded_cancellation_is_not_treated_as_running_conclude(self):
        calls = []
        def service(method, params, directory):
            calls.append(method)
            raise ServiceError("SHUTDOWN_UNCONFIRMED", "No stop request was recorded")
        with self.assertRaises(ServiceError):
            conclude_run(self.packet, self.state_dir, service=service, clock=self.clock)
        self.assertEqual(calls, ["conclude"])

    def test_lost_retry_reply_does_not_claim_conclusion_failed(self):
        attempts = 0
        def service(method, params, directory):
            nonlocal attempts
            if method == "conclude":
                attempts += 1
                if attempts == 1:
                    raise ServiceError("SHUTDOWN_UNCONFIRMED", "Still stopping",
                                       cancellationRecorded=True)
                raise ServiceError("SERVICE_UNAVAILABLE", "Reply was lost")
            return self.view(confirmed=True)
        answer = conclude_run(self.packet, self.state_dir, service=service, clock=self.clock)
        self.assertEqual(answer["error"]["code"], "SERVICE_UNAVAILABLE")
        self.assertTrue(answer["cancellationRecorded"])
        self.assertIsNone(answer["conclusionRecorded"])
        self.assertTrue(answer["shutdownConfirmed"])

    def test_upgrade_handover_rearms_same_wait(self):
        (self.state_dir / "upgrade.json").write_text("{}", encoding="utf-8")
        waits = 0
        calls = []
        def service(method, params, directory):
            nonlocal waits
            calls.append(method)
            if method == "conclude":
                if calls.count("conclude") == 1:
                    raise ServiceError("SHUTDOWN_UNCONFIRMED", "Still stopping",
                                       cancellationRecorded=True)
                return {"conclusionId": "done"}
            if method == "task_get":
                return self.view()
            waits += 1
            if waits == 1:
                raise ServiceError("SERVICE_UNAVAILABLE", "Old daemon detached")
            return self.view(confirmed=True)
        with mock.patch.object(blocking.time, "sleep", side_effect=self.clock.advance):
            answer = conclude_run(self.packet, self.state_dir, service=service, clock=self.clock)
        self.assertEqual(answer["conclusionId"], "done")
        self.assertEqual(calls, ["conclude", "task_get", "task_wait", "task_wait", "conclude"])

    def test_abandonment_reports_cancellation_already_requested(self):
        stop = threading.Event()
        def service(method, params, directory):
            stop.set()
            raise ServiceError("SHUTDOWN_UNCONFIRMED", "Still stopping",
                               cancellationRecorded=True)
        answer = conclude_run(self.packet, self.state_dir, service=service,
                              clock=self.clock, stop=stop)
        self.assertEqual(answer["error"]["code"], "WAIT_ABANDONED")
        self.assertTrue(answer["cancellationRecorded"])
        self.assertFalse(answer["conclusionRecorded"])

    def test_keyboard_interrupt_after_cancellation_keeps_the_stop_fact(self):
        def service(method, params, directory):
            if method == "conclude":
                raise ServiceError("SHUTDOWN_UNCONFIRMED", "Still stopping",
                                   cancellationRecorded=True)
            if method == "task_get":
                return self.view()
            raise KeyboardInterrupt
        answer = conclude_run(self.packet, self.state_dir, service=service, clock=self.clock)
        self.assertEqual(answer["error"]["code"], "WAIT_ABANDONED")
        self.assertTrue(answer["cancellationRecorded"])
        self.assertFalse(answer["conclusionRecorded"])

    def test_interrupt_during_retry_leaves_the_conclusion_unknown(self):
        attempts = 0
        def service(method, params, directory):
            nonlocal attempts
            if method == "conclude":
                attempts += 1
                if attempts == 1:
                    raise ServiceError("SHUTDOWN_UNCONFIRMED", "Still stopping", cancellationRecorded=True)
                raise KeyboardInterrupt
            return self.view(confirmed=True)
        answer = conclude_run(self.packet, self.state_dir, service=service, clock=self.clock)
        self.assertEqual(answer["error"]["code"], "WAIT_ABANDONED")
        self.assertTrue(answer["cancellationRecorded"])
        self.assertIsNone(answer["conclusionRecorded"])

    def test_main_routes_conclude_through_wait_and_returns_nonzero_on_timeout(self):
        calls = []
        clock = Clock()
        packet = {"runId": "lead-1", "note": "Finish", "waitSeconds": 1}
        def service(method, params, directory):
            calls.append((method, dict(params)))
            if method == "conclude":
                raise ServiceError("SHUTDOWN_UNCONFIRMED", "Still stopping",
                                   cancellationRecorded=True)
            if method == "task_get":
                return self.view()
            clock.advance(params["timeoutMs"] / 1000)
            return self.view()
        output = io.StringIO()
        with (contextlib.redirect_stdout(output),
              mock.patch.object(cli, "get_state_dir", return_value=self.state_dir),
              mock.patch.object(cli, "_call_service", side_effect=service),
              mock.patch.object(blocking, "conclude_run",
                                side_effect=lambda parsed, **kwargs: conclude_run(parsed, clock=clock, **kwargs))):
            status = cli.main(["conclude", json.dumps(packet)], credential_document=None)
        answer = json.loads(output.getvalue())
        self.assertEqual(status, 1)
        self.assertEqual(answer["error"]["code"], "SHUTDOWN_UNCONFIRMED")
        self.assertEqual([method for method, _ in calls], ["conclude", "task_get", "task_wait"])


if __name__ == "__main__":
    unittest.main()
