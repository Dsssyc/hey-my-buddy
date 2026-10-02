"""L4-G2 frozen claims, publication evidence and no automatic replacement calls."""
from __future__ import annotations

import json
import os
from unittest.mock import patch

from buddy.adapters.base import ExecutionContext
from buddy.adapters.decision import DecisionAdapter
from buddy.adapters.dsh import DshAdapter
from buddy.errors import BoardError
from support import InProcessBoard
from test_decision import DecisionTestCase, PROFILE, PROFILE_ID, SECOND_PROFILE_ID
from fixtures.router_tool_receipt import claim_tool_receipt

NONCE = 'g' * 32


def stopped_answer(claim, *, profile_id=PROFILE_ID):
    """A deterministic native receipt, with each required fact explicit."""
    document = claim['decisionInput']
    return {**claim_tool_receipt(claim), 'status': 'ok', 'operation': 'select', 'tableRevision': document['tableRevision'],
            'requested': document['profile'], 'usage': {'elapsedMs': 100, 'toolCalls': 0},
            'zeroToolVerified': True,
            'stopEvidence': {'shutdownConfirmed': True, 'native': {'shutdownConfirmed': True}},
            'inputVerification': {'unchanged': True, 'snapshotSha256': 'private-fixture-digest',
                                  'manifestSha256': (document.get('executionWorkspace') or {}).get('manifestSha256')},
            'decision': {'profileId': profile_id, 'reason': 'Frozen legal candidate', 'evidence': []}}


