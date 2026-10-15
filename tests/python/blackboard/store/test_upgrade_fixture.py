"""Portable schema-20 stock from the installed base's real named operations."""
from __future__ import annotations

from contextlib import ExitStack
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest import mock

from blackboard.store.upgrade_fixture import load_fixture, _git, _sha, _json
from hey_my_buddy.blackboard.store import backup
from hey_my_buddy.blackboard.routing import router
from hey_my_buddy.blackboard.store.db import Database, SCHEMA_VERSION
from hey_my_buddy.blackboard.tasks import workspace
from hey_my_buddy.blackboard.tasks.workflow import WorkflowCoordinator
from hey_my_buddy.install import upgrade


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
        self.assertEqual(fixture['sourceCommit'], '6b385e53bf24e462b7c1534efcd77698b2c4729a')
        self.assertEqual(fixture['sourceVersion'], '0.36.0')
        self.assertEqual(fixture['schemaVersion'], 20)
        self.assertEqual(connection.execute('PRAGMA integrity_check').fetchone()[0], 'ok')
        self.assertEqual(connection.execute('PRAGMA foreign_key_check').fetchall(), [])
        self.assertEqual(connection.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0], '20')
        self.assertEqual({row[0] for row in connection.execute('SELECT key FROM meta')},
                         {'schema_version', 'created_at', 'capability_secret'})
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
                    output = json.loads(artifact['manifest_json'])
                    self.assertTrue(output['ref'].startswith('refs/hey-my-buddy/workspaces/'))
                    self.assertEqual(_git(Path(manifest['snapshot']['repositoryPath']), 'rev-parse', output['ref']), output['commit'])
                    self.assertEqual(_git(Path(manifest['snapshot']['repositoryPath']), 'rev-parse', output['commit'] + '^{tree}'), output['tree'])
                    self.assertEqual(_sha(Path(output['diffPath']).read_bytes()), output['diffSha256'])
                    self.assertEqual(_sha(_json(output['snapshot']).encode()), output['snapshotSha256'])
                elif artifact['kind'] == 'shared-refs':
                    WorkflowCoordinator.verify_shared_refs_record(json.loads(artifact['manifest_json']))
        self.assertEqual(connection.execute('SELECT COUNT(*) FROM workflow_host_conclusions').fetchone()[0], 2)
        self.assertEqual({row[0] for row in connection.execute('SELECT enabled FROM buddies')}, {0, 1})
        self.assertEqual({row[0] for row in connection.execute(
            'SELECT mode FROM evaluation_preferences')}, {'pin', 'exclude'})
        self.assertEqual(dict(connection.execute('SELECT run_id,state FROM workflow_runs')),
                         {fixture['stableIds'][key]: value for key, value in
                          (('delivered', 'concluded'), ('accepted', 'accepted'),
                           ('failed', 'concluded'), ('cancelled', 'cancelled'))})
        self.assertEqual(connection.execute("SELECT COUNT(*) FROM tasks WHERE state IN ('queued','running')").fetchone()[0], 0)
        self.assertEqual({row[0] for row in connection.execute(
            "SELECT json_extract(execution_workspace_json,'$.kind') FROM workflow_runs")}, {'existing'})
        self.assertEqual(router.configuration(connection)['routerBuddyIds'], fixture['routerBuddyIds'])
        self.assertEqual(tuple(connection.execute(
            'SELECT default_mode,budget_preset,retry_interval_seconds FROM router_settings').fetchone()),
            ('review', 'deep', 47))
        for table in ('buddies', 'evaluation_preferences', 'model_service_notes',
                      'model_service_concurrency', 'harness_health'):
            self.assertGreater(connection.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0], 0, table)

    def test_host_seal_replays_without_overwriting_worker_stock(self):
        fixture, connection = self.restored()
        run_id, manifest, attempt = self.sample(fixture, connection)
        output = json.loads(connection.execute("SELECT manifest_json FROM workflow_artifacts WHERE run_id=? AND kind='output'", (run_id,)).fetchone()[0])
        worker_file = Path(output['diffPath']).with_name('output.json')
        before = worker_file.read_bytes()
        sealed = workspace.host_seal(fixture['state'], manifest, run_id, attempt)
        self.assertEqual(sealed['kind'], 'host-output')
        self.assertNotEqual(Path(sealed['diffPath']).parent, worker_file.parent)
        self.assertEqual(workspace.host_seal(fixture['state'], manifest, run_id, attempt), sealed)
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

    def test_schema_upgrade_preserves_stock_and_matches_fresh_structure(self):
        fixture, old = self.restored()
        state = fixture['state']
        before = upgrade.idle_snapshot(state)
        board_bytes = (state / 'board.sqlite3').read_bytes()
        expected_settings = router.configuration(old)
        expected_runs = dict(old.execute('SELECT run_id,state FROM workflow_runs'))
        request_fingerprints = dict(old.execute('SELECT run_id,request_fingerprint FROM workflow_runs'))
        expected_preferences = [tuple(row) for row in old.execute(
            'SELECT buddy_id,mode,reason FROM evaluation_preferences ORDER BY buddy_id')]
        old.close()
        history = {path.relative_to(state): path.read_bytes()
                   for path in (state / 'workspaces').rglob('*') if path.is_file()}
        self.assertTrue(history)
        runtime_root = self.root / 'runtimes'
        previous = runtime_root / ('a' * 32)
        target = runtime_root / ('b' * 32)
        previous.mkdir(parents=True)
        target.mkdir()
        endpoint = {'runtimeIdentity': 'runtime:' + previous.name, 'serviceId': 'fixture-old',
                    'pid': 123, 'contractVersion': 'fixture-contract'}
        (state / 'control.json').write_text(json.dumps(endpoint))
        health = {**endpoint, 'runtimeContentId': previous.name, 'runtimeStable': True,
                  'maxConcurrent': 2, 'waitCapacity': 4, 'schemaVersion': fixture['schemaVersion']}
        with ExitStack() as stack:
            stack.enter_context(mock.patch.dict(os.environ, {'HMB_RUNTIME_ROOT': str(runtime_root)}))
            for name, options in (
                ('get_state_dir', {'return_value': state}),
                ('runtime.is_ready', {'return_value': True}),
                ('runtime.read_ready', {'return_value': {'sourceCommit': fixture['sourceCommit']}}),
                ('runtime.materialize', {'return_value': {'runtimeDir': str(target)}}),
                ('probe', {'return_value': health}),
                ('detach', {'return_value': None}),
                ('start', {'return_value': {**health, 'runtimeContentId': target.name, 'schemaVersion': SCHEMA_VERSION}}),
                ('command', {'return_value': {'leaks': []}}),
                ('write_active_runtime', {'return_value': None}),
            ):
                stack.enter_context(mock.patch('hey_my_buddy.install.upgrade.' + name, **options))
            stack.enter_context(mock.patch('hey_my_buddy.blackboard.tasks.storage.prune_old_runtimes',
                                           return_value={'complete': True}))
            result = upgrade.upgrade({})
        self.assertTrue(result['upgraded'], result)
        self.assertNotIn('archive', result)
        after = upgrade.idle_snapshot(state, event_head=before['eventHead'])
        self.assertEqual(after['schema'], SCHEMA_VERSION)
        self.assertEqual(after['fingerprints'], before['fingerprints'])
        self.assertEqual((state / 'board.sqlite3').read_bytes(), board_bytes)
        self.assertEqual({relative: (state / relative).read_bytes() for relative in history}, history)
        self.assertEqual(backup.verify(Path(result['backup']['path']))['schema'], fixture['schemaVersion'])

        fresh = Database(self.root / 'fresh')
        fresh.initialize()
        with sqlite3.connect(state / 'board.sqlite3') as upgraded, fresh.read() as baseline:
            upgraded.row_factory = sqlite3.Row
            source_schema = [tuple(row) for row in upgraded.execute(
                'SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name')]
            fresh_schema = [tuple(row) for row in baseline.execute(
                'SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name')]
            self.assertEqual(source_schema, fresh_schema)
            self.assertEqual(upgraded.execute('SELECT COUNT(*) FROM workflow_lead_tasks').fetchone()[0], 0)
            self.assertEqual({row[0] for row in upgraded.execute(
                'SELECT delegation_authority FROM workflow_runs')}, {0})
            attempt_columns = {row[1] for row in upgraded.execute('PRAGMA table_info(attempts)')}
            self.assertTrue({'stop_initiator', 'grace_deadline'} <= attempt_columns)
            self.assertEqual(router.configuration(upgraded), expected_settings)
            self.assertEqual(dict(upgraded.execute('SELECT run_id,state FROM workflow_runs')), expected_runs)
            self.assertEqual(dict(upgraded.execute('SELECT run_id,request_fingerprint FROM workflow_runs')),
                             request_fingerprints)
            self.assertEqual([tuple(row) for row in upgraded.execute(
                'SELECT buddy_id,mode,reason FROM evaluation_preferences ORDER BY buddy_id')],
                expected_preferences)
            self.assertEqual(upgraded.execute('PRAGMA integrity_check').fetchone()[0], 'ok')
            self.assertEqual(upgraded.execute('PRAGMA foreign_key_check').fetchall(), [])
