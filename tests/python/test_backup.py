"""Private backup publication and recovery evidence; never touches daily state."""
from pathlib import Path
from unittest import mock
import json

from buddy import backup
from buddy.errors import BoardError
from support import BoardTestCase


class BackupTests(BoardTestCase):
    def test_roundtrip_one_generation_and_excluded_regenerable_data(self):
        board = self.board()
        for name in ('controls/x.json', 'submissions/y.json', 'attempts/run/attempt/result.json', 'workers/local/receipts/a.json', 'workspaces/large', 'harnesses/zcode/native'):
            p = board.directory / name
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text('{}')
        first = board.call('backup', {})
        current = Path(first['path'])
        manifest = backup.verify(current)
        self.assertTrue(first['verified'])
        self.assertIn('state/controls/x.json', manifest['files'])
        self.assertIn('state/workers/local/receipts/a.json', manifest['files'])
        self.assertFalse(any('workspaces' in p or 'harnesses' in p for p in manifest['files']))
        (board.directory / 'controls/x.json').write_text('{"new":true}')
        second = board.call('backup', {})
        self.assertEqual(first['path'], second['path'])
        self.assertEqual(json.loads((current / 'state/controls/x.json').read_text()), {'new': True})
        self.assertEqual([p.name for p in current.parent.iterdir() if p.is_dir()], ['current'])
        self.assertEqual((current / 'state/controls/x.json').stat().st_mode & 0o777, 0o600)

    def test_failed_validation_keeps_old_backup_and_retries_incoming(self):
        board = self.board()
        current = Path(board.call('backup', {})['path'])
        original = (current / 'manifest.json').read_bytes()
        with mock.patch('buddy.backup.verify', side_effect=BoardError('BACKUP_INVALID', 'injected')):
            with self.assertRaises(BoardError):
                board.call('backup', {})
        self.assertEqual((current / 'manifest.json').read_bytes(), original)
        board.call('backup', {})
        backup.verify(current)
        self.assertFalse((current.parent / '.incoming').exists())

    def test_tamper_is_detected_and_source_symlink_refused(self):
        board = self.board()
        current = Path(board.call('backup', {})['path'])
        with (current / 'board.sqlite3.gz').open('ab') as stream:
            stream.write(b'tamper')
        with self.assertRaises(BoardError):
            backup.verify(current)
        (board.directory / 'controls').mkdir(exist_ok=True)
        (board.directory / 'controls/link').symlink_to(board.directory / 'board.sqlite3')
        with self.assertRaises(BoardError) as error:
            board.call('backup', {})
        self.assertEqual(error.exception.code, 'BACKUP_UNSAFE_PATH')
