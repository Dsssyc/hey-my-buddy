"""Portable schema-18 stock from the released base's real named operations."""
from __future__ import annotations

from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from blackboard.store.upgrade_fixture import load_fixture
from blackboard.catalog.test_catalog_trust import reading
from hey_my_buddy.blackboard.catalog.catalog import CONFIRMED_ABSENCE_REASON
from hey_my_buddy.blackboard.evaluation.evaluation import EvaluationStore
from hey_my_buddy.blackboard.routing import router
from hey_my_buddy.blackboard.store import migrations
from hey_my_buddy.blackboard.store.db import Database
from hey_my_buddy.blackboard.store.store import BoardStore
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
        for _, run_id in fixture['stableIds'].items():
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

    def test_upgraded_buddies_absent_from_first_confirmed_catalog_have_catalog_reason(self):
        fixture, old = self.restored()
        # The portable base keeps configured identities but does not carry the
        # daily board's stale account diagnosis. Reproduce it in this private
        # restored stock; the committed fixture remains unchanged.
        old.execute("UPDATE evaluation_profiles SET available=0,unavailable_reason='ACCOUNT_STATUS_UNKNOWN'")
        old.commit()
        absent = [tuple(row) for row in old.execute('SELECT profile_id,adapter FROM evaluation_profiles')]
        self.assertTrue(absent)
        before = idle_snapshot(fixture['state'])
        old.close()
        migrate_board(fixture['state'], before)
        board = BoardStore(fixture['state'])
        board.initialize()
        evaluation = EvaluationStore(board)
        with board.db.read() as connection:
            for profile_id, _adapter in absent:
                self.assertIsNone(connection.execute(
                    'SELECT unavailable_reason FROM evaluation_profiles WHERE profile_id=?',
                    (profile_id,)).fetchone()[0])
        for adapter in sorted({adapter for _profile_id, adapter in absent}):
            evaluation.record_catalog(reading(adapter=adapter, models=()))
        with board.db.read() as connection:
            for profile_id, _adapter in absent:
                self.assertEqual(tuple(connection.execute(
                    'SELECT available,unavailable_reason FROM evaluation_profiles WHERE profile_id=?',
                    (profile_id,)).fetchone()), (0, CONFIRMED_ABSENCE_REASON))
            self.assertEqual({row[0] for row in connection.execute('SELECT key FROM meta')},
                             {'schema_version', 'created_at', 'capability_secret'})

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
            self.assertEqual(router.configuration(upgraded), settings["router"])
            self.assertEqual(upgraded.execute("SELECT COUNT(*) FROM harness_health WHERE scan_after IS NOT NULL").fetchone()[0], 0)
            self.assertEqual(router.configuration(upgraded), router.configuration(baseline))
            names = {row[0] for row in upgraded.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            self.assertEqual(names, {row[0] for row in baseline.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")})
            for name in names:
                if name == 'meta':
                    query = "SELECT key,value FROM meta WHERE key NOT IN ('capability_secret','created_at')"
                elif name == 'evaluation_state':
                    query = 'SELECT id,decision_profile_id FROM evaluation_state'
                else:
                    query = f'SELECT * FROM "{name}"'
                self.assertEqual(sorted([tuple(row) for row in upgraded.execute(query)], key=repr),
                                 sorted([tuple(row) for row in baseline.execute(query)], key=repr), name)
            self.assertEqual(upgraded.execute('PRAGMA integrity_check').fetchone()[0], 'ok')
            self.assertEqual(upgraded.execute('PRAGMA foreign_key_check').fetchall(), [])

    def test_upgrade_preserves_each_buddys_composed_preference(self):
        fixture, old = self.restored()
        # Mutate only this restored schema-18 board. Its view is the independent
        # baseline for the new board's per-buddy policy, including disabled and
        # unavailable members and the old explicit absence override.
        family = tuple(old.execute(
            'SELECT adapter,provider,model FROM evaluation_profiles ORDER BY profile_id LIMIT 1'
        ).fetchone())
        for effort in ('low', 'max'):
            profile_id = ':'.join((*family, effort))
            old.execute(
                'INSERT INTO evaluation_profiles(profile_id,label,adapter,provider,model,effort,'
                'available,enabled,capabilities_json,created_revision,updated_revision)'
                ' VALUES(?,?,?,?,?,?,1,1,?,1,1)',
                (profile_id, f'fixture · {effort}', *family, effort, json.dumps(['execution:codex'])))
        profile_ids = [row[0] for row in old.execute(
            'SELECT profile_id FROM evaluation_profiles WHERE adapter=? AND provider=? AND model=? ORDER BY profile_id',
            family)]
        old.execute('DELETE FROM evaluation_preferences')
        old.execute('DELETE FROM family_preferences')
        old.execute('INSERT INTO family_preferences(adapter,provider,model,mode,reason,updated_revision) VALUES(?,?,?,?,?,1)',
                    (*family, 'prefer', 'model service reason'))
        old.execute('INSERT INTO evaluation_preferences(profile_id,mode,reason,updated_revision) VALUES(?,?,?,1)',
                    (profile_ids[0], 'exclude', 'buddy reason'))
        old.execute('INSERT INTO evaluation_preferences(profile_id,mode,reason,updated_revision) VALUES(?,?,?,1)',
                    (profile_ids[1], 'none', 'clear inherited preference'))
        old.execute('UPDATE evaluation_profiles SET available=0,enabled=0 WHERE profile_id=?', (profile_ids[2],))
        old.commit()
        expected = {row['profile_id']: (row['mode'], row['reason']) for row in old.execute(
            'SELECT profile_id,mode,reason FROM effective_preferences')}
        all_ids = [row[0] for row in old.execute('SELECT profile_id FROM evaluation_profiles ORDER BY profile_id')]
        self.assertEqual(expected[profile_ids[0]], ('exclude', 'buddy reason'))
        self.assertNotIn(profile_ids[1], expected)
        self.assertEqual(expected[profile_ids[2]], ('prefer', 'model service reason'))
        self.assertTrue(set(all_ids) - set(expected))
        old.close()
        before = idle_snapshot(fixture['state'])
        summary, _ = migrate_board(fixture['state'], before)
        self.assertNotIn('family_preferences', summary['settingsRows'])
        with closing(sqlite3.connect(fixture['state'] / 'board.sqlite3')) as upgraded:
            self.assertEqual([row[0] for row in upgraded.execute(
                'SELECT profile_id FROM evaluation_profiles ORDER BY profile_id')], all_ids)
            actual = {row[0]: (row[1], row[2]) for row in upgraded.execute(
                'SELECT profile_id,mode,reason FROM evaluation_preferences')}
            for profile_id in all_ids:
                with self.subTest(profile_id=profile_id):
                    self.assertEqual(actual.get(profile_id), expected.get(profile_id))
            self.assertEqual(actual, expected)
            self.assertEqual(upgraded.execute(
                "SELECT name FROM sqlite_master WHERE name IN ('family_preferences','effective_preferences')"
            ).fetchall(), [])
