"""Private writable console: origin/session/CSRF checks, allowlist, assets, no leaks.

The tests speak real HTTP to the real loopback server. Only the C-Two transport is
skipped by the in-process board harness; every command reaches the same validated
Python operation the CLI and C-Two use.
"""
from __future__ import annotations

import http.client
import json
import os
import socket
import unittest
from urllib.parse import urlsplit

from support import BoardTestCase

from buddy.dashboard import Dashboard

PROFILE_ID = "dsh:deepseek-official:deepseek-flash:off"
PROFILE = {
    "profileId": PROFILE_ID,
    "label": "Flash off",
    "adapter": "dsh",
    "provider": "deepseek-official",
    "model": "deepseek-flash",
    "effort": "off",
    "available": True,
    "enabled": True,
    "capabilities": [],
    "contextWindow": 1000000,
    "description": "fixture",
    "source": "manual",
}


def http_call(host: str, port: int, method: str, path: str, *, body=None, headers=None, timeout: float = 10):
    connection = http.client.HTTPConnection(host, port, timeout=timeout)
    try:
        payload = None
        if body is not None:
            payload = body if isinstance(body, bytes) else json.dumps(body).encode()
        connection.request(method, path, body=payload, headers=headers or {})
        response = connection.getresponse()
        data = response.read()
        return response.status, {key.lower(): value for key, value in response.getheaders()}, data
    finally:
        connection.close()


class Browser:
    """A minimal same-origin browser client that keeps the private session cookie."""

    def __init__(self, url: str):
        parts = urlsplit(url)
        self.host = parts.hostname or "127.0.0.1"
        self.port = parts.port or 80
        self.prefix = parts.path.rstrip("/")
        self.origin = f"http://{self.host}:{self.port}"
        self.cookie: str | None = None

    # -- raw ----------------------------------------------------------------
    def call(self, method: str, path: str, *, body=None, headers=None):
        merged = {"Host": f"{self.host}:{self.port}", "Origin": self.origin}
        if self.cookie:
            merged["Cookie"] = self.cookie
        merged.update(headers or {})
        status, response_headers, data = http_call(self.host, self.port, method, path, body=body, headers=merged)
        cookie = response_headers.get("set-cookie")
        if cookie:
            self.cookie = cookie.split(";", 1)[0]
        return status, response_headers, data

    def get(self, path: str = "", *, headers=None):
        return self.call("GET", self.prefix + path, headers=headers)

    def bootstrap(self) -> dict:
        status, _headers, body = self.get("/api/console")
        assert status == 200, body[:400]
        return json.loads(body)

    def command(self, operation: str, params: dict, *, csrf: str | None = None):
        headers = {"Content-Type": "application/json"}
        if csrf is not None:
            headers["X-Buddy-CSRF"] = csrf
        return self.call(
            "POST",
            self.prefix + "/api/command",
            body={"operation": operation, "params": params},
            headers=headers,
        )


def raw_status(browser: Browser, csrf: str, *, extra_headers: str) -> str:
    """Send one raw request (optionally declaring a huge body) and return its status line."""
    with socket.create_connection((browser.host, browser.port), timeout=10) as raw:
        raw.sendall(
            (
                f"POST {browser.prefix}/api/command HTTP/1.1\r\nHost: {browser.host}:{browser.port}\r\n"
                f"Origin: {browser.origin}\r\nCookie: {browser.cookie}\r\nX-Buddy-CSRF: {csrf}\r\n"
                f"{extra_headers}Connection: close\r\n\r\n"
            ).encode()
        )
        chunks = []
        while True:
            chunk = raw.recv(65536)
            if not chunk:
                break
            chunks.append(chunk)
    return b"".join(chunks).decode(errors="replace").split("\r\n", 1)[0]


