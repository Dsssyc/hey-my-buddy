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
            self.assertEqual(board.call('workflow_get', {'runId': run_id})['state'], 'cancelled')
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
        self.assertIn("'console','" + snapshot['consoleSession']['id'] + "'", dump)
        self.assertNotIn(bearer,(board.directory/'console-sessions.json').read_text())

    def test_standalone_stop_preserves_unknown_shutdown(self):
        board = self.board()
        run = self.submit(board, kind='worktree')
        self.register(board)
        claim = self.claim(board)
        _, browser = self.open_console(board)
        status, _, raw = browser.command('macro_task_stop', {'macroTaskId': 'run:' + run['runId'], 'commandId': 'standalone-stop'}, csrf=browser.bootstrap()['csrfToken'])
        self.assertEqual(status, 200, raw)
        task = board.call('task_get', {'runId': run['runId']})['task']
        self.assertEqual(task['status'], 'cancelling')
        timeline = board.call('macro_task_timeline', {'macroTaskId': 'run:' + run['runId']})
        self.assertFalse(timeline['rows'][0]['shutdownConfirmed'])
        self.finish_turn(board, claim, runner_status='cancelled', seal=False)
        stopped = board.call('workflow_get', {'runId': run['runId']})
        self.assertEqual(stopped['state'], 'cancelled')
        self.assertIsNone(stopped['hostConclusion'])
        closed = board.call('workflow_conclude', {'runId': run['runId'],
            'note': 'Host ends the stopped micro task', **self.control(stopped)})
        self.assertEqual(closed['state'], 'concluded')

    def test_concluded_roots_are_skipped_and_reported_separately(self):
        board = self.board()
        closed = self.submit(board, kind='worktree', macroTask={'title': 'Group'})
        cancelled = board.call('workflow_cancel', {'runId': closed['runId'], **self.control(closed)})
        closed = board.call('workflow_conclude', {'runId': closed['runId'],
            'note': 'Already closed by Host', **self.control(cancelled)})
        pending = self.submit(board, kind='worktree', request_id='pending', macroTaskId=closed['macroTaskId'])
        _, browser = self.open_console(board)
        params = {'macroTaskId': closed['macroTaskId'], 'commandId': 'stop-around-concluded'}
        status, _, raw = browser.command('macro_task_stop', params, csrf=browser.bootstrap()['csrfToken'])
        self.assertEqual(status, 200, raw)
        result = json.loads(raw)['result']
        self.assertEqual(set(result), {'macroTaskId', 'runIds', 'acceptedRunIds', 'concludedRunIds', 'results', 'duplicate'})
        self.assertEqual(result['runIds'], [pending['runId']])
        self.assertEqual(result['acceptedRunIds'], [])
        self.assertEqual(result['concludedRunIds'], [closed['runId']])
        current = board.call('workflow_get', {'runId': closed['runId']})
        self.assertEqual(current['revision'], closed['revision'])
        self.assertEqual(current['hostConclusion'], closed['hostConclusion'])
        self.assertEqual(board.call('workflow_get', {'runId': pending['runId']})['state'], 'cancelled')
        status, _, raw = browser.command('macro_task_stop', params, csrf=browser.bootstrap()['csrfToken'])
        self.assertEqual(status, 200, raw)
        self.assertTrue(json.loads(raw)['result']['duplicate'])
        self.assertEqual(json.loads(raw)['result']['concludedRunIds'], [closed['runId']])

    def test_stop_cancels_delivered_and_failed_roots_without_a_conclusion(self):
        board = self.board()
        self.register(board)
        for source in ('delivered', 'failed'):
            with self.subTest(source=source):
                run = self.submit(board, kind='worktree', request_id='stop-' + source)
                self.finish_turn(board, self.claim(board, claim_request_id='claim-' + source),
                    runner_status='failed' if source == 'failed' else 'ok',
                    exit_code=1 if source == 'failed' else 0, seal=source != 'failed')
                before = board.call('workflow_get', {'runId': run['runId']})
                self.assertEqual(before['state'], source)
                _, browser = self.open_console(board)
                status, _, raw = browser.command('macro_task_stop',
                    {'macroTaskId': 'run:' + run['runId'], 'commandId': 'stop-' + source},
                    csrf=browser.bootstrap()['csrfToken'])
                self.assertEqual(status, 200, raw)
                current = board.call('workflow_get', {'runId': run['runId']})
                self.assertEqual(current['state'], 'cancelled')
                self.assertIsNone(current['hostConclusion'])
        with board.store.db.read() as connection:
            self.assertEqual(connection.execute('SELECT COUNT(*) FROM workflow_host_conclusions').fetchone()[0], 0)

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
        self.claim(board, claim_request_id='helper-claim', run_id=helper)
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
