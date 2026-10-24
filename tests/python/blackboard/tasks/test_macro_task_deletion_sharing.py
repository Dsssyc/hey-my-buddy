"""Shared-object eligibility through real workspace admission and ownership."""
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
        self.accept(board,view,artifact,not_required=True,keepCheckout=True)
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
