"""The released schema-18 board becomes a fresh board containing only settings."""
import json
from contextlib import closing
import sqlite3
from pathlib import Path

from support import BoardTestCase
from blackboard.store.upgrade_fixture import load_fixture
from hey_my_buddy.blackboard.routing import router
from hey_my_buddy.blackboard.store import backup
from hey_my_buddy.blackboard.store.db import SCHEMA_VERSION
from hey_my_buddy.blackboard.store.store import BoardStore
from hey_my_buddy.errors import BoardError
from hey_my_buddy.install import upgrade


SETTING_COLUMNS = {
    "buddies": ("buddy_id", "enabled"),
    "evaluation_preferences": ("buddy_id", "mode", "reason"),
    "model_service_notes": ("adapter", "provider", "model", "text"),
    "model_service_concurrency": ("adapter", "provider", "model", "concurrency_limit"),
}
UPGRADE_BASE_TABLES = {
    "buddies": "evaluation_profiles",
    "model_service_notes": "family_annotations",
    "model_service_concurrency": "model_concurrency",
}
UPGRADE_BASE_COLUMNS = {
    "buddies": ("profile_id", "enabled"),
    "evaluation_preferences": ("profile_id", "mode", "reason"),
}
HISTORY_TABLES = (
    "tasks", "attempts", "workflow_runs", "workflow_host_conclusions",
    "workflow_artifacts", "events", "commands", "workspace_reservations",
    "macro_tasks", "decision_requests", "evaluation_decisions",
)


def rows(state, table, columns):
    with closing(sqlite3.connect(state / "board.sqlite3")) as connection:
        fields = ",".join(columns)
        return sorted(connection.execute(f"SELECT {fields} FROM {table}").fetchall())


class UpgradeMigrationTests(BoardTestCase):
    def setUp(self):
        super().setUp()
        self.state = self.directory / "state"
        self.fixture = load_fixture(self.state)

    def test_verified_schema_18_stock_becomes_new_schema_with_only_settings(self):
        original_settings = {table: rows(self.state, UPGRADE_BASE_TABLES[table],
                                         UPGRADE_BASE_COLUMNS.get(table, columns))
                             for table, columns in SETTING_COLUMNS.items() if table != "evaluation_preferences"}
        original_settings["evaluation_preferences"] = rows(
            self.state, "effective_preferences", UPGRADE_BASE_COLUMNS["evaluation_preferences"])
        original_router = dict(rows(self.state, "meta", ("key", "value")))
        expected_router = {
            "routerBuddyIds": json.loads(original_router["router_profile_ids"]),
            "defaultRoutingMode": original_router["router_default_mode"],
            "routingBudget": original_router["router_budget_preset"],
            "routerRetryIntervalSeconds": int(original_router["router_retry_interval_seconds"]),
        }
        current = Path(backup.create(BoardStore(self.state))["path"])
        manifest = backup.verify(current)
        self.assertEqual(manifest["schema"], 18)
        before = {**manifest["databaseSnapshot"], "schema": manifest["schema"]}
        summary, expected = upgrade.migrate_board(self.state, before)

        self.assertEqual(summary["fromSchema"], 18)
        self.assertEqual(summary["toSchema"], SCHEMA_VERSION)
        self.assertEqual(expected["schema"], SCHEMA_VERSION)
        self.assertEqual(upgrade.idle_snapshot(self.state),
                         {key: value for key, value in expected.items() if key != "runtimeSettings"})
        for table, columns in SETTING_COLUMNS.items():
            with self.subTest(table=table):
                self.assertEqual(rows(self.state, table, columns), original_settings[table])
        with closing(sqlite3.connect(self.state / "board.sqlite3")) as connection:
            connection.row_factory = sqlite3.Row
            self.assertEqual(router.configuration(connection), expected_router)
        for table in HISTORY_TABLES:
            with self.subTest(table=table):
                self.assertEqual(rows(self.state, table, ("*",)), [])

    def test_restoring_verified_backup_recovers_schema_18_history_and_settings(self):
        current = Path(backup.create(BoardStore(self.state))["path"])
        manifest = backup.verify(current)
        before = {**manifest["databaseSnapshot"], "schema": manifest["schema"]}
        historical_tasks = rows(self.state, "tasks", ("task_id", "state"))
        upgrade.migrate_board(self.state, before)
        upgrade.restore(self.state, current)
        self.assertEqual(upgrade.idle_snapshot(self.state), before)
        self.assertEqual(rows(self.state, "tasks", ("task_id", "state")), historical_tasks)

    def test_unconcluded_delivery_blocks_archive_without_changing_the_board(self):
        delivered = self.fixture["stableIds"]["delivered"]
        with closing(sqlite3.connect(self.state / "board.sqlite3")) as connection, connection:
            connection.execute("DELETE FROM workflow_host_conclusions WHERE run_id=?", (delivered,))
        before = (self.state / "board.sqlite3").read_bytes()
        with self.assertRaises(BoardError) as caught:
            upgrade.require_archive_readiness(self.state)
        self.assertEqual(caught.exception.code, "UPGRADE_NOT_IDLE")
        self.assertIn({"runId": delivered, "state": "delivered"}, caught.exception.details["active"])
        self.assertEqual((self.state / "board.sqlite3").read_bytes(), before)
