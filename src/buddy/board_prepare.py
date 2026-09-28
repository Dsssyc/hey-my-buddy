"""Explicit offline preparation of a current-schema board from an idle schema-11 board.

The runtime never converts a board at startup. This tool is the separate, explicit
step the work-objective slice needs: it copies an idle, verified schema-11 board
into a *new* state directory, adds the objective table, the nullable grouping and
display columns and the activity indexes, derives the latest-activity
projection from the events already recorded, and then applies the same 12 → 13
migration ``upgrade`` uses. It never writes to the source, never
overwrites a destination and never invents an objective for a historical run:
old roots stay standalone delegations. Activating the prepared copy is a separate
coordinated cutover.

Usage::

    python -m buddy.board_prepare --source <schema-11 state dir> --destination <new state dir>
"""
from __future__ import annotations

import argparse
from . import locking
import hashlib
import json
import os
import sqlite3
import sys
from pathlib import Path

from .db import DB_FILE, SCHEMA, SCHEMA_VERSION, SECRET_KEY
from .errors import BoardError
from .migrations import MIGRATED_TABLES_13, migrate_12_to_13, migrate_13_to_14
from .objectives import record_activity

SOURCE_SCHEMA_VERSION = 11
OWNER_LOCKS = ("control-daemon.lock", "board-owner.lock")

#: The schema-12 additions, applied to the copy only. ``ADD COLUMN`` keeps every
#: existing row and appends nullable/defaulted columns.
UPGRADE_STATEMENTS = (
    """CREATE TABLE objectives (
    objective_id       TEXT PRIMARY KEY,
    title              TEXT NOT NULL,
    project_id         TEXT NOT NULL,
    project_path       TEXT NOT NULL,
    source_host_id     TEXT NOT NULL,
    created_at         TEXT NOT NULL,
    activity_seq       INTEGER NOT NULL DEFAULT 0,
    activity_at        TEXT NOT NULL
)""",
    "CREATE INDEX objectives_activity_idx ON objectives(activity_seq DESC, objective_id DESC)",
    "CREATE INDEX objectives_project_idx ON objectives(project_id, activity_seq DESC, objective_id DESC)",
    "ALTER TABLE workflow_runs ADD COLUMN objective_id TEXT REFERENCES objectives(objective_id) ON DELETE RESTRICT",
    "ALTER TABLE workflow_runs ADD COLUMN title TEXT",
    "ALTER TABLE workflow_runs ADD COLUMN activity_seq INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE workflow_runs ADD COLUMN activity_at TEXT",
    "CREATE INDEX workflow_runs_objective_idx ON workflow_runs(objective_id, activity_seq DESC, run_id DESC)",
    "CREATE INDEX workflow_runs_activity_idx ON workflow_runs(activity_seq)",
    "CREATE INDEX IF NOT EXISTS decision_requests_task_idx ON decision_requests(task_id)",
    """CREATE INDEX IF NOT EXISTS events_decision_status_idx ON events(kind, seq DESC)
       WHERE kind IN ('decision.completed','decision.failed','decision.needs_host','decision.cancelled','decision.stale')""",
)

#: Attempt states that mean a board still owns live or unresolved work.
UNFINISHED_ATTEMPT_SQL = (
    "SELECT COUNT(*) FROM attempts WHERE execution_state <> 'finished' OR shutdown_confirmed <> 1"
)
OPEN_TASK_SQL = "SELECT COUNT(*) FROM tasks WHERE state IN ('queued','running','cancelling')"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _refuse_active_owner(source: Path) -> None:
    """A running daemon holds its owner locks; probing them never takes ownership."""
    for name in OWNER_LOCKS:
        path = source / name
        if not path.exists():
            continue
        fd = os.open(path, os.O_RDONLY)
        try:
            try:
                locking.lock(fd, blocking=False)
            except BlockingIOError:
                raise BoardError("SOURCE_ACTIVE", "A service still owns the source board; stop it first",
                                 lock=name) from None
            locking.unlock(fd)
        finally:
            os.close(fd)


