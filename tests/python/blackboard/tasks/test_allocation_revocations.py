"""Crash and generation boundaries for durable allocation revocation."""
import errno
import os
from pathlib import Path
from unittest.mock import patch

from support import BoardTestCase
from blackboard.tasks.workspace_lifecycle_case import LifecycleTestCase
from hey_my_buddy.blackboard.tasks import allocation_revocations, storage, workspace
from hey_my_buddy.blackboard.tasks.reclamation import Reclamation, allocation_inventory
from hey_my_buddy.errors import BoardError
from hey_my_buddy.paths import native_path


class AllocationRevocationTests(BoardTestCase):
    def setUp(self):
        super().setUp()
        self.instance = self.board()
        self.store = self.instance.store

    def shell(self, request_id, *, request=True):
        _, directory = workspace._workspace_directory(self.directory, request_id)
        (directory / '.lock').touch()
        if request:
            (directory / 'request.json').write_bytes(workspace._json({'requestId': request_id}))
        return directory

    def row(self, directory):
        with self.store.db.read() as connection:
            return connection.execute(
                'SELECT request_id FROM allocation_revocations WHERE allocation_id=?',
                (directory.name,)).fetchone()

    def record(self, directory, request_id):
        with workspace._lock(directory, create=False), storage.locked(self.store):
            allocation_revocations.record(self.store, directory, request_id)

    def test_request_is_unlinked_only_after_durable_record_and_failed_lock_unlink_retries(self):
        directory = self.shell('record-before-unlink')
        request = directory / 'request.json'
        real_unlink = Path.unlink
        seen = []

        def unlink(path, *args, **kwargs):
            if path == request:
                self.assertEqual(self.row(directory)['request_id'], 'record-before-unlink')
                seen.append('request')
            if path == directory / '.lock':
                raise PermissionError(errno.EACCES, 'controlled lock unlink failure', str(path))
            return real_unlink(path, *args, **kwargs)

        if os.name == 'nt':
            from hey_my_buddy import windows_files
            with patch.object(Path, 'unlink', new=unlink), patch.object(
                    windows_files, 'unlink_regular_file', side_effect=PermissionError(
                        errno.EACCES, 'controlled lock unlink failure')):
                Reclamation(self.store).run()
        else:
            with patch.object(Path, 'unlink', new=unlink):
                Reclamation(self.store).run()
        self.assertEqual(seen, ['request'])
        self.assertFalse(request.exists())
        self.assertTrue((directory / '.lock').exists())
        self.assertEqual(self.row(directory)['request_id'], 'record-before-unlink')

        Reclamation(self.store).run()
        self.assertFalse(directory.exists())
        self.assertIsNone(self.row(directory))

    def test_nonempty_directory_after_lock_unlink_keeps_row_until_next_reclamation(self):
        directory = self.shell('after-lock-unlink')
        real_rmdir = Path.rmdir

        def rmdir(path):
            if path == native_path(directory):
                raise OSError(errno.ENOTEMPTY, 'controlled interruption after lock unlink', str(path))
            return real_rmdir(path)

        if os.name == 'nt':
            from hey_my_buddy import windows_files
            with patch.object(windows_files, 'remove_directory', side_effect=OSError(
                    errno.ENOTEMPTY, 'controlled interruption after lock unlink')):
                Reclamation(self.store).run()
        else:
            with patch.object(Path, 'rmdir', new=rmdir):
                Reclamation(self.store).run()
        self.assertFalse((directory / 'request.json').exists())
        self.assertFalse((directory / '.lock').exists())
        self.assertEqual(self.row(directory)['request_id'], 'after-lock-unlink')
        self.assertEqual(self.store.service_issues(), [])

        Reclamation(self.store).run()
        self.assertFalse(directory.exists())
        self.assertIsNone(self.row(directory))
        self.assertEqual(self.store.service_issues(), [])

    def test_interruption_before_request_unlink_keeps_the_record_for_retry(self):
        directory = self.shell('before-request-unlink')
        request = directory / 'request.json'
        real_unlink = Path.unlink
        def interrupted(path, *args, **kwargs):
            if path == request:
                raise OSError(errno.EIO, 'controlled interruption before request unlink')
            return real_unlink(path, *args, **kwargs)
        maintenance = Reclamation(self.store)
        with patch.object(Path, 'unlink', new=interrupted):
            maintenance.run()
        self.assertTrue(request.exists())
        self.assertIsNotNone(self.row(directory))
        maintenance.run()
        self.assertFalse(directory.exists())
        self.assertIsNone(self.row(directory))

    def test_missing_directory_after_rmdir_clears_only_its_old_row(self):
        directory = self.shell('after-rmdir')
        self.record(directory, 'after-rmdir')
        (directory / 'request.json').unlink()
        (directory / '.lock').unlink()
        directory.rmdir()

        Reclamation(self.store).run()
        self.assertIsNone(self.row(directory))
        self.assertFalse(directory.exists())

    def test_lock_only_without_revocation_is_retained(self):
        directory = self.shell('unproven-lock-only', request=False)
        Reclamation(self.store).run()
        self.assertTrue((directory / '.lock').is_file())
        self.assertIsNone(self.row(directory))
        inventory = allocation_inventory(self.store, [directory])
        self.assertEqual(len(inventory), 1)
        self.assertIn('allocation-unproven', inventory[0]['reasons'])

    def test_revoked_shell_with_extra_file_is_retained(self):
        directory = self.shell('revoked-with-evidence', request=False)
        self.record(directory, 'revoked-with-evidence')
        (directory / 'new-evidence.txt').write_text('must survive', encoding='utf-8')
        Reclamation(self.store).run()
        self.assertEqual((directory / 'new-evidence.txt').read_text(encoding='utf-8'), 'must survive')
        self.assertEqual(self.row(directory)['request_id'], 'revoked-with-evidence')

    def test_inventory_does_not_create_a_lock_in_a_recorded_empty_tail(self):
        directory = self.shell('read-only-empty-tail', request=False)
        self.record(directory, 'read-only-empty-tail')
        (directory / '.lock').unlink()
        self.assertEqual(allocation_inventory(self.store, [directory]), [])
        self.assertEqual(list(directory.iterdir()), [])
        self.assertIsNotNone(self.row(directory))
        Reclamation(self.store).run()
        self.assertFalse(directory.exists())
        self.assertIsNone(self.row(directory))

    def test_disappearing_lock_is_busy_for_inventory_and_maintenance(self):
        inventory_shell = self.shell('inventory-lock-open-race')
        maintenance_shell = self.shell('maintenance-lock-open-race')
        other_shell = self.shell('unrelated-shell')
        real_open = workspace.private_dirs.open_regular_fd
        removed = []

        def remove_before_open(path, flags, mode=0o600):
            if path in (inventory_shell / '.lock', maintenance_shell / '.lock') and path not in removed:
                path.unlink()
                removed.append(path)
            return real_open(path, flags, mode)

        with patch.object(workspace.private_dirs, 'open_regular_fd', side_effect=remove_before_open):
            inventory = allocation_inventory(self.store, [inventory_shell])
            Reclamation(self.store).run()

        self.assertEqual(removed, [inventory_shell / '.lock', maintenance_shell / '.lock'])
        self.assertEqual(len(inventory), 1)
        self.assertIn('WORKSPACE_BUSY', inventory[0]['reasons'])
        self.assertTrue((inventory_shell / 'request.json').is_file())
        self.assertTrue((maintenance_shell / 'request.json').is_file())
        self.assertFalse(other_shell.exists())
        self.assertEqual(self.store.service_issues(), [])

    def test_missing_lock_leaf_open_is_reported_as_busy(self):
        directory = self.shell('missing-lock-leaf-open')
        lock = directory / '.lock'
        real_open = workspace.private_dirs.open_regular_fd
        removed = []

        def remove_before_open(path, flags, mode=0o600):
            if path == lock:
                path.unlink()
                removed.append(path)
            return real_open(path, flags, mode)

        with patch.object(workspace.private_dirs, 'open_regular_fd', side_effect=remove_before_open):
            with self.assertRaises(BoardError) as caught:
                with workspace._lock(directory, create=False):
                    self.fail('Missing lock leaf was accepted')
        self.assertEqual(caught.exception.code, 'WORKSPACE_BUSY')
        self.assertEqual(removed, [lock])
        self.assertTrue((directory / 'request.json').is_file())

    def test_new_generation_between_unlink_and_rmdir_survives_failed_old_finish(self):
        directory = self.shell('generation-race', request=False)
        self.record(directory, 'generation-race')
        real_unlink = Path.unlink
        created = []

        def create_new_generation():
            with workspace._lock(directory):
                allocation_revocations.prepare(self.store, directory)
                (directory / 'request.json').write_bytes(
                    workspace._json({'requestId': 'generation-race'}))
                (directory / 'new-file.txt').write_text('new generation', encoding='utf-8')
                with self.assertRaises(BoardError) as third:
                    with workspace._lock(directory, create=False):
                        self.fail('Third caller entered the new generation lock')
                self.assertEqual(third.exception.code, 'WORKSPACE_BUSY')
            created.append(True)

        def replace_lock(path, *args, **kwargs):
            result = real_unlink(path, *args, **kwargs)
            if path == native_path(directory / '.lock'):
                create_new_generation()
            return result

        if os.name == 'nt':
            from hey_my_buddy import windows_files
            real_windows_unlink = windows_files.unlink_regular_file

            def windows_replace(path):
                result = real_windows_unlink(path)
                create_new_generation()
                return result

            unlink_patch = patch.object(windows_files, 'unlink_regular_file', side_effect=windows_replace)
        else:
            unlink_patch = patch.object(Path, 'unlink', new=replace_lock)
        with workspace._lock(directory, create=False), unlink_patch:
            self.assertIs(allocation_revocations.finish(self.store, directory), False)
        self.assertEqual(created, [True])
        self.assertIsNone(self.row(directory))
        self.assertEqual({path.name for path in directory.iterdir()},
                         {'.lock', 'request.json', 'new-file.txt'})
        Reclamation(self.store).run()
        self.assertEqual((directory / 'new-file.txt').read_text(encoding='utf-8'), 'new generation')
        self.assertEqual(workspace._record(directory / 'request.json')['requestId'], 'generation-race')
        self.assertEqual({path.name for path in directory.iterdir()},
                         {'.lock', 'request.json', 'new-file.txt'})
        self.assertIsNone(self.row(directory))
        self.assertEqual(self.store.service_issues(), [])

    def test_replaced_open_lock_descriptor_is_rejected_as_busy(self):
        directory = self.shell('replaced-descriptor')
        real_lock = workspace.locking.lock
        replacements = []

        def replace_after_acquisition(descriptor, *, blocking):
            real_lock(descriptor, blocking=blocking)
            if os.name == 'nt':
                from hey_my_buddy.windows_files import unlink_regular_file
                unlink_regular_file(directory / '.lock')
            else:
                native_path(directory / '.lock').unlink()
            (directory / '.lock').touch()
            replacements.append(True)

        with patch.object(workspace.locking, 'lock', side_effect=replace_after_acquisition):
            with self.assertRaises(BoardError) as caught:
                with workspace._lock(directory, create=False):
                    self.fail('Replaced lock descriptor was accepted')
        self.assertEqual(caught.exception.code, 'WORKSPACE_BUSY')
        self.assertEqual(replacements, [True])
        self.assertTrue((directory / '.lock').exists())


