"""Browser-only deletion through real HTTP, board transactions and Git allocations."""
import json
from pathlib import Path
from unittest.mock import patch

from blackboard.tasks import test_workflow as workflow_fixtures, test_workspace as workspace_fixtures
from console.test_console import ConsoleTestCase
from hey_my_buddy.blackboard.tasks import macro_task_deletion, workflow, workspace
from hey_my_buddy.errors import BoardError
from hey_my_buddy.protocol.reasons import reason


PREVIEW = 'macro_task_deletion_preview'
DELETE = 'macro_task_delete'


class ConsoleMacroTaskDeletionTests(workflow_fixtures.WorkflowTestCase, ConsoleTestCase):
    git = workspace_fixtures.WorkspaceTests.git

    def setUp(self):
        super().setUp()
        self.repo = self.workdir('deletion-api')
        workspace_fixtures.WorkspaceTests._seed_repository(self)
        self.enterContext(patch.object(workflow, '_workspace_module', workspace))

    def create(self, board, request_id='owned', *, settled=True, **extra):
        run = self.submit(board, request_id=request_id, cwd=str(self.repo), kind='worktree', **extra)
        if settled:
            board.call('workflow_cancel', {'runId': run['runId'], **self.control(run)})
        return run

    def command(self, browser, operation, params, *, status=200, code=None):
        actual, _, raw = browser.command(operation, params, csrf=browser.bootstrap()['csrfToken'])
        self.assertEqual(actual, status, raw)
        payload = json.loads(raw)
        if code is not None:
            self.assertEqual(set(payload), {'ok', 'error'})
            self.assertIs(payload['ok'], False)
            self.assertEqual(payload['error']['code'], code)
            return payload['error']
        self.assertEqual(set(payload), {'ok', 'result'})
        self.assertIs(payload['ok'], True)
        return payload['result']

    def test_browser_previews_and_deletes_whole_macro_task_without_residual_record(self):
        board = self.board()
        first = self.create(board, macroTask={'title': 'Disposable'})
        second = self.create(board, 'second', macroTaskId=first['macroTaskId'])
        other = self.create(board, 'retained', macroTask={'title': 'Retained'})
        _, browser = self.open_console(board)
        target = first['macroTaskId']
        preview = self.command(browser, PREVIEW, {'macroTaskId': target})
        self.assertEqual(preview, {
            'macroTaskId': target, 'microTaskCount': 2, 'unconcludedFailureCount': 2,
            'blockerTotal': 0, 'blockers': [], 'backupRetention': 'until-backup-rotation', 'deletionState': None,
            'scope': {'artifacts': {'count': 2, 'bytes': None}, 'sessions': {'count': 0, 'bytes': 0},
                      'checkouts': 2, 'gitRefs': 8},
        })
        params = {'macroTaskId': target, 'microTaskCount': preview['microTaskCount'], 'confirm': True}
        with board.store.db.read() as connection:
            checkouts = [Path(json.loads(row[0])['path']) for row in connection.execute(
                'SELECT workspace_manifest_json FROM workflow_runs WHERE run_id IN (?,?)',
                (first['runId'], second['runId']))]
        deleted = self.command(browser, DELETE, params)
        self.assertEqual(set(deleted), {'deletionId', 'macroTaskId', 'state', 'deletedAt', 'microTaskCount', 'duplicate'})
        self.assertEqual((deleted['state'], deleted['microTaskCount'], deleted['duplicate']), ('deleted', 2, False))
        self.assertIsNotNone(deleted['deletedAt'])
        self.command(browser, DELETE, params, status=404, code='NOT_FOUND')
        self.command(browser, PREVIEW, {'macroTaskId': target}, status=404, code='NOT_FOUND')
        with board.store.db.read() as connection:
            self.assertEqual(connection.execute('SELECT COUNT(*) FROM macro_task_deletions').fetchone()[0], 0)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM events WHERE kind='macro_task.deleted'").fetchone()[0], 0)
            self.assertEqual(connection.execute('PRAGMA foreign_key_check').fetchall(), [])
        self.assertTrue(all(not checkout.exists() for checkout in checkouts))
        self.assertEqual(board.call('workflow_get', {'runId': other['runId']})['state'], 'cancelled')

    def test_standalone_tree_uses_the_same_console_operations(self):
        board = self.board()
        run = self.create(board)
        _, browser = self.open_console(board)
        target = 'run:' + run['runId']
        preview = self.command(browser, PREVIEW, {'macroTaskId': target})
        self.assertEqual(preview['microTaskCount'], 1)
        self.assertEqual(self.command(browser, DELETE, {
            'macroTaskId': target, 'microTaskCount': 1, 'confirm': True,
        })['state'], 'deleted')

    def test_missing_repository_with_allocation_deletes_macro_task_records(self):
        self.missing_repository_deletion(allocation_present=True)

    def test_missing_repository_with_only_refs_deletes_macro_task_records(self):
        self.missing_repository_deletion(allocation_present=False)

    def missing_repository_deletion(self, *, allocation_present):
        board = self.board()
        run = self.create(board, macroTask={'title': 'Missing repository'})
        with board.store.db.read() as connection:
            manifest = json.loads(connection.execute(
                'SELECT workspace_manifest_json FROM workflow_runs WHERE run_id=?',
                (run['runId'],)).fetchone()[0])
        directory = board.store.directory / 'workspaces' / manifest['workspaceId']
        if not allocation_present:
            directory.rename(directory.with_name('retained-' + manifest['workspaceId']))
        prefix = f"refs/hey-my-buddy/workspaces/{manifest['workspaceId']}/"
        refs = self.git('for-each-ref', '--format=%(refname)%00%(objectname)', prefix)
        self.assertTrue(refs)
        retained = self.repo.with_name('retained-repository')
        self.repo.rename(retained)
        try:
            _, browser = self.open_console(board)
            result = self.command(browser, DELETE, {
                'macroTaskId': run['macroTaskId'], 'microTaskCount': 1, 'confirm': True,
            })
            self.assertEqual(result['state'], 'deleted')
            self.assertIsNotNone(result['deletedAt'])
            self.assertEqual(result['skipped'], [{
                'kind': 'allocation' if allocation_present else 'ref',
                'path': manifest['snapshot']['repositoryPath'], 'workspaceId': manifest['workspaceId'],
                'reason': {'code': 'macro-task-deletion.repository-missing', 'params': {}},
            }])
            self.command(browser, PREVIEW, {'macroTaskId': run['macroTaskId']}, status=404, code='NOT_FOUND')
            with board.store.db.read() as connection:
                for table in ('macro_tasks', 'macro_task_deletions', 'workflow_runs', 'tasks',
                              'workflow_turns', 'workflow_artifacts', 'workflow_requests',
                              'workspace_reservations', 'commands'):
                    self.assertEqual(connection.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0], 0, table)
                self.assertEqual(connection.execute('PRAGMA foreign_key_check').fetchall(), [])
            self.assertEqual(directory.exists(), allocation_present)
            self.assertEqual(self.git('for-each-ref', '--format=%(refname)%00%(objectname)', prefix,
                                      cwd=retained), refs)
        finally:
            retained.rename(self.repo)

    def test_service_host_and_worker_credentials_do_not_authorize_either_operation(self):
        board = self.board()
        owner = self.create(board, macroTask={'title': 'Console owned'})
        target = owner['macroTaskId']
        worker = self.create(board, 'active-worker', settled=False)
        credential = self.claim(board, run_id=worker['runId'])['claim']['agentCredential']
        for operation, params in ((PREVIEW, {'macroTaskId': target}),
                                  (DELETE, {'macroTaskId': target, 'microTaskCount': 1, 'confirm': True})):
            for authority in ({}, {'controlToken': self.controls[owner['runId']]['controlToken']},
                              {'credential': credential}, {'consoleAuthority': {'sessionId': 'forged'}}):
                with self.subTest(operation=operation, authority=list(authority)), self.assertRaises(BoardError) as caught:
                    board.call(operation, {**params, **authority})
                self.assertEqual(caught.exception.code, 'FORBIDDEN' if 'credential' in authority else 'UNAUTHORIZED')
        with board.store.db.read() as connection:
            self.assertEqual(connection.execute('SELECT COUNT(*) FROM macro_task_deletions').fetchone()[0], 0)

    def test_requests_reject_invalid_identifiers_unknown_fields_confirmation_and_counts(self):
        board = self.board()
        target = self.create(board, macroTask={'title': 'Validated target'})['macroTaskId']
        _, browser = self.open_console(board)
        valid = {'macroTaskId': target, 'microTaskCount': 1, 'confirm': True}
        for identifier in (None, 1, '', 'unknown', 'macro-', 'run:', 'macro-../escape',
                           'run:../escape', 'macro-with_space', 'run:invalid\nidentifier', 'macro-' + 'a' * 121):
            for operation, params in ((PREVIEW, {'macroTaskId': identifier}),
                                      (DELETE, {**valid, 'macroTaskId': identifier})):
                with self.subTest(operation=operation, identifier=identifier):
                    self.command(browser, operation, params, status=400, code='INVALID_ARGUMENT')
        for operation, params in ((PREVIEW, {}), (DELETE, {'microTaskCount': 1, 'confirm': True}),
                                  (PREVIEW, {'macroTaskId': target, 'microTaskCount': 1}),
                                  (DELETE, {key: value for key, value in valid.items() if key != 'confirm'}),
                                  (DELETE, {key: value for key, value in valid.items() if key != 'microTaskCount'})):
            with self.subTest(operation=operation, params=params):
                self.command(browser, operation, params, status=400, code='INVALID_ARGUMENT')
        for confirm in (False, None, 0, 1, 'true', {}, []):
            with self.subTest(confirm=confirm):
                self.command(browser, DELETE, {**valid, 'confirm': confirm}, status=400, code='INVALID_ARGUMENT')
        for count in (None, -1, True, False, 1.0, '1', {}, []):
            with self.subTest(count=count):
                self.command(browser, DELETE, {**valid, 'microTaskCount': count}, status=400, code='INVALID_ARGUMENT')
        for field in ('scopeToken', 'sessionId', 'path', 'unexpected'):
            for operation, params in ((PREVIEW, {'macroTaskId': target}), (DELETE, valid)):
                with self.subTest(operation=operation, field=field):
                    error = self.command(browser, operation, {**params, field: 'untrusted'},
                                         status=400, code='INVALID_ARGUMENT')
                    self.assertEqual(error['details']['field'], field)
        with board.store.db.read() as connection:
            self.assertEqual(connection.execute('SELECT COUNT(*) FROM macro_task_deletions').fetchone()[0], 0)

    def test_new_operations_keep_the_origin_csrf_and_authority_field_boundaries(self):
        board = self.board()
        target = self.create(board)['runId']
        _, browser = self.open_console(board)
        for operation, params in ((PREVIEW, {'macroTaskId': 'run:' + target}),
                                  (DELETE, {'macroTaskId': 'run:' + target, 'microTaskCount': 1, 'confirm': True})):
            with self.subTest(operation=operation):
                status, _, raw = browser.command(operation, params)
                self.assertEqual(status, 403, raw)
                self.assertIs(json.loads(raw)['ok'], False)
                csrf = browser.bootstrap()['csrfToken']
                status, _, raw = browser.call('POST', browser.prefix + '/api/command',
                    body={'operation': operation, 'params': params},
                    headers={'Content-Type': 'application/json', 'Origin': 'https://outside.invalid',
                             'X-Hey-My-Buddy-CSRF': csrf})
                self.assertEqual(status, 403, raw)
                self.assertIs(json.loads(raw)['ok'], False)
                self.command(browser, operation, {**params, 'consoleAuthority': 'forged'},
                             status=400, code='INVALID_ARGUMENT')

    def test_unsettled_preview_and_delete_share_structured_blockers(self):
        board = self.board()
        target = self.create(board, settled=False, macroTask={'title': 'Queued'})['macroTaskId']
        _, browser = self.open_console(board)
        preview = self.command(browser, PREVIEW, {'macroTaskId': target})
        self.assertGreater(preview['blockerTotal'], 0)
        for blocker in preview['blockers']:
            self.assertEqual(set(blocker), {'runId', 'taskId', 'state', 'runState', 'reason', 'attemptIds',
                                           'title', 'titleSource'})
            self.assertEqual((blocker['title'], blocker['titleSource']), ('do the thing', 'task'))
            self.assertEqual(set(blocker['reason']), {'code', 'params'})
            self.assertEqual(blocker['reason']['params'], {})
        error = self.command(browser, DELETE, {'macroTaskId': target, 'microTaskCount': 1, 'confirm': True},
                             status=409, code='MACRO_TASK_NOT_SETTLED')
        self.assertEqual(error['details']['blockers'], preview['blockers'])

    def test_console_stop_then_confirmed_cancelled_micro_tasks_can_delete_without_host_conclusion(self):
        board = self.board()
        first = self.create(board, settled=False, macroTask={'title': 'Stop then delete'})
        claim = self.claim(board, run_id=first['runId'])
        second = self.create(board, 'queued-to-stop', settled=False, macroTaskId=first['macroTaskId'])
        target = first['macroTaskId']
        _, browser = self.open_console(board)
        stopped = self.command(browser, 'macro_task_stop', {'macroTaskId': target, 'commandId': 'stop-before-delete'})
        self.assertEqual({entry['runId'] for entry in stopped['results']}, {first['runId'], second['runId']})
        self.assertTrue(all(entry['state'] == 'cancelled' and entry['stopped'] for entry in stopped['results']))
        # Console stop fences the active turn, but cannot invent its shutdown.
        preview = self.command(browser, PREVIEW, {'macroTaskId': target})
        self.assertIn('macro-task-deletion.stop-unconfirmed', {row['reason']['code'] for row in preview['blockers']})
        self.command(browser, DELETE, {'macroTaskId': target, 'microTaskCount': 2, 'confirm': True},
                     status=409, code='MACRO_TASK_NOT_SETTLED')
        self.finish_turn(board, claim, runner_status='cancelled', seal=False)
        for run in (first, second):
            view = board.call('workflow_get', {'runId': run['runId']})
            self.assertEqual((view['state'], view['status'], view['hostConclusion']), ('cancelled', 'cancelled', None))
        preview = self.command(browser, PREVIEW, {'macroTaskId': target})
        self.assertEqual((preview['microTaskCount'], preview['unconcludedFailureCount'], preview['blockerTotal']), (2, 2, 0))
        with board.store.db.read() as connection:
            self.assertEqual(connection.execute('SELECT COUNT(*) FROM workflow_host_conclusions').fetchone()[0], 0)
        deleted = self.command(browser, DELETE, {'macroTaskId': target, 'microTaskCount': 2, 'confirm': True})
        self.assertEqual(deleted['state'], 'deleted')
        with board.store.db.read() as connection:
            self.assertEqual(connection.execute('SELECT COUNT(*) FROM workflow_runs WHERE macro_task_id=?', (target,)).fetchone()[0], 0)
            self.assertEqual(connection.execute('PRAGMA foreign_key_check').fetchall(), [])

    def test_count_change_requires_a_new_preview_before_deletion(self):
        board = self.board()
        target = self.create(board, macroTask={'title': 'Growing group'})['macroTaskId']
        _, browser = self.open_console(board)
        preview = self.command(browser, PREVIEW, {'macroTaskId': target})
        self.create(board, 'new-member', macroTaskId=target)
        error = self.command(browser, DELETE, {
            'macroTaskId': target, 'microTaskCount': preview['microTaskCount'], 'confirm': True,
        }, status=409, code='REVISION_CONFLICT')
        self.assertEqual(error['details']['microTaskCount'], 2)
        with board.store.db.read() as connection:
            self.assertEqual(connection.execute('SELECT COUNT(*) FROM macro_task_deletions').fetchone()[0], 0)

    def test_unknown_well_formed_targets_return_not_found(self):
        board = self.board()
        _, browser = self.open_console(board)
        for target in ('macro-missing', 'run:missing'):
            self.command(browser, PREVIEW, {'macroTaskId': target}, status=404, code='NOT_FOUND')
            self.command(browser, DELETE, {'macroTaskId': target, 'microTaskCount': 0, 'confirm': True},
                         status=404, code='NOT_FOUND')

    def test_error_codes_keep_the_http_status_and_board_error_envelope(self):
        board = self.board()
        _, browser = self.open_console(board)
        for code, status in (('MACRO_TASK_NOT_SETTLED', 409), ('MACRO_TASK_STORAGE_IN_USE', 409),
                             ('MACRO_TASK_HARNESS_UNAVAILABLE', 409), ('MACRO_TASK_BUSY', 409),
                             ('STORAGE_BUSY', 409), ('TASK_DELETING', 409),
                             ('MACRO_TASK_DELETING', 409), ('REVISION_CONFLICT', 409), ('NOT_FOUND', 404)):
            for operation, seam, params in ((PREVIEW, 'preview', {'macroTaskId': 'macro-fixture'}),
                    (DELETE, 'delete', {'macroTaskId': 'macro-fixture', 'microTaskCount': 0, 'confirm': True})):
                with self.subTest(code=code, operation=operation), patch.object(
                        macro_task_deletion, seam, side_effect=BoardError(code, 'Fixture failure', deletionId='del-fixture')):
                    error = self.command(browser, operation, params, status=status, code=code)
                self.assertEqual(error, {'code': code, 'message': 'Fixture failure', 'details': {'deletionId': 'del-fixture'}})

    def test_unavailable_harness_refuses_with_structured_details_and_recovers(self):
        board = self.board()
        run = self.create(board, macroTask={'title': 'Harness health'})
        target = run['macroTaskId']
        session_id = '55555555-5555-4555-8555-555555555555'
        receipt = {'result': {'nativeSession': {'harness': 'codex', 'sessionId': session_id,
                                                'captured': True, 'storageOwner': 'harness-user-store',
                                                'resumeMode': 'initial'},
                              'nativeCheckpoint': {'version': 1, 'sessionId': session_id,
                                                   'taskId': run['runId'], 'attemptId': 'health-attempt-1'}}}
        not_found = reason('harness.not-found', operation='probe')
        now = board.store.now()
        with board.store.db.write() as connection:
            connection.execute('INSERT INTO attempts(attempt_id,task_id,generation,nonce_verifier,execution_state,'
                               'adapter,shutdown_confirmed,result_json,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)',
                               ('health-attempt-1', run['runId'], 1, 'fixture', 'finished', 'codex', 1,
                                json.dumps(receipt), now, now))
            connection.execute("UPDATE harness_health SET status='missing',record_json=? WHERE harness='codex'",
                               (json.dumps({'reason': not_found}),))
        _, browser = self.open_console(board)
        for operation, params in ((PREVIEW, {'macroTaskId': target}),
                                  (DELETE, {'macroTaskId': target, 'microTaskCount': 1, 'confirm': True})):
            with self.subTest(operation=operation):
                error = self.command(browser, operation, params, status=409, code='MACRO_TASK_HARNESS_UNAVAILABLE')
                self.assertEqual(error['details'], {'macroTaskId': target,
                                                    'harnesses': [{'harness': 'codex', 'reason': not_found,
                                                                   'microTaskIds': [run['runId']]}]})
        with board.store.db.read() as connection:
            self.assertEqual(connection.execute('SELECT COUNT(*) FROM macro_task_deletions').fetchone()[0], 0)
        self.assertEqual(board.call('workflow_get', {'runId': run['runId']})['state'], 'cancelled')
        with board.store.db.write() as connection:
            connection.execute("UPDATE harness_health SET status='ready',record_json=? WHERE harness='codex'", (json.dumps({"reason": None}),))
        # The native session deletion seam is patched: the real one launches a
        # controller process, which belongs to the physical-phase suites.
        with patch.object(macro_task_deletion, '_delete_sessions', return_value=[]):
            deleted = self.command(browser, DELETE, {'macroTaskId': target, 'microTaskCount': 1, 'confirm': True})
        self.assertEqual(deleted['state'], 'deleted')

    def test_interrupted_deletion_returns_structured_reason_and_resumes_the_same_record(self):
        board = self.board()
        target = self.create(board, macroTask={'title': 'Interrupted group'})['macroTaskId']
        _, browser = self.open_console(board)
        params = {'macroTaskId': target, 'microTaskCount': 1, 'confirm': True}
        with patch.object(macro_task_deletion, '_delete_private_roots', side_effect=OSError('Fixture interruption')):
            interrupted = self.command(browser, DELETE, params)
        self.assertEqual((interrupted['state'], interrupted['deletedAt'], interrupted['duplicate']), ('deleting', None, False))
        self.assertEqual(interrupted['reason'], {'code': 'macro-task-deletion.physical-failed', 'params': {'errorCode': 'IO_ERROR'}})
        self.assertNotIn('reasonCode', interrupted)
        self.assertEqual(self.command(browser, PREVIEW, {'macroTaskId': target})['deletionState'], 'deleting')
        completed = self.command(browser, DELETE, params)
        self.assertEqual((completed['state'], completed['deletionId'], completed['duplicate']),
                         ('deleted', interrupted['deletionId'], True))

    def test_skipped_checkout_uses_one_structured_reason_per_item(self):
        board = self.board()
        run = self.create(board, macroTask={'title': 'Retained checkout'})
        with board.store.db.read() as connection:
            manifest = json.loads(connection.execute('SELECT workspace_manifest_json FROM workflow_runs WHERE run_id=?',
                                                     (run['runId'],)).fetchone()[0])
        checkout = Path(manifest['path'])
        (checkout / 'user-change.txt').write_text('Keep this file', encoding="utf-8", newline="\n")
        _, browser = self.open_console(board)
        result = self.command(browser, DELETE, {'macroTaskId': run['macroTaskId'], 'microTaskCount': 1, 'confirm': True})
        self.assertEqual(result['state'], 'deleted')
        self.assertIn({'code': 'macro-task-deletion.unsealed-changes', 'params': {}},
                      [item['reason'] for item in result['skipped']])
        self.assertTrue(checkout.exists())
        for item in result['skipped']:
            self.assertEqual(set(item['reason']), {'code', 'params'})
            self.assertNotIn('reasonCode', item)
            self.assertNotIn('reasons', item)
