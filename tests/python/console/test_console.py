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
from blackboard.tasks.test_workflow import WorkflowTestCase


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

    def __init__(self, url: str, *, redeem: bool = True):
        parts = urlsplit(url)
        self.host = parts.hostname or "127.0.0.1"
        self.port = parts.port or 80
        self.prefix = parts.path.rstrip("/")
        self.origin = f"http://{self.host}:{self.port}"
        self.cookie: str | None = None
        self.entry_headers: dict = {}
        if redeem and parts.path.startswith("/launch/"):
            status, self.entry_headers, body = self.call("GET", parts.path)
            assert status == 303, body[:400]
            self.prefix = self.entry_headers["location"].rstrip("/")

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
    # These inherited security cases exercise the optional-login mode. Tests of
    # the default cookie-free mode explicitly opt out of this fixture.
    require_login = True

    def board(self, **options):
        board = super().board(**options)
        if self.require_login:
            from hey_my_buddy.install.launcher import write_private
            settings = {"port": 0, "requireLogin": True, "revision": 0}
            write_private(board.directory / "console-settings.json", settings)
            board.console._settings = settings
            board.console.require_login = True
        return board

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
        self.assertEqual(sorted(snapshot), sorted(["consoleSession", "consoleAccess", "harnesses", "backupPreflight", "csrfToken", "tableRevision", "gate", "configuration", "configurationError", "profiles", "modelConcurrency", "routingHealth", "unavailableProfileCount", "familyAnnotations", "familyPreferences", "preferenceOverrides", "preferences", "cards", "evidence", "decisions", "pendingEvidence", "sampleCounts", "tasks", "capabilities"]))
        self.assertIsNone(snapshot['configurationError'])
        self.assertEqual(snapshot["routingHealth"], board.call("health", {})["routingHealth"])
        self.assertEqual(snapshot["routingHealth"]["sampleCount"], 0)
        self.assertTrue(snapshot["csrfToken"])
        self.assertIn("httponly", browser.entry_headers["set-cookie"].lower())
        self.assertIn("samesite=strict", browser.entry_headers["set-cookie"].lower())
        self.assertIn("Max-Age=34560000", headers["set-cookie"])
        self.assertEqual(headers["cache-control"], "no-store")
        self.assertIn("content-security-policy", headers)
        self.assertNotIn("access-control-allow-origin", headers)
        self.assertNotIn(board.service.token, body.decode())
        # A second read reuses the same session without rotating it.
        status, headers, _body = browser.get("/api/console")
        self.assertEqual(status, 200)
        self.assertIn("Max-Age=34560000", headers["set-cookie"])
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
        self.assertEqual(status, 401, data)
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
        self.assertEqual(status, 401)
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

    def test_command_allowlist_and_bounded_bodies(self):
        board = self.board()
        _stated, browser = self.open_console(board)
        snapshot = browser.bootstrap()
        csrf = snapshot["csrfToken"]
        for operation in ("dispatch", "store.task_cancel", "_receipt", "console_snapshot", "unknown_operation", "task_cancel", "task_retry", "task_acknowledge",
                          "workflow_submit", "workflow_decide", "workflow_continue", "workflow_takeover",
                          "workflow_cancel", "workflow_accept", "workflow_conclude", "workflow_reclaim",
                          "workflow_acknowledge", "workflow_scope_amend",
                          "workflow_workspace_resolve", "workflow_integration_record", "workspace_cleanup_plan",
                          "workspace_cleanup_apply", "workflow_suggest"):
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
        # A writer holds the table; settings remain writable but task control is refused.
        refreshed = board.call("model_catalog_refresh", {"requestId": "cat"})
        profile = next(p for p in board.call("console_snapshot", {})["profiles"] if p["profileId"] == PROFILE_ID)
        family = {key: profile[key] for key in ("adapter", "provider", "model")}
        status, _h, data = browser.command("evaluation_write_begin", {"requestId": "w1", "expectedRevision": refreshed["tableRevision"], "kind": "human"}, csrf=csrf)
        self.assertEqual(status, 200, data)
        begin = json.loads(data)["result"]
        status, _h, data = browser.command("task_cancel", {"runId": run_id, "reason": "console operator"}, csrf=csrf)
        self.assertEqual(status, 404, data)
        self.assertEqual(board.call("task_get", {"runId": run_id})["task"]["status"], "queued")
        # A publish through the browser is the same operation the CLI uses: it is
        # validated, gated and persisted exactly once.
        status, _h, data = browser.command(
            "user_policy_publish",
            {
                "commandId": "browser-publish",
                "writerId": begin["writerId"],
                "generation": begin["generation"],
                "writerToken": begin["writerToken"],
                "expectedRevision": refreshed["tableRevision"],
                "profileSettings": [{"profileId": PROFILE_ID, "enabled": True}],
                "modelConcurrency": [{**family, "limit": 4}],
            },
            csrf=csrf,
        )
        self.assertEqual(status, 200, data)
        result = json.loads(data)["result"]
        self.assertEqual(result["revision"], refreshed["tableRevision"] + 1)
        self.assertTrue(next(p for p in board.call("console_snapshot", {})["profiles"] if p["profileId"] == PROFILE_ID)["enabled"])
        status, _headers, data = browser.get("/api/console")
        self.assertEqual(status, 200)
        capacity = next(row for row in json.loads(data)["modelConcurrency"]
                        if all(row[key] == value for key, value in family.items()))
        self.assertEqual(capacity, {**family, "limit": 4, "active": 0})
        # Invalid input is a safe 4xx envelope, never a traceback.
        status, _h, data = browser.command("objective_stop", {"objectiveId": "run:" + run_id, "commandId": "invalid-stop", "nonsense": True}, csrf=csrf)
        self.assertEqual(status, 400, data)
        payload = json.loads(data)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error"]["code"], "INVALID_ARGUMENT")
        self.assertNotIn("Traceback", data.decode())
        # Snapshot reads expose no control or session secrets.
        snapshot = snapshot_of(board)
        encoded = json.dumps(snapshot)
        self.assertNotIn(begin["writerToken"], encoded)
        self.assertNotIn("test-token", encoded)
        def keys(value):
            if isinstance(value, dict):
                for key, child in value.items():
                    yield key.lower()
                    yield from keys(child)
            elif isinstance(value, list):
                for child in value:
                    yield from keys(child)
        # tokenUsage/inputTokens are public counters, not credential fields.
        self.assertFalse(set(keys(snapshot)) & {"token", "writertoken", "controltoken", "servicetoken", "agentcredential", "accesstoken"})

    def test_browser_reads_decisions_but_cannot_launch_maintenance(self):
        """The console reads facts/history; only a Harness prepares maintenance."""
        board = self.board()
        _stated, browser = self.open_console(board)
        csrf = browser.bootstrap()["csrfToken"]
        created = board.call("selection_request", {"requestId": "host-pick", "task": "inspect routing"})
        status, _headers, data = browser.command(
            "selection_get", {"decisionId": created["decisionId"]}, csrf=csrf
        )
        self.assertEqual(status, 200, data)
        self.assertEqual(json.loads(data)["result"]["decision"],
                         board.call("selection_get", {"decisionId": created["decisionId"]})["decision"])
        before_tasks = board.store.count_tasks()
        with board.store.db.read() as connection:
            before_events = connection.execute("SELECT COUNT(*) FROM events").fetchone()[0]
        for operation in ("evaluation_prepare", "evaluation_maintain", "evaluation_evidence_record",
                          "evaluation_reader_begin", "selection_request"):
            with self.subTest(operation=operation):
                status, _headers, data = browser.command(operation, {"requestId": "not-authorized"}, csrf=csrf)
                self.assertEqual(status, 404, data)
                self.assertEqual(json.loads(data)["error"]["code"], "METHOD_NOT_FOUND")
        status, _headers, data = browser.command("evaluation_history", {"limit": 20}, csrf=csrf)
        self.assertEqual(status, 200, data)
        self.assertEqual(json.loads(data)["result"], board.call("evaluation_history", {"limit": 20}))
        self.assertEqual(board.store.count_tasks(), before_tasks)
        with board.store.db.read() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM events").fetchone()[0], before_events)
        _stated, browser = self.open_console(board)
        from hey_my_buddy.blackboard.catalog import catalog

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


