"""Explicit offline v5 -> v6 migration: backup, identity preservation, atomicity.

The fixture is a real version-5 database built from the frozen v5 DDL with
representative tasks, attempts, workers, messages, artifacts, events, receipts,
claims and cursors. No test here touches the operator's live state directory.
"""
from __future__ import annotations

import json
import os
import secrets
import sqlite3
import unittest
from pathlib import Path

from support import BoardTestCase

from buddy import migrate as migrate_module
from buddy.db import DB_FILE, SCHEMA_V5, SCHEMA_VERSION, V5_SCHEMA_VERSION
from buddy.errors import BoardError
from buddy.store import BoardStore

NOW = "2026-01-01T00:00:00.000Z"
LATER = "2026-01-01T00:05:00.000Z"

TASK_INSERT = (
    "INSERT INTO tasks(task_id, request_id, owner, spec_json, spec_canonical_json, input_fingerprint,"
    " fingerprint_version, adapter, required_capabilities, cwd, exclusive_resources, timeout_seconds, state,"
    " queue_reason, revision, selected_attempt_id, active_attempt_id, created_at, updated_at, accepted_at,"
    " acceptance_note, acceptance_verdict, legacy, legacy_fingerprint, legacy_fingerprint_version, queue_position)"
    " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"
)
ATTEMPT_INSERT = (
    "INSERT INTO attempts(attempt_id, task_id, generation, worker_id, worker_identity, worker_instance,"
    " capability_version, nonce_verifier, claim_request_id, lease_expires_at, lease_seconds, execution_state,"
    " ownership, runtime_identity, adapter, log_paths, result_json, result_command_id, error, shutdown_confirmed,"
    " cancel_requested_at, exit_code, signal, started_at, finished_at, created_at, updated_at, revision)"
    " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"
)


def _task(
    task_id: str,
    request_id: str,
    state: str,
    *,
    attempt_id: str | None,
    accepted: bool = False,
    legacy: bool = False,
    fingerprint_version: int = 2,
) -> tuple:
    spec = json.dumps({"task": f"task {task_id}", "cwd": "/tmp", "adapter": "command", "timeoutSeconds": 1800, "workspace": True, "requiredCapabilities": [], "exclusiveResources": []})
    return (
        task_id,
        request_id,
        "cli",
        spec,
        spec,
        f"fingerprint-{task_id}",
        fingerprint_version,
        "command",
        "[]",
        "/tmp",
        "[]",
        1800,
        state,
        None if state != "queued" else "awaiting-worker",
        3 if accepted else 1,
        attempt_id,
        attempt_id if state == "running" else None,
        NOW,
        LATER,
        LATER if accepted else None,
        "inspected the diff" if accepted else None,
        "accepted" if accepted else None,
        1 if legacy else 0,
        "legacy-fingerprint" if legacy else None,
        1 if legacy else None,
        None,
    )


def _attempt(attempt_id: str, task_id: str, generation: int, state: str, *, with_result: bool = False) -> tuple:
    result = (
        json.dumps(
            {
                "status": "ok",
                "result": {"finalText": "done"},
                "error": None,
                "exitCode": 0,
                "signal": None,
                "shutdownConfirmed": True,
                "logPaths": None,
                "runtimeIdentity": "runtime:fixture",
                "artifacts": [],
                "completedAt": LATER,
            }
        )
        if with_result
        else None
    )
    return (
        attempt_id,
        task_id,
        generation,
        "worker-1",
        "command:worker-1",
        "instance-1",
        1,
        "a" * 64,
        f"claim-{attempt_id}",
        None if state == "finished" else "2026-01-01T00:10:00.000Z",
        120,
        state,
        "owned",
        "runtime:fixture",
        "command",
        "{}",
        result,
        f"result:{attempt_id}" if with_result else None,
        None,
        1 if with_result else 0,
        None,
        0 if with_result else None,
        None,
        NOW,
        LATER if with_result else None,
        NOW,
        LATER,
        2 if with_result else 1,
    )


