"""None turns bind original receipts and never acquire or reclaim a checkout."""
import hashlib
import json
import sqlite3
import subprocess
from pathlib import Path
from unittest import mock

from blackboard.tasks.test_workflow import WorkflowTestCase, BUDDY
from blackboard.tasks.test_workflow_worker import MicroTaskWorkerTestCase
from hey_my_buddy.blackboard.tasks import workflow, workspace, macro_task_deletion
from hey_my_buddy.cli.blocking import await_run
from hey_my_buddy.cli.main import _creation_key
from hey_my_buddy.errors import BoardError
from hey_my_buddy.protocol.turn_input import FOLDER_CHANGES_NOTICE
from support import enable_fixture_buddy, wait_for


class NoneWorkflowTests(WorkflowTestCase):
    def setUp(self):
        super().setUp()
        # Keep model-free native provenance, but identify real folders and Git.
        workflow._workspace_module = workspace

    def submit_none(self, board, *, cwd=None, access='read', title='answer', macro=None):
        cwd = str(cwd or self.workdir())
        identity = board.call('workflow_get', {'cwd': cwd})['project']['id']
        enable_fixture_buddy(board.store, BUDDY)
        packet = {'cwd': cwd, 'title': title, 'task': title, 'macroTaskId': macro or 'standalone',
                  'buddy': ':'.join(BUDDY.values()), 'checkout': 'none', 'access': access,
                  'expectedSeconds': 30, 'creationKey': _creation_key([
                      'adr032-submit-v1', 'macro:' + macro if macro else 'project:' + identity, title, title])}
        if access == 'full':
            packet['accessNote'] = 'The fixture authorizes full native access'
        return board.call('workflow_submit', packet)

    def view(self, board, run):
        return board.call('workflow_get', {'runId': run['runId']})

    def finish_none(self, board, run, **options):
        return self.finish_turn(board, self.claim(board, run_id=run['runId']), seal=False, **options)

    def test_folder_read_delivers_one_original_receipt_and_accepts_that_identity(self):
        board = self.board()
        folder = self.workdir()
        run = self.submit_none(board, cwd=folder)
        with mock.patch.object(workspace, 'verify', side_effect=AssertionError('none must not inspect Git')), \
             mock.patch.object(workspace, 'seal', side_effect=AssertionError('none must not seal Git')), \
             mock.patch.object(workspace, 'shared_stash_inventory', side_effect=AssertionError('none has no Git stash')):
            claim = self.claim(board, run_id=run['runId'])
            frozen = claim['claim']['turn']['input']
            self.assertEqual(frozen['version'], 5)
            self.assertEqual(frozen['executionWorkspace']['path'], str(folder))
            self.assertEqual(frozen['executionWorkspace']['projectKind'], 'folder')
            self.assertNotIn('workspaceId', frozen['executionWorkspace'])
            self.finish_turn(board, claim, seal=False)
        view = self.view(board, run)
        self.assertEqual(view['state'], 'delivered')
        self.assertEqual(view['checkout'], 'none')
        self.assertEqual(board.call('workflow_get', {'cwd': str(folder)})['items'][0]['runId'], run['runId'])
        self.assertNotIn('unsealedChanges', view)
        self.assertEqual(len(view['artifacts']), 1)
        artifact = view['artifacts'][0]
        self.assertEqual(artifact['kind'], 'result')
        self.assertEqual(artifact['contentForm'], 'result-reference')
        self.assertEqual(artifact['artifactId'], view['finalArtifactId'])
        self.assertEqual(artifact['runId'], run['runId'])
        self.assertEqual(artifact['generation'], 1)
        raw = Path(artifact['resultFile']['path']).read_bytes()
        self.assertEqual(hashlib.sha256(raw).hexdigest(), artifact['resultFile']['sha256'])
        self.assertEqual(Path(artifact['resultFile']['path']).name, 'result-receipt.json')
        for key in ('diffPath', 'changedPaths', 'sealedEvidence', 'integrationSource'):
            self.assertNotIn(key, artifact)
        with board.store.db.read() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM workspace_reservations').fetchone()[0], 0)
            row = db.execute('SELECT * FROM workflow_artifacts').fetchone()
            for column in ('workspace_id', 'manifest_json', 'output_commit', 'output_workspace_id', 'input_manifest_sha256'):
                self.assertIsNone(row[column])
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute('UPDATE workflow_artifacts SET generation=NULL')
        self.assertEqual(board.call('artifact_list', {'runId': run['runId']})['artifacts'], [artifact])
        accepted = board.call('workflow_accept', {'runId': run['runId'], 'artifactId': artifact['artifactId'],
                              'note': 'Checked task against the fixed answer', 'notRequired': 'This is an answer'})
        self.assertEqual(accepted['state'], 'accepted')
        self.assertEqual(accepted['reclaim']['reasons'], ['no-checkout'])
        self.assertTrue(folder.is_dir())
        self.assertFalse((board.directory / 'workspaces').exists())

    def test_none_in_git_never_seals_files_and_write_full_surface_the_notice(self):
        for access in ('write', 'full'):
            with self.subTest(access=access):
                board = self.board()
                folder = self.workdir('git-' + access)
                subprocess.run(['git', '-C', str(folder), 'init', '-q'], check=True, capture_output=True)
                source = folder / 'edited.txt'
                source.write_text('unsealed source\n')
                run = self.submit_none(board, cwd=folder, access=access, title='answer-' + access)
                self.finish_none(board, run)
                view = self.view(board, run)
                self.assertEqual(view['project']['kind'], 'git')
                for item in (view, view['workspace'], view['currentTurn'], view['artifacts'][0]):
                    self.assertEqual(item['folderChangesNotice'], FOLDER_CHANGES_NOTICE)
                    self.assertTrue(item['unsealedChanges'])
                def service(operation, params, directory):
                    response = board.call(operation, params)
                    return response['task'] if operation == 'task_get' else response
                waited = await_run({'runId': run['runId'], 'waitSeconds': 1}, board.directory, service=service)
                self.assertEqual(waited['folderChangesNotice'], FOLDER_CHANGES_NOTICE)
                artifact = view['finalArtifactId']
                with self.assertRaises(BoardError) as error:
                    board.call('workflow_accept', {'runId': run['runId'], 'artifactId': artifact, 'note': 'invalid target',
                                                  'target': {'path': str(folder), 'ref': 'HEAD'}})
                self.assertEqual(error.exception.code, 'INVALID_ARGUMENT')
                board.call('workflow_conclude', {'runId': run['runId'], 'artifactId': artifact, 'note': 'Keep local changes'})
                self.assertEqual(source.read_text(), 'unsealed source\n')

    def test_assistance_keeps_result_and_continuation_produces_new_fixed_result(self):
        board = self.board()
        run = self.submit_none(board, access='write')
        self.finish_none(board, run, disposition='assistance')
        waiting = self.view(board, run)
        old = waiting['artifacts'][0]['artifactId']
        with self.assertRaises(BoardError) as error:
            board.call('workflow_accept', {'runId': run['runId'], 'artifactId': old,
                                          'note': 'Not a completed answer', 'notRequired': 'answer'})
        self.assertEqual(error.exception.code, 'NOT_READY')
        board.call('workflow_continue', {'runId': run['runId'], 'requestId': waiting['activeRequest']['requestId'],
                                        'input': 'Use the documented choice', 'access': 'read'})
        self.finish_none(board, run)
        view = self.view(board, run)
        self.assertEqual(view['state'], 'delivered')
        self.assertNotEqual(view['finalArtifactId'], old)
        self.assertNotIn('unsealedChanges', view['currentTurn'])
        self.assertEqual(view['turns'][1]['folderChangesNotice'], FOLDER_CHANGES_NOTICE)
        self.assertEqual(len(view['artifacts']), 2)
        with self.assertRaises(BoardError) as error:
            board.call('workflow_accept', {'runId': run['runId'], 'artifactId': old,
                                          'note': 'Old response', 'notRequired': 'answer'})
        self.assertEqual(error.exception.code, 'CONFLICT')

    def test_continue_answers_exact_result_and_failure_with_receipt_is_readable(self):
        board = self.board()
        run = self.submit_none(board)
        self.finish_none(board, run)
        old = self.view(board, run)['finalArtifactId']
        board.call('workflow_continue', {'runId': run['runId'], 'artifactId': old, 'input': 'Check another point'})
        self.finish_none(board, run, runner_status='failed')
        view = self.view(board, run)
        self.assertEqual(view['state'], 'failed')
        self.assertEqual(len(view['artifacts']), 2)
        current = next(a for a in view['artifacts'] if a['turnId'] == view['currentTurn']['turnId'])
        self.assertIn('resultFile', current)
        board.call('workflow_conclude', {'runId': run['runId'], 'artifactId': current['artifactId'], 'note': 'Failure inspected'})
        self.assertTrue(self.workdir().is_dir())

    def test_cancel_and_macro_delete_only_remove_private_results(self):
        board = self.board()
        folder = self.workdir()
        source = folder / 'keep.txt'
        source.write_text('user source\n')
        identity = board.call('workflow_get', {'cwd': str(folder)})['project']['id']
        macro = board.call('workflow_create', {'cwd': str(folder), 'title': 'Review', 'goal': 'Inspect',
                          'creationKey': _creation_key(['adr032-create-v1', identity, 'Review'])})['macroTaskId']
        run = self.submit_none(board, cwd=folder, access='write', macro=macro)
        self.finish_none(board, run)
        fixed = Path(self.view(board, run)['artifacts'][0]['resultFile']['path'])
        board.call('workflow_cancel', {'runId': run['runId']})
        board.call('workflow_conclude', {'runId': run['runId'], 'note': 'Stop and clear board'})
        self.assertEqual(source.read_text(), 'user source\n')
        self.assertEqual(macro_task_deletion.delete(board.store, macro, 1)['state'], 'deleted')
        self.assertFalse(fixed.exists())
        self.assertEqual(source.read_text(), 'user source\n')

    def test_folder_git_conversion_keeps_old_none_run_and_turn_facts(self):
        board = self.board()
        folder = self.workdir()
        run = self.submit_none(board, cwd=folder)
        self.finish_none(board, run)
        before = self.view(board, run)
        subprocess.run(['git', '-C', str(folder), 'init', '-q'], check=True, capture_output=True)
        newer = self.submit_none(board, cwd=folder, title='After Git')
        self.assertEqual(self.view(board, newer)['project']['id'], before['project']['id'])
        self.assertEqual(self.view(board, newer)['project']['kind'], 'git')
        old = self.view(board, run)
        self.assertEqual(old['project']['kind'], 'folder')
        self.assertEqual(old['currentTurn']['projectKind'], 'folder')
        self.assertEqual(old['workspace']['projectKind'], 'folder')

    def test_result_digest_is_fixed_and_cannot_be_changed_before_acceptance(self):
        board = self.board()
        run = self.submit_none(board)
        self.finish_none(board, run)
        view = self.view(board, run)
        artifact = view['artifacts'][0]
        path = Path(artifact['resultFile']['path'])
        original = path.read_bytes()
        path.write_bytes(original + b'\n')
        with self.assertRaises(BoardError) as error:
            board.call('workflow_accept', {'runId': run['runId'], 'artifactId': artifact['artifactId'],
                                          'note': 'Changed receipt must fail', 'notRequired': 'answer'})
        self.assertEqual(error.exception.code, 'RECEIPT_MISMATCH')
        path.write_bytes(original)

    def test_none_helper_hands_fixed_result_back_without_transferring_a_checkout(self):
        board = self.board()
        run = self.submit_none(board, access='write')
        self.finish_none(board, run, disposition='attention')
        view = self.view(board, run)
        approved = self.decide(board, view, view['activeRequest']['requestId'], helpers=[{
            'cwd': str(self.workdir()), 'task': 'Check the answer independently',
            'checkout': 'none', 'access': 'read'}], autoContinue=True)
        child = approved['children'][0]['taskId']
        helper_claim = self.claim(board, run_id=child, claim_request_id='helper-claim')
        self.assertEqual(helper_claim['claim']['turn']['input']['executionWorkspace']['kind'], 'none')
        self.finish_turn(board, helper_claim, seal=False)
        resumed = self.claim(board, run_id=run['runId'], claim_request_id='parent-resume')
        handoff = resumed['claim']['turn']['input']['context']['helperOutcomes'][0]
        self.assertEqual(handoff['artifact']['kind'], 'result')
        self.assertIn('resultFile', handoff['artifact'])
        self.assertNotIn('diffPath', handoff['artifact'])
        self.finish_turn(board, resumed, seal=False)
        with board.store.db.read() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM workspace_reservations').fetchone()[0], 0)

    def test_macro_none_helper_freezes_its_own_execution_project(self):
        board = self.board()
        folder = self.workdir('parent-folder')
        identity = board.call('workflow_get', {'cwd': str(folder)})['project']['id']
        macro = board.call('workflow_create', {'cwd': str(folder), 'title': 'Project A', 'goal': 'Review',
                          'creationKey': _creation_key(['adr032-create-v1', identity, 'Project A'])})['macroTaskId']
        parent = self.submit_none(board, cwd=folder, macro=macro)
        self.finish_none(board, parent, disposition='assistance')
        helper_cwd = self.workdir('helper-folder')
        expected = board.call('workflow_get', {'cwd': str(helper_cwd)})['project']['id']
        view = self.view(board, parent)
        approved = self.decide(board, view, view['activeRequest']['requestId'], helpers=[{
            'cwd': str(helper_cwd), 'task': 'Check from a separate project',
            'checkout': 'none', 'access': 'read'}], autoContinue=False)
        child = approved['children'][0]['taskId']
        claim = self.claim(board, run_id=child, claim_request_id='other-project-helper')
        frozen = claim['claim']['turn']['input']['executionWorkspace']
        self.assertEqual(frozen['projectId'], expected)
        self.assertEqual(frozen['path'], str(helper_cwd))
        self.assertNotEqual(frozen['projectId'], identity)
        self.finish_turn(board, claim, seal=False)

