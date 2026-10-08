"""Mechanical launch and collection for the registered Python controllers.

Each caller supplies its command preparation, bounded result reader and
explicit two-layer stop rule. The outer layer owns the controller process and
log descriptors; it makes no role verdict and does not drive native protocols.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import os
import stat
import subprocess
import time
from pathlib import Path

from .base import ProcessHandle, open_logs
from ..runtime.windows_process import owned_popen
from ...errors import BoardError
from ...json_codec import decode_strict_json
from ..roles.turn_io import guard_private_path

#: The strict controller-result bound shared by the Codex, Claude and ZCode
#: worker and discovery reads.
STRICT_RESULT_BYTES = 512 * 1024
#: The Router channel's ordinary evidence bound, with its own private-path
#: checks for retained request/result material.
ROUTER_EVIDENCE_BYTES = 256 * 1024


def launch_controller(*, prepare, log_paths: dict, timeout_seconds: int | None = None,
                      unbounded_deadline: float | None = None,
                      grace_seconds: float = 0) -> ProcessHandle:
    """Open logs, prepare and spawn one owned child, closing both log FDs.

    Preparation runs inside the spawn try block and returns command, cwd and
    environment. A failed preparation leaves the logs as evidence and closes
    their descriptors. Positive budgets stamp a deadline plus the supplied
    grace; zero uses the caller's unbounded deadline; discovery supplies None
    and terminates its handle after its own bounded wait.
    """
    stdout, stderr = open_logs(log_paths)
    try:
        command, cwd, environment = prepare()
        process = owned_popen(command, cwd=cwd, env=environment, stdin=subprocess.DEVNULL,
                              stdout=stdout, stderr=stderr, start_new_session=True, close_fds=True)
    finally:
        os.close(stdout)
        os.close(stderr)
    handle = ProcessHandle(process, own_group=True, log_paths=log_paths)
    if timeout_seconds is None:
        return handle
    if timeout_seconds == 0:
        handle.deadline = unbounded_deadline
    else:
        handle.deadline = time.monotonic() + timeout_seconds + grace_seconds
    return handle


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


def stop_confirmed(payload: object, handle: ProcessHandle) -> bool:
    """The Worker and discovery two-layer stop, exactly their baseline rule.

    The whole conjunction is boolean: the controller's own receipt must carry
    ``processState.shutdownConfirmed`` exactly ``True`` and the owned outer
    group must then be observed gone. Without a receipt the outer observation
    is not even started, and any unobservable layer leaves the attempt not
    stopped: unknown stays alive.
    """
    return bool(payload and payload.get("processState", {}).get("shutdownConfirmed") is True
                and handle.shutdown_confirmed())


def router_stop_confirmed(payload: object, handle: ProcessHandle) -> bool:
    """The old Router channel's two-layer stop, with its own outer coercion.

    The receipt must still be exactly ``True``, and the outer observation must
    also be exactly ``True`` (``handle.shutdown_confirmed() is True``): a
    non-boolean outer value never counts as stopped and never reaches the
    report as a truthy number or a ``None`` field.
    """
    return payload.get("processState", {}).get("shutdownConfirmed") is True \
        and handle.shutdown_confirmed() is True


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