class ConsoleTaskHistoryTests(ConsoleTestCase, WorkflowTestCase):
    """The read-only ``GET /api/tasks`` history route over the named task_list."""

    def execution_task(self, board, request_id: str, *, cwd=None):
        return board.call(
            "task_submit",
            {
                "requestId": request_id,
                "task": f"ordinary {request_id}",
                "cwd": str(cwd or self.workdir()),
                "adapter": "command",
                "argv": ["/bin/echo", request_id],
            },
        )["task"]

    def test_history_route_serves_the_same_named_operation_and_session(self):
        board = self.board()
        created = self.execution_task(board, "history-1")
        _stated, browser = self.open_console(board)
        status, headers, body = browser.get("/api/tasks?limit=5")
        self.assertEqual(status, 200, body[:300])
        self.assertIn("application/json", headers["content-type"])
        self.assertEqual(headers["cache-control"], "no-store")
        self.assertNotIn("access-control-allow-origin", headers)
        self.assertIn("Max-Age=34560000", headers["set-cookie"])
        payload = json.loads(body)
        self.assertEqual(sorted(payload), sorted(["runs", "tasks", "total", "cursor", "nextCursor"]))
        self.assertEqual(payload["runs"], payload["tasks"])
        self.assertEqual(payload["total"], 1)
        self.assertEqual(payload["runs"][0]["runId"], created["runId"])
        self.assertIn("delegation", payload["runs"][0])
        direct = board.call("task_list", {"limit": 5})
        self.assertEqual([row["runId"] for row in payload["runs"]], [row["runId"] for row in direct["runs"]])
        self.assertEqual(payload["total"], direct["total"])
        # A public URL is not a credential. History reads require a cookie, but no
        # CSRF header or write permission; task_list is not a POST command.
        anonymous = Browser(browser.origin + browser.prefix + "/")
        status, _headers, body = anonymous.get("/api/tasks")
        self.assertEqual(status, 401, body[:300])
        csrf = browser.bootstrap()["csrfToken"]
        status, _headers, body = browser.command("task_list", {}, csrf=None)
        self.assertEqual(status, 403, body[:300])
        status, _headers, body = browser.command("task_list", {}, csrf=csrf)
        self.assertEqual(status, 404, body[:300])
        self.assertEqual(json.loads(body)["error"]["code"], "METHOD_NOT_FOUND")

    def test_roots_only_history_pages_over_http(self):
        board = self.board()
        goals = [
            self.submit(
                board,
                request_id=f"goal-{index}",
                host_id="host-a",
                cwd=str(self.workdir(f"goal-{index}")),
                kind="worktree",
            )["runId"]
            for index in range(3)
        ]
        self.execution_task(board, "plain")
        _stated, browser = self.open_console(board)
        status, _headers, body = browser.get("/api/tasks?rootsOnly=true&limit=2")
        self.assertEqual(status, 200, body[:300])
        first = json.loads(body)
        self.assertEqual(first["total"], 3)
        self.assertEqual(len(first["runs"]), 2)
        self.assertIsNotNone(first["nextCursor"])
        self.assertTrue(all(row["delegation"]["kind"] == "goal" for row in first["runs"]))
        status, _headers, body = browser.get(f"/api/tasks?rootsOnly=true&limit=2&before={first['nextCursor']}")
        self.assertEqual(status, 200, body[:300])
        second = json.loads(body)
        self.assertEqual(len(second["runs"]), 1)
        self.assertIsNone(second["nextCursor"])
        seen = {row["runId"] for row in first["runs"]} | {row["runId"] for row in second["runs"]}
        self.assertEqual(seen, set(goals))
        # A filtered history never counts the excluded rows.
        status, _headers, body = browser.get("/api/tasks?rootsOnly=true&hostId=host-a&query=goal")
        self.assertEqual(json.loads(body)["total"], 3)
        status, _headers, body = browser.get("/api/tasks?rootsOnly=true&hostId=nobody")
        self.assertEqual(json.loads(body)["total"], 0)

    def test_snapshot_cursor_resumes_the_history_route(self):
        board = self.board()
        for index in range(105):
            self.execution_task(board, f"bulk-{index:03d}")
        _stated, browser = self.open_console(board)
        snapshot = browser.bootstrap()
        self.assertEqual(snapshot["tasks"]["total"], 105)
        self.assertEqual(len(snapshot["tasks"]["runs"]), 100)
        cursor = snapshot["tasks"]["nextCursor"]
        self.assertIsNotNone(cursor)
        status, _headers, body = browser.get(f"/api/tasks?limit=100&before={cursor}")
        self.assertEqual(status, 200, body[:300])
        page = json.loads(body)
        self.assertEqual(page["total"], 105)
        self.assertEqual(len(page["runs"]), 5)
        self.assertIsNone(page["nextCursor"])
        seen = {row["runId"] for row in snapshot["tasks"]["runs"]} | {row["runId"] for row in page["runs"]}
        self.assertEqual(len(seen), 105)

    def test_history_parameters_are_whitelisted_typed_and_single(self):
        board = self.board()
        self.execution_task(board, "history-1")
        _stated, browser = self.open_console(board)
        for query, field in (
            ("unknown=1", "unknown"),
            ("limit=1&limit=2", "limit"),
            ("%6cimit=1&limit=2", "limit"),
            ("rootsOnly=maybe", "rootsOnly"),
            ("rootsOnly=", "rootsOnly"),
            ("limit=abc", "limit"),
            ("limit=-1", "limit"),
            ("limit=101", "limit"),
            ("offset=x", "offset"),
            ("before=not-a-cursor", "before"),
            ("query=", "query"),
            ("filter=everything", "filter"),
            ("state=not-a-state", "state"),
            ("adapter=unknown", "adapter"),
        ):
            with self.subTest(query=query):
                status, _headers, body = browser.get(f"/api/tasks?{query}")
                self.assertEqual(status, 400, body[:300])
                error = json.loads(body)["error"]
                self.assertEqual(error["code"], "INVALID_ARGUMENT")
                self.assertIn(field, error["message"])
        # Typed parameters arrive at the store as real booleans and integers.
        status, _headers, body = browser.get("/api/tasks?rootsOnly=false&limit=1&offset=0")
        self.assertEqual(status, 200, body[:300])
        self.assertEqual(json.loads(body)["total"], 1)

    def test_history_route_refuses_foreign_origin_host_and_cross_site_reads(self):
        board = self.board()
        _stated, browser = self.open_console(board)
        status, headers, _body = browser.get("/api/tasks", headers={"Origin": "http://evil.example"})
        self.assertEqual(status, 403)
        self.assertNotIn("access-control-allow-origin", headers)
        status, _headers, _body = browser.get("/api/tasks", headers={"Host": "localhost:1"})
        self.assertEqual(status, 403)
        status, _headers, _body = browser.get("/api/tasks", headers={"Sec-Fetch-Site": "cross-site"})
        self.assertEqual(status, 403)
        # A crafted path under the prefix is still just a task identity or a 404.
        self.assertEqual(browser.get("/api/tasks/..%2f..%2fcontrol.json")[0], 404)
        self.assertEqual(browser.get("/api/tasks?before=../../etc/passwd")[0], 400)


