"""Attempt-bound session reads over real authenticated HTTP, without a harness."""
import json
import sqlite3
from contextlib import closing
from unittest import mock

from console.test_console import Browser, ConsoleTestCase
from blackboard.tasks.test_workflow import WorkflowTestCase
from hey_my_buddy import private_dirs
from hey_my_buddy.blackboard.tasks import storage
from hey_my_buddy.console import session_content


class SessionContentTests(ConsoleTestCase, WorkflowTestCase):
    def fixture(self, *, count=0, harness='codex', record=True, macro=False):
        board = self.board()
        run = self.submit(board, **({'macroTask': {'title': 'Session fixture'}} if macro else {}))
        task_id, attempt_id = run['runId'], 'session-attempt'
        now = board.store.now()
        with board.store.db.write() as connection:
            connection.execute('INSERT INTO attempts(attempt_id,task_id,generation,nonce_verifier,'
                               'execution_state,adapter,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)',
                               (attempt_id, task_id, 1, 'fixture', 'executing', harness, now, now))
        group_id = run.get('macroTaskId') or 'run:' + task_id
        path = private_dirs.session_record_path(board.directory, group_id, attempt_id)
        events = [self.event(attempt_id, f'消息 {i}') for i in range(count)]
        if record:
            path.parent.mkdir(parents=True)
            with closing(sqlite3.connect(path)) as connection, connection:
                connection.execute('PRAGMA journal_mode=WAL')
                connection.execute('CREATE TABLE events(seq INTEGER PRIMARY KEY, notification TEXT NOT NULL)')
                connection.execute('PRAGMA user_version=1')
                connection.executemany('INSERT INTO events(notification) VALUES(?)',
                                       [(json.dumps(event),) for event in events])
        _, browser = self.open_console(board)
        route = f'/api/tasks/{task_id}/attempts/{attempt_id}/session'
        return board, browser, route, path, events, 0, run

    def append(self, path, event):
        with closing(sqlite3.connect(path)) as connection, connection:
            connection.execute('INSERT INTO events(notification) VALUES(?)', (json.dumps(event),))

    def event(self, attempt_id, text):
        return {'sessionId': attempt_id, 'update': {'sessionUpdate': 'agent_message_chunk',
                                                  'content': {'type': 'text', 'text': text}}}

    def get(self, browser, route, *, status=200, code=None):
        actual, headers, raw = browser.get(route)
        self.assertEqual(actual, status, raw[:400])
        self.assertEqual(headers['cache-control'], 'no-store')
        body = json.loads(raw)
        if code is not None:
            self.assertEqual(body['error']['code'], code)
        return body

    def test_latest_before_to_start_and_after_to_empty_preserve_positions(self):
        _, browser, route, path, events, start, _ = self.fixture(count=7)
        page = self.get(browser, route + '?limit=3')
        self.assertEqual(set(page), {'state', 'stopped', 'omitted', 'events', 'before', 'after', 'hasEarlier'})
        self.assertEqual(page['events'], events[4:])
        self.assertEqual((page['before'], page['after'], page['stopped']), (4, 7, None))
        self.assertTrue(page['hasEarlier'])
        earlier = self.get(browser, route + f'?before={page["before"]}&limit=3')
        first = self.get(browser, route + f'?before={earlier["before"]}&limit=3')
        self.assertEqual(first['events'] + earlier['events'] + page['events'], events)
        self.assertEqual(first['before'], start)
        self.assertFalse(first['hasEarlier'])
        empty = self.get(browser, route + f'?before={start}')
        self.assertEqual((empty['events'], empty['before'], empty['after']), ([], start, start))
        cursor, found = start, []
        while True:
            next_page = self.get(browser, route + f'?after={cursor}&limit=2')
            found.extend(next_page['events'])
            if not next_page['events']:
                self.assertEqual((next_page['before'], next_page['after']), (cursor, cursor))
                break
            cursor = next_page['after']
        self.assertEqual(found, events)

    def test_default_and_maximum_page_sizes(self):
        _, browser, route, _, events, _, _ = self.fixture(count=300)
        self.assertEqual(self.get(browser, route)['events'], events[-100:])
        self.assertEqual(self.get(browser, route + '?limit=256')['events'], events[-256:])

    def test_invalid_parameters_are_rejected_and_outside_positions_are_empty(self):
        _, browser, route, path, _, start, _ = self.fixture(count=2)
        for query in ('before=1&after=1',
                      'after=-1', 'after=1.0', 'after=', 'before=1&before=1',
                      'thread=secret', 'path=secret', 'limit=0', 'limit=257', 'limit=1&limit=2',
                      'after=9223372036854775808'):
            with self.subTest(query=query):
                self.get(browser, route + '?' + query, status=400, code='INVALID_ARGUMENT')
        self.assertEqual(self.get(browser, route + '?after=100')['events'], [])
        self.assertEqual(self.get(browser, route + '?before=0')['events'], [])

    def test_cursor_survives_append_and_finish_with_stopped_reason(self):
        board, browser, route, path, events, _, _ = self.fixture(count=1)
        cursor = self.get(browser, route)['after']
        appended = self.event('session-attempt', '原生路径 /workspace/src/code.py')
        held = self.get(browser, route + f'?after={cursor}')
        self.assertEqual((held['events'], held['after'], held['state']), ([], cursor, 'running'))
        self.assertEqual(self.get(browser, route)['events'], events)
        self.append(path, appended)
        stopped = {'code': 'session-record-write-failed', 'message': 'Could not write the attempt session record', 'cause': 'OSError'}
        with board.store.db.write() as connection:
            connection.execute("UPDATE attempts SET execution_state='finished', result_json=?",
                               (json.dumps({'sessionRecordFailure': stopped}),))
        ended = self.get(browser, route + f'?after={cursor}')
        self.assertEqual((ended['events'], ended['state'], ended['stopped']), ([appended], 'ended', stopped))

    def test_snapshot_excludes_appends_after_query(self):
        _, browser, route, path, events, _, _ = self.fixture(count=1)
        original = session_content._page
        appended = self.event('session-attempt', 'later')
        injected = False
        def snapshot(*args):
            nonlocal injected
            page = original(*args)
            if not injected:
                self.append(path, appended)
                injected = True
            return page
        with mock.patch.object(session_content, '_page', side_effect=snapshot):
            self.assertEqual(self.get(browser, route)['events'], events)
        self.assertEqual(self.get(browser, route)['events'][-1], appended)

    def test_empty_database_and_empty_after_initial_position(self):
        _, browser, route, _, _, start, _ = self.fixture()
        page = self.get(browser, route)
        self.assertEqual((page['events'], page['before'], page['after'], page['hasEarlier']),
                         ([], start, start, False))
        self.assertEqual(self.get(browser, route + f'?after={start}'), page)

    def test_very_long_event_is_returned_whole_with_omitted_reason(self):
        _, browser, route, path, _, _, _ = self.fixture()
        event = self.event('session-attempt', '内容 /workspace/src/large.py\n' * 10000)
        self.append(path, event)
        page = self.get(browser, route)
        self.assertEqual(page['events'], [event])
        self.assertEqual(page['omitted'], [{'code': 'session.codex-internal-reasoning-omitted', 'params': {}}])

    def test_bad_json_version_and_event_shapes_fail_without_source_paths(self):
        _, browser, route, path, _, _, _ = self.fixture()
        for content in ('not-json', '{"sessionId":"x","sessionId":"y","update":{}}',
                        '{"sessionId":"x","update":{"sessionUpdate":"unknown"}}',
                        '{"private":"/secret/credentials"}', '{"value":NaN}'):
            with self.subTest(content=content):
                with closing(sqlite3.connect(path)) as connection, connection:
                    connection.execute('DELETE FROM events')
                    connection.execute('INSERT INTO events(notification) VALUES(?)', (content,))
                error = self.get(browser, route, status=422, code='SESSION_INVALID_RECORD')
                self.assertEqual(error['error']['details']['reason']['code'], 'session.invalid-record')
                self.assertNotIn('/secret/credentials', json.dumps(error))
        with closing(sqlite3.connect(path)) as connection, connection:
            connection.execute('PRAGMA user_version=2')
        self.get(browser, route, status=422, code='SESSION_INVALID_RECORD')

    def test_missing_record_is_not_recorded(self):
        board, browser, route, _, _, _, _ = self.fixture(record=False)
        error = self.get(browser, route, status=404, code='SESSION_NOT_RECORDED')
        self.assertEqual(error['error']['details']['reason']['code'], 'session.not-recorded')

    def test_claude_reads_its_attempt_record_and_missing_history_is_not_recorded(self):
        board, browser, route, path, events, _, _ = self.fixture(count=2, harness='claude')
        page = self.get(browser, route)
        self.assertEqual((page['events'], page['omitted'], page['state']), (events, [], 'running'))
        cursor = page['after']
        appended = self.event('session-attempt', 'Claude Code 追加')
        self.append(path, appended)
        with board.store.db.write() as connection:
            connection.execute("UPDATE attempts SET execution_state='finished'")
        ended = self.get(browser, route + f'?after={cursor}')
        self.assertEqual((ended['events'], ended['state']), ([appended], 'ended'))
        path.unlink()
        self.get(browser, route, status=404, code='SESSION_NOT_RECORDED')

    def test_dsh_reads_its_attempt_record_and_missing_history_is_not_recorded(self):
        board, browser, route, path, events, _, _ = self.fixture(count=2, harness='dsh')
        page = self.get(browser, route)
        self.assertEqual(page['events'], events)
        self.assertEqual(page['omitted'], [])
        self.assertEqual(page['state'], 'running')
        cursor = page['after']
        appended = self.event('session-attempt', 'DSH 追加')
        self.append(path, appended)
        with board.store.db.write() as connection:
            connection.execute("UPDATE attempts SET execution_state='finished'")
        ended = self.get(browser, route + f'?after={cursor}')
        self.assertEqual((ended['events'], ended['state']), ([appended], 'ended'))
        path.unlink()
        self.get(browser, route, status=404, code='SESSION_NOT_RECORDED')

    def test_zcode_reads_its_attempt_record_and_missing_history_is_not_recorded(self):
        board, browser, route, path, events, _, _ = self.fixture(count=2, harness='zcode')
        page = self.get(browser, route)
        self.assertEqual((page['events'], page['omitted'], page['state']), (events, [], 'running'))
        cursor = page['after']
        appended = self.event('session-attempt', 'ZCode 追加')
        self.append(path, appended)
        with board.store.db.write() as connection:
            connection.execute("UPDATE attempts SET execution_state='finished'")
        ended = self.get(browser, route + f'?after={cursor}')
        self.assertEqual((ended['events'], ended['state']), ([appended], 'ended'))
        path.unlink()
        self.get(browser, route, status=404, code='SESSION_NOT_RECORDED')

    def test_authentication_host_origin_and_private_url_are_checked_first(self):
        _, browser, route, _, _, _, _ = self.fixture(count=1)
        stranger = Browser(browser.origin + browser.prefix, redeem=False)
        with mock.patch.object(session_content, 'read', side_effect=AssertionError('unauthenticated read')):
            self.get(stranger, route, status=401, code='CONSOLE_SESSION_EXPIRED')
            for headers in ({'Host': 'evil.example'}, {'Origin': 'https://evil.example'}):
                status, response, _ = browser.get(route, headers=headers)
                self.assertEqual(status, 403)
                self.assertEqual(response['cache-control'], 'no-store')
        self.get(browser, route)
        status, headers, _ = browser.get(route, headers={'If-None-Match': '*'})
        self.assertEqual((status, headers['cache-control']), (200, 'no-store'))

    def test_task_attempt_binding_is_checked(self):
        _, browser, route, _, _, _, _ = self.fixture(count=1)
        self.get(browser, route.replace('session-attempt', 'unrelated'), status=404, code='NOT_FOUND')
        self.get(browser, '/api/tasks/other/attempts/session-attempt/session', status=404, code='NOT_FOUND')

    def marked(self, macro):
        board, browser, route, _, _, _, run = self.fixture(macro=macro)
        target = run.get('macroTaskId') or 'run:' + run['runId']
        with board.store.db.write() as connection:
            connection.execute('INSERT INTO macro_task_deletions VALUES(?,?,?,?,?,?,?)',
                               ('delete-test', 'macro-task' if macro else 'micro-task-tree', target,
                                'deleting', board.store.now(), None, 1))
        with mock.patch.object(session_content, 'session_projector', side_effect=AssertionError('projection read')):
            self.get(browser, route, status=410, code='SESSION_DELETED')

    def test_standalone_deletion_marker_precedes_file_and_projection_reads(self):
        self.marked(False)

    def test_macro_deletion_marker_precedes_file_and_projection_reads(self):
        self.marked(True)

    def test_file_disappearing_during_read_is_deleted(self):
        _, browser, route, path, _, _, _ = self.fixture(count=1)
        original = session_content._page
        def remove(connection, params):
            page = original(connection, params)
            path.unlink()
            return page
        with mock.patch.object(session_content, '_page', side_effect=remove):
            self.get(browser, route, status=410, code='SESSION_DELETED')

    def test_linked_file_and_io_errors_do_not_leak_private_paths(self):
        board, browser, route, path, _, _, _ = self.fixture(count=1)
        target = board.directory / 'credential-secret'
        target.write_text('credentials must stay private')
        path.unlink()
        path.symlink_to(target)
        error = self.get(browser, route, status=500, code='SESSION_UNREADABLE')
        with mock.patch.object(private_dirs, 'open_regular_fd', side_effect=OSError(str(target))):
            error = self.get(browser, route, status=500, code='SESSION_UNREADABLE')

    def test_session_bytes_are_separate_and_ordinary_storage_apply_retains_them(self):
        board, browser, route, path, events, _, run = self.fixture(count=2)
        payload = private_dirs.attempt_root(board.directory, 'codex', run['runId'], 'session-attempt') / 'other-evidence'
        payload.parent.mkdir(parents=True)
        payload.write_bytes(b'other')
        with mock.patch.object(storage, 'process_inventory', return_value=([], [], True)), \
             mock.patch.object(storage, '_zcode_reasons', return_value=[]):
            plan = board.call('storage_plan', {})
            session = next(row for row in plan['candidates'] if row['category'] == 'session-records')
            parent = next(row for row in plan['candidates'] if row['category'] == 'harnesses')
            self.assertEqual((session['bytes'], session['groupId']),
                             (path.stat().st_size, 'run:' + run['runId']))
            self.assertEqual(session['reasons'], ['macro-task-deletion'])
            self.assertEqual(parent['bytes'], len(b'other'))
            self.assertNotIn('session-records-retained', parent['reasons'])
            board.call('storage_apply', {'planId': plan['planId'], 'commandId': 'keep-session', 'confirm': True})
        self.assertEqual(self.get(browser, route)['events'], events)
