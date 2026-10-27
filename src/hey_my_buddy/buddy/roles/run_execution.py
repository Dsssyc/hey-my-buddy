"""Role execution over registered runs, using the shared controller and handles.

Worker prompts, result publication and the fast verdict live here. Native
service naming, provenance, storage and discovery come only from the registry.
"""
from __future__ import annotations

import hashlib
import math
import os
import shutil
import secrets
import sys
import threading
import time
import uuid
from contextvars import copy_context
from functools import partial
from pathlib import Path

from ...errors import BoardError
from ...paths import native_path, resolved_path
from ...json_codec import decode_strict_json
from ...private_dirs import context_root, native_root, ensure_private_dir, make_temporary_dir
from ...private_files import open_read, publish_bytes, read_bytes, read_text
from ...protocol import usage
from ...protocol.capabilities import PERMISSION_LEVELS
from ...protocol.stop import WRAP_UP_INITIATORS
from ..harnesses.base import AdapterOutcome, ExecutionContext
from ..harnesses.controller import (
    STRICT_RESULT_BYTES, collect_controller, launch_controller, read_strict_result, signal_name, stop_confirmed,
)
from ..harnesses.run_contract import (
    PrivateStatePaths,
    ResumeCheckpoint, RunBudget, RunBuddy, RunContinuation, RunIdentity, RunRequest, RunResult,
)
from . import turn_io, worker_services
from .run_observers import FastCorrection, ReviewObserver, worker_observer
from .run_controller import NATIVE_OPERATIONS
from .structured_call import (
    _NoToolEvidence, structured_prompt,
)
from .turn_io import input_hash, private_json, validate_outcome, canonical_json

WORKER_STOP_GRACE_SECONDS = 8.0
_NATIVE_CONTROL = "native-control.json"
_execution_live_server = None


def set_execution_live_server(server) -> None:
    """Bind the single execution endpoint before starting any native run."""
    global _execution_live_server
    _execution_live_server = server


def execution_live_server():
    return _execution_live_server


class InlineRunHandle:
    """The held result and stop intent for a native run in this process."""

    def __init__(self, control: dict, identity: RunIdentity, *, grace_seconds: float):
        self.role_run_control = control
        self.role_run_identity = identity
        self.role_run_original_identity = identity
        self.role_run_active_control = control
        self.role_run_active_identity = identity
        self.result: RunResult | None = None
        self.request: RunRequest | None = None
        self.verdict: dict | None = None
        self.turn_record: dict | None = None
        self.exit_code: int | None = None
        self.cancel_requested = False
        self.force_stop_requested = False
        self.wrap_up_started = False
        self.stop_initiator: str | None = None
        self.signalled_at: float | None = None
        self.grace_deadline: float | None = None
        self.grace_expired = False
        self.stop_lock = threading.RLock()
        self.cancelled = threading.Event()
        self.force_cancelled = threading.Event()
        self.done = threading.Event()
        self.error: BaseException | None = None
        self.deadline = (time.monotonic() + control["timeoutSeconds"] + grace_seconds
                         if control["timeoutSeconds"] is not None else math.inf)
        self.thread: threading.Thread | None = None
        self.live_endpoint = None

    @property
    def pid(self) -> int:
        return os.getpid()

    @property
    def finished(self) -> bool:
        return self.done.is_set()

    def wait(self, timeout: float | None) -> int | None:
        self.done.wait(timeout)
        return self.exit_code if self.done.is_set() else None

    def terminate(self, *, grace_seconds: float = WORKER_STOP_GRACE_SECONDS) -> None:
        self.cancel_requested = True
        self.force_stop_requested = True
        self.signalled_at = time.monotonic()
        self.force_cancelled.set()
        self.cancelled.set()

    def shutdown_confirmed(self, settle_seconds: float = 0) -> bool:
        return bool(self.done.is_set() and self.result is not None
                    and self.result.stop_evidence.native.group_state == "gone")


def _evidence(result, kind: str) -> dict | None:
    for ref in result.evidence_refs:
        if ref.kind == kind:
            with open_read(Path(ref.location)) as stream:
                raw = stream.read(STRICT_RESULT_BYTES + 1)
            if (len(raw) > STRICT_RESULT_BYTES or ref.size_bytes != len(raw)
                    or ref.sha256 != hashlib.sha256(raw).hexdigest()):
                raise BoardError("INVALID_ARGUMENT", "The native evidence reference no longer matches its content")
            value = decode_strict_json(raw)
            if not isinstance(value, dict):
                raise BoardError("INVALID_ARGUMENT", "Native role evidence must be an object")
            return value
    return None


