"""Refuse checkout deletion while same-account processes use or may use its cwd."""
from __future__ import annotations

from datetime import datetime
import os
from pathlib import Path

import psutil

from ...errors import BoardError
from ...process_observation import _processes
from ...protocol.process_start import process_start
from ...protocol.reasons import reason


def first_execution_time(connection, manifest: dict) -> float | None:
    """The first admission on this physical checkout, across its logical turns.

    Admission precedes the spawned execution, so it is a conservative lower
    bound when excluding older processes. These are existing board facts.
    """
    value = connection.execute(
        "SELECT MIN(a.started_at) FROM workflow_turns w JOIN attempts a ON a.attempt_id=w.attempt_id"
        " WHERE json_extract(w.input_json,'$.executionWorkspace.checkoutRoot')=?",
        (manifest['checkoutRoot'],),
    ).fetchone()[0]
    return datetime.fromisoformat(value).timestamp() if value is not None else None


def require_idle_checkout(checkout: Path, *, first_execution_at: float | None = None) -> None:
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
                    if first_execution_at is not None and process.create_time() < first_execution_at:
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
