"""Windows pipe EOF is independent of the child's eventual exit."""
from __future__ import annotations

import ctypes
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


class WindowsProbePipeTests(unittest.TestCase):
    def fake_probe(self, error, *, wait_expires=False):
        process = mock.Mock(returncode=0)
        process.poll.return_value = None  # stdout can close before process exit
        process.stdout.fileno.return_value = 123
        process.wait.side_effect = [subprocess.TimeoutExpired('fixture', 1), 0] if wait_expires else None
        def peek(_pipe, _buffer, _size, _read, available, _left):
            if not getattr(peek, 'read', False):
                available._obj.value = 4
                peek.read = True
                return True
            return False
        kernel = SimpleNamespace(PeekNamedPipe=mock.Mock(side_effect=peek))
        with mock.patch.dict(sys.modules, {'msvcrt': SimpleNamespace(get_osfhandle=lambda _: 456)}), \
             mock.patch.object(ctypes, 'windll', SimpleNamespace(kernel32=kernel), create=True), \
             mock.patch.object(ctypes, 'WinDLL', return_value=kernel, create=True), \
             mock.patch.object(ctypes, 'get_last_error', return_value=error, create=True), \
             mock.patch.object(discovery.os, 'read', return_value=b'1.2\n'), \
             mock.patch.object(discovery.subprocess, 'run'):
            result = discovery._probe_windows(process, time.monotonic() + 1)
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
