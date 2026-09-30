"""Private fixtures for board provenance and exact native archive selection."""
import json
import os
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest import mock

from buddy import backup, worker_sessions
from buddy.errors import BoardError


class FakeDB:
    def __init__(self, rows):
        self.rows = rows

    @contextmanager
    def read(self):
        yield self

    def execute(self, _query, args):
        adapter = args[0]
        return mock.Mock(fetchall=lambda: [row for row in self.rows if row['adapter'] == adapter])


class FakeStore:
    def __init__(self, directory, rows):
        self.directory = directory
        self.db = FakeDB(rows)


def dsh_row(session, *, group='group-1', created=False, stopped=True, cwd='/fixture/work'):
    result = {'nativeSession': {'adapter': 'dsh', 'sessionId': session, 'captured': True,
                                'sessionIdConflict': False, 'ambiguous': False},
              'workspace': {'bound': True, 'id': group, 'sessionId': session,
                            'path': cwd, 'created': created},
              'workspaceManifest': {'path': cwd}}
    return {'adapter': 'dsh', 'task_id': f'task-{session}', 'attempt_id': f'attempt-{session}',
            'cwd': '/source/project', 'execution_state': 'finished' if stopped else 'uncertain',
            'shutdown_confirmed': int(stopped),
            'result_json': json.dumps({'result': result, 'shutdownConfirmed': stopped})}


class WorkerSessionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.state = Path(self.temporary.name)

    def test_list_uses_board_only_and_never_guesses_other_sessions(self):
        rows = [dsh_row('buddy'), dsh_row('uncertain', stopped=False)]
        unrelated = dsh_row('unrelated')
        receipt = json.loads(unrelated['result_json'])
        receipt['result']['workspace']['bound'] = False
        unrelated['result_json'] = json.dumps(receipt)
        rows.append(unrelated)
        with mock.patch.object(worker_sessions, '_native', side_effect=AssertionError('native called')):
            result = worker_sessions.handle(FakeStore(self.state, rows), {'adapter': 'dsh'})
        self.assertEqual([r['sessionId'] for r in result['sessions']], ['buddy', 'uncertain'])
        self.assertFalse(result['nativeAccess'])

    def test_plan_apply_two_owned_siblings_keeps_unrelated_member(self):
        store = FakeStore(self.state, [dsh_row('first'), dsh_row('second')])
        members = ['unrelated', 'first', 'second']
        calls = []

        def native(method, payload):
            calls.append((method, payload['sessionId']))
            if method == 'inspect-session':
                return {'revision': 'r1', 'sessionIds': list(members)}
            members.remove(payload['sessionId'])
            return {'sessionArchived': True, 'groupRemoved': False,
                    'groupRetainedReason': 'other-sessions-remain'}

        with mock.patch.object(worker_sessions, '_native', side_effect=native):
            plan = worker_sessions.handle(store, {'action': 'plan'})
            self.assertEqual(plan['sessionsSelected'], 2)
            self.assertFalse(any(row['groupCreated'] for row in plan['selected']))
            result = worker_sessions.handle(store, {'action': 'apply',
                'planDigest': plan['planDigest'], 'selectionDigest': plan['selectionDigest']})
            replay = worker_sessions.handle(store, {'action': 'apply',
                'planDigest': plan['planDigest'], 'selectionDigest': plan['selectionDigest']})
        self.assertEqual(result, replay)
        self.assertEqual(result['sessionsArchived'], 2)
        self.assertEqual(result['groupsRemoved'], 0)
        self.assertEqual(result['groupsRetained'], 1)
        self.assertEqual(members, ['unrelated'])
        self.assertEqual([action for action, _ in calls].count('archive-session'), 2)

    def test_lost_reply_replays_only_after_durable_intent(self):
        store = FakeStore(self.state, [dsh_row('owned', created=True)])
        members = ['owned']
        first = True

        def native(method, payload):
            nonlocal first
            if method == 'inspect-session':
                if not members:
                    raise BoardError('NATIVE_SESSION_UNAVAILABLE', 'already detached', reason='session-mismatch')
                return {'revision': 'r1', 'sessionIds': list(members)}
            if first:
                first = False
                members.clear()
                raise BoardError('NATIVE_SESSION_UNAVAILABLE', 'reply lost')
            self.assertTrue(payload['replay'])
            return {'sessionArchived': True, 'groupRemoved': False,
                    'groupRetainedReason': 'creation-unproven'}

        with mock.patch.object(worker_sessions, '_native', side_effect=native):
            plan = worker_sessions.handle(store, {'action': 'plan'})
            params = {'action': 'apply', 'planDigest': plan['planDigest'],
                      'selectionDigest': plan['selectionDigest']}
            first_reply = worker_sessions.handle(store, params)
            self.assertEqual(first_reply['sessionsArchived'], 0)
            reply = worker_sessions.handle(store, params)
        self.assertEqual(reply['sessionsArchived'], 1)
        self.assertEqual(reply['groupsRemoved'], 0)
        self.assertEqual(reply['groupsRetained'], 1)

    def test_linked_journal_is_refused(self):
        store = FakeStore(self.state, [dsh_row('owned')])
        with mock.patch.object(worker_sessions, '_native', return_value={'revision': 'r', 'sessionIds': ['owned']}):
            plan = worker_sessions.handle(store, {'action': 'plan'})
        root = self.state / 'worker-session-cleanup'
        root.mkdir()
        outside = self.state / 'outside.json'
        outside.write_text('{}')
        (root / f"{plan['planDigest']}.json").symlink_to(outside)
        with mock.patch.object(worker_sessions, '_native', return_value={'revision': 'r', 'sessionIds': ['owned']}):
            with self.assertRaises(BoardError) as raised:
                worker_sessions.handle(store, {'action': 'apply', 'planDigest': plan['planDigest'],
                                               'selectionDigest': plan['selectionDigest']})
        self.assertEqual(raised.exception.code, 'PRIVATE_PATH_UNSAFE')
        self.assertEqual(outside.read_text(), '{}')

    def test_backup_includes_only_regular_exact_journal_names(self):
        root = self.state / 'worker-session-cleanup'
        root.mkdir()
        good = root / ('a' * 64 + '.json')
        good.write_text('{}')
        other = root / 'notes.json'
        other.write_text('protected')
        entries = list(backup.preflight_entries(self.state))
        self.assertIn(('copied', good, 'durable-state'), entries)
        self.assertIn(('rejected', other, 'undeclared-journal-entry'), entries)

    def test_codex_list_accepts_actual_stopped_adapter_fixture(self):
        from test_codex import CodexAdapterTests
        fixture = CodexAdapterTests('test_completed_native_turn_has_structured_provenance_and_activity')
        fixture.setUp()
        try:
            with mock.patch.dict(os.environ, {'BUDDY_DEV_SOURCE': '1',
                                              'BUDDY_STATE_DIR': str(fixture.root / 'state'),
                                              'BUDDY_RUNTIME_ROOT': str(fixture.root / 'runtime'),
                                              'BUDDY_CODEX_CLI': str(fixture.environment['BUDDY_CODEX_CLI'])}):
                outcome = fixture.execute(fixture.context())
            self.assertTrue(outcome.shutdown_confirmed)
            receipt = {'result': outcome.result, 'shutdownConfirmed': True}
            row = {'adapter': 'codex', 'task_id': 'goal-1', 'attempt_id': 'attempt-1',
                   'cwd': str(fixture.cwd), 'execution_state': 'finished', 'shutdown_confirmed': 1,
                   'result_json': json.dumps(receipt)}
            with mock.patch.object(worker_sessions, '_native', side_effect=AssertionError('native called')):
                result = worker_sessions.handle(FakeStore(self.state, [row]), {'adapter': 'codex'})
            self.assertEqual(result['count'], 1)
            self.assertEqual(result['sessions'][0]['storageOwner'], 'buddy-goal')
            # Receipts before the checkpoint/nativeSession additions still have
            # a strictly correlated completed turn. Do not lose that history.
            legacy = json.loads(row['result_json'])
            legacy['result'].pop('nativeCheckpoint')
            legacy['result'].pop('nativeSession')
            legacy_row = {**row, 'result_json': json.dumps(legacy)}
            historical = worker_sessions.handle(FakeStore(self.state, [legacy_row]), {'adapter': 'codex'})
            self.assertEqual(historical['count'], 1)
            self.assertEqual(historical['sessions'][0]['storageOwner'], 'harness-user-store')
            legacy['result']['turn']['taskId'] = 'unrelated-goal'
            legacy_row['result_json'] = json.dumps(legacy)
            self.assertEqual(worker_sessions.handle(FakeStore(self.state, [legacy_row]), {'adapter': 'codex'})['count'], 0)
            resumed = dict(row)
            resumed['attempt_id'] = 'attempt-2'
            continued = json.loads(row['result_json'])
            continued['result']['nativeCheckpoint']['attemptId'] = 'attempt-2'
            continued['result']['nativeSession']['resumeMode'] = 'native-session'
            resumed['result_json'] = json.dumps(continued)
            deduped = worker_sessions.handle(FakeStore(self.state, [row, resumed]), {'adapter': 'codex'})
            self.assertEqual(deduped['count'], 1)
            self.assertEqual(deduped['sessions'][0]['attemptIds'], ['attempt-1', 'attempt-2'])
        finally:
            fixture.doCleanups()


if __name__ == '__main__':
    unittest.main()
