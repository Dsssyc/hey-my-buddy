"""The single pending board-schema upgrade, invoked by installation after backup."""
from __future__ import annotations

from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3

from .db import MESSAGES_DDL, SCHEMA_VERSION, UPGRADE_BASE_SCHEMA, utc_now
from ...errors import BoardError
from ...json_codec import canonical_json
from ...protocol.inquiry_journal import read_inquiry_journal, merged_inquiry_records, normalize_inquiry_record

RETAINED_MESSAGE_COLUMNS = (
    'message_id', 'task_id', 'attempt_id', 'inquiry_id', 'direction', 'body',
    'payload_hash', 'state', 'reason_code', 'reason_params_json',
    'delivery_json', 'answer_json', 'created_at', 'updated_at', 'revision',
)
UPGRADED_MESSAGE_COLUMNS = (*RETAINED_MESSAGE_COLUMNS[:5], 'author_role', 'author_id',
                            *RETAINED_MESSAGE_COLUMNS[5:])


def plan_inquiry_settlement(connection, state: Path, at: str) -> dict:
    """Derive the one-time stock conversion without changing the board or journals."""
    cursor = connection.execute(
        "SELECT m.*,a.generation,(SELECT turn_id FROM workflow_turns "
        "WHERE attempt_id=a.attempt_id LIMIT 1) AS turn_id FROM messages m "
        "JOIN attempts a ON a.attempt_id=m.attempt_id AND a.task_id=m.task_id "
        "WHERE a.execution_state='finished' AND (m.state IN ('queued','claimed','delivered') "
        "OR (m.state='unavailable' AND m.reason_code='inquiry-queued-at-attempt-end')) "
        "ORDER BY m.attempt_id,m.message_id")
    columns = [item[0] for item in cursor.description]
    rows = [dict(zip(columns, row)) for row in cursor]
    journals = {}
    updates = {}
    counts = {'candidates': len(rows), 'changed': 0, 'unchanged': 0,
              'answered': 0, 'delivered': 0, 'queued': 0,
              'journal-missing': 0, 'journal-unreadable': 0,
              'journal-no-record': 0, 'journal-rejected': 0}
    for row in rows:
        attempt_id = row['attempt_id']
        if attempt_id not in journals:
            identity = {'taskId': row['task_id'], 'attemptId': attempt_id,
                        'generation': row['generation'], 'turnId': row['turn_id']}
            path = state / 'attempts' / row['task_id'] / attempt_id / 'inquiry.results.jsonl'
            fact = read_inquiry_journal(identity, path)
            journals[attempt_id] = (fact, merged_inquiry_records(fact['records']),
                                    {item['questionId'] for item in fact['rejections']})
        fact, records, rejected = journals[attempt_id]
        record = records.get(row['inquiry_id'])
        patch = None
        if not fact['available']:
            counts['journal-missing' if fact['reason'] == 'journal-not-written' else 'journal-unreadable'] += 1
        elif row['inquiry_id'] in rejected:
            counts['journal-rejected'] += 1
        elif record is None:
            counts['journal-no-record'] += 1
        elif record.get('questionSha256') != row['payload_hash']:
            counts['journal-rejected'] += 1
        else:
            patch = normalize_inquiry_record(record, was_delivered=row['state'] == 'delivered')
            if patch is None:
                counts['journal-rejected'] += 1
        delivery = json.loads(row['delivery_json']) if row['delivery_json'] else None
        answer = json.loads(row['answer_json']) if row['answer_json'] else None
        if patch:
            if patch.get('delivery'):
                delivery = {**(delivery or {}), **patch['delivery']}
            if patch.get('answer') and answer is None:
                answer = patch['answer']
        if answer is not None:
            final_state, code, params = 'answered', None, None
            counts['answered'] += 1
        elif row['state'] == 'delivered' or (delivery or {}).get('deliveredAt') or (patch or {}).get('state') == 'delivered':
            final_state, code, params = 'unavailable', 'inquiry-delivered-at-attempt-end', '{}'
            counts['delivered'] += 1
        else:
            final_state, code, params = 'unavailable', 'inquiry-queued-at-attempt-end', '{}'
            counts['queued'] += 1
        values = {'state': final_state, 'reason_code': code, 'reason_params_json': params,
                  'delivery_json': canonical_json(delivery) if delivery else None,
                  'answer_json': canonical_json(answer) if answer else None}
        # Match the current writer's semantic comparison: unchanged JSON keeps
        # its original spelling and does not manufacture a revision.
        if (row['state'] == final_state and row['reason_code'] == code
                and (json.loads(row['reason_params_json']) if row['reason_params_json'] else None)
                == ({} if params else None)
                and (json.loads(row['delivery_json']) if row['delivery_json'] else None) == delivery
                and (json.loads(row['answer_json']) if row['answer_json'] else None) == answer):
            counts['unchanged'] += 1
            continue
        updates[row['message_id']] = {**values, 'revision': row['revision'] + 1, 'updated_at': at}
        counts['changed'] += 1
    return {'updates': updates, 'counts': counts}


