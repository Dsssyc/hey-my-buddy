"""The pywin32 ownership boundary is injectable on every host OS."""
from __future__ import annotations

import sys
from types import SimpleNamespace
import unittest
from unittest import mock

from hey_my_buddy.protocol import windows_process as windows


class Win32Error(Exception):
    def __init__(self, code):
        super().__init__(code, "fixture", "Win32 operation failed")
        self.winerror = code


class Handle:
    def __init__(self, value):
        self.value = value

    def __int__(self):
        return self.value


class WindowsAPITests(unittest.TestCase):
    def setUp(self):
        self.job = mock.Mock(
            JobObjectExtendedLimitInformation=9, JobObjectBasicProcessIdList=3,
            JobObjectBasicAccountingInformation=1, JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE=0x2000,
            JOB_OBJECT_QUERY=4, JOB_OBJECT_TERMINATE=8)
        self.api, self.process = mock.Mock(), mock.Mock()
        self.kernel = mock.Mock(spec=["CreateToolhelp32Snapshot", "Thread32First",
                                      "Thread32Next", "GetProcessIdOfThread"])
        self.addCleanup(mock.patch.stopall)
        mock.patch.dict(sys.modules, {
            "pywintypes": SimpleNamespace(error=Win32Error), "win32job": self.job,
            "win32api": self.api, "win32process": self.process,
        }).start()
        mock.patch.object(windows.ctypes, "WinDLL", return_value=self.kernel, create=True).start()
        # ctypes.WinError is Windows-only; the fake keeps its OSError contract.
        mock.patch.object(windows.ctypes, "WinError", side_effect=lambda code: OSError(code, "fixture"),
                          create=True).start()
        self.boundary = windows.WindowsAPI()
        self.job_handle, self.thread = Handle(77), Handle(91)
        self.job.CreateJobObject.return_value = self.job_handle
        self.kernel.CreateToolhelp32Snapshot.return_value = 51
        self.kernel.GetProcessIdOfThread.return_value = 321
        self.api.OpenThread.return_value = self.thread
        self.process.ResumeThread.return_value = 1
        self.child = SimpleNamespace(pid=321, _handle=42)

    def test_job_owns_the_created_handle_and_sets_kill_on_last_close(self):
        limits = {"BasicLimitInformation": {"LimitFlags": 0}, "ProcessMemoryLimit": 0}
        self.job.QueryInformationJobObject.return_value = limits
        self.assertIs(self.boundary.create_job("Local\\HeyMyBuddy-fixture"), self.job_handle)
        self.job.CreateJobObject.assert_called_once_with(None, "Local\\HeyMyBuddy-fixture")
        self.job.QueryInformationJobObject.assert_called_once_with(self.job_handle, 9)
        self.assertEqual(limits["BasicLimitInformation"]["LimitFlags"], 0x2000)
        self.job.SetInformationJobObject.assert_called_once_with(self.job_handle, 9, limits)
        self.api.CloseHandle.assert_not_called()

    def test_limit_query_or_setup_failure_closes_the_new_job(self):
        for method in ("QueryInformationJobObject", "SetInformationJobObject"):
            with self.subTest(method=method):
                self.job.QueryInformationJobObject.return_value = {"BasicLimitInformation": {"LimitFlags": 0}}
                failure = Win32Error(5)
                with mock.patch.object(self.job, method, side_effect=failure):
                    with self.assertRaises(OSError) as caught:
                        self.boundary.create_job("fixture")
                self.assertIs(caught.exception.__cause__, failure)
                self.api.CloseHandle.assert_called_once_with(self.job_handle)
                self.api.CloseHandle.reset_mock()

    def test_named_job_absence_is_distinct_from_denied_or_failed_observation(self):
        self.job.OpenJobObject.return_value = self.job_handle
        self.assertIs(self.boundary.open_job("fixture"), self.job_handle)
        self.job.OpenJobObject.assert_called_once_with(12, False, "fixture")
        for code in (2, 5, 6):
            with self.subTest(code=code):
                failure = Win32Error(code)
                self.job.OpenJobObject.side_effect = failure
                if code == 2:
                    self.assertIsNone(self.boundary.open_job("fixture"))
                else:
                    with self.assertRaises(OSError) as caught:
                        self.boundary.open_job("fixture")
                    self.assertIs(caught.exception.__cause__, failure)

    def test_pid_list_and_active_count_use_the_held_job(self):
        self.job.QueryInformationJobObject.side_effect = [(321, 654), {"ActiveProcesses": 2}, Win32Error(5)]
        self.assertEqual(self.boundary.process_ids(self.job_handle), [321, 654])
        self.assertEqual(self.boundary.active(self.job_handle), 2)
        with self.assertRaises(OSError):
            self.boundary.active(self.job_handle)
        self.assertEqual(self.job.QueryInformationJobObject.call_args_list,
                         [mock.call(self.job_handle, 3), mock.call(self.job_handle, 1), mock.call(self.job_handle, 1)])

    def test_assignment_borrows_the_popen_handle_without_reopening_or_closing_it(self):
        self.boundary.assign(self.job_handle, self.child)
        self.job.AssignProcessToJobObject.assert_called_once_with(self.job_handle, 42)
        self.api.CloseHandle.assert_not_called()
        self.api.OpenProcess.assert_not_called()
        self.job.AssignProcessToJobObject.side_effect = Win32Error(5)
        with self.assertRaises(OSError):
            self.boundary.assign(self.job_handle, self.child)

    def resume(self):
        with mock.patch.object(self.boundary, "_primary_thread", return_value=19) as primary:
            self.boundary.resume(self.child)
        primary.assert_called_once_with(51, 321)

    def test_resume_checks_thread_identity_and_closes_both_owned_handles(self):
        events = mock.Mock()
        events.attach_mock(self.api.OpenThread, "open")
        events.attach_mock(self.kernel.GetProcessIdOfThread, "owner")
        events.attach_mock(self.process.ResumeThread, "resume")
        events.attach_mock(self.api.CloseHandle, "close")
        self.resume()
        self.assertEqual(events.mock_calls, [mock.call.open(0x802, False, 19), mock.call.owner(91),
            mock.call.resume(self.thread), mock.call.close(self.thread), mock.call.close(51)])

    def test_thread_open_failure_closes_snapshot_without_resuming(self):
        self.api.OpenThread.side_effect = Win32Error(5)
        with self.assertRaises(OSError):
            self.resume()
        self.api.CloseHandle.assert_called_once_with(51)
        self.process.ResumeThread.assert_not_called()

    def test_changed_thread_owner_is_never_resumed(self):
        self.kernel.GetProcessIdOfThread.return_value = 999
        with self.assertRaisesRegex(OSError, "identity changed"):
            self.resume()
        self.process.ResumeThread.assert_not_called()
        self.assertEqual(self.api.CloseHandle.call_args_list, [mock.call(self.thread), mock.call(51)])

    def test_resume_failure_or_wrong_suspend_count_still_closes_owned_handles(self):
        for outcome in (Win32Error(5), 0, 2):
            with self.subTest(outcome=outcome):
                self.process.ResumeThread.side_effect = [outcome]
                with self.assertRaises(OSError):
                    self.resume()
                self.assertEqual(self.api.CloseHandle.call_args_list, [mock.call(self.thread), mock.call(51)])
                self.api.CloseHandle.reset_mock()

    def test_termination_and_close_failures_remain_os_errors(self):
        self.boundary.terminate(self.job_handle)
        self.job.TerminateJobObject.assert_called_once_with(self.job_handle, 1)
        self.job.TerminateJobObject.side_effect = Win32Error(5)
        with self.assertRaises(OSError):
            self.boundary.terminate(self.job_handle)
        self.api.CloseHandle.side_effect = Win32Error(6)
        with self.assertRaises(OSError):
            self.boundary.close(self.job_handle)


if __name__ == "__main__":
    unittest.main()
