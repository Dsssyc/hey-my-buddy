"""Disposable samples use only private synthetic boards, never daily state."""
from contextlib import closing
import json
import os
from pathlib import Path
import runpy
import sqlite3
import subprocess
import sys
import time
import zlib
from unittest.mock import patch

from blackboard.tasks.test_workflow import WorkflowTestCase
from blackboard.store.upgrade_fixture import load_fixture
from console.test_console import Browser
from support import (InProcessBoard, write_catalog_fixture, _child_environment, wait_for, enable_fixture_buddy,
                     native_exists, native_is_file, native_read_bytes, native_write_bytes)
from private_acl import assert_private_path
from hey_my_buddy.blackboard.store.db import DB_FILE, SCHEMA_VERSION, SECRET_KEY, Database
from hey_my_buddy.blackboard.store.upgrade import UPGRADE_FLOOR_SCHEMA, upgrade_state
from hey_my_buddy import private_dirs
from hey_my_buddy.errors import BoardError
from hey_my_buddy.paths import native_path
from hey_my_buddy.buddy.harnesses.session_record import SessionRecord
from hey_my_buddy.protocol.session_content import MessageChunk, TextContent
from hey_my_buddy.protocol.transport import _read_endpoint, _request

TOOL = Path(__file__).resolve().parents[3] / 'tools/export_board_sample.py'
export_sample = runpy.run_path(str(TOOL))['export_sample']


def files(root):
    return {str(path.relative_to(native_path(root))): path.read_bytes()
            for path in native_path(root).rglob('*') if path.is_file()}


