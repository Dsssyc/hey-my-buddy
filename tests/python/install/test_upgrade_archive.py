"""Same-schema cutovers preserve the board and its historical workspace files."""
from contextlib import ExitStack
import json
import os
from pathlib import Path
import shutil
from unittest import mock

from support import BoardTestCase
from blackboard.store.upgrade_fixture import load_fixture
from hey_my_buddy.blackboard.store import backup
from hey_my_buddy.blackboard.store.upgrade import upgrade_database
from hey_my_buddy.blackboard.store.db import SCHEMA_VERSION
from hey_my_buddy.blackboard.store.store import BoardStore
from hey_my_buddy.errors import BoardError
from hey_my_buddy.install import upgrade


class SameSchemaCutoverTests(BoardTestCase):
    def setUp(self):
        super().setUp()
        self.state = self.directory / "state"
        load_fixture(self.state)
        upgrade_database(self.state)
        (self.state / "console-settings.json").write_text('{"theme":"dark"}')
        legacy = self.state / 'workers' / 'local'
        legacy.mkdir(parents=True)
        (legacy / 'startup.json').write_text('{"legacy":true}')
        (legacy / 'supervisor.lock').write_text('')
        (self.state / 'worker-pool.json').write_text('{"legacy":true}')
        store = BoardStore(self.state)
        old_backup = Path(backup.create(store)["path"])
        store.db.close()
        self.retained_archive = self.state / "archives" / "previous"
        shutil.copytree(old_backup, self.retained_archive)
        self.archive_bytes = {path.relative_to(self.retained_archive): path.read_bytes()
                              for path in self.retained_archive.rglob("*") if path.is_file()}
        self.assertEqual(backup.verify(self.retained_archive)["schema"], SCHEMA_VERSION)
        self.assertFalse(any('workers/' in path or 'worker-pool.json' in path
                             for path in backup.verify(self.retained_archive)['files']))
        self.original_board = (self.state / "board.sqlite3").read_bytes()
        self.before = upgrade.idle_snapshot(self.state)
        self.history = {path.relative_to(self.state): path.read_bytes()
                        for path in (self.state / "workspaces").rglob("*") if path.is_file()}
        self.assertTrue(self.history)
        self.runtime_root = self.directory / "runtimes"
        self.previous = self.runtime_root / ("a" * 32)
        self.previous.mkdir(parents=True)
        self.started_runtimes = []

    def cutover(self, previous: Path, target: Path, *, fail_target=False):
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
            return {**health, "runtimeContentId": path.name, "schemaVersion": SCHEMA_VERSION}

        with ExitStack() as stack:
            stack.enter_context(mock.patch.dict(os.environ, {"HMB_RUNTIME_ROOT": str(self.runtime_root)}))
            for name, options in (
                ("get_state_dir", {"return_value": self.state}),
                ("runtime.is_ready", {"return_value": True}),
                ("runtime.read_ready", {"return_value": {"sourceCommit": "fixture-commit"}}),
                ("runtime.materialize", {"return_value": {"runtimeDir": str(target)}}),
                ("probe", {"return_value": health}),
                ("detach", {"side_effect": lambda state, _target: upgrade.retire_workers(state)}),
                ("start", {"side_effect": start}),
                ("command", {"return_value": {"leaks": []}}),
            ):
                stack.enter_context(mock.patch("hey_my_buddy.install.upgrade." + name, **options))
            stack.enter_context(mock.patch("hey_my_buddy.install.upgrade.write_active_runtime"))
            stack.enter_context(mock.patch("hey_my_buddy.blackboard.tasks.storage.prune_old_runtimes",
                                           return_value={"complete": True}))
            return upgrade.upgrade({})

    def assert_history_preserved(self):
        self.assertEqual((self.state / "board.sqlite3").read_bytes(), self.original_board)
        self.assertEqual((self.state / "console-settings.json").read_text(), '{"theme":"dark"}')
        self.assertEqual({path: (self.state / path).read_bytes() for path in self.history}, self.history)
        self.assertEqual({path.relative_to(self.retained_archive): path.read_bytes()
                          for path in self.retained_archive.rglob("*") if path.is_file()}, self.archive_bytes)
        self.assertEqual(upgrade.idle_snapshot(self.state), self.before)

    def test_two_same_schema_upgrades_keep_board_and_history(self):
        first = self.runtime_root / ("b" * 32)
        result = self.cutover(self.previous, first)
        self.assertTrue(result["upgraded"], result)
        self.assertNotIn("archive", result)
        self.assertEqual(backup.verify(Path(result["backup"]["path"]))["schema"], SCHEMA_VERSION)
        self.assertFalse((self.state / 'workers').exists())
        self.assertFalse((self.state / 'worker-pool.json').exists())
        self.assert_history_preserved()

        second = self.runtime_root / ("c" * 32)
        result = self.cutover(first, second)
        self.assertTrue(result["upgraded"], result)
        self.assertNotIn("archive", result)
        self.assert_history_preserved()
        self.assertEqual(self.started_runtimes, [first, second])

    def test_retired_worker_cleanup_failure_keeps_the_verified_runtime_available(self):
        target = self.runtime_root / ('b' * 32)
        with mock.patch.object(upgrade, 'remove_legacy_worker_state', side_effect=OSError('denied')):
            result = self.cutover(self.previous, target)
        self.assertTrue(result['upgraded'], result)
        self.assertIsNone(result['rollback'])
        self.assertIn('denied', result['legacyCleanupError'])
        self.assertFalse((self.state / 'upgrade.json').exists())
        record = json.loads((self.state / 'upgrade-last.json').read_text())
        self.assertEqual(record['phase'], 'verified')
        self.assertTrue(record['legacyWorkerCleanup'])
        self.assertEqual(self.started_runtimes, [target])
        self.assert_history_preserved()
        self.assertIsNone(upgrade.retry_legacy_worker_cleanup(self.state))
        self.assertFalse((self.state / 'workers').exists())
        self.assertFalse((self.state / 'worker-pool.json').exists())

    def test_failed_new_runtime_returns_to_previous_runtime_with_history(self):
        target = self.runtime_root / ("b" * 32)
        result = self.cutover(self.previous, target, fail_target=True)
        self.assertFalse(result["upgraded"], result)
        self.assertEqual(result["error"]["details"]["reason"]["code"], "INJECTED_START")
        self.assertEqual(result["rollback"]["runtimeContentId"], self.previous.name)
        self.assertTrue((self.state / 'workers/local/startup.json').exists())
        self.assertTrue((self.state / 'workers/local/retire.request').exists())
        self.assertTrue((self.state / 'worker-pool.json').exists())
        self.assertEqual(self.started_runtimes, [target, self.previous])
        self.assert_history_preserved()
