"""Frozen upgrade output and the floor-to-current migration chain."""
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest import mock

from blackboard.store.upgrade_fixture import FIXTURE, complete_fixture_cleanup
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

    def test_process_identity_stock_preserves_root_identity_and_rowids(self):
        rows = list(self.db.execute('SELECT * FROM attempt_process_identities'))
        self.assertEqual(len(rows), 2)
        self.assertTrue(all(row['process_group_id'] == row['root_pid'] for row in rows))
        self.schema31()
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM attempt_process_identities WHERE boot_id IS NULL AND job_name IS NOT NULL').fetchone()[0], 1)
        query = 'SELECT rowid,attempt_id,boot_id,root_pid,root_started_at,job_name FROM attempt_process_identities ORDER BY rowid'
        before = [tuple(row) for row in self.db.execute(query)]
        upgrade.upgrade_state(self.state)
        self.assertEqual([tuple(row) for row in self.db.execute(query)], before)
        self.assertNotIn('process_group_id', {row[1] for row in self.db.execute('PRAGMA table_info(attempt_process_identities)')})
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.execute('UPDATE attempt_process_identities SET boot_id=NULL')
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.execute("UPDATE attempt_process_identities SET job_name='unexpected-job' WHERE boot_id IS NOT NULL")
        self.assert_current_structure()

    def test_result_stock_drops_read_counters_and_budget_copies_and_keeps_frozen_content(self):
        self.schema31()
        frozen = {table: [tuple(row) for row in self.db.execute(f'SELECT rowid,* FROM {table} ORDER BY rowid')]
                  for table in ('router_requests', 'workflow_turns', 'attempt_received_receipts')}
        specs = list(self.db.execute('SELECT task_id,spec_json,input_fingerprint FROM tasks ORDER BY rowid'))
        sources = (('attempts', 'attempt_id', 'result_json', ('result', 'usage')),
                   ('decision_requests', 'decision_id', 'output_json', ('usage',)))
        expected = {}
        unchanged = {}
        for table, key, column, path in sources:
            rows = list(self.db.execute(f'SELECT {key},{column} FROM {table} WHERE {column} IS NOT NULL'))
            self.assertEqual(self.db.execute(f"SELECT COUNT(*) FROM {table},json_tree({column}) WHERE json_tree.key='bytesRead'").fetchone()[0], 2)
            expected[table], unchanged[table] = {}, {}
            for row in rows:
                value = json.loads(row[column])
                usage = value
                for field in path:
                    usage = usage.get(field, {})
                result = value['result'] if table == 'attempts' else value
                budget = result.pop('budget', None)
                if 'bytesRead' in usage:
                    self.assertIsNone(usage.pop('bytesRead'))
                    self.assertEqual(budget['bytesRead'], 2097152)
                elif budget is None:
                    unchanged[table][row[key]] = row[column]
                expected[table][row[key]] = value
        upgrade.upgrade_state(self.state)
        for table, key, column, _path in sources:
            actual = dict(self.db.execute(f'SELECT {key},{column} FROM {table} WHERE {column} IS NOT NULL'))
            self.assertEqual({key: json.loads(value) for key, value in actual.items()}, expected[table])
            self.assertEqual({key: actual[key] for key in unchanged[table]}, unchanged[table])
            self.assertEqual(self.db.execute(f"SELECT COUNT(*) FROM {table},json_tree({column}) WHERE json_tree.key='bytesRead'").fetchone()[0], 0)
            budget_path = '$.result.budget' if table == 'attempts' else '$.budget'
            self.assertEqual(self.db.execute(f"SELECT COUNT(*) FROM {table} WHERE json_type({column},?) IS NOT NULL",
                                             (budget_path,)).fetchone()[0], 0)
        for table, rows in frozen.items():
            self.assertEqual([tuple(row) for row in self.db.execute(f'SELECT rowid,* FROM {table} ORDER BY rowid')], rows)
        self.assertEqual(list(self.db.execute('SELECT task_id,spec_json,input_fingerprint FROM tasks ORDER BY rowid')), specs)
        self.assertGreater(self.db.execute("SELECT COUNT(*) FROM router_requests,json_tree(snapshot_json) WHERE json_tree.key='bytesRead'").fetchone()[0], 0)
        self.assertEqual(self.db.execute("SELECT json_extract(spec_json,'$.decision.document.budget.bytesRead') FROM tasks WHERE task_id='schema31-maintenance-task'").fetchone()[0], 524288)

    def test_budget_copies_are_removed_even_without_retired_read_counters(self):
        self.schema31()
        for table, column, usage_path, budget_path in (
                ('attempts', 'result_json', '$.result.usage.bytesRead', '$.result.budget'),
                ('decision_requests', 'output_json', '$.usage.bytesRead', '$.budget')):
            self.db.execute(f'UPDATE {table} SET {column}=json_remove({column},?,?)',
                            (usage_path, budget_path + '.bytesRead'))
            self.assertGreater(self.db.execute(f'SELECT COUNT(*) FROM {table} WHERE json_type({column},?) IS NOT NULL',
                                               (budget_path,)).fetchone()[0], 0)
        self.db.commit()
        upgrade.upgrade_state(self.state)
        for table, column, path in (('attempts', 'result_json', '$.result.budget'),
                                    ('decision_requests', 'output_json', '$.budget')):
            self.assertEqual(self.db.execute(f'SELECT COUNT(*) FROM {table} WHERE json_type({column},?) IS NOT NULL',
                                             (path,)).fetchone()[0], 0)

    def test_catalog_observation_stock_keeps_only_observation_facts(self):
        self.schema31()
        query = 'SELECT rowid,observation_id,request_id,response_json,harnesses_json FROM catalog_observations ORDER BY rowid'
        before = [tuple(row) for row in self.db.execute(query)]
        self.assertEqual(len(before), 4)
        expected = {}
        completed = 0
        for row in before:
            if row[3] is None:
                expected[row[0]] = None
                continue
            value = json.loads(row[3])
            expected[row[0]] = {'discoveryId': value['discoveryId'], 'observationId': value['observationId'],
                                'duplicate': value['duplicate'], 'discoveredAt': value['catalog']['discoveredAt'],
                                'appliedHarnesses': value['appliedHarnesses'], 'staleHarnesses': value['staleHarnesses']}
            completed += 1
        self.assertEqual(completed, 3)
        retained = {table: [tuple(row) for row in self.db.execute(f'SELECT rowid,* FROM {table} ORDER BY rowid')]
                    for table in ('evaluation_catalog', 'catalog_current', 'catalog_pending')}
        upgrade.upgrade_state(self.state)
        after = [tuple(row) for row in self.db.execute(query)]
        self.assertEqual([row[:3] + row[4:] for row in after], [row[:3] + row[4:] for row in before])
        self.assertEqual({row[0]: json.loads(row[3]) if row[3] is not None else None for row in after}, expected)
        for table, rows in retained.items():
            self.assertEqual([tuple(row) for row in self.db.execute(f'SELECT rowid,* FROM {table} ORDER BY rowid')], rows)

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
        complete_fixture_cleanup(self.db)
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
