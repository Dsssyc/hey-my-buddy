"""Shared launch, private material and C-Two client for one execution."""
from __future__ import annotations

import json
import hashlib
import os
import sys
import time
import uuid
from pathlib import Path

import c_two as cc

from ..private_files import publish_bytes
from ..errors import BoardError
from ..json_codec import canonical_json, decode_strict_json
from ..private_dirs import ensure_private_dir, attempt_root, open_regular_fd
from . import rpc_config
from .contracts import ExecutionLive
from .process_identity import (capture_process_identity, inspect_process_identity,
                               validate_process_identity, boot_id, _creation_time)
from .process_controller import launch_controller
from .run_identity import RunIdentity

STARTUP = "execution-start.json"
LIVE = "execution-live.json"
RECEIPT = "result-receipt.json"
BUDGET = "execution-budget.json"


def fsync_json(path: Path, value: dict, *, immutable: bool = False) -> None:
    """Serialize the execution envelope unchanged and publish complete bytes."""
    publish_bytes(path, json.dumps(value, ensure_ascii=False, allow_nan=False).encode("utf-8"), immutable=immutable)


def private_adapter(claim: dict) -> str:
    adapter = claim["task"]["spec"]["adapter"]
    return claim["decisionInput"]["buddy"]["harness"] if adapter == "decision" else adapter


def execution_directory(claim: dict, state_dir: Path) -> Path:
    return attempt_root(Path(state_dir), private_adapter(claim), claim["task"]["taskId"],
                        claim["attempt"]["attemptId"])


def launch_execution(claim: dict, state_dir: Path, nonce: str, *,
                     session_record_path: Path | str | None = None):
    """Persist intent and startup before spawning an owned new-session process."""
    state_dir = Path(state_dir)
    attempt = claim["attempt"]
    binding = {"taskId": claim["task"]["taskId"], "attemptId": attempt["attemptId"],
               "generation": attempt["generation"]}
    directory = execution_directory(claim, state_dir)
    evidence = state_dir / "attempts" / binding["taskId"] / binding["attemptId"]
    job_name = f"Local\\HeyMyBuddy-{uuid.uuid4()}" if os.name == "nt" else None
    handle = None
    try:
        ensure_private_dir(directory)
        ensure_private_dir(evidence)
        fsync_json(evidence / "spawn.intent", binding)
        startup = directory / STARTUP
        fsync_json(startup, {"claim": claim, "stateDir": str(state_dir), "nonce": nonce,
                             "jobName": job_name,
                             "sessionRecordPath": str(session_record_path) if session_record_path else None})
        environment = dict(os.environ)
        for key in ("HMB_AGENT_CREDENTIAL_FILE", "HMB_AGENT_CREDENTIAL_FD"):
            environment.pop(key, None)
        handle = launch_controller(
            prepare=lambda: ([sys.executable, "-m", "hey_my_buddy.buddy.runtime.execution",
                              "--startup", str(startup)], str(evidence), environment),
            log_paths={"stdout": str(evidence / "execution.stdout.log"),
                       "stderr": str(evidence / "execution.stderr.log")},
            **({"job_name": job_name} if job_name is not None else {}))
        handle.execution_directory = directory
        handle.execution_live_file = directory / LIVE
        handle.execution_binding = binding
        handle.budget_path = directory / BUDGET
        handle.role_run_identity = RunIdentity(task_id=binding["taskId"],
            attempt_id=binding["attemptId"], generation=binding["generation"],
            invocation_id="execution", turn_id=claim["turn"]["turnId"] if "turn" in claim else None)
        handle.process_identity = None
        handle.process_identity = capture_process_identity(handle)
        fsync_json(evidence / "spawn.marker", {**binding, "processIdentity": handle.process_identity})
        return handle
    except BaseException as error:
        surviving_handle = handle if handle is not None else getattr(error, "execution_handle", None)
        if surviving_handle is not None:
            surviving_handle.execution_directory = directory
            surviving_handle.execution_live_file = directory / LIVE
            surviving_handle.execution_binding = binding
            surviving_handle.budget_path = directory / BUDGET
            surviving_handle.role_run_identity = RunIdentity(task_id=binding["taskId"],
                attempt_id=binding["attemptId"], generation=binding["generation"],
                invocation_id="execution", turn_id=claim["turn"]["turnId"] if "turn" in claim else None)
            if not hasattr(surviving_handle, "process_identity"):
                surviving_handle.process_identity = None
        error.spawn_created = surviving_handle is not None or bool(
            getattr(getattr(error, "owned_popen_evidence", None), "created", False))
        error.execution_handle = surviving_handle
        raise


