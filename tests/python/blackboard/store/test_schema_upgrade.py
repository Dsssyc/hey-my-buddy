"""Schema-25 output records convert from their durable evidence only."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import tempfile
import unittest

from blackboard.store.upgrade_fixture import load_fixture
from hey_my_buddy.blackboard.store.db import SCHEMA_VERSION
from hey_my_buddy.blackboard.store.upgrade import (
    BASE_SCHEMA_VERSION, restore_schema_25_physical_records, upgrade_schema_25_to_26,
)
from hey_my_buddy.errors import BoardError


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=True).encode()).hexdigest()


def _git(root, *args):
    env = {**os.environ, "GIT_AUTHOR_NAME": "fixture", "GIT_AUTHOR_EMAIL": "fixture@example.invalid",
           "GIT_COMMITTER_NAME": "fixture", "GIT_COMMITTER_EMAIL": "fixture@example.invalid"}
    return subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True, env=env).stdout.decode().strip()


class SchemaUpgradeTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="schema-25-upgrade-"))
        self.addCleanup(shutil.rmtree, self.root)

    def test_fixed_base_converts_without_rewriting_historical_attempt_evidence(self):
        fixture = load_fixture(self.root / "state")
        state = fixture["state"]
        with sqlite3.connect(state / "board.sqlite3") as connection:
            old_attempts = connection.execute("SELECT result_json FROM attempts ORDER BY attempt_id").fetchall()
        result = upgrade_schema_25_to_26(state)
        self.assertEqual((result["fromSchema"], result["schema"]), (BASE_SCHEMA_VERSION, SCHEMA_VERSION))
        self.assertGreater(result["convertedArtifacts"], 0)
        with sqlite3.connect(state / "board.sqlite3") as connection:
            self.assertEqual(connection.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0],
                             str(SCHEMA_VERSION))
            self.assertEqual(connection.execute("SELECT result_json FROM attempts ORDER BY attempt_id").fetchall(),
                             old_attempts)
            for raw, digest in connection.execute("SELECT manifest_json,manifest_sha256 FROM workflow_artifacts "
                                                  "WHERE kind IN ('output','resolved-output','partial-output','abandoned-site')"):
                record = json.loads(raw)
                self.assertNotIn("changedEntriesTruncated", record["snapshot"])
                self.assertEqual(record["snapshotSha256"], _digest(record["snapshot"]))
                self.assertEqual(digest, record["snapshotSha256"])
                path = Path(record["diffPath"]).with_name("output.json")
                if path.exists():
                    self.assertEqual(json.loads(path.read_text())["snapshotSha256"], digest)
        with self.assertRaises(BoardError) as caught:
            upgrade_schema_25_to_26(state)
        self.assertEqual(caught.exception.code, "UNSUPPORTED_SCHEMA")

    def test_complete_legacy_entries_convert_without_repository_or_physical_output(self):
        fixture = load_fixture(self.root / "state")
        state = fixture["state"]
        shutil.rmtree(fixture["fixtureRoot"] / "repository")
        shutil.rmtree(state / "workspaces")
        result = upgrade_schema_25_to_26(state)
        self.assertGreater(result["convertedArtifacts"], 0)

    def _truncated_board(self, *, object_format="sha1"):
        state = self.root / "state"
        state.mkdir()
        repository = self.root / "repository"
        repository.mkdir()
        _git(repository, "init", "--quiet", "--object-format=" + object_format)
        (repository / "a.txt").write_text("old")
        (repository / "c.txt").write_text("remove")
        _git(repository, "add", ".")
        _git(repository, "commit", "--quiet", "-m", "input")
        input_commit = _git(repository, "rev-parse", "HEAD")
        (repository / "a.txt").write_text("new")
        (repository / "b.txt").write_text("added")
        (repository / "c.txt").unlink()
        _git(repository, "add", "-A")
        _git(repository, "commit", "--quiet", "-m", "output")
        output_commit = _git(repository, "rev-parse", "HEAD")
        directory = state / "workspaces" / "ws-test" / "outputs" / "test"
        directory.mkdir(parents=True)
        snapshot = {"inputCommit": input_commit, "commit": output_commit,
                    "tree": _git(repository, "rev-parse", "HEAD^{tree}"),
                    "changedPaths": ["a.txt", "b.txt", "c.txt"],
                    "changedEntries": {"a.txt": ["100644", _git(repository, "rev-parse", "HEAD:a.txt")]},
                    "changedEntriesTruncated": True}
        manifest = {"workspaceId": "ws-test", "taskId": "task", "attemptId": "attempt",
                    "manifestSha256": "input-digest", "inputCommit": input_commit, "commit": output_commit,
                    "changedPaths": snapshot["changedPaths"], "snapshot": snapshot,
                    "snapshotSha256": _digest(snapshot), "diffPath": str(directory / "output.patch"),
                    "tree": _git(repository, "rev-parse", "HEAD^{tree}"), "ref": "ref", "diffSha256": "patch"}
        physical = json.loads(json.dumps(manifest))
        (directory / "output.json").write_text(json.dumps(physical))
        manifest["cumulativePatch"] = {"path": str(directory / "cumulative.patch"), "sha256": "retained"}
        with sqlite3.connect(state / "board.sqlite3") as connection:
            connection.execute("CREATE TABLE meta(key TEXT PRIMARY KEY,value TEXT)")
            connection.execute("INSERT INTO meta VALUES('schema_version','25')")
            connection.execute("CREATE TABLE workflow_artifacts(artifact_id TEXT,run_id TEXT,kind TEXT,"
                               "manifest_json TEXT,manifest_sha256 TEXT)")
            connection.execute("INSERT INTO workflow_artifacts VALUES(?,?,?,?,?)",
                               ("input", "run", "input", json.dumps({"manifestSha256": "input-digest",
                                "snapshot": {"repositoryPath": str(repository / ".git")}}), "input-digest"))
            connection.execute("INSERT INTO workflow_artifacts VALUES(?,?,?,?,?)",
                               ("output", "run", "output", json.dumps(manifest), manifest["snapshotSha256"]))
        return state, repository, directory, manifest, physical

    def test_truncated_entries_recompute_add_change_and_delete_then_rollback(self):
        state, _repository, directory, old, physical = self._truncated_board()
        upgrade_schema_25_to_26(state)
        with sqlite3.connect(state / "board.sqlite3") as connection:
            record = json.loads(connection.execute("SELECT manifest_json FROM workflow_artifacts "
                                                   "WHERE artifact_id='output'").fetchone()[0])
        entries = record["snapshot"]["changedEntries"]
        self.assertEqual(sorted(entries), ["a.txt", "b.txt", "c.txt"])
        self.assertIsNone(entries["c.txt"])
        self.assertNotIn("changedEntriesTruncated", record["snapshot"])
        self.assertEqual(record["cumulativePatch"], old["cumulativePatch"])
        self.assertEqual(json.loads((directory / "output.json").read_text())["snapshotSha256"],
                         record["snapshotSha256"])
        with sqlite3.connect(state / "board.sqlite3") as connection:
            connection.execute("UPDATE meta SET value='25' WHERE key='schema_version'")
            connection.execute("UPDATE workflow_artifacts SET manifest_json=?,manifest_sha256=? "
                               "WHERE artifact_id='output'", (json.dumps(old), old["snapshotSha256"]))
        self.assertEqual(restore_schema_25_physical_records(state), 1)
        self.assertEqual(json.loads((directory / "output.json").read_text()), physical)

    def test_linked_record_parent_refuses_without_overwriting_the_target(self):
        state, _repository, directory, _old, _physical = self._truncated_board()
        outside = self.root / "outside-records"
        directory.rename(outside)
        try:
            directory.symlink_to(outside, target_is_directory=True)
        except OSError as error:
            self.skipTest(f"Directory symlinks unavailable: {error}")
        before = (outside / "output.json").read_bytes()
        with self.assertRaises(BoardError) as caught:
            upgrade_schema_25_to_26(state)
        self.assertEqual(caught.exception.code, "UPGRADE_ARTIFACT_UNRECOVERABLE")
        self.assertEqual(caught.exception.details["reason"], "record-outside-state")
        self.assertEqual((outside / "output.json").read_bytes(), before)
        with sqlite3.connect(state / "board.sqlite3") as connection:
            self.assertEqual(connection.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0],
                             "25")

    def test_missing_git_object_refuses_with_artifact_identity_before_writes(self):
        state, repository, directory, old, physical = self._truncated_board()
        shutil.rmtree(repository / ".git" / "objects")
        with self.assertRaises(BoardError) as caught:
            upgrade_schema_25_to_26(state)
        self.assertEqual(caught.exception.code, "UPGRADE_ARTIFACT_UNRECOVERABLE")
        self.assertEqual(caught.exception.details["artifactId"], "output")
        self.assertEqual(caught.exception.details["artifactKind"], "output")
        self.assertEqual(caught.exception.details["reason"], "git-object-unavailable")
        self.assertEqual(json.loads((directory / "output.json").read_text()), physical)
        with sqlite3.connect(state / "board.sqlite3") as connection:
            self.assertEqual(connection.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0],
                             "25")

    def test_sha256_truncated_entries_use_full_commit_ids(self):
        probe = self.root / "probe"
        probe.mkdir()
        result = subprocess.run(["git", "-C", str(probe), "init", "--quiet", "--object-format=sha256"],
                                capture_output=True)
        if result.returncode or _git(probe, "rev-parse", "--show-object-format") != "sha256":
            self.skipTest("this Git does not support SHA-256 object format")
        state, _repository, _directory, _old, _physical = self._truncated_board(object_format="sha256")
        self.assertEqual(upgrade_schema_25_to_26(state)["schema"], SCHEMA_VERSION)
        with sqlite3.connect(state / "board.sqlite3") as connection:
            record = json.loads(connection.execute("SELECT manifest_json FROM workflow_artifacts "
                                                   "WHERE artifact_id='output'").fetchone()[0])
        self.assertEqual(len(record["commit"]), 64)
        self.assertEqual(len(record["snapshot"]["changedEntries"]["b.txt"][1]), 64)
