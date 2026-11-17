"""Schema 36 records route exploration and accepted-delivery defect facts."""
from __future__ import annotations

from pathlib import Path
import sqlite3
import tempfile
import unittest
from collections import Counter

from blackboard.store.upgrade_fixture import load_fixture, load_issue_stock
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
            self.assertEqual(connection.execute('SELECT COUNT(*) FROM workflow_fixes').fetchone()[0], 2)
            # The floor's own writers cannot state either new form: the stock
            # carries routes and settings that predate exploration.
            self.assertNotIn('exploration',
                             {row['name'] for row in connection.execute('PRAGMA table_info(workflow_routes)')})
            self.assertNotIn('exploration_every',
                             {row['name'] for row in connection.execute('PRAGMA table_info(router_settings)')})
            self.assertNotIn('defects',
                             {row['name'] for row in connection.execute('PRAGMA table_info(kind_statistics)')})
            expected = self.base_writer_defect_counts(connection)
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
            actual = {tuple(row[:3]): row[3] for row in upgraded.execute(
                'SELECT k.harness,m.display_name,k.effort,k.defects FROM kind_statistics k'
                ' JOIN models m ON m.model_id=k.model_id WHERE k.defects > 0')}
            self.assertEqual(actual, dict(expected))

    @staticmethod
    def base_writer_defect_counts(connection):
        """Use the frozen floor statistics writer to identify successful target samples."""
        from hey_my_buddy.blackboard.store.schema_30_31 import samples_for_run
        expected = Counter()
        fixed_runs = {row[0] for row in connection.execute(
            'SELECT DISTINCT fixed_run_id FROM workflow_fixes')}
        for run_id in fixed_runs:
            samples = samples_for_run(connection, run_id)
            successful = [(kind, sample) for kind, sample in samples.items() if sample['success']]
            if len(successful) != 1:
                raise AssertionError(f'Expected one successful base-writer sample for {run_id}: {successful}')
            expected[successful[0][0]] += 1
        return expected

    def test_step_converts_only_its_own_forms_from_a_schema_35_board(self):
        fixture = load_fixture(self.root / 'state')
        path = fixture['state'] / 'board.sqlite3'
        with sqlite3.connect(path) as connection:
            connection.row_factory = sqlite3.Row
            with connection:
                for step in (schema_30_31, schema_31_32, schema_32_33, schema_33_34, schema_34_35):
                    step.upgrade(connection)
            expected = self.base_writer_defect_counts(connection)
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
            actual = {tuple(row[:3]): row[3] for row in connection.execute(
                'SELECT harness,model,effort,defects FROM kind_statistics WHERE defects > 0')}
            self.assertEqual(actual, dict(expected))

    def test_step_discards_only_issues_cleared_by_the_matching_event(self):
        fixture = load_fixture(self.root / 'issue-state')
        load_issue_stock(fixture)
        with sqlite3.connect(fixture['state'] / 'board.sqlite3') as connection:
            connection.row_factory = sqlite3.Row
            for step in (schema_30_31, schema_31_32, schema_32_33, schema_33_34, schema_34_35):
                step.upgrade(connection)
            connection.commit()
            stale = dict(connection.execute("SELECT * FROM execution_issues WHERE stage='connection'").fetchone())
            report = connection.execute(
                "SELECT MAX(seq) FROM events WHERE kind='execution.issue'"
                " AND json_extract(payload_json,'$.stage')='connection'").fetchone()[0]
            cleared = connection.execute(
                "SELECT MAX(seq) FROM events WHERE kind='execution.shutdown_confirmed'").fetchone()[0]
            self.assertGreater(cleared, report)
            for stage, event, removed in (
                ('connection', 'execution.shutdown_confirmed', True),
                ('connection', 'execution.received', True),
                ('shutdown', 'execution.shutdown_confirmed', True),
                ('recovery', 'execution.shutdown_confirmed', True),
                ('native', 'execution.issue_resolved', True),
                ('shutdown', 'execution.received', False),
                ('recovery', 'execution.received', False),
                ('native', 'execution.shutdown_confirmed', False),
            ):
                with self.subTest(stage=stage, event=event):
                    connection.execute('SAVEPOINT issue_case')
                    connection.execute("UPDATE execution_issues SET stage=? WHERE stage='connection'", (stage,))
                    connection.execute("UPDATE events SET payload_json=json_set(payload_json,'$.stage',?) WHERE seq=?",
                                       (stage, report))
                    connection.execute("UPDATE events SET kind=?,payload_json=json_object('stage',?) WHERE seq=?",
                                       (event, stage, cleared))
                    step_upgrade(connection)
                    residual = connection.execute('SELECT * FROM execution_issues WHERE stage=?', (stage,)).fetchone()
                    if removed:
                        self.assertIsNone(residual)
                    else:
                        self.assertEqual(dict(residual), {**stale, 'stage': stage, 'occurrence_seq': report})
                    self.assertIsNotNone(connection.execute(
                        "SELECT * FROM execution_issues WHERE stage='upgrade-stock'").fetchone())
                    connection.execute('ROLLBACK TO issue_case')
                    connection.execute('RELEASE issue_case')

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
