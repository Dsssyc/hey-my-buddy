"""Settings carried from the schema-18 upgrade base into a fresh board."""
from __future__ import annotations

import json
import sqlite3

ROUTER_KEYS = ('router_profile_ids', 'router_default_mode', 'router_budget_preset',
               'router_retry_interval_seconds')
SETTINGS_TABLES = ('evaluation_profiles', 'evaluation_preferences',
                   'family_annotations', 'model_concurrency', 'harness_health')
SETTINGS_COLUMNS = {
    'evaluation_profiles': ('profile_id', 'label', 'adapter', 'provider', 'model', 'effort',
                            'available', 'enabled', 'capabilities_json', 'context_window',
                            'description', 'source', 'unavailable_reason'),
    'evaluation_preferences': ('profile_id', 'mode', 'reason'),
    'family_annotations': ('adapter', 'provider', 'model', 'text', 'updated_at'),
    'model_concurrency': ('adapter', 'provider', 'model', 'concurrency_limit', 'created_at', 'updated_at'),
    'harness_health': ('adapter', 'manual_path', 'revision', 'status', 'record_json',
                       'checked_at', 'expires_at'),
}


def read_settings(connection: sqlite3.Connection) -> dict:
    values = dict(connection.execute(
        'SELECT key,value FROM meta WHERE key IN (?,?,?,?)', ROUTER_KEYS))
    tables = {name: [tuple(row) for row in connection.execute(
        'SELECT ' + ','.join(SETTINGS_COLUMNS[name]) + ' FROM ' + name)]
              for name in SETTINGS_TABLES if name != 'evaluation_preferences'}
    # Read the upgrade base's composed policy once. A buddy override wins over
    # its model service default, including an explicit absence of preference.
    tables['evaluation_preferences'] = [tuple(row) for row in connection.execute(
        'SELECT profile_id,mode,reason FROM effective_preferences ORDER BY profile_id')]
    return {
        'router': {
            'routerProfileIds': json.loads(values['router_profile_ids']),
            'defaultRoutingMode': values['router_default_mode'],
            'routingBudget': values['router_budget_preset'],
            'routerRetryIntervalSeconds': int(values['router_retry_interval_seconds']),
        },
        'tables': tables,
    }


def write_settings(connection: sqlite3.Connection, settings: dict) -> None:
    from ..routing.router import _write_settings
    _write_settings(connection, settings['router'])
    for name in SETTINGS_TABLES:
        rows = settings['tables'][name]
        if rows:
            connection.executemany('INSERT INTO ' + name + '(' + ','.join(SETTINGS_COLUMNS[name]) + ')'
                                   ' VALUES(' + ','.join('?' for _ in SETTINGS_COLUMNS[name]) + ')', rows)
    # Catalog history is archived, so its unknown-account diagnosis is no
    # longer a fact about the fresh board's retained buddy settings.
    connection.execute("UPDATE evaluation_profiles SET unavailable_reason=NULL WHERE unavailable_reason='ACCOUNT_STATUS_UNKNOWN'")
