"""Process entry retained only for registered harness model discovery."""
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
    if control.get("operation") != "discover":
        raise BoardError("INVALID_ARGUMENT", "The controller only supports model discovery")
    module = run_seam(control["harness"])
    if module is None or not callable(getattr(module, "run_discovery", None)):
        raise BoardError("ROLE_RUN_UNREGISTERED", "The harness has no registered discovery operation")
    try:
        catalog = module.run_discovery(
            cwd=control["cwd"], invocation_root=Path(control["privateRoot"]),
            native_root=Path(control["nativeRoot"]), timeout_seconds=control["timeoutSeconds"],
            cancelled=cancelled.is_set)
    except Exception as error:
        unknown = getattr(error, "code", None) == "deadline"
        return canonical_json({"status": "unknown" if unknown else "error", "modelStarted": False,
            "code": getattr(error, "code", None), "error": str(error)[:512], "processState": {
                "shutdownConfirmed": getattr(error, "discovery_shutdown_confirmed", None) is True}}), 0 if unknown else 1
    return canonical_json({"status": "ok", "modelStarted": False, "catalog": catalog,
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