def build_v5_board(directory: Path) -> dict:
    """Create a genuine version-5 board with representative durable records."""
    path = Path(directory) / DB_FILE
    connection = sqlite3.connect(path, isolation_level=None, timeout=10)
    try:
        connection.executescript(SCHEMA_V5)
        connection.execute("BEGIN IMMEDIATE")
        connection.execute("INSERT INTO meta(key, value) VALUES('schema_version', ?)", (str(V5_SCHEMA_VERSION),))
        connection.execute("INSERT INTO meta(key, value) VALUES('created_at', ?)", (NOW,))
        connection.execute("INSERT INTO meta(key, value) VALUES('capability_secret', ?)", (secrets.token_hex(32),))
        connection.execute(
            "INSERT INTO workers(worker_id, identity, adapter, capabilities, host, pid, state, current_attempt_id,"
            " registered_at, last_seen_at, revision) VALUES('worker-1','command:worker-1','command','[\"command\"]',"
            "'fixture-host',4242,'busy','attempt-2',?,?,4)",
            (NOW, LATER),
        )
        connection.execute(TASK_INSERT, _task("task-completed", "request-completed", "completed", attempt_id="attempt-1", accepted=True))
        connection.execute(TASK_INSERT, _task("task-queued", "request-queued", "queued", attempt_id=None))
        connection.execute(TASK_INSERT, _task("task-running", "request-running", "running", attempt_id="attempt-2"))
        connection.execute(TASK_INSERT, _task("task-legacy", "legacy-request", "completed", attempt_id="attempt-3", legacy=True, fingerprint_version=1))
        connection.execute(ATTEMPT_INSERT, _attempt("attempt-1", "task-completed", 1, "finished", with_result=True))
        connection.execute(ATTEMPT_INSERT, _attempt("attempt-2", "task-running", 1, "executing"))
        connection.execute(ATTEMPT_INSERT, _attempt("attempt-3", "task-legacy", 1, "finished", with_result=True))
        connection.execute(
            "INSERT INTO messages(message_id, task_id, attempt_id, inquiry_id, direction, author, recipient,"
            " correlation_id, body, body_bytes, payload_hash, state, reason, delivery_json, answer_json,"
            " attempts_count, created_at, updated_at, revision)"
            " VALUES('message-1','task-running','attempt-2','q1','question','cli',NULL,'q1','blocked?',8,?,"
            "'answered',NULL,?,?,1,?,?,2)",
            ("b" * 64, json.dumps({"deliveredAt": LATER}), json.dumps({"text": "no", "bytes": 2}), NOW, LATER),
        )
        connection.execute(
            "INSERT INTO artifacts(artifact_id, task_id, attempt_id, kind, location, content_hash, size_bytes,"
            " verified, created_at) VALUES('artifact-1','task-completed','attempt-1','file','/tmp/out.txt',?,12,1,?)",
            ("c" * 64, LATER),
        )
        for seq, kind in ((1, "task.submitted"), (2, "attempt.claimed"), (3, "task.completed")):
            connection.execute(
                "INSERT INTO events(seq, task_id, attempt_id, revision, kind, payload_json, created_at)"
                " VALUES(?,?,?,?,?,?,?)",
                (seq, "task-completed", "attempt-1", seq, kind, "{}", NOW),
            )
        connection.execute(
            "INSERT INTO commands(command_id, task_id, attempt_id, kind, request_hash, response_json, subject,"
            " created_at) VALUES('command-1','task-completed','attempt-1','task.acknowledge',?,?,NULL,?)",
            ("d" * 64, json.dumps({"task": {"runId": "task-completed"}}), LATER),
        )
        connection.execute(
            "INSERT INTO resource_claims(claim_id, task_id, attempt_id, resource, kind, state, created_at, released_at)"
            " VALUES('claim-1','task-running','attempt-2','/tmp','cwd','held',?,NULL)",
            (NOW,),
        )
        connection.execute("INSERT INTO cursors(consumer, seq, updated_at) VALUES('fixture',3,?)", (LATER,))
        connection.execute("COMMIT")
    finally:
        connection.close()
    os.chmod(path, 0o600)
    return {"database": path}


def read_rows(path: Path) -> dict[str, list[tuple]]:
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        tables = {}
        for table in ("tasks", "attempts", "workers", "messages", "artifacts", "events", "commands", "resource_claims", "cursors"):
            tables[table] = [tuple(row) for row in connection.execute(f"SELECT * FROM {table} ORDER BY rowid")]
        return tables
    finally:
        connection.close()


def schema_version(path: Path) -> int:
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        return int(connection.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0])
    finally:
        connection.close()


class MigrationTestCase(BoardTestCase):
    def fixture(self) -> Path:
        build_v5_board(self.directory)
        return self.directory


