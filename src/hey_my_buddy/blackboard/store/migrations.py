"""Settings carried from the schema-18 upgrade base into a fresh board."""
from __future__ import annotations

import sqlite3

ROUTER_KEYS = ('router_profile_ids', 'router_default_mode', 'router_budget_preset',
               'router_retry_interval_seconds')
SETTINGS_TABLES = ('evaluation_profiles', 'evaluation_preferences', 'family_preferences',
                   'family_annotations', 'model_concurrency', 'harness_health')


def read_settings(connection: sqlite3.Connection) -> dict:
    return {
        'router': list(connection.execute(
            'SELECT key,value FROM meta WHERE key IN (?,?,?,?) ORDER BY key', ROUTER_KEYS)),
        'tables': {name: [tuple(row) for row in connection.execute('SELECT * FROM ' + name)]
                   for name in SETTINGS_TABLES},
        'evaluationRevisions': tuple(connection.execute(
            'SELECT table_revision,configuration_revision FROM evaluation_state WHERE id=1').fetchone()),
    }


def write_settings(connection: sqlite3.Connection, settings: dict) -> None:
    connection.executemany('UPDATE meta SET value=? WHERE key=?',
                           [(value, key) for key, value in settings['router']])
    for name in SETTINGS_TABLES:
        rows = settings['tables'][name]
        if rows:
            connection.executemany('INSERT INTO ' + name + ' VALUES(' + ','.join('?' for _ in rows[0]) + ')', rows)
    connection.execute('UPDATE evaluation_state SET table_revision=?,configuration_revision=? WHERE id=1',
                       settings['evaluationRevisions'])
