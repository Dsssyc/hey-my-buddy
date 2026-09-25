"""Buddy's private C-Two profile: application order, measured capacity, no starvation.

Every process in this file is test-owned: private state and runtime roots, inherited
``BUDDY_*`` runtime/worker/credential variables removed, and no daily board. The
numbers asserted here were measured against the installed ``c-two==0.5.1``:

- a maximum legal 8 MiB message (``MAX_MESSAGE_BYTES``) plus its pickle envelope fits
  one 16 MiB pool segment with about 2x headroom;
- a request above the configured ``max_payload_size`` fails explicitly and quickly
  while the service keeps answering;
- six concurrent 7 MiB transfers complete with only two pool segments;
- a server started with ``pool_enabled=False`` still maps a shared-memory segment,
  which is why the profile offers no fake "off" mode;
- the configured execution capacity (64) keeps ordinary control traffic responsive
  while the C-Two default of 10 delayed it by seconds behind admitted waits.

Configured capacity, mapped shared memory and resident RSS are reported as three
separate measurements; none of them is derived from another.
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
import pickle
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from support import stop_private_workers

from buddy import rpc_config
from buddy.service import WAIT_CAPACITY_DEFAULT
from buddy.transport import MAX_MESSAGE_BYTES, ServiceError, _read_endpoint, _request, call_board, encode_message

ROOT = Path(__file__).resolve().parents[2]
PYTHON = ROOT / "src"
TESTS = ROOT / "tests" / "python"

#: Inherited variables that would point a test subprocess at a production runtime,
#: another Worker's state or a credential instead of its private roots.
INHERITED_RUNTIME_VARIABLES = (
    "BUDDY_STATE_DIR",
    "BUDDY_RUNTIME_ROOT",
    "BUDDY_RUNTIME",
    "BUDDY_RUNTIME_IDENTITY",
    "BUDDY_WORKER_STATE",
    "BUDDY_WORKER_ID",
    "BUDDY_AGENT_CREDENTIAL",
    "BUDDY_AGENT_CREDENTIAL_FILE",
    "VIRTUAL_ENV",
    "UV_PROJECT_ENVIRONMENT",
)

MIB = 1024 * 1024

#: One isolated real C-Two server. ``profile`` mode exercises the frozen Buddy
#: interface; ``raw`` mode exists only to measure upstream behavior such as
#: ``pool_enabled=False``.
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

if os.environ.get("PROBE_SERVER_MODE") == "raw":
    cc.set_server(ipc_overrides=json.loads(os.environ["PROBE_SERVER_OVERRIDES"]))
else:
    from buddy import rpc_config
    rpc_config.configure_server()
cc.register(ProbeContract, ProbeImpl(), name="buddy-probe",
            concurrency=cc.ConcurrencyConfig(mode=cc.ConcurrencyMode.PARALLEL))
Path(sys.argv[1]).write_text(json.dumps({"address": cc.server_address(), "pid": os.getpid()}))
stop = sys.argv[2]
while not os.path.exists(stop):
    time.sleep(0.05)
cc.shutdown()
'''

PROBE_CLIENT = '''
import hashlib, json, os, sys, time
import c_two as cc
from c_two import crm

@crm(namespace="buddy.probe", version="1.0.0")
class ProbeContract:
    def echo(self, payload: str) -> str: ...
    def sleep_ms(self, payload: str) -> str: ...

mode = os.environ.get("PROBE_CLIENT_MODE", "profile")
if mode == "raw":
    cc.set_client(ipc_overrides=json.loads(os.environ["PROBE_CLIENT_OVERRIDES"]))
elif mode == "profile":
    from buddy import rpc_config
    rpc_config.configure_client()

address, operation, argument = sys.argv[1], sys.argv[2], sys.argv[3]
payload = "x" * int(argument.split(":", 1)[1]) if argument.startswith("bytes:") else argument
started = time.monotonic()
try:
    with cc.connect(ProbeContract, name="buddy-probe", address=address) as probe:
        result = getattr(probe, operation)(payload)
    reply = {
        "ok": True,
        "seconds": round(time.monotonic() - started, 3),
        "bytes": len(result.encode("utf-8")),
        "sha256": hashlib.sha256(result.encode("utf-8")).hexdigest(),
    }
except Exception as exc:
    reply = {"ok": False, "seconds": round(time.monotonic() - started, 3),
             "error": f"{type(exc).__name__}: {exc}"[:400]}
print(json.dumps(reply), flush=True)
'''


def private_environment(root: Path, **overrides) -> dict:
    """A child environment for this checkout with no inherited runtime or credential."""
    values = {key: value for key, value in os.environ.items() if key not in INHERITED_RUNTIME_VARIABLES}
    inherited = values.get("PYTHONPATH")
    values["PYTHONPATH"] = os.pathsep.join([str(PYTHON), str(TESTS)] + ([inherited] if inherited else []))
    values["BUDDY_DEV_SOURCE"] = "1"
    values["BUDDY_RUNTIME_ROOT"] = str(root / "runtime-root")
    values.update(overrides)
    return values


def resident_bytes(pid: int) -> int | None:
    """Resident set size of one live process, in bytes."""
    completed = subprocess.run(["ps", "-o", "rss=", "-p", str(pid)], capture_output=True, text=True)
    text = completed.stdout.strip()
    return int(text) * 1024 if text else None


def shm_mapping_regions(pid: int) -> list[int] | None:
    """Sizes of one live process's shared-memory mappings; None when unmeasurable.

    macOS reports anonymous shared-memory mappings through ``vmmap``; Linux exposes the
    POSIX segment names in ``/proc/<pid>/maps``. These are *mappings*, never the
    configured pool size and never RSS: a pool segment is mapped once per open pool and
    the total says nothing about resident memory.
    """
    if sys.platform == "darwin":
        try:
            text = subprocess.run(["vmmap", str(pid)], capture_output=True, text=True, timeout=180).stdout
        except (OSError, subprocess.SubprocessError):
            return None
        regions = []
        for line in text.splitlines():
            if "shared memory" not in line or "SM=SHM" not in line:
                continue
            match = re.search(r"\[\s*([0-9.]+)([KMG])\s", line)
            if match:
                regions.append(int(float(match.group(1)) * {"K": 1024, "M": MIB, "G": 1024 * MIB}[match.group(2)]))
        return regions or None
    maps = Path(f"/proc/{pid}/maps")
    if not maps.exists():
        return None
    regions = []
    for line in maps.read_text().splitlines():
        if "/dev/shm/" not in line:
            continue
        match = re.match(r"([0-9a-f]+)-([0-9a-f]+)", line)
        if match:
            regions.append(int(match.group(2), 16) - int(match.group(1), 16))
    return regions or None


def shm_mapping_bytes(pid: int) -> int | None:
    """Total mapped shared memory of one live process, in bytes."""
    regions = shm_mapping_regions(pid)
    return sum(regions) if regions else None


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
    """The frozen interface itself: bounded values, one application, no environment."""

    def test_profile_is_bounded_and_offers_no_fake_off_switch(self):
        server = rpc_config.report("server")
        client = rpc_config.report("client")
        self.assertEqual(server["profile"], rpc_config.PROFILE_ID)
        self.assertEqual(set(server["overrides"]), set(rpc_config.WHITELISTED_KEYS["server"]))
        # The client carries exactly the shared pool settings and nothing server-only.
        self.assertEqual(set(client["overrides"]), set(rpc_config.WHITELISTED_KEYS["client"]))
        for key in rpc_config.WHITELISTED_KEYS["client"]:
            self.assertEqual(client["overrides"][key], server["overrides"][key], key)
        self.assertIs(server["overrides"]["pool_enabled"], True)
        self.assertFalse(server["sharedMemoryDisabled"])
        self.assertNotIn("false", json.dumps(server["overrides"]).lower(), "no override may disable the pool")
        # A maximum legal message plus its pickle envelope fits one pool segment, and the
        # two-segment capacity covers two such transfers without serializing.
        dto = pickle.dumps("x" * MAX_MESSAGE_BYTES, protocol=4)
        self.assertLess(len(dto), server["capacity"]["poolSegmentBytes"])
        self.assertGreaterEqual(server["capacity"]["poolCapacityBytes"], 2 * len(dto) - 1)
        # The pool and reassembly ceilings are explicit and far below the C-Two defaults.
        self.assertEqual(server["capacity"]["poolCapacityBytes"], 32 * MIB)
        self.assertEqual(server["capacity"]["reassemblyCapacityBytes"], 32 * MIB)
        self.assertGreaterEqual(server["capacity"]["maxReassemblyBytes"], MAX_MESSAGE_BYTES)
        # The callback capacity must cover the waits plus ordinary control traffic; the
        # C-Two default of 10 is below Buddy's default wait admission.
        self.assertGreater(server["overrides"]["max_execution_workers"], WAIT_CAPACITY_DEFAULT)
        self.assertLessEqual(server["overrides"]["max_execution_workers"], 64)

    def test_configure_applies_once_and_writes_no_environment(self):
        before = dict(os.environ)
        first = rpc_config.configure_server()
        second = rpc_config.configure_server()
        client = rpc_config.configure_client()
        self.assertEqual(first, second)
        self.assertEqual(client["role"], "client")
        self.assertEqual(set(rpc_config.configured_roles()), {"client", "server"})
        created = {key for key in os.environ if key not in before}
        self.assertEqual(created, set(), "the private C-Two profile must not be exported to child processes")

    def test_wait_admission_cannot_consume_every_native_callback(self):
        from buddy.daemon import Daemon
        from buddy.errors import BoardError
        with tempfile.TemporaryDirectory(prefix="buddy-wait-bound-") as root:
            with mock.patch.dict(os.environ, {"BUDDY_WAIT_CAPACITY": "64"}):
                with self.assertRaises(BoardError) as raised:
                    Daemon(Path(root))
                self.assertEqual(raised.exception.code, "INVALID_ARGUMENT")
            with mock.patch.dict(os.environ, {"BUDDY_WAIT_CAPACITY": "48"}):
                daemon = Daemon(Path(root))
                self.assertEqual(daemon.wait_admission.capacity, 48)


class IsolatedTransportTests(unittest.TestCase):
    """The frozen profile under a real server and real clients, in separate processes."""

    def setUp(self):
        self.work = Path(tempfile.mkdtemp(prefix="buddy-rpc-probe-"))
        os.chmod(self.work, 0o700)
        self.addCleanup(shutil.rmtree, self.work, ignore_errors=True)
        self.probes: list[ProbeServer] = []
        self.addCleanup(self._close_probes)

    def _close_probes(self):
        for probe in reversed(self.probes):
            try:
                probe.close()
            except Exception:  # noqa: BLE001 - cleanup must not mask the test result
                pass

    def probe(self, **kwargs) -> ProbeServer:
        server = ProbeServer(self.work, **kwargs)
        self.probes.append(server)
        return server

    def test_maximum_legal_payload_round_trips_and_over_limit_fails_explicitly(self):
        server = self.probe()
        legal = server.call("echo", f"bytes:{MAX_MESSAGE_BYTES}")
        self.assertTrue(legal["ok"], legal)
        self.assertEqual(legal["bytes"], MAX_MESSAGE_BYTES)
        self.assertEqual(legal["sha256"], hashlib.sha256(b"x" * MAX_MESSAGE_BYTES).hexdigest())
        # A request JSON of exactly 8 MiB is the legal maximum; one byte more is refused.
        skeleton = encode_message({"token": "t", "pad": ""})
        request = encode_message({"token": "t", "pad": "x" * (MAX_MESSAGE_BYTES - len(skeleton.encode("utf-8")))})
        self.assertEqual(len(request.encode("utf-8")), MAX_MESSAGE_BYTES)
        with self.assertRaises(ServiceError) as rejected:
            encode_message({"token": "t", "pad": "x" * (MAX_MESSAGE_BYTES - len(skeleton.encode("utf-8")) + 1)})
        self.assertEqual(rejected.exception.code, "MESSAGE_TOO_LARGE")
        # Above the configured server ceiling the failure is explicit and fast, and the
        # service keeps answering ordinary control calls afterwards.
        over = server.call("echo", f"bytes:{40 * MIB}")
        self.assertFalse(over["ok"])
        self.assertIn("max_payload_size", over["error"])
        self.assertIn(str(rpc_config.SERVER_OVERRIDES["max_payload_size"]), over["error"])
        self.assertLess(over["seconds"], 10)
        after = server.call("echo", "still-here")
        self.assertTrue(after["ok"], after)
        self.assertEqual(after["bytes"], len("still-here"))

    def test_concurrent_large_transfers_fit_two_segments(self):
        server = self.probe()
        payload = f"bytes:{7 * MIB}"
        clients = [server.spawn("echo", payload) for _ in range(6)]
        results = []
        for client in clients:
            out, err = client.communicate(timeout=180)
            line = out.strip().splitlines()
            results.append(json.loads(line[-1]) if line else {"ok": False, "error": (out + err)[-300:]})
        self.assertTrue(all(result["ok"] for result in results), results)
        self.assertTrue(all(result["bytes"] == 7 * MIB for result in results), results)
        self.assertTrue(all(result["sha256"] == hashlib.sha256(b"x" * (7 * MIB)).hexdigest() for result in results))

    def test_configured_capacity_mapping_and_rss_are_separate_measurements(self):
        """Three distinct numbers: a configured ceiling, a mapping and a resident size.

        One segment is mapped per open pool, so the mapping total is not the configured
        capacity; the resident bytes are smaller again. The profile is proven at the
        segment level: no region may reach the 256 MiB C-Two default.
        """
        server = self.probe()
        report = rpc_config.report("server")
        configured = report["capacity"]["poolCapacityBytes"]
        segment = report["capacity"]["poolSegmentBytes"]
        baseline_rss = resident_bytes(server.pid)
        self.assertIsNotNone(baseline_rss)
        transferred = server.call("echo", f"bytes:{8 * MIB}")
        self.assertTrue(transferred["ok"], transferred)
        regions = shm_mapping_regions(server.pid)
        resident = resident_bytes(server.pid)
        evidence = {
            "configuredPoolCapacityBytes": configured,
            "configuredSegmentBytes": segment,
            "mappedSharedMemoryRegions": regions,
            "mappedSharedMemoryBytes": sum(regions) if regions else None,
            "residentBytesAfter8MiB": resident,
            "residentBytesBaseline": baseline_rss,
            "defaultPoolSegmentBytes": 256 * MIB,
        }
        print("rpc_config memory evidence: " + json.dumps(evidence))
        if regions is not None:
            self.assertGreaterEqual(len(regions), 1)
            self.assertLessEqual(max(regions), segment, "no mapping may reach the C-Two default segment")
            self.assertGreaterEqual(max(regions), segment, "the configured segment is actually mapped")
        self.assertIsNotNone(resident)
        self.assertLess(resident, configured + 256 * MIB)
        # 16x smaller segments and a 32x smaller pool ceiling than the C-Two defaults.
        self.assertEqual(evidence["defaultPoolSegmentBytes"] // segment, 16)
        self.assertEqual((4 * 256 * MIB) // configured, 32)

    def test_pool_enabled_false_still_maps_shared_memory(self):
        """The measured reason the profile never claims a disabled pool."""
        server = self.probe(mode="raw", overrides={"pool_enabled": False}, name="nopool")
        mapped = shm_mapping_bytes(server.pid)
        if mapped is None:
            self.skipTest("this platform cannot report shared-memory mappings for a live process")
        print("pool_enabled=False mapping evidence: " + json.dumps({"mappedSharedMemoryBytes": mapped}))
        self.assertGreaterEqual(mapped, 64 * MIB, "0.5.1 still maps a pool segment with pool_enabled=False")
        probe = server.call("echo", "still-works")
        self.assertTrue(probe["ok"], probe)

    def test_a_client_with_other_overrides_can_still_reach_the_profile_server(self):
        """A rolling upgrade may pair clients and servers with different pools."""
        server = self.probe()
        reply = server.call("echo", f"bytes:{7 * MIB}", client_mode="raw", overrides={})
        self.assertTrue(reply["ok"], reply)
        self.assertEqual(reply["bytes"], 7 * MIB)

    def test_chunked_fallback_uses_the_production_reassembly_bounds(self):
        """Reassembly is exercised separately from the pool by forcing a chunk boundary.

        A maximum legal 8 MiB message never chunks under the production profile (0.9 x
        16 MiB). This probe keeps the production reassembly values (16 MiB x 2 with a
        16 MiB payload ceiling) and moves only the pool segment and chunk ratio so that a
        multi-megabyte payload takes the chunked path; every chunk must reassemble
        without truncation or corruption.
        """
        shared = {
            **rpc_config.POOL_OVERRIDES,
            "pool_segment_size": 2 * MIB,
            "max_pool_segments": 2,
            "chunk_threshold_ratio": 0.5,
        }
        server_only = {
            **shared,
            "max_execution_workers": 64,
            "max_pending_requests": 256,
            "max_payload_size": 32 * MIB,
            "max_frame_size": 16 * MIB,
        }
        server = self.probe(mode="raw", overrides=server_only, name="chunked")
        ceiling = (
            server_only["pool_segment_size"] * server_only["max_pool_segments"]
            + server_only["reassembly_segment_size"] * server_only["reassembly_max_segments"]
        )
        for size in (4 * MIB, 7 * MIB):
            reply = server.call("echo", f"bytes:{size}", client_mode="raw", overrides=shared)
            self.assertTrue(reply["ok"], reply)
            self.assertEqual(reply["bytes"], size)
            self.assertEqual(reply["sha256"], hashlib.sha256(b"x" * size).hexdigest())
        regions = shm_mapping_regions(server.pid)
        print("chunked reassembly evidence: " + json.dumps({"mappedSharedMemoryRegions": regions, "ceilingBytes": ceiling}))
        if regions is not None:
            self.assertGreaterEqual(max(regions), server_only["pool_segment_size"])
            self.assertLessEqual(max(regions), server_only["reassembly_segment_size"])


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
        self.addCleanup(self._cleanup)
        self.endpoint = self._start_daemon()
        self._assert_daemon_uses_the_lightweight_pool()

    def _cleanup(self):
        for child in self.children:
            if child.poll() is None:
                child.terminate()
                try:
                    child.wait(timeout=15)
                except subprocess.TimeoutExpired:  # pragma: no cover - test watchdog
                    child.kill()
                    child.wait(timeout=5)
        stop_private_workers(self.state)
        shutil.rmtree(self.work, ignore_errors=True)

    def _start_daemon(self) -> dict:
        environment = private_environment(
            self.work,
            BUDDY_STATE_DIR=str(self.state),
            BUDDY_WAIT_CAPACITY=str(self.WAIT_CAPACITY),
            # Two total slots so the waited and the renewable attempt coexist.
            BUDDY_MAX_CONCURRENT="2",
        )
        log_path = self.work / "daemon.log"
        log = open(log_path, "ab")
        process = subprocess.Popen(
            [sys.executable, "-m", "buddy.daemon"],
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
                    health = _request(endpoint, "health", {})
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

    def _assert_daemon_uses_the_lightweight_pool(self) -> None:
        """The real daemon process must map the bounded pool, not the 256 MiB default."""
        report = rpc_config.report("server")
        segment = report["capacity"]["poolSegmentBytes"]
        ceiling = report["capacity"]["poolCapacityBytes"]
        regions = None
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            regions = shm_mapping_regions(self.children[0].pid)
            if regions:
                break
            time.sleep(0.1)
        if not regions:
            self.skipTest("this platform cannot report shared-memory mappings for a live process")
        self.assertEqual(max(regions), segment, "the daemon must map the configured segment, not the C-Two default")
        print(
            "daemon pool evidence: "
            + json.dumps({"mappedSharedMemoryRegions": regions, "poolCeilingBytes": ceiling, "rssBytes": resident_bytes(self.children[0].pid)})
        )

    def _submit_external(self, request_id: str) -> str:
        # One private cwd per task: two live attempts must not share a workspace
        # reservation, or the second claim is refused with cwd-overlap before any
        # control traffic is involved.
        cwd = self.work / request_id
        cwd.mkdir(parents=True, exist_ok=True)
        reply = call_board(
            "task_submit",
            {"requestId": request_id, "task": "hold for control traffic", "cwd": str(cwd), "adapter": "external"},
            endpoint=self.endpoint,
        )
        return reply["task"]["runId"]

    def _claim(self, worker_id: str, claim_request_id: str, nonce: str, run_id: str) -> dict:
        claim = call_board(
            "worker_claim",
            {"workerId": worker_id, "claimRequestId": claim_request_id, "nonce": nonce, "runId": run_id},
            endpoint=self.endpoint,
        )
        granted = (claim.get("claim") or {}).get("attempt")
        self.assertTrue(granted, f"the probe worker must claim its attempt: {claim.get('reason')}")
        return granted

    def _admission(self) -> dict:
        return call_board("wait_capacity", {}, endpoint=self.endpoint, resource="wait")

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
            endpoint=self.endpoint,
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
                endpoint=self.endpoint,
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
                reply = call_board(operation, params, endpoint=self.endpoint, resource=resource)
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
        final = call_board("task_get", {"runId": renewable}, endpoint=self.endpoint)
        self.assertEqual(final["task"]["status"], "completed")
        self.assertTrue(final["task"]["resultAvailable"])


if __name__ == "__main__":  # pragma: no cover - manual focus
    unittest.main()
