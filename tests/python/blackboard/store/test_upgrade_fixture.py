"""Portable schema-24 stock from the installed base's real named operations."""
from __future__ import annotations

from contextlib import ExitStack
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import tempfile
import unittest
from unittest import mock

from blackboard.store.upgrade_fixture import load_fixture, _git, _sha, _json
from hey_my_buddy.blackboard.store import backup
from hey_my_buddy.blackboard.store.store import BoardStore
from hey_my_buddy.blackboard.routing import router
from hey_my_buddy.blackboard.store.db import Corruption, Database, SCHEMA_VERSION, canonical_json
from hey_my_buddy.blackboard.store.upgrade import UPGRADE_BASE_SCHEMA, upgrade_schema_24_to_25
from hey_my_buddy.blackboard.tasks import workspace
from hey_my_buddy.blackboard.tasks.workflow import WorkflowCoordinator
from hey_my_buddy.errors import BoardError
from hey_my_buddy.install import upgrade
from hey_my_buddy.protocol import schemas


class UpgradeFixtureTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix='upgrade-base-fixture-'))

    def restored(self, name='state'):
        fixture = load_fixture(self.root / name)
        connection = sqlite3.connect(fixture['state'] / 'board.sqlite3')
        connection.row_factory = sqlite3.Row
        self.addCleanup(connection.close)
        return fixture, connection

    def old_event(self, connection, kind, *, task_id=None, attempt_id=None, payload=None):
        # Schema 24's writer moves actor from the payload into the event columns.
        writer = mock.Mock()
        writer.now.return_value = 'created'
        return BoardStore._append_event(writer, connection, kind, task_id=task_id,
                                        attempt_id=attempt_id, payload=payload)

    def old_router_attempt(self, fixture, connection, decision_id, *, claimed=True):
        task_id, attempt_id = decision_id + '-router-task', decision_id + '-router-attempt'
        request_id = connection.execute('SELECT request_id FROM decision_requests WHERE decision_id=?',
                                        (decision_id,)).fetchone()[0]
        cwd = str(fixture['state'] / 'decisions' / decision_id)
        spec = {'adapter': 'decision', 'cwd': cwd, 'task': 'Bounded selection decision over the current evaluation table.',
                'routerBudgetSeconds': 70, 'requiredCapabilities': ['decision'],
                'decision': {'decisionId': decision_id, 'kind': 'select', 'routerBudgetSeconds': 60}}
        raw_spec = canonical_json(spec)
        connection.execute("INSERT INTO tasks(task_id,request_id,owner,spec_json,spec_canonical_json,input_fingerprint,"
                           "fingerprint_version,adapter,required_capabilities,cwd,exclusive_resources,router_budget_seconds,"
                           "state,revision,selected_attempt_id,created_at,updated_at)"
                           " VALUES(?,?,'decision',?,?,?,?,'decision','[\"decision\"]',?,'[]',70,?,?,?,'created','created')",
                           (task_id, 'decision:' + request_id + ':0:0', raw_spec, raw_spec,
                            schemas.spec_fingerprint(spec), schemas.FINGERPRINT_VERSION_CURRENT, cwd,
                            'completed' if claimed else 'queued', 2 if claimed else 1,
                            attempt_id if claimed else None))
        if claimed:
            connection.execute("INSERT INTO attempts(attempt_id,task_id,generation,nonce_verifier,execution_state,adapter,"
                               "log_paths,shutdown_confirmed,created_at,updated_at)"
                               " VALUES(?,?,1,'old-verifier','finished','decision','{}',1,'created','created')",
                               (attempt_id, task_id))
        buddy = connection.execute('SELECT b.buddy_id,b.harness,b.provider,b.model,b.effort FROM buddies b JOIN evaluation_decisions d'
                                   ' ON d.buddy_id=b.buddy_id WHERE d.decision_id=?', (decision_id,)).fetchone()
        if claimed:
            connection.execute('UPDATE attempts SET model_harness=?,model_provider=?,model_model=? WHERE attempt_id=?',
                               (buddy['harness'], buddy['provider'], buddy['model'], attempt_id))
        candidates = [dict(row) for row in connection.execute('SELECT buddy_id,harness,provider,model,effort'
                                                               ' FROM buddies ORDER BY rowid LIMIT 2')]
        document = {'buddies': [{'buddyId': item['buddy_id'], **{key: item[key] for key in
                                                             ('harness', 'provider', 'model', 'effort')}}
                                for item in candidates],
                    'task': 'fixture routing choice', 'routerBuddyId': buddy['buddy_id'],
                    'routerIndex': 0, 'buddy': {'harness': buddy['harness'], 'provider': buddy['provider'],
                                               'model': buddy['model'], 'effort': buddy['effort']}}
        raw_document = canonical_json(document)
        digest = hashlib.sha256(raw_document.encode()).hexdigest()
        requested = json.loads(connection.execute('SELECT requested_json FROM decision_requests WHERE decision_id=?',
                                                  (decision_id,)).fetchone()[0])
        requested.pop('routerCalled', None)
        requested.update({'routingMode': 'fast', 'routerBuddyId': buddy['buddy_id'],
                          'routerBudgetSeconds': 60, 'budget': {'timeoutSeconds': 60}, 'routerIndex': 0,
                          'routingBasis': {'candidateCount': len(candidates), 'excludedBuddies': [],
                                           'excludedCount': 0}})
        connection.execute('INSERT INTO router_requests(decision_id,snapshot_json,frozen_at) VALUES(?,?,?)',
                           (decision_id, canonical_json({'facts': {}, 'baseInput': {'buddies': document['buddies']},
                                                         'inspections': []}),
                            'created'))
        connection.execute('INSERT INTO router_dispatches(task_id,decision_id,router_index,buddy_id,harness,provider,model,'
                           'effort,document_json,input_sha256,created_at) VALUES(?,?,0,?,?,?,?,?,?,? ,?)',
                           (task_id, decision_id, buddy['buddy_id'], buddy['harness'], buddy['provider'], buddy['model'],
                            buddy['effort'], raw_document, digest, 'created'))
        if claimed:
            connection.execute('INSERT INTO router_claims(attempt_id,task_id,generation,tool_policy_json)'
                               ' VALUES(?,?,1,?)',
                               (attempt_id, task_id, canonical_json({'systemSandbox': False})))
        connection.execute('UPDATE decision_requests SET task_id=?,attempt_id=?,generation=?,input_json=?,input_sha256=?,'
                           'output_json=NULL,selected_json=NULL,requested_json=? WHERE decision_id=?',
                           (task_id, attempt_id if claimed else None, 1 if claimed else None,
                            raw_document, digest, canonical_json(requested), decision_id))
        return task_id, attempt_id if claimed else None

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
        upgrade_schema_24_to_25(fixture['state'])
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
        self.assertEqual(fixture['sourceCommit'], '90bd0148230fe1b030cc65e325026dea0cdfb8b9')
        self.assertEqual(fixture['sourceVersion'], '0.42.0')
        self.assertEqual(fixture['schemaVersion'], UPGRADE_BASE_SCHEMA)
        self.assertEqual(connection.execute('PRAGMA integrity_check').fetchone()[0], 'ok')
        self.assertEqual(connection.execute('PRAGMA foreign_key_check').fetchall(), [])
        self.assertEqual(connection.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0], str(UPGRADE_BASE_SCHEMA))
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

    def test_note_upgrade_strips_defaults_and_preserves_authored_bytes_and_digests(self):
        fixture, connection = self.restored('note-ownership')
        run_id = fixture['stableIds']['delivered']
        authored = 'Host note: 原样保留  A  '
        integration_id = connection.execute('SELECT integration_id FROM workflow_integrations LIMIT 1').fetchone()[0]
        connection.execute('UPDATE workflow_integrations SET reason=? WHERE integration_id=?',
                           (authored, integration_id))
        verification = json.loads(connection.execute(
            'SELECT verification_json FROM workflow_integrations WHERE integration_id=?',
            (integration_id,)).fetchone()[0])
        verification['reason'] = authored
        connection.execute('UPDATE workflow_integrations SET verification_json=? WHERE integration_id=?',
                           (json.dumps(verification), integration_id))
        connection.execute("UPDATE workflow_scope_versions SET reason='original submission' WHERE scope_version=1")
        connection.execute("UPDATE workflow_continuations SET reason='Host continuation' WHERE kind='manual'")
        workspace = json.loads(connection.execute(
            'SELECT execution_workspace_json FROM workflow_runs WHERE run_id=?', (run_id,)).fetchone()[0])
        workspace['accessReason'] = authored
        connection.execute('UPDATE workflow_runs SET execution_workspace_json=? WHERE run_id=?',
                           (json.dumps(workspace), run_id))
        request = connection.execute('SELECT request_id,decision_json FROM workflow_requests LIMIT 1').fetchone()
        if request is not None:
            decision = json.loads(request['decision_json'] or '{}')
            decision['reason'] = authored
            connection.execute('UPDATE workflow_requests SET decision_json=? WHERE request_id=?',
                               (json.dumps(decision), request['request_id']))
        event = connection.execute('SELECT seq FROM events ORDER BY seq LIMIT 1').fetchone()[0]
        connection.execute("UPDATE events SET kind='task.retried',payload_json=? WHERE seq=?",
                           (json.dumps({'reason': 'explicit retry', 'previousAttemptId': 'old'}), event))
        event2 = connection.execute('SELECT seq FROM events WHERE seq!=? ORDER BY seq LIMIT 1', (event,)).fetchone()[0]
        connection.execute("UPDATE events SET kind='workflow.cancelled',payload_json=? WHERE seq=?",
                           (json.dumps({'reason': 'service stop: ' + authored,
                                        'stopInitiator': 'service'}), event2))
        connection.execute("UPDATE events SET actor_role='service',actor_id='stop' WHERE seq=?", (event2,))
        event3 = connection.execute('SELECT seq FROM events WHERE seq NOT IN (?,?) ORDER BY seq LIMIT 1',
                                    (event, event2)).fetchone()[0]
        route_cancel = {'reason': 'Owning Micro task cancelled', 'owningRunId': run_id}
        connection.execute("UPDATE events SET kind='task.cancelled',payload_json=? WHERE seq=?",
                           (json.dumps(route_cancel), event3))
        event4 = connection.execute('SELECT seq FROM events WHERE seq NOT IN (?,?,?) ORDER BY seq LIMIT 1',
                                    (event, event2, event3)).fetchone()[0]
        connection.execute("UPDATE events SET kind='workflow.cancelled',payload_json=? WHERE seq=?",
                           (json.dumps({'reason': 'signal'}), event4))
        connection.execute("UPDATE events SET actor_role='host',actor_id='fixture' WHERE seq=?", (event4,))
        connection.commit()
        commands = [tuple(row) for row in connection.execute(
            'SELECT command_id,kind,request_hash FROM commands ORDER BY command_id')]
        turns = [tuple(row) for row in connection.execute(
            'SELECT turn_id,input_json,input_sha256,outcome_json FROM workflow_turns ORDER BY turn_id')]
        artifacts = [tuple(row) for row in connection.execute(
            "SELECT artifact_id,manifest_json FROM workflow_artifacts WHERE kind='shared-refs' ORDER BY artifact_id")]
        bindings = [tuple(row) for row in connection.execute(
            'SELECT integration_id,binding_sha256 FROM workflow_integrations ORDER BY integration_id')]
        upgrade_schema_24_to_25(fixture['state'])
        self.assertEqual([tuple(row) for row in connection.execute(
            'SELECT command_id,kind,request_hash FROM commands ORDER BY command_id')], commands)
        self.assertEqual([tuple(row) for row in connection.execute(
            'SELECT turn_id,input_json,input_sha256,outcome_json FROM workflow_turns ORDER BY turn_id')], turns)
        self.assertEqual([tuple(row) for row in connection.execute(
            "SELECT artifact_id,manifest_json FROM workflow_artifacts WHERE kind='shared-refs' ORDER BY artifact_id")], artifacts)
        self.assertEqual([tuple(row) for row in connection.execute(
            'SELECT integration_id,binding_sha256 FROM workflow_integrations ORDER BY integration_id')], bindings)
        self.assertEqual(connection.execute('SELECT note FROM workflow_integrations WHERE integration_id=?',
                                            (integration_id,)).fetchone()[0], authored)
        verification = json.loads(connection.execute(
            'SELECT verification_json FROM workflow_integrations WHERE integration_id=?',
            (integration_id,)).fetchone()[0])
        self.assertEqual(verification['note'], authored)
        self.assertNotIn('reason', verification)
        self.assertEqual(connection.execute('SELECT COUNT(*) FROM workflow_scope_versions WHERE note IS NOT NULL'
                                            " AND scope_version=1").fetchone()[0], 0)
        self.assertEqual(connection.execute("SELECT COUNT(*) FROM workflow_continuations WHERE kind='manual'"
                                            " AND note='Host continuation'").fetchone()[0],
                         connection.execute("SELECT COUNT(*) FROM workflow_continuations WHERE kind='manual'").fetchone()[0])
        self.assertEqual(json.loads(connection.execute(
            'SELECT execution_workspace_json FROM workflow_runs WHERE run_id=?', (run_id,)).fetchone()[0])['accessNote'],
                         authored)
        if request is not None:
            self.assertEqual(json.loads(connection.execute('SELECT decision_json FROM workflow_requests WHERE request_id=?',
                                                            (request['request_id'],)).fetchone()[0])['note'], authored)
        self.assertIsNone(json.loads(connection.execute('SELECT payload_json FROM events WHERE seq=?',
                                                        (event,)).fetchone()[0])['note'])
        self.assertEqual(json.loads(connection.execute('SELECT payload_json FROM events WHERE seq=?',
                                                       (event2,)).fetchone()[0])['note'], authored)
        self.assertEqual(json.loads(connection.execute('SELECT payload_json FROM events WHERE seq=?',
                                                       (event3,)).fetchone()[0]), route_cancel)
        self.assertEqual(json.loads(connection.execute('SELECT payload_json FROM events WHERE seq=?',
                                                       (event4,)).fetchone()[0])['note'], 'signal')

    def prepare_generated_conclusion(self, fixture, connection, *, nested: bool = False):
        conclusions = [dict(row) for row in connection.execute(
            "SELECT c.*,e.seq FROM workflow_host_conclusions c JOIN events e"
            " ON json_extract(e.payload_json,'$.conclusionId')=c.conclusion_id"
            " WHERE e.kind='workflow.host_concluded' ORDER BY e.seq")]
        self.assertEqual(len(conclusions), 2)
        child, parent = conclusions
        self.assertLess(child['seq'], parent['seq'])
        turn_id = connection.execute('SELECT turn_id FROM workflow_turns WHERE run_id=? LIMIT 1',
                                     (parent['run_id'],)).fetchone()
        if turn_id is None:
            turn_id = connection.execute('SELECT turn_id FROM workflow_turns LIMIT 1').fetchone()
        self.assertIsNotNone(turn_id)
        connection.execute('UPDATE workflow_runs SET delegation_authority=1 WHERE run_id=?',
                           (parent['run_id'],))
        immediate_lead = parent['run_id']
        if nested:
            immediate_lead = next(run_id for (run_id,) in connection.execute('SELECT run_id FROM workflow_runs')
                                  if run_id not in {child['run_id'], parent['run_id']})
            connection.execute('INSERT OR REPLACE INTO workflow_lead_tasks'
                               '(owned_run_id,lead_run_id,lead_turn_id,created_at) VALUES(?,?,?,?)',
                               (immediate_lead, parent['run_id'], turn_id[0], parent['created_at']))
        connection.execute('INSERT OR REPLACE INTO workflow_lead_tasks'
                           '(owned_run_id,lead_run_id,lead_turn_id,created_at) VALUES(?,?,?,?)',
                           (child['run_id'], immediate_lead, turn_id[0], parent['created_at']))
        note = f"Concluded because Lead micro task {parent['run_id']} received a Host conclusion"
        connection.execute('UPDATE workflow_host_conclusions SET note=?,created_at=?,actor_role=?,actor_id=?'
                           ' WHERE conclusion_id=?',
                           (note, parent['created_at'], parent['actor_role'], parent['actor_id'],
                            child['conclusion_id']))
        connection.commit()
        return child, parent, note

    def test_generated_conclusion_uses_lead_relationship_and_preserves_command_identity(self):
        fixture, connection = self.restored('generated-conclusion')
        child, parent, _note = self.prepare_generated_conclusion(fixture, connection, nested=True)
        commands = [tuple(row) for row in connection.execute(
            'SELECT command_id,request_hash FROM commands ORDER BY command_id')]
        upgrade_schema_24_to_25(fixture['state'])
        converted = connection.execute('SELECT note,reason_code,reason_params_json FROM workflow_host_conclusions'
                                       ' WHERE conclusion_id=?', (child['conclusion_id'],)).fetchone()
        self.assertIsNone(converted['note'])
        self.assertEqual(converted['reason_code'], 'lead.owned-concluded')
        self.assertEqual(json.loads(converted['reason_params_json']), {'leadRunId': parent['run_id']})
        self.assertEqual(json.loads(connection.execute(
            "SELECT payload_json FROM events WHERE kind='workflow.host_concluded' AND task_id=?",
            (child['run_id'],)).fetchone()[0])['reason']['code'], 'lead.owned-concluded')
        self.assertEqual([tuple(row) for row in connection.execute(
            'SELECT command_id,request_hash FROM commands ORDER BY command_id')], commands)

    def test_generated_helper_conclusion_follows_the_lead_ownership_graph(self):
        for nested in (False, True):
            with self.subTest(nested=nested):
                fixture, connection = self.restored('generated-helper-conclusion-' + str(nested))
                child, parent, _note = self.prepare_generated_conclusion(
                    fixture, connection, nested=nested)
                immediate = connection.execute('SELECT lead_run_id FROM workflow_lead_tasks WHERE owned_run_id=?',
                                               (child['run_id'],)).fetchone()[0]
                connection.execute('DELETE FROM workflow_lead_tasks WHERE owned_run_id=?', (child['run_id'],))
                connection.execute('INSERT INTO workflow_children'
                                   '(child_task_id,parent_run_id,request_id,decision_command_id,state,'
                                   'workspace_intent_json,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)',
                                   (child['run_id'], immediate, 'fixture-helper-request', 'fixture-helper-decision',
                                    'failed', '{}', parent['created_at'], parent['created_at']))
                connection.commit()
                upgrade_schema_24_to_25(fixture['state'])
                row = connection.execute('SELECT note,reason_code,reason_params_json FROM workflow_host_conclusions'
                                         ' WHERE conclusion_id=?', (child['conclusion_id'],)).fetchone()
                self.assertIsNone(row['note'])
                self.assertEqual(row['reason_code'], 'lead.owned-concluded')
                self.assertEqual(json.loads(row['reason_params_json']), {'leadRunId': parent['run_id']})

    def test_generated_looking_conclusion_without_matching_time_remains_content(self):
        fixture, connection = self.restored('ambiguous-conclusion')
        child, _, note = self.prepare_generated_conclusion(fixture, connection)
        connection.execute('UPDATE workflow_host_conclusions SET created_at=? WHERE conclusion_id=?',
                           ('different-time', child['conclusion_id']))
        connection.commit()
        upgrade_schema_24_to_25(fixture['state'])
        row = connection.execute('SELECT note,reason_code FROM workflow_host_conclusions WHERE conclusion_id=?',
                                 (child['conclusion_id'],)).fetchone()
        self.assertEqual(row['note'], note)
        self.assertIsNone(row['reason_code'])

    def test_host_conclusion_sentence_without_lead_cascade_remains_content(self):
        fixture, connection = self.restored('host-conclusion-content')
        conclusion = connection.execute('SELECT conclusion_id FROM workflow_host_conclusions LIMIT 1').fetchone()[0]
        authored = 'Concluded because Lead micro task unrelated received a Host conclusion'
        connection.execute('UPDATE workflow_host_conclusions SET note=? WHERE conclusion_id=?',
                           (authored, conclusion))
        connection.commit()
        upgrade_schema_24_to_25(fixture['state'])
        row = connection.execute('SELECT note,reason_code FROM workflow_host_conclusions WHERE conclusion_id=?',
                                 (conclusion,)).fetchone()
        self.assertEqual(row['note'], authored)
        self.assertIsNone(row['reason_code'])

    def test_old_full_access_manifest_verifies_with_its_original_field_and_digest(self):
        fixture, connection = self.restored('sealed-full-access')
        _run_id, manifest, _attempt = self.sample(fixture, connection)
        old = dict(manifest, version=2, access='full', accessReason='Host granted full access')
        old['manifestSha256'] = _sha(_json({key: value for key, value in old.items()
                                             if key != 'manifestSha256'}).encode())
        self.assertEqual(workspace.verify(old)['valid'], True)
        self.assertEqual(old['accessReason'], 'Host granted full access')
        self.assertEqual(json.loads(connection.execute('SELECT workspace_manifest_json FROM workflow_runs'
                                                       ' WHERE run_id=?', (_run_id,)).fetchone()[0]), manifest)

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

    def test_contract_cutover_preserves_stock_and_matches_fresh_structure(self):
        fixture, old = self.restored()
        state = fixture['state']
        self.assertEqual(fixture['schemaVersion'], UPGRADE_BASE_SCHEMA)
        before = upgrade.idle_snapshot(state, exact=True)
        expected_settings = router.configuration(old)
        expected_runs = dict(old.execute('SELECT run_id,state FROM workflow_runs'))
        request_fingerprints = dict(old.execute('SELECT run_id,request_fingerprint FROM workflow_runs'))
        # 0.42.0 (90bd0148) through 0.45.3 (e006910b) share the same
        # writer: an omitted reason is '', explicit strings (<= 500 characters)
        # are stored verbatim. Derive the expected nullable notes from that stock.
        expected_preferences = [(row[0], row[1], row[2] or None) for row in old.execute(
            'SELECT buddy_id,mode,reason FROM evaluation_preferences ORDER BY buddy_id')]
        retained_meta = dict(old.execute("SELECT key,value FROM meta WHERE key!='schema_version'"))
        stock = {row[0]: [dict(item) for item in old.execute(f'SELECT * FROM {row[0]} ORDER BY rowid')]
                 for row in old.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
        stock.pop('quota_current')
        stock.pop('harness_quota_observations')
        stock['provider_cooling'] = []
        stock['commands'] = [row for row in stock['commands'] if row['kind'] != 'quota.redetect']
        for row in stock['commands']:
            row['deletion_id'] = None
        for row in stock['meta']:
            if row['key'] == 'schema_version':
                row['value'] = str(SCHEMA_VERSION)
        stock['macro_task_deletions'] = []
        for row in stock['evaluation_preferences']:
            row['note'] = row.pop('reason') or None
        for row in stock['workflow_requests']:
            row['reason_code'] = row['reason_params_json'] = None
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
            # Reason-bearing rows change form; all other stock stays exact.
            for table, rows in stock.items():
                if table in {'buddies', 'catalog_observations', 'evaluation_catalog',
                             'catalog_current', 'harness_health', 'events',
                             'workflow_host_conclusions', 'workflow_integrations',
                             'workflow_scope_versions', 'workflow_turns',
                             'workflow_continuations', 'workflow_workspace_conflicts',
                             'workflow_runs', 'workflow_requests', 'evaluation_decisions',
                             'workflow_routes', 'decision_requests', 'router_requests'}:
                    continue
                with self.subTest(retained_table=table):
                    self.assertEqual([dict(item) for item in upgraded.execute(f'SELECT * FROM {table} ORDER BY rowid')], rows)
            self.assertEqual(set(stock),
                             {row[0] for row in upgraded.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")})
            source_schema = [tuple(row) for row in upgraded.execute(
                'SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name')]
            fresh_schema = [tuple(row) for row in baseline.execute(
                'SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name')]
            # SQLite rewrites renamed column/table SQL with different spacing and
            # quotes. Compare the actual declarations, constraints and indexes.
            def structure(rows):
                return [(kind, name, table,
                         ''.join(re.sub(r'--[^\n]*', '', sql).replace('"', '').split()) if sql is not None else None)
                        for kind, name, table, sql in rows]
            self.assertEqual(structure(source_schema), structure(fresh_schema))
            for table in stock:
                with self.subTest(structure=table):
                    self.assertEqual([tuple(row) for row in upgraded.execute(f'PRAGMA table_info({table})')],
                                     [tuple(row) for row in baseline.execute(f'PRAGMA table_info({table})')])
                    self.assertEqual([tuple(row) for row in upgraded.execute(f'PRAGMA foreign_key_list({table})')],
                                     [tuple(row) for row in baseline.execute(f'PRAGMA foreign_key_list({table})')])
            self.assertEqual(upgraded.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0],
                             str(SCHEMA_VERSION))
            self.assertEqual(dict(upgraded.execute("SELECT key,value FROM meta WHERE key!='schema_version'")),
                             retained_meta)
            self.assertEqual(tuple(upgraded.execute(
                'SELECT silence_seconds,revision FROM reminder_settings WHERE id=1').fetchone()), (900, 1))
            for label, connection in (('upgraded', upgraded), ('fresh', baseline)):
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

    def test_quota_stock_is_discarded_and_cooling_constraints_match_fresh_board(self):
        fixture, old = self.restored('quota-stock')
        observed = '2026-01-01T00:00:00.000Z'
        for limit_id, blocked, code, resets_at, manual, consumed in (
            ('primary', 1, 'HARNESS_QUOTA_EXHAUSTED', None, observed, observed),
            ('secondary', 1, 'HARNESS_BALANCE_ZERO', '2026-01-02T00:00:00.000Z', None, None),
            ('available', 0, None, None, None, None),
        ):
            old.execute('INSERT INTO quota_current(harness,provider,limit_id,blocked,code,source,'
                        'observed_at,last_observed_at,resets_at,retry_manual_at,retry_consumed_at,retry_consumed_by)'
                        ' VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
                        ('codex', 'openai', limit_id, blocked, code, 'native', observed, observed,
                         resets_at, manual, consumed, 'decision-1' if consumed else None))
        old.execute('INSERT INTO harness_quota_observations(harness,quota_json) VALUES(?,?)'
                    ' ON CONFLICT(harness) DO UPDATE SET quota_json=excluded.quota_json',
                    ('codex', canonical_json({'observedAt': observed, 'resetsAt': observed})))
        raw_results = [tuple(row) for row in old.execute('SELECT attempt_id,result_json FROM attempts ORDER BY attempt_id')]
        old.commit()
        upgrade_schema_24_to_25(fixture['state'])
        self.assertEqual(old.execute('SELECT COUNT(*) FROM provider_cooling').fetchone()[0], 0)
        self.assertEqual([tuple(row) for row in old.execute('SELECT attempt_id,result_json FROM attempts ORDER BY attempt_id')],
                         raw_results)
        self.assertEqual(old.execute('PRAGMA foreign_key_check').fetchall(), [])
        fresh = Database(self.root / 'fresh-cooling')
        fresh.initialize()
        self.addCleanup(fresh.close)
        with fresh.connect() as baseline:
            for connection in (old, baseline):
                connection.execute('INSERT INTO provider_cooling VALUES(?,?,?,?,?)',
                                   ('codex', 'openai', 1, None, observed))
                with self.assertRaises(sqlite3.IntegrityError):
                    connection.execute('INSERT INTO provider_cooling VALUES(?,?,?,?,?)',
                                       ('codex', 'openai', 2, observed, observed))
                for count, until, rejected_at in ((0, None, observed), (1.5, None, observed),
                                                  (1, '2026-01-01T00:00:00+01:00', observed),
                                                  (1, None, 'not-a-utc-time'),
                                                  (1, '2026-13-01T00:00:00.000Z', observed),
                                                  (1, None, '2026-13-01T00:00:00.000Z')):
                    with self.subTest(count=count, until=until, rejected_at=rejected_at):
                        with self.assertRaises(sqlite3.IntegrityError):
                            connection.execute('INSERT INTO provider_cooling VALUES(?,?,?,?,?)',
                                               ('claude', 'anthropic', count, until, rejected_at))
                self.assertEqual(connection.execute('SELECT COUNT(*) FROM provider_cooling').fetchone()[0], 1)

    def test_turn_rebuild_preserves_rows_and_references_and_accepts_rejected(self):
        fixture, connection = self.restored('rejected-turn')
        original = [tuple(row) for row in connection.execute(
            'SELECT turn_id,run_id,attempt_id,turn_index,state,input_json FROM workflow_turns ORDER BY turn_id')]
        lead_run = original[0][1]
        owned_run = next(run_id for run_id, in connection.execute('SELECT run_id FROM workflow_runs')
                         if run_id != lead_run)
        connection.execute('INSERT INTO workflow_lead_tasks(owned_run_id,lead_run_id,lead_turn_id,created_at)'
                           ' VALUES(?,?,?,?)', (owned_run, lead_run, original[0][0], '2026-01-01T00:00:00.000Z'))
        connection.commit()
        upgrade_schema_24_to_25(fixture['state'])
        self.assertEqual([tuple(row) for row in connection.execute(
            'SELECT turn_id,run_id,attempt_id,turn_index,state,input_json FROM workflow_turns ORDER BY turn_id')],
                         original)
        self.assertEqual(connection.execute('PRAGMA foreign_key_check').fetchall(), [])
        self.assertEqual(connection.execute('SELECT lead_turn_id FROM workflow_lead_tasks WHERE owned_run_id=?',
                                            (owned_run,)).fetchone()[0], original[0][0])
        turn_id = original[0][0]
        connection.execute("UPDATE workflow_turns SET state='rejected' WHERE turn_id=?", (turn_id,))
        with self.assertRaises(sqlite3.IntegrityError):
            connection.execute("UPDATE workflow_turns SET state='unknown' WHERE turn_id=?", (turn_id,))
            fix_indexes = [row['name'] for row in upgraded.execute('PRAGMA index_list(workflow_fixes)')]
            self.assertIn(('fixed_run_id', 'created_at'), [tuple(row['name'] for row in upgraded.execute(
                f'PRAGMA index_info({name})')) for name in fix_indexes])
            self.assertEqual(router.configuration(upgraded), expected_settings)
            self.assertEqual(dict(upgraded.execute('SELECT run_id,state FROM workflow_runs')), expected_runs)
            self.assertEqual(dict(upgraded.execute('SELECT run_id,request_fingerprint FROM workflow_runs')),
                             request_fingerprints)
            self.assertEqual([tuple(row) for row in upgraded.execute(
                'SELECT buddy_id,mode,note FROM evaluation_preferences ORDER BY buddy_id')],
                expected_preferences)
            self.assertEqual(upgraded.execute('PRAGMA integrity_check').fetchone()[0], 'ok')
            self.assertEqual(upgraded.execute('PRAGMA foreign_key_check').fetchall(), [])

    def test_preference_notes_preserve_the_accepted_schema24_writer_values(self):
        # The unchanged 0.42.0--0.45.3 _reason writer defaults to '' and accepts
        # every string of up to 500 characters, including whitespace and Unicode.
        for index, (written, expected) in enumerate((('', None), ('  ', '  '),
                                                    ('用户的说明' * 100, '用户的说明' * 100))):
            with self.subTest(written=written):
                fixture, old = self.restored(f'preference-{index}')
                old.execute('UPDATE evaluation_preferences SET reason=?', (written,))
                ids = [row[0] for row in old.execute('SELECT buddy_id FROM evaluation_preferences ORDER BY buddy_id')]
                old.commit()
                old.close()
                upgrade_schema_24_to_25(fixture['state'])
                with sqlite3.connect(fixture['state'] / 'board.sqlite3') as upgraded:
                    self.assertEqual(upgraded.execute(
                        'SELECT buddy_id,note FROM evaluation_preferences ORDER BY buddy_id').fetchall(),
                        [(buddy_id, expected) for buddy_id in ids])

    def test_preference_notes_upgrade_board_routing_copies_and_keep_native_audit(self):
        # 0.45.3 freezes user_policy.preferences in baseInput/programSelection
        # and f.reason in routingBasis; these are board-owned copies of the same
        # schema-24 writer values. The dispatched native input stays exact.
        fixture, old = self.restored('preference-copies')
        decision_id, buddy_id = old.execute(
            'SELECT decision_id,buddy_id FROM evaluation_decisions LIMIT 1').fetchone()
        preferences = [{'buddyId': buddy_id, 'mode': 'pin', 'reason': ''}]
        excluded = [{'buddyId': buddy_id, 'harness': 'dsh', 'provider': 'fixture',
                     'model': 'model', 'effort': 'max', 'reason': ' User explanation  '}]
        expected_preferences = [{'buddyId': buddy_id, 'mode': 'pin', 'note': None}]
        expected_excluded = [{'buddyId': buddy_id, 'harness': 'dsh', 'provider': 'fixture',
                              'model': 'model', 'effort': 'max', 'note': ' User explanation  '}]
        requested = json.loads(old.execute('SELECT requested_json FROM decision_requests WHERE decision_id=?',
                                           (decision_id,)).fetchone()[0])
        requested['routingBasis'] = {'candidateCount': 1, 'excludedCount': 1, 'excludedBuddies': excluded}
        native = canonical_json({'preferences': preferences, 'routingBasis': requested['routingBasis']})
        native_sha = _sha(native.encode())
        output = {'programSelection': {'code': 'single-candidate', 'buddyId': buddy_id,
                                       'preferences': preferences}}
        old.execute("UPDATE evaluation_decisions SET status='completed',error=NULL WHERE decision_id=?", (decision_id,))
        old.execute('UPDATE decision_requests SET requested_json=?,input_json=?,input_sha256=?,output_json=?'
                    ' WHERE decision_id=?', (canonical_json(requested), native, native_sha,
                                             canonical_json(output), decision_id))
        snapshot = {'baseInput': {'preferences': preferences, 'routingBasis': requested['routingBasis']}}
        old.execute('INSERT OR REPLACE INTO router_requests(decision_id,snapshot_json,frozen_at) VALUES(?,?,?)',
                    (decision_id, canonical_json(snapshot), 'created'))
        old.commit()
        upgrade_schema_24_to_25(fixture['state'])
        row = old.execute('SELECT requested_json,input_json,input_sha256,output_json FROM decision_requests'
                          ' WHERE decision_id=?', (decision_id,)).fetchone()
        self.assertEqual(json.loads(row['requested_json'])['routingBasis']['excludedBuddies'], expected_excluded)
        self.assertEqual(json.loads(row['output_json'])['programSelection']['preferences'], expected_preferences)
        copied = json.loads(old.execute('SELECT snapshot_json FROM router_requests WHERE decision_id=?',
                                       (decision_id,)).fetchone()[0])['baseInput']
        self.assertEqual(copied['preferences'], expected_preferences)
        self.assertEqual(copied['routingBasis']['excludedBuddies'], expected_excluded)
        self.assertEqual((row['input_json'], row['input_sha256']), (native, native_sha))

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

    def test_failed_schema_write_rolls_back_ddl_and_all_stock(self):
        fixture, connection = self.restored('failed-upgrade')
        connection.execute("CREATE TRIGGER reject_upgrade BEFORE UPDATE OF value ON meta "
                           "WHEN NEW.key='schema_version' BEGIN SELECT RAISE(ABORT,'injected upgrade failure'); END")
        connection.commit()
        before = backup.database_snapshot(connection, exact=True)
        schema = list(connection.execute('SELECT * FROM sqlite_master ORDER BY type,name'))
        with self.assertRaisesRegex(sqlite3.IntegrityError, 'injected upgrade failure'):
            upgrade_schema_24_to_25(fixture['state'])
        self.assertEqual(backup.database_snapshot(connection, exact=True), before)
        self.assertEqual(list(connection.execute('SELECT * FROM sqlite_master ORDER BY type,name')), schema)

    def test_health_and_catalog_reasons_preserve_native_content(self):
        fixture, connection = self.restored('reason-conversion')
        native = 'Native limit: 原样保留 (12/12)'
        health = json.loads(connection.execute("SELECT record_json FROM harness_health WHERE harness='codex'").fetchone()[0])
        health['reasonCode'] = 'HARNESS_ACCOUNT_STOP_UNCONFIRMED'
        health['remedy'] = 'old guidance'
        health['accountReadFailure'] = {'reasonCode': 'HARNESS_ACCOUNT_STOP_UNCONFIRMED',
                                        'message': 'native diagnostic'}
        health['lastProbe']['reasonCode'] = 'timeout'
        health['lastProbe']['candidates'] = [{'reasonCode': 'node-missing', 'remedy': 'old guidance'}]
        health['candidates'] = [{'reasonCode': 'not-found', 'status': 'missing'}]
        connection.execute("UPDATE harness_health SET record_json=? WHERE harness='codex'", (json.dumps(health),))
        connection.execute("UPDATE buddies SET available=0,unavailable_reason=? WHERE model='fixture-beta'", (native,))
        connection.execute("UPDATE buddies SET available=0,unavailable_reason=? WHERE model='fixture-alpha'",
                           ('not present in the latest confirmed native discovery',))
        for discovery_id, payload_json in connection.execute('SELECT discovery_id,payload_json FROM evaluation_catalog').fetchall():
            payload = json.loads(payload_json)
            for model in (model for provider in payload['providers'] for model in provider['models']
                          if model['id'] == 'fixture-beta'):
                model.update(available=False, unavailableReason=native)
            connection.execute('UPDATE evaluation_catalog SET payload_json=? WHERE discovery_id=?',
                               (json.dumps(payload), discovery_id))
        for observation_id, response_json, harnesses_json in connection.execute(
                'SELECT observation_id,response_json,harnesses_json FROM catalog_observations').fetchall():
            response = json.loads(response_json)
            response['catalog']['warnings'] = [
                'native: 文案逐字保全', 'A model with no usable identity or reasoning effort was omitted']
            for model in (model for provider in response['catalog']['providers'] for model in provider['models']
                          if model['id'] == 'fixture-beta'):
                model.update(available=False, unavailableReason=native)
            for buddy in (buddy for buddy in response['buddies'] if buddy['model'] == 'fixture-beta'):
                buddy['unavailableReason'] = native
            harnesses = json.loads(harnesses_json)
            harnesses[0]['reasonCode'] = 'HARNESS_NOT_CHECKED'
            connection.execute('UPDATE catalog_observations SET response_json=?,harnesses_json=? WHERE observation_id=?',
                               (json.dumps(response), json.dumps(harnesses), observation_id))
        connection.execute("UPDATE catalog_current SET reason='ACCOUNT_STATUS_UNKNOWN' WHERE harness='codex'")
        connection.commit()
        upgrade_schema_24_to_25(fixture['state'])
        rows = {row['model']: row for row in connection.execute('SELECT * FROM buddies')}
        self.assertEqual((rows['fixture-beta']['unavailable_code'], rows['fixture-beta']['unavailable_content']),
                         ('catalog.native-unavailable', native))
        self.assertEqual(json.loads(rows['fixture-beta']['unavailable_params_json']), {})
        self.assertEqual(rows['fixture-alpha']['unavailable_code'], 'catalog.model-absent')
        health = json.loads(connection.execute("SELECT record_json FROM harness_health WHERE harness='codex'").fetchone()[0])
        self.assertEqual(health['reason'], {'code': 'harness.shutdown-unverified',
                                             'params': {'operation': 'account-read'}})
        self.assertEqual(health['lastProbe']['reason']['code'], 'harness.timeout')
        self.assertEqual(health['lastProbe']['candidates'][0]['reason']['code'], 'harness.node-missing')
        self.assertNotIn('remedy', health['lastProbe']['candidates'][0])
        self.assertEqual(health['candidates'][0]['reason']['code'], 'harness.not-found')
        self.assertNotIn('remedy', health)
        self.assertEqual(health['accountReadFailure']['message'], 'native diagnostic')
        payload = json.loads(connection.execute('SELECT payload_json FROM evaluation_catalog LIMIT 1').fetchone()[0])
        model = next(model for provider in payload['providers'] for model in provider['models']
                     if model['id'] == 'fixture-beta')
        self.assertEqual(model['unavailableContent'], native)
        self.assertEqual(model['unavailableReason']['code'], 'catalog.native-unavailable')
        response = json.loads(connection.execute('SELECT response_json FROM catalog_observations LIMIT 1').fetchone()[0])
        self.assertNotIn('note', response)
        self.assertEqual(response['catalog']['warnings'],
                         [{'reason': {'code': 'catalog.native-warning', 'params': {}},
                           'content': 'native: 文案逐字保全'},
                          {'reason': {'code': 'catalog.native-warning', 'params': {}},
                           'content': 'A model with no usable identity or reasoning effort was omitted'}])
        self.assertEqual(json.loads(connection.execute('SELECT reason FROM catalog_current').fetchone()[0]),
                         {'code': 'catalog.account-status-unknown', 'params': {'harness': 'codex'}})

    def test_known_blackboard_unknown_reason_becomes_history_and_unknown_owner_rolls_back(self):
        fixture, connection = self.restored('reason-ownership')
        connection.execute("UPDATE buddies SET available=0,unavailable_reason='older blackboard text' "
                           "WHERE model='fixture-alpha'")
        connection.commit()
        upgrade_schema_24_to_25(fixture['state'])
        row = connection.execute("SELECT buddy_id,unavailable_code,unavailable_params_json,unavailable_content "
                                 "FROM buddies WHERE model='fixture-alpha'").fetchone()
        self.assertEqual(row['unavailable_code'], 'history.reason-unavailable')
        self.assertEqual(json.loads(row['unavailable_params_json']), {'recordId': row['buddy_id']})
        self.assertIsNone(row['unavailable_content'])

        fixture, connection = self.restored('reason-unknown-owner')
        connection.execute("UPDATE buddies SET source='outside',available=0,unavailable_reason='ambiguous' "
                           "WHERE model='fixture-alpha'")
        connection.commit()
        before = backup.database_snapshot(connection, exact=True)
        schema = list(connection.execute('SELECT * FROM sqlite_master ORDER BY type,name'))
        with self.assertRaises(BoardError) as caught:
            upgrade_schema_24_to_25(fixture['state'])
        self.assertEqual(caught.exception.code, 'UPGRADE_INVALID_BOARD')
        self.assertEqual(backup.database_snapshot(connection, exact=True), before)
        self.assertEqual(list(connection.execute('SELECT * FROM sqlite_master ORDER BY type,name')), schema)

    def test_blackboard_constants_without_retained_discovery_still_convert(self):
        fixture, connection = self.restored('known-reason-without-discovery')
        cases = (
            ('not present in the latest confirmed native discovery', 'catalog.model-absent', {}),
            ('not present in the latest complete native discovery', 'catalog.effort-retired', {}),
            ('HARNESS_NOT_CHECKED', 'harness.not-checked', {'operation': 'probe'}),
            ('HARNESS_START_FAILED', 'harness.launch-failed', {'operation': 'probe'}),
            ('timeout', 'harness.timeout', {'operation': 'probe'}),
            ('HARNESS_ACCOUNT_STOP_UNCONFIRMED', 'harness.shutdown-unverified', {'operation': 'account-read'}),
            ('ACCOUNT_BINDING_CHANGED', 'catalog.account-binding-changed', {}),
        )
        expected = {}
        for source in ('catalog:expired-discovery', 'outside'):
            self.assertIsNone(connection.execute('SELECT 1 FROM evaluation_catalog WHERE discovery_id=?',
                                                  (source.removeprefix('catalog:'),)).fetchone())
            for index, (old, code, params) in enumerate(cases):
                buddy_id = f'{source}-{index}'
                connection.execute('INSERT INTO buddies(buddy_id,label,harness,provider,model,effort,available,source,unavailable_reason) '
                                   'VALUES(?,?,?,?,?,?,0,?,?)',
                                   (buddy_id, buddy_id, 'codex', 'fixture', buddy_id, 'low', source, old))
                expected[buddy_id] = (code, params)
        connection.commit()
        upgrade_schema_24_to_25(fixture['state'])
        for buddy_id, (code, params) in expected.items():
            with self.subTest(buddy=buddy_id):
                row = connection.execute('SELECT unavailable_code,unavailable_params_json,unavailable_content '
                                         'FROM buddies WHERE buddy_id=?', (buddy_id,)).fetchone()
                self.assertEqual(row['unavailable_code'], code)
                self.assertEqual(json.loads(row['unavailable_params_json']), params)
                self.assertIsNone(row['unavailable_content'])

    def test_blackboard_constant_wins_over_identical_native_description(self):
        fixture, connection = self.restored('catalog-fact-collision')
        native = 'not present in the latest confirmed native discovery'
        connection.execute('UPDATE buddies SET available=0,unavailable_reason=? WHERE model=?',
                           (native, 'fixture-beta'))
        for discovery_id, payload_json in connection.execute('SELECT discovery_id,payload_json FROM evaluation_catalog').fetchall():
            payload = json.loads(payload_json)
            for provider in payload['providers']:
                for model in provider['models']:
                    if model['id'] == 'fixture-beta':
                        model.update(available=False, unavailableReason=native)
            connection.execute('UPDATE evaluation_catalog SET payload_json=? WHERE discovery_id=?',
                               (json.dumps(payload), discovery_id))
        connection.commit()
        upgrade_schema_24_to_25(fixture['state'])
        row = connection.execute("SELECT unavailable_code,unavailable_content FROM buddies WHERE model='fixture-beta'").fetchone()
        self.assertEqual(tuple(row), ('catalog.model-absent', None))
        contents = [model['unavailableContent']
                    for (payload_json,) in connection.execute('SELECT payload_json FROM evaluation_catalog')
                    for provider in json.loads(payload_json)['providers'] for model in provider['models']
                    if model['id'] == 'fixture-beta']
        self.assertTrue(contents)
        self.assertEqual(contents, [native] * len(contents))

    def test_unrelated_historical_native_text_does_not_override_catalog_fact(self):
        fixture, connection = self.restored('catalog-fact-source-binding')
        old_text = 'not present in the latest confirmed native discovery'
        linked = connection.execute("SELECT source FROM buddies WHERE model='fixture-beta'").fetchone()[0]
        self.assertTrue(linked.startswith('catalog:'))
        connection.execute('UPDATE buddies SET available=0,unavailable_reason=? WHERE model=?',
                           (old_text, 'fixture-beta'))
        other = connection.execute('SELECT discovery_id,payload_json FROM evaluation_catalog WHERE discovery_id!=?',
                                   (linked[len('catalog:'):],)).fetchone()
        payload = json.loads(other['payload_json'])
        next(model for provider in payload['providers'] for model in provider['models']
             if model['id'] == 'fixture-beta').update(available=False, unavailableReason=old_text)
        connection.execute('UPDATE evaluation_catalog SET payload_json=? WHERE discovery_id=?',
                           (json.dumps(payload), other['discovery_id']))
        connection.commit()
        upgrade_schema_24_to_25(fixture['state'])
        row = connection.execute("SELECT unavailable_code,unavailable_content FROM buddies WHERE model='fixture-beta'").fetchone()
        self.assertEqual(tuple(row), ('catalog.model-absent', None))

    def test_unresolved_catalog_source_rolls_back(self):
        fixture, connection = self.restored('catalog-source-unresolved')
        connection.execute("UPDATE buddies SET source='catalog:truncated',available=0,"
                           "unavailable_reason='native description without retained evidence' "
                           "WHERE model='fixture-beta'")
        connection.commit()
        before = backup.database_snapshot(connection, exact=True)
        with self.assertRaises(BoardError) as caught:
            upgrade_schema_24_to_25(fixture['state'])
        self.assertEqual(caught.exception.code, 'UPGRADE_INVALID_BOARD')
        self.assertEqual(backup.database_snapshot(connection, exact=True), before)

    def test_native_fallback_ambiguity_rolls_back(self):
        fixture, connection = self.restored('native-fallback-ambiguous')
        payload_json = connection.execute('SELECT payload_json FROM evaluation_catalog LIMIT 1').fetchone()[0]
        payload = json.loads(payload_json)
        payload['providers'][0]['models'][0].update(
            available=False, unavailableReason='native route is unavailable')
        discovery_id = connection.execute('SELECT discovery_id FROM evaluation_catalog LIMIT 1').fetchone()[0]
        connection.execute('UPDATE evaluation_catalog SET payload_json=? WHERE discovery_id=?',
                           (json.dumps(payload), discovery_id))
        connection.commit()
        before = backup.database_snapshot(connection, exact=True)
        with self.assertRaises(BoardError) as caught:
            upgrade_schema_24_to_25(fixture['state'])
        self.assertEqual(caught.exception.code, 'UPGRADE_INVALID_BOARD')
        self.assertEqual(backup.database_snapshot(connection, exact=True), before)

    def test_native_discovery_uppercase_reason_remains_content(self):
        fixture, connection = self.restored('native-discovery-uppercase')
        observation_id = connection.execute('SELECT observation_id FROM catalog_current WHERE harness=?',
                                            ('codex',)).fetchone()[0]
        response = json.loads(connection.execute('SELECT response_json FROM catalog_observations WHERE observation_id=?',
                                                 (observation_id,)).fetchone()[0])
        response['catalog']['discoveries'][0].update(status='unknown', reason='NATIVE_UPPERCASE_DETAIL')
        connection.execute('UPDATE catalog_observations SET response_json=? WHERE observation_id=?',
                           (json.dumps(response), observation_id))
        connection.execute("UPDATE catalog_current SET reason='NATIVE_UPPERCASE_DETAIL' WHERE harness='codex'")
        connection.commit()
        upgrade_schema_24_to_25(fixture['state'])
        response = json.loads(connection.execute('SELECT response_json FROM catalog_observations WHERE observation_id=?',
                                                 (observation_id,)).fetchone()[0])
        discovery = response['catalog']['discoveries'][0]
        self.assertEqual(discovery['reason'], {'code': 'catalog.native-unknown', 'params': {'harness': 'codex'}})
        self.assertEqual(discovery['content'], 'NATIVE_UPPERCASE_DETAIL')
        self.assertEqual(json.loads(connection.execute('SELECT reason FROM catalog_current').fetchone()[0]),
                         discovery['reason'])

    def test_old_controller_timeout_receipt_uses_its_native_reason(self):
        fixture, connection = self.restored('native-timeout-receipt')
        # The schema-24 execution controller emitted this reason when its
        # bounded metadata wait expired. Another harness may succeed too.
        old = {'source': 'dsh-acp-session-config; native-discovery',
               'providers': [{'harness': 'dsh', 'provider': 'fixture', 'models': []}],
               'warnings': [],
               'discoveries': [{'harness': 'dsh', 'status': 'complete',
                                'accountStatus': 'not-applicable'},
                               {'harness': 'zcode', 'status': 'unknown',
                                'accountStatus': 'unknown',
                                'reason': 'native-metadata-wait-expired'}]}
        connection.execute('INSERT INTO evaluation_catalog(discovery_id,discovered_at,source,payload_json,created_at) '
                           'VALUES(?,?,?,?,?)', ('old-native-timeout', '2026-10-16T00:00:00Z',
                                               old['source'], json.dumps(old), '2026-10-16T00:00:00Z'))
        connection.execute('INSERT INTO catalog_observations(request_id,created_at,completed_at,response_json,harnesses_json) '
                           'VALUES(?,?,?,?,?)', ('old-native-timeout', '2026-10-16T00:00:00Z',
                                               '2026-10-16T00:00:00Z',
                                               json.dumps({'catalog': old, 'buddies': []}), '[]'))
        observation_id = connection.execute('SELECT observation_id FROM catalog_observations WHERE request_id=?',
                                            ('old-native-timeout',)).fetchone()[0]
        connection.execute('INSERT INTO catalog_current(harness,observation_id,status,reason,updated_at) '
                           'VALUES(?,?,?,?,?)', ('zcode', observation_id, 'unknown',
                                               'native-metadata-wait-expired', '2026-10-16T00:00:00Z'))
        connection.commit()
        upgrade_schema_24_to_25(fixture['state'])
        stored = json.loads(connection.execute('SELECT payload_json FROM evaluation_catalog WHERE discovery_id=?',
                                               ('old-native-timeout',)).fetchone()[0])
        self.assertEqual(stored['discoveries'][1], {'harness': 'zcode', 'status': 'unknown',
                         'accountStatus': 'unknown', 'reason': {'code': 'native-metadata-wait-expired',
                                                                'params': {}}, 'content': None})
        response = json.loads(connection.execute('SELECT response_json FROM catalog_observations WHERE observation_id=?',
                                                 (observation_id,)).fetchone()[0])
        self.assertEqual(response['catalog']['discoveries'][1], stored['discoveries'][1])
        self.assertEqual(json.loads(connection.execute('SELECT reason FROM catalog_current WHERE harness=?',
                                                       ('zcode',)).fetchone()[0]), stored['discoveries'][1]['reason'])

    def test_native_timeout_literal_converts_the_current_catalog_copy(self):
        fixture, connection = self.restored('native-timeout-literal')
        old = {'source': 'current-native-observations', 'providers': [], 'warnings': [],
               'discoveries': [{'harness': 'codex', 'status': 'unknown',
                                'accountStatus': 'unknown', 'reason': 'native-metadata-wait-expired'}]}
        connection.execute('INSERT INTO evaluation_catalog(discovery_id,discovered_at,source,payload_json,created_at) '
                           'VALUES(?,?,?,?,?)', ('literal-timeout', '2026-10-16T00:00:00Z',
                                               old['source'], json.dumps(old), '2026-10-16T00:00:00Z'))
        connection.commit()
        upgrade_schema_24_to_25(fixture['state'])
        converted = json.loads(connection.execute('SELECT payload_json FROM evaluation_catalog WHERE discovery_id=?',
                                                   ('literal-timeout',)).fetchone()[0])
        self.assertEqual(converted['discoveries'][0]['reason'],
                         {'code': 'native-metadata-wait-expired', 'params': {}})
        self.assertIsNone(converted['discoveries'][0]['content'])

    def test_codex_account_recovery_ready_record_allows_older_unknown_probes(self):
        for probe_reason in ('scan-timeout', 'version-unknown'):
            with self.subTest(probe_reason=probe_reason):
                fixture, connection = self.restored('codex-recovered-' + probe_reason)
                record = json.loads(connection.execute(
                    "SELECT record_json FROM harness_health WHERE harness='codex'").fetchone()[0])
                record.update(status='ready', available=True,
                              lastProbe={'status': 'unknown' if probe_reason == 'scan-timeout' else 'complete',
                                         'reasonCode': probe_reason,
                                         'observedAt': '2026-10-16T00:00:00Z', 'candidates': []})
                record.pop('reasonCode', None)
                connection.execute("UPDATE harness_health SET status='ready',record_json=? WHERE harness='codex'",
                                   (json.dumps(record),))
                observation = connection.execute('SELECT observation_id,harnesses_json FROM catalog_observations LIMIT 1').fetchone()
                snapshots = json.loads(observation['harnesses_json'])
                snapshot = next(item for item in snapshots if item['harness'] == 'codex')
                snapshot.update(record)
                snapshot.pop('reasonCode', None)
                connection.execute('UPDATE catalog_observations SET harnesses_json=? WHERE observation_id=?',
                                   (json.dumps(snapshots), observation['observation_id']))
                connection.commit()
                upgrade_schema_24_to_25(fixture['state'])
                upgraded = json.loads(connection.execute(
                    "SELECT record_json FROM harness_health WHERE harness='codex'").fetchone()[0])
                self.assertIsNone(upgraded['reason'])
                self.assertEqual(upgraded['lastProbe']['reason']['code'], 'harness.' + probe_reason)
                snapshots = json.loads(connection.execute('SELECT harnesses_json FROM catalog_observations '
                                                          'WHERE observation_id=?',
                                                          (observation['observation_id'],)).fetchone()[0])
                self.assertIsNone(next(item for item in snapshots if item['harness'] == 'codex')['reason'])

    def test_old_unknown_scan_claim_gains_the_read_time_reason(self):
        fixture, connection = self.restored('empty-scan-claim')
        # Schema 24's refresh and catalog scan claims could create this row
        # before any native probe wrote a record.
        connection.execute("UPDATE harness_health SET status='unknown',record_json='{}' WHERE harness='codex'")
        connection.commit()
        upgrade_schema_24_to_25(fixture['state'])
        record = json.loads(connection.execute("SELECT record_json FROM harness_health WHERE harness='codex'").fetchone()[0])
        self.assertEqual(record['reason'], {'code': 'harness.not-checked', 'params': {'operation': 'probe'}})

        fixture, connection = self.restored('unprobed-initial-health')
        connection.execute("UPDATE harness_health SET status='unknown',record_json=? WHERE harness='codex'",
                           (json.dumps({'lastProbe': None}),))
        connection.commit()
        upgrade_schema_24_to_25(fixture['state'])
        record = json.loads(connection.execute("SELECT record_json FROM harness_health WHERE harness='codex'").fetchone()[0])
        self.assertEqual(record['reason'], {'code': 'harness.not-checked', 'params': {'operation': 'probe'}})

        fixture, connection = self.restored('unknown-probe-after-empty-claim')
        old = {'lastProbe': {'status': 'unknown', 'reasonCode': 'scan-timeout',
                             'observedAt': '2026-10-16T00:00:00Z', 'candidates': []}}
        connection.execute("UPDATE harness_health SET status='unknown',record_json=? WHERE harness='codex'",
                           (json.dumps(old),))
        connection.commit()
        upgrade_schema_24_to_25(fixture['state'])
        record = json.loads(connection.execute("SELECT record_json FROM harness_health WHERE harness='codex'").fetchone()[0])
        self.assertEqual(record['reason'], {'code': 'harness.not-checked', 'params': {'operation': 'probe'}})
        self.assertEqual(record['lastProbe']['reason'],
                         {'code': 'harness.scan-timeout', 'params': {'operation': 'probe'}})

    def test_unprobed_health_observation_views_use_the_underlying_record_shape(self):
        raw_records = ({}, {'lastProbe': None}, {
            'lastProbe': {'status': 'unknown', 'reasonCode': 'scan-timeout',
                          'observedAt': '2026-10-16T00:00:00Z', 'candidates': []},
        })
        for index, raw_record in enumerate(raw_records):
            with self.subTest(raw_record=raw_record):
                fixture, connection = self.restored('unprobed-health-view-' + str(index))
                observation = connection.execute('SELECT observation_id,harnesses_json FROM catalog_observations '
                                                 'LIMIT 1').fetchone()
                snapshots = json.loads(observation['harnesses_json'])
                original = next(item for item in snapshots if item['harness'] == 'codex')
                # 0.42.0 read_health returned the raw record plus these view
                # fields; it added no root reasonCode for an existing row.
                snapshot = {key: original[key] for key in (
                    'billingByProvider', 'quota', 'quotaRouting', 'harness', 'available', 'status',
                    'account', 'revision', 'manualPath', 'checkedAt', 'expiresAt', 'scanAfter',
                    'systemSandbox', 'readOnlyStructured',
                )}
                snapshot.update(raw_record)
                snapshot.update(status='unknown', available=False, revision=1,
                                billingByProvider={}, manualPath=None, checkedAt=None, expiresAt=None,
                                account={'harness': 'codex', 'status': 'unknown', 'accountType': None,
                                         'checkedAt': None, 'reasonCode': 'ACCOUNT_NOT_CHECKED',
                                         'guidance': 'Refresh the harness to obtain a native observation.'})
                snapshots[snapshots.index(original)] = snapshot
                connection.execute('UPDATE catalog_observations SET harnesses_json=? WHERE observation_id=?',
                                   (json.dumps(snapshots), observation['observation_id']))
                connection.commit()
                upgrade_schema_24_to_25(fixture['state'])
                stored = json.loads(connection.execute('SELECT harnesses_json FROM catalog_observations '
                                                        'WHERE observation_id=?',
                                                        (observation['observation_id'],)).fetchone()[0])
                upgraded = next(item for item in stored if item['harness'] == 'codex')
                self.assertEqual(upgraded['reason'], {'code': 'harness.not-checked', 'params': {'operation': 'probe'}})
                self.assertEqual(upgraded['account']['reason'], {'code': 'account.not-checked', 'params': {}})
                for key in ('billingByProvider', 'quota', 'quotaRouting', 'harness', 'available', 'status',
                            'revision', 'manualPath', 'checkedAt', 'expiresAt', 'scanAfter', 'systemSandbox'):
                    self.assertEqual(upgraded[key], snapshot[key])
                if index == 2:
                    self.assertEqual(upgraded['lastProbe']['reason'],
                                     {'code': 'harness.scan-timeout', 'params': {'operation': 'probe'}})
                else:
                    self.assertEqual(upgraded.get('lastProbe'), raw_record.get('lastProbe'))

    def test_unknown_health_shape_without_reason_rolls_back(self):
        fixture, connection = self.restored('unknown-health-shape')
        connection.execute("UPDATE harness_health SET status='unknown',record_json=? WHERE harness='codex'",
                           (json.dumps({'unexpected': True}),))
        connection.commit()
        before = backup.database_snapshot(connection, exact=True)
        with self.assertRaises(BoardError) as caught:
            upgrade_schema_24_to_25(fixture['state'])
        self.assertEqual(caught.exception.code, 'UPGRADE_INVALID_BOARD')
        self.assertEqual(backup.database_snapshot(connection, exact=True), before)

    def test_account_read_protocol_code_stays_diagnostic(self):
        fixture, connection = self.restored('account-read-diagnostic')
        health = json.loads(connection.execute('SELECT record_json FROM harness_health').fetchone()[0])
        health['reasonCode'] = 'deadline'
        health['accountReadFailure'] = {'reasonCode': 'deadline', 'status': 'unknown',
                                        'message': 'native deadline detail'}
        connection.execute('UPDATE harness_health SET record_json=?', (json.dumps(health),))
        connection.commit()
        upgrade_schema_24_to_25(fixture['state'])
        upgraded = json.loads(connection.execute('SELECT record_json FROM harness_health').fetchone()[0])
        self.assertEqual(upgraded['reason'], {'code': 'harness.account-read-failed',
                                              'params': {'operation': 'account-read'}})
        self.assertEqual(upgraded['accountReadFailure']['reason'], upgraded['reason'])
        self.assertEqual(upgraded['accountReadFailure']['diagnosticCode'], 'deadline')
        self.assertEqual(upgraded['accountReadFailure']['message'], 'native deadline detail')

    def test_health_without_embedded_harness_uses_table_identity_for_history(self):
        fixture, connection = self.restored('health-history-owner')
        connection.execute("UPDATE harness_health SET record_json=? WHERE harness='codex'",
                           (json.dumps({'reasonCode': 'OLD_HEALTH_CODE', 'available': False}),))
        connection.commit()
        upgrade_schema_24_to_25(fixture['state'])
        health = json.loads(connection.execute('SELECT record_json FROM harness_health').fetchone()[0])
        self.assertEqual(health['reason'],
                         {'code': 'history.reason-unavailable', 'params': {'recordId': 'codex'}})

    def test_old_catalog_refresh_failure_follows_its_invalidation_copies(self):
        fixture, connection = self.restored('catalog-refresh-failure')
        old_code = 'CATALOG_UNAVAILABLE'
        old_health = json.loads(connection.execute(
            "SELECT record_json FROM harness_health WHERE harness='codex'").fetchone()[0])
        old_health.update(reasonCode=old_code, available=False)
        connection.execute("UPDATE harness_health SET status='unhealthy',record_json=? WHERE harness='codex'",
                           (json.dumps(old_health),))
        connection.execute("UPDATE buddies SET available=0,unavailable_reason=? WHERE model='fixture-beta'",
                           (old_code,))
        observation = connection.execute('SELECT observation_id,harnesses_json FROM catalog_observations LIMIT 1').fetchone()
        snapshots = json.loads(observation['harnesses_json'])
        next(item for item in snapshots if item['harness'] == 'codex').update(reasonCode=old_code,
                                                                            available=False)
        connection.execute('UPDATE catalog_observations SET harnesses_json=? WHERE observation_id=?',
                           (json.dumps(snapshots), observation['observation_id']))
        self.old_event(connection, 'harness.invalidated',
                       payload={'harness': 'codex', 'reasonCode': old_code})
        connection.commit()

        upgrade_schema_24_to_25(fixture['state'])
        expected = {'code': 'catalog.discovery-failed',
                    'params': {'harness': 'codex', 'errorCode': old_code}}
        health = json.loads(connection.execute(
            "SELECT record_json FROM harness_health WHERE harness='codex'").fetchone()[0])
        self.assertEqual(health['reason'], expected)
        snapshots = json.loads(connection.execute('SELECT harnesses_json FROM catalog_observations WHERE observation_id=?',
                                                  (observation['observation_id'],)).fetchone()[0])
        self.assertEqual(next(item for item in snapshots if item['harness'] == 'codex')['reason'], expected)
        buddy = connection.execute("SELECT unavailable_code,unavailable_params_json FROM buddies WHERE model='fixture-beta'").fetchone()
        self.assertEqual(buddy['unavailable_code'], expected['code'])
        self.assertEqual(json.loads(buddy['unavailable_params_json']), expected['params'])
        event = json.loads(connection.execute("SELECT payload_json FROM events WHERE kind='harness.invalidated' "
                                              'ORDER BY seq DESC LIMIT 1').fetchone()[0])
        self.assertEqual(event['reason'], expected)
        self.assertNotIn('reasonCode', event)

    def test_all_old_harness_events_convert_reason_codes(self):
        fixture, connection = self.restored('harness-event-reasons')
        cases = (
            ('harness.checked', {'harness': 'codex', 'status': 'unhealthy', 'revision': 2, 'version': None,
                                 'reasonCode': 'HARNESS_HANDSHAKE_FAILED'}, 'harness.probe-failed'),
            ('harness.probe_unknown', {'harness': 'codex', 'revision': 3, 'reasonCode': 'scan-timeout'},
             'harness.scan-timeout'),
            ('harness.account_read_failed', {'harness': 'codex', 'reasonCode': 'HARNESS_ACCOUNT_READ_FAILED',
                                             'revision': 2, 'currentRevision': 2,
                                             'accountReadFailure': {'reasonCode': 'HARNESS_ACCOUNT_READ_FAILED',
                                                                    'errorType': 'OSError', 'message': 'native failure',
                                                                    'observedAt': '2026-10-16T00:00:00Z', 'status': 'failed'}},
             'harness.account-read-failed'),
            ('harness.account_read_unknown', {'harness': 'codex', 'reasonCode': 'deadline', 'revision': 2, 'currentRevision': 2,
                                              'accountReadFailure': {'reasonCode': 'deadline',
                                                                     'errorType': 'CodexProtocolError', 'message': 'native wait expired',
                                                                     'observedAt': '2026-10-16T00:00:00Z', 'status': 'unknown'}},
             'harness.account-read-failed'),
            ('harness.invalidated', {'harness': 'codex', 'reasonCode': 'HARNESS_START_FAILED'},
             'harness.launch-failed'),
        )
        event_seqs = []
        for kind, payload, _ in cases:
            event_seqs.append(self.old_event(connection, kind, payload=payload))
        connection.commit()
        upgrade_schema_24_to_25(fixture['state'])
        for (kind, _, code), seq in zip(cases, event_seqs):
            with self.subTest(kind=kind):
                payload = json.loads(connection.execute('SELECT payload_json FROM events WHERE seq=?',
                                                        (seq,)).fetchone()[0])
                self.assertNotIn('reasonCode', payload)
                self.assertEqual(payload['reason']['code'], code)
                if 'accountReadFailure' in payload:
                    self.assertEqual(payload['accountReadFailure']['reason']['code'], code)
                    self.assertNotIn('reasonCode', payload['accountReadFailure'])

    def test_service_catalog_copies_use_board_reason_tables(self):
        for source, old, expected in (
            ('current-native-observations', 'ACCOUNT_STATUS_UNKNOWN', 'catalog.account-status-unknown'),
            ('current-native-observations', 'CATALOG_UNAVAILABLE', 'catalog.discovery-failed'),
            ('harness-health', 'HARNESS_NOT_CHECKED', 'harness.not-checked'),
        ):
            with self.subTest(source=source, old=old):
                fixture, connection = self.restored('service-copy-' + old)
                payload = {'source': source, 'providers': [], 'warnings': [],
                           'discoveries': [{'harness': 'codex', 'status': 'unknown',
                                            'accountStatus': 'unknown', 'reason': old}]}
                connection.execute('INSERT INTO evaluation_catalog(discovery_id,discovered_at,source,payload_json,created_at) '
                                   'VALUES(?,?,?,?,?)', (source + old, '2026-10-16T00:00:00Z',
                                                       source, json.dumps(payload), '2026-10-16T00:00:00Z'))
                connection.commit()
                upgrade_schema_24_to_25(fixture['state'])
                converted = json.loads(connection.execute('SELECT payload_json FROM evaluation_catalog '
                                                          'WHERE discovery_id=?',
                                                          (source + old,)).fetchone()[0])
                discovery = converted['discoveries'][0]
                self.assertEqual(discovery['reason']['code'], expected)
                self.assertIsNone(discovery['content'])

    def test_current_catalog_model_health_copy_keeps_the_board_reason(self):
        fixture, connection = self.restored('current-model-health-copy')
        observation = connection.execute('SELECT response_json,harnesses_json FROM catalog_observations '
                                         'WHERE response_json IS NOT NULL LIMIT 1').fetchone()
        response = json.loads(observation['response_json'])
        payload = response['catalog']
        payload['source'] = 'current-native-observations'
        response['discoveryId'] = 'old-health-copy'
        # The old current() reader replaced each model's reason with this
        # constant whenever the harness was unhealthy, before refresh recorded it.
        for provider in payload['providers']:
            for model in provider['models']:
                model.update(available=False, unavailableReason='HARNESS_UNHEALTHY')
        for buddy in response['buddies']:
            buddy.update(available=False, unavailableReason='HARNESS_UNHEALTHY')
        connection.execute('INSERT INTO evaluation_catalog(discovery_id,discovered_at,source,payload_json,created_at) '
                           'VALUES(?,?,?,?,?)', ('old-health-copy', '2026-10-16T00:00:00Z', payload['source'],
                                               json.dumps(payload), '2026-10-16T00:00:00Z'))
        connection.execute('INSERT INTO catalog_observations(request_id,created_at,completed_at,response_json,harnesses_json) '
                           'VALUES(?,?,?,?,?)', ('old-health-copy', '2026-10-16T00:00:00Z', '2026-10-16T00:00:00Z',
                                               json.dumps(response), observation['harnesses_json']))
        connection.commit()
        upgrade_schema_24_to_25(fixture['state'])
        converted = json.loads(connection.execute('SELECT payload_json FROM evaluation_catalog '
                                                  "WHERE discovery_id='old-health-copy'").fetchone()[0])
        expected = {'code': 'harness.probe-failed', 'params': {'operation': 'probe'}}
        for provider in converted['providers']:
            for model in provider['models']:
                self.assertEqual(model['unavailableReason'], expected)
                self.assertIsNone(model['unavailableContent'])
        recorded = json.loads(connection.execute("SELECT response_json FROM catalog_observations "
                                                 "WHERE request_id='old-health-copy'").fetchone()[0])
        self.assertEqual(recorded['catalog'], converted)
        for buddy in recorded['buddies']:
            self.assertEqual(buddy['unavailableReason'], expected)
            self.assertIsNone(buddy['unavailableContent'])

    def test_known_native_warning_becomes_reason_and_unknown_source_rolls_back(self):
        fixture, connection = self.restored('native-warning')
        row = connection.execute('SELECT discovery_id,payload_json FROM evaluation_catalog LIMIT 1').fetchone()
        payload = json.loads(row['payload_json'])
        payload['source'] = 'codex-native-app-server'
        payload['warnings'] = ['A model with no usable identity or reasoning effort was omitted']
        connection.execute('UPDATE evaluation_catalog SET payload_json=? WHERE discovery_id=?',
                           (json.dumps(payload), row['discovery_id']))
        connection.commit()
        upgrade_schema_24_to_25(fixture['state'])
        converted = json.loads(connection.execute('SELECT payload_json FROM evaluation_catalog WHERE discovery_id=?',
                                                  (row['discovery_id'],)).fetchone()[0])
        self.assertEqual(converted['warnings'],
                         [{'reason': {'code': 'catalog.model-identity-or-effort-omitted', 'params': {}}}])

        fixture, connection = self.restored('warning-unknown-owner')
        row = connection.execute('SELECT discovery_id,payload_json FROM evaluation_catalog LIMIT 1').fetchone()
        payload = json.loads(row['payload_json'])
        payload['source'] = 'unrecognized-source'
        payload['warnings'] = ['A model with no usable identity or reasoning effort was omitted']
        connection.execute('UPDATE evaluation_catalog SET payload_json=? WHERE discovery_id=?',
                           (json.dumps(payload), row['discovery_id']))
        connection.commit()
        before = backup.database_snapshot(connection, exact=True)
        with self.assertRaises(BoardError) as caught:
            upgrade_schema_24_to_25(fixture['state'])
        self.assertEqual(caught.exception.code, 'UPGRADE_INVALID_BOARD')
        self.assertEqual(backup.database_snapshot(connection, exact=True), before)

    def test_quota_catalog_warnings_are_removed_from_both_current_copies(self):
        fixture, connection = self.restored('old-quota-warnings')
        warnings = [{'code': code, 'message': code} for code in (
            'HARNESS_RATE_LIMIT_REPORTED', 'HARNESS_QUOTA_LIMIT_REPORTED',
            'HARNESS_QUOTA_NEAR_LIMIT')]
        warnings.append({'message': 'native detail'})
        for observation_id, raw in connection.execute(
                'SELECT observation_id,response_json FROM catalog_observations WHERE response_json IS NOT NULL').fetchall():
            response = json.loads(raw)
            response['catalog']['warnings'] = warnings
            connection.execute('UPDATE catalog_observations SET response_json=? WHERE observation_id=?',
                               (canonical_json(response), observation_id))
        for discovery_id, raw in connection.execute('SELECT discovery_id,payload_json FROM evaluation_catalog').fetchall():
            payload = json.loads(raw)
            payload['warnings'] = warnings
            connection.execute('UPDATE evaluation_catalog SET payload_json=? WHERE discovery_id=?',
                               (canonical_json(payload), discovery_id))
        connection.commit()
        upgrade_schema_24_to_25(fixture['state'])
        for raw, in connection.execute('SELECT response_json FROM catalog_observations WHERE response_json IS NOT NULL'):
            converted = json.loads(raw)['catalog']['warnings']
            self.assertEqual(converted, [{'reason': {'code': 'catalog.native-warning', 'params': {}},
                                          'content': 'native detail'}])
        for raw, in connection.execute('SELECT payload_json FROM evaluation_catalog'):
            converted = json.loads(raw)['warnings']
            self.assertEqual(converted, [{'reason': {'code': 'catalog.native-warning', 'params': {}},
                                          'content': 'native detail'}])

    def test_unknown_catalog_warning_still_refuses_upgrade(self):
        fixture, connection = self.restored('unknown-quota-warning')
        discovery_id, raw = connection.execute('SELECT discovery_id,payload_json FROM evaluation_catalog LIMIT 1').fetchone()
        payload = json.loads(raw)
        payload['warnings'] = [{'code': 'HARNESS_UNKNOWN_WARNING', 'message': 'unknown'}]
        connection.execute('UPDATE evaluation_catalog SET payload_json=? WHERE discovery_id=?',
                           (canonical_json(payload), discovery_id))
        connection.commit()
        before = backup.database_snapshot(connection, exact=True)
        with self.assertRaises(BoardError) as caught:
            upgrade_schema_24_to_25(fixture['state'])
        self.assertEqual(caught.exception.code, 'UPGRADE_INVALID_BOARD')
        self.assertEqual(backup.database_snapshot(connection, exact=True), before)

    def test_upgraded_and_fresh_buddies_enforce_reason_content_constraints(self):
        fixture, upgraded = self.restored('buddy-reason-constraints')
        upgrade_schema_24_to_25(fixture['state'])
        fresh = Database(self.root / 'fresh-buddy-reason-constraints')
        fresh.initialize()
        self.addCleanup(fresh.close)
        with fresh.connect() as baseline:
            for connection in (upgraded, baseline):
                with self.subTest(database='upgrade' if connection is upgraded else 'fresh'):
                    if connection is baseline:
                        connection.execute("INSERT INTO buddies(buddy_id,label,harness,provider,model,effort,source) "
                                           "VALUES('test','test','codex','test','test','off','test')")
                    with self.assertRaises(sqlite3.IntegrityError):
                        connection.execute("UPDATE buddies SET unavailable_code='catalog.model-absent',"
                                           "unavailable_params_json=NULL WHERE buddy_id="
                                           "(SELECT buddy_id FROM buddies LIMIT 1)")
                    with self.assertRaises(sqlite3.IntegrityError):
                        connection.execute("UPDATE buddies SET unavailable_code='catalog.model-absent',"
                                           "unavailable_params_json='{}',unavailable_content='native' "
                                           "WHERE buddy_id=(SELECT buddy_id FROM buddies LIMIT 1)")
                    with self.assertRaises(sqlite3.IntegrityError):
                        connection.execute("UPDATE buddies SET unavailable_code=NULL,"
                                           "unavailable_params_json=NULL,unavailable_content='native' "
                                           "WHERE buddy_id=(SELECT buddy_id FROM buddies LIMIT 1)")

    def test_foreign_key_failure_rolls_back_schema_and_stock(self):
        fixture, connection = self.restored('invalid-foreign-key')
        connection.execute('INSERT INTO workflow_fixes(run_id,fixed_run_id,created_at) VALUES(?,?,?)',
                           (fixture['stableIds']['accepted'], 'missing-run', '2026-01-01T00:00:00Z'))
        connection.commit()
        before = backup.database_snapshot(connection, exact=True)
        schema = list(connection.execute('SELECT * FROM sqlite_master ORDER BY type,name'))
        with self.assertRaises(BoardError) as caught:
            upgrade_schema_24_to_25(fixture['state'])
        self.assertEqual(caught.exception.code, 'UPGRADE_INVALID_BOARD')
        self.assertEqual(backup.database_snapshot(connection, exact=True), before)
        self.assertEqual(list(connection.execute('SELECT * FROM sqlite_master ORDER BY type,name')), schema)

    def test_pending_upgrade_rejects_every_other_schema_without_writes(self):
        fixture, connection = self.restored('unsupported-upgrade')
        for version in ('23', '25', '26', '999', 'unknown', None):
            with self.subTest(schema=version):
                connection.execute("DELETE FROM meta WHERE key='schema_version'")
                if version is not None:
                    connection.execute("INSERT INTO meta VALUES('schema_version',?)", (version,))
                connection.commit()
                before = (fixture['state'] / 'board.sqlite3').read_bytes()
                with self.assertRaises(BoardError) as caught:
                    upgrade_schema_24_to_25(fixture['state'])
                self.assertEqual(caught.exception.code, 'UNSUPPORTED_SCHEMA')
                self.assertEqual((fixture['state'] / 'board.sqlite3').read_bytes(), before)

    def test_current_startup_refuses_the_unmodified_upgrade_base(self):
        fixture, connection = self.restored('startup-base')
        connection.close()
        state = fixture['state']
        state.chmod(0o750)
        before = {path.relative_to(state): path.read_bytes() for path in state.rglob('*') if path.is_file()}
        database = Database(state)
        self.addCleanup(database.close)
        with self.assertRaisesRegex(Corruption, 'only schema 25'):
            database.initialize()
        self.assertEqual({path.relative_to(state): path.read_bytes() for path in state.rglob('*') if path.is_file()}, before)
        self.assertEqual(state.stat().st_mode & 0o777, 0o750)

    def test_new_and_upgraded_deletion_tables_enforce_the_same_constraints(self):
        fixture, upgraded = self.restored('deletion-constraints')
        upgrade_schema_24_to_25(fixture['state'])
        fresh = Database(self.root / 'fresh-constraints')
        fresh.initialize()
        self.addCleanup(fresh.close)
        with fresh.connect() as baseline:
            for label, connection in (('upgraded', upgraded), ('fresh', baseline)):
                with self.subTest(schema=label):
                    connection.execute('PRAGMA foreign_keys=ON')
                    self.assertEqual([row['name'] for row in connection.execute('PRAGMA table_info(macro_task_deletions)')],
                                     ['deletion_id', 'target_kind', 'target_id', 'state', 'started_at', 'deleted_at', 'micro_task_count'])
                    insert = ('INSERT INTO macro_task_deletions(deletion_id,target_kind,target_id,state,started_at,deleted_at,micro_task_count)'
                              ' VALUES(?,?,?,?,?,?,?)')
                    for kind in ('macro-task', 'micro-task-tree'):
                        for state in ('deleting', 'deleted'):
                            identity = kind + '-' + state
                            connection.execute(insert, (identity, kind, identity, state, 'started',
                                                        'finished' if state == 'deleted' else None, 2))
                    for kind, state, deleted_at, count in (
                        ('other', 'deleting', None, 0), ('macro-task', 'other', None, 0),
                        ('macro-task', 'deleted', None, 0), ('macro-task', 'deleting', 'finished', 0),
                        ('macro-task', 'deleting', None, None),
                    ):
                        with self.subTest(kind=kind, state=state, deletedAt=deleted_at, count=count), self.assertRaises(sqlite3.IntegrityError):
                            connection.execute(insert, ('invalid', kind, 'invalid', state, 'started', deleted_at, count))
                    with self.assertRaises(sqlite3.IntegrityError):
                        connection.execute(insert, ('duplicate-target', 'macro-task', 'macro-task-deleting', 'deleting', 'started', None, 2))
                    connection.execute("INSERT INTO commands(command_id,kind,request_hash,response_json,created_at)"
                                       " VALUES('deletion-command','fixture','hash','{}','started')")
                    self.assertIsNone(connection.execute("SELECT deletion_id FROM commands WHERE command_id='deletion-command'").fetchone()[0])
                    with self.assertRaises(sqlite3.IntegrityError):
                        connection.execute("UPDATE commands SET deletion_id='missing' WHERE command_id='deletion-command'")
                    connection.execute("UPDATE commands SET deletion_id='macro-task-deleted' WHERE command_id='deletion-command'")
                    with self.assertRaises(sqlite3.IntegrityError):
                        connection.execute("DELETE FROM macro_task_deletions WHERE deletion_id='macro-task-deleted'")
                    self.assertEqual(connection.execute('PRAGMA foreign_key_check').fetchall(), [])

    def test_started_verification_preserves_command_fingerprints_and_rejects_tampering(self):
        fixture, connection = self.restored('started-stop')
        self.insert_stop_receipt(fixture, connection)
        upgrade_schema_24_to_25(fixture['state'])
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
        # A NULL fingerprint is retained stock from submissions that predate
        # fingerprinting; the current reader still refuses their replay.
        connection.execute('UPDATE workflow_runs SET request_fingerprint=NULL')
        connection.commit()
        connection.close()
        upgrade_schema_24_to_25(fixture['state'])
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

    def old_request(self, connection, run_id, request_id, *, payload, summary='program text', kind='attention',
                    turn_id=None, child_task_id=None):
        raw = payload if isinstance(payload, str) else json.dumps(payload)
        connection.execute("INSERT INTO workflow_requests(request_id,run_id,turn_id,child_task_id,kind,summary,payload_json,"
                           "state,expected_revision,created_at,updated_at) VALUES(?,?,?,?,?,?,?,'open',1,'created','updated')",
                           (request_id, run_id, turn_id, child_task_id, kind, summary, raw))

    def test_system_requests_convert_by_markers_and_proxies_follow_the_original_form(self):
        fixture, connection = self.restored('system-requests')
        run_id = fixture['stableIds']['delivered']
        other = fixture['stableIds']['accepted']
        turn_id = connection.execute('SELECT turn_id FROM workflow_turns WHERE run_id=?', (run_id,)).fetchone()[0]
        connection.execute("UPDATE workflow_turns SET disposition='waiting' WHERE turn_id=?", (turn_id,))
        sentences = {'summary': 'old service sentence', 'attempted': 'old attempt', 'neededWork': ['old work'],
                     'expectedArtifacts': [], 'acceptance': 'old acceptance'}
        self.old_request(connection, run_id, 'scope', payload={**sentences, 'reason': 'workspace-scope-violation',
                         'conflictIds': ['conflict-a'], 'paths': ['a.py']})
        self.old_request(connection, run_id, 'prep-continuation-a', payload={**sentences,
                         'continuationId': 'continuation-a', 'preparationError': {'code': 'GIT_ERROR', 'message': 'native details'}})
        self.old_request(connection, run_id, 'lead', turn_id=turn_id, summary='The old sentence says completed',
                         payload={**sentences, 'source': 'lead-lifecycle', 'unfinishedOwnedMicroTasks': [],
                                  'unfinishedOwnedMicroTaskTotal': 0})
        for code in ('helpers-settled', 'CONFLICT', 'SHUTDOWN_UNCONFIRMED', 'REVISION_CONFLICT',
                     'ANCESTOR_TERMINAL', 'UPGRADE_IN_PROGRESS', 'ILLEGAL_TRANSITION'):
            self.old_request(connection, run_id, code, kind='helper-report', summary=code,
                             payload={**sentences, 'summary': code, 'helperOutcomes': [{'taskId': other, 'state': 'succeeded'}]})
        origin = {'runId': run_id, 'requestId': 'scope'}
        proxy = {'runId': run_id, 'requestId': 'scope'}
        self.old_request(connection, other, 'scope-proxy', kind='helper-attention', child_task_id=run_id,
                         payload={**sentences, 'reason': 'workspace-scope-violation', 'conflictIds': ['conflict-a'],
                                  'paths': ['a.py'], 'origin': origin, 'proxy': proxy})
        content = '  Lead completion requires every owned micro task\nCONFLICT\n原样  '
        content_payload = ' { "summary" : "CONFLICT", "neededWork" : ["User content"] } '
        self.old_request(connection, run_id, 'worker', kind='assistance', summary=content, payload=content_payload)
        decision_id = connection.execute('SELECT decision_id FROM evaluation_decisions LIMIT 1').fetchone()[0]
        connection.execute('INSERT INTO workflow_routes(decision_id,run_id,owner_generation,state,reason,created_at,updated_at)'
                           " VALUES(?,?,1,'needs-host','No legal coding buddy was selected','created','updated')",
                           (decision_id, run_id))
        routing_payload = json.dumps({'source': 'routing', 'decisionId': decision_id,
                                      'summary': 'No legal coding buddy was selected'})
        self.old_request(connection, run_id, 'routing', summary='No legal coding buddy was selected', payload=routing_payload)
        self.old_request(connection, other, 'worker-proxy', kind='helper-attention', child_task_id=run_id,
                         summary=content, payload={'origin': {'runId': run_id, 'requestId': 'worker'},
                                                  'proxy': {'runId': run_id, 'requestId': 'worker'}})
        event = {'turnId': turn_id, 'requestId': 'lead', 'reason': 'old sentence',
                 'unfinishedOwnedMicroTasks': [], 'unfinishedOwnedMicroTaskTotal': 0}
        connection.execute("INSERT INTO events(kind,task_id,revision,payload_json,created_at) VALUES('workflow.lead_result_refused',?,1,?,'created')",
                           (run_id, json.dumps(event)))
        event_seq = connection.execute('SELECT MAX(seq) FROM events').fetchone()[0]
        turn_raw = connection.execute('SELECT input_json,outcome_json FROM workflow_turns WHERE turn_id=?', (turn_id,)).fetchone()
        connection.commit()
        upgrade_schema_24_to_25(fixture['state'])
        rows = {row['request_id']: row for row in connection.execute('SELECT * FROM workflow_requests')}
        for request_id, code, params in (
            ('scope', 'workspace-scope-violation', {'conflictIds': ['conflict-a'], 'paths': ['a.py']}),
            ('prep-continuation-a', 'workspace.continuation-prepare-failed', {'continuationId': 'continuation-a'}),
            ('lead', 'lead.result-refused', {'disposition': 'waiting', 'unfinished': [], 'total': 0}),
        ):
            row = rows[request_id]
            self.assertIsNone(row['summary'])
            self.assertEqual(row['reason_code'], code)
            self.assertEqual(json.loads(row['reason_params_json']), params)
            self.assertTrue(set(sentences).isdisjoint(json.loads(row['payload_json'])))
            self.assertEqual((row['created_at'], row['updated_at'], row['expected_revision']), ('created', 'updated', 1))
        self.assertEqual(json.loads(rows['scope']['payload_json']), {})
        self.assertEqual(json.loads(rows['prep-continuation-a']['payload_json']), {'preparationError': {'code': 'GIT_ERROR', 'message': 'native details'}})
        self.assertEqual(json.loads(rows['lead']['payload_json']), {})
        for code in ('helpers-settled', 'CONFLICT', 'SHUTDOWN_UNCONFIRMED', 'REVISION_CONFLICT',
                     'ANCESTOR_TERMINAL', 'UPGRADE_IN_PROGRESS', 'ILLEGAL_TRANSITION'):
            self.assertEqual((rows[code]['summary'], rows[code]['reason_code'], rows[code]['reason_params_json']), (None, code, '{}'))
            self.assertEqual(json.loads(rows[code]['payload_json']), {'helperOutcomes': [{'taskId': other, 'state': 'succeeded'}]})
        self.assertEqual(rows['scope-proxy']['reason_params_json'], rows['scope']['reason_params_json'])
        self.assertEqual(json.loads(rows['scope-proxy']['payload_json']), {'origin': origin, 'proxy': proxy})
        self.assertEqual((rows['worker']['summary'], rows['worker']['payload_json']), (content, content_payload))
        self.assertEqual((rows['routing']['summary'], rows['routing']['decision_id'],
                          rows['routing']['reason_code'], json.loads(rows['routing']['payload_json'])),
                         (None, decision_id, 'routing-unusable-selection', {'source': 'routing'}))
        for request_id in ('worker', 'worker-proxy'):
            self.assertIsNone(rows[request_id]['reason_code'])
            self.assertIsNone(rows[request_id]['reason_params_json'])
        self.assertEqual(rows['worker-proxy']['summary'], content)
        event.pop('reason')
        self.assertEqual(json.loads(connection.execute('SELECT payload_json FROM events WHERE seq=?', (event_seq,)).fetchone()[0]), event)
        self.assertEqual(tuple(connection.execute('SELECT input_json,outcome_json FROM workflow_turns WHERE turn_id=?', (turn_id,)).fetchone()), tuple(turn_raw))

    def test_attributed_system_request_without_recoverable_facts_uses_history_reason_and_proxy(self):
        fixture, connection = self.restored('missing-request-facts')
        run_id, other = fixture['stableIds']['delivered'], fixture['stableIds']['accepted']
        self.old_request(connection, run_id, 'missing-facts', payload={'source': 'lead-lifecycle', 'summary': 'old sentence'})
        self.old_request(connection, other, 'missing-proxy', kind='helper-attention', child_task_id=run_id,
                         payload={'source': 'lead-lifecycle', 'summary': 'old sentence',
                                  'origin': {'runId': run_id, 'requestId': 'missing-facts'},
                                  'proxy': {'runId': run_id, 'requestId': 'missing-facts'}})
        connection.commit()
        upgrade_schema_24_to_25(fixture['state'])
        for record_id in ('missing-facts', 'missing-proxy'):
            row = connection.execute('SELECT * FROM workflow_requests WHERE request_id=?', (record_id,)).fetchone()
            self.assertEqual(row['reason_code'], 'history.reason-unavailable')
            self.assertEqual(json.loads(row['reason_params_json']), {'recordId': 'missing-facts'})
            self.assertIsNone(row['summary'])
            payload = json.loads(row['payload_json'])
            self.assertTrue({'summary', 'source'}.isdisjoint(payload))

    def test_unattributable_helper_report_refuses_the_upgrade_and_rolls_back(self):
        fixture, connection = self.restored('unknown-helper-report')
        run_id = fixture['stableIds']['delivered']
        self.old_request(connection, run_id, 'unknown-report', kind='helper-report', summary='unknown text',
                         payload={'summary': 'unknown text', 'helperOutcomes': []})
        connection.commit()
        before = list(connection.execute('SELECT * FROM sqlite_master ORDER BY name'))
        requests = list(connection.execute('SELECT * FROM workflow_requests ORDER BY rowid'))
        with self.assertRaises(BoardError) as caught:
            upgrade_schema_24_to_25(fixture['state'])
        self.assertEqual(caught.exception.code, 'UPGRADE_INVALID_BOARD')
        self.assertEqual(list(connection.execute('SELECT * FROM sqlite_master ORDER BY name')), before)
        self.assertEqual(list(connection.execute('SELECT * FROM workflow_requests ORDER BY rowid')), requests)
        self.assertEqual(connection.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0], '24')

    def test_request_check_requires_exactly_one_content_or_object_reason_form(self):
        fixture, connection = self.restored('request-check')
        connection.commit()
        upgrade_schema_24_to_25(fixture['state'])
        run_id = fixture['stableIds']['delivered']
        fresh = Database(self.root / 'request-check-fresh')
        fresh.initialize()
        # The run FK is not part of this column CHECK and is verified separately.
        with sqlite3.connect(self.root / 'request-check-fresh' / 'board.sqlite3') as baseline:
            for label, db in (('upgraded', connection), ('fresh', baseline)):
                for index, values in enumerate(((None, None, None, None), ('text', 'CONFLICT', '{}', None),
                                                (None, 'CONFLICT', None, None), (None, None, '{}', None),
                                                (None, 'CONFLICT', '[]', None), (None, 'CONFLICT', 'null', None),
                                                (None, 'CONFLICT', 'true', None), (None, 'CONFLICT', '123', None),
                                                (None, 'CONFLICT', '"text"', None),
                                                ('text', None, '{}', None), ('text', None, None, 'decision'))):
                    with self.subTest(schema=label, values=values), self.assertRaises(sqlite3.IntegrityError):
                        db.execute("INSERT INTO workflow_requests(request_id,run_id,kind,summary,reason_code,reason_params_json,"
                                   "decision_id,payload_json,state,expected_revision,created_at,updated_at)"
                                   " VALUES(?,?,'attention',?,?,?,?,'{}','open',1,'created','updated')",
                                   (f'bad-{index}', run_id, *values))
                for index, values in enumerate((('buddy content', None, None, None),
                                                (None, 'CONFLICT', '{}', None),
                                                (None, 'routing-unusable-selection', '{}', 'decision'),
                                                (None, None, None, 'decision'))):
                    db.execute("INSERT INTO workflow_requests(request_id,run_id,kind,summary,reason_code,reason_params_json,"
                               "decision_id,payload_json,state,expected_revision,created_at,updated_at)"
                               " VALUES(?,?,'attention',?,?,?,?,'{}','open',1,'created','updated')",
                               (f'good-{index}', run_id, *values))
                self.assertEqual(db.execute("SELECT COUNT(*) FROM workflow_requests WHERE request_id LIKE 'good-%'")
                                 .fetchone()[0], 4)
                for index, values in enumerate((('CONFLICT', None), (None, '{}'), ('CONFLICT', '[]'),
                                                ('CONFLICT', 'null'), ('CONFLICT', '123'),
                                                ('CONFLICT', '"text"'))):
                    with self.subTest(schema=label, decision_values=values), self.assertRaises(sqlite3.IntegrityError):
                        db.execute('INSERT INTO evaluation_decisions(decision_id,status,task,reason_code,'
                                   'reason_params_json,created_at) VALUES(?,?,?,?,?,?)',
                                   (f'bad-decision-{index}', 'needs-host', 'task', *values, 'created'))
                for index, values in enumerate(((None, None, None), ('CONFLICT', '{}', None),
                                                (None, None, 'Router content'))):
                    db.execute('INSERT INTO evaluation_decisions(decision_id,status,task,reason_code,'
                               'reason_params_json,note,created_at) VALUES(?,?,?,?,?,?,?)',
                               (f'good-decision-{index}', 'needs-host', 'task', *values, 'created'))
                self.assertEqual(db.execute("SELECT COUNT(*) FROM evaluation_decisions"
                                            " WHERE decision_id LIKE 'good-decision-%'").fetchone()[0], 3)

    def test_decision_upgrade_preserves_adopted_router_note_and_removes_routing_copies(self):
        fixture, connection = self.restored('decision-reasons')
        decision_id = connection.execute('SELECT decision_id FROM evaluation_decisions LIMIT 1').fetchone()[0]
        buddy_id = connection.execute('SELECT buddy_id FROM evaluation_decisions WHERE decision_id=?',
                                      (decision_id,)).fetchone()[0]
        run_id = fixture['stableIds']['delivered']
        note = 'Router authored note; retain exactly  '
        output = {'status': 'ok', 'operation': 'select',
                  'decision': {'buddyId': buddy_id, 'reason': note, 'evidence': []}}
        connection.execute('UPDATE evaluation_decisions SET reason=?,error=NULL WHERE decision_id=?',
                           (note, decision_id))
        requested = json.loads(connection.execute('SELECT requested_json FROM decision_requests WHERE decision_id=?',
                                                  (decision_id,)).fetchone()[0])
        requested.pop('routerCalled', None)
        requested['inspections'] = [
            {'code': 'router-unavailable', 'reason': 'Router buddy 在请求冻结时尚未发布'},
            {'code': 'router-unavailable',
             'reason': 'Router 不可用：buddy x 在已发布目录中不可用（已禁用）'},
        ]
        connection.execute('UPDATE decision_requests SET output_json=?,requested_json=? WHERE decision_id=?',
                           (json.dumps(output), canonical_json(requested), decision_id))
        connection.execute('INSERT INTO router_requests(decision_id,snapshot_json,frozen_at) VALUES(?,?,?)',
                           (decision_id, json.dumps({'facts': {}, 'baseInput': {'buddies': []},
                                                     'inspections': requested['inspections'],
                                                     'routerProblem': {'code': 'router-unavailable',
                                                                       'reason': 'Router 不可用：列表中 2 项当前都不可用（router-unavailable）'}}),
                            'created'))
        validation = 'INVALID_BUDDY: The selected route is no longer usable'
        connection.execute('INSERT INTO workflow_routes(decision_id,run_id,owner_generation,state,reason,created_at,updated_at)'
                           ' VALUES(?, ?, 1, ?, ?, ?, ?)',
                           (decision_id, run_id, 'needs-host', validation, 'created', 'created'))
        self.old_request(connection, run_id, 'routing-ref', summary=validation,
                         payload={'source': 'routing', 'decisionId': decision_id, 'summary': validation})
        self.old_event(connection, 'decision.completed', payload={
            'decisionId': decision_id, 'status': 'completed', 'reason': note, 'error': None})
        connection.commit()
        original_output = connection.execute('SELECT output_json FROM decision_requests WHERE decision_id=?',
                                             (decision_id,)).fetchone()[0]
        upgrade_schema_24_to_25(fixture['state'])
        decision = connection.execute('SELECT * FROM evaluation_decisions WHERE decision_id=?',
                                      (decision_id,)).fetchone()
        self.assertEqual((decision['reason_code'], decision['reason_params_json'], decision['note']),
                         (None, None, note))
        self.assertEqual(connection.execute('SELECT output_json FROM decision_requests WHERE decision_id=?',
                                            (decision_id,)).fetchone()[0], original_output)
        inspections = json.loads(connection.execute('SELECT requested_json FROM decision_requests WHERE decision_id=?',
                                                    (decision_id,)).fetchone()[0])['inspections']
        self.assertEqual(inspections, [{'code': 'router-not-published-at-freeze'},
                                       {'code': 'router-catalog-unavailable'}])
        self.assertEqual(json.loads(connection.execute('SELECT snapshot_json FROM router_requests WHERE decision_id=?',
                                                       (decision_id,)).fetchone()[0])['routerProblem'],
                         {'code': 'router-all-unavailable'})
        self.assertNotIn('reason', connection.execute('SELECT * FROM workflow_routes').fetchone().keys())
        request = connection.execute("SELECT * FROM workflow_requests WHERE request_id='routing-ref'").fetchone()
        self.assertEqual((request['summary'], request['decision_id'], request['reason_code']),
                         (None, decision_id, 'dispatch.buddy-rejected'))
        event = json.loads(connection.execute("SELECT payload_json FROM events WHERE kind='decision.completed'"
                                              " ORDER BY seq DESC LIMIT 1").fetchone()[0])
        self.assertEqual(event, {'decisionId': decision_id, 'status': 'completed'})

    def test_quota_retry_routing_snapshot_reason_and_receipt_are_retired(self):
        fixture, connection = self.restored('quota-retry-history')
        decision_id = connection.execute('SELECT decision_id FROM evaluation_decisions LIMIT 1').fetchone()[0]
        self.old_router_attempt(fixture, connection, decision_id)
        row = connection.execute('SELECT requested_json FROM decision_requests WHERE decision_id=?',
                                 (decision_id,)).fetchone()
        requested = json.loads(row[0])
        requested['routingBasis']['quotaRetryBuddyIds'] = ['old-buddy']
        connection.execute('UPDATE decision_requests SET requested_json=? WHERE decision_id=?',
                           (canonical_json(requested), decision_id))
        snapshot = json.loads(connection.execute('SELECT snapshot_json FROM router_requests WHERE decision_id=?',
                                                 (decision_id,)).fetchone()[0])
        snapshot['routingBasis'] = {'candidateCount': 1, 'quotaRetryBuddyIds': ['old-buddy']}
        snapshot['baseInput']['routingBasis'] = {'candidateCount': 1, 'quotaRetryBuddyIds': ['old-buddy']}
        connection.execute('UPDATE router_requests SET snapshot_json=? WHERE decision_id=?',
                           (canonical_json(snapshot), decision_id))
        connection.execute("UPDATE evaluation_decisions SET status='needs-host',buddy_id=NULL,"
                           "reason='the selected quota retry was consumed',error='router-out-of-bounds'"
                           ' WHERE decision_id=?', (decision_id,))
        connection.execute('INSERT INTO commands(command_id,kind,request_hash,response_json,created_at)'
                           ' VALUES(?,?,?,?,?)', ('old-redetect', 'quota.redetect', 'old-redetect-hash',
                                                  '{"oldRetry":true}', '2026-01-01T00:00:00.000Z'))
        connection.execute('INSERT INTO commands(command_id,kind,request_hash,response_json,created_at)'
                           ' VALUES(?,?,?,?,?)', ('other-receipt', 'workflow.cancel', 'other-hash',
                                                  '{"cancelled":true}', '2026-01-01T00:00:00.000Z'))
        connection.commit()
        upgrade_schema_24_to_25(fixture['state'])
        requested = json.loads(connection.execute('SELECT requested_json FROM decision_requests WHERE decision_id=?',
                                                  (decision_id,)).fetchone()[0])
        snapshot = json.loads(connection.execute('SELECT snapshot_json FROM router_requests WHERE decision_id=?',
                                                 (decision_id,)).fetchone()[0])
        for basis in (requested['routingBasis'], snapshot['routingBasis'], snapshot['baseInput']['routingBasis']):
            self.assertNotIn('quotaRetryBuddyIds', basis)
        self.assertEqual(snapshot['routingBasis'], {'candidateCount': 1})
        self.assertEqual(snapshot['baseInput']['routingBasis'], {'candidateCount': 1})
        row = connection.execute('SELECT reason_code,reason_params_json FROM evaluation_decisions WHERE decision_id=?',
                                 (decision_id,)).fetchone()
        self.assertEqual((row['reason_code'], json.loads(row['reason_params_json'])),
                         ('router-out-of-bounds', {'bound': 'constraints-or-quota'}))
        self.assertIsNone(connection.execute("SELECT 1 FROM commands WHERE kind='quota.redetect'").fetchone())
        self.assertEqual(connection.execute("SELECT response_json FROM commands WHERE command_id='other-receipt'").fetchone()[0],
                         '{"cancelled":true}')

    def test_decision_upgrade_uses_history_only_for_known_board_prose(self):
        fixture, connection = self.restored('decision-history')
        decision_id = connection.execute('SELECT decision_id FROM evaluation_decisions LIMIT 1').fetchone()[0]
        connection.execute("UPDATE evaluation_decisions SET status='needs-host',buddy_id=NULL,"
                           "reason='Old board sentence no longer recognized',error=NULL WHERE decision_id=?",
                           (decision_id,))
        connection.execute('UPDATE decision_requests SET output_json=NULL WHERE decision_id=?',
                           (decision_id,))
        self.old_event(connection, 'decision.needs_host', payload={
            'decisionId': decision_id, 'status': 'needs-host',
            'reason': 'Old board sentence no longer recognized'})
        connection.commit()
        upgrade_schema_24_to_25(fixture['state'])
        row = connection.execute('SELECT reason_code,reason_params_json,note FROM evaluation_decisions'
                                 ' WHERE decision_id=?', (decision_id,)).fetchone()
        self.assertEqual((row['reason_code'], json.loads(row['reason_params_json']), row['note']),
                         ('history.reason-unavailable', {'recordId': decision_id}, None))

    def test_decision_upgrade_rejects_ambiguous_router_ownership_atomically(self):
        fixture, connection = self.restored('decision-ambiguous')
        decision_id = connection.execute('SELECT decision_id FROM evaluation_decisions LIMIT 1').fetchone()[0]
        note = 'Router output with an unrelated selected buddy'
        connection.execute('UPDATE evaluation_decisions SET reason=? WHERE decision_id=?', (note, decision_id))
        connection.execute('UPDATE decision_requests SET output_json=? WHERE decision_id=?',
                           (json.dumps({'status': 'ok', 'operation': 'select',
                                        'decision': {'buddyId': 'another-buddy', 'reason': note, 'evidence': []}}),
                            decision_id))
        connection.commit()
        before = backup.database_snapshot(connection, exact=True)
        schema = list(connection.execute('SELECT * FROM sqlite_master ORDER BY name'))
        with self.assertRaises(BoardError) as caught:
            upgrade_schema_24_to_25(fixture['state'])
        self.assertEqual(caught.exception.code, 'UPGRADE_INVALID_BOARD')
        self.assertEqual(backup.database_snapshot(connection, exact=True), before)
        self.assertEqual(list(connection.execute('SELECT * FROM sqlite_master ORDER BY name')), schema)

    def test_decision_upgrade_uses_frozen_table_count_for_oversize_reason(self):
        fixture, connection = self.restored('decision-oversize')
        decision_id = connection.execute('SELECT decision_id FROM evaluation_decisions LIMIT 1').fetchone()[0]
        connection.execute("UPDATE evaluation_decisions SET status='needs-host',buddy_id=NULL,"
                           "reason='an old truncated limit message',error='router-input-too-large'"
                           ' WHERE decision_id=?', (decision_id,))
        frozen = {'baseInput': {'buddies': [{} for _ in range(201)], 'preferences': [],
                                'annotations': []}}
        connection.execute('INSERT INTO router_requests(decision_id,snapshot_json,frozen_at) VALUES(?,?,?)',
                           (decision_id, json.dumps(frozen), 'created'))
        connection.commit()
        upgrade_schema_24_to_25(fixture['state'])
        row = connection.execute('SELECT reason_code,reason_params_json FROM evaluation_decisions'
                                 ' WHERE decision_id=?', (decision_id,)).fetchone()
        self.assertEqual((row['reason_code'], json.loads(row['reason_params_json'])),
                         ('router-input-too-large', {'collection': 'buddies', 'count': 201, 'limit': 200}))

    def test_release_upgrade_moves_program_reason_and_keeps_event_identity(self):
        fixture, connection = self.restored('release-reason')
        old = 'service stopped before execution launch'
        decision_id = connection.execute('SELECT decision_id FROM evaluation_decisions LIMIT 1').fetchone()[0]
        original_attempt = connection.execute('SELECT attempt_id,task_id FROM attempts LIMIT 1').fetchone()
        task_id, attempt_id = self.old_router_attempt(fixture, connection, decision_id)
        attempt = {'task_id': task_id, 'attempt_id': attempt_id}
        connection.execute("UPDATE tasks SET state='failed' WHERE task_id=?", (task_id,))
        connection.execute("UPDATE evaluation_decisions SET status='needs-host',buddy_id=NULL,reason=?,error='router-unavailable'"
                           ' WHERE decision_id=?', (old, decision_id))
        connection.execute('UPDATE decision_requests SET task_id=?,attempt_id=?,generation=1,output_json=? WHERE decision_id=?',
                           (attempt['task_id'], attempt['attempt_id'],
                            canonical_json({'status': 'error', 'code': 'router-unavailable'}), decision_id))
        connection.execute('UPDATE attempts SET error=? WHERE attempt_id=?', (old, attempt['attempt_id']))
        for kind, payload in (
            ('attempt.released', {'reason': old, 'releaseEvidence': 'processCreated=false',
                                  'previousState': 'starting'}),
            ('workflow.attempt_released', {'reason': old, 'shutdownConfirmed': True}),
            ('attempt.uncertain', {'reason': 'service restarted while this attempt owned possible external work; '
                                   'its resource claims are retained until the execution reconnects or recovery proves shutdown'}),
        ):
            event_attempt = attempt if kind == 'attempt.released' else original_attempt
            self.old_event(connection, kind, task_id=event_attempt['task_id'],
                           attempt_id=event_attempt['attempt_id'], payload=payload)
        before = [row['seq'] for row in connection.execute('SELECT seq FROM events ORDER BY seq')]
        connection.commit()
        upgrade_schema_24_to_25(fixture['state'])
        self.assertIsNone(connection.execute('SELECT error FROM attempts WHERE attempt_id=?',
                                             (attempt['attempt_id'],)).fetchone()[0])
        self.assertEqual(connection.execute('SELECT reason_code FROM evaluation_decisions WHERE decision_id=?',
                                            (decision_id,)).fetchone()[0], 'service-stopped-before-launch')
        self.assertEqual([row['seq'] for row in connection.execute('SELECT seq FROM events ORDER BY seq')], before)
        payloads = {row['kind']: json.loads(row['payload_json']) for row in connection.execute(
            "SELECT kind,payload_json FROM events WHERE kind IN ('attempt.released','workflow.attempt_released','attempt.uncertain')")}
        self.assertEqual(payloads['attempt.released']['reason'],
                         {'code': 'service-stopped-before-launch', 'params': {}})
        self.assertEqual(payloads['workflow.attempt_released'], {'shutdownConfirmed': True})
        self.assertEqual(payloads['attempt.uncertain'], {})

    def test_interrupted_decision_requires_durable_interruption_relation(self):
        fixture, connection = self.restored('decision-interrupted')
        decision_id = connection.execute('SELECT decision_id FROM evaluation_decisions LIMIT 1').fetchone()[0]
        attempt_id = connection.execute('SELECT attempt_id FROM attempts LIMIT 1').fetchone()[0]
        old = 'the execution ended before result delivery; no delivered answer can become a current recommendation'
        connection.execute("UPDATE evaluation_decisions SET status='failed',buddy_id=NULL,reason=?,error=NULL"
                           ' WHERE decision_id=?', (old, decision_id))
        connection.execute('UPDATE decision_requests SET attempt_id=? WHERE decision_id=?',
                           (attempt_id, decision_id))
        connection.execute('INSERT INTO attempt_interruptions(attempt_id,evidence_kind,observed_at,state)'
                           " VALUES(?,'identity-absent','created','settled')", (attempt_id,))
        connection.commit()
        upgrade_schema_24_to_25(fixture['state'])
        row = connection.execute('SELECT reason_code,reason_params_json FROM evaluation_decisions'
                                 ' WHERE decision_id=?', (decision_id,)).fetchone()
        self.assertEqual((row['reason_code'], row['reason_params_json']),
                         ('interrupted-before-delivery', '{}'))

    def test_fenced_route_reason_uses_its_recorded_owner(self):
        for name, run_key, old, code in (
            ('takeover', 'delivered', 'Host takeover fenced model selection', 'routing-taken-over'),
            ('cancel', 'cancelled', 'Owning Micro task cancelled', 'micro-task-cancelled'),
        ):
            with self.subTest(name=name):
                fixture, connection = self.restored('fenced-' + name)
                decision_id = connection.execute('SELECT decision_id FROM evaluation_decisions LIMIT 1').fetchone()[0]
                run_id = fixture['stableIds'][run_key]
                connection.execute("UPDATE evaluation_decisions SET status='cancelled',buddy_id=NULL,reason=?,error=NULL"
                                   ' WHERE decision_id=?', ('the decision run was cancelled: ' + old, decision_id))
                connection.execute('INSERT INTO workflow_routes(decision_id,run_id,owner_generation,state,reason,'
                                   "created_at,updated_at) VALUES(?,?,1,'fenced',?,'created','created')",
                                   (decision_id, run_id, old))
                if name == 'takeover':
                    self.old_event(connection, 'workflow.takeover', task_id=run_id,
                                   payload={'newHostId': 'new-host', 'ownerGeneration': 2,
                                            'previousOwnerGeneration': 1,
                                            'actor': {'role': 'host', 'id': 'host-1'}})
                connection.commit()
                upgrade_schema_24_to_25(fixture['state'])
                self.assertEqual(connection.execute('SELECT reason_code FROM evaluation_decisions WHERE decision_id=?',
                                                    (decision_id,)).fetchone()[0], code)

    def test_decision_cancel_reason_does_not_consume_host_cancel_note(self):
        fixture, connection = self.restored('decision-host-cancel')
        decision_id = connection.execute('SELECT decision_id FROM evaluation_decisions LIMIT 1').fetchone()[0]
        task_id = 'host-cancel-router-task'
        spec = json.dumps({'decision': {'decisionId': decision_id, 'kind': 'select'}})
        connection.execute(
            "INSERT INTO tasks(task_id,request_id,owner,spec_json,spec_canonical_json,input_fingerprint,"
            "fingerprint_version,adapter,cwd,router_budget_seconds,state,revision,created_at,updated_at)"
            " VALUES(?,?,'decision',?,?,'fixture-digest',1,'decision','/work',60,'cancelled',1,'created','updated')",
            (task_id, task_id, spec, spec))
        note = 'Host cancelled this choice  '
        connection.execute("UPDATE evaluation_decisions SET status='cancelled',buddy_id=NULL,reason=?,error=NULL"
                           ' WHERE decision_id=?', ('the decision run was cancelled: ' + note, decision_id))
        connection.execute('UPDATE decision_requests SET task_id=? WHERE decision_id=?', (task_id, decision_id))
        self.old_event(connection, 'task.cancelled', task_id=task_id,
                       payload={'reason': note, 'actor': {'role': 'host', 'id': 'host-1'}})
        connection.commit()
        upgrade_schema_24_to_25(fixture['state'])
        self.assertEqual(connection.execute('SELECT reason_code FROM evaluation_decisions WHERE decision_id=?',
                                            (decision_id,)).fetchone()[0], 'decision-cancelled')
        event = json.loads(connection.execute("SELECT payload_json FROM events WHERE kind='task.cancelled'"
                                              ' ORDER BY seq DESC LIMIT 1').fetchone()[0])
        self.assertEqual(event['note'], note)


    def test_blackboard_event_reason_cleanup_uses_writer_not_wording(self):
        fixture, connection = self.restored('blackboard-event-writers')
        attempt = connection.execute('SELECT attempt_id,task_id FROM attempts LIMIT 1').fetchone()
        old = ('service restarted while this attempt owned possible external work; '
               'its resource claims are retained until the worker reattaches or an operator retries')
        current = ('service restarted while this attempt owned possible external work; '
                   'its resource claims are retained until the execution reconnects or recovery proves shutdown')
        events = []
        for kind, text in [('attempt.uncertain', old), ('attempt.uncertain', current),
                           ('attempt.uncertain', 'an earlier unrecognized service sentence'),
                           ('attempt.uncertain', None), ('workflow.attempt_released', 'older service wording')]:
            seq = self.old_event(connection, kind, task_id=attempt['task_id'],
                                 attempt_id=attempt['attempt_id'],
                                 payload={'reason': text, 'retainedFact': 7})
            events.append(seq)
        connection.commit()
        upgrade_schema_24_to_25(fixture['state'])
        for seq in events:
            row = connection.execute('SELECT payload_json FROM events WHERE seq=?', (seq,)).fetchone()
            self.assertEqual(json.loads(row[0]), {'retainedFact': 7})

    def test_dispatch_rejection_upgrade_keeps_error_code_as_a_parameter(self):
        fixture, connection = self.restored('dispatch-rejection')
        run_id = fixture['stableIds']['delivered']
        decision_id = connection.execute('SELECT decision_id FROM evaluation_decisions LIMIT 1').fetchone()[0]
        connection.execute('INSERT INTO workflow_routes(decision_id,run_id,owner_generation,state,reason,created_at,updated_at)'
                           " VALUES(?,?,1,'needs-host','BUDDY_UNAVAILABLE: old diagnostic','created','updated')",
                           (decision_id, run_id))
        self.old_request(connection, run_id, 'dispatch-with-decision', summary='BUDDY_UNAVAILABLE: old diagnostic',
                         payload={'source': 'routing', 'decisionId': decision_id})
        self.old_request(connection, run_id, 'dispatch-explicit', summary='HARNESS_UNAVAILABLE: old diagnostic',
                         payload={'source': 'routing', 'decisionId': None})
        connection.commit()
        upgrade_schema_24_to_25(fixture['state'])
        for request_id, error_code, relation in [('dispatch-with-decision', 'BUDDY_UNAVAILABLE', decision_id),
                                                ('dispatch-explicit', 'HARNESS_UNAVAILABLE', None)]:
            row = connection.execute('SELECT * FROM workflow_requests WHERE request_id=?', (request_id,)).fetchone()
            self.assertEqual(row['reason_code'], 'dispatch.buddy-rejected')
            self.assertEqual(json.loads(row['reason_params_json']), {'errorCode': error_code})
            self.assertEqual(row['decision_id'], relation)
            self.assertIsNone(row['summary'])
            self.assertEqual(json.loads(row['payload_json']), {'source': 'routing'})

    def test_program_selection_upgrade_uses_recorded_result_not_sentence(self):
        fixture, connection = self.restored('program-result-writer')
        connection.execute("UPDATE evaluation_decisions SET reason='older program description'")
        connection.commit()
        upgrade_schema_24_to_25(fixture['state'])
        row = connection.execute('SELECT reason_code,reason_params_json,note FROM evaluation_decisions').fetchone()
        self.assertEqual((row['reason_code'], row['reason_params_json'], row['note']),
                         ('single-candidate', '{}', None))

    def test_old_preclaim_router_unavailable_uses_the_recorded_output(self):
        for phrase, expected in (
            ('Router 不可用：列表第 2 项之后没有更多 Router', 'router-list-exhausted'),
            ('Router 不可用：列表中 2 项当前都不可用（router-unavailable）', 'router-all-unavailable'),
        ):
            with self.subTest(phrase=phrase):
                fixture, connection = self.restored('preclaim-' + expected)
                decision_id = connection.execute('SELECT decision_id FROM evaluation_decisions LIMIT 1').fetchone()[0]
                task_id, _ = self.old_router_attempt(fixture, connection, decision_id, claimed=False)
                connection.execute("UPDATE tasks SET state='cancelled' WHERE task_id=?", (task_id,))
                if expected == 'router-all-unavailable':
                    requested = json.loads(connection.execute('SELECT requested_json FROM decision_requests WHERE decision_id=?',
                                                              (decision_id,)).fetchone()[0])
                    requested['inspections'] = [
                        {'index': 0, 'buddyId': 'disabled-router', 'eligible': False,
                         'code': 'router-unavailable', 'reason': 'Router 不可用：buddy disabled-router 已禁用',
                         'selected': False},
                        {'index': 1, 'buddyId': 'unhealthy-router', 'eligible': False,
                         'code': 'router-unavailable', 'reason': 'Router 不可用：harness codex 不可用（probe-failed）',
                         'selected': False},
                    ]
                    connection.execute('UPDATE decision_requests SET requested_json=? WHERE decision_id=?',
                                       (canonical_json(requested), decision_id))
                connection.execute("UPDATE evaluation_decisions SET status='needs-host',buddy_id=NULL,reason=?,error='router-unavailable'"
                                   ' WHERE decision_id=?', (phrase, decision_id))
                connection.execute('UPDATE decision_requests SET task_id=?,attempt_id=NULL,output_json=? WHERE decision_id=?',
                                   (task_id, canonical_json({'status': 'error', 'code': 'router-unavailable'}), decision_id))
                connection.commit()
                upgrade_schema_24_to_25(fixture['state'])
                self.assertEqual(connection.execute('SELECT reason_code FROM evaluation_decisions WHERE decision_id=?',
                                                    (decision_id,)).fetchone()[0], expected)

    def test_old_publication_failures_are_attributed_without_an_output(self):
        for label, status, reason, error, expected_code, params in (
            ('board', 'needs-host', 'the model output could not be adopted (INVALID_ARGUMENT: malformed); the Host decides',
             None, 'decision-publication-failed', {'errorCode': 'INVALID_ARGUMENT'}),
            ('board-multiline', 'needs-host', 'the model output could not be adopted (INVALID_ARGUMENT: first line\nsecond line); the Host decides',
             None, 'decision-publication-failed', {'errorCode': 'INVALID_ARGUMENT'}),
            ('board-oversize', 'needs-host', 'the model output could not be adopted (router-input-too-large: frozen retry dispatch is oversized); the Host decides',
             None, 'router-input-too-large', None),
            ('board-truncated', 'needs-host', 'the model output could not be adopted (INVALID_',
             None, 'history.reason-unavailable', None),
            ('generic', 'failed', 'the decision result could not be processed; no recommendation is published',
             'ValueError: malformed', 'decision-publication-failed', {}),
        ):
            with self.subTest(label=label):
                fixture, connection = self.restored('publication-' + label)
                decision_id = connection.execute('SELECT decision_id FROM evaluation_decisions LIMIT 1').fetchone()[0]
                _, attempt_id = self.old_router_attempt(fixture, connection, decision_id)
                if label == 'board-oversize':
                    document = json.loads(connection.execute('SELECT input_json FROM decision_requests WHERE decision_id=?',
                                                             (decision_id,)).fetchone()[0])
                    document['annotations'] = [{'text': 'x' * 131072}]
                    params = {'bytes': len(canonical_json(document).encode()), 'limit': 128 * 1024}
                    connection.execute('UPDATE decision_requests SET input_json=? WHERE decision_id=?',
                                       (canonical_json(document), decision_id))
                connection.execute('UPDATE evaluation_decisions SET status=?,buddy_id=NULL,reason=?,error=? WHERE decision_id=?',
                                   (status, reason, error, decision_id))
                connection.commit()
                upgrade_schema_24_to_25(fixture['state'])
                row = connection.execute('SELECT reason_code,reason_params_json FROM evaluation_decisions WHERE decision_id=?',
                                         (decision_id,)).fetchone()
                self.assertEqual((row['reason_code'], json.loads(row['reason_params_json'])),
                                 (expected_code, {'recordId': decision_id} if params is None else params))

    def test_old_oversized_micro_task_reference_recovers_exact_bytes(self):
        fixture, connection = self.restored('oversized-micro-task-reference')
        decision_id = connection.execute('SELECT decision_id FROM evaluation_decisions LIMIT 1').fetchone()[0]
        requested = json.loads(connection.execute('SELECT requested_json FROM decision_requests WHERE decision_id=?',
                                                  (decision_id,)).fetchone()[0])
        requested.pop('routerCalled', None)
        requested['routingBasis'] = {'candidateCount': 2, 'excludedBuddies': [], 'excludedCount': 0}
        reference = {**requested, 'task': 'Micro task micro-task (full task retained in workflow)',
                     'workflowRouting': True,
                     'taskReference': {'runId': 'micro-task', 'bytes': 8193, 'sha256': 'old-digest'}}
        connection.execute("UPDATE evaluation_decisions SET status='needs-host',buddy_id=NULL,reason=?,error=NULL"
                           ' WHERE decision_id=?',
                           ("The complete Micro task contains 8193 UTF-8 bytes, above the selector's 8192-byte task limit. No model call was made. Continue the same Micro task with a complete buddy; its full task remains unchanged.", decision_id))
        connection.execute('UPDATE decision_requests SET requested_json=?,output_json=NULL,selected_json=NULL WHERE decision_id=?',
                           (canonical_json(reference), decision_id))
        candidates = [dict(row) for row in connection.execute(
            'SELECT buddy_id,harness,provider,model,effort FROM buddies ORDER BY rowid LIMIT 2')]
        connection.execute('INSERT INTO router_requests(decision_id,snapshot_json,frozen_at) VALUES(?,?,?)',
                           (decision_id, canonical_json({'facts': {}, 'baseInput': {'buddies': [
                               {'buddyId': item['buddy_id'], **{key: item[key] for key in
                                                                ('harness', 'provider', 'model', 'effort')}}
                               for item in candidates]},
                                                         'inspections': []}), 'created'))
        connection.commit()
        upgrade_schema_24_to_25(fixture['state'])
        row = connection.execute('SELECT reason_code,reason_params_json FROM evaluation_decisions WHERE decision_id=?',
                                 (decision_id,)).fetchone()
        self.assertEqual((row['reason_code'], json.loads(row['reason_params_json'])),
                         ('router-input-too-large', {'bytes': 8193, 'limit': 8192}))

    def test_service_queued_cancellation_reads_actor_columns(self):
        fixture, connection = self.restored('service-queued-cancel')
        decision_id = connection.execute('SELECT decision_id FROM evaluation_decisions LIMIT 1').fetchone()[0]
        task_id, _ = self.old_router_attempt(fixture, connection, decision_id, claimed=False)
        connection.execute("UPDATE tasks SET state='cancelled' WHERE task_id=?", (task_id,))
        connection.execute("UPDATE evaluation_decisions SET status='needs-host',buddy_id=NULL,"
                           "reason='Router 不可用：列表中 2 项当前都不可用（router-unavailable）',"
                           "error='router-unavailable' WHERE decision_id=?", (decision_id,))
        connection.execute('UPDATE decision_requests SET output_json=? WHERE decision_id=?',
                           (canonical_json({'status': 'error', 'code': 'router-unavailable'}), decision_id))
        seq = self.old_event(connection, 'task.cancelled', task_id=task_id,
                             payload={'reason': 'the Router item is unavailable before the model call',
                                      'phase': 'queued', 'actor': {'role': 'service', 'id': 'service'}})
        old = connection.execute('SELECT actor_role,actor_id,payload_json FROM events WHERE seq=?', (seq,)).fetchone()
        self.assertEqual((old['actor_role'], old['actor_id']), ('service', 'service'))
        self.assertNotIn('actor', json.loads(old['payload_json']))
        connection.commit()
        upgrade_schema_24_to_25(fixture['state'])
        self.assertEqual(json.loads(connection.execute('SELECT payload_json FROM events WHERE seq=?', (seq,)).fetchone()[0]),
                         {'phase': 'queued', 'decisionId': decision_id})

    def test_frozen_dispatch_prose_and_live_status_do_not_acquire_a_false_reason(self):
        for label, status, prose, expected in (
            ('snapshot', 'needs-host', 'No frozen Router request snapshot is recorded for this dispatch', 'router-dispatch-unverified'),
            ('dispatch', 'needs-host', 'No frozen Router dispatch is recorded', 'router-dispatch-unverified'),
            ('queued', 'queued', 'queued for a Router worker', None),
            ('running', 'running', 'Router is running over its frozen input', None),
        ):
            with self.subTest(label=label):
                fixture, connection = self.restored('frozen-' + label)
                decision_id = connection.execute('SELECT decision_id FROM evaluation_decisions LIMIT 1').fetchone()[0]
                task_id, attempt_id = self.old_router_attempt(
                    fixture, connection, decision_id, claimed=(status == 'running'))
                if label == 'snapshot':
                    connection.execute('DELETE FROM router_dispatches WHERE task_id=?', (task_id,))
                    connection.execute('DELETE FROM router_requests WHERE decision_id=?', (decision_id,))
                elif label == 'dispatch':
                    connection.execute('DELETE FROM router_dispatches WHERE task_id=?', (task_id,))
                if status == 'needs-host':
                    connection.execute("UPDATE tasks SET state='cancelled' WHERE task_id=?", (task_id,))
                elif status == 'running':
                    connection.execute("UPDATE tasks SET state='running',active_attempt_id=? WHERE task_id=?",
                                       (attempt_id, task_id))
                    connection.execute("UPDATE attempts SET execution_state='executing',shutdown_confirmed=0"
                                       ' WHERE attempt_id=?', (attempt_id,))
                connection.execute('UPDATE evaluation_decisions SET status=?,buddy_id=NULL,reason=?,error=NULL WHERE decision_id=?',
                                   (status, prose, decision_id))
                connection.execute('UPDATE decision_requests SET output_json=NULL WHERE decision_id=?', (decision_id,))
                connection.commit()
                upgrade_schema_24_to_25(fixture['state'])
                self.assertEqual(connection.execute('SELECT reason_code FROM evaluation_decisions WHERE decision_id=?',
                                                    (decision_id,)).fetchone()[0], expected)

    def test_snapshot_nonobject_router_problem_rolls_back(self):
        for label, snapshot in (('snapshot', 'old text'),
                                ('router-problem', {'routerProblem': 'old text'}),
                                ('requested', 'old text')):
            with self.subTest(label=label):
                fixture, connection = self.restored('snapshot-nonobject-' + label)
                decision_id = connection.execute('SELECT decision_id FROM evaluation_decisions LIMIT 1').fetchone()[0]
                if label == 'requested':
                    connection.execute('UPDATE decision_requests SET requested_json=? WHERE decision_id=?',
                                       (canonical_json(snapshot), decision_id))
                else:
                    connection.execute('INSERT INTO router_requests(decision_id,snapshot_json,frozen_at) VALUES(?,?,?)',
                                       (decision_id, canonical_json(snapshot), 'created'))
                connection.commit()
                before = backup.database_snapshot(connection, exact=True)
                with self.assertRaises(BoardError) as caught:
                    upgrade_schema_24_to_25(fixture['state'])
                self.assertEqual(caught.exception.code, 'UPGRADE_INVALID_BOARD')
                self.assertEqual(backup.database_snapshot(connection, exact=True), before)

    def test_router_outcome_index_is_removed_with_the_old_event_form(self):
        fixture, connection = self.restored('router-outcome-index')
        self.assertIsNotNone(connection.execute("SELECT 1 FROM sqlite_master WHERE type='index'"
                                                " AND name='events_router_outcome_idx'").fetchone())
        connection.commit()
        upgrade_schema_24_to_25(fixture['state'])
        self.assertIsNone(connection.execute("SELECT 1 FROM sqlite_master WHERE type='index'"
                                             " AND name='events_router_outcome_idx'").fetchone())

    def test_takeover_request_keeps_its_fenced_route_when_current_decision_is_absent(self):
        fixture, connection = self.restored('takeover-no-current-decision')
        run_id = fixture['stableIds']['delivered']
        decision_id = connection.execute('SELECT decision_id FROM evaluation_decisions LIMIT 1').fetchone()[0]
        connection.execute("INSERT INTO workflow_routes(decision_id,run_id,owner_generation,state,reason,created_at,updated_at)"
                           " VALUES(?,?,1,'fenced','Host takeover fenced model selection','created','created')",
                           (decision_id, run_id))
        self.old_event(connection, 'workflow.takeover', task_id=run_id,
                       payload={'newHostId': 'new-host', 'ownerGeneration': 2,
                                'previousOwnerGeneration': 1,
                                'actor': {'role': 'host', 'id': 'host-1'}})
        self.old_request(connection, run_id, 'takeover-routing',
                         summary='Host takeover interrupted model selection; wait for its confirmed stop, then choose a buddy or reroute',
                         payload={'source': 'routing', 'decisionId': None})
        connection.commit()
        upgrade_schema_24_to_25(fixture['state'])
        row = connection.execute("SELECT reason_code,decision_id FROM workflow_requests WHERE request_id='takeover-routing'").fetchone()
        self.assertEqual((row['reason_code'], row['decision_id']), ('routing-taken-over', None))

    def test_takeover_request_uses_interrupted_route_with_a_different_current_decision(self):
        fixture, connection = self.restored('takeover-different-current-decision')
        run_id = fixture['stableIds']['delivered']
        current_id = connection.execute('SELECT decision_id FROM evaluation_decisions LIMIT 1').fetchone()[0]
        interrupted_id = 'interrupted-route'
        connection.execute("INSERT INTO evaluation_decisions(decision_id,status,task,reason,created_at)"
                           " VALUES(?,'cancelled','task','the decision run was cancelled: Host takeover fenced model selection','created')",
                           (interrupted_id,))
        connection.execute("INSERT INTO decision_requests(decision_id,request_id,kind,input_fingerprint,requested_json,created_at,updated_at)"
                           " VALUES(?,?,'select','old-fingerprint','{}','created','created')",
                           (interrupted_id, interrupted_id))
        connection.execute("INSERT INTO workflow_routes(decision_id,run_id,owner_generation,state,reason,created_at,updated_at)"
                           " VALUES(?,?,1,'fenced','Host takeover fenced model selection','created','created')",
                           (interrupted_id, run_id))
        connection.execute("INSERT INTO workflow_routes(decision_id,run_id,owner_generation,state,reason,created_at,updated_at)"
                           " VALUES(?,?,2,'needs-host','No legal coding buddy was selected','created','created')",
                           (current_id, run_id))
        self.old_event(connection, 'workflow.takeover', task_id=run_id,
                       payload={'newHostId': 'new-host', 'ownerGeneration': 2,
                                'previousOwnerGeneration': 1,
                                'actor': {'role': 'host', 'id': 'host-1'}})
        self.old_request(connection, run_id, 'takeover-current',
                         summary='Host takeover interrupted model selection; wait for its confirmed stop, then choose a buddy or reroute',
                         payload={'source': 'routing', 'decisionId': current_id})
        self.old_request(connection, run_id, 'later-validation',
                         summary='No legal coding buddy was selected',
                         payload={'source': 'routing', 'decisionId': current_id})
        connection.execute("UPDATE workflow_requests SET created_at='later',updated_at='later'"
                           " WHERE request_id='later-validation'")
        connection.commit()
        upgrade_schema_24_to_25(fixture['state'])
        row = connection.execute("SELECT reason_code,decision_id FROM workflow_requests WHERE request_id='takeover-current'").fetchone()
        self.assertEqual((row['reason_code'], row['decision_id']), ('routing-taken-over', current_id))
        self.assertEqual(connection.execute("SELECT reason_code FROM workflow_requests WHERE request_id='later-validation'").fetchone()[0],
                         'routing-unusable-selection')

    def test_old_cancel_before_release_and_cancelled_completion_keep_cancellation(self):
        for label, prose, error in (
            ('intent', 'the decision run was cancelled: Host cancelled', None),
            ('completion', 'The routing request was cancelled; no recommendation is published', 'router-no-answer'),
        ):
            with self.subTest(label=label):
                fixture, connection = self.restored('cancel-before-release-' + label)
                decision_id = connection.execute('SELECT decision_id FROM evaluation_decisions LIMIT 1').fetchone()[0]
                task_id, attempt_id = self.old_router_attempt(fixture, connection, decision_id)
                connection.execute("UPDATE tasks SET state='cancelled' WHERE task_id=?", (task_id,))
                connection.execute('UPDATE evaluation_decisions SET status=?,buddy_id=NULL,reason=?,error=? WHERE decision_id=?',
                                   ('cancelled', prose, error, decision_id))
                connection.execute('UPDATE decision_requests SET output_json=? WHERE decision_id=?',
                                   (canonical_json({'status': 'error', 'code': error}) if error else None, decision_id))
                self.old_event(connection, 'task.cancelled', task_id=task_id,
                               payload={'reason': 'Host cancelled', 'actor': {'role': 'host', 'id': 'host-1'}})
                self.old_event(connection, 'attempt.released', task_id=task_id,
                               attempt_id=attempt_id,
                               payload={'reason': 'service stopped before execution launch',
                                        'releaseEvidence': 'processCreated=false'})
                connection.commit()
                upgrade_schema_24_to_25(fixture['state'])
                self.assertEqual(connection.execute('SELECT reason_code FROM evaluation_decisions WHERE decision_id=?',
                                                    (decision_id,)).fetchone()[0], 'decision-cancelled')

    def test_admission_unavailable_and_empty_reroute_recover_codes(self):
        for label, reason, error, output, expected, params in (
            ('admission', 'Router 不可用：列表中 2 项当前都不可用（router-unavailable）', None,
             None, 'router-all-unavailable', {}),
            ('reroute', 'Selected buddy b is disabled; no legal candidate remains after rerouting',
             'router-out-of-bounds', {'code': 'router-out-of-bounds'},
             'router-out-of-bounds', {'bound': 'disabled'}),
        ):
            with self.subTest(label=label):
                fixture, connection = self.restored('admission-reroute-' + label)
                decision_id = connection.execute('SELECT decision_id FROM evaluation_decisions LIMIT 1').fetchone()[0]
                connection.execute("UPDATE evaluation_decisions SET status='needs-host',buddy_id=NULL,reason=?,error=?"
                                   ' WHERE decision_id=?', (reason, error, decision_id))
                connection.execute('UPDATE decision_requests SET task_id=NULL,attempt_id=NULL,output_json=?,selected_json=NULL'
                                   ' WHERE decision_id=?',
                                   (canonical_json(output) if output else None, decision_id))
                if label == 'admission':
                    requested = json.loads(connection.execute('SELECT requested_json FROM decision_requests WHERE decision_id=?',
                                                              (decision_id,)).fetchone()[0])
                    requested.pop('routerCalled', None)
                    requested['routingBasis'] = {'candidateCount': 2, 'excludedBuddies': [], 'excludedCount': 0}
                    requested['routerProblem'] = {'code': 'router-unavailable', 'reason': reason}
                    requested['inspections'] = [
                        {'index': 0, 'buddyId': 'disabled-router', 'eligible': False,
                         'code': 'router-unavailable', 'reason': 'Router 不可用：buddy disabled-router 已禁用',
                         'selected': False},
                        {'index': 1, 'buddyId': 'unhealthy-router', 'eligible': False,
                         'code': 'router-unavailable', 'reason': 'Router 不可用：harness codex 不可用（probe-failed）',
                         'selected': False},
                    ]
                    connection.execute('UPDATE decision_requests SET requested_json=? WHERE decision_id=?',
                                       (canonical_json(requested), decision_id))
                    candidates = [dict(row) for row in connection.execute(
                        'SELECT buddy_id,harness,provider,model,effort FROM buddies ORDER BY rowid LIMIT 2')]
                    connection.execute('INSERT INTO router_requests(decision_id,snapshot_json,frozen_at) VALUES(?,?,?)',
                                       (decision_id, canonical_json({'facts': {}, 'baseInput': {'buddies': [
                                           {'buddyId': item['buddy_id'], **{key: item[key] for key in
                                                                            ('harness', 'provider', 'model', 'effort')}}
                                           for item in candidates]},
                                                                     'inspections': requested['inspections']}),
                                        'created'))
                connection.commit()
                upgrade_schema_24_to_25(fixture['state'])
                row = connection.execute('SELECT reason_code,reason_params_json FROM evaluation_decisions WHERE decision_id=?',
                                         (decision_id,)).fetchone()
                self.assertEqual((row['reason_code'], json.loads(row['reason_params_json'])), (expected, params))

    def test_completed_routing_request_without_old_validation_shape_fails(self):
        fixture, connection = self.restored('routing-request-unknown-validation')
        decision_id = connection.execute('SELECT decision_id FROM evaluation_decisions LIMIT 1').fetchone()[0]
        run_id = fixture['stableIds']['delivered']
        connection.execute("INSERT INTO workflow_routes(decision_id,run_id,owner_generation,state,reason,created_at,updated_at)"
                           " VALUES(?,?,1,'needs-host','unknown validation','created','updated')", (decision_id, run_id))
        self.old_request(connection, run_id, 'routing-unknown',
                         summary='unknown validation', payload={'source': 'routing', 'decisionId': decision_id})
        connection.commit()
        before = backup.database_snapshot(connection, exact=True)
        with self.assertRaises(BoardError) as caught:
            upgrade_schema_24_to_25(fixture['state'])
        self.assertEqual(caught.exception.code, 'UPGRADE_INVALID_BOARD')
        self.assertEqual(backup.database_snapshot(connection, exact=True), before)