def _base_result(result) -> dict:
    """The role receipt's common head, projected from the common result."""
    payload = {
        "status": result.end.status, "mode": result.harness,
        "harnessVersion": result.harness_version or "unknown",
        "requested": result.buddy.requested.to_payload() if result.buddy.requested else None,
        "resolved": None,
        "modelStarted": result.model_started is True,
        "processState": {"shutdownConfirmed": result.stop_evidence.native.group_state == "gone",
                         "nativeExitCode": result.end.native_exit_code},
    }
    checked = result.buddy.checked
    if checked.provider is not None and checked.model is not None and checked.effort is not None:
        payload["resolved"] = {"provider": checked.provider.value, "model": checked.model.value,
                               "effort": checked.effort.value}
    if result.native_identity is not None:
        payload["sessionId"] = result.native_identity.session_id
    if result.end.status != "ok":
        payload["code"] = result.end.reason_code
        payload["error"] = result.end.message or result.end.reason_code or "the native run failed"
    if result.provider_rejection is not None:
        payload["providerRejection"] = result.provider_rejection.value
    if result.session_record_failure is not None:
        payload["sessionRecordFailure"] = result.session_record_failure.to_payload()
    _merge_native_evidence(result, payload)
    return payload


def _merge_native_evidence(result: RunResult, payload: dict) -> None:
    evidence = result.native_evidence
    if evidence is None:
        return
    facts = {}
    if evidence.unknown_events is not None and evidence.unknown_events.total:
        facts["unknownEvents"] = evidence.unknown_events.to_payload()
    if evidence.unconfirmed:
        facts["unconfirmed"] = list(evidence.unconfirmed)
    if facts:
        payload.setdefault("nativeEvidence", {}).update(facts)


def _fast_result(result, request: RunRequest, verdict: dict) -> dict:
    from ..harnesses.registry import fast_evidence_is_top_level, run_seam

    payload = _base_result(result)
    if result.end.reason_code == "observer-interrupt" and verdict.get("stopReason"):
        # The role observer stopped this run; its own recorded reason is the
        # legacy channel code, and the projection keeps the legacy shape of an
        # immediately refused structured call.
        payload["status"] = "error"
        payload["code"] = verdict.get("stopReason")
        payload["error"] = "the no-tool call was refused (" + verdict["stopReason"] + ")"
    tools = result.tool_evidence.value if result.tool_evidence is not None else {}
    calls = tools.get("toolCalls", 0)
    payload["usage"] = {"toolCalls": calls, "bytesRead": None,
                        "elapsedMs": verdict["elapsedMs"]}
    if payload["status"] == "ok":
        raw = result.value.raw if result.value is not None else None
        payload["rawAnswer"] = raw
        payload["correctionCount"] = result.value.correction_count if result.value else 0
        if result.native_identity is not None:
            payload["nativeIdentity"] = result.native_identity.to_payload()
        payload["nativeEventCount"] = result.native_event_count
    if result.usage is not None:
        payload["tokenUsage"] = usage.normalize_token_usage(result.usage.value)
    top_level = fast_evidence_is_top_level(result.harness)
    if top_level or request.capture_evidence and payload["status"] == "ok":
        module = run_seam(result.harness)
        evidence = module.native_evidence(result) if module.native_evidence is not None else {}
        if top_level:
            payload.update(evidence)
        elif evidence:
            payload.setdefault("nativeEvidence", {}).update(evidence)
    if tools:
        payload["toolEvidence"] = tools
    return payload


def _worker_facts(result, request: RunRequest | None = None, *, delegation_authority: bool = False) -> dict:
    from ..harnesses.registry import worker_format

    format = worker_format(result.harness)
    if format is not None and delegation_authority:
        format = format.for_turn({"context": {"delegationAuthority": True}})
    payload = _base_result(result)
    if format is not None:
        payload.update(format.receipt_fields(result))
    # The micro task receipt carries the on-the-spot model fact with the same
    # verified-evidence rule as every other reference: a reference that no
    # longer matches its content fails the collection, never silently.
    check = _evidence(result, "model-check")
    if isinstance(check, dict) and check.get("selectedModelListed") is False \
            and isinstance(check.get("selected"), dict):
        payload["selectedModelListed"] = False
        payload["selectedModel"] = check["selected"]
    if request is not None:
        payload.setdefault("nativeEvidence", {})["permission"] = {
            "level": request.permission,
            "enforcement": PERMISSION_LEVELS[request.harness][request.permission],
        }
    payload["tokenUsage"] = result.usage.value if result.usage is not None else None
    payload["lastAssistantMessage"] = (result.last_assistant_message.value
                                       if result.last_assistant_message is not None else None)
    if result.native_error is not None:
        payload["nativeFailure"] = result.native_error.value
    if result.activity is not None:
        activity = result.activity.value
        payload["activity"] = {"phase": activity.get("phase"),
                               "eventSeq": activity.get("eventSeq")}
    return payload


