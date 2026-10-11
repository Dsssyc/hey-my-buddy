"""The portable old board also covers columns appended by earlier releases."""
from pathlib import Path
import re
import sqlite3
from unittest import mock

from support import BoardTestCase
from blackboard.store.test_migrations import (
    RebuildDamageConnection, assert_rebuild_damage_rejected, rows, schema_shape, seed_private_rebuild_values,
)
from blackboard.store.upgrade_fixture import load_fixture
from hey_my_buddy.blackboard.store import backup, migrations
from hey_my_buddy.blackboard.store.db import Database
from hey_my_buddy.blackboard.store.store import BoardStore
from hey_my_buddy.errors import BoardError
from hey_my_buddy.install import upgrade


APPENDED_COLUMNS = {
    'attempts': ('model_adapter', 'model_provider', 'model_model', 'token_usage_json'),
    'workflow_runs': ('objective_id', 'title', 'activity_seq', 'activity_at', 'configuration_locked'),
    'evaluation_cards': ('origin',),
}
MOVED_INDEXES = ('attempts_model_family_idx', 'workflow_runs_activity_idx',
                 'workflow_runs_objective_idx', 'workflow_runs_state_idx')


def named_rows(connection, table):
    columns = sorted(row[1] for row in connection.execute(f'PRAGMA table_info({table})'))
    names = ', '.join(columns)
    return columns, [tuple(row) for row in connection.execute(f'SELECT {names} FROM {table} ORDER BY {names}')]


