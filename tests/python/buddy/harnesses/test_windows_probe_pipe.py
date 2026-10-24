"""Windows pipe EOF is independent of the child's eventual exit."""
from __future__ import annotations

import os
from contextlib import contextmanager
import subprocess
import sys
import time
from types import SimpleNamespace
import unittest
from unittest import mock

from hey_my_buddy.buddy.harnesses import discovery
from hey_my_buddy.buddy.harnesses.base import ProcessHandle
from hey_my_buddy.protocol import owned_process, process_handle, process_identity
from hey_my_buddy.protocol.owned_process import OwnedJob, owned_popen


class Win32Error(Exception):
    def __init__(self, code):
        super().__init__(code, 'PeekNamedPipe', 'fixture')
        self.winerror = code


class WindowsProbePipeTests(unittest.TestCase):
    @contextmanager
    def probe_fixture(self, error=109, *, wait_expires=False, active_unknown=False, exited=False):
        process = mock.Mock(pid=321, returncode=0 if exited else None, _handle=42)
        process.poll.side_effect = lambda: process.returncode
        process.stdout.fileno.return_value = 123
        count = [1]  # A descendant can remain even when the root has already exited.

        def wait(timeout=None):
            if wait_expires and process.returncode is None:
                raise subprocess.TimeoutExpired('fixture', timeout)
            process.returncode = 0
            return 0

        def terminate(_job):
            if process.returncode is None:
                process.returncode = 1
            count[0] = 0

        api = mock.Mock()
        api.create_job.return_value = 77
        api.terminate.side_effect = terminate
        api.active.side_effect = OSError('active unavailable') if active_unknown else lambda _: count[0]
        process.wait.side_effect = wait
        process._buddy_job = OwnedJob(api, 77, 'fixture')
        peek = mock.Mock(side_effect=[(b'', 4, 0), Win32Error(error)])
        clock = SimpleNamespace(now=0.0)

        def sleep(seconds):
            clock.now += seconds

        fake_time = SimpleNamespace(monotonic=lambda: clock.now, sleep=sleep)
        read = mock.Mock(return_value=b'1.2\n')
        with mock.patch.dict(sys.modules, {'msvcrt': SimpleNamespace(get_osfhandle=lambda _: 456),
                                          'pywintypes': SimpleNamespace(error=Win32Error),
                                          'win32pipe': SimpleNamespace(PeekNamedPipe=peek)}), \
             mock.patch.object(discovery, 'os', SimpleNamespace(name='nt', read=read)), \
             mock.patch.object(discovery, 'time', fake_time), \
             mock.patch.object(process_handle, 'time', fake_time), \
             mock.patch.object(process_identity, '_creation_time', return_value=100):
            handle = ProcessHandle(process, own_group=True, log_paths={})
            try:
                yield SimpleNamespace(process=process, api=api, handle=handle, peek=peek, read=read)
            finally:
                process._buddy_job.close()

    def fake_probe(self, error, **kwargs):
        with self.probe_fixture(error, **kwargs) as fixture:
            result = discovery._probe_windows(fixture.process, 1, fixture.handle)
            fixture.peek.assert_called_with(456, 0)
            fixture.api.terminate.assert_called_once_with(77)
            if result[2]:
                fixture.api.close.assert_called_once_with(77)
                self.assertTrue(fixture.handle.shutdown_confirmed(settle_seconds=0))
            return result, fixture.process

    def test_broken_pipe_waits_for_exit_and_keeps_already_read_output(self):
        result, process = self.fake_probe(109)  # ERROR_BROKEN_PIPE means EOF
        self.assertEqual(result, (None, b'1.2\n', True))
        process.kill.assert_not_called()
        self.assertIn('timeout', process.wait.call_args.kwargs)

    def test_other_pipe_errors_remain_failures(self):
        result, _ = self.fake_probe(5)  # ERROR_ACCESS_DENIED is not EOF
        self.assertEqual(result, ('pipe-failed', b'', True))

    def test_eof_without_exit_uses_the_remaining_probe_deadline(self):
        result, process = self.fake_probe(109, wait_expires=True)
        self.assertEqual(result, ('timeout', b'', True))
        process.kill.assert_not_called()

    def test_output_limit_still_requires_verified_shutdown(self):
        with mock.patch.object(discovery, '_MAX_OUTPUT', 3):
            result, process = self.fake_probe(109)
        self.assertEqual(result, ('output-limit', b'', True))
        process.kill.assert_not_called()
        process.stdout.close.assert_called_once()

    def test_empty_pipe_times_out_even_if_the_leader_exited(self):
        with self.probe_fixture(exited=True) as fixture:
            fixture.peek.side_effect = None
            fixture.peek.return_value = (b'', 0, 0)
            result = discovery._probe_windows(fixture.process, 0.1, fixture.handle)
            self.assertEqual(result, ('timeout', b'', True))
            fixture.peek.assert_called_with(456, 0)
            fixture.read.assert_not_called()
            fixture.process.kill.assert_not_called()
            fixture.process.stdout.close.assert_called_once()

    def test_successful_root_exit_does_not_confirm_an_unobservable_job(self):
        result, process = self.fake_probe(109, active_unknown=True)
        self.assertEqual(result, (None, b'1.2\n', False))
        self.assertEqual(process.returncode, 0)

    def test_service_probe_without_journal_creates_job_before_resume_and_observes_empty(self):
        for active_unknown in (False, True):
            with self.subTest(active_unknown=active_unknown), \
                 self.probe_fixture(active_unknown=active_unknown) as fixture:
                # Exercise the real owned_popen and ProcessHandle from the service path.
                fixture.process._buddy_job.close()
                fixture.api.close.reset_mock()
                del fixture.process._buddy_job
                events = mock.Mock()
                for method, name in ((fixture.api.create_job, 'create'), (fixture.api.assign, 'assign'),
                                     (fixture.api.resume, 'resume'), (fixture.api.terminate, 'terminate'),
                                     (fixture.api.active, 'active'), (fixture.api.close, 'close')):
                    events.attach_mock(method, name)
                with mock.patch.object(owned_process, 'os', SimpleNamespace(name='nt')), \
                     mock.patch.object(owned_process, 'WindowsAPI', return_value=fixture.api), \
                     mock.patch.object(owned_process.subprocess, 'Popen', return_value=fixture.process) as popen, \
                     mock.patch.object(process_identity, 'native_process_journal_active', return_value=False), \
                     mock.patch.object(process_identity, 'begin_native_process_spawn', return_value=None) as begin:
                    events.attach_mock(popen, 'popen')
                    receipt = []
                    result = discovery.probe(['fixture'], {}, owned=receipt)
                self.assertEqual(result, ('shutdown-unverified', b'') if active_unknown else (None, b'1.2\n'))
                self.assertEqual(receipt, [{'created': True, 'shutdown': not active_unknown, 'exit_code': 0}])
                self.assertEqual(popen.call_args.kwargs['creationflags'], 0x08000204)
                self.assertFalse(popen.call_args.kwargs['start_new_session'])
                begin.assert_called_once_with()
                calls = [call[0] for call in events.mock_calls]
                self.assertEqual(calls[:5], ['create', 'popen', 'assign', 'resume', 'terminate'])
                if not active_unknown:
                    self.assertEqual(calls[-1], 'close')

    def test_service_probe_creation_failure_preserves_the_owned_creation_receipt(self):
        for confirmed in (False, True):
            with self.subTest(confirmed=confirmed):
                failure = OSError('resume failed') if confirmed else RuntimeError('cleanup unknown')
                failure.owned_popen_evidence = owned_process.ProcessCreationEvidence(True, confirmed, 1)
                receipt = []
                with mock.patch.object(owned_process, 'owned_popen', side_effect=failure):
                    if confirmed:
                        self.assertEqual(discovery.probe(['fixture'], {}, owned=receipt), ('launch-failed', b''))
                    else:
                        with self.assertRaises(RuntimeError) as caught:
                            discovery.probe(['fixture'], {}, owned=receipt)
                        self.assertIs(caught.exception, failure)
                self.assertEqual(receipt, [{'created': True, 'shutdown': confirmed, 'exit_code': 1}])

    def test_reader_exception_retains_observed_job_stop(self):
        with self.probe_fixture() as fixture:
            failure = OSError('pipe read failed')
            fixture.read.side_effect = failure
            with self.assertRaises(OSError) as caught:
                discovery._probe_windows(fixture.process, 1, fixture.handle)
            self.assertIs(caught.exception, failure)
            self.assertTrue(failure.discovery_shutdown_confirmed)
            fixture.api.terminate.assert_called_once_with(77)
            fixture.api.close.assert_called_once_with(77)
            fixture.process.stdout.close.assert_called_once()

    def test_handle_construction_failure_closes_pipe_and_preserves_stop_evidence(self):
        for active_unknown in (False, True):
            with self.subTest(active_unknown=active_unknown), self.probe_fixture(active_unknown=active_unknown) as fixture:
                failure = OSError('process identity unavailable')
                receipt = []
                with mock.patch.object(owned_process, 'owned_popen', return_value=fixture.process), \
                     mock.patch.object(process_identity, 'native_process_journal_active', return_value=False), \
                     mock.patch.object(process_identity, '_creation_time', side_effect=failure):
                    with self.assertRaises(RuntimeError if active_unknown else OSError) as caught:
                        discovery.probe(['fixture'], {}, owned=receipt)
                self.assertEqual(caught.exception.owned_popen_evidence.created, True)
                self.assertEqual(receipt, [{'created': True, 'shutdown': not active_unknown, 'exit_code': 0}])
                fixture.process.stdout.close.assert_called_once_with()

    @unittest.skipUnless(os.name == 'nt', 'actual PeekNamedPipe requires Windows')
    def test_native_child_can_close_stdout_before_a_successful_exit(self):
        command = [sys.executable, '-c',
                   'import os,time; os.write(1,b"fixture 1.2.3\\n"); os.close(1); time.sleep(0.3); os._exit(0)']
        process = owned_popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                              stderr=subprocess.DEVNULL)
        handle = ProcessHandle(process, own_group=True, log_paths={})
        try:
            result = discovery._probe_windows(process, time.monotonic() + 3, handle)
            self.assertEqual(result, (None, b'fixture 1.2.3\n', True))
            self.assertTrue(handle.shutdown_confirmed(settle_seconds=0.2))
        finally:
            if not handle.shutdown_confirmed(settle_seconds=0.2):
                handle.terminate(grace_seconds=0)
            if not process.stdout.closed:
                process.stdout.close()


if __name__ == '__main__':
    unittest.main()
