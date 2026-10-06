"""Role execution over registered runs, using the shared controller and handles.

Worker prompts, result publication and the fast verdict live here. Native
service naming, provenance, storage and discovery come only from the registry.
"""
from __future__ import annotations

import hashlib
import math
import os
import shutil
import sys
import tempfile
import uuid
from pathlib import Path

from ...errors import BoardError
from ...json_codec import decode_strict_json
from ...private_dirs import context_root, native_root, ensure_private_dir
from ...protocol import usage
from ..harnesses.base import AdapterOutcome, ExecutionContext, ProcessHandle
from ..harnesses.controller import (
    STRICT_RESULT_BYTES, collect_controller, launch_controller, read_strict_result,
    signal_name, stop_confirmed,
)
from ..harnesses.run_contract import (
    MAX_RUN_REQUEST_BYTES, MAX_RUN_RESULT_BYTES, PrivateStatePaths,
    RunBudget, RunConfiguration, RunContinuation, RunIdentity, RunRequest, RunResult,
    decode_run_request, decode_run_result,
)
from . import turn_io, worker_services
from .run_observers import FastCorrection, worker_observer
from .structured_call import (
    _NoToolEvidence, _account_environment, no_tool_prompt, valid_answer,
)
from .turn_io import input_hash, private_json, validate_outcome, guard_private_path, canonical_json

_STRICT_RESULT_BYTES = STRICT_RESULT_BYTES
_CONTROL = "role-run-control.json"
_REQUEST = "role-run-request.json"
_VERDICT = "role-run-verdict.json"


