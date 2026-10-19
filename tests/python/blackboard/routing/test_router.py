"""Router answer boundary checks without any native model calls."""
import unittest
import json

from hey_my_buddy.blackboard.routing import router
from hey_my_buddy.buddy.harnesses.dsh.adapter import DshAdapter
from hey_my_buddy.errors import BoardError
from blackboard.routing.fixtures.router_tool_receipt import claim_tool_receipt


class RouterContractTests(unittest.TestCase):
    def test_independent_references_and_alternative_are_advisory(self):
        answer = {"buddyId": "legal-alternative", "note": "Better fit after reading the code",
                  "evidence": [{"kind": "annotation", "ref": "model-note"},
                               {"kind": "file", "ref": "src/worker.py"}]}
        self.assertEqual(router.validate_answer(answer, ["preferred", "legal-alternative"]), answer)

    def test_candidate_escape_has_noncorrectable_code(self):
        with self.assertRaises(BoardError) as caught:
            router.validate_answer({"buddyId": "foreign", "note": "x", "evidence": []}, ["legal"])
        self.assertEqual(caught.exception.code, "router-out-of-bounds")
        self.assertEqual(caught.exception.details, {"bound": "not-a-candidate"})

    def test_file_references_cannot_escape_or_embed_lines(self):
        for ref in ("../secret", "/tmp/other-run", "src/../../secret", "C:\\secret", "file\ncontents"):
            with self.subTest(ref=ref), self.assertRaises(BoardError):
                router.validate_answer({"buddyId": "legal", "note": "x",
                                        "evidence": [{"kind": "file", "ref": ref}]}, ["legal"])

    def test_abstention_and_malformed_results(self):
        self.assertIsNone(router.validate_answer('{"buddyId":null,"note":"Insufficient evidence","evidence":[]}', [])['buddyId'])
        for value in ("invalid", {}, {"buddyId": None, "note": " ", "evidence": []},
                      {"buddyId": None, "note": "x", "evidence": [], "request": {}}):
            with self.subTest(value=value), self.assertRaises(BoardError):
                router.validate_answer(value, [])

    def test_cancelled_native_receipt_without_stop_proof_is_not_cancelled(self):
        import tempfile
        from pathlib import Path
        from hey_my_buddy.buddy.harnesses.run_contract import (
            RunEnd, RunResult, StopEvidence, StopLayer,
        )
        from hey_my_buddy.buddy.roles.run_execution import InlineRunHandle, review_request
        from hey_my_buddy.buddy.roles.structured_call import collect
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            control = {
                'operation': 'review', 'harness': 'dsh', 'taskId': 'task',
                'attemptId': 'attempt', 'generation': 1, 'invocationId': 'invocation',
                'privateRoot': str(root), 'nativeRoot': str(root / 'native'),
                'sessionRecordPath': None,
                'executionLiveFile': str(root / 'execution-live.json'),
                'cwd': str(root), 'timeoutSeconds': 10, 'canCorrect': False,
                'spec': {'provider': 'fixture', 'model': 'fixture', 'effort': 'off'},
                'readOnlyRequest': {
                    'prompt': 'fixture', 'budget': {'toolCalls': 1},
                    'outputSchema': {'type': 'object', 'properties': {}, 'additionalProperties': False},
                },
            }
            request, *_ = review_request(control, None)
            native = RunResult(identity=request.identity, harness='dsh',
                               end=RunEnd(status='cancelled'),
                               stop_evidence=StopEvidence(native=StopLayer(group_state='unknown')))
            handle = InlineRunHandle(control, request.identity, grace_seconds=0)
            handle.request = request
            handle.result = native
            handle.verdict = {'stopReason': None, 'elapsedMs': 0}
            handle.exit_code = 1
            handle.done.set()
            result = collect(handle)
        self.assertEqual(result.status, 'failed')
        self.assertFalse(result.shutdown_confirmed)

    def test_prompt_prefix_stays_stable_and_excludes_display_metadata(self):
        doc = {"buddies": [{"buddyId": "legal"}], "task": "first", "title": "display-only"}
        first = router.render_prompt(doc)
        second = router.render_prompt({**doc, "task": "second", "macroTask": "private display label"})
        self.assertEqual(first.split('\n\n')[:2], second.split('\n\n')[:2])
        self.assertNotIn("display-only", first)
        self.assertNotIn("private display label", second)


from unittest.mock import patch
from blackboard.routing.test_decision import DecisionTestCase, BUDDY_ID, SECOND_BUDDY_ID
from blackboard.tasks.test_workflow import WorkflowTestCase, NONCE


