"""Generic structured calls using native harness permissions, without a workflow turn."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid

from jsonschema import Draft202012Validator

from .base import AdapterOutcome, ExecutionContext, ProcessHandle, ReadOnlyStructuredRequest, open_logs
from .turn_io import private_json


def valid_answer(raw, schema: dict) -> bool:
    try:
        value = json.loads(raw) if isinstance(raw, str) else raw
        return Draft202012Validator(schema).is_valid(value)
    except (ValueError, RecursionError):
        return False


def correction_code(raw, schema: dict) -> str | None:
    try:
        value = json.loads(raw) if isinstance(raw, str) else raw
    except (ValueError, RecursionError):
        return "answer-invalid-json"
    errors = list(Draft202012Validator(schema).iter_errors(value))
    # Enum violations include choices outside frozen candidate bounds. Never retry them.
    if any(error.validator == 'enum' for error in errors):
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
