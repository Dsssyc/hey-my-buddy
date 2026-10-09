"""Settings-only runtime facts over the real authenticated private HTTP surface."""
from __future__ import annotations

import json
import io
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from console.test_console import Browser
from support import BoardTestCase
from hey_my_buddy.blackboard.store import backup
from hey_my_buddy.blackboard.store.db import SCHEMA_VERSION
from hey_my_buddy.console import version_info
from hey_my_buddy.console.console_sessions import ConsoleSessions
from hey_my_buddy.install import launcher, runtime
from hey_my_buddy.protocol.contracts import CONTRACT_VERSION


FIELDS = {"softwareVersion", "contractVersion", "schemaVersion", "sourceCommit", "installedAt"}


class VersionInfoTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="buddy-version-")
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.source = self.base / "source"
        self.installed = self.base / "runtime" / ("a" * 32)
        self.state = self.base / "state"
        self.state.mkdir()
        self.write_project(self.source, "7.2.1", "source-recorded-commit")
        self.write_project(self.installed, "6.8.2", "installed-build-commit")
        self.enterContext(mock.patch.object(runtime, "project_root", return_value=self.source))
        self.enterContext(mock.patch.object(runtime, "process_identity", side_effect=self.actual))
        self.enterContext(mock.patch.dict(os.environ, {"BUDDY_RUNTIME_ROOT": str(self.base / "runtime")}))

    def write_project(self, root, version, commit):
        package = root / "src/hey_my_buddy"
        (package / "protocol").mkdir(parents=True)
        (package / "blackboard/store").mkdir(parents=True)
        (root / "packaging").mkdir()
        (root / "pyproject.toml").write_text(f'[project]\nname = "fixture"\nversion = "{version}"\n')
        (package / "protocol/contracts.py").write_text('CONTRACT_VERSION = "6.8.0"\n')
        (package / "blackboard/store/db.py").write_text('SCHEMA_VERSION = 14\n')
        (package / "build-info.json").write_text(json.dumps({"sourceCommit": commit}))
        (root / "packaging/runtime-assets.json").write_text(json.dumps({
            "format": 1, "assets": [{"path": "packaging/runtime-assets.json", "kind": "file"},
                                     {"path": "pyproject.toml", "kind": "file"},
                                     {"path": "src/hey_my_buddy", "kind": "directory"}],
            "resources": {"fixture": "src/hey_my_buddy/build-info.json"},
        }))

    def actual(self):
        root = self.actual_root if hasattr(self, "actual_root") else self.source
        return {"package": str(root / "src/hey_my_buddy"), "prefix": str(root / "venv"),
                "executable": str(root / "venv/bin/python"),
                "resources": {"fixture": str(root / "src/hey_my_buddy/build-info.json")},
                "missingResources": []}

    def ready(self, *, in_use=False, recorded=True, pin=True):
        python = self.installed / "venv/bin/python"
        python.parent.mkdir(parents=True)
        python.write_text("# fixture only; never executed\n")
        marker = {"format": 1, "state": "READY", "contentId": self.installed.name,
                  "python": str(python), "resources": {"fixture": str(self.installed / "src/hey_my_buddy/build-info.json")},
                  "privateToken": "do-not-expose", "files": ["do-not-expose"]}
        if recorded:
            marker.update(sourceCommit="installed-ready-commit", installedAt="2026-09-30T01:02:03Z")
        (self.installed / "READY.json").write_text(json.dumps(marker))
        if pin:
            self.enterContext(mock.patch.dict(os.environ, {"BUDDY_RUNTIME": str(self.installed)}))
        if in_use:
            self.actual_root = self.installed
            runtime.project_root.return_value = self.installed

    def test_source_versions_use_service_source_and_recorded_build_only(self):
        with mock.patch.object(runtime.subprocess, "check_output") as git:
            info = version_info.version_info(self.state)
        git.assert_not_called()
        self.assertEqual(info, {"running": {"mode": "source", "softwareVersion": "7.2.1",
                         "contractVersion": CONTRACT_VERSION, "schemaVersion": SCHEMA_VERSION,
                         "sourceCommit": "source-recorded-commit", "installedAt": None}, "installed": None})

    def test_ready_install_is_distinct_from_current_source_service(self):
        self.ready()
        info = version_info.version_info(self.state)
        self.assertEqual(info["running"]["mode"], "source")
        self.assertEqual(info["running"]["softwareVersion"], "7.2.1")
        self.assertEqual(info["running"]["sourceCommit"], "source-recorded-commit")
        self.assertIsNone(info["running"]["installedAt"])
        self.assertEqual(info["installed"], {"softwareVersion": "6.8.2", "contractVersion": "6.8.0",
                         "schemaVersion": 14, "sourceCommit": "installed-ready-commit",
                         "installedAt": "2026-09-30T01:02:03Z"})
        self.assertEqual(set(info["running"]), FIELDS | {"mode"})
        self.assertEqual(set(info["installed"]), FIELDS)
        self.assertNotIn("do-not-expose", json.dumps(info))

    def test_source_without_build_metadata_does_not_infer_a_commit(self):
        absent = self.base / "unrecorded-source"
        (absent / "src/hey_my_buddy").mkdir(parents=True)
        (absent / "packaging").mkdir()
        (absent / "pyproject.toml").write_bytes((self.source / "pyproject.toml").read_bytes())
        (absent / "packaging/runtime-assets.json").write_bytes((self.source / "packaging/runtime-assets.json").read_bytes())
        runtime.project_root.return_value = absent
        with mock.patch.object(runtime.subprocess, "check_output") as git:
            info = version_info.version_info(self.state)
        git.assert_not_called()
        self.assertEqual(info["running"]["mode"], "source")
        self.assertIsNone(info["running"]["sourceCommit"])
        self.assertIsNone(info["running"]["installedAt"])
        self.assertIsNone(info["installed"])

    def test_in_use_ready_runtime_uses_installation_identity(self):
        self.ready(in_use=True)
        info = version_info.version_info(self.state)
        self.assertEqual(info["running"]["mode"], "runtime")
        self.assertEqual(info["running"]["softwareVersion"], "6.8.2")
        self.assertEqual(info["running"]["sourceCommit"], "installed-ready-commit")
        self.assertEqual(info["running"]["installedAt"], "2026-09-30T01:02:03Z")

    def test_missing_or_damaged_metadata_stays_unknown_without_git(self):
        (self.source / "src/hey_my_buddy/build-info.json").write_text("broken json")
        (self.source / "pyproject.toml").write_text("broken toml")
        self.ready(recorded=False)
        (self.installed / "src/hey_my_buddy/protocol/contracts.py").write_text("# not recorded\n")
        (self.installed / "src/hey_my_buddy/blackboard/store/db.py").write_text("# not recorded\n")
        with mock.patch.object(runtime.subprocess, "check_output") as git:
            info = version_info.version_info(self.state)
        git.assert_not_called()
        self.assertIsNone(info["running"]["softwareVersion"])
        self.assertIsNone(info["running"]["sourceCommit"])
        for field in ("contractVersion", "schemaVersion", "sourceCommit", "installedAt"):
            self.assertIsNone(info["installed"][field])

    def test_source_uses_validated_active_old_runtime_without_a_pin(self):
        self.ready(pin=False)
        launcher.write_active_runtime(self.state, self.installed)
        self.assertNotIn("BUDDY_RUNTIME", os.environ)
        self.assertNotEqual(runtime.content_id(), self.installed.name)
        self.assertEqual(runtime.resolve_runtime()["state"], "SOURCE")
        with mock.patch.object(backup, "preflight", side_effect=AssertionError("unexpected preflight")), \
             mock.patch.object(runtime.subprocess, "check_output") as git:
            info = version_info.version_info(self.state)
        git.assert_not_called()
        self.assertEqual(info["running"], {"mode": "source", "softwareVersion": "7.2.1",
                         "contractVersion": CONTRACT_VERSION, "schemaVersion": SCHEMA_VERSION,
                         "sourceCommit": "source-recorded-commit", "installedAt": None})
        self.assertEqual(info["installed"], {"softwareVersion": "6.8.2", "contractVersion": "6.8.0",
                         "schemaVersion": 14, "sourceCommit": "installed-ready-commit",
                         "installedAt": "2026-09-30T01:02:03Z"})
        self.assertNotIn("do-not-expose", json.dumps(info))

    def test_source_does_not_guess_from_old_ready_directories_without_a_pointer(self):
        self.ready(pin=False)
        first = self.installed
        self.installed = first.parent / ("b" * 32)
        self.write_project(self.installed, "6.8.3", "other-installed-build")
        self.ready(pin=False)
        self.assertTrue(runtime.is_ready(first))
        self.assertTrue(runtime.is_ready(self.installed))
        self.assertNotIn("BUDDY_RUNTIME", os.environ)
        info = version_info.version_info(self.state)
        self.assertEqual(info["running"]["mode"], "source")
        self.assertEqual(info["running"]["softwareVersion"], "7.2.1")
        self.assertIsNone(info["installed"])

    def test_source_keeps_running_facts_when_active_metadata_is_damaged(self):
        self.ready(pin=False)
        launcher.write_active_runtime(self.state, self.installed)
        pointer = self.state / "active-runtime.json"
        for damaged in ("broken json", "[]", json.dumps({"format": 1, "contentId": "wrong", "runtimeDir": str(self.installed)}),
                        json.dumps({"format": 1, "contentId": self.source.name, "runtimeDir": str(self.source)}),
                        json.dumps({"format": 1, "contentId": "c" * 32, "runtimeDir": str(self.installed.parent / ("c" * 32))})):
            with self.subTest(pointer=damaged):
                pointer.write_text(damaged)
                try:
                    info = version_info.version_info(self.state)
                except Exception as error:
                    self.fail(f"Damaged installation metadata hid running facts: {type(error).__name__}")
                self.assertEqual(info["running"]["softwareVersion"], "7.2.1")
                self.assertEqual(info["running"]["sourceCommit"], "source-recorded-commit")
                self.assertIsNone(info["installed"])

    def test_source_keeps_running_facts_when_active_selection_is_unreadable(self):
        with mock.patch.object(launcher, "selected_runtime", side_effect=OSError("private-state-unreadable")):
            try:
                info = version_info.version_info(self.state)
            except Exception as error:
                self.fail(f"Unreadable installation metadata hid running facts: {type(error).__name__}")
        self.assertEqual(info["running"]["mode"], "source")
        self.assertEqual(info["running"]["softwareVersion"], "7.2.1")
        self.assertIsNone(info["installed"])


