"""Owned, model-free processes for the service -> Worker -> controller proof.

The simulated native child commits the journal before the controller publishes
facts. The actual WorkerRunExecutor stages the role control/request; only native
launch and collection are injected. No framing, process discovery or PID adoption.
All material is retained. Cooperative teardown addresses saved Popen objects.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import selectors
import subprocess
import sys
import threading
import time
from types import SimpleNamespace
from unittest.mock import patch

import c_two as cc

from hey_my_buddy.buddy.harnesses.base import AdapterOutcome, ProcessHandle
from hey_my_buddy.buddy.harnesses.c_two_live import CTwoLiveEndpoint, write_ready_material
from hey_my_buddy.buddy.harnesses.live import (
    InquiryState, LiveCapabilities, LiveJournal, LiveObservation, LiveReply,
)
from hey_my_buddy.buddy.harnesses.run_contract import encode_run_request
from hey_my_buddy.buddy.roles import controller as role_seam, run_execution
from hey_my_buddy.buddy.roles.run_execution import WorkerRunExecutor
from hey_my_buddy.buddy.runtime.worker import Worker
from hey_my_buddy.json_codec import canonical_json
from hey_my_buddy.protocol.client import BoardClient
from hey_my_buddy.protocol.contracts import HarnessRunLive
from support import enable_fixture_configuration


CONFIGURATION = dict(adapter="dsh", provider="deepseek-official",
                     model="deepseek-flash", effort="off")
SELF = Path(__file__).resolve()


def emit(value):
    print(canonical_json(value), flush=True)


def exchange(process, value, *, timeout=5):
    """One bounded fixture-pipe exchange; this is not the SDK transport.

    Check the saved child while waiting, including when another inherited pipe
    writer keeps stdout open after that child fails. Nonblocking writes and
    reads share one deadline; partial replies cannot hang in TextIO.readline.
    """
    if process.poll() is not None:
        raise RuntimeError(f"owned fixture exited with code {process.returncode}")
    deadline = time.monotonic() + timeout
    source, target = process.stdout.fileno(), process.stdin.fileno()
    modes = {fd: os.get_blocking(fd) for fd in (source, target)}
    payload = (canonical_json(value) + "\n").encode()
    reply = bytearray()
    try:
        for fd in modes:
            os.set_blocking(fd, False)
        with selectors.DefaultSelector() as selector:
            selector.register(target, selectors.EVENT_WRITE)
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("owned fixture pipe exchange deadline expired")
                events = selector.select(min(remaining, 0.05))
                if not events and process.poll() is not None:
                    raise RuntimeError(f"owned fixture exited with code {process.returncode}")
                for key, _mask in events:
                    if key.fd == target:
                        try:
                            count = os.write(target, payload)
                        except BlockingIOError:
                            continue
                        payload = payload[count:]
                        if not payload:
                            selector.unregister(target)
                            selector.register(source, selectors.EVENT_READ)
                    else:
                        try:
                            chunk = os.read(source, 4096)
                        except BlockingIOError:
                            continue
                        if not chunk:
                            raise RuntimeError("owned fixture closed its reply pipe")
                        reply.extend(chunk)
                        if len(reply) > 1024 * 1024:
                            raise RuntimeError("owned fixture reply exceeds its bound")
                        if b"\n" in reply:
                            line, _separator, extra = reply.partition(b"\n")
                            if extra.strip():
                                raise RuntimeError("owned fixture sent an unsolicited reply")
                            return json.loads(line)
    finally:
        for fd, blocking in modes.items():
            os.set_blocking(fd, blocking)


def write_fact(path, value):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as stream:
        json.dump(value, stream, sort_keys=True)
        stream.flush()
        os.fsync(stream.fileno())


def native(root):
    """A real subprocess, with EOF as its cooperative stop boundary."""
    entries = 0
    try:
        for line in sys.stdin:
            request = json.loads(line)
            with (root / "native-journal.jsonl").open("a") as stream:
                stream.write(canonical_json(request) + "\n")
                stream.flush()
                os.fsync(stream.fileno())
            entries += 1
            emit({"committed": True, "entries": entries})
    finally:
        write_fact(root / "native-stopped.json", {"pid": os.getpid(), "eofObserved": True})


class OneSlowCallEndpoint(CTwoLiveEndpoint):
    """Exercise native SDK per-call expiry without a second transport."""

    def observe(self, request_json):
        if json.loads(request_json).get("afterSeq") == 999:
            time.sleep(0.2)
        return super().observe(request_json)


def controller(control_path: Path, state_dir: Path):
    from hey_my_buddy.buddy.harnesses.registry import run_seam

    control = json.loads(control_path.read_text())
    root = Path(control["privateRoot"])
    request, _services, _observer, error = run_execution.worker_request(control, run_seam("dsh"))
    if error is not None:
        raise error
    write_fact(control["requestFile"], json.loads(encode_run_request(request)))
    endpoint = OneSlowCallEndpoint(request.identity,
        LiveCapabilities(inquiry_delivery="cooperative-checkpoint"), HarnessRunLive,
        token=control["live"]["token"], instance_id=control["live"]["instanceId"],
        state_dir=state_dir)
    process = subprocess.Popen([sys.executable, str(SELF), "native", "--root", str(root)],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    owned = ProcessHandle(process, own_group=False, log_paths={})
    try:
        descriptor = endpoint.start()
        endpoint.publish_observation(LiveObservation(ready=True, session_id="simulated-native",
            agent_status="active", supported=True, delivery_mode="cooperative-checkpoint"))
        write_ready_material(control["live"]["readyFile"], descriptor)
        while not (root / "finish.request").exists():
            incoming = endpoint.consume_request(0.02)
            if incoming is None:
                continue
            committed = exchange(process, incoming.to_payload())
            if not committed["committed"]:
                raise AssertionError("native commit missing")
            endpoint.publish_journal(LiveJournal(available=True, entries=committed["entries"]))
            endpoint.publish_inquiry_state(InquiryState(
                question_id=incoming.payload.question_id, status="queued"))
            endpoint.settle_request(incoming.request_id,
                LiveReply(status="queued", observed=True, state="queued"))
    finally:
        # Closing only this child's input also works if the controller is killed:
        # OS pipe closure lets the native child record its actual EOF and exit.
        try:
            process.stdin.close()
            try:
                process.wait(timeout=5)
                write_fact(root / "native-collected.json", {
                    "exitCode": process.returncode, "shutdownConfirmed": owned.shutdown_confirmed()})
            finally:
                process.stdout.close()
        finally:
            endpoint.stop()


class SimulatedExecutor(WorkerRunExecutor):
    def __init__(self):
        super().__init__(SimpleNamespace(name="dsh", native_resume=False,
            available=lambda: (True, None)), SimpleNamespace(check_preparation=lambda *a: None))
        self.handles = []
        self.reports = []

    def start(self, context):
        launcher = run_execution.launch_controller

        def simulated_launch(*, prepare, **options):
            def prepared():
                argv, cwd, environment = prepare()
                control = argv[argv.index("--control") + 1]
                return [sys.executable, str(SELF), "controller", "--control", control], cwd, environment
            return launcher(prepare=prepared, **options)

        with patch.object(run_execution, "launch_controller", simulated_launch):
            handle = super().start(context)
        self.handles.append(handle)
        return handle

    def collect(self, handle, context):
        # This is explicitly a failed simulated harness run, never fake delivery.
        # Positive native evidence and real group observation gate the receipt.
        root = Path(handle.role_run_control["privateRoot"])
        deadline = time.monotonic() + 3
        while not (root / "native-stopped.json").exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        native_stop = json.loads((root / "native-stopped.json").read_text())
        stopped = native_stop["eofObserved"] is True and handle.shutdown_confirmed() is True
        report = AdapterOutcome(status="failed", result={"modelStarted": False,
            "simulatedNativeStop": native_stop,
            "processState": {"shutdownConfirmed": stopped}}, error="model-free fixture",
            exit_code=handle.process.poll(), shutdown_confirmed=stopped)
        self.reports.append(report.to_report())
        return report


class RecordingBoardClient(BoardClient):
    def __init__(self, state):
        super().__init__(state, autostart=False)
        self.attachments = []

    def live_attach(self, attachment):
        result = super().live_attach(attachment)
        if result.get("attached"):
            self.attachments.append(attachment)
        return result


def worker(root):
    client = RecordingBoardClient(root / "state")
    executor = SimulatedExecutor()
    instance = Worker("integration-worker", root / "state", client=client,
                      adapters=("dsh",), log=lambda text: print(text, file=sys.stderr, flush=True))
    descriptor = instance.live.start()
    errors = []

    def run():
        try:
            instance.run(max_iterations=1)
        except BaseException as error:
            errors.append(repr(error))

    thread = threading.Thread(target=run)
    with patch.object(role_seam, "worker_executor", lambda name: executor):
        thread.start()
        try:
            for line in sys.stdin:
                command = json.loads(line)
                if command["op"] == "status":
                    handle = executor.handles[0] if executor.handles else None
                    cleanup = getattr(handle, "role_endpoint_cleanup", None)
                    emit({"ok": True, "worker": descriptor.to_payload(),
                        "controller": (getattr(handle, "role_live_descriptor", None).to_payload()
                            if getattr(handle, "role_live_descriptor", None) else None),
                        "bound": bool(client.attachments), "running": thread.is_alive(),
                        "reports": executor.reports, "errors": errors,
                        "cleanup": cleanup.to_payload() if cleanup else None})
                elif command["op"] in ("finish", "kill-controller"):
                    handle = executor.handles[0]
                    if command["op"] == "kill-controller":
                        handle.process.kill()  # This saved Popen, never a discovered PID.
                    else:
                        Path(handle.role_run_control["privateRoot"], "finish.request").touch(mode=0o600)
                    emit({"ok": True})
                elif command["op"] == "stop":
                    break
                else:
                    raise ValueError("unknown private fixture operation")
        finally:
            try:
                try:
                    for handle in executor.handles:
                        Path(handle.role_run_control["privateRoot"], "finish.request").touch(mode=0o600, exist_ok=True)
                finally:
                    instance.stop.set()
                    thread.join(15)
                    if thread.is_alive():
                        raise AssertionError("own Worker stop unconfirmed; retain task root")
            finally:
                instance.live.stop()
    emit({"ok": True, "stopped": True})


def service(root):
    from hey_my_buddy.blackboard.service.daemon import Daemon
    from hey_my_buddy.blackboard.tasks.workflow import WorkflowCoordinator
    from hey_my_buddy.buddy.harnesses.dsh.adapter import DshAdapter

    daemon = Daemon(root / "state")
    original = daemon.service

    def private_service():
        service = original()
        service.automatic_discovery = False
        enable_fixture_configuration(daemon.store, CONFIGURATION)
        # Discovery is synthetic; all service, task, lease and live RPCs are real.
        record = {"adapter": "dsh", "available": True, "status": "ready", "revision": 0,
                  "command": [sys.executable], "version": "model-free-fixture"}
        with daemon.store.db.write() as db:
            db.execute("INSERT INTO harness_health(adapter,status,record_json) VALUES('dsh','ready',?) "
                       "ON CONFLICT(adapter) DO UPDATE SET status='ready',record_json=excluded.record_json",
                       (canonical_json(record),))
        service.harnesses.refresh = lambda name, **kwargs: service.harnesses.get(name)
        return service

    description = DshAdapter()
    description.available = lambda: (True, None)
    # A single explicitly owned Worker replaces the fixture's automatic pool.
    with patch.object(daemon, "service", private_service), \
         patch.object(daemon, "_start_pool", lambda: None), \
         patch.object(daemon, "_reconcile_pool", lambda: None), \
         patch.object(WorkflowCoordinator, "_execution_adapter", staticmethod(lambda name: description)):
        daemon.run()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("service", "worker", "controller", "native"))
    parser.add_argument("--root", type=Path)
    parser.add_argument("--control", type=Path)
    args = parser.parse_args()
    if args.mode == "controller":
        state_dir = os.environ.get("BUDDY_STATE_DIR")
        if not state_dir:
            parser.error("BUDDY_STATE_DIR is required for the controller process")
        controller(args.control, Path(state_dir))
    else:
        globals()[args.mode](args.root)


if __name__ == "__main__":
    main()
