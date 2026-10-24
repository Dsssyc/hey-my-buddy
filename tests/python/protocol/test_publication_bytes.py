"""Byte contracts captured from writers at d5c908aaaed3cfd66c20822222570a1e267ff9f2.

The expected UTF-8 literals reflect that commit's writer output on the inputs
below. They intentionally do not call any current serializer or require Git at
test time. A value that becomes a path is interpolated only after serialization.
"""
from __future__ import annotations

from pathlib import Path
from contextlib import closing
from json.encoder import encode_basestring_ascii
import sqlite3
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

from hey_my_buddy.blackboard.store import backup
from hey_my_buddy.blackboard.tasks import storage
from hey_my_buddy.cli import main as cli
from hey_my_buddy.console.console_sessions import BrowserSession, ConsoleSessions
from hey_my_buddy.home import submission_file_path
from hey_my_buddy.install import runtime, skill_install, upgrade
from hey_my_buddy.protocol.process_identity import NativeProcessJournal


VALUE = {"z": "café", "a": [1, True]}
ORDINARY = b'{"z": "caf\xc3\xa9", "a": [1, true]}'
CANONICAL_JOURNAL = b'{"binding":{"a":[1,true],"z":"caf\xc3\xa9"},"identities":[],"pending":[]}'


def _path_in_json(path: Path) -> bytes:
    """Insert a temporary native path into an old byte fixture on either OS."""
    return encode_basestring_ascii(str(path))[1:-1].encode("ascii")