def _review_result(result, request: RunRequest, verdict: dict) -> dict:
    payload = _base_result(result)
    facts = _worker_facts(result)
    if _worker_reports(result, facts):
        payload.update(status="error", code="invalid-native-result")
    for key in ("tokenUsage", "providerRejection", "lastAssistantMessage",
                "nativeInterruptRequested", "nativeInterruptAcknowledged"):
        if key in facts:
            payload[key] = facts[key]
    tools = result.tool_evidence.value if result.tool_evidence is not None else None
    payload["usage"] = {"toolCalls": tools.get("toolCalls") if tools else None,
                        "bytesRead": None, "elapsedMs": verdict["elapsedMs"]}
    if tools is not None:
        payload["toolEvidence"] = tools
    if result.end.reason_code == "observer-interrupt" and verdict.get("stopReason"):
        payload.update(status="error", code=verdict["stopReason"], error="Read-only tool budget exhausted")
    if payload["status"] == "ok":
        raw = (result.value.parsed.value if result.value.parsed is not None else result.value.raw
               ) if result.value is not None else None
        payload.update(rawAnswer=raw,
                       correctionCount=(result.value.correction_count or 0) if result.value is not None else 0)
        if result.native_identity is not None:
            payload["nativeIdentity"] = result.native_identity.to_payload()
    from ..harnesses.registry import run_seam
    module = run_seam(result.harness)
    if module.native_evidence is not None:
        payload.update(module.native_evidence(result))
    return payload


def _worker_reports(result, payload: dict) -> bool:
    """Keep each validated report even when another report or delivery fails."""
    invalid = False
    for kind, key in (("inquiry-report", "inquiry"), ("inquiry-tool-failures", "inquiryToolFailures"),
                      ("attention-report", "nativeAttention"),
                      ("native-checkpoint", "nativeCheckpoint"),
                      ("native-observations", None), ("native-turn-facts", None)):
        try:
            report = _evidence(result, kind)
        except (OSError, ValueError, BoardError, RecursionError):
            invalid = True
        else:
            if report is None:
                continue
            if key is not None:
                payload[key] = report
                if key == "nativeCheckpoint" and result.native_identity is not None:
                    payload["nativeTurnId"] = result.native_identity.turn_id
            elif kind == "native-observations":
                for field in ("sessionModel", "observedModels", "totalCostUsd"):
                    if field in report:
                        payload[field] = report[field]
                if report.get("unsupportedNativeRequests"):
                    payload["unsupportedNativeRequests"] = report["unsupportedNativeRequests"]
                for field in ("nativeInterruptRequested", "nativeInterruptAcknowledged"):
                    if type(report.get(field)) is bool:
                        payload[field] = report[field]
            elif "permissionDenials" in report:
                payload["nativeAttention"] = {
                    "requests": len(report.get("deniedControlRequestIds") or []),
                    "resultDenials": report["permissionDenials"],
                }
    return invalid


def _worker_result(result, control: dict, turn_input: dict, prompt: str) -> dict:
    outcome, provenance = worker_delivery(
        result, delegation_authority=turn_input.get("context", {}).get("delegationAuthority") is True)
    identity = {key: turn_input[key] for key in ("taskId", "attemptId", "generation", "turnId")}
    record = {"version": 1, **identity, "inputSha256": input_hash(turn_input),
              "promptSha256": hashlib.sha256(prompt.encode()).hexdigest(),
              "sessionId": result.native_identity.session_id if result.native_identity else None,
              "previousSessionId": turn_input.get("previousSessionId"),
              "resumeMode": turn_input["resumeMode"],
              "outcome": outcome, "provenance": provenance}
    raw = canonical_json(record).encode()
    if len(raw) > turn_io.MAX_RECORD_BYTES:
        raise BoardError("invalid-result", "the turn record exceeds its byte bound")
    publish_bytes(Path(control["outputFile"]), raw, immutable=True)
    return record


def worker_delivery(result, *, delegation_authority: bool = False) -> tuple[dict, dict]:
    """Judge the role's delivery once through the selected completion policy."""
    from ..harnesses.registry import worker_format

    format = worker_format(result.harness)
    if format is not None and delegation_authority:
        format = format.for_turn({"context": {"delegationAuthority": True}})
    if format is not None:
        facts = _evidence(result, "native-turn-facts")
        if not isinstance(facts, dict):
            raise BoardError("invalid-result", "The native run has no correlated completion facts")
        inquiry = _evidence(result, "inquiry-report")
        if inquiry is not None:
            facts = {**facts, "unansweredDeliveredInquiries": inquiry.get("unansweredDelivered", 0)}
        return format.delivery(result, facts)
    provenance = _evidence(result, "turn-provenance")
    outcome = result.value.parsed.value if result.value is not None and result.value.parsed else None
    if (result.value is None or result.value.schema_status != "valid"
            or validate_outcome(outcome, delegation_authority=delegation_authority) is not None
            or not isinstance(provenance, dict)):
        raise BoardError("INVALID_ARGUMENT", "The Worker run did not deliver its valid outcome and provenance")
    return outcome, provenance