class ExportBoardSampleTests(WorkflowTestCase):
    def assert_source_unchanged(self, before, after):
        auxiliary = {DB_FILE + '-shm', DB_FILE + '-wal'}
        changed = [name for name in before.keys() | after.keys()
                   if name not in auxiliary and before.get(name) != after.get(name)]
        self.assertEqual(sorted(changed), [], 'Source database or non-SQLite files changed')
        wal = DB_FILE + '-wal'
        self.assertEqual(after.get(wal, b''), before.get(wal, b''),
                         'Existing WAL bytes must not change; a new WAL must be empty')

    def assert_only_upgrade_project_marker_changed(self, before, after, fixture, project_id):
        repository = fixture['fixtureRoot'] / 'repository'
        relative = str(repository.relative_to(fixture['state']))
        ref = str(Path(relative) / '.git/refs/hey-my-buddy/project')
        self.assertNotIn(ref, before)
        self.assertIn(ref, after)
        oid = after[ref].decode('ascii').strip()
        blob = str(Path(relative) / '.git/objects' / oid[:2] / oid[2:])
        self.assertNotIn(blob, before)
        self.assertIn(blob, after)
        self.assertEqual(zlib.decompress(after[blob]),
                         b'blob ' + str(len(project_id.encode())).encode() + b'\0' + project_id.encode())
        self.assert_source_unchanged(before, {name: value for name, value in after.items()
                                             if name not in {ref, blob}})

    def source_board(self):
        board = InProcessBoard(self.directory / 'source')
        self._stack.append(board)
        return board

    def accepted(self, board, name, **options):
        run = self.submit(board, request_id=name, **options)
        claim = self.claim(board, run_id=run['runId'])
        self.finish_turn(board, claim, seal=options.get('kind') != 'none')
        delivered = board.call('workflow_get', {'runId': run['runId']})
        board.call('workflow_accept', {'runId': run['runId'],
            'artifactId': delivered['finalArtifactId'], 'note': 'Synthetic acceptance',
            'notRequired': 'Private fixture has no integration target'})
        attempt_id = claim['claim']['attempt']['attemptId']
        board.store.complete_execution_cleanup(attempt_id)
        return run, attempt_id

    def test_record_patch_locations_use_the_reference_across_platforms(self):
        from hey_my_buddy.protocol.workspace_manifest import artifact_manifest
        board = self.source_board()
        run, _attempt = self.accepted(board, "native-path-record")
        with board.store.db.read() as connection:
            row = connection.execute("SELECT * FROM workflow_artifacts WHERE run_id=? AND kind='output'",
                                     (run["runId"],)).fetchone()
        record = artifact_manifest(board.directory, row)
        # A decoded frozen record may name the Windows host that produced it.
        # The current sample's files still live at their state-relative references.
        foreign = {**record, "diffPath": r"C:\origin-host\output.patch"}
        expected = (board.directory / row["record_path"]).with_suffix(".patch")
        with patch.object(board.store.workflow, "_artifact_document", return_value=foreign):
            view = board.store.workflow._artifact_view(row)
        self.assertEqual(view["diffPath"], str(expected))
        self.assertEqual(view["cumulativePatch"]["path"], str(expected))
        before = files(board.directory)
        with patch.dict(export_sample.__globals__, {"resolve_record": lambda *args: foreign}):
            sample = export_sample(board.directory, self.directory / "portable-record-sample")
        copied = (sample / row["record_path"]).with_suffix(".patch")
        self.assertEqual(native_read_bytes(copied), native_read_bytes(expected))
        self.assert_source_unchanged(before, files(board.directory))

    def test_result_reference_copies_the_bound_original_receipt(self):
        board = self.source_board()
        run, attempt_id = self.accepted(board, 'result-reference-sample', kind='none')
        with board.store.db.read() as connection:
            relative, digest = connection.execute("SELECT record_path,record_sha256 FROM workflow_artifacts WHERE kind='result'").fetchone()
        receipt = board.directory / relative
        original = private_dirs.attempt_root(board.directory, 'dsh', run['runId'], attempt_id) / 'result-receipt.json'
        self.assertEqual(native_read_bytes(receipt), native_read_bytes(original))
        board.store.db.close()
        before = files(board.directory)
        output = export_sample(board.directory, self.directory / 'receipt-sample')
        self.assertEqual(native_read_bytes(output / relative), native_read_bytes(receipt))
        with closing(sqlite3.connect(output / DB_FILE)) as connection:
            self.assertEqual(connection.execute(
                "SELECT record_path,record_sha256 FROM workflow_artifacts WHERE kind='result'").fetchone(),
                (relative, digest))
        self.assert_source_unchanged(before, files(board.directory))

    def test_none_result_survives_native_retention_and_can_be_read_and_exported(self):
        from hey_my_buddy.blackboard.tasks.reclamation import reclaim_native
        board = self.source_board()
        run, attempt_id = self.accepted(board, 'result-after-native-retention', kind='none')
        before = board.call('workflow_get', {'runId': run['runId']})
        result = before['artifacts'][0]
        raw = native_read_bytes(Path(result['resultFile']['path']))
        native = private_dirs.micro_task_root(board.directory, 'dsh', run['runId'])
        self.assertTrue(native_exists(native))
        with board.store.db.write() as connection:
            connection.execute("UPDATE tasks SET accepted_at='2000-01-01T00:00:00Z' WHERE task_id=?", (run['runId'],))
        reclaim_native(board.store)
        self.assertFalse(native_exists(native))
        after = board.call('workflow_get', {'runId': run['runId']})
        self.assertEqual(after['artifacts'], before['artifacts'])
        self.assertEqual(board.call('artifact_list', {'runId': run['runId']})['artifacts'], before['artifacts'])
        self.assertEqual(native_read_bytes(Path(result['resultFile']['path'])), raw)
        relative = Path(result['resultFile']['path']).relative_to(board.directory)
        board.store.db.close()
        output = export_sample(board.directory, self.directory / 'retained-result-sample')
        self.assertEqual(native_read_bytes(output / relative), raw)

    def test_result_reference_rejects_a_receipt_outside_its_attempt(self):
        board = self.source_board()
        run, attempt_id = self.accepted(board, 'result-reference-invalid', kind='none')
        with board.store.db.write() as connection:
            connection.execute(
                "UPDATE workflow_artifacts SET record_path='harnesses/other/result-receipt.json' WHERE kind='result'")
        board.store.db.close()
        with self.assertRaisesRegex(ValueError, 'outside its bound attempt'):
            export_sample(board.directory, self.directory / 'invalid-receipt-sample')
        self.assertFalse((self.directory / 'invalid-receipt-sample').exists())

    def test_none_results_keep_each_attempt_harness_after_buddy_changes(self):
        board = self.source_board()
        run = self.submit(board, request_id='two-harness-results', kind='none')
        first = self.claim(board, run_id=run['runId'])
        self.finish_turn(board, first, seal=False)
        artifact = board.call('workflow_get', {'runId': run['runId']})['finalArtifactId']
        replacement = {'harness': 'codex', 'provider': 'openai', 'model': 'fixture', 'effort': 'low'}
        enable_fixture_buddy(board.store, replacement)
        board.call('workflow_continue', {'runId': run['runId'], 'artifactId': artifact,
            'input': 'Inspect again with the selected buddy', 'buddy': ' / '.join(replacement.values()),
            'note': 'A second native harness is authorized'})
        second = self.claim(board, run_id=run['runId'], claim_request_id='second-harness')
        self.finish_turn(board, second, seal=False)
        final = board.call('workflow_get', {'runId': run['runId']})['finalArtifactId']
        board.call('workflow_accept', {'runId': run['runId'], 'artifactId': final,
            'note': 'Both answers checked', 'notRequired': 'Answers only'})
        for claimed in (first, second):
            board.store.complete_execution_cleanup(claimed['claim']['attempt']['attemptId'])
        with board.store.db.read() as connection:
            results = [row[0] for row in connection.execute("SELECT record_path FROM workflow_artifacts WHERE kind='result'")]
        self.assertEqual({Path(path).parts[:2] for path in results}, {('attempts', run['runId'])})
        self.assertEqual({Path(path).parts[2] for path in results},
                         {claimed['claim']['attempt']['attemptId'] for claimed in (first, second)})
        board.store.db.close()
        output = export_sample(board.directory, self.directory / 'two-harness-sample')
        for relative in results:
            self.assertEqual(native_read_bytes(output / relative), native_read_bytes(board.directory / relative))

    def test_console_history_and_sessions_survive_and_source_bytes_do_not_change(self):
        board = self.source_board()
        first, attempt = self.accepted(board, 'accepted-first', create_macro={'title': 'Sample macro', 'goal': 'Sample macro'})
        second, _ = self.accepted(board, 'accepted-second', macroTaskId=first['macroTaskId'])
        unaccepted = self.submit(board, request_id='not-accepted', macroTaskId=first['macroTaskId'])
        unselected_manifest = Path(board.call('workflow_get', {'runId': unaccepted['runId']})['workspace']['manifestFile']['path'])
        self.finish_turn(board, self.claim(board, run_id=unaccepted['runId']))
        cancelled = self.submit(board, request_id='cancelled', kind='worktree')
        board.call('workflow_cancel', {'runId': cancelled['runId'], 'note': 'Fixture'})
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
        native_path(unrelated).write_text('unselected session')
        inquiry = board.directory / 'attempts' / first['runId'] / attempt / 'inquiry.results.jsonl'
        private_dirs.ensure_private_dir(inquiry.parent)
        native_write_bytes(inquiry, b'{"first":"accepted"}\n')
        source_secret = board.store.db.secret.hex()
        board.store.db.close()
        before = files(board.directory)
        with closing(sqlite3.connect(board.directory / DB_FILE)) as connection:
            source_results = dict(connection.execute('SELECT attempt_id,result_json FROM attempts'))
            source_keys = dict(connection.execute('SELECT run_id,creation_key FROM workflow_runs'))
            macro_project = connection.execute(
                'SELECT project_id FROM macro_tasks WHERE macro_task_id=?',
                (first['macroTaskId'],)).fetchone()[0]
        output = export_sample(board.directory, self.directory / 'sample')
        assert_private_path(self, output, 0o700)
        self.assertFalse(native_exists(output / unselected_manifest.relative_to(board.directory)))
        self.assertFalse((output / 'controls').exists())
        self.assertFalse((output / 'harnesses').exists())
        self.assertEqual([p.name for p in native_path(output / 'sessions').rglob('*.jsonl')], [attempt + '.jsonl'])
        self.assertEqual(native_path(private_dirs.session_record_path(output, first['macroTaskId'], attempt)).read_bytes(), native_path(path).read_bytes())
        self.assertEqual(native_path(output / inquiry.relative_to(board.directory)).read_bytes(), native_path(inquiry).read_bytes())
        with closing(sqlite3.connect(output / DB_FILE)) as connection:
            self.assertEqual(set(row[0] for row in connection.execute('SELECT run_id FROM workflow_runs')),
                             {first['runId'], second['runId']})
            columns = {row[1] for row in connection.execute('PRAGMA table_info(workflow_runs)')}
            self.assertTrue(columns.isdisjoint({'control_verifier', 'submission_verifier'}))
            self.assertEqual(dict(connection.execute('SELECT run_id,creation_key FROM workflow_runs')),
                             {run_id: source_keys[run_id] for run_id in (first['runId'], second['runId'])})
            self.assertEqual(connection.execute('SELECT project_id FROM projects').fetchall(),
                             [(macro_project,)])
            self.assertEqual(connection.execute('SELECT COUNT(*) FROM agent_credentials').fetchone()[0], 0)
            self.assertEqual(connection.execute('SELECT SUM(samples),SUM(successes) FROM kind_statistics').fetchone(), (2, 2))
            self.assertEqual(connection.execute('SELECT COUNT(*) FROM kind_cell_statistics').fetchone()[0], 0)
            self.assertEqual(connection.execute('SELECT COUNT(*) FROM attempts WHERE execution_state!=\'finished\'').fetchone()[0], 0)
            self.assertEqual(connection.execute('PRAGMA foreign_key_check').fetchall(), [])
            self.assertEqual(connection.execute('PRAGMA integrity_check').fetchone()[0], 'ok')
            if SCHEMA_VERSION >= 34:
                referenced = connection.execute(
                    "SELECT content_form,workspace_id,record_path FROM workflow_artifacts "
                    "WHERE content_form IN ('input-reference','record-reference')").fetchall()
                self.assertTrue(referenced)
                for form, workspace_id, record_path in referenced:
                    path = (output / 'workspaces' / workspace_id / 'manifest.json'
                            if form == 'input-reference' else output / record_path)
                    self.assertTrue(native_is_file(path), (form, path))
                    self.assertTrue(path.is_relative_to(output))
                for attempt_id, raw in connection.execute(
                        "SELECT attempt_id,result_json FROM attempts WHERE result_form='current-report-v2'"):
                    self.assertEqual(raw, source_results[attempt_id])
                    result = json.loads(raw)['result'] or {}
                    for key in ('workspaceSeal', 'partialWorkspaceSeal'):
                        if key in result:
                            self.assertTrue(native_is_file(output / result[key]['recordFile']['path']))
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
            connection.execute('INSERT INTO projects(project_id,kind,path) VALUES(?,?,?)',
                               ('sample-project', 'folder', str(source / 'sample-project')))
            connection.execute('INSERT INTO projects(project_id,kind,path) VALUES(?,?,?)',
                               ('unselected-project', 'folder', str(source / 'unselected-project')))
            for number in reversed(range(103)):
                identifier = f'run-{number:03d}'
                time = f'2026-01-01T00:{number // 60:02d}:{number % 60:02d}.000Z'
                connection.execute(
                    "INSERT INTO tasks(task_id,request_id,spec_json,input_fingerprint,fingerprint_version,adapter,cwd,state,revision,created_at,updated_at,accepted_at) VALUES(?,?,json_set('{}','$.expectedSeconds',10),'hash',1,'dsh','fixture','completed',1,?,?,?)",
                    (identifier, identifier, time, time, time))
                connection.execute(
                    'INSERT INTO workflow_runs(run_id,execution_workspace_json,standalone_project_id,'
                    'project_kind,checkout,state,created_at,updated_at) '
                    "VALUES(?,'{}','sample-project','folder','none','accepted',?,?)", (identifier, time, time))
        database.close()
        before = files(source)
        result = subprocess.run([sys.executable, str(TOOL), str(self.directory / 'sample'), '--source', str(source)],
                                env=self.child_environment(), capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('Delete this private sample', result.stdout)
        with closing(sqlite3.connect(self.directory / 'sample' / DB_FILE)) as connection:
            self.assertEqual([row[0] for row in connection.execute('SELECT run_id FROM workflow_runs ORDER BY rowid')],
                             [f'run-{number:03d}' for number in range(3, 103)])
            self.assertEqual(connection.execute('SELECT project_id FROM projects').fetchall(),
                             [('sample-project',)])
        self.assert_source_unchanged(before, files(source))

    def test_invalid_selected_execution_is_not_published(self):
        for assignment in ("execution_state='uncertain'", 'shutdown_confirmed=0', 'cleanup_pending=1'):
            with self.subTest(assignment=assignment):
                board = InProcessBoard(self.directory / ('source-' + str(len(self._stack))))
                self._stack.append(board)
                run, attempt = self.accepted(board, 'accepted-' + str(len(self._stack)), kind='worktree')
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
        # below are the only stop evidence the sample must show. An existing
        # checkout keeps the run selected: the double never applies a cleanup
        # plan, and a worktree run without one no longer rides along.
        board = InProcessBoard(self.directory / 'source')
        self._stack.append(board)
        run, attempt = self.accepted(board, 'accepted')
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

    def test_sample_carries_applied_plans_and_leaves_unreclaimed_checkouts_out(self):
        board = self.source_board()
        reclaimed, reclaimed_attempt = self.accepted(board, 'reclaimed-worktree', kind='worktree')
        retained, _retained_attempt = self.accepted(board, 'retained-worktree', kind='worktree')
        plain, plain_attempt = self.accepted(board, 'plain-existing')
        with board.store.db.write() as connection:
            # The double cannot apply a plan; the source fact "this checkout is
            # reclaimed" is exactly what the sample has to carry.
            connection.execute(
                "UPDATE workspace_cleanup_plans SET state='applied',applied_at='2026-01-01T00:00:00.000Z'"
                " WHERE run_id=?", (reclaimed['runId'],))
        with board.store.db.read() as connection:
            self.assertEqual({row['run_id']: row['state'] for row in
                              connection.execute('SELECT run_id,state FROM workspace_cleanup_plans')},
                             {reclaimed['runId']: 'applied', retained['runId']: 'blocked'})
            applied_source = dict(connection.execute(
                'SELECT * FROM workspace_cleanup_plans WHERE state=\'applied\'').fetchone())
        board.store.db.close()
        output = export_sample(board.directory, self.directory / 'sample')
        with closing(sqlite3.connect(output / DB_FILE)) as connection:
            connection.row_factory = sqlite3.Row
            self.assertEqual({row[0] for row in connection.execute('SELECT run_id FROM workflow_runs')},
                             {reclaimed['runId'], plain['runId']})
            self.assertEqual({row[0] for row in connection.execute('SELECT attempt_id FROM attempts')},
                             {reclaimed_attempt, plain_attempt})
            self.assertEqual({row['plan_id']: dict(row) for row in
                              connection.execute('SELECT * FROM workspace_cleanup_plans')},
                             {applied_source['plan_id']: applied_source})
            self.assertFalse((output / 'attempts' / retained['runId']).exists())

    def test_still_unreclaimed_after_one_rebuild_is_refused(self):
        board = self.source_board()
        self.accepted(board, 'retained-worktree', kind='worktree')
        board.store.db.close()
        original = export_sample.__globals__['_staged_candidates']
        checks = []

        def staged(stage, schema):
            result = original(stage, schema)
            checks.append(result)
            # A second opinion that never falls silent stands for a drifted
            # candidate query: the tool must refuse, not loop or publish.
            return result or {'always-unreclaimed'}

        output = self.directory / 'refused'
        with patch.dict(export_sample.__globals__, {'_staged_candidates': staged}):
            with self.assertRaisesRegex(ValueError, 'after one rebuild'):
                export_sample(board.directory, output)
        self.assertEqual(len(checks), 2)
        self.assertFalse(output.exists())
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

    def test_unsupported_schema_preserves_source_and_publishes_no_sample(self):
        board = self.source_board()
        board.store.db.close()
        for schema in (UPGRADE_FLOOR_SCHEMA - 1, SCHEMA_VERSION + 1):
            with self.subTest(schema=schema):
                with closing(sqlite3.connect(board.directory / DB_FILE)) as connection, connection:
                    connection.execute("UPDATE meta SET value=? WHERE key='schema_version'", (str(schema),))
                before = files(board.directory)
                output = self.directory / f'unsupported-{schema}'
                with self.assertRaisesRegex(ValueError, f'Source must use schema {UPGRADE_FLOOR_SCHEMA} through {SCHEMA_VERSION}'):
                    export_sample(board.directory, output)
                self.assertFalse(output.exists())
                self.assert_source_unchanged(before, files(board.directory))

    def test_fixture_export_keeps_schema_and_every_selected_attempt_journal(self):
        fixture = load_fixture(self.directory / 'source')
        source = fixture['state']
        with closing(sqlite3.connect(source / DB_FILE)) as connection:
            project_ids = {row[0] for row in connection.execute(
                "SELECT DISTINCT json_extract(workspace_manifest_json,'$.repositoryId') "
                "FROM workflow_runs WHERE workspace_manifest_json IS NOT NULL")}
            self.assertEqual(len(project_ids), 1)
            project_id = project_ids.pop()
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
            native_write_bytes(journal, (json.dumps({'taskId': task, 'attemptId': attempt}) + '\n').encode())
        before = files(source)
        output = export_sample(source, self.directory / 'sample')
        with closing(sqlite3.connect(output / DB_FILE)) as connection:
            self.assertEqual(connection.execute(
                "SELECT value FROM meta WHERE key='schema_version'").fetchone()[0],
                str(fixture['schemaVersion']))
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
        self.assertEqual({str(path.relative_to(native_path(output))) for path in native_path(output).rglob('inquiry.results.jsonl')},
                         {str(Path('attempts') / task / attempt / 'inquiry.results.jsonl')
                          for task, attempt in selected_attempts})
        for task, attempt in selected_attempts:
            relative = Path('attempts') / task / attempt / 'inquiry.results.jsonl'
            self.assertEqual(native_path(output / relative).read_bytes(), native_path(source / relative).read_bytes())
        self.assertEqual(upgrade_state(output),
                         {'sourceSchema': UPGRADE_FLOOR_SCHEMA, 'targetSchema': SCHEMA_VERSION})
        current = Database(output)
        self.addCleanup(current.close)
        current.initialize()
        self.assert_only_upgrade_project_marker_changed(before, files(source), fixture, project_id)

    def test_schema31_sample_keeps_source_form_and_can_upgrade(self):
        from hey_my_buddy.blackboard.store import schema_30_31
        fixture = load_fixture(self.directory / 'source31')
        with closing(sqlite3.connect(fixture['state'] / DB_FILE)) as connection:
            connection.row_factory = sqlite3.Row
            project_ids = {row[0] for row in connection.execute(
                "SELECT DISTINCT json_extract(workspace_manifest_json,'$.repositoryId') "
                "FROM workflow_runs WHERE workspace_manifest_json IS NOT NULL")}
            self.assertEqual(len(project_ids), 1)
            project_id = project_ids.pop()
            with connection:
                schema_30_31.upgrade(connection)
                connection.execute("UPDATE attempts SET execution_state='finished',shutdown_confirmed=1,cleanup_pending=0")
                connection.execute("UPDATE tasks SET state='completed',active_attempt_id=NULL")
        before = files(fixture['state'])
        sample = export_sample(fixture['state'], self.directory / 'sample31')
        with closing(sqlite3.connect(sample / DB_FILE)) as connection:
            self.assertEqual(connection.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0], '31')
            self.assertGreater(connection.execute('SELECT SUM(samples) FROM kind_statistics').fetchone()[0], 0)
            statistics = list(connection.execute('SELECT * FROM kind_statistics ORDER BY harness,model,effort'))
        self.assertEqual(upgrade_state(sample), {'sourceSchema': 31, 'targetSchema': SCHEMA_VERSION})
        with closing(sqlite3.connect(sample / DB_FILE)) as connection:
            self.assertEqual(list(connection.execute('SELECT * FROM kind_statistics ORDER BY harness,model,effort')),
                             statistics)
        self.assert_only_upgrade_project_marker_changed(before, files(fixture['state']), fixture, project_id)

    def test_schema34_sample_keeps_its_structure_seals_and_can_upgrade(self):
        from hey_my_buddy.blackboard.store.upgrade import STEPS
        fixture = load_fixture(self.directory / 'source34')
        source = fixture['state']
        with closing(sqlite3.connect(source / DB_FILE)) as connection:
            connection.row_factory = sqlite3.Row
            with connection:
                for version in range(UPGRADE_FLOOR_SCHEMA, 34):
                    STEPS[version](connection)
                connection.execute("UPDATE attempts SET execution_state='finished',shutdown_confirmed=1,cleanup_pending=0")
                connection.execute("UPDATE tasks SET state='completed',active_attempt_id=NULL")
            source_schema = list(connection.execute(
                "SELECT type,name,tbl_name,sql FROM sqlite_master "
                "WHERE type IN ('table','index') AND name NOT LIKE 'sqlite_%' ORDER BY type,name"))
            source_verifiers = {row['run_id']: row['control_verifier'] for row in connection.execute(
                "SELECT r.run_id,r.control_verifier FROM workflow_runs r JOIN tasks t ON t.task_id=r.run_id "
                "WHERE r.state='accepted' AND t.accepted_at IS NOT NULL")}
            self.assertTrue(source_verifiers)
            self.assertTrue(all(source_verifiers.values()))
            source_seals = dict(connection.execute(
                "SELECT a.artifact_id,a.manifest_json FROM workflow_artifacts a "
                "JOIN workflow_runs r ON r.run_id=a.run_id JOIN tasks t ON t.task_id=r.run_id "
                "WHERE r.state='accepted' AND t.accepted_at IS NOT NULL"))
            self.assertTrue(source_seals)
        before = files(source)
        sample = export_sample(source, self.directory / 'sample34')
        with closing(sqlite3.connect(sample / DB_FILE)) as connection:
            self.assertEqual(connection.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0], '34')
            self.assertEqual([tuple(row) for row in source_schema], list(connection.execute(
                "SELECT type,name,tbl_name,sql FROM sqlite_master "
                "WHERE type IN ('table','index') AND name NOT LIKE 'sqlite_%' ORDER BY type,name")))
            for run_id, verifier, submission in connection.execute(
                    'SELECT run_id,control_verifier,submission_verifier FROM workflow_runs'):
                self.assertNotEqual(verifier, source_verifiers[run_id])
                self.assertEqual(len(verifier), 64)
                self.assertIsNone(submission)
            self.assertEqual(connection.execute('SELECT COUNT(*) FROM agent_credentials').fetchone()[0], 0)
            self.assertEqual(connection.execute('PRAGMA foreign_key_check').fetchall(), [])
            # The floor's seals remain inline after the frozen 33 → 34 step;
            # export and the later upgrade must preserve those exact bytes. A run
            # whose checkout the source never reclaimed stays out of the sample,
            # so only the seals of the selected runs are compared.
            selected_seals = {artifact_id: source_seals[artifact_id] for artifact_id, in
                              connection.execute('SELECT artifact_id FROM workflow_artifacts')}
            self.assertTrue(selected_seals)
            self.assertLess(set(selected_seals), set(source_seals))
            self.assertEqual(dict(connection.execute('SELECT artifact_id,manifest_json FROM workflow_artifacts')),
                             selected_seals)
            self.assertEqual({row[0] for row in connection.execute('SELECT content_form FROM workflow_artifacts')},
                             {'sealed-inline-v1'})
        self.assert_source_unchanged(before, files(source))
        self.assertEqual(upgrade_state(sample), {'sourceSchema': 34, 'targetSchema': SCHEMA_VERSION})
        with closing(sqlite3.connect(sample / DB_FILE)) as connection:
            columns = {row[1] for row in connection.execute('PRAGMA table_info(workflow_runs)')}
            self.assertTrue(columns.isdisjoint({'control_verifier', 'submission_verifier'}))
            self.assertEqual(dict(connection.execute('SELECT artifact_id,manifest_json FROM workflow_artifacts')),
                             selected_seals)
        self.assert_source_unchanged(before, files(source))

    def _schema34_source(self, name):
        """The check suite's synthetic schema 34 source: floor stock plus the chain."""
        from hey_my_buddy.blackboard.store.upgrade import STEPS
        source = load_fixture(self.directory / name)['state']
        with closing(sqlite3.connect(source / DB_FILE)) as connection:
            connection.row_factory = sqlite3.Row
            with connection:
                for version in range(UPGRADE_FLOOR_SCHEMA, 34):
                    STEPS[version](connection)
                connection.execute("UPDATE attempts SET execution_state='finished',shutdown_confirmed=1,cleanup_pending=0")
                connection.execute("UPDATE tasks SET state='completed',active_attempt_id=NULL")
        return source

    def test_schema34_sample_carries_the_source_reclaimed_facts(self):
        source = self._schema34_source('source34-facts')
        with closing(sqlite3.connect(source / DB_FILE)) as connection:
            connection.row_factory = sqlite3.Row
            reclaimed = {row['run_id'] for row in connection.execute(
                "SELECT p.run_id FROM workspace_cleanup_plans p"
                " JOIN workflow_runs r ON r.run_id=p.run_id JOIN tasks t ON t.task_id=r.run_id"
                " WHERE p.state='applied' AND r.state='accepted' AND t.accepted_at IS NOT NULL")}
            # The chain deleted this run's unstarted plan; its checkout is still
            # on disk in the source, so the sample must not carry the run at all.
            unreclaimed = {row['run_id'] for row in connection.execute(
                "SELECT r.run_id FROM workflow_runs r JOIN tasks t ON t.task_id=r.run_id"
                " JOIN workflow_artifacts a ON a.run_id=r.run_id AND a.kind='input'"
                " AND a.workspace_facts_role='owner'"
                " WHERE r.state='accepted' AND t.accepted_at IS NOT NULL AND a.workspace_kind='worktree'"
                " AND r.run_id NOT IN (SELECT run_id FROM workspace_cleanup_plans WHERE state='applied')")}
            self.assertTrue(reclaimed)
            self.assertTrue(unreclaimed)
            applied = {row['plan_id']: dict(row) for row in connection.execute(
                "SELECT * FROM workspace_cleanup_plans WHERE state='applied'")}
        before = files(source)
        sample = export_sample(source, self.directory / 'sample34-facts')
        with closing(sqlite3.connect(sample / DB_FILE)) as connection:
            connection.row_factory = sqlite3.Row
            selected = {row[0] for row in connection.execute('SELECT run_id FROM workflow_runs')}
            self.assertTrue(reclaimed <= selected)
            self.assertTrue(unreclaimed.isdisjoint(selected))
            self.assertEqual({row['plan_id']: dict(row) for row in
                              connection.execute('SELECT * FROM workspace_cleanup_plans')},
                             {plan_id: row for plan_id, row in applied.items() if row['run_id'] in selected})
        self.assert_source_unchanged(before, files(source))
        self.assertEqual(upgrade_state(sample), {'sourceSchema': 34, 'targetSchema': SCHEMA_VERSION})
        with closing(sqlite3.connect(sample / DB_FILE)) as connection:
            connection.row_factory = sqlite3.Row
            self.assertEqual(export_sample.__globals__['_reclamation_candidates'](connection), set())
        self.assert_source_unchanged(before, files(source))

    def test_private_service_opens_a_schema34_sample_without_planning_source_reclamations(self):
        source = self._schema34_source('source34-service')
        before = files(source)
        sample = export_sample(source, self.directory / 'sample34-service')
        self.assertEqual(upgrade_state(sample), {'sourceSchema': 34, 'targetSchema': SCHEMA_VERSION})
        with closing(sqlite3.connect(sample / DB_FILE)) as connection:
            connection.row_factory = sqlite3.Row
            exported_plans = {row['plan_id']: dict(row) for row in
                              connection.execute('SELECT * FROM workspace_cleanup_plans')}
            self.assertTrue(exported_plans)
            self.assertEqual(export_sample.__globals__['_reclamation_candidates'](connection), set())
        # The real private service opens the upgraded sample. Its maintenance
        # pass must plan nothing inside the source state and record no issue;
        # the poll gives the daemon's first pass time to run to completion.
        original_directory = self.directory
        self.directory = sample
        try:
            catalog = write_catalog_fixture(sample)
            with self.daemon(env={'HMB_MODEL_CATALOG_FILE': str(catalog)}):
                deadline = time.monotonic() + 4
                while True:
                    with closing(sqlite3.connect(sample / DB_FILE)) as connection:
                        connection.row_factory = sqlite3.Row
                        plans = {row['plan_id']: dict(row) for row in
                                 connection.execute('SELECT * FROM workspace_cleanup_plans')}
                        issues = [dict(row) for row in connection.execute('SELECT * FROM service_issues')]
                    self.assertEqual(issues, [])
                    self.assertEqual(plans, exported_plans)
                    self.assertTrue(all(row['state'] == 'applied' and row['path'].startswith(str(source))
                                        for row in plans.values()))
                    if time.monotonic() >= deadline:
                        break
                    time.sleep(0.25)
        finally:
            self.directory = original_directory
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

                def capture_reader(reader, source_schema, excluded):
                    readers.append(reader)
                    return original_select(reader, source_schema, excluded)

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