def _evidence(result, kind: str) -> dict | None:
    for ref in result.evidence_refs:
        if ref.kind == kind:
            with guard_private_path(Path(ref.location)).open("rb") as stream:
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
    """The legacy receipt's common head, projected from the common result."""
    payload = {
        "status": result.end.status, "mode": result.harness,
        "harnessVersion": result.harness_version or "unknown",
        "requested": result.configuration.requested.to_payload() if result.configuration.requested else None,
        # The legacy receipts carry ``observed`` as a None placeholder; the
        # native readback is the checked block, never a look-alike observed
        # value, so this key stays a projection constant.
        "resolved": None, "observed": None,
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


def _fast_result(result, request: RunRequest, verdict: dict) -> dict:
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
        payload["answerValid"] = valid_answer(raw, request.output_schema.value)
        payload["correctionCount"] = result.value.correction_count if result.value else 0
        if result.native_identity is not None:
            payload["nativeIdentity"] = result.native_identity.to_payload()
        payload["nativeEventCount"] = result.native_event_count
        payload["zeroToolVerified"] = calls == 0
        if request.capture_evidence:
            from ..harnesses.registry import run_seam
            module = run_seam(result.harness)
            if module is None or not callable(getattr(module, "native_evidence", None)):
                raise BoardError("ROLE_RUN_UNREGISTERED", "The registered run has no native evidence projection")
            payload["nativeEvidence"] = module.native_evidence(result)
    else:
        payload.pop("zeroToolVerified", None)
    if tools:
        payload["toolEvidence"] = tools
    return payload


def _worker_facts(result) -> dict:
    payload = _base_result(result)
    payload["tokenUsage"] = result.usage.value if result.usage is not None else None
    payload["lastAssistantMessage"] = (result.last_assistant_message.value
                                       if result.last_assistant_message is not None else None)
    if result.native_error is not None:
        payload["nativeFailure"] = result.native_error.value
    if result.native_failure is not None:
        payload["quotaFailure"] = result.native_failure.value
    if result.activity is not None:
        activity = result.activity.value
        payload["activity"] = {"published": True, "phase": activity.get("phase"),
                               "eventSeq": activity.get("eventSeq")}
    return payload


def _worker_reports(result, payload: dict) -> bool:
    """Keep each validated report even when another report or delivery fails."""
    invalid = False
    for kind, key in (("inquiry-report", "inquiry"), ("attention-report", "nativeAttention")):
        try:
            report = _evidence(result, kind)
        except (OSError, ValueError, BoardError, RecursionError):
            invalid = True
        else:
            if report is not None:
                payload[key] = report
    return invalid


def _worker_result(result, control: dict, turn_input: dict, prompt: str, payload: dict) -> dict:
    if result.end.status == "ok":
        completion = result.completion_evidence
        if completion is not None and completion.native_identity is not None:
            payload["nativeTurnId"] = completion.native_identity.turn_id
        provenance = _evidence(result, "turn-provenance")
        outcome = result.value.parsed.value if result.value is not None and result.value.parsed else None
        if (result.value is None or result.value.schema_status != "valid"
                or validate_outcome(outcome) is not None or not isinstance(provenance, dict)):
            raise BoardError("INVALID_ARGUMENT", "The Worker run did not deliver its valid outcome and provenance")
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
            if not Path(control["outputFile"]).exists():
                private_json(Path(control["outputFile"]), record, exclusive=True)
            payload["nativeTurnId"] = provenance.get("nativeTurnId")
    return payload


def worker_request(control: dict, module):
    """Project one governed control file into the frozen request and services."""
    invocation_root = Path(control["privateRoot"])
    turn_input = decode_strict_json(Path(control["inputFile"]).read_bytes())
    if not isinstance(turn_input, dict):
        raise BoardError("INVALID_ARGUMENT", "the governed input is not an object")
    identity = {key: turn_input[key] for key in ("taskId", "attemptId", "generation", "turnId")}
    inquiry = control.get("inquiry") if isinstance(control.get("inquiry"), dict) else None
    attention_path = Path(control["directory"]) / "attention.json"
    binding = module.prepare_services(
        invocation_root=invocation_root, identity=identity, input_sha256=input_hash(turn_input),
        attention_path=attention_path,
        session_tools=worker_services.session_tools(), completion_tool="buddy_finish_turn",
        inquiry=inquiry, inquiry_tools=("buddy_checkpoint", "buddy_answer_inquiry"),
        validate_outcome=validate_outcome, activity_dir=control["directory"],
        native_stderr=str(Path(control["directory"]) / "native.stderr.log"))
    task_text = Path(control["taskFile"]).read_text()
    prompt = worker_services.governed_prompt(
        task_text, turn_input, binding.completion_tool,
        checkpoint_tool=binding.checkpoint_tool, answer_tool=binding.answer_tool)
    mode = turn_input.get("resumeMode")
    previous = turn_input.get("previousSessionId")
    continuation = None
    if mode in ("native-session", "reconstructed-new-session"):
        continuation = RunContinuation(mode=mode, previous_session_id=previous)
    request = RunRequest(
        identity=RunIdentity(task_id=identity["taskId"], attempt_id=identity["attemptId"],
                             generation=identity["generation"], invocation_id=control["invocationId"],
                             turn_id=identity["turnId"], input_sha256=input_hash(turn_input)),
        harness=control["harness"],
        configuration=RunConfiguration(**{key: control["spec"][key]
                                          for key in ("provider", "model", "effort")}),
        cwd=control["cwd"],
        private_state=PrivateStatePaths(invocation_root=str(invocation_root),
                                        native_root=control["nativeRoot"]),
        input_text=prompt, tool_scope="write",
        output_schema=worker_services.OUTCOME_SCHEMA,
        budget=RunBudget(timeout_seconds=control["timeoutSeconds"]),
        continuation=continuation,
        session_services=(binding.description,),
    )
    return request, binding.services, worker_observer


def fast_request(control: dict):
    """Project one no-tool control file into the frozen request and observer."""
    invocation_root = Path(control["privateRoot"])
    request_values = control["noToolRequest"]
    base_prompt = no_tool_prompt(request_values["prompt"], request_values["outputSchema"])
    request = RunRequest(
        identity=RunIdentity(task_id=control["taskId"], attempt_id=control["attemptId"],
                             generation=control["generation"], invocation_id=control["invocationId"]),
        harness=control["harness"],
        configuration=RunConfiguration(**{key: control["spec"][key]
                                          for key in ("provider", "model", "effort")}),
        cwd=control["cwd"],
        private_state=PrivateStatePaths(invocation_root=str(invocation_root),
                                        native_root=control["nativeRoot"]),
        input_text=base_prompt, tool_scope="none",
        output_schema=request_values["outputSchema"], capture_evidence=request_values.get("captureEvidence") is True,
        budget=RunBudget(timeout_seconds=control["timeoutSeconds"]),
    )
    correction = FastCorrection(request_values["outputSchema"], base_prompt)
    return request, None, correction.observer, correction


def _launch(control: dict, context: ExecutionContext, *, environment: dict,
            identity: RunIdentity) -> ProcessHandle:
    from ..harnesses.runtime_selection import controller_environment

    root = ensure_private_dir(Path(control["privateRoot"]))
    control.update(invocationId=identity.invocation_id, requestFile=str(root / _REQUEST),
                   verdictFile=str(root / _VERDICT))
    path = root / _CONTROL
    private_json(path, control)
    handle = launch_controller(
        prepare=lambda _environment: (
            [sys.executable, "-m", "hey_my_buddy.buddy.roles.run_controller", "--control", str(path)],
            control["cwd"], controller_environment(context.directory, environment,
                                                  read_only=control["operation"] == "fast")),
        log_paths=context.log_paths(), timeout_seconds=control["timeoutSeconds"],
        unbounded_deadline=math.inf)
    # These bindings are held by the process owner, independent of mutable
    # control files and native evidence written during the run.
    handle.role_run_control = decode_strict_json(canonical_json(control))
    handle.role_run_identity = identity
    return handle


def _read_frame(path: Path, maximum: int, decode):
    with guard_private_path(path).open("rb") as stream:
        return decode(stream.read(maximum + 1))


def _read_run(handle: ProcessHandle, path: Path) -> RunResult | None:
    try:
        result = _read_frame(path, MAX_RUN_RESULT_BYTES, decode_run_result)
        if result.identity != handle.role_run_identity or result.harness != handle.role_run_control["harness"]:
            return None
        return result
    except (OSError, ValueError, BoardError, RecursionError):
        return None


def _read_request(handle: ProcessHandle) -> RunRequest:
    request = _read_frame(Path(handle.role_run_control["requestFile"]),
                          MAX_RUN_REQUEST_BYTES, decode_run_request)
    if request.identity != handle.role_run_identity or request.harness != handle.role_run_control["harness"]:
        raise BoardError("INVALID_ARGUMENT", "The run request does not match its owned invocation")
    return request


def _role_stop(result: RunResult | None, handle: ProcessHandle) -> bool:
    return bool(result is not None and result.stop_evidence.native.group_state == "gone"
                and handle.shutdown_confirmed() is True)


def read_fast_result(handle: ProcessHandle, path: Path) -> dict:
    result = _read_run(handle, path)
    if result is None:
        return {"status": "error", "code": "invalid-native-result"}
    try:
        request = _read_request(handle)
        verdict = read_strict_result(Path(handle.role_run_control["verdictFile"]))
        if (not isinstance(verdict, dict) or set(verdict) != {"stopReason", "elapsedMs"}
                or type(verdict.get("elapsedMs")) is not int or verdict["elapsedMs"] < 0
                or verdict.get("stopReason") not in (None, "no-tool-violation", "invalid-protocol")):
            raise BoardError("INVALID_ARGUMENT", "The fast role verdict is missing")
        return _fast_result(result, request, verdict)
    except (OSError, ValueError, BoardError, RecursionError):
        return {**_base_result(result),
                "status": "error", "code": "invalid-native-result"}


def start_fast(module, name: str, context: ExecutionContext, request) -> ProcessHandle:
    """Run the selected fast role with no turn, tools or blackboard authority."""
    native_environment = _account_environment(name, context, purpose="router")
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
    module.check_preparation(context.spec, native_environment)
    directory = ensure_private_dir(context.directory)
    invocation_name = "no-tool-" + uuid.uuid4().hex
    invocation = ensure_private_dir(context_root(context, name) / invocation_name)
    request_values = {"prompt": request.prompt, "outputSchema": request.output_schema,
                      "captureEvidence": request.capture_evidence}
    control = {
        "operation": "fast", "harness": name, "privateRoot": str(invocation),
        "directory": str(invocation), "nativeRoot": str(invocation / "native"),
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


class WorkerRunExecutor:
    """The runtime's unchanged role surface over one selected registered run."""

    def __init__(self, description, module):
        self.description = description
        self.module = module
        self.name = description.name

    def available(self):
        return self.description.available()

    def prepare(self, context: ExecutionContext) -> None:
        context.private_adapter = self.name
        if any(not isinstance(context.spec.get(key), str) or not context.spec[key].strip()
               for key in ("provider", "model", "effort")):
            raise BoardError("INVALID_ARGUMENT", "Coding requires a complete provider, model and effort after routing", adapter=self.name)
        self.module.check_preparation(context.spec, context.environment)
        if context.turn_input is None or not context.turn_id:
            raise BoardError("INVALID_ARGUMENT", "Coding requires a governed turn input", adapter=self.name)
        if context.turn_input.get("resumeMode") not in ("initial", "native-session", "reconstructed-new-session"):
            raise BoardError("INVALID_ARGUMENT", "Coding requires an explicit turn resume mode", adapter=self.name)
        state = context.environment.get("BUDDY_STATE_DIR")
        if not state:
            raise BoardError("INVALID_ARGUMENT", "Coding requires the owning private state directory", adapter=self.name)
        if context.turn_output_file().exists():
            raise BoardError("CONFLICT", "The attempt already has a turn result; it cannot execute twice", adapter=self.name)
        turn_io.prepare_turn(context)
        root = ensure_private_dir(native_root(Path(state), self.name, context.task_id))
        inquiry = turn_io.inquiry_paths(context)
        private_json(ensure_private_dir(context_root(context, self.name)) / _CONTROL, {
            "operation": "worker", "harness": self.name,
            "directory": str(context.directory.resolve()),
            "privateRoot": str(context_root(context, self.name)), "nativeRoot": str(root),
            "cwd": str(Path(turn_io.workspace_cwd(context)).resolve()), "timeoutSeconds": context.timeout_seconds,
            "inputFile": str(context.turn_input_file()), "outputFile": str(context.turn_output_file()),
            "taskFile": str(context.task_file()),
            "inquiry": {key: inquiry[key] for key in ("socketPath", "resultsPath", "errorPath", "token")},
            "spec": {key: context.spec[key] for key in ("provider", "model", "effort")},
        })

    def start(self, context: ExecutionContext) -> ProcessHandle:
        self.prepare(context)
        control = read_strict_result(context_root(context, self.name) / _CONTROL)
        identity = RunIdentity(task_id=context.task_id, attempt_id=context.attempt_id,
                               generation=context.generation, invocation_id=uuid.uuid4().hex,
                               turn_id=context.turn_id, input_sha256=input_hash(context.turn_input))
        return _launch(control, context, environment=context.environment, identity=identity)

    def collect(self, handle: ProcessHandle, context: ExecutionContext) -> AdapterOutcome:
        collection = collect_controller(handle, read=lambda path: _read_run(handle, path), stop=_role_stop)
        result, exit_code, shutdown = collection.payload, collection.exit_code, collection.stop_confirmed
        payload = {"status": "invalid-result", "error": "the role controller produced no complete run result"}
        if result is not None:
            payload = _worker_facts(result)
            reports_invalid = _worker_reports(result, payload)
            try:
                request = _read_request(handle)
                if reports_invalid:
                    raise BoardError("INVALID_ARGUMENT", "A native report could not be verified")
                if shutdown:
                    _worker_result(result, handle.role_run_control, context.turn_input, request.input_text, payload)
            except (OSError, ValueError, BoardError, RecursionError):
                payload.update(status="error", code="invalid-role-result",
                               error="The run's role evidence could not be collected")
        for key, normalizer in (("tokenUsage", usage.normalize_token_usage), ("quota", usage.normalize_quota),
                                ("quotaFailure", usage.normalize_quota_failure)):
            payload[key] = normalizer(payload.get(key))
        payload["lastAssistantMessage"] = usage.normalize_last_assistant_message(
            payload.get("lastAssistantMessage"), source=self.name + "/session-root-assistant-message")
        status = "cancelled" if (handle.cancel_requested or payload.get("status") == "cancelled") and shutdown else "failed"
        if not handle.cancel_requested and exit_code == 0 and payload.get("status") == "ok" and shutdown:
            status = "ok"
        record, error = turn_io.read_turn(context, shutdown, exit_code, self.module.validate_turn_provenance)
        attention = payload.get("nativeAttention") if isinstance(payload.get("nativeAttention"), dict) else {}
        attention_requests = attention.get("requests")
        payload["attentionRequired"] = (attention_requests > 0
                                        if type(attention_requests) is int and attention_requests >= 0 else None)
        if payload["attentionRequired"] is True and record is not None and record["outcome"]["disposition"] == "completed":
            error = ("a native interactive request was refused during this turn; a completed outcome "
                     "cannot stand in for the Host attention that request requires")
        seal_error = None
        payload["turnResultPath"] = str(context.turn_output_file())
        session_id = payload.get("sessionId") or (record or {}).get("sessionId")
        facts = self.module.session_facts(Path(handle.role_run_control["nativeRoot"]), session_id)
        payload["nativeSession"] = {**facts, "resumeMode": context.turn_input.get("resumeMode"),
            "resumable": bool(facts["bindingPresent"] and shutdown
                              and record is not None and result is not None
                              and result.continuation is not None and result.continuation.resumable is True)}
        if isinstance(getattr(context, "effective_workspace", None), dict):
            payload["workspaceManifest"] = context.effective_workspace
        if error:
            payload["turnError"] = error
            if status == "ok":
                status = "failed"
        elif record is not None and status == "ok":
            payload["turn"] = record
            seal, seal_error = turn_io.seal_workspace(context)
            if seal_error:
                payload["workspaceSealError"] = seal_error
                status = "failed"
            elif seal:
                payload["workspaceSeal"] = seal
        return AdapterOutcome(status=status, result=payload, error=payload.get("error") or error or seal_error,
                              exit_code=exit_code, signal=signal_name(exit_code), shutdown_confirmed=shutdown,
                              artifacts=_artifacts(context, handle) if shutdown else [])

    def cancel(self, handle: ProcessHandle, *, grace_seconds: float = 8.0) -> None:
        handle.terminate(grace_seconds=grace_seconds)

def _artifacts(context: ExecutionContext, handle: ProcessHandle) -> list[dict]:
    paths = [("task-specification", context.task_file()), ("turn-result", context.turn_output_file()),
             ("native-attention", context.directory / "attention.json"),
             *(("runner-" + key, Path(value)) for key, value in handle.log_paths.items() if key in ("stdout", "stderr"))]
    return [{"kind": kind, "location": str(path.resolve()), "sizeBytes": path.stat().st_size,
             "contentHash": hashlib.sha256(path.read_bytes()).hexdigest()}
            for kind, path in paths if path.is_file()]


def discover_models(name: str) -> dict:
    from ..harnesses.registry import run_seam
    from ..harnesses.runtime_selection import controller_environment

    module = run_seam(name)
    if module is None or not callable(getattr(module, "run_discovery", None)):
        raise BoardError("CATALOG_UNAVAILABLE", "The harness has no registered discovery operation", adapter=name)
    directory = Path(tempfile.mkdtemp(prefix="buddy-" + name + "-catalog-"))
    stopped = False
    try:
        control = directory / _CONTROL
        private_json(control, {"operation": "discover", "harness": name,
                               "privateRoot": str(directory), "nativeRoot": str(directory / "native"),
                               "cwd": str(directory), "timeoutSeconds": 25})
        handle = launch_controller(
            prepare=lambda _environment: (
                [sys.executable, "-m", "hey_my_buddy.buddy.roles.run_controller", "--control", str(control)],
                None, controller_environment(directory)),
            log_paths={"stdout": str(directory / "stdout"), "stderr": str(directory / "stderr")})
        if handle.wait(30) is None:
            handle.terminate(grace_seconds=8)
        collection = collect_controller(handle, read=read_strict_result, stop=stop_confirmed)
        stopped = collection.stop_confirmed
        if not collection.payload or collection.exit_code != 0 or collection.payload.get("status") != "ok" or not stopped:
            raise BoardError("ADAPTER_UNAVAILABLE", "Native model discovery did not settle successfully", adapter=name)
        return collection.payload["catalog"]
    finally:
        if stopped:
            shutil.rmtree(directory)
