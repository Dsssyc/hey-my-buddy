"""Death is established by full identity; receipt and native recovery share settlement."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from admission import admit_execution
from support import BoardTestCase, InProcessBoard
from hey_my_buddy.blackboard.tasks import interruption
from hey_my_buddy.errors import BoardError
from hey_my_buddy.protocol import process_identity as processes
from hey_my_buddy.protocol.execution import fsync_json, RECEIPT

IDENTITY = {'bootId': 'macos:00000000-0000-0000-0000-000000000001', 'pid': 123,
            'startedAt': 123250000, 'processGroupId': 123, 'jobName': None}


class InterruptionRecoveryTests(BoardTestCase):
    def claimed(self, *, identity=True):
        self.case_index = getattr(self, 'case_index', 0) + 1
        board = InProcessBoard(self.directory / f'case-{self.case_index}')
        self._stack.append(board)
        client = board.client()
        task = client.submit(requestId='interruption', task='work', adapter='command',
                             cwd=str(self.workdir()), argv=['echo', 'work'])['task']
        claim = admit_execution(client, 'n' * 32)['claim']
        actor = {'attemptId': claim['attempt']['attemptId'], 'generation': claim['attempt']['generation'],
                 'nonce': 'n' * 32}
        if identity:
            board.call('execution_progress', {**actor, 'processIdentity': IDENTITY})
            journal = self.journal(board, actor['attemptId'])
            Path(journal['path']).parent.mkdir(parents=True, exist_ok=True)
            processes.NativeProcessJournal(Path(journal['path']), journal['binding'])
        return board, client, task, claim, actor

    @staticmethod
    def journal(board, attempt_id):
        with board.store.db.read() as connection:
            attempt = dict(board.store._attempt_row(connection, attempt_id))
        return {'path': str(processes.native_process_journal_path(interruption._directory(board.store, attempt), IDENTITY)),
                'binding': {'taskId': attempt['task_id'], 'attemptId': attempt_id, 'generation': attempt['generation']}}

    def test_identity_is_immutable_and_nonce_generation_are_authenticated(self):
        board, _, _, _, actor = self.claimed()
        board.call('execution_progress', {**actor, 'processIdentity': IDENTITY})
        for change, code in (({'nonce': 'wrong'}, 'UNAUTHORIZED'), ({'generation': 2}, 'STALE_GENERATION'),
                             ({'processIdentity': {**IDENTITY, 'pid': 456, 'processGroupId': 456}}, 'CONFLICT')):
            with self.subTest(change=change), self.assertRaises(BoardError) as caught:
                board.call('execution_progress', {**actor, 'processIdentity': IDENTITY, **change})
            self.assertEqual(caught.exception.code, code)
        with board.store.db.read() as connection:
            self.assertEqual(connection.execute('SELECT COUNT(*) FROM attempt_process_identities').fetchone()[0], 1)
            self.assertEqual(interruption.read_identity(connection, actor['attemptId']), IDENTITY)

    def test_boot_changed_or_absent_execution_settles_and_releases_capacity(self):
        for observed in ('boot-changed', 'identity-absent'):
            board, client, task, _, actor = self.claimed()
            with self.subTest(observed=observed), patch.object(processes, 'inspect_process_identity', return_value=observed):
                self.assertEqual(len(board.store.recover_executions()), 1)
            view = client.get(runId=task['taskId'])
            self.assertEqual(view['terminationReason'], 'interrupted')
            self.assertTrue(view['shutdownConfirmed'])
            self.assertEqual(view['interruptionEvidence']['kind'], observed)
            self.assertEqual(board.store.resource_claims(), [])

    def test_live_and_unknown_identity_keep_capacity_and_no_receipt_handoff(self):
        for observed in ('alive', 'unknown'):
            board, _, _, _, _ = self.claimed()
            with self.subTest(observed=observed), patch.object(processes, 'inspect_process_identity', return_value=observed), \
                    patch('hey_my_buddy.protocol.execution.read_execution_receipt') as read:
                self.assertEqual(board.store.recover_executions(), [])
                read.assert_not_called()
            self.assertEqual(board.store.capacity_report()['totalActive'], 1)

    def test_startup_keeps_a_live_execution_uncertain_and_accepts_its_result(self):
        board, client, task, _, actor = self.claimed()
        with patch.object(processes, 'inspect_process_identity', return_value='alive'):
            self.assertEqual(board.store.reconcile_startup()['uncertain'], 1)
        self.assertEqual(client.get(runId=task['taskId'])['attemptState'], 'uncertain')
        board.call('execution_result', {**actor, 'status': 'ok', 'result': {'finalText': 'done'},
                                       'shutdownConfirmed': True})
        self.assertEqual(client.get(runId=task['taskId'])['state'], 'completed')

    def test_receipt_is_used_before_native_journal_and_commits_through_result(self):
        board, client, task, claim, actor = self.claimed()
        with board.store.db.read() as connection:
            attempt = dict(board.store._attempt_row(connection, actor['attemptId']))
        root = interruption._directory(board.store, attempt)
        receipt = {'taskId': task['taskId'], 'attemptId': actor['attemptId'], 'generation': actor['generation'],
                   'commandId': 'receipt-first', 'createdAt': board.store.now(),
                   'report': {'status': 'ok', 'result': {'finalText': 'durable'}, 'shutdownConfirmed': True}}
        fsync_json(root / RECEIPT, receipt)
        with patch.object(processes, 'inspect_process_identity', return_value='identity-absent'), \
                patch.object(processes, 'read_native_process_journal') as journal:
            response = board.store.recover_executions(actor['attemptId'])
            self.assertEqual(len(response), 1)
            journal.assert_not_called()
        self.assertEqual(client.result(runId=task['taskId'])['result'], {'finalText': 'durable'})
        with board.store.db.read() as connection:
            self.assertEqual(connection.execute("SELECT kind FROM commands WHERE command_id='receipt-first'").fetchone()[0],
                             'execution.result')
        self.assertEqual(board.store.recover_executions(), [])

    def test_no_receipt_preserves_the_persisted_stop_reason(self):
        for initiator, expected in (('console', 'console-stop'), ('service', 'service-stop'),
                                    ('unknown', 'unknown-stop')):
            board, client, task, _, actor = self.claimed()
            client.cancel(runId=task['taskId'])
            with board.store.db.write() as connection:
                connection.execute('UPDATE attempts SET stop_initiator=? WHERE attempt_id=?', (initiator, actor['attemptId']))
            with patch.object(processes, 'inspect_process_identity', return_value='identity-absent'):
                board.store.recover_executions()
            view = client.get(runId=task['taskId'])
            self.assertEqual(view['terminationReason'], expected)
            self.assertEqual(view['state'], 'cancelled')
            self.assertTrue(view['shutdownConfirmed'])

    def test_missing_identity_cannot_be_guessed_from_elapsed_time(self):
        board, _, _, _, _ = self.claimed(identity=False)
        self.assertEqual(board.store.recover_executions(), [])
        self.assertEqual(board.store.capacity_report()['totalActive'], 1)

    def test_missing_journal_settles_without_claiming_native_shutdown(self):
        board, client, task, _, actor = self.claimed()
        Path(self.journal(board, actor['attemptId'])['path']).unlink()
        with patch.object(processes, 'inspect_process_identity', return_value='identity-absent'):
            self.assertEqual(len(board.store.recover_executions()), 1)
        view = client.get(runId=task['taskId'])
        self.assertFalse(view['shutdownConfirmed'])
        self.assertEqual(view['terminationReason'], 'interrupted')
        self.assertTrue(board.store.resource_claims())

    def test_absent_execution_can_finish_an_earlier_unconfirmed_receipt(self):
        board, client, task, _, actor = self.claimed()
        board.call('execution_result', {**actor, 'status': 'failed', 'result': {'native': 'preserved'},
                                       'shutdownConfirmed': False})
        self.assertEqual(client.get(runId=task['taskId'])['attemptState'], 'uncertain')
        with patch.object(processes, 'inspect_process_identity', return_value='identity-absent'):
            self.assertEqual(len(board.store.recover_executions()), 1)
        final = client.get(runId=task['taskId'])
        self.assertEqual(final['attemptState'], 'finished')
        self.assertTrue(final['shutdownConfirmed'])
        self.assertEqual(final['terminationReason'], 'interrupted')
        self.assertEqual(client.result(runId=task['taskId'])['result']['previousReceipt']['result'],
                         {'native': 'preserved'})
        self.assertEqual(board.store.resource_claims(), [])
