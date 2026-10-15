"""Process ownership boundary tests runnable without a Windows host."""
from __future__ import annotations

import os
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from hey_my_buddy.buddy.harnesses.base import ProcessHandle
from hey_my_buddy.buddy.harnesses.codex.protocol import CodexProtocolError, Connection
from hey_my_buddy.protocol.owned_process import (
    CREATE_NEW_PROCESS_GROUP,
    CREATE_SUSPENDED,
    ProcessCreationEvidence,
    owned_popen,
)
from hey_my_buddy.protocol import process_controller


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

    def create_job(self, name):
        self.name = name
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
    def test_unconfirmed_handle_construction_failure_returns_held_handle(self):
        process = FakeProcess()
        with mock.patch("hey_my_buddy.protocol.process_identity._creation_time",
                        side_effect=OSError("identity unavailable")), \
             mock.patch.object(ProcessHandle, "terminate", side_effect=OSError("stop unknown")):
            with self.assertRaises(RuntimeError) as caught:
                ProcessHandle(process, own_group=True, log_paths={})
        self.assertTrue(caught.exception.owned_popen_evidence.created)
        self.assertFalse(caught.exception.owned_popen_evidence.shutdown_confirmed)
        self.assertIs(caught.exception.execution_handle.process, process)

    @unittest.skipIf(os.name == "nt", "POSIX process-group observation")
    def test_handle_construction_failure_stops_spawned_group(self):
        children = []
        original = process_controller.owned_popen

        def spawn(*args, **kwargs):
            child = original(*args, **kwargs)
            children.append(child)
            return child

        with tempfile.TemporaryDirectory() as directory:
            logs = {"stdout": str(Path(directory) / "stdout.log"),
                    "stderr": str(Path(directory) / "stderr.log")}
            with mock.patch.object(process_controller, "owned_popen", side_effect=spawn), \
                 mock.patch("hey_my_buddy.protocol.process_identity._creation_time",
                            side_effect=OSError("identity unavailable")):
                with self.assertRaises(OSError) as caught:
                    process_controller.launch_controller(
                        prepare=lambda: ([sys.executable, "-c", "import time; time.sleep(30)"],
                                         directory, dict(os.environ)), log_paths=logs)
        self.assertEqual(len(children), 1)
        self.assertTrue(caught.exception.owned_popen_evidence.created)
        self.assertTrue(caught.exception.owned_popen_evidence.shutdown_confirmed)
        self.assertIsNotNone(children[0].poll())

    @unittest.skipIf(os.name == "nt", "POSIX process-group observation")
    def test_controller_guards_popen_when_handle_constructor_itself_raises(self):
        children = []
        original = process_controller.owned_popen

        def spawn(*args, **kwargs):
            child = original(*args, **kwargs)
            children.append(child)
            return child

        with tempfile.TemporaryDirectory() as directory:
            logs = {"stdout": str(Path(directory) / "stdout.log"),
                    "stderr": str(Path(directory) / "stderr.log")}
            with mock.patch.object(process_controller, "owned_popen", side_effect=spawn), \
                 mock.patch.object(process_controller, "ProcessHandle",
                                   side_effect=ValueError("constructor refused")):
                with self.assertRaises(ValueError) as caught:
                    process_controller.launch_controller(
                        prepare=lambda: ([sys.executable, "-c", "import time; time.sleep(30)"],
                                         directory, dict(os.environ)), log_paths=logs)
        self.assertEqual(len(children), 1)
        self.assertTrue(caught.exception.owned_popen_evidence.created)
        self.assertTrue(caught.exception.owned_popen_evidence.shutdown_confirmed)
        self.assertIsNotNone(children[0].poll())

    def launch(self, api, process):
        with mock.patch("hey_my_buddy.protocol.owned_process.subprocess.Popen", return_value=process) as popen:
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
        self.assertEqual(child._buddy_job.name, api.name)
        self.assertTrue(api.name.startswith("Local\\HeyMyBuddy-"))
        child._buddy_job.close()

    def test_preselected_job_name_is_the_job_assigned_before_resume(self):
        process = FakeProcess()
        api = FakeAPI(process)
        name = "Local\\HeyMyBuddy-00000000-0000-4000-8000-000000000001"
        with mock.patch("hey_my_buddy.protocol.owned_process.subprocess.Popen", return_value=process):
            child = owned_popen(["child"], windows_api=api, job_name=name)
        self.assertEqual(api.name, name)
        self.assertEqual(api.events[:3], ["create", ("assign", 77, 42), ("resume", 42)])
        self.assertEqual(child._buddy_job.name, name)
        child._buddy_job.close()

    def test_assignment_failure_kills_suspended_child_before_returning(self):
        process = FakeProcess()
        api = FakeAPI(process)
        api.fail_at = "assign"
        with mock.patch("hey_my_buddy.protocol.owned_process.subprocess.Popen", return_value=process):
            with self.assertRaises(OSError) as caught:
                owned_popen(["child"], windows_api=api)
        self.assertEqual(caught.exception.owned_popen_evidence, ProcessCreationEvidence(True, True, 1))
        self.assertEqual(process.kills, 1)
        self.assertNotIn(("resume", 42), api.events)
        self.assertEqual(api.events[-1], ("close", 77))

    def test_missing_windows_api_fails_before_spawn(self):
        with mock.patch("hey_my_buddy.protocol.owned_process.WindowsAPI", side_effect=OSError("unavailable")), \
             mock.patch("hey_my_buddy.protocol.owned_process.subprocess.Popen") as popen, \
             mock.patch("hey_my_buddy.protocol.owned_process.os.name", "nt"):
            with self.assertRaises(OSError) as caught:
                owned_popen(["child"])
        popen.assert_not_called()
        self.assertEqual(caught.exception.owned_popen_evidence, ProcessCreationEvidence(False, True, None))

    def test_job_creation_failure_proves_no_child_created(self):
        api = FakeAPI(FakeProcess())
        error = OSError("fixture job creation refused")
        with mock.patch.object(api, "create_job", side_effect=error), \
             mock.patch("hey_my_buddy.protocol.owned_process.subprocess.Popen") as popen:
            with self.assertRaises(OSError) as caught:
                owned_popen(["child"], windows_api=api)
        popen.assert_not_called()
        self.assertIs(caught.exception, error)
        self.assertEqual(error.owned_popen_evidence, ProcessCreationEvidence(False, True, None))

    def test_popen_failure_proves_no_child_even_if_empty_job_close_fails(self):
        for close_fails in (False, True):
            with self.subTest(close_fails=close_fails):
                api = FakeAPI(FakeProcess())
                error = OSError("fixture process creation refused")
                with mock.patch("hey_my_buddy.protocol.owned_process.subprocess.Popen", side_effect=error), \
                     mock.patch.object(api, "close", side_effect=OSError("fixture close refused") if close_fails else None):
                    with self.assertRaises(OSError) as caught:
                        owned_popen(["child"], windows_api=api)
                self.assertIs(caught.exception, error)
                self.assertEqual(error.owned_popen_evidence, ProcessCreationEvidence(False, True, None))
                self.assertEqual(api.events, ["create"])

    def test_posix_popen_failure_carries_no_child_evidence(self):
        if os.name == "nt":
            self.skipTest("POSIX only")
        error = OSError("fixture process creation refused")
        with mock.patch("hey_my_buddy.protocol.owned_process.subprocess.Popen", side_effect=error):
            with self.assertRaises(OSError) as caught:
                owned_popen(["child"])
        self.assertIs(caught.exception, error)
        self.assertEqual(error.owned_popen_evidence, ProcessCreationEvidence(False, True, None))

    def test_resume_failure_terminates_assigned_job(self):
        process = FakeProcess()
        api = FakeAPI(process)
        api.fail_at = "resume"
        with mock.patch("hey_my_buddy.protocol.owned_process.subprocess.Popen", return_value=process):
            with self.assertRaises(OSError) as caught:
                owned_popen(["child"], windows_api=api)
        self.assertEqual(caught.exception.owned_popen_evidence, ProcessCreationEvidence(True, True, 1))
        self.assertLess(api.events.index(("assign", 77, 42)), api.events.index(("resume", 42)))
        self.assertIn(("terminate", 77), api.events)
        self.assertEqual(api.events[-1], ("close", 77))

    def test_assignment_failure_with_unknown_cleanup_carries_created_evidence(self):
        for failure in ("kill", "wait", "close"):
            with self.subTest(failure=failure):
                process = FakeProcess()
                api = FakeAPI(process)
                api.fail_at = "assign"
                target = api if failure == "close" else process
                error = subprocess.TimeoutExpired("fake", 2) if failure == "wait" else OSError("fixture cleanup refused")
                with mock.patch("hey_my_buddy.protocol.owned_process.subprocess.Popen", return_value=process), \
                     mock.patch.object(target, failure, side_effect=error):
                    with self.assertRaises(RuntimeError) as caught:
                        owned_popen(["child"], windows_api=api)
                evidence = caught.exception.owned_popen_evidence
                self.assertEqual(evidence, ProcessCreationEvidence(True, False, process.returncode))
                self.assertEqual(caught.exception.__cause__.args, ("assign failed",))
                self.assertIn(("assign", 77, 42), api.events)
                self.assertNotIn(("resume", 42), api.events)

    def test_resume_failure_with_unknown_cleanup_carries_created_evidence(self):
        for failure in ("terminate", "wait", "active", "nonempty", "close"):
            with self.subTest(failure=failure):
                process = FakeProcess()
                api = FakeAPI(process)
                api.fail_at = "resume"
                target = process if failure == "wait" else api
                method = "active" if failure == "nonempty" else failure
                options = {"return_value": 1} if failure == "nonempty" else {
                    "side_effect": subprocess.TimeoutExpired("fake", 2) if failure == "wait" else OSError("fixture cleanup refused")}
                with mock.patch("hey_my_buddy.protocol.owned_process.subprocess.Popen", return_value=process), \
                     mock.patch.object(target, method, **options):
                    with self.assertRaises(RuntimeError) as caught:
                        owned_popen(["child"], windows_api=api)
                self.assertEqual(caught.exception.owned_popen_evidence,
                                 ProcessCreationEvidence(True, False, process.returncode))
                self.assertEqual(caught.exception.__cause__.args, ("resume failed",))
                self.assertIn(("assign", 77, 42), api.events)
                self.assertIn(("resume", 42), api.events)

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
             mock.patch("hey_my_buddy.protocol.process_handle.os.killpg", side_effect=AssertionError("PID signaling")):
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
