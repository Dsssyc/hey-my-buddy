"""Private fixtures for board provenance of the read-only session listing."""
import json
import os
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest import mock

from hey_my_buddy.blackboard.tasks import worker_sessions
from hey_my_buddy.errors import BoardError


class FakeDB:
    def __init__(self, rows):
        self.rows = rows

    @contextmanager
    def read(self):
        yield self

    def execute(self, _query, args=()):
        harness = args[0] if args else 'codex'
        return mock.Mock(fetchall=lambda: [row for row in self.rows if row['harness'] == harness])


class FakeStore:
    def __init__(self, directory, rows):
        self.directory = directory
        self.db = FakeDB(rows)


def dsh_grouped_row(session, *, stopped=True):
    """A receipt shape only the removed grouped feature could produce."""
    result = {'nativeSession': {'harness': 'dsh', 'sessionId': session, 'captured': True,
                                'sessionIdConflict': False, 'ambiguous': False},
              'workspace': {'bound': True, 'id': 'group-1', 'sessionId': session,
                            'path': '/fixture/work', 'created': False},
              'workspaceManifest': {'path': '/fixture/work'}}
    return {'harness': 'dsh', 'task_id': f'task-{session}', 'attempt_id': f'attempt-{session}',
            'cwd': '/source/project', 'execution_state': 'finished' if stopped else 'uncertain',
            'shutdown_confirmed': int(stopped),
            'result_json': json.dumps({'result': result, 'shutdownConfirmed': stopped})}


class WorkerSessionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.state = Path(self.temporary.name)

    def test_dsh_sessions_are_no_longer_listed_or_archived(self):
        # ADR-021 decision 18: the product DSH grouping feature is gone, so a
        # historical grouped receipt is neither listed nor acted upon, and the
        # grouped-session plan/apply entry points are refused outright.
        rows = [dsh_grouped_row('legacy-grouped')]
        store = FakeStore(self.state, rows)
        with self.assertRaises(BoardError) as raised:
            worker_sessions.handle(store, {'harness': 'dsh'})
        self.assertEqual(raised.exception.code, 'UNSUPPORTED')
        for action in ('plan', 'apply'):
            with self.assertRaises(BoardError) as raised:
                worker_sessions.handle(store, {'action': action})
            self.assertEqual(raised.exception.code, 'INVALID_ARGUMENT')
        with self.assertRaises(BoardError) as raised:
            worker_sessions.handle(store, {'harness': 'dsh', 'planDigest': '0' * 64, 'selectionDigest': '0' * 64})
        self.assertEqual(raised.exception.code, 'INVALID_ARGUMENT')
        with self.assertRaises(BoardError) as raised:
            worker_sessions.handle(store, {'harness': 'codex', 'action': 'apply'})
        self.assertEqual(raised.exception.code, 'INVALID_ARGUMENT')

    def test_codex_list_uses_board_only_and_never_guesses_other_sessions(self):
        row = {'harness': 'codex', 'task_id': 'micro-task-1', 'attempt_id': 'attempt-1',
               'cwd': '/fixture/work', 'execution_state': 'finished', 'shutdown_confirmed': 1,
               'result_json': json.dumps({'result': {
                   'nativeSession': {'harness': 'codex', 'sessionId': 'thread-1', 'captured': True,
                                     'storageOwner': 'buddy-micro-task', 'resumeMode': 'initial',
                                     'nativeAppVisibility': 'unknown'},
                   'nativeCheckpoint': {'version': 1, 'sessionId': 'thread-1', 'taskId': 'micro-task-1',
                                        'attemptId': 'attempt-1'}},
                   'shutdownConfirmed': True})}
        result = worker_sessions.handle(FakeStore(self.state, [row]), {'harness': 'codex'})
        self.assertEqual([r['sessionId'] for r in result['sessions']], ['thread-1'])
        self.assertEqual(result['sessions'][0]['attemptIds'], ['attempt-1'])
        self.assertFalse(result['nativeAccess'])
        self.assertEqual(result['count'], 1)
        default = worker_sessions.handle(FakeStore(self.state, [row]), {})
        self.assertEqual(default['harness'], 'codex')

    def test_codex_list_accepts_actual_stopped_adapter_fixture(self):
        from buddy.harnesses.codex.test_codex import CodexAdapterTests
        fixture = CodexAdapterTests('test_completed_native_turn_has_structured_provenance_and_preserved_final_activity')
        fixture.setUp()
        try:
            with mock.patch.dict(os.environ, {'BUDDY_DEV_SOURCE': '1',
                                              'BUDDY_STATE_DIR': str(fixture.root / 'state'),
                                              'BUDDY_RUNTIME_ROOT': str(fixture.root / 'runtime'),
                                              'BUDDY_CODEX_CLI': str(fixture.environment['BUDDY_CODEX_CLI'])}):
                outcome = fixture.execute(fixture.context())
            self.assertTrue(outcome.shutdown_confirmed)
            receipt = {'result': outcome.result, 'shutdownConfirmed': True}
            row = {'harness': 'codex', 'task_id': 'micro-task-1', 'attempt_id': 'attempt-1',
                   'cwd': str(fixture.cwd), 'execution_state': 'finished', 'shutdown_confirmed': 1,
                   'result_json': json.dumps(receipt)}
            result = worker_sessions.handle(FakeStore(self.state, [row]), {'harness': 'codex'})
            self.assertEqual(result['count'], 1)
            self.assertEqual(outcome.result['nativeSession']['storageScope'], 'harness-user-store')
            self.assertEqual(result['sessions'][0]['storageOwner'], 'harness-user-store')
            # Receipts before the checkpoint/nativeSession additions still have
            # a strictly correlated completed turn. Do not lose that history.
            legacy = json.loads(row['result_json'])
            legacy['result'].pop('nativeCheckpoint')
            legacy['result'].pop('nativeSession')
            legacy_row = {**row, 'result_json': json.dumps(legacy)}
            historical = worker_sessions.handle(FakeStore(self.state, [legacy_row]), {'harness': 'codex'})
            self.assertEqual(historical['count'], 1)
            self.assertEqual(historical['sessions'][0]['storageOwner'], 'harness-user-store')
            legacy['result']['turn']['taskId'] = 'unrelated-micro-task'
            legacy_row['result_json'] = json.dumps(legacy)
            self.assertEqual(worker_sessions.handle(FakeStore(self.state, [legacy_row]), {'harness': 'codex'})['count'], 0)
            resumed = dict(row)
            resumed['attempt_id'] = 'attempt-2'
            continued = json.loads(row['result_json'])
            continued['result']['nativeCheckpoint']['attemptId'] = 'attempt-2'
            continued['result']['nativeSession']['resumeMode'] = 'native-session'
            resumed['result_json'] = json.dumps(continued)
            deduped = worker_sessions.handle(FakeStore(self.state, [row, resumed]), {'harness': 'codex'})
            self.assertEqual(deduped['count'], 1)
            self.assertEqual(deduped['sessions'][0]['attemptIds'], ['attempt-1', 'attempt-2'])
        finally:
            fixture.doCleanups()


if __name__ == '__main__':
    unittest.main()