def foreign_keys(connection):
    tables = [row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")]
    return {table: sorted(tuple(row) for row in connection.execute(f'PRAGMA foreign_key_list({table})'))
            for table in tables}


def append_old_columns(connection):
    """Reorder the loaded fixture, keeping its declarations, indexes and rows.

    This only prepares test input; neither the fixed stock nor its generator is
    changed. The base used one declaration per line, with CHECK continuations.
    """
    connection.execute('PRAGMA foreign_keys=OFF')
    connection.execute('BEGIN IMMEDIATE')
    try:
        for table, appended in APPENDED_COLUMNS.items():
            sql = connection.execute('SELECT sql FROM sqlite_master WHERE type=\'table\' AND name=?',
                                     (table,)).fetchone()[0]
            sql = re.sub(r'    --[^\n]*\n', '', sql)
            indexes = [row[0] for row in connection.execute(
                'SELECT sql FROM sqlite_master WHERE type=\'index\' AND tbl_name=? AND sql IS NOT NULL', (table,))]
            declarations = re.split(r',\n(?=    (?:[a-z_]|UNIQUE))', sql.partition('(\n')[2].rsplit('\n)', 1)[0])
            by_name = {declaration.split()[0]: declaration for declaration in declarations}
            columns = [row[1] for row in connection.execute(f'PRAGMA table_info({table})')]
            order = [name for name in columns if name not in appended] + list(appended)
            constraints = [declaration for declaration in declarations if declaration.split()[0] not in columns]
            body = ',\n'.join([by_name[name] for name in order] + constraints)
            connection.execute(f'CREATE TABLE {table}_appended (\n{body}\n)')
            names = ', '.join(columns)
            connection.execute(f'INSERT INTO {table}_appended ({names}) SELECT {names} FROM {table}')
            connection.execute(f'DROP TABLE {table}')
            connection.execute(f'ALTER TABLE {table}_appended RENAME TO {table}')
            for index in indexes:
                connection.execute(index)
        connection.execute('COMMIT')
    except BaseException:
        connection.execute('ROLLBACK')
        raise


class UpgradeColumnOrderTests(BoardTestCase):
    def setUp(self):
        super().setUp()
        self.fresh = Database(self.directory / 'fresh')
        self.fresh.initialize()
        with self.fresh.read() as connection:
            self.fresh_shape = schema_shape(connection)

    def prepare(self, name, *, factory=sqlite3.Connection):
        state = self.directory / name
        load_fixture(state)
        connection = sqlite3.connect(state / 'board.sqlite3', isolation_level=None, factory=factory)
        self.addCleanup(connection.close)
        seed_private_rebuild_values(connection)
        before = {table: named_rows(connection, table) for table in APPENDED_COLUMNS}
        self.assertEqual({table: len(value[0]) for table, value in before.items()},
                         {'attempts': 32, 'workflow_runs': 31, 'evaluation_cards': 10})
        references = foreign_keys(connection)
        append_old_columns(connection)
        self.assertEqual(references, foreign_keys(connection))
        self.assertEqual(connection.execute('PRAGMA foreign_key_check').fetchall(), [])
        for table, appended in APPENDED_COLUMNS.items():
            columns = [row[1] for row in connection.execute(f'PRAGMA table_info({table})')]
            self.assertEqual(columns[-len(appended):], list(appended))
            self.assertNotEqual(columns, [row[1] for row in self.fresh_shape[table]['columns']])
            self.assertEqual(before[table], named_rows(connection, table))
            self.assertTrue(before[table][1], table)
        for index in MOVED_INDEXES:
            mechanics = [tuple(row) for row in connection.execute(f'PRAGMA index_xinfo({index})')]
            self.assertNotEqual([row[1] for row in mechanics], [row[1] for row in self.fresh_shape[index]], index)
        # Check the precise historical positions, not just any different order.
        attempts = [row[1] for row in connection.execute('PRAGMA table_info(attempts)')]
        self.assertEqual(attempts[attempts.index('revision') + 1:], list(APPENDED_COLUMNS['attempts']))
        runs = [row[1] for row in connection.execute('PRAGMA table_info(workflow_runs)')]
        self.assertEqual(runs[runs.index('updated_at') + 1:], list(APPENDED_COLUMNS['workflow_runs']))
        self.assertTrue(connection.execute('SELECT COUNT(*) FROM workflow_turns WHERE attempt_id IS NOT NULL').fetchone()[0])
        self.assertTrue(connection.execute('SELECT COUNT(*) FROM workflow_artifacts WHERE run_id IS NOT NULL').fetchone()[0])
        self.assertEqual(connection.execute('SELECT COUNT(*) FROM workflow_runs r JOIN workflow_routes w '
                                            'ON r.current_routing_id=w.decision_id AND r.run_id=w.run_id').fetchone()[0],
                         len(before['workflow_runs'][1]))
        return state, connection

    def assert_preserved(self, connection, before):
        after = rows(connection)
        for table, values in before.items():
            if table in APPENDED_COLUMNS:
                self.assertEqual(values, named_rows(connection, table), table)
            elif table not in ('meta', 'catalog_current'):
                self.assertEqual(values, after[table], table)

    def preservation_baseline(self, connection):
        before = rows(connection)
        before.update({table: named_rows(connection, table) for table in APPENDED_COLUMNS})
        return before

    def test_appended_columns_migrate_to_complete_fresh_schema_and_preserve_named_values(self):
        for setting in (0, 1):
            with self.subTest(foreign_keys=setting):
                _state, connection = self.prepare(f'success-{setting}')
                before = self.preservation_baseline(connection)
                references = foreign_keys(connection)
                connection.execute(f'PRAGMA foreign_keys={setting}')
                statements = []
                connection.set_trace_callback(statements.append)
                try:
                    migrations.migrate_15_to_16(connection)
                except BoardError as error:
                    self.fail(f'Valid private input must migrate with every value preserved: {error.payload()}')
                self.assertEqual(schema_shape(connection), self.fresh_shape)
                self.assert_preserved(connection, before)
                current_references = foreign_keys(connection)
                for table, value in references.items():
                    self.assertEqual(current_references[table], value, table)
                self.assertEqual(connection.execute('PRAGMA foreign_key_check').fetchall(), [])
                self.assertEqual(connection.execute('PRAGMA foreign_keys').fetchone()[0], setting)
                self.assertFalse(connection.in_transaction)
                self.assertLess(statements.index('PRAGMA foreign_keys=OFF'), statements.index('BEGIN IMMEDIATE'))
                self.assertLess(statements.index('PRAGMA foreign_key_check'), statements.index('COMMIT'))
                self.assertLess(statements.index('COMMIT'), statements.index(f'PRAGMA foreign_keys={setting}'))
                for table in APPENDED_COLUMNS:
                    self.assertIn(f'ALTER TABLE {table}_16 RENAME TO {table}', statements)
                    self.assertFalse(any(sql.startswith(f'ALTER TABLE {table} RENAME') for sql in statements))

    def test_verified_backup_and_real_upgrade_normalize_appended_columns(self):
        state, connection = self.prepare('coordinator')
        before = self.preservation_baseline(connection)
        current = backup.create(BoardStore(state))
        verified = backup.verify(Path(current['path']))
        self.assertEqual(migrations.schema_version(connection), 15)
        summary, expected = upgrade.migrate_board(state, {**verified['databaseSnapshot'], 'schema':verified['schema']})
        self.assertEqual(summary['toSchema'], 16)
        self.assertEqual(expected, {**upgrade.idle_snapshot(state), 'runtimeSettings':{}})
        self.assertEqual(schema_shape(connection), self.fresh_shape)
        self.assert_preserved(connection, before)

    def test_late_sql_failure_restores_appended_schema_references_rows_and_foreign_key_setting(self):
        for setting in (0, 1):
            with self.subTest(foreign_keys=setting):
                _state, connection = self.prepare(f'rollback-{setting}')
                before, shape, references = rows(connection), schema_shape(connection), foreign_keys(connection)
                connection.execute(f'PRAGMA foreign_keys={setting}')
                with mock.patch.object(migrations, '_CONCLUSION_INDEX', 'CREATE INDEX broken ON missing(value)'):
                    with self.assertRaises(BoardError) as caught:
                        migrations.migrate_15_to_16(connection)
                self.assertEqual(caught.exception.details['tables'], ['workflow_host_conclusions'])
                self.assertEqual(rows(connection), before)
                self.assertEqual(schema_shape(connection), shape)
                self.assertEqual(foreign_keys(connection), references)
                self.assertEqual(connection.execute('PRAGMA foreign_key_check').fetchall(), [])
                self.assertEqual(connection.execute('PRAGMA foreign_keys').fetchone()[0], setting)
                self.assertFalse(connection.in_transaction)

    def test_foreign_key_check_rejects_invalid_reference_before_commit_and_restores_setting(self):
        _state, connection = self.prepare('foreign-key-failure')
        # A stock violation must be caught after rebuilding, even with enforcement
        # disabled for the transaction. It is not an allowed retained-value change.
        connection.execute("UPDATE workflow_artifacts SET run_id='missing-run' WHERE artifact_id="
                           "(SELECT artifact_id FROM workflow_artifacts LIMIT 1)")
        before, shape, references = rows(connection), schema_shape(connection), foreign_keys(connection)
        connection.execute('PRAGMA foreign_keys=ON')
        with self.assertRaises(BoardError) as caught:
            migrations.migrate_15_to_16(connection)
        self.assertEqual(caught.exception.message, 'Foreign key check failed after migration')
        self.assertEqual(caught.exception.details['tables'], ['workflow_artifacts'])
        self.assertEqual(rows(connection), before)
        self.assertEqual(schema_shape(connection), shape)
        self.assertEqual(foreign_keys(connection), references)
        self.assertEqual(connection.execute('PRAGMA foreign_keys').fetchone()[0], 1)
        self.assertFalse(connection.in_transaction)

    def test_appended_table_value_damage_reaches_fingerprint_comparison_and_rolls_back(self):
        for table in APPENDED_COLUMNS:
            with self.subTest(table=table):
                _state, connection = self.prepare(f'retained-value-{table}', factory=RebuildDamageConnection)
                connection.execute('PRAGMA foreign_keys=ON')
                assert_rebuild_damage_rejected(self, connection, table)
                self.assertEqual(connection.execute('PRAGMA foreign_keys').fetchone()[0], 1)
                self.assertEqual(connection.execute('PRAGMA foreign_key_check').fetchall(), [])
