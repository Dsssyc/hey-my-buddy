"""Workflow response identities and cleanup journals on private boards."""
from __future__ import annotations
from hey_my_buddy.protocol.workspace_manifest import turn_manifest
from unittest.mock import patch as defer_reclaim

from contextlib import ExitStack
import json
import os
from pathlib import Path
import shutil
import subprocess
import unittest
from unittest.mock import patch

from blackboard.tasks.test_workflow import WorkflowTestCase
from blackboard.tasks import test_scope_recovery as scope_fixtures
from blackboard.tasks.test_workflow_real import RealWorkspaceTestCase

from hey_my_buddy.blackboard.tasks import workflow as workflow_module
from hey_my_buddy.blackboard.tasks import workspace as workspace_module
from hey_my_buddy.blackboard.tasks import macro_task_deletion
from hey_my_buddy.errors import BoardError


class WorkflowReceiptTests(WorkflowTestCase):
    def test_cleanup_keeps_all_retained_artifacts_and_distinct_allocation_manifests(self):
        from hey_my_buddy.blackboard.store.db import canonical_json, sha256_text

        board = self.board()
        submitted = self.submit(board)
        claimed = self.claim(board)
        coordinator = board.store.workflow
        original = turn_manifest(self.directory, claimed["claim"]["turn"]["input"])
        digests = {original["manifestSha256"]}
        with board.store.db.write() as connection:
            for index in range(140):
                manifest = {**original, "workspaceId": f"ws-{index:032x}"}
                manifest.pop("manifestSha256")
                manifest["manifestSha256"] = sha256_text(canonical_json(manifest))
                record_dir = self.directory / "workspaces" / manifest["workspaceId"]
                record_dir.mkdir(parents=True)
                (record_dir / "manifest.json").write_text(canonical_json(manifest), encoding="utf-8")
                digests.add(manifest["manifestSha256"])
                coordinator._pin_artifact(connection, run_id=submitted["runId"], kind="input",
                                          manifest=manifest, attempt_id=None, turn_id=None, now=coordinator.now())
            run = coordinator._run_row(connection, submitted["runId"])
            task = connection.execute("SELECT * FROM tasks WHERE task_id=?", (submitted["runId"],)).fetchone()
            retained = coordinator._allocation_provenance(connection, run)
            _reasons, _evidence, retention = coordinator._cleanup_reasons(connection, run, task, original)
            artifact_ids = {row[0] for row in connection.execute(
                "SELECT artifact_id FROM workflow_artifacts WHERE run_id=?", (submitted["runId"],))}
        # The original appears in both a pinned artifact and its frozen turn.
        self.assertEqual(len(retained), len(digests))
        self.assertEqual({item["manifestSha256"] for item in retained}, digests)
        self.assertEqual(set(retention["artifactIds"]), artifact_ids)
        self.assertGreater(len(artifact_ids), 128)

    def test_deleted_domain_responses_are_removed_with_the_run(self):
        board = self.board()
        submitted = self.submit(board, request_id="delete-replay",
                                create_macro={"title": "Disposable", "goal": "Disposable"})
        self.finish_turn(board, self.claim(board), disposition="attention")
        view = board.call("workflow_get", {"runId": submitted["runId"]})
        answered = board.call("workflow_continue", {
            "runId": submitted["runId"], "requestId": view["activeRequest"]["requestId"],
            "decision": "decline",
        })
        self.assertEqual(answered["state"], "executing")
        cancelled = board.call("workflow_cancel", {"runId": submitted["runId"],
                                                    "note": "Close disposable work"})
        self.assertEqual(cancelled["state"], "cancelled")
        board.call("workflow_conclude", {"runId": submitted["runId"],
                                         "note": "Reviewed the stopped work"})
        with ExitStack() as stack:
            for stage in ("_delete_sessions", "_delete_workspaces", "_delete_private_roots"):
                stack.enter_context(patch.object(macro_task_deletion, stage, return_value=[]))
            macro_task_deletion.delete(board.store, submitted["macroTaskId"], 1)
        with board.store.db.read() as connection:
            for table in ("workflow_requests", "workflow_continuations", "workflow_host_conclusions"):
                self.assertEqual(connection.execute(
                    f"SELECT COUNT(*) FROM {table} WHERE run_id=?", (submitted["runId"],)
                ).fetchone()[0], 0)
        for operation, params in (("workflow_continue", {"runId": submitted["runId"],
                                                         "requestId": view["activeRequest"]["requestId"],
                                                         "decision": "decline"}),
                                  ("workflow_cancel", {"runId": submitted["runId"]})):
            with self.subTest(operation=operation), self.assertRaises(BoardError) as caught:
                board.call(operation, params)
            self.assertEqual(caught.exception.code, "NOT_FOUND")

    def test_decision_replay_uses_the_request_row(self):
        board = self.board()
        submitted = self.submit(board)
        self.finish_turn(board, self.claim(board), disposition="attention")
        view = board.call("workflow_get", {"runId": submitted["runId"]})
        params = {"runId": submitted["runId"], "requestId": view["activeRequest"]["requestId"],
                  "decision": "approve"}
        first = board.call("workflow_continue", params)
        replayed = board.call("workflow_continue", params)
        self.assertEqual(replayed["state"], first["state"])
        self.assertTrue(replayed["alreadyResponded"])
        with board.store.db.read() as connection:
            response = connection.execute(
                "SELECT response_kind,response_id FROM workflow_requests WHERE request_id=?",
                (params["requestId"],),
            ).fetchone()
        self.assertEqual((response["response_kind"], response["response_id"]),
                         ("decision", params["requestId"]))
        with self.assertRaises(BoardError) as changed:
            board.call("workflow_continue", {**params, "decision": "decline"})
        self.assertEqual(changed.exception.code, "CONFLICT")

    def test_acceptance_consumes_the_artifact_once(self):
        board = self.board()
        submitted = self.submit(board)
        self.finish_turn(board, self.claim(board))
        delivered = board.call("workflow_get", {"runId": submitted["runId"]})
        artifact_id = delivered["finalArtifactId"]
        params = {"runId": submitted["runId"], "artifactId": artifact_id,
                  "note": "Reviewed", "notRequired": "No separate target"}
        accepted = board.call("workflow_accept", params)
        replayed = board.call("workflow_accept", params)
        self.assertTrue(replayed["alreadyResponded"])
        self.assertEqual(replayed["integrationId"], accepted["integrationId"])
        with board.store.db.read() as connection:
            row = connection.execute(
                "SELECT response_kind,response_id FROM workflow_artifacts WHERE artifact_id=?",
                (artifact_id,),
            ).fetchone()
        self.assertEqual((row["response_kind"], row["response_id"]),
                         ("accept", accepted["integrationId"]))
        with self.assertRaises(BoardError) as changed:
            board.call("workflow_continue", {"runId": submitted["runId"],
                                             "artifactId": artifact_id, "input": "Try again"})
        self.assertEqual(changed.exception.code, "CONFLICT")

    def test_conclusion_replay_uses_its_domain_record(self):
        board = self.board()
        submitted = self.submit(board)
        self.finish_turn(board, self.claim(board))
        delivered = board.call("workflow_get", {"runId": submitted["runId"]})
        params = {"runId": submitted["runId"], "artifactId": delivered["finalArtifactId"],
                  "note": "Close with the recorded output"}
        first = board.call("workflow_conclude", params)
        replayed = board.call("workflow_conclude", params)
        self.assertTrue(replayed["alreadyResponded"])
        self.assertEqual(replayed["conclusionId"], first["conclusionId"])
        with board.store.db.read() as connection:
            self.assertEqual(connection.execute(
                "SELECT COUNT(*) FROM workflow_host_conclusions WHERE run_id=?",
                (submitted["runId"],),
            ).fetchone()[0], 1)
        with self.assertRaises(BoardError) as changed:
            board.call("workflow_conclude", {**params, "note": "Different conclusion"})
        self.assertEqual(changed.exception.code, "CONFLICT")

    def test_small_answers_remain_exact(self):
        board = self.board()
        params = {"requestId": "small-receipt", "adapter": "command", "task": "Record a command",
                  "cwd": str(self.workdir()), "argv": ["/bin/true"]}
        answer = board.store.task_submit(params, command_id="small-submit")
        board.client().cancel(runId=answer["task"]["runId"])
        self.assertEqual(board.client().get(runId=answer["task"]["runId"])["state"], "cancelled")
        self.assertEqual(board.store.task_submit(params, command_id="small-submit"), answer)
        with self.assertRaises(BoardError) as caught:
            board.store.task_submit({**params, "task": "A different request"}, command_id="small-submit")
        self.assertEqual(caught.exception.code, "CONFLICT")


