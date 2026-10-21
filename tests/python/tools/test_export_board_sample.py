"""Disposable samples use only private synthetic boards, never daily state."""
from contextlib import closing
import os
from pathlib import Path
import runpy
import sqlite3
import subprocess
import sys
from unittest.mock import patch

from blackboard.tasks.test_workflow import WorkflowTestCase
from console.test_console import Browser
from support import InProcessBoard, write_catalog_fixture, _child_environment, wait_for
from hey_my_buddy.blackboard.store.db import DB_FILE, SCHEMA_VERSION, SECRET_KEY, Database
from hey_my_buddy import private_dirs
from hey_my_buddy.buddy.harnesses.session_record import SessionRecord
from hey_my_buddy.protocol.session_content import MessageChunk, TextContent
from hey_my_buddy.protocol.transport import _read_endpoint, _request

TOOL = Path(__file__).resolve().parents[3] / 'tools/export_board_sample.py'
export_sample = runpy.run_path(str(TOOL))['export_sample']


def files(root):
    return {str(path.relative_to(root)): path.read_bytes()
            for path in root.rglob('*') if path.is_file()}


class ExportBoardSampleTests(WorkflowTestCase):
    def assert_source_unchanged(self, before, after):
        auxiliary = {DB_FILE + '-shm', DB_FILE + '-wal'}
        changed = [name for name in before.keys() | after.keys()
                   if name not in auxiliary and before.get(name) != after.get(name)]
        self.assertEqual(sorted(changed), [], 'Source database or non-SQLite files changed')
        wal = DB_FILE + '-wal'
        self.assertEqual(after.get(wal, b''), before.get(wal, b''),
                         'Existing WAL bytes must not change; a new WAL must be empty')

    def source_board(self):
        board = InProcessBoard(self.directory / 'source')
        self._stack.append(board)
        return board

    def accepted(self, board, name, **options):
        run = self.submit(board, request_id=name, **options)
        claim = self.claim(board, run_id=run['runId'])
        self.finish_turn(board, claim)
        delivered = board.call('workflow_get', {'runId': run['runId']})
        board.call('workflow_accept', {'runId': run['runId'], **self.control(run),
            'artifactId': delivered['finalArtifactId'], 'note': 'Synthetic acceptance',
            'notRequired': 'Private fixture has no integration target'})
        attempt_id = claim['claim']['attempt']['attemptId']
        board.store.complete_execution_cleanup(attempt_id)
        return run, attempt_id

    def test_console_history_and_sessions_survive_and_source_bytes_do_not_change(self):
        board = self.source_board()
        first, attempt = self.accepted(board, 'accepted-first', macroTask={'title': 'Sample macro'})
        second, _ = self.accepted(board, 'accepted-second', macroTaskId=first['macroTaskId'])
        unaccepted = self.submit(board, request_id='not-accepted', macroTaskId=first['macroTaskId'])
        self.finish_turn(board, self.claim(board, run_id=unaccepted['runId']))
        cancelled = self.submit(board, request_id='cancelled', kind='worktree')
        board.call('workflow_cancel', {'runId': cancelled['runId'], **self.control(cancelled), 'note': 'Fixture'})
        running = self.submit(board, request_id='running', kind='worktree')
        self.claim(board, run_id=running['runId'])
        path = private_dirs.session_record_path(board.directory, first['macroTaskId'], attempt)
        record = SessionRecord(path, attempt)
        record.append(MessageChunk(sessionUpdate='agent_message_chunk',
                                   content=TextContent(type='text', text='Sample transcript')))
        record.close()
        self.assertIsNone(record.failure)
        # Credentials/config and unrelated sessions must never be traversed.
        (board.directory / 'controls').mkdir()
        (board.directory / 'controls' / 'secret.json').write_text('private credential')
        unrelated = private_dirs.session_record_path(board.directory, 'run:' + running['runId'], 'unrelated')
        private_dirs.ensure_private_dir(unrelated.parent)
        unrelated.write_text('unselected session')
        source_secret = board.store.db.secret.hex()
        board.store.db.close()
        before = files(board.directory)
        output = export_sample(board.directory, self.directory / 'sample')
        self.assertEqual(output.stat().st_mode & 0o777, 0o700)
        self.assertFalse((output / 'controls').exists())
        self.assertFalse((output / 'harnesses').exists())
        self.assertEqual([p.name for p in (output / 'sessions').rglob('*.jsonl')], [attempt + '.jsonl'])
        self.assertEqual(private_dirs.session_record_path(output, first['macroTaskId'], attempt).read_bytes(), path.read_bytes())
        with closing(sqlite3.connect(output / DB_FILE)) as connection:
            self.assertEqual(set(row[0] for row in connection.execute('SELECT run_id FROM workflow_runs')),
                             {first['runId'], second['runId']})
            self.assertEqual(connection.execute('SELECT COUNT(*) FROM agent_credentials').fetchone()[0], 0)
            self.assertEqual(connection.execute('SELECT COUNT(*) FROM attempts WHERE execution_state!=\'finished\'').fetchone()[0], 0)
            self.assertEqual(connection.execute('PRAGMA foreign_key_check').fetchall(), [])
            self.assertEqual(connection.execute('PRAGMA integrity_check').fetchone()[0], 'ok')
            meta = dict(connection.execute('SELECT key,value FROM meta'))
            self.assertEqual(meta['schema_version'], str(SCHEMA_VERSION))
            self.assertNotEqual(meta[SECRET_KEY], source_secret)
        # The real private service/console reads full history and transcript over HTTP.
        original_directory = self.directory
        self.directory = output
        try:
            catalog = write_catalog_fixture(output)
            with self.daemon(env={'HMB_MODEL_CATALOG_FILE': str(catalog)}):
                opened = _request(_read_endpoint(output), 'console', {'action': 'open'}, state_dir=output)
                browser = Browser(opened['url'])
                for run in (first, second):
                    status, _, body = browser.get('/api/tasks/' + run['runId'])
                    self.assertEqual(status, 200, body)
                    self.assertIn(run['runId'].encode(), body)
                status, _, body = browser.get('/api/macro-tasks/' + first['macroTaskId'] + '/timeline')
                self.assertEqual(status, 200, body)
                self.assertNotIn(unaccepted['runId'].encode(), body)
                status, _, body = browser.get(f'/api/tasks/{first["runId"]}/attempts/{attempt}/session')
                self.assertEqual(status, 200, body)
                self.assertIn(b'Sample transcript', body)
        finally:
            self.directory = original_directory
        self.assert_source_unchanged(before, files(board.directory))

    def test_most_recent_hundred_accepted_are_selected_and_cli_announces_disposal(self):
        source = self.directory / 'source'
        database = Database(source)
        database.initialize()
        with database.write() as connection:
            for number in reversed(range(103)):
                identifier = f'run-{number:03d}'
                time = f'2026-01-01T00:{number // 60:02d}:{number % 60:02d}.000Z'
                connection.execute(
                    'INSERT INTO tasks(task_id,request_id,spec_json,input_fingerprint,fingerprint_version,'
                    'adapter,cwd,expected_seconds,state,revision,created_at,updated_at,accepted_at) '
                    'VALUES(?,?,\'{}\',\'hash\',1,\'dsh\',\'fixture\',10,\'completed\',1,?,?,?)',
                    (identifier, identifier, time, time, time))
                connection.execute(
                    'INSERT INTO workflow_runs(run_id,control_verifier,execution_workspace_json,state,created_at,updated_at) '
                    'VALUES(?,\'source-verifier\',\'{}\',\'accepted\',?,?)', (identifier, time, time))
        database.close()
        before = files(source)
        result = subprocess.run([sys.executable, str(TOOL), str(self.directory / 'sample'), '--source', str(source)],
                                env=self.child_environment(), capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('Delete this private sample', result.stdout)
        with closing(sqlite3.connect(self.directory / 'sample' / DB_FILE)) as connection:
            self.assertEqual([row[0] for row in connection.execute('SELECT run_id FROM workflow_runs ORDER BY rowid')],
                             [f'run-{number:03d}' for number in range(3, 103)])
        self.assert_source_unchanged(before, files(source))

    def test_invalid_selected_execution_is_not_published(self):
        for assignment in ("execution_state='uncertain'", 'shutdown_confirmed=0', 'cleanup_pending=1'):
            with self.subTest(assignment=assignment):
                board = InProcessBoard(self.directory / ('source-' + str(len(self._stack))))
                self._stack.append(board)
                run, attempt = self.accepted(board, 'accepted', kind='worktree')
                with board.store.db.write() as connection:
                    connection.execute('UPDATE attempts SET execution_state=\'finished\',shutdown_confirmed=1,cleanup_pending=0')
                    connection.execute(f'UPDATE attempts SET {assignment} WHERE attempt_id=?', (attempt,))
                output = self.directory / 'invalid'
                board.store.db.close()
                before = files(board.directory)
                with self.assertRaisesRegex(ValueError, 'unfinished or unconfirmed'):
                    export_sample(board.directory, output)
                self.assertFalse(output.exists())
                self.assert_source_unchanged(before, files(board.directory))
                self.assertEqual(list(self.directory.glob('.board-sample-*')), [])

    def test_empty_sample_empty_output_and_nonempty_output_refusal(self):
        board = self.source_board()
        output = self.directory / 'sample'
        output.mkdir()
        export_sample(board.directory, output)
        with closing(sqlite3.connect(output / DB_FILE)) as connection:
            self.assertEqual(connection.execute('SELECT COUNT(*) FROM tasks').fetchone()[0], 0)
        before = files(output)
        with self.assertRaisesRegex(ValueError, 'nonempty output'):
            export_sample(board.directory, output)
        self.assertEqual(files(output), before)
        with self.assertRaisesRegex(ValueError, 'separate directory trees'):
            export_sample(board.directory, board.directory / 'sample')

    def test_live_wal_read_preserves_database_wal_and_non_sqlite_files(self):
        board = self.source_board()
        self.accepted(board, 'accepted')
        before = files(board.directory)
        export_sample(board.directory, self.directory / 'sample')
        after = files(board.directory)
        self.assert_source_unchanged(before, after)

    def test_source_service_can_commit_during_snapshot_and_destination_creation(self):
        board = self.source_board()
        run, _ = self.accepted(board, 'accepted')
        with board.store.db.write() as connection:
            connection.execute("UPDATE tasks SET acceptance_note='fixture-version-0'")
            connection.execute("UPDATE workflow_runs SET title='fixture-version-0'")
        # Retire every in-process resource while retaining the synthetic board.
        board._resources.close()
        control = self.directory / 'writer-control'
        control.mkdir(mode=0o700)
        catalog = write_catalog_fixture(board.directory)
        environment = _child_environment(board.directory, {
            'HMB_MODEL_CATALOG_FILE': str(catalog),
            'HMB_TEST_WRITE_CONTROL': str(control),
            'PYTHONPATH': os.pathsep.join((str(TOOL.parents[1] / 'src'), str(TOOL.parents[1] / 'tests/python'))),
        })
        command = [sys.executable, str(Path(__file__).parent / 'fixtures/writing_daemon.py')]
        with (self.directory / 'writer-daemon.log').open('wb') as log:
            process = subprocess.Popen(command, env=environment, stdin=subprocess.DEVNULL,
                                       stdout=log, stderr=log, start_new_session=True)
            self.children = getattr(self, 'children', [])
            self.children.append(process)
            try:
                self.assertTrue(wait_for(lambda: (control / 'ready').exists()
                                         and _read_endpoint(board.directory) is not None, 15),
                                'Private writing service did not start')
                endpoint = _read_endpoint(board.directory)
                _request(endpoint, 'health', {}, state_dir=board.directory)
                namespace = export_sample.__globals__
                original_rows = namespace['_rows']
                original_select = namespace['_select']
                original_initialize = Database.initialize
                readers = []

                def wait_commit(version):
                    (control / f'write-{version}').touch()
                    self.assertTrue(wait_for(lambda: (control / f'committed-{version}').exists()
                                             or (control / 'error').exists(), 10),
                                    'Service writer was blocked by the exporter')
                    self.assertFalse((control / 'error').exists(),
                                     (control / 'error').read_text() if (control / 'error').exists() else '')

                def interleave_rows(connection, table, column, values):
                    rows = original_rows(connection, table, column, values)
                    if table == 'tasks':
                        # The task row is already read, but the events query has
                        # not run. Commit across that gap to expose torn reads.
                        wait_commit(1)
                    return rows

                def capture_reader(reader):
                    readers.append(reader)
                    return original_select(reader)

                def initialize_destination(database):
                    self.assertEqual(len(readers), 1)
                    with self.assertRaises(sqlite3.ProgrammingError):
                        readers[0].execute('SELECT 1')
                    wait_commit(2)
                    original_initialize(database)

                with patch.dict(namespace, {'_rows': interleave_rows, '_select': capture_reader}), \
                        patch.object(Database, 'initialize', initialize_destination):
                    output = export_sample(board.directory, self.directory / 'sample')
                with closing(sqlite3.connect(output / DB_FILE)) as connection:
                    self.assertEqual(connection.execute('SELECT acceptance_note FROM tasks').fetchone()[0],
                                     'fixture-version-0')
                    self.assertEqual(connection.execute('SELECT title FROM workflow_runs').fetchone()[0],
                                     'fixture-version-0')
                    self.assertEqual(connection.execute(
                        "SELECT COUNT(*) FROM events WHERE kind='fixture.concurrent_write'").fetchone()[0], 0)
                    self.assertEqual(connection.execute('PRAGMA foreign_key_check').fetchall(), [])
                    self.assertEqual(connection.execute('PRAGMA integrity_check').fetchone()[0], 'ok')
                with closing(sqlite3.connect((board.directory / DB_FILE).as_uri() + '?mode=ro', uri=True)) as connection:
                    self.assertEqual(connection.execute('SELECT acceptance_note FROM tasks').fetchone()[0],
                                     'fixture-version-2')
                    self.assertEqual(connection.execute(
                        "SELECT COUNT(*) FROM events WHERE kind='fixture.concurrent_write'").fetchone()[0], 2)
                self.assertEqual(_request(endpoint, 'task_get', {'runId': run['runId']},
                                          state_dir=board.directory)['task']['runId'], run['runId'])
            finally:
                process.terminate()
                process.wait(timeout=20)
