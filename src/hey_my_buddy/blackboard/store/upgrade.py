"""The single pending board-schema upgrade, invoked by installation after backup."""
from __future__ import annotations

from contextlib import closing
from pathlib import Path
import sqlite3

from .db import MESSAGES_DDL, SCHEMA_VERSION, UPGRADE_BASE_SCHEMA
from ...errors import BoardError

RETAINED_MESSAGE_COLUMNS = (
    'message_id', 'task_id', 'attempt_id', 'inquiry_id', 'direction', 'body',
    'payload_hash', 'state', 'reason_code', 'reason_params_json',
    'delivery_json', 'answer_json', 'created_at', 'updated_at', 'revision',
)


def migrate_schema(state: Path) -> None:
    """Consume the single pending schema-27 upgrade after the rollback backup exists."""
    with closing(sqlite3.connect(state / 'board.sqlite3')) as connection:
        connection.execute('PRAGMA foreign_keys=ON')
        connection.execute('BEGIN IMMEDIATE')
        try:
            version = connection.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
            if version is None or version[0] not in (str(UPGRADE_BASE_SCHEMA), str(SCHEMA_VERSION)):
                raise BoardError('UNSUPPORTED_SCHEMA', 'Board schema cannot be upgraded')
            if version[0] == str(UPGRADE_BASE_SCHEMA):
                connection.execute('DROP INDEX messages_task_idx')
                connection.execute('DROP INDEX messages_state_idx')
                connection.execute('ALTER TABLE messages RENAME TO messages_schema27')
                for statement in MESSAGES_DDL.split(';'):
                    if statement.strip():
                        connection.execute(statement)
                columns = ','.join(RETAINED_MESSAGE_COLUMNS)
                connection.execute(f'INSERT INTO messages({columns}) SELECT {columns} FROM messages_schema27')
                connection.execute('DROP TABLE messages_schema27')
                connection.execute("UPDATE meta SET value=? WHERE key='schema_version'", (str(SCHEMA_VERSION),))
                if connection.execute('PRAGMA integrity_check').fetchall() != [('ok',)] or connection.execute('PRAGMA foreign_key_check').fetchall():
                    raise BoardError('UPGRADE_INVALID_BOARD', 'Migrated board integrity or foreign key checks failed')
            connection.execute('COMMIT')
        except BaseException:
            connection.rollback()
            raise

