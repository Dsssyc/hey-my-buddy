"""Portable schema-23 stock from the installed base's real named operations."""
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
from hey_my_buddy.blackboard.store.store import BoardStore
from hey_my_buddy.blackboard.routing import router
from hey_my_buddy.blackboard.store.db import Database, SCHEMA_VERSION
from hey_my_buddy.blackboard.store.upgrade import UPGRADE_BASE_SCHEMA, upgrade_schema_23_to_24
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
        upgrade_schema_23_to_24(fixture['state'])
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
        self.assertEqual(fixture['sourceCommit'], '9baa0524a884fb335f64446a7bad8f27522082b8')
        self.assertEqual(fixture['sourceVersion'], '0.40.0')
        self.assertEqual(fixture['schemaVersion'], 23)
        self.assertEqual(connection.execute('PRAGMA integrity_check').fetchone()[0], 'ok')
        self.assertEqual(connection.execute('PRAGMA foreign_key_check').fetchall(), [])
        self.assertEqual(connection.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0], '23')
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

    def test_authoritative_allocation_references_survive_receipt_and_state_conversion(self):
        from hey_my_buddy.blackboard.tasks import storage

        fixture, old = self.restored()
        allocations = []
        for row in old.execute('SELECT workspace_manifest_json FROM workflow_runs'):
            manifest = json.loads(row[0])
            allocations.append(manifest)
        retained = {}
        for manifest in allocations:
            rows = ((table, row) for table, row in storage._workspace_reference_rows(old)
                    if table not in ('commands', 'events'))
            protected = storage._allocation_reference_reasons(rows, manifest, None)
            self.assertIn('workflow_runs-reference', protected)
            self.assertIn('workflow_artifacts-reference', protected)
            retained[manifest['workspaceId']] = set(protected)
        old.close()
        upgrade_schema_23_to_24(fixture['state'])
        with sqlite3.connect(fixture['state'] / 'board.sqlite3') as current:
            current.row_factory = sqlite3.Row
            for manifest in allocations:
                reasons = set(storage.allocation_references(current, manifest))
                reasons.discard('commands-reference')
                reasons.discard('events-reference')
                self.assertEqual(reasons, retained[manifest['workspaceId']])

    def test_contract_cutover_preserves_stock_and_matches_fresh_structure(self):
        from hey_my_buddy.blackboard.tasks.command_receipts import stored_response

        fixture, old = self.restored()
        state = fixture['state']
        self.assertEqual(fixture['schemaVersion'], UPGRADE_BASE_SCHEMA)
        run_id, _, attempt_id = self.sample(fixture, old)
        old.executemany('INSERT INTO events(task_id,attempt_id,kind,payload_json,created_at) VALUES(?,?,?,?,?)', [
            (run_id, attempt_id, 'attempt.activity', '{"phase":"first"}', '2026-01-01T00:00:01Z'),
            (run_id, attempt_id, 'attempt.activity', '{"phase":"last"}', '2026-01-01T00:00:02Z'),
            (None, None, 'harness.checked', '{"harness":"codex","status":"first"}', '2026-01-01T00:00:01Z'),
            (None, None, 'harness.checked', '{"harness":"codex","status":"last"}', '2026-01-01T00:00:02Z'),
            (None, None, 'harness.probe_unknown', '{"harness":"codex","status":"first"}', '2026-01-01T00:00:01Z'),
            (None, None, 'harness.probe_unknown', '{"harness":"codex","status":"last"}', '2026-01-01T00:00:02Z'),
            (None, None, 'harness.account_observed', '{"harness":"codex","status":"first"}', '2026-01-01T00:00:01Z'),
            (None, None, 'harness.account_observed', '{"harness":"codex","status":"last"}', '2026-01-01T00:00:02Z'),
        ])
        old.commit()
        before = upgrade.idle_snapshot(state, exact=True)
        expected_settings = router.configuration(old)
        expected_runs = dict(old.execute('SELECT run_id,state FROM workflow_runs'))
        request_fingerprints = dict(old.execute('SELECT run_id,request_fingerprint FROM workflow_runs'))
        expected_preferences = [tuple(row) for row in old.execute(
            'SELECT buddy_id,mode,reason FROM evaluation_preferences ORDER BY buddy_id')]
        retained_meta = dict(old.execute("SELECT key,value FROM meta WHERE key!='schema_version'"))
        stock = {row[0]: [dict(item) for item in old.execute(f'SELECT * FROM {row[0]} ORDER BY rowid')]
                 for row in old.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
        retained_events = {}
        for row in stock['events']:
            if row['kind'] == 'attempt.activity':
                retained_events[('activity', row['attempt_id'])] = row['seq']
            elif row['kind'] in ('harness.checked', 'harness.probe_unknown', 'harness.account_observed'):
                retained_events[(row['kind'], json.loads(row['payload_json'])['harness'])] = row['seq']
        stock['events'] = [row for row in stock['events'] if row['kind'] not in
                           ('attempt.activity', 'harness.checked', 'harness.probe_unknown', 'harness.account_observed')
                           or row['seq'] in retained_events.values()]
        self.assertTrue(any(row['kind'] == 'attempt.activity' for row in stock['events']))
        for kind in ('harness.checked', 'harness.probe_unknown', 'harness.account_observed'):
            self.assertEqual(sum(row['kind'] == kind and json.loads(row['payload_json'])['harness'] == 'codex'
                                 for row in stock['events']), 1)
        stock['commands'] = [row for row in stock['commands'] if row['kind'] != 'execution.result']
        for row in stock['commands']:
            if row['kind'] == 'harness.prepare':
                continue
            original = json.loads(row['response_json'])
            response = stored_response(row['kind'], original)
            if response != original:
                row['response_json'] = _json(response)
        for row in stock['meta']:
            if row['key'] == 'schema_version':
                row['value'] = str(SCHEMA_VERSION)
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
        self.assertEqual({relative: (state / relative).read_bytes() for relative in history}, history)
        self.assertEqual(backup.verify(Path(result['backup']['path']))['schema'], fixture['schemaVersion'])

        fresh = Database(self.root / 'fresh')
        fresh.initialize()
        with sqlite3.connect(state / 'board.sqlite3') as upgraded, fresh.read() as baseline:
            upgraded.row_factory = sqlite3.Row
            # Only the explicit command, event and schema-version conversions
            # differ from the released base's stored rows.
            for table, rows in stock.items():
                with self.subTest(retained_table=table):
                    self.assertEqual([dict(item) for item in upgraded.execute(f'SELECT * FROM {table} ORDER BY rowid')], rows)
            self.assertEqual(set(stock),
                             {row[0] for row in upgraded.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")})
            source_schema = [tuple(row) for row in upgraded.execute(
                'SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name')]
            fresh_schema = [tuple(row) for row in baseline.execute(
                'SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name')]
            self.assertEqual(source_schema, fresh_schema)
            self.assertEqual(upgraded.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0],
                             str(SCHEMA_VERSION))
            self.assertEqual(dict(upgraded.execute("SELECT key,value FROM meta WHERE key!='schema_version'")),
                             retained_meta)
            self.assertEqual(tuple(upgraded.execute(
                'SELECT silence_seconds,revision FROM reminder_settings WHERE id=1').fetchone()), (900, 1))
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
            self.assertEqual(router.configuration(upgraded), expected_settings)
            self.assertEqual(dict(upgraded.execute('SELECT run_id,state FROM workflow_runs')), expected_runs)
            self.assertEqual(dict(upgraded.execute('SELECT run_id,request_fingerprint FROM workflow_runs')),
                             request_fingerprints)
            self.assertEqual([tuple(row) for row in upgraded.execute(
                'SELECT buddy_id,mode,reason FROM evaluation_preferences ORDER BY buddy_id')],
                expected_preferences)
            self.assertEqual(upgraded.execute('PRAGMA integrity_check').fetchone()[0], 'ok')
            self.assertEqual(upgraded.execute('PRAGMA foreign_key_check').fetchall(), [])

    def test_failed_conversion_rolls_back_all_receipts_and_schema(self):
        from hey_my_buddy.blackboard.tasks.command_receipts import stored_response

        fixture, connection = self.restored('failed-conversion')
        before = backup.database_snapshot(connection, exact=True)
        converted = 0

        def interrupt(kind, response):
            nonlocal converted
            if converted:
                raise RuntimeError('injected conversion interruption')
            result = stored_response(kind, response)
            if result != response:
                converted += 1
            return result

        with mock.patch('hey_my_buddy.blackboard.tasks.command_receipts.stored_response', side_effect=interrupt):
            with self.assertRaisesRegex(RuntimeError, 'injected conversion interruption'):
                upgrade_schema_23_to_24(fixture['state'])
        self.assertEqual(converted, 1)
        self.assertEqual(backup.database_snapshot(connection, exact=True), before)
        self.assertEqual(connection.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0],
                         str(UPGRADE_BASE_SCHEMA))

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
        before = upgrade.idle_snapshot(fixture['state'], exact=True)
        target = self.root / 'runtimes' / ('b' * 32)
        health = {'runtimeContentId': target.name, 'runtimeStable': True, 'schemaVersion': UPGRADE_BASE_SCHEMA}
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
        # A NULL fingerprint is retained stock from submissions that predate
        # fingerprinting; the current reader still refuses their replay.
        connection.execute('UPDATE workflow_runs SET request_fingerprint=NULL')
        connection.commit()
        connection.close()
        upgrade_schema_23_to_24(fixture['state'])
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
