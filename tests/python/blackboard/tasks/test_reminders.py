"""Deadline and native-silence facts, independent of harness/model execution."""
from support import BoardTestCase, FakeClock
import uuid
from hey_my_buddy.blackboard.tasks import reminders


class ReminderFacts(BoardTestCase):
    def setUp(self):
        super().setUp()
        self.clock = FakeClock()
        self.fixture = self.board(clock=self.clock)
        self.store = self.fixture.store
        self.client = self.fixture.client()

    def claim(self, expected=10):
        self.task = self.client.submit(requestId='reminder', task='observe', cwd=str(self.workdir()),
                                       adapter='command', argv=['echo', 'test'], expectedSeconds=expected)['task']
        self.client.register_worker('w-reminder', adapter='command', capabilities=['command'])
        self.nonce = 'n' * 32
        self.attempt = self.client.claim('w-reminder', 'claim-reminder', self.nonce)['claim']['attempt']

    def activity(self, sequence, phase='streaming-model', **extra):
        return self.client.progress('w-reminder', self.attempt['attemptId'], self.attempt['generation'], self.nonce,
                                    'native event', data={'activity': {'phase': phase, 'eventSeq': sequence,
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

    def test_silence_keeps_native_timestamp_tool_fact_and_ignores_renewal(self):
        self.claim(expected=100)
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
        self.client.renew('w-reminder', self.attempt['attemptId'], self.attempt['generation'], self.nonce)
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
        # New service resource, same durable SQLite state: no process-local cursor.
        from hey_my_buddy.blackboard.service.service import BoardService
        service = BoardService(self.store, token='test-token', control={}, on_stop=lambda p: {}, on_restart=lambda p: {})
        next_view = service.store_task_wait({'runId': self.task['runId'], 'timeoutMs': 0, 'consumeReminders': True,
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
        self.claim(expected=100)
        with self.store.db.write() as db:
            db.execute('UPDATE reminder_settings SET silence_seconds=5 WHERE id=1')
        self.clock.advance(3)
        self.client.progress('w-reminder', self.attempt['attemptId'], self.attempt['generation'], self.nonce,
                             'prompt sent', data={'activity': {'phase': 'waiting-model', 'eventSeq': 0,
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
        self.claim(expected=100)
        with self.store.db.write() as db:
            db.execute('UPDATE reminder_settings SET silence_seconds=5 WHERE id=1')
        for sequence, observed in ((1, True), (2, False)):
            self.clock.advance(3)
            native_at = self.clock()
            activity = {'phase': 'tool-running', 'eventSeq': sequence}
            if observed:
                activity['observedAt'] = native_at
            self.client.progress('w-reminder', self.attempt['attemptId'], self.attempt['generation'], self.nonce,
                                 'native event', data={'activity': activity})
            self.assertEqual(reminders.collect(self.store), 5)
            self.clock.advance(5)
            reminders.collect(self.store)
            self.assertEqual(self.consume()['reminder']['lastNativeEventAt'], native_at)
