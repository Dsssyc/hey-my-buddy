"""Attempt-bound session reads over real authenticated HTTP, without a harness."""
import json
from unittest import mock

from console.test_console import Browser, ConsoleTestCase
from blackboard.tasks.test_workflow import WorkflowTestCase
from hey_my_buddy import private_dirs
from hey_my_buddy.blackboard.tasks import storage
from hey_my_buddy.buddy.harnesses.session_record import SessionRecord
from hey_my_buddy.buddy.harnesses.base import AdapterOutcome
from hey_my_buddy.buddy.harnesses.run_contract import (RunResult, RunEnd, RunIdentity, SessionRecordFailure, StopEvidence, StopLayer)
from hey_my_buddy.buddy.roles.run_execution import _base_result
from hey_my_buddy.console import session_content
from hey_my_buddy.protocol.session_content import SESSION_FORMAT, MessageChunk, TextContent


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
        header = self.line(SESSION_FORMAT.model_dump(mode='json', by_alias=True, exclude_none=True))
        if record:
            path.parent.mkdir(parents=True)
            path.write_bytes(header + b''.join(self.line(event) for event in events))
        _, browser = self.open_console(board)
        route = f'/api/tasks/{task_id}/attempts/{attempt_id}/session'
        return board, browser, route, path, events, len(header), run

    def line(self, value):
        return (json.dumps(value, ensure_ascii=False, separators=(',', ':')) + '\n').encode()

    def append(self, path, event):
        with path.open('ab') as stream:
            stream.write(self.line(event))

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
        expected_before = start + sum(len(self.line(event)) for event in events[:4])
        self.assertEqual((page['before'], page['after'], page['stopped']),
                         (expected_before, path.stat().st_size, None))
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

    def test_invalid_parameters_and_byte_positions_are_rejected(self):
        _, browser, route, path, _, start, _ = self.fixture(count=2)
        for query in ('before=1&after=1',
                      'after=-1', 'after=1.0', 'after=', 'before=1&before=1',
                      'thread=secret', 'path=secret', 'limit=0', 'limit=257', 'limit=1&limit=2',
                      'after=9223372036854775808'):
            with self.subTest(query=query):
                self.get(browser, route + '?' + query, status=400, code='INVALID_ARGUMENT')
        for query in (f'after={start - 1}', f'before={path.stat().st_size + 1}',
                      f'after={start + 1}'):
            with self.subTest(query=query):
                self.get(browser, route + '?' + query, status=400, code='INVALID_ARGUMENT')

    def finish_recording_failure(self, board, run, path, error):
        record = SessionRecord(path, 'session-attempt')
        with mock.patch.object(record, '_write', side_effect=error):
            record.append(MessageChunk(session_update='agent_message_chunk', content=TextContent(text='display')))
        record.close()
        native = RunResult(
            identity=RunIdentity(task_id=run['runId'], attempt_id='session-attempt', generation=1,
                                 invocation_id='session-read-result'), harness='codex',
            end=RunEnd(status='ok', native_exit_code=0),
            stop_evidence=StopEvidence(native=StopLayer(group_state='gone')),
            session_record_failure=SessionRecordFailure.from_payload(record.failure))
        report = AdapterOutcome(status='ok', result=_base_result(native), exit_code=0,
                                shutdown_confirmed=True).to_report()
        with board.store.db.write() as connection:
            connection.execute("UPDATE attempts SET execution_state='finished', result_json=?",
                               (json.dumps(report),))
        return native.session_record_failure.to_payload()

    def test_cursor_survives_append_and_finish_with_stopped_reason(self):
        board, browser, route, path, events, _, run = self.fixture(count=1)
        cursor = self.get(browser, route)['after']
        appended = self.event('session-attempt', '原生路径 /workspace/src/code.py')
        held = self.get(browser, route + f'?after={cursor}')
        self.assertEqual((held['events'], held['after'], held['state']), ([], cursor, 'running'))
        self.assertEqual(self.get(browser, route)['events'], events)
        self.append(path, appended)
        stopped = self.finish_recording_failure(board, run, path, OSError('file write unavailable'))
        ended = self.get(browser, route + f'?after={cursor}')
        self.assertEqual((ended['events'], ended['state'], ended['stopped']), ([appended], 'ended', stopped))

    def test_empty_and_partial_header_pages_keep_polling_until_events_arrive(self):
        _, browser, route, path, _, _, _ = self.fixture()
        for body in (b'', b'{"protocol":'):
            with self.subTest(body=body):
                path.write_bytes(body)
                page = self.get(browser, route)
                self.assertEqual((page['events'], page['before'], page['after'], page['stopped']),
                                 ([], 0, 0, None))
                self.assertEqual(self.get(browser, route + '?after=0')['events'], [])
        event = self.event('session-attempt', 'first complete event')
        path.write_bytes(self.line(SESSION_FORMAT.to_payload()) + self.line(event))
        self.assertEqual(self.get(browser, route + '?after=0')['events'], [event])

    def assert_failure_before_first_line(self, kind):
        board, browser, route, path, _, _, run = self.fixture(record=False)
        if kind == 'linked-parent':
            outside = board.directory / 'outside-session'
            outside.mkdir()
            path.parent.parent.mkdir(parents=True, exist_ok=True)
            path.parent.symlink_to(outside, target_is_directory=True)
            error = OSError('linked session directory')
        else:
            error = OSError('file could not be opened')
        with mock.patch('hey_my_buddy.buddy.harnesses.session_record.open_regular_fd', side_effect=error):
            stopped = self.finish_recording_failure(board, run, path, error)
        if kind == 'linked-parent':
            self.get(browser, route, status=500, code='SESSION_UNREADABLE')
        else:
            page = self.get(browser, route)
            self.assertEqual((page['events'], page['before'], page['after'], page['stopped']),
                             ([], 0, 0, stopped))
        self.assertEqual(set(stopped), {'code', 'params'})
        self.assertEqual(stopped['params']['category'], 'BoardError' if kind == 'linked-parent' else 'OSError')
        if kind == 'linked-parent':
            self.assertEqual(list(outside.iterdir()), [])

    def test_missing_record_with_failure_returns_an_empty_stopped_page(self):
        self.assert_failure_before_first_line('missing')

    def test_linked_directory_with_failure_is_unreadable(self):
        self.assert_failure_before_first_line('linked-parent')

    def test_stopped_record_permission_failure_is_unreadable(self):
        board, browser, route, path, _, _, run = self.fixture(record=False)
        self.finish_recording_failure(board, run, path, OSError('file write unavailable'))
        with mock.patch.object(private_dirs, 'open_regular_fd', side_effect=PermissionError('private path')):
            error = self.get(browser, route, status=500, code='SESSION_UNREADABLE')
        self.assertNotIn('private path', json.dumps(error))

    def test_snapshot_excludes_appends_after_the_size_was_fixed(self):
        _, browser, route, path, events, _, _ = self.fixture(count=1)
        original = session_content._last_newline
        appended = self.event('session-attempt', 'later')
        injected = False
        def snapshot(*args):
            nonlocal injected
            if not injected:
                self.append(path, appended)
                injected = True
            return original(*args)
        with mock.patch.object(session_content, '_last_newline', side_effect=snapshot):
            self.assertEqual(self.get(browser, route)['events'], events)
        self.assertEqual(self.get(browser, route)['events'][-1], appended)

    def test_partial_final_line_is_ignored_until_writer_recovers(self):
        _, browser, route, path, events, _, _ = self.fixture(count=1)
        cursor = self.get(browser, route)['after']
        with path.open('ab') as stream:
            stream.write(b'{"sessionId":"interrupted"')
        page = self.get(browser, route)
        self.assertEqual((page['events'], page['after']), (events, cursor))
        self.assertEqual(self.get(browser, route + f'?after={cursor}')['events'], [])
        resumed = self.event('session-attempt', 'after recovery')
        record = SessionRecord(path, 'session-attempt')
        record.append(MessageChunk(session_update='agent_message_chunk',
                                   content=TextContent(text='after recovery')))
        record.close()
        self.assertIsNone(record.failure)
        page = self.get(browser, route + f'?after={cursor}')
        self.assertEqual(page['events'], [resumed])

    def test_empty_record_and_empty_after_initial_position(self):
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

    def test_bad_json_header_and_event_shapes_fail_without_source_paths(self):
        _, browser, route, path, _, _, _ = self.fixture()
        header = self.line(SESSION_FORMAT.model_dump(mode='json', by_alias=True, exclude_none=True))
        for content in ('not-json', '{"sessionId":"x","sessionId":"y","update":{}}',
                        '{"sessionId":"x","update":{"sessionUpdate":"unknown"}}',
                        '{"private":"/secret/credentials"}', '{"value":NaN}'):
            with self.subTest(content=content):
                path.write_bytes(header + content.encode() + b'\n')
                error = self.get(browser, route, status=422, code='SESSION_INVALID_RECORD')
                self.assertEqual(error['error']['details']['reason']['code'], 'session.invalid-record')
                self.assertNotIn('/secret/credentials', json.dumps(error))
        path.write_bytes(self.line({'format': 'unexpected'}) + self.line(self.event('session-attempt', 'ok')))
        self.get(browser, route, status=422, code='SESSION_INVALID_RECORD')
        bad_version = {**SESSION_FORMAT.model_dump(mode='json', by_alias=True), 'projectionVersion': 2}
        path.write_bytes(self.line(bad_version) + self.line(self.event('session-attempt', 'ok')))
        self.get(browser, route, status=422, code='SESSION_INVALID_RECORD')

    def test_missing_record_is_not_recorded(self):
        board, browser, route, _, _, _, _ = self.fixture(record=False)
        error = self.get(browser, route, status=404, code='SESSION_NOT_RECORDED')
        self.assertEqual(error['error']['details']['reason']['code'], 'session.not-recorded')

    def test_attempt_without_a_micro_task_group_has_no_session_record(self):
        board, browser, _, _, _, _, run = self.fixture(record=False)
        plain_task, plain_attempt = 'plain-execution', 'plain-attempt'
        with board.store.db.write() as connection:
            row = dict(connection.execute('SELECT * FROM tasks WHERE task_id=?', (run['runId'],)).fetchone())
            row.update(task_id=plain_task, request_id='plain-execution-request',
                       selected_attempt_id=None, active_attempt_id=None)
            columns = tuple(row)
            connection.execute(f"INSERT INTO tasks({','.join(columns)}) VALUES({','.join('?' for _ in columns)})",
                               tuple(row.values()))
            now = board.store.now()
            connection.execute('INSERT INTO attempts(attempt_id,task_id,generation,nonce_verifier,'
                               'execution_state,adapter,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)',
                               (plain_attempt, plain_task, 1, 'fixture', 'executing', 'codex', now, now))
        route = f'/api/tasks/{plain_task}/attempts/{plain_attempt}/session'
        self.get(browser, route, status=404, code='SESSION_NOT_RECORDED')

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
            connection.execute('INSERT INTO macro_task_deletions VALUES(?,?,?)',
                               ('delete-test', 'macro-task' if macro else 'micro-task-tree', target))
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

    def test_linked_file_and_parent_are_refused_without_reading_outside_content(self):
        board, browser, route, path, _, _, _ = self.fixture(count=1)
        outside = board.directory / 'outside-session-read'
        outside.mkdir()
        target = outside / path.name
        expected = path.read_bytes() + self.line(self.event('session-attempt', 'credentials must stay private'))
        target.write_bytes(expected)
        path.unlink()
        path.symlink_to(target)
        error = self.get(browser, route, status=500, code='SESSION_UNREADABLE')
        self.assertNotIn('credentials must stay private', json.dumps(error))
        self.assertEqual(target.read_bytes(), expected)
        path.unlink()
        path.parent.rmdir()
        path.parent.symlink_to(outside, target_is_directory=True)
        error = self.get(browser, route, status=500, code='SESSION_UNREADABLE')
        self.assertNotIn('credentials must stay private', json.dumps(error))
        self.assertEqual(target.read_bytes(), expected)
        with mock.patch.object(private_dirs, 'open_regular_fd', side_effect=OSError(str(target))):
            self.get(browser, route, status=500, code='SESSION_UNREADABLE')

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
