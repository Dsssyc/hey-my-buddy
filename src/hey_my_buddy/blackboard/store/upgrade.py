"""The pending offline schema-21 to schema-22 board upgrade."""
from __future__ import annotations

from contextlib import closing
from pathlib import Path
import sqlite3

from ...errors import BoardError
from .db import REMINDER_SCHEMA, SCHEMA_VERSION, utc_now


BASE_SCHEMA_VERSION = 21


def migrate(state: Path) -> None:
    """Extend a stopped schema-21 board after its backup has been verified."""
    board = Path(state) / 'board.sqlite3'
    with closing(sqlite3.connect(board, isolation_level=None)) as connection:
        connection.execute('PRAGMA foreign_keys=ON')
        connection.execute('BEGIN IMMEDIATE')
        try:
            version = connection.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
            if version is None or version[0] != str(BASE_SCHEMA_VERSION):
                raise BoardError('UNSUPPORTED_SCHEMA', f'Expected schema {BASE_SCHEMA_VERSION} for the pending upgrade')
            retained_meta = dict(connection.execute("SELECT key,value FROM meta WHERE key!='schema_version'"))
            existing = connection.execute(
                "SELECT name FROM sqlite_master WHERE name IN "
                "('reminder_settings','attempt_reminders','attempt_reminders_expected_idx',"
                "'attempt_reminders_silence_idx')"
            ).fetchall()
            if existing:
                raise BoardError('UPGRADE_INVALID_BOARD', 'Schema-21 board already contains reminder structure')
            for statement in REMINDER_SCHEMA.split(';'):
                if statement.strip():
                    connection.execute(statement)
            connection.execute(
                'INSERT INTO reminder_settings(id, silence_seconds, updated_at, revision) VALUES(1, 900, ?, 1)',
                (utc_now(),),
            )
            connection.execute("UPDATE meta SET value=? WHERE key='schema_version'", (str(SCHEMA_VERSION),))
            if dict(connection.execute("SELECT key,value FROM meta WHERE key!='schema_version'")) != retained_meta:
                raise BoardError('UPGRADE_INVALID_BOARD', 'Schema conversion changed retained board metadata')
            if connection.execute('PRAGMA foreign_key_check').fetchall():
                raise BoardError('UPGRADE_INVALID_BOARD', 'Foreign keys failed after schema conversion')
            if connection.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                raise BoardError('UPGRADE_INVALID_BOARD', 'SQLite integrity failed after schema conversion')
            connection.execute('COMMIT')
        except BaseException:
            connection.execute('ROLLBACK')
            raise
