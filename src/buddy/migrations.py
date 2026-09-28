"""In-place board migration from the previous schema, run only by ``upgrade``.

The runtime still never converts a board at startup. ``upgrade`` calls
``migrate_12_to_13`` after the verified backup exists and while it holds the
exclusive daemon and board-owner locks; one ``BEGIN IMMEDIATE`` transaction either
applies every change or none, and any later failure restores that backup.

Schema 13 moves user preferences and notes to the model family (design and data
contract in ``docs/design/buddy-settings.md``): a preference shared by every effort of a family
becomes the family default; anything else stays a per-effort override. Notes are
merged per family, labelled with their source effort when they differ, and never
truncated. Tables other than the three preference/note tables and the schema marker
must be byte-for-byte unchanged, which ``upgrade`` verifies by fingerprint.
"""
from __future__ import annotations

from collections import defaultdict
import sqlite3

from .db import PREVIOUS_SCHEMA_VERSION, SCHEMA_VERSION

#: Tables this migration may rewrite; every other table must keep its fingerprint.
MIGRATED_TABLES = frozenset({"evaluation_preferences", "evaluation_annotations",
                             "family_preferences", "family_annotations", "meta"})

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
    if schema_version(connection) != PREVIOUS_SCHEMA_VERSION:
        raise ValueError(f"migration expects schema {PREVIOUS_SCHEMA_VERSION}")
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
        connection.execute("UPDATE meta SET value=? WHERE key='schema_version'", (str(SCHEMA_VERSION),))
        if connection.execute("PRAGMA foreign_key_check").fetchall():
            raise ValueError("foreign key check failed after migration")
        connection.execute("COMMIT")
    except BaseException:
        connection.execute("ROLLBACK")
        raise
    finally:
        connection.execute("PRAGMA foreign_keys=ON")
    if connection.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
        raise ValueError("integrity check failed after migration")
    return {"fromSchema": PREVIOUS_SCHEMA_VERSION, "toSchema": SCHEMA_VERSION,
            "familyPreferences": merged_families, "preferenceOverrides": overrides,
            "familyAnnotations": merged_notes, "annotationsMerged": len(notes)}
