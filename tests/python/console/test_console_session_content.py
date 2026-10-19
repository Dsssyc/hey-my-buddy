"""Attempt-bound session reads over real authenticated HTTP, without a harness."""
import json
from unittest import mock

from console.test_console import Browser, ConsoleTestCase
from blackboard.tasks.test_workflow import WorkflowTestCase
from hey_my_buddy import private_dirs
from hey_my_buddy.blackboard.tasks import storage
from hey_my_buddy.console import session_content
from hey_my_buddy.protocol.session_content import SESSION_FORMAT


def line(value):
    return (json.dumps(value, ensure_ascii=False, separators=(',', ':')) + '\n').encode()


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
        path = private_dirs.attempt_root(board.directory, harness, task_id, attempt_id) / 'session.jsonl'
        events = [self.event(attempt_id, f'消息 {i}') for i in range(count)]
        header = line(SESSION_FORMAT.model_dump(by_alias=True))
        if record:
            path.parent.mkdir(parents=True)
            path.write_bytes(header + b''.join(line(event) for event in events))
        _, browser = self.open_console(board)
        route = f'/api/tasks/{task_id}/attempts/{attempt_id}/session'
        return board, browser, route, path, events, len(header), run

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

    def test_latest_before_to_start_and_after_to_empty_preserve_byte_offsets(self):
        _, browser, route, path, events, start, _ = self.fixture(count=7)
        page = self.get(browser, route + '?limit=3')
        self.assertEqual(set(page), {'state', 'omitted', 'events', 'before', 'after', 'hasEarlier'})
        self.assertEqual(page['events'], events[4:])
        self.assertEqual(page['after'], path.stat().st_size)
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

    def test_invalid_parameters_and_nonboundary_offsets_are_rejected(self):
        _, browser, route, path, _, start, _ = self.fixture(count=2)
        for query in ('before=0', f'after={start - 1}', f'before={start + 1}',
                      f'after={path.stat().st_size + 1}', 'before=1&after=1',
                      'after=-1', 'after=1.0', 'after=', 'before=1&before=1',
                      'thread=secret', 'path=secret', 'limit=0', 'limit=257', 'limit=1&limit=2'):
            with self.subTest(query=query):
                self.get(browser, route + '?' + query, status=400, code='INVALID_ARGUMENT')

    def test_partial_tail_is_held_until_newline_and_cursor_survives_finish(self):
        board, browser, route, path, events, _, _ = self.fixture(count=1)
        cursor = self.get(browser, route)['after']
        appended = self.event('session-attempt', '原生路径 /workspace/src/code.py')
        with path.open('ab') as stream:
            stream.write(line(appended)[:-1])
        held = self.get(browser, route + f'?after={cursor}')
        self.assertEqual((held['events'], held['after'], held['state']), ([], cursor, 'running'))
        self.assertEqual(self.get(browser, route)['events'], events)
        with path.open('ab') as stream:
            stream.write(b'\n')
        with board.store.db.write() as connection:
            connection.execute("UPDATE attempts SET execution_state='finished'")
        ended = self.get(browser, route + f'?after={cursor}')
        self.assertEqual((ended['events'], ended['state']), ([appended], 'ended'))

    def test_snapshot_excludes_appends_after_the_size_was_fixed(self):
        _, browser, route, path, events, _, _ = self.fixture(count=1)
        original = session_content._last_newline
        appended = self.event('session-attempt', 'later')
        injected = False
        def snapshot(*args):
            nonlocal injected
            if not injected:
                with path.open('ab') as stream:
                    stream.write(line(appended))
                injected = True
            return original(*args)
        with mock.patch.object(session_content, '_last_newline', side_effect=snapshot):
            self.assertEqual(self.get(browser, route)['events'], events)
        self.assertEqual(self.get(browser, route)['events'][-1], appended)

    def test_header_only_and_empty_after_at_first_event(self):
        _, browser, route, _, _, start, _ = self.fixture()
        page = self.get(browser, route)
        self.assertEqual((page['events'], page['before'], page['after'], page['hasEarlier']),
                         ([], start, start, False))
        self.assertEqual(self.get(browser, route + f'?after={start}'), page)

    def test_very_long_event_is_returned_whole_with_omitted_reason(self):
        _, browser, route, path, _, _, _ = self.fixture()
        event = self.event('session-attempt', '内容 /workspace/src/large.py\n' * 10000)
        with path.open('ab') as stream:
            stream.write(line(event))
        page = self.get(browser, route)
        self.assertEqual(page['events'], [event])
        self.assertEqual(page['omitted'], [{'code': 'session.codex-internal-reasoning-omitted', 'params': {}}])
        self.assertNotIn(str(path.parent), json.dumps(page))

    def test_bad_json_version_and_event_shapes_fail_without_source_paths(self):
        _, browser, route, path, _, _, _ = self.fixture()
        header = path.read_bytes()
        for content in (b'not-json\n', header.replace(b'"projectionVersion":1', b'"projectionVersion":2'),
                        header + b'{"sessionId":"x","sessionId":"y","update":{}}\n',
                        header + b'{"sessionId":"x","update":{"sessionUpdate":"unknown"}}\n',
                        header + b'{"private":"/secret/credentials"}\n',
                        header + b'{"value":NaN}\n'):
            with self.subTest(content=content):
                path.write_bytes(content)
                error = self.get(browser, route, status=422, code='SESSION_INVALID_RECORD')
                self.assertEqual(error['error']['details']['reason']['code'], 'session.invalid-record')
                self.assertNotIn(str(path), json.dumps(error))
                self.assertNotIn('/secret/credentials', json.dumps(error))

    def test_not_recorded_and_not_integrated_are_distinct(self):
        board, browser, route, _, _, _, _ = self.fixture(record=False)
        error = self.get(browser, route, status=404, code='SESSION_NOT_RECORDED')
        self.assertEqual(error['error']['details']['reason']['code'], 'session.not-recorded')
        for harness in ('dsh', 'claude', 'zcode'):
            with self.subTest(harness=harness):
                with board.store.db.write() as connection:
                    connection.execute('UPDATE attempts SET adapter=?', (harness,))
                error = self.get(browser, route, status=501, code='SESSION_NOT_INTEGRATED')
                self.assertEqual(error['error']['details']['reason']['code'], 'session.not-integrated')

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
        def remove(stream, params):
            page = original(stream, params)
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
        self.assertNotIn(str(target), json.dumps(error))
        self.assertNotIn(str(path.parent), json.dumps(error))
        with mock.patch.object(private_dirs, 'open_regular_fd', side_effect=OSError(str(target))):
            error = self.get(browser, route, status=500, code='SESSION_UNREADABLE')
        self.assertNotIn(str(target), json.dumps(error))

    def test_session_bytes_are_separate_and_ordinary_storage_apply_retains_them(self):
        board, browser, route, path, events, _, run = self.fixture(count=2)
        payload = path.parent / 'other-evidence'
        payload.write_bytes(b'other')
        with mock.patch.object(storage, 'process_inventory', return_value=([], [], True)), \
             mock.patch.object(storage, '_zcode_reasons', return_value=[]):
            plan = board.call('storage_plan', {})
            session = next(row for row in plan['candidates'] if row['category'] == 'session-records')
            parent = next(row for row in plan['candidates'] if row['category'] == 'harnesses')
            self.assertEqual((session['bytes'], session['taskId'], session['runId']),
                             (path.stat().st_size, run['runId'], run['runId']))
            self.assertEqual(session['reasons'], ['macro-task-deletion'])
            self.assertEqual(parent['bytes'], len(b'other'))
            self.assertFalse(parent['eligible'])
            self.assertIn('session-records-retained', parent['reasons'])
            board.call('storage_apply', {'planId': plan['planId'], 'commandId': 'keep-session', 'confirm': True})
        self.assertEqual(self.get(browser, route)['events'], events)
