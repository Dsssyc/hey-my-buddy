"""Only task-bound private bytes and content-bearing storage plans are removed."""
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from hey_my_buddy import private_dirs
from hey_my_buddy.blackboard.tasks import macro_task_deletion as deletion
from hey_my_buddy.home import control_file_path, submission_file_path
from support import BoardTestCase


class DeletionFileTests(BoardTestCase):
    def scope(self):
        return SimpleNamespace(
            tasks={'owned'},runs={'owned'},run_rows=[],attempt_rows=[],manifests=[],
            task_rows=[{'task_id':'owned','adapter':'codex','request_id':'owned-request'}])

    def file(self,path,document):
        path.parent.mkdir(parents=True,exist_ok=True)
        path.write_text(json.dumps(document))
        return path

    def test_private_roots_follow_historical_adapter_bindings_and_leave_links(self):
        board = self.board()
        scope = self.scope()
        scope.attempt_rows = [{'task_id':'owned','adapter':'decision','model_harness':name,
                               'attempt_id':'attempt-'+name} for name in ('claude','zcode','dsh')]
        paths = []
        for name in ('codex','claude','zcode','dsh'):
            for root in (private_dirs.micro_task_root(board.directory,name,'owned'),
                         board.directory / 'harnesses' / name / hashlib.sha256(b'owned').hexdigest()):
                paths.append(self.file(root / 'native' / 'payload',{'private':True}))
        attempts = self.file(board.directory / 'attempts' / 'owned' / 'attempt-1' / 'payload',{})
        untouched = self.file(private_dirs.micro_task_root(board.directory,'codex','other') / 'payload',{})
        with patch.object(deletion,'_rows',return_value=[]):
            self.assertEqual(deletion._delete_private_roots(board.store,scope),[])
        self.assertTrue(all(not path.exists() for path in paths))
        self.assertFalse(attempts.exists())
        self.assertTrue(untouched.exists())
        user = self.file(board.directory / 'user' / 'payload',{'user':True})
        root = private_dirs.micro_task_root(board.directory,'codex','owned')
        root.symlink_to(user.parent,target_is_directory=True)
        with patch.object(deletion,'_rows',return_value=[]):
            skipped = deletion._delete_private_roots(board.store,self.scope())
        self.assertTrue(skipped)
        self.assertTrue(root.is_symlink())
        self.assertEqual(json.loads(user.read_text()),{'user':True})

    def test_cli_temporary_files_require_exact_embedded_binding(self):
        board = self.board()
        scope = self.scope()
        controls = [self.file(control_file_path(board.directory,'owned',generation),{}) for generation in (1,9)]
        temporary = self.file(board.directory / 'controls' / '.owned.g9.json.test.tmp',
                              {'runId':'owned','ownerGeneration':9})
        wrong = self.file(board.directory / 'controls' / '.owned.g9.json.other.tmp',
                          {'runId':'other','ownerGeneration':9})
        other = self.file(control_file_path(board.directory,'other',1),{})
        submission = self.file(submission_file_path(board.directory,'owned-request'),{})
        partial = self.file(submission.with_name('.'+submission.stem+'.test.tmp'),{'requestId':'owned-request'})
        unknown = self.file(submission.with_name('.'+submission.stem+'.wrong.tmp'),{'requestId':'other-request'})
        self.assertEqual(deletion._delete_cli_files(board.store,scope),[])
        self.assertTrue(all(not path.exists() for path in [*controls,temporary,submission,partial]))
        self.assertTrue(all(path.exists() for path in (wrong,other,unknown)))

    def test_cli_link_is_retained_with_its_user_target(self):
        board = self.board()
        user = self.file(board.directory / 'user-file',{'keep':True})
        control = control_file_path(board.directory,'owned',1)
        control.parent.mkdir()
        control.symlink_to(user)
        skipped = deletion._delete_cli_files(board.store,self.scope())
        self.assertEqual(len(skipped),1)
        self.assertTrue(control.is_symlink())
        self.assertEqual(json.loads(user.read_text()),{'keep':True})

    def test_content_bearing_plan_is_removed_but_opaque_receipt_is_retained(self):
        board = self.board()
        scope = self.scope()
        scope.manifests = [{'workspaceId':'ws-owned'}]
        plan = self.file(board.directory / 'storage' / 'stg-owned.json',{
            'candidates':[{'orphan':True,'manifest':{'workspaceId':'ws-owned','snapshot':{'entries':['user-file']}}}]})
        other = self.file(board.directory / 'storage' / 'stg-other.json',{
            'candidates':[{'orphan':True,'manifest':{'workspaceId':'ws-other'}}]})
        receipt = self.file(board.directory / 'storage' / 'receipt-owned.json',{
            'planId':'stg-owned','removed':[{'id':'owned','path':'blackboard/path','bytes':7}]})
        opaque_plan = self.file(board.directory / 'storage' / 'stg-opaque.json',{
            'candidates':[{'id':'owned','runId':'owned','path':'blackboard/path'}]})
        self.assertEqual(deletion._delete_storage_plans(board.store,scope),[])
        self.assertFalse(plan.exists())
        self.assertTrue(all(path.exists() for path in (other,receipt,opaque_plan)))
