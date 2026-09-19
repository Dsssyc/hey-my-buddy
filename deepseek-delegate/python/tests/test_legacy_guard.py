"""Focused regression tests for the retired legacy ``service.sock`` guard.

A legitimate state directory can be longer than the platform's AF_UNIX address
capacity; ``bind`` then fails with a real ``AF_UNIX path too long`` error and the
obsolete listener must be omitted without bypassing anything that exists at the
path. Short, representable paths keep the original live/stale exclusion, the
``MIGRATED`` reply and both owner locks.

Every case is private: a unique state directory, a unique runtime root, an
environment scrubbed of anything a running service exported, and only child
processes this test created.
"""
from __future__ import annotations

import errno
import fcntl
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import unittest

from support import PYTHON_ROOT, wait_for

import buddy.daemon as daemon_module
from buddy.daemon import LegacySocketGuard, _unix_address_unrepresentable

#: A running service exports these; inheriting any of them would make this test
#: attach to, pin, or install into the operator's state and runtime.
INHERITED_KEYS = (
    "BUDDY_STATE_DIR",
    "BUDDY_RUNTIME",
    "BUDDY_RUNTIME_IDENTITY",
    "BUDDY_RUNTIME_ROOT",
    "BUDDY_DEV_SOURCE",
    "BUDDY_WORKER_ID",
    "PYTHONPATH",
)
CLI_TIMEOUT = 300


def environment_for(state_dir: Path, runtime_root: Path, *, dev_source: bool) -> dict:
    environment = {key: value for key, value in os.environ.items() if key not in INHERITED_KEYS}
    environment.update(
        {
            "BUDDY_STATE_DIR": str(state_dir),
            "BUDDY_RUNTIME_ROOT": str(runtime_root),
            "PYTHONPATH": str(PYTHON_ROOT),
            "VIRTUAL_ENV": "",
        }
    )
    if dev_source:
        environment["BUDDY_DEV_SOURCE"] = "1"
    return environment


def run_cli(state_dir: Path, runtime_root: Path, *arguments: str, dev_source: bool = True, timeout: int = CLI_TIMEOUT):
    completed = subprocess.run(
        [sys.executable, "-m", "buddy.cli", *arguments],
        env=environment_for(state_dir, runtime_root, dev_source=dev_source),
        cwd="/tmp",
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    text = completed.stdout.strip()
    payload = json.loads(text) if text else {}
    return completed.returncode, payload, completed.stderr


def run_daemon(state_dir: Path, runtime_root: Path, *, dev_source: bool = True, timeout: int = 60):
    return subprocess.run(
        [sys.executable, "-m", "buddy.daemon"],
        env=environment_for(state_dir, runtime_root, dev_source=dev_source),
        cwd="/tmp",
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def socket_bind_error(path: Path) -> OSError | None:
    """The real bind result for *path*: evidence, never a guessed path-length limit."""
    probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        probe.bind(str(path))
    except OSError as error:
        return error
    else:
        path.unlink(missing_ok=True)
        return None
    finally:
        probe.close()


def lock_is_free(path: Path) -> bool:
    """True when no live process holds this lock (exactly the ownership rule)."""
    if not path.exists():
        return True
    fd = os.open(path, os.O_RDWR)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return False
        fcntl.flock(fd, fcntl.LOCK_UN)
        return True
    finally:
        os.close(fd)


def connect_and_accept(listener: socket.socket, path: Path) -> None:
    """Prove a test-owned legacy listener still owns its endpoint."""
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.settimeout(5)
        client.connect(str(path))
        connection, _ = listener.accept()
        connection.close()


class GuardTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="buddy-guard-")).resolve()
        os.chmod(self.root, 0o700)
        self.runtime_root = self.root / "runtime"
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)

    def state_dir(self, name: str = "state") -> Path:
        directory = self.root / name
        directory.mkdir(mode=0o700, parents=True)
        os.chmod(directory, 0o700)
        return directory

    def long_state_dir(self) -> Path:
        """A private state directory whose ``service.sock`` AF_UNIX cannot name."""
        directory = self.root / ("long-state-" + "x" * 96) / "state"
        directory.mkdir(mode=0o700, parents=True)
        os.chmod(directory, 0o700)
        return directory

    def cli(self, state_dir: Path, *arguments: str, dev_source: bool = True, timeout: int = CLI_TIMEOUT):
        return run_cli(state_dir, self.runtime_root, *arguments, dev_source=dev_source, timeout=timeout)

    def daemon(self, state_dir: Path, *, dev_source: bool = True, timeout: int = 60):
        return run_daemon(state_dir, self.runtime_root, dev_source=dev_source, timeout=timeout)

    def stop_quietly(self, state_dir: Path) -> None:
        try:
            self.cli(state_dir, "stop", timeout=120)
        except Exception:  # noqa: BLE001 - best-effort cleanup of a test-owned service
            pass
        wait_for(lambda: self.shutdown_complete(state_dir), 30)

    def shutdown_complete(self, state_dir: Path) -> bool:
        """No owner lock and no worker supervisor lock is still held."""
        return all(
            lock_is_free(state_dir / name)
            for name in ("control-daemon.lock", "board-owner.lock", "workers/local/supervisor.lock")
        )


