"""POSIX session/tree observations admitted while the original root pins its PID.

No session number alone authorizes a signal. Observations retain exact kernel
creation values, including for members that subsequently reparent or setsid.
"""
from __future__ import annotations

from dataclasses import dataclass
import os
import threading

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
                after = process_start(pid, include_exited=True)
                if after is None:
                    continue
                if after != started:
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
                 snapshot=None, creation=None, alive=None, send=None, lock=None):
        self.pid = pid
        self.started_at = started_at
        self.session = session
        self.snapshot = snapshot or process_snapshot
        self.creation = creation or (lambda pid: process_start(pid, include_exited=True))
        self.alive = alive or (lambda pid: process_start(pid, include_exited=False))
        self.send = send or os.kill
        # The creating handle or durable identity already names the root.
        # Its exact creation value is rechecked before every signal; only new
        # members require a complete scan while that original root pins its PID.
        self.known: dict[int, int] = {pid: started_at}
        self.live: dict[int, int] = {}
        self.lock = lock or threading.RLock()
        self._empty_observations = 0

    def observe(self) -> str:
        """One scan; two consecutive empty scans are needed to prove absence."""
        with self.lock:
            try:
                observation = self._observe()
            except OSError:
                self._empty_observations = 0
                raise
            if observation != "identity-absent":
                self._empty_observations = 0
                return observation
            self._empty_observations += 1
            return "identity-absent" if self._empty_observations >= 2 else "empty"

    def invalidate_observation(self) -> None:
        """An unavailable prerequisite breaks consecutive empty evidence."""
        with self.lock:
            self._empty_observations = 0

    def _observe(self) -> str:
        root_before = self.creation(self.pid)
        rows = self.snapshot()
        root_after = self.creation(self.pid)
        if root_after is not None and root_before != root_after:
            raise OSError("root identity changed during session scan")
        pinned = root_before == root_after == self.started_at
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
            parent_starts = {pid: row.started_at for pid, row in rows.items()}
            # The root may have exited after a child's PPID was read and be
            # omitted as a zombie by Darwin. Its pinned creation identity still
            # proves that edge without requiring a live root table row.
            parent_starts[self.pid] = self.started_at
            while True:
                children = {pid for pid, row in rows.items()
                            if row.parent in admitted and parent_starts[row.parent] <= row.started_at}
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

    def signal(self, sig: int) -> bool:
        """Signal admitted identities even when the latest scan is unknown.

        Unknown blocks confirmation and admission, never stopping an identity
        already proved while the root pinned its PID. No scan runs here.
        """
        with self.lock:
            verified = False
            # Descendants first. Recheck every identity, including after a
            # failed scan, and let an unreadable member leave others stoppable.
            for pid, started in sorted(self.known.items(), key=lambda item: item[0] == self.pid):
                try:
                    if self.creation(pid) == started:
                        verified = True
                        self.send(pid, sig)
                except OSError:
                    continue
            return verified
