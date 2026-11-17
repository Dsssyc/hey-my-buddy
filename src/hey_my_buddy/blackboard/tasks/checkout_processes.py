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

_WINDOWS = os.name == "nt"


def allocation_created_at(directory: Path) -> float:
    """request.json is written once, before this allocation creates a checkout."""
    try:
        return native_path(directory / 'request.json').stat().st_mtime
    except OSError as error:
        raise BoardError('WORKSPACE_IO_ERROR', str(error)) from error


def removal_sibling(checkout: Path) -> Path:
    """The reserved same-parent name a Windows removal renames the checkout to.

    The name is deterministic so an interrupted removal is recognized and
    finished by the next attempt instead of being left as unknown evidence.
    """
    return checkout.parent / (checkout.name + '.removing')


def _rename_whole_checkout(source: Path, target: Path) -> None:
    """Rename one whole checkout directory through the native filesystem call."""
    native_path(source).rename(native_path(target))


def require_idle_checkout(checkout: Path, *, allocated_at: float) -> None:
    if _WINDOWS:
        # Windows provides no supported query for another process's cwd. One
        # same-parent rename of the whole checkout and back is the proof: NTFS
        # refuses the rename outright while any process keeps the checkout or
        # any descendant as its cwd or holds any handle inside it, and a refused
        # rename has touched nothing. The caller holds the allocation lock.
        root = Path(os.path.realpath(checkout))
        sibling = removal_sibling(root)
        try:
            _rename_whole_checkout(root, sibling)
        except PermissionError as error:
            raise BoardError("NOT_READY", "The checkout is in use and cannot be proven idle",
                             reasons=["checkout-in-use"], path=str(checkout)) from error
        try:
            _rename_whole_checkout(sibling, root)
        except OSError as error:
            raise BoardError("WORKSPACE_IO_ERROR", "The checkout idle rename could not be undone",
                             path=str(checkout), removalName=str(sibling)) from error
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
