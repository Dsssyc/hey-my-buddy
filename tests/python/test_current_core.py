"""Only the current schema and named RPC surface are accepted; archives stay intact."""
import contextlib
import importlib.util
import io
import os
from pathlib import Path
import sqlite3
import stat
from unittest.mock import patch

from support import BoardTestCase
from buddy import cli, daemon, schemas, transport
from buddy.contracts import CONTRACT_VERSION, BuddyControl
from buddy.db import Corruption, Database, SCHEMA_VERSION
from buddy.errors import BoardError
from buddy.service import CONTROL_OPERATIONS
from buddy.store import BoardStore


class CurrentCoreTests(BoardTestCase):
    def old_database(self, directory, version, *, has_meta=True):
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / "board.sqlite3"
        with contextlib.closing(sqlite3.connect(path)) as connection, connection:
            connection.execute("CREATE TABLE archived_records(id TEXT PRIMARY KEY, value TEXT)")
            connection.execute("INSERT INTO archived_records VALUES('kept', 'unchanged')")
            if has_meta:
                connection.execute("CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT NOT NULL)")
                if version is not None:
                    connection.execute("INSERT INTO meta VALUES('schema_version', ?)", (version,))
        return path

    def test_fresh_schema_has_only_current_fields_and_survives_reopen(self):
        self.assertEqual(CONTRACT_VERSION, "0.27.0")
        self.assertEqual(SCHEMA_VERSION, 15)
        board = self.board()
        with board.store.db.read() as connection:
            self.assertEqual(connection.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0], str(SCHEMA_VERSION))
            columns = {row["name"] for row in connection.execute("PRAGMA table_info(tasks)")}
        self.assertTrue({"input_fingerprint", "fingerprint_version", "selected_attempt_id"}.issubset(columns))
        self.assertFalse(any(name.startswith("legacy") for name in columns))
        request = {"requestId": "same-current-input", "task": "current task", "cwd": str(self.workdir()), "adapter": "external"}
        first = board.call("task_submit", request)
        reopened = BoardStore(self.directory)
        reopened.initialize()
        replay = reopened.task_submit(request)
        self.assertTrue(replay["duplicate"])
        self.assertEqual(first["task"]["taskId"], replay["task"]["taskId"])
        self.assertNotIn("legacy", replay["task"])
        self.assertEqual(replay["task"]["fingerprintVersion"], schemas.FINGERPRINT_VERSION_CURRENT)

    def test_unsupported_schema_is_rejected_without_rewriting_archive(self):
        # The immediately previous schema is included: only ``upgrade`` migrates it,
        # never startup.
        for version in ("5", "6", "7", "8", "9", "10", "11", "12", "999", None, "unrecognized"):
            with self.subTest(version=version):
                directory = self.directory / str(version)
                path = self.old_database(directory, version)
                directory.chmod(0o750)
                before = path.read_bytes()
                before_entries = sorted(item.name for item in directory.iterdir())
                with self.assertRaises(Corruption) as caught:
                    Database(directory).initialize()
                self.assertIn("clean state directory", str(caught.exception))
                self.assertIn(f"only schema {SCHEMA_VERSION}", str(caught.exception))
                self.assertNotIn("migrate", str(caught.exception))
                self.assertEqual(path.read_bytes(), before)
                self.assertEqual(sorted(item.name for item in directory.iterdir()), before_entries)
                self.assertEqual(stat.S_IMODE(directory.stat().st_mode), 0o750)

    def test_unversioned_or_non_sqlite_files_are_not_adopted(self):
        directory = self.directory / "unversioned"
        path = self.old_database(directory, None, has_meta=False)
        before = path.read_bytes()
        with self.assertRaisesRegex(Corruption, "clean state directory"):
            Database(directory).initialize()
        self.assertEqual(path.read_bytes(), before)
        other = self.directory / "non-sqlite"
        other.mkdir()
        file = other / "board.sqlite3"
        file.write_bytes(b"not a supported board format")
        with self.assertRaisesRegex(Corruption, "clean state directory"):
            Database(other).initialize()
        self.assertEqual(file.read_bytes(), b"not a supported board format")

    def test_default_current_state_is_a_new_child_directory(self):
        home = self.directory / "home"
        archive = home / ".local/share/hey-my-buddy"
        old_file = self.old_database(archive, "7")
        before = old_file.read_bytes()
        with patch.dict(os.environ, {}, clear=True), patch.object(Path, "home", return_value=home):
            current = transport.get_state_dir()
            self.assertEqual(current, (archive / "state").resolve())
            Database(current).initialize()
        self.assertEqual(old_file.read_bytes(), before)
        self.assertTrue((current / "board.sqlite3").is_file())

    def test_removed_entrypoints_are_absent_on_each_current_surface(self):
        board = self.board()
        for method, operation in (("legacy-import", "legacy_import"), ("dashboard", "dashboard"), ("migrate", "migrate")):
            with self.subTest(method=method):
                self.assertNotIn(method, cli.METHODS)
                self.assertNotIn(method, cli.LOCAL_METHODS)
                self.assertNotIn(method, transport.METHOD_MAP)
                self.assertNotIn(operation, CONTROL_OPERATIONS)
                self.assertFalse(hasattr(BuddyControl, operation))
                self.assertFalse(hasattr(board.service, operation))
                with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as caught:
                    cli.main([method, "{}"])
                self.assertEqual(caught.exception.code, 2)
        self.assertFalse(hasattr(daemon, "LegacySocketGuard"))
        for module in ("buddy.legacy", "buddy.migrate", "buddy.dashboard"):
            self.assertIsNone(importlib.util.find_spec(module))
        self.assertFalse(hasattr(schemas, "FINGERPRINT_VERSION_LEGACY"))
        self.assertFalse(hasattr(schemas, "legacy_fingerprint"))
        for operation in ("worker_claim", "worker_reconcile", "worker_result", "task_submit", "task_get", "console"):
            self.assertIn(operation, CONTROL_OPERATIONS)
        with self.assertRaises(BoardError) as caught:
            board.call("workflow_get", {"runId": "current-run", "taskId": "silently-ignored-target"})
        self.assertEqual(caught.exception.code, "INVALID_ARGUMENT")

    def test_current_receipt_keeps_result_opaque_and_refuses_flat_old_format(self):
        board = self.board()
        client = board.client()
        task = client.submit(requestId="opaque-current-result", task="return data", cwd=str(self.workdir()), adapter="external")["task"]
        client.register_worker("external", adapter="external")
        claim = client.claim("external", "claim-current", "n" * 32, task_id=task["taskId"])["claim"]
        attempt = claim["attempt"]
        data = {"status": "arbitrary-data", "result": {"turn": {"outcome": {"disposition": "attention"}}}}
        client.submit_result("external", attempt["attemptId"], attempt["generation"], "n" * 32,
                             {"status": "ok", "result": data, "shutdownConfirmed": True})
        result = client.result(runId=task["taskId"])
        self.assertEqual(result["result"], data)
        self.assertEqual(result["resultMeta"]["status"], "ok")
        self.assertNotIn("workflow", result)
        with board.store.db.write() as connection:
            connection.execute("UPDATE attempts SET result_json=? WHERE attempt_id=?", ('{"status":"ok","finalText":"old shape"}', attempt["attemptId"]))
        with self.assertRaises(BoardError) as caught:
            client.result(runId=task["taskId"])
        self.assertEqual(caught.exception.code, "UNSUPPORTED_RESULT_FORMAT")
