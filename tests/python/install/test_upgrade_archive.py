"""Runtime cutovers preserve historical workspace files."""
from contextlib import ExitStack
import json
import os
from pathlib import Path
import shutil
import sqlite3
from unittest import mock

from hey_my_buddy.protocol.contracts import CONTRACT_VERSION
from support import BoardTestCase
from blackboard.store.upgrade_fixture import load_fixture
from hey_my_buddy.blackboard.store import backup, upgrade as schema_upgrade
from hey_my_buddy.blackboard.store.db import SCHEMA_VERSION
from hey_my_buddy.blackboard.store.store import BoardStore
from hey_my_buddy.errors import BoardError
from hey_my_buddy.install import upgrade


class UpgradeCutoverTests(BoardTestCase):
    def setUp(self):
        super().setUp()
        self.state = self.directory / "state"
        load_fixture(self.state)
        schema_upgrade.upgrade(self.state)
        (self.state / "console-settings.json").write_text('{"theme":"dark"}')
        store = BoardStore(self.state)
        store.db.close()
        old_backup = Path(backup.create(self.state, runtime_identity=None, plugin_commit=None, contract_version=CONTRACT_VERSION)["path"])
        self.retained_archive = self.state / "archives" / "previous"
        shutil.copytree(old_backup, self.retained_archive)
        self.archive_bytes = {path.relative_to(self.retained_archive): path.read_bytes()
                              for path in self.retained_archive.rglob("*") if path.is_file()}
        self.assertEqual(backup.verify(self.retained_archive)["schema"], SCHEMA_VERSION)
        self.evidence = self.state / "attempts" / "fixture-run" / "fixture-attempt" / "result.json"
        self.evidence.parent.mkdir(parents=True, exist_ok=True)
        self.evidence.write_text('{"retained":"evidence"}')
        self.expected_evidence = self.evidence.read_text()
        self.start_log = self.state / "upgrade-start.log"
        self.start_log.write_text("retained startup log\n")
        self.expected_start_log = self.start_log.read_text()
        self.original_board = (self.state / "board.sqlite3").read_bytes()
        self.before = upgrade.idle_snapshot(self.state)
        self.history = {path.relative_to(self.state): path.read_bytes()
                        for path in (self.state / "workspaces").rglob("*") if path.is_file()}
        self.assertTrue(self.history)
        self.original_history = dict(self.history)
        self.runtime_root = self.directory / "runtimes"
        self.previous = self.runtime_root / ("a" * 32)
        self.previous.mkdir(parents=True)
        self.started_runtimes = []

    def cutover(self, previous: Path, target: Path, *, fail_target=False,
                fail_backup=False, interrupt_target=False):
        target.mkdir(parents=True, exist_ok=True)
        endpoint = {"runtimeIdentity": "runtime:" + previous.name,
                    "serviceId": "private-old", "pid": 123, "contractVersion": "fixture-contract"}
        (self.state / "control.json").write_text(json.dumps(endpoint))
        health = {**endpoint, "runtimeContentId": previous.name, "runtimeStable": True,
                  "maxConcurrent": 2, "waitCapacity": 4}

        def start(_state, path):
            self.started_runtimes.append(path)
            if (fail_target or interrupt_target) and path == target:
                # Model writes by a failed replacement after the archive was
                # sealed: rollback replays board/settings, not these records.
                with sqlite3.connect(self.state / "board.sqlite3") as connection:
                    connection.execute("INSERT INTO meta(key,value) VALUES('injected-after-backup','changed')")
                (self.state / "console-settings.json").write_text('{"theme":"light"}')
                self.expected_evidence = '{"retained":"later evidence"}'
                self.evidence.write_text(self.expected_evidence)
                relative = next(iter(self.history))
                self.history[relative] = b"later checkout content"
                (self.state / relative).write_bytes(self.history[relative])
                self.expected_start_log += "replacement attempted\n"
                self.start_log.write_text(self.expected_start_log)
            if interrupt_target and path == target:
                raise KeyboardInterrupt("interrupted after backup")
            if fail_target and path == target:
                raise BoardError("INJECTED_START", "new runtime did not start")
            return {**health, "runtimeContentId": path.name, "schemaVersion": SCHEMA_VERSION}

        with ExitStack() as stack:
            stack.enter_context(mock.patch.dict(os.environ, {"HMB_RUNTIME_ROOT": str(self.runtime_root)}))
            for name, options in (
                ("get_state_dir", {"return_value": self.state}),
                ("runtime.is_ready", {"return_value": True}),
                ("runtime.read_ready", {"return_value": {"sourceCommit": "fixture-commit"}}),
                ("runtime.materialize", {"return_value": {"runtimeDir": str(target)}}),
                ("probe", {"return_value": health}),
                ("detach", {"return_value": None}),
                ("start", {"side_effect": start}),
                ("command", {"return_value": {"leaks": []}}),
            ):
                stack.enter_context(mock.patch("hey_my_buddy.install.upgrade." + name, **options))
            stack.enter_context(mock.patch("hey_my_buddy.install.upgrade.write_active_runtime"))
            stack.enter_context(mock.patch("hey_my_buddy.blackboard.tasks.storage.prune_old_runtimes",
                                           return_value={"complete": True}))
            if fail_backup:
                stack.enter_context(mock.patch.object(
                    backup, "create", side_effect=BoardError("BACKUP_INVALID", "injected backup failure")))
            if interrupt_target:
                with self.assertRaises(KeyboardInterrupt):
                    upgrade.upgrade({})
                journal = json.loads((self.state / "upgrade.json").read_text())
                self.assertEqual(journal["phase"], "prepared")
                return upgrade.recover(self.state, self.runtime_root)
            return upgrade.upgrade({})

    def assert_history_preserved(self):
        self.assertEqual((self.state / "board.sqlite3").read_bytes(), self.original_board)
        self.assertEqual((self.state / "console-settings.json").read_text(), '{"theme":"dark"}')
        self.assertEqual(self.evidence.read_text(), self.expected_evidence)
        self.assertEqual(self.start_log.read_text(), self.expected_start_log)
        self.assertEqual({path: (self.state / path).read_bytes() for path in self.history}, self.history)
        if self.expected_evidence != '{"retained":"evidence"}':
            self.assertNotEqual(self.history, self.original_history)
        self.assertEqual({path.relative_to(self.retained_archive): path.read_bytes()
                          for path in self.retained_archive.rglob("*") if path.is_file()}, self.archive_bytes)
        self.assertEqual(upgrade.idle_snapshot(self.state), self.before)

    def test_same_schema_cutovers_keep_board_and_history(self):
        first = self.runtime_root / ("b" * 32)
        result = self.cutover(self.previous, first)
        self.assertTrue(result["upgraded"], result)
        self.assertEqual(upgrade.idle_snapshot(self.state)['schema'], SCHEMA_VERSION)
        self.assertNotIn("archive", result)
        self.assertEqual(backup.verify(Path(result["backup"]["path"]))["schema"], SCHEMA_VERSION)
        self.assert_history_preserved()

        second = self.runtime_root / ("c" * 32)
        result = self.cutover(first, second)
        self.assertTrue(result["upgraded"], result)
        self.assertNotIn("archive", result)
        self.assertEqual(backup.verify(Path(result["backup"]["path"]))["schema"], SCHEMA_VERSION)
        self.assert_history_preserved()
        self.assertEqual(self.started_runtimes, [first, second])

    def test_failed_new_runtime_returns_to_previous_runtime_with_history(self):
        target = self.runtime_root / ("b" * 32)
        result = self.cutover(self.previous, target, fail_target=True)
        self.assertFalse(result["upgraded"], result)
        self.assertEqual(result["error"]["details"]["reason"]["code"], "INJECTED_START")
        self.assertEqual(result["error"]["message"], "Upgrade failed; the previous runtime and backup were restored")
        self.assertEqual(result["rollback"]["runtimeContentId"], self.previous.name)
        self.assertEqual(self.started_runtimes, [target, self.previous])
        self.assert_history_preserved()

    def test_failed_backup_returns_to_previous_runtime_with_history(self):
        target = self.runtime_root / ("b" * 32)
        result = self.cutover(self.previous, target, fail_backup=True)
        self.assertFalse(result["upgraded"], result)
        self.assertEqual(result["error"]["details"]["reason"]["code"], "BACKUP_INVALID")
        self.assertEqual(result["error"]["message"], "Installation failed; the previous runtime was restored")
        self.assertNotIn("backup", result)
        self.assertEqual(result["rollback"]["runtimeContentId"], self.previous.name)
        self.assertEqual(self.started_runtimes, [self.previous])
        self.assert_history_preserved()

    def test_interrupted_install_journal_recovers_with_history(self):
        target = self.runtime_root / ("b" * 32)
        result = self.cutover(self.previous, target, interrupt_target=True)
        self.assertFalse(result["upgraded"], result)
        self.assertTrue(result["recoveredJournal"])
        self.assertEqual(result["rollback"]["runtimeContentId"], self.previous.name)
        self.assertFalse((self.state / "upgrade.json").exists())
        self.assertEqual(self.started_runtimes, [target, self.previous])
        self.assert_history_preserved()
