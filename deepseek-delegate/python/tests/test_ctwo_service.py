"""Real cross-process C-Two board calls over the named contract.

The previous version of this file drove an owned mock Node engine. The engine is
gone: this file now exercises the actual Python blackboard service over the actual
C-Two transport, including its token check, its named operations and its dedicated
bounded wait route.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
import unittest

import c_two as cc

from support import PYTHON_ROOT, BoardTestCase

from buddy.contracts import CONTROL_NAME, CONTRACT_VERSION, WAIT_NAME, BuddyControl, BuddyWait
from buddy.transport import _read_endpoint

SCHEMA_VERSION = 5


class TestNamedContract(BoardTestCase):
    def _endpoint(self):
        endpoint = _read_endpoint(self.directory)
        self.assertIsNotNone(endpoint, "the daemon must publish a private endpoint")
        return endpoint

    def _wait_call(self, endpoint, operation: str, params: dict) -> dict:
        with cc.connect(BuddyWait, name=WAIT_NAME, address=endpoint["address"]) as wait:
            return json.loads(getattr(wait, operation)(json.dumps({"token": endpoint["token"], **params})))

    def test_every_operation_is_named_and_authenticated(self):
        with self.daemon():
            endpoint = self._endpoint()
            with cc.connect(BuddyControl, name=CONTROL_NAME, address=endpoint["address"]) as board:
                health = json.loads(board.health(json.dumps({"token": endpoint["token"]})))
                self.assertEqual(health["status"], "ok")
                self.assertEqual(health["contractVersion"], CONTRACT_VERSION)
                self.assertEqual(health["schemaVersion"], SCHEMA_VERSION)
                self.assertIn("runtimeIdentity", health)
                self.assertTrue(hasattr(board, "worker_claim"))
                self.assertTrue(hasattr(board, "events_read"))
                self.assertFalse(hasattr(board, "dispatch"), "no generic dispatch facade may exist")
                denied = json.loads(board.task_list(json.dumps({"token": "wrong"})))
                self.assertEqual(denied["error"]["code"], "UNAUTHORIZED")
                rejected = json.loads(
                    board.task_list(json.dumps({"token": endpoint["token"], "nonsense": 1}))
                )
                self.assertEqual(rejected["error"]["code"], "INVALID_ARGUMENT")

    def test_the_wait_route_is_a_separate_resource_with_its_own_capacity(self):
        with self.daemon(env={"BUDDY_WAIT_CAPACITY": "1"}):
            endpoint = self._endpoint()
            capacity = self._wait_call(endpoint, "wait_capacity", {})
            self.assertEqual(capacity["capacity"], 1)
            held: list[dict] = []
            ready = threading.Event()
            # Wait at the CURRENT cursor so the holder really blocks and keeps its slot.
            cursor = self._wait_call(endpoint, "events_wait", {"after": 0, "timeoutMs": 100})["cursor"]

            def hold():
                ready.set()
                held.append(self._wait_call(endpoint, "events_wait", {"after": cursor, "timeoutMs": 10000}))

            holder = threading.Thread(target=hold)
            holder.start()
            ready.wait(timeout=10)
            # The holder keeps its admitted slot for 10 s; this probe runs strictly
            # inside that window, so saturation is observable rather than racy.
            time.sleep(1.0)
            overloaded = self._wait_call(endpoint, "events_wait", {"after": cursor, "timeoutMs": 100})
            self.assertIn("error", overloaded, overloaded)
            self.assertEqual(overloaded["error"]["code"], "WAIT_OVERLOAD")
            self.assertEqual(overloaded["error"]["details"]["retryAfterMs"], 250)
            self.assertIn("cursor", overloaded["error"]["details"])
            # Control capacity is untouched: a mutation still commits immediately.
            started = time.monotonic()
            with cc.connect(BuddyControl, name=CONTROL_NAME, address=endpoint["address"]) as board:
                reply = json.loads(
                    board.task_submit(
                        json.dumps(
                            {
                                "token": endpoint["token"],
                                "requestId": "wait-route",
                                "task": "do",
                                "cwd": str(self.workdir()),
                            }
                        )
                    )
                )
                self.assertEqual(reply["task"]["status"], "queued")
            self.assertLess(time.monotonic() - started, 5, "a mutation must not wait for the wait route")
            holder.join(timeout=30)
            self.assertEqual(len(held), 1)

    def test_multiple_clients_share_one_authoritative_task(self):
        with self.daemon():
            endpoint = self._endpoint()
            payload = json.dumps(
                {"token": endpoint["token"], "requestId": "shared", "task": "do", "cwd": str(self.workdir())}
            )
            with cc.connect(BuddyControl, name=CONTROL_NAME, address=endpoint["address"]) as first:
                with cc.connect(BuddyControl, name=CONTROL_NAME, address=endpoint["address"]) as second:
                    one = json.loads(first.task_submit(payload))
                    two = json.loads(second.task_submit(payload))
                    self.assertEqual(one["task"]["runId"], two["task"]["runId"])
                    self.assertTrue(two["duplicate"])

    def test_a_second_daemon_refuses_to_share_the_state_directory(self):
        with self.daemon():
            code, health = self.cli("health")
            self.assertEqual(code, 0, health)
            environment = {
                **os.environ,
                "BUDDY_STATE_DIR": str(self.directory),
                "PYTHONPATH": str(PYTHON_ROOT),
                "VIRTUAL_ENV": "",
            }
            second = subprocess.run(
                [sys.executable, "-m", "buddy.daemon"], env=environment, capture_output=True, text=True, timeout=60
            )
            self.assertEqual(second.returncode, 2)
            self.assertIn("ALREADY_RUNNING", second.stderr)


if __name__ == "__main__":
    unittest.main()
