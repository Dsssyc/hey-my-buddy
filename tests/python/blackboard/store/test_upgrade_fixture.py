"""Portable schema-19 stock from the installed base's real named operations."""
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
from hey_my_buddy.blackboard.routing import router
from hey_my_buddy.blackboard.store.db import Database
from hey_my_buddy.blackboard.tasks import workspace
from hey_my_buddy.blackboard.tasks.workflow import WorkflowCoordinator
from hey_my_buddy.install import upgrade
from hey_my_buddy.private_dirs import native_root


class UpgradeFixtureTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix='upgrade-base-fixture-'))

    def restored(self, name='state'):
        fixture = load_fixture(self.root / name)
        connection = sqlite3.connect(fixture['state'] / 'board.sqlite3')
        connection.row_factory = sqlite3.Row
        self.addCleanup(connection.close)
        return fixture, connection

    def native_bindings(self, state):
        bindings = []
        for harness in ('codex', 'dsh'):
            task_id, session_id = 'fixture-' + harness, 'session-' + harness
            path = native_root(state, harness, task_id) / (hashlib.sha256(session_id.encode()).hexdigest() + '.json')
            path.parent.mkdir(parents=True)
            binding = {'taskId': task_id, 'cwd': '/fixture/checkout',
                       'buddy': {'provider': 'fixture', 'model': 'fixture', 'effort': 'fixture'}}
            if harness == 'codex':
                binding.update(threadId=session_id, lastTurnId='native-turn', lastAttemptId='attempt',
                               lastInputSha256='a' * 64)
            else:
                binding['sessionId'] = session_id
            path.write_text(json.dumps(binding))
            bindings.append((path, binding))
        return bindings

    def test_upgrade_leaves_native_bindings_byte_for_byte_unchanged(self):
        fixture, connection = self.restored()
        connection.close()
        bindings = self.native_bindings(fixture['state'])
        before = {path: path.read_bytes() for path, _ in bindings}
        board_upgrade.migrate(fixture['state'])
        for path, _ in bindings:
            self.assertEqual(path.read_bytes(), before[path])

    def test_upgrade_removes_stored_scopes_and_preserves_credentials(self):
        fixture, connection = self.restored()
        columns = [row[1] for row in connection.execute('PRAGMA table_info(agent_credentials)') if row[1] != 'scopes_json']
        projection = ','.join(columns)
        before = [tuple(row) for row in connection.execute(f'SELECT {projection} FROM agent_credentials ORDER BY credential_id')]
        self.assertTrue(before)
        connection.close()
        board_upgrade.migrate(fixture['state'])
        with sqlite3.connect(fixture['state'] / 'board.sqlite3') as current:
            self.assertEqual([row[1] for row in current.execute('PRAGMA table_info(agent_credentials)')], columns)
            self.assertEqual(list(current.execute(f'SELECT {projection} FROM agent_credentials ORDER BY credential_id')), before)

    def test_failed_upgrade_leaves_native_bindings_byte_for_byte_unchanged(self):
        fixture, connection = self.restored()
        bindings = self.native_bindings(fixture['state'])
        before = {path: path.read_bytes() for path, _ in bindings}
        connection.execute("UPDATE tasks SET spec_json='{}' WHERE task_id=?",
                           (fixture['stableIds']['delivered'],))
        connection.commit()
        connection.close()
        with self.assertRaises(KeyError):
            board_upgrade.migrate(fixture['state'])
        for path, _ in bindings:
            self.assertEqual(path.read_bytes(), before[path])
        with sqlite3.connect(fixture['state'] / 'board.sqlite3') as board:
            self.assertEqual(board.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0], '19')

    def sample(self, fixture, connection):
        run_id = fixture['stableIds']['delivered']
        row = connection.execute('SELECT * FROM workflow_runs WHERE run_id=?', (run_id,)).fetchone()
        manifest = json.loads(row['workspace_manifest_json'])
        attempt = connection.execute('SELECT selected_attempt_id FROM tasks WHERE task_id=?', (run_id,)).fetchone()[0]
        return run_id, manifest, attempt

    def test_public_operation_stock_retains_source_schema_and_history_hashes(self):
        fixture, connection = self.restored()
        self.assertEqual(fixture['sourceCommit'], '4ca5344b2103c44d425e40d9144928be54f65f60')
        self.assertEqual(fixture['sourceVersion'], '0.35.0')
        self.assertEqual(fixture['schemaVersion'], 19)
        self.assertEqual(connection.execute('PRAGMA integrity_check').fetchone()[0], 'ok')
        self.assertEqual(connection.execute('PRAGMA foreign_key_check').fetchall(), [])
        self.assertEqual(connection.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0], '19')
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
        board_bytes = (state / 'board.sqlite3').read_bytes()
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
                  'maxConcurrent': 2, 'waitCapacity': 4, 'schemaVersion': 19}
        with ExitStack() as stack:
            stack.enter_context(mock.patch.dict(os.environ, {'HMB_RUNTIME_ROOT': str(runtime_root)}))
            for name, options in (
                ('get_state_dir', {'return_value': state}),
                ('runtime.is_ready', {'return_value': True}),
                ('runtime.read_ready', {'return_value': {'sourceCommit': fixture['sourceCommit']}}),
                ('runtime.materialize', {'return_value': {'runtimeDir': str(target)}}),
                ('probe', {'return_value': health}),
                ('detach', {'return_value': None}),
                ('start', {'return_value': {**health, 'runtimeContentId': target.name, 'schemaVersion': 20}}),
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
        self.assertEqual(after['schema'], 20)
        self.assertNotEqual(after['fingerprints'], before['fingerprints'])
        self.assertNotEqual((state / 'board.sqlite3').read_bytes(), board_bytes)
        self.assertEqual({relative: (state / relative).read_bytes() for relative in history}, history)
        self.assertEqual(backup.verify(Path(result['backup']['path']))['schema'], 19)

        fresh = Database(self.root / 'fresh')
        fresh.initialize()
        with sqlite3.connect(state / 'board.sqlite3') as upgraded, fresh.read() as baseline:
            upgraded.row_factory = sqlite3.Row
            source_schema = [tuple(row) for row in upgraded.execute(
                'SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name')]
            fresh_schema = [tuple(row) for row in baseline.execute(
                'SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name')]
            self.assertEqual(source_schema, fresh_schema)
            self.assertEqual(upgraded.execute('SELECT COUNT(*) FROM workflow_lead_tasks').fetchone()[0], 0)
            self.assertEqual({row[0] for row in upgraded.execute(
                'SELECT delegation_authority FROM workflow_runs')}, {0})
            migrated_columns = {row[1] for row in upgraded.execute('PRAGMA table_info(attempts)')}
            self.assertTrue({'stop_initiator', 'grace_deadline'} <= migrated_columns)
            self.assertEqual(router.configuration(upgraded), expected_settings)
            self.assertEqual(dict(upgraded.execute('SELECT run_id,state FROM workflow_runs')), expected_runs)
            self.assertEqual(dict(upgraded.execute('SELECT run_id,request_fingerprint FROM workflow_runs')),
                             request_fingerprints)
            self.assertEqual([tuple(row) for row in upgraded.execute(
                'SELECT buddy_id,mode,reason FROM evaluation_preferences ORDER BY buddy_id')],
                expected_preferences)
            self.assertEqual(upgraded.execute('PRAGMA integrity_check').fetchone()[0], 'ok')
            self.assertEqual(upgraded.execute('PRAGMA foreign_key_check').fetchall(), [])

    def test_migration_converts_timeout_stock_and_preserves_turn_input(self):
        fixture, connection = self.restored()
        state = fixture['state']
        run_id = fixture['stableIds']['delivered']
        turn = connection.execute('SELECT input_json,input_sha256 FROM workflow_turns WHERE run_id=?', (run_id,)).fetchone()
        connection.execute('UPDATE tasks SET timeout_seconds=0 WHERE task_id=?', (run_id,))
        for column in ('spec_json', 'spec_canonical_json'):
            original = json.loads(connection.execute(f'SELECT {column} FROM tasks WHERE task_id=?', (run_id,)).fetchone()[0])
            original['timeoutSeconds'] = 0
            connection.execute(f'UPDATE tasks SET {column}=? WHERE task_id=?', (json.dumps(original), run_id))
        connection.execute(
            "INSERT INTO commands(command_id,task_id,kind,request_hash,response_json,created_at) VALUES(?,?,?,?,?,?)",
            ('fixture-submit-receipt', run_id, 'task.submit', 'old-hash',
             json.dumps({'task': {'taskId': run_id, 'adapter': 'codex', 'timeoutSeconds': 0,
                                  'spec': original, 'inputFingerprint': 'old-fingerprint'}}),
             '2026-01-01T00:00:00.000Z'))
        connection.commit()
        connection.close()
        board_upgrade.migrate(state)
        with sqlite3.connect(state / 'board.sqlite3') as current:
            task = current.execute('SELECT expected_seconds,router_budget_seconds,spec_json FROM tasks WHERE task_id=?', (run_id,)).fetchone()
            self.assertEqual(task[:2], (1800, None))
            self.assertEqual(json.loads(task[2])['expectedSeconds'], 1800)
            converted = current.execute('SELECT expected_seconds,input_json,input_sha256 FROM workflow_turns WHERE run_id=?', (run_id,)).fetchone()
            self.assertEqual(converted, (1800, *turn))
            receipt_hash, receipt_json = current.execute(
                "SELECT request_hash,response_json FROM commands WHERE command_id='fixture-submit-receipt'").fetchone()
            request_id, fingerprint = current.execute('SELECT request_id,input_fingerprint FROM tasks WHERE task_id=?',
                                                      (run_id,)).fetchone()
            expected_hash = hashlib.sha256(_json({'requestId': request_id, 'fingerprint': fingerprint}).encode()).hexdigest()
            self.assertEqual(receipt_hash, expected_hash)
            receipt = json.loads(receipt_json)['task']
            self.assertEqual(receipt['expectedSeconds'], 1800)
            self.assertEqual(receipt['inputFingerprint'], fingerprint)
            self.assertEqual(current.execute('PRAGMA foreign_key_check').fetchall(), [])

    def test_migration_freezes_router_budget_without_changing_router_policy(self):
        fixture, connection = self.restored()
        fields = [column[1] for column in connection.execute('PRAGMA table_info(tasks)')]
        template = dict(zip(fields, connection.execute('SELECT * FROM tasks LIMIT 1').fetchone()))
        decision = {'adapter': 'decision', 'cwd': '/tmp/fixture-decision', 'task': 'choose',
                    'timeoutSeconds': 250, 'requiredCapabilities': ['decision'],
                    'decision': {'decisionId': 'fixture-decision', 'kind': 'select', 'timeoutSeconds': 240}}
        template.update(task_id='fixture-decision-task', request_id='fixture-decision-task', owner='decision',
                        adapter='decision', spec_json=json.dumps(decision), spec_canonical_json=json.dumps(decision),
                        timeout_seconds=250)
        connection.execute('INSERT INTO tasks VALUES(' + ','.join('?' for _ in fields) + ')', tuple(template.values()))
        requested = json.loads(connection.execute('SELECT requested_json FROM decision_requests LIMIT 1').fetchone()[0])
        requested['timeoutSeconds'] = 240
        connection.execute('UPDATE decision_requests SET requested_json=?', (json.dumps(requested),))
        policy = [tuple(row) for row in connection.execute('SELECT * FROM router_settings')]
        connection.commit()
        connection.close()
        board_upgrade.migrate(fixture['state'])
        with sqlite3.connect(fixture['state'] / 'board.sqlite3') as current:
            expected, budget, raw = current.execute(
                "SELECT expected_seconds,router_budget_seconds,spec_json FROM tasks WHERE task_id='fixture-decision-task'").fetchone()
            self.assertIsNone(expected)
            self.assertEqual(budget, 250)
            spec = json.loads(raw)
            self.assertEqual(spec['routerBudgetSeconds'], 250)
            self.assertEqual(spec['decision']['routerBudgetSeconds'], 240)
            self.assertNotIn('timeoutSeconds', spec)
            request = json.loads(current.execute('SELECT requested_json FROM decision_requests LIMIT 1').fetchone()[0])
            self.assertEqual(request['routerBudgetSeconds'], 240)
            self.assertEqual([tuple(row) for row in current.execute('SELECT * FROM router_settings')], policy)

    def test_migration_rolls_back_invalid_source_without_consuming_base(self):
        fixture, connection = self.restored()
        connection.execute("UPDATE tasks SET spec_json='{}' WHERE task_id=?", (fixture['stableIds']['delivered'],))
        connection.commit()
        connection.close()
        with self.assertRaises(KeyError):
            board_upgrade.migrate(fixture['state'])
        with sqlite3.connect(fixture['state'] / 'board.sqlite3') as unchanged:
            self.assertEqual(unchanged.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0], '19')
            self.assertEqual([column[1] for column in unchanged.execute('PRAGMA table_info(tasks)') if column[1] == 'timeout_seconds'],
                             ['timeout_seconds'])
            self.assertEqual(unchanged.execute('PRAGMA integrity_check').fetchone()[0], 'ok')

    def test_old_worker_deadlines_are_cleared_without_rewriting_native_evidence(self):
        fixture, connection = self.restored()
        attempt_id, raw = connection.execute("SELECT attempt_id,result_json FROM attempts WHERE adapter!='decision' AND result_json IS NOT NULL LIMIT 1").fetchone()
        receipt = json.loads(raw)
        receipt['terminationReason'] = 'deadline'
        native = {'taskId': 'native-report', 'adapter': 'command', 'timeoutSeconds': 123, 'terminationReason': 'deadline'}
        receipt['result'] = native
        connection.execute('UPDATE attempts SET result_json=? WHERE attempt_id=?', (json.dumps(receipt), attempt_id))
        cached = {'attempt': {'taskId': 'service-task', 'attemptId': attempt_id, 'adapter': 'command', 'result': receipt},
                  'task': {'runId': 'service-task', 'adapter': 'command', 'timeoutSeconds': 10, 'terminationReason': 'deadline'}}
        connection.execute("INSERT INTO commands(command_id,kind,request_hash,response_json,created_at) VALUES('deadline-cache','worker.result','original-request',?, '2026-01-01')", (json.dumps(cached),))
        connection.commit()
        connection.close()
        board_upgrade.migrate(fixture['state'])
        with sqlite3.connect(fixture['state'] / 'board.sqlite3') as current:
            converted = json.loads(current.execute('SELECT result_json FROM attempts WHERE attempt_id=?', (attempt_id,)).fetchone()[0])
            self.assertIsNone(converted['terminationReason'])
            self.assertNotIn('stopInitiator', converted)
            self.assertEqual(converted['result'], native)
            cached = json.loads(current.execute("SELECT response_json FROM commands WHERE command_id='deadline-cache'").fetchone()[0])
            self.assertIsNone(cached['attempt']['result']['terminationReason'])
            self.assertEqual(cached['attempt']['result']['result'], native)
            self.assertIsNone(cached['task']['terminationReason'])
            router = board_upgrade._receipt_object({'taskId': 'router', 'adapter': 'decision', 'terminationReason': 'deadline'})
            self.assertEqual(router['terminationReason'], 'deadline')

    def assert_legacy_stop(self, actor, expected_initiator, expected_reason):
        fixture, connection = self.restored('stop-' + (actor or 'unknown'))
        attempt_id, task_id, raw = connection.execute(
            "SELECT attempt_id,task_id,result_json FROM attempts WHERE result_json IS NOT NULL LIMIT 1").fetchone()
        connection.execute("DELETE FROM events WHERE attempt_id=? AND kind='task.completed'", (attempt_id,))
        receipt = json.loads(raw)
        native = {'terminationReason': 'user-cancel', 'origin': 'native report'}
        receipt.update(terminationReason='user-cancel', shutdownConfirmed=True, result=native)
        connection.execute(
            'UPDATE attempts SET cancel_requested_at=?,result_json=? WHERE attempt_id=?',
            ('2026-01-01T00:00:00.000Z', json.dumps(receipt), attempt_id))
        cached = {'attempt': {'taskId': task_id, 'attemptId': attempt_id, 'adapter': 'command',
                              'cancelRequestedAt': '2026-01-01T00:00:00.000Z', 'result': receipt},
                  'task': {'runId': task_id, 'adapter': 'command', 'terminationReason': 'user-cancel',
                           'shutdownConfirmed': True,
                           'result': native}}
        connection.execute("INSERT INTO commands(command_id,kind,request_hash,response_json,created_at) VALUES('legacy-stop','worker.result','legacy-hash',?,'2026-01-01')",
                           (json.dumps(cached),))
        if actor is not None:
            connection.execute(
                "INSERT INTO events(task_id,kind,payload_json,created_at) VALUES(?,'workflow.cancelled',?,'2026-01-01T00:00:00.000Z')",
                (task_id, json.dumps({'actor': actor})))
        connection.execute("INSERT INTO events(task_id,attempt_id,kind,payload_json,created_at) VALUES(?,?,'attempt.finished',?,'2026-01-01T00:00:00.000Z')",
                           (task_id, attempt_id, json.dumps({'terminationReason': 'user-cancel', 'shutdownConfirmed': True,
                                                    'report': native})))
        connection.execute(
            "INSERT INTO events(task_id,attempt_id,kind,payload_json,created_at) VALUES(?,?,'task.cancelled',?,'2026-01-01T00:00:00.000Z')",
            (task_id, attempt_id, json.dumps({'taskState': 'cancelled'})))
        # A later cancellation of the same task cannot relabel this attempt.
        connection.execute(
            "INSERT INTO events(task_id,kind,payload_json,created_at) VALUES(?,'workflow.cancelled',?,'2026-01-01T00:00:00.000Z')",
            (task_id, json.dumps({'actor': 'console:later-session'})))
        connection.commit()
        connection.close()
        board_upgrade.migrate(fixture['state'])
        with sqlite3.connect(fixture['state'] / 'board.sqlite3') as current:
            initiator, deadline, raw = current.execute(
                'SELECT stop_initiator,grace_deadline,result_json FROM attempts WHERE attempt_id=?',
                (attempt_id,)).fetchone()
            self.assertEqual((initiator, deadline), (expected_initiator, None))
            converted = json.loads(raw)
            self.assertEqual(converted['terminationReason'], expected_reason)
            self.assertNotIn('stopInitiator', converted)
            self.assertEqual(converted['result'], native)
            cached = json.loads(current.execute(
                "SELECT response_json FROM commands WHERE command_id='legacy-stop'").fetchone()[0])
            self.assertEqual(cached['attempt']['stopInitiator'], expected_initiator)
            self.assertIsNone(cached['attempt']['graceDeadline'])
            self.assertEqual(cached['attempt']['result']['terminationReason'], expected_reason)
            self.assertNotIn('stopInitiator', cached['attempt']['result'])
            self.assertEqual(cached['attempt']['result']['result'], native)
            self.assertEqual(cached['task']['terminationReason'], expected_reason)
            self.assertEqual(cached['task']['stopInitiator'], expected_initiator)
            self.assertEqual(cached['task']['result'], native)
            event = json.loads(current.execute(
                "SELECT payload_json FROM events WHERE kind='attempt.finished' ORDER BY seq DESC LIMIT 1").fetchone()[0])
            self.assertEqual(event['terminationReason'], expected_reason)
            self.assertNotIn('stopInitiator', event)
            self.assertEqual(event['report'], native)

    def test_migration_uses_matching_legacy_cancellation_actor(self):
        for actor, initiator, reason in (
            ('host:fixture-host', 'host', 'forced-stop'),
            ('console:fixture-session', 'console', 'console-stop'),
            ('service-stop', 'service', 'service-stop'),
            (None, 'unknown', 'unknown-stop'),
            ('unreadable', 'unknown', 'unknown-stop'),
        ):
            with self.subTest(actor=actor):
                self.assert_legacy_stop(actor, initiator, reason)

    def test_migration_attributes_unfinished_cancel_request_without_terminal_event(self):
        fixture, connection = self.restored()
        attempt_id, task_id = connection.execute(
            "SELECT attempt_id,task_id FROM attempts LIMIT 1").fetchone()
        connection.execute("DELETE FROM events WHERE attempt_id=? AND kind='task.completed'", (attempt_id,))
        connection.execute(
            "UPDATE attempts SET execution_state='uncertain',finished_at=NULL,result_json=NULL,"
            " shutdown_confirmed=0,cancel_requested_at=? WHERE attempt_id=?",
            ('2026-01-01T00:00:00.000Z', attempt_id))
        connection.execute(
            "INSERT INTO events(task_id,kind,payload_json,created_at) VALUES(?,'workflow.cancelled',?,'2026-01-01T00:00:00.000Z')",
            (task_id, json.dumps({'actor': 'console:active-session'})))
        connection.commit()
        connection.close()
        board_upgrade.migrate(fixture['state'])
        with sqlite3.connect(fixture['state'] / 'board.sqlite3') as current:
            initiator, result = current.execute(
                'SELECT stop_initiator,result_json FROM attempts WHERE attempt_id=?', (attempt_id,)).fetchone()
            self.assertEqual(initiator, 'console')
            self.assertIsNone(result)

    def test_migration_does_not_claim_an_unconfirmed_legacy_stop(self):
        fixture, connection = self.restored()
        attempt_id, task_id, raw = connection.execute(
            "SELECT attempt_id,task_id,result_json FROM attempts WHERE result_json IS NOT NULL LIMIT 1").fetchone()
        connection.execute("DELETE FROM events WHERE attempt_id=? AND kind='task.completed'", (attempt_id,))
        receipt = json.loads(raw)
        native = {'terminationReason': 'user-cancel', 'origin': 'native report'}
        receipt.update(terminationReason='user-cancel', shutdownConfirmed=False, result=native)
        connection.execute('UPDATE attempts SET shutdown_confirmed=0,cancel_requested_at=?,result_json=? WHERE attempt_id=?',
                           ('2026-01-01T00:00:00.000Z', json.dumps(receipt), attempt_id))
        cached = {'task': {'runId': task_id, 'adapter': 'command', 'shutdownConfirmed': False,
                           'terminationReason': 'user-cancel', 'result': native}}
        connection.execute("INSERT INTO commands(command_id,kind,request_hash,response_json,created_at) VALUES('unconfirmed-stop','task.result','legacy-hash',?,'2026-01-01')",
                           (json.dumps(cached),))
        connection.execute(
            "INSERT INTO events(task_id,kind,payload_json,created_at) VALUES(?,'workflow.cancelled',?,'2026-01-01T00:00:00.000Z')",
            (task_id, json.dumps({'actor': 'host:fixture-host'})))
        connection.execute("INSERT INTO events(task_id,attempt_id,kind,payload_json,created_at) VALUES(?,?,'attempt.unconfirmed',?,'2026-01-01T00:00:00.000Z')",
                           (task_id, attempt_id, json.dumps({'terminationReason': 'user-cancel',
                                                    'shutdownConfirmed': False, 'report': native})))
        connection.execute("INSERT INTO events(task_id,attempt_id,kind,payload_json,created_at) VALUES(NULL,?,'attempt.missing-stop-evidence',?,'2026-01-01')",
                           (attempt_id, json.dumps({'terminationReason': 'user-cancel', 'report': native})))
        connection.commit()
        connection.close()
        board_upgrade.migrate(fixture['state'])
        with sqlite3.connect(fixture['state'] / 'board.sqlite3') as current:
            initiator, deadline, raw = current.execute(
                'SELECT stop_initiator,grace_deadline,result_json FROM attempts WHERE attempt_id=?',
                (attempt_id,)).fetchone()
            self.assertEqual((initiator, deadline), ('host', None))
            converted = json.loads(raw)
            self.assertIsNone(converted['terminationReason'])
            self.assertNotIn('stopInitiator', converted)
            self.assertEqual(converted['result'], native)
            cached = json.loads(current.execute(
                "SELECT response_json FROM commands WHERE command_id='unconfirmed-stop'").fetchone()[0])
            self.assertIsNone(cached['task']['terminationReason'])
            self.assertEqual(cached['task']['stopInitiator'], 'host')
            self.assertEqual(cached['task']['result'], native)
            event = json.loads(current.execute(
                "SELECT payload_json FROM events WHERE kind='attempt.unconfirmed'").fetchone()[0])
            self.assertIsNone(event['terminationReason'])
            self.assertNotIn('stopInitiator', event)
            self.assertEqual(event['report'], native)
            missing = json.loads(current.execute(
                "SELECT payload_json FROM events WHERE kind='attempt.missing-stop-evidence'").fetchone()[0])
            self.assertIsNone(missing['terminationReason'])
            self.assertNotIn('stopInitiator', missing)
            self.assertEqual(missing['report'], native)

    def test_installed_upgrade_restores_schema19_backup_when_conversion_fails(self):
        fixture, connection = self.restored()
        connection.close()
        state = fixture['state']
        original = (state / 'board.sqlite3').read_bytes()
        runtime_root = self.root / 'runtimes'
        previous, target = runtime_root / ('a' * 32), runtime_root / ('b' * 32)
        previous.mkdir(parents=True)
        target.mkdir()
        endpoint = {'runtimeIdentity': 'runtime:' + previous.name, 'serviceId': 'fixture-old',
                    'pid': 123, 'contractVersion': 'fixture-contract'}
        (state / 'control.json').write_text(json.dumps(endpoint))
        health = {**endpoint, 'runtimeContentId': previous.name, 'runtimeStable': True,
                  'maxConcurrent': 2, 'waitCapacity': 4, 'schemaVersion': 19}
        with ExitStack() as stack:
            stack.enter_context(mock.patch.dict(os.environ, {'HMB_RUNTIME_ROOT': str(runtime_root)}))
            for name, options in (
                ('get_state_dir', {'return_value': state}),
                ('runtime.is_ready', {'return_value': True}),
                ('runtime.read_ready', {'return_value': {'sourceCommit': fixture['sourceCommit']}}),
                ('runtime.materialize', {'return_value': {'runtimeDir': str(target)}}),
                ('probe', {'return_value': health}),
                ('detach', {'return_value': None}),
                ('start', {'return_value': health}),
                ('command', {'return_value': {'leaks': []}}),
                ('write_active_runtime', {'return_value': None}),
                ('board_upgrade.migrate', {'side_effect': RuntimeError('injected conversion failure')}),
            ):
                stack.enter_context(mock.patch('hey_my_buddy.install.upgrade.' + name, **options))
            result = upgrade.upgrade({})
        self.assertFalse(result['upgraded'], result)
        self.assertEqual(result['rollback']['schemaVersion'], 19)
        self.assertEqual((state / 'board.sqlite3').read_bytes(), original)
        self.assertEqual(backup.verify(Path(result['backup']['path']))['schema'], 19)
        self.assertFalse((state / 'upgrade.json').exists())
