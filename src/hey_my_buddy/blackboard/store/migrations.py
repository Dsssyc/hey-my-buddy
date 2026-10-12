"""The single explicit upgrade from the frozen 0.29.0 schema-16 board.

SQL retirement has no filesystem, credential or process side effects.
"""
from __future__ import annotations

import sqlite3

from .db import SCHEMA_VERSION, PREVIOUS_SCHEMA_VERSION
from ...errors import BoardError

NEW_TABLES = frozenset({'quota_current', 'harness_quota_observations', 'harness_account_reads'})
RETIRED_META_PREFIXES = (
    'account-selection:', 'account-credential:', 'account-mutation:', 'attempt-account:',
    'account-operation:', 'account-quota:', 'account-read-at:', 'attempt-harness:', 'catalog-accounts:',
    'catalog-account:', 'attempt-routing-accounts:', 'quota-routing:', 'quota-retry:',
    'catalog-read-at:', 'catalog-account-status:', 'catalog-scan-after:', 'catalog-harnesses:',
)
RETIRED_META_KEYS = frozenset({'codex_account_read_at'})

_NEW_DDL = (
    ("quota_current", """CREATE TABLE quota_current (
    adapter TEXT NOT NULL,
    provider TEXT NOT NULL,
    limit_id TEXT NOT NULL,
    blocked INTEGER NOT NULL CHECK (blocked IN (0,1)),
    code TEXT,
    source TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    last_observed_at TEXT NOT NULL,
    resets_at TEXT,
    native_account_id TEXT,
    retry_manual_at TEXT,
    retry_consumed_at TEXT,
    retry_consumed_by TEXT,
    PRIMARY KEY (adapter, provider, limit_id),
    CHECK ((blocked=1 AND code IS NOT NULL AND code IN ('HARNESS_BALANCE_ZERO','HARNESS_QUOTA_EXHAUSTED'))
           OR (blocked=0 AND code IS NULL))
);"""),
    ("harness_quota_observations", """CREATE TABLE harness_quota_observations (
    adapter TEXT PRIMARY KEY,
    quota_json TEXT NOT NULL
);"""),
    ("harness_account_reads", """CREATE TABLE harness_account_reads (
    adapter TEXT PRIMARY KEY,
    checked_at TEXT NOT NULL
);"""),
)
_HEALTH_DDL = """CREATE TABLE harness_health_17 (
    adapter TEXT PRIMARY KEY CHECK (adapter IN ('dsh','zcode','codex','claude')),
    manual_path TEXT,
    revision INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'unknown'
        CHECK (status IN ('unknown','ready','missing','login-required','unhealthy')),
    record_json TEXT NOT NULL DEFAULT '{}',
    checked_at TEXT,
    expires_at TEXT,
    scan_after TEXT
)"""


def retired_meta(key: str) -> bool:
    return key in RETIRED_META_KEYS or key.startswith(RETIRED_META_PREFIXES)


def schema_version(connection: sqlite3.Connection) -> int:
    row = connection.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
    return int(row[0]) if row else 0


def migrate_16_to_17(connection: sqlite3.Connection) -> dict:
    """Retire account state atomically; preserve all sealed execution history."""
    if schema_version(connection) != PREVIOUS_SCHEMA_VERSION:
        raise BoardError('UPGRADE_MIGRATION_FAILED', 'Migration expects schema 16', tables=['meta'])
    foreign_keys = connection.execute('PRAGMA foreign_keys').fetchone()[0]
    connection.execute('PRAGMA foreign_keys=OFF')
    try:
        connection.execute('BEGIN IMMEDIATE')
        return _migrate_16_to_17_transaction(connection)
    finally:
        connection.execute(f'PRAGMA foreign_keys={foreign_keys}')


