"""Schema 40 → 41: record paused macro tasks and standalone micro tasks."""
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


def upgrade(connection):
    connection.execute(WORK_PAUSES_DDL)
    connection.execute("UPDATE meta SET value='41' WHERE key='schema_version'")
