"""Real cross-process C-Two board calls over the named contract.

The previous version of this file drove an owned mock Node engine. The engine is
gone: this file now exercises the actual Python blackboard service over the actual
C-Two transport, including its token check, its named operations and its
multiplexed board_changed topic wakes on BuddyControl.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import unittest
from concurrent.futures import ThreadPoolExecutor

from support import BoardTestCase, _child_environment, request_private_service_stop, wait_for

from hey_my_buddy.protocol.contracts import CONTROL_NAME, CONTRACT_VERSION, BuddyControl
from hey_my_buddy.protocol.transport import ServiceError, _read_endpoint, open_wait_session, wait_events
from hey_my_buddy.blackboard.store.db import SCHEMA_VERSION
from hey_my_buddy.protocol.client import BoardClient


class TestNamedContract(BoardTestCase):
    def test_relocate_crosses_the_named_contract_and_rejects_extra_fields(self):
        repository = self.workdir("repository")
        subprocess.run(["git", "init", "-q", str(repository)], check=True,
                       env=_child_environment(self.directory), capture_output=True)
        with self.daemon():
            client = BoardClient(self.directory, autostart=False)
            created = client.workflow_create(cwd=str(repository), title="Relocation", goal="Track project")
            project = client.workflow_get(macroTaskId=created["macroTaskId"])["project"]
            moved = self.directory / "moved-repository"
            repository.rename(moved)
            result = client.relocate(cwd=str(moved))
            self.assertEqual(result["project"], {"id": project["id"], "kind": "git",
                                                 "path": str(moved), "label": "moved-repository"})
            self.assertIn("project_relocate", client.capabilities()["operations"]["control"])
            endpoint = self._endpoint()
            with self.rpc_connection(BuddyControl, name=CONTROL_NAME, address=endpoint["address"]) as control:
                denied = json.loads(control.project_relocate(json.dumps({"token": "wrong", "cwd": str(moved)})))
                self.assertEqual(denied["error"]["code"], "UNAUTHORIZED")
                rejected = json.loads(control.project_relocate(json.dumps({"token": endpoint["token"],
                                                                           "cwd": str(moved), "projectId": "forged"})))
                self.assertEqual(rejected["error"]["code"], "INVALID_ARGUMENT")

    def test_idle_waits_reuse_one_named_connection_across_timeouts(self):
        with self.daemon(env={"HMB_MAX_CONCURRENT": "1"}):
            client = BoardClient(self.directory, autostart=False)
            with open_wait_session(self.directory) as session:
                session.subscribe()
                self.assertTrue(wait_for(lambda: client.call("health")["activeSubscribers"] == 1, 5))
                for _ in range(3):
                    session.prepare_read()
                    page = session.call("events_read", {"after": 2**62})
                    self.assertEqual(page["events"], [])
                    self.assertFalse(session.wait_wake(0.05))
            self.assertTrue(wait_for(lambda: client.call("health")["activeSubscribers"] == 0, 5))

    def test_service_stop_wakes_remote_topic_subscribers(self):
        with self.daemon(env={"HMB_MAX_CONCURRENT": "1"}):
            client = BoardClient(self.directory, autostart=False)
            with ThreadPoolExecutor(max_workers=1) as executor:
                def wait_for_stop() -> ServiceError | None:
                    try:
                        wait_events({"after": 2**62, "timeoutMs": 30000}, self.directory)
                        return None
                    except ServiceError as error:
                        return error

                holder = executor.submit(wait_for_stop)
                self.assertTrue(wait_for(lambda: client.call("health")["activeSubscribers"] == 1, 5))
                request_private_service_stop(self.directory, client.call, drainSeconds=0)
                caught = holder.result(timeout=5)
                self.assertIsNotNone(caught)
                self.assertIn(caught.code, ("C_TWO_CALL_CANCELLED", "SERVICE_UNAVAILABLE", "C_TWO_IPC_ERROR"))

    def _endpoint(self):
        endpoint = _read_endpoint(self.directory)
        self.assertIsNotNone(endpoint, "the daemon must publish a private endpoint")
        return endpoint

    def _active_subscribers(self, endpoint) -> int:
        with self.rpc_connection(BuddyControl, name=CONTROL_NAME, address=endpoint["address"]) as control:
            return json.loads(control.health(json.dumps({"token": endpoint["token"]})))["activeSubscribers"]

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
                self.assertEqual(health["activeSubscribers"], 0)
                self.assertNotIn("waitCapacity", health)
                self.assertNotIn("waitAdmission", health)
                self.assertTrue(hasattr(board, "execution_result"))
                self.assertTrue(hasattr(board, "task_observe"))
                self.assertTrue(hasattr(board, "board_changed"))
                self.assertFalse(hasattr(board, "worker_claim"))
                for retired in ("workflow_takeover", "workflow_decide", "workflow_reclaim", "console_snapshot",
                                "selection_request", "task_retry", "task_acknowledge", "task_wait", "events_wait"):
                    self.assertFalse(hasattr(board, retired), retired)
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

    def test_topic_wait_wakes_on_matching_task_events_without_blocking_control(self):
        with self.daemon():
            endpoint = self._endpoint()
            with self.rpc_connection(BuddyControl, name=CONTROL_NAME, address=endpoint["address"]) as board:
                reply = self.submit_execution(
                    requestId="wait-route", task="do", cwd=str(self.workdir()),
                    adapter="command", argv=[sys.executable, "-c", "import time\nwhile True: time.sleep(1)"],
                    expectedSeconds=60,
                )
                self.assertEqual(reply["task"]["state"], "queued")
                task_id = reply["task"]["runId"]
                deadline = time.monotonic() + 180
                while True:
                    page = json.loads(board.events_read(json.dumps({
                        "token": endpoint["token"], "runId": task_id})))
                    if any(event["kind"] == "attempt.progress" and event["payload"]["phase"] == "executing"
                           for event in page["events"]):
                        break
                    self.assertLess(time.monotonic(), deadline, page)
                    time.sleep(0.02)
                cursor = page["cursor"]
                params = {"after": cursor, "runId": task_id, "timeoutMs": 10000}
                with ThreadPoolExecutor(max_workers=1) as pool:
                    holder = pool.submit(wait_events, params, self.directory)
                    self.assertTrue(wait_for(lambda: self._active_subscribers(endpoint) == 1, 5))
                    self.assertFalse(holder.done())

                    # An unrelated event must not end the scoped wait.
                    unrelated = self.submit_execution(
                        requestId="wait-route-unrelated", task="unrelated", cwd=str(self.workdir()),
                        adapter="command", argv=[sys.executable, "-c", "pass"], expectedSeconds=1800,
                    )
                    self.assertNotIn("error", unrelated)
                    time.sleep(0.1)
                    self.assertFalse(holder.done())

                    started = time.monotonic()
                    cancelled = json.loads(board.task_cancel(json.dumps({
                        "token": endpoint["token"], "runId": task_id,
                    })))
                    self.assertTrue(cancelled["task"]["cancelRequested"])
                    self.assertLess(time.monotonic() - started, 5, "a mutation must not wait for topic subscribers")
                    held = holder.result(timeout=5)
                    self.assertFalse(held["timedOut"])
                    self.assertTrue(held["events"])
                    self.assertTrue(all(event["taskId"] == task_id for event in held["events"]))
                self.assertTrue(wait_for(lambda: self._active_subscribers(endpoint) == 0, 5))

    def test_multiple_clients_share_one_authoritative_task(self):
        with self.daemon():
            endpoint = self._endpoint()
            params = {"requestId": "shared", "task": "do", "cwd": str(self.workdir()),
                      "adapter": "command", "argv": [sys.executable, "-c", "pass"], "expectedSeconds": 1800}
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
                [sys.executable, "-m", "hey_my_buddy.blackboard.service.daemon"], env=environment, capture_output=True, text=True, timeout=60, encoding="utf-8"
            )
            self.assertEqual(second.returncode, 2)
            self.assertIn("ALREADY_RUNNING", second.stderr)

    def test_quiet_daemon_keyboard_interrupt_exits_promptly_and_cleans_subscriber(self):
        with self.daemon():
            client = BoardClient(self.directory, autostart=False)
            script = (
                "import os, signal, sys\n"
                "from hey_my_buddy.protocol.transport import wait_events\n"
                "try:\n"
                "    wait_events({'after': 2**62, 'timeoutMs': 30000}, os.environ['HMB_STATE_DIR'])\n"
                "except KeyboardInterrupt:\n"
                "    sys.exit(130)\n"
            )
            env = _child_environment(self.directory)
            child = subprocess.Popen([sys.executable, "-c", script], env=env,
                                     stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                     text=True, encoding="utf-8")
            try:
                self.assertTrue(wait_for(lambda: client.call("health")["activeSubscribers"] == 1, 10))
                started = time.monotonic()
                if os.name == "nt":
                    child.terminate()
                else:
                    import signal
                    child.send_signal(signal.SIGINT)
                _out, err = child.communicate(timeout=5)
                elapsed = time.monotonic() - started
                self.assertLess(elapsed, 2.5, err)
                if os.name != "nt":
                    self.assertEqual(child.returncode, 130, err)
            finally:
                if child.poll() is None:
                    child.kill()
                    child.wait(timeout=5)
            self.assertTrue(wait_for(lambda: client.call("health")["activeSubscribers"] == 0, 5))

    def test_store_notify_publishes_on_deletion_without_seq_growth_and_isolates_publish_errors(self):
        board = self.board()
        published: list[int] = []
        board.store.on_publish = published.append
        head = board.store.head()
        before_seq = board.store.change_seq()
        # Macro-task deletion can leave MAX(seq) unchanged while changing task visibility.
        board.store._notify(head, changed_tasks={"deleted-task"})
        self.assertGreater(board.store.change_seq(), before_seq)
        self.assertEqual(len(published), 1)
        self.assertEqual(published[0], board.store.change_seq())
        # A post-commit publish failure must never abort a committed store notification.
        def failing_publish(_value: int) -> None:
            raise RuntimeError("simulated topic publish failure")
        board.store.on_publish = failing_publish
        before_error_seq = board.store.change_seq()
        board.store._notify(head + 1)
        self.assertEqual(board.store.head(), head + 1)
        self.assertGreater(board.store.change_seq(), before_error_seq)


if __name__ == "__main__":
    unittest.main()
