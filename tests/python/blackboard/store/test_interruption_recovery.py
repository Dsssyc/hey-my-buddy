"""Death is established by full identity; receipt and native recovery share settlement."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import Mock, patch

from admission import admit_execution
from support import BoardTestCase, InProcessBoard
from hey_my_buddy.blackboard.tasks import interruption
from hey_my_buddy.blackboard.service.execution_scheduler import _ExecutionObservation
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
        task = board.submit_execution(requestId='interruption', task='work', adapter='command',
                             cwd=str(self.workdir()), argv=['echo', 'work'])['task']
        claim = admit_execution(client, 'n' * 32)['claim']
        actor = {'attemptId': claim['attempt']['attemptId'], 'generation': claim['attempt']['generation'],
                 'nonce': 'n' * 32}
        (board.directory / 'attempts' / task['taskId'] / actor['attemptId']).mkdir(parents=True)
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

    def test_normal_startup_waits_for_endpoint_without_unknown_or_reconnect_events(self):
        from hey_my_buddy.blackboard.service.execution_scheduler import ExecutionScheduler, _RecoveredExecution
        from hey_my_buddy.protocol.run_identity import RunIdentity
        for acknowledgement in ('during-missing-read', 'after-first-wait'):
            with self.subTest(acknowledgement=acknowledgement):
                board, client, task, claim, actor = self.claimed(identity=False)
                scheduler = ExecutionScheduler(board.service, live_registry=board.live_registry)
                binding = {'taskId': task['taskId'], 'attemptId': actor['attemptId'], 'generation': 1}
                with board.store.db.read() as db:
                    directory = interruption._directory(board.store, board.store._attempt_row(db, actor['attemptId']))
                location = _RecoveredExecution(directory, binding, RunIdentity(
                    task_id=task['taskId'], attempt_id=actor['attemptId'], generation=1, invocation_id='execution'))
                endpoint = Mock()
                endpoint.connect.side_effect = scheduler._stopping.set
                reads = []

                def publish_endpoint(*_args, **_kwargs):
                    view = client.get(runId=task['taskId'])
                    self.assertNotEqual(view['attemptState'], 'uncertain')
                    reads.append(view['attemptState'])
                    if len(reads) == 1:
                        self.assertFalse(board.store.execution_identity_confirmed(actor['attemptId']))
                        if acknowledgement == 'during-missing-read':
                            # Publication and its acknowledgement can finish after
                            # open reported absence but before that read returns.
                            board.call('execution_progress', {**actor, 'processIdentity': IDENTITY, 'phase': 'executing'})
                        return None
                    return endpoint

                with patch('hey_my_buddy.blackboard.service.execution_scheduler.read_execution_endpoint',
                           side_effect=publish_endpoint), patch(
                           'hey_my_buddy.blackboard.service.execution_scheduler.inspect_process_identity',
                           return_value='alive'), patch.object(
                           scheduler, '_watch_connection', return_value=Mock(error=None)):
                    scheduler._observed[actor['attemptId']] = _ExecutionObservation(
                        task['taskId'], scheduler._condition)
                    scheduler._monitor(claim, location, IDENTITY)
                self.assertEqual(len(reads), 2)
                if acknowledgement == 'after-first-wait':
                    self.assertEqual(client.get(runId=task['taskId'])['attemptState'], 'starting')
                    board.call('execution_progress', {**actor, 'processIdentity': IDENTITY, 'phase': 'executing'})
                view = client.get(runId=task['taskId'])
                self.assertEqual(view['attemptState'], 'executing')
                self.assertEqual(view['selectedAttempt']['ownership'], 'owned')
                self.assertEqual(view['selectedAttempt']['issues'], [])
                self.assertIsNone(view['queueReason'])
                self.assertEqual(board.store.resource_claims()[0]['state'], 'held')
                with board.store.db.read() as db:
                    events = db.execute("SELECT kind FROM events WHERE attempt_id=? AND kind IN"
                        " ('attempt.uncertain','execution.unknown','execution.connected',"
                        " 'execution.issue','execution.issue_resolved')", (actor['attemptId'],)).fetchall()
                self.assertEqual(events, [])

    def test_normal_endpoint_close_after_completion_is_quiet_but_cleanup_failures_remain_visible(self):
        board, client, task, _, actor = self.claimed()
        board.call('execution_result', {**actor, 'status': 'ok', 'result': {}, 'shutdownConfirmed': True})
        head = board.store.head()
        board.store.execution_connection(actor['attemptId'], connected=False)
        board.store.record_execution_issue(actor['attemptId'], 'connection',
                                           BoardError('EXECUTION_UNKNOWN', 'The execution endpoint is unavailable'))
        self.assertEqual(board.store.head(), head)
        self.assertEqual(client.get(runId=task['taskId'])['selectedAttempt']['issues'], [])
        board.store.record_execution_issue(actor['attemptId'], 'endpoint-cleanup', OSError('cleanup failed'))
        self.assertEqual(client.get(runId=task['taskId'])['selectedAttempt']['issues'][0]['stage'], 'endpoint-cleanup')

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

    def test_authenticated_reconnection_restores_ownership_claims_and_waiting_reason_once(self):
        board, client, task, _, actor = self.claimed()
        with patch.object(processes, 'inspect_process_identity', return_value='alive'):
            board.store.reconcile_startup()
        self.assertEqual(board.store.resource_claims()[0]['state'], 'retained')
        board.store.execution_connection(actor['attemptId'], connected=True)
        view = client.get(runId=task['taskId'])
        self.assertEqual(view['attemptState'], 'executing')
        self.assertEqual(view['selectedAttempt']['ownership'], 'owned')
        self.assertIsNone(view['queueReason'])
        self.assertEqual(board.store.resource_claims()[0]['state'], 'held')
        head = board.store.head()
        board.store.execution_connection(actor['attemptId'], connected=True)
        self.assertEqual(board.store.head(), head)
        board.store.execution_connection(actor['attemptId'], connected=False)
        view = client.get(runId=task['taskId'])
        self.assertEqual(view['attemptState'], 'uncertain')
        self.assertEqual(view['selectedAttempt']['ownership'], 'uncertain')
        self.assertEqual(board.store.resource_claims()[0]['state'], 'retained')
        board.call('execution_progress', {**actor, 'data': {'waitingReason':
                   {'code': 'router-input-unknown', 'params': {}}}})
        board.store.execution_connection(actor['attemptId'], connected=True)
        self.assertEqual(client.get(runId=task['taskId'])['queueReason'], 'router-input-unknown')

    def test_receipt_is_used_before_native_journal_and_commits_through_result(self):
        board, client, task, claim, actor = self.claimed()
        with board.store.db.read() as connection:
            attempt = dict(board.store._attempt_row(connection, actor['attemptId']))
        root = interruption._directory(board.store, attempt)
        receipt = {'taskId': task['taskId'], 'attemptId': actor['attemptId'], 'generation': actor['generation'],
                   'createdAt': board.store.now(),
                   'report': {'status': 'ok', 'result': {'finalText': 'durable'}, 'shutdownConfirmed': True}}
        fsync_json(root / RECEIPT, receipt)
        with patch.object(processes, 'inspect_process_identity', return_value='identity-absent'), \
                patch.object(processes, 'read_native_process_journal') as journal:
            response = board.store.recover_executions(actor['attemptId'])
            self.assertEqual(len(response), 1)
            journal.assert_not_called()
        self.assertEqual(client.result(runId=task['taskId'])['result'], {'finalText': 'durable'})
        with board.store.db.read() as connection:
            self.assertIsNone(connection.execute("SELECT kind FROM commands WHERE kind='execution.result'").fetchone())
        self.assertEqual(board.store.recover_executions(), [])

    def test_credentials_are_cleaned_by_service_after_complete_stop_on_each_exit_path(self):
        for exit_path in ('delivered', 'crashed', 'forced'):
            with self.subTest(exit_path=exit_path):
                board, client, task, _, actor = self.claimed()
                root = Path(self.journal(board, actor['attemptId'])['path']).parent
                credential = root / 'agent-credential.json'
                credential.write_text('private fixture')
                if exit_path == 'delivered':
                    board.call('execution_result', {**actor, 'status': 'ok', 'shutdownConfirmed': True})
                elif exit_path == 'forced':
                    client.cancel(runId=task['taskId'])
                    board.store.record_execution_deadline(actor['attemptId'], 'forced-stop')
                with patch.object(processes, 'inspect_process_identity', return_value='alive'):
                    board.store.recover_executions()
                self.assertTrue(credential.exists(), 'execution identity still exists')
                with patch.object(processes, 'inspect_process_identity', return_value='identity-absent'):
                    board.store.recover_executions()
                self.assertTrue(client.get(runId=task['taskId'])['shutdownConfirmed'])
                self.assertFalse(credential.exists())
                with board.store.db.read() as connection:
                    self.assertFalse(board.store._attempt_row(connection, actor['attemptId'])['cleanup_pending'])

    def test_native_stop_unknown_keeps_credentials_until_complete_proof_arrives(self):
        board, client, task, _, actor = self.claimed()
        root = Path(self.journal(board, actor['attemptId'])['path']).parent
        credential = root / 'agent-credential.json'
        credential.write_text('private fixture')
        startup = root / 'execution-start.json'
        fsync_json(startup, {'claim': {'agentCredential': 'private fixture'}})
        with patch.object(processes, 'inspect_process_identity', return_value='identity-absent'), \
                patch.object(interruption, '_stop_native', return_value=(False, None)):
            board.store.recover_executions()
        self.assertFalse(client.get(runId=task['taskId'])['shutdownConfirmed'])
        self.assertTrue(credential.exists())
        self.assertTrue(startup.exists())
        with board.store.db.read() as connection:
            self.assertTrue(board.store._attempt_row(connection, actor['attemptId'])['cleanup_pending'])
        with patch.object(processes, 'inspect_process_identity', return_value='identity-absent'):
            board.store.recover_executions()
        self.assertTrue(client.get(runId=task['taskId'])['shutdownConfirmed'])
        self.assertFalse(credential.exists())
        self.assertFalse(startup.exists())

    def test_credential_cleanup_failure_is_visible_and_retried_without_losing_receipt(self):
        board, client, task, _, actor = self.claimed()
        root = Path(self.journal(board, actor['attemptId'])['path']).parent
        credential = root / 'agent-credential.json'
        credential.write_text('private fixture')
        receipt = {'taskId': task['taskId'], 'attemptId': actor['attemptId'], 'generation': actor['generation'],
                   'createdAt': board.store.now(), 'report': {'status': 'ok', 'shutdownConfirmed': True}}
        fsync_json(root / RECEIPT, receipt)
        original = (root / RECEIPT).read_bytes()
        with patch.object(processes, 'inspect_process_identity', return_value='identity-absent'), \
                patch('hey_my_buddy.private_dirs.cleanup_attempt_credentials', side_effect=OSError('remove denied')):
            board.store.recover_executions()
        issues = client.get(runId=task['taskId'])['selectedAttempt']['issues']
        self.assertTrue(any(issue['stage'] == 'credential-cleanup' and 'remove denied' in issue['message']
                            for issue in issues), issues)
        self.assertTrue(credential.exists())
        self.assertEqual((root / RECEIPT).read_bytes(), original)
        with patch.object(processes, 'inspect_process_identity', return_value='identity-absent'):
            board.store.recover_executions()
        self.assertFalse(credential.exists())
        self.assertEqual((root / RECEIPT).read_bytes(), original)
        self.assertFalse(client.get(runId=task['taskId'])['selectedAttempt']['issues'])

    def test_failed_recovery_commit_retains_exact_receipt_and_credentials(self):
        board, client, task, _, actor = self.claimed()
        root = Path(self.journal(board, actor['attemptId'])['path']).parent
        credential = root / 'agent-credential.json'
        credential.write_text('private fixture')
        receipt = {'taskId': task['taskId'], 'attemptId': actor['attemptId'], 'generation': actor['generation'],
                   'createdAt': board.store.now(), 'report': {'status': 'ok', 'shutdownConfirmed': True}}
        fsync_json(root / RECEIPT, receipt)
        original = (root / RECEIPT).read_bytes()
        with patch.object(processes, 'inspect_process_identity', return_value='identity-absent'), \
                patch.object(board.store, 'execution_result', side_effect=RuntimeError('commit failed')):
            self.assertEqual(board.store.recover_executions(), [])
        self.assertEqual((root / RECEIPT).read_bytes(), original)
        self.assertTrue(credential.exists())
        self.assertTrue(any(issue['stage'] == 'recovery' for issue in
                            client.get(runId=task['taskId'])['selectedAttempt']['issues']))
        with patch.object(processes, 'inspect_process_identity', return_value='identity-absent'):
            self.assertEqual(len(board.store.recover_executions()), 1)
        self.assertEqual((root / RECEIPT).read_bytes(), original)
        self.assertFalse(credential.exists())

    def test_unknown_connection_logs_diagnostics_and_deduplicates_a_stable_issue(self):
        from hey_my_buddy.blackboard.service.execution_scheduler import ExecutionScheduler
        board, client, task, _, actor = self.claimed()
        scheduler = ExecutionScheduler(board.service, live_registry=board.live_registry)
        with self.assertLogs(level='WARNING') as logs:
            scheduler._unknown(actor['attemptId'], BoardError('LIVE_UNAVAILABLE', 'connect failed',
                io_kind='permission_denied', raw_os_error=13, io_operation='connect', connectionId=1, cause='first'))
            head = board.store.head()
            scheduler._unknown(actor['attemptId'], BoardError('LIVE_UNAVAILABLE', 'connect failed again',
                io_kind='permission_denied', raw_os_error=13, io_operation='connect', connectionId=2, cause='second'))
        self.assertEqual(board.store.head(), head)
        issue = client.get(runId=task['taskId'])['selectedAttempt']['issues'][0]
        self.assertEqual(issue['code'], 'EXECUTION_UNKNOWN')
        self.assertEqual(issue['message'], 'The execution connection is unavailable')
        for field in ('io_kind', 'raw_os_error', 'io_operation', 'connectionId', 'first', 'second'):
            self.assertIn(field, '\n'.join(logs.output))
        self.assertFalse(client.get(runId=task['taskId'])['shutdownConfirmed'])

    def test_refused_receipt_is_never_replayed_and_settles_failed_with_released_capacity(self):
        from hey_my_buddy.buddy.runtime.receipts import deliver_receipt
        from hey_my_buddy.protocol.execution import DELIVERY
        board, client, task, _, actor = self.claimed()
        directory = Path(self.journal(board, actor['attemptId'])['path']).parent
        binding = {'taskId': task['taskId'], 'attemptId': actor['attemptId'], 'generation': actor['generation']}
        receipt = {**binding, 'createdAt': board.store.now(),
                   'report': {'status': 'invalid-status', 'shutdownConfirmed': True}}
        fsync_json(directory / RECEIPT, receipt)
        original = (directory / RECEIPT).read_bytes()
        self.assertEqual(board.store.capacity_report()['totalActive'], 1)
        self.assertFalse(deliver_receipt(client, receipt, directory, nonce=actor['nonce']))
        decision = json.loads((directory / DELIVERY).read_text())
        self.assertEqual(decision['error']['code'], 'INVALID_ARGUMENT')
        self.assertFalse(decision['accepted'])
        # A rejection records no stop proof. Until the root is absent it still
        # owns capacity; recovery must not replay its invalid report afterwards.
        with patch.object(processes, 'inspect_process_identity', return_value='alive'):
            self.assertEqual(board.store.recover_executions(), [])
        self.assertEqual(board.store.capacity_report()['totalActive'], 1)
        with patch.object(processes, 'inspect_process_identity', return_value='identity-absent'), \
             patch.object(board.store, 'execution_result', wraps=board.store.execution_result) as commit:
            self.assertEqual(len(board.store.recover_executions()), 1)
        self.assertEqual(commit.call_count, 1)
        self.assertEqual(commit.call_args.args[0]['status'], 'failed')
        view = client.get(runId=task['taskId'])
        self.assertEqual(view['state'], 'failed')
        self.assertEqual(view['selectedAttempt']['result']['result']['receiptRejection'], decision['error'])
        self.assertIn('INVALID_ARGUMENT', view['selectedAttempt']['result']['error'])
        self.assertIn(decision['error']['message'], view['selectedAttempt']['result']['error'])
        self.assertTrue(view['shutdownConfirmed'])
        self.assertEqual(board.store.capacity_report()['totalActive'], 0)
        self.assertEqual((directory / RECEIPT).read_bytes(), original)

    def test_unchanged_progress_notifies_observer_without_creating_an_event(self):
        board, _, _, _, actor = self.claimed()
        scheduler = Mock()
        board.service.execution_scheduler = scheduler
        head = board.store.head()
        response = board.call('execution_progress', {**actor, 'data': {'waitingReason': None}})
        self.assertTrue(response['recorded'])
        self.assertEqual(board.store.head(), head)
        scheduler.wake_attempt.assert_called_once_with(actor['attemptId'])

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

    def test_missing_spawn_intent_after_restart_proves_no_process_was_created(self):
        board, client, task, _, actor = self.claimed(identity=False)
        # A live creator may be between admission and intent publication.
        self.assertEqual(board.store.recover_executions(), [])
        self.assertEqual(board.store.capacity_report()['totalActive'], 1)
        self.assertEqual(board.store.reconcile_startup()['interrupted'], 1)
        view = client.get(runId=task['taskId'])
        self.assertEqual(view['attemptState'], 'finished')
        self.assertTrue(view['shutdownConfirmed'])
        self.assertEqual(board.store.capacity_report()['totalActive'], 0)
        event = [event for event in board.store.events_read({'limit': 200})['events']
                 if event['kind'] == 'attempt.released'][0]
        self.assertEqual(event['payload']['releaseEvidence'], 'processCreated=false')
        self.assertEqual(event['payload']['reason'],
                         {'code': 'execution-process-not-created', 'params': {}})

    def test_written_intent_without_identity_cannot_be_guessed_from_elapsed_time(self):
        board, _, task, _, actor = self.claimed(identity=False)
        fsync_json(board.directory / 'attempts' / task['taskId'] / actor['attemptId'] / 'spawn.intent',
                   {'taskId': task['taskId'], 'attemptId': actor['attemptId'], 'generation': 1})
        self.assertEqual(board.store.reconcile_startup()['interrupted'], 0)
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

    def assert_idempotently_confirmed(self, board, client, task, actor, original, *, state):
        self.assertEqual(len(board.store.recover_executions()), 1)
        final = client.get(runId=task['taskId'])
        self.assertEqual(final['state'], state)
        self.assertEqual(final['attemptState'], 'finished')
        self.assertTrue(final['shutdownConfirmed'])
        self.assertEqual(board.store.resource_claims(), [])
        payload = client.result(runId=task['taskId'])['selectedAttempt']['result']
        self.assertEqual(payload, {**original, 'shutdownConfirmed': True})
        self.assertIsNotNone(final['selectedAttempt']['shutdownEvidence'])
        head = board.store.head()
        self.assertEqual(board.store.recover_executions(), [])
        self.assertEqual(board.store.head(), head)

    def test_absent_execution_confirms_success_without_replacing_its_result(self):
        board, client, task, _, actor = self.claimed()
        report = {'status': 'ok', 'result': {'native': 'preserved', 'output': {'status': 'ok'}},
                  'shutdownConfirmed': False}
        board.call('execution_result', {**actor, **report})
        original = client.result(runId=task['taskId'])['selectedAttempt']['result']
        self.assertEqual(client.get(runId=task['taskId'])['state'], 'reconciliation-needed')
        with patch.object(processes, 'inspect_process_identity', return_value='identity-absent'):
            self.assert_idempotently_confirmed(board, client, task, actor, original, state='completed')
        replay = board.call('execution_result', {**actor, **report})
        self.assertTrue(replay['duplicate'])
        self.assertTrue(replay['attempt']['shutdownConfirmed'])

    def test_partial_native_journal_can_gain_proof_on_the_next_recovery(self):
        board, client, task, _, actor = self.claimed()
        journal = self.journal(board, actor['attemptId'])
        native = {**IDENTITY, 'pid': 456, 'processGroupId': 456}
        fsync_json(Path(journal['path']), {'binding': journal['binding'], 'pending': ['a' * 32],
                                          'identities': [native]})
        with patch.object(processes, 'inspect_process_identity', return_value='identity-absent'), \
                patch.object(processes, 'stop_orphan_process', return_value='orphan-stopped'):
            self.assertEqual(len(board.store.recover_executions()), 1)
            first = client.result(runId=task['taskId'])['selectedAttempt']['result']
            self.assertFalse(first['shutdownConfirmed'])
            self.assertTrue(board.store.resource_claims())
            fsync_json(Path(journal['path']), {'binding': journal['binding'], 'pending': [], 'identities': [native]})
            self.assert_idempotently_confirmed(board, client, task, actor, first, state='failed')
        with board.store.db.read() as db:
            stored = db.execute('SELECT evidence_kind FROM attempt_interruptions WHERE attempt_id=?',
                                (actor['attemptId'],)).fetchone()[0]
        self.assertEqual(stored, first['result']['interruptionEvidence']['kind'])
        self.assertEqual(client.get(runId=task['taskId'])['selectedAttempt']['shutdownEvidence']['kind'], 'orphan-stopped')

    def test_committed_unconfirmed_receipt_left_on_disk_is_not_resubmitted(self):
        board, client, task, _, actor = self.claimed()
        report = {'status': 'ok', 'result': {'native': 'receipt survives commit'}, 'shutdownConfirmed': False}
        board.call('execution_result', {**actor, **report})
        original = client.result(runId=task['taskId'])['selectedAttempt']['result']
        with board.store.db.read() as db:
            attempt = dict(board.store._attempt_row(db, actor['attemptId']))
            self.assertIsNone(db.execute("SELECT * FROM commands WHERE kind='execution.result'").fetchone())
        fsync_json(interruption._directory(board.store, attempt) / RECEIPT, {
            'taskId': task['taskId'], 'attemptId': actor['attemptId'], 'generation': actor['generation'],
            'createdAt': board.store.now(), 'report': report})
        with patch.object(processes, 'inspect_process_identity', return_value='identity-absent'):
            self.assert_idempotently_confirmed(board, client, task, actor, original, state='completed')
        with board.store.db.read() as db:
            self.assertIsNone(db.execute("SELECT * FROM commands WHERE kind='execution.result'").fetchone())

    def test_bad_receipt_is_visible_once_and_does_not_block_startup_or_later_attempts(self):
        cases = (
            ('json', '{bad json', 'JSONDecodeError'),
            ('shape', {'wrong': 'envelope'}, 'INVALID_ARGUMENT'),
            ('binding', {'bindingMismatch': True}, 'INVALID_ARGUMENT'),
            ('result', {'status': 'not-a-status'}, 'INVALID_ARGUMENT'),
            ('artifacts', {'status': 'ok', 'artifacts': [{'location': str(self.directory / 'missing')}],
                           'shutdownConfirmed': True}, 'ARTIFACT_MISSING'),
            ('limit', {'status': 'ok', 'artifacts': [{}] * 101}, 'INVALID_ARGUMENT'),
        )
        for name, bad, code in cases:
            with self.subTest(case=name):
                board, client, task, _, actor = self.claimed()
                good_task = board.submit_execution(requestId='later', task='later', adapter='command',
                    cwd=str(self.workdir(name + '-later')), argv=['echo', 'later'])['task']
                good = admit_execution(client, 'g' * 32)['claim']
                good_actor = {'attemptId': good['attempt']['attemptId'], 'generation': 1, 'nonce': 'g' * 32}
                board.call('execution_progress', {**good_actor, 'processIdentity': IDENTITY})
                with board.store.db.read() as db:
                    attempt = dict(board.store._attempt_row(db, actor['attemptId']))
                    later = dict(board.store._attempt_row(db, good_actor['attemptId']))
                envelope = {'taskId': task['taskId'], 'attemptId': actor['attemptId'], 'generation': 1,
                            'createdAt': board.store.now(),
                            'report': {'status': 'ok', 'result': {'native': name}, 'shutdownConfirmed': True}}
                if name == 'shape':
                    envelope = bad
                elif name == 'binding':
                    envelope['attemptId'] = 'different-attempt'
                elif name != 'json':
                    envelope['report'] = bad
                path = interruption._directory(board.store, attempt) / RECEIPT
                fsync_json(path, envelope)
                if name == 'json':
                    path.write_text(bad, encoding="utf-8", newline="\n")
                later_root = interruption._directory(board.store, later)
                later_root.mkdir(parents=True)
                fsync_json(later_root / RECEIPT, {
                    'taskId': good_task['taskId'], 'attemptId': good_actor['attemptId'], 'generation': 1,
                    'createdAt': board.store.now(),
                    'report': {'status': 'ok', 'result': {'native': 'later'}, 'shutdownConfirmed': True}})
                with patch.object(processes, 'inspect_process_identity', return_value='identity-absent'):
                    self.assertEqual(board.store.reconcile_startup()['interrupted'], 1)
                    self.assertEqual(client.get(runId=good_task['taskId'])['state'], 'completed')
                    issue = client.get(runId=task['taskId'])['selectedAttempt']['issues'][0]
                    self.assertEqual(issue['stage'], 'recovery')
                    self.assertEqual(issue['code'], code)
                    head = board.store.head()
                    self.assertEqual(board.store.recover_executions(), [])
                    self.assertEqual(board.store.head(), head)
                    board.store.reconcile_startup()
                    self.assertEqual(board.store.head(), head)

    def test_service_deadline_reason_is_a_durable_attempt_fact(self):
        board, client, task, _, actor = self.claimed()
        first = board.store.record_execution_deadline(actor['attemptId'], 'service-stop')
        second = board.store.record_execution_deadline(actor['attemptId'], 'forced-stop')
        self.assertEqual(first, second)
        with board.store.db.write() as db:
            db.execute("DELETE FROM events WHERE kind='execution.deadline'")
        with patch.object(processes, 'inspect_process_identity', return_value='identity-absent'):
            board.store.recover_executions()
        view = client.get(runId=task['taskId'])
        self.assertEqual(view['terminationReason'], 'service-stop')
        self.assertEqual(view['selectedAttempt']['forceReason'], 'service-stop')

    def test_unsettled_recovery_retains_startup_credentials_with_a_bad_receipt(self):
        from hey_my_buddy.protocol.execution import STARTUP, LIVE
        board, client, task, _, actor = self.claimed()
        with board.store.db.read() as db:
            attempt = dict(board.store._attempt_row(db, actor['attemptId']))
        directory = interruption._directory(board.store, attempt)
        fsync_json(directory / STARTUP, {'nonce': 'private nonce'})
        fsync_json(directory / LIVE, {'token': 'private token'})
        fsync_json(directory / RECEIPT, {'bad': 'receipt'})
        with patch.object(processes, 'inspect_process_identity', return_value='identity-absent'):
            board.store.recover_executions()
        self.assertTrue((directory / STARTUP).exists())
        # A failed endpoint reap keeps the sole cleanup credential for retry.
        self.assertTrue((directory / LIVE).exists())
        self.assertTrue((directory / RECEIPT).exists())
        self.assertTrue(client.get(runId=task['taskId'])['selectedAttempt']['issues'])

    def test_reaped_child_before_identity_ack_confirms_no_native_work(self):
        board, client, task, _, actor = self.claimed(identity=False)
        with patch.object(processes, 'inspect_process_identity') as inspect:
            self.assertEqual(len(board.store.recover_executions(actor['attemptId'],
                stopped_evidence={'kind': 'creator-reaped', 'exitCode': 1})), 1)
            inspect.assert_not_called()
        self.assertTrue(client.get(runId=task['taskId'])['shutdownConfirmed'])
        self.assertEqual(board.store.capacity_report()['totalActive'], 0)

    def test_live_spawn_marker_without_identity_ack_enters_the_shared_observer(self):
        from hey_my_buddy.blackboard.service.execution_scheduler import ExecutionScheduler
        board, client, task, _, actor = self.claimed(identity=False)
        evidence = board.directory / 'attempts' / task['taskId'] / actor['attemptId']
        fsync_json(evidence / 'spawn.marker', {'taskId': task['taskId'], 'attemptId': actor['attemptId'],
                                             'generation': 1, 'processIdentity': IDENTITY})
        with patch.object(processes, 'inspect_process_identity', return_value='alive'):
            board.store.reconcile_startup()
        scheduler = ExecutionScheduler(board.service, live_registry=board.live_registry)
        def retain_observer(claim, *_args):
            scheduler._observed[claim['attempt']['attemptId']] = _ExecutionObservation(
                claim['task']['taskId'], scheduler._condition)
        with patch.object(scheduler, '_start_observer', side_effect=retain_observer) as observe:
            scheduler._resume_executions()
            scheduler._resume_executions()
        observe.assert_called_once()
        recovered, location, identity = observe.call_args.args
        self.assertEqual(identity, IDENTITY)
        self.assertEqual(recovered['attempt']['attemptId'], actor['attemptId'])
        self.assertIsNone(location.process)
        with board.store.db.read() as connection:
            attempt = board.store._attempt_row(connection, actor['attemptId'])
            self.assertIsNone(attempt['identity_confirmed_at'])
            self.assertEqual(location.execution_directory, interruption._directory(board.store, attempt))
        self.assertEqual(client.get(runId=task['taskId'])['attemptState'], 'uncertain')
        self.assertEqual(board.store.resource_claims()[0]['state'], 'retained')

    def test_marker_without_identity_ack_confirms_no_native_creation_after_exit(self):
        board, client, task, _, actor = self.claimed(identity=False)
        evidence = board.directory / 'attempts' / task['taskId'] / actor['attemptId']
        fsync_json(evidence / 'spawn.marker', {'taskId': task['taskId'], 'attemptId': actor['attemptId'],
                                             'generation': 1, 'processIdentity': IDENTITY})
        with patch.object(processes, 'inspect_process_identity', return_value='identity-absent') as inspect:
            self.assertEqual(len(board.store.recover_executions()), 1)
            self.assertEqual(inspect.call_count, 1)
        self.assertTrue(client.get(runId=task['taskId'])['shutdownConfirmed'])
        self.assertEqual(board.store.capacity_report()['totalActive'], 0)

    def test_seal_retry_updates_interruption_kind_to_match_the_committed_result(self):
        board, client, task, _, actor = self.claimed()
        with patch.object(processes, 'inspect_process_identity', return_value='identity-absent'), \
                patch.object(interruption, '_stop_native', return_value=(True, 'orphan-stopped')), \
                patch.object(board.store.workflow, 'seal_interrupted_turn',
                             side_effect=BoardError('GIT_FAILED', 'temporary seal failure')):
            self.assertEqual(board.store.recover_executions(), [])
        with patch.object(processes, 'inspect_process_identity', return_value='identity-absent'):
            self.assertEqual(len(board.store.recover_executions()), 1)
        with board.store.db.read() as db:
            kind = db.execute('SELECT evidence_kind FROM attempt_interruptions WHERE attempt_id=?',
                              (actor['attemptId'],)).fetchone()[0]
        self.assertEqual(kind, client.get(runId=task['taskId'])['interruptionEvidence']['kind'])
        self.assertEqual(kind, 'identity-absent')

    def test_failed_endpoint_reap_retains_its_credential_and_retries_after_settlement(self):
        from hey_my_buddy.protocol.execution import STARTUP, LIVE
        board, client, task, _, actor = self.claimed()
        with board.store.db.read() as db:
            attempt = dict(board.store._attempt_row(db, actor['attemptId']))
        directory = interruption._directory(board.store, attempt)
        fsync_json(directory / STARTUP, {'nonce': 'private nonce'})
        fsync_json(directory / LIVE, {'token': 'private token'})
        with patch.object(processes, 'inspect_process_identity', return_value='identity-absent'), \
                patch('hey_my_buddy.protocol.execution.cleanup_execution_endpoint',
                      return_value={'status': 'unverified', 'reason': 'temporary cleanup failure'}):
            self.assertEqual(len(board.store.recover_executions()), 1)
        self.assertEqual(client.get(runId=task['taskId'])['attemptState'], 'finished')
        self.assertFalse((directory / STARTUP).exists())
        self.assertTrue((directory / LIVE).exists())
        with patch.object(processes, 'inspect_process_identity', return_value='identity-absent'), \
                patch('hey_my_buddy.protocol.execution.cleanup_execution_endpoint',
                      return_value={'status': 'reaped', 'reason': None}) as reap:
            self.assertEqual(board.store.recover_executions(), [])
            reap.assert_called_once()
        self.assertFalse((directory / LIVE).exists())
        self.assertFalse(client.get(runId=task['taskId'])['selectedAttempt']['issues'])
        self.assertEqual(board.store.resource_claims(), [])

    def test_released_admission_cannot_later_commit_a_result(self):
        board, _, _, _, actor = self.claimed(identity=False)
        board.store.release_execution(actor['attemptId'], reason='execution-process-not-created')
        with self.assertRaises(BoardError) as refused:
            board.call('execution_result', {**actor, 'status': 'ok', 'result': {'late': True},
                                           'shutdownConfirmed': True})
        self.assertEqual(refused.exception.code, 'ATTEMPT_FINISHED')

    def test_release_reason_belongs_to_the_selected_attempt(self):
        board, client, task, _, first = self.claimed(identity=False)
        self.assertNotIn('releaseReason', first)
        self.assertIsNone(client.get(runId=task['taskId'])['selectedAttempt']['releaseReason'])

        board.store.release_execution(first['attemptId'], reason='execution-process-not-created')
        released = client.get(runId=task['taskId'])['selectedAttempt']
        self.assertEqual(released['attemptId'], first['attemptId'])
        self.assertEqual(released['releaseReason'],
                         {'code': 'execution-process-not-created', 'params': {}})

        client.retry(runId=task['taskId'])
        second = admit_execution(client, 'n' * 32)['claim']['attempt']
        self.assertNotIn('releaseReason', second)
        current = client.get(runId=task['taskId'])['selectedAttempt']
        self.assertEqual(current['attemptId'], second['attemptId'])
        self.assertIsNone(current['releaseReason'])

        board.store.release_execution(second['attemptId'], reason='service-stopped-before-launch')
        self.assertEqual(client.get(runId=task['taskId'])['selectedAttempt']['releaseReason'],
                         {'code': 'service-stopped-before-launch', 'params': {}})
        with board.store.db.read() as connection:
            plan = connection.execute(
                "EXPLAIN QUERY PLAN SELECT payload_json FROM events "
                "WHERE task_id=? AND attempt_id=? AND kind='attempt.released'",
                (task['taskId'], second['attemptId']),
            ).fetchall()
        self.assertTrue(any('events_task_idx' in row['detail'] for row in plan), plan)

    def test_service_stop_between_admission_and_creation_releases_as_cancelled(self):
        board, client, task, _, actor = self.claimed(identity=False)
        board.store.task_cancel({'runId': task['taskId'], 'note': 'service stop'}, initiator='service')
        board.store.release_execution(actor['attemptId'], reason='service-stopped-before-launch')
        final = client.get(runId=task['taskId'])
        self.assertEqual(final['state'], 'cancelled')
        self.assertEqual(final['stopInitiator'], 'service')
        self.assertEqual(board.store.resource_claims(), [])
        self.assertTrue(final['shutdownConfirmed'])

    def test_finished_result_still_cleans_the_execution_secrets_after_crash(self):
        from hey_my_buddy.protocol.execution import STARTUP, LIVE
        board, client, task, _, actor = self.claimed()
        with board.store.db.read() as db:
            attempt = dict(board.store._attempt_row(db, actor['attemptId']))
        directory = interruption._directory(board.store, attempt)
        fsync_json(directory / STARTUP, {'nonce': 'private nonce'})
        fsync_json(directory / LIVE, {'token': 'private token'})
        board.call('execution_result', {**actor, 'status': 'ok', 'result': {'already': 'committed'},
                                       'shutdownConfirmed': True})
        with patch.object(processes, 'inspect_process_identity', return_value='alive'), \
                patch('hey_my_buddy.protocol.execution.cleanup_execution_endpoint') as reap:
            self.assertEqual(board.store.recover_executions(), [])
            reap.assert_not_called()
        self.assertTrue((directory / STARTUP).exists())
        with patch.object(processes, 'inspect_process_identity', return_value='identity-absent'), \
                patch('hey_my_buddy.protocol.execution.cleanup_execution_endpoint',
                      return_value={'status': 'reaped', 'reason': None}):
            self.assertEqual(board.store.recover_executions(), [])
        self.assertFalse((directory / STARTUP).exists())
        self.assertFalse((directory / LIVE).exists())
        with board.store.db.read() as db:
            self.assertEqual(board.store._attempt_row(db, actor['attemptId'])['cleanup_pending'], 0)
        self.assertEqual(client.result(runId=task['taskId'])['result'], {'already': 'committed'})
        self.assertEqual(client.get(runId=task['taskId'])['state'], 'completed')

    def test_creator_reaping_proof_survives_a_private_cleanup_retry(self):
        board, client, task, _, actor = self.claimed(identity=False)
        with patch.object(board.store, 'complete_execution_cleanup', side_effect=OSError('temporary cleanup write')):
            self.assertEqual(board.store.recover_executions(actor['attemptId'],
                stopped_evidence={'kind': 'creator-reaped', 'exitCode': 1}), [])
        self.assertEqual(client.get(runId=task['taskId'])['attemptState'], 'finished')
        with patch.object(processes, 'inspect_process_identity') as inspect:
            self.assertEqual(board.store.recover_executions(), [])
            inspect.assert_not_called()
        with board.store.db.read() as db:
            self.assertEqual(board.store._attempt_row(db, actor['attemptId'])['cleanup_pending'], 0)

    def test_late_native_stop_keeps_cancelled_result_and_records_its_stop_reason_separately(self):
        board, client, task, _, actor = self.claimed()
        board.store.task_cancel({'runId': task['taskId']}, initiator='service')
        journal = self.journal(board, actor['attemptId'])
        Path(journal['path']).unlink()
        with patch.object(processes, 'inspect_process_identity', return_value='identity-absent'):
            self.assertEqual(len(board.store.recover_executions()), 1)
            original = client.result(runId=task['taskId'])['selectedAttempt']['result']
            self.assertEqual(original['status'], 'cancelled')
            self.assertIsNone(original['terminationReason'])
            processes.NativeProcessJournal(Path(journal['path']), journal['binding'])
            self.assert_idempotently_confirmed(board, client, task, actor, original, state='cancelled')
        final = client.get(runId=task['taskId'])
        self.assertEqual(final['terminationReason'], 'service-stop')
        self.assertEqual(final['selectedAttempt']['shutdownEvidence']['detail']['terminationReason'], 'service-stop')
