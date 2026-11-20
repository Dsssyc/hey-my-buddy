"""The owned stop sequence, with real stdio children and deterministic escalation."""
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
from types import SimpleNamespace
from unittest.mock import Mock, patch

import support
from hey_my_buddy.protocol.owned_process import owned_popen
from hey_my_buddy.protocol.process_handle import ProcessHandle


class StopSequenceTests(unittest.TestCase):
    def stop(self, channel, *, windows=False, eof_exits=False, root_exits=False, unknown=False):
        events = []
        clock = [0.0]
        state = {"alive": True, "exit": None}

        def note(event):
            events.append((event, round(clock[0], 3)))

        def eof():
            note("eof")
            if eof_exits:
                state.update(alive=False, exit=0)
            elif root_exits:
                state["exit"] = 0

        def wait(timeout):
            if state["exit"] is None:
                clock[0] += timeout
                raise subprocess.TimeoutExpired("fixture", timeout)
            return state["exit"]

        def send(sig):
            note(signal.Signals(sig).name)
            if sig == signal.SIGKILL:
                state.update(alive=False, exit=-sig)

        def force():
            note("job.terminate")
            state.update(alive=False, exit=1)

        def active():
            if unknown:
                raise OSError("Job cannot be observed")
            return int(state["alive"])

        process = Mock(pid=123, returncode=None)
        process.poll.side_effect = lambda: state["exit"]
        process.wait.side_effect = wait
        process.stdin.close.side_effect = eof
        if windows:
            process._buddy_job = SimpleNamespace(terminate=force, active=active,
                                                close=lambda **kw: note("job.close"))
        with patch("hey_my_buddy.protocol.process_identity._creation_time", return_value=10):
            handle = ProcessHandle(process, log_paths={}, stop_channel=channel)
        if not windows:
            handle.observe = lambda: (state["exit"], "alive" if state["alive"] else "gone")
        clock_api = SimpleNamespace(monotonic=lambda: clock[0],
                                    sleep=lambda seconds: clock.__setitem__(0, clock[0] + seconds))
        with patch("hey_my_buddy.protocol.process_handle.time", clock_api), \
             patch.object(handle, "_signal_session", side_effect=send):
            result = handle.terminate(grace_seconds=3, settle_seconds=0.1)
        process.send_signal.assert_not_called()
        process.kill.assert_not_called()
        return result, events

    @unittest.skipIf(os.name == "nt", "POSIX signal escalation")
    def test_stdio_then_grace_then_term_then_short_wait_then_kill(self):
        result, events = self.stop("stdin")
        self.assertEqual(result, (True, True))
        self.assertEqual(events, [("eof", 0), ("SIGTERM", 3), ("SIGKILL", 4)])

    @unittest.skipIf(os.name == "nt", "POSIX signal channel")
    def test_signal_channel_spends_its_grace_after_term_once(self):
        result, events = self.stop("signal")
        self.assertEqual(result, (True, True))
        self.assertEqual(events, [("SIGTERM", 0), ("SIGKILL", 3)])

    def test_windows_stdio_waits_for_eof_then_terminates_only_its_job(self):
        result, events = self.stop("stdin", windows=True)
        self.assertEqual(result, (True, True))
        self.assertEqual(events, [("eof", 0), ("job.terminate", 3), ("job.close", 3)])

    def test_windows_without_a_channel_does_not_spend_the_grace(self):
        result, events = self.stop("signal", windows=True)
        self.assertEqual(result, (True, True))
        self.assertEqual(events, [("job.terminate", 0), ("job.close", 0)])

    def test_stdio_exit_does_not_escalate(self):
        result, events = self.stop("stdin", windows=True, eof_exits=True)
        self.assertEqual(result, (True, False))
        self.assertEqual(events, [("eof", 0), ("job.close", 0)])

    @unittest.skipIf(os.name == "nt", "POSIX signal escalation")
    def test_root_exit_ends_grace_and_terms_surviving_group_immediately(self):
        result, events = self.stop("stdin", root_exits=True)
        self.assertEqual(result, (True, True))
        self.assertEqual(events, [("eof", 0), ("SIGTERM", 0), ("SIGKILL", 1)])

    def test_windows_root_exit_ends_grace_and_terminates_surviving_job_immediately(self):
        result, events = self.stop("stdin", windows=True, root_exits=True)
        self.assertEqual(result, (True, True))
        self.assertEqual(events, [("eof", 0), ("job.terminate", 0), ("job.close", 0)])

    def test_unknown_job_after_force_never_confirms_or_closes_it(self):
        result, events = self.stop("signal", windows=True, unknown=True)
        self.assertEqual(result, (False, True))
        self.assertEqual(events, [("job.terminate", 0)])

    def test_terminate_is_not_starved_by_continuous_wait_loop(self):
        process = Mock(pid=123, returncode=None, stdin=Mock())
        process.poll.return_value = None
        entered_first = threading.Event()
        release_first = threading.Event()
        exited = False
        wait_polls = 0
        polls_at_stop = None
        done = threading.Event()

        def fake_close():
            nonlocal exited, polls_at_stop
            polls_at_stop = wait_polls
            exited = True

        with patch("hey_my_buddy.protocol.process_identity._creation_time", return_value=10):
            handle = ProcessHandle(process, log_paths={}, stop_channel="stdin", close_input=fake_close)

        def fake_observe():
            nonlocal wait_polls
            with handle.stop_lock:
                if handle._stop_owner != threading.get_ident():
                    wait_polls += 1
                    entered_first.set()
                    release_first.wait(timeout=2)
                    # Simulate a slow OS scan that exceeds wait(timeout=0.02).
                    time.sleep(0.03)
                return (0, "gone") if exited else (None, "alive")

        handle.observe = fake_observe

        def loop():
            while not done.is_set():
                if handle.wait(timeout=0.02) is not None:
                    break

        waiter = threading.Thread(target=loop)
        waiter.start()
        try:
            self.assertTrue(entered_first.wait(timeout=2))
            polls_before = wait_polls
            result_holder = []

            def stop_it():
                result_holder.append(handle.terminate(grace_seconds=0.5, settle_seconds=0.1))

            stopper = threading.Thread(target=stop_it)
            stopper.start()
            # Give stopper a moment to register as a stop waiter before releasing first poll.
            deadline = time.monotonic() + 2
            while handle._stop_waiters == 0 and time.monotonic() < deadline:
                time.sleep(0.005)
            self.assertEqual(handle._stop_waiters, 1)
            release_first.set()
            stopper.join(timeout=3)
            self.assertFalse(stopper.is_alive())
            self.assertEqual(result_holder, [(True, False)])
            self.assertEqual(polls_at_stop, polls_before)
        finally:
            release_first.set()
            done.set()
            waiter.join(timeout=2)

    @unittest.skipIf(os.name == "nt", "POSIX exit re-check after confirmed empty scans")
    def test_observe_rechecks_root_exit_after_confirmed_empty_session(self):
        process = Mock(pid=123, returncode=None)
        with patch("hey_my_buddy.protocol.process_identity._creation_time", return_value=10):
            handle = ProcessHandle(process, log_paths={}, stop_channel="signal")
        exit_reads = iter([None, -signal.SIGTERM])
        with patch("hey_my_buddy.protocol.process_handle.child_exit_code", side_effect=lambda _pid: next(exit_reads)), \
             patch.object(handle, "_session_observation", return_value="gone") as session_obs:
            code, state = handle.observe()
        self.assertEqual((code, state), (-signal.SIGTERM, "gone"))
        self.assertTrue(handle._confirmed)
        process.wait.assert_called_once_with()
        session_obs.assert_called_once_with()


