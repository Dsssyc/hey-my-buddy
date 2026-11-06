"""Schema 36 records the exploration fact on routes and Router settings."""
from __future__ import annotations

from pathlib import Path
import sqlite3
import tempfile
import unittest

from blackboard.store.upgrade_fixture import load_fixture
from hey_my_buddy.blackboard.store import schema_30_31, schema_31_32, schema_32_33, schema_33_34, schema_34_35
from hey_my_buddy.blackboard.store.schema_35_36 import upgrade as step_upgrade
from hey_my_buddy.blackboard.store.db import SCHEMA_VERSION
from hey_my_buddy.blackboard.store.upgrade import upgrade_state


class Schema35To36Tests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='schema-36-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def test_floor_stock_gains_the_exploration_fact_and_matches_a_new_board(self):
        fixture = load_fixture(self.root / 'state')
        path = fixture['state'] / 'board.sqlite3'
        with sqlite3.connect(path) as connection:
            connection.row_factory = sqlite3.Row
            self.assertEqual(connection.execute('SELECT COUNT(*) FROM workflow_routes').fetchone()[0], 1)
            self.assertEqual(connection.execute('SELECT COUNT(*) FROM router_settings').fetchone()[0], 1)
            # The floor's own writers cannot state either new form: the stock
            # carries routes and settings that predate exploration.
            self.assertNotIn('exploration',
                             {row['name'] for row in connection.execute('PRAGMA table_info(workflow_routes)')})
            self.assertNotIn('exploration_every',
                             {row['name'] for row in connection.execute('PRAGMA table_info(router_settings)')})
        self.assertEqual(upgrade_state(fixture['state']), {'sourceSchema': 30, 'targetSchema': SCHEMA_VERSION})
        from hey_my_buddy.blackboard.store.db import Database
        fresh = Database(self.root / 'fresh')
        self.addCleanup(fresh.close)
        fresh.initialize()
        query = 'SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name'
        with sqlite3.connect(path) as upgraded, sqlite3.connect(self.root / 'fresh' / 'board.sqlite3') as created:
            self.assertEqual([tuple(row) for row in upgraded.execute(query)],
                             [tuple(row) for row in created.execute(query)])
            self.assertEqual([row[0] for row in upgraded.execute('PRAGMA integrity_check')], ['ok'])
            self.assertEqual(list(upgraded.execute('PRAGMA foreign_key_check')), [])
            self.assertEqual([row[0] for row in upgraded.execute('SELECT exploration FROM workflow_routes')],
                             ['pre-exploration'])
            self.assertEqual(upgraded.execute('SELECT exploration_every FROM router_settings').fetchone()[0], 5)

    def test_step_converts_only_its_own_forms_from_a_schema_35_board(self):
        fixture = load_fixture(self.root / 'state')
        path = fixture['state'] / 'board.sqlite3'
        with sqlite3.connect(path) as connection:
            connection.row_factory = sqlite3.Row
            with connection:
                for step in (schema_30_31, schema_31_32, schema_32_33, schema_33_34, schema_34_35):
                    step.upgrade(connection)
            routes = [tuple(row) for row in connection.execute(
                'SELECT rowid,decision_id,run_id,state,created_at FROM workflow_routes ORDER BY rowid')]
            settings = tuple(connection.execute(
                'SELECT id,default_mode,budget_preset,retry_interval_seconds,maintenance_buddy_id,'
                'maintenance_interval_seconds FROM router_settings').fetchone())
            step_upgrade(connection)
        with sqlite3.connect(path) as connection:
            connection.row_factory = sqlite3.Row
            self.assertEqual([tuple(row) for row in connection.execute(
                'SELECT rowid,decision_id,run_id,state,created_at FROM workflow_routes ORDER BY rowid')], routes)
            self.assertEqual(tuple(connection.execute(
                'SELECT id,default_mode,budget_preset,retry_interval_seconds,maintenance_buddy_id,'
                'maintenance_interval_seconds FROM router_settings').fetchone()), settings)
            self.assertEqual([row[0] for row in connection.execute('SELECT exploration FROM workflow_routes')],
                             ['pre-exploration'])
            self.assertEqual(connection.execute(
                "SELECT value FROM meta WHERE key='schema_version'").fetchone()[0], '36')

    def test_the_new_forms_have_database_fences(self):
        fixture = load_fixture(self.root / 'state')
        upgrade_state(fixture['state'])
        with sqlite3.connect(fixture['state'] / 'board.sqlite3') as connection:
            for value in ('counted', 'quota-kept', 'quota-reselected', 'ineligible', 'pre-exploration'):
                connection.execute('UPDATE workflow_routes SET exploration=?', (value,))
                connection.rollback()
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute("UPDATE workflow_routes SET exploration='explored'")
            connection.rollback()
            connection.execute('UPDATE router_settings SET exploration_every=NULL')
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute('UPDATE router_settings SET exploration_every=1')
            connection.rollback()
            # NULL is the recorded form of exploration off.
            connection.execute('UPDATE router_settings SET exploration_every=NULL')
            connection.rollback()
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute('UPDATE workflow_routes SET exploration=NULL')
            connection.rollback()

    def test_the_exploration_counter_reads_the_index_not_the_whole_history(self):
        from hey_my_buddy.blackboard.routing.decision import COUNTED_SINCE_LAST_QUOTA_SQL
        fixture = load_fixture(self.root / 'state')
        upgrade_state(fixture['state'])
        with sqlite3.connect(fixture['state'] / 'board.sqlite3') as connection:
            # Foreign keys stay off in this raw connection: the synthetic rows
            # only exercise the counter's index and result, no referenced fact.
            connection.executemany(
                "INSERT INTO workflow_routes(decision_id,run_id,state,exploration,created_at)"
                " VALUES(?,?,?,?, '2026-01-01T00:00:00.000Z')",
                [('counted-early', 'r', 'resolved', 'counted'),
                 ('quota-kept-early', 'r', 'resolved', 'quota-kept'),
                 ('counted-middle', 'r', 'resolved', 'counted'),
                 ('quota-reselected-late', 'r', 'resolved', 'quota-reselected'),
                 ('counted-late', 'r', 'resolved', 'counted'),
                 ('counted-latest', 'r', 'resolved', 'counted'),
                 ('pre-exploration-late', 'r', 'resolved', 'pre-exploration')])
            # Two counted routes follow the last quota-carrying one.
            self.assertEqual(connection.execute(COUNTED_SINCE_LAST_QUOTA_SQL).fetchone()[0], 2)
            # Every step of the counter — the outer count and both scalar
            # subqueries that find the last quota-carrying route — seeks the
            # covering exploration index instead of scanning the history.
            plan = [row[3] for row in connection.execute('EXPLAIN QUERY PLAN ' + COUNTED_SINCE_LAST_QUOTA_SQL)]
            steps = [step for step in plan if not step.startswith('SCALAR SUBQUERY')]
            self.assertEqual(len(steps), 3, plan)
            for step in steps:
                self.assertIn('USING COVERING INDEX workflow_routes_exploration_idx', step, plan)


if __name__ == '__main__':
    unittest.main()
