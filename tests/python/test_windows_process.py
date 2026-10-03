"""Process ownership boundary tests runnable without a Windows host."""
from __future__ import annotations

import os
import signal
import subprocess
import sys
import threading
import time
import unittest
from unittest import mock

from hey_my_buddy.buddy.harnesses.base import ProcessHandle
from hey_my_buddy.buddy.harnesses.codex.protocol import CodexProtocolError, Connection
from hey_my_buddy.buddy.runtime.windows_process import (
    CREATE_NEW_PROCESS_GROUP,
    CREATE_SUSPENDED,
    owned_popen,
)


class FakeProcess:
    pid = 321
    _handle = 42

    def __init__(self):
        self.returncode = None
        self.signals = []
        self.kills = 0

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        if self.returncode is None:
            raise subprocess.TimeoutExpired("fake", timeout)
        return self.returncode

    def kill(self):
        self.kills += 1
        self.returncode = 1

    def send_signal(self, sig):
        self.signals.append(sig)


class FakeAPI:
    def __init__(self, process):
        self.process = process
        self.events = []
        self.count = 1
        self.fail_at = None

    def create_job(self):
        self.events.append("create")
        return 77

    def assign(self, job, process):
        self.events.append(("assign", job, process._handle))
        if self.fail_at == "assign":
            raise OSError("assign failed")

    def resume(self, process):
        self.events.append(("resume", process._handle))
        if self.fail_at == "resume":
            raise OSError("resume failed")

    def active(self, job):
        self.events.append(("active", job))
        if self.fail_at == "active":
            raise OSError("observation failed")
        return self.count

    def terminate(self, job):
        self.events.append(("terminate", job))
        self.process.returncode = 1
        self.count = 0

    def close(self, job):
        self.events.append(("close", job))


class WindowsProcessTests(unittest.TestCase):
    def launch(self, api, process):
        with mock.patch("hey_my_buddy.buddy.runtime.windows_process.subprocess.Popen", return_value=process) as popen:
            child = owned_popen(["child"], windows_api=api, start_new_session=True)
        flags = popen.call_args.kwargs["creationflags"]
        self.assertEqual(flags & (CREATE_SUSPENDED | CREATE_NEW_PROCESS_GROUP),
                         CREATE_SUSPENDED | CREATE_NEW_PROCESS_GROUP)
        self.assertFalse(popen.call_args.kwargs["start_new_session"])
        return child

    def test_assigns_held_process_before_resuming(self):
        process = FakeProcess()
        api = FakeAPI(process)
        child = self.launch(api, process)
        self.assertEqual(api.events[:3], ["create", ("assign", 77, 42), ("resume", 42)])
        self.assertIs(child._buddy_job.api, api)
        child._buddy_job.close()

    def test_assignment_failure_kills_suspended_child_before_returning(self):
        process = FakeProcess()
        api = FakeAPI(process)
        api.fail_at = "assign"
        with mock.patch("hey_my_buddy.buddy.runtime.windows_process.subprocess.Popen", return_value=process):
            with self.assertRaises(OSError):
                owned_popen(["child"], windows_api=api)
        self.assertEqual(process.kills, 1)
        self.assertNotIn(("resume", 42), api.events)
        self.assertEqual(api.events[-1], ("close", 77))

    def test_missing_windows_api_fails_before_spawn(self):
        with mock.patch("hey_my_buddy.buddy.runtime.windows_process.WindowsAPI", side_effect=OSError("unavailable")), \
             mock.patch("hey_my_buddy.buddy.runtime.windows_process.subprocess.Popen") as popen, \
             mock.patch("hey_my_buddy.buddy.runtime.windows_process.os.name", "nt"):
            with self.assertRaises(OSError):
                owned_popen(["child"])
        popen.assert_not_called()

    def test_resume_failure_terminates_assigned_job(self):
        process = FakeProcess()
        api = FakeAPI(process)
        api.fail_at = "resume"
        with mock.patch("hey_my_buddy.buddy.runtime.windows_process.subprocess.Popen", return_value=process):
            with self.assertRaises(OSError):
                owned_popen(["child"], windows_api=api)
        self.assertLess(api.events.index(("assign", 77, 42)), api.events.index(("resume", 42)))
        self.assertIn(("terminate", 77), api.events)
        self.assertEqual(api.events[-1], ("close", 77))

    def test_live_descendant_and_failed_observation_do_not_confirm_shutdown(self):
        process = FakeProcess()
        api = FakeAPI(process)
        child = self.launch(api, process)
        handle = ProcessHandle(child, own_group=True, log_paths={})
        process.returncode = 0  # The leader exited; a child is still active.
        self.assertFalse(handle.shutdown_confirmed(settle_seconds=0))
        api.fail_at = "active"
        self.assertFalse(handle.shutdown_confirmed(settle_seconds=0))
        api.fail_at = None
        api.count = 0
        self.assertTrue(handle.shutdown_confirmed(settle_seconds=0))
        self.assertEqual(api.events[-1], ("close", 77))

    def test_termination_touches_only_owned_job(self):
        process = FakeProcess()
        api = FakeAPI(process)
        child = self.launch(api, process)
        handle = ProcessHandle(child, own_group=True, log_paths={})
        with mock.patch.object(signal, "CTRL_BREAK_EVENT", 1, create=True), \
             mock.patch("hey_my_buddy.buddy.harnesses.base.os.killpg", side_effect=AssertionError("PID signaling")):
            handle.terminate(grace_seconds=0)
        self.assertEqual(process.signals, [1])
        self.assertIn(("terminate", 77), api.events)
        self.assertTrue(handle.shutdown_confirmed(settle_seconds=0))

    def test_posix_group_regression(self):
        if os.name == "nt":
            self.skipTest("POSIX only")
        process = owned_popen([sys.executable, "-c", "pass"], start_new_session=True)
        handle = ProcessHandle(process, own_group=True, log_paths={})
        self.assertEqual(handle.wait(timeout=5), 0)
        self.assertTrue(handle.shutdown_confirmed(settle_seconds=1))


