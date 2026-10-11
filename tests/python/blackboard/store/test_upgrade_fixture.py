"""L18 fixture boundary: fixed stock, legacy proof and real private host_seal.

This does not test schema upgrade or service workflow_conclude. No generator,
old-source import, model, daemon or mocked workspace validation is involved.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from blackboard.store.upgrade_fixture import FIXTURE, load_fixture
from hey_my_buddy.blackboard.store import workspace_identity_migration as migration
from hey_my_buddy.blackboard.tasks import workspace, workspace_identity as identity
from hey_my_buddy.errors import BoardError


class UpgradeFixtureTests(unittest.TestCase):
    def setUp(self):
        # Retain private evidence. The focused run supplies a checkout-local
        # TMPDIR; the check runner supplies its own private temporary root.
        self.root = Path(tempfile.mkdtemp(prefix="l18-portable-fixture-"))

    def restored(self, name="state"):
        fixture = load_fixture(self.root / name)
        connection = sqlite3.connect(fixture["state"] / "board.sqlite3")
        connection.row_factory = sqlite3.Row
        self.addCleanup(connection.close)
        return fixture, connection

    def sample(self, fixture, connection, label="delivered"):
        run_id = fixture["stableIds"][label]
        row = connection.execute("SELECT * FROM workflow_runs WHERE run_id=?", (run_id,)).fetchone()
        manifest = json.loads(row["workspace_manifest_json"])
        attempt = connection.execute("SELECT selected_attempt_id FROM tasks WHERE task_id=?", (run_id,)).fetchone()[0]
        return run_id, manifest, attempt

    def proof(self, state, manifest):
        # Use the existing conversion's actual inode, fixed-ref and source-input
        # evidence. This proves the aliases without running a schema migration.
        evidence = migration._proof(state, manifest, [manifest])
        mapping = {old: new for old, (new, anchors) in evidence.items()}
        for old, (new, anchors) in evidence.items():
            self.assertNotEqual(old, new)
            self.assertTrue(anchors)
            for anchor in anchors:
                path = Path(anchor["directory"])
                self.assertEqual(anchor["inode"], path.stat().st_ino)
                self.assertEqual(identity.legacy_device(path, old), anchor["legacyDevice"])
                self.assertEqual(identity.stable_identity(path), new)
        return mapping

    def test_l18_f01_restores_manifests_seals_and_frozen_hash_bindings(self):
        fixture, connection = self.restored()
        self.assertEqual(connection.execute("PRAGMA integrity_check").fetchone()[0], "ok")
        self.assertEqual(connection.execute("PRAGMA foreign_key_check").fetchall(), [])
        self.assertEqual(connection.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0], "15")
        schema = [list(row) for row in connection.execute(
            "SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name")]
        encoded = json.dumps(schema, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
        self.assertEqual(hashlib.sha256(encoded).hexdigest(), fixture["schemaSha256"])
        for label in fixture["stableIds"]:
            with self.subTest(label=label):
                run_id, manifest, attempt = self.sample(fixture, connection, label)
                workspace._validate_manifest(manifest)
                mapping = self.proof(fixture["state"], manifest)
                disk = fixture["state"] / "workspaces" / manifest["workspaceId"] / "manifest.json"
                self.assertEqual(json.loads(disk.read_text()), manifest)
                with identity.aliases(mapping):
                    workspace.verify(manifest)
                    for row in connection.execute("SELECT * FROM workflow_artifacts WHERE run_id=?", (run_id,)):
                        artifact = json.loads(row["manifest_json"])
                        if row["kind"] == "input":
                            self.assertEqual(artifact, manifest)
                            self.assertEqual(row["manifest_sha256"], manifest["manifestSha256"])
                        else:
                            self.assertEqual(row["manifest_sha256"], artifact["snapshotSha256"])
                            self.assertEqual(workspace.verify_sealed_output(manifest, manifest, artifact), {"changedPaths": []})
                            self.assertEqual(json.loads(Path(artifact["diffPath"]).with_name("output.json").read_text()), artifact)
                for turn in connection.execute("SELECT * FROM workflow_turns WHERE run_id=?", (run_id,)):
                    self.assertEqual(hashlib.sha256(turn["input_json"].encode()).hexdigest(), turn["input_sha256"])
                    self.assertEqual(json.loads(turn["input_json"])["executionWorkspace"], manifest)
                    result = json.loads(connection.execute("SELECT result_json FROM attempts WHERE attempt_id=?", (attempt,)).fetchone()[0])
                    self.assertEqual(result["result"]["turn"]["inputSha256"], turn["input_sha256"])
                task = connection.execute("SELECT * FROM tasks WHERE task_id=?", (run_id,)).fetchone()
                expected = hashlib.sha256(("2:" + task["spec_canonical_json"]).encode()).hexdigest()
                self.assertEqual(task["input_fingerprint"], expected)
                self.assertEqual(connection.execute("SELECT goal_fingerprint FROM workflow_runs WHERE run_id=?", (run_id,)).fetchone()[0], expected)
        # No alias or conclusion is manufactured by fixture preparation.
        self.assertEqual(connection.execute("SELECT COUNT(*) FROM meta WHERE key LIKE 'workspace-identity%' ").fetchone()[0], 0)
        self.assertEqual(connection.execute("SELECT COUNT(*) FROM workflow_host_conclusions").fetchone()[0], 0)
        mapping, additions, kept, registered = migration._plan(connection, fixture["state"])
        self.assertEqual(kept, {})
        self.assertEqual(registered, {})
        self.assertEqual(len(mapping), 5)
        for old, entry in additions.items():
            self.assertTrue(identity._valid_entry(entry, old))
        repository = fixture["fixtureRoot"] / "repository"
        workspace._git(repository, "fsck", "--strict", "--no-reflogs")
        self.assertEqual(workspace._git(repository, "for-each-ref", "refs/heads/", "refs/tags/"), b"")

    def test_l18_f02_delivered_sample_gets_real_independent_host_seal(self):
        fixture, connection = self.restored()
        run_id, manifest, attempt = self.sample(fixture, connection)
        self.assertEqual(connection.execute("SELECT state FROM workflow_runs WHERE run_id=?", (run_id,)).fetchone()[0], "delivered")
        worker = json.loads(connection.execute("SELECT manifest_json FROM workflow_artifacts WHERE run_id=? AND kind='output'", (run_id,)).fetchone()[0])
        worker_file = Path(worker["diffPath"]).with_name("output.json")
        worker_bytes = worker_file.read_bytes()
        host_file = worker_file.parent.with_name(worker_file.parent.name + "-host") / "output.json"
        self.assertFalse(host_file.exists())
        with identity.aliases(self.proof(fixture["state"], manifest)):
            sealed = workspace.host_seal(fixture["state"], manifest, run_id, attempt)
            self.assertEqual(workspace.verify_sealed_output(manifest, manifest, sealed), {"changedPaths": []})
            self.assertEqual(workspace.host_seal(fixture["state"], manifest, run_id, attempt), sealed)
        self.assertEqual(sealed["kind"], "host-output")
        self.assertTrue(sealed["ref"].endswith("-host"))
        self.assertEqual(json.loads(host_file.read_text()), sealed)
        self.assertEqual(Path(sealed["diffPath"]).read_bytes(), b"")
        self.assertEqual(worker_file.read_bytes(), worker_bytes)
        self.assertEqual(connection.execute("SELECT COUNT(*) FROM workflow_host_conclusions WHERE run_id=?", (run_id,)).fetchone()[0], 0)

    def test_l18_f03_private_roots_rebind_legacy_identity_without_changing_native_history(self):
        first, c1 = self.restored("first state")
        second, c2 = self.restored('第二个 "私有" <fixture-root> state')
        _, m1, _ = self.sample(first, c1)
        run_id, m2, attempt = self.sample(second, c2)
        self.assertNotEqual(m1["repositoryId"], m2["repositoryId"])
        self.assertNotEqual(m1["checkoutId"], m2["checkoutId"])
        self.assertNotEqual(m1["manifestSha256"], m2["manifestSha256"])
        with identity.aliases(self.proof(second["state"], m2)):
            worker = json.loads(c2.execute("SELECT manifest_json FROM workflow_artifacts WHERE run_id=? AND kind='output'", (run_id,)).fetchone()[0])
            workspace.verify_sealed_output(m2, m2, worker)
            sealed = workspace.host_seal(second["state"], m2, run_id, attempt)
            workspace.verify_sealed_output(m2, m2, sealed)
        for column in ("outcome_json", "provenance_json", "prompt_sha256"):
            rows1 = c1.execute(f"SELECT {column} FROM workflow_turns ORDER BY turn_id").fetchall()
            rows2 = c2.execute(f"SELECT {column} FROM workflow_turns ORDER BY turn_id").fetchall()
            self.assertEqual([tuple(row) for row in rows1], [tuple(row) for row in rows2])

    def test_l18_f04_raw_template_and_unproved_or_drifted_identity_are_refused(self):
        raw = sqlite3.connect(":memory:")
        self.addCleanup(raw.close)
        raw.executescript((FIXTURE / "board.sql").read_text())
        manifest = json.loads(raw.execute("SELECT workspace_manifest_json FROM workflow_runs WHERE run_id=?",
            ("00000000-0000-0000-0000-000000000007",)).fetchone()[0])
        with self.assertRaises(BoardError) as invalid:
            workspace.host_seal(self.root, manifest, "unused", "unused")
        self.assertEqual(invalid.exception.code, "WORKSPACE_MANIFEST_CHANGED")
        fixture, connection = self.restored()
        run_id, manifest, attempt = self.sample(fixture, connection)
        # Without the real old-to-current proof, current sealing rejects the old id.
        with self.assertRaises(BoardError) as unproved:
            workspace.host_seal(fixture["state"], manifest, run_id, attempt)
        self.assertEqual(unproved.exception.code, "WORKSPACE_CHANGED")
        with identity.aliases(self.proof(fixture["state"], manifest)):
            damaged = dict(manifest, path=str(self.root / "wrong-path"))
            with self.assertRaises(BoardError) as drifted:
                workspace.host_seal(fixture["state"], damaged, run_id, attempt)
        self.assertEqual(drifted.exception.code, "WORKSPACE_MANIFEST_CHANGED")
        with self.assertRaises(ValueError):
            load_fixture(fixture["state"])

    def test_l18_f05_drifted_patch_and_fixed_git_ref_cannot_prove_a_seal(self):
        fixture, connection = self.restored()
        run_id, manifest, _ = self.sample(fixture, connection)
        mapping = self.proof(fixture["state"], manifest)
        worker = json.loads(connection.execute("SELECT manifest_json FROM workflow_artifacts WHERE run_id=? AND kind='output'", (run_id,)).fetchone()[0])
        Path(worker["diffPath"]).write_bytes(b"fixture corruption probe\n")
        with identity.aliases(mapping):
            with self.assertRaises(BoardError) as patch_drift:
                workspace.verify_sealed_output(manifest, manifest, worker)
        self.assertEqual(patch_drift.exception.code, "WORKSPACE_MANIFEST_CHANGED")
        workspace._git(fixture["fixtureRoot"] / "repository", "update-ref",
                       manifest["snapshot"]["inputRef"], manifest["inputTree"])
        with self.assertRaises(BoardError) as ref_drift:
            migration._fixed_refs(manifest)
        self.assertEqual(ref_drift.exception.code, "IDENTITY_UNPROVEN")
        with self.assertRaises(BoardError):
            migration._proof(fixture["state"], manifest, [manifest])

    def test_r8_base_native_operation_provenance_and_raw_uncertainty_are_restored(self):
        fixture, connection = self.restored()
        provenance = fixture['accountOperation']
        self.assertEqual(provenance['source'], 'buddy.accounts.native_operation')
        self.assertIs(provenance['shutdownConfirmed'], False)
        self.assertEqual(set(fixture['stableIds']), {'delivered', 'accepted', 'rejected', 'cancelled'})
        self.assertNotIn(provenance['operationId'], fixture['stableIds'].values())
        raw = sqlite3.connect(':memory:')
        self.addCleanup(raw.close)
        raw.executescript((FIXTURE / 'board.sql').read_text())
        query = "SELECT key,value FROM meta WHERE key LIKE 'account-operation:%'"
        original = raw.execute(query).fetchall()
        restored = [tuple(row) for row in connection.execute(query)]
        self.assertEqual(restored, original)
        self.assertEqual(len(restored), 1)
        account = provenance['account']
        self.assertEqual(json.loads(restored[0][1]), {
            'adapter': account['adapter'], 'source': account['source'],
            'credentialRevision': account['credentialRevision'],
            'purpose': provenance['purpose'], 'status': 'uncertain'})
        recorded = fixture['operations'][-1]
        self.assertEqual(recorded['operation'], 'accounts.native_operation')
        self.assertEqual(recorded['response'], {
            'operationId': provenance['operationId'], 'shutdownConfirmed': False})


if __name__ == "__main__":
    unittest.main()
