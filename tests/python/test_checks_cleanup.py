"""The checks runner contains every suite inside one private root it can retire."""
from __future__ import annotations

import fcntl
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from buddy import checks


def hold_lock(path: Path) -> int:
    """An open fd that owns ``path``'s flock until the caller closes it."""
    fd = os.open(path, os.O_RDWR)
    fcntl.flock(fd, fcntl.LOCK_EX)
    return fd


def release_lock(fd: int) -> None:
    fcntl.flock(fd, fcntl.LOCK_UN)
    os.close(fd)


def wait_until(predicate, timeout: float = 5.0, interval: float = 0.01):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(interval)
    return None


class ChecksEnvironmentTests(unittest.TestCase):
    def test_environment_is_private_and_unpinned(self):
        inherited = {
            "BUDDY_STATE_DIR": "/wrong-state",
            "BUDDY_RUNTIME_ROOT": "/wrong-runtime",
            "BUDDY_RUNTIME": "/pinned-runtime",
            "BUDDY_RUNTIME_IDENTITY": "pinned",
            "BUDDY_WORKER_STATE": "/wrong-worker",
            "BUDDY_WORKER_ID": "wrong-worker",
            "BUDDY_AGENT_CREDENTIAL": "secret",
            "BUDDY_AGENT_CREDENTIAL_FILE": "/secret-file",
            "VIRTUAL_ENV": "/wrong-venv",
            "UV_PROJECT_ENVIRONMENT": "/wrong-uv",
            "BUDDY_CONSOLE_PORT": "8642",
        }
        with patch.dict(os.environ, inherited):
            environment = checks.test_environment(Path("/repo"))
        for key in checks.SANITIZED_VARIABLES:
            self.assertNotIn(key, environment)
        self.assertEqual(environment["BUDDY_DEV_SOURCE"], "1")
        self.assertEqual(environment["BUDDY_PYTHON"], sys.executable)
        self.assertEqual(environment["BUDDY_CONSOLE_PORT"], "0")
        self.assertTrue(environment["BUDDY_CLAUDE_CLI"].endswith("claude-not-installed"))
        path_entries = environment["PYTHONPATH"].split(os.pathsep)
        self.assertEqual(path_entries[:2], [str(Path("/repo/src")), str(Path("/repo/tests/python"))])

    def test_private_root_and_child_environment_contain_every_temp_file(self):
        root = checks.create_private_root()
        try:
            self.assertTrue(root.name.startswith("buddy-checks-"))
            self.assertEqual(stat.S_IMODE(os.stat(root).st_mode), 0o700)
            tmp = root / "tmp"
            self.assertTrue(tmp.is_dir())
            environment = checks.child_environment(Path("/repo"), root)
            self.assertEqual(environment["TMPDIR"], str(tmp))
            completed = subprocess.run(
                [sys.executable, "-c", "import tempfile; print(tempfile.mkdtemp(prefix='inside-'))"],
                env=environment,
                capture_output=True,
                text=True,
                timeout=60,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(Path(completed.stdout.strip()).parent, tmp)
        finally:
            shutil.rmtree(root, ignore_errors=True)


class ResidueObservationTests(unittest.TestCase):
    def test_lock_is_held_requires_an_owner(self):
        with tempfile.TemporaryDirectory(prefix="buddy-checks-test-") as raw:
            path = Path(raw) / "lifetime.lock"
            path.write_text("")
            self.assertFalse(checks.lock_is_held(path))
            fd = hold_lock(path)
            try:
                self.assertTrue(checks.lock_is_held(path))
            finally:
                release_lock(fd)
            self.assertFalse(checks.lock_is_held(path))
            self.assertFalse(checks.lock_is_held(Path(raw) / "absent.lock"))

    def test_held_locks_report_only_buddy_lifetime_owners(self):
        with tempfile.TemporaryDirectory(prefix="buddy-checks-test-") as raw:
            root = Path(raw)
            state = root / "buddy-test-state"
            (state / "workers/local").mkdir(parents=True)
            for name in ("control-daemon.lock", "board-owner.lock"):
                (state / name).write_text("")
            supervisor = state / "workers/local/supervisor.lock"
            supervisor.write_text("")
            unrelated = state / "unrelated.lock"
            unrelated.write_text("")
            bystander = hold_lock(unrelated)
            fd = hold_lock(supervisor)
            try:
                residue = checks.held_locks(root)
                self.assertEqual(residue["daemon"], [])
                self.assertEqual(residue["supervisor"], [supervisor])
            finally:
                release_lock(fd)
                release_lock(bystander)


class PrivateRootTeardownTests(unittest.TestCase):
    def test_teardown_removes_a_clean_private_root(self):
        root = checks.create_private_root()
        state = root / "tmp" / "buddy-test-clean"
        (state / "workers/local").mkdir(parents=True)
        (state / "control-daemon.lock").write_text("")
        (state / "workers/local/supervisor.lock").write_text("")
        self.assertIsNone(checks.teardown_private_root(root, drain_seconds=0.5))
        self.assertFalse(root.exists())

    def test_teardown_preserves_evidence_when_a_supervisor_survives(self):
        root = checks.create_private_root()
        state = root / "tmp" / "buddy-test-supervisor"
        worker = state / "workers/local"
        worker.mkdir(parents=True)
        (worker / "supervisor.lock").write_text("")
        (state / "worker.log").write_text("supervisor startup\n" + "noise " * 400
                                          + "\nfinal supervisor evidence line\n")
        fd = hold_lock(worker / "supervisor.lock")
        try:
            evidence = checks.teardown_private_root(root, drain_seconds=0.2)
            self.assertIsNotNone(evidence)
            self.assertTrue(root.exists())
            report = json.loads((root / checks.EVIDENCE_FILE_NAME).read_text())
            entry = report["held"]["supervisor"][0]
            self.assertEqual(entry["path"], str(worker / "supervisor.lock"))
            self.assertEqual(entry["directory"], str(worker))
            self.assertIn("stop.request", entry["directoryFiles"])
            state_entry = report["stateDirectories"][str(state)]
            self.assertIn("workers", state_entry["files"])
            self.assertIn("final supervisor evidence line", state_entry["workerLogTail"])
        finally:
            release_lock(fd)
            shutil.rmtree(root, ignore_errors=True)

    def test_teardown_preserves_evidence_when_a_daemon_survives(self):
        root = checks.create_private_root()
        state = root / "tmp" / "buddy-test-daemon"
        state.mkdir(parents=True)
        (state / "control-daemon.lock").write_text("")
        fd = hold_lock(state / "control-daemon.lock")
        try:
            evidence = checks.teardown_private_root(root, drain_seconds=0.2)
            self.assertIsNotNone(evidence)
            self.assertTrue(root.exists())
            report = json.loads((root / checks.EVIDENCE_FILE_NAME).read_text())
            entry = report["held"]["daemon"][0]
            self.assertEqual(entry["path"], str(state / "control-daemon.lock"))
            self.assertEqual(entry["directory"], str(state))
            self.assertIn("control-daemon.lock", entry["directoryFiles"])
            self.assertIn("control-daemon.lock", report["stateDirectories"][str(state)]["files"])
        finally:
            release_lock(fd)
            shutil.rmtree(root, ignore_errors=True)

    def test_drain_reaches_worker_dirs_with_no_visible_lock(self):
        # A racing cleanup can unlink a live supervisor's lock file, leaving its
        # worker directory as the only cooperative surface still observable.
        root = checks.create_private_root()
        worker = root / "tmp" / "buddy-test-orphan" / "workers" / "local"
        worker.mkdir(parents=True)
        checks.request_cooperative_stop(root, timeout=0.1)
        try:
            self.assertTrue((worker / "stop.request").exists())
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_teardown_drains_a_cooperative_supervisor(self):
        root = checks.create_private_root()
        worker = root / "tmp" / "buddy-test-drain" / "workers/local"
        worker.mkdir(parents=True)
        lock = worker / "supervisor.lock"
        lock.write_text("")
        observed = threading.Event()

        def supervise() -> None:
            fd = hold_lock(lock)
            while not (worker / "stop.request").exists():
                time.sleep(0.01)
            observed.set()
            release_lock(fd)

        thread = threading.Thread(target=supervise)
        thread.start()
        try:
            self.assertTrue(wait_until(lambda: checks.lock_is_held(lock)), "supervisor never took its lock")
            self.assertIsNone(checks.teardown_private_root(root, drain_seconds=10))
            self.assertFalse(root.exists())
            self.assertTrue(observed.is_set(), "the drain never wrote the cooperative stop request")
        finally:
            thread.join(timeout=5)
            shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