class MigrationPlanTests(MigrationTestCase):
    def test_a_v5_database_is_refused_by_a_cold_start_and_points_at_migrate(self):
        directory = self.fixture()
        from buddy.db import Corruption

        with self.assertRaises(Corruption) as caught:
            BoardStore(directory).initialize()
        self.assertIn("buddy migrate", str(caught.exception))
        self.assertEqual(schema_version(directory / DB_FILE), V5_SCHEMA_VERSION)

    def test_dry_run_reports_the_plan_without_touching_anything(self):
        directory = self.fixture()
        before = read_rows(directory / DB_FILE)
        plan = migrate_module.migrate(directory, dry_run=True)
        self.assertTrue(plan["migrated"])
        self.assertTrue(plan["dryRun"])
        self.assertEqual(plan["fromVersion"], V5_SCHEMA_VERSION)
        self.assertEqual(plan["toVersion"], SCHEMA_VERSION)
        self.assertIsNone(plan["backup"])
        self.assertEqual(schema_version(directory / DB_FILE), V5_SCHEMA_VERSION)
        self.assertEqual(read_rows(directory / DB_FILE), before)
        self.assertEqual(migrate_module.backup_files(directory), [])

    def test_confirmation_is_required_before_any_write(self):
        directory = self.fixture()
        error = None
        try:
            migrate_module.migrate(directory)
        except BoardError as caught:
            error = caught
        self.assertIsNotNone(error)
        self.assertEqual(error.code, "CONFIRMATION_REQUIRED")
        self.assertEqual(schema_version(directory / DB_FILE), V5_SCHEMA_VERSION)
        self.assertEqual(migrate_module.backup_files(directory), [])

    def test_unknown_versions_and_insecure_directories_are_refused(self):
        directory = self.fixture()
        path = directory / DB_FILE
        connection = sqlite3.connect(path)
        try:
            connection.execute("UPDATE meta SET value='4' WHERE key='schema_version'")
            connection.commit()
        finally:
            connection.close()
        try:
            migrate_module.migrate(directory, confirm=True)
        except BoardError as caught:
            self.assertEqual(caught.code, "UNSUPPORTED_SCHEMA_VERSION")
        else:  # pragma: no cover - must not migrate
            self.fail("an unknown schema version must not be migrated")
        # Restore a v5 fixture and make the directory non-private.
        connection = sqlite3.connect(path)
        try:
            connection.execute("UPDATE meta SET value='5' WHERE key='schema_version'")
            connection.commit()
        finally:
            connection.close()
        os.chmod(directory, 0o755)
        self.addCleanup(os.chmod, directory, 0o700)
        try:
            migrate_module.migrate(directory, confirm=True)
        except BoardError as caught:
            self.assertEqual(caught.code, "INSECURE_STATE_DIR")
        else:  # pragma: no cover - must not migrate
            self.fail("a non-private state directory must not be migrated")

    def test_a_corrupt_board_is_refused(self):
        directory = self.fixture()
        connection = sqlite3.connect(directory / DB_FILE)
        try:
            connection.execute("PRAGMA foreign_keys=OFF")
            connection.execute(
                "INSERT INTO attempts(attempt_id, task_id, generation, nonce_verifier, claim_request_id,"
                " execution_state, ownership, adapter, created_at, updated_at)"
                " VALUES('dangling-attempt','missing-task',1,'x','c','finished','owned','command',?,?)",
                (NOW, NOW),
            )
            connection.commit()
        finally:
            connection.close()
        try:
            migrate_module.migrate(directory, confirm=True)
        except BoardError as caught:
            self.assertEqual(caught.code, "CORRUPT")
        else:  # pragma: no cover - must not migrate
            self.fail("a corrupt board must not be migrated")


