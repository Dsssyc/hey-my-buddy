"""Explicit offline migration of an older board database to the current schema.

A cold start never migrates: :class:`buddy.db.Database` refuses a schema version that
does not match the build, so a running board is never rewritten underneath its owner.
This module is the one supported upgrade path from a version-5 board (no evaluation
table) or a version-6 board (no governed workflow table):

1. take exclusive ownership of the state directory (the same two lifetime locks the
   daemon uses) and refuse while a service answers;
2. verify the source database is a healthy supported board;
3. copy it to a verified ``board.sqlite3.v<source>-backup-<timestamp>`` sibling;
4. apply the missing additions inside one transaction and verify integrity,
   foreign keys and every preserved record count *before* committing.

A failure therefore leaves the original database exactly as it was, with the verified
backup still available. Task, attempt, event, receipt, claim, cursor, evaluation and
workflow identities are copied by SQLite itself: the migration adds tables and meta
rows, never rewrites a record.
"""
from __future__ import annotations

import fcntl
import os

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from .db import (
    DB_FILE,
    EVALUATION_TABLES,
    SCHEMA_VERSION,
    V5_SCHEMA_VERSION,
    V6_SCHEMA_VERSION,
    WORKFLOW_TABLES,
    utc_now,
)
from .errors import BoardError

#: Tables whose contents must be identical before and after the upgrade. Only the
#: tables that actually exist in the source database are compared, so the same code
#: verifies a v5 upgrade (core tables) and a v6 upgrade (core plus evaluation and
#: workflow tables).
PRESERVED_TABLES = (
    "tasks",
    "attempts",
    "workers",
    "messages",
    "artifacts",
    "events",
    "commands",
    "resource_claims",
    "cursors",
    "evaluation_state",
    "evaluation_revisions",
    "evaluation_profiles",
    "evaluation_cards",
    "evaluation_preferences",
    "evaluation_evidence",
    "evaluation_decisions",
    "evaluation_catalog",
    "evaluation_readers",
    "evaluation_writers",
    "evaluation_evidence_pending",
    "evaluation_samples",
    "evaluation_card_history",
    "evaluation_aggregates",
    "decision_requests",
    "workflow_runs",
    "workflow_turns",
    "workflow_requests",
    "workflow_children",
    "workflow_continuations",
    "workspace_reservations",
    "agent_credentials",
    "workflow_artifacts",
    "workflow_suggestions",
)

#: Source versions this build can upgrade offline, oldest first.
SUPPORTED_SOURCE_VERSIONS = (V5_SCHEMA_VERSION, V6_SCHEMA_VERSION)

LOCK_NAMES = ("control-daemon.lock", "board-owner.lock")


