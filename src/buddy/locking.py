"""Cross-platform advisory file locks on an open descriptor.

``portalocker`` uses ``fcntl.flock`` on POSIX, so these locks interoperate exactly
with any other ``flock`` holder of the same file, and ``LockFileEx`` on Windows. The
lock belongs to the open file description and is released when it is unlocked or the
owning process exits, which is what lifetime-lock liveness checks rely on. A
non-blocking attempt on a held lock raises ``BlockingIOError``, as ``fcntl`` did.
"""
from __future__ import annotations

import errno

import portalocker


class _Descriptor:
    __slots__ = ("fd",)

    def __init__(self, fd: int):
        self.fd = fd

    def fileno(self) -> int:
        return self.fd


def lock(fd: int, *, shared: bool = False, blocking: bool = True) -> None:
    flags = portalocker.LockFlags.SHARED if shared else portalocker.LockFlags.EXCLUSIVE
    if not blocking:
        flags |= portalocker.LockFlags.NON_BLOCKING
    try:
        portalocker.lock(_Descriptor(fd), flags)
    except portalocker.exceptions.AlreadyLocked as error:
        raise BlockingIOError(errno.EAGAIN, "the file is locked by another owner") from error


def unlock(fd: int) -> None:
    portalocker.unlock(_Descriptor(fd))
