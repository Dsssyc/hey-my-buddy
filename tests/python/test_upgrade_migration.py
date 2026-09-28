"""Upgrade of an idle schema-13 board: verified backup, in-place migration, rollback."""
import gzip
import sqlite3
from contextlib import closing
from pathlib import Path
from unittest import mock

from buddy import backup, migrations, upgrade
from buddy.db import Database
from buddy.errors import BoardError
from buddy.store import BoardStore
from support import BoardTestCase

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "schema-12.sql"


class UpgradeMigrationTests(BoardTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.state = self.directory / "state"
        self.state.mkdir(mode=0o700)
        with closing(sqlite3.connect(self.state / "board.sqlite3")) as connection:
            connection.executescript(FIXTURE.read_text())
            connection.executemany("INSERT INTO meta(key, value) VALUES(?, ?)",
                                   [("schema_version", "12"), ("created_at", "2026-09-01T00:00:00Z"),
                                    ("capability_secret", "ab" * 32)])
            connection.execute("INSERT INTO evaluation_state(id, created_at, updated_at) VALUES(1, 'then', 'then')")
            for effort in ("low", "high"):
                connection.execute(
                    "INSERT INTO evaluation_profiles(profile_id,label,adapter,provider,model,effort,created_revision,updated_revision)"
                    " VALUES(?,?,'claude','anthropic','sonnet',?,1,1)", (f"sonnet-{effort}", f"Sonnet {effort}", effort))
                connection.execute("INSERT INTO evaluation_preferences VALUES(?, 'prefer', 'steady', 2)", (f"sonnet-{effort}",))
                connection.execute("INSERT INTO evaluation_annotations VALUES(?, ?, 3, 'then')",
                                   (f"sonnet-{effort}", f"{effort} note"))
            connection.commit()
            migrations.migrate_12_to_13(connection)

    def schema(self) -> int:
        with closing(sqlite3.connect(self.state / "board.sqlite3")) as connection:
            return migrations.schema_version(connection)

    def backed_up(self) -> tuple[Path, dict]:
        current = Path(backup.create(BoardStore(self.state))["path"])
        manifest = backup.verify(current)
        return current, {**manifest["databaseSnapshot"], "schema": manifest["schema"]}

    def test_backup_records_schema_13_and_proves_it_migrates_without_touching_the_source(self):
        current, before = self.backed_up()
        self.assertEqual(before["schema"], 13)
        self.assertEqual(upgrade.idle_snapshot(self.state)["schema"], 13)
        self.assertEqual(self.schema(), 13)
        with gzip.open(current / "board.sqlite3.gz", "rb") as stream:
            self.assertIn(b"family_annotations", stream.read())

    def test_migration_moves_only_declared_tables_and_the_board_opens_as_current(self):
        _current, before = self.backed_up()
        summary, expected = upgrade.migrate_board(self.state, before)
        self.assertEqual(summary["harnessHealth"], 0)
        self.assertEqual(expected["schema"], 14)
        for name, value in before["fingerprints"].items():
            if name not in migrations.MIGRATED_TABLES:
                self.assertEqual(expected["fingerprints"][name], value, name)
        Database(self.state).initialize()
        with closing(sqlite3.connect(self.state / "board.sqlite3")) as connection:
            self.assertEqual(connection.execute("SELECT value FROM meta WHERE key='capability_secret'").fetchone()[0],
                             "ab" * 32)
            self.assertEqual(connection.execute("SELECT text FROM family_annotations").fetchone()[0],
                             "high note（来自 high 档位）\n\nlow note（来自 low 档位）")

    def test_a_migration_that_changes_other_tables_is_refused(self):
        _current, before = self.backed_up()
        original = migrations.migrate_13_to_14

        def collateral(connection):
            summary = original(connection)
            connection.execute("UPDATE evaluation_profiles SET label='rewritten'")
            return summary

        with mock.patch("buddy.migrations.migrate_13_to_14", collateral):
            with self.assertRaises(BoardError) as caught:
                upgrade.migrate_board(self.state, before)
        self.assertEqual(caught.exception.code, "UPGRADE_MIGRATION_FAILED")
        self.assertIn("evaluation_profiles", caught.exception.details["tables"])

    def test_restore_returns_a_migrated_board_to_its_schema_13_backup(self):
        current, before = self.backed_up()
        upgrade.migrate_board(self.state, before)
        self.assertEqual(self.schema(), 14)
        upgrade.restore(self.state, current)
        restored = upgrade.idle_snapshot(self.state, event_head=before["eventHead"])
        self.assertEqual(restored["schema"], 13)
        self.assertEqual(restored["fingerprints"], before["fingerprints"])

    def test_verification_expects_the_schema_of_the_runtime_being_verified(self):
        _current, before = self.backed_up()
        target = self.directory / "runtime-previous"
        health = {"runtimeContentId": target.name, "runtimeStable": True, "schemaVersion": 14}
        with self.assertRaises(BoardError) as caught:
            upgrade.verify_started(self.state, target, health, before)
        self.assertEqual(caught.exception.code, "UPGRADE_VERIFY_FAILED")
        with mock.patch("buddy.upgrade.command", return_value={"leaks": []}):
            evidence = upgrade.verify_started(self.state, target, {**health, "schemaVersion": 13}, before)
        self.assertEqual(evidence["schemaVersion"], 13)
