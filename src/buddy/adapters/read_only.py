"""Generic structured calls using native harness permissions, without a workflow turn."""
from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import time
import uuid

from .base import AdapterOutcome, ExecutionContext, NoToolStructuredRequest, ProcessHandle, ReadOnlyStructuredRequest, open_logs
from ..errors import BoardError
from ..private_dirs import context_root, ensure_private_dir
from .windows_process import owned_popen
from .turn_io import private_json, canonical_json, guard_private_path

_TYPES = {"object": dict, "array": list, "string": str, "null": type(None)}


@dataclass(frozen=True)
class _NoToolEvidence:
    adapter: str
    task_id: str
    attempt_id: str
    generation: int
    directory: Path
    private_root: Path
    evidence_root: Path
    request_json: str


def schema_errors(value, schema: dict) -> list[str]:
    """Keywords violated by ``value``, for the JSON Schema subset Router answers use.

    Supported: type, enum, required, properties, additionalProperties=false,
    items, minLength, maxLength and maxItems. Any other keyword is refused so an
    unsupported schema can never pass silently.
    """
    supported = {"type", "enum", "required", "properties", "additionalProperties",
                 "items", "minLength", "maxLength", "maxItems"}
    unknown = set(schema) - supported
    if unknown or schema.get("additionalProperties", False) is not False:
        raise ValueError(f"unsupported schema keywords: {sorted(unknown) or ['additionalProperties']}")
    errors = []
    kinds = schema.get("type")
    if kinds is not None:
        kinds = [kinds] if isinstance(kinds, str) else list(kinds)
        if not any(isinstance(value, _TYPES[kind]) and not (kind != "null" and isinstance(value, bool)) for kind in kinds):
            return ["type"]
    if "enum" in schema and value not in schema["enum"]:
        errors.append("enum")
    if isinstance(value, str):
        if len(value) < schema.get("minLength", 0):
            errors.append("minLength")
        if "maxLength" in schema and len(value) > schema["maxLength"]:
            errors.append("maxLength")
    if isinstance(value, list):
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            errors.append("maxItems")
        for item in value:
            errors.extend(schema_errors(item, schema.get("items", {})))
    if isinstance(value, dict):
        properties = schema.get("properties", {})
        missing = [key for key in schema.get("required", []) if key not in value]
        errors.extend("required" for _ in missing)
        if "additionalProperties" in schema and set(value) - set(properties):
            errors.append("additionalProperties")
        for key, sub in properties.items():
            if key in value:
                errors.extend(schema_errors(value[key], sub))
    return errors


def _decode(raw):
    return json.loads(raw) if isinstance(raw, str) else raw


def valid_answer(raw, schema: dict) -> bool:
    try:
        return not schema_errors(_decode(raw), schema)
    except (ValueError, RecursionError):
        return False


def correction_code(raw, schema: dict) -> str | None:
    try:
        value = _decode(raw)
    except (ValueError, RecursionError):
        return "answer-invalid-json"
    errors = schema_errors(value, schema)
    # Enum violations include choices outside frozen candidate bounds. Never retry them.
    if "enum" in errors:
        return None
    return "answer-shape" if errors else None


def no_tool_prompt(prompt: str, schema: dict) -> str:
    """Python supplies the answer schema for harnesses without a native JSON option."""
    return prompt + "\n\nReturn only one JSON value matching this schema: " + canonical_json(schema)


def _account_environment(name: str, context: ExecutionContext, *, purpose: str) -> dict:
    """Consume the same service-frozen selection as the coding attempt."""
    account = context.runtime.get('account')
    if name == 'codex':
        from .codex_home import frozen_account
        account = frozen_account(context.runtime)
    if account is None:
        return context.environment
    if not isinstance(account, dict) or account.get('adapter') != name:
        raise BoardError('INVALID_ARGUMENT', 'Structured calls require the matching frozen account')
    state = context.environment.get('BUDDY_STATE_DIR')
    if not state:
        raise BoardError('INVALID_ARGUMENT', 'Account selection requires the private state directory')
    from ..accounts import execution_environment
    return execution_environment(Path(state), account, context.environment, purpose=purpose)


