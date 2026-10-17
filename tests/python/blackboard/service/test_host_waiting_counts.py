"""Host reply counts share the history filters and follow completed mutations."""
import json

from blackboard.tasks.test_workflow import WorkflowTestCase
import blackboard.tasks.test_workspace_lifecycle as lifecycle
from blackboard.tasks.test_workflow_real import BUDDY, RealWorkspaceTestCase
from hey_my_buddy.protocol.client import BoardClient


class HostWaitingCountsTests(WorkflowTestCase):
    def setUp(self):
        super().setUp()
        self.board_instance = self.board()
        self.register(self.board_instance)

    def returned(self, name, disposition='completed'):
        run = self.submit(self.board_instance, request_id=name, cwd=str(self.workdir(name)))
        claim = self.claim(self.board_instance, claim_request_id=name, run_id=run['runId'])
        self.finish_turn(self.board_instance, claim, disposition=disposition)
        return self.board_instance.call('workflow_get', {'runId': run['runId']}), run['control']

    def assert_counts(self, response, expected):
        self.assertEqual(response.get('waitingForHost', {}), expected)
        for name in ('host', 'review'):
            listed = self.board_instance.call('task_list', {'hostId': 'host-1', 'filter': name})
            self.assertEqual(listed['total'], expected.get(name, 0))
            self.assertEqual(listed.get('waitingForHost', {}), expected)

    def test_selectors_and_mutations_use_post_request_counts(self):
        first, control = self.returned('first', 'attention')
        second, second_control = self.returned('second')
        third, third_control = self.returned('third')
        expected = {'host': 1, 'review': 2}
        for operation, params in (
            ('workflow_get', {'runId': first['runId']}),
            ('task_get', {'runId': first['runId']}),
            ('task_get', {'requestId': 'second'}),
            ('task_wait', {'runId': third['runId'], 'timeoutMs': 0}),
            ('task_result', {'runId': third['runId']}),
        ):
            with self.subTest(operation=operation, params=params):
                self.assert_counts(self.board_instance.call(operation, params), expected)
        submitted = self.submit(self.board_instance, request_id='new', cwd=str(self.workdir('new')))
        self.assert_counts(submitted, expected)
        continued = self.board_instance.call('workflow_continue', {
            'runId': first['runId'], **control, 'commandId': 'resume',
            'expectedRevision': first['revision'], 'input': 'continue the work', 'helperPolicy': 'keep',
        })
        self.assert_counts(continued, {'review': 2})
        accepted = self.board_instance.call('workflow_accept', {
            'runId': second['runId'], **second_control, 'artifactId': second['finalArtifactId'],
            'note': 'checked output', 'notRequired': 'fixture has no integration target',
        })
        self.assert_counts(accepted, {'review': 1})
        concluded = self.board_instance.call('workflow_conclude', {
            'runId': third['runId'], **third_control, 'note': 'recorded the fixture conclusion',
        })
        self.assert_counts(concluded, {})
        self.assertNotIn('waitingForHost', concluded)
        self.assertNotIn('waitingForHost', self.board_instance.call('task_list', {'hostId': 'host-b'}))
        self.assertNotIn('waitingForHost', self.board_instance.call('task_list', {}))

    def test_counts_ignore_list_pagination_and_other_filters(self):
        for index in range(22):
            self.returned(f'delivered-{index}')
        listed = self.board_instance.call('task_list', {
            'hostId': 'host-1', 'filter': 'review', 'limit': 1, 'offset': 20,
        })
        self.assertEqual(len(listed['runs']), 1)
        self.assertEqual(listed['total'], 22)
        self.assert_counts(listed, {'review': 22})
        empty = self.board_instance.call('task_list', {'hostId': 'host-1', 'query': 'no matching task'})
        self.assertEqual(empty['total'], 0)
        self.assert_counts(empty, {'review': 22})