class RouterPublicationTests(WorkflowTestCase):
    seed = DecisionTestCase.seed

    def setUp(self):
        super().setUp()
        self.catalog_fixture()
        self.enterContext(patch('hey_my_buddy.buddy.harnesses.dsh.adapter.DshAdapter.read_only_structured', True))
        from protocol.fixtures import mock_readonly
        mock_readonly.install(self)
        self.enterContext(patch('hey_my_buddy.blackboard.routing.decision.DecisionCoordinator._adapter_available', return_value=(True, None)))

    def settle(self, buddy, *, code=None, preferences=None, result_status=None):
        board = self.board()
        self.seed(board, preferences=preferences)
        request = board.call('selection_request', {'requestId': 'route', 'task': 'choose'})
        pass  # Admission is owned by the service.
        claim = self.claim(board, 'router', run_id=request['runId'])['claim']
        result = {**claim_tool_receipt(claim), 'status': 'error' if code else 'ok', 'operation': 'select',

                  'stopEvidence': {'shutdownConfirmed': True, 'native': {'shutdownConfirmed': True}},
                  'usage': {'elapsedMs': 100, 'toolCalls': 0},
                  'inputVerification': {'unchanged': True, 'manifestSha256': None, 'snapshotSha256': 'fixture-digest'},
                  'decision': {'buddyId': buddy, 'note': 'Read the frozen input',
                               'evidence': [{'kind': 'annotation', 'ref': 'not-supplied'}]}}
        if code:
            result['code'] = code
        board.call('execution_result', {'attemptId': claim['attempt']['attemptId'], 'generation': claim['attempt']['generation'], 'nonce': NONCE, 'status': result_status or ('failed' if code else 'ok'), 'shutdownConfirmed': True, 'result': result})
        return board, board.call('selection_get', {'decisionId': request['decisionId']})['decision']

    def test_publication_accepts_alternative_with_advisory_reference(self):
        board, decision = self.settle(SECOND_BUDDY_ID, preferences=[{'buddyId': BUDDY_ID, 'mode': 'prefer'}])
        self.assertEqual(decision['status'], 'completed')
        self.assertEqual(decision['policyCheck']['userPreference'], 'alternative')
        self.assertEqual(decision['evidence'], [{'kind': 'annotation', 'ref': 'not-supplied'}])

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
        self.seed(board, decision_buddy=None)
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
        from hey_my_buddy.blackboard.store.db import SCHEMA_VERSION
        board = self.board()
        self.seed(board)
        board.console_call('user_policy_publish', {
            'commandId': 'budget-change',
            'configuration': {'routingBudget': 'deep'}})
        configured = board.call('console_snapshot', {})['configuration']
        self.assertEqual(configured['routingBudget'], 'deep')
        self.assertEqual(configured['routerBuddyIds'], [BUDDY_ID])
        with board.store.db.read() as connection:
            self.assertEqual(connection.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0], str(SCHEMA_VERSION))

    def test_budget_update_does_not_change_selection_request_replay(self):
        board = self.board()
        self.seed(board)
        params = {'requestId': 'stable-budget-request', 'task': 'choose'}
        original = board.call('selection_request', params)
        with board.store.db.write() as connection:
            connection.execute("UPDATE router_settings SET budget_preset='deep' WHERE id=1")
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
        with patch('hey_my_buddy.buddy.harnesses.dsh.adapter.DshAdapter.local_read_only_check', return_value={'eligible': False, 'reason': {'code': 'harness.readonly-unavailable', 'params': {}},
                'systemSandbox': False, 'sameAttemptContinuation': False}):
            snapshot = board.call('console_snapshot', {})
        buddy = next(p for p in snapshot['buddies'] if p['buddyId'] == BUDDY_ID)
        self.assertNotIn('decision', buddy['capabilities'])
        with board.store.db.read() as connection:
            recorded = json.loads(connection.execute('SELECT capabilities_json FROM buddies WHERE buddy_id=?', (BUDDY_ID,)).fetchone()[0])
        self.assertIn('decision', recorded)
        self.assertFalse(hasattr(DshAdapter, 'read_only_structured_verified'))
        eligible = board.call('console_snapshot', {})
        self.assertIn('decision', next(p for p in eligible['buddies'] if p['buddyId'] == BUDDY_ID)['capabilities'])

    def test_outside_choice_is_rejected_and_not_abstention(self):
        board, decision = self.settle('foreign')
        self.assertEqual(decision['status'], 'needs-host')
        health = board.store.decisions.health_summary()
        self.assertEqual(health['boundsRejectedCount'], 1)
        self.assertEqual(health['abstentionCount'], 0)


import blackboard.tasks.test_workspace as workspace_fixtures


class RouterInputTests(unittest.TestCase):
    setUp = workspace_fixtures.WorkspaceTests.setUp
    git = workspace_fixtures.WorkspaceTests.git
    intent = workspace_fixtures.WorkspaceTests.intent
    prepare = workspace_fixtures.WorkspaceTests.prepare

    def test_existing_input_is_materialized_without_live_or_ignored_files(self):
        from hey_my_buddy.buddy.roles import router_input
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
        from hey_my_buddy.buddy.roles import router_input
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
        from hey_my_buddy.buddy.roles import router_input
        old = self.git('rev-parse', 'HEAD:src/file.txt').decode().strip()
        replacement = self.git('hash-object', '-w', '--stdin', data=b'replacement content').decode().strip()
        self.git('replace', old, replacement)
        manifest = self.prepare(kind='existing')
        attempt = self.root / 'replace-attempt'
        attempt.mkdir()
        mirror, _ = router_input.prepare(manifest, attempt)
        self.assertEqual((mirror / 'src/file.txt').read_bytes(), b'base\n')

    def test_frozen_symlink_cycle_has_a_bounded_input_error(self):
        from hey_my_buddy.buddy.roles import router_input
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
        from hey_my_buddy.buddy.roles import router_input
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
