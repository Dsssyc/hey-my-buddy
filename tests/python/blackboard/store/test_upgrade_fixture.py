"""The installed schema-30 stock upgrades explicitly without changing its evidence."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest import mock

from blackboard.store.upgrade_fixture import FIXTURE, FIXTURE_ROOT_TOKEN, load_fixture, load_delivered_stock, complete_fixture_cleanup, _git, _sha, _json
from hey_my_buddy.blackboard.store.store import BoardStore
from hey_my_buddy.blackboard.routing import router_sequence
from hey_my_buddy.blackboard.store.db import Corruption, Database, SCHEMA_VERSION
from hey_my_buddy.blackboard.store import upgrade as schema_upgrade
from hey_my_buddy.blackboard.store.upgrade import UPGRADE_FLOOR_SCHEMA, upgrade_state
from hey_my_buddy.blackboard.tasks import workspace
from hey_my_buddy.blackboard.tasks.workflow import WorkflowCoordinator
from hey_my_buddy.errors import BoardError
from hey_my_buddy.install import upgrade
from hey_my_buddy.paths import native_path


class UpgradeFixtureTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix='upgrade-floor-fixture-'))


    def restored(self, name='state', *, migrate=True):
        fixture = load_fixture(self.root / name)
        if migrate:
            upgrade_state(fixture['state'])
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

    def test_floor_created_descriptions_become_goals_with_original_event_times(self):
        fixture = load_fixture(self.root / 'goal-stock')
        load_delivered_stock(fixture)
        # The released writer normalized the submitted whitespace before it
        # stored the event; the migration preserves that stored value.
        expected_body = 'Goal from the 0.54.1 writer. Preserve this line.'
        with sqlite3.connect(fixture['state'] / 'board.sqlite3') as connection:
            connection.row_factory = sqlite3.Row
            source_events = list(connection.execute(
                "SELECT payload_json,created_at FROM events WHERE kind='workflow.macro_task_created' ORDER BY seq"))
            delivered_macro = connection.execute(
                'SELECT macro_task_id FROM workflow_runs WHERE run_id=?',
                (fixture['deliveredStockRunId'],)).fetchone()[0]
            # Distinguish the retained event time from the independent display
            # row's time; the floor writer's event and sealed inputs stay intact.
            connection.execute('UPDATE macro_tasks SET created_at=? WHERE macro_task_id=?',
                               ('2026-01-05T00:00:01.000Z', delivered_macro))
            source_macros = [tuple(row) for row in connection.execute(
                'SELECT macro_task_id,title,project_id,source_host_id,created_at,activity_seq,activity_at '
                'FROM macro_tasks ORDER BY rowid')]
            paths = list(connection.execute(
                "SELECT m.project_path,(SELECT json_extract(t.spec_json,'$.cwd')"
                " FROM workflow_runs r JOIN tasks t ON t.task_id=r.run_id"
                " WHERE r.macro_task_id=m.macro_task_id ORDER BY r.created_at,r.rowid LIMIT 1) AS source_cwd"
                " FROM macro_tasks m"))
            self.assertEqual(len(paths), 3)
            for row in paths:
                self.assertEqual(row['project_path'], row['source_cwd'])
        self.assertEqual(len(source_events), 3)
        descriptions = {json.loads(row['payload_json'])['macroTaskId']:
                        (json.loads(row['payload_json'])['description'], row['created_at'])
                        for row in source_events}
        self.assertEqual(descriptions[delivered_macro], (expected_body, '2026-01-05T00:00:00.000Z'))
        self.assertEqual([body for macro_id, (body, _at) in descriptions.items()
                          if macro_id != delivered_macro], [None, None])

        upgrade_state(fixture['state'])
        with sqlite3.connect(fixture['state'] / 'board.sqlite3') as connection:
            self.assertEqual(connection.execute(
                "SELECT COUNT(*) FROM events WHERE kind='workflow.macro_task_created'").fetchone()[0], 0)
            self.assertEqual(list(connection.execute(
                'SELECT macro_task_id,revision,body,created_at FROM macro_task_goals')),
                [(delivered_macro, 1, expected_body, '2026-01-05T00:00:00.000Z')])
            self.assertEqual([tuple(row) for row in connection.execute(
                'SELECT macro_task_id,title,project_id,source_host_id,created_at,activity_seq,activity_at '
                'FROM macro_tasks ORDER BY rowid')], source_macros)
            self.assertNotIn('project_path', {row[1] for row in connection.execute('PRAGMA table_info(macro_tasks)')})

    def test_delivered_v3_stock_continues_and_is_accepted_after_upgrade(self):
        from admission import admit_board
        from support import FakeClock, InProcessBoard

        fixture = load_fixture(self.root / 'delivered-v3')
        load_delivered_stock(fixture)
        run_id = fixture['deliveredStockRunId']
        with sqlite3.connect(fixture['state'] / 'board.sqlite3') as connection:
            raw, state = connection.execute('SELECT workspace_manifest_json,state FROM workflow_runs WHERE run_id=?',
                                            (run_id,)).fetchone()
            self.assertEqual(state, 'delivered')
            self.assertEqual(json.loads(raw)['version'], 3)
        upgrade_state(fixture['state'])
        board = InProcessBoard(fixture['state'], clock=FakeClock('2026-01-05T00:10:00.000Z'))
        self.addCleanup(board.close)
        view = board.call('workflow_get', {'runId': run_id})
        self.assertEqual(view['state'], 'delivered')
        old_manifest = json.loads(raw)
        self.assertTrue(workspace.verify(old_manifest, state_dir=fixture['state'])['valid'])
        board.call('workflow_continue', {
            'runId': run_id, 'commandId': 'schema33-v3-continue',
            'expectedRevision': view['revision'], 'input': 'Finish the read-only review',
            **fixture['deliveredStockControl'],
        })
        board.store.workflow.prepare_continuation_workspace({'runId': run_id})
        nonce = 'schema33-fixture-nonce'
        claim = admit_board(board, {'nonce': nonce, 'runId': run_id})['claim']
        self.assertIsNotNone(claim)
        attempt, turn = claim['attempt'], claim['turn']
        manifest = turn['input']['executionWorkspace']
        seal = workspace.seal(fixture['state'], manifest, run_id, attempt['attemptId'])
        session_id = (turn['input']['previousSessionId'] if turn['resumeMode'] == 'native-session'
                      else 'fixture-schema33-continued')
        record = {
            'version': 1, 'taskId': run_id, 'attemptId': attempt['attemptId'],
            'generation': attempt['generation'], 'turnId': turn['turnId'],
            'resumeMode': turn['resumeMode'],
            'previousSessionId': turn['input']['previousSessionId'],
            'sessionId': session_id, 'promptSha256': 'a' * 64,
            'inputSha256': turn['inputSha256'],
            'outcome': {'disposition': 'completed', 'summary': 'Review completed',
                        'remaining': [], 'decisions': [], 'artifacts': [], 'request': None},
            'provenance': {'harness': 'codex', 'turnEnd': 'completed',
                           'outputSchemaValidated': True, 'finalMessageCompleted': True,
                           'nativeTurnStarted': True, 'nativeTurnCompleted': True,
                           'nativeThreadId': session_id,
                           'nativeTurnId': 'fixture-schema33-native-turn',
                           'finalItemId': 'fixture-item', 'eventSeq': 2},
        }
        board.call('execution_result', {
            'attemptId': attempt['attemptId'], 'generation': attempt['generation'], 'nonce': nonce,
            'status': 'ok', 'exitCode': 0, 'shutdownConfirmed': True,
            'result': {'status': 'ok', 'workspaceManifest': manifest, 'turn': record,
                       'workspaceSeal': seal, 'processState': {'shutdownConfirmed': True}, 'fixture': True},
        })
        delivered = board.call('workflow_get', {'runId': run_id})
        self.assertEqual(delivered['state'], 'delivered')
        target_repository = fixture['fixtureRoot'] / 'repository'
        _git(target_repository, 'checkout', '-b', 'fixture-schema33-integration')
        target = {'path': str(target_repository), 'ref': 'refs/heads/fixture-schema33-integration'}
        verified = workspace.integration_verify(
            seal, original_input=old_manifest, final_input=manifest, **target)
        self.assertTrue(verified['verified'])
        accepted = board.call('workflow_accept', {
            'runId': run_id, 'artifactId': delivered['finalArtifactId'],
            'target': target,
            'note': 'Accepted after the v3 continuation',
            **fixture['deliveredStockControl'],
        })
        self.assertEqual(accepted['state'], 'accepted')
        self.assertEqual(accepted['integration']['state'], 'verified')


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
                connection.execute('UPDATE workflow_turns SET input_json=? WHERE turn_id=?',
                                   (raw, row['turn_id']))
                connection.execute("UPDATE attempts SET result_json=json_set(result_json,'$.result.turn.inputSha256',?)"
                                   " WHERE attempt_id=(SELECT attempt_id FROM workflow_turns WHERE turn_id=?)",
                                   (hashlib.sha256(raw.encode()).hexdigest(), row['turn_id']))
        self.assertTrue(turns)
        connection.commit()
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
        fixture, connection = self.restored(migrate=False)
        self.assertEqual(fixture['sourceCommit'], '4de1dd9478980e924a4a86b2c50aaca33e33b79d')
        self.assertEqual(fixture['sourceVersion'], '0.54.1')
        self.assertEqual(fixture['schemaVersion'], UPGRADE_FLOOR_SCHEMA)
        self.assertEqual(connection.execute('PRAGMA integrity_check').fetchone()[0], 'ok')
        self.assertEqual(connection.execute('PRAGMA foreign_key_check').fetchall(), [])
        self.assertEqual(connection.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0],
                         str(UPGRADE_FLOOR_SCHEMA))
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
        for row in connection.execute("SELECT w.input_json,a.result_json FROM workflow_turns w"
                                      " JOIN attempts a ON a.attempt_id=w.attempt_id WHERE w.state='returned'"):
            self.assertEqual(_sha(row[0].encode()), json.loads(row[1])['result']['turn']['inputSha256'])
        sealed_manifests = []
        for _, run_id in fixture['stableIds'].items():
            row = connection.execute('SELECT workspace_manifest_json FROM workflow_runs WHERE run_id=?', (run_id,)).fetchone()
            manifest = json.loads(row[0])
            sealed_manifests.append(manifest)
            for artifact in connection.execute('SELECT * FROM workflow_artifacts WHERE run_id=?', (run_id,)):
                if artifact['kind'] == 'output':
                    output = json.loads(artifact['manifest_json'])
                    self.assertTrue(output['ref'].startswith('refs/hey-my-buddy/workspaces/'))
                    self.assertEqual(_git(Path(manifest['snapshot']['repositoryPath']), 'rev-parse', output['ref']), output['commit'])
                    self.assertEqual(_git(Path(manifest['snapshot']['repositoryPath']), 'rev-parse', output['commit'] + '^{tree}'), output['tree'])
                    self.assertEqual(_sha(native_path(Path(output['diffPath'])).read_bytes()), output['diffSha256'])
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
        self.assertEqual([row[0] for row in connection.execute('SELECT buddy_id FROM router_buddies ORDER BY position')], fixture['routerBuddyIds'])
        self.assertEqual(tuple(connection.execute(
            'SELECT default_mode,budget_preset,retry_interval_seconds FROM router_settings').fetchone()),
            ('review', 'deep', 47))
        for table in ('buddies', 'evaluation_preferences', 'model_service_notes',
                      'model_service_concurrency', 'harness_health'):
            self.assertGreater(connection.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0], 0, table)
        upgrade_state(fixture['state'])
        for manifest in sealed_manifests:
            workspace.verify(manifest, state_dir=fixture['state'])


    def test_host_seal_replays_without_overwriting_worker_stock(self):
        fixture, connection = self.restored()
        run_id, manifest, attempt = self.sample(fixture, connection)
        output = json.loads(connection.execute("SELECT manifest_json FROM workflow_artifacts WHERE run_id=? AND kind='output'", (run_id,)).fetchone()[0])
        worker_file = Path(output['diffPath']).with_name('output.json')
        before = native_path(worker_file).read_bytes()
        sealed = workspace.host_seal(fixture['state'], manifest, run_id, attempt)
        self.assertEqual(sealed['kind'], 'host-output')
        self.assertNotEqual(Path(sealed['diffPath']).parent, worker_file.parent)
        self.assertEqual(workspace.host_seal(fixture['state'], manifest, run_id, attempt), sealed)
        self.assertEqual(native_path(worker_file).read_bytes(), before)


    def test_portable_roots_rebind_stable_identities_without_changing_native_history(self):
        first, c1 = self.restored('first state')
        name = '第二个 “私有” ' + FIXTURE_ROOT_TOKEN + ' state'
        second, c2 = self.restored(name)
        _, m1, _ = self.sample(first, c1)
        _, m2, _ = self.sample(second, c2)
        self.assertNotEqual(m1['workspaceId'], m2['workspaceId'])
        self.assertNotEqual(m1['repositoryId'], m2['repositoryId'])
        self.assertNotEqual(m1['checkoutId'], m2['checkoutId'])
        self.assertNotEqual(m1['manifestSha256'], m2['manifestSha256'])
        workspace.verify(m1, state_dir=first['state'])
        workspace.verify(m2, state_dir=second['state'])
        for field in ('outcome', 'provenance', 'promptSha256'):
            query = f"SELECT json_extract(result_json,'$.result.turn.{field}') FROM attempts ORDER BY attempt_id"
            self.assertEqual([tuple(row) for row in c1.execute(query)], [tuple(row) for row in c2.execute(query)])
        with self.assertRaises(ValueError):
            load_fixture(first['state'])


    def test_floor_requires_explicit_upgrade_and_matches_fresh_structure(self):
        fixture, connection = self.restored(migrate=False)
        state = fixture['state']
        complete_fixture_cleanup(connection)
        before = upgrade.idle_snapshot(state)
        old_rows = {name: [tuple(row) for row in connection.execute(f'SELECT rowid,* FROM "{name}" ORDER BY rowid')]
                    for name in before['tables']}
        macro_columns = ['rowid', *[row[1] for row in connection.execute('PRAGMA table_info(macro_tasks)')]]
        task_columns = ['rowid', *[row[1] for row in connection.execute('PRAGMA table_info(tasks)')]]
        identity_columns = ['rowid', *[row[1] for row in connection.execute('PRAGMA table_info(attempt_process_identities)')]]
        cleanup_columns = ['rowid', *[row[1] for row in connection.execute('PRAGMA table_info(workspace_cleanup_plans)')]]
        reservation_columns = ['rowid', *[row[1] for row in connection.execute('PRAGMA table_info(workspace_reservations)')]]
        integration_columns = ['rowid', *[row[1] for row in connection.execute('PRAGMA table_info(workflow_integrations)')]]
        sealed_roots = {}
        for (raw,) in connection.execute('SELECT workspace_manifest_json FROM workflow_runs'):
            manifest = json.loads(raw)
            sealed_roots[manifest['workspaceId'], manifest['manifestSha256']] = manifest['checkoutRoot']
        for (raw,) in connection.execute("SELECT manifest_json FROM workflow_artifacts WHERE kind IN ('input','turn-input')"):
            manifest = json.loads(raw)
            sealed_roots[manifest['workspaceId'], manifest['manifestSha256']] = manifest['checkoutRoot']
        releases = dict(connection.execute(
            "SELECT w.reservation_id,h.created_at FROM workspace_reservations w"
            " JOIN workflow_host_conclusions h ON h.run_id=w.holder_task_id"
            " JOIN workflow_runs r ON r.run_id=h.run_id WHERE w.state='held' AND r.state='concluded'"))
        self.assertTrue(releases)
        deleted_plans = {row['plan_id'] for row in connection.execute(
            "SELECT * FROM workspace_cleanup_plans WHERE state IN ('planned','blocked')")}
        retained_commands = {row['command_id'] for row in connection.execute(
            "SELECT command_id FROM commands WHERE kind NOT IN ('workflow.cleanup_plan','workflow.cleanup_apply')")}
        retained_events = {row['seq'] for row in connection.execute('SELECT * FROM events')
                           if row['kind'] != 'workflow.macro_task_created'
                           and json.loads(row['payload_json']).get('planId') not in deleted_plans}
        old_project_id = json.loads(connection.execute(
            'SELECT workspace_manifest_json FROM workflow_runs LIMIT 1').fetchone()[0])['repositoryId']
        marker_blob = hashlib.sha1(b'blob ' + str(len(old_project_id)).encode() + b'\0'
                                   + old_project_id.encode()).hexdigest()
        git_root = Path('fixture-root/repository/.git')
        project_files = {git_root / 'refs/hey-my-buddy/project',
                         git_root / 'objects' / marker_blob[:2] / marker_blob[2:]}

        def state_files():
            return {p.relative_to(native_path(state)): p.read_bytes() for p in native_path(state).rglob('*')
                    if p.is_file() and not p.name.startswith('board.sqlite3')
                    and p.relative_to(native_path(state)) not in project_files}

        files = state_files()
        self.assertEqual(before['schema'], UPGRADE_FLOOR_SCHEMA)
        with self.assertRaises(Corruption):
            Database(state).initialize()
        self.assertEqual(upgrade.preflight(state, contract_changed=True)['schema'], UPGRADE_FLOOR_SCHEMA)
        with self.assertRaises(BoardError) as caught:
            upgrade.preflight(state, contract_changed=False)
        self.assertEqual(caught.exception.code, 'UNSUPPORTED_SCHEMA')
        self.assertEqual(upgrade_state(state), {'sourceSchema': UPGRADE_FLOOR_SCHEMA, 'targetSchema': SCHEMA_VERSION})
        self.assertEqual(upgrade.idle_snapshot(state)['schema'], SCHEMA_VERSION)
        for changed in (False, True):
            self.assertEqual(upgrade.preflight(state, contract_changed=changed)['schema'], SCHEMA_VERSION)
        current = Database(state)
        self.addCleanup(current.close)
        current.initialize()
        current.initialize()
        fresh = Database(self.root / 'fresh')
        self.addCleanup(fresh.close)
        fresh.initialize()
        with fresh.read() as baseline:
            query = 'SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name'
            self.assertEqual([tuple(row) for row in connection.execute(query)],
                             [tuple(row) for row in baseline.execute(query)])
        after = upgrade.idle_snapshot(state)
        for name, original in old_rows.items():
            actual = [tuple(row) for row in connection.execute(f'SELECT rowid,* FROM "{name}" ORDER BY rowid')]
            if name == 'meta':
                self.assertEqual([(row[1], row[2]) for row in actual if row[1] != 'schema_version'],
                                 [(row[1], row[2]) for row in original if row[1] != 'schema_version'])
            elif name == 'macro_tasks':
                keep = [i for i, column in enumerate(macro_columns) if column != 'project_path']
                self.assertEqual(actual, [tuple(row[i] for i in keep) for row in original])
            elif name == 'tasks':
                removed = {'required_capabilities', 'exclusive_resources', 'expected_seconds', 'router_budget_seconds'}
                keep = [i for i, column in enumerate(task_columns) if column not in removed]
                self.assertEqual(actual, [tuple(row[i] for i in keep) for row in original])
            elif name == 'attempt_process_identities':
                keep = [i for i, column in enumerate(identity_columns) if column != 'process_group_id']
                self.assertEqual(actual, [tuple(row[i] for i in keep) for row in original])
            elif name == 'workspace_cleanup_plans':
                expected = []
                for row in original:
                    value = dict(zip(cleanup_columns, row))
                    if value['plan_id'] in deleted_plans:
                        continue
                    evidence = json.loads(value['evidence_json'])
                    workspace = evidence['workspace']
                    allocation = workspace['allocation']
                    self.assertEqual(set(evidence), {'acceptance', 'attemptId', 'finalArtifactId',
                                                     'hostConclusion', 'integrationId', 'ownerGeneration',
                                                     'shutdown', 'workspace'})
                    self.assertEqual(set(workspace), {'allocation', 'attachedBranch', 'checkoutId',
                                                      'eligible', 'kind', 'locked', 'manifestWorkspaceId',
                                                      'reasons', 'repositoryId', 'sealedObservation',
                                                      'unsealedPaths', 'workspaceId', 'worktree'})
                    self.assertEqual(set(allocation), {'checkoutId', 'kind', 'manifestSha256',
                                                       'manifestWorkspaceId', 'path', 'repositoryId', 'workspaceId'})
                    self.assertEqual(set(json.loads(value['result_json'])), {'alreadyRemoved'})
                    value['evidence_json'] = {
                        **{key: evidence[key] for key in ('acceptance', 'attemptId', 'finalArtifactId',
                                                          'hostConclusion', 'integrationId', 'ownerGeneration',
                                                          'shutdown')},
                        'workspace': {
                            **{key: workspace[key] for key in ('attachedBranch', 'eligible', 'kind', 'locked',
                                                                'manifestWorkspaceId', 'reasons',
                                                                'sealedObservation', 'unsealedPaths',
                                                                'workspaceId', 'worktree')},
                            'projectId': workspace['repositoryId'],
                            'allocation': {
                                **{key: allocation[key] for key in ('kind', 'manifestSha256',
                                                                     'manifestWorkspaceId', 'path', 'workspaceId')},
                                'projectId': allocation['repositoryId'],
                            },
                        },
                    }
                    for removed in ('checkout_id', 'repository_id', 'expires_at', 'command_id'):
                        value.pop(removed)
                    expected.append(value)
                after_columns = ['rowid', *[column[1] for column in connection.execute('PRAGMA table_info(workspace_cleanup_plans)')]]
                for row, value in zip(actual, expected, strict=True):
                    observed = dict(zip(after_columns, row))
                    observed['evidence_json'] = json.loads(observed['evidence_json'])
                    self.assertEqual(observed, value)
            elif name == 'workspace_reservations':
                expected = []
                for row in original:
                    value = dict(zip(reservation_columns, row))
                    if value['reservation_id'] in releases:
                        value.update(state='released', released_at=releases[value['reservation_id']])
                    value['checkout_root'] = sealed_roots[value['workspace_id'], value['manifest_sha256']]
                    for removed in ('checkout_id', 'repository_id', 'path'):
                        value.pop(removed)
                    expected.append(value)
                after_columns = ['rowid', *[column[1] for column in connection.execute('PRAGMA table_info(workspace_reservations)')]]
                self.assertEqual([dict(zip(after_columns, row)) for row in actual], expected)
            elif name == 'workflow_integrations':
                expected = []
                for row in original:
                    value = dict(zip(integration_columns, row))
                    value.pop('target_repository_id')
                    value['target_project_id'] = None
                    value.pop('target_checkout_id')
                    expected.append(value)
                after_columns = ['rowid', *[column[1] for column in connection.execute('PRAGMA table_info(workflow_integrations)')]]
                self.assertEqual([dict(zip(after_columns, row)) for row in actual], expected)
            elif name == 'commands':
                self.assertEqual(actual, [row for row in original if row[1] in retained_commands])
            elif name == 'events':
                self.assertEqual(actual, [row for row in original if row[1] in retained_events])
            elif name == 'catalog_observations':
                self.assertEqual(len(actual), len(original))
                for old, new in zip(original, actual):
                    self.assertEqual(new[:3] + new[4:], old[:3] + old[4:])
                    if old[3] is None:
                        self.assertIsNone(new[3])
                        continue
                    expected = json.loads(old[3])
                    expected['discoveredAt'] = expected['catalog']['discoveredAt']
                    del expected['catalog'], expected['buddies']
                    self.assertEqual(json.loads(new[3]), expected)
            elif name == 'router_settings':
                self.assertEqual([row[:len(original[0])] for row in actual], original)
                self.assertEqual(actual[0][-2:], (None, 3600))
            else:
                self.assertEqual(actual, original, name)
                self.assertEqual(after['fingerprints'][name], before['fingerprints'][name], name)
        for name in ('maintenance_runs', 'domain_nodes', 'micro_task_classifications',
                     'kind_summaries', 'kind_summary_evidence', 'routing_judgments'):
            self.assertEqual(after['tables'][name], 0, name)
        self.assertNotIn('kind_summary_citations', after['tables'])
        with self.assertRaises(BoardError) as caught:
            upgrade_state(state)
        self.assertEqual(caught.exception.code, 'UNSUPPORTED_SCHEMA')
        self.assertEqual(upgrade.idle_snapshot(state), after)
        self.assertEqual(state_files(), files)

    def test_retained_catalog_is_normalized_for_refresh_and_console_without_rewriting_evidence(self):
        from console.test_console import Browser
        from support import FakeClock, InProcessBoard
        from hey_my_buddy.blackboard.catalog import catalog, catalog_store

        fixture, connection = self.restored('retained-catalog')
        original = dict(connection.execute('SELECT discovery_id,CAST(payload_json AS BLOB) FROM evaluation_catalog'))
        self.assertEqual(len(original), 3)
        for raw in original.values():
            value = json.loads(raw)
            self.assertIn('providerVersion', value)
            for provider in value['providers']:
                self.assertTrue({'packageName', 'packageVersion'} <= provider.keys())
        legacy_id = connection.execute("SELECT discovery_id FROM catalog_current WHERE harness='codex'").fetchone()[0]
        pending = [tuple(row) for row in connection.execute('SELECT * FROM catalog_pending ORDER BY model')]
        sources = [tuple(row) for row in connection.execute('SELECT buddy_id,source FROM buddies ORDER BY buddy_id')]
        board = InProcessBoard(fixture['state'], clock=FakeClock('2026-01-01T00:03:00.000Z'))
        self.addCleanup(board.close)

        def assert_current_form(value):
            if isinstance(value, dict):
                self.assertFalse({'packageName', 'packageVersion', 'providerVersion'} & value.keys())
                children = value.values()
            elif isinstance(value, list):
                children = value
            else:
                return
            for child in children:
                assert_current_form(child)

        def current():
            with board.store.db.read() as db:
                view = catalog_store.current(db)
                self.assertEqual(db.execute("SELECT discovery_id FROM catalog_current WHERE harness='codex'").fetchone()[0], legacy_id)
            metadata = view.metadata()
            self.assertEqual([model['id'] for provider in metadata['providers'] for model in provider['models']], ['fixture-alpha'])
            assert_current_form(metadata)

        current()
        request_id = connection.execute('SELECT request_id FROM catalog_observations WHERE observation_id=3').fetchone()[0]
        replay = board.call('model_catalog_refresh', {'requestId': request_id})
        self.assertTrue(replay['duplicate'])
        self.assertEqual(replay['discoveryId'], legacy_id)
        normalized = catalog.canonical_payload(json.loads(original[legacy_id]))
        self.assertEqual(replay['catalog']['providers'], normalized['providers'])
        self.assertEqual(replay['buddies'], catalog.CatalogView(normalized).proposed_buddies())
        assert_current_form(replay)
        unknown = {'source': 'fixture:native-unknown', 'providers': [], 'discoveries': [
            {'harness': 'codex', 'status': 'unknown', 'accountStatus': 'unknown',
             'reason': {'code': 'model-list-shape-unreadable', 'params': {}}}]}
        with mock.patch('hey_my_buddy.buddy.harnesses.registry.adapter') as native:
            native.return_value.discover_models.return_value = unknown
            board.service._refresh_harness_catalog('codex', board.service.harnesses.get('codex'))
            native.return_value.discover_models.assert_called_once_with(state_dir=fixture['state'])
        current()
        self.assertEqual(connection.execute("SELECT status FROM catalog_current WHERE harness='codex'").fetchone()[0], 'unknown')

        board.service.automatic_discovery = True
        browser = Browser(board.call('console', {'action': 'open'})['url'])
        snapshot = browser.bootstrap()
        assert_current_form(snapshot)
        self.assertEqual({buddy['model'] for buddy in snapshot['buddies']}, {'fixture-alpha', 'fixture-beta', 'fixture-gamma'})
        status, _headers, body = browser.command('model_catalog_refresh', {'requestId': 'retained-http-refresh'}, csrf=snapshot['csrfToken'])
        self.assertEqual(status, 200, body)
        response = json.loads(body)['result']
        self.assertEqual(response['catalog']['source'], 'current-native-observations')
        self.assertTrue(response['catalog']['providers'])
        assert_current_form(response)
        current()
        self.assertEqual([tuple(row) for row in connection.execute('SELECT * FROM catalog_pending ORDER BY model')], pending)
        self.assertEqual([tuple(row) for row in connection.execute('SELECT buddy_id,source FROM buddies ORDER BY buddy_id')], sources)

        board.service.automatic_discovery = False
        with mock.patch.dict(os.environ, {'HMB_MODEL_CATALOG_FILE': str(FIXTURE / 'catalog.json')}):
            fresh = board.call('model_catalog_refresh', {'requestId': 'new-file-discovery'})
        assert_current_form(fresh)
        self.assertEqual(len(fresh['catalog']['providers'][0]['models']), 3)
        rows = dict(connection.execute('SELECT discovery_id,CAST(payload_json AS BLOB) FROM evaluation_catalog'))
        self.assertEqual({key: rows[key] for key in original}, original)
        self.assertGreater(len(rows), len(original))
        for key in rows.keys() - original.keys():
            assert_current_form(json.loads(rows[key]))
        for (raw,) in connection.execute('SELECT response_json FROM catalog_observations WHERE response_json IS NOT NULL'):
            assert_current_form(json.loads(raw))
        self.assertEqual(catalog.identities_from_payload(rows[legacy_id], 'codex'), {('openai', 'fixture-alpha', 'high')})

    def test_routing_budget_views_match_for_pre_upgrade_and_new_micro_tasks(self):
        from support import FakeClock, InProcessBoard

        fixture, connection = self.restored('budget-projections', migrate=False)
        run_id = fixture['stockIds']['helperParent']
        decision_id = connection.execute('SELECT current_routing_id FROM workflow_runs WHERE run_id=?',
                                         (run_id,)).fetchone()[0]
        frozen = [tuple(row) for row in connection.execute('SELECT rowid,* FROM router_requests ORDER BY rowid')]
        legacy_budget = json.loads(connection.execute('SELECT snapshot_json FROM router_requests WHERE decision_id=?',
                                                       (decision_id,)).fetchone()[0])['facts']['budget']
        self.assertEqual(legacy_budget, {'preset': 'deep', 'timeoutSeconds': 600, 'toolCalls': 64, 'bytesRead': 2097152})
        upgrade_state(fixture['state'])
        board = InProcessBoard(fixture['state'], clock=FakeClock('2026-01-01T00:03:00.000Z'))
        self.addCleanup(board.close)
        old = board.call('workflow_get', {'runId': run_id, 'routingHistory': {}})
        # The floor pins alpha, which would select it without calling a Router.
        with board.store.db.write() as db:
            db.execute("DELETE FROM evaluation_preferences WHERE mode='pin'")
        new = board.call('workflow_submit', {'requestId': 'budget-after-upgrade', 'hostId': 'fixture-host',
                                            'task': 'Compare current routing budget views',
                                            'cwd': str(fixture['fixtureRoot'] / 'repository'),
                                            'executionWorkspace': {'kind': 'worktree', 'access': 'write'}})
        new = board.call('workflow_get', {'runId': new['runId'], 'routingHistory': {}})
        expected = {'preset': 'deep', 'timeoutSeconds': 600, 'toolCalls': 64}
        for view in (old, new):
            self.assertEqual(view['routing']['budget'], expected, view['routing'])
            self.assertEqual(view['routingHistory']['entries'][0]['budget'], expected)
            decision = board.call('selection_get', {'decisionId': view['routing']['decisionId']})['decision']
            self.assertEqual(decision['budget'], expected)
        timeline = board.call('macro_task_timeline', {'macroTaskId': old['macroTaskId']})
        spans = [span for span in timeline['spans'] if span['kind'] == 'routing' and span['decisionId'] == decision_id]
        self.assertTrue(spans)
        self.assertTrue(all(span['routing']['budget'] == expected for span in spans))
        self.assertEqual([tuple(row) for row in connection.execute('SELECT rowid,* FROM router_requests ORDER BY rowid')
                          if row['decision_id'] != new['routing']['decisionId']], frozen)

    def test_failed_second_column_addition_rolls_back_the_whole_upgrade(self):
        fixture, connection = self.restored('failed-upgrade', migrate=False)
        state = fixture['state']
        complete_fixture_cleanup(connection)
        before = upgrade.idle_snapshot(state)
        schema = [tuple(row) for row in connection.execute(
            'SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name')]
        real_connect = sqlite3.connect

        class FailingConnection(sqlite3.Connection):
            additions = 0

            def execute(self, sql, parameters=(), /):
                if sql.startswith('ALTER TABLE router_settings ADD COLUMN'):
                    self.additions += 1
                    if self.additions == 2:
                        raise sqlite3.OperationalError('injected second column failure')
                return super().execute(sql, parameters)

        with mock.patch.object(schema_upgrade.sqlite3, 'connect',
                               side_effect=lambda *args, **kwargs: real_connect(*args, **kwargs, factory=FailingConnection)):
            with self.assertRaisesRegex(sqlite3.OperationalError, 'injected second column failure'):
                upgrade_state(state)
        self.assertEqual(upgrade.idle_snapshot(state), before)
        self.assertEqual([tuple(row) for row in connection.execute(
            'SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name')], schema)
        self.assertEqual(connection.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0],
                         str(UPGRADE_FLOOR_SCHEMA))
        self.assertEqual(upgrade_state(state)['targetSchema'], SCHEMA_VERSION)

    def test_started_verification_checks_retained_workflow_columns(self):
        fixture, connection = self.restored('projected-columns')
        complete_fixture_cleanup(connection)
        before = upgrade.idle_snapshot(fixture['state'])
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
                    self.assertFalse(native_path(fixture['state'] / item['journal']).exists())
                    self.assertNotIn(item['journal'], resources)
                else:
                    self.assertTrue(native_path(fixture['state'] / item['journal']).is_file())
                    self.assertIn(item['journal'], resources)

    def test_in_progress_deletion_removes_its_commands_events_and_fence(self):
        from hey_my_buddy.blackboard.tasks import macro_task_deletion

        fixture, connection = self.restored()
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
                         {'applied'})
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
            dispatch = connection.execute('SELECT task_id FROM router_dispatches WHERE task_id=?',
                                          (claim['task_id'],)).fetchone()
            document = router_sequence.dispatch(connection, dispatch['task_id'])
            self.assertEqual(document['taskId'], claim['task_id'])
            self.assertEqual(document['document']['routerBuddyId'], document['buddyId'])
        outcomes = connection.execute('SELECT * FROM router_outcomes').fetchall()
        self.assertEqual({row['outcome'] for row in outcomes}, {'answered', 'no_answer'})
        for outcome in outcomes:
            event = connection.execute('SELECT kind,payload_json FROM events WHERE seq=?',
                                       (outcome['event_seq'],)).fetchone()
            payload = json.loads(event['payload_json'])
            self.assertEqual(event['kind'], 'router.' + outcome['outcome'])
            fields = ['routing', outcome['request_id'], payload.get('selectionRetry', False),
                      outcome['router_index'], outcome['phase'], outcome['task_id'],
                      outcome['attempt_id'], outcome['outcome']]
            self.assertEqual(_sha(_json(fields).encode()), outcome['receipt_id'])
        self.assertEqual(connection.execute("SELECT COUNT(*) FROM events WHERE kind='router.claimed'").fetchone()[0], 7)


    def test_task_only_initial_and_retry_rounds_have_frozen_stock(self):
        fixture, connection = self.restored()
        stock = fixture['taskOnlyStock']
        self.assertEqual(set(stock), {'initialZero', 'initialSole', 'retryZero', 'retrySole'})
        for label, decision_id in stock.items():
            with self.subTest(label=label):
                retry = label.startswith('retry')
                request = connection.execute('SELECT requested_json FROM decision_requests WHERE decision_id=?',
                                             (decision_id,)).fetchone()
                requested = json.loads(request['requested_json'])
                self.assertEqual(requested['routingBasis']['candidateCount'], 1 if label.endswith('Sole') else 0)
                self.assertEqual(bool(requested.get('selectionRetry')), retry)
                rounds = connection.execute('SELECT * FROM router_requests WHERE decision_id=? ORDER BY round',
                                            (decision_id,)).fetchall()
                self.assertEqual([row['round'] for row in rounds], [1, 2] if retry else [1])
                snapshot = json.loads(rounds[-1]['snapshot_json'])
                self.assertEqual(snapshot['baseInput'], {'task': requested['task']})
                self.assertEqual(snapshot['inspections'], [])
                self.assertEqual(snapshot['facts'], {'routerBuddyId': None, 'routingMode': None,
                                                    'budget': None, 'routerCalled': False})
                self.assertEqual(connection.execute('SELECT COUNT(*) FROM router_dispatches WHERE decision_id=?',
                                                    (decision_id,)).fetchone()[0], int(retry))
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
        sessions = list(native_path(fixture['state'] / 'sessions').rglob('*.jsonl'))
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
        fixture, connection = self.restored(migrate=False)
        for version in ('24', '25', '26', '27', '28', '29', '0', '999', '028', '28.0', '029', '29.0', '030', '30.0', 'unrecognized'):
            with self.subTest(schema=version):
                connection.execute("UPDATE meta SET value=? WHERE key='schema_version'", (version,))
                connection.commit()
                before = (fixture['state'] / 'board.sqlite3').read_bytes()
                with self.assertRaises(Corruption):
                    Database(fixture['state']).initialize()
                with self.assertRaises(BoardError) as caught:
                    upgrade.idle_snapshot(fixture['state'])
                self.assertEqual(caught.exception.code, 'UNSUPPORTED_SCHEMA')
                for changed in (False, True):
                    with self.assertRaises(BoardError) as caught:
                        upgrade.preflight(fixture['state'], contract_changed=changed)
                    self.assertEqual(caught.exception.code, 'UNSUPPORTED_SCHEMA')
                self.assertEqual((fixture['state'] / 'board.sqlite3').read_bytes(), before)

    def test_started_verification_rejects_changed_retained_events(self):
        fixture, connection = self.restored()
        complete_fixture_cleanup(connection)
        before = upgrade.idle_snapshot(fixture['state'])
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

    def test_started_verification_checks_retained_json(self):
        fixture, connection = self.restored('checked-json-retention')
        complete_fixture_cleanup(connection)
        before = upgrade.idle_snapshot(fixture['state'])
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
        complete_fixture_cleanup(connection)
        before = upgrade.idle_snapshot(fixture['state'])
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

    def test_cleanup_expiry_is_removed_from_populated_floor_stock(self):
        fixture, connection = self.restored('cleanup-expiry', migrate=False)
        columns = {row[1] for row in connection.execute('PRAGMA table_info(workspace_cleanup_plans)')}
        self.assertIn('expires_at', columns)
        stock = [tuple(row) for row in connection.execute("SELECT plan_id,created_at FROM workspace_cleanup_plans WHERE state IN ('applying','applied') ORDER BY rowid")]
        self.assertTrue(stock)
        self.assertTrue(all(row[0] for row in connection.execute('SELECT expires_at FROM workspace_cleanup_plans')))
        connection.close()
        upgrade_state(fixture['state'])
        upgraded = sqlite3.connect(fixture['state'] / 'board.sqlite3')
        self.addCleanup(upgraded.close)
        upgraded.row_factory = sqlite3.Row
        self.assertNotIn('expires_at', {row[1] for row in upgraded.execute('PRAGMA table_info(workspace_cleanup_plans)')})
        self.assertEqual([tuple(row) for row in upgraded.execute('SELECT plan_id,created_at FROM workspace_cleanup_plans ORDER BY rowid')], stock)
        for row in upgraded.execute('SELECT * FROM workspace_cleanup_plans'):
            self.assertNotIn('expiresAt', WorkflowCoordinator._plan_view(row))

    def test_cleanup_stock_records_its_current_generator_and_fixed_floor(self):
        metadata = json.loads((FIXTURE / 'metadata.json').read_text())
        root = Path(__file__).resolve().parents[4]
        self.assertEqual(metadata['generatorSha256'], '53bc339e01bf2f083a3f2c0e621dda633a944e9bc1b1ea4af7dca632f1de5cc4')
        self.assertNotIn('baseGeneratorSha256', metadata)
        self.assertEqual(metadata['cleanupStockGeneratorSha256'], _sha((root / 'packaging/generate-upgrade-fixture.py').read_bytes()))
        self.assertEqual(metadata['cleanupStockBaseSha256'], _sha((FIXTURE / 'board.sql').read_bytes()))
        self.assertEqual(metadata['cleanupStockSha256'], _sha((FIXTURE / 'schema-31-cleanup-stock.sql').read_bytes()))
        self.assertIn('--cleanup-stock-for', metadata['cleanupStockGenerationCommand'])

    def test_reservation_stock_records_its_floor_writer_and_generator(self):
        metadata = json.loads((FIXTURE / 'metadata.json').read_text())
        root = Path(__file__).resolve().parents[4]
        self.assertEqual(metadata['reservationStockGeneratorSha256'], _sha((root / 'packaging/generate-upgrade-fixture.py').read_bytes()))
        self.assertEqual(metadata['reservationStockBaseSha256'], _sha((FIXTURE / 'board.sql').read_bytes()))
        self.assertEqual(metadata['reservationStockSha256'], _sha((FIXTURE / 'schema-30-reservation-stock.sql').read_bytes()))
        self.assertIn('--reservation-stock-for', metadata['reservationStockGenerationCommand'])

    def test_delivered_stock_records_its_floor_writer_and_physical_resources(self):
        metadata = json.loads((FIXTURE / 'metadata.json').read_text())
        root = Path(__file__).resolve().parents[4]
        self.assertEqual(metadata['deliveredStockGeneratorSha256'],
                         _sha((root / 'packaging/generate-upgrade-fixture.py').read_bytes()))
        self.assertEqual(metadata['deliveredStockBaseSha256'], _sha((FIXTURE / 'board.sql').read_bytes()))
        self.assertEqual(metadata['deliveredStockSqlSha256'],
                         _sha((FIXTURE / 'schema-30-delivered-stock.sql').read_bytes()))
        self.assertEqual(metadata['deliveredStockResourcesSha256'],
                         _sha((FIXTURE / 'schema-30-delivered-resources.json').read_bytes()))
        self.assertIn('--delivered-stock-for', metadata['deliveredStockGenerationCommand'])
