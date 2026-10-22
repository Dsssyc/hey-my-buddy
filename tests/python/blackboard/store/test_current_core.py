"""Only the current schema and named RPC surface are accepted; archives stay intact."""
from admission import admit_execution
import ast
import contextlib
import importlib.util
import io
import json
import os
import re
from pathlib import Path
import sqlite3
import stat
from unittest.mock import patch

from support import BoardTestCase
from hey_my_buddy.cli import main as cli
from hey_my_buddy.blackboard.service import daemon
from hey_my_buddy.protocol import schemas, transport
from hey_my_buddy.protocol.contracts import CONTRACT_VERSION, BuddyControl
from hey_my_buddy.blackboard.store.db import Corruption, Database, SCHEMA_VERSION, SECRET_KEY
from hey_my_buddy.errors import BoardError
from hey_my_buddy.blackboard.service.service import CONTROL_OPERATIONS
from hey_my_buddy.blackboard.store.store import BoardStore


class CurrentCoreTests(BoardTestCase):
    def test_fresh_database_creates_private_file_without_changing_directory_mode(self):
        directory = self.directory / "database-mode"
        directory.mkdir(mode=0o750)
        if os.name != 'nt':
            directory.chmod(0o750)
        database = Database(directory)
        database.initialize()
        self.addCleanup(database.close)
        with database.read() as connection:
            meta = dict(connection.execute("SELECT key, value FROM meta"))
        self.assertEqual(meta['schema_version'], str(SCHEMA_VERSION))
        self.assertEqual(bytes.fromhex(meta[SECRET_KEY]), database.secret)
        if os.name != 'nt':
            self.assertEqual(stat.S_IMODE(directory.stat().st_mode), 0o750)
            self.assertEqual(stat.S_IMODE(database.path.stat().st_mode), 0o600)

    def test_existing_database_without_secret_is_corruption(self):
        directory = self.directory / "missing-secret"
        database = Database(directory)
        database.initialize()
        database.close()
        with contextlib.closing(sqlite3.connect(directory / 'board.sqlite3')) as connection, connection:
            connection.execute("DELETE FROM meta WHERE key=?", (SECRET_KEY,))
        reopened = Database(directory)
        self.addCleanup(reopened.close)
        with self.assertRaisesRegex(Corruption, "no capability secret"):
            reopened.initialize()
        with contextlib.closing(sqlite3.connect(directory / 'board.sqlite3')) as connection:
            self.assertIsNone(connection.execute("SELECT value FROM meta WHERE key=?", (SECRET_KEY,)).fetchone())

    def test_event_actor_roles_are_closed_to_production_roles(self):
        board = self.board()
        with board.store.db.write() as connection:
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute("INSERT INTO events(kind,payload_json,actor_role,actor_id,created_at) "
                                   "VALUES('fixture','{}','worker','one','2026-01-01T00:00:00Z')")
            connection.execute("INSERT INTO events(kind,payload_json,actor_role,actor_id,created_at) "
                               "VALUES('fixture','{}','service','service','2026-01-01T00:00:00Z')")

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
        signature = json.loads((Path(__file__).resolve().parents[2] / "protocol" / "contract_signatures.json").read_text(encoding="utf-8"))
        self.assertEqual(CONTRACT_VERSION, signature["contractVersion"])
        self.assertEqual(SCHEMA_VERSION, 28)
        board = self.board()
        with board.store.db.read() as connection:
            self.assertEqual(connection.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0], str(SCHEMA_VERSION))
            columns = {row["name"] for row in connection.execute("PRAGMA table_info(tasks)")}
            attempt_columns = {row["name"] for row in connection.execute("PRAGMA table_info(attempts)")}
        self.assertTrue({"input_fingerprint", "fingerprint_version", "selected_attempt_id"}.issubset(columns))
        self.assertNotIn("result_command_id", attempt_columns)
        self.assertFalse(any(name.startswith("legacy") for name in columns))
        request = {"requestId": "same-current-input", "task": "current task", "cwd": str(self.workdir()), "adapter": "command", "argv": ["/bin/true"]}
        first = board.submit_execution(**request)
        reopened = BoardStore(self.directory)
        reopened.initialize()
        replay = reopened.task_submit(request)
        self.assertTrue(replay["duplicate"])
        self.assertEqual(first["task"]["taskId"], replay["task"]["taskId"])
        self.assertNotIn("legacy", replay["task"])
        self.assertEqual(replay["task"]["fingerprintVersion"], schemas.FINGERPRINT_VERSION_CURRENT)

    def test_meta_writers_are_only_the_two_board_singletons(self):
        source = Path(__file__).resolve().parents[4] / "src" / "hey_my_buddy"
        writes = []
        for path in source.rglob("*.py"):
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"), filename=str(path))):
                if isinstance(node, ast.Constant) and isinstance(node.value, str):
                    if re.search(r"\b(?:INSERT(?:\s+OR\s+\w+)?\s+INTO|UPDATE|DELETE\s+FROM)\s+meta\b",
                                 node.value, re.IGNORECASE):
                        writes.append((path.relative_to(source).as_posix(), node.value))
        self.assertEqual(sorted(writes), sorted([
            ("blackboard/store/db.py", "INSERT INTO meta(key, value) VALUES('schema_version', ?)"),
            ("blackboard/store/db.py", "INSERT INTO meta(key, value) VALUES(?, ?)"),
            ("blackboard/store/upgrade.py", "UPDATE meta SET value=? WHERE key='schema_version'"),
        ]))
        board = self.board()
        with board.store.db.read() as connection:
            self.assertEqual({row[0] for row in connection.execute("SELECT key FROM meta")},
                             {"schema_version", "capability_secret"})
        self.assertFalse(hasattr(Database, "meta"))
        self.assertFalse(hasattr(Database, "set_meta"))

    def test_unsupported_schema_is_rejected_without_rewriting_archive(self):
        # Schema 18 is now as unsupported as every older board form.
        for version in ("5", "6", "7", "8", "9", "10", "11", "12", "13", "14", "15", "16", "17", "18", "24", "25", "26", "999", None, "unrecognized"):
            with self.subTest(version=version):
                directory = self.directory / str(version)
                path = self.old_database(directory, version)
                directory.chmod(0o750)
                before = path.read_bytes()
                before_entries = sorted(item.name for item in directory.iterdir())
                with self.assertRaises(Corruption) as caught:
                    Database(directory).initialize()
                self.assertIn("Retain other unsupported state as an archive", str(caught.exception))
                self.assertIn(f"only schema {SCHEMA_VERSION}", str(caught.exception))
                self.assertNotIn("schema 18 requires an explicit idle upgrade", str(caught.exception))
                self.assertNotIn("migrate", str(caught.exception))
                self.assertEqual(path.read_bytes(), before)
                self.assertEqual(sorted(item.name for item in directory.iterdir()), before_entries)
                self.assertEqual(stat.S_IMODE(directory.stat().st_mode), 0o750)

    def test_unversioned_or_non_sqlite_files_are_not_adopted(self):
        directory = self.directory / "unversioned"
        path = self.old_database(directory, None, has_meta=False)
        before = path.read_bytes()
        with self.assertRaisesRegex(Corruption, "Retain other unsupported state as an archive"):
            Database(directory).initialize()
        self.assertEqual(path.read_bytes(), before)
        other = self.directory / "non-sqlite"
        other.mkdir()
        file = other / "board.sqlite3"
        file.write_bytes(b"not a supported board format")
        with self.assertRaisesRegex(Corruption, "Retain other unsupported state as an archive"):
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
                self.assertNotIn(method, transport.METHOD_MAP)
                self.assertNotIn(method, transport.METHOD_MAP)
                self.assertNotIn(operation, CONTROL_OPERATIONS)
                self.assertFalse(hasattr(BuddyControl, operation))
                self.assertFalse(hasattr(board.service, operation))
                with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as caught:
                    cli.main([method, "{}"])
                self.assertEqual(caught.exception.code, 2)
        self.assertFalse(hasattr(daemon, "LegacySocketGuard"))
        for module in ("hey_my_buddy.legacy", "hey_my_buddy.migrate", "hey_my_buddy.dashboard"):
            self.assertIsNone(importlib.util.find_spec(module))
        self.assertFalse(hasattr(schemas, "FINGERPRINT_VERSION_LEGACY"))
        self.assertFalse(hasattr(schemas, "legacy_fingerprint"))
        for operation in ("execution_progress", 'execution_result', "task_get", "console"):
            self.assertIn(operation, CONTROL_OPERATIONS)
        self.assertNotIn("task_submit", CONTROL_OPERATIONS)
        self.assertFalse(hasattr(BuddyControl, "task_submit"))
        self.assertFalse(hasattr(board.service, "task_submit"))
        self.assertNotIn("execution-submit", cli.METHODS)
        self.assertNotIn(("control", "task_submit"), transport.METHOD_MAP.values())
        with self.assertRaises(BoardError) as caught:
            board.call("workflow_get", {"runId": "current-run", "taskId": "silently-ignored-target"})
        self.assertEqual(caught.exception.code, "INVALID_ARGUMENT")

    def test_result_settles_only_its_unanswered_questions_and_replay_adds_no_events(self):
        for status, confirmed in (("ok", True), ("failed", True), ("cancelled", True)):
            with self.subTest(status=status, shutdownConfirmed=confirmed):
                board = self.board()
                client = board.client()
                task = board.submit_execution(requestId=f"inquiry-end-{status}-{confirmed}", task="return data",
                    cwd=str(self.workdir(f"{status}-{confirmed}")), adapter="command", argv=["true"])["task"]
                claim = admit_execution(client, "n" * 32, task_id=task["taskId"])["claim"]
                attempt = claim["attempt"]
                other = board.submit_execution(requestId=f"other-{status}-{confirmed}", task="still running",
                    cwd=str(self.workdir(f"other-{status}-{confirmed}")), adapter="command", argv=["true"])["task"]
                other_attempt = admit_execution(client, "o" * 32, task_id=other["taskId"])["claim"]["attempt"]
                board.store._message_post(other["taskId"], "q-other", "Still there?")
                for inquiry_id in ("q-pending", "q-delivered", "q-answered"):
                    board.store._message_post(task["taskId"], inquiry_id, "Status?")
                board.store._message_update(task["taskId"], "q-delivered", actor="bridge", state="delivered")
                board.store._message_update(task["taskId"], "q-answered", actor="bridge", answer={
                    "text": "Still working", "bytes": 13, "via": None, "toolCallId": None,
                    "at": None, "truncated": False, "source": "bridge-journal",
                })
                report = {"status": status, "shutdownConfirmed": confirmed}
                client.submit_result(attempt["attemptId"], attempt["generation"], "n" * 32, report)
                pending = client.get_message("q-pending", runId=task["taskId"])
                self.assertEqual(pending["state"], "unavailable")
                self.assertEqual(pending["reason"], {"code": "inquiry-queued-at-attempt-end", "params": {}})
                delivered = client.get_message("q-delivered", runId=task["taskId"])
                self.assertEqual(delivered["state"], "unavailable")
                self.assertEqual(delivered["reason"], {"code": "inquiry-delivered-at-attempt-end", "params": {}})
                self.assertEqual(client.get_message("q-answered", runId=task["taskId"])["answer"]["text"], "Still working")
                events = client.read_events(taskId=task["taskId"])
                unavailable = [e for e in events["events"] if e["kind"] == "message.updated"
                               and e["payload"].get("state") == "unavailable"]
                self.assertEqual(len(unavailable), 2)
                self.assertEqual(unavailable[0]["attemptId"], attempt["attemptId"])
                head = board.store.head()
                client.submit_result(attempt["attemptId"], attempt["generation"], "n" * 32, report)
                self.assertEqual(board.store.head(), head)
                self.assertEqual(client.get_message("q-other", runId=other["taskId"])["state"], "queued")
                client.submit_result(other_attempt["attemptId"], other_attempt["generation"], "o" * 32,
                                     {"status": "ok", "shutdownConfirmed": True})

    def test_current_receipt_keeps_result_opaque(self):
        board = self.board()
        client = board.client()
        task = board.submit_execution(requestId="opaque-current-result", task="return data", cwd=str(self.workdir()), adapter="command", argv=["/bin/true"])["task"]
        pass  # Admission is owned by the service.
        claim = admit_execution(client, "n" * 32, task_id=task["taskId"])["claim"]
        attempt = claim["attempt"]
        data = {"status": "arbitrary-data", "result": {"turn": {"outcome": {"disposition": "attention"}}}}
        client.submit_result(attempt["attemptId"], attempt["generation"], "n" * 32, {"status": "ok", "result": data, "shutdownConfirmed": True})
        result = client.result(runId=task["taskId"])
        self.assertEqual(result["result"], data)
        self.assertEqual(result["resultMeta"]["status"], "ok")
        self.assertNotIn("workflow", result)
