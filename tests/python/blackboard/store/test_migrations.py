"""The released schema-17 board guards the single 17→18 conversion."""
from __future__ import annotations

import json
import re
import sqlite3
from unittest import mock

from support import BoardTestCase
from blackboard.store.upgrade_fixture import FIXTURE, load_fixture
from hey_my_buddy.blackboard.catalog.catalog import permission_capabilities
from hey_my_buddy.blackboard.store import migrations
from hey_my_buddy.blackboard.store.db import Database, Corruption
from hey_my_buddy.errors import BoardError

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


class Schema17UpgradeTests(BoardTestCase):
    def setUp(self):
        super().setUp()
        self.state = self.directory / 'base'
        self.fixture = load_fixture(self.state)
        self.connection = sqlite3.connect(self.state / 'board.sqlite3', isolation_level=None)
        self.addCleanup(self.connection.close)

    def test_migration_has_the_complete_fresh_schema(self):
        summary = migrations.migrate_17_to_18(self.connection)
        fresh = Database(self.directory / 'fresh')
        fresh.initialize()
        with fresh.read() as connection:
            self.assertEqual(schema_shape(self.connection), schema_shape(connection))
        self.assertEqual(summary, {'fromSchema': 17, 'toSchema': 18,
                                   'migratedTurns': len(rows(self.connection)['workflow_turns']),
                                   'migratedProfiles': len(rows(self.connection)['evaluation_profiles'])})

    def test_every_historical_value_is_preserved_and_turn_access_is_backfilled(self):
        old = rows(self.connection)
        old_columns = [row[1] for row in self.connection.execute('PRAGMA table_info(workflow_turns)')]
        access_index = old_columns.index('state')
        run_access = {row[0]: json.loads(row[1])['access'] for row in self.connection.execute(
            'SELECT run_id,execution_workspace_json FROM workflow_runs')}
        migrations.migrate_17_to_18(self.connection)
        actual = rows(self.connection)
        for table, values in old.items():
            if table not in ('meta', 'workflow_turns', 'evaluation_profiles'):
                self.assertEqual(actual[table], values, table)
        cap_index = [row[1] for row in self.connection.execute('PRAGMA table_info(evaluation_profiles)')].index('capabilities_json')
        adapter_index = [row[1] for row in self.connection.execute('PRAGMA table_info(evaluation_profiles)')].index('adapter')
        for before, after in zip(old['evaluation_profiles'], actual['evaluation_profiles']):
            self.assertEqual(after[:cap_index] + after[cap_index + 1:], before[:cap_index] + before[cap_index + 1:])
            self.assertEqual(set(json.loads(after[cap_index])),
                             {cap for cap in json.loads(before[cap_index]) if not cap.startswith('permission:')}
                             | set(permission_capabilities(before[adapter_index])))
            if before[adapter_index] in ('codex', 'dsh'):
                self.assertIn('permission:full', json.loads(after[cap_index]))
        expected_turns = [row[:access_index] + (run_access[row[1]], None) + row[access_index:]
                          for row in old['workflow_turns']]
        self.assertEqual(actual['workflow_turns'], sorted(expected_turns, key=repr))
        self.assertEqual(dict(actual['meta']), {**dict(old['meta']), 'schema_version': '18'})

    def test_existing_published_profiles_gain_read_and_full_eligibility_after_upgrade(self):
        from hey_my_buddy.blackboard.routing.decision import DecisionCoordinator
        self.connection.row_factory = sqlite3.Row
        original = self.connection.execute('SELECT profile_id,adapter,enabled,available FROM evaluation_profiles ORDER BY profile_id').fetchall()
        migrations.migrate_17_to_18(self.connection)
        self.assertEqual([tuple(row) for row in self.connection.execute('SELECT profile_id,adapter,enabled,available FROM evaluation_profiles ORDER BY profile_id')],
                         [tuple(row) for row in original])
        # Quota records remain real preserved stock. Clear only this private
        # fixture's quota stop so the scan isolates permission eligibility.
        self.connection.execute('DELETE FROM quota_current')
        for level in ('read', 'full'):
            with self.subTest(level=level):
                expected = {row['profile_id'] for row in original if row['enabled'] and row['available']
                            and f'permission:{level}' in permission_capabilities(row['adapter'])}
                candidates, _ = DecisionCoordinator._scan_candidates(
                    self.connection, [f'permission:{level}'], coding_only=True)
                self.assertEqual({row['profile_id'] for row in candidates}, expected)

    def test_historical_write_access_uses_owning_run_without_changing_turn_evidence(self):
        run_id = DELIVERED
        self.connection.execute(
            "UPDATE workflow_runs SET execution_workspace_json=json_set(execution_workspace_json,'$.access','write')"
            ' WHERE run_id=?', (run_id,))
        before = self.connection.execute(
            'SELECT input_json,input_sha256,prompt_sha256,provenance_json FROM workflow_turns WHERE run_id=?',
            (run_id,),
        ).fetchone()
        migrations.migrate_17_to_18(self.connection)
        after = self.connection.execute(
            'SELECT access,access_reason,input_json,input_sha256,prompt_sha256,provenance_json'
            ' FROM workflow_turns WHERE run_id=?', (run_id,),
        ).fetchone()
        self.assertEqual(after, ('write', None, *before))
        self.assertEqual({row[0] for row in self.connection.execute(
            'SELECT access FROM workflow_turns WHERE run_id!=?', (run_id,))}, {'read'})

    def test_turn_access_and_reason_constraint(self):
        migrations.migrate_17_to_18(self.connection)
        turn_id = self.connection.execute('SELECT turn_id FROM workflow_turns LIMIT 1').fetchone()[0]
        with self.assertRaises(sqlite3.IntegrityError):
            self.connection.execute("UPDATE workflow_turns SET access='full' WHERE turn_id=?", (turn_id,))
        self.connection.execute("UPDATE workflow_turns SET access='full',access_reason='Host choice' WHERE turn_id=?", (turn_id,))
        with self.assertRaises(sqlite3.IntegrityError):
            self.connection.execute("UPDATE workflow_turns SET access='read' WHERE turn_id=?", (turn_id,))

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
                        with mock.patch.object(migrations, 'WORKFLOW_TURNS_DDL', 'CREATE TABLE workflow_turns ('):
                            with self.assertRaises(BoardError):
                                migrations.migrate_17_to_18(connection)
                        self.assertEqual(rows(connection), before)
                    else:
                        migrations.migrate_17_to_18(connection)
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
            migrations.migrate_17_to_18(connection)
        self.assertFalse(connection.in_transaction)
        self.assertEqual(connection.execute('PRAGMA foreign_keys').fetchone()[0], 1)
        self.assertEqual(rows(connection), before)

    def test_late_ddl_failure_rolls_back_values_and_schema(self):
        before, shape = rows(self.connection), schema_shape(self.connection)
        ddl = migrations.WORKFLOW_TURNS_DDL + '\nCREATE INDEX broken ON absent(value)'
        with mock.patch.object(migrations, 'WORKFLOW_TURNS_DDL', ddl):
            with self.assertRaises(BoardError):
                migrations.migrate_17_to_18(self.connection)
        self.assertEqual(rows(self.connection), before)
        self.assertEqual(schema_shape(self.connection), shape)
        self.assertFalse(self.connection.in_transaction)

    def test_exact_fingerprints_reject_damage_to_retained_rows(self):
        for table, statement in (
            ('events', "UPDATE events SET payload_json='{}' WHERE seq=(SELECT MIN(seq) FROM events)"),
            ('workers', "UPDATE workers SET last_seen_at='damaged'"),
            ('sqlite_sequence', "UPDATE sqlite_sequence SET seq=seq+1 WHERE name='events'"),
        ):
            with self.subTest(table=table):
                self.connection.execute(f'CREATE TRIGGER damage AFTER UPDATE ON meta BEGIN {statement}; END')
                before = rows(self.connection)
                with self.assertRaises(BoardError) as caught:
                    migrations.migrate_17_to_18(self.connection)
                self.assertIn(table, caught.exception.details['tables'])
                self.assertEqual(rows(self.connection), before)
                self.connection.execute('DROP TRIGGER damage')

    def test_planned_meta_and_turn_values_cannot_be_changed_by_triggers(self):
        for table, statement in (
            ('meta', "UPDATE meta SET value='damaged' WHERE key='capability_secret'"),
            ('workflow_turns', "UPDATE workflow_turns SET input_sha256='damaged'"),
        ):
            with self.subTest(table=table):
                self.connection.execute(f'CREATE TRIGGER damage AFTER UPDATE ON meta BEGIN {statement}; END')
                before = rows(self.connection)
                with self.assertRaises(BoardError) as caught:
                    migrations.migrate_17_to_18(self.connection)
                self.assertIn(table, caught.exception.details['tables'])
                self.assertEqual(rows(self.connection), before)
                self.connection.execute('DROP TRIGGER damage')

    def test_foreign_key_violation_rolls_back_and_restores_enforcement(self):
        self.connection.execute("UPDATE workflow_artifacts SET run_id='missing' WHERE artifact_id=(SELECT artifact_id FROM workflow_artifacts LIMIT 1)")
        self.connection.execute('PRAGMA foreign_keys=ON')
        before = rows(self.connection)
        with self.assertRaises(BoardError) as caught:
            migrations.migrate_17_to_18(self.connection)
        self.assertEqual(caught.exception.message, 'Foreign key check failed after migration')
        self.assertEqual(rows(self.connection), before)
        self.assertEqual(self.connection.execute('PRAGMA foreign_keys').fetchone()[0], 1)

    def test_only_schema_17_is_upgrade_input_and_startup_cannot_upgrade_it(self):
        with self.assertRaises(Corruption):
            Database(self.state).initialize()
        for version in (11, 12, 13, 14, 15, 16, 18, 999):
            self.connection.execute("UPDATE meta SET value=? WHERE key='schema_version'", (str(version),))
            with self.assertRaises(BoardError) as caught:
                migrations.migrate_17_to_18(self.connection)
            self.assertEqual(caught.exception.details['tables'], ['meta'])
        self.connection.execute("UPDATE meta SET value='17' WHERE key='schema_version'")
        migrations.migrate_17_to_18(self.connection)
        before = schema_shape(self.connection)
        Database(self.state).initialize()
        self.assertEqual(schema_shape(self.connection), before)
