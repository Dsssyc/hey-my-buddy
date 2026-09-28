"""Router answer boundary checks without any native model calls."""
import unittest

from buddy import router
from buddy.errors import BoardError


class RouterContractTests(unittest.TestCase):
    def test_unknown_evidence_and_alternative_are_advisory(self):
        answer = {"profileId": "legal-alternative", "reason": "Better fit after reading the code",
                  "evidence": [{"kind": "card", "ref": "not-in-packet"},
                               {"kind": "file", "ref": "src/worker.py"}]}
        self.assertEqual(router.validate_answer(answer, ["preferred", "legal-alternative"]), answer)

    def test_candidate_escape_has_noncorrectable_code(self):
        with self.assertRaises(BoardError) as caught:
            router.validate_answer({"profileId": "foreign", "reason": "x", "evidence": []}, ["legal"])
        self.assertEqual(caught.exception.code, "router-out-of-bounds")

    def test_file_references_cannot_escape_or_embed_lines(self):
        for ref in ("../secret", "/tmp/other-run", "src/../../secret", "C:\\secret", "file\ncontents"):
            with self.subTest(ref=ref), self.assertRaises(BoardError):
                router.validate_answer({"profileId": "legal", "reason": "x",
                                        "evidence": [{"kind": "file", "ref": ref}]}, ["legal"])

    def test_abstention_and_malformed_results(self):
        self.assertIsNone(router.validate_answer('{"profileId":null,"reason":"Insufficient evidence","evidence":[]}', [])['profileId'])
        for value in ("invalid", {}, {"profileId": None, "reason": " ", "evidence": []},
                      {"profileId": None, "reason": "x", "evidence": [], "request": {}}):
            with self.subTest(value=value), self.assertRaises(BoardError):
                router.validate_answer(value, [])

    def test_prompt_prefix_stays_stable_and_excludes_display_metadata(self):
        doc = {"profiles": [{"profileId": "legal"}], "task": "first", "title": "display-only"}
        first = router.render_prompt(doc)
        second = router.render_prompt({**doc, "task": "second", "objective": "private display label"})
        self.assertEqual(first.split('\n\n')[:2], second.split('\n\n')[:2])
        self.assertNotIn("display-only", first)
        self.assertNotIn("private display label", second)


from unittest.mock import patch
from test_decision import DecisionTestCase, PROFILE_ID, SECOND_PROFILE_ID
from test_workflow import WorkflowTestCase, NONCE


class RouterPublicationTests(WorkflowTestCase):
    seed = DecisionTestCase.seed

    def setUp(self):
        super().setUp()
        self.catalog_fixture()
        self.enterContext(patch('buddy.adapters.dsh.DshAdapter.read_only_structured', True))
        self.enterContext(patch('buddy.adapters.dsh.DshAdapter.read_only_structured_verified', True))
        self.enterContext(patch('buddy.decision.DecisionCoordinator._adapter_available', return_value=(True, None)))

    def settle(self, profile, *, code=None, preferences=None):
        board = self.board()
        self.seed(board, preferences=preferences)
        request = board.call('selection_request', {'requestId': 'route', 'task': 'choose'})
        board.call('worker_register', {'workerId': 'router', 'adapter': 'decision', 'capabilities': ['decision']})
        claim = self.claim(board, 'router', run_id=request['runId'])['claim']
        result = {'status': 'error' if code else 'ok', 'operation': 'select',
                  'tableRevision': claim['decisionInput']['tableRevision'],
                  'decision': {'profileId': profile, 'reason': 'Read the frozen input',
                               'evidence': [{'kind': 'card', 'ref': 'not-supplied'}]}}
        if code:
            result['code'] = code
        board.call('worker_result', {'workerId': 'router', 'attemptId': claim['attempt']['attemptId'],
                                    'generation': claim['attempt']['generation'], 'nonce': NONCE,
                                    'status': 'failed' if code else 'ok', 'shutdownConfirmed': True, 'result': result})
        return board, board.call('selection_get', {'decisionId': request['decisionId']})['decision']

    def test_publication_accepts_alternative_without_card_veto(self):
        board, decision = self.settle(SECOND_PROFILE_ID, preferences=[{'profileId': PROFILE_ID, 'mode': 'prefer'}])
        self.assertEqual(decision['status'], 'completed')
        self.assertEqual(decision['policyCheck']['userPreference'], 'alternative')
        self.assertEqual(decision['evidence'], [{'kind': 'card', 'ref': 'not-supplied'}])

    def test_boundary_and_budget_counters_are_separate(self):
        for code, counter in [('router-budget-exhausted', 'budgetExhaustedCount'),
                              ('router-input-changed', 'inputChangedCount')]:
            with self.subTest(code=code):
                board, decision = self.settle(None, code=code)
                self.assertEqual(decision['status'], 'needs-host')
                health = board.store.decisions.health_summary()
                self.assertEqual(health[counter], 1)
                self.assertEqual(health['failureCount'], 0)
                board.close()
                self._stack.remove(board)

    def test_budget_publication_preserves_model_and_schema(self):
        from buddy.db import SCHEMA_VERSION
        board = self.board()
        self.seed(board)
        snapshot = board.call('console_snapshot', {})
        grant = board.console_call('evaluation_write_begin', {
            'requestId': 'budget-change', 'kind': 'human', 'expectedRevision': snapshot['tableRevision']})
        board.console_call('user_policy_publish', {
            'commandId': 'budget-change', 'writerId': grant['writerId'], 'generation': grant['generation'],
            'writerToken': grant['writerToken'], 'expectedRevision': grant['tableRevision'],
            'configuration': {'routingBudget': 'deep'}})
        configured = board.call('console_snapshot', {})['configuration']
        self.assertEqual(configured['routingBudget'], 'deep')
        self.assertEqual(configured['decisionProfileId'], PROFILE_ID)
        self.assertEqual(SCHEMA_VERSION, 12)

    def test_outside_choice_is_rejected_and_not_abstention(self):
        board, decision = self.settle('foreign')
        self.assertEqual(decision['status'], 'needs-host')
        health = board.store.decisions.health_summary()
        self.assertEqual(health['boundsRejectedCount'], 1)
        self.assertEqual(health['abstentionCount'], 0)


if __name__ == '__main__':
    unittest.main()
