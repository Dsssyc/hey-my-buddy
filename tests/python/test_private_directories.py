"""Private directory boundaries and stopped legacy relocation."""
import json
import os
from pathlib import Path
import stat
import tempfile
from types import SimpleNamespace
from unittest import mock

from hey_my_buddy import private_dirs
from hey_my_buddy.errors import BoardError
from support import BoardTestCase


class PrivateDirectoryTests(BoardTestCase):
    def test_remove_tree_retains_exact_evidence_subtree_and_existing_link_guards(self):
        root = private_dirs.ensure_private_dir(self.directory / 'private')
        failed = private_dirs.ensure_private_dir(root / 'attempts' / 'failed')
        (failed / 'unknown-events.json').write_text('failure evidence')
        native = private_dirs.ensure_private_dir(root / 'native')
        (native / 'session.json').write_text('native state')
        successful = private_dirs.ensure_private_dir(root / 'attempts' / 'completed')
        (successful / 'result.json').write_text('success evidence')
        outside = private_dirs.ensure_private_dir(self.directory / 'outside')
        (outside / 'sentinel').write_text('safe')
        (root / 'linked').symlink_to(outside, target_is_directory=True)
        self.assertFalse(private_dirs.remove_tree(root, keep=frozenset({failed})))
        self.assertEqual((failed / 'unknown-events.json').read_text(), 'failure evidence')
        self.assertEqual(list(root.iterdir()), [root / 'attempts'])
        self.assertEqual(list((root / 'attempts').iterdir()), [failed])
        self.assertEqual((outside / 'sentinel').read_text(), 'safe')
        alias = self.directory / 'alias'
        alias.symlink_to(root, target_is_directory=True)
        with self.assertRaises(BoardError):
            private_dirs.remove_tree(alias, keep=frozenset({alias / 'attempts' / 'failed'}))
        self.assertTrue(failed.exists())

    def test_execution_start_cleanup_matches_exact_private_filename(self):
        state = self.directory.resolve()
        root = private_dirs.ensure_private_dir(private_dirs.attempt_root(state, 'zcode', 'microTask', 'attempt'))
        (root / 'execution-start.json').write_text('private')
        (root / 'execution-live.json').write_text('private endpoint token')
        (root / 'execution-started.json').write_text('retain unknown filename')
        removed = private_dirs.cleanup_attempt_credentials(state, 'zcode', 'microTask', 'attempt')
        self.assertEqual(removed['count'], 1)
        self.assertFalse((root / 'execution-start.json').exists())
        self.assertTrue((root / 'execution-live.json').exists())
        self.assertEqual((root / 'execution-started.json').read_text(), 'retain unknown filename')

    def test_explicit_state_and_reparse_guard(self):
        context = SimpleNamespace(environment={'HMB_STATE_DIR': str(self.directory.resolve())},
                                  spec={'adapter': 'codex'}, task_id='microTask', attempt_id='attempt')
        root = private_dirs.context_root(context)
        self.assertEqual(root, private_dirs.attempt_root(self.directory, 'codex', 'microTask', 'attempt'))
        marker = SimpleNamespace(st_mode=stat.S_IFDIR, st_file_attributes=0x400)
        with mock.patch.object(Path, 'lstat', return_value=marker), mock.patch.object(Path, 'is_junction', return_value=False):
            self.assertTrue(private_dirs.linked(root))

    def test_remove_tree_never_follows_a_nested_link(self):
        outside = self.directory.resolve() / 'outside'
        outside.mkdir()
        sentinel = outside / 'keep'
        sentinel.write_text('safe')
        target = self.directory.resolve() / 'private'
        target.mkdir()
        (target / 'linked').symlink_to(outside, target_is_directory=True)
        (target / 'regular').write_text('remove')
        self.assertTrue(private_dirs.remove_tree(target))
        self.assertEqual(sentinel.read_text(), 'safe')

    def test_parent_guard_stops_before_inspecting_an_entry_under_a_link(self):
        outside = self.directory.resolve() / 'external-parent'
        outside.mkdir()
        (outside / 'secret').write_text('retained')
        alias = self.directory.resolve() / 'alias'
        alias.symlink_to(outside, target_is_directory=True)
        original = Path.lstat
        def metadata(path, *args, **kwargs):
            if path == alias / 'secret':
                raise AssertionError('Entry below linked ancestor was inspected')
            return original(path, *args, **kwargs)
        with mock.patch.object(Path, 'lstat', metadata):
            with self.assertRaises(BoardError) as caught:
                private_dirs._guard_parents(alias / 'secret')
        self.assertEqual(caught.exception.code, 'PRIVATE_PATH_UNSAFE')
        self.assertEqual(caught.exception.details['path'], str(alias))

    def test_exclusive_open_rejects_a_link_inserted_after_the_guard(self):
        sentinel = self.directory.resolve() / 'external-sentinel'
        sentinel.write_bytes(b'retained')
        temporary = self.directory.resolve() / 'new-file'
        original = os.open
        def raced_open(path, flags, *args, **kwargs):
            if Path(path) == temporary:
                temporary.symlink_to(sentinel)
            return original(path, flags, *args, **kwargs)
        with mock.patch('hey_my_buddy.private_dirs.os.open', side_effect=raced_open):
            with self.assertRaises(FileExistsError):
                private_dirs.open_regular_fd(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
        self.assertTrue(temporary.is_symlink())
        self.assertEqual(sentinel.read_bytes(), b'retained')

    def test_cleanup_removes_credentials_and_exact_fallback_socket_only(self):
        state = self.directory.resolve()
        root = private_dirs.ensure_private_dir(private_dirs.attempt_root(state, 'dsh', 'microTask', 'fixture-attempt'))
        outside = state / 'outside-auth'
        outside.write_text('secret')
        home = private_dirs.ensure_private_dir(root / 'native/codex-home')
        (home / 'auth.json').symlink_to(outside)
        (root / 'agent-credential.json').write_text('secret')
        (root / 'execution-start.json').write_text('private startup claim and nonce')
        (root / 'diagnostic.json').write_text('retained')
        fallback = Path(tempfile.gettempdir()).resolve() / 'hey-my-buddy-inquiry' / 'fixture-attempt'
        fallback.mkdir(parents=True, exist_ok=True)
        (fallback / 'inquiry.sock').write_text('socket')
        (root / 'inquiry.json').write_text(json.dumps({'socketPath': str(fallback / 'inquiry.sock'), 'token': 'secret'}))
        result = private_dirs.cleanup_attempt_credentials(state, 'dsh', 'microTask', 'fixture-attempt')
        self.assertGreaterEqual(result['count'], 4)
        self.assertFalse(fallback.exists())
        self.assertFalse((home / 'auth.json').is_symlink())
        self.assertFalse((root / 'execution-start.json').exists())
        self.assertEqual(outside.read_text(), 'secret')
        self.assertEqual((root / 'diagnostic.json').read_text(), 'retained')
        unrelated = state / 'unrelated' / 'fixture-attempt'
        unrelated.mkdir(parents=True)
        (unrelated / 'inquiry.sock').write_text('outside')
        (root / 'inquiry.json').write_text(json.dumps({'socketPath':str(unrelated / 'inquiry.sock')}))
        private_dirs.cleanup_attempt_credentials(state, 'dsh', 'microTask', 'fixture-attempt')
        self.assertEqual((unrelated / 'inquiry.sock').read_text(), 'outside')
