"""Outer controller exit alone never releases a surviving native harness group."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from support import BoardTestCase
from blackboard.store import test_interruption_recovery as helpers
from hey_my_buddy.blackboard.tasks import interruption
from hey_my_buddy.errors import BoardError
from hey_my_buddy.protocol import process_identity as processes


class NativeTreeRecoveryTests(BoardTestCase):
    claimed = helpers.InterruptionRecoveryTests.claimed

    def test_confirmed_premodel_retry_appends_root_without_changing_its_history(self):
        board, client, task, claim, actor = self.claimed()
        second = {**helpers.IDENTITY, 'pid': 456, 'processGroupId': 456, 'startedAt': 130.25}
        board.call('worker_progress', {**actor, 'processIdentity': second, 'previousProcessStopped': True})
        board.call('worker_progress', {**actor, 'processIdentity': helpers.IDENTITY})
        with board.store.db.read() as connection:
            rows = [tuple(row) for row in connection.execute(
                'SELECT ordinal,root_pid,shutdown_confirmed FROM attempt_process_identities ORDER BY ordinal')]
            self.assertEqual(rows, [(0, 123, 1), (1, 456, 0)])
            self.assertEqual(interruption.read_identity(connection, actor['attemptId']), second)

    def harness_attempt(self):
        board, client, task, claim, actor = self.claimed()
        with board.store.db.write() as connection:
            connection.execute("UPDATE attempts SET adapter='dsh' WHERE attempt_id=?", (actor['attemptId'],))
            attempt = dict(board.store._attempt_row(connection, actor['attemptId']))
        journal = interruption._native_journal(board.store, attempt, helpers.IDENTITY)
        Path(journal['path']).parent.mkdir(parents=True, exist_ok=True)
        return board, client, task, claim, actor, journal

    def test_missing_or_pending_native_journal_keeps_unknown(self):
        for pending in (False, True):
            with self.subTest(pending=pending):
                board, client, task, claim, actor, journal = self.harness_attempt()
                if pending:
                    owner = processes.NativeProcessJournal(Path(journal['path']), journal['binding'])
                    owner.begin()
                with patch.object(processes, 'worker_identity_alive', return_value=False), \
                     patch.object(processes, 'inspect_process_identity', return_value='identity-absent'):
                    board.store.reconcile_startup()
                    candidates = board.call('worker_recover', {'workerId': 'original'})['candidates']
                self.assertEqual(candidates[0]['nativeJournal'], journal)
                self.assertFalse(client.get(runId=task['taskId'])['shutdownConfirmed'])
                self.assertEqual(len(board.store.resource_claims()), 1)

    def test_native_group_is_imported_and_must_be_gone_before_settlement(self):
        board, client, task, claim, actor, journal = self.harness_attempt()
        native = {**helpers.IDENTITY, 'pid': 223, 'processGroupId': 223}
        owner = processes.NativeProcessJournal(Path(journal['path']), journal['binding'])
        owner.state['identities'] = [native]
        owner._publish()
        def inspection(record):
            return 'alive' if record['pid'] == 223 else 'identity-absent'
        with patch.object(processes, 'worker_identity_alive', return_value=False), \
             patch.object(processes, 'inspect_process_identity', side_effect=inspection):
            board.store.reconcile_startup()
            self.assertFalse(client.get(runId=task['taskId'])['shutdownConfirmed'])
            with board.store.db.read() as connection:
                self.assertEqual(connection.execute('SELECT root_pid FROM attempt_native_process_identities').fetchone()[0], 223)
        with patch.object(processes, 'worker_identity_alive', return_value=False), \
             patch.object(processes, 'inspect_process_identity', return_value='identity-absent'):
            response = board.call('worker_recover', {'workerId': 'original', 'attemptId': actor['attemptId'], 'evidence': 'worker-stopped'})
        self.assertTrue(response['settled'])
        self.assertEqual(client.get(runId=task['taskId'])['interruptionEvidence']['kind'], 'worker-stopped')

    def test_failed_seal_remains_retryable_and_fences_results_before_git(self):
        board, client, task, claim, actor = self.claimed()
        with patch.object(processes, 'worker_identity_alive', return_value=False), \
             patch.object(processes, 'inspect_process_identity', return_value='identity-absent'), \
             patch.object(board.store.workflow, 'seal_interrupted_turn', side_effect=BoardError('GIT_FAILED', 'temporary checkout failure')):
            board.store.reconcile_startup()
        self.assertFalse(client.get(runId=task['taskId'])['shutdownConfirmed'])
        with self.assertRaises(BoardError) as caught:
            board.call('worker_result', {**actor, 'status': 'ok', 'result': {}, 'shutdownConfirmed': True})
        self.assertEqual(caught.exception.code, 'ATTEMPT_FINISHED')
        with patch.object(processes, 'worker_identity_alive', return_value=False), \
             patch.object(processes, 'inspect_process_identity', return_value='identity-absent'):
            self.assertEqual(board.store.recover_interrupted_attempts(), 1)
        self.assertTrue(client.get(runId=task['taskId'])['shutdownConfirmed'])
        with board.store.db.read() as connection:
            self.assertEqual(connection.execute('SELECT state,seal_error FROM attempt_interruptions').fetchone()[:], ('settled', None))
