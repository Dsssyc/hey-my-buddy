"""A private installed daemon survives the schema-25 to schema-26 cutover."""
from __future__ import annotations

from contextlib import closing
import gzip
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
from unittest import mock

from blackboard.store.upgrade_fixture import load_fixture
from hey_my_buddy.blackboard.store import upgrade as store_upgrade
from hey_my_buddy.blackboard.store.db import SCHEMA_VERSION
from hey_my_buddy.install import launcher, runtime, upgrade
from support import BoardTestCase
from install import test_upgrade_host_cutover as host_cutover


def _json(value):
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


def _digest(value):
    return hashlib.sha256(_json(value).encode()).hexdigest()


def _git(repository, *args):
    environment = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    environment.update(GIT_AUTHOR_NAME="fixture", GIT_AUTHOR_EMAIL="fixture@example.invalid",
                       GIT_COMMITTER_NAME="fixture", GIT_COMMITTER_EMAIL="fixture@example.invalid")
    return subprocess.run(["git", "-c", "core.hooksPath=/dev/null", "-C", str(repository), *args],
                          env=environment, check=True, capture_output=True).stdout.decode().strip()


class SchemaCutoverTests(BoardTestCase):
    # Reuse the real copied runtime and private venv construction without
    # inheriting the separate Host cutover tests.
    private_runtime = host_cutover.HostCutoverTests.private_runtime

    def setUp(self):
        super().setUp()
        self.fixture = load_fixture(self.directory)
        self._make_truncated_output()
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
        self.history = {path.relative_to(self.directory): path.read_bytes()
                        for path in (self.directory / "workspaces").rglob("*") if path.is_file()}
        with closing(sqlite3.connect(self.directory / "board.sqlite3")) as connection:
            self.attempts = connection.execute("SELECT attempt_id,result_json FROM attempts ORDER BY attempt_id").fetchall()

    def _make_truncated_output(self):
        repository = self.fixture["fixtureRoot"] / "repository"
        # The fixed stock is unchanged; only its private restored repository
        # receives a real child commit with add, change, and deletion evidence.
        (repository / "README.md").write_text("Cutover changed this file.\n")
        for number in range(520):
            (repository / f"cutover-{number:03}.txt").write_text(f"Cutover file {number}.\n")
        (repository / ".gitignore").unlink()
        _git(repository, "add", "-A")
        _git(repository, "commit", "--quiet", "-m", "private cutover evidence")
        output_commit = _git(repository, "rev-parse", "HEAD")
        output_tree = _git(repository, "rev-parse", "HEAD^{tree}")
        input_commit = self.fixture["workspaceInputCommit"]
        changed_raw = subprocess.run(["git", "-C", str(repository), "diff-tree", "-r", "--no-renames",
                                      "--name-only", "-z", input_commit, output_commit, "--"],
                                     check=True, capture_output=True).stdout
        changed = sorted(os.fsdecode(path) for path in changed_raw.split(b"\0") if path)
        self.assertGreater(len(changed), 512)
        tree_raw = subprocess.run(["git", "-C", str(repository), "ls-tree", "-rz", output_commit],
                                  check=True, capture_output=True).stdout
        tree_entries = {}
        for record in tree_raw.split(b"\0"):
            if record:
                metadata, path = record.split(b"\t", 1)
                mode, kind, oid = metadata.decode().split()
                if kind == "blob":
                    tree_entries[os.fsdecode(path)] = [mode, oid]
        self.expected_entries = {path: tree_entries.get(path) for path in changed}
        patch = subprocess.run(["git", "-C", str(repository), "diff", "--no-ext-diff", "--binary",
                                input_commit, output_commit, "--"], check=True, capture_output=True).stdout
        with closing(sqlite3.connect(self.directory / "board.sqlite3")) as connection, connection:
            connection.row_factory = sqlite3.Row
            row = connection.execute("SELECT artifact_id,manifest_json FROM workflow_artifacts "
                                     "WHERE kind='output' ORDER BY artifact_id LIMIT 1").fetchone()
            self.artifact_id = row["artifact_id"]
            artifact = json.loads(row["manifest_json"])
            snapshot = artifact["snapshot"]
            artifact["commit"] = snapshot["commit"] = output_commit
            artifact["tree"] = snapshot["tree"] = output_tree
            artifact["changedPaths"] = snapshot["changedPaths"] = changed
            snapshot["changedEntries"] = {path: self.expected_entries[path] for path in changed[:512]}
            snapshot["changedEntriesTruncated"] = True
            artifact["diffSha256"] = snapshot["diffSha256"] = hashlib.sha256(patch).hexdigest()
            artifact["snapshotSha256"] = _digest(snapshot)
            connection.execute("UPDATE workflow_artifacts SET manifest_json=?,manifest_sha256=? WHERE artifact_id=?",
                               (_json(artifact), artifact["snapshotSha256"], self.artifact_id))
        _git(repository, "update-ref", artifact["ref"], output_commit)
        output_dir = Path(artifact["diffPath"]).parent
        Path(artifact["diffPath"]).write_bytes(patch)
        for name in ("pending.json", "output.json"):
            path = output_dir / name
            if path.exists():
                physical = json.loads(path.read_text())
                physical.update({key: artifact[key] for key in
                                 ("commit", "tree", "changedPaths", "diffSha256", "snapshotSha256")})
                physical["snapshot"] = snapshot
                path.write_text(_json(physical))
        self.physical = {path: path.read_bytes() for path in (output_dir / "pending.json", output_dir / "output.json")
                         if path.exists()}

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
            self.assertEqual(connection.execute("SELECT attempt_id,result_json FROM attempts ORDER BY attempt_id").fetchall(),
                             self.attempts)

    def _assert_old_restored(self, result, backup_bytes):
        self.assertFalse(result["upgraded"], result)
        self.assertEqual(result["rollback"]["runtimeContentId"], self.previous.name)
        self.assertEqual(upgrade.command(self.directory, self.previous, "health")["schemaVersion"], 25)
        self.assertEqual((self.directory / "board.sqlite3").read_bytes(), backup_bytes)
        self._assert_history(converted=False)
        for path, before in self.physical.items():
            self.assertEqual(path.read_bytes(), before)

    def test_real_private_daemon_converts_truncated_output_and_preserves_history(self):
        def action():
            result = upgrade.upgrade({})
            self.assertTrue(result["upgraded"], result)
            self.assertEqual(result["verification"]["schemaVersion"], SCHEMA_VERSION)
            journal = json.loads((self.directory / "upgrade-last.json").read_text())
            self.assertEqual(journal["before"]["schema"], 25)
            self.assertEqual(journal["expected"]["schema"], SCHEMA_VERSION)
            self.assertEqual(upgrade.command(self.directory, self.target, "health")["schemaVersion"], SCHEMA_VERSION)
            with closing(sqlite3.connect(self.directory / "board.sqlite3")) as connection:
                raw = connection.execute("SELECT manifest_json FROM workflow_artifacts WHERE artifact_id=?",
                                         (self.artifact_id,)).fetchone()[0]
            snapshot = json.loads(raw)["snapshot"]
            self.assertEqual(snapshot["changedEntries"], self.expected_entries)
            self.assertNotIn("changedEntriesTruncated", snapshot)
            self._assert_history(converted=True)
            for path in self.physical:
                record = json.loads(path.read_text())
                self.assertEqual(record["snapshot"]["changedEntries"], self.expected_entries)
                self.assertEqual(record["snapshotSha256"], _digest(record["snapshot"]))
            with closing(sqlite3.connect(self.directory / "board.sqlite3")) as connection:
                for raw, digest in connection.execute("SELECT manifest_json,manifest_sha256 FROM workflow_artifacts "
                                                      "WHERE kind IN ('output','resolved-output','partial-output','abandoned-site')"):
                    artifact = json.loads(raw)
                    self.assertEqual(digest, _digest(artifact["snapshot"]))
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

    def test_unrelated_metadata_change_rejects_conversion_and_rolls_back(self):
        def action():
            original = store_upgrade.upgrade_schema_25_to_26

            def alter_metadata(state):
                converted = original(state)
                with closing(sqlite3.connect(state / "board.sqlite3")) as connection, connection:
                    connection.execute("UPDATE meta SET value='changed-during-conversion' "
                                       "WHERE key='capability_secret'")
                return converted

            with mock.patch.object(store_upgrade, "upgrade_schema_25_to_26", side_effect=alter_metadata):
                result = upgrade.upgrade({})
            with gzip.open(Path(result["backup"]["path"]) / "board.sqlite3.gz", "rb") as source:
                backup_bytes = source.read()
            self.assertEqual(result["error"]["details"]["reason"]["code"], "UPGRADE_VERIFY_FAILED")
            self.assertIn("meta", result["error"]["details"]["reason"]["details"]["tables"])
            self._assert_old_restored(result, backup_bytes)
        self._run_with_previous(action)

    def test_unrelated_output_manifest_change_rejects_conversion_and_rolls_back(self):
        def action():
            original = store_upgrade.upgrade_schema_25_to_26

            def alter_manifest(state):
                converted = original(state)
                with closing(sqlite3.connect(state / "board.sqlite3")) as connection, connection:
                    raw = connection.execute("SELECT manifest_json FROM workflow_artifacts WHERE artifact_id=?",
                                             (self.artifact_id,)).fetchone()[0]
                    artifact = json.loads(raw)
                    artifact["note"] = "injected unrelated edit"
                    connection.execute("UPDATE workflow_artifacts SET manifest_json=? WHERE artifact_id=?",
                                       (_json(artifact), self.artifact_id))
                return converted

            with mock.patch.object(store_upgrade, "upgrade_schema_25_to_26", side_effect=alter_manifest):
                result = upgrade.upgrade({})
            with gzip.open(Path(result["backup"]["path"]) / "board.sqlite3.gz", "rb") as source:
                backup_bytes = source.read()
            self.assertEqual(result["error"]["details"]["reason"]["code"], "UPGRADE_VERIFY_FAILED")
            self.assertIn("workflow_artifacts", result["error"]["details"]["reason"]["details"]["tables"])
            self._assert_old_restored(result, backup_bytes)
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

            def interrupt_after_commit(state):
                original(state)
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
