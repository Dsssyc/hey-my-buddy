"""Observe a creator's exited child without reaping its PID."""
from __future__ import annotations

import ctypes
import os
import sys


class _MacSiginfo(ctypes.Structure):
    # Darwin sys/signal.h, 64-bit siginfo_t (arm64 and x86_64).
    _fields_ = [("signo", ctypes.c_int), ("error", ctypes.c_int),
                ("code", ctypes.c_int), ("pid", ctypes.c_int),
                ("uid", ctypes.c_uint), ("status", ctypes.c_int),
                ("address", ctypes.c_void_p), ("value", ctypes.c_void_p),
                ("band", ctypes.c_long), ("padding", ctypes.c_ulong * 7)]


def child_exit_code(pid: int) -> int | None:
    if sys.platform == "darwin":
        # Python builds may omit os.waitid on macOS; Darwin's public waitid
        # remains available. sys/wait.h: P_PID=1, WEXITED=4, WNOHANG=1,
        # WNOWAIT=32. WNOWAIT deliberately leaves the child waitable.
        libc = ctypes.CDLL(None, use_errno=True)
        method = libc.waitid
        method.argtypes = [ctypes.c_uint, ctypes.c_uint, ctypes.POINTER(_MacSiginfo), ctypes.c_int]
        method.restype = ctypes.c_int
        info = _MacSiginfo()
        if method(1, pid, ctypes.byref(info), 4 | 1 | 32) != 0:
            raise OSError(ctypes.get_errno(), "cannot observe owned child exit")
        if info.pid == 0:
            return None
        return info.status if info.code == 1 else -info.status  # CLD_EXITED
    info = os.waitid(os.P_PID, pid, os.WEXITED | os.WNOHANG | os.WNOWAIT)
    if info is None:
        return None
    return info.si_status if info.si_code == os.CLD_EXITED else -info.si_status
