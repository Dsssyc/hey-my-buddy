"""Permission choices freeze on turns and follow explicit continuation boundaries."""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from blackboard.tasks import test_workspace_lifecycle as lifecycle_fixtures
from blackboard.tasks.test_workflow import WorkflowTestCase
from blackboard.tasks.test_workflow_real import BUDDY, RealWorkspaceTestCase
from hey_my_buddy.blackboard.tasks import workspace as git_workspace
from hey_my_buddy.buddy.harnesses.registry import _WORKER_PERMISSIONS
from hey_my_buddy.errors import BoardError
from hey_my_buddy.cli.cli_views import micro_task_brief


class PermissionLevelsTests(WorkflowTestCase):
    def setUp(self):
        super().setUp()
        self.executors['claude'] = SimpleNamespace(native_resume=False, native_resume_permission_change=False, validate_turn_provenance=lambda record: None)

    def claude_run(self, board, **extra):
        return self.submit(board, harness='claude', provider='fixture', model='fixture', effort='high', **extra)

    def register_claude(self, board):
        return board.call('worker_register', {'workerId': 'w1', 'capabilities': ['claude']})

    def test_submit_and_continue_reject_missing_harness_permission_without_mutation(self):
        self.enterContext(patch.dict(_WORKER_PERMISSIONS, {'dsh': {
            level: declaration for level, declaration in _WORKER_PERMISSIONS['dsh'].items()
            if level != 'full'}}))
        board = self.board()
        for access, reason in [('full', 'Need system writes')]:
            with self.subTest(access=access):
                kwargs = {'executionWorkspace': {'kind': 'existing', 'access': access}}
                if reason:
                    kwargs['executionWorkspace']['accessReason'] = reason
                with self.assertRaises(BoardError) as rejected:
                    self.submit(board, **kwargs)
                self.assertEqual(rejected.exception.code, 'UNSUPPORTED_CAPABILITY')
                self.assertEqual(rejected.exception.details['requiredCapability'], 'permission:' + access)
        self.register(board)
        view = self.submit(board)
        claim = self.claim(board, run_id=view['runId'])
        self.finish_turn(board, claim)
        view = board.call('workflow_get', {'runId': view['runId']})
        for access, reason in [('full', 'Need system writes')]:
            kwargs = {'access': access}
            if reason:
                kwargs['accessReason'] = reason
            with self.assertRaises(BoardError) as rejected:
                self.continue_run(board, view, **kwargs)
            self.assertEqual(rejected.exception.code, 'UNSUPPORTED_CAPABILITY')
            after = board.call('workflow_get', {'runId': view['runId']})
            self.assertEqual(after['revision'], view['revision'])
            self.assertEqual(after['access'], 'write')

    def test_explicit_read_remains_available_for_every_harness(self):
        board = self.board()
        for harness in ('codex', 'dsh', 'zcode', 'claude'):
            with self.subTest(harness=harness):
                view = self.submit(board, request_id='read-' + harness, harness=harness,
                                   provider='fixture', model='fixture', effort='high', access='read',
                                   cwd=str(self.workdir('read-' + harness)))
                self.assertEqual(view['access'], 'read')
                self.assertEqual(view['buddy']['harness'], harness)
                self.assertEqual(micro_task_brief(view)['access'], 'read')
                self.assertNotIn('enforcement', view)
                self.assertNotIn('enforcement', micro_task_brief(view))

    def test_command_and_external_are_rejected_before_permission_checks(self):
        board = self.board()
        for adapter in ('command', 'external'):
            with self.subTest(adapter=adapter):
                fields = {'argv': ['true']} if adapter == 'command' else {}
                with self.assertRaises(BoardError) as rejected:
                    self.submit(board, request_id='full-' + adapter, adapter=adapter, **fields,
                                executionWorkspace={'kind': 'existing', 'access': 'full',
                                                    'accessReason': 'Exercise the existing connector permission gate'})
                self.assertEqual(rejected.exception.code, 'INVALID_ARGUMENT')
                self.assertEqual(rejected.exception.details['field'], 'adapter')
                self.assertIn('execution-submit', rejected.exception.message)
        with board.store.db.read() as connection:
            self.assertEqual(connection.execute('SELECT COUNT(*) FROM tasks').fetchone()[0], 0)

    def test_turns_freeze_access_and_full_reason_then_inherit(self):
        board = self.board()
        self.register_claude(board)
        view = self.claude_run(board, access='read')
        expected = [('read', None), ('write', None), ('full', 'Need system writes'), ('full', 'Need system writes')]
        for index, (access, reason) in enumerate(expected):
            if index:
                current = board.call('workflow_get', {'runId': view['runId']})
                kwargs = {} if index == 3 else {'access': access}
                if reason and index != 3:
                    kwargs['accessReason'] = reason
                self.continue_run(board, current, command_id=f'continue-{index}', **kwargs)
            claim = self.claim(board, run_id=view['runId'], claim_request_id=f'claim-{index}')
            self.assertIsNotNone(claim['claim'])
            workspace = claim['claim']['turn']['input']['executionWorkspace']
            self.assertEqual(workspace['access'], access)
            self.assertEqual(workspace.get('accessReason'), reason)
            self.finish_turn(board, claim, session_id=f'session-{index}')
        full = board.call('workflow_get', {'runId': view['runId'], 'includeAudit': True})
        turns = sorted(full['audit']['turns'], key=lambda turn: turn['turnIndex'])
        self.assertEqual([(turn['access'], turn['accessReason']) for turn in turns], expected)
        self.assertEqual(micro_task_brief(full)['access'], 'full')
        with board.store.db.read() as connection:
            stored = connection.execute('SELECT access,access_reason FROM workflow_turns ORDER BY turn_index').fetchall()
            self.assertEqual([tuple(row) for row in stored], expected)
            changes = connection.execute("SELECT payload_json FROM events WHERE kind='workflow.access_changed' ORDER BY seq").fetchall()
            self.assertEqual([json.loads(row[0])['access'] for row in changes], ['write', 'full'])
            self.assertEqual(json.loads(changes[-1][0])['accessReason'], 'Need system writes')
            self.assertEqual(connection.execute("SELECT access FROM workspace_reservations WHERE state='held'").fetchone()[0], 'write')

    def test_access_change_before_first_claim_uses_the_new_selection(self):
        board = self.board()
        self.register_claude(board)
        view = self.claude_run(board)
        changed = self.continue_run(board, view, access='read')
        self.assertEqual(changed['access'], 'read')
        claim = self.claim(board, run_id=view['runId'])
        self.assertEqual(claim['claim']['turn']['input']['executionWorkspace']['access'], 'read')
        self.assertEqual(claim['claim']['turn']['turnIndex'], 1)

    def test_continue_unknown_access_and_invalid_reason_are_rejected(self):
        board = self.board()
        view = self.submit(board)
        for kwargs in ({'access': 'unknown'}, {'access': 'full'}, {'access': 'read', 'accessReason': 'Reason'},
                       {'accessReason': 'Reason'}, {'access': 'full', 'accessReason': 'x' * 1001}):
            with self.subTest(kwargs=kwargs), self.assertRaises(BoardError) as rejected:
                self.continue_run(board, view, **kwargs)
            self.assertEqual(rejected.exception.code, 'INVALID_ARGUMENT')