def _migrate_16_to_17_transaction(connection: sqlite3.Connection) -> dict:
    from .backup import database_snapshot

    tables = ['meta']
    try:
        original_meta = dict(connection.execute('SELECT key,value FROM meta'))
        planned_meta = {key: value for key, value in original_meta.items() if not retired_meta(key)}
        planned_meta['schema_version'] = str(SCHEMA_VERSION)
        health = [tuple(row) for row in connection.execute(
            'SELECT adapter,manual_path,revision FROM harness_health ORDER BY adapter')]
        planned_health = [(adapter, manual_path, revision, 'unknown', '{}', None, None, None)
                          for adapter, manual_path, revision in health]
        planned_catalog = [(row[0], 0, None, 'unknown', 'ACCOUNT_STATUS_UNKNOWN', row[1])
                           for row in connection.execute('SELECT adapter,updated_at FROM catalog_current ORDER BY adapter')]
        profile_columns = [row[1] for row in connection.execute('PRAGMA table_info(evaluation_profiles)')]
        planned_profiles = [tuple(0 if column == 'available' else 'ACCOUNT_STATUS_UNKNOWN' if column == 'unavailable_reason' else value
                                  for column, value in zip(profile_columns, row))
                            for row in connection.execute('SELECT * FROM evaluation_profiles ORDER BY profile_id')]
        retained_tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        retained_tables -= {'meta', 'harness_health', 'catalog_current', 'catalog_pending', 'evaluation_profiles'}
        retained = database_snapshot(connection, exact=True, table_names=retained_tables)
        for table, statement in _NEW_DDL:
            tables = [table]
            connection.execute(statement)
        tables = ['harness_health']
        connection.execute(_HEALTH_DDL)
        connection.executemany('INSERT INTO harness_health_17 VALUES(?,?,?,?,?,?,?,?)', planned_health)
        connection.execute('DROP TABLE harness_health')
        connection.execute('ALTER TABLE harness_health_17 RENAME TO harness_health')
        tables = ['catalog_current']
        connection.execute("UPDATE catalog_current SET observation_id=0,discovery_id=NULL,status='unknown',reason='ACCOUNT_STATUS_UNKNOWN'")
        tables = ['evaluation_profiles']
        connection.execute("UPDATE evaluation_profiles SET available=0,unavailable_reason='ACCOUNT_STATUS_UNKNOWN'")
        tables = ['catalog_pending']
        connection.execute('DELETE FROM catalog_pending')
        tables = ['meta']
        connection.executemany('DELETE FROM meta WHERE key=?', [(key,) for key in original_meta if retired_meta(key)])
        connection.execute("UPDATE meta SET value=? WHERE key='schema_version'", (str(SCHEMA_VERSION),))
        actual = database_snapshot(connection, exact=True, table_names=retained_tables)
        changed = [name for name in retained['fingerprints']
                   if retained['fingerprints'][name] != actual['fingerprints'].get(name)
                   or retained['tables'][name] != actual['tables'].get(name)]
        changed += [name for name in retained.get('exemptFingerprints', {})
                    if retained['exemptFingerprints'][name] != actual['exemptFingerprints'].get(name)]
        if dict(connection.execute('SELECT key,value FROM meta')) != planned_meta:
            changed.append('meta')
        if [tuple(row) for row in connection.execute('SELECT * FROM harness_health ORDER BY adapter')] != planned_health:
            changed.append('harness_health')
        if [tuple(row) for row in connection.execute('SELECT * FROM catalog_current ORDER BY adapter')] != planned_catalog:
            changed.append('catalog_current')
        if [tuple(row) for row in connection.execute('SELECT * FROM evaluation_profiles ORDER BY profile_id')] != planned_profiles:
            changed.append('evaluation_profiles')
        for table in NEW_TABLES | {'catalog_pending'}:
            if connection.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0]:
                changed.append(table)
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
        if isinstance(error, (sqlite3.Error, ValueError)):
            raise BoardError('UPGRADE_MIGRATION_FAILED', str(error), tables=tables) from error
        raise
    return {'fromSchema': PREVIOUS_SCHEMA_VERSION, 'toSchema': SCHEMA_VERSION,
            'newTables': sorted(NEW_TABLES), 'catalogUnknown': [row[0] for row in planned_catalog]}
