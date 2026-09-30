"""Test fixtures must retire their own detached daemon before deleting state."""
from __future__ import annotations

import fcntl
import json
import os
import shutil
import subprocess
import sys
import unittest
from unittest.mock import patch

from support import (
    BoardTestCase, PYTHON_ROOT, _child_environment, _lock_held, private_state_dir,
    stop_private_service, stop_private_workers, wait_for,
)
from buddy.transport import _read_endpoint


def assert_released(test: unittest.TestCase, handles: list[int]) -> None:
    for fd in handles:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            test.fail("A test daemon or supervisor still holds its lifetime lock")
        else:
            fcntl.flock(fd, fcntl.LOCK_UN)


class FixtureCleanupTests(unittest.TestCase):
    def test_unconfirmed_shutdown_preserves_private_state(self):
        directory = None
        try:
            with self.assertRaisesRegex(AssertionError, "shutdown unconfirmed"):
                with patch("support.stop_private_service", side_effect=AssertionError("shutdown unconfirmed")):
                    with private_state_dir() as directory:
                        (directory / "receipt.json").write_text("evidence")
            self.assertEqual((directory / "receipt.json").read_text(), "evidence")
        finally:
            if directory is not None:
                shutil.rmtree(directory)

    def test_child_environment_drops_inherited_ownership_and_credentials(self):
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
        }
        with private_state_dir() as directory, patch.dict(os.environ, inherited):
            environment = _child_environment(directory)
            self.assertEqual(environment["BUDDY_STATE_DIR"], str(directory))
            self.assertEqual(environment["BUDDY_RUNTIME_ROOT"], str(directory / "runtime-root"))
            for key in inherited.keys() - {"BUDDY_STATE_DIR", "BUDDY_RUNTIME_ROOT"}:
                self.assertNotIn(key, environment)

    def test_child_environment_preserves_explicit_private_paths(self):
        with private_state_dir() as directory:
            runtime_root = directory / "alternate-runtime"
            python_path = os.pathsep.join((str(PYTHON_ROOT), str(directory / "helpers")))
            environment = _child_environment(directory, {
                "BUDDY_STATE_DIR": "/wrong-state",
                "BUDDY_RUNTIME_ROOT": str(runtime_root),
                "PYTHONPATH": python_path,
            })
            self.assertEqual(environment["BUDDY_STATE_DIR"], str(directory))
            self.assertEqual(environment["BUDDY_RUNTIME_ROOT"], str(runtime_root))
            self.assertEqual(environment["PYTHONPATH"], python_path)
            with self.assertRaisesRegex(ValueError, "private state directory"):
                _child_environment(directory, {"BUDDY_RUNTIME_ROOT": "/wrong-runtime"})

    def test_cleanup_stops_cli_started_replacement_after_restart(self):
        fixture = BoardTestCase("runTest")
        fixture.setUp()
        directory = fixture.directory
        handles = []
        try:
            with fixture.daemon(env={"BUDDY_MAX_CONCURRENT": "1"}) as original:
                original_id = _read_endpoint(directory)["serviceId"]
                code, restarted = fixture.cli("restart", json.dumps({"drainSeconds": 0}))
                self.assertEqual(code, 0, restarted)
                self.assertTrue(restarted["restarting"])
                original.wait(timeout=20)

            # The CLI detaches this new daemon, so it is absent from children.
            code, health = fixture.cli("health", env={"BUDDY_MAX_CONCURRENT": "1"})
            self.assertEqual(code, 0, health)
            self.assertNotEqual(_read_endpoint(directory)["serviceId"], original_id)
            self.assertTrue(wait_for(lambda: _lock_held(directory / "workers/local/supervisor.lock")))
            for path in ("control-daemon.lock", "board-owner.lock", "workers/local/supervisor.lock"):
                handles.append(os.open(directory / path, os.O_RDWR))

            # Leave the directory available for a protocol cleanup if the assertion
            # fails; the lock FDs also let us inspect ownership after deletion.
            with patch("support.shutil.rmtree") as remove:
                fixture._cleanup()
            remove.assert_called_once_with(directory)
            assert_released(self, handles)
        finally:
            if directory.exists():
                stop_private_service(directory)
                stop_private_workers(directory)
                shutil.rmtree(directory)
            for fd in handles:
                os.close(fd)

    def test_private_state_context_stops_its_cli_started_service(self):
        handles = []
        directory = None
        try:
            with patch("support.shutil.rmtree") as remove:
                with private_state_dir() as directory:
                    environment = _child_environment(directory, {"BUDDY_MAX_CONCURRENT": "1"})
                    completed = subprocess.run(
                        [sys.executable, "-m", "buddy.cli", "health"], env=environment,
                        capture_output=True, text=True, timeout=60,
                    )
                    self.assertEqual(completed.returncode, 0, completed.stderr)
                    self.assertTrue(wait_for(lambda: _lock_held(directory / "workers/local/supervisor.lock")))
                    for path in ("control-daemon.lock", "board-owner.lock", "workers/local/supervisor.lock"):
                        handles.append(os.open(directory / path, os.O_RDWR))
            remove.assert_called_once_with(directory)
            assert_released(self, handles)
        finally:
            if directory is not None and directory.exists():
                stop_private_service(directory)
                stop_private_workers(directory)
                shutil.rmtree(directory)
            for fd in handles:
                os.close(fd)


if __name__ == "__main__":
    unittest.main()
