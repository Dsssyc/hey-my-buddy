"""Frozen upgrade output and the floor-to-current migration chain."""
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest import mock

from blackboard.store.upgrade_fixture import FIXTURE
from hey_my_buddy.blackboard.store import schema_30_31, upgrade
from hey_my_buddy.blackboard.store.db import Database, SCHEMA_VERSION
from hey_my_buddy.install.upgrade import preflight
from hey_my_buddy.errors import BoardError


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(',', ':')).encode()).hexdigest()


class FrozenUpgradeTests(unittest.TestCase):
    def test_30_to_31_matches_the_complete_pre_freeze_output(self):
        oracle = json.loads((FIXTURE / 'schema-31-oracle.json').read_text())
        sql = (FIXTURE / 'board.sql').read_bytes()
        self.assertEqual(hashlib.sha256(sql).hexdigest(), oracle['fixtureSqlSha256'])
        with closing(sqlite3.connect(':memory:')) as db:
            db.executescript(sql.decode())
            db.row_factory = sqlite3.Row
            with db:
                schema_30_31.upgrade(db)
            schema = [tuple(r) for r in db.execute('SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name')]
            self.assertEqual(digest(schema), oracle['schemaSha256'])
            tables = {}
            for (name,) in db.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"):
                rows = [tuple(r) for r in db.execute(f'SELECT rowid,* FROM "{name}" ORDER BY rowid')]
                tables[name] = {'rows': len(rows), 'sha256': digest(rows)}
            self.assertEqual(tables, oracle['tables'])
            self.assertEqual(db.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0], '31')
            self.assertEqual([r[0] for r in db.execute('PRAGMA integrity_check')], ['ok'])
            self.assertEqual(list(db.execute('PRAGMA foreign_key_check')), [])


def add_schema31_stock(db):
    for sql in (FIXTURE / 'schema-31-stock.sql').read_text().split(';'):
        if sql.strip():
            db.execute(sql)


