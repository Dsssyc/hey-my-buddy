"""Single Router mode, pure legacy conversion and durable routing tests; no native model calls."""
import unittest
from unittest.mock import patch

from hey_my_buddy.blackboard.routing import router
from hey_my_buddy.protocol import schemas
from hey_my_buddy.errors import BoardError
from blackboard.routing.fixtures.router_tool_receipt import claim_tool_receipt
from support import BoardTestCase, FakeClock
from blackboard.routing.test_decision import DecisionTestCase, PROFILE_ID, SECOND_PROFILE_ID


class RoutingModesTests(BoardTestCase):
    seed = DecisionTestCase.seed

    def setUp(self):
        super().setUp()
        self.catalog_fixture()
        from protocol.fixtures import mock_readonly
        mock_readonly.install(self)
        self.enterContext(patch('hey_my_buddy.buddy.harnesses.dsh.adapter.DshAdapter.no_tool_structured', True, create=True))

    def configure(self, board, **settings):
        revision = board.call('console_snapshot', {})['tableRevision']
        return board.console_call('user_policy_publish', {
            'commandId': f'publish-{revision}', 'expectedRevision': revision, 'configuration': settings})

    def test_fresh_board_defaults_fast_and_patch_preserves_omitted_settings(self):
        board = self.board()
        fresh = board.call('console_snapshot', {})['configuration']
        self.assertEqual(fresh['defaultRoutingMode'], 'fast')
        self.assertEqual(fresh['routerProfileIds'], [])
        self.seed(board)
        self.configure(board, routerProfileIds=[SECOND_PROFILE_ID], defaultRoutingMode='fast')
        self.configure(board, routingBudget='brief')
        settings = board.call('console_snapshot', {})['configuration']
        self.assertEqual(settings['routerProfileIds'], [SECOND_PROFILE_ID])
        self.assertEqual(settings['defaultRoutingMode'], 'fast')
        self.assertEqual(settings['routingBudget'], 'brief')
        from hey_my_buddy.blackboard.store.db import SCHEMA_VERSION
        self.assertEqual(board.store.db.meta('schema_version'), str(SCHEMA_VERSION))



    def test_fast_input_is_repo_free_and_fixed_at_sixty_seconds(self):
        board = self.board()
        self.seed(board)
        # The mode comes from the Router setting; the submission no longer carries it.
        self.configure(board, routerProfileIds=[PROFILE_ID], defaultRoutingMode='fast')
        result = board.call('selection_request', {'requestId': 'fast', 'task': 'choose a tiny edit', 'timeoutSeconds': 500})
        view = board.call('selection_get', {'decisionId': result['decisionId'], 'includeAudit': True})['decision']
        self.assertEqual(view['routingMode'], 'fast')
        self.assertEqual(view['budget'], {'timeoutSeconds': 60})
        self.assertNotIn('executionWorkspace', view['input'])
        self.assertNotIn('evidence', view['input'])
        self.assertNotIn('file', view['input']['outputSchema']['properties']['evidence']['items']['properties']['kind']['enum'])

    def test_review_unconfigured_stops_host_without_using_available_fast_buddy(self):
        board = self.board()
        self.seed(board)
        self.configure(board, routerProfileIds=[], defaultRoutingMode='review')
        result = board.call('selection_request', {'requestId': 'no-fallback', 'task': 'choose'})
        self.assertEqual(result['status'], 'needs-host')
        self.assertIsNone(result['runId'])
        self.assertEqual(result['decision']['routingMode'], 'review')
        self.assertEqual(result['decision']['routerProblem']['code'], 'router-not-configured')
        self.assertNotIn('requestedRoutingMode', result['decision'])
        self.assertNotIn('fallback', result['decision'])
        with board.store.db.read() as connection:
            self.assertEqual(connection.execute('SELECT COUNT(*) FROM attempts').fetchone()[0], 0)

    def test_current_router_has_no_internal_mode_fallback(self):
        board = self.board()
        self.seed(board)
        self.configure(board, routerProfileIds=[])
        with board.store.db.read() as connection:
            resolution = router.current_router(connection)
            with self.assertRaises(TypeError):
                router.current_router(connection, 'review', False)
        self.assertIsNone(resolution.profile)
        self.assertEqual(resolution.facts['routingMode'], 'review')
        self.assertNotIn('fallback', resolution.facts)
        self.assertEqual(resolution.problem['code'], 'router-not-configured')
        self.assertTrue(resolution.problem['reason'].startswith('Router 不可用：'))

    def test_claim_rechecks_review_without_charging_another_family(self):
        board = self.board()
        self.seed(board)
        result = board.call('selection_request', {'requestId': 'later-ineligible', 'task': 'choose'})
        board.call('worker_register', {'workerId': 'router', 'adapter': 'decision', 'capabilities': ['decision']})
        with patch('hey_my_buddy.buddy.harnesses.dsh.adapter.DshAdapter.local_read_only_check', return_value={
                'eligible': False, 'reasonCode': 'readonly-unavailable', 'reason': 'changed local resource',
                'systemSandbox': False, 'sameAttemptContinuation': False}):
            claim = board.client().claim('router', 'mode-claim', 'nonce-abcdefghijklmnop', task_id=result['runId'])['claim']
        self.assertIsNone(claim)
        decision = board.call('selection_get', {'decisionId': result['decisionId'], 'includeAudit': True})['decision']
        self.assertEqual(decision['status'], 'needs-host')
        self.assertEqual(decision['output']['code'], 'router-unavailable')
        self.assertEqual(decision['routingMode'], 'review')
        with board.store.db.read() as connection:
            import json
            fact = json.loads(connection.execute("SELECT payload_json FROM events WHERE kind='router.no_answer'").fetchone()[0])
            self.assertEqual(fact['code'], 'router-review-unsupported')
            self.assertEqual(connection.execute('SELECT COUNT(*) FROM attempts').fetchone()[0], 0)
            self.assertEqual(connection.execute('SELECT COUNT(*) FROM evaluation_readers').fetchone()[0], 0)

    def test_replay_does_not_reresolve_default_or_budget(self):
        board = self.board()
        self.seed(board)
        params = {'requestId': 'frozen-mode', 'task': 'choose'}
        original = board.call('selection_request', params)
        self.configure(board, defaultRoutingMode='fast', routerProfileIds=[PROFILE_ID])
        replay = board.call('selection_request', params)
        self.assertTrue(replay['duplicate'])
        self.assertEqual(original['decisionId'], replay['decisionId'])
        self.assertEqual(replay['decision']['routingMode'], 'review')

    def test_preflight_policy_failure_stops_host_after_stop_without_second_attempt(self):
        board = self.board()
        self.seed(board)
        result = board.call('selection_request', {'requestId': 'preflight', 'task': 'choose'})
        board.call('worker_register', {'workerId': 'router', 'adapter': 'decision', 'capabilities': ['decision']})
        nonce = 'nonce-abcdefghijklmnop'
        first = board.client().claim('router', 'preflight-claim', nonce, task_id=result['runId'])['claim']
        response = board.call('worker_result', {
            'workerId': 'router', 'attemptId': first['attempt']['attemptId'], 'generation': first['attempt']['generation'],
            'nonce': nonce, 'status': 'failed', 'shutdownConfirmed': True,
            'result': {'status': 'error', 'code': 'router-review-unavailable', 'modelStarted': False}})
        self.assertEqual(response['taskState'], 'failed')
        self.assertIsNone(board.client().claim('router', 'second-claim', nonce, task_id=result['runId'])['claim'])
        decision = board.call('selection_get', {'decisionId': result['decisionId'], 'includeAudit': True})['decision']
        self.assertEqual(decision['status'], 'needs-host')
        self.assertEqual(decision['routingMode'], 'review')
        self.assertEqual(decision['input'], first['decisionInput'])
        with board.store.db.read() as connection:
            self.assertEqual(connection.execute('SELECT COUNT(*) FROM attempts').fetchone()[0], 1)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM events WHERE kind='decision.fallback'").fetchone()[0], 0)

    def test_fast_receipt_requires_zero_tool_evidence_at_publication(self):
        clock = FakeClock()
        board = self.board(clock=clock)
        self.seed(board)
        self.configure(board, routerProfileIds=[PROFILE_ID], defaultRoutingMode='fast')
        nonce = 'nonce-abcdefghijklmnop'
        for index, (proof, count, expected, code) in enumerate((
                (True, 0, 'completed', None),
                (None, 0, 'needs-host', 'router-tool-evidence-unverified'),
                (True, 1, 'needs-host', 'router-tools-forbidden'),
                (True, False, 'needs-host', 'router-tool-evidence-unverified'))):
            clock.advance(601)
            result = board.call('selection_request', {'requestId': f'proof-{index}', 'task': 'choose'})
            worker = f'router-{index}'
            board.call('worker_register', {'workerId': worker, 'adapter': 'decision', 'capabilities': ['decision']})
            claim = board.client().claim(worker, f'proof-claim-{index}', nonce, task_id=result['runId'])['claim']
            facts = claim_tool_receipt(claim, count if type(count) is int else 0)
            if proof is None:
                facts.pop('toolEvidence')
            board.call('worker_result', {'workerId': worker, 'attemptId': claim['attempt']['attemptId'],
                'generation': claim['attempt']['generation'], 'nonce': nonce, 'status': 'ok', 'shutdownConfirmed': True,
                'result': {**facts, 'status': 'ok', 'operation': 'select', 'tableRevision': claim['decisionInput']['tableRevision'],
                    'stopEvidence': {'shutdownConfirmed': True, 'native': {'shutdownConfirmed': True}},
                    'zeroToolVerified': proof, 'usage': {'elapsedMs': 100, 'toolCalls': count},
                    'decision': {'profileId': PROFILE_ID, 'reason': 'candidate match', 'evidence': []}}})
            decision = board.call('selection_get', {'decisionId': result['decisionId']})['decision']
            self.assertEqual(decision['status'], expected)
            if expected == 'needs-host':
                self.assertEqual(decision['error'], code)


    def test_file_evidence_is_rejected_even_if_schema_was_bypassed(self):
        with self.assertRaises(BoardError) as caught:
            router.validate_answer({'profileId': 'legal', 'reason': 'x', 'evidence': [{'kind': 'file', 'ref': 'src/a.py'}]}, ['legal'], 'fast')
        self.assertEqual(caught.exception.code, 'router-evidence-out-of-bounds')

    def test_fast_prompt_drops_repository_and_history_payloads(self):
        prompt = router.render_prompt({'routingMode': 'fast', 'task': 'choose', 'evidence': ['secret-evidence'],
                                       'executionWorkspace': {'inputTree': 'secret-tree'}, 'profiles': []})
        self.assertNotIn('secret-evidence', prompt)
        self.assertNotIn('secret-tree', prompt)

    def test_mode_and_fallback_are_retired_submission_input(self):
        for payload in ({'routingMode': 'quick'}, {'routingMode': 'fast'}, {'routingMode': None},
                        {'allowRoutingFallback': 'false'}, {'allowRoutingFallback': True}):
            with self.assertRaises(BoardError) as raised:
                schemas.normalize_workflow_spec({'requestId': 'test', 'task': 'x', 'cwd': '.', **payload})
            self.assertEqual(raised.exception.code, 'INVALID_ARGUMENT')
            self.assertIn('retired Host routing input', raised.exception.message)
        with self.assertRaises(BoardError):
            router.budget('quick')
