"""Idle runtime cutovers retire only their private Unix endpoint namespace."""
from __future__ import annotations

import os
import json
import sqlite3
import sys
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest import mock

from admission import admit_execution
from hey_my_buddy.blackboard.store import backup
from hey_my_buddy.blackboard.store.db import SCHEMA_VERSION
from hey_my_buddy.errors import BoardError
from hey_my_buddy.install import launcher, upgrade
from hey_my_buddy.protocol.contracts import CONTRACT_VERSION
from support import BoardTestCase


@unittest.skipIf(os.name == 'nt', 'Windows has no filesystem endpoint namespace')
class UpgradeIpcTests(BoardTestCase):
    def setUp(self):
        super().setUp()
        board = self.board()
        board.store.db.close()
        self.state = board.directory
        self.root = self.directory / 'runtimes'
        self.previous, self.target = self.root / ('a' * 32), self.root / ('b' * 32)
        self.previous.mkdir(parents=True)
        self.ipc = self.state / 'ipc'
        self.ipc.mkdir(mode=0o700)
        (self.ipc / '.gate.marker').write_bytes(b'previous format')
        self.original_inode = self.ipc.stat().st_ino
        self.keep = self.state / 'keep.txt'
        self.keep.write_bytes(b'unrelated state')
        self.health = {'serviceId': 'owner', 'pid': 123, 'maxConcurrent': 1, 'waitCapacity': 32,
                       'runtimeStable': True, 'schemaVersion': SCHEMA_VERSION}
        self.enterContext(mock.patch.dict(os.environ, {'HMB_RUNTIME_ROOT': str(self.root)}))
        self.enterContext(mock.patch.object(upgrade.runtime, 'is_ready', return_value=True))
        self.enterContext(mock.patch.object(upgrade.runtime, 'read_ready', return_value={'sourceCommit': None}))
        self.enterContext(mock.patch.object(upgrade.runtime, 'materialize', return_value={'runtimeDir': str(self.target)}))
        self.enterContext(mock.patch.object(upgrade, 'detach'))
        self.enterContext(mock.patch.object(upgrade, 'retain_runtimes', return_value={'complete': True}))
        self.probe = self.enterContext(mock.patch.object(upgrade, 'probe', return_value=self.health))
        rebuild = upgrade._rebuild_ipc_root

        def fenced_rebuild(state):
            self.assertEqual(state, self.state)
            for name in ('control-start.lock', 'control-daemon.lock', 'board-owner.lock'):
                with self.assertRaises(BoardError) as busy:
                    with upgrade.file_lock(state / name):
                        self.fail(f'IPC rebuild ran without {name}')
                self.assertEqual(busy.exception.code, 'UPGRADE_BUSY')
            rebuild(state)

        self.rebuild = self.enterContext(mock.patch.object(upgrade, '_rebuild_ipc_root', side_effect=fenced_rebuild))

    def cutover(self, *, contract_changed, fail_target=False, admit_after_start=False):
        contract = '0.57.0' if contract_changed else CONTRACT_VERSION
        launcher.write_private(self.state / 'control.json', {
            'runtimeIdentity': 'runtime:' + self.previous.name, 'serviceId': 'owner', 'pid': 123,
            'contractVersion': contract})

        def start(state, target):
            if contract_changed:
                self.assertFalse(self.ipc.exists(), 'each version must start with a new namespace')
                self.ipc.mkdir(mode=0o700)
                (self.ipc / '.gate.marker').write_bytes(target.name.encode())
            else:
                self.assertEqual(self.ipc.stat().st_ino, self.original_inode)
                self.assertEqual((self.ipc / '.gate.marker').read_bytes(), b'previous format')
            if target == self.target and fail_target:
                if admit_after_start:
                    self.admit_unfinished_work()
                    (self.state / 'control.json').unlink()
                    self.probe.return_value = None
                raise BoardError('UPGRADE_START_FAILED', 'target failed after creating its namespace')
            return {**self.health, 'runtimeContentId': target.name}

        with mock.patch.object(upgrade, 'start', side_effect=start) as started:
            result = upgrade.upgrade(self.state)
        self.assertEqual(self.keep.read_bytes(), b'unrelated state')
        self.assertEqual(started.call_args_list, [mock.call(self.state, self.target)] +
                         ([mock.call(self.state, self.previous)] if fail_target else []))
        if fail_target:
            self.assertEqual(result['error']['code'], 'UPGRADE_ROLLED_BACK', result)
        else:
            self.assertTrue(result['upgraded'], result)
        return result

    def admit_unfinished_work(self):
        # Seed post-start facts with the normal writers outside admission's
        # journal fence. Recovery must protect persisted work even after a crash.
        marker = self.state / 'upgrade.json'
        journal = upgrade._read_json(marker) if marker.exists() else None
        if journal is not None:
            marker.unlink()
        try:
            board = self.board()
            board.submit_execution(requestId='after-target-start', task='retained work',
                cwd=str(self.workdir()), adapter='command', argv=[sys.executable, '-c', 'pass'])
            claim = admit_execution(board.client(), 'n' * 32)['claim']
            self.assertIsNotNone(claim)
            self.busy_task = claim['task']['taskId']
            self.busy_attempt = claim['attempt']['attemptId']
            board.store.db.close()
        finally:
            if journal is not None:
                upgrade.write_journal(marker, journal)

    def unfinished_work(self):
        with closing(sqlite3.connect((self.state / 'board.sqlite3').as_uri() + '?mode=ro', uri=True)) as db:
            return db.execute('SELECT t.state,a.execution_state,a.shutdown_confirmed FROM tasks t '
                              'JOIN attempts a ON a.task_id=t.task_id WHERE a.attempt_id=?',
                              (self.busy_attempt,)).fetchone()

    def test_changed_contract_rebuilds_before_starting_target(self):
        self.cutover(contract_changed=True)
        self.rebuild.assert_called_once_with(self.state)

    def test_failed_target_rebuilds_again_before_starting_previous(self):
        self.cutover(contract_changed=True, fail_target=True)
        self.assertEqual(self.rebuild.call_args_list, [mock.call(self.state), mock.call(self.state)])

    def test_rollback_cannot_restore_over_work_admitted_by_a_lost_target(self):
        with mock.patch.object(upgrade, 'restore', wraps=upgrade.restore) as restore, \
             self.assertRaises(BoardError) as refused:
            self.cutover(contract_changed=True, fail_target=True, admit_after_start=True)
        self.assertEqual(refused.exception.code, 'UPGRADE_RECOVERY_REQUIRED')
        self.assertEqual(refused.exception.details['recoveryReason']['code'], 'UPGRADE_NOT_IDLE')
        restore.assert_not_called()
        self.rebuild.assert_called_once_with(self.state)  # Only the forward cutover.
        self.assertIsNotNone(self.unfinished_work())
        self.assertEqual((self.ipc / '.gate.marker').read_bytes(), self.target.name.encode())
        journal = json.loads((self.state / 'upgrade.json').read_text())
        self.assertEqual(journal['phase'], 'recovery-required')
        self.assertTrue((self.state / 'backups/current').is_dir())

    def test_patch_install_keeps_the_namespace(self):
        self.cutover(contract_changed=False)
        self.rebuild.assert_not_called()

    def test_patch_rollback_keeps_the_namespace(self):
        self.cutover(contract_changed=False, fail_target=True)
        self.rebuild.assert_not_called()

    def prepare_recovery(self, *, unfinished=False):
        before = upgrade.idle_snapshot(self.state)
        saved = backup.create(self.state, runtime_identity=None, plugin_commit=None,
                              contract_version='0.57.0')
        if unfinished:
            self.admit_unfinished_work()
        upgrade.write_journal(self.state / 'upgrade.json', {
            'phase': 'prepared', 'contractChanged': True, 'previous': str(self.previous),
            'target': str(self.target), 'before': before, 'backup': saved})
        self.probe.return_value = None  # Neither runtime is serving; idle must still be proved.

    def test_recovery_without_a_service_proves_idle_and_rebuilds(self):
        self.prepare_recovery()

        def start(state, target):
            self.assertFalse(self.ipc.exists())
            return {**self.health, 'runtimeContentId': target.name}

        with upgrade.file_lock(self.state / 'upgrade.lock'), upgrade.file_lock(self.state / 'control-start.lock'), \
             mock.patch.object(upgrade, 'start', side_effect=start) as started:
            result = upgrade.recover(self.state, self.root)
        self.assertTrue(result['recoveredJournal'])
        self.rebuild.assert_called_once_with(self.state)
        started.assert_called_once_with(self.state, self.previous)
        self.assertEqual(self.keep.read_bytes(), b'unrelated state')

    def test_recovery_cannot_restore_or_rebuild_over_the_nonidle_board(self):
        self.prepare_recovery(unfinished=True)
        before = self.unfinished_work()
        self.assertIsNotNone(before)
        with upgrade.file_lock(self.state / 'upgrade.lock'), upgrade.file_lock(self.state / 'control-start.lock'), \
             mock.patch.object(upgrade, 'restore', wraps=upgrade.restore) as restore, \
             mock.patch.object(upgrade, 'start') as started, self.assertRaises(BoardError) as refused:
            upgrade.recover(self.state, self.root)
        self.assertEqual(refused.exception.code, 'UPGRADE_NOT_IDLE')
        self.assertIn(self.busy_attempt, refused.exception.details['unresolvedAttempts'])
        restore.assert_not_called()
        self.rebuild.assert_not_called()
        started.assert_not_called()
        self.assertEqual(self.unfinished_work(), before)
        self.assertEqual((self.ipc / '.gate.marker').read_bytes(), b'previous format')
        self.assertTrue((self.state / 'upgrade.json').exists())


