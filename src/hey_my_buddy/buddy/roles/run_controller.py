"""Process entry for registered harness operations that do not start model turns."""
from __future__ import annotations

import argparse
import os
import sys
import threading
from pathlib import Path

from ...text import configure_utf8_output
from ...errors import BoardError
from ...json_codec import canonical_json, decode_strict_json
from ...private_files import read_bytes
from ...protocol import schemas
from ..harnesses.run_contract import HARNESS_NAMES
from ..harnesses.registry import native_operation

_cancelled = threading.Event()


def _read_stop():
    # The parent alone holds stdin's write end. EOF is this controller's one
    # stop channel, including on Windows with CREATE_NO_WINDOW.
    # A raw read lets a normally completed operation exit while this daemon
    # thread is still waiting, without holding Python's buffered-stdin lock.
    while os.read(sys.stdin.fileno(), 1):
        pass
    _cancelled.set()


NATIVE_OPERATIONS = {
    "discover": {
        "resultKey": "catalog", "allowUnknown": True,
        "stopUnconfirmedCode": "HARNESS_DISCOVERY_STOP_UNCONFIRMED",
    },
    "delete-sessions": {
        "resultKey": "sessions", "allowUnknown": False,
        "stopUnconfirmedCode": "HARNESS_DELETE_STOP_UNCONFIRMED",
    },
}


def execute(control: dict, cancelled: threading.Event) -> tuple[str, int]:
    operation = control.get("operation")
    if not isinstance(operation, str) or operation not in NATIVE_OPERATIONS:
        raise BoardError("INVALID_ARGUMENT", "Unknown native controller operation")
    schemas.reject_unknown(control, {"operation", "arguments", "harness", "cwd", "privateRoot",
                                     "nativeRoot", "timeoutSeconds"}, "native controller")
    if control.get("harness") not in HARNESS_NAMES:
        raise BoardError("INVALID_ARGUMENT", "Unknown native controller harness")
    specification = NATIVE_OPERATIONS[operation]
    seam = native_operation(control["harness"], operation)
    try:
        value = seam(
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
    configure_utf8_output()
    if not os.environ.get("HMB_STATE_DIR"):
        sys.stderr.write("hey-my-buddy run controller: HMB_STATE_DIR is required\n")
        return 2
    parser = argparse.ArgumentParser()
    parser.add_argument("--control", required=True)
    args = parser.parse_args()
    threading.Thread(target=_read_stop, daemon=True, name="controller-input").start()
    try:
        control = decode_strict_json(read_bytes(Path(args.control)))
        frame, code = execute(control, _cancelled)
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
