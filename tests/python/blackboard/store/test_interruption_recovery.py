"""Stop proof, atomic settlement, immutable identities and legacy conversion."""
from __future__ import annotations

from unittest.mock import patch

from support import BoardTestCase, InProcessBoard
from hey_my_buddy.errors import BoardError
from hey_my_buddy.blackboard.tasks import interruption
from hey_my_buddy.cli.cli_views import task_brief


IDENTITY = {'bootId': 'macos:00000000-0000-0000-0000-000000000001', 'pid': 123, 'startedAt': 123.25,
            'processGroupId': 123, 'jobName': None,
            'worker': {'bootId': 'macos:00000000-0000-0000-0000-000000000001', 'pid': 122, 'startedAt': 120.5}}


class InterruptionRecoveryTests(BoardTestCase):
    def claimed(self, *, identity=True):
        self.case_index = getattr(self, 'case_index', 0) + 1
        board = InProcessBoard(self.directory / f'case-{self.case_index}')
        self._stack.append(board)
        client = board.client()
        task = client.submit(requestId='interruption', task='work', adapter='command',
                             cwd=str(self.workdir()), argv=['echo', 'work'])['task']
        client.register_worker('original', adapter='command', capabilities=['command'])
        claim = client.claim('original', 'claim-interruption', 'n' * 32, worker_instance='instance-one')['claim']
        actor = {'workerId': 'original', 'attemptId': claim['attempt']['attemptId'],
                 'generation': claim['attempt']['generation'], 'nonce': 'n' * 32}
        if identity:
            board.call('worker_progress', {**actor, 'processIdentity': IDENTITY})
        return board, client, task, claim, actor

    def test_identity_is_normalized_immutable_and_generation_authenticated(self):
        board, _, _, claim, actor = self.claimed()
        with board.store.db.read() as connection:
            self.assertEqual(interruption.read_identity(connection, actor['attemptId']), IDENTITY)
            self.assertEqual(connection.execute('SELECT root_started_at FROM attempt_process_identities').fetchone()[0], 123.25)
        board.call('worker_progress', {**actor, 'processIdentity': IDENTITY})
        for change, code in (({'nonce': 'wrong'}, 'UNAUTHORIZED'), ({'generation': 2}, 'STALE_GENERATION'),
                             ({'processIdentity': {**IDENTITY, 'pid': 456, 'processGroupId': 456}}, 'CONFLICT')):
            with self.subTest(change=change), self.assertRaises(BoardError) as caught:
                board.call('worker_progress', {**actor, 'processIdentity': IDENTITY, **change})
            self.assertEqual(caught.exception.code, code)

    def test_boot_and_absent_identity_settle_and_release_capacity(self):
        for reason in ('boot-changed', 'identity-absent'):
            with self.subTest(reason=reason):
                board, client, task, claim, actor = self.claimed()
                with patch.object(interruption.processes, 'inspect_process_identity', return_value=reason):
                    summary = board.store.reconcile_startup()
                self.assertEqual(summary['interrupted'], 1)
                view = board.call('task_get', {'runId': task['taskId']})['task']
                self.assertEqual(view['state'], 'failed')
                self.assertEqual(view['terminationReason'], 'interrupted')
                self.assertEqual(view['interruptionEvidence']['kind'], reason)
                self.assertEqual(task_brief(view)['interruptionEvidence']['kind'], reason)
                self.assertTrue(view['shutdownConfirmed'])
                from hey_my_buddy.cli.blocking import await_run
                operations = []
                def service(method, params, _state):
                    operations.append(method)
                    if method == 'status':
                        return board.call('task_get', params)['task']
                    if method == 'result':
                        return board.call('task_result', params)
                    self.fail('A stopped interrupted task must not wait or mutate: ' + method)
                waited = await_run({'runId': task['taskId'], 'waitSeconds': 1}, board.directory, service=service)
                self.assertEqual(waited['terminationReason'], 'interrupted')
                self.assertEqual(waited['interruptionEvidence']['kind'], reason)
                self.assertFalse(waited['ok'])
                self.assertEqual(operations, ['status', 'result'])
                self.assertEqual(board.store.resource_claims(), [])
                with board.store.db.read() as connection:
                    events = connection.execute("SELECT kind FROM events WHERE attempt_id=?", (actor['attemptId'],)).fetchall()
                    self.assertIn('task.failed', [row[0] for row in events])
                    self.assertEqual(connection.execute('SELECT execution_state FROM attempts').fetchone()[0], 'finished')

    def test_unknown_or_live_identity_retains_claims_without_retry(self):
        for observation in ('unknown', 'alive'):
            with self.subTest(observation=observation):
                board, client, task, claim, actor = self.claimed()
                with patch.object(interruption.processes, 'inspect_process_identity', return_value=observation):
                    board.store.reconcile_startup()
                view = board.call('task_get', {'runId': task['taskId']})['task']
                self.assertEqual(view['selectedAttempt']['executionState'], 'uncertain')
                self.assertFalse(view['shutdownConfirmed'])
                self.assertEqual(len(board.store.resource_claims()), 1)
                self.assertEqual(view['attemptGeneration'], 1)

    def test_worker_recovery_requires_owner_absence_and_independent_stop_proof(self):
        board, client, task, _, actor = self.claimed()
        client.register_worker('replacement', adapter='command', capabilities=['command'])
        with patch.object(interruption.processes, 'inspect_process_identity', return_value='alive'):
            board.store.reconcile_startup()
            for owner in (True, None):
                with patch.object(interruption.processes, 'worker_identity_alive', return_value=owner):
                    self.assertEqual(board.call('worker_recover', {'workerId': 'replacement'})['candidates'], [])
            with patch.object(interruption.processes, 'worker_identity_alive', return_value=False):
                candidates = board.call('worker_recover', {'workerId': 'replacement'})['candidates']
                self.assertEqual(candidates, [{'attemptId': actor['attemptId'], 'processIdentity': IDENTITY}])
                refused = board.call('worker_recover', {'workerId': 'replacement', 'attemptId': actor['attemptId'], 'evidence': 'worker-stopped'})
                self.assertFalse(refused['settled'])
        with patch.object(interruption.processes, 'inspect_process_identity', return_value='identity-absent'), \
             patch.object(interruption.processes, 'worker_identity_alive', return_value=False):
            settled = board.call('worker_recover', {'workerId': 'replacement', 'attemptId': actor['attemptId'], 'evidence': 'worker-stopped'})
        self.assertTrue(settled['settled'])
        view = board.call('task_get', {'runId': task['taskId']})['task']
        self.assertEqual(view['interruptionEvidence']['kind'], 'worker-stopped')

    def test_periodic_sweep_proves_stop_without_a_service_restart(self):
        board, client, task, _, actor = self.claimed()
        with board.store.db.write() as connection:
            connection.execute("UPDATE attempts SET lease_expires_at='2000-01-01T00:00:00Z'")
        with patch.object(interruption.processes, 'inspect_process_identity', return_value='identity-absent'):
            self.assertEqual(board.store.mark_expired_leases(), 1)
        self.assertEqual(board.call('task_get', {'runId': task['taskId']})['task']['terminationReason'], 'interrupted')

    def test_missing_current_identity_is_never_guessed_from_a_timestamp(self):
        board, client, task, _, actor = self.claimed(identity=False)
        board.store.reconcile_startup()
        self.assertFalse(board.call('task_get', {'runId': task['taskId']})['task']['shutdownConfirmed'])

    def test_surviving_owner_keeps_collection_even_if_child_completed_during_sleep(self):
        board, client, task, _, actor = self.claimed()
        with patch.object(interruption.processes, 'worker_identity_alive', return_value=True), \
             patch.object(interruption.processes, 'inspect_process_identity', return_value='identity-absent'):
            board.store.reconcile_startup()
        self.assertFalse(board.call('task_get', {'runId': task['taskId']})['task']['shutdownConfirmed'])

    def test_explicit_legacy_start_only_settles_before_this_boot(self):
        for before_boot in (False, True):
            with self.subTest(before_boot=before_boot):
                board, client, task, _, actor = self.claimed(identity=False)
                with board.store.db.write() as connection:
                    connection.execute('INSERT INTO attempt_legacy_starts VALUES(?,?)',
                                       (actor['attemptId'], '2026-01-01T00:00:00Z'))
                with patch.object(interruption.processes, 'boot_started_at', return_value=2_000_000_000 if before_boot else 1):
                    board.store.reconcile_startup()
                self.assertEqual(board.call('task_get', {'runId': task['taskId']})['task']['shutdownConfirmed'], before_boot)
