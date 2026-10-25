"""Reopened fixtures keep their shared SQLite file until every owner is closed."""
from support import BoardTestCase
from pathlib import Path
from temporary import TemporaryDirectory
from hey_my_buddy.paths import native_path


class InProcessFixtureCleanupTests(BoardTestCase):
    def test_temporary_cleanup_removes_a_tree_beyond_the_windows_path_limit(self):
        temporary = TemporaryDirectory(dir=self.directory)
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        leaf = root / ("x" * 120) / ("y" * 120) / "evidence.txt"
        self.assertGreater(len(str(leaf)), 260)
        native_path(leaf.parent).mkdir(parents=True)
        native_path(leaf).write_bytes(b"private fixture")
        temporary.cleanup()
        self.assertFalse(root.exists())

    def test_closing_an_old_board_does_not_delete_the_reopened_database(self):
        original = self.board()
        reopened = self.board()
        with reopened.store.db.read() as connection:
            secret = connection.execute("SELECT value FROM meta WHERE key='capability_secret'").fetchone()[0]
        original.close()
        self.assertTrue(reopened.store.db.path.is_file())
        with reopened.store.db.read() as connection:
            self.assertEqual(connection.execute("SELECT value FROM meta WHERE key='capability_secret'").fetchone()[0], secret)
