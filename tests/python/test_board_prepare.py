"""Explicit offline preparation of a schema-13 board from an idle schema-11 board."""
from __future__ import annotations

import fcntl
import hashlib
import os
import shutil
import sqlite3
import tempfile
from pathlib import Path

from test_workflow import WorkflowTestCase

from buddy import board_prepare
from buddy.db import DB_FILE, PREVIOUS_SCHEMA_VERSION, SCHEMA_VERSION, Database
from buddy.errors import BoardError

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "schema-11.sql"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class BoardPrepareTests(WorkflowTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.scratch = Path(tempfile.mkdtemp(prefix='buddy-prepare-'))
        self.addCleanup(shutil.rmtree, self.scratch, True)

    def finished_board(self):
        """A current-schema board whose runs are all terminal with confirmed stop."""
        board = self.board()
        self.register(board)
        first = self.submit(board, request_id='old-1', kind='worktree', task='first historical goal')
        second = self.submit(board, request_id='old-2', kind='worktree', task='second historical goal')
        self.finish_turn(board, self.claim(board, run_id=first['runId']))
        self.finish_turn(board, self.claim(board, claim_request_id='c2', run_id=second['runId']))
        return board, first, second

    def schema11_copy(self, name='source') -> Path:
        """Rebuild the current board's rows into the frozen schema-11 DDL."""
        target_dir = self.scratch / f'{name}'
        target_dir.mkdir(mode=0o700)
        target = sqlite3.connect(target_dir / DB_FILE)
        try:
            target.executescript(FIXTURE.read_text())
            target.execute("ATTACH DATABASE ? AS current", (str(self.directory / DB_FILE),))
            tables = [row[0] for row in target.execute(
                "SELECT name FROM main.sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
            # The live board no longer has ``evaluation_annotations`` (schema 13):
            # a table the current schema dropped is left empty for the migration.
            current_tables = {row[0] for row in target.execute(
                "SELECT name FROM current.sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
            target.execute("PRAGMA foreign_keys=OFF")
            for table in tables:
                if table not in current_tables:
                    continue
                columns = [row[1] for row in target.execute(f"PRAGMA main.table_info('{table}')")]
                joined = ",".join(f'"{column}"' for column in columns)
                target.execute(f'INSERT INTO main."{table}"({joined}) SELECT {joined} FROM current."{table}"')
            target.execute("UPDATE main.meta SET value='11' WHERE key='schema_version'")
            target.commit()
            target.execute("DETACH DATABASE current")
        finally:
            target.close()
        return target_dir

    def test_prepared_copy_opens_as_schema_13_and_preserves_every_row(self):
        board, first, second = self.finished_board()
        with board.store.db.read() as db:
            runtime_activity = {row['run_id']: row['activity_seq'] for row in db.execute(
                'SELECT run_id, activity_seq FROM workflow_runs')}
        source = self.schema11_copy()
        before = sha256(source / DB_FILE)
        destination = self.scratch / f'prepared'
        report = board_prepare.prepare(source, destination)
        self.assertTrue(report['verified'])
        self.assertEqual((report['sourceSchema'], report['schema']), (11, SCHEMA_VERSION))
        self.assertEqual((report['migration']['fromSchema'], report['migration']['toSchema']),
                         (PREVIOUS_SCHEMA_VERSION, SCHEMA_VERSION))
        self.assertEqual(report['objectivesCreated'], 0)
        self.assertEqual(sha256(source / DB_FILE), before, 'the source is never written')
        self.assertEqual(os.stat(destination / DB_FILE).st_mode & 0o777, 0o600)
        Database(destination).initialize()  # the runtime accepts the prepared copy
        prepared = sqlite3.connect(destination / DB_FILE)
        try:
            prepared_activity = dict(prepared.execute('SELECT run_id, activity_seq FROM workflow_runs'))
            self.assertEqual(prepared_activity, runtime_activity)
            self.assertEqual(prepared.execute('SELECT COUNT(*) FROM objectives').fetchone()[0], 0)
            self.assertEqual(prepared.execute('SELECT COUNT(*) FROM workflow_runs WHERE objective_id IS NOT NULL'
                                              ).fetchone()[0], 0)
            original = sqlite3.connect(source / DB_FILE)
            try:
                for table in ('tasks', 'attempts', 'events', 'workflow_runs', 'workflow_turns'):
                    self.assertEqual(prepared.execute(f'SELECT COUNT(*) FROM {table}').fetchone(),
                                     original.execute(f'SELECT COUNT(*) FROM {table}').fetchone())
                self.assertEqual(
                    prepared.execute("SELECT value FROM meta WHERE key='capability_secret'").fetchone(),
                    original.execute("SELECT value FROM meta WHERE key='capability_secret'").fetchone())
            finally:
                original.close()
        finally:
            prepared.close()

    def test_refuses_existing_destination_wrong_schema_and_active_sources(self):
        board, first, second = self.finished_board()
        source = self.schema11_copy()
        existing = self.scratch / f'existing'
        existing.mkdir()
        with self.assertRaises(BoardError) as error:
            board_prepare.prepare(source, existing)
        self.assertEqual(error.exception.code, 'DESTINATION_EXISTS')
        with self.assertRaises(BoardError) as error:
            board_prepare.prepare(self.directory, self.scratch / f'from-12')
        self.assertEqual(error.exception.code, 'UNSUPPORTED_SOURCE')
        lock = os.open(source / 'board-owner.lock', os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaises(BoardError) as error:
                board_prepare.prepare(source, self.scratch / f'locked')
            self.assertEqual(error.exception.code, 'SOURCE_ACTIVE')
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)
            os.close(lock)
        self.assertFalse((self.scratch / f'locked').exists())

    def test_refuses_a_board_with_open_work(self):
        board = self.board()
        self.submit(board, request_id='still-open', kind='worktree')
        source = self.schema11_copy('open')
        with self.assertRaises(BoardError) as error:
            board_prepare.prepare(source, self.scratch / f'open-prepared')
        self.assertEqual(error.exception.code, 'SOURCE_ACTIVE')
