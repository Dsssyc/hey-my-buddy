"""Regressions through the cleanup and storage callers' existing interfaces."""
import os
from pathlib import Path
import unittest
from unittest.mock import patch

from hey_my_buddy import process_observation as observation
from hey_my_buddy.blackboard.tasks import storage
from hey_my_buddy.cli import checks
from process_observation_fakes import proc_references, process, processes


class ProcessObservationRetryTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch.object(observation.psutil, 'WINDOWS', False))
        self.enterContext(patch.object(observation.psutil, 'LINUX', True))
        self.enterContext(patch.object(observation.psutil, 'MACOS', False))
        self.enterContext(patch.object(observation.os, 'geteuid', return_value=0, create=True))

    def test_transient_argv_failure_retries_once_after_identity_match(self):
        for error in (observation.psutil.AccessDenied(123), OSError('busy'), SystemError('proc_cmdline')):
            with self.subTest(error=type(error).__name__):
                target = process(123, ['python', 'argument with spaces'])
                target.cmdline.side_effect = [error, ['python', 'argument with spaces']]
                with processes(target):
                    table = checks._ps_argv_table()
                self.assertEqual(table, [(123, ['python', 'argument with spaces'])])
                self.assertEqual(target.cmdline.call_count, 2)
                self.assertEqual(target.create_time.call_count, 2)
                target.status.assert_not_called()

    @unittest.skipIf(os.name == 'nt', 'POSIX runtime absence proof; Windows remains unknown')
    def test_denied_paths_allow_an_unused_runtime(self):
        target = process(123)
        target.cwd.side_effect = observation.psutil.AccessDenied(123)
        with processes(target), proc_references():
            self.assertEqual(storage.runtime_usage(Path('/fixture'), [], True), [])
