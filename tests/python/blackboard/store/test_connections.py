"""Thread-owned SQLite connections retain transaction isolation and rollbacks."""
import sqlite3
import threading

from support import BoardTestCase
from hey_my_buddy.blackboard.store.db import Database


class ConnectionTests(BoardTestCase):
    def database(self):
        database = Database(self.directory)
        database.initialize()
        self.addCleanup(database.close)
        return database

    def test_one_configured_connection_across_read_and_write_transactions(self):
        database = self.database()
        with database.read() as first:
            self.assertTrue(first.in_transaction)
            self.assertEqual(first.execute("PRAGMA foreign_keys").fetchone()[0], 1)
            self.assertEqual(first.execute("PRAGMA journal_mode").fetchone()[0], "wal")
            self.assertEqual(first.execute("PRAGMA synchronous").fetchone()[0], 2)
            self.assertEqual(first.execute("PRAGMA busy_timeout").fetchone()[0], 10000)
            self.assertEqual(first.execute("PRAGMA trusted_schema").fetchone()[0], 0)
        self.assertFalse(first.in_transaction)
        with database.write() as second:
            self.assertIs(first, second)
            second.execute("CREATE TABLE connection_test(value INTEGER UNIQUE)")
            second.execute("INSERT INTO connection_test VALUES(1)")
        with database.read() as third:
            self.assertIs(first, third)
            self.assertEqual(third.execute("SELECT value FROM connection_test").fetchone()[0], 1)

    def test_failed_read_and_write_roll_back_and_keep_the_connection_usable(self):
        database = self.database()
        with database.write() as connection:
            connection.execute("CREATE TABLE connection_test(value INTEGER UNIQUE)")
        with self.assertRaises(sqlite3.IntegrityError):
            with database.write() as reused:
                self.assertIs(connection, reused)
                reused.execute("INSERT INTO connection_test VALUES(1)")
                reused.execute("INSERT INTO connection_test VALUES(1)")
        self.assertFalse(connection.in_transaction)
        with self.assertRaisesRegex(RuntimeError, "read failed"):
            with database.read() as reused:
                self.assertEqual(reused.execute("SELECT COUNT(*) FROM connection_test").fetchone()[0], 0)
                raise RuntimeError("read failed")
        with database.write(immediate=False) as reused:
            self.assertIs(connection, reused)
            reused.execute("INSERT INTO connection_test VALUES(2)")
        with database.read() as reused:
            self.assertEqual(reused.execute("SELECT value FROM connection_test").fetchone()[0], 2)

    def test_nested_reads_use_and_reuse_independent_connections_at_each_depth(self):
        database = self.database()
        with database.write() as outer:
            outer.execute("CREATE TABLE connection_test(value INTEGER)")
        for rollback in (True, False):
            with self.subTest(rollback=rollback):
                try:
                    with database.read() as outer_again:
                        self.assertIs(outer, outer_again)
                        with database.read() as inner:
                            self.assertIsNot(outer, inner)
                            self.assertTrue(outer.in_transaction)
                            with database.read() as deepest:
                                self.assertIsNot(deepest, outer)
                                self.assertIsNot(deepest, inner)
                            with database.read() as deepest_again:
                                self.assertIs(deepest, deepest_again)
                        self.assertFalse(inner.in_transaction)
                        with database.read() as inner_again:
                            self.assertIs(inner, inner_again)
                            self.assertEqual(inner.execute("SELECT COUNT(*) FROM connection_test").fetchone()[0], 0)
                        outer.execute("INSERT INTO connection_test VALUES(1)")
                        if rollback:
                            raise RuntimeError("outer failed")
                except RuntimeError as error:
                    self.assertEqual(str(error), "outer failed")
                self.assertFalse(outer.in_transaction)
                with database.read() as reused:
                    self.assertIs(reused, outer)
                    self.assertEqual(reused.execute("SELECT COUNT(*) FROM connection_test").fetchone()[0], int(not rollback))
        database.close()
        for connection in (outer, inner, deepest):
            with self.assertRaises(sqlite3.ProgrammingError):
                connection.execute("SELECT 1")

    def test_read_nested_in_write_cannot_see_uncommitted_outer_changes(self):
        database = self.database()
        with database.write() as outer:
            outer.execute("CREATE TABLE connection_test(value INTEGER)")
        for rollback in (True, False):
            with self.subTest(rollback=rollback):
                try:
                    with database.write() as outer_again:
                        self.assertIs(outer, outer_again)
                        outer.execute("INSERT INTO connection_test VALUES(1)")
                        with database.read() as inner:
                            self.assertIsNot(outer, inner)
                            self.assertEqual(inner.execute("SELECT COUNT(*) FROM connection_test").fetchone()[0], 0)
                        self.assertFalse(inner.in_transaction)
                        self.assertTrue(outer.in_transaction)
                        self.assertEqual(outer.execute("SELECT COUNT(*) FROM connection_test").fetchone()[0], 1)
                        if rollback:
                            raise RuntimeError("outer failed")
                except RuntimeError as error:
                    self.assertEqual(str(error), "outer failed")
                self.assertFalse(outer.in_transaction)
                with database.read() as reused:
                    self.assertIs(reused, outer)
                    self.assertEqual(reused.execute("SELECT COUNT(*) FROM connection_test").fetchone()[0], int(not rollback))

    def test_write_nested_in_read_commits_independently_of_outer_snapshot_and_rollback(self):
        database = self.database()
        with database.write() as outer:
            outer.execute("CREATE TABLE connection_test(value INTEGER)")
        for count, rollback in enumerate((True, False)):
            with self.subTest(rollback=rollback):
                try:
                    with database.read() as outer_again:
                        self.assertIs(outer, outer_again)
                        self.assertEqual(outer.execute("SELECT COUNT(*) FROM connection_test").fetchone()[0], count)
                        with database.write() as inner:
                            self.assertIsNot(outer, inner)
                            inner.execute("INSERT INTO connection_test VALUES(1)")
                        self.assertFalse(inner.in_transaction)
                        self.assertTrue(outer.in_transaction)
                        self.assertEqual(outer.execute("SELECT COUNT(*) FROM connection_test").fetchone()[0], count)
                        with database.read() as inner_again:
                            self.assertIs(inner, inner_again)
                            self.assertEqual(inner.execute("SELECT COUNT(*) FROM connection_test").fetchone()[0], count + 1)
                        if rollback:
                            raise RuntimeError("outer failed")
                except RuntimeError as error:
                    self.assertEqual(str(error), "outer failed")
                self.assertFalse(outer.in_transaction)
                with database.read() as reused:
                    self.assertIs(reused, outer)
                    self.assertEqual(reused.execute("SELECT COUNT(*) FROM connection_test").fetchone()[0], count + 1)

    def test_failed_nested_transactions_leave_both_depths_usable(self):
        database = self.database()
        with database.write() as outer:
            outer.execute("CREATE TABLE parent(id INTEGER PRIMARY KEY)")
            outer.execute("CREATE TABLE child(parent_id INTEGER REFERENCES parent(id) DEFERRABLE INITIALLY DEFERRED)")
        with database.read() as outer:
            for transaction in (database.read, database.write):
                with self.assertRaises(sqlite3.IntegrityError):
                    with transaction() as inner:
                        inner.execute("INSERT INTO child VALUES(1)")
                self.assertFalse(inner.in_transaction)
                self.assertTrue(outer.in_transaction)
                with database.read() as inner_again:
                    self.assertIs(inner, inner_again)
                    self.assertEqual(inner.execute("SELECT COUNT(*) FROM child").fetchone()[0], 0)
            with self.assertRaisesRegex(RuntimeError, "inner failed"):
                with database.write() as inner:
                    inner.execute("INSERT INTO parent VALUES(1)")
                    raise RuntimeError("inner failed")
            self.assertFalse(inner.in_transaction)
            outer.execute("INSERT INTO parent VALUES(2)")
        self.assertFalse(outer.in_transaction)
        with database.read() as reused:
            self.assertIs(outer, reused)
            self.assertEqual([row[0] for row in reused.execute("SELECT id FROM parent")], [2])

    def test_connections_are_per_thread_and_service_close_also_closes_exited_threads(self):
        database = self.database()
        connections = []
        with database.read() as main:
            pass

        def read_twice():
            with database.read() as first:
                connections.append(first)
            with database.read() as second:
                connections.append(second)

        thread = threading.Thread(target=read_twice)
        thread.start()
        thread.join(5)
        self.assertFalse(thread.is_alive())
        self.assertEqual(len(connections), 2)
        self.assertIs(connections[0], connections[1])
        self.assertIsNot(main, connections[0])
        database.close()
        with self.assertRaises(sqlite3.ProgrammingError):
            connections[0].execute("SELECT 1")

    def test_failed_commit_rolls_back_before_the_connection_is_reused(self):
        database = self.database()
        with database.write() as connection:
            connection.execute("CREATE TABLE parent(id INTEGER PRIMARY KEY)")
            connection.execute("CREATE TABLE child(parent_id INTEGER REFERENCES parent(id) DEFERRABLE INITIALLY DEFERRED)")
        for transaction in (database.write, database.read):
            with self.assertRaises(sqlite3.IntegrityError):
                with transaction() as reused:
                    self.assertIs(connection, reused)
                    reused.execute("INSERT INTO child VALUES(1)")
            self.assertFalse(connection.in_transaction)
        with database.write() as reused:
            self.assertEqual(reused.execute("SELECT COUNT(*) FROM child").fetchone()[0], 0)
            reused.execute("INSERT INTO parent VALUES(1)")
            reused.execute("INSERT INTO child VALUES(1)")

    def test_service_close_releases_every_live_threads_connection(self):
        database = self.database()
        ready, release = threading.Event(), threading.Event()
        connections = []

        def quiescent_thread():
            with database.read() as connection:
                connections.append(connection)
            ready.set()
            release.wait(5)

        thread = threading.Thread(target=quiescent_thread)
        thread.start()
        try:
            self.assertTrue(ready.wait(5))
            with database.read() as main:
                connections.append(main)
            database.close()
            for connection in connections:
                with self.assertRaises(sqlite3.ProgrammingError):
                    connection.execute("SELECT 1")
            with self.assertRaisesRegex(RuntimeError, "Database is closed"):
                with database.read():
                    pass
        finally:
            release.set()
            thread.join(5)
