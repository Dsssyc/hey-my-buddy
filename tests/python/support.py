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

from hey_my_buddy.protocol.client import BoardClient  # noqa: E402
from hey_my_buddy.console.server import Console  # noqa: E402
from hey_my_buddy.blackboard.routing.decision import DecisionCoordinator  # noqa: E402
from hey_my_buddy.blackboard.evaluation.evaluation import EvaluationStore  # noqa: E402
from hey_my_buddy.blackboard.service.service import BoardService, WaitAdmission, WaitService, dispatch_local  # noqa: E402
from hey_my_buddy.blackboard.store.store import BoardStore  # noqa: E402

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


def shutdown_private_rpc() -> None:
    """End this fixture's communication before another private state is selected."""
    import c_two as cc
    if not cc.shutdown().get("completed"):
        raise AssertionError("Private test RPC shutdown is unconfirmed; preserve its state")


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
    from hey_my_buddy.protocol.transport import ServiceError, _read_endpoint, _request

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
                                                                    "reason": "private test cleanup"}, state_dir=directory)
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
    "BUDDY_ACCOUNT_SELECTION", "BUDDY_SUPERVISOR_START_ID",
}


def offline_facts_source(directory: Path) -> str:
    """The offline model-facts source every test harness pins by default.

    The path names a fixture that normally does not exist, so a facts refresh inside
    a test fails its source read and retains the previous snapshot instead of
    touching the network. A test that wants a real parse writes its fixture there.
    """
    return str(Path(directory) / "model-facts-fixture.json")


def _child_environment(directory: Path, overrides: dict | None = None) -> dict:
    from hey_my_buddy.buddy.harnesses.claude.config import THIRD_PARTY_OVERRIDE_VARIABLES

    removed = _INHERITED_CHILD_KEYS | set(THIRD_PARTY_OVERRIDE_VARIABLES)
    environment = {key: value for key, value in os.environ.items() if key not in removed and not key.startswith(("BUDDY_", "ANTHROPIC_", "C2_"))}
    # HOME and SDK locations belong to this fixture, never to the interpreter's
    # inherited account. Do not create native configuration files in this home.
    private_home = directory / "home"
    private_home.mkdir(mode=0o700, exist_ok=True)
    environment.update(
        HOME=str(private_home),
        USERPROFILE=str(private_home),
        CODEX_HOME=str(private_home / ".codex"),
        CLAUDE_CONFIG_DIR=str(private_home / ".claude"),
        ZCODE_DATA_BASE_DIR=str(private_home / ".zcode"),
        ZCODE_BUILTIN_PROVIDER_CONFIG_FILE=str(private_home / ".zcode/builtin-provider.json"),
        ZCODE_PERSONAL_PROVIDER_CONFIG_FILE=str(private_home / ".zcode/personal-provider.json"),
        DSH_HOME=str(private_home / ".dsh"),
        XDG_CONFIG_HOME=str(private_home / ".config"),
        XDG_DATA_HOME=str(private_home / ".local/share"),
        XDG_CACHE_HOME=str(private_home / ".cache"),
        XDG_STATE_HOME=str(private_home / ".local/state"),
        XDG_RUNTIME_DIR=str(directory / "runtime-root"),
        APPDATA=str(private_home / "AppData/Roaming"),
        LOCALAPPDATA=str(private_home / "AppData/Local"),
        BUDDY_STATE_DIR=str(directory),
        BUDDY_RUNTIME_ROOT=str(directory / "runtime-root"),
        PYTHONPATH=str(PYTHON_ROOT) + (os.pathsep + environment["PYTHONPATH"] if environment.get("PYTHONPATH") else ""),
        BUDDY_DEV_SOURCE="1",
        # A test child never shares the console backend's fixed default port; the
        # operating system assigns a private one. Tests may override this explicitly.
        BUDDY_CONSOLE_PORT="0",
        BUDDY_CLAUDE_CLI=str(PYTHON_ROOT.parent / "tests/python/fixtures/claude-not-installed"),
        BUDDY_CODEX_CLI=str(PYTHON_ROOT.parent / "tests/python/fixtures/codex-not-installed"),
        # Model-facts refreshes stay offline: the pinned source is a private fixture
        # path, and a missing one is a retained snapshot, never a network request.
        BUDDY_MODEL_FACTS_FILE=str(directory / "model-facts-fixture.json"),
    )
    environment.update(overrides or {})
    # Test-supplied paths may vary within the fixture, but must not redirect its
    # daemon or installed runtime into another board's state.
    environment["BUDDY_STATE_DIR"] = str(directory)
    if not Path(environment["BUDDY_RUNTIME_ROOT"]).resolve().is_relative_to(directory.resolve()):
        raise ValueError("Test runtime root must be inside its private state directory")
    for key in ("HOME", "USERPROFILE", "CODEX_HOME", "CLAUDE_CONFIG_DIR", "ZCODE_DATA_BASE_DIR",
                "ZCODE_BUILTIN_PROVIDER_CONFIG_FILE", "ZCODE_PERSONAL_PROVIDER_CONFIG_FILE", "DSH_HOME",
                "XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME", "XDG_STATE_HOME",
                "XDG_RUNTIME_DIR", "APPDATA", "LOCALAPPDATA", "BUDDY_MODEL_CATALOG_FILE"):
        if key in environment and not Path(environment[key]).resolve().is_relative_to(directory.resolve()):
            raise ValueError(f"Test {key} must be inside its private state directory")
    return environment


