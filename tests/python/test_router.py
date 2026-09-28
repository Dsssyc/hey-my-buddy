"""Router answer boundary checks without any native model calls."""
import unittest
import json

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

    def test_reroute_waits_for_new_continuation_manifest(self):
        board = self.board()
        self.seed(board, decision_profile=None)
        submitted = board.call('workflow_submit', {
            'requestId': 'frozen-reroute', 'hostId': 'host', 'task': 'Original task',
            'cwd': str(self.workdir()), 'executionWorkspace': {'kind': 'existing', 'access': 'write'}})
        self.controls[submitted['runId']] = submitted['control']
        continued = self.continue_run(board, submitted, reroute=True, input='Changed continuation task')
        with board.store.db.read() as connection:
            self.assertEqual(connection.execute('SELECT COUNT(*) FROM workflow_routes').fetchone()[0], 1)
            self.assertIsNone(connection.execute('SELECT workspace_manifest_json FROM workflow_continuations').fetchone()[0])
        board.store.workflow.prepare_continuation_workspace({'runId': submitted['runId']})
        with board.store.db.read() as connection:
            self.assertEqual(connection.execute('SELECT COUNT(*) FROM workflow_routes').fetchone()[0], 2)
            continuation = connection.execute('SELECT workspace_manifest_json FROM workflow_continuations').fetchone()[0]
            self.assertIsNotNone(continuation)
            frozen = json.loads(connection.execute('SELECT requested_json FROM decision_requests ORDER BY rowid DESC LIMIT 1').fetchone()[0])
            self.assertEqual(frozen['executionWorkspace'], json.loads(continuation))
            self.assertIn('Changed continuation task', frozen['task'])

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


import test_workspace as workspace_fixtures


class RouterInputTests(unittest.TestCase):
    setUp = workspace_fixtures.WorkspaceTests.setUp
    git = workspace_fixtures.WorkspaceTests.git
    intent = workspace_fixtures.WorkspaceTests.intent
    prepare = workspace_fixtures.WorkspaceTests.prepare

    def test_existing_input_is_materialized_without_live_or_ignored_files(self):
        from buddy import router_input
        (self.repo / 'src/file.txt').write_text('dirty frozen input')
        (self.repo / '.gitattributes').write_text('src/file.txt export-ignore\n')
        self.git('add', '.gitattributes')
        (self.repo / '.gitignore').write_text('secret-cache\n')
        (self.repo / 'secret-cache').write_text('not frozen')
        manifest = self.prepare(kind='existing')
        attempt = self.root / 'attempt'
        attempt.mkdir()
        mirror, fingerprint = router_input.prepare(manifest, attempt)
        self.assertNotEqual(mirror, self.repo)
        self.assertEqual((mirror / 'src/file.txt').read_text(), 'dirty frozen input')
        self.assertFalse((mirror / 'secret-cache').exists())
        self.assertFalse((mirror / '.git').exists())
        self.assertTrue(router_input.verify(manifest, mirror, fingerprint)['unchanged'])
        (self.repo / 'src/file.txt').write_text('Host changed after Router start')
        self.assertEqual((mirror / 'src/file.txt').read_text(), 'dirty frozen input')
        self.assertEqual(router_input.verify(manifest, mirror, fingerprint)['code'], 'router-input-changed')

    def test_changed_router_copy_and_escaping_link_are_rejected(self):
        from buddy import router_input
        manifest = self.prepare(kind='existing')
        attempt = self.root / 'attempt'
        attempt.mkdir()
        mirror, fingerprint = router_input.prepare(manifest, attempt)
        (mirror / 'src/file.txt').chmod(0o644)
        (mirror / 'src/file.txt').write_text('unexpected native write')
        self.assertFalse(router_input.verify(manifest, mirror, fingerprint)['unchanged'])
        (self.repo / 'src/escape').symlink_to(self.root / 'outside')
        self.git('add', 'src/escape')
        manifest = self.prepare(request='escape', kind='existing')
        attempt = self.root / 'escape-attempt'
        attempt.mkdir()
        with self.assertRaises(BoardError):
            router_input.prepare(manifest, attempt)


if __name__ == '__main__':
    unittest.main()