def worker_request(control: dict, module):
    """Project one micro task control file into the frozen request and services."""
    invocation_root = Path(control["privateRoot"])
    turn_input = decode_strict_json(read_bytes(Path(control["inputFile"])))
    if not isinstance(turn_input, dict):
        raise BoardError("INVALID_ARGUMENT", "the micro task input is not an object")
    identity = {key: turn_input[key] for key in ("taskId", "attemptId", "generation", "turnId")}
    delegation_authority = turn_input.get("context", {}).get("delegationAuthority") is True
    credential_file = control.get("credentialFile") if delegation_authority else None
    if delegation_authority and not credential_file:
        raise BoardError("UNAUTHORIZED", "A Lead turn requires its attempt credential file")
    inquiry = control.get("inquiry") if isinstance(control.get("inquiry"), dict) else None
    attention_path = Path(control["directory"]) / "attention.json"
    task_text = read_text(Path(control["taskFile"]))
    from ..harnesses.registry import worker_format
    format = worker_format(control["harness"])
    permission = turn_input["executionWorkspace"]["access"]
    if format is None:
        binding = module.prepare_services(
            invocation_root=invocation_root, identity=identity, input_sha256=input_hash(turn_input),
            attention_path=attention_path,
            session_tools=worker_services.session_tools(delegation_authority=delegation_authority),
            completion_tool="hey_my_buddy_finish_turn",
            inquiry=inquiry, inquiry_tools=("hey_my_buddy_checkpoint", "hey_my_buddy_answer_inquiry"),
            validate_outcome=partial(validate_outcome, delegation_authority=delegation_authority),
            delegation_authority=delegation_authority,
            native_stderr=str(invocation_root / "native-stderr.log"))
        prompt = worker_services.micro_task_prompt(
            task_text, turn_input, binding.completion_tool,
            checkpoint_tool=binding.checkpoint_tool, answer_tool=binding.answer_tool,
            credential_file=credential_file)
        services, descriptions = binding.services, (binding.description,)
        mount = services.mount
        schema = next(tool["inputSchema"] for tool in mount.tool_contracts
                      if f"mcp__{mount.server_name}__{tool['name']}" == binding.completion_tool)
    else:
        format = format.for_turn(turn_input)
        prompt, schema = format.prompt(task_text, turn_input, credential_file=credential_file), format.schema
        inquiry_services = None
        if inquiry is not None:
            inquiry_services = module.prepare_inquiry_services(
                invocation_root=invocation_root, identity=identity, input_sha256=input_hash(turn_input),
                attention_path=attention_path, inquiry=inquiry, answer_schema=worker_services.ANSWER_SCHEMA)
            prompt += ("\nHost questions may arrive as inputs during this running turn. Answer each with "
                       f"{inquiry_services.answer_tool}, using its exact "
                       "inquiryId and answer text. Assistant prose does not count as an answer. "
                       "Questions never open a new turn or extend this turn's deadline.\n")
        services = _native_services(module, inquiry=inquiry_services)
        descriptions = ()
    mode = turn_input.get("resumeMode")
    previous = turn_input.get("previousSessionId")
    continuation = None
    if mode in ("native-session", "reconstructed-new-session"):
        checkpoint = (turn_input.get("context") or {}).get("nativeResume") if mode == "native-session" else None
        continuation = RunContinuation(
            mode=mode, previous_session_id=previous,
            checkpoint=ResumeCheckpoint.from_payload(checkpoint) if checkpoint is not None else None)
    request = RunRequest(
        identity=RunIdentity(task_id=identity["taskId"], attempt_id=identity["attemptId"],
                             generation=identity["generation"], invocation_id=control["invocationId"],
                             turn_id=identity["turnId"], input_sha256=input_hash(turn_input)),
        harness=control["harness"],
        buddy=RunBuddy(**{key: control["spec"][key]
                                          for key in ("provider", "model", "effort")}),
        cwd=control["cwd"],
        session_record_path=control["sessionRecordPath"],
        private_state=PrivateStatePaths(state_dir=control["stateDir"], invocation_root=str(invocation_root),
                                        native_root=control["nativeRoot"]),
        input_text=prompt, permission=permission,
        output_schema=schema,
        budget=RunBudget(timeout_seconds=control["timeoutSeconds"]),
        continuation=continuation,
        session_services=descriptions,
    )
    error = None
    if format is not None and format.reject_previous_on_initial and mode == "initial" and previous is not None:
        error = BoardError("invalid-resume-mode", f"an initial {format.display_name} turn must not carry a previous session")
    return request, services, worker_observer, error


def _native_services(module, *, inquiry=None):
    """Use the service factory declared by the registered driver."""
    factory = module.prepare_run_services
    return factory(inquiry=inquiry) if factory is not None else None


def fast_request(control: dict, module):
    """Project one no-tool control file into the frozen request and observer."""
    invocation_root = Path(control["privateRoot"])
    request_values = control["noToolRequest"]
    base_prompt = structured_prompt(request_values["prompt"], request_values["outputSchema"])
    request = RunRequest(
        identity=RunIdentity(task_id=control["taskId"], attempt_id=control["attemptId"],
                             generation=control["generation"], invocation_id=control["invocationId"]),
        harness=control["harness"],
        buddy=RunBuddy(**{key: control["spec"][key]
                                          for key in ("provider", "model", "effort")}),
        cwd=control["cwd"],
        session_record_path=control["sessionRecordPath"],
        private_state=PrivateStatePaths(state_dir=control["stateDir"], invocation_root=str(invocation_root),
                                        native_root=control["nativeRoot"]),
        input_text=base_prompt, permission="none",
        output_schema=request_values["outputSchema"], capture_evidence=request_values.get("captureEvidence") is True,
        budget=RunBudget(timeout_seconds=control["timeoutSeconds"]),
    )
    correction = FastCorrection(request_values["outputSchema"], base_prompt)
    return request, _native_services(module), correction.observer, correction


