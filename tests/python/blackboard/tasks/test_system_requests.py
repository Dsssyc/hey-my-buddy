"""System boundaries store one reason; content and ancestor identity stay intact."""
import json
from unittest.mock import patch

from blackboard.tasks import test_workflow_boundaries as boundary_helpers
from blackboard.tasks.test_workflow import WorkflowTestCase
from hey_my_buddy.errors import BoardError


class SystemRequestTests(WorkflowTestCase):
    parent_with_helpers = boundary_helpers.WorkflowBoundaryTests.parent_with_helpers
    helper_spec = boundary_helpers.WorkflowBoundaryTests.helper_spec
    view = boundary_helpers.WorkflowBoundaryTests.view
    yield_helper = boundary_helpers.WorkflowBoundaryTests.yield_helper
    answer = boundary_helpers.WorkflowBoundaryTests.answer

    def report_after_settlement(self, *, auto_continue, error_code=None):
        board = self.board()
        self.register(board)
        root = self.submit(board)
        self.finish_turn(board, self.claim(board), disposition='assistance')
        view = self.view(board, root['runId'])
        worker_request = view['activeRequest']
        self.assertEqual(worker_request['summary'], 'please review the approach')
        self.assertIsNone(worker_request['reason'])
        approved = self.decide(board, view, worker_request['requestId'],
                               helpers=[self.helper_spec('child')], autoContinue=auto_continue)
        child_id = approved['children'][0]['taskId']
        claim = self.claim(board, run_id=child_id, claim_request_id='child-turn')
        if error_code:
            with patch.object(board.store.workflow, '_requeue', side_effect=BoardError(error_code, 'diagnostic')):
                self.finish_turn(board, claim)
        else:
            self.finish_turn(board, claim)
        return board, root['runId'], child_id

    def test_helpers_settled_has_facts_and_audit_is_the_stored_payload(self):
        board, root_id, child_id = self.report_after_settlement(auto_continue=False)
        view = board.call('workflow_get', {'runId': root_id, 'includeAudit': True})
        request = view['activeRequest']
        self.assertEqual(request['reason'], {'code': 'helpers-settled', 'params': {}})
        self.assertIsNone(request['summary'])
        self.assertEqual(request['helperOutcomes'][0]['taskId'], child_id)
        self.assertTrue({'attempted', 'neededWork', 'expectedArtifacts', 'acceptance'}.isdisjoint(request))
        stored_request = next(row for row in view['audit']['requests'] if row['requestId'] == request['requestId'])
        with board.store.db.read() as connection:
            raw = connection.execute('SELECT payload_json FROM workflow_requests WHERE request_id=?',
                                     (request['requestId'],)).fetchone()[0]
        self.assertEqual(stored_request['payload'], json.loads(raw))
        self.assertEqual(stored_request['payload'], {'helperOutcomes': request['helperOutcomes']})

    def test_each_automatic_requeue_failure_is_a_registered_reason(self):
        for code in ('CONFLICT', 'SHUTDOWN_UNCONFIRMED', 'REVISION_CONFLICT', 'ANCESTOR_TERMINAL',
                     'UPGRADE_IN_PROGRESS', 'ILLEGAL_TRANSITION'):
            with self.subTest(code=code), patch.object(self, 'directory', self.directory / code):
                board, root_id, child_id = self.report_after_settlement(auto_continue=True, error_code=code)
                view = self.view(board, root_id)
                self.assertEqual(view['state'], 'awaiting-host')
                self.assertIsNone(view['activeRequest']['summary'])
                self.assertEqual(view['activeRequest']['reason'], {'code': code, 'params': {}})
                self.assertEqual(view['activeRequest']['helperOutcomes'][0]['taskId'], child_id)

    def test_unlisted_automatic_requeue_error_cannot_become_a_reason(self):
        with self.assertRaises(BoardError) as caught:
            self.report_after_settlement(auto_continue=True, error_code='UNLISTED_ERROR')
        self.assertEqual(caught.exception.code, 'INTERNAL_ERROR')
        with self._stack[-1].store.db.read() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM workflow_requests WHERE reason_code='UNLISTED_ERROR'").fetchone()[0], 0)

    def test_system_reason_and_payload_follow_two_owned_ancestors(self):
        board, root, (middle,) = self.parent_with_helpers()
        self.yield_helper(board, middle, 'w1', 'middle-yield')
        self.answer(board, root['runId'], 'grant-dependency', helpers=[self.helper_spec('leaf')])
        leaf = self.view(board, middle)['children'][0]['taskId']
        with board.store.db.write() as connection:
            run = board.store.workflow._run_row(connection, leaf)
            board.store.workflow._record_helper_report(connection, run, [],
                reason={'code': 'helpers-settled', 'params': {}}, now=board.store.workflow.now())
        leaf_view = self.view(board, leaf)['activeRequest']
        middle_view = self.view(board, middle)['activeRequest']
        root_view = self.view(board, root['runId'])['activeRequest']
        for request in (middle_view, root_view):
            self.assertEqual(request['reason'], leaf_view['reason'])
            self.assertIsNone(request['summary'])
            self.assertEqual(request['helperOutcomes'], [])
            self.assertEqual(request['origin'], {'runId': leaf, 'requestId': leaf_view['requestId']})
        self.assertEqual(middle_view['proxy'], {'runId': leaf, 'requestId': leaf_view['requestId']})
        self.assertEqual(root_view['proxy'], {'runId': middle, 'requestId': middle_view['requestId']})
        with board.store.db.read() as connection:
            rows = connection.execute("SELECT summary,reason_code,reason_params_json,payload_json FROM workflow_requests"
                                      " WHERE request_id IN (?,?,?)", (leaf_view['requestId'], middle_view['requestId'],
                                                                     root_view['requestId'])).fetchall()
        self.assertEqual(len(rows), 3)
        for row in rows:
            self.assertEqual(tuple(row)[:3], (None, 'helpers-settled', '{}'))
            self.assertTrue({'summary', 'attempted', 'neededWork', 'expectedArtifacts', 'acceptance'}
                            .isdisjoint(json.loads(row['payload_json'])))