class RecreatedRequestTests(LifecycleTestCase):
    def test_unrevoked_preparation_does_not_need_the_storage_lock(self):
        board = self.board()
        intent = {'kind': 'worktree', 'cwd': str(self.repo), 'access': 'write', 'writeScope': ['.'],
                  'base': {'kind': 'working-tree'},
                  'integrator': {'role': 'host', 'id': 'fixture-host'}}
        with storage.locked(board.store):
            with board.store.workflow._submission_preparation('unrevoked-request', intent) as prepared:
                directory = board.directory / 'workspaces' / prepared['manifest']['workspaceId']
                self.assertTrue((directory / 'request.json').is_file())

    def test_same_request_preparation_clears_old_revocation_before_writing_new_files(self):
        board = self.board()
        request_id = 'same-request-new-generation'
        _, directory = workspace._workspace_directory(board.directory, request_id)
        with workspace._lock(directory), storage.locked(board.store):
            allocation_revocations.record(board.store, directory, request_id)
        intent = {'kind': 'worktree', 'cwd': str(self.repo), 'access': 'write', 'writeScope': ['.'],
                  'base': {'kind': 'working-tree'},
                  'integrator': {'role': 'host', 'id': 'fixture-host'}}
        write = workspace._write_once
        request_writes = []
        def write_new_request(path, data):
            if path == directory / 'request.json':
                with board.store.db.read() as connection:
                    self.assertIsNone(connection.execute(
                        'SELECT 1 FROM allocation_revocations WHERE allocation_id=?',
                        (directory.name,)).fetchone())
                request_writes.append(True)
            return write(path, data)
        with patch.object(workspace, '_write_once', side_effect=write_new_request):
            with board.store.workflow._submission_preparation(request_id, intent) as prepared:
                self.assertEqual(prepared['manifest']['workspaceId'], directory.name)
                with board.store.db.read() as connection:
                    self.assertIsNone(connection.execute(
                        'SELECT 1 FROM allocation_revocations WHERE allocation_id=?',
                        (directory.name,)).fetchone())
                self.assertTrue((directory / 'request.json').is_file())
                (directory / 'new-file.txt').write_text('preserve new owner', encoding='utf-8')

        self.assertTrue(request_writes)
        Reclamation(board.store).run()
        self.assertEqual((directory / 'new-file.txt').read_text(encoding='utf-8'), 'preserve new owner')
        self.assertTrue((directory / 'request.json').exists())