def review_request(control: dict, module):
    values = control["readOnlyRequest"]
    prompt = structured_prompt(values["prompt"], values["outputSchema"])
    request = RunRequest(
        identity=RunIdentity(task_id=control["taskId"], attempt_id=control["attemptId"],
                             generation=control["generation"], invocation_id=control["invocationId"]),
        harness=control["harness"], buddy=RunBuddy(**control["spec"]),
        cwd=control["cwd"],
        session_record_path=control["sessionRecordPath"],
        private_state=PrivateStatePaths(state_dir=control["stateDir"], invocation_root=control["privateRoot"],
                                        native_root=control["nativeRoot"]),
        input_text=prompt, output_schema=values["outputSchema"], permission="read",
        budget=RunBudget(timeout_seconds=control["timeoutSeconds"]),
        capture_evidence=values.get("captureEvidence") is True)
    observer = ReviewObserver(values["budget"]["toolCalls"], values["outputSchema"], prompt,
                              can_correct=control["canCorrect"])
    return request, _native_services(module), observer.observer, observer


def _launch(control: dict, context: ExecutionContext, *, environment: dict,
            identity: RunIdentity, grace_seconds: float = 0) -> InlineRunHandle:
    ensure_private_dir(Path(control["privateRoot"]))
    control.update(stateDir=environment["HMB_STATE_DIR"], invocationId=identity.invocation_id,
                   taskId=identity.task_id, attemptId=identity.attempt_id,
                   generation=identity.generation)
    handle = InlineRunHandle(control, identity, grace_seconds=grace_seconds)
    def run() -> None:
        from .inline_run import execute
        try:
            handle.result, handle.request, handle.verdict, handle.exit_code = execute(
                control, handle)
            handle.role_run_active_identity = handle.request.identity
        except BaseException as error:
            handle.error = error
            handle.exit_code = 1
        finally:
            handle.done.set()
    caller_context = copy_context()
    handle.thread = threading.Thread(target=lambda: caller_context.run(run),
                                     name="hmb-inline-" + identity.invocation_id)
    from ..harnesses import runtime_selection
    caller_context.run(runtime_selection._environment.set, environment)
    handle.thread.start()
    return handle


def _read_run(handle: InlineRunHandle) -> RunResult:
    if handle.error is not None:
        raise handle.error
    return handle.result


def _read_request(handle: InlineRunHandle) -> RunRequest:
    if handle.error is not None:
        raise handle.error
    return handle.request


def read_fast_result(handle: InlineRunHandle) -> dict:
    return _read_structured_result(handle, _fast_result)


def read_review_result(handle: InlineRunHandle) -> dict:
    return _read_structured_result(handle, _review_result)


def _read_structured_result(handle, project):
    result = _read_run(handle)
    return project(result, _read_request(handle), handle.verdict)


def start_fast(module, name: str, context: ExecutionContext, request) -> InlineRunHandle:
    """Run the selected fast role with no turn, tools or blackboard authority."""
    native_environment = context.environment
    cwd = Path(request.cwd)
    module.check_preparation(context.spec, native_environment)
    directory = ensure_private_dir(context.directory)
    invocation_name = "no-tool-" + uuid.uuid4().hex
    invocation = ensure_private_dir(context_root(context, name) / invocation_name)
    request_values = {"prompt": request.prompt, "outputSchema": request.output_schema,
                      "captureEvidence": request.capture_evidence}
    control = {
        "operation": "fast", "harness": name, "privateRoot": str(invocation),
        "directory": str(invocation), "nativeRoot": str(invocation / "native"),
        "sessionRecordPath": context.session_record_path,
        "taskId": context.task_id, "attemptId": context.attempt_id, "generation": context.generation,
        "cwd": str(cwd.resolve()),
        "timeoutSeconds": request.timeout_seconds,
        "spec": {key: context.spec[key] for key in ("provider", "model", "effort")},
        "noToolRequest": request_values,
    }
    identity = RunIdentity(task_id=context.task_id, attempt_id=context.attempt_id,
                           generation=context.generation, invocation_id=uuid.uuid4().hex)
    handle = _launch(control, context, environment=native_environment, identity=identity)
    handle.no_tool = True
    handle.no_tool_evidence = _NoToolEvidence(
        name, context.task_id, context.attempt_id, context.generation, directory,
        invocation, directory / invocation_name, canonical_json(request_values))
    return handle