class GitPermissionLevelsTests(RealWorkspaceTestCase):
    register = lifecycle_fixtures.LifecycleTestCase.register
    control = lifecycle_fixtures.LifecycleTestCase.control
    claim = lifecycle_fixtures.LifecycleTestCase.claim
    turn_record = lifecycle_fixtures.LifecycleTestCase.turn_record
    finish_turn = lifecycle_fixtures.LifecycleTestCase.finish_turn
    view = lifecycle_fixtures.LifecycleTestCase.view

    def setUp(self):
        super().setUp()
        self.controls = {}
        self.worker = "w-life"

    def test_real_checkout_reprepares_permissions_without_rewriting_history(self):
        # Exercise storage and Git ownership with DSH's declared permission
        # levels; no native DSH process is started by these receipts.
        board = self.board()
        self.register(board)
        submitted = board.call('workflow_submit', {
            **BUDDY, 'requestId': 'permission-git', 'hostId': 'host-1', 'task': 'Inspect and change',
            'cwd': str(self.repo), 'submissionToken': 't' * 32,
            'executionWorkspace': {'kind': 'worktree', 'access': 'write'},
        })
        run_id = submitted['runId']
        self.controls[run_id] = submitted['control']
        saved = []
        for index, access in enumerate(('write', 'read', 'full', 'write')):
            if index:
                current = self.view(board, run_id)
                board.call('workflow_continue', {
                    'runId': run_id, 'commandId': f'git-continue-{index}', 'expectedRevision': current['revision'],
                    **self.control(current), 'input': 'Continue at next permission', 'access': access,
                    **({'accessReason': 'Need system writes'} if access == 'full' else {}),
                })
            claim = self.claim(board, run_id, request_id=f'git-claim-{index}')
            self.assertIsNotNone(claim['claim'])
            manifest = claim['claim']['turn']['input']['executionWorkspace']
            git_workspace.verify(manifest, require_unchanged=True)
            saved.append(dict(manifest))
            self.assertEqual(manifest['access'], access)
            if access != 'read':
                (Path(manifest['path']) / 'tracked.txt').write_text(f'turn {index}\n')
            attempt_id = claim['claim']['attempt']['attemptId']
            seal = git_workspace.seal(self.directory, manifest, run_id, attempt_id)
            self.finish_turn(board, claim, seal)
        with board.store.db.read() as connection:
            turns = connection.execute('SELECT input_json,access,access_reason FROM workflow_turns ORDER BY turn_index').fetchall()
            self.assertEqual([json.loads(row[0])['executionWorkspace'] for row in turns], saved)
            self.assertEqual([row[1] for row in turns], ['write', 'read', 'full', 'write'])
            self.assertEqual([row[2] for row in turns], [None, None, 'Need system writes', None])
            self.assertEqual(connection.execute("SELECT access FROM workspace_reservations WHERE state='held'").fetchone()[0], 'write')

    def test_full_reservation_is_recognized_at_a_routing_boundary(self):
        board = self.board()
        self.register(board)
        submitted = board.call('workflow_submit', {
            **BUDDY, 'requestId': 'permission-route-git', 'hostId': 'host-1', 'task': 'Full storage route',
            'cwd': str(self.repo), 'submissionToken': 't' * 32,
            'executionWorkspace': {'kind': 'worktree', 'access': 'full', 'accessReason': 'Need system writes'},
        })
        run_id = submitted['runId']
        self.controls[run_id] = submitted['control']
        claim = self.claim(board, run_id)
        manifest = claim['claim']['turn']['input']['executionWorkspace']
        seal = git_workspace.seal(self.directory, manifest, run_id, claim['claim']['attempt']['attemptId'])
        self.finish_turn(board, claim, seal)
        current = self.view(board, run_id)
        board.call('workflow_continue', {'runId': run_id, 'commandId': 'full-route',
                   'expectedRevision': current['revision'], **self.control(current), 'input': 'Choose again', 'reroute': True})
        board.store.workflow.prepare_continuation_workspace({'runId': run_id})
        from hey_my_buddy.blackboard.routing.router_boundary_data import _continuation_context
        with board.store.db.read() as connection:
            run = connection.execute('SELECT * FROM workflow_runs WHERE run_id=?', (run_id,)).fetchone()
            row = board.store.decisions._row(connection, run['current_routing_id'])
            stopped, problem, _policy, _run = _continuation_context(connection, row, run_id, run_id)
            self.assertTrue(stopped)
            self.assertIsNone(problem)

    def test_permission_only_preparation_preserves_untracked_input_selectors(self):
        (self.repo / 'input.txt').write_text('Explicit input\n')
        board = self.board()
        self.register(board)
        submitted = board.call('workflow_submit', {
            **BUDDY, 'requestId': 'permission-input', 'hostId': 'host-1', 'task': 'Read input',
            'cwd': str(self.repo), 'submissionToken': 't' * 32,
            'executionWorkspace': {'kind': 'existing', 'access': 'write', 'includeUntracked': ['input.txt']},
        })
        self.controls[submitted['runId']] = submitted['control']
        board.call('workflow_continue', {'runId': submitted['runId'], 'commandId': 'read-input',
                   'expectedRevision': submitted['revision'], **self.control(submitted), 'input': 'Inspect', 'access': 'read'})
        claim = self.claim(board, submitted['runId'])
        manifest = claim['claim']['turn']['input']['executionWorkspace']
        self.assertEqual(manifest['snapshot']['executionSelectors'], ['input.txt'])
        self.assertEqual(manifest['access'], 'read')
        git_workspace.verify(manifest, require_unchanged=True)
