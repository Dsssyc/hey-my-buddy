"""Process entry for registered harness operations that do not start model turns."""
from __future__ import annotations

import argparse
import io
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
from ...private_files import read_bytes
from ...protocol import schemas
from ..harnesses.run_contract import HARNESS_NAMES
from ..harnesses.registry import run_seam


NATIVE_OPERATIONS = {
    "discover": {
        "seam": "run_discovery", "resultKey": "catalog", "allowUnknown": True,
        "unavailableCode": "CATALOG_UNAVAILABLE",
        "stopUnconfirmedCode": "HARNESS_DISCOVERY_STOP_UNCONFIRMED",
    },
    "delete-sessions": {
        "seam": "run_delete_sessions", "resultKey": "sessions", "allowUnknown": False,
        "unavailableCode": "UNSUPPORTED", "stopUnconfirmedCode": "HARNESS_DELETE_STOP_UNCONFIRMED",
    },
}


def execute(control: dict, cancelled: threading.Event, state_dir: Path) -> tuple[str, int]:
    operation = control.get("operation")
    if not isinstance(operation, str) or operation not in NATIVE_OPERATIONS:
        raise BoardError("INVALID_ARGUMENT", "Unknown native controller operation")
    schemas.reject_unknown(control, {"operation", "arguments", "harness", "cwd", "privateRoot",
                                     "nativeRoot", "timeoutSeconds"}, "native controller")
    if control.get("harness") not in HARNESS_NAMES:
        raise BoardError("INVALID_ARGUMENT", "Unknown native controller harness")
    specification = NATIVE_OPERATIONS[operation]
    seam = specification["seam"]
    module = run_seam(control["harness"])
    if not callable(getattr(module, seam, None)):
        raise BoardError("ROLE_RUN_UNREGISTERED", f"The harness has no registered {operation} operation")
    try:
        value = getattr(module, seam)(
            cwd=control["cwd"], invocation_root=Path(control["privateRoot"]),
            native_root=Path(control["nativeRoot"]), timeout_seconds=control["timeoutSeconds"],
            cancelled=cancelled.is_set, **control["arguments"])
    except Exception as error:
        unknown = specification["allowUnknown"] and getattr(error, "code", None) == "deadline"
        native_error = getattr(error, "native_error", None)
        return canonical_json({"status": "unknown" if unknown else "error", "modelStarted": False,
            "code": getattr(error, "code", None), "error": str(error).encode("utf-8")[:512].decode("utf-8", errors="ignore"),
            **({"nativeError": native_error} if native_error else {}), "processState": {
                "shutdownConfirmed": getattr(error, "discovery_shutdown_confirmed", None) is True}}), 0 if unknown else 1
    return canonical_json({"status": "ok", "modelStarted": False, specification["resultKey"]: value,
                           "processState": {"shutdownConfirmed": True, "nativeExitCode": 0}}), 0


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        if isinstance(stream, io.TextIOWrapper):
            stream.reconfigure(encoding="utf-8", newline="\n")
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
        control = decode_strict_json(read_bytes(Path(args.control)))
        frame, code = execute(control, _cancelled, Path(selected))
    except BoardError as error:
        frame, code = canonical_json({"status": "error", "modelStarted": False, **error.payload(),
                                      "processState": {"shutdownConfirmed": True}}), 1
    except Exception:
        frame, code = canonical_json({"status": "error", "code": "role-controller-failed",
                                      "processState": {"shutdownConfirmed": False}}), 1
    sys.stdout.buffer.write((frame + "\n").encode("utf-8"))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
