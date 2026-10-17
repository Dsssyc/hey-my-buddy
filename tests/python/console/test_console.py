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
import sys
import unittest
from pathlib import Path
from urllib.parse import urlsplit

from support import BoardTestCase, PYTHON_ROOT
from blackboard.tasks.test_workflow import WorkflowTestCase
from hey_my_buddy.errors import BoardError


BUDDY_ID = "dsh:deepseek-official:deepseek-flash:off"
BUDDY = {
    "buddyId": BUDDY_ID,
    "label": "Flash off",
    "harness": "dsh",
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
            headers["X-Hey-My-Buddy-CSRF"] = csrf
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
                f"Origin: {browser.origin}\r\nCookie: {browser.cookie}\r\nX-Hey-My-Buddy-CSRF: {csrf}\r\n"
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
            (root / "index.html").write_text("<!doctype html><div id=root>hey-my-buddy console fixture</div>")
            (root / "assets" / "app.js").write_text("export const ready = true;\n")
            (root / "assets" / "app.css").write_text("body{color:#20332e}\n")
        stated = board.call("console", {"action": "open"})
        self.assertTrue(stated["url"].startswith("http://127.0.0.1:"))
        self.assertFalse(stated["readOnly"])
        return stated, Browser(stated["url"])


class ConsoleSecurityTests(ConsoleTestCase):
    def test_reminder_threshold_requires_console_authority_and_updates_the_snapshot(self):
        board = self.board()
        _stated, browser = self.open_console(board)
        snapshot = browser.bootstrap()
        self.assertEqual(snapshot["reminderSettings"]["silenceSeconds"], 900)
        with self.assertRaises(BoardError) as refused:
            board.call("reminder_settings_set", {"silenceSeconds": 1200})
        self.assertEqual(refused.exception.code, "UNAUTHORIZED")
        csrf = snapshot["csrfToken"]
        status, _headers, body = browser.command("reminder_settings_set", {"silenceSeconds": 0}, csrf=csrf)
        self.assertEqual(status, 400, body)
        for invalid in ({"silenceSeconds": True}, {"silenceSeconds": 1200, "unexpected": 1}):
            status, _headers, body = browser.command("reminder_settings_set", invalid, csrf=csrf)
            self.assertEqual(status, 400, body)
        status, _headers, body = browser.command("reminder_settings_set", {"silenceSeconds": 1200}, csrf=csrf)
        self.assertEqual(status, 200, body)
        changed = json.loads(body)["result"]
        self.assertEqual((changed["silenceSeconds"], changed["revision"]), (1200, 2))
        self.assertEqual(browser.bootstrap()["reminderSettings"], changed)
        events = board.call("events_read", {"after": snapshot["eventCursor"]})["events"]
        self.assertEqual([event["kind"] for event in events], ["reminder.settings_changed"])
        status, _headers, body = browser.command("reminder_settings_set", {"silenceSeconds": 1200}, csrf=csrf)
        self.assertEqual(status, 200, body)
        self.assertEqual(json.loads(body)["result"], changed)
        self.assertEqual(board.call("events_read", {"after": snapshot["eventCursor"]})["events"], events)

    def test_bootstrap_is_the_snapshot_object_and_sets_a_private_session(self):
        board = self.board()
        _stated, browser = self.open_console(board)
        status, headers, body = browser.get("/api/console")
        self.assertEqual(status, 200)
        snapshot = json.loads(body)
        self.assertEqual(sorted(snapshot), sorted(["consoleSession", "consoleAccess", "reminderSettings", "harnesses", "csrfToken", "eventCursor", "configuration", "configurationError", "buddies", "modelServiceConcurrency", "routingHealth", "unavailableBuddyCount", "modelServiceNotes", "preferences", "decisions", "tasks", "capabilities"]))
        self.assertEqual(snapshot["reminderSettings"]["silenceSeconds"], 900)
        self.assertIsNone(snapshot['configurationError'])
        self.assertEqual(snapshot["routingHealth"], board.call("health", {})["routingHealth"])
        self.assertEqual(snapshot["routingHealth"]["sampleCount"], 0)
        self.assertTrue(snapshot["csrfToken"])
        self.assertIn("httponly", browser.entry_headers["set-cookie"].lower())
        self.assertIn("samesite=strict", browser.entry_headers["set-cookie"].lower())
        self.assertIn("Max-Age=34560000", headers["set-cookie"])
        # Read routes keep one private browser copy that must always revalidate:
        # the marker check happens on the server before any 304 leaves it.
        self.assertEqual(headers["cache-control"], "private, max-age=0, must-revalidate")
        self.assertEqual(headers["vary"], "Accept-Encoding")
        self.assertTrue(headers["etag"].startswith('"'))
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
        body = {"operation": "user_policy_publish", "params": {"commandId": "w1", "buddySettings": []}}

        # No session cookie at all.
        status, _headers, data = http_call(
            browser.host,
            browser.port,
            "POST",
            browser.prefix + "/api/command",
            body=body,
            headers={"Host": f"{browser.host}:{browser.port}", "Origin": browser.origin, "X-Hey-My-Buddy-CSRF": csrf},
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
            headers={"Cookie": "hey_my_buddy_console_session=forged", "X-Hey-My-Buddy-CSRF": csrf},
        )
        self.assertEqual(status, 401)
        # Wrong CSRF value.
        status, _headers, _data = browser.call(
            "POST", browser.prefix + "/api/command", body=body, headers={"X-Hey-My-Buddy-CSRF": "nope"}
        )
        self.assertEqual(status, 403)
        self.assertEqual(board.call("console_snapshot", {})["configuration"], snapshot["configuration"])

    def test_foreign_origin_host_and_cross_site_requests_are_refused(self):
        board = self.board()
        _stated, browser = self.open_console(board)
        snapshot = browser.bootstrap()
        csrf = snapshot["csrfToken"]
        body = {"operation": "model_catalog_refresh", "params": {"requestId": "origin-check"}}
        good_headers = {"X-Hey-My-Buddy-CSRF": csrf, "Content-Type": "application/json"}

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
            headers={"X-Hey-My-Buddy-CSRF": csrf},
        )
        self.assertEqual(status, 400)
        self.assertEqual(json.loads(data)["error"]["code"], "INVALID_ARGUMENT")
        # A missing Content-Length, an empty body and an oversized body are all refused
        # before any command reaches the service.
        self.assertIn("411", raw_status(browser, csrf, extra_headers=""))
        self.assertIn("413", raw_status(browser, csrf, extra_headers="Content-Length: 1048577\r\n"))
        status, _h, _data = browser.call(
            "POST", browser.prefix + "/api/command", body=b"", headers={"X-Hey-My-Buddy-CSRF": csrf}
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
                headers={"X-Hey-My-Buddy-CSRF": csrf, "Content-Type": "application/json"},
            )
        except (ConnectionResetError, BrokenPipeError):
            status = 413
        self.assertEqual(status, 413)

    def test_commands_use_the_same_validated_operations(self):
        board = self.board()
        submitted = board.submit_execution(**{
                "requestId": "console-task",
                "task": "keep working",
                "cwd": str(self.workdir()),
                "adapter": "command",
                "argv": ["/bin/echo", "hi"],
            })
        run_id = submitted["task"]["runId"]
        _, browser = self.open_console(board)
        csrf = browser.bootstrap()["csrfToken"]
        board.call("model_catalog_refresh", {"requestId": "cat"})
        buddy = next(p for p in board.call("console_snapshot", {})["buddies"] if p["buddyId"] == BUDDY_ID)
        model_service = {key: buddy[key] for key in ("harness", "provider", "model")}
        status, _h, data = browser.command("task_cancel", {"runId": run_id, "reason": "console operator"}, csrf=csrf)
        self.assertEqual(status, 404, data)
        self.assertEqual(board.call("task_get", {"runId": run_id})["task"]["status"], "queued")
        # A publish through the browser is the same operation the CLI uses: it is
        # validated and persisted exactly once.
        status, _h, data = browser.command(
            "user_policy_publish",
            {
                "commandId": "browser-publish",
                "buddySettings": [{"buddyId": BUDDY_ID, "enabled": True}],
                "modelServiceConcurrency": [{**model_service, "limit": 4}],
            },
            csrf=csrf,
        )
        self.assertEqual(status, 200, data)
        result = json.loads(data)["result"]
        self.assertTrue(result["published"])
        self.assertTrue(next(p for p in board.call("console_snapshot", {})["buddies"] if p["buddyId"] == BUDDY_ID)["enabled"])
        status, _headers, data = browser.get("/api/console")
        self.assertEqual(status, 200)
        capacity = next(row for row in json.loads(data)["modelServiceConcurrency"]
                        if all(row[key] == value for key, value in model_service.items()))
        self.assertEqual(capacity, {**model_service, "limit": 4, "active": 0})
        # Invalid input is a safe 4xx envelope, never a traceback.
        status, _h, data = browser.command("macro_task_stop", {"macroTaskId": "run:" + run_id, "commandId": "invalid-stop", "nonsense": True}, csrf=csrf)
        self.assertEqual(status, 400, data)
        payload = json.loads(data)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error"]["code"], "INVALID_ARGUMENT")
        self.assertNotIn("Traceback", data.decode())
        # Snapshot reads expose no control or session secrets.
        snapshot = snapshot_of(board)
        encoded = json.dumps(snapshot)
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

    def test_browser_reads_decisions_but_cannot_launch_router_calls(self):
        """The console reads selection records but cannot launch a Router call."""
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
        for operation in ("evaluation_history", "evaluation_prepare", "evaluation_maintain", "evaluation_evidence_record",
                          "evaluation_reader_begin", "evaluation_write_begin", "selection_request"):
            with self.subTest(operation=operation):
                status, _headers, data = browser.command(operation, {"requestId": "not-authorized"}, csrf=csrf)
                self.assertEqual(status, 404, data)
                self.assertEqual(json.loads(data)["error"]["code"], "METHOD_NOT_FOUND")
        self.assertEqual(board.store.count_tasks(), before_tasks)
        with board.store.db.read() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM events").fetchone()[0], before_events)
        _stated, browser = self.open_console(board)
        from hey_my_buddy.blackboard.catalog import catalog

        original = catalog.discover
        catalog.discover = lambda: (_ for _ in ()).throw(AssertionError("GET must not discover models"))
        try:
            snapshot = browser.bootstrap()
            self.assertNotIn("tableRevision", snapshot)
        finally:
            catalog.discover = original


