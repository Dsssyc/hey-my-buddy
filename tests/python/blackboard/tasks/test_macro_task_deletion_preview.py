"""Read-only deletion scope and titles from retained board facts, offline."""
import json
from pathlib import Path
from unittest.mock import patch

from blackboard.tasks import test_workflow as workflow_fixtures, test_workspace as workspace_fixtures
from hey_my_buddy.errors import BoardError
from hey_my_buddy.blackboard.tasks import macro_task_deletion as deletion, macro_tasks, worker_sessions, workflow, workspace


class DeletionPreviewTests(workflow_fixtures.WorkflowTestCase):
    git = workspace_fixtures.WorkspaceTests.git

    def setUp(self):
        super().setUp()
        self.repo = self.workdir('preview-repository')
        workspace_fixtures.WorkspaceTests._seed_repository(self)
        self.enterContext(patch.object(workflow, '_workspace_module', workspace))

    def create(self, board, request_id='preview', *, kind='worktree', **extra):
        return self.submit(board, request_id=request_id, cwd=str(self.repo), kind=kind, **extra)

    def dump(self, board):
        with board.store.db.read() as connection:
            return tuple(connection.iterdump())

    def test_repeated_manifest_references_count_records_checkouts_and_refs_once_without_writes(self):
        board = self.board()
        run = self.create(board, macroTask={'title': 'Scope'})
        with board.store.db.read() as connection:
            scope = deletion._collect(connection, run['macroTaskId'])
        self.assertEqual(len(scope.artifacts), 1)
        self.assertGreater(len(scope.manifests), 1)
        before = self.dump(board)
        allocation = board.directory / 'workspaces' / scope.manifests[0]['workspaceId']
        files = {str(path.relative_to(allocation)): path.read_bytes()
                 for path in allocation.rglob('*') if path.is_file()}
        refs = self.git('for-each-ref', '--format=%(refname) %(objectname)', 'refs/hey-my-buddy/')
        preview = deletion.preview(board.store, run['macroTaskId'])
        self.assertEqual(preview['scope'], {'artifacts': {'count': 1, 'bytes': None},
                                           'sessions': {'count': 0, 'bytes': 0}, 'checkouts': 1, 'gitRefs': 4})
        self.assertEqual(self.dump(board), before)
        self.assertEqual({str(path.relative_to(allocation)): path.read_bytes()
                          for path in allocation.rglob('*') if path.is_file()}, files)
        self.assertEqual(self.git('for-each-ref', '--format=%(refname) %(objectname)', 'refs/hey-my-buddy/'), refs)

    def test_distinct_artifact_records_with_the_same_path_count_separately(self):
        board = self.board()
        run = self.create(board)
        with board.store.db.write() as connection:
            original = dict(connection.execute('SELECT * FROM workflow_artifacts WHERE run_id=?', (run['runId'],)).fetchone())
            duplicate = {**original, 'artifact_id': 'same-path-second-record'}
            columns = tuple(duplicate)
            connection.execute(f"INSERT INTO workflow_artifacts({','.join(columns)}) VALUES({','.join('?' for _ in columns)})",
                               tuple(duplicate.values()))
        scope = deletion.preview(board.store, 'run:' + run['runId'])['scope']
        self.assertEqual(scope, {'artifacts': {'count': 2, 'bytes': None},
                                 'sessions': {'count': 0, 'bytes': 0}, 'checkouts': 1, 'gitRefs': 4})
        # These two rows name one checkout; measuring it twice (or measuring the
        # entire repository) cannot establish an owned artifact byte total.

    def test_existing_checkout_is_not_a_managed_allocation(self):
        board = self.board()
        run = self.create(board, kind='existing')
        scope = deletion.preview(board.store, 'run:' + run['runId'])['scope']
        self.assertEqual(scope['checkouts'], 0)
        self.assertEqual(scope['gitRefs'], 4)

    def test_sealed_output_references_are_deduplicated_across_artifact_rows(self):
        board = self.board()
        run = self.create(board)
        with board.store.db.read() as connection:
            manifest = deletion._collect(connection, 'run:' + run['runId']).manifests[0]
        (Path(manifest['path']) / 'src/file.txt').write_text('sealed output\n')
        artifact = workspace.seal(board.directory, manifest, run['runId'], 'sealed-attempt')
        with board.store.db.write() as connection:
            for identifier in ('sealed-first-record', 'sealed-second-record'):
                connection.execute('INSERT INTO workflow_artifacts(artifact_id,run_id,kind,manifest_json,manifest_sha256,created_at)'
                                   ' VALUES(?,?,?,?,?,?)', (identifier, run['runId'], 'output', json.dumps(artifact),
                                                          artifact['snapshotSha256'], board.store.now()))
        before = self.dump(board)
        self.assertEqual(deletion.preview(board.store, 'run:' + run['runId'])['scope'], {
            'artifacts': {'count': 3, 'bytes': None}, 'sessions': {'count': 0, 'bytes': 0}, 'checkouts': 1, 'gitRefs': 6})
        self.assertEqual(self.dump(board), before)

    def test_unproven_allocation_makes_whole_checkout_count_unknown(self):
        board = self.board()
        first = self.create(board, macroTask={'title': 'Incomplete allocations'})
        self.create(board, 'second', macroTaskId=first['macroTaskId'])
        with board.store.db.read() as connection:
            manifest = json.loads(connection.execute('SELECT workspace_manifest_json FROM workflow_runs WHERE run_id=?',
                                                     (first['runId'],)).fetchone()[0])
        (board.directory / 'workspaces' / manifest['workspaceId'] / 'manifest.json').unlink()
        scope = deletion.preview(board.store, first['macroTaskId'])['scope']
        self.assertIsNone(scope['checkouts'])
        self.assertEqual(scope['gitRefs'], 8)

    def test_empty_retained_scope_is_zero(self):
        board = self.board()
        with board.store.db.write() as connection:
            connection.execute('INSERT INTO macro_tasks(macro_task_id,title,project_id,project_path,source_host_id,created_at,activity_at)'
                               ' VALUES(?,?,?,?,?,?,?)', ('macro-empty', 'Empty scope', 'private-project', str(self.repo),
                                                         'host-1', board.store.now(), board.store.now()))
        before = self.dump(board)
        self.assertEqual(deletion.preview(board.store, 'macro-empty')['scope'], {
            'artifacts': {'count': 0, 'bytes': 0}, 'sessions': {'count': 0, 'bytes': 0}, 'checkouts': 0, 'gitRefs': 0})
        self.assertEqual(self.dump(board), before)

    def test_conflicting_retained_bindings_make_whole_reference_count_unknown(self):
        board = self.board()
        run = self.create(board)
        with board.store.db.read() as connection:
            collected = deletion._collect(connection, 'run:' + run['runId'])
        conflicting = json.loads(json.dumps(collected.manifests[0]))
        conflicting['targetRef'] = 'refs/heads/another-target'
        conflicting['manifestSha256'] = workspace._sha(workspace._json({key: value for key, value in conflicting.items()
                                                                     if key != 'manifestSha256'}))
        workspace._validate_manifest(conflicting)
        collected.manifests.append(conflicting)
        self.assertIsNone(deletion._scope_summary(board.store, collected)['gitRefs'])

    def test_session_count_uses_distinct_native_identities_without_native_reads(self):
        board = self.board()
        run = self.create(board)
        session_id = '11111111-1111-4111-8111-111111111111'
        now = board.store.now()
        with board.store.db.write() as connection:
            for generation, mode in ((1, 'initial'), (2, 'native-session')):
                attempt_id = f'session-attempt-{generation}'
                receipt = {'result': {'nativeSession': {'harness': 'codex', 'sessionId': session_id,
                            'captured': True, 'storageOwner': 'harness-user-store', 'resumeMode': mode},
                            'nativeCheckpoint': {'version': 1, 'sessionId': session_id,
                            'taskId': run['runId'], 'attemptId': attempt_id}}}
                connection.execute('INSERT INTO attempts(attempt_id,task_id,generation,nonce_verifier,execution_state,'
                                   'adapter,shutdown_confirmed,result_json,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)',
                                   (attempt_id, run['runId'], generation, 'fixture', 'finished', 'codex', 1,
                                    json.dumps(receipt), now, now))
        inventory = worker_sessions.deletable_sessions(board.store, {run['runId']})
        # Also repeat a task binding to make the projection's identity rule
        # independent of the inventory's existing per-task deduplication.
        with patch.object(worker_sessions, 'deletable_sessions', return_value=inventory * 2):
            before = self.dump(board)
            scope = deletion.preview(board.store, 'run:' + run['runId'])['scope']
            self.assertEqual(scope['sessions'], {'count': 1, 'bytes': None})
            self.assertEqual(self.dump(board), before)

    def test_deleted_target_preview_is_not_found_without_writes(self):
        board = self.board()
        run = self.create(board)
        board.call('workflow_cancel', {'runId': run['runId'], **self.control(run)})
        target = 'run:' + run['runId']
        self.assertEqual(deletion.delete(board.store, target, 1)['state'], 'deleted')
        before = self.dump(board)
        with self.assertRaises(BoardError) as caught:
            deletion.preview(board.store, target)
        self.assertEqual(caught.exception.code, 'NOT_FOUND')
        self.assertEqual(self.dump(board), before)

    def test_blockers_use_the_same_stored_title_and_task_rules_without_loaded_timeline_rows(self):
        board = self.board()
        first = self.create(board, macroTask={'title': 'Blocker names'}, title='  Authored name  ')
        second = self.create(board, 'task-line', macroTaskId=first['macroTaskId'], task='  Task   first line\nLater line')
        third = self.create(board, 'no-name', macroTaskId=first['macroTaskId'])
        with board.store.db.write() as connection:
            connection.execute("UPDATE workflow_runs SET micro_task_json=json_set(micro_task_json,'$.task','') WHERE run_id=?",
                               (third['runId'],))
        before = self.dump(board)
        preview = deletion.preview(board.store, first['macroTaskId'])
        names = {first['runId']: ('Authored name', 'title'), second['runId']: ('Task first line', 'task'),
                 third['runId']: (None, 'none')}
        self.assertEqual({row['runId'] for row in preview['blockers']}, set(names))
        for blocker in preview['blockers']:
            self.assertEqual((blocker['title'], blocker['titleSource']), names[blocker['runId']])
            with board.store.db.read() as connection:
                self.assertEqual((blocker['title'], blocker['titleSource']),
                                 macro_tasks._run_presentation(connection, blocker['runId'])[:2])
        self.assertEqual(self.dump(board), before)

    def test_routing_execution_without_a_workflow_title_has_explicit_unknown_name(self):
        board = self.board()
        run = self.create(board)
        with board.store.db.read() as connection:
            scope = deletion._collect(connection, 'run:' + run['runId'])
            route_task = dict(scope.task_rows[0])
            route_task['task_id'] = 'route-without-title'
            scope.task_rows.append(route_task)
            blockers = deletion._blockers(board.store, connection, scope)
        routing = [row for row in blockers if row['taskId'] == 'route-without-title']
        self.assertEqual(len(routing), 1)
        self.assertEqual((routing[0]['runId'], routing[0]['title'], routing[0]['titleSource']), (None, None, 'none'))
