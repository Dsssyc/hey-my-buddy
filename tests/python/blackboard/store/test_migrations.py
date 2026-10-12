"""The public-operation schema-16 board guards the unique 16→17 retirement."""
from __future__ import annotations

import json
from pathlib import Path
import sqlite3
import re
from unittest import mock

from support import BoardTestCase
from blackboard.store.upgrade_fixture import FIXTURE, load_fixture
from hey_my_buddy.blackboard.store import migrations
from hey_my_buddy.blackboard.store.db import Database, SCHEMA_VERSION, Corruption
from hey_my_buddy.errors import BoardError

FIXTURE_ROOT = FIXTURE
_METADATA = json.loads((FIXTURE / 'metadata.json').read_text())
DELIVERED = _METADATA['stableIds']['delivered']
CANCELLED = _METADATA['stableIds']['cancelled']

def normalized_sql(sql):
    if sql is None:
        return None
    sql = re.sub(r'--[^\n]*', '', sql)
    sql = re.sub(r'\bIF NOT EXISTS\s+', '', sql, flags=re.I)
    sql = re.sub(r'"([A-Za-z_][A-Za-z0-9_]*)"', r'\1', sql)
    return re.sub(r'\s*([(),;])\s*', r'\1', ' '.join(sql.split())).rstrip(';')


def schema_shape(connection) -> dict:
    """Definitions plus column, FK and index mechanics, independent of row values."""
    objects = [tuple(row) for row in connection.execute(
        'SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name')]
    shape = {'master': [(kind, name, table, normalized_sql(sql)) for kind, name, table, sql in objects]}
    for kind, name, _table, _sql in objects:
        if kind in ('table', 'view'):
            shape[name] = {
                'columns': [tuple(row) for row in connection.execute(f'PRAGMA table_xinfo("{name}")')],
                'foreignKeys': sorted(tuple(row) for row in connection.execute(f'PRAGMA foreign_key_list("{name}")')),
                'indexes': sorted(tuple(row)[1:] for row in connection.execute(f'PRAGMA index_list("{name}")')),
            }
        elif kind == 'index':
            shape[name] = [tuple(row) for row in connection.execute(f'PRAGMA index_xinfo("{name}")')]
    shape['versionMeta'] = [tuple(row) for row in connection.execute('SELECT key,value FROM meta ORDER BY key')
                            if row[0].endswith('_version')]
    return shape