class _VersionConsoleTestCase(BoardTestCase):
    """Only the private board/login fixture; no other console test cases are inherited."""

    def setUp(self):
        super().setUp()
        self.catalog_fixture()

    def board(self, **options):
        from hey_my_buddy.install.launcher import write_private

        board = super().board(**options)
        settings = {"port": 0, "requireLogin": True, "revision": 0}
        write_private(board.directory / "console-settings.json", settings)
        board.console._settings = settings
        board.console.require_login = True
        return board

    def open_console(self, board):
        stated = board.call("console", {"action": "open"})
        self.assertTrue(stated["url"].startswith("http://127.0.0.1:"))
        self.assertFalse(stated["readOnly"])
        return stated, Browser(stated["url"])


class VersionInfoHTTPTests(_VersionConsoleTestCase):
    def test_version_route_is_login_protected_and_whitelisted(self):
        board = self.board()
        stated, browser = self.open_console(board)
        stranger = Browser(stated["url"], redeem=False)
        expected = {"running": {"mode": "source", "softwareVersion": "0.29.0",
                    "contractVersion": CONTRACT_VERSION, "schemaVersion": SCHEMA_VERSION,
                    "sourceCommit": None, "installedAt": None}, "installed": None}
        with mock.patch.object(version_info, "version_info", return_value=expected) as read:
            status, _headers, _body = stranger.call("GET", "/api/runtime-version")
            self.assertEqual(status, 401)
            read.assert_not_called()
            status, headers, body = browser.get("/api/runtime-version")
            self.assertEqual(status, 200)
            self.assertEqual(json.loads(body), expected)
            self.assertEqual(headers["cache-control"], "no-store")
            self.assertEqual(read.call_count, 1)
            self.assertNotIn(board.service.token, body.decode())
            status, _headers, _body = browser.get("/api/runtime-version", headers={"Origin": "https://foreign.invalid"})
            self.assertEqual(status, 403)
            self.assertEqual(read.call_count, 1)

    def test_runtime_read_failure_is_explicit_and_does_not_expose_private_details(self):
        board = self.board()
        _stated, browser = self.open_console(board)
        with mock.patch.object(version_info, "version_info", side_effect=OSError("private-token-and-path")):
            status, _headers, body = browser.get("/api/runtime-version")
        self.assertEqual(status, 500)
        self.assertEqual(json.loads(body)["error"]["code"], "INTERNAL_ERROR")
        self.assertNotIn("private-token-and-path", body.decode())

    def test_periodic_snapshot_never_reads_runtime_or_preflight(self):
        board = self.board()
        _stated, browser = self.open_console(board)
        with mock.patch.object(runtime, "resolve_runtime", wraps=runtime.resolve_runtime) as resolve, \
             mock.patch.object(version_info, "version_info", wraps=version_info.version_info) as read, \
             mock.patch.object(backup, "preflight", wraps=backup.preflight) as preflight:
            for _ in range(3):
                status, _headers, body = browser.get("/api/console")
                self.assertEqual(status, 200)
                self.assertNotIn("runtimeVersion", json.loads(body))
            self.assertEqual(read.call_count, 0)
            self.assertEqual(resolve.call_count, 0)
            self.assertEqual(preflight.call_count, 0)
            status, _headers, body = browser.get("/api/runtime-version")
            self.assertEqual(status, 200, body[:200])
            self.assertEqual(read.call_count, 1)
            self.assertEqual(resolve.call_count, 1)
            self.assertEqual(preflight.call_count, 0)