def start_review(name: str, context: ExecutionContext, request) -> InlineRunHandle:
    """Use the same process owner and run seam over the frozen review mirror."""
    from ..harnesses.registry import adapter

    environment = context.environment
    ensure_private_dir(context.directory)
    private = ensure_private_dir(context_root(context, name) / ("review-" + uuid.uuid4().hex))
    control = {
        "operation": "review", "harness": name, "privateRoot": str(private),
        "directory": str(context.directory),
        "nativeRoot": str(ensure_private_dir(context_root(context, name) / "review-native")),
        "sessionRecordPath": context.session_record_path,
        "cwd": request.cwd, "timeoutSeconds": request.budget["timeoutSeconds"],
        "taskId": context.task_id, "attemptId": context.attempt_id, "generation": context.generation,
        "spec": {key: context.spec[key] for key in ("provider", "model", "effort")},
        "canCorrect": adapter(name).native_resume,
        "readOnlyRequest": {"prompt": request.prompt, "outputSchema": request.output_schema,
                            "budget": request.budget, "captureEvidence": request.capture_evidence},
    }
    identity = RunIdentity(task_id=context.task_id, attempt_id=context.attempt_id,
                           generation=context.generation, invocation_id=uuid.uuid4().hex)
    return _launch(control, context, environment=environment, identity=identity, grace_seconds=10)


