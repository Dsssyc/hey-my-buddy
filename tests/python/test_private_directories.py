"""Private directory boundaries and stopped legacy relocation."""
import hashlib
import json
import os
from pathlib import Path
import stat
import tempfile
from types import SimpleNamespace
from unittest import mock

from buddy import backup, private_dirs, private_migration, upgrade
from buddy.errors import BoardError
from support import BoardTestCase


class PrivateDirectoryTests(BoardTestCase):
    def test_explicit_state_and_reparse_guard(self):
        context = SimpleNamespace(environment={}, spec={'adapter': 'codex'}, task_id='goal', attempt_id='attempt')
        with self.assertRaises(BoardError) as caught:
            private_dirs.context_root(context)
        self.assertEqual(caught.exception.code, 'PRIVATE_STATE_REQUIRED')
        context.environment['BUDDY_STATE_DIR'] = str(self.directory.resolve())
        root = private_dirs.context_root(context)
        self.assertEqual(root, private_dirs.attempt_root(self.directory, 'codex', 'goal', 'attempt'))
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
        with mock.patch('buddy.private_dirs.os.open', side_effect=raced_open):
            with self.assertRaises(FileExistsError):
                private_dirs.open_regular_fd(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
        self.assertTrue(temporary.is_symlink())
        self.assertEqual(sentinel.read_bytes(), b'retained')

    def test_cleanup_removes_credentials_and_exact_fallback_socket_only(self):
        state = self.directory.resolve()
        root = private_dirs.ensure_private_dir(private_dirs.attempt_root(state, 'dsh', 'goal', 'fixture-attempt'))
        outside = state / 'outside-auth'
        outside.write_text('secret')
        home = private_dirs.ensure_private_dir(root / 'native/codex-home')
        (home / 'auth.json').symlink_to(outside)
        (root / 'agent-credential.json').write_text('secret')
        (root / 'diagnostic.json').write_text('retained')
        fallback = Path(tempfile.gettempdir()).resolve() / 'hey-my-buddy-inquiry' / 'fixture-attempt'
        fallback.mkdir(parents=True, exist_ok=True)
        (fallback / 'inquiry.sock').write_text('socket')
        (root / 'inquiry.json').write_text(json.dumps({'socketPath': str(fallback / 'inquiry.sock'), 'token': 'secret'}))
        result = private_dirs.cleanup_attempt_credentials(state, 'dsh', 'goal', 'fixture-attempt')
        self.assertGreaterEqual(result['count'], 4)
        self.assertFalse(fallback.exists())
        self.assertFalse((home / 'auth.json').is_symlink())
        self.assertEqual(outside.read_text(), 'secret')
        self.assertEqual((root / 'diagnostic.json').read_text(), 'retained')
        unrelated = state / 'unrelated' / 'fixture-attempt'
        unrelated.mkdir(parents=True)
        (unrelated / 'inquiry.sock').write_text('outside')
        (root / 'inquiry.json').write_text(json.dumps({'socketPath':str(unrelated / 'inquiry.sock')}))
        private_dirs.cleanup_attempt_credentials(state, 'dsh', 'goal', 'fixture-attempt')
        self.assertEqual((unrelated / 'inquiry.sock').read_text(), 'outside')


class PrivateMigrationTests(BoardTestCase):
    def _stopped_attempt(self):
        board = self.board()
        task = board.call('task_submit', {'requestId':'migration-fixture', 'task':'private migration',
            'cwd':str(self.workdir()), 'adapter':'command', 'argv':['/bin/true']})['task']
        task_id = task['runId']
        attempt_id = 'attempt-fixture'
        with board.store.db.write() as connection:
            connection.execute("UPDATE tasks SET state='completed',selected_attempt_id=? WHERE task_id=?",
                               (attempt_id, task_id))
            connection.execute("INSERT INTO attempts(attempt_id,task_id,generation,nonce_verifier,claim_request_id,"
                               "execution_state,adapter,shutdown_confirmed,created_at,updated_at)"
                               " VALUES(?,?,1,'fixture','fixture','finished','command',1,'then','then')",
                               (attempt_id, task_id))
        return board, task_id, attempt_id

    def test_stopped_legacy_private_content_moves_and_rolls_back_without_evidence_loss(self):
        board, task_id, attempt_id = self._stopped_attempt()
        state = board.directory.resolve()
        old = state / 'attempts' / task_id / attempt_id
        old.mkdir(parents=True, exist_ok=True)
        (old / 'agent-credential.json').write_text('{"token":"secret"}')
        fallback = Path(tempfile.gettempdir()).resolve() / 'hey-my-buddy-inquiry' / attempt_id
        fallback.mkdir(parents=True, exist_ok=True)
        (fallback / 'inquiry.sock').write_text('socket')
        (old / 'inquiry.json').write_text(json.dumps({'socketPath': str(fallback / 'inquiry.sock'), 'token': 'secret'}))
        (old / 'finish-bridge.json').write_text('{"key":"secret"}')
        (old / 'zcode-control.json').write_text('{"inquiry":{"token":"secret"}}')
        (old / 'personal-provider.json').write_text('{"apiKey":"secret"}')
        claude = old / 'claude-private'
        claude.mkdir()
        (claude / 'settings.json').write_text('{"private":"settings"}')
        sessions = old / 'sessions'
        sessions.mkdir()
        (sessions / 'rollout.jsonl').write_text('session')
        old_dsh = old / 'deepseek-delegate-run-fixture'
        old_dsh.mkdir()
        (old_dsh / 'stdout.log').write_text('retained log')
        (old_dsh / 'session-temp').mkdir()
        (old_dsh / 'session-temp' / 'native.json').write_text('private')
        native = old / 'native/codex-home'
        native.mkdir(parents=True)
        outside = self.directory.resolve() / 'auth'
        outside.write_text('account')
        (native / 'auth.json').symlink_to(outside)
        invocation = old / ('no-tool-' + 'a' * 32)
        modules = invocation / 'dsh-home/profiles/headless/node_modules'
        modules.mkdir(parents=True)
        (modules / 'link').symlink_to(outside)
        call = invocation / 'call-1'
        call.mkdir()
        (call / 'request.json').write_text('{"prompt":"evidence"}')
        (call / 'patch.json').write_text('{"credentialPath":"private"}')
        zcode_call = old / ('no-tool-' + 'b' * 32)
        (zcode_call / 'native/storage').mkdir(parents=True)
        (zcode_call / 'native/sessions.sqlite').write_text('zcode session')
        (zcode_call / 'personal-provider.json').write_text('{"apiKey":"secret"}')
        codex_call = old / ('no-tool-' + 'c' * 32)
        codex_call_home = codex_call / 'native/codex-home'
        codex_call_home.mkdir(parents=True)
        (codex_call_home / 'auth.json').symlink_to(outside)
        legacy_goal = state / 'harnesses/codex' / hashlib.sha256(task_id.encode()).hexdigest()
        legacy_goal.mkdir(parents=True)
        (legacy_goal / 'binding.json').write_text('{"session":"bound"}')
        legacy_zcode_goal = state / 'harnesses/zcode' / hashlib.sha256(task_id.encode()).hexdigest()
        legacy_zcode_goal.mkdir(parents=True)
        (legacy_zcode_goal / 'sessions.sqlite').write_text('zcode goal session')

        report = backup.preflight(state)
        self.assertGreater(report['skipped']['count'], 0)
        readiness = private_migration.require_readiness(state, report)
        journal = {'privateMigration': {**readiness['plan'], 'applied': False}}
        try:
            summary = private_migration.apply(state, journal, lambda _value: None)
            private_migration.apply(state, journal, lambda _value: None)
        except BoardError as error:
            self.fail(f'{error.code}: {error.details}')
        self.assertFalse((old / 'agent-credential.json').exists())
        self.assertFalse(fallback.exists())
        for name in ('finish-bridge.json', 'zcode-control.json', 'personal-provider.json'):
            self.assertFalse((old / name).exists())
        self.assertGreaterEqual(summary['movedCount'], 3)
        self.assertGreaterEqual(summary['deletedCount'], 2)
        self.assertGreaterEqual(summary['credentialCleanupCount'], 1)
        self.assertLessEqual(len(summary['paths']), 20)
        self.assertEqual((call / 'request.json').read_text(), '{"prompt":"evidence"}')
        self.assertFalse((call / 'patch.json').exists())
        self.assertTrue((private_dirs.attempt_root(state, 'dsh', task_id, attempt_id) /
                         invocation.name / 'dsh-home/profiles/headless/node_modules/link').is_symlink())
        self.assertFalse((private_dirs.attempt_root(state, 'codex', task_id, attempt_id) /
                          'native/codex-home/auth.json').exists())
        self.assertTrue((private_dirs.native_root(state, 'codex', task_id) / 'binding.json').exists())
        self.assertTrue((private_dirs.native_root(state, 'zcode', task_id) / 'sessions.sqlite').exists())
        self.assertEqual((old_dsh / 'stdout.log').read_text(), 'retained log')
        self.assertFalse((old_dsh / 'session-temp').exists())
        self.assertTrue((private_dirs.attempt_root(state, 'dsh', task_id, attempt_id) /
                         old_dsh.name / 'session-temp/native.json').exists())
        self.assertFalse((codex_call_home / 'auth.json').is_symlink())
        self.assertFalse((zcode_call / 'personal-provider.json').exists())
        self.assertEqual(backup.preflight(state)['skipped']['count'], 0)
        private_migration.rollback(state, journal, lambda _value: None)
        self.assertTrue((legacy_goal / 'binding.json').exists())
        self.assertTrue((legacy_zcode_goal / 'sessions.sqlite').exists())
        self.assertTrue((modules / 'link').is_symlink())
        self.assertTrue((sessions / 'rollout.jsonl').exists())
        self.assertTrue((old_dsh / 'session-temp/native.json').exists())
        self.assertEqual((call / 'request.json').read_text(), '{"prompt":"evidence"}')
        self.assertEqual(outside.read_text(), 'account')

    def test_full_legacy_layout_including_native_logs_reports_installer_consistency(self):
        board, task_id, attempt_id = self._stopped_attempt()
        state = board.directory.resolve()
        with board.store.db.write() as connection:
            connection.execute("UPDATE tasks SET adapter='zcode' WHERE task_id=?", (task_id,))
        old = state / 'attempts' / task_id / attempt_id
        old.mkdir(parents=True, exist_ok=True)
        (old / 'task.txt').write_text('retained input')
        for name in ('agent-credential.json', 'inquiry.json', 'finish-bridge.json', 'zcode-control.json',
                     'builtin-provider.json', 'personal-provider.json'):
            (old / name).write_text('{"secret":"credential"}')
        (old / 'native-logs').mkdir()
        (old / 'native-logs/zcode-2026-09-30.log').write_text('native zcode log')
        (old / 'native-logs/deeper').mkdir()
        (old / 'native-logs/deeper/turn.log').write_text('nested native log')
        (old / 'claude-private').mkdir()
        (old / 'claude-private/settings.json').write_text('{"private":"settings"}')
        (old / 'sessions').mkdir()
        (old / 'sessions/rollout.jsonl').write_text('session')
        old_dsh = old / 'deepseek-delegate-run-fixture'
        old_dsh.mkdir()
        (old_dsh / 'stdout.log').write_text('retained delegate log')
        (old_dsh / 'session-temp').mkdir()
        (old_dsh / 'session-temp/native.json').write_text('private')
        native = old / 'native/codex-home'
        native.mkdir(parents=True)
        outside = self.directory.resolve() / 'auth'
        outside.write_text('account')
        (native / 'auth.json').symlink_to(outside)
        invocation = old / ('no-tool-' + 'a' * 32)
        modules = invocation / 'dsh-home/profiles/headless/node_modules'
        modules.mkdir(parents=True)
        (modules / 'link').symlink_to(outside)
        call = invocation / 'call-1'
        call.mkdir()
        (call / 'request.json').write_text('{"prompt":"evidence"}')
        (call / 'patch.json').write_text('{"credentialPath":"private"}')
        zcode_call = old / ('no-tool-' + 'b' * 32)
        (zcode_call / 'native/storage').mkdir(parents=True)
        (zcode_call / 'native/sessions.sqlite').write_text('zcode session')

        report = backup.preflight(state)
        self.assertTrue(report['ok'], report['legacyPlan'])
        self.assertTrue(report['needsAttention'])
        self.assertGreater(report['skipped']['count'], 0)
        self.assertEqual(report['legacyPlan']['ready'], True)
        self.assertEqual(report['legacyPlan']['uncovered'], {'count': 0, 'paths': [], 'entries': []})
        self.assertEqual(report['legacyPlan']['blocked'], {'count': 0, 'paths': [], 'entries': []})
        readiness = private_migration.require_readiness(state, report)
        self.assertEqual(readiness['ready'], True)
        zcode_root = private_dirs.attempt_root(state, 'zcode', task_id, attempt_id)
        self.assertIn({'source': str(old / 'native-logs'), 'target': str(zcode_root / 'native-logs'),
                       'taskId': task_id, 'adapter': 'zcode', 'attemptId': attempt_id},
                      readiness['plan']['moves'])

        journal = {'privateMigration': {**readiness['plan'], 'applied': False}}
        try:
            private_migration.apply(state, journal, lambda _value: None)
            private_migration.apply(state, journal, lambda _value: None)
        except BoardError as error:
            self.fail(f'{error.code}: {error.details}')
        self.assertFalse((old / 'native-logs').exists())
        self.assertEqual((zcode_root / 'native-logs/zcode-2026-09-30.log').read_text(), 'native zcode log')
        self.assertEqual((zcode_root / 'native-logs/deeper/turn.log').read_text(), 'nested native log')
        for name in ('agent-credential.json', 'inquiry.json', 'finish-bridge.json', 'zcode-control.json',
                     'builtin-provider.json', 'personal-provider.json'):
            self.assertFalse((old / name).exists(), name)
        self.assertEqual((old / 'task.txt').read_text(), 'retained input')
        self.assertEqual((call / 'request.json').read_text(), '{"prompt":"evidence"}')
        self.assertEqual((old_dsh / 'stdout.log').read_text(), 'retained delegate log')
        after = backup.preflight(state)
        self.assertEqual(after['skipped']['count'], 0)
        self.assertTrue(after['ok'], after['legacyPlan'])
        private_migration.rollback(state, journal, lambda _value: None)
        self.assertEqual((old / 'native-logs/zcode-2026-09-30.log').read_text(), 'native zcode log')

    def test_uncovered_category_fails_preflight_like_the_installer(self):
        board, task_id, attempt_id = self._stopped_attempt()
        state = board.directory.resolve()
        old = state / 'attempts' / task_id / attempt_id
        old.mkdir(parents=True, exist_ok=True)
        (old / 'task.txt').write_text('retained input')
        (old / 'mystery-private').mkdir()
        (old / 'mystery-private/native.json').write_text('unknown category')
        report = backup.preflight(state)
        self.assertFalse(report['ok'])
        self.assertEqual(report['legacyPlan']['ready'], False)
        self.assertEqual(report['legacyPlan']['uncovered']['count'], 1)
        self.assertEqual(report['legacyPlan']['uncovered']['entries'],
                         [{'path': f'attempts/{task_id}/{attempt_id}/mystery-private',
                           'reason': 'not-in-relocation-plan'}])
        self.assertEqual(report['legacyPlan']['blocked']['count'], 0)
        with self.assertRaises(BoardError) as caught:
            private_migration.require_readiness(state, report)
        self.assertEqual(caught.exception.code, 'BACKUP_PREFLIGHT_FAILED')
        self.assertIn(f'attempts/{task_id}/{attempt_id}/mystery-private', caught.exception.details['paths'])
        self.assertEqual((old / 'mystery-private/native.json').read_text(), 'unknown category')

    def test_unconfirmed_native_logs_stay_in_place_and_block_preflight_like_the_installer(self):
        board, task_id, attempt_id = self._stopped_attempt()
        state = board.directory.resolve()
        old = state / 'attempts' / task_id / attempt_id
        old.mkdir(parents=True, exist_ok=True)
        (old / 'native-logs').mkdir()
        (old / 'native-logs/zcode.log').write_text('native log')
        with board.store.db.write() as connection:
            connection.execute("UPDATE attempts SET shutdown_confirmed=0,execution_state='uncertain' WHERE attempt_id=?",
                               (attempt_id,))
        report = backup.preflight(state)
        self.assertFalse(report['ok'])
        self.assertEqual(report['legacyPlan']['ready'], False)
        self.assertEqual(report['legacyPlan']['blocked']['entries'],
                         [{'path': f'attempts/{task_id}/{attempt_id}/native-logs',
                           'reason': 'shutdown-unconfirmed'}])
        self.assertEqual(report['legacyPlan']['uncovered']['count'], 0)
        with self.assertRaises(BoardError) as caught:
            upgrade.layout_readiness(state)
        self.assertEqual(caught.exception.code, 'BACKUP_PREFLIGHT_FAILED')
        self.assertEqual((old / 'native-logs/zcode.log').read_text(), 'native log')

    def test_interrupted_move_resumes_and_tampered_journal_cannot_escape_state(self):
        board, task_id, attempt_id = self._stopped_attempt()
        state = board.directory.resolve()
        old = state / 'attempts' / task_id / attempt_id
        old.mkdir(parents=True, exist_ok=True)
        native = old / 'native/codex-home'
        native.mkdir(parents=True)
        (native / 'cache.json').write_text('session')
        report = backup.preflight(state)
        journal = {'privateMigration': {**private_migration.require_readiness(state, report)['plan'], 'applied': False}}
        move = journal['privateMigration']['moves'][0]
        target = Path(move['target'])
        private_dirs.ensure_private_dir(target.parent)
        os.rename(move['source'], target)  # Crash after rename, before journal update.
        result = private_migration.apply(state, journal, lambda _value: None)
        self.assertEqual(result['movedCount'], 1)
        self.assertEqual((target / 'codex-home/cache.json').read_text(), 'session')
        outside = self.directory.resolve() / 'external'
        outside.mkdir()
        sentinel = outside / 'keep'
        sentinel.write_text('safe')
        move['target'] = str(outside)
        with self.assertRaises(BoardError) as caught:
            private_migration.rollback(state, journal, lambda _value: None)
        self.assertEqual(caught.exception.code, 'UPGRADE_RECOVERY_REQUIRED')
        self.assertEqual(sentinel.read_text(), 'safe')
        move['target'] = str(target)
        private_migration.rollback(state, journal, lambda _value: None)
        self.assertEqual((native / 'cache.json').read_text(), 'session')

    def test_linked_legacy_parent_is_refused_before_preview_walk(self):
        board, task_id, attempt_id = self._stopped_attempt()
        state = board.directory.resolve()
        outside = self.directory.resolve() / 'outside-tree'
        (outside / attempt_id).mkdir(parents=True)
        sentinel = outside / attempt_id / 'native'
        sentinel.mkdir()
        (state / 'attempts').mkdir(exist_ok=True)
        (state / 'attempts' / task_id).symlink_to(outside, target_is_directory=True)
        with self.assertRaises(BoardError) as caught:
            private_migration.preview(state)
        self.assertEqual(caught.exception.code, 'UPGRADE_UNSAFE')
        self.assertTrue(sentinel.is_dir())

    def test_unknown_stop_blocks_layout_readiness(self):
        board, task_id, attempt_id = self._stopped_attempt()
        state = board.directory.resolve()
        old = state / 'attempts' / task_id / attempt_id
        old.mkdir(parents=True, exist_ok=True)
        (old / 'agent-credential.json').write_text('secret')
        with board.store.db.write() as connection:
            connection.execute("UPDATE attempts SET shutdown_confirmed=0,execution_state='uncertain' WHERE attempt_id=?",
                               (attempt_id,))
        with self.assertRaises(BoardError) as caught:
            upgrade.layout_readiness(state)
        self.assertEqual(caught.exception.code, 'BACKUP_PREFLIGHT_FAILED')
        self.assertTrue((old / 'agent-credential.json').exists())

    def test_preflight_race_to_link_is_rejected_by_the_second_scan(self):
        board, task_id, attempt_id = self._stopped_attempt()
        state = board.directory.resolve()
        old = state / 'attempts' / task_id / attempt_id
        old.mkdir(parents=True, exist_ok=True)
        evidence = old / 'task.txt'
        evidence.write_text('retained input')
        first = backup.preflight(state)
        self.assertEqual(first['rejected']['count'], 0)
        evidence.unlink()
        outside = self.directory.resolve() / 'outside-evidence'
        outside.write_text('private')
        evidence.symlink_to(outside)
        with self.assertRaises(BoardError) as caught:
            private_migration.require_readiness(state, first)
        self.assertEqual(caught.exception.code, 'BACKUP_PREFLIGHT_FAILED')
        self.assertEqual(outside.read_text(), 'private')
