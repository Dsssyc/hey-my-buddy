"""The transitional ZCode controller CLI: a thin projection over the one native run.

ADR-025 step 2-B keeps this process entry alive only so the existing Worker
and fast callers keep working until step 2-C switches the role wiring to the
registered run seam and deletes this file. It owns no lifecycle of its own:
the control file is projected into one frozen
:class:`~hey_my_buddy.buddy.harnesses.run_contract.RunRequest`, the role's
observation rules and session service binding are assembled exactly as the
2-C role wiring will, and :func:`hey_my_buddy.buddy.harnesses.zcode.native_run.run`
performs every native step; the returned :class:`RunResult` is projected back
into the legacy controller receipt, reading the run's own evidence references
for the facts the legacy shape still wants as reports. Model discovery goes
through the same module's separate no-prompt operation.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import signal
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

from ....errors import BoardError
from ....json_codec import canonical_json, decode_strict_json
from ...roles import worker_services
from ...roles.run_observers import FastCorrection, worker_observer
from ...roles.structured_call import no_tool_prompt, valid_answer
from ...roles.turn_io import input_hash, private_json, validate_outcome
from ..run_contract import (
    NetworkPolicy,
    PrivateStatePaths,
    RunBudget,
    RunConfiguration,
    RunContinuation,
    RunIdentity,
    RunRequest,
    SessionService,
)
from . import native_run
from .native_run import SessionServices
from .protocol import NativeError

#: The strict controller-result bound of the shared outer read; the request's
#: output budget mirrors it because this harness enforces only the timeout.
_STRICT_RESULT_BYTES = 512 * 1024


def _evidence(result, kind: str) -> dict | None:
    for ref in result.evidence_refs:
        if ref.kind == kind:
            try:
                value = decode_strict_json(Path(ref.location).read_bytes())
            except (OSError, ValueError):
                return None
            return value if isinstance(value, dict) else None
    return None


def _base_result(result, control: dict) -> dict:
    """The legacy receipt's common head, projected from the common result."""
    payload = {
        "status": result.end.status, "mode": "zcode",
        "harnessVersion": result.harness_version or "unknown",
        "requested": control.get("spec"), "resolved": None, "observed": None,
        "modelStarted": result.model_started is True,
        "processState": {"shutdownConfirmed": result.stop_evidence.native.group_state == "gone",
                         "nativeExitCode": result.end.native_exit_code},
    }
    checked = result.configuration.checked
    if checked.provider is not None and checked.model is not None and checked.effort is not None:
        payload["resolved"] = {"provider": checked.provider.value, "model": checked.model.value,
                               "effort": checked.effort.value}
    if result.native_identity is not None:
        payload["sessionId"] = result.native_identity.session_id
    if result.end.status != "ok":
        payload["code"] = result.end.reason_code
        payload["error"] = result.end.message or result.end.reason_code or "the native run failed"
        if result.end.reason_code == "native-disconnected":
            payload["failureKind"] = "transport"
    return payload


def _fast_result(result, control: dict, correction: FastCorrection, started_at: float) -> dict:
    request = control["noToolRequest"]
    payload = _base_result(result, control)
    if result.end.reason_code == "observer-interrupt" and correction.stop_reason:
        # The role observer stopped this run; its own recorded reason is the
        # legacy channel code, and the projection keeps the legacy shape of an
        # immediately refused structured call.
        payload["status"] = "error"
        payload["code"] = correction.stop_reason
        payload["error"] = f"the no-tool call was refused ({correction.stop_reason})"
    tools = result.tool_evidence.value if result.tool_evidence is not None else {}
    calls = tools.get("toolCalls", 0)
    payload["usage"] = {"toolCalls": calls, "bytesRead": None,
                        "elapsedMs": round((time.monotonic() - started_at) * 1000)}
    if payload["status"] == "ok":
        raw = result.value.raw if result.value is not None else None
        payload["rawAnswer"] = raw
        payload["answerValid"] = valid_answer(raw, request["outputSchema"])
        payload["correctionCount"] = result.value.correction_count if result.value else 0
        if result.native_identity is not None:
            payload["nativeIdentity"] = {key: value for key, value in
                                         (("sessionId", result.native_identity.session_id),
                                          ("turnId", result.native_identity.turn_id)) if value}
        payload["nativeEventCount"] = result.native_event_count
        payload["zeroToolVerified"] = calls == 0
        if request.get("captureEvidence"):
            payload["nativeEvidence"] = {"eventCount": payload["nativeEventCount"],
                                         "toolAllowlist": [], "titleGenerationEnabled": False,
                                         "streamEof": True}
    else:
        payload.pop("zeroToolVerified", None)
    if tools:
        payload["toolEvidence"] = tools
    return payload


