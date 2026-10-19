"""Only task-bound private bytes are removed, and interrupted removal resumes."""
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
            paths.append(self.file(private_dirs.attempt_root(board.directory, name, 'owned', 'attempt-' + name)
                                   / 'session.jsonl', {'display': True}))
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



from blackboard.tasks import test_macro_task_deletion as engine_fixtures
from blackboard.tasks import test_workflow as workflow_fixtures


class DeletionFileRetryTests(workflow_fixtures.WorkflowTestCase):
    closed = engine_fixtures.MacroTaskDeletionTests.closed

    def test_private_root_partial_removal_retries_from_real_files(self):
        board = self.board()
        run = self.closed(board,'private-interruption',macro_task='Private root interruption')
        root = private_dirs.micro_task_root(board.directory,'dsh',run['runId'])
        root.mkdir(parents=True)
        first,remaining = root/'first',root/'remaining'
        first.write_text('first private byte')
        remaining.write_text('remaining private bytes')
        evidence = board.directory/'attempts'/run['runId']/'evidence'
        evidence.parent.mkdir(parents=True)
        evidence.write_text('execution evidence')
        original = private_dirs.remove_tree
        def interrupted(path,**kwargs):
            if path == root:
                first.unlink()
                raise OSError('interrupted inside private root')
            return original(path,**kwargs)
        with patch.object(deletion,'_delete_workspaces',return_value=[]), \
                patch.object(private_dirs,'remove_tree',side_effect=interrupted):
            result = deletion.delete(board.store,run['macroTaskId'],1)
        self.assertEqual((result['state'],result['objectKind']),('deleting','private-root'))
        self.assertFalse(first.exists())
        self.assertTrue(remaining.exists())
        with patch.object(deletion,'_delete_workspaces',return_value=[]):
            completed = deletion.delete(board.store,run['macroTaskId'],1)
        self.assertEqual(completed['state'],'deleted')
        self.assertFalse(root.exists())
        self.assertFalse(evidence.exists())

    def test_cli_partial_removal_retries_from_real_files(self):
        board = self.board()
        run = self.closed(board,'cli-interruption',macro_task='CLI interruption')
        first = control_file_path(board.directory,run['runId'],1)
        remaining = control_file_path(board.directory,run['runId'],2)
        first.parent.mkdir()
        first.write_text('{}')
        remaining.write_text('{}')
        submission = submission_file_path(board.directory,'cli-interruption')
        submission.parent.mkdir()
        submission.write_text('{}')
        user = first.parent/'unrelated.g1.json'
        user.write_text('unrelated control')
        original = Path.unlink
        removed = []
        def interrupted(path,*args,**kwargs):
            result = original(path,*args,**kwargs)
            if path in (first,remaining) and not removed:
                removed.append(path)
                raise OSError('interrupted after unlink inside CLI file phase')
            return result
        with patch.object(deletion,'_delete_workspaces',return_value=[]), \
                patch.object(Path,'unlink',new=interrupted):
            result = deletion.delete(board.store,run['macroTaskId'],1)
        self.assertEqual((result['state'],result['objectKind']),('deleting','cli-file'))
        self.assertEqual(sum(path.exists() for path in (first,remaining)),1)
        self.assertEqual(len(removed),1)
        self.assertTrue(submission.exists())
        with patch.object(deletion,'_delete_workspaces',return_value=[]):
            completed = deletion.delete(board.store,run['macroTaskId'],1)
        self.assertEqual(completed['state'],'deleted')
        self.assertFalse(first.exists())
        self.assertFalse(remaining.exists())
        self.assertFalse(submission.exists())
        self.assertEqual(user.read_text(),'unrelated control')
