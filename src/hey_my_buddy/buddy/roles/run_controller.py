"""The single Python process entry for roles using a registered harness run."""
from __future__ import annotations

import signal
import threading
import math
import uuid

_cancelled = threading.Event()
_signal_generation = 0


def _interrupt(_sig, _frame):
    global _signal_generation
    _signal_generation += 1
    _cancelled.set()


if __name__ == "__main__":
    for sig in (signal.SIGTERM, signal.SIGINT,
                *((signal.SIGBREAK,) if hasattr(signal, "SIGBREAK") else ())):
        signal.signal(sig, _interrupt)

import argparse
import hashlib
import os
from contextlib import contextmanager
from dataclasses import replace
import sys
import time
from pathlib import Path

from ...errors import BoardError
from ...protocol.contracts import HarnessRunLive
from ..harnesses.c_two_live import CTwoLiveEndpoint, write_ready_material
from ..harnesses.live import EXISTING_CAPABILITIES, LiveCapabilities
from ...json_codec import canonical_json, decode_strict_json
from ..harnesses.registry import adapter, run_seam
from ..harnesses.run_contract import (
    EvidenceRef, ResultBuddy, RunEnd, RunResult, StopEvidence, StopLayer,
    ResumeCheckpoint, RunBudget, RunContinuation, decode_run_request, encode_run_request, encode_run_result,
)
from . import run_execution
from .controller import run_harness
from .turn_io import _private_bytes, guard_private_path, private_json


@contextmanager
def _controller_live(control, request, services, state_dir: Path):
    material = control.get("live")
    if material is None:
        yield services
        return
    capabilities = (EXISTING_CAPABILITIES[request.harness] if getattr(services, "inquiry", None)
                    else LiveCapabilities(inquiry_delivery="unsupported"))
    endpoint = CTwoLiveEndpoint(request.identity, capabilities, HarnessRunLive,
                               instance_id=material["instanceId"], token=material["token"],
                               state_dir=state_dir)
    try:
        descriptor = endpoint.start()
        write_ready_material(material["readyFile"], descriptor)
        yield replace(services, live=endpoint) if services is not None else None
    finally:
        endpoint.stop()


def _host_stop(control: dict, request) -> float | None:
    """Accept only this invocation's private Host request with an absolute deadline."""
    path = Path(control["privateRoot"]) / run_execution._STOP_CONTROL
    try:
        value = decode_strict_json(guard_private_path(path).read_bytes())
    except (OSError, ValueError, BoardError):
        return None
    if (not isinstance(value, dict) or set(value) != {"kind", "stopInitiator", "graceDeadline", "binding"}
            or value["kind"] != "host-graceful-stop" or value["stopInitiator"] != "host"
            or value["binding"] != request.identity.to_payload()
            or type(value["graceDeadline"]) not in (float, int)
            or not math.isfinite(value["graceDeadline"])):
        return None
    return float(value["graceDeadline"])


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


