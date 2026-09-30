"""Daemon wiring of the shared ceiling: BUDDY_MAX_CONCURRENT is the only limit.

BUDDY_MAX_CONCURRENT (default 8, range 1–32) sizes both the store's admission
ceiling and the managed supervisor pool. The separate BUDDY_MAX_DECISIONS setting
and its reserved lane are gone: routing and execution share one ceiling and the
same per-family counters.
"""
from __future__ import annotations

import os
import fcntl
import subprocess
import sys
import threading
import unittest
import uuid
from contextlib import contextmanager
from unittest.mock import patch

from support import BoardTestCase, PYTHON_ROOT
from buddy.db import SCHEMA_VERSION

#: Tests run inside a Buddy-managed process may inherit runtime, worker and agent
#: credential variables. Every daemon construction here starts from a clean base so
#: the observed ceiling and pool come from the test's own input only.
CLEAN_ENV = {
    key: value
    for key, value in os.environ.items()
    if not key.startswith("BUDDY_") and key not in ("VIRTUAL_ENV", "UV_PROJECT_ENVIRONMENT")
}


@contextmanager
def clean_buddy_env(**overrides):
    environment = {**CLEAN_ENV,
                   "BUDDY_CONSOLE_PORT": "0",
                   "BUDDY_CLAUDE_CLI": str(PYTHON_ROOT.parent / "tests/python/fixtures/claude-not-installed"),
                   **overrides}
    with patch.dict(os.environ, environment, clear=True):
        yield


class DaemonCeilingTests(BoardTestCase):
    def make_daemon(self, name: str, **env):
        from buddy.daemon import Daemon

        with clean_buddy_env(**env):
            return Daemon(self.directory / f"state-{name}")

    def test_the_default_ceiling_is_eight_slots_and_eight_supervisors(self):
        daemon = self.make_daemon("default")
        self.assertEqual(daemon.store.max_concurrent, 8)
        self.assertEqual(len(daemon.pool.worker_ids), 8)
        self.assertEqual(daemon.pool.worker_ids[0], "local")
        self.assertEqual(daemon.pool.worker_ids[-1], "local-8")

    def test_buddy_max_concurrent_sizes_the_store_and_the_pool_together(self):
        daemon = self.make_daemon("three", BUDDY_MAX_CONCURRENT="3")
        self.assertEqual(daemon.store.max_concurrent, 3)
        self.assertEqual(daemon.pool.worker_ids, ["local", "local-2", "local-3"])
        self.assertEqual(daemon.pool.total_limit, 3)

    def test_the_ceiling_clamps_to_the_documented_one_to_thirty_two_range(self):
        self.assertEqual(self.make_daemon("high", BUDDY_MAX_CONCURRENT="99").store.max_concurrent, 32)
        self.assertEqual(self.make_daemon("low", BUDDY_MAX_CONCURRENT="0").store.max_concurrent, 1)
        self.assertEqual(len(self.make_daemon("low2", BUDDY_MAX_CONCURRENT="0").pool.worker_ids), 1)

    def test_buddy_max_decisions_no_longer_exists_as_a_separate_limit(self):
        daemon = self.make_daemon("nodecisions", BUDDY_MAX_CONCURRENT="4", BUDDY_MAX_DECISIONS="3")
        self.assertFalse(hasattr(daemon.store, "decision_concurrent"))
        self.assertEqual(daemon.pool.total_limit, 4)
        self.assertEqual(len(daemon.pool.worker_ids), 4)


