"""Terminal reclamation through real Git, board receipts and the service loop."""
import json
from pathlib import Path

from blackboard.tasks.workspace_lifecycle_case import LifecycleTestCase, git
from hey_my_buddy.blackboard.tasks import workspace


class ReclamationTests(LifecycleTestCase):
    def deferred_checkout(self):
        from unittest.mock import patch
        board = self.board()
        _, _, manifest, checkout, _, view, artifact = self.run_worktree(board)
        with patch.object(board.store.workflow, '_reclaim_checkout', return_value={
                'removed': False, 'reasons': ['fixture-deferred']}):
            self.accept(board, view, artifact, not_required=True)
        return board, manifest, checkout, view

    def test_partial_physical_deletion_retains_git_identity_and_reports_each_blocked_retry(self):
        import errno
        from unittest.mock import patch
        from hey_my_buddy import private_dirs
        from hey_my_buddy.blackboard.tasks.reclamation import Reclamation
        from hey_my_buddy.paths import native_path
        board, manifest, checkout, view = self.deferred_checkout()
        original = private_dirs.remove_tree

        def occupied(path, **kwargs):
            if Path(path) == checkout:
                native_path(checkout / '.git').unlink()
                native_path(checkout / 'src/feature.py').unlink()
                native_path(checkout / 'src').rmdir()
                raise PermissionError(errno.EACCES, 'fixture occupied file', str(checkout / 'tracked.txt'))
            return original(path, **kwargs)

        maintenance = Reclamation(board.store)
        with patch.object(private_dirs, 'remove_tree', side_effect=occupied):
            maintenance.run()
        self.assertEqual(self.view(board, view['runId'])['cleanup']['state'], 'applying')
        self.assertEqual(workspace._worktree_record(self.repo, checkout)['locked'],
                         'hey-my-buddy:' + manifest['workspaceId'])
        self.assertEqual({path.name for path in checkout.iterdir()}, {'tracked.txt'})
        self.assertEqual(board.store.service_issues()[0]['stage'], 'workspace-cleanup')
        git(self.repo, 'worktree', 'unlock', str(checkout))
        git(self.repo, 'worktree', 'lock', '--reason', 'foreign-owner', str(checkout))
        maintenance.run()
        issues = board.store.service_issues()
        self.assertEqual(issues[0]['stage'], 'workspace-cleanup')
        self.assertIn('lock-mismatch', issues[0]['message'])
        self.assertIn(view['runId'], issues[0]['message'])
        head = board.store.head()
        maintenance.run()
        self.assertEqual(board.store.head(), head)
        git(self.repo, 'worktree', 'unlock', str(checkout))
        git(self.repo, 'worktree', 'lock', '--reason', 'hey-my-buddy:' + manifest['workspaceId'], str(checkout))
        maintenance.run()
        self.assertFalse(checkout.exists())
        self.assertIsNone(workspace._worktree_record(self.repo, checkout))
        self.assertEqual(self.view(board, view['runId'])['cleanup']['state'], 'applied')
        self.assertEqual(board.store.service_issues(), [])

    def test_removed_checkout_keeps_applying_until_its_git_registration_is_removed(self):
        from unittest.mock import patch
        from hey_my_buddy.blackboard.tasks.reclamation import Reclamation
        from hey_my_buddy.errors import BoardError
        board, manifest, checkout, view = self.deferred_checkout()
        original = workspace._git

        def fail_registration(path, *args, **kwargs):
            if args[:2] == ('worktree', 'remove'):
                raise BoardError('WORKSPACE_GIT_ERROR', 'fixture registration deletion refused')
            return original(path, *args, **kwargs)

        maintenance = Reclamation(board.store)
        with patch.object(workspace, '_git', side_effect=fail_registration):
            maintenance.run()
        self.assertFalse(checkout.exists())
        self.assertEqual(workspace._worktree_record(self.repo, checkout)['locked'],
                         'hey-my-buddy:' + manifest['workspaceId'])
        self.assertEqual(self.view(board, view['runId'])['cleanup']['state'], 'applying')
        self.assertEqual(board.store.service_issues()[0]['stage'], 'workspace-cleanup')
        maintenance.run()
        self.assertIsNone(workspace._worktree_record(self.repo, checkout))
        current = self.view(board, view['runId'])['cleanup']
        self.assertEqual(current['state'], 'applied')
        self.assertTrue(current['result']['alreadyRemoved'])
        self.assertEqual(board.store.service_issues(), [])

    def test_reclamation_preserves_a_readonly_cache_object_outside_the_checkout(self):
        import os
        import stat
        from hey_my_buddy.blackboard.tasks.reclamation import Reclamation
        from hey_my_buddy.paths import native_path
        (self.repo / '.gitignore').write_text('.cache/\n', encoding='utf-8', newline='\n')
        git(self.repo, 'add', '.gitignore')
        git(self.repo, 'commit', '-qm', 'Ignore local cache')
        board, _, checkout, view = self.deferred_checkout()
        outside = self.directory / 'cache-object'
        outside.write_bytes(b'immutable cached payload')
        cached = checkout / '.cache/object'
        native_path(cached.parent).mkdir()
        os.link(outside, native_path(cached))
        outside.chmod(stat.S_IREAD)
        before = outside.stat()
        try:
            Reclamation(board.store).run()
            self.assertFalse(checkout.exists(), board.store.service_issues())
            after = outside.stat()
            self.assertEqual(after.st_mode, before.st_mode)
            self.assertEqual(getattr(after, 'st_file_attributes', None), getattr(before, 'st_file_attributes', None))
            self.assertEqual(after.st_ino, before.st_ino)
            self.assertEqual(after.st_nlink, 1)
            self.assertEqual(outside.read_bytes(), b'immutable cached payload')
        finally:
            outside.chmod(stat.S_IREAD | stat.S_IWRITE)

    def test_macro_deletion_retries_git_registration_after_checkout_removal(self):
        from unittest.mock import patch
        from hey_my_buddy.errors import BoardError
        board = self.board()
        submitted, _, manifest, checkout, _, view, _ = self.run_worktree(board, disposition='attention')
        board.call('workflow_cancel', {'runId': view['runId'], **self.control(view)})
        target = 'run:' + submitted['runId']
        params = {'macroTaskId': target, 'microTaskCount': 1, 'confirm': True}
        original = workspace._git

        def fail_registration(path, *args, **kwargs):
            if args[:2] == ('worktree', 'remove'):
                raise BoardError('WORKSPACE_GIT_ERROR', 'fixture registration deletion refused')
            return original(path, *args, **kwargs)

        with patch.object(workspace, '_git', side_effect=fail_registration):
            blocked = board.console_call('macro_task_delete', params)
        self.assertEqual(blocked['state'], 'deleting')
        self.assertFalse(checkout.exists())
        self.assertIsNotNone(workspace._worktree_record(self.repo, checkout))
        completed = board.console_call('macro_task_delete', params)
        self.assertEqual((completed['state'], completed['deletionId']), ('deleted', blocked['deletionId']))
        self.assertIsNone(workspace._worktree_record(self.repo, checkout))
        self.assertFalse((board.directory / 'workspaces' / manifest['workspaceId']).exists())

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
        from unittest.mock import patch
        from hey_my_buddy.blackboard.tasks.reclamation import Reclamation, workspace_candidates
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
        maintenance = Reclamation(board.store)
        with patch.object(workspace, 'cleanup_inspect', wraps=workspace.cleanup_inspect) as inspect:
            maintenance.run()
            maintenance.run()
            self.assertEqual(inspect.call_count, 2)
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

    def test_terminal_observation_failure_is_reported_and_retried(self):
        from unittest.mock import patch
        from hey_my_buddy.errors import BoardError
        from hey_my_buddy.blackboard.tasks import reclamation
        board = self.board()
        _, _, _, checkout, _, view, artifact = self.run_worktree(board)
        git(checkout, 'switch', '-q', '-c', 'held-for-retry')
        self.accept(board, view, artifact, target=self.target_with_artifact(artifact))
        maintenance = reclamation.Reclamation(board.store)
        with patch.object(workspace, 'cleanup_inspect', side_effect=BoardError(
                'WORKSPACE_GIT_UNKNOWN', 'Temporary Git observation failure')):
            maintenance.run()
        self.assertEqual([(issue['stage'], issue['code']) for issue in board.store.service_issues()],
                         [('workspace-cleanup', 'WORKSPACE_GIT_UNKNOWN')])
        with patch.object(workspace, 'cleanup_inspect', wraps=workspace.cleanup_inspect) as inspect:
            maintenance.run()
            self.assertEqual(inspect.call_count, 1)
        self.assertEqual(board.store.service_issues(), [])

    def test_terminal_maintenance_does_not_take_storage_lock(self):
        from unittest.mock import patch
        from hey_my_buddy.blackboard.tasks import reclamation, storage
        board = self.board()
        _, _, _, checkout, _, view, artifact = self.run_worktree(board)
        git(checkout, 'switch', '-q', '-c', 'held-without-storage-lock')
        self.accept(board, view, artifact, target=self.target_with_artifact(artifact))
        real = board.store.workflow._reclaim_checkout
        unlocked = []
        def reclaim_without_storage_lock(*, run_id):
            with storage.locked(board.store):
                unlocked.append(run_id)
            return real(run_id=run_id)
        with patch.object(board.store.workflow, '_reclaim_checkout', side_effect=reclaim_without_storage_lock):
            reclamation.Reclamation(board.store).run()
        self.assertEqual(unlocked, [view['runId']])
        self.assertTrue(checkout.exists())
        self.assertEqual(board.store.service_issues(), [])

    def test_unowned_allocation_ignores_only_unreadable_processes_older_than_creation(self):
        import sys
        from unittest.mock import Mock, patch
        import psutil
        from hey_my_buddy.blackboard.tasks import checkout_processes, reclamation
        if sys.platform == 'win32':
            self.skipTest('POSIX process cwd observation')
        intent = {'kind': 'worktree', 'cwd': str(self.repo), 'access': 'write', 'writeScope': ['.'],
                  'base': {'kind': 'working-tree'}, 'integrator': {'role': 'host', 'id': 'fixture-host'}}
        for age, removed in ((-60, True), (60, False)):
            with self.subTest(age=age):
                board = self.board()
                manifest = workspace.prepare(board.directory, f'unowned-process-{age}', intent)
                checkout = Path(manifest['checkoutRoot'])
                created_at = (checkout.parent / 'request.json').stat().st_mtime
                process = Mock(pid=34567)
                process.cwd.side_effect = psutil.AccessDenied(pid=process.pid)
                process.create_time.return_value = created_at + age
                process.name.return_value = '(sd-pam)'
                with patch.object(checkout_processes, '_processes', return_value=([process], True)), \
                     patch.object(checkout_processes, 'process_start', return_value=created_at + age):
                    reclamation.Reclamation(board.store).run()
                self.assertEqual(checkout.exists(), not removed)
                self.assertEqual([(issue['stage'], issue['code']) for issue in board.store.service_issues()],
                                 [] if removed else [('allocation-reclamation', 'NOT_READY')])

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
        from hey_my_buddy.blackboard.tasks.reclamation import Reclamation
        board = self.board()
        intent = {'kind': 'worktree', 'cwd': str(self.repo), 'access': 'write', 'writeScope': ['.'],
                  'base': {'kind': 'working-tree'}, 'integrator': {'role': 'host', 'id': 'fixture-host'}}
        shell_id, shell = workspace._workspace_directory(board.directory, 'abandoned-shell')
        (shell / '.lock').touch()
        (shell / 'request.json').write_bytes(workspace._json({'requestId': 'abandoned-shell', 'version': 4, 'intent': intent}))
        with workspace._lock(shell, create=False):
            Reclamation(board.store).run()
            self.assertTrue(shell.exists())
            self.assertEqual(board.store.service_issues(), [])
        Reclamation(board.store).run()
        self.assertFalse(shell.exists())
        manifest = workspace.prepare(board.directory, 'abandoned-allocation', intent)
        directory = board.directory / 'workspaces' / manifest['workspaceId']
        checkout = Path(manifest['checkoutRoot'])
        with workspace._lock(directory, create=False):
            Reclamation(board.store).run()
            self.assertTrue(checkout.exists())
        Reclamation(board.store).run()
        self.assertFalse(directory.exists())
        self.assertFalse(git(self.repo, 'for-each-ref', '--format=%(refname)',
                             'refs/hey-my-buddy/workspaces/' + manifest['workspaceId'] + '/').strip())
        submitted, _, manifest, checkout, _, view, _ = self.run_worktree(board, request_id='live-allocation')
        directory = board.directory / 'workspaces' / manifest['workspaceId']
        Reclamation(board.store).run()
        self.assertTrue(checkout.exists())
        self.assertTrue(directory.exists())

    def test_a_missing_checkout_uses_the_same_revoke_plan_for_its_records_and_refs(self):
        from hey_my_buddy.blackboard.tasks.reclamation import Reclamation
        board = self.board()
        manifest = workspace.prepare(board.directory, 'already-removed-checkout', {
            'kind': 'worktree', 'cwd': str(self.repo), 'access': 'write', 'writeScope': ['.'],
            'base': {'kind': 'working-tree'}, 'integrator': {'role': 'host', 'id': 'fixture-host'}})
        directory = Path(manifest['checkoutRoot']).parent
        workspace.cleanup_remove(board.directory, manifest)
        self.assertFalse(Path(manifest['checkoutRoot']).exists())
        self.assertTrue((directory / 'manifest.json').exists())
        Reclamation(board.store).run()
        self.assertFalse(directory.exists())
        self.assertFalse(git(self.repo, 'for-each-ref', '--format=%(refname)',
                             'refs/hey-my-buddy/workspaces/' + manifest['workspaceId']))
        self.assertEqual(board.store.service_issues(), [])

    def test_a_foreign_manifest_cannot_redirect_an_unregistered_revocation(self):
        from hey_my_buddy.blackboard.tasks.reclamation import Reclamation
        board = self.board()
        intent = {'kind': 'worktree', 'cwd': str(self.repo), 'access': 'write', 'writeScope': ['.'],
                  'base': {'kind': 'working-tree'}, 'integrator': {'role': 'host', 'id': 'fixture-host'}}
        first = workspace.prepare(board.directory, 'misplaced-manifest', intent)
        second = workspace.prepare(board.directory, 'other-allocation', intent)
        directory = Path(first['checkoutRoot']).parent
        other = Path(second['checkoutRoot']).parent
        (directory / 'manifest.json').write_bytes(workspace._json(second))
        with workspace._lock(other, create=False):
            Reclamation(board.store).run()
            self.assertTrue(Path(first['checkoutRoot']).exists())
            self.assertTrue(Path(second['checkoutRoot']).exists())
        self.assertEqual(board.store.service_issues(), [])

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
        from hey_my_buddy.blackboard.tasks.reclamation import Reclamation
        board = self.board()
        task = board.submit_execution(requestId='live-shell', adapter='command', task='private fixture',
                                      argv=['unused-fixture-command'], cwd=str(self.repo), expectedSeconds=60)
        _, directory = workspace._workspace_directory(board.directory, 'live-shell')
        (directory / '.lock').touch()
        (directory / 'request.json').write_bytes(workspace._json({'requestId': 'live-shell'}))
        Reclamation(board.store).run()
        self.assertTrue(directory.exists())
        with board.store.db.write() as connection:
            connection.execute('DELETE FROM events WHERE task_id=?', (task['task']['runId'],))
            connection.execute('DELETE FROM tasks WHERE task_id=?', (task['task']['runId'],))
        Reclamation(board.store).run()
        self.assertFalse(directory.exists())

    def test_maintenance_never_locks_a_running_tasks_allocation(self):
        from unittest.mock import patch
        from hey_my_buddy.blackboard.tasks.reclamation import Reclamation
        from blackboard.tasks.workspace_lifecycle_case import BUDDY
        board = self.board()
        submitted = board.call('workflow_submit', {
            **BUDDY, 'requestId': 'running-no-maintenance-lock', 'hostId': 'host-1',
            'task': 'Keep sealing available', 'cwd': str(self.repo), 'submissionToken': 't' * 32,
            'executionWorkspace': {'kind': 'worktree', 'access': 'write', 'writeScope': ['.']},
        })
        claimed = self.claim(board, submitted['runId'], request_id='running-maintenance-claim')
        manifest = claimed['claim']['turn']['input']['executionWorkspace']
        directory = Path(manifest['checkoutRoot']).parent
        maintenance = Reclamation(board.store)
        original_lock = workspace._lock
        def no_running_lock(path, **kwargs):
            self.assertNotEqual(path, directory, 'Maintenance must not lock a running task allocation')
            return original_lock(path, **kwargs)
        with patch.object(workspace, '_lock', side_effect=no_running_lock):
            maintenance.run()
            maintenance.changed([submitted['runId']])
            maintenance.run()
        self.assertEqual(board.store.service_issues(), [])
        seal = workspace.seal(board.directory, manifest, submitted['runId'], claimed['claim']['attempt']['attemptId'])
        self.finish_turn(board, claimed, seal)
        self.assertEqual(self.view(board, submitted['runId'])['state'], 'delivered')

    def test_hundreds_of_shells_can_be_inspected_and_reclaimed_with_256_descriptors(self):
        import os
        from unittest.mock import patch
        if os.name == 'nt':
            self.skipTest('POSIX descriptor limit')
        import resource
        from hey_my_buddy.blackboard.tasks import reclamation, storage
        board = self.board()
        directories = []
        for index in range(600):
            request_id = 'low-descriptor-shell-' + str(index)
            _, directory = workspace._workspace_directory(board.directory, request_id)
            (directory / '.lock').touch()
            (directory / 'request.json').write_bytes(workspace._json({'requestId': request_id}))
            directories.append(directory)
        previous = resource.getrlimit(resource.RLIMIT_NOFILE)
        try:
            resource.setrlimit(resource.RLIMIT_NOFILE, (256, previous[1]))
            with patch.object(storage, 'process_inventory', return_value=([], [], True)), \
                 patch.object(storage, '_workspace_reference_rows', side_effect=AssertionError('Shells need no reference scan')):
                inventory = board.call('storage_plan', {})
                self.assertFalse(any(row['category'] == 'workspaces' for row in inventory['candidates']))
                reclamation.Reclamation(board.store).run()
                self.assertFalse(any(directory.exists() for directory in directories))
                self.assertEqual(board.store.service_issues(), [])
        finally:
            resource.setrlimit(resource.RLIMIT_NOFILE, previous)

    def test_transient_allocation_lock_and_record_failures_retry_without_an_entry_change(self):
        import errno
        from unittest.mock import patch
        from hey_my_buddy.blackboard.tasks.reclamation import Reclamation
        for operation in ('_lock', '_record'):
            with self.subTest(operation=operation):
                board = self.board()
                request_id = 'transient-' + operation
                _, directory = workspace._workspace_directory(board.directory, request_id)
                (directory / '.lock').touch()
                (directory / 'request.json').write_bytes(workspace._json({'requestId': request_id}))
                maintenance = Reclamation(board.store)
                before = maintenance._allocation_entry(directory)
                with patch.object(workspace, operation, side_effect=OSError(errno.EMFILE, 'temporary descriptor exhaustion')):
                    maintenance.run()
                self.assertTrue(directory.exists())
                self.assertEqual(maintenance._allocation_entry(directory), before)
                self.assertEqual([(issue['stage'], issue['code']) for issue in board.store.service_issues()],
                                 [('allocation-reclamation', 'OSError')])
                maintenance.run()
                self.assertFalse(directory.exists())
                self.assertEqual(board.store.service_issues(), [])

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
        self.assertFalse(any(row['runId'] == submitted['runId'] for row in terminal['candidates'] if row.get('runId') and row['category'] == 'workspaces'))
        self.assertTrue(checkout.exists())

    def test_macro_deletion_removes_worker_commits_only_after_ownership_is_proven(self):
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
                self.assertEqual(deleted['state'], 'deleted' if proven else 'deleting')
                with board.store.db.read() as connection:
                    retained = connection.execute('SELECT 1 FROM workflow_runs WHERE run_id=?',
                                                  (submitted['runId'],)).fetchone()
                    self.assertEqual(retained is not None, not proven)
                directory = board.directory / 'workspaces' / manifest['workspaceId']
                refs = git(self.repo, 'for-each-ref', '--format=%(refname)',
                           'refs/hey-my-buddy/workspaces/' + manifest['workspaceId'] + '/').strip()
                if proven:
                    self.assertFalse(directory.exists())
                    self.assertFalse(refs)
                else:
                    self.assertTrue(directory.exists())
                    self.assertTrue(refs)
                    self.assertEqual(deleted['failures'][0]['reason']['code'], 'macro-task-deletion.lock-mismatch')
                    git(checkout, 'worktree', 'unlock', str(checkout))
                    git(checkout, 'worktree', 'lock', '--reason', 'hey-my-buddy:' + manifest['workspaceId'], str(checkout))
                    retried = board.console_call('macro_task_delete', {
                        'macroTaskId': target, 'microTaskCount': preview['microTaskCount'], 'confirm': True,
                    })
                    self.assertEqual((retried['state'], retried['deletionId']), ('deleted', deleted['deletionId']))
                    self.assertFalse(directory.exists())

    def test_unregistered_allocation_retained_by_an_independent_integration_is_not_deleted(self):
        from hey_my_buddy.blackboard.tasks.reclamation import Reclamation
        from unittest.mock import patch
        board = self.board()
        _, _, _, _, _, view, artifact = self.run_worktree(board, request_id='integration-owner')
        intent = {'kind': 'worktree', 'cwd': str(self.repo), 'access': 'write', 'writeScope': ['.'],
                  'base': {'kind': 'working-tree'}, 'integrator': {'role': 'host', 'id': 'fixture-host'}}
        manifest = workspace.prepare(board.directory, 'unregistered-integration-target', intent)
        checkout = Path(manifest['checkoutRoot'])
        before = git(checkout, 'rev-parse', 'HEAD').strip()
        git(checkout, 'apply', '--index', artifact['diffPath'])
        git(checkout, 'commit', '-qm', 'Independent detached integration')
        integration_head = git(checkout, 'rev-parse', 'HEAD').strip()
        accepted = self.accept(board, view, artifact, target={'path': checkout, 'ref': 'HEAD', 'before': before})
        self.assertEqual(accepted['state'], 'accepted')
        directory = board.directory / 'workspaces' / manifest['workspaceId']
        refs = git(self.repo, 'for-each-ref', '--format=%(refname) %(objectname)',
                   'refs/hey-my-buddy/workspaces/' + manifest['workspaceId'] + '/')
        with patch.object(workspace, 'cleanup_remove', side_effect=AssertionError('Referenced target deleted')):
            Reclamation(board.store).run()
        self.assertTrue(checkout.exists())
        self.assertEqual(git(checkout, 'rev-parse', 'HEAD').strip(), integration_head)
        self.assertEqual(git(self.repo, 'for-each-ref', '--format=%(refname) %(objectname)',
                             'refs/hey-my-buddy/workspaces/' + manifest['workspaceId'] + '/'), refs)

    def test_unproven_allocation_is_retained_without_a_service_issue(self):
        from hey_my_buddy.blackboard.tasks.reclamation import Reclamation
        board = self.board()
        directories = []
        for request_id, request in (('unproven-request', {'requestId': 'someone-else'}),
                                    ('nonobject-request', []), ('linked-lock', {'requestId': 'linked-lock'})):
            _, directory = workspace._workspace_directory(board.directory, request_id)
            if request_id == 'linked-lock':
                (directory / 'ordinary-file').touch()
                (directory / '.lock').symlink_to('ordinary-file')
            else:
                (directory / '.lock').touch()
            (directory / 'request.json').write_bytes(workspace._json(request))
            directories.append(directory)
        Reclamation(board.store).run()
        self.assertTrue(all(directory.exists() for directory in directories))
        self.assertEqual(board.store.service_issues(), [])

    def test_warm_maintenance_does_not_revisit_reclaimed_history(self):
        from hey_my_buddy.blackboard.tasks import reclamation
        from unittest.mock import patch
        board = self.board()
        submitted, _, _, _, _, view, artifact = self.run_worktree(board)
        self.accept(board, view, artifact, target=self.target_with_artifact(artifact))
        with board.store.db.write() as connection:
            connection.execute("UPDATE tasks SET accepted_at='2000-01-01T00:00:00Z' WHERE task_id=?", (submitted['runId'],))
        maintenance = reclamation.Reclamation(board.store)
        board.store.on_change = maintenance.changed
        maintenance.run()
        with patch.object(board.store.workflow, '_allocation_provenance', side_effect=AssertionError('Historical allocation parsed')), \
             patch.object(board.store.workflow, 'shutdown_summary', side_effect=AssertionError('Historical stop recomputed')), \
             patch.object(reclamation, 'native_task_ids', side_effect=AssertionError('Historical task ids scanned')), \
             patch.object(workspace, '_record', side_effect=AssertionError('Historical allocation directory read')):
            maintenance.run()
            maintenance.run()
        self.assertEqual(board.store.service_issues(), [])

    def test_maintenance_releases_storage_lock_between_objects_and_stops_after_current_object(self):
        import threading
        from hey_my_buddy.blackboard.tasks import reclamation, storage
        from unittest.mock import patch
        board = self.board()
        for request_id in ('shell-one', 'shell-two'):
            _, directory = workspace._workspace_directory(board.directory, request_id)
            (directory / '.lock').touch()
            (directory / 'request.json').write_bytes(workspace._json({'requestId': request_id}))
        real = workspace.finish_submission_revoke
        unlocked = []
        def remove(directory):
            with storage.locked(board.store), workspace._lock(directory, create=False):
                unlocked.append(directory.name)
            return real(directory)
        with patch.object(workspace, 'finish_submission_revoke', side_effect=remove):
            reclamation.Reclamation(board.store).run()
        self.assertEqual(len(unlocked), 2)
        stopped = threading.Event()
        def finish_one(**kwargs):
            stopped.set()
            return {'removed': True}
        with patch.object(reclamation, 'workspace_candidates', return_value=['one', 'two']), \
             patch.object(board.store.workflow, '_reclaim_checkout', side_effect=finish_one) as cleanup, \
             patch.object(reclamation, 'reclaim_native', side_effect=AssertionError('Started next object')):
            reclamation.Reclamation(board.store).run(stopping=stopped.is_set)
        self.assertEqual(cleanup.call_count, 1)

    def test_incomplete_task_deletion_retains_cached_ownership_until_retry_finishes(self):
        from hey_my_buddy.blackboard.tasks import reclamation
        board = self.board()
        submitted = self.submit(board, request_id='cached-owner', kind='worktree')
        self.controls[submitted['runId']] = submitted['control']
        with board.store.db.read() as connection:
            manifest = json.loads(board.store.workflow._run_row(connection, submitted['runId'])['workspace_manifest_json'])
        maintenance = reclamation.Reclamation(board.store)
        board.store.on_change = maintenance.changed
        maintenance.run()
        board.call('workflow_cancel', {'runId': submitted['runId'], **self.control(submitted)})
        directory = board.directory / 'workspaces' / manifest['workspaceId']
        original = (directory / 'request.json').read_bytes()
        (directory / 'request.json').write_bytes(workspace._json({'requestId': 'unknown-owner'}))
        target = 'run:' + submitted['runId']
        preview = board.console_call('macro_task_deletion_preview', {'macroTaskId': target})
        deleted = board.console_call('macro_task_delete', {'macroTaskId': target, 'microTaskCount': preview['microTaskCount'], 'confirm': True})
        self.assertEqual(deleted['state'], 'deleting')
        self.assertTrue(directory.exists())
        maintenance.run()
        self.assertEqual(board.store.service_issues(), [])
        (directory / 'request.json').write_bytes(original)
        maintenance.run()
        self.assertTrue(directory.exists())
        finished = board.console_call('macro_task_delete', {'macroTaskId': target, 'microTaskCount': preview['microTaskCount'], 'confirm': True})
        self.assertEqual((finished['state'], finished['deletionId']), ('deleted', deleted['deletionId']))
        maintenance.run()
        self.assertFalse(directory.exists())
        self.assertEqual(board.store.service_issues(), [])

    def test_native_reclamation_and_macro_deletion_share_the_storage_lock(self):
        from hey_my_buddy import private_dirs
        from hey_my_buddy.blackboard.tasks import macro_task_deletion, reclamation
        from unittest.mock import patch
        board = self.board()
        submitted, _, _, _, _, view, artifact = self.run_worktree(board)
        self.accept(board, view, artifact, target=self.target_with_artifact(artifact))
        run_id = submitted['runId']
        root = private_dirs.ensure_private_dir(private_dirs.micro_task_root(board.directory, 'dsh', run_id))
        (root / 'evidence.log').write_text('preserve until the owning removal', encoding='utf-8')
        with board.store.db.write() as connection:
            connection.execute("UPDATE tasks SET accepted_at='2000-01-01T00:00:00Z' WHERE task_id=?", (run_id,))
        remove = macro_task_deletion._delete_private_roots
        serialized = []
        def delete_native(store, scope):
            reclamation.reclaim_native(store)
            self.assertTrue(root.exists())
            serialized.append(True)
            return remove(store, scope)
        target = 'run:' + run_id
        preview = board.console_call('macro_task_deletion_preview', {'macroTaskId': target})
        with patch.object(macro_task_deletion, '_delete_private_roots', side_effect=delete_native):
            result = board.console_call('macro_task_delete', {'macroTaskId': target, 'microTaskCount': preview['microTaskCount'], 'confirm': True})
        self.assertEqual(result['state'], 'deleted')
        self.assertEqual(serialized, [True])
        self.assertFalse(root.exists())
        reclamation.reclaim_native(board.store)

    def test_continuation_selects_a_new_manifest_host_seal_and_ignores_another_checkout(self):
        board = self.board()
        submitted, claim, _, checkout, _, view, _ = self.run_worktree(board, disposition='attention')
        run_id = submitted['runId']
        board.call('workflow_continue', {'runId': run_id, 'commandId': 'prepare-before-cancel',
                                        'expectedRevision': view['revision'], 'input': 'prepare', 'helperPolicy': 'keep', **self.control(view)})
        board.store.workflow.prepare_continuation_workspace({'runId': run_id})
        board.call('workflow_cancel', {'runId': run_id, **self.control(view)})
        with board.store.db.read() as connection:
            run = board.store.workflow._run_row(connection, run_id)
            current = json.loads(run['workspace_manifest_json'])
        (checkout / 'host-stage.txt').write_text('Host progress after preparation', encoding='utf-8')
        attempt_id = claim['claim']['attempt']['attemptId']
        host_seal = workspace.host_seal(board.directory, current, run_id, attempt_id)
        foreign = workspace.prepare(board.directory, 'foreign-handoff', {
            'kind': 'worktree', 'cwd': str(self.repo), 'access': 'write', 'writeScope': ['.'],
            'base': {'kind': 'working-tree'}, 'integrator': {'role': 'host', 'id': 'fixture-host'}})
        foreign_seal = workspace.host_seal(board.directory, foreign, run_id, attempt_id)
        with board.store.db.write() as connection:
            pin = board.store.workflow._pin_artifact
            pin(connection, run_id=run_id, kind='partial-output', manifest=host_seal,
                attempt_id=attempt_id, turn_id=None, now=board.store.now())
            pin(connection, run_id=run_id, kind='input', manifest=foreign,
                attempt_id=None, turn_id=None, now=board.store.now())
            pin(connection, run_id=run_id, kind='partial-output', manifest=foreign_seal,
                attempt_id=attempt_id, turn_id=None, now=board.store.now())
            chosen = board.store.workflow._latest_handoff(connection, run, current)
        self.assertEqual(chosen['commit'], host_seal['commit'])
        cancelled = self.view(board, run_id)
        board.call('workflow_continue', {'runId': run_id, 'commandId': 'continue-host-seal',
                                        'expectedRevision': cancelled['revision'], 'input': 'finish', **self.control(cancelled)})
        board.store.workflow.prepare_continuation_workspace({'runId': run_id})
        with board.store.db.read() as connection:
            prepared = json.loads(board.store.workflow._run_row(connection, run_id)['workspace_manifest_json'])
        self.assertEqual(prepared['inputCommit'], host_seal['commit'])
        self.assertEqual(prepared['checkoutRoot'], current['checkoutRoot'])

    def test_cleanup_plan_keeps_one_binding_per_root_for_a_run(self):
        from hey_my_buddy.blackboard.store.db import canonical_json
        board, submitted, manifest, checkout, _, _ = self.accepted_worktree()
        coordinator = board.store.workflow
        first = coordinator.cleanup_plan({'runId': submitted['runId']})['plan']
        self.assertEqual(coordinator.cleanup_plan({'runId': submitted['runId']})['plan']['planId'], first['planId'])
        replacement = workspace.prepare(board.directory, 'second-root-plan', {
            'kind': 'worktree', 'cwd': str(self.repo), 'access': 'write', 'writeScope': ['.'],
            'base': {'kind': 'working-tree'}, 'integrator': {'role': 'host', 'id': 'host-1'},
        })
        with board.store.db.write() as connection:
            connection.execute('UPDATE workflow_runs SET workspace_manifest_json=? WHERE run_id=?',
                               (canonical_json(replacement), submitted['runId']))
        second = coordinator.cleanup_plan({'runId': submitted['runId']})['plan']
        self.assertNotEqual(second['planId'], first['planId'])
        self.assertEqual(second['path'], replacement['checkoutRoot'])
        with board.store.db.read() as connection:
            paths = [row[0] for row in connection.execute(
                'SELECT path FROM workspace_cleanup_plans WHERE run_id=? ORDER BY rowid',
                (submitted['runId'],))]
        self.assertEqual(paths, [str(checkout), replacement['checkoutRoot']])

    def test_busy_result_converges_if_a_competing_cleanup_already_committed(self):
        from unittest.mock import patch
        from hey_my_buddy.errors import BoardError
        board, submitted, _, checkout, _, _ = self.accepted_worktree()
        coordinator = board.store.workflow
        real_apply = coordinator.cleanup_apply
        def completed_elsewhere(params, **kwargs):
            real_apply(params, **kwargs)
            raise BoardError('WORKSPACE_BUSY', 'The competing operation held the lock')
        with patch.object(coordinator, 'cleanup_apply', side_effect=completed_elsewhere):
            result = self.reclaim(board, self.view(board, submitted['runId']))
        self.assertTrue(result['removed'])
        self.assertTrue(result['alreadyRemoved'])
        self.assertFalse(checkout.exists())

    def test_allocation_git_process_and_removal_work_never_hold_a_database_transaction(self):
        import sqlite3
        from unittest.mock import patch
        from hey_my_buddy.blackboard.tasks import checkout_processes, reclamation
        board = self.board()
        intent = {'kind': 'worktree', 'cwd': str(self.repo), 'access': 'write', 'writeScope': ['.'],
                  'base': {'kind': 'working-tree'}, 'integrator': {'role': 'host', 'id': 'fixture-host'}}
        manifest = workspace.prepare(board.directory, 'transaction-free-orphan', intent)
        observed = []
        def outside_transaction(name, operation):
            def call(*args, **kwargs):
                with sqlite3.connect(board.store.db.path, timeout=0) as other:
                    other.execute('BEGIN IMMEDIATE')
                    other.rollback()
                observed.append(name)
                return operation(*args, **kwargs)
            return call
        git_call = workspace._git
        def git_operation(root, *args, **kwargs):
            operation = outside_transaction('refs', git_call) if args[0] == 'update-ref' else git_call
            return operation(root, *args, **kwargs)
        with patch.object(workspace, 'submission_revoke_plan', side_effect=outside_transaction('proof', workspace.submission_revoke_plan)), \
             patch.object(checkout_processes, 'require_idle_checkout', side_effect=outside_transaction('process', checkout_processes.require_idle_checkout)), \
             patch.object(workspace, 'revoke_submission', side_effect=outside_transaction('revoke', workspace.revoke_submission)), \
             patch.object(workspace, '_remove_worktree', side_effect=outside_transaction('checkout', workspace._remove_worktree)), \
             patch.object(workspace, '_git', side_effect=git_operation), \
             patch.object(workspace, 'finish_submission_revoke', side_effect=outside_transaction('records', workspace.finish_submission_revoke)):
            reclamation.Reclamation(board.store).run()
        self.assertEqual(observed, ['proof', 'process', 'revoke', 'checkout', 'refs', 'refs', 'refs', 'refs', 'records'])
        self.assertFalse(Path(manifest['checkoutRoot']).parent.exists())

    def test_unproven_allocations_are_scanned_once_each_and_wait_for_entry_or_related_task_changes(self):
        import os
        from unittest.mock import patch
        from hey_my_buddy.blackboard.tasks import reclamation, storage
        board = self.board()
        intent = {'kind': 'worktree', 'cwd': str(self.repo), 'access': 'write', 'writeScope': ['.'],
                  'base': {'kind': 'working-tree'}, 'integrator': {'role': 'host', 'id': 'fixture-host'}}
        manifests = [workspace.prepare(board.directory, request, intent) for request in ('blocked-one', 'blocked-two')]
        for index, manifest in enumerate(manifests):
            git(Path(manifest['checkoutRoot']), 'switch', '-q', '-c', 'private-branch-' + str(index))
        maintenance = reclamation.Reclamation(board.store)
        with patch.object(storage, '_workspace_reference_rows', wraps=storage._workspace_reference_rows) as scan:
            maintenance.run()
            self.assertEqual(scan.call_count, 2)
            maintenance.run()
            maintenance.changed(['unrelated-task'])
            maintenance.run()
            self.assertEqual(scan.call_count, 2)
            directory = Path(manifests[0]['checkoutRoot']).parent
            os.utime(directory, None)
            maintenance.run()
            self.assertEqual(scan.call_count, 3)
        self.assertEqual(board.store.service_issues(), [])
        submitted, _, _, _, _, _, _ = self.run_worktree(board, request_id='reference-source')
        third = workspace.prepare(board.directory, 'reference-retained', intent)
        with board.store.db.write() as connection:
            connection.execute("INSERT INTO events(kind,task_id,payload_json,created_at) VALUES ('retainer',?,?,?)",
                (submitted['runId'], json.dumps({'path': third['checkoutRoot']}), board.store.now()))
        maintenance.run()
        self.assertTrue(Path(third['checkoutRoot']).exists())
        with board.store.db.write() as connection:
            connection.execute("DELETE FROM events WHERE kind='retainer'")
        maintenance.changed([submitted['runId']])
        maintenance.run()
        self.assertFalse(Path(third['checkoutRoot']).parent.exists())

    def test_allocation_internal_git_and_process_errors_are_visible(self):
        from unittest.mock import patch
        from hey_my_buddy.errors import BoardError
        from hey_my_buddy.blackboard.tasks import checkout_processes, reclamation
        intent = {'kind': 'worktree', 'cwd': str(self.repo), 'access': 'write', 'writeScope': ['.'],
                  'base': {'kind': 'working-tree'}, 'integrator': {'role': 'host', 'id': 'fixture-host'}}
        for operation, failure in (('git', BoardError('WORKSPACE_GIT_UNKNOWN', 'fixture Git unknown')),
                                   ('git', TypeError('fixture internal defect')),
                                   ('process', BoardError('NOT_READY', 'fixture scan incomplete',
                                    reason={'code': 'checkout.process-scan-failed', 'params': {}}))):
            with self.subTest(operation=operation, failure=str(failure)):
                board = self.board()
                manifest = workspace.prepare(board.directory, operation + type(failure).__name__, intent)
                module, name = (workspace, 'submission_revoke_plan') if operation == 'git' else (checkout_processes, 'require_idle_checkout')
                with patch.object(module, name, side_effect=failure):
                    reclamation.Reclamation(board.store).run()
                issues = board.store.service_issues()
                self.assertEqual([(item['stage'], item['code']) for item in issues],
                                 [('allocation-reclamation', failure.code if isinstance(failure, BoardError) else 'TypeError')])
                self.assertTrue(Path(manifest['checkoutRoot']).exists())

    def test_unregistered_existing_snapshot_does_not_own_the_borrowed_checkout(self):
        from hey_my_buddy.blackboard.tasks.reclamation import Reclamation
        board = self.board()
        self.run_worktree(board, request_id='retained-source')
        intent = {'kind': 'existing', 'cwd': str(self.repo), 'access': 'write', 'writeScope': ['.'],
                  'base': {'kind': 'working-tree'}, 'integrator': {'role': 'host', 'id': 'fixture-host'}}
        manifest = workspace.prepare(board.directory, 'unregistered-existing-snapshot', intent)
        directory = board.directory / 'workspaces' / manifest['workspaceId']
        Reclamation(board.store).run()
        self.assertFalse(directory.exists())
        self.assertTrue(self.repo.exists())
        self.assertEqual(board.store.service_issues(), [])

    def test_disappeared_unproven_allocation_does_not_remain_in_the_pending_inventory(self):
        from unittest.mock import patch
        from hey_my_buddy import private_dirs
        from hey_my_buddy.blackboard.tasks.reclamation import Reclamation
        board = self.board()
        _, directory = workspace._workspace_directory(board.directory, 'disappearing-unproven')
        (directory / '.lock').touch()
        (directory / 'request.json').write_text('not a record', encoding='utf-8')
        maintenance = Reclamation(board.store)
        maintenance.run()
        private_dirs.remove_tree(directory)
        lstat = Path.lstat
        def no_removed_history(path, *args, **kwargs):
            if path.is_relative_to(directory):
                raise AssertionError('Revisited a removed historical allocation')
            return lstat(path, *args, **kwargs)
        with patch.object(Path, 'lstat', new=no_removed_history):
            maintenance.run()
            maintenance.run()
        self.assertEqual(board.store.service_issues(), [])
