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
    def test_concurrent_private_directory_creator_is_accepted_and_checked(self):
        if os.name == 'nt':
            self.skipTest('POSIX mkdir race; Windows inherits the parent ACL')
        raced = self.directory / 'concurrent-parent'
        target = raced / 'leaf'
        original = Path.mkdir
        def create_concurrently(path, mode=0o777, parents=False, exist_ok=False):
            if path == raced:
                self.assertFalse(path.exists())
                self.assertEqual(mode, 0o700)
                original(path, mode=0o700)
            return original(path, mode=mode, parents=parents, exist_ok=exist_ok)
        with mock.patch.object(Path, 'mkdir', create_concurrently):
            self.assertEqual(private_dirs.ensure_private_dir(target), target)
        self.assertTrue(target.is_dir())
        self.assertEqual(stat.S_IMODE(raced.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o700)

    def test_concurrent_creator_cannot_replace_a_private_directory_with_a_file(self):
        if os.name == 'nt':
            self.skipTest('POSIX mkdir race; Windows uses ordinary mkdir')
        target = self.directory / 'concurrent-file'
        original = Path.mkdir
        def create_file(path, mode=0o777, parents=False, exist_ok=False):
            if path == target:
                path.write_bytes(b'not a directory')
            return original(path, mode=mode, parents=parents, exist_ok=exist_ok)
        with mock.patch.object(Path, 'mkdir', create_file), self.assertRaises(BoardError):
            private_dirs.ensure_private_dir(target)

    def test_session_paths_follow_group_identity_and_reject_escaping_components(self):
        self.assertEqual(private_dirs.session_record_path(self.directory, 'macro', 'attempt'),
                         self.directory / 'sessions/macro-tasks/macro/attempt.jsonl')
        self.assertEqual(private_dirs.session_record_path(self.directory, 'run:micro', 'attempt'),
                         self.directory / 'sessions/micro-tasks/micro/attempt.jsonl')
        for group, attempt in (('../escape', 'attempt'), ('run:../escape', 'attempt'),
                               ('macro', '../attempt'), ('run:', 'attempt')):
            with self.subTest(group=group, attempt=attempt), self.assertRaises(BoardError):
                private_dirs.session_record_path(self.directory, group, attempt)

    def test_remove_tree_deletes_links_even_when_the_exact_link_is_kept(self):
        outside = private_dirs.ensure_private_dir(self.directory / 'outside-kept-links')
        sentinel = outside / 'sentinel'
        sentinel.write_text('retained outside target', encoding="utf-8", newline="\n")
        targets = (('file', sentinel, False), ('directory', outside, True),
                   ('dangling', outside / 'missing', False))
        for kind, target, directory_link in targets:
            with self.subTest(kind=kind):
                root = private_dirs.ensure_private_dir(self.directory / ('private-link-' + kind))
                link = root / 'evidence.json'
                link.symlink_to(target, target_is_directory=directory_link)
                self.assertTrue(private_dirs.remove_tree(root, keep=frozenset({link})))
                self.assertFalse(root.exists())
                self.assertFalse(link.is_symlink())
                self.assertEqual(sentinel.read_text(encoding="utf-8"), 'retained outside target')

    def test_remove_tree_keeps_the_exact_file_without_retaining_same_named_directories(self):
        root = private_dirs.ensure_private_dir(self.directory / 'private-records')
        recorded = private_dirs.ensure_private_dir(root / 'attempts' / 'recorded')
        session = recorded / 'evidence.json'
        session.write_text('retained evidence\n', encoding="utf-8", newline="\n")
        (recorded / 'settings.json').write_text('remove configuration', encoding="utf-8", newline="\n")
        same_name = private_dirs.ensure_private_dir(root / 'attempts' / 'directory' / 'evidence.json')
        (same_name / 'unrelated.txt').write_text('a directory is not an evidence file', encoding="utf-8", newline="\n")
        self.assertFalse(private_dirs.remove_tree(root, keep=frozenset({session})))
        self.assertEqual(session.read_text(encoding="utf-8"), 'retained evidence\n')
        self.assertEqual(list(recorded.iterdir()), [session])
        self.assertEqual(list((root / 'attempts').iterdir()), [recorded])
        self.assertFalse(same_name.exists())

    def test_remove_tree_retains_exact_evidence_subtree_and_existing_link_guards(self):
        root = private_dirs.ensure_private_dir(self.directory / 'private')
        failed = private_dirs.ensure_private_dir(root / 'attempts' / 'failed')
        (failed / 'unknown-events.json').write_text('failure evidence', encoding="utf-8", newline="\n")
        native = private_dirs.ensure_private_dir(root / 'native')
        (native / 'session.json').write_text('native state', encoding="utf-8", newline="\n")
        successful = private_dirs.ensure_private_dir(root / 'attempts' / 'completed')
        (successful / 'result.json').write_text('success evidence', encoding="utf-8", newline="\n")
        outside = private_dirs.ensure_private_dir(self.directory / 'outside')
        (outside / 'sentinel').write_text('safe', encoding="utf-8", newline="\n")
        (root / 'linked').symlink_to(outside, target_is_directory=True)
        self.assertFalse(private_dirs.remove_tree(root, keep=frozenset({failed})))
        self.assertEqual((failed / 'unknown-events.json').read_text(encoding="utf-8"), 'failure evidence')
        self.assertEqual(list(root.iterdir()), [root / 'attempts'])
        self.assertEqual(list((root / 'attempts').iterdir()), [failed])
        self.assertEqual((outside / 'sentinel').read_text(encoding="utf-8"), 'safe')
        alias = self.directory / 'alias'
        alias.symlink_to(root, target_is_directory=True)
        with self.assertRaises(BoardError):
            private_dirs.remove_tree(alias, keep=frozenset({alias / 'attempts' / 'failed'}))
        self.assertTrue(failed.exists())

    def test_execution_start_cleanup_matches_exact_private_filename(self):
        state = self.directory.resolve()
        root = private_dirs.ensure_private_dir(private_dirs.attempt_root(state, 'zcode', 'microTask', 'attempt'))
        (root / 'execution-start.json').write_text('private', encoding="utf-8", newline="\n")
        (root / 'execution-live.json').write_text('private endpoint token', encoding="utf-8", newline="\n")
        (root / 'execution-started.json').write_text('retain unknown filename', encoding="utf-8", newline="\n")
        removed = private_dirs.cleanup_attempt_credentials(state, 'zcode', 'microTask', 'attempt')
        self.assertEqual(removed['count'], 1)
        self.assertFalse((root / 'execution-start.json').exists())
        self.assertTrue((root / 'execution-live.json').exists())
        self.assertEqual((root / 'execution-started.json').read_text(encoding="utf-8"), 'retain unknown filename')

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
        sentinel.write_text('safe', encoding="utf-8", newline="\n")
        target = self.directory.resolve() / 'private'
        target.mkdir()
        (target / 'linked').symlink_to(outside, target_is_directory=True)
        (target / 'regular').write_text('remove', encoding="utf-8", newline="\n")
        self.assertTrue(private_dirs.remove_tree(target))
        self.assertEqual(sentinel.read_text(encoding="utf-8"), 'safe')

    def test_parent_guard_stops_before_inspecting_an_entry_under_a_link(self):
        outside = self.directory.resolve() / 'external-parent'
        outside.mkdir()
        (outside / 'secret').write_text('retained', encoding="utf-8", newline="\n")
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
        if os.name == 'nt':
            from hey_my_buddy.windows_files import WindowsFileAPI, CREATE_NEW
            windows_open = WindowsFileAPI._open
            def raced_windows_open(api, path, access, disposition, **kwargs):
                if Path(path) == temporary and disposition == CREATE_NEW:
                    temporary.symlink_to(sentinel)
                return windows_open(api, path, access, disposition, **kwargs)
            patcher = mock.patch.object(WindowsFileAPI, '_open', raced_windows_open)
        else:
            patcher = mock.patch('hey_my_buddy.private_dirs.os.open', side_effect=raced_open)
        with patcher:
            with self.assertRaises(FileExistsError):
                private_dirs.open_regular_fd(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
        self.assertTrue(temporary.is_symlink())
        self.assertEqual(sentinel.read_bytes(), b'retained')

    def test_cleanup_removes_credentials_and_exact_fallback_socket_only(self):
        state = self.directory.resolve()
        root = private_dirs.ensure_private_dir(private_dirs.attempt_root(state, 'dsh', 'microTask', 'fixture-attempt'))
        outside = state / 'outside-auth'
        outside.write_text('secret', encoding="utf-8", newline="\n")
        home = private_dirs.ensure_private_dir(root / 'native/codex-home')
        (home / 'auth.json').symlink_to(outside)
        (root / 'agent-credential.json').write_text('secret', encoding="utf-8", newline="\n")
        (root / 'execution-start.json').write_text('private startup claim and nonce', encoding="utf-8", newline="\n")
        (root / 'diagnostic.json').write_text('retained', encoding="utf-8", newline="\n")
        fallback = Path(tempfile.gettempdir()).resolve() / 'hey-my-buddy-inquiry' / 'fixture-attempt'
        fallback.mkdir(parents=True, exist_ok=True)
        (fallback / 'inquiry.sock').write_text('socket', encoding="utf-8", newline="\n")
        (root / 'inquiry.json').write_text(json.dumps({'socketPath': str(fallback / 'inquiry.sock'), 'token': 'secret'}), encoding="utf-8", newline="\n")
        result = private_dirs.cleanup_attempt_credentials(state, 'dsh', 'microTask', 'fixture-attempt')
        self.assertGreaterEqual(result['count'], 4)
        self.assertFalse(fallback.exists())
        self.assertFalse((home / 'auth.json').is_symlink())
        self.assertFalse((root / 'execution-start.json').exists())
        self.assertEqual(outside.read_text(encoding="utf-8"), 'secret')
        self.assertEqual((root / 'diagnostic.json').read_text(encoding="utf-8"), 'retained')
        unrelated = state / 'unrelated' / 'fixture-attempt'
        unrelated.mkdir(parents=True)
        (unrelated / 'inquiry.sock').write_text('outside', encoding="utf-8", newline="\n")
        (root / 'inquiry.json').write_text(json.dumps({'socketPath':str(unrelated / 'inquiry.sock')}), encoding="utf-8", newline="\n")
        private_dirs.cleanup_attempt_credentials(state, 'dsh', 'microTask', 'fixture-attempt')
        self.assertEqual((unrelated / 'inquiry.sock').read_text(encoding="utf-8"), 'outside')
