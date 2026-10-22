"""Private fixtures for board provenance of the read-only session listing."""
import json
import os
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest import mock

from hey_my_buddy.blackboard.tasks import worker_sessions


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

    def test_dsh_sessions_are_not_in_codex_history(self):
        rows = [dsh_grouped_row('legacy-grouped')]
        store = FakeStore(self.state, rows)
        self.assertEqual(worker_sessions.handle(store, {})['count'], 0)

    def test_codex_list_uses_board_only_and_never_guesses_other_sessions(self):
        row = {'harness': 'codex', 'task_id': 'micro-task-1', 'attempt_id': 'attempt-1',
               'cwd': '/fixture/work', 'execution_state': 'finished', 'shutdown_confirmed': 1,
               'result_json': json.dumps({'result': {
                   'nativeSession': {'harness': 'codex', 'sessionId': 'thread-1', 'captured': True,
                                     'storageOwner': 'hey-my-buddy-micro-task', 'resumeMode': 'initial',
                                     'nativeAppVisibility': 'unknown'},
                   'nativeCheckpoint': {'version': 1, 'sessionId': 'thread-1', 'taskId': 'micro-task-1',
                                        'attemptId': 'attempt-1'}},
                   'shutdownConfirmed': True})}
        result = worker_sessions.handle(FakeStore(self.state, [row]), {})
        self.assertEqual([r['sessionId'] for r in result['sessions']], ['thread-1'])
        self.assertEqual(result['sessions'][0]['attemptIds'], ['attempt-1'])
        self.assertNotIn('nativeAccess', result)
        self.assertNotIn('codexAction', result)
        self.assertEqual(result['count'], 1)
        default = worker_sessions.handle(FakeStore(self.state, [row]), {})
        self.assertEqual(default['harness'], 'codex')

    def test_codex_list_accepts_actual_stopped_adapter_fixture(self):
        from buddy.harnesses.codex.test_codex import CodexAdapterTests
        fixture = CodexAdapterTests('test_completed_native_turn_has_structured_provenance_and_preserved_final_activity')
        fixture.setUp()
        try:
            with mock.patch.dict(os.environ, {'HMB_DEV_SOURCE': '1',
                                              'HMB_STATE_DIR': str(fixture.root / 'state'),
                                              'HMB_RUNTIME_ROOT': str(fixture.root / 'runtime'),
                                              'HMB_HARNESS_RECORD_FILE': fixture.environment['HMB_HARNESS_RECORD_FILE']}):
                outcome = fixture.execute(fixture.context())
            self.assertTrue(outcome.shutdown_confirmed)
            from hey_my_buddy.buddy.runtime.execution import Execution
            claim = {'task': {'taskId': 'micro-task-1', 'spec': {'adapter': 'codex'}},
                     'attempt': {'taskId': 'micro-task-1', 'attemptId': 'attempt-1', 'generation': 1}}
            execution = Execution(claim, self.state, 'n' * 32, client=mock.Mock())
            execution.directory.mkdir(parents=True, mode=0o700)
            receipt = execution.receipt(claim, outcome.to_report(), execution.directory)['report']
            self.assertNotIn('workerId', json.dumps(receipt))
            self.assertNotIn('nonce', json.dumps(receipt))
            row = {'harness': 'codex', 'task_id': 'micro-task-1', 'attempt_id': 'attempt-1',
                   'cwd': str(fixture.cwd), 'execution_state': 'finished', 'shutdown_confirmed': 1,
                   'result_json': json.dumps(receipt)}
            result = worker_sessions.handle(FakeStore(self.state, [row]), {})
            self.assertEqual(result['count'], 1)
            self.assertEqual(outcome.result['nativeSession']['storageScope'], 'harness-user-store')
            self.assertEqual(result['sessions'][0]['storageOwner'], 'harness-user-store')
            # Receipts before the checkpoint/nativeSession additions still have
            # a strictly correlated completed turn. Do not lose that history.
            legacy = json.loads(row['result_json'])
            legacy['result'].pop('nativeCheckpoint')
            legacy['result'].pop('nativeSession')
            legacy_row = {**row, 'result_json': json.dumps(legacy)}
            historical = worker_sessions.handle(FakeStore(self.state, [legacy_row]), {})
            self.assertEqual(historical['count'], 1)
            self.assertEqual(historical['sessions'][0]['storageOwner'], 'harness-user-store')
            legacy['result']['turn']['taskId'] = 'unrelated-micro-task'
            legacy_row['result_json'] = json.dumps(legacy)
            self.assertEqual(worker_sessions.handle(FakeStore(self.state, [legacy_row]), {})['count'], 0)
            resumed = dict(row)
            resumed['attempt_id'] = 'attempt-2'
            continued = json.loads(row['result_json'])
            continued['result']['nativeCheckpoint']['attemptId'] = 'attempt-2'
            continued['result']['nativeSession']['resumeMode'] = 'native-session'
            resumed['result_json'] = json.dumps(continued)
            deduped = worker_sessions.handle(FakeStore(self.state, [row, resumed]), {})
            self.assertEqual(deduped['count'], 1)
            self.assertEqual(deduped['sessions'][0]['attemptIds'], ['attempt-1', 'attempt-2'])
        finally:
            fixture.doCleanups()


if __name__ == '__main__':
    unittest.main()
