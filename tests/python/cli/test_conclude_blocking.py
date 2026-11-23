"""Conclude waits for owned shutdown without losing the original response."""
from __future__ import annotations

import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from hey_my_buddy.cli import blocking, main as cli
from hey_my_buddy.cli.blocking import conclude_run
from hey_my_buddy.protocol.transport import ServiceError
from hey_my_buddy.protocol.requests import parse_request
from hey_my_buddy.errors import BoardError
from cli.test_blocking import Clock


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
        self.assertEqual(calls, [("conclude", {"runId": "lead-1", "note": "Finish"})])

    def test_service_model_rejects_cli_wait_field(self):
        with self.assertRaises(BoardError) as raised:
            parse_request("conclude", self.packet, audience="service")
        self.assertIn("waitSeconds", str(raised.exception))

    def test_confirmed_stop_definitive_rejection_records_no_conclusion(self):
        for code in ("CONFLICT", "WORKSPACE_SCOPE_VIOLATION"):
            with self.subTest(code=code):
                attempts = 0
                def service(method, params, directory):
                    nonlocal attempts
                    if method == "conclude":
                        attempts += 1
                        if attempts == 1:
                            raise ServiceError("SHUTDOWN_UNCONFIRMED", "Stopping", cancellationRecorded=True)
                        raise ServiceError(code, "Rejected")
                    return self.view(confirmed=True)
                answer = conclude_run(self.packet, self.state_dir, service=service, clock=self.clock)
                self.assertEqual(answer["error"]["code"], code)
                self.assertIs(answer["conclusionRecorded"], False)

    def test_transport_failure_after_resend_leaves_conclusion_unknown(self):
        for code in ("C_TWO_IO", "INVALID_RESPONSE"):
            with self.subTest(code=code):
                attempts = 0
                def service(method, params, directory):
                    nonlocal attempts
                    if method == "conclude":
                        attempts += 1
                        if attempts == 1:
                            raise ServiceError("SHUTDOWN_UNCONFIRMED", "Stopping", cancellationRecorded=True)
                        raise ServiceError(code, "No authoritative reply")
                    return self.view(confirmed=True)
                answer = conclude_run(self.packet, self.state_dir, service=service, clock=self.clock)
                self.assertIsNone(answer["conclusionRecorded"])

    def test_lost_resend_remains_unknown_if_a_later_retry_is_unauthorized(self):
        (self.state_dir / "upgrade.json").write_text("{}", encoding="utf-8")
        attempts = 0
        def service(method, params, directory):
            nonlocal attempts
            if method == "conclude":
                attempts += 1
                if attempts == 1:
                    raise ServiceError("SHUTDOWN_UNCONFIRMED", "Stopping", cancellationRecorded=True)
                if attempts == 2:
                    raise ServiceError("SERVICE_UNAVAILABLE", "Reply lost after possible commit")
                raise ServiceError("UNAUTHORIZED", "The Lead's credential was revoked before replay")
            return self.view(confirmed=True)
        with mock.patch.object(blocking.time, "sleep", side_effect=self.clock.advance):
            answer = conclude_run(self.packet, self.state_dir, service=service, clock=self.clock)
        self.assertEqual(answer["error"]["code"], "UNAUTHORIZED")
        self.assertIsNone(answer["conclusionRecorded"])

    def test_resend_handover_stops_retrying_at_the_deadline(self):
        (self.state_dir / "upgrade.json").write_text("{}", encoding="utf-8")
        attempts = 0
        def service(method, params, directory):
            nonlocal attempts
            if method == "conclude":
                attempts += 1
                if attempts > 10:
                    self.fail("The expired handover kept retrying conclude")
                if attempts == 1:
                    raise ServiceError("SHUTDOWN_UNCONFIRMED", "Stopping", cancellationRecorded=True)
                raise ServiceError("SERVICE_UNAVAILABLE", "Still handing over")
            return self.view(confirmed=True)
        with mock.patch.object(blocking.time, "sleep", side_effect=self.clock.advance):
            answer = conclude_run({**self.packet, "waitSeconds": 1}, self.state_dir,
                                  service=service, clock=self.clock)
        self.assertIsNone(answer["conclusionRecorded"])
        self.assertEqual(self.clock(), 1)

    def test_interrupted_initial_conclude_reports_unknown_facts(self):
        def service(method, params, directory):
            raise KeyboardInterrupt
        answer = conclude_run(self.packet, self.state_dir, service=service, clock=self.clock)
        self.assertEqual(answer["error"]["code"], "WAIT_ABANDONED")
        self.assertIsNone(answer["cancellationRecorded"])
        self.assertIsNone(answer["conclusionRecorded"])

    def test_cli_prints_json_when_initial_conclude_is_interrupted(self):
        output = io.StringIO()
        with (contextlib.redirect_stdout(output),
              mock.patch.object(cli, "get_state_dir", return_value=self.state_dir),
              mock.patch.object(cli, "_call_service", side_effect=KeyboardInterrupt)):
            status = cli.main(["conclude", json.dumps(self.packet)], credential_document=None)
        answer = json.loads(output.getvalue())
        self.assertEqual(status, 1)
        self.assertEqual(answer["error"]["code"], "WAIT_ABANDONED")
        self.assertIsNone(answer["cancellationRecorded"])
        self.assertIsNone(answer["conclusionRecorded"])

    def test_upgrade_handover_rearms_resend(self):
        (self.state_dir / "upgrade.json").write_text("{}", encoding="utf-8")
        attempts = 0
        def service(method, params, directory):
            nonlocal attempts
            if method == "conclude":
                attempts += 1
                if attempts == 1:
                    raise ServiceError("SHUTDOWN_UNCONFIRMED", "Stopping", cancellationRecorded=True)
                if attempts == 2:
                    raise ServiceError("SERVICE_UNAVAILABLE", "Daemon changed")
                return {"conclusionId": "done"}
            return self.view(confirmed=True)
        with mock.patch.object(blocking.time, "sleep", side_effect=self.clock.advance):
            answer = conclude_run(self.packet, self.state_dir, service=service, clock=self.clock)
        self.assertEqual(answer["conclusionId"], "done")
        self.assertEqual(attempts, 3)

    def test_timeout_error_uses_latest_shutdown_facts(self):
        observes = 0
        def service(method, params, directory):
            nonlocal observes
            if method == "conclude":
                raise ServiceError("SHUTDOWN_UNCONFIRMED", "Stopping", cancellationRecorded=True,
                                   unconfirmedRunIds=["stale-child"])
            observes += 1
            if observes == 1:
                return self.view()
            self.clock.advance(1)
            return {**self.view(), "workflowShutdown": {"selfConfirmed": True,
                    "descendantsConfirmed": False, "unconfirmedRunIds": ["current-child"]}}
        answer = conclude_run({**self.packet, "waitSeconds": 1}, self.state_dir,
                              service=service, clock=self.clock)
        self.assertEqual(answer["shutdown"]["unconfirmedRunIds"], ["current-child"])
        self.assertNotIn("unconfirmedRunIds", answer["error"].get("details", {}))

    def test_descendant_only_stop_retries_identical_packet_without_consuming_reminders(self):
        calls = []
        attempts = 0
        observes = 0
        def service(method, params, directory):
            nonlocal attempts, observes
            calls.append((method, dict(params)))
            if method == "conclude":
                attempts += 1
                if attempts == 1:
                    raise ServiceError("SHUTDOWN_UNCONFIRMED", "Child still stopping",
                                       cancellationRecorded=True, conclusionRecorded=False,
                                       unconfirmedRunIds=["child-1"])
                return {"runId": "lead-1", "conclusionId": "conclusion-1"}
            if method == "task_observe":
                observes += 1
                return self.view() if observes == 1 else self.view(confirmed=True)
            self.fail(method)
        answer = conclude_run(self.packet, self.state_dir, service=service, clock=self.clock)
        self.assertEqual(answer["conclusionId"], "conclusion-1")
        self.assertEqual([method for method, _ in calls],
                         ["conclude", "task_observe", "task_observe", "conclude"])
        self.assertNotIn("reminderWaitId", calls[1][1])
        self.assertNotIn("reminderWaitId", calls[2][1])
        self.assertEqual(calls[0][1], calls[3][1])
        self.assertNotIn("waitSeconds", calls[0][1])

    def test_timeout_reports_recorded_cancellation(self):
        calls = []
        observes = 0
        def service(method, params, directory):
            nonlocal observes
            calls.append((method, dict(params)))
            if method == "conclude":
                raise ServiceError("SHUTDOWN_UNCONFIRMED", "Still stopping",
                                   cancellationRecorded=True)
            observes += 1
            if observes == 2:
                self.clock.advance(65)
            return self.view()
        answer = conclude_run(self.packet, self.state_dir, service=service, clock=self.clock)
        self.assertEqual([method for method, _ in calls], ["conclude", "task_observe", "task_observe"])
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
        observes = 0
        def service(method, params, directory):
            nonlocal attempts, observes
            if method == "conclude":
                attempts += 1
                if attempts == 1:
                    raise ServiceError("SHUTDOWN_UNCONFIRMED", "Still stopping", cancellationRecorded=True)
                return {"conclusionId": "done"}
            observes += 1
            if observes == 1:
                return self.view()
            self.clock.advance(1)
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
            self.assertEqual(method, "task_observe")
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
        observes = 0
        def service(method, params, directory):
            nonlocal observes
            if method == "conclude":
                raise ServiceError("SHUTDOWN_UNCONFIRMED", "Still stopping",
                                   cancellationRecorded=True)
            observes += 1
            if observes == 1:
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
        observes = 0
        calls = []
        def service(method, params, directory):
            nonlocal observes
            calls.append(method)
            if method == "conclude":
                if calls.count("conclude") == 1:
                    raise ServiceError("SHUTDOWN_UNCONFIRMED", "Still stopping",
                                       cancellationRecorded=True)
                return {"conclusionId": "done"}
            observes += 1
            if observes == 1:
                return self.view()
            if observes == 2:
                raise ServiceError("SERVICE_UNAVAILABLE", "Old daemon detached")
            return self.view(confirmed=True)
        with mock.patch.object(blocking.time, "sleep", side_effect=self.clock.advance):
            answer = conclude_run(self.packet, self.state_dir, service=service, clock=self.clock)
        self.assertEqual(answer["conclusionId"], "done")
        self.assertEqual(calls, ["conclude", "task_observe", "task_observe", "task_observe", "conclude"])

    def test_keyboard_interrupt_after_cancellation_keeps_the_stop_fact(self):
        observes = 0
        def service(method, params, directory):
            nonlocal observes
            if method == "conclude":
                raise ServiceError("SHUTDOWN_UNCONFIRMED", "Still stopping",
                                   cancellationRecorded=True)
            observes += 1
            if observes == 1:
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
        observes = 0
        packet = {"runId": "lead-1", "note": "Finish", "waitSeconds": 1}
        def service(method, params, directory):
            nonlocal observes
            calls.append((method, dict(params)))
            if method == "conclude":
                raise ServiceError("SHUTDOWN_UNCONFIRMED", "Still stopping",
                                   cancellationRecorded=True)
            observes += 1
            if observes == 2:
                clock.advance(1)
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
        self.assertEqual([method for method, _ in calls], ["conclude", "task_observe", "task_observe"])


if __name__ == "__main__":
    unittest.main()
