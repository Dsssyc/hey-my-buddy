"""The single explicit upgrade from the released 0.30.0 schema-17 board."""
from __future__ import annotations

import json
import sqlite3

from .db import PREVIOUS_SCHEMA_VERSION, SCHEMA_VERSION, WORKFLOW_TURNS_DDL
from ...errors import BoardError


def schema_version(connection: sqlite3.Connection) -> int:
    row = connection.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
    return int(row[0]) if row else 0


def migrate_17_to_18(connection: sqlite3.Connection) -> dict:
    """Freeze each historical turn's owning-run access in one transaction."""
    if schema_version(connection) != PREVIOUS_SCHEMA_VERSION:
        raise BoardError('UPGRADE_MIGRATION_FAILED', 'Migration expects schema 17', tables=['meta'])
    foreign_keys = connection.execute('PRAGMA foreign_keys').fetchone()[0]
    connection.execute('PRAGMA foreign_keys=OFF')
    try:
        connection.execute('BEGIN IMMEDIATE')
        return _migrate_17_to_18_transaction(connection)
    finally:
        connection.execute(f'PRAGMA foreign_keys={foreign_keys}')


def _migrate_17_to_18_transaction(connection: sqlite3.Connection) -> dict:
    from .backup import database_snapshot
    from ..catalog.catalog import permission_capabilities
    from .db import canonical_json

    tables = ['meta']
    try:
        original_meta = dict(connection.execute('SELECT key,value FROM meta'))
        planned_meta = {**original_meta, 'schema_version': str(SCHEMA_VERSION)}
        old_columns = [row[1] for row in connection.execute('PRAGMA table_info(workflow_turns)')]
        access_index = old_columns.index('state')
        turn_columns = old_columns[:access_index] + ['access', 'access_reason'] + old_columns[access_index:]
        planned_turns = []
        for row in connection.execute(
            'SELECT t.*, r.execution_workspace_json FROM workflow_turns AS t'
            ' LEFT JOIN workflow_runs AS r ON r.run_id=t.run_id ORDER BY t.turn_id'
        ):
            values = tuple(row)
            workspace = json.loads(values[-1])
            access = workspace.get('access') if isinstance(workspace, dict) else None
            if access not in ('read', 'write'):
                raise ValueError(f'Unsupported historical turn access: {access}')
            planned_turns.append(values[:access_index] + (access, None) + values[access_index:-1])
        profile_columns = [row[1] for row in connection.execute('PRAGMA table_info(evaluation_profiles)')]
        profile_cap_index = profile_columns.index('capabilities_json')
        profile_adapter_index = profile_columns.index('adapter')
        profile_id_index = profile_columns.index('profile_id')
        permissions = {name: permission_capabilities(name) for name in
                       {row[0] for row in connection.execute('SELECT DISTINCT adapter FROM evaluation_profiles')}}
        planned_profiles = []
        for row in connection.execute('SELECT * FROM evaluation_profiles ORDER BY profile_id'):
            values = list(row)
            capabilities = [item for item in json.loads(values[profile_cap_index]) if not item.startswith('permission:')]
            values[profile_cap_index] = canonical_json(capabilities + permissions[values[profile_adapter_index]])
            planned_profiles.append(tuple(values))
        retained_tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        retained_tables -= {'meta', 'workflow_turns', 'evaluation_profiles'}
        retained = database_snapshot(connection, exact=True, table_names=retained_tables)

        tables = ['workflow_turns']
        connection.execute(WORKFLOW_TURNS_DDL.replace('workflow_turns (', 'workflow_turns_18 (', 1))
        placeholders = ','.join('?' for _ in turn_columns)
        connection.executemany(
            f"INSERT INTO workflow_turns_18({','.join(turn_columns)}) VALUES({placeholders})",
            planned_turns,
        )
        connection.execute('DROP TABLE workflow_turns')
        connection.execute('ALTER TABLE workflow_turns_18 RENAME TO workflow_turns')
        connection.execute('CREATE INDEX workflow_turns_run_idx ON workflow_turns(run_id, turn_index)')
        connection.execute('CREATE INDEX workflow_turns_attempt_idx ON workflow_turns(attempt_id)')
        tables = ['evaluation_profiles']
        connection.executemany('UPDATE evaluation_profiles SET capabilities_json=? WHERE profile_id=?',
                               [(row[profile_cap_index], row[profile_id_index]) for row in planned_profiles])
        tables = ['meta']
        connection.execute("UPDATE meta SET value=? WHERE key='schema_version'", (str(SCHEMA_VERSION),))

        actual = database_snapshot(connection, exact=True, table_names=retained_tables)
        changed = [name for name in retained['fingerprints']
                   if retained['fingerprints'][name] != actual['fingerprints'].get(name)
                   or retained['tables'][name] != actual['tables'].get(name)]
        changed += [name for name in retained.get('exemptFingerprints', {})
                    if retained['exemptFingerprints'][name] != actual['exemptFingerprints'].get(name)]
        if dict(connection.execute('SELECT key,value FROM meta')) != planned_meta:
            changed.append('meta')
        if [tuple(row) for row in connection.execute('SELECT * FROM workflow_turns ORDER BY turn_id')] != planned_turns:
            changed.append('workflow_turns')
        if [tuple(row) for row in connection.execute('SELECT * FROM evaluation_profiles ORDER BY profile_id')] != planned_profiles:
            changed.append('evaluation_profiles')
        if changed:
            raise BoardError('UPGRADE_MIGRATION_FAILED', 'Migration changed values outside its exact plan', tables=sorted(set(changed)))
        violations = connection.execute('PRAGMA foreign_key_check').fetchall()
        if violations:
            raise BoardError('UPGRADE_MIGRATION_FAILED', 'Foreign key check failed after migration', tables=sorted({row[0] for row in violations}))
        integrity = [tuple(row) for row in connection.execute('PRAGMA integrity_check')]
        if integrity != [('ok',)]:
            raise BoardError('UPGRADE_MIGRATION_FAILED', 'Integrity check failed after migration', tables=[], integrity=integrity)
        connection.execute('COMMIT')
    except BaseException as error:
        connection.execute('ROLLBACK')
        if isinstance(error, (sqlite3.Error, ValueError, TypeError)):
            raise BoardError('UPGRADE_MIGRATION_FAILED', str(error), tables=tables) from error
        raise
    return {'fromSchema': PREVIOUS_SCHEMA_VERSION, 'toSchema': SCHEMA_VERSION,
            'migratedTurns': len(planned_turns), 'migratedProfiles': len(planned_profiles)}
