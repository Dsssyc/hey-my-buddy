"""Short catalog lookups must release every connection they create."""
from unittest.mock import patch

from support import BoardTestCase
from hey_my_buddy.blackboard.catalog import catalog
from hey_my_buddy.blackboard.store.db import Database
from hey_my_buddy.errors import BoardError


class CatalogConnectionTests(BoardTestCase):
    def test_reread_window_and_rejected_validation_release_temporary_databases(self):
        board = self.board()
        databases = []

        def opened(*args, **kwargs):
            database = Database(*args, **kwargs)
            databases.append(database)  # Keep GC from closing an unowned connection.
            return database

        with patch('hey_my_buddy.blackboard.store.db.Database', side_effect=opened):
            self.assertEqual(catalog.coordinate_catalog_reread(
                board.directory, 'dsh', reader=lambda: None, wait=True), 'started')
            self.assertEqual(catalog.coordinate_catalog_reread(
                board.directory, 'dsh', reader=lambda: self.fail('window must prevent a scan'), wait=True), 'window')
            with patch.object(catalog, '_override', return_value=None), \
                 self.assertRaises(BoardError):
                catalog.validate_buddy({'harness': 'dsh', 'provider': 'fixture',
                                        'model': 'missing', 'effort': 'max'}, directory=board.directory)
        self.assertGreaterEqual(len(databases), 3)
        for database in databases:
            self.assertTrue(database._closed)
            self.assertEqual(database._connections, {})
