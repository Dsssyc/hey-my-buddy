"""ADR-020 loopback access and optional login over real private HTTP servers."""
import json
from unittest import mock
from urllib.parse import urlsplit

from buddy.console import Console
from buddy.console_sessions import ENTRY_SECONDS
from test_console import Browser, ConsoleTestCase, http_call


class ConsoleAccessTests(ConsoleTestCase):
    require_login = False

    def change(self, browser, required, revision=None):
        snapshot = browser.bootstrap()
        return browser.command("console_access_set", {
            "requireLogin": required,
            "expectedRevision": snapshot["consoleAccess"]["revision"] if revision is None else revision,
        }, csrf=snapshot["csrfToken"])

    def test_default_fixed_url_and_writes_need_no_cookie(self):
        board = self.board()
        entry, first = self.open_console(board, assets=True)
        self.assertEqual(entry["url"], first.origin + "/")
        self.assertIsNone(entry["expiresAt"])
        second = Browser(entry["url"])
        for browser in (first, second):
            status, headers, _ = browser.get("/")
            self.assertEqual(status, 200)
            self.assertNotIn("set-cookie", headers)
            self.assertNotIn("access-control-allow-origin", headers)
            self.assertIn("frame-ancestors 'none'", headers["content-security-policy"])
            snapshot = browser.bootstrap()
            self.assertEqual(snapshot["consoleAccess"], {"requireLogin": False, "revision": 0, "sessions": []})
            self.assertTrue(snapshot["consoleSession"]["canWrite"])
            self.assertIsNone(browser.cookie)
        self.assertEqual(board.console.status()["sessionCount"], 0)
        for _ in range(70):
            self.assertEqual(board.console.start()["url"], entry["url"])
        self.assertFalse((board.directory / "console-sessions.json").exists())
        self.assertEqual(first.command("evaluation_history", {}, csrf="wrong")[0], 403)
        self.assertEqual(first.command("evaluation_history", {}, csrf=first.bootstrap()["csrfToken"])[0], 200)

    def test_host_origin_fetch_metadata_and_csrf_refusals_without_cookie(self):
        board = self.board()
        _, browser = self.open_console(board, assets=True)
        csrf = browser.bootstrap()["csrfToken"]
        body = {"operation": "console_access_set", "params": {"requireLogin": True, "expectedRevision": 0}}
        for headers in ({"Host": "localhost:" + str(browser.port)}, {"Host": "evil.test"},
                        {"Origin": "http://evil.test"}, {"Origin": "null"},
                        {"Sec-Fetch-Site": "cross-site"}, {"Sec-Fetch-Site": "same-site"}):
            with self.subTest(headers=headers):
                self.assertEqual(browser.get("/api/console", headers=headers)[0], 403)
                self.assertEqual(browser.call("POST", "/api/command", body=body,
                    headers={"X-Buddy-CSRF": csrf, **headers})[0], 403)
        self.assertEqual(http_call(browser.host, browser.port, "POST", "/api/command", body=body,
            headers={"Host": f"{browser.host}:{browser.port}", "X-Buddy-CSRF": csrf})[0], 403)
        self.assertEqual(browser.command("console_access_set", body["params"])[0], 403)
        self.assertEqual(browser.call("OPTIONS", "/api/command")[0], 405)
        self.assertFalse(board.console.require_login)

    def test_switch_enrolls_current_browser_and_requires_ten_minute_entry_for_others(self):
        board = self.board()
        now = [1000.0]
        board.console._clock = lambda: now[0]
        _, browser = self.open_console(board)
        old_csrf = browser.bootstrap()["csrfToken"]
        old_authority = board.console._local_session.cookie
        self.assertEqual(self.change(browser, True)[0], 200)
        self.assertFalse(board.service.console_session_valid(old_authority))
        self.assertIsNotNone(browser.cookie)
        self.assertEqual(browser.command("evaluation_history", {}, csrf=old_csrf)[0], 403)
        anonymous = Browser(browser.origin + "/")
        self.assertEqual(anonymous.get("/api/console")[0], 401)
        pending = board.console.start()
        self.assertTrue(pending["url"].startswith(browser.origin + "/launch/"))
        now[0] += ENTRY_SECONDS - 1
        second = Browser(pending["url"])
        self.assertTrue(second.bootstrap()["consoleSession"]["canWrite"])
        self.assertEqual(second.call("GET", urlsplit(pending["url"]).path)[0], 410)
        expired = board.console.start()
        now[0] += ENTRY_SECONDS
        self.assertEqual(second.call("GET", urlsplit(expired["url"]).path)[0], 410)
        self.assertEqual(self.change(browser, False, revision=0)[0], 409)
        self.assertTrue(board.console.require_login)

    def test_disable_revokes_old_sessions_and_entries_across_restart(self):
        board = self.board()
        _, browser = self.open_console(board)
        self.change(browser, True)
        second = Browser(board.console.start()["url"])
        old_cookie = second.cookie
        pending = board.console.start()
        self.assertEqual(self.change(browser, False)[0], 200)
        self.assertEqual(board.console.status()["sessionCount"], 0)
        self.assertEqual(browser.call("GET", urlsplit(pending["url"]).path)[0], 410)
        board.console.close()
        board.console = Console(board.store, board.service, port=browser.port)
        board.console.start(issue_ticket=False)
        self.assertFalse(browser.bootstrap()["consoleAccess"]["requireLogin"])
        self.change(browser, True)
        second.cookie = old_cookie
        self.assertEqual(second.get("/api/console")[0], 401)
        settings = json.loads((board.directory / "console-settings.json").read_text())
        self.assertEqual(settings["revision"], 3)
        self.assertTrue(settings["requireLogin"])

    def test_session_revocation_and_logout_are_durable(self):
        board = self.board()
        now = [1000.0]
        board.console._clock = lambda: now[0]
        _, first = self.open_console(board)
        self.change(first, True)
        second = Browser(board.console.start()["url"])
        identity = second.bootstrap()["consoleSession"]["id"]
        cookie = second.cookie
        now[0] += 800 * 86400
        board.console.close()
        board.console = Console(board.store, board.service, port=first.port, clock=lambda: now[0])
        board.console.start(issue_ticket=False)
        self.assertEqual(second.bootstrap()["consoleSession"]["id"], identity)
        csrf = first.bootstrap()["csrfToken"]
        self.assertEqual(first.command("console_session_revoke", {"sessionId": identity}, csrf=csrf)[0], 200)
        self.assertFalse(board.service.console_session_valid(cookie.split("=", 1)[1]))
        self.assertEqual(second.get("/api/console")[0], 401)
        self.assertEqual(first.command("console_logout", {}, csrf=csrf)[0], 200)
        self.assertEqual(first.get("/api/console")[0], 401)
        self.assertEqual(json.loads((board.directory / "console-sessions.json").read_text())["sessions"], [])

    def test_two_cookie_free_writers_keep_publication_revision_fences(self):
        from test_console_sessions import ConsoleSessionTests
        ConsoleSessionTests.test_independent_sessions_keep_authority_and_stale_revision_is_refused(self)

    def test_access_setting_write_failure_preserves_authority_and_revision(self):
        board = self.board()
        _, browser = self.open_console(board)
        before = browser.bootstrap()
        with mock.patch("buddy.launcher.write_private", side_effect=OSError("fixture disk error")):
            self.assertEqual(self.change(browser, True)[0], 500)
        after = browser.bootstrap()
        self.assertEqual(after["consoleAccess"], before["consoleAccess"])
        self.assertEqual(after["csrfToken"], before["csrfToken"])

    def test_access_fields_and_local_logout_are_refused(self):
        board = self.board()
        _, browser = self.open_console(board)
        csrf = browser.bootstrap()["csrfToken"]
        for params in ({}, {"requireLogin": "true", "expectedRevision": 0},
                       {"requireLogin": True, "expectedRevision": True},
                       {"requireLogin": True, "expectedRevision": 0, "consoleAuthority": {}}):
            self.assertEqual(browser.command("console_access_set", params, csrf=csrf)[0], 400)
        self.assertEqual(browser.command("console_logout", {}, csrf=csrf)[0], 409)
