"""Router answer boundary checks without any native model calls."""
import unittest
import json

from buddy import router
from buddy.adapters.dsh import DshAdapter
from buddy.errors import BoardError
from fixtures.router_tool_receipt import claim_tool_receipt


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

    def test_cancelled_native_receipt_without_stop_proof_is_not_cancelled(self):
        import tempfile
        from pathlib import Path
        from types import SimpleNamespace
        from buddy.adapters.read_only import collect
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'native.json'
            output.write_text(json.dumps({'status': 'cancelled', 'processState': {'shutdownConfirmed': False}}))
            handle = SimpleNamespace(log_paths={'stdout': str(output)}, process=SimpleNamespace(returncode=1),
                                     shutdown_confirmed=lambda: True)
            result = collect(handle)
        self.assertEqual(result.status, 'failed')
        self.assertFalse(result.shutdown_confirmed)

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
        from fixtures import mock_readonly
        mock_readonly.install(self)
        self.enterContext(patch('buddy.decision.DecisionCoordinator._adapter_available', return_value=(True, None)))

    def settle(self, profile, *, code=None, preferences=None, result_status=None):
        board = self.board()
        self.seed(board, preferences=preferences)
        request = board.call('selection_request', {'requestId': 'route', 'task': 'choose'})
        board.call('worker_register', {'workerId': 'router', 'adapter': 'decision', 'capabilities': ['decision']})
        claim = self.claim(board, 'router', run_id=request['runId'])['claim']
        result = {**claim_tool_receipt(claim), 'status': 'error' if code else 'ok', 'operation': 'select',
                  'tableRevision': claim['decisionInput']['tableRevision'],
                  'stopEvidence': {'shutdownConfirmed': True, 'native': {'shutdownConfirmed': True}},
                  'usage': {'elapsedMs': 100, 'toolCalls': 0},
                  'inputVerification': {'unchanged': True, 'manifestSha256': None, 'snapshotSha256': 'fixture-digest'},
                  'decision': {'profileId': profile, 'reason': 'Read the frozen input',
                               'evidence': [{'kind': 'card', 'ref': 'not-supplied'}]}}
        if code:
            result['code'] = code
        board.call('worker_result', {'workerId': 'router', 'attemptId': claim['attempt']['attemptId'],
                                    'generation': claim['attempt']['generation'], 'nonce': NONCE,
                                    'status': result_status or ('failed' if code else 'ok'), 'shutdownConfirmed': True, 'result': result})
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
                self.assertEqual(health['failureCount'], 1 if code == 'router-budget-exhausted' else 0)
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
        self.assertEqual(continued['routing']['status'], 'queued')
        self.assertIsNone(continued['routing']['source'])
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
        self.assertEqual(configured['routerProfileIds'], [PROFILE_ID])
        self.assertEqual(board.store.db.meta('schema_version'), str(SCHEMA_VERSION))

    def test_budget_update_does_not_change_selection_request_replay(self):
        board = self.board()
        self.seed(board)
        params = {'requestId': 'stable-budget-request', 'task': 'choose'}
        original = board.call('selection_request', params)
        with board.store.db.write() as connection:
            connection.execute("INSERT OR REPLACE INTO meta(key,value) VALUES('router_budget_preset','deep')")
        replay = board.call('selection_request', params)
        self.assertTrue(replay['duplicate'])
        self.assertEqual(replay['decisionId'], original['decisionId'])
        view = board.call('selection_get', {'decisionId': original['decisionId']})['decision']
        self.assertEqual(view['budget']['preset'], 'standard')

    def test_cancellation_keeps_budget_code_out_of_budget_count(self):
        board, decision = self.settle(None, code='router-budget-exhausted', result_status='cancelled')
        health = board.store.decisions.health_summary()
        self.assertEqual(decision['status'], 'cancelled')
        self.assertEqual(health['cancelledCount'], 1)
        self.assertEqual(health['budgetExhaustedCount'], 0)

    def test_retained_legacy_capability_uses_local_eligibility_not_certificate(self):
        board = self.board()
        self.seed(board)
        with patch('buddy.adapters.dsh.DshAdapter.local_read_only_check', return_value={'eligible': False, 'reasonCode': 'readonly-unavailable', 'reason': 'fixture',
                'systemSandbox': False, 'sameAttemptContinuation': False}):
            snapshot = board.call('console_snapshot', {})
        profile = next(p for p in snapshot['profiles'] if p['profileId'] == PROFILE_ID)
        self.assertNotIn('decision', profile['capabilities'])
        with board.store.db.read() as connection:
            recorded = json.loads(connection.execute('SELECT capabilities_json FROM evaluation_profiles WHERE profile_id=?', (PROFILE_ID,)).fetchone()[0])
        self.assertIn('decision', recorded)
        self.assertFalse(hasattr(DshAdapter, 'read_only_structured_verified'))
        eligible = board.call('console_snapshot', {})
        self.assertIn('decision', next(p for p in eligible['profiles'] if p['profileId'] == PROFILE_ID)['capabilities'])

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

    def test_many_files_are_read_through_one_batch_process_and_failures_leave_no_copy(self):
        from unittest import mock
        from buddy import router_input
        for index in range(40):
            (self.repo / f'src/many-{index}.txt').write_text(f'file {index}\n')
        self.git('add', 'src')
        manifest = self.prepare(kind='existing')
        attempt = self.root / 'batch-attempt'
        attempt.mkdir()
        real_popen, real_run = router_input.subprocess.Popen, router_input.subprocess.run
        with mock.patch.object(router_input.subprocess, 'Popen', wraps=real_popen) as popen, \
                mock.patch.object(router_input.subprocess, 'run', wraps=real_run) as run:
            mirror, _ = router_input.prepare(manifest, attempt)
        # subprocess.run is built on Popen, so count only the blob readers.
        readers = [call for call in popen.call_args_list if 'cat-file' in call.args[0]]
        self.assertEqual(len(readers), 1)
        self.assertFalse([call for call in run.call_args_list if 'cat-file' in call.args[0]])
        self.assertEqual((mirror / 'src/many-39.txt').read_text(), 'file 39\n')
        router_input.discard(mirror)
        self.assertFalse(mirror.exists())
        broken = dict(manifest, inputTree='0' * 40)
        attempt = self.root / 'broken-attempt'
        attempt.mkdir()
        with mock.patch.object(router_input.workspace, 'verify'), self.assertRaises(BoardError):
            router_input.prepare(broken, attempt)
        self.assertFalse((attempt / 'frozen-input').exists())

    def test_git_replace_cannot_substitute_frozen_blob_contents(self):
        from buddy import router_input
        old = self.git('rev-parse', 'HEAD:src/file.txt').decode().strip()
        replacement = self.git('hash-object', '-w', '--stdin', data=b'replacement content').decode().strip()
        self.git('replace', old, replacement)
        manifest = self.prepare(kind='existing')
        attempt = self.root / 'replace-attempt'
        attempt.mkdir()
        mirror, _ = router_input.prepare(manifest, attempt)
        self.assertEqual((mirror / 'src/file.txt').read_bytes(), b'base\n')

    def test_frozen_symlink_cycle_has_a_bounded_input_error(self):
        from buddy import router_input
        (self.repo / 'src/a').symlink_to('b')
        (self.repo / 'src/b').symlink_to('a')
        self.git('add', 'src/a', 'src/b')
        manifest = self.prepare(kind='existing')
        attempt = self.root / 'loop-attempt'
        attempt.mkdir()
        with self.assertRaises(BoardError) as caught:
            router_input.prepare(manifest, attempt)
        self.assertEqual(caught.exception.code, 'router-input-unavailable')

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