class MigrationExecutionTests(MigrationTestCase):
    def test_migration_preserves_every_identity_and_installs_the_new_schema(self):
        directory = self.fixture()
        path = directory / DB_FILE
        before = read_rows(path)
        result = migrate_module.migrate(directory, confirm=True)
        self.assertTrue(result["migrated"])
        self.assertEqual(result["schemaVersion"], SCHEMA_VERSION)
        self.assertEqual(result["integrity"], "ok")
        self.assertEqual(result["counts"], result["countsAfter"])
        self.assertEqual(result["counts"]["tasks"], 4)
        self.assertEqual(result["counts"]["events"], 3)
        backup = Path(result["backup"])
        self.assertTrue(backup.is_file())
        self.assertEqual(backup.stat().st_mode & 0o777, 0o600)
        self.assertEqual(schema_version(backup), V5_SCHEMA_VERSION)
        self.assertEqual(read_rows(path), before, "no preserved record may change")
        self.assertEqual(schema_version(path), SCHEMA_VERSION)

        # The upgraded board starts, keeps its facts and exposes the new empty table.
        store = BoardStore(directory)
        store.initialize()
        completed = store.task_get({"runId": "task-completed"})["task"]
        self.assertEqual(completed["acceptanceVerdict"], "accepted")
        self.assertEqual(completed["selectedAttemptId"], "attempt-1")
        legacy = store.task_get({"requestId": "legacy-request"})["task"]
        self.assertTrue(legacy["legacy"])
        self.assertEqual(legacy["legacyFingerprint"], "legacy-fingerprint")
        events = store.events_read({"after": 0, "limit": 10})["events"]
        self.assertEqual([event["seq"] for event in events[:3]], [1, 2, 3])
        self.assertEqual(
            [event["kind"] for event in events[:3]], ["task.submitted", "attempt.claimed", "task.completed"]
        )
        self.assertEqual(store.count_tasks(), 4)
        from buddy.evaluation import EvaluationStore

        snapshot = EvaluationStore(store).snapshot({})
        self.assertEqual(snapshot["tableRevision"], 0)
        self.assertEqual(snapshot["profiles"], [])
        self.assertEqual(snapshot["decisions"], [])
        # The restart reconciliation marks the in-flight attempt uncertain - after the
        # identity comparison above - and records why without inventing a stop.
        running = store.task_get({"runId": "task-running"})["task"]
        self.assertEqual(running["selectedAttempt"]["executionState"], "uncertain")
        self.assertEqual(running["queueReason"], "attempt-uncertain-after-restart")
        self.assertEqual(store.events_read({"after": 3, "limit": 10})["events"][0]["kind"], "attempt.uncertain")

    def test_migration_is_idempotent(self):
        directory = self.fixture()
        first = migrate_module.migrate(directory, confirm=True)
        backups = migrate_module.backup_files(directory)
        second = migrate_module.migrate(directory, confirm=True)
        self.assertFalse(second["migrated"])
        self.assertTrue(second["alreadyCurrent"])
        self.assertEqual(len(backups), 1)
        self.assertEqual(len(migrate_module.backup_files(directory)), 1)
        self.assertEqual(schema_version(directory / DB_FILE), SCHEMA_VERSION)

    def test_migration_via_the_cli_envelope(self):
        directory = self.fixture()
        code, plan = self.cli("migrate", json.dumps({"dryRun": True}))
        self.assertEqual(code, 0, plan)
        self.assertTrue(plan["dryRun"])
        self.assertEqual(schema_version(directory / DB_FILE), V5_SCHEMA_VERSION)
        code, applied = self.cli("migrate", json.dumps({"confirm": True}))
        self.assertEqual(code, 0, applied)
        self.assertTrue(applied["migrated"])
        self.assertEqual(applied["schemaVersion"], SCHEMA_VERSION)
        # The migrated board is usable by the real CLI straight away.
        self.addCleanup(lambda: self.cli("stop", "{}", timeout=60))
        code, health = self.cli("health", "{}")
        self.assertEqual(code, 0, health)
        self.assertEqual(health["schemaVersion"], SCHEMA_VERSION)
        code, snapshot = self.cli("console-snapshot", "{}")
        self.assertEqual(code, 0, snapshot)
        self.assertEqual(snapshot["tableRevision"], 0)

    def test_migration_refuses_a_running_service(self):
        directory = self.fixture()
        migrate_module.migrate(directory, confirm=True)
        with self.daemon():
            try:
                migrate_module.migrate(directory, confirm=True)
            except BoardError as caught:
                self.assertEqual(caught.code, "SERVICE_RUNNING")
            else:  # pragma: no cover - must not migrate
                self.fail("a running service owns the state directory")
        self.assertEqual(schema_version(directory / DB_FILE), SCHEMA_VERSION)

    def test_failed_upgrade_rolls_back_and_the_backup_stays_recoverable(self):
        directory = self.fixture()
        path = directory / DB_FILE
        before = read_rows(path)
        original = migrate_module._apply

        def boom(connection, backup_path, counts):
            raise RuntimeError("simulated crash during schema write")

        migrate_module._apply = boom
        try:
            try:
                migrate_module.migrate(directory, confirm=True)
            except BoardError as caught:
                self.assertEqual(caught.code, "MIGRATION_FAILED")
                self.assertIn("rolled back", caught.message)
            else:  # pragma: no cover - must fail
                self.fail("the injected failure must surface")
        finally:
            migrate_module._apply = original
        # The original database is intact and still a v5 board; the backup verifies.
        self.assertEqual(schema_version(path), V5_SCHEMA_VERSION)
        self.assertEqual(read_rows(path), before)
        connection = sqlite3.connect(path)
        try:
            tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        finally:
            connection.close()
        self.assertNotIn("evaluation_state", tables)
        backups = migrate_module.backup_files(directory)
        self.assertEqual(len(backups), 1)
        self.assertEqual(schema_version(backups[0]), V5_SCHEMA_VERSION)
        # Recovery: the same command succeeds afterwards and preserves identity.
        recovered = migrate_module.migrate(directory, confirm=True)
        self.assertTrue(recovered["migrated"])
        self.assertEqual(read_rows(path), before)
        self.assertEqual(schema_version(path), SCHEMA_VERSION)

    def test_directory_without_a_board_is_reported_not_created(self):
        empty = self.directory / "empty"
        empty.mkdir()
        os.chmod(empty, 0o700)
        try:
            migrate_module.migrate(empty, confirm=True)
        except BoardError as caught:
            self.assertEqual(caught.code, "NOT_FOUND")
        else:  # pragma: no cover - must not invent a board
            self.fail("a directory without a board database must not be migrated")
        self.assertFalse((empty / DB_FILE).exists())


if __name__ == "__main__":
    unittest.main()
