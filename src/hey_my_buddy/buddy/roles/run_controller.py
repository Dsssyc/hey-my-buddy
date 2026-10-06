"""The single Python process entry for roles using a registered harness run."""
from __future__ import annotations

import argparse
import signal
import sys
import threading
import time
from pathlib import Path

from ...errors import BoardError
from ...json_codec import canonical_json, decode_strict_json
from ..harnesses.registry import run_seam
from ..harnesses.run_contract import decode_run_request, encode_run_request, encode_run_result
from . import run_execution
from .controller import run_harness
from .turn_io import _private_bytes, guard_private_path, private_json


def execute(control: dict, cancelled: threading.Event) -> tuple[str, int]:
    module = run_seam(control["harness"])
    if module is None:
        raise BoardError("ROLE_RUN_UNREGISTERED", "The role controller requires a registered run")
    if control["operation"] == "discover":
        catalog = module.run_discovery(
            cwd=control["cwd"], invocation_root=Path(control["privateRoot"]),
            native_root=Path(control["nativeRoot"]), timeout_seconds=control["timeoutSeconds"],
            cancelled=cancelled.is_set)
        return canonical_json({"status": "ok", "modelStarted": False, "catalog": catalog,
                               "processState": {"shutdownConfirmed": True, "nativeExitCode": 0}}), 0
    started = time.monotonic()
    if control["operation"] == "worker":
        request, services, observer = run_execution.worker_request(control, module)
        correction = None
    elif control["operation"] == "fast":
        request, services, observer, correction = run_execution.fast_request(control)
    elif control["operation"] == "review":
        request, services, observer, correction = run_execution.review_request(control)
    else:
        raise BoardError("INVALID_ARGUMENT", "Unknown role operation")
    # The run consumes the actual stored public request frame, rather than a
    # legacy dictionary or an object that never crossed its decode boundary.
    request_path = guard_private_path(Path(control["requestFile"]))
    _private_bytes(request_path, encode_run_request(request).encode(), exclusive=True)
    request = decode_run_request(request_path.read_bytes())
    result = run_harness(module, request, observer=observer, services=services, cancelled=cancelled.is_set)
    private_json(Path(control["verdictFile"]), {
        "stopReason": correction.stop_reason if correction is not None else None,
        "elapsedMs": round((time.monotonic() - started) * 1000),
    }, exclusive=True)
    # Stdout is one RunResult frame; the owning role decodes and projects it.
    return encode_run_result(result), 0 if result.end.status == "ok" else 1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--control", required=True)
    args = parser.parse_args()
    cancelled = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT,
                *((signal.SIGBREAK,) if hasattr(signal, "SIGBREAK") else ())):
        signal.signal(sig, lambda _sig, _frame: cancelled.set())
    try:
        control = decode_strict_json(guard_private_path(Path(args.control)).read_bytes())
        frame, code = execute(control, cancelled)
    except Exception:
        # No native receipt can be inferred from a controller failure. The
        # normal result reader refuses this diagnostic as a RunResult.
        frame, code = canonical_json({"status": "error", "code": "role-controller-failed",
                                      "processState": {"shutdownConfirmed": False}}), 1
    sys.stdout.write(frame + "\n")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
