"""Exact, kernel-owned process creation values used within a boot session.

Linux uses /proc stat clock ticks, macOS uses the kernel's raw microseconds, and
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


class _MacKinfoProc(ctypes.Structure):
    # sys/sysctl.h's 648-byte kinfo_proc on Darwin's 64-bit ABIs (arm64/x86_64).
    # Only the prefix of kp_proc (sys/proc.h's extern_proc) is interpreted;
    # the remaining extern_proc fields and kp_eproc stay opaque. p_starttime
    # is a timeval: a 64-bit tv_sec and a 32-bit tv_usec, padded to 16 bytes.
    _fields_ = [
        ("start_tvsec", ctypes.c_int64), ("start_tvusec", ctypes.c_int32),
        ("_timeval_padding", ctypes.c_int32),
        ("_vmspace", ctypes.c_uint64), ("_sigacts", ctypes.c_uint64),
        ("_flag", ctypes.c_int32), ("status", ctypes.c_uint8),
        ("pid", ctypes.c_int32), ("_remaining", ctypes.c_byte * 604),
    ]


def _macos_process_start(pid: int, *, include_exited: bool) -> int | None:
    # Unlike proc_pidinfo(PROC_PIDTBSDINFO), KERN_PROC_PID includes unreaped
    # children. Both expose the same kernel start timeval, without rounding.
    library = ctypes.CDLL(None, use_errno=True)
    method = library.sysctl
    method.argtypes = [ctypes.POINTER(ctypes.c_int), ctypes.c_uint,
                       ctypes.c_void_p, ctypes.POINTER(ctypes.c_size_t),
                       ctypes.c_void_p, ctypes.c_size_t]
    method.restype = ctypes.c_int
    mib = (ctypes.c_int * 4)(1, 14, 1, pid)  # CTL_KERN, KERN_PROC, KERN_PROC_PID
    info = _MacKinfoProc()
    length = ctypes.c_size_t(ctypes.sizeof(info))
    if method(mib, len(mib), ctypes.byref(info), ctypes.byref(length), None, 0) != 0:
        error = ctypes.get_errno()
        if error == errno.ESRCH:
            return None
        raise OSError(error, "cannot inspect process creation time")
    if length.value == 0:
        return None
    if (length.value != ctypes.sizeof(info) or info.pid != pid
            or info.status not in {1, 2, 3, 4, 5}
            or info.start_tvsec <= 0 or not 0 <= info.start_tvusec < 1_000_000):
        raise OSError("incomplete process creation observation")
    if not include_exited and info.status == 5:  # sys/proc.h SZOMB
        return None
    return info.start_tvsec * 1_000_000 + info.start_tvusec


class _FileTime(ctypes.Structure):
    _fields_ = [("low", ctypes.c_uint32), ("high", ctypes.c_uint32)]


def windows_handle_start(handle: int, *, kernel) -> int:
    """Read exact FILETIME creation ticks from an already held process handle."""
    method = kernel.GetProcessTimes
    method.argtypes = [ctypes.c_void_p] + [ctypes.POINTER(_FileTime)] * 4
    method.restype = ctypes.c_int
    created, exited, kernel_time, user_time = (_FileTime() for _ in range(4))
    if not method(handle, ctypes.byref(created), ctypes.byref(exited),
                  ctypes.byref(kernel_time), ctypes.byref(user_time)):
        raise OSError(ctypes.get_last_error(), "cannot inspect process creation time")
    return (created.high << 32) | created.low


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
        return windows_handle_start(handle, kernel=kernel)
    finally:
        close = kernel.CloseHandle
        close.argtypes = [ctypes.c_void_p]
        close.restype = ctypes.c_int
        close(handle)
