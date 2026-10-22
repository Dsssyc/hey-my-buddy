"""Backup publication state-machine tests on private directories."""
import gzip
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from unittest import mock

from hey_my_buddy.protocol.contracts import CONTRACT_VERSION
from hey_my_buddy.blackboard.store import backup
from hey_my_buddy.errors import BoardError
from support import BoardTestCase


class BackupPublicationTests(BoardTestCase):
    def _board(self):
        board = self.board()
        board.store.db.close()
        marker = board.directory / 'console-settings.json'
        marker.write_text(json.dumps({'generation': 'old'}), encoding="utf-8", newline="\n")
        marker.chmod(0o600)
        root = board.directory / 'backups'
        if root.exists():
            shutil.rmtree(root)
        return board, marker, root

    @staticmethod
    def _generation(root):
        return json.loads((root / 'current/state/console-settings.json').read_text(encoding="utf-8"))['generation']

    def _assert_settled(self, root, expected):
        self.assertEqual(self._generation(root), expected)
        backup.verify(root / 'current')
        self.assertEqual(gzip.decompress((root / 'current/board.sqlite3.gz').read_bytes()),
                         (root.parent / 'board.sqlite3').read_bytes())
        self.assertEqual([path.name for path in root.iterdir() if path.is_dir()], ['current'])
        self.assertFalse(any((root / name).exists() for name in ('.incoming', '.previous', '.publish.json')))

    def test_killed_writer_recovers_whole_generation_and_retains_sole_copy(self):
        # The child announces a publication boundary and waits to be killed.
        # A new reader then exercises recovery against the on-disk state.
        script = """
import gzip
import os
from pathlib import Path
import shutil
import sys
import time
from unittest import mock
from hey_my_buddy.blackboard.store import backup
from hey_my_buddy.protocol.contracts import CONTRACT_VERSION

state, marker, phase = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3]
rename = backup._rename
link = os.link
remove = shutil.rmtree
gzip_write, gzip_close, fsync = gzip.GzipFile.write, gzip.GzipFile.close, os.fsync

def pause():
    marker.write_text('ready', encoding='ascii')
    while True:
        time.sleep(60)

def hooked_rename(source, target):
    rename(source, target)
    if phase == 'old' and source.name == 'current' and target.name == '.previous':
        pause()
    if phase == 'new' and source.name == '.incoming' and target.name == 'current':
        pause()

def hooked_link(source, target, *args, **kwargs):
    if phase == 'journal_before' and Path(target).name == '.publish.json':
        pause()
    result = link(source, target, *args, **kwargs)
    if phase == 'journal_after' and Path(target).name == '.publish.json':
        pause()
    return result

def hooked_remove(path, *args, **kwargs):
    if phase == 'cleanup' and Path(path).name == '.previous':
        pause()
    result = remove(path, *args, **kwargs)
    if phase == 'after_cleanup' and Path(path).name == '.previous':
        pause()
    return result

def hooked_gzip_write(writer, data):
    if phase == 'gzip_body':
        gzip_write(writer, data[:len(data) // 2])
        writer.fileobj.flush()
        pause()
    return gzip_write(writer, data)

def hooked_gzip_close(writer):
    writing = writer.mode == gzip.WRITE and writer.fileobj is not None
    result = gzip_close(writer)
    if phase == 'gzip_footer' and writing:
        pause()
    return result

def hooked_fsync(fd):
    compressed = state / 'backups/.incoming/board.sqlite3.gz'
    is_payload = compressed.exists() and os.path.samestat(os.fstat(fd), compressed.stat())
    if phase == 'gzip_before_sync' and is_payload:
        pause()
    result = fsync(fd)
    if phase == 'gzip_after_sync' and is_payload:
        pause()
    return result

with (mock.patch.object(backup, '_rename', side_effect=hooked_rename),
      mock.patch.object(os, 'link', side_effect=hooked_link),
      mock.patch.object(backup.shutil, 'rmtree', side_effect=hooked_remove),
      mock.patch.object(gzip.GzipFile, 'write', hooked_gzip_write),
      mock.patch.object(gzip.GzipFile, 'close', hooked_gzip_close),
      mock.patch.object(os, 'fsync', side_effect=hooked_fsync)):
    backup.create(state, runtime_identity=None, plugin_commit=None, contract_version=CONTRACT_VERSION)
"""
        for phase, repeated, expected in (
            *((phase, repeated, 'old') for phase in ('journal_before', 'journal_after')
              for repeated in (False, True)),
            ('old', True, 'old'),
            ('new', True, 'new'),
            ('cleanup', True, 'new'),
            ('after_cleanup', True, 'new'),
            *((phase, repeated, 'old') for phase in
              ('gzip_body', 'gzip_footer', 'gzip_before_sync', 'gzip_after_sync')
              for repeated in (False, True)),
        ):
            with self.subTest(phase=phase, repeated=repeated):
                board, marker, root = self._board()
                if repeated:
                    backup.create(board.directory, runtime_identity=None, plugin_commit=None,
                                  contract_version=CONTRACT_VERSION)
                    marker.write_text(json.dumps({'generation': 'new'}), encoding='utf-8')
                pause_marker = self.workdir('backup-kill-' + phase) / 'ready'
                child_env = os.environ.copy()
                for name in ('HMB_STATE_DIR', 'HMB_RUNTIME_ROOT', 'HMB_RUNTIME', 'HMB_RUNTIME_IDENTITY',
                             'HMB_AGENT_CREDENTIAL_FILE', 'HMB_AGENT_CREDENTIAL_FD', 'VIRTUAL_ENV',
                             'UV_PROJECT_ENVIRONMENT'):
                    child_env.pop(name, None)
                process = subprocess.Popen([sys.executable, '-c', script, str(board.directory),
                                            str(pause_marker), phase], stdout=subprocess.DEVNULL,
                                           stderr=subprocess.PIPE, text=True, env=child_env)
                try:
                    deadline = time.monotonic() + 15
                    while not pause_marker.exists() and process.poll() is None and time.monotonic() < deadline:
                        time.sleep(0.02)
                    self.assertTrue(pause_marker.exists(), process.stderr.read() if process.poll() is not None else
                                    'writer did not reach publication boundary')
                    process.kill()
                    process.communicate(timeout=5)
                    backup.recover(board.directory)
                    if phase.startswith('gzip_') and not repeated:
                        self.assertFalse((root / 'current').exists())
                        with self.assertRaises(BoardError):
                            backup.verify(root / 'current')
                        backup.create(board.directory, runtime_identity=None, plugin_commit=None,
                                      contract_version=CONTRACT_VERSION)
                    self._assert_settled(root, expected)
                finally:
                    if process.poll() is None:
                        process.kill()
                        process.communicate(timeout=5)

    def test_first_and_repeated_publication(self):
        board, marker, root = self._board()
        backup.create(board.directory, runtime_identity=None, plugin_commit=None, contract_version=CONTRACT_VERSION)
        self._assert_settled(root, 'old')
        marker.write_text(json.dumps({'generation': 'new'}), encoding="utf-8", newline="\n")
        backup.create(board.directory, runtime_identity=None, plugin_commit=None, contract_version=CONTRACT_VERSION)
        self._assert_settled(root, 'new')

    def test_incomplete_first_candidate_allows_rebuild(self):
        board, _, root = self._board()
        (root / '.incoming').mkdir(parents=True)
        (root / '.incoming/board.sqlite3.gz').write_bytes(b'incomplete')
        self.assertEqual(backup.recover(board.directory), root / 'current')
        self.assertFalse((root / 'current').exists())
        backup.create(board.directory, runtime_identity=None, plugin_commit=None,
                      contract_version=CONTRACT_VERSION)
        self._assert_settled(root, 'old')

    def test_every_publication_rename_boundary_recovers(self):
        # Each rename can fail normally or stop the process on either side.
        for repeated in (False, True):
            for source, target in (('current', '.previous'), ('.incoming', 'current')):
                if not repeated and source == 'current':
                    continue
                for timing in ('error_before', 'interrupt_before', 'interrupt_after'):
                    after = timing == 'interrupt_after'
                    failure = OSError if timing == 'error_before' else KeyboardInterrupt
                    with self.subTest(repeated=repeated, rename=(source, target), timing=timing):
                        board, marker, root = self._board()
                        if repeated:
                            backup.create(board.directory, runtime_identity=None, plugin_commit=None, contract_version=CONTRACT_VERSION)
                            marker.write_text(json.dumps({'generation': 'new'}), encoding="utf-8", newline="\n")
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
                                backup.create(board.directory, runtime_identity=None, plugin_commit=None, contract_version=CONTRACT_VERSION)
                        self.assertTrue(fired)
                        backup.recover(board.directory)
                        expected = 'new' if source == '.incoming' and after else 'old'
                        if not repeated:
                            expected = 'old'
                        self._assert_settled(root, expected)

    def test_journal_link_boundary_recovers(self):
        for repeated in (False, True):
            for timing in ('error_before', 'interrupt_before', 'interrupt_after'):
                with self.subTest(repeated=repeated, timing=timing):
                    board, marker, root = self._board()
                    if repeated:
                        backup.create(board.directory, runtime_identity=None, plugin_commit=None,
                                      contract_version=CONTRACT_VERSION)
                        marker.write_text(json.dumps({'generation': 'new'}), encoding='utf-8')
                    real_link = os.link
                    fired = False

                    def interrupted(source, target, *args, **kwargs):
                        nonlocal fired
                        if not fired and Path(target).name == '.publish.json':
                            fired = True
                            if timing == 'interrupt_after':
                                real_link(source, target, *args, **kwargs)
                            failure = OSError if timing == 'error_before' else KeyboardInterrupt
                            raise failure('simulated journal link failure')
                        return real_link(source, target, *args, **kwargs)

                    failure = OSError if timing == 'error_before' else KeyboardInterrupt
                    with mock.patch.object(os, 'link', side_effect=interrupted):
                        with self.assertRaises(failure):
                            backup.create(board.directory, runtime_identity=None, plugin_commit=None,
                                          contract_version=CONTRACT_VERSION)
                    self.assertTrue(fired)
                    if not repeated and timing != 'interrupt_after':
                        # The verified candidate remains the sole complete copy.
                        backup._verify(root / '.incoming')
                    backup.recover(board.directory)
                    self._assert_settled(root, 'old')

    def test_bad_candidate_and_interrupted_rollback_keep_old(self):
        board, marker, root = self._board()
        backup.create(board.directory, runtime_identity=None, plugin_commit=None, contract_version=CONTRACT_VERSION)
        marker.write_text(json.dumps({'generation': 'new'}), encoding="utf-8", newline="\n")
        with mock.patch.object(backup, 'verify', side_effect=BoardError('BACKUP_INVALID', 'candidate rejected')):
            with self.assertRaises(BoardError):
                backup.create(board.directory, runtime_identity=None, plugin_commit=None, contract_version=CONTRACT_VERSION)
        self._assert_settled(root, 'old')

        for source, target in (('current', '.incoming'), ('.previous', 'current')):
            for after in (False, True):
                with self.subTest(rename=(source, target), after=after):
                    previous = root / '.previous'
                    shutil.copytree(root / 'current', previous)
                    (root / 'current/state/console-settings.json').write_text('corrupt', encoding="utf-8", newline="\n")
                    (root / '.publish.json').write_text(json.dumps({'format': 1, 'hadCurrent': True}), encoding="utf-8", newline="\n")
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
        backup.create(board.directory, runtime_identity=None, plugin_commit=None, contract_version=CONTRACT_VERSION)
        marker.write_text(json.dumps({'generation': 'new'}), encoding="utf-8", newline="\n")
        real_rename = backup._rename

        def stop_after_old(left, right):
            real_rename(left, right)
            if left.name == 'current' and right.name == '.previous':
                raise KeyboardInterrupt('simulated stop')

        with mock.patch.object(backup, '_rename', side_effect=stop_after_old):
            with self.assertRaises(KeyboardInterrupt):
                backup.create(board.directory, runtime_identity=None, plugin_commit=None, contract_version=CONTRACT_VERSION)
        (root / '.incoming/state/console-settings.json').write_text('corrupt', encoding="utf-8", newline="\n")
        backup.verify(root / 'current')  # The read API must recover before use.
        self._assert_settled(root, 'old')

    def test_next_backup_recovers_before_building_its_candidate(self):
        board, marker, root = self._board()
        backup.create(board.directory, runtime_identity=None, plugin_commit=None, contract_version=CONTRACT_VERSION)
        marker.write_text(json.dumps({'generation': 'new'}), encoding="utf-8", newline="\n")
        real_rename = backup._rename

        def stop_after_old(left, right):
            real_rename(left, right)
            if left.name == 'current' and right.name == '.previous':
                raise KeyboardInterrupt('simulated stop')

        with mock.patch.object(backup, '_rename', side_effect=stop_after_old):
            with self.assertRaises(KeyboardInterrupt):
                backup.create(board.directory, runtime_identity=None, plugin_commit=None, contract_version=CONTRACT_VERSION)
        backup.create(board.directory, runtime_identity=None, plugin_commit=None, contract_version=CONTRACT_VERSION)
        self._assert_settled(root, 'new')

    def test_interrupted_old_generation_retirement_keeps_new(self):
        for after in (False, True):
            with self.subTest(after=after):
                board, marker, root = self._board()
                backup.create(board.directory, runtime_identity=None, plugin_commit=None, contract_version=CONTRACT_VERSION)
                marker.write_text(json.dumps({'generation': 'new'}), encoding="utf-8", newline="\n")
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
                        backup.create(board.directory, runtime_identity=None, plugin_commit=None, contract_version=CONTRACT_VERSION)
                self.assertTrue(fired)
                backup.recover(board.directory)
                self._assert_settled(root, 'new')

    def test_reject_links_and_forged_journal_paths(self):
        board, _, root = self._board()
        backup.create(board.directory, runtime_identity=None, plugin_commit=None, contract_version=CONTRACT_VERSION)
        outside = self.workdir('outside')
        for name, expected_code in (('.incoming', 'BACKUP_UNSAFE_PATH'),
                                    ('.previous', 'BACKUP_RECOVERY_REQUIRED'),
                                    ('.publish.json', 'BACKUP_UNSAFE_PATH')):
            with self.subTest(link=name):
                linked = root / name
                linked.symlink_to(outside)
                try:
                    with self.assertRaises(BoardError) as raised:
                        backup.recover(board.directory)
                    self.assertEqual(raised.exception.code, expected_code)
                    self.assertTrue(outside.exists())
                finally:
                    linked.unlink()
        journal = root / '.publish.json'
        journal.write_text(json.dumps({'format': 1, 'hadCurrent': True, 'previous': str(outside)}), encoding="utf-8", newline="\n")
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
