"""A private installed daemon survives the schema-25 to schema-26 cutover."""
from __future__ import annotations

from contextlib import closing
import gzip
import hashlib
import json
import os
from pathlib import Path
import sqlite3
from unittest import mock

from blackboard.store.upgrade_fixture import load_fixture
from hey_my_buddy.blackboard.store import upgrade as store_upgrade
from hey_my_buddy.blackboard.store.db import SCHEMA_VERSION
from hey_my_buddy.errors import BoardError
from hey_my_buddy.install import launcher, runtime, upgrade
from support import BoardTestCase
from install import test_upgrade_host_cutover as host_cutover


def _json(value):
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


def _digest(value):
    return hashlib.sha256(_json(value).encode()).hexdigest()


class SchemaCutoverTests(BoardTestCase):
    # Reuse the real copied runtime and private venv construction without
    # inheriting the separate Host cutover tests.
    private_runtime = host_cutover.HostCutoverTests.private_runtime

    def setUp(self):
        super().setUp()
        self.fixture = load_fixture(self.directory)
        self.catalog_fixture()
        original_copy = runtime._copy_assets

        def copy_old_assets(root, destination):
            original_copy(root, destination)
            for relative, old, new in (
                ("blackboard/store/db.py", "SCHEMA_VERSION = 26", "SCHEMA_VERSION = 25"),
                ("protocol/contracts.py", 'CONTRACT_VERSION = "0.47.0"',
                 'CONTRACT_VERSION = "0.46.0"'),
            ):
                source = destination / "src/hey_my_buddy" / relative
                text = source.read_text()
                self.assertEqual(text.count(old), 1)
                source.write_text(text.replace(old, new))

        with mock.patch.object(runtime, "_copy_assets", side_effect=copy_old_assets):
            self.previous = self.private_runtime("schema-25")
        self.target = self.private_runtime("schema-26")
        self.environment = self.child_environment({"HMB_RUNTIME_ROOT": str(self.previous.parent),
                                                   "HMB_MAX_CONCURRENT": "1"})
        self.environment.pop("PYTHONPATH", None)
        self.environment.pop("HMB_DEV_SOURCE", None)
        self.physical = self._physical_stock()
        self.history = {path.relative_to(self.directory): path.read_bytes()
                        for path in (self.directory / "workspaces").rglob("*") if path.is_file()}
        with closing(sqlite3.connect(self.directory / "board.sqlite3")) as connection:
            self.attempts = connection.execute("SELECT attempt_id,result_json FROM attempts ORDER BY attempt_id").fetchall()

    def _physical_stock(self):
        plan = store_upgrade.plan_schema_25_to_26(self.directory)
        return {path: original for path, (original, _) in plan["physical"].items()}

    def _fixture_modules(self, path):
        return {**self.original_modules(path), "daemon": "hey_my_buddy._upgrade_fixture_daemon"}

    def _run_with_previous(self, action):
        self.original_modules = launcher.entry_modules
        with mock.patch.dict(os.environ, self.environment, clear=True), \
             mock.patch.object(launcher, "entry_modules", side_effect=self._fixture_modules), \
             mock.patch.object(upgrade.runtime, "materialize", return_value={"runtimeDir": str(self.target)}):
            old = upgrade.start(self.directory, self.previous)
            self.assertEqual(old["schemaVersion"], 25)
            self.assertEqual(old["contractVersion"], "0.46.0")
            launcher.write_active_runtime(self.directory, self.previous)
            try:
                return action()
            finally:
                endpoint = self.directory / "control.json"
                if endpoint.exists():
                    identity = json.loads(endpoint.read_text()).get("runtimeIdentity", "")
                    active = self.previous.parent / identity.removeprefix("runtime:")
                    if active in (self.previous, self.target):
                        upgrade.detach(self.directory, active, contract_changed=False)

    def _assert_history(self, *, converted):
        for relative, before in self.history.items():
            path = self.directory / relative
            if converted and "outputs" in relative.parts and relative.name in (
                "pending.json", "output.json", "resolve-restore-pending.json", "resolve-restore.json",
                "resolve-adopt-pending.json", "resolve-adopt.json", "abandoned-pending.json",
                "abandoned.json",
            ):
                continue
            self.assertEqual(path.read_bytes(), before, str(relative))
        with closing(sqlite3.connect(self.directory / "board.sqlite3")) as connection:
            actual = connection.execute("SELECT attempt_id,result_json FROM attempts ORDER BY attempt_id").fetchall()
            if not converted:
                self.assertEqual(actual, self.attempts)
            else:
                self.assertEqual([row[0] for row in actual], [row[0] for row in self.attempts])
                for _, raw in actual:
                    if raw:
                        seal = json.loads(raw).get("result", {}).get("workspaceSeal")
                        if seal:
                            self.assertNotIn("changedEntries", seal["snapshot"])
                            self.assertNotIn("changedEntriesTruncated", seal["snapshot"])

    def _assert_old_restored(self, result, backup_bytes):
        self.assertFalse(result["upgraded"], result)
        self.assertEqual(result["rollback"]["runtimeContentId"], self.previous.name)
        self.assertEqual(upgrade.command(self.directory, self.previous, "health")["schemaVersion"], 25)
        self.assertEqual((self.directory / "board.sqlite3").read_bytes(), backup_bytes)
        self._assert_history(converted=False)
        for path, before in self.physical.items():
            self.assertEqual(path.read_bytes(), before)

    def test_real_private_daemon_removes_fields_from_all_saved_seals(self):
        def action():
            result = upgrade.upgrade({})
            self.assertTrue(result["upgraded"], result)
            self.assertEqual(result["verification"]["schemaVersion"], SCHEMA_VERSION)
            journal = json.loads((self.directory / "upgrade-last.json").read_text())
            self.assertEqual(journal["before"]["schema"], 25)
            self.assertEqual(journal["expected"]["schema"], SCHEMA_VERSION)
            self._assert_history(converted=True)
            for path in self.physical:
                record = json.loads(path.read_text())
                self.assertNotIn("changedEntries", record["snapshot"])
                self.assertNotIn("changedEntriesTruncated", record["snapshot"])
                self.assertEqual(record["snapshotSha256"], _digest(record["snapshot"]))
            with closing(sqlite3.connect(self.directory / "board.sqlite3")) as connection:
                for raw, digest in connection.execute("SELECT manifest_json,manifest_sha256 FROM workflow_artifacts"):
                    artifact = json.loads(raw)
                    if "snapshotSha256" in artifact:
                        self.assertEqual(digest, _digest(artifact["snapshot"]))
                        self.assertNotIn("changedEntries", artifact["snapshot"])
                        self.assertNotIn("changedEntriesTruncated", artifact["snapshot"])
        self._run_with_previous(action)

    def test_physical_write_failure_restores_schema_25_and_old_daemon(self):
        def action():
            original = store_upgrade._replace_record
            writes = []

            def fail_after_first(path, data):
                original(path, data)
                writes.append(path)
                if len(writes) == 1:
                    raise OSError("injected physical write failure")

            with mock.patch.object(store_upgrade, "_replace_record", side_effect=fail_after_first):
                result = upgrade.upgrade({})
            self.assertGreaterEqual(len(writes), 1)
            with gzip.open(Path(result["backup"]["path"]) / "board.sqlite3.gz", "rb") as source:
                backup_bytes = source.read()
            self._assert_old_restored(result, backup_bytes)
        self._run_with_previous(action)

    def test_invalid_seal_rejects_before_backup_or_physical_write(self):
        def action():
            with closing(sqlite3.connect(self.directory / "board.sqlite3")) as connection:
                connection.execute("UPDATE workflow_artifacts SET manifest_sha256='invalid' "
                                   "WHERE artifact_id=(SELECT artifact_id FROM workflow_artifacts WHERE kind='output' LIMIT 1)")
                connection.commit()
            originals = {path: path.read_bytes() for path in self.physical}
            result = upgrade.upgrade({})
            self.assertFalse(result["upgraded"], result)
            self.assertEqual({path: path.read_bytes() for path in originals}, originals)
            self.assertFalse((self.directory / "backups" / "current").exists())
            self.assertEqual(upgrade.command(self.directory, self.previous, "health")["schemaVersion"], 25)
        self._run_with_previous(action)

    def test_failed_new_runtime_restores_original_record_bytes(self):
        def action():
            path = next(iter(self.physical))
            original = path.read_bytes()
            path.write_bytes(b" \n" + original + b"\n")
            self.physical[path] = path.read_bytes()
            self.history[path.relative_to(self.directory)] = path.read_bytes()
            start = upgrade.start

            def fail_target(state, target):
                if target == self.target:
                    raise BoardError("INJECTED_START", "replacement did not start")
                return start(state, target)

            with mock.patch.object(upgrade, "start", side_effect=fail_target):
                result = upgrade.upgrade({})
            self.assertFalse(result["upgraded"], result)
            self.assertEqual(result["error"]["details"]["reason"]["code"], "INJECTED_START")
            self.assertEqual(path.read_bytes(), self.physical[path])
            self.assertEqual(upgrade.command(self.directory, self.previous, "health")["schemaVersion"], 25)
        self._run_with_previous(action)

    def test_interrupt_during_conversion_recovers_old_daemon_then_retries(self):
        def action():
            original = store_upgrade._replace_record
            writes = []

            def interrupt_after_first(path, data):
                original(path, data)
                writes.append(path)
                if len(writes) == 1:
                    raise KeyboardInterrupt("injected interruption after physical write")

            with mock.patch.object(store_upgrade, "_replace_record", side_effect=interrupt_after_first):
                with self.assertRaises(KeyboardInterrupt):
                    upgrade.upgrade({})
            self.assertEqual(json.loads((self.directory / "upgrade.json").read_text())["phase"], "converting")
            result = upgrade.upgrade({})
            with gzip.open(Path(result["backup"]["path"]) / "board.sqlite3.gz", "rb") as source:
                backup_bytes = source.read()
            self._assert_old_restored(result, backup_bytes)
            self.assertTrue(result["recoveredJournal"])
            self.assertFalse((self.directory / "upgrade.json").exists())
            retried = upgrade.upgrade({})
            self.assertTrue(retried["upgraded"], retried)
        self._run_with_previous(action)

    def test_interrupt_after_database_commit_recovers_schema_25(self):
        def action():
            original = store_upgrade.upgrade_schema_25_to_26

            def interrupt_after_commit(state, *, plan):
                original(state, plan=plan)
                raise KeyboardInterrupt("injected interruption after schema commit")

            with mock.patch.object(store_upgrade, "upgrade_schema_25_to_26", side_effect=interrupt_after_commit):
                with self.assertRaises(KeyboardInterrupt):
                    upgrade.upgrade({})
            self.assertEqual(json.loads((self.directory / "upgrade.json").read_text())["phase"], "converting")
            with closing(sqlite3.connect(self.directory / "board.sqlite3")) as connection:
                self.assertEqual(connection.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0],
                                 str(SCHEMA_VERSION))
            self.assertTrue(any(path.read_bytes() != old for path, old in self.physical.items()))
            result = upgrade.upgrade({})
            with gzip.open(Path(result["backup"]["path"]) / "board.sqlite3.gz", "rb") as source:
                backup_bytes = source.read()
            self.assertTrue(result["recoveredJournal"])
            self._assert_old_restored(result, backup_bytes)
        self._run_with_previous(action)