class TestUnrepresentableAddress(GuardTestCase):
    def test_only_a_real_bind_failure_counts_as_unrepresentable(self):
        """The omission is decided by bind evidence, never by a guessed length limit."""
        error = socket_bind_error(self.long_state_dir() / "service.sock")
        self.assertIsNotNone(error, "this test needs an address AF_UNIX cannot represent")
        self.assertTrue(_unix_address_unrepresentable(error), error)
        self.assertTrue(_unix_address_unrepresentable(OSError(errno.ENAMETOOLONG, "File name too long")))
        self.assertTrue(_unix_address_unrepresentable(OSError("AF_UNIX path too long")))
        for other in (errno.EACCES, errno.EADDRINUSE, errno.ENOENT, errno.ECONNREFUSED):
            self.assertFalse(_unix_address_unrepresentable(OSError(other, os.strerror(other))))

    def test_cli_cold_start_health_and_orderly_stop_with_a_long_state_path(self):
        """The failed acceptance shape: a real CLI cold start at an unusable socket path."""
        state = self.long_state_dir()
        socket_path = state / "service.sock"
        error = socket_bind_error(socket_path)
        self.assertIsNotNone(error, "this test needs an address AF_UNIX cannot represent")
        self.assertIn("too long", str(error).lower())
        self.assertFalse(socket_path.exists(), "a refused bind must create nothing")

        code, health, stderr = self.cli(state, "health")
        self.addCleanup(self.stop_quietly, state)
        self.assertEqual(code, 0, (health, stderr))
        self.assertEqual(health["status"], "ok")
        self.assertEqual(health["stateDir"], str(state))
        self.assertFalse(health["runtimeStable"], health)
        self.assertTrue(health["runtimeIdentity"].startswith("source:"), health["runtimeIdentity"])
        self.assertFalse(socket_path.exists(), "the obsolete listener is omitted, never faked")
        self.assertIn("legacy socket guard omitted", (state / "control.log").read_text())

        code, stopped, stderr = self.cli(state, "stop")
        self.assertEqual(code, 0, (stopped, stderr))
        self.assertTrue(stopped["stopped"], stopped)
        self.assertTrue(wait_for(lambda: not (state / "control.json").exists(), 30), "orderly stop removes its endpoint")
        self.assertTrue(wait_for(lambda: self.shutdown_complete(state), 30), "orderly stop releases locks and supervisor")
        self.assertFalse(socket_path.exists())

        code, again, stderr = self.cli(state, "stop")
        self.assertEqual(code, 0, (again, stderr))
        self.assertTrue(again["alreadyStopped"], again)

    @unittest.skipUnless(shutil.which("uv"), "uv is required to install the content-addressed runtime")
    def test_default_runtime_cold_start_at_a_long_path_installs_a_fresh_runtime(self):
        """The acceptance shape without BUDDY_DEV_SOURCE: the runtime copy carries the fix."""
        state = self.long_state_dir()
        socket_path = state / "service.sock"
        self.assertIsNotNone(socket_bind_error(socket_path))

        code, health, stderr = self.cli(state, "health", dev_source=False, timeout=900)
        self.addCleanup(self.stop_quietly, state)
        self.assertEqual(code, 0, (health, stderr))
        self.assertEqual(health["status"], "ok")
        self.assertTrue(health["runtimeStable"], health)
        self.assertTrue(health["runtimeIdentity"].startswith("runtime:"), health["runtimeIdentity"])
        ready = json.loads((self.runtime_root / health["runtimeContentId"] / "READY.json").read_text())
        self.assertEqual(ready["contentId"], health["runtimeContentId"])
        runtime_daemon = Path(ready["source"]) / "python/buddy/daemon.py"
        self.assertIn("_unix_address_unrepresentable", runtime_daemon.read_text())
        self.assertFalse(socket_path.exists())

        code, stopped, stderr = self.cli(state, "stop", dev_source=False)
        self.assertEqual(code, 0, (stopped, stderr))
        self.assertTrue(stopped["stopped"], stopped)
        self.assertTrue(wait_for(lambda: self.shutdown_complete(state), 30), "orderly stop releases locks and supervisor")


