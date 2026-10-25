"""Model-free, real private service/execution C-Two integration.

Run this file in its own Python process, as the check runner normally does.
Every process belongs to this fixture and has a saved Popen; no default board
attachment, PID discovery, manual deletion or model invocation is permitted.
Raw facts remain below HMB_CHECKS_TMPDIR for independent Host acceptance.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

import c_two as cc
from c_two.error import CallDeadlineExceeded

from support import _child_environment, request_private_service_stop, write_catalog_fixture
from hey_my_buddy.buddy.runtime.execution import LiveEndpointDescriptor
from hey_my_buddy.json_codec import canonical_json
from hey_my_buddy.protocol import rpc_config
from hey_my_buddy.protocol.client import BoardClient
from hey_my_buddy.protocol.contracts import ExecutionLive
from hey_my_buddy.protocol.transport import _read_endpoint, _request
from protocol.fixtures.ctwo_controller import BUDDY, SELF, exchange, join_exchanges


@unittest.skipUnless(os.name == "posix", "private IPC inspection is POSIX")
class CTwoIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="ctwo-integration-",
            dir=os.environ.get("HMB_CHECKS_TMPDIR", "/tmp"))).resolve()
        self.state = self.root / "state"
        self.state.mkdir(mode=0o700)
        (self.root / "tmp").mkdir(mode=0o700)
        self.processes = []
        self.logs = []
        self.service = None
        self.service_endpoint = None
        self.descriptors = {}
        self.env = _child_environment(self.state, {
            "TMPDIR": str(self.root / "tmp"), "HMB_CHECKS_TMPDIR": str(self.root / "tmp"),
            "HMB_MODEL_CATALOG_FILE": str(write_catalog_fixture(self.state)),
            "HMB_MAX_CONCURRENT": "1", "HMB_CONSOLE_PORT": "0",
            "PYTHONPATH": os.pathsep.join((str(SELF.parents[2]), str(SELF.parents[4] / "src"))),
            "PYTHONDONTWRITEBYTECODE": "1", "C2_RELAY_ANCHOR_ADDRESS": "", "C2_ENV_FILE": "",
        })
        # Select only this private state in the test process before SDK I/O.
        self.enterContext(patch.dict(os.environ, self.env, clear=True))
        self.addCleanup(self.stop_owned)

    def record(self, name, value):
        (self.root / name).write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")

    def spawn(self, argv, label, *, pipes=False, cwd=None):
        log = (self.root / f"{label}.stderr.log").open("x", encoding="utf-8", newline="\n")
        self.logs.append(log)
        process = subprocess.Popen(argv, env=self.env, cwd=cwd,
            stdin=subprocess.PIPE if pipes else subprocess.DEVNULL,
            stdout=subprocess.PIPE if pipes else log, stderr=log,
            text=True, start_new_session=True, encoding="utf-8")
        self.processes.append(process)
        return process

    def until(self, predicate, message, timeout=10):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            result = predicate()
            if result:
                return result
            time.sleep(0.02)
        logs = {str(path.relative_to(self.root)): path.read_text(errors='replace', encoding="utf-8")[-6000:]
                for path in self.root.rglob('*.stderr.log')}
        self.fail(message + '\n' + json.dumps(logs, ensure_ascii=False))

    def start_service(self):
        self.service = self.spawn([sys.executable, str(SELF), "service", "--root", str(self.root)],
                                  "service", pipes=True)

        def ready():
            self.assertIsNone(self.service.poll(), "private service exited; inspect retained stderr")
            return _read_endpoint(self.state)

        self.service_endpoint = self.until(ready, "private service never published its endpoint")
        rpc_config.configure_client(self.state, create=False)
        self.client = BoardClient(self.state, autostart=False)
        self.assertEqual(self.client.ping()["serviceId"], self.service_endpoint["serviceId"])
        self.descriptors["service"] = self.service_endpoint["address"]

    def start_execution(self):
        work = self.root / "work"
        work.mkdir(mode=0o700)
        for number, arguments in enumerate((("init", "-q"),
                ("-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
                 "commit", "--allow-empty", "-qm", "private integration input"))):
            process = self.spawn(["git", *arguments], f"git-{number}", cwd=work)
            self.assertEqual(process.wait(timeout=5), 0)
        submitted = self.client.call("workflow_submit", {**BUDDY,
            "requestId": "ctwo-integration", "hostId": "integration-host",
            "task": "model-free live transport fixture", "cwd": str(work), "expectedSeconds": 30,
            "executionWorkspace": {"kind": "existing", "access": "write"}})
        self.run_id = submitted["runId"]
        def attached():
            self.assertIsNone(self.service.poll(), "private service exited")
            value = exchange(self.service, {"op": "status"})
            self.assertEqual(value["errors"], [])
            return value if value["running"] and value["execution"] and value["bound"] and value["nativeReady"] else None

        value = self.until(attached, "service did not start its live execution")
        self.execution_descriptor = LiveEndpointDescriptor.from_payload(value["execution"])
        self.assertEqual(self.execution_descriptor.host_pid, value["executionPid"])
        self.descriptors["execution"] = self.execution_descriptor.address
        return value

    def endpoint_status(self, address):
        return cc.inspect_endpoint(address, context=cc.local_endpoint_context(root=str(self.state / "ipc")))

    def assert_private_endpoints(self):
        for label, address in self.descriptors.items():
            inspected = self.endpoint_status(address)
            self.assertEqual(inspected["status"], "present", label)
            credential = inspected["credential"]
            self.assertIsNotNone(credential, label)
            self.assertEqual(Path(credential.context.root), self.state / "ipc", label)
            self.assertEqual(credential.address, address, label)
        self.assertEqual(len(set(self.descriptors.values())), 2)
        self.assertEqual(stat.S_IMODE((self.state / "ipc").stat().st_mode), 0o700)

    def assert_same_connection_survives_expiry(self):
        # The live descriptor is published by the real execution process.
        materials = list(self.state.glob("harnesses/dsh/micro-tasks/*/attempts/*/execution-live.json"))
        self.assertEqual(len(materials), 1)
        material = json.loads(materials[0].read_text(encoding="utf-8"))
        query = dict(identity=material["identity"], instanceId=self.execution_descriptor.instance_id,
                     token=material["token"])
        slow_frame = {**query, "afterSeq": 999, "limit": 1, "fields": None, "inquiryId": None}
        with cc.connect(ExecutionLive, name=self.execution_descriptor.name, address=self.execution_descriptor.address,
                        timeout=1) as peer:
            with self.assertRaises(CallDeadlineExceeded):
                cc.with_call_options(peer, timeout=0.03).observe(canonical_json(slow_frame))
            # A client deadline is not stop evidence. Reuse precisely this peer.
            capabilities = json.loads(cc.with_call_options(peer, timeout=1).capabilities(
                canonical_json(query)))
            self.assertEqual(capabilities["inquiryDelivery"], "cooperative-checkpoint")
        self.assertTrue(exchange(self.service, {"op": "status"})["running"])
        self.assertEqual(self.endpoint_status(self.execution_descriptor.address)["status"], "present")
        self.record("same-connection-deadline.json", {
            "sdkDeadlineRaised": True, "samePeerFollowupSucceeded": True, "peerStillPresent": True})

    def exercise(self, *, kill):
        self.start_service()
        self.start_execution()
        self.assert_private_endpoints()
        if not kill:
            self.assert_same_connection_survives_expiry()
        observation = self.client.call("inquiry_observe", {"runId": self.run_id,
            "inquiryId": "through-service", "question": "Commit this private inquiry.", "timeoutMs": 2000})
        self.record("service-inquiry.json", observation)
        self.assertTrue(observation["bridge"]["enabled"])
        self.assertTrue(observation["bridge"]["observed"], "service -> execution must return a commit")
        self.assertEqual(observation["inquiry"]["state"], "queued")
        self.assertEqual(observation["journal"]["entries"], 1)
        journals = list(self.state.glob("harnesses/dsh/micro-tasks/*/attempts/*/run-*/native-journal.jsonl"))
        self.assertEqual(len(journals), 1)
        committed = json.loads(journals[0].read_text(encoding="utf-8"))
        self.assertEqual(committed["requestId"], "through-service")
        self.assertEqual(committed["identity"]["taskId"], self.run_id)
        exchange(self.service, {"op": "kill-execution" if kill else "finish"})

        def finished():
            value = exchange(self.service, {"op": "status"})
            task = self.client.get(runId=self.run_id)
            return value if not value["running"] and task["state"] in {
                "completed", "failed", "cancelled"} else None

        value = self.until(finished, "service did not settle its execution")
        self.record("execution-final.json", value)
        self.assertEqual(value["errors"], [])
        if not kill:
            self.assertEqual(len(value["reports"]), 1)
            self.assertTrue(value["reports"][0]["shutdownConfirmed"])
        native_stop = json.loads(journals[0].with_name("native-stopped.json").read_text(encoding="utf-8"))
        self.assertTrue(native_stop["eofObserved"], "owned native child observes execution pipe EOF")
        self.assertEqual(self.endpoint_status(self.execution_descriptor.address)["status"], "absent")
        task = self.client.get(runId=self.run_id)
        # The committed native stop journal proves shutdown after either exit.
        self.record("task-final.json", task)
        self.assertTrue(task["shutdownConfirmed"])

    def test_normal_stop_and_same_connection_after_deadline(self):
        self.exercise(kill=False)

    def test_service_reaps_killed_execution(self):
        self.exercise(kill=True)

    def test_parent_identity_capture_failure_is_closed_by_execution_self_report(self):
        self.env["HMB_C2_PARENT_CAPTURE_FAILURE"] = "1"
        self.exercise(kill=False)

    def stop_owned(self):
        # Finally only asks objects created here to stop. No PID scan or signals.
        failures = []

        def attempt(label, action):
            try:
                return action()
            except Exception as error:
                error.add_note(f"owned integration cleanup: {label}")
                failures.append(error)
                return None

        def request_service_stop():
            endpoint = self.service_endpoint or _read_endpoint(self.state)
            self.assertIsNotNone(endpoint, "service start failed; retain root for Host")
            stopped = request_private_service_stop(self.state,
                lambda operation, params: _request(endpoint, operation, params, state_dir=self.state),
                drainSeconds=1, note="owned integration fixture")
            self.record("service-stop.json", stopped)

        try:
            if self.service is not None:
                if self.service.poll() is None:
                    attempt("service cooperative request", request_service_stop)
                    if self.service.stdin is not None and not self.service.stdin.closed:
                        attempt("service input EOF", self.service.stdin.close)
                attempt("service saved Popen reap", lambda: self.service.wait(timeout=10))
            statuses = {}
            for label, address in self.descriptors.items():
                inspected = attempt(label + " endpoint inspection", lambda: self.endpoint_status(address))
                if inspected is not None:
                    statuses[label] = inspected["status"]
            attempt("endpoint record", lambda: self.record("endpoint-final.json", statuses))
            attempt("endpoint absence", lambda: self.assertTrue(
                all(status == "absent" for status in statuses.values()), statuses))

            def check_sockets():
                sockets = [str(path.relative_to(self.root)) for path in (self.state / "ipc").rglob("*")
                           if stat.S_ISSOCK(path.lstat().st_mode)]
                self.assertEqual(sockets, [], "no endpoint socket remains; coordination directories may remain")

            attempt("private socket inventory", check_sockets)
        finally:
            # Assertions, inspection failures and failed peers must never bypass
            # native SDK shutdown and freeze the next case's private root.
            shutdown = attempt("native SDK shutdown", cc.shutdown)
            if shutdown is not None:
                attempt("native SDK stop evidence", lambda: self.record("sdk-shutdown.json", shutdown))
                attempt("native SDK stop confirmation", lambda: self.assertTrue(shutdown["completed"], shutdown))
            for process in self.processes:
                attempt(f"saved Popen {process.pid} pipe threads join", lambda: join_exchanges(process))
                for name in ("stdin", "stdout", "stderr"):
                    pipe = getattr(process, name)
                    if pipe is not None and not pipe.closed:
                        attempt(f"saved Popen {process.pid} {name} close", pipe.close)
            for log in self.logs:
                attempt("own log close", log.close)
        attempt("cleanup failure record", lambda: self.record("cleanup-failures.json", {
            "errors": [f"{type(error).__name__}: {error}" for error in failures],
            "processes": [{"pid": process.pid, "exitCode": process.poll()} for process in self.processes]}))
        if failures:
            raise ExceptionGroup("Owned integration cleanup failed; material retained", failures)


if __name__ == "__main__":
    unittest.main()
