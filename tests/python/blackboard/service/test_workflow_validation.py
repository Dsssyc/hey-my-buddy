"""Reject original workflow requests before discovery or workspace side effects."""
import json
from unittest.mock import Mock, patch

from blackboard.tasks.test_workflow import BUDDY, WorkflowTestCase
from hey_my_buddy.blackboard.service.service import BoardService
from hey_my_buddy.protocol import schemas


class WorkflowValidationTests(WorkflowTestCase):
    def service(self, board):
        service = BoardService(board.store, live_registry=board.live_registry, token='host', control={}, on_stop=lambda _: {},
                               on_restart=lambda _: {}, automatic_discovery=True)
        service.harnesses.get = Mock(return_value={'status': 'unknown'})
        service.harnesses.refresh = Mock()
        service.harnesses.kick = Mock()
        self.addCleanup(service.harnesses.close)
        return service

    def assert_no_discovery(self, service):
        service.harnesses.get.assert_not_called()
        service.harnesses.refresh.assert_not_called()
        service.harnesses.kick.assert_not_called()

    def test_invalid_submission_never_probes_prepares_or_writes(self):
        board = self.board()
        service = self.service(board)
        base = {**BUDDY, 'requestId': 'invalid-submit', 'hostId': 'host-1', 'task': 'fixture',
                'cwd': str(self.workdir()), 'executionWorkspace': {'kind': 'existing', 'access': 'read'}}
        for fields in ({'adapter': 'command'}, {'argv': ['true']}, {'spec': {}}, {'routingMode': 'review'},
                       {'effort': None}):
            with self.subTest(fields=fields):
                result = json.loads(service.workflow_submit(json.dumps({'token': 'host', **base, **fields})))
                self.assertEqual(result['error']['code'], 'INVALID_ARGUMENT')
        self.assert_no_discovery(service)
        self.catalog_validation.assert_not_called()
        self.assertEqual(self.workspace.prepare_calls, [])
        with board.store.db.read() as connection:
            self.assertEqual(connection.execute('SELECT COUNT(*) FROM tasks').fetchone()[0], 0)

    def test_invalid_helper_never_probes_prepares_or_writes(self):
        board = self.board()
        self.register(board)
        parent = self.submit(board)
        self.finish_turn(board, self.claim(board), disposition='assistance')
        view = board.call('workflow_get', {'runId': parent['runId']})
        service = self.service(board)
        prepared = len(self.workspace.prepare_calls)
        self.catalog_validation.reset_mock()
        helper = {**BUDDY, 'requestId': 'invalid-helper', 'task': 'fixture', 'cwd': str(self.workdir('helper')),
                  'executionWorkspace': {'kind': 'worktree', 'access': 'read'}}
        for fields in ({'adapter': 'external'}, {'argv': None}, {'inheritRoutingPreferences': True}, {'model': None}):
            with self.subTest(fields=fields):
                result = json.loads(service.workflow_decide(json.dumps({
                    'token': 'host', 'runId': view['runId'], 'requestId': view['activeRequest']['requestId'],
                    'commandId': 'invalid-helper', 'expectedRevision': view['revision'], 'decision': 'approve',
                    'helpers': [{**helper, **fields}], **self.control(view),
                })))
                self.assertEqual(result['error']['code'], 'INVALID_ARGUMENT')
        self.assert_no_discovery(service)
        self.catalog_validation.assert_not_called()
        self.assertEqual(len(self.workspace.prepare_calls), prepared)
        with board.store.db.read() as connection:
            self.assertEqual(connection.execute('SELECT COUNT(*) FROM tasks').fetchone()[0], 1)

    def test_normalized_submission_is_passed_down_without_revalidation(self):
        board = self.board()
        service = self.service(board)
        params = {**BUDDY, 'requestId': 'normalize-once', 'hostId': 'host-1', 'task': 'fixture',
                  'cwd': str(self.workdir()), 'executionWorkspace': {'kind': 'existing', 'access': 'read'}}
        with patch.object(schemas, 'normalize_workflow_submit', wraps=schemas.normalize_workflow_submit) as normalized:
            result = json.loads(service.workflow_submit(json.dumps({'token': 'host', **params})))
        self.assertNotIn('error', result)
        normalized.assert_called_once()
        self.assertEqual(result['buddy'], BUDDY)

    def test_normalized_helper_is_passed_down_without_revalidation(self):
        board = self.board()
        self.register(board)
        parent = self.submit(board)
        self.finish_turn(board, self.claim(board), disposition='assistance')
        view = board.call('workflow_get', {'runId': parent['runId']})
        service = self.service(board)
        helper = {**BUDDY, 'requestId': 'valid-helper', 'task': 'fixture', 'cwd': str(self.workdir('helper')),
                  'executionWorkspace': {'kind': 'worktree', 'access': 'read'}}
        with patch.object(schemas, 'normalize_helpers', wraps=schemas.normalize_helpers) as normalized:
            result = json.loads(service.workflow_decide(json.dumps({
                'token': 'host', 'runId': view['runId'], 'requestId': view['activeRequest']['requestId'],
                'commandId': 'valid-helper', 'expectedRevision': view['revision'], 'decision': 'approve',
                'helpers': [helper], **self.control(view),
            })))
        self.assertNotIn('error', result)
        normalized.assert_called_once()
        self.assertEqual(len(result['children']), 1)
