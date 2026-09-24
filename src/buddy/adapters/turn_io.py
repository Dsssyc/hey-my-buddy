"""Private governed-turn files and workspaces shared by coding harnesses."""
from __future__ import annotations

import hashlib
import json
import os
import uuid
from pathlib import Path
from typing import Callable

from ..errors import BoardError
from .base import ExecutionContext

MAX_INPUT_BYTES = 262144
MAX_OUTCOME_BYTES = 65536
MAX_RECORD_BYTES = 98304


def canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def input_hash(value: dict) -> str:
    return hashlib.sha256(canonical_json(value).encode()).hexdigest()


def private_json(path: Path, value: dict, *, exclusive: bool = False) -> None:
    """Write a bounded private artifact; immutable receipts must not overwrite."""
    raw = canonical_json(value).encode()
    target = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp") if exclusive else path
    flags = os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW | (os.O_EXCL if exclusive else os.O_TRUNC)
    fd = os.open(target, flags, 0o600)
    try:
        with os.fdopen(fd, "wb") as stream:
            os.fchmod(stream.fileno(), 0o600)
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        if exclusive:
            os.link(target, path)
        parent = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(parent)
        finally:
            os.close(parent)
    finally:
        if exclusive:
            target.unlink(missing_ok=True)


def prepare_turn(context: ExecutionContext) -> None:
    context.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(context.directory, 0o700)
    context.task_file().write_text(context.spec["task"])
    os.chmod(context.task_file(), 0o600)
    write_turn_files(context)
    verify_workspace(context)


def write_turn_files(context: ExecutionContext) -> None:
    if context.turn_input is None:
        return
    if len(canonical_json(context.turn_input).encode()) > MAX_INPUT_BYTES:
        raise BoardError("INVALID_ARGUMENT", "the governed turn input exceeds its byte bound")
    private_json(context.turn_input_file(), context.turn_input)
    if context.agent_credential:
        private_json(context.credential_file(), {
            "token": context.agent_credential, "taskId": context.task_id,
            "attemptId": context.attempt_id, "generation": context.generation, "turnId": context.turn_id,
        })
        context.environment["BUDDY_AGENT_CREDENTIAL_FILE"] = str(context.credential_file())
        context.environment["BUDDY_TASK_ID"] = context.task_id
        context.environment["BUDDY_ATTEMPT_ID"] = context.attempt_id


def workspace_cwd(context: ExecutionContext) -> str:
    manifest = getattr(context, "effective_workspace", None)
    return str(manifest["path"]) if isinstance(manifest, dict) and manifest.get("path") else context.cwd


def verify_workspace(context: ExecutionContext) -> None:
    manifest = (context.turn_input or {}).get("executionWorkspace")
    if not isinstance(manifest, dict) or not manifest:
        context.effective_workspace = None
        return
    from ..workflow import workspace_module
    workspace_module().verify(manifest, require_unchanged=manifest.get("access") == "read")
    context.effective_workspace = manifest


