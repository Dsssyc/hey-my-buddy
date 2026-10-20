"""The installed schema-26 board converts only after backup and owner release."""
from contextlib import ExitStack
import json
import os
from pathlib import Path
import sqlite3
from unittest import mock

from support import BoardTestCase
from blackboard.store.upgrade_fixture import load_fixture
from hey_my_buddy.blackboard.store import backup, upgrade as schema_upgrade
from hey_my_buddy.blackboard.store.db import SCHEMA_VERSION
from hey_my_buddy.errors import BoardError
from hey_my_buddy.install import upgrade


class SchemaUpgradeInstallTests(BoardTestCase):
    def setUp(self):
        super().setUp()
        self.state = self.directory / 'state'
        load_fixture(self.state)
        self.original = (self.state / 'board.sqlite3').read_bytes()
        self.before = upgrade.idle_snapshot(self.state)
        self.root = self.directory / 'runtimes'
        self.previous = self.root / ('a' * 32)
        self.target = self.root / ('b' * 32)
        self.previous.mkdir(parents=True)
        self.target.mkdir()
        self.endpoint = {'runtimeIdentity': 'runtime:' + self.previous.name,
                         'serviceId': 'old', 'pid': 123, 'contractVersion': '0.48.0'}
        (self.state / 'control.json').write_text(json.dumps(self.endpoint))
        self.health = {**self.endpoint, 'runtimeContentId': self.previous.name,
                       'runtimeStable': True, 'maxConcurrent': 2, 'waitCapacity': 4,
                       'schemaVersion': 26}
        self.stack = self.enterContext(ExitStack())
        self.stack.enter_context(mock.patch.dict(os.environ, {'HMB_RUNTIME_ROOT': str(self.root)}))
        for name, options in (
            ('get_state_dir', {'return_value': self.state}),
            ('runtime.is_ready', {'return_value': True}),
            ('runtime.read_ready', {'return_value': {'sourceCommit': 'fixture'}}),
            ('runtime.materialize', {'return_value': {'runtimeDir': str(self.target)}}),
            ('probe', {'return_value': self.health}),
            ('detach', {'return_value': None}),
            ('start', {'side_effect': self.start}),
            ('command', {'return_value': {'leaks': []}}),
            ('write_active_runtime', {}),
            ('retain_runtimes', {'return_value': {'complete': True}}),
        ):
            self.stack.enter_context(mock.patch('hey_my_buddy.install.upgrade.' + name, **options))
        self.started = []

    def start(self, state, runtime):
        self.started.append(runtime)
        with sqlite3.connect(state / 'board.sqlite3') as db:
            version = int(db.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0])
        self.assertEqual(version, SCHEMA_VERSION if runtime == self.target else 26)
        return {**self.health, 'runtimeContentId': runtime.name, 'schemaVersion': version}

    def test_conversion_uses_verified_backup_and_verifies_the_converted_snapshot(self):
        original_upgrade = schema_upgrade.upgrade
        def convert(state):
            journal = json.loads((state / 'upgrade.json').read_text())
            self.assertEqual(journal['phase'], 'converting')
            self.assertEqual(backup.verify(Path(journal['backup']['path']))['schema'], 26)
            self.assertEqual(self.started, [])
            original_upgrade(state)
        with mock.patch.object(schema_upgrade, 'upgrade', side_effect=convert):
            result = upgrade.upgrade({})
        self.assertTrue(result['upgraded'], result)
        journal = json.loads((self.state / 'upgrade-last.json').read_text())
        self.assertEqual(journal['before']['schema'], 26)
        self.assertEqual(journal['expected']['schema'], SCHEMA_VERSION)
        self.assertEqual(result['verification']['schemaVersion'], SCHEMA_VERSION)
        self.assertEqual(self.started, [self.target])

    def test_failure_after_conversion_restores_exact_board_and_previous_runtime(self):
        original_upgrade = schema_upgrade.upgrade
        def fail(state):
            original_upgrade(state)
            raise BoardError('INJECTED_CONVERSION', 'failure after commit')
        with mock.patch.object(schema_upgrade, 'upgrade', side_effect=fail):
            result = upgrade.upgrade({})
        self.assertFalse(result['upgraded'])
        self.assertEqual(result['error']['code'], 'UPGRADE_ROLLED_BACK')
        self.assertEqual(result['rollback']['schemaVersion'], 26)
        self.assertEqual((self.state / 'board.sqlite3').read_bytes(), self.original)
        self.assertEqual(upgrade.idle_snapshot(self.state), self.before)
        self.assertEqual(self.started, [self.previous])

    def test_interrupted_conversion_is_recovered_from_backup_before_old_start(self):
        original_upgrade = schema_upgrade.upgrade
        def interrupt(state):
            original_upgrade(state)
            raise KeyboardInterrupt('after commit before expected snapshot')
        with mock.patch.object(schema_upgrade, 'upgrade', side_effect=interrupt):
            with self.assertRaises(KeyboardInterrupt):
                upgrade.upgrade({})
        self.assertEqual(json.loads((self.state / 'upgrade.json').read_text())['phase'], 'converting')
        with mock.patch.object(upgrade, 'probe', return_value=None):
            recovered = upgrade.recover(self.state, self.root)
        self.assertFalse(recovered['upgraded'])
        self.assertEqual(recovered['rollback']['schemaVersion'], 26)
        self.assertEqual((self.state / 'board.sqlite3').read_bytes(), self.original)
        self.assertEqual(self.started, [self.previous])

    def test_target_start_failure_restores_base_and_old_runtime(self):
        start = self.start
        def fail_target(state, runtime):
            if runtime == self.target:
                raise BoardError('INJECTED_START', 'new service cannot start')
            return start(state, runtime)
        with mock.patch.object(upgrade, 'start', side_effect=fail_target):
            result = upgrade.upgrade({})
        self.assertFalse(result['upgraded'])
        self.assertEqual(result['error']['code'], 'UPGRADE_ROLLED_BACK')
        self.assertEqual(result['rollback']['schemaVersion'], 26)
        self.assertEqual((self.state / 'board.sqlite3').read_bytes(), self.original)
        self.assertEqual(self.started, [self.previous])