class UpgradeChainTests(unittest.TestCase):
    def setUp(self):
        self.scratch = tempfile.TemporaryDirectory(prefix='upgrade-chain-')
        self.addCleanup(self.scratch.cleanup)
        self.root = Path(self.scratch.name)
        self.state = self.root / 'state'
        self.state.mkdir()
        self.db = sqlite3.connect(self.state / 'board.sqlite3')
        self.addCleanup(self.db.close)
        self.db.executescript((FIXTURE / 'board.sql').read_text())
        self.db.row_factory = sqlite3.Row

    def schema31(self):
        with self.db:
            schema_30_31.upgrade(self.db)
            add_schema31_stock(self.db)

    def assert_current_structure(self):
        fresh = Database(self.root / 'fresh')
        self.addCleanup(fresh.close)
        fresh.initialize()
        query = 'SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name'
        with fresh.read() as connection:
            self.assertEqual([tuple(r) for r in self.db.execute(query)],
                             [tuple(r) for r in connection.execute(query)])
        self.assertEqual([r[0] for r in self.db.execute('PRAGMA integrity_check')], ['ok'])
        self.assertEqual(list(self.db.execute('PRAGMA foreign_key_check')), [])

    def test_all_dropped_facts_have_floor_stock_and_equal_the_frozen_spec(self):
        rows = list(self.db.execute('SELECT * FROM tasks'))
        self.assertEqual(len(rows), 23)
        self.assertEqual(sum(row['adapter'] == 'decision' for row in rows), 7)
        for row in rows:
            spec = json.loads(row['spec_json'])
            self.assertEqual(json.loads(row['required_capabilities']), spec.get('requiredCapabilities', []))
            self.assertEqual(json.loads(row['exclusive_resources']), spec.get('exclusiveResources', []))
            self.assertEqual(row['expected_seconds'], spec.get('expectedSeconds'))
            self.assertEqual(row['router_budget_seconds'], spec.get('routerBudgetSeconds'))

    def test_floor_runs_every_step_with_schema31_stock_and_matches_a_new_board(self):
        def step_with_new_form(db):
            schema_30_31.upgrade(db)
            add_schema31_stock(db)
        with mock.patch.dict(upgrade.STEPS, {30: step_with_new_form}):
            self.assertEqual(upgrade.upgrade_state(self.state), {'sourceSchema': 30, 'targetSchema': SCHEMA_VERSION})
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM maintenance_runs').fetchone()[0], 1)
        self.assert_current_structure()

    def test_schema31_preserves_every_surviving_value_and_old_maintenance_deadlines(self):
        self.schema31()
        stored = self.db.execute("SELECT spec_json,input_fingerprint FROM tasks WHERE task_id='schema31-maintenance-task'").fetchone()
        self.assertEqual(stored[1], hashlib.sha256(('2:' + stored[0]).encode()).hexdigest())
        removed = {'required_capabilities','exclusive_resources','expected_seconds','router_budget_seconds'}
        columns = ['rowid', *[r[1] for r in self.db.execute('PRAGMA table_info(tasks)') if r[1] not in removed]]
        query = 'SELECT ' + ','.join(columns) + ' FROM tasks ORDER BY rowid'
        before = [tuple(r) for r in self.db.execute(query)]
        self.assertEqual(preflight(self.state, contract_changed=True)['schema'], 31)
        self.assertEqual(upgrade.upgrade_state(self.state), {'sourceSchema': 31, 'targetSchema': SCHEMA_VERSION})
        self.assertEqual([tuple(r) for r in self.db.execute(query)], before)
        self.assertTrue(removed.isdisjoint({r[1] for r in self.db.execute('PRAGMA table_info(tasks)')}))
        self.assertEqual(self.db.execute("SELECT json_extract(spec_json,'$.routerBudgetSeconds'),"
                          "json_extract(spec_json,'$.decision.routerBudgetSeconds'),"
                          "json_extract(spec_json,'$.decision.document.budget.timeoutSeconds')"
                          " FROM tasks WHERE task_id='schema31-maintenance-task'").fetchone()[:], (310,300,300))
        self.assert_current_structure()

    def test_second_step_failure_rolls_back_the_entire_chain_and_marker(self):
        before = list(self.db.iterdump())
        real = upgrade.STEPS[31]
        def failing(db):
            real(db)
            raise RuntimeError('after-task-rebuild')
        with mock.patch.dict(upgrade.STEPS, {31: failing}), self.assertRaisesRegex(RuntimeError, 'after-task-rebuild'):
            upgrade.upgrade_state(self.state)
        self.assertEqual(list(self.db.iterdump()), before)

    def test_foreign_key_check_prevents_committing_a_broken_rebuild(self):
        before = list(self.db.iterdump())
        real = upgrade.STEPS[31]
        def broken(db):
            real(db)
            db.execute("UPDATE attempts SET task_id='missing-parent' WHERE rowid=(SELECT MIN(rowid) FROM attempts)")
        with mock.patch.dict(upgrade.STEPS, {31: broken}), self.assertRaises(BoardError) as caught:
            upgrade.upgrade_state(self.state)
        self.assertEqual(caught.exception.code, 'UPGRADE_INVALID_BOARD')
        self.assertEqual(list(self.db.iterdump()), before)

    def test_below_floor_install_and_upgrade_name_the_required_release_without_writing(self):
        with self.db:
            self.db.execute("UPDATE meta SET value='29' WHERE key='schema_version'")
        before = list(self.db.iterdump())
        for operation in (lambda: preflight(self.state, contract_changed=True),
                          lambda: upgrade.upgrade_state(self.state)):
            with self.subTest(operation=operation), self.assertRaises(BoardError) as caught:
                operation()
            self.assertEqual(caught.exception.code, 'UNSUPPORTED_SCHEMA')
            self.assertIn('0.54.1', str(caught.exception))
            self.assertEqual(list(self.db.iterdump()), before)