def rows(connection) -> dict:
    names = [row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")]
    return {name: sorted([tuple(row) for row in connection.execute(f'SELECT * FROM "{name}"')], key=repr)
            for name in names}


class Schema16UpgradeTests(BoardTestCase):
    def setUp(self):
        super().setUp()
        self.state = self.directory / 'base'
        self.fixture = load_fixture(self.state)
        self.connection = sqlite3.connect(self.state / 'board.sqlite3', isolation_level=None)
        self.addCleanup(self.connection.close)

    def test_migration_has_the_complete_fresh_schema_and_empty_new_tables(self):
        summary = migrations.migrate_16_to_17(self.connection)
        fresh = Database(self.directory / 'fresh')
        fresh.initialize()
        with fresh.read() as connection:
            self.assertEqual(schema_shape(self.connection), schema_shape(connection))
        self.assertEqual(summary['fromSchema'], 16)
        self.assertEqual(summary['toSchema'], 17)
        self.assertEqual(summary['newTables'], sorted(migrations.NEW_TABLES))
        for table in migrations.NEW_TABLES:
            self.assertEqual(self.connection.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0], 0)
        self.assertNotIn('quota_json', [row[1] for row in self.connection.execute('PRAGMA table_info(harness_health)')])
        quota_columns = {row[1] for row in self.connection.execute('PRAGMA table_info(quota_current)')}
        self.assertFalse({'available', 'retry_basis'} & quota_columns)
        self.assertEqual(
            {row[1] for row in self.connection.execute('PRAGMA table_info(harness_quota_observations)')},
            {'adapter', 'quota_json'},
        )

    def test_all_retired_stock_is_deleted_and_every_historical_value_is_preserved(self):
        old = rows(self.connection)
        keys = dict(old['meta'])
        for prefix in migrations.RETIRED_META_PREFIXES:
            self.assertTrue(any(key.startswith(prefix) for key in keys), prefix)
        migrations.migrate_16_to_17(self.connection)
        actual = rows(self.connection)
        for table, values in old.items():
            if table not in ('meta', 'catalog_current', 'catalog_pending', 'harness_health', 'evaluation_profiles'):
                self.assertEqual(actual[table], values, table)
        planned = {key: value for key, value in keys.items() if not migrations.retired_meta(key)}
        planned['schema_version'] = '17'
        self.assertEqual(dict(actual['meta']), planned)
        self.assertEqual(actual['catalog_pending'], [])
        self.assertTrue(old['catalog_pending'])
        profile_columns = [row[1] for row in self.connection.execute('PRAGMA table_info(evaluation_profiles)')]
        expected_profiles = [tuple(0 if column == 'available' else 'ACCOUNT_STATUS_UNKNOWN' if column == 'unavailable_reason' else value
                                   for column, value in zip(profile_columns, row))
                             for row in old['evaluation_profiles']]
        self.assertEqual(actual['evaluation_profiles'], expected_profiles)
        for row in self.connection.execute('SELECT * FROM catalog_current'):
            self.assertEqual(row[1:5], (0, None, 'unknown', 'ACCOUNT_STATUS_UNKNOWN'))
        for row in self.connection.execute('SELECT * FROM harness_health'):
            original = next(item for item in old['harness_health'] if item[0] == row[0])
            self.assertEqual(row[:3], original[:3])
            self.assertEqual(row[3:], ('unknown', '{}', None, None, None))

    def test_health_recovery_cannot_route_old_profiles_without_a_new_trusted_catalog(self):
        from hey_my_buddy.blackboard.catalog.catalog import restore_retained_availability
        from hey_my_buddy.blackboard.routing.decision import DecisionCoordinator
        from hey_my_buddy.blackboard.service.harness_health import read_health

        self.connection.row_factory = sqlite3.Row
        self.assertTrue(self.connection.execute('SELECT COUNT(*) FROM evaluation_profiles WHERE available=1 AND enabled=1').fetchone()[0])
        migrations.migrate_16_to_17(self.connection)
        self.connection.execute("UPDATE harness_health SET status='ready' WHERE adapter='codex'")
        self.assertTrue(read_health(self.connection, 'codex')['available'])
        self.assertEqual(restore_retained_availability(self.connection, 'codex'), 0)
        candidates, retried = DecisionCoordinator._scan_candidates(self.connection, [])
        self.assertEqual((candidates, retried), ([], []))
        self.assertEqual(self.connection.execute("SELECT status,discovery_id FROM catalog_current WHERE adapter='codex'").fetchone()[:], ('unknown', None))
        self.assertEqual(self.connection.execute('SELECT COUNT(*) FROM evaluation_profiles WHERE available!=0').fetchone()[0], 0)
        self.assertEqual(self.connection.execute("SELECT COUNT(*) FROM evaluation_profiles WHERE unavailable_reason!='ACCOUNT_STATUS_UNKNOWN'").fetchone()[0], 0)

    def test_retired_read_alias_without_a_base_writer_is_deleted(self):
        # The released base never writes this old read alias; synthesize only
        # this boundary input, leaving the public-operation fixture untouched.
        self.connection.execute("INSERT INTO meta(key,value) VALUES('codex_account_read_at','old')")
        migrations.migrate_16_to_17(self.connection)
        self.assertIsNone(self.connection.execute("SELECT key FROM meta WHERE key='codex_account_read_at'").fetchone())

    def test_account_status_never_blocks_sql_retirement_or_claims_shutdown(self):
        for status in ('active', 'uncertain'):
            with self.subTest(status=status):
                state = self.directory / f'status-{status}'
                load_fixture(state)
                connection = sqlite3.connect(state / 'board.sqlite3', isolation_level=None)
                self.addCleanup(connection.close)
                connection.execute("UPDATE meta SET value=json_set(value,'$.status',?) WHERE key LIKE 'account-operation:%' OR key LIKE 'account-mutation:%'", (status,))
                before = rows(connection)
                migrations.migrate_16_to_17(connection)
                self.assertEqual(rows(connection)['attempts'], before['attempts'])
                self.assertEqual(rows(connection)['events'], before['events'])

    def test_foreign_key_setting_is_restored_after_success_and_rollback(self):
        for setting in (0, 1):
            for fail in (False, True):
                with self.subTest(foreign_keys=setting, fail=fail):
                    state = self.directory / f'fk-{setting}-{fail}'
                    load_fixture(state)
                    connection = sqlite3.connect(state / 'board.sqlite3', isolation_level=None)
                    self.addCleanup(connection.close)
                    connection.execute(f'PRAGMA foreign_keys={setting}')
                    before = rows(connection)
                    trace = []
                    connection.set_trace_callback(trace.append)
                    if fail:
                        with mock.patch.object(migrations, '_NEW_DDL', migrations._NEW_DDL + (('quota_current', 'CREATE INDEX broken ON absent(value)'),)):
                            with self.assertRaises(BoardError):
                                migrations.migrate_16_to_17(connection)
                        self.assertEqual(rows(connection), before)
                    else:
                        migrations.migrate_16_to_17(connection)
                    self.assertFalse(connection.in_transaction)
                    self.assertEqual(connection.execute('PRAGMA foreign_keys').fetchone()[0], setting)
                    self.assertLess(trace.index('PRAGMA foreign_keys=OFF'), trace.index('BEGIN IMMEDIATE'))
                    self.assertLess(trace.index('ROLLBACK' if fail else 'COMMIT'), trace.index(f'PRAGMA foreign_keys={setting}'))

    def test_failed_transaction_begin_restores_foreign_key_setting(self):
        class FailingBeginConnection(sqlite3.Connection):
            def execute(self, statement, *args):
                if statement == 'BEGIN IMMEDIATE':
                    raise sqlite3.OperationalError('private injected begin failure')
                return super().execute(statement, *args)

        connection = sqlite3.connect(self.state / 'board.sqlite3', isolation_level=None, factory=FailingBeginConnection)
        self.addCleanup(connection.close)
        connection.execute('PRAGMA foreign_keys=ON')
        before = rows(connection)
        with self.assertRaisesRegex(sqlite3.OperationalError, 'private injected begin failure'):
            migrations.migrate_16_to_17(connection)
        self.assertFalse(connection.in_transaction)
        self.assertEqual(connection.execute('PRAGMA foreign_keys').fetchone()[0], 1)
        self.assertEqual(rows(connection), before)

    def test_late_ddl_failure_rolls_back_every_value_and_schema(self):
        before, shape = rows(self.connection), schema_shape(self.connection)
        with mock.patch.object(migrations, '_NEW_DDL', migrations._NEW_DDL + (('quota_current', 'CREATE INDEX broken ON absent(value)'),)):
            with self.assertRaises(BoardError) as caught:
                migrations.migrate_16_to_17(self.connection)
        self.assertEqual(caught.exception.code, 'UPGRADE_MIGRATION_FAILED')
        self.assertEqual(caught.exception.details['tables'], ['quota_current'])
        self.assertEqual(rows(self.connection), before)
        self.assertEqual(schema_shape(self.connection), shape)
        self.assertFalse(self.connection.in_transaction)

    def test_exact_fingerprints_reject_damage_to_retained_event_and_worker_rows(self):
        for table, statement in (
            ('events', "UPDATE events SET payload_json='{}' WHERE seq=(SELECT MIN(seq) FROM events)"),
            ('workers', "UPDATE workers SET last_seen_at='damaged'"),
            ('sqlite_sequence', "UPDATE sqlite_sequence SET seq=seq+1 WHERE name='events'"),
        ):
            with self.subTest(table=table):
                self.connection.execute(f'CREATE TRIGGER damage AFTER DELETE ON catalog_pending BEGIN {statement}; END')
                before = rows(self.connection)
                with self.assertRaises(BoardError) as caught:
                    migrations.migrate_16_to_17(self.connection)
                self.assertIn(table, caught.exception.details['tables'])
                self.assertEqual(rows(self.connection), before)
                self.connection.execute('DROP TRIGGER damage')

    def test_planned_current_and_meta_values_cannot_be_changed_by_triggers(self):
        for table, statement in (
            ('meta', "UPDATE meta SET value='damaged' WHERE key='capability_secret'"),
            ('harness_health', "UPDATE harness_health SET revision=revision+1"),
            ('catalog_current', "UPDATE catalog_current SET observation_id=42"),
            ('evaluation_profiles', "UPDATE evaluation_profiles SET enabled=0"),
        ):
            with self.subTest(table=table):
                self.connection.execute(f'CREATE TRIGGER damage AFTER DELETE ON catalog_pending BEGIN {statement}; END')
                before = rows(self.connection)
                with self.assertRaises(BoardError) as caught:
                    migrations.migrate_16_to_17(self.connection)
                self.assertIn(table, caught.exception.details['tables'])
                self.assertEqual(rows(self.connection), before)
                self.connection.execute('DROP TRIGGER damage')

    def test_foreign_key_violation_rolls_back_and_restores_enforcement(self):
        self.connection.execute("UPDATE workflow_artifacts SET run_id='missing' WHERE artifact_id=(SELECT artifact_id FROM workflow_artifacts LIMIT 1)")
        self.connection.execute('PRAGMA foreign_keys=ON')
        before = rows(self.connection)
        with self.assertRaises(BoardError) as caught:
            migrations.migrate_16_to_17(self.connection)
        self.assertEqual(caught.exception.message, 'Foreign key check failed after migration')
        self.assertEqual(rows(self.connection), before)
        self.assertEqual(self.connection.execute('PRAGMA foreign_keys').fetchone()[0], 1)

    def test_only_schema_16_is_upgrade_input_and_startup_cannot_upgrade_it(self):
        with self.assertRaises(Corruption):
            Database(self.state).initialize()
        for version in (11, 12, 13, 14, 15, 17, 999):
            self.connection.execute("UPDATE meta SET value=? WHERE key='schema_version'", (str(version),))
            with self.assertRaises(BoardError) as caught:
                migrations.migrate_16_to_17(self.connection)
            self.assertEqual(caught.exception.details['tables'], ['meta'])
        self.connection.execute("UPDATE meta SET value='16' WHERE key='schema_version'")
        migrations.migrate_16_to_17(self.connection)
        before = schema_shape(self.connection)
        Database(self.state).initialize()
        self.assertEqual(schema_shape(self.connection), before)
