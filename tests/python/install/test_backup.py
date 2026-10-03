"""Private backup publication and recovery evidence; never touches daily state."""
import os
from pathlib import Path
from unittest import mock
import json

from hey_my_buddy.blackboard.store import backup
from hey_my_buddy.errors import BoardError
from support import BoardTestCase


class BackupTests(BoardTestCase):
    def test_roundtrip_one_generation_and_excluded_regenerable_data(self):
        board = self.board()
        for name in ('controls/x.json', 'submissions/y.json', 'attempts/run/attempt/result.json', 'workers/local/receipts/a.json', 'workers/local/startup.json', 'worker-pool.json', 'workspaces/large', 'harnesses/zcode/native'):
            p = board.directory / name
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text('{}')
        first = board.call('backup', {})
        current = Path(first['path'])
        manifest = backup.verify(current)
        self.assertTrue(first['verified'])
        self.assertIn('state/controls/x.json', manifest['files'])
        self.assertIn('state/workers/local/receipts/a.json', manifest['files'])
        self.assertIn('state/workers/local/startup.json', manifest['files'])
        self.assertIn('state/worker-pool.json', manifest['files'])
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
        with mock.patch('hey_my_buddy.blackboard.store.backup.verify', side_effect=BoardError('BACKUP_INVALID', 'injected')):
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
        self.assertEqual(error.exception.details['path'], 'controls/link')
        self.assertIn('controls/link', error.exception.message)

    def test_linked_control_directory_is_refused_with_relative_path(self):
        board = self.board()
        (board.directory / 'controls').mkdir()
        (board.directory / 'controls/sub').symlink_to(self.directory)
        with self.assertRaises(BoardError) as error:
            board.call('backup', {})
        self.assertEqual(error.exception.code, 'BACKUP_UNSAFE_PATH')
        self.assertEqual(error.exception.details['path'], 'controls/sub')
        self.assertIn('controls/sub', error.exception.message)

    def test_attempt_private_native_home_and_provider_snapshots_are_excluded(self):
        board = self.board()
        attempt = board.directory / 'attempts/run/attempt'
        home = attempt / 'native/codex-home'
        home.mkdir(parents=True)
        secret = self.directory / 'private-auth.json'
        secret.write_text('private auth must not be copied')
        (home / 'auth.json').symlink_to(secret)
        (home / 'native.sqlite').write_bytes(b'native cache')
        for name in ('builtin-provider.json', 'personal-provider.json'):
            (attempt / name).write_text('private provider key')
            (attempt / 'native' / name).write_text('retained native record')
        (attempt / 'result.json').write_text('{"status":"ok"}')

        current = Path(board.call('backup', {})['path'])
        manifest = backup.verify(current)
        entries = set(manifest['files'])
        self.assertIn('state/attempts/run/attempt/result.json', entries)
        self.assertNotIn('state/attempts/run/attempt/native/builtin-provider.json', entries)
        self.assertNotIn('state/attempts/run/attempt/native/personal-provider.json', entries)
        self.assertFalse(any(name.startswith('state/attempts/run/attempt/native/codex-home/') for name in entries))
        self.assertNotIn('state/attempts/run/attempt/builtin-provider.json', entries)
        self.assertNotIn('state/attempts/run/attempt/personal-provider.json', entries)
        self.assertTrue((home / 'auth.json').is_symlink())
        self.assertEqual(secret.read_text(), 'private auth must not be copied')

    def test_no_tool_private_homes_are_excluded_and_call_evidence_kept(self):
        board = self.board()
        invocation = board.directory / ('attempts/run/attempt/no-tool-' + 'a' * 32)
        secret = self.directory / 'private-auth.json'
        secret.write_text('private credential')
        dsh_modules = invocation / 'dsh-home/profiles/headless/node_modules'
        dsh_modules.mkdir(parents=True)
        (dsh_modules / '.bin').symlink_to(secret)
        (dsh_modules / 'package.json').write_text('{"private":true}')
        codex_home = invocation / 'native/codex-home'
        codex_home.mkdir(parents=True)
        (codex_home / 'auth.json').symlink_to(secret)
        (codex_home / 'config.toml').write_text('private codex config')
        zcode_state = invocation / 'native/storage'
        zcode_state.mkdir(parents=True)
        (zcode_state / 'session.db').write_bytes(b'native session state')
        for name in ('builtin-provider.json', 'personal-provider.json'):
            (invocation / name).write_text('private provider key')
        (invocation / 'native.stderr.log').write_text('native stderr evidence')
        (invocation / 'call-1').mkdir(parents=True)
        (invocation / 'call-1/request.json').write_text('{"prompt":"call evidence"}')
        (invocation / 'call-1/result.json').write_text('{"status":"ok"}')

        current = Path(board.call('backup', {})['path'])
        manifest = backup.verify(current)
        entries = set(manifest['files'])
        prefix = 'state/attempts/run/attempt/no-tool-' + 'a' * 32 + '/'
        self.assertIn(prefix + 'call-1/request.json', entries)
        self.assertIn(prefix + 'call-1/result.json', entries)
        self.assertIn(prefix + 'native.stderr.log', entries)
        self.assertFalse(any(name.startswith(prefix + 'dsh-home/') for name in entries))
        self.assertFalse(any(name.startswith(prefix + 'native/') for name in entries))
        self.assertNotIn(prefix + 'builtin-provider.json', entries)
        self.assertNotIn(prefix + 'personal-provider.json', entries)
        self.assertEqual(manifest['skippedAttemptEntries']['count'], 4)
        self.assertTrue((dsh_modules / '.bin').is_symlink())
        self.assertTrue((codex_home / 'auth.json').is_symlink())
        self.assertEqual(secret.read_text(), 'private credential')
        leaked = [name for name, row in manifest['files'].items()
                  if any(secret_bytes in (current / name).read_bytes()
                         for secret_bytes in (b'private credential', b'private codex config',
                                              b'native session state', b'private provider key'))]
        self.assertEqual(leaked, [])

    def test_undeclared_attempt_symlinks_are_skipped_and_recorded(self):
        board = self.board()
        attempt = board.directory / 'attempts/run/attempt'
        attempt.mkdir(parents=True)
        secret = self.directory / 'leak.txt'
        secret.write_text('link target must not be copied')
        (attempt / 'unregistered.json').symlink_to(secret)
        (attempt / 'linked-dir').symlink_to(self.directory)
        (attempt / 'task.txt').write_text('{"kept":true}')

        result = board.call('backup', {})
        current = Path(result['path'])
        manifest = backup.verify(current)
        entries = set(manifest['files'])
        self.assertIn('state/attempts/run/attempt/task.txt', entries)
        self.assertNotIn('state/attempts/run/attempt/unregistered.json', entries)
        self.assertFalse(any('linked-dir' in name for name in entries))
        self.assertEqual(result['skippedAttemptEntries'], 2)
        self.assertEqual(manifest['skippedAttemptEntries'],
                         {'count': 2, 'paths': ['attempts/run/attempt/linked-dir', 'attempts/run/attempt/unregistered.json']})
        self.assertTrue((attempt / 'unregistered.json').is_symlink())
        self.assertEqual(secret.read_text(), 'link target must not be copied')
        leaked = [name for name in entries if (current / name).read_bytes().find(b'link target must not be copied') >= 0]
        self.assertEqual(leaked, [])

    def test_nonregular_attempt_entry_is_skipped(self):
        board = self.board()
        attempt = board.directory / 'attempts/run/attempt'
        attempt.mkdir(parents=True)
        os.mkfifo(attempt / 'pipe')
        result = board.call('backup', {})
        manifest = backup.verify(Path(result['path']))
        self.assertNotIn('state/attempts/run/attempt/pipe', manifest['files'])
        self.assertEqual(manifest['skippedAttemptEntries'],
                         {'count': 1, 'paths': ['attempts/run/attempt/pipe']})

    def test_skip_recording_is_bounded_to_twenty_paths(self):
        board = self.board()
        attempt = board.directory / 'attempts/run/attempt'
        attempt.mkdir(parents=True)
        secret = self.directory / 'leak.txt'
        secret.write_text('target')
        for index in range(25):
            (attempt / ('link-%02d' % index)).symlink_to(secret)

        result = board.call('backup', {})
        manifest = backup.verify(Path(result['path']))
        recorded = manifest['skippedAttemptEntries']
        self.assertEqual(result['skippedAttemptEntries'], 25)
        self.assertEqual(recorded['count'], 25)
        self.assertEqual(len(recorded['paths']), 20)
        self.assertEqual(recorded['paths'], sorted(recorded['paths']))
        self.assertTrue(all(path.startswith('attempts/run/attempt/link-') for path in recorded['paths']))

    def test_no_tool_exclusion_requires_the_exact_structural_name(self):
        board = self.board()
        imprecise = board.directory / 'attempts/run/attempt/no-tool-not-a-hex-name/dsh-home'
        imprecise.mkdir(parents=True)
        secret = self.directory / 'leak.txt'
        secret.write_text('target')
        (imprecise / 'link').symlink_to(secret)

        result = board.call('backup', {})
        manifest = backup.verify(Path(result['path']))
        self.assertEqual(manifest['skippedAttemptEntries'],
                         {'count': 1, 'paths': ['attempts/run/attempt/no-tool-not-a-hex-name']})
        self.assertEqual(secret.read_text(), 'target')
