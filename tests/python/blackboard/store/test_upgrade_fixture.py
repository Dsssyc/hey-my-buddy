"""Portable schema-22 stock from the installed base's real named operations."""
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
from hey_my_buddy.blackboard.store.upgrade import upgrade_database
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

    def assert_projected_delta(self, before, after, path=()):
        """Every retained JSON difference must be one named service field conversion."""
        if isinstance(before, dict):
            self.assertIsInstance(after, dict, path)
            self.assertFalse(after.keys() - before.keys(), path)
            self.assertTrue(before.keys() - after.keys() <= {
                'workerId', 'workerIdentity', 'workerInstance', 'leaseSeconds',
                'leaseExpiresAt', 'claimRequestId', 'worker'}, path)
            for key in before.keys() & after.keys():
                self.assert_projected_delta(before[key], after[key], (*path, key))
        elif isinstance(before, list):
            self.assertIsInstance(after, list, path)
            self.assertEqual(len(after), len(before), path)
            for index, (old, new) in enumerate(zip(before, after)):
                self.assert_projected_delta(old, new, (*path, index))
        elif before != after:
            self.assertIn((path[-1], before, after), {
                ('queueReason', 'awaiting-worker', 'awaiting-execution'),
                ('kind', 'worker-stopped', 'orphan-stopped'),
                ('evidence', 'worker-stopped', 'orphan-stopped'),
                ('releaseEvidence', 'spawnIntentWritten=false', 'processCreated=false'),
            }, path)

    def insert_retained_native_selections(self, fixture, connection):
        selected = {}
        for key, adapter in (('delivered', 'command'), ('accepted', 'external')):
            run_id = fixture['stableIds'][key]
            original = json.loads(connection.execute(
                'SELECT micro_task_json FROM workflow_runs WHERE run_id=?', (run_id,)
            ).fetchone()[0])
            for field in ('harness', 'provider', 'model', 'effort'):
                original.pop(field, None)
            original['adapter'] = adapter
            original_raw = _json(original)
            payload = _json({'source': 'original-explicit', 'adapter': adapter})
            connection.execute('UPDATE tasks SET adapter=? WHERE task_id=?', (adapter, run_id))
            connection.execute('UPDATE attempts SET adapter=? WHERE task_id=?', (adapter, run_id))
            connection.execute('UPDATE workflow_runs SET buddy_json=?,micro_task_json=?,micro_task_fingerprint=?'
                               ' WHERE run_id=?', (None, original_raw,
                                                  hashlib.sha256(original_raw.encode()).hexdigest(), run_id))
            connection.execute("UPDATE events SET kind='workflow.execution_selected',payload_json=?"
                               " WHERE task_id=? AND kind='workflow.buddy_selected'", (payload, run_id))
            event = connection.execute(
                "SELECT seq FROM events WHERE task_id=? AND kind='workflow.execution_selected'", (run_id,)
            ).fetchone()
            self.assertIsNotNone(event)
            selected[run_id] = (adapter, event[0])
        connection.commit()
        return selected

    def test_retained_execution_rows_remain_readable_with_no_buddy(self):
        from hey_my_buddy.cli.cli_views import list_brief, micro_task_brief, task_brief

        fixture, connection = self.restored('read-old-executions')
        selections = self.insert_retained_native_selections(fixture, connection)
        turns = {}
        for run_id, (adapter, _seq) in selections.items():
            for row in connection.execute('SELECT turn_id,input_json FROM workflow_turns WHERE run_id=?', (run_id,)).fetchall():
                payload = json.loads(row['input_json'])
                payload['context']['buddy'] = {'adapter': adapter}
                raw = json.dumps(payload)
                turns[row['turn_id']] = raw
                connection.execute('UPDATE workflow_turns SET input_json=?,input_sha256=? WHERE turn_id=?',
                                   (raw, hashlib.sha256(raw.encode()).hexdigest(), row['turn_id']))
        self.assertTrue(turns)
        connection.commit()
        upgrade_database(fixture['state'])
        store = BoardStore(fixture['state'])
        store.initialize()
        self.addCleanup(store.db.close)
        listed = store.task_list({})
        brief_rows = {row['runId']: row for row in list_brief(listed)['runs']}
        for run_id, (adapter, _seq) in selections.items():
            with self.subTest(adapter=adapter):
                workflow = store.workflow.get({'runId': run_id, 'includeAudit': True})
                task = store.task_get({'runId': run_id})['task']
                for view in (workflow, micro_task_brief(workflow), task_brief(task), brief_rows[run_id]):
                    self.assertIsNone(view['buddy'])
                self.assertNotIn('adapter', workflow)
                self.assertNotIn('adapter', micro_task_brief(workflow))
                self.assertEqual(task_brief(task)['adapter'], adapter)
                self.assertEqual(brief_rows[run_id]['adapter'], adapter)
                self.assertIsNone(task['lineage']['buddy'])
                self.assertEqual(task['adapter'], adapter)
                self.assertIsNone(workflow['currentTurn']['buddy'])
                for turn in [*workflow['turns'], *workflow['audit']['turns']]:
                    self.assertIsNone(turn['buddy'])
        self.assertEqual(dict(connection.execute('SELECT turn_id,input_json FROM workflow_turns WHERE turn_id IN ('
                                                + ','.join('?' for _ in turns) + ')', list(turns))), turns)

    def test_public_operation_stock_retains_source_schema_and_history_hashes(self):
        fixture, connection = self.restored()
        self.assertEqual(fixture['sourceCommit'], 'b9632dd9421b02fab170752175edabf683a8aeb1')
        self.assertEqual(fixture['sourceVersion'], '0.39.0')
        self.assertEqual(fixture['schemaVersion'], 22)
        self.assertEqual(connection.execute('PRAGMA integrity_check').fetchone()[0], 'ok')
        self.assertEqual(connection.execute('PRAGMA foreign_key_check').fetchall(), [])
        self.assertEqual(connection.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0], '22')
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
        before = upgrade.idle_snapshot(state, exact=True)
        expected_settings = router.configuration(old)
        expected_runs = dict(old.execute('SELECT run_id,state FROM workflow_runs'))
        request_fingerprints = dict(old.execute('SELECT run_id,request_fingerprint FROM workflow_runs'))
        expected_preferences = [tuple(row) for row in old.execute(
            'SELECT buddy_id,mode,reason FROM evaluation_preferences ORDER BY buddy_id')]
        retained_meta = dict(old.execute("SELECT key,value FROM meta WHERE key!='schema_version'"))
        stock = {row[0]: [dict(item) for item in old.execute(f'SELECT * FROM {row[0]} ORDER BY rowid')]
                 for row in old.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
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
        after = upgrade.idle_snapshot(state, event_head=before['eventHead'], exact=True)
        self.assertEqual(after['schema'], SCHEMA_VERSION)
        self.assertEqual(after['eventHead'], before['eventHead'])
        self.assertNotIn('workers', after['tables'])
        self.assertEqual(after['tables']['attempts'], before['tables']['attempts'])
        self.assertEqual({relative: (state / relative).read_bytes() for relative in history}, history)
        self.assertEqual(backup.verify(Path(result['backup']['path']))['schema'], fixture['schemaVersion'])

        fresh = Database(self.root / 'fresh')
        fresh.initialize()
        with sqlite3.connect(state / 'board.sqlite3') as upgraded, fresh.read() as baseline:
            upgraded.row_factory = sqlite3.Row
            replaced = {'meta', 'tasks', 'attempts', 'attempt_process_identities',
                        'attempt_interruptions', 'commands', 'events', 'workers'}
            new_tables = {'attempt_shutdown_evidence', 'execution_issues', 'service_issues'}
            for table, rows in stock.items():
                if table in replaced:
                    continue
                with self.subTest(retained_table=table):
                    self.assertEqual([dict(item) for item in upgraded.execute(f'SELECT * FROM {table} ORDER BY rowid')], rows)
            for table in new_tables:
                with self.subTest(new_table=table):
                    self.assertEqual(upgraded.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0], 0)
            self.assertEqual(set(stock) - {'workers'} | new_tables,
                             {row[0] for row in upgraded.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")})
            for table, key, changed in (
                ('tasks', 'task_id', {'queue_reason'}),
                ('attempts', 'attempt_id', {'result_json', 'result_command_id'}),
                ('attempt_process_identities', 'attempt_id', set()),
                ('attempt_interruptions', 'attempt_id', {'evidence_kind'}),
                ('events', 'seq', {'payload_json'}),
            ):
                old_rows = {row[key]: row for row in stock[table]}
                new_rows = {row[key]: dict(row) for row in upgraded.execute(f'SELECT * FROM {table}')}
                self.assertEqual(set(new_rows), set(old_rows), table)
                for identity, original in old_rows.items():
                    for column in (original.keys() & new_rows[identity].keys()) - changed:
                        with self.subTest(table=table, row=identity, column=column):
                            self.assertEqual(new_rows[identity][column], original[column])
            for original in stock['tasks']:
                actual = upgraded.execute('SELECT queue_reason FROM tasks WHERE task_id=?',
                                          (original['task_id'],)).fetchone()[0]
                expected = ('awaiting-execution' if original['queue_reason'] == 'awaiting-worker'
                            else original['queue_reason'])
                self.assertEqual(actual, expected)
            for original in stock['attempt_interruptions']:
                actual = upgraded.execute('SELECT evidence_kind FROM attempt_interruptions WHERE attempt_id=?',
                                          (original['attempt_id'],)).fetchone()[0]
                expected = ('orphan-stopped' if original['evidence_kind'] == 'worker-stopped'
                            else original['evidence_kind'])
                self.assertEqual(actual, expected)
            old_commands = {row['command_id']: row for row in stock['commands']
                            if not (row['kind'].startswith('worker.') or row['kind'] == 'harness.prepare')}
            new_commands = {row['command_id']: dict(row) for row in upgraded.execute('SELECT * FROM commands')}
            self.assertEqual(set(new_commands), set(old_commands))
            for identity, original in old_commands.items():
                for column in original.keys() - {'response_json'}:
                    self.assertEqual(new_commands[identity][column], original[column])
                self.assert_projected_delta(json.loads(original['response_json']),
                                            json.loads(new_commands[identity]['response_json']),
                                            ('commands', identity))
            for original in stock['events']:
                current = upgraded.execute('SELECT payload_json FROM events WHERE seq=?',
                                           (original['seq'],)).fetchone()[0]
                self.assert_projected_delta(json.loads(original['payload_json']), json.loads(current),
                                            ('events', original['seq']))
            for original in stock['attempts']:
                current = upgraded.execute('SELECT result_json,result_command_id FROM attempts WHERE attempt_id=?',
                                           (original['attempt_id'],)).fetchone()
                if original['result_json'] is not None:
                    self.assert_projected_delta(json.loads(original['result_json']), json.loads(current[0]),
                                                ('attempts', original['attempt_id']))
                if original['result_command_id'] is not None:
                    retired = next((row for row in stock['commands']
                                    if row['command_id'] == original['result_command_id']), None)
                    expected_id = None if retired and (retired['kind'].startswith('worker.')
                                   or retired['kind'] == 'harness.prepare') else original['result_command_id']
                    self.assertEqual(current[1], expected_id)
            source_schema = [tuple(row) for row in upgraded.execute(
                'SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name')]
            fresh_schema = [tuple(row) for row in baseline.execute(
                'SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name')]
            self.assertEqual(source_schema, fresh_schema)
            self.assertNotIn('attempt_legacy_starts', after['tables'])
            self.assertEqual(upgraded.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0],
                             str(SCHEMA_VERSION))
            self.assertEqual(dict(upgraded.execute("SELECT key,value FROM meta WHERE key!='schema_version'")),
                             retained_meta)
            self.assertEqual(tuple(upgraded.execute(
                'SELECT silence_seconds,revision FROM reminder_settings WHERE id=1').fetchone()), (900, 1))
            self.assertEqual(upgraded.execute('SELECT COUNT(*) FROM attempt_reminders').fetchone()[0], 0)
            self.assertEqual(upgraded.execute('SELECT COUNT(*) FROM workflow_lead_tasks').fetchone()[0], 0)
            self.assertEqual(upgraded.execute('SELECT COUNT(*) FROM workflow_fixes').fetchone()[0], 0)
            fix_columns = {row['name']: row for row in upgraded.execute('PRAGMA table_info(workflow_fixes)')}
            self.assertEqual(set(fix_columns), {'run_id', 'fixed_run_id', 'created_at'})
            self.assertEqual(fix_columns['run_id']['pk'], 1)
            for label, connection in (('migrated', upgraded), ('fresh', baseline)):
                with self.subTest(schema=label):
                    columns = {row['name']: row for row in connection.execute('PRAGMA table_info(workflow_fixes)')}
                    self.assertEqual(columns['run_id']['notnull'], 1)
                    for run_id, fixed_run_id, column in (
                        (None, fixture['stableIds']['accepted'], 'run_id'),
                        (None, fixture['stableIds']['accepted'], 'run_id'),
                        ('missing-run', None, 'fixed_run_id'),
                    ):
                        with self.subTest(run_id=run_id, fixed_run_id=fixed_run_id):
                            with self.assertRaisesRegex(sqlite3.IntegrityError,
                                                        rf'NOT NULL constraint failed: workflow_fixes\.{column}'):
                                connection.execute(
                                    'INSERT INTO workflow_fixes(run_id,fixed_run_id,created_at) VALUES(?,?,?)',
                                    (run_id, fixed_run_id, '2026-01-01T00:00:00Z'))
                            self.assertEqual(connection.execute('SELECT COUNT(*) FROM workflow_fixes').fetchone()[0], 0)
            fix_fks = {(row['from'], row['table'], row['to']) for row in upgraded.execute(
                'PRAGMA foreign_key_list(workflow_fixes)')}
            self.assertEqual(fix_fks, {('run_id', 'workflow_runs', 'run_id'),
                                       ('fixed_run_id', 'workflow_runs', 'run_id')})
            fix_indexes = [row['name'] for row in upgraded.execute('PRAGMA index_list(workflow_fixes)')]
            self.assertIn(('fixed_run_id', 'created_at'), [tuple(row['name'] for row in upgraded.execute(
                f'PRAGMA index_info({name})')) for name in fix_indexes])
            self.assertEqual({row[0] for row in upgraded.execute(
                'SELECT delegation_authority FROM workflow_runs')}, {0})
            attempt_columns = {row[1] for row in upgraded.execute('PRAGMA table_info(attempts)')}
            self.assertTrue({'stop_initiator', 'grace_deadline', 'force_reason',
                             'force_requested_at', 'identity_confirmed_at'} <= attempt_columns)
            self.assertTrue(all(row[0] == 0 for row in upgraded.execute('SELECT cleanup_pending FROM attempts')))
            for column in ('force_reason', 'force_requested_at', 'identity_confirmed_at'):
                self.assertTrue(all(row[0] is None for row in upgraded.execute(f'SELECT {column} FROM attempts')))
            self.assertEqual(router.configuration(upgraded), expected_settings)
            self.assertEqual(dict(upgraded.execute('SELECT run_id,state FROM workflow_runs')), expected_runs)
            self.assertEqual(dict(upgraded.execute('SELECT run_id,request_fingerprint FROM workflow_runs')),
                             request_fingerprints)
            self.assertTrue(all(row['owner'] is None for row in upgraded.execute(
                'SELECT t.owner FROM tasks t JOIN workflow_runs r ON r.run_id=t.task_id')))
            self.assertEqual([tuple(row) for row in upgraded.execute(
                'SELECT buddy_id,mode,reason FROM evaluation_preferences ORDER BY buddy_id')],
                expected_preferences)
            self.assertEqual(upgraded.execute('PRAGMA integrity_check').fetchone()[0], 'ok')
            self.assertEqual(upgraded.execute('PRAGMA foreign_key_check').fetchall(), [])

    def test_upgrade_converts_only_service_owned_fields_and_preserves_opaque_results(self):
        fixture, connection = self.restored('opaque-upgrade')
        attempt = connection.execute('SELECT attempt_id FROM attempts LIMIT 1').fetchone()[0]
        opaque = {'status': 'ok', 'result': {'workerId': 'user-data', 'leaseSeconds': 7,
                  'nested': ['worker-stopped', 'awaiting-worker'], 'processIdentity': {'worker': 'user-value'}},
                  'shutdownConfirmed': True}
        raw = json.dumps(opaque, ensure_ascii=False, separators=(',', ':'))
        connection.execute('UPDATE attempts SET result_json=? WHERE attempt_id=?', (raw, attempt))
        connection.execute("INSERT INTO attempt_interruptions VALUES(?,'worker-stopped',?,'settled',NULL)",
                           (attempt, '2026-01-01T00:00:00Z'))
        response = {'task': {'taskId': 'fixture', 'requestId': 'fixture', 'spec': {}, 'state': 'completed',
                            'workerId': 'retired', 'queueReason': 'awaiting-worker',
                            'interruptionEvidence': {'kind': 'worker-stopped'},
                            'selectedAttempt': {'attemptId': attempt, 'generation': 1,
                            'executionState': 'finished', 'ownership': 'owned', 'workerId': 'retired',
                            'leaseSeconds': 120, 'result': opaque}},
                    'workflow': {'runId': 'fixture', 'requestId': 'fixture', 'state': 'running',
                                 'queueReason': 'awaiting-worker',
                                 'task': {'runId': 'fixture', 'requestId': 'fixture', 'state': 'queued',
                                          'queueReason': 'awaiting-worker',
                                          'interruptionEvidence': {'kind': 'worker-stopped'}}}}
        connection.execute('INSERT INTO commands(command_id,kind,request_hash,response_json,created_at) VALUES(?,?,?,?,?)',
                           ('projected-old-view', 'task.cancel', 'hash', json.dumps(response), '2026-01-01T00:00:00Z'))
        recovery = {'attempt': {'attemptId': attempt, 'generation': 1, 'executionState': 'finished',
                    'ownership': 'owned', 'result': {'status': 'failed', 'shutdownConfirmed': True,
                    'terminationReason': 'interrupted', 'result': {
                        'interruptionEvidence': {'kind': 'worker-stopped'},
                        'modelOutput': {'kind': 'worker-stopped'}}}}}
        flat = {'runId': 'flat-task', 'requestId': 'flat-request', 'state': 'failed',
                'status': 'failed', 'shutdownConfirmed': True, 'terminationReason': 'interrupted',
                'interruptionEvidence': {'kind': 'worker-stopped'}, 'result': {
                    'interruptionEvidence': {'kind': 'worker-stopped'},
                    'modelOutput': {'kind': 'worker-stopped'}}}
        connection.execute('INSERT INTO commands(command_id,kind,request_hash,response_json,created_at) VALUES(?,?,?,?,?)',
                           ('flat-recovery-view', 'task.result', 'flat-hash', json.dumps(flat),
                            '2026-01-01T00:00:00Z'))
        connection.execute('INSERT INTO commands(command_id,kind,request_hash,response_json,created_at) VALUES(?,?,?,?,?)',
                           ('recovery-old-view', 'task.result', 'recovery-hash', json.dumps(recovery),
                            '2026-01-01T00:00:00Z'))
        connection.execute('INSERT INTO commands(command_id,kind,request_hash,response_json,created_at) VALUES(?,?,?,?,?)',
                           ('retired-result', 'worker.result', 'retired-hash', '{}', '2026-01-01T00:00:00Z'))
        connection.execute('UPDATE attempts SET result_command_id=? WHERE attempt_id=?', ('retired-result', attempt))
        connection.execute('INSERT INTO events(kind,payload_json,created_at) VALUES(?,?,?)',
                           ('attempt.released', json.dumps({'releaseEvidence': 'spawnIntentWritten=false',
                            'previousState': 'starting', 'reason': 'no process'}), '2026-01-01T00:00:00Z'))
        connection.execute('INSERT INTO events(kind,payload_json,created_at) VALUES(?,?,?)',
                           ('worker.registered', json.dumps({'workerId': 'history', 'message': 'worker-stopped'}),
                            '2026-01-01T00:00:00Z'))
        connection.execute('INSERT INTO events(kind,payload_json,created_at) VALUES(?,?,?)',
                           ('workflow.summary', json.dumps({'task': {'runId': 'fixture', 'requestId': 'fixture',
                              'queueReason': 'awaiting-worker', 'state': 'queued'},
                              'modelOutput': {'queueReason': 'awaiting-worker'}}), '2026-01-01T00:00:00Z'))
        connection.execute('INSERT INTO events(kind,payload_json,created_at) VALUES(?,?,?)',
                           ('task.failed', json.dumps({'interruptionEvidence': {'kind': 'worker-stopped'},
                                                      'message': 'worker-stopped'}), '2026-01-01T00:00:00Z'))
        connection.commit()
        upgrade_database(fixture['state'])
        self.assertEqual(json.loads(connection.execute('SELECT result_json FROM attempts WHERE attempt_id=?',
                                                       (attempt,)).fetchone()[0]), opaque)
        self.assertEqual(connection.execute('SELECT evidence_kind FROM attempt_interruptions WHERE attempt_id=?',
                                            (attempt,)).fetchone()[0], 'orphan-stopped')
        projected = json.loads(connection.execute("SELECT response_json FROM commands WHERE command_id='projected-old-view'").fetchone()[0])
        self.assertNotIn('workerId', projected['task'])
        self.assertEqual(projected['task']['queueReason'], 'awaiting-execution')
        self.assertEqual(projected['task']['interruptionEvidence']['kind'], 'orphan-stopped')
        released = json.loads(connection.execute("SELECT payload_json FROM events WHERE kind='attempt.released' ORDER BY seq DESC LIMIT 1").fetchone()[0])
        self.assertEqual(released['releaseEvidence'], 'processCreated=false')
        self.assertNotIn('leaseSeconds', projected['task']['selectedAttempt'])
        self.assertEqual(projected['task']['selectedAttempt']['result'], opaque)
        self.assertEqual(projected['workflow']['queueReason'], 'awaiting-execution')
        self.assertEqual(projected['workflow']['task']['queueReason'], 'awaiting-execution')
        self.assertEqual(projected['workflow']['task']['interruptionEvidence']['kind'], 'orphan-stopped')
        recovered = json.loads(connection.execute("SELECT response_json FROM commands WHERE command_id='recovery-old-view'").fetchone()[0])
        self.assertEqual(recovered['attempt']['result']['result']['interruptionEvidence']['kind'], 'orphan-stopped')
        self.assertEqual(recovered['attempt']['result']['result']['modelOutput']['kind'], 'worker-stopped')
        flat_current = json.loads(connection.execute("SELECT response_json FROM commands WHERE command_id='flat-recovery-view'").fetchone()[0])
        self.assertEqual(flat_current['interruptionEvidence']['kind'], 'orphan-stopped')
        self.assertEqual(flat_current['result']['interruptionEvidence']['kind'], 'orphan-stopped')
        self.assertEqual(flat_current['result']['modelOutput']['kind'], 'worker-stopped')
        self.assertIsNone(connection.execute('SELECT result_command_id FROM attempts WHERE attempt_id=?',
                                             (attempt,)).fetchone()[0])
        self.assertIsNone(connection.execute("SELECT 1 FROM commands WHERE command_id='retired-result'").fetchone())
        history = json.loads(connection.execute("SELECT payload_json FROM events WHERE kind='worker.registered' ORDER BY seq DESC LIMIT 1").fetchone()[0])
        self.assertEqual(history, {'workerId': 'history', 'message': 'worker-stopped'})
        summary = json.loads(connection.execute("SELECT payload_json FROM events WHERE kind='workflow.summary' ORDER BY seq DESC LIMIT 1").fetchone()[0])
        self.assertEqual(summary['task']['queueReason'], 'awaiting-execution')
        self.assertEqual(summary['modelOutput']['queueReason'], 'awaiting-worker')
        failed = json.loads(connection.execute("SELECT payload_json FROM events WHERE kind='task.failed' ORDER BY seq DESC LIMIT 1").fetchone()[0])
        self.assertEqual(failed['interruptionEvidence']['kind'], 'orphan-stopped')
        self.assertEqual(failed['message'], 'worker-stopped')

    def test_upgrade_failure_rolls_back_every_table_replacement(self):
        fixture, connection = self.restored('rollback-conversion')
        connection.execute("INSERT INTO events(kind,payload_json,created_at) VALUES('task.failed','{broken','2026-01-01T00:00:00Z')")
        connection.commit()
        schema = [tuple(row) for row in connection.execute('SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name')]
        counts = connection.execute('SELECT COUNT(*) FROM attempts').fetchone()[0]
        with self.assertRaises(BoardError):
            upgrade_database(fixture['state'])
        self.assertEqual([tuple(row) for row in connection.execute('SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name')], schema)
        self.assertEqual(connection.execute('SELECT COUNT(*) FROM attempts').fetchone()[0], counts)
        self.assertEqual(connection.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0], '22')

    def insert_stop_receipt(self, fixture, connection):
        response = {'macroTaskId': 'run:' + fixture['stableIds']['cancelled'],
                    'results': [{'runId': fixture['stableIds']['cancelled'], 'state': 'cancelled', 'stopped': True},
                                {'runId': fixture['stableIds']['accepted'], 'state': 'accepted', 'stopped': False}],
                    'duplicate': False}
        connection.execute('INSERT INTO commands(command_id,kind,request_hash,response_json,created_at)'
                           ' VALUES(?,?,?,?,?)', ('install-stop-fixture', 'macro_task.stop', 'stop-request-hash',
                                                  json.dumps(response), '2026-01-01T00:00:00Z'))
        connection.execute('INSERT INTO commands(command_id,kind,request_hash,response_json,created_at)'
                           ' VALUES(?,?,?,?,?)', ('retained-command', 'workflow.cancel', 'other-request-hash',
                                                  '{"cancelled":true}', '2026-01-01T00:00:00Z'))
        connection.commit()
        return response

    def test_started_verification_preserves_command_fingerprints_and_rejects_tampering(self):
        fixture, connection = self.restored('started-stop')
        self.insert_stop_receipt(fixture, connection)
        upgrade_database(fixture['state'])
        before = upgrade.idle_snapshot(fixture['state'], exact=True)
        target = self.root / 'runtimes' / ('b' * 32)
        health = {'runtimeContentId': target.name, 'runtimeStable': True, 'schemaVersion': SCHEMA_VERSION}
        with mock.patch.object(upgrade, 'command', return_value={'leaks': []}):
            self.assertTrue(upgrade.verify_started(fixture['state'], target, health, before)['retainedDataFingerprints'])
        for command_id in ('install-stop-fixture', 'retained-command'):
            original = connection.execute('SELECT response_json FROM commands WHERE command_id=?', (command_id,)).fetchone()[0]
            connection.execute("UPDATE commands SET response_json='{}' WHERE command_id=?", (command_id,))
            connection.commit()
            with self.subTest(command_id=command_id), mock.patch.object(upgrade, 'command') as command:
                with self.assertRaises(BoardError) as caught:
                    upgrade.verify_started(fixture['state'], target, health, before)
                self.assertEqual(caught.exception.code, 'UPGRADE_VERIFY_FAILED')
                self.assertEqual(caught.exception.details['tables'], ['commands'])
                command.assert_not_called()
            connection.execute('UPDATE commands SET response_json=? WHERE command_id=?', (original, command_id))
            connection.commit()

    def test_unavailable_submission_digest_refuses_replay_and_new_submissions_replay(self):
        fixture, connection = self.restored()
        # A NULL fingerprint is retained schema-22 stock from the consumed upgrade.
        connection.execute('UPDATE workflow_runs SET request_fingerprint=NULL')
        connection.commit()
        connection.close()
        upgrade_database(fixture['state'])
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
            self.assertEqual(upgraded.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0], str(SCHEMA_VERSION))
            self.assertTrue(all(row[0] is None for row in upgraded.execute(
                'SELECT request_fingerprint FROM workflow_runs')))
        repository = self.root / 'new-submission-repository'
        repository.mkdir()
        _git(repository, 'init')
        _git(repository, '-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid',
             'commit', '--allow-empty', '-m', 'Private test input')
        current = {'requestId': 'post-upgrade-submit', 'hostId': 'current-host',
                   'harness': 'dsh', 'provider': 'deepseek-official', 'model': 'deepseek-flash', 'effort': 'off',
                   'task': 'New current-schema submission', 'cwd': str(repository),
                   'executionWorkspace': {'kind': 'existing', 'access': 'read'}}
        with mock.patch('hey_my_buddy.blackboard.catalog.catalog.validate_buddy', side_effect=lambda buddy, **_: buddy):
            first = store.workflow.submit(current)
            replay = store.workflow.submit(current)
        self.assertIsNotNone(first['requestFingerprint'])
        self.assertTrue(replay['duplicate'])
        self.assertEqual(replay['runId'], first['runId'])
        self.assertEqual(replay['requestFingerprint'], first['requestFingerprint'])
