"""Private published C-Two 0.7.5 RPC, non-pool accounting and control capacity.

Every native process configures an explicit test-owned state/ipc directory before
local I/O. Memory statistics are C-Two accounting, never RSS.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

from support import stop_private_service, _child_environment, shutdown_private_rpc

from hey_my_buddy.protocol import rpc_config
from hey_my_buddy.blackboard.service.service import WAIT_CAPACITY_DEFAULT
from hey_my_buddy.protocol.transport import MAX_MESSAGE_BYTES, ServiceError, _read_endpoint, _request, call_board, encode_message

ROOT = Path(__file__).resolve().parents[3]
PYTHON = ROOT / "src"
TESTS = ROOT / "tests" / "python"

MIB = 1024 * 1024

#: Native subprocess fixtures; raw mode varies only explicit IPC overrides.
PROBE_SERVER = '''
import json, os, sys, time
from pathlib import Path
from types import SimpleNamespace
import c_two as cc
from c_two import crm

@crm(namespace="buddy.probe", version="1.0.0")
class ProbeContract:
    def echo(self, payload: str) -> str: ...
    def sleep_ms(self, payload: str) -> str: ...

class ProbeImpl:
    def echo(self, payload: str) -> str:
        return payload
    def sleep_ms(self, payload: str) -> str:
        time.sleep(int(payload) / 1000.0)
        return json.dumps({"slept": int(payload)})

from hey_my_buddy.protocol import rpc_config
state = Path(os.environ["HMB_STATE_DIR"])
if os.environ.get("PROBE_SERVER_MODE") == "raw":
    rpc_config.configure_local_endpoint(state)
    cc.set_server(ipc_overrides=json.loads(os.environ["PROBE_SERVER_OVERRIDES"]))
else:
    from hey_my_buddy.protocol import rpc_config
    rpc_config.configure_server(state)
cc.register(ProbeContract, ProbeImpl(), name="hey-my-buddy-probe",
            concurrency=cc.ConcurrencyConfig(mode=cc.ConcurrencyMode.PARALLEL))
Path(sys.argv[1]).write_text(json.dumps({"address": cc.server_address(), "pid": os.getpid(), "root": cc.local_endpoint_context().root}))
stop = sys.argv[2]
while not os.path.exists(stop):
    time.sleep(0.05)
assert cc.shutdown()["completed"]
'''

PROBE_CLIENT = '''
import hashlib, json, os, sys, time
import c_two as cc
from c_two import crm

@crm(namespace="buddy.probe", version="1.0.0")
class ProbeContract:
    def echo(self, payload: str) -> str: ...
    def sleep_ms(self, payload: str) -> str: ...

from pathlib import Path
from types import SimpleNamespace
from hey_my_buddy.protocol import rpc_config
state = Path(os.environ["HMB_STATE_DIR"])
mode = os.environ.get("PROBE_CLIENT_MODE", "profile")
if mode == "raw":
    rpc_config.configure_local_endpoint(state)
    cc.set_client(ipc_overrides=json.loads(os.environ["PROBE_CLIENT_OVERRIDES"]))
elif mode == "profile":
    from hey_my_buddy.protocol import rpc_config
    rpc_config.configure_client(state)

address, operation, argument = sys.argv[1], sys.argv[2], sys.argv[3]
payload = "x" * int(argument.split(":", 1)[1]) if argument.startswith("bytes:") else argument
started = time.monotonic()
try:
    with cc.connect(ProbeContract, name="hey-my-buddy-probe", address=address) as probe:
        result = getattr(probe, operation)(payload)
    reply = {
        "ok": True,
        "version": cc.__version__,
        "memory": cc.memory_stats(),
        "seconds": round(time.monotonic() - started, 3),
        "bytes": len(result.encode("utf-8")),
        "sha256": hashlib.sha256(result.encode("utf-8")).hexdigest(),
    }
except Exception as exc:
    reply = {"ok": False, "seconds": round(time.monotonic() - started, 3),
             "error": f"{type(exc).__name__}: {exc}"[:400]}
print(json.dumps(reply), flush=True)
assert cc.shutdown()["completed"]
'''


def private_environment(root: Path, **overrides) -> dict:
    """Remove inherited credentials, runtime pins and native C-Two settings."""
    fallback = root / "native-fallback"
    fallback.mkdir(mode=0o700, exist_ok=True)
    return _child_environment(root, {"C2_IPC_ROOT": str(fallback), **overrides})


class ProbeServer:
    """One test-owned real C-Two server with the frozen profile (or a raw override)."""

    def __init__(self, root: Path, *, mode: str = "profile", overrides: dict | None = None, name: str = "probe"):
        self.root = Path(root)
        self.script = self.root / f"{name}-server.py"
        self.script.write_text(PROBE_SERVER)
        self.endpoint_path = self.root / f"{name}-endpoint.json"
        self.stop_path = self.root / f"{name}-stop"
        self.log_path = self.root / f"{name}-server.log"
        self.log = open(self.log_path, "wb")
        environment = private_environment(
            self.root,
            PROBE_SERVER_MODE=mode,
            PROBE_SERVER_OVERRIDES=json.dumps(overrides or {}),
        )
        self.process = subprocess.Popen(
            [sys.executable, str(self.script), str(self.endpoint_path), str(self.stop_path)],
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=self.log,
            stderr=self.log,
            start_new_session=True,
        )
        self.address = self._await_endpoint()

    def _await_endpoint(self) -> str:
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if self.endpoint_path.exists():
                return json.loads(self.endpoint_path.read_text())["address"]
            if self.process.poll() is not None:
                raise AssertionError(f"probe server exited early: {self._log_tail()}")
            time.sleep(0.05)
        raise AssertionError(f"probe server did not publish an endpoint: {self._log_tail()}")

    def _log_tail(self) -> str:
        self.log.flush()
        return self.log_path.read_text()[-1500:]

    @property
    def pid(self) -> int:
        return self.process.pid

    def call(self, operation: str, argument: str, *, client_mode: str = "profile", overrides: dict | None = None) -> dict:
        """One client subprocess that configures itself before connecting."""
        script = self.root / "probe-client.py"
        if not script.exists():
            script.write_text(PROBE_CLIENT)
        environment = private_environment(
            self.root,
            PROBE_CLIENT_MODE=client_mode,
            PROBE_CLIENT_OVERRIDES=json.dumps(overrides or {}),
        )
        completed = subprocess.run(
            [sys.executable, str(script), self.address, operation, argument],
            env=environment,
            capture_output=True,
            text=True,
            timeout=180,
        )
        text = completed.stdout.strip().splitlines()
        if not text:
            return {"ok": False, "error": (completed.stdout + completed.stderr)[-400:]}
        return json.loads(text[-1])

    def spawn(self, operation: str, argument: str) -> subprocess.Popen:
        script = self.root / "probe-client.py"
        if not script.exists():
            script.write_text(PROBE_CLIENT)
        return subprocess.Popen(
            [sys.executable, str(script), self.address, operation, argument],
            env=private_environment(self.root),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

    def close(self) -> None:
        self.stop_path.write_text("1")
        try:
            self.process.wait(timeout=30)
        except subprocess.TimeoutExpired:  # pragma: no cover - test watchdog
            self.process.kill()
            self.process.wait(timeout=10)
        self.log.close()


class ProfileTests(unittest.TestCase):
    """Public setup API, safe directories and the native domain fence."""

    def setUp(self):
        import c_two as cc
        self.temp = tempfile.TemporaryDirectory(prefix="rpc-config-")
        self.addCleanup(self.temp.cleanup)
        self.addCleanup(shutdown_private_rpc)
        self.state = Path(self.temp.name).resolve()

    def test_profile_disables_pool_and_preserves_non_pool_limits(self):
        server, client = rpc_config.report("server"), rpc_config.report("client")
        self.assertEqual(set(server["overrides"]), set(rpc_config.WHITELISTED_KEYS["server"]))
        self.assertEqual(set(client["overrides"]), set(rpc_config.WHITELISTED_KEYS["client"]))
        self.assertIs(server["overrides"]["pool_enabled"], False)
        self.assertFalse(server["sharedMemoryDisabled"])
        for values in (server, client):
            self.assertFalse(any("pool" in key.lower() for key in values["capacity"]))
            self.assertNotIn("pool_segment_size", values["overrides"])
            self.assertNotIn("max_pool_segments", values["overrides"])
        self.assertEqual(server["capacity"]["reassemblyCapacityBytes"], 32 * MIB)
        self.assertGreaterEqual(server["capacity"]["maxReassemblyBytes"], MAX_MESSAGE_BYTES)
        self.assertGreater(server["overrides"]["max_execution_workers"], WAIT_CAPACITY_DEFAULT)
        self.assertLessEqual(server["overrides"]["max_execution_workers"], 64)

    def test_configure_is_idempotent_and_writes_no_environment(self):
        import c_two as cc
        before = dict(os.environ)
        first = rpc_config.configure_server(self.state)
        self.assertEqual(rpc_config.configure_server(self.state), first)
        self.assertEqual(rpc_config.configure_client(self.state)["role"], "client")
        self.assertEqual(Path(cc.local_endpoint_context().root), self.state / "ipc")
        self.assertEqual((self.state / "ipc").stat().st_mode & 0o777, 0o700)
        self.assertEqual(dict(os.environ), before)

    def test_windows_never_passes_root_override(self):
        with mock.patch.object(rpc_config, "os", SimpleNamespace(name="nt", environ=os.environ)), mock.patch.object(rpc_config.cc, "set_local_endpoint") as endpoint:
            rpc_config.configure_local_endpoint(self.state)
            endpoint.assert_called_once_with()
        self.assertFalse((self.state / "ipc").exists())

    def test_0755_state_creates_0700_ipc_without_parent_repair(self):
        """R-02 setup boundary; the transport test separately verifies I/O."""
        from hey_my_buddy.errors import BoardError
        self.state.chmod(0o755)
        error = None
        try:
            root = rpc_config.configure_local_endpoint(self.state)
        except BoardError as caught:
            error = caught
        self.assertIsNone(error, "0755 state must be accepted without chmod")
        self.assertEqual(root, self.state / "ipc")
        self.assertEqual(self.state.stat().st_mode & 0o777, 0o755)
        self.assertEqual(root.stat().st_mode & 0o777, 0o700)
        # Existing non-writable-by-group/others modes are left for native access
        # checks; create=False must never turn setup into a permission repair.
        root.chmod(0o755)
        self.assertEqual(rpc_config.configure_local_endpoint(self.state, create=False), root)
        self.assertEqual(root.stat().st_mode & 0o777, 0o755)

    def test_endpoint_registration_refuses_unsafe_ipc_without_repair(self):
        """R-03: real SDK access refusal is an endpoint start failure."""
        from hey_my_buddy.errors import BoardError
        from hey_my_buddy.buddy.harnesses.c_two_live import CTwoLiveEndpoint
        from hey_my_buddy.buddy.harnesses.live import LiveCapabilities
        from hey_my_buddy.buddy.harnesses.run_contract import RunIdentity
        import c_two as cc
        @cc.crm(namespace="buddy.fixture.live", version="1.0.0")
        class FixtureLive:
            def request(self, request_json: str) -> str: ...
            def observe(self, request_json: str) -> str: ...
            def capabilities(self, request_json: str) -> str: ...
        run = RunIdentity(task_id="rpc-test", attempt_id="rpc-attempt", generation=1,
                          invocation_id="rpc-invocation", input_sha256="a" * 64)
        ipc = self.state / "ipc"
        ipc.mkdir(mode=0o700)
        self.state.chmod(0o755)
        for mode, reason in ((0o775, "group and other users must not have write permission"),
                             (0o777, "group and other users must not have write permission"),
                             (0o500, "effective user lacks read/write/traverse access")):
            with self.subTest(mode=oct(mode)):
                ipc.chmod(mode)
                target = CTwoLiveEndpoint(run, LiveCapabilities(inquiry_delivery="unsupported"),
                                         FixtureLive, state_dir=self.state)
                # Selection alone succeeds; only real native I/O checks access.
                rpc_config.configure_local_endpoint(self.state, create=False)
                error = None
                try:
                    target.start()
                except Exception as caught:
                    error = caught
                self.assertIsInstance(error, BoardError)
                self.assertEqual(error.code, "LIVE_ENDPOINT_START_FAILED")
                self.assertIsInstance(error.__cause__, RuntimeError)
                self.assertIn(reason, str(error.__cause__))
                self.assertEqual(error.message, str(error.__cause__))
                self.assertEqual(ipc.stat().st_mode & 0o777, mode)
                self.assertTrue(cc.shutdown()["completed"])
        ipc.chmod(0o700)

    def test_bad_state_or_ipc_is_refused_without_repair(self):
        """R-03: structural path guards keep their private-path refusal."""
        from hey_my_buddy.errors import BoardError
        ipc = self.state / "ipc"
        ipc.mkdir(mode=0o700)
        file_state = self.state / "file-state"
        file_state.write_text("ordinary file")
        with self.assertRaises(BoardError) as refused:
            rpc_config.configure_client(file_state)
        self.assertEqual(refused.exception.code, "PRIVATE_PATH_UNSAFE")
        file_ipc = self.state / "file-ipc"
        file_ipc.mkdir()
        (file_ipc / "ipc").write_text("ordinary file")
        with self.assertRaises(BoardError) as refused:
            rpc_config.configure_client(file_ipc)
        self.assertEqual(refused.exception.code, "PRIVATE_PATH_UNSAFE")
        link = self.state / "link"
        link.symlink_to(ipc, target_is_directory=True)
        with self.assertRaises(BoardError) as refused:
            rpc_config._validate_path(link / "child", boundary=self.state)
        self.assertEqual(refused.exception.code, "PRIVATE_PATH_UNSAFE")
        # A chosen public root may pass through a link. Preserve the nested
        # private-link assertion within its explicit state boundary instead.
        with self.assertRaises(BoardError) as refused:
            rpc_config._validate_path(link / "child" / "inner", boundary=self.state)
        self.assertEqual(refused.exception.details["path"], str(link))
        linked_state = self.state / "linked-state"
        linked_state.mkdir()
        (linked_state / "ipc").symlink_to(ipc, target_is_directory=True)
        with self.assertRaises(BoardError):
            rpc_config.configure_client(linked_state)
        with self.assertRaises(BoardError):
            rpc_config._validate_path(self.state / "unused" / "..")
        with self.assertRaises(BoardError):
            rpc_config.configure_client(self.state / "unused" / "..")

    @unittest.skipIf(os.name == "nt", "POSIX state-root symlink boundary")
    def test_linked_state_root_is_refused_with_root_link_diagnostic_before_changes(self):
        from hey_my_buddy.errors import BoardError
        target = self.state / "target"
        target.mkdir(mode=0o755)
        target.chmod(0o755)
        alias = self.state / "state-alias"
        alias.symlink_to(target, target_is_directory=True)
        marker = self.state / "untouched"
        marker.write_text("private sibling")
        before = sorted(self.state.rglob("*"))
        with self.assertRaises(BoardError) as refused:
            rpc_config.configure_local_endpoint(alias)
        self.assertEqual(target.stat().st_mode & 0o777, 0o755)
        self.assertEqual(sorted(self.state.rglob("*")), before)
        self.assertEqual(marker.read_text(), "private sibling")
        self.assertTrue(alias.is_symlink())
        self.assertFalse((target / "ipc").exists())
        self.assertEqual(refused.exception.code, "PRIVATE_PATH_UNSAFE")
        self.assertEqual(refused.exception.details["path"], str(alias))
        self.assertEqual(refused.exception.message, "IPC path contains a linked or unsafe component")

    def test_read_only_setup_does_not_create_or_chmod_missing_directories(self):
        from hey_my_buddy.errors import BoardError
        missing = self.state / "missing"
        with self.assertRaises(BoardError):
            rpc_config.configure_client(missing, create=False)
        self.assertFalse(missing.exists())
        with self.assertRaises(BoardError):
            rpc_config.configure_client(self.state, create=False)
        self.assertFalse((self.state / "ipc").exists())

    def test_active_root_is_fenced_and_complete_shutdown_allows_new_root(self):
        import c_two as cc
        from hey_my_buddy.protocol.contracts import BuddyControl, CONTROL_NAME
        rpc_config.configure_client(self.state)
        # Even a failed native connection freezes the active domain. This address
        # is absent inside this test's private root, never a public endpoint.
        with self.assertRaises(Exception):
            cc.connect(BuddyControl, name=CONTROL_NAME, address="ipc://rpc-config-absent")
        other = self.state / "other"
        with self.assertRaisesRegex(Exception, "configuration is frozen"):
            rpc_config.configure_client(other)
        self.assertEqual(Path(cc.local_endpoint_context().root), self.state / "ipc")
        self.assertTrue(cc.shutdown()["completed"])
        rpc_config.configure_client(other)
        self.assertEqual(Path(cc.local_endpoint_context().root), other / "ipc")

    def test_wait_admission_cannot_consume_every_native_callback(self):
        from hey_my_buddy.blackboard.service.daemon import Daemon
        from hey_my_buddy.errors import BoardError
        with mock.patch.dict(os.environ, {"HMB_WAIT_CAPACITY": "64"}):
            with self.assertRaises(BoardError) as raised:
                Daemon(self.state)
            self.assertEqual(raised.exception.code, "INVALID_ARGUMENT")
        with mock.patch.dict(os.environ, {"HMB_WAIT_CAPACITY": "48"}):
            self.assertEqual(Daemon(self.state).wait_admission.capacity, 48)


class IsolatedTransportTests(unittest.TestCase):
    """V-03/V-04/V-05: released RPC and public memory_stats, in private processes."""

    def setUp(self):
        self.work = Path(tempfile.mkdtemp(prefix="rpc-probe-")).resolve()
        self.addCleanup(shutil.rmtree, self.work, ignore_errors=True)
        self.probes = []
        self.addCleanup(self._close_probes)

    def _close_probes(self):
        for probe in reversed(self.probes):
            probe.close()

    def probe(self, **kwargs):
        server = ProbeServer(self.work, **kwargs)
        self.probes.append(server)
        return server

    def test_0755_state_creates_0700_ipc_and_round_trips(self):
        """R-02: a real published server/client uses an ordinary state parent."""
        self.work.chmod(0o755)
        self.assertFalse((self.work / "ipc").exists())
        server = self.probe()
        self.assertEqual(self.work.stat().st_mode & 0o777, 0o755)
        self.assertEqual((self.work / "ipc").stat().st_mode & 0o777, 0o700)
        self.assertEqual(Path(json.loads(server.endpoint_path.read_text())["root"]), self.work / "ipc")
        reply = server.call("echo", "0755-parent")
        self.assertTrue(reply["ok"], reply)
        self.assertEqual(reply["sha256"], hashlib.sha256(b"0755-parent").hexdigest())

    def test_maximum_legal_payload_round_trips_and_over_limit_fails_explicitly(self):
        server = self.probe()
        for size in (4096, MAX_MESSAGE_BYTES):
            legal = server.call("echo", f"bytes:{size}")
            self.assertTrue(legal["ok"], legal)
            self.assertEqual(legal["version"], "0.7.5")
            self.assertEqual(legal["bytes"], size)
            self.assertEqual(legal["sha256"], hashlib.sha256(b"x" * size).hexdigest())
        skeleton = encode_message({"token": "t", "pad": ""})
        pad = "x" * (MAX_MESSAGE_BYTES - len(skeleton.encode()))
        self.assertEqual(len(encode_message({"token": "t", "pad": pad}).encode()), MAX_MESSAGE_BYTES)
        with self.assertRaises(ServiceError) as rejected:
            encode_message({"token": "t", "pad": pad + "x"})
        self.assertEqual(rejected.exception.code, "MESSAGE_TOO_LARGE")
        over = server.call("echo", f"bytes:{40 * MIB}")
        self.assertFalse(over["ok"], over)
        self.assertIn("max_payload_size", over["error"])
        self.assertIn(str(rpc_config.SERVER_OVERRIDES["max_payload_size"]), over["error"])
        self.assertLess(over["seconds"], 10)
        self.assertTrue(server.call("echo", "still-here")["ok"])

    def test_concurrent_large_transfers_complete_without_pool(self):
        server = self.probe()
        clients = [server.spawn("echo", f"bytes:{7 * MIB}") for _ in range(6)]
        results = []
        for client in clients:
            out, err = client.communicate(timeout=180)
            self.assertEqual(client.returncode, 0, err)
            results.append(json.loads(out.strip().splitlines()[-1]))
        for reply in results:
            self.assertTrue(reply["ok"], reply)
            self.assertEqual(reply["bytes"], 7 * MIB)
            self.assertEqual(reply["sha256"], hashlib.sha256(b"x" * (7 * MIB)).hexdigest())

    def test_disabled_pool_releases_outgoing_shm_after_real_rpc(self):
        server = self.probe()
        reply = server.call("echo", f"bytes:{8 * MIB}")
        self.assertTrue(reply["ok"], reply)
        cell = reply["memory"]["runtime_outgoing"]["cells"]["shm"]
        print("V-03 outgoing SHM accounting: " + json.dumps(cell))
        self.assertEqual(cell["used_bytes"], 0, "disabled buddy pool must release temporary outgoing SHM")
        self.assertEqual(cell["peak_bytes"], 8392704)

    def test_private_root_overrides_inherited_namespace(self):
        # A mutation remains private: the deliberately conflicting native fallback
        # is also a test-owned 0700 directory, never the default public namespace.
        server = self.probe()
        endpoint = json.loads(server.endpoint_path.read_text())
        self.assertEqual(Path(endpoint["root"]), self.work / "ipc")
        reply = server.call("echo", "private-root")
        self.assertTrue(reply["ok"], reply)

    def test_different_private_root_cannot_connect(self):
        server = self.probe()
        other = self.work / "other"
        other.mkdir(mode=0o700)
        script = other / "client.py"
        script.write_text(PROBE_CLIENT)
        completed = subprocess.run([sys.executable, str(script), server.address, "echo", "unreachable"],
                                   env=private_environment(other), capture_output=True, text=True, timeout=30)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertFalse(json.loads(completed.stdout.strip().splitlines()[-1])["ok"])
        self.assertTrue(server.call("echo", "still-here")["ok"])


class DaemonFixtureCleanupTests(unittest.TestCase):
    """A failed private-service stop retains its evidence and owned process."""

    def test_cleanup_waits_for_owned_service_before_removing_private_root(self):
        work = Path(tempfile.mkdtemp(prefix="rpc-daemon-cleanup-"))
        self.addCleanup(shutil.rmtree, work)
        state = work / "state"
        state.mkdir(mode=0o700)
        fixture = RealDaemonControlTests.__new__(RealDaemonControlTests)
        fixture.work, fixture.state = work, state
        child = mock.Mock()
        fixture.children = [child]
        with mock.patch(__name__ + ".stop_private_service") as stop, \
                mock.patch(__name__ + ".shutdown_private_rpc") as shutdown, \
                mock.patch(__name__ + ".shutil.rmtree") as remove:
            order = mock.Mock()
            for name, operation in (("stop", stop), ("child", child.wait),
                                    ("shutdown", shutdown), ("remove", remove)):
                order.attach_mock(operation, name)
            fixture._cleanup()
        self.assertEqual(order.mock_calls, [mock.call.stop(state), mock.call.child(timeout=15),
                                           mock.call.shutdown(), mock.call.remove(work)])
        child.terminate.assert_not_called()
        child.kill.assert_not_called()

    def test_unconfirmed_stop_retains_the_private_root(self):
        work = Path(tempfile.mkdtemp(prefix="rpc-daemon-cleanup-"))
        self.addCleanup(shutil.rmtree, work)
        state = work / "state"
        state.mkdir(mode=0o700)
        fixture = RealDaemonControlTests.__new__(RealDaemonControlTests)
        fixture.work, fixture.state, fixture.children = work, state, [mock.Mock()]
        with mock.patch(__name__ + ".stop_private_service", side_effect=AssertionError("stop unconfirmed")), \
                mock.patch(__name__ + ".shutil.rmtree") as remove:
            with self.assertRaisesRegex(AssertionError, "stop unconfirmed"):
                fixture._cleanup()
            remove.assert_not_called()
        self.assertTrue(work.is_dir())


class RealDaemonControlTests(unittest.TestCase):
    """Real command executions leave control RPC responsive while waits are admitted."""

    WAIT_CAPACITY = 6
    WAIT_MILLISECONDS = 3500
    CONTROL_BUDGET_SECONDS = 2.0

    def setUp(self):
        self.work = Path(tempfile.mkdtemp(prefix="hey-my-buddy-rpc-daemon-"))
        os.chmod(self.work, 0o700)
        self.state = self.work / "state"
        self.state.mkdir(mode=0o700)
        self.children: list[subprocess.Popen] = []
        self.addCleanup(self._cleanup)
        self.endpoint = self._start_daemon()
        health = call_board("health", {}, endpoint=self.endpoint, state_dir=self.state)
        self.assertIs(health["rpcProfile"]["overrides"]["pool_enabled"], False)

    def _cleanup(self):
        stop_private_service(self.state)
        for child in self.children:
            child.wait(timeout=15)
        shutdown_private_rpc()
        shutil.rmtree(self.work)

    def _start_daemon(self) -> dict:
        environment = private_environment(self.state, HMB_WAIT_CAPACITY=str(self.WAIT_CAPACITY),
                                          HMB_MAX_CONCURRENT="2")
        log_path = self.work / "daemon.log"
        log = open(log_path, "ab")
        process = subprocess.Popen([sys.executable, "-m", "hey_my_buddy.blackboard.service.daemon"],
                                   env=environment, stdin=subprocess.DEVNULL, stdout=log,
                                   stderr=log, start_new_session=True)
        self.children.append(process)
        deadline = time.monotonic() + 30
        health = None
        while time.monotonic() < deadline:
            endpoint = _read_endpoint(self.state)
            if endpoint:
                try:
                    health = _request(endpoint, "health", {}, state_dir=self.state)
                    break
                except ServiceError:
                    pass
            if process.poll() is not None:
                log.close()
                raise AssertionError(f"daemon exited early: {log_path.read_text()[-2000:]}")
            time.sleep(0.05)
        log.close()
        if health is None:
            raise AssertionError(f"daemon never became healthy: {log_path.read_text()[-2000:]}")
        return _read_endpoint(self.state)

    def _submit_command(self, request_id: str, *, sleep: float) -> str:
        cwd = self.work / request_id
        cwd.mkdir(parents=True, exist_ok=True)
        reply = call_board("task_submit", {
            "requestId": request_id, "task": "private command probe", "cwd": str(cwd),
            "adapter": "command", "argv": [sys.executable, "-c", f"import time; time.sleep({sleep})"],
            "expectedSeconds": 30,
        }, endpoint=self.endpoint, state_dir=self.state)
        return reply["task"]["runId"]

    def _admission(self) -> dict:
        return call_board("wait_capacity", {}, endpoint=self.endpoint,
                          state_dir=self.state, resource="wait")

    def _await_admitted(self, expected: int) -> None:
        deadline = time.monotonic() + 10
        admitted = None
        while time.monotonic() < deadline:
            admitted = self._admission().get("admitted")
            if admitted == expected:
                return
            time.sleep(0.05)
        self.fail(f"every wait slot must be busy before control traffic; admitted={admitted}")

    def _wait(self, run_id: str) -> dict:
        return call_board("task_wait", {"runId": run_id, "timeoutMs": self.WAIT_MILLISECONDS},
                          endpoint=self.endpoint, state_dir=self.state, resource="wait")

    def test_admitted_waits_do_not_starve_control_or_command_completion(self):
        waited = self._submit_command("rpc-waited-1", sleep=15)
        cancellable = self._submit_command("rpc-cancel-1", sleep=15)
        deadline = time.monotonic() + 10
        while call_board("task_get", {"runId": waited}, endpoint=self.endpoint,
                         state_dir=self.state)["task"]["status"] == "queued":
            self.assertLess(time.monotonic(), deadline)
            time.sleep(0.05)
        started = time.monotonic()
        with ThreadPoolExecutor(max_workers=self.WAIT_CAPACITY) as pool:
            waits = [pool.submit(self._wait, waited) for _ in range(self.WAIT_CAPACITY)]
            self._await_admitted(self.WAIT_CAPACITY)
            measured: dict[str, float] = {}

            def control(name: str, operation: str, params: dict) -> dict:
                begin = time.monotonic()
                reply = call_board(operation, params, endpoint=self.endpoint, state_dir=self.state)
                measured[name] = round(time.monotonic() - begin, 3)
                return reply

            self.assertEqual(control("health", "health", {})["status"], "ok")
            self.assertIn("events", control("events", "events_read", {"after": 0}))
            cancelled = control("cancel", "task_cancel", {"runId": cancellable,
                                                           "reason": "rpc profile probe"})
            self.assertTrue(cancelled["task"]["cancelRequested"])
            quick = self._submit_command("rpc-quick", sleep=0)
            self.assertEqual(self._admission()["admitted"], self.WAIT_CAPACITY)
            self.assertFalse(any(wait.done() for wait in waits),
                             "control traffic must not end an admitted wait")
            replies = [wait.result(timeout=self.WAIT_MILLISECONDS / 1000 + 10) for wait in waits]
        elapsed = round(time.monotonic() - started, 3)
        print("daemon control evidence: " + json.dumps({"controlSeconds": measured,
                                                         "waitsSeconds": elapsed}))
        self.assertEqual(len(replies), self.WAIT_CAPACITY)
        for reply in replies:
            self.assertNotIn("error", reply, reply)
        for name, seconds in measured.items():
            self.assertLess(seconds, self.CONTROL_BUDGET_SECONDS,
                            f"{name} was starved behind admitted waits")
        self.assertGreaterEqual(elapsed, self.WAIT_MILLISECONDS / 1000)
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            final = call_board("task_get", {"runId": quick}, endpoint=self.endpoint,
                               state_dir=self.state)["task"]
            if final["status"] == "completed":
                break
            time.sleep(0.05)
        self.assertEqual(final["status"], "completed")
        self.assertTrue(final["resultAvailable"])


if __name__ == "__main__":
    unittest.main()
