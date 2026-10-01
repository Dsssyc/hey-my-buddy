"""Real-Git acceptance and checkout reclaim over real temp checkouts.

These tests exercise the Host-owned lifecycle operations against real Git
repositories, real isolated worktrees and the real governed transactions: one
``accept`` derives and verifies the integration itself (or records an explicit
not-required decision) for the exact final artifact and then reclaims the
checkout, and ``conclude``/``reclaim`` remove exactly one registered disposable
checkout after acceptance or a recorded conclusion, confirmed shutdown and the
whole eligibility proof. No test touches an existing workspace.
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

from buddy import private_dirs
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

    def accept(self, board, view, artifact, *, not_required=False, target=None, reason=None,
               note="inspected the sealed output", **extra):
        """One-step acceptance: the service derives and verifies the integration itself."""
        params = {
            "runId": view["runId"], "artifactId": artifact["artifactId"], "note": note,
            **self.control(view),
        }
        if not_required:
            params["notRequired"] = "the artifact needs no repository target" if reason is None else reason
        else:
            self.assertIsNotNone(target)
            target_params = {"path": str(target["path"]), "ref": target.get("ref") or "HEAD"}
            for key in ("repositoryId", "checkoutId"):
                if target.get(key):
                    target_params[key] = target[key]
            params["target"] = target_params
            if target.get("before"):
                params["beforeCommit"] = target["before"]
        params.update(extra)
        return board.call("workflow_accept", params)

    def reclaim(self, board, view, **extra):
        params = {"runId": view["runId"], **self.control(view)}
        params.update(extra)
        return board.call("workflow_reclaim", params)

    def target_with_artifact(self, artifact, *, name="target", previous=()):
        target = self.directory / name
        git(self.directory, "clone", "-q", str(self.repo), str(target))
        before = git(target, "rev-parse", "HEAD").strip()
        for item in (*previous, artifact):
            completed = subprocess.run(
                ["git", "-C", str(target), "apply", "--binary", item["diffPath"]],
                env={**os.environ, **GIT_ENV}, capture_output=True, text=True,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
        git(target, "add", ".")
        git(target, "commit", "-qm", "integrate")
        after = git(target, "rev-parse", "HEAD").strip()
        return {"path": target, "before": before, "after": after}

    def acknowledge(self, board, view, artifact, *, verdict="accepted", **extra):
        """Compatibility shim: every acknowledgement is one accept under the current contract."""
        assert verdict == "accepted"
        return self.accept(board, view, artifact, **extra)

    # -- acceptance carries its integration evidence -------------------------
    def test_acceptance_requires_a_verified_or_not_required_decision(self):
        board = self.board()
        self.register(board)
        submitted, claimed, manifest, checkout, seal, view, artifact = self.run_worktree(board)
        with self.assertRaises(BoardError) as raised:
            board.call("workflow_accept", {
                "runId": view["runId"], "artifactId": artifact["artifactId"],
                "note": "inspected", **self.control(view),
            })
        self.assertEqual(raised.exception.code, "INVALID_ARGUMENT")
        with self.assertRaises(BoardError) as raised:
            self.accept(board, view, artifact, not_required=True, reason="")
        self.assertEqual(raised.exception.code, "INVALID_ARGUMENT")
        with self.assertRaises(BoardError) as raised:
            board.call("workflow_accept", {
                "runId": view["runId"], "artifactId": artifact["artifactId"], "note": "no",
                "notRequired": "no", "target": {"path": str(self.repo), "ref": "HEAD"},
                **self.control(view),
            })
        self.assertEqual(raised.exception.code, "INVALID_ARGUMENT")
        accepted = self.accept(board, view, artifact, not_required=True, reason="no separate target")
        self.assertFalse(accepted["duplicate"])
        self.assertEqual(accepted["state"], "accepted")
        self.assertEqual(accepted["integration"]["state"], "not-required")
        self.assertIsNone(accepted["integration"]["target"])
        self.assertEqual(accepted["integration"]["artifactId"], artifact["artifactId"])
        duplicate = board.call("workflow_accept", {
            "runId": view["runId"], "artifactId": artifact["artifactId"],
            "note": "inspected the sealed output", "notRequired": "no separate target",
            **self.control(view),
        })
        self.assertTrue(duplicate["duplicate"])
        with self.assertRaises(BoardError) as raised:
            board.call("workflow_accept", {
                "runId": view["runId"], "artifactId": artifact["artifactId"],
                "note": "a different review", "notRequired": "no separate target",
                **self.control(view),
            })
        self.assertEqual(raised.exception.code, "CONFLICT")

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
        # Acceptance binds the exact current final artifact; an older output is
        # refused instead of standing in for it.
        with self.assertRaises(BoardError) as raised:
            self.accept(board, delivered, first_output, not_required=True, reason="older output")
        self.assertEqual(raised.exception.code, "CONFLICT")
        current = self.view(board, run_id)
        accepted = self.accept(board, current, final_output, not_required=True, reason="final output")
        self.assertEqual(accepted["state"], "accepted")
        self.assertEqual(accepted["finalArtifactId"], final_output["artifactId"])

    def test_verified_integration_binds_the_actual_target_checkout(self):
        board = self.board()
        self.register(board)
        submitted, claimed, manifest, checkout, seal, view, artifact = self.run_worktree(board)
        target = self.target_with_artifact(artifact)
        # Before any acceptance, a target that never received the artifact is
        # refused with the differing paths, and a wrong repository identity is a
        # change refusal; the Host's own adjustments need adjusted:true and a note.
        blank = self.directory / "blank"
        git(self.directory, "clone", "-q", str(self.repo), str(blank))
        with self.assertRaises(BoardError) as raised:
            self.accept(board, view, artifact, target={"path": blank, "before": None})
        self.assertEqual(raised.exception.code, "INTEGRATION_UNVERIFIED")
        self.assertIn("tracked.txt", raised.exception.details["paths"])
        with self.assertRaises(BoardError) as raised:
            self.accept(board, view, artifact,
                        target={"path": target["path"], "repositoryId": "0" * 64, "before": target["before"]})
        self.assertEqual(raised.exception.code, "WORKSPACE_CHANGED")
        with self.assertRaises(BoardError) as raised:
            board.call("workflow_accept", {
                "runId": view["runId"], "artifactId": "art-not-real", "note": "x",
                "notRequired": "unknown", **self.control(view),
            })
        self.assertEqual(raised.exception.code, "NOT_FOUND")
        recorded = self.accept(board, view, artifact, target=target, reason="merged the sealed patch")
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
        self.assertEqual(recorded["state"], "accepted")
        # After the acceptance, any changed payload conflicts against the recorded
        # review instead of relabelling it.
        current = self.view(board, submitted["runId"])
        with self.assertRaises(BoardError) as raised:
            board.call("workflow_accept", {
                "runId": current["runId"], "artifactId": artifact["artifactId"],
                "note": "a different review",
                "target": {"path": str(target["path"]), "ref": "HEAD"},
                "beforeCommit": target["before"], **self.control(current),
            })
        self.assertEqual(raised.exception.code, "CONFLICT")

    def test_retained_output_without_path_summary_uses_fixed_git_objects(self):
        board = self.board()
        self.register(board)
        submitted, claimed, manifest, checkout, seal, view, artifact = self.run_worktree(
            board, request_id="old-path-summary"
        )
        target = self.target_with_artifact(artifact)
        # Model a retained 0.7 output whose optional summary was never published.
        with board.store.db.write() as connection:
            row = connection.execute("SELECT manifest_json FROM workflow_artifacts WHERE artifact_id=?",
                                     (artifact["artifactId"],)).fetchone()
            retained = json.loads(row["manifest_json"])
            retained["snapshot"].pop("changedEntries")
            retained["snapshot"].pop("changedEntriesTruncated")
            retained["snapshotSha256"] = workspace_module._sha(workspace_module._json(retained["snapshot"]))
            connection.execute("UPDATE workflow_artifacts SET manifest_json=?,manifest_sha256=? WHERE artifact_id=?",
                               (canonical_json(retained), retained["snapshotSha256"], artifact["artifactId"]))
        recorded = self.accept(board, view, artifact, target=target, keepCheckout=True)
        self.assertEqual(recorded["integration"]["verification"]["matchingPaths"], ["tracked.txt"])
        self.assertEqual(recorded["state"], "accepted")
        (checkout / "tracked.txt").write_text("unsealed edit\n")
        inspected = workspace_module.cleanup_inspect(self.directory, manifest, sealed=retained, retained=[manifest])
        self.assertIn("unsealed-changes", inspected["reasons"])
        with self.assertRaises(BoardError) as raised:
            self.reclaim(board, self.view(board, submitted["runId"]))
        self.assertEqual(raised.exception.code, "NOT_READY")
        self.assertIn("unsealed-changes", raised.exception.details["reasons"])
        self.assertTrue(checkout.exists())
        # Returning the site to the sealed state makes the same workspace eligible.
        (checkout / "tracked.txt").write_text("sealed output\n")
        applied = self.reclaim(board, self.view(board, submitted["runId"]))
        self.assertTrue(applied["removed"])

    def test_missing_or_spoofed_source_objects_cannot_verify(self):
        board = self.board()
        self.register(board)
        submitted, claimed, manifest, checkout, seal, view, artifact = self.run_worktree(
            board, request_id="object-binding"
        )
        target = self.target_with_artifact(artifact)
        with self.assertRaises(BoardError):
            workspace_module.integration_verify(
                {**seal, "commit": "0" * 40}, original_input=manifest, final_input=manifest,
                path=str(target["path"]), ref="HEAD", strategy="patch", before_commit=target["before"],
            )
        with mock.patch.object(workspace_module, "OUTPUT_ENTRY_LIMIT", 0):
            with self.assertRaises(BoardError) as oversized:
                self.accept(board, self.view(board, submitted["runId"]), artifact, target=target)
        self.assertEqual(oversized.exception.code, "WORKSPACE_UNSUPPORTED")
        git(self.repo, "update-ref", "-d", manifest["snapshot"]["inputRef"])
        with self.assertRaises(BoardError) as raised:
            self.accept(board, self.view(board, submitted["runId"]), artifact, target=target)
        self.assertEqual(raised.exception.code, "WORKSPACE_REF_INVALID")

    def test_final_noop_continuation_still_requires_earlier_goal_change(self):
        board = self.board()
        self.register(board)
        submitted, first_claim, first_manifest, checkout, first_seal, yielded, first = self.run_worktree(
            board, request_id="noop-final", output="first output\n", disposition="assistance"
        )
        continued = board.call("workflow_continue", {
            "runId": submitted["runId"], "commandId": "noop-continue", "expectedRevision": yielded["revision"],
            "input": "finish", **self.control(yielded),
        })
        second = self.claim(board, submitted["runId"], request_id="noop-claim", nonce="q" * 16)
        second_manifest = second["claim"]["turn"]["input"]["executionWorkspace"]
        final_seal = workspace_module.seal(self.directory, second_manifest, submitted["runId"],
                                           second["claim"]["attempt"]["attemptId"])
        self.assertEqual(final_seal["changedPaths"], [])
        self.finish_turn(board, second, final_seal, nonce="q" * 16)
        delivered = self.view(board, submitted["runId"])
        final = next(row for row in delivered["artifacts"] if row["kind"] == "output")
        blank = self.directory / "noop-blank"
        git(self.directory, "clone", "-q", str(self.repo), str(blank))
        before = git(blank, "rev-parse", "HEAD").strip()
        with self.assertRaises(BoardError) as raised:
            self.accept(board, delivered, final, target={"path": blank, "before": before})
        self.assertEqual(raised.exception.code, "INTEGRATION_UNVERIFIED")
        self.assertIn("tracked.txt", raised.exception.details["paths"])
        # A Host adjustment is bound to the acceptance note: without it there is no
        # adjusted acceptance at all.
        with self.assertRaises(BoardError) as unreasoned:
            board.call("workflow_accept", {
                "runId": delivered["runId"], "artifactId": final["artifactId"], "note": "",
                "adjusted": True, "target": {"path": str(blank), "ref": "HEAD"},
                **self.control(delivered),
            })
        self.assertEqual(unreasoned.exception.code, "INVALID_ARGUMENT")
        # Confirming the difference as its own adjustment accepts against the same
        # blank target, with the exact adjusted paths recorded.
        adjusted = self.accept(board, self.view(board, submitted["runId"]), final,
                               target={"path": blank, "before": before}, adjusted=True,
                               reason="the whole-goal change is already the Host's own edit")
        self.assertEqual(adjusted["state"], "accepted")
        self.assertEqual(adjusted["integration"]["verification"]["adjustments"], ["tracked.txt"])
        self.assertEqual(adjusted["integration"]["verification"]["unrecordedPaths"], [])

    def test_deletion_and_mode_change_are_bound_to_final_tree(self):
        (self.repo / "mode.txt").write_text("executable content\n")
        git(self.repo, "add", "mode.txt")
        git(self.repo, "commit", "-qm", "add mode fixture")
        board = self.board()
        self.register(board)
        submitted = board.call("workflow_submit", {
            **CONFIGURATION, "requestId": "delete-mode", "hostId": "host-1", "task": "delete and chmod",
            "cwd": str(self.repo), "submissionToken": "t" * 32,
            "executionWorkspace": {"kind": "worktree", "access": "write", "writeScope": ["."]},
        })
        self.controls[submitted["runId"]] = submitted["control"]
        claimed = self.claim(board, submitted["runId"], request_id="delete-mode-claim")
        manifest = claimed["claim"]["turn"]["input"]["executionWorkspace"]
        checkout = Path(manifest["path"])
        (checkout / "tracked.txt").unlink()
        (checkout / "mode.txt").chmod(0o755)
        seal = workspace_module.seal(self.directory, manifest, submitted["runId"],
                                     claimed["claim"]["attempt"]["attemptId"])
        self.finish_turn(board, claimed, seal)
        view = self.view(board, submitted["runId"])
        artifact = next(row for row in view["artifacts"] if row["kind"] == "output")
        target = self.target_with_artifact(artifact, name="delete-mode-target")
        recorded = self.accept(board, view, artifact, target=target)
        self.assertEqual(recorded["state"], "accepted")
        self.assertEqual(recorded["integration"]["verification"]["matchingPaths"], ["mode.txt", "tracked.txt"])
        self.assertEqual(git(target["path"], "ls-tree", "HEAD", "mode.txt").split()[0], "100755")
        self.assertEqual(git(target["path"], "ls-tree", "HEAD", "tracked.txt"), "")

    # -- cleanup -------------------------------------------------------------
    def accepted_worktree(self, *, request_id="cleanup-1"):
        board = self.board()
        self.register(board)
        submitted, claimed, manifest, checkout, seal, view, artifact = self.run_worktree(board, request_id=request_id)
        target = self.target_with_artifact(artifact)
        accepted = self.accept(board, view, artifact, target=target, reason="verified before cleanup",
                               keepCheckout=True)
        self.assertEqual(accepted["state"], "accepted")
        return board, submitted, manifest, checkout, artifact, self.view(board, submitted["runId"])

    def test_storage_apply_reuses_workspace_protection_and_current_revision(self):
        board, submitted, manifest, checkout, artifact, view = self.accepted_worktree()
        with mock.patch.dict(os.environ, {'BUDDY_RUNTIME_ROOT':str(self.directory / 'runtime')}), mock.patch('buddy.storage.process_inventory', return_value=([],[],True)):
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
        native = private_dirs.goal_root(board.directory, 'zcode', run_id)
        session = private_dirs.ensure_private_dir(native / 'native') / 'sessions.sqlite'
        session.write_bytes(b'private session fixture')
        with mock.patch.dict(os.environ, {'BUDDY_RUNTIME_ROOT':str(self.directory / 'runtime')}), mock.patch('buddy.storage.process_inventory', return_value=([],[],True)):
            planned = board.call('storage_plan', {})
            request = {'planId':planned['planId'], 'commandId':'native-interrupted', 'confirm':True}
            with mock.patch('buddy.storage.private_dirs.remove_tree', side_effect=OSError('injected')):
                with self.assertRaises(BoardError) as caught:
                    board.call('storage_apply', request)
            self.assertEqual(caught.exception.code, 'STORAGE_INCOMPLETE')
            self.assertFalse(native.exists())
            result = board.call('storage_apply', request)
            self.assertTrue(result['complete'])
            self.assertTrue(any(r['path'] == str(native.resolve()) for r in result['removed']))
            self.assertFalse(list(native.parent.glob('.reclaim-*')))

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
        target = self.target_with_artifact(final, previous=(first_artifact,))
        accepted = self.accept(board, delivered, final, target=target,
                               reason="verified the sealed second stage", keepCheckout=True)
        self.assertEqual(accepted["state"], "accepted")
        return board, submitted, first_manifest, second_manifest, final, self.view(board, run_id)

    def test_cleanup_removes_only_the_registered_worktree_and_is_idempotent(self):
        board, submitted, manifest, checkout, artifact, view = self.accepted_worktree()
        run_id = submitted["runId"]
        private_goal = private_dirs.ensure_private_dir(private_dirs.native_root(board.directory, 'codex', run_id))
        (private_goal / 'session.json').write_text('private native binding')
        account = private_dirs.ensure_private_dir(private_dirs.account_root(board.directory, 'codex'))
        (account / 'account.json').write_text('protected account fixture')
        applied = self.reclaim(board, view)
        self.assertTrue(applied["removed"])
        self.assertEqual(applied["path"], manifest["checkoutRoot"])
        self.assertFalse(checkout.exists())
        self.assertFalse(private_goal.exists())
        self.assertEqual((account / 'account.json').read_text(), 'protected account fixture')
        # Only the checkout was removed: manifests, patches and Git refs stay readable.
        workspace_dir = Path(self.directory) / "workspaces" / manifest["workspaceId"]
        self.assertTrue((workspace_dir / "manifest.json").exists())
        self.assertTrue(Path(artifact["diffPath"]).exists())
        refs = git(self.repo, "for-each-ref", "--format=%(refname)", f"refs/buddy/workspaces/{manifest['workspaceId']}/")
        self.assertIn("outputs", refs)
        still = self.view(board, run_id)
        self.assertEqual(still["cleanup"]["state"], "applied")
        self.assertTrue(still["cleanup"]["retention"]["artifactIds"])
        self.assertIn(str(Path(self.directory) / "workspaces" / manifest["workspaceId"]),
                      still["cleanup"]["retention"]["workspaceDirectory"])
        # Reclaiming an already removed allocation is the same achieved outcome.
        repeat = self.reclaim(board, still)
        self.assertTrue(repeat["removed"])
        self.assertTrue(repeat["alreadyRemoved"])

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
        (checkout / "tracked.txt").write_text("edited after the seal\n")
        with self.assertRaises(BoardError) as raised:
            self.reclaim(board, view)
        self.assertEqual(raised.exception.code, "NOT_READY")
        self.assertIn("unsealed-changes", raised.exception.details["reasons"])
        blocked_view = self.view(board, submitted["runId"])
        self.assertEqual(blocked_view["cleanup"]["state"], "blocked")
        self.assertEqual(blocked_view["cleanup"]["evidence"]["workspace"]["unsealedPaths"], ["tracked.txt"])
        self.assertTrue(checkout.exists())
        # Returning the site to the sealed state makes the same workspace eligible.
        (checkout / "tracked.txt").write_text("sealed output\n")
        recovered = self.reclaim(board, blocked_view)
        self.assertTrue(recovered["removed"])
        self.assertFalse(checkout.exists())

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
        self.assertIn(f"refs/buddy/workspaces/{first['workspaceId']}/", refs)
        self.assertIn(f"refs/buddy/workspaces/{second['workspaceId']}/", refs)
        # The applied plan stays the one authorization; repeating it is a replay.
        repeat_view = self.view(board, run_id)
        self.assertEqual(repeat_view["cleanup"]["state"], "applied")
        repeat = self.reclaim(board, repeat_view)
        self.assertTrue(repeat["removed"])
        self.assertTrue(repeat["alreadyRemoved"])

    def test_continuation_cleanup_blocks_a_change_after_the_latest_seal(self):
        board, submitted, first, second, final, view = self.two_turn_worktree(request_id="continuation-dirty")
        checkout = Path(second["checkoutRoot"])
        (checkout / "tracked.txt").write_text("edited after the second seal\n")
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
        (checkout / "tracked.txt").write_text("first turn output\n")
        recovered = self.reclaim(board, blocked_view)
        self.assertTrue(recovered["removed"], recovered)

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
        accepted = board.call("workflow_accept", {
            "runId": run_id, "targetRunId": helper_id, "artifactId": artifact["artifactId"],
            "note": "reviewed the helper output",
            "target": {"path": str(target["path"]), "ref": "HEAD"},
            "beforeCommit": target["before"], "keepCheckout": True, **self.control(root_view),
        })
        self.assertEqual(accepted["state"], "accepted")
        self.assertEqual(accepted["targetRunId"], helper_id)
        self.assertEqual(accepted["integration"]["state"], "verified")
        self.assertEqual(self.view(board, helper_id)["state"], "accepted")
        with board.store.db.read() as connection:
            integration_run = connection.execute(
                "SELECT run_id FROM workflow_integrations WHERE integration_id=?",
                (accepted["integrationId"],),
            ).fetchone()["run_id"]
        self.assertEqual(integration_run, helper_id)
        # A sibling or foreign target is never reachable through the root capability.
        with self.assertRaises(BoardError) as raised:
            board.call("workflow_accept", {
                "runId": run_id, "targetRunId": "run-not-owned", "artifactId": artifact["artifactId"],
                "note": "wrong target", "notRequired": "wrong target", **self.control(root_view),
            })
        self.assertEqual(raised.exception.code, "UNAUTHORIZED")
        # The kept helper checkout is reclaimed through the root capability alone.
        self.assertTrue(flow["helperCheckout"].exists())
        applied = board.call("workflow_reclaim", {
            "runId": run_id, "targetRunId": helper_id, **self.control(root_view),
        })
        self.assertTrue(applied["removed"])
        self.assertFalse(applied["alreadyRemoved"])
        self.assertEqual(applied["targetRunId"], helper_id)
        self.assertFalse(flow["helperCheckout"].exists())
        # The root's own worktree is not the helper target and stays in place.
        self.assertTrue(flow["checkout"].exists())

    def test_an_accepted_helper_allocation_cleans_after_the_parent_is_accepted(self):
        flow = self.root_with_helper_worktree(request_id="helper-after-parent")
        board, run_id, helper_id = flow["board"], flow["runId"], flow["helperId"]
        root_view, artifact = flow["rootView"], flow["artifact"]
        target = self.target_with_artifact(artifact)
        accepted = board.call("workflow_accept", {
            "runId": run_id, "targetRunId": helper_id, "artifactId": artifact["artifactId"],
            "note": "helper reviewed", "target": {"path": str(target["path"]), "ref": "HEAD"},
            "beforeCommit": target["before"], **self.control(root_view),
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
        first_parent_artifact = next(row for row in flow["rootView"]["artifacts"] if row["kind"] == "output")
        parent_target = self.target_with_artifact(parent_artifact, name="parent-target",
                                                  previous=(first_parent_artifact,))
        parent_accepted = board.call("workflow_accept", {
            "runId": run_id, "artifactId": parent_artifact["artifactId"], "note": "parent reviewed",
            "target": {"path": str(parent_target["path"]), "ref": "HEAD"},
            "beforeCommit": parent_target["before"], "keepCheckout": True, **self.control(root_view),
        })
        self.assertEqual(parent_accepted["state"], "accepted")
        # The accepted helper allocation is still owned and still removable.
        root_view = self.view(board, run_id)
        applied = board.call("workflow_reclaim", {
            "runId": run_id, "targetRunId": helper_id, **self.control(root_view),
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
            "runId": run_id, "targetRunId": helper_id, "artifactId": artifact["artifactId"],
            "note": "a stale owner must be fenced",
            "target": {"path": str(target["path"]), "ref": "HEAD"},
            "beforeCommit": target["before"],
        }
        with self.assertRaises(BoardError) as raised:
            board.call("workflow_accept", {**params, **self.control(root_view)})
        self.assertEqual(raised.exception.code, "STALE_GENERATION")
        recorded = board.call("workflow_accept", {**params, **dict(taken["control"])})
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
                board.call("workflow_accept", {
                    "runId": run_id, "targetRunId": helper_id, "artifactId": artifact["artifactId"],
                    "note": "detached mid-flight",
                    "target": {"path": str(target["path"]), "ref": "HEAD"},
                    "beforeCommit": target["before"], **self.control(root_view),
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

    def test_acceptance_payload_binds_the_recorded_review(self):
        board = self.board()
        self.register(board)
        submitted, claimed, manifest, checkout, seal, view, artifact = self.run_worktree(
            board, request_id="ack-key")
        accepted = board.call("workflow_accept", {
            "runId": submitted["runId"], "artifactId": artifact["artifactId"],
            "note": "reviewed the first claim", "notRequired": "no separate target",
            **self.control(view),
        })
        self.assertEqual(accepted["state"], "accepted")
        self.assertEqual(accepted["integration"]["state"], "not-required")
        replay = board.call("workflow_accept", {
            "runId": submitted["runId"], "artifactId": artifact["artifactId"],
            "note": "reviewed the first claim", "notRequired": "no separate target",
            **self.control(view),
        })
        self.assertTrue(replay["duplicate"])
        # A changed payload derives a different command and conflicts against the
        # recorded acceptance instead of relabelling the reviewed outcome.
        with self.assertRaises(BoardError) as raised:
            board.call("workflow_accept", {
                "runId": submitted["runId"], "artifactId": artifact["artifactId"],
                "note": "a different claim", "notRequired": "no separate target",
                **self.control(view),
            })
        self.assertEqual(raised.exception.code, "CONFLICT")
        with self.assertRaises(BoardError) as raised:
            board.call("workflow_accept", {
                "runId": submitted["runId"], "artifactId": artifact["artifactId"],
                "note": "reviewed the first claim",
                "target": {"path": str(self.directory / "blank-ack"), "ref": "HEAD"},
                **self.control(view),
            })
        self.assertEqual(raised.exception.code, "CONFLICT")

    def test_reclaim_after_adopt_counts_the_adopted_paths(self):
        """An adopted out-of-scope conflict no longer blocks the delivered reclaim."""
        board = self.board()
        self.register(board)
        submitted = board.call("workflow_submit", {
            **CONFIGURATION, "requestId": "adopt-reclaim", "hostId": "host-1",
            "task": "scoped run whose only failure is the refused seal",
            "cwd": str(self.repo), "submissionToken": "t" * 32,
            "executionWorkspace": {"kind": "worktree", "access": "write", "writeScope": ["src"]},
        })
        self.controls[submitted["runId"]] = submitted["control"]
        claimed = self.claim(board, submitted["runId"], request_id="adopt-reclaim-claim")
        manifest = claimed["claim"]["turn"]["input"]["executionWorkspace"]
        checkout = Path(manifest["path"])
        (checkout / "src" / "feature.py").parent.mkdir(parents=True, exist_ok=True)
        (checkout / "src" / "feature.py").write_text("value = 2\n")
        (checkout / "tracked.txt").write_text("outside the authorized scope\n")
        (checkout / "outside.txt").write_text("untracked outside\n")
        with self.assertRaises(BoardError) as raised:
            workspace_module.seal(self.directory, manifest, submitted["runId"],
                                  claimed["claim"]["attempt"]["attemptId"])
        self.assertEqual(raised.exception.code, "WORKSPACE_SCOPE_VIOLATION")
        # A scope-only failure keeps the attempt's own native completed outcome.
        claim = claimed["claim"]
        seal_error = "WORKSPACE_SCOPE_VIOLATION: managed changes outside the declared write scope"
        board.call("worker_result", {
            "workerId": self.worker, "attemptId": claim["attempt"]["attemptId"],
            "generation": claim["attempt"]["generation"], "nonce": "n" * 16, "status": "failed",
            "result": {"status": "failed", "processState": {"shutdownConfirmed": True},
                       "workspaceSealError": seal_error, "turnResultPath": "/tmp/turn-result.json",
                       "turn": self.turn_record(claim, disposition="completed")},
            "shutdownConfirmed": True, "exitCode": 1, "error": seal_error,
        })
        view = self.view(board, submitted["runId"])
        conflict = view["workspaceConflicts"][0]
        self.assertEqual(conflict["state"], "open")
        adopted = board.store.workflow.workspace_resolve({
            "runId": submitted["runId"], "commandId": "adopt-site", "expectedRevision": view["revision"],
            "conflictId": conflict["conflictId"], "action": "adopt",
            "observedFingerprint": conflict["observedFingerprint"],
            "reason": "the Host adopts this exact site as the delivered outcome", **self.control(view),
        })
        self.assertEqual(adopted["resolutionState"], "adopted")
        self.assertTrue(adopted["delivered"])
        self.assertIn("tracked.txt", adopted["adoptedPaths"])
        delivered = self.view(board, submitted["runId"])
        resolved = next(row for row in delivered["artifacts"] if row["kind"] == "resolved-output")
        # The adopted site is exactly what acceptance retains: the automatic reclaim
        # must count the adopted paths as authorized instead of reporting unsealed.
        accepted = board.call("workflow_accept", {
            "runId": submitted["runId"], "artifactId": resolved["artifactId"],
            "note": "reviewed the adopted site", "notRequired": "the adopted site needs no separate target",
            **self.control(delivered),
        })
        self.assertEqual(accepted["state"], "accepted")
        self.assertTrue(accepted["reclaim"]["removed"], accepted["reclaim"])
        self.assertFalse(checkout.exists())

    def test_a_forged_adopt_record_authorizes_nothing_for_conclude(self):
        """Only board-bound, Git-verified adoption records authorize out-of-scope paths."""
        board = self.board()
        self.register(board)
        submitted = board.call("workflow_submit", {
            **CONFIGURATION, "requestId": "adopt-forge", "hostId": "host-1",
            "task": "scoped run whose only failure is the refused seal",
            "cwd": str(self.repo), "submissionToken": "t" * 32,
            "executionWorkspace": {"kind": "worktree", "access": "write", "writeScope": ["src"]},
        })
        self.controls[submitted["runId"]] = submitted["control"]
        claimed = self.claim(board, submitted["runId"], request_id="adopt-forge-claim")
        manifest = claimed["claim"]["turn"]["input"]["executionWorkspace"]
        checkout = Path(manifest["path"])
        (checkout / "src" / "feature.py").parent.mkdir(parents=True, exist_ok=True)
        (checkout / "src" / "feature.py").write_text("value = 2\n")
        (checkout / "tracked.txt").write_text("outside the authorized scope\n")
        with self.assertRaises(BoardError):
            workspace_module.seal(self.directory, manifest, submitted["runId"],
                                  claimed["claim"]["attempt"]["attemptId"])
        claim = claimed["claim"]
        seal_error = "WORKSPACE_SCOPE_VIOLATION: managed changes outside the declared write scope"
        board.call("worker_result", {
            "workerId": self.worker, "attemptId": claim["attempt"]["attemptId"],
            "generation": claim["attempt"]["generation"], "nonce": "n" * 16, "status": "failed",
            "result": {"status": "failed", "processState": {"shutdownConfirmed": True},
                       "workspaceSealError": seal_error, "turnResultPath": "/tmp/turn-result.json",
                       "turn": self.turn_record(claim, disposition="completed")},
            "shutdownConfirmed": True, "exitCode": 1, "error": seal_error,
        })
        view = self.view(board, submitted["runId"])
        conflict = view["workspaceConflicts"][0]
        adopted = board.store.workflow.workspace_resolve({
            "runId": submitted["runId"], "commandId": "adopt-site", "expectedRevision": view["revision"],
            "conflictId": conflict["conflictId"], "action": "adopt",
            "observedFingerprint": conflict["observedFingerprint"],
            "reason": "adopt this exact site", **self.control(view),
        })
        self.assertTrue(adopted["delivered"])
        # A Host change beyond both the scope and the adopted site, plus a forged
        # adoption record that claims the Host already promoted exactly that path.
        (checkout / "smuggled.txt").write_text("never authorized\n")
        with board.store.db.write() as connection:
            connection.execute(
                "INSERT INTO workflow_artifacts(artifact_id, run_id, turn_id, attempt_id, source_task_id, kind,"
                " manifest_json, manifest_sha256, created_at) VALUES('art-forged', ?, NULL, NULL, NULL,"
                " 'resolved-output', ?, '0' * 64wait, '2020-01-01T00:00:00.000Z')"
                .replace("'0' * 64wait", "'0'"),
                (submitted["runId"], canonical_json({
                    "kind": "resolution", "action": "adopt",
                    "manifestSha256": manifest["manifestSha256"], "snapshotSha256": "0",
                    "adoptedPaths": ["smuggled.txt"], "changedPaths": ["smuggled.txt"],
                })),
            )
        params = {"runId": submitted["runId"], "note": "the forged record must not smuggle this path",
                  **self.control(self.view(board, submitted["runId"]))}
        with self.assertRaises(BoardError) as raised:
            board.call("workflow_conclude", params)
        self.assertEqual(raised.exception.code, "WORKSPACE_SCOPE_VIOLATION")
        self.assertEqual(raised.exception.details["paths"], ["smuggled.txt"])
        self.assertTrue(checkout.exists())
        # Without the forgery the same conclusion seals the real adopted site and
        # the Host's in-scope work, then reclaims the checkout.
        with board.store.db.write() as connection:
            connection.execute("DELETE FROM workflow_artifacts WHERE artifact_id='art-forged'")
        (checkout / "smuggled.txt").unlink()
        concluded = board.call("workflow_conclude", params)
        self.assertEqual(concluded["state"], "delivered")
        self.assertTrue(concluded["reclaim"]["removed"], concluded["reclaim"])
        self.assertFalse(checkout.exists())

    def test_accept_refuses_a_detached_target_before_any_git_work(self):
        flow = self.root_with_helper_worktree(request_id="accept-detach-early")
        board, run_id, helper_id = flow["board"], flow["runId"], flow["helperId"]
        root_view, artifact = flow["rootView"], flow["artifact"]
        target = self.target_with_artifact(artifact)
        real_verify = workspace_module.integration_verify
        called: list[int] = []

        def counting_verify(*args, **kwargs):
            called.append(1)
            return real_verify(*args, **kwargs)

        with board.store.db.write() as connection:
            connection.execute("DELETE FROM workflow_children WHERE child_task_id=?", (helper_id,))
        with mock.patch.object(workspace_module, "integration_verify", counting_verify):
            with self.assertRaises(BoardError) as raised:
                board.call("workflow_accept", {
                    "runId": run_id, "targetRunId": helper_id, "artifactId": artifact["artifactId"],
                    "note": "a detached target is refused before any Git work",
                    "target": {"path": str(target["path"]), "ref": "HEAD"},
                    "beforeCommit": target["before"], **self.control(root_view),
                })
        self.assertEqual(raised.exception.code, "UNAUTHORIZED")
        self.assertEqual(called, [], "Git verification must not run for an unauthorized target")
        with board.store.db.read() as connection:
            self.assertEqual(connection.execute(
                "SELECT COUNT(*) FROM workflow_integrations WHERE run_id=?", (helper_id,)).fetchone()[0], 0)

    def test_accept_refuses_an_unrelated_commit_interval(self):
        board = self.board()
        self.register(board)
        submitted, claimed, manifest, checkout, seal, view, artifact = self.run_worktree(
            board, request_id="interval-refusal")
        target = self.target_with_artifact(artifact)
        before = git(target["path"], "rev-parse", "HEAD").strip()
        # An unrelated root commit that exists in the target repository but is no
        # ancestor of the integrated ref.
        git(target["path"], "checkout", "-q", "--orphan", "unrelated")
        git(target["path"], "commit", "-q", "--allow-empty", "-qm", "an unrelated root commit")
        unrelated_commit = git(target["path"], "rev-parse", "HEAD").strip()
        # The artifact content matches the target, but the named commit interval
        # does not hold: the comparison proves nothing and is refused.
        with self.assertRaises(BoardError) as raised:
            board.call("workflow_accept", {
                "runId": view["runId"], "artifactId": artifact["artifactId"],
                "note": "content matches but the interval does not",
                "target": {"path": str(target["path"]), "ref": before},
                "beforeCommit": unrelated_commit,
                **self.control(view),
            })
        self.assertEqual(raised.exception.code, "INTEGRATION_UNVERIFIED")
        self.assertFalse(raised.exception.details["verification"]["verified"])
        with board.store.db.read() as connection:
            self.assertEqual(connection.execute(
                "SELECT COUNT(*) FROM workflow_integrations WHERE run_id=?", (submitted["runId"],)).fetchone()[0], 0)
            self.assertIsNone(connection.execute(
                "SELECT accepted_at FROM tasks WHERE task_id=?", (submitted["runId"],)).fetchone()[0])
        # The real interval verifies and accepts (the orphan branch moved HEAD,
        # so the integrated commit is named explicitly).
        accepted = self.accept(board, self.view(board, submitted["runId"]), artifact,
                               target={"path": target["path"], "before": before, "ref": target["after"]})
        self.assertEqual(accepted["state"], "accepted")

    def test_a_takeover_between_review_and_reclaim_blocks_only_the_removal(self):
        board = self.board()
        self.register(board)
        submitted, claimed, manifest, checkout, seal, view, artifact = self.run_worktree(
            board, request_id="takeover-window")
        real_plan = board.store.workflow.cleanup_plan
        generation = {"done": False}
        nonlocal_taken = {"control": None}

        def taking_over_plan(coordinator, params, *, console_authority=None):
            if not generation["done"]:
                # Exactly the documented window: the acceptance is committed, the
                # automatic reclaim has not planned its removal yet, and another
                # Host takes over the controlling goal.
                generation["done"] = True
                current = self.view(board, submitted["runId"])
                current_control = dict(self.control(current))
                taken = board.call("workflow_takeover", {
                    "runId": submitted["runId"], "commandId": "window-takeover",
                    "expectedOwnerGeneration": current["ownerGeneration"], "newHostId": "host-2",
                    **current_control,
                })
                nonlocal_taken["control"] = dict(taken["control"])
            return real_plan(params)

        with mock.patch.object(workflow_module.WorkflowCoordinator, "cleanup_plan", taking_over_plan):
            accepted = self.accept(board, view, artifact, target=self.target_with_artifact(artifact))
        self.assertEqual(accepted["state"], "accepted")
        self.assertFalse(accepted["reclaim"]["removed"])
        self.assertIn("STALE_GENERATION", accepted["reclaim"]["reasons"])
        self.assertTrue(checkout.exists())
        taken_control = nonlocal_taken["control"]
        # The old owner cannot even retry; the new owner's reclaim completes it.
        old_control = dict(self.controls[submitted["runId"]])
        self.controls[submitted["runId"]] = dict(taken_control)
        with self.assertRaises(BoardError) as raised:
            board.call("workflow_reclaim", {"runId": submitted["runId"], **old_control})
        self.assertEqual(raised.exception.code, "STALE_GENERATION")
        applied = self.reclaim(board, self.view(board, submitted["runId"]))
        self.assertTrue(applied["removed"])
        self.assertFalse(checkout.exists())

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

    def test_a_blocked_reclaim_needs_a_fresh_removal_for_the_same_exact_path(self):
        board = self.board()
        self.register(board)
        submitted, claimed, manifest, checkout, seal, view, artifact = self.run_worktree(board, request_id="expiry-1")
        target = self.target_with_artifact(artifact)
        accepted = self.accept(board, view, artifact, target=target, reason="verified before cleanup",
                               keepCheckout=True)
        self.assertEqual(accepted["state"], "accepted")
        view = self.view(board, submitted["runId"])
        (checkout / "tracked.txt").write_text("edited after the seal\n")
        with self.assertRaises(BoardError) as raised:
            self.reclaim(board, view)
        self.assertEqual(raised.exception.code, "NOT_READY")
        blocked = self.view(board, submitted["runId"])["cleanup"]
        self.assertEqual(blocked["state"], "blocked")
        self.assertTrue(checkout.exists())
        # Removing the blocker runs one fresh removal authorization for the same
        # exact registered path.
        (checkout / "tracked.txt").write_text("sealed output\n")
        applied = self.reclaim(board, self.view(board, submitted["runId"]))
        self.assertTrue(applied["removed"])
        self.assertEqual(applied["path"], manifest["checkoutRoot"])
        self.assertNotEqual(self.view(board, submitted["runId"])["cleanup"]["planId"], blocked["planId"])
        self.assertFalse(checkout.exists())

    def test_started_reclaim_recovers_after_interruption_without_removing_twice(self):
        clock = FakeClock()
        board = self.board(clock=clock)
        self.register(board)
        submitted, _claim, _manifest, checkout, _seal, view, artifact = self.run_worktree(board)
        self.accept(board, view, artifact, target=self.target_with_artifact(artifact), keepCheckout=True)
        remove = workspace_module.cleanup_remove

        def crash_after_removal(*args, **kwargs):
            remove(*args, **kwargs)
            raise RuntimeError("simulated loss before the completion transaction")

        with mock.patch.object(workspace_module, "cleanup_remove", side_effect=crash_after_removal):
            with self.assertRaises(Exception):
                self.reclaim(board, self.view(board, submitted["runId"]))
        self.assertFalse(checkout.exists())
        self.assertEqual(self.view(board, submitted["runId"])["cleanup"]["state"], "applying")
        # Even after the internal admission window has lapsed, an applying reclaim
        # resumes to its recorded completion instead of deleting anything twice.
        clock.value = "2099-01-01T00:00:00.000Z"
        with mock.patch.object(workspace_module, "cleanup_remove", side_effect=AssertionError("second deletion")):
            recovered = self.reclaim(board, self.view(board, submitted["runId"]))
        self.assertTrue(recovered["removed"])
        self.assertTrue(recovered["alreadyRemoved"])
        self.assertEqual(self.view(board, submitted["runId"])["cleanup"]["state"], "applied")
        replay = self.reclaim(board, self.view(board, submitted["runId"]))
        self.assertTrue(replay["removed"])
        self.assertTrue(replay["alreadyRemoved"])
        self.assertTrue(Path(artifact["diffPath"]).is_file())


if __name__ == "__main__":
    unittest.main()
