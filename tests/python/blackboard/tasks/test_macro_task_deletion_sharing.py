"""Shared-object eligibility through real workspace admission and ownership."""
from unittest.mock import patch as defer_reclaim
from pathlib import Path

from blackboard.tasks import workspace_lifecycle_case as lifecycle_fixtures
from blackboard.tasks.test_workflow_real import BUDDY, RealWorkspaceTestCase
from hey_my_buddy.blackboard.tasks import macro_task_deletion as deletion, workspace, workflow
from hey_my_buddy.errors import BoardError


class DeletionSharingTests(RealWorkspaceTestCase):
    control = lifecycle_fixtures.LifecycleTestCase.control
    claim = lifecycle_fixtures.LifecycleTestCase.claim
    turn_record = lifecycle_fixtures.LifecycleTestCase.turn_record
    finish_turn = lifecycle_fixtures.LifecycleTestCase.finish_turn
    view = lifecycle_fixtures.LifecycleTestCase.view
    accept = lifecycle_fixtures.LifecycleTestCase.accept

    def setUp(self):
        super().setUp()
        self.controls = {}
        self.worker = 'w-deletion-sharing'
        original = workflow._workspace_module
        workflow._workspace_module = workspace
        self.addCleanup(setattr,workflow,'_workspace_module',original)

    def submit_group(self,board,request_id,*,kind='existing',cwd=None):
        run = board.call('workflow_submit',{
            **BUDDY,'requestId':request_id,'hostId':'host-1','task':'Private shared-object fixture',
            'cwd':str(cwd or self.repo),'submissionToken':'t'*32,'macroTask':{'title':request_id},
            'executionWorkspace':{'kind':kind,'access':'write','writeScope':['.']},
        })
        self.controls[run['runId']] = run['control']
        return run

    def close(self,board,run):
        board.call('workflow_cancel',{'runId':run['runId'],**self.control(run)})
        return board.call('workflow_conclude',{'runId':run['runId'],'note':'No execution was admitted',**self.control(run)})

    def test_two_existing_groups_on_the_same_host_checkout_can_both_be_deleted(self):
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

    def test_borrowed_managed_checkout_retains_owner_until_borrower_is_deleted(self):
        board = self.board()
        owner = self.submit_group(board,'managed-owner',kind='worktree')
        claim = self.claim(board,owner['runId'])
        manifest = claim['claim']['turn']['input']['executionWorkspace']
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
        deleted = deletion.delete(board.store,owner['macroTaskId'],1)
        self.assertEqual(deleted['state'],'deleted',deleted)
        self.assertFalse(checkout.exists())
        self.assertEqual((self.repo/'tracked.txt').read_text(encoding="utf-8"),'base\n')

    def test_nested_repository_borrower_retains_owner_checkout_before_deletion_mark(self):
        board = self.board()
        owner = self.submit_group(board, 'nested-managed-owner', kind='worktree')
        claim = self.claim(board, owner['runId'])
        manifest = claim['claim']['turn']['input']['executionWorkspace']
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
        self.assertNotEqual(workspace.inspect(str(nested))['repositoryId'], manifest['repositoryId'])
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
        manifest = claim['claim']['turn']['input']['executionWorkspace']
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
