"""The one pending, offline upgrade from 0.48.0 (schema 26) to schema 27."""
from __future__ import annotations

from contextlib import closing
from pathlib import Path
import sqlite3

from .db import DB_FILE, SCHEMA, SCHEMA_VERSION

BASE_SCHEMA_VERSION = 26
_REBUILT_TABLES = (
    "macro_task_deletions", "commands", "tasks", "resource_claims",
    "attempt_interruptions", "messages", "workflow_runs", "workflow_children",
    "agent_credentials", "workflow_artifacts", "workflow_suggestions",
    "workflow_host_conclusions", "attempt_activity", "attempt_reminders",
    "workflow_routes", "workflow_continuations", "workspace_reservations",
)
_DEAD_TABLES = ("cursors", "evaluation_state", "model_facts")
_DEAD_INDEXES = (
    "tasks_request_idx", "attempts_task_idx", "workflow_turns_run_idx",
    "workflow_scope_versions_run_idx", "commands_kind_idx",
)


def _convert(connection: sqlite3.Connection) -> None:
    # These redundant columns fed public views. Refuse inconsistent old stock
    # at the conversion boundary rather than silently changing its answer.
    if connection.execute("SELECT 1 FROM tasks WHERE acceptance_verdict IS NOT "
                          "CASE WHEN accepted_at IS NOT NULL THEN 'accepted' END LIMIT 1").fetchone():
        raise ValueError("Inconsistent tasks.acceptance_verdict in upgrade stock")
    if connection.execute("SELECT 1 FROM messages WHERE correlation_id IS NOT inquiry_id "
                          "OR body_bytes != length(CAST(body AS BLOB)) LIMIT 1").fetchone():
        raise ValueError("Inconsistent messages.correlation_id/body_bytes in upgrade stock")
    checks = (
        ("workflow_runs.micro_task_json/micro_task_fingerprint",
         "SELECT 1 FROM workflow_runs r JOIN tasks t ON t.task_id=r.run_id "
         "WHERE r.micro_task_json IS NOT t.spec_json OR r.micro_task_fingerprint IS NOT t.input_fingerprint"),
        ("workflow_runs.workspace_id/workspace_manifest_sha256",
         "SELECT 1 FROM workflow_runs WHERE workspace_id IS NOT json_extract(workspace_manifest_json,'$.workspaceId') "
         "OR workspace_manifest_sha256 IS NOT json_extract(workspace_manifest_json,'$.manifestSha256')"),
        ("workflow_children.workspace_manifest_sha256/auto_continue",
         "SELECT 1 FROM workflow_children WHERE auto_continue != 0 OR "
         "workspace_manifest_sha256 IS NOT json_extract(workspace_manifest_json,'$.manifestSha256')"),
        ("agent_credentials.task_id/generation/expires_at",
         "SELECT 1 FROM agent_credentials c LEFT JOIN attempts a ON a.attempt_id=c.attempt_id "
         "WHERE c.task_id IS NOT c.run_id OR c.generation IS NOT a.generation OR c.expires_at IS NOT NULL"),
        ("workflow_artifacts.source_task_id",
         "SELECT 1 FROM workflow_artifacts WHERE source_task_id IS NOT CASE WHEN kind='input' THEN NULL ELSE run_id END"),
        ("workflow_suggestions.author",
         "SELECT 1 FROM workflow_suggestions WHERE author IS NOT "
         "CASE WHEN attempt_id IS NOT NULL THEN 'attempt:' || attempt_id ELSE 'agent' END"),
        ("workflow_host_conclusions.evidence_json/integration_id",
         "SELECT 1 FROM workflow_host_conclusions WHERE json(evidence_json) != '{\"references\":[]}' OR integration_id IS NOT NULL"),
    )
    for name, query in checks:
        if connection.execute(query + " LIMIT 1").fetchone():
            raise ValueError(f"Inconsistent {name} in upgrade stock")
    # The same timestamp algorithm generated the old stored elapsed value. The
    # reminder event and any sealed input containing it remain untouched.
    from ..tasks.reminders import _seconds
    for elapsed, created, started in connection.execute(
        "SELECT r.elapsed_seconds,r.created_at,a.started_at FROM attempt_reminders r "
        "JOIN attempts a ON a.attempt_id=r.attempt_id"
    ):
        if elapsed != max(0.0, _seconds(created) - _seconds(started)):
            raise ValueError("Inconsistent attempt_reminders.elapsed_seconds in upgrade stock")
    # Only completed deletions lose their receipts. A physical deletion still in
    # progress retains its fence, tasks and commands so it can finish normally.
    connection.execute("DELETE FROM commands WHERE deletion_id IN "
                       "(SELECT deletion_id FROM macro_task_deletions WHERE state='deleted')")
    connection.execute("DELETE FROM macro_task_deletions WHERE state='deleted'")
    connection.execute("DELETE FROM events WHERE kind='macro_task.deleted'")
    for name in _DEAD_INDEXES:
        connection.execute(f'DROP INDEX "{name}"')
    for name in _DEAD_TABLES:
        connection.execute(f'DROP TABLE "{name}"')

    reminder_sequence = connection.execute(
        "SELECT seq FROM sqlite_sequence WHERE name='attempt_reminders'"
    ).fetchone()

    # Take DDL from the fresh schema, keeping constraints and indexes identical
    # without maintaining a second copy of the current table definitions.
    with closing(sqlite3.connect(":memory:")) as fresh:
        fresh.executescript(SCHEMA)
        for table in _REBUILT_TABLES:
            columns = ",".join(f'"{row[1]}"' for row in fresh.execute(f'PRAGMA table_info("{table}")'))
            connection.execute(f'CREATE TEMP TABLE upgrade_saved AS SELECT {columns} FROM "{table}"')
            connection.execute(f'DROP TABLE "{table}"')
            ddl = fresh.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone()[0]
            connection.execute(ddl)
            connection.execute(f'INSERT INTO "{table}" ({columns}) SELECT {columns} FROM upgrade_saved')
            connection.execute("DROP TABLE upgrade_saved")
            for ddl, in fresh.execute("SELECT sql FROM sqlite_master WHERE type='index' AND tbl_name=? AND sql IS NOT NULL", (table,)):
                connection.execute(ddl)
    # Rebuilding an AUTOINCREMENT table must retain ids consumed by deleted rows.
    connection.execute("DELETE FROM sqlite_sequence WHERE name='attempt_reminders'")
    if reminder_sequence is not None:
        connection.execute("INSERT INTO sqlite_sequence(name,seq) VALUES('attempt_reminders',?)", reminder_sequence)