class WorkerRunExecutor:
    """The runtime's unchanged role surface over one selected registered run."""

    def __init__(self, description, module):
        from ..harnesses.registry import worker_receipt_options

        self.description = description
        self.module = module
        self.name = description.name
        self.receipt_options = worker_receipt_options(self.name)

    def available(self):
        return self.description.available()

    def prepare(self, context: ExecutionContext) -> None:
        from ..harnesses.registry import worker_format
        format = worker_format(self.name)
        if any(not isinstance(context.spec.get(key), str) or not context.spec[key].strip()
               for key in ("provider", "model", "effort")):
            raise BoardError("INVALID_ARGUMENT", "Coding requires a complete provider, model and effort after routing", harness=self.name)
        self.module.check_preparation(context.spec, context.environment)
        if context.turn_input is None or not context.turn_id:
            raise BoardError("INVALID_ARGUMENT", "Coding requires a micro task turn input", harness=self.name)
        if context.turn_input.get("resumeMode") not in ("initial", "native-session", "reconstructed-new-session"):
            raise BoardError("INVALID_ARGUMENT", "Coding requires an explicit turn resume mode", harness=self.name)
        if context.turn_input.get("resumeMode") == "native-session" and not self.description.native_resume:
            raise BoardError("INVALID_ARGUMENT", "This harness does not support native-session resume", harness=self.name)
        if native_path(context.turn_output_file()).exists():
            raise BoardError("CONFLICT", "The attempt already has a turn result; it cannot execute twice", harness=self.name)
        turn_io.prepare_turn(context)
        state = context.environment["HMB_STATE_DIR"]
        root = ensure_private_dir(native_root(Path(state), self.name, context.task_id)
                                  if self.description.native_resume else
                                  context_root(context, self.name) / (self.name + "-private"))
        inquiry = turn_io.inquiry_paths(context)
        context.role_run_control = {
            "operation": "worker", "harness": self.name,
            "directory": str(resolved_path(context.directory)),
            "privateRoot": str(context_root(context, self.name)), "nativeRoot": str(root),
            "sessionRecordPath": context.session_record_path,
            "cwd": str(Path(context.cwd).resolve()), "timeoutSeconds": context.timeout_seconds,
            "inputFile": str(context.turn_input_file()), "outputFile": str(context.turn_output_file()),
            "taskFile": str(context.task_file()),
            "inquiry": {key: inquiry[key] for key in ("resultsPath",)},
            "credentialFile": str(context.credential_file()) if context.agent_credential else None,
            "spec": {key: context.spec[key] for key in ("provider", "model", "effort")},
        }

    def start(self, context: ExecutionContext) -> InlineRunHandle:
        control = context.role_run_control
        invocation_id = uuid.uuid4().hex
        control["privateRoot"] = str(ensure_private_dir(
            context_root(context, self.name) / ("run-" + invocation_id)))
        identity = RunIdentity(task_id=context.task_id, attempt_id=context.attempt_id,
                               generation=context.generation, invocation_id=invocation_id,
                               turn_id=context.turn_id, input_sha256=input_hash(context.turn_input))
        return _launch(control, context, environment=context.environment, identity=identity)

    def collect(self, handle: InlineRunHandle, context: ExecutionContext) -> AdapterOutcome:
        result, exit_code, shutdown = _read_run(handle), handle.exit_code, handle.shutdown_confirmed() is True
        payload = _base_result(result)
        verdict = None
        wrap_up_result = result.identity != handle.role_run_original_identity
        try:
            request = _read_request(handle)
            payload = _worker_facts(result, request, delegation_authority=(context.turn_input or {}).get(
                "context", {}).get("delegationAuthority") is True)
            reports_invalid = _worker_reports(result, payload)
            if reports_invalid:
                raise BoardError("INVALID_ARGUMENT", "A native report could not be verified")
            if shutdown:
                if result.end.status == "ok":
                    for kind in ("turn-provenance", "native-turn-facts"):
                        _evidence(result, kind)
                active_control = handle.role_run_active_control
                if (active_control is not handle.role_run_control
                        and (request.continuation is None
                             or result.native_identity is None
                             or result.native_identity.session_id != request.continuation.previous_session_id)):
                    raise BoardError("INVALID_ARGUMENT", "The summary run changed its native session")
                if result.end.status == "ok" or handle.cancel_requested or wrap_up_result:
                    verdict = handle.verdict
                    error = verdict.get("workerError")
                    if error is not None:
                        raise BoardError(error["code"], error["message"])
        except (OSError, ValueError, BoardError, RecursionError) as problem:
            role_invalid = isinstance(problem, BoardError) and problem.code == "invalid-result"
            payload.update(status="error", code="invalid-result" if role_invalid else "invalid-role-result",
                           error=problem.message if role_invalid else "The run's role evidence could not be collected")
        if verdict is not None:
            payload["wrapUpDelivered"] = verdict["wrapUpDelivered"]
            payload["graceExpired"] = verdict["graceExpired"]
        checkpoint = turn_io.validated_checkpoint(payload, context.turn_input) if shutdown else None
        if checkpoint is None:
            payload.pop("nativeCheckpoint", None)
            if any(ref.kind == "native-checkpoint" for ref in result.evidence_refs):
                payload["lastAssistantMessage"] = None
        payload["tokenUsage"] = usage.normalize_token_usage(payload.get("tokenUsage"))
        from ..harnesses.registry import worker_message_source
        payload["lastAssistantMessage"] = usage.normalize_last_assistant_message(
            payload.get("lastAssistantMessage"), source=worker_message_source(self.name))
        from ..harnesses.registry import worker_format
        format = worker_format(self.name)
        status = "cancelled" if (handle.cancel_requested or payload.get("status") == "cancelled") and shutdown else "failed"
        if not handle.cancel_requested and exit_code == 0 and payload.get("status") == "ok" and shutdown:
            status = "ok"
        if wrap_up_result and not payload.get("wrapUpDelivered"):
            record, error = None, "the wrap-up did not deliver a valid result within its deadline"
        elif exit_code != 0:
            record, error = None, "the runner did not exit zero"
        elif not shutdown:
            record, error = None, "runner shutdown is not confirmed, so no turn outcome is imported"
        else:
            record = handle.turn_record
            error = None if record is not None else "the run delivered no turn outcome"
        attention = payload.get("nativeAttention") if isinstance(payload.get("nativeAttention"), dict) else {}
        attention_requests = attention.get("requests")
        if format is None:
            payload["attentionRequired"] = (attention_requests + (attention.get("resultDenials") or 0) > 0
                                            if type(attention_requests) is int and attention_requests >= 0 else None)
        elif format.interaction_kind == "permissions":
            payload["attentionRequired"] = bool((attention_requests or 0) + (attention.get("resultDenials") or 0))
        if payload.get("attentionRequired") is True and record is not None and record["outcome"]["disposition"] in ("completed", "waiting"):
            error = ("a native interactive request was refused during this turn; a completed or waiting outcome "
                     "cannot stand in for the Host attention that request requires")
        original_completed = (payload.get("status") == "ok"
                              and result.identity == handle.role_run_original_identity and exit_code == 0 and shutdown
                              and record is not None and error is None)
        if original_completed:
            status = "ok"
        seal_error = None
        payload["turnResultPath"] = str(context.turn_output_file())
        session_id = payload.get("sessionId") or (record or {}).get("sessionId")
        if self.receipt_options.capture_session_from_turn:
            # This registered projection takes its session from the delivered
            # record. The service validates that record in workflow._validate_turn.
            session_id = (record or {}).get("sessionId")
        facts = self.module.session_facts(Path(handle.role_run_control["nativeRoot"]), session_id)
        payload["nativeSession"] = {**facts, "resumeMode": context.turn_input.get("resumeMode"),
            "resumable": bool(facts.get("bindingPresent", True) and shutdown
                              and result.continuation is not None and result.continuation.resumable is True
                              and (record is not None or checkpoint is not None
                                   and turn_io.checkpoint_resumable(payload, checkpoint)))}
        if self.receipt_options.capture_session_from_turn:
            payload["nativeSession"]["sessionIdSource"] = "turn-record" if session_id else "none"
        payload["workspaceManifest"] = context.effective_workspace
        if error:
            payload["turnError"] = error
            if status == "ok":
                status = "failed"
        elif record is not None and status in ("ok", "cancelled"):
            payload["turn"] = record
            if status == "ok":
                seal, seal_error = turn_io.seal_workspace(context)
                if seal_error:
                    payload["workspaceSealError"] = seal_error
                    status = "failed"
                elif seal:
                    payload["workspaceSeal"] = seal
        return AdapterOutcome(status=status, result=payload, error=payload.get("error") or error or seal_error,
                              exit_code=exit_code, signal=signal_name(exit_code), shutdown_confirmed=shutdown,
                              artifacts=_artifacts(context) if shutdown else [])

    def stop(self, handle: InlineRunHandle, *, stop_initiator: str,
             grace_deadline: float | None) -> None:
        if (stop_initiator in WRAP_UP_INITIATORS and grace_deadline is not None
                and self.description.interrupted_resume):
            from .controller import start_wrap_up
            start_wrap_up(handle, grace_deadline, stop_initiator=stop_initiator)
        else:
            handle.force_stop_requested = True
            self.cancel(handle)

    def cancel(self, handle: InlineRunHandle, *, grace_seconds: float = WORKER_STOP_GRACE_SECONDS) -> None:
        handle.terminate(grace_seconds=grace_seconds)

