"""Real console cancellation, fixed replay scope and unchanged CLI authority."""
import json
from unittest.mock import patch

from hey_my_buddy.errors import BoardError
from console.test_console import ConsoleTestCase
from blackboard.tasks.test_workflow import WorkflowTestCase


class MacroTaskStopTests(WorkflowTestCase, ConsoleTestCase):
    def test_stop_entire_group_and_replay_without_expanding_scope(self):
        board = self.board()
        first = self.submit(board, kind='worktree', macroTask={'title': 'Group'})
        second = self.submit(board, kind='worktree', request_id='second', macroTaskId=first['macroTaskId'])
        unrelated = self.submit(board, kind='worktree', request_id='other')
        _, browser = self.open_console(board)
        params = {'macroTaskId': first['macroTaskId'], 'commandId': 'stop-group'}
        status, _, raw = browser.command('macro_task_stop', params, csrf=browser.bootstrap()['csrfToken'])
        self.assertEqual(status, 200, raw)
        result = json.loads(raw)['result']
        self.assertEqual(set(result['runIds']), {first['runId'], second['runId']})
        for run_id in result['runIds']:
            view = board.call('workflow_get', {'runId': run_id})
            self.assertEqual(view['state'], 'concluded')
            self.assertEqual(view['hostConclusion']['executionStatus'], 'cancelled')
        self.assertEqual(board.call('workflow_get', {'runId': unrelated['runId']})['state'], 'executing')
        later = self.submit(board, kind='worktree', request_id='later', macroTaskId=first['macroTaskId'])
        status, _, raw = browser.command('macro_task_stop', params, csrf=browser.bootstrap()['csrfToken'])
        self.assertEqual(status, 200, raw)
        self.assertTrue(json.loads(raw)['result']['duplicate'])
        self.assertEqual(board.call('workflow_get', {'runId': later['runId']})['state'], 'executing')
        self.assertEqual(browser.command('macro_task_stop', {**params, 'reason': 'changed'}, csrf=browser.bootstrap()['csrfToken'])[0], 409)

    def test_atomic_failure_and_console_authority(self):
        board = self.board()
        first = self.submit(board, kind='worktree', macroTask={'title': 'Group'})
        second = self.submit(board, kind='worktree', request_id='second', macroTaskId=first['macroTaskId'])
        params = {'macroTaskId': first['macroTaskId'], 'commandId': 'atomic-stop'}
        with self.assertRaises(BoardError) as denied:
            board.call('macro_task_stop', params)
        self.assertEqual(denied.exception.code, 'UNAUTHORIZED')
        _, browser = self.open_console(board)
        cancel = board.store.workflow._cancel_in_transaction
        calls = []
        def fail_second(*args, **kwargs):
            calls.append(1)
            if len(calls) == 2:
                raise BoardError('CONFLICT', 'injected second root failure')
            return cancel(*args, **kwargs)
        with patch.object(board.store.workflow, '_cancel_in_transaction', side_effect=fail_second):
            self.assertEqual(browser.command('macro_task_stop', params, csrf=browser.bootstrap()['csrfToken'])[0], 409)
        for run in (first, second):
            self.assertEqual(board.call('workflow_get', {'runId': run['runId']})['state'], 'executing')
        _, new_browser = self.open_console(board)
        self.assertEqual(browser.command('macro_task_stop', params, csrf=browser.bootstrap()['csrfToken'])[0], 200)
        self.assertEqual(new_browser.command('macro_task_stop', params, csrf=new_browser.bootstrap()['csrfToken'])[0], 200)

    def test_console_audit_keeps_public_identity_but_never_the_bearer(self):
        board=self.board()
        run=self.submit(board,kind='worktree')
        _,browser=self.open_console(board)
        snapshot=browser.bootstrap()
        params={'macroTaskId':'run:'+run['runId'],'commandId':'private-audit-stop'}
        with self.assertRaises(BoardError) as denied:
            board.call('macro_task_stop',{**params,'consoleAuthority':{'sessionId':snapshot['consoleSession']['id']}})
        self.assertEqual(denied.exception.code,'UNAUTHORIZED')
        self.assertEqual(browser.command('macro_task_stop',params,csrf=snapshot['csrfToken'])[0],200)
        bearer=browser.cookie.split('=',1)[1]
        with board.store.db.connect() as connection:
            dump='\n'.join(connection.iterdump())
        self.assertNotIn(bearer,dump)
        self.assertIn('console:'+snapshot['consoleSession']['id'],dump)
        self.assertNotIn(bearer,(board.directory/'console-sessions.json').read_text())

    def test_standalone_stop_preserves_unknown_shutdown(self):
        board = self.board()
        run = self.submit(board, kind='worktree')
        self.register(board)
        self.claim(board)
        _, browser = self.open_console(board)
        status, _, raw = browser.command('macro_task_stop', {'macroTaskId': 'run:' + run['runId'], 'commandId': 'standalone-stop'}, csrf=browser.bootstrap()['csrfToken'])
        self.assertEqual(status, 200, raw)
        task = board.call('task_get', {'runId': run['runId']})['task']
        self.assertEqual(task['status'], 'cancelling')
        self.assertEqual(board.call('workflow_get', {'runId': run['runId']})['state'], 'cancelled')
        timeline = board.call('macro_task_timeline', {'macroTaskId': 'run:' + run['runId']})
        self.assertFalse(timeline['rows'][0]['shutdownConfirmed'])

    def test_stopped_execution_results_keep_their_status_in_conclusion(self):
        for source in ('delivered', 'failed', 'cancelled'):
            with self.subTest(source=source):
                board = self.board()
                run = self.submit(board, kind='worktree', request_id='stop-' + source,
                                  macroTask={'title': 'Group ' + source})
                if source == 'cancelled':
                    board.call('workflow_cancel', {'runId': run['runId'], **self.control(run)})
                else:
                    self.register(board)
                    claim = self.claim(board, claim_request_id='claim-' + source)
                    self.finish_turn(board, claim, runner_status='failed' if source == 'failed' else 'ok',
                                     exit_code=1 if source == 'failed' else 0, seal=source != 'failed')
                before = board.call('workflow_get', {'runId': run['runId']})
                self.assertEqual(before['state'], source)
                _, browser = self.open_console(board)
                status, _, raw = browser.command('macro_task_stop',
                    {'macroTaskId': run['macroTaskId'], 'commandId': 'stop-' + source},
                    csrf=browser.bootstrap()['csrfToken'])
                self.assertEqual(status, 200, raw)
                after = board.call('workflow_get', {'runId': run['runId']})
                self.assertEqual(after['state'], 'concluded')
                self.assertEqual(after['hostConclusion']['executionStatus'], source)
                self.assertEqual(after['finalArtifactId'], before['finalArtifactId'])
                status, _, raw = browser.command('macro_task_stop',
                    {'macroTaskId': run['macroTaskId'], 'commandId': 'stop-closed-' + source},
                    csrf=browser.bootstrap()['csrfToken'])
                self.assertEqual(status, 200, raw)
                self.assertEqual(json.loads(raw)['result']['acceptedRunIds'], [])
                self.assertEqual(json.loads(raw)['result']['concludedRunIds'], [run['runId']])

    def test_active_root_concludes_after_confirmed_worker_result(self):
        board = self.board()
        run = self.submit(board, kind='worktree')
        self.register(board)
        claim = self.claim(board)
        _, browser = self.open_console(board)
        status, _, raw = browser.command('macro_task_stop',
            {'macroTaskId': 'run:' + run['runId'], 'commandId': 'active-stop'},
            csrf=browser.bootstrap()['csrfToken'])
        self.assertEqual(status, 200, raw)
        self.assertEqual(board.call('workflow_get', {'runId': run['runId']})['state'], 'cancelled')
        self.finish_turn(board, claim, runner_status='cancelled', seal=False)
        after = board.call('workflow_get', {'runId': run['runId']})
        self.assertEqual(after['state'], 'concluded')
        self.assertEqual(after['hostConclusion']['executionStatus'], 'cancelled')

    def test_continuation_supersedes_stop_before_delayed_conclusion(self):
        board = self.board()
        run = self.submit(board, kind='worktree')
        self.register(board)
        claim = self.claim(board)
        _, browser = self.open_console(board)
        params = {'macroTaskId': 'run:' + run['runId'], 'commandId': 'stop-before-continue'}
        self.assertEqual(browser.command('macro_task_stop', params, csrf=browser.bootstrap()['csrfToken'])[0], 200)
        with patch.object(board.store.workflow, 'finalize_macro_stops'):
            self.finish_turn(board, claim, runner_status='cancelled', seal=False)
        current = board.call('workflow_get', {'runId': run['runId']})
        self.continue_run(board, current, command_id='continue-after-stop')
        status, _, raw = browser.command('macro_task_stop', params, csrf=browser.bootstrap()['csrfToken'])
        self.assertEqual(status, 200, raw)
        self.assertEqual(json.loads(raw)['result']['conclusions'][0]['state'], 'superseded')
        self.assertNotEqual(board.call('workflow_get', {'runId': run['runId']})['state'], 'concluded')

    def test_takeover_fences_old_stop_conclusion(self):
        board = self.board()
        run = self.submit(board, kind='worktree')
        self.register(board)
        claim = self.claim(board)
        _, browser = self.open_console(board)
        params = {'macroTaskId': 'run:' + run['runId'], 'commandId': 'stop-before-takeover'}
        self.assertEqual(browser.command('macro_task_stop', params, csrf=browser.bootstrap()['csrfToken'])[0], 200)
        current = board.call('workflow_get', {'runId': run['runId']})
        board.call('workflow_takeover', {'runId': run['runId'], 'commandId': 'takeover-after-stop',
            'expectedOwnerGeneration': current['ownerGeneration'], 'newHostId': 'host-2', **self.control(current)})
        self.finish_turn(board, claim, runner_status='cancelled', seal=False)
        status, _, raw = browser.command('macro_task_stop', params, csrf=browser.bootstrap()['csrfToken'])
        self.assertEqual(status, 200, raw)
        self.assertEqual(json.loads(raw)['result']['conclusions'][0]['state'], 'superseded')
        self.assertEqual(board.call('workflow_get', {'runId': run['runId']})['state'], 'cancelled')

    def test_helpers_are_cancelled_and_accepted_roots_are_retained(self):
        board = self.board()
        self.register(board)
        accepted = self.submit(board, kind='worktree', macroTask={'title': 'Group'})
        self.finish_turn(board, self.claim(board))
        view = board.call('workflow_get', {'runId': accepted['runId']})
        board.call('workflow_accept', {
            'runId': view['runId'], 'artifactId': view['finalArtifactId'],
            'note': 'Checked fixed fixture artifact', 'notRequired': 'Fixture has no changes', **self.control(view),
        })
        pending = self.submit(board, kind='worktree', request_id='pending', macroTaskId=accepted['macroTaskId'])
        self.finish_turn(board, self.claim(board, claim_request_id='parent-claim'), disposition='assistance')
        view = board.call('workflow_get', {'runId': pending['runId']})
        approved = self.decide(board, view, view['activeRequest']['requestId'], helpers=[{
            'requestId': 'helper', 'task': 'Assist', 'cwd': str(self.workdir('helper')),
            'executionWorkspace': {'kind': 'worktree', 'access': 'write'},
        }])
        helper = approved['children'][0]['taskId']
        helper_claim = self.claim(board, claim_request_id='helper-claim', run_id=helper)
        _, browser = self.open_console(board)
        status, _, raw = browser.command('macro_task_stop', {'macroTaskId': accepted['macroTaskId'], 'commandId': 'stop-with-helper'}, csrf=browser.bootstrap()['csrfToken'])
        self.assertEqual(status, 200, raw)
        self.assertEqual(json.loads(raw)['result']['acceptedRunIds'], [accepted['runId']])
        self.assertEqual(board.call('task_get', {'runId': helper})['task']['status'], 'cancelling')
        self.assertEqual(board.call('workflow_get', {'runId': accepted['runId']})['state'], 'accepted')
        group = board.call('macro_task_timeline', {'macroTaskId': accepted['macroTaskId']})['macroTask']
        self.assertEqual(group['counts']['accepted'], 1)
        self.assertEqual(group['counts']['roots'], 2)
        self.assertEqual(group['counts']['helpers'], 1)
        self.assertEqual(board.call('workflow_get', {'runId': pending['runId']})['state'], 'cancelled')
        self.finish_turn(board, helper_claim, runner_status='cancelled', seal=False)
        for run_id in (helper, pending['runId']):
            view = board.call('workflow_get', {'runId': run_id})
            self.assertEqual(view['state'], 'concluded')
            self.assertEqual(view['hostConclusion']['executionStatus'], 'cancelled')
