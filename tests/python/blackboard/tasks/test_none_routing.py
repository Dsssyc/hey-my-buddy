"""A routed Micro task without a checkout keeps its access and has no manifest."""
import json

from blackboard.routing.fixtures.router_tool_receipt import claim_tool_receipt
from blackboard.routing.test_decision import DecisionTestCase, BUDDY_ID
from blackboard.tasks.test_workflow import BUDDY, NONCE, WorkflowTestCase


class NoneRoutingTests(WorkflowTestCase):
    seed = DecisionTestCase.seed
    valid_decision = DecisionTestCase.valid_decision

    def setUp(self):
        super().setUp()
        self.catalog_fixture()
        DecisionTestCase.use_helper(self)

    def routed(self, board, request_id, access='read'):
        return self.submit(board, request_id=request_id, cwd=str(self.workdir(request_id)),
                           buddy='route', kind='none', access=access)

    def router_claim(self, board, run, claim_id):
        claimed = self.claim(board, 'router', run_id=run['routing']['taskId'], claim_request_id=claim_id)
        self.assertIsNotNone(claimed['claim'], (claimed, run['routing']))
        self.assertIsNone(claimed['claim']['decisionInput']['executionWorkspace'])
        return claimed

    def select(self, board, claimed):
        owned = claimed['claim']
        decision_input = owned['decisionInput']
        response = board.call('execution_result', {
            'attemptId': owned['attempt']['attemptId'], 'generation': owned['attempt']['generation'],
            'nonce': NONCE, 'status': 'ok', 'shutdownConfirmed': True,
            'result': {**claim_tool_receipt(owned, state_dir=board.directory),
                       'status': 'ok', 'operation': 'select',
                       'stopEvidence': {'shutdownConfirmed': True, 'native': {'shutdownConfirmed': True}},
                       'usage': {'elapsedMs': 100, 'toolCalls': 0},
                       'inputVerification': {'unchanged': True, 'snapshotSha256': 'fixture-digest',
                                             'manifestSha256': None},
                       'decision': self.valid_decision(decision_input, BUDDY_ID)},
        })
        self.assertTrue(response['committed'], response)

    def assert_routed(self, board, run, access, claim_id):
        claimed = self.router_claim(board, run, claim_id)
        document = claimed['claim']['decisionInput']
        self.assertIn(f'permission:{access}', document['requiredCapabilities'])
        with board.store.db.read() as db:
            request = json.loads(db.execute(
                'SELECT requested_json FROM decision_requests WHERE decision_id=?',
                (run['routing']['decisionId'],)).fetchone()[0])
        self.assertIsNone(request['executionWorkspace'])
        self.assertIn(f'permission:{access}', request['requiredCapabilities'])
        self.select(board, claimed)
        selected = board.call('workflow_get', {'runId': run['runId']})
        self.assertEqual(selected['routing']['status'], 'completed', selected['routing'])
        self.assertEqual(selected['buddy'], BUDDY)
        self.assertEqual(selected['checkout'], 'none')
        return selected

    def test_direct_submit_routes_none_read(self):
        board = self.board()
        self.seed(board)
        run = self.routed(board, 'direct-none')
        selected = self.assert_routed(board, run, 'read', 'direct-router')
        claim = self.claim(board, run_id=selected['runId'], claim_request_id='direct-worker')
        self.assertEqual(claim['claim']['turn']['input']['executionWorkspace']['kind'], 'none')

    def test_helper_admission_routes_none_read(self):
        board = self.board()
        self.seed(board)
        parent = self.submit(board, request_id='helper-parent', kind='none', access='read')
        self.finish_turn(board, self.claim(board, run_id=parent['runId']), seal=False,
                         disposition='attention')
        view = board.call('workflow_get', {'runId': parent['runId']})
        approved = self.decide(board, view, view['activeRequest']['requestId'], helpers=[{
            'title': 'Routed answer', 'task': 'Check the answer', 'cwd': str(self.workdir('helper-none')),
            'buddy': 'route', 'checkout': 'none', 'access': 'read',
        }])
        child_id = approved['children'][0]['taskId']
        helper = board.call('workflow_get', {'runId': child_id})
        self.assert_routed(board, helper, 'read', 'helper-router')
        claim = self.claim(board, run_id=child_id, claim_request_id='helper-worker')
        self.assertEqual(claim['claim']['turn']['input']['executionWorkspace']['kind'], 'none')

    def test_queued_continuation_can_reroute_none(self):
        board = self.board()
        run = self.routed(board, 'queued-none')
        self.assertEqual(run['routing']['status'], 'needs-host')
        self.seed(board)
        continued = self.continue_run(board, run, buddy='route', note='Use current Router')
        self.assertEqual(continued['routing']['status'], 'queued', continued['routing'])
        self.assertIsNotNone(continued['routing']['decisionId'])
        self.assert_routed(board, continued, 'read', 'queued-router')

    def test_reroute_after_turn_preserves_none_access(self):
        board = self.board()
        self.seed(board)
        run = self.routed(board, 'turn-none')
        self.assert_routed(board, run, 'read', 'initial-router')
        self.finish_turn(board, self.claim(board, run_id=run['runId'],
                                           claim_request_id='first-worker'), seal=False,
                         disposition='attention')
        waiting = board.call('workflow_get', {'runId': run['runId']})
        continued = self.continue_run(board, waiting, buddy='route', note='Select for next turn')
        self.assertEqual(continued['routing']['status'], 'queued')
        self.assertIsNotNone(continued['routing']['decisionId'])
        self.assert_routed(board, continued, 'read', 'second-router')

    def test_pre_model_rejection_automatically_reroutes_none_without_manifest(self):
        board = self.board()
        self.seed(board)
        run = self.routed(board, 'rejected-none')
        self.assert_routed(board, run, 'read', 'rejected-initial-router')
        claimed = self.claim(board, run_id=run['runId'], claim_request_id='rejected-worker')['claim']
        attempt = claimed['attempt']
        settled = board.call('execution_result', {
            'attemptId': attempt['attemptId'], 'generation': attempt['generation'], 'nonce': NONCE,
            'status': 'failed', 'shutdownConfirmed': True, 'exitCode': 1,
            'result': {'code': 'harness-error', 'providerRejection': {
                'code': 'overloaded', 'nativeCode': 'serverOverloaded', 'source': 'synthetic/turn-error',
                'observedAt': board.store.now(), 'resetsAt': None, 'rejectionPhase': 'before-model'}},
        })
        self.assertTrue(settled['workflow']['autoContinued'])
        view = board.call('workflow_get', {'runId': run['runId']})
        self.assertNotEqual(view['routing']['decisionId'], run['routing']['decisionId'])
        self.assertEqual(view['turns'][0]['state'], 'rejected')
        with board.store.db.read() as db:
            requested = json.loads(db.execute('SELECT requested_json FROM decision_requests WHERE decision_id=?',
                                             (view['routing']['decisionId'],)).fetchone()[0])
        self.assertIsNone(requested['executionWorkspace'])
        self.assertIn('permission:read', requested['requiredCapabilities'])
        self.assertEqual(view['checkout'], 'none')
