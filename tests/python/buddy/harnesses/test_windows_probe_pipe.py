"""Windows pipe EOF is independent of the child's eventual exit."""
from __future__ import annotations

import os
import subprocess
import sys
import time
from types import SimpleNamespace
import unittest
from unittest import mock

from hey_my_buddy.buddy.harnesses import discovery
from hey_my_buddy.buddy.harnesses.base import ProcessHandle
from hey_my_buddy.protocol.owned_process import owned_popen


class Win32Error(Exception):
    def __init__(self, code):
        super().__init__(code, 'PeekNamedPipe', 'fixture')
        self.winerror = code


class WindowsProbePipeTests(unittest.TestCase):
    def fake_probe(self, error, *, wait_expires=False):
        process = mock.Mock(returncode=0)
        process.poll.return_value = None  # stdout can close before process exit
        process.stdout.fileno.return_value = 123
        process.wait.side_effect = [subprocess.TimeoutExpired('fixture', 1), 0] if wait_expires else None
        peek = mock.Mock(side_effect=[(b'', 4, 0), Win32Error(error)])
        with mock.patch.dict(sys.modules, {'msvcrt': SimpleNamespace(get_osfhandle=lambda _: 456),
                                          'pywintypes': SimpleNamespace(error=Win32Error),
                                          'win32pipe': SimpleNamespace(PeekNamedPipe=peek)}), \
             mock.patch.object(discovery.os, 'read', return_value=b'1.2\n'), \
             mock.patch.object(discovery.subprocess, 'run'):
            result = discovery._probe_windows(process, time.monotonic() + 1)
        peek.assert_called_with(456, 0)
        return result, process

    def test_broken_pipe_waits_for_exit_and_keeps_already_read_output(self):
        result, process = self.fake_probe(109)  # ERROR_BROKEN_PIPE means EOF
        self.assertEqual(result, (None, b'1.2\n', True))
        process.kill.assert_not_called()
        self.assertIn('timeout', process.wait.call_args.kwargs)

    def test_other_pipe_errors_remain_failures(self):
        result, _ = self.fake_probe(5)  # ERROR_ACCESS_DENIED is not EOF
        self.assertEqual(result, ('pipe-failed', b'', False))

    def test_eof_without_exit_uses_the_remaining_probe_deadline(self):
        result, process = self.fake_probe(109, wait_expires=True)
        self.assertEqual(result, ('timeout', b'', False))
        process.kill.assert_called_once()

    def test_output_limit_still_requires_verified_shutdown(self):
        with mock.patch.object(discovery, '_MAX_OUTPUT', 3):
            result, process = self.fake_probe(109)
        self.assertEqual(result, ('output-limit', b'', False))
        process.kill.assert_called_once()
        process.stdout.close.assert_called_once()

    def test_empty_pipe_times_out_even_if_the_leader_exited(self):
        process = mock.Mock(returncode=0)
        process.poll.return_value = 0
        process.stdout.fileno.return_value = 123
        peek = mock.Mock(return_value=(b'', 0, 0))
        clock = SimpleNamespace(now=0.0)

        def sleep(seconds):
            clock.now += seconds

        with mock.patch.dict(sys.modules, {'msvcrt': SimpleNamespace(get_osfhandle=lambda _: 456),
                                          'pywintypes': SimpleNamespace(error=Win32Error),
                                          'win32pipe': SimpleNamespace(PeekNamedPipe=peek)}), \
             mock.patch.object(discovery.time, 'monotonic', side_effect=lambda: clock.now), \
             mock.patch.object(discovery.time, 'sleep', side_effect=sleep), \
             mock.patch.object(discovery.os, 'read') as read, \
             mock.patch.object(discovery.subprocess, 'run'):
            result = discovery._probe_windows(process, 0.1)
        self.assertEqual(result, ('timeout', b'', False))
        peek.assert_called_with(456, 0)
        read.assert_not_called()
        process.kill.assert_not_called()
        process.stdout.close.assert_called_once()

    @unittest.skipUnless(os.name == 'nt', 'actual PeekNamedPipe requires Windows')
    def test_native_child_can_close_stdout_before_a_successful_exit(self):
        command = [sys.executable, '-c',
                   'import os,time; os.write(1,b"fixture 1.2.3\\n"); os.close(1); time.sleep(0.3); os._exit(0)']
        process = owned_popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                              stderr=subprocess.DEVNULL)
        handle = ProcessHandle(process, own_group=True, log_paths={})
        try:
            result = discovery._probe_windows(process, time.monotonic() + 3)
            self.assertEqual(result, (None, b'fixture 1.2.3\n', True))
            self.assertTrue(handle.shutdown_confirmed(settle_seconds=0.2))
        finally:
            if not handle.shutdown_confirmed(settle_seconds=0.2):
                handle.terminate(grace_seconds=0)
            if not process.stdout.closed:
                process.stdout.close()


if __name__ == '__main__':
    unittest.main()