class PrivateServiceWaitingCountsTests(RealWorkspaceTestCase):
    register = lifecycle.LifecycleTestCase.register
    claim = lifecycle.LifecycleTestCase.claim
    turn_record = lifecycle.LifecycleTestCase.turn_record
    finish_turn = lifecycle.LifecycleTestCase.finish_turn
    view = lifecycle.LifecycleTestCase.view
    run_worktree = lifecycle.LifecycleTestCase.run_worktree

    def setUp(self):
        super().setUp()
        self.controls = {}
        self.worker = 'waiting-count-fixture'

    def invoke(self, method, params):
        code, response = self.cli(method, json.dumps(params))
        self.assertEqual(code, 0, response)
        return response

    def test_real_cli_service_counts_and_control_files_without_model_calls(self):
        # Prepare completed native-turn fixtures before starting the private
        # service. No native harness runs; only the explicit command below runs.
        board = self.board()
        self.register(board)
        boundary = self.run_worktree(board, request_id='boundary', disposition='attention')
        first = self.run_worktree(board, request_id='first')
        second = self.run_worktree(board, request_id='second')
        run_id = boundary[0]['runId']
        with self.daemon(env={'HMB_MAX_CONCURRENT': '1'}):
            expected = {'host': 1, 'review': 2}
            for mode in ('brief', 'full'):
                for method, params in (
                    ('status', {'runId': run_id}),
                    ('status', {'requestId': 'first'}),
                    ('get', {'runId': run_id}),
                    ('list', {'hostId': 'host-1'}),
                    ('await', {'runId': run_id, 'waitSeconds': 1}),
                    ('await', {'requestId': 'first', 'waitSeconds': 1}),
                    ('wait', {'runId': run_id, 'timeoutMs': 0}),
                    ('result', {'runId': run_id}),
                    ('events', {'runId': run_id}),
                    ('artifacts', {'runId': run_id}),
                ):
                    with self.subTest(method=method, mode=mode):
                        response = self.invoke(method, {**params, 'output': mode})
                        self.assertEqual(response['waitingForHost'], expected)
            submitted = self.invoke('submit', {
                **BUDDY, 'requestId': 'first', 'hostId': 'host-1', 'task': 'isolated worktree task',
                'cwd': str(self.repo), 'submissionToken': 't' * 32,
                'executionWorkspace': {'kind': 'worktree', 'access': 'write', 'writeScope': ['.']},
            })
            self.assertEqual(submitted['waitingForHost'], expected)
            for name in ('host', 'review'):
                listed = self.invoke('list', {'hostId': 'host-1', 'filter': name})
                self.assertEqual(listed['total'], expected[name])
            self.assertNotIn('waitingForHost', self.invoke('list', {'hostId': 'host-b'}))
            self.assertNotIn('waitingForHost', self.invoke('list', {}))
            seeded = self.submit_execution(**{
                'requestId': 'no-model-command', 'task': 'Run a private command',
                'cwd': str(self.workdir('command')), 'adapter': 'command', 'argv': ['/bin/echo', 'done'],
            })['task']
            command = self.invoke('status', {'runId': seeded['runId']})
            self.assertNotIn('waitingForHost', command)
            self.assertTrue(self.invoke('await', {'runId': command['runId'], 'waitSeconds': 10})['ok'])
            accepted = self.invoke('accept', {
                'runId': first[0]['runId'], 'controlFile': submitted['controlFile'],
                'artifactId': first[6]['artifactId'], 'note': 'checked the sealed fixture',
                'notRequired': 'fixture has no integration target',
            })
            self.assertEqual(accepted['waitingForHost'], {'host': 1, 'review': 1})
            concluded = self.invoke('conclude', {
                'runId': second[0]['runId'], **second[0]['control'], 'note': 'retained fixture conclusion',
            })
            self.assertEqual(concluded['waitingForHost'], {'host': 1})
            cancelled = self.invoke('cancel', {
                'runId': run_id, **boundary[0]['control'], 'commandId': 'cancel-boundary',
                'reason': 'close the fixture Host boundary',
            })
            self.assertNotIn('waitingForHost', cancelled)
            concluded = self.invoke('conclude', {
                'runId': run_id, **boundary[0]['control'], 'note': 'retained fixture boundary conclusion',
            })
            self.assertNotIn('waitingForHost', concluded)
            self.assertNotIn('waitingForHost', self.invoke('status', {'runId': run_id}))
            client = BoardClient(self.directory, autostart=False)
            for name in ('host', 'review'):
                self.assertEqual(client.list_tasks(hostId='host-1', filter=name)['total'], 0)

