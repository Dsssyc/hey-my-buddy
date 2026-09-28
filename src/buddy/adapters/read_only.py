"""Generic structured calls using native harness permissions, without a workflow turn."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid

from .base import AdapterOutcome, ExecutionContext, ProcessHandle, ReadOnlyStructuredRequest, open_logs
from .turn_io import private_json

_TYPES = {"object": dict, "array": list, "string": str, "null": type(None)}


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


def start(name: str, context: ExecutionContext, request: ReadOnlyStructuredRequest) -> ProcessHandle:
    context.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    control = {
        "directory": str(context.directory), "nativeRoot": str(context.directory / "native"),
        "cwd": request.cwd, "timeoutSeconds": request.budget["timeoutSeconds"],
        "taskId": context.task_id, "attemptId": context.attempt_id, "generation": context.generation,
        "sessionId": str(uuid.uuid4()), "access": "read",
        "activityFile": str(context.directory / "activity.json"),
        "spec": {key: context.spec[key] for key in ("provider", "model", "effort")},
        "readOnlyRequest": {"prompt": request.prompt, "outputSchema": request.output_schema,
                            "budget": request.budget},
    }
    path = context.directory / "readonly-control.json"
    private_json(path, control)
    environment = {key: value for key, value in context.environment.items()
                   if not key.startswith(("BUDDY_AGENT_", "BUDDY_WORKER_"))}
    stdout, stderr = open_logs(context.log_paths())
    try:
        process = subprocess.Popen([sys.executable, "-m", f"buddy.adapters.{name}_runner", "--control", str(path)],
                                   cwd=request.cwd, env=environment, stdin=subprocess.DEVNULL,
                                   stdout=stdout, stderr=stderr, start_new_session=True, close_fds=True)
    finally:
        os.close(stdout)
        os.close(stderr)
    handle = ProcessHandle(process, own_group=True, log_paths=context.log_paths())
    handle.deadline = time.monotonic() + request.budget["timeoutSeconds"] + 10
    return handle


def collect(handle: ProcessHandle) -> AdapterOutcome:
    payload = None
    try:
        path = Path(handle.log_paths["stdout"])
        if path.stat().st_size <= 256 * 1024:
            payload = json.loads(path.read_text())
    except (OSError, ValueError, RecursionError):
        pass
    if not isinstance(payload, dict):
        payload = {"status": "error", "code": "invalid-native-result"}
    stopped = (payload.get("processState", {}).get("shutdownConfirmed") is True
               and handle.shutdown_confirmed())
    status = "ok" if payload.get("status") == "ok" and handle.process.returncode == 0 and stopped else "failed"
    if stopped and payload.get("status") == "cancelled":
        status = "cancelled"
    return AdapterOutcome(status=status, result=payload, error=payload.get("code") if status != "ok" else None,
                          exit_code=handle.process.returncode, shutdown_confirmed=stopped)
