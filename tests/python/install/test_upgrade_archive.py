"""Runtime cutovers preserve historical workspace files."""
from contextlib import closing, ExitStack
import json
import os
from pathlib import Path
import shutil
import sqlite3
from unittest import mock

from hey_my_buddy.protocol.contracts import CONTRACT_VERSION
from support import BoardTestCase
from blackboard.store.upgrade_fixture import FIXTURE, load_fixture
from hey_my_buddy.blackboard.store import backup, upgrade as schema_upgrade
from hey_my_buddy.blackboard.store.db import SCHEMA_VERSION, UPGRADE_BASE_SCHEMA
from hey_my_buddy.blackboard.store.store import BoardStore
from hey_my_buddy.errors import BoardError
from hey_my_buddy.install import upgrade


class UpgradeCutoverTests(BoardTestCase):
    def setUp(self):
        super().setUp()
        self.state = self.directory / "state"
        load_fixture(self.state)
        resources = json.loads((FIXTURE / 'workspace-resources.json').read_text(encoding='utf-8'))
        self.private_files = {relative: (self.state / relative).read_bytes() for relative in resources['files']}
        self.legacy_files = {relative for relative in self.private_files
                             if '/goals/' in relative or relative.startswith('decisions/')
                             or relative in ('legacy-worker-cleanup.lock', 'worker.log')}
        self.assertEqual(len(self.legacy_files), 7)
        (self.state / "console-settings.json").write_text('{"theme":"dark"}', encoding="utf-8", newline="\n")
        store = BoardStore(self.state)
        store.db.close()
        old_backup = Path(backup.create(self.state, runtime_identity=None, plugin_commit=None, contract_version=CONTRACT_VERSION)["path"])
        self.retained_archive = self.state / "archives" / "previous"
        shutil.copytree(old_backup, self.retained_archive)
        self.archive_bytes = {path.relative_to(self.retained_archive): path.read_bytes()
                              for path in self.retained_archive.rglob("*") if path.is_file()}
        self.assertEqual(backup.verify(self.retained_archive)["schema"], UPGRADE_BASE_SCHEMA)
        self.evidence = self.state / "attempts" / "fixture-run" / "fixture-attempt" / "result.json"
        self.evidence.parent.mkdir(parents=True, exist_ok=True)
        self.evidence.write_text('{"retained":"evidence"}', encoding="utf-8", newline="\n")
        self.expected_evidence = self.evidence.read_text(encoding="utf-8")
        self.start_log = self.state / "upgrade-start.log"
        self.start_log.write_text("retained startup log\n", encoding="utf-8", newline="\n")
        self.expected_start_log = self.start_log.read_text(encoding="utf-8")
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
        (self.state / "control.json").write_text(json.dumps(endpoint), encoding="utf-8", newline="\n")
        health = {**endpoint, "runtimeContentId": previous.name, "runtimeStable": True,
                  "maxConcurrent": 2, "waitCapacity": 4}

        def start(_state, path):
            self.started_runtimes.append(path)
            if (fail_target or interrupt_target) and path == target:
                # Model writes by a failed replacement after the archive was
                # sealed: rollback replays board/settings, not these records.
                with closing(sqlite3.connect(self.state / "board.sqlite3")) as connection, connection:
                    connection.execute("INSERT INTO meta(key,value) VALUES('injected-after-backup','changed')")
                (self.state / "console-settings.json").write_text('{"theme":"light"}', encoding="utf-8", newline="\n")
                self.expected_evidence = '{"retained":"later evidence"}'
                self.evidence.write_text(self.expected_evidence, encoding="utf-8", newline="\n")
                relative = next(iter(self.history))
                self.history[relative] = b"later checkout content"
                (self.state / relative).write_bytes(self.history[relative])
                self.expected_start_log += "replacement attempted\n"
                self.start_log.write_text(self.expected_start_log, encoding="utf-8", newline="\n")
            if interrupt_target and path == target:
                raise KeyboardInterrupt("interrupted after backup")
            if fail_target and path == target:
                raise BoardError("INJECTED_START", "new runtime did not start")
            with closing(sqlite3.connect(self.state / "board.sqlite3")) as connection:
                schema = int(connection.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0])
            return {**health, "runtimeContentId": path.name, "schemaVersion": schema}

        with ExitStack() as stack:
            stack.enter_context(mock.patch.dict(os.environ, {"HMB_RUNTIME_ROOT": str(self.runtime_root)}))
            for name, options in (
                ("runtime.is_ready", {"return_value": True}),
                ("runtime.read_ready", {"return_value": {"sourceCommit": "fixture-commit"}}),
                ("runtime.materialize", {"return_value": {"runtimeDir": str(target)}}),
                ("probe", {"return_value": health}),
                ("detach", {"return_value": None}),
                ("start", {"side_effect": start}),
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
                    upgrade.upgrade(self.state)
                journal = json.loads((self.state / "upgrade.json").read_text(encoding="utf-8"))
                self.assertEqual(journal["phase"], "prepared")
                return upgrade.recover(self.state, self.runtime_root)
            return upgrade.upgrade(self.state)

    def assert_history_preserved(self, *, migrated=False):
        if not migrated:
            self.assertEqual((self.state / "board.sqlite3").read_bytes(), self.original_board)
        self.assertEqual((self.state / "console-settings.json").read_text(encoding="utf-8"), '{"theme":"dark"}')
        self.assertEqual(self.evidence.read_text(encoding="utf-8"), self.expected_evidence)
        self.assertEqual(self.start_log.read_text(encoding="utf-8"), self.expected_start_log)
        self.assertEqual({path: (self.state / path).read_bytes() for path in self.history}, self.history)
        if self.expected_evidence != '{"retained":"evidence"}':
            self.assertNotEqual(self.history, self.original_history)
        self.assertEqual({path.relative_to(self.retained_archive): path.read_bytes()
                          for path in self.retained_archive.rglob("*") if path.is_file()}, self.archive_bytes)
        for relative, content in self.private_files.items():
            path = self.state / relative
            if migrated and relative in self.legacy_files:
                self.assertFalse(path.exists(), relative)
            else:
                expected = self.history.get(Path(relative), content)
                self.assertEqual(path.read_bytes(), expected, relative)
        actual = upgrade.idle_snapshot(
            self.state, column_projections=self.before['columnProjections'] if migrated else None)
        if migrated:
            self.assertEqual(actual['schema'], SCHEMA_VERSION)
            self.assertEqual(set(actual['tables']), set(self.before['tables']))
            self.assertEqual(actual['retainedMetaSha256'], self.before['retainedMetaSha256'])
            for table, fingerprint in self.before['fingerprints'].items():
                if table != 'meta':
                    self.assertEqual(actual['fingerprints'][table],
                                     self.before['expectedFingerprints'].get(table, fingerprint),
                                     table)
        else:
            self.assertEqual(actual, self.before)

    def test_invalid_delivery_preserves_its_cause_in_the_installation_reply(self):
        with closing(sqlite3.connect(self.state / 'board.sqlite3')) as connection, connection:
            message_id, original = connection.execute(
                "SELECT message_id,delivery_json FROM messages"
                " WHERE json_type(delivery_json,'$.supported') IS NOT NULL LIMIT 1").fetchone()
            delivery = {**json.loads(original), 'supported': False}
            connection.execute('UPDATE messages SET delivery_json=? WHERE message_id=?',
                               (json.dumps(delivery), message_id))
        with closing(sqlite3.connect(self.state / 'board.sqlite3')) as connection:
            stock = list(connection.iterdump())
        target = self.runtime_root / ('b' * 32)
        result = self.cutover(self.previous, target)
        self.assertFalse(result['upgraded'], result)
        cause = result['error']['details']['reason']
        self.assertEqual(cause['code'], 'UPGRADE_INVALID_BOARD')
        self.assertEqual(cause['details']['messageId'], message_id)
        self.assertEqual(cause['details']['field'], 'supported')
        self.assertEqual(json.loads((self.state / 'upgrade-last.json').read_text())['failure'], cause)
        self.assertEqual(self.started_runtimes, [self.previous])
        with closing(sqlite3.connect(self.state / 'board.sqlite3')) as connection:
            self.assertEqual(list(connection.iterdump()), stock)

    def test_schema_upgrade_and_same_schema_cutover_keep_history(self):
        first = self.runtime_root / ("b" * 32)
        result = self.cutover(self.previous, first)
        self.assertTrue(result["upgraded"], result)
        self.assertTrue(result['legacyLayoutCleanup']['complete'])
        self.assertEqual(set(result['legacyLayoutCleanup']['removed']),
                         {'harnesses/' + harness + '/goals' for harness in ('codex', 'claude', 'zcode', 'dsh')}
                         | {'decisions', 'legacy-worker-cleanup.lock', 'worker.log'})
        self.assertEqual(upgrade.idle_snapshot(self.state)['schema'], SCHEMA_VERSION)
        self.assertNotIn("archive", result)
        self.assertEqual(backup.verify(Path(result["backup"]["path"]))["schema"], UPGRADE_BASE_SCHEMA)
        self.assert_history_preserved(migrated=True)
        migrated_board = (self.state / 'board.sqlite3').read_bytes()

        second = self.runtime_root / ("c" * 32)
        later = self.state / 'harnesses/codex/goals/after-conversion.jsonl'
        later.parent.mkdir(parents=True)
        later.write_bytes(b'preserve after the one conversion')
        with mock.patch.object(schema_upgrade, 'cleanup_legacy_layout',
                               side_effect=AssertionError('unexpected legacy cleanup')) as cleanup:
            result = self.cutover(first, second)
        cleanup.assert_not_called()
        self.assertTrue(result["upgraded"], result)
        self.assertNotIn('legacyLayoutCleanup', result)
        self.assertEqual(later.read_bytes(), b'preserve after the one conversion')
        self.assertNotIn("archive", result)
        self.assertEqual(backup.verify(Path(result["backup"]["path"]))["schema"], SCHEMA_VERSION)
        self.assert_history_preserved(migrated=True)
        self.assertEqual((self.state / 'board.sqlite3').read_bytes(), migrated_board)
        self.assertEqual(self.started_runtimes, [first, second])

    def test_verified_base_conversion_recovery_cleans_legacy_layout(self):
        target = self.runtime_root / ('b' * 32)
        target.mkdir()
        schema_upgrade.migrate_schema(self.state)
        journal = {'phase': 'verified', 'contractChanged': True,
                   'previous': str(self.previous), 'target': str(target),
                   'before': self.before, 'expected': self.before,
                   'backup': {'path': str(self.state / 'backups/current')}}
        upgrade.write_journal(self.state / 'upgrade.json', journal)
        health = {'runtimeContentId': target.name, 'runtimeStable': True, 'schemaVersion': SCHEMA_VERSION}
        with mock.patch.object(upgrade.runtime, 'is_ready', return_value=True), \
             mock.patch.object(upgrade, 'probe', side_effect=lambda _state, runtime: health if runtime == target else None), \
             mock.patch.object(upgrade, 'write_active_runtime'), \
             mock.patch.object(upgrade, 'retain_runtimes', return_value={'complete': True}), \
             mock.patch.object(upgrade, 'start', side_effect=AssertionError('unexpected restart')):
            result = upgrade.recover(self.state, self.runtime_root)
        self.assertTrue(result['upgraded'], result)
        self.assertTrue(result['recoveredJournal'])
        self.assertTrue(result['legacyLayoutCleanup']['complete'])
        self.assertEqual(len(result['legacyLayoutCleanup']['removed']), 7)
        self.assertFalse((self.state / 'upgrade.json').exists())
        self.assert_history_preserved(migrated=True)

    def test_legacy_cleanup_failure_is_reported_after_verified_cutover(self):
        target = self.runtime_root / ('b' * 32)
        remove = upgrade.private_dirs.remove_tree

        def refuse_goals(path, **options):
            if path == self.state / 'harnesses/codex/goals':
                raise PermissionError('retired native directory is busy')
            return remove(path, **options)

        with mock.patch.object(upgrade.private_dirs, 'remove_tree', side_effect=refuse_goals):
            result = self.cutover(self.previous, target)
        self.assertTrue(result['upgraded'], result)
        self.assertFalse(result['legacyLayoutCleanup']['complete'])
        self.assertEqual(result['legacyLayoutCleanup']['failures'], [
            {'path': 'harnesses/codex/goals', 'code': 'PermissionError',
             'message': 'retired native directory is busy'}])
        self.assertEqual(self.started_runtimes, [target])
        self.assertTrue((self.state / 'harnesses/codex/goals').is_dir())
        self.assertFalse((self.state / 'harnesses/claude/goals').exists())
        self.assertFalse((self.state / 'worker.log').exists())
        self.assertFalse((self.state / 'upgrade.json').exists())

    def test_failed_new_runtime_returns_to_previous_runtime_with_history(self):
        target = self.runtime_root / ("b" * 32)
        result = self.cutover(self.previous, target, fail_target=True)
        self.assertFalse(result["upgraded"], result)
        self.assertEqual(result["error"]["details"]["reason"]["code"], "INJECTED_START")
        self.assertEqual(result["error"]["message"], "Installation failed; the previous runtime and backup were restored")
        self.assertEqual(result["rollback"]["runtimeContentId"], self.previous.name)
        self.assertEqual(self.started_runtimes, [target, self.previous])
        self.assert_history_preserved()

    def test_migration_uses_a_verified_base_backup_under_both_owner_locks(self):
        migrate = schema_upgrade.migrate_schema

        def checked_migrate(state):
            archived = state / 'backups/current'
            self.assertEqual(backup.verify(archived)['schema'], UPGRADE_BASE_SCHEMA)
            for name in ('control-daemon.lock', 'board-owner.lock'):
                with self.assertRaises(BoardError) as caught:
                    with upgrade.file_lock(state / name):
                        self.fail('migration ran outside ' + name)
                self.assertEqual(caught.exception.code, 'UPGRADE_BUSY')
            migrate(state)

        target = self.runtime_root / ('b' * 32)
        with mock.patch.object(schema_upgrade, 'migrate_schema', side_effect=checked_migrate) as conversion:
            result = self.cutover(self.previous, target)
        self.assertTrue(result['upgraded'], result)
        conversion.assert_called_once_with(self.state)
        self.assert_history_preserved(migrated=True)

    def test_migration_failure_restores_the_base_before_restarting_the_old_runtime(self):
        migrate = schema_upgrade.migrate_schema

        def fail_after_migration(state):
            migrate(state)
            raise sqlite3.OperationalError('injected migration failure')

        target = self.runtime_root / ('b' * 32)
        with mock.patch.object(schema_upgrade, 'migrate_schema', side_effect=fail_after_migration):
            result = self.cutover(self.previous, target)
        self.assertFalse(result['upgraded'], result)
        self.assertEqual(result['error']['code'], 'UPGRADE_ROLLED_BACK')
        self.assertEqual(result['error']['details']['reason']['code'], 'OperationalError')
        self.assertEqual(result['rollback']['schemaVersion'], UPGRADE_BASE_SCHEMA)
        self.assertEqual(self.started_runtimes, [self.previous])
        self.assert_history_preserved()

    def test_retention_defect_after_verification_does_not_restore_over_new_work(self):
        target = self.runtime_root / ('b' * 32)

        def failed_retention(*_args):
            self.assertFalse((self.state / 'upgrade.json').exists())
            for relative in self.legacy_files:
                self.assertFalse((self.state / relative).exists(), relative)
            (self.state / 'console-settings.json').write_text('{"theme":"after-verification"}', encoding='utf-8')
            raise TypeError('retention defect')

        with mock.patch.object(upgrade, 'retain_runtimes', side_effect=failed_retention), self.assertRaises(TypeError):
            self.cutover(self.previous, target)
        self.assertEqual(self.started_runtimes, [target])
        self.assertEqual((self.state / 'console-settings.json').read_text(encoding='utf-8'), '{"theme":"after-verification"}')
        self.assertEqual(json.loads((self.state / 'upgrade-last.json').read_text(encoding='utf-8'))['phase'], 'verified')

    def test_verified_history_write_failure_leaves_the_recoverable_new_generation(self):
        target = self.runtime_root / ('b' * 32)
        write = upgrade.write_journal

        def refuse_history(path, journal):
            if path == self.state / 'upgrade-last.json' and journal['phase'] == 'verified':
                raise PermissionError('history write denied')
            return write(path, journal)

        with mock.patch.object(upgrade, 'write_journal', side_effect=refuse_history), self.assertRaises(PermissionError):
            self.cutover(self.previous, target)
        self.assertEqual(self.started_runtimes, [target])
        self.assertEqual(json.loads((self.state / 'upgrade.json').read_text(encoding='utf-8'))['phase'], 'verified')

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
