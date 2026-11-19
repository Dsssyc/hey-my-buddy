"""Schema 41 → 42: relationally constrained attempt waiting and connection reasons."""
from __future__ import annotations


TASKS_DDL = """
CREATE TABLE "tasks" (
    task_id                 TEXT PRIMARY KEY,
    request_id              TEXT NOT NULL UNIQUE,
    spec_json               TEXT NOT NULL,
    input_fingerprint       TEXT NOT NULL,
    fingerprint_version     INTEGER NOT NULL,
    adapter                 TEXT NOT NULL,
    cwd                     TEXT NOT NULL,
    state                   TEXT NOT NULL
        CHECK (state IN ('queued','running','cancelling','completed','concluded','failed','cancelled','reconciliation-needed')),
    queue_reason            TEXT,
    revision                INTEGER NOT NULL,
    selected_attempt_id     TEXT,
    active_attempt_id       TEXT,
    created_at              TEXT NOT NULL,
    updated_at              TEXT NOT NULL,
    accepted_at             TEXT,
    acceptance_note         TEXT,
    review_rejected_at      TEXT,
    review_rejection_note   TEXT,
    CHECK ((adapter='decision' AND json_extract(spec_json,'$.expectedSeconds') IS NULL)
        OR (adapter!='decision' AND json_type(spec_json,'$.expectedSeconds') IS 'integer'
            AND json_extract(spec_json,'$.expectedSeconds') > 0)),
    -- A running task's reason lives on its active attempt row; the task's own
    -- reason is the queued writer's fact and never coexists with an attempt.
    CHECK (active_attempt_id IS NULL OR queue_reason IS NULL),
    CHECK (queue_reason IS NULL OR queue_reason NOT IN ('attempt-uncertain-after-restart','execution-connection-unknown','harness-probe-unknown','router-input-unknown'))
);
"""

TASKS_INDEXES = """
CREATE INDEX IF NOT EXISTS tasks_state_idx ON tasks(state, created_at);
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
    -- The attempt's own waiting facts, each owned by exactly one writer: the
    -- connection reason while the execution's liveness is unresolved, an unknown
    -- harness probe observed before or without a reconnect, and the Router's
    -- control wait. A restart-marked uncertain attempt and a live-session
    -- disconnect are distinguished by the reason itself.
    connection_reason TEXT CHECK (connection_reason IN ('attempt-uncertain-after-restart','execution-connection-unknown')),
    probe_unknown     INTEGER NOT NULL DEFAULT 0 CHECK (probe_unknown IN (0,1)),
    waiting_reason    TEXT CHECK (waiting_reason IN ('router-input-unknown')),
    -- The monotone barrier of the execution's waiting hints: every hint carries
    -- a per-attempt sequence, and only a higher one rewrites waiting_reason,
    -- so a late replay of an older dispatch cannot revive a cleared wait.
    waiting_hint_seq  INTEGER NOT NULL DEFAULT 0 CHECK (waiting_hint_seq >= 0),
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
    -- The connection reason names why a still-running attempt is uncertain: it
    -- is bound to execution_state, not ownership, because a finished attempt may
    -- keep its historical uncertain ownership, and a committed result ends every
    -- live connection question without proving a stop.
    CHECK ((execution_state='uncertain' AND result_json IS NULL AND connection_reason IS NOT NULL)
        OR ((execution_state!='uncertain' OR result_json IS NOT NULL) AND connection_reason IS NULL)),
    -- Receiving a receipt settles every wait: no probe flag or Router wait may
    -- survive into finalizing, finished or a committed result.
    CHECK ((execution_state NOT IN ('finalizing','finished') AND result_json IS NULL)
        OR (probe_unknown = 0 AND waiting_reason IS NULL)),
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
    # The stopped-board installer runs this step on idle stock: every attempt is
    # finished with confirmed shutdown, so no live connection reason, probe flag
    # or Router wait survives. Copying inside SQLite via TEMP tables avoids
    # loading task specs and attempt reports into Python memory and avoids
    # renaming referenced tables (which would rewrite child foreign keys).
    connection.execute('CREATE TEMP TABLE _tasks_41 AS SELECT rowid AS _rowid, * FROM "tasks"')
    connection.execute('DROP TABLE "tasks"')
    connection.execute(TASKS_DDL)
    task_columns = [row[1] for row in connection.execute('PRAGMA table_info("tasks")')]
    connection.execute(
        f'INSERT INTO "tasks"(rowid,{",".join(task_columns)})'
        f' SELECT _rowid,{",".join(task_columns)} FROM _tasks_41 ORDER BY _rowid')
    connection.execute('DROP TABLE _tasks_41')
    for statement in TASKS_INDEXES.split(';'):
        if statement.strip():
            connection.execute(statement)
    connection.execute('CREATE TEMP TABLE _attempts_41 AS SELECT rowid AS _rowid, * FROM attempts')
    connection.execute('DROP TABLE attempts')
    connection.execute(ATTEMPTS_DDL)
    attempt_columns = [row[1] for row in connection.execute('PRAGMA table_info(attempts)')]
    # Idle stock has no live waiting fact: the new columns start empty, the
    # probe flag starts clear and the monotone hint barrier starts at zero.
    defaults = {'connection_reason': 'NULL', 'probe_unknown': '0', 'waiting_reason': 'NULL',
                'waiting_hint_seq': '0'}
    select_columns = [defaults.get(column, column) for column in attempt_columns]
    connection.execute(
        f'INSERT INTO attempts(rowid,{",".join(attempt_columns)})'
        f' SELECT _rowid,{",".join(select_columns)} FROM _attempts_41 ORDER BY _rowid')
    connection.execute('DROP TABLE _attempts_41')
    for statement in ATTEMPTS_INDEXES.split(';'):
        if statement.strip():
            connection.execute(statement)
    connection.execute("UPDATE meta SET value='42' WHERE key='schema_version'")
