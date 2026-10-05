"""One mechanical outer layer for the harness controller processes.

ADR-025 step 1-B. The outer launch, log FD finalization, owned spawn, deadline
stamping, controller-result reads and two-layer stop confirmation that the four
harness adapters and the old Router structured-call channel each wrote out
again live here once — every path goes through the same launch and collection
face, with its own differences expressed as explicit rules, never merged into
a stronger or weaker assumption. The native runner, config, protocol and
tool-evidence bodies keep their own implementations; this module adds no
process ownership, no IPC, no role verdict and no fact the paths did not
already publish. The strict decoders stay parameters because the Codex, Claude
and ZCode decoders genuinely differ (Claude also refuses floats that overflow
to infinity), the Router's ordinary 256 KiB read keeps its private-path
exceptions, the DSH worker keeps its unbounded last-line read, and the DSH
worker keeps its legacy Node stop exceptions documented until step four.
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
from ..roles.turn_io import guard_private_path

#: The strict controller-result bound shared by the Codex, Claude and ZCode
#: worker and discovery reads.
STRICT_RESULT_BYTES = 512 * 1024
#: The Router channel's ordinary evidence bound, with its own private-path
#: checks; the DSH worker's last-line read is unbounded by design.
ROUTER_EVIDENCE_BYTES = 256 * 1024


def launch_controller(*, prepare, log_paths: dict, before_try=None, timeout_seconds: int | None = None,
                      unbounded_deadline: float | None = None,
                      grace_seconds: float = 0) -> ProcessHandle:
    """Open the run's logs, resolve the spawn arguments, spawn one owned child.

    ``prepare`` is the calling site's own post-log step, run exactly where the
    baselines evaluated their command, working directory and environment: after
    ``open_logs`` and inside the spawn ``try``. An evaluation failure therefore
    leaves the run's two log files in place, both descriptors are still closed
    by the ``finally``, and ``owned_popen`` never runs. It takes the value
    ``before_try`` produced (None when there was none) and returns
    ``(command, cwd, environment)`` for ``owned_popen``, which spawns with
    ``stdin=DEVNULL``, a fresh session and closed inheritable descriptors so
    the outer group stays separable from every inner native group.

    ``before_try`` — only the DSH worker path passes one — is the baseline's
    between step: it runs after ``open_logs`` and outside the spawn ``try``,
    exactly where the DSH baseline evaluated the selected harness record, built
    its native environment and applied its variable allowlist. A failure there
    leaves the run's two log descriptors open, as the baseline did, and its
    return value is handed to ``prepare``.

    ``timeout_seconds`` of 0 keeps each path's own unlimited meaning by
    stamping exactly ``unbounded_deadline`` — math.inf on the Codex, Claude and
    ZCode worker paths, None on the DSH worker path — and a positive value
    stamps now + timeout + grace_seconds, where only the Router review passes
    its +10 second grace (the budget reaching it is validated positive, and the
    worker and no-tool paths pass 0). ``None`` — the discovery launches —
    stamps no deadline; the caller waits a bounded time and terminates the
    handle itself.
    """
    stdout, stderr = open_logs(log_paths)
    bound = before_try() if before_try is not None else None
    try:
        command, cwd, environment = prepare(bound)
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


def read_strict_result(path: Path, *, decode) -> dict | None:
    """The strict controller-result read of the Codex, Claude and ZCode paths.

    Reads at most 512 KiB + 1 byte; an over-limit file, undecodable bytes or a
    non-object value return None. ``decode`` is the calling harness's own
    protocol decoder (duplicate members and non-finite numbers refused); the
    three are never merged into one stronger decoder.
    """
    try:
        with path.open("rb") as stream:
            raw = stream.read(STRICT_RESULT_BYTES + 1)
        if len(raw) > STRICT_RESULT_BYTES:
            return None
        value = decode(raw)
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


def read_router_result(path: Path) -> object | None:
    """The Router channel's collection read rule over :func:`read_plain_evidence`.

    Its baseline exceptions (``OSError``, ``ValueError``, ``BoardError``,
    ``RecursionError``) fold to None; a parsed non-object value is returned
    as-is for the role's own fallback.
    """
    try:
        return read_plain_evidence(path)
    except (OSError, ValueError, BoardError, RecursionError):
        return None


def read_last_line_result(path: Path) -> dict | None:
    """The DSH worker's plain read, unchanged in size and error semantics.

    Unbounded ``read_text(errors="replace")``, the last non-empty line as one
    plain ``json.loads`` value; a non-object, an empty log or any failure of
    the baseline's exception set (``OSError``, ``ValueError``, ``IndexError``)
    is None.
    """
    try:
        raw = path.read_text(errors="replace")
        payload = json.loads(raw.strip().splitlines()[-1]) if raw.strip() else None
        return payload if isinstance(payload, dict) else None
    except (OSError, ValueError, IndexError):
        return None


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


def legacy_node_stop_confirmed(handle: ProcessHandle, *, native_receipt: object,
                               preflight) -> bool:
    """The DSH worker's legacy inner stop, removed with the Node runner in step four.

    Any truthy runner receipt counts (not only ``is True``), and the deferred
    ``preflight`` — the runner's exit-2 preflight failure with an empty stdout
    log — is evaluated only when the receipt is falsy, as in the baseline. The
    outer layer is the same conservative owned-group observation every path
    shares, and unknown stays alive.
    """
    return (bool(native_receipt) or preflight()) and handle.shutdown_confirmed()


@dataclass(frozen=True)
class ControllerCollection:
    """The mechanical collection facts of one settled controller run.

    ``payload`` is whatever the path's read rule admitted (None when it
    admitted nothing) and ``exit_code`` is the child's own return code as
    snapshotted after the read. ``stop_confirmed`` is the path's stop rule, or
    None when the path runs its own rule later (the DSH worker's legacy rule
    needs its preflight only in its own branches). No success or cancellation
    verdict and no signal projection is taken here.
    """

    payload: dict | None
    exit_code: int | None
    stop_confirmed: bool | None


def collect_controller(handle: ProcessHandle, *, read, stop=None) -> ControllerCollection:
    """One collection face for every controller result, strict or not.

    The shared mechanical sequence is the baseline's: run the path's ``read``
    rule against the stdout log, take the child's return code, then run the
    path's ``stop`` rule over ``(payload, handle)``. ``read`` is one of the
    named rules above or a narrow closure over one; ``stop`` is one of the
    stop rules, omitted only where the caller must run its own later. No
    success, cancellation or failure verdict is taken here.
    """
    payload = read(Path(handle.log_paths["stdout"]))
    exit_code = handle.process.returncode
    return ControllerCollection(payload=payload, exit_code=exit_code,
                                stop_confirmed=None if stop is None else stop(payload, handle))


def signal_name(exit_code: int | None) -> str | None:
    """The POSIX signal name of a negative exit code; no signal otherwise."""
    if exit_code is None or exit_code >= 0:
        return None
    import signal
    try:
        return signal.Signals(-exit_code).name
    except ValueError:
        return f"signal-{-exit_code}"
