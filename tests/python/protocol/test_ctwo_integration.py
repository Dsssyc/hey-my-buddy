"""Model-free, real private service/Worker/controller C-Two integration.

Run this file in its own Python process, as the check runner normally does.
Every process belongs to this fixture and has a saved Popen; no default board
attachment, PID discovery, manual deletion or model invocation is permitted.
Raw facts remain below BUDDY_CHECKS_TMPDIR for independent Host acceptance.
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

from support import _child_environment, write_catalog_fixture
from buddy.runtime.test_worker_live_wiring import PEER
from hey_my_buddy.buddy.harnesses.c_two_live import (
    LiveEndpointDescriptor, LiveWireObserve, LiveWireQuery,
)
from hey_my_buddy.buddy.harnesses.run_contract import RunIdentity
from hey_my_buddy.json_codec import canonical_json
from hey_my_buddy.protocol import rpc_config
from hey_my_buddy.protocol.client import BoardClient
from hey_my_buddy.protocol.contracts import HarnessRunLive
from hey_my_buddy.protocol.transport import _read_endpoint, _request
from protocol.fixtures.ctwo_controller import CONFIGURATION, SELF, exchange


@unittest.skipUnless(os.name == "posix", "private IPC inspection is POSIX")
class CTwoIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="ctwo-integration-",
            dir=os.environ.get("BUDDY_CHECKS_TMPDIR", "/tmp"))).resolve()
        self.state = self.root / "state"
        self.state.mkdir(mode=0o700)
        (self.root / "tmp").mkdir(mode=0o700)
        self.processes = []
        self.logs = []
        self.service = self.worker = self.foreign = None
        self.service_endpoint = None
        self.descriptors = {}
        self.env = _child_environment(self.state, {
            "TMPDIR": str(self.root / "tmp"), "BUDDY_CHECKS_TMPDIR": str(self.root / "tmp"),
            "BUDDY_MODEL_CATALOG_FILE": str(write_catalog_fixture(self.state)),
            "BUDDY_MAX_CONCURRENT": "1", "BUDDY_CONSOLE_PORT": "0",
            "PYTHONPATH": os.pathsep.join((str(SELF.parents[2]), str(SELF.parents[4] / "src"))),
            "PYTHONDONTWRITEBYTECODE": "1", "C2_RELAY_ANCHOR_ADDRESS": "", "C2_ENV_FILE": "",
        })
        # Select only this private state in the test process before SDK I/O.
        self.enterContext(patch.dict(os.environ, self.env, clear=True))
        self.addCleanup(self.stop_owned)

    def record(self, name, value):
        (self.root / name).write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")

    def spawn(self, argv, label, *, pipes=False, cwd=None):
        log = (self.root / f"{label}.stderr.log").open("x")
        self.logs.append(log)
        process = subprocess.Popen(argv, env=self.env, cwd=cwd,
            stdin=subprocess.PIPE if pipes else subprocess.DEVNULL,
            stdout=subprocess.PIPE if pipes else log, stderr=log,
            text=True, start_new_session=True)
        self.processes.append(process)
        return process

    def until(self, predicate, message, timeout=10):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            result = predicate()
            if result:
                return result
            time.sleep(0.02)
        self.fail(message)

    def start_service(self):
        self.service = self.spawn([sys.executable, str(SELF), "service", "--root", str(self.root)], "service")

        def ready():
            self.assertIsNone(self.service.poll(), "private service exited; inspect retained stderr")
            return _read_endpoint(self.state)

        self.service_endpoint = self.until(ready, "private service never published its endpoint")
        rpc_config.configure_client(self.state, create=False)
        self.client = BoardClient(self.state, autostart=False)
        self.assertEqual(self.client.ping()["serviceId"], self.service_endpoint["serviceId"])
        self.descriptors["service"] = self.service_endpoint["address"]

    def start_worker(self):
        work = self.root / "work"
        work.mkdir(mode=0o700)
        for number, arguments in enumerate((("init", "-q"),
                ("-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
                 "commit", "--allow-empty", "-qm", "private integration input"))):
            process = self.spawn(["git", *arguments], f"git-{number}", cwd=work)
            self.assertEqual(process.wait(timeout=5), 0)
        submitted = self.client.call("workflow_submit", {**CONFIGURATION,
            "requestId": "ctwo-integration", "hostId": "integration-host",
            "task": "model-free live transport fixture", "cwd": str(work), "timeoutSeconds": 30,
            "executionWorkspace": {"kind": "existing", "access": "write"}})
        self.run_id = submitted["runId"]
        self.worker = self.spawn([sys.executable, str(SELF), "worker", "--root", str(self.root)],
                                 "worker", pipes=True)

        def attached():
            self.assertIsNone(self.worker.poll(), "own Worker fixture exited")
            value = exchange(self.worker, {"op": "status"})
            self.worker_descriptor = LiveEndpointDescriptor.from_payload(value["worker"])
            self.descriptors["worker"] = self.worker_descriptor.address
            self.assertEqual(value["errors"], [])
            self.assertTrue(value["running"] or value["bound"],
                            "own Worker round ended before its controller attached")
            return value if value["bound"] and value["controller"] else None

        value = self.until(attached, "actual Worker renewal tick did not attach its live controller")
        self.controller = LiveEndpointDescriptor.from_payload(value["controller"])
        self.worker_descriptor = LiveEndpointDescriptor.from_payload(value["worker"])
        self.descriptors.update(worker=self.worker_descriptor.address, controller=self.controller.address)
        return value

    def start_foreign(self):
        self.foreign = self.spawn([sys.executable, str(PEER), "controller"], "foreign", pipes=True)
        identity = RunIdentity(task_id="foreign-task", attempt_id="foreign-attempt", generation=1,
            invocation_id="foreign-invocation", turn_id="foreign-turn", input_sha256="f" * 64)
        value = exchange(self.foreign, {"op": "start", "identity": identity.to_payload(),
                                      "journal": str(self.root / "foreign-journal.jsonl")})
        self.foreign_descriptor = LiveEndpointDescriptor.from_payload(value["descriptor"])
        self.descriptors["foreign"] = self.foreign_descriptor.address

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
        self.assertEqual(len(set(self.descriptors.values())), 4)
        self.assertEqual(stat.S_IMODE((self.state / "ipc").stat().st_mode), 0o700)

    def assert_same_connection_survives_expiry(self):
        # The ready descriptor/request are role outputs, not invented wire data.
        from hey_my_buddy.buddy.harnesses.run_contract import decode_run_request
        requests = list(self.state.glob("harnesses/dsh/goals/*/attempts/*/role-run-request.json"))
        self.assertEqual(len(requests), 1)
        request = decode_run_request(requests[0].read_bytes())
        controls = json.loads(requests[0].with_name("role-run-control.json").read_text())
        query = dict(identity=request.identity, instance_id=self.controller.instance_id,
                     token=controls["live"]["token"])
        slow_frame = LiveWireObserve(**query, after_seq=999, limit=1)
        with cc.connect(HarnessRunLive, name=self.controller.name, address=self.controller.address,
                        timeout=1) as peer:
            with self.assertRaises(CallDeadlineExceeded):
                cc.with_call_options(peer, timeout=0.03).observe(canonical_json(slow_frame.to_payload()))
            # A client deadline is not stop evidence. Reuse precisely this peer.
            capabilities = json.loads(cc.with_call_options(peer, timeout=1).capabilities(
                canonical_json(LiveWireQuery(**query).to_payload())))
            self.assertEqual(capabilities["inquiryDelivery"], "cooperative-checkpoint")
        self.assertIsNone(self.worker.poll())
        self.assertTrue(exchange(self.worker, {"op": "status"})["running"])
        self.assertEqual(self.endpoint_status(self.controller.address)["status"], "present")
        self.record("same-connection-deadline.json", {
            "sdkDeadlineRaised": True, "samePeerFollowupSucceeded": True, "peerStillPresent": True})

    def exercise(self, *, kill):
        self.start_service()
        self.start_worker()
        self.start_foreign()
        self.assert_private_endpoints()
        if not kill:
            self.assert_same_connection_survives_expiry()
        observation = self.client.call("inquiry_observe", {"runId": self.run_id,
            "inquiryId": "through-service", "question": "Commit this private inquiry.", "timeoutMs": 2000})
        self.record("service-inquiry.json", observation)
        self.assertTrue(observation["bridge"]["enabled"])
        self.assertTrue(observation["bridge"]["observed"], "service -> Worker -> controller must return a commit")
        self.assertEqual(observation["inquiry"]["state"], "queued")
        self.assertEqual(observation["journal"]["entries"], 1)
        journals = list(self.state.glob("harnesses/dsh/goals/*/attempts/*/native-journal.jsonl"))
        self.assertEqual(len(journals), 1)
        committed = json.loads(journals[0].read_text())
        self.assertEqual(committed["requestId"], "through-service")
        self.assertEqual(committed["identity"]["taskId"], self.run_id)
        exchange(self.worker, {"op": "kill-controller" if kill else "finish"})

        def finished():
            value = exchange(self.worker, {"op": "status"})
            return value if not value["running"] else None

        value = self.until(finished, "own Worker did not finish its single main-loop round")
        self.record("worker-final.json", value)
        self.assertEqual(value["errors"], [])
        self.assertEqual(len(value["reports"]), 1)
        self.assertTrue(value["reports"][0]["shutdownConfirmed"])
        self.assertTrue(value["reports"][0]["result"]["processState"]["shutdownConfirmed"])
        self.assertIsNotNone(value["cleanup"], "owning Worker must record its endpoint cleanup")
        self.assertEqual(value["cleanup"]["outcome"], "reaped" if kill else "already-absent")
        self.assertEqual(self.endpoint_status(self.controller.address)["status"], "absent")
        self.assertEqual(self.endpoint_status(self.worker_descriptor.address)["status"], "absent")
        self.assertIsNone(self.foreign.poll(), "foreign owned peer must survive the holder's reclamation")
        self.assertEqual(self.endpoint_status(self.foreign_descriptor.address)["status"], "present")
        task = self.client.get(runId=self.run_id)
        self.assertTrue(task["shutdownConfirmed"], "two-layer fact must reach authoritative board")
        self.record("task-final.json", task)

    def test_normal_stop_and_same_connection_after_deadline(self):
        self.exercise(kill=False)

    def test_holder_reaps_killed_controller_and_preserves_foreign_peer(self):
        self.exercise(kill=True)

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

        def stop_peer(process, label, timeout):
            if process is None:
                return
            if process.poll() is None:
                attempt(label + " cooperative request", lambda: exchange(process, {"op": "stop"}, timeout=timeout))
            # EOF is cooperative too, including after a failed reply exchange.
            if process.stdin is not None and not process.stdin.closed:
                attempt(label + " input EOF", process.stdin.close)
            attempt(label + " saved Popen reap", lambda: process.wait(timeout=timeout))

        def request_service_stop():
            endpoint = self.service_endpoint or _read_endpoint(self.state)
            self.assertIsNotNone(endpoint, "service start failed; retain root for Host")
            stopped = _request(endpoint, "service_control", {
                "action": "stop", "drainSeconds": 1, "reason": "owned integration fixture"}, state_dir=self.state)
            self.assertTrue(stopped["stopped"])

        try:
            stop_peer(self.worker, "Worker", 20)
            stop_peer(self.foreign, "foreign peer", 10)
            if self.service is not None:
                if self.service.poll() is None:
                    attempt("service cooperative request", request_service_stop)
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