class IpcRemovalBoundaryTests(BoardTestCase):
    def test_windows_has_no_ipc_root_to_rebuild(self):
        with mock.patch.object(upgrade, 'os', SimpleNamespace(name='nt')), \
             mock.patch.object(upgrade.shutil, 'rmtree') as remove:
            upgrade._rebuild_ipc_root(self.directory)
        remove.assert_not_called()

    @unittest.skipIf(os.name == 'nt', 'POSIX private directories')
    def test_refuses_linked_nonprivate_and_nondirectory_roots(self):
        outside = self.directory / 'outside'
        outside.mkdir()
        sentinel = outside / 'keep.txt'
        sentinel.write_bytes(b'keep')
        root = self.directory / 'ipc'
        root.symlink_to(outside, target_is_directory=True)
        with self.assertRaises(BoardError):
            upgrade._rebuild_ipc_root(self.directory)
        root.unlink()
        root.write_bytes(b'ordinary file')
        with self.assertRaises(BoardError):
            upgrade._rebuild_ipc_root(self.directory)
        root.unlink()
        root.mkdir(mode=0o755)
        with self.assertRaises(BoardError):
            upgrade._rebuild_ipc_root(self.directory)
        self.assertEqual(sentinel.read_bytes(), b'keep')

    @unittest.skipIf(os.name == 'nt', 'POSIX directory descriptors')
    def test_removes_only_ipc_without_following_links_inside_it(self):
        root = self.directory / 'ipc'
        root.mkdir(mode=0o700)
        outside = self.directory / 'outside'
        outside.mkdir()
        (outside / 'keep.txt').write_bytes(b'keep')
        (root / 'linked').symlink_to(outside, target_is_directory=True)
        (root / '.gate').write_bytes(b'opaque')
        upgrade._rebuild_ipc_root(self.directory)
        self.assertFalse(root.exists())
        self.assertEqual((outside / 'keep.txt').read_bytes(), b'keep')
        upgrade._rebuild_ipc_root(self.directory)  # A previous failed start may leave no root.
