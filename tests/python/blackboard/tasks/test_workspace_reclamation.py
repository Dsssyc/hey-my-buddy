"""Terminal reclamation through real Git, board receipts and the service loop."""
import json
from pathlib import Path

from blackboard.tasks.workspace_lifecycle_case import LifecycleTestCase, git
from hey_my_buddy.blackboard.tasks import workspace


class ReclamationTests(LifecycleTestCase):
    def test_continue_prepared_then_cancelled_uses_the_conclusion_seal(self):
        board = self.board()
        submitted, claimed, manifest, checkout, seal, view, artifact = self.run_worktree(
            board, disposition="attention")
        git(checkout, "add", ".")
        git(checkout, "commit", "-qm", "Worker committed the sealed tree")
        self.assertEqual(git(checkout, "rev-parse", "HEAD^{tree}").strip(), seal["tree"])
        board.call("workflow_continue", {
            "runId": view["runId"], "commandId": "manual-continue", "expectedRevision": view["revision"],
            "input": "Continue", "helperPolicy": "keep", **self.control(view),
        })
        board.store.workflow.prepare_continuation_workspace({"runId": view["runId"]})
        board.call("workflow_cancel", {"runId": view["runId"], **self.control(view)})
        concluded = board.call("workflow_conclude", {
            "runId": view["runId"], "note": "Finished before the next attempt", **self.control(view),
        })
        self.assertTrue(concluded["reclaim"]["removed"], concluded)
        self.assertFalse(checkout.exists())
        with board.store.db.read() as connection:
            run = board.store.workflow._run_row(connection, view["runId"])
            current = json.loads(run["workspace_manifest_json"])
            self.assertNotEqual(current["manifestSha256"], manifest["manifestSha256"])
            handoff = board.store.workflow._latest_handoff(connection, run, current)
            self.assertEqual(handoff["kind"], "host-output")
            self.assertEqual(handoff["manifestSha256"], current["manifestSha256"])
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM workflow_turns WHERE run_id=?",
                                                (view["runId"],)).fetchone()[0], 1)

    def test_same_tree_with_a_different_head_can_be_reclaimed(self):
        board = self.board()
        _, _, manifest, checkout, seal, view, artifact = self.run_worktree(board)
        git(checkout, "add", ".")
        git(checkout, "commit", "-qm", "Worker committed the sealed tree")
        self.assertNotEqual(git(checkout, "rev-parse", "HEAD").strip(), manifest["inputCommit"])
        self.assertEqual(git(checkout, "rev-parse", "HEAD^{tree}").strip(), seal["tree"])
        accepted = self.accept(board, view, artifact, target=self.target_with_artifact(artifact))
        self.assertTrue(accepted["reclaim"]["removed"], accepted)
        self.assertFalse(checkout.exists())

    def maintain(self, board):
        from hey_my_buddy.blackboard.service.daemon import Daemon
        daemon = Daemon(board.directory)
        daemon.store.db.close()
        daemon.store = board.store
        daemon._reclaim_storage()

    def test_service_retries_blocked_checkout_without_accumulating_plans_or_events(self):
        from hey_my_buddy.blackboard.tasks.reclamation import workspace_candidates
        board = self.board()
        submitted, _, _, checkout, _, view, artifact = self.run_worktree(board)
        git(checkout, 'switch', '-q', '-c', 'held-branch')
        accepted = self.accept(board, view, artifact, target=self.target_with_artifact(artifact))
        self.assertEqual(accepted['reclaim']['reasons'], ['attached-branch'])
        self.assertIn('switch --detach', accepted['reclaim']['attachedBranch']['detachCommand'])
        self.assertEqual(workspace_candidates(board.store), [submitted['runId']])
        with board.store.db.read() as connection:
            before = dict(connection.execute('SELECT * FROM workspace_cleanup_plans').fetchone())
        head = board.store.head()
        self.maintain(board)
        self.maintain(board)
        self.assertEqual(board.store.head(), head)
        with board.store.db.read() as connection:
            rows = [dict(row) for row in connection.execute('SELECT * FROM workspace_cleanup_plans')]
        self.assertEqual(rows, [before])
        git(checkout, 'switch', '-q', '-c', 'another-held-branch')
        self.maintain(board)
        current = self.view(board, view['runId'])['cleanup']
        self.assertEqual(current['planId'], before['plan_id'])
        self.assertEqual(current['evidence']['workspace']['attachedBranch']['branch'], 'another-held-branch')
        changed_head = board.store.head()
        self.maintain(board)
        self.assertEqual(board.store.head(), changed_head)
        taken = board.call('workflow_takeover', {
            'runId': view['runId'], 'commandId': 'takeover-blocked',
            'expectedOwnerGeneration': view['ownerGeneration'], 'newHostId': 'replacement-host',
            **self.control(view),
        })
        git(checkout, 'switch', '--detach', '-q')
        self.maintain(board)
        self.assertFalse(checkout.exists())
        self.assertEqual(workspace_candidates(board.store), [])
        with board.store.db.read() as connection:
            rows = list(connection.execute('SELECT plan_id,state FROM workspace_cleanup_plans'))
        self.assertEqual([tuple(row) for row in rows], [(before['plan_id'], 'applied')])
        self.assertEqual(git(self.repo, 'rev-parse', 'refs/heads/held-branch').strip(),
                         accepted['reclaim']['attachedBranch']['headCommit'])

    def test_native_roots_wait_three_days_and_require_confirmed_stop(self):
        from support import FakeClock
        from hey_my_buddy import private_dirs
        from hey_my_buddy.blackboard.tasks.reclamation import reclaim_native
        clock = FakeClock()
        board = self.board(clock=clock)
        submitted, claim, _, _, _, view, artifact = self.run_worktree(board)
        run_id = submitted['runId']
        roots = []
        for harness in private_dirs.ADAPTERS:
            root = private_dirs.ensure_private_dir(private_dirs.micro_task_root(board.directory, harness, run_id))
            (root / 'failed-attempt.log').write_text('failed evidence', encoding='utf-8')
            roots.append(root)
        self.accept(board, view, artifact, target=self.target_with_artifact(artifact))
        reclaim_native(board.store)
        self.assertTrue(all(root.exists() for root in roots))
        clock.advance(3 * 86400 - 1)
        reclaim_native(board.store)
        self.assertTrue(all(root.exists() for root in roots))
        clock.advance(1)
        with board.store.db.write() as connection:
            connection.execute('UPDATE attempts SET shutdown_confirmed=0 WHERE task_id=?', (run_id,))
        reclaim_native(board.store)
        self.assertTrue(all(root.exists() for root in roots))
        with board.store.db.write() as connection:
            connection.execute('UPDATE attempts SET shutdown_confirmed=1 WHERE task_id=?', (run_id,))
        reclaim_native(board.store)
        self.assertTrue(all(not root.exists() for root in roots))

    def test_abandoned_shell_and_unowned_allocation_are_reclaimed_under_their_locks(self):
        from hey_my_buddy.blackboard.tasks.reclamation import reclaim_allocation
        from unittest.mock import patch
        board = self.board()
        intent = {'kind': 'worktree', 'cwd': str(self.repo), 'access': 'write', 'writeScope': ['.'],
                  'base': {'kind': 'working-tree'}, 'integrator': {'role': 'host', 'id': 'fixture-host'}}
        shell_id, shell = workspace._workspace_directory(board.directory, 'abandoned-shell')
        (shell / '.lock').touch()
        (shell / 'request.json').write_bytes(workspace._json({'requestId': 'abandoned-shell', 'version': 3, 'intent': intent}))
        with workspace._lock(shell, create=False):
            with self.assertRaises(Exception) as caught:
                reclaim_allocation(board.store, shell)
            self.assertEqual(caught.exception.code, 'WORKSPACE_BUSY')
        reclaim_allocation(board.store, shell)
        self.assertFalse(shell.exists())
        manifest = workspace.prepare(board.directory, 'abandoned-allocation', intent)
        directory = board.directory / 'workspaces' / manifest['workspaceId']
        checkout = Path(manifest['checkoutRoot'])
        (checkout / 'unsealed.txt').write_text('abandoned preparation', encoding='utf-8')
        git(checkout, 'add', '.')
        git(checkout, 'commit', '-qm', 'Preparation committed before admission')
        reclaim_allocation(board.store, directory)
        self.assertFalse(directory.exists())
        self.assertFalse(git(self.repo, 'for-each-ref', '--format=%(refname)',
                             'refs/hey-my-buddy/workspaces/' + manifest['workspaceId'] + '/').strip())
        submitted, _, manifest, checkout, _, view, _ = self.run_worktree(board, request_id='live-allocation')
        directory = board.directory / 'workspaces' / manifest['workspaceId']
        reclaim_allocation(board.store, directory)
        self.assertTrue(checkout.exists())
        self.assertTrue(directory.exists())

    def test_failed_or_cancelled_borrower_uses_its_concluded_parents_retention_time(self):
        from support import FakeClock
        from hey_my_buddy import private_dirs
        from hey_my_buddy.blackboard.tasks.reclamation import reclaim_native
        from blackboard.tasks.test_workflow_real import BUDDY
        for terminal in ('failed', 'cancelled'):
            with self.subTest(terminal=terminal):
                clock = FakeClock()
                board = self.board(clock=clock)
                submitted, _, _, checkout, _, yielded, _ = self.run_worktree(
                    board, request_id='parent-' + terminal, disposition='assistance')
                decided = board.call('workflow_decide', {
                    'runId': submitted['runId'], 'requestId': yielded['activeRequest']['requestId'],
                    'commandId': 'borrow-' + terminal, 'expectedRevision': yielded['revision'], 'decision': 'approve',
                    'helpers': [{**BUDDY, 'requestId': 'borrower-' + terminal, 'task': 'inspect shared files',
                                 'cwd': str(checkout), 'executionWorkspace': {
                                     'kind': 'existing', 'access': 'write', 'writeScope': ['.']}}],
                    **self.control(yielded),
                })
                child_id = decided['children'][0]['taskId']
                if terminal == 'failed':
                    child = self.claim(board, child_id, request_id='borrower-failed-claim')['claim']
                    attempt = child['attempt']
                    seal = workspace.seal(board.directory, child['turn']['input']['executionWorkspace'], child_id, attempt['attemptId'])
                    board.call('execution_result', {
                        'attemptId': attempt['attemptId'], 'generation': attempt['generation'], 'nonce': 'n' * 16,
                        'status': 'failed', 'terminationReason': 'harness-error', 'shutdownConfirmed': True, 'exitCode': 1,
                        'result': {'status': 'invalid-result', 'workspaceSeal': seal},
                    })
                root = private_dirs.ensure_private_dir(private_dirs.micro_task_root(board.directory, 'dsh', child_id))
                (root / 'failed-evidence.log').write_text('retained', encoding='utf-8')
                board.call('workflow_cancel', {'runId': submitted['runId'], **self.control(yielded)})
                board.call('workflow_conclude', {'runId': submitted['runId'], 'note': 'Ended the parent', **self.control(yielded)})
                reclaim_native(board.store)
                self.assertTrue(root.exists())
                clock.advance(3 * 86400)
                reclaim_native(board.store)
                self.assertFalse(root.exists())
                with board.store.db.read() as connection:
                    row = connection.execute('SELECT state FROM workflow_runs WHERE run_id=?', (child_id,)).fetchone()
                    self.assertIn(row['state'], ('failed', 'cancelled'))
                    self.assertIsNone(connection.execute('SELECT 1 FROM workflow_host_conclusions WHERE run_id=?', (child_id,)).fetchone())

    def test_shell_with_a_live_request_is_retained_until_its_task_has_gone(self):
        from hey_my_buddy.blackboard.tasks.reclamation import reclaim_allocation
        board = self.board()
        task = board.submit_execution(requestId='live-shell', adapter='command', task='private fixture',
                                      argv=['unused-fixture-command'], cwd=str(self.repo), expectedSeconds=60)
        _, directory = workspace._workspace_directory(board.directory, 'live-shell')
        (directory / '.lock').touch()
        (directory / 'request.json').write_bytes(workspace._json({'requestId': 'live-shell'}))
        reclaim_allocation(board.store, directory)
        self.assertTrue(directory.exists())
        with board.store.db.write() as connection:
            connection.execute('DELETE FROM events WHERE task_id=?', (task['task']['runId'],))
            connection.execute('DELETE FROM tasks WHERE task_id=?', (task['task']['runId'],))
        reclaim_allocation(board.store, directory)
        self.assertFalse(directory.exists())

    def test_storage_keeps_live_checkout_inventory_and_excludes_terminal_candidates(self):
        from unittest.mock import patch
        board = self.board()
        submitted, _, _, checkout, _, view, artifact = self.run_worktree(board)
        with patch('hey_my_buddy.blackboard.tasks.storage.process_inventory', return_value=([], [], True)):
            planned = board.call('storage_plan', {})
        live = next(row for row in planned['candidates'] if row['path'] == str(checkout))
        self.assertIn('not-accepted', live['reasons'])
        self.assertFalse(live['eligible'])
        git(checkout, 'switch', '-q', '-c', 'retained-terminal')
        self.accept(board, view, artifact, target=self.target_with_artifact(artifact))
        with patch('hey_my_buddy.blackboard.tasks.storage.process_inventory', return_value=([], [], True)):
            terminal = board.call('storage_plan', {})
        self.assertFalse(any(row['runId'] == submitted['runId'] for row in terminal['candidates'] if row.get('runId')))
        self.assertTrue(checkout.exists())

    def test_macro_deletion_removes_worker_commits_and_retains_unproven_ownership_for_inventory(self):
        from unittest.mock import patch
        for proven in (True, False):
            with self.subTest(proven=proven):
                board = self.board()
                submitted, _, manifest, checkout, _, view, _ = self.run_worktree(
                    board, request_id='macro-commit-' + str(proven), disposition='attention')
                git(checkout, 'add', '.')
                git(checkout, 'commit', '-qm', 'Worker committed task contents')
                board.call('workflow_cancel', {'runId': submitted['runId'], **self.control(view)})
                if not proven:
                    git(checkout, 'worktree', 'unlock', str(checkout))
                    git(checkout, 'worktree', 'lock', '--reason', 'foreign-allocation', str(checkout))
                target = 'run:' + submitted['runId']
                preview = board.console_call('macro_task_deletion_preview', {'macroTaskId': target})
                deleted = board.console_call('macro_task_delete', {
                    'macroTaskId': target, 'microTaskCount': preview['microTaskCount'], 'confirm': True,
                })
                self.assertEqual(deleted['state'], 'deleted')
                with board.store.db.read() as connection:
                    self.assertIsNone(connection.execute('SELECT 1 FROM workflow_runs WHERE run_id=?',
                                                         (submitted['runId'],)).fetchone())
                directory = board.directory / 'workspaces' / manifest['workspaceId']
                refs = git(self.repo, 'for-each-ref', '--format=%(refname)',
                           'refs/hey-my-buddy/workspaces/' + manifest['workspaceId'] + '/').strip()
                if proven:
                    self.assertFalse(directory.exists())
                    self.assertFalse(refs)
                else:
                    self.assertTrue(directory.exists())
                    self.assertTrue(refs)
                    self.assertIn('macro-task-deletion.lock-mismatch', [item['reason']['code'] for item in deleted['skipped']])
                    with patch('hey_my_buddy.blackboard.tasks.storage.process_inventory', return_value=([], [], True)):
                        inventory = board.call('storage_plan', {})
                    orphan = next(row for row in inventory['candidates'] if row['path'] == str(checkout))
                    self.assertTrue(orphan['orphan'])
                    self.assertFalse(orphan['eligible'])