def start(name: str, context: ExecutionContext, request: ReadOnlyStructuredRequest) -> ProcessHandle:
    if name in ("dsh", "zcode"):
        raise BoardError("UNSUPPORTED_ADAPTER", "Review on the Worker carrier is not implemented", adapter=name)
    native_environment = _account_environment(name, context, purpose='review')
    ensure_private_dir(context.directory)
    control = {
        "directory": str(context.directory), "nativeRoot": str(ensure_private_dir(context_root(context, name) / "review-native")),
        "account": context.runtime.get('account'),
        "cwd": request.cwd, "timeoutSeconds": request.budget["timeoutSeconds"],
        "taskId": context.task_id, "attemptId": context.attempt_id, "generation": context.generation,
        "sessionId": str(uuid.uuid4()), "access": "read",
        "activityFile": str(context.directory / "activity.json"),
        "spec": {key: context.spec[key] for key in ("provider", "model", "effort")},
        "readOnlyRequest": {"prompt": request.prompt, "outputSchema": request.output_schema,
                            "budget": request.budget, "captureEvidence": request.capture_evidence},
    }
    path = context.directory / "readonly-control.json"
    private_json(path, control)
    from ..harness_runtime import controller_environment
    environment = controller_environment(context.directory, native_environment, read_only=True)
    for log_path in context.log_paths().values():
        guard_private_path(Path(log_path))
    stdout, stderr = open_logs(context.log_paths())
    try:
        process = owned_popen([sys.executable, "-m", f"buddy.adapters.{name}_runner", "--control", str(path)],
                                   cwd=request.cwd, env=environment, stdin=subprocess.DEVNULL,
                                   stdout=stdout, stderr=stderr, start_new_session=True, close_fds=True)
    finally:
        os.close(stdout)
        os.close(stderr)
    handle = ProcessHandle(process, own_group=True, log_paths=context.log_paths())
    handle.deadline = time.monotonic() + request.budget["timeoutSeconds"] + 10
    return handle


def start_no_tool(name: str, context: ExecutionContext, request: NoToolStructuredRequest) -> ProcessHandle:
    """Start a separate native controller without a workflow turn or agent authority."""
    native_environment = _account_environment(name, context, purpose='router')
    cwd = Path(request.cwd)
    if (context.turn is not None or context.agent_credential is not None
            or type(request.timeout_seconds) is not int or not 0 < request.timeout_seconds <= 60
            or not isinstance(request.prompt, str) or not request.prompt.strip()
            or not isinstance(request.output_schema, dict)):
        raise BoardError("INVALID_ARGUMENT", "no-tool call requires a bounded prompt, schema and no workflow authority")
    try:
        if not cwd.is_absolute() or not cwd.is_dir() or cwd.is_symlink() or any(cwd.iterdir()):
            raise ValueError("not an empty private directory")
        info = cwd.stat()
        if info.st_mode & 0o077 or (hasattr(os, "getuid") and info.st_uid != os.getuid()):
            raise ValueError("directory is not owner-private")
    except (OSError, ValueError):
        raise BoardError("INVALID_ARGUMENT", "no-tool cwd must be an existing empty private directory") from None
    directory = ensure_private_dir(context.directory)
    invocation_name = "no-tool-" + uuid.uuid4().hex
    invocation = ensure_private_dir(context_root(context, name) / invocation_name)
    evidence = directory / invocation_name
    control = {
        "directory": str(invocation), "nativeRoot": str(invocation / "native"), "evidenceRoot": str(evidence),
        "taskId": context.task_id, "attemptId": context.attempt_id, "generation": context.generation,
        "account": context.runtime.get('account'),
        "cwd": str(cwd.resolve()), "timeoutSeconds": request.timeout_seconds,
        "spec": {key: context.spec[key] for key in ("provider", "model", "effort")},
        "noToolRequest": {"prompt": request.prompt, "outputSchema": request.output_schema,
                          "captureEvidence": request.capture_evidence},
    }
    binding = _NoToolEvidence(name, context.task_id, context.attempt_id, context.generation,
                              directory, invocation, evidence, canonical_json(control["noToolRequest"]))
    path = context.directory / "no-tool-control.json"
    private_json(path, control)
    from ..harness_runtime import controller_environment
    environment = controller_environment(context.directory, native_environment, read_only=True)
    if name == "dsh" and native_environment.get("DSH_HOME"):
        environment["DSH_HOME"] = native_environment["DSH_HOME"]
    for log_path in context.log_paths().values():
        guard_private_path(Path(log_path))
    stdout, stderr = open_logs(context.log_paths())
    try:
        process = owned_popen([sys.executable, "-m", f"buddy.adapters.{name}_runner", "--control", str(path)],
                              cwd=str(cwd), env=environment, stdin=subprocess.DEVNULL,
                              stdout=stdout, stderr=stderr, start_new_session=True, close_fds=True)
    finally:
        os.close(stdout)
        os.close(stderr)
    handle = ProcessHandle(process, own_group=True, log_paths=context.log_paths())
    handle.deadline = time.monotonic() + request.timeout_seconds
    handle.no_tool = True
    handle.no_tool_control = path
    handle.no_tool_evidence = binding
    return handle


