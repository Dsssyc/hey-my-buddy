"""The fixed schema-28 stock upgrades atomically to the current schema."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest import mock

from blackboard.store.upgrade_fixture import FIXTURE, load_fixture, _git, _sha, _json
from hey_my_buddy.blackboard.store.store import BoardStore
from hey_my_buddy.blackboard.routing import router
from hey_my_buddy.blackboard.store import upgrade as schema_upgrade
from hey_my_buddy.blackboard.store.db import Corruption, Database, SCHEMA_VERSION, UPGRADE_BASE_SCHEMA
from hey_my_buddy.blackboard.tasks import workspace
from hey_my_buddy.blackboard.tasks.workflow import WorkflowCoordinator
from hey_my_buddy.errors import BoardError
from hey_my_buddy.install import upgrade


class UpgradeFixtureTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix='upgrade-base-fixture-'))


    def test_legacy_cleanup_keeps_link_targets_and_current_private_files(self):
        state = self.root / 'state'
        state.mkdir()
        outside = self.root / 'outside'
        (outside / 'goals').mkdir(parents=True)
        survivor = outside / 'goals/native.jsonl'
        survivor.write_bytes(b'outside history')
        harnesses = state / 'harnesses'
        harnesses.mkdir()
        (harnesses / 'codex').symlink_to(outside, target_is_directory=True)
        (state / 'decisions').symlink_to(outside, target_is_directory=True)
        retired = harnesses / 'claude/goals'
        retired.mkdir(parents=True)
        (retired / 'external').symlink_to(outside, target_is_directory=True)
        current = harnesses / 'claude/micro-tasks/current/native.jsonl'
        current.parent.mkdir(parents=True)
        current.write_bytes(b'current private native history')
        (state / 'worker.log').write_bytes(b'retired log')
        result = schema_upgrade.cleanup_legacy_layout(state)
        self.assertFalse(result['complete'])
        self.assertEqual({item['path'] for item in result['failures']}, {'harnesses/codex/goals', 'decisions'})
        self.assertEqual({item['code'] for item in result['failures']}, {'PRIVATE_PATH_UNSAFE'})
        self.assertEqual(survivor.read_bytes(), b'outside history')
        self.assertEqual(current.read_bytes(), b'current private native history')
        self.assertFalse(retired.exists())
        self.assertFalse((state / 'worker.log').exists())


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
                'SELECT spec_json FROM tasks WHERE task_id=?', (run_id,)
            ).fetchone()[0])
            for field in ('harness', 'provider', 'model', 'effort'):
                original.pop(field, None)
            original['adapter'] = adapter
            original_raw = _json(original)
            payload = _json({'source': 'original-explicit', 'adapter': adapter})
            connection.execute('UPDATE tasks SET adapter=? WHERE task_id=?', (adapter, run_id))
            connection.execute('UPDATE attempts SET adapter=? WHERE task_id=?', (adapter, run_id))
            connection.execute('UPDATE tasks SET spec_json=?,input_fingerprint=? WHERE task_id=?',
                               (original_raw, hashlib.sha256(original_raw.encode()).hexdigest(), run_id))
            connection.execute('UPDATE workflow_runs SET buddy_json=NULL WHERE run_id=?', (run_id,))
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
                connection.execute("UPDATE attempts SET result_json=json_set(result_json,'$.result.turn.inputSha256',?)"
                                   " WHERE attempt_id=(SELECT attempt_id FROM workflow_turns WHERE turn_id=?)",
                                   (hashlib.sha256(raw.encode()).hexdigest(), row['turn_id']))
        self.assertTrue(turns)
        connection.commit()
        schema_upgrade.migrate_schema(fixture['state'])
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
        self.assertEqual(fixture['sourceCommit'], 'fd9f04e828ea49a5f2259cdcae0d71401e5c0d84')
        self.assertEqual(fixture['sourceVersion'], '0.51.0')
        self.assertEqual(fixture['schemaVersion'], UPGRADE_BASE_SCHEMA)
        self.assertEqual(connection.execute('PRAGMA integrity_check').fetchone()[0], 'ok')
        self.assertEqual(connection.execute('PRAGMA foreign_key_check').fetchall(), [])
        self.assertEqual(connection.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0],
                         str(UPGRADE_BASE_SCHEMA))
        self.assertEqual({row[0] for row in connection.execute('SELECT key FROM meta')},
                         {'schema_version', 'capability_secret'})
        self.assertEqual(tuple(connection.execute(
            "SELECT author_role,author_id FROM messages ORDER BY rowid LIMIT 1").fetchone()),
            ('console', 'f' * 24))
        self.assertEqual({row[0] for row in connection.execute('SELECT author_role FROM messages')},
                         {None, 'console', 'lead'})
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
                    record = json.loads(artifact['manifest_json'])
                    self.assertEqual(record['manifestSha256'], WorkflowCoordinator.shared_refs_record_digest(record))
        self.assertEqual(connection.execute('SELECT COUNT(*) FROM workflow_host_conclusions').fetchone()[0], 4)
        self.assertEqual({row[0] for row in connection.execute('SELECT enabled FROM buddies')}, {0, 1})
        self.assertEqual({row[0] for row in connection.execute(
            'SELECT mode FROM evaluation_preferences')}, {'pin', 'exclude'})
        self.assertEqual(dict(connection.execute('SELECT run_id,state FROM workflow_runs')),
                         {**{fixture['stableIds'][key]: value for key, value in
                          (('delivered', 'concluded'), ('accepted', 'accepted'),
                           ('failed', 'concluded'), ('cancelled', 'cancelled'))},
                          **{fixture['stockIds'][key]: 'accepted' for key in ('adopted', 'verified', 'helper', 'lead')},
                          fixture['stockIds']['helperParent']: 'concluded',
                          fixture['stockIds']['leadOwned']: 'concluded',
                          **{fixture['stockIds'][key]: 'cancelled' for key in ('scopeParent', 'scopeHelper')},
                          **{entry['runId']: 'failed' for entry in fixture['edgeStock'].values()}})
        self.assertEqual(connection.execute("SELECT COUNT(*) FROM tasks WHERE state IN ('queued','running')").fetchone()[0], 0)
        self.assertEqual({row[0] for row in connection.execute(
            "SELECT json_extract(execution_workspace_json,'$.kind') FROM workflow_runs")}, {'existing', 'worktree'})
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


    def test_base_upgrades_once_and_matches_fresh_structure(self):
        fixture, connection = self.restored()
        state = fixture['state']
        columns = [row['name'] for row in connection.execute('PRAGMA table_info(messages)')]
        self.assertIn('author_role', columns)
        self.assertIn('author_id', columns)
        self.assertNotIn('author', columns)
        before = upgrade.idle_snapshot(state)
        files = {p.relative_to(state): p.read_bytes() for p in state.rglob('*')
                 if p.is_file() and not p.name.startswith('board.sqlite3')}
        self.assertEqual(before['schema'], UPGRADE_BASE_SCHEMA)
        self.assertEqual(upgrade.preflight(state, contract_changed=True)['schema'], UPGRADE_BASE_SCHEMA)
        with self.assertRaises(BoardError) as caught:
            upgrade.preflight(state, contract_changed=False)
        self.assertEqual(caught.exception.code, 'UNSUPPORTED_SCHEMA')
        events = [dict(row) for row in connection.execute('SELECT rowid AS event_rowid,* FROM events ORDER BY seq')]
        # These values come from the base admission writers, independent of the
        # migration SQL: ordinary and helper admissions name their approving
        # Host, while Lead admission carries no source Host.
        expected_hosts = {}
        for event in events:
            payload = json.loads(event['payload_json'])
            if event['kind'] == 'task.submitted' and 'microTask' in payload:
                expected_hosts.setdefault(event['task_id'], payload['microTask'].get('sourceHostId'))
            elif event['kind'] == 'workflow.helper_admitted':
                expected_hosts.setdefault(event['task_id'], payload['sourceHostId'])
        runs = {row[0] for row in connection.execute('SELECT run_id FROM workflow_runs')}
        self.assertEqual(set(expected_hosts), runs)
        self.assertIsNone(expected_hosts[fixture['stockIds']['leadOwned']])
        self.assertEqual(expected_hosts[fixture['stockIds']['helper']], 'fixture-host')
        self.assertGreater(connection.execute('SELECT COUNT(*) FROM workflow_children').fetchone()[0], 0)
        children = [tuple(row) for row in connection.execute('SELECT role,integrator FROM workflow_children')]
        self.assertTrue(all(bool(integrator) == (role == 'integrator') for role, integrator in children))
        activities = dict(connection.execute('SELECT attempt_id,activity_json FROM attempt_activity'))
        activity_events = [event for event in events if event['kind'] == 'attempt.activity']
        self.assertTrue(activity_events)
        self.assertTrue(any(event['kind'] == 'router.claimed' for event in events))
        # fd9f04e8's progress writer emitted exactly one activity field; the
        # replacement event already carried its task/attempt in the columns.
        for event in activity_events:
            self.assertEqual(json.loads(event['payload_json']),
                             {'activity': json.loads(activities[event['attempt_id']])})
            self.assertIsNotNone(event['task_id'])
            self.assertIsNotNone(event['attempt_id'])
        rowids = {name: [row[0] for row in connection.execute(f'SELECT rowid FROM "{name}" ORDER BY rowid')]
                  for name in before['tables']}
        current = Database(state)
        self.addCleanup(current.close)
        with self.assertRaises(Corruption):
            current.initialize()
        schema_upgrade.migrate_schema(state)
        current.initialize()
        current.initialize()
        fresh = Database(self.root / 'fresh')
        fresh.initialize()
        self.addCleanup(fresh.close)
        with fresh.read() as baseline:
            query = 'SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name'
            self.assertEqual([tuple(row) for row in connection.execute(query)],
                             [tuple(row) for row in baseline.execute(query)])
        after = upgrade.idle_snapshot(state, column_projections=before['columnProjections'])
        self.assertEqual(after['schema'], SCHEMA_VERSION)
        self.assertEqual(after['tables'], {**before['tables'],
                                         'router_requests': before['tables']['router_requests'] + 5})
        self.assertNotIn('expectedTableCounts', before)
        for name, fingerprint in before['fingerprints'].items():
            if name != 'meta':
                self.assertEqual(after['fingerprints'][name],
                                 before['expectedFingerprints'].get(name, fingerprint), name)
        self.assertEqual(after['retainedMetaSha256'], before['retainedMetaSha256'])
        self.assertEqual(dict(connection.execute('SELECT run_id,source_host_id FROM workflow_runs')), expected_hosts)
        self.assertEqual({row[1]: row[3] for row in connection.execute('PRAGMA table_info(workflow_runs)')}
                         ['workspace_manifest_json'], 1)
        self.assertNotIn('integrator', {row[1] for row in connection.execute('PRAGMA table_info(workflow_children)')})
        expected = []
        for event in events:
            if event['kind'] == 'attempt.activity':
                event = {**event, 'payload_json': '{}'}
            elif event['kind'] == 'workflow.scope_conflict':
                payload = json.loads(event['payload_json'])
                del payload['paths']
                event = {**event, 'payload_json': _json(payload)}
            expected.append(event)
        self.assertEqual([dict(row) for row in connection.execute('SELECT rowid AS event_rowid,* FROM events ORDER BY seq')],
                         expected)
        for name, original in rowids.items():
            migrated = [row[0] for row in connection.execute(f'SELECT rowid FROM "{name}" ORDER BY rowid')]
            self.assertEqual(migrated[:len(original)], original, name)
            if name != 'router_requests':
                self.assertEqual(migrated, original, name)
            else:
                self.assertEqual(len(migrated), len(original) + 5)
        board = (state / 'board.sqlite3').read_bytes()
        schema_upgrade.migrate_schema(state)
        self.assertEqual((state / 'board.sqlite3').read_bytes(), board)
        self.assertEqual({p.relative_to(state): p.read_bytes() for p in state.rglob('*')
                          if p.is_file() and not p.name.startswith('board.sqlite3')}, files)

    def test_delivery_and_scope_stock_loses_only_the_old_writers_checked_copies(self):
        fixture, connection = self.restored('delivery-and-scope-stock')
        constants = {'extendsDeadline': False, 'requestedDelivery': None, 'startsNewTurn': False, 'supported': True}
        messages = [dict(row) for row in connection.execute('SELECT rowid,* FROM messages ORDER BY rowid')]
        expected_messages = []
        changed = 0
        for row in messages:
            if row['delivery_json'] is not None:
                delivery = json.loads(row['delivery_json'])
                retired = constants.keys() & delivery.keys()
                for key in retired:
                    self.assertIs(type(delivery[key]), type(constants[key]))
                    self.assertEqual(delivery[key], constants[key])
                if retired:
                    self.assertEqual(retired, constants.keys())
                    self.assertIn('admittedDelivery', delivery)
                    self.assertIn('deliveredAt', delivery)
                    self.assertIn('nativeCorrelation', delivery)
                    row = {**row, 'delivery_json': _json({key: value for key, value in delivery.items()
                                                          if key not in constants})}
                    changed += 1
            expected_messages.append(row)
        self.assertGreater(changed, 0)
        conflicts = [dict(row) for row in connection.execute('SELECT rowid,* FROM workflow_workspace_conflicts ORDER BY rowid')]
        evidence = {row['conflict_id']: json.loads(row['evidence_json'])['blockingPaths'] for row in conflicts}
        self.assertTrue(any(len(paths) == 40 for paths in evidence.values()))
        reasons = [dict(row) for row in connection.execute(
            "SELECT rowid,* FROM workflow_requests WHERE reason_code='workspace-scope-violation' ORDER BY rowid")]
        self.assertTrue(any(row['child_task_id'] is not None for row in reasons))
        expected_reasons = []
        for row in reasons:
            params = json.loads(row['reason_params_json'])
            self.assertEqual(set(params), {'conflictIds', 'paths'})
            expected_paths = sorted({path for conflict_id in params['conflictIds'] for path in evidence[conflict_id]})[:32]
            self.assertEqual(params['paths'], expected_paths)
            expected_reasons.append({**row, 'reason_params_json': _json({'conflictIds': params['conflictIds']})})
        old_events = [dict(row) for row in connection.execute(
            "SELECT rowid,* FROM events WHERE kind='workflow.scope_conflict' ORDER BY seq")]
        self.assertEqual(len(old_events), len(conflicts))
        expected_events = []
        for row in old_events:
            payload = json.loads(row['payload_json'])
            self.assertEqual(set(payload), {'conflictIds', 'paths', 'requestId'})
            self.assertEqual(payload['paths'], sorted({path for conflict_id in payload['conflictIds']
                                                      for path in evidence[conflict_id]})[:32])
            expected_events.append({**row, 'payload_json': _json({key: value for key, value in payload.items() if key != 'paths'})})
        schema_upgrade.migrate_schema(fixture['state'])
        self.assertEqual([dict(row) for row in connection.execute('SELECT rowid,* FROM messages ORDER BY rowid')], expected_messages)
        self.assertEqual([dict(row) for row in connection.execute(
            "SELECT rowid,* FROM workflow_requests WHERE reason_code='workspace-scope-violation' ORDER BY rowid")], expected_reasons)
        self.assertEqual([dict(row) for row in connection.execute(
            "SELECT rowid,* FROM events WHERE kind='workflow.scope_conflict' ORDER BY seq")], expected_events)
        self.assertEqual([dict(row) for row in connection.execute(
            'SELECT rowid,* FROM workflow_workspace_conflicts ORDER BY rowid')], conflicts)

    def test_nonconstant_inquiry_delivery_rolls_back_the_upgrade(self):
        fixture, connection = self.restored('nonconstant-delivery')
        message = connection.execute("SELECT message_id,delivery_json FROM messages"
                                     " WHERE json_type(delivery_json,'$.supported') IS NOT NULL LIMIT 1").fetchone()
        original = json.loads(message['delivery_json'])
        for field, value in (('extendsDeadline', True), ('requestedDelivery', 'realtime'),
                             ('startsNewTurn', True), ('supported', False),
                             ('extendsDeadline', 0), ('supported', 1)):
            with self.subTest(field=field, value=value):
                connection.execute('UPDATE messages SET delivery_json=? WHERE message_id=?',
                                   (_json({**original, field: value}), message['message_id']))
                connection.commit()
                before = list(connection.iterdump())
                with self.assertRaises(BoardError) as caught:
                    schema_upgrade.migrate_schema(fixture['state'])
                self.assertEqual(caught.exception.code, 'UPGRADE_INVALID_BOARD')
                self.assertEqual(caught.exception.details['field'], field)
                self.assertEqual(list(connection.iterdump()), before)

    def test_scope_request_proxy_and_event_path_mismatches_roll_back_the_upgrade(self):
        fixture, connection = self.restored('scope-copy-mismatch')
        for table, column, predicate in (
                ('workflow_requests', 'reason_params_json', "reason_code='workspace-scope-violation' AND child_task_id IS NULL"),
                ('workflow_requests', 'reason_params_json', "reason_code='workspace-scope-violation' AND child_task_id IS NOT NULL"),
                ('events', 'payload_json', "kind='workflow.scope_conflict'")):
            row = connection.execute(f'SELECT rowid,{column} FROM {table} WHERE {predicate} LIMIT 1').fetchone()
            for change in ({'paths': ['different.txt']}, {'conflictIds': ['missing-conflict']}):
                with self.subTest(table=table, predicate=predicate, change=change):
                    facts = {**json.loads(row[1]), **change}
                    connection.execute(f'UPDATE {table} SET {column}=? WHERE rowid=?', (_json(facts), row[0]))
                    connection.commit()
                    before = list(connection.iterdump())
                    with self.assertRaises(BoardError) as caught:
                        schema_upgrade.migrate_schema(fixture['state'])
                    self.assertEqual(caught.exception.code, 'UPGRADE_INVALID_BOARD')
                    self.assertEqual(list(connection.iterdump()), before)
                    connection.execute(f'UPDATE {table} SET {column}=? WHERE rowid=?', (row[1], row[0]))
                    connection.commit()

    def test_missing_manifest_rolls_back_the_whole_upgrade(self):
        fixture, connection = self.restored('missing-manifest')
        connection.execute('UPDATE workflow_runs SET workspace_manifest_json=NULL WHERE run_id=?',
                           (fixture['stableIds']['failed'],))
        connection.commit()
        before = (fixture['state'] / 'board.sqlite3').read_bytes()
        with self.assertRaisesRegex(sqlite3.IntegrityError, 'NOT NULL constraint failed'):
            schema_upgrade.migrate_schema(fixture['state'])
        self.assertEqual((fixture['state'] / 'board.sqlite3').read_bytes(), before)

    def test_source_host_backfill_uses_admission_before_first_takeover(self):
        fixture, connection = self.restored('source-host-takeover')
        with_source = fixture['stableIds']['delivered']
        without_source = fixture['stableIds']['failed']
        connection.execute("UPDATE events SET payload_json=json_remove(payload_json,'$.microTask.sourceHostId')"
                           " WHERE task_id=? AND kind='task.submitted'", (without_source,))
        for run_id in (with_source, without_source):
            connection.execute("UPDATE workflow_runs SET host_id='current-host',owner_generation=3 WHERE run_id=?", (run_id,))
            for generation, previous in ((1, 'first-host'), (2, 'later-host')):
                connection.execute("INSERT INTO events(task_id,revision,kind,payload_json,created_at)"
                                   " VALUES(?,1,'workflow.takeover',?,'2026-01-01T00:00:00.000Z')",
                                   (run_id, json.dumps({'previousOwnerGeneration': generation, 'previousHostId': previous})))
        connection.commit()
        schema_upgrade.migrate_schema(fixture['state'])
        actual = dict(connection.execute('SELECT run_id,source_host_id FROM workflow_runs'))
        self.assertEqual(actual[with_source], 'fixture-host')
        self.assertEqual(actual[without_source], 'first-host')
        self.assertIsNone(actual[fixture['stockIds']['leadOwned']])

    def test_started_verification_checks_retained_workflow_columns(self):
        fixture, connection = self.restored('projected-columns')
        before = upgrade.idle_snapshot(fixture['state'])
        schema_upgrade.migrate_schema(fixture['state'])
        target = self.root / 'runtimes' / ('b' * 32)
        health = {'runtimeContentId': target.name, 'runtimeStable': True, 'schemaVersion': SCHEMA_VERSION}
        self.assertTrue(upgrade.verify_started(fixture['state'], target, health, before)['retainedDataFingerprints'])
        for table, column in (('workflow_runs', 'title'), ('workflow_children', 'role'),
                              ('workflow_turns', 'input_json')):
            with self.subTest(table=table):
                original = [tuple(row) for row in connection.execute(f'SELECT rowid,{column} FROM {table}')]
                connection.execute(f"UPDATE {table} SET {column}='changed'")
                connection.commit()
                with self.assertRaises(BoardError) as caught:
                    upgrade.verify_started(fixture['state'], target, health, before)
                self.assertEqual(caught.exception.details['tables'], [table])
                connection.executemany(f'UPDATE {table} SET {column}=? WHERE rowid=?',
                                       [(value, rowid) for rowid, value in original])
                connection.commit()

    def test_invalid_router_json_rolls_back_activity_and_index_changes(self):
        fixture, connection = self.restored()
        connection.execute("UPDATE events SET payload_json='invalid JSON' WHERE kind='router.claimed'")
        connection.commit()
        before = (fixture['state'] / 'board.sqlite3').read_bytes()
        with self.assertRaisesRegex(sqlite3.OperationalError, 'malformed JSON'):
            schema_upgrade.migrate_schema(fixture['state'])
        self.assertEqual((fixture['state'] / 'board.sqlite3').read_bytes(), before)

    def test_foreign_key_failure_rolls_back_the_whole_upgrade(self):
        fixture, connection = self.restored()
        connection.execute("UPDATE attempt_activity SET task_id='missing-task'")
        connection.commit()
        before = (fixture['state'] / 'board.sqlite3').read_bytes()
        with self.assertRaises(BoardError) as caught:
            schema_upgrade.migrate_schema(fixture['state'])
        self.assertEqual(caught.exception.code, 'UPGRADE_INVALID_BOARD')
        self.assertEqual((fixture['state'] / 'board.sqlite3').read_bytes(), before)

    def test_router_evidence_failures_roll_back_without_a_schema_change(self):
        def damage_dispatch(connection):
            row = connection.execute("SELECT d.task_id,d.document_json FROM router_dispatches d "
                                     "JOIN tasks t ON t.task_id=d.task_id "
                                     "WHERE t.request_id='decision:fixture-routing-retry:0:0'").fetchone()
            document = json.loads(row['document_json'])
            document['task'] = 'changed historical task'
            encoded = _json(document)
            connection.execute('UPDATE router_dispatches SET document_json=?,input_sha256=? WHERE task_id=?',
                               (encoded, _sha(encoded.encode()), row['task_id']))

        def damage_decision_task(connection):
            connection.execute("UPDATE evaluation_decisions SET task='changed current task' "
                               "WHERE decision_id=(SELECT decision_id FROM decision_requests "
                               "WHERE request_id='fixture-routing-retry')")

        def damage_outcome_plane(connection):
            connection.execute("UPDATE router_outcomes SET plane='maintenance' WHERE rowid="
                               "(SELECT MIN(rowid) FROM router_outcomes)")

        def remove_snapshot(connection):
            connection.execute("DELETE FROM router_requests WHERE decision_id="
                               "(SELECT decision_id FROM decision_requests "
                               "WHERE request_id='fixture-routing-answered')")

        for name, damage in (('dispatch', damage_dispatch), ('decision task', damage_decision_task),
                             ('outcome plane', damage_outcome_plane), ('missing snapshot', remove_snapshot)):
            with self.subTest(name=name):
                fixture, connection = self.restored(name.replace(' ', '-'))
                damage(connection)
                connection.commit()
                board_path = fixture['state'] / 'board.sqlite3'
                before = board_path.read_bytes()
                with self.assertRaises(BoardError) as caught:
                    schema_upgrade.migrate_schema(fixture['state'])
                self.assertEqual(caught.exception.code, 'UPGRADE_INVALID_BOARD')
                self.assertEqual(board_path.read_bytes(), before)
                self.assertEqual(connection.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0],
                                 str(UPGRADE_BASE_SCHEMA))

    def test_inquiry_stock_covers_the_current_message_forms(self):
        fixture, connection = self.restored('inquiry-stock')
        stock = fixture['inquiryStock']
        self.assertEqual(set(stock), {'journalAnsweredEarlier', 'journalAnsweredQueued',
            'journalAnsweredDelivered', 'journalAnsweredClaimed', 'journalDeliveredOnly',
            'journalNoEntry', 'dbDeliveredNoEntry', 'journalBindingMismatch',
            'missingJournal', 'unavailableOtherReason', 'existingUnavailable', 'leadAuthored'})
        rows = {row['inquiry_id']: dict(row) for row in connection.execute('SELECT * FROM messages')}
        self.assertEqual(set(rows), {item['inquiryId'] for item in stock.values()})

        def row_of(case):
            return rows[stock[case]['inquiryId']]

        answered = ('journalAnsweredEarlier', 'journalAnsweredQueued', 'journalAnsweredDelivered',
                    'journalAnsweredClaimed', 'unavailableOtherReason')
        for case in answered:
            row = row_of(case)
            with self.subTest(case=case):
                self.assertEqual(row['state'], 'answered')
                self.assertIsNone(row['reason_code'])
                answer = json.loads(row['answer_json'])
                self.assertEqual(answer['source'], 'bridge-journal')
                self.assertEqual(answer['via'], 'tool:hey_my_buddy_answer_inquiry')
                self.assertEqual(json.loads(row['delivery_json'])['via'], 'native:in-turn')
        for case, reason in (('journalDeliveredOnly', 'inquiry-delivered-at-attempt-end'),
                             ('dbDeliveredNoEntry', 'inquiry-delivered-at-attempt-end'),
                             ('missingJournal', 'inquiry-delivered-at-attempt-end'),
                             ('journalNoEntry', 'inquiry-queued-at-attempt-end'),
                             ('journalBindingMismatch', 'inquiry-queued-at-attempt-end')):
            row = row_of(case)
            with self.subTest(case=case):
                self.assertEqual(row['state'], 'unavailable')
                self.assertEqual(row['reason_code'], reason)
                self.assertIsNone(row['answer_json'])
        original = row_of('existingUnavailable')
        self.assertEqual((original['state'], original['reason_code'], original['author_role']),
                         ('unavailable', 'inquiry-root-turn-ended', 'console'))
        lead = row_of('leadAuthored')
        self.assertEqual((lead['state'], lead['reason_code'], lead['author_role']),
                         ('unavailable', 'inquiry-queued-at-attempt-end', 'lead'))
        self.assertEqual(lead['author_id'], 'cred-' + stock['leadAuthored']['attemptId'])
        resources = json.loads((FIXTURE / 'workspace-resources.json').read_text(encoding='utf-8'))['files']
        for case, item in stock.items():
            if case in ('existingUnavailable', 'leadAuthored'):
                continue
            with self.subTest(case=case):
                if case == 'missingJournal':
                    self.assertFalse((fixture['state'] / item['journal']).exists())
                    self.assertNotIn(item['journal'], resources)
                else:
                    self.assertTrue((fixture['state'] / item['journal']).is_file())
                    self.assertIn(item['journal'], resources)

    def test_in_progress_deletion_removes_its_commands_events_and_fence(self):
        from hey_my_buddy.blackboard.tasks import macro_task_deletion

        fixture, connection = self.restored()
        schema_upgrade.migrate_schema(fixture['state'])
        store = BoardStore(fixture['state'])
        store.initialize()
        self.addCleanup(store.db.close)
        deletion = connection.execute('SELECT target_id FROM macro_task_deletions').fetchone()
        self.assertIsNotNone(deletion)
        target = deletion['target_id']
        with store.db.read() as db:
            scope = macro_task_deletion._collect(db, target)
        identifiers = {target, *scope.runs, *scope.tasks, *scope.attempts, *scope.decisions}
        self.assertTrue(scope.runs)
        self.assertGreater(connection.execute('SELECT COUNT(*) FROM commands').fetchone()[0], 0)
        with mock.patch.object(macro_task_deletion, '_delete_sessions', return_value=[]):
            result = macro_task_deletion.delete(store, target, len(scope.runs))
        self.assertEqual(result['state'], 'deleted')
        self.assertTrue(result['duplicate'])
        self.assertEqual(result['microTaskCount'], len(scope.runs))
        self.assertEqual(connection.execute('SELECT COUNT(*) FROM macro_task_deletions').fetchone()[0], 0)
        for table in ('commands', 'events'):
            for row in connection.execute(f'SELECT * FROM {table}'):
                text = json.dumps(tuple(row))
                self.assertFalse(any(identifier in text for identifier in identifiers), (table, tuple(row)))
        for operation in (macro_task_deletion.preview, lambda store, target: macro_task_deletion.delete(store, target, 0)):
            with self.assertRaises(BoardError) as caught:
                operation(store, target)
            self.assertEqual(caught.exception.code, 'NOT_FOUND')


    def test_credentials_require_a_real_attempt(self):
        _fixture, connection = self.restored()
        connection.execute('PRAGMA foreign_keys=ON')
        credential = connection.execute('SELECT credential_id FROM agent_credentials LIMIT 1').fetchone()[0]
        for attempt in (None, 'missing-attempt'):
            with self.subTest(attempt=attempt), self.assertRaises(sqlite3.IntegrityError):
                connection.execute('UPDATE agent_credentials SET attempt_id=? WHERE credential_id=?',
                                   (attempt, credential))
        self.assertEqual(connection.execute('PRAGMA foreign_key_check').fetchall(), [])


    def test_unknown_shutdown_stays_unknown_and_blocks_installation(self):
        fixture, connection = self.restored()
        attempt = connection.execute('SELECT selected_attempt_id FROM tasks WHERE task_id=?',
                                     (fixture['stableIds']['accepted'],)).fetchone()[0]
        connection.execute("UPDATE attempts SET execution_state='uncertain',shutdown_confirmed=0 WHERE attempt_id=?", (attempt,))
        connection.commit()
        self.assertEqual(tuple(connection.execute('SELECT execution_state,shutdown_confirmed FROM attempts WHERE attempt_id=?', (attempt,)).fetchone()), ('uncertain', 0))
        with self.assertRaises(BoardError) as caught:
            upgrade.preflight(fixture['state'], contract_changed=True)
        self.assertEqual(caught.exception.code, 'UPGRADE_NOT_IDLE')

    def test_base_contains_current_stored_forms_and_in_progress_deletion(self):
        fixture, connection = self.restored()
        cooling = connection.execute('SELECT harness,provider,consecutive_rejections,cooling_until '
                                     'FROM provider_cooling').fetchone()
        self.assertEqual(tuple(cooling), ('codex', 'openai', 1, None))
        self.assertEqual(connection.execute('SELECT COUNT(*) FROM macro_task_deletions').fetchone()[0], 1)
        for table in ('workflow_runs', 'workflow_children', 'agent_credentials', 'workflow_artifacts',
                      'workflow_suggestions', 'workflow_host_conclusions', 'attempt_activity',
                      'attempt_reminders', 'workflow_routes', 'workflow_continuations', 'workspace_reservations',
                      'attempt_interruptions', 'attempt_shutdown_evidence', 'attempt_process_identities'):
            self.assertGreater(connection.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0], 0, table)
        self.assertEqual({row[0] for row in connection.execute('SELECT kind FROM attempt_reminders')},
                         {'expected-duration', 'native-silence'})
        self.assertEqual({row[0] is None for row in connection.execute('SELECT attempt_id FROM workflow_suggestions')},
                         {True, False})
        self.assertEqual(connection.execute("SELECT COUNT(*) FROM attempt_interruptions WHERE state='settled'").fetchone()[0], 2)
        self.assertEqual(connection.execute("SELECT COUNT(*) FROM workflow_continuations WHERE state='consumed'").fetchone()[0], 1)
        self.assertEqual(connection.execute('SELECT COUNT(*) FROM workflow_workspace_conflicts WHERE delivery_json IS NOT NULL').fetchone()[0], 1)
        self.assertEqual({row[0] for row in connection.execute('SELECT strategy FROM workflow_integrations')},
                         {'patch', 'not-required'})
        self.assertEqual({row[0] for row in connection.execute('SELECT state FROM workspace_cleanup_plans')},
                         {'planned', 'applied', 'blocked'})
        verification = json.loads(connection.execute(
            "SELECT verification_json FROM workflow_integrations WHERE strategy='patch'").fetchone()[0])
        self.assertEqual([item['path'] for item in verification['differingPaths']], ['README.md'])
        self.assertEqual(verification['hostPaths'], ['fixture-host.txt'])
        delivery = json.loads(connection.execute(
            "SELECT delivery_json FROM workflow_workspace_conflicts WHERE state='adopted'").fetchone()[0])
        self.assertEqual(set(delivery), {'resolvedFingerprint', 'deliveredAt'})
        for row in connection.execute('SELECT retention_json,result_json FROM workspace_cleanup_plans'):
            self.assertNotIn('commandReceipts', json.loads(row[0]))
            if row[1] is not None:
                self.assertEqual(set(json.loads(row[1])), {'alreadyRemoved'})
        route = connection.execute('SELECT run_id,state FROM workflow_routes').fetchone()
        self.assertEqual(tuple(route), (fixture['stockIds']['helperParent'], 'resolved'))
        self.assertEqual(connection.execute('PRAGMA foreign_key_check').fetchall(), [])
        inquiry = connection.execute('SELECT reason_code,reason_params_json FROM messages ORDER BY rowid LIMIT 1').fetchone()
        self.assertEqual(tuple(inquiry), ('inquiry-root-turn-ended', '{}'))
        self.assertEqual({row[0] for row in connection.execute('SELECT delegation_authority FROM workflow_runs')},
                         {0, 1})
        self.assertEqual({tuple(row) for row in connection.execute('SELECT mode,note FROM evaluation_preferences')},
                         {('pin', 'fixture pinned buddy'), ('exclude', 'fixture exclusion')})
        self.assertEqual(connection.execute("SELECT COUNT(*) FROM commands WHERE kind='quota.redetect'").fetchone()[0], 2)
        for row in connection.execute('SELECT activity_json FROM attempt_activity'):
            self.assertEqual(json.loads(row[0])['eventSeq'], 3)
        for row in connection.execute('SELECT record_json FROM harness_health'):
            self.assertFalse({'harness', 'status', 'available'} & json.loads(row[0]).keys())

    def test_router_stock_binds_claims_dispatches_and_current_outcomes(self):
        fixture, connection = self.restored()
        self.assertEqual([row['name'] for row in connection.execute('PRAGMA table_info(router_claims)')],
                         ['attempt_id', 'task_id', 'generation'])
        claims = connection.execute('SELECT * FROM router_claims').fetchall()
        self.assertEqual(len(claims), 7)
        for claim in claims:
            attempt = connection.execute('SELECT task_id,generation FROM attempts WHERE attempt_id=?',
                                         (claim['attempt_id'],)).fetchone()
            self.assertEqual(tuple(attempt), (claim['task_id'], claim['generation']))
            dispatch = connection.execute('SELECT document_json,input_sha256 FROM router_dispatches WHERE task_id=?',
                                          (claim['task_id'],)).fetchone()
            self.assertEqual(_sha(dispatch['document_json'].encode()), dispatch['input_sha256'])
        outcomes = connection.execute('SELECT * FROM router_outcomes').fetchall()
        self.assertEqual({(row['plane'], row['outcome']) for row in outcomes},
                         {('routing', 'answered'), ('routing', 'no_answer')})
        for outcome in outcomes:
            event = connection.execute('SELECT kind,payload_json FROM events WHERE seq=?',
                                       (outcome['event_seq'],)).fetchone()
            payload = json.loads(event['payload_json'])
            self.assertEqual(event['kind'], 'router.' + outcome['outcome'])
            self.assertEqual(payload['plane'], 'routing')
            fields = ['routing', outcome['request_id'], payload.get('selectionRetry', False),
                      outcome['router_index'], outcome['phase'], outcome['task_id'],
                      outcome['attempt_id'], outcome['outcome']]
            self.assertEqual(_sha(_json(fields).encode()), outcome['receipt_id'])
        self.assertEqual(connection.execute("SELECT COUNT(*) FROM events WHERE kind='router.claimed'").fetchone()[0], 7)

    def test_retry_rounds_reconstruct_each_historical_dispatch(self):
        fixture, connection = self.restored()
        request = connection.execute("SELECT * FROM decision_requests WHERE request_id='fixture-routing-retry'").fetchone()
        decision_id = request['decision_id']
        first_snapshot = connection.execute('SELECT rowid,* FROM router_requests WHERE decision_id=?',
                                            (decision_id,)).fetchone()
        original_request_count = connection.execute('SELECT COUNT(*) FROM router_requests').fetchone()[0]
        original_max_rowid = connection.execute('SELECT MAX(rowid) FROM router_requests').fetchone()[0]
        original_dispatches = connection.execute(
            'SELECT d.rowid AS dispatch_rowid,d.*,t.request_id AS task_request_id FROM router_dispatches d '
            'JOIN tasks t ON t.task_id=d.task_id WHERE d.decision_id=? ORDER BY t.request_id',
            (decision_id,)).fetchall()
        self.assertEqual(len(original_dispatches), 2)
        self.assertEqual([row['task_request_id'] for row in original_dispatches],
                         ['decision:fixture-routing-retry:0:0', 'decision:fixture-routing-retry:1:0'])
        self.assertEqual(request['task_id'], original_dispatches[1]['task_id'])
        requested = json.loads(request['requested_json'])
        self.assertIs(requested['selectionRetry'], True)
        raw_documents = {row['task_id']: json.loads(row['document_json']) for row in original_dispatches}
        for row in original_dispatches:
            self.assertEqual(_sha(row['document_json'].encode()), row['input_sha256'])
        self.assertNotEqual(raw_documents[original_dispatches[0]['task_id']],
                            raw_documents[original_dispatches[1]['task_id']])
        legacy_excluded = raw_documents[original_dispatches[1]['task_id']]['routingBasis']['excludedBuddies']
        self.assertEqual(legacy_excluded, [{'buddyId': 'codex:openai:fixture-alpha:high',
            'harness': 'codex', 'provider': 'openai', 'model': 'fixture-alpha', 'effort': 'high', 'reason': ''}])
        rerouted = connection.execute("SELECT created_at FROM events WHERE kind='decision.selection_rerouted' "
                                      "AND json_extract(payload_json,'$.decisionId')=?", (decision_id,)).fetchone()
        self.assertIsNotNone(rerouted)
        self.assertEqual(connection.execute('PRAGMA foreign_key_check').fetchall(), [])

        # The expected second round comes from the released writer's stored
        # document and request, before the migration runs. The historical empty
        # reason is the one independently specified normalization in this stock.
        normalized = json.loads(json.dumps(raw_documents[original_dispatches[1]['task_id']]))
        normalized_excluded = normalized['routingBasis']['excludedBuddies'][0]
        normalized_excluded['note'] = None
        del normalized_excluded['reason']
        actor_fields = {'buddy', 'routerBuddyId', 'routerBuddy', 'routerIndex'}
        second_base = {key: value for key, value in normalized.items() if key not in actor_fields}
        facts_fields = ('routerBuddyIds', 'routerIdentities', 'routingMode', 'routingBudget',
                        'routerRetryIntervalSeconds', 'budget')
        second_snapshot = {'baseInput': second_base,
                           'facts': {key: normalized[key] for key in facts_fields},
                           'inspections': requested['selectionRetryInspections']}
        expected_documents = {original_dispatches[0]['task_id']: raw_documents[original_dispatches[0]['task_id']],
                              original_dispatches[1]['task_id']: normalized}

        schema_upgrade.migrate_schema(fixture['state'])
        rounds = connection.execute('SELECT rowid,* FROM router_requests WHERE decision_id=? ORDER BY round',
                                    (decision_id,)).fetchall()
        self.assertEqual([row['round'] for row in rounds], [1, 2])
        self.assertEqual(rounds[0]['rowid'], first_snapshot['rowid'])
        self.assertGreater(rounds[1]['rowid'], original_max_rowid)
        self.assertEqual(connection.execute('SELECT COUNT(*) FROM router_requests').fetchone()[0],
                         original_request_count + 5)
        self.assertEqual(json.loads(rounds[0]['snapshot_json']), json.loads(first_snapshot['snapshot_json']))
        self.assertEqual(json.loads(rounds[1]['snapshot_json']), second_snapshot)
        self.assertEqual(rounds[0]['frozen_at'], first_snapshot['frozen_at'])
        self.assertEqual(rounds[1]['frozen_at'], rerouted['created_at'])
        migrated_dispatches = connection.execute('SELECT rowid,* FROM router_dispatches WHERE decision_id=? '
                                                'ORDER BY round,router_index', (decision_id,)).fetchall()
        self.assertEqual([(row['rowid'], row['round']) for row in migrated_dispatches],
                         [(original_dispatches[0]['dispatch_rowid'], 1),
                          (original_dispatches[1]['dispatch_rowid'], 2)])
        snapshots = {row['round']: json.loads(row['snapshot_json']) for row in rounds}
        for row in migrated_dispatches:
            snapshot = snapshots[row['round']]
            actor = {'harness': row['harness'], 'provider': row['provider'],
                     'model': row['model'], 'effort': row['effort']}
            reconstructed = {**snapshot['baseInput'], **snapshot['facts'],
                             'buddy': actor, 'routerBuddyId': row['buddy_id'],
                             'routerBuddy': actor, 'routerIndex': row['router_index']}
            self.assertEqual(reconstructed, expected_documents[row['task_id']])
        self.assertEqual(connection.execute('PRAGMA foreign_key_check').fetchall(), [])
        for table, removed in (('decision_requests', {'input_json', 'input_sha256'}),
                               ('router_dispatches', {'document_json', 'input_sha256'}),
                               ('evaluation_decisions', {'task'}), ('router_outcomes', {'plane'})):
            self.assertFalse(removed & {row['name'] for row in connection.execute(f'PRAGMA table_info({table})')}, table)

    def test_request_router_facts_move_to_their_frozen_rounds(self):
        fixture, connection = self.restored()
        expected, deleted = {}, set()
        for row in connection.execute('SELECT rowid,decision_id,requested_json FROM decision_requests'):
            request = json.loads(row['requested_json'])
            facts = set()
            for snapshot in connection.execute('SELECT snapshot_json FROM router_requests WHERE decision_id=?',
                                                (row['decision_id'],)):
                facts.update(json.loads(snapshot[0])['facts'])
            if request['routingBasis']['candidateCount'] <= 1:
                # The old writer's no-Router branch omitted a round snapshot.
                facts.update(('routerBuddyId', 'routingMode', 'budget', 'routerCalled'))
            copies = request.keys() & facts
            deleted.update(copies)
            expected[row['decision_id']] = (row['rowid'],
                {key: value for key, value in request.items()
                 if key not in copies and key not in ('selectionRetryInspections', 'routerBudgetSeconds')})
        self.assertEqual(deleted, {'routerBuddyIds', 'routerIdentities', 'routingMode', 'routingBudget',
                                  'routerRetryIntervalSeconds', 'budget', 'routerBuddyId', 'routerCalled'})
        before = upgrade.idle_snapshot(fixture['state'])
        self.assertIn('decision_requests', before['expectedFingerprints'])
        self.assertNotIn('expectedTableCounts', before)
        schema_upgrade.migrate_schema(fixture['state'])
        for row in connection.execute('SELECT rowid,decision_id,requested_json FROM decision_requests'):
            self.assertEqual((row['rowid'], json.loads(row['requested_json'])), expected[row['decision_id']])
        actual = upgrade.idle_snapshot(fixture['state'], column_projections=before['columnProjections'])
        self.assertEqual(actual['fingerprints']['decision_requests'],
                         before['expectedFingerprints']['decision_requests'])
        target = self.root / 'runtimes' / ('b' * 32)
        health = {'runtimeContentId': target.name, 'runtimeStable': True, 'schemaVersion': SCHEMA_VERSION}
        connection.execute("UPDATE decision_requests SET requested_json=json_set(requested_json,'$.task','tampered')"
                           " WHERE rowid=(SELECT MIN(rowid) FROM decision_requests)")
        connection.commit()
        with self.assertRaises(BoardError) as caught:
            upgrade.verify_started(fixture['state'], target, health, before)
        self.assertEqual(caught.exception.code, 'UPGRADE_VERIFY_FAILED')
        self.assertEqual(caught.exception.details['tables'], ['decision_requests'])

    def test_conflicting_request_router_copies_roll_back_the_upgrade(self):
        cases = (('fixture-routing-answered', 'routerRetryIntervalSeconds', 999),
                 ('fixture-routing-retry', 'routerBuddyIds', ['different-router']),
                 ('fixture-routing-answered', 'routerBudgetSeconds', 999),
                 ('fixture-routing-answered', 'selectionRetryInspections', []))
        for number, (request_id, field, value) in enumerate(cases):
            with self.subTest(request=request_id, field=field):
                fixture, connection = self.restored(f'conflicting-router-fact-{number}')
                row = connection.execute('SELECT decision_id,requested_json FROM decision_requests WHERE request_id=?',
                                         (request_id,)).fetchone()
                request = json.loads(row['requested_json'])
                request[field] = value
                connection.execute('UPDATE decision_requests SET requested_json=? WHERE decision_id=?',
                                   (json.dumps(request), row['decision_id']))
                connection.commit()
                before = (fixture['state'] / 'board.sqlite3').read_bytes()
                with self.assertRaises(BoardError) as caught:
                    schema_upgrade.migrate_schema(fixture['state'])
                self.assertEqual(caught.exception.code, 'UPGRADE_INVALID_BOARD')
                self.assertEqual(caught.exception.details['field'], field)
                self.assertEqual((fixture['state'] / 'board.sqlite3').read_bytes(), before)

    def test_task_only_initial_and_retry_rounds_preserve_old_writer_facts(self):
        fixture, connection = self.restored()
        stock = fixture['taskOnlyStock']
        self.assertEqual(set(stock), {'initialZero', 'initialSole', 'retryZero', 'retrySole'})
        old_max = connection.execute('SELECT MAX(rowid) FROM router_requests').fetchone()[0]
        source = {}
        for label, decision_id in stock.items():
            row = connection.execute(
                'SELECT d.task,d.created_at,d.status,r.requested_json,r.task_id,r.input_json '
                'FROM evaluation_decisions d JOIN decision_requests r ON r.decision_id=d.decision_id '
                'WHERE d.decision_id=?', (decision_id,)).fetchone()
            requested = json.loads(row['requested_json'])
            self.assertEqual(requested['task'], row['task'])
            self.assertEqual(requested['routingBasis']['candidateCount'], 1 if label.endswith('Sole') else 0)
            retry = label.startswith('retry')
            self.assertEqual(bool(requested.get('selectionRetry')), retry)
            originals = connection.execute('SELECT rowid,* FROM router_requests WHERE decision_id=?',
                                           (decision_id,)).fetchall()
            dispatches = connection.execute('SELECT rowid,* FROM router_dispatches WHERE decision_id=?',
                                            (decision_id,)).fetchall()
            self.assertEqual(len(originals), int(retry))
            self.assertEqual(len(dispatches), int(retry))
            self.assertNotIn('selectionRetryInspections', requested)
            if label == 'retryZero':
                self.assertEqual(row['task_id'], dispatches[0]['task_id'])
                self.assertEqual(row['input_json'], dispatches[0]['document_json'])
            else:
                self.assertIsNone(row['task_id'])
                self.assertIsNone(row['input_json'])
            rerouted = connection.execute(
                "SELECT created_at FROM events WHERE kind='decision.selection_rerouted' "
                "AND json_extract(payload_json,'$.decisionId')=?", (decision_id,)).fetchall()
            self.assertEqual(len(rerouted), int(retry))
            source[label] = (row['task'], row['created_at'], rerouted[0]['created_at'] if retry else None,
                             [item['rowid'] for item in originals], [item['rowid'] for item in dispatches])

        schema_upgrade.migrate_schema(fixture['state'])
        appended = []
        for label, decision_id in stock.items():
            task, created_at, rerouted_at, original_rows, dispatch_rows = source[label]
            rounds = connection.execute('SELECT rowid,* FROM router_requests WHERE decision_id=? ORDER BY round',
                                        (decision_id,)).fetchall()
            self.assertEqual([row['round'] for row in rounds], [1, 2] if rerouted_at else [1])
            if original_rows:
                self.assertEqual(rounds[0]['rowid'], original_rows[0])
            else:
                appended.append(rounds[0]['rowid'])
            task_round = rounds[-1]
            self.assertEqual(json.loads(task_round['snapshot_json'])['baseInput'], {'task': task})
            self.assertEqual(json.loads(task_round['snapshot_json'])['inspections'], [])
            self.assertEqual(task_round['frozen_at'], rerouted_at or created_at)
            self.assertEqual(json.loads(task_round['snapshot_json'])['facts'],
                             {'routerBuddyId': None, 'routingMode': None, 'budget': None, 'routerCalled': False})
            if original_rows:
                appended.append(task_round['rowid'])
            self.assertEqual([row['rowid'] for row in connection.execute(
                'SELECT rowid FROM router_dispatches WHERE decision_id=?', (decision_id,))], dispatch_rows)
        self.assertEqual(len(set(appended)), 4)
        self.assertTrue(all(rowid > old_max for rowid in appended))
        self.assertEqual(connection.execute('PRAGMA foreign_key_check').fetchall(), [])

    def test_current_file_stock_and_stored_evidence_are_not_empty(self):
        from hey_my_buddy.protocol.execution import read_execution_receipt
        from hey_my_buddy.protocol.session_content import SessionFormat, SessionNotification
        fixture, connection = self.restored()
        references = [entry['params']['receipt'] for entry in fixture['operations']
                      if entry['operation'] == 'execution_result']
        self.assertEqual(len(references), 20)
        for reference in references:
            receipt = read_execution_receipt(Path(reference['path']).parent, sha256=reference['sha256'])
            self.assertIsNotNone(receipt)
            attempt = connection.execute('SELECT task_id,generation FROM attempts WHERE attempt_id=?',
                                         (receipt['attemptId'],)).fetchone()
            self.assertEqual(tuple(attempt), (receipt['taskId'], receipt['generation']))
        sessions = list((fixture['state'] / 'sessions').rglob('*.jsonl'))
        self.assertEqual(len(sessions), 14)
        for path in sessions:
            lines = path.read_text(encoding="utf-8").splitlines()
            format = SessionFormat.model_validate_json(lines[0])
            self.assertEqual((format.protocol_version, format.projection_version), (1, 1))
            notification = SessionNotification.model_validate_json(lines[1])
            self.assertEqual(notification.session_id, path.stem)
        self.assertEqual({row[0] for row in connection.execute(
            "SELECT run_id FROM workflow_artifacts WHERE kind='turn-input'")},
            {fixture['stableIds']['delivered'], fixture['stableIds']['accepted'], fixture['stockIds']['verified'],
             fixture['stockIds']['lead'], fixture['stockIds']['helperParent'], fixture['stockIds']['helper'],
             fixture['stockIds']['scopeParent'],
             fixture['edgeStock']['partial-failed']['runId']})
        outputs = [json.loads(row[0]) for row in connection.execute(
            "SELECT manifest_json FROM workflow_artifacts WHERE kind='output'")]
        self.assertTrue(outputs)
        self.assertTrue(any(output['snapshot']['changedPaths'] == ['fixture.txt'] for output in outputs))
        for output in outputs:
            self.assertEqual(output['snapshot']['version'], 1)
            self.assertEqual(_sha(_json(output['snapshot']).encode()), output['snapshotSha256'])
        conflict = connection.execute('SELECT * FROM workflow_workspace_conflicts').fetchone()
        self.assertEqual(conflict['state'], 'restored')
        evidence = json.loads(conflict['evidence_json'])
        self.assertEqual(evidence['blockingPaths'], ['outside.txt'])
        self.assertEqual([entry['path'] for entry in evidence['entries']], ['outside.txt'])
        for row in connection.execute('SELECT capabilities_json FROM buddies'):
            self.assertEqual(set(json.loads(row[0])), {'execution:codex', 'effort:high', 'input:text'})
        health = json.loads(connection.execute("SELECT record_json FROM harness_health WHERE harness='codex'").fetchone()[0])
        self.assertEqual(connection.execute("SELECT status FROM harness_health WHERE harness='codex'").fetchone()[0], 'ready')
        self.assertFalse({'harness', 'status', 'available'} & health.keys())
        self.assertFalse({'reviewVerification', 'systemSandbox', 'readOnlyStructured'} & health.keys())

    def test_every_other_schema_is_refused_without_writes(self):
        fixture, connection = self.restored()
        for version in ('24', '25', '26', '27', '0', '999', '028', '28.0', '029', '29.0', 'unrecognized'):
            with self.subTest(schema=version):
                connection.execute("UPDATE meta SET value=? WHERE key='schema_version'", (version,))
                connection.commit()
                before = (fixture['state'] / 'board.sqlite3').read_bytes()
                with self.assertRaises(Corruption):
                    Database(fixture['state']).initialize()
                with self.assertRaises(BoardError) as caught:
                    upgrade.idle_snapshot(fixture['state'])
                self.assertEqual(caught.exception.code, 'UNSUPPORTED_SCHEMA')
                with self.assertRaises(BoardError) as caught:
                    schema_upgrade.migrate_schema(fixture['state'])
                self.assertEqual(caught.exception.code, 'UNSUPPORTED_SCHEMA')
                for changed in (False, True):
                    with self.assertRaises(BoardError) as caught:
                        upgrade.preflight(fixture['state'], contract_changed=changed)
                    self.assertEqual(caught.exception.code, 'UNSUPPORTED_SCHEMA')
                self.assertEqual((fixture['state'] / 'board.sqlite3').read_bytes(), before)

    def test_started_verification_rejects_changed_converted_or_retained_events(self):
        fixture, connection = self.restored()
        before = upgrade.idle_snapshot(fixture['state'])
        schema_upgrade.migrate_schema(fixture['state'])
        target = self.root / 'runtimes' / ('b' * 32)
        health = {'runtimeContentId': target.name, 'runtimeStable': True, 'schemaVersion': SCHEMA_VERSION}
        for kind in ('attempt.activity', 'router.claimed', 'workflow.scope_conflict'):
            with self.subTest(kind=kind):
                row = connection.execute('SELECT seq,payload_json FROM events WHERE kind=? LIMIT 1', (kind,)).fetchone()
                connection.execute('UPDATE events SET payload_json=? WHERE seq=?',
                                   (json.dumps({'tampered': True}), row['seq']))
                connection.commit()
                with self.assertRaises(BoardError) as caught:
                    upgrade.verify_started(fixture['state'], target, health, before)
                self.assertEqual(caught.exception.code, 'UPGRADE_VERIFY_FAILED')
                self.assertEqual(caught.exception.details['tables'], ['events'])
                connection.execute('UPDATE events SET payload_json=? WHERE seq=?', (row['payload_json'], row['seq']))
                connection.commit()

    def test_started_verification_checks_the_retained_parts_of_converted_json(self):
        fixture, connection = self.restored('checked-json-retention')
        before = upgrade.idle_snapshot(fixture['state'])
        schema_upgrade.migrate_schema(fixture['state'])
        target = self.root / 'runtimes' / ('b' * 32)
        health = {'runtimeContentId': target.name, 'runtimeStable': True, 'schemaVersion': SCHEMA_VERSION}
        for table, column, predicate in (
                ('messages', 'delivery_json', 'delivery_json IS NOT NULL'),
                ('workflow_requests', 'reason_params_json', "reason_code='workspace-scope-violation'")):
            with self.subTest(table=table):
                row = connection.execute(f'SELECT rowid,{column} FROM {table} WHERE {predicate} LIMIT 1').fetchone()
                facts = {**json.loads(row[1]), 'changedRetainedFact': True}
                connection.execute(f'UPDATE {table} SET {column}=? WHERE rowid=?', (_json(facts), row[0]))
                connection.commit()
                with self.assertRaises(BoardError) as caught:
                    upgrade.verify_started(fixture['state'], target, health, before)
                self.assertEqual(caught.exception.code, 'UPGRADE_VERIFY_FAILED')
                self.assertEqual(caught.exception.details['tables'], [table])
                connection.execute(f'UPDATE {table} SET {column}=? WHERE rowid=?', (row[1], row[0]))
                connection.commit()

    def test_started_verification_rejects_rewritten_command_receipts(self):
        fixture, connection = self.restored()
        before = upgrade.idle_snapshot(fixture['state'])
        schema_upgrade.migrate_schema(fixture['state'])
        target = self.root / 'runtimes' / ('b' * 32)
        health = {'runtimeContentId': target.name, 'runtimeStable': True, 'schemaVersion': SCHEMA_VERSION}
        with mock.patch.object(upgrade, 'command', return_value={'leaks': []}):
            self.assertTrue(upgrade.verify_started(fixture['state'], target, health, before)['retainedDataFingerprints'])
        secret = connection.execute("SELECT value FROM meta WHERE key='capability_secret'").fetchone()[0]
        connection.execute("UPDATE meta SET value=? WHERE key='capability_secret'", ('00' * 32,))
        connection.commit()
        with mock.patch.object(upgrade, 'command') as command:
            with self.assertRaises(BoardError) as caught:
                upgrade.verify_started(fixture['state'], target, health, before)
            self.assertEqual(caught.exception.code, 'UPGRADE_VERIFY_FAILED')
            self.assertEqual(caught.exception.details['tables'], ['meta'])
            command.assert_not_called()
        connection.execute("UPDATE meta SET value=? WHERE key='capability_secret'", (secret,))
        connection.commit()
        command_id = connection.execute("SELECT command_id FROM commands WHERE response_json!='{}' LIMIT 1").fetchone()[0]
        connection.execute("UPDATE commands SET response_json='{}' WHERE command_id=?", (command_id,))
        connection.commit()
        with mock.patch.object(upgrade, 'command') as command:
            with self.assertRaises(BoardError) as caught:
                upgrade.verify_started(fixture['state'], target, health, before)
            self.assertEqual(caught.exception.code, 'UPGRADE_VERIFY_FAILED')
            self.assertEqual(caught.exception.details['tables'], ['commands'])
            command.assert_not_called()
