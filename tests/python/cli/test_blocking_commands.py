"""Await failure and internal observation routes expose no command templates."""
from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from hey_my_buddy.cli.blocking import await_run
from hey_my_buddy.protocol.transport import ServiceError, INTERNAL_METHOD_MAP, METHOD_MAP


class AwaitRouteTests(unittest.TestCase):
    def test_service_failure_keeps_run_identity_and_current_actions(self):
        with tempfile.TemporaryDirectory() as directory:
            calls = []
            def service(method, params, state_dir):
                calls.append(method)
                if len(calls) == 1:
                    return {"runId": "run-1", "state": "running", "workflowState": "executing", "revision": 1,
                            "actions": ["await", "inquire", "cancel", "get"]}
                raise ServiceError("SERVICE_UNAVAILABLE", "service restarted")
            answer = await_run({"runId": "run-1"}, Path(directory), service=service)
        self.assertEqual(calls, ["task_observe", "task_observe"])
        self.assertEqual(answer["outcome"], "unavailable")
        self.assertEqual(answer["runId"], "run-1")
        self.assertEqual(answer["error"]["code"], "SERVICE_UNAVAILABLE")
        self.assertEqual(answer["actions"], ["await", "inquire", "cancel", "get"])
        self.assertNotIn("commands", answer)
        self.assertNotIn("nextCommands", answer)

    def test_observation_names_are_private_to_await_and_clients(self):
        self.assertEqual(INTERNAL_METHOD_MAP["task_get"], "task_get")
        self.assertEqual(INTERNAL_METHOD_MAP["task_result"], "task_result")
        self.assertEqual(INTERNAL_METHOD_MAP["task_observe"], "task_observe")
        self.assertNotIn("task_wait", INTERNAL_METHOD_MAP)
        self.assertNotIn("events_wait", INTERNAL_METHOD_MAP)
        for name in ("task_get", "task_result", "task_observe", "task_wait", "events_wait", "events_read"):
            self.assertNotIn(name, METHOD_MAP)


if __name__ == "__main__":
    unittest.main()
