"""Codex transport deadline boundary checks; no model network or shared state is used."""
import math
import subprocess
import sys
import threading
import time
import unittest

from buddy.adapters.codex_protocol import CodexProtocolError, Connection
from buddy.adapters.codex_runner import execution_deadline


class UnlimitedDeadlineTests(unittest.TestCase):
    def test_zero_timeout_maps_to_an_infinite_overall_deadline_only(self):
        self.assertEqual(execution_deadline(0), math.inf)
        before = time.monotonic()
        deadline = execution_deadline(45)
        self.assertLessEqual(before + 44, deadline)
        self.assertLessEqual(deadline, time.monotonic() + 45)

    def test_infinite_deadline_keeps_each_wait_bounded_and_cancellation_honored(self):
        process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"],
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE)

        def stop():
            process.kill()
            process.wait(timeout=5)
            process.stdin.close()
            process.stdout.close()

        self.addCleanup(stop)
        connection = Connection(process, math.inf, threading.Event())
        started = time.monotonic()
        connection.pump()  # no message arrives: the bounded per-pump wait must still return
        self.assertLess(time.monotonic() - started, 5.0)
        connection.cancelled.set()
        with self.assertRaises(CodexProtocolError) as error:
            connection.pump()
        self.assertEqual(error.exception.code, "user-cancel")


if __name__ == "__main__":
    unittest.main()
