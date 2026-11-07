"""Schema 38 opens deletion only to a service's latest correspondence change."""
from __future__ import annotations

from pathlib import Path
import sqlite3
import tempfile
import unittest

from blackboard.store.upgrade_fixture import load_fixture, load_schema36_model_stock
from hey_my_buddy.blackboard.store import (
    schema_30_31, schema_31_32, schema_32_33, schema_33_34,
    schema_34_35, schema_35_36, schema_36_37,
)
from hey_my_buddy.blackboard.store.schema_37_38 import upgrade as step_upgrade
from hey_my_buddy.blackboard.store.db import Database, SCHEMA_VERSION


class Schema37To38Tests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='schema-38-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def schema_37(self):
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
                schema_36_37.upgrade(connection)
                # The form only a schema-37 writer makes: one service with a
                # user-set change on top of its same-name row.
                row = connection.execute(
                    'SELECT harness,provider,model,model_id FROM model_correspondences LIMIT 1').fetchone()
                connection.execute(
                    'INSERT INTO model_correspondences(correspondence_id,harness,provider,model,model_id,created_at,source)'
                    " VALUES('schema-38-history',?,?,?,?,'2026-11-07T00:00:00Z','user')",
                    (row['harness'], row['provider'], row['model'], row['model_id']))
        return path

    def test_trigger_opens_only_the_latest_nonsole_row_and_matches_a_new_board(self):
        path = self.schema_37()
        with sqlite3.connect(path) as connection:
            connection.row_factory = sqlite3.Row
            first = connection.execute(
                'SELECT rowid,harness,provider,model FROM model_correspondences ORDER BY rowid LIMIT 1').fetchone()
            earliest = first['rowid']
            latest = connection.execute(
                'SELECT rowid FROM model_correspondences WHERE harness=? AND provider=? AND model=?'
                ' ORDER BY rowid DESC LIMIT 1',
                (first['harness'], first['provider'], first['model'])).fetchone()[0]
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute('DELETE FROM model_correspondences WHERE rowid=?', (latest,))
            step_upgrade(connection)
            connection.commit()
            self.assertEqual(connection.execute(
                "SELECT value FROM meta WHERE key='schema_version'").fetchone()[0], '38')
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute('DELETE FROM model_correspondences WHERE rowid=?', (earliest,))
            connection.rollback()
            connection.execute('DELETE FROM model_correspondences WHERE rowid=?', (latest,))
            self.assertEqual(connection.execute(
                'SELECT COUNT(*) FROM model_correspondences WHERE rowid=?', (latest,)).fetchone()[0], 0)
            # With the latest change gone the remaining row is the only one.
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute('DELETE FROM model_correspondences WHERE rowid=?', (earliest,))
            connection.rollback()
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute("UPDATE model_correspondences SET source='user' "
                                   "WHERE rowid=(SELECT MIN(rowid) FROM model_correspondences)")
            connection.rollback()
            self.assertEqual(list(connection.execute('PRAGMA foreign_key_check')), [])

        fresh = Database(self.root / 'fresh')
        self.addCleanup(fresh.close)
        fresh.initialize()
        query = 'SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name'
        with sqlite3.connect(path) as upgraded, fresh.read() as created:
            self.assertEqual([tuple(row) for row in upgraded.execute(query)],
                             [tuple(row) for row in created.execute(query)])


if __name__ == '__main__':
    unittest.main()
