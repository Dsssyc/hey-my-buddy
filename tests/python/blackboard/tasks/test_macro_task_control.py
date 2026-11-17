"""Console pause/resume/end uses durable units, real admission and stopped evidence."""
from __future__ import annotations

import json
import threading
from unittest.mock import patch

from blackboard.tasks.test_workflow import WorkflowTestCase, NONCE
from blackboard.routing.test_decision import DecisionTestCase
from blackboard.tasks import test_lead_owned as lead_helpers
from blackboard.tasks import test_interrupted_continuation as interruption_fixtures
from hey_my_buddy.blackboard.service.execution_scheduler import ExecutionScheduler
from types import SimpleNamespace
from console.test_console import ConsoleTestCase
from hey_my_buddy.blackboard.tasks.macro_tasks import pending_root_count
from support import macro_summaries
from hey_my_buddy.buddy.roles.turn_io import input_hash
from hey_my_buddy.errors import BoardError


class MacroTaskControlTests(WorkflowTestCase, ConsoleTestCase):
    lead = lead_helpers.LeadOwnedTests.lead
    owned = lead_helpers.LeadOwnedTests.owned
    finish_owned = lead_helpers.LeadOwnedTests.finish_owned
    interrupted = interruption_fixtures.InterruptedContinuationTests.interrupted
    def control(self, board, action, target, command=None, **extra):
        if not hasattr(self, '_control_browser'):
            _, self._control_browser = self.open_console(board)
        browser = self._control_browser
        status, _, raw = browser.command('macro_task_' + action,
            {'macroTaskId': target, 'commandId': command or action + target, **extra},
            csrf=browser.bootstrap()['csrfToken'])
        self.assertEqual(status, 200, raw)
        return json.loads(raw)['result']

    def pause_result(self, board, claim, *, shutdown=True, harness='dsh'):
        document = claim['claim']['turn']['input']
        result = {'processState': {'shutdownConfirmed': shutdown}}
        if harness in ('codex', 'dsh'):
            result['nativeSession'] = {
                'harness': harness, 'sessionId': 'paused-session', 'resumable': True,
                'storageOwner': 'harness-user-store' if harness == 'codex' else 'hey-my-buddy-micro-task'}
        if harness == 'codex':
            result.update(sessionId='paused-session', nativeTurnId='interrupted-native-turn')
            result['nativeCheckpoint'] = {
                'version': 1, **{key: document[key] for key in ('taskId','attemptId','generation','turnId')},
                'inputSha256': input_hash(document), 'sessionId': 'paused-session',
                'nativeTurnId': 'interrupted-native-turn', 'nativeTurnStarted': True,
                'nativeTurnStatus': 'interrupted', 'eventSeq': 4, 'bindingSaved': True}
        return board.call('execution_result', {
            'attemptId': document['attemptId'], 'generation': document['generation'], 'nonce': NONCE,
            'status': 'cancelled', 'result': result, 'shutdownConfirmed': shutdown,
            'terminationReason': 'pause' if shutdown else None})

    def test_pause_fences_existing_new_work_and_restart_resume_releases_both(self):
        board = self.board()
        first = self.submit(board, kind='worktree', create_macro={'title':'Pause unit','goal':'Work'})
        target = first['macroTaskId']
        paused = self.control(board, 'pause', target)
        self.assertEqual(paused, {'macroTaskId': target, 'duplicate': False})
        late = self.submit(board, kind='worktree', request_id='late', macroTaskId=target)
        other = self.submit(board, kind='worktree', request_id='other')
        for run in (first, late):
            self.assertEqual(board.call('workflow_get', {'runId':run['runId']})['queueReason'], 'paused')
            self.assertIsNone(self.claim(board, run_id=run['runId'])['claim'])
        self.assertIsNotNone(self.claim(board, run_id=other['runId'])['claim'])
        restarted = self.board()
        restarted.store.reconcile_startup()
        self.assertEqual(restarted.call('workflow_get', {'macroTaskId':target})['paused']['shutdownConfirmed'], True)
        self.assertIsNotNone(restarted.call('workflow_get', {'macroTaskId':target})['paused']['pausedAt'])
        self.assertIsNone(self.claim(restarted, run_id=late['runId'])['claim'])
        self.control(board, 'resume', target)
        for run in (first, late):
            self.assertIsNotNone(self.claim(board, run_id=run['runId'])['claim'])

    def test_pause_views_keep_equal_time_and_counts_but_distinct_stop_proof(self):
        board = self.board(max_concurrent=4)
        settled = self.submit(board, request_id='settled', create_macro={'title':'Settled pause','goal':'Work'})
        running = self.submit(board, request_id='running', cwd=str(self.workdir('other-pause')),
                              create_macro={'title':'Running pause','goal':'Work'})
        active = self.claim(board, run_id=running['runId'])
        instant = board.store.now()
        with patch.object(board.store.workflow, 'now', return_value=instant):
            first = self.control(board, 'pause', settled['macroTaskId'])
            second = self.control(board, 'pause', running['macroTaskId'])
        self.assertEqual(first, {'macroTaskId': settled['macroTaskId'], 'duplicate': False})
        self.assertEqual(second, {'macroTaskId': running['macroTaskId'], 'duplicate': False})
        expected = {settled['macroTaskId']: True, running['macroTaskId']: False}
        list_rows = {row['macroTaskId']: row for row in macro_summaries(board.call('macro_task_list', {}))}
        for target, confirmed in expected.items():
            pause = {'pausedAt': instant, 'shutdownConfirmed': confirmed}
            summary = list_rows[target]
            self.assertEqual(summary['paused'], pause)
            self.assertEqual(summary['counts']['unfinished'], 1)
            self.assertEqual(board.call('workflow_get', {'macroTaskId':target})['paused'], pause)
            self.assertEqual(board.call('macro_task_timeline', {'macroTaskId':target})['macroTask']['paused'], pause)
            project = board.call('workflow_get', {'cwd':summary['project']['path']})
            item = next(item for item in project['items'] if item.get('macroTaskId') == target)
            self.assertEqual(item['paused'], pause)
        self.pause_result(board, active)
        self.assertTrue(board.call('workflow_get', {'macroTaskId':running['macroTaskId']})['paused']['shutdownConfirmed'])
        spans = board.call('macro_task_timeline', {'macroTaskId':running['macroTaskId']})['spans']
        self.assertEqual(next(span for span in spans if span['kind'] == 'execution')['terminationReason'], 'pause')
        self.assertEqual(self.control(board, 'resume', settled['macroTaskId']),
                         {'macroTaskId': settled['macroTaskId'], 'duplicate': False})
        self.assertIsNone(board.call('workflow_get', {'macroTaskId':settled['macroTaskId']})['paused'])

    def test_paused_running_task_without_attempt_has_no_stop_proof(self):
        board = self.board()
        run = self.submit(board, request_id='missing-attempt')
        target = 'run:' + run['runId']
        self.control(board, 'pause', target)
        with board.store.db.write() as connection:
            connection.execute("UPDATE tasks SET state='running' WHERE task_id=?", (run['runId'],))
        self.assertEqual(board.call('macro_task_timeline', {'macroTaskId':target})['macroTask']['paused']['shutdownConfirmed'], False)

    def test_interruption_queues_one_console_continuation_and_native_resume_by_capability(self):
        board = self.board(max_concurrent=8)
        for harness, expected in (('codex','native-session'),('dsh','native-session'),
                                  ('zcode','reconstructed-new-session'),('claude','reconstructed-new-session')):
            with self.subTest(harness=harness):
                self.executors[harness].native_resume = harness != 'claude'
                run = self.submit(board, kind='worktree', request_id=harness, harness=harness)
                first = self.claim(board, run_id=run['runId'])
                target = 'run:' + run['runId']
                self.control(board, 'pause', target)
                attempt = board.call('task_get', {'runId':run['runId']})['task']['selectedAttempt']
                self.assertEqual(attempt['stopInitiator'], 'console')
                self.assertEqual(attempt['graceDeadline'] is not None, harness in ('codex','dsh'))
                self.pause_result(board, first, harness=harness)
                paused = board.call('workflow_get', {'runId':run['runId']})
                self.assertEqual((paused['state'],paused['queueReason'],paused['currentTurn']['state']),
                                 ('executing','paused','interrupted'))
                self.assertFalse(paused['awaitingHost'])
                self.assertEqual(pending_root_count(board.store), 0)
                self.assertIsNone(self.claim(board, run_id=run['runId'])['claim'])
                wait = {'runId':run['runId'],'timeoutMs':0,'consumeReminders':True,'reminderWaitId':'pause-'+harness}
                self.assertIn('pause', board.call('task_wait', wait))
                self.assertNotIn('pause', board.call('task_wait', {**wait,'reminderWaitId':'later-'+harness}))
                self.control(board, 'resume', target)
                second = self.claim(board, run_id=run['runId'])['claim']['turn']['input']
                self.assertEqual(second['resumeMode'], expected)
                self.assertIn('Continue the original task', second['context']['continuation']['input'])
                with board.store.db.read() as connection:
                    records = connection.execute('SELECT * FROM workflow_continuations WHERE run_id=?', (run['runId'],)).fetchall()
                    self.assertEqual(len(records), 1)
                    event = connection.execute("SELECT * FROM events WHERE task_id=? AND kind='workflow.paused'", (run['runId'],)).fetchone()
                    self.assertEqual(event['actor_role'], 'console')

    def test_host_continue_during_pause_is_recorded_then_starts_after_resume(self):
        board = self.board()
        run = self.submit(board, kind='worktree')
        self.finish_turn(board, self.claim(board), disposition='attention')
        target = 'run:' + run['runId']
        self.control(board, 'pause', target)
        waiting = board.call('workflow_get', {'runId':run['runId']})
        self.assertEqual(waiting['state'], 'awaiting-host')
        self.continue_run(board, waiting)
        self.assertEqual(board.call('workflow_get', {'runId':run['runId']})['queueReason'], 'paused')
        self.assertIsNone(self.claim(board, run_id=run['runId'])['claim'])
        self.control(board, 'resume', target)
        self.assertIsNotNone(self.claim(board, run_id=run['runId'])['claim'])

    def test_pause_does_not_cancel_queued_host_response(self):
        board = self.board()
        run = self.submit(board, kind='worktree')
        self.finish_turn(board, self.claim(board), disposition='attention')
        continued = self.continue_run(board, board.call('workflow_get', {'runId':run['runId']}))
        target = 'run:' + run['runId']
        self.control(board, 'pause', target)
        with board.store.db.read() as connection:
            self.assertEqual(connection.execute('SELECT state FROM workflow_continuations WHERE continuation_id=?',
                                                (continued['continuationId'],)).fetchone()[0], 'queued')
        self.control(board, 'resume', target)
        self.assertIsNotNone(self.claim(board, run_id=run['runId'])['claim'])

    def test_resume_before_stop_settlement_still_queues_the_interrupted_turn(self):
        board = self.board()
        run = self.submit(board, kind='worktree')
        first = self.claim(board)
        target = 'run:' + run['runId']
        self.control(board, 'pause', target)
        self.control(board, 'resume', target)
        self.assertIsNone(self.claim(board, run_id=run['runId'])['claim'])
        with board.store.db.write() as connection:
            connection.execute("DELETE FROM events WHERE kind='macro_task.paused'")
        self.pause_result(board, first)
        self.assertEqual(board.call('workflow_get', {'runId':run['runId']})['state'], 'executing')
        self.assertIsNotNone(self.claim(board, run_id=run['runId'])['claim'])

    def test_router_is_fenced_and_resume_routes_again(self):
        board = self.board()
        self.catalog_fixture()
        DecisionTestCase.use_helper(self)
        DecisionTestCase.seed(self, board)
        run = self.submit(board, request_id='routed', buddy='route',
                          create_macro={'title':'Routing pause','goal':'Route'})
        old = run['routing']['taskId']
        self.control(board, 'pause', run['macroTaskId'])
        self.assertEqual(board.call('task_get', {'runId':old})['task']['state'], 'cancelled')
        self.assertIsNone(self.claim(board, run_id=old)['claim'])
        self.control(board, 'resume', run['macroTaskId'])
        self.claim(board, run_id=run['runId'])
        current = board.call('workflow_get', {'runId':run['runId']})
        self.assertNotEqual(current['routing']['taskId'], old)
        self.assertEqual(current['state'], 'executing')

    def test_running_router_attempt_records_pause_then_end_purpose(self):
        board = self.board()
        self.catalog_fixture()
        DecisionTestCase.use_helper(self)
        DecisionTestCase.seed(self, board)
        run = self.submit(board, request_id='active-router-control', buddy='route',
                          create_macro={'title':'Router control','goal':'Work'})
        router_id = run['routing']['taskId']
        claim = self.claim(board, run_id=router_id)['claim']
        self.assertIsNotNone(claim)
        self.control(board, 'pause', run['macroTaskId'])
        self.assertFalse(board.call('workflow_get', {'macroTaskId':run['macroTaskId']})['paused']['shutdownConfirmed'])
        with board.store.db.read() as connection:
            attempt = board.store._attempt_row(connection, claim['attempt']['attemptId'])
            self.assertEqual((attempt['stop_initiator'],attempt['stop_purpose']), ('console','pause'))
        with patch.object(board.store.workflow, 'END_WAIT_SECONDS', 0):
            result = self.control(board, 'end', run['macroTaskId'])
        self.assertEqual(result, {'macroTaskId': run['macroTaskId'], 'duplicate': False})
        self.assertIsNone(board.call('workflow_get', {'runId':run['runId']})['hostConclusion'])
        with board.store.db.read() as connection:
            attempt = board.store._attempt_row(connection, claim['attempt']['attemptId'])
            self.assertEqual((attempt['stop_initiator'],attempt['stop_purpose']), ('console','end'))

    def test_end_concludes_queued_routing_work_and_stops_its_router(self):
        board = self.board()
        self.catalog_fixture()
        DecisionTestCase.use_helper(self)
        DecisionTestCase.seed(self, board)
        run = self.submit(board, request_id='routing-end', buddy='route',
                          create_macro={'title':'End routing','goal':'Route'})
        router_id = run['routing']['taskId']
        result = self.control(board, 'end', run['macroTaskId'])
        self.assertEqual(result, {'macroTaskId': run['macroTaskId'], 'duplicate': False})
        self.assertEqual(board.call('workflow_get', {'runId':run['runId']})['state'], 'concluded')
        self.assertEqual(board.call('task_get', {'runId':router_id})['task']['state'], 'cancelled')
        self.assertIsNone(self.claim(board, run_id=router_id)['claim'])

    def test_end_concludes_all_unfinished_states_and_preserves_delivered(self):
        board = self.board(max_concurrent=8)
        queued = self.submit(board, kind='worktree', request_id='queued', create_macro={'title':'End unit','goal':'Work'})
        runs = {'queued':queued}
        for state in ('awaiting-host','failed','cancelled','delivered','accepted'):
            run = self.submit(board, kind='worktree', request_id=state, macroTaskId=queued['macroTaskId'])
            runs[state] = run
            if state == 'cancelled':
                board.call('workflow_cancel', {'runId':run['runId']})
                continue
            self.finish_turn(board, self.claim(board, run_id=run['runId']),
                disposition='attention' if state == 'awaiting-host' else 'completed',
                runner_status='failed' if state == 'failed' else 'ok', seal=state != 'failed')
            if state == 'accepted':
                self.accept_run(board, board.call('workflow_get', {'runId':run['runId']}))
        before = {name:board.call('workflow_get', {'runId':run['runId']}) for name,run in runs.items()}
        counts = board.call('workflow_get', {'macroTaskId':queued['macroTaskId']})['counts']
        self.assertEqual(counts['unfinished'], 4)
        self.assertEqual(counts['delivered'], 1)
        for retired in ('stoppableRoots', 'stoppableHelpers', 'awaitingHost'):
            self.assertNotIn(retired, counts)
        result = self.control(board, 'end', queued['macroTaskId'])
        self.assertEqual(result, {'macroTaskId': queued['macroTaskId'], 'duplicate': False})
        for state, run in runs.items():
            view = board.call('workflow_get', {'runId':run['runId']})
            if state in ('delivered','accepted'):
                self.assertEqual(view['state'], state)
                self.assertEqual(view['task']['revision'], before[state]['task']['revision'])
            else:
                self.assertEqual(view['state'], 'concluded', view)
                self.assertEqual(view['hostConclusion']['actor']['role'], 'console')
                self.assertEqual(view['hostConclusion']['reason']['code'], 'macro-task-ended')
                if state in ('failed','cancelled'):
                    self.assertEqual(view['hostConclusion']['executionStatus'], state)
        replay = self.control(board, 'end', queued['macroTaskId'])
        self.assertEqual(replay, {'macroTaskId': queued['macroTaskId'], 'duplicate': True})

    def test_end_unknown_stop_records_no_conclusion_then_new_end_can_close_it(self):
        board = self.board()
        run = self.submit(board, kind='worktree')
        active = self.claim(board)
        target = 'run:' + run['runId']
        with patch.object(board.store.workflow, 'END_WAIT_SECONDS', 0):
            result = self.control(board, 'end', target)
        self.assertEqual(result, {'macroTaskId': target, 'duplicate': False})
        self.assertIsNone(board.call('workflow_get', {'runId':run['runId']})['hostConclusion'])
        self.assertFalse(board.call('workflow_get', {'runId':run['runId']})['shutdown']['selfConfirmed'])
        self.finish_turn(board, active, runner_status='cancelled', seal=False)
        result = self.control(board, 'end', target, 'confirmed-end')
        self.assertEqual(result, {'macroTaskId': target, 'duplicate': False})
        self.assertIsNotNone(board.call('workflow_get', {'runId':run['runId']})['hostConclusion'])

    def test_end_waits_for_real_settlement_before_concluding(self):
        board = self.board()
        run = self.submit(board, kind='worktree')
        claim = self.claim(board)
        def settle():
            with board.store._condition:
                board.store._condition.wait_for(lambda: self._end_recorded(board), timeout=5)
            self.finish_turn(board, claim, runner_status='cancelled', seal=False)
        thread = threading.Thread(target=settle)
        thread.start()
        try:
            result = self.control(board, 'end', 'run:' + run['runId'])
        finally:
            thread.join(timeout=10)
        self.assertFalse(thread.is_alive())
        self.assertEqual(result, {'macroTaskId': 'run:' + run['runId'], 'duplicate': False})
        self.assertEqual(board.call('workflow_get', {'runId':run['runId']})['state'], 'concluded')

    @staticmethod
    def _end_recorded(board):
        with board.store.db.read() as connection:
            return connection.execute("SELECT 1 FROM events WHERE kind='macro_task.ended'").fetchone() is not None

    def test_end_records_conclusion_even_if_checkout_reclaim_is_blocked(self):
        board = self.board()
        run = self.submit(board, kind='worktree')
        with patch.object(board.store.workflow, '_reclaim_checkout', side_effect=BoardError('STORAGE_BUSY','Fixture cleanup is busy')):
            result = self.control(board, 'end', 'run:'+run['runId'])
        self.assertEqual(result, {'macroTaskId': 'run:'+run['runId'], 'duplicate': False})
        self.assertEqual(board.call('workflow_get', {'runId':run['runId']})['state'], 'concluded')

    def test_new_end_can_settle_an_end_interrupted_before_its_receipt(self):
        board = self.board()
        run = self.submit(board, kind='worktree')
        target = 'run:'+run['runId']
        with patch.object(board.store.workflow, '_seal_conclusion', side_effect=RuntimeError('simulated service loss')):
            with self.assertRaises(RuntimeError):
                board.store.workflow.end_macro_task({'macroTaskId':target, 'commandId':'lost-end'},
                                                     console_authority={'sessionId':'browser'})
        result = self.control(board, 'end', target, 'recover-end')
        self.assertEqual(result, {'macroTaskId': target, 'duplicate': False})
        self.assertEqual(board.call('workflow_get', {'runId':run['runId']})['state'], 'concluded')
        self.control(board, 'pause', target, 'pause-closed-unit')

    def test_end_reports_unconcluded_when_host_continues_before_conclusion_commits(self):
        board = self.board()
        run = self.submit(board, kind='worktree')
        self.finish_turn(board, self.claim(board), disposition='attention')
        waiting = board.call('workflow_get', {'runId':run['runId']})
        params = {'runId':run['runId'], 'requestId':waiting['activeRequest']['requestId'], 'input':'Continue'}
        board.call('workflow_continue', params)
        seal = board.store.workflow._seal_conclusion
        continued = []
        def host_wins(run_id, work):
            sealed = seal(run_id, work)
            continued.append(board.call('workflow_continue', params))
            return sealed
        with patch.object(board.store.workflow, '_seal_conclusion', side_effect=host_wins):
            result = self.control(board, 'end', 'run:'+run['runId'])
        self.assertEqual(len(continued), 1)
        self.assertEqual(result, {'macroTaskId': 'run:'+run['runId'], 'duplicate': False})
        view = board.call('workflow_get', {'runId':run['runId']})
        self.assertEqual(view['state'], 'executing')
        self.assertIsNone(view['hostConclusion'])
        self.assertIsNotNone(self.claim(board, run_id=run['runId'])['claim'])

    def test_end_keeps_a_host_turn_that_returns_during_another_stop_wait(self):
        board = self.board(max_concurrent=4)
        first = self.submit(board, kind='worktree', create_macro={'title':'End race','goal':'Work'})
        self.finish_turn(board, self.claim(board), disposition='attention')
        waiting = board.call('workflow_get', {'runId':first['runId']})
        params = {'runId':first['runId'], 'requestId':waiting['activeRequest']['requestId'], 'input':'Continue'}
        board.call('workflow_continue', params)
        other = self.submit(board, kind='worktree', request_id='stop-wait', macroTaskId=first['macroTaskId'])
        active = self.claim(board, run_id=other['runId'])
        continued = []
        def host_finishes(_params):
            continued.append(board.call('workflow_continue', params))
            self.finish_turn(board, self.claim(board, run_id=first['runId']), disposition='attention')
            self.finish_turn(board, active, runner_status='cancelled', seal=False)
            return {}
        with patch.object(board.store, 'events_wait', side_effect=host_finishes):
            result = self.control(board, 'end', first['macroTaskId'])
        self.assertEqual(len(continued), 1)
        self.assertEqual(result, {'macroTaskId': first['macroTaskId'], 'duplicate': False})
        view = board.call('workflow_get', {'runId':first['runId']})
        self.assertEqual(view['state'], 'awaiting-host')
        self.assertIsNotNone(view['activeRequest'])
        self.assertIsNone(view['hostConclusion'])

    def test_retry_without_end_receipt_recomputes_unfinished_scope(self):
        board = self.board()
        first = self.submit(board, kind='worktree', create_macro={'title':'Retry end','goal':'Work'})
        second = self.submit(board, kind='worktree', request_id='second-end', macroTaskId=first['macroTaskId'])
        params = {'macroTaskId':first['macroTaskId'], 'commandId':'lost-end'}
        seal = board.store.workflow._seal_conclusion
        sealed_runs = []
        def stop_after_one(run_id, work):
            if sealed_runs:
                raise RuntimeError('service loss')
            sealed_runs.append(run_id)
            return seal(run_id, work)
        with patch.object(board.store.workflow, '_seal_conclusion', side_effect=stop_after_one):
            with self.assertRaises(RuntimeError):
                board.store.workflow.end_macro_task(params, console_authority={'sessionId':'browser'})
        self.assertEqual(board.call('workflow_get', {'runId':sealed_runs[0]})['state'], 'concluded')
        late = self.submit(board, kind='worktree', request_id='later-end', macroTaskId=first['macroTaskId'])
        result = self.control(board, 'end', first['macroTaskId'], 'lost-end')
        self.assertEqual(result, {'macroTaskId': first['macroTaskId'], 'duplicate': False})
        for run in (first, second, late):
            self.assertEqual(board.call('workflow_get', {'runId':run['runId']})['state'], 'concluded')

    def test_pause_keeps_host_boundary_and_blocks_helpers_in_the_standalone_tree(self):
        board = self.board(max_concurrent=4)
        run = self.submit(board, kind='worktree')
        self.finish_turn(board, self.claim(board), disposition='assistance')
        view = board.call('workflow_get', {'runId':run['runId']})
        approved = self.decide(board, view, view['activeRequest']['requestId'], autoContinue=False, helpers=[{
            'title':'helper','task':'Assist','cwd':str(self.workdir('pause-helper')),
            'workspace':{'kind':'worktree','access':'write'}}])
        helper = approved['children'][0]['taskId']
        before = board.call('workflow_get', {'runId':run['runId']})
        self.control(board, 'pause', 'run:'+run['runId'])
        self.assertIsNone(self.claim(board, run_id=helper)['claim'])
        self.assertEqual(board.call('workflow_get', {'runId':helper})['queueReason'], 'paused')
        current = board.call('workflow_get', {'runId':run['runId']})
        self.assertEqual(current['activeRequest'], before['activeRequest'])
        self.assertEqual(current['queueReason'], before['queueReason'])
        self.control(board, 'resume', 'run:'+run['runId'])
        self.assertIsNotNone(self.claim(board, run_id=helper)['claim'])

    def test_unfinished_counts_unconcluded_failed_and_cancelled_helpers(self):
        board = self.board(max_concurrent=4)
        run = self.submit(board, kind='worktree')
        self.finish_turn(board, self.claim(board), disposition='assistance')
        view = board.call('workflow_get', {'runId':run['runId']})
        approved = self.decide(board, view, view['activeRequest']['requestId'], autoContinue=False, helpers=[{
            'title':name, 'task':'Assist', 'cwd':str(self.workdir('end-helper-'+name)),
            'workspace':{'kind':'worktree','access':'write'}} for name in ('failed','cancelled','delivered')])
        children = dict(zip(('failed','cancelled','delivered'),
                            (child['taskId'] for child in approved['children'])))
        for name, child in children.items():
            self.finish_turn(board, self.claim(board, run_id=child),
                             runner_status='ok' if name == 'delivered' else name, seal=name == 'delivered')
        target = 'run:'+run['runId']
        counts = board.call('macro_task_timeline', {'macroTaskId':target})['macroTask']['counts']
        self.assertEqual(counts['unfinished'], 3)
        self.assertEqual(counts['delivered'], 1)
        ended = self.control(board, 'end', target)
        self.assertEqual(ended, {'macroTaskId': target, 'duplicate': False})
        for run_id in (run['runId'], children['failed'], children['cancelled']):
            self.assertEqual(board.call('workflow_get', {'runId':run_id})['state'], 'concluded')
        self.assertEqual(board.call('workflow_get', {'runId':children['delivered']})['state'], 'delivered')

    def test_end_preserves_delivered_owned_work_below_the_lead(self):
        board = self.board(max_concurrent=4)
        lead, claim = self.lead(board, create_macro={'title':'Lead end','goal':'Work'})
        delivered = self.owned(board, claim, 'delivered-owned')
        pending = self.owned(board, claim, 'pending-owned')
        failed = self.owned(board, claim, 'failed-owned')
        cancelled = self.owned(board, claim, 'cancelled-owned')
        self.finish_owned(board, delivered, worker='w2')
        self.finish_owned(board, failed, worker='w3', runner_status='failed', seal=False)
        self.finish_owned(board, cancelled, worker='w4', runner_status='cancelled', seal=False)
        self.finish_turn(board, {'claim':claim}, outcome={
            'disposition':'waiting','summary':'Wait for owned work','remaining':[], 'decisions':[], 'artifacts':[], 'request':None})
        counts = board.call('workflow_get', {'macroTaskId':lead['macroTaskId']})['counts']
        self.assertEqual(counts['unfinished'], 4)
        self.assertEqual(counts['delivered'], 1)
        result = self.control(board, 'end', lead['macroTaskId'])
        self.assertEqual(result, {'macroTaskId': lead['macroTaskId'], 'duplicate': False})
        self.assertEqual(board.call('workflow_get', {'runId':delivered['runId']})['state'], 'delivered')
        self.assertEqual(board.call('workflow_get', {'runId':pending['runId']})['state'], 'concluded')
        self.assertEqual(board.call('workflow_get', {'runId':failed['runId']})['state'], 'concluded')
        self.assertEqual(board.call('workflow_get', {'runId':cancelled['runId']})['state'], 'concluded')
        self.assertEqual(board.call('workflow_get', {'runId':lead['runId']})['state'], 'concluded')

    def test_recovery_without_receipt_keeps_pause_intent_and_queues_continuation(self):
        board = self.board()
        run = self.submit(board, kind='worktree')
        claim = self.claim(board)
        self.control(board, 'pause', 'run:'+run['runId'])
        with board.store.db.write() as connection:
            connection.execute("DELETE FROM events WHERE kind='macro_task.paused'")
        self.interrupted(board, claim)
        view = board.call('workflow_get', {'runId':run['runId']})
        self.assertEqual((view['state'],view['currentTurn']['state'],view['queueReason']), ('executing','interrupted','paused'))
        result = board.call('task_get', {'runId':run['runId']})['task']
        self.assertEqual(result['terminationReason'], 'pause')
        self.assertTrue(any(item['kind']=='partial-output' for item in view['artifacts']))

    def test_pause_release_before_process_creation_keeps_queued_work_and_ownership(self):
        board = self.board()
        run = self.submit(board, kind='worktree')
        claim = self.claim(board)
        self.control(board, 'pause', 'run:'+run['runId'])
        released = board.store.release_execution(claim['claim']['attempt']['attemptId'], reason='execution-process-not-created')
        self.assertEqual(released['task']['state'], 'queued')
        with board.store.db.read() as connection:
            self.assertEqual(connection.execute("SELECT state FROM workspace_reservations WHERE holder_task_id=? ORDER BY rowid DESC", (run['runId'],)).fetchone()[0], 'held')
        self.control(board, 'resume', 'run:'+run['runId'])
        self.assertIsNotNone(self.claim(board, run_id=run['runId'])['claim'])

    def test_pause_interrupts_an_existing_host_stop_without_reviving_cancelled_work(self):
        board = self.board()
        run = self.submit(board, kind='worktree')
        claim = self.claim(board)
        board.call('workflow_cancel', {'runId':run['runId']})
        self.control(board, 'pause', 'run:'+run['runId'])
        scheduler = ExecutionScheduler(board.service, live_registry=board.service.live_registry)
        view = board.call('task_get', {'runId':run['runId']})['task']
        intent, _ = scheduler._stop_intent(view, SimpleNamespace(budget_path=self.directory/'no-budget'))
        self.assertEqual(intent[0], 'pause')
        self.pause_result(board, claim)
        self.assertEqual(board.call('workflow_get', {'runId':run['runId']})['state'], 'cancelled')
        with board.store.db.read() as connection:
            self.assertEqual(connection.execute('SELECT COUNT(*) FROM workflow_continuations WHERE run_id=?', (run['runId'],)).fetchone()[0], 0)

    def test_scheduler_reads_persisted_pause_after_resume_and_forces_at_the_grace_deadline(self):
        board = self.board()
        run = self.submit(board, kind='worktree')
        claim = self.claim(board)
        target = 'run:'+run['runId']
        self.control(board, 'pause', target)
        self.control(board, 'resume', target)
        with board.store.db.write() as connection:
            connection.execute("DELETE FROM events WHERE kind='macro_task.paused'")
            attempt = board.store._attempt_row(connection, claim['claim']['attempt']['attemptId'])
            self.assertEqual(attempt['stop_purpose'], 'pause')
        scheduler = ExecutionScheduler(board.service, live_registry=board.service.live_registry)
        view = board.call('task_get', {'runId':run['runId']})['task']
        handle = SimpleNamespace(budget_path=self.directory/'no-budget')
        intent, _ = scheduler._stop_intent(view, handle)
        self.assertEqual((intent[0],intent[2]), ('pause', False))
        with patch.object(scheduler, '_deadline', return_value=0):
            intent, _ = scheduler._stop_intent(view, handle)
        self.assertEqual((intent[0],intent[2],intent[3]), ('pause', True, 'pause'))

    def test_end_overrides_pause_at_the_same_clock_instant(self):
        board = self.board()
        run = self.submit(board, kind='worktree')
        claim = self.claim(board)
        target = 'run:'+run['runId']
        instant = board.store.now()
        with patch.object(board.store.workflow, 'now', return_value=instant):
            self.control(board, 'pause', target)
            with patch.object(board.store.workflow, 'END_WAIT_SECONDS', 0):
                result = self.control(board, 'end', target)
        self.assertEqual(result, {'macroTaskId': target, 'duplicate': False})
        self.assertIsNone(board.call('workflow_get', {'runId':run['runId']})['hostConclusion'])
        with board.store.db.read() as connection:
            attempt = board.store._attempt_row(connection, claim['claim']['attempt']['attemptId'])
            self.assertEqual(attempt['cancel_requested_at'], instant)
            self.assertEqual(attempt['stop_purpose'], 'end')
        scheduler = ExecutionScheduler(board.service, live_registry=board.service.live_registry)
        view = board.call('task_get', {'runId':run['runId']})['task']
        intent, _ = scheduler._stop_intent(view, SimpleNamespace(budget_path=self.directory/'no-budget'))
        self.assertEqual((intent[0],intent[2],intent[3]), ('console',True,'console-stop'))
        self.interrupted(board, claim)
        view = board.call('workflow_get', {'runId':run['runId']})
        self.assertEqual(view['state'], 'cancelled')
        self.assertEqual(view['task']['terminationReason'], 'console-stop')
        with board.store.db.read() as connection:
            self.assertEqual(connection.execute('SELECT COUNT(*) FROM workflow_continuations WHERE run_id=?',
                                               (run['runId'],)).fetchone()[0], 0)

    def test_controls_require_console_authority_and_receipts_reject_changed_requests(self):
        board = self.board()
        run = self.submit(board, kind='worktree')
        target = 'run:' + run['runId']
        for action in ('pause','resume','end'):
            with self.assertRaises(BoardError) as caught:
                board.call('macro_task_'+action, {'macroTaskId':target,'commandId':action})
            self.assertEqual(caught.exception.code, 'UNAUTHORIZED')
        first = self.control(board, 'pause', target, 'same')
        self.assertTrue(self.control(board, 'pause', target, 'same')['duplicate'])
        self.assertEqual(first, {'macroTaskId': target, 'duplicate': False})
        resumed = self.control(board, 'resume', target, 'resume-once')
        self.assertEqual(resumed, {'macroTaskId': target, 'duplicate': False})
        self.assertEqual(self.control(board, 'resume', target, 'resume-once'),
                         {'macroTaskId': target, 'duplicate': True})
        browser = self._control_browser
        self.assertEqual(browser.command('macro_task_pause', {'macroTaskId':target,'commandId':'same','note':'changed'},
                                        csrf=browser.bootstrap()['csrfToken'])[0], 409)
        self.assertEqual(browser.command('macro_task_stop', {'macroTaskId':target,'commandId':'retired'},
                                        csrf=browser.bootstrap()['csrfToken'])[0], 404)
