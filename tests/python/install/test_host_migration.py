"""Host workflow migration preserves historical authority and execution facts."""
from contextlib import closing
from pathlib import Path
import sqlite3
import unittest

from hey_my_buddy.blackboard.store import migrations
from hey_my_buddy.blackboard.store.db import SCHEMA
from blackboard.store.test_migrations import shape


class HostMigrationTests(unittest.TestCase):
    def setUp(self):
        self.connection = sqlite3.connect(":memory:", isolation_level=None)
        self.addCleanup(self.connection.close)
        self.connection.executescript((Path(__file__).resolve().parents[1] / "blackboard/store/fixtures/schema-14.sql").read_text())
        self.connection.execute("INSERT INTO meta VALUES('schema_version','14')")
        self.connection.execute("INSERT INTO meta VALUES('user-policy','unchanged')")
        self.connection.execute("INSERT INTO harness_health(adapter,status,record_json) VALUES('codex','ready','{\"version\":\"fixture\"}')")
        self.connection.execute("INSERT INTO tasks(task_id,request_id,owner,spec_json,spec_canonical_json,input_fingerprint,fingerprint_version,adapter,cwd,timeout_seconds,state,revision,created_at,updated_at) VALUES('r','q','host','{}','{}','digest',2,'codex','/private/fixture',0,'failed',1,'then','then')")
        self.connection.execute("INSERT INTO workflow_runs(run_id,host_id,control_verifier,goal_json,goal_fingerprint,request_fingerprint,execution_workspace_json,state,created_at,updated_at) VALUES('r','host','secret-verifier','{\"model\":\"original\"}','original','request','{}','failed','then','then')")

    def test_fresh_shape_preserves_original_columns_and_defaults_to_unknown(self):
        columns, before = migrations.retained_columns(self.connection)
        self.assertEqual(migrations.migrate_14_to_15(self.connection)["toSchema"], 15)
        self.assertEqual(migrations.retained_columns(self.connection, columns)[1], before)
        # The historical 14→15 migration stays frozen; the ADR-021 pending schema 16
        # reaches every current board through the runtime's idempotent initialization
        # DDL, so both sides of this comparison get the same additive pass before the
        # shapes must match.
        self.assertIsNotNone(self.connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='workflow_host_conclusions'"
        ).fetchone(), "the historical migration itself must create Host conclusion storage")
        self.connection.executescript(SCHEMA)
        with closing(sqlite3.connect(":memory:")) as fresh:
            fresh.executescript(SCHEMA)
            self.assertEqual(shape(self.connection), shape(fresh))
        self.assertEqual(self.connection.execute("SELECT configuration_locked FROM workflow_runs").fetchone(), (0,))
        self.assertEqual(self.connection.execute("SELECT quota_json FROM harness_health").fetchone(), (None,))
        self.assertEqual(self.connection.execute("SELECT * FROM workflow_host_conclusions").fetchall(), [])
        self.assertEqual(self.connection.execute("SELECT value FROM meta WHERE key='user-policy'").fetchone(), ('unchanged',))

    def test_failed_migration_rolls_back_all_added_storage(self):
        before = shape(self.connection)
        self.connection.execute("CREATE TRIGGER refuse_version BEFORE UPDATE ON meta BEGIN SELECT RAISE(ABORT,'injected'); END")
        with self.assertRaises(sqlite3.IntegrityError):
            migrations.migrate_14_to_15(self.connection)
        self.connection.execute("DROP TRIGGER refuse_version")
        self.assertEqual(shape(self.connection), before)
        self.assertEqual(migrations.schema_version(self.connection), 14)

    def test_migration_cannot_reinterpret_an_already_migrated_board(self):
        migrations.migrate_14_to_15(self.connection)
        with self.assertRaises(ValueError):
            migrations.migrate_14_to_15(self.connection)