class RealStdioStopTests(unittest.TestCase):
    @unittest.skipIf(os.name == "nt", "POSIX session observation")
    def test_quick_child_exit_observes_only_alive_and_gone(self):
        child = owned_popen([sys.executable, "-c", "import sys; sys.stdin.buffer.read()"],
                            stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL, start_new_session=True)
        handle = ProcessHandle(child, log_paths={}, stop_channel="stdin")
        try:
            self.assertEqual(handle.observe(), (None, "alive"))
            child.stdin.close()
            deadline = time.monotonic() + 5
            while True:
                code, state = handle.observe()
                self.assertIn(state, {"alive", "gone"})
                if code is not None and state == "gone":
                    break
                self.assertLess(time.monotonic(), deadline)
                time.sleep(0.01)
            self.assertEqual(code, 0)
            self.assertTrue(handle.shutdown_confirmed(settle_seconds=0))
        finally:
            handle.terminate(grace_seconds=0)

    def test_stdin_channel_without_pipe_stops_child_before_rejecting_ownership(self):
        child = owned_popen([sys.executable, "-c", "import time; time.sleep(30)"],
                            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL, start_new_session=True)
        try:
            with self.assertRaisesRegex(ValueError, "stdin stop channel requires a pipe") as caught:
                ProcessHandle(child, log_paths={}, stop_channel="stdin")
            self.assertTrue(caught.exception.owned_popen_evidence.shutdown_confirmed)
            self.assertTrue(caught.exception.execution_handle.shutdown_confirmed(settle_seconds=0))
        finally:
            if child.poll() is None:
                ProcessHandle(child, log_paths={}, stop_channel="signal").terminate(grace_seconds=0)

    def test_owned_windowless_child_exits_on_input_eof(self):
        with tempfile.TemporaryDirectory() as name:
            ready = Path(name) / "ready"
            child = owned_popen([sys.executable, "-c",
                "import sys; from pathlib import Path; "
                f"Path({str(ready)!r}).touch(); sys.stdin.buffer.read()"],
                stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                start_new_session=True)
            handle = ProcessHandle(child, log_paths={}, stop_channel="stdin")
            try:
                deadline = time.monotonic() + 5
                while not ready.exists():
                    self.assertIsNone(child.poll())
                    self.assertLess(time.monotonic(), deadline)
                    time.sleep(0.01)
                stopped, escalated = handle.terminate(grace_seconds=3)
                self.assertTrue(stopped)
                if os.name != "nt" or not escalated:
                    self.assertEqual(child.returncode, 0)
                self.assertIsNotNone(child.returncode)
                self.assertTrue(handle.shutdown_confirmed(settle_seconds=0))
            finally:
                handle.terminate(grace_seconds=0)


class OccupiedDirectoryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)

    def spawn(self, argv, **kwargs):
        self.child = owned_popen(argv, **kwargs)
        return self.child

    def assert_released(self):
        self.assertIsNotNone(self.child.returncode)
        self.assertTrue(self.child.stdin.closed)
        self.assertTrue(self.child.stdout.closed)
        job = getattr(self.child, "_buddy_job", None)
        if job is not None:
            self.assertIsNone(job.handle)

    def test_binary_handshake_and_eof_exit_with_non_ascii_stdio_encodings(self):
        for encoding in ("cp037", "utf-16-le"):
            with self.subTest(encoding=encoding), patch.dict(os.environ, PYTHONIOENCODING=encoding):
                with support.occupied_directory(self.directory) as child:
                    self.child = child
                    self.assertIsNone(child.poll())
                self.assert_released()
                self.assertEqual(child.returncode, 0)

    def test_readiness_timeout_stops_child_and_closes_pipes(self):
        with patch.object(support, "owned_popen", side_effect=lambda argv, **kwargs: self.spawn(
                [*argv[:-1], "import sys; sys.stdin.buffer.read(1)"], **kwargs)):
            with self.assertRaisesRegex(AssertionError, "Timed out waiting"):
                with support.occupied_directory(self.directory, timeout=0.1):
                    self.fail("A child without a handshake entered the context")
        self.assert_released()

    def test_unconfirmed_or_raised_termination_releases_resources_and_reports_failure(self):
        for stop_error in (None, OSError("stop failed")):
            with self.subTest(stop_error=stop_error), patch.object(
                    ProcessHandle, "terminate", return_value=(False, True), side_effect=stop_error):
                with self.assertRaisesRegex(AssertionError, "did not confirm shutdown") as caught:
                    with support.occupied_directory(self.directory) as child:
                        self.child = child
                        self.assertIsNone(child.poll())
                self.assertIs(caught.exception.__cause__, stop_error)
            self.assert_released()

    def test_handle_construction_failure_releases_resources(self):
        with patch.object(support, "owned_popen", side_effect=self.spawn), \
             patch.object(support, "ProcessHandle", side_effect=ValueError("cannot construct handle")):
            with self.assertRaisesRegex(ValueError, "cannot construct handle"):
                with support.occupied_directory(self.directory):
                    self.fail("A child without a process handle entered the context")
        self.assert_released()

    def test_cleanup_interrupt_releases_resources_and_propagates_unchanged(self):
        interrupt = KeyboardInterrupt("stop interrupted")
        with patch.object(ProcessHandle, "terminate", side_effect=interrupt):
            with self.assertRaises(KeyboardInterrupt) as caught:
                with support.occupied_directory(self.directory) as child:
                    self.child = child
                    self.assertIsNone(child.poll())
        self.assertIs(caught.exception, interrupt)
        self.assert_released()


if __name__ == "__main__":
    unittest.main()
