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

from buddy.console_sessions import MAX_ENTRIES, MAX_SESSIONS, ENTRY_SECONDS
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
        now[0] += ENTRY_SECONDS
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

    def test_shared_cookie_jar_keeps_stable_bookmark_and_writable_login(self):
        board = self.board()
        self.open_console(board, assets=True)
        jar = http.cookiejar.CookieJar()
        opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
        first = opener.open(board.call('console', {'action': 'open'})['url'], timeout=5).geturl()
        second = opener.open(board.call('console', {'action': 'open'})['url'], timeout=5).geturl()
        self.assertEqual(first, second)
        self.assertEqual(len(list(jar)), 1)
        reread = json.load(opener.open(first + 'api/console', timeout=5))
        self.assertTrue(reread['consoleSession']['canWrite'])
        self.assertIsNone(reread['consoleSession']['reason'])

    def queued_task(self, board):
        return board.call('task_submit', {'requestId': 'console-session-task', 'task': 'still pending',
            'cwd': str(self.workdir()), 'adapter': 'command', 'argv': ['/bin/echo', 'ok']})['task']['runId']

    def test_independent_sessions_keep_authority_and_stale_revision_is_refused(self):
        board = self.board()
        _, first = self.open_console(board)
        _, second = self.open_console(board)
        for i, browser in enumerate((first, second)):
            snapshot = browser.bootstrap()
            self.assertTrue(snapshot['consoleSession']['canWrite'])
            status, _, raw = browser.command('evaluation_write_begin',
                {'requestId': f'grant-{i}', 'expectedRevision': snapshot['tableRevision'], 'kind': 'human'}, csrf=snapshot['csrfToken'])
            self.assertEqual(status, 200, raw)
            grant = json.loads(raw)['result']
            status, _, raw = browser.command('evaluation_write_abort',
                {**{k: grant[k] for k in ('writerId','generation','writerToken')}, 'commandId': f'abort-{i}'}, csrf=snapshot['csrfToken'])
            self.assertEqual(status, 200, raw)
        snapshot = first.bootstrap()
        status, _, raw = first.command('evaluation_write_begin',
            {'requestId':'stale', 'expectedRevision': snapshot['tableRevision'] + 1, 'kind':'human'}, csrf=snapshot['csrfToken'])
        self.assertEqual(status, 200, raw)
        grant = json.loads(raw)['result']
        status, _, raw = first.command('user_policy_publish', {
            **{k: grant[k] for k in ('writerId','generation','writerToken')},
            'commandId':'stale-publish', 'expectedRevision':snapshot['tableRevision'] + 1,
            'profileSettings':[]}, csrf=snapshot['csrfToken'])
        self.assertEqual(json.loads(raw)['error']['code'], 'REVISION_CONFLICT')

    def test_sessions_persist_hashed_and_restart_reauthenticates_cookie(self):
        import os
        from buddy.console import Console
        board = self.board()
        _, browser = self.open_console(board)
        snapshot = browser.bootstrap()
        session_file = board.directory / 'console-sessions.json'
        data = session_file.read_text()
        self.assertNotIn(browser.cookie.split('=', 1)[1], data)
        self.assertNotIn(snapshot['csrfToken'], data)
        self.assertEqual(os.stat(session_file).st_mode & 0o777, 0o600)
        board.console.close()
        board.console = Console(board.store, board.service, port=browser.port)
        board.console.start(issue_ticket=False)
        restored = browser.bootstrap()
        self.assertEqual(snapshot['consoleSession']['id'], restored['consoleSession']['id'])
        self.assertTrue(restored['consoleSession']['canWrite'])
        self.assertNotEqual(snapshot['csrfToken'], restored['csrfToken'])
        self.assertEqual(browser.command('evaluation_history', {}, csrf=restored['csrfToken'])[0], 200)

    def test_fixed_port_collision_is_explicit_and_nonloopback_is_refused(self):
        from buddy.console import Console
        board = self.board()
        _, browser = self.open_console(board)
        other = self.board()
        occupied = Console(other.store, other.service, port=browser.port)
        with self.assertRaises(BoardError) as caught:
            occupied.start()
        self.assertEqual(caught.exception.code, 'CONSOLE_PORT_IN_USE')
        with self.assertRaises(BoardError):
            Console(other.store, other.service, host='0.0.0.0')

    def test_idle_console_stays_open_and_does_not_touch_tasks(self):
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
        self.assertTrue(board.console.status()['running'])
        self.assertEqual(board.call('task_get', {'runId': run_id})['task']['status'], 'queued')
        self.assertTrue(browser.bootstrap()['consoleSession']['canWrite'])

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

    def test_configured_port_survives_restart_without_environment_override(self):
        import os
        import socket
        from buddy.console import Console
        board=self.board()
        with socket.socket() as reserved:
            reserved.bind(('127.0.0.1',0))
            port=reserved.getsockname()[1]
        with mock.patch.dict(os.environ, {'BUDDY_CONSOLE_PORT':str(port)}):
            board.console=Console(board.store,board.service)
            board.console.start(issue_ticket=False)
            self.assertEqual(board.console.port,port)
            board.console.close()
        clean={k:v for k,v in os.environ.items() if k!='BUDDY_CONSOLE_PORT'}
        with mock.patch.dict(os.environ,clean,clear=True):
            board.console=Console(board.store,board.service)
            board.console.start(issue_ticket=False)
            self.assertEqual(board.console.origin,f'http://127.0.0.1:{port}')
            self.assertEqual((board.directory/'console-settings.json').stat().st_mode & 0o777,0o600)

    def test_real_daemon_port_collision_reaches_the_cli_as_actionable_error(self):
        import socket
        with socket.socket() as reserved:
            reserved.bind(('127.0.0.1',0));reserved.listen(1)
            code,result=self.cli('health',env={'BUDDY_CONSOLE_PORT':str(reserved.getsockname()[1])})
        self.assertNotEqual(code,0)
        self.assertEqual(result['error']['code'],'CONSOLE_PORT_IN_USE')
        self.assertIn('BUDDY_CONSOLE_PORT',result['error']['message'])

    def test_launch_capacity_is_bounded_and_recovers_after_expiry(self):
        board, now = self.timed_board()
        for _ in range(MAX_ENTRIES):
            board.call('console', {'action': 'open'})
        with self.assertRaises(BoardError) as error:
            board.call('console', {'action': 'open'})
        self.assertEqual(error.exception.code, 'CONSOLE_LIMIT')
        now[0] += ENTRY_SECONDS
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
        existing_id = first.bootstrap()['consoleSession']['id']
        self.assertEqual(first.call('GET', urlsplit(pending['url']).path)[0], 303)
        self.assertEqual(first.bootstrap()['consoleSession']['id'], existing_id)
        fresh_ticket = board.call('console', {'action':'open'})
        anonymous = Browser(first.origin + '/')
        self.assertEqual(anonymous.call('GET', urlsplit(fresh_ticket['url']).path)[0], 429)
        self.assertEqual(first.get('/api/console')[0], 200)
        self.assertEqual(board.console.status()['sessionCount'], MAX_SESSIONS)
        now[0] += 800 * 24 * 3600
        self.assertEqual(first.get('/api/console')[0], 200)
        csrf = first.bootstrap()['csrfToken']
        self.assertEqual(first.command('console_logout', {}, csrf=csrf)[0], 200)
        fresh = board.call('console', {'action': 'open'})
        self.assertTrue(Browser(fresh['url']).bootstrap()['consoleSession']['canWrite'])
        self.assertEqual(board.console.status()['sessionCount'], MAX_SESSIONS)

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
