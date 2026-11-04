"""Host actions address independently allocated helpers through their current root.

Acceptance, new scope and mechanical resolution retain the root/target relation;
the service reclaims terminal allocations from their own durable facts.
"""
from __future__ import annotations
from hey_my_buddy.protocol.workspace_manifest import turn_manifest, manifest_ref, record_reference, artifact_manifest
from unittest.mock import patch as defer_reclaim
import json
from pathlib import Path
from unittest import mock
from hey_my_buddy.blackboard.tasks import workspace as workspace_module
from hey_my_buddy.errors import BoardError

from blackboard.tasks.workspace_lifecycle_case import LifecycleTestCase
from hey_my_buddy.paths import native_path


class RootControlLifecycleTests(LifecycleTestCase):
    def test_root_control_targets_an_independently_allocated_helper(self):
        flow = self.root_with_helper_worktree()
        board, run_id, helper_id = flow["board"], flow["runId"], flow["helperId"]
        root_view, artifact = flow["rootView"], flow["artifact"]
        target = self.target_with_artifact(artifact)
        with defer_reclaim.object(board.store.workflow, "_reclaim_checkout", return_value={"removed": False, "reasons": ["fixture-retained"]}):
            accepted = board.call("workflow_accept", {
                "runId": run_id, "targetRunId": helper_id, "artifactId": artifact["artifactId"],
                "note": "reviewed the helper output",
                "target": {"path": str(target["path"]), "ref": "HEAD"},
                "beforeCommit": target["before"],
            })
        self.assertEqual(self.view(board, helper_id)["state"], "accepted")
        self.assertEqual(accepted["targetRunId"], helper_id)
        self.assertEqual(self.view(board, helper_id)["integrations"][0]["state"], "verified")
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
                "note": "wrong target", "notRequired": "wrong target",
            })
        self.assertEqual(raised.exception.code, "UNAUTHORIZED")
        # The kept helper checkout is reclaimed through the root capability alone.
        self.assertTrue(flow["helperCheckout"].exists())
        applied = board.store.workflow._reclaim_checkout(run_id=helper_id)
        self.assertTrue(applied["removed"])
        self.assertFalse(applied["alreadyRemoved"])
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
            "beforeCommit": target["before"],
        })
        self.assertEqual(self.view(board, helper_id)["state"], "accepted")
        # Deliver and accept the parent without cleaning the helper first. The
        # approval's auto continuation already requeued the parent for its next turn.
        root_view = self.view(board, run_id)
        self.assertNotEqual(root_view["state"], "accepted")
        parent_claim = self.claim(board, run_id, request_id="parent-claim-2", nonce="p" * 16)
        self.assertIsNotNone(parent_claim["claim"], parent_claim)
        parent_manifest = turn_manifest(self.directory, parent_claim["claim"]["turn"]["input"])
        native_path(Path(parent_manifest["path"]) / "parent-second.txt").write_text("parent second stage\n", encoding="utf-8", newline="\n")
        parent_seal = workspace_module.seal(self.directory, parent_manifest, run_id,
                                            parent_claim["claim"]["attempt"]["attemptId"])
        self.finish_turn(board, parent_claim, parent_seal, nonce="p" * 16)
        root_view = self.view(board, run_id)
        self.assertEqual(root_view["state"], "delivered")
        parent_artifact = next(row for row in root_view["artifacts"] if row["kind"] == "output")
        first_parent_artifact = next(row for row in flow["rootView"]["artifacts"] if row["kind"] == "output")
        parent_target = self.target_with_artifact(parent_artifact, name="parent-target",
                                                  previous=(first_parent_artifact,))
        with defer_reclaim.object(board.store.workflow, "_reclaim_checkout", return_value={"removed": False, "reasons": ["fixture-retained"]}):
            parent_accepted = board.call("workflow_accept", {
                "runId": run_id, "artifactId": parent_artifact["artifactId"], "note": "parent reviewed",
                "target": {"path": str(parent_target["path"]), "ref": "HEAD"},
                "beforeCommit": parent_target["before"],
            })
        self.assertEqual(parent_accepted["state"], "accepted")
        # The accepted helper allocation is still owned and still removable.
        root_view = self.view(board, run_id)
        applied = board.store.workflow._reclaim_checkout(run_id=helper_id)
        self.assertTrue(applied["removed"])
        self.assertFalse(flow["helperCheckout"].exists())
        self.assertTrue(flow["checkout"].exists())


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
                    "beforeCommit": target["before"],
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

    def test_root_control_amends_a_helper_scope(self):
        flow = self.root_with_helper_worktree(request_id="helper-amend")
        board, run_id, helper_id = flow["board"], flow["runId"], flow["helperId"]
        root_view = flow["rootView"]
        amended = self.continue_run(board, {'runId': run_id, 'targetRunId': helper_id, 'writeScope': ["docs", "."], 'note': "the helper may touch docs next", 'input': 'Continue with the revised scope'})
        self.assertEqual(self.view(board, helper_id)["scope"]["scopeVersion"], 2)
        self.assertEqual(amended["targetRunId"], helper_id)
        with board.store.db.read() as connection:
            stored = connection.execute(
                "SELECT write_scope_json FROM workflow_scope_versions WHERE run_id=? AND scope_version=2",
                (helper_id,),
            ).fetchone()
        self.assertCountEqual(json.loads(stored["write_scope_json"]), [".", "docs"])

    def test_root_control_resolves_a_helper_scope_conflict(self):
        flow = self.helper_scope_failure()
        board, run_id, helper_id = flow["board"], flow["runId"], flow["helperId"]
        root_view = self.view(board, run_id)
        helper_request = self.view(board, helper_id)["activeRequest"]
        self.assertIsNone(root_view["activeRequest"]["summary"])
        self.assertEqual(root_view["activeRequest"]["reason"], helper_request["reason"])
        self.assertEqual(root_view["activeRequest"]["origin"], {
            "runId": helper_id, "requestId": helper_request["requestId"]})
        with board.store.db.read() as connection:
            opened = connection.execute(
                "SELECT COUNT(*) FROM events WHERE task_id=? AND kind='workflow.boundary_updated'"
                " AND json_extract(payload_json,'$.activeRequestId')=?",
                (run_id, root_view["activeRequest"]["requestId"]),
            ).fetchone()[0]
        self.assertEqual(opened, 1, "settlement must not propagate the system request a second time")
        resolved = self.continue_run(board, {'runId': run_id, 'targetRunId': helper_id, 'paths': ["tracked.txt"], 'observedFingerprint': flow["conflict"]["observedFingerprint"], 'note': "restore the helper site to its authorized input", 'resolution': "restore", 'requestId': self.view(board, run_id)["activeRequest"]["requestId"]})
        self.assertEqual(resolved["targetRunId"], helper_id)
        self.assertEqual(resolved["resolutionState"], "restored")
        self.assertIn("tracked.txt", self.view(board, helper_id)["workspaceConflicts"][0]["resolvedPaths"])
        self.assertEqual((flow["helperCheckout"] / "tracked.txt").read_text(encoding="utf-8"), "base\n")
        self.assertEqual((flow["helperCheckout"] / "src" / "feature.py").read_text(encoding="utf-8"), "value = 2\n")
        with board.store.db.read() as connection:
            conflict_run = connection.execute(
                "SELECT run_id FROM workflow_workspace_conflicts WHERE conflict_id=?",
                (flow["conflict"]["conflictId"],),
            ).fetchone()["run_id"]
        self.assertEqual(conflict_run, helper_id)
