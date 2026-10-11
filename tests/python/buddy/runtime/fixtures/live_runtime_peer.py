"""Private real Worker/controller peers; no model, harness or daily state.

The Worker retains the actual Popen objects it spawned. The controller owner
commits a real append-only, fsynced fake journal before settling an inquiry.
All test material is retained; normal C-Two stop owns its socket lifecycle.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import threading
import time
from unittest.mock import patch

from hey_my_buddy.buddy.harnesses import c_two_live as ctl
from hey_my_buddy.buddy.harnesses.live import (
    InquiryState, LiveCapabilities, LiveJournal, LiveReply, LiveSnapshot,
)
from hey_my_buddy.buddy.runtime.live import WorkerLiveRuntime
from hey_my_buddy.json_codec import canonical_json
from hey_my_buddy.protocol.contracts import HarnessRunLive
from hey_my_buddy.protocol.run_identity import RunIdentity
from hey_my_buddy.protocol.worker_live import WorkerLiveActor, WorkerLiveAttach, WorkerLiveDetach


def emit(value):
    sys.stdout.write(canonical_json(value) + "\n")
    sys.stdout.flush()


def exchange(process, command):
    process.stdin.write(canonical_json(command) + "\n")
    process.stdin.flush()
    line = process.stdout.readline()
    if not line:
        raise RuntimeError("private peer closed its command pipe")
    result = json.loads(line)
    if not result.get("ok"):
        raise RuntimeError("private peer command failed")
    return result


class SlowEndpoint(ctl.CTwoLiveEndpoint):
    def request(self, request_json):
        time.sleep(2)
        return canonical_json(LiveReply(status="unavailable", reason_code="fixture-stall").to_payload())


def controller():
    endpoint = None
    finished = threading.Event()
    commit_allowed = threading.Event()
    owner = None
    consumed = threading.Event()
    try:
        for line in sys.stdin:
            command = json.loads(line)
            op = command["op"]
            if op == "start":
                identity = RunIdentity.from_payload(command["identity"])
                token = secrets.token_hex(32)
                endpoint_type = SlowEndpoint if command.get("slow") else ctl.CTwoLiveEndpoint
                endpoint = endpoint_type(identity, LiveCapabilities(inquiry_delivery="cooperative-checkpoint"),
                                         HarnessRunLive, token=token, name="Ada",
                                         state_dir=Path(os.environ["BUDDY_STATE_DIR"]))
                descriptor = endpoint.start()
                journal = Path(command["journal"])
                if not command.get("paused"):
                    commit_allowed.set()

                def native_owner():
                    entries = 0
                    while not finished.is_set():
                        request = endpoint.consume_request(0.05)
                        if request is None:
                            continue
                        consumed.set()
                        while not commit_allowed.wait(0.05):
                            if finished.is_set():
                                return
                        # This write+fsync is the fake native queue commit, not a DTO assertion.
                        fd = os.open(journal, os.O_CREAT | os.O_APPEND | os.O_WRONLY, 0o600)
                        try:
                            record = canonical_json(request.to_payload()).encode() + b"\n"
                            os.write(fd, record)
                            os.fsync(fd)
                        finally:
                            os.close(fd)
                        entries += 1
                        endpoint.publish_journal(LiveJournal(available=True, entries=entries))
                        endpoint.publish_inquiry_state(InquiryState(
                            question_id=request.payload.question_id, status="queued"))
                        endpoint.settle_request(request.request_id,
                                                LiveReply(status="queued", observed=True, state="queued"))

                owner = threading.Thread(target=native_owner, name="fake-native-owner")
                owner.start()
                emit({"ok": True, "descriptor": descriptor.to_payload(), "token": token})
            elif op == "consumed":
                emit({"ok": True, "consumed": consumed.is_set()})
            elif op == "commit":
                commit_allowed.set()
                emit({"ok": True})
            elif op == "journal":
                records = [json.loads(s) for s in journal.read_text().splitlines()] if journal.exists() else []
                emit({"ok": True, "records": records})
            elif op == "publish":
                endpoint.publish_snapshot(LiveSnapshot.from_payload(command["snapshot"]))
                emit({"ok": True})
            elif op == "stop":
                break
            else:
                raise RuntimeError("unknown controller fixture operation")
    finally:
        finished.set()
        commit_allowed.set()
        if owner is not None:
            owner.join(2)
        if endpoint is not None:
            endpoint.stop()
    emit({"ok": True})


class FakeBoardClient:
    def __init__(self):
        self.attachments = []
        self.registry = {}
        self.detach_count = 0

    def live_attach(self, attachment):
        # The real strict model round trip reads every Actor and endpoint field.
        validated = WorkerLiveAttach.from_payload(attachment.to_payload())
        self.attachments.append(validated)
        self.registry[validated.identity] = validated
        return {"attached": True}

    def live_detach(self, withdrawal):
        validated = WorkerLiveDetach.from_payload(withdrawal.to_payload())
        current = self.registry.get(validated.identity)
        if current is not None and current.instance_id == validated.instance_id \
                and all(getattr(current, field) == getattr(validated, field)
                        for field in WorkerLiveActor.model_fields):
            self.registry.pop(validated.identity)
        self.detach_count += 1
        return {"detached": True}


def worker():
    runtime = None
    handles = {}
    channels = {}
    descriptors = {}
    client = FakeBoardClient()
    try:
        for line in sys.stdin:
            command = json.loads(line)
            op = command["op"]
            if op == "start":
                def resolve(handle):
                    return "bound", channels[handle]

                with patch("hey_my_buddy.buddy.runtime.live.random_person_name", return_value="Ada"):
                    runtime = WorkerLiveRuntime(client, command["workerId"], command["workerInstance"], resolve, state_dir=Path(os.environ["BUDDY_STATE_DIR"]))
                descriptor = runtime.start()
                emit({"ok": True, "descriptor": descriptor.to_payload()})
            elif op == "spawn":
                handle = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "controller"],
                                          stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                          stderr=sys.stderr, text=True)
                handles[command["label"]] = handle
                handle.role_run_identity = RunIdentity.from_payload(command["identity"])
                result = exchange(handle, {"op": "start", "identity": command["identity"],
                                           "journal": command["journal"], "slow": command.get("slow", False),
                                           "paused": command.get("paused", False)})
                descriptor = ctl.LiveEndpointDescriptor.from_payload(result["descriptor"])
                channels[handle] = ctl.CTwoLiveChannel(
                    handle.role_run_identity, HarnessRunLive, name=descriptor.name,
                    address=descriptor.address, instance_id=descriptor.instance_id, token=result["token"], state_dir=Path(os.environ["BUDDY_STATE_DIR"]))
                descriptors[command["label"]] = descriptor
                emit({"ok": True, "descriptor": descriptor.to_payload()})
            elif op in ("bind", "refresh"):
                handle = handles[command["label"]]
                method = runtime.bind if op == "bind" else runtime.refresh
                result = method(command["claim"], handle, command["nonce"])
                emit({"ok": True, "bound": result,
                      "attachment": client.attachments[-1].to_payload() if result else None,
                      "attachCount": len(client.attachments)})
            elif op == "unbind":
                result = runtime.unbind(command["claim"])
                emit({"ok": True, "unbound": result, "registrationCount": len(client.registry)})
            elif op == "controller":
                handle = handles[command["label"]]
                result = exchange(handle, command["command"])
                if command["command"]["op"] == "stop":
                    handle.wait(timeout=5)
                emit(result)
            elif op == "held":
                handle = handles[command["label"]]
                emit({"ok": True, "held": handle in channels, "returnCode": handle.poll()})
            elif op == "stop":
                break
            else:
                raise RuntimeError("unknown Worker fixture operation")
    finally:
        # Native/process stop belongs to this private fixture, never the runtime.
        for handle in handles.values():
            if handle.poll() is None:
                exchange(handle, {"op": "stop"})
                handle.wait(timeout=5)
            handle.stdin.close()
            handle.stdout.close()
        if runtime is not None:
            runtime.stop()
            runtime.stop()
    emit({"ok": True, "registrationCount": len(client.registry), "detachCount": client.detach_count})


if __name__ == "__main__":
    os.environ["C2_RELAY_ANCHOR_ADDRESS"] = ""
    os.environ["C2_ENV_FILE"] = ""
    if sys.argv[1] == "controller":
        controller()
    else:
        worker()