def _worker_result(result, control: dict, turn_input: dict, prompt: str) -> dict:
    payload = _base_result(result, control)
    payload["tokenUsage"] = result.usage.value if result.usage is not None else None
    payload["lastAssistantMessage"] = (result.last_assistant_message.value
                                       if result.last_assistant_message is not None else None)
    if result.native_error is not None:
        payload["nativeFailure"] = result.native_error.value
    if result.native_failure is not None:
        payload["quotaFailure"] = result.native_failure.value
    inquiry = _evidence(result, "inquiry-report")
    if inquiry is not None:
        payload["inquiry"] = inquiry
    attention = _evidence(result, "attention-report")
    if attention is not None:
        payload["nativeAttention"] = attention
    if result.activity is not None:
        activity = result.activity.value
        payload["activity"] = {"published": True, "phase": activity.get("phase"),
                               "eventSeq": activity.get("eventSeq")}
    if result.end.status == "ok":
        completion = result.completion_evidence
        if completion is not None and completion.native_identity is not None:
            payload["nativeTurnId"] = completion.native_identity.turn_id
        provenance = _evidence(result, "turn-provenance")
        outcome = result.value.parsed.value if result.value is not None and result.value.parsed else None
        if isinstance(outcome, dict) and isinstance(provenance, dict):
            # The governed turn record is this role's document: the attempt
            # identity, input hash and prompt hash are the role's own facts,
            # the outcome and ordered provenance are the driver's evidence
            # parts, and the published record lands exactly where the role's
            # reader expects it.
            identity = {key: turn_input[key] for key in ("taskId", "attemptId", "generation", "turnId")}
            record = {"version": 1, **identity, "inputSha256": input_hash(turn_input),
                      "promptSha256": hashlib.sha256(prompt.encode()).hexdigest(),
                      "sessionId": payload.get("sessionId"),
                      "previousSessionId": turn_input.get("previousSessionId"),
                      "resumeMode": turn_input.get("resumeMode"),
                      "outcome": outcome, "provenance": provenance}
            private_json(Path(control["outputFile"]), record, exclusive=True)
            payload["nativeTurnId"] = provenance.get("nativeTurnId")
    return payload


def _worker_request(control: dict, *, invocation_root: Path):
    """Project one governed control file into the frozen request and services."""
    turn_input = decode_strict_json(Path(control["inputFile"]).read_bytes())
    if not isinstance(turn_input, dict):
        raise NativeError("invalid-input", "the governed input is not an object")
    identity = {key: turn_input[key] for key in ("taskId", "attemptId", "generation", "turnId")}
    inquiry = control.get("inquiry") if isinstance(control.get("inquiry"), dict) else None
    attention_path = Path(control["directory"]) / "attention.json"
    mount = native_run.prepare_session_service(
        invocation_root=invocation_root, identity=identity, input_sha256=input_hash(turn_input),
        attention_path=attention_path,
        session_tools=worker_services.session_tools(), completion_tool="buddy_finish_turn",
        inquiry=inquiry, inquiry_tools=("buddy_checkpoint", "buddy_answer_inquiry"))
    task_text = Path(control["taskFile"]).read_text()
    prompt = worker_services.governed_prompt(
        task_text, turn_input, mount.finish_tool,
        checkpoint_tool=mount.checkpoint_tool, answer_tool=mount.answer_tool)
    mode = turn_input.get("resumeMode")
    previous = turn_input.get("previousSessionId")
    continuation = None
    if mode in ("native-session", "reconstructed-new-session"):
        continuation = RunContinuation(mode=mode, previous_session_id=previous)
    names = [f"mcp__{mount.server_name}__{name}" for name in mount.bare_tools]
    request = RunRequest(
        identity=RunIdentity(task_id=identity["taskId"], attempt_id=identity["attemptId"],
                             generation=identity["generation"], invocation_id=uuid.uuid4().hex,
                             turn_id=identity["turnId"], input_sha256=input_hash(turn_input)),
        harness="zcode",
        configuration=RunConfiguration(**{key: control["spec"][key]
                                          for key in ("provider", "model", "effort")}),
        cwd=control["cwd"],
        private_state=PrivateStatePaths(invocation_root=str(invocation_root),
                                        native_root=control["nativeRoot"]),
        input_text=prompt, tool_scope="write", network=NetworkPolicy(requested=False),
        output_schema=worker_services.OUTCOME_SCHEMA,
        budget=RunBudget(timeout_seconds=control["timeoutSeconds"], max_output_bytes=_STRICT_RESULT_BYTES),
        continuation=continuation,
        session_services=(SessionService(service_id=mount.server_name, kind="session-tools",
                                         tool_names=names,
                                         input_schema=worker_services.OUTCOME_SCHEMA,
                                         delivery_mode="in-turn"),),
    )
    services = SessionServices(mount=mount, validate_outcome=validate_outcome, inquiry=inquiry,
                               activity_dir=str(control["directory"]),
                               native_stderr=str(Path(control["directory"]) / "native.stderr.log"))
    return request, services, worker_observer, turn_input


