"""Real HTTP coverage for one-time entry and authenticated browser sessions."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import http.cookiejar
import json
import os
import signal
import subprocess
import sys
import threading
from unittest import mock
import urllib.request
from urllib.parse import urlsplit

from hey_my_buddy.console.console_sessions import MAX_ENTRIES, MAX_SESSIONS, ENTRY_SECONDS
from hey_my_buddy.errors import BoardError
from console.test_console import Browser, ConsoleTestCase, http_call
from support import _child_environment, wait_for
from private_acl import assert_private_path


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
        status, headers, body = browser.call('GET', ticket)
        self.assertEqual(status, 410)
        self.assertEqual(headers['cache-control'], 'no-store')
        self.assertIn("default-src 'none'", headers['content-security-policy'])
        self.assertEqual(headers['content-type'], 'text/html; charset=utf-8')
        self.assertIn(b'<meta name="hey-my-buddy-reason" content="console.entry-unavailable">', body)
        self.assertIn(b'hey-my-buddy console fixture', body)
        self.assertIn(b'src="/assets/app.js"', body)
        self.assertNotIn(b'src="./assets/app.js"', body)
        self.assertNotIn(ticket.removeprefix('/launch/').encode(), body)
        self.assertNotIn(ticket.removeprefix('/launch/'), browser.prefix)
        self.assertNotIn(browser.cookie.split('=', 1)[1], browser.prefix)
        copied = Browser(browser.origin + browser.prefix + '/')
        for route in ('/', '/api/console', '/api/macro-tasks'):
            self.assertEqual(copied.get(route)[0], 401, route)
            self.assertEqual(browser.get(route)[0], 200, route)
        status, headers, body = copied.call('GET', '/assets/app.js')
        self.assertEqual(status, 200)
        self.assertIn('text/javascript', headers['content-type'])
        self.assertIn("default-src 'none'", headers['content-security-policy'])
        self.assertEqual(body.strip(), b'export const ready = true;')
        self.assertEqual(copied.call('GET', '/assets/../../outside.txt')[0], 404)
        self.assertEqual(copied.call('GET', '/assets/app.js', headers={'Origin': 'http://evil.test'})[0], 403)
        self.assertNotIn('url', board.call('console', {'action': 'status'}))

    def test_expired_or_unredeemed_ticket_does_not_take_write_access(self):
        board, now = self.timed_board()
        _, first = self.open_console(board, assets=True)
        pending = board.call('console', {'action': 'open'})
        self.assertTrue(first.bootstrap()['consoleSession']['id'])
        now[0] += ENTRY_SECONDS
        self.assertEqual(first.call('GET', urlsplit(pending['url']).path)[0], 410)
        self.assertTrue(first.bootstrap()['consoleSession']['id'])

    def test_two_redemptions_race_for_exactly_one_session(self):
        board = self.board()
        board.console.assets_dir.mkdir(parents=True, exist_ok=True)
        (board.console.assets_dir / 'index.html').write_text('<!doctype html><html><head></head><body></body></html>', encoding="utf-8", newline="\n")
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
        self.assertTrue(reread['consoleSession']['id'])

    def queued_task(self, board):
        return board.submit_execution(**{'requestId': 'console-session-task', 'task': 'still pending',
            'cwd': str(self.workdir()), 'adapter': 'command', 'argv': ['/bin/echo', 'ok']})['task']['runId']

    def test_independent_sessions_can_publish_without_revision_fences(self):
        board = self.board()
        _, first = self.open_console(board)
        _, second = self.open_console(board)
        for browser in (first, second):
            self.assertTrue(browser.bootstrap()['consoleSession']['id'])
        snapshot = first.bootstrap()
        status, _, raw = first.command('user_policy_publish', {
            'commandId': 'first-publish', 'buddySettings': []}, csrf=snapshot['csrfToken'])
        self.assertEqual(status, 200, raw)
        other = second.bootstrap()
        status, _, raw = second.command('user_policy_publish', {
            'commandId': 'second-publish', 'buddySettings': []}, csrf=other['csrfToken'])
        self.assertEqual(status, 200, raw)

    def test_sessions_persist_hashed_and_restart_reauthenticates_cookie(self):
        import os
        from hey_my_buddy.console.server import Console
        board = self.board()
        _, browser = self.open_console(board)
        snapshot = browser.bootstrap()
        session_file = board.directory / 'console-sessions.json'
        data = session_file.read_text(encoding="utf-8")
        self.assertNotIn(browser.cookie.split('=', 1)[1], data)
        self.assertNotIn(snapshot['csrfToken'], data)
        assert_private_path(self, session_file, 0o600)
        board.console.close()
        board.console = Console(board.store, board.service, port=browser.port)
        board.console.start(issue_ticket=False)
        restored = browser.bootstrap()
        self.assertEqual(snapshot['consoleSession']['id'], restored['consoleSession']['id'])
        self.assertTrue(restored['consoleSession']['id'])
        self.assertNotEqual(snapshot['csrfToken'], restored['csrfToken'])
        self.assertEqual(browser.command('selection_list', {}, csrf=restored['csrfToken'])[0], 200)

    def test_fixed_port_collision_is_explicit(self):
        from hey_my_buddy.console.server import Console
        board = self.board()
        _, browser = self.open_console(board)
        other = self.board()
        occupied = Console(other.store, other.service, port=browser.port)
        with self.assertRaises(BoardError) as caught:
            occupied.start()
        self.assertEqual(caught.exception.code, 'CONSOLE_PORT_IN_USE')

    def test_old_waiter_cannot_close_replacement(self):
        board = self.board()
        old, browser = self.open_console(board)
        board.call('console', {'action': 'close', 'expectedConsoleId': old['consoleId']})
        new = board.call('console', {'action': 'open'})
        refusal = board.call('console', {'action': 'close', 'expectedConsoleId': old['consoleId']})
        self.assertEqual(refusal, {'closed': False, 'reason': 'replaced', 'consoleId': new['consoleId']})
        self.assertTrue(board.console.status()['running'])

    def test_configured_port_survives_restart_without_environment_override(self):
        import os
        import socket
        from hey_my_buddy.console.server import Console
        board=self.board()
        with socket.socket() as reserved:
            reserved.bind(('127.0.0.1',0))
            port=reserved.getsockname()[1]
        with mock.patch.dict(os.environ, {'HMB_CONSOLE_PORT':str(port)}):
            board.console=Console(board.store,board.service)
            board.console.start(issue_ticket=False)
            self.assertEqual(board.console.port,port)
            board.console.close()
        clean={k:v for k,v in os.environ.items() if k!='HMB_CONSOLE_PORT'}
        with mock.patch.dict(os.environ,clean,clear=True):
            board.console=Console(board.store,board.service)
            board.console.start(issue_ticket=False)
            self.assertEqual(board.console.origin,f'http://127.0.0.1:{port}')
            assert_private_path(self, board.directory / 'console-settings.json', 0o600)

    def test_real_daemon_port_collision_reaches_the_cli_as_actionable_error(self):
        import socket
        with socket.socket() as reserved:
            reserved.bind(('127.0.0.1',0));reserved.listen(1)
            code,result=self.cli('health',env={'HMB_CONSOLE_PORT':str(reserved.getsockname()[1])})
        self.assertNotEqual(code,0)
        self.assertEqual(result['error']['code'],'CONSOLE_PORT_IN_USE')
        self.assertIn('HMB_CONSOLE_PORT',result['error']['message'])

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
        for cookie in ('hey_my_buddy_console_session=é', browser.cookie + '; ' + browser.cookie):
            self.assertEqual(browser.get('/api/console', headers={'Cookie': cookie})[0], 401)

    def test_active_session_bound_does_not_discard_an_existing_reader(self):
        board, now = self.timed_board()
        first = None
        for _ in range(MAX_SESSIONS):
            _, browser = self.open_console(board, assets=True)
            first = first or browser
        pending = board.call('console', {'action': 'open'})
        existing_id = first.bootstrap()['consoleSession']['id']
        self.assertEqual(first.call('GET', urlsplit(pending['url']).path)[0], 303)
        self.assertEqual(first.bootstrap()['consoleSession']['id'], existing_id)
        fresh_ticket = board.call('console', {'action':'open'})
        anonymous = Browser(first.origin + '/')
        status, headers, body = anonymous.call('GET', urlsplit(fresh_ticket['url']).path)
        self.assertEqual(status, 429)
        self.assertEqual(headers['cache-control'], 'no-store')
        self.assertIn(b'console.entry-unavailable', body)
        self.assertEqual(first.get('/api/console')[0], 200)
        self.assertEqual(board.console.status()['sessionCount'], MAX_SESSIONS)
        now[0] += 800 * 24 * 3600
        self.assertEqual(first.get('/api/console')[0], 200)
        csrf = first.bootstrap()['csrfToken']
        self.assertEqual(first.command('console_logout', {}, csrf=csrf)[0], 200)
        fresh = board.call('console', {'action': 'open'})
        self.assertTrue(Browser(fresh['url']).bootstrap()['consoleSession']['id'])
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
            task = self.submit_execution(**{
                'requestId': 'console-wait-owned-task', 'task': 'outlive the browser',
                'cwd': str(self.workdir()), 'adapter': 'command',
                'argv': ['/bin/sleep', '30'], 'expectedSeconds': 1800,
            })["task"]
            child = subprocess.Popen([sys.executable, '-m', 'hey_my_buddy.cli.main', 'console',
                '{"browser":false,"wait":true}'], env=_child_environment(self.directory),
                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, start_new_session=True, encoding="utf-8")
            notices = []
            reported = threading.Event()

            def read_notice():
                notices.append(child.stderr.readline())
                reported.set()

            reader = threading.Thread(target=read_notice, daemon=True)
            reader.start()
            try:
                self.assertTrue(reported.wait(20), 'CLI did not report the entry')
                reader.join(5)
                self.assertFalse(reader.is_alive())
                notice = notices[0]
                self.assertTrue(notice.startswith('hey-my-buddy console: the browser was not opened; entry URL: '))
                browser = Browser(notice.partition('entry URL: ')[2].strip())
                self.assertTrue(browser.bootstrap()['consoleSession']['id'])
                child.send_signal(signal.SIGINT)
                self.assertTrue(wait_for(
                    lambda: not self.cli('console', '{"action":"status"}')[1]['running'], 30),
                    'the foreground CLI did not close its console after the interrupt')
                stdout, stderr = child.communicate(timeout=30)
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
                self.assertIn(viewed['state'], ('queued', 'running', 'completed'))
                self.assertFalse(self.cli('console', '{"action":"status"}')[1]['running'])
            finally:
                if child.poll() is None:
                    child.terminate()
                    child.communicate(timeout=10)
                reader.join(5)
                self.assertFalse(reader.is_alive(), 'CLI notice reader remained after child exit')
                self.cli('execution-cancel', json.dumps({'runId': task['runId'], 'note': 'private test cleanup'}))
                self.cli('await', json.dumps({'runId': task['runId'], 'waitSeconds': 15}))
