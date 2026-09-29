"""Explicit board migrations; runtime startup never converts a database.

Upgrade migrates an idle, backed-up schema-13 board to 14 under exclusive owner
locks and verifies every retained table fingerprint. The historical 12-to-13
step remains only for the separately invoked offline board preparation helper.
"""
from __future__ import annotations

from collections import defaultdict
import sqlite3

from .db import HARNESS_SCHEMA, HOST_CONCLUSION_TABLE, HOST_CONCLUSION_INDEX

#: Tables this migration may rewrite; every other table must keep its fingerprint.
MIGRATED_TABLES_13 = frozenset({"evaluation_preferences", "evaluation_annotations",
                             "family_preferences", "family_annotations", "meta"})
MIGRATED_TABLES_14 = frozenset({"harness_health", "meta"})
MIGRATED_TABLES = frozenset({"meta", "workflow_runs", "attempts", "harness_health", "workflow_host_conclusions"})

_STATEMENTS = (
    """CREATE TABLE family_preferences (
    adapter           TEXT NOT NULL,
    provider          TEXT NOT NULL,
    model             TEXT NOT NULL,
    mode              TEXT NOT NULL CHECK (mode IN ('prefer','pin','exclude')),
    reason            TEXT NOT NULL DEFAULT '',
    updated_revision  INTEGER NOT NULL,
    PRIMARY KEY (adapter, provider, model)
)""",
    """CREATE TABLE family_annotations (
    adapter    TEXT NOT NULL,
    provider   TEXT NOT NULL,
    model      TEXT NOT NULL,
    text       TEXT NOT NULL,
    revision   INTEGER NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (adapter, provider, model)
)""",
    """CREATE TABLE evaluation_preferences_13 (
    profile_id        TEXT PRIMARY KEY REFERENCES evaluation_profiles(profile_id) ON DELETE RESTRICT,
    mode              TEXT NOT NULL CHECK (mode IN ('prefer','pin','exclude','none')),
    reason            TEXT NOT NULL DEFAULT '',
    updated_revision  INTEGER NOT NULL
)""",
)

_VIEW = """CREATE VIEW effective_preferences AS
SELECT p.profile_id AS profile_id,
       COALESCE(o.mode, f.mode) AS mode,
       CASE WHEN o.profile_id IS NOT NULL THEN o.reason ELSE f.reason END AS reason,
       CASE WHEN o.profile_id IS NOT NULL THEN 'override' ELSE 'family' END AS source
FROM evaluation_profiles p
LEFT JOIN evaluation_preferences o ON o.profile_id = p.profile_id
LEFT JOIN family_preferences f ON f.adapter = p.adapter AND f.provider = p.provider AND f.model = p.model
WHERE (o.profile_id IS NOT NULL AND o.mode != 'none') OR (o.profile_id IS NULL AND f.mode IS NOT NULL)"""


def schema_version(connection: sqlite3.Connection) -> int | None:
    row = connection.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
    return int(row[0]) if row and str(row[0]).isdigit() else None


def _families(connection: sqlite3.Connection) -> dict[tuple, list[tuple[str, str]]]:
    families: dict[tuple, list[tuple[str, str]]] = defaultdict(list)
    for profile_id, adapter, provider, model, effort in connection.execute(
            "SELECT profile_id, adapter, provider, model, effort FROM evaluation_profiles ORDER BY profile_id"):
        families[(adapter, provider, model)].append((profile_id, effort))
    return families


