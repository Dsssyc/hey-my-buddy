"""Pending-upgrade inquiry settlement and exact installation preservation."""
from contextlib import nullcontext
import hashlib
import json
import sqlite3
from unittest import mock

from blackboard.store.upgrade_fixture import load_fixture
from support import BoardTestCase
from hey_my_buddy.blackboard.store import upgrade as migration
from hey_my_buddy.blackboard.store.db import SCHEMA_VERSION
from hey_my_buddy.errors import BoardError
from hey_my_buddy.install import upgrade


AT = '2026-10-22T12:00:00.000Z'
DELIVERY = {'deliveredAt': '2026-10-21T12:00:00.000Z', 'via': 'fixture-checkpoint'}
ANSWER = {'text': '历史答复', 'via': 'tool:hey_my_buddy_answer_inquiry',
          'toolCallId': 'answer-tool', 'at': '2026-10-21T12:01:00.000Z'}


class InquiryUpgradeTests(BoardTestCase):
    def stock(self):
        state = self.directory / 'base'
        load_fixture(state)
        connection = sqlite3.connect(state / 'board.sqlite3')
        connection.row_factory = sqlite3.Row
        self.addCleanup(connection.close)
        attempt = connection.execute(
            "SELECT a.*,t.turn_id FROM attempts a JOIN workflow_turns t ON t.attempt_id=a.attempt_id "
            "WHERE a.execution_state='finished' ORDER BY a.attempt_id LIMIT 1").fetchone()
        identity = {'taskId': attempt['task_id'], 'attemptId': attempt['attempt_id'],
                    'generation': attempt['generation'], 'turnId': attempt['turn_id']}
        template = dict(connection.execute('SELECT * FROM messages LIMIT 1').fetchone())
        connection.execute('DELETE FROM messages')
        path = state / 'attempts' / attempt['task_id'] / attempt['attempt_id'] / 'inquiry.results.jsonl'
        path.parent.mkdir(parents=True, exist_ok=True)
        return state, connection, identity, template, path

    def message(self, connection, identity, template, name, *, state='unavailable', code='inquiry-queued-at-attempt-end', delivery=None):
        row = {**template, 'message_id': name, 'inquiry_id': name,
               'task_id': identity['taskId'], 'attempt_id': identity['attemptId'],
               'body': name, 'payload_hash': hashlib.sha256(name.encode()).hexdigest(),
               'state': state, 'reason_code': code, 'reason_params_json': '{}' if code else None,
               'delivery_json': json.dumps(delivery) if delivery else None, 'answer_json': None,
               'revision': 7, 'updated_at': '2026-10-20T00:00:00.000Z'}
        connection.execute('INSERT INTO messages(' + ','.join(row) + ') VALUES('
                           + ','.join('?' for _ in row) + ')', tuple(row.values()))
        return row

    def records(self, identity, name, state='answered', **fields):
        return [dict(version=1, **identity, inquiryId=name, state='queued', question=name,
                     questionSha256=hashlib.sha256(name.encode()).hexdigest()),
                dict(version=1, **identity, inquiryId=name, state=state, **fields)]

    def test_finished_stock_replays_by_own_attempt_and_preserves_untouched_bytes(self):
        state, connection, identity, template, path = self.stock()
        originals = {}
        for name, options in {
                'answered': {}, 'delivered': {'state': 'claimed', 'code': None},
                'board-delivered': {'state': 'delivered', 'code': None, 'delivery': DELIVERY},
                'missing-entry': {}, 'wrong-generation': {}, 'wrong-turn': {}, 'wrong-task': {},
                'wrong-attempt': {}, 'wrong-digest': {},
                'rejected-at-admission': {'code': 'inquiry-root-turn-ended'},
                'queued': {'state': 'queued', 'code': None}}.items():
            originals[name] = self.message(connection, identity, template, name, **options)
        records = self.records(identity, 'answered', answer=ANSWER, delivery=DELIVERY)
        records += self.records(identity, 'delivered', state='delivered', delivery=DELIVERY)
        for name, key, value in [('wrong-generation', 'generation', identity['generation'] + 1),
                                 ('wrong-turn', 'turnId', 'foreign-turn'),
                                 ('wrong-task', 'taskId', 'foreign-task'),
                                 ('wrong-attempt', 'attemptId', 'foreign-attempt')]:
            records += self.records({**identity, key: value}, name, answer=ANSWER, delivery=DELIVERY)
        wrong = self.records(identity, 'wrong-digest', answer=ANSWER)
        wrong[0]['question'] = 'foreign question'
        wrong[0]['questionSha256'] = hashlib.sha256(b'foreign question').hexdigest()
        records += wrong
        records += self.records(identity, 'rejected-at-admission', answer=ANSWER)
        connection.execute("UPDATE messages SET reason_params_json='{  }' WHERE message_id='missing-entry'")
        originals['missing-entry']['reason_params_json'] = '{  }'
        path.write_text('\n'.join(json.dumps(row) for row in records) + '\n')
        log_bytes = path.read_bytes()
        # The message still belongs to its own attempt when no task selects it.
        connection.execute('UPDATE tasks SET selected_attempt_id=NULL WHERE task_id=?', (identity['taskId'],))
        connection.commit()
        events = [tuple(row) for row in connection.execute('SELECT * FROM events ORDER BY seq')]
        plan = migration.plan_inquiry_settlement(connection, state, AT)
        self.assertEqual(plan['counts']['journal-rejected'], 5)
        self.assertEqual(plan['counts']['answered'], 1)
        self.assertEqual(plan['counts']['delivered'], 2)
        self.assertEqual(plan['counts']['changed'], 4)
        expected = migration.expected_messages_fingerprint(connection, plan['updates'])
        migration.migrate_schema(state, inquiry_settlement_at=AT)
        self.assertEqual(upgrade.idle_snapshot(state)['fingerprints']['messages'], expected)
        rows = {row['message_id']: dict(row) for row in connection.execute('SELECT * FROM messages')}
        answered = rows['answered']
        self.assertEqual(answered['state'], 'answered')
        self.assertIsNone(answered['reason_code'])
        self.assertIsNone(answered['reason_params_json'])
        self.assertEqual(json.loads(answered['delivery_json']), DELIVERY)
        self.assertEqual(json.loads(answered['answer_json']), {**ANSWER, 'bytes': len(ANSWER['text'].encode()),
                                                            'source': 'bridge-journal', 'truncated': False})
        for name in ('delivered', 'board-delivered'):
            self.assertEqual(rows[name]['state'], 'unavailable')
            self.assertEqual(rows[name]['reason_code'], 'inquiry-delivered-at-attempt-end')
            self.assertEqual(json.loads(rows[name]['delivery_json']), DELIVERY)
        self.assertEqual(rows['queued']['reason_code'], 'inquiry-queued-at-attempt-end')
        for name in originals:
            if name in ('answered', 'delivered', 'board-delivered', 'queued'):
                self.assertEqual(rows[name]['revision'], 8)
                self.assertEqual(rows[name]['updated_at'], AT)
            else:
                self.assertEqual({key: rows[name][key] for key in migration.RETAINED_MESSAGE_COLUMNS},
                                 {key: originals[name][key] for key in migration.RETAINED_MESSAGE_COLUMNS})
        self.assertEqual([tuple(row) for row in connection.execute('SELECT * FROM events ORDER BY seq')], events)
        self.assertEqual(path.read_bytes(), log_bytes)
        before = [tuple(row) for row in connection.execute('SELECT * FROM messages ORDER BY message_id')]
        migration.migrate_schema(state, inquiry_settlement_at='2027-01-01T00:00:00.000Z')
        self.assertEqual([tuple(row) for row in connection.execute('SELECT * FROM messages ORDER BY message_id')], before)

    def test_missing_unreadable_and_malformed_journals_settle_without_fabricated_delivery(self):
        state, connection, identity, template, path = self.stock()
        self.message(connection, identity, template, 'undelivered', state='queued', code=None)
        self.message(connection, identity, template, 'delivered', state='delivered', code=None, delivery=DELIVERY)
        connection.commit()
        for kind in ('missing', 'unreadable', 'malformed', 'oversized', 'nonfinite', 'deeply-nested',
                     'invalid-answer-utf8', 'surrogate-delivery', 'duplicate-members'):
            with self.subTest(kind=kind):
                path.unlink(missing_ok=True)
                if kind != 'missing':
                    path.write_bytes(b'broken\xffjson\n' if kind == 'malformed' else b'x' * (1048577 if kind == 'oversized' else 1))
                if kind == 'nonfinite':
                    records = self.records(identity, 'undelivered', delivery={'nativeCorrelation': float('nan')})
                    path.write_text(json.dumps({**records[0], **records[-1]}) + '\n')
                elif kind == 'deeply-nested':
                    path.write_text('[' * 2000 + '0' + ']' * 2000 + '\n')
                elif kind == 'invalid-answer-utf8':
                    records = self.records(identity, 'undelivered', answer={'text': 'bad-byte'})
                    path.write_bytes((json.dumps({**records[0], **records[-1]})
                                      .replace('bad-byte', 'bad-CHAR') + '\n').encode().replace(b'CHAR', b'\xff'))
                elif kind == 'surrogate-delivery':
                    records = self.records(identity, 'undelivered', delivery={'via': '\ud800'})
                    path.write_text(json.dumps({**records[0], **records[-1]}) + '\n')
                elif kind == 'duplicate-members':
                    records = self.records(identity, 'undelivered', answer={'text': 'original'})
                    path.write_text(json.dumps({**records[0], **records[-1]}).replace(
                        '"text": "original"', '"text": "first", "text": "second"') + '\n')
                with mock.patch('hey_my_buddy.protocol.inquiry_journal.read_shared_snapshot', return_value=None) if kind == 'unreadable' else nullcontext():
                    plan = migration.plan_inquiry_settlement(connection, state, AT)
                counted = ('journal-missing' if kind == 'missing' else
                           'journal-no-record' if kind in ('malformed', 'nonfinite', 'deeply-nested',
                                                          'invalid-answer-utf8', 'surrogate-delivery',
                                                          'duplicate-members') else 'journal-unreadable')
                self.assertEqual(plan['counts'][counted], 2)
                self.assertEqual(plan['updates']['undelivered']['reason_code'], 'inquiry-queued-at-attempt-end')
                self.assertEqual(plan['updates']['delivered']['reason_code'], 'inquiry-delivered-at-attempt-end')

    def test_installation_rejects_changed_unconverted_and_converted_rows(self):
        state, connection, identity, template, path = self.stock()
        self.message(connection, identity, template, 'changed')
        self.message(connection, identity, template, 'untouched', code='inquiry-root-turn-ended')
        path.write_text('\n'.join(json.dumps(row) for row in self.records(identity, 'changed', answer=ANSWER, delivery=DELIVERY)) + '\n')
        connection.commit()
        before = upgrade.idle_snapshot(state, inquiry_settlement_at=AT)
        migration.migrate_schema(state, inquiry_settlement_at=AT)
        target = self.directory / ('a' * 32)
        health = {'runtimeContentId': target.name, 'runtimeStable': True, 'schemaVersion': SCHEMA_VERSION}
        with mock.patch.object(upgrade, 'command', return_value={'leaks': []}):
            self.assertTrue(upgrade.verify_started(state, target, health, before)['retainedDataFingerprints'])
        for name, column, value in [('untouched', 'body', 'rewritten'), ('changed', 'answer_json', '{}'),
                                    ('changed', 'delivery_json', '{}'), ('changed', 'revision', 99),
                                    ('changed', 'updated_at', 'foreign-time'),
                                    ('changed', 'author_role', 'host'), ('changed', 'state', 'discarded'),
                                    ('untouched', 'reason_code', 'inquiry-observe-only')]:
            with self.subTest(name=name, column=column):
                original = dict(connection.execute('SELECT * FROM messages WHERE message_id=?', (name,)).fetchone())
                changes = {column: value, **({'author_id': 'foreign-host'} if column == 'author_role' else {})}
                connection.execute('UPDATE messages SET ' + ','.join(key + '=?' for key in changes)
                                   + ' WHERE message_id=?', (*changes.values(), name))
                connection.commit()
                with self.assertRaises(BoardError) as caught:
                    upgrade.verify_started(state, target, health, before)
                self.assertEqual(caught.exception.code, 'UPGRADE_VERIFY_FAILED')
                self.assertEqual(caught.exception.details['tables'], ['messages'])
                connection.execute('UPDATE messages SET ' + ','.join(key + '=?' for key in changes)
                                   + ' WHERE message_id=?', (*(original[key] for key in changes), name))
                connection.commit()
        changed = dict(connection.execute("SELECT * FROM messages WHERE message_id='changed'").fetchone())
        connection.execute("DELETE FROM messages WHERE message_id='changed'")
        connection.commit()
        with self.assertRaises(BoardError) as caught:
            upgrade.verify_started(state, target, health, before)
        self.assertEqual(caught.exception.details['tables'], ['messages'])
        connection.execute('INSERT INTO messages(' + ','.join(changed) + ') VALUES('
                           + ','.join('?' for _ in changed) + ')', tuple(changed.values()))
        extra = {**changed, 'message_id': 'extra-row', 'inquiry_id': 'extra-row'}
        connection.execute('INSERT INTO messages(' + ','.join(extra) + ') VALUES('
                           + ','.join('?' for _ in extra) + ')', tuple(extra.values()))
        connection.commit()
        with self.assertRaises(BoardError) as caught:
            upgrade.verify_started(state, target, health, before)
        self.assertEqual(caught.exception.details['tables'], ['messages'])

    def test_unfinished_attempts_are_not_converted(self):
        state, connection, identity, template, path = self.stock()
        self.message(connection, identity, template, 'active', state='queued', code=None)
        path.write_text('\n'.join(json.dumps(row) for row in self.records(identity, 'active', answer=ANSWER)) + '\n')
        connection.execute("UPDATE attempts SET execution_state='executing' WHERE attempt_id=?", (identity['attemptId'],))
        connection.commit()
        original = dict(connection.execute("SELECT * FROM messages WHERE message_id='active'").fetchone())
        migration.migrate_schema(state, inquiry_settlement_at=AT)
        actual = dict(connection.execute("SELECT * FROM messages WHERE message_id='active'").fetchone())
        self.assertEqual({key: actual[key] for key in migration.RETAINED_MESSAGE_COLUMNS},
                         {key: original[key] for key in migration.RETAINED_MESSAGE_COLUMNS})

    def test_a_failure_after_rebuild_and_message_write_rolls_back_the_whole_upgrade(self):
        state, connection, identity, template, path = self.stock()
        self.message(connection, identity, template, 'queued', state='queued', code=None)
        connection.commit()
        before = list(connection.iterdump())
        original_connect = sqlite3.connect

        class FailingConnection(sqlite3.Connection):
            def execute(self, sql, parameters=()):
                result = super().execute(sql, parameters)
                if sql.startswith('UPDATE messages SET '):
                    raise RuntimeError('injected failure after message write')
                return result

        with mock.patch.object(migration.sqlite3, 'connect', side_effect=lambda *args, **kwargs:
                               original_connect(*args, **kwargs, factory=FailingConnection)):
            with self.assertRaisesRegex(RuntimeError, 'injected failure'):
                migration.migrate_schema(state, inquiry_settlement_at=AT)
        self.assertEqual(list(connection.iterdump()), before)

