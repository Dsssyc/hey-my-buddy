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
import time
import unittest
from concurrent.futures import ThreadPoolExecutor

from support import BoardTestCase, _child_environment

from hey_my_buddy.protocol.contracts import CONTROL_NAME, CONTRACT_VERSION, WAIT_NAME, BuddyControl, BuddyWait
from hey_my_buddy.protocol.transport import _read_endpoint
from hey_my_buddy.blackboard.store.db import SCHEMA_VERSION
from hey_my_buddy.protocol.client import BoardClient


class TestNamedContract(BoardTestCase):
    def test_idle_waits_reuse_one_named_connection_across_timeouts(self):
        with self.daemon(env={"HMB_MAX_CONCURRENT": "1", "HMB_WAIT_CAPACITY": "1"}):
            client = BoardClient(self.directory, autostart=False)
            endpoint = self._endpoint()
            with self.rpc_connection(BuddyWait, name=WAIT_NAME, address=endpoint["address"]) as peer:
                for _ in range(3):
                    frame = json.dumps({"token": endpoint["token"], "after": 2**62, "timeoutMs": 50})
                    self.assertTrue(json.loads(peer.events_wait(frame))["timedOut"])
            capacity = client.call("wait_capacity", resource="wait")
            self.assertEqual(capacity["admitted"], 0)
            self.assertEqual(capacity["available"], 1)

    def test_service_stop_wakes_the_remote_wait_resource(self):
        with self.daemon(env={"HMB_MAX_CONCURRENT": "1"}):
            client = BoardClient(self.directory, autostart=False)
            endpoint = self._endpoint()
            with ThreadPoolExecutor(max_workers=1) as executor:
                holder = executor.submit(self._wait_call, endpoint, "events_wait",
                                         {"after": 2**62, "timeoutMs": 30000})
                deadline = time.monotonic() + 3
                while True:
                    capacity = client.call("wait_capacity", resource="wait")
                    if capacity["admitted"] == 1:
                        break
                    self.assertLess(time.monotonic(), deadline)
                    time.sleep(.02)
                client.call("service_control", {"action": "stop", "drainSeconds": 0})
                self.assertEqual(holder.result(timeout=2)["error"]["code"], "SERVICE_STOPPING")

    def _endpoint(self):
        endpoint = _read_endpoint(self.directory)
        self.assertIsNotNone(endpoint, "the daemon must publish a private endpoint")
        return endpoint

    def _wait_call(self, endpoint, operation: str, params: dict) -> dict:
        with self.rpc_connection(BuddyWait, name=WAIT_NAME, address=endpoint["address"]) as wait:
            return json.loads(getattr(wait, operation)(json.dumps({"token": endpoint["token"], **params})))

    def test_every_operation_is_named_and_authenticated(self):
        with self.daemon():
            endpoint = self._endpoint()
            with self.rpc_connection(BuddyControl, name=CONTROL_NAME, address=endpoint["address"]) as board:
                ping = json.loads(board.ping(json.dumps({"token": endpoint["token"]})))
                self.assertEqual(ping["contractVersion"], CONTRACT_VERSION)
                self.assertEqual(ping["serviceId"], endpoint["serviceId"])
                self.assertNotIn("integrity", ping)
                health = json.loads(board.health(json.dumps({"token": endpoint["token"]})))
                self.assertEqual(health["status"], "ok")
                self.assertEqual(health["contractVersion"], CONTRACT_VERSION)
                self.assertEqual(health["schemaVersion"], SCHEMA_VERSION)
                self.assertIn("runtimeIdentity", health)
                self.assertNotIn("backupPreflight", health)
                self.assertTrue(hasattr(board, "execution_result"))
                self.assertFalse(hasattr(board, "worker_claim"))
                self.assertTrue(hasattr(board, "events_read"))
                self.assertFalse(hasattr(board, "dispatch"), "no generic dispatch facade may exist")
                denied = json.loads(board.task_list(json.dumps({"token": "wrong"})))
                self.assertEqual(denied["error"]["code"], "UNAUTHORIZED")
                rejected = json.loads(
                    board.task_list(json.dumps({"token": endpoint["token"], "nonsense": 1}))
                )
                self.assertEqual(rejected["error"]["code"], "INVALID_ARGUMENT")
                for operation, params in (("macro_task_deletion_preview", {"macroTaskId": "macro-private"}),
                        ("macro_task_delete", {"macroTaskId": "macro-private", "microTaskCount": 0, "confirm": True})):
                    denied = json.loads(getattr(board, operation)(json.dumps({"token": endpoint["token"], **params})))
                    self.assertEqual(denied["error"]["code"], "UNAUTHORIZED")

    def test_the_wait_route_is_a_separate_resource_with_its_own_capacity(self):
        with self.daemon(env={"HMB_WAIT_CAPACITY": "1"}):
            endpoint = self._endpoint()
            capacity = self._wait_call(endpoint, "wait_capacity", {})
            self.assertEqual(capacity["capacity"], 1)
            with self.rpc_connection(BuddyControl, name=CONTROL_NAME, address=endpoint["address"]) as board:
                reply = self.submit_execution(
                    requestId="wait-route", task="do", cwd=str(self.workdir()),
                    adapter="command", argv=[sys.executable, "-c", "import time; time.sleep(30)"],
                    expectedSeconds=60,
                )
                self.assertEqual(reply["task"]["status"], "queued")
                task_id = reply["task"]["runId"]
                # The attempt becomes `executing` only when its process reports in,
                # in the same transaction that appends attempt.progress; waiting for
                # that state leaves the cancellation below as the only later event of
                # this task, so nothing else can end the scoped wait.
                deadline = time.monotonic() + 10
                while json.loads(board.task_get(json.dumps({"token": endpoint["token"],
                                                           "runId": task_id})))["task"].get("attemptState") != "executing":
                    self.assertLess(time.monotonic(), deadline)
                    time.sleep(0.02)
                cursor = json.loads(board.events_read(json.dumps({"token": endpoint["token"]})))["cursor"]
                # Scope the wait to this task; unrelated events cannot release it.
                params = {"after": cursor, "taskId": task_id, "timeoutMs": 10000}
                with ThreadPoolExecutor(max_workers=1) as pool:
                    holder = pool.submit(self._wait_call, endpoint, "events_wait", params)
                    deadline = time.monotonic() + 5
                    while self._wait_call(endpoint, "wait_capacity", {})["admitted"] != 1:
                        if holder.done():
                            self.fail(f"The holder returned before admission was observed: {holder.result()}")
                        if time.monotonic() >= deadline:
                            self.fail("The holder did not acquire the wait slot")
                        time.sleep(0.02)

                    # An unrelated event must not release the scoped wait slot.
                    unrelated = self.submit_execution(
                        requestId="wait-route-unrelated", task="unrelated", cwd=str(self.workdir()),
                        adapter="command", argv=[sys.executable, "-c", "pass"],
                    )
                    self.assertNotIn("error", unrelated)
                    overloaded = self._wait_call(endpoint, "events_wait", {**params, "timeoutMs": 100})
                    self.assertIn("error", overloaded, overloaded)
                    self.assertEqual(overloaded["error"]["code"], "WAIT_OVERLOAD")
                    self.assertEqual(overloaded["error"]["details"]["retryAfterMs"], 250)
                    self.assertIn("cursor", overloaded["error"]["details"])
                    # Control capacity remains available; this mutation also wakes
                    # the holder through the event it was actually waiting for.
                    started = time.monotonic()
                    cancelled = json.loads(board.task_cancel(json.dumps({
                        "token": endpoint["token"], "runId": task_id,
                    })))
                    self.assertTrue(cancelled["task"]["cancelRequested"])
                    self.assertLess(time.monotonic() - started, 5, "a mutation must not wait for the wait route")
                    held = holder.result(timeout=5)
                    self.assertFalse(held["timedOut"])
                    self.assertTrue(held["events"])
                    self.assertTrue(all(event["taskId"] == task_id for event in held["events"]))

    def test_multiple_clients_share_one_authoritative_task(self):
        with self.daemon():
            endpoint = self._endpoint()
            params = {"requestId": "shared", "task": "do", "cwd": str(self.workdir()),
                      "adapter": "command", "argv": [sys.executable, "-c", "pass"]}
            one = self.submit_execution(**params)
            two = self.submit_execution(**params)
            self.assertEqual(one["task"]["runId"], two["task"]["runId"])
            self.assertTrue(two["duplicate"])
            with self.rpc_connection(BuddyControl, name=CONTROL_NAME, address=endpoint["address"]) as first:
                with self.rpc_connection(BuddyControl, name=CONTROL_NAME, address=endpoint["address"]) as second:
                    selector = json.dumps({"token": endpoint["token"], "requestId": "shared"})
                    first_view = json.loads(first.task_get(selector))
                    second_view = json.loads(second.task_get(selector))
                    self.assertEqual(first_view["task"]["runId"], one["task"]["runId"])
                    self.assertEqual(second_view["task"]["runId"], one["task"]["runId"])

    def test_a_second_daemon_refuses_to_share_the_state_directory(self):
        with self.daemon():
            code, health = self.cli("health")
            self.assertEqual(code, 0, health)
            environment = _child_environment(self.directory)
            second = subprocess.run(
                [sys.executable, "-m", "hey_my_buddy.blackboard.service.daemon"], env=environment, capture_output=True, text=True, timeout=60
            )
            self.assertEqual(second.returncode, 2)
            self.assertIn("ALREADY_RUNNING", second.stderr)


if __name__ == "__main__":
    unittest.main()
