"""The one pending, offline upgrade from the 0.40.0 board to schema 24."""
from __future__ import annotations

from contextlib import closing
import json
from pathlib import Path
import sqlite3

from ...errors import BoardError
from .db import SCHEMA_VERSION


UPGRADE_BASE_SCHEMA = 23


def upgrade_schema_23_to_24(state: Path) -> None:
    """Convert a stopped schema-23 board after its exact rollback backup exists."""
    from ..tasks.command_receipts import stored_response

    with closing(sqlite3.connect(Path(state) / 'board.sqlite3')) as connection:
        connection.execute('PRAGMA foreign_keys=ON')
        connection.execute('BEGIN IMMEDIATE')
        try:
            row = connection.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
            if row is None or row[0] != str(UPGRADE_BASE_SCHEMA) or SCHEMA_VERSION != 24:
                raise BoardError('UNSUPPORTED_SCHEMA', 'The pending upgrade accepts schema 23 and writes schema 24')
            for command_id, kind, raw in connection.execute(
                "SELECT command_id,kind,response_json FROM commands"
                " WHERE kind NOT IN ('execution.result','harness.prepare')"
            ):
                original = json.loads(raw)
                response = stored_response(kind, original)
                if response != original:
                    updated = json.dumps(response, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
                    connection.execute('UPDATE commands SET response_json=? WHERE command_id=?', (updated, command_id))
            connection.execute("DELETE FROM commands WHERE kind='execution.result'")
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