def _acquire_ownership(directory: Path) -> list[int]:
    fds: list[int] = []
    for name in LOCK_NAMES:
        fd = os.open(directory / name, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            os.close(fd)
            for held in fds:
                try:
                    fcntl.flock(held, fcntl.LOCK_UN)
                finally:
                    os.close(held)
            raise BoardError(
                "SERVICE_RUNNING",
                "Another owner already holds this state directory; stop the board service before migrating. "
                "The database was not changed.",
                lock=name,
            ) from None
        fds.append(fd)
    return fds


def _release(fds: list[int]) -> None:
    for fd in fds:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        except OSError:  # pragma: no cover - teardown best effort
            pass
        finally:
            os.close(fd)


def _service_answering(directory: Path) -> str | None:
    from .transport import ServiceError, _read_endpoint, _request

    endpoint = _read_endpoint(directory)
    if endpoint is None:
        return None
    try:
        _request(endpoint, "health", {})
    except ServiceError:
        return None
    return endpoint.get("address")


def _counts(connection: sqlite3.Connection, tables: tuple[str, ...]) -> dict[str, int]:
    return {
        table: int(connection.execute(f"SELECT COUNT(*) AS count FROM {table}").fetchone()["count"])
        for table in tables
    }


def _present_tables(connection: sqlite3.Connection) -> tuple[str, ...]:
    names = {
        row["name"]
        for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    }
    return tuple(table for table in PRESERVED_TABLES if table in names)


def _verify(connection: sqlite3.Connection, before: dict[str, int], tables: tuple[str, ...]) -> None:
    integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
    if integrity != "ok":
        raise BoardError("MIGRATION_FAILED", f"Post-migration integrity check failed: {integrity}")
    violations = connection.execute("PRAGMA foreign_key_check").fetchall()
    if violations:
        raise BoardError("MIGRATION_FAILED", f"Post-migration foreign-key check found {len(violations)} violation(s)")
    after = _counts(connection, tables)
    if after != before:
        changed = sorted(key for key in before if before[key] != after.get(key))
        raise BoardError(
            "MIGRATION_FAILED",
            f"The upgrade would change preserved record counts ({', '.join(changed) or 'unknown'}); "
            "the transaction was rolled back",
        )
    version = connection.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
    if version is None or int(version["value"]) != SCHEMA_VERSION:
        raise BoardError("MIGRATION_FAILED", "The upgraded database does not report the new schema version")


def _backup(connection: sqlite3.Connection, directory: Path, source_version: int) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = directory / f"{DB_FILE}.v{source_version}-backup-{stamp}"
    counter = 0
    while path.exists():
        counter += 1
        path = directory / f"{DB_FILE}.v{source_version}-backup-{stamp}-{counter}"
    destination = sqlite3.connect(path, isolation_level=None, timeout=10)
    try:
        connection.backup(destination)
    finally:
        destination.close()
    os.chmod(path, 0o600)
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
    check = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        version = check.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
        integrity = check.execute("PRAGMA integrity_check").fetchone()[0]
    finally:
        check.close()
    if version is None or int(version[0]) != source_version or integrity != "ok":
        path.unlink(missing_ok=True)
        raise BoardError(
            "BACKUP_FAILED",
            f"The pre-migration backup did not verify as a healthy version-{source_version} board; nothing was changed",
        )
    return path


def _apply(
    connection: sqlite3.Connection,
    backup_path: Path,
    before: dict[str, int],
    tables: tuple[str, ...],
    source_version: int,
) -> None:
    """One transaction: add the missing tables, then verify before committing.

    ``executescript`` is deliberately not used: it would commit the open transaction
    before running. Every statement here stays inside ``BEGIN IMMEDIATE``.
    """
    now = utc_now()
    connection.execute("BEGIN IMMEDIATE")
    try:
        if source_version < V6_SCHEMA_VERSION:
            for statement in EVALUATION_TABLES:
                connection.execute(statement)
        for statement in WORKFLOW_TABLES:
            connection.execute(statement)
        connection.execute(
            "INSERT OR IGNORE INTO evaluation_state(id, table_revision, configuration_revision, auto_maintain,"
            " writer_sequence, created_at, updated_at) VALUES(1, 0, 0, 0, 0, ?, ?)",
            (now, now),
        )
        for key, value in (
            ("schema_version", str(SCHEMA_VERSION)),
            ("migrated_from", str(source_version)),
            ("migrated_at", now),
            ("migration_backup", str(backup_path)),
        ):
            connection.execute(
                "INSERT INTO meta(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, value),
            )
        _verify(connection, before, tables)
    except BaseException:
        connection.execute("ROLLBACK")
        raise
    else:
        connection.execute("COMMIT")


def migrate(state_dir: str | Path, *, confirm: bool = False, dry_run: bool = False) -> dict:
    """Upgrade one stopped version-5 or version-6 board. Never called by a cold start."""
    directory = Path(state_dir).expanduser().absolute()
    if not directory.is_dir():
        raise BoardError("NOT_FOUND", f"State directory {directory} does not exist")
    from .transport import _private_directory, _trusted_ancestors

    if not _private_directory(directory) or not _trusted_ancestors(directory):
        raise BoardError(
            "INSECURE_STATE_DIR",
            f"{directory} is not a private directory owned by this user with no group/other access",
        )
    database = directory / DB_FILE
    if not database.is_file():
        raise BoardError("NOT_FOUND", f"{database} does not exist; nothing to migrate")

    locks = _acquire_ownership(directory)
    try:
        address = _service_answering(directory)
        if address:
            raise BoardError(
                "SERVICE_RUNNING",
                "A board service is still answering on this state directory; stop it before migrating. "
                "The database was not changed.",
                address=address,
            )
        connection = sqlite3.connect(database, isolation_level=None, timeout=10)
        try:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA busy_timeout=10000")
            connection.execute("PRAGMA foreign_keys=ON")
            meta = connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='meta'"
            ).fetchone()
            if meta is None:
                raise BoardError("NOT_A_BOARD", f"{database} has no board meta table; refusing to touch it")
            row = connection.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
            if row is None:
                raise BoardError("NOT_A_BOARD", f"{database} has no recorded schema version; manual recovery is required")
            version = int(row["value"])
            if version == SCHEMA_VERSION:
                return {
                    "migrated": False,
                    "alreadyCurrent": True,
                    "schemaVersion": version,
                    "stateDir": str(directory),
                    "database": str(database),
                }
            if version not in SUPPORTED_SOURCE_VERSIONS:
                raise BoardError(
                    "UNSUPPORTED_SCHEMA_VERSION",
                    f"Schema version {version} has no supported upgrade path in this build (expected "
                    f"{' or '.join(str(item) for item in SUPPORTED_SOURCE_VERSIONS)} or {SCHEMA_VERSION}); "
                    "the database was not changed",
                    foundVersion=version,
                )
            integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
            if integrity != "ok":
                raise BoardError(
                    "CORRUPT", f"Refusing to migrate a database whose integrity check failed: {integrity}"
                )
            violations = connection.execute("PRAGMA foreign_key_check").fetchall()
            if violations:
                raise BoardError(
                    "CORRUPT",
                    f"Refusing to migrate a database with {len(violations)} foreign-key violation(s)",
                )
            tables = _present_tables(connection)
            before = _counts(connection, tables)
            plan = {
                "migrated": True,
                "dryRun": bool(dry_run),
                "fromVersion": version,
                "toVersion": SCHEMA_VERSION,
                "stateDir": str(directory),
                "database": str(database),
                "backup": None,
                "counts": before,
            }
            if dry_run:
                plan["note"] = "Dry run only: no backup was written and no schema change was applied."
                return plan
            if not confirm:
                raise BoardError(
                    "CONFIRMATION_REQUIRED",
                    "Migration needs explicit confirmation. Re-run with confirm=true (and a stopped service) to write "
                    "a verified backup and upgrade the database.",
                    fromVersion=version,
                    toVersion=SCHEMA_VERSION,
                )
            backup_path = _backup(connection, directory, version)
            plan["backup"] = str(backup_path)
            try:
                _apply(connection, backup_path, before, tables, version)
            except BoardError:
                raise
            except Exception as error:  # noqa: BLE001 - one honest failure envelope
                raise BoardError(
                    "MIGRATION_FAILED",
                    f"The upgrade failed and was rolled back; the original database is unchanged. "
                    f"Backup: {backup_path}. Cause: {type(error).__name__}",
                ) from error
            plan["countsAfter"] = _counts(connection, tables)
            plan["schemaVersion"] = int(
                connection.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()["value"]
            )
            plan["integrity"] = connection.execute("PRAGMA integrity_check").fetchone()[0]
            plan["note"] = (
                "The board was upgraded in one transaction. Task, attempt, event, receipt, claim, cursor, "
                "evaluation and workflow identities are preserved; the verified pre-migration copy remains "
                "beside the database."
            )
            return plan
        finally:
            connection.close()
    finally:
        _release(locks)


def backup_files(directory: str | Path) -> list[Path]:
    """Existing pre-migration backups, newest last (read-only helper)."""
    root = Path(directory)
    return sorted({*root.glob(f"{DB_FILE}.v5-backup-*"), *root.glob(f"{DB_FILE}.v6-backup-*")})


__all__ = ["backup_files", "migrate"]