def migrate_12_to_13(connection: sqlite3.Connection) -> dict:
    """Apply the whole migration in one transaction and return what moved."""
    if schema_version(connection) != 12:
        raise ValueError("migration expects schema 12")
    connection.execute("PRAGMA foreign_keys=OFF")
    connection.execute("BEGIN IMMEDIATE")
    try:
        for statement in _STATEMENTS:
            connection.execute(statement)
        families = _families(connection)
        preferences = {row[0]: row for row in connection.execute(
            "SELECT profile_id, mode, reason, updated_revision FROM evaluation_preferences")}
        merged_families, overrides = 0, 0
        for (adapter, provider, model), members in families.items():
            rows = [preferences.get(profile_id) for profile_id, _effort in members]
            shared = (rows and all(rows) and len({(row[1], row[2]) for row in rows}) == 1)
            if shared:
                connection.execute(
                    "INSERT INTO family_preferences(adapter,provider,model,mode,reason,updated_revision) VALUES(?,?,?,?,?,?)",
                    (adapter, provider, model, rows[0][1], rows[0][2], max(row[3] for row in rows)))
                merged_families += 1
            else:
                for row in rows:
                    if row:
                        connection.execute(
                            "INSERT INTO evaluation_preferences_13(profile_id,mode,reason,updated_revision) VALUES(?,?,?,?)", tuple(row))
                        overrides += 1
        connection.execute("DROP TABLE evaluation_preferences")
        connection.execute("ALTER TABLE evaluation_preferences_13 RENAME TO evaluation_preferences")
        connection.execute("CREATE INDEX evaluation_preferences_mode_idx ON evaluation_preferences(mode, profile_id)")
        notes = {row[0]: row for row in connection.execute(
            "SELECT profile_id, text, revision, updated_at FROM evaluation_annotations")}
        merged_notes = 0
        for (adapter, provider, model), members in families.items():
            found = [(effort, notes[profile_id]) for profile_id, effort in members if profile_id in notes]
            if not found:
                continue
            texts = [note[1] for _effort, note in found]
            if len(set(texts)) == 1:
                text = texts[0]
            else:
                text = "\n\n".join(f"{note[1]}（来自 {effort} 档位）" for effort, note in found)
            connection.execute(
                "INSERT INTO family_annotations(adapter,provider,model,text,revision,updated_at) VALUES(?,?,?,?,?,?)",
                (adapter, provider, model, text, max(note[2] for _effort, note in found),
                 max(note[3] for _effort, note in found)))
            merged_notes += 1
        orphaned = set(notes) - {profile_id for members in families.values() for profile_id, _effort in members}
        if orphaned:
            raise ValueError("annotations reference unknown profiles")
        connection.execute("DROP TABLE evaluation_annotations")
        connection.execute(_VIEW)
        connection.execute("UPDATE meta SET value='13' WHERE key='schema_version'")
        if connection.execute("PRAGMA foreign_key_check").fetchall():
            raise ValueError("foreign key check failed after migration")
        connection.execute("COMMIT")
    except BaseException:
        connection.execute("ROLLBACK")
        raise
    finally:
        connection.execute("PRAGMA foreign_keys=ON")
    if [tuple(row) for row in connection.execute("PRAGMA integrity_check")] != [("ok",)]:
        raise ValueError("integrity check failed after migration")
    return {"fromSchema": 12, "toSchema": 13,
            "familyPreferences": merged_families, "preferenceOverrides": overrides,
            "familyAnnotations": merged_notes, "annotationsMerged": len(notes)}


def migrate_13_to_14(connection: sqlite3.Connection) -> dict:
    """Add empty service-owned health records; never probe or change user policy."""
    if schema_version(connection) != 13:
        raise ValueError("migration expects schema 13")
    connection.execute("BEGIN IMMEDIATE")
    try:
        if connection.execute("SELECT 1 FROM sqlite_master WHERE name='harness_health'").fetchone():
            raise ValueError("unexpected harness_health table in schema 13")
        connection.execute(HARNESS_SCHEMA.replace("    scan_after TEXT,\n    quota_json TEXT", "    scan_after TEXT"))
        connection.execute("UPDATE meta SET value='14' WHERE key='schema_version'")
        if connection.execute("PRAGMA foreign_key_check").fetchall():
            raise ValueError("foreign key check failed after migration")
        if [tuple(row) for row in connection.execute("PRAGMA integrity_check")] != [("ok",)]:
            raise ValueError("integrity check failed after migration")
        connection.execute("COMMIT")
    except BaseException:
        connection.execute("ROLLBACK")
        raise
    return {"fromSchema": 13, "toSchema": 14, "harnessHealth": 0}


def retained_columns(connection, columns=None):
    """Fingerprint the original columns in each extended table, including empty rows."""
    import hashlib
    import json
    if columns is None:
        columns = {table: [row[1] for row in connection.execute(f'PRAGMA table_info("{table}")')]
                   for table in ("workflow_runs", "attempts", "harness_health")}
    fingerprints = {}
    for table, names in columns.items():
        projection = ','.join('"' + name + '"' for name in names)
        digest = hashlib.sha256()
        for row in connection.execute(f'SELECT {projection} FROM "{table}" ORDER BY 1'):
            digest.update(json.dumps(tuple(row), ensure_ascii=False, separators=(',', ':')).encode())
            digest.update(b'\n')
        fingerprints[table] = digest.hexdigest()
    return columns, fingerprints


def migrate_14_to_15(connection: sqlite3.Connection) -> dict:
    """Add Host conclusions and native observations without relabelling history."""
    if schema_version(connection) != 14:
        raise ValueError("migration expects schema 14")
    connection.execute("BEGIN IMMEDIATE")
    try:
        columns, before = retained_columns(connection)
        connection.execute("ALTER TABLE workflow_runs ADD COLUMN configuration_locked INTEGER NOT NULL DEFAULT 0 CHECK (configuration_locked IN (0,1))")
        connection.execute("ALTER TABLE attempts ADD COLUMN token_usage_json TEXT")
        connection.execute("ALTER TABLE harness_health ADD COLUMN quota_json TEXT")
        connection.execute(HOST_CONCLUSION_TABLE)
        connection.execute(HOST_CONCLUSION_INDEX)
        if retained_columns(connection, columns)[1] != before:
            raise ValueError("migration changed retained columns")
        connection.execute("UPDATE meta SET value='15' WHERE key='schema_version'")
        if connection.execute("PRAGMA foreign_key_check").fetchall():
            raise ValueError("foreign key check failed after migration")
        if [tuple(row) for row in connection.execute("PRAGMA integrity_check")] != [("ok",)]:
            raise ValueError("integrity check failed after migration")
        connection.execute("COMMIT")
    except BaseException:
        connection.execute("ROLLBACK")
        raise
    return {"fromSchema": 14, "toSchema": 15, "hostConclusions": 0}
