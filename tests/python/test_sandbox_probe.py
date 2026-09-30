"""Native controller challenges and historical replay on disposable fixtures."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

from buddy.sandbox_probe import CAP, FORMAT, PROBES, results, run
from buddy.review_replay import replay
from buddy.adapters.codex_protocol import CodexProtocolError
import test_review_probe as _probe_fixtures

FIXTURES = Path(__file__).parent / 'fixtures/review-evidence'


class FixedChallengeTests(unittest.TestCase):
    def connection(self, *, change=None):
        connection = Mock()
        connection.responses = {}
        connection.strict_responses = False
        connection.next_id = 10
        seen = []
        def call(method, params):
            index = len(seen)
            self.assertTrue(connection.strict_responses)
            self.assertEqual(method, 'command/exec')
            self.assertEqual(params['permissionProfile'], 'buddy-router')
            self.assertNotIn('sandboxPolicy', params)
            seen.append(params)
            connection.next_id += 1
            response = {'exitCode': 0 if index == 0 else 1,
                        'stdout': 'marker\n' if index == 0 else '',
                        'stderr': '' if index == 0 else 'Operation not permitted'}
            return change(index, response) if change else response
        connection.call.side_effect = call
        return connection, seen

    def test_fixed_operation_ids_and_correlated_native_results(self):
        connection, seen = self.connection()
        value = run(connection, frozen=Path('/private-fixture/frozen'), sentinel=Path('/private-fixture/outside.txt'),
                    url='http://127.0.0.1:12345/')
        parsed, complete = results(value)
        self.assertTrue(complete)
        self.assertEqual(set(parsed), set(PROBES))
        self.assertEqual([item['requestId'] for item in value['operations']], list(range(10,15)))
        self.assertEqual(len(seen), 5)
        self.assertIs(connection.strict_responses, False)
        self.assertEqual(parsed['internal-read'], (0, 'marker\n', ''))

    def test_truncation_or_missing_exit_fails_without_more_calls(self):
        for update in ({'stdout': 'x' * CAP}, {'exitCode': None}, {'stderr': None}):
            with self.subTest(update=list(update)):
                connection, seen = self.connection(change=lambda _i,r: {**r,**update})
                with self.assertRaises(CodexProtocolError):
                    run(connection, frozen=Path('/fixture/frozen'), sentinel=Path('/fixture/outside'), url='http://127.0.0.1:1/')
                self.assertEqual(len(seen), 1)
                self.assertIs(connection.strict_responses, False)

    def test_pending_unrelated_reply_is_refused_before_execution(self):
        connection, seen = self.connection()
        connection.responses = {777: {'result': {}}}
        with self.assertRaises(CodexProtocolError):
            run(connection, frozen=Path('/fixture/frozen'), sentinel=Path('/fixture/outside'), url='http://127.0.0.1:1/')
        self.assertEqual(seen, [])

    def test_missing_duplicate_extra_and_wrong_profile_native_results_fail(self):
        connection,_ = self.connection()
        original=run(connection,frozen=Path('/fixture/frozen'),sentinel=Path('/fixture/outside'),url='http://127.0.0.1:1/')
        variants=[]
        v=copy.deepcopy(original);v['operations'].pop();variants.append(v)
        v=copy.deepcopy(original);v['operations'].append(v['operations'][0]);variants.append(v)
        v=copy.deepcopy(original);v['operations'][1]['requestId']=v['operations'][0]['requestId'];variants.append(v)
        v=copy.deepcopy(original);v['operations'][1]['operation']='internal-read';variants.append(v)
        v=copy.deepcopy(original);v['operations'][1]['permissionProfile']='workspace-write';variants.append(v)
        for variant in variants:
            self.assertFalse(results(variant)[1])


class ModelWritingTests(unittest.TestCase):
    def setUp(self):
        # Reuse fixture setup without exporting another TestCase to discovery.
        self.fixture = _probe_fixtures.ReviewProbeTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)

    def test_different_model_writing_cannot_change_native_sandbox_verdict(self):
        forms = ('text(await tools.exec_command({workdir:"different",cmd:"cat ./marker.txt"}));',
                 'const r = await tools.exec_command({cmd:"/bin/cat marker.txt"}); text(r.output);',
                 'let outcome = await tools.exec_command({cmd:"cat marker.txt", yield_time_ms:10000}); text(outcome);')
        original = copy.deepcopy(self.fixture.payload)
        for style in forms:
            self.fixture.payload = copy.deepcopy(original)
            for event in self.fixture.payload['nativeRawToolEvents']:
                item = event['params']['item']
                if item['type']=='function_call':
                    item['arguments']=json.dumps({'code': style})
            self.assertTrue(all(self.fixture.assess().values()))

    def test_four_model_denials_cannot_replace_missing_fixed_native_probes(self):
        self.fixture.payload.pop('nativeSandboxProbes')
        self.assertTrue(self.fixture.assess()['forbiddenTools'])
        self.assertFalse(self.fixture.assess()['boundaryDenials'])
        self.assertFalse(self.fixture.assess()['internalRead'])

    def test_native_boundary_success_cannot_be_overruled_by_model_denied_claim(self):
        self.fixture.payload['nativeSandboxProbes']['operations'][4].update(exitCode=0,stderr='')
        self.assertFalse(self.fixture.assess()['boundaryDenials'])
        self.fixture.payload['nativeSandboxProbes']['operations'][4].update(exitCode=7,stderr='Connection refused')
        self.assertFalse(self.fixture.assess()['boundaryDenials'])


class HistoricalReplayTests(unittest.TestCase):
    def test_sol_and_luna_have_the_same_native_observation_and_same_missing_proof(self):
        sol=json.loads((FIXTURES/'sol-v1.json').read_text())
        supplement=json.loads((FIXTURES/'sol-replay.json').read_text())
        luna=json.loads((FIXTURES/'luna-v1.json').read_text())
        a,b=replay(sol,supplemental=supplement),replay(luna)
        self.assertEqual(a,b)
        self.assertTrue(a['nativePolicy'])
        self.assertTrue(a['toolStreamCorrelated'])
        self.assertTrue(a['forbiddenTools'])
        self.assertTrue(a['internalReadObserved'])
        self.assertEqual(a['nativeDenialCount'],4)
        self.assertFalse(a['certifiable'])
        self.assertEqual(a['reasonCode'],'HISTORICAL_PROBE_BINDING_MISSING')

    def test_historical_changed_reply_extra_tools_approval_and_truncation_fail(self):
        original=json.loads((FIXTURES/'luna-v1.json').read_text())
        variants=[]
        v=copy.deepcopy(original);v['events'][1]['call']='unrelated';variants.append(v)
        v=copy.deepcopy(original);v['events'][0]['tool']='web_search';variants.append(v)
        v=copy.deepcopy(original);v['events'].append(v['events'][0]);variants.append(v)
        v=copy.deepcopy(original);v['eventCounts']['nativeDeniedRequests']=1;variants.append(v)
        v=copy.deepcopy(original);v['truncated']=True;variants.append(v)
        for value in variants:
            self.assertFalse(replay(value)['forbiddenTools'])

    def test_supplemental_historical_events_cannot_be_rebound_to_another_run(self):
        sol=json.loads((FIXTURES/'sol-v1.json').read_text())
        supplement=json.loads((FIXTURES/'sol-replay.json').read_text())
        sol['attempt']['taskId']='other'
        with self.assertRaises(ValueError): replay(sol,supplemental=supplement)
