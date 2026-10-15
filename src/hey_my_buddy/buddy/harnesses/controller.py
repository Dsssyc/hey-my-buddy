"""Mechanical launch and collection for the model-discovery process.

The same module also guards retained native evidence used by structured roles.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import os
import stat
import subprocess
import time
from pathlib import Path
from typing import Callable

from .base import ProcessHandle, open_logs
from ..runtime.windows_process import ProcessCreationEvidence, owned_popen
from ...errors import BoardError
from ...json_codec import decode_strict_json
from ..roles.turn_io import guard_private_path

#: The strict discovery result and retained native evidence bound.
STRICT_RESULT_BYTES = 512 * 1024
#: The Router channel's ordinary evidence bound, with its own private-path
#: checks for retained request/result material.
ROUTER_EVIDENCE_BYTES = 256 * 1024


def launch_controller(*, prepare, log_paths: dict, timeout_seconds: int | None = None,
                      grace_seconds: float = 0) -> ProcessHandle:
    """Open logs, prepare and spawn one owned child, closing both log FDs.

    Every failure carries the creating boundary's ``owned_popen_evidence``.
    That receipt describes this controller only, not a separately owned native
    child. Consumers combine it with the controller's native stop receipt.
    Log opening and preparation precede child creation. A successful spawn
    transfers ownership to its handle before log descriptors are closed, so
    later failures terminate and observe that owned group. An ownership
    failure consumes owned_popen's receipt even when no handle was returned.
    Positive budgets stamp a deadline plus the supplied grace. An absent
    budget leaves the controller unbounded; discovery enforces its own limit.
    """
    evidence = ProcessCreationEvidence(created=False, shutdown_confirmed=True, exit_code=None)
    handle = None
    try:
        stdout, stderr = open_logs(log_paths)
        try:
            command, cwd, environment = prepare()
            try:
                process = owned_popen(command, cwd=cwd, env=environment, stdin=subprocess.DEVNULL,
                                      stdout=stdout, stderr=stderr, start_new_session=True, close_fds=True)
            except BaseException as error:
                evidence = error.owned_popen_evidence
                raise
            evidence = ProcessCreationEvidence(created=True, shutdown_confirmed=False,
                                               exit_code=process.returncode)
            handle = ProcessHandle(process, own_group=True, log_paths=log_paths)
        finally:
            try:
                os.close(stdout)
            finally:
                os.close(stderr)
        if timeout_seconds is not None:
            handle.deadline = time.monotonic() + timeout_seconds + grace_seconds
        return handle
    except BaseException as error:
        if handle is not None:
            try:
                handle.terminate(grace_seconds=0)
                stopped = handle.shutdown_confirmed()
            except BaseException as cleanup_error:
                cleanup_error.owned_popen_evidence = ProcessCreationEvidence(
                    created=True, shutdown_confirmed=False, exit_code=process.returncode)
                raise
            evidence = ProcessCreationEvidence(created=True, shutdown_confirmed=stopped,
                                               exit_code=process.returncode)
        error.owned_popen_evidence = evidence
        raise


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
