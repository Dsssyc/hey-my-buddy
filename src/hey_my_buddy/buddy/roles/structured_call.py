"""Generic structured calls using native harness permissions, without a workflow turn."""
from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path

from ..harnesses.base import AdapterOutcome, ExecutionContext, ProcessHandle
from ..harnesses.controller import read_plain_evidence
from ...errors import BoardError
from ...private_dirs import ensure_private_dir
from ...paths import native_path
from ...protocol.structured_schema import schema_errors
from .turn_io import private_json, canonical_json


@dataclass(frozen=True)
class _NoToolEvidence:
    harness: str
    task_id: str
    attempt_id: str
    generation: int
    directory: Path
    private_root: Path
    evidence_root: Path
    request_json: str

def correction_code(raw, schema: dict) -> str | None:
    try:
        value = json.loads(raw) if isinstance(raw, str) else raw
    except (ValueError, RecursionError):
        return "answer-invalid-json"
    errors = schema_errors(value, schema)
    # Enum violations include choices outside frozen candidate bounds. Never retry them.
    if "enum" in errors:
        return None
    return "answer-shape" if errors else None


def structured_prompt(prompt: str, schema: dict) -> str:
    """Python supplies the answer schema for harnesses without a native JSON option."""
    return prompt + "\n\nReturn only one JSON value matching this schema: " + canonical_json(schema)


def _retain_no_tool_evidence(binding: _NoToolEvidence, payload: dict) -> None:
    observed_calls = 0
    # These paths and the request are frozen at start, never reconstructed from
    # the controller's mutable on-disk configuration after it has run.
    for number in (1, 2):
        source = binding.private_root / f"call-{number}"
        if not native_path(source).exists():
            continue
        if not native_path(source).is_dir():
            raise BoardError("PRIVATE_PATH_UNSAFE", "No-tool call source is not a directory", path=str(source))
        pair = [source / name for name in ("request.json", "result.json")]
        if not all(native_path(path).exists() for path in pair):
            continue
        values = [read_plain_evidence(path) for path in pair]
        target = ensure_private_dir(binding.evidence_root / f"call-{number}")
        for source_file, value in zip(pair, values):
            private_json(target / source_file.name, value)
        observed_calls += 1
    if not observed_calls:
        evidence = ensure_private_dir(binding.evidence_root / "call-1")
        private_json(evidence / "request.json", json.loads(binding.request_json))
        private_json(evidence / "result.json", payload)


def collect(handle: ProcessHandle) -> AdapterOutcome:
    from .run_execution import read_fast_result, read_review_result
    project = read_review_result if handle.role_run_control["operation"] == "review" else read_fast_result
    payload = project(handle)
    stopped = handle.shutdown_confirmed() is True
    status = "ok" if payload.get("status") == "ok" and handle.exit_code == 0 and stopped else "failed"
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
                                  identity={"harness": binding.harness, "taskId": binding.task_id,
                                            "attemptId": binding.attempt_id, "generation": binding.generation,
                                            "directory": str(binding.directory)})
            if isinstance(error, BoardError):
                diagnostic.update(reasonCode=error.code, **error.details)
            handle.evidence_retention_failure = diagnostic
            payload = {**payload, "status": "error", "code": "evidence-retention-failed", "evidenceRetention": diagnostic}
            status = "failed"
    return AdapterOutcome(status=status, result=payload, error=payload.get("code") if status != "ok" else None,
                          exit_code=handle.exit_code, shutdown_confirmed=stopped)
