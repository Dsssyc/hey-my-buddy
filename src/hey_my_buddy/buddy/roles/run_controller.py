"""Process entry for registered harness operations that do not start model turns."""
from __future__ import annotations

import argparse
import os
import signal
import sys
import threading
from pathlib import Path

_cancelled = threading.Event()


def _interrupt(_sig, _frame):
    _cancelled.set()


if __name__ == "__main__":
    for _signal in (signal.SIGTERM, signal.SIGINT,
                    *((signal.SIGBREAK,) if hasattr(signal, "SIGBREAK") else ())):
        signal.signal(_signal, _interrupt)

from ...errors import BoardError
from ...json_codec import canonical_json, decode_strict_json
from ..harnesses.registry import run_seam
from .turn_io import guard_private_path


def execute(control: dict, cancelled: threading.Event, state_dir: Path) -> tuple[str, int]:
    operation = control["operation"]
    if operation == "discover":
        seam, result_key, arguments = "run_discovery", "catalog", {}
    elif operation == "delete-sessions":
        seam, result_key = "run_delete_sessions", "sessions"
        arguments = {"session_ids": control["sessionIds"]}
    else:
        raise BoardError("INVALID_ARGUMENT", "Unsupported native controller operation")
    module = run_seam(control["harness"])
    if module is None or not callable(getattr(module, seam, None)):
        raise BoardError("ROLE_RUN_UNREGISTERED", f"The harness has no registered {operation} operation")
    try:
        value = getattr(module, seam)(
            cwd=control["cwd"], invocation_root=Path(control["privateRoot"]),
            native_root=Path(control["nativeRoot"]), timeout_seconds=control["timeoutSeconds"],
            cancelled=cancelled.is_set, **arguments)
    except Exception as error:
        unknown = operation == "discover" and getattr(error, "code", None) == "deadline"
        native_error = getattr(error, "native_error", None)
        return canonical_json({"status": "unknown" if unknown else "error", "modelStarted": False,
            "code": getattr(error, "code", None), "error": str(error) if native_error else str(error)[:512],
            **({"nativeError": native_error} if native_error else {}), "processState": {
                "shutdownConfirmed": getattr(error, "discovery_shutdown_confirmed", None) is True}}), 0 if unknown else 1
    return canonical_json({"status": "ok", "modelStarted": False, result_key: value,
                           "processState": {"shutdownConfirmed": True, "nativeExitCode": 0}}), 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--control", required=True)
    args = parser.parse_args()
    selected = os.environ.get("HMB_STATE_DIR")
    if not selected:
        parser.error("HMB_STATE_DIR is required")
    for sig in (signal.SIGTERM, signal.SIGINT,
                *((signal.SIGBREAK,) if hasattr(signal, "SIGBREAK") else ())):
        signal.signal(sig, _interrupt)
    try:
        control = decode_strict_json(guard_private_path(Path(args.control)).read_bytes())
        frame, code = execute(control, _cancelled, Path(selected))
    except Exception:
        frame, code = canonical_json({"status": "error", "code": "role-controller-failed",
                                      "processState": {"shutdownConfirmed": False}}), 1
    sys.stdout.write(frame + "\n")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
