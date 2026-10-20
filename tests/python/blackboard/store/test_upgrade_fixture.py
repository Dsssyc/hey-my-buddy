"""The immutable schema-25 fixture exercises the explicit current upgrade."""
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
from hey_my_buddy.blackboard.routing import router
from hey_my_buddy.blackboard.store.db import Corruption, Database, SCHEMA_VERSION, canonical_json
from hey_my_buddy.blackboard.store import upgrade as schema_upgrade
from hey_my_buddy.blackboard.tasks import workspace
from hey_my_buddy.blackboard.tasks.workflow import WorkflowCoordinator
from hey_my_buddy.errors import BoardError
from hey_my_buddy.install import upgrade


class UpgradeFixtureTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix='upgrade-base-fixture-'))


    def restored(self, name='state', *, base=False):
        fixture = load_fixture(self.root / name)
        if not base:
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
        fixture, connection = self.restored(base=True)
        self.assertEqual(fixture['sourceCommit'], '137caef7b42c26616fbaf4fcaf36c1252449bd76')
        self.assertEqual(fixture['sourceVersion'], '0.46.0')
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


    def test_base_requires_explicit_upgrade_and_matches_fresh_structure(self):
        fixture, connection = self.restored(base=True)
        state = fixture['state']
        stock = list(connection.iterdump())
        fresh = Database(self.root / 'fresh')
        fresh.initialize()
        self.addCleanup(fresh.close)
        current = Database(state)
        with self.assertRaises(Corruption):
            current.initialize()
        self.assertEqual(list(connection.iterdump()), stock)
        connection.close()
        schema_upgrade.upgrade(state)
        connection = sqlite3.connect(state / 'board.sqlite3')
        self.addCleanup(connection.close)
        self.assertEqual(connection.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0],
                         str(SCHEMA_VERSION))
        current.initialize()
        current.close()
        with fresh.read() as baseline:
            query = 'SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name'
            self.assertEqual([tuple(row) for row in connection.execute(query)],
                             [tuple(row) for row in baseline.execute(query)])
            for table, in connection.execute("SELECT name FROM sqlite_master WHERE type='table'"):
                self.assertEqual([tuple(row) for row in connection.execute(f'PRAGMA table_info({table})')],
                                 [tuple(row) for row in baseline.execute(f'PRAGMA table_info({table})')])
        self.assertEqual(upgrade.idle_snapshot(state)['schema'], SCHEMA_VERSION)

    def test_base_contains_current_stored_forms(self):
        _fixture, connection = self.restored()
        cooling = connection.execute('SELECT harness,provider,consecutive_rejections,cooling_until '
                                     'FROM provider_cooling').fetchone()
        self.assertEqual(tuple(cooling), ('codex', 'openai', 1, None))
        deletion = connection.execute('SELECT deletion_id,state,micro_task_count FROM macro_task_deletions').fetchone()
        self.assertEqual(tuple(deletion)[1:], ('deleted', 1))
        self.assertGreater(connection.execute('SELECT COUNT(*) FROM commands WHERE deletion_id=?',
                                             (deletion['deletion_id'],)).fetchone()[0], 0)
        self.assertEqual(connection.execute('PRAGMA foreign_key_check').fetchall(), [])
        inquiry = connection.execute('SELECT reason_code,reason_params_json FROM messages').fetchone()
        self.assertEqual(tuple(inquiry), ('inquiry-no-live-agent', '{}'))
        self.assertEqual({tuple(row) for row in connection.execute(
            'SELECT mode,note FROM evaluation_preferences')},
            {('pin', 'fixture pinned buddy'), ('exclude', 'fixture exclusion')})
        self.assertEqual(connection.execute("SELECT COUNT(*) FROM commands WHERE kind='quota.redetect'").fetchone()[0], 1)

    def test_explicit_upgrade_converts_mutable_facts_and_preserves_sealed_content(self):
        fixture, connection = self.restored('converted', base=True)
        state = fixture['state']
        run_id, manifest, attempt_id = self.sample(fixture, connection)
        generation = connection.execute('SELECT generation FROM attempts WHERE attempt_id=?',
                                        (attempt_id,)).fetchone()[0]
        decision_id = connection.execute('SELECT decision_id FROM decision_requests LIMIT 1').fetchone()[0]
        connection.execute('INSERT INTO router_requests VALUES(?,?,?)', (decision_id, '{}', '2026-01-01T00:00:00Z'))
        document = '{"sealed":"unchanged"}'
        document_hash = hashlib.sha256(document.encode()).hexdigest()
        connection.execute('INSERT INTO router_dispatches VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                           (run_id, decision_id, 0, 'codex:openai:fixture-alpha:high', 'codex', 'openai',
                            'fixture-alpha', 'high', document, document_hash, '2026-01-01T00:00:00Z'))
        connection.execute('INSERT INTO router_claims VALUES(?,?,?,?)',
                           (attempt_id, run_id, generation, '{"tools":false}'))
        connection.execute("UPDATE buddies SET capabilities_json=? WHERE buddy_id=?",
                           (canonical_json(['execution:codex', 'decision', 'routing:fast', 'permission:read',
                                            'input:text']), 'codex:openai:fixture-alpha:high'))
        health = json.loads(connection.execute("SELECT record_json FROM harness_health WHERE harness='codex'").fetchone()[0])
        health.update({'reviewVerification': 'old', 'readOnlyStructured': True})
        connection.execute("UPDATE harness_health SET record_json=? WHERE harness='codex'", (canonical_json(health),))
        request_id = 'upgrade-routing-fixture'
        claimed = {'plane': 'work', 'requestId': request_id, 'buddyId': 'codex:openai:fixture-alpha:high'}
        connection.execute("INSERT INTO events(kind,payload_json,created_at) VALUES('router.claimed',?,?)",
                           (canonical_json(claimed), '2026-01-01T00:00:00Z'))
        outcomes = {}
        for outcome in ('answered', 'no_answer'):
            payload = {'plane': 'work', 'requestId': request_id, 'routerIndex': 0,
                       'buddyId': 'codex:openai:fixture-alpha:high', 'phase': outcome,
                       'taskId': None, 'attemptId': None, 'outcome': outcome, 'code': None}
            if outcome == 'no_answer':
                payload['selectionRetry'] = True
            seq = connection.execute('INSERT INTO events(kind,payload_json,created_at) VALUES(?,?,?)',
                                     (f'router.{outcome}', canonical_json(payload),
                                      '2026-01-01T00:00:00Z')).lastrowid
            receipt = hashlib.sha256(canonical_json(['work', request_id,
                outcome == 'no_answer', 0, outcome, None, None, outcome]).encode()).hexdigest()
            connection.execute('INSERT INTO router_outcomes VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
                               (receipt, 'work', request_id, 0, payload['buddyId'], outcome, None, None,
                                outcome, None, seq, '2026-01-01T00:00:00Z'))
            outcomes[outcome] = (seq, receipt)
        connection.commit()
        frozen = {
            'turns': [tuple(row) for row in connection.execute('SELECT input_json,input_sha256 FROM workflow_turns ORDER BY turn_id')],
            'artifacts': [tuple(row) for row in connection.execute('SELECT artifact_id,manifest_json FROM workflow_artifacts ORDER BY artifact_id')],
            'dispatch': tuple(connection.execute('SELECT document_json,input_sha256 FROM router_dispatches').fetchone()),
        }
        output = json.loads(connection.execute("SELECT manifest_json FROM workflow_artifacts WHERE run_id=? AND kind='output'",
                                               (run_id,)).fetchone()[0])
        file_path = Path(output['diffPath'])
        file_bytes = file_path.read_bytes()
        connection.close()

        schema_upgrade.upgrade(state)
        with sqlite3.connect(state / 'board.sqlite3') as migrated:
            migrated.row_factory = sqlite3.Row
            self.assertEqual([row[1] for row in migrated.execute('PRAGMA table_info(router_claims)')],
                             ['attempt_id', 'task_id', 'generation'])
            self.assertEqual(tuple(migrated.execute('SELECT * FROM router_claims').fetchone()),
                             (attempt_id, run_id, generation))
            self.assertEqual(json.loads(migrated.execute('SELECT capabilities_json FROM buddies WHERE buddy_id=?',
                ('codex:openai:fixture-alpha:high',)).fetchone()[0]), ['execution:codex', 'input:text'])
            health = json.loads(migrated.execute("SELECT record_json FROM harness_health WHERE harness='codex'").fetchone()[0])
            self.assertFalse({'reviewVerification', 'systemSandbox', 'readOnlyStructured'} & health.keys())
            self.assertEqual(json.loads(migrated.execute("SELECT payload_json FROM events WHERE kind='router.claimed'"
                                                            ).fetchone()[0])['plane'], 'routing')
            for outcome, (seq, old_receipt) in outcomes.items():
                row = migrated.execute('SELECT * FROM router_outcomes WHERE event_seq=?', (seq,)).fetchone()
                self.assertEqual(row['plane'], 'routing')
                self.assertNotEqual(row['receipt_id'], old_receipt)
                event = json.loads(migrated.execute('SELECT payload_json FROM events WHERE seq=?', (seq,)).fetchone()[0])
                self.assertEqual(event['plane'], 'routing')
                expected = hashlib.sha256(canonical_json(['routing', request_id,
                    outcome == 'no_answer', 0, outcome, None, None, outcome]).encode()).hexdigest()
                self.assertEqual(row['receipt_id'], expected)
            self.assertEqual([tuple(row) for row in migrated.execute('SELECT input_json,input_sha256 FROM workflow_turns ORDER BY turn_id')], frozen['turns'])
            self.assertEqual([tuple(row) for row in migrated.execute('SELECT artifact_id,manifest_json FROM workflow_artifacts ORDER BY artifact_id')], frozen['artifacts'])
            self.assertEqual(tuple(migrated.execute('SELECT document_json,input_sha256 FROM router_dispatches').fetchone()), frozen['dispatch'])
            self.assertEqual(migrated.execute('PRAGMA foreign_key_check').fetchall(), [])
        self.assertEqual(file_path.read_bytes(), file_bytes)

    def test_failed_conversion_rolls_back_all_database_changes(self):
        fixture, connection = self.restored('rollback', base=True)
        before = list(connection.iterdump())
        connection.close()
        rebuild = schema_upgrade._rebuild_router_tables
        def fail_after_rebuild(db):
            rebuild(db)
            raise RuntimeError('injected')
        with mock.patch.object(schema_upgrade, '_rebuild_router_tables', side_effect=fail_after_rebuild):
            with self.assertRaisesRegex(RuntimeError, 'injected'):
                schema_upgrade.upgrade(fixture['state'])
        with sqlite3.connect(fixture['state'] / 'board.sqlite3') as returned:
            self.assertEqual(list(returned.iterdump()), before)
            self.assertEqual(returned.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0],
                             str(schema_upgrade.BASE_SCHEMA_VERSION))

    def test_every_other_schema_is_refused_without_writes(self):
        fixture, connection = self.restored(base=True)
        for version in ('24', '0', '999', '025'):
            with self.subTest(schema=version):
                connection.execute("UPDATE meta SET value=? WHERE key='schema_version'", (version,))
                connection.commit()
                before = (fixture['state'] / 'board.sqlite3').read_bytes()
                with self.assertRaises(Corruption):
                    Database(fixture['state']).initialize()
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
