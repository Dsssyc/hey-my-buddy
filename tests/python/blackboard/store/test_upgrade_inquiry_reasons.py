"""Schema-24 inquiry and waiting facts retain their writer and event identity."""
from __future__ import annotations

import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest import mock

from blackboard.store.upgrade_fixture import load_fixture
from hey_my_buddy.blackboard.store.db import Database, canonical_json
from hey_my_buddy.blackboard.store.store import BoardStore
from hey_my_buddy.blackboard.store.upgrade import upgrade_schema_24_to_25
from hey_my_buddy.errors import BoardError


class InquiryReasonUpgradeTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix='upgrade-inquiry-'))
        self.fixture = load_fixture(self.root / 'state')
        self.db = sqlite3.connect(self.fixture['state'] / 'board.sqlite3')
        self.db.row_factory = sqlite3.Row
        self.addCleanup(self.db.close)
        self.task_id = self.fixture['stableIds']['delivered']
        self.attempt_id = self.db.execute('SELECT selected_attempt_id FROM tasks WHERE task_id=?',
                                          (self.task_id,)).fetchone()[0]

    def event(self, kind, payload, *, attempt_id=None):
        writer = mock.Mock()
        writer.now.return_value = 'upgrade-test'
        return BoardStore._append_event(writer, self.db, kind, task_id=self.task_id,
                                        attempt_id=attempt_id, payload=payload)

    def message(self, inquiry_id, state, reason, *, actor=None, answer=None, posted_state="queued",
                updated_state=None, updated_inquiry_id=None):
        message_id = 'message-' + inquiry_id
        self.db.execute(
            'INSERT INTO messages(message_id,task_id,attempt_id,inquiry_id,direction,author,body,'
            'body_bytes,payload_hash,state,reason,answer_json,created_at,updated_at) '
            'VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
            (message_id, self.task_id, self.attempt_id, inquiry_id, 'question', 'cli',
             'question', 8, 'hash', state, reason, canonical_json(answer) if answer else None,
             'upgrade-test', 'upgrade-test'))
        self.event('message.posted', {'inquiryId': inquiry_id, 'state': posted_state})
        if actor:
            self.event('message.updated', {'inquiryId': updated_inquiry_id or inquiry_id,
                                           'state': updated_state or state,
                                           'actor': {'role': actor[0], 'id': actor[1]}},
                       attempt_id=self.attempt_id)
        return message_id

    def upgrade(self):
        self.db.commit()
        upgrade_schema_24_to_25(self.fixture['state'])

    def test_program_reasons_follow_event_actor(self):
        cases = (
            ('finished', 'unavailable', 'the attempt ended before this question could be delivered',
             ('service', 'attempt-finished'), 'inquiry-queued-at-attempt-end', {}),
            ('observe', 'unavailable', 'this adapter observes native activity but has no correlated inquiry capability',
             ('bridge', 'live-bridge'), 'inquiry-observe-only', {}),
            ('empty', 'delivered', 'the bridge recorded an answered journal entry without usable answer text',
             ('bridge', 'bridge-journal'), 'inquiry-answer-empty', {}),
            ('closed', 'unavailable', 'the micro task root turn ended before this inquiry was answered',
             ('bridge', 'bridge-journal'), 'inquiry-root-turn-ended', {}),
            ('refused', 'unavailable', 'the bridge cannot deliver this question (agent-gone)',
             ('bridge', 'live-bridge'), 'bridge-refused', {'errorCode': 'agent-gone'}),
        )
        for inquiry_id, state, old, actor, _, _ in cases:
            self.message(inquiry_id, state, old, actor=actor)
        self.message('answered', 'answered', cases[2][2], actor=('bridge', 'bridge-journal'),
                     answer={'text': 'answer'})
        self.upgrade()
        for inquiry_id, _, _, _, code, params in cases:
            with self.subTest(inquiry=inquiry_id):
                row = self.db.execute('SELECT reason_code,reason_params_json FROM messages WHERE inquiry_id=?',
                                      (inquiry_id,)).fetchone()
                self.assertEqual((row['reason_code'], json.loads(row['reason_params_json'])),
                                 (code, params))
        answered = self.db.execute("SELECT reason_code,reason_params_json FROM messages WHERE inquiry_id='answered'").fetchone()
        self.assertEqual(tuple(answered), (None, None))
        self.assertEqual(self.db.execute('PRAGMA foreign_key_check').fetchall(), [])

    def test_posted_unavailable_has_the_post_writer_reason(self):
        self.message('no-live', 'unavailable', 'no live agent to receive this question', posted_state='unavailable')
        self.upgrade()
        self.assertEqual(self.db.execute("SELECT reason_code FROM messages WHERE inquiry_id='no-live'").fetchone()[0],
                         'inquiry-no-live-agent')

    def test_unknown_program_text_aborts_upgrade(self):
        self.message('old-prose', 'unavailable', 'an unknown bridge explanation', actor=('bridge', 'live-bridge'))
        with self.assertRaises(BoardError) as caught:
            self.upgrade()
        self.assertEqual(caught.exception.code, 'UPGRADE_INVALID_BOARD')

    def test_caller_text_aborts_upgrade(self):
        self.message('caller-prose', 'unavailable', 'Host-authored delivery explanation', actor=('bridge', 'cli'))
        with self.assertRaises(BoardError) as caught:
            self.upgrade()
        self.assertEqual(caught.exception.code, 'UPGRADE_INVALID_BOARD')

    def test_old_journal_limitation_and_claude_close_writers(self):
        # 0.42.0's journal importer selected these fixed limitations before its
        # reason, and stored the first 400 characters (both fit in that bound).
        dsh = ("Host questions reach this root only when the root itself calls hey_my_buddy_checkpoint inside the one "
               "admitted native turn (cooperative-checkpoint delivery); they are never injected mid-turn, never "
               "start a new turn, and an answer counts only through hey_my_buddy_answer_inquiry verified against this "
               "root turn's own tool evidence")
        zcode = ("Host questions are queued by the bridge and delivered only at the root's next hey_my_buddy_checkpoint "
                 "or finish refusal inside the same admitted native turn; they are never injected through "
                 "session/send, a v4 command, a stop, a restart or a new turn, and an answer counts only through "
                 "hey_my_buddy_answer_inquiry verified against the root turn's own tool evidence")
        claude = ("the micro task root turn ended before reading or answering this inquiry; "
                  "its queued follow-up was stopped")
        for inquiry_id, text in (('dsh', dsh), ('zcode', zcode), ('claude', claude)):
            self.message(inquiry_id, 'unavailable', text[:400], actor=('bridge', 'bridge-journal'))
        self.message('live-empty', 'delivered', 'the live bridge reported answered without usable answer text',
                     actor=('bridge', 'live-bridge'))
        self.upgrade()
        for inquiry_id in ('dsh', 'zcode', 'claude'):
            self.assertEqual(self.db.execute('SELECT reason_code FROM messages WHERE inquiry_id=?',
                                            (inquiry_id,)).fetchone()[0], 'inquiry-root-turn-ended')
        self.assertEqual(self.db.execute("SELECT reason_code FROM messages WHERE inquiry_id='live-empty'").fetchone()[0],
                         'inquiry-answer-empty')

    def test_answered_reason_is_cleared_without_needing_a_writer(self):
        self.message('answered-unattributed', 'answered', 'obsolete reason', answer={'text': 'late answer'})
        self.upgrade()
        row = self.db.execute("SELECT reason_code,reason_params_json FROM messages WHERE inquiry_id='answered-unattributed'").fetchone()
        self.assertEqual(tuple(row), (None, None))

    def test_live_closed_frame_without_reason_has_a_structured_reason(self):
        self.message('live-closed', 'unavailable', None, actor=('bridge', 'live-bridge'))
        self.upgrade()
        row = self.db.execute("SELECT reason_code,reason_params_json FROM messages WHERE inquiry_id='live-closed'").fetchone()
        self.assertEqual(tuple(row), ('inquiry-root-turn-ended', '{}'))

    def test_unavailable_without_a_matching_live_closed_event_aborts(self):
        cases = (
            ('absent', {}),
            ('wrong-actor', {'actor': ('bridge', 'bridge-journal')}),
            ('wrong-state', {'actor': ('bridge', 'live-bridge'), 'updated_state': 'delivered'}),
            ('wrong-inquiry', {'actor': ('bridge', 'live-bridge'), 'updated_inquiry_id': 'another-inquiry'}),
        )
        for inquiry_id, options in cases:
            with self.subTest(inquiry=inquiry_id):
                previous_seq = self.db.execute('SELECT COALESCE(MAX(seq), 0) FROM events').fetchone()[0]
                self.message(inquiry_id, 'unavailable', None, **options)
                with self.assertRaises(BoardError) as caught:
                    self.upgrade()
                self.assertEqual(caught.exception.code, 'UPGRADE_INVALID_BOARD')
                self.assertEqual(self.db.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0], '24')
                self.db.execute('DELETE FROM events WHERE seq>?', (previous_seq,))
                self.db.execute('DELETE FROM messages WHERE inquiry_id=?', (inquiry_id,))
                self.db.commit()

    def test_ended_attempt_queued_question_settles_without_new_event(self):
        self.message('old-queued', 'queued', None)
        self.db.execute("UPDATE attempts SET execution_state='finished' WHERE attempt_id=?", (self.attempt_id,))
        event_count = self.db.execute('SELECT COUNT(*) FROM events').fetchone()[0]
        self.upgrade()
        row = self.db.execute("SELECT state,reason_code,reason_params_json FROM messages WHERE inquiry_id='old-queued'").fetchone()
        self.assertEqual(tuple(row), ('unavailable', 'inquiry-queued-at-attempt-end', '{}'))
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM events').fetchone()[0], event_count)

    def test_unattributed_reason_aborts_upgrade(self):
        self.message('unattributed', 'unavailable', 'custom text')
        self.db.commit()
        with self.assertRaises(BoardError) as caught:
            upgrade_schema_24_to_25(self.fixture['state'])
        self.assertEqual(caught.exception.code, 'UPGRADE_INVALID_BOARD')
        self.assertEqual(self.db.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0], '24')

    def test_waiting_events_and_latest_activity_convert(self):
        waiting_seq = self.event('attempt.waiting', {'reason': 'router-input-unknown'}, attempt_id=self.attempt_id)
        null_seq = self.event('attempt.waiting', {'reason': None}, attempt_id=self.attempt_id)
        activity = {'eventSeq': 1, 'waitingReason': 'native permission.requested requires Host authority'}
        self.db.execute('INSERT INTO attempt_activity(attempt_id,task_id,generation,activity_json,created_at,updated_at)'
                        ' VALUES(?,?,?,?,?,?)', (self.attempt_id, self.task_id, 1, canonical_json(activity),
                                                'upgrade-test', 'upgrade-test'))
        activity_seq = self.event('attempt.activity', {'activity': {
            'eventSeq': 2, 'waitingReason': 'native userInput.requested requires Host authority'}},
            attempt_id=self.attempt_id)
        self.upgrade()
        self.assertEqual(json.loads(self.db.execute('SELECT payload_json FROM events WHERE seq=?',
                                                    (waiting_seq,)).fetchone()[0])['reason'],
                         {'code': 'router-input-unknown', 'params': {}})
        self.assertIsNone(json.loads(self.db.execute('SELECT payload_json FROM events WHERE seq=?',
                                                   (null_seq,)).fetchone()[0])['reason'])
        self.assertEqual(json.loads(self.db.execute('SELECT payload_json FROM events WHERE seq=?',
                                                    (activity_seq,)).fetchone()[0])['activity']['waitingReason'],
                         {'code': 'native-authority-required', 'params': {'kind': 'user-input'}})
        self.assertEqual(json.loads(self.db.execute('SELECT activity_json FROM attempt_activity WHERE attempt_id=?',
                                                    (self.attempt_id,)).fetchone()[0])['waitingReason'],
                         {'code': 'native-authority-required', 'params': {'kind': 'permission'}})

    def test_unknown_waiting_text_aborts_without_consuming_base(self):
        self.event('attempt.waiting', {'reason': 'unrecognized waiting condition'}, attempt_id=self.attempt_id)
        self.db.commit()
        with self.assertRaises(BoardError) as caught:
            upgrade_schema_24_to_25(self.fixture['state'])
        self.assertEqual(caught.exception.code, 'UPGRADE_INVALID_BOARD')
        self.assertEqual(self.db.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0], '24')

    def test_unknown_activity_table_waiting_text_aborts(self):
        self.db.execute('INSERT INTO attempt_activity(attempt_id,task_id,generation,activity_json,created_at,updated_at)'
                        ' VALUES(?,?,?,?,?,?)', (self.attempt_id, self.task_id, 1,
                            canonical_json({'waitingReason': 'native unknown requires Host authority'}), 'test', 'test'))
        with self.assertRaises(BoardError) as caught:
            self.upgrade()
        self.assertEqual(caught.exception.code, 'UPGRADE_INVALID_BOARD')

    def test_unknown_activity_event_waiting_text_aborts(self):
        self.event('attempt.activity', {'activity': {'waitingReason': 'unknown native request'}}, attempt_id=self.attempt_id)
        with self.assertRaises(BoardError) as caught:
            self.upgrade()
        self.assertEqual(caught.exception.code, 'UPGRADE_INVALID_BOARD')

    def test_messages_table_matches_fresh_schema(self):
        self.upgrade()
        fresh = Database(self.root / 'fresh')
        fresh.initialize()
        self.addCleanup(fresh.close)
        with fresh.connect() as reference:
            for name in ('messages', 'messages_task_idx', 'messages_state_idx'):
                self.assertEqual(self.db.execute('SELECT sql FROM sqlite_master WHERE name=?', (name,)).fetchone()[0],
                                 reference.execute('SELECT sql FROM sqlite_master WHERE name=?', (name,)).fetchone()[0])


if __name__ == '__main__':
    unittest.main()
