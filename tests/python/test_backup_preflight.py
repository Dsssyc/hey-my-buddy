"""Evidence policy and read-only inventory on disposable boards only."""
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
from types import SimpleNamespace
from unittest import mock

from buddy import attempt_evidence, backup, private_dirs
from buddy.errors import BoardError
from support import BoardTestCase, DELEGATE_ROOT, _child_environment


class BackupPreflightTests(BoardTestCase):
    def files(self, *names):
        board = self.board()
        root = board.directory / 'attempts/run/attempt'
        root.mkdir(parents=True, exist_ok=True)
        for name in names:
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('{}')
        return board, root

    def test_allowlist_retains_only_declared_regular_evidence_and_counts_private_trees(self):
        board, root = self.files('task.txt', 'activity.json', 'plain.json', 'agent-credential.json',
                                'claude-private/settings.json', 'sessions/native.json',
                                'no-tool-' + 'a' * 32 + '/call-1/request.json',
                                'no-tool-' + 'a' * 32 + '/call-1/patch.json')
        report = backup.preflight(board.directory)
        # Skipped entries without an owning stopped attempt have no relocation
        # plan, so the report is not ok even though nothing is refused.
        self.assertFalse(report['ok'])
        self.assertTrue(report['needsAttention'])
        self.assertEqual(report['skipped']['count'], 5)
        self.assertEqual(report['legacyPlan']['ready'], False)
        self.assertEqual(report['legacyPlan']['uncovered']['count'], 5)
        self.assertEqual(len(report['legacyPlan']['uncovered']['entries']), 5)
        self.assertEqual({row['path'] for row in report['legacyPlan']['uncovered']['entries']},
                         {'attempts/run/attempt/plain.json', 'attempts/run/attempt/agent-credential.json',
                          'attempts/run/attempt/claude-private', 'attempts/run/attempt/sessions',
                          'attempts/run/attempt/no-tool-' + 'a' * 32 + '/call-1/patch.json'})
        self.assertEqual({row['reason'] for row in report['legacyPlan']['uncovered']['entries']},
                         {'not-in-relocation-plan'})
        result = backup.create(board.store)
        manifest = backup.verify(Path(result['path']))
        copied = set(manifest['files'])
        self.assertIn('state/attempts/run/attempt/task.txt', copied)
        self.assertIn('state/attempts/run/attempt/no-tool-' + 'a' * 32 + '/call-1/request.json', copied)
        self.assertFalse(any('private' in path or 'agent-credential' in path or 'patch.json' in path for path in copied))
        self.assertEqual(manifest['skippedAttemptEntries']['count'], 5)
        self.assertEqual(manifest['attemptEvidencePolicy'], attempt_evidence.POLICY)

    def test_declared_links_special_files_and_directory_type_changes_refuse_with_path(self):
        for kind in ('file-link', 'directory-link', 'fifo', 'directory-instead-of-file', 'file-instead-of-directory'):
            with self.subTest(kind=kind):
                board, root = self.files()
                leaf = 'dsh-run' if kind in ('directory-link', 'file-instead-of-directory') else 'task.txt'
                path = root / leaf
                if kind.endswith('link'):
                    path.symlink_to(self.directory)
                elif kind == 'fifo':
                    os.mkfifo(path)
                elif kind == 'directory-instead-of-file':
                    path.mkdir()
                else:
                    path.write_text('invalid')
                report = backup.preflight(board.directory)
                self.assertFalse(report['ok'])
                self.assertEqual(report['rejected']['paths'], ['attempts/run/attempt/' + leaf])
                with self.assertRaises(BoardError) as error:
                    backup.create(board.store)
                self.assertEqual(error.exception.code, 'BACKUP_UNSAFE_PATH')
                self.assertEqual(error.exception.details['path'], 'attempts/run/attempt/' + leaf)
                if path.is_dir() and not private_dirs.linked(path):
                    path.rmdir()
                else:
                    path.unlink()

    def test_unknown_link_tree_is_never_read_and_samples_are_bounded(self):
        board, root = self.files()
        outside = self.directory / 'outside'
        outside.mkdir()
        (outside / 'task.txt').write_text('private target')
        for index in range(25):
            (root / f'unknown-{index:02d}').symlink_to(outside)
        report = backup.preflight(board.directory)
        self.assertEqual(report['skipped']['count'], 25)
        self.assertEqual(len(report['skipped']['paths']), 20)
        self.assertEqual(len(list(backup.preflight_entries(board.directory))), 26)
        self.assertEqual((outside / 'task.txt').read_text(), 'private target')

    def test_cli_preflight_writes_nothing_and_starts_no_service_or_runtime(self):
        board, root = self.files('task.txt', 'unknown.json')
        runtime = self.directory / 'preflight-runtime'
        runtime.mkdir()
        def snapshot():
            return {str(path): (path.lstat().st_mtime_ns, path.lstat().st_size,
                               path.read_bytes() if path.is_file() else None)
                    for base in (board.directory, runtime) for path in [base, *base.rglob('*')]}
        before = snapshot()
        environment = _child_environment(board.directory, {'BUDDY_RUNTIME_ROOT': str(runtime)})
        result = subprocess.run([sys.executable, '-m', 'buddy.cli', 'backup-preflight', '{}'],
                                cwd=DELEGATE_ROOT, env=environment, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertEqual(json.loads(result.stdout)['skipped']['count'], 1)
        self.assertEqual(snapshot(), before)
        self.assertFalse((board.directory / 'control.json').exists())
        self.assertEqual(list(runtime.iterdir()), [])

    def test_all_windows_reparse_points_are_refused_as_declared_evidence(self):
        board, root = self.files('task.txt')
        target = root / 'task.txt'
        original = Path.lstat
        def metadata(path, *args, **kwargs):
            value = original(path, *args, **kwargs)
            if path == target:
                return SimpleNamespace(st_mode=stat.S_IFREG | 0o600, st_file_attributes=0x400)
            return value
        with mock.patch.object(Path, 'lstat', metadata):
            report = backup.preflight(board.directory)
        self.assertEqual(report['rejected']['entries'],
                         [{'path': 'attempts/run/attempt/task.txt', 'reason': 'linked-evidence'}])

    def test_single_checks_links_before_following_file_type_queries(self):
        outside = self.directory / 'outside-single'
        outside.mkdir()
        path = self.directory / 'linked-single'
        path.symlink_to(outside, target_is_directory=True)
        original = Path.stat
        def metadata(path, *args, **kwargs):
            if kwargs.get('follow_symlinks', True):
                raise AssertionError('Linked target was inspected')
            return original(path, *args, **kwargs)
        with mock.patch.object(Path, 'stat', metadata):
            self.assertEqual(list(backup._single(path)), [('rejected', path, 'linked-path')])

    def test_verify_rejects_linked_or_reparse_ancestors_before_reading_manifest(self):
        board, _ = self.files('task.txt')
        current = Path(backup.create(board.store)['path'])
        alias = self.directory / 'backup-parent-link'
        alias.symlink_to(current.parent, target_is_directory=True)
        def unread():
            return mock.patch.object(Path, 'open', side_effect=AssertionError('External manifest was read'))
        with unread():
            with self.assertRaises(BoardError) as caught:
                backup._verify(alias / current.name)
        self.assertEqual(caught.exception.code, 'BACKUP_UNSAFE_PATH')
        original = Path.lstat
        def reparse(path, *args, **kwargs):
            value = original(path, *args, **kwargs)
            if path == current.parent:
                return SimpleNamespace(st_mode=stat.S_IFDIR | 0o700, st_file_attributes=0x400)
            return value
        with mock.patch.object(Path, 'lstat', reparse), unread():
            with self.assertRaises(BoardError) as caught:
                backup._verify(current)
        self.assertEqual(caught.exception.code, 'BACKUP_UNSAFE_PATH')

    def test_digest_and_copy_reject_linked_source_and_destination_without_opening_them(self):
        outside = self.directory / 'outside-copy'
        outside.mkdir()
        sentinel = outside / 'sentinel'
        sentinel.write_bytes(b'retained')
        source = self.directory / 'ordinary-source'
        source.write_bytes(b'evidence')
        leaf = self.directory / 'linked-file'
        leaf.symlink_to(sentinel)
        parent = self.directory / 'linked-parent'
        parent.symlink_to(outside, target_is_directory=True)
        for path in (leaf, parent / 'sentinel'):
            with self.subTest(path=path), \
                 mock.patch.object(Path, 'open', side_effect=AssertionError('External payload was opened')), \
                 mock.patch('buddy.private_dirs.os.open', side_effect=AssertionError('External payload was opened')):
                for action in (lambda: backup.digest(path),
                               lambda: backup._private_copy(path, self.directory / 'new-copy'),
                               lambda: backup._private_copy(source, path)):
                    with self.assertRaises(BoardError) as caught:
                        action()
                    self.assertEqual(caught.exception.code, 'BACKUP_UNSAFE_PATH')
        self.assertEqual(sentinel.read_bytes(), b'retained')
        self.assertEqual(source.read_bytes(), b'evidence')

    def test_digest_copy_and_readiness_reject_reparse_entries_and_ancestors(self):
        board, _ = self.files('task.txt')
        source = board.directory.resolve() / 'controls' / 'source.json'
        source.parent.mkdir()
        source.write_bytes(b'evidence')
        destination = self.directory.resolve() / 'target' / 'sentinel'
        destination.parent.mkdir()
        destination.write_bytes(b'retained')
        original = Path.lstat
        for marked in (source, source.parent, destination, destination.parent):
            with self.subTest(marked=marked):
                def reparse(path, *args, **kwargs):
                    value = original(path, *args, **kwargs)
                    if path == marked:
                        return SimpleNamespace(st_mode=value.st_mode, st_file_attributes=0x400)
                    return value
                with mock.patch.object(Path, 'lstat', reparse), \
                     mock.patch.object(Path, 'open', side_effect=AssertionError('Reparse payload was opened')), \
                     mock.patch('buddy.private_dirs.os.open', side_effect=AssertionError('Reparse payload was opened')):
                    with self.assertRaises(BoardError) as caught:
                        backup._private_copy(source, destination)
                    self.assertEqual(caught.exception.code, 'BACKUP_UNSAFE_PATH')
                    if marked in (source, source.parent):
                        with self.assertRaises(BoardError) as caught:
                            backup.digest(source)
                        self.assertEqual(caught.exception.code, 'BACKUP_UNSAFE_PATH')
                        self.assertFalse(backup.preflight(board.directory)['ok'])
        self.assertEqual(source.read_bytes(), b'evidence')
        self.assertEqual(destination.read_bytes(), b'retained')

    def test_health_and_console_warn_from_current_inventory(self):
        board, root = self.files('agent-credential.json')
        for method in ('health', 'console_snapshot'):
            with self.subTest(method=method):
                status = board.call(method, {})['backupPreflight']
                self.assertTrue(status['needsAttention'])
                self.assertEqual(status['skipped']['paths'], ['attempts/run/attempt/agent-credential.json'])
        (root / 'agent-credential.json').unlink()
        self.assertFalse(board.call('health', {})['backupPreflight']['needsAttention'])
