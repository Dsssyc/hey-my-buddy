"""Refuse checkout deletion while same-account processes use or may use its cwd."""
from __future__ import annotations

import os
from pathlib import Path

import psutil

from ...errors import BoardError
from ...paths import native_path
from ...process_observation import _processes
from ...protocol.process_start import process_start
from ...protocol.reasons import reason


def allocation_created_at(directory: Path) -> float:
    """request.json is written once, before this allocation creates a checkout."""
    try:
        return native_path(directory / 'request.json').stat().st_mtime
    except OSError as error:
        raise BoardError('WORKSPACE_IO_ERROR', str(error)) from error


def require_idle_checkout(checkout: Path, *, allocated_at: float) -> None:
    if os.name == "nt":
        # Windows provides no supported query for another process's cwd, and
        # the OS already refuses deletion of a directory used as a cwd.
        return
    try:
        # Only this owned checkout is resolved. psutil's cwd is already a
        # kernel-resolved path; never open, stat or resolve another process's cwd.
        root = Path(os.path.realpath(checkout))
        processes, complete = _processes()
        if not complete:
            raise OSError("incomplete process enumeration")
        users, unreadable = [], []
        for process in processes:
            try:
                started = process_start(process.pid, include_exited=False)
                if started is None:
                    continue
                try:
                    cwd = process.cwd()
                except (psutil.AccessDenied, OSError):
                    if process.create_time() < allocated_at:
                        continue
                    cwd = None
                if cwd is not None and not Path(cwd).is_relative_to(root):
                    continue
                executable = process.name()
                after = process_start(process.pid, include_exited=False)
                if after is None:
                    continue
                if after != started:
                    raise OSError("process identity changed during cwd observation")
                item = {"pid": process.pid, "startedAt": started, "executable": executable}
                (unreadable if cwd is None else users).append(item)
            except (psutil.NoSuchProcess, ProcessLookupError):
                continue
        if not users and not unreadable:
            return
        cause = reason("checkout.process-blockers", processes=users, unreadableProcesses=unreadable)
    except (OSError, psutil.Error) as error:
        cause = reason("checkout.process-scan-failed")
        raise BoardError("NOT_READY", "The checkout process observation blocks deletion",
                         reasons=[cause["code"]], reason=cause) from error
    raise BoardError("NOT_READY", "The checkout process observation blocks deletion",
                     reasons=[cause["code"]], reason=cause)