def _summary_run(control, request, result, module, state_dir, deadline):
    """Resume one stopped native session for a bounded, structured work summary."""
    from ...private_dirs import ensure_private_dir
    from ..harnesses.registry import worker_format
    identity = result.native_identity
    if (result.identity != request.identity or result.harness != request.harness
            or identity is None or not identity.session_id
            or result.stop_evidence.native.group_state != "gone"
            or result.continuation is None or result.continuation.resumable is not True
            or time.monotonic() >= deadline):
        return None
    checkpoint = _stop_checkpoint(result, request)
    if request.harness == "codex" and checkpoint is None:
        return None
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        return None
    root = ensure_private_dir(Path(control["privateRoot"]) / "stop-summary")
    task_path = root / "summary-task.txt"
    _private_bytes(task_path, (
        "The Host has stopped this micro task. Resume this exact session only to report the work already done, "
        "what remains, decisions, and artifacts. Do not continue implementation. Deliver the normal structured "
        "Worker outcome promptly. State that this is a stopped partial result; do not claim Host acceptance."
    ).encode(), exclusive=True)
    next_control = {**control, "privateRoot": str(root), "taskFile": str(task_path),
                    "requestFile": str(root / run_execution._REQUEST),
                    "verdictFile": str(root / run_execution._VERDICT),
                    "invocationId": uuid.uuid4().hex,
                    "timeoutSeconds": min(86400, math.ceil(remaining)),
                    "live": {"instanceId": uuid.uuid4().hex + uuid.uuid4().hex,
                             "token": os.urandom(32).hex(), "readyFile": str(root / "live-ready.json")}}
    next_request, services, observer, error = run_execution.worker_request(next_control, module)
    if error is not None:
        return None
    next_request = next_request.model_copy(update={
        "continuation": RunContinuation(mode="native-session", previous_session_id=identity.session_id,
                                        checkpoint=checkpoint),
        "budget": RunBudget(timeout_seconds=next_control["timeoutSeconds"]),
    })
    private_json(Path(control["privateRoot"]) / run_execution._STOP_SUMMARY,
                 {"parent": request.identity.to_payload(), "identity": next_request.identity.to_payload(),
                  "control": next_control}, exclusive=True)
    request_path = guard_private_path(Path(next_control["requestFile"]))
    _private_bytes(request_path, encode_run_request(next_request).encode(), exclusive=True)
    next_request = decode_run_request(request_path.read_bytes())
    generation = _signal_generation
    def summary_cancelled():
        return (time.monotonic() >= deadline or _signal_generation != generation
                or _host_stop(control, request) != deadline)
    if summary_cancelled():
        return None
    try:
        with _controller_live(next_control, next_request, services, state_dir) as live_services:
            if summary_cancelled():
                return None
            summary = run_harness(module, next_request, observer=observer, services=live_services,
                                  cancelled=summary_cancelled)
    except Exception:
        # A second native invocation may have spawned before the exception.
        # The first run's gone-group receipt cannot describe that new owner.
        summary = RunResult(identity=next_request.identity, harness=next_request.harness,
            end=RunEnd(status="unknown", reason_code="native-summary-unverified",
                       message="The summary run produced no verified stop receipt"),
            buddy=ResultBuddy(requested=next_request.buddy),
            stop_evidence=StopEvidence(native=StopLayer(group_state="unknown")))
        return next_control, next_request, summary, False
    if (time.monotonic() >= deadline or summary_cancelled()
            or summary.identity != next_request.identity or summary.harness != request.harness
            or summary.native_identity is None or summary.native_identity.session_id != identity.session_id
            or summary.stop_evidence.native.group_state != "gone" or summary.end.status != "ok"):
        return next_control, next_request, summary, False
    # The role's own delivery policy validates the structured outcome and its
    # correlated native facts; no success is inferred from an exit code alone.
    try:
        format = worker_format(request.harness)
        if format is not None and format.parse_value(summary)[0] is None:
            return next_control, next_request, summary, False
        run_execution.worker_delivery(summary)
    except (BoardError, OSError, ValueError, RecursionError):
        return next_control, next_request, summary, False
    return next_control, next_request, summary, True


