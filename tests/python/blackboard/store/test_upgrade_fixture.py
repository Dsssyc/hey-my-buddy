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
from hey_my_buddy.blackboard.store import upgrade as board_upgrade
from hey_my_buddy.blackboard.store.store import BoardStore
from hey_my_buddy.blackboard.routing import router
from hey_my_buddy.blackboard.store.db import Database, SCHEMA_VERSION
from hey_my_buddy.blackboard.tasks import workspace
from hey_my_buddy.blackboard.tasks.workflow import WorkflowCoordinator
from hey_my_buddy.errors import BoardError
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
        converted = {'events', 'workflow_runs', 'workflow_scope_versions',
                     'workflow_workspace_conflicts', 'workflow_integrations',
                     'workspace_cleanup_plans', 'workflow_host_conclusions', 'attempt_legacy_starts',
                     'attempts', 'commands', 'workflow_requests', 'meta', 'tasks', 'workflow_children'}
        self.assertEqual(after['eventHead'], before['eventHead'])
        self.assertEqual({name: value for name, value in after['fingerprints'].items() if name not in converted},
                         {name: value for name, value in before['fingerprints'].items() if name not in converted})
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
            self.assertNotIn('attempt_legacy_starts', after['tables'])
            self.assertEqual(upgraded.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0],
                             str(SCHEMA_VERSION))
            self.assertEqual(upgraded.execute('SELECT COUNT(*) FROM workflow_lead_tasks').fetchone()[0], 0)
            self.assertEqual({row[0] for row in upgraded.execute(
                'SELECT delegation_authority FROM workflow_runs')}, {0})
            attempt_columns = {row[1] for row in upgraded.execute('PRAGMA table_info(attempts)')}
            self.assertTrue({'stop_initiator', 'grace_deadline'} <= attempt_columns)
            self.assertEqual(router.configuration(upgraded), expected_settings)
            self.assertEqual(dict(upgraded.execute('SELECT run_id,state FROM workflow_runs')), expected_runs)
            self.assertEqual(set(dict(upgraded.execute('SELECT run_id,request_fingerprint FROM workflow_runs'))),
                             set(request_fingerprints))
            self.assertTrue(all(row['request_fingerprint'] is None
                                for row in upgraded.execute('SELECT request_fingerprint FROM workflow_runs')))
            self.assertTrue(all(row['owner'] is None for row in upgraded.execute(
                'SELECT t.owner FROM tasks t JOIN workflow_runs r ON r.run_id=t.task_id')))
            self.assertEqual([tuple(row) for row in upgraded.execute(
                'SELECT buddy_id,mode,reason FROM evaluation_preferences ORDER BY buddy_id')],
                expected_preferences)
            self.assertEqual(upgraded.execute('PRAGMA integrity_check').fetchone()[0], 'ok')
            self.assertEqual(upgraded.execute('PRAGMA foreign_key_check').fetchall(), [])

    def test_lead_relationship_converts_authority_and_preserves_opaque_actor_text(self):
        fixture, connection = self.restored()
        owned = fixture['stableIds']['delivered']
        lead = fixture['stableIds']['accepted']
        helper = fixture['stableIds']['failed']
        unrelated = fixture['stableIds']['cancelled']
        lead_turn = connection.execute('SELECT turn_id FROM workflow_turns WHERE run_id=? LIMIT 1', (lead,)).fetchone()[0]
        connection.execute('INSERT INTO workflow_lead_tasks VALUES(?,?,?,?)',
                           (owned, lead, lead_turn, '2026-01-01T00:00:00Z'))
        connection.execute('INSERT INTO workflow_lead_tasks VALUES(?,?,?,?)',
                           (unrelated, lead, lead_turn, '2026-01-01T00:00:00Z'))
        connection.execute(
            "INSERT INTO workflow_children(child_task_id,parent_run_id,request_id,decision_command_id,"
            "state,workspace_intent_json,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",
            (helper, owned, 'owned-helper', 'owned-helper-decision', 'succeeded', '{}',
             '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z'))
        connection.execute('UPDATE workflow_runs SET host_id=? WHERE run_id IN (?,?)', ('lead:' + lead, owned, helper))
        connection.execute('UPDATE workflow_runs SET host_id=?,owner_generation=2 WHERE run_id=?',
                           ('lead:' + lead, unrelated))
        connection.execute('UPDATE workflow_runs SET host_id=? WHERE run_id=?', ('lead:literal-host', lead))
        connection.execute('UPDATE tasks SET owner=? WHERE task_id=?', ('host:lead:' + lead, owned))
        connection.execute('UPDATE workflow_scope_versions SET actor=? WHERE run_id IN (?,?) AND scope_version=1',
                           ('host:lead:' + lead, owned, unrelated))
        connection.execute("INSERT INTO workflow_requests(request_id,run_id,kind,summary,payload_json,state,decision_json,"
                           "expected_revision,created_at,updated_at,decided_at) VALUES(?,?,'attention','fixture','{}',"
                           "'approved',?,1,?,?,?)",
                           ('fixture-resolution', owned,
                            json.dumps({'resolution': {'actor': 'host:lead:' + lead}}),
                            '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z'))
        connection.execute('INSERT INTO events(task_id,kind,payload_json,created_at) VALUES(?,?,?,?)',
                           (unrelated, 'workflow.cancelled',
                            json.dumps({'actor': 'host:lead:' + lead, 'reason': 'before takeover'}),
                            '2026-01-01T00:00:00Z'))
        connection.execute('INSERT INTO events(task_id,kind,payload_json,created_at) VALUES(?,?,?,?)',
                           (unrelated, 'workflow.takeover', json.dumps({'actor': 'host:lead:' + lead,
                            'newHostId': 'lead:' + lead,
                            'previousHostId': 'lead:' + lead, 'previousOwnerGeneration': 1, 'ownerGeneration': 2}),
                            '2026-01-01T00:00:00Z'))
        for run_id, actor in ((owned, 'host:lead:' + lead), (helper, 'lead:' + lead),
                              (unrelated, 'host:lead:' + lead), (lead, 'host:lead:literal-host')):
            connection.execute(
                'INSERT INTO events(task_id,kind,payload_json,created_at) VALUES(?,?,?,?)',
                (run_id, 'workflow.fixture_actor', json.dumps({'actor': actor,
                    'opaque': {'actor': 'host:stay-opaque'},
                    'interruptionEvidence': {'kind': 'boot-changed', 'legacyStartedAt': None}}),
                 '2026-01-01T00:00:00Z'))
        for kind, actor in (('task.cancel_requested', 'console:literal-host'),
                            ('message.updated', 'host:literal-bridge')):
            connection.execute('INSERT INTO events(task_id,kind,payload_json,created_at) VALUES(?,?,?,?)',
                               (owned, kind, json.dumps({'actor': actor}), '2026-01-01T00:00:00Z'))
        connection.execute('INSERT INTO events(task_id,kind,payload_json,created_at) VALUES(?,?,?,?)',
                           (owned, 'workflow.cancelled',
                            json.dumps({'actor': 'host:lead:' + lead, 'reason': 'fixture cancellation'}),
                            '2026-01-01T00:00:00Z'))
        receipt = {'runId': owned, 'ownerGeneration': 1, 'hostId': 'lead:' + lead,
                   'requestFingerprint': 'old-digest',
                   'control': {'hostId': 'lead:' + lead},
                   'executionWorkspace': {'integrator': 'host:lead:' + lead},
                   'scope': {'scopeVersion': 1, 'actor': 'host:lead:' + lead},
                   'hostConclusion': {'ownerGeneration': 1, 'actor': 'lead:' + lead},
                   'integrations': [{'createdAt': '2026-01-01T00:00:00Z', 'actor': 'host:lead:' + lead}],
                   'cancellation': {'actor': 'host:lead:' + lead, 'reason': 'fixture cancellation'},
                   'currentTurn': {'outcome': {'actor': 'host:stay-opaque'}}}
        attempt_id = connection.execute('SELECT selected_attempt_id FROM tasks WHERE task_id=?', (owned,)).fetchone()[0]
        interrupted = {'terminationReason': 'interrupted', 'result': {
            'interruptionEvidence': {'kind': 'boot-changed', 'legacyStartedAt': '2026-01-01T00:00:00Z'},
            'opaque': {'actor': 'host:stay-opaque'}}}
        connection.execute('UPDATE attempts SET result_json=? WHERE attempt_id=?',
                           (json.dumps(interrupted), attempt_id))
        connection.execute('INSERT INTO attempt_legacy_starts VALUES(?,?)',
                           (attempt_id, '2026-01-01T00:00:00Z'))
        receipt['task'] = {'owner': 'host:lead:' + lead,
                           'interruptionEvidence': dict(interrupted['result']['interruptionEvidence']),
                           'selectedAttempt': {'result': json.loads(json.dumps(interrupted))}}
        receipt['attempt'] = {'result': json.loads(json.dumps(interrupted))}
        connection.execute('INSERT INTO commands(command_id,kind,request_hash,response_json,created_at) VALUES(?,?,?,?,?)',
                           ('fixture-lead-replay', 'workflow.fixture', 'fixture-hash', json.dumps(receipt),
                            '2026-01-01T00:00:00Z'))
        connection.execute('INSERT INTO commands(command_id,kind,request_hash,response_json,created_at) VALUES(?,?,?,?,?)',
                           ('fixture-host-replay', 'workflow.fixture', 'fixture-hash',
                            json.dumps({'runId': lead, 'ownerGeneration': 1,
                                        'control': {'hostId': 'lead:literal-host', 'controlToken': 'opaque-token'}}),
                            '2026-01-01T00:00:00Z'))
        connection.execute('INSERT INTO commands(command_id,kind,request_hash,response_json,created_at) VALUES(?,?,?,?,?)',
                           ('fixture-post-takeover', 'workflow.fixture', 'fixture-hash',
                            json.dumps({'runId': unrelated, 'ownerGeneration': 2,
                                        'scope': {'scopeVersion': 1, 'actor': 'host:lead:' + lead},
                                        'cancellation': {'actor': 'host:lead:' + lead,
                                                         'reason': 'before takeover'}}),
                            '2026-01-01T00:00:01Z'))
        connection.execute('INSERT INTO commands(command_id,kind,request_hash,response_json,created_at) VALUES(?,?,?,?,?)',
                           ('fixture-post-takeover-task', 'task.cancel', 'fixture-hash',
                            json.dumps({'task': {'taskId': unrelated,
                                                 'cancellation': {'actor': 'host:lead:' + lead,
                                                                  'reason': 'before takeover'}}}),
                            '2026-01-01T00:00:01Z'))
        connection.execute('INSERT INTO commands(command_id,kind,request_hash,response_json,created_at) VALUES(?,?,?,?,?)',
                           ('fixture-task-replay', 'task.cancel', 'fixture-hash',
                            json.dumps({'task': {'cancellation': {'actor': 'console:literal-host'}}}),
                            '2026-01-01T00:00:00Z'))
        connection.commit()
        connection.close()
        archived = backup.create(BoardStore(fixture['state']), offline=True)
        self.assertEqual(backup.verify(Path(archived['path']))['schema'], 20)
        board_upgrade.migrate(fixture['state'])
        with sqlite3.connect(fixture['state'] / 'board.sqlite3') as upgraded:
            upgraded.row_factory = sqlite3.Row
            self.assertEqual(dict(upgraded.execute(
                'SELECT run_id,host_id FROM workflow_runs WHERE run_id IN (?,?,?,?)',
                (owned, helper, unrelated, lead))),
                {owned: None, helper: None, unrelated: 'lead:' + lead, lead: 'lead:literal-host'})
            self.assertIsNone(upgraded.execute('SELECT owner FROM tasks WHERE task_id=?', (owned,)).fetchone()[0])
            self.assertEqual(tuple(upgraded.execute(
                "SELECT actor_role,actor_id FROM events WHERE kind='task.submitted' AND task_id=?",
                (owned,)).fetchone()), ('host', 'fixture-host'))
            self.assertEqual(tuple(upgraded.execute(
                "SELECT actor_role,actor_id FROM events WHERE kind='task.submitted' AND task_id=?",
                (lead,)).fetchone()), ('host', 'fixture-host'))
            self.assertEqual({tuple(row) for row in upgraded.execute(
                'SELECT actor_role,actor_id FROM workflow_scope_versions WHERE run_id IN (?,?) AND scope_version=1',
                (owned, unrelated))}, {('lead', lead)})
            rows = upgraded.execute("SELECT task_id,actor_role,actor_id,payload_json FROM events WHERE kind='workflow.fixture_actor'").fetchall()
            actors = {row['task_id']: (row['actor_role'], row['actor_id']) for row in rows}
            self.assertEqual(actors, {owned: ('lead', lead), helper: ('lead', lead),
                                      unrelated: ('host', 'lead:' + lead), lead: ('host', 'lead:literal-host')})
            self.assertEqual([tuple(row) for row in upgraded.execute(
                "SELECT actor_role,actor_id FROM events WHERE kind='task.cancel_requested' AND actor_id='literal-host'")],
                [('console', 'literal-host')])
            self.assertEqual([tuple(row) for row in upgraded.execute(
                "SELECT actor_role,actor_id FROM events WHERE kind='message.updated' AND actor_id='literal-bridge'")],
                [('host', 'literal-bridge')])
            for row in rows:
                payload = json.loads(row['payload_json'])
                self.assertNotIn('actor', payload)
                self.assertEqual(payload['opaque'], {'actor': 'host:stay-opaque'})
                self.assertEqual(payload['interruptionEvidence'], {'kind': 'boot-changed'})
            replay = json.loads(upgraded.execute(
                "SELECT response_json FROM commands WHERE command_id='fixture-lead-replay'").fetchone()[0])
            self.assertIsNone(replay['hostId'])
            self.assertIsNone(replay['requestFingerprint'])
            self.assertNotIn('control', replay)
            host_replay = json.loads(upgraded.execute(
                "SELECT response_json FROM commands WHERE command_id='fixture-host-replay'").fetchone()[0])
            self.assertEqual(host_replay['control']['controlToken'], 'opaque-token')
            post_takeover = json.loads(upgraded.execute(
                "SELECT response_json FROM commands WHERE command_id='fixture-post-takeover'").fetchone()[0])
            scope_actor = upgraded.execute('SELECT actor_role,actor_id FROM workflow_scope_versions '
                                           'WHERE run_id=? AND scope_version=1', (unrelated,)).fetchone()
            self.assertEqual(post_takeover['scope']['actor'],
                             {'role': scope_actor['actor_role'], 'id': scope_actor['actor_id']})
            cancellation_actor = next(row for row in upgraded.execute(
                "SELECT actor_role,actor_id,payload_json FROM events WHERE kind='workflow.cancelled' AND task_id=?",
                (unrelated,)) if json.loads(row['payload_json']).get('reason') == 'before takeover')
            expected_cancellation = {'role': cancellation_actor['actor_role'], 'id': cancellation_actor['actor_id']}
            self.assertEqual(post_takeover['cancellation']['actor'], expected_cancellation)
            task_replay = json.loads(upgraded.execute(
                "SELECT response_json FROM commands WHERE command_id='fixture-post-takeover-task'").fetchone()[0])
            self.assertEqual(task_replay['task']['cancellation']['actor'], expected_cancellation)
            self.assertIsNone(replay['task']['owner'])
            self.assertEqual(replay['executionWorkspace']['integrator'], {'role': 'lead', 'id': lead})
            self.assertEqual(json.loads(upgraded.execute(
                'SELECT execution_workspace_json FROM workflow_runs WHERE run_id=?', (owned,)).fetchone()[0])['integrator'],
                {'role': 'lead', 'id': lead})
            self.assertEqual(json.loads(upgraded.execute(
                'SELECT workspace_intent_json FROM workflow_children WHERE child_task_id=?', (helper,)).fetchone()[0])['integrator'],
                {'role': 'lead', 'id': lead})
            takeover = json.loads(upgraded.execute("SELECT payload_json FROM events WHERE kind='workflow.takeover' "
                                                   'AND task_id=?', (unrelated,)).fetchone()[0])
            self.assertIsNone(takeover['previousHostId'])
            self.assertEqual(takeover['newHostId'], 'lead:' + lead)
            self.assertEqual(tuple(upgraded.execute("SELECT actor_role,actor_id FROM events WHERE kind='workflow.takeover' "
                                                    'AND task_id=?', (unrelated,)).fetchone()), ('lead', lead))
            resolution = json.loads(upgraded.execute(
                'SELECT decision_json FROM workflow_requests WHERE request_id=?', ('fixture-resolution',)).fetchone()[0])
            self.assertEqual(resolution['resolution']['actor'], {'role': 'lead', 'id': lead})
            for field in ('scope', 'hostConclusion', 'cancellation'):
                self.assertEqual(replay[field]['actor'], {'role': 'lead', 'id': lead})
            self.assertEqual(replay['integrations'][0]['actor'], {'role': 'lead', 'id': lead})
            self.assertEqual(replay['currentTurn']['outcome'], {'actor': 'host:stay-opaque'})
            ordinary = json.loads(upgraded.execute(
                "SELECT response_json FROM commands WHERE command_id='fixture-task-replay'").fetchone()[0])
            self.assertEqual(ordinary['task']['cancellation']['actor'],
                             {'role': 'console', 'id': 'literal-host'})
            self.assertEqual(replay['task']['interruptionEvidence'], {'kind': 'boot-changed'})
            for envelope in (replay['task']['selectedAttempt']['result'], replay['attempt']['result'],
                             json.loads(upgraded.execute(
                                 'SELECT result_json FROM attempts WHERE attempt_id=?', (attempt_id,)).fetchone()[0])):
                self.assertEqual(envelope['result']['interruptionEvidence'], {'kind': 'boot-changed'})
                self.assertEqual(envelope['result']['opaque'], {'actor': 'host:stay-opaque'})

    def test_unsettled_legacy_attempt_refuses_conversion_without_consuming_base(self):
        fixture, connection = self.restored()
        _run_id, _manifest, attempt_id = self.sample(fixture, connection)
        connection.execute("UPDATE attempts SET execution_state='uncertain',shutdown_confirmed=0 WHERE attempt_id=?",
                           (attempt_id,))
        connection.execute('INSERT INTO attempt_legacy_starts VALUES(?,?)',
                           (attempt_id, '2026-01-01T00:00:00Z'))
        connection.commit()
        connection.close()
        store = BoardStore(fixture['state'])
        archived = backup.create(store)
        store.db.close()
        self.assertEqual(backup.verify(Path(archived['path']))['schema'], 20)
        before = (fixture['state'] / 'board.sqlite3').read_bytes()
        with self.assertRaises(BoardError) as caught:
            board_upgrade.migrate(fixture['state'])
        self.assertEqual(caught.exception.code, 'UPGRADE_NOT_IDLE')
        self.assertEqual((fixture['state'] / 'board.sqlite3').read_bytes(), before)
        with sqlite3.connect(fixture['state'] / 'board.sqlite3') as unchanged:
            self.assertEqual(unchanged.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0], '20')
            self.assertEqual(unchanged.execute('SELECT COUNT(*) FROM attempt_legacy_starts').fetchone()[0], 1)

    def test_unknown_actor_refuses_conversion_atomically(self):
        fixture, connection = self.restored()
        run_id = fixture['stableIds']['delivered']
        connection.execute('INSERT INTO events(task_id,kind,payload_json,created_at) VALUES(?,?,?,?)',
                           (run_id, 'workflow.fixture_actor', json.dumps({'actor': 'worker:untrusted'}),
                            '2026-01-01T00:00:00Z'))
        connection.commit()
        connection.close()
        before = (fixture['state'] / 'board.sqlite3').read_bytes()
        with self.assertRaises(BoardError) as caught:
            board_upgrade.migrate(fixture['state'])
        self.assertEqual(caught.exception.code, 'UPGRADE_INVALID_ACTOR')
        with sqlite3.connect(fixture['state'] / 'board.sqlite3') as preserved:
            self.assertEqual(preserved.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0], '20')
            self.assertEqual(preserved.execute('PRAGMA integrity_check').fetchone()[0], 'ok')
        self.assertEqual((fixture['state'] / 'board.sqlite3').read_bytes(), before)

    def test_old_submission_digest_is_cleared_and_replay_is_explicitly_refused(self):
        fixture, connection = self.restored()
        # A previous schema upgrade may have changed the request's stored inputs.
        # The old digest is opaque evidence, not a recoverable current request.
        connection.execute('UPDATE workflow_runs SET request_fingerprint=?', ('0' * 64,))
        connection.commit()
        connection.close()
        archived = backup.create(BoardStore(fixture['state']), offline=True)
        self.assertEqual(backup.verify(Path(archived['path']))['schema'], 20)
        board_upgrade.migrate(fixture['state'])
        store = BoardStore(fixture['state'])
        store.initialize()
        self.addCleanup(store.db.close)
        for entry in fixture['operations']:
            if entry['operation'] != 'workflow_submit':
                continue
            for params in (entry['params'], {**entry['params'], 'task': 'Different input also cannot replay'}):
                with self.subTest(request=params['requestId'], changed=params is not entry['params']):
                    with self.assertRaises(BoardError) as caught:
                        store.workflow.submit(params)
                    self.assertEqual(caught.exception.code, 'SUBMISSION_REPLAY_UNAVAILABLE')
                    self.assertEqual(caught.exception.details['requestId'], params['requestId'])
        with store.db.read() as upgraded:
            self.assertEqual(upgraded.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0], '21')
            self.assertTrue(all(row[0] is None for row in upgraded.execute(
                'SELECT request_fingerprint FROM workflow_runs')))
            for row in upgraded.execute("SELECT response_json FROM commands WHERE kind LIKE 'workflow.%'"):
                response = json.loads(row[0])
                if isinstance(response, dict) and 'requestFingerprint' in response:
                    self.assertIsNone(response['requestFingerprint'])
        repository = self.root / 'new-submission-repository'
        repository.mkdir()
        _git(repository, 'init')
        _git(repository, '-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid',
             'commit', '--allow-empty', '-m', 'Private test input')
        current = {'requestId': 'post-upgrade-submit', 'hostId': 'current-host',
                   'adapter': 'external', 'task': 'New schema-21 submission', 'cwd': str(repository),
                   'executionWorkspace': {'kind': 'existing', 'access': 'read'}}
        first = store.workflow.submit(current)
        replay = store.workflow.submit(current)
        self.assertIsNotNone(first['requestFingerprint'])
        self.assertTrue(replay['duplicate'])
        self.assertEqual(replay['runId'], first['runId'])
        self.assertEqual(replay['requestFingerprint'], first['requestFingerprint'])

    def test_malformed_product_actor_refuses_conversion_atomically(self):
        fixture, connection = self.restored()
        run_id = fixture['stableIds']['delivered']
        connection.execute('INSERT INTO events(task_id,kind,payload_json,created_at) VALUES(?,?,?,?)',
                           (run_id, 'workflow.fixture_actor', json.dumps({'actor': {'role': 'host', 'id': 'x'}}),
                            '2026-01-01T00:00:00Z'))
        connection.commit()
        connection.close()
        before = (fixture['state'] / 'board.sqlite3').read_bytes()
        with self.assertRaises(BoardError) as caught:
            board_upgrade.migrate(fixture['state'])
        self.assertEqual(caught.exception.code, 'UPGRADE_INVALID_ACTOR')
        self.assertEqual((fixture['state'] / 'board.sqlite3').read_bytes(), before)
