"""Schema 31 → 32: task facts are read from the frozen specification.

No sealed content or spec is rewritten. Rebuild in SQLite's documented order;
the caller holds the transaction with foreign keys disabled until its check.
"""

TASKS_DDL = """
CREATE TABLE tasks_next (
    task_id                 TEXT PRIMARY KEY,
    request_id              TEXT NOT NULL UNIQUE,
    owner                   TEXT,
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
            AND json_extract(spec_json,'$.expectedSeconds') > 0))
);
"""

TASKS_INDEX_DDL = """
CREATE INDEX IF NOT EXISTS tasks_state_idx ON tasks(state, created_at);
"""

TASK_COLUMNS = (
    "task_id",
    "request_id",
    "owner",
    "spec_json",
    "input_fingerprint",
    "fingerprint_version",
    "adapter",
    "cwd",
    "state",
    "queue_reason",
    "revision",
    "selected_attempt_id",
    "active_attempt_id",
    "created_at",
    "updated_at",
    "accepted_at",
    "acceptance_note",
    "review_rejected_at",
    "review_rejection_note",
)


def upgrade(connection):
    connection.execute(TASKS_DDL)
    columns = ','.join(('rowid', *TASK_COLUMNS))
    connection.execute(f"INSERT INTO tasks_next({columns}) SELECT {columns} FROM tasks")
    connection.execute("DROP TABLE tasks")
    connection.execute("ALTER TABLE tasks_next RENAME TO tasks")
    connection.execute(TASKS_INDEX_DDL)
    connection.execute("UPDATE meta SET value='32' WHERE key='schema_version'")