class VersionInfoHandlerTests(_VersionConsoleTestCase):
    """The same request handler without a listening socket; HTTP above tests transport."""

    def setUp(self):
        super().setUp()
        self.private_board = self.board()
        self.console = self.private_board.console
        self.console.origin = "http://127.0.0.1:12345"
        self.console.port = 12345
        self.enterContext(mock.patch.object(self.console, "_server", SimpleNamespace(server_address=("127.0.0.1", 12345))))
        self.console._sessions = ConsoleSessions(path=self.directory / "handler-sessions.json")
        self.session = self.console._sessions.redeem(self.console._sessions.issue())

    def request(self, path, *, logged_in=True, origin=None):
        class Connection:
            def __init__(self, request):
                self.input = io.BytesIO(request)
                self.output = bytearray()

            def settimeout(self, timeout):
                pass

            def makefile(self, *args):
                return self.input

            def sendall(self, data):
                self.output.extend(data)

        cookie = f"Cookie: buddy_console_session={self.session.cookie}\r\n" if logged_in else ""
        request_origin = self.console.origin if origin is None else origin
        request = f"GET {path} HTTP/1.1\r\nHost: 127.0.0.1:12345\r\nOrigin: {request_origin}\r\n{cookie}Connection: close\r\n\r\n"
        connection = Connection(request.encode())
        self.console._handler()(connection, ("127.0.0.1", 1), None)
        headers, body = bytes(connection.output).split(b"\r\n\r\n", 1)
        return int(headers.split()[1]), body

    def test_version_handler_uses_serving_board_state_for_the_active_installation(self):
        self.installed = self.directory / "runtime" / ("a" * 32)
        VersionInfoTests.write_project(self, self.installed, "6.8.2", "installed-build-commit")
        VersionInfoTests.ready(self, pin=False)
        launcher.write_active_runtime(self.console.store.directory, self.installed)
        # The process environment may name a different state than this Console.
        with mock.patch.dict(os.environ, {"BUDDY_STATE_DIR": str(self.directory / "unrelated-state"),
                                          "BUDDY_RUNTIME_ROOT": str(self.installed.parent)}), \
             mock.patch.object(backup, "preflight", side_effect=AssertionError("unexpected preflight")):
            status, body = self.request("/api/runtime-version")
        self.assertEqual(status, 200)
        info = json.loads(body)
        self.assertEqual(info["running"]["mode"], "source")
        self.assertEqual(info["installed"], {"softwareVersion": "6.8.2", "contractVersion": "6.8.0",
                         "schemaVersion": 14, "sourceCommit": "installed-ready-commit",
                         "installedAt": "2026-09-30T01:02:03Z"})

    def test_version_handler_checks_session_before_read(self):
        with mock.patch.object(version_info, "version_info", return_value={"running": {}, "installed": None}) as read:
            self.assertEqual(self.request("/api/runtime-version", logged_in=False)[0], 401)
            self.assertEqual(read.call_count, 0)
            status, body = self.request("/api/runtime-version")
            self.assertEqual(status, 200)
            self.assertEqual(json.loads(body), {"running": {}, "installed": None})
            self.assertEqual(read.call_count, 1)

    def test_version_handler_reports_read_failure(self):
        with mock.patch.object(version_info, "version_info", side_effect=OSError("private-path")):
            status, body = self.request("/api/runtime-version")
        self.assertEqual(status, 500)
        self.assertEqual(json.loads(body)["error"]["code"], "INTERNAL_ERROR")
        self.assertNotIn("private-path", body.decode())

    def test_version_handler_rejects_foreign_origin_before_read(self):
        with mock.patch.object(version_info, "version_info", return_value={"running": {}, "installed": None}) as read:
            status, _body = self.request("/api/runtime-version", origin="https://foreign.invalid")
            self.assertEqual(status, 403)
            self.assertEqual(read.call_count, 0)

    def test_periodic_handler_does_not_scan_versions_or_backup(self):
        with mock.patch.object(runtime, "resolve_runtime", wraps=runtime.resolve_runtime) as resolve, \
             mock.patch.object(version_info, "version_info", wraps=version_info.version_info) as read, \
             mock.patch.object(backup, "preflight", wraps=backup.preflight) as preflight:
            for _ in range(3):
                self.assertEqual(self.request("/api/console")[0], 200)
            self.assertEqual(read.call_count, 0)
            self.assertEqual(resolve.call_count, 0)
            self.assertEqual(preflight.call_count, 0)
            status, body = self.request("/api/runtime-version")
            self.assertEqual(status, 200, body[:200])
            self.assertEqual(read.call_count, 1)
            self.assertEqual(resolve.call_count, 1)
            self.assertEqual(preflight.call_count, 0)


if __name__ == "__main__":
    unittest.main()