def snapshot_of(board) -> dict:
    return board.call("console_snapshot", {})


    def test_task_detail_route_returns_the_existing_view(self):
        board = self.board()
        submitted = board.submit_execution(**{
                "requestId": "detail-task",
                "task": "produce a detail view",
                "cwd": str(self.workdir()),
                "adapter": "command",
                "argv": ["/bin/echo", "detail"],
            })
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
        self.assertEqual(browser.get("/api/tasks")[0], 404)



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
        self.assertNotIn("gate", snapshot)
        self.assertFalse(snapshot["capabilities"]["consoleAssets"])

    def test_assets_are_served_with_headers_and_traversal_is_refused(self):
        board = self.board()
        (self.directory / "outside.txt").write_text("not an asset")
        _, browser = self.open_console(board, assets=True)
        status, headers, body = browser.get("/")
        self.assertEqual(status, 200)
        self.assertIn("hey-my-buddy console fixture", body.decode())
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
        self.assertIn("hey-my-buddy console fixture", body.decode())

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
    def test_r05_private_daemon_reads_owned_catalog_without_native_discovery(self):
        """A missing catalog must trip a recording, model-free native decoy."""
        guard = self.directory / "discovery-guard"
        guard.mkdir(mode=0o700)
        trace = self.directory / "native-cli-calls.jsonl"
        decoy = guard / "reject-native.py"
        decoy.write_text(
            f"#!{sys.executable}\n"
            "import json, sys\n"
            f"with open({str(trace)!r}, 'a') as stream:\n"
            "    stream.write(json.dumps(sys.argv[1:]) + '\\n')\n"
            "raise SystemExit(86)\n"
        )
        decoy.chmod(0o700)
        # Discovery ignores HOME when resolving system account homes. Intercept
        # its entry before it can inspect any installed CLI or account metadata.
        # The decoy still executes and records the harness that tried discovery.
        (guard / "sitecustomize.py").write_text(
            "import subprocess, sys\n"
            "from hey_my_buddy.buddy.harnesses import discovery\n"
            "def reject(harness, **kwargs):\n"
            f"    subprocess.run([sys.executable, {str(decoy)!r}, harness], check=False)\n"
            "    raise RuntimeError('R-05: native discovery reached the rejecting CLI')\n"
            "discovery.candidate_snapshot = reject\n"
        )
        environment = {
            "PYTHONPATH": os.pathsep.join((str(guard), str(PYTHON_ROOT), os.environ.get("PYTHONPATH", ""))),
            **{f"HMB_{name}_CLI": str(decoy) for name in ("CLAUDE", "CODEX", "DSH", "ZCODE")},
        }
        payload = json.loads(self._catalog_fixture_path.read_text())
        model = payload["providers"][0]["models"][0]
        model["id"] = "r05-first-private-model"
        fixture = self.catalog_fixture(payload)
        try:
            with self.daemon(env=environment):
                code, health = self.cli("health", env=environment)
                self.assertEqual(code, 0, health)
                self.assertEqual(Path(health["stateDir"]), self.directory)
                code, capabilities = self.cli("harnesses", "{}", env=environment)
                self.assertEqual(code, 0, capabilities)
                self.assertFalse(trace.exists(), trace.read_text() if trace.exists() else "")
                code, opened = self.cli("console", json.dumps({"action": "open", "browser": False}), env=environment)
                self.assertEqual(code, 0, opened)
                browser = Browser(opened["url"])
                csrf = browser.bootstrap()["csrfToken"]
                status, _headers, body = browser.command(
                    "model_catalog_refresh", {"requestId": "r05-http-catalog"}, csrf=csrf,
                )
                self.assertEqual(status, 200, body)
                self.assertFalse(trace.exists(), trace.read_text() if trace.exists() else "")
                refreshed = json.loads(body)["result"]["catalog"]
                self.assertEqual(refreshed["source"], f"file:{fixture}", "R-05: private fixture source")
                self.assertEqual(refreshed["providers"][0]["models"][0]["id"], model["id"], "R-05: private fixture content")
                # Re-read changed file contents through the real CLI and transport;
                # a cached constant or a different fixture cannot satisfy this check.
                model["id"] = "r05-second-private-model"
                fixture.write_text(json.dumps(payload))
                code, refreshed = self.cli("model-catalog-refresh", json.dumps({"requestId": "r05-cli-catalog"}), env=environment)
                self.assertEqual(code, 0, refreshed)
                self.assertFalse(trace.exists(), trace.read_text() if trace.exists() else "")
                self.assertEqual(refreshed["catalog"]["source"], f"file:{fixture}", "R-05: private fixture source after rewrite")
                self.assertEqual(refreshed["catalog"]["providers"][0]["models"][0]["id"], model["id"], "R-05: private fixture content after rewrite")
                self.assertIn(model["id"], {buddy["model"] for buddy in browser.bootstrap()["buddies"]})
        finally:
            # CLI, HTTP and bootstrap failures must still identify a discovery
            # attempt before the generic transport/status assertion can hide it.
            self.assertFalse(
                trace.exists(),
                "R-05: attempted native discovery/start; rejecting decoy calls: "
                + (trace.read_text() if trace.exists() else ""),
            )
        self.assertEqual(list((self.directory / "home").iterdir()), [])

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
            self.assertNotIn("tableRevision", snapshot)
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

    def test_cli_policy_publish_round_trip_through_ctwo(self):
        with self.daemon():
            code, caps = self.cli("harnesses", "{}")
            self.assertEqual(code, 0, caps)
            self.assertIn("console_snapshot", caps["operations"]["control"])
            for operation in ("selection_request", "selection_get", "user_policy_publish"):
                self.assertIn(operation, caps["operations"]["control"])
            for operation in ("evaluation_prepare", "evaluation_history", "evaluation_write_begin"):
                self.assertNotIn(operation, caps["operations"]["control"])
            self.assertIn("selectionFallback", caps["limitations"])
            self.assertIn("not claimed", caps["limitations"]["osIsolation"])
            code, snapshot = self.cli("console-snapshot", "{}")
            self.assertEqual(code, 0, snapshot)
            self.assertNotIn("tableRevision", snapshot)
            code, refreshed = self.cli("model-catalog-refresh", json.dumps({"requestId": "cli-cat"}))
            self.assertEqual(code, 0, refreshed)
            self.assertEqual(refreshed["catalog"]["source"], f"file:{self.directory / 'model-catalog.json'}")
            code, opened = self.cli("console", json.dumps({"action": "open", "browser": False}))
            self.assertEqual(code, 0, opened)
            browser = Browser(opened["url"])
            csrf = browser.bootstrap()["csrfToken"]
            status, _headers, body = browser.command("user_policy_publish", {
                "commandId": "cli-publish",
                "buddySettings": [{"buddyId": BUDDY_ID, "enabled": True}],
            }, csrf=csrf)
            self.assertEqual(status, 200, body)
            published = json.loads(body)["result"]
            self.assertTrue(published["published"])
            code, final = self.cli("console-snapshot", "{}")
            self.assertEqual(code, 0, final)
            self.assertNotIn("tableRevision", final)
            self.assertTrue(next(p for p in final["buddies"] if p["buddyId"] == BUDDY_ID)["enabled"])
            self.assertNotIn("autoMaintain", final["configuration"])


