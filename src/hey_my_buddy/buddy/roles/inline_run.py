"""A registered native run owned by the execution process itself."""
from __future__ import annotations

import math
import os
import time
import uuid
from contextlib import contextmanager
from dataclasses import replace
from functools import partial
from pathlib import Path

from ...errors import BoardError
from ...json_codec import decode_strict_json
from ...private_files import publish_bytes, read_bytes
from ...private_dirs import ensure_private_dir
from ...protocol.capabilities import select_input_limit
from ...protocol.input_failure import INPUT_REJECTED, input_failure, is_input_too_large
from ...protocol.input_limit import check_prompt_input
from ...protocol.stop import WRAP_UP_INITIATORS
from ..harnesses.c_two_live import CTwoLiveEndpoint
from ..harnesses.live import EXISTING_CAPABILITIES, LiveCapabilities
from ..harnesses.registry import adapter, run_seam
from ..harnesses.runtime_selection import environment_for, selected
from ..harnesses.run_contract import (
    FrozenJson, ResultBuddy, RunEnd, RunResult, StopEvidence, StopLayer,
    ResumeCheckpoint, RunBudget, RunContinuation,
)
from . import run_execution
from .controller import run_harness
from .prompts import wrap_up_prompt


def _prelaunch_stopped(handle) -> bool:
    """True when this handle or its owning execution already accepted a stop.

    Only called before entering ``run_harness`` (never as the in-flight
    cancellation callback): an in-flight stop must travel through
    ``worker_stop`` so wrap-up grace and stop policy are never bypassed.
    """
    if handle.cancelled.is_set():
        return True
    reader = getattr(handle, "accepted_stop", None)
    view = reader() if callable(reader) else None
    if not isinstance(view, dict):
        return False
    # A stop accepted before control.attach still binds the handle's own stop
    # facts so termination keeps the real initiator instead of falling back.
    with handle.stop_lock:
        handle.cancel_requested = True
        if handle.stop_initiator is None:
            handle.stop_initiator = view.get("stopInitiator")
        handle.cancelled.set()
    return True


def _offset_result_activity(result, offset: int):
    if not offset:
        return result
    activity = getattr(result, "activity", None)
    value = activity.value if activity is not None else None
    if not isinstance(value, dict):
        return result
    seq = value.get("eventSeq")
    if type(seq) is not int or seq < 0:
        return result
    return result.model_copy(update={"activity": FrozenJson({**value, "eventSeq": seq + offset})})


def _next_event_seq_offset(current: int, live_activity: dict | None, result) -> int:
    highest = None
    for candidate in (live_activity, getattr(getattr(result, "activity", None), "value", None)):
        if isinstance(candidate, dict):
            seq = candidate.get("eventSeq")
            if type(seq) is int and seq >= 0 and (highest is None or seq > highest):
                highest = seq
    return max(current, highest + 1) if highest is not None else current