def execute(control: dict, cancelled: threading.Event, state_dir: Path) -> tuple[str, int]:
    if control["operation"] == "review" and not adapter(control["harness"]).read_only_structured:
        raise BoardError("INVALID_ARGUMENT", "The harness has no review carrier")
    module = run_seam(control["harness"])
    if module is None:
        raise BoardError("ROLE_RUN_UNREGISTERED", "The role controller requires a registered run")
    if control["operation"] == "discover":
        try:
            catalog = module.run_discovery(
                cwd=control["cwd"], invocation_root=Path(control["privateRoot"]),
                native_root=Path(control["nativeRoot"]), timeout_seconds=control["timeoutSeconds"],
                cancelled=cancelled.is_set)
        except Exception as error:
            return canonical_json({"status": "error", "modelStarted": False,
                "error": str(error)[:512], "processState": {
                    "shutdownConfirmed": getattr(error, "discovery_shutdown_confirmed", None) is True}}), 1
        return canonical_json({"status": "ok", "modelStarted": False, "catalog": catalog,
                               "processState": {"shutdownConfirmed": True, "nativeExitCode": 0}}), 0
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
    # The run consumes the actual stored public request frame, rather than a
    # legacy dictionary or an object that never crossed its decode boundary.
    request_path = guard_private_path(Path(control["requestFile"]))
    _private_bytes(request_path, encode_run_request(request).encode(), exclusive=True)
    request = decode_run_request(request_path.read_bytes())
    if input_error is None:
        with _controller_live(control, request, services, state_dir) as live_services:
            result = run_harness(module, request, observer=observer, services=live_services, cancelled=cancelled.is_set)
    else:
        result = RunResult(identity=request.identity, harness=request.harness,
            end=RunEnd(status="error", reason_code=input_error.code, message=input_error.message),
            buddy=ResultBuddy(requested=request.buddy), model_started=False,
            stop_evidence=StopEvidence(native=StopLayer(group_state="gone")))
    graceful_stop = False
    if (control["operation"] == "worker" and cancelled.is_set()
            and result.end.status == "cancelled" and adapter(control["harness"]).native_resume):
        deadline = _host_stop(control, request)
        if deadline is not None:
            resumed = _summary_run(control, request, result, module, state_dir, deadline)
            if resumed is not None:
                control, request, result, graceful_stop = resumed
    if control["operation"] == "worker" and request.worker_permission is not None:
        # This is the harness mapping selected for the call, not an observation
        # that the native process obeyed it. Bind it to this exact run on both
        # sides of the controller frame, including failed native runs.
        path = Path(request.private_state.invocation_root) / "worker-permission.json"
        declaration = {"source": "harness-permission-declaration",
                       "binding": request.identity.to_payload(),
                       "permission": request.worker_permission.to_payload()}
        raw = canonical_json(declaration).encode()
        _private_bytes(path, raw, exclusive=True)
        ref = EvidenceRef(kind="worker-permission", location=str(path),
                          size_bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest())
        result = result.model_copy(update={"evidence_refs": (*result.evidence_refs, ref)})
    verdict = {
        "stopReason": correction.stop_reason if correction is not None else None,
        "elapsedMs": round((time.monotonic() - started) * 1000),
    }
    if control["operation"] == "worker":
        verdict["gracefulStop"] = graceful_stop
    code = 0 if result.end.status == "ok" else 1
    if control["operation"] == "worker" and code == 0:
        from ..harnesses.registry import worker_format
        if worker_format(control["harness"]) is not None:
            try:
                run_execution.worker_delivery(result)
            except (BoardError, OSError, ValueError, RecursionError) as error:
                verdict["workerError"] = ({"code": error.code, "message": error.message}
                    if isinstance(error, BoardError) else
                    {"code": "invalid-role-result", "message": "The run's role evidence could not be collected"})
                code = 1
    private_json(Path(control["verdictFile"]), verdict, exclusive=True)
    # Stdout is one RunResult frame; the owning role decodes and projects it.
    return encode_run_result(result), code


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--control", required=True)
    args = parser.parse_args()
    selected = os.environ.get("HMB_STATE_DIR")
    if not selected:
        parser.error("HMB_STATE_DIR is required")
    state_dir = Path(selected)
    try:
        control = decode_strict_json(guard_private_path(Path(args.control)).read_bytes())
        frame, code = execute(control, _cancelled, state_dir)
    except Exception:
        # No native receipt can be inferred from a controller failure. The
        # normal result reader refuses this diagnostic as a RunResult.
        frame, code = canonical_json({"status": "error", "code": "role-controller-failed",
                                      "processState": {"shutdownConfirmed": False}}), 1
    sys.stdout.write(frame + "\n")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
