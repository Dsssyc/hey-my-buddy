"""One identity-checked retry and the sole denied-path exception."""
from contextlib import redirect_stderr
import io
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from hey_my_buddy import process_observation as observation
from hey_my_buddy.blackboard.tasks import storage
from hey_my_buddy.cli import checks
from process_observation_fakes import native_references, proc_references, process, processes


class ProcessObservationTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch.object(observation.psutil, 'WINDOWS', False))
        self.enterContext(patch.object(observation.psutil, 'LINUX', True))
        self.enterContext(patch.object(observation.psutil, 'MACOS', False))
        self.enterContext(patch.object(observation.os, 'geteuid', return_value=0, create=True))

    def test_transient_path_reads_retry_once_and_keep_recovered_references(self):
        for channel in ('cwd', 'memory_maps', '/proc/pid/fd'):
            with self.subTest(channel=channel):
                target = process(123)
                failure = observation.psutil.AccessDenied(123)
                if channel == '/proc/pid/fd':
                    reader = patch.object(observation, '_proc_fd_paths', side_effect=[failure, {'/fixture/file'}])
                else:
                    method = getattr(target, channel)
                    method.side_effect = [failure, '/fixture' if channel == 'cwd' else process(1, mappings=['/fixture/map']).memory_maps()]
                    reader = proc_references()
                with processes(target), reader:
                    opened, error, available, denied = observation.observe_open_paths()
                self.assertTrue(any(path.startswith('/fixture') for path in opened[123]))
                self.assertIsNone(error)
                self.assertTrue(available)
                self.assertEqual(denied, [])
                self.assertEqual(target.create_time.call_count, 2)

    def test_changed_or_gone_identity_does_not_read_the_replacement(self):
        for channel in ('cmdline', 'cwd', 'memory_maps'):
            for identity in (1235.0, observation.psutil.NoSuchProcess(123)):
                with self.subTest(channel=channel, identity=identity):
                    target = process(123)
                    target.create_time.side_effect = [1234.5, identity]
                    getattr(target, channel).side_effect = observation.psutil.AccessDenied(123)
                    with processes(target), proc_references():
                        if channel == 'cmdline':
                            rows, problems = observation.observe_process_argv()
                            self.assertEqual((rows, problems), ([], []))
                        else:
                            self.assertEqual(observation.observe_open_paths(), ({}, None, True, []))
                    self.assertEqual(getattr(target, channel).call_count, 1)
                    target.status.assert_not_called()

    def test_unreadable_identity_never_uses_the_path_exception_or_a_second_read(self):
        for channel in ('cmdline', 'cwd', 'memory_maps'):
            for initial in (False, True):
                with self.subTest(channel=channel, initial=initial):
                    target = process(123)
                    failure = observation.psutil.AccessDenied(123)
                    target.create_time.side_effect = failure if initial else [1234.5, failure]
                    getattr(target, channel).side_effect = failure
                    with processes(target), proc_references():
                        if channel == 'cmdline':
                            _, problems = observation.observe_process_argv()
                        else:
                            _, problems, _, denied = observation.observe_open_paths()
                            self.assertEqual(denied, [])
                    self.assertTrue(problems)
                    self.assertEqual(getattr(target, channel).call_count, 0 if initial else 1)
                    target.status.assert_not_called()

    def test_persistent_argv_failure_is_unknown_and_identifies_the_read(self):
        for failure in (observation.psutil.AccessDenied(123), OSError('busy'), SystemError('proc_cmdline')):
            with self.subTest(error=type(failure).__name__):
                target = process(123, name='agent')
                target.cmdline.side_effect = failure
                with processes(target):
                    rows, problems = observation.observe_process_argv()
                self.assertEqual(rows, [])
                self.assertEqual(len(problems), 1)
                for detail in ('pid=123', 'createTime=1234.5', "name='agent'", 'read=cmdline', type(failure).__name__):
                    self.assertIn(detail, problems[0])
                self.assertEqual(target.cmdline.call_count, 2)

    def test_persistent_access_denied_records_identity_and_path_channel(self):
        for channel in ('cwd', 'memory_maps', '/proc/pid/fd'):
            with self.subTest(channel=channel):
                target = process(123, name='agent')
                failure = observation.psutil.AccessDenied(123)
                if channel == '/proc/pid/fd':
                    reader = patch.object(observation, '_proc_fd_paths', side_effect=failure)
                else:
                    getattr(target, channel).side_effect = failure
                    reader = proc_references()
                with processes(target), reader:
                    _, error, available, denied = observation.observe_open_paths()
                self.assertIsNone(error)
                self.assertTrue(available)
                self.assertEqual(len(denied), 1)
                for detail in ('pid=123', 'createTime=1234.5', "name='agent'", 'read=' + channel, 'AccessDenied'):
                    self.assertIn(detail, str(denied[0]))
                self.assertEqual(target.create_time.call_count, 2)

    def test_other_path_errors_remain_unknown_after_the_second_read(self):
        for failure in (OSError('busy'), PermissionError('native permission error')):
            for channel in ('cwd', 'memory_maps', '/proc/pid/fd'):
                with self.subTest(error=type(failure).__name__, channel=channel):
                    target = process(123)
                    if channel == '/proc/pid/fd':
                        reader = patch.object(observation, '_proc_fd_paths', side_effect=failure)
                    else:
                        getattr(target, channel).side_effect = failure
                        reader = proc_references()
                    with processes(target), reader:
                        _, error, available, denied = observation.observe_open_paths()
                    self.assertIn('read=' + channel, error)
                    self.assertIn(type(failure).__name__, error)
                    self.assertTrue(available)
                    self.assertEqual(denied, [])
                    self.assertEqual(target.create_time.call_count, 2)

    @unittest.skipIf(os.name == 'nt', 'POSIX runtime absence proof; Windows remains unknown')
    def test_denied_maps_keep_positive_cwd_evidence_for_storage(self):
        target = process(123, cwd='/fixture')
        target.memory_maps.side_effect = observation.psutil.AccessDenied(123)
        log = io.StringIO()
        with processes(target), proc_references(), redirect_stderr(log):
            self.assertEqual(storage.runtime_usage(Path('/fixture'), [], True), ['runtime-in-use'])
        self.assertEqual(len(log.getvalue().splitlines()), 1)
        self.assertIn('read=memory_maps: AccessDenied', log.getvalue())

    @unittest.skipIf(os.name == 'nt', 'POSIX runtime absence proof; Windows remains unknown')
    def test_storage_log_deduplicates_identity_and_reports_a_reused_pid(self):
        target = process(123)
        target.cwd.side_effect = observation.psutil.AccessDenied(123)
        reported = set()
        log = io.StringIO()
        with processes(target), proc_references(), redirect_stderr(log):
            for created in (1234.5, 1234.5, 1235.5):
                target.create_time.return_value = created
                self.assertEqual(storage.runtime_usage(Path('/fixture'), [], True, reported=reported), [])
        lines = log.getvalue().splitlines()
        self.assertEqual(len(lines), 2)
        self.assertIn('pid=123 createTime=1234.5', lines[0])
        self.assertIn('pid=123 createTime=1235.5', lines[1])

    def test_denied_cwd_is_a_cleanup_note_even_when_lsof_omits_the_pid(self):
        target = process(123)
        target.cwd.side_effect = observation.psutil.AccessDenied(123)
        with (processes(target), native_references('', returncode=1),
              patch.object(observation.psutil, 'LINUX', False),
              patch.object(observation.psutil, 'MACOS', True)):
            findings, problems, notes = checks._observation_snapshot(Path('/fixture'))
        self.assertEqual((findings, problems), ({}, []))
        self.assertEqual(len(notes), 1)
        self.assertIn('pid=123', notes[0])

    def test_clean_teardown_prints_denied_identity_before_removing_its_private_root(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = process(123)
            target.cwd.side_effect = observation.psutil.AccessDenied(123)
            with (processes(target), proc_references(),
                  patch.object(checks, 'request_cooperative_stop', return_value=[]),
                  patch.object(checks, 'held_locks', return_value={'daemon': []}),
                  patch.object(checks, 'remove_private_test_root') as remove,
                  patch('builtins.print') as output,
                  patch.object(checks.os, 'name', 'posix')):
                self.assertIsNone(checks.teardown_private_root(root))
            remove.assert_called_once_with(root)
            self.assertIn('read=cwd: AccessDenied', output.call_args.args[0])


if __name__ == '__main__':
    unittest.main()
