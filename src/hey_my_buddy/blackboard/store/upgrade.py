"""The one pending, offline upgrade from the 0.42.0 board to schema 25."""
from __future__ import annotations

from contextlib import closing
from pathlib import Path
import sqlite3

from .db import COMMAND_DELETION_COLUMN, MACRO_TASK_DELETIONS_DDL
from ...errors import BoardError


UPGRADE_BASE_SCHEMA = 24


def upgrade_schema_24_to_25(state: Path) -> None:
    """Convert a stopped schema-24 board after its exact rollback backup exists."""
    with closing(sqlite3.connect(Path(state) / 'board.sqlite3')) as connection:
        connection.execute('PRAGMA foreign_keys=ON')
        connection.execute('BEGIN IMMEDIATE')
        try:
            row = connection.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
            if row is None or row[0] != str(UPGRADE_BASE_SCHEMA):
                raise BoardError('UNSUPPORTED_SCHEMA', 'The pending upgrade accepts schema 24 and writes schema 25')
            connection.execute(MACRO_TASK_DELETIONS_DDL)
            connection.execute('ALTER TABLE commands ADD COLUMN ' + COMMAND_DELETION_COLUMN)
            connection.execute("UPDATE meta SET value='25' WHERE key='schema_version'")
            if connection.execute('PRAGMA foreign_key_check').fetchall():
                raise BoardError('UPGRADE_INVALID_BOARD', 'The upgraded board has foreign key violations')
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
