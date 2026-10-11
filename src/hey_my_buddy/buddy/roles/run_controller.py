"""The single Python process entry for roles using a registered harness run."""
from __future__ import annotations

import argparse
import os
from contextlib import contextmanager
from dataclasses import replace
import signal
import sys
import threading
import time
from pathlib import Path

from ...errors import BoardError
from ...protocol.contracts import HarnessRunLive
from ..harnesses.c_two_live import CTwoLiveEndpoint, write_ready_material
from ..harnesses.live import EXISTING_CAPABILITIES, LiveCapabilities
from ...json_codec import canonical_json, decode_strict_json
from ..harnesses.registry import adapter, run_seam
from ..harnesses.run_contract import (
    ResultConfiguration, RunEnd, RunResult, StopEvidence, StopLayer,
    decode_run_request, encode_run_request, encode_run_result,
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
            configuration=ResultConfiguration(requested=request.configuration), model_started=False,
            stop_evidence=StopEvidence(native=StopLayer(group_state="gone")))
    verdict = {
        "stopReason": correction.stop_reason if correction is not None else None,
        "elapsedMs": round((time.monotonic() - started) * 1000),
    }
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
    selected = os.environ.get("BUDDY_STATE_DIR")
    if not selected:
        parser.error("BUDDY_STATE_DIR is required")
    state_dir = Path(selected)
    cancelled = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT,
                *((signal.SIGBREAK,) if hasattr(signal, "SIGBREAK") else ())):
        signal.signal(sig, lambda _sig, _frame: cancelled.set())
    try:
        control = decode_strict_json(guard_private_path(Path(args.control)).read_bytes())
        frame, code = execute(control, cancelled, state_dir)
    except Exception:
        # No native receipt can be inferred from a controller failure. The
        # normal result reader refuses this diagnostic as a RunResult.
        frame, code = canonical_json({"status": "error", "code": "role-controller-failed",
                                      "processState": {"shutdownConfirmed": False}}), 1
    sys.stdout.write(frame + "\n")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