def self_execution_process_identity(job_name: str | None) -> dict:
    """Capture the execution's own complete identity without a service marker."""
    pid = os.getpid()
    started_at = _creation_time(pid)
    if started_at is None:
        raise BoardError("PROCESS_IDENTITY_UNKNOWN", "Execution creation time is unavailable")
    if os.name == "nt":
        record = {"bootId": boot_id(), "pid": pid, "startedAt": started_at,
                  "processGroupId": None, "jobName": job_name}
    else:
        if job_name is not None or os.getpgrp() != pid or os.getsid(pid) != pid:
            raise BoardError("PROCESS_IDENTITY_UNKNOWN", "Execution does not own its process group")
        record = {"bootId": boot_id(), "pid": pid, "startedAt": started_at,
                  "processGroupId": pid, "jobName": None}
    validate_process_identity(record)
    if inspect_process_identity(record) != "alive":
        raise BoardError("PROCESS_IDENTITY_UNKNOWN", "Execution process identity is unverified")
    return record


def _private_json(path: Path, maximum: int = 32768) -> dict:
    fd = open_regular_fd(path, os.O_RDONLY)
    with os.fdopen(fd, "rb") as stream:
        raw = stream.read(maximum + 1)
    if len(raw) > maximum:
        raise BoardError("INVALID_ARGUMENT", "Private execution material exceeds its bound")
    value = decode_strict_json(raw)
    if not isinstance(value, dict):
        raise BoardError("INVALID_ARGUMENT", "Private execution material must be an object")
    return value


