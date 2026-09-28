"""The in-place 12 -> 13 migration: family preferences and notes."""
from __future__ import annotations

import sqlite3
import unittest
from pathlib import Path

from buddy import migrations
from buddy.db import SCHEMA

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "schema-12.sql"


def shape(connection: sqlite3.Connection) -> dict:
    objects = connection.execute(
        "SELECT type, name FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' ORDER BY type, name").fetchall()
    columns = {name: [tuple(row) for row in connection.execute(f"PRAGMA table_xinfo('{name}')")]
               for kind, name in objects if kind in ("table", "view")}
    return {"objects": objects, "columns": columns}


class MigrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.connection = sqlite3.connect(":memory:", isolation_level=None)
        self.addCleanup(self.connection.close)
        self.connection.executescript(FIXTURE.read_text())
        self.connection.execute("INSERT INTO meta(key, value) VALUES('schema_version', '12')")

    def profile(self, profile_id: str, model: str, effort: str, adapter: str = "claude") -> None:
        self.connection.execute(
            "INSERT INTO evaluation_profiles(profile_id,label,adapter,provider,model,effort,created_revision,updated_revision)"
            " VALUES(?,?,?,?,?,?,1,1)", (profile_id, profile_id, adapter, "anthropic", model, effort))

    def preference(self, profile_id: str, mode: str, reason: str = "", revision: int = 1) -> None:
        self.connection.execute(
            "INSERT INTO evaluation_preferences(profile_id,mode,reason,updated_revision) VALUES(?,?,?,?)",
            (profile_id, mode, reason, revision))

    def note(self, profile_id: str, text: str, revision: int = 1, at: str = "2026-09-01T00:00:00Z") -> None:
        self.connection.execute(
            "INSERT INTO evaluation_annotations(profile_id,text,revision,updated_at) VALUES(?,?,?,?)",
            (profile_id, text, revision, at))

    def test_shape_matches_a_fresh_schema_13_board(self):
        migrations.migrate_12_to_13(self.connection)
        fresh = sqlite3.connect(":memory:")
        self.addCleanup(fresh.close)
        fresh.executescript(SCHEMA)
        self.assertEqual(shape(self.connection), shape(fresh))
        self.assertEqual(migrations.schema_version(self.connection), 13)

    def test_identical_preferences_merge_into_the_family_default(self):
        for effort in ("low", "high"):
            self.profile(f"sonnet-{effort}", "sonnet", effort)
        self.preference("sonnet-low", "prefer", "fast", revision=3)
        self.preference("sonnet-high", "prefer", "fast", revision=5)
        summary = migrations.migrate_12_to_13(self.connection)
        self.assertEqual((summary["familyPreferences"], summary["preferenceOverrides"]), (1, 0))
        self.assertEqual(self.connection.execute("SELECT * FROM family_preferences").fetchall(),
                         [("claude", "anthropic", "sonnet", "prefer", "fast", 5)])
        self.assertEqual(self.connection.execute("SELECT COUNT(*) FROM evaluation_preferences").fetchone()[0], 0)
        self.assertEqual(self.connection.execute(
            "SELECT profile_id, mode, source FROM effective_preferences ORDER BY profile_id").fetchall(),
            [("sonnet-high", "prefer", "family"), ("sonnet-low", "prefer", "family")])

    def test_partial_or_differing_preferences_stay_per_effort(self):
        for effort in ("low", "medium", "high"):
            self.profile(f"opus-{effort}", "opus", effort)
            self.profile(f"gpt-{effort}", "gpt", effort, adapter="codex")
        self.preference("opus-low", "prefer")
        self.preference("opus-high", "prefer")          # medium has none: not shared
        self.preference("gpt-low", "prefer", "cheap")
        self.preference("gpt-medium", "prefer", "cheap")
        self.preference("gpt-high", "exclude", "cost")  # differing mode
        summary = migrations.migrate_12_to_13(self.connection)
        self.assertEqual((summary["familyPreferences"], summary["preferenceOverrides"]), (0, 5))
        self.assertEqual(self.connection.execute("SELECT COUNT(*) FROM family_preferences").fetchone()[0], 0)
        self.assertEqual(self.connection.execute(
            "SELECT profile_id, mode, source FROM effective_preferences ORDER BY profile_id").fetchall(),
            [("gpt-high", "exclude", "override"), ("gpt-low", "prefer", "override"),
             ("gpt-medium", "prefer", "override"), ("opus-high", "prefer", "override"),
             ("opus-low", "prefer", "override")])

    def test_notes_merge_per_family_without_truncation(self):
        for effort in ("low", "high"):
            self.profile(f"sonnet-{effort}", "sonnet", effort)
            self.profile(f"opus-{effort}", "opus", effort)
        self.profile("flash-high", "flash", "high", adapter="dsh")
        long_text = "细节" * 3000
        self.note("sonnet-low", "same", revision=2)
        self.note("sonnet-high", "same", revision=4, at="2026-09-02T00:00:00Z")
        self.note("opus-low", "fast enough")
        self.note("opus-high", long_text)
        self.note("flash-high", "only one")
        summary = migrations.migrate_12_to_13(self.connection)
        self.assertEqual((summary["familyAnnotations"], summary["annotationsMerged"]), (3, 5))
        notes = {row[0]: row[1:] for row in self.connection.execute(
            "SELECT model, text, revision, updated_at FROM family_annotations")}
        self.assertEqual(notes["sonnet"], ("same", 4, "2026-09-02T00:00:00Z"))
        self.assertEqual(notes["flash"][0], "only one")
        self.assertEqual(notes["opus"][0], f"{long_text}（来自 high 档位）\n\nfast enough（来自 low 档位）")
        self.assertIsNone(self.connection.execute(
            "SELECT name FROM sqlite_master WHERE name='evaluation_annotations'").fetchone())

    def test_none_override_hides_a_family_default(self):
        migrations.migrate_12_to_13(self.connection)
        for effort in ("low", "high"):
            self.profile(f"sonnet-{effort}", "sonnet", effort)
        self.connection.execute(
            "INSERT INTO family_preferences VALUES('claude','anthropic','sonnet','prefer','',1)")
        self.connection.execute("INSERT INTO evaluation_preferences VALUES('sonnet-high','none','',2)")
        self.assertEqual(self.connection.execute("SELECT profile_id FROM effective_preferences").fetchall(),
                         [("sonnet-low",)])

    def test_failure_rolls_back_every_change(self):
        self.profile("sonnet-low", "sonnet", "low")
        self.preference("sonnet-low", "pin")
        self.connection.execute("PRAGMA foreign_keys=OFF")
        self.note("ghost", "orphan")
        with self.assertRaises(ValueError):
            migrations.migrate_12_to_13(self.connection)
        self.assertEqual(migrations.schema_version(self.connection), 12)
        tables = {row[0] for row in self.connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertIn("evaluation_annotations", tables)
        self.assertNotIn("family_preferences", tables)
        self.assertEqual(self.connection.execute("SELECT * FROM evaluation_preferences").fetchall(),
                         [("sonnet-low", "pin", "", 1)])

    def test_refuses_any_other_schema(self):
        self.connection.execute("UPDATE meta SET value='13' WHERE key='schema_version'")
        with self.assertRaises(ValueError):
            migrations.migrate_12_to_13(self.connection)


if __name__ == "__main__":
    unittest.main()
