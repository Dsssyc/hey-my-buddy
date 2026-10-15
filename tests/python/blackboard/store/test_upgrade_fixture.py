"""Portable schema-21 stock from the installed base's real named operations."""
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
from hey_my_buddy.blackboard.store import backup, upgrade as board_upgrade
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

    def insert_native_selections(self, fixture, connection):
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
            old_buddy = json.dumps({'adapter': adapter})
            old_payload = json.dumps({'source': 'original-explicit', 'buddy': {'adapter': adapter},
                                      'buddyRevision': 1})
            connection.execute('UPDATE tasks SET adapter=? WHERE task_id=?', (adapter, run_id))
            connection.execute('UPDATE attempts SET adapter=? WHERE task_id=?', (adapter, run_id))
            connection.execute('UPDATE workflow_runs SET buddy_json=?,micro_task_json=?,micro_task_fingerprint=?'
                               ' WHERE run_id=?', (old_buddy, original_raw,
                                                  hashlib.sha256(original_raw.encode()).hexdigest(), run_id))
            connection.execute("UPDATE events SET payload_json=? WHERE task_id=? AND kind='workflow.buddy_selected'",
                               (old_payload, run_id))
            event = connection.execute(
                "SELECT seq FROM events WHERE task_id=? AND kind='workflow.buddy_selected'", (run_id,)
            ).fetchone()
            self.assertIsNotNone(event)
            selected[run_id] = (adapter, event[0], old_buddy, old_payload)
        connection.commit()
        return selected

    def test_upgrade_preserves_historical_buddy_after_a_harness_override(self):
        fixture, connection = self.restored('historical-buddy')
        run_id = fixture['stableIds']['delivered']
        events = [tuple(row) for row in connection.execute(
            "SELECT seq,payload_json FROM events WHERE task_id=? AND kind='workflow.buddy_selected'", (run_id,))]
        self.assertTrue(events)
        original = json.loads(events[0][1])['buddy']
        harness = 'claude' if original['harness'] != 'claude' else 'codex'
        current = {'harness': harness, 'provider': 'fixture-provider', 'model': 'fixture-model', 'effort': 'high'}
        connection.execute("UPDATE tasks SET adapter=? WHERE task_id=?", (harness, run_id))
        connection.execute('UPDATE workflow_runs SET buddy_json=? WHERE run_id=?', (json.dumps(current), run_id))
        connection.commit()
        before = upgrade.idle_snapshot(fixture['state'], exact=True)
        board_upgrade.migrate(fixture['state'])
        upgrade.verify_migrated_snapshot(before, upgrade.idle_snapshot(fixture['state'], exact=True))
        self.assertEqual([tuple(row) for row in connection.execute(
            "SELECT seq,payload_json FROM events WHERE task_id=? AND kind='workflow.buddy_selected'", (run_id,))], events)
        self.assertEqual(json.loads(connection.execute(
            'SELECT buddy_json FROM workflow_runs WHERE run_id=?', (run_id,)).fetchone()[0]), current)

    def test_native_selection_event_uses_original_spec_after_harness_override(self):
        fixture, connection = self.restored('native-override')
        selections = self.insert_native_selections(fixture, connection)
        run_id = fixture['stableIds']['delivered']
        adapter, seq, _, _ = selections[run_id]
        current = {'harness': 'codex', 'provider': 'openai', 'model': 'fixture-model', 'effort': 'high'}
        connection.execute("UPDATE tasks SET adapter='codex' WHERE task_id=?", (run_id,))
        connection.execute('UPDATE workflow_runs SET buddy_json=? WHERE run_id=?', (json.dumps(current), run_id))
        connection.commit()
        before = upgrade.idle_snapshot(fixture['state'], exact=True)
        board_upgrade.migrate(fixture['state'])
        upgrade.verify_migrated_snapshot(before, upgrade.idle_snapshot(fixture['state'], exact=True))
        self.assertEqual(tuple(connection.execute('SELECT kind,payload_json FROM events WHERE seq=?', (seq,)).fetchone()),
                         ('workflow.execution_selected', _json({'source': 'original-explicit', 'adapter': adapter})))
        self.assertEqual(json.loads(connection.execute(
            'SELECT buddy_json FROM workflow_runs WHERE run_id=?', (run_id,)).fetchone()[0]), current)

    def test_migrated_execution_rows_remain_readable_with_no_buddy(self):
        from hey_my_buddy.cli.cli_views import list_brief, micro_task_brief, task_brief

        fixture, connection = self.restored('read-old-executions')
        selections = self.insert_native_selections(fixture, connection)
        turns = {}
        for run_id, (adapter, _seq, _buddy, _event) in selections.items():
            for row in connection.execute('SELECT turn_id,input_json FROM workflow_turns WHERE run_id=?', (run_id,)).fetchall():
                payload = json.loads(row['input_json'])
                payload['context']['buddy'] = {'adapter': adapter}
                raw = json.dumps(payload)
                turns[row['turn_id']] = raw
                connection.execute('UPDATE workflow_turns SET input_json=?,input_sha256=? WHERE turn_id=?',
                                   (raw, hashlib.sha256(raw.encode()).hexdigest(), row['turn_id']))
        self.assertTrue(turns)
        connection.commit()
        board_upgrade.migrate(fixture['state'])
        store = BoardStore(fixture['state'])
        store.initialize()
        self.addCleanup(store.db.close)
        listed = store.task_list({})
        brief_rows = {row['runId']: row for row in list_brief(listed)['runs']}
        for run_id, (adapter, _seq, _buddy, _event) in selections.items():
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
        self.assertEqual(fixture['sourceCommit'], 'f620f1e2719ccd170e6183e89c3f9eb04d084f32')
        self.assertEqual(fixture['sourceVersion'], '0.38.0')
        self.assertEqual(fixture['schemaVersion'], 21)
        self.assertEqual(connection.execute('PRAGMA integrity_check').fetchone()[0], 'ok')
        self.assertEqual(connection.execute('PRAGMA foreign_key_check').fetchall(), [])
        self.assertEqual(connection.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0], '21')
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
        old_stop = self.insert_stop_receipt(fixture, old)
        native = self.insert_native_selections(fixture, old)
        before = upgrade.idle_snapshot(state, exact=True)
        expected_settings = router.configuration(old)
        expected_runs = dict(old.execute('SELECT run_id,state FROM workflow_runs'))
        request_fingerprints = dict(old.execute('SELECT run_id,request_fingerprint FROM workflow_runs'))
        expected_preferences = [tuple(row) for row in old.execute(
            'SELECT buddy_id,mode,reason FROM evaluation_preferences ORDER BY buddy_id')]
        retained_meta = dict(old.execute("SELECT key,value FROM meta WHERE key!='schema_version'"))
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
        converted_tables = {'meta', 'commands', 'workflow_runs', 'events'}
        self.assertEqual({name: after['fingerprints'][name] for name in before['fingerprints'] if name not in converted_tables},
                         {name: value for name, value in before['fingerprints'].items() if name not in converted_tables})
        self.assertEqual(after['fingerprints']['commands'], before['migratedCommandsFingerprint'])
        self.assertEqual(after['fingerprints']['workflow_runs'], before['migratedWorkflowRunsFingerprint'])
        self.assertEqual(after['fingerprints']['events'], before['migratedEventsFingerprint'])
        with sqlite3.connect(state / 'board.sqlite3') as migrated:
            receipt = json.loads(migrated.execute(
                "SELECT response_json FROM commands WHERE command_id='install-stop-fixture'").fetchone()[0])
            for run_id, (adapter, seq, _, _) in native.items():
                self.assertIsNone(migrated.execute(
                    'SELECT buddy_json FROM workflow_runs WHERE run_id=?', (run_id,)).fetchone()[0])
                self.assertEqual(tuple(migrated.execute(
                    'SELECT seq,kind,payload_json FROM events WHERE seq=?', (seq,)).fetchone()),
                    (seq, 'workflow.execution_selected',
                     json.dumps({'source': 'original-explicit', 'adapter': adapter},
                                sort_keys=True, separators=(',', ':'))))
            self.assertEqual(migrated.execute(
                "SELECT COUNT(*) FROM events WHERE kind='workflow.buddy_selected'").fetchone()[0], 2)
        self.assertEqual(receipt, json.loads(board_upgrade.migrated_stop_response(json.dumps(old_stop))))
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
            self.assertTrue({'stop_initiator', 'grace_deadline'} <= attempt_columns)
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

    def test_upgrade_refuses_existing_fix_storage_without_changing_the_board(self):
        definitions = (
            ('CREATE TABLE workflow_fixes(run_id TEXT PRIMARY KEY, fixed_run_id TEXT, created_at TEXT)', None),
            ('CREATE TABLE workflow_fixes(run_id TEXT PRIMARY KEY, fixed_run_id TEXT, created_at TEXT)',
             "INSERT INTO workflow_fixes VALUES('prior', 'target', '2026-01-01T00:00:00Z')"),
            ('CREATE VIEW workflow_fixes AS SELECT run_id,run_id AS fixed_run_id,created_at FROM workflow_runs', None),
        )
        for index, (definition, insert) in enumerate(definitions):
            with self.subTest(index=index):
                fixture, connection = self.restored(f'existing-fixes-{index}')
                connection.execute(definition)
                if insert:
                    connection.execute(insert)
                connection.commit()
                before = connection.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0]
                with self.assertRaises(BoardError) as caught:
                    board_upgrade.migrate(fixture['state'])
                self.assertEqual(caught.exception.code, 'UPGRADE_INVALID_BOARD')
                self.assertEqual(connection.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0], before)
                self.assertIsNone(connection.execute(
                    "SELECT name FROM sqlite_master WHERE name='reminder_settings'").fetchone())

    def test_migrated_snapshot_rejects_a_fix_row_inserted_after_upgrade(self):
        fixture, connection = self.restored('changed-fixes')
        before = upgrade.idle_snapshot(fixture['state'], exact=True)
        board_upgrade.migrate(fixture['state'])
        after = upgrade.idle_snapshot(fixture['state'], event_head=before['eventHead'], exact=True)
        upgrade.verify_migrated_snapshot(before, after)
        connection.execute('INSERT INTO workflow_fixes(run_id,fixed_run_id,created_at) VALUES(?,?,?)',
                           (fixture['stableIds']['accepted'], fixture['stableIds']['delivered'],
                            '2026-01-01T00:00:00Z'))
        connection.commit()
        corrupted = upgrade.idle_snapshot(fixture['state'], event_head=before['eventHead'], exact=True)
        with self.assertRaises(BoardError) as caught:
            upgrade.verify_migrated_snapshot(before, corrupted)
        self.assertEqual(caught.exception.code, 'UPGRADE_VERIFY_FAILED')

    def insert_stop_receipt(self, fixture, connection):
        old = {'macroTaskId': 'run:' + fixture['stableIds']['cancelled'],
               'runIds': [fixture['stableIds']['cancelled']],
               'acceptedRunIds': [fixture['stableIds']['accepted']], 'concludedRunIds': [],
               'results': [{'runId': fixture['stableIds']['cancelled'], 'state': 'cancelled',
                            'cancelled': True, 'duplicate': False, 'note': 'Historical stop',
                            'status': 'cancelled'}], 'duplicate': False}
        connection.execute('INSERT INTO commands(command_id,kind,request_hash,response_json,created_at)'
                           ' VALUES(?,?,?,?,?)', ('install-stop-fixture', 'macro_task.stop', 'stop-request-hash',
                                                  json.dumps(old), '2026-01-01T00:00:00Z'))
        connection.execute('INSERT INTO commands(command_id,kind,request_hash,response_json,created_at)'
                           ' VALUES(?,?,?,?,?)', ('retained-command', 'workflow.cancel', 'other-request-hash',
                                                  '{"cancelled":true}', '2026-01-01T00:00:00Z'))
        connection.commit()
        return old

    def test_migration_verification_refuses_changes_beyond_the_exact_stop_conversion(self):
        changes = (
            ("UPDATE commands SET response_json='{}' WHERE command_id='install-stop-fixture'",),
            ("UPDATE commands SET request_hash='tampered' WHERE command_id='install-stop-fixture'",),
            ("UPDATE commands SET response_json='{}' WHERE command_id='retained-command'",),
            ("DELETE FROM commands WHERE command_id='retained-command'",),
            ("UPDATE workflow_runs SET title='tampered' WHERE run_id=?", 'cancelled'),
        )
        for index, change in enumerate(changes):
            with self.subTest(change=change[0]):
                fixture, connection = self.restored(f'verify-stop-{index}')
                self.insert_stop_receipt(fixture, connection)
                before = upgrade.idle_snapshot(fixture['state'], exact=True)
                board_upgrade.migrate(fixture['state'])
                after = upgrade.idle_snapshot(fixture['state'], event_head=before['eventHead'], exact=True)
                upgrade.verify_migrated_snapshot(before, after)
                connection.execute(change[0], (fixture['stableIds'][change[1]],) if len(change) == 2 else ())
                connection.commit()
                corrupted = upgrade.idle_snapshot(fixture['state'], event_head=before['eventHead'], exact=True)
                with self.assertRaises(BoardError) as caught:
                    upgrade.verify_migrated_snapshot(before, corrupted)
                self.assertEqual(caught.exception.code, 'UPGRADE_VERIFY_FAILED')

    def test_native_view_receipts_are_deleted_by_original_run_identity(self):
        fixture, connection = self.restored('native-receipts')
        selections = self.insert_native_selections(fixture, connection)
        native_id = fixture['stableIds']['delivered']
        model_id = fixture['stableIds']['failed']
        # The current adapter can change after Host override. Ownership follows
        # the immutable original micro task and the receipt's task_id.
        connection.execute("UPDATE tasks SET adapter='codex' WHERE task_id=?", (native_id,))
        connection.execute('UPDATE workflow_runs SET buddy_json=? WHERE run_id=?',
                           (_json({'harness': 'codex', 'provider': 'openai',
                                   'model': 'fixture-model', 'effort': 'high'}), native_id))
        connection.execute('INSERT INTO commands(command_id,task_id,kind,request_hash,response_json,created_at)'
                           ' VALUES(?,?,?,?,?,?)', ('native-decide', native_id, 'workflow.decide', 'hash',
                                                   '{"buddy":{"adapter":"command"}}', '2026-01-01T00:00:00Z'))
        connection.execute('INSERT INTO commands(command_id,task_id,kind,request_hash,response_json,created_at)'
                           ' VALUES(?,?,?,?,?,?)', ('model-decide', model_id, 'workflow.decide', 'hash',
                                                   '{"buddy":{"harness":"codex"}}', '2026-01-01T00:00:00Z'))
        connection.execute('INSERT INTO commands(command_id,task_id,kind,request_hash,response_json,created_at)'
                           ' VALUES(?,?,?,?,?,?)', ('native-message', native_id, 'message.post', 'hash',
                                                   '{"message":"retained"}', '2026-01-01T00:00:00Z'))
        connection.commit()
        before = upgrade.idle_snapshot(fixture['state'], exact=True)
        board_upgrade.migrate(fixture['state'])
        after = upgrade.idle_snapshot(fixture['state'], exact=True)
        upgrade.verify_migrated_snapshot(before, after)
        self.assertEqual(after['tables']['commands'], before['migratedCommandsCount'])
        self.assertEqual(after['fingerprints']['commands'], before['migratedCommandsFingerprint'])
        kinds = dict(connection.execute('SELECT command_id,kind FROM commands'))
        self.assertNotIn('native-decide', kinds)
        self.assertIn('model-decide', kinds)
        self.assertIn('native-message', kinds)
        self.assertTrue(all(not (task_id in selections and
                                 (kind.startswith('workflow.') or kind in ('worker.claim', 'worker.result')))
                            for task_id, kind in connection.execute('SELECT task_id,kind FROM commands')))

    def test_exact_migration_snapshot_detects_workers_sequence_and_metadata_changes(self):
        changes = (
            ('workers', "UPDATE workers SET last_seen_at='changed' WHERE worker_id=(SELECT worker_id FROM workers LIMIT 1)"),
            ('sqlite_sequence', "UPDATE sqlite_sequence SET seq=seq+1 WHERE name='events'"),
            ('meta', "UPDATE meta SET value='changed' WHERE key='created_at'"),
        )
        for index, (table, statement) in enumerate(changes):
            with self.subTest(table=table):
                fixture, connection = self.restored(f'exact-{index}')
                before = upgrade.idle_snapshot(fixture['state'], exact=True)
                board_upgrade.migrate(fixture['state'])
                after = upgrade.idle_snapshot(fixture['state'], exact=True)
                upgrade.verify_migrated_snapshot(before, after)
                connection.execute(statement)
                connection.commit()
                corrupted = upgrade.idle_snapshot(fixture['state'], exact=True)
                with self.assertRaises(BoardError) as caught:
                    upgrade.verify_migrated_snapshot(before, corrupted)
                self.assertEqual(caught.exception.code, 'UPGRADE_VERIFY_FAILED')

    def test_model_selection_event_must_match_the_original_explicit_buddy(self):
        for index, change in enumerate(('different-harness', 'different-model', 'no-explicit-buddy')):
            with self.subTest(change=change):
                fixture, connection = self.restored(f'false-model-selection-{index}')
                run_id = fixture['stableIds']['delivered']
                if change == 'no-explicit-buddy':
                    spec = json.loads(connection.execute('SELECT micro_task_json FROM workflow_runs WHERE run_id=?',
                                                         (run_id,)).fetchone()[0])
                    for key in ('harness', 'provider', 'model', 'effort'):
                        spec.pop(key)
                    connection.execute('UPDATE workflow_runs SET micro_task_json=? WHERE run_id=?',
                                       (json.dumps(spec), run_id))
                else:
                    seq, raw = connection.execute(
                        "SELECT seq,payload_json FROM events WHERE task_id=? AND kind='workflow.buddy_selected'", (run_id,)).fetchone()
                    payload = json.loads(raw)
                    key, value = (('harness', 'claude' if payload['buddy']['harness'] != 'claude' else 'codex')
                                  if change == 'different-harness' else ('model', 'different-model'))
                    payload['buddy'][key] = value
                    connection.execute('UPDATE events SET payload_json=? WHERE seq=?', (json.dumps(payload), seq))
                connection.commit()
                with self.assertRaises(BoardError) as caught:
                    board_upgrade.migrate(fixture['state'])
                self.assertEqual(caught.exception.code, 'UPGRADE_INVALID_BOARD')
                self.assertEqual(connection.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0], '21')

    def test_started_verification_uses_converted_command_fingerprints_and_rejects_tampering(self):
        fixture, connection = self.restored('started-stop')
        self.insert_stop_receipt(fixture, connection)
        before = upgrade.idle_snapshot(fixture['state'], exact=True)
        board_upgrade.migrate(fixture['state'])
        after = upgrade.idle_snapshot(fixture['state'], event_head=before['eventHead'], exact=True)
        upgrade.verify_migrated_snapshot(before, after)
        target = self.root / 'runtimes' / ('b' * 32)
        health = {'runtimeContentId': target.name, 'runtimeStable': True, 'schemaVersion': SCHEMA_VERSION}
        with mock.patch.object(upgrade, 'command', return_value={'leaks': []}):
            self.assertTrue(upgrade.verify_started(fixture['state'], target, health, after)['retainedDataFingerprints'])
        for command_id in ('install-stop-fixture', 'retained-command'):
            original = connection.execute('SELECT response_json FROM commands WHERE command_id=?', (command_id,)).fetchone()[0]
            connection.execute("UPDATE commands SET response_json='{}' WHERE command_id=?", (command_id,))
            connection.commit()
            with self.subTest(command_id=command_id), mock.patch.object(upgrade, 'command') as command:
                with self.assertRaises(BoardError) as caught:
                    upgrade.verify_started(fixture['state'], target, health, after)
                self.assertEqual(caught.exception.code, 'UPGRADE_VERIFY_FAILED')
                self.assertEqual(caught.exception.details['tables'], ['commands'])
                command.assert_not_called()
            connection.execute('UPDATE commands SET response_json=? WHERE command_id=?', (original, command_id))
            connection.commit()

    def test_macro_stop_receipt_converts_to_the_exact_current_form(self):
        fixture, connection = self.restored()
        stopped = {'runId': 'root-stopped', 'state': 'cancelled', 'status': 'cancelling',
                   'cancelled': True, 'duplicate': False, 'note': 'Stop requested',
                   'shutdownConfirmed': False, 'children': [{'runId': 'child'}]}
        old = {'macroTaskId': 'macro-example', 'runIds': ['root-stopped'],
               'acceptedRunIds': ['root-accepted'], 'concludedRunIds': ['root-concluded'],
               'results': [stopped], 'duplicate': False}
        connection.execute('INSERT INTO commands(command_id,kind,request_hash,response_json,created_at)'
                           ' VALUES(?,?,?,?,?)', ('stop-fixture', 'macro_task.stop', 'hash',
                                                  json.dumps(old), '2026-01-01T00:00:00Z'))
        connection.commit()
        board_upgrade.migrate(fixture['state'])
        result = json.loads(connection.execute(
            "SELECT response_json FROM commands WHERE command_id='stop-fixture'").fetchone()[0])
        self.assertEqual(result, {'macroTaskId': 'macro-example', 'results': [
            {'runId': 'root-stopped', 'state': 'cancelled', 'stopped': True},
            {'runId': 'root-accepted', 'state': 'accepted', 'stopped': False},
            {'runId': 'root-concluded', 'state': 'concluded', 'stopped': False},
        ], 'duplicate': False})

    def test_invalid_macro_stop_receipt_rolls_back_all_upgrade_changes(self):
        cases = (
            {'state': 'unknown'},
            {'runIds': ['different']},
            {'acceptedRunIds': ['root-stopped']},
            {'extra': True},
        )
        for index, change in enumerate(cases):
            with self.subTest(change=change):
                fixture, connection = self.restored(f'invalid-stop-{index}')
                valid = {'macroTaskId': 'macro-example', 'runIds': ['root-stopped'],
                         'acceptedRunIds': [], 'concludedRunIds': [],
                         'results': [{'runId': 'root-stopped', 'state': 'cancelled',
                                      'cancelled': True, 'duplicate': False}], 'duplicate': False}
                invalid = {**valid, **change}
                if 'state' in change:
                    invalid['results'] = [{**valid['results'][0], 'state': change['state']}]
                    del invalid['state']
                for command_id, value in (('a-valid', valid), ('z-invalid', invalid)):
                    connection.execute('INSERT INTO commands(command_id,kind,request_hash,response_json,created_at)'
                                       ' VALUES(?,?,?,?,?)', (command_id, 'macro_task.stop', 'hash',
                                                              json.dumps(value), '2026-01-01T00:00:00Z'))
                connection.commit()
                with self.assertRaises(BoardError) as caught:
                    board_upgrade.migrate(fixture['state'])
                self.assertEqual(caught.exception.code, 'UPGRADE_INVALID_BOARD')
                self.assertEqual(connection.execute(
                    "SELECT value FROM meta WHERE key='schema_version'").fetchone()[0], '21')
                self.assertIsNone(connection.execute(
                    "SELECT name FROM sqlite_master WHERE name='reminder_settings'").fetchone())
                self.assertEqual(json.loads(connection.execute(
                    "SELECT response_json FROM commands WHERE command_id='a-valid'").fetchone()[0]), valid)

    def test_invalid_native_selection_rolls_back_all_upgrade_changes(self):
        changes = (
            ('run', '{"adapter":"command","extra":true}'),
            ('run', '{"adapter":"external"}'),
            ('run', '{"adapter":"unknown"}'),
            ('run', '{"adapter":[]}'),
            ('run', '{"harness":"command","provider":"p","model":"m","effort":"off"}'),
            ('event', '{"source":"original-explicit","buddy":{"adapter":"external"},"buddyRevision":1}'),
            ('event', '{"source":"original-explicit","buddy":{"adapter":"unknown"},"buddyRevision":1}'),
            ('event', '{"source":"original-explicit","buddy":{"harness":"command","provider":"p","model":"m","effort":"off"},"buddyRevision":1}'),
            ('event', '{"source":"original-explicit","buddy":{"adapter":"command"},"buddyRevision":true}'),
            ('event', '{"source":"original-explicit","buddy":{"adapter":"command"},"buddyRevision":1,"extra":true}'),
            ('event-task', None),
            ('original', '{"adapter":"unknown"}'),
            ('original', '{"adapter":[]}'),
        )
        for index, (location, invalid) in enumerate(changes):
            with self.subTest(location=location, invalid=invalid):
                fixture, connection = self.restored(f'invalid-native-{index}')
                native = self.insert_native_selections(fixture, connection)
                run_id = fixture['stableIds']['delivered']
                _, seq, _, _ = native[run_id]
                if location == 'run':
                    connection.execute('UPDATE workflow_runs SET buddy_json=? WHERE run_id=?', (invalid, run_id))
                elif location == 'event-task':
                    connection.execute('UPDATE events SET task_id=NULL WHERE seq=?', (seq,))
                elif location == 'original':
                    connection.execute('UPDATE workflow_runs SET micro_task_json=? WHERE run_id=?', (invalid, run_id))
                else:
                    connection.execute('UPDATE events SET payload_json=? WHERE seq=?', (invalid, seq))
                connection.commit()
                old_run = connection.execute('SELECT buddy_json FROM workflow_runs WHERE run_id=?', (run_id,)).fetchone()[0]
                old_event = tuple(connection.execute('SELECT kind,payload_json FROM events WHERE seq=?', (seq,)).fetchone())
                with self.assertRaises(BoardError) as caught:
                    board_upgrade.migrate(fixture['state'])
                self.assertEqual(caught.exception.code, 'UPGRADE_INVALID_BOARD')
                self.assertEqual(connection.execute(
                    "SELECT value FROM meta WHERE key='schema_version'").fetchone()[0], '21')
                self.assertEqual(connection.execute(
                    'SELECT buddy_json FROM workflow_runs WHERE run_id=?', (run_id,)).fetchone()[0], old_run)
                self.assertEqual(tuple(connection.execute(
                    'SELECT kind,payload_json FROM events WHERE seq=?', (seq,)).fetchone()), old_event)
                self.assertEqual(connection.execute('SELECT task_id FROM events WHERE seq=?', (seq,)).fetchone()[0],
                                 None if location == 'event-task' else run_id)
                self.assertEqual(connection.execute(
                    'SELECT buddy_json FROM workflow_runs WHERE run_id=?',
                    (fixture['stableIds']['accepted'],)).fetchone()[0], native[fixture['stableIds']['accepted']][2])
                self.assertIsNone(connection.execute(
                    "SELECT name FROM sqlite_master WHERE name='reminder_settings'").fetchone())

    def test_native_selection_snapshot_detects_run_and_event_tampering(self):
        changes = (
            ('workflow_runs', 'UPDATE workflow_runs SET buddy_json=? WHERE run_id=?',
             ('{"adapter":"command"}', 'delivered')),
            ('workflow_runs', 'UPDATE workflow_runs SET title=? WHERE run_id=?', ('tampered', 'delivered')),
            ('events', 'UPDATE events SET kind=? WHERE seq=?', ('workflow.buddy_selected', 'event')),
            ('events', 'UPDATE events SET payload_json=? WHERE seq=?', ('{}', 'event')),
        )
        for index, (table, sql, values) in enumerate(changes):
            with self.subTest(table=table, sql=sql):
                fixture, connection = self.restored(f'tampered-native-{index}')
                native = self.insert_native_selections(fixture, connection)
                before = upgrade.idle_snapshot(fixture['state'], exact=True)
                board_upgrade.migrate(fixture['state'])
                after = upgrade.idle_snapshot(fixture['state'], event_head=before['eventHead'], exact=True)
                upgrade.verify_migrated_snapshot(before, after)
                run_id = fixture['stableIds']['delivered']
                event_seq = native[run_id][1]
                connection.execute(sql, (values[0], event_seq if values[1] == 'event' else run_id))
                connection.commit()
                corrupted = upgrade.idle_snapshot(fixture['state'], event_head=before['eventHead'], exact=True)
                with self.assertRaises(BoardError) as caught:
                    upgrade.verify_migrated_snapshot(before, corrupted)
                self.assertEqual(caught.exception.code, 'UPGRADE_VERIFY_FAILED')

    def test_unavailable_submission_digest_refuses_replay_and_new_submissions_replay(self):
        fixture, connection = self.restored()
        # A NULL fingerprint is retained schema-21 stock from the consumed upgrade.
        connection.execute('UPDATE workflow_runs SET request_fingerprint=NULL')
        connection.commit()
        connection.close()
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
                   'task': 'New schema-21 submission', 'cwd': str(repository),
                   'executionWorkspace': {'kind': 'existing', 'access': 'read'}}
        with mock.patch('hey_my_buddy.blackboard.catalog.catalog.validate_buddy', side_effect=lambda buddy, **_: buddy):
            first = store.workflow.submit(current)
            replay = store.workflow.submit(current)
        self.assertIsNotNone(first['requestFingerprint'])
        self.assertTrue(replay['duplicate'])
        self.assertEqual(replay['runId'], first['runId'])
        self.assertEqual(replay['requestFingerprint'], first['requestFingerprint'])
