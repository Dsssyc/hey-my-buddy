"""Verified archive publication, exact rollback, and a later same-schema cutover."""
from contextlib import ExitStack
import gzip
import json
import os
from pathlib import Path
from unittest import mock

from support import BoardTestCase
from blackboard.store.upgrade_fixture import load_fixture
from hey_my_buddy.blackboard.store import backup
from hey_my_buddy.blackboard.store.db import SCHEMA_VERSION
from hey_my_buddy.errors import BoardError
from hey_my_buddy.install import upgrade


class ArchiveCutoverTests(BoardTestCase):
    def setUp(self):
        super().setUp()
        self.state = self.directory / "state"
        self.fixture = load_fixture(self.state)
        self.board_path = self.state / "board.sqlite3"
        self.original_board = self.board_path.read_bytes()
        self.original_history = {path: path.read_bytes() for path in (self.state / 'workspaces').rglob('*') if path.is_file()}
        (self.state / 'console-settings.json').write_text('{"theme":"dark"}')
        self.runtime_root = self.directory / "runtimes"
        self.previous = self.runtime_root / ("a" * 32)
        self.previous.mkdir(parents=True)
        self.endpoint = {"runtimeIdentity": "runtime:" + self.previous.name,
                         "serviceId": "private-old", "pid": 123, "contractVersion": "fixture-contract"}
        (self.state / "control.json").write_text(json.dumps(self.endpoint))
        self.started_runtimes = []

    def cutover(self, previous, target, *, fail_archive=False, corrupt_archive=False,
                fail_target=False, mutate_after_archive=None):
        target.mkdir(parents=True, exist_ok=True)
        endpoint = {"runtimeIdentity": "runtime:" + previous.name,
                    "serviceId": "private-old", "pid": 123, "contractVersion": "fixture-contract"}
        (self.state / "control.json").write_text(json.dumps(endpoint))
        health = {**endpoint, "runtimeContentId": previous.name, "runtimeStable": True,
                  "maxConcurrent": 2, "waitCapacity": 4}

        def start(_state, path):
            self.started_runtimes.append(path)
            if fail_target and path == target:
                raise BoardError("INJECTED_START", "new runtime did not start")
            return {**health, "runtimeContentId": path.name,
                    "schemaVersion": SCHEMA_VERSION if path == target else 18}

        with ExitStack() as stack:
            stack.enter_context(mock.patch.dict(os.environ, {"BUDDY_RUNTIME_ROOT": str(self.runtime_root)}))
            for name, options in (
                ("get_state_dir", {"return_value": self.state}),
                ("runtime.is_ready", {"return_value": True}),
                ("runtime.read_ready", {"return_value": {"sourceCommit": "fixture-commit"}}),
                ("runtime.materialize", {"return_value": {"runtimeDir": str(target)}}),
                ("probe", {"return_value": health}),
                ("detach", {}),
                ("start", {"side_effect": start}),
                ("command", {"return_value": {"leaks": []}}),
            ):
                stack.enter_context(mock.patch("hey_my_buddy.install.upgrade." + name, **options))
            stack.enter_context(mock.patch("hey_my_buddy.install.launcher.write_active_runtime"))
            stack.enter_context(mock.patch("hey_my_buddy.blackboard.tasks.storage.prune_old_runtimes",
                                           return_value={"complete": True}))
            if fail_archive:
                stack.enter_context(mock.patch.object(upgrade, "archive_backup",
                    side_effect=BoardError("INJECTED_ARCHIVE", "archive verification failed")))
            if corrupt_archive:
                original = upgrade.archive_history_paths

                def corrupt(state, path):
                    (path / "board.sqlite3.gz").write_bytes(b"damaged archive")
                    return original(state, path)

                stack.enter_context(mock.patch.object(upgrade, "archive_history_paths", side_effect=corrupt))
            if mutate_after_archive is not None:
                original = upgrade.archive_history_paths

                def mutate(state, path):
                    self.assertEqual(path.parent, state / "archives")
                    manifest = backup.verify(path)
                    self.original_history = {
                        state.joinpath(*Path(relative).parts[1:]):
                            state.joinpath(*Path(relative).parts[1:]).read_bytes()
                        for relative in manifest['files']
                        if Path(relative).parts[0] == 'state' and
                           Path(relative).parts[1] not in backup.STATE_FILES}
                    self.assertTrue(self.original_history)
                    mutate_after_archive(state, path)
                    return original(state, path)

                stack.enter_context(mock.patch.object(upgrade, "archive_history_paths", side_effect=mutate))
            return upgrade.upgrade({})

    def test_archive_failure_keeps_the_old_board_and_starts_old_runtime(self):
        target = self.runtime_root / ("b" * 32)
        result = self.cutover(self.previous, target, fail_archive=True)
        self.assertFalse(result["upgraded"], result)
        self.assertEqual(result["error"]["details"]["reason"]["code"], "INJECTED_ARCHIVE")
        self.assertEqual(self.board_path.read_bytes(), self.original_board)
        self.assertEqual(upgrade.idle_snapshot(self.state)["schema"], 18)
        self.assertEqual(list((self.state / "archives").glob("*")) if (self.state / "archives").exists() else [], [])

    def test_invalid_published_archive_does_not_replace_the_old_board_or_delete_history(self):
        target = self.runtime_root / ("b" * 32)
        historical_files = list((self.state / "workspaces").rglob("*.json"))
        self.assertTrue(historical_files)
        result = self.cutover(self.previous, target, corrupt_archive=True)
        self.assertFalse(result["upgraded"], result)
        self.assertEqual(self.board_path.read_bytes(), self.original_board)
        self.assertTrue(all(path.exists() for path in historical_files))
        self.assertEqual(upgrade.idle_snapshot(self.state)["schema"], 18)

    def test_changed_history_after_archive_publication_keeps_the_old_board_and_history(self):
        target = self.runtime_root / ("b" * 32)
        changed = {}

        def mutate_history(state, archive):
            manifest = backup.verify(archive)
            relative = next(relative for relative in manifest["files"]
                            if relative.startswith("state/workspaces/") and relative.endswith(".json"))
            path = state.joinpath(*Path(relative).parts[1:])
            changed[path] = path.read_bytes() + b"\n"
            path.write_bytes(changed[path])

        result = self.cutover(self.previous, target, mutate_after_archive=mutate_history)
        self.assertFalse(result["upgraded"], result)
        self.assertEqual(result["error"]["details"]["reason"]["code"], "UPGRADE_ARCHIVE_CHANGED")
        self.assertEqual(self.board_path.read_bytes(), self.original_board)
        self.assertEqual({path: path.read_bytes() for path in self.original_history},
                         {**self.original_history, **changed})
        self.assertEqual(self.started_runtimes, [self.previous])
        self.assertEqual(result["rollback"]["runtimeContentId"], self.previous.name)

    def test_changed_board_bytes_after_archive_publication_keeps_the_old_board_and_history(self):
        target = self.runtime_root / ("b" * 32)
        changed_board = self.original_board + b"\x00"

        def mutate_board(state, archive):
            self.assertTrue((archive / "manifest.json").is_file())
            (state / "board.sqlite3").write_bytes(changed_board)

        result = self.cutover(self.previous, target, mutate_after_archive=mutate_board)
        self.assertFalse(result["upgraded"], result)
        self.assertEqual(result["error"]["details"]["reason"]["code"], "UPGRADE_ARCHIVE_CHANGED")
        self.assertEqual(self.board_path.read_bytes(), changed_board)
        self.assertEqual({path: path.read_bytes() for path in self.original_history}, self.original_history)
        self.assertEqual(self.started_runtimes, [self.previous])
        self.assertEqual(result["rollback"]["runtimeContentId"], self.previous.name)

    def test_failed_new_runtime_restores_exact_schema_18_board_from_archive(self):
        target = self.runtime_root / ("b" * 32)
        evidence = {path.relative_to(self.state): path.read_bytes()
                    for path in (self.state / "workspaces").rglob("*.json")}
        self.assertTrue(evidence)
        result = self.cutover(self.previous, target, fail_target=True)
        self.assertFalse(result["upgraded"], result)
        archive = Path(result["archive"]["path"])
        self.assertEqual(gzip.decompress((archive / "board.sqlite3.gz").read_bytes()), self.original_board)
        self.assertEqual(self.board_path.read_bytes(), self.original_board)
        for relative, content in evidence.items():
            self.assertEqual((self.state / relative).read_bytes(), content)
        self.assertEqual(result["rollback"]["runtimeContentId"], self.previous.name)
        self.assertEqual({path: path.read_bytes() for path in self.original_history}, self.original_history)
        self.assertEqual(upgrade.idle_snapshot(self.state)["schema"], 18)

    def test_second_upgrade_keeps_the_first_archive_and_fresh_board(self):
        first = self.runtime_root / ("b" * 32)
        result = self.cutover(self.previous, first)
        self.assertTrue(result["upgraded"], result)
        archive = Path(result["archive"]["path"])
        self.assertEqual(archive.parent, self.state / "archives")
        self.assertTrue(archive.name.endswith("-schema-18"))
        self.assertEqual(gzip.decompress((archive / "board.sqlite3.gz").read_bytes()), self.original_board)
        manifest = backup.verify(archive)
        self.assertEqual(manifest["archivedFrom"], {"sourceCommit": "fixture-commit",
                                                     "version": "fixture-contract", "schema": 18})
        archive_bytes = {path.name: path.read_bytes() for path in archive.iterdir() if path.is_file()}
        self.assertEqual(upgrade.idle_snapshot(self.state)["schema"], SCHEMA_VERSION)
        self.assertEqual((self.state / 'console-settings.json').read_text(), '{"theme":"dark"}')
        archived_history = [relative for relative in manifest["files"]
                            if relative.startswith("state/")
                            and Path(relative).parts[1] not in backup.STATE_FILES]
        self.assertTrue(archived_history)
        for relative in archived_history:
            self.assertFalse((self.state / Path(*Path(relative).parts[1:])).exists(), relative)

        second = self.runtime_root / ("c" * 32)
        second_result = self.cutover(first, second)
        self.assertTrue(second_result["upgraded"], second_result)
        self.assertIsNone(second_result["archive"])
        self.assertEqual(list((self.state / "archives").iterdir()), [archive])
        self.assertEqual({path.name: path.read_bytes() for path in archive.iterdir() if path.is_file()}, archive_bytes)
        self.assertEqual(upgrade.idle_snapshot(self.state)["schema"], SCHEMA_VERSION)