class PublicationByteTests(unittest.TestCase):
    def test_upgrade_and_storage_writers_keep_old_utf8_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            upgrade_path, storage_path = root / "upgrade.json", root / "storage.json"
            upgrade.write_journal(upgrade_path, VALUE)
            storage._atomic_storage_json(storage_path, VALUE)
            self.assertEqual(upgrade_path.read_bytes(), ORDINARY)
            self.assertEqual(storage_path.read_bytes(), ORDINARY)

    def test_console_session_writer_keeps_old_ascii_escaped_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sessions.json"
            sessions = ConsoleSessions(path=path)
            sessions.sessions["café"] = BrowserSession("café", "", "", 1.5, "a" * 64)
            sessions._save()
            expected = (b'{"version": 1, "sessions": [{"id": "caf\\u00e9", '
                        b'"tokenHash": "' + b'a' * 64 + b'", "lastSeen": 1.5}]}')
            self.assertEqual(path.read_bytes(), expected)

    def test_cli_submission_token_keeps_old_sorted_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            with mock.patch.object(cli.secrets, "token_hex", return_value="f" * 64):
                self.assertEqual(cli._submission_token("req-café", {}, state), "f" * 64)
            expected = (b'{"requestId": "req-caf\\u00e9", "submissionToken": "'
                        + b'f' * 64 + b'"}')
            self.assertEqual(submission_file_path(state, "req-café").read_bytes(), expected)

    def test_cli_control_and_runtime_hint_keep_old_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            with mock.patch.object(cli.time, "strftime", return_value="2026-01-02T03:04:05Z"):
                path = cli._save_control("run-byte", {"hostId": "hôst", "ownerGeneration": 7,
                                                     "controlToken": "f" * 64}, state)
            expected = (b'{"hostId": "h\xc3\xb4st", "ownerGeneration": 7, "controlToken": "'
                        + b'f' * 64 + b'", "runId": "run-byte", "savedAt": "2026-01-02T03:04:05Z"}')
            self.assertEqual(Path(path).read_bytes(), expected)
            with mock.patch.object(runtime, "runtime_python", return_value=Path("python-café")):
                skill_install.write_runtime_hint(state, state / "runtime")
            self.assertEqual((state / ".runtime-python").read_bytes(), b'python-caf\xc3\xa9\n')

    def test_native_process_journal_keeps_old_canonical_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "native-process-journal.json"
            journal = NativeProcessJournal(path, VALUE)
            self.assertEqual(path.read_bytes(), CANONICAL_JOURNAL)
            journal.state["pending"].append("0" * 32)
            journal._publish()
            expected = (b'{"binding":{"a":[1,true],"z":"caf\xc3\xa9"},'
                        b'"identities":[],"pending":["' + b'0' * 32 + b'"]}')
            self.assertEqual(path.read_bytes(), expected)

    def test_backup_publication_journal_keeps_old_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            backup._publication_journal(root, had_current=True)
            self.assertEqual((root / ".publish.json").read_bytes(),
                             b'{"format": 1, "hadCurrent": true}')

    def test_backup_create_writes_old_sorted_manifest_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            with closing(sqlite3.connect(state / "board.sqlite3")) as connection, connection:
                connection.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT)")
                connection.execute("INSERT INTO meta VALUES ('schema_version', '28')")
            with mock.patch.object(backup, "database_snapshot", return_value={"z": "café"}), \
                 mock.patch.object(backup, "utc_now", return_value="2026-01-02T03:04:05Z"), \
                 mock.patch.object(backup, "digest", return_value="a" * 64), \
                 mock.patch.object(backup, "verify", return_value={}), \
                 mock.patch.object(backup, "_verify", return_value={}):
                result = backup.create(state, runtime_identity=None, plugin_commit=None,
                                       contract_version="0.50.0")
            current = Path(result["path"])
            payload_size = (current / "board.sqlite3.gz").stat().st_size
            expected = (
                b'{"backupToolContract": "0.53.0", "contract": "0.50.0", '
                b'"createdAt": "2026-01-02T03:04:05Z", "databaseSnapshot": {"z": "caf\xc3\xa9"}, '
                b'"files": {"board.sqlite3.gz": {"bytes": ' + str(payload_size).encode("ascii")
                + b', "sha256": "' + b'a' * 64 + b'"}}, "format": 1, "pluginCommit": null, '
                b'"pluginCommitStatus": "unavailable-in-runtime-metadata", "runtime": null, "schema": 28}')
            self.assertEqual((current / "manifest.json").read_bytes(), expected)

    def test_runtime_materialization_writes_old_pretty_manifest_and_ready_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "runtimes" / "fixture-runtime"
            target.parent.mkdir()
            record = {"z": "café", "a": [1, True], "manifestSha256": "digest"}

            def installed(*args, **kwargs):
                interpreter = runtime.runtime_python(target)
                interpreter.parent.mkdir(parents=True)
                interpreter.write_bytes(b"python")
                return SimpleNamespace(returncode=0)

            with mock.patch.object(runtime, "_copy_assets"), \
                 mock.patch.object(runtime, "missing_resources", return_value=[]), \
                 mock.patch.object(runtime, "manifest", return_value=record), \
                 mock.patch.object(runtime.subprocess, "run", side_effect=installed), \
                 mock.patch.object(runtime, "_now", return_value="2026-01-02T03:04:05Z"), \
                 mock.patch("hey_my_buddy.install.skill_package.source_commit", return_value="a" * 40), \
                 mock.patch.object(runtime, "resource_paths", return_value={"fixture.runner": target / "runner.py"}), \
                 mock.patch.object(runtime, "read_ready", return_value={}):
                runtime._materialize_locked(root, target, "uv", None)
            self.assertEqual((target / "manifest.json").read_bytes(),
                             b'{\n  "a": [\n    1,\n    true\n  ],\n  "manifestSha256": "digest",\n'
                             b'  "z": "caf\\u00e9"\n}\n')
            expected_ready = (
                b'{\n  "contentId": "fixture-runtime",\n  "environment": "' + _path_in_json(target / "venv")
                + b'",\n  "format": 1,\n  "installedAt": "2026-01-02T03:04:05Z",\n'
                b'  "manifest": "packaging/runtime-assets.json",\n  "manifestSha256": "digest",\n'
                b'  "projectRoot": "' + _path_in_json(target) + b'",\n  "python": "'
                + _path_in_json(runtime.runtime_python(target)) + b'",\n  "resources": {\n'
                b'    "fixture.runner": "' + _path_in_json(target / "runner.py")
                + b'"\n  },\n  "runtimeRoot": "' + _path_in_json(target.parent)
                + b'",\n  "source": "' + _path_in_json(target)
                + b'",\n  "sourceCommit": "' + b'a' * 40 + b'",\n  "state": "READY"\n}\n')
            self.assertEqual((target / "READY.json").read_bytes(), expected_ready)


if __name__ == "__main__":
    unittest.main()