class ConsoleAssetTests(ConsoleTestCase):
    def test_missing_build_is_an_honest_setup_page_but_the_api_works(self):
        board = self.board()
        _stated, browser = self.open_console(board, assets=False)
        status, headers, body = browser.get("/")
        self.assertEqual(status, 503)
        text = body.decode()
        self.assertIn("Console assets are not built", text)
        self.assertIn("/api/console", text)
        self.assertIn("assets/index.html", text)
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

    def test_console_close_and_reopen_requires_cookie_at_stable_path(self):
        board = self.board()
        stated, browser = self.open_console(board)
        old_prefix = browser.prefix
        board.call("console", {"action": "close"})
        status = board.call("console", {"action": "status"})
        self.assertFalse(status["running"])
        # A fresh ticket does not make the stable URL public.
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
        self.assertEqual(status, 401)


class ConsoleDaemonTests(ConsoleTestCase):
    def test_cli_console_opens_and_closes_the_real_daemon_surface(self):
        with self.daemon():
            code, opened = self.cli("console", json.dumps({"action": "open", "browser": False}))
            self.assertEqual(code, 0, opened)
            self.assertFalse(opened["readOnly"])
            self.assertTrue(opened["url"].startswith("http://127.0.0.1:"))
            browser = Browser(opened["url"])
            status, headers, body = browser.get("/api/console")
            self.assertEqual(status, 200, body[:300])
            snapshot = json.loads(body)
            self.assertEqual(snapshot["tableRevision"], 0)
            self.assertIn("csrfToken", snapshot)
            self.assertNotIn("access-control-allow-origin", headers)
            code, status_reply = self.cli("console", json.dumps({"action": "status"}))
            self.assertEqual(code, 0, status_reply)
            self.assertTrue(status_reply["running"])
            # The integrated console frontend ships built assets, so the daemon serves
            # a real bundle instead of the "assets not built" setup page.
            self.assertTrue(status_reply["assetsBuilt"])
            code, closed = self.cli("console", json.dumps({"action": "close"}))
            self.assertEqual(code, 0, closed)
            self.assertTrue(closed["closed"])

    def test_cli_evaluation_operations_round_trip_through_ctwo(self):
        with self.daemon():
            code, caps = self.cli("capabilities", "{}")
            self.assertEqual(code, 0, caps)
            self.assertIn("console_snapshot", caps["operations"]["control"])
            # Selection and maintenance are advertised, but only through their real
            # bounded semantics: no silent fallback and a card-only adoption scope.
            for operation in ("selection_request", "selection_get", "evaluation_prepare", "evaluation_history"):
                self.assertIn(operation, caps["operations"]["control"])
            self.assertNotIn("evaluation_maintain", caps["operations"]["control"])
            self.assertIn("selectionFallback", caps["limitations"])
            self.assertIn("maintenanceScope", caps["limitations"])
            self.assertIn("card-only", caps["limitations"]["maintenanceScope"])
            self.assertIn("not claimed", caps["limitations"]["osIsolation"])
            code, snapshot = self.cli("console-snapshot", "{}")
            self.assertEqual(code, 0, snapshot)
            self.assertEqual(snapshot["tableRevision"], 0)
            code, refreshed = self.cli("model-catalog-refresh", json.dumps({"requestId": "cli-cat"}))
            self.assertEqual(code, 0, refreshed)
            self.assertEqual(refreshed["catalog"]["source"], f"file:{self.directory / 'model-catalog.json'}")
            code, opened = self.cli("console", json.dumps({"action": "open", "browser": False}))
            self.assertEqual(code, 0, opened)
            browser = Browser(opened["url"])
            csrf = browser.bootstrap()["csrfToken"]
            status, _headers, body = browser.command("evaluation_write_begin", {"requestId": "cli-1", "expectedRevision": refreshed["tableRevision"], "kind": "human"}, csrf=csrf)
            self.assertEqual(status, 200, body)
            begin = json.loads(body)["result"]
            status, _headers, body = browser.command("user_policy_publish", {
                "commandId": "cli-publish", "writerId": begin["writerId"],
                "generation": begin["generation"], "writerToken": begin["writerToken"],
                "expectedRevision": begin["tableRevision"],
                "profileSettings": [{"profileId": PROFILE_ID, "enabled": True}],
            }, csrf=csrf)
            self.assertEqual(status, 200, body)
            published = json.loads(body)["result"]
            self.assertEqual(published["revision"], refreshed["tableRevision"] + 1)
            code, evidence = self.cli(
                "evaluation-evidence-record",
                json.dumps({"profileId": PROFILE_ID, "kind": "manual", "summary": "cli note", "source": "cli"}),
            )
            self.assertEqual(code, 0, evidence)
            self.assertFalse(evidence["counted"])
            code, reader = self.cli("evaluation-reader-begin", json.dumps({"kind": "selection", "revision": published["revision"]}))
            self.assertEqual(code, 0, reader)
            code, released = self.cli("evaluation-reader-release", json.dumps({"readerId": reader["readerId"]}))
            self.assertEqual(code, 0, released)
            self.assertTrue(released["released"])
            code, final = self.cli("console-snapshot", "{}")
            self.assertEqual(code, 0, final)
            self.assertEqual(final["tableRevision"], published["revision"])
            self.assertEqual(final["pendingEvidence"], 1)
            self.assertTrue(next(p for p in final["profiles"] if p["profileId"] == PROFILE_ID)["enabled"])
            self.assertEqual(len(final["evidence"]), 1)
            self.assertNotIn("autoMaintain", final["configuration"])
            self.assertEqual(final["sampleCounts"].get(PROFILE_ID, 0), 0)
            code, prepared = self.cli("evaluation-prepare", json.dumps({"requestId": "cli-prepare", "limit": 1}))
            self.assertEqual(code, 0, prepared)
            self.assertEqual(prepared["tableRevision"], published["revision"])
            self.assertEqual(prepared["newEvidenceIds"], [])
            code, repeated = self.cli("evaluation-prepare", json.dumps({"requestId": "cli-prepare", "limit": 1}))
            self.assertEqual(code, 0, repeated)
            self.assertEqual(prepared, repeated)
            code, history = self.cli("evaluation-history", json.dumps({"limit": 1}))
            self.assertEqual(code, 0, history)
            self.assertEqual([row["revision"] for row in history["revisions"]], [published["revision"]])
            self.assertGreaterEqual(history["total"], 2)  # call-triggered catalog refreshes may also publish
            self.assertIsNotNone(history["nextCursor"])


