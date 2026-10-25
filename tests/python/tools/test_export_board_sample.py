"""Disposable samples use only private synthetic boards, never daily state."""
from contextlib import closing
import json
import os
from pathlib import Path
import runpy
import sqlite3
import subprocess
import sys
from unittest.mock import patch

from blackboard.tasks.test_workflow import WorkflowTestCase
from blackboard.store.upgrade_fixture import load_fixture
from console.test_console import Browser
from support import InProcessBoard, write_catalog_fixture, _child_environment, wait_for
from private_acl import assert_private_path
from hey_my_buddy.blackboard.store.db import DB_FILE, SCHEMA_VERSION, UPGRADE_BASE_SCHEMA, SECRET_KEY, Database
from hey_my_buddy import private_dirs
from hey_my_buddy.errors import BoardError
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
        inquiry = board.directory / 'attempts' / first['runId'] / attempt / 'inquiry.results.jsonl'
        private_dirs.ensure_private_dir(inquiry.parent)
        inquiry.write_bytes(b'{"first":"accepted"}\n')
        source_secret = board.store.db.secret.hex()
        board.store.db.close()
        before = files(board.directory)
        output = export_sample(board.directory, self.directory / 'sample')
        assert_private_path(self, output, 0o700)
        self.assertFalse((output / 'controls').exists())
        self.assertFalse((output / 'harnesses').exists())
        self.assertEqual([p.name for p in (output / 'sessions').rglob('*.jsonl')], [attempt + '.jsonl'])
        self.assertEqual(private_dirs.session_record_path(output, first['macroTaskId'], attempt).read_bytes(), path.read_bytes())
        self.assertEqual((output / inquiry.relative_to(board.directory)).read_bytes(), inquiry.read_bytes())
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
                    'INSERT INTO workflow_runs(run_id,control_verifier,execution_workspace_json,workspace_manifest_json,state,created_at,updated_at) '
                    'VALUES(?,\'source-verifier\',\'{}\',\'{}\',\'accepted\',?,?)', (identifier, time, time))
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

    def test_uncertain_ownership_after_confirmed_stop_is_exported(self):
        # A lost connection freezes ownership at 'uncertain'; the stop facts
        # below are the only stop evidence the sample must show.
        board = InProcessBoard(self.directory / 'source')
        self._stack.append(board)
        run, attempt = self.accepted(board, 'accepted', kind='worktree')
        with board.store.db.write() as connection:
            connection.execute('UPDATE attempts SET execution_state=\'finished\',shutdown_confirmed=1,cleanup_pending=0')
            connection.execute("UPDATE attempts SET ownership='uncertain' WHERE attempt_id=?", (attempt,))
        board.store.db.close()
        output = export_sample(board.directory, self.directory / 'sample')
        with closing(sqlite3.connect(output / DB_FILE)) as connection:
            self.assertEqual([row[0] for row in connection.execute('SELECT run_id FROM workflow_runs')],
                             [run['runId']])
            self.assertEqual(connection.execute(
                'SELECT ownership,execution_state,shutdown_confirmed,cleanup_pending FROM attempts').fetchone(),
                ('uncertain', 'finished', 1, 0))

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

    def test_nonregular_inquiry_journal_is_refused_without_publishing(self):
        board = self.source_board()
        run, attempt = self.accepted(board, 'accepted')
        journal = board.directory / 'attempts' / run['runId'] / attempt / 'inquiry.results.jsonl'
        private_dirs.ensure_private_dir(journal.parent)
        journal.mkdir()
        board.store.db.close()
        before = files(board.directory)
        output = self.directory / 'sample'
        with self.assertRaisesRegex(BoardError, 'ordinary file'):
            export_sample(board.directory, output)
        self.assertFalse(output.exists())
        self.assert_source_unchanged(before, files(board.directory))

    def test_fixture_export_keeps_schema_and_every_selected_attempt_journal(self):
        fixture = load_fixture(self.directory / 'source')
        source = fixture['state']
        with closing(sqlite3.connect(source / DB_FILE)) as connection:
            routed = connection.execute(
                'SELECT r.run_id FROM workflow_routes r '
                'JOIN router_dispatches d ON d.decision_id=r.decision_id '
                'JOIN attempts a ON a.task_id=d.task_id LIMIT 1').fetchone()
            self.assertIsNotNone(routed, 'The base fixture must include a Router attempt')
            connection.execute("UPDATE workflow_runs SET state='accepted' WHERE run_id=?", routed)
            connection.execute("UPDATE tasks SET accepted_at='2026-01-02T00:00:00.000Z' WHERE task_id=?", routed)
            connection.execute("UPDATE attempts SET execution_state='finished',"
                               "shutdown_confirmed=1,cleanup_pending=0")
            connection.execute("UPDATE tasks SET state='completed',active_attempt_id=NULL "
                               "WHERE state NOT IN ('completed','concluded','failed','cancelled') "
                               "OR active_attempt_id IS NOT NULL")
            connection.commit()
            attempts = list(connection.execute('SELECT task_id,attempt_id FROM attempts'))
            source_schema = list(connection.execute(
                "SELECT type,name,tbl_name,sql FROM sqlite_master "
                "WHERE type IN ('table','index') AND name NOT LIKE 'sqlite_%' "
                "ORDER BY type,name"))
        for task, attempt in attempts:
            journal = source / 'attempts' / task / attempt / 'inquiry.results.jsonl'
            private_dirs.ensure_private_dir(journal.parent)
            journal.write_bytes((json.dumps({'taskId': task, 'attemptId': attempt}) + '\n').encode())
        before = files(source)
        output = export_sample(source, self.directory / 'sample')
        with closing(sqlite3.connect(output / DB_FILE)) as connection:
            self.assertEqual(connection.execute(
                "SELECT value FROM meta WHERE key='schema_version'").fetchone()[0],
                str(UPGRADE_BASE_SCHEMA))
            self.assertEqual(list(connection.execute(
                "SELECT type,name,tbl_name,sql FROM sqlite_master "
                "WHERE type IN ('table','index') AND name NOT LIKE 'sqlite_%' "
                "ORDER BY type,name")), source_schema)
            columns = {row[1] for row in connection.execute('PRAGMA table_info(messages)')}
            self.assertIn('author_role', columns)
            self.assertIn('author_id', columns)
            selected_attempts = set(connection.execute('SELECT task_id,attempt_id FROM attempts'))
            self.assertTrue(selected_attempts)
            self.assertGreater(connection.execute(
                'SELECT COUNT(*) FROM attempts a JOIN router_dispatches d ON d.task_id=a.task_id'
            ).fetchone()[0], 0)
        self.assertLess(len(selected_attempts), len(attempts))
        self.assertEqual({path.relative_to(output).as_posix() for path in output.rglob('inquiry.results.jsonl')},
                         {f'attempts/{task}/{attempt}/inquiry.results.jsonl'
                          for task, attempt in selected_attempts})
        for task, attempt in selected_attempts:
            relative = Path('attempts') / task / attempt / 'inquiry.results.jsonl'
            self.assertEqual((output / relative).read_bytes(), (source / relative).read_bytes())
        self.assert_source_unchanged(before, files(source))

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
                original_create_schema = namespace['_create_schema']
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

                def create_destination(connection, version, definitions):
                    self.assertEqual(len(readers), 1)
                    with self.assertRaises(sqlite3.ProgrammingError):
                        readers[0].execute('SELECT 1')
                    wait_commit(2)
                    original_create_schema(connection, version, definitions)

                with patch.dict(namespace, {'_rows': interleave_rows, '_select': capture_reader,
                                            '_create_schema': create_destination}):
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
