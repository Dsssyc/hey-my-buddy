"""Frozen attempt file ownership survives the retirement of helper lineage."""
import os
from types import SimpleNamespace
from unittest.mock import patch

from blackboard.tasks import test_lead_owned
from blackboard.tasks.test_workflow import WorkflowTestCase
from hey_my_buddy.blackboard.routing import router
from hey_my_buddy.blackboard.tasks import macro_task_deletion, storage
from hey_my_buddy.blackboard.tasks.session_content import identity
from hey_my_buddy.buddy.harnesses.session_record import SessionRecord
from hey_my_buddy.console.session_content import parse_query, read
from hey_my_buddy.errors import BoardError
from hey_my_buddy.paths import native_path
from hey_my_buddy.private_dirs import session_record_path
from hey_my_buddy.protocol.session_content import MessageChunk, TextContent
from support import seed_buddy


class SessionGroupRetirementTests(WorkflowTestCase):
    lead = test_lead_owned.LeadOwnedTests.lead
    owned = test_lead_owned.LeadOwnedTests.owned

    def accepted(self, board, name):
        run = self.submit(board, request_id=name, cwd=str(self.workdir(name)))
        claim = self.claim(board, run_id=run['runId'])
        self.finish_turn(board, claim)
        delivered = board.call('workflow_get', {'runId': run['runId']})
        board.call('workflow_accept', {'runId': run['runId'],
                   'artifactId': delivered['finalArtifactId'], 'note': 'Verified fixture',
                   'notRequired': 'The fixture has no integration target'})
        attempt = claim['claim']['attempt']['attemptId']
        board.store.complete_execution_cleanup(attempt)
        return run['runId'], attempt

    def record_session(self, board, group, attempt, content):
        path = session_record_path(board.directory, group, attempt)
        record = SessionRecord(path, attempt)
        record.append(MessageChunk(sessionUpdate='agent_message_chunk',
                                   content=TextContent(type='text', text=content)))
        record.close()
        self.assertIsNone(record.failure)
        return native_path(path)

    def test_admission_freezes_paths_even_before_lazy_record_creation(self):
        board = self.board(max_concurrent=3)
        lead, lead_claim = self.lead(board, kind='worktree')
        member = self.owned(board, lead_claim)
        member_claim = self.claim(board, run_id=member['runId'])['claim']
        group = 'run:' + lead['runId']
        for claim in (lead_claim, member_claim):
            attempt = claim['attempt']['attemptId']
            self.assertEqual(identity(board.store, claim['task']['taskId'], attempt)['session_group_id'], group)
            path = session_record_path(board.directory, group, attempt)
            self.assertEqual(claim['sessionRecordPath'], str(path))
            self.assertFalse(native_path(path).exists())
        plain = board.submit_execution(requestId='command', task='Independent execution',
                                       cwd=str(self.workdir('command')), adapter='command', argv=['test-command'],
                                       expectedSeconds=1800)
        run_id = plain['task']['runId']
        claim = self.claim(board, run_id=run_id)['claim']
        self.assertIsNone(claim['sessionRecordPath'])
        self.assertIsNone(identity(board.store, run_id, claim['attempt']['attemptId'])['session_group_id'])

    def test_deleting_historical_parent_preserves_split_run_session(self):
        board = self.board()
        parent, parent_attempt = self.accepted(board, 'historical-parent')
        split, split_attempt = self.accepted(board, 'now-standalone')
        old_group = 'run:' + parent
        current_group = 'run:' + split
        with board.store.db.write() as connection:
            # The source migration writes the historical directory before dropping the edge.
            connection.execute('UPDATE attempts SET session_group_id=? WHERE attempt_id=?',
                               (old_group, split_attempt))
        parent_path = self.record_session(board, old_group, parent_attempt, 'parent')
        split_path = self.record_session(board, old_group, split_attempt, 'split')
        split_identity = identity(board.store, split, split_attempt)
        self.assertEqual((split_identity['group_id'], split_identity['session_group_id']),
                         (current_group, old_group))
        with patch.object(storage, 'process_inventory', return_value=([], [], True)):
            records = [row for row in storage.inspect(board.store)['candidates']
                       if row['category'] == 'session-records']
        self.assertEqual({(row['taskId'], row['attemptId'], row['groupId'], row['sessionGroupId']) for row in records},
                         {(parent, parent_attempt, old_group, old_group),
                          (split, split_attempt, current_group, old_group)})
        delete_records = macro_task_deletion._delete_session_records

        def read_while_deleting(store, scope):
            with self.assertRaises(BoardError) as deleting:
                read(store, parent, parent_attempt, parse_query(''))
            self.assertEqual(deleting.exception.code, 'SESSION_DELETED')
            page = read(store, split, split_attempt, parse_query(''))
            self.assertEqual(page['events'][0]['update']['content']['text'], 'split')
            return delete_records(store, scope)

        with patch.object(macro_task_deletion, '_delete_workspaces', return_value=[]), \
                patch.object(macro_task_deletion, '_delete_session_records', side_effect=read_while_deleting):
            self.assertEqual(macro_task_deletion.delete(board.store, old_group, 1)['state'], 'deleted')
        self.assertFalse(parent_path.exists())
        self.assertTrue(split_path.is_file())
        with patch.object(macro_task_deletion, '_delete_workspaces', return_value=[]):
            self.assertEqual(macro_task_deletion.delete(board.store, current_group, 1)['state'], 'deleted')
        self.assertFalse(split_path.parent.exists())

    def test_deletion_retains_linked_record_and_its_target_for_retry(self):
        board = self.board()
        run, attempt = self.accepted(board, 'linked-record')
        group = 'run:' + run
        target = self.workdir('outside') / 'session.jsonl'
        target.write_text('unowned data', encoding='utf-8')
        path = native_path(session_record_path(board.directory, group, attempt))
        path.parent.mkdir(parents=True)
        try:
            path.symlink_to(target)
        except OSError as error:
            if os.name == 'nt' and getattr(error, 'winerror', None) == 1314:
                self.skipTest('Windows symlink privilege is unavailable')
            raise
        with patch.object(macro_task_deletion, '_delete_workspaces', return_value=[]):
            result = macro_task_deletion.delete(board.store, group, 1)
            self.assertEqual(result['state'], 'deleting')
            self.assertEqual(result['failures'][0]['reason']['code'], 'macro-task-deletion.owner-unproven')
            self.assertTrue(path.is_symlink())
            self.assertEqual(target.read_text(encoding='utf-8'), 'unowned data')
            path.unlink()
            retry = macro_task_deletion.delete(board.store, group, 1)
        self.assertEqual((retry['state'], retry['deletionId']), ('deleted', result['deletionId']))
        self.assertFalse(path.parent.exists())

    def test_shared_directory_pending_attempt_fences_empty_directory_removal(self):
        board = self.board()
        parent, parent_attempt = self.accepted(board, 'old-root')
        run = self.submit(board, request_id='live-root', cwd=str(self.workdir('live-root')))
        live = self.claim(board, run_id=run['runId'])['claim']['attempt']['attemptId']
        group = 'run:' + parent
        with board.store.db.write() as connection:
            connection.execute('UPDATE attempts SET session_group_id=? WHERE attempt_id=?', (group, live))
        path = self.record_session(board, group, parent_attempt, 'parent')
        scope = SimpleNamespace(attempt_rows=[{'session_group_id': group, 'attempt_id': parent_attempt}])
        with storage.locked(board.store):
            self.assertEqual(macro_task_deletion._delete_session_records(board.store, scope), [])
        self.assertFalse(path.exists())
        self.assertTrue(path.parent.is_dir())

    def test_checkout_none_router_boundary_allows_continuation(self):
        board = self.board()
        with board.store.db.write() as connection:
            for model in ('one', 'two'):
                seed_buddy(connection, buddy_id='fixture-' + model, harness='dsh', provider='fixture',
                           model=model, effort='max', now=board.store.now())
            router._write_settings(connection, {**router.configuration(connection), 'routerBuddyIds': []})
        run = self.submit(board, request_id='routed-folder', kind='none', buddy='route')
        boundary = run['routing']['routingBoundary']
        self.assertIsNone(boundary['continuationProblem'])
        self.assertEqual(boundary['actions'], ['continue', 'cancel'])
        result = board.call('workflow_continue', {
            'runId': run['runId'], 'requestId': run['activeRequest']['requestId'], 'input': 'Use the folder directly',
            'buddy': 'dsh / fixture / one / max', 'note': 'Select the Worker after routing stopped',
        })
        self.assertEqual(result['state'], 'executing')
