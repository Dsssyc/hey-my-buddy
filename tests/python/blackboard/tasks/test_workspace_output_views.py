"""Large stored workspace evidence stays readable through bounded Host views."""
import json
import unittest
from unittest import mock

from hey_my_buddy.blackboard.tasks import command_receipts
from hey_my_buddy.blackboard.tasks.workflow import WorkflowCoordinator
from hey_my_buddy.protocol.transport import MAX_MESSAGE_BYTES


def large_paths():
    # A path can be long and a rename-heavy result can contain thousands of
    # paths; the complete stored list exceeds the C-Two message ceiling.
    return [f"src/{index:04d}-" + "a" * 2990 for index in range(3000)]


class WorkspaceOutputViewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.paths = large_paths()
        assert len(json.dumps(cls.paths).encode()) > MAX_MESSAGE_BYTES

    def assert_bounded(self, value):
        self.assertLess(len(json.dumps(value).encode()), MAX_MESSAGE_BYTES)

    def test_output_artifact_preserves_stored_paths_and_exposes_patch(self):
        manifest = {
            "commit": "output-commit", "diffPath": "/private/output.patch", "diffSha256": "diff-digest",
            "changedPaths": self.paths,
            "cumulativePatch": {"path": "/private/cumulative.patch", "changedPaths": self.paths},
        }
        row = {"artifact_id": "artifact", "kind": "output", "attempt_id": "attempt", "turn_id": "turn",
               "run_id": "run", "manifest_sha256": "snapshot", "created_at": "now",
               "manifest_json": json.dumps(manifest)}
        view = WorkflowCoordinator._artifact_view(row)
        self.assertEqual(len(json.loads(row["manifest_json"])["changedPaths"]), 3000)
        self.assertEqual(view["changedPaths"], [path[:500] for path in self.paths[:32]])
        self.assertEqual(view["changedPathsCount"], 3000)
        self.assertTrue(view["changedPathsTruncated"])
        self.assertTrue(view["changedPathsSamplesShortened"])
        self.assertEqual(view["diffPath"], "/private/output.patch")
        self.assertEqual(view["cumulativePatch"]["changedPathsCount"], 3000)
        self.assertEqual(view["cumulativePatch"]["path"], "/private/cumulative.patch")
        self.assert_bounded({"artifacts": [view] * 32})

    def test_integration_acceptance_and_get_views_keep_complete_verification_in_row(self):
        verification = {"matchingPaths": self.paths, "differingPaths": [],
                        "missingPaths": [], "hostPaths": [], "note": None}
        row = {"integration_id": "integration", "run_id": "run", "artifact_id": "artifact",
               "strategy": "patch", "target_path": "/private/target", "target_ref": "HEAD",
               "target_repository_id": "repository", "target_checkout_id": "checkout",
               "source_commit": "source", "source_tree": "source-tree", "before_commit": "before",
               "after_commit": "after", "before_tree": "before-tree", "after_tree": "after-tree",
               "verification_json": json.dumps(verification), "note": "accepted", "actor_role": "host",
               "actor_id": "host-1", "created_at": "now"}
        connection = mock.Mock()
        connection.execute.return_value.fetchone.return_value = {"attempt_id": "attempt"}
        view = WorkflowCoordinator._integration_view(object.__new__(WorkflowCoordinator), connection, row)
        self.assertEqual(len(json.loads(row["verification_json"])["matchingPaths"]), 3000)
        self.assertEqual(view["verification"]["matchingPaths"], [path[:500] for path in self.paths[:32]])
        self.assertEqual(view["verification"]["matchingPathsCount"], 3000)
        self.assertTrue(view["verification"]["matchingPathsTruncated"])
        self.assertEqual(set(view["verification"]),
                         {"matchingPaths", "matchingPathsCount", "matchingPathsTruncated",
                          "matchingPathsSamplesShortened", "differingPaths", "differingPathsCount",
                          "differingPathsTruncated", "missingPaths", "missingPathsCount", "missingPathsTruncated",
                          "hostPaths", "hostPathsCount", "hostPathsTruncated", "note"})
        self.assertNotIn("notRequired", view)
        # Both get.integrations and accept.integration use this same projection.
        self.assert_bounded({"integrations": [view] * 8, "integration": view})

    def test_conflict_and_resolution_path_views_are_bounded(self):
        row = {"conflict_id": "conflict", "run_id": "run", "attempt_id": "attempt", "turn_id": "turn",
               "observed_fingerprint": "fingerprint", "resolved_paths_json": json.dumps(self.paths),
               "conflicting_paths_json": json.dumps(self.paths),
               "evidence_json": json.dumps({"manifestSha256": "manifest", "blockingPaths": self.paths,
                                            "entries": [{"path": path} for path in self.paths]}),
               "delivery_json": None, "state": "open", "action": None, "artifact_id": None,
               "actor_role": None, "actor_id": None,
               "note": None, "created_at": "now", "updated_at": "now"}
        view = WorkflowCoordinator._conflict_view(object.__new__(WorkflowCoordinator), mock.Mock(), row)
        self.assertEqual(view["blockingPathsCount"], 3000)
        self.assertEqual(view["resolvedPathsCount"], 3000)
        self.assertEqual(view["conflictingPathsCount"], 3000)
        self.assertEqual(view["pathsCount"], 3000)
        self.assertEqual(len(view["paths"][0]["path"]), 500)
        self.assert_bounded({"workspaceConflicts": [view]})

    def test_multibyte_path_samples_have_a_byte_limit(self):
        projected = WorkflowCoordinator._bounded_path_fields(
            {"changedPaths": ["目录/" + "界" * 1500]}, ("changedPaths",))
        self.assertLessEqual(len(projected["changedPaths"][0].encode()), 500)
        self.assertTrue(projected["changedPathsSamplesShortened"])

    def test_cleanup_plan_keeps_full_unsealed_evidence_and_bounds_reply(self):
        row = {"plan_id": "plan", "run_id": "run", "workspace_id": "workspace", "checkout_id": "checkout",
               "repository_id": "repository", "path": "/private/checkout", "kind": "worktree", "state": "blocked",
               "reasons_json": '["unsealed-changes"]',
               "evidence_json": json.dumps({"workspace": {"unsealedPaths": self.paths}}),
               "retention_json": "{}", "result_json": None, "revision": 2, "created_at": "now",
               "applied_at": None, "expires_at": "later"}
        view = WorkflowCoordinator._plan_view(row)
        self.assertEqual(len(json.loads(row["evidence_json"])["workspace"]["unsealedPaths"]), len(self.paths))
        self.assertEqual(view["evidence"]["workspace"]["unsealedPathsCount"], len(self.paths))
        self.assertEqual(len(view["evidence"]["workspace"]["unsealedPaths"]), 32)
        self.assert_bounded(view)

    def test_resolution_receipt_retains_full_operation_paths_and_bounds_replay(self):
        response = {"runId": "run", "targetRunId": "run", "conflictId": "conflict", "action": "restore",
                    "resolutionState": "restored", "resolvedPaths": self.paths, "preservedPaths": [],
                    "remainingPaths": [], "observedFingerprint": "before", "resolvedFingerprint": "after",
                    "artifactId": None, "nextBoundaryRequestId": None, "delivered": False}
        stored = command_receipts.stored_response("workflow.workspace_resolve", response)
        self.assertEqual(stored["resolvedPaths"], self.paths)
        coordinator = object.__new__(WorkflowCoordinator)
        connection = mock.Mock()
        connection.execute.return_value.fetchone.return_value = None
        with mock.patch.object(coordinator, "_run_row", return_value={"revision": 2}), \
             mock.patch.object(coordinator, "compact", return_value={"runId": "run"}):
            replay = command_receipts.replay_response(coordinator, connection, "workflow.workspace_resolve", stored)
        self.assertEqual(replay["resolvedPathsCount"], len(self.paths))
        self.assertEqual(len(replay["resolvedPaths"]), 32)
        self.assertTrue(replay["resolvedPathsTruncated"])
        self.assert_bounded(replay)


if __name__ == "__main__":
    unittest.main()