@contextmanager
def private_state_dir(prefix: str = "buddy-test-"):
    # The resolved form keeps one path identity for the whole board: macOS's
    # per-user /var/folders alias and its /private/var target must never be two
    # different roots inside one test (storage boundaries and Git both resolve).
    directory = Path(tempfile.mkdtemp(prefix=prefix)).resolve()
    os.chmod(directory, 0o700)
    previous = os.environ.get("BUDDY_MODEL_FACTS_FILE")
    os.environ["BUDDY_MODEL_FACTS_FILE"] = offline_facts_source(directory)

    def restore_facts_source() -> None:
        if previous is None:
            os.environ.pop("BUDDY_MODEL_FACTS_FILE", None)
        else:
            os.environ["BUDDY_MODEL_FACTS_FILE"] = previous

    try:
        yield directory
    finally:
        restore_facts_source()
        stop_private_service(directory)
        stop_private_workers(directory)
        shutdown_private_rpc()
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
        self.directory = Path(tempfile.mkdtemp(prefix="buddy-test-")).resolve()
        os.chmod(self.directory, 0o700)
        # In-process boards read the source hook from this process environment; pin
        # the same offline fixture path the child environments get, so enabling a
        # model in any test cannot reach the network.
        previous = os.environ.get("BUDDY_MODEL_FACTS_FILE")
        os.environ["BUDDY_MODEL_FACTS_FILE"] = offline_facts_source(self.directory)
        self.addCleanup(self._restore_facts_source, previous)
        self.addCleanup(self._cleanup)

    def _restore_facts_source(self, previous: str | None) -> None:
        if previous is None:
            os.environ.pop("BUDDY_MODEL_FACTS_FILE", None)
        else:
            os.environ["BUDDY_MODEL_FACTS_FILE"] = previous

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
        shutdown_private_rpc()
        shutil.rmtree(self.directory)

    def board(self, **options) -> InProcessBoard:
        board = InProcessBoard(self.directory, **options)
        self._stack.append(board)
        return board

    def assert_rpc_state_dir(self, state_dir) -> None:
        """RPC substitutes accept only this test's explicitly supplied state root."""
        self.assertIsInstance(state_dir, (str, Path), "RPC requires an explicit private state_dir")
        self.assertEqual(Path(state_dir).resolve(), self.directory.resolve(),
                         "RPC state_dir must belong to the current test")

    @contextmanager
    def rpc_connection(self, contract, *, name: str, address: str):
        """A direct native connection in this fixture's private state domain."""
        import c_two as cc
        from hey_my_buddy.protocol.rpc_config import configure_client
        configure_client(self.directory)
        with cc.connect(contract, name=name, address=address) as proxy:
            yield proxy

    def workdir(self, name: str = "work") -> Path:
        path = self.directory / name
        path.mkdir(parents=True, exist_ok=True)
        return path

    def catalog_fixture(self, payload: dict | None = None) -> Path:
        """Point discovery at a private fixture instead of the installed harness."""
        path = write_catalog_fixture(self.directory, payload)
        self._catalog_fixture_path = path
        previous = os.environ.get("BUDDY_MODEL_CATALOG_FILE")
        os.environ["BUDDY_MODEL_CATALOG_FILE"] = str(path)

        def restore() -> None:
            if previous is None:
                os.environ.pop("BUDDY_MODEL_CATALOG_FILE", None)
            else:
                os.environ["BUDDY_MODEL_CATALOG_FILE"] = previous

        self.addCleanup(restore)
        return path

    def child_environment(self, overrides: dict | None = None) -> dict:
        """Pass this owner's catalog explicitly after inherited pins are cleared."""
        explicit = {}
        if getattr(self, "_catalog_fixture_path", None) is not None:
            explicit["BUDDY_MODEL_CATALOG_FILE"] = str(self._catalog_fixture_path)
        explicit.update(overrides or {})
        return _child_environment(self.directory, explicit)

    # -- real daemon helpers -------------------------------------------------
    @contextmanager
    def daemon(self, *, env: dict | None = None):
        """Start the real daemon in a child process and wait for health."""
        from hey_my_buddy.protocol.transport import _request, _read_endpoint, ServiceError

        from hey_my_buddy.protocol.rpc_config import configure_client
        configure_client(self.directory)
        environment = self.child_environment(env)
        log = open(self.directory / "test-daemon.log", "ab")
        command = [sys.executable, '-m', 'hey_my_buddy.blackboard.service.daemon']
        if environment.get('BUDDY_MODEL_CATALOG_FILE'):
            command = [sys.executable, str(Path(__file__).parent / 'blackboard/service/fixtures/daemon_with_catalog.py')]
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
                        health = _request(endpoint, "health", {}, state_dir=self.directory)
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
        environment = self.child_environment(env)
        completed = subprocess.run(
            [sys.executable, "-m", "hey_my_buddy.cli.main", *arguments],
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


def enable_fixture_configuration(store, configuration):
    """Explicit private fixture policy; negative policy tests call the service directly."""
    keys = ("adapter", "provider", "model", "effort")
    if not all(configuration.get(key) for key in keys):
        return
    with store.db.write() as db:
        db.execute("INSERT OR IGNORE INTO evaluation_profiles(profile_id,label,adapter,provider,model,effort,enabled,available,created_revision,updated_revision) VALUES(?,?,?,?,?,?,1,1,0,0) ON CONFLICT(profile_id) DO UPDATE SET enabled=1,available=1",
                   (":".join(configuration[key] for key in keys), "Private enabled fixture", *(configuration[key] for key in keys)))
