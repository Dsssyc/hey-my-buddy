"""Private service observation and Lead doorbells share durable message facts."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
import threading
import uuid
from unittest.mock import patch

from blackboard.tasks.test_workflow import WorkflowTestCase
from blackboard.tasks import test_lead_lifecycle as lead_helpers
from hey_my_buddy.blackboard.tasks import messages, reminders
from hey_my_buddy.cli.blocking import await_run
from hey_my_buddy.errors import BoardError
from support import FakeClock


class MessageTests(WorkflowTestCase):
    lead = lead_helpers.LeadLifecycleTests.lead
    owned = lead_helpers.LeadLifecycleTests.owned
    wait = lead_helpers.LeadLifecycleTests.wait
    current = lead_helpers.LeadLifecycleTests.current
    wakeups = lead_helpers.LeadLifecycleTests.wakeups

    def test_own_accept_and_queued_cancel_of_newly_created_tasks_do_not_ring(self):
        for action in ('accept', 'cancel'):
            with self.subTest(action=action):
                board = self.board(max_concurrent=2)
                lead, claim = self.lead(board, name='own-' + action)
                child = self.owned(board, claim, name='handled')
                self.owned(board, claim, name='ongoing')
                if action == 'accept':
                    self.finish_turn(board, self.claim(board, 'w2', run_id=child['runId']), worker_id='w2')
                    self.accept_run(board, self.current(board, child), credential=claim['agentCredential'])
                else:
                    board.call('workflow_cancel', {'runId': child['runId'], 'credential': claim['agentCredential']})
                self.wait(board, claim)
                self.assertEqual(self.current(board, lead)['state'], 'waiting-owned')
                self.assertEqual(self.wakeups(board, lead), [])

    def test_unclaimed_issues_before_the_next_input_keep_ringing_until_await_claims_them(self):
        for kind in ('execution', 'service'):
            with self.subTest(kind=kind):
                board = self.board(max_concurrent=2)
                lead, claim = self.lead(board, name='unclaimed-' + kind)
                child = self.owned(board, claim)
                child_claim = self.claim(board, 'w2', run_id=child['runId'])['claim']
                self.wait(board, claim)
                if kind == 'execution':
                    board.store.record_execution_issue(child_claim['attempt']['attemptId'], 'connection', RuntimeError('pending'))
                else:
                    board.store.record_service_issue('scheduler', RuntimeError('pending'))
                resumed = self.claim(board, run_id=lead['runId'], claim_request_id='first-ring')['claim']
                self.wait(board, resumed)
                self.assertEqual(self.current(board, lead)['state'], 'executing')
                self.assertEqual(len(self.wakeups(board, lead)), 2)
                resumed = self.claim(board, run_id=lead['runId'], claim_request_id='claim-content')['claim']
                self.assertEqual(self.observe(board, child['runId'], credential=resumed['agentCredential'])['outcome'], 'issue')
                self.wait(board, resumed)
                self.assertEqual(self.current(board, lead)['state'], 'waiting-owned')

    def test_unclaimed_reminder_before_the_next_input_keeps_ringing(self):
        clock = FakeClock()
        board = self.board(max_concurrent=2, clock=clock)
        lead, claim = self.lead(board)
        child = self.owned(board, claim, expectedSeconds=10)
        self.claim(board, 'w2', run_id=child['runId'])
        self.wait(board, claim)
        clock.advance(10)
        reminders.collect(board.store)
        resumed = self.claim(board, run_id=lead['runId'], claim_request_id='first-reminder')['claim']
        self.wait(board, resumed)
        self.assertEqual(self.current(board, lead)['state'], 'executing')
        self.assertEqual(len(self.wakeups(board, lead)), 2)

    def test_first_issue_delivery_uses_the_updated_cause_with_the_original_identity(self):
        board = self.board()
        run = self.submit(board)
        attempt = self.claim(board, run_id=run['runId'])['claim']['attempt']['attemptId']
        board.store.record_execution_issue(attempt, 'connection', RuntimeError('first cause'))
        with board.store.db.read() as connection:
            opening = connection.execute('SELECT occurrence_seq FROM execution_issues WHERE attempt_id=?', (attempt,)).fetchone()[0]
        board.store.record_execution_issue(attempt, 'connection', RuntimeError('current cause'))
        reply = self.receipt(board, run['runId'])
        current = {**reply['selectedAttempt']['issues'][0], 'attemptId': attempt}
        self.assertEqual(reply['executionIssues'][0], current)
        self.assertEqual(current['message'], 'current cause')
        with board.store.db.read() as connection:
            receipt = connection.execute('SELECT occurrence_seq,observation_seq FROM issue_deliveries').fetchone()
            self.assertEqual(receipt['occurrence_seq'], opening)
            self.assertGreater(receipt['observation_seq'], opening)

    def test_delivery_preserves_observation_time_when_event_append_is_later(self):
        board = self.board()
        run = self.submit(board)
        attempt = self.claim(board, run_id=run['runId'])['claim']['attempt']['attemptId']
        observed = '2026-11-06T00:00:01.000Z'
        appended = '2026-11-06T00:00:01.001Z'
        with patch.object(board.store, 'now', side_effect=[observed, appended]):
            board.store.record_execution_issue(attempt, 'connection', RuntimeError('current cause'))
        reply = self.receipt(board, run['runId'], 'observation-clock')
        self.assertEqual(reply['executionIssues'][0]['observedAt'], observed)
        self.assertEqual(reply['selectedAttempt']['issues'][0]['observedAt'], observed)
        board.store.clear_execution_issue(attempt, 'connection')
        self.assertEqual(self.receipt(board, run['runId'], 'observation-clock')['executionIssues'][0]['observedAt'], observed)

    def test_issue_resolved_before_delivery_does_not_ring_or_get_claimed(self):
        board = self.board(max_concurrent=2)
        lead, claim = self.lead(board)
        child = self.owned(board, claim)
        board.store.record_service_issue('scheduler', RuntimeError('short-lived'))
        board.store.clear_service_issue('scheduler')
        self.wait(board, claim)
        self.assertEqual(self.current(board, lead)['state'], 'waiting-owned')
        self.assertNotIn('issue', self.receipt(board, child['runId']))

    def test_writing_an_owned_event_never_decorates_or_embeds_an_await_view(self):
        board = self.board()
        _, claim = self.lead(board)
        child = self.owned(board, claim)
        with patch.object(board.store, '_decorate', side_effect=AssertionError('event computed task view')):
            with board.store.db.write() as connection:
                seq = board.store._append_event(connection, 'attempt.progress', task_id=child['runId'])
        with board.store.db.read() as connection:
            payload = json.loads(connection.execute('SELECT payload_json FROM events WHERE seq=?', (seq,)).fetchone()[0])
        self.assertNotIn('awaitMessage', payload)

    def test_accepting_owned_work_and_its_cleanup_do_not_ring_the_lead_again(self):
        board = self.board(max_concurrent=2)
        lead, claim = self.lead(board)
        child = self.owned(board, claim, name='to-accept')
        self.owned(board, claim, name='still-queued')
        self.wait(board, claim)
        child_claim = self.claim(board, 'w2', run_id=child['runId'])
        self.finish_turn(board, child_claim, worker_id='w2')
        resumed = self.claim(board, run_id=lead['runId'], claim_request_id='accept-delivery')['claim']
        delivered = self.current(board, child)
        self.accept_run(board, delivered, credential=resumed['agentCredential'])
        with board.store.db.read() as connection:
            cleanup = connection.execute("SELECT actor_role FROM events WHERE task_id=? AND kind='workflow.cleanup_planned'",
                                         (child['runId'],)).fetchone()
        self.assertIsNotNone(cleanup)
        self.assertIsNone(cleanup['actor_role'])
        self.wait(board, resumed)
        self.assertEqual(self.current(board, lead)['state'], 'waiting-owned')
        self.assertEqual(len(self.wakeups(board, lead)), 1)

    def service(self, board, credential=None):
        def call(method, params, directory):
            params = {**params, **({'credential': credential} if credential else {})}
            result = board.call(method, params)
            return result['task'] if method == 'task_get' else result
        return call

    def observe(self, board, run_id, *, seconds=1, credential=None):
        return await_run({'runId': run_id, 'waitSeconds': seconds}, self.directory,
                         service=self.service(board, credential))

    def receipt(self, board, run_id, wait_id=None):
        return board.store.task_wait({'runId': run_id, 'timeoutMs': 0, 'consumeReminders': True,
                                      'reminderWaitId': wait_id or uuid.uuid4().hex})

    def test_empty_consume_and_issue_replay_do_not_open_write_transactions(self):
        board = self.board()
        run_id = self.submit(board)['runId']
        selector = {'runId': run_id, 'reminderWaitId': 'issue-reply'}
        with patch.object(board.store.db, 'write', side_effect=AssertionError('unexpected writer')):
            self.assertEqual(messages.consume(board.store, selector), {})
            with self.assertRaises(BoardError) as caught:
                messages.consume(board.store, {**selector, 'runId': 'missing'})
            self.assertEqual(caught.exception.code, 'NOT_FOUND')
        board.store.record_service_issue('scheduler', RuntimeError('private scheduler failed'))
        first = messages.consume(board.store, selector)
        self.assertIn('issue', first)
        with patch.object(board.store.db, 'write', side_effect=AssertionError('unexpected writer')):
            self.assertEqual(messages.consume(board.store, selector), first)

    def test_message_delivery_event_queries_use_task_index(self):
        board = self.board()
        run_id = self.submit(board)['runId']
        statements = []
        with board.store.db.read() as connection:
            connection.set_trace_callback(statements.append)
        try:
            self.assertEqual(messages.consume(board.store, {'runId': run_id, 'reminderWaitId': 'no-message'}), {})
        finally:
            with board.store.db.read() as connection:
                connection.set_trace_callback(None)
        with board.store.db.read() as connection:
            for prefix, event_tables in (
                ('SELECT * FROM events WHERE', ('events',)),
                ('SELECT p.* FROM events p', ('p', 'd')),
                ('SELECT r.*, a.started_at, e.seq AS event_seq', ('e',)),
            ):
                with self.subTest(query=prefix):
                    [statement] = [sql for sql in statements if sql.startswith(prefix)]
                    plan = [row['detail'] for row in connection.execute('EXPLAIN QUERY PLAN ' + statement)]
                    for table in event_tables:
                        self.assertTrue(any(detail.startswith(f'SEARCH {table} ') and 'events_task_idx' in detail
                                            for detail in plan), plan)
                        self.assertFalse(any(detail.startswith(f'SCAN {table} ') or detail == f'SCAN {table}'
                                             for detail in plan), plan)

    def test_wait_rejects_a_missing_or_invalid_run_identifier_at_the_boundary(self):
        board = self.board()
        for selector in ({}, {'runId': 1}, {'runId': ''}):
            with self.subTest(selector=selector), self.assertRaises(BoardError) as caught:
                board.call('task_wait', {**selector, 'timeoutMs': 0})
            self.assertEqual(caught.exception.code, 'INVALID_ARGUMENT')

    def test_publication_issue_updates_keep_the_same_occurrence(self):
        board = self.board()
        run = self.submit(board)
        claim = self.claim(board, run_id=run['runId'])['claim']
        for message in ('first publication failure', 'updated publication failure'):
            with board.store.db.write() as connection:
                attempt = board.store._attempt_row(connection, claim['attempt']['attemptId'])
                board.store.decisions._record_publication_issue(
                    connection, attempt, BoardError('PUBLICATION_FAILED', message), board.store.now())
            reply = self.receipt(board, run['runId'])
            self.assertEqual('issue' in reply, message == 'first publication failure')

    def test_execution_issue_wakes_a_wait_once_and_a_recurrence_wakes_again(self):
        board = self.board()
        run = self.submit(board)
        claim = self.claim(board, run_id=run['runId'])['claim']
        attempt_id = claim['attempt']['attemptId']
        entered = threading.Event()
        events_wait = board.store.events_wait
        def subscribed(params, **kwargs):
            entered.set()
            return events_wait(params, **kwargs)
        with patch.object(board.store, 'events_wait', side_effect=subscribed), ThreadPoolExecutor(1) as pool:
            future = pool.submit(self.observe, board, run['runId'], seconds=5)
            self.assertTrue(entered.wait(3))
            board.store.record_execution_issue(attempt_id, 'connection', BoardError('EXECUTION_UNKNOWN', 'No connection'))
            reply = future.result(timeout=3)
        self.assertEqual(reply['outcome'], 'issue')
        self.assertEqual(reply['executionIssues'][0]['code'], 'EXECUTION_UNKNOWN')
        self.assertEqual(reply['serviceIssues'], [])
        # Updating the cause while the stage stays unresolved is the same issue.
        board.store.record_execution_issue(attempt_id, 'connection', BoardError('EXECUTION_UNKNOWN', 'Still no connection'))
        self.assertEqual(self.observe(board, run['runId'])['outcome'], 'wait-timeout')
        board.store.clear_execution_issue(attempt_id, 'connection')
        board.store.record_execution_issue(attempt_id, 'connection', BoardError('EXECUTION_UNKNOWN', 'No connection'))
        self.assertEqual(self.observe(board, run['runId'])['outcome'], 'issue')
        self.assertNotIn('issue', self.receipt(board, run['runId']))
        with board.store.db.read() as connection:
            rows = connection.execute('SELECT e.kind,d.occurrence_seq FROM issue_deliveries d JOIN events e ON e.seq=d.occurrence_seq WHERE d.task_id=?',
                                      (run['runId'],)).fetchall()
        self.assertEqual(len(rows), 2)
        self.assertEqual({row['kind'] for row in rows}, {'execution.issue'})
        self.assertNotEqual(rows[0]['occurrence_seq'], rows[1]['occurrence_seq'])

    def test_same_wait_replays_its_issue_even_after_resolution_and_a_new_occurrence(self):
        board = self.board()
        run = self.submit(board)
        claim = self.claim(board, run_id=run['runId'])['claim']
        attempt_id = claim['attempt']['attemptId']
        board.store.record_execution_issue(attempt_id, 'shutdown', RuntimeError('stop unknown'))
        first = self.receipt(board, run['runId'], 'lost-reply')
        self.assertTrue(first['issue'])
        board.store.clear_execution_issue(attempt_id, 'shutdown')
        board.store.record_execution_issue(attempt_id, 'shutdown', RuntimeError('unknown again'))
        replay = self.receipt(board, run['runId'], 'lost-reply')
        self.assertEqual(replay['issue'], first['issue'])
        self.assertEqual(replay['executionIssues'][0], first['executionIssues'][0])
        self.assertEqual(self.receipt(board, run['runId'], 'next-wait')['executionIssues'][0]['message'], 'unknown again')
        with board.store.db.read() as connection:
            self.assertEqual(connection.execute('SELECT COUNT(*) FROM issue_deliveries').fetchone()[0], 2)
        other = self.submit(board, request_id='other', cwd=str(self.workdir('other')))
        with self.assertRaises(BoardError) as caught:
            self.receipt(board, other['runId'], 'lost-reply')
        self.assertEqual(caught.exception.code, 'CONFLICT')

    def test_service_issue_wakes_an_existing_wait_and_is_delivered_once_per_micro_task(self):
        board = self.board()
        first = self.submit(board)
        second = self.submit(board, request_id='second', cwd=str(self.workdir('second')))
        entered = threading.Event()
        events_wait = board.store.events_wait
        def subscribed(params, **kwargs):
            entered.set()
            return events_wait(params, **kwargs)
        with patch.object(board.store, 'events_wait', side_effect=subscribed), ThreadPoolExecutor(1) as pool:
            future = pool.submit(self.observe, board, first['runId'], seconds=5)
            self.assertTrue(entered.wait(3))
            board.store.record_service_issue('scheduler', RuntimeError('private scheduler failed'))
            self.assertEqual(future.result(timeout=3)['outcome'], 'issue')
        reply = self.observe(board, second['runId'])
        self.assertEqual(reply['outcome'], 'issue')
        self.assertEqual(reply['serviceIssues'][0]['stage'], 'scheduler')
        for run in (first, second):
            self.assertNotIn('issue', self.receipt(board, run['runId']))
        board.store.record_service_issue('scheduler', RuntimeError('updated diagnosis'))
        self.assertNotIn('issue', self.receipt(board, first['runId']))
        board.store.clear_service_issue('scheduler')
        board.store.record_service_issue('scheduler', RuntimeError('private scheduler failed'))
        for run in (first, second):
            self.assertEqual(self.observe(board, run['runId'])['outcome'], 'issue')

    def test_new_issue_is_claimed_even_when_the_workflow_already_has_a_delivery(self):
        board = self.board()
        run = self.submit(board)
        self.finish_turn(board, self.claim(board, run_id=run['runId']))
        board.store.record_service_issue('scheduler', RuntimeError('new failure after delivery'))
        self.assertEqual(self.observe(board, run['runId'])['outcome'], 'issue')
        self.assertEqual(self.observe(board, run['runId'])['outcome'], 'delivered')

    def test_issue_replay_precedes_a_reminder_that_arrived_after_its_lost_reply(self):
        clock = FakeClock()
        board = self.board(clock=clock)
        run = self.submit(board, expectedSeconds=10)
        claim = self.claim(board, run_id=run['runId'])['claim']
        board.store.record_execution_issue(claim['attempt']['attemptId'], 'connection', RuntimeError('unknown'))
        self.assertTrue(self.receipt(board, run['runId'], 'issue-first')['issue'])
        clock.advance(10)
        reminders.collect(board.store)
        self.assertTrue(self.receipt(board, run['runId'], 'issue-first')['issue'])
        self.assertEqual(self.receipt(board, run['runId'])['reminder']['kind'], 'expected-duration')

    def test_concurrent_retries_claim_only_one_message(self):
        clock = FakeClock()
        board = self.board(clock=clock)
        run = self.submit(board, expectedSeconds=10)
        claim = self.claim(board, run_id=run['runId'])['claim']
        board.store.record_execution_issue(claim['attempt']['attemptId'], 'shutdown', RuntimeError('unknown'))
        clock.advance(10)
        reminders.collect(board.store)
        write = board.store.db.write
        contenders = threading.Barrier(2, timeout=5)

        def synchronized_write():
            contenders.wait()
            return write()

        with patch.object(board.store.db, 'write', side_effect=synchronized_write), ThreadPoolExecutor(2) as pool:
            replies = list(pool.map(lambda _: self.receipt(board, run['runId'], 'same-wait'), range(2)))
        self.assertEqual(replies[0]['reminder'], replies[1]['reminder'])
        self.assertTrue(self.receipt(board, run['runId'])['issue'])

    def test_waiters_recheck_an_unrelated_wakeup_during_a_write(self):
        board = self.board()
        run_id = self.submit(board)['runId']
        unrelated_id = self.submit(board, request_id='unrelated', cwd=str(self.workdir('unrelated')))['runId']
        initial_head = board.store.head()
        events_wait = board.store.events_wait
        subscribed = threading.Barrier(4, timeout=5)
        writer_holds_lock = threading.Event()

        def wait_for_events(params):
            subscribed.wait()
            result = events_wait(params)
            if params['after'] == initial_head:
                self.assertTrue(writer_holds_lock.wait(5))
                self.assertEqual(result['events'][0]['taskId'], unrelated_id)
            return result

        params = {'runId': run_id, 'timeoutMs': 15000, 'consumeReminders': True, 'reminderWaitId': 'shared-wait'}
        with patch.object(board.store, 'events_wait', side_effect=wait_for_events), ThreadPoolExecutor(3) as pool:
            futures = [pool.submit(board.store.task_wait, params) for _ in range(3)]
            subscribed.wait()
            with board.store.db.write() as connection:
                head = board.store._append_event(connection, 'attempt.progress', task_id=unrelated_id)
            with board.store.db.write():
                board.store._notify(head)
                writer_holds_lock.set()
                # All waiters must finish their empty recheck while SQLite's writer lock is held.
                subscribed.wait()
            board.store.record_service_issue('scheduler', RuntimeError('wake all retries'))
            replies = [future.result(timeout=5)['issue'] for future in futures]
        self.assertEqual(replies, [replies[0]] * 3)
        with board.store.db.read() as connection:
            self.assertEqual(connection.execute('SELECT COUNT(*) FROM issue_deliveries').fetchone()[0], 1)

    def test_lead_wait_authority_is_limited_to_its_owned_tasks(self):
        board = self.board(max_concurrent=2)
        lead, claim = self.lead(board)
        child = self.owned(board, claim)
        foreign = self.submit(board, request_id='foreign', cwd=str(self.workdir('foreign')))
        params = {'runId': foreign['runId'], 'timeoutMs': 0, 'credential': claim['agentCredential']}
        with self.assertRaises(BoardError) as caught:
            board.call('task_wait', params)
        self.assertEqual(caught.exception.code, 'FORBIDDEN')
        self.assertEqual(board.call('task_wait', {**params, 'runId': child['runId']})['runId'], child['runId'])

    def test_lead_doorbell_follows_issue_occurrences_and_await_reads_the_content(self):
        for kind in ('execution', 'service'):
            with self.subTest(kind=kind):
                board = self.board(max_concurrent=2)
                lead, claim = self.lead(board, name='lead-' + kind)
                child = self.owned(board, claim)
                owned_claim = self.claim(board, 'w2', run_id=child['runId'])['claim']
                attempt_id = owned_claim['attempt']['attemptId']
                self.wait(board, claim)
                def record(message):
                    if kind == 'execution':
                        board.store.record_execution_issue(attempt_id, 'connection', RuntimeError(message))
                    else:
                        board.store.record_service_issue('scheduler', RuntimeError(message))
                record('unknown')
                self.assertEqual(self.current(board, lead)['state'], 'executing')
                self.assertEqual(len(self.wakeups(board, lead)), 1)
                resumed = self.claim(board, run_id=lead['runId'], claim_request_id='read-issue')['claim']
                reply = self.observe(board, child['runId'], credential=resumed['agentCredential'])
                self.assertEqual(reply['outcome'], 'issue')
                self.wait(board, resumed)
                record('updated cause')
                self.assertEqual(self.current(board, lead)['state'], 'waiting-owned')
                if kind == 'execution':
                    board.store.clear_execution_issue(attempt_id, 'connection')
                else:
                    board.store.clear_service_issue('scheduler')
                record('unknown')
                self.assertEqual(self.current(board, lead)['state'], 'executing')
                self.assertEqual(len(self.wakeups(board, lead)), 2)
                trigger = json.loads(self.wakeups(board, lead)[-1]['trigger_event_json'])
                self.assertEqual(trigger['runId'], child['runId'])
                self.assertEqual(trigger['kind'], kind + '.issue')

    def test_reminders_wake_the_lead_without_consuming_the_await_delivery(self):
        for kind in ('expected-duration', 'native-silence'):
            with self.subTest(kind=kind):
                clock = FakeClock()
                board = self.board(max_concurrent=2, clock=clock)
                lead, claim = self.lead(board, name='lead-' + kind)
                child = self.owned(board, claim, expectedSeconds=10 if kind == 'expected-duration' else 600)
                self.claim(board, 'w2', run_id=child['runId'])
                with board.store.db.write() as connection:
                    connection.execute('UPDATE reminder_settings SET silence_seconds=5 WHERE id=1')
                self.wait(board, claim)
                clock.advance(10 if kind == 'expected-duration' else 5)
                reminders.collect(board.store)
                self.assertEqual(self.current(board, lead)['state'], 'executing')
                self.assertEqual(len(self.wakeups(board, lead)), 1)
                self.assertEqual(self.observe(board, child['runId'])['outcome'], kind)
