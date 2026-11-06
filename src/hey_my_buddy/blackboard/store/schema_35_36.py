"""Schema 35 → 36: exploration facts and accepted-delivery defect counts.

This step owns its DDL and conversion. Existing routes predate exploration, so
they record ``pre-exploration``; existing settings take the default quota
interval of 5. Nothing else a route or the settings row holds is rewritten.
"""
from __future__ import annotations

import json


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
    'kind_statistics': """CREATE TABLE IF NOT EXISTS kind_statistics (
    harness TEXT NOT NULL,
    model TEXT NOT NULL,
    effort TEXT NOT NULL,
    samples INTEGER NOT NULL CHECK (typeof(samples)='integer' AND samples > 0),
    successes INTEGER NOT NULL CHECK (typeof(successes)='integer' AND successes >= 0),
    first_passes INTEGER NOT NULL CHECK (typeof(first_passes)='integer' AND first_passes >= 0 AND first_passes <= successes),
    rework_rounds INTEGER NOT NULL CHECK (typeof(rework_rounds)='integer' AND rework_rounds >= 0),
    failures INTEGER NOT NULL CHECK (typeof(failures)='integer' AND failures >= 0),
    defects INTEGER NOT NULL DEFAULT 0 CHECK (typeof(defects)='integer' AND defects >= 0 AND defects <= successes),
    PRIMARY KEY (harness, model, effort),
    CHECK (successes + failures = samples)
)""",
    'kind_cell_statistics': """CREATE TABLE IF NOT EXISTS kind_cell_statistics (
    harness TEXT NOT NULL,
    model TEXT NOT NULL,
    effort TEXT NOT NULL,
    domain_node_id TEXT NOT NULL REFERENCES domain_nodes(node_id) ON DELETE RESTRICT,
    task_type TEXT NOT NULL CHECK (task_type IN ('create','change','debug','verify','review','research','write')),
    samples INTEGER NOT NULL CHECK (typeof(samples)='integer' AND samples > 0),
    successes INTEGER NOT NULL CHECK (typeof(successes)='integer' AND successes >= 0),
    first_passes INTEGER NOT NULL CHECK (typeof(first_passes)='integer' AND first_passes >= 0 AND first_passes <= successes),
    rework_rounds INTEGER NOT NULL CHECK (typeof(rework_rounds)='integer' AND rework_rounds >= 0),
    failures INTEGER NOT NULL CHECK (typeof(failures)='integer' AND failures >= 0),
    defects INTEGER NOT NULL DEFAULT 0 CHECK (typeof(defects)='integer' AND defects >= 0 AND defects <= successes),
    PRIMARY KEY (harness, model, effort, domain_node_id, task_type),
    CHECK (successes + failures = samples)
)""",
}

INDEX_DDL = (
    """CREATE INDEX workflow_routes_run_idx ON workflow_routes(run_id)""",
    # The exploration counter seeks the last quota-carrying route and the
    # counted routes after it; both steps read this covering index (its
    # implicit rowid tail included) instead of scanning the routing history.
    """CREATE INDEX workflow_routes_exploration_idx ON workflow_routes(exploration)""",
)


def _rows(connection, table):
    return [dict(row) for row in connection.execute(f'SELECT rowid,* FROM "{table}" ORDER BY rowid')]


def _rebuild(connection, table, rows):
    connection.execute(f'DROP TABLE "{table}"')
    connection.execute(TABLE_DDL[table])
    columns = ['rowid', *(row[1] for row in connection.execute(f'PRAGMA table_info("{table}")'))]
    insert = f'INSERT INTO "{table}"({",".join(columns)}) VALUES({",".join("?" for _ in columns)})'
    connection.executemany(insert, ([row.get(column) for column in columns] for row in rows))


def _accepted_kind(connection, run_id):
    """Resolve the kind that owned the accepted delivery from its frozen input."""
    row = connection.execute(
        "SELECT w.input_json FROM workflow_runs r JOIN workflow_turns w"
        " ON w.run_id=r.run_id AND w.attempt_id=r.final_attempt_id"
        " WHERE r.run_id=? AND r.state='accepted' ORDER BY w.turn_index DESC LIMIT 1",
        (run_id,)).fetchone()
    if row is None or row["input_json"] is None:
        raise ValueError(f"Schema 35 → 36: fixed micro task {run_id} has no accepted turn input")
    buddy = json.loads(row["input_json"])["context"]["buddy"]
    return tuple(buddy[field] for field in ("harness", "model", "effort"))


def _accumulate_defects(connection):
    """Backfill one defect per accepted target, preserving its kind and cell."""
    fixed_runs = connection.execute(
        "SELECT DISTINCT fixed_run_id FROM workflow_fixes ORDER BY fixed_run_id").fetchall()
    for row in fixed_runs:
        run_id = row["fixed_run_id"]
        kind = _accepted_kind(connection, run_id)
        updated = connection.execute(
            "UPDATE kind_statistics SET defects=defects+1"
            " WHERE harness=? AND model=? AND effort=?", kind)
        if updated.rowcount != 1:
            raise ValueError(f"Schema 35 → 36: fixed micro task {run_id} has no overall sample")
        classification = connection.execute(
            "SELECT domain_node_id,task_type FROM micro_task_classifications WHERE run_id=?",
            (run_id,)).fetchone()
        if classification is not None:
            updated = connection.execute(
                "UPDATE kind_cell_statistics SET defects=defects+1"
                " WHERE harness=? AND model=? AND effort=? AND domain_node_id=? AND task_type=?",
                (*kind, classification["domain_node_id"], classification["task_type"]))
            if updated.rowcount != 1:
                raise ValueError(f"Schema 35 → 36: fixed micro task {run_id} has no classified sample")


def upgrade(connection):
    stock = {table: _rows(connection, table) for table in TABLE_DDL}
    for row in stock['workflow_routes']:
        row['exploration'] = 'pre-exploration'
    for row in stock['router_settings']:
        row['exploration_every'] = 5
    for table in ('kind_statistics', 'kind_cell_statistics'):
        for row in stock[table]:
            row['defects'] = 0
    for table, rows in stock.items():
        _rebuild(connection, table, rows)
    for ddl in INDEX_DDL:
        connection.execute(ddl)
    _accumulate_defects(connection)
    connection.execute("UPDATE meta SET value='36' WHERE key='schema_version'")
