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
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute("UPDATE model_mappings SET source='user' "
                                   "WHERE mapping_id=(SELECT mapping_id FROM model_mappings LIMIT 1)")
            connection.rollback()
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute('DELETE FROM model_mappings')
            connection.rollback()

        fresh = Database(self.root / 'fresh')
        self.addCleanup(fresh.close)
        fresh.initialize()
        query = 'SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name'
        with sqlite3.connect(path) as upgraded, fresh.read() as created:
            self.assertEqual([tuple(row) for row in upgraded.execute(query)],
                             [tuple(row) for row in created.execute(query)])

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
                "SELECT COUNT(*) FROM model_mappings WHERE model='same'").fetchone()[0], 2)
            self.assertEqual(list(connection.execute('PRAGMA foreign_key_check')), [])


if __name__ == '__main__':
    unittest.main()
