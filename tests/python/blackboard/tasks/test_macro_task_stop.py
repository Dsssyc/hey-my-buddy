"""Real console cancellation, fixed replay scope and unchanged CLI authority."""
import json
from unittest.mock import patch

from hey_my_buddy.errors import BoardError
from console.test_console import ConsoleTestCase
from blackboard.tasks.test_workflow import WorkflowTestCase
from blackboard.tasks import test_lead_owned as lead_helpers
from blackboard.routing.test_decision import DecisionTestCase
from hey_my_buddy.cli import cli_views


class MacroTaskStopTests(WorkflowTestCase, ConsoleTestCase):
    lead = lead_helpers.LeadOwnedTests.lead
    owned = lead_helpers.LeadOwnedTests.owned
    finish_owned = lead_helpers.LeadOwnedTests.finish_owned

    def stop(self, browser, macro_task_id, command_id, *, note=None):
        params = {'macroTaskId': macro_task_id, 'commandId': command_id}
        if note is not None:
            params['note'] = note
        status, _, raw = browser.command('macro_task_stop', params, csrf=browser.bootstrap()['csrfToken'])
        self.assertEqual(status, 200, raw)
        result = json.loads(raw)['result']
        self.assertEqual(set(result), {'macroTaskId', 'results', 'duplicate'})
        self.assertEqual(result['macroTaskId'], macro_task_id)
        for entry in result['results']:
            self.assertEqual(set(entry), {'runId', 'state', 'stopped'})
        return result

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
        self.assertEqual(set(result), {'macroTaskId', 'results', 'duplicate'})
        self.assertEqual({entry['runId'] for entry in result['results']}, {first['runId'], second['runId']})
        self.assertTrue(all(entry['state'] == 'cancelled' and entry['stopped'] for entry in result['results']))
        for run_id in (first['runId'], second['runId']):
            self.assertEqual(board.call('workflow_get', {'runId': run_id})['state'], 'cancelled')
        self.assertEqual(board.call('workflow_get', {'runId': unrelated['runId']})['state'], 'executing')
        later = self.submit(board, kind='worktree', request_id='later', macroTaskId=first['macroTaskId'])
        status, _, raw = browser.command('macro_task_stop', params, csrf=browser.bootstrap()['csrfToken'])
        self.assertEqual(status, 200, raw)
        replay = json.loads(raw)['result']
        self.assertTrue(replay['duplicate'])
        self.assertEqual({**replay, 'duplicate': False}, result)
        self.assertEqual(board.call('workflow_get', {'runId': later['runId']})['state'], 'executing')
        self.assertEqual(browser.command('macro_task_stop', {**params, 'note': 'changed'}, csrf=browser.bootstrap()['csrfToken'])[0], 409)

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

    def test_concluded_roots_are_preserved_and_reported_in_results(self):
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
        self.assertEqual(set(result), {'macroTaskId', 'results', 'duplicate'})
        self.assertEqual({entry['runId']: (entry['state'], entry['stopped']) for entry in result['results']}, {
            closed['runId']: ('concluded', False), pending['runId']: ('cancelled', True),
        })
        current = board.call('workflow_get', {'runId': closed['runId']})
        self.assertEqual(current['revision'], closed['revision'])
        self.assertEqual(current['hostConclusion'], closed['hostConclusion'])
        self.assertEqual(board.call('workflow_get', {'runId': pending['runId']})['state'], 'cancelled')
        status, _, raw = browser.command('macro_task_stop', params, csrf=browser.bootstrap()['csrfToken'])
        self.assertEqual(status, 200, raw)
        self.assertTrue(json.loads(raw)['result']['duplicate'])
        self.assertEqual(json.loads(raw)['result']['results'], result['results'])

    def test_stop_preserves_delivered_and_failed_roots_without_a_conclusion(self):
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
                self.assertEqual(current['state'], source)
                self.assertEqual(json.loads(raw)['result']['results'], [
                    {'runId': run['runId'], 'state': source, 'stopped': False},
                ])
                self.assertIsNone(current['hostConclusion'])
                counts = board.call('macro_task_timeline', {'macroTaskId': 'run:' + run['runId']})['macroTask']['counts']
                self.assertEqual((counts['stoppableRoots'], counts['stoppableHelpers']), (0, 0))
                self.assertEqual(counts['delivered'], int(source == 'delivered'))
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
        self.assertEqual({entry['runId']: (entry['state'], entry['stopped']) for entry in json.loads(raw)['result']['results']}, {
            accepted['runId']: ('accepted', False), pending['runId']: ('cancelled', True),
            helper: ('cancelled', True),
        })
        self.assertEqual(board.call('task_get', {'runId': helper})['task']['status'], 'cancelling')
        self.assertEqual(board.call('workflow_get', {'runId': accepted['runId']})['state'], 'accepted')
        group = board.call('macro_task_timeline', {'macroTaskId': accepted['macroTaskId']})['macroTask']
        self.assertEqual(group['counts']['accepted'], 1)
        self.assertEqual(group['counts']['roots'], 2)
        self.assertEqual(group['counts']['helpers'], 1)
        self.assertEqual((group['counts']['stoppableRoots'], group['counts']['stoppableHelpers']), (0, 0))

    def test_queued_running_awaiting_host_and_cancelled_roots(self):
        board = self.board(max_concurrent=4)
        queued = self.submit(board, kind='worktree', request_id='queued', macroTask={'title': 'State scope'})
        running = self.submit(board, kind='worktree', request_id='running', macroTaskId=queued['macroTaskId'])
        awaiting = self.submit(board, kind='worktree', request_id='awaiting', macroTaskId=queued['macroTaskId'])
        already_cancelled = self.submit(board, kind='worktree', request_id='cancelled', macroTaskId=queued['macroTaskId'])
        self.register(board)
        self.register(board, 'w2')
        running_claim = self.claim(board, claim_request_id='running-claim', run_id=running['runId'])
        self.finish_turn(board, self.claim(board, 'w2', claim_request_id='awaiting-claim', run_id=awaiting['runId']),
                         worker_id='w2', disposition='attention')
        board.call('workflow_cancel', {'runId': already_cancelled['runId'], **self.control(already_cancelled)})
        preserved = {run['runId']: board.call('workflow_get', {'runId': run['runId']})
                     for run in (awaiting, already_cancelled)}
        self.assertEqual(board.call('task_get', {'runId': queued['runId']})['task']['status'], 'queued')
        self.assertEqual(board.call('task_get', {'runId': running['runId']})['task']['status'], 'running')
        _, browser = self.open_console(board)
        before = board.call('macro_task_timeline', {'macroTaskId': queued['macroTaskId'], 'limit': 1})
        self.assertEqual(before['macroTask']['counts']['stoppableRoots'], 2)
        self.assertEqual(before['macroTask']['counts']['awaitingHost'], 1)
        self.assertEqual(before['totals']['allRows'], 4)
        result = self.stop(browser, queued['macroTaskId'], 'state-scope', note='Stop selected active work')
        self.assertEqual({entry['runId']: (entry['state'], entry['stopped']) for entry in result['results']}, {
            queued['runId']: ('cancelled', True), running['runId']: ('cancelled', True),
            awaiting['runId']: ('awaiting-host', False), already_cancelled['runId']: ('cancelled', False),
        })
        for run in (awaiting, already_cancelled):
            current = board.call('workflow_get', {'runId': run['runId']})
            self.assertEqual(current['revision'], preserved[run['runId']]['revision'])
            self.assertEqual(current['activeRequest'], preserved[run['runId']]['activeRequest'])
        attempt = board.call('task_get', {'runId': running['runId']})['task']['selectedAttempt']
        self.assertEqual(attempt['stopInitiator'], 'console')
        self.assertTrue(attempt['cancelRequested'])
        self.assertEqual(board.call('task_get', {'runId': queued['runId']})['task']['status'], 'cancelled')
        self.finish_turn(board, running_claim, runner_status='cancelled', seal=False)
        after = board.call('macro_task_timeline', {'macroTaskId': queued['macroTaskId'], 'query': 'no such task'})
        self.assertEqual(after['macroTask']['counts']['stoppableRoots'], 0)
        self.assertEqual(after['macroTask']['counts']['awaitingHost'], 1)
        self.assertEqual(after['totals']['rows'], 0)

    def test_routing_root_is_stopped_with_its_router_task(self):
        board = self.board()
        self.catalog_fixture()
        DecisionTestCase.use_helper(self)
        DecisionTestCase.seed(self, board)
        routed = board.call('workflow_submit', {
            'requestId': 'routed-stop', 'hostId': 'host-1', 'task': 'Route this work',
            'cwd': str(self.workdir('routed-stop')),
            'executionWorkspace': {'kind': 'existing', 'access': 'write'},
            'macroTask': {'title': 'Routing stop'},
        })
        self.assertEqual(routed['routing']['status'], 'queued')
        self.assertEqual(board.call('workflow_get', {'runId': routed['runId']})['state'], 'executing')
        router_id = routed['routing']['taskId']
        _, browser = self.open_console(board)
        result = self.stop(browser, routed['macroTaskId'], 'stop-routing')
        self.assertEqual(result['results'], [
            {'runId': routed['runId'], 'state': 'cancelled', 'stopped': True},
        ])
        self.assertEqual(board.call('task_get', {'runId': router_id})['task']['status'], 'cancelled')

    def test_waiting_owned_lead_is_stopped_with_its_active_child(self):
        board = self.board(max_concurrent=4)
        lead, claim = self.lead(board, macroTask={'title': 'Lead group'})
        active = self.owned(board, claim, 'active-owned')
        self.finish_turn(board, {'claim': claim}, outcome={
            'disposition': 'waiting', 'summary': 'Waiting for owned work',
            'remaining': [], 'decisions': [], 'artifacts': [], 'request': None,
        })
        self.assertEqual(board.call('workflow_get', {'runId': lead['runId']})['state'], 'waiting-owned')
        _, browser = self.open_console(board)
        result = self.stop(browser, lead['macroTaskId'], 'stop-waiting-owned')
        self.assertEqual({entry['runId']: (entry['state'], entry['stopped']) for entry in result['results']}, {
            lead['runId']: ('cancelled', True), active['runId']: ('cancelled', True),
        })
        self.assertEqual(board.call('workflow_get', {'runId': active['runId']})['state'], 'cancelled')

    def test_delivered_owned_child_is_preserved_below_cancelled_lead(self):
        board = self.board(max_concurrent=4)
        lead, claim = self.lead(board, macroTask={'title': 'Lead delivery'})
        delivered = self.owned(board, claim, 'delivered-owned')
        self.finish_owned(board, delivered, worker='w2')
        _, browser = self.open_console(board)
        result = self.stop(browser, lead['macroTaskId'], 'stop-after-owned-delivery')
        self.assertEqual({entry['runId']: (entry['state'], entry['stopped']) for entry in result['results']}, {
            lead['runId']: ('cancelled', True), delivered['runId']: ('delivered', False),
        })
        self.assertEqual(board.call('workflow_get', {'runId': delivered['runId']})['state'], 'delivered')

    def test_active_nested_helper_stops_under_preserved_awaiting_host_lead(self):
        board = self.board(max_concurrent=4)
        lead, claim = self.lead(board, macroTask={'title': 'Preserved Lead'})
        owned = self.owned(board, claim, 'owned-with-helper')
        view, _ = self.finish_owned(board, owned, worker='w2', disposition='assistance')
        approved = self.decide(board, view, view['activeRequest']['requestId'],
            helpers=[{'requestId': 'nested-helper', 'task': 'Help owned work',
                      'cwd': str(self.workdir('nested-helper')),
                      'executionWorkspace': {'kind': 'existing', 'access': 'write'}}],
            autoContinue=False, credential=claim['agentCredential'])
        helper_id = approved['children'][0]['taskId']
        self.register(board, 'w3')
        helper_claim = self.claim(board, 'w3', claim_request_id='nested-helper-claim', run_id=helper_id)
        self.finish_turn(board, {'claim': claim})
        before = board.call('workflow_get', {'runId': lead['runId']})
        self.assertEqual(before['state'], 'awaiting-host')
        self.assertIsNotNone(before['activeRequest'])
        _, browser = self.open_console(board)
        counts = board.call('macro_task_timeline', {'macroTaskId': lead['macroTaskId'], 'limit': 1})['macroTask']['counts']
        self.assertEqual(counts['awaitingHost'], 1)
        self.assertEqual(counts['stoppableHelpers'], 1)
        result = self.stop(browser, lead['macroTaskId'], 'stop-preserved-lead', note='Stop remaining owned work')
        self.assertEqual({entry['runId']: (entry['state'], entry['stopped']) for entry in result['results']}, {
            lead['runId']: ('awaiting-host', False), owned['runId']: ('cancelled', True),
            helper_id: ('cancelled', True),
        })
        current = board.call('workflow_get', {'runId': lead['runId']})
        self.assertEqual(current['revision'], before['revision'])
        self.assertEqual(current['activeRequest'], before['activeRequest'])
        self.assertEqual(current['cancellation'], before['cancellation'])
        helper_task = board.call('task_get', {'runId': helper_id})['task']
        self.assertEqual(helper_task['status'], 'cancelling')
        self.assertEqual(helper_task['selectedAttempt']['stopInitiator'], 'console')
        self.finish_turn(board, helper_claim, worker_id='w3', runner_status='cancelled', seal=False)
        settled = board.call('workflow_get', {'runId': lead['runId']})
        self.assertEqual(settled['state'], 'awaiting-host')
        self.assertEqual(settled['activeRequest']['requestId'], before['activeRequest']['requestId'])
        self.assertEqual(settled['counts']['turns'], before['counts']['turns'])
        late = self.submit(board, kind='worktree', request_id='late-root', cwd=str(self.workdir('lead')),
                           macroTaskId=lead['macroTaskId'])
        replay = self.stop(browser, lead['macroTaskId'], 'stop-preserved-lead', note='Stop remaining owned work')
        self.assertTrue(replay['duplicate'])
        self.assertEqual({**replay, 'duplicate': False}, result)
        self.assertEqual(board.call('workflow_get', {'runId': late['runId']})['state'], 'executing')
        after = board.call('macro_task_timeline', {'macroTaskId': lead['macroTaskId']})['macroTask']['counts']
        self.assertEqual((after['stoppableRoots'], after['stoppableHelpers'], after['awaitingHost']), (1, 0, 1))

    def test_console_attribution_and_reason_are_readable_for_lead_owned_stop(self):
        board = self.board()
        lead, claim = self.lead(board, macroTask={'title': 'Attribution'})
        owned = self.owned(board, claim, 'attributed-owned')
        _, browser = self.open_console(board)
        session_id = browser.bootstrap()['consoleSession']['id']
        note = 'Stopped from the task console'
        result = self.stop(browser, lead['macroTaskId'], 'attributed-stop', note=note)
        self.assertEqual(len(result['results']), 2)
        expected = {'actor': {'role': 'console', 'id': session_id}, 'note': note}
        for run in (lead, owned):
            view = board.call('workflow_get', {'runId': run['runId']})
            task = board.call('task_get', {'runId': run['runId']})['task']
            self.assertEqual(view['cancellation'], expected)
            self.assertEqual(task['cancellation'], expected)
            self.assertEqual(cli_views.render('get', view)['cancellation'], expected)
            self.assertEqual(cli_views.render('status', task)['cancellation'], expected)

    def test_preserved_helper_boundary_survives_active_sibling_stop_receipt(self):
        board = self.board(max_concurrent=4)
        self.register(board)
        parent = self.submit(board, kind='worktree')
        self.finish_turn(board, self.claim(board), disposition='assistance')
        view = board.call('workflow_get', {'runId': parent['runId']})
        approved = self.decide(board, view, view['activeRequest']['requestId'], helpers=[
            {'requestId': name, 'task': name, 'cwd': str(self.workdir(name)),
             'executionWorkspace': {'kind': 'worktree', 'access': 'write'}}
            for name in ('boundary-helper', 'running-helper')
        ])
        boundary_id, running_id = [child['taskId'] for child in approved['children']]
        self.finish_turn(board, self.claim(board, claim_request_id='boundary-claim', run_id=boundary_id),
                         disposition='attention')
        running = self.claim(board, claim_request_id='sibling-claim', run_id=running_id)
        before = board.call('workflow_get', {'runId': parent['runId']})
        self.assertEqual(before['state'], 'awaiting-host')
        self.assertEqual(before['activeRequest']['childTaskId'], boundary_id)
        _, browser = self.open_console(board)
        result = self.stop(browser, 'run:' + parent['runId'], 'preserve-helper-boundary')
        self.assertEqual({entry['runId']: (entry['state'], entry['stopped']) for entry in result['results']}, {
            parent['runId']: ('awaiting-host', False), boundary_id: ('awaiting-host', False),
            running_id: ('cancelled', True),
        })
        stopped = board.call('workflow_get', {'runId': parent['runId']})
        self.assertEqual(stopped['revision'], before['revision'])
        self.assertEqual(stopped['activeRequest'], before['activeRequest'])
        self.finish_turn(board, running, runner_status='cancelled', seal=False)
        settled = board.call('workflow_get', {'runId': parent['runId']})
        self.assertEqual(settled['state'], 'awaiting-host')
        self.assertEqual(settled['activeRequest']['requestId'], before['activeRequest']['requestId'])
        self.assertEqual(settled['counts']['turns'], before['counts']['turns'])
        self.assertEqual(board.call('workflow_get', {'runId': boundary_id})['state'], 'awaiting-host')
