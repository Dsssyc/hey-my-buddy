"""Neutral Win32 Job and process-handle operations used at the OS boundary."""
from __future__ import annotations

import ctypes
from ctypes import wintypes

TH32CS_SNAPTHREAD = 0x00000004
THREAD_SUSPEND_RESUME = 0x0002
THREAD_QUERY_LIMITED_INFORMATION = 0x0800
ERROR_FILE_NOT_FOUND = 2
ERROR_ALREADY_EXISTS = 183


class _ThreadEntry(ctypes.Structure):
    _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD),
                ("th32ThreadID", wintypes.DWORD), ("th32OwnerProcessID", wintypes.DWORD),
                ("tpBasePri", wintypes.LONG), ("tpDeltaPri", wintypes.LONG), ("dwFlags", wintypes.DWORD)]


class WindowsAPI:
    """Small injectable Win32 boundary; all returned handles are owned here."""

    def __init__(self):
        import pywintypes
        import win32api
        import win32job
        import win32process

        self.error = pywintypes.error
        self.api, self.job, self.process = win32api, win32job, win32process
        # pywin32 exposes neither Toolhelp thread enumeration nor the owning
        # process query needed to verify the thread found for a suspended Popen.
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self.kernel = kernel
        signatures = {
            "CreateToolhelp32Snapshot": (wintypes.HANDLE, [wintypes.DWORD, wintypes.DWORD]),
            "Thread32First": (wintypes.BOOL, [wintypes.HANDLE, ctypes.POINTER(_ThreadEntry)]),
            "Thread32Next": (wintypes.BOOL, [wintypes.HANDLE, ctypes.POINTER(_ThreadEntry)]),
            "GetProcessIdOfThread": (wintypes.DWORD, [wintypes.HANDLE]),
        }
        for name, (result, arguments) in signatures.items():
            method = getattr(kernel, name)
            method.restype, method.argtypes = result, arguments

    @staticmethod
    def _error():
        raise ctypes.WinError(ctypes.get_last_error())

    def _call(self, method, *args):
        # The ownership callers handle OSError; pywintypes.error is separate.
        try:
            return method(*args)
        except self.error as error:
            raise ctypes.WinError(error.winerror) from error

    def create_job(self, name):
        handle = self._call(self.job.CreateJobObject, None, name)
        if self.api.GetLastError() == ERROR_ALREADY_EXISTS:
            self.close(handle)
            raise FileExistsError(ERROR_ALREADY_EXISTS, f"Job already exists: {name}")
        try:
            limits = self._call(self.job.QueryInformationJobObject, handle,
                                self.job.JobObjectExtendedLimitInformation)
            limits["BasicLimitInformation"]["LimitFlags"] = self.job.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            self._call(self.job.SetInformationJobObject, handle,
                       self.job.JobObjectExtendedLimitInformation, limits)
            return handle
        except BaseException:
            self.close(handle)
            raise

    def open_job(self, name):
        try:
            return self.job.OpenJobObject(self.job.JOB_OBJECT_QUERY | self.job.JOB_OBJECT_TERMINATE, False, name)
        except self.error as error:
            if error.winerror == ERROR_FILE_NOT_FOUND:
                return None
            raise ctypes.WinError(error.winerror) from error

    def process_ids(self, job):
        return list(self._call(self.job.QueryInformationJobObject, job, self.job.JobObjectBasicProcessIdList))

    def assign(self, job, process):
        # Popen retains this exact process handle; no OpenProcess/PID recovery.
        self._call(self.job.AssignProcessToJobObject, job, int(process._handle))

    def _primary_thread(self, snapshot, pid):
        entry = _ThreadEntry()
        entry.dwSize = ctypes.sizeof(entry)
        if not self.kernel.Thread32First(snapshot, ctypes.byref(entry)):
            self._error()
        candidates = []
        while True:
            if entry.th32OwnerProcessID == pid:
                candidates.append(entry.th32ThreadID)
            entry.dwSize = ctypes.sizeof(entry)
            if not self.kernel.Thread32Next(snapshot, ctypes.byref(entry)):
                if ctypes.get_last_error() != 18:  # ERROR_NO_MORE_FILES
                    self._error()
                break
        if len(candidates) != 1:
            raise OSError("suspended child has no unique primary thread")
        return candidates[0]

    def resume(self, process):
        snapshot = self.kernel.CreateToolhelp32Snapshot(TH32CS_SNAPTHREAD, 0)
        if snapshot == ctypes.c_void_p(-1).value:
            self._error()
        try:
            thread_id = self._primary_thread(snapshot, process.pid)
            thread = self._call(self.api.OpenThread, THREAD_SUSPEND_RESUME | THREAD_QUERY_LIMITED_INFORMATION,
                                False, thread_id)
            try:
                if self.kernel.GetProcessIdOfThread(int(thread)) != process.pid:
                    raise OSError("suspended child thread identity changed")
                if self._call(self.process.ResumeThread, thread) != 1:
                    raise OSError("suspended child did not resume exactly once")
            finally:
                self.close(thread)
        finally:
            self.close(snapshot)

    def active(self, job):
        return self._call(self.job.QueryInformationJobObject, job,
                          self.job.JobObjectBasicAccountingInformation)["ActiveProcesses"]

    def terminate(self, job):
        self._call(self.job.TerminateJobObject, job, 1)

    def close(self, job):
        self._call(self.api.CloseHandle, job)

