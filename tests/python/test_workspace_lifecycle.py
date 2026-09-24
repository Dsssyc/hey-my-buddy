"""Real-Git integration receipts and workspace cleanup over real temp checkouts.

These tests exercise the Host-owned lifecycle operations against real Git
repositories, real isolated worktrees and the real governed transactions: an
accepted goal must hold a verified integration record (or an explicit
not-required decision) for its final artifact, and a disposable checkout is only
removed after acceptance, integration, confirmed shutdown and an idempotent,
expiring plan that names the exact path. No test touches an existing workspace.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import unittest
from pathlib import Path
from unittest import mock

from support import FakeClock
from test_workflow_real import CONFIGURATION, GIT_ENV, RealWorkspaceTestCase

from buddy import workflow as workflow_module
from buddy import workspace as workspace_module
from buddy.db import canonical_json
from buddy.errors import BoardError


def git(path, *arguments: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(path), *arguments], env={**os.environ, **GIT_ENV}, capture_output=True, text=True,
    )
    if completed.returncode != 0:
        raise AssertionError(f"git {' '.join(arguments)} failed: {completed.stderr}")
    return completed.stdout


class LifecycleTestCase(RealWorkspaceTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.controls: dict[str, dict] = {}
        self.worker = "w-life"
        self._previous_workspace = workflow_module._workspace_module
        workflow_module._workspace_module = workspace_module
        self.addCleanup(self._restore_workspace)

    def _restore_workspace(self) -> None:
        workflow_module._workspace_module = self._previous_workspace

    # -- governed helpers ----------------------------------------------------
    def register(self, board, worker_id=None):
        return board.call("worker_register", {"workerId": worker_id or self.worker, "capabilities": ["dsh"]})

    def control(self, view):
        return dict(self.controls[view["runId"]])

    def claim(self, board, run_id, *, request_id="claim-1", nonce="n" * 16):
        return board.call("worker_claim", {
            "workerId": self.worker, "claimRequestId": request_id, "nonce": nonce, "runId": run_id,
        })

    def turn_record(self, claim, *, disposition="completed"):
        attempt, turn = claim["attempt"], claim["turn"]
        outcome = {"disposition": disposition, "summary": "did the work", "remaining": [], "decisions": [],
                   "artifacts": [], "request": None}
        if disposition in ("assistance", "attention"):
            outcome["request"] = {"summary": "please review", "attempted": "tried the obvious fix",
                                  "neededWork": ["decide the schema"], "expectedArtifacts": ["a reviewed design"],
                                  "acceptance": "the design is approved"}
        return {
            "version": 1, "taskId": attempt["taskId"], "attemptId": attempt["attemptId"],
            "generation": attempt["generation"], "turnId": turn["turnId"], "resumeMode": turn["resumeMode"],
            "previousSessionId": turn["input"].get("previousSessionId"), "sessionId": f"sess-{turn['turnId'][:8]}",
            "promptSha256": "a" * 64, "inputSha256": turn["inputSha256"],
            "outcome": outcome,
            "provenance": {"tool": "buddy_finish_turn", "turnEnd": "completed", "flush": "awaited",
                           "rootSessionMatched": True},
        }

    def finish_turn(self, board, claimed, seal, *, disposition="completed", nonce="n" * 16, worker_id=None):
        claim = claimed["claim"]
        attempt = claim["attempt"]
        return board.call("worker_result", {
            "workerId": worker_id or self.worker, "attemptId": attempt["attemptId"], "generation": attempt["generation"],
            "nonce": nonce, "status": "ok",
            "result": {"status": "ok", "processState": {"shutdownConfirmed": True},
                       "turn": self.turn_record(claim, disposition=disposition),
                       "turnResultPath": "/tmp/turn-result.json", "workspaceSeal": seal},
            "shutdownConfirmed": True, "exitCode": 0,
        })

    def view(self, board, run_id, **extra):
        return board.call("workflow_get", {"runId": run_id, **extra})

    def run_worktree(self, board, *, request_id="lifecycle-1", output="sealed output\n", disposition="completed"):
        submitted = board.call("workflow_submit", {
            **CONFIGURATION, "requestId": request_id, "hostId": "host-1", "task": "isolated worktree task",
            "cwd": str(self.repo), "submissionToken": "t" * 32,
            "executionWorkspace": {"kind": "worktree", "access": "write", "writeScope": ["."]},
        })
        self.controls[submitted["runId"]] = submitted["control"]
        claimed = self.claim(board, submitted["runId"], request_id=f"{request_id}-claim")
        manifest = claimed["claim"]["turn"]["input"]["executionWorkspace"]
        checkout = Path(manifest["path"])
        (checkout / "tracked.txt").write_text(output)
        seal = workspace_module.seal(self.directory, manifest, submitted["runId"],
                                     claimed["claim"]["attempt"]["attemptId"])
        self.finish_turn(board, claimed, seal, disposition=disposition)
        view = self.view(board, submitted["runId"])
        artifact = next(row for row in view["artifacts"] if row["kind"] == "output")
        return submitted, claimed, manifest, checkout, seal, view, artifact

    def integrate(self, board, view, artifact, *, not_required=False, target=None, reason=None,
                  command_id="integration-1", **extra):
        params = {
            "runId": view["runId"], "commandId": command_id, "expectedRevision": view["revision"],
            "artifactId": artifact["artifactId"], **self.control(view), **extra,
        }
        if not_required:
            params.update(notRequired=True,
                          reason="the artifact needs no repository target" if reason is None else reason) 
        else:
            params.update(strategy=extra.pop("strategy", "patch"), beforeCommit=target["before"],
                          target={"path": str(target["path"]), "ref": "HEAD"}, reason=reason)
        return board.store.workflow.integration_record(params)

    def target_with_artifact(self, artifact, *, name="target"):
        target = self.directory / name
        git(self.directory, "clone", "-q", str(self.repo), str(target))
        before = git(target, "rev-parse", "HEAD").strip()
        completed = subprocess.run(
            ["git", "-C", str(target), "apply", "--binary", artifact["diffPath"]],
            env={**os.environ, **GIT_ENV}, capture_output=True, text=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        git(target, "add", ".")
        git(target, "commit", "-qm", "integrate")
        after = git(target, "rev-parse", "HEAD").strip()
        return {"path": target, "before": before, "after": after}

    def acknowledge(self, board, view, artifact, *, verdict="accepted", **extra):
        return board.call("workflow_acknowledge", {
            "runId": view["runId"], "artifactId": artifact["artifactId"], "note": "inspected the sealed output",
            "verdict": verdict, **self.control(view), **extra,
        })

    def plan(self, board, view, *, command_id="clean-1"):
        return board.store.workflow.cleanup_plan({
            "runId": view["runId"], "commandId": command_id, "expectedRevision": view["revision"],
            **self.control(view),
        })

    def apply(self, board, view, plan, *, command_id="clean-apply-1", confirm_path=None):
        return board.store.workflow.cleanup_apply({
            "runId": view["runId"], "planId": plan["planId"], "commandId": command_id,
            "expectedRevision": view["revision"], "confirmPath": confirm_path or plan["path"],
            **self.control(view),
        })

    # -- acceptance requires integration evidence ----------------------------
    def test_acceptance_requires_a_verified_or_not_required_record(self):
        board = self.board()
        self.register(board)
        submitted, claimed, manifest, checkout, seal, view, artifact = self.run_worktree(board)
        with self.assertRaises(BoardError) as raised:
            self.acknowledge(board, view, artifact)
        self.assertEqual(raised.exception.code, "INTEGRATION_REQUIRED")
        self.assertEqual(raised.exception.details["artifactId"], artifact["artifactId"])
        with self.assertRaises(BoardError) as raised:
            self.integrate(board, view, artifact, not_required=True, reason="")
        self.assertEqual(raised.exception.code, "INVALID_ARGUMENT")
        with self.assertRaises(BoardError) as raised:
            board.store.workflow.integration_record({
                "runId": view["runId"], "commandId": "integration-bad-target",
                "expectedRevision": view["revision"], "artifactId": artifact["artifactId"],
                "notRequired": True, "reason": "no", "target": {"path": str(self.repo), "ref": "HEAD"},
                **self.control(view),
            })
        self.assertEqual(raised.exception.code, "INVALID_ARGUMENT")
        recorded = self.integrate(board, view, artifact, not_required=True, reason="no separate target")
        self.assertFalse(recorded["duplicate"])
        self.assertEqual(recorded["integration"]["state"], "not-required")
        self.assertIsNone(recorded["integration"]["target"])
        duplicate = self.integrate(board, recorded, artifact, not_required=True, reason="no separate target",
                                   command_id="integration-2")
        self.assertTrue(duplicate["duplicate"])
        accepted = self.acknowledge(board, duplicate, artifact)
        self.assertEqual(accepted["state"], "accepted")
        self.assertEqual(accepted["integration"]["state"], "not-required")
        self.assertEqual(accepted["integration"]["artifactId"], artifact["artifactId"])

    def test_a_stale_output_record_never_accepts_a_newer_final_artifact(self):
        board = self.board()
        self.register(board)
        submitted, first_claim, manifest, checkout, first_seal, yielded, first_artifact = self.run_worktree(
            board, request_id="two-turns-1", output="first output\n", disposition="assistance"
        )
        run_id = submitted["runId"]
        self.assertEqual(yielded["state"], "awaiting-host")
        # The Host continues; a second turn seals the new final output.
        continued = board.call("workflow_continue", {
            "runId": run_id, "commandId": "continue-two", "expectedRevision": yielded["revision"],
            "input": "finish the goal", **self.control(yielded),
        })
        self.assertEqual(continued["state"], "executing")
        second = self.claim(board, run_id, request_id="two-turns-claim-2", nonce="q" * 16)
        self.assertIsNotNone(second["claim"], second)
        second_manifest = second["claim"]["turn"]["input"]["executionWorkspace"]
        (checkout / "tracked.txt").write_text("second output\n")
        second_seal = workspace_module.seal(self.directory, second_manifest, run_id,
                                            second["claim"]["attempt"]["attemptId"])
        self.finish_turn(board, second, second_seal, nonce="q" * 16)
        delivered = self.view(board, run_id)
        self.assertEqual(delivered["state"], "delivered")
        outputs = [row for row in delivered["artifacts"] if row["kind"] == "output"]
        self.assertEqual(len(outputs), 2)
        first_output, final_output = outputs[-1], outputs[0]
        # A record for the older output cannot stand in for the final artifact.
        self.integrate(board, delivered, first_output, not_required=True, command_id="integration-old",
                       reason="older output")
        with self.assertRaises(BoardError) as raised:
            self.acknowledge(board, delivered, final_output)
        self.assertEqual(raised.exception.code, "INTEGRATION_REQUIRED")
        current = self.view(board, run_id)
        self.integrate(board, current, final_output, not_required=True, command_id="integration-final",
                       reason="final output")
        accepted = self.acknowledge(board, current, final_output)
        self.assertEqual(accepted["state"], "accepted")
        self.assertEqual(accepted["finalArtifactId"], final_output["artifactId"])

    def test_verified_integration_binds_the_actual_target_checkout(self):
        board = self.board()
        self.register(board)
        submitted, claimed, manifest, checkout, seal, view, artifact = self.run_worktree(board)
        target = self.target_with_artifact(artifact)
        recorded = self.integrate(board, view, artifact, target=target, reason="merged the sealed patch")
        integration = recorded["integration"]
        self.assertEqual(integration["state"], "verified")
        self.assertEqual(integration["target"]["path"], str(Path(target["path"]).resolve()))
        self.assertEqual(integration["target"]["repositoryId"], workspace_module.inspect(str(target["path"]))["repositoryId"])
        self.assertEqual(integration["beforeCommit"], target["before"])
        self.assertEqual(integration["afterCommit"], target["after"])
        self.assertEqual(integration["beforeTree"], git(target["path"], "rev-parse", target["before"] + "^{tree}").strip())
        self.assertEqual(integration["afterTree"], git(target["path"], "rev-parse", target["after"] + "^{tree}").strip())
        self.assertEqual(integration["sourceCommit"], artifact["outputCommit"])
        self.assertEqual(integration["verification"]["matchingPaths"], ["tracked.txt"])
        self.assertEqual(integration["verification"]["missingPaths"], [])
        accepted = self.acknowledge(board, recorded, artifact)
        self.assertEqual(accepted["state"], "accepted")
        self.assertEqual(accepted["integration"]["state"], "verified")
        # A target that never received the artifact is never recorded as verified.
        current = self.view(board, recorded["runId"])
        blank = self.directory / "blank"
        git(self.directory, "clone", "-q", str(self.repo), str(blank))
        blank_before = git(blank, "rev-parse", "HEAD").strip()
        with self.assertRaises(BoardError) as raised:
            board.store.workflow.integration_record({
                "runId": current["runId"], "commandId": "integration-blank", "expectedRevision": current["revision"],
                "artifactId": artifact["artifactId"], "strategy": "patch", "beforeCommit": blank_before,
                "target": {"path": str(blank), "ref": "HEAD"}, **self.control(current),
            })
        self.assertEqual(raised.exception.code, "INTEGRATION_UNVERIFIED")
        self.assertIn("tracked.txt", [item["path"] for item in raised.exception.details["verification"]["differingPaths"]])
        with self.assertRaises(BoardError) as raised:
            board.store.workflow.integration_record({
                "runId": current["runId"], "commandId": "integration-wrong-repo",
                "expectedRevision": current["revision"], "artifactId": artifact["artifactId"], "strategy": "patch",
                "beforeCommit": target["before"],
                "target": {"path": str(target["path"]), "ref": "HEAD", "repositoryId": "0" * 64},
                **self.control(current),
            })
        self.assertEqual(raised.exception.code, "WORKSPACE_CHANGED")
        with self.assertRaises(BoardError) as raised:
            board.store.workflow.integration_record({
                "runId": current["runId"], "commandId": "integration-missing-artifact",
                "expectedRevision": current["revision"], "artifactId": "art-not-real", "notRequired": True,
                "reason": "unknown", **self.control(current),
            })
        self.assertEqual(raised.exception.code, "NOT_FOUND")

    # -- cleanup -------------------------------------------------------------
    def accepted_worktree(self, *, request_id="cleanup-1"):
        board = self.board()
        self.register(board)
        submitted, claimed, manifest, checkout, seal, view, artifact = self.run_worktree(board, request_id=request_id)
        target = self.target_with_artifact(artifact)
        self.integrate(board, view, artifact, target=target, reason="verified before cleanup")
        accepted = self.acknowledge(board, view, artifact)
        self.assertEqual(accepted["state"], "accepted")
        return board, submitted, manifest, checkout, artifact, self.view(board, submitted["runId"])

    # -- continuation cleanup -------------------------------------------------
    def two_turn_worktree(self, *, request_id="continuation-cleanup"):
        """One managed worktree whose second stage is prepared from the first seal.

        The continuation reuses the same physical checkout under a new logical
        workspace id, so the current run manifest is no longer the worktree
        manifest that created the allocation.
        """
        board = self.board()
        self.register(board)
        submitted, first_claim, first_manifest, checkout, first_seal, yielded, first_artifact = self.run_worktree(
            board, request_id=request_id, output="first turn output\n", disposition="assistance"
        )
        run_id = submitted["runId"]
        self.assertEqual(yielded["state"], "awaiting-host")
        continued = board.call("workflow_continue", {
            "runId": run_id, "commandId": f"{request_id}-continue", "expectedRevision": yielded["revision"],
            "input": "finish the goal", **self.control(yielded),
        })
        self.assertEqual(continued["state"], "executing")
        second = self.claim(board, run_id, request_id=f"{request_id}-claim-2", nonce="q" * 16)
        self.assertIsNotNone(second["claim"], second)
        second_manifest = second["claim"]["turn"]["input"]["executionWorkspace"]
        self.assertEqual(second_manifest["kind"], "existing")
        self.assertNotEqual(second_manifest["workspaceId"], first_manifest["workspaceId"])
        for field in ("checkoutRoot", "checkoutId", "repositoryId", "path"):
            self.assertEqual(second_manifest[field], first_manifest[field])
        # A new-file second stage keeps the sealed patch applicable to a fresh target.
        (checkout / "second.txt").write_text("second turn output\n")
        second_seal = workspace_module.seal(self.directory, second_manifest, run_id,
                                            second["claim"]["attempt"]["attemptId"])
        self.finish_turn(board, second, second_seal, nonce="q" * 16)
        delivered = self.view(board, run_id)
        self.assertEqual(delivered["state"], "delivered")
        final = next(row for row in delivered["artifacts"] if row["kind"] == "output")
        target = self.target_with_artifact(final)
        self.integrate(board, delivered, final, target=target, command_id=f"{request_id}-integration",
                       reason="verified the sealed second stage")
        accepted = self.acknowledge(board, delivered, final)
        self.assertEqual(accepted["state"], "accepted")
        return board, submitted, first_manifest, second_manifest, final, self.view(board, run_id)

    def test_cleanup_removes_only_the_registered_worktree_and_is_idempotent(self):
        board, submitted, manifest, checkout, artifact, view = self.accepted_worktree()
        run_id = submitted["runId"]
        planned = self.plan(board, view)
        plan = planned["plan"]
        self.assertEqual(plan["state"], "planned")
        self.assertTrue(plan["eligible"])
        self.assertEqual(plan["path"], manifest["checkoutRoot"])
        self.assertEqual(plan["workspaceId"], manifest["workspaceId"])
        self.assertEqual(plan["checkoutId"], manifest["checkoutId"])
        self.assertEqual(plan["kind"], "worktree")
        self.assertTrue(plan["retention"]["artifactIds"])
        self.assertIn(str(Path(self.directory) / "workspaces" / manifest["workspaceId"]),
                      plan["retention"]["workspaceDirectory"])
        # A different confirmPath is never a deletion authorization.
        with self.assertRaises(BoardError) as raised:
            self.apply(board, planned, plan, confirm_path=str(self.repo))
        self.assertEqual(raised.exception.code, "INVALID_ARGUMENT")
        self.assertTrue(checkout.exists())
        # Repeated planning returns the same live plan instead of a second one.
        again = self.plan(board, planned, command_id="clean-2")
        self.assertTrue(again["duplicate"])
        self.assertEqual(again["plan"]["planId"], plan["planId"])
        applied = self.apply(board, again, plan)
        self.assertTrue(applied["removed"])
        self.assertEqual(applied["plan"]["state"], "applied")
        self.assertFalse(checkout.exists())
        # Only the checkout was removed: manifests, patches and Git refs stay readable.
        workspace_dir = Path(self.directory) / "workspaces" / manifest["workspaceId"]
        self.assertTrue((workspace_dir / "manifest.json").exists())
        self.assertTrue(Path(artifact["diffPath"]).exists())
        refs = git(self.repo, "for-each-ref", "--format=%(refname)", f"refs/buddy/workspaces/{manifest['workspaceId']}/")
        self.assertIn("outputs", refs)
        still = self.view(board, run_id)
        self.assertEqual(still["cleanup"]["state"], "applied")
        repeat = self.apply(board, still, plan, command_id="clean-apply-2")
        self.assertTrue(repeat["duplicate"])
        self.assertEqual(repeat["plan"]["planId"], plan["planId"])

    def test_delivered_output_without_integration_yields_a_blocked_cleanup_plan(self):
        board = self.board()
        self.register(board)
        _submitted, _claim, _manifest, checkout, _seal, view, _artifact = self.run_worktree(board)
        planned = self.plan(board, view)
        self.assertEqual(planned["plan"]["state"], "blocked")
        self.assertIn("not-accepted", planned["plan"]["reasons"])
        self.assertIn("integration-missing", planned["plan"]["reasons"])
        self.assertTrue(checkout.exists())

    def test_cleanup_is_blocked_by_unsealed_changes(self):
        board, submitted, manifest, checkout, artifact, view = self.accepted_worktree(request_id="cleanup-dirty")
        (checkout / "tracked.txt").write_text("edited after the seal\n")
        planned = self.plan(board, view)
        self.assertEqual(planned["plan"]["state"], "blocked")
        self.assertIn("unsealed-changes", planned["plan"]["reasons"])
        self.assertEqual(planned["plan"]["evidence"]["workspace"]["unsealedPaths"], ["tracked.txt"])
        with self.assertRaises(BoardError) as raised:
            self.apply(board, planned, planned["plan"])
        self.assertEqual(raised.exception.code, "NOT_READY")
        self.assertIn("unsealed-changes", raised.exception.details["reasons"])
        self.assertTrue(checkout.exists())
        # Returning the site to the sealed state makes the same workspace eligible.
        (checkout / "tracked.txt").write_text("sealed output\n")
        recovered = self.plan(board, planned, command_id="clean-recovered")
        self.assertEqual(recovered["plan"]["state"], "planned")
        self.assertTrue(recovered["plan"]["eligible"])

    def test_cleanup_never_targets_a_source_checkout_or_foreign_path(self):
        board, submitted, manifest, checkout, artifact, view = self.accepted_worktree(request_id="cleanup-foreign")
        # An existing (non-disposable) workspace is never a cleanup target.
        board_existing = self.board()
        self.register(board_existing, worker_id="w-existing")
        existing = board_existing.call("workflow_submit", {
            **CONFIGURATION, "requestId": "existing-run", "hostId": "host-1", "task": "source checkout task",
            "cwd": str(self.repo), "submissionToken": "t" * 32,
            "executionWorkspace": {"kind": "existing", "access": "read", "writeScope": []},
        })
        self.controls[existing["runId"]] = existing["control"]
        existing_claim = board_existing.call("worker_claim", {
            "workerId": "w-existing", "claimRequestId": "existing-claim", "nonce": "e" * 16,
            "runId": existing["runId"],
        })
        existing_manifest = existing_claim["claim"]["turn"]["input"]["executionWorkspace"]
        existing_seal = workspace_module.seal(self.directory, existing_manifest, existing["runId"],
                                              existing_claim["claim"]["attempt"]["attemptId"])
        self.finish_turn(board_existing, existing_claim, existing_seal, nonce="e" * 16, worker_id="w-existing")
        existing_view = self.view(board_existing, existing["runId"])
        existing_artifact = next(row for row in existing_view["artifacts"] if row["kind"] == "output")
        self.integrate(board_existing, existing_view, existing_artifact, not_required=True,
                       command_id="existing-integration", reason="read-only source checkout")
        self.acknowledge(board_existing, existing_view, existing_artifact)
        existing_view = self.view(board_existing, existing["runId"])
        planned = self.plan(board_existing, existing_view, command_id="clean-existing")
        self.assertEqual(planned["plan"]["state"], "blocked")
        self.assertIn("not-a-managed-worktree", planned["plan"]["reasons"])
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
        blocked = self.plan(board, foreign, command_id="clean-foreign")
        self.assertEqual(blocked["plan"]["state"], "blocked")
        self.assertIn("unsafe-path", blocked["plan"]["reasons"])
        with self.assertRaises(BoardError) as raised:
            self.apply(board, blocked, blocked["plan"], command_id="clean-foreign-apply")
        self.assertEqual(raised.exception.code, "NOT_READY")
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
        planned = self.plan(board, view)
        applied = self.apply(board, planned, planned["plan"])
        self.assertTrue(applied["removed"])
        self.assertFalse(checkout.exists())
        self.assertIn(foreign_path, git(self.repo, "worktree", "list", "--porcelain"))
        # The repository owner can still repair its own stale entry afterwards.
        git(self.repo, "worktree", "prune")
        self.assertNotIn(foreign_path, git(self.repo, "worktree", "list", "--porcelain"))

    def test_a_new_writer_cannot_claim_the_checkout_across_the_remove_boundary(self):
        board, submitted, manifest, checkout, artifact, view = self.accepted_worktree(
            request_id="cleanup-fence")
        planned = self.plan(board, view)
        plan = planned["plan"]
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
            applied = self.apply(board, planned, plan)
        self.assertTrue(applied["removed"])
        for attempts, label in ((early, "source capture"), (claims, "reservation admission")):
            self.assertEqual(len(attempts), 1)
            self.assertIsNotNone(attempts[0], f"a new writer claimed the checkout through {label}")
            self.assertEqual(attempts[0].code, "PREPARATION_CONFLICT")
            self.assertEqual(attempts[0].details["planId"], plan["planId"])
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
        planned = self.plan(board, view)
        plan = planned["plan"]
        self.assertEqual(plan["state"], "planned", plan["reasons"])
        self.assertTrue(plan["eligible"])
        # The plan names the physical allocation that created the worktree, not the
        # later turn's logical workspace id on the same checkout.
        self.assertEqual(plan["workspaceId"], first["workspaceId"])
        self.assertNotEqual(plan["workspaceId"], second["workspaceId"])
        self.assertEqual(plan["kind"], "worktree")
        self.assertEqual(plan["path"], first["checkoutRoot"])
        self.assertEqual(plan["checkoutId"], first["checkoutId"])
        self.assertEqual(plan["evidence"]["workspace"]["manifestWorkspaceId"], second["workspaceId"])
        self.assertTrue(plan["retention"]["artifactIds"])
        self.assertTrue(any("outputs" in ref for ref in plan["retention"]["fixedRefs"]))
        applied = self.apply(board, planned, plan)
        self.assertTrue(applied["removed"])
        self.assertEqual(applied["plan"]["state"], "applied")
        self.assertFalse(checkout.exists())
        # Only the checkout is removed: both turns' manifests, patches and refs stay.
        self.assertTrue((Path(self.directory) / "workspaces" / first["workspaceId"] / "manifest.json").exists())
        self.assertTrue((Path(self.directory) / "workspaces" / second["workspaceId"] / "manifest.json").exists())
        self.assertTrue(Path(final["diffPath"]).exists())
        refs = git(self.repo, "for-each-ref", "--format=%(refname)")
        self.assertIn(f"refs/buddy/workspaces/{first['workspaceId']}/", refs)
        self.assertIn(f"refs/buddy/workspaces/{second['workspaceId']}/", refs)
        # The applied plan stays the one authorization; repeating it is a replay.
        repeat_view = self.view(board, run_id)
        self.assertEqual(repeat_view["cleanup"]["state"], "applied")
        repeat = self.apply(board, repeat_view, plan, command_id="clean-continuation-apply-2")
        self.assertTrue(repeat["duplicate"])
        self.assertEqual(repeat["plan"]["planId"], plan["planId"])

    def test_continuation_cleanup_blocks_a_change_after_the_latest_seal(self):
        board, submitted, first, second, final, view = self.two_turn_worktree(request_id="continuation-dirty")
        checkout = Path(second["checkoutRoot"])
        (checkout / "tracked.txt").write_text("edited after the second seal\n")
        planned = self.plan(board, view)
        self.assertEqual(planned["plan"]["state"], "blocked")
        self.assertIn("unsealed-changes", planned["plan"]["reasons"])
        # The delta is against the latest prepared input: the second stage's own
        # file is sealed, only the later edit is named.
        self.assertEqual(planned["plan"]["evidence"]["workspace"]["unsealedPaths"], ["tracked.txt"])
        with self.assertRaises(BoardError) as raised:
            self.apply(board, planned, planned["plan"], command_id="clean-continuation-dirty-apply")
        self.assertEqual(raised.exception.code, "NOT_READY")
        self.assertTrue(checkout.exists())
        # Returning the site to the latest sealed state makes it eligible again.
        (checkout / "tracked.txt").write_text("first turn output\n")
        recovered = self.plan(board, planned, command_id="clean-continuation-recovered")
        self.assertEqual(recovered["plan"]["state"], "planned", recovered["plan"]["reasons"])

    def test_a_borrowed_run_never_authorizes_its_own_cleanup(self):
        board, submitted, first, second, final, view = self.two_turn_worktree(request_id="borrow-owner")
        checkout = Path(first["checkoutRoot"])
        self.register(board, worker_id="w-borrow")
        borrowed = board.call("workflow_submit", {
            **CONFIGURATION, "requestId": "borrowed-run", "hostId": "host-1", "task": "borrowed checkout task",
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
        blocked = self.plan(board, owner_view, command_id="clean-owner-blocked")
        self.assertEqual(blocked["plan"]["state"], "blocked")
        self.assertIn("workspace-dependency", blocked["plan"]["reasons"])
        # The borrowed run has no worktree manifest of its own: it can never delete it.
        borrowed_view = self.view(board, borrowed["runId"])
        planned = self.plan(board, borrowed_view, command_id="clean-borrower")
        self.assertEqual(planned["plan"]["state"], "blocked")
        self.assertIn("not-a-managed-worktree", planned["plan"]["reasons"])
        with self.assertRaises(BoardError) as raised:
            self.apply(board, planned, planned["plan"], command_id="clean-borrower-apply")
        self.assertEqual(raised.exception.code, "NOT_READY")
        with self.assertRaises(BoardError) as raised:
            workspace_module.cleanup_remove(self.directory, borrowed_manifest)
        self.assertEqual(raised.exception.code, "WORKSPACE_UNSAFE")
        self.assertTrue(checkout.exists())
        # Once the borrower stops holding the checkout, the owner may clean it.
        board.call("workflow_cancel", {
            "runId": borrowed["runId"], "commandId": "borrowed-cancel",
            **self.control(self.view(board, borrowed["runId"])),
        })
        released = self.plan(board, self.view(board, submitted["runId"]), command_id="clean-owner-released")
        self.assertEqual(released["plan"]["state"], "planned", released["plan"]["reasons"])
        applied = self.apply(board, released, released["plan"], command_id="clean-owner-released-apply")
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
            "helpers": [{**CONFIGURATION, "requestId": "borrow-helper", "task": "helper on the parent checkout",
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
        helper_view = self.view(board, helper_id)
        planned = board.store.workflow.cleanup_plan({
            "runId": helper_id, "commandId": "clean-helper", "expectedRevision": helper_view["revision"],
        }, console_authority={"sessionId": "console-test"})
        self.assertEqual(planned["plan"]["state"], "blocked")
        self.assertIn("not-a-managed-worktree", planned["plan"]["reasons"])
        with self.assertRaises(BoardError) as raised:
            workspace_module.cleanup_remove(self.directory, helper_manifest)
        self.assertEqual(raised.exception.code, "WORKSPACE_UNSAFE")
        # The owner's own allocation cannot be removed while the helper holds it.
        owner_view = self.view(board, run_id)
        blocked = self.plan(board, owner_view, command_id="clean-helper-owner")
        self.assertEqual(blocked["plan"]["state"], "blocked")
        self.assertIn("workspace-dependency", blocked["plan"]["reasons"])
        self.assertTrue(checkout.exists())

    # -- Host control over an independently allocated helper ------------------
    def root_with_helper_worktree(self, *, request_id="helper-target"):
        """A root worktree run plus a helper that owns a separate isolated worktree.

        Only the root control capability is kept, exactly like the real Host: the
        helper's own control verifier is never issued to any caller.
        """
        board = self.board()
        self.register(board)
        submitted, first_claim, manifest, checkout, seal, yielded, artifact = self.run_worktree(
            board, request_id=request_id, output="root output\n", disposition="assistance"
        )
        run_id = submitted["runId"]
        decided = board.call("workflow_decide", {
            "runId": run_id, "requestId": yielded["activeRequest"]["requestId"],
            "commandId": f"{request_id}-decide", "expectedRevision": yielded["revision"], "decision": "approve",
            "helpers": [{**CONFIGURATION, "requestId": f"{request_id}-helper",
                         "task": "helper owns its own worktree", "cwd": str(self.repo),
                         "executionWorkspace": {"kind": "worktree", "access": "write", "writeScope": ["."]}}],
            **self.control(yielded),
        })
        self.assertEqual(decided["decision"], "approve")
        helper_id = decided["children"][0]["taskId"]
        with board.store.db.read() as connection:
            helper_manifest = json.loads(connection.execute(
                "SELECT workspace_manifest_json FROM workflow_children WHERE child_task_id=?", (helper_id,)
            ).fetchone()["workspace_manifest_json"])
        self.assertEqual(helper_manifest["kind"], "worktree")
        self.assertNotEqual(helper_manifest["checkoutId"], manifest["checkoutId"])
        helper_claim = self.claim(board, helper_id, request_id=f"{request_id}-helper-claim", nonce="h" * 16)
        self.assertIsNotNone(helper_claim["claim"], helper_claim)
        helper_turn_manifest = helper_claim["claim"]["turn"]["input"]["executionWorkspace"]
        helper_checkout = Path(helper_turn_manifest["path"])
        (helper_checkout / "helper.txt").write_text("helper output\n")
        helper_seal = workspace_module.seal(self.directory, helper_turn_manifest, helper_id,
                                            helper_claim["claim"]["attempt"]["attemptId"])
        self.finish_turn(board, helper_claim, helper_seal, nonce="h" * 16)
        helper_view = self.view(board, helper_id)
        self.assertEqual(helper_view["state"], "delivered")
        helper_artifact = next(row for row in helper_view["artifacts"] if row["kind"] == "output")
        return {
            "board": board, "runId": run_id, "rootView": self.view(board, run_id), "manifest": manifest,
            "checkout": checkout, "helperId": helper_id, "helperView": helper_view,
            "helperManifest": helper_manifest, "helperCheckout": helper_checkout, "artifact": helper_artifact,
        }

    def test_root_control_targets_an_independently_allocated_helper(self):
        flow = self.root_with_helper_worktree()
        board, run_id, helper_id = flow["board"], flow["runId"], flow["helperId"]
        root_view, artifact = flow["rootView"], flow["artifact"]
        target = self.target_with_artifact(artifact)
        recorded = board.store.workflow.integration_record({
            "runId": run_id, "targetRunId": helper_id, "commandId": "helper-integration",
            "expectedRevision": root_view["revision"], "artifactId": artifact["artifactId"],
            "strategy": "patch", "beforeCommit": target["before"],
            "target": {"path": str(target["path"]), "ref": "HEAD"},
            "reason": "verified the helper worktree output", **self.control(root_view),
        })
        self.assertEqual(recorded["targetRunId"], helper_id)
        self.assertEqual(recorded["integration"]["state"], "verified")
        self.assertGreater(recorded["targetRevision"], flow["helperView"]["revision"])
        with board.store.db.read() as connection:
            integration_run = connection.execute(
                "SELECT run_id FROM workflow_integrations WHERE integration_id=?",
                (recorded["integrationId"],),
            ).fetchone()["run_id"]
        self.assertEqual(integration_run, helper_id)
        # A sibling or foreign target is never reachable through the root capability.
        with self.assertRaises(BoardError) as raised:
            board.store.workflow.integration_record({
                "runId": run_id, "targetRunId": "run-not-owned", "commandId": "helper-integration-sibling",
                "expectedRevision": root_view["revision"], "artifactId": artifact["artifactId"],
                "notRequired": True, "reason": "wrong target", **self.control(root_view),
            })
        self.assertEqual(raised.exception.code, "UNAUTHORIZED")
        accepted = board.call("workflow_acknowledge", {
            "runId": run_id, "targetRunId": helper_id, "artifactId": artifact["artifactId"],
            "commandId": "helper-ack", "expectedRevision": root_view["revision"],
            "note": "reviewed the helper output", "verdict": "accepted", **self.control(root_view),
        })
        self.assertEqual(accepted["state"], "accepted")
        self.assertEqual(accepted["targetRunId"], helper_id)
        self.assertGreater(accepted["targetRevision"], recorded["targetRevision"])
        self.assertEqual(self.view(board, helper_id)["state"], "accepted")
        planned = board.store.workflow.cleanup_plan({
            "runId": run_id, "targetRunId": helper_id, "commandId": "helper-clean-plan",
            "expectedRevision": root_view["revision"], **self.control(root_view),
        })
        plan = planned["plan"]
        self.assertEqual(plan["state"], "planned", plan["reasons"])
        self.assertEqual(plan["workspaceId"], flow["helperManifest"]["workspaceId"])
        self.assertEqual(plan["path"], flow["helperManifest"]["checkoutRoot"])
        self.assertEqual(planned["targetRunId"], helper_id)
        applied = board.store.workflow.cleanup_apply({
            "runId": run_id, "targetRunId": helper_id, "planId": plan["planId"], "commandId": "helper-clean-apply",
            "expectedRevision": root_view["revision"], "confirmPath": plan["path"], **self.control(root_view),
        })
        self.assertTrue(applied["removed"])
        self.assertEqual(applied["targetRunId"], helper_id)
        self.assertFalse(flow["helperCheckout"].exists())
        # The root's own worktree is not the helper target and stays in place.
        self.assertTrue(flow["checkout"].exists())

    def test_an_accepted_helper_allocation_cleans_after_the_parent_is_accepted(self):
        flow = self.root_with_helper_worktree(request_id="helper-after-parent")
        board, run_id, helper_id = flow["board"], flow["runId"], flow["helperId"]
        root_view, artifact = flow["rootView"], flow["artifact"]
        target = self.target_with_artifact(artifact)
        board.store.workflow.integration_record({
            "runId": run_id, "targetRunId": helper_id, "commandId": "helper-before-parent-int",
            "expectedRevision": root_view["revision"], "artifactId": artifact["artifactId"],
            "strategy": "patch", "beforeCommit": target["before"],
            "target": {"path": str(target["path"]), "ref": "HEAD"},
            "reason": "helper output verified", **self.control(root_view),
        })
        accepted = board.call("workflow_acknowledge", {
            "runId": run_id, "targetRunId": helper_id, "artifactId": artifact["artifactId"],
            "commandId": "helper-before-parent-ack", "note": "helper reviewed", "verdict": "accepted",
            **self.control(root_view),
        })
        self.assertEqual(accepted["state"], "accepted")
        # Deliver and accept the parent without cleaning the helper first. The
        # approval's auto continuation already requeued the parent for its next turn.
        root_view = self.view(board, run_id)
        self.assertNotEqual(root_view["state"], "accepted")
        parent_claim = self.claim(board, run_id, request_id="parent-claim-2", nonce="p" * 16)
        self.assertIsNotNone(parent_claim["claim"], parent_claim)
        parent_manifest = parent_claim["claim"]["turn"]["input"]["executionWorkspace"]
        (Path(parent_manifest["path"]) / "parent-second.txt").write_text("parent second stage\n")
        parent_seal = workspace_module.seal(self.directory, parent_manifest, run_id,
                                            parent_claim["claim"]["attempt"]["attemptId"])
        self.finish_turn(board, parent_claim, parent_seal, nonce="p" * 16)
        root_view = self.view(board, run_id)
        self.assertEqual(root_view["state"], "delivered")
        parent_artifact = next(row for row in root_view["artifacts"] if row["kind"] == "output")
        parent_target = self.target_with_artifact(parent_artifact, name="parent-target")
        board.store.workflow.integration_record({
            "runId": run_id, "commandId": "parent-int", "expectedRevision": root_view["revision"],
            "artifactId": parent_artifact["artifactId"], "strategy": "patch", "beforeCommit": parent_target["before"],
            "target": {"path": str(parent_target["path"]), "ref": "HEAD"}, "reason": "parent output verified",
            **self.control(root_view),
        })
        root_view = self.view(board, run_id)
        parent_accepted = board.call("workflow_acknowledge", {
            "runId": run_id, "artifactId": parent_artifact["artifactId"], "commandId": "parent-ack",
            "note": "parent reviewed", "verdict": "accepted", **self.control(root_view),
        })
        self.assertEqual(parent_accepted["state"], "accepted")
        # The accepted helper allocation is still owned and still removable.
        root_view = self.view(board, run_id)
        planned = board.store.workflow.cleanup_plan({
            "runId": run_id, "targetRunId": helper_id, "commandId": "helper-after-parent-clean",
            "expectedRevision": root_view["revision"], **self.control(root_view),
        })
        self.assertEqual(planned["plan"]["state"], "planned", planned["plan"]["reasons"])
        applied = board.store.workflow.cleanup_apply({
            "runId": run_id, "targetRunId": helper_id, "planId": planned["plan"]["planId"],
            "commandId": "helper-after-parent-apply", "expectedRevision": root_view["revision"],
            "confirmPath": planned["plan"]["path"], **self.control(root_view),
        })
        self.assertTrue(applied["removed"])
        self.assertFalse(flow["helperCheckout"].exists())
        self.assertTrue(flow["checkout"].exists())

    def test_root_takeover_fences_a_stale_helper_target_command(self):
        flow = self.root_with_helper_worktree(request_id="helper-takeover")
        board, run_id, helper_id = flow["board"], flow["runId"], flow["helperId"]
        root_view, artifact = flow["rootView"], flow["artifact"]
        target = self.target_with_artifact(artifact)
        taken = board.call("workflow_takeover", {
            "runId": run_id, "commandId": "helper-takeover", "expectedOwnerGeneration": root_view["ownerGeneration"],
            "newHostId": "host-2", **self.control(root_view),
        })
        self.assertEqual(taken["ownerGeneration"], root_view["ownerGeneration"] + 1)
        params = {
            "runId": run_id, "targetRunId": helper_id, "commandId": "helper-stale-integration",
            "expectedRevision": taken["revision"], "artifactId": artifact["artifactId"], "strategy": "patch",
            "beforeCommit": target["before"], "target": {"path": str(target["path"]), "ref": "HEAD"},
            "reason": "a stale owner must be fenced",
        }
        with self.assertRaises(BoardError) as raised:
            board.store.workflow.integration_record({**params, **self.control(root_view)})
        self.assertEqual(raised.exception.code, "STALE_GENERATION")
        recorded = board.store.workflow.integration_record({**params, **dict(taken["control"])})
        self.assertEqual(recorded["targetRunId"], helper_id)
        self.assertEqual(recorded["integration"]["state"], "verified")

    def test_a_target_detached_during_git_work_is_refused(self):
        flow = self.root_with_helper_worktree(request_id="helper-detach")
        board, run_id, helper_id = flow["board"], flow["runId"], flow["helperId"]
        root_view, artifact = flow["rootView"], flow["artifact"]
        target = self.target_with_artifact(artifact)
        real_verify = workspace_module.integration_verify

        def detaching_verify(*args, **kwargs):
            verification = real_verify(*args, **kwargs)
            # Simulate the ownership link disappearing while the target checkout was
            # being verified, after the read-phase target snapshot.
            with board.store.db.write() as connection:
                connection.execute("DELETE FROM workflow_children WHERE child_task_id=?", (helper_id,))
            return verification

        with mock.patch.object(workspace_module, "integration_verify", detaching_verify):
            with self.assertRaises(BoardError) as raised:
                board.store.workflow.integration_record({
                    "runId": run_id, "targetRunId": helper_id, "commandId": "helper-detach-integration",
                    "expectedRevision": root_view["revision"], "artifactId": artifact["artifactId"],
                    "strategy": "patch", "beforeCommit": target["before"],
                    "target": {"path": str(target["path"]), "ref": "HEAD"},
                    "reason": "detached mid-flight", **self.control(root_view),
                })
        self.assertEqual(raised.exception.code, "UNAUTHORIZED")
        with board.store.db.read() as connection:
            recorded = connection.execute(
                "SELECT COUNT(*) FROM workflow_integrations WHERE run_id=?", (helper_id,)).fetchone()[0]
            parent = connection.execute(
                "SELECT parent_run_id FROM workflow_children WHERE child_task_id=?", (helper_id,)).fetchone()
        self.assertEqual(recorded, 0)
        self.assertIsNone(parent)
        self.assertTrue(flow["helperCheckout"].exists())

    def test_acknowledge_command_id_binds_the_claimed_integration(self):
        board = self.board()
        self.register(board)
        submitted, claimed, manifest, checkout, seal, view, artifact = self.run_worktree(
            board, request_id="ack-key")
        first = self.integrate(board, view, artifact, not_required=True, reason="first claim",
                               command_id="ack-int-1")
        second = self.integrate(board, first, artifact, not_required=True, reason="second claim",
                                command_id="ack-int-2")
        accepted = board.call("workflow_acknowledge", {
            "runId": submitted["runId"], "artifactId": artifact["artifactId"], "commandId": "ack-once",
            "integrationId": first["integration"]["integrationId"], "note": "reviewed the first claim",
            "verdict": "accepted", **self.control(second),
        })
        self.assertEqual(accepted["state"], "accepted")
        self.assertEqual(accepted["integration"]["integrationId"], first["integration"]["integrationId"])
        replay = board.call("workflow_acknowledge", {
            "runId": submitted["runId"], "artifactId": artifact["artifactId"], "commandId": "ack-once",
            "integrationId": first["integration"]["integrationId"], "note": "reviewed the first claim",
            "verdict": "accepted", **self.control(second),
        })
        self.assertTrue(replay["duplicate"])
        # The same commandId with a changed claimed integration is a different
        # command and conflicts instead of replaying the stored verdict.
        with self.assertRaises(BoardError) as raised:
            board.call("workflow_acknowledge", {
                "runId": submitted["runId"], "artifactId": artifact["artifactId"], "commandId": "ack-once",
                "integrationId": second["integration"]["integrationId"], "note": "reviewed the first claim",
                "verdict": "accepted", **self.control(second),
            })
        self.assertEqual(raised.exception.code, "CONFLICT")
        self.assertEqual(raised.exception.details["commandId"], "ack-once")
        # A different commandId that tries to relabel an accepted outcome with
        # another integration record conflicts against the recorded verdict.
        with self.assertRaises(BoardError) as raised:
            board.call("workflow_acknowledge", {
                "runId": submitted["runId"], "artifactId": artifact["artifactId"], "commandId": "ack-relabel",
                "integrationId": second["integration"]["integrationId"], "note": "reviewed the first claim",
                "verdict": "accepted", **self.control(second),
            })
        self.assertEqual(raised.exception.code, "CONFLICT")
        self.assertEqual(raised.exception.details["recordedIntegrationId"],
                         first["integration"]["integrationId"])

    def test_root_control_amends_a_helper_scope(self):
        flow = self.root_with_helper_worktree(request_id="helper-amend")
        board, run_id, helper_id = flow["board"], flow["runId"], flow["helperId"]
        root_view = flow["rootView"]
        amended = board.store.workflow.scope_amend({
            "runId": run_id, "targetRunId": helper_id, "commandId": "helper-amend",
            "expectedRevision": root_view["revision"], "expectedScopeVersion": 1,
            "writeScope": ["docs", "."], "reason": "the helper may touch docs next", **self.control(root_view),
        })
        self.assertEqual(amended["scopeVersion"], 2)
        self.assertEqual(amended["targetRunId"], helper_id)
        with board.store.db.read() as connection:
            stored = connection.execute(
                "SELECT write_scope_json FROM workflow_scope_versions WHERE run_id=? AND scope_version=2",
                (helper_id,),
            ).fetchone()
        self.assertEqual(json.loads(stored["write_scope_json"]), [".", "docs"])

    def test_root_control_resolves_a_helper_scope_conflict(self):
        flow = self.helper_scope_failure()
        board, run_id, helper_id = flow["board"], flow["runId"], flow["helperId"]
        root_view = self.view(board, run_id)
        resolved = board.store.workflow.workspace_resolve({
            "runId": run_id, "targetRunId": helper_id, "commandId": "helper-resolve",
            "expectedRevision": root_view["revision"], "conflictId": flow["conflict"]["conflictId"],
            "action": "restore", "paths": ["tracked.txt"],
            "observedFingerprint": flow["conflict"]["observedFingerprint"],
            "reason": "restore the helper site to its authorized input", **self.control(root_view),
        })
        self.assertEqual(resolved["targetRunId"], helper_id)
        self.assertEqual(resolved["resolutionState"], "restored")
        self.assertIn("tracked.txt", resolved["resolvedPaths"])
        self.assertEqual((flow["helperCheckout"] / "tracked.txt").read_text(), "base\n")
        self.assertEqual((flow["helperCheckout"] / "src" / "feature.py").read_text(), "value = 2\n")
        with board.store.db.read() as connection:
            conflict_run = connection.execute(
                "SELECT run_id FROM workflow_workspace_conflicts WHERE conflict_id=?",
                (flow["conflict"]["conflictId"],),
            ).fetchone()["run_id"]
        self.assertEqual(conflict_run, helper_id)

    def helper_scope_failure(self, *, request_id="helper-scope"):
        """A root run plus a helper whose own worktree seal fails outside its scope."""
        board = self.board()
        self.register(board)
        submitted, first_claim, manifest, checkout, seal, yielded, artifact = self.run_worktree(
            board, request_id=request_id, output="root output\n", disposition="assistance"
        )
        run_id = submitted["runId"]
        decided = board.call("workflow_decide", {
            "runId": run_id, "requestId": yielded["activeRequest"]["requestId"],
            "commandId": f"{request_id}-decide", "expectedRevision": yielded["revision"], "decision": "approve",
            "helpers": [{**CONFIGURATION, "requestId": f"{request_id}-helper", "task": "scoped helper",
                         "cwd": str(self.repo),
                         "executionWorkspace": {"kind": "worktree", "access": "write", "writeScope": ["src"]}}],
            **self.control(yielded),
        })
        helper_id = decided["children"][0]["taskId"]
        helper_claim = self.claim(board, helper_id, request_id=f"{request_id}-helper-claim", nonce="s" * 16)
        self.assertIsNotNone(helper_claim["claim"], helper_claim)
        helper_manifest = helper_claim["claim"]["turn"]["input"]["executionWorkspace"]
        helper_checkout = Path(helper_manifest["path"])
        (helper_checkout / "src" / "feature.py").write_text("value = 2\n")
        (helper_checkout / "tracked.txt").write_text("outside the helper scope\n")
        with self.assertRaises(BoardError) as raised:
            workspace_module.seal(self.directory, helper_manifest, helper_id,
                                  helper_claim["claim"]["attempt"]["attemptId"])
        self.assertEqual(raised.exception.code, "WORKSPACE_SCOPE_VIOLATION")
        attempt = helper_claim["claim"]["attempt"]
        seal_error = "WORKSPACE_SCOPE_VIOLATION: managed changes outside the declared write scope"
        board.call("worker_result", {
            "workerId": self.worker, "attemptId": attempt["attemptId"], "generation": attempt["generation"],
            "nonce": "s" * 16, "status": "failed",
            "result": {"status": "failed", "workspaceSealError": seal_error, "turnResultPath": "/tmp/turn-result.json"},
            "shutdownConfirmed": True, "exitCode": 1, "error": seal_error,
        })
        helper_view = self.view(board, helper_id)
        self.assertEqual(helper_view["state"], "awaiting-host")
        conflict = helper_view["workspaceConflicts"][0]
        self.assertEqual(conflict["state"], "open")
        return {"board": board, "runId": run_id, "helperId": helper_id, "helperManifest": helper_manifest,
                "helperCheckout": helper_checkout, "conflict": conflict}

    def test_cleanup_plan_expires_and_a_new_plan_is_required(self):
        clock = FakeClock()
        board = self.board(clock=clock)
        self.register(board)
        submitted, claimed, manifest, checkout, seal, view, artifact = self.run_worktree(board, request_id="expiry-1")
        target = self.target_with_artifact(artifact)
        self.integrate(board, view, artifact, target=target, reason="verified before cleanup")
        self.acknowledge(board, view, artifact)
        view = self.view(board, submitted["runId"])
        planned = self.plan(board, view)
        plan = planned["plan"]
        self.assertEqual(plan["state"], "planned")
        clock.value = "2099-01-01T00:00:00.000Z"
        with self.assertRaises(BoardError) as raised:
            self.apply(board, planned, plan, command_id="clean-expired-apply")
        self.assertEqual(raised.exception.code, "PLAN_EXPIRED")
        self.assertTrue(checkout.exists())
        # A fresh plan after expiry is a new authorization for the same exact path.
        fresh = self.plan(board, planned, command_id="clean-fresh")
        self.assertFalse(fresh["duplicate"])
        self.assertNotEqual(fresh["plan"]["planId"], plan["planId"])
        self.assertEqual(fresh["plan"]["state"], "planned")
        self.assertEqual(fresh["plan"]["path"], manifest["checkoutRoot"])

    def test_started_cleanup_recovers_after_plan_expiry_without_removing_twice(self):
        clock = FakeClock()
        board = self.board(clock=clock)
        self.register(board)
        submitted, _claim, _manifest, checkout, _seal, view, artifact = self.run_worktree(board)
        self.integrate(board, view, artifact, target=self.target_with_artifact(artifact))
        self.acknowledge(board, view, artifact)
        planned = self.plan(board, self.view(board, submitted["runId"]))
        plan = planned["plan"]
        remove = workspace_module.cleanup_remove

        def crash_after_removal(*args, **kwargs):
            remove(*args, **kwargs)
            raise RuntimeError("simulated loss before the completion transaction")

        with mock.patch.object(workspace_module, "cleanup_remove", side_effect=crash_after_removal):
            with self.assertRaisesRegex(RuntimeError, "simulated loss"):
                self.apply(board, planned, plan)
        self.assertFalse(checkout.exists())
        self.assertEqual(self.view(board, submitted["runId"])["cleanup"]["state"], "applying")
        clock.value = "2099-01-01T00:00:00.000Z"
        with mock.patch.object(workspace_module, "cleanup_remove", side_effect=AssertionError("second deletion")):
            recovered = self.apply(board, planned, plan)
            self.assertEqual(recovered["plan"]["state"], "applied")
            replay = self.apply(board, recovered, plan, command_id="recheck-applied")
        self.assertTrue(replay["duplicate"])
        self.assertTrue(Path(artifact["diffPath"]).is_file())


if __name__ == "__main__":
    unittest.main()
