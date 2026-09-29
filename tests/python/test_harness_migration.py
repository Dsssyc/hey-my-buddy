"""Schema 14 is additive, atomic, and never a startup conversion."""
from contextlib import closing
import sqlite3
import unittest

from pathlib import Path
SCHEMA = (Path(__file__).parent / "fixtures/schema-14.sql").read_text()
from buddy.migrations import migrate_13_to_14, schema_version
from test_migrations import shape


class HarnessMigrationTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(':memory:', isolation_level=None)
        self.addCleanup(self.db.close)
        self.db.executescript(SCHEMA)
        self.db.execute('DROP TABLE harness_health')
        self.db.execute("INSERT INTO meta VALUES('schema_version','13')")
        self.db.execute("INSERT INTO meta VALUES('user-setting','preserved')")

    def test_matches_fresh_schema_and_keeps_existing_values(self):
        before = self.db.execute('SELECT key,value FROM meta ORDER BY key').fetchall()
        self.assertEqual(migrate_13_to_14(self.db)['harnessHealth'], 0)
        self.assertEqual(schema_version(self.db), 14)
        self.assertEqual(self.db.execute('SELECT * FROM harness_health').fetchall(), [])
        with closing(sqlite3.connect(':memory:')) as fresh:
            fresh.executescript(SCHEMA)
            self.assertEqual(shape(self.db), shape(fresh))
        self.assertEqual(self.db.execute("SELECT value FROM meta WHERE key='user-setting'").fetchone()[0], before[1][1])

    def test_failure_rolls_back_new_table_and_version(self):
        self.db.execute("CREATE TRIGGER refuse_version BEFORE UPDATE ON meta BEGIN SELECT RAISE(ABORT,'injected'); END")
        with self.assertRaises(sqlite3.IntegrityError):
            migrate_13_to_14(self.db)
        self.assertEqual(schema_version(self.db), 13)
        self.assertIsNone(self.db.execute("SELECT name FROM sqlite_master WHERE name='harness_health'").fetchone())

    def test_refuses_repeated_migration(self):
        migrate_13_to_14(self.db)
        with self.assertRaises(ValueError):
            migrate_13_to_14(self.db)
