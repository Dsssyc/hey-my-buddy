"""Expected-duration facts for every execution and native-silence facts for harnesses."""
from admission import admit_execution
from contextlib import contextmanager
from support import BoardTestCase, FakeClock
import uuid
from unittest.mock import patch
from hey_my_buddy.blackboard.tasks import reminders


class ReminderFacts(BoardTestCase):
    def setUp(self):
        super().setUp()
        self.clock = FakeClock()
        self.fixture = self.board(clock=self.clock)
        self.store = self.fixture.store
        self.client = self.fixture.client()

    def claim(self, expected=10, adapter='command'):
        self.task = self.fixture.submit_execution(requestId='reminder', task='observe', cwd=str(self.workdir()),
                                       adapter=adapter, **({'argv': ['echo', 'test']} if adapter == 'command' else {}),
                                       expectedSeconds=expected)['task']
        self.nonce = 'n' * 32
        self.attempt = admit_execution(self.client, self.nonce)['claim']['attempt']

    def claim_native(self, expected=100):
        from hey_my_buddy.blackboard.tasks import workflow
        from mock_workspace import MockWorkspace

        self.catalog_fixture()
        self.enterContext(patch.object(workflow, '_workspace_module', MockWorkspace(self.directory)))
        submitted = self.client.workflow_submit(
            requestId='reminder', hostId='reminder-host', task='observe', cwd=str(self.workdir()),
            harness='dsh', provider='deepseek-official', model='deepseek-flash', effort='off',
            executionWorkspace={'kind': 'existing', 'access': 'read'}, expectedSeconds=expected)
        self.task = submitted['task']
        self.nonce = 'n' * 32
        self.attempt = admit_execution(self.client, self.nonce)['claim']['attempt']

    def activity(self, sequence, phase='streaming-model', **extra):
        return self.client.progress(self.attempt['attemptId'], self.attempt['generation'], self.nonce, 'native event', data={'activity': {'phase': phase, 'eventSeq': sequence,
                                                                     'lastNativeActivityAt': self.clock(), **extra}})

    def records(self):
        return [event['payload'] for event in self.client.read_events(after=0, limit=200)['events']
                if event['kind'] == 'attempt.reminder']

    def consume(self):
        return self.fixture.call('task_wait', {'runId': self.task['runId'], 'timeoutMs': 0,
                                              'consumeReminders': True, 'reminderWaitId': str(uuid.uuid4())})

    def test_idle_has_no_deadline_and_deadlines_follow_expected_duration(self):
        self.assertIsNone(reminders.collect(self.store))
        self.claim()
        self.assertEqual(reminders.collect(self.store), 10)
        self.clock.advance(10)
        self.assertEqual(reminders.collect(self.store), 10)
        first = self.consume()['reminder']
        self.assertEqual((first['kind'], first['expectedMultiple'], first['elapsedSeconds']), ('expected-duration', 1, 10))
        self.assertIsNone(first['lastNativeEventAt'])
        self.assertNotIn('reminder', self.consume())
        self.clock.advance(10)
        reminders.collect(self.store)
        second = self.consume()['reminder']
        self.assertEqual((second['expectedMultiple'], second['elapsedSeconds']), (2, 20))
        self.assertNotEqual(first['reminderId'], second['reminderId'])
        self.assertEqual(len(self.records()), 2)
        self.assertEqual(self.client.get(runId=self.task['runId'])['status'], 'running')

    def test_idle_and_future_deadlines_do_not_open_a_write_transaction(self):
        with patch.object(self.store.db, 'write', side_effect=AssertionError('unexpected writer')):
            self.assertIsNone(reminders.collect(self.store))
        self.claim()
        with patch.object(self.store.db, 'write', side_effect=AssertionError('unexpected writer')):
            self.assertEqual(reminders.collect(self.store), 10)
        self.assertEqual(self.records(), [])

    def _assert_no_native_silence(self, adapter):
        self.claim(adapter=adapter)
        with self.store.db.write() as db:
            db.execute('UPDATE reminder_settings SET silence_seconds=5 WHERE id=1')
        self.assertEqual(reminders.collect(self.store), 10)
        self.clock.advance(5)
        self.assertEqual(reminders.collect(self.store), 5)
        self.assertEqual(self.records(), [])
        self.assertFalse(self.client.get(runId=self.task['runId'])['selectedAttempt']['reminders']['possiblyStuck'])
        self.clock.advance(5)
        self.assertEqual(reminders.collect(self.store), 10)
        self.assertEqual([record['kind'] for record in self.records()], ['expected-duration'])
        self.assertEqual(self.consume()['reminder']['expectedMultiple'], 1)

    def test_command_attempt_has_duration_but_no_native_silence_deadline(self):
        self._assert_no_native_silence('command')

    def test_due_reminder_is_rechecked_after_a_concurrent_completion(self):
        self.claim()
        self.clock.advance(10)
        original_write = self.store.db.write

        @contextmanager
        def complete_before_collect_write():
            with original_write() as connection:
                connection.execute("UPDATE attempts SET execution_state='finished',result_json='{}'"
                                   ' WHERE attempt_id=?', (self.attempt['attemptId'],))
            with original_write() as connection:
                yield connection

        with patch.object(self.store.db, 'write', side_effect=complete_before_collect_write):
            self.assertIsNone(reminders.collect(self.store))
        self.assertEqual(self.records(), [])

    def test_silence_keeps_native_timestamp_tool_fact_and_ignores_renewal(self):
        self.claim_native()
        with self.store.db.write() as db:
            db.execute('UPDATE reminder_settings SET silence_seconds=5 WHERE id=1')
        self.activity(1, 'tool-running')
        native_at = self.clock()
        self.clock.advance(5)
        self.assertEqual(reminders.collect(self.store), 95)
        first = self.consume()['reminder']
        self.assertEqual((first['kind'], first['lastNativeEventAt'], first['toolInProgress']),
                         ('native-silence', native_at, True))
        self.assertTrue(self.client.get(runId=self.task['runId'])['selectedAttempt']['reminders']['possiblyStuck'])
        self.clock.advance(5)
        self.client.progress(self.attempt['attemptId'], self.attempt['generation'], self.nonce)
        reminders.collect(self.store)
        self.assertNotIn('reminder', self.consume())
        self.assertEqual(len(self.records()), 1)
        self.activity(2)
        self.assertFalse(self.client.get(runId=self.task['runId'])['selectedAttempt']['reminders']['possiblyStuck'])
        self.clock.advance(5)
        reminders.collect(self.store)
        second = self.consume()['reminder']
        self.assertEqual(second['lastNativeEventAt'], '2026-01-01T00:00:10.000Z')
        self.assertFalse(second['toolInProgress'])
        self.assertEqual(len(self.records()), 2)

    def test_missed_deadlines_are_each_recorded_once_and_consumption_survives_restart(self):
        self.claim()
        self.clock.advance(30)
        reminders.collect(self.store)
        reminders.collect(self.store)
        self.assertEqual([record['expectedMultiple'] for record in self.records()], [1, 2, 3])
        self.assertEqual(self.consume()['reminder']['expectedMultiple'], 1)
        # Waiting reads the durable reminder cursor from this store.
        next_view = self.store.task_wait({'runId': self.task['runId'], 'timeoutMs': 0, 'consumeReminders': True,
                                            'reminderWaitId': str(uuid.uuid4())})
        self.assertEqual(next_view['reminder']['expectedMultiple'], 2)

    def test_wait_receipt_replays_after_lost_reply_without_consuming_the_next_reminder(self):
        self.claim()
        self.clock.advance(20)
        reminders.collect(self.store)
        params = {'runId': self.task['runId'], 'timeoutMs': 0, 'consumeReminders': True, 'reminderWaitId': 'lost-reply'}
        first = self.fixture.call('task_wait', params)['reminder']
        self.assertEqual(self.fixture.call('task_wait', params)['reminder'], first)
        self.assertEqual(self.consume()['reminder']['expectedMultiple'], 2)

    def test_prompt_observation_does_not_reset_silence_or_invent_a_native_event(self):
        self.claim_native()
        with self.store.db.write() as db:
            db.execute('UPDATE reminder_settings SET silence_seconds=5 WHERE id=1')
        self.clock.advance(3)
        self.client.progress(self.attempt['attemptId'], self.attempt['generation'], self.nonce, 'prompt sent', data={'activity': {'phase': 'waiting-model', 'eventSeq': 0,
                                                              'observedAt': self.clock()}})
        self.assertEqual(reminders.collect(self.store), 2)
        self.clock.advance(2)
        reminders.collect(self.store)
        self.assertIsNone(self.consume()['reminder']['lastNativeEventAt'])

    def test_unknown_stop_does_not_suppress_or_stop_reminders(self):
        self.claim()
        with self.store.db.write() as db:
            db.execute("UPDATE attempts SET execution_state='uncertain',ownership='uncertain' WHERE attempt_id=?",
                       (self.attempt['attemptId'],))
        self.clock.advance(10)
        reminders.collect(self.store)
        view = self.consume()
        self.assertEqual(view['reminder']['kind'], 'expected-duration')
        self.assertEqual(view['attemptState'], 'uncertain')
        self.assertFalse(view['shutdownConfirmed'])

    def test_observed_time_and_sequence_only_receipts_count_as_native_activity(self):
        self.claim_native()
        with self.store.db.write() as db:
            db.execute('UPDATE reminder_settings SET silence_seconds=5 WHERE id=1')
        for sequence, observed in ((1, True), (2, False)):
            self.clock.advance(3)
            native_at = self.clock()
            activity = {'phase': 'tool-running', 'eventSeq': sequence}
            if observed:
                activity['observedAt'] = native_at
            self.client.progress(self.attempt['attemptId'], self.attempt['generation'], self.nonce, 'native event', data={'activity': activity})
            self.assertEqual(reminders.collect(self.store), 5)
            self.clock.advance(5)
            reminders.collect(self.store)
            self.assertEqual(self.consume()['reminder']['lastNativeEventAt'], native_at)
