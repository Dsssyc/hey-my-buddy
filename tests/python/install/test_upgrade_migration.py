"""Upgrade of an idle schema-14 board: verified backup, in-place migration, rollback."""
import gzip
import json
import os
import sqlite3
import shutil
import tempfile
from contextlib import closing, ExitStack
from pathlib import Path
from unittest import mock

from hey_my_buddy.blackboard.store import backup, migrations
from hey_my_buddy.install import upgrade
from hey_my_buddy.blackboard.store.db import Database
from hey_my_buddy.errors import BoardError
from hey_my_buddy.blackboard.store.store import BoardStore
from support import BoardTestCase
from blackboard.tasks import test_workspace_identity as identity_fixtures

FIXTURE = Path(__file__).resolve().parents[1] / "blackboard/store/fixtures/schema-12.sql"


class UpgradeMigrationTests(BoardTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        temporary = tempfile.TemporaryDirectory(prefix="buddy-upgrade-schema-")
        cls.addClassCleanup(temporary.cleanup)
        fixture = cls(methodName="runTest")
        fixture.state = Path(temporary.name) / "state"
        fixture.state.mkdir(mode=0o700)
        fixture._seed_schema_14()
        cls._database_template = fixture.state / "board.sqlite3"

    def setUp(self) -> None:
        super().setUp()
        self.state = self.directory / "state"
        self.state.mkdir(mode=0o700)
        # The seed connection is closed before publication. Each case owns a
        # distinct database inode, connections, journals, locks and backup files.
        shutil.copyfile(self._database_template, self.state / "board.sqlite3")

    def _seed_schema_14(self) -> None:
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
            migrations.migrate_13_to_14(connection)

    def schema(self) -> int:
        with closing(sqlite3.connect(self.state / "board.sqlite3")) as connection:
            return migrations.schema_version(connection)

    def backed_up(self) -> tuple[Path, dict]:
        current = Path(backup.create(BoardStore(self.state))["path"])
        manifest = backup.verify(current)
        return current, {**manifest["databaseSnapshot"], "schema": manifest["schema"]}

    def test_backup_records_schema_14_and_proves_it_migrates_without_touching_the_source(self):
        current, before = self.backed_up()
        self.assertEqual(before["schema"], 14)
        self.assertEqual(upgrade.idle_snapshot(self.state)["schema"], 14)
        self.assertEqual(self.schema(), 14)
        with gzip.open(current / "board.sqlite3.gz", "rb") as stream:
            self.assertIn(b"family_annotations", stream.read())

    def test_migration_moves_only_declared_tables_and_the_board_opens_as_current(self):
        _current, before = self.backed_up()
        summary, expected = upgrade.migrate_board(self.state, before)
        self.assertEqual(summary["hostConclusions"], 0)
        self.assertEqual(expected["schema"], 15)
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
        original = migrations.migrate_14_to_15

        def collateral(connection):
            summary = original(connection)
            connection.execute("UPDATE evaluation_profiles SET label='rewritten'")
            return summary

        with mock.patch("hey_my_buddy.blackboard.store.migrations.migrate_14_to_15", collateral):
            with self.assertRaises(BoardError) as caught:
                upgrade.migrate_board(self.state, before)
        self.assertEqual(caught.exception.code, "UPGRADE_MIGRATION_FAILED")
        self.assertIn("evaluation_profiles", caught.exception.details["tables"])

    def test_restore_returns_a_migrated_board_to_its_schema_14_backup(self):
        current, before = self.backed_up()
        upgrade.migrate_board(self.state, before)
        self.assertEqual(self.schema(), 15)
        upgrade.restore(self.state, current)
        restored = upgrade.idle_snapshot(self.state, event_head=before["eventHead"])
        self.assertEqual(restored["schema"], 14)
        self.assertEqual(restored["fingerprints"], before["fingerprints"])

    def test_verification_expects_the_schema_of_the_runtime_being_verified(self):
        _current, before = self.backed_up()
        target = self.directory / "runtime-previous"
        health = {"runtimeContentId": target.name, "runtimeStable": True, "schemaVersion": 15}
        with self.assertRaises(BoardError) as caught:
            upgrade.verify_started(self.state, target, health, before)
        self.assertEqual(caught.exception.code, "UPGRADE_VERIFY_FAILED")
        with mock.patch("hey_my_buddy.install.upgrade.command", return_value={"leaks": []}):
            evidence = upgrade.verify_started(self.state, target, {**health, "schemaVersion": 14}, before)
            self.assertEqual(evidence["schemaVersion"], 14)


class UpgradeWorkspaceIdentityTests(identity_fixtures.WorkspaceIdentityFixture):
    """Run the actual upgrade coordinator on private state without processes.

    Lifecycle process seams are substituted. The maintenance locks, verified
    backup, migration, exact fingerprint check and rollback use real files/SQL.
    No service, Worker, runtime materialization or login operation runs.
    """

    def private_upgrade(self, *, reject_verification=False):
        board, run, manifest = self.legacy_run()
        board.call("workflow_cancel", {"runId": run["runId"], "reason": "private idle fixture",
                                       **self.control(run)})
        initial = self.all_rows(board)
        root = self.directory / "private-runtimes"
        previous, target = root / ("a" * 32), root / ("b" * 32)
        previous.mkdir(parents=True)
        target.mkdir()
        endpoint = {"runtimeIdentity": "runtime:" + previous.name, "serviceId": "private-service",
                    "pid": 123, "contractVersion": "private-fixture"}
        (self.directory / "control.json").write_text(json.dumps(endpoint))
        health = {**endpoint, "maxConcurrent": 2, "waitCapacity": 4}
        started = {"runtimeContentId": target.name, "runtimeStable": True, "schemaVersion": 15,
                   "maxConcurrent": 2, "waitCapacity": 4}
        original_verify = upgrade.verify_started

        def verify(state, runtime_path, observed, expected):
            if reject_verification and runtime_path == target:
                raise BoardError("UPGRADE_VERIFY_FAILED", "private target verification failure")
            return original_verify(state, runtime_path, observed, expected)

        with ExitStack() as stack:
            stack.enter_context(mock.patch.dict(os.environ, {"BUDDY_STATE_DIR": str(self.directory),
                                                            "BUDDY_RUNTIME_ROOT": str(root)}))
            for name, options in (
                ("get_state_dir", {"return_value": self.directory}),
                ("runtime.is_ready", {"return_value": True}),
                ("runtime.read_ready", {"return_value": {"sourceCommit": "private-fixture"}}),
                ("runtime.materialize", {"return_value": {"runtimeDir": str(target)}}),
                ("probe", {"return_value": health}),
                ("detach", {}),
                ("start", {"side_effect": lambda state, runtime_path: {**started, "runtimeContentId": runtime_path.name}}),
                ("command", {"return_value": {"leaks": []}}),
                ("verify_started", {"side_effect": verify}),
            ):
                stack.enter_context(mock.patch("hey_my_buddy.install.upgrade." + name, **options))
            stack.enter_context(mock.patch("hey_my_buddy.install.launcher.write_active_runtime"))
            stack.enter_context(mock.patch("hey_my_buddy.blackboard.tasks.storage.prune_old_runtimes", return_value={"complete": True}))
            result = upgrade.upgrade({})
        return board, run, manifest, initial, result

    def test_real_upgrade_calls_identity_migration_after_verified_backup(self):
        board, run, manifest, initial, result = self.private_upgrade()
        self.assertTrue(result["upgraded"], result)
        verified_backup = backup.verify(Path(result["backup"]["path"]))
        self.assertEqual(verified_backup["schema"], 15)
        self.assertTrue(result["verification"]["retainedDataFingerprints"])
        current = self.all_rows(board)
        actual = self.workspace.inspect(manifest["path"])
        with board.store.db.read() as connection:
            self.assertEqual(connection.execute("SELECT project_id FROM objectives WHERE objective_id=?", (run["objectiveId"],)).fetchone()[0], actual["repositoryId"])
            self.assertEqual(connection.execute("SELECT checkout_id FROM workspace_reservations WHERE holder_task_id=?", (run["runId"],)).fetchone()[0], actual["checkoutId"])
        for table in initial:
            if table not in {"meta", "objectives", "workspace_reservations"}:
                self.assertEqual(current[table], initial[table], table)
        journal = json.loads((self.directory / "upgrade-last.json").read_text())
        self.assertIn(manifest["checkoutId"], journal["identityMigration"]["mapped"])

    def test_post_migration_failure_restores_exact_private_backup(self):
        board, _run, _manifest, initial, result = self.private_upgrade(reject_verification=True)
        self.assertFalse(result["upgraded"], result)
        self.assertEqual(result["rollback"]["runtimeContentId"], "a" * 32, result)
        self.assertEqual(self.all_rows(board), initial)
