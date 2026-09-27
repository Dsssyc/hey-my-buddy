"""Real HTTP coverage for one-time entry and single-writer browser sessions."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import http.cookiejar
import json
import select
import signal
import subprocess
import sys
import threading
from unittest import mock
import urllib.request
from urllib.parse import urlsplit

from buddy.console_sessions import MAX_ENTRIES, MAX_SESSIONS
from buddy.errors import BoardError
from test_console import Browser, ConsoleTestCase, http_call
from support import _child_environment


class ConsoleSessionTests(ConsoleTestCase):
    def timed_board(self):
        board = self.board()
        now = [1000.0]
        board.console._clock = lambda: now[0]
        return board, now

    def test_ticket_is_one_use_and_clean_url_requires_cookie(self):
        board = self.board()
        entry, browser = self.open_console(board, assets=True)
        ticket = urlsplit(entry['url']).path
        self.assertEqual(browser.call('GET', ticket)[0], 410)
        self.assertNotIn(ticket.removeprefix('/launch/'), browser.prefix)
        self.assertNotIn(browser.cookie.split('=', 1)[1], browser.prefix)
        copied = Browser(browser.origin + browser.prefix + '/')
        for route in ('/', '/api/console', '/api/tasks', '/assets/app.js'):
            self.assertEqual(copied.get(route)[0], 401, route)
            self.assertEqual(browser.get(route)[0], 200, route)
        self.assertNotIn('url', board.call('console', {'action': 'status'}))

    def test_expired_or_unredeemed_ticket_does_not_take_write_access(self):
        board, now = self.timed_board()
        _, first = self.open_console(board)
        pending = board.call('console', {'action': 'open'})
        self.assertTrue(first.bootstrap()['consoleSession']['canWrite'])
        now[0] += 60
        self.assertEqual(first.call('GET', urlsplit(pending['url']).path)[0], 410)
        self.assertTrue(first.bootstrap()['consoleSession']['canWrite'])

    def test_two_redemptions_race_for_exactly_one_session(self):
        board = self.board()
        entry = board.call('console', {'action': 'open'})
        parts = urlsplit(entry['url'])
        barrier = threading.Barrier(2)
        def redeem():
            barrier.wait(timeout=5)
            return http_call(parts.hostname, parts.port, 'GET', parts.path)[0]
        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertEqual(sorted(pool.map(lambda _: redeem(), range(2))), [303, 410])
        self.assertEqual(board.console.status()['sessionCount'], 1)

    def test_shared_cookie_jar_preserves_old_read_only_session_after_new_open(self):
        board = self.board()
        entry, _ = self.open_console(board, assets=True)
        # Two CLI entries in one browser cookie jar reproduce shared-tab cookies.
        jar = http.cookiejar.CookieJar()
        opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
        first = opener.open(board.call('console', {'action': 'open'})['url'], timeout=5).geturl()
        old_snapshot = json.load(opener.open(first + 'api/console', timeout=5))
        second = opener.open(board.call('console', {'action': 'open'})['url'], timeout=5).geturl()
        new_snapshot = json.load(opener.open(second + 'api/console', timeout=5))
        reread = json.load(opener.open(first + 'api/console', timeout=5))
        self.assertNotEqual(first, second)
        self.assertEqual(len(list(jar)), 2)
        self.assertFalse(reread['consoleSession']['canWrite'])
        self.assertEqual(reread['consoleSession']['reason'], 'superseded')
        self.assertEqual(old_snapshot['csrfToken'], reread['csrfToken'])
        self.assertNotEqual(reread['csrfToken'], new_snapshot['csrfToken'])
        self.assertTrue(new_snapshot['consoleSession']['canWrite'])

    def queued_task(self, board):
        return board.call('task_submit', {'requestId': 'console-session-task', 'task': 'still pending',
            'cwd': str(self.workdir()), 'adapter': 'command', 'argv': ['/bin/echo', 'ok']})['task']['runId']

    def test_old_session_can_browse_but_cannot_mutate_or_reuse_old_grant(self):
        board = self.board()
        run_id = self.queued_task(board)
        _, first = self.open_console(board)
        csrf = first.bootstrap()['csrfToken']
        status, _, raw = first.command('evaluation_write_begin',
            {'requestId': 'old-grant', 'expectedRevision': 0, 'kind': 'human'}, csrf=csrf)
        self.assertEqual(status, 200)
        grant = json.loads(raw)['result']
        _, second = self.open_console(board)
        for operation, params in (
            ('objective_stop', {'objectiveId': 'run:' + run_id, 'commandId': 'stale-stop'}),
            ('model_catalog_refresh', {'requestId': 'old-refresh'}),
            ('evaluation_write_renew', {k: grant[k] for k in ('writerId','generation','writerToken')}),
        ):
            status, _, raw = first.command(operation, params, csrf=csrf)
            self.assertEqual(status, 403)
            self.assertEqual(json.loads(raw)['error']['code'], 'CONSOLE_READ_ONLY')
        self.assertEqual(first.get('/api/tasks')[0], 200)
        self.assertEqual(first.command('evaluation_history', {}, csrf=csrf)[0], 200)
        self.assertEqual(first.command('selection_list', {}, csrf=csrf)[0], 200)
        self.assertEqual(first.command('model_profiles', {}, csrf=csrf)[0], 200)
        self.assertEqual(board.call('task_get', {'runId': run_id})['task']['status'], 'queued')
        self.assertEqual(second.command('task_cancel', {'runId': run_id}, csrf=second.bootstrap()['csrfToken'])[0], 404)

    def test_late_request_body_cannot_cross_a_writer_handoff(self):
        board = self.board()
        run_id = self.queued_task(board)
        _, first = self.open_console(board)
        csrf = first.bootstrap()['csrfToken']
        handler = board.console._server.RequestHandlerClass
        read_body = handler._read_body
        authenticated, release = threading.Event(), threading.Event()
        def delayed_read(request):
            authenticated.set()
            if not release.wait(5):
                raise TimeoutError('test barrier not released')
            return read_body(request)
        with mock.patch.object(handler, '_read_body', delayed_read), ThreadPoolExecutor(max_workers=1) as pool:
            pending = pool.submit(first.command, 'objective_stop', {'objectiveId': 'run:' + run_id, 'commandId': 'late-stop'}, csrf=csrf)
            try:
                self.assertTrue(authenticated.wait(5))
                _, second = self.open_console(board)
                self.assertTrue(second.bootstrap()['consoleSession']['canWrite'])
            finally:
                release.set()
            status, _, raw = pending.result(timeout=5)
        self.assertEqual(status, 403)
        self.assertEqual(json.loads(raw)['error']['code'], 'CONSOLE_READ_ONLY')
        self.assertEqual(board.call('task_get', {'runId': run_id})['task']['status'], 'queued')

    def test_idle_console_closes_without_touching_tasks_or_starting_again(self):
        board, now = self.timed_board()
        run_id = self.queued_task(board)
        entry, browser = self.open_console(board)
        now[0] += 200
        board.call('console', {'action': 'status'})
        # Neither a public status read nor an unauthenticated request is activity.
        anonymous = Browser(browser.origin + browser.prefix + '/')
        self.assertEqual(anonymous.get('/api/console')[0], 401)
        now[0] += 100
        board.console.expire(entry['consoleId'])
        self.assertFalse(board.console.status()['running'])
        self.assertEqual(board.call('task_get', {'runId': run_id})['task']['status'], 'queued')
        self.assertFalse(board.service._console_sessions)

    def test_authenticated_reads_keep_the_console_alive_but_old_waiter_cannot_close_replacement(self):
        board, now = self.timed_board()
        old, browser = self.open_console(board)
        now[0] += 299
        browser.bootstrap()
        now[0] += 2
        board.console.expire(old['consoleId'])
        self.assertTrue(board.console.status()['running'])
        board.call('console', {'action': 'close', 'expectedConsoleId': old['consoleId']})
        new = board.call('console', {'action': 'open'})
        refusal = board.call('console', {'action': 'close', 'expectedConsoleId': old['consoleId']})
        self.assertEqual(refusal, {'closed': False, 'reason': 'replaced', 'consoleId': new['consoleId']})
        self.assertTrue(board.console.status()['running'])

    def test_launch_capacity_is_bounded_and_recovers_after_expiry(self):
        board, now = self.timed_board()
        for _ in range(MAX_ENTRIES):
            board.call('console', {'action': 'open'})
        with self.assertRaises(BoardError) as error:
            board.call('console', {'action': 'open'})
        self.assertEqual(error.exception.code, 'CONSOLE_LIMIT')
        now[0] += 60
        self.assertIn('url', board.call('console', {'action': 'open'}))

    def test_cookie_duplicates_and_non_ascii_forgery_are_refused(self):
        board = self.board()
        _, browser = self.open_console(board)
        for cookie in ('buddy_console_session=é', browser.cookie + '; ' + browser.cookie):
            self.assertEqual(browser.get('/api/console', headers={'Cookie': cookie})[0], 401)

    def test_active_session_bound_does_not_discard_an_existing_reader(self):
        board, now = self.timed_board()
        first = None
        for _ in range(MAX_SESSIONS):
            _, browser = self.open_console(board)
            first = first or browser
        pending = board.call('console', {'action': 'open'})
        self.assertEqual(first.call('GET', urlsplit(pending['url']).path)[0], 429)
        self.assertEqual(first.get('/api/console')[0], 200)
        self.assertEqual(board.console.status()['sessionCount'], MAX_SESSIONS)
        now[0] += 300
        fresh = board.call('console', {'action': 'open'})
        self.assertTrue(Browser(fresh['url']).bootstrap()['consoleSession']['canWrite'])
        self.assertEqual(board.console.status()['sessionCount'], 1)

    def test_close_fence_rejects_invalid_or_misplaced_identity(self):
        board = self.board()
        for params in ({'action':'status','expectedConsoleId':'a'*24},
                       {'action':'close','expectedConsoleId':None},
                       {'action':'close','expectedConsoleId':'../other'}):
            with self.assertRaises(BoardError) as error:
                board.call('console', params)
            self.assertEqual(error.exception.code, 'INVALID_ARGUMENT')
        self.assertFalse(board.console.status()['running'])


class ConsoleForegroundTests(ConsoleTestCase):
    def test_real_cli_interrupt_closes_only_its_console_and_preserves_work(self):
        with self.daemon():
            code, before = self.cli('ping')
            self.assertEqual(code, 0)
            code, task = self.cli('execution-submit', json.dumps({
                'requestId': 'console-wait-owned-task', 'task': 'outlive the browser',
                'cwd': str(self.workdir()), 'adapter': 'command',
                'argv': ['/bin/sleep', '30'], 'timeoutSeconds': 0,
            }))
            self.assertEqual(code, 0, task)
            child = subprocess.Popen([sys.executable, '-m', 'buddy.cli', 'console',
                '{"browser":false,"wait":true}'], env=_child_environment(self.directory),
                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, start_new_session=True)
            try:
                self.assertTrue(select.select([child.stderr], [], [], 20)[0], 'CLI did not report the entry')
                notice = child.stderr.readline()
                self.assertTrue(notice.startswith('buddy console: the browser was not opened; entry URL: '))
                browser = Browser(notice.partition('entry URL: ')[2].strip())
                self.assertTrue(browser.bootstrap()['consoleSession']['canWrite'])
                child.send_signal(signal.SIGINT)
                stdout, stderr = child.communicate(timeout=15)
                self.assertEqual(child.returncode, 0, stderr)
                result = json.loads(stdout)
                self.assertEqual(result['wait']['status'], 'interrupted')
                self.assertTrue(result['wait']['close']['closed'])
                self.assertEqual(result['consoleId'], result['wait']['close']['consoleId'])
                code, after = self.cli('ping')
                self.assertEqual(code, 0)
                self.assertEqual(after['serviceId'], before['serviceId'])
                code, viewed = self.cli('status', json.dumps({'runId': task['runId']}))
                self.assertEqual(code, 0)
                self.assertIn(viewed['status'], ('queued', 'running', 'completed'))
                self.assertFalse(self.cli('console', '{"action":"status"}')[1]['running'])
            finally:
                if child.poll() is None:
                    child.terminate()
                    child.communicate(timeout=10)
                self.cli('execution-cancel', json.dumps({'runId': task['runId'], 'reason': 'private test cleanup'}))
                self.cli('await', json.dumps({'runId': task['runId'], 'waitSeconds': 15}))