def _profile_execute(control, handle, module, started):
    """Correct a profile in fresh native sessions, each with its own start wait."""
    from ...protocol.capabilities import select_input_limit
    from ...protocol.input_limit import check_prompt_input
    from ..harnesses.runtime_selection import environment_for, selected
    from ...protocol import tool_evidence, usage

    total_calls = 0
    launched_rounds = 0
    usage_readings = []
    evidences = []
    native_identities = []
    model_responded = []
    request_sent = []
    feedback = None
    previous_raw = None
    errors = None
    event_seq_offset = 0
    result = request = observer = None
    round_index = 0
    model_wait = handle.model_response_wait
    while round_index <= 2:
        if round_index:
            # Check an already-accepted stop or latched timeout before re-arming
            # the correction round's wait, then clear model_responded and start
            # the timer atomically under model_wait.lock so the supervision loop
            # can never write the previous round's True back onto the new budget.
            if _prelaunch_stopped(handle) or (model_wait is not None and model_wait.expired_now()):
                break
            if model_wait is not None:
                model_wait.begin(handle)
            else:
                handle.model_responded = False
        invocation_root = ensure_private_dir(Path(control["privateRoot"]) / f"call-{round_index + 1}")
        next_control = {**control, "invocationId": uuid.uuid4().hex,
                        "privateRoot": str(invocation_root)}
        next_request, services, observing, observer, parts = run_execution.profile_request(
            next_control, module, round_index=round_index, previous_raw=previous_raw,
            errors=errors)
        record = selected(next_request.harness, environment_for())
        version = record.get("version") if record is not None else None
        blocked = check_prompt_input(select_input_limit(next_request.harness, version), parts).blocked
        # A stop, or a model-response wait that already expired or timed out,
        # must not enter the native harness. The first round is rechecked here
        # exactly like a correction round, so a profile stopped or timed out
        # while its round was being prepared never starts a native session.
        if _prelaunch_stopped(handle) or (model_wait is not None and model_wait.expired_now()):
            if result is None:
                # No native call was ever sent: the stopped attempt itself is
                # the result — cancelled, with no native session behind it.
                result = RunResult(identity=next_request.identity, harness=next_request.harness,
                    end=RunEnd(status="cancelled"),
                    buddy=ResultBuddy(requested=next_request.buddy), native_request_sent=False,
                    model_responded=False,
                    stop_evidence=StopEvidence(native=StopLayer(group_state="gone")))
                request = next_request
            break
        request = next_request
        handle.request = request
        handle.role_run_active_control = next_control
        handle.role_run_active_identity = request.identity
        if blocked:
            result = RunResult(identity=request.identity, harness=request.harness,
                end=RunEnd(status="error", reason_code="input-rejected",
                           message="The profile prompt exceeds the harness input limit"),
                harness_version=version, buddy=ResultBuddy(requested=request.buddy),
                stop_evidence=StopEvidence(native=StopLayer(group_state="gone")))
            break
        live_activity = None
        launched = False
        with _inline_live(request, services, handle, event_seq_offset=event_seq_offset) as live_services:
            if _prelaunch_stopped(handle) or (model_wait is not None and model_wait.expired_now()):
                if result is None:
                    result = RunResult(identity=request.identity, harness=request.harness,
                        end=RunEnd(status="cancelled"),
                        buddy=ResultBuddy(requested=request.buddy), native_request_sent=False,
                        model_responded=False,
                        stop_evidence=StopEvidence(native=StopLayer(group_state="gone")))
            else:
                result = run_harness(module, request, observer=partial(handle.observe, observing),
                                     services=live_services, cancelled=handle.cancelled.is_set)
                launched = True
                if live_services is not None and getattr(live_services, "live", None) is not None:
                    live_activity = live_services.live.current_activity()
        if not launched:
            break
        launched_rounds += 1
        result = _offset_result_activity(result, event_seq_offset)
        event_seq_offset = _next_event_seq_offset(event_seq_offset, live_activity, result)
        model_responded.append(result.model_responded)
        request_sent.append(result.native_request_sent)
        tools = result.tool_evidence.value if result.tool_evidence is not None else {}
        evidences.append(tools if tools else None)
        if result.native_identity is not None:
            native_identities.append(result.native_identity.to_payload())
        calls = tools.get("toolCalls")
        if type(calls) is not int:
            calls = 0
        total_calls += calls
        usage_readings.append(usage.normalize_token_usage(result.usage.value)
                              if result.usage is not None else None)
        if result.end.status != "ok":
            break
        if result.stop_evidence.native.group_state != "gone":
            break
        raw = result.value.raw if result.value is not None else None
        if raw is None:
            break
        observations = {"toolCalls": calls, "toolMarkerFrames": tools.get("toolMarkerFrames", 0),
                        "deniedInteractions": tools.get("deniedInteractions", 0)}
        publisher = run_execution._profile_publisher.get()
        if publisher is None:
            raise BoardError("INVALID_ARGUMENT", "The profile service publisher is unavailable")
        feedback = publisher(raw, round_index, observations)
        if feedback["accepted"] or not feedback["errors"] or round_index == 2:
            break
        previous_raw, errors = raw, feedback["errors"]
        round_index += 1
    token_usage = None
    if usage_readings:
        totals = {"version": 1, "source": "profile/native-sessions", "scope": "attempt",
                  "inputBasis": "includes-cached",
                  "completeness": "complete" if all(reading is not None and reading["completeness"] == "complete"
                                                       for reading in usage_readings) else "partial",
                  "nativeRecords": sum((reading or {}).get("nativeRecords", 0) for reading in usage_readings) or 1}
        for field in ("inputTokens", "cachedInputTokens", "outputTokens", "reasoningOutputTokens"):
            values = [(reading or {}).get(field) for reading in usage_readings]
            totals[field] = sum(values) if all(value is not None for value in values) else None
        token_usage = usage.normalize_token_usage(totals)
    aggregate_evidence = None
    if len(evidences) == 1:
        aggregate_evidence = evidences[0]
    elif evidences and all(evidence is not None for evidence in evidences):
        binding = evidences[0]["binding"]
        roots = []
        calls = []
        for evidence in evidences:
            if evidence["binding"] != binding:
                raise BoardError("INVALID_ARGUMENT", "Profile run evidence has inconsistent bindings")
            roots.extend(evidence["nativeIdentity"])
            calls.extend(tool_evidence.read_tool_calls(evidence, state_dir=Path(control["stateDir"])))
        aggregate_evidence = {
            "version": tool_evidence.TOOL_EVIDENCE_VERSION, "binding": binding,
            "nativeIdentity": roots,
            "streamComplete": all(evidence["streamComplete"] for evidence in evidences),
            "calls": tool_evidence.write_tool_calls(binding, calls, state_dir=Path(control["stateDir"])),
            "toolCalls": sum(evidence["toolCalls"] for evidence in evidences),
            "unsettledToolCalls": sum(evidence["unsettledToolCalls"] for evidence in evidences),
            "toolSummary": tool_evidence.merge_tool_summaries(evidence["toolSummary"] for evidence in evidences),
        }
    def any_fact(values):
        return True if True in values else None if None in values else False
    verdict = {"modelResponded": any_fact(model_responded), "nativeRequestSent": any_fact(request_sent),
               "elapsedMs": round((time.monotonic() - started) * 1000),
               "toolCalls": total_calls,
               # Only rounds that actually entered a native harness count: the
               # first of them is the initial answer, every later one a correction.
               "correctionCount": launched_rounds - 1 if launched_rounds else 0,
               "profileFeedback": feedback, "tokenUsage": token_usage,
               "toolEvidence": aggregate_evidence, "nativeIdentity": native_identities}
    code = 0 if result.end.status == "ok" and feedback and feedback["accepted"] else 1
    return result, request, verdict, code