class TestExistingEntryAtUnrepresentableAddress(GuardTestCase):
    def test_a_regular_file_at_a_long_path_is_never_bypassed_or_removed(self):
        state = self.long_state_dir()
        entry = state / "service.sock"
        entry.write_text("legacy-owner")

        result = self.daemon(state)
        self.assertEqual(result.returncode, 2, result.stdout)
        self.assertIn("STALE_LEGACY_SOCKET", result.stderr)
        self.assertEqual(entry.read_text(), "legacy-owner")
        self.assertFalse((state / "control.json").exists())
        self.assertTrue(lock_is_free(state / "control-daemon.lock"))
        self.assertTrue(lock_is_free(state / "board-owner.lock"))

    def test_a_live_socket_reached_through_a_short_alias_still_blocks(self):
        """An older service can hold this socket through a shorter alias; never bypass it."""
        state = self.long_state_dir()
        alias = self.root / "alias"
        alias.symlink_to(state, target_is_directory=True)
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.addCleanup(listener.close)
        listener.bind(str(alias / "service.sock"))
        listener.listen(4)
        self.assertTrue((state / "service.sock").exists(), "the alias really created the long-path socket")

        result = self.daemon(state)
        self.assertEqual(result.returncode, 2, result.stdout)
        self.assertIn("STALE_LEGACY_SOCKET", result.stderr)
        self.assertTrue((state / "service.sock").exists())
        connect_and_accept(listener, alias / "service.sock")
        self.assertFalse((state / "control.json").exists())


