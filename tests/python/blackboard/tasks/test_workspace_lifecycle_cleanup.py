"""Cleanup eligibility proofs, evidence retention and continuation cleanup.

Reclaim removes exactly one registered disposable checkout after acceptance or a
recorded conclusion, blocked by unsealed changes and by every honest
cross-check, while failed executions keep their evidence and storage applies its
own protection to unsealed sites.
"""
from __future__ import annotations
from hey_my_buddy.protocol.workspace_manifest import turn_manifest, resolve_manifest, record_reference
from unittest.mock import patch as defer_reclaim
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
from hey_my_buddy.errors import BoardError
from hey_my_buddy.paths import native_path

from blackboard.tasks.workspace_lifecycle_case import LifecycleTestCase, git


class CleanupLifecycleTests(LifecycleTestCase):



    def test_cumulative_failure_keeps_partial_output_readable_and_resumable(self):
        board = self.board()
        self.register(board)
        submitted, _first, original, checkout, _seal, delivered, _artifact = self.run_worktree(board)
        run_id = submitted["runId"]
        board.call("workflow_continue", {"runId": run_id, "commandId": "partial-stage",
                   "expectedRevision": delivered["revision"], "input": "Continue", **self.control(delivered)})
        second = self.claim(board, run_id, request_id="partial-claim", nonce="q" * 16)
        claim = second["claim"]
        manifest = turn_manifest(board.directory, claim["turn"]["input"])
        (checkout / "partial.txt").write_text("partial change\n", encoding="utf-8")
        partial = workspace_module.seal(board.directory, manifest, run_id, claim["attempt"]["attemptId"])
        cumulative = Path(partial["diffPath"]).with_name("cumulative-" + original["inputCommit"] + ".patch")
        self.assertFalse(cumulative.exists())
        board.call("execution_result", {"attemptId": claim["attempt"]["attemptId"],
                   "generation": claim["attempt"]["generation"], "nonce": "q" * 16,
                   "status": "failed", "shutdownConfirmed": True, "exitCode": 1,
                   "result": {"status": "failed", "partialWorkspaceSeal": record_reference(board.directory, partial),
                              "cumulativePatchError": {"code": "WORKSPACE_IO_ERROR", "message": "injected patch failure"}}})
        failed = self.view(board, run_id)
        preserved = next(row for row in failed["artifacts"] if row["kind"] == "partial-output")
        self.assertNotIn("cumulativePatch", preserved)
        self.assertEqual(Path(preserved["diffPath"]).read_bytes(), Path(partial["diffPath"]).read_bytes())
        board.call("workflow_continue", {"runId": run_id, "commandId": "resume-partial",
                   "expectedRevision": failed["revision"], "input": "Finish", **self.control(failed)})
        third = self.claim(board, run_id, request_id="partial-resume", nonce="r" * 16)
        self.assertIsNotNone(third["claim"], third)
        final_input = turn_manifest(board.directory, third["claim"]["turn"]["input"])
        (checkout / "final.txt").write_text("final change\n", encoding="utf-8")
        final = workspace_module.seal(board.directory, final_input, run_id, third["claim"]["attempt"]["attemptId"])
        self.finish_turn(board, third, final, nonce="r" * 16)
        view = self.view(board, run_id)
        artifact = next(row for row in view["artifacts"] if row["artifactId"] == view["finalArtifactId"])
        with defer_reclaim.object(board.store.workflow, "_reclaim_checkout", return_value={"removed": False}):
            self.accept(board, view, artifact, not_required=True)
        with board.store.db.read() as connection:
            attempt_ids = [row[0] for row in connection.execute("SELECT attempt_id FROM attempts WHERE task_id=?", (run_id,))]
        for attempt_id in attempt_ids:
            board.store.complete_execution_cleanup(attempt_id)
        import tempfile
        from tools.test_export_board_sample import export_sample
        from support import InProcessBoard
        with tempfile.TemporaryDirectory(dir=self.directory.parent, prefix="partial-sample-") as temporary:
            output = export_sample(board.directory, Path(temporary) / "sample")
            sample = InProcessBoard(output)
            try:
                sample_view = sample.call("workflow_get", {"runId": run_id})
                sample_partial = next(row for row in sample_view["artifacts"] if row["kind"] == "partial-output")
                self.assertNotIn("cumulativePatch", sample_partial)
                self.assertTrue(Path(sample_partial["diffPath"]).is_relative_to(output))
            finally:
                sample.close()
        # A required cumulative file that disappears is still an integrity error.
        Path(artifact["cumulativePatch"]["path"]).unlink()
        with self.assertRaises(BoardError) as missing:
            self.view(board, run_id)
        self.assertEqual(missing.exception.code, "ARTIFACT_MISSING")

    def test_cleanup_removes_only_the_registered_worktree_and_is_idempotent(self):
        board, submitted, manifest, checkout, artifact, view = self.accepted_worktree()
        run_id = submitted["runId"]
        private_micro_task = private_dirs.ensure_private_dir(private_dirs.native_root(board.directory, 'codex', run_id))
        native_path(private_micro_task / 'session.json').write_text('private native binding', encoding="utf-8", newline="\n")
        applied = self.reclaim(board, view)
        self.assertTrue(applied["removed"])
        self.assertEqual(applied["path"], manifest["checkoutRoot"])
        self.assertFalse(checkout.exists())
        self.assertTrue(native_path(private_micro_task).exists())
        # Only the checkout was removed: manifests, patches and Git refs stay readable.
        workspace_dir = Path(self.directory) / "workspaces" / manifest["workspaceId"]
        self.assertTrue((workspace_dir / "manifest.json").exists())
        self.assertTrue(native_path(Path(artifact["diffPath"])).exists())
        refs = git(self.repo, "for-each-ref", "--format=%(refname)", f"refs/hey-my-buddy/workspaces/{manifest['workspaceId']}/")
        self.assertIn("outputs", refs)
        still = self.view(board, run_id)
        self.assertEqual(still["cleanup"]["state"], "applied")
        self.assertTrue(still["cleanup"]["retention"]["artifactIds"])
        self.assertEqual(artifact["cumulativePatch"]["path"], artifact["diffPath"])
        self.assertEqual(still["cleanup"]["retention"]["outputPatches"], [artifact["diffPath"]])
        self.assertIn(str(Path(self.directory) / "workspaces" / manifest["workspaceId"]),
                      still["cleanup"]["retention"]["workspaceDirectory"])
        # Reclaiming an already removed allocation is the same achieved outcome.
        repeat = self.reclaim(board, still)
        self.assertTrue(repeat["removed"])
        self.assertEqual(repeat["alreadyRemoved"], applied["alreadyRemoved"])

    def test_failed_execution_evidence_survives_acceptance_until_service_reclamation(self):
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
        self.assertTrue(native_path(native).exists())
        self.assertEqual([native_path(Path(ref)).read_text(encoding="utf-8") for ref in refs], ['failure evidence'] * 2)
        root = failed_root.parent.parent
        with board.store.db.write() as connection:
            connection.execute("UPDATE tasks SET accepted_at='2000-01-01T00:00:00Z' WHERE task_id=?", (run_id,))
        from hey_my_buddy.blackboard.tasks.reclamation import reclaim_native
        reclaim_native(board.store)
        self.assertTrue(root.exists())
        with board.store.db.write() as connection:
            connection.execute("UPDATE workflow_host_conclusions SET created_at='2000-01-01T00:00:00Z' WHERE run_id=?",
                               (run_id,))
        self.assert_service_reclaims_evidence(board, run_id, root)

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
        helper_manifest = turn_manifest(self.directory, claim['turn']['input'])
        self.assertEqual(helper_manifest['checkoutRoot'], str(checkout))
        evidence = private_dirs.ensure_private_dir(private_dirs.attempt_root(
            board.directory, 'dsh', helper_id, attempt['attemptId'])) / 'unknown-events.json'
        native_path(evidence).write_text('helper failure evidence', encoding="utf-8", newline="\n")
        seal = workspace_module.seal(self.directory, helper_manifest, helper_id, attempt['attemptId'])
        board.call('execution_result', {'attemptId': attempt['attemptId'], 'generation': attempt['generation'], 'nonce': 'n' * 16, 'status': 'failed', 'terminationReason': 'harness-error', 'shutdownConfirmed': True, 'exitCode': 1, 'result': {'status': 'invalid-result', 'workspaceSeal': record_reference(self.directory, seal)}})
        concluded = board.call('workflow_conclude', {
            'runId': run_id, 'targetRunId': helper_id, 'note': 'end the failed helper',
            **self.control(self.view(board, run_id)),
        })
        self.assertEqual(concluded['state'], 'concluded')
        self.assertTrue(checkout.exists())
        self.assertEqual(native_path(evidence).read_text(encoding="utf-8"), 'helper failure evidence')
        with board.store.db.write() as connection:
            connection.execute("UPDATE workflow_host_conclusions SET created_at='2000-01-01T00:00:00Z' WHERE run_id=?",
                               (helper_id,))
        from hey_my_buddy.blackboard.tasks.reclamation import reclaim_native
        reclaim_native(board.store)
        self.assertFalse(native_path(evidence).exists())
        self.assertTrue(checkout.exists())

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

    def test_cleanup_remove_keeps_the_original_registration_after_git_removal_fails(self):
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
        self.assertFalse(checkout.exists())
        self.assertEqual(workspace_module._worktree_record(self.repo, checkout)['locked'],
                         'hey-my-buddy:' + manifest['workspaceId'])
        retried = workspace_module.cleanup_remove(self.directory, manifest)
        self.assertTrue(retried['removed'])
        self.assertTrue(retried['alreadyRemoved'])
        self.assertIsNone(workspace_module._worktree_record(self.repo, checkout))

    def test_reclaim_converges_when_another_owner_finishes_the_removal(self):
        board, submitted, manifest, checkout, artifact, view = self.accepted_worktree(request_id="cleanup-race")
        real_remove = workspace_module.cleanup_remove
        def racing_remove(state_dir, target_manifest, **kwargs):
            # Removal outside this service can complete after its proof. The
            # exact allocation is already gone when the proven removal starts.
            workspace_module._git(self.repo, "worktree", "unlock", str(checkout), allowed=(0, 1))
            workspace_module._git(self.repo, "worktree", "remove", "--force", str(checkout), allowed=(0,))
            return real_remove(state_dir, target_manifest, **kwargs)

        with mock.patch.object(workspace_module, "cleanup_remove", racing_remove):
            applied = self.reclaim(board, view)
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
        native_path(session).write_text('retain until reclaim is authorized', encoding="utf-8", newline="\n")
        (checkout / "tracked.txt").write_text("edited after the seal\n", encoding="utf-8", newline="\n")
        with self.assertRaises(BoardError) as raised:
            self.reclaim(board, view)
        self.assertEqual(raised.exception.code, "NOT_READY")
        self.assertIn("unsealed-changes", raised.exception.details["reasons"])
        blocked_view = self.view(board, submitted["runId"])
        self.assertEqual(blocked_view["cleanup"]["state"], "blocked")
        self.assertEqual(blocked_view["cleanup"]["evidence"]["workspace"]["unsealedPaths"], ["tracked.txt"])
        self.assertTrue(checkout.exists())
        self.assertEqual(native_path(session).read_text(encoding="utf-8"), 'retain until reclaim is authorized')
        # Returning the site to the sealed state makes the same workspace eligible.
        (checkout / "tracked.txt").write_text("sealed output\n", encoding="utf-8", newline="\n")
        recovered = self.reclaim(board, blocked_view)
        self.assertTrue(recovered["removed"])
        self.assertFalse(checkout.exists())
        self.assertTrue(native_path(native).exists())

    def test_matching_seal_fingerprint_needs_no_tree_construction_for_cleanup_proof(self):
        board = self.board()
        self.register(board)
        _submitted, _claimed, manifest, checkout, seal, view, artifact = self.run_worktree(
            board, request_id="cleanup-matching-fingerprint"
        )
        with defer_reclaim.object(board.store.workflow, "_reclaim_checkout", return_value={"removed": False, "reasons": ["fixture-retained"]}):
            self.accept(board, view, artifact, target=self.target_with_artifact(artifact))
        with mock.patch.object(workspace_module, "_tree", side_effect=AssertionError("tree construction")) as tree:
            proof = workspace_module.cleanup_inspect(self.directory, manifest, sealed=seal, retained=[manifest])
        self.assertTrue(proof["eligible"], proof)
        self.assertEqual(proof["reasons"], [])
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
        existing_manifest = turn_manifest(self.directory, existing_claim["claim"]["turn"]["input"])
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
        with board.store.db.read() as connection:
            frozen = connection.execute("SELECT workspace_id,workspace_manifest_sha256 FROM workflow_runs WHERE run_id=?",
                                        (submitted["runId"],)).fetchone()
        self.assertEqual(tuple(frozen), (None, None))
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
        foreign_path = foreign.resolve().as_posix()
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
            self.assertEqual(attempts[0].details["checkoutRoot"], manifest["checkoutRoot"])
        self.assertFalse(checkout.exists())
        # No reservation survived either refused claim.
        with board.store.db.read() as connection:
            held = connection.execute(
                "SELECT COUNT(*) FROM workspace_reservations WHERE checkout_root=? AND state IN ('held','transferred')",
                (manifest["checkoutRoot"],),
            ).fetchone()[0]
        self.assertEqual(held, 0)

    # -- physical allocation ownership across turns --------------------------
    def test_continuation_cleanup_removes_the_original_physical_allocation(self):
        board, submitted, first, second, final, view = self.two_turn_worktree()
        checkout = Path(first["checkoutRoot"])
        run_id = submitted["runId"]
        from hey_my_buddy.blackboard.tasks import reclamation
        self.assertIn(run_id, reclamation.workspace_candidates(board.store))
        cumulative = final["cumulativePatch"]
        self.assertNotEqual(cumulative["path"], final["diffPath"])
        self.assertNotEqual(cumulative["sha256"], final["diffSha256"])
        self.assertNotEqual(native_path(Path(cumulative["path"])).read_bytes(),
                            native_path(Path(final["diffPath"])).read_bytes())
        self.assertEqual({path.name for path in native_path(Path(final["diffPath"]).parent).glob("*.patch")},
                         {"output.patch", f"cumulative-{first['inputCommit']}.patch"})
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
        self.assertNotIn("checkoutId", evidence)
        self.assertEqual(evidence["evidence"]["workspace"]["manifestWorkspaceId"], second["workspaceId"])
        self.assertTrue(evidence["retention"]["artifactIds"])
        self.assertTrue(any("outputs" in ref for ref in evidence["retention"]["fixedRefs"]))
        outputs = [row for row in view["artifacts"] if row["kind"] == "output"]
        retained = evidence["retention"]["outputPatches"]
        self.assertEqual(len(retained), 3)
        self.assertEqual(len(retained), len(set(retained)))
        self.assertEqual(set(retained), {path for row in outputs
                                        for path in (row["diffPath"], row["cumulativePatch"]["path"])})
        for path in retained:
            self.assertTrue(native_path(Path(path)).is_file())
        # The whole-task patch alone reconstructs both turns after checkout removal.
        target = self.directory / "cumulative-target"
        git(self.directory, "clone", "-q", str(self.repo), str(target))
        git(target, "apply", "--binary", cumulative["path"])
        self.assertEqual((target / "tracked.txt").read_text(encoding="utf-8"), "first turn output\n")
        self.assertEqual((target / "second.txt").read_text(encoding="utf-8"), "second turn output\n")
        # Only the checkout is removed: both turns' manifests, patches and refs stay.
        self.assertTrue((Path(self.directory) / "workspaces" / first["workspaceId"] / "manifest.json").exists())
        self.assertTrue((Path(self.directory) / "workspaces" / second["workspaceId"] / "manifest.json").exists())
        self.assertTrue(native_path(Path(final["diffPath"])).exists())
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
        self.assertTrue(native_path(Path(final["diffPath"])).is_file())

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
            borrowed_row = connection.execute(
                "SELECT workspace_id,workspace_manifest_sha256 FROM workflow_runs WHERE run_id=?", (borrowed["runId"],)
            ).fetchone()
        borrowed_manifest = resolve_manifest(self.directory, {"workspaceId": borrowed_row["workspace_id"],
                                                              "manifestSha256": borrowed_row["workspace_manifest_sha256"]})
        # The borrow is the same physical checkout under another logical workspace id.
        self.assertEqual(borrowed_manifest["kind"], "existing")
        self.assertEqual(borrowed_manifest["checkoutRoot"], first["checkoutRoot"])
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
            helper_row = connection.execute(
                "SELECT workspace_id,workspace_manifest_sha256 FROM workflow_runs WHERE run_id=?", (helper_id,)
            ).fetchone()
        helper_manifest = resolve_manifest(self.directory, {"workspaceId": helper_row["workspace_id"],
                                                            "manifestSha256": helper_row["workspace_manifest_sha256"]})
        self.assertEqual(helper_manifest["kind"], "existing")
        self.assertEqual(helper_manifest["checkoutRoot"], manifest["checkoutRoot"])
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