def validate_outcome(outcome: object) -> str | None:
    fields = {"disposition", "summary", "remaining", "decisions", "artifacts", "request"}
    if not isinstance(outcome, dict) or set(outcome) != fields:
        return "the outcome must contain exactly the current outcome fields"
    def text(value: object, limit: int = 8000) -> bool:
        return isinstance(value, str) and bool(value.strip()) and "\0" not in value and len(value.encode()) <= limit
    def strings(value: object) -> bool:
        return isinstance(value, list) and len(value) <= 32 and all(text(x, 4096) for x in value)
    if outcome["disposition"] not in ("completed", "assistance", "attention") or not text(outcome["summary"]):
        return "the turn outcome requires a valid disposition and nonblank summary"
    if not strings(outcome["remaining"]) or not strings(outcome["decisions"]):
        return "remaining and decisions must be bounded string arrays"
    artifacts = outcome["artifacts"]
    if not isinstance(artifacts, list) or len(artifacts) > 32:
        return "artifacts must be a bounded array"
    try:
        if any(not (text(x, 4096) or isinstance(x, dict) and bool(x)) or len(canonical_json(x).encode()) > 4096 for x in artifacts):
            return "artifacts must contain bounded nonempty references"
        if len(canonical_json(outcome).encode()) > MAX_OUTCOME_BYTES:
            return "the outcome exceeds its byte bound"
    except (TypeError, ValueError, RecursionError):
        return "the outcome must contain finite JSON values"
    request = outcome["request"]
    if outcome["disposition"] == "completed":
        return None if request is None else "a completed outcome requires request: null"
    required = {"summary", "attempted", "neededWork", "expectedArtifacts", "acceptance"}
    if not isinstance(request, dict) or not required <= set(request) or set(request) - required - {"suggestedProfileId"}:
        return "an assistance or attention outcome requires the current request fields"
    if any(not text(request[k]) for k in ("summary", "attempted", "neededWork", "acceptance")):
        return "the turn request requires nonblank summary, attempted, neededWork and acceptance"
    if not strings(request["expectedArtifacts"]) or ("suggestedProfileId" in request and not text(request["suggestedProfileId"], 256)):
        return "the turn request references are invalid"
    return None


def read_turn(context: ExecutionContext, shutdown_confirmed: bool, exit_code: int | None,
              validate_provenance: Callable[[dict], str | None] | None = None) -> tuple[dict | None, str | None]:
    turn_input = context.turn_input
    if turn_input is None:
        return None, None
    if exit_code != 0:
        return None, "the runner did not exit zero"
    if not shutdown_confirmed:
        return None, "runner shutdown is not confirmed, so no turn outcome is imported"
    try:
        with context.turn_output_file().open("rb") as stream:
            raw = stream.read(MAX_RECORD_BYTES + 1)
        if len(raw) > MAX_RECORD_BYTES:
            return None, "the turn record exceeds its byte bound"
        record = json.loads(raw)
    except (OSError, ValueError):
        return None, "the governed runner wrote no valid JSON turn output"
    if not isinstance(record, dict) or record.get("version") != 1:
        return None, "the turn output version is not supported"
    expected = {"taskId": context.task_id, "attemptId": context.attempt_id,
                "generation": context.generation, "turnId": context.turn_id,
                "resumeMode": turn_input.get("resumeMode"), "previousSessionId": turn_input.get("previousSessionId")}
    for key, value in expected.items():
        if record.get(key) != value:
            return None, f"the turn record {key} does not match the service-owned execution identity"
    if record.get("inputSha256") != input_hash(turn_input):
        return None, "the turn record inputSha256 does not match the turn input the service wrote"
    error = validate_outcome(record.get("outcome"))
    if error:
        return None, error
    if not isinstance(record.get("sessionId"), str) or not record["sessionId"]:
        return None, "the turn record carries no session identity"
    if validate_provenance is None:
        from . import adapter
        validate_provenance = adapter(context.spec["adapter"]).validate_turn_provenance
    error = validate_provenance(record)
    return (None, error) if error else (record, None)


def seal_workspace(context: ExecutionContext) -> tuple[dict | None, str | None]:
    manifest = getattr(context, "effective_workspace", None) or (context.turn_input or {}).get("executionWorkspace")
    if not isinstance(manifest, dict) or not manifest:
        return None, None
    state_dir = context.environment.get("BUDDY_STATE_DIR")
    if not state_dir:
        return None, "the attempt has no private state directory to seal into"
    try:
        from ..workflow import workspace_module
        seal = workspace_module().seal(Path(state_dir), manifest, context.task_id, context.attempt_id)
    except BoardError as error:
        return None, f"{error.code}: {error.message}"
    if not isinstance(seal, dict) or not seal.get("manifestSha256"):
        return None, "the workspace seal returned no immutable manifest"
    return seal, None
