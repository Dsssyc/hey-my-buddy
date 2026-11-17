"""Schema 40 → 41: record paused work units and explicit execution stop purpose."""
from __future__ import annotations


WORK_PAUSES_DDL = """
CREATE TABLE IF NOT EXISTS work_pauses (
    macro_task_id TEXT REFERENCES macro_tasks(macro_task_id) ON DELETE RESTRICT,
    run_id        TEXT REFERENCES workflow_runs(run_id) ON DELETE RESTRICT,
    paused_at     TEXT NOT NULL,
    CHECK ((macro_task_id IS NULL) <> (run_id IS NULL)),
    UNIQUE (macro_task_id),
    UNIQUE (run_id)
);
"""


ATTEMPTS_DDL = """
CREATE TABLE IF NOT EXISTS attempts (
    attempt_id          TEXT PRIMARY KEY,
    task_id             TEXT NOT NULL REFERENCES tasks(task_id) ON DELETE RESTRICT,
    generation          INTEGER NOT NULL,
    nonce_verifier      TEXT NOT NULL,
    execution_state     TEXT NOT NULL
        CHECK (execution_state IN ('starting','executing','finalizing','uncertain','finished')),
    ownership           TEXT NOT NULL DEFAULT 'owned' CHECK (ownership IN ('owned','uncertain')),
    runtime_identity    TEXT,
    adapter             TEXT NOT NULL,
    -- Frozen model-service identity (harness/provider/model, no effort dimension).
    -- Written once by the claim transaction that resolved it and never updated
    -- afterwards, so a historical or shutdown-uncertain attempt can never change
    -- which model service's concurrency slot it occupies. NULL for model-less work
    -- (commands) which counts only against the machine-wide ceiling.
    model_harness       TEXT,
    model_provider      TEXT,
    model_model         TEXT,
    model_id            TEXT REFERENCES models(model_id) ON DELETE RESTRICT,
    result_json         TEXT,
    result_form         TEXT CHECK (result_form IN ('archived-report-v1','current-report-v2')),
    shutdown_confirmed  INTEGER NOT NULL DEFAULT 0,
    cancel_requested_at TEXT,
    stop_initiator      TEXT CHECK (stop_initiator IN ('host','lead','console','service','unknown')),
    stop_purpose        TEXT CHECK (stop_purpose IN ('pause','end')),
    grace_deadline      TEXT,
    force_reason        TEXT,
    force_requested_at  TEXT,
    identity_confirmed_at TEXT,
    cleanup_pending     INTEGER NOT NULL DEFAULT 0 CHECK (cleanup_pending IN (0,1)),
    started_at          TEXT,
    finished_at         TEXT,
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL,
    revision            INTEGER NOT NULL DEFAULT 1,
    CHECK ((result_json IS NULL AND result_form IS NULL)
        OR (result_json IS NOT NULL AND result_form IS NOT NULL)),
    UNIQUE(task_id, generation)
);
"""

ATTEMPTS_INDEXES = """
CREATE UNIQUE INDEX IF NOT EXISTS attempts_effective_unique ON attempts(task_id)
    WHERE execution_state IN ('starting','executing','finalizing','uncertain');
CREATE INDEX IF NOT EXISTS attempts_active_idx ON attempts(execution_state);
CREATE INDEX IF NOT EXISTS attempts_model_service_idx
    ON attempts(model_harness, model_provider, model_model) WHERE model_model IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS attempts_router_binding_unique ON attempts(attempt_id, task_id, generation);
CREATE UNIQUE INDEX IF NOT EXISTS attempts_router_outcome_binding_unique ON attempts(attempt_id, task_id);
"""


def upgrade(connection):
    # Every pre-0.69 attempt keeps its existing facts and a NULL purpose,
    # including legacy console cancellations. No event implies a new purpose.
    rows = [dict(row) for row in connection.execute('SELECT rowid,* FROM attempts ORDER BY rowid')]
    connection.execute('DROP TABLE attempts')
    connection.execute(ATTEMPTS_DDL)
    columns = ['rowid', *(row[1] for row in connection.execute('PRAGMA table_info(attempts)'))]
    connection.executemany(
        f'INSERT INTO attempts({",".join(columns)}) VALUES({",".join("?" for _ in columns)})',
        ([None if column == 'stop_purpose' else row[column] for column in columns] for row in rows))
    for statement in ATTEMPTS_INDEXES.split(';'):
        if statement.strip():
            connection.execute(statement)
    connection.execute(WORK_PAUSES_DDL)
    connection.execute("UPDATE meta SET value='41' WHERE key='schema_version'")