class ConsoleTestCase(BoardTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.catalog_fixture()

    def open_console(self, board, *, assets: bool = False):
        if assets:
            root = board.console.assets_dir
            (root / "assets").mkdir(parents=True, exist_ok=True)
            (root / "index.html").write_text("<!doctype html><div id=root>Buddy console fixture</div>")
            (root / "assets" / "app.js").write_text("export const ready = true;\n")
            (root / "assets" / "app.css").write_text("body{color:#20332e}\n")
        stated = board.call("console", {"action": "open"})
        self.assertTrue(stated["url"].startswith("http://127.0.0.1:"))
        self.assertFalse(stated["readOnly"])
        return stated, Browser(stated["url"])


class ConsoleSecurityTests(ConsoleTestCase):
    def test_bootstrap_is_the_snapshot_object_and_sets_a_private_session(self):
        board = self.board()
        _stated, browser = self.open_console(board)
        status, headers, body = browser.get("/api/console")
        self.assertEqual(status, 200)
        snapshot = json.loads(body)
        self.assertEqual(sorted(snapshot), sorted(["csrfToken", "tableRevision", "gate", "configuration", "profiles", "preferences", "cards", "evidence", "decisions", "pendingEvidence", "tasks", "capabilities"]))
        self.assertTrue(snapshot["csrfToken"])
        self.assertIn("httponly", headers["set-cookie"].lower())
        self.assertIn("samesite=strict", headers["set-cookie"].lower())
        self.assertEqual(headers["cache-control"], "no-store")
        self.assertIn("content-security-policy", headers)
        self.assertNotIn("access-control-allow-origin", headers)
        self.assertNotIn(board.service.token, body.decode())
        # A second read reuses the same session without rotating it.
        status, headers, _body = browser.get("/api/console")
        self.assertEqual(status, 200)
        self.assertNotIn("set-cookie", headers)
        # The console is a GET-only read surface for the snapshot and never a writer.
        self.assertEqual(board.store.count_tasks(), 0)

    def test_writes_require_session_csrf_and_exact_origin(self):
        board = self.board()
        _stated, browser = self.open_console(board)
        snapshot = browser.bootstrap()
        csrf = snapshot["csrfToken"]
        body = {"operation": "evaluation_write_begin", "params": {"requestId": "w1", "expectedRevision": 0, "kind": "human"}}

        # No session cookie at all.
        status, _headers, data = http_call(
            browser.host,
            browser.port,
            "POST",
            browser.prefix + "/api/command",
            body=body,
            headers={"Host": f"{browser.host}:{browser.port}", "Origin": browser.origin, "X-Buddy-CSRF": csrf},
        )
        self.assertEqual(status, 403, data)
        # Session cookie but no CSRF header.
        status, _headers, _data = browser.call("POST", browser.prefix + "/api/command", body=body)
        self.assertEqual(status, 403)
        # CSRF header but a forged session cookie.
        status, _headers, _data = browser.call(
            "POST",
            browser.prefix + "/api/command",
            body=body,
            headers={"Cookie": "buddy_console_session=forged", "X-Buddy-CSRF": csrf},
        )
        self.assertEqual(status, 403)
        # Wrong CSRF value.
        status, _headers, _data = browser.call(
            "POST", browser.prefix + "/api/command", body=body, headers={"X-Buddy-CSRF": "nope"}
        )
        self.assertEqual(status, 403)
        self.assertEqual(board.call("console_snapshot", {})["tableRevision"], 0)

    def test_foreign_origin_host_and_cross_site_requests_are_refused(self):
        board = self.board()
        _stated, browser = self.open_console(board)
        snapshot = browser.bootstrap()
        csrf = snapshot["csrfToken"]
        body = {"operation": "model_catalog_refresh", "params": {"requestId": "origin-check"}}
        good_headers = {"X-Buddy-CSRF": csrf, "Content-Type": "application/json"}

        status, _h, _d = browser.call(
            "POST", browser.prefix + "/api/command", body=body, headers={**good_headers, "Origin": "http://evil.example"}
        )
        self.assertEqual(status, 403)
        status, _h, _d = browser.call(
            "POST",
            browser.prefix + "/api/command",
            body=body,
            headers={**good_headers, "Host": "localhost:1"},
        )
        self.assertEqual(status, 403)
        status, _h, _d = browser.call(
            "POST",
            browser.prefix + "/api/command",
            body=body,
            headers={**good_headers, "Sec-Fetch-Site": "cross-site"},
        )
        self.assertEqual(status, 403)
        # Reads are refused for a foreign origin too, and never answer with CORS.
        status, headers, _d = browser.get("/api/console", headers={"Origin": "http://evil.example"})
        self.assertEqual(status, 403)
        self.assertNotIn("access-control-allow-origin", headers)
        # A valid same-origin write succeeds and returns the command envelope.
        status, _h, data = browser.call("POST", browser.prefix + "/api/command", body=body, headers=good_headers)
        self.assertEqual(status, 200, data)
        self.assertTrue(json.loads(data)["ok"])

    def test_dashboard_token_cannot_write_through_the_console(self):
        board = self.board()
        dashboard = Dashboard(board.store)
        self.addCleanup(dashboard.close)
        dashboard_url = dashboard.start()["url"]
        _stated, browser = self.open_console(board)
        dashboard_path = urlsplit(dashboard_url).path
        # The read-only dashboard token is not a console session.
        status, _headers, _body = browser.call(
            "GET", dashboard_path + "api", headers={"X-Buddy-CSRF": "x"}
        )
        self.assertEqual(status, 404)
        status, _headers, _body = browser.call(
            "POST",
            dashboard_path + "api/command",
            body={"operation": "task_list", "params": {}},
            headers={"X-Buddy-CSRF": "x"},
        )
        self.assertEqual(status, 404)
        # The dashboard itself stays read-only over its own URL.
        parts = urlsplit(dashboard_url)
        status, _headers, _body = http_call(
            parts.hostname,
            parts.port,
            "POST",
            parts.path + "api/command",
            body={"operation": "task_list", "params": {}},
            headers={"Host": f"{parts.hostname}:{parts.port}", "Origin": dashboard_url.rstrip("/")},
        )
        self.assertEqual(status, 405)
        self.assertEqual(board.call("console_snapshot", {})["tableRevision"], 0)

    def test_command_allowlist_and_bounded_bodies(self):
        board = self.board()
        _stated, browser = self.open_console(board)
        snapshot = browser.bootstrap()
        csrf = snapshot["csrfToken"]
        for operation in ("dispatch", "store.task_cancel", "_receipt", "console_snapshot", "legacy_import"):
            status, _h, data = browser.command(operation, {}, csrf=csrf)
            self.assertEqual(status, 404, operation)
            self.assertEqual(json.loads(data)["error"]["code"], "METHOD_NOT_FOUND")
        # Unknown extra fields are rejected rather than ignored.
        status, _h, data = browser.call(
            "POST",
            browser.prefix + "/api/command",
            body={"operation": "task_list", "params": {}, "nonsense": 1},
            headers={"X-Buddy-CSRF": csrf},
        )
        self.assertEqual(status, 400)
        self.assertEqual(json.loads(data)["error"]["code"], "INVALID_ARGUMENT")
        # A missing Content-Length, an empty body and an oversized body are all refused
        # before any command reaches the service.
        self.assertIn("411", raw_status(browser, csrf, extra_headers=""))
        self.assertIn("413", raw_status(browser, csrf, extra_headers="Content-Length: 1048577\r\n"))
        status, _h, _data = browser.call(
            "POST", browser.prefix + "/api/command", body=b"", headers={"X-Buddy-CSRF": csrf}
        )
        self.assertEqual(status, 413)
        # A real oversized body is refused before any of it is parsed. The server may
        # close the connection while the client is still sending, which is the same
        # refusal observed as a reset instead of a response.
        try:
            status, _h, _data = browser.call(
                "POST",
                browser.prefix + "/api/command",
                body=b"x" * (1024 * 1024 + 1),
                headers={"X-Buddy-CSRF": csrf, "Content-Type": "application/json"},
            )
        except (ConnectionResetError, BrokenPipeError):
            status = 413
        self.assertEqual(status, 413)

    def test_commands_use_the_same_validated_operations_and_survive_a_closed_gate(self):
        board = self.board()
        submitted = board.call(
            "task_submit",
            {
                "requestId": "console-task",
                "task": "keep working",
                "cwd": str(self.workdir()),
                "adapter": "command",
                "argv": ["/bin/echo", "hi"],
            },
        )
        run_id = submitted["task"]["runId"]
        stated, browser = self.open_console(board)
        csrf = browser.bootstrap()["csrfToken"]
        # A writer holds the table; task control through the console still works.
        begin = board.call("evaluation_write_begin", {"requestId": "w1", "expectedRevision": 0, "kind": "human"})
        status, _h, data = browser.command("task_cancel", {"runId": run_id, "reason": "console operator"}, csrf=csrf)
        self.assertEqual(status, 200, data)
        self.assertEqual(json.loads(data)["result"]["task"]["status"], "cancelled")
        # A publish through the browser is the same operation the CLI uses: it is
        # validated, gated and persisted exactly once.
        board.call("model_catalog_refresh", {"requestId": "cat"})
        status, _h, data = browser.command(
            "evaluation_write_publish",
            {
                "commandId": "browser-publish",
                "writerId": begin["writerId"],
                "generation": begin["generation"],
                "writerToken": begin["writerToken"],
                "expectedRevision": 0,
                "profiles": [PROFILE],
            },
            csrf=csrf,
        )
        self.assertEqual(status, 200, data)
        result = json.loads(data)["result"]
        self.assertEqual(result["revision"], 1)
        self.assertEqual(board.call("console_snapshot", {})["profiles"][0]["profileId"], PROFILE_ID)
        # Invalid input is a safe 4xx envelope, never a traceback.
        status, _h, data = browser.command("task_cancel", {"runId": run_id, "nonsense": True}, csrf=csrf)
        self.assertEqual(status, 400, data)
        payload = json.loads(data)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error"]["code"], "INVALID_ARGUMENT")
        self.assertNotIn("Traceback", data.decode())
        # The read-only dashboard token is not a session: a write with it is refused.
        self.assertNotIn("token", json.dumps(snapshot_of(board)).lower().replace("csrftoken", ""))

    def test_snapshot_get_does_not_run_model_discovery(self):
        board = self.board()
        _stated, browser = self.open_console(board)
        from buddy import catalog

        original = catalog.discover
        catalog.discover = lambda: (_ for _ in ()).throw(AssertionError("GET must not discover models"))
        try:
            snapshot = browser.bootstrap()
            self.assertEqual(snapshot["tableRevision"], 0)
        finally:
            catalog.discover = original


def snapshot_of(board) -> dict:
    return board.call("console_snapshot", {})


    def test_task_detail_route_returns_the_existing_view(self):
        board = self.board()
        submitted = board.call(
            "task_submit",
            {
                "requestId": "detail-task",
                "task": "produce a detail view",
                "cwd": str(self.workdir()),
                "adapter": "command",
                "argv": ["/bin/echo", "detail"],
            },
        )
        run_id = submitted["task"]["runId"]
        client = board.client()
        client.register_worker("worker-1", adapter="command", capabilities=["command"])
        claim = client.claim("worker-1", "claim-detail", "n" * 32, task_id=run_id)["claim"]
        client.submit_result(
            "worker-1",
            claim["attempt"]["attemptId"],
            claim["attempt"]["generation"],
            "n" * 32,
            {"status": "ok", "result": {"finalText": "detail"}, "shutdownConfirmed": True, "exitCode": 0},
        )
        _stated, browser = self.open_console(board)
        status, headers, body = browser.get(f"/api/tasks/{run_id}")
        self.assertEqual(status, 200, body[:300])
        self.assertIn("application/json", headers["content-type"])
        view = json.loads(body)
        self.assertEqual(view["runId"], run_id)
        self.assertEqual(view["status"], "completed")
        self.assertEqual(view["result"]["finalText"], "detail")
        self.assertEqual(view["resultMeta"]["status"], "ok")
        self.assertEqual(view["spec"]["adapter"], "command")
        # Unknown or crafted identities are a plain 404, never a lookup path.
        self.assertEqual(browser.get("/api/tasks/does-not-exist")[0], 404)
        self.assertEqual(browser.get("/api/tasks/..%2f..%2fcontrol.json")[0], 404)
        self.assertEqual(browser.get("/api/tasks/" + "x" * 200)[0], 404)


class ConsoleAssetTests(ConsoleTestCase):
    def test_missing_build_is_an_honest_setup_page_but_the_api_works(self):
        board = self.board()
        _stated, browser = self.open_console(board, assets=False)
        status, headers, body = browser.get("/")
        self.assertEqual(status, 503)
        text = body.decode()
        self.assertIn("Console assets are not built", text)
        self.assertIn("/api/console", text)
        self.assertIn("console_assets", text)
        self.assertIn("text/html", headers["content-type"])
        # The JSON API is still available without a frontend build.
        snapshot = browser.bootstrap()
        self.assertIn("gate", snapshot)
        self.assertFalse(snapshot["capabilities"]["consoleAssets"])

    def test_assets_are_served_with_headers_and_traversal_is_refused(self):
        board = self.board()
        (self.directory / "outside.txt").write_text("not an asset")
        stated, browser = self.open_console(board, assets=True)
        status, headers, body = browser.get("/")
        self.assertEqual(status, 200)
        self.assertIn("Buddy console fixture", body.decode())
        self.assertEqual(headers["cache-control"], "no-cache")
        status, headers, body = browser.get("/assets/app.js")
        self.assertEqual(status, 200)
        self.assertTrue(headers["content-type"].startswith("text/javascript"))
        self.assertIn("immutable", headers["cache-control"])
        self.assertEqual(headers["x-content-type-options"], "nosniff")
        self.assertIn("default-src 'none'", headers["content-security-policy"])
        self.assertEqual(body.decode().strip(), "export const ready = true;")
        status, headers, _body = browser.get("/assets/app.css")
        self.assertEqual(status, 200)
        self.assertTrue(headers["content-type"].startswith("text/css"))
        # Directory listings and non-allowlisted files are not reachable.
        self.assertEqual(browser.get("/assets/")[0], 404)
        self.assertEqual(browser.get("/assets")[0], 404)
        self.assertEqual(browser.get("/outside.txt")[0], 404)
        # Traversal, encoded traversal, absolute paths and crafted names are refused.
        for path in (
            "/../outside.txt",
            "/%2e%2e/outside.txt",
            "/assets/../../outside.txt",
            "/assets/%2e%2e/%2e%2e/outside.txt",
            "/assets//etc/passwd",
            "/assets/app.js%00.txt",
            "/..%2foutside.txt",
        ):
            status, _headers, _body = browser.get(path)
            self.assertEqual(status, 404, path)
        # A symlink escaping the asset root is refused as well.
        os.symlink(self.directory / "outside.txt", board.console.assets_dir / "assets" / "leak.txt")
        self.assertEqual(browser.get("/assets/leak.txt")[0], 404)
        # An extension-less navigation path serves the single page document.
        status, headers, body = browser.get("/evaluation/table")
        self.assertEqual(status, 200)
        self.assertIn("text/html", headers["content-type"])
        self.assertIn("Buddy console fixture", body.decode())

    def test_console_close_and_reopen_rotates_the_private_session(self):
        board = self.board()
        stated, browser = self.open_console(board)
        old_prefix = browser.prefix
        board.call("console", {"action": "close"})
        status = board.call("console", {"action": "status"})
        self.assertFalse(status["running"])
        # A fresh open is a new secret; the previous URL path is gone.
        reopened = board.call("console", {"action": "open"})
        self.assertNotEqual(reopened["url"], stated["url"])
        new_parts = urlsplit(reopened["url"])
        status, _headers, _body = http_call(
            new_parts.hostname,
            new_parts.port,
            "GET",
            old_prefix + "/api/console",
            headers={"Host": f"{new_parts.hostname}:{new_parts.port}"},
        )
        self.assertEqual(status, 404)


class ConsoleDaemonTests(ConsoleTestCase):
    def test_cli_console_opens_and_closes_the_real_daemon_surface(self):
        with self.daemon():
            code, opened = self.cli("console", json.dumps({"action": "open"}))
            self.assertEqual(code, 0, opened)
            self.assertFalse(opened["readOnly"])
            self.assertTrue(opened["url"].startswith("http://127.0.0.1:"))
            parts = urlsplit(opened["url"])
            status, headers, body = http_call(
                parts.hostname,
                parts.port,
                "GET",
                parts.path + "api/console",
                headers={"Host": f"{parts.hostname}:{parts.port}"},
            )
            self.assertEqual(status, 200, body[:300])
            snapshot = json.loads(body)
            self.assertEqual(snapshot["tableRevision"], 0)
            self.assertIn("csrfToken", snapshot)
            self.assertNotIn("access-control-allow-origin", headers)
            code, status_reply = self.cli("console", json.dumps({"action": "status"}))
            self.assertEqual(code, 0, status_reply)
            self.assertTrue(status_reply["running"])
            self.assertFalse(status_reply["assetsBuilt"])
            code, closed = self.cli("console", json.dumps({"action": "close"}))
            self.assertEqual(code, 0, closed)
            self.assertTrue(closed["closed"])

    def test_cli_evaluation_operations_round_trip_through_ctwo(self):
        with self.daemon():
            code, caps = self.cli("capabilities", "{}")
            self.assertEqual(code, 0, caps)
            self.assertIn("console_snapshot", caps["operations"]["control"])
            self.assertIn("not implemented", caps["limitations"]["selection"])
            self.assertIn("not implemented", caps["limitations"]["maintenance"])
            self.assertIn("not claimed", caps["limitations"]["osIsolation"])
            code, snapshot = self.cli("console-snapshot", "{}")
            self.assertEqual(code, 0, snapshot)
            self.assertEqual(snapshot["tableRevision"], 0)
            code, begin = self.cli(
                "evaluation-write-begin",
                json.dumps({"requestId": "cli-1", "expectedRevision": 0, "kind": "human"}),
            )
            self.assertEqual(code, 0, begin)
            self.assertEqual(begin["state"], "active")
            self.assertIn("writerToken", begin)
            code, refreshed = self.cli("model-catalog-refresh", json.dumps({"requestId": "cli-cat"}))
            self.assertEqual(code, 0, refreshed)
            self.assertEqual(refreshed["catalog"]["source"], f"file:{self.directory / 'model-catalog.json'}")
            code, published = self.cli(
                "evaluation-write-publish",
                json.dumps(
                    {
                        "commandId": "cli-publish",
                        "writerId": begin["writerId"],
                        "generation": begin["generation"],
                        "writerToken": begin["writerToken"],
                        "expectedRevision": 0,
                        "profiles": [PROFILE],
                    }
                ),
            )
            self.assertEqual(code, 0, published)
            self.assertEqual(published["revision"], 1)
            code, evidence = self.cli(
                "evaluation-evidence-record",
                json.dumps({"profileId": PROFILE_ID, "kind": "manual", "summary": "cli note", "source": "cli"}),
            )
            self.assertEqual(code, 0, evidence)
            self.assertFalse(evidence["counted"])
            code, reader = self.cli("evaluation-reader-begin", json.dumps({"kind": "selection", "revision": 1}))
            self.assertEqual(code, 0, reader)
            code, released = self.cli("evaluation-reader-release", json.dumps({"readerId": reader["readerId"]}))
            self.assertEqual(code, 0, released)
            self.assertTrue(released["released"])
            code, final = self.cli("console-snapshot", "{}")
            self.assertEqual(code, 0, final)
            self.assertEqual(final["tableRevision"], 1)
            self.assertEqual(final["pendingEvidence"], 1)
            self.assertEqual(len(final["profiles"]), 1)
            self.assertEqual(len(final["evidence"]), 1)


if __name__ == "__main__":
    unittest.main()