def _artifacts(context: ExecutionContext) -> list[dict]:
    paths = [("task-specification", context.task_file()), ("turn-result", context.turn_output_file()),
             ("native-attention", context.directory / "attention.json")]
    artifacts = []
    for kind, path in paths:
        if native_path(path).is_file():
            raw = read_bytes(path)
            artifacts.append({"kind": kind, "location": str(resolved_path(path)), "sizeBytes": native_path(path).stat().st_size,
                              "contentHash": hashlib.sha256(raw).hexdigest()})
    return artifacts


def discover_models(name: str, *, state_dir: Path) -> dict:
    return _native_operation(name, "discover", state_dir=state_dir)


def delete_sessions(name: str, session_ids: list[str], *, state_dir: Path) -> dict[str, str]:
    """Delete proven native sessions, validating the controller receipt once."""
    sessions = _native_operation(name, "delete-sessions", state_dir=state_dir, session_ids=session_ids)
    if (not isinstance(sessions, dict) or sessions.keys() != set(session_ids)
            or any(value not in ("deleted", "already-gone") for value in sessions.values())):
        raise BoardError("INVALID_PROTOCOL", "The native session deletion receipt does not match the request",
                         harness=name)
    return sessions


def _native_operation(name: str, operation: str, *, state_dir: Path, **arguments) -> dict:
    from ..harnesses.registry import native_operation
    from ..harnesses.runtime_selection import controller_environment

    specification = NATIVE_OPERATIONS[operation]
    directory = None
    stopped = True  # No controller exists during control-file preparation.
    try:
        try:
            native_operation(name, operation)
            directory = make_temporary_dir(prefix=f"hey-my-buddy-{name}-{specification['resultKey']}-",
                directory=ensure_private_dir(state_dir / "tmp"))
            control = directory / _NATIVE_CONTROL
            private_json(control, {"operation": operation, "arguments": arguments,
                                   "harness": name,
                                   "privateRoot": str(directory), "nativeRoot": str(directory / "native"),
                                   "cwd": str(directory), "timeoutSeconds": 25})
            environment = controller_environment(directory)
            environment["HMB_STATE_DIR"] = str(state_dir)
            stopped = False  # Ownership passes to the launch boundary.
            try:
                handle = launch_controller(
                    stop_channel="stdin",
                    prepare=lambda: (
                        [sys.executable, "-m", "hey_my_buddy.buddy.roles.run_controller", "--control", str(control)],
                        None, environment),
                    log_paths={"stdout": str(directory / "stdout"), "stderr": str(directory / "stderr")})
            except Exception as error:
                creation = error.owned_popen_evidence
                if creation.created:
                    stopped = stop_confirmed(read_strict_result(directory / "stdout"),
                                             lambda: creation.shutdown_confirmed)
                else:
                    stopped = creation.shutdown_confirmed
                raise
            wait_expired = handle.wait(30) is None
            # A completed root may still leave members in its owned group/Job.
            # Stop that boundary as well before consuming the native receipt.
            handle.terminate(grace_seconds=8 if wait_expired else 0)
            collection = collect_controller(handle, read=read_strict_result,
                                            stop=lambda payload, owned: stop_confirmed(payload, owned.shutdown_confirmed))
            stopped = collection.stop_confirmed
            if not stopped:
                raise BoardError(specification["stopUnconfirmedCode"],
                                 f'The native {operation} process stop is unconfirmed', harness=name)
            payload = collection.payload
            if not isinstance(payload, dict) and not (wait_expired and specification["allowUnknown"]):
                raise BoardError("HARNESS_UNAVAILABLE", "The native operation receipt must be an object", harness=name)
            if specification["allowUnknown"] and (wait_expired or payload.get("status") == "unknown"):
                return {"source": "native-discovery", "providers": [], "discoveries": [{
                    "harness": name, "status": "unknown", "accountStatus": "unknown",
                    "reason": {"code": "native-metadata-wait-expired", "params": {}}}]}
            if wait_expired or collection.exit_code != 0 or payload.get("status") != "ok":
                message = (collection.payload or {}).get("error")
                if not isinstance(message, str) or not message:
                    message = f"Native {operation} did not settle successfully"
                raise BoardError("HARNESS_UNAVAILABLE", message, harness=name,
                                 **({"nativeError": payload["nativeError"]} if "nativeError" in payload else {}))
            return payload.get(specification["resultKey"])
        finally:
            if stopped and directory is not None:
                shutil.rmtree(native_path(directory))
    except Exception as error:
        error.discovery_shutdown_confirmed = stopped
        raise