class WindowsPipeTests(unittest.TestCase):
    def test_full_pipe_honors_deadline_and_cancel_without_windows_select(self):
        from hey_my_buddy.buddy.harnesses.codex import protocol as codex_protocol
        from hey_my_buddy.buddy.harnesses.claude import protocol as claude_protocol
        from hey_my_buddy.buddy.harnesses.zcode import protocol as zcode_protocol
        # CPython >=3.12 supports nonblocking Windows pipes. Exercise the same
        # branch with real nonblocking pipes; it must never use socket select.
        for module, error_type in ((codex_protocol, codex_protocol.CodexProtocolError),
                                   (claude_protocol, claude_protocol.ClaudeProtocolError),
                                   (zcode_protocol, zcode_protocol.NativeError)):
            for cancel in (False, True):
                with self.subTest(protocol=module.__name__, cancel=cancel):
                    read_fd, write_fd = os.pipe()
                    os.set_blocking(write_fd, False)
                    kind = getattr(module, 'NativeConnection', None) or module.Connection
                    connection = kind.__new__(kind)
                    connection.process = mock.Mock(stdin=mock.Mock())
                    connection.process.stdin.fileno.return_value = write_fd
                    connection.cancelled = threading.Event()
                    connection.deadline = time.monotonic() + (5 if cancel else .1)
                    timer = threading.Timer(.05, connection.cancelled.set) if cancel else None
                    try:
                        if timer: timer.start()
                        with mock.patch.object(module, '_WINDOWS_PIPE', True), \
                             mock.patch.object(module.select, 'select', side_effect=AssertionError('Windows pipes cannot use select')):
                            started = time.monotonic()
                            with self.assertRaises(error_type) as error:
                                connection.send({'fill': 'x' * 1000000})
                        expected = ('cancelled' if cancel else 'timeout') if module is zcode_protocol else ('user-cancel' if cancel else 'deadline')
                        self.assertEqual(error.exception.code, expected)
                        self.assertLess(time.monotonic() - started, .5)
                    finally:
                        if timer: timer.join()
                        os.close(read_fd)
                        os.close(write_fd)
