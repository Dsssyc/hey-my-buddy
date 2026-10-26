"""The owned stop sequence, with real stdio children and deterministic escalation."""
from __future__ import annotations

import os
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from hey_my_buddy.protocol.owned_process import owned_popen
from hey_my_buddy.protocol.process_handle import ProcessHandle


class StopSequenceTests(unittest.TestCase):
    def stop(self, channel, *, windows=False, eof_exits=False, unknown=False):
        events = []
        clock = [0.0]
        state = {"alive": True, "exit": None}

        def note(event):
            events.append((event, round(clock[0], 3)))

        def eof():
            note("eof")
            if eof_exits:
                state.update(alive=False, exit=0)

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
        process.stdin.close.side_effect = eof
        if windows:
            process._buddy_job = SimpleNamespace(terminate=force, active=active,
                                                close=lambda **kw: note("job.close"))
        with patch("hey_my_buddy.protocol.process_identity._creation_time", return_value=10):
            handle = ProcessHandle(process, own_group=True, log_paths={}, stop_channel=channel)
        if not windows:
            handle.group_alive = lambda: state["alive"]
        clock_api = SimpleNamespace(monotonic=lambda: clock[0],
                                    sleep=lambda seconds: clock.__setitem__(0, clock[0] + seconds))
        with patch("hey_my_buddy.protocol.process_handle.time", clock_api), \
             patch.object(handle, "_signal_group", side_effect=send):
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

    def test_unknown_job_after_force_never_confirms_or_closes_it(self):
        result, events = self.stop("signal", windows=True, unknown=True)
        self.assertEqual(result, (False, True))
        self.assertEqual(events, [("job.terminate", 0)])


class RealStdioStopTests(unittest.TestCase):
    def test_owned_windowless_child_exits_on_input_eof(self):
        with tempfile.TemporaryDirectory() as name:
            ready = Path(name) / "ready"
            child = owned_popen([sys.executable, "-c",
                "import sys; from pathlib import Path; "
                f"Path({str(ready)!r}).touch(); sys.stdin.buffer.read()"],
                stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                start_new_session=True)
            handle = ProcessHandle(child, own_group=True, log_paths={}, stop_channel="stdin")
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


if __name__ == "__main__":
    unittest.main()
