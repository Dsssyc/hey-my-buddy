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

        def racing_remove(state_dir, target_manifest):
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
            return real_remove(state_dir, target_manifest)

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


if __name__ == "__main__":
    unittest.main()
