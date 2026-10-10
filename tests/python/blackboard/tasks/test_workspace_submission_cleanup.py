"""Submission failure revokes only the invocation's new private Git allocation."""
import json
import tempfile
from pathlib import Path
import sqlite3
import threading
from unittest.mock import patch

from blackboard.tasks import test_workflow as workflow_tests
from blackboard.tasks import test_workflow_preparation as preparation_tests
from hey_my_buddy.blackboard.tasks import workflow as workflow_module, workspace
from hey_my_buddy.errors import BoardError


class SubmissionCleanupTests(workflow_tests.WorkflowTestCase):
    git = preparation_tests.PreparationTests.git
    repository = preparation_tests.PreparationTests.repository

    _repository_template = None

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # Share only initial Git objects/config. Mutable files, index, refs,
        # worktree registration and identity facts belong to each copied site.
        temporary = tempfile.TemporaryDirectory(prefix="buddy-submission-repository-")
        cls.addClassCleanup(temporary.cleanup)
        fixture = cls(methodName="runTest")
        fixture.directory = Path(temporary.name)
        cls._repository_template = None
        cls._repository_template = fixture.repository("template")

    def setUp(self):
        super().setUp()
        self.workspace = workspace
        workflow_module._workspace_module = workspace
        self.repo = self.repository('source')

    def rejected(self, board, request='rejected', **options):
        return board.store.workflow.submit({**workflow_tests.CONFIGURATION,
            'requestId': request, 'hostId': 'host-1', 'task': 'do the thing', 'cwd': str(self.repo),
            'executionWorkspace': {'kind': 'worktree', 'access': 'write'}, **options})

    def allocation(self, request='rejected'):
        return workspace._workspace_directory(self.directory, request)[1]

    def assert_no_registration(self, board, request='rejected'):
        with board.store.db.read() as connection:
            self.assertIsNone(connection.execute('SELECT task_id FROM tasks WHERE request_id=?', (request,)).fetchone())
            self.assertEqual(connection.execute('SELECT COUNT(*) FROM workspace_reservations WHERE path=?', (str(self.allocation(request) / 'checkout'),)).fetchone()[0], 0)

    def test_public_integration_during_failed_admission_retains_new_target(self):
        board = self.board()
        delivered = self.submit(board, request_id='integration-source', cwd=str(self.repo))
        self.register(board)
        claim = self.claim(board, run_id=delivered['runId'])
        (self.repo / 'tracked.txt').write_text('sealed integration output\n')
        self.finish_turn(board, claim)
        view = board.call('workflow_get', {'runId': delivered['runId']})
        with board.store.db.read() as connection:
            sealed = json.loads(connection.execute(
                'SELECT manifest_json FROM workflow_artifacts WHERE artifact_id=?',
                (view['finalArtifactId'],)).fetchone()[0])
        original = BoardError('ADMISSION_FAILED', 'Controlled admission refusal after integration')
        def admission(*args, **kwargs):
            manifest = kwargs['governed']['manifest']
            accepted = board.call('workflow_accept', {
                'runId': delivered['runId'], 'artifactId': view['finalArtifactId'],
                'note': 'Retained private integration target', 'keepCheckout': True,
                'target': {'path': manifest['checkoutRoot'], 'ref': sealed['commit']}, **self.control(view)})
            self.assertEqual(accepted['integration']['state'], 'verified')
            raise original
        with patch.object(board.store, 'task_submit', side_effect=admission):
            with self.assertRaises(BoardError) as caught:
                self.rejected(board)
        self.assertIs(caught.exception, original)
        self.assertFalse(original.details['submissionCleanup']['removed'])
        self.assertIn('workflow_integrations-reference', original.details['submissionCleanup']['reasons'])
        self.assertTrue((self.allocation() / 'checkout').is_dir())
        self.assert_no_registration(board)

    def test_real_project_admission_rejection_removes_only_new_checkout(self):
        board = self.board()
        foreign = self.submit(board, request_id='foreign', cwd=str(self.repository('foreign')),
                              objective={'title': 'other repository'}, executionWorkspace={'kind': 'existing', 'access': 'read'})
        source_index = (self.repo / '.git/index').read_bytes()
        original = board.store.task_submit
        saved = {}
        def admission(*args, **kwargs):
            manifest = kwargs['governed']['manifest']
            directory = Path(manifest['checkoutRoot']).parent
            (directory / 'outputs').mkdir()
            (directory / 'outputs/evidence.txt').write_text('retained output')
            saved.update(manifest=manifest, refs=self.git(self.repo, 'for-each-ref', '--format=%(refname) %(objectname)', 'refs/buddy/'))
            return original(*args, **kwargs)
        with patch.object(board.store, 'task_submit', side_effect=admission):
            with self.assertRaises(BoardError) as caught:
                self.rejected(board, objectiveId=foreign['objectiveId'])
        self.assertEqual(caught.exception.code, 'CONFLICT')
        self.assertTrue(caught.exception.details['submissionCleanup']['removed'])
        directory = self.allocation()
        self.assertFalse((directory / 'checkout').exists())
        self.assertEqual((directory / 'outputs/evidence.txt').read_text(), 'retained output')
        self.assertTrue((directory / 'manifest.json').exists())
        self.assertEqual(source_index, (self.repo / '.git/index').read_bytes())
        self.assertEqual(saved['refs'], self.git(self.repo, 'for-each-ref', '--format=%(refname) %(objectname)', 'refs/buddy/'))
        self.assert_no_registration(board)

    def test_transaction_failure_rolls_back_task_and_revokes_checkout(self):
        board = self.board()
        error = sqlite3.OperationalError('controlled database rejection')
        with patch.object(board.store.workflow, 'attach_governed_task', side_effect=error):
            with self.assertRaises(sqlite3.OperationalError) as caught:
                self.rejected(board)
        self.assertIs(caught.exception, error)
        self.assertFalse((self.allocation() / 'checkout').exists())
        self.assert_no_registration(board)
        self.assertTrue(list(self.allocation().glob('submission-cleanup-*.json')))

    def test_error_before_transaction_revokes_checkout(self):
        board = self.board()
        error = BoardError('ADMISSION_FAILED', 'controlled pretransaction failure')
        with patch.object(board.store, 'task_submit', side_effect=error):
            with self.assertRaises(BoardError) as caught:
                self.rejected(board)
        self.assertIs(caught.exception, error)
        self.assertTrue(error.details['submissionCleanup']['removed'])
        self.assertFalse((self.allocation() / 'checkout').exists())
        self.assert_no_registration(board)

    def test_cleanup_failure_preserves_original_error_and_recovery_manifest(self):
        board = self.board()
        error = BoardError('ADMISSION_FAILED', 'original admission failure', originalFact='kept')
        with patch.object(board.store, 'task_submit', side_effect=error), patch.object(workspace, 'cleanup_remove', side_effect=BoardError('WORKSPACE_GIT_ERROR', 'controlled cleanup failure', argv=['git', 'worktree', 'remove'])):
            with self.assertRaises(BoardError) as caught:
                self.rejected(board)
        self.assertIs(caught.exception, error)
        self.assertEqual(error.details['originalFact'], 'kept')
        facts = error.details['submissionCleanup']
        self.assertFalse(facts['removed'])
        self.assertEqual(facts['cleanupError']['code'], 'WORKSPACE_GIT_ERROR')
        directory = self.allocation()
        self.assertTrue((directory / 'checkout').is_dir())
        manifest = json.loads((directory / 'manifest.json').read_text())
        self.assertTrue(workspace.cleanup_inspect(self.directory, manifest)['eligible'])
        self.assertEqual(json.loads(next(directory.glob('submission-cleanup-*.json')).read_text()), facts)
        self.assert_no_registration(board)

    def test_preexisting_prepared_worktree_is_preserved_on_rejection(self):
        board = self.board()
        intent = {'kind':'worktree', 'cwd':str(self.repo), 'access':'write', 'base':{'kind':'working-tree'}, 'writeScope':['.'], 'integrator':'host:host-1'}
        manifest = workspace.prepare(self.directory, 'rejected', intent)
        with patch.object(board.store, 'task_submit', side_effect=BoardError('ADMISSION_FAILED', 'rejected')):
            with self.assertRaises(BoardError) as caught:
                self.rejected(board)
        self.assertEqual(caught.exception.code, 'ADMISSION_FAILED')
        self.assertTrue(Path(manifest['checkoutRoot']).exists())
        workspace.verify(manifest)

    def test_existing_source_is_preserved_on_rejection(self):
        board = self.board()
        with patch.object(board.store, 'task_submit', side_effect=BoardError('ADMISSION_FAILED', 'rejected')):
            with self.assertRaises(BoardError):
                self.submit(board, cwd=str(self.repo), executionWorkspace={'kind':'existing', 'access':'write'})
        self.assertEqual((self.repo / 'tracked.txt').read_text(), 'base\n')
        self.assertEqual(self.git(self.repo, 'status', '--porcelain'), '')

    def test_committed_winner_survives_post_commit_failure(self):
        board = self.board()
        original = board.store.task_submit
        error = BoardError('REPLY_FAILED', 'lost reply after commit')
        def committed(*args, **kwargs):
            original(*args, **kwargs)
            raise error
        with patch.object(board.store, 'task_submit', side_effect=committed):
            with self.assertRaises(BoardError) as caught:
                self.rejected(board)
        self.assertIs(caught.exception, error)
        self.assertTrue((self.allocation() / 'checkout').exists())
        self.assertFalse(error.details['submissionCleanup']['removed'])
        recovered = self.rejected(board)
        self.assertTrue(recovered['duplicate'])

    def test_same_request_conflict_recovers_concurrent_winner(self):
        board = self.board()
        original = board.store.task_submit
        def committed(*args, **kwargs):
            original(*args, **kwargs)
            raise BoardError('CONFLICT', 'same request winner committed')
        with patch.object(board.store, 'task_submit', side_effect=committed):
            recovered = self.rejected(board)
        self.assertTrue(recovered['duplicate'])
        self.assertTrue((self.allocation() / 'checkout').exists())
        with patch.object(workspace, 'prepare', side_effect=AssertionError('replay must not prepare')):
            self.assertTrue(self.rejected(board)['duplicate'])

    def test_allocation_lock_fences_parallel_same_request_until_winner_admitted(self):
        board = self.board()
        entered, release = threading.Event(), threading.Event()
        original = board.store.task_submit
        result = []
        def admission(*args, **kwargs):
            entered.set()
            self.assertTrue(release.wait(15))
            return original(*args, **kwargs)
        def run():
            try:
                result.append(self.rejected(board))
            except Exception as error:
                result.append(error)
        with patch.object(board.store, 'task_submit', side_effect=admission):
            thread = threading.Thread(target=run)
            thread.start()
            try:
                self.assertTrue(entered.wait(15))
                with self.assertRaises(BoardError) as caught:
                    self.rejected(board)
                self.assertEqual(caught.exception.code, 'WORKSPACE_BUSY')
            finally:
                release.set()
                thread.join(15)
        self.assertEqual(len(result), 1)
        self.assertIsInstance(result[0], dict, result)
        self.assertTrue((self.allocation() / 'checkout').exists())
        self.assertTrue(self.rejected(board)['duplicate'])

    def test_partial_materialization_failure_revokes_new_checkout(self):
        board = self.board()
        original = workspace._materialize
        def incomplete(target, entries):
            original(target, entries)
            raise BoardError('MATERIALIZATION_FAILED', 'controlled failure after writing')
        with patch.object(workspace, '_materialize', side_effect=incomplete):
            with self.assertRaises(BoardError) as caught:
                self.rejected(board)
        self.assertEqual(caught.exception.code, 'MATERIALIZATION_FAILED')
        self.assertTrue(caught.exception.details['preparationCleanup']['removed'])
        self.assertFalse((self.allocation() / 'checkout').exists())
        self.assertTrue((self.allocation() / 'input.json').exists())
        self.assert_no_registration(board)

    def test_failure_after_materialization_revokes_new_checkout(self):
        board = self.board()
        original = workspace._write_once
        def failed_manifest(path, data):
            if path.name == 'manifest.json':
                raise BoardError('MANIFEST_FAILED', 'controlled publish failure')
            return original(path, data)
        with patch.object(workspace, '_write_once', side_effect=failed_manifest):
            with self.assertRaises(BoardError) as caught:
                self.rejected(board)
        self.assertEqual(caught.exception.code, 'MANIFEST_FAILED')
        self.assertTrue(caught.exception.details['preparationCleanup']['removed'])
        self.assertFalse((self.allocation() / 'checkout').exists())
        self.assert_no_registration(board)

    def test_oserror_admission_keeps_original_type_and_recovery_note(self):
        board = self.board()
        error = OSError('controlled admission I/O failure')
        with patch.object(board.store, 'task_submit', side_effect=error):
            with self.assertRaises(OSError) as caught:
                self.rejected(board)
        self.assertIs(caught.exception, error)
        self.assertTrue(any('Submission cleanup:' in note for note in error.__notes__))
        self.assertFalse((self.allocation() / 'checkout').exists())
        self.assert_no_registration(board)

    def test_failed_git_remove_restores_allocation_lock_and_keeps_admission_error(self):
        board = self.board()
        original_git = workspace._git
        def removal_failure(root, *args, **kwargs):
            if args[:2] == ('worktree', 'remove'):
                raise BoardError('WORKSPACE_GIT_ERROR', 'controlled removal failure', argv=['git', *args])
            return original_git(root, *args, **kwargs)
        error = BoardError('ADMISSION_FAILED', 'original rejection')
        with patch.object(board.store, 'task_submit', side_effect=error), patch.object(workspace, '_git', side_effect=removal_failure):
            with self.assertRaises(BoardError) as caught:
                self.rejected(board)
        self.assertIs(caught.exception, error)
        directory = self.allocation()
        manifest = json.loads((directory / 'manifest.json').read_text())
        self.assertTrue((directory / 'checkout').exists())
        self.assertEqual(error.details['submissionCleanup']['cleanupError']['code'], 'WORKSPACE_GIT_ERROR')
        self.assertTrue(workspace.cleanup_inspect(self.directory, manifest)['eligible'])
        self.assert_no_registration(board)

    def test_changed_new_checkout_is_retained_after_admission_rejection(self):
        board = self.board()
        error = BoardError('ADMISSION_FAILED', 'rejected with changed content')
        def changed(*args, **kwargs):
            checkout = Path(kwargs['governed']['manifest']['checkoutRoot'])
            (checkout / 'tracked.txt').write_text('valuable new content')
            raise error
        with patch.object(board.store, 'task_submit', side_effect=changed):
            with self.assertRaises(BoardError):
                self.rejected(board)
        self.assertEqual((self.allocation() / 'checkout/tracked.txt').read_text(), 'valuable new content')
        self.assertIn('unsealed-changes', error.details['submissionCleanup']['reasons'])
        self.assert_no_registration(board)

    def test_read_tree_failure_revokes_materialized_checkout(self):
        board = self.board()
        original_git = workspace._git
        error = BoardError('WORKSPACE_GIT_ERROR', 'controlled read-tree failure', argv=['git', 'read-tree'])
        def read_tree_failure(root, *args, **kwargs):
            if args[0] == 'read-tree' and Path(root).name == 'checkout':
                raise error
            return original_git(root, *args, **kwargs)
        with patch.object(workspace, '_git', side_effect=read_tree_failure):
            with self.assertRaises(BoardError) as caught:
                self.rejected(board)
        self.assertIs(caught.exception, error)
        self.assertTrue(error.details['preparationCleanup']['removed'])
        self.assertFalse((self.allocation() / 'checkout').exists())
        self.assert_no_registration(board)

    def test_git_add_created_checkout_before_error_is_revoked(self):
        board = self.board()
        original_git = workspace._git
        error = BoardError('WORKSPACE_GIT_ERROR', 'controlled lost worktree-add reply', argv=['git', 'worktree', 'add'])
        def add_failure(root, *args, **kwargs):
            result = original_git(root, *args, **kwargs)
            if args[:2] == ('worktree', 'add'):
                raise error
            return result
        with patch.object(workspace, '_git', side_effect=add_failure):
            with self.assertRaises(BoardError) as caught:
                self.rejected(board)
        self.assertIs(caught.exception, error)
        self.assertTrue(error.details['preparationCleanup']['removed'])
        self.assertFalse((self.allocation() / 'checkout').exists())
        self.assert_no_registration(board)

    def test_existing_managed_reuse_holds_original_allocation_through_admission(self):
        from hey_my_buddy.blackboard.tasks import storage
        board = self.board()
        intent = {'kind': 'worktree', 'cwd': str(self.repo), 'access': 'write',
                  'base': {'kind': 'working-tree'}, 'writeScope': ['.'], 'integrator': 'host:host-1'}
        original_submit = board.store.task_submit
        for kind in ('existing', 'worktree'):
            with self.subTest(kind=kind):
                manifest = workspace.prepare(self.directory, 'borrowed-allocation-' + kind, intent)
                checkout = Path(manifest['checkoutRoot'])
                captures = []
                with patch.object(storage, 'process_inventory', return_value=([], [], True)):
                    planned = board.call('storage_plan', {})
                    candidate = next(row for row in planned['candidates'] if row['path'] == str(checkout))
                    self.assertTrue(candidate['eligible'], candidate['reasons'])
                    def cleanup_before_admission(*args, **kwargs):
                        captures.append(board.call('storage_apply', {'planId': planned['planId'],
                            'commandId': 'borrowed-admission-cleanup-' + kind, 'confirm': True}))
                        return original_submit(*args, **kwargs)
                    with patch.object(board.store, 'task_submit', side_effect=cleanup_before_admission):
                        submitted = self.submit(board, request_id='reuse-allocation-' + kind, cwd=str(checkout), kind=kind)
                self.assertTrue(checkout.exists(), 'a borrowed allocation must survive its admission window')
                self.assertEqual(captures[0]['removed'], [])
                self.assertTrue(any('WORKSPACE_BUSY' in row['reasons'] for row in captures[0]['skipped']))
                with board.store.db.read() as connection:
                    self.assertIsNotNone(connection.execute("SELECT 1 FROM workspace_reservations WHERE holder_task_id=? AND state='held'",
                                                           (submitted['runId'],)).fetchone())

    def test_all_prepared_helpers_hold_locks_until_database_admission(self):
        from hey_my_buddy.blackboard.tasks import storage
        board = self.board()
        self.register(board)
        parent = self.submit(board, cwd=str(self.repo), kind='existing')
        self.finish_turn(board, self.claim(board), disposition='assistance')
        view = board.call('workflow_get', {'runId': parent['runId']})
        original_prepare = workspace._prepare_locked
        original_configuration = board.store.workflow._validated_configuration
        captures = {}
        def capture_first(directory, workspace_id, request_id, intent):
            manifest = original_prepare(directory, workspace_id, request_id, intent)
            if request_id == 'first-cleanup-helper':
                captures['manifest'] = manifest
            return manifest
        def cleanup_while_preparing_next(spec):
            if spec.get('task') == 'later helper':
                checkout = captures['manifest']['checkoutRoot']
                planned = board.call('storage_plan', {})
                candidate = next(row for row in planned['candidates'] if row['path'] == checkout)
                self.assertTrue(candidate['eligible'], candidate['reasons'])
                captures['cleanup'] = board.call('storage_apply', {'planId': planned['planId'],
                    'commandId': 'helper-admission-cleanup', 'confirm': True})
            return original_configuration(spec)
        with patch.object(storage, 'process_inventory', return_value=([], [], True)), \
                patch.object(workspace, '_prepare_locked', side_effect=capture_first), \
                patch.object(board.store.workflow, '_validated_configuration', side_effect=cleanup_while_preparing_next):
            approved = self.decide(board, view, view['activeRequest']['requestId'], autoContinue=False,
                helpers=[{'requestId': 'first-cleanup-helper', 'task': 'first helper', 'cwd': str(self.repo),
                          'executionWorkspace': {'kind': 'worktree', 'writeScope': ['.']}},
                         {'requestId': 'later-cleanup-helper', 'task': 'later helper', 'cwd': str(self.repo),
                          'executionWorkspace': {'kind': 'worktree', 'writeScope': ['.']}}])
        checkout = Path(captures['manifest']['checkoutRoot'])
        self.assertTrue(checkout.exists(), 'an earlier prepared helper must stay allocated while later helpers prepare')
        self.assertEqual(captures['cleanup']['removed'], [])
        self.assertTrue(any('WORKSPACE_BUSY' in row['reasons'] for row in captures['cleanup']['skipped']))
        self.assertEqual(len(approved['children']), 2)
        with board.store.db.read() as connection:
            for child in approved['children']:
                self.assertIsNotNone(connection.execute("SELECT 1 FROM workspace_reservations WHERE holder_task_id=? AND state='held'",
                                                       (child['taskId'],)).fetchone())

    def test_two_read_helpers_share_one_original_allocation_lock(self):
        from hey_my_buddy.protocol import schemas
        board = self.board()
        intent = {'kind': 'worktree', 'cwd': str(self.repo), 'access': 'write',
                  'base': {'kind': 'working-tree'}, 'writeScope': ['.'], 'integrator': 'host:host-1'}
        manifest = workspace.prepare(self.directory, 'shared-original-allocation', intent)
        checkout = Path(manifest['checkoutRoot'])
        helpers = schemas.normalize_helpers({'helpers': [
            {'requestId': 'shared-reader-one', 'task': 'read one', 'cwd': str(checkout),
             'executionWorkspace': {'kind': 'existing', 'access': 'read'}},
            {'requestId': 'shared-reader-two', 'task': 'read two', 'cwd': str(checkout),
             'executionWorkspace': {'kind': 'existing', 'access': 'read'}}]}, host_id='host-1')
        with patch.object(workspace, '_lock', wraps=workspace._lock) as lock:
            with board.store.workflow._helper_preparations(helpers) as prepared:
                self.assertEqual(len(prepared), 2)
                self.assertEqual([item['manifest']['checkoutId'] for item in prepared],
                                 [manifest['checkoutId'], manifest['checkoutId']])
                self.assertTrue(checkout.exists())
        self.assertEqual(sum(call.args[0] == checkout.parent for call in lock.call_args_list), 1,
                         'shared helpers must acquire the original physical allocation lock only once')
        self.assertTrue(checkout.exists())