def upgrade(state: Path) -> None:
    """Convert a stopped schema-26 board atomically; reject other schemas."""
    path = Path(state) / DB_FILE
    if not path.is_file():
        raise FileNotFoundError(path)
    with closing(sqlite3.connect(path, isolation_level=None, timeout=10)) as connection:
        # Referenced tables are rebuilt inside one transaction. Validate all
        # relationships before commit, after their original names are restored.
        connection.execute("PRAGMA foreign_keys=OFF")
        connection.execute("BEGIN IMMEDIATE")
        try:
            version = connection.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
            if version is None or version[0] != str(BASE_SCHEMA_VERSION):
                raise ValueError(f"Expected schema {BASE_SCHEMA_VERSION} for upgrade; found {None if version is None else version[0]!r}")
            if connection.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
                raise ValueError("Board integrity check failed before schema upgrade")
            if connection.execute("PRAGMA foreign_key_check").fetchall():
                raise ValueError("Board foreign key check failed before schema upgrade")
            _convert(connection)
            connection.execute("UPDATE meta SET value=? WHERE key='schema_version'", (str(SCHEMA_VERSION),))
            if connection.execute("PRAGMA foreign_key_check").fetchall():
                raise ValueError("Board foreign key check failed after schema upgrade")
            if connection.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
                raise ValueError("Board integrity check failed after schema upgrade")
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
