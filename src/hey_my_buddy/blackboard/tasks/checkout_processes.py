"""Refuse checkout deletion while a live same-account process uses its cwd."""
from __future__ import annotations

import os
from pathlib import Path

import psutil

from ...errors import BoardError
from ...process_observation import _processes
from ...protocol.process_start import process_start
from ...protocol.reasons import reason


def _below_directory(path: str, identity: tuple[int, int]) -> bool:
    """Walk directory objects, so aliases and similarly prefixed paths are safe."""
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        while True:
            current = os.fstat(fd)
            current_id = (current.st_dev, current.st_ino)
            if current_id == identity:
                return True
            parent = os.open("..", os.O_RDONLY | os.O_DIRECTORY, dir_fd=fd)
            info = os.fstat(parent)
            if (info.st_dev, info.st_ino) == current_id:
                os.close(parent)
                return False
            os.close(fd)
            fd = parent
    finally:
        os.close(fd)


def require_idle_checkout(checkout: Path) -> None:
    if os.name == "nt":
        # Windows provides no supported query for another process's cwd, and
        # the OS already refuses deletion of a directory used as a cwd.
        return
    try:
        root = checkout.stat()
        identity = (root.st_dev, root.st_ino)
        processes, complete = _processes()
        if not complete:
            raise OSError("incomplete process enumeration")
        users = []
        for process in processes:
            try:
                started = process_start(process.pid, include_exited=False)
                if started is None:
                    continue
                cwd = process.cwd()
                inside = _below_directory(cwd, identity)
                executable = Path(process.exe()).name if inside else None
                if process_start(process.pid, include_exited=False) != started:
                    raise OSError("process identity changed during cwd observation")
                if inside:
                    users.append({"pid": process.pid, "startedAt": started, "executable": executable})
            except (psutil.NoSuchProcess, ProcessLookupError):
                continue
        if not users:
            return
        cause = reason("checkout.live-processes", processes=users)
    except (OSError, psutil.Error) as error:
        cause = reason("checkout.process-scan-failed")
        raise BoardError("NOT_READY", "The checkout process observation blocks deletion",
                         reasons=[cause["code"]], reason=cause) from error
    raise BoardError("NOT_READY", "The checkout process observation blocks deletion",
                     reasons=[cause["code"]], reason=cause)
