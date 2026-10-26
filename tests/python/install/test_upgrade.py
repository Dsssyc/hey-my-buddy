"""Private upgrade refusal, admission fence and backup restoration."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import errno
import json
import os
import stat
import sqlite3
import unittest
from pathlib import Path
from unittest import mock
from threading import Event
from types import SimpleNamespace
from hey_my_buddy.protocol import service_process
from hey_my_buddy.protocol.contracts import CONTRACT_VERSION
from hey_my_buddy.blackboard.store import backup
from hey_my_buddy.blackboard.store.db import SCHEMA_VERSION
from hey_my_buddy import locking, private_dirs
from hey_my_buddy.blackboard.tasks import storage
from hey_my_buddy.install import upgrade
from hey_my_buddy.install import launcher
from hey_my_buddy.errors import BoardError
from support import BoardTestCase

class UpgradeTests(BoardTestCase):
    def test_windows_replacement_and_rollback_use_independent_service_flags(self):
        state = self.directory / 'state'
        state.mkdir()
        for name in ('replacement-runtime', 'rollback-runtime'):
            with self.subTest(name=name):
                target = self.directory / name
                (target / 'src' / 'hey_my_buddy').mkdir(parents=True)
                child = mock.Mock(pid=4321)
                child.poll.return_value = None
                health = {'status': 'ok', 'runtimeContentId': target.name, 'runtimeStable': True}
                with mock.patch.object(service_process, 'os', SimpleNamespace(name='nt')), \
                     mock.patch.object(service_process.subprocess, 'Popen', return_value=child) as popen, \
                     mock.patch.object(upgrade, '_environment', return_value={}), \
                     mock.patch.object(upgrade, 'command', return_value=health) as command:
                    self.assertEqual(upgrade.start(state, target), health)
                popen.assert_called_once()
                self.assertEqual(popen.call_args.kwargs['creationflags'], 0x09000200)
                self.assertNotIn('start_new_session', popen.call_args.kwargs)
                self.assertEqual(popen.call_args.kwargs['cwd'], target)
                # Readiness is the probe's own health request, not a second one.
                command.assert_called_once_with(state, target, 'health')

    def test_start_clears_the_previous_startup_error_before_spawning(self):
        state = self.directory / 'state'
        state.mkdir()
        target = self.directory / 'runtime-target'
        (target / 'src' / 'hey_my_buddy').mkdir(parents=True)
        child = mock.Mock(pid=4321)
        child.poll.return_value = 2
        # The record even carries the spawned child's pid: only the pre-spawn
        # clear, not a pid comparison, keeps it from impersonating this start.
        launcher.write_private(state / 'startup-error.json',
                               {'pid': 4321, 'code': 'UPGRADE_SHUTDOWN_UNCONFIRMED', 'message': 'earlier start'})
        with mock.patch.object(upgrade.subprocess, 'Popen', return_value=child), \
             mock.patch.object(upgrade, 'probe', return_value=None):
            with self.assertRaises(BoardError) as caught:
                upgrade.start(state, target)
        self.assertEqual(caught.exception.code, 'UPGRADE_START_FAILED')
        self.assertFalse((state / 'startup-error.json').exists(),
                         'the previous start\'s record must be cleared before the spawn')

    def test_start_claims_a_startup_error_written_after_the_spawn(self):
        state = self.directory / 'state'
        state.mkdir()
        target = self.directory / 'runtime-target'
        (target / 'src' / 'hey_my_buddy').mkdir(parents=True)
        child = mock.Mock(pid=4321)
        child.poll.return_value = 2

        def spawn_records_the_daemons_error(*_args, **_kwargs):
            # A rollback starts an older daemon that still records its own pid,
            # which differs from the Popen handle this start reaps; its structured
            # cause travels with the code and message.
            launcher.write_private(state / 'startup-error.json', {
                'pid': 9999, 'code': 'LAUNCH_IO_ERROR', 'message': 'replacement daemon failed to bind',
                'details': {'io_operation': 'bind', 'raw_os_error': errno.EADDRINUSE}})
            return child

        with mock.patch.object(upgrade.subprocess, 'Popen', side_effect=spawn_records_the_daemons_error), \
             mock.patch.object(upgrade, 'probe', return_value=None):
            with self.assertRaises(BoardError) as caught:
                upgrade.start(state, target)
        self.assertEqual(caught.exception.payload(), {
            'code': 'LAUNCH_IO_ERROR', 'message': 'replacement daemon failed to bind',
            'details': {'io_operation': 'bind', 'raw_os_error': errno.EADDRINUSE}})

    def test_runtime_preparation_excludes_the_previous_services_reclaimer(self):
        board = self.board()
        state = board.directory
        target = self.directory / 'runtimes' / ('a' * 32)
        target.mkdir(parents=True)
        attempted, entered = Event(), Event()
        original_lock = locking.lock

        def observe_lock(fd, **options):
            attempted.set()
            return original_lock(fd, **options)

        def materialize():
            entered.set()
            # This is the existing old-service storage fence, independent of
            # either generation's runtime lock naming scheme.
            with self.assertRaises(BoardError) as busy:
                with storage.locked(board.store):
                    self.fail('old storage operation entered during installation')
            self.assertEqual(busy.exception.code, 'STORAGE_BUSY')
            return {'runtimeDir': str(target)}

        def prepare():
            with upgrade._prepare_runtime(state, skill_source=None) as result:
                with self.assertRaises(BoardError) as busy:
                    with storage.locked(board.store):
                        self.fail('old reclaimer entered after READY, before cutover')
                self.assertEqual(busy.exception.code, 'STORAGE_BUSY')
                return result

        with ThreadPoolExecutor(max_workers=1) as pool, \
             mock.patch.object(upgrade.runtime, 'materialize', side_effect=materialize):
            with storage.locked(board.store), \
                 mock.patch.object(locking, 'lock', side_effect=observe_lock):
                future = pool.submit(prepare)
                self.assertTrue(attempted.wait(5))
                self.assertFalse(entered.is_set())
            self.assertEqual(future.result(timeout=10), {'runtimeDir': str(target)})
        with storage.locked(board.store):
            pass

    def test_detach_uses_only_the_old_services_restart_fields(self):
        state = self.directory / 'state'
        state.mkdir()
        target = self.directory / 'runtimes' / ('a' * 32)
        with mock.patch.object(upgrade, 'command', return_value={}) as command, \
             mock.patch.object(upgrade, 'ensure_idle'):
            upgrade.detach(state, target, require_idle=True)
        command.assert_called_once_with(state, target, 'restart', {'drainSeconds': 0})

    def test_runtime_environment_uses_pinned_runtime_and_excludes_dev_source(self):
        state = self.directory / 'state'
        target = self.directory / 'runtimes' / ('a' * 32)
        target.mkdir(parents=True)
        base = {'HMB_DEV_SOURCE': '1',
                'HMB_MAX_CONCURRENT': '99', 'HMB_WAIT_CAPACITY': '99'}
        with mock.patch('hey_my_buddy.install.launcher.service_environment', return_value=base):
            environment = upgrade._environment(state, target)
        self.assertEqual(environment['HMB_STATE_DIR'], str(state))
        self.assertEqual(environment['HMB_RUNTIME'], str(target))
        self.assertEqual(environment['HMB_RUNTIME_ROOT'], str(target.parent))
        self.assertEqual(environment['HMB_RUNTIME_IDENTITY'], 'runtime:' + target.name)
        self.assertEqual(environment['HMB_MAX_CONCURRENT'], '99')
        self.assertEqual(environment['HMB_WAIT_CAPACITY'], '99')
        self.assertNotIn('HMB_DEV_SOURCE', environment)

    def test_runtime_switch_preserves_current_launch_settings(self):
        state = self.directory / 'state'
        state.mkdir()
        target = self.directory / 'runtimes' / ('a' * 32)
        settings = {'HMB_MAX_CONCURRENT': '3', 'HMB_WAIT_CAPACITY': '48'}
        launcher.write_active_runtime(state, target, settings)
        with mock.patch('hey_my_buddy.install.launcher.service_environment', return_value={}):
            environment = upgrade._environment(state, target)
        self.assertEqual(environment['HMB_MAX_CONCURRENT'], '3')
        self.assertEqual(environment['HMB_WAIT_CAPACITY'], '48')
        self.assertEqual(json.loads((state / 'launch-settings.json').read_text(encoding="utf-8"))['environment'], settings)

    def test_upgrade_environment_rejects_invalid_persisted_launch_capacity(self):
        state = self.directory / 'state'
        state.mkdir()
        for settings in ({'HMB_MAX_CONCURRENT': '99'}, {'HMB_WAIT_CAPACITY': '0'}, {'OTHER': '1'}):
            launcher.write_private(state / 'launch-settings.json', {'environment': settings})
            with self.subTest(settings=settings), self.assertRaises(BoardError) as failure:
                upgrade._environment(state, self.directory / 'runtime')
            self.assertEqual(failure.exception.code, 'LAUNCH_SETTINGS_INVALID')

    def test_verification_uses_health_runtime_stability_without_a_second_request(self):
        target = self.directory / 'runtimes' / ('a' * 32)
        health = {'runtimeContentId': target.name, 'runtimeStable': True, 'schemaVersion': SCHEMA_VERSION}
        with mock.patch.object(upgrade, 'command', side_effect=AssertionError('extra runtime request')):
            self.assertEqual(upgrade.verify_started(self.directory, target, health, {"schema": SCHEMA_VERSION}, contract_changed=False),
                             {'runtimeContentId': target.name, 'schemaVersion': SCHEMA_VERSION})
            health['runtimeStable'] = False
            with self.assertRaises(BoardError) as failure:
                upgrade.verify_started(self.directory, target, health, {"schema": SCHEMA_VERSION}, contract_changed=False)
        self.assertEqual(failure.exception.code, 'UPGRADE_VERIFY_FAILED')

    def test_verified_upgrade_reports_skill_cleanup_failure_without_rollback(self):
        board = self.board()
        state, root = board.directory, self.directory / 'runtimes'
        previous, target = root / ('a' * 32), root / ('b' * 32)
        previous.mkdir(parents=True)
        retired = state / 'worker.log'
        retired.write_bytes(b'preserve on same-contract install')
        skill = self.directory / 'skills/hey-my-buddy'
        source = self.directory / 'new-skill'
        for path, content in ((skill, 'old'), (source, 'new')):
            path.mkdir(parents=True)
            (path / 'skill.json').write_text(json.dumps({'name': 'hey-my-buddy', 'contract': CONTRACT_VERSION, 'sourceCommit': None}), encoding='utf-8')
            (path / 'SKILL.md').write_text(content, encoding='utf-8')
        launcher.write_private(state / 'control.json', {'runtimeIdentity': 'runtime:' + previous.name,
                               'serviceId': 'owner', 'pid': 123, 'contractVersion': CONTRACT_VERSION})
        health = {'serviceId': 'owner', 'pid': 123, 'maxConcurrent': 1, 'waitCapacity': 32,
                  'runtimeContentId': target.name, 'runtimeStable': True, 'schemaVersion': SCHEMA_VERSION,
                  'contractVersion': CONTRACT_VERSION}
        remove = upgrade.shutil.rmtree

        def refuse_previous(path, *args, **kwargs):
            if path.name.startswith('.hey-my-buddy-upgrade-previous-'):
                raise PermissionError('skill cleanup denied')
            return remove(path, *args, **kwargs)

        with mock.patch.dict(os.environ, {'HMB_RUNTIME_ROOT': str(root)}), \
             mock.patch.object(upgrade.runtime, 'is_ready', return_value=True), \
             mock.patch.object(upgrade.runtime, 'read_ready', return_value={'sourceCommit': None}), \
             mock.patch.object(upgrade.runtime, 'materialize', return_value={'runtimeDir': str(target)}), \
             mock.patch.object(upgrade, 'probe', return_value=health), \
             mock.patch.object(upgrade, 'detach'), \
             mock.patch.object(upgrade, 'start', return_value=health) as start, \
             mock.patch.object(upgrade, 'retain_runtimes', return_value={'complete': True}), \
             mock.patch('hey_my_buddy.install.skill_install._link_claude', return_value={'status': 'already-linked'}), \
             mock.patch.object(upgrade.shutil, 'rmtree', side_effect=refuse_previous):
            result = upgrade.upgrade(state, skill_source=source, skill_target=skill)
        self.assertTrue(result['upgraded'], result)
        self.assertEqual(retired.read_bytes(), b'preserve on same-contract install')
        self.assertIn('skill cleanup denied', result['skillCleanupError'])
        self.assertFalse((state / 'upgrade.json').exists())
        self.assertEqual(launcher.read_private(state / 'active-runtime.json')['runtimeDir'], str(target))
        self.assertEqual((skill / 'SKILL.md').read_text(encoding='utf-8'), 'new')
        start.assert_called_once_with(state, target)

    def test_same_contract_verified_recovery_keeps_history_after_skill_failure(self):
        state, root = self.directory / 'state', self.directory / 'runtimes'
        state.mkdir()
        goals = state / 'harnesses/codex/goals'
        goals.mkdir(parents=True)
        (goals / 'retired.jsonl').write_bytes(b'retired native history')
        (state / 'worker.log').write_bytes(b'retired worker log')
        previous, target = root / ('a' * 32), root / ('b' * 32)
        skill = self.directory / 'skills/hey-my-buddy'
        staged = skill.with_name('.hey-my-buddy-upgrade-stage-' + 'c' * 32)
        old_skill = skill.with_name('.hey-my-buddy-upgrade-previous-' + 'c' * 32)
        old_skill.mkdir(parents=True)
        journal = {'phase': 'verified', 'contractChanged': False, 'previous': str(previous), 'target': str(target),
                   'before': {}, 'expected': {}, 'skillTarget': str(skill), 'skillStage': str(staged), 'skillPrevious': str(old_skill)}
        upgrade.write_journal(state / 'upgrade.json', journal)
        with mock.patch('hey_my_buddy.install.skill_install.agent_skills_home', return_value=skill.parent), \
             mock.patch.object(upgrade.runtime, 'is_ready', return_value=True), \
             mock.patch.object(upgrade, 'probe', return_value={'runtimeContentId': target.name}), \
             mock.patch.object(upgrade, 'verify_started', return_value={}), \
             mock.patch.object(upgrade, '_verify_skill_generation'), \
             mock.patch.object(upgrade, 'retain_runtimes', return_value={'complete': True}), \
             mock.patch.object(upgrade.shutil, 'rmtree', side_effect=PermissionError('cleanup denied')), \
             mock.patch.object(upgrade, 'start', side_effect=AssertionError('rollback')):
            result = upgrade.recover(state, root)
        self.assertTrue(result['upgraded'])
        self.assertIn('cleanup denied', result['skillCleanupError'])
        self.assertEqual((goals / 'retired.jsonl').read_bytes(), b'retired native history')
        self.assertEqual((state / 'worker.log').read_bytes(), b'retired worker log')
        self.assertTrue(old_skill.exists())
        self.assertFalse((state / 'upgrade.json').exists())

    def test_runtime_retention_reports_expected_errors_and_propagates_defects(self):
        for error in (BoardError('STORAGE_BUSY', 'in use'), OSError('retention write denied')):
            with self.subTest(error=type(error).__name__), mock.patch.object(upgrade, 'write_journal', side_effect=error):
                result = upgrade.retain_runtimes(self.directory, self.directory / 'new', self.directory / 'old')
            self.assertFalse(result['complete'])
            self.assertIn(str(error), result['message'])
        with mock.patch.object(upgrade, 'write_journal', side_effect=TypeError('defect')), self.assertRaises(TypeError):
            upgrade.retain_runtimes(self.directory, self.directory / 'new', self.directory / 'old')

    def test_verified_recovery_keeps_journal_when_clear_fails_without_rollback(self):
        from hey_my_buddy.blackboard.store.store import BoardStore
        state = self.directory / 'state'
        state.mkdir()
        store = BoardStore(state)
        self.addCleanup(store.db.close)
        store.initialize()
        root = self.directory / 'runtimes'
        previous, target = root / ('a' * 32), root / ('b' * 32)
        journal = {'phase': 'verified', 'contractChanged': True, 'previous': str(previous),
                   'target': str(target), 'before': {'schema': SCHEMA_VERSION}, 'expected': {'schema': SCHEMA_VERSION}}
        marker = state / 'upgrade.json'
        upgrade.write_journal(marker, journal)

        def refuse_clear(_path):
            self.assertTrue(marker.exists())
            self.assertEqual(json.loads((state / 'upgrade-last.json').read_text(encoding="utf-8"))['phase'], 'verified')
            raise OSError('denied')

        with mock.patch.object(upgrade.runtime, 'is_ready', return_value=True), \
             mock.patch.object(upgrade, 'probe', return_value={'runtimeContentId': target.name}), \
             mock.patch.object(upgrade, 'verify_started', return_value={'runtimeContentId': target.name, 'schemaVersion': SCHEMA_VERSION}), \
             mock.patch.object(upgrade, 'write_active_runtime'), \
             mock.patch.object(upgrade, 'clear_journal', side_effect=refuse_clear):
            result = upgrade.recover(state, root)
        self.assertTrue(result['upgraded'])
        self.assertEqual(result['journalError'], 'denied')
        self.assertTrue(marker.exists())
        self.assertEqual(json.loads(marker.read_text(encoding="utf-8"))['phase'], 'verified')

    def test_busy_upgrade_leaves_skill_launcher_runtime_and_service_unchanged(self):
        board = self.board()
        state = board.directory
        root = self.directory / 'runtimes'
        previous = root / ('a' * 32)
        previous.mkdir(parents=True)
        skill = self.directory / 'skills/hey-my-buddy'
        (skill / 'scripts').mkdir(parents=True)
        (skill / 'SKILL.md').write_text('old skill', encoding="utf-8", newline="\n")
        (skill / 'scripts/hey-my-buddy').write_text('old launcher', encoding="utf-8", newline="\n")
        source = self.directory / 'new-skill'
        source.mkdir()
        (source / 'SKILL.md').write_text('new skill', encoding="utf-8", newline="\n")
        control = {'runtimeIdentity': 'runtime:' + previous.name, 'serviceId': 'existing', 'pid': 123,
                   'contractVersion': '0.19.0'}
        (state / 'control.json').write_text(json.dumps(control), encoding="utf-8", newline="\n")
        active = state / 'active-runtime.json'
        active.write_text(json.dumps({'runtimeDir': str(previous)}), encoding="utf-8", newline="\n")
        before = [(skill / 'SKILL.md').read_bytes(), (skill / 'scripts/hey-my-buddy').read_bytes(),
                  active.read_bytes(), (state / 'control.json').read_bytes()]
        health = {'serviceId': 'existing', 'pid': 123, 'maxConcurrent': 1, 'waitCapacity': 32, 'contractVersion': '0.19.0'}
        busy = BoardError('UPGRADE_NOT_IDLE', 'running work', active=[{'runId': 'busy', 'state': 'running'}])
        with mock.patch.dict(os.environ, {'HMB_RUNTIME_ROOT': str(root)}), \
                mock.patch('hey_my_buddy.install.upgrade.runtime.is_ready', return_value=True), \
             mock.patch('hey_my_buddy.install.upgrade.runtime.read_ready', return_value={'sourceCommit': 'f' * 40}), \
                mock.patch('hey_my_buddy.install.upgrade.probe', return_value=health), \
                mock.patch('hey_my_buddy.install.upgrade.ensure_idle', side_effect=busy), \
                mock.patch('hey_my_buddy.install.upgrade.runtime.materialize') as materialize:
            with self.assertRaises(BoardError) as caught:
                upgrade.upgrade(state, skill_source=source, skill_target=skill)
        self.assertEqual(caught.exception.code, 'UPGRADE_NOT_IDLE')
        self.assertEqual(caught.exception.details['active'][0]['runId'], 'busy')
        materialize.assert_not_called()
        self.assertEqual(before, [(skill / 'SKILL.md').read_bytes(), (skill / 'scripts/hey-my-buddy').read_bytes(),
                                  active.read_bytes(), (state / 'control.json').read_bytes()])

    def test_skill_swap_failure_restores_old_skill_and_launcher(self):
        skill = self.directory / 'skills/hey-my-buddy'
        old_skill = skill.with_name('buddy')
        old_skill.mkdir(parents=True)
        (old_skill / 'skill.json').write_text(json.dumps({'name': 'buddy'}), encoding="utf-8", newline="\n")
        old_link = self.directory / 'claude/buddy'
        old_link.parent.mkdir()
        old_link.symlink_to(old_skill, target_is_directory=True)
        previous = skill.with_name('.hey-my-buddy-upgrade-previous-' + 'a' * 32)
        staged = skill.with_name('.hey-my-buddy-upgrade-stage-' + 'a' * 32)
        for directory, value in ((skill, 'old'), (staged, 'new')):
            (directory / 'scripts').mkdir(parents=True)
            (directory / 'SKILL.md').write_text(value, encoding="utf-8", newline="\n")
            (directory / 'scripts/hey-my-buddy').write_text(value + ' launcher', encoding="utf-8", newline="\n")
        journal = {'skillTarget': str(skill), 'skillStage': str(staged),
                   'skillPrevious': str(previous), 'skillHadPrevious': True}
        with mock.patch('hey_my_buddy.install.skill_install.agent_skills_home', return_value=skill.parent), \
             mock.patch('hey_my_buddy.install.skill_install._link_claude', return_value={'status': 'already-linked'}):
            upgrade._publish_skill(journal, (skill, staged, previous))
            self.assertEqual((skill / 'SKILL.md').read_text(encoding="utf-8"), 'new')
            upgrade._restore_skill(journal, (skill, staged, previous))
        self.assertEqual((skill / 'SKILL.md').read_text(encoding="utf-8"), 'old')
        self.assertEqual((skill / 'scripts/hey-my-buddy').read_text(encoding="utf-8"), 'old launcher')
        self.assertFalse(previous.exists())
        self.assertFalse(staged.exists())
        self.assertEqual(json.loads((old_skill / 'skill.json').read_text(encoding="utf-8")), {'name': 'buddy'})
        self.assertTrue(old_link.is_symlink())

    def test_recovery_rejects_unrelated_skill_paths_before_deleting(self):
        unrelated = self.directory / 'unrelated'
        unrelated.mkdir()
        keep = unrelated / 'keep.txt'
        keep.write_text('user data', encoding="utf-8", newline="\n")
        journal = {'contractChanged': False, 'skillTarget': str(unrelated), 'skillStage': str(self.directory / 'stage'),
                   'skillPrevious': str(self.directory / 'previous'), 'skillHadPrevious': False}
        with self.assertRaises(BoardError) as caught:
            state = self.directory / 'state'
            state.mkdir()
            upgrade.write_journal(state / 'upgrade.json', journal)
            upgrade.recover(state, self.directory / 'runtimes')
        self.assertEqual(caught.exception.code, 'UPGRADE_RECOVERY_REQUIRED')
        self.assertEqual(keep.read_text(encoding="utf-8"), 'user data')

    def test_raced_admission_aborts_before_backup_and_keeps_previous_owner(self):
        board = self.board()
        state = board.directory
        root = self.directory / 'runtimes'
        previous, target = root / ('a' * 32), root / ('b' * 32)
        previous.mkdir(parents=True)
        endpoint = {'runtimeIdentity': 'runtime:' + previous.name, 'serviceId': 'old', 'pid': 123, 'contractVersion': '0.19.0'}
        (state / 'control.json').write_text(json.dumps(endpoint), encoding="utf-8", newline="\n")
        health = {**endpoint, 'maxConcurrent': 1, 'waitCapacity': 32}
        busy = BoardError('UPGRADE_NOT_IDLE', 'raced task', active=[{'runId': 'raced', 'state': 'queued'}])

        def raced_snapshot(directory):
            self.assertEqual(directory, state)
            command.assert_called_once_with(state, previous, 'restart', {'drainSeconds': 0})
            with self.assertRaises(BoardError) as held:
                with upgrade.file_lock(state / 'board-owner.lock'):
                    self.fail('idle snapshot ran without the owner lock')
            self.assertEqual(held.exception.code, 'UPGRADE_BUSY')
            raise busy

        with mock.patch.dict(os.environ, {'HMB_RUNTIME_ROOT': str(root)}), \
             mock.patch('hey_my_buddy.install.upgrade.runtime.is_ready', return_value=True), \
             mock.patch('hey_my_buddy.install.upgrade.runtime.read_ready', return_value={'sourceCommit': 'f' * 40}), \
             mock.patch('hey_my_buddy.install.upgrade.probe', return_value=health), \
             mock.patch('hey_my_buddy.install.upgrade.runtime.materialize', return_value={'runtimeDir': str(target)}), \
             mock.patch.object(upgrade, 'ensure_idle', wraps=upgrade.ensure_idle) as ensure_idle, \
             mock.patch.object(upgrade, 'idle_snapshot', side_effect=raced_snapshot) as snapshot, \
             mock.patch.object(upgrade, 'command', return_value={}) as command, \
             mock.patch.object(upgrade, 'start', return_value=health) as start, \
             mock.patch('hey_my_buddy.install.upgrade.backup.create') as create:
            result = upgrade.upgrade(state)
        self.assertEqual(result['error']['code'], 'UPGRADE_NOT_IDLE')
        self.assertEqual(result['error']['details']['active'][0]['runId'], 'raced')
        self.assertNotIn('failure', result)
        reason = result['error']['details']['reason']
        self.assertEqual(reason, {'code':'UPGRADE_NOT_IDLE', 'message':'raced task',
                                 'details':{'active':[{'runId':'raced', 'state':'queued'}], 'tables':[]}})
        journal = json.loads((state / 'upgrade-last.json').read_text(encoding="utf-8"))
        self.assertEqual(journal['phase'], 'rolled-back')
        self.assertEqual(journal['failure'], reason)
        self.assertTrue(result['rollback']['workPreserved'])
        command.assert_called_once_with(state, previous, 'restart', {'drainSeconds': 0})
        ensure_idle.assert_called_once_with(state)
        snapshot.assert_called_once_with(state)
        start.assert_called_once_with(state, previous)
        create.assert_not_called()
        self.assertFalse((state / 'upgrade.json').exists())
        self.assertEqual(json.loads((state / 'control.json').read_text(encoding="utf-8")), endpoint)
        self.assertEqual(launcher.read_private(state / 'active-runtime.json')['runtimeDir'], str(previous))

    def test_changed_contract_recovery_without_backup_preserves_raced_work(self):
        state, root = self.directory / 'state', self.directory / 'runtimes'
        state.mkdir()
        previous, target = root / ('a' * 32), root / ('b' * 32)
        journal = {'phase': 'recovery-required', 'contractChanged': True, 'previous': str(previous),
                   'target': str(target), 'before': {'schema': SCHEMA_VERSION}}
        marker = state / 'upgrade.json'
        upgrade.write_journal(marker, journal)
        launcher.write_private(state / 'control.json', {'runtimeIdentity': 'runtime:' + previous.name})
        with mock.patch.object(upgrade.runtime, 'is_ready', return_value=True), \
             mock.patch.object(upgrade, 'probe', return_value=None), \
             mock.patch.object(upgrade, 'ensure_idle', side_effect=AssertionError('raced work must be kept')), \
             mock.patch.object(upgrade, 'idle_snapshot', side_effect=AssertionError('raced work must be kept')), \
             mock.patch.object(upgrade, 'restore', side_effect=AssertionError('no board was replaced')), \
             mock.patch.object(upgrade, 'start', return_value={}) as start:
            with upgrade.file_lock(state / 'board-owner.lock'), \
                 mock.patch.object(upgrade, 'OWNER_LOCK_TIMEOUT_SECONDS', 0):
                with self.assertRaises(BoardError) as held:
                    upgrade.recover(state, root)
            self.assertEqual(held.exception.code, 'UPGRADE_BUSY')
            start.assert_not_called()
            self.assertTrue(marker.exists())
            result = upgrade.recover(state, root)
        self.assertEqual(result, {'upgraded': False, 'recoveredJournal': True,
                                 'rollback': {'runtimeContentId': previous.name, 'workPreserved': True}})
        start.assert_called_once_with(state, previous)
        self.assertFalse(marker.exists())
        self.assertEqual(launcher.read_private(state / 'active-runtime.json')['runtimeDir'], str(previous))

    def test_fenced_upgrade_rejects_new_linked_state_file_and_rolls_back(self):
        board = self.board()
        state = board.directory.resolve()
        root = self.directory / 'runtimes'
        previous, target = root / ('a' * 32), root / ('b' * 32)
        previous.mkdir(parents=True)
        endpoint = {'runtimeIdentity': 'runtime:' + previous.name, 'serviceId': 'old',
                    'pid': 123, 'contractVersion': '0.19.0'}
        (state / 'control.json').write_text(json.dumps(endpoint), encoding="utf-8", newline="\n")
        health = {**endpoint, 'maxConcurrent': 1, 'waitCapacity': 32}
        original_write = upgrade.write_journal
        inserted = False
        sentinel = self.directory / 'outside-sentinel'
        sentinel.write_text('keep outside', encoding="utf-8", newline="\n")
        relative = 'console-settings.json'
        evidence = state / 'attempts/run/attempt/result.json'
        evidence.parent.mkdir(parents=True)
        evidence.write_text('retained evidence', encoding="utf-8", newline="\n")
        checkout = state / 'workspaces/checkout/keep.txt'
        checkout.parent.mkdir(parents=True)
        checkout.write_text('retained checkout', encoding="utf-8", newline="\n")

        def fenced_write(path, value):
            nonlocal inserted
            original_write(path, value)
            if path == state / 'upgrade.json' and not inserted:
                inserted = True
                candidate = state / relative
                candidate.symlink_to(sentinel)

        board.store.db.close()
        with mock.patch.dict(os.environ, {'HMB_RUNTIME_ROOT': str(root)}), \
             mock.patch('hey_my_buddy.install.upgrade.runtime.is_ready', return_value=True), \
             mock.patch('hey_my_buddy.install.upgrade.runtime.read_ready', return_value={'sourceCommit': 'f' * 40}), \
             mock.patch('hey_my_buddy.install.upgrade.probe', return_value=health), \
             mock.patch('hey_my_buddy.install.upgrade.runtime.materialize', return_value={'runtimeDir': str(target)}), \
             mock.patch('hey_my_buddy.install.upgrade.write_journal', side_effect=fenced_write), \
             mock.patch('hey_my_buddy.install.upgrade.detach') as detach, \
             mock.patch('hey_my_buddy.install.upgrade.start', return_value=health) as start:
            result = upgrade.upgrade(state)
        self.assertTrue(inserted)
        self.assertEqual(result['error']['code'], 'UPGRADE_ROLLED_BACK', result)
        self.assertEqual(result['error']['message'], 'Installation failed; the previous runtime was restored')
        self.assertEqual(result['error']['details']['reason']['code'], 'BACKUP_UNSAFE_PATH')
        self.assertEqual(result['error']['details']['reason']['details']['path'], str(state / relative))
        detach.assert_called()
        start.assert_called()
        self.assertFalse((state / 'upgrade.json').exists())
        self.assertEqual(json.loads((state / 'control.json').read_text(encoding="utf-8")), endpoint)
        self.assertEqual(sentinel.read_text(encoding="utf-8"), 'keep outside')
        self.assertEqual(evidence.read_text(encoding="utf-8"), 'retained evidence')
        self.assertEqual(checkout.read_text(encoding="utf-8"), 'retained checkout')
        self.assertFalse((state / 'backups/current').exists())

    def test_transaction_admission_rechecks_a_fence_after_rpc_entry(self):
        board = self.board()
        (board.directory / 'upgrade.json').write_text('{}', encoding="utf-8", newline="\n")
        # Bypass the outer RPC guard, as a call that entered just before fencing would.
        with self.assertRaises(BoardError) as caught:
            board.store.task_submit({'requestId': 'late', 'task': 'late work', 'cwd': str(self.workdir()),
                                     'adapter': 'command', 'argv': ['/bin/true']})
        self.assertEqual(caught.exception.code, 'UPGRADE_IN_PROGRESS')
        self.assertEqual(board.store.count_tasks(), 0)

    def test_nonidle_preflight_refuses_without_cancelling(self):
        board=self.board()
        run=board.submit_execution(**{'requestId':'busy', 'task':'protected work', 'cwd':str(self.workdir()), 'adapter':'command', 'argv':['/bin/echo','ok']})['task']['runId']
        with self.assertRaises(BoardError) as caught:
            upgrade.idle_snapshot(board.directory)
        self.assertEqual(caught.exception.code, 'UPGRADE_NOT_IDLE')
        self.assertEqual(board.call('task_get', {'runId':run})['task']['state'], 'queued')

    def test_idle_preflight_checks_admission_without_scanning_all_rows(self):
        board = self.board()
        with mock.patch.object(backup, 'database_snapshot', side_effect=AssertionError('full scan during preflight')):
            self.assertEqual(upgrade.preflight(board.directory, contract_changed=True), {'schema': SCHEMA_VERSION})

    def test_upgrade_journal_fences_admission_without_cancellation(self):
        board=self.board()
        marker=board.directory/'upgrade.json';marker.write_text('{}', encoding="utf-8", newline="\n")
        self.assertEqual(board.call('ping', {})['status'], 'ok')
        with self.assertRaises(BoardError) as caught:
            board.call('task_retry', {'runId': 'blocked'})
        self.assertEqual(caught.exception.code,'UPGRADE_IN_PROGRESS')
        self.assertEqual(board.store.count_tasks(),0)

    def test_restore_verified_backup_replays_only_saved_state_files(self):
        board=self.board()
        settings=board.directory/'console-settings.json';settings.write_text('{"original":true}', encoding="utf-8", newline="\n")
        preserved = {name: board.directory / name for name in
                     ('active-runtime.json', 'runtime-retention.json')}
        for path in preserved.values():
            path.write_text('{"beforeBackup":true}', encoding="utf-8", newline="\n")
        missing=board.directory/'launch-settings.json'
        native=board.directory/'harnesses/zcode/retained';native.mkdir(parents=True);(native/'sessions.sqlite').write_bytes(b'native')
        home=board.directory/'attempts/run/attempt/native/codex-home';home.mkdir(parents=True)
        secret=self.directory/'private-auth.json';secret.write_text('private credential', encoding="utf-8", newline="\n")
        (home/'auth.json').symlink_to(secret)
        (home.parent.parent/'personal-provider.json').write_text('private provider key', encoding="utf-8", newline="\n")
        board.store.db.close()
        current=Path(backup.create(board.directory, runtime_identity=None, plugin_commit=None, contract_version=CONTRACT_VERSION)['path'])
        settings.write_text('{"changed":true}', encoding="utf-8", newline="\n")
        for path in preserved.values():
            path.write_text('{"afterBackup":true}', encoding="utf-8", newline="\n")
        missing.write_text('{"createdAfterBackup":true}', encoding="utf-8", newline="\n")
        evidence=board.directory/'attempts/run/attempt/result.json'
        evidence.write_text('post-backup evidence', encoding="utf-8", newline="\n")
        checkout=board.directory/'workspaces/checkout/keep.txt'
        checkout.parent.mkdir(parents=True)
        checkout.write_text('post-backup checkout', encoding="utf-8", newline="\n")
        with closing(sqlite3.connect(board.directory / 'board.sqlite3')) as connection, connection:
            connection.execute("INSERT INTO meta(key,value) VALUES('post-backup','changed')")
        (board.directory / '.restore.sqlite3').write_bytes(b'partial earlier restore')
        upgrade.restore(board.directory,current)
        self.assertFalse((board.directory / '.restore.sqlite3').exists())
        self.assertEqual(json.loads(settings.read_text(encoding="utf-8")),{'original':True})
        self.assertEqual(json.loads(missing.read_text(encoding="utf-8")),{'createdAfterBackup':True})
        for path in preserved.values():
            self.assertEqual(json.loads(path.read_text(encoding="utf-8")), {'afterBackup': True})
        self.assertEqual(evidence.read_text(encoding="utf-8"),'post-backup evidence')
        self.assertEqual(checkout.read_text(encoding="utf-8"),'post-backup checkout')
        self.assertEqual((native/'sessions.sqlite').read_bytes(),b'native')
        self.assertTrue((home/'auth.json').is_symlink())
        self.assertEqual(secret.read_text(encoding="utf-8"),'private credential')
        self.assertFalse((current/'state/attempts/run/attempt/native/codex-home').exists())
        self.assertFalse((current/'state/attempts/run/attempt/personal-provider.json').exists())
        with closing(sqlite3.connect(board.directory / 'board.sqlite3')) as connection:
            self.assertIsNone(connection.execute("SELECT value FROM meta WHERE key='post-backup'").fetchone())
        upgrade.idle_snapshot(board.directory)

    def test_restore_rejects_linked_snapshot_without_truncating_target(self):
        board = self.board()
        state = board.directory
        board.store.db.close()
        current = Path(backup.create(board.directory, runtime_identity=None, plugin_commit=None, contract_version=CONTRACT_VERSION)['path'])
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
        board.store.db.close()
        current = Path(backup.create(board.directory, runtime_identity=None, plugin_commit=None, contract_version=CONTRACT_VERSION)['path'])
        snapshot = state / '.restore.sqlite3'
        snapshot.write_bytes(b'keep reparse sentinel')
        original_linked = private_dirs.linked
        with mock.patch('hey_my_buddy.private_dirs.linked', side_effect=lambda path: path == snapshot or original_linked(path)):
            with self.assertRaises(BoardError) as caught:
                upgrade.restore(state, current)
        self.assertEqual(caught.exception.code, 'UPGRADE_UNSAFE')
        self.assertEqual(snapshot.read_bytes(), b'keep reparse sentinel')

    def test_restore_rejects_linked_intermediate_parent_before_verification(self):
        board = self.board()
        state = board.directory
        board.store.db.close()
        current = Path(backup.create(board.directory, runtime_identity=None, plugin_commit=None, contract_version=CONTRACT_VERSION)['path'])
        alias = self.directory / 'linked-state-parent'
        alias.symlink_to(state, target_is_directory=True)
        with mock.patch('hey_my_buddy.install.upgrade.backup.verify') as verify:
            with self.assertRaises(BoardError) as caught:
                upgrade.restore(alias, current)
        self.assertEqual(caught.exception.code, 'UPGRADE_UNSAFE')
        verify.assert_not_called()


    def test_restore_rejects_linked_state_destination_without_touching_target(self):
        board = self.board()
        state = board.directory
        control = state / 'console-settings.json'
        control.write_text('{"backedUp":true}', encoding="utf-8", newline="\n")
        board.store.db.close()
        current = Path(backup.create(board.directory, runtime_identity=None, plugin_commit=None, contract_version=CONTRACT_VERSION)['path'])
        sentinel = self.directory / 'outside-sentinel'
        sentinel.write_bytes(b'keep outside')
        control.unlink()
        control.symlink_to(sentinel)
        with self.assertRaises(BoardError) as caught:
            upgrade.restore(state, current)
        self.assertEqual(caught.exception.code, 'UPGRADE_UNSAFE')
        self.assertEqual(sentinel.read_bytes(), b'keep outside')
        self.assertTrue(control.is_symlink())

    def test_restore_rejects_parent_traversal_before_verifying_backup(self):
        state = self.directory / 'state' / '..' / 'state'
        current = self.directory / 'backups/current'
        with mock.patch.object(backup, 'verify') as verify:
            with self.assertRaises(BoardError) as caught:
                upgrade.restore(state, current)
        self.assertEqual(caught.exception.code, 'UPGRADE_UNSAFE')
        verify.assert_not_called()

    @unittest.skipUnless(hasattr(os, 'mkfifo'), 'requires POSIX FIFOs')
    def test_restore_refuses_special_existing_targets_before_changing_board(self):
        board = self.board()
        state = board.directory
        settings = state / 'console-settings.json'
        settings.write_text('{"backedUp":true}', encoding="utf-8", newline="\n")
        board.store.db.close()
        current = Path(backup.create(board.directory, runtime_identity=None, plugin_commit=None, contract_version=CONTRACT_VERSION)['path'])
        board_bytes = (state / 'board.sqlite3').read_bytes()
        evidence = state / 'attempts/run/attempt/result.json'
        evidence.parent.mkdir(parents=True)
        evidence.write_text('retained evidence', encoding="utf-8", newline="\n")
        checkout = state / 'workspaces/checkout/keep.txt'
        checkout.parent.mkdir(parents=True)
        checkout.write_text('retained checkout', encoding="utf-8", newline="\n")
        for name in ('board.sqlite3', 'board.sqlite3-wal', 'board.sqlite3-shm',
                     'console-settings.json', '.restore.sqlite3'):
            with self.subTest(path=name):
                target = state / name
                original = target.read_bytes() if target.exists() else None
                target.unlink(missing_ok=True)
                os.mkfifo(target)
                try:
                    with self.assertRaises(BoardError) as caught:
                        upgrade.restore(state, current)
                    self.assertEqual(caught.exception.code, 'UPGRADE_UNSAFE')
                    self.assertTrue(stat.S_ISFIFO(target.lstat().st_mode))
                    if name != 'board.sqlite3':
                        self.assertEqual((state / 'board.sqlite3').read_bytes(), board_bytes)
                    self.assertEqual(evidence.read_text(encoding="utf-8"), 'retained evidence')
                    self.assertEqual(checkout.read_text(encoding="utf-8"), 'retained checkout')
                finally:
                    target.unlink()
                    if original is not None:
                        target.write_bytes(original)

    def test_startup_timeout_never_signals_a_daemon_or_cancels_work(self):
        board=self.board()
        child=mock.Mock()
        child.poll.return_value=None
        (self.directory/'runtime-target'/'src'/'hey_my_buddy').mkdir(parents=True)
        with mock.patch('hey_my_buddy.install.upgrade.subprocess.Popen',return_value=child), \
             mock.patch.object(upgrade, 'probe', return_value=None), \
             mock.patch('hey_my_buddy.install.upgrade.time.monotonic',side_effect=[0,46]):
            with self.assertRaises(BoardError) as caught:
                upgrade.start(board.directory, self.directory/'runtime-target')
        self.assertEqual(caught.exception.code,'UPGRADE_SHUTDOWN_UNCONFIRMED')
        child.terminate.assert_not_called()
        child.kill.assert_not_called()

    def test_upgrade_refuses_older_or_unknown_schema(self):
        board=self.board()
        for version in ('19', '20', '21', '22', '23', '24', '25', '26', '27', '28', '31', '028', '029', '999'):
            with self.subTest(schema=version):
                with board.store.db.write() as connection:
                    connection.execute("UPDATE meta SET value=? WHERE key='schema_version'", (version,))
                with self.assertRaises(BoardError) as caught:
                    upgrade.idle_snapshot(board.directory)
                self.assertEqual(caught.exception.code, 'UNSUPPORTED_SCHEMA')
                with board.store.db.read() as connection:
                    self.assertEqual(connection.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0], version)

    def test_retained_event_prefix_detects_rewrites_but_allows_new_events(self):
        board=self.board()
        board.submit_execution(**{'requestId':'event-fixture','task':'private','cwd':str(self.workdir()),'adapter':'command','argv':['/bin/true']})
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
