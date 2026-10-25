"""Private fixture cleanup reports owners and retries only READONLY file leaves."""
from __future__ import annotations

import os
import sqlite3
import stat
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from support import BoardTestCase, InProcessBoard, private_state_dir, remove_private_test_root
from hey_my_buddy.blackboard.store.store import BoardStore
from hey_my_buddy.cli import checks


class PrivateRootCleanupTests(unittest.TestCase):
    @unittest.skipUnless(os.name == 'nt', 'Windows READONLY file deletion')
    def test_native_readonly_file_is_removed(self):
        with private_state_dir() as root:
            objects = root / 'objects'
            objects.mkdir()
            path = objects / 'object'
            path.write_bytes(b'git object fixture')
            path.chmod(stat.S_IREAD)
            remove_private_test_root(objects)
            self.assertFalse(objects.exists())

    def windows_delete(self, root, path, error, *, attributes=stat.FILE_ATTRIBUTE_READONLY,
                       mode=stat.S_IFREG, retry_error=None, ancestor_reparse=False):
        unlink = Mock(side_effect=retry_error)
        actual_lstat = Path.lstat

        def information(part):
            if part == path:
                return SimpleNamespace(st_mode=mode, st_file_attributes=attributes)
            if ancestor_reparse and part == path.parent:
                return SimpleNamespace(st_mode=stat.S_IFDIR,
                                       st_file_attributes=stat.FILE_ATTRIBUTE_REPARSE_POINT)
            return actual_lstat(part)

        def removal(directory, *, onexc):
            self.assertEqual(directory, root)
            onexc(unlink, str(path), error)

        with patch.object(checks, 'os', wraps=os) as windows, patch.object(checks.shutil, 'rmtree', side_effect=removal), patch.object(Path, 'lstat', information):
            windows.name = 'nt'
            windows.unlink = unlink
            windows.chmod = Mock()
            try:
                remove_private_test_root(root)
            finally:
                self.chmod_calls = windows.chmod.call_args_list
                self.unlink_calls = unlink.call_args_list

    @staticmethod
    def failure(code):
        error = PermissionError('fixture deletion denied')
        error.winerror = code
        return error

    def test_readonly_regular_file_retried_once(self):
        with private_state_dir() as root:
            path = root / 'object'
            path.write_bytes(b'fixture')
            self.windows_delete(root, path, self.failure(5))
            self.assertEqual(len(self.chmod_calls), 1)
            self.assertEqual(self.chmod_calls[0].args, (path, stat.S_IWRITE))
            self.assertEqual(len(self.unlink_calls), 1)
            self.assertEqual(self.unlink_calls[0].args, (str(path),))

    def test_other_failures_preserve_the_file_without_chmod(self):
        cases = [
            {'error': self.failure(32)},
            {'error': self.failure(5), 'attributes': 0},
            {'error': self.failure(5), 'mode': stat.S_IFDIR},
            {'error': self.failure(5), 'mode': stat.S_IFLNK},
            {'error': self.failure(5), 'attributes': stat.FILE_ATTRIBUTE_READONLY | stat.FILE_ATTRIBUTE_REPARSE_POINT},
            {'error': self.failure(5), 'ancestor_reparse': True},
        ]
        with private_state_dir() as root:
            path = root / 'nested' / 'object'
            path.parent.mkdir()
            path.write_bytes(b'evidence')
            for case in cases:
                with self.subTest(case=case), self.assertRaises(PermissionError) as raised:
                    self.windows_delete(root, path, **case)
                self.assertIs(raised.exception, case['error'])
                self.assertFalse(self.chmod_calls)
                self.assertFalse(self.unlink_calls)
                self.assertEqual(path.read_bytes(), b'evidence')

    def test_failed_retry_is_reported_without_another_attempt(self):
        with private_state_dir() as root:
            path = root / 'object'
            path.write_bytes(b'evidence')
            retry_error = self.failure(32)
            with self.assertRaises(PermissionError) as raised:
                self.windows_delete(root, path, self.failure(5), retry_error=retry_error)
            self.assertIs(raised.exception, retry_error)
            self.assertEqual(len(self.chmod_calls), 1)
            self.assertEqual(len(self.unlink_calls), 1)
            self.assertTrue(path.exists())

    def test_board_close_failure_is_reported_and_preserves_root(self):
        fixture = BoardTestCase('runTest')
        fixture.setUp()
        failure = RuntimeError('board close failed')
        broken = Mock(close=Mock(side_effect=failure))
        other = Mock()
        fixture._stack = [broken, other]
        try:
            with self.assertRaises(ExceptionGroup) as raised:
                fixture._cleanup()
            self.assertEqual(raised.exception.exceptions, (failure,))
            other.close.assert_called_once_with()
            self.assertTrue(fixture.directory.exists())
        finally:
            fixture._stack = []
            fixture.doCleanups()

    def test_console_close_failure_still_closes_database(self):
        with private_state_dir() as root:
            board = InProcessBoard(root)
            with board.store.db.connect() as connection:
                connection.execute('SELECT 1')
            with patch.object(board.console, 'close', side_effect=RuntimeError('console close failed')):
                with self.assertRaisesRegex(RuntimeError, 'console close failed'):
                    board.close()
            with self.assertRaises(sqlite3.ProgrammingError):
                connection.execute('SELECT 1')

    def test_service_stop_failure_still_retires_owned_resources(self):
        fixture = BoardTestCase('runTest')
        fixture.setUp()
        board = Mock()
        fixture._stack = [board]
        try:
            with patch('support.stop_private_service', side_effect=RuntimeError('stop failed')), patch('support.shutdown_private_rpc') as shutdown:
                with self.assertRaises(ExceptionGroup) as raised:
                    fixture._cleanup()
            self.assertIn('stop failed', str(raised.exception.exceptions[0]))
            board.close.assert_called_once_with()
            shutdown.assert_called_once_with()
            self.assertTrue(fixture.directory.exists())
        finally:
            fixture._stack = []
            fixture.doCleanups()

    def test_failed_registered_cleanup_preserves_root(self):
        class FailingCleanup(BoardTestCase):
            def runTest(self):
                def fail_close():
                    raise RuntimeError('registered close failed')
                self.addCleanup(fail_close)

        fixture = FailingCleanup()
        result = unittest.TestResult()
        fixture.run(result)
        try:
            self.assertEqual(len(result.errors), 2)
            self.assertTrue(fixture.directory.exists())
            self.assertIn('registered close failed', result.errors[0][1])
            self.assertIn('preserved', result.errors[1][1])
        finally:
            remove_private_test_root(fixture.directory)

    def test_failed_board_initialization_closes_its_connection(self):
        initialize = BoardStore.initialize
        connections = []

        def fail_after_open(store):
            initialize(store)
            with store.db.connect() as connection:
                connections.append(connection)
            raise RuntimeError('initialization failed')

        with private_state_dir() as root, patch.object(BoardStore, 'initialize', fail_after_open):
            with self.assertRaisesRegex(RuntimeError, 'initialization failed'):
                InProcessBoard(root)
            with self.assertRaises(sqlite3.ProgrammingError):
                connections[0].execute('SELECT 1')
