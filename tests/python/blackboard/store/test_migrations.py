"""The frozen upgrade-base board guards the single schema-15-to-16 migration."""
from __future__ import annotations

from contextlib import closing
import json
from pathlib import Path
import re
import sqlite3
from unittest import mock

from support import BoardTestCase
from blackboard.store.upgrade_fixture import load_fixture
from hey_my_buddy.blackboard.catalog import catalog, catalog_store
from hey_my_buddy.blackboard.routing import router
from hey_my_buddy.blackboard.store import migrations
from hey_my_buddy.blackboard.store.db import Corruption, Database, SCHEMA_VERSION
from hey_my_buddy.errors import BoardError

FIXTURE_ROOT = Path(__file__).resolve().parents[3] / 'fixtures/adr021-l18'
DELIVERED = '00000000-0000-0000-0000-000000000007'
CANCELLED = '00000000-0000-0000-0000-00000000001e'


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


class Schema15UpgradeTests(BoardTestCase):
    def setUp(self):
        super().setUp()
        self.state = self.directory / 'upgrade-state'
        load_fixture(self.state)
        self.connection = sqlite3.connect(self.state / 'board.sqlite3', isolation_level=None)
        self.connection.row_factory = sqlite3.Row
        self.addCleanup(self.connection.close)

    def test_fixed_fixture_migrates_to_the_complete_fresh_schema_before_any_opener(self):
        statements = []
        self.connection.set_trace_callback(statements.append)
        summary = migrations.migrate_15_to_16(self.connection)
        migrated = schema_shape(self.connection)
        fresh = Database(self.directory / 'fresh')
        fresh.initialize()
        with fresh.read() as connection:
            self.assertEqual(migrated, schema_shape(connection))
        self.assertEqual(summary['fromSchema'], 15)
        self.assertEqual(summary['toSchema'], 16)
        created_tables = [sql for sql in statements if sql.lstrip().startswith('CREATE TABLE')]
        self.assertEqual(len(created_tables), len(migrations.NEW_TABLES) + 4)
        self.assertFalse(any('CREATE TABLE IF NOT EXISTS tasks' in sql for sql in statements))
        for table in migrations.NEW_TABLES:
            self.assertEqual(self.connection.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0], 0, table)

    def test_schema_guard_detects_a_new_current_form_missing_from_the_migration(self):
        migrations.migrate_15_to_16(self.connection)
        fresh = Database(self.directory / 'unmigrated-new-form')
        fresh.initialize()
        with fresh.write() as connection:
            connection.execute('ALTER TABLE model_facts ADD COLUMN unplanned TEXT')
        with fresh.read() as connection:
            # Use the same full comparison as the guard, prove it really fails.
            with self.assertRaises(AssertionError):
                self.assertEqual(schema_shape(self.connection), schema_shape(connection))
        self.assertNotIn('unplanned', [row[1] for row in self.connection.execute('PRAGMA table_info(model_facts)')])

    def test_rows_raw_json_and_historical_hashes_are_preserved_except_the_exact_plan(self):
        self.connection.execute("INSERT INTO workflow_host_conclusions(conclusion_id,run_id,run_revision,"
                                "owner_generation,execution_status,note,actor,command_id,created_at)"
                                " VALUES('old-conclusion',?,1,1,'cancelled','old note','fixture','old-command','then')",
                                (CANCELLED,))
        old = rows(self.connection)
        migrations.migrate_15_to_16(self.connection)
        new = rows(self.connection)
        for table in old:
            if table not in ('meta', 'catalog_current'):
                self.assertEqual(old[table], new[table], table)
        old_meta, new_meta = dict(old['meta']), dict(new['meta'])
        allowed = migrations.ROUTER_META_KEYS | {'schema_version'}
        self.assertEqual({key: value for key, value in old_meta.items() if key not in allowed},
                         {key: value for key, value in new_meta.items() if key not in allowed})
        self.assertEqual([row[:3] + row[5:] for row in old['catalog_current']],
                         [row[:3] + row[5:] for row in new['catalog_current']])
        self.assertEqual(self.connection.execute("SELECT note FROM workflow_host_conclusions").fetchone()[0], 'old note')
        self.connection.execute("INSERT INTO workflow_host_conclusions(conclusion_id,run_id,run_revision,"
                                "owner_generation,execution_status,note,actor,command_id,created_at)"
                                " VALUES('new-conclusion',?,1,1,'delivered','new note','fixture','new-command','now')",
                                (DELIVERED,))
        with self.assertRaises(sqlite3.IntegrityError):
            self.connection.execute("UPDATE workflow_host_conclusions SET execution_status='invented'")

    def test_router_slots_follow_default_order_remove_old_keys_and_keep_settings_history(self):
        old_events = [tuple(row) for row in self.connection.execute('SELECT * FROM events')]
        migrations.migrate_15_to_16(self.connection)
        settings = router.configuration(self.connection)
        slots = json.loads((FIXTURE_ROOT / 'metadata.json').read_text())['routerSlots']
        self.assertEqual(settings['routerProfileIds'], [slots['review'], slots['fast']])
        self.assertEqual(settings['defaultRoutingMode'], 'review')
        self.assertEqual(settings['routerRetryIntervalSeconds'], 600)
        self.assertIsNone(self.connection.execute("SELECT 1 FROM meta WHERE key IN"
                                                " ('router_fast_profile_id','router_review_profile_id')").fetchone())
        self.assertEqual(old_events, [tuple(row) for row in self.connection.execute('SELECT * FROM events')])

    def test_router_conversion_keeps_unavailable_ids_deduplicates_empties_and_maps_quick(self):
        self.connection.execute("UPDATE meta SET value='missing-profile' WHERE key='router_fast_profile_id'")
        self.connection.execute("UPDATE meta SET value='' WHERE key='router_review_profile_id'")
        self.connection.execute("UPDATE meta SET value='quick' WHERE key='router_budget_preset'")
        migrations.migrate_15_to_16(self.connection)
        settings = router.configuration(self.connection)
        self.assertEqual(settings['routerProfileIds'], ['missing-profile'])
        self.assertEqual(settings['routingBudget'], 'brief')

    def test_router_identical_slots_deduplicate_and_two_empty_slots_make_an_empty_list(self):
        for value, expected in (('unavailable-profile', ['unavailable-profile']), ('', [])):
            with self.subTest(value=value):
                state = self.directory / ('duplicate' if value else 'empty')
                load_fixture(state)
                with closing(sqlite3.connect(state / 'board.sqlite3', isolation_level=None)) as connection:
                    connection.row_factory = sqlite3.Row
                    connection.execute("UPDATE meta SET value=? WHERE key IN"
                        " ('router_fast_profile_id','router_review_profile_id')", (value,))
                    migrations.migrate_15_to_16(connection)
                    self.assertEqual(router.configuration(connection)['routerProfileIds'], expected)

    def test_catalog_current_is_unknown_without_invented_read_times_or_pending_windows(self):
        original = [tuple(row) for row in self.connection.execute('SELECT * FROM catalog_current')]
        profiles = [tuple(row) for row in self.connection.execute('SELECT * FROM evaluation_profiles')]
        payloads = [tuple(row) for row in self.connection.execute('SELECT * FROM evaluation_catalog')]
        migrations.migrate_15_to_16(self.connection)
        for adapter, observation, discovery, _status, _reason, updated_at in original:
            current = self.connection.execute('SELECT * FROM catalog_current WHERE adapter=?', (adapter,)).fetchone()
            self.assertEqual(tuple(current), (adapter, observation, discovery, 'unknown', 'ACCOUNT_STATUS_UNKNOWN', updated_at))
            self.assertIsNone(self.connection.execute('SELECT value FROM meta WHERE key=?',
                                                     ('catalog-account-status:' + adapter,)).fetchone(), adapter)
            self.assertEqual(catalog.catalog_account_status(self.connection, adapter), 'unknown')
            self.assertIsNone(catalog.catalog_read_at(self.connection, adapter))
            self.assertEqual(catalog._pending_map(self.connection, adapter), {})
        self.assertEqual(profiles, [tuple(row) for row in self.connection.execute('SELECT * FROM evaluation_profiles')])
        self.assertEqual(payloads, [tuple(row) for row in self.connection.execute('SELECT * FROM evaluation_catalog')])
        self.assertIsNone(self.connection.execute("SELECT 1 FROM meta WHERE key LIKE 'catalog-read-at:%'"
                                                " OR key LIKE 'catalog-scan-after:%'").fetchone())
        self.assertTrue(catalog_store.current(self.connection).payload['providers'])

    def test_new_and_rebuilt_event_indexes_have_the_current_predicates(self):
        migrations.migrate_15_to_16(self.connection)
        definitions = dict(self.connection.execute("SELECT name,sql FROM sqlite_master WHERE type='index'"))
        self.assertIn('workflow.review_rejected', definitions['events_review_seq_idx'])
        self.assertIn("'router.answered','router.no_answer'", definitions['events_router_outcome_idx'])
        self.assertIn("'router.claimed'", definitions['events_router_claimed_idx'])

    def test_sql_failure_rolls_back_every_form_value_and_marker(self):
        old_rows, old_shape = rows(self.connection), schema_shape(self.connection)
        with mock.patch.object(migrations, '_CONCLUSION_INDEX', 'CREATE INDEX broken ON no_such_table(value)'):
            with self.assertRaises(BoardError) as caught:
                migrations.migrate_15_to_16(self.connection)
        self.assertEqual(caught.exception.details['tables'], ['workflow_host_conclusions'])
        self.assertEqual(rows(self.connection), old_rows)
        self.assertEqual(schema_shape(self.connection), old_shape)

    def test_a_trigger_cannot_change_an_unrelated_retained_table(self):
        self.connection.execute("CREATE TRIGGER retained_value_changed AFTER UPDATE ON catalog_current BEGIN"
                                " UPDATE evaluation_cards SET summary='not authorized by this upgrade'; END")
        old_rows, old_shape = rows(self.connection), schema_shape(self.connection)
        with self.assertRaises(BoardError) as caught:
            migrations.migrate_15_to_16(self.connection)
        self.assertEqual(caught.exception.details['tables'], ['evaluation_cards'])
        self.assertEqual(rows(self.connection), old_rows)
        self.assertEqual(schema_shape(self.connection), old_shape)

    def test_trigger_cannot_change_retained_values_inside_an_allowed_table(self):
        self.connection.execute("CREATE TRIGGER corrupt_catalog AFTER UPDATE ON catalog_current BEGIN"
                                " UPDATE catalog_current SET discovery_id=NULL WHERE adapter=NEW.adapter; END")
        old = rows(self.connection)
        with self.assertRaises(BoardError) as caught:
            migrations.migrate_15_to_16(self.connection)
        self.assertIn('catalog_current', caught.exception.details['tables'])
        self.assertEqual(rows(self.connection), old)

    def test_meta_failure_rolls_back_router_catalog_sql_and_version_together(self):
        self.connection.execute("CREATE TRIGGER refuse_marker BEFORE UPDATE ON meta"
                                " WHEN NEW.key='schema_version' BEGIN SELECT RAISE(ABORT,'marker failure'); END")
        old_rows, old_shape = rows(self.connection), schema_shape(self.connection)
        with self.assertRaises(BoardError) as caught:
            migrations.migrate_15_to_16(self.connection)
        self.assertEqual(caught.exception.details['tables'], ['meta'])
        self.assertEqual(rows(self.connection), old_rows)
        self.assertEqual(schema_shape(self.connection), old_shape)

    def test_invalid_source_router_settings_fail_without_any_conversion(self):
        self.connection.execute("UPDATE meta SET value='invalid-mode' WHERE key='router_default_mode'")
        old_rows, old_shape = rows(self.connection), schema_shape(self.connection)
        with self.assertRaises(BoardError) as caught:
            migrations.migrate_15_to_16(self.connection)
        self.assertEqual(caught.exception.details['tables'], ['meta'])
        self.assertEqual(caught.exception.code, 'UPGRADE_MIGRATION_FAILED')
        self.assertEqual(rows(self.connection), old_rows)
        self.assertEqual(schema_shape(self.connection), old_shape)

    def test_intermediate_and_current_router_forms_fail_with_meta_context_and_preserve_settings(self):
        for version in ('2', '3'):
            with self.subTest(version=version):
                state = self.directory / ('router-version-' + version)
                load_fixture(state)
                with closing(sqlite3.connect(state / 'board.sqlite3', isolation_level=None)) as connection:
                    connection.execute("UPDATE meta SET value=? WHERE key='router_configuration_version'", (version,))
                    connection.execute("DELETE FROM meta WHERE key IN ('router_fast_profile_id','router_review_profile_id')")
                    connection.execute("INSERT INTO meta(key,value) VALUES('router_profile_ids',?)",
                                       ('["keep-this-profile"]',))
                    old_rows, old_shape = rows(connection), schema_shape(connection)
                    statements = []
                    connection.set_trace_callback(statements.append)
                    with self.assertRaises(BoardError) as caught:
                        migrations.migrate_15_to_16(connection)
                    self.assertEqual(caught.exception.payload(), {
                        'code':'UPGRADE_MIGRATION_FAILED',
                        'message':'Schema 15 Router configuration must match the upgrade-base version 1 form',
                        'details':{'tables':['meta'], 'routerForm':{
                            'expectedVersion':'1', 'actualVersion':version,
                            'missingKeys':['router_fast_profile_id', 'router_review_profile_id'],
                            'unexpectedKeys':['router_profile_ids'],
                        }},
                    })
                    self.assertIn('ROLLBACK', statements)
                    self.assertFalse(any(sql.lstrip().startswith(('CREATE ', 'DROP ', 'ALTER ', 'INSERT ', 'UPDATE ', 'DELETE '))
                                         for sql in statements))
                    self.assertFalse(connection.in_transaction)
                    self.assertEqual(rows(connection), old_rows)
                    self.assertEqual(schema_shape(connection), old_shape)

    def test_missing_router_marker_is_rejected_without_projecting_the_legacy_pointer(self):
        self.connection.execute("DELETE FROM meta WHERE key='router_configuration_version'")
        self.connection.execute("UPDATE evaluation_state SET decision_profile_id='legacy-pointer'")
        old_rows, old_shape = rows(self.connection), schema_shape(self.connection)
        with self.assertRaises(BoardError) as caught:
            migrations.migrate_15_to_16(self.connection)
        self.assertEqual(caught.exception.code, 'UPGRADE_MIGRATION_FAILED')
        self.assertEqual(caught.exception.details, {'tables':['meta'], 'routerForm':{
            'expectedVersion':'1', 'actualVersion':None,
            'missingKeys':['router_configuration_version'], 'unexpectedKeys':[],
        }})
        self.assertEqual(rows(self.connection), old_rows)
        self.assertEqual(schema_shape(self.connection), old_shape)
        self.assertFalse(self.connection.in_transaction)

    def test_version_one_router_cannot_mix_forms_or_invent_missing_settings(self):
        for key, value in (('router_profile_ids', '["keep-this-profile"]'),
                           ('router_retry_interval_seconds', '123'), ('router_default_mode', None)):
            with self.subTest(key=key):
                state = self.directory / key
                load_fixture(state)
                with closing(sqlite3.connect(state / 'board.sqlite3', isolation_level=None)) as connection:
                    if value is None:
                        connection.execute('DELETE FROM meta WHERE key=?', (key,))
                    else:
                        connection.execute('INSERT INTO meta(key,value) VALUES(?,?)', (key, value))
                    old_rows, old_shape = rows(connection), schema_shape(connection)
                    with self.assertRaises(BoardError) as caught:
                        migrations.migrate_15_to_16(connection)
                    self.assertEqual(caught.exception.details, {'tables':['meta'], 'routerForm':{
                        'expectedVersion':'1', 'actualVersion':'1',
                        'missingKeys':[key] if value is None else [],
                        'unexpectedKeys':[key] if value is not None else [],
                    }})
                    self.assertEqual(rows(connection), old_rows)
                    self.assertEqual(schema_shape(connection), old_shape)
                    self.assertFalse(connection.in_transaction)

    def test_only_schema_15_is_an_upgrade_input_and_15_cannot_open_implicitly(self):
        with self.assertRaises(Corruption):
            Database(self.state).initialize()
        for version in (11, 12, 13, 14, 16, 999):
            self.connection.execute("UPDATE meta SET value=? WHERE key='schema_version'", (str(version),))
            old = rows(self.connection)
            with self.assertRaises(BoardError):
                migrations.migrate_15_to_16(self.connection)
            self.assertEqual(rows(self.connection), old)

    def test_current_opener_performs_no_ddl_or_implicit_schema_repair(self):
        migrations.migrate_15_to_16(self.connection)
        self.connection.execute('DROP TABLE model_facts')
        old_shape = schema_shape(self.connection)
        Database(self.state).initialize()
        self.assertEqual(schema_shape(self.connection), old_shape)