def expected_messages_fingerprint(connection, updates: dict) -> str:
    """Hash every future message column, including untouched rows and new NULL authors.

    SQLite sorts the same encoded row format as backup.database_snapshot; only
    changed rows are retained in Python, never the complete message table.
    """
    def encode(*values):
        row = dict(zip(RETAINED_MESSAGE_COLUMNS, values))
        row.update(updates.get(row['message_id'], {}))
        row.update(author_role=None, author_id=None)
        return json.dumps([row[key] for key in UPGRADED_MESSAGE_COLUMNS],
                          ensure_ascii=False, separators=(',', ':'))

    connection.create_function('hmb_upgrade_message_row', len(RETAINED_MESSAGE_COLUMNS), encode)
    try:
        cursor = connection.execute('SELECT hmb_upgrade_message_row('
                                    + ','.join(RETAINED_MESSAGE_COLUMNS)
                                    + ') AS encoded FROM messages ORDER BY encoded COLLATE BINARY')
        digest = hashlib.sha256()
        separator = b''
        for row in cursor:
            digest.update(separator)
            digest.update(row[0].encode())
            separator = b'\n'
        return digest.hexdigest()
    finally:
        connection.create_function('hmb_upgrade_message_row', len(RETAINED_MESSAGE_COLUMNS), None)


def migrate_schema(state: Path, *, inquiry_settlement_at: str | None = None) -> None:
    """Consume the single pending schema-27 upgrade after the rollback backup exists."""
    with closing(sqlite3.connect(state / 'board.sqlite3')) as connection:
        connection.execute('PRAGMA foreign_keys=ON')
        connection.execute('BEGIN IMMEDIATE')
        try:
            version = connection.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
            if version is None or version[0] not in (str(UPGRADE_BASE_SCHEMA), str(SCHEMA_VERSION)):
                raise BoardError('UNSUPPORTED_SCHEMA', 'Board schema cannot be upgraded')
            if version[0] == str(UPGRADE_BASE_SCHEMA):
                plan = plan_inquiry_settlement(connection, state, inquiry_settlement_at or utc_now())
                connection.execute('DROP INDEX messages_task_idx')
                connection.execute('DROP INDEX messages_state_idx')
                connection.execute('ALTER TABLE messages RENAME TO messages_schema27')
                for statement in MESSAGES_DDL.split(';'):
                    if statement.strip():
                        connection.execute(statement)
                columns = ','.join(RETAINED_MESSAGE_COLUMNS)
                connection.execute(f'INSERT INTO messages({columns}) SELECT {columns} FROM messages_schema27')
                connection.execute('DROP TABLE messages_schema27')
                for message_id, values in plan['updates'].items():
                    connection.execute('UPDATE messages SET ' + ','.join(key + '=?' for key in values)
                                       + ' WHERE message_id=?', (*values.values(), message_id))
                connection.execute("UPDATE meta SET value=? WHERE key='schema_version'", (str(SCHEMA_VERSION),))
                if connection.execute('PRAGMA integrity_check').fetchall() != [('ok',)] or connection.execute('PRAGMA foreign_key_check').fetchall():
                    raise BoardError('UPGRADE_INVALID_BOARD', 'Migrated board integrity or foreign key checks failed')
            connection.execute('COMMIT')
        except BaseException:
            connection.rollback()
            raise