class TestRepresentablePathExclusion(GuardTestCase):
    def test_a_live_legacy_listener_blocks_the_daemon(self):
        state = self.state_dir()
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.addCleanup(listener.close)
        listener.bind(str(state / "service.sock"))
        listener.listen(4)

        result = self.daemon(state)
        self.assertEqual(result.returncode, 2, result.stdout)
        self.assertIn("LEGACY_SERVICE_RUNNING", result.stderr)

        code, payload, stderr = self.cli(state, "health")
        self.assertEqual(code, 1, (payload, stderr))
        self.assertEqual(payload["error"]["code"], "SERVICE_START_FAILED")
        self.assertIn("LEGACY_SERVICE_RUNNING", (state / "control.log").read_text())

        self.assertTrue((state / "service.sock").exists())
        self.assertTrue(lock_is_free(state / "control-daemon.lock"))
        self.assertTrue(lock_is_free(state / "board-owner.lock"))
        connect_and_accept(listener, state / "service.sock")

    def test_a_stale_legacy_socket_blocks_the_daemon_and_stays_in_place(self):
        state = self.state_dir()
        abandoned = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        abandoned.bind(str(state / "service.sock"))
        abandoned.close()
        self.assertTrue((state / "service.sock").exists(), "a closed listener leaves its socket file")

        result = self.daemon(state)
        self.assertEqual(result.returncode, 2, result.stdout)
        self.assertIn("STALE_LEGACY_SOCKET", result.stderr)
        self.assertTrue((state / "service.sock").exists(), "the guard must not clean up a foreign socket")
        self.assertTrue(lock_is_free(state / "control-daemon.lock"))
        self.assertTrue(lock_is_free(state / "board-owner.lock"))

        # The documented operator action -- inspect and remove it -- unblocks the daemon.
        (state / "service.sock").unlink()
        code, health, stderr = self.cli(state, "health")
        self.addCleanup(self.stop_quietly, state)
        self.assertEqual(code, 0, (health, stderr))
        self.assertEqual(health["status"], "ok")
        self.assertTrue(self.cli(state, "stop")[1]["stopped"])

    def test_the_guard_still_answers_migrated_on_a_representable_path(self):
        self.assertTrue(
            Path(daemon_module.__file__).resolve().is_relative_to(PYTHON_ROOT),
            f"these tests must import from the checkout, not {daemon_module.__file__}",
        )
        state = self.state_dir()
        guard = LegacySocketGuard(state)
        self.addCleanup(guard.close)
        self.assertFalse(guard.omitted)
        self.assertIsNotNone(guard.thread)
        self.assertTrue((state / "service.sock").exists())

        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.settimeout(5)
            client.connect(str(state / "service.sock"))
            client.sendall(b'{"id":"old-client","method":"status"}\n')
            reply = json.loads(client.recv(8192).split(b"\n", 1)[0])
        self.assertEqual(reply["error"]["code"], "MIGRATED")
        self.assertEqual(reply["id"], "old-client")

        guard.close()
        guard.close()  # closing twice must stay safe
        self.assertFalse((state / "service.sock").exists(), "the guard removes only its own socket")


class TestOmittedGuardOwnership(GuardTestCase):
    def test_an_omitted_guard_has_no_listener_and_never_removes_foreign_entries(self):
        state = self.long_state_dir()
        guard = LegacySocketGuard(state)
        self.assertTrue(guard.omitted)
        self.assertIsNone(guard.thread, "an omitted guard must not serve the retired endpoint")
        self.assertIsNone(guard.identity)
        self.assertFalse((state / "service.sock").exists())

        guard.close()
        guard.close()  # an omitted guard has a safe, idempotent teardown

        foreign = state / "service.sock"
        foreign.write_text("older service reached through a shorter alias")
        guard.close()
        self.assertEqual(foreign.read_text(), "older service reached through a shorter alias")

    def test_a_second_owner_is_still_refused_when_the_guard_is_omitted(self):
        state = self.long_state_dir()
        code, health, stderr = self.cli(state, "health")
        self.addCleanup(self.stop_quietly, state)
        self.assertEqual(code, 0, (health, stderr))
        for name in ("control-daemon.lock", "board-owner.lock"):
            self.assertTrue((state / name).exists(), name)
            self.assertFalse(lock_is_free(state / name), f"{name} must be held by the running owner")

        second = self.daemon(state)
        self.assertEqual(second.returncode, 2, second.stdout)
        self.assertIn("ALREADY_RUNNING", second.stderr)
        self.assertEqual(self.cli(state, "health")[1]["serviceId"], health["serviceId"])

        code, stopped, stderr = self.cli(state, "stop")
        self.assertEqual(code, 0, (stopped, stderr))
        self.assertTrue(stopped["stopped"], stopped)
        self.assertTrue(wait_for(lambda: self.shutdown_complete(state), 30), "orderly stop releases both owner locks")
        self.assertTrue(lock_is_free(state / "control-daemon.lock"))
        self.assertTrue(lock_is_free(state / "board-owner.lock"))


if __name__ == "__main__":
    unittest.main()
