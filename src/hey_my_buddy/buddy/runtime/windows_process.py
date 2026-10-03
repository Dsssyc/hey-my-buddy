"""Owned Windows process trees for adapter children.

The child starts suspended and joins a private Job before its primary thread
runs. The Job handle is never reconstructed from a stored PID.
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes
import os
import subprocess

CREATE_SUSPENDED = 0x00000004
CREATE_NEW_PROCESS_GROUP = 0x00000200
TH32CS_SNAPTHREAD = 0x00000004
THREAD_SUSPEND_RESUME = 0x0002
THREAD_QUERY_LIMITED_INFORMATION = 0x0800
JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000


class _BasicLimit(ctypes.Structure):
    _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD),
                ("SchedulingClass", wintypes.DWORD)]


class _IoCounters(ctypes.Structure):
    _fields_ = [(name, ctypes.c_uint64) for name in
                ("ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
                 "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]


class _ExtendedLimit(ctypes.Structure):
    _fields_ = [("BasicLimitInformation", _BasicLimit), ("IoInfo", _IoCounters),
                ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]


class _BasicAccounting(ctypes.Structure):
    _fields_ = [("TotalUserTime", ctypes.c_int64), ("TotalKernelTime", ctypes.c_int64),
                ("ThisPeriodTotalUserTime", ctypes.c_int64), ("ThisPeriodTotalKernelTime", ctypes.c_int64),
                ("TotalPageFaultCount", wintypes.DWORD), ("TotalProcesses", wintypes.DWORD),
                ("ActiveProcesses", wintypes.DWORD), ("TotalTerminatedProcesses", wintypes.DWORD)]


class _ThreadEntry(ctypes.Structure):
    _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD),
                ("th32ThreadID", wintypes.DWORD), ("th32OwnerProcessID", wintypes.DWORD),
                ("tpBasePri", wintypes.LONG), ("tpDeltaPri", wintypes.LONG), ("dwFlags", wintypes.DWORD)]


class WindowsAPI:
    """Small injectable Win32 boundary; all returned handles are owned here."""

    def __init__(self):
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self.kernel = kernel
        signatures = {
            "CreateJobObjectW": (wintypes.HANDLE, [ctypes.c_void_p, wintypes.LPCWSTR]),
            "SetInformationJobObject": (wintypes.BOOL, [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]),
            "AssignProcessToJobObject": (wintypes.BOOL, [wintypes.HANDLE, wintypes.HANDLE]),
            "QueryInformationJobObject": (wintypes.BOOL, [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p]),
            "TerminateJobObject": (wintypes.BOOL, [wintypes.HANDLE, wintypes.UINT]),
            "CreateToolhelp32Snapshot": (wintypes.HANDLE, [wintypes.DWORD, wintypes.DWORD]),
            "Thread32First": (wintypes.BOOL, [wintypes.HANDLE, ctypes.POINTER(_ThreadEntry)]),
            "Thread32Next": (wintypes.BOOL, [wintypes.HANDLE, ctypes.POINTER(_ThreadEntry)]),
            "OpenThread": (wintypes.HANDLE, [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]),
            "GetProcessIdOfThread": (wintypes.DWORD, [wintypes.HANDLE]),
            "ResumeThread": (wintypes.DWORD, [wintypes.HANDLE]),
            "CloseHandle": (wintypes.BOOL, [wintypes.HANDLE]),
        }
        for name, (result, arguments) in signatures.items():
            method = getattr(kernel, name)
            method.restype, method.argtypes = result, arguments

    @staticmethod
    def _error():
        raise ctypes.WinError(ctypes.get_last_error())

    def create_job(self):
        handle = self.kernel.CreateJobObjectW(None, None)
        if not handle:
            self._error()
        try:
            limits = _ExtendedLimit()
            limits.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            if not self.kernel.SetInformationJobObject(handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
                self._error()
            return handle
        except BaseException:
            self.kernel.CloseHandle(handle)
            raise

    def assign(self, job, process):
        # Popen retains this exact process handle; no OpenProcess/PID recovery.
        if not self.kernel.AssignProcessToJobObject(job, int(process._handle)):
            self._error()

    def resume(self, process):
        snapshot = self.kernel.CreateToolhelp32Snapshot(TH32CS_SNAPTHREAD, 0)
        if snapshot == ctypes.c_void_p(-1).value:
            self._error()
        try:
            entry = _ThreadEntry()
            entry.dwSize = ctypes.sizeof(entry)
            if not self.kernel.Thread32First(snapshot, ctypes.byref(entry)):
                self._error()
            candidates = []
            while True:
                if entry.th32OwnerProcessID == process.pid:
                    candidates.append(entry.th32ThreadID)
                entry.dwSize = ctypes.sizeof(entry)
                if not self.kernel.Thread32Next(snapshot, ctypes.byref(entry)):
                    if ctypes.get_last_error() != 18:  # ERROR_NO_MORE_FILES
                        self._error()
                    break
            if len(candidates) != 1:
                raise OSError("suspended child has no unique primary thread")
            thread = self.kernel.OpenThread(THREAD_SUSPEND_RESUME | THREAD_QUERY_LIMITED_INFORMATION,
                                            False, candidates[0])
            if not thread:
                self._error()
            try:
                if self.kernel.GetProcessIdOfThread(thread) != process.pid:
                    raise OSError("suspended child thread identity changed")
                if self.kernel.ResumeThread(thread) != 1:
                    raise OSError("suspended child did not resume exactly once")
            finally:
                self.kernel.CloseHandle(thread)
        finally:
            self.kernel.CloseHandle(snapshot)

    def active(self, job):
        accounting = _BasicAccounting()
        if not self.kernel.QueryInformationJobObject(job, 1, ctypes.byref(accounting),
                                                      ctypes.sizeof(accounting), None):
            self._error()
        return accounting.ActiveProcesses

    def terminate(self, job):
        if not self.kernel.TerminateJobObject(job, 1):
            self._error()

    def close(self, job):
        if not self.kernel.CloseHandle(job):
            self._error()


class OwnedJob:
    def __init__(self, api, handle):
        self.api, self.handle = api, handle
        self.empty_confirmed = False

    def active(self):
        if self.handle is None:
            return 0 if self.empty_confirmed else None
        return self.api.active(self.handle)

    def terminate(self):
        if self.handle is not None:
            self.api.terminate(self.handle)

    def close(self, *, confirmed=False):
        if self.handle is not None:
            self.api.close(self.handle)
            self.handle = None
            self.empty_confirmed = confirmed

    def __del__(self):
        if self.handle is not None:
            try:
                self.close()
            except OSError:
                pass


def owned_popen(*args, windows_api=None, **kwargs):
    """Create a process with tree ownership before any user code can run."""
    if os.name != "nt" and windows_api is None:
        return subprocess.Popen(*args, **kwargs)
    api = windows_api if windows_api is not None else WindowsAPI()
    job = OwnedJob(api, api.create_job())
    process = None
    assigned = False
    try:
        kwargs = {**kwargs, "creationflags": kwargs.get("creationflags", 0) |
                  CREATE_SUSPENDED | CREATE_NEW_PROCESS_GROUP, "start_new_session": False}
        process = subprocess.Popen(*args, **kwargs)
        api.assign(job.handle, process)
        assigned = True
        api.resume(process)
        process._buddy_job = job
        return process
    except BaseException as error:
        stopped = True
        if process is not None:
            try:
                if assigned:
                    job.terminate()
                else:
                    process.kill()  # Popen's held process handle, never a persisted PID.
                process.wait(timeout=2)
                if assigned:
                    stopped = job.active() == 0
            except (OSError, subprocess.TimeoutExpired):
                stopped = False
        try:
            job.close()
        except OSError:
            stopped = False
        if not stopped:
            raise RuntimeError("suspended child shutdown is unconfirmed after ownership failure") from error
        raise