class DaemonPoolLifecycleTests(BoardTestCase):
    def private_daemon(self, name):
        from buddy.daemon import Daemon

        with clean_buddy_env():
            daemon = Daemon(self.directory / name)
        daemon.store.initialize()
        return daemon

    def test_stop_waits_for_initial_late_slots_and_keeps_every_stop_intent(self):
        from buddy.daemon import SupervisorHandle

        daemon = self.private_daemon("late-start")
        blocked, release, stopping, stopped = (threading.Event() for _ in range(4))
        failures = []

        def delayed_start(handle, *_args, **_kwargs):
            if handle.worker_id == "local-7":
                blocked.set()
                if not release.wait(5):
                    raise AssertionError("late start was not released")
            handle.prepare()
            handle.stop_request.unlink(missing_ok=True)
            return True

        def stop():
            stopping.set()
            try:
                daemon.on_stop({"drainSeconds": 0})
            except BaseException as error:
                failures.append(error)
            finally:
                stopped.set()

        with patch.object(SupervisorHandle, "start", delayed_start):
            starter = threading.Thread(target=daemon._start_pool)
            starter.start()
            self.assertTrue(blocked.wait(5))
            stopper = threading.Thread(target=stop)
            stopper.start()
            try:
                self.assertTrue(stopping.wait(5))
                self.assertFalse(stopped.wait(.1), "stop bypassed an in-flight pool start")
            finally:
                release.set()
                starter.join(5)
                stopper.join(5)
        self.assertFalse(starter.is_alive() or stopper.is_alive())
        self.assertEqual(failures, [])
        self.assertTrue(all(daemon.pool.handle(worker).stop_request.exists() for worker in daemon.pool.worker_ids))

    def test_signal_style_reentry_cannot_erase_late_slot_stop_intents(self):
        from buddy.daemon import SupervisorHandle

        daemon = self.private_daemon("reentrant-start")

        def interrupted_start(handle, *_args, **_kwargs):
            handle.prepare()
            if handle.worker_id == "local-7":
                daemon.on_stop({"drainSeconds": 0})
            handle.stop_request.unlink(missing_ok=True)
            return True

        with patch.object(SupervisorHandle, "start", interrupted_start):
            daemon._start_pool()
        self.assertTrue(all(daemon.pool.handle(worker).stop_request.exists() for worker in daemon.pool.worker_ids))

    def test_signal_style_reentry_during_reconcile_preserves_stop_intents(self):
        daemon = self.private_daemon("reentrant-reconcile")

        def interrupted_reconcile(_store):
            daemon.on_stop({"drainSeconds": 0})
            daemon.pool.handle("local-8").stop_request.unlink()
            return daemon.pool.report()

        with patch.object(daemon.pool, "reconcile", interrupted_reconcile):
            daemon._reconcile_pool()
        self.assertTrue(daemon.pool.handle("local-8").stop_request.exists())

    def test_failed_late_start_drains_spawned_processes_but_preserves_reused_workers(self):
        from buddy.daemon import SupervisorHandle
        from buddy.errors import BoardError

        daemon = self.private_daemon("failed-start")
        reused = daemon.pool.handle("local")
        reused.prepare()
        owned = []
        child_code = (
            "import fcntl,sys,time; from pathlib import Path; p=Path(sys.argv[1]); "
            "f=(p/'supervisor.lock').open('a'); fcntl.flock(f,fcntl.LOCK_EX); "
            "print('ready',flush=True)\n"
            "while not Path(sys.argv[2]).exists(): time.sleep(.01)\n"
        )

        def failing_start(handle, *_args, **_kwargs):
            if handle.worker_id == "local":
                return False
            if handle.worker_id == "local-7":
                raise OSError("injected seventh-slot spawn failure")
            handle.prepare()
            handle.start_stop_request = handle.directory / f"stop-{uuid.uuid4().hex}.request"
            handle.process = subprocess.Popen([sys.executable, "-c", child_code, str(handle.directory), str(handle.start_stop_request)],
                                              stdout=subprocess.PIPE, text=True)
            owned.append(handle)
            self.assertEqual(handle.process.stdout.readline().strip(), "ready")
            return True

        with reused.lock_path.open("a") as retained_lock:
            fcntl.flock(retained_lock, fcntl.LOCK_EX)
            try:
                with patch.object(SupervisorHandle, "start", failing_start):
                    with self.assertRaises(BoardError) as raised:
                        daemon._start_pool()
                self.assertEqual(raised.exception.code, "WORKER_POOL_START_FAILED")
                self.assertEqual(len(raised.exception.details["workers"]), 5)
                self.assertTrue(all(row["supervisorExited"] for row in raised.exception.details["workers"]))
                self.assertFalse(reused.stop_request.exists(), "failed startup must preserve pre-existing work")
                for handle in owned:
                    self.assertIsNotNone(handle.process.poll())
                    with handle.lock_path.open("a") as lock:
                        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            finally:
                for handle in owned:
                    handle.start_stop_request.touch()
                    handle.process.wait(timeout=5)
                    handle.process.stdout.close()

    def test_failed_start_never_stops_another_owner_after_its_child_lost_the_lock(self):
        daemon = self.private_daemon("lost-start-race")
        handle = daemon.pool.handle("local")
        handle.prepare()
        handle.start_stop_request = handle.directory / f"stop-{uuid.uuid4().hex}.request"
        handle.process = subprocess.Popen([sys.executable, "-c", "pass"])
        handle.process.wait(timeout=5)
        with handle.lock_path.open("a") as other_owner:
            fcntl.flock(other_owner, fcntl.LOCK_EX)
            evidence = daemon.pool.stop_spawned()
            self.assertTrue(evidence[0]["supervisorExited"])
            self.assertFalse(handle.stop_request.exists())
            self.assertFalse(handle.start_stop_request.exists())
            self.assertTrue(handle.running(), "the other owner must retain its lock")

    def test_failed_start_drains_a_real_supervisor_through_its_private_instance_request(self):
        from buddy.daemon import SupervisorHandle
        from buddy.errors import BoardError

        daemon = self.private_daemon("real-failed-start")
        original_start = SupervisorHandle.start

        def fail_second(handle, *args, **kwargs):
            if handle.worker_id == "local-2":
                raise OSError("injected after real supervisor spawn")
            return original_start(handle, *args, **kwargs)

        target = {"python": sys.executable, "pythonPath": str(PYTHON_ROOT), "stable": False}
        handle = daemon.pool.handle("local")
        try:
            with patch("buddy.daemon.runtime.launch_target", return_value=target), patch.object(SupervisorHandle, "start", fail_second):
                with self.assertRaises(BoardError) as raised:
                    daemon._start_pool()
            self.assertEqual(len(raised.exception.details["workers"]), 1)
            self.assertTrue(raised.exception.details["workers"][0]["supervisorExited"])
            self.assertFalse(handle.stop_request.exists(), "failure cleanup must not write the shared stop")
            self.assertFalse(handle.running())
        finally:
            if handle.process is not None:
                if handle.process.poll() is None:
                    handle.start_stop_request.touch()
                handle.process.wait(timeout=10)

    def test_private_start_abort_targets_only_the_matching_supervisor_instance(self):
        from buddy.worker.supervisor import Supervisor
        from buddy.worker.worker import Worker

        with patch.dict(os.environ, {"BUDDY_SUPERVISOR_START_ID": "1" * 32}):
            first = Supervisor("shared", self.directory)
            first_worker = Worker("shared", self.directory)
        with patch.dict(os.environ, {"BUDDY_SUPERVISOR_START_ID": "2" * 32}):
            other = Supervisor("shared", self.directory)
            other_worker = Worker("shared", self.directory)
        first.directory.mkdir(parents=True, exist_ok=True)
        first.start_stop_request.touch()
        self.assertTrue(first.stop_file_requested())
        self.assertTrue(first_worker.stop_requested())
        self.assertFalse(other.stop_file_requested())
        self.assertFalse(other_worker.stop_requested())

    def test_signal_style_reentry_followed_by_spawn_error_keeps_every_stop_intent(self):
        from buddy.daemon import SupervisorHandle
        from buddy.errors import BoardError

        daemon = self.private_daemon("failed-reentrant-start")

        def interrupted_start(handle, *_args, **_kwargs):
            handle.prepare()
            if handle.worker_id == "local-7":
                daemon.on_stop({"drainSeconds": 0})
                handle.stop_request.unlink()
                raise OSError("injected after signal-style stop")
            return True

        with patch.object(SupervisorHandle, "start", interrupted_start), self.assertRaises(BoardError):
            daemon._start_pool()
        self.assertTrue(all(daemon.pool.handle(worker).stop_request.exists() for worker in daemon.pool.worker_ids))


class DaemonHealthTests(BoardTestCase):
    def test_a_running_daemon_reports_the_shared_ceiling_and_pool(self):
        from buddy.transport import _read_endpoint, _request

        with clean_buddy_env():
            with self.daemon(env={"BUDDY_MAX_CONCURRENT": "2", "BUDDY_WORKER_ID": "local"}) as process:
                self.assertNotEqual(process.poll(), 0)
                endpoint = _read_endpoint(self.directory)
                health = _request(endpoint, "health", {})
                self.assertEqual(health["schemaVersion"], SCHEMA_VERSION)
                self.assertEqual(health["maxConcurrent"], 2)
                self.assertEqual(set(health["capacity"]), {"totalLimit", "totalActive", "models"})
                self.assertEqual(health["capacity"]["totalLimit"], 2)
                self.assertEqual(health["capacity"]["totalActive"], 0)
                self.assertEqual(health["managedWorkerIds"], ["local", "local-2"])


if __name__ == "__main__":
    unittest.main()