class ConsoleDecisionBrowseTests(ConsoleTestCase):
    """The bounded decision/routing reads are reachable over the real console HTTP path."""

    def test_selection_list_is_reachable_over_the_console_boundary(self):
        board = self.board()
        board.call("selection_request", {"requestId": "http-select", "task": "browse over http"})
        # A retained maintenance record is historical data, never executable input.
        with board.store.db.write() as connection:
            connection.execute("INSERT INTO evaluation_decisions(decision_id,status,task,table_revision,created_at) VALUES('archived-maintenance','needs-host','prior proposal',0,'2026-01-01T00:00:00.000Z')")
            connection.execute("INSERT INTO decision_requests(decision_id,request_id,kind,input_fingerprint,created_at,updated_at) VALUES('archived-maintenance','archived-maintenance','maintain','historical','2026-01-01T00:00:00.000Z','2026-01-01T00:00:00.000Z')")
        _stated, browser = self.open_console(board)
        csrf = browser.bootstrap()["csrfToken"]

        status, _headers, data = browser.command("selection_list", {"kind": "maintain", "limit": 5}, csrf=csrf)
        self.assertEqual(status, 200, data)
        listed = json.loads(data)["result"]
        self.assertEqual(listed["total"], 1)
        self.assertEqual([item["decisionId"] for item in listed["decisions"]], ["archived-maintenance"])
        self.assertEqual(listed["decisions"][0]["kind"], "maintain")
        self.assertIsNone(listed["nextCursor"])

        status, _headers, data = browser.command("selection_list", {"limit": 0}, csrf=csrf)
        self.assertEqual(status, 400, data)
        self.assertEqual(json.loads(data)["error"]["code"], "INVALID_ARGUMENT")
        for invalid_params in (None, False, 0, [], ""):
            status, _headers, data = browser.command("selection_list", invalid_params, csrf=csrf)
            self.assertEqual(status, 400)
            self.assertEqual(json.loads(data)["error"]["code"], "INVALID_ARGUMENT")
        # The CLI spelling is a client alias, never an operation name on the boundary.
        status, _headers, data = browser.command("selection-list", {}, csrf=csrf)
        self.assertEqual(status, 404, data)
        self.assertEqual(json.loads(data)["error"]["code"], "METHOD_NOT_FOUND")

    def test_workflow_get_routing_history_is_reachable_over_the_console_boundary(self):
        board = self.board()
        plain = board.call(
            "task_submit",
            {
                "requestId": "console-plain",
                "task": "ordinary",
                "cwd": str(self.workdir()),
                "adapter": "command",
                "argv": ["true"],
            },
        )
        _stated, browser = self.open_console(board)
        csrf = browser.bootstrap()["csrfToken"]
        status, _headers, data = browser.command(
            "workflow_get",
            {"runId": plain["task"]["runId"], "routingHistory": {"limit": 5}},
            csrf=csrf,
        )
        self.assertEqual(status, 200, data)
        result = json.loads(data)["result"]
        self.assertFalse(result["governed"])
        self.assertEqual(result["routingHistory"], {"entries": [], "nextCursor": None, "total": 0})
        status, _headers, data = browser.command(
            "workflow_get",
            {"runId": plain["task"]["runId"], "routingHistory": {"before": 0}},
            csrf=csrf,
        )
        self.assertEqual(status, 400, data)
        self.assertEqual(json.loads(data)["error"]["code"], "INVALID_ARGUMENT")


