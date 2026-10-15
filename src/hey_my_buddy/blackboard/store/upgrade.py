"""The pending offline schema-21 to schema-22 board upgrade."""
from __future__ import annotations

from contextlib import closing
from pathlib import Path
import sqlite3

from ...errors import BoardError
from ...json_codec import canonical_json, decode_strict_json
from .db import REMINDER_SCHEMA, SCHEMA_VERSION, utc_now


BASE_SCHEMA_VERSION = 21


def migrated_stop_response(raw: str) -> str:
    """The exact schema-22 reply, shared by conversion and its expected snapshot."""
    try:
        old = decode_strict_json(raw)
    except (ValueError, RecursionError) as error:
        raise BoardError('UPGRADE_INVALID_BOARD', f'Invalid macro_task.stop receipt JSON: {error}') from error
    required = {'macroTaskId', 'runIds', 'acceptedRunIds', 'concludedRunIds', 'results', 'duplicate'}
    if not isinstance(old, dict) or set(old) != required or not isinstance(old['macroTaskId'], str) or not old['macroTaskId']:
        raise BoardError('UPGRADE_INVALID_BOARD', 'Invalid macro_task.stop receipt structure')
    if old['duplicate'] is not False or any(not isinstance(old[key], list) for key in
                                             ('runIds', 'acceptedRunIds', 'concludedRunIds', 'results')):
        raise BoardError('UPGRADE_INVALID_BOARD', 'Invalid macro_task.stop receipt lists or replay state')
    run_ids, accepted, concluded = (old[key] for key in ('runIds', 'acceptedRunIds', 'concludedRunIds'))
    all_ids = [*run_ids, *accepted, *concluded]
    if any(not isinstance(run_id, str) or not run_id for run_id in all_ids) or len(set(all_ids)) != len(all_ids):
        raise BoardError('UPGRADE_INVALID_BOARD', 'Invalid macro_task.stop receipt run identities')
    results = old['results']
    if len(results) != len(run_ids) or any(
        not isinstance(item, dict) or item.get('runId') != run_id or item.get('state') != 'cancelled'
        or item.get('cancelled') is not True or item.get('duplicate') is not False or 'stopped' in item
        for run_id, item in zip(run_ids, results)
    ):
        raise BoardError('UPGRADE_INVALID_BOARD', 'Invalid macro_task.stop receipt results')
    converted = [{'runId': item['runId'], 'state': item['state'], 'stopped': True} for item in results]
    converted.extend({'runId': run_id, 'state': 'accepted', 'stopped': False} for run_id in accepted)
    converted.extend({'runId': run_id, 'state': 'concluded', 'stopped': False} for run_id in concluded)
    return canonical_json({'macroTaskId': old['macroTaskId'], 'results': converted, 'duplicate': False})


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
            for command_id, raw in connection.execute(
                "SELECT command_id,response_json FROM commands WHERE kind='macro_task.stop'"
            ).fetchall():
                connection.execute('UPDATE commands SET response_json=? WHERE command_id=?',
                                   (migrated_stop_response(raw), command_id))
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
