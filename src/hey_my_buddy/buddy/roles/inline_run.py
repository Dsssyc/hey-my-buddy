"""A registered native run owned by the execution process itself."""
from __future__ import annotations

import hashlib
import math
import os
import time
import uuid
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path

from ...errors import BoardError
from ...json_codec import canonical_json, decode_strict_json
from ..harnesses.c_two_live import CTwoLiveEndpoint
from ..harnesses.live import EXISTING_CAPABILITIES, LiveCapabilities
from ..harnesses.registry import adapter, run_seam
from ..harnesses.run_contract import (
    EvidenceRef, FrozenJson, ResultBuddy, RunEnd, RunResult, StopEvidence, StopLayer,
    ResumeCheckpoint, RunBudget, RunContinuation,
)
from . import run_execution
from .controller import run_harness
from .prompts import wrap_up_prompt
from .turn_io import _private_bytes, guard_private_path


@contextmanager
def _inline_live(control, request, services, state_dir: Path, handle):
    capabilities = (EXISTING_CAPABILITIES[request.harness] if getattr(services, "inquiry", None)
                    else LiveCapabilities(inquiry_delivery="unsupported"))
    endpoint = CTwoLiveEndpoint(request.identity, capabilities, None, state_dir=state_dir)
    server = run_execution.execution_live_server()
    try:
        if server is not None:
            server.bind_native(endpoint)
        handle.live_endpoint = endpoint
        yield replace(services, live=endpoint) if services is not None else None
    finally:
        handle.live_endpoint = None
        if server is not None:
            server.unbind_native(endpoint)
        endpoint.close(reason="native-run-ended")


def _stop_checkpoint(result: RunResult, request) -> ResumeCheckpoint | None:
    try:
        value = run_execution._evidence(result, "native-checkpoint")
    except (OSError, ValueError, BoardError, RecursionError):
        return None
    identity = result.native_identity
    if not isinstance(value, dict) or identity is None or identity.turn_id is None:
        return None
    expected = {"taskId": request.identity.task_id, "attemptId": request.identity.attempt_id,
                "generation": request.identity.generation, "turnId": request.identity.turn_id,
                "inputSha256": request.identity.input_sha256, "sessionId": identity.session_id,
                "nativeTurnId": identity.turn_id}
    if any(value.get(key) != item for key, item in expected.items()):
        return None
    return ResumeCheckpoint(native_turn_id=identity.turn_id,
                            attempt_id=request.identity.attempt_id,
                            input_sha256=request.identity.input_sha256)


def _summary_run(control, request, result, module, state_dir, deadline, handle):
    """Resume one stopped native session while the Host grace remains."""
    from ...private_dirs import ensure_private_dir

    identity = result.native_identity
    if handle.force_cancelled.is_set():
        return control, request, result, False, time.monotonic() >= deadline
    if (identity is None or not identity.session_id
            or result.stop_evidence.native.group_state != "gone"
            or result.continuation is None or result.continuation.resumable is not True):
        return control, request, result, False, time.monotonic() >= deadline
    checkpoint = _stop_checkpoint(result, request)
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        return control, request, result, False, True
    root = ensure_private_dir(Path(control["privateRoot"]) / "stop-summary")
    task_path = root / "summary-task.txt"
    _private_bytes(task_path, wrap_up_prompt().encode(), exclusive=True)
    next_control = {**control, "privateRoot": str(root), "taskFile": str(task_path),
                    "invocationId": uuid.uuid4().hex,
                    "timeoutSeconds": min(86400, math.ceil(remaining))}
    next_request, services, observer, error = run_execution.worker_request(next_control, module)
    if error is not None:
        return control, request, result, False, time.monotonic() >= deadline
    next_request = next_request.model_copy(update={
        "continuation": RunContinuation(mode="native-session", previous_session_id=identity.session_id,
                                        checkpoint=checkpoint),
        "budget": RunBudget(timeout_seconds=next_control["timeoutSeconds"]),
        "session_record_failure": result.session_record_failure,
    })
    handle.request = next_request
    handle.role_run_active_control = next_control
    handle.role_run_active_identity = next_request.identity
    def summary_cancelled():
        return time.monotonic() >= deadline or handle.force_cancelled.is_set()
    if summary_cancelled():
        return control, request, result, False, time.monotonic() >= deadline
    try:
        with _inline_live(next_control, next_request, services, state_dir, handle) as live_services:
            summary = run_harness(module, next_request, observer=observer, services=live_services,
                                  cancelled=summary_cancelled)
    except Exception as error:
        summary = RunResult(identity=next_request.identity, harness=next_request.harness,
            end=RunEnd(status="unknown", reason_code="native-summary-unverified",
                       message="The summary run produced no verified stop receipt"),
            buddy=ResultBuddy(requested=next_request.buddy),
            session_record_failure=getattr(error, "session_record_failure", next_request.session_record_failure),
            stop_evidence=StopEvidence(native=StopLayer(group_state="unknown")))
        return next_control, next_request, summary, False, time.monotonic() >= deadline
    expired = time.monotonic() >= deadline
    if (expired or handle.force_cancelled.is_set()
            or summary.stop_evidence.native.group_state != "gone" or summary.end.status != "ok"):
        return next_control, next_request, summary, False, expired
    return next_control, next_request, summary, True, False


