"""Schema 31 → 32: remove derived task/execution facts and unused catalog metadata.

No sealed content or spec is rewritten. Rebuild in SQLite's documented order;
the caller holds the transaction with foreign keys disabled until its check.
"""

import json

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

ATTEMPT_PROCESS_IDENTITIES_DDL = """
CREATE TABLE attempt_process_identities_next (
    attempt_id          TEXT PRIMARY KEY REFERENCES attempts(attempt_id) ON DELETE RESTRICT,
    boot_id             TEXT,
    root_pid            INTEGER NOT NULL CHECK (root_pid > 0),
    root_started_at     INTEGER NOT NULL CHECK (root_started_at > 0),
    job_name            TEXT,
    CHECK ((boot_id IS NOT NULL AND job_name IS NULL)
        OR (boot_id IS NULL AND job_name IS NOT NULL))
);
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
    connection.execute(ATTEMPT_PROCESS_IDENTITIES_DDL)
    columns = 'rowid,attempt_id,boot_id,root_pid,root_started_at,job_name'
    connection.execute(f"INSERT INTO attempt_process_identities_next({columns})"
                       f" SELECT {columns} FROM attempt_process_identities")
    connection.execute("DROP TABLE attempt_process_identities")
    connection.execute("ALTER TABLE attempt_process_identities_next RENAME TO attempt_process_identities")
    connection.execute("UPDATE attempts SET result_json=json_remove(result_json,"
                       "'$.result.usage.bytesRead','$.result.budget')"
                       " WHERE json_type(result_json,'$.result.usage.bytesRead') IS NOT NULL"
                       " OR json_type(result_json,'$.result.budget') IS NOT NULL")
    connection.execute("UPDATE decision_requests SET output_json=json_remove(output_json,"
                       "'$.usage.bytesRead','$.budget')"
                       " WHERE json_type(output_json,'$.usage.bytesRead') IS NOT NULL"
                       " OR json_type(output_json,'$.budget') IS NOT NULL")
    for observation_id, raw in connection.execute(
            "SELECT observation_id,response_json FROM catalog_observations WHERE response_json IS NOT NULL").fetchall():
        response = json.loads(raw)
        facts = {key: response[key] for key in
                 ('discoveryId', 'observationId', 'duplicate', 'appliedHarnesses', 'staleHarnesses')}
        facts['discoveredAt'] = response['catalog']['discoveredAt']
        connection.execute("UPDATE catalog_observations SET response_json=? WHERE observation_id=?",
                           (json.dumps(facts, ensure_ascii=False, sort_keys=True, separators=(',', ':')), observation_id))
    connection.execute("UPDATE meta SET value='32' WHERE key='schema_version'")
