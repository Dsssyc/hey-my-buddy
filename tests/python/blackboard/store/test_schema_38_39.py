"""Schema 39 removes notes, prunes their unused models and keeps evidence intact."""
from __future__ import annotations

from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest import mock

from blackboard.store.upgrade_fixture import load_fixture
from hey_my_buddy.blackboard.store import schema_38_39, upgrade
from hey_my_buddy.blackboard.store.db import Database


class Schema38To39Tests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='schema-39-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.state = load_fixture(self.root / 'state')['state']
        self.path = self.state / 'board.sqlite3'
        with closing(sqlite3.connect(self.path)) as connection:
            connection.row_factory = sqlite3.Row
            with connection:
                for version in range(30, 38):
                    upgrade.STEPS[version](connection)
        self.connection = sqlite3.connect(self.path)
        self.addCleanup(self.connection.close)
        self.connection.row_factory = sqlite3.Row

    def test_floor_note_is_removed_and_remaining_schema_matches_new_board(self):
        connection = self.connection
        self.assertEqual(connection.execute('SELECT COUNT(*) FROM model_notes').fetchone()[0], 1)
        self.assertEqual(connection.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0], '38')
        before = {table: [tuple(row) for row in connection.execute(f'SELECT rowid,* FROM {table} ORDER BY rowid')]
                  for table in ('models', 'model_correspondences', 'attempts', 'events')}
        self.assertEqual(upgrade.upgrade_state(self.state), {'sourceSchema': 38, 'targetSchema': 39})
        self.assertEqual(connection.execute(
            "SELECT name FROM sqlite_master WHERE name IN ('model_notes','model_service_notes')").fetchall(), [])
        for table, rows in before.items():
            self.assertEqual([tuple(row) for row in connection.execute(f'SELECT rowid,* FROM {table} ORDER BY rowid')], rows)
        self.assertEqual(list(connection.execute('PRAGMA foreign_key_check')), [])
        fresh = Database(self.root / 'fresh')
        self.addCleanup(fresh.close)
        fresh.initialize()
        query = 'SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name'
        with fresh.read() as created:
            self.assertEqual([tuple(row) for row in connection.execute(query)],
                             [tuple(row) for row in created.execute(query)])

    def test_notes_prune_only_models_with_no_remaining_reference(self):
        connection = self.connection
        with connection:
            connection.executemany('INSERT INTO models(model_id,display_name) VALUES(?,?)',
                                   [('note-only', 'Note only'), ('sampled', 'Sampled')])
            connection.executemany('INSERT INTO model_notes(model_id,text,updated_at) VALUES(?,?,?)',
                                   [(model, 'Retired note', '2026-11-09T00:00:00Z')
                                    for model in ('note-only', 'sampled')])
            connection.execute(
                'INSERT INTO kind_statistics(harness,model_id,effort,samples,successes,first_passes,rework_rounds,failures)'
                " VALUES('fixture','sampled','high',1,1,1,0,0)")
        actual_references = {table[0] for table in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")
            if table[0] != 'model_notes' and any(row[2] == 'models'
                for row in connection.execute(f'PRAGMA foreign_key_list("{table[0]}")'))}
        self.assertEqual(actual_references, set(schema_38_39.MODEL_REFERENCES))
        upgrade.upgrade_state(self.state)
        self.assertIsNone(connection.execute("SELECT 1 FROM models WHERE model_id='note-only'").fetchone())
        self.assertIsNotNone(connection.execute("SELECT 1 FROM models WHERE model_id='sampled'").fetchone())
        self.assertEqual(connection.execute("SELECT samples FROM kind_statistics WHERE model_id='sampled'").fetchone()[0], 1)

    def test_trigger_forbids_updates_and_still_allows_only_latest_undo(self):
        connection = self.connection
        first = connection.execute('SELECT * FROM model_correspondences ORDER BY rowid LIMIT 1').fetchone()
        with connection:
            connection.execute(
                'INSERT INTO model_correspondences(correspondence_id,harness,provider,model,model_id,created_at,source)'
                " VALUES('latest',?,?,?,?,?,'user')",
                (first['harness'], first['provider'], first['model'], first['model_id'], '2026-11-09T00:00:00Z'))
        upgrade.upgrade_state(self.state)
        with self.assertRaisesRegex(sqlite3.IntegrityError, 'rows cannot be updated; undo deletes the latest change'):
            connection.execute("UPDATE model_correspondences SET created_at='later' WHERE correspondence_id='latest'")
        connection.rollback()
        with self.assertRaisesRegex(sqlite3.IntegrityError, 'only delete the latest change'):
            connection.execute('DELETE FROM model_correspondences WHERE correspondence_id=?', (first['correspondence_id'],))
        connection.rollback()
        with connection:
            connection.execute("DELETE FROM model_correspondences WHERE correspondence_id='latest'")
        with self.assertRaisesRegex(sqlite3.IntegrityError, 'only delete the latest change'):
            connection.execute('DELETE FROM model_correspondences WHERE correspondence_id=?', (first['correspondence_id'],))
        connection.rollback()

    def test_failure_after_drop_restores_notes_models_and_trigger(self):
        connection = self.connection
        query = 'SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name'
        before_schema = [tuple(row) for row in connection.execute(query)]
        before_notes = [tuple(row) for row in connection.execute('SELECT * FROM model_notes')]
        before_models = [tuple(row) for row in connection.execute('SELECT * FROM models')]
        def fail_after_step(db):
            schema_38_39.upgrade(db)
            raise RuntimeError('after-note-removal')
        with mock.patch.dict(upgrade.STEPS, {38: fail_after_step}), self.assertRaisesRegex(RuntimeError, 'after-note-removal'):
            upgrade.upgrade_state(self.state)
        self.assertEqual([tuple(row) for row in connection.execute(query)], before_schema)
        self.assertEqual([tuple(row) for row in connection.execute('SELECT * FROM model_notes')], before_notes)
        self.assertEqual([tuple(row) for row in connection.execute('SELECT * FROM models')], before_models)
        self.assertEqual(connection.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0], '38')


if __name__ == '__main__':
    unittest.main()
