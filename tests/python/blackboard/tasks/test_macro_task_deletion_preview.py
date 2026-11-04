"""Read-only deletion scope from retained board and current Git facts, offline."""
import json
from pathlib import Path
from unittest.mock import patch

from blackboard.tasks import test_workflow as workflow_fixtures, test_workspace as workspace_fixtures
from hey_my_buddy.errors import BoardError
from hey_my_buddy.blackboard.tasks import macro_task_deletion as deletion, macro_tasks, worker_sessions, workflow, workspace
from hey_my_buddy.paths import native_path
from hey_my_buddy.cli.main import _creation_key


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
        run = self.create(board, create_macro={'title': 'Scope', 'goal': 'Scope'})
        with board.store.db.read() as connection:
            scope = deletion._collect(connection, run['macroTaskId'], board.directory)
        self.assertEqual(len(scope.artifacts), 1)
        self.assertEqual(len(scope.manifests), 1)
        before = self.dump(board)
        allocation = board.directory / 'workspaces' / scope.manifests[0]['workspace_id']
        files = {str(path.relative_to(native_path(allocation))): path.read_bytes()
                 for path in native_path(allocation).rglob('*') if path.is_file()}
        refs = self.git('for-each-ref', '--format=%(refname) %(objectname)', 'refs/hey-my-buddy/')
        preview = deletion.preview(board.store, run['macroTaskId'])
        self.assertEqual(preview['scope'], {'artifacts': {'count': 1, 'bytes': None},
                                           'sessions': {'count': 0, 'bytes': 0}, 'checkouts': 1, 'gitRefs': 4})
        self.assertEqual(self.dump(board), before)
        self.assertEqual({str(path.relative_to(native_path(allocation))): path.read_bytes()
                          for path in native_path(allocation).rglob('*') if path.is_file()}, files)
        self.assertEqual(self.git('for-each-ref', '--format=%(refname) %(objectname)', 'refs/hey-my-buddy/'), refs)

    def test_distinct_artifact_records_with_the_same_path_count_separately(self):
        board = self.board()
        run = self.create(board)
        with board.store.db.write() as connection:
            original = dict(connection.execute('SELECT * FROM workflow_artifacts WHERE run_id=?', (run['runId'],)).fetchone())
            duplicate = {**original, 'artifact_id': 'same-path-second-record',
                         'workspace_facts_role': 'reference', 'workspace_kind': None,
                         'checkout_root': None, 'repository_path': None,
                         'input_commit': None, 'staged_commit': None}
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

    def test_snapshot_dedup_counts_only_existing_worker_and_host_refs(self):
        board = self.board()
        for paired in (False, True):
            with self.subTest(paired=paired):
                run = self.create(board, request_id='paired-' + str(paired))
                with board.store.db.read() as connection:
                    from hey_my_buddy.blackboard.tasks.workspace_bindings import effective_manifest
                    row = connection.execute('SELECT * FROM workflow_runs WHERE run_id=?', (run['runId'],)).fetchone()
                    manifest = effective_manifest(board.directory, connection, row)
                worker = workspace.seal(board.directory, manifest, run['runId'], 'equal-snapshot')
                with board.store.db.write() as connection:
                    first = board.store.workflow._pin_artifact(
                        connection, run_id=run['runId'], kind='output', manifest=worker,
                        attempt_id='equal-snapshot', turn_id=None, now=board.store.now())
                if paired:
                    host = workspace.host_seal(board.directory, manifest, run['runId'], 'equal-snapshot')
                    self.assertEqual(worker['snapshotSha256'], host['snapshotSha256'])
                    with board.store.db.write() as connection:
                        second = board.store.workflow._pin_artifact(
                            connection, run_id=run['runId'], kind='partial-output', manifest=host,
                            attempt_id='equal-snapshot', turn_id=None, now=board.store.now())
                    self.assertEqual(first, second)
                before = self.dump(board)
                count = deletion.preview(board.store, 'run:' + run['runId'])['scope']['gitRefs']
                actual = self.git('for-each-ref', '--format=%(refname)',
                                  f"refs/hey-my-buddy/workspaces/{manifest['workspaceId']}/")
                self.assertEqual(count, 8 if paired else 6)
                self.assertEqual(count, len(actual.splitlines()))
                self.assertEqual(self.dump(board), before)

    def test_reclaimed_checkout_reference_count_uses_known_relocated_repository(self):
        board = self.board()
        run = self.create(board)
        with board.store.db.read() as connection:
            from hey_my_buddy.blackboard.tasks.workspace_bindings import effective_manifest
            row = connection.execute("SELECT * FROM workflow_runs WHERE run_id=?", (run["runId"],)).fetchone()
            manifest = effective_manifest(board.directory, connection, row)
        proof = workspace.deletion_inspect(board.directory, manifest)
        workspace.cleanup_remove(board.directory, manifest, proof=proof)
        self.assertFalse(Path(manifest["checkoutRoot"]).exists())
        moved = self.workdir("moved-preview-repository")
        moved.rmdir()
        self.repo.rename(moved)
        self.repo = moved
        self.create(board, request_id="project-at-new-location", kind="existing")
        before = self.dump(board)
        preview = deletion.preview(board.store, "run:" + run["runId"])
        self.assertEqual(preview["scope"]["gitRefs"], 4)
        self.assertFalse(Path(manifest["checkoutRoot"]).exists())
        self.assertEqual(self.dump(board), before)

    def test_missing_manifest_does_not_change_sql_allocation_count(self):
        board = self.board()
        first = self.create(board, create_macro={'title': 'Incomplete allocations', 'goal': 'Incomplete allocations'})
        self.create(board, 'second', macroTaskId=first['macroTaskId'])
        with board.store.db.read() as connection:
            from hey_my_buddy.blackboard.tasks.workspace_bindings import effective_manifest
            row = connection.execute('SELECT * FROM workflow_runs WHERE run_id=?', (first['runId'],)).fetchone()
            manifest = effective_manifest(board.directory, connection, row)
        (board.directory / 'workspaces' / manifest['workspaceId'] / 'manifest.json').unlink()
        scope = deletion.preview(board.store, first['macroTaskId'])['scope']
        self.assertEqual(scope['checkouts'], 2)

    def test_empty_retained_scope_is_zero(self):
        board = self.board()
        project_id = workspace.ensure_project_id(workspace.repository_at(self.repo, allow_parent=True))
        created = board.call('workflow_create', {
            'cwd': str(self.repo), 'title': 'Empty scope', 'goal': 'Empty scope',
            'creationKey': _creation_key(['adr032-create-v1', project_id, 'Empty scope']),
        })
        before = self.dump(board)
        self.assertEqual(deletion.preview(board.store, created['macroTaskId'])['scope'], {
            'artifacts': {'count': 0, 'bytes': 0}, 'sessions': {'count': 0, 'bytes': 0}, 'checkouts': 0, 'gitRefs': 0})
        self.assertEqual(self.dump(board), before)

    def test_conflicting_retained_bindings_make_whole_reference_count_unknown(self):
        board = self.board()
        run = self.create(board)
        with board.store.db.read() as connection:
            collected = deletion._collect(connection, 'run:' + run['runId'], board.directory)
        conflicting = json.loads(json.dumps(collected.manifests[0]))
        conflicting['input_commit'] = 'f' * 40
        collected.manifests.append(conflicting)
        with board.store.db.read() as connection:
            self.assertIsNone(deletion._scope_summary(board.store, collected, connection)['gitRefs'])

    def test_conflicting_output_commits_make_the_reference_count_unknown(self):
        board = self.board()
        run = self.create(board)
        with board.store.db.read() as connection:
            collected = deletion._collect(connection, 'run:' + run['runId'], board.directory)
        manifest = collected.manifests[0]
        workspace_id = manifest['workspace_id']
        output = {'run_id': run['runId'], 'attempt_id': 'conflicting-output', 'kind': 'output',
                  'output_origin': 'worker', 'output_action': None,
                  'output_workspace_id': workspace_id,
                  'output_manifest_sha256': manifest['manifest_sha256'],
                  'output_commit': 'a' * 40}
        conflicting = {**output, 'output_commit': 'b' * 40}
        collected.artifacts.extend([output, conflicting])
        with board.store.db.read() as connection:
            self.assertIsNone(deletion._scope_summary(board.store, collected, connection)['gitRefs'])

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
                                   'adapter,shutdown_confirmed,result_json,result_form,created_at,updated_at)'
                                   ' VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                                   (attempt_id, run['runId'], generation, 'fixture', 'finished', 'codex', 1,
                                    json.dumps(receipt), 'archived-report-v1', now, now))
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
        board.call('workflow_cancel', {'runId': run['runId']})
        target = 'run:' + run['runId']
        self.assertEqual(deletion.delete(board.store, target, 1)['state'], 'deleted')
        before = self.dump(board)
        with self.assertRaises(BoardError) as caught:
            deletion.preview(board.store, target)
        self.assertEqual(caught.exception.code, 'NOT_FOUND')
        self.assertEqual(self.dump(board), before)

    def test_blockers_use_the_same_stored_title_and_task_rules_without_loaded_timeline_rows(self):
        board = self.board()
        first = self.create(board, create_macro={'title': 'Blocker names', 'goal': 'Blocker names'}, title='  Authored name  ')
        second = self.create(board, 'task-line', macroTaskId=first['macroTaskId'], task='  Task   first line\nLater line')
        third = self.create(board, 'no-name', macroTaskId=first['macroTaskId'])
        with board.store.db.write() as connection:
            connection.execute('UPDATE workflow_runs SET title=NULL WHERE run_id IN (?,?)',
                               (second['runId'], third['runId']))
            connection.execute("UPDATE tasks SET spec_json=json_set(spec_json,'$.task','') WHERE task_id=?",
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
                                 macro_tasks._run_title(connection, blocker['runId']))
        self.assertEqual(self.dump(board), before)

    def test_routing_execution_without_a_workflow_title_has_explicit_unknown_name(self):
        board = self.board()
        run = self.create(board)
        with board.store.db.read() as connection:
            scope = deletion._collect(connection, 'run:' + run['runId'], board.directory)
            route_task = dict(scope.task_rows[0])
            route_task['task_id'] = 'route-without-title'
            scope.task_rows.append(route_task)
            blockers = deletion._blockers(board.store, connection, scope)
        routing = [row for row in blockers if row['taskId'] == 'route-without-title']
        self.assertEqual(len(routing), 1)
        self.assertEqual((routing[0]['runId'], routing[0]['title'], routing[0]['titleSource']), (None, None, 'none'))