class NoneWorkerTurnTests(MicroTaskWorkerTestCase):
    def test_offline_native_worker_runs_at_folder_cwd_publishes_receipt_and_accepts(self):
        folder = self.workdir('ordinary-folder')
        task = {'cwd': str(folder), 'title': 'Answer in folder', 'task': 'run the mock turn',
                'macroTaskId': 'standalone', 'buddy': ':'.join(BUDDY.values()),
                'checkout': 'none', 'access': 'write', 'expectedSeconds': 30}
        with self.daemon(env=self.env()):
            code, submitted = self.cli('submit', json.dumps(task), env=self.env())
            self.assertEqual(code, 0, submitted)
            def delivered():
                code, view = self.cli('get', json.dumps({'runId': submitted['runId'], 'output': 'full'}), env=self.env())
                self.assertEqual(code, 0, view)
                if view.get('state') in ('failed', 'awaiting-host', 'cancelled'):
                    self.fail(json.dumps(view))
                return view if view.get('state') == 'delivered' else None
            view = wait_for(delivered, timeout=60)
            self.assertEqual(view['workspace']['path'], str(folder))
            self.assertEqual(view['project']['kind'], 'folder')
            self.assertEqual(view['folderChangesNotice'], FOLDER_CHANGES_NOTICE)
            artifact = view['artifacts'][0]
            self.assertEqual(artifact['artifactId'], view['finalArtifactId'])
            receipt = json.loads(Path(artifact['resultFile']['path']).read_bytes())
            self.assertEqual(receipt['report']['result']['folderChangesNotice'], FOLDER_CHANGES_NOTICE)
            self.assertNotIn('workspaceSeal', receipt['report']['result'])
            # The fake native agent wrote into its actual cwd; no private checkout exists.
            self.assertTrue(any(path.name != '.git' for path in folder.iterdir()))
            self.assertFalse((self.directory / 'workspaces').exists())
            code, waited = self.cli('await', json.dumps({'runId': submitted['runId'], 'waitSeconds': 1}), env=self.env())
            self.assertEqual(code, 0, waited)
            self.assertEqual(waited['folderChangesNotice'], FOLDER_CHANGES_NOTICE)
            code, accepted = self.cli('accept', json.dumps({'runId': submitted['runId'],
                'artifactId': artifact['artifactId'], 'note': 'Checked the fixed fixture response',
                'notRequired': 'The answer is the delivered result'}), env=self.env())
            self.assertEqual(code, 0, accepted)
            self.assertEqual(accepted['state'], 'accepted')
            self.assertTrue(folder.is_dir())
