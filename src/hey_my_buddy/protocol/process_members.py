"""POSIX session/tree observations admitted while the original root pins its PID.

No session number alone authorizes a signal. Observations retain exact kernel
creation values, including for members that subsequently reparent or setsid.
"""
from __future__ import annotations

from dataclasses import dataclass
import os

import psutil

from .process_start import process_start


@dataclass(frozen=True)
class ProcessMember:
    pid: int
    started_at: int
    parent: int
    session: int
    live: bool


def process_snapshot() -> dict[int, ProcessMember]:
    """One same-account PID scan; an incomplete observation raises OSError."""
    try:
        rows = {}
        for pid in psutil.pids():
            try:
                process = psutil.Process(pid)
                if process.uids().effective != os.geteuid():
                    continue
                started = process_start(pid, include_exited=True)
                if started is None:
                    continue
                parent = process.ppid()
                session = os.getsid(pid)
                live = process.status() != psutil.STATUS_ZOMBIE
                if process_start(pid, include_exited=True) != started:
                    raise OSError("process identity changed during session scan")
                rows[pid] = ProcessMember(pid, started, parent, session, live)
            except (psutil.NoSuchProcess, ProcessLookupError):
                continue
        return rows
    except psutil.Error as error:
        raise OSError("incomplete process session scan") from error


class SessionProcesses:
    """One root's admitted identities, shared by owned and recovery stops."""

    def __init__(self, pid: int, started_at: int, *, session: int | None = None,
                 snapshot=None, creation=None, alive=None, send=None):
        self.pid = pid
        self.started_at = started_at
        self.session = session
        self.snapshot = snapshot or process_snapshot
        self.creation = creation or (lambda pid: process_start(pid, include_exited=True))
        self.alive = alive or (lambda pid: process_start(pid, include_exited=False))
        self.send = send or os.kill
        self.known: dict[int, int] = {}
        self.live: dict[int, int] = {}

    def observe(self) -> str:
        root_before = self.creation(self.pid)
        rows = self.snapshot()
        root_after = self.creation(self.pid)
        if root_before != root_after:
            raise OSError("root identity changed during session scan")
        pinned = root_before == self.started_at
        if pinned:
            root = rows.get(self.pid)
            # Darwin's libproc/psutil omits zombies; KERN_PROC_PID still pins
            # their creation identity. A zombie root needs no SID/PPID row.
            if root is None and self.alive(self.pid) == self.started_at:
                raise OSError("root missing from session scan")
            if root is not None and root.started_at != self.started_at:
                raise OSError("root changed during session scan")
            if root is not None and self.session is not None and root.session != self.session:
                raise OSError("owned root changed session")
            admitted = {self.pid}
            admitted.update(pid for pid, row in rows.items()
                            if self.session is not None and row.session == self.session)
            admitted.update(pid for pid, start in self.known.items()
                            if pid in rows and rows[pid].started_at == start)
            while True:
                children = {pid for pid, row in rows.items()
                            if row.parent in admitted and row.parent in rows
                            and rows[row.parent].started_at <= row.started_at}
                if children <= admitted:
                    break
                admitted.update(children)
            self.known.update((pid, rows[pid].started_at) for pid in admitted if pid in rows)
        self.live = {pid: start for pid, start in self.known.items()
                     if pid in rows and rows[pid].started_at == start and rows[pid].live}
        # After someone else reaps the root, newly seen SID members cannot be
        # attributed. The SID may now name a different session on either OS.
        if not pinned and any(row.live and row.session == self.session
                              and self.known.get(pid) != row.started_at
                              for pid, row in rows.items()):
            return "unknown"
        return "alive" if self.live else "identity-absent"

    def signal(self, sig: int) -> None:
        if self.observe() == "unknown":
            return
        # Descendants first: all identities were admitted before stopping root.
        for pid, started in sorted(self.live.items(), key=lambda item: item[0] == self.pid):
            if self.creation(pid) != started:
                continue
            try:
                self.send(pid, sig)
            except ProcessLookupError:
                pass
