"""Settings carried from the schema-18 upgrade base into a fresh board."""
from __future__ import annotations

import sqlite3

ROUTER_KEYS = ('router_profile_ids', 'router_default_mode', 'router_budget_preset',
               'router_retry_interval_seconds')
SETTINGS_TABLES = ('evaluation_profiles', 'evaluation_preferences', 'family_preferences',
                   'family_annotations', 'model_concurrency', 'harness_health')
SETTINGS_COLUMNS = {
    'evaluation_profiles': ('profile_id', 'label', 'adapter', 'provider', 'model', 'effort',
                            'available', 'enabled', 'capabilities_json', 'context_window',
                            'description', 'source', 'unavailable_reason'),
    'evaluation_preferences': ('profile_id', 'mode', 'reason'),
    'family_preferences': ('adapter', 'provider', 'model', 'mode', 'reason'),
    'family_annotations': ('adapter', 'provider', 'model', 'text', 'updated_at'),
    'model_concurrency': ('adapter', 'provider', 'model', 'concurrency_limit', 'created_at', 'updated_at'),
    'harness_health': ('adapter', 'manual_path', 'revision', 'status', 'record_json',
                       'checked_at', 'expires_at', 'scan_after'),
}


def read_settings(connection: sqlite3.Connection) -> dict:
    return {
        'router': list(connection.execute(
            'SELECT key,value FROM meta WHERE key IN (?,?,?,?) ORDER BY key', ROUTER_KEYS)),
        'tables': {name: [tuple(row) for row in connection.execute(
            'SELECT ' + ','.join(SETTINGS_COLUMNS[name]) + ' FROM ' + name)]
                   for name in SETTINGS_TABLES},
    }


def write_settings(connection: sqlite3.Connection, settings: dict) -> None:
    connection.executemany('UPDATE meta SET value=? WHERE key=?',
                           [(value, key) for key, value in settings['router']])
    for name in SETTINGS_TABLES:
        rows = settings['tables'][name]
        if rows:
            connection.executemany('INSERT INTO ' + name + ' VALUES(' + ','.join('?' for _ in rows[0]) + ')', rows)
