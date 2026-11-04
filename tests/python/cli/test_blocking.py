"""Bounded await preserves decision, reminder and shutdown wakeups."""
from __future__ import annotations

from pathlib import Path
import tempfile
import threading
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
                    "workflow": {"activeRequestId": "req-7", "requestKind": "attention"},
                    "resultAvailable": False, "revision": 2, "actions": ["continue", "cancel", "get"]}
        def service(method, params, directory):
            calls.append(method)
            if method == "workflow_get":
                return {"state": "awaiting-host", "actions": ["continue", "cancel", "get"],
                        "activeRequest": {"requestId": "req-7", "kind": "attention"},
                        "finalArtifactId": None}
            return active if method == "task_get" else boundary
        answer = await_run({"runId": "run-1"}, self.state_dir, service=service, clock=self.clock)
        self.assertEqual(calls, ["task_get", "task_wait", "workflow_get"])
        self.assertEqual(answer["requestId"], "req-7")
        self.assertEqual(answer["requestKind"], "attention")
        self.assertEqual(answer["actions"], ["continue", "cancel", "get"])
        self.assertNotIn("nextCommands", answer)
        self.assertNotIn("resultCommand", answer)

    def test_descendant_shutdown_waits_on_event_then_rechecks_parent(self):
        calls = []
        unconfirmed = {"runId": "run-1", "state": "completed", "workflowState": "delivered",
                       "resultAvailable": True, "revision": 3, "actions": ["await", "inquire", "cancel", "get"],
                       "workflowShutdown": {"selfConfirmed": True, "descendantsConfirmed": False,
                                            "unconfirmedRunIds": ["child-1"]}}
        confirmed = {**unconfirmed, "workflowShutdown": {"selfConfirmed": True,
                        "descendantsConfirmed": True, "unconfirmedRunIds": []},
                     "artifactId": "artifact-1", "actions": ["accept", "continue", "conclude", "get"]}
        count = 0
        def service(method, params, directory):
            nonlocal count
            calls.append((method, dict(params)))
            if method == "task_get":
                count += 1
                return unconfirmed if count == 1 else confirmed
            if method == "events_read":
                return {"head": 9}
            if method == "workflow_get":
                return {"state": "delivered", "actions": ["accept", "continue", "conclude", "get"],
                        "finalArtifactId": "artifact-1", "activeRequest": None}
            if method == "task_result":
                return {"result": {"status": "completed"}}
            self.fail(f"unexpected call {method}")
        answer = await_run({"runId": "run-1"}, self.state_dir, service=service, clock=self.clock)
        self.assertEqual([name for name, _ in calls], ["task_get", "events_read", "task_get", "workflow_get", "task_result"])
        self.assertEqual(calls[1][1]["runId"], "child-1")
        self.assertEqual(answer["artifactId"], "artifact-1")
        self.assertTrue(answer["shutdownConfirmed"])
        self.assertEqual(answer["actions"], ["accept", "continue", "conclude", "get"])

    def test_scoped_descendant_wait_uses_own_event_cursor_and_real_event_wait(self):
        unconfirmed = {"runId": "run-1", "state": "completed", "workflowState": "delivered",
                       "resultAvailable": True, "revision": 3, "actions": ["await", "get"],
                       "workflowShutdown": {"selfConfirmed": True, "descendantsConfirmed": False,
                                            "unconfirmedRunIds": ["private-child"]}}
        confirmed = {**unconfirmed, "actions": ["accept", "continue", "conclude", "get"],
                     "workflowShutdown": {"selfConfirmed": True, "descendantsConfirmed": True,
                                          "unconfirmedRunIds": []}}
        reads = 0
        calls = []
        def service(method, params, directory):
            nonlocal reads
            calls.append((method, dict(params)))
            if method == "task_get":
                reads += 1
                return confirmed if reads == 3 else unconfirmed
            if method == "events_read":
                return {"head": 7}
            if method == "events_wait":
                return {"head": 8}
            if method == "workflow_get":
                return {"state": "delivered", "actions": confirmed["actions"],
                        "activeRequest": None, "finalArtifactId": "art-1"}
            if method == "task_result":
                return {"result": {"status": "completed"}}
            self.fail(f"unexpected call {method}")
        answer = await_run({"runId": "run-1"}, self.state_dir, service=service,
                           clock=self.clock, scoped=True)
        self.assertEqual([name for name, _ in calls],
                         ["task_get", "events_read", "task_get", "events_wait", "task_get", "workflow_get", "task_result"])
        self.assertEqual(calls[1][1]["runId"], "run-1")
        self.assertEqual(calls[3][1]["runId"], "run-1")
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
        self.assertEqual(calls, ["task_get"])
        self.assertEqual(answer["reminder"], reminder)
        self.assertEqual(answer["outcome"], "review-reminder")
        self.assertEqual(answer["actions"], run["actions"])

    def test_upgrade_handover_rearms_same_wait_inside_window(self):
        (self.state_dir / "upgrade.json").write_text("{}", encoding="utf-8")
        active = {"runId": "run-1", "state": "running", "workflowState": "executing",
                  "revision": 1, "actions": ["await", "inquire", "cancel", "get"]}
        boundary = {"runId": "run-1", "state": "completed", "workflowState": "awaiting-host",
                    "revision": 2, "resultAvailable": False, "actions": ["continue", "cancel", "get"],
                    "workflow": {"activeRequestId": "req-1", "requestKind": "attention"}}
        waits = 0
        calls = []
        def service(method, params, directory):
            nonlocal waits
            calls.append(method)
            if method == "task_get":
                return active
            if method == "task_wait":
                waits += 1
                if waits == 1:
                    raise ServiceError("SERVICE_UNAVAILABLE", "old daemon detached")
                return boundary
            if method == "workflow_get":
                return {"state": "awaiting-host", "actions": boundary["actions"],
                        "activeRequest": {"requestId": "req-1", "kind": "attention"},
                        "finalArtifactId": None}
            self.fail(f"unexpected call {method}")
        with mock.patch.object(blocking.time, "sleep", side_effect=self.clock.advance):
            answer = await_run({"runId": "run-1", "waitSeconds": 2}, self.state_dir,
                               service=service, clock=self.clock)
        self.assertEqual(calls, ["task_get", "task_wait", "task_wait", "workflow_get"])
        self.assertEqual(answer["requestId"], "req-1")
        self.assertNotIn("error", answer)

    def test_wait_window_expires_without_cancelling_run(self):
        active = {"runId": "run-1", "state": "running", "workflowState": "executing", "revision": 1,
                  "actions": ["await", "inquire", "cancel", "get"],
                  "selectedAttempt": {"issues": [{"code": "native-permission-denied"}]}}
        calls = []
        def service(method, params, directory):
            calls.append(method)
            if method == "task_wait":
                self.clock.advance(1)
            return active
        answer = await_run({"runId": "run-1", "waitSeconds": 1}, self.state_dir,
                           service=service, clock=self.clock)
        self.assertEqual(answer["outcome"], "wait-timeout")
        self.assertTrue(answer["timedOut"])
        self.assertEqual(answer["selectedAttempt"]["issues"], [{"code": "native-permission-denied"}])
        self.assertEqual(calls, ["task_get", "task_wait"])
        self.assertNotIn("cancel", calls)

    def test_client_stop_abandons_only_this_wait(self):
        stop = threading.Event()
        stop.set()
        with self.assertRaises(WaitAbandoned) as raised:
            await_run({"runId": "run-1"}, self.state_dir, stop=stop)
        self.assertEqual(raised.exception.run_id, "run-1")


if __name__ == "__main__":
    unittest.main()
