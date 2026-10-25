"""Cleanup eligibility proofs, evidence retention and continuation cleanup.

Reclaim removes exactly one registered disposable checkout after acceptance or a
recorded conclusion, blocked by unsealed changes and by every honest
cross-check, while failed executions keep their evidence and storage applies its
own protection to unsealed sites.
"""
from __future__ import annotations
from admission import admit_board
import json
import os
import shutil
from pathlib import Path
from unittest import mock
from blackboard.tasks.test_workflow_real import BUDDY
from hey_my_buddy import private_dirs
from hey_my_buddy.blackboard.tasks import workflow as workflow_module
from hey_my_buddy.blackboard.tasks import workspace as workspace_module
from hey_my_buddy.blackboard.store.db import canonical_json
from hey_my_buddy.errors import BoardError

from blackboard.tasks.workspace_lifecycle_case import LifecycleTestCase, git


class CleanupLifecycleTests(LifecycleTestCase):
    def test_storage_apply_reuses_workspace_protection_and_current_revision(self):
        board, submitted, manifest, checkout, artifact, view = self.accepted_worktree()
        with mock.patch.dict(os.environ, {'HMB_RUNTIME_ROOT':str(self.directory / 'runtime')}), mock.patch('hey_my_buddy.blackboard.tasks.storage.process_inventory', return_value=([],[],True)):
            planned = board.call('storage_plan', {})
            candidate = next(r for r in planned['candidates'] if r['path'] == str(checkout))
            self.assertTrue(candidate['eligible'], candidate)
            request = {'planId':planned['planId'], 'commandId':'storage-cleanup', 'confirm':True}
            result = board.call('storage_apply', request)
            self.assertFalse(checkout.exists(), result)
            self.assertTrue(result['removed'], result)
            self.assertEqual(board.call('storage_apply', request), result)
            self.assertTrue(self.repo.exists())

    def test_storage_native_removal_resumes_after_interrupted_delete(self):
        board, submitted, manifest, checkout, artifact, view = self.accepted_worktree()
        run_id = submitted['runId']
        with board.store.db.write() as connection:
            connection.execute("UPDATE tasks SET accepted_at='2000-01-01T00:00:00Z' WHERE task_id=?", (run_id,))
        native = private_dirs.micro_task_root(board.directory, 'zcode', run_id)
        session = private_dirs.ensure_private_dir(native / 'native') / 'sessions.sqlite'
        session.write_bytes(b'private session fixture')
        with mock.patch.dict(os.environ, {'HMB_RUNTIME_ROOT':str(self.directory / 'runtime')}), mock.patch('hey_my_buddy.blackboard.tasks.storage.process_inventory', return_value=([],[],True)):
            planned = board.call('storage_plan', {})
            request = {'planId':planned['planId'], 'commandId':'native-interrupted', 'confirm':True}
            remove_tree = private_dirs.remove_tree
            def fail_native_tomb(path, **kwargs):
                if Path(path).parent == native.parent and Path(path).name.endswith('-' + native.name):
                    raise OSError('injected')
                return remove_tree(path, **kwargs)
            with mock.patch('hey_my_buddy.blackboard.tasks.storage.private_dirs.remove_tree', side_effect=fail_native_tomb):
                with self.assertRaises(BoardError) as caught:
                    board.call('storage_apply', request)
            self.assertEqual(caught.exception.code, 'STORAGE_INCOMPLETE')
            self.assertFalse(native.exists())
            result = board.call('storage_apply', request)
            self.assertTrue(result['complete'])
            self.assertTrue(any(r['path'] == str(native.resolve()) for r in result['removed']))
            self.assertFalse(list(native.parent.glob('.reclaim-*')))

    def test_storage_apply_keeps_unsealed_change_reasons_after_planning(self):
        for preserve_stat in (False, True):
            with self.subTest(preserve_stat=preserve_stat):
                board = self.board()
                self.register(board)
                submitted, claimed, manifest, checkout, seal, view, artifact = self.run_worktree(
                    board, request_id=f"storage-dirty-{preserve_stat}")
                target = self.target_with_artifact(artifact, name=f"storage-dirty-target-{preserve_stat}")
                self.accept(board, view, artifact, target=target, keepCheckout=True)
                with mock.patch.dict(os.environ, {'HMB_RUNTIME_ROOT': str(self.directory / 'runtime')}), \
                        mock.patch('hey_my_buddy.blackboard.tasks.storage.process_inventory', return_value=([], [], True)):
                    planned = board.call('storage_plan', {})
                    candidate = next(row for row in planned['candidates'] if row['path'] == str(checkout))
                    self.assertTrue(candidate['eligible'], candidate)
                    tracked = checkout / 'tracked.txt'
                    facts = tracked.stat()
                    tracked.write_text('dirty! output\n', encoding="utf-8", newline="\n")
                    if preserve_stat:
                        os.utime(tracked, ns=(facts.st_atime_ns, facts.st_mtime_ns))
                    retained = {path.relative_to(checkout): path.read_bytes()
                                for path in checkout.rglob('*') if path.is_file()}
                    refs = git(self.repo, 'for-each-ref', '--format=%(refname) %(objectname)', 'refs/hey-my-buddy/')
                    result = board.call('storage_apply', {
                        'planId': planned['planId'], 'commandId': f'storage-dirty-{preserve_stat}', 'confirm': True})
                skipped = next(row for row in result['skipped'] if row['path'] == str(checkout))
                self.assertEqual(skipped['reasons'], ['unsealed-changes'])
                self.assertFalse(any(row['path'] == str(checkout) for row in result['removed']))
                self.assertTrue(checkout.is_dir())
                self.assertEqual({path.relative_to(checkout): path.read_bytes()
                                  for path in checkout.rglob('*') if path.is_file()}, retained)
                self.assertEqual(git(self.repo, 'for-each-ref', '--format=%(refname) %(objectname)', 'refs/hey-my-buddy/'), refs)

    def test_cleanup_removes_only_the_registered_worktree_and_is_idempotent(self):
        board, submitted, manifest, checkout, artifact, view = self.accepted_worktree()
        run_id = submitted["runId"]
        private_micro_task = private_dirs.ensure_private_dir(private_dirs.native_root(board.directory, 'codex', run_id))
        (private_micro_task / 'session.json').write_text('private native binding', encoding="utf-8", newline="\n")
        applied = self.reclaim(board, view)
        self.assertTrue(applied["removed"])
        self.assertEqual(applied["path"], manifest["checkoutRoot"])
        self.assertFalse(checkout.exists())
        self.assertFalse(private_micro_task.exists())
        # Only the checkout was removed: manifests, patches and Git refs stay readable.
        workspace_dir = Path(self.directory) / "workspaces" / manifest["workspaceId"]
        self.assertTrue((workspace_dir / "manifest.json").exists())
        self.assertTrue(Path(artifact["diffPath"]).exists())
        refs = git(self.repo, "for-each-ref", "--format=%(refname)", f"refs/hey-my-buddy/workspaces/{manifest['workspaceId']}/")
        self.assertIn("outputs", refs)
        still = self.view(board, run_id)
        self.assertEqual(still["cleanup"]["state"], "applied")
        self.assertTrue(still["cleanup"]["retention"]["artifactIds"])
        self.assertIn(str(Path(self.directory) / "workspaces" / manifest["workspaceId"]),
                      still["cleanup"]["retention"]["workspaceDirectory"])
        # Reclaiming an already removed allocation is the same achieved outcome.
        repeat = self.reclaim(board, still)
        self.assertTrue(repeat["removed"])
        self.assertEqual(repeat["alreadyRemoved"], applied["alreadyRemoved"])

    def test_failed_execution_evidence_survives_continuation_acceptance_and_storage_reclaims_it(self):
        self.assert_failed_evidence_survives_continuation()

    def test_completed_execution_with_an_import_failure_keeps_its_evidence(self):
        self.assert_failed_evidence_survives_continuation(import_failure=True)

    def test_concluded_failed_execution_evidence_expires_from_its_host_conclusion(self):
        board, run_id, _attempt, checkout, failed_root, refs, native, _recorded = self.failed_evidence_worktree()
        concluded = board.call('workflow_conclude', {
            'runId': run_id, 'note': 'reviewed the failure and ended this micro task',
            **self.control(self.view(board, run_id)),
        })
        self.assertEqual(concluded['state'], 'concluded')
        self.assertTrue(concluded['reclaim']['removed'], concluded['reclaim'])
        self.assertFalse(checkout.exists())
        self.assertFalse(native.exists())
        self.assertEqual([Path(ref).read_text(encoding="utf-8") for ref in refs], ['failure evidence'] * 2)
        root = failed_root.parent.parent
        with board.store.db.write() as connection:
            connection.execute("UPDATE tasks SET accepted_at='2000-01-01T00:00:00Z' WHERE task_id=?", (run_id,))
        with mock.patch('hey_my_buddy.blackboard.tasks.storage.process_inventory', return_value=([], [], True)):
            planned = board.call('storage_plan', {})
        candidate = next(row for row in planned['candidates'] if row['path'] == str(root))
        self.assertFalse(candidate['eligible'], candidate)
        self.assertEqual(candidate['reasons'], ['grace-period'])
        with board.store.db.write() as connection:
            connection.execute("UPDATE workflow_host_conclusions SET created_at='2000-01-01T00:00:00Z' WHERE run_id=?",
                               (run_id,))
        self.assert_storage_reclaims_evidence(board, run_id, root)

    def test_borrowed_helpers_private_evidence_uses_its_own_conclusion(self):
        board = self.board()
        self.register(board)
        submitted, _claim, _manifest, checkout, _seal, yielded, _artifact = self.run_worktree(
            board, request_id='borrowed-evidence', disposition='assistance')
        run_id = submitted['runId']
        decided = board.call('workflow_decide', {
            'runId': run_id, 'requestId': yielded['activeRequest']['requestId'],
            'commandId': 'borrowed-evidence-helper', 'expectedRevision': yielded['revision'], 'decision': 'approve',
            'helpers': [{**BUDDY, 'requestId': 'evidence-helper', 'task': 'inspect the shared checkout',
                         'cwd': str(checkout),
                         'executionWorkspace': {'kind': 'existing', 'access': 'write', 'writeScope': ['.']}}],
            **self.control(yielded),
        })
        helper_id = decided['children'][0]['taskId']
        claim = self.claim(board, helper_id, request_id='evidence-helper-claim')['claim']
        attempt = claim['attempt']
        helper_manifest = claim['turn']['input']['executionWorkspace']
        self.assertEqual(helper_manifest['checkoutRoot'], str(checkout))
        evidence = private_dirs.ensure_private_dir(private_dirs.attempt_root(
            board.directory, 'dsh', helper_id, attempt['attemptId'])) / 'unknown-events.json'
        evidence.write_text('helper failure evidence', encoding="utf-8", newline="\n")
        seal = workspace_module.seal(self.directory, helper_manifest, helper_id, attempt['attemptId'])
        board.call('execution_result', {'attemptId': attempt['attemptId'], 'generation': attempt['generation'], 'nonce': 'n' * 16, 'status': 'failed', 'terminationReason': 'harness-error', 'shutdownConfirmed': True, 'exitCode': 1, 'result': {'status': 'invalid-result', 'workspaceSeal': seal}})
        concluded = board.call('workflow_conclude', {
            'runId': run_id, 'targetRunId': helper_id, 'note': 'end the failed helper',
            **self.control(self.view(board, run_id)),
        })
        self.assertEqual(concluded['state'], 'concluded')
        self.assertTrue(checkout.exists())
        self.assertEqual(evidence.read_text(encoding="utf-8"), 'helper failure evidence')
        with board.store.db.write() as connection:
            connection.execute("UPDATE workflow_host_conclusions SET created_at='2000-01-01T00:00:00Z' WHERE run_id=?",
                               (helper_id,))
        with mock.patch('hey_my_buddy.blackboard.tasks.storage.process_inventory', return_value=([], [], True)):
            planned = board.call('storage_plan', {})
        root = evidence.parent.parent.parent
        candidate = next(row for row in planned['candidates'] if row['path'] == str(root))
        self.assertEqual(candidate['runId'], helper_id)
        self.assertTrue(candidate['eligible'], candidate)
        self.assertNotIn(self.view(board, run_id)['state'], ('accepted', 'concluded'))

    def test_reclaim_reports_a_checkout_that_vanished_before_the_delete(self):
        board, submitted, manifest, checkout, artifact, view = self.accepted_worktree(request_id="cleanup-vanished")
        shutil.rmtree(checkout)
        self.assertFalse(checkout.exists())
        applied = self.reclaim(board, view)
        self.assertTrue(applied["removed"])
        self.assertTrue(applied["alreadyRemoved"])
        # The retained manifest, not the vanished cwd, still answers compact/get.
        after = self.view(board, submitted["runId"], includeAudit=True)
        self.assertEqual(after["workspace"]["path"], manifest["path"])
        self.assertTrue(after["audit"]["turns"])
        # Reclaiming again is the same achieved outcome, not a second deletion.
        replay = self.reclaim(board, after)
        self.assertTrue(replay["removed"])
        self.assertTrue(replay["alreadyRemoved"])

    def test_cleanup_remove_is_idempotent_for_an_already_removed_allocation(self):
        board, submitted, manifest, checkout, artifact, view = self.accepted_worktree(request_id="cleanup-twice")
        first = workspace_module.cleanup_remove(self.directory, manifest)
        self.assertTrue(first["removed"])
        self.assertFalse(first.get("alreadyRemoved", False))
        self.assertFalse(checkout.exists())
        # The allocation record is the authorization; its checkout being gone is the
        # achieved outcome, not an I/O failure for the daemon sweep or a retry.
        second = workspace_module.cleanup_remove(self.directory, manifest)
        self.assertTrue(second["removed"])
        self.assertTrue(second["alreadyRemoved"])
        self.assertEqual(second["workspaceId"], manifest["workspaceId"])

    def test_reclaim_does_not_report_a_dangling_symlink_as_removed(self):
        board, submitted, manifest, checkout, artifact, view = self.accepted_worktree(request_id='cleanup-dangling')
        workspace_module.cleanup_remove(self.directory, manifest)
        checkout.symlink_to(self.directory / 'absent-target', target_is_directory=True)
        with self.assertRaises(BoardError) as raised:
            self.reclaim(board, view)
        self.assertEqual(raised.exception.code, "NOT_READY")
        self.assertTrue(checkout.is_symlink())
        self.assertNotEqual(self.view(board, submitted['runId'])['cleanup']['state'], 'applied')

    def test_cleanup_remove_keeps_a_real_git_failure_on_a_present_checkout(self):
        board, submitted, manifest, checkout, artifact, view = self.accepted_worktree(request_id="cleanup-git-fail")
        real_git = workspace_module._git

        def failing_remove(root, *arguments, **kwargs):
            if arguments[:2] == ("worktree", "remove"):
                raise BoardError("WORKSPACE_GIT_ERROR", "Git workspace operation failed", operation="worktree")
            return real_git(root, *arguments, **kwargs)

        with mock.patch.object(workspace_module, "_git", failing_remove):
            with self.assertRaises(BoardError) as raised:
                workspace_module.cleanup_remove(self.directory, manifest)
        self.assertEqual(raised.exception.code, "WORKSPACE_GIT_ERROR")
        self.assertTrue(checkout.exists(), "a refused removal must never be reported as achieved")

    def test_reclaim_converges_when_another_owner_finishes_the_removal(self):
        board, submitted, manifest, checkout, artifact, view = self.accepted_worktree(request_id="cleanup-race")
        real_remove = workspace_module.cleanup_remove
        real_record = workspace_module._worktree_record
        state = {"removing": False, "raced": False}

        def racing_record(repository, target):
            record = real_record(repository, target)
            if state["removing"] and record is not None and not state["raced"]:
                # The daemon's own accepted-workspace sweep removes this exact
                # worktree after its registration was read and before the removal
                # transaction below runs: the proof is stale but the deletion is real.
                state["raced"] = True
                workspace_module._git(repository, "worktree", "unlock", str(target), allowed=(0, 1))
                workspace_module._git(repository, "worktree", "remove", "--force", str(target), allowed=(0,))
            return record

        def racing_remove(state_dir, target_manifest, **kwargs):
            state["removing"] = True
            try:
                return real_remove(state_dir, target_manifest, **kwargs)
            finally:
                state["removing"] = False

        with mock.patch.object(workspace_module, "_worktree_record", racing_record), \
                mock.patch.object(workspace_module, "cleanup_remove", racing_remove):
            applied = self.reclaim(board, view)
        self.assertTrue(state["raced"])
        self.assertFalse(checkout.exists())
        self.assertTrue(applied["removed"])
        self.assertTrue(applied["alreadyRemoved"])
        # The event stream records the achieved deletion once, with its real cause.
        with board.store.db.read() as connection:
            events = connection.execute(
                "SELECT payload_json FROM events WHERE task_id=? AND kind='workflow.cleanup_applied' ORDER BY seq",
                (submitted["runId"],),
            ).fetchall()
        self.assertEqual([json.loads(row["payload_json"])["alreadyRemoved"] for row in events], [True])

    def test_reclaim_converges_when_the_checkout_vanishes_during_the_proof(self):
        board, submitted, manifest, checkout, artifact, view = self.accepted_worktree(request_id="cleanup-vanish-proof")
        real_refs = workspace_module._allocation_refs

        def vanishing_refs(repository, allocation_id, target_manifest, retained):
            refs = real_refs(repository, allocation_id, target_manifest, retained)
            # Mid-proof: the other remover deletes the checkout and its registration.
            workspace_module._git(repository, "worktree", "unlock", str(checkout), allowed=(0, 1))
            workspace_module._git(repository, "worktree", "remove", "--force", str(checkout), allowed=(0,))
            return refs

        with mock.patch.object(workspace_module, "_allocation_refs", vanishing_refs):
            applied = self.reclaim(board, view)
        self.assertFalse(checkout.exists())
        self.assertTrue(applied["removed"])
        self.assertTrue(applied["alreadyRemoved"])

    def test_delivered_output_without_integration_blocks_the_reclaim(self):
        board = self.board()
        self.register(board)
        _submitted, _claim, _manifest, checkout, _seal, view, _artifact = self.run_worktree(board)
        with self.assertRaises(BoardError) as raised:
            self.reclaim(board, view)
        self.assertEqual(raised.exception.code, "NOT_READY")
        self.assertIn("not-accepted", raised.exception.details["reasons"])
        self.assertIn("integration-missing", raised.exception.details["reasons"])
        self.assertTrue(checkout.exists())

    def test_cleanup_is_blocked_by_unsealed_changes(self):
        board, submitted, manifest, checkout, artifact, view = self.accepted_worktree(request_id="cleanup-dirty")
        native = private_dirs.ensure_private_dir(private_dirs.native_root(board.directory, 'dsh', submitted['runId']))
        session = native / 'session.json'
        session.write_text('retain until reclaim is authorized', encoding="utf-8", newline="\n")
        (checkout / "tracked.txt").write_text("edited after the seal\n", encoding="utf-8", newline="\n")
        with self.assertRaises(BoardError) as raised:
            self.reclaim(board, view)
        self.assertEqual(raised.exception.code, "NOT_READY")
        self.assertIn("unsealed-changes", raised.exception.details["reasons"])
        blocked_view = self.view(board, submitted["runId"])
        self.assertEqual(blocked_view["cleanup"]["state"], "blocked")
        self.assertEqual(blocked_view["cleanup"]["evidence"]["workspace"]["unsealedPaths"], ["tracked.txt"])
        self.assertTrue(checkout.exists())
        self.assertEqual(session.read_text(encoding="utf-8"), 'retain until reclaim is authorized')
        # Returning the site to the sealed state makes the same workspace eligible.
        (checkout / "tracked.txt").write_text("sealed output\n", encoding="utf-8", newline="\n")
        recovered = self.reclaim(board, blocked_view)
        self.assertTrue(recovered["removed"])
        self.assertFalse(checkout.exists())
        self.assertFalse(native.exists())

    def test_matching_seal_fingerprint_needs_no_git_object_writes_for_cleanup_proof(self):
        board = self.board()
        self.register(board)
        _submitted, _claimed, manifest, checkout, seal, view, artifact = self.run_worktree(
            board, request_id="cleanup-matching-fingerprint"
        )
        self.accept(board, view, artifact, target=self.target_with_artifact(artifact), keepCheckout=True)
        with mock.patch.object(workspace_module, "_write_objects", side_effect=AssertionError("object write")) as write, \
                mock.patch.object(workspace_module, "_tree", side_effect=AssertionError("tree construction")) as tree:
            proof = workspace_module.cleanup_inspect(self.directory, manifest, sealed=seal, retained=[manifest])
        self.assertTrue(proof["eligible"], proof)
        self.assertEqual(proof["reasons"], [])
        write.assert_not_called()
        tree.assert_not_called()
        self.assertTrue(checkout.exists())

    def test_cleanup_never_targets_a_source_checkout_or_foreign_path(self):
        board, submitted, manifest, checkout, artifact, view = self.accepted_worktree(request_id="cleanup-foreign")
        # An existing (non-disposable) workspace is never a cleanup target.
        board_existing = self.board()
        self.register(board_existing, worker_id="w-existing")
        existing = board_existing.call("workflow_submit", {
            **BUDDY, "requestId": "existing-run", "hostId": "host-1", "task": "source checkout task",
            "cwd": str(self.repo), "submissionToken": "t" * 32,
            "executionWorkspace": {"kind": "existing", "access": "read", "writeScope": []},
        })
        self.controls[existing["runId"]] = existing["control"]
        existing_claim = admit_board(board_existing, {'workerId': 'w-existing', 'claimRequestId': 'existing-claim', 'nonce': 'e' * 16, 'runId': existing['runId']})
        existing_manifest = existing_claim["claim"]["turn"]["input"]["executionWorkspace"]
        existing_seal = workspace_module.seal(self.directory, existing_manifest, existing["runId"],
                                              existing_claim["claim"]["attempt"]["attemptId"])
        self.finish_turn(board_existing, existing_claim, existing_seal, nonce="e" * 16, worker_id="w-existing")
        existing_view = self.view(board_existing, existing["runId"])
        existing_artifact = next(row for row in existing_view["artifacts"] if row["kind"] == "output")
        self.accept(board_existing, existing_view, existing_artifact, not_required=True,
                    reason="read-only source checkout")
        existing_view = self.view(board_existing, existing["runId"])
        with self.assertRaises(BoardError) as refused:
            self.reclaim(board_existing, existing_view)
        self.assertEqual(refused.exception.code, "NOT_READY")
        self.assertIn("not-a-managed-worktree", refused.exception.details["reasons"])
        self.assertTrue((self.repo / "tracked.txt").exists())
        # A manifest redirected to the source checkout is preserved, never removed.
        tampered = {**manifest, "checkoutRoot": str(self.repo)}
        tampered["manifestSha256"] = workspace_module._sha(
            workspace_module._json({key: value for key, value in tampered.items() if key != "manifestSha256"})
        )
        with board.store.db.write() as connection:
            connection.execute(
                "UPDATE workflow_runs SET workspace_manifest_json=? WHERE run_id=?",
                (canonical_json(tampered), submitted["runId"]),
            )
        foreign = self.view(board, submitted["runId"])
        with self.assertRaises(BoardError) as raised:
            self.reclaim(board, foreign)
        self.assertEqual(raised.exception.code, "NOT_READY")
        self.assertIn("unsafe-path", raised.exception.details["reasons"])
        self.assertTrue((self.repo / "tracked.txt").exists())
        self.assertTrue(checkout.exists())
        with self.assertRaises(BoardError) as raised:
            workspace_module.cleanup_remove(self.directory, tampered)
        self.assertEqual(raised.exception.code, "WORKSPACE_UNSAFE")
        self.assertTrue((self.repo / "tracked.txt").exists())

    def test_cleanup_keeps_a_foreign_stale_worktree_registry_entry(self):
        board, submitted, manifest, checkout, artifact, view = self.accepted_worktree(
            request_id="cleanup-foreign-stale")
        # A missing registry entry that belongs to someone else is visible to Git
        # until its owner prunes it; this cleanup never runs a repository-wide prune.
        foreign = self.directory / "foreign-worktree"
        git(self.repo, "worktree", "add", "-q", "--detach", str(foreign), "HEAD")
        shutil.rmtree(foreign)
        foreign_path = str(foreign.resolve())
        self.assertIn(foreign_path, git(self.repo, "worktree", "list", "--porcelain"))
        applied = self.reclaim(board, view)
        self.assertTrue(applied["removed"])
        self.assertFalse(checkout.exists())
        self.assertIn(foreign_path, git(self.repo, "worktree", "list", "--porcelain"))
        # The repository owner can still repair its own stale entry afterwards.
        git(self.repo, "worktree", "prune")
        self.assertNotIn(foreign_path, git(self.repo, "worktree", "list", "--porcelain"))

    def test_a_new_writer_cannot_claim_the_checkout_across_the_remove_boundary(self):
        board, submitted, manifest, checkout, artifact, view = self.accepted_worktree(
            request_id="cleanup-fence")
        early: list = []
        claims: list = []
        real_remove = workspace_module.cleanup_remove

        def racing_remove(state_dir, target_manifest, **kwargs):
            # Exactly the documented window: the plan is committed as ``applying`` and
            # inspected, but the directory has not been removed yet. A concurrent new
            # existing-workspace writer first meets the early capture fence...
            try:
                self.submit(board, request_id="fence-writer-early", cwd=checkout)
            except BoardError as error:
                early.append(error)
            else:  # pragma: no cover - the capture fence must refuse this submit
                early.append(None)
            # ...and with that early check disabled, the atomic reservation admission
            # is what refuses the claim.
            with mock.patch.object(workflow_module.WorkflowCoordinator, "_cleanup_fence",
                                   lambda coordinator, path: None):
                try:
                    self.submit(board, request_id="fence-writer-claim", cwd=checkout)
                except BoardError as error:
                    claims.append(error)
                else:  # pragma: no cover - the reservation fence must refuse this claim
                    claims.append(None)
            return real_remove(state_dir, target_manifest, **kwargs)

        with mock.patch.object(workspace_module, "cleanup_remove", racing_remove):
            applied = self.reclaim(board, view)
        self.assertTrue(applied["removed"])
        for attempts, label in ((early, "source capture"), (claims, "reservation admission")):
            self.assertEqual(len(attempts), 1)
            self.assertIsNotNone(attempts[0], f"a new writer claimed the checkout through {label}")
            self.assertEqual(attempts[0].code, "PREPARATION_CONFLICT")
            # Both refusals name the reclaim's own applying plan for this checkout.
            self.assertEqual(attempts[0].details["planId"], applied["planId"])
            self.assertEqual(attempts[0].details["checkoutId"], manifest["checkoutId"])
        self.assertFalse(checkout.exists())
        # No reservation survived either refused claim.
        with board.store.db.read() as connection:
            held = connection.execute(
                "SELECT COUNT(*) FROM workspace_reservations WHERE checkout_id=? AND state IN ('held','transferred')",
                (manifest["checkoutId"],),
            ).fetchone()[0]
        self.assertEqual(held, 0)

    # -- physical allocation ownership across turns --------------------------
    def test_continuation_cleanup_removes_the_original_physical_allocation(self):
        board, submitted, first, second, final, view = self.two_turn_worktree()
        checkout = Path(first["checkoutRoot"])
        run_id = submitted["runId"]
        applied = self.reclaim(board, view)
        self.assertTrue(applied["removed"])
        self.assertEqual(applied["path"], first["checkoutRoot"])
        self.assertFalse(checkout.exists())
        evidence = self.view(board, run_id)["cleanup"]
        # The reclaim names the physical allocation that created the worktree, not
        # the later turn's logical workspace id on the same checkout.
        self.assertEqual(evidence["workspaceId"], first["workspaceId"])
        self.assertNotEqual(evidence["workspaceId"], second["workspaceId"])
        self.assertEqual(evidence["kind"], "worktree")
        self.assertEqual(evidence["path"], first["checkoutRoot"])
        self.assertEqual(evidence["checkoutId"], first["checkoutId"])
        self.assertEqual(evidence["evidence"]["workspace"]["manifestWorkspaceId"], second["workspaceId"])
        self.assertTrue(evidence["retention"]["artifactIds"])
        self.assertTrue(any("outputs" in ref for ref in evidence["retention"]["fixedRefs"]))
        # Only the checkout is removed: both turns' manifests, patches and refs stay.
        self.assertTrue((Path(self.directory) / "workspaces" / first["workspaceId"] / "manifest.json").exists())
        self.assertTrue((Path(self.directory) / "workspaces" / second["workspaceId"] / "manifest.json").exists())
        self.assertTrue(Path(final["diffPath"]).exists())
        refs = git(self.repo, "for-each-ref", "--format=%(refname)")
        self.assertIn(f"refs/hey-my-buddy/workspaces/{first['workspaceId']}/", refs)
        self.assertIn(f"refs/hey-my-buddy/workspaces/{second['workspaceId']}/", refs)
        # The applied plan stays the one authorization; repeating it is a replay.
        repeat_view = self.view(board, run_id)
        self.assertEqual(repeat_view["cleanup"]["state"], "applied")
        repeat = self.reclaim(board, repeat_view)
        self.assertTrue(repeat["removed"])
        self.assertEqual(repeat["alreadyRemoved"], applied["alreadyRemoved"])

    def test_continuation_cleanup_blocks_a_change_after_the_latest_seal(self):
        board, submitted, first, second, final, view = self.two_turn_worktree(request_id="continuation-dirty")
        checkout = Path(second["checkoutRoot"])
        (checkout / "tracked.txt").write_text("edited after the second seal\n", encoding="utf-8", newline="\n")
        with self.assertRaises(BoardError) as raised:
            self.reclaim(board, view)
        self.assertEqual(raised.exception.code, "NOT_READY")
        self.assertIn("unsealed-changes", raised.exception.details["reasons"])
        # The delta is against the latest prepared input: the second stage's own
        # file is sealed, only the later edit is named.
        blocked_view = self.view(board, submitted["runId"])
        self.assertEqual(blocked_view["cleanup"]["evidence"]["workspace"]["unsealedPaths"], ["tracked.txt"])
        self.assertTrue(checkout.exists())
        # Returning the site to the latest sealed state makes it eligible again.
        (checkout / "tracked.txt").write_text("first turn output\n", encoding="utf-8", newline="\n")
        recovered = self.reclaim(board, blocked_view)
        self.assertTrue(recovered["removed"], recovered)

    def test_started_continuation_reclaim_resumes_the_original_allocation_without_gitfile(self):
        board, submitted, first, second, final, view = self.two_turn_worktree()
        checkout = Path(first["checkoutRoot"])
        self.interrupt_reclaim(board, view, checkout, missing=("tracked.txt",), remove_git=True)
        reclaimed = self.reclaim(board, self.view(board, submitted["runId"]))
        self.assertTrue(reclaimed["removed"])
        self.assertFalse(reclaimed["alreadyRemoved"])
        self.assertFalse(checkout.exists())
        plan = self.view(board, submitted["runId"])["cleanup"]
        self.assertEqual(plan["workspaceId"], first["workspaceId"])
        self.assertNotEqual(plan["workspaceId"], second["workspaceId"])
        self.assertTrue(Path(final["diffPath"]).is_file())

    def test_a_borrowed_run_never_authorizes_its_own_cleanup(self):
        board, submitted, first, second, final, view = self.two_turn_worktree(request_id="borrow-owner")
        checkout = Path(first["checkoutRoot"])
        self.register(board, worker_id="w-borrow")
        borrowed = board.call("workflow_submit", {
            **BUDDY, "requestId": "borrowed-run", "hostId": "host-1", "task": "borrowed checkout task",
            "cwd": str(checkout), "submissionToken": "b" * 32,
            "executionWorkspace": {"kind": "existing", "access": "read"},
        })
        self.controls[borrowed["runId"]] = borrowed["control"]
        with board.store.db.read() as connection:
            borrowed_manifest = json.loads(connection.execute(
                "SELECT workspace_manifest_json FROM workflow_runs WHERE run_id=?", (borrowed["runId"],)
            ).fetchone()[0])
        # The borrow is the same physical checkout under another logical workspace id.
        self.assertEqual(borrowed_manifest["kind"], "existing")
        self.assertEqual(borrowed_manifest["checkoutId"], first["checkoutId"])
        self.assertNotEqual(borrowed_manifest["workspaceId"], first["workspaceId"])
        # A live holder keeps the allocation owner from removing the checkout.
        owner_view = self.view(board, submitted["runId"])
        with self.assertRaises(BoardError) as blocked:
            self.reclaim(board, owner_view)
        self.assertEqual(blocked.exception.code, "NOT_READY")
        self.assertIn("workspace-dependency", blocked.exception.details["reasons"])
        # The borrowed run has no worktree manifest of its own: it can never delete it.
        borrowed_view = self.view(board, borrowed["runId"])
        with self.assertRaises(BoardError) as raised:
            self.reclaim(board, borrowed_view)
        self.assertEqual(raised.exception.code, "NOT_READY")
        self.assertIn("not-a-managed-worktree", raised.exception.details["reasons"])
        with self.assertRaises(BoardError) as raised:
            workspace_module.cleanup_remove(self.directory, borrowed_manifest)
        self.assertEqual(raised.exception.code, "WORKSPACE_UNSAFE")
        self.assertTrue(checkout.exists())
        # Once the borrower stops holding the checkout, the owner may clean it.
        board.call("workflow_cancel", {
            "runId": borrowed["runId"], "commandId": "borrowed-cancel",
            **self.control(self.view(board, borrowed["runId"])),
        })
        applied = self.reclaim(board, self.view(board, submitted["runId"]))
        self.assertTrue(applied["removed"])
        self.assertFalse(checkout.exists())

    def test_a_helper_that_only_borrowed_the_worktree_cannot_clean_it(self):
        board = self.board()
        self.register(board)
        submitted, first_claim, manifest, checkout, seal, yielded, artifact = self.run_worktree(
            board, request_id="helper-owner", disposition="assistance"
        )
        run_id = submitted["runId"]
        decided = board.call("workflow_decide", {
            "runId": run_id, "requestId": yielded["activeRequest"]["requestId"], "commandId": "helper-approval",
            "expectedRevision": yielded["revision"], "decision": "approve",
            "helpers": [{**BUDDY, "requestId": "borrow-helper", "task": "helper on the parent checkout",
                         "cwd": str(checkout),
                         "executionWorkspace": {"kind": "existing", "access": "write", "writeScope": ["."]}}],
            **self.control(yielded),
        })
        self.assertEqual(decided["decision"], "approve")
        helper_id = decided["children"][0]["taskId"]
        with board.store.db.read() as connection:
            helper_manifest = json.loads(connection.execute(
                "SELECT workspace_manifest_json FROM workflow_children WHERE child_task_id=?", (helper_id,)
            ).fetchone()[0])
        self.assertEqual(helper_manifest["kind"], "existing")
        self.assertEqual(helper_manifest["checkoutId"], manifest["checkoutId"])
        self.assertNotEqual(helper_manifest["workspaceId"], manifest["workspaceId"])
        # A helper run that only borrowed the parent's worktree has no allocation
        # provenance of its own, even though its cwd is under state/workspaces.
        owner_view = self.view(board, run_id)
        # Reclaim always carries the controlling root's Host control, even when it
        # only proves that a borrowed run owns nothing disposable.
        with self.assertRaises(BoardError) as raised:
            self.reclaim(board, owner_view, targetRunId=helper_id)
        self.assertEqual(raised.exception.code, "NOT_READY")
        self.assertIn("not-a-managed-worktree", raised.exception.details["reasons"])
        with self.assertRaises(BoardError) as raised:
            workspace_module.cleanup_remove(self.directory, helper_manifest)
        self.assertEqual(raised.exception.code, "WORKSPACE_UNSAFE")
        # The owner's own allocation cannot be removed while the helper holds it.
        with self.assertRaises(BoardError) as blocked:
            self.reclaim(board, owner_view)
        self.assertEqual(blocked.exception.code, "NOT_READY")
        self.assertIn("workspace-dependency", blocked.exception.details["reasons"])
        self.assertTrue(checkout.exists())
