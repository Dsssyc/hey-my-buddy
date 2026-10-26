"""The installed upgrade base upgrades explicitly to the current schema."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest import mock

from blackboard.store.upgrade_fixture import FIXTURE, load_fixture, _git, _sha, _json
from hey_my_buddy.blackboard.store.store import BoardStore
from hey_my_buddy.blackboard.routing import router, router_sequence
from hey_my_buddy.blackboard.store.db import Corruption, Database, SCHEMA_VERSION
from hey_my_buddy.blackboard.store.upgrade import UPGRADE_BASE_SCHEMA, upgrade_state
from hey_my_buddy.blackboard.tasks import workspace
from hey_my_buddy.blackboard.tasks.workflow import WorkflowCoordinator
from hey_my_buddy.errors import BoardError
from hey_my_buddy.install import upgrade
from hey_my_buddy.paths import native_path


class UpgradeFixtureTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix='upgrade-base-fixture-'))


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
        self.assertEqual(fixture['sourceCommit'], '8ad462b2d58491eabac6f7c14ac5617dfbd1dbdf')
        self.assertEqual(fixture['sourceVersion'], '0.53.0')
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
        for row in connection.execute("SELECT w.input_json,a.result_json FROM workflow_turns w"
                                      " JOIN attempts a ON a.attempt_id=w.attempt_id WHERE w.state='returned'"):
            self.assertEqual(_sha(row[0].encode()), json.loads(row[1])['result']['turn']['inputSha256'])
        for _, run_id in fixture['stableIds'].items():
            row = connection.execute('SELECT workspace_manifest_json FROM workflow_runs WHERE run_id=?', (run_id,)).fetchone()
            manifest = json.loads(row[0])
            workspace.verify(manifest, state_dir=fixture['state'])
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
        before = native_path(worker_file).read_bytes()
        sealed = workspace.host_seal(fixture['state'], manifest, run_id, attempt)
        self.assertEqual(sealed['kind'], 'host-output')
        self.assertNotEqual(Path(sealed['diffPath']).parent, worker_file.parent)
        self.assertEqual(workspace.host_seal(fixture['state'], manifest, run_id, attempt), sealed)
        self.assertEqual(native_path(worker_file).read_bytes(), before)


    def test_portable_roots_rebind_stable_identities_without_changing_native_history(self):
        first, c1 = self.restored('first state')
        name = ('第二个 “私有” fixture-root state' if os.name == 'nt'
                else '第二个 "私有" <fixture-root> state')
        second, c2 = self.restored(name)
        _, m1, _ = self.sample(first, c1)
        _, m2, _ = self.sample(second, c2)
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


    def test_upgraded_base_opens_and_matches_fresh_structure(self):
        fixture, connection = self.restored()
        state = fixture['state']
        before = upgrade.idle_snapshot(state)
        files = {p.relative_to(native_path(state)): p.read_bytes() for p in native_path(state).rglob('*')
                 if p.is_file() and not p.name.startswith('board.sqlite3')}
        rowids = {name: [row[0] for row in connection.execute(f'SELECT rowid FROM "{name}" ORDER BY rowid')]
                  for name in before['tables']}
        self.assertEqual(before['schema'], SCHEMA_VERSION)
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
        self.assertEqual(upgrade.idle_snapshot(state), before)
        for name, original in rowids.items():
            self.assertEqual([row[0] for row in connection.execute(f'SELECT rowid FROM "{name}" ORDER BY rowid')],
                             original, name)
        self.assertEqual({p.relative_to(native_path(state)): p.read_bytes() for p in native_path(state).rglob('*')
                          if p.is_file() and not p.name.startswith('board.sqlite3')}, files)

    def test_started_verification_checks_retained_workflow_columns(self):
        fixture, connection = self.restored('projected-columns')
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
        fixture, connection = self.restored()
        for version in ('24', '25', '26', '27', '28', '0', '999', '028', '28.0', '029', '29.0', 'unrecognized'):
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