def receipt_reference(directory: Path) -> dict:
    """Identify the durable receipt without putting its facts in an RPC frame."""
    path = Path(directory) / RECEIPT
    with os.fdopen(open_regular_fd(path, os.O_RDONLY), "rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    return {"path": str(path), "sha256": digest}


def read_execution_receipt(directory: Path, *, binding: dict | None = None,
                           sha256: str | None = None) -> dict | None:
    try:
        descriptor = open_regular_fd(Path(directory) / RECEIPT, os.O_RDONLY)
    except FileNotFoundError:
        return None
    with os.fdopen(descriptor, "rb") as stream:
        raw = stream.read()
    if sha256 is not None and hashlib.sha256(raw).hexdigest() != sha256:
        raise BoardError("RECEIPT_MISMATCH", "Execution receipt digest does not match")
    value = decode_strict_json(raw)
    if not isinstance(value, dict):
        raise BoardError("INVALID_ARGUMENT", "Execution receipt must be an object")
    if set(value) != {"attemptId", "generation", "taskId", "report", "createdAt"}:
        raise BoardError("INVALID_ARGUMENT", "Execution receipt has an invalid shape")
    if (not isinstance(value["taskId"], str) or not value["taskId"]
            or not isinstance(value["attemptId"], str) or not value["attemptId"]
            or type(value["generation"]) is not int or value["generation"] <= 0
            or not isinstance(value["report"], dict)
            or not isinstance(value["createdAt"], str) or not value["createdAt"]):
        raise BoardError("INVALID_ARGUMENT", "Execution receipt has invalid fields")
    if binding is not None and any(value[key] != binding[key] for key in
                                   ("taskId", "attemptId", "generation")):
        raise BoardError("INVALID_ARGUMENT", "Execution receipt binding differs")
    return value


class ExecutionLiveClient:
    """One authenticated connection per bounded operation; no liveness inference."""

    def __init__(self, identity: RunIdentity, *, address: str, name: str,
                 instance_id: str, token: str, state_dir: Path):
        self.identity = identity
        self.address = address
        self.name = name
        self.instance_id = instance_id
        self.token = token
        self.state_dir = Path(state_dir)
        self._closed = False

    def _frame(self, **values) -> str:
        return canonical_json({"identity": self.identity.to_payload(),
                               "instanceId": self.instance_id, "token": self.token, **values})

    def _call(self, method: str, timeout: float, **values) -> dict:
        if self._closed:
            raise BoardError("LIVE_UNAVAILABLE", "The execution channel is closed")
        rpc_config.configure_client(self.state_dir)
        try:
            with cc.connect(ExecutionLive, name=self.name, address=self.address, timeout=timeout) as peer:
                bounded = cc.with_call_options(peer, timeout=timeout)
                raw = getattr(bounded, method)(self._frame(**values))
        except Exception as error:
            details = dict(getattr(error, "details", {}))
            for source in (error.__cause__, error):
                if source is None:
                    continue
                for field in ("io_kind", "raw_os_error", "io_operation", "retryable", "transport_phase"):
                    value = getattr(source, field, None)
                    if value is not None:
                        details[field] = value
            raise BoardError("LIVE_UNAVAILABLE", "The execution endpoint is unavailable",
                             **{**details, "causeType": type(error).__name__, "cause": str(error)}) from error
        try:
            response = decode_strict_json(raw)
        except (TypeError, ValueError, RecursionError) as error:
            raise BoardError("LIVE_UNAVAILABLE", "The execution endpoint returned invalid JSON") from error
        if not isinstance(response, dict):
            raise BoardError("LIVE_UNAVAILABLE", "The execution endpoint returned an invalid frame")
        return response

    def wait(self) -> bool:
        if self._call("wait", 5.0) != {"alive": True}:
            raise BoardError("LIVE_UNAVAILABLE", "The execution wait reply is invalid")
        return True

    def stop(self, initiator: str = "unknown", grace_deadline: float | None = None,
             force: bool = False) -> dict:
        response = self._call("stop", 1.0, initiator=initiator,
                              graceDeadline=grace_deadline, force=force)
        if response != {"accepted": True}:
            raise BoardError("LIVE_UNAVAILABLE", "The execution stop reply is invalid")
        return response

    def request(self, request, *, timeout_ms: int) -> dict:
        request_payload = request.to_payload() if hasattr(request, "to_payload") else request
        return self._call("send", timeout_ms / 1000 + 1,
                          request=request_payload, timeoutMs=timeout_ms,
                          deadlineMonotonic=time.monotonic() + timeout_ms / 1000)

    def observe(self, *, after_seq=None, limit=None, timeout_ms=3000,
                fields=None, inquiry_id=None) -> dict:
        return self._call("observe", timeout_ms / 1000 + 1,
                          afterSeq=after_seq, limit=limit, fields=fields, inquiryId=inquiry_id)

    def capabilities(self) -> dict:
        return self._call("capabilities", 5.0)

    def close(self, reason: str = "service-detached") -> None:
        self._closed = True


def read_execution_endpoint(directory: Path, identity: RunIdentity, *, state_dir: Path,
                            process_identity: dict | None = None) -> ExecutionLiveClient | None:
    """Read a private descriptor and bind it to the expected attempt identity."""
    try:
        value = _private_json(Path(directory) / LIVE)
    except FileNotFoundError:
        return None
    if set(value) != {"identity", "processIdentity", "descriptor", "token"}:
        raise BoardError("LIVE_UNAVAILABLE", "Execution endpoint material has an invalid shape")
    actual = RunIdentity.from_payload(value["identity"])
    if actual != identity:
        raise BoardError("LIVE_UNAVAILABLE", "Execution endpoint identity does not match")
    observed_process_identity = validate_process_identity(value["processIdentity"])
    if process_identity is not None and observed_process_identity != process_identity:
        raise BoardError("LIVE_UNAVAILABLE", "Execution endpoint process identity differs")
    descriptor = value["descriptor"]
    if not isinstance(descriptor, dict) or set(descriptor) != {
            "address", "name", "instanceId", "hostPid", "endpointCredential"}:
        raise BoardError("LIVE_UNAVAILABLE", "Execution endpoint descriptor is invalid")
    if (not isinstance(descriptor["address"], str) or not descriptor["address"]
            or len(descriptor["address"]) > 256 or not isinstance(descriptor["name"], str)
            or not descriptor["name"] or len(descriptor["name"]) > 128
            or not isinstance(descriptor["instanceId"], str)
            or len(descriptor["instanceId"]) != 64
            or any(c not in "0123456789abcdef" for c in descriptor["instanceId"])):
        raise BoardError("LIVE_UNAVAILABLE", "Execution endpoint descriptor is invalid")
    if type(descriptor["hostPid"]) is not int or descriptor["hostPid"] != observed_process_identity["pid"]:
        raise BoardError("LIVE_UNAVAILABLE", "Execution endpoint host PID differs")
    credential_json = descriptor["endpointCredential"]
    if credential_json is None:
        if os.name != "nt":
            raise BoardError("LIVE_UNAVAILABLE", "Execution endpoint credential is absent")
    else:
        credential = cc.EndpointCredential.from_json(credential_json)
        trusted = cc.local_endpoint_context()
        if credential.address != descriptor["address"] or (
                credential.context.platform, credential.context.namespace_id, credential.context.root) != (
                trusted.platform, trusted.namespace_id, trusted.root):
            raise BoardError("LIVE_UNAVAILABLE", "Execution endpoint credential differs")
    token = value["token"]
    if not isinstance(token, str) or len(token) != 64 or any(c not in "0123456789abcdef" for c in token):
        raise BoardError("LIVE_UNAVAILABLE", "Execution endpoint token is invalid")
    return ExecutionLiveClient(identity, address=descriptor["address"], name=descriptor["name"],
                               instance_id=descriptor["instanceId"], token=token, state_dir=state_dir)


def cleanup_execution_endpoint(directory: Path, process_identity: dict, *, state_dir: Path,
                               stopped_evidence: str) -> dict:
    """Reap an endpoint after the caller has verified the execution is gone."""
    expected = validate_process_identity(process_identity)
    if stopped_evidence not in ("boot-changed", "identity-absent", "creator-reaped"):
        return {"status": "unverified", "reason": "execution-still-present-or-unknown"}
    path = Path(directory) / LIVE
    try:
        value = _private_json(path)
    except FileNotFoundError:
        return {"status": "already-absent", "reason": None}
    except (BoardError, OSError, ValueError, RecursionError):
        return {"status": "unverified", "reason": "endpoint-material-invalid"}
    if set(value) != {"identity", "processIdentity", "descriptor", "token"}:
        return {"status": "unverified", "reason": "endpoint-material-invalid"}
    try:
        actual = validate_process_identity(value["processIdentity"])
    except (BoardError, KeyError):
        return {"status": "unverified", "reason": "endpoint-material-invalid"}
    if actual != expected:
        return {"status": "unverified", "reason": "process-identity-mismatch"}
    descriptor = value["descriptor"]
    if not isinstance(descriptor, dict) or descriptor.get("hostPid") != expected["pid"]:
        return {"status": "unverified", "reason": "endpoint-host-mismatch"}
    rpc_config.configure_server(Path(state_dir))
    rpc_config.configure_client(Path(state_dir))
    credential_json = descriptor.get("endpointCredential")
    if credential_json is None:
        result = {"status": "not-applicable" if os.name == "nt" else "unverified",
                  "reason": "endpoint-credential-unavailable"}
    else:
        try:
            credential = cc.EndpointCredential.from_json(credential_json)
        except (ValueError, TypeError):
            return {"status": "unverified", "reason": "endpoint-credential-invalid"}
        context = cc.local_endpoint_context()
        if credential.address != descriptor.get("address") or (
                credential.context.platform, credential.context.namespace_id, credential.context.root) != (
                context.platform, context.namespace_id, context.root):
            return {"status": "unverified", "reason": "endpoint-domain-mismatch"}
        result = cc.reap_endpoint(descriptor["address"], credential, context=context)
    if result["status"] in ("reaped", "already-absent", "not-applicable"):
        path.unlink(missing_ok=True)
    return result
