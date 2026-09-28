"""Shared test support: private state directories and an in-process board harness.

Every service test uses a unique private state directory. Cleanup addresses only
that directory through its authenticated service endpoint and lifetime locks,
including a replacement daemon that the CLI may have started after a restart.
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

PYTHON_ROOT = (Path(__file__).resolve().parents[2] / "src")
DELEGATE_ROOT = PYTHON_ROOT.parent
if str(PYTHON_ROOT) not in sys.path:
    sys.path.insert(0, str(PYTHON_ROOT))

from buddy.client import BoardClient  # noqa: E402
from buddy.console import Console  # noqa: E402
from buddy.decision import DecisionCoordinator  # noqa: E402
from buddy.evaluation import EvaluationStore  # noqa: E402
from buddy.service import BoardService, WaitAdmission, WaitService, dispatch_local  # noqa: E402
from buddy.store import BoardStore  # noqa: E402

#: A discovery document with the same shape the installed-harness helper emits. Tests
#: point ``BUDDY_MODEL_CATALOG_FILE`` at this fixture instead of invoking Node.
FIXTURE_CATALOG = {
    "source": "file:test-fixture",
    "harnessVersion": "test-harness-1",
    "providerVersion": "@deepseek-ai/dsh-llm-deepseek@test",
    "discoveredAt": "2026-01-01T00:00:00.000Z",
    "providers": [
        {
            "provider": "deepseek-official",
            "displayName": "DeepSeek",
            "packageName": "@deepseek-ai/dsh-llm-deepseek",
            "packageVersion": "test",
            "adapter": "dsh",
            "efforts": ["off", "low", "high", "max"],
            "models": [
                {
                    "id": "deepseek-flash",
                    "name": "DeepSeek-V41-Flash",
                    "description": "fixture fast model",
                    "contextWindow": 1000000,
                    "inputModalities": ["text", "image"],
                },
                {
                    "id": "deepseek-v4-pro",
                    "name": "DeepSeek-V4-Pro",
                    "description": "fixture strong model",
                    "contextWindow": 1000000,
                    "inputModalities": ["text"],
                },
            ],
        }
    ],
    "warnings": [],
}


def write_catalog_fixture(directory: Path, payload: dict | None = None) -> Path:
    path = Path(directory) / "model-catalog.json"
    path.write_text(json.dumps(payload or FIXTURE_CATALOG))
    os.chmod(path, 0o600)
    return path


class FakeClock:
    """Deterministic wall clock so lease and expiry fences are exercisable."""

    def __init__(self, start: str = "2026-01-01T00:00:00.000Z"):
        self.value = start

    def __call__(self) -> str:
        return self.value

    def advance(self, seconds: float) -> str:
        from datetime import datetime, timedelta

        moment = datetime.fromisoformat(self.value.replace("Z", "+00:00")) + timedelta(seconds=seconds)
        self.value = moment.isoformat(timespec="milliseconds").replace("+00:00", "Z")
        return self.value


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


def _lock_held(path: Path) -> bool:
    try:
        fd = os.open(path, os.O_RDWR)
    except FileNotFoundError:
        return False
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return True
        fcntl.flock(fd, fcntl.LOCK_UN)
        return False
    finally:
        os.close(fd)


def stop_private_service(directory: Path, timeout: float = 35.0) -> None:
    """Stop the daemon attached to this exact test directory and await its locks."""
    from buddy.transport import ServiceError, _read_endpoint, _request

    directory = Path(directory)
    deadline = time.monotonic() + timeout
    requested = False
    while True:
        starting = _lock_held(directory / "control-start.lock")
        running = any(_lock_held(directory / name) for name in ("control-daemon.lock", "board-owner.lock"))
        if not starting and not running:
            return
        if running and not requested:
            endpoint = _read_endpoint(directory)
            if endpoint is not None:
                try:
                    reply = _request(endpoint, "service_control", {"action": "stop", "drainSeconds": 20,
                                                                    "reason": "private test cleanup"})
                except ServiceError:
                    # The endpoint may have disappeared during a restart. Recheck
                    # the lifetime locks and attach to its replacement if needed.
                    pass
                else:
                    if not reply.get("stopped") or reply.get("unresolvedAttempts"):
                        raise AssertionError(f"Test service shutdown unconfirmed; preserved {directory}: {reply}")
                    requested = True
        if time.monotonic() >= deadline:
            raise AssertionError(f"Test service still owns state; preserved {directory}")
        time.sleep(0.05)


_INHERITED_CHILD_KEYS = {
    "BUDDY_STATE_DIR", "BUDDY_RUNTIME_ROOT", "BUDDY_RUNTIME", "BUDDY_RUNTIME_IDENTITY",
    "BUDDY_WORKER_STATE", "BUDDY_WORKER_ID", "BUDDY_AGENT_CREDENTIAL",
    "BUDDY_AGENT_CREDENTIAL_FILE", "BUDDY_DEV_SOURCE", "VIRTUAL_ENV", "UV_PROJECT_ENVIRONMENT",
}


def _child_environment(directory: Path, overrides: dict | None = None) -> dict:
    environment = {key: value for key, value in os.environ.items() if key not in _INHERITED_CHILD_KEYS}
    environment.update(
        BUDDY_STATE_DIR=str(directory),
        BUDDY_RUNTIME_ROOT=str(directory / "runtime-root"),
        PYTHONPATH=str(PYTHON_ROOT) + (os.pathsep + environment["PYTHONPATH"] if environment.get("PYTHONPATH") else ""),
        BUDDY_DEV_SOURCE="1",
        # A test child never shares the console backend's fixed default port; the
        # operating system assigns a private one. Tests may override this explicitly.
        BUDDY_CONSOLE_PORT="0",
    )
    environment.update(overrides or {})
    # Test-supplied paths may vary within the fixture, but must not redirect its
    # daemon or installed runtime into another board's state.
    environment["BUDDY_STATE_DIR"] = str(directory)
    if not Path(environment["BUDDY_RUNTIME_ROOT"]).resolve().is_relative_to(directory.resolve()):
        raise ValueError("Test runtime root must be inside its private state directory")
    return environment


@contextmanager
def private_state_dir(prefix: str = "buddy-test-"):
    directory = Path(tempfile.mkdtemp(prefix=prefix))
    os.chmod(directory, 0o700)
    try:
        yield directory
    finally:
        stop_private_service(directory)
        stop_private_workers(directory)
        shutil.rmtree(directory)


class InProcessBoard:
    """The real store and the real resource implementations, in one process.

    Only the C-Two transport is skipped; every validation, transaction, transition
    and error code is the production code path.
    """

    def __init__(self, directory: Path, **options):
        self.directory = Path(directory)
        clock = options.get("clock")
        self.store = BoardStore(
            self.directory,
            max_concurrent=options.get("max_concurrent", 2),
            lease_seconds=options.get("lease_seconds", 60),
            wait_capacity=options.get("wait_capacity", 4),
            **({"clock": clock} if clock is not None else {}),
        )
        # One evaluation store and one decision coordinator for the whole harness:
        # the store hooks and the resource implementations must fence the same gate.
        self.store.evaluation = EvaluationStore(
            self.store,
            clock=clock,
            writer_lease_seconds=options.get("writer_lease_seconds", 60),
            writer_queue_seconds=options.get("writer_queue_seconds", 120),
            reader_lease_seconds=options.get("reader_lease_seconds", 300),
        )
        self.store.decisions = DecisionCoordinator(self.store, self.store.evaluation)
        self.store.initialize()
        # These store/transport unit fixtures model healthy native harnesses.
        # Dedicated discovery/service tests start with empty real health records.
        with self.store.db.write() as db:
            for name in ('dsh', 'zcode', 'codex', 'claude'):
                db.execute("INSERT OR IGNORE INTO harness_health(adapter,status,record_json) VALUES(?,'ready','{}')", (name,))
        self.admission = WaitAdmission(options.get("wait_capacity", 4))
        self.control: dict = {"wait_admission": self.admission}
        self.stopped: list[dict] = []
        self.restarted: list[dict] = []
        self.evaluation = self.store.evaluation
        self.console: Console | None = None
        self.service = BoardService(
            self.store,
            token="test-token",
            control=self.control,
            on_stop=lambda params: self.stopped.append(params) or {"action": "stop", "stopped": True},
            on_restart=lambda params: self.restarted.append(params) or {"action": "restart", "restarting": True},
            evaluation=self.evaluation,
            console_factory=self.console_action,
        )
        # Native startup validation shares this synthetic health boundary. The
        # real service/discovery suites construct BoardService independently.
        self.service.harnesses.refresh = lambda name, **kwargs: self.service.harnesses.get(name)
        self.wait_service = WaitService(self.store, self.admission, token="test-token")
        self.console = Console(
            self.store,
            self.service,
            port=0,
            assets_dir=options.get("console_assets", self.directory / "console-assets"),
        )

    def console_action(self, params: dict) -> dict:
        if self.console is None:  # pragma: no cover - only during construction
            return {"url": None, "running": False, "readOnly": False}
        if params["action"] == "close":
            return self.console.close(expected_console_id=params.get("expectedConsoleId"))
        if params["action"] == "status":
            return self.console.status()
        return self.console.start()

    def call(self, operation: str, params: dict) -> dict:
        service = self.wait_service if operation.startswith("events_wait") or operation == "message_wait" or operation == "wait_capacity" else self.service
        return dispatch_local(service, operation, params)

    def console_call(self, operation: str, params: dict) -> dict:
        """Exercise an authenticated private console session in process."""
        session = "test-console-session"
        self.service.register_console_authority(session)
        return self.call(operation, {**params, "consoleAuthority": session})

    def client(self, **kwargs) -> BoardClient:
        return BoardClient(self.directory, call=self.call, **kwargs)

    def close(self) -> None:
        if self.console is not None:
            self.console.close()
        self.store.db.path.unlink(missing_ok=True)


class BoardTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._stack = []
        self.directory = Path(tempfile.mkdtemp(prefix="buddy-test-"))
        os.chmod(self.directory, 0o700)
        self.addCleanup(self._cleanup)

    def _cleanup(self) -> None:
        stop_private_service(self.directory)
        stop_private_workers(self.directory)
        for board in getattr(self, "_stack", []):
            try:
                board.close()
            except Exception:  # noqa: BLE001 - cleanup must never mask the test result
                pass
        for handle in getattr(self, "children", []):
            if handle.poll() is None:
                handle.terminate()
                try:
                    handle.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    handle.kill()
                    handle.wait(timeout=5)
        shutil.rmtree(self.directory)

    def board(self, **options) -> InProcessBoard:
        board = InProcessBoard(self.directory, **options)
        self._stack.append(board)
        return board

    def workdir(self, name: str = "work") -> Path:
        path = self.directory / name
        path.mkdir(parents=True, exist_ok=True)
        return path

    def catalog_fixture(self, payload: dict | None = None) -> Path:
        """Point discovery at a private fixture instead of the installed harness."""
        path = write_catalog_fixture(self.directory, payload)
        previous = os.environ.get("BUDDY_MODEL_CATALOG_FILE")
        os.environ["BUDDY_MODEL_CATALOG_FILE"] = str(path)

        def restore() -> None:
            if previous is None:
                os.environ.pop("BUDDY_MODEL_CATALOG_FILE", None)
            else:
                os.environ["BUDDY_MODEL_CATALOG_FILE"] = previous

        self.addCleanup(restore)
        return path

    # -- real daemon helpers -------------------------------------------------
    @contextmanager
    def daemon(self, *, env: dict | None = None):
        """Start the real daemon in a child process and wait for health."""
        from buddy.transport import _request, _read_endpoint, ServiceError

        environment = _child_environment(self.directory, env)
        log = open(self.directory / "test-daemon.log", "ab")
        command = [sys.executable, '-m', 'buddy.daemon']
        if environment.get('BUDDY_MODEL_CATALOG_FILE'):
            command = [sys.executable, str(Path(__file__).parent / 'fixtures/daemon_with_catalog.py')]
        process = subprocess.Popen(
            command,
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
                        health = _request(endpoint, "health", {})
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
            # A healthy endpoint can precede pool startup. Observe each managed
            # supervisor taking its lifetime lock before a short test can finish;
            # otherwise cleanup may delete stop requests before late children boot.
            for worker_id in health.get("managedWorkerIds", []):
                lock = self.directory / "workers" / worker_id / "supervisor.lock"
                deadline = time.monotonic() + 20
                while True:
                    held = False
                    if lock.exists():
                        fd = os.open(lock, os.O_RDWR)
                        try:
                            try:
                                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                            except BlockingIOError:
                                held = True
                            else:
                                fcntl.flock(fd, fcntl.LOCK_UN)
                        finally:
                            os.close(fd)
                    if held:
                        break
                    if time.monotonic() >= deadline:
                        raise AssertionError(f"Managed test worker did not start: {worker_id}")
                    time.sleep(0.05)
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
        environment = _child_environment(self.directory, env)
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