def _wrap_up_usage(first: RunResult, summary: RunResult) -> FrozenJson | None:
    known = [result.usage.value for result in (first, summary) if result.usage is not None]
    if not known:
        return None
    combined = {
        "version": 1, "source": "worker/wrap-up-native-turns", "scope": "attempt",
        "inputBasis": "includes-cached",
        "completeness": "complete" if len(known) == 2
        and all(reading["completeness"] == "complete" for reading in known) else "partial",
        "nativeRecords": sum(reading["nativeRecords"] for reading in known),
    }
    for field in ("inputTokens", "cachedInputTokens", "outputTokens", "reasoningOutputTokens"):
        counts = [reading.get(field) for reading in known]
        if field != "reasoningOutputTokens" or all(count is not None for count in counts):
            combined[field] = sum(counts) if all(count is not None for count in counts) else None
    return FrozenJson(combined)


def execute(control: dict, handle, state_dir: Path) -> tuple[RunResult, object, dict, int]:
    if control["operation"] == "review" and not adapter(control["harness"]).read_only_structured:
        raise BoardError("INVALID_ARGUMENT", "The harness has no review carrier")
    module = run_seam(control["harness"])
    if module is None:
        raise BoardError("ROLE_RUN_UNREGISTERED", "The role run requires a registered harness")
    started = time.monotonic()
    input_error = None
    if control["operation"] == "worker":
        request, services, observer, input_error = run_execution.worker_request(control, module)
        correction = None
    elif control["operation"] == "fast":
        request, services, observer, correction = run_execution.fast_request(control, module)
    elif control["operation"] == "review":
        request, services, observer, correction = run_execution.review_request(control, module)
    else:
        raise BoardError("INVALID_ARGUMENT", "Unknown role operation")
    handle.request = request
    original_identity = request.identity
    if input_error is None:
        with _inline_live(control, request, services, state_dir, handle) as live_services:
            result = run_harness(module, request, observer=observer, services=live_services,
                                 cancelled=handle.cancelled.is_set)
    else:
        result = RunResult(identity=request.identity, harness=request.harness,
            end=RunEnd(status="error", reason_code=input_error.code, message=input_error.message),
            buddy=ResultBuddy(requested=request.buddy), model_started=False,
            stop_evidence=StopEvidence(native=StopLayer(group_state="gone")))
    wrap_up_candidate = False
    grace_expired = False
    if (control["operation"] == "worker" and handle.cancelled.is_set()
            and result.end.status == "cancelled" and adapter(control["harness"]).interrupted_resume):
        deadline = handle.grace_deadline if handle.wrap_up_started else None
        if deadline is not None:
            first = result
            active_control, request, result, wrap_up_candidate, grace_expired = _summary_run(
                control, request, result, module, state_dir, deadline, handle)
            handle.role_run_active_control = active_control
            if result.identity != first.identity:
                result = result.model_copy(update={
                    "usage": _wrap_up_usage(first, result),
                    "session_record_failure": first.session_record_failure or result.session_record_failure,
                })
    if control["operation"] == "worker" and request.worker_permission is not None:
        path = Path(request.private_state.invocation_root) / "worker-permission.json"
        declaration = {"source": "harness-permission-declaration",
                       "binding": request.identity.to_payload(),
                       "permission": request.worker_permission.to_payload()}
        raw = canonical_json(declaration).encode()
        _private_bytes(path, raw, exclusive=True)
        ref = EvidenceRef(kind="worker-permission", location=str(path),
                          size_bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest())
        result = result.model_copy(update={"evidence_refs": (*result.evidence_refs, ref)})
    verdict = {"stopReason": correction.stop_reason if correction is not None else None,
               "elapsedMs": round((time.monotonic() - started) * 1000)}
    code = 0 if result.end.status == "ok" else 1
    if control["operation"] == "worker":
        delivered = False
        wrap_up_run = result.identity != original_identity
        if code == 0 and (not wrap_up_run or wrap_up_candidate):
            try:
                turn_input = decode_strict_json(guard_private_path(Path(control["inputFile"])).read_bytes())
                facts = {"sessionId": result.native_identity.session_id if result.native_identity else None}
                run_execution._worker_result(result, control, turn_input, request.input_text, facts)
                delivered = wrap_up_candidate
            except (BoardError, OSError, ValueError, RecursionError) as error:
                verdict["workerError"] = ({"code": error.code, "message": error.message}
                    if isinstance(error, BoardError) else
                    {"code": "invalid-role-result", "message": "The run's role evidence could not be collected"})
                code = 1
        verdict["wrapUpDelivered"] = delivered
        verdict["graceExpired"] = grace_expired
    return result, request, verdict, code