class SingleRouterClaimTests(DecisionTestCase):
    def case_board(self):
        self._case_number = getattr(self, '_case_number', 0) + 1
        board = InProcessBoard(self.directory / f'case-{self._case_number}')
        self._stack.append(board)
        return board

    def claim_route(self, board, request, *, worker='router', command='claim'):
        board.call('worker_register', {'workerId': worker, 'adapter': 'decision', 'capabilities': ['decision']})
        return board.client().claim(worker, command, NONCE, task_id=request['runId'])

    def report(self, board, claim, output, *, shutdown=True, status='ok', worker='router'):
        return board.call('worker_result', {'workerId': worker, 'attemptId': claim['attempt']['attemptId'],
            'generation': claim['attempt']['generation'], 'nonce': NONCE,
            'status': status, 'shutdownConfirmed': shutdown, 'result': output})

    def assert_unclaimed(self, board, request, code, frozen):
        response = self.claim_route(board, request)
        self.assertIsNone(response['claim'])
        view = self.decision(board, request['decisionId'])
        self.assertEqual(view['status'], 'needs-host')
        self.assertEqual(view['output']['code'], code)
        self.assertEqual(view['input'], frozen['input'])
        self.assertEqual(view['requested'], frozen['requested'])
        with board.store.db.read() as connection:
            self.assertEqual(connection.execute('SELECT COUNT(*) FROM attempts').fetchone()[0], 0)
            self.assertEqual(connection.execute('SELECT COUNT(*) FROM evaluation_readers').fetchone()[0], 0)
            event = json.loads(connection.execute("SELECT payload_json FROM events WHERE kind='decision.needs_host'").fetchone()[0])
            self.assertEqual(event['errorCode'], code)
        self.readonly_start.assert_not_called()

    def test_setting_revision_identity_mode_and_budget_changes_never_refresh_claim(self):
        for settings in ({'routerProfileId': SECOND_PROFILE_ID}, {'defaultRoutingMode': 'fast'},
                         {'routingBudget': 'brief'}):
            with self.subTest(settings=settings):
                board = self.case_board()
                self.seed(board)
                request = self.request(board)
                frozen = self.decision(board, request['decisionId'])
                self.publish_user_patch(board, request_id='change', command_id='change', configuration=settings)
                self.assert_unclaimed(board, request, 'router-configuration-changed', frozen)
                board.close()
                self._stack.remove(board)

    def test_published_router_identity_drift_is_not_a_different_family_claim(self):
        board = self.board()
        self.seed(board)
        request = self.request(board)
        frozen = self.decision(board, request['decisionId'])
        with board.store.db.write() as connection:
            connection.execute('UPDATE evaluation_profiles SET model=? WHERE profile_id=?', ('deepseek-v4-pro', PROFILE_ID))
        self.assert_unclaimed(board, request, 'router-profile-changed', frozen)

    def test_profile_removal_incomplete_disabled_and_unavailable_are_specific_boundaries(self):
        for sql, code in (("DELETE FROM evaluation_profiles WHERE profile_id=?", 'router-not-published'),
                          ("UPDATE evaluation_profiles SET effort='' WHERE profile_id=?", 'router-incomplete'),
                          ("UPDATE evaluation_profiles SET enabled=0 WHERE profile_id=?", 'router-unavailable'),
                          ("UPDATE evaluation_profiles SET available=0 WHERE profile_id=?", 'router-unavailable')):
            with self.subTest(code=code, sql=sql):
                board = self.case_board()
                self.seed(board)
                request = self.request(board)
                frozen = self.decision(board, request['decisionId'])
                with board.store.db.write() as connection:
                    connection.execute(sql, (PROFILE_ID,))
                self.assert_unclaimed(board, request, code, frozen)
                board.close()
                self._stack.remove(board)

    def test_unhealthy_router_and_exhausted_router_cannot_claim_or_consume_retry(self):
        for unavailable in ('health', 'quota'):
            with self.subTest(unavailable=unavailable):
                board = self.case_board()
                self.seed(board)
                request = self.request(board)
                frozen = self.decision(board, request['decisionId'])
                if unavailable == 'health':
                    with board.store.db.write() as connection:
                        connection.execute("UPDATE harness_health SET status='unhealthy' WHERE adapter='dsh'")
                    self.assert_unclaimed(board, request, 'router-unavailable', frozen)
                else:
                    with patch('buddy.native_observations.exhausted', return_value={'nativeCode': 'quota'}), \
                            patch('buddy.quota_routing.claim', side_effect=AssertionError('Router must not use a Worker retry')):
                        self.assert_unclaimed(board, request, 'router-quota-exhausted', frozen)
                board.close()
                self._stack.remove(board)

    def test_fast_entrypoint_loss_does_not_run_review(self):
        board = self.board()
        self.seed(board)
        self.publish_user_patch(board, request_id='fast', command_id='fast', configuration={'defaultRoutingMode': 'fast'})
        request = self.request(board)
        frozen = self.decision(board, request['decisionId'])
        with patch('buddy.adapters.dsh.DshAdapter.no_tool_structured', False):
            self.assert_unclaimed(board, request, 'router-no-tool-unsupported', frozen)

    def test_family_capacity_is_frozen_and_unknown_stop_keeps_its_slot(self):
        board = self.board(max_concurrent=3)
        self.seed(board)
        with board.store.db.write() as connection:
            connection.execute("INSERT INTO model_concurrency(adapter,provider,model,concurrency_limit,updated_revision,created_at,updated_at) VALUES(?,?,?,1,1,'fixture','fixture')",
                               (PROFILE['adapter'], PROFILE['provider'], PROFILE['model']))
        first = self.request(board, request_id='one')
        second = self.request(board, request_id='two')
        claim = self.claim_route(board, first)['claim']
        self.assertEqual(claim['decisionInput'], self.decision(board, first['decisionId'])['input'])
        blocked = self.claim_route(board, second, worker='other', command='blocked')
        self.assertIsNone(blocked['claim'])
        self.assertEqual(blocked['reason'], 'model-capacity')
        self.assertEqual(self.decision(board, second['decisionId'])['status'], 'queued')
        self.report(board, claim, stopped_answer(claim), shutdown=False)
        blocked = self.claim_route(board, second, worker='other', command='unknown-stop')
        self.assertIsNone(blocked['claim'])
        self.assertEqual(blocked['reason'], 'model-capacity')
        with board.store.db.read() as connection:
            family = connection.execute('SELECT model_adapter,model_provider,model_model,shutdown_confirmed FROM attempts').fetchone()
            self.assertEqual(tuple(family), ('dsh', 'deepseek-official', 'deepseek-flash', 0))
            self.assertEqual(board.store._model_active_counts(connection), {('dsh', 'deepseek-official', 'deepseek-flash'): 1})
        self.assertIsNone(self.decision(board, first['decisionId'])['selectedProfile'])

    def test_confirmed_stop_releases_same_router_family_for_next_claim(self):
        board = self.board(max_concurrent=3)
        self.seed(board)
        with board.store.db.write() as connection:
            connection.execute("INSERT INTO model_concurrency(adapter,provider,model,concurrency_limit,updated_revision,created_at,updated_at) VALUES(?,?,?,1,1,'fixture','fixture')",
                               ('dsh', 'deepseek-official', 'deepseek-flash'))
        first = self.request(board, request_id='one')
        second = self.request(board, request_id='two')
        claim = self.claim_route(board, first)['claim']
        self.report(board, claim, stopped_answer(claim))
        self.assertEqual(self.decision(board, first['decisionId'])['status'], 'completed')
        next_claim = self.claim_route(board, second, worker='other', command='next')['claim']
        self.assertIsNotNone(next_claim)
        self.assertEqual(next_claim['decisionInput']['routerProfile'], claim['decisionInput']['routerProfile'])

    def test_missing_stop_budget_and_snapshot_evidence_cannot_publish(self):
        for field, replacement, code in (
                ('stopEvidence', None, 'router-stop-unconfirmed'),
                ('stopEvidence', {'shutdownConfirmed': True, 'native': {'shutdownConfirmed': False}}, 'router-stop-unconfirmed'),
                ('usage', None, 'router-tool-evidence-unverified'),
                ('usage', {'elapsedMs': 1, 'toolCalls': False}, 'router-tool-evidence-unverified'),
                ('usage', {'elapsedMs': 1, 'toolCalls': -1}, 'router-tool-evidence-unverified'),
                ('usage', {'elapsedMs': 300001, 'toolCalls': 0}, 'router-budget-exhausted'),
                ('usage', {'elapsedMs': 1, 'toolCalls': 25}, 'router-budget-exhausted'),
                ('inputVerification', None, 'router-input-changed'),
                ('inputVerification', {'unchanged': True, 'manifestSha256': None}, 'router-input-changed'),
                ('inputVerification', {'unchanged': False, 'snapshotSha256': 'changed'}, 'router-input-changed')):
            with self.subTest(field=field, replacement=replacement):
                board = self.case_board()
                self.seed(board)
                request = self.request(board)
                claim = self.claim_route(board, request)['claim']
                answer = stopped_answer(claim)
                answer[field] = replacement
                self.report(board, claim, answer)
                decision = self.decision(board, request['decisionId'])
                self.assertEqual(decision['status'], 'needs-host')
                self.assertEqual(decision['output']['code'], code)
                self.assertIsNone(decision['selectedProfile'])
                self.assertIsNone(self.claim_route(board, request, command='late')['claim'])
                board.close()
                self._stack.remove(board)

    def test_review_exact_budget_boundary_and_unknown_bytes_remain_valid(self):
        board = self.board()
        self.seed(board)
        request = self.request(board)
        claim = self.claim_route(board, request)['claim']
        answer = stopped_answer(claim)
        answer['usage'] = {'elapsedMs': 300000, 'toolCalls': 24, 'bytesRead': None}
        answer.update(claim_tool_receipt(claim, 24))
        self.report(board, claim, answer)
        self.assertEqual(self.decision(board, request['decisionId'])['status'], 'completed')

    def test_preflight_failure_with_unknown_stop_does_not_open_a_stopped_boundary(self):
        board = self.board()
        self.seed(board)
        request = self.request(board)
        claim = self.claim_route(board, request)['claim']
        self.report(board, claim, {'status': 'error', 'code': 'router-review-unavailable', 'modelStarted': False},
                    status='failed', shutdown=False)
        decision = self.decision(board, request['decisionId'])
        self.assertEqual(decision['status'], 'failed')
        self.assertIn('unconfirmed', decision['reason'])
        self.assertIsNone(self.claim_route(board, request, command='no-retry', worker='second-worker')['claim'])
        with board.store.db.read() as connection:
            self.assertEqual(connection.execute('SELECT shutdown_confirmed FROM attempts').fetchone()[0], 0)

    def test_mismatched_attempt_generation_cannot_publish_or_replace_audit(self):
        board = self.board()
        self.seed(board)
        request = self.request(board)
        claim = self.claim_route(board, request)['claim']
        output = stopped_answer(claim)
        with board.store.db.write() as connection:
            task = connection.execute('SELECT * FROM tasks WHERE task_id=?', (request['runId'],)).fetchone()
            attempt = dict(connection.execute('SELECT * FROM attempts').fetchone())
            attempt['generation'] += 1
            result = board.store.decisions.complete(connection, task=task, attempt=attempt, status='ok',
                result=output, shutdown_confirmed=True, error=None, now=board.store.now())
            self.assertTrue(result['late'])
        decision = self.decision(board, request['decisionId'])
        self.assertEqual(decision['status'], 'running')
        self.assertIsNone(decision['output'])
        self.assertIsNone(decision['selectedProfile'])

    def test_historical_fallback_json_is_audit_only_and_is_not_rewritten(self):
        board = self.board()
        self.seed(board)
        request = self.request(board)
        with board.store.db.write() as connection:
            row = board.store.decisions._row(connection, request['decisionId'])
            historical = json.loads(row['requested_json'])
            historical.update(requestedRoutingMode='review', fallback={'from': 'review', 'to': 'fast'})
            connection.execute('UPDATE decision_requests SET requested_json=? WHERE decision_id=?',
                               (json.dumps(historical), request['decisionId']))
        audit = self.decision(board, request['decisionId'])
        self.assertEqual(audit['requested'], historical)
        self.assertNotIn('fallback', audit)
        self.assertNotIn('requestedRoutingMode', audit)
        compact = self.decision(board, request['decisionId'], audit=False)
        self.assertNotIn('fallback', compact)
        self.assertNotIn('requestedRoutingMode', compact)

    def test_adapter_start_and_collect_use_local_eligibility_without_certificate(self):
        board = self.board()
        self.seed(board)
        request = self.request(board)
        claim = self.claim_route(board, request)['claim']
        context = ExecutionContext(task_id=request['runId'], attempt_id=claim['attempt']['attemptId'],
            generation=claim['attempt']['generation'], spec=claim['task']['spec'],
            directory=board.directory / 'adapter-unit', runtime={},
            environment={**os.environ, 'BUDDY_STATE_DIR': str(board.directory)}, decision_input=claim['decisionInput'])
        native = DecisionAdapter()
        self.assertFalse(hasattr(DshAdapter, 'read_only_structured_verified'))
        self.assertTrue(native.available()[0])
        handle = native.start(context)
        handle.process.wait(timeout=5)
        outcome = native.collect(handle, context)
        self.assertEqual(outcome.status, 'ok')
        self.assertTrue(outcome.shutdown_confirmed)
        self.assertTrue(outcome.result['inputVerification']['unchanged'])
        self.assertTrue(outcome.result['stopEvidence']['native']['shutdownConfirmed'])
        self.assertEqual(self.readonly_start.call_count, 1)
        self.report(board, claim, outcome.result)
        self.assertEqual(self.decision(board, request['decisionId'])['status'], 'completed')

    def test_adapter_local_check_failure_never_prepares_input_or_starts_native(self):
        board = self.board()
        self.seed(board)
        request = self.request(board)
        document = self.decision(board, request['decisionId'])['input']
        context = ExecutionContext(task_id='task', attempt_id='attempt', generation=1,
            spec={'adapter': 'decision'}, directory=board.directory / 'blocked-adapter', runtime={},
            environment={'BUDDY_STATE_DIR': str(board.directory)}, decision_input=document)
        with patch('buddy.adapters.dsh.DshAdapter.local_read_only_check', return_value={'eligible': False}):
            with self.assertRaises(BoardError) as failure:
                DecisionAdapter().start(context)
        self.assertEqual(failure.exception.code, 'router-review-unsupported')
        self.readonly_start.assert_not_called()
        self.input_prepare.assert_not_called()

    def test_claim_reader_attempt_and_started_event_roll_back_together(self):
        board = self.board()
        self.seed(board)
        request = self.request(board)
        original = board.store.decisions._mark_running
        def failed_mark(*args, **kwargs):
            original(*args, **kwargs)
            raise BoardError('fixture-failure', 'fail after reader and decision binding')
        with patch.object(board.store.decisions, '_mark_running', side_effect=failed_mark):
            with self.assertRaises(BoardError):
                self.claim_route(board, request)
        with board.store.db.read() as connection:
            self.assertEqual(connection.execute('SELECT COUNT(*) FROM attempts').fetchone()[0], 0)
            self.assertEqual(connection.execute('SELECT COUNT(*) FROM evaluation_readers').fetchone()[0], 0)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM events WHERE kind='decision.started'").fetchone()[0], 0)
        decision = self.decision(board, request['decisionId'])
        self.assertEqual(decision['status'], 'queued')
        self.assertIsNone(decision['attemptId'])
        self.assertIsNotNone(self.claim_route(board, request, command='retry-after-rollback')['claim'])

    def test_publication_failure_rolls_back_choice_and_commits_worker_receipt(self):
        board = self.board()
        self.seed(board)
        request = self.request(board)
        claim = self.claim_route(board, request)['claim']
        original = board.store.decisions._finish
        def failed_finish(*args, **kwargs):
            original(*args, **kwargs)
            if kwargs['status'] == 'completed':
                raise BoardError('fixture-failure', 'fail after choice and event publication')
        with patch.object(board.store.decisions, '_finish', side_effect=failed_finish):
            response = self.report(board, claim, stopped_answer(claim))
        self.assertTrue(response['committed'])
        decision = self.decision(board, request['decisionId'])
        self.assertEqual(decision['status'], 'needs-host')
        self.assertIsNone(decision['profileId'])
        self.assertIsNone(decision['selectedProfile'])
        with board.store.db.read() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM events WHERE kind='decision.completed'").fetchone()[0], 0)
            self.assertIsNotNone(connection.execute('SELECT result_json FROM attempts').fetchone()[0])
            self.assertEqual(connection.execute('SELECT COUNT(*) FROM evaluation_readers WHERE released_at IS NULL').fetchone()[0], 0)

    def test_frozen_router_is_rechecked_before_publication(self):
        board = self.board()
        self.seed(board)
        request = self.request(board)
        claim = self.claim_route(board, request)['claim']
        with patch('buddy.adapters.dsh.DshAdapter.local_read_only_check', return_value={
                'eligible': False, 'reasonCode': 'readonly-resource-missing', 'reason': 'removed controller',
                'systemSandbox': False, 'sameAttemptContinuation': False}):
            self.report(board, claim, stopped_answer(claim))
        decision = self.decision(board, request['decisionId'])
        self.assertEqual(decision['status'], 'needs-host')
        self.assertEqual(decision['output']['code'], 'router-review-unsupported')
        self.assertEqual(decision['input'], claim['decisionInput'])
        self.assertIsNone(self.claim_route(board, request, command='no-replacement')['claim'])
