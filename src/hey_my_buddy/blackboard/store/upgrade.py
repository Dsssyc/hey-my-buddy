"""The one pending, offline upgrade from 0.48.0 (schema 26) to schema 27."""
from __future__ import annotations

from contextlib import closing
from pathlib import Path
import sqlite3

from .db import DB_FILE, SCHEMA, SCHEMA_VERSION

BASE_SCHEMA_VERSION = 26
_REBUILT_TABLES = (
    "macro_task_deletions", "commands", "tasks", "resource_claims",
    "attempt_interruptions", "messages",
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
