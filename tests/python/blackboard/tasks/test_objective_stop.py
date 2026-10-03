"""Real console cancellation, fixed replay scope and unchanged CLI authority."""
import json
from unittest.mock import patch

from hey_my_buddy.errors import BoardError
from console.test_console import ConsoleTestCase
from blackboard.tasks.test_workflow import WorkflowTestCase


class ObjectiveStopTests(WorkflowTestCase, ConsoleTestCase):
    def test_stop_entire_group_and_replay_without_expanding_scope(self):
        board = self.board()
        first = self.submit(board, kind='worktree', objective={'title': 'Group'})
        second = self.submit(board, kind='worktree', request_id='second', objectiveId=first['objectiveId'])
        unrelated = self.submit(board, kind='worktree', request_id='other')
        _, browser = self.open_console(board)
        params = {'objectiveId': first['objectiveId'], 'commandId': 'stop-group'}
        status, _, raw = browser.command('objective_stop', params, csrf=browser.bootstrap()['csrfToken'])
        self.assertEqual(status, 200, raw)
        result = json.loads(raw)['result']
        self.assertEqual(set(result['runIds']), {first['runId'], second['runId']})
        for run_id in result['runIds']:
            self.assertEqual(board.call('workflow_get', {'runId': run_id})['state'], 'cancelled')
        self.assertEqual(board.call('workflow_get', {'runId': unrelated['runId']})['state'], 'executing')
        later = self.submit(board, kind='worktree', request_id='later', objectiveId=first['objectiveId'])
        status, _, raw = browser.command('objective_stop', params, csrf=browser.bootstrap()['csrfToken'])
        self.assertEqual(status, 200, raw)
        self.assertTrue(json.loads(raw)['result']['duplicate'])
        self.assertEqual(board.call('workflow_get', {'runId': later['runId']})['state'], 'executing')
        self.assertEqual(browser.command('objective_stop', {**params, 'reason': 'changed'}, csrf=browser.bootstrap()['csrfToken'])[0], 409)

    def test_atomic_failure_and_console_authority(self):
        board = self.board()
        first = self.submit(board, kind='worktree', objective={'title': 'Group'})
        second = self.submit(board, kind='worktree', request_id='second', objectiveId=first['objectiveId'])
        params = {'objectiveId': first['objectiveId'], 'commandId': 'atomic-stop'}
        with self.assertRaises(BoardError) as denied:
            board.call('objective_stop', params)
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
            self.assertEqual(browser.command('objective_stop', params, csrf=browser.bootstrap()['csrfToken'])[0], 409)
        for run in (first, second):
            self.assertEqual(board.call('workflow_get', {'runId': run['runId']})['state'], 'executing')
        _, new_browser = self.open_console(board)
        self.assertEqual(browser.command('objective_stop', params, csrf=browser.bootstrap()['csrfToken'])[0], 200)
        self.assertEqual(new_browser.command('objective_stop', params, csrf=new_browser.bootstrap()['csrfToken'])[0], 200)

    def test_console_audit_keeps_public_identity_but_never_the_bearer(self):
        board=self.board()
        run=self.submit(board,kind='worktree')
        _,browser=self.open_console(board)
        snapshot=browser.bootstrap()
        params={'objectiveId':'run:'+run['runId'],'commandId':'private-audit-stop'}
        with self.assertRaises(BoardError) as denied:
            board.call('objective_stop',{**params,'consoleAuthority':{'sessionId':snapshot['consoleSession']['id']}})
        self.assertEqual(denied.exception.code,'UNAUTHORIZED')
        self.assertEqual(browser.command('objective_stop',params,csrf=snapshot['csrfToken'])[0],200)
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
        status, _, raw = browser.command('objective_stop', {'objectiveId': 'run:' + run['runId'], 'commandId': 'standalone-stop'}, csrf=browser.bootstrap()['csrfToken'])
        self.assertEqual(status, 200, raw)
        task = board.call('task_get', {'runId': run['runId']})['task']
        self.assertEqual(task['status'], 'cancelling')
        timeline = board.call('objective_timeline', {'objectiveId': 'run:' + run['runId']})
        self.assertFalse(timeline['rows'][0]['shutdownConfirmed'])

    def test_helpers_are_cancelled_and_accepted_roots_are_retained(self):
        board = self.board()
        self.register(board)
        accepted = self.submit(board, kind='worktree', objective={'title': 'Group'})
        self.finish_turn(board, self.claim(board))
        view = board.call('workflow_get', {'runId': accepted['runId']})
        board.call('workflow_accept', {
            'runId': view['runId'], 'artifactId': view['finalArtifactId'],
            'note': 'Checked fixed fixture artifact', 'notRequired': 'Fixture has no changes', **self.control(view),
        })
        pending = self.submit(board, kind='worktree', request_id='pending', objectiveId=accepted['objectiveId'])
        self.finish_turn(board, self.claim(board, claim_request_id='parent-claim'), disposition='assistance')
        view = board.call('workflow_get', {'runId': pending['runId']})
        approved = self.decide(board, view, view['activeRequest']['requestId'], helpers=[{
            'requestId': 'helper', 'task': 'Assist', 'cwd': str(self.workdir('helper')),
            'executionWorkspace': {'kind': 'worktree', 'access': 'write'},
        }])
        helper = approved['children'][0]['taskId']
        self.claim(board, claim_request_id='helper-claim', run_id=helper)
        _, browser = self.open_console(board)
        status, _, raw = browser.command('objective_stop', {'objectiveId': accepted['objectiveId'], 'commandId': 'stop-with-helper'}, csrf=browser.bootstrap()['csrfToken'])
        self.assertEqual(status, 200, raw)
        self.assertEqual(json.loads(raw)['result']['acceptedRunIds'], [accepted['runId']])
        self.assertEqual(board.call('task_get', {'runId': helper})['task']['status'], 'cancelling')
        self.assertEqual(board.call('workflow_get', {'runId': accepted['runId']})['state'], 'accepted')
        group = board.call('objective_timeline', {'objectiveId': accepted['objectiveId']})['objective']
        self.assertEqual(group['counts']['accepted'], 1)
        self.assertEqual(group['counts']['roots'], 2)
        self.assertEqual(group['counts']['helpers'], 1)