class ResolutionReceiptTests(RealWorkspaceTestCase):
    # Share the established real-Git scenario without inheriting its full suite.
    register = scope_fixtures.ScopeRecoveryTestCase.register
    micro_task_submit = scope_fixtures.ScopeRecoveryTestCase.micro_task_submit
    claim = scope_fixtures.ScopeRecoveryTestCase.claim
    scope_failure = scope_fixtures.ScopeRecoveryTestCase.scope_failure
    turn_record = scope_fixtures.ScopeRecoveryTestCase.turn_record
    finish_turn = scope_fixtures.ScopeRecoveryTestCase.finish_turn
    view = scope_fixtures.ScopeRecoveryTestCase.view
    resolve = scope_fixtures.ScopeRecoveryTestCase.resolve
    resolution_facts = scope_fixtures.ScopeRecoveryTestCase.resolution_facts
    scoped_failure = scope_fixtures.ScopeRecoveryTestCase.scoped_failure
    native_scope_failure = scope_fixtures.ScopeRecoveryTestCase.native_scope_failure
    verified_target = scope_fixtures.ScopeRecoveryTestCase.verified_target

    def setUp(self):
        super().setUp()
        self.worker = "w-scope"
        previous_workspace = workflow_module._workspace_module
        workflow_module._workspace_module = workspace_module
        self.addCleanup(setattr, workflow_module, "_workspace_module", previous_workspace)

    def test_partial_resolution_replay_retains_the_first_response(self):
        board, submitted, _claimed, _manifest, _checkout = self.scoped_failure()
        view = self.view(board, submitted["runId"])
        conflict = view["workspaceConflicts"][0]
        first_request = {"runId": submitted["runId"], "requestId": view["activeRequest"]["requestId"],
                         "resolution": "restore", "observedFingerprint": conflict["observedFingerprint"],
                         "paths": ["outside.txt"], "note": "Restore one path"}
        partial = board.call("workflow_continue", first_request)
        self.assertEqual(partial["resolutionState"], "open")
        current = self.view(board, submitted["runId"])
        completed = self.resolve(board, current, current["workspaceConflicts"][0], "restore",
                                 paths=["tracked.txt"], fingerprint=partial["resolvedFingerprint"],
                                 note="Restore remaining path")
        self.assertEqual(completed["resolutionState"], "restored")
        replayed = board.call("workflow_continue", first_request)
        self.assertTrue(replayed["alreadyResponded"])
        self.assertEqual(replayed["artifactId"], partial["artifactId"])
        with board.store.db.read() as connection:
            first_row = connection.execute(
                "SELECT response_kind,response_id FROM workflow_requests WHERE request_id=?",
                (first_request["requestId"],),
            ).fetchone()
        self.assertEqual(first_row["response_kind"], "resolution")
        self.assertEqual(first_row["response_id"], conflict["conflictId"])

    def test_acceptance_replay_preserves_resolved_artifact_binding(self):
        board, submitted, _claimed, _manifest, _checkout = self.native_scope_failure(
            request_id="receipt-native-resolution")
        view = self.view(board, submitted["runId"])
        conflict = view["workspaceConflicts"][0]
        resolved = self.resolve(board, view, conflict, "restore", paths=["outside.txt", "tracked.txt"],
                                note="restore refused paths")
        self.assertTrue(resolved["delivered"])
        target = self.verified_target(resolved["artifact"])
        request = {"runId": submitted["runId"], "artifactId": resolved["artifactId"],
                   "note": "reviewed the Host-resolved delivery",
                   "target": {"path": str(target["path"]), "ref": "HEAD"},
                   "beforeCommit": target["before"]}
        first = board.call("workflow_accept", request)
        self.assertEqual(first["state"], "accepted")
        accepted = self.view(board, submitted["runId"])
        self.assertEqual(accepted["integrations"][0]["artifactId"], resolved["artifactId"])
        self.assertEqual(accepted["workspaceConflicts"][0]["artifactId"], resolved["artifactId"])
        self.assertTrue(accepted["workspaceConflicts"][0]["delivery"]["deliveredAt"])
        replayed = board.call("workflow_accept", request)
        self.assertTrue(replayed["alreadyResponded"])
        self.assertEqual(replayed["integrationId"], first["integrationId"])
        self.assertEqual(len(self.view(board, submitted["runId"])["integrations"]), 1)

    def test_cleanup_idempotency_uses_the_journal_without_command_receipts(self):
        board = self.board()
        submitted = self.micro_task_submit(board, request_id="receipt-cleanup", scope=(".",), kind="worktree")
        run_id = submitted["runId"]
        claimed = self.claim(board, run_id)
        manifest = turn_manifest(self.directory, claimed["claim"]["turn"]["input"])
        checkout = Path(manifest["path"])
        (checkout / "tracked.txt").write_text("accepted output\n", encoding="utf-8", newline="\n")
        seal = workspace_module.seal(self.directory, manifest, run_id, claimed["claim"]["attempt"]["attemptId"])
        self.finish_turn(board, claimed, seal)
        delivered = self.view(board, run_id)
        with defer_reclaim.object(board.store.workflow, "_reclaim_checkout", return_value={"removed": False, "reasons": ["fixture-retained"]}):
            accepted = board.call("workflow_accept", {
                "runId": run_id, "artifactId": delivered["finalArtifactId"],
                "note": "Reviewed the output", "notRequired": "No separate target in this fixture",
            })
        planned = board.store.workflow.cleanup_plan({
            "runId": run_id,
        })
        applied = board.store.workflow.cleanup_apply({
            "runId": run_id,
            "planId": planned["plan"]["planId"],
            "confirmPath": planned["plan"]["path"],
        })
        self.assertFalse(checkout.exists())
        repeated = board.store.workflow.cleanup_plan({'runId': run_id})
        replayed = board.store.workflow.cleanup_apply({'runId': run_id, 'planId': planned['plan']['planId'],
                                                      'confirmPath': planned['plan']['path']})
        self.assertEqual(repeated['plan'], applied['plan'])
        self.assertTrue(replayed['removed'])
        self.assertTrue(replayed['duplicate'])
        with board.store.db.read() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM commands WHERE kind IN"
                                               " ('workflow.cleanup_plan','workflow.cleanup_apply')").fetchone()[0], 0)

    def missing_allocation_plan(self, request_id):
        board = self.board()
        submitted = self.micro_task_submit(board, request_id=request_id, scope=(".",), kind="worktree")
        run_id = submitted["runId"]
        claimed = self.claim(board, run_id)
        manifest = turn_manifest(self.directory, claimed["claim"]["turn"]["input"])
        checkout = Path(manifest["path"])
        git_dir = Path(workspace_module.inspect(str(checkout))["gitDir"])
        (checkout / "tracked.txt").write_text("accepted output\n", encoding="utf-8", newline="\n")
        seal = workspace_module.seal(self.directory, manifest, run_id, claimed["claim"]["attempt"]["attemptId"])
        self.finish_turn(board, claimed, seal)
        delivered = self.view(board, run_id)
        with defer_reclaim.object(board.store.workflow, "_reclaim_checkout", return_value={"removed": False, "reasons": ["fixture-retained"]}):
            accepted = board.call("workflow_accept", {
                "runId": run_id, "artifactId": delivered["finalArtifactId"],
                "note": "Reviewed the output", "notRequired": "No separate target in this fixture",
            })
        planned = board.store.workflow.cleanup_plan({
            "runId": run_id,
        })
        with board.store.db.write() as connection:
            connection.execute("UPDATE workspace_cleanup_plans SET state='applying' WHERE plan_id=?",
                               (planned["plan"]["planId"],))
        shutil.rmtree(self.directory / "workspaces" / manifest["workspaceId"])
        return board, run_id, manifest, checkout, git_dir, planned, accepted

    def test_cleanup_apply_accepts_an_already_gone_allocation(self):
        board, run_id, manifest, checkout, git_dir, planned, accepted = self.missing_allocation_plan(
            "missing-allocation")
        repository = Path(manifest["snapshot"]["repositoryPath"])
        self.assertIsNotNone(workspace_module._worktree_record(repository, checkout))
        self.assertFalse((self.directory / "workspaces" / manifest["workspaceId"]).exists())
        foreign_ref = f"refs/hey-my-buddy/workspaces/{manifest['workspaceId']}/foreign"
        self.git("update-ref", foreign_ref, "HEAD")
        foreign_oid = self.git("rev-parse", foreign_ref).strip()
        applied = board.store.workflow.cleanup_apply({
            "runId": run_id,
            "planId": planned["plan"]["planId"],
            "confirmPath": planned["plan"]["path"],
        })
        self.assertTrue(applied["removed"])
        self.assertTrue(applied["plan"]["result"]["alreadyRemoved"])
        self.assertFalse(checkout.exists())
        self.assertFalse(git_dir.exists())
        self.assertIsNone(workspace_module._worktree_record(repository, checkout))
        self.assertEqual(self.git("rev-parse", foreign_ref).strip(), foreign_oid)
        with self.assertRaises(BoardError) as missing_view:
            board.call('workflow_get', {'runId': run_id})
        self.assertEqual(missing_view.exception.code, 'WORKSPACE_MANIFEST_CHANGED')
        # Reusing an applied journal cannot bypass a later deletion marker.
        with board.store.db.write() as connection:
            connection.execute(
                "INSERT INTO macro_task_deletions"
                " (deletion_id,target_kind,target_id)"
                " VALUES('gone-deletion','micro-task-tree',?)",
                ('run:' + run_id,),
            )
        operations = (
            lambda: board.store.workflow.cleanup_plan({
                'runId': run_id,
            }),
            lambda: board.store.workflow.cleanup_apply({
                'runId': run_id,
                'planId': planned['plan']['planId'],
                'confirmPath': planned['plan']['path'],
            }),
        )
        with defer_reclaim.object(workspace_module, "locate_repository",
                                  side_effect=AssertionError("Deletion fence must precede repository lookup")):
            for operation in operations:
                with self.assertRaises(BoardError) as caught:
                    operation()
                self.assertEqual(caught.exception.code,'TASK_DELETING')

    def test_missing_allocation_refuses_a_checkout_recreated_during_registration_proof(self):
        board, run_id, manifest, checkout, git_dir, planned, _accepted = self.missing_allocation_plan(
            "missing-allocation-race")
        original = workspace_module._cleanup_registration

        def recreate_checkout(repository, target, original_manifest=None, **kwargs):
            registration = original(repository, target, original_manifest, **kwargs)
            checkout.mkdir(parents=True)
            (checkout / "foreign.txt").write_text("new occupant\n", encoding="utf-8")
            return registration

        with patch.object(workspace_module, "_cleanup_registration", side_effect=recreate_checkout):
            with self.assertRaises(BoardError) as caught:
                board.store.workflow.cleanup_apply({
                    "runId": run_id, "planId": planned["plan"]["planId"],
                    "confirmPath": planned["plan"]["path"],
                })
        self.assertEqual(caught.exception.code, "WORKSPACE_UNSAFE")
        self.assertEqual((checkout / "foreign.txt").read_text(encoding="utf-8"), "new occupant\n")
        self.assertTrue(git_dir.exists())
        self.assertIsNotNone(workspace_module._worktree_record(
            Path(manifest["snapshot"]["repositoryPath"]), checkout))

    def test_missing_allocation_retries_after_a_partial_gitdir_removal(self):
        board, run_id, manifest, checkout, git_dir, planned, _accepted = self.missing_allocation_plan(
            "missing-allocation-partial")
        original = workspace_module.private_dirs.remove_tree

        def partial_remove(path, **kwargs):
            if Path(path) == git_dir:
                (git_dir / "locked").unlink()
                raise PermissionError("partial Git metadata removal")
            return original(path, **kwargs)

        with patch.object(workspace_module.private_dirs, "remove_tree", side_effect=partial_remove):
            with self.assertRaises(BoardError) as caught:
                board.store.workflow.cleanup_apply({
                    "runId": run_id, "planId": planned["plan"]["planId"],
                    "confirmPath": planned["plan"]["path"],
                })
        self.assertEqual(caught.exception.code, "WORKSPACE_IO_ERROR")
        self.assertEqual(workspace_module._worktree_record(
            Path(manifest["snapshot"]["repositoryPath"]), checkout)["locked"],
            "hey-my-buddy:" + manifest["workspaceId"])
        board = self.board()
        self.assertEqual(board.store.workflow.cleanup_plan({"runId": run_id})["plan"]["state"], "applying")
        applied = board.store.workflow.cleanup_apply({
            "runId": run_id, "planId": planned["plan"]["planId"],
            "confirmPath": planned["plan"]["path"],
        })
        self.assertTrue(applied["plan"]["result"]["alreadyRemoved"])
        self.assertFalse(git_dir.exists())

    def test_missing_allocation_refuses_a_foreign_git_lock(self):
        board, run_id, manifest, checkout, git_dir, planned, _accepted = self.missing_allocation_plan(
            "missing-allocation-lock")
        (git_dir / "locked").write_text("foreign-owner\n", encoding="utf-8")
        with self.assertRaises(BoardError) as caught:
            board.store.workflow.cleanup_apply({
                "runId": run_id, "planId": planned["plan"]["planId"],
                "confirmPath": planned["plan"]["path"],
            })
        self.assertEqual(caught.exception.code, "WORKSPACE_UNSAFE")
        self.assertTrue(git_dir.exists())
        self.assertFalse(checkout.exists())
        self.assertEqual(workspace_module._worktree_record(
            Path(manifest["snapshot"]["repositoryPath"]), checkout)["locked"], "foreign-owner")

    def test_missing_allocation_requires_an_applying_plan(self):
        board, run_id, manifest, checkout, git_dir, planned, _accepted = self.missing_allocation_plan(
            "missing-allocation-unstarted")
        with board.store.db.write() as connection:
            connection.execute("UPDATE workspace_cleanup_plans SET state='planned' WHERE plan_id=?",
                               (planned["plan"]["planId"],))
        with self.assertRaises(BoardError) as caught:
            board.store.workflow.cleanup_apply({
                "runId": run_id, "planId": planned["plan"]["planId"],
                "confirmPath": planned["plan"]["path"],
            })
        self.assertEqual(caught.exception.code, "WORKSPACE_MANIFEST_CHANGED")
        self.assertTrue(git_dir.exists())

    def test_missing_allocation_refuses_changed_sql_owner_proof(self):
        board, run_id, manifest, checkout, git_dir, planned, _accepted = self.missing_allocation_plan(
            "missing-allocation-owner")
        with board.store.db.write() as connection:
            row = connection.execute("SELECT evidence_json FROM workspace_cleanup_plans WHERE plan_id=?",
                                     (planned["plan"]["planId"],)).fetchone()
            evidence = json.loads(row["evidence_json"])
            evidence["workspace"]["allocation"]["manifestSha256"] = "0" * 64
            connection.execute("UPDATE workspace_cleanup_plans SET evidence_json=? WHERE plan_id=?",
                               (json.dumps(evidence), planned["plan"]["planId"]))
        with self.assertRaises(BoardError) as caught:
            board.store.workflow.cleanup_apply({
                "runId": run_id, "planId": planned["plan"]["planId"],
                "confirmPath": planned["plan"]["path"],
            })
        self.assertEqual(caught.exception.code, "CONFLICT")
        self.assertTrue(git_dir.exists())

    def test_missing_allocation_refuses_a_foreign_repository(self):
        board, run_id, manifest, checkout, git_dir, planned, _accepted = self.missing_allocation_plan(
            "missing-allocation-repository")
        foreign = self.directory / "foreign-repo"
        foreign.mkdir()
        subprocess.run(["git", "init", "-q", str(foreign)], check=True, capture_output=True)
        with self.assertRaises(BoardError) as caught:
            board.store.workflow.cleanup_apply({
                "runId": run_id, "planId": planned["plan"]["planId"],
                "confirmPath": planned["plan"]["path"],
            }, repository=str(foreign / ".git"))
        self.assertEqual(caught.exception.code, "WORKSPACE_UNSAFE")
        self.assertTrue(git_dir.exists())

    @unittest.skipUnless(os.name == 'nt', 'native Windows dangling junction')
    def test_missing_allocation_refuses_a_dangling_junction_at_the_allocation_path(self):
        board, run_id, manifest, checkout, git_dir, planned, _accepted = self.missing_allocation_plan(
            'missing-allocation-junction')
        target = self.directory / 'junction-target'
        target.mkdir()
        linked = subprocess.run(['cmd.exe', '/d', '/c', 'mklink', '/J', str(checkout.parent), str(target)],
                                capture_output=True, timeout=10)
        self.assertEqual(linked.returncode, 0, (linked.stdout, linked.stderr))
        target.rmdir()
        self.assertTrue(checkout.parent.is_junction())
        try:
            with self.assertRaises(BoardError) as caught:
                board.store.workflow.cleanup_apply({
                    'runId': run_id, 'planId': planned['plan']['planId'],
                    'confirmPath': planned['plan']['path'],
                })
            # The manifest boundary rejects the linked private directory before
            # any workspace-layer check runs; the first boundary owns the code.
            self.assertEqual(caught.exception.code, 'PRIVATE_PATH_UNSAFE')
            self.assertTrue(checkout.parent.is_junction())
            self.assertTrue(git_dir.exists())
        finally:
            checkout.parent.rmdir()
