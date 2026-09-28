"""Windows publication state-machine tests on private macOS/POSIX directories."""
import json
import os
import shutil
from unittest import mock

from buddy import backup
from buddy.errors import BoardError
from support import BoardTestCase


class WindowsBackupTests(BoardTestCase):
    def setUp(self):
        super().setUp()
        platform = mock.patch.object(backup, '_windows', return_value=True)
        platform.start()
        self.addCleanup(platform.stop)

    def _board(self):
        board = self.board()
        marker = board.directory / 'controls/version.json'
        marker.parent.mkdir(exist_ok=True)
        marker.write_text(json.dumps('old'))
        root = board.directory / 'backups'
        if root.exists():
            shutil.rmtree(root)
        return board, marker, root

    @staticmethod
    def _generation(root):
        return json.loads((root / 'current/state/controls/version.json').read_text())

    def _assert_settled(self, root, expected):
        self.assertEqual(self._generation(root), expected)
        backup.verify(root / 'current')
        self.assertFalse(any((root / name).exists() for name in ('.incoming', '.previous', '.publish.json', '.publish.tmp')))

    def test_first_and_repeated_publication(self):
        board, marker, root = self._board()
        board.call('backup', {})
        self._assert_settled(root, 'old')
        marker.write_text(json.dumps('new'))
        board.call('backup', {})
        self._assert_settled(root, 'new')

    def test_every_publication_rename_boundary_recovers(self):
        # Each rename can fail normally or stop the process on either side.
        for repeated in (False, True):
            for source, target in (('.publish.tmp', '.publish.json'), ('current', '.previous'), ('.incoming', 'current')):
                if not repeated and source == 'current':
                    continue
                for timing in ('error_before', 'interrupt_before', 'interrupt_after'):
                    after = timing == 'interrupt_after'
                    failure = OSError if timing == 'error_before' else KeyboardInterrupt
                    with self.subTest(repeated=repeated, rename=(source, target), timing=timing):
                        board, marker, root = self._board()
                        if repeated:
                            board.call('backup', {})
                            marker.write_text(json.dumps('new'))
                        real_rename = backup._rename
                        fired = False

                        def interrupted(left, right):
                            nonlocal fired
                            if not fired and left.name == source and right.name == target:
                                fired = True
                                if after:
                                    real_rename(left, right)
                                raise failure('simulated rename failure')
                            real_rename(left, right)

                        with mock.patch.object(backup, '_rename', side_effect=interrupted):
                            with self.assertRaises(failure):
                                backup.create(board.store)
                        self.assertTrue(fired)
                        if not repeated and source == '.publish.tmp' and timing == 'error_before':
                            # No journal was committed. The next backup can
                            # safely rebuild the first generation.
                            board.call('backup', {})
                        else:
                            backup.recover(board.directory)
                        expected = 'new' if source == '.incoming' and after else 'old'
                        if not repeated:
                            expected = 'old'
                        self._assert_settled(root, expected)

    def test_bad_candidate_and_interrupted_rollback_keep_old(self):
        board, marker, root = self._board()
        board.call('backup', {})
        marker.write_text(json.dumps('new'))
        with mock.patch.object(backup, 'verify', side_effect=BoardError('BACKUP_INVALID', 'candidate rejected')):
            with self.assertRaises(BoardError):
                board.call('backup', {})
        self._assert_settled(root, 'old')

        for source, target in (('current', '.incoming'), ('.previous', 'current')):
            for after in (False, True):
                with self.subTest(rename=(source, target), after=after):
                    previous = root / '.previous'
                    shutil.copytree(root / 'current', previous)
                    (root / 'current/state/controls/version.json').write_text('corrupt')
                    (root / '.publish.json').write_text(json.dumps({'format': 1, 'hadCurrent': True}))
                    real_rename = backup._rename
                    fired = False

                    def interrupted(left, right):
                        nonlocal fired
                        if not fired and left.name == source and right.name == target:
                            fired = True
                            if after:
                                real_rename(left, right)
                            raise KeyboardInterrupt('simulated stop')
                        real_rename(left, right)

                    with mock.patch.object(backup, '_rename', side_effect=interrupted):
                        with self.assertRaises(KeyboardInterrupt):
                            backup.recover(board.directory)
                    self.assertTrue(fired)
                    backup.recover(board.directory)
                    self._assert_settled(root, 'old')

    def test_bad_candidate_after_old_rename_restores_old(self):
        board, marker, root = self._board()
        board.call('backup', {})
        marker.write_text(json.dumps('new'))
        real_rename = backup._rename

        def stop_after_old(left, right):
            real_rename(left, right)
            if left.name == 'current' and right.name == '.previous':
                raise KeyboardInterrupt('simulated stop')

        with mock.patch.object(backup, '_rename', side_effect=stop_after_old):
            with self.assertRaises(KeyboardInterrupt):
                board.call('backup', {})
        (root / '.incoming/state/controls/version.json').write_text('corrupt')
        backup.verify(root / 'current')  # The read API must recover before use.
        self._assert_settled(root, 'old')

    def test_next_backup_recovers_before_building_its_candidate(self):
        board, marker, root = self._board()
        board.call('backup', {})
        marker.write_text(json.dumps('new'))
        real_rename = backup._rename

        def stop_after_old(left, right):
            real_rename(left, right)
            if left.name == 'current' and right.name == '.previous':
                raise KeyboardInterrupt('simulated stop')

        with mock.patch.object(backup, '_rename', side_effect=stop_after_old):
            with self.assertRaises(KeyboardInterrupt):
                board.call('backup', {})
        board.call('backup', {})
        self._assert_settled(root, 'new')

    def test_interrupted_old_generation_retirement_keeps_new(self):
        for after in (False, True):
            with self.subTest(after=after):
                board, marker, root = self._board()
                board.call('backup', {})
                marker.write_text(json.dumps('new'))
                real_remove = shutil.rmtree
                fired = False

                def interrupted(path, *args, **kwargs):
                    nonlocal fired
                    if not fired and path == root / '.previous':
                        fired = True
                        if after:
                            real_remove(path, *args, **kwargs)
                        raise KeyboardInterrupt('simulated stop')
                    return real_remove(path, *args, **kwargs)

                with mock.patch.object(backup.shutil, 'rmtree', side_effect=interrupted):
                    with self.assertRaises(KeyboardInterrupt):
                        board.call('backup', {})
                self.assertTrue(fired)
                backup.recover(board.directory)
                self._assert_settled(root, 'new')

    def test_reject_links_and_forged_journal_paths(self):
        board, _, root = self._board()
        board.call('backup', {})
        outside = self.workdir('outside')
        for name in ('.incoming', '.previous', '.publish.json', '.publish.tmp'):
            with self.subTest(link=name):
                linked = root / name
                linked.symlink_to(outside)
                try:
                    with self.assertRaises(BoardError) as raised:
                        backup.recover(board.directory)
                    self.assertEqual(raised.exception.code, 'BACKUP_UNSAFE_PATH')
                finally:
                    linked.unlink()
        journal = root / '.publish.json'
        journal.write_text(json.dumps({'format': 1, 'hadCurrent': True, 'previous': str(outside)}))
        with self.assertRaises(BoardError) as raised:
            backup.recover(board.directory)
        self.assertEqual(raised.exception.code, 'BACKUP_RECOVERY_REQUIRED')
        self.assertTrue(outside.exists())
        journal.unlink()
        current = root / 'current'
        parked = root / '.parked'
        os.replace(current, parked)
        current.symlink_to(outside, target_is_directory=True)
        try:
            with self.assertRaises(BoardError) as raised:
                backup.verify(current)
            self.assertEqual(raised.exception.code, 'BACKUP_UNSAFE_PATH')
        finally:
            current.unlink()
            os.replace(parked, current)
        manifest = current / 'manifest.json'
        saved = root / '.saved-manifest'
        os.replace(manifest, saved)
        manifest.symlink_to(saved)
        try:
            with self.assertRaises(BoardError) as raised:
                backup.verify(current)
            self.assertEqual(raised.exception.code, 'BACKUP_UNSAFE_PATH')
        finally:
            manifest.unlink()
            os.replace(saved, manifest)
        parked_root = board.directory / '.parked-backups'
        os.replace(root, parked_root)
        root.symlink_to(outside, target_is_directory=True)
        try:
            with self.assertRaises(BoardError) as raised:
                backup.recover(board.directory)
            self.assertEqual(raised.exception.code, 'BACKUP_UNSAFE_PATH')
        finally:
            root.unlink()
            os.replace(parked_root, root)
