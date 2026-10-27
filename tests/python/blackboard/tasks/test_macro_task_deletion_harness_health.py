"""Deletion needs every harness that still owns sessions in its user store."""
from __future__ import annotations

from contextlib import ExitStack
import json
from unittest.mock import patch

from blackboard.tasks.test_workflow import WorkflowTestCase
from hey_my_buddy.blackboard.tasks import macro_task_deletion
from hey_my_buddy.errors import BoardError
from hey_my_buddy.protocol.reasons import reason


SESSION_ID = '44444444-4444-4444-8444-444444444444'
NOT_FOUND = reason('harness.not-found', operation='probe')


class DeletionHarnessHealthTests(WorkflowTestCase):
    def closed(self, board, request_id, *, macro_task=None):
        extra = {'macroTask': {'title': macro_task}} if macro_task else {}
        run = self.submit(board, request_id=request_id, cwd=str(self.workdir('project')), **extra)
        cancelled = board.call('workflow_cancel', {'runId': run['runId'], **self.control(run)})
        board.call('workflow_conclude', {'runId': run['runId'], 'note': 'Private health fixture is settled',
                                         **self.control(cancelled)})
        return run

    def owned_user_store_session(self, board, run):
        """One finished Codex attempt whose receipt proves a user-store session."""
        attempt_id = 'health-attempt-1'
        receipt = {'result': {'nativeSession': {'harness': 'codex', 'sessionId': SESSION_ID,
                                                'captured': True, 'storageOwner': 'harness-user-store',
                                                'resumeMode': 'initial'},
                              'nativeCheckpoint': {'version': 1, 'sessionId': SESSION_ID,
                                                   'taskId': run['runId'], 'attemptId': attempt_id}}}
        now = board.store.now()
        with board.store.db.write() as db:
            db.execute('INSERT INTO attempts(attempt_id,task_id,generation,nonce_verifier,execution_state,'
                       'adapter,shutdown_confirmed,result_json,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)',
                       (attempt_id, run['runId'], 1, 'fixture', 'finished', 'codex', 1,
                        json.dumps(receipt), now, now))
        return attempt_id

    def set_codex_health(self, board, status, record):
        with board.store.db.write() as db:
            db.execute("UPDATE harness_health SET status=?,record_json=? WHERE harness='codex'",
                       (status, json.dumps(record)))

    def physical_io_off(self):
        stack = ExitStack()
        for stage in ('_delete_sessions', '_delete_workspaces', '_delete_private_roots', '_delete_cli_files'):
            stack.enter_context(patch.object(macro_task_deletion, stage, return_value=[]))
        return stack

    def test_unavailable_harness_refuses_preview_and_delete_without_marking(self):
        board = self.board()
        run = self.closed(board, 'health-owner', macro_task='Health owner')
        self.owned_user_store_session(board, run)
        self.set_codex_health(board, 'missing', {'reason': NOT_FOUND})
        for operation, args in ((macro_task_deletion.preview, (board.store, run['macroTaskId'])),
                                (macro_task_deletion.delete, (board.store, run['macroTaskId'], 1))):
            with self.subTest(operation=operation.__name__), self.assertRaises(BoardError) as caught:
                operation(*args)
            self.assertEqual(caught.exception.code, 'MACRO_TASK_HARNESS_UNAVAILABLE')
            self.assertEqual(caught.exception.details, {
                'macroTaskId': run['macroTaskId'],
                'harnesses': [{'harness': 'codex', 'reason': NOT_FOUND, 'microTaskIds': [run['runId']]}]})
        with board.store.db.read() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM macro_task_deletions').fetchone()[0], 0)
            self.assertIsNotNone(db.execute("SELECT 1 FROM attempts WHERE attempt_id='health-attempt-1'").fetchone())
        self.assertEqual(board.call('workflow_get', {'runId': run['runId']})['state'], 'concluded')

    def test_recovered_harness_allows_deletion_again(self):
        board = self.board()
        run = self.closed(board, 'health-recovered', macro_task='Health recovered')
        self.owned_user_store_session(board, run)
        self.set_codex_health(board, 'unhealthy',
                              {'reason': reason('harness.probe-failed', operation='probe')})
        with self.assertRaises(BoardError) as caught:
            macro_task_deletion.delete(board.store, run['macroTaskId'], 1)
        self.assertEqual(caught.exception.code, 'MACRO_TASK_HARNESS_UNAVAILABLE')
        self.set_codex_health(board, 'ready', {})
        self.assertEqual(macro_task_deletion.preview(board.store, run['macroTaskId'])['blockerTotal'], 0)
        with self.physical_io_off():
            result = macro_task_deletion.delete(board.store, run['macroTaskId'], 1)
        self.assertEqual(result['state'], 'deleted')

    def test_macro_task_without_user_store_sessions_ignores_harness_health(self):
        board = self.board()
        run = self.closed(board, 'health-free', macro_task='Health free')
        self.set_codex_health(board, 'missing', {'reason': NOT_FOUND})
        self.assertEqual(macro_task_deletion.preview(board.store, run['macroTaskId'])['blockerTotal'], 0)
        with self.physical_io_off():
            self.assertEqual(macro_task_deletion.delete(board.store, run['macroTaskId'], 1)['state'], 'deleted')

    def test_retry_of_marked_deletion_proceeds_after_health_degrades(self):
        board = self.board()
        run = self.closed(board, 'health-marked', macro_task='Health marked')
        self.owned_user_store_session(board, run)
        with self.physical_io_off(), patch.object(macro_task_deletion, '_delete_sessions',
                                                  side_effect=OSError('offline interruption')):
            interrupted = macro_task_deletion.delete(board.store, run['macroTaskId'], 1)
        self.assertEqual(interrupted['state'], 'deleting')
        self.set_codex_health(board, 'missing', {'reason': NOT_FOUND})
        with self.physical_io_off():
            completed = macro_task_deletion.delete(board.store, run['macroTaskId'], 1)
        self.assertEqual((completed['state'], completed['deletionId'], completed['duplicate']),
                         ('deleted', interrupted['deletionId'], True))
