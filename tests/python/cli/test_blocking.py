"""Bounded await preserves decision, reminder and shutdown wakeups."""
from __future__ import annotations

from pathlib import Path
import tempfile
import unittest
from unittest import mock

from hey_my_buddy.cli import blocking
from hey_my_buddy.cli.blocking import WaitAbandoned, await_run
from hey_my_buddy.protocol.transport import ServiceError


class Clock:
    def __init__(self):
        self.value = 0.0

    def __call__(self):
        return self.value

    def advance(self, seconds: float):
        self.value += seconds


class AwaitTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.state_dir = Path(directory.name)
        self.clock = Clock()

    def test_boundary_returns_exact_request_id_without_command_templates(self):
        calls = []
        active = {"runId": "run-1", "state": "running", "workflowState": "executing", "revision": 1,
                  "actions": ["await", "inquire", "cancel", "get"]}
        boundary = {"runId": "run-1", "state": "completed", "workflowState": "awaiting-host",
                    "workflow": {"activeRequestId": "req-7"},
                    "resultAvailable": False, "revision": 2, "actions": ["continue", "cancel", "get"]}
        def service(method, params, directory):
            calls.append(method)
            if method == "workflow_get":
                return {"state": "awaiting-host", "actions": ["continue", "cancel", "get"],
                        "activeRequest": {"requestId": "req-7"},
                        "finalArtifactId": None}
            return active if len(calls) == 1 else boundary
        answer = await_run({"runId": "run-1"}, self.state_dir, service=service, clock=self.clock)
        self.assertEqual(calls, ["task_observe", "task_observe", "workflow_get"])
        self.assertEqual(answer["requestId"], "req-7")
        self.assertNotIn("requestKind", answer)
        self.assertEqual(answer["actions"], ["continue", "cancel", "get"])
        self.assertNotIn("nextCommands", answer)
        self.assertNotIn("resultCommand", answer)

    def test_descendant_shutdown_uses_the_message_wait_without_a_revision_change(self):
        calls = []
        unconfirmed = {"runId": "run-1", "state": "completed", "workflowState": "delivered",
                       "resultAvailable": True, "revision": 3, "actions": ["await", "inquire", "cancel", "get"],
                       "workflowShutdown": {"selfConfirmed": True, "descendantsConfirmed": False,
                                            "unconfirmedRunIds": ["child-1"]}}
        confirmed = {**unconfirmed, "workflowShutdown": {"selfConfirmed": True,
                        "descendantsConfirmed": True, "unconfirmedRunIds": []},
                     "artifactId": "artifact-1", "actions": ["accept", "continue", "conclude", "get"]}
        def service(method, params, directory):
            calls.append((method, dict(params)))
            if method == "task_observe":
                return unconfirmed if sum(1 for m, _ in calls if m == "task_observe") == 1 else confirmed
            if method == "workflow_get":
                return {"state": "delivered", "actions": ["accept", "continue", "conclude", "get"],
                        "finalArtifactId": "artifact-1", "activeRequest": None}
            if method == "task_result":
                return {"result": {"status": "completed"}}
            self.fail(f"unexpected call {method}")
        answer = await_run({"runId": "run-1"}, self.state_dir, service=service, clock=self.clock)
        self.assertEqual([name for name, _ in calls], ["task_observe", "task_observe", "workflow_get", "task_result"])
        self.assertEqual(calls[1][1]["runId"], "run-1")
        self.assertEqual(calls[0][1]["reminderWaitId"], calls[1][1]["reminderWaitId"])
        self.assertEqual(answer["artifactId"], "artifact-1")
        self.assertTrue(answer["shutdownConfirmed"])
        self.assertEqual(answer["actions"], ["accept", "continue", "conclude", "get"])

    def test_scoped_descendant_wait_observes_only_the_owned_task(self):
        unconfirmed = {"runId": "run-1", "state": "completed", "workflowState": "delivered",
                       "resultAvailable": True, "revision": 3, "actions": ["await", "get"],
                       "workflowShutdown": {"selfConfirmed": True, "descendantsConfirmed": False,
                                            "unconfirmedRunIds": ["private-child"]}}
        confirmed = {**unconfirmed, "actions": ["accept", "continue", "conclude", "get"],
                     "workflowShutdown": {"selfConfirmed": True, "descendantsConfirmed": True,
                                          "unconfirmedRunIds": []}}
        calls = []
        def service(method, params, directory):
            calls.append((method, dict(params)))
            if method == "task_observe":
                return unconfirmed if sum(1 for m, _ in calls if m == "task_observe") == 1 else confirmed
            if method == "workflow_get":
                return {"state": "delivered", "actions": confirmed["actions"],
                        "activeRequest": None, "finalArtifactId": "art-1"}
            if method == "task_result":
                return {"result": {"status": "completed"}}
            self.fail(f"unexpected call {method}")
        answer = await_run({"runId": "run-1"}, self.state_dir, service=service,
                           clock=self.clock)
        self.assertEqual([name for name, _ in calls],
                         ["task_observe", "task_observe", "workflow_get", "task_result"])
        self.assertEqual(calls[1][1]["runId"], "run-1")
        self.assertEqual(answer["artifactId"], "art-1")
        self.assertTrue(answer["shutdownConfirmed"])

    def test_pending_reminder_ends_wait_without_starting_a_new_turn(self):
        reminder = {"kind": "review-reminder", "reason": "review is waiting"}
        run = {"runId": "run-1", "state": "running", "workflowState": "executing",
               "actions": ["await", "inquire", "cancel", "get"], "reminder": reminder}
        calls = []
        def service(method, params, directory):
            calls.append(method)
            return run
        answer = await_run({"runId": "run-1"}, self.state_dir, service=service, clock=self.clock)
        self.assertEqual(calls, ["task_observe"])
        self.assertEqual(answer["reminder"], reminder)
        self.assertEqual(answer["outcome"], "review-reminder")
        self.assertEqual(answer["actions"], run["actions"])

    def test_running_reminders_and_issues_include_compact_activity(self):
        observed_at = "2026-11-11T00:00:00Z"
        activity = {"phase": "waiting-host", "observedAt": observed_at,
                    "lastNativeActivityAt": observed_at, "lastToolActivityAt": observed_at,
                    "toolName": "read", "counts": {"toolCalls": 1},
                    "waitingReason": {"code": "native-authority-required", "params": {"kind": "permission"}}}
        native = {**activity, "eventSeq": 7, "nativeSessionId": "native-session"}
        for outcome, message in (
            ("expected-duration", {"reminder": {"kind": "expected-duration"}}),
            ("native-silence", {"reminder": {"kind": "native-silence"}}),
            ("issue", {"issue": True}),
        ):
            with self.subTest(outcome=outcome):
                run = {"runId": "run-1", "state": "running", "workflowState": "executing",
                       "actions": ["await", "get"], "activity": native, "providerRejection": None, **message}
                service = mock.Mock(return_value=run)
                answer = await_run({"runId": "run-1"}, self.state_dir, service=service, clock=self.clock)
                self.assertEqual(answer["outcome"], outcome)
                self.assertEqual(answer["activity"], activity)
                self.assertNotIn("providerRejection", answer)

    def test_pause_interruption_returns_once_without_treating_run_as_host_pending(self):
        active = {"runId": "run-1", "state": "queued", "workflowState": "executing",
                  "queueReason": "paused", "revision": 2, "actions": ["await", "get"]}
        delivered = {**active, "pause": {"pausedAt": "2026-11-11T00:00:00Z", "eventId": 7},
                     "activity": {"phase": "finishing", "eventSeq": 5}}
        calls = []
        def service(method, params, directory):
            calls.append(method)
            return active if len(calls) == 1 else delivered
        answer = await_run({"runId": "run-1"}, self.state_dir, service=service,
                           clock=self.clock)
        self.assertEqual(calls, ['task_observe', 'task_observe'])
        self.assertEqual(answer['outcome'], 'paused')
        self.assertEqual(answer['queueReason'], 'paused')
        self.assertEqual(answer['pause'], delivered['pause'])
        self.assertEqual(answer["activity"], {"phase": "finishing"})
        self.assertNotIn('requestId', answer)

    def test_upgrade_handover_rearms_same_wait_inside_window(self):
        (self.state_dir / "upgrade.json").write_text("{}", encoding="utf-8")
        active = {"runId": "run-1", "state": "running", "workflowState": "executing",
                  "revision": 1, "actions": ["await", "inquire", "cancel", "get"]}
        boundary = {"runId": "run-1", "state": "completed", "workflowState": "awaiting-host",
                    "revision": 2, "resultAvailable": False, "actions": ["continue", "cancel", "get"],
                    "workflow": {"activeRequestId": "req-1"}}
        observes = 0
        calls = []
        wait_ids = []
        def service(method, params, directory):
            nonlocal observes
            calls.append(method)
            if method == "task_observe":
                observes += 1
                wait_ids.append(params.get("reminderWaitId"))
                if observes == 1:
                    return active
                if observes == 2:
                    raise ServiceError("SERVICE_UNAVAILABLE", "old daemon detached")
                return boundary
            if method == "workflow_get":
                return {"state": "awaiting-host", "actions": boundary["actions"],
                        "activeRequest": {"requestId": "req-1"},
                        "finalArtifactId": None}
            self.fail(f"unexpected call {method}")
        with mock.patch.object(blocking.time, "sleep", side_effect=self.clock.advance):
            answer = await_run({"runId": "run-1", "waitSeconds": 2}, self.state_dir,
                               service=service, clock=self.clock)
        self.assertEqual(calls, ["task_observe", "task_observe", "task_observe", "workflow_get"])
        self.assertEqual(len(set(wait_ids)), 1)
        self.assertEqual(answer["requestId"], "req-1")
        self.assertNotIn("error", answer)

    def test_wait_window_expires_without_cancelling_run(self):
        active = {"runId": "run-1", "state": "running", "workflowState": "executing", "revision": 1,
                  "actions": ["await", "inquire", "cancel", "get"],
                  "selectedAttempt": {"issues": [{"code": "native-permission-denied"}]}}
        activity = {"phase": "streaming-model", "observedAt": "2026-11-11T00:00:00Z"}
        calls = []
        def service(method, params, directory):
            calls.append(method)
            if len(calls) == 2:
                self.clock.advance(1)
                return {**active, "activity": {**activity, "eventSeq": 2}}
            return active
        answer = await_run({"runId": "run-1", "waitSeconds": 1}, self.state_dir,
                           service=service, clock=self.clock)
        self.assertEqual(answer["outcome"], "wait-timeout")
        self.assertTrue(answer["timedOut"])
        self.assertEqual(answer["activity"], activity)
        self.assertEqual(answer["selectedAttempt"]["issues"], [{"code": "native-permission-denied"}])
        self.assertEqual(calls, ["task_observe", "task_observe"])
        self.assertNotIn("cancel", calls)

    def test_failed_await_exposes_provider_rejection_and_omits_activity(self):
        rejection = {"code": "quota-exceeded", "nativeCode": "usageLimitExceeded"}
        run = {"runId": "run-1", "state": "failed", "workflowState": "failed",
               "actions": ["get"], "resultAvailable": True, "shutdownConfirmed": True,
               "activity": {"phase": "finishing", "observedAt": "2026-11-11T00:00:00Z"},
               "providerRejection": rejection}
        responses = {
            "task_observe": run,
            "workflow_get": {"state": "failed", "actions": ["get"]},
            "task_result": {"result": {"status": "failed", "providerRejection": rejection}},
        }
        def service(method, params, directory):
            return responses[method]
        answer = await_run({"runId": "run-1"}, self.state_dir, service=service, clock=self.clock)
        self.assertEqual(answer["outcome"], "failed")
        self.assertEqual(answer["providerRejection"], rejection)
        self.assertNotIn("activity", answer)

    def test_client_interrupt_abandons_only_this_wait(self):
        def service(method, params, directory):
            raise KeyboardInterrupt
        with self.assertRaises(WaitAbandoned) as raised:
            await_run({"runId": "run-1"}, self.state_dir, service=service)
        self.assertEqual(raised.exception.run_id, "run-1")

    def test_workflow_error_preserves_service_details(self):
        run = {"runId": "run-1", "state": "completed", "workflowState": "awaiting-host",
               "actions": ["continue", "get"]}
        def service(method, params, directory):
            if method == "task_observe":
                return run
            raise ServiceError("CONFLICT", "View changed", revision=9)
        answer = await_run({"runId": "run-1"}, self.state_dir, service=service, clock=self.clock)
        self.assertEqual(answer["error"], {"code": "CONFLICT", "message": "View changed",
                                           "details": {"revision": 9}})


if __name__ == "__main__":
    unittest.main()
