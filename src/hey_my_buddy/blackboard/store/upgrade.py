"""The one pending, offline upgrade from the 0.40.0 board to schema 24."""
from __future__ import annotations

from contextlib import closing
import json
from pathlib import Path
import sqlite3

from ...errors import BoardError
from ...json_codec import canonical_json


UPGRADE_BASE_SCHEMA = 23


def upgrade_schema_23_to_24(state: Path) -> None:
    """Convert a stopped schema-23 board after its exact rollback backup exists."""
    from ..tasks.command_receipts import stored_response

    with closing(sqlite3.connect(Path(state) / 'board.sqlite3')) as connection:
        connection.execute('PRAGMA foreign_keys=ON')
        connection.execute('BEGIN IMMEDIATE')
        try:
            row = connection.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
            if row is None or row[0] != str(UPGRADE_BASE_SCHEMA):
                raise BoardError('UNSUPPORTED_SCHEMA', 'The pending upgrade accepts schema 23 and writes schema 24')
            connection.execute("DELETE FROM commands WHERE kind='execution.result'")
            commands = connection.execute(
                'SELECT command_id,kind,response_json FROM commands'
            ).fetchall()
            for command_id, kind, raw in commands:
                original = json.loads(raw)
                response = stored_response(kind, original)
                if response != original:
                    connection.execute('UPDATE commands SET response_json=? WHERE command_id=?',
                                       (canonical_json(response), command_id))
            connection.execute('ALTER TABLE attempts DROP COLUMN result_command_id')
            connection.execute(
                "DELETE FROM events WHERE kind='attempt.activity' AND seq NOT IN ("
                "SELECT MAX(seq) FROM events WHERE kind='attempt.activity' GROUP BY attempt_id)"
            )
            connection.execute(
                "DELETE FROM events WHERE kind IN ('harness.checked','harness.probe_unknown',"
                " 'harness.account_observed') AND seq NOT IN ("
                "SELECT MAX(seq) FROM events WHERE kind IN ('harness.checked','harness.probe_unknown',"
                " 'harness.account_observed') GROUP BY kind,json_extract(payload_json,'$.harness'))"
            )
            connection.execute("UPDATE meta SET value='24' WHERE key='schema_version'")
            if connection.execute('PRAGMA foreign_key_check').fetchall():
                raise BoardError('UPGRADE_INVALID_BOARD', 'The upgraded board has foreign key violations')
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
