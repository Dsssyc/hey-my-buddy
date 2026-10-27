"""Private state aliases share one leaf and tree deletion policy."""
import errno
import os
import unittest
from types import SimpleNamespace
from unittest import mock

from blackboard.tasks import test_storage as storage_tests, test_workspace as workspace_tests
from hey_my_buddy import private_dirs
from hey_my_buddy.blackboard.tasks import storage
from hey_my_buddy.blackboard.tasks.macro_task_deletion_workspace import delete_workspaces
from hey_my_buddy.errors import BoardError
from support import BoardTestCase


class PrivateStateLinksTests(BoardTestCase):
    def test_leaf_open_accepts_linked_parent_and_rejects_linked_or_nonregular_leaf(self):
        actual = self.directory / 'actual'
        actual.mkdir()
        alias = self.directory / 'alias'
        alias.symlink_to(actual, target_is_directory=True)
        record = alias / 'record'
        record.write_bytes(b'fact')
        fd = private_dirs.open_regular_fd(record, os.O_RDONLY)
        with os.fdopen(fd, 'rb') as stream:
            self.assertEqual(stream.read(), b'fact')
        (alias / 'linked').symlink_to(record)
        for path in (alias / 'linked', actual):
            with self.subTest(path=path), self.assertRaises(BoardError) as caught:
                private_dirs.open_regular_fd(path, os.O_RDONLY)
            self.assertEqual(caught.exception.code, 'PRIVATE_PATH_UNSAFE')

    @unittest.skipIf(os.name == 'nt', 'POSIX nonblocking ordinary-file open')
    def test_leaf_open_is_nonblocking_and_rejects_a_fifo(self):
        record = self.directory / 'record'
        record.write_bytes(b'fact')
        with mock.patch.object(private_dirs.os, 'open', wraps=os.open) as opened:
            fd = private_dirs.open_regular_fd(record, os.O_RDONLY)
        os.close(fd)
        self.assertTrue(opened.call_args.args[1] & os.O_NONBLOCK)
        fifo = self.directory / 'fifo'
        os.mkfifo(fifo)
        with self.assertRaises(BoardError) as caught:
            private_dirs.open_regular_fd(fifo, os.O_RDONLY)
        self.assertEqual(caught.exception.code, 'PRIVATE_PATH_UNSAFE')
        with self.assertRaises(BoardError) as caught:
            storage._read_storage_json(fifo)
        self.assertEqual(caught.exception.code, 'STORAGE_UNSAFE')

    def test_storage_record_open_preserves_permission_and_io_errors(self):
        for code in (errno.EACCES, errno.EIO):
            error = OSError(code, 'injected storage read failure')
            with self.subTest(code=code), \
                 mock.patch.object(private_dirs, 'open_regular_fd', side_effect=error), \
                 self.assertRaises(OSError) as caught:
                storage._read_storage_json(self.directory / 'record')
            self.assertIs(caught.exception, error)

    def test_storage_lock_rejects_a_directory_and_preserves_io_errors(self):
        store = SimpleNamespace(directory=self.directory)
        for code in (errno.EACCES, errno.EIO):
            error = OSError(code, 'injected lock open failure')
            with self.subTest(code=code), \
                 mock.patch.object(private_dirs, 'open_regular_fd', side_effect=error), \
                 self.assertRaises(OSError) as caught:
                with storage.locked(store):
                    pass
            self.assertIs(caught.exception, error)
        (self.directory / 'storage' / '.lock').mkdir()
        with self.assertRaises(BoardError) as caught:
            with storage.locked(store):
                pass
        self.assertEqual(caught.exception.code, 'STORAGE_UNSAFE')

    def test_inventory_traverses_linked_adapter_and_micro_task_ancestors(self):
        board = self.board()
        for adapter in ('codex', 'zcode'):
            for level in ('adapter', 'micro-tasks'):
                with self.subTest(adapter=adapter, level=level):
                    actual = self.directory / (adapter + '-' + level)
                    actual.mkdir()
                    adapter_root = board.directory / 'harnesses' / adapter
                    adapter_root.parent.mkdir(exist_ok=True)
                    if level == 'adapter':
                        adapter_root.symlink_to(actual, target_is_directory=True)
                        groups = adapter_root / 'micro-tasks'
                        groups.mkdir()
                    else:
                        adapter_root.mkdir()
                        groups = adapter_root / 'micro-tasks'
                        groups.symlink_to(actual, target_is_directory=True)
                    candidate = groups / 'unknown'
                    candidate.mkdir()
                    (candidate / 'payload').write_bytes(b'owned bytes')
                    linked_leaf = groups / 'linked-leaf'
                    linked_leaf.symlink_to(candidate, target_is_directory=True)
                    with mock.patch.object(storage, 'process_inventory', return_value=([], [], True)):
                        planned = board.call('storage_plan', {})
                    rows = {row['path']: row for row in planned['candidates']}
                    self.assertEqual(rows[str(candidate)]['bytes'], len(b'owned bytes'))
                    self.assertNotIn('linked-path', rows[str(candidate)]['reasons'])
                    self.assertIn('linked-path', rows[str(linked_leaf)]['reasons'])
                    if level == 'adapter':
                        adapter_root.unlink()
                    else:
                        groups.unlink()
                        adapter_root.rmdir()

    def test_storage_reclaims_through_linked_private_ancestor_without_a_tomb(self):
        board = self.board()
        for interrupted in (False, True):
            with self.subTest(interrupted=interrupted):
                actual = self.directory / ('relocated-runtimes-' + str(interrupted))
                actual.mkdir()
                alias = board.directory / ('runtime-location-' + str(interrupted))
                alias.symlink_to(actual, target_is_directory=True)
                with mock.patch.dict(os.environ, {'HMB_RUNTIME_ROOT': str(alias / 'runtime')}):
                    candidate, planned, _ = storage_tests.StorageTests._runtime_plan(self, board)
                    self.assertTrue(storage.tree_info(candidate)[2])
                    params = {'planId': planned['planId'],
                              'commandId': 'linked-private-reclaim-' + str(interrupted), 'confirm': True}
                    if interrupted:
                        with mock.patch.object(private_dirs, 'remove_tree', side_effect=OSError('interrupted')):
                            with self.assertRaises(BoardError) as caught:
                                board.call('storage_apply', params)
                        self.assertEqual(caught.exception.code, 'STORAGE_INCOMPLETE')
                        self.assertEqual(len(list(candidate.parent.glob('.reclaim-*'))), 1)
                    result = board.call('storage_apply', params)
                self.assertTrue(result['complete'])
                self.assertEqual([row['path'] for row in result['removed']], [str(candidate)])
                self.assertFalse(candidate.exists())
                self.assertEqual(list(candidate.parent.glob('.reclaim-*')), [])


class LinkedWorkspaceRecordDeletionTests(unittest.TestCase):
    setUp = workspace_tests.WorkspaceTests.setUp
    git = workspace_tests.WorkspaceTests.git
    intent = workspace_tests.WorkspaceTests.intent
    prepare = workspace_tests.WorkspaceTests.prepare

    def test_macro_deletion_removes_records_through_linked_workspaces_without_a_tomb(self):
        manifest = self.prepare(kind='existing')
        records = self.state / 'workspaces'
        relocated = self.root / 'relocated-workspaces'
        records.rename(relocated)
        records.symlink_to(relocated, target_is_directory=True)
        skipped = delete_workspaces(SimpleNamespace(directory=self.state), [manifest], [])
        self.assertEqual(skipped, [])
        self.assertEqual(list(relocated.iterdir()), [])
        self.assertTrue(records.is_symlink())
        self.assertTrue(self.repo.is_dir())
        self.assertFalse(self.git('for-each-ref', '--format=%(refname)',
                                  f"refs/hey-my-buddy/workspaces/{manifest['workspaceId']}/"))
