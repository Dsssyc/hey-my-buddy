"""Private service transactions for account freezing, permissions and fencing."""
import json
import os
from pathlib import Path
import stat
from types import SimpleNamespace
from unittest.mock import patch

from hey_my_buddy.blackboard.catalog import accounts, catalog_store
from hey_my_buddy.blackboard.store.db import canonical_json, utc_now
from hey_my_buddy.errors import BoardError
from hey_my_buddy.blackboard.service.harness_health import read_health
from hey_my_buddy.blackboard.evaluation.native_observations import latest_quota, record_quota
from hey_my_buddy.private_dirs import account_root
from blackboard.tasks.test_workflow import WorkflowTestCase, NONCE, CONFIGURATION


class AccountServiceTests(WorkflowTestCase):
    def setUp(self):
        super().setUp()
        cleared = {'BUDDY_STATE_DIR', 'BUDDY_RUNTIME_ROOT', 'BUDDY_RUNTIME', 'BUDDY_RUNTIME_IDENTITY',
                   'BUDDY_WORKER_STATE', 'BUDDY_WORKER_ID', 'BUDDY_AGENT_CREDENTIAL',
                   'BUDDY_AGENT_CREDENTIAL_FILE', 'VIRTUAL_ENV', 'UV_PROJECT_ENVIRONMENT'}
        env = {key: value for key, value in os.environ.items() if key not in cleared}
        runtime = self.directory / 'empty-runtime'
        runtime.mkdir(mode=0o700)
        cli = self.directory / 'sentinels'
        cli.mkdir(mode=0o700)
        for name in accounts.ADAPTERS:
            path = cli / name
            path.write_text('#!/bin/sh\nexit 97\n')
            path.chmod(0o700)
            env['BUDDY_' + name.upper() + '_CLI'] = str(path)
        home = self.directory / 'home'
        home.mkdir(mode=0o700)
        env.update(BUDDY_STATE_DIR=str(self.directory), BUDDY_RUNTIME_ROOT=str(runtime),
                   HOME=str(home), CODEX_HOME=str(home / 'codex'), DSH_HOME=str(home / 'dsh'),
                   BUDDY_DEV_SOURCE='1', PATH=str(cli) + os.pathsep + env.get('PATH', ''))
        self.enterContext(patch.dict(os.environ, env, clear=True))
        self.enterContext(patch('hey_my_buddy.buddy.harnesses.discovery.discover', side_effect=AssertionError('Native CLI forbidden')))
        self.native_read = self.enterContext(patch('hey_my_buddy.buddy.harnesses.codex.account_probe.read', return_value=(None, None)))
        self.catalog_fixture()
        self.executors['codex'] = SimpleNamespace(native_resume=True, validate_turn_provenance=lambda record: None)

    def ready(self, board, adapter):
        with board.store.db.write() as db:
            account = accounts.selection(db, adapter)
            record = {'account': accounts.identity(account), 'version': 'fixture', 'command': ['/fixture/' + adapter],
                      'billingByProvider': {'openai': {'kind': 'subscription', 'source': 'fixture', 'observedAt': utc_now()}}}
            db.execute("UPDATE harness_health SET status='ready',record_json=? WHERE adapter=?", (canonical_json(record), adapter))

    def select(self, board, source, *, adapter='dsh', revision=0):
        return board.call('account_set', {'adapter': adapter, 'source': source, 'expectedRevision': revision})['account']

    def test_read_only_accounts_contract_matches_health_and_has_no_native_refresh(self):
        board = self.board()
        with board.store.db.read() as db:
            before = db.execute('SELECT count(*) FROM events').fetchone()[0]
        result = board.call('accounts', {})
        required = {'adapter', 'source', 'revision', 'credentialRevision', 'status', 'accountType', 'checkedAt',
                    'capabilities', 'reasonCode', 'guidance'}
        with board.store.db.read() as db:
            for account in result['accounts']:
                self.assertEqual(set(account), required)
                self.assertEqual(account, read_health(db, account['adapter'])['account'])
                self.assertEqual(account['source'], 'native')
                self.assertEqual(account['revision'], 0)
                self.assertEqual(account['credentialRevision'], 0)
                self.assertFalse(any(account['capabilities'].values()))
            self.assertEqual(before, db.execute('SELECT count(*) FROM events').fetchone()[0])
        self.native_read.assert_not_called()

    def test_source_cas_is_separate_from_credentials_and_worker_defaults_refused(self):
        board = self.board()
        worker = self.select(board, 'worker', adapter='codex')
        self.assertEqual((worker['revision'], worker['credentialRevision'], worker['status']), (1, 0, 'unsupported'))
        root = account_root(board.store.directory, 'codex')
        for path in (root, root.parent, root.parents[3], root.parents[4]):
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o700)
        with self.assertRaises(BoardError) as conflict:
            self.select(board, 'native', adapter='codex')
        self.assertEqual(conflict.exception.code, 'REVISION_CONFLICT')
        with self.assertRaises(BoardError) as rejected:
            accounts.execution_environment(board.store.directory, worker, dict(os.environ))
        self.assertEqual(rejected.exception.code, 'ACCOUNT_CAPABILITY_UNVERIFIED')
        switched = self.select(board, 'native', adapter='codex', revision=1)
        self.assertEqual((switched['revision'], switched['credentialRevision']), (2, 0))

    def test_named_contract_and_cli_use_the_same_current_account_operations(self):
        from hey_my_buddy.protocol.contracts import BuddyControl, CONTRACT_VERSION
        from hey_my_buddy.cli.main import METHODS
        from hey_my_buddy.protocol.transport import METHOD_MAP
        from hey_my_buddy.cli.cli_help import render
        self.assertEqual(CONTRACT_VERSION, '0.28.0')
        for name in ('accounts', 'account-set'):
            operation = name.replace('-', '_')
            self.assertTrue(hasattr(BuddyControl, operation))
            self.assertIn(name, METHODS)
            self.assertEqual(METHOD_MAP[name], ('control', operation))
        text, _ = render('account-set', METHODS)
        for field in ('adapter', 'source', 'expectedRevision'):
            self.assertIn(field, text)

    def test_running_switch_retains_harness_and_old_receipt_cannot_pollute_new_account(self):
        board = self.board()
        self.register(board)
        submitted = self.submit(board)
        first = self.claim(board)
        frozen = first['claim']['account']
        self.assertEqual(frozen['source'], 'native')
        worker = self.select(board, 'worker')
        attempt = first['claim']['attempt']
        with patch.object(board.service.harnesses, 'refresh', side_effect=AssertionError('Wrong account refresh')):
            prepared = board.call('harness_prepare', {'workerId': 'w1', 'attemptId': attempt['attemptId'],
                'generation': attempt['generation'], 'nonce': NONCE, 'commandId': 'prepare-old'})
        self.assertEqual(prepared['harness']['account']['source'], 'native')
        self.assertTrue(prepared['harness']['available'])
        self.ready(board, 'dsh')
        def receipt(result):
            result.update(account=worker, quota={'source': 'fixture', 'observedAt': utc_now(),
                'reachedType': 'insufficient_quota', 'windows': [], 'account': worker})
        self.finish_turn(board, first, disposition='attention', result_mutator=receipt)
        with board.store.db.read() as db:
            row = db.execute('SELECT * FROM attempts WHERE attempt_id=?', (attempt['attemptId'],)).fetchone()
            self.assertEqual(accounts.attempt_account(db, row), frozen)
            self.assertIsNone(latest_quota(db, 'dsh'))
            self.assertEqual(latest_quota(db, 'dsh', account=frozen)['attemptId'], attempt['attemptId'])
            self.assertEqual(read_health(db, 'dsh')['billingByProvider']['openai']['kind'], 'subscription')
        view = board.call('workflow_get', {'runId': submitted['runId']})
        self.continue_run(board, view)
        second = self.claim(board, claim_request_id='new-source')
        self.assertEqual(second['claim']['account']['source'], 'worker')
        replay = self.claim(board, claim_request_id='c1')
        self.assertEqual(replay['claim']['account'], frozen)

    def test_active_and_unknown_stop_credentials_are_retained_even_after_source_switch(self):
        board = self.board()
        self.register(board)
        self.submit(board)
        first = self.claim(board)
        self.select(board, 'worker')
        def change():
            with board.service.account_settings.credential_change('dsh', 'native', expected_credential_revision=0):
                self.fail('Must not enter credential mutation while still in use')
        with self.assertRaises(BoardError) as active:
            change()
        self.assertEqual(active.exception.code, 'ACCOUNT_IN_USE')
        self.finish_turn(board, first, shutdown=False)
        with self.assertRaises(BoardError) as uncertain:
            change()
        self.assertEqual(uncertain.exception.code, 'ACCOUNT_IN_USE')

    def test_same_source_credential_revision_rebuilds_service_native_session(self):
        board = self.board()
        configuration = {**CONFIGURATION, 'adapter': 'codex'}
        submitted = self.submit(board, **configuration)
        board.call('worker_register', {'workerId': 'w1', 'adapter': 'codex', 'capabilities': ['codex']})
        first = self.claim(board)
        def native(result):
            result['nativeSession'] = {'storageOwner': 'buddy-goal'}
        self.finish_turn(board, first, disposition='attention', session_id='old-native', result_mutator=native)
        with board.service.account_settings.credential_change('codex', 'native', expected_credential_revision=0):
            pass  # Trusted revision hook only; no credential file exists or changes.
        self.ready(board, 'codex')
        view = board.call('workflow_get', {'runId': submitted['runId']})
        self.continue_run(board, view)
        second = self.claim(board, claim_request_id='changed-credential')
        self.assertEqual(second['claim']['account']['credentialRevision'], 1)
        self.assertEqual(second['claim']['account']['revision'], 0)
        self.assertEqual(second['claim']['turn']['resumeMode'], 'reconstructed-new-session')
        self.assertEqual(second['claim']['turn']['input']['context']['resumeReason'], 'account-credentials-changed')

    def test_codex_session_without_a_frozen_account_binding_reconstructs(self):
        board = self.board()
        submitted = self.submit(board, **{**CONFIGURATION, 'adapter': 'codex'})
        board.call('worker_register', {'workerId': 'w1', 'adapter': 'codex', 'capabilities': ['codex']})
        first = self.claim(board)
        self.finish_turn(board, first, disposition='attention', session_id='unbound-native',
                         result_mutator=lambda result: result.update(nativeSession={'storageOwner': 'buddy-goal'}))
        with board.store.db.write() as db:
            row = db.execute('SELECT turn_id,input_json FROM workflow_turns').fetchone()
            legacy = json.loads(row['input_json'])
            legacy['context'].pop('account')
            db.execute('UPDATE workflow_turns SET input_json=? WHERE turn_id=?', (canonical_json(legacy), row['turn_id']))
        view = board.call('workflow_get', {'runId': submitted['runId']})
        self.continue_run(board, view)
        second = self.claim(board, claim_request_id='rebuild-unbound')
        self.assertEqual(second['claim']['turn']['resumeMode'], 'reconstructed-new-session')
        self.assertEqual(second['claim']['turn']['input']['context']['resumeReason'], 'account-binding-required')

    def test_account_permissions_refuse_links_and_backup_and_storage_protect_roots(self):
        board = self.board()
        self.select(board, 'worker')
        root = account_root(board.store.directory, 'dsh')
        credential = root / 'fixture-credential'
        credential.write_text('not a real credential')
        credential.chmod(0o644)
        accounts.secure_account_root(board.store.directory, 'dsh')
        self.assertEqual(stat.S_IMODE(credential.stat().st_mode), 0o600)
        target = self.directory / 'outside'
        target.write_text('fixture untouched')
        link = root / 'linked'
        link.symlink_to(target)
        with self.assertRaises(BoardError) as refused:
            accounts.secure_account_root(board.store.directory, 'dsh')
        self.assertEqual(refused.exception.code, 'PRIVATE_PATH_UNSAFE')
        link.unlink()
        from hey_my_buddy.blackboard.store.backup import preflight, create
        self.assertNotIn('harnesses/dsh/accounts', json.dumps(preflight(board.store.directory)))
        created = create(board.store)
        self.assertNotIn('fixture-credential', json.dumps(created))
        backup_path = Path(created['path'])
        self.assertFalse(any(path.name == 'fixture-credential' for path in backup_path.rglob('*')))
        from hey_my_buddy.blackboard.tasks import storage
        with patch.object(storage, 'process_inventory', return_value=([], [], True)):
            plan = storage.plan(board.store, {})
        protected = [entry for entry in plan['candidates'] if entry['path'] == str(root.parent)]
        self.assertTrue(protected)
        self.assertFalse(protected[0]['eligible'])

    def test_catalog_switch_during_native_read_fences_late_publication(self):
        board = self.board()
        observation = catalog_store.begin(board.evaluation)
        self.select(board, 'worker')
        self.ready(board, 'dsh')
        from support import FIXTURE_CATALOG
        result = catalog_store.record(board.evaluation, FIXTURE_CATALOG, observation['observationId'])
        self.assertEqual(result['staleAdapters'], ['dsh'])
        self.assertEqual(result['appliedAdapters'], [])

    def test_catalog_execution_uses_observation_account_before_source_switch(self):
        from hey_my_buddy.buddy.harnesses.runtime_selection import selected
        from support import FIXTURE_CATALOG
        board = self.board()
        begin = catalog_store.begin

        def switch_after_freeze(*args, **kwargs):
            observation = begin(*args, **kwargs)
            self.select(board, 'worker')
            return observation

        def discover(**kwargs):
            self.assertEqual(selected('dsh')['account']['source'], 'native')
            self.assertEqual(kwargs['database'], board.store.db)
            return FIXTURE_CATALOG

        with patch.object(catalog_store, 'begin', side_effect=switch_after_freeze), patch('hey_my_buddy.blackboard.catalog.catalog.discover', side_effect=discover):
            result = board.call('model_catalog_refresh', {'requestId': 'frozen-catalog-source'})
        self.assertEqual(result['staleAdapters'], ['dsh'])
        self.assertEqual(result['appliedAdapters'], [])

    def test_cached_catalog_cannot_be_relabelled_as_observation_source(self):
        from support import FIXTURE_CATALOG
        board = self.board()
        old = catalog_store.begin(board.evaluation)
        self.select(board, 'worker')
        worker = catalog_store.begin(board.evaluation)
        trusted = {**FIXTURE_CATALOG, 'discoveries': [{'adapter': 'dsh', 'status': 'complete', 'accountStatus': 'confirmed'}]}
        catalog_store.record(board.evaluation, trusted, worker['observationId'])
        with board.store.db.read() as db:
            cached = catalog_store.current(db, accounts=old['accounts'])
        self.assertEqual(cached.payload['providers'], [])
        self.assertEqual(cached.payload['discoveries'][0]['reason'], 'ACCOUNT_BINDING_CHANGED')

    def test_exhausted_old_credentials_do_not_exclude_new_native_candidates(self):
        board = self.board()
        from support import enable_fixture_configuration
        from hey_my_buddy.blackboard.routing.decision import DecisionCoordinator
        enable_fixture_configuration(board.store, CONFIGURATION)
        with board.store.db.write() as db:
            record_quota(db, 'dsh', {'source': 'fixture', 'provider': CONFIGURATION['provider'],
                'observedAt': utc_now(), 'reachedType': 'insufficient_quota', 'windows': []})
            self.assertEqual(DecisionCoordinator._select_candidates(db, [], coding_only=True), [])
        with board.service.account_settings.credential_change('dsh', 'native', expected_credential_revision=0):
            pass
        self.ready(board, 'dsh')
        with board.store.db.read() as db:
            self.assertEqual(DecisionCoordinator._select_candidates(db, [], coding_only=True), [],
                             'New credentials need their own trusted catalog before routing')
        from support import FIXTURE_CATALOG
        board.evaluation.record_catalog({**FIXTURE_CATALOG, 'discoveries': [
            {'adapter': 'dsh', 'status': 'complete', 'accountStatus': 'confirmed'}]})
        with board.store.db.read() as db:
            self.assertEqual(len(DecisionCoordinator._select_candidates(db, [], coding_only=True)), 1)

    def test_account_query_limits_and_late_readback_are_bound_to_frozen_credentials(self):
        board = self.board()
        self.ready(board, 'codex')
        health = board.service.harnesses
        with patch('hey_my_buddy.buddy.harnesses.codex.account_probe.read', return_value=(None, None)) as read:
            health._codex_account_read(health.get('codex'))
            health._codex_account_read(health.get('codex'))
            self.assertEqual(read.call_count, 1)
            with board.service.account_settings.credential_change('codex', 'native', expected_credential_revision=0):
                pass
            self.ready(board, 'codex')
            health._codex_account_read(health.get('codex'))
            self.assertEqual(read.call_count, 2)
        with board.store.db.write() as db:
            db.execute("DELETE FROM meta WHERE key LIKE 'account-read-at:%'")
        self.ready(board, 'codex')
        def late(*_):
            self.select(board, 'worker', adapter='codex')
            return ({'kind': 'metered', 'source': 'fixture', 'observedAt': utc_now()},
                    {'source': 'fixture', 'observedAt': utc_now(), 'provider': 'openai', 'reachedType': 'insufficient_quota', 'windows': []})
        old = health.get('codex')['account']
        with patch('hey_my_buddy.buddy.harnesses.codex.account_probe.read', side_effect=late):
            health._codex_account_read(health.get('codex'))
        current = health.get('codex')
        self.assertFalse(current['available'])
        self.assertEqual(current['billingByProvider'], {})
        self.assertIsNone(current['quota'])
        with board.store.db.read() as db:
            self.assertIsNotNone(latest_quota(db, 'codex', account=old))

    def test_account_mutation_is_forbidden_to_attempt_scoped_credential(self):
        board = self.board()
        self.register(board)
        self.submit(board)
        first = self.claim(board)
        with self.assertRaises(BoardError) as refused:
            board.call('account_set', {'adapter': 'dsh', 'source': 'worker', 'expectedRevision': 0,
                'credential': first['claim']['agentCredential']})
        self.assertEqual(refused.exception.code, 'FORBIDDEN')

    def test_native_query_reservation_keeps_credentials_until_actual_stop(self):
        board = self.board()
        with board.store.db.read() as db:
            account = accounts.selection(db, 'codex')
        with accounts.native_operation(board.store.db, account, 'fixture-query') as stop:
            with self.assertRaises(BoardError) as blocked:
                with board.service.account_settings.credential_change('codex', 'native', expected_credential_revision=0):
                    self.fail('Active readback still owns its credentials')
            self.assertEqual(blocked.exception.code, 'ACCOUNT_IN_USE')
            # Unknown shutdown intentionally retains the durable reservation.
        with self.assertRaises(BoardError) as uncertain:
            with board.service.account_settings.credential_change('codex', 'native', expected_credential_revision=0):
                self.fail('Unknown readback shutdown still owns its credentials')
        self.assertEqual(uncertain.exception.code, 'ACCOUNT_IN_USE')
        board.service.account_settings.native_operation_stopped(stop['operationId'], shutdown_confirmed=True)
        with board.service.account_settings.credential_change('codex', 'native', expected_credential_revision=0):
            pass

    def test_credential_mutation_reserves_without_sqlite_lock_and_failed_owner_stays_uncertain(self):
        board = self.board()
        owner = board.service.account_settings
        with self.assertRaises(RuntimeError):
            with owner.credential_change('codex', 'native', expected_credential_revision=0) as mutation:
                # A second ordinary service transaction can run while native I/O
                # would occur; it cannot freeze these mutating credentials.
                with board.store.db.write() as db:
                    with self.assertRaises(BoardError) as refused:
                        accounts.freeze(db, 'fixture-only-attempt', 'codex')
                    self.assertEqual(refused.exception.code, 'ACCOUNT_IN_USE')
                self.assertEqual(next(a for a in owner.all() if a['adapter'] == 'codex')['status'], 'unknown')
                raise RuntimeError('Fixture owner lost native stop evidence')
        with self.assertRaises(BoardError):
            owner.credential_change_stopped('codex', 'native', operation_id=mutation['operationId'], shutdown_confirmed=False)
        with self.assertRaises(BoardError) as uncertain:
            with owner.credential_change('codex', 'native', expected_credential_revision=0):
                self.fail('Uncertain native mutation must stay reserved')
        self.assertEqual(uncertain.exception.code, 'ACCOUNT_IN_USE')
        owner.credential_change_stopped('codex', 'native', operation_id=mutation['operationId'], shutdown_confirmed=True)
        with board.store.db.read() as db:
            self.assertEqual(accounts.selection(db, 'codex')['credentialRevision'], 1)

    def test_router_receipt_cannot_adopt_selection_after_candidate_credential_change(self):
        from blackboard.routing.test_decision import DecisionTestCase
        from blackboard.tasks.test_workflow_routing import TestWorkflowRouting
        DecisionTestCase.use_helper(self)
        board = self.board()
        # The shared fixture catalog predates per-observation account facts; this
        # test's subject is selection fencing, so seed with one confirmed reading.
        from support import FIXTURE_CATALOG
        from unittest.mock import patch as mock_patch
        trusted = {**FIXTURE_CATALOG, 'discoveries': [{'adapter': 'dsh', 'status': 'complete', 'accountStatus': 'confirmed'}]}
        with mock_patch('hey_my_buddy.blackboard.catalog.catalog.discover', return_value=trusted):
            DecisionTestCase.seed(self, board)
        # Use production decision/claim/result transactions with deterministic
        # structured output; no Router process or model is started.
        submitted = TestWorkflowRouting.routed(self, board)
        claim = TestWorkflowRouting.router_claim(self, board, submitted)
        self.select(board, 'worker')
        self.valid_decision = DecisionTestCase.valid_decision
        TestWorkflowRouting.select(self, board, claim)
        decision = board.call('selection_get', {'decisionId': submitted['routing']['decisionId']})['decision']
        self.assertEqual(decision['status'], 'stale')
        self.assertIsNone(decision['selectedProfile'])
