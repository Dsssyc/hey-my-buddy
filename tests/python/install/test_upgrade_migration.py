"""The fixed schema-16 board follows the install coordinator's migration/backup path."""
from contextlib import closing, ExitStack
import json
import os
from pathlib import Path
import sqlite3
from unittest import mock

from support import BoardTestCase, InProcessBoard
from blackboard.store.test_migrations import rows, schema_shape, DELIVERED
from blackboard.store.upgrade_fixture import load_fixture
from blackboard.tasks import test_workspace_identity as identity_fixtures
from hey_my_buddy.blackboard.store import backup, migrations
from hey_my_buddy.blackboard.store.db import Database, SCHEMA_VERSION, canonical_json
from hey_my_buddy.blackboard.store.store import BoardStore
from hey_my_buddy.blackboard.store.workspace_identity_migration import migrate_workspace_identity
from hey_my_buddy.errors import BoardError
from hey_my_buddy.install import upgrade


class UpgradeMigrationTests(BoardTestCase):
    def setUp(self):
        super().setUp()
        self.state = self.directory / 'state'
        self.fixture = load_fixture(self.state)

    def backed_up(self):
        current = Path(backup.create(BoardStore(self.state))["path"])
        manifest = backup.verify(current)
        return current, {**manifest["databaseSnapshot"], "schema": manifest["schema"]}

    def all_rows(self):
        with closing(sqlite3.connect(self.state / 'board.sqlite3')) as connection:
            return rows(connection)

    def account_operation(self):
        operation_id = self.fixture['accountOperation']['operationId']
        with closing(sqlite3.connect(self.state / 'board.sqlite3')) as connection:
            return connection.execute('SELECT value FROM meta WHERE key=?', (operation_id,)).fetchone()

    def test_uncertain_base_operation_is_backed_up_then_retired_without_rewriting_history(self):
        original = self.account_operation()
        self.assertEqual(json.loads(original[0])['status'], 'uncertain')
        current, before = self.backed_up()
        self.assertEqual(self.account_operation(), original)
        migration, expected = upgrade.migrate_board(self.state, before)
        self.assertEqual(migration['toSchema'], 17)
        self.assertIsNone(self.account_operation())
        _identity, expected = migrate_workspace_identity(self.state, expected)
        target = self.directory / 'private-runtime'
        health = {'runtimeContentId': target.name, 'runtimeStable': True, 'schemaVersion': 17}
        with mock.patch.object(upgrade, 'command', return_value={'leaks': []}):
            verified = upgrade.verify_started(self.state, target, health, expected)
        self.assertTrue(verified['retainedDataFingerprints'])
        upgrade.restore(self.state, current)
        self.assertEqual(upgrade.idle_snapshot(self.state)['schema'], 16)
        self.assertEqual(self.account_operation(), original)

    def test_active_or_uncertain_account_reservations_do_not_assert_task_shutdown(self):
        for status in ('active', 'uncertain'):
            with self.subTest(status=status):
                with closing(sqlite3.connect(self.state / 'board.sqlite3', isolation_level=None)) as connection:
                    connection.execute("UPDATE meta SET value=json_set(value,'$.status',?) WHERE key LIKE 'account-operation:%' OR key LIKE 'account-mutation:%'", (status,))
                original = self.all_rows()
                self.assertEqual(upgrade.idle_snapshot(self.state)['schema'], 16)
                self.assertEqual(self.all_rows(), original)

    def test_backup_records_16_and_trials_the_same_migration_without_touching_the_source(self):
        old = self.all_rows()
        original = migrations.migrate_16_to_17
        with mock.patch.object(migrations, 'migrate_16_to_17', wraps=original) as migrated:
            _current, before = self.backed_up()
        self.assertGreater(migrated.call_count, 0)
        self.assertEqual(before['schema'], 16)
        self.assertEqual(upgrade.idle_snapshot(self.state)['schema'], 16)
        self.assertEqual(self.all_rows(), old)

    def test_migration_and_identity_path_preserve_history_and_bind_the_updated_snapshot(self):
        _current, before = self.backed_up()
        old = self.all_rows()
        summary, expected = upgrade.migrate_board(self.state, before)
        self.assertEqual(summary['toSchema'], 17)
        self.assertEqual(expected['schema'], 17)
        migrated = self.all_rows()
        for table in old:
            if table not in ('meta', 'catalog_current', 'harness_health', 'catalog_pending', 'evaluation_profiles'):
                self.assertEqual(migrated[table], old[table], table)
        identity, expected = migrate_workspace_identity(self.state, expected)
        self.assertEqual(expected, {**upgrade.idle_snapshot(self.state), 'runtimeSettings':{}})
        self.assertEqual(identity['mapped'], [])
        self.assertEqual(identity['kept'], {})
        new = self.all_rows()
        for table in migrated:
            if table not in ('meta', 'objectives', 'workspace_reservations'):
                self.assertEqual(new[table], migrated[table], table)
        before_shape = None
        with closing(sqlite3.connect(self.state / 'board.sqlite3')) as connection:
            before_shape = schema_shape(connection)
        Database(self.state).initialize()
        with closing(sqlite3.connect(self.state / 'board.sqlite3')) as connection:
            self.assertEqual(schema_shape(connection), before_shape)

    def test_schema_16_restore_is_exact_after_a_successful_migration(self):
        current, before = self.backed_up()
        old = self.all_rows()
        upgrade.migrate_board(self.state, before)
        self.assertEqual(upgrade.idle_snapshot(self.state)['schema'], 17)
        upgrade.restore(self.state, current)
        self.assertEqual(self.all_rows(), old)
        self.assertEqual(upgrade.idle_snapshot(self.state)['fingerprints'], before['fingerprints'])
        self.assertEqual(upgrade.idle_snapshot(self.state)['schema'], 16)

    def test_public_reads_and_delivered_host_conclusion_are_successful_and_idempotent_after_identity_migration(self):
        _current, before = self.backed_up()
        migration, expected = upgrade.migrate_board(self.state, before)
        identity, expected = migrate_workspace_identity(self.state, expected)
        self.assertEqual(identity['kept'], {})
        self.assertEqual(identity['mapped'], [])
        self.assertEqual(expected, {**upgrade.idle_snapshot(self.state), 'runtimeSettings':{}})
        board = InProcessBoard(self.state)
        board.service.automatic_discovery = False
        snapshot = board.call('console_snapshot', {})
        self.assertEqual(snapshot['configuration']['routerProfileIds'], self.fixture['routerProfileIds'])
        models = board.call('model_profiles', {'includeUnavailable': True})['profiles']
        self.assertTrue(models)
        self.assertTrue(all(model['catalogState'] == 'unknown' for model in models))
        self.assertTrue(all(model['catalogReason'] == 'ACCOUNT_STATUS_UNKNOWN' for model in models))
        delivered = board.call('workflow_get', {'runId':DELIVERED})
        self.assertEqual(delivered['state'], 'delivered')
        self.assertIsNone(delivered['hostConclusion'])
        with board.store.db.read() as db:
            for adapter in migration['catalogUnknown']:
                self.assertIsNone(db.execute('SELECT value FROM meta WHERE key=?',
                                            ('catalog-account-status:' + adapter,)).fetchone(), adapter)
            worker_output = db.execute("SELECT * FROM workflow_artifacts WHERE run_id=? AND kind='output'",
                                       (DELIVERED,)).fetchone()
        worker_manifest = json.loads(worker_output['manifest_json'])
        worker_file = Path(worker_manifest['diffPath']).with_name('output.json')
        worker_bytes = worker_file.read_bytes()
        host_file = worker_file.parent.with_name(worker_file.parent.name + '-host') / 'output.json'
        self.assertFalse(host_file.exists())

        def control(run_id):
            with board.store.db.read() as db:
                row = db.execute('SELECT host_id,owner_generation FROM workflow_runs WHERE run_id=?', (run_id,)).fetchone()
            return {'hostId':row['host_id'], 'ownerGeneration':row['owner_generation'],
                    'controlToken':board.store.db.control_token(run_id, row['owner_generation'])}

        params = {'runId':DELIVERED, 'note':'private upgraded fixture', **control(DELIVERED)}
        concluded = board.call('workflow_conclude', params)
        self.assertFalse(concluded['duplicate'])
        self.assertEqual(concluded['conclusion']['executionStatus'], 'delivered')
        self.assertEqual(concluded['state'], 'delivered')
        self.assertIsNotNone(concluded['conclusion']['artifactId'])
        replay = board.call('workflow_conclude', params)
        self.assertTrue(replay['duplicate'])
        self.assertEqual(replay['conclusion'], concluded['conclusion'])
        self.assertEqual(board.call('workflow_get', {'runId':DELIVERED})['hostConclusion'], concluded['conclusion'])
        with board.store.db.read() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM workflow_host_conclusions WHERE run_id=?',
                                        (DELIVERED,)).fetchone()[0], 1)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM events WHERE task_id=? AND kind='workflow.host_concluded'",
                                        (DELIVERED,)).fetchone()[0], 1)
            artifact = db.execute('SELECT manifest_sha256 FROM workflow_artifacts WHERE artifact_id=?',
                                  (concluded['conclusion']['artifactId'],)).fetchone()
            self.assertEqual(tuple(db.execute('SELECT * FROM workflow_artifacts WHERE artifact_id=?',
                                              (worker_output['artifact_id'],)).fetchone()), tuple(worker_output))
        sealed = json.loads(host_file.read_text())
        self.assertEqual(sealed['kind'], 'host-output')
        self.assertEqual(sealed['snapshotSha256'], artifact['manifest_sha256'])
        self.assertEqual(worker_file.read_bytes(), worker_bytes)

    def test_a_stale_verified_backup_is_refused_before_migration(self):
        _current, before = self.backed_up()
        with closing(sqlite3.connect(self.state / 'board.sqlite3', isolation_level=None)) as connection:
            connection.execute("UPDATE evaluation_profiles SET label='changed after backup'")
        with self.assertRaises(BoardError) as caught:
            upgrade.migrate_board(self.state, before)
        self.assertIn('evaluation_profiles', caught.exception.details['tables'])
        self.assertEqual(upgrade.idle_snapshot(self.state)['schema'], 16)

    def seed_router(self, status, *, active=False):
        with closing(sqlite3.connect(self.state / 'board.sqlite3', isolation_level=None)) as connection:
            task_id = 'old-router-task' if active else None
            if active:
                connection.execute("INSERT INTO tasks(task_id,request_id,owner,spec_json,spec_canonical_json,"
                    "input_fingerprint,fingerprint_version,adapter,cwd,timeout_seconds,state,revision,created_at,updated_at)"
                    " VALUES(?,'old-router-task','decision','{}','{}','old-digest',1,'decision','<private>',60,"
                    "?,1,'then','then')", (task_id, status))
            connection.execute("INSERT INTO evaluation_decisions(decision_id,status,task,table_revision,reason,created_at)"
                " VALUES('old-router',?,'retained Router request',0,'legacy','then')", (status,))
            connection.execute("INSERT INTO decision_requests(decision_id,request_id,kind,input_fingerprint,task_id,"
                "input_json,input_sha256,output_json,requested_json,created_at,updated_at)"
                " VALUES('old-router','old-route-request','select','old-fingerprint',?,?,'raw-old-hash',?,?,'then','then')",
                (task_id, '{"oldFrozenInput": true}', '{"legacyOutput": true}',
                 '{"routerProfileId":"old-buddy","routingMode":"review"}'))
            connection.execute("INSERT INTO events(task_id,kind,payload_json,created_at) VALUES(?,?,'{}','then')",
                (task_id, 'decision.' + status.replace('-', '_')))

    def test_old_terminal_router_history_is_kept_without_new_execution_facts(self):
        self.seed_router('needs-host')
        before = upgrade.idle_snapshot(self.state)
        old = self.all_rows()
        upgrade.migrate_board(self.state, before)
        new = self.all_rows()
        for table in ('evaluation_decisions', 'decision_requests', 'events'):
            self.assertEqual(new[table], old[table])
        for table in ('router_requests', 'router_dispatches', 'router_claims', 'router_outcomes'):
            self.assertEqual(new[table], old[table])

    def test_active_old_router_is_refused_by_idle_without_cancelling_or_rewriting(self):
        for status in ('queued', 'running'):
            with self.subTest(status=status):
                self.state = self.directory / status
                load_fixture(self.state)
                self.seed_router(status, active=True)
                old = self.all_rows()
                with self.assertRaises(BoardError) as caught:
                    upgrade.idle_snapshot(self.state)
                self.assertEqual(caught.exception.code, 'UPGRADE_NOT_IDLE')
                self.assertIn({'runId':'old-router-task','state':status}, caught.exception.details['active'])
                self.assertEqual(self.all_rows(), old)

    def test_shutdown_unknown_old_router_attempt_is_not_idle(self):
        self.seed_router('running', active=True)
        with closing(sqlite3.connect(self.state / 'board.sqlite3', isolation_level=None)) as connection:
            connection.execute("UPDATE tasks SET state='completed' WHERE task_id='old-router-task'")
            connection.execute("INSERT INTO attempts(attempt_id,task_id,generation,nonce_verifier,claim_request_id,"
                "execution_state,adapter,shutdown_confirmed,created_at,updated_at)"
                " VALUES('old-router-attempt','old-router-task',1,'nonce','claim','finished','decision',0,'then','then')")
        old = self.all_rows()
        with self.assertRaises(BoardError) as caught:
            upgrade.idle_snapshot(self.state)
        self.assertIn('old-router-attempt', caught.exception.details['unresolvedAttempts'])
        self.assertEqual(self.all_rows(), old)

    def test_verification_reports_exact_inventory_and_retained_value_failures(self):
        _current, before = self.backed_up()
        target = self.directory / 'runtime-previous'
        health = {'runtimeContentId':target.name, 'runtimeStable':True, 'schemaVersion':16}
        with mock.patch('hey_my_buddy.install.upgrade.command', return_value={'leaks':[]}):
            self.assertEqual(upgrade.verify_started(self.state, target, health, before)['schemaVersion'], 16)
            with closing(sqlite3.connect(self.state / 'board.sqlite3', isolation_level=None)) as connection:
                connection.execute('CREATE TABLE stray(value TEXT)')
            with self.assertRaises(BoardError) as caught:
                upgrade.verify_started(self.state, target, health, before)
        self.assertEqual(caught.exception.details['tables'], ['stray'])

    def failed_upgrade(self, verification):
        root = self.directory / (self.state.name + '-runtimes')
        previous, target = root / ('a' * 32), root / ('b' * 32)
        previous.mkdir(parents=True)
        target.mkdir()
        endpoint = {'runtimeIdentity':'runtime:' + previous.name, 'serviceId':'private-service',
                    'pid':123, 'contractVersion':'private-fixture'}
        (self.state / 'control.json').write_text(json.dumps(endpoint))
        health = {**endpoint, 'maxConcurrent':2, 'waitCapacity':4}
        started = {'runtimeStable':True, 'maxConcurrent':2, 'waitCapacity':4}
        with ExitStack() as stack:
            stack.enter_context(mock.patch.dict(os.environ, {'BUDDY_RUNTIME_ROOT':str(root)}))
            for name, options in (
                ('get_state_dir', {'return_value':self.state}),
                ('runtime.is_ready', {'return_value':True}),
                ('runtime.read_ready', {'return_value':{'sourceCommit':'fixture'}}),
                ('runtime.materialize', {'return_value':{'runtimeDir':str(target)}}),
                ('probe', {'return_value':health}), ('detach', {}),
                ('start', {'side_effect':lambda state, path: {
                    **started, 'runtimeContentId':path.name,
                    'schemaVersion': SCHEMA_VERSION if path == target else self.fixture['schemaVersion']}}),
                ('verify_started', {'side_effect':verification}),
            ):
                stack.enter_context(mock.patch('hey_my_buddy.install.upgrade.' + name, **options))
            stack.enter_context(mock.patch('hey_my_buddy.install.launcher.write_active_runtime'))
            return upgrade.upgrade({})

    def test_failed_upgrade_response_and_journal_share_the_structured_cause_and_tables(self):
        old = self.all_rows()
        with closing(sqlite3.connect(self.state / 'board.sqlite3')) as connection:
            old_shape = schema_shape(connection)
        original = upgrade.migrate_board

        def failed_migration(*args):
            # Let backup trials succeed, then fail SQLite in the actual board
            # migration with context independent of the injected SQL text.
            with mock.patch.object(migrations, '_NEW_DDL', migrations._NEW_DDL + (
                    ('router_outcomes', 'CREATE INDEX broken ON no_such_table(value)'),)):
                return original(*args)

        with mock.patch.object(upgrade, 'migrate_board', side_effect=failed_migration):
            result = self.failed_upgrade([{'retainedDataFingerprints':True}])
        journal = json.loads((self.state / 'upgrade-last.json').read_text())
        self.assertFalse(result['upgraded'])
        self.assertNotIn('failure', result)
        self.assertEqual(result['error']['code'], 'UPGRADE_ROLLED_BACK')
        self.assertEqual(result['error']['details']['reason'], {
            'code':'UPGRADE_MIGRATION_FAILED', 'message':'no such table: main.no_such_table',
            'details':{'tables':['router_outcomes']},
        })
        self.assertEqual(journal['failure'], result['error']['details']['reason'])
        self.assertEqual(journal['phase'], 'rolled-back')
        self.assertIsNotNone(result['backup'])
        self.assertFalse((self.state / 'upgrade.json').exists())
        self.assertEqual(self.all_rows(), old)
        with closing(sqlite3.connect(self.state / 'board.sqlite3')) as connection:
            self.assertEqual(schema_shape(connection), old_shape)

    def test_non_board_error_rolls_back_with_tables_in_response_and_durable_journal(self):
        old = self.all_rows()
        with closing(sqlite3.connect(self.state / 'board.sqlite3')) as connection:
            old_shape = schema_shape(connection)

        def fail_target_verification(state, runtime_path, observed, expected):
            # The real backup and migration have finished before a plain Python
            # exception reaches the coordinator's rollback/error boundary.
            self.assertEqual(observed['schemaVersion'], upgrade.idle_snapshot(state)['schema'])
            if runtime_path.name == 'b' * 32:
                self.assertEqual(upgrade.idle_snapshot(state)['schema'], 17)
                raise RuntimeError('private target verification failure')
            return {'retainedDataFingerprints':True}

        result = self.failed_upgrade(fail_target_verification)
        journal = json.loads((self.state / 'upgrade-last.json').read_text())
        self.assertFalse(result['upgraded'])
        self.assertEqual(result['error']['code'], 'UPGRADE_ROLLED_BACK')
        self.assertNotIn('failure', result)
        self.assertEqual(result['error']['details']['reason'], {
            'code':'RuntimeError', 'message':'private target verification failure', 'details':{'tables':[]},
        })
        self.assertEqual(journal['failure'], result['error']['details']['reason'])
        self.assertEqual(journal['failure']['details']['tables'], [])
        self.assertEqual(journal['phase'], 'rolled-back')
        self.assertIsNotNone(result['backup'])
        self.assertFalse((self.state / 'upgrade.json').exists())
        self.assertEqual(self.all_rows(), old)
        with closing(sqlite3.connect(self.state / 'board.sqlite3')) as connection:
            self.assertEqual(schema_shape(connection), old_shape)

    def test_unproven_recovery_keeps_the_same_structured_reason_in_both_journals_and_error(self):
        failure = BoardError('UPGRADE_VERIFY_FAILED', 'declared target failure', tables=['catalog_current'])
        recovery = BoardError('UPGRADE_VERIFY_FAILED', 'declared recovery failure', tables=['events'])
        with self.assertRaises(BoardError) as caught:
            self.failed_upgrade([failure, recovery])
        self.assertEqual(caught.exception.code, 'UPGRADE_RECOVERY_REQUIRED')
        active = json.loads((self.state / 'upgrade.json').read_text())
        last = json.loads((self.state / 'upgrade-last.json').read_text())
        self.assertEqual(active, last)
        self.assertEqual(last['phase'], 'recovery-required')
        self.assertEqual(caught.exception.details['reason'], failure.payload())
        self.assertEqual(caught.exception.details['recoveryReason'], recovery.payload())
        self.assertEqual(last['failure'], caught.exception.details['reason'])
        self.assertEqual(last['recoveryFailure'], caught.exception.details['recoveryReason'])
        self.assertEqual(last['failure']['details']['tables'], ['catalog_current'])