class ConsoleDecisionBrowseTests(ConsoleTestCase):
    """The bounded decision/routing reads are reachable over the real console HTTP path."""

    def test_selection_list_is_reachable_over_the_console_boundary(self):
        board = self.board()
        board.call("selection_request", {"requestId": "http-select", "task": "browse over http"})
        _stated, browser = self.open_console(board)
        csrf = browser.bootstrap()["csrfToken"]

        status, _headers, data = browser.command("selection_list", {"limit": 5}, csrf=csrf)
        self.assertEqual(status, 200, data)
        listed = json.loads(data)["result"]
        self.assertEqual(listed["total"], 1)
        self.assertEqual(len(listed["decisions"]), 1)
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
        plain = board.submit_execution(**{
                "requestId": "console-plain",
                "task": "ordinary",
                "cwd": str(self.workdir()),
                "adapter": "command",
                "argv": ["true"],
            })
        _stated, browser = self.open_console(board)
        csrf = browser.bootstrap()["csrfToken"]
        status, _headers, data = browser.command(
            "workflow_get",
            {"runId": plain["task"]["runId"], "routingHistory": {"limit": 5}},
            csrf=csrf,
        )
        self.assertEqual(status, 200, data)
        result = json.loads(data)["result"]
        self.assertEqual(result["task"]["lineage"]["kind"], "execution")
        self.assertNotIn("microTask", result)
        self.assertEqual(result["routingHistory"], {"entries": [], "nextCursor": None, "total": 0})
        status, _headers, data = browser.command(
            "workflow_get",
            {"runId": plain["task"]["runId"], "routingHistory": {"before": 0}},
            csrf=csrf,
        )
        self.assertEqual(status, 400, data)
        self.assertEqual(json.loads(data)["error"]["code"], "INVALID_ARGUMENT")

    def test_message_list_reads_the_inquiries_of_one_run_over_the_console_boundary(self):
        board = self.board()
        run = board.submit_execution(**{
                "requestId": "console-inquiry",
                "task": "ordinary",
                "cwd": str(self.workdir()),
                "adapter": "command",
                "argv": ["true"],
            })["task"]["runId"]
        posted = board.call("message_post", {"runId": run, "inquiryId": "ask-1", "question": "Status?"})["message"]
        _stated, browser = self.open_console(board)
        csrf = browser.bootstrap()["csrfToken"]

        status, _headers, data = browser.command("message_list", {"runId": run, "limit": 200}, csrf=csrf)
        self.assertEqual(status, 200, data)
        messages = json.loads(data)["result"]["messages"]
        self.assertEqual([message["messageId"] for message in messages], [posted["messageId"]])
        self.assertEqual(messages[0]["question"], "Status?")
        status, _headers, data = browser.command("message_list", {"runId": run, "limit": 201}, csrf=csrf)
        self.assertEqual(status, 400, data)
        self.assertEqual(json.loads(data)["error"]["code"], "INVALID_ARGUMENT")
        # The browser reads inquiries but cannot post or answer them.
        for operation in ("message_post", "message_update"):
            status, _headers, data = browser.command(operation, {"runId": run, "inquiryId": "ask-2", "question": "?"}, csrf=csrf)
            self.assertEqual(status, 404, data)
            self.assertEqual(json.loads(data)["error"]["code"], "METHOD_NOT_FOUND")

    def test_a_browser_cannot_bring_its_own_console_authority_to_a_read(self):
        board = self.board()
        _stated, browser = self.open_console(board)
        csrf = browser.bootstrap()["csrfToken"]
        for operation in ("message_list", "workflow_get"):
            status, _headers, data = browser.command(
                operation, {"runId": "unknown", "consoleAuthority": {"sessionId": "forged"}}, csrf=csrf
            )
            self.assertEqual(status, 400, data)
            self.assertEqual(json.loads(data)["error"]["code"], "INVALID_ARGUMENT")
            self.assertIn("consoleAuthority", json.loads(data)["error"]["message"])

    def test_the_preview_answers_only_what_the_real_console_allows(self) -> None:
        from hey_my_buddy.console.console_sessions import READ_OPERATIONS
        from hey_my_buddy.console.server import CONSOLE_OPERATIONS

        probes = Path(__file__).resolve().parents[3] / "tests" / "probes"
        sys.path.insert(0, str(probes))
        try:
            import macro_task_console_preview as preview
        finally:
            sys.path.remove(str(probes))
        self.assertTrue(set(preview.ALLOWED_OPERATIONS) <= set(CONSOLE_OPERATIONS))
        self.assertEqual(set(preview.READ_OPERATIONS), set(READ_OPERATIONS) & set(preview.ALLOWED_OPERATIONS))
        self.assertIn("message_list", preview.ALLOWED_OPERATIONS)
        self.assertEqual(preview.validate_fixtures(), [])


