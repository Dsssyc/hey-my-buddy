"""Portable schema-18 stock from the released base's real named operations."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from blackboard.store.upgrade_fixture import FIXTURE, load_fixture
from hey_my_buddy.blackboard.store import migrations
from hey_my_buddy.blackboard.store.db import Database
from hey_my_buddy.blackboard.tasks import workspace
from hey_my_buddy.blackboard.tasks.workflow import WorkflowCoordinator
from hey_my_buddy.install.upgrade import idle_snapshot, migrate_board


class UpgradeFixtureTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix='upgrade-base-fixture-'))

    def restored(self, name='state'):
        fixture = load_fixture(self.root / name)
        connection = sqlite3.connect(fixture['state'] / 'board.sqlite3')
        connection.row_factory = sqlite3.Row
        self.addCleanup(connection.close)
        return fixture, connection

    def sample(self, fixture, connection):
        run_id = fixture['stableIds']['delivered']
        row = connection.execute('SELECT * FROM workflow_runs WHERE run_id=?', (run_id,)).fetchone()
        manifest = json.loads(row['workspace_manifest_json'])
        attempt = connection.execute('SELECT selected_attempt_id FROM tasks WHERE task_id=?', (run_id,)).fetchone()[0]
        return run_id, manifest, attempt

    def test_public_operation_stock_retains_source_schema_and_history_hashes(self):
        fixture, connection = self.restored()
        self.assertEqual(fixture['sourceCommit'], '4b7f494670efe7a3fe053062f16cce9312a5e042')
        self.assertEqual(fixture['sourceVersion'], '0.34.1')
        self.assertEqual(fixture['schemaVersion'], 18)
        self.assertEqual(connection.execute('PRAGMA integrity_check').fetchone()[0], 'ok')
        self.assertEqual(connection.execute('PRAGMA foreign_key_check').fetchall(), [])
        self.assertEqual(connection.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0], '18')
        schema = [list(row) for row in connection.execute('SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name')]
        encoded = json.dumps(schema, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()
        self.assertEqual(hashlib.sha256(encoded).hexdigest(), fixture['schemaSha256'])
        for row in connection.execute('SELECT input_json,input_sha256 FROM workflow_turns WHERE input_sha256 IS NOT NULL'):
            self.assertEqual(hashlib.sha256(row[0].encode()).hexdigest(), row[1])
        for label, run_id in fixture['stableIds'].items():
            row = connection.execute('SELECT workspace_manifest_json FROM workflow_runs WHERE run_id=?', (run_id,)).fetchone()
            manifest = json.loads(row[0])
            workspace.verify(manifest)
            for artifact in connection.execute('SELECT * FROM workflow_artifacts WHERE run_id=?', (run_id,)):
                if artifact['kind'] == 'output':
                    workspace.verify_sealed_output(manifest, manifest, json.loads(artifact['manifest_json']))
                elif artifact['kind'] == 'shared-refs':
                    WorkflowCoordinator.verify_shared_refs_record(json.loads(artifact['manifest_json']))
        self.assertEqual(connection.execute('SELECT COUNT(*) FROM workflow_host_conclusions').fetchone()[0], 2)
        self.assertEqual({row[0] for row in connection.execute('SELECT enabled FROM evaluation_profiles')}, {0, 1})
        self.assertEqual({row[0] for row in connection.execute(
            'SELECT mode FROM evaluation_preferences UNION SELECT mode FROM family_preferences')},
            {'prefer', 'pin', 'exclude'})
        self.assertEqual(dict(connection.execute('SELECT run_id,state FROM workflow_runs')),
                         {fixture['stableIds'][key]: key for key in ('delivered', 'accepted', 'cancelled', 'failed')})
        self.assertEqual(connection.execute("SELECT COUNT(*) FROM tasks WHERE state IN ('queued','running')").fetchone()[0], 0)
        self.assertEqual({row[0] for row in connection.execute(
            "SELECT json_extract(execution_workspace_json,'$.kind') FROM workflow_runs")}, {'existing'})
        self.assertEqual(dict(connection.execute(
            "SELECT key,value FROM meta WHERE key IN ('router_retry_interval_seconds','router_default_mode','router_budget_preset')")),
            {'router_retry_interval_seconds': '47', 'router_default_mode': 'review', 'router_budget_preset': 'deep'})
        for table in ('evaluation_profiles', 'evaluation_preferences', 'family_preferences',
                      'family_annotations', 'model_concurrency', 'harness_health'):
            self.assertGreater(connection.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0], 0, table)

    def test_delivered_stock_can_get_a_real_independent_host_seal(self):
        fixture, connection = self.restored()
        run_id, manifest, attempt = self.sample(fixture, connection)
        output = json.loads(connection.execute("SELECT manifest_json FROM workflow_artifacts WHERE run_id=? AND kind='output'", (run_id,)).fetchone()[0])
        worker_file = Path(output['diffPath']).with_name('output.json')
        before = worker_file.read_bytes()
        seal = workspace.host_seal(fixture['state'], manifest, run_id, attempt)
        self.assertEqual(seal['kind'], 'host-output')
        self.assertEqual(workspace.verify_sealed_output(manifest, manifest, seal), {'changedPaths': []})
        self.assertEqual(workspace.host_seal(fixture['state'], manifest, run_id, attempt), seal)
        self.assertEqual(worker_file.read_bytes(), before)

    def test_portable_roots_rebind_stable_identities_without_changing_native_history(self):
        first, c1 = self.restored('first state')
        second, c2 = self.restored('第二个 "私有" <fixture-root> state')
        _, m1, _ = self.sample(first, c1)
        _, m2, _ = self.sample(second, c2)
        self.assertNotEqual(m1['repositoryId'], m2['repositoryId'])
        self.assertNotEqual(m1['checkoutId'], m2['checkoutId'])
        self.assertNotEqual(m1['manifestSha256'], m2['manifestSha256'])
        workspace.verify(m1)
        workspace.verify(m2)
        for column in ('outcome_json', 'provenance_json', 'prompt_sha256'):
            query = f'SELECT {column} FROM workflow_turns ORDER BY turn_id'
            self.assertEqual([tuple(row) for row in c1.execute(query)], [tuple(row) for row in c2.execute(query)])
        with self.assertRaises(ValueError):
            load_fixture(first['state'])

    def test_upgrade_matches_a_fresh_board_with_only_the_retained_settings(self):
        fixture, old = self.restored()
        settings = migrations.read_settings(old)
        original_secret = old.execute("SELECT value FROM meta WHERE key='capability_secret'").fetchone()[0]
        old.close()
        before = idle_snapshot(fixture['state'])
        summary, expected = migrate_board(fixture['state'], before)
        self.assertEqual((summary['fromSchema'], summary['toSchema']), (18, 19))
        self.assertEqual(expected['schema'], 19)

        fresh = Database(self.root / 'fresh')
        fresh.initialize()
        with fresh.write() as connection:
            migrations.write_settings(connection, settings)

        with sqlite3.connect(fixture['state'] / 'board.sqlite3') as upgraded, fresh.read() as baseline:
            upgraded.row_factory = sqlite3.Row
            self.assertNotEqual(upgraded.execute("SELECT value FROM meta WHERE key='capability_secret'").fetchone()[0],
                                original_secret)
            self.assertEqual(migrations.read_settings(upgraded), migrations.read_settings(baseline))
            names = {row[0] for row in upgraded.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            self.assertEqual(names, {row[0] for row in baseline.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")})
            for name in names:
                if name == 'meta':
                    query = "SELECT key,value FROM meta WHERE key NOT IN ('capability_secret','created_at')"
                elif name == 'evaluation_state':
                    query = 'SELECT id,table_revision,configuration_revision,decision_profile_id,writer_sequence FROM evaluation_state'
                else:
                    query = f'SELECT * FROM "{name}"'
                self.assertEqual(sorted([tuple(row) for row in upgraded.execute(query)], key=repr),
                                 sorted([tuple(row) for row in baseline.execute(query)], key=repr), name)
            self.assertEqual(upgraded.execute('PRAGMA integrity_check').fetchone()[0], 'ok')
            self.assertEqual(upgraded.execute('PRAGMA foreign_key_check').fetchall(), [])