def _fast_request(control: dict, *, invocation_root: Path):
    """Project one no-tool control file into the frozen request and observer."""
    request_values = control["noToolRequest"]
    base_prompt = no_tool_prompt(request_values["prompt"], request_values["outputSchema"])
    request = RunRequest(
        identity=RunIdentity(task_id=control["taskId"], attempt_id=control["attemptId"],
                             generation=control["generation"], invocation_id=uuid.uuid4().hex),
        harness="zcode",
        configuration=RunConfiguration(**{key: control["spec"][key]
                                          for key in ("provider", "model", "effort")}),
        cwd=control["cwd"],
        private_state=PrivateStatePaths(invocation_root=str(invocation_root),
                                        native_root=control["nativeRoot"]),
        input_text=base_prompt, tool_scope="none", network=NetworkPolicy(requested=False),
        output_schema=request_values["outputSchema"],
        budget=RunBudget(timeout_seconds=control["timeoutSeconds"], max_output_bytes=_STRICT_RESULT_BYTES),
    )
    correction = FastCorrection(request_values["outputSchema"], base_prompt)
    return request, None, correction.observer, correction


def execute(control: dict, cancelled: threading.Event) -> tuple[dict, int]:
    started_at = time.monotonic()
    if "readOnlyRequest" in control:
        return {"status": "error", "code": "readonly-worker-carrier-unimplemented", "modelStarted": False,
                "processState": {"shutdownConfirmed": True}}, 1
    if control.get("discover"):
        catalog_value = native_run.run_discovery(
            cwd=control["cwd"], invocation_root=Path(control["directory"]),
            native_root=Path(control["nativeRoot"]), timeout_seconds=control["timeoutSeconds"],
            cancelled=cancelled.is_set)
        # run_discovery only returns past a confirmed group shutdown with a
        # zero native exit, so those are the reachability-checked stop facts.
        payload = {"status": "ok", "mode": "zcode", "harnessVersion": catalog_value["harnessVersion"],
                   "requested": control.get("spec"), "resolved": None, "observed": None,
                   "modelStarted": False, "catalog": catalog_value,
                   "processState": {"shutdownConfirmed": True, "nativeExitCode": 0}}
        return payload, 0
    invocation_root = Path(control.get("privateRoot") or control["directory"])
    if control.get("noToolRequest"):
        request, services, observer, correction = _fast_request(control, invocation_root=invocation_root)
        result = native_run.run(request, observer=observer, services=services,
                                cancelled=cancelled.is_set)
        return _fast_result(result, control, correction, started_at), 0 if result.end.status == "ok" else 1
    request, services, observer, turn_input = _worker_request(control, invocation_root=invocation_root)
    result = native_run.run(request, observer=observer, services=services,
                            cancelled=cancelled.is_set)
    payload = _worker_result(result, control, turn_input, request.input_text)
    return payload, 0 if payload["status"] == "ok" else 1


def run(control: dict, cancelled: threading.Event) -> tuple[dict, int]:
    """The legacy controller entry, kept as the same thin projection until 2-C."""
    return execute(control, cancelled)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--control", required=True)
    args = parser.parse_args()
    cancelled = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT,
                *((signal.SIGBREAK,) if hasattr(signal, "SIGBREAK") else ())):
        signal.signal(sig, lambda _sig, _frame: cancelled.set())
    try:
        result, code = execute(json.loads(Path(args.control).read_text()), cancelled)
    except (NativeError, OSError, ValueError, subprocess.TimeoutExpired, BoardError):
        result, code = {"status": "error", "code": "zcode-controller-failed",
                        "error": "ZCode controller failed before verified native settlement",
                        "processState": {"shutdownConfirmed": False}}, 1
    sys.stdout.write(canonical_json(result) + "\n")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
