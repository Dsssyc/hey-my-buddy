"""Shared test support: private state directories and an in-process board harness.

Every service test uses a unique private state directory. Nothing here touches the
default state directory, the installed plugin or any process this test did not
create, and every child handle is retained so cleanup only stops test-owned work.
"""
from __future__ import annotations

import fcntl
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import contextmanager
from pathlib import Path

PYTHON_ROOT = Path(__file__).resolve().parents[1]
DELEGATE_ROOT = PYTHON_ROOT.parent
if str(PYTHON_ROOT) not in sys.path:
    sys.path.insert(0, str(PYTHON_ROOT))

from buddy.client import BoardClient  # noqa: E402
from buddy.legacy import LegacyImporter  # noqa: E402
from buddy.service import BoardService, WaitAdmission, WaitService, dispatch_local  # noqa: E402
from buddy.store import BoardStore  # noqa: E402


def stop_private_workers(directory: Path, timeout: float = 35.0) -> None:
    """Keep this test's state until its detached supervisors release ownership.

    Deleting the stop file with TemporaryDirectory while a worker is still exiting
    can make it miss the request and recreate the directory in its retry loop.
    The supervisor's lifetime lock is the completion boundary; no stored PID is
    used to signal or adopt a process.
    """
    locks = list((Path(directory) / "workers").glob("*/supervisor.lock"))
    for lock in locks:
        (lock.parent / "stop.request").touch(mode=0o600, exist_ok=True)
    deadline = time.monotonic() + timeout
    while locks:
        pending = []
        for path in locks:
            try:
                fd = os.open(path, os.O_RDWR)
            except FileNotFoundError:
                continue
            try:
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    pending.append(path)
                else:
                    fcntl.flock(fd, fcntl.LOCK_UN)
            finally:
                os.close(fd)
        if not pending:
            return
        if time.monotonic() >= deadline:
            raise AssertionError(f"Test supervisors still own state; preserved {directory}")
        locks = pending
        time.sleep(0.05)


@contextmanager
def private_state_dir(prefix: str = "buddy-test-"):
    directory = Path(tempfile.mkdtemp(prefix=prefix))
    os.chmod(directory, 0o700)
    try:
        yield directory
    finally:
        shutil.rmtree(directory, ignore_errors=True)


class InProcessBoard:
    """The real store and the real resource implementations, in one process.

    Only the C-Two transport is skipped; every validation, transaction, transition
    and error code is the production code path.
    """

    def __init__(self, directory: Path, **options):
        self.directory = Path(directory)
        self.store = BoardStore(
            self.directory,
            max_concurrent=options.get("max_concurrent", 2),
            lease_seconds=options.get("lease_seconds", 60),
            wait_capacity=options.get("wait_capacity", 4),
        )
        self.store.initialize()
        self.admission = WaitAdmission(options.get("wait_capacity", 4))
        self.control: dict = {"wait_admission": self.admission}
        self.stopped: list[dict] = []
        self.restarted: list[dict] = []
        self.service = BoardService(
            self.store,
            token="test-token",
            control=self.control,
            on_stop=lambda params: self.stopped.append(params) or {"action": "stop", "stopped": True},
            on_restart=lambda params: self.restarted.append(params) or {"action": "restart", "restarting": True},
            legacy_importer=LegacyImporter(self.store).run,
        )
        self.wait_service = WaitService(self.store, self.admission, token="test-token")

    def call(self, operation: str, params: dict) -> dict:
        service = self.wait_service if operation.startswith("events_wait") or operation == "message_wait" or operation == "wait_capacity" else self.service
        return dispatch_local(service, operation, params)

    def client(self, **kwargs) -> BoardClient:
        return BoardClient(self.directory, call=self.call, **kwargs)

    def close(self) -> None:
        self.store.db.path.unlink(missing_ok=True)


class BoardTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._stack = []
        self.directory = Path(tempfile.mkdtemp(prefix="buddy-test-"))
        os.chmod(self.directory, 0o700)
        self.addCleanup(self._cleanup)

    def _cleanup(self) -> None:
        for handle in getattr(self, "children", []):
            if handle.poll() is None:
                handle.terminate()
                try:
                    handle.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    handle.kill()
                    handle.wait(timeout=5)
        stop_private_workers(self.directory)
        shutil.rmtree(self.directory, ignore_errors=True)

    def board(self, **options) -> InProcessBoard:
        board = InProcessBoard(self.directory, **options)
        self._stack.append(board)
        return board

    def workdir(self, name: str = "work") -> Path:
        path = self.directory / name
        path.mkdir(parents=True, exist_ok=True)
        return path

    # -- real daemon helpers -------------------------------------------------
    @contextmanager
    def daemon(self, *, env: dict | None = None):
        """Start the real daemon in a child process and wait for health."""
        from buddy.transport import _request, _read_endpoint, ServiceError

        environment = {
            **os.environ,
            "BUDDY_STATE_DIR": str(self.directory),
            "PYTHONPATH": str(PYTHON_ROOT) + (os.pathsep + os.environ["PYTHONPATH"] if os.environ.get("PYTHONPATH") else ""),
            "VIRTUAL_ENV": "",
            # Tests exercise the interpreter under test; the packaging gate covers the
            # default stable-runtime install separately. The runtime root is private so
            # a test never installs into (or reads from) the operator's default one.
            "BUDDY_DEV_SOURCE": "1",
            "BUDDY_RUNTIME_ROOT": str(self.directory / "runtime-root"),
        }
        environment.update(env or {})
        log = open(self.directory / "test-daemon.log", "ab")
        process = subprocess.Popen(
            [sys.executable, "-m", "buddy.daemon"],
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=log,
            start_new_session=True,
        )
        self.children = getattr(self, "children", [])
        self.children.append(process)
        deadline = time.monotonic() + 25
        try:
            while time.monotonic() < deadline:
                endpoint = _read_endpoint(self.directory)
                if endpoint:
                    try:
                        _request(endpoint, "health", {})
                        break
                    except ServiceError:
                        pass
                if process.poll() is not None:
                    raise AssertionError(
                        f"daemon exited early: {(self.directory / 'test-daemon.log').read_text()[-2000:]}"
                    )
                time.sleep(0.05)
            else:
                raise AssertionError(
                    f"daemon did not become healthy: {(self.directory / 'test-daemon.log').read_text()[-2000:]}"
                )
            yield process
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=15)
                except subprocess.TimeoutExpired:  # pragma: no cover - test watchdog
                    process.kill()
            log.close()

    def cli(self, *arguments: str, env: dict | None = None, timeout: int = 90) -> tuple[int, dict]:
        """Run the real CLI in a child process; returns (exit code, parsed stdout)."""
        environment = {
            **os.environ,
            "BUDDY_STATE_DIR": str(self.directory),
            "PYTHONPATH": str(PYTHON_ROOT) + (os.pathsep + os.environ["PYTHONPATH"] if os.environ.get("PYTHONPATH") else ""),
            "VIRTUAL_ENV": "",
            "BUDDY_DEV_SOURCE": "1",
            "BUDDY_RUNTIME_ROOT": str(self.directory / "runtime-root"),
        }
        environment.update(env or {})
        completed = subprocess.run(
            [sys.executable, "-m", "buddy.cli", *arguments],
            env=environment,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        text = completed.stdout.strip()
        try:
            # The CLI pretty-prints one JSON document; parse the whole document.
            payload = json.loads(text) if text else {}
        except ValueError:
            try:
                payload = json.loads(text.splitlines()[-1]) if text else {}
            except (ValueError, IndexError):
                payload = {"raw": completed.stdout, "stderr": completed.stderr}
        return completed.returncode, payload


def wait_for(predicate, timeout: float = 20.0, interval: float = 0.05):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(interval)
    return None
