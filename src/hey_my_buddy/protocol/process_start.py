"""Exact, kernel-owned process creation values used within a boot session.

Linux uses /proc stat clock ticks, macOS uses libproc's raw microseconds, and
Windows uses GetProcessTimes FILETIME ticks.  The platform in bootId identifies
the unit; no wall-clock conversion or floating-point rounding enters storage.
"""
from __future__ import annotations

import ctypes
import errno
import sys


def process_start(pid: int, *, include_exited: bool) -> int | None:
    if sys.platform == "linux":
        try:
            with open(f"/proc/{pid}/stat", "rb") as stream:
                stat = stream.read()
        except FileNotFoundError:
            return None
        # comm is parenthesized and can contain spaces or closing parentheses.
        fields = stat[stat.rfind(b")") + 2:].split()
        if not include_exited and fields[0] in {b"Z", b"X", b"x"}:
            return None
        return int(fields[19])  # field 22, counting state as field 3
    if sys.platform == "darwin":
        return _macos_process_start(pid, include_exited=include_exited)
    if sys.platform == "win32":
        return _windows_process_start(pid, include_exited=include_exited)
    raise OSError(f"unsupported platform: {sys.platform}")


class _MacBSDInfo(ctypes.Structure):
    # sys/proc_info.h: struct proc_bsdinfo; sys/param.h: MAXCOMLEN = 16.
    _fields_ = [
        ("pbi_flags", ctypes.c_uint32), ("pbi_status", ctypes.c_uint32),
        ("pbi_xstatus", ctypes.c_uint32), ("pbi_pid", ctypes.c_uint32),
        ("pbi_ppid", ctypes.c_uint32),
        ("pbi_uid", ctypes.c_uint32), ("pbi_gid", ctypes.c_uint32),
        ("pbi_ruid", ctypes.c_uint32), ("pbi_rgid", ctypes.c_uint32),
        ("pbi_svuid", ctypes.c_uint32), ("pbi_svgid", ctypes.c_uint32),
        ("rfu_1", ctypes.c_uint32),
        ("pbi_comm", ctypes.c_char * 16), ("pbi_name", ctypes.c_char * 32),
        ("pbi_nfiles", ctypes.c_uint32), ("pbi_pgid", ctypes.c_uint32),
        ("pbi_pjobc", ctypes.c_uint32), ("e_tdev", ctypes.c_uint32),
        ("e_tpgid", ctypes.c_uint32), ("pbi_nice", ctypes.c_int32),
        ("pbi_start_tvsec", ctypes.c_uint64),
        ("pbi_start_tvusec", ctypes.c_uint64),
    ]


def _macos_process_start(pid: int, *, include_exited: bool) -> int | None:
    library = ctypes.CDLL("/usr/lib/libproc.dylib", use_errno=True)
    method = library.proc_pidinfo
    method.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_uint64,
                       ctypes.c_void_p, ctypes.c_int]
    method.restype = ctypes.c_int
    info = _MacBSDInfo()
    count = method(pid, 3, 0, ctypes.byref(info), ctypes.sizeof(info))
    if count == 0:
        error = ctypes.get_errno()
        if error == errno.ESRCH:
            return None
        raise OSError(error, "cannot inspect process creation time")
    if count != ctypes.sizeof(info) or info.pbi_pid != pid:
        raise OSError("incomplete process creation observation")
    if not include_exited and info.pbi_status == 5:  # sys/proc.h SZOMB
        return None
    return info.pbi_start_tvsec * 1_000_000 + info.pbi_start_tvusec


class _FileTime(ctypes.Structure):
    _fields_ = [("low", ctypes.c_uint32), ("high", ctypes.c_uint32)]


def _windows_process_start(pid: int, *, include_exited: bool) -> int | None:
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    open_process = kernel.OpenProcess
    open_process.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
    open_process.restype = ctypes.c_void_p
    handle = open_process(0x1000 | 0x00100000, 0, pid)  # QUERY_LIMITED_INFORMATION | SYNCHRONIZE
    if not handle:
        error = ctypes.get_last_error()
        if error == 87:  # ERROR_INVALID_PARAMETER: no such PID
            return None
        raise OSError(error, "cannot open process")
    try:
        if not include_exited:
            wait = kernel.WaitForSingleObject
            wait.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
            wait.restype = ctypes.c_uint32
            result = wait(handle, 0)
            if result == 0:  # WAIT_OBJECT_0
                return None
            if result != 0x102:  # WAIT_TIMEOUT
                raise OSError(ctypes.get_last_error(), "cannot inspect process liveness")
        method = kernel.GetProcessTimes
        method.argtypes = [ctypes.c_void_p] + [ctypes.POINTER(_FileTime)] * 4
        method.restype = ctypes.c_int
        created, exited, kernel_time, user_time = (_FileTime() for _ in range(4))
        if not method(handle, ctypes.byref(created), ctypes.byref(exited),
                      ctypes.byref(kernel_time), ctypes.byref(user_time)):
            raise OSError(ctypes.get_last_error(), "cannot inspect process creation time")
        return (created.high << 32) | created.low
    finally:
        close = kernel.CloseHandle
        close.argtypes = [ctypes.c_void_p]
        close.restype = ctypes.c_int
        close(handle)
