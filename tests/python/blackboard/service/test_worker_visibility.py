"""Supervisor observations remain separate from authoritative worker rows."""
from __future__ import annotations

import json
import threading
from unittest.mock import patch

from support import BoardTestCase, wait_for
from hey_my_buddy.buddy.runtime.supervisor import Supervisor
from hey_my_buddy.buddy.runtime.worker import Worker


class WorkerVisibilityTests(BoardTestCase):
    def test_unregistered_and_surplus_workers_are_visible_without_writing_files(self):
        board = self.board()
        board.control["worker_pool"] = {"workerIds": ["local"], "supervisedWorkerIds": ["local", "surplus", "missing"]}
        path = self.directory / "workers/local/supervisor.json"
        path.parent.mkdir(parents=True)
        status = {"workerId": "local", "state": "restarting", "lastError": "RuntimeError('startup')",
                  "startedAt": "2026-10-15T01:00:00Z", "updatedAt": "2026-10-15T01:01:00Z"}
        path.write_text(json.dumps(status))
        surplus_path = self.directory / "workers/surplus/supervisor.json"
        surplus_path.parent.mkdir(parents=True)
        surplus_path.write_text(json.dumps({**status, "workerId": "surplus", "lastError": "surplus startup failed"}))
        before = path.read_bytes(), path.stat().st_mtime_ns
        health = board.call("health", {})
        workers = board.call("worker_list", {"state": "idle", "limit": 1})
        self.assertEqual(health["managedWorkerIds"], ["local"])
        self.assertEqual(workers["workers"], [])
        self.assertEqual(workers["workerSupervisors"], health["workerSupervisors"])
        self.assertEqual(health["workerSupervisors"][0]["supervisor"], {
            "state": "restarting", "lastError": status["lastError"], "lastErrorAt": status["updatedAt"],
            "startedAt": status["startedAt"], "updatedAt": status["updatedAt"]})
        self.assertEqual([row["workerId"] for row in health["workerSupervisors"]], ["local", "surplus", "missing"])
        self.assertEqual(health["workerSupervisors"][1]["supervisor"]["state"], "restarting")
        self.assertEqual(health["workerSupervisors"][1]["supervisor"]["lastError"], "surplus startup failed")
        self.assertEqual(health["workerSupervisors"][2]["supervisor"]["state"], "unknown")
        self.assertEqual((path.read_bytes(), path.stat().st_mtime_ns), before)
        self.assertFalse((self.directory / "workers/missing").exists())

    def test_invalid_observations_are_unknown_in_both_queries(self):
        board = self.board()
        board.control["worker_pool"] = {"workerIds": ["local"], "supervisedWorkerIds": ["local"]}
        board.client().register_worker("local", adapter="command")
        path = self.directory / "workers/local/supervisor.json"
        path.parent.mkdir(parents=True)
        valid = {"workerId": "local", "state": "running", "startedAt": "2026-10-15T01:00:00Z",
                 "updatedAt": "2026-10-15T01:01:00Z"}
        for value in (None, "{", "[]", json.dumps({}),
                      *(json.dumps({**valid, **change}) for change in (
                          {"workerId": "other"}, {"state": "healthy"}, {"state": []},
                          {"startedAt": "bad"}, {"updatedAt": 5}, {"updatedAt": "2026-10-15"},
                          {"lastError": {}}, {"state": "restarting"}))):
            with self.subTest(value=value):
                if value is not None:
                    path.write_text(value)
                health = board.call("health", {})["workerSupervisors"][0]["supervisor"]
                worker = board.call("worker_list", {})["workers"][0]
                self.assertEqual(health["state"], "unknown")
                self.assertIsNone(health["startedAt"])
                self.assertEqual(worker["supervisor"], health)
                self.assertEqual(worker["state"], "idle")
        for state in ("starting", "running", "retiring", "retired", "stopped"):
            with self.subTest(state=state):
                path.write_text(json.dumps({**valid, "state": state}))
                self.assertEqual(board.call("health", {})["workerSupervisors"][0]["supervisor"]["state"], state)

    def test_private_service_and_cli_observe_startup_failure_then_recovery(self):
        fail = threading.Event()
        fail.set()
        registered = threading.Event()
        supervisor = Supervisor("local", self.directory)
        errors = []

        def worker_run(worker):
            if fail.is_set():
                raise RuntimeError("injected Worker startup failure")
            worker.register()
            registered.set()
            worker.stop.wait(15)

        def serve():
            try:
                supervisor.serve()
            except BaseException as error:
                errors.append(error)

        with patch.object(Worker, "run", worker_run), patch(
                "hey_my_buddy.buddy.runtime.supervisor.RESTART_BACKOFF_SECONDS", .2):
            thread = threading.Thread(target=serve)
            thread.start()
            try:
                self.assertTrue(wait_for(lambda: supervisor.status_path.exists()))
                with self.daemon(env={"HMB_MAX_CONCURRENT": "1"}):
                    code, health = self.cli("health")
                    self.assertEqual(code, 0, health)
                    failed = health["workerSupervisors"][0]["supervisor"]
                    self.assertEqual(failed["state"], "restarting")
                    self.assertEqual(failed["lastError"], "RuntimeError('injected Worker startup failure')")
                    self.assertEqual(failed["lastErrorAt"], failed["updatedAt"])
                    self.assertIsNotNone(failed["startedAt"])
                    code, workers = self.cli("workers")
                    self.assertEqual(code, 0, workers)
                    self.assertEqual(workers["workers"], [])
                    self.assertEqual(workers["workerSupervisors"][0]["supervisor"]["state"], "restarting")
                    fail.clear()
                    self.assertTrue(registered.wait(10), errors)
                    code, health = self.cli("health")
                    self.assertEqual(code, 0, health)
                    recovered = health["workerSupervisors"][0]["supervisor"]
                    self.assertEqual(recovered["state"], "running")
                    self.assertEqual(recovered["startedAt"], failed["startedAt"])
                    self.assertIsNone(recovered["lastError"])
                    self.assertIsNone(recovered["lastErrorAt"])
                    code, workers = self.cli("workers")
                    self.assertEqual(code, 0, workers)
                    self.assertEqual(workers["workers"][0]["state"], "idle")
                    self.assertEqual(workers["workers"][0]["supervisor"], recovered)
            finally:
                supervisor.request_stop()
                thread.join(20)
        self.assertFalse(thread.is_alive())
        self.assertEqual(errors, [])