if __name__ == "__main__":
    unittest.main()


class ConsoleObjectiveTests(ConsoleTestCase, WorkflowTestCase):
    """Authenticated read-only objective routes over the named objective operations."""

    def test_objective_list_and_timeline_routes(self):
        board = self.board()
        first = self.submit(board, request_id="objective-http-1", kind="worktree", objective={"title": "HTTP group"})
        loose = self.submit(board, request_id="objective-http-2", kind="worktree", task="loose root")
        _stated, browser = self.open_console(board)
        status, headers, body = browser.get("/api/objectives?limit=5&filter=all")
        self.assertEqual(status, 200, body[:300])
        self.assertEqual(headers["cache-control"], "no-store")
        page = json.loads(body)
        self.assertEqual(page, json.loads(json.dumps(board.call("objective_list", {"limit": 5, "filter": "all"}))))
        self.assertEqual({item["objectiveId"] for item in page["objectives"]},
                         {first["objectiveId"], f"run:{loose['runId']}"})
        status, _headers, body = browser.get(f"/api/objectives/{first['objectiveId']}/timeline?limit=10")
        self.assertEqual(status, 200, body[:300])
        timeline = json.loads(body)
        self.assertEqual([row["runId"] for row in timeline["rows"]], [first["runId"]])
        encoded = f"/api/objectives/run%3A{loose['runId']}/timeline"
        status, _headers, body = browser.get(encoded)
        self.assertEqual(status, 200, body[:300])
        self.assertEqual(json.loads(body)["rows"][0]["runId"], loose["runId"])
        for path, code in (("/api/objectives?unknown=1", 400), ("/api/objectives?limit=1&limit=2", 400),
                           ("/api/objectives/obj-missing/timeline", 404), ("/api/objectives/bad%20id/timeline", 404),
                           ("/api/objectives/obj-x/timeline?before=1", 400)):
            with self.subTest(path=path):
                self.assertEqual(browser.get(path)[0], code)
        anonymous = Browser(browser.origin + browser.prefix + "/")
        self.assertEqual(anonymous.get("/api/objectives")[0], 401)