if __name__ == "__main__":
    unittest.main()


class ConsoleMacroTaskTests(ConsoleTestCase, WorkflowTestCase):
    """Authenticated read-only macro task routes over the named macro task operations."""

    def test_macro_task_list_and_timeline_routes(self):
        board = self.board()
        first = self.submit(board, request_id="macro-task-http-1", kind="worktree", macroTask={"title": "HTTP group"})
        loose = self.submit(board, request_id="macro-task-http-2", kind="worktree", task="loose root")
        _stated, browser = self.open_console(board)
        status, headers, body = browser.get("/api/macro-tasks?limit=5&filter=all")
        self.assertEqual(status, 200, body[:300])
        self.assertEqual(headers["cache-control"], "private, max-age=0, must-revalidate")
        page = json.loads(body)
        self.assertEqual(page, json.loads(json.dumps(board.call("macro_task_list", {"limit": 5, "filter": "all"}))))
        self.assertEqual(page["totals"], {"all": 2, "active": 2, "host": 0, "review": 0})
        self.assertNotIn("total", page)
        self.assertEqual({item["macroTaskId"] for item in page["macroTasks"]},
                         {first["macroTaskId"], f"run:{loose['runId']}"})
        status, _headers, body = browser.get(f"/api/macro-tasks/{first['macroTaskId']}/timeline?limit=10")
        self.assertEqual(status, 200, body[:300])
        timeline = json.loads(body)
        self.assertEqual([row["runId"] for row in timeline["rows"]], [first["runId"]])
        encoded = f"/api/macro-tasks/run%3A{loose['runId']}/timeline"
        status, _headers, body = browser.get(encoded)
        self.assertEqual(status, 200, body[:300])
        self.assertEqual(json.loads(body)["rows"][0]["runId"], loose["runId"])
        for path, code in (("/api/macro-tasks?unknown=1", 400), ("/api/macro-tasks?limit=1&limit=2", 400),
                           ("/api/macro-tasks/macro-missing/timeline", 404), ("/api/macro-tasks/bad%20id/timeline", 404),
                           ("/api/macro-tasks/macro-x/timeline?before=1", 400)):
            with self.subTest(path=path):
                self.assertEqual(browser.get(path)[0], code)
        anonymous = Browser(browser.origin + browser.prefix + "/")
        self.assertEqual(anonymous.get("/api/macro-tasks")[0], 401)
