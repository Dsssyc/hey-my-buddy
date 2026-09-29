"""ADR-018 mode, meta migration and durable routing tests; no native model calls."""
import json
import unittest
from unittest.mock import patch

from buddy import router, schemas
from buddy.errors import BoardError
from support import BoardTestCase
from test_decision import DecisionTestCase, PROFILE_ID, SECOND_PROFILE_ID


class RoutingModesTests(BoardTestCase):
    seed = DecisionTestCase.seed

    def setUp(self):
        super().setUp()
        self.catalog_fixture()
        from fixtures import mock_readonly
        mock_readonly.install(self)
        self.enterContext(patch('buddy.adapters.dsh.DshAdapter.no_tool_structured', True, create=True))

    def configure(self, board, **settings):
        revision = board.call('console_snapshot', {})['tableRevision']
        grant = board.console_call('evaluation_write_begin', {
            'requestId': f'config-{revision}', 'kind': 'human', 'expectedRevision': revision})
        return board.console_call('user_policy_publish', {
            **{key: grant[key] for key in ('writerId', 'generation', 'writerToken')},
            'commandId': f'publish-{revision}', 'expectedRevision': revision, 'configuration': settings})

    def test_fresh_board_defaults_fast_and_patch_preserves_other_slot(self):
        board = self.board()
        fresh = board.call('console_snapshot', {})['configuration']
        self.assertEqual(fresh['defaultRoutingMode'], 'fast')
        self.assertIsNone(fresh['fastRouterProfileId'])
        self.assertIsNone(fresh['reviewRouterProfileId'])
        self.seed(board)
        self.configure(board, fastRouterProfileId=SECOND_PROFILE_ID, defaultRoutingMode='fast')
        self.configure(board, routingBudget='brief')
        settings = board.call('console_snapshot', {})['configuration']
        self.assertEqual(settings['reviewRouterProfileId'], PROFILE_ID)
        self.assertEqual(settings['fastRouterProfileId'], SECOND_PROFILE_ID)
        self.assertEqual(settings['routingBudget'], 'brief')
        self.assertEqual(board.store.db.meta('schema_version'), '14')

    def test_legacy_mapping_is_idempotent_and_retains_review_when_verified(self):
        board = self.board()
        self.seed(board)
        with board.store.db.write() as connection:
            for key in router.CONFIG_META_KEYS:
                connection.execute('DELETE FROM meta WHERE key=?', (key,))
            connection.execute('UPDATE evaluation_state SET decision_profile_id=?', (PROFILE_ID,))
            connection.execute("INSERT INTO meta VALUES('router_budget_preset','quick')")
            settings = router.initialize_configuration(connection)
            self.assertEqual(settings, router.initialize_configuration(connection))
            self.assertEqual(settings['reviewRouterProfileId'], PROFILE_ID)
            self.assertIsNone(settings['fastRouterProfileId'])
            self.assertEqual(settings['routingBudget'], 'brief')
            self.assertEqual(settings['defaultRoutingMode'], 'review')

    def test_legacy_unverified_review_moves_to_fast(self):
        board = self.board()
        self.seed(board)
        with board.store.db.write() as connection, patch('buddy.adapters.dsh.DshAdapter.read_only_structured_verified', False):
            for key in router.CONFIG_META_KEYS:
                connection.execute('DELETE FROM meta WHERE key=?', (key,))
            connection.execute('UPDATE evaluation_state SET decision_profile_id=?', (PROFILE_ID,))
            settings = router.initialize_configuration(connection)
            self.assertEqual(settings['fastRouterProfileId'], PROFILE_ID)
            self.assertIsNone(settings['reviewRouterProfileId'])
            self.assertEqual(settings['defaultRoutingMode'], 'fast')

    def test_fast_input_is_repo_free_and_fixed_at_sixty_seconds(self):
        board = self.board()
        self.seed(board)
        self.configure(board, fastRouterProfileId=PROFILE_ID)
        result = board.call('selection_request', {'requestId': 'fast', 'task': 'choose a tiny edit', 'routingMode': 'fast', 'timeoutSeconds': 500})
        view = board.call('selection_get', {'decisionId': result['decisionId'], 'includeAudit': True})['decision']
        self.assertEqual(view['routingMode'], 'fast')
        self.assertEqual(view['budget'], {'timeoutSeconds': 60})
        self.assertNotIn('executionWorkspace', view['input'])
        self.assertNotIn('evidence', view['input'])
        self.assertNotIn('file', view['input']['outputSchema']['properties']['evidence']['items']['properties']['kind']['enum'])

    def test_review_unavailable_falls_back_or_stops_when_disabled(self):
        board = self.board()
        self.seed(board)
        self.configure(board, reviewRouterProfileId=None, fastRouterProfileId=PROFILE_ID)
        for enabled in (True, False):
            result = board.call('selection_request', {'requestId': f'fallback-{enabled}', 'task': 'choose',
                               'routingMode': 'review', 'allowRoutingFallback': enabled})
            view = result['decision']
            self.assertEqual(result['status'], 'queued' if enabled else 'needs-host')
            self.assertEqual(view['requestedRoutingMode'], 'review')
            self.assertEqual(view['routingMode'], 'fast' if enabled else 'review')
            self.assertEqual(view['fallback']['code'] if enabled else view['fallback'], 'router-not-configured' if enabled else None)

    def test_claim_rechecks_review_and_charges_the_fallback_family(self):
        board = self.board()
        self.seed(board)
        self.configure(board, fastRouterProfileId=SECOND_PROFILE_ID)
        result = board.call('selection_request', {'requestId': 'later-unverified', 'task': 'choose', 'routingMode': 'review'})
        board.call('worker_register', {'workerId': 'router', 'adapter': 'decision', 'capabilities': ['decision']})
        with patch('buddy.adapters.dsh.DshAdapter.read_only_structured_verified', False):
            claim = board.client().claim('router', 'mode-claim', 'nonce-abcdefghijklmnop', task_id=result['runId'])['claim']
        self.assertIsNotNone(claim)
        self.assertEqual(claim['decisionInput']['routingMode'], 'fast')
        self.assertEqual(claim['decisionInput']['profile']['model'], 'deepseek-v4-pro')
        self.assertEqual(claim['decisionInput']['fallback']['code'], 'router-review-unverified')
        with board.store.db.read() as connection:
            stored = connection.execute('SELECT model_model FROM attempts WHERE attempt_id=?', (claim['attempt']['attemptId'],)).fetchone()
        self.assertEqual(stored[0], 'deepseek-v4-pro')

    def test_replay_does_not_reresolve_default_or_budget(self):
        board = self.board()
        self.seed(board)
        params = {'requestId': 'frozen-mode', 'task': 'choose'}
        original = board.call('selection_request', params)
        self.configure(board, defaultRoutingMode='fast', fastRouterProfileId=PROFILE_ID)
        replay = board.call('selection_request', params)
        self.assertTrue(replay['duplicate'])
        self.assertEqual(original['decisionId'], replay['decisionId'])
        self.assertEqual(replay['decision']['routingMode'], 'review')

    def test_preflight_version_change_requeues_only_after_no_model_and_stopped_receipt(self):
        board = self.board()
        self.seed(board)
        self.configure(board, fastRouterProfileId=SECOND_PROFILE_ID)
        result = board.call('selection_request', {'requestId': 'preflight', 'task': 'choose', 'routingMode': 'review'})
        board.call('worker_register', {'workerId': 'router', 'adapter': 'decision', 'capabilities': ['decision']})
        nonce = 'nonce-abcdefghijklmnop'
        first = board.client().claim('router', 'preflight-claim', nonce, task_id=result['runId'])['claim']
        response = board.call('worker_result', {
            'workerId': 'router', 'attemptId': first['attempt']['attemptId'], 'generation': first['attempt']['generation'],
            'nonce': nonce, 'status': 'failed', 'shutdownConfirmed': True,
            'result': {'status': 'error', 'code': 'router-review-unavailable', 'modelStarted': False,
                       'reasonCode': 'router-review-unverified', 'routingMode': 'review', 'requestedRoutingMode': 'review'}})
        self.assertEqual(response['taskState'], 'queued')
        second = board.client().claim('router', 'fallback-claim', nonce, task_id=result['runId'])['claim']
        self.assertEqual(second['attempt']['generation'], first['attempt']['generation'] + 1)
        self.assertEqual(second['decisionInput']['routingMode'], 'fast')
        self.assertEqual(second['decisionInput']['profile']['model'], 'deepseek-v4-pro')
        self.assertEqual(first['decisionInput']['profiles'], second['decisionInput']['profiles'])

    def test_fast_receipt_requires_zero_tool_evidence_at_publication(self):
        board = self.board()
        self.seed(board)
        self.configure(board, fastRouterProfileId=PROFILE_ID)
        nonce = 'nonce-abcdefghijklmnop'
        for index, (proof, count, expected) in enumerate(((True, 0, 'completed'), (None, 0, 'needs-host'), (True, 1, 'needs-host'), (True, False, 'needs-host'))):
            result = board.call('selection_request', {'requestId': f'proof-{index}', 'task': 'choose', 'routingMode': 'fast'})
            worker = f'router-{index}'
            board.call('worker_register', {'workerId': worker, 'adapter': 'decision', 'capabilities': ['decision']})
            claim = board.client().claim(worker, f'proof-claim-{index}', nonce, task_id=result['runId'])['claim']
            board.call('worker_result', {'workerId': worker, 'attemptId': claim['attempt']['attemptId'],
                'generation': claim['attempt']['generation'], 'nonce': nonce, 'status': 'ok', 'shutdownConfirmed': True,
                'result': {'status': 'ok', 'operation': 'select', 'tableRevision': claim['decisionInput']['tableRevision'],
                    'zeroToolVerified': proof, 'usage': {'toolCalls': count},
                    'decision': {'profileId': PROFILE_ID, 'reason': 'card match', 'evidence': []}}})
            decision = board.call('selection_get', {'decisionId': result['decisionId']})['decision']
            self.assertEqual(decision['status'], expected)

    def test_upgrade_changes_only_routing_meta_and_keeps_schema(self):
        from buddy.upgrade import idle_snapshot, migrate_routing_configuration
        board = self.board()
        self.seed(board)
        with board.store.db.write() as connection:
            for key in router.CONFIG_META_KEYS:
                connection.execute('DELETE FROM meta WHERE key=?', (key,))
            connection.execute('UPDATE evaluation_state SET decision_profile_id=?', (PROFILE_ID,))
            connection.execute("INSERT INTO meta VALUES('router_budget_preset','quick')")
            connection.execute("INSERT INTO meta VALUES('unrelated-test-setting','keep')")
        before = idle_snapshot(board.directory)
        settings, after = migrate_routing_configuration(board.directory, before)
        self.assertEqual(settings['reviewRouterProfileId'], PROFILE_ID)
        self.assertEqual(settings['routingBudget'], 'brief')
        self.assertEqual(after['schema'], 14)
        self.assertEqual(board.store.db.meta('unrelated-test-setting'), 'keep')
        self.assertEqual(set(before['tables']), set(after['tables']))
        self.assertEqual({k: v for k, v in before['fingerprints'].items() if k != 'meta'},
                         {k: v for k, v in after['fingerprints'].items() if k != 'meta'})


class FastAnswerTests(unittest.TestCase):
    def test_file_evidence_is_rejected_even_if_schema_was_bypassed(self):
        with self.assertRaises(BoardError) as caught:
            router.validate_answer({'profileId': 'legal', 'reason': 'x', 'evidence': [{'kind': 'file', 'ref': 'src/a.py'}]}, ['legal'], 'fast')
        self.assertEqual(caught.exception.code, 'router-evidence-out-of-bounds')

    def test_fast_prompt_drops_repository_and_history_payloads(self):
        prompt = router.render_prompt({'routingMode': 'fast', 'task': 'choose', 'evidence': ['secret-evidence'],
                                       'executionWorkspace': {'inputTree': 'secret-tree'}, 'profiles': []})
        self.assertNotIn('secret-evidence', prompt)
        self.assertNotIn('secret-tree', prompt)

    def test_mode_and_fallback_are_validated_and_fingerprinted(self):
        for payload in ({'routingMode': 'quick'}, {'routingMode': None}, {'allowRoutingFallback': 'false'}):
            with self.assertRaises(BoardError):
                schemas.normalize_workflow_spec({'requestId': 'test', 'task': 'x', 'cwd': '.', **payload})
        with self.assertRaises(BoardError):
            router.budget('quick')