def _evidence_json(path: Path) -> object:
    path = guard_private_path(path)
    metadata = path.lstat()
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > 256 * 1024:
        raise BoardError("PRIVATE_PATH_UNSAFE", "Evidence must be bounded ordinary JSON", path=str(path))
    with path.open("rb") as stream:
        raw = stream.read(256 * 1024 + 1)
    if len(raw) > 256 * 1024:
        raise BoardError("PRIVATE_PATH_UNSAFE", "Evidence exceeds its byte bound", path=str(path))
    return json.loads(raw)


def _retain_no_tool_evidence(binding: _NoToolEvidence, payload: dict) -> None:
    observed_calls = 0
    # These paths and the request are frozen at start, never reconstructed from
    # the controller's mutable on-disk configuration after it has run.
    for number in (1, 2):
        source = guard_private_path(binding.private_root / f"call-{number}")
        if not source.exists():
            continue
        if not source.is_dir():
            raise BoardError("PRIVATE_PATH_UNSAFE", "No-tool call source is not a directory", path=str(source))
        pair = [guard_private_path(source / name) for name in ("request.json", "result.json")]
        if not all(path.exists() for path in pair):
            continue
        values = [_evidence_json(path) for path in pair]
        target = ensure_private_dir(binding.evidence_root / f"call-{number}")
        for source_file, value in zip(pair, values):
            private_json(target / source_file.name, value)
        observed_calls += 1
    if not observed_calls:
        evidence = ensure_private_dir(binding.evidence_root / "call-1")
        private_json(evidence / "request.json", json.loads(binding.request_json))
        private_json(evidence / "result.json", payload)


def collect(handle: ProcessHandle) -> AdapterOutcome:
    payload = None
    try:
        payload = _evidence_json(Path(handle.log_paths["stdout"]))
    except (OSError, ValueError, BoardError, RecursionError):
        pass
    if not isinstance(payload, dict):
        payload = {"status": "error", "code": "invalid-native-result"}
    stopped = (payload.get("processState", {}).get("shutdownConfirmed") is True
               and handle.shutdown_confirmed() is True)
    status = "ok" if payload.get("status") == "ok" and handle.process.returncode == 0 and stopped else "failed"
    if stopped and payload.get("status") == "cancelled":
        status = "cancelled"
    if stopped and getattr(handle, "no_tool", False) is True:
        binding = getattr(handle, "no_tool_evidence", None)
        try:
            if not isinstance(binding, _NoToolEvidence):
                raise BoardError("PRIVATE_PATH_UNSAFE", "The no-tool handle has no frozen evidence binding")
            _retain_no_tool_evidence(binding, payload)
        except (OSError, ValueError, TypeError, BoardError, RecursionError) as error:
            diagnostic = {"status": "failed", "reason": str(error),
                          "nativeStatus": payload.get("status"), "nativeCode": payload.get("code")}
            if isinstance(binding, _NoToolEvidence):
                diagnostic.update(privateRoot=str(binding.private_root), evidenceRoot=str(binding.evidence_root),
                                  identity={"adapter": binding.adapter, "taskId": binding.task_id,
                                            "attemptId": binding.attempt_id, "generation": binding.generation,
                                            "directory": str(binding.directory)})
            if isinstance(error, BoardError):
                diagnostic.update(reasonCode=error.code, **error.details)
            handle.evidence_retention_failure = diagnostic
            payload = {**payload, "status": "error", "code": "evidence-retention-failed", "evidenceRetention": diagnostic}
            status = "failed"
    return AdapterOutcome(status=status, result=payload, error=payload.get("code") if status != "ok" else None,
                          exit_code=handle.process.returncode, shutdown_confirmed=stopped)
