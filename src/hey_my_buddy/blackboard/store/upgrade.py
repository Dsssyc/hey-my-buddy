"""The single pending board-schema upgrade, invoked by installation after backup."""
from __future__ import annotations

from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3

from .db import DB_FILE, EVENTS_ROUTER_CLAIMED_INDEX_DDL, SCHEMA_VERSION, UPGRADE_BASE_SCHEMA
from ...errors import BoardError

# Task and attempt identity already live in the event columns.
ACTIVITY_EVENT_PAYLOAD = '{}'
EVENT_COLUMNS = ('seq', 'task_id', 'attempt_id', 'revision', 'kind', 'payload_json',
                 'actor_role', 'actor_id', 'created_at')


def expected_events_fingerprint(connection, *, event_head: int) -> str:
    """Hash upgraded event rows in the same bounded sorted format as the backup."""
    def encode(*values):
        row = list(values)
        if row[4] == 'attempt.activity':
            row[5] = ACTIVITY_EVENT_PAYLOAD
        return json.dumps(row, ensure_ascii=False, separators=(',', ':'))

    connection.create_function('hmb_upgrade_event_row', len(EVENT_COLUMNS), encode)
    try:
        cursor = connection.execute('SELECT hmb_upgrade_event_row('
                                    + ','.join(EVENT_COLUMNS)
                                    + ') AS encoded FROM events WHERE seq<=? ORDER BY encoded COLLATE BINARY',
                                    (event_head,))
        digest = hashlib.sha256()
        separator = b''
        for row in cursor:
            digest.update(separator)
            digest.update(row[0].encode())
            separator = b'\n'
        return digest.hexdigest()
    finally:
        connection.create_function('hmb_upgrade_event_row', len(EVENT_COLUMNS), None)


def migrate_schema(state: Path) -> None:
    """Consume the single pending schema-28 upgrade after a verified backup."""
    with closing(sqlite3.connect(state / DB_FILE)) as connection:
        connection.execute('PRAGMA foreign_keys=ON')
        connection.execute('BEGIN IMMEDIATE')
        try:
            version = connection.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
            if version is None or version[0] not in (str(UPGRADE_BASE_SCHEMA), str(SCHEMA_VERSION)):
                raise BoardError('UNSUPPORTED_SCHEMA', 'Board schema cannot be upgraded')
            if version[0] == str(UPGRADE_BASE_SCHEMA):
                connection.execute("UPDATE events SET payload_json=? WHERE kind='attempt.activity'",
                                   (ACTIVITY_EVENT_PAYLOAD,))
                connection.execute('DROP INDEX events_router_claimed_idx')
                connection.execute(EVENTS_ROUTER_CLAIMED_INDEX_DDL)
                connection.execute("UPDATE meta SET value=? WHERE key='schema_version'", (str(SCHEMA_VERSION),))
            if connection.execute('PRAGMA integrity_check').fetchall() != [('ok',)] or connection.execute('PRAGMA foreign_key_check').fetchall():
                raise BoardError('UPGRADE_INVALID_BOARD', 'Migrated board integrity or foreign key checks failed')
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
