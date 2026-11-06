"""Schema 37 keeps model identity separate from service names and frozen attempts."""
from __future__ import annotations

from pathlib import Path
import sqlite3
import tempfile
import unittest

from blackboard.store.upgrade_fixture import load_fixture, load_schema36_model_stock
from hey_my_buddy.blackboard.store import (
    schema_30_31, schema_31_32, schema_32_33, schema_33_34,
    schema_34_35, schema_35_36,
)
from hey_my_buddy.blackboard.store.schema_36_37 import KIND_TABLES, upgrade as step_upgrade
from hey_my_buddy.blackboard.store.db import Database


class Schema36To37Tests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='schema-37-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def schema_36(self):
        fixture = load_fixture(self.root / 'state')
        path = fixture['state'] / 'board.sqlite3'
        with sqlite3.connect(path) as connection:
            connection.row_factory = sqlite3.Row
            connection.execute('PRAGMA foreign_keys=OFF')
            with connection:
                for step in (schema_30_31, schema_31_32, schema_32_33,
                             schema_33_34, schema_34_35, schema_35_36):
                    step.upgrade(connection)
                    if step is schema_30_31:
                        connection.executescript((Path(__file__).resolve().parents[3]
                            / 'fixtures/upgrade-floor/schema-31-stock.sql').read_text(encoding='utf-8'))
                load_schema36_model_stock(connection)
        return path

    def test_stock_conversion_and_fresh_schema_match(self):
        path = self.schema_36()
        with sqlite3.connect(path) as connection:
            connection.row_factory = sqlite3.Row
            stock = {table: [dict(row) for row in connection.execute(
                f'SELECT rowid,* FROM {table} ORDER BY rowid')]
                for table in (*KIND_TABLES, 'attempts')}
            # The fixture must exercise every stored form that changes its key.
            for table in KIND_TABLES:
                self.assertTrue(stock[table], table)
            names = {row['model'] for table in KIND_TABLES for row in stock[table]}
            names.update(row['model_model'] for row in stock['attempts'] if row['model_model'])
            names.update(row[0] for row in connection.execute('SELECT model FROM buddies'))
            names.update(row[0] for row in connection.execute('SELECT model FROM model_service_notes'))
            step_upgrade(connection)
            connection.commit()
            self.assertEqual(connection.execute(
                "SELECT value FROM meta WHERE key='schema_version'").fetchone()[0], '37')
            self.assertEqual({row[0] for row in connection.execute('SELECT display_name FROM models')}, names)
            for table in ('profile_runs', 'model_profiles', 'model_profile_claims'):
                self.assertEqual(connection.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0], 0)
            settings = connection.execute(
                'SELECT profile_buddy_id,profile_budget FROM router_settings').fetchone()
            self.assertEqual(tuple(settings), (None, 'deep'))
            self.assertEqual(list(connection.execute('PRAGMA foreign_key_check')), [])
            for table in KIND_TABLES:
                rows = [dict(row) for row in connection.execute(
                    f'SELECT t.rowid,t.*,m.display_name FROM {table} t '
                    'JOIN models m ON m.model_id=t.model_id ORDER BY t.rowid')]
                self.assertEqual(len(rows), len(stock[table]), table)
                for old, new in zip(stock[table], rows):
                    self.assertEqual(new['display_name'], old['model'])
                    for key, value in old.items():
                        if key != 'model':
                            self.assertEqual(new[key], value, (table, key))
            attempts = {row['rowid']: dict(row) for row in connection.execute(
                'SELECT a.rowid,a.*,m.display_name FROM attempts a '
                'LEFT JOIN models m ON m.model_id=a.model_id')}
            for old in stock['attempts']:
                new = attempts[old['rowid']]
                self.assertEqual(new['display_name'], old['model_model'])
                for key, value in old.items():
                    self.assertEqual(new[key], value, key)
            model_id = connection.execute('SELECT model_id FROM models LIMIT 1').fetchone()[0]
            for source, url, date in (
                ('profile', None, '2026-11-07'),
                ('profile', 'https://example.org/model', None),
                ('profile', '', '2026-11-07'),
                ('same-name', 'https://example.org/model', None),
                ('user', None, '2026-11-07'),
            ):
                with self.subTest(source=source, url=url, date=date), self.assertRaises(sqlite3.IntegrityError):
                    connection.execute(
                        'INSERT INTO model_correspondences(correspondence_id,harness,provider,model,model_id,created_at,source,evidence_url,evidence_date)'
                        ' VALUES(?,?,?,?,?,?,?,?,?)',
                        ('invalid-correspondence', 'h', 'p', 'm', model_id, '2026-11-07T00:00:00Z', source, url, date))
            connection.execute(
                'INSERT INTO model_correspondences(correspondence_id,harness,provider,model,model_id,created_at,source,evidence_url,evidence_date)'
                ' VALUES(?,?,?,?,?,?,?,?,?)',
                ('profile-correspondence', 'h', 'p', 'm', model_id, '2026-11-07T00:00:00Z', 'profile',
                 'https://example.org/model', '2026-11-07'))
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute("UPDATE model_correspondences SET source='user' "
                                   "WHERE correspondence_id=(SELECT correspondence_id FROM model_correspondences LIMIT 1)")
            connection.rollback()
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute('DELETE FROM model_correspondences')
            connection.rollback()

        fresh = Database(self.root / 'fresh')
        self.addCleanup(fresh.close)
        fresh.initialize()
        query = 'SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name'
        with sqlite3.connect(path) as upgraded, fresh.read() as created:
            self.assertEqual([tuple(row) for row in upgraded.execute(query)],
                             [tuple(row) for row in created.execute(query)])

    def test_profile_run_and_claim_constraints(self):
        path = self.schema_36()
        with sqlite3.connect(path) as connection:
            connection.row_factory = sqlite3.Row
            step_upgrade(connection)
            connection.commit()
            connection.execute('PRAGMA foreign_keys=ON')
            model_id = connection.execute('SELECT model_id FROM models LIMIT 1').fetchone()[0]
            buddy_id = connection.execute('SELECT buddy_id FROM buddies LIMIT 1').fetchone()[0]
            task_id = connection.execute('SELECT task_id FROM tasks LIMIT 1').fetchone()[0]
            connection.execute(
                'INSERT INTO profile_runs(profile_run_id,model_id,buddy_id,task_id,state,started_at) '
                "VALUES('run-1',?,?,?,'running','2026-01-01T00:00:00Z')",
                (model_id, buddy_id, task_id))
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    'INSERT INTO model_profiles(model_id,positioning,built_at,profile_run_id) '
                    "VALUES(?,'brief','2026-01-01T00:00:00Z','absent')", (model_id,))
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute("UPDATE profile_runs SET correction_rounds=-1 WHERE profile_run_id='run-1'")
            connection.execute(
                "UPDATE profile_runs SET state='completed',finished_at='2026-01-01T00:01:00Z',"
                "usage_json=? WHERE profile_run_id='run-1'", ('{"attempts":[]}',))
            connection.execute(
                'INSERT INTO model_profiles(model_id,positioning,built_at,profile_run_id) '
                "VALUES(?,'brief','2026-01-01T00:01:00Z','run-1')", (model_id,))
            connection.execute(
                'INSERT INTO model_profile_claims(model_id,ordinal,polarity,claim,source_url,source_date,source_kind) '
                "VALUES(?,0,'strength','fast','https://example.org','2026-01-01','third-party')", (model_id,))
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    'INSERT INTO model_profile_claims(model_id,ordinal,polarity,claim,source_url,source_date,source_kind) '
                    "VALUES(?,1,'neutral','fast','https://example.org','2026-01-01','vendor')", (model_id,))
            self.assertEqual(connection.execute('PRAGMA foreign_key_check').fetchall(), [])

    def test_notes_merge_by_exact_name_without_losing_different_text(self):
        path = self.schema_36()
        with sqlite3.connect(path) as connection:
            connection.row_factory = sqlite3.Row
            connection.executemany(
                'INSERT INTO model_service_notes(harness,provider,model,text,updated_at) VALUES(?,?,?,?,?)',
                [('h', 'a', 'same', 'one', '2026-01-01T00:00:00Z'),
                 ('h', 'b', 'same', 'one', '2026-01-02T00:00:00Z'),
                 ('h', 'a', 'different', 'first', '2026-01-01T00:00:00Z'),
                 ('h', 'b', 'different', 'second', '2026-01-02T00:00:00Z')])
            step_upgrade(connection)
            result = {row['display_name']: (row['text'], row['updated_at']) for row in connection.execute(
                'SELECT m.display_name,n.text,n.updated_at FROM model_notes n '
                'JOIN models m ON m.model_id=n.model_id')}
            self.assertEqual(result['same'], ('one', '2026-01-02T00:00:00Z'))
            self.assertEqual(result['different'],
                             ('[h / a / different]\nfirst\n\n[h / b / different]\nsecond',
                              '2026-01-02T00:00:00Z'))
            self.assertEqual(connection.execute(
                "SELECT COUNT(*) FROM model_correspondences WHERE model='same'").fetchone()[0], 2)
            self.assertEqual(list(connection.execute('PRAGMA foreign_key_check')), [])


if __name__ == '__main__':
    unittest.main()