class UpgradeWorkspaceIdentityTests(identity_fixtures.WorkspaceIdentityFixture):
    """Run the actual upgrade coordinator on private state without processes.

    Lifecycle process seams are substituted. The maintenance locks, verified
    backup, migration, exact fingerprint check and rollback use real files/SQL.
    No service, Worker, runtime materialization or login operation runs.
    """

    def private_upgrade(self, *, reject_verification=False):
        board, run, manifest = self.legacy_run()
        board.call("workflow_cancel", {"runId": run["runId"], "reason": "private idle fixture",
                                       **self.control(run)})
        initial = self.all_rows(board)
        root = self.directory / "private-runtimes"
        previous, target = root / ("a" * 32), root / ("b" * 32)
        previous.mkdir(parents=True)
        target.mkdir()
        endpoint = {"runtimeIdentity": "runtime:" + previous.name, "serviceId": "private-service",
                    "pid": 123, "contractVersion": "private-fixture"}
        (self.directory / "control.json").write_text(json.dumps(endpoint))
        health = {**endpoint, "maxConcurrent": 2, "waitCapacity": 4}
        started = {"runtimeContentId": target.name, "runtimeStable": True, "schemaVersion": 17,
                   "maxConcurrent": 2, "waitCapacity": 4}
        original_verify = upgrade.verify_started

        def verify(state, runtime_path, observed, expected):
            if reject_verification and runtime_path == target:
                raise BoardError("UPGRADE_VERIFY_FAILED", "private target verification failure")
            return original_verify(state, runtime_path, observed, expected)

        with ExitStack() as stack:
            stack.enter_context(mock.patch.dict(os.environ, {"BUDDY_STATE_DIR": str(self.directory),
                                                            "BUDDY_RUNTIME_ROOT": str(root)}))
            for name, options in (
                ("get_state_dir", {"return_value": self.directory}),
                ("runtime.is_ready", {"return_value": True}),
                ("runtime.read_ready", {"return_value": {"sourceCommit": "private-fixture"}}),
                ("runtime.materialize", {"return_value": {"runtimeDir": str(target)}}),
                ("probe", {"return_value": health}),
                ("detach", {}),
                ("start", {"side_effect": lambda state, runtime_path: {**started, "runtimeContentId": runtime_path.name}}),
                ("command", {"return_value": {"leaks": []}}),
                ("verify_started", {"side_effect": verify}),
            ):
                stack.enter_context(mock.patch("hey_my_buddy.install.upgrade." + name, **options))
            stack.enter_context(mock.patch("hey_my_buddy.install.launcher.write_active_runtime"))
            stack.enter_context(mock.patch("hey_my_buddy.blackboard.tasks.storage.prune_old_runtimes", return_value={"complete": True}))
            result = upgrade.upgrade({})
        return board, run, manifest, initial, result

    def test_real_upgrade_calls_identity_migration_after_verified_backup(self):
        board, run, manifest, initial, result = self.private_upgrade()
        self.assertTrue(result["upgraded"], result)
        verified_backup = backup.verify(Path(result["backup"]["path"]))
        self.assertEqual(verified_backup["schema"], 17)
        self.assertTrue(result["verification"]["retainedDataFingerprints"])
        current = self.all_rows(board)
        actual = self.workspace.inspect(manifest["path"])
        with board.store.db.read() as connection:
            self.assertEqual(connection.execute("SELECT project_id FROM objectives WHERE objective_id=?", (run["objectiveId"],)).fetchone()[0], actual["repositoryId"])
            self.assertEqual(connection.execute("SELECT checkout_id FROM workspace_reservations WHERE holder_task_id=?", (run["runId"],)).fetchone()[0], actual["checkoutId"])
        for table in initial:
            if table not in {"meta", "objectives", "workspace_reservations"}:
                self.assertEqual(current[table], initial[table], table)
        journal = json.loads((self.directory / "upgrade-last.json").read_text())
        self.assertIn(manifest["checkoutId"], journal["identityMigration"]["mapped"])

    def test_post_migration_failure_restores_exact_private_backup(self):
        board, _run, _manifest, initial, result = self.private_upgrade(reject_verification=True)
        self.assertFalse(result["upgraded"], result)
        self.assertEqual(result["rollback"]["runtimeContentId"], "a" * 32, result)
        self.assertEqual(self.all_rows(board), initial)