def _schema_shape(connection: sqlite3.Connection) -> dict:
    tables = [row[0] for row in connection.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
    return {
        "tables": {table: sorted(row[1] for row in connection.execute(f"PRAGMA table_info('{table}')"))
                   for table in tables},
        "indexes": sorted(row[0] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND name NOT LIKE 'sqlite_%'")),
        "views": sorted(row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='view'")),
    }


def _reference_shape() -> dict:
    reference = sqlite3.connect(":memory:")
    try:
        reference.executescript(SCHEMA)
        return _schema_shape(reference)
    finally:
        reference.close()


def prepare(source: Path | str, destination: Path | str) -> dict:
    """Prepare a verified current-schema copy of an idle schema-11 board; return a report."""
    source, destination = Path(source).resolve(), Path(destination).resolve()
    source_db = source / DB_FILE
    if not source_db.is_file():
        raise BoardError("INVALID_ARGUMENT", "The source directory has no board database")
    if destination.exists():
        raise BoardError("DESTINATION_EXISTS", "The destination already exists; choose a new directory")
    if destination == source or source in destination.parents:
        raise BoardError("INVALID_ARGUMENT", "The destination must be outside the source state directory")
    _refuse_active_owner(source)
    source_hash = _sha256(source_db)
    reader = sqlite3.connect(source_db.as_uri() + "?mode=ro", uri=True, timeout=10)
    try:
        version = reader.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
        if version is None or version[0] != str(SOURCE_SCHEMA_VERSION):
            raise BoardError("UNSUPPORTED_SOURCE", f"The source is not a schema-{SOURCE_SCHEMA_VERSION} board",
                             found=None if version is None else version[0])
        if reader.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise BoardError("SOURCE_INVALID", "The source board failed its integrity check")
        if reader.execute("PRAGMA foreign_key_check").fetchall():
            raise BoardError("SOURCE_INVALID", "The source board has foreign-key violations")
        unfinished = reader.execute(UNFINISHED_ATTEMPT_SQL).fetchone()[0]
        open_tasks = reader.execute(OPEN_TASK_SQL).fetchone()[0]
        if unfinished or open_tasks:
            raise BoardError("SOURCE_ACTIVE", "The source board still has open tasks or unconfirmed attempts",
                             openTasks=open_tasks, unconfirmedAttempts=unfinished)
        tables = [row[0] for row in reader.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
        source_counts = {table: reader.execute(f"SELECT COUNT(*) FROM '{table}'").fetchone()[0] for table in tables}
        source_secret = reader.execute("SELECT value FROM meta WHERE key=?", (SECRET_KEY,)).fetchone()
        destination.mkdir(mode=0o700, parents=True)
        target_db = destination / DB_FILE
        writer = sqlite3.connect(target_db)
        try:
            reader.backup(writer)
        finally:
            writer.close()
    finally:
        reader.close()
    os.chmod(target_db, 0o600)
    connection = sqlite3.connect(target_db, isolation_level=None)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("BEGIN IMMEDIATE")
        try:
            for statement in UPGRADE_STATEMENTS:
                connection.execute(statement)
            # Replaying the recorded event order through the runtime's own projection
            # gives every run and its owners the sequence of their newest event.
            derived = 0
            for event in connection.execute("SELECT seq, task_id, created_at FROM events ORDER BY seq").fetchall():
                record_activity(connection, event["task_id"], int(event["seq"]), event["created_at"])
                derived += 1
            connection.execute("UPDATE meta SET value='12' WHERE key='schema_version'")
            connection.execute("COMMIT")
        except BaseException:
            connection.execute("ROLLBACK")
            raise
        migration = migrate_12_to_13(connection)
        migration = {**migration, "healthMigration": migrate_13_to_14(connection), "toSchema": SCHEMA_VERSION}
        connection.execute("PRAGMA foreign_keys=ON")
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        violations = connection.execute("PRAGMA foreign_key_check").fetchall()
        shape = _schema_shape(connection)
        tables = [table for table in tables if table not in MIGRATED_TABLES_13]
        target_counts = {table: connection.execute(f"SELECT COUNT(*) FROM '{table}'").fetchone()[0] for table in tables}
        target_secret = connection.execute("SELECT value FROM meta WHERE key=?", (SECRET_KEY,)).fetchone()
        runs_with_activity = connection.execute("SELECT COUNT(*) FROM workflow_runs WHERE activity_seq > 0").fetchone()[0]
    finally:
        connection.close()
    problems = []
    if integrity != "ok":
        problems.append(f"integrity: {integrity}")
    if violations:
        problems.append(f"{len(violations)} foreign-key violation(s)")
    if shape != _reference_shape():
        problems.append(f"the prepared schema differs from a fresh schema-{SCHEMA_VERSION} board")
    changed = {table: (source_counts[table], target_counts[table]) for table in tables
               if source_counts[table] != target_counts[table]}
    if changed:
        problems.append(f"row counts changed: {changed}")
    if (source_secret is None) != (target_secret is None) or (
        source_secret is not None and source_secret[0] != target_secret[0]
    ):
        problems.append("the capability secret was not preserved")
    if _sha256(source_db) != source_hash:
        problems.append("the source database changed during preparation")
    if problems:
        raise BoardError("PREPARATION_FAILED", "; ".join(problems), destination=str(destination))
    return {
        "source": str(source),
        "destination": str(destination),
        "sourceSchema": SOURCE_SCHEMA_VERSION,
        "schema": SCHEMA_VERSION,
        "sourceSha256": source_hash,
        "tables": len(tables),
        "rows": sum(target_counts.values()),
        "eventsReplayed": derived,
        "runsWithActivity": runs_with_activity,
        "objectivesCreated": 0,
        "migration": migration,
        "verified": True,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--source", required=True, help="idle schema-11 state directory (read only)")
    parser.add_argument("--destination", required=True, help="new state directory to create")
    args = parser.parse_args(argv)
    try:
        report = prepare(args.source, args.destination)
    except BoardError as error:
        print(json.dumps({"error": error.payload()}, ensure_ascii=False))
        return 1
    print(json.dumps(report, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
