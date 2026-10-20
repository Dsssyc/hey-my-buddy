"""The fixed schema-26 stock exercises the pending schema-27 upgrade."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest import mock

from blackboard.store.upgrade_fixture import load_fixture, _git, _sha, _json
from hey_my_buddy.blackboard.store.store import BoardStore
from hey_my_buddy.blackboard.store import upgrade as schema_upgrade
from hey_my_buddy.blackboard.routing import router
from hey_my_buddy.blackboard.store.db import Corruption, Database, SCHEMA_VERSION
from hey_my_buddy.blackboard.tasks import workspace
from hey_my_buddy.blackboard.tasks.workflow import WorkflowCoordinator
from hey_my_buddy.errors import BoardError
from hey_my_buddy.install import upgrade


class UpgradeFixtureTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix='upgrade-base-fixture-'))


    def restored(self, name='state', *, convert=True):
        fixture = load_fixture(self.root / name)
        if convert:
            schema_upgrade.upgrade(fixture['state'])
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
        fixture, connection = self.restored(convert=False)
        self.assertEqual(fixture['sourceCommit'], 'd9e8df5d039708654f098015fbc8fc76f9fbd11d')
        self.assertEqual(fixture['sourceVersion'], '0.48.0')
        self.assertEqual(fixture['schemaVersion'], schema_upgrade.BASE_SCHEMA_VERSION)
        self.assertEqual(connection.execute('PRAGMA integrity_check').fetchone()[0], 'ok')
        self.assertEqual(connection.execute('PRAGMA foreign_key_check').fetchall(), [])
        self.assertEqual(connection.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0],
                         str(schema_upgrade.BASE_SCHEMA_VERSION))
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


    def test_upgrade_matches_fresh_structure_and_preserves_every_retained_value(self):
        fixture, connection = self.restored(convert=False)
        state = fixture['state']
        before = list(connection.iterdump())
        with self.assertRaises(Corruption):
            Database(state).initialize()
        self.assertEqual(list(connection.iterdump()), before)
        self.assertEqual(upgrade.idle_snapshot(state)['schema'], 26)
        self.assertEqual(upgrade.preflight(state, contract_changed=True)['schema'], 26)
        with self.assertRaises(BoardError):
            upgrade.preflight(state, contract_changed=False)
        files = {p.relative_to(state): p.read_bytes() for p in state.rglob('*')
                 if p.is_file() and not p.name.startswith('board.sqlite3')}
        old_tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        removed = {'cursors', 'evaluation_state', 'model_facts'}
        saved = {}
        fresh = Database(self.root / 'fresh')
        fresh.initialize()
        self.addCleanup(fresh.close)
        with fresh.read() as baseline:
            for table in old_tables - removed:
                columns = ','.join('"' + row[1] + '"' for row in baseline.execute(f'PRAGMA table_info("{table}")'))
                predicate = {"meta": " WHERE key!='schema_version'",
                             "macro_task_deletions": " WHERE state='deleting'",
                             "commands": " WHERE deletion_id IS NULL",
                             "events": " WHERE kind!='macro_task.deleted'"}.get(table, '')
                saved[table] = (columns, sorted((tuple(row) for row in connection.execute(f'SELECT {columns} FROM "{table}"{predicate}')), key=repr))
            schema_upgrade.upgrade(state)
            query = 'SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name'
            self.assertEqual([tuple(row) for row in connection.execute(query)],
                             [tuple(row) for row in baseline.execute(query)])
            for table, (columns, rows) in saved.items():
                predicate = " WHERE key!='schema_version'" if table == 'meta' else ''
                self.assertEqual(sorted((tuple(row) for row in connection.execute(f'SELECT {columns} FROM "{table}"{predicate}')), key=repr), rows, table)
        current = Database(state)
        current.initialize()
        self.addCleanup(current.close)
        self.assertEqual({p.relative_to(state): p.read_bytes() for p in state.rglob('*')
                          if p.is_file() and not p.name.startswith('board.sqlite3')}, files)
        self.assertEqual(upgrade.preflight(state, contract_changed=False)['schema'], SCHEMA_VERSION)

    def test_failed_conversion_rolls_back_all_rows_and_ddl(self):
        fixture, connection = self.restored(convert=False)
        before = list(connection.iterdump())
        convert = schema_upgrade._convert
        def fail(db):
            convert(db)
            raise RuntimeError('injected after DDL and data conversion')
        with mock.patch.object(schema_upgrade, '_convert', side_effect=fail):
            with self.assertRaisesRegex(RuntimeError, 'injected'):
                schema_upgrade.upgrade(fixture['state'])
        self.assertEqual(list(connection.iterdump()), before)
        self.assertEqual(connection.execute('PRAGMA foreign_key_check').fetchall(), [])

    def test_inconsistent_redundant_values_are_rejected_without_changes(self):
        for name, mutation in (
            ('acceptance', "UPDATE tasks SET acceptance_verdict=NULL WHERE accepted_at IS NOT NULL"),
            ('correlation', "UPDATE messages SET correlation_id='different'"),
            ('body-bytes', "UPDATE messages SET body_bytes=body_bytes+1"),
        ):
            with self.subTest(form=name):
                fixture, connection = self.restored(name, convert=False)
                connection.execute(mutation)
                connection.commit()
                before = list(connection.iterdump())
                with self.assertRaisesRegex(ValueError, 'Inconsistent'):
                    schema_upgrade.upgrade(fixture['state'])
                self.assertEqual(list(connection.iterdump()), before)

    def test_unknown_shutdown_stays_unknown_and_blocks_installation(self):
        fixture, connection = self.restored(convert=False)
        attempt = connection.execute('SELECT selected_attempt_id FROM tasks WHERE task_id=?',
                                     (fixture['stableIds']['accepted'],)).fetchone()[0]
        connection.execute("UPDATE attempts SET execution_state='uncertain',shutdown_confirmed=0 WHERE attempt_id=?", (attempt,))
        connection.commit()
        schema_upgrade.upgrade(fixture['state'])
        self.assertEqual(tuple(connection.execute('SELECT execution_state,shutdown_confirmed FROM attempts WHERE attempt_id=?', (attempt,)).fetchone()), ('uncertain', 0))
        with self.assertRaises(BoardError) as caught:
            upgrade.preflight(fixture['state'], contract_changed=True)
        self.assertEqual(caught.exception.code, 'UPGRADE_NOT_IDLE')

    def test_base_contains_every_pruned_form_and_in_progress_deletion(self):
        _fixture, connection = self.restored(convert=False)
        cooling = connection.execute('SELECT harness,provider,consecutive_rejections,cooling_until '
                                     'FROM provider_cooling').fetchone()
        self.assertEqual(tuple(cooling), ('codex', 'openai', 1, None))
        deletion = connection.execute("SELECT deletion_id,state,micro_task_count FROM macro_task_deletions WHERE state='deleted'").fetchone()
        self.assertEqual(tuple(deletion)[1:], ('deleted', 1))
        self.assertEqual(connection.execute("SELECT COUNT(*) FROM macro_task_deletions WHERE state='deleting'").fetchone()[0], 1)
        for table in ('cursors', 'evaluation_state', 'model_facts', 'attempt_interruptions'):
            self.assertGreater(connection.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0], 0, table)
        self.assertGreater(connection.execute('SELECT COUNT(*) FROM tasks WHERE queue_position IS NOT NULL').fetchone()[0], 0)

        self.assertGreater(connection.execute('SELECT COUNT(*) FROM commands WHERE deletion_id=?',
                                             (deletion['deletion_id'],)).fetchone()[0], 0)
        self.assertEqual(connection.execute('PRAGMA foreign_key_check').fetchall(), [])
        inquiry = connection.execute('SELECT reason_code,reason_params_json FROM messages').fetchone()
        self.assertEqual(tuple(inquiry), ('inquiry-no-live-agent', '{}'))
        self.assertEqual({tuple(row) for row in connection.execute(
            'SELECT mode,note FROM evaluation_preferences')},
            {('pin', 'fixture pinned buddy'), ('exclude', 'fixture exclusion')})
        self.assertEqual(connection.execute("SELECT COUNT(*) FROM commands WHERE kind='quota.redetect'").fetchone()[0], 1)

    def test_router_stock_binds_claims_dispatches_and_current_outcomes(self):
        fixture, connection = self.restored()
        self.assertEqual([row['name'] for row in connection.execute('PRAGMA table_info(router_claims)')],
                         ['attempt_id', 'task_id', 'generation'])
        claims = connection.execute('SELECT * FROM router_claims').fetchall()
        self.assertEqual(len(claims), 2)
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
        self.assertEqual(connection.execute("SELECT COUNT(*) FROM events WHERE kind='router.claimed'").fetchone()[0], 2)

    def test_current_file_stock_and_stored_evidence_are_not_empty(self):
        from hey_my_buddy.protocol.execution import read_execution_receipt
        from hey_my_buddy.protocol.session_content import SessionFormat, SessionNotification
        fixture, connection = self.restored()
        references = [entry['params']['receipt'] for entry in fixture['operations']
                      if entry['operation'] == 'execution_result']
        self.assertEqual(len(references), 5)
        for reference in references:
            receipt = read_execution_receipt(Path(reference['path']).parent, sha256=reference['sha256'])
            self.assertIsNotNone(receipt)
            attempt = connection.execute('SELECT task_id,generation FROM attempts WHERE attempt_id=?',
                                         (receipt['attemptId'],)).fetchone()
            self.assertEqual(tuple(attempt), (receipt['taskId'], receipt['generation']))
        sessions = list((fixture['state'] / 'sessions').rglob('*.jsonl'))
        self.assertEqual(len(sessions), 3)
        for path in sessions:
            lines = path.read_text().splitlines()
            format = SessionFormat.model_validate_json(lines[0])
            self.assertEqual((format.protocol_version, format.projection_version), (1, 1))
            notification = SessionNotification.model_validate_json(lines[1])
            self.assertEqual(notification.session_id, path.stem)
        self.assertEqual({row[0] for row in connection.execute(
            "SELECT run_id FROM workflow_artifacts WHERE kind='turn-input'")},
            {fixture['stableIds']['delivered'], fixture['stableIds']['accepted']})
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
        self.assertEqual(health['status'], 'ready')
        self.assertFalse({'reviewVerification', 'systemSandbox', 'readOnlyStructured'} & health.keys())

    def test_every_other_schema_is_refused_without_writes(self):
        fixture, connection = self.restored()
        for version in ('24', '25', '0', '999', '026', '26.0', '027', '27.0', 'unrecognized'):
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

    def test_started_verification_rejects_rewritten_command_receipts(self):
        fixture, connection = self.restored()
        before = upgrade.idle_snapshot(fixture['state'], exact=True)
        target = self.root / 'runtimes' / ('b' * 32)
        health = {'runtimeContentId': target.name, 'runtimeStable': True, 'schemaVersion': SCHEMA_VERSION}
        with mock.patch.object(upgrade, 'command', return_value={'leaks': []}):
            self.assertTrue(upgrade.verify_started(fixture['state'], target, health, before)['retainedDataFingerprints'])
        command_id = connection.execute("SELECT command_id FROM commands WHERE response_json!='{}' LIMIT 1").fetchone()[0]
        connection.execute("UPDATE commands SET response_json='{}' WHERE command_id=?", (command_id,))
        connection.commit()
        with mock.patch.object(upgrade, 'command') as command:
            with self.assertRaises(BoardError) as caught:
                upgrade.verify_started(fixture['state'], target, health, before)
            self.assertEqual(caught.exception.code, 'UPGRADE_VERIFY_FAILED')
            self.assertEqual(caught.exception.details['tables'], ['commands'])
            command.assert_not_called()