@contextmanager
def _inline_live(request, services, handle, *, event_seq_offset: int = 0):
    capabilities = (EXISTING_CAPABILITIES[request.harness] if services is not None and services.inquiry is not None
                    else LiveCapabilities(inquiry_delivery="unsupported"))
    endpoint = CTwoLiveEndpoint(request.identity, capabilities, event_seq_offset=event_seq_offset)
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


def _summary_run(control, request, result, module, deadline, handle):
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
    publish_bytes(task_path, wrap_up_prompt().encode(), immutable=True)
    next_control = {**control, "privateRoot": str(root), "taskFile": str(task_path),
                    "invocationId": uuid.uuid4().hex,
                    "timeoutSeconds": min(86400, math.ceil(remaining))}
    next_request, services, observer, _, _parts = run_execution.worker_request(next_control, module)
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
        with _inline_live(next_request, services, handle) as live_services:
            summary = run_harness(module, next_request, observer=partial(handle.observe, observer), services=live_services,
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


def execute(control: dict, handle) -> tuple[RunResult, object, dict, int]:
    module = run_seam(control["harness"])
    started = time.monotonic()
    if control["operation"] == "profile":
        return _profile_execute(control, handle, module, started)
    input_error = None
    if control["operation"] == "worker":
        request, services, observer, input_error, parts = run_execution.worker_request(control, module)
        correction = None
    elif control["operation"] == "fast":
        request, services, observer, correction, parts = run_execution.fast_request(control, module)
    elif control["operation"] == "review":
        request, services, observer, correction, parts = run_execution.review_request(control, module)
    else:
        raise BoardError("INVALID_ARGUMENT", "Unknown role operation")
    handle.request = request
    original_identity = request.identity
    record = selected(request.harness, environment_for())
    version = record.get("version") if record is not None else None
    check = check_prompt_input(select_input_limit(request.harness, version), parts)
    if input_error is None and check.blocked:
        input_error = BoardError(INPUT_REJECTED, "The initial prompt exceeds the harness's declared input limit")
    model_wait = handle.model_response_wait
    if _prelaunch_stopped(handle) or (model_wait is not None and model_wait.expired_now()):
        result = RunResult(identity=request.identity, harness=request.harness,
            end=RunEnd(status="cancelled"), harness_version=version,
            buddy=ResultBuddy(requested=request.buddy), native_request_sent=False,
            model_responded=False,
            stop_evidence=StopEvidence(native=StopLayer(group_state="gone")))
    elif input_error is None:
        with _inline_live(request, services, handle) as live_services:
            if _prelaunch_stopped(handle) or (model_wait is not None and model_wait.expired_now()):
                result = RunResult(identity=request.identity, harness=request.harness,
                    end=RunEnd(status="cancelled"), harness_version=version,
                    buddy=ResultBuddy(requested=request.buddy), native_request_sent=False,
                    model_responded=False,
                    stop_evidence=StopEvidence(native=StopLayer(group_state="gone")))
            else:
                result = run_harness(module, request, observer=partial(handle.observe, observer), services=live_services,
                                     cancelled=handle.cancelled.is_set)
        if result.native_admission_error is not None:
            native_error = result.native_admission_error.value
            native_version = result.harness_version if result.harness_version is not None else version
            native_check = (check_prompt_input(select_input_limit(request.harness, native_version), parts)
                            if native_version != version else check)
            failure = input_failure(native_check, stage="native-admission", harness=request.harness,
                version=native_version,
                native_request_sent=result.native_request_sent, model_responded=result.model_responded,
                native_error=native_error)
            update = {"input_failure": FrozenJson(failure)}
            if is_input_too_large(native_error):
                update["end"] = result.end.model_copy(update={"reason_code": INPUT_REJECTED})
                update["provider_rejection"] = None
            result = result.model_copy(update=update)
    else:
        result = RunResult(identity=request.identity, harness=request.harness,
            end=RunEnd(status="error", reason_code=input_error.code, message=input_error.message),
            harness_version=version, buddy=ResultBuddy(requested=request.buddy),
            native_request_sent=False, model_responded=False,
            input_failure=(input_failure(check, stage="preflight", harness=request.harness, version=version,
                           native_request_sent=False, model_responded=False)
                           if input_error.code == INPUT_REJECTED else None),
            stop_evidence=StopEvidence(native=StopLayer(group_state="gone")))
    wrap_up_candidate = False
    grace_expired = False
    if (control["operation"] == "worker" and handle.stop_initiator in WRAP_UP_INITIATORS
            and handle.cancelled.is_set()
            and result.end.status == "cancelled" and adapter(control["harness"]).interrupted_resume):
        deadline = handle.grace_deadline if handle.wrap_up_started else None
        if deadline is not None:
            first = result
            active_control, request, result, wrap_up_candidate, grace_expired = _summary_run(
                control, request, result, module, deadline, handle)
            handle.role_run_active_control = active_control
            if result.identity != first.identity:
                result = result.model_copy(update={
                    "usage": _wrap_up_usage(first, result),
                    "session_record_failure": first.session_record_failure or result.session_record_failure,
                })
    verdict = {"elapsedMs": round((time.monotonic() - started) * 1000)}
    if control["operation"] == "fast":
        verdict["stopReason"] = correction.stop_reason
    code = 0 if result.end.status == "ok" else 1
    if control["operation"] == "worker":
        delivered = False
        wrap_up_run = result.identity != original_identity
        if code == 0 and (not wrap_up_run or wrap_up_candidate):
            try:
                turn_input = decode_strict_json(read_bytes(Path(control["inputFile"])))
                handle.turn_record = run_execution._worker_result(
                    result, control, turn_input, request.input_text)
                delivered = wrap_up_candidate
            except (BoardError, OSError, ValueError, RecursionError) as error:
                verdict["workerError"] = ({"code": error.code, "message": error.message}
                    if isinstance(error, BoardError) else
                    {"code": "invalid-role-result", "message": "The run's role evidence could not be collected"})
                code = 1
        verdict["wrapUpDelivered"] = delivered
        verdict["graceExpired"] = grace_expired
    return result, request, verdict, code
