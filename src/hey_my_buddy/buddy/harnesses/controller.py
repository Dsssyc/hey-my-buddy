"""Mechanical launch and collection for the model-discovery process.

The same module also guards retained native evidence used by structured roles.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import stat
from pathlib import Path
from typing import Callable

from ...errors import BoardError
from ...protocol.process_controller import launch_controller
from ...protocol.process_handle import ProcessHandle
from ...json_codec import decode_strict_json
from ..roles.turn_io import guard_private_path

#: The strict discovery result and retained native evidence bound.
STRICT_RESULT_BYTES = 512 * 1024
#: The Router channel's ordinary evidence bound, with its own private-path
#: checks for retained request/result material.
ROUTER_EVIDENCE_BYTES = 256 * 1024


def read_strict_result(path: Path) -> dict | None:
    """The strict controller-result read of the Codex, Claude and ZCode paths.

    Reads at most 512 KiB + 1 byte; an over-limit file, undecodable bytes or a
    non-object value return None. Decoding is the root package's one strict
    decoder: duplicate members and non-finite numbers (``1e999`` overflow
    included) are refused on all three paths alike.
    """
    try:
        with path.open("rb") as stream:
            raw = stream.read(STRICT_RESULT_BYTES + 1)
        if len(raw) > STRICT_RESULT_BYTES:
            return None
        value = decode_strict_json(raw)
        return value if isinstance(value, dict) else None
    except (OSError, ValueError, RecursionError):
        return None


def read_plain_evidence(path: Path) -> object:
    """The Router channel's ordinary evidence read, raising like its baseline.

    The path must be a guarded private path and an ordinary file of at most
    256 KiB, read as one plain ``json.loads`` value (duplicates and non-finite
    numbers are the Router's own ordinary semantics, not refused here). The
    same read serves the frozen request/result retention; violations raise
    ``BoardError`` and I/O or decoding failures raise ``OSError``/``ValueError``.
    """
    path = guard_private_path(path)
    metadata = path.lstat()
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > ROUTER_EVIDENCE_BYTES:
        raise BoardError("PRIVATE_PATH_UNSAFE", "Evidence must be bounded ordinary JSON", path=str(path))
    with path.open("rb") as stream:
        raw = stream.read(ROUTER_EVIDENCE_BYTES + 1)
    if len(raw) > ROUTER_EVIDENCE_BYTES:
        raise BoardError("PRIVATE_PATH_UNSAFE", "Evidence exceeds its byte bound", path=str(path))
    return json.loads(raw)


def stop_confirmed(payload: dict | None, observe_outer: Callable[[], bool]) -> bool:
    """Combine the native receipt with one actual outer stop observation.

    The whole conjunction is boolean: the controller's own receipt must carry
    ``processState.shutdownConfirmed`` exactly ``True`` and the owned outer
    group must then be observed gone. The observation is a held handle's
    method or the creating boundary's recorded fact, never a fabricated
    handle. Missing or malformed native receipts never start the observation;
    any unconfirmed layer leaves the whole native read unconfirmed.
    """
    if payload is None:
        return False
    native = payload.get("processState")
    return bool(isinstance(native, dict) and native.get("shutdownConfirmed") is True
                and observe_outer())


@dataclass(frozen=True)
class ControllerCollection:
    """The admitted payload, return code and explicit stop fact of one run.

    No success or cancellation verdict and no signal projection is taken here.
    """

    payload: dict | None
    exit_code: int | None
    stop_confirmed: bool


def collect_controller(handle: ProcessHandle, *, read, stop) -> ControllerCollection:
    """Read the owned result, snapshot its return code and apply its stop rule."""
    payload = read(Path(handle.log_paths["stdout"]))
    exit_code = handle.process.returncode
    return ControllerCollection(payload=payload, exit_code=exit_code,
                                stop_confirmed=stop(payload, handle))


def signal_name(exit_code: int | None) -> str | None:
    """The POSIX signal name of a negative exit code; no signal otherwise."""
    if exit_code is None or exit_code >= 0:
        return None
    import signal
    try:
        return signal.Signals(-exit_code).name
    except ValueError:
        return f"signal-{-exit_code}"
