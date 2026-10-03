"""Private upgrade refusal, admission fence and backup restoration."""
import json
import os
from pathlib import Path
from unittest import mock
from hey_my_buddy.blackboard.store import backup
from hey_my_buddy import private_dirs
from hey_my_buddy.install import upgrade
from hey_my_buddy.errors import BoardError
from support import BoardTestCase

class UpgradeTests(BoardTestCase):
    def stopped_attempt(self, board):
        task_id = board.call('task_submit', {'requestId':'upgrade-legacy', 'task':'stopped legacy attempt',
            'cwd':str(self.workdir()), 'adapter':'command', 'argv':['/bin/true']})['task']['runId']
        with board.store.db.write() as connection:
            connection.execute("UPDATE tasks SET state='completed',selected_attempt_id='attempt' WHERE task_id=?", (task_id,))
            connection.execute("INSERT INTO attempts(attempt_id,task_id,generation,nonce_verifier,claim_request_id,"
                               "execution_state,adapter,shutdown_confirmed,created_at,updated_at)"
                               " VALUES('attempt',?,1,'fixture','fixture','finished','command',1,'then','then')",
                               (task_id,))
        return task_id

    def test_upgrade_backs_up_board_with_historical_codex_auth_link(self):
        board = self.board()
        state = board.directory
        task_id = self.stopped_attempt(board)
        root = self.directory / 'runtimes'
        previous, target = root / ('a' * 32), root / ('b' * 32)
        previous.mkdir(parents=True)
        target.mkdir()
        endpoint = {'runtimeIdentity': 'runtime:' + previous.name, 'serviceId': 'old',
                    'pid': 123, 'contractVersion': '0.19.0'}
        (state / 'control.json').write_text(json.dumps(endpoint))
        home = state / 'attempts' / task_id / 'attempt/native/codex-home'
        home.mkdir(parents=True)
        secret = self.directory / 'private-auth.json'
        secret.write_text('private credential')
        (home / 'auth.json').symlink_to(secret)
        (home / 'cache.sqlite').write_bytes(b'native cache')
        health = {**endpoint, 'maxConcurrent': 1, 'waitCapacity': 32}
        with mock.patch.dict(os.environ, {'BUDDY_RUNTIME_ROOT': str(root)}), \
             mock.patch('hey_my_buddy.install.upgrade.get_state_dir', return_value=state), \
             mock.patch('hey_my_buddy.install.upgrade.runtime.is_ready', return_value=True), \
             mock.patch('hey_my_buddy.install.upgrade.runtime.read_ready', return_value={'sourceCommit': 'fixture'}), \
             mock.patch('hey_my_buddy.install.upgrade.runtime.materialize', return_value={'runtimeDir': str(target)}), \
             mock.patch('hey_my_buddy.install.upgrade.probe', return_value=health), \
             mock.patch('hey_my_buddy.install.upgrade.detach'), \
             mock.patch('hey_my_buddy.install.upgrade.start', return_value={'runtimeContentId': target.name}), \
             mock.patch('hey_my_buddy.install.upgrade.verify_started', return_value={'retainedDataFingerprints': True}), \
             mock.patch('hey_my_buddy.blackboard.tasks.storage.prune_old_runtimes', return_value={'complete': True}):
            result = upgrade.upgrade({})
        self.assertTrue(result['upgraded'], result)
        manifest = backup.verify(Path(result['backup']['path']))
        self.assertFalse(any('codex-home' in name for name in manifest['files']))
        self.assertFalse((home / 'auth.json').is_symlink())
        self.assertTrue((private_dirs.attempt_root(state, 'codex', task_id, 'attempt') / 'native/codex-home/cache.sqlite').exists())
        self.assertEqual(secret.read_text(), 'private credential')

    def test_upgrade_succeeds_with_no_tool_private_links_in_attempts(self):
        board = self.board()
        state = board.directory
        task_id = self.stopped_attempt(board)
        root = self.directory / 'runtimes'
        previous, target = root / ('a' * 32), root / ('b' * 32)
        previous.mkdir(parents=True)
        target.mkdir()
        endpoint = {'runtimeIdentity': 'runtime:' + previous.name, 'serviceId': 'old',
                    'pid': 123, 'contractVersion': '0.19.0'}
        (state / 'control.json').write_text(json.dumps(endpoint))
        invocation = state / ('attempts/' + task_id + '/attempt/no-tool-' + 'a' * 32)
        dsh_modules = invocation / 'dsh-home/profiles/headless/node_modules'
        dsh_modules.mkdir(parents=True)
        (dsh_modules / '.bin').symlink_to(self.directory)
        codex_home = invocation / 'native/codex-home'
        codex_home.mkdir(parents=True)
        secret = self.directory / 'private-auth.json'
        secret.write_text('private credential')
        (codex_home / 'auth.json').symlink_to(secret)
        (invocation / 'call-1').mkdir(parents=True)
        (invocation / 'call-1/request.json').write_text('{"prompt":"call evidence"}')
        health = {**endpoint, 'maxConcurrent': 1, 'waitCapacity': 32}
        with mock.patch.dict(os.environ, {'BUDDY_RUNTIME_ROOT': str(root)}), \
             mock.patch('hey_my_buddy.install.upgrade.get_state_dir', return_value=state), \
             mock.patch('hey_my_buddy.install.upgrade.runtime.is_ready', return_value=True), \
             mock.patch('hey_my_buddy.install.upgrade.runtime.read_ready', return_value={'sourceCommit': 'fixture'}), \
             mock.patch('hey_my_buddy.install.upgrade.runtime.materialize', return_value={'runtimeDir': str(target)}), \
             mock.patch('hey_my_buddy.install.upgrade.probe', return_value=health), \
             mock.patch('hey_my_buddy.install.upgrade.detach'), \
             mock.patch('hey_my_buddy.install.upgrade.start', return_value={'runtimeContentId': target.name}), \
             mock.patch('hey_my_buddy.install.upgrade.verify_started', return_value={'retainedDataFingerprints': True}), \
             mock.patch('hey_my_buddy.blackboard.tasks.storage.prune_old_runtimes', return_value={'complete': True}):
            result = upgrade.upgrade({})
        self.assertTrue(result['upgraded'], result)
        manifest = backup.verify(Path(result['backup']['path']))
        entries = set(manifest['files'])
        prefix = 'state/attempts/' + task_id + '/attempt/no-tool-' + 'a' * 32 + '/'
        self.assertIn(prefix + 'call-1/request.json', entries)
        self.assertFalse(any(name.startswith(prefix + 'dsh-home/') for name in entries))
        self.assertFalse(any(name.startswith(prefix + 'native/') for name in entries))
        self.assertGreaterEqual(manifest['skippedAttemptEntries']['count'], 2)
        leaked = [name for name in entries if (Path(result['backup']['path']) / name).read_bytes().find(b'private credential') >= 0]
        self.assertEqual(leaked, [])
        self.assertFalse((dsh_modules / '.bin').is_symlink())
        self.assertTrue((private_dirs.attempt_root(state, 'dsh', task_id, 'attempt') /
                         invocation.name / 'dsh-home/profiles/headless/node_modules/.bin').is_symlink())
        self.assertFalse((codex_home / 'auth.json').is_symlink())
        self.assertEqual(secret.read_text(), 'private credential')

    def test_failed_runtime_switch_restores_legacy_session_layout(self):
        board = self.board()
        state = board.directory
        task_id = self.stopped_attempt(board)
        old = state / 'attempts' / task_id / 'attempt/sessions'
        old.mkdir(parents=True)
        (old / 'session.jsonl').write_text('retained session')
        # Native grouped history lives outside Buddy's old-layout migration.
        native_group = self.directory / 'native-dsh-group.json'
        native_group.write_text('{"sessionIds":["worker-session","user-session"]}')
        relocation_started = []
        original_apply = upgrade.private_migration.apply
        def verified_relocation(*args):
            backup.verify(state / 'backups/current')
            relocation_started.append(True)
            self.assertEqual(native_group.read_text(), '{"sessionIds":["worker-session","user-session"]}')
            return original_apply(*args)
        root = self.directory / 'runtimes'
        previous, target = root / ('a' * 32), root / ('b' * 32)
        previous.mkdir(parents=True)
        target.mkdir()
        endpoint = {'runtimeIdentity':'runtime:' + previous.name, 'serviceId':'old',
                    'pid':123, 'contractVersion':'0.19.0'}
        (state / 'control.json').write_text(json.dumps(endpoint))
        health = {**endpoint, 'maxConcurrent':1, 'waitCapacity':32}
        with mock.patch.dict(os.environ, {'BUDDY_RUNTIME_ROOT':str(root)}), \
             mock.patch('hey_my_buddy.install.upgrade.get_state_dir', return_value=state), \
             mock.patch('hey_my_buddy.install.upgrade.runtime.is_ready', return_value=True), \
             mock.patch('hey_my_buddy.install.upgrade.runtime.read_ready', return_value={'sourceCommit':'fixture'}), \
             mock.patch('hey_my_buddy.install.upgrade.runtime.materialize', return_value={'runtimeDir':str(target)}), \
             mock.patch('hey_my_buddy.install.upgrade.probe', return_value=health), \
             mock.patch('hey_my_buddy.install.upgrade.detach'), \
             mock.patch('hey_my_buddy.install.upgrade.start', side_effect=[BoardError('INJECTED','target failed'), health]), \
             mock.patch('hey_my_buddy.install.upgrade.private_migration.apply', side_effect=verified_relocation), \
             mock.patch('hey_my_buddy.install.upgrade.verify_started', return_value={'retainedDataFingerprints':True}), \
             mock.patch('hey_my_buddy.blackboard.tasks.storage.prune_old_runtimes', return_value={'complete':True}):
            result = upgrade.upgrade({})
        self.assertFalse(result['upgraded'], result)
        self.assertEqual(result['failure'], 'INJECTED')
        self.assertEqual((old / 'session.jsonl').read_text(), 'retained session')
        self.assertFalse((private_dirs.attempt_root(state, 'dsh', task_id, 'attempt') / 'sessions').exists())
        self.assertEqual(relocation_started, [True])
        self.assertEqual(native_group.read_text(), '{"sessionIds":["worker-session","user-session"]}')

    def test_busy_upgrade_leaves_skill_launcher_runtime_and_service_unchanged(self):
        board = self.board()
        state = board.directory
        root = self.directory / 'runtimes'
        previous = root / ('a' * 32)
        previous.mkdir(parents=True)
        skill = self.directory / 'skills/buddy'
        (skill / 'scripts').mkdir(parents=True)
        (skill / 'SKILL.md').write_text('old skill')
        (skill / 'scripts/buddy').write_text('old launcher')
        source = self.directory / 'new-skill'
        source.mkdir()
        (source / 'SKILL.md').write_text('new skill')
        control = {'runtimeIdentity': 'runtime:' + previous.name, 'serviceId': 'existing', 'pid': 123}
        (state / 'control.json').write_text(json.dumps(control))
        active = state / 'active-runtime.json'
        active.write_text(json.dumps({'runtimeDir': str(previous)}))
        before = [(skill / 'SKILL.md').read_bytes(), (skill / 'scripts/buddy').read_bytes(),
                  active.read_bytes(), (state / 'control.json').read_bytes()]
        health = {'serviceId': 'existing', 'pid': 123, 'maxConcurrent': 1, 'waitCapacity': 32}
        busy = BoardError('UPGRADE_NOT_IDLE', 'running work', active=[{'runId': 'busy', 'state': 'running'}])
        with mock.patch.dict(os.environ, {'BUDDY_RUNTIME_ROOT': str(root)}), \
                mock.patch('hey_my_buddy.install.upgrade.get_state_dir', return_value=state), \
                mock.patch('hey_my_buddy.install.upgrade.runtime.is_ready', return_value=True), \
                mock.patch('hey_my_buddy.install.upgrade.probe', return_value=health), \
                mock.patch('hey_my_buddy.install.upgrade.idle_snapshot', side_effect=busy), \
                mock.patch('hey_my_buddy.install.upgrade.runtime.materialize') as materialize:
            with self.assertRaises(BoardError) as caught:
                upgrade.upgrade({}, skill_source=source, skill_target=skill)
        self.assertEqual(caught.exception.code, 'UPGRADE_NOT_IDLE')
        self.assertEqual(caught.exception.details['active'][0]['runId'], 'busy')
        materialize.assert_not_called()
        self.assertEqual(before, [(skill / 'SKILL.md').read_bytes(), (skill / 'scripts/buddy').read_bytes(),
                                  active.read_bytes(), (state / 'control.json').read_bytes()])

    def test_skill_swap_failure_restores_old_skill_and_launcher(self):
        skill = self.directory / 'skills/buddy'
        previous = skill.with_name('.buddy-upgrade-previous-' + 'a' * 32)
        staged = skill.with_name('.buddy-upgrade-stage-' + 'a' * 32)
        for directory, value in ((skill, 'old'), (staged, 'new')):
            (directory / 'scripts').mkdir(parents=True)
            (directory / 'SKILL.md').write_text(value)
            (directory / 'scripts/buddy').write_text(value + ' launcher')
        journal = {'skillTarget': str(skill), 'skillStage': str(staged),
                   'skillPrevious': str(previous), 'skillHadPrevious': True}
        with mock.patch('hey_my_buddy.install.skill_install.agent_skills_home', return_value=skill.parent), \
             mock.patch('hey_my_buddy.install.skill_install._link_claude', return_value={'status': 'already-linked'}):
            upgrade._publish_skill(journal)
            self.assertEqual((skill / 'SKILL.md').read_text(), 'new')
            upgrade._restore_skill(journal)
        self.assertEqual((skill / 'SKILL.md').read_text(), 'old')
        self.assertEqual((skill / 'scripts/buddy').read_text(), 'old launcher')
        self.assertFalse(previous.exists())
        self.assertFalse(staged.exists())

    def test_recovery_rejects_unrelated_skill_paths_before_deleting(self):
        unrelated = self.directory / 'unrelated'
        unrelated.mkdir()
        keep = unrelated / 'keep.txt'
        keep.write_text('user data')
        journal = {'skillTarget': str(unrelated), 'skillStage': str(self.directory / 'stage'),
                   'skillPrevious': str(self.directory / 'previous'), 'skillHadPrevious': False}
        with self.assertRaises(BoardError) as caught:
            upgrade._restore_skill(journal)
        self.assertEqual(caught.exception.code, 'UPGRADE_RECOVERY_REQUIRED')
        self.assertEqual(keep.read_text(), 'user data')

    def test_raced_admission_aborts_before_backup_and_keeps_previous_owner(self):
        board = self.board()
        state = board.directory
        root = self.directory / 'runtimes'
        previous, target = root / ('a' * 32), root / ('b' * 32)
        previous.mkdir(parents=True)
        endpoint = {'runtimeIdentity': 'runtime:' + previous.name, 'serviceId': 'old', 'pid': 123}
        (state / 'control.json').write_text(json.dumps(endpoint))
        health = {**endpoint, 'maxConcurrent': 1, 'waitCapacity': 32}
        before = upgrade.idle_snapshot(state)
        busy = BoardError('UPGRADE_NOT_IDLE', 'raced task', active=[{'runId': 'raced', 'state': 'queued'}])
        with mock.patch.dict(os.environ, {'BUDDY_RUNTIME_ROOT': str(root)}), \
             mock.patch('hey_my_buddy.install.upgrade.get_state_dir', return_value=state), \
             mock.patch('hey_my_buddy.install.upgrade.runtime.is_ready', return_value=True), \
             mock.patch('hey_my_buddy.install.upgrade.probe', return_value=health), \
             mock.patch('hey_my_buddy.install.upgrade.runtime.materialize', return_value={'runtimeDir': str(target)}), \
             mock.patch('hey_my_buddy.install.upgrade.idle_snapshot', side_effect=[before, before, busy]), \
             mock.patch('hey_my_buddy.install.upgrade.detach') as detach, \
             mock.patch('hey_my_buddy.install.upgrade.backup.create') as create:
            result = upgrade.upgrade({})
        self.assertEqual(result['error']['code'], 'UPGRADE_NOT_IDLE')
        self.assertEqual(result['error']['details']['active'][0]['runId'], 'raced')
        detach.assert_not_called()
        create.assert_not_called()
        self.assertFalse((state / 'upgrade.json').exists())
        self.assertEqual(json.loads((state / 'control.json').read_text()), endpoint)

    def _assert_fence_rejects_new_path(self, relative: str, *, linked: bool):
        board = self.board()
        state = board.directory.resolve()
        root = self.directory / 'runtimes'
        previous, target = root / ('a' * 32), root / ('b' * 32)
        previous.mkdir(parents=True)
        endpoint = {'runtimeIdentity': 'runtime:' + previous.name, 'serviceId': 'old',
                    'pid': 123, 'contractVersion': '0.19.0'}
        (state / 'control.json').write_text(json.dumps(endpoint))
        health = {**endpoint, 'maxConcurrent': 1, 'waitCapacity': 32}
        original_write = upgrade.write_journal
        inserted = False
        sentinel = self.directory / 'outside-sentinel'
        sentinel.write_text('keep outside')

        def fenced_write(path, value):
            nonlocal inserted
            original_write(path, value)
            if path == state / 'upgrade.json' and not inserted:
                inserted = True
                candidate = state / relative
                candidate.parent.mkdir(parents=True, exist_ok=True)
                if linked:
                    candidate.symlink_to(sentinel)
                else:
                    candidate.write_text('new undeclared content')

        with mock.patch.dict(os.environ, {'BUDDY_RUNTIME_ROOT': str(root)}), \
             mock.patch('hey_my_buddy.install.upgrade.get_state_dir', return_value=state), \
             mock.patch('hey_my_buddy.install.upgrade.runtime.is_ready', return_value=True), \
             mock.patch('hey_my_buddy.install.upgrade.probe', return_value=health), \
             mock.patch('hey_my_buddy.install.upgrade.runtime.materialize', return_value={'runtimeDir': str(target)}), \
             mock.patch('hey_my_buddy.install.upgrade.write_journal', side_effect=fenced_write), \
             mock.patch('hey_my_buddy.install.upgrade.detach') as detach, \
             mock.patch('hey_my_buddy.install.upgrade.start', return_value=health) as start, \
             mock.patch('hey_my_buddy.install.upgrade.backup.create') as create:
            result = upgrade.upgrade({})
        self.assertTrue(inserted)
        self.assertEqual(result['error']['code'], 'BACKUP_PREFLIGHT_FAILED', result)
        self.assertIn(relative, result['error']['details']['paths'])
        detach.assert_not_called()
        start.assert_not_called()
        create.assert_not_called()
        self.assertFalse((state / 'upgrade.json').exists())
        self.assertEqual(json.loads((state / 'control.json').read_text()), endpoint)
        self.assertEqual(sentinel.read_text(), 'keep outside')

    def test_fenced_upgrade_refuses_new_linked_evidence_before_detach(self):
        self._assert_fence_rejects_new_path('controls/new.json', linked=True)

    def test_fenced_upgrade_refuses_new_unknown_content_before_detach(self):
        self._assert_fence_rejects_new_path('attempts/unknown.txt', linked=False)

    def test_transaction_admission_rechecks_a_fence_after_rpc_entry(self):
        board = self.board()
        (board.directory / 'upgrade.json').write_text('{}')
        # Bypass the outer RPC guard, as a call that entered just before fencing would.
        with self.assertRaises(BoardError) as caught:
            board.store.task_submit({'requestId': 'late', 'task': 'late work', 'cwd': str(self.workdir()),
                                     'adapter': 'command', 'argv': ['/bin/true']})
        self.assertEqual(caught.exception.code, 'UPGRADE_IN_PROGRESS')
        self.assertEqual(board.store.count_tasks(), 0)

    def test_nonidle_preflight_refuses_without_cancelling(self):
        board=self.board()
        run=board.call('task_submit', {'requestId':'busy', 'task':'protected work', 'cwd':str(self.workdir()), 'adapter':'command', 'argv':['/bin/echo','ok']})['task']['runId']
        with self.assertRaises(BoardError) as caught:
            upgrade.idle_snapshot(board.directory)
        self.assertEqual(caught.exception.code, 'UPGRADE_NOT_IDLE')
        self.assertEqual(board.call('task_get', {'runId':run})['task']['state'], 'queued')

    def test_upgrade_journal_fences_admission_and_worker_claim_without_cancellation(self):
        board=self.board()
        marker=board.directory/'upgrade.json';marker.write_text('{}')
        self.assertEqual(board.call('ping', {})['status'], 'ok')
        with self.assertRaises(BoardError) as caught:
            board.call('task_submit', {'requestId':'blocked', 'task':'work', 'cwd':str(self.workdir()), 'adapter':'command', 'argv':['/bin/echo','ok']})
        self.assertEqual(caught.exception.code,'UPGRADE_IN_PROGRESS')
        with self.assertRaises(BoardError):
            board.call('worker_claim', {'workerId':'local','claimRequestId':'x','nonce':'x'*16})
        self.assertEqual(board.store.count_tasks(),0)

    def test_restore_verified_backup_preserves_regenerable_directories(self):
        board=self.board()
        control=board.directory/'controls/x.json';control.parent.mkdir();control.write_text('{"original":true}')
        native=board.directory/'harnesses/zcode/retained';native.mkdir(parents=True);(native/'sessions.sqlite').write_bytes(b'native')
        home=board.directory/'attempts/run/attempt/native/codex-home';home.mkdir(parents=True)
        secret=self.directory/'private-auth.json';secret.write_text('private credential')
        (home/'auth.json').symlink_to(secret)
        (home.parent.parent/'personal-provider.json').write_text('private provider key')
        current=Path(backup.create(board.store)['path'])
        control.write_text('{"changed":true}')
        with board.store.db.write() as connection:
            connection.execute("INSERT INTO meta(key,value) VALUES('post-backup','changed')")
        (board.directory / '.restore.sqlite3').write_bytes(b'partial earlier restore')
        upgrade.restore(board.directory,current)
        self.assertFalse((board.directory / '.restore.sqlite3').exists())
        self.assertEqual(json.loads(control.read_text()),{'original':True})
        self.assertEqual((native/'sessions.sqlite').read_bytes(),b'native')
        self.assertTrue((home/'auth.json').is_symlink())
        self.assertEqual(secret.read_text(),'private credential')
        self.assertFalse((current/'state/attempts/run/attempt/native/codex-home').exists())
        self.assertFalse((current/'state/attempts/run/attempt/personal-provider.json').exists())
        with board.store.db.read() as connection:
            self.assertIsNone(connection.execute("SELECT value FROM meta WHERE key='post-backup'").fetchone())
        upgrade.idle_snapshot(board.directory)

    def test_restore_rejects_linked_snapshot_without_truncating_target(self):
        board = self.board()
        state = board.directory
        current = Path(backup.create(board.store)['path'])
        sentinel = self.directory / 'outside-sentinel'
        sentinel.write_bytes(b'keep outside')
        (state / '.restore.sqlite3').symlink_to(sentinel)
        with self.assertRaises(BoardError) as caught:
            upgrade.restore(state, current)
        self.assertEqual(caught.exception.code, 'UPGRADE_UNSAFE')
        self.assertEqual(sentinel.read_bytes(), b'keep outside')
        self.assertTrue((state / '.restore.sqlite3').is_symlink())

    def test_restore_rejects_reparse_snapshot_without_changing_sentinel(self):
        board = self.board()
        state = board.directory.resolve()
        current = Path(backup.create(board.store)['path'])
        snapshot = state / '.restore.sqlite3'
        snapshot.write_bytes(b'keep reparse sentinel')
        original_linked = private_dirs.linked
        with mock.patch('hey_my_buddy.private_dirs.linked', side_effect=lambda path: path == snapshot or original_linked(path)):
            with self.assertRaises(BoardError) as caught:
                upgrade.restore(state, current)
        self.assertEqual(caught.exception.code, 'UPGRADE_UNSAFE')
        self.assertEqual(snapshot.read_bytes(), b'keep reparse sentinel')

    def test_restore_rejects_linked_intermediate_parent_before_recovery(self):
        board = self.board()
        state = board.directory
        current = Path(backup.create(board.store)['path'])
        alias = self.directory / 'linked-state-parent'
        alias.symlink_to(state, target_is_directory=True)
        with mock.patch('hey_my_buddy.install.upgrade.backup.recover') as recover:
            with self.assertRaises(BoardError) as caught:
                upgrade.restore(alias, current)
        self.assertEqual(caught.exception.code, 'UPGRADE_UNSAFE')
        recover.assert_not_called()

    def test_restore_rejects_linked_backup_parent_before_verification(self):
        board = self.board()
        current = Path(backup.create(board.store)['path'])
        alias = self.directory / 'linked-backup-parent'
        alias.symlink_to(current.parent, target_is_directory=True)
        with mock.patch('hey_my_buddy.install.upgrade.backup.recover') as recover:
            with self.assertRaises(BoardError) as caught:
                upgrade.restore(board.directory, alias / current.name)
        self.assertEqual(caught.exception.code, 'UPGRADE_UNSAFE')
        recover.assert_not_called()

    def test_restore_rejects_linked_state_destination_without_touching_target(self):
        board = self.board()
        state = board.directory
        control = state / 'controls/x.json'
        control.parent.mkdir()
        control.write_text('{"backedUp":true}')
        current = Path(backup.create(board.store)['path'])
        sentinel = self.directory / 'outside-sentinel'
        sentinel.write_bytes(b'keep outside')
        control.unlink()
        control.symlink_to(sentinel)
        with self.assertRaises(BoardError) as caught:
            upgrade.restore(state, current)
        self.assertEqual(caught.exception.code, 'UPGRADE_UNSAFE')
        self.assertEqual(sentinel.read_bytes(), b'keep outside')
        self.assertTrue(control.is_symlink())

    def test_startup_timeout_never_signals_a_daemon_or_cancels_work(self):
        board=self.board()
        child=mock.Mock()
        child.poll.return_value=None
        (self.directory/'runtime-target'/'src'/'hey_my_buddy').mkdir(parents=True)
        with mock.patch('hey_my_buddy.install.upgrade.subprocess.Popen',return_value=child), mock.patch('hey_my_buddy.install.upgrade.time.monotonic',side_effect=[0,46]):
            with self.assertRaises(BoardError) as caught:
                upgrade.start(board.directory, self.directory/'runtime-target')
        self.assertEqual(caught.exception.code,'UPGRADE_SHUTDOWN_UNCONFIRMED')
        child.terminate.assert_not_called()
        child.kill.assert_not_called()

    def test_upgrade_refuses_another_schema_without_conversion(self):
        board=self.board()
        with board.store.db.write() as connection:
            connection.execute("UPDATE meta SET value='999' WHERE key='schema_version'")
        with self.assertRaises(BoardError) as caught:
            upgrade.idle_snapshot(board.directory)
        self.assertEqual(caught.exception.code, 'UNSUPPORTED_SCHEMA')
        with board.store.db.read() as connection:
            self.assertEqual(connection.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0],'999')

    def test_retained_event_prefix_detects_rewrites_but_allows_new_events(self):
        board=self.board()
        board.call('task_submit',{'requestId':'event-fixture','task':'private','cwd':str(self.workdir()),'adapter':'command','argv':['/bin/true']})
        with board.store.db.connect() as connection:
            before=backup.database_snapshot(connection)
        with board.store.db.write() as connection:
            board.store._append_event(connection, 'private-lifecycle-observation', payload={})
        with board.store.db.connect() as connection:
            appended=backup.database_snapshot(connection,event_head=before['eventHead'])
        self.assertEqual(before['fingerprints']['events'],appended['fingerprints']['events'])
        with board.store.db.write() as connection:
            connection.execute("UPDATE events SET payload_json=? WHERE seq=(SELECT MIN(seq) FROM events)", ('{"tampered":true}',))
        with board.store.db.connect() as connection:
            after=backup.database_snapshot(connection,event_head=before['eventHead'])
        self.assertNotEqual(before['fingerprints']['events'],after['fingerprints']['events'])
