"""Shared-object eligibility through real workspace admission and ownership."""
import json
import shutil
import sqlite3
import tempfile
from unittest.mock import patch as defer_reclaim
from pathlib import Path
from types import SimpleNamespace

from blackboard.tasks import workspace_lifecycle_case as lifecycle_fixtures
from blackboard.tasks.test_workflow_real import BUDDY, RealWorkspaceTestCase
from hey_my_buddy.blackboard.tasks import macro_task_deletion as deletion, workspace, workflow
from hey_my_buddy.blackboard.store.store import BoardStore
from hey_my_buddy import private_dirs
from hey_my_buddy.buddy.harnesses import registry as harness_registry
from hey_my_buddy.errors import BoardError
from hey_my_buddy.cli.main import _creation_key
from support import native_exists, native_is_dir, native_is_file, native_read_bytes, native_unlink, native_write_bytes


class DeletionSharingTests(RealWorkspaceTestCase):
    claim = lifecycle_fixtures.LifecycleTestCase.claim
    turn_record = lifecycle_fixtures.LifecycleTestCase.turn_record
    finish_turn = lifecycle_fixtures.LifecycleTestCase.finish_turn
    view = lifecycle_fixtures.LifecycleTestCase.view
    accept = lifecycle_fixtures.LifecycleTestCase.accept

    def setUp(self):
        super().setUp()
        self.worker = 'w-deletion-sharing'
        original = workflow._workspace_module
        workflow._workspace_module = workspace
        self.addCleanup(setattr,workflow,'_workspace_module',original)

    def submit_group(self,board,request_id,*,kind='existing',cwd=None,write_scope=None):
        cwd = str(cwd or self.repo)
        project_id = workspace.ensure_project_id(workspace.repository_at(cwd, allow_parent=True))
        created = board.call('workflow_create', {'cwd': cwd, 'title': request_id, 'goal': request_id,
            'creationKey': _creation_key(['adr032-create-v1', project_id, request_id])})
        task = 'Private shared-object fixture'
        run = board.call('workflow_submit',{
            'cwd': cwd, 'title': request_id, 'task': task, 'macroTaskId': created['macroTaskId'],
            'creationKey': _creation_key(['adr032-submit-v1', 'macro:' + created['macroTaskId'], request_id, task]),
            'buddy': ':'.join(BUDDY.values()), 'checkout': kind, 'access': 'write',
            'inputs': [], 'writeScope': write_scope or ['.'], 'expectedSeconds': 1800,
        })
        return {**board.call('workflow_get', {'runId': run['runId']}), **run}

    def close(self,board,run):
        board.call('workflow_cancel',{'runId':run['runId']})
        return board.call('workflow_conclude',{'runId':run['runId'],'note':'No execution was admitted'})

    def test_two_existing_groups_on_the_same_checkout_can_both_be_deleted(self):
        board = self.board()
        initial = self.state()
        first = self.submit_group(board,'host-existing-first')
        self.close(board,first)
        second = self.submit_group(board,'host-existing-second')
        self.close(board,second)
        for run in (first,second):
            self.assertEqual(deletion.preview(board.store,run['macroTaskId'])['blockerTotal'],0)
            result = deletion.delete(board.store,run['macroTaskId'],1)
            self.assertEqual(result['state'],'deleted',result)
        self.assertEqual(self.state(),initial)
        self.assertTrue((self.repo/'tracked.txt').is_file())
        with board.store.db.read() as connection:
            self.assertEqual(connection.execute('PRAGMA foreign_key_check').fetchall(),[])

    def test_deletion_uses_checkout_repository_when_project_is_copied(self):
        board = self.board()
        first = self.submit_group(board, 'original-project')
        self.close(board, first)
        copied = self.directory / 'copied-project'
        shutil.copytree(self.repo, copied)
        second = self.submit_group(board, 'copied-project', cwd=copied)
        self.close(board, second)
        with board.store.db.read() as connection:
            identifier = connection.execute(
                "SELECT workspace_id FROM workflow_artifacts WHERE run_id=? AND kind='input'",
                (second['runId'],)).fetchone()[0]
        prefix = f'refs/hey-my-buddy/workspaces/{identifier}/'
        with defer_reclaim.object(self, 'repo', copied):
            before = self.git('for-each-ref', '--format=%(refname)', prefix)
        self.assertTrue(before)
        result = deletion.delete(board.store, second['macroTaskId'], 1)
        self.assertEqual(result['state'], 'deleted', result)
        self.assertFalse(native_exists(board.directory / 'workspaces' / identifier))
        with defer_reclaim.object(self, 'repo', copied):
            self.assertFalse(self.git('for-each-ref', '--format=%(refname)', prefix))

    def test_retry_after_workspace_records_removed_by_an_earlier_phase(self):
        board = self.board()
        run = self.submit_group(board, 'retry-after-workspace')
        self.close(board, run)
        with board.store.db.read() as connection:
            workspace_id = connection.execute(
                "SELECT workspace_id FROM workflow_artifacts WHERE run_id=? AND kind='input'",
                (run['runId'],)).fetchone()[0]
        with defer_reclaim.object(deletion, '_delete_private_roots', side_effect=OSError('later phase interrupted')):
            interrupted = deletion.delete(board.store, run['macroTaskId'], 1)
        self.assertEqual(interrupted['state'], 'deleting')
        self.assertFalse(native_exists(board.directory / 'workspaces' / workspace_id))
        completed = deletion.delete(board.store, run['macroTaskId'], 1)
        self.assertEqual(completed['state'], 'deleted')

    def test_rebound_inline_board_without_workspace_files_cannot_delete_origin_refs(self):
        origin = self.board()
        run = self.submit_group(origin, 'historic-inline-origin', kind='worktree')
        claim = self.claim(origin, run['runId'])
        from hey_my_buddy.protocol.workspace_manifest import turn_manifest
        prepared = turn_manifest(origin.directory, claim['claim']['turn']['input'])
        seal = workspace.seal(origin.directory, prepared, run['runId'],
                              claim['claim']['attempt']['attemptId'])
        self.finish_turn(origin, claim, seal)
        view = self.view(origin, run['runId'])
        artifact = next(item for item in view['artifacts'] if item['kind'] == 'output')
        with defer_reclaim.object(origin.store.workflow, '_reclaim_checkout',
                                 return_value={'removed': False, 'reasons': ['fixture-retained']}):
            self.accept(origin, view, artifact, not_required=True)
        with origin.store.db.read() as connection:
            pair = connection.execute(
                "SELECT workspace_id,input_manifest_sha256 FROM workflow_artifacts"
                " WHERE run_id=? AND kind='input'", (run['runId'],)).fetchone()
        from hey_my_buddy.protocol.workspace_manifest import resolve_manifest
        manifest = resolve_manifest(origin.directory, {'workspaceId': pair['workspace_id'],
                                                       'manifestSha256': pair['input_manifest_sha256']})
        checkout = Path(manifest['checkoutRoot'])
        self.assertTrue(checkout.is_dir())
        prefix = f"refs/hey-my-buddy/workspaces/{manifest['workspaceId']}/"
        before_refs = self.git('for-each-ref', '--format=%(refname)%00%(objectname)', prefix)
        self.assertTrue(before_refs)
        temporary = tempfile.TemporaryDirectory(prefix='hey-my-buddy-old-inline-sample-')
        self.addCleanup(temporary.cleanup)
        sample_state = Path(temporary.name) / 'state'
        sample_state.mkdir(mode=0o700)
        with origin.store.db.read() as source, sqlite3.connect(sample_state / 'board.sqlite3') as copy:
            source.backup(copy)
        (sample_state / 'board.sqlite3').chmod(0o600)
        sample = BoardStore(sample_state)
        self.addCleanup(sample.db.close)
        sample.initialize()
        with sample.db.write() as connection:
            connection.execute(
                "UPDATE workflow_artifacts SET content_form='sealed-inline-v1',manifest_json=?"
                " WHERE run_id=? AND kind='input'",
                (json.dumps(manifest), run['runId']))
        self.assertFalse(native_exists(sample_state / 'workspaces' / manifest['workspaceId']))
        result = deletion.delete(sample, run['macroTaskId'], 1)
        self.assertEqual(result['state'], 'deleting', result)
        self.assertIn('macro-task-deletion.owner-unproven',
                      [item['reason']['code'] for item in result['failures']])
        with sample.db.read() as connection:
            self.assertIsNotNone(connection.execute('SELECT 1 FROM workflow_runs WHERE run_id=?',
                                                    (run['runId'],)).fetchone())
        self.assertTrue(checkout.is_dir())
        self.assertEqual(self.git('for-each-ref', '--format=%(refname)%00%(objectname)', prefix), before_refs)

    def test_each_physical_phase_retries_after_store_restart_with_real_git(self):
        stages = ('_delete_sessions', '_delete_workspaces', '_delete_private_roots',
                  '_delete_session_group', '_delete_rows')
        for index, stage in enumerate(stages):
            with self.subTest(stage=stage):
                board = self.board()
                run = self.submit_group(board, f'restart-phase-{index}', kind='worktree')
                self.close(board, run)
                with board.store.db.read() as connection:
                    workspace_id = connection.execute(
                        "SELECT workspace_id FROM workflow_artifacts WHERE run_id=? AND kind='input'",
                        (run['runId'],)).fetchone()[0]
                directory = board.directory / 'workspaces' / workspace_id
                frozen = native_read_bytes(directory / 'manifest.json')
                own_prefix = f'refs/hey-my-buddy/workspaces/{workspace_id}/'
                self.assertTrue(self.git('for-each-ref', '--format=%(refname)', own_prefix))
                attempts = board.directory / 'attempts' / run['runId']
                attempts.mkdir(parents=True, exist_ok=True)
                (attempts / 'phase-proof').write_bytes(b'attempt evidence')
                sessions = private_dirs.session_group_root(board.directory, run['macroTaskId'])
                sessions.mkdir(parents=True, exist_ok=True)
                (sessions / 'phase-proof').write_bytes(b'session evidence')
                native = self.directory / f'native-session-{index}.jsonl'
                foreign_native = self.directory / f'foreign-native-session-{index}.jsonl'
                native.write_bytes(b'owned native session evidence')
                foreign_native.write_bytes(b'foreign native session evidence')
                attempt_id = f'native-phase-attempt-{index}'
                session_id = f'native-phase-session-{index}'
                receipt = {'result': {'nativeSession': {
                    'harness': 'codex', 'sessionId': session_id, 'captured': True,
                    'storageOwner': 'harness-user-store', 'resumeMode': 'initial'},
                    'nativeCheckpoint': {'version': 1, 'sessionId': session_id,
                                         'taskId': run['runId'], 'attemptId': attempt_id}}}
                with board.store.db.write() as connection:
                    connection.execute(
                        "INSERT INTO attempts(attempt_id,task_id,generation,nonce_verifier,execution_state,"
                        "adapter,shutdown_confirmed,result_json,result_form,created_at,updated_at)"
                        " VALUES(?,?,1,'fixture','finished','codex',1,?,'archived-report-v1',?,?)",
                        (attempt_id, run['runId'], json.dumps(receipt), board.store.now(), board.store.now()))

                def delete_native(ids, *, state_dir):
                    # Substitute only the native app boundary; every selected
                    # identity and phase, SQL fence and physical file are real.
                    self.assertEqual(ids, [session_id])
                    self.assertEqual(state_dir, board.directory)
                    present = native.exists()
                    native.unlink(missing_ok=True)
                    return {session_id: 'deleted' if present else 'already-gone'}

                with defer_reclaim.object(harness_registry, 'adapter',
                        return_value=SimpleNamespace(delete_sessions=delete_native)):
                    foreign = f'refs/hey-my-buddy/workspaces/ws-{"f" * 32}/input'
                    head = self.git('rev-parse', 'HEAD').strip()
                    self.git('update-ref', foreign, head)
                    with defer_reclaim.object(deletion, stage, side_effect=OSError('injected phase stop')):
                        if stage == '_delete_rows':
                            with self.assertRaisesRegex(OSError, 'injected phase stop'):
                                deletion.delete(board.store, run['macroTaskId'], 1)
                        else:
                            interrupted = deletion.delete(board.store, run['macroTaskId'], 1)
                            self.assertEqual(interrupted['state'], 'deleting', interrupted)
                    workspace_gone = stages.index(stage) > stages.index('_delete_workspaces')
                    self.assertEqual(native.exists(), stage == '_delete_sessions')
                    self.assertEqual(foreign_native.read_bytes(), b'foreign native session evidence')
                    attempts_gone = stages.index(stage) > stages.index('_delete_private_roots')
                    sessions_gone = stages.index(stage) > stages.index('_delete_session_group')
                    self.assertEqual(native_exists(directory), not workspace_gone)
                    if not workspace_gone:
                        self.assertEqual(native_read_bytes(directory / 'manifest.json'), frozen)
                    self.assertEqual(bool(self.git('for-each-ref', '--format=%(refname)', own_prefix)),
                                     not workspace_gone)
                    self.assertEqual((attempts / 'phase-proof').exists(), not attempts_gone)
                    self.assertEqual((sessions / 'phase-proof').exists(), not sessions_gone)
                    self.assertEqual(self.git('rev-parse', foreign).strip(), head)
                    with board.store.db.read() as connection:
                        self.assertIsNotNone(connection.execute('SELECT 1 FROM macro_task_deletions WHERE target_id=?',
                                                                (run['macroTaskId'],)).fetchone())
                        self.assertIsNotNone(connection.execute('SELECT 1 FROM workflow_runs WHERE run_id=?',
                                                                (run['runId'],)).fetchone())
                    board.store.db.close()
                    resumed = BoardStore(board.directory)
                    self.addCleanup(resumed.db.close)
                    resumed.initialize()
                    done = deletion.delete(resumed, run['macroTaskId'], 1)
                    self.assertEqual(done['state'], 'deleted', done)
                    self.assertFalse(native_exists(directory))
                    self.assertFalse(attempts.exists())
                    self.assertFalse(sessions.exists())
                    self.assertFalse(native.exists())
                    self.assertEqual(foreign_native.read_bytes(), b'foreign native session evidence')
                    self.assertFalse(self.git('for-each-ref', '--format=%(refname)', own_prefix))
                    self.assertEqual(self.git('rev-parse', foreign).strip(), head)

    def test_borrower_deletes_only_its_output_in_a_foreign_workspace_namespace(self):
        board = self.board()
        owner = self.submit_group(board, 'foreign-output-owner', kind='worktree')
        with board.store.db.read() as connection:
            owner_input = connection.execute(
                "SELECT workspace_id,input_manifest_sha256 FROM workflow_artifacts"
                " WHERE run_id=? AND kind='input'", (owner['runId'],)).fetchone()
        from hey_my_buddy.protocol.workspace_manifest import resolve_manifest
        manifest = resolve_manifest(board.directory, {'workspaceId': owner_input['workspace_id'],
                                                     'manifestSha256': owner_input['input_manifest_sha256']})
        (self.repo / 'src' / 'feature.py').write_text('value = 2\n', encoding='utf-8', newline='\n')
        self.git('add', '.')
        self.git('commit', '-q', '-m', 'new borrower baseline')
        borrower = self.submit_group(board, 'foreign-output-borrower', kind='existing')
        output = workspace.seal(board.directory, manifest, borrower['runId'], 'foreign-output-attempt')
        with board.store.db.read() as connection:
            borrower_commit = connection.execute(
                "SELECT input_commit FROM workflow_artifacts WHERE run_id=? AND kind='input'",
                (borrower['runId'],)).fetchone()[0]
        self.assertNotEqual(borrower_commit, output['inputCommit'])
        cumulative = workspace.cumulative_patch(manifest, output, borrower_commit)
        self.assertTrue(native_is_file(Path(cumulative['path'])))
        host_output = workspace.host_seal(board.directory, manifest, borrower['runId'], 'foreign-output-attempt')
        self.assertEqual(output['snapshotSha256'], host_output['snapshotSha256'])
        with board.store.db.write() as connection:
            output_id = board.store.workflow._pin_artifact(connection, run_id=borrower['runId'], kind='output',
                                                           manifest=output, attempt_id='foreign-output-attempt', turn_id=None,
                                                           now=board.store.now())
            host_id = board.store.workflow._pin_artifact(connection, run_id=borrower['runId'], kind='partial-output',
                                                         manifest=host_output, attempt_id='foreign-output-attempt', turn_id=None,
                                                         now=board.store.now())
            self.assertEqual(output_id, host_id)
        with self.assertRaises(BoardError) as caught:
            deletion.preview(board.store, owner['macroTaskId'])
        self.assertEqual(caught.exception.code, 'MACRO_TASK_STORAGE_IN_USE')
        with board.store.db.write() as connection:
            board.store.workflow._pin_artifact(connection, run_id=borrower['runId'], kind='input',
                                               manifest=manifest, attempt_id=None, turn_id=None,
                                               now=board.store.now())
        self.close(board, borrower)
        workspace_dir = board.directory / 'workspaces' / manifest['workspaceId']
        output_file = workspace_dir / 'outputs' / workspace._sha(workspace._json(
            [borrower['runId'], 'foreign-output-attempt'])) / 'output.json'
        self.assertTrue(native_is_file(output_file))
        host_file = output_file.parent.with_name(output_file.parent.name + '-host') / 'output.json'
        self.assertTrue(native_is_file(host_file))
        workspace._record_scope_evidence(output_file.parent, {'observedFingerprint': 'fixture-conflict'},
                                         borrower['runId'], 'foreign-output-attempt')
        conflicts = output_file.parent / 'scope-conflicts'
        self.assertTrue(native_is_dir(conflicts))
        unknown = output_file.parent / 'unproven.bin'
        native_write_bytes(unknown, b'foreign content')
        self.assertEqual(self.git('rev-parse', output['ref']).strip(), output['commit'])
        blocked = deletion.delete(board.store, borrower['macroTaskId'], 1)
        self.assertEqual(blocked['state'], 'deleting', blocked)
        self.assertIn('macro-task-deletion.dependency-retained',
                      [item['reason']['code'] for item in blocked['failures']])
        self.assertTrue(native_is_file(unknown))
        with board.store.db.read() as connection:
            self.assertIsNotNone(connection.execute('SELECT 1 FROM workflow_runs WHERE run_id=?',
                                                    (borrower['runId'],)).fetchone())
        native_unlink(unknown)
        deleted = deletion.delete(board.store, borrower['macroTaskId'], 1)
        self.assertEqual(deleted['state'], 'deleted', deleted)
        self.assertFalse(native_exists(output_file))
        self.assertFalse(native_exists(Path(cumulative['path'])))
        self.assertFalse(native_exists(output_file.parent))
        self.assertFalse(native_exists(conflicts))
        self.assertFalse(native_exists(host_file))
        self.assertFalse(native_exists(host_file.parent))
        self.assertNotIn(output['ref'], self.git('for-each-ref', '--format=%(refname)', output['ref']))
        self.assertNotIn(host_output['ref'], self.git('for-each-ref', '--format=%(refname)', host_output['ref']))
        self.assertTrue(native_is_dir(workspace_dir))
        self.assertTrue(Path(manifest['checkoutRoot']).is_dir())
        self.assertEqual(self.git('rev-parse', manifest['snapshot']['inputRef']).strip(), manifest['inputCommit'])
        self.close(board, owner)
        deleted = deletion.delete(board.store, owner['macroTaskId'], 1)
        self.assertEqual(deleted['state'], 'deleted', deleted)

    def test_foreign_resolution_and_output_share_directory_until_both_are_removed(self):
        board = self.board()
        owner = self.submit_group(board, 'resolution-owner', kind='worktree',
                                  write_scope=['tracked.txt'])
        with board.store.db.read() as connection:
            pair = connection.execute(
                "SELECT workspace_id,input_manifest_sha256 FROM workflow_artifacts"
                " WHERE run_id=? AND kind='input'", (owner['runId'],)).fetchone()
        from hey_my_buddy.protocol.workspace_manifest import resolve_manifest
        manifest = resolve_manifest(board.directory, {'workspaceId': pair['workspace_id'],
                                                     'manifestSha256': pair['input_manifest_sha256']})
        (self.repo / 'src' / 'feature.py').write_text('value = 3\n', encoding='utf-8', newline='\n')
        self.git('add', '.')
        self.git('commit', '-q', '-m', 'new resolution borrower baseline')
        borrower = self.submit_group(board, 'resolution-borrower', kind='existing')
        with board.store.db.read() as connection:
            initial = connection.execute(
                "SELECT input_commit FROM workflow_artifacts WHERE run_id=? AND kind='input'",
                (borrower['runId'],)).fetchone()[0]
        attempt_id = 'foreign-resolution-attempt'
        checkout = Path(manifest['checkoutRoot'])
        (checkout / 'src' / 'foreign.txt').write_text('outside scope\n', encoding='utf-8', newline='\n')
        with self.assertRaises(BoardError) as caught:
            workspace.seal(board.directory, manifest, borrower['runId'], attempt_id)
        self.assertEqual(caught.exception.code, 'WORKSPACE_SCOPE_VIOLATION')
        fingerprint = caught.exception.details['evidence']['observedFingerprint']
        resolution = workspace.resolve(board.directory, manifest, task_id=borrower['runId'],
                                       attempt_id=attempt_id, action='restore', paths=['src/foreign.txt'],
                                       observed_fingerprint=fingerprint,
                                       actor={'role': 'host'})['artifact']
        output = workspace.seal(board.directory, manifest, borrower['runId'], attempt_id)
        cumulative = workspace.cumulative_patch(manifest, resolution, initial)
        self.assertNotEqual(initial, resolution['inputCommit'])
        self.assertTrue(native_is_file(Path(cumulative['path'])))
        with board.store.db.write() as connection:
            board.store.workflow._pin_artifact(connection, run_id=borrower['runId'], kind='input',
                                               manifest=manifest, attempt_id=None, turn_id=None,
                                               now=board.store.now())
            board.store.workflow._pin_artifact(connection, run_id=borrower['runId'], kind='resolved-output',
                                               manifest=resolution, attempt_id=attempt_id, turn_id=None,
                                               now=board.store.now())
            board.store.workflow._pin_artifact(connection, run_id=borrower['runId'], kind='output',
                                               manifest=output, attempt_id=attempt_id, turn_id=None,
                                               now=board.store.now())
        self.close(board, borrower)
        folder = board.directory / 'workspaces' / manifest['workspaceId'] / 'outputs' / workspace._sha(
            workspace._json([borrower['runId'], attempt_id]))
        self.assertTrue(native_is_file(folder / 'resolve-restore.json'))
        self.assertTrue(native_is_file(folder / 'output.json'))
        self.assertTrue(native_is_dir(folder / 'scope-conflicts'))
        result = deletion.delete(board.store, borrower['macroTaskId'], 1)
        self.assertEqual(result['state'], 'deleted', result)
        self.assertFalse(native_exists(folder))
        self.assertFalse(native_exists(Path(cumulative['path'])))
        self.assertTrue(checkout.is_dir())
        self.assertEqual(self.git('rev-parse', manifest['snapshot']['inputRef']).strip(), manifest['inputCommit'])
        self.close(board, owner)
        self.assertEqual(deletion.delete(board.store, owner['macroTaskId'], 1)['state'], 'deleted')

    def test_borrowed_managed_checkout_retains_owner_until_borrower_is_deleted(self):
        board = self.board()
        owner = self.submit_group(board,'managed-owner',kind='worktree')
        claim = self.claim(board,owner['runId'])
        from hey_my_buddy.protocol.workspace_manifest import resolve_manifest
        manifest = resolve_manifest(board.directory, claim['claim']['turn']['input']['executionWorkspace'])
        checkout = Path(manifest['path'])
        (checkout/'tracked.txt').write_text('owned sealed output\n', encoding="utf-8", newline="\n")
        seal = workspace.seal(self.directory,manifest,owner['runId'],claim['claim']['attempt']['attemptId'])
        self.finish_turn(board,claim,seal)
        view = self.view(board,owner['runId'])
        artifact = next(item for item in view['artifacts'] if item['kind']=='output')
        with defer_reclaim.object(board.store.workflow, "_reclaim_checkout", return_value={"removed": False, "reasons": ["fixture-retained"]}):
            self.accept(board,view,artifact,not_required=True)
        borrower = self.submit_group(board,'managed-borrower',cwd=checkout)
        for operation,args in ((deletion.preview,(board.store,owner['macroTaskId'])),
                               (deletion.delete,(board.store,owner['macroTaskId'],1))):
            with self.assertRaises(BoardError) as caught:
                operation(*args)
            self.assertEqual(caught.exception.code,'MACRO_TASK_STORAGE_IN_USE')
            self.assertTrue(any(borrower['runId'] in item['holderRunIds'] for item in caught.exception.details['objects']))
        self.close(board,borrower)
        deleted = deletion.delete(board.store,borrower['macroTaskId'],1)
        self.assertEqual(deleted['state'],'deleted',deleted)
        self.assertTrue(checkout.is_dir())
        with board.store.db.read() as connection:
            record_path = connection.execute(
                "SELECT record_path FROM workflow_artifacts WHERE run_id=? AND kind='output'",
                (owner['runId'],)).fetchone()[0]
        native_unlink(board.directory / 'workspaces' / manifest['workspaceId'] / 'manifest.json')
        native_unlink(board.directory / record_path)
        deleted = deletion.delete(board.store,owner['macroTaskId'],1)
        self.assertEqual(deleted['state'],'deleted',deleted)
        self.assertFalse(checkout.exists())
        self.assertEqual((self.repo/'tracked.txt').read_text(encoding="utf-8"),'base\n')

    def test_nested_repository_borrower_retains_owner_checkout_before_deletion_mark(self):
        board = self.board()
        owner = self.submit_group(board, 'nested-managed-owner', kind='worktree')
        claim = self.claim(board, owner['runId'])
        from hey_my_buddy.protocol.workspace_manifest import resolve_manifest
        manifest = resolve_manifest(board.directory, claim['claim']['turn']['input']['executionWorkspace'])
        checkout = Path(manifest['checkoutRoot'])
        (checkout / 'tracked.txt').write_text('owned sealed output\n', encoding='utf-8', newline='\n')
        seal = workspace.seal(self.directory, manifest, owner['runId'], claim['claim']['attempt']['attemptId'])
        self.finish_turn(board, claim, seal)
        view = self.view(board, owner['runId'])
        artifact = next(item for item in view['artifacts'] if item['kind'] == 'output')
        with defer_reclaim.object(board.store.workflow, '_reclaim_checkout',
                                  return_value={'removed': False, 'reasons': ['fixture-retained']}):
            self.accept(board, view, artifact, not_required=True)

        nested = checkout / 'src'
        self.git('-C', str(nested), 'init', '-q', '-b', 'main')
        self.git('-C', str(nested), 'config', 'user.email', 'buddy@example.invalid')
        self.git('-C', str(nested), 'config', 'user.name', 'Buddy Test')
        self.git('-C', str(nested), 'add', '.')
        self.git('-C', str(nested), 'commit', '-qm', 'nested repository')
        self.assertNotEqual(workspace.inspect(str(nested))['projectId'], manifest['projectId'])
        borrower = self.submit_group(board, 'nested-managed-borrower', cwd=nested)

        with board.store.db.read() as connection:
            self.assertIsNone(connection.execute(
                'SELECT 1 FROM macro_task_deletions WHERE target_id=?', (owner['macroTaskId'],),
            ).fetchone())
        for operation, args in ((deletion.preview, (board.store, owner['macroTaskId'])),
                                (deletion.delete, (board.store, owner['macroTaskId'], 1))):
            with self.assertRaises(BoardError) as caught:
                operation(*args)
            self.assertEqual(caught.exception.code, 'MACRO_TASK_STORAGE_IN_USE')
            self.assertTrue(any(borrower['runId'] in item['holderRunIds']
                                for item in caught.exception.details['objects']))
        self.assertTrue(checkout.is_dir())
        with board.store.db.read() as connection:
            self.assertIsNotNone(connection.execute(
                'SELECT 1 FROM workflow_runs WHERE run_id=?', (owner['runId'],),
            ).fetchone())

    def test_foreign_git_lock_keeps_macro_task_and_checkout_for_retry(self):
        board = self.board()
        run = self.submit_group(board, 'foreign-lock', kind='worktree')
        claim = self.claim(board, run['runId'])
        from hey_my_buddy.protocol.workspace_manifest import resolve_manifest
        manifest = resolve_manifest(board.directory, claim['claim']['turn']['input']['executionWorkspace'])
        checkout = Path(manifest['path'])
        (checkout / 'tracked.txt').write_text('sealed output\n', encoding='utf-8', newline='\n')
        seal = workspace.seal(self.directory, manifest, run['runId'], claim['claim']['attempt']['attemptId'])
        self.finish_turn(board, claim, seal)
        view = self.view(board, run['runId'])
        artifact = next(item for item in view['artifacts'] if item['kind'] == 'output')
        with defer_reclaim.object(board.store.workflow, '_reclaim_checkout',
                                  return_value={'removed': False, 'reasons': ['fixture-retained']}):
            self.accept(board, view, artifact, not_required=True)
        self.git('worktree', 'unlock', str(checkout))
        self.git('worktree', 'lock', '--reason', 'foreign-owner', str(checkout))

        blocked = deletion.delete(board.store, run['macroTaskId'], 1)
        self.assertEqual((blocked['state'], blocked['deletedAt'], blocked['failures'][0]['objectKind']),
                         ('deleting', None, 'workspace'))
        self.assertEqual(blocked['failures'][0]['reason']['code'], 'macro-task-deletion.lock-mismatch')
        self.assertTrue(checkout.is_dir())
        self.assertEqual(workspace._worktree_record(self.repo, checkout)['locked'], 'foreign-owner')
        with board.store.db.read() as connection:
            self.assertIsNotNone(connection.execute('SELECT 1 FROM macro_tasks WHERE macro_task_id=?',
                                                    (run['macroTaskId'],)).fetchone())
            self.assertIsNotNone(connection.execute('SELECT 1 FROM workflow_runs WHERE run_id=?',
                                                    (run['runId'],)).fetchone())
            mark = connection.execute('SELECT deletion_id FROM macro_task_deletions WHERE target_id=?',
                                      (run['macroTaskId'],)).fetchone()
            self.assertEqual(mark['deletion_id'], blocked['deletionId'])
        self.assertEqual(deletion.preview(board.store, run['macroTaskId'])['deletionState'], 'deleting')

        self.git('worktree', 'unlock', str(checkout))
        self.git('worktree', 'lock', '--reason', 'hey-my-buddy:' + manifest['workspaceId'], str(checkout))
        completed = deletion.delete(board.store, run['macroTaskId'], 1)
        self.assertEqual((completed['state'], completed['duplicate'], completed['deletionId']),
                         ('deleted', True, blocked['deletionId']))
        self.assertFalse(checkout.exists())
        with board.store.db.read() as connection:
            self.assertIsNone(connection.execute('SELECT 1 FROM workflow_runs WHERE run_id=?',
                                                 (run['runId'],)).fetchone())
            self.assertIsNone(connection.execute('SELECT 1 FROM macro_task_deletions WHERE target_id=?',
                                                 (run['macroTaskId'],)).fetchone())
