"""Private published C-Two 0.7.3 RPC, non-pool accounting and control capacity.

Every native process configures an explicit test-owned state/ipc directory before
local I/O. Memory statistics are C-Two accounting, never RSS.
"""
from __future__ import annotations

import fcntl
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

from support import stop_private_service, stop_private_workers, _child_environment, shutdown_private_rpc

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
state = Path(os.environ["BUDDY_STATE_DIR"])
if os.environ.get("PROBE_SERVER_MODE") == "raw":
    rpc_config.configure_local_endpoint(state)
    cc.set_server(ipc_overrides=json.loads(os.environ["PROBE_SERVER_OVERRIDES"]))
else:
    from hey_my_buddy.protocol import rpc_config
    rpc_config.configure_server(state)
cc.register(ProbeContract, ProbeImpl(), name="buddy-probe",
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
from hey_my_buddy.protocol import rpc_config
state = Path(os.environ["BUDDY_STATE_DIR"])
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
    with cc.connect(ProbeContract, name="buddy-probe", address=address) as probe:
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
        with mock.patch.object(rpc_config.os, "name", "nt"), mock.patch.object(rpc_config.cc, "set_local_endpoint") as endpoint:
            rpc_config.configure_local_endpoint(self.state)
            endpoint.assert_called_once_with()
        self.assertFalse((self.state / "ipc").exists())

    def test_bad_state_or_ipc_is_refused_without_repair(self):
        from hey_my_buddy.errors import BoardError
        ipc = self.state / "ipc"
        ipc.mkdir(mode=0o700)
        for path in (self.state, ipc):
            path.chmod(0o750)
            with self.assertRaises(BoardError):
                rpc_config.configure_client(self.state)
            self.assertEqual(path.stat().st_mode & 0o777, 0o750)
            path.chmod(0o700)
        # Owner-private state may be read-only, but IPC's native container must
        # retain exactly 0700. Do not silently repair an owner-only 0500 IPC root.
        ipc.chmod(0o500)
        with self.assertRaises(BoardError):
            rpc_config.configure_client(self.state, create=False)
        self.assertEqual(ipc.stat().st_mode & 0o777, 0o500)
        ipc.chmod(0o700)
        file_state = self.state / "file-state"
        file_state.write_text("ordinary file")
        with self.assertRaises(BoardError):
            rpc_config.configure_client(file_state)
        link = self.state / "link"
        link.symlink_to(ipc, target_is_directory=True)
        with self.assertRaises(BoardError):
            rpc_config.configure_client(link / "child")
        with mock.patch.object(rpc_config.os, "geteuid", return_value=os.geteuid() + 1):
            with self.assertRaises(BoardError):
                rpc_config.configure_client(self.state)

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
        with mock.patch.dict(os.environ, {"BUDDY_WAIT_CAPACITY": "64"}):
            with self.assertRaises(BoardError) as raised:
                Daemon(self.state)
            self.assertEqual(raised.exception.code, "INVALID_ARGUMENT")
        with mock.patch.dict(os.environ, {"BUDDY_WAIT_CAPACITY": "48"}):
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

    def test_maximum_legal_payload_round_trips_and_over_limit_fails_explicitly(self):
        server = self.probe()
        for size in (4096, MAX_MESSAGE_BYTES):
            legal = server.call("echo", f"bytes:{size}")
            self.assertTrue(legal["ok"], legal)
            self.assertEqual(legal["version"], "0.7.3")
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


class DaemonFixtureTests(unittest.TestCase):
    """Exercise the real fixture wiring without starting native IPC or workers."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="rpc-daemon-fixture-")
        self.addCleanup(self.temp.cleanup)
        self.fixture = RealDaemonControlTests()
        self.fixture.work = Path(self.temp.name)
        self.fixture.state = self.fixture.work / "state"
        self.fixture.state.mkdir(mode=0o700)
        self.fixture.children = []
        self.fixture.probe_claims = {}

    def test_daemon_environment_health_and_cleanup_select_the_same_state(self):
        fixture = self.fixture
        endpoint = {"address": "ipc://fixture-only"}
        with mock.patch(__name__ + ".subprocess.Popen") as spawn, \
                mock.patch(__name__ + "._read_endpoint", return_value=endpoint) as read, \
                mock.patch(__name__ + "._request", return_value={"managedWorkerIds": []}) as request:
            self.assertEqual(fixture._start_daemon(), endpoint)
        environment = spawn.call_args.kwargs["env"]
        self.assertEqual(Path(environment["BUDDY_STATE_DIR"]), fixture.state)
        self.assertEqual(Path(environment["BUDDY_RUNTIME_ROOT"]), fixture.state / "runtime-root")
        self.assertEqual(Path(environment["C2_IPC_ROOT"]), fixture.state / "native-fallback")
        self.assertEqual(read.call_args_list, [mock.call(fixture.state), mock.call(fixture.state)])
        request.assert_called_once_with(endpoint, "health", {}, state_dir=fixture.state)
        # No real child exists, but exercise cleanup with the actual environment
        # passed to Popen rather than assuming an override reached the subprocess.
        with mock.patch(__name__ + ".stop_private_service") as service, \
                mock.patch(__name__ + ".stop_private_workers") as workers, \
                mock.patch(__name__ + ".shutdown_private_rpc"), \
                mock.patch(__name__ + ".shutil.rmtree"):
            fixture._cleanup()
        selected_state = Path(environment["BUDDY_STATE_DIR"])
        service.assert_called_once_with(selected_state)
        workers.assert_called_once_with(selected_state)

    def test_environment_helper_keeps_its_state_and_runtime_boundary(self):
        state = self.fixture.state
        outside = self.fixture.work / "outside"
        environment = private_environment(state, BUDDY_STATE_DIR=str(outside))
        self.assertEqual(Path(environment["BUDDY_STATE_DIR"]), state)
        with self.assertRaisesRegex(ValueError, "inside its private state"):
            private_environment(state, BUDDY_RUNTIME_ROOT=str(outside))

    def test_cleanup_confirms_only_its_service_workers_and_child_before_removal(self):
        fixture = self.fixture
        child = mock.Mock()
        fixture.children = [child]
        with mock.patch(__name__ + ".stop_private_service") as service, \
                mock.patch(__name__ + ".stop_private_workers") as workers, \
                mock.patch(__name__ + ".shutdown_private_rpc") as shutdown, \
                mock.patch(__name__ + ".shutil.rmtree") as remove:
            order = mock.Mock()
            for name, operation in (("service", service), ("workers", workers),
                                    ("child", child), ("shutdown", shutdown), ("remove", remove)):
                order.attach_mock(operation, name)
            fixture._cleanup()
        self.assertEqual(order.mock_calls, [mock.call.service(fixture.state), mock.call.workers(fixture.state),
                                           mock.call.child.wait(timeout=15), mock.call.shutdown(),
                                           mock.call.remove(fixture.work)])
        child.terminate.assert_not_called()
        child.kill.assert_not_called()

    def test_unconfirmed_cleanup_preserves_the_fixture(self):
        fixture = self.fixture
        for stage in ("service", "workers", "child", "shutdown"):
            with self.subTest(stage=stage), \
                    mock.patch(__name__ + ".stop_private_service") as service, \
                    mock.patch(__name__ + ".stop_private_workers") as workers, \
                    mock.patch(__name__ + ".shutdown_private_rpc") as shutdown, \
                    mock.patch(__name__ + ".shutil.rmtree") as remove:
                child = mock.Mock()
                fixture.children = [child]
                operation = {"service": service, "workers": workers, "child": child.wait, "shutdown": shutdown}[stage]
                failure = subprocess.TimeoutExpired("fixture-daemon", 15) if stage == "child" else AssertionError(stage)
                operation.side_effect = failure
                with self.assertRaises(type(failure)):
                    fixture._cleanup()
                remove.assert_not_called()
                child.terminate.assert_not_called()
                child.kill.assert_not_called()
                self.assertTrue(fixture.work.is_dir())

    def test_cleanup_does_not_hide_directory_removal_errors(self):
        with mock.patch(__name__ + ".stop_private_service"), \
                mock.patch(__name__ + ".stop_private_workers"), \
                mock.patch(__name__ + ".shutdown_private_rpc"), \
                mock.patch(__name__ + ".shutil.rmtree", side_effect=OSError("fixture removal failed")) as remove:
            with self.assertRaisesRegex(OSError, "fixture removal failed"):
                self.fixture._cleanup()
            remove.assert_called_once_with(self.fixture.work)

    def test_cleanup_releases_only_its_synthetic_claims_before_service_stop(self):
        fixture = self.fixture
        fixture.endpoint = {"address": "ipc://fixture-only"}
        claim = {"attemptId": "fixture-attempt", "generation": 7}
        with mock.patch(__name__ + ".call_board", return_value={"claim": {"attempt": claim}}):
            fixture._claim("fixture-worker", "fixture-claim", "fixture-nonce", "fixture-run")
        with mock.patch(__name__ + ".call_board", return_value={"committed": True}) as result, \
                mock.patch(__name__ + ".stop_private_service") as service, \
                mock.patch(__name__ + ".stop_private_workers"), \
                mock.patch(__name__ + ".shutdown_private_rpc"), \
                mock.patch(__name__ + ".shutil.rmtree"):
            order = mock.Mock()
            order.attach_mock(result, "result")
            order.attach_mock(service, "service")
            fixture._cleanup()
        self.assertEqual([call[0] for call in order.mock_calls], ["result", "service"])
        operation, params = result.call_args.args
        self.assertEqual(operation, "worker_result")
        self.assertEqual({key: params[key] for key in ("workerId", "attemptId", "generation", "nonce")},
                         {"workerId": "fixture-worker", "attemptId": "fixture-attempt", "generation": 7,
                          "nonce": "fixture-nonce"})
        self.assertIs(params["shutdownConfirmed"], True)
        self.assertEqual(result.call_args.kwargs, {"endpoint": fixture.endpoint, "state_dir": fixture.state})
        self.assertEqual(fixture.probe_claims, {})

    def test_uncommitted_probe_receipt_preserves_claim_and_state(self):
        fixture = self.fixture
        fixture.endpoint = {"address": "ipc://fixture-only"}
        fixture.probe_claims = {"fixture-attempt": {"attemptId": "fixture-attempt", "generation": 7,
                                                  "workerId": "fixture-worker", "nonce": "fixture-nonce"}}
        with mock.patch(__name__ + ".call_board", return_value={"committed": False}), \
                mock.patch(__name__ + ".stop_private_service") as service, \
                mock.patch(__name__ + ".shutil.rmtree") as remove:
            with self.assertRaisesRegex(AssertionError, "probe receipt"):
                fixture._cleanup()
            service.assert_not_called()
            remove.assert_not_called()
        self.assertIn("fixture-attempt", fixture.probe_claims)


class RealDaemonControlTests(unittest.TestCase):
    """Admitted waits must never starve renew, result, cancel or health on the daemon."""

    WAIT_CAPACITY = 6
    WAIT_MILLISECONDS = 3500
    CONTROL_BUDGET_SECONDS = 2.0

    def setUp(self):
        self.work = Path(tempfile.mkdtemp(prefix="buddy-rpc-daemon-"))
        os.chmod(self.work, 0o700)
        self.state = self.work / "state"
        self.state.mkdir(mode=0o700)
        self.children: list[subprocess.Popen] = []
        self.probe_claims: dict[str, dict] = {}
        self.addCleanup(self._cleanup)
        self.endpoint = self._start_daemon()
        self.assertIs(call_board("health", {}, endpoint=self.endpoint, state_dir=self.state)["rpcProfile"]["overrides"]["pool_enabled"], False)

    def _cleanup(self):
        # These external probes never spawn a harness/child. End their own claims
        # after the wait threads have joined, so cooperative service stop can drain.
        for attempt_id, claim in list(self.probe_claims.items()):
            reply = call_board(
                "worker_result",
                {**claim, "commandId": f"rpc-cleanup-{attempt_id}", "status": "ok",
                 "result": {"note": "synthetic control probe finished; no harness child was started"},
                 "shutdownConfirmed": True, "exitCode": 0},
                endpoint=self.endpoint, state_dir=self.state,
            )
            self.assertTrue(reply["committed"], f"probe receipt was not committed: {reply}")
            del self.probe_claims[attempt_id]
        stop_private_service(self.state)
        stop_private_workers(self.state)
        for child in self.children:
            child.wait(timeout=15)
        shutdown_private_rpc()
        shutil.rmtree(self.work)

    def _start_daemon(self) -> dict:
        environment = private_environment(
            self.state,
            BUDDY_WAIT_CAPACITY=str(self.WAIT_CAPACITY),
            # Two total slots so the waited and the renewable attempt coexist.
            BUDDY_MAX_CONCURRENT="2",
        )
        log_path = self.work / "daemon.log"
        log = open(log_path, "ab")
        process = subprocess.Popen(
            [sys.executable, "-m", "hey_my_buddy.blackboard.service.daemon"],
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=log,
            start_new_session=True,
        )
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
        # A healthy endpoint can precede pool startup. Wait for every managed supervisor
        # to hold its lock so teardown cannot delete a stop request before it exists.
        for worker_id in health.get("managedWorkerIds", []):
            lock = self.state / "workers" / worker_id / "supervisor.lock"
            lock_deadline = time.monotonic() + 25
            while time.monotonic() < lock_deadline:
                if lock.exists():
                    fd = os.open(lock, os.O_RDWR)
                    try:
                        try:
                            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                        except BlockingIOError:
                            break
                        else:
                            fcntl.flock(fd, fcntl.LOCK_UN)
                    finally:
                        os.close(fd)
                time.sleep(0.05)
            else:
                raise AssertionError(f"managed supervisor did not start: {worker_id}")
        return _read_endpoint(self.state)

    def _submit_external(self, request_id: str) -> str:
        # One private cwd per task: two live attempts must not share a workspace
        # reservation, or the second claim is refused with cwd-overlap before any
        # control traffic is involved.
        cwd = self.work / request_id
        cwd.mkdir(parents=True, exist_ok=True)
        reply = call_board(
            "task_submit",
            {"requestId": request_id, "task": "hold for control traffic", "cwd": str(cwd), "adapter": "external"},
            endpoint=self.endpoint, state_dir=self.state,
        )
        return reply["task"]["runId"]

    def _claim(self, worker_id: str, claim_request_id: str, nonce: str, run_id: str) -> dict:
        claim = call_board(
            "worker_claim",
            {"workerId": worker_id, "claimRequestId": claim_request_id, "nonce": nonce, "runId": run_id},
            endpoint=self.endpoint, state_dir=self.state,
        )
        granted = (claim.get("claim") or {}).get("attempt")
        self.assertTrue(granted, f"the probe worker must claim its attempt: {claim.get('reason')}")
        self.probe_claims[granted["attemptId"]] = {
            "workerId": worker_id, "attemptId": granted["attemptId"], "generation": granted["generation"], "nonce": nonce,
        }
        return granted

    def _admission(self) -> dict:
        return call_board("wait_capacity", {}, endpoint=self.endpoint, state_dir=self.state, resource="wait")

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
        return call_board(
            "task_wait",
            {"runId": run_id, "timeoutMs": self.WAIT_MILLISECONDS},
            endpoint=self.endpoint, state_dir=self.state,
            resource="wait",
        )

    def test_waits_and_control_operations_share_execution_capacity_without_starvation(self):
        waited = self._submit_external("rpc-waited-1")
        renewable = self._submit_external("rpc-renew-1")
        cancellable = self._submit_external("rpc-cancel-1")
        for worker_id in ("rpc-probe", "rpc-probe-2"):
            call_board(
                "worker_register",
                {"workerId": worker_id, "adapter": "external", "capabilities": ["external"], "pid": os.getpid()},
                endpoint=self.endpoint, state_dir=self.state,
            )
        first_nonce, second_nonce = "n" * 32, "m" * 32
        waited_claim = self._claim("rpc-probe", "rpc-claim-1", first_nonce, waited)
        renewable_claim = self._claim("rpc-probe-2", "rpc-claim-2", second_nonce, renewable)

        started = time.monotonic()
        with ThreadPoolExecutor(max_workers=self.WAIT_CAPACITY) as pool:
            waits = [pool.submit(self._wait, waited) for _ in range(self.WAIT_CAPACITY)]
            self._await_admitted(self.WAIT_CAPACITY)

            measured: dict[str, float] = {}

            def control(name: str, operation: str, params: dict, resource: str = "control") -> dict:
                begin = time.monotonic()
                reply = call_board(operation, params, endpoint=self.endpoint, state_dir=self.state, resource=resource)
                measured[name] = round(time.monotonic() - begin, 3)
                return reply

            health_reply = control("health", "health", {})
            self.assertEqual(health_reply["status"], "ok")
            renewed = control(
                "renew",
                "worker_renew",
                {
                    "workerId": "rpc-probe-2",
                    "attemptId": renewable_claim["attemptId"],
                    "generation": renewable_claim["generation"],
                    "nonce": second_nonce,
                    "phase": "executing",
                },
            )
            self.assertEqual(renewed["attempt"]["attemptId"], renewable_claim["attemptId"])
            cancelled = control("cancel", "task_cancel", {"runId": cancellable, "reason": "rpc profile probe"})
            self.assertEqual(cancelled["task"]["status"], "cancelled")
            result = control(
                "result",
                "worker_result",
                {
                    "workerId": "rpc-probe-2",
                    "attemptId": renewable_claim["attemptId"],
                    "generation": renewable_claim["generation"],
                    "nonce": second_nonce,
                    "commandId": "rpc-result-1",
                    "status": "ok",
                    "result": {"note": "probe completed while waits were admitted"},
                    "shutdownConfirmed": True,
                    "exitCode": 0,
                },
            )
            self.assertTrue(result["committed"])
            del self.probe_claims[renewable_claim["attemptId"]]
            # Every control operation above committed while all wait slots stayed busy:
            # no admitted wait was ended, converted into an error or left behind.
            self.assertEqual(self._admission()["admitted"], self.WAIT_CAPACITY)
            self.assertFalse(any(wait.done() for wait in waits), "control traffic must not end an admitted wait")
            replies = [wait.result(timeout=self.WAIT_MILLISECONDS / 1000 + 60) for wait in waits]
        elapsed = round(time.monotonic() - started, 3)
        print("daemon control evidence: " + json.dumps({"controlSeconds": measured, "waitsSeconds": elapsed}))
        self.assertEqual(len(replies), self.WAIT_CAPACITY)
        for reply in replies:
            self.assertNotIn("error", reply, reply)
            self.assertEqual(reply["status"], "running")
        for name, seconds in measured.items():
            self.assertLess(seconds, self.CONTROL_BUDGET_SECONDS, f"{name} was starved behind admitted waits")
        self.assertGreaterEqual(
            elapsed, self.WAIT_MILLISECONDS / 1000, "the waits must have been genuinely in flight for their whole window"
        )
        final = call_board("task_get", {"runId": renewable}, endpoint=self.endpoint, state_dir=self.state)
        self.assertEqual(final["task"]["status"], "completed")
        self.assertTrue(final["task"]["resultAvailable"])


if __name__ == "__main__":  # pragma: no cover - manual focus
    unittest.main()
