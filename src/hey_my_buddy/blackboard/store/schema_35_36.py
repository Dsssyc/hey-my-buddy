"""Schema 35 → 36: the exploration fact on routing records and Router settings.

This step owns its DDL and conversion. Existing routes predate exploration, so
they record ``pre-exploration``; existing settings take the default quota
interval of 5. Nothing else a route or the settings row holds is rewritten.
"""
from __future__ import annotations


TABLE_DDL = {
    'workflow_routes': """CREATE TABLE workflow_routes (
    decision_id         TEXT PRIMARY KEY REFERENCES decision_requests(decision_id) ON DELETE RESTRICT,
    run_id              TEXT NOT NULL REFERENCES workflow_runs(run_id) ON DELETE RESTRICT,
    state               TEXT NOT NULL CHECK (state IN ('pending','resolved','needs-host','fenced')),
    exploration         TEXT NOT NULL CHECK (exploration IN ('pre-exploration','ineligible','counted','quota-kept','quota-reselected')),
    created_at          TEXT NOT NULL
)""",
    'router_settings': """CREATE TABLE router_settings (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    default_mode TEXT NOT NULL CHECK (default_mode IN ('fast','review')),
    budget_preset TEXT NOT NULL CHECK (budget_preset IN ('brief','standard','deep')),
    retry_interval_seconds INTEGER NOT NULL CHECK (retry_interval_seconds >= 1)
, maintenance_buddy_id TEXT, maintenance_interval_seconds INTEGER NOT NULL DEFAULT 3600 CHECK (maintenance_interval_seconds >= 1)
, exploration_every INTEGER DEFAULT 5 CHECK (exploration_every IS NULL OR exploration_every >= 2));""",
}

INDEX_DDL = (
    """CREATE INDEX workflow_routes_run_idx ON workflow_routes(run_id)""",
)


def _rows(connection, table):
    return [dict(row) for row in connection.execute(f'SELECT rowid,* FROM "{table}" ORDER BY rowid')]


def _rebuild(connection, table, rows):
    connection.execute(f'DROP TABLE "{table}"')
    connection.execute(TABLE_DDL[table])
    columns = ['rowid', *(row[1] for row in connection.execute(f'PRAGMA table_info("{table}")'))]
    insert = f'INSERT INTO "{table}"({",".join(columns)}) VALUES({",".join("?" for _ in columns)})'
    connection.executemany(insert, ([row.get(column) for column in columns] for row in rows))


def upgrade(connection):
    stock = {table: _rows(connection, table) for table in TABLE_DDL}
    for row in stock['workflow_routes']:
        row['exploration'] = 'pre-exploration'
    for row in stock['router_settings']:
        row['exploration_every'] = 5
    for table, rows in stock.items():
        _rebuild(connection, table, rows)
    for ddl in INDEX_DDL:
        connection.execute(ddl)
    connection.execute("UPDATE meta SET value='36' WHERE key='schema_version'")
