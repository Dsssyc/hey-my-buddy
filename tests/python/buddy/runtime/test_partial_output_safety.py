"""A partial artifact never substitutes for process-stop or model-start evidence."""
from types import SimpleNamespace
import unittest
from unittest.mock import patch, Mock

from hey_my_buddy.buddy.harnesses.base import AdapterOutcome
from hey_my_buddy.errors import BoardError
from hey_my_buddy.buddy.runtime.execution import classify_termination
from hey_my_buddy.buddy.runtime.partial_outputs import capture, enrich


class PartialOutputSafetyTests(unittest.TestCase):
    def context(self):
        return SimpleNamespace(turn_input={"executionWorkspace": {"path": "/private/fixture"}},
                               environment={"HMB_STATE_DIR": "/private/fixture-state"}, task_id="run", attempt_id="attempt")

    def test_unconfirmed_stop_and_explicit_pre_model_failure_never_scan_checkout(self):
        for stopped, payload in ((False, {"modelStarted": True}), (True, {"modelStarted": False})):
            with self.subTest(stopped=stopped, payload=payload), patch("hey_my_buddy.blackboard.tasks.workflow.workspace_module") as workspace:
                result = AdapterOutcome("failed", result=payload, shutdown_confirmed=stopped)
                capture(self.context(), result)
                workspace.assert_not_called()
                self.assertNotIn("partialWorkspaceSeal", result.result)

    def test_model_started_without_changed_paths_publishes_no_partial_output(self):
        # The runners set modelStarted before the request is sent, so it only
        # guards retry; an unchanged checkout carries no work evidence to publish.
        module = SimpleNamespace(seal=Mock(return_value={"changedPaths": []}))
        result = AdapterOutcome("failed", result={"modelStarted": True, "code": "transport-error"},
                                shutdown_confirmed=True)
        with patch("hey_my_buddy.blackboard.tasks.workflow.workspace_module", return_value=module):
            capture(self.context(), result)
        module.seal.assert_called_once()
        for key in ("partialWorkspaceSeal", "workspaceManifest", "partialOutput"):
            self.assertNotIn(key, result.result)

    def test_changed_paths_publish_the_partial_output_regardless_of_model_state(self):
        for payload in ({"modelStarted": True, "code": "transport-error"},
                        {"modelStarted": None, "code": "native-exit"}, {}):
            with self.subTest(payload=payload), patch(
                    "hey_my_buddy.blackboard.tasks.workflow.workspace_module",
                    return_value=SimpleNamespace(seal=Mock(return_value={"changedPaths": ["src/one.py"]}))):
                result = AdapterOutcome("failed", result=dict(payload), shutdown_confirmed=True)
                capture(self.context(), result)
                self.assertEqual(result.result["partialWorkspaceSeal"]["changedPaths"], ["src/one.py"])
                self.assertEqual(result.result["workspaceManifest"], {"path": "/private/fixture"})
                self.assertEqual(result.result["partialOutput"],
                                 {"partial": True, "verified": False, "final": False})

    def test_out_of_scope_failure_preserves_conflict_instead_of_a_partial_output(self):
        error = BoardError("WORKSPACE_SCOPE_VIOLATION", "outside declared scope", paths=["src/forbidden.py"])
        module = SimpleNamespace(seal=Mock(side_effect=error))
        result = AdapterOutcome("failed", result={"modelStarted": True, "code": "transport-error"},
                                error="Native transport closed", shutdown_confirmed=True)
        with patch("hey_my_buddy.blackboard.tasks.workflow.workspace_module", return_value=module):
            capture(self.context(), result)
        self.assertEqual(result.result["partialSealError"], error.payload())
        self.assertEqual(result.error, "Native transport closed")
        self.assertEqual(classify_termination(result, timed_out=False, cancel_requested=False), "transport-error")
        self.assertEqual(classify_termination(result, timed_out=False, cancel_requested=True,
                                               stop_initiator="host"), "forced-stop")
        self.assertNotIn("partialWorkspaceSeal", result.result)

    def test_all_confirmed_host_stop_paths_seal_partial_changes(self):
        for reason in ("wrap-up", "grace-expired", "forced-stop"):
            with self.subTest(reason=reason):
                module = SimpleNamespace(seal=Mock(return_value={"changedPaths": ["src/one.py"]}))
                result = AdapterOutcome("cancelled", {"modelStarted": True, "stopReason": reason},
                                        shutdown_confirmed=True)
                with patch("hey_my_buddy.blackboard.tasks.workflow.workspace_module", return_value=module):
                    capture(self.context(), result)
                module.seal.assert_called_once()
                self.assertEqual(result.result["partialWorkspaceSeal"]["changedPaths"], ["src/one.py"])
                self.assertFalse(result.result["partialOutput"]["final"])
                self.assertFalse(result.result["partialOutput"]["verified"])

    def test_successful_turn_with_failed_cumulative_patch_is_a_workspace_failure(self):
        context = self.context()
        context.turn_input["context"] = {"microTaskInputCommit": "initial-commit"}
        seal = {"changedPaths": ["src/one.py"], "manifestSha256": "sealed"}
        result = AdapterOutcome("ok", result={"workspaceSeal": seal}, shutdown_confirmed=True)
        error = BoardError("WORKSPACE_PATCH_FAILED", "Git could not capture the patch", path="src/one.py")
        module = SimpleNamespace(cumulative_patch=Mock(side_effect=error))
        self.assertEqual(classify_termination(result, timed_out=False, cancel_requested=False), "completed")
        with patch("hey_my_buddy.blackboard.tasks.workflow.workspace_module", return_value=module):
            workspace_cause = enrich(context, result)
        module.cumulative_patch.assert_called_once_with(context.turn_input["executionWorkspace"], seal, "initial-commit")
        self.assertTrue(workspace_cause)
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.error, "Cumulative patch could not be preserved: Git could not capture the patch")
        self.assertEqual(result.result["cumulativePatchError"], error.payload())

    def test_cumulative_patch_failure_keeps_original_harness_error(self):
        context = self.context()
        context.turn_input["context"] = {"microTaskInputCommit": "initial-commit"}
        seal = {"changedPaths": ["src/one.py"], "manifestSha256": "sealed"}
        result = AdapterOutcome("failed", result={"workspaceSeal": seal, "modelStarted": True,
                                "code": "transport-error"}, error="Native transport closed", shutdown_confirmed=True)
        error = BoardError("WORKSPACE_PATCH_FAILED", "Git could not capture the patch")
        module = SimpleNamespace(cumulative_patch=Mock(side_effect=error), seal=Mock(return_value=seal))
        original_reason = classify_termination(result, timed_out=False, cancel_requested=False)
        with patch("hey_my_buddy.blackboard.tasks.workflow.workspace_module", return_value=module):
            workspace_cause = enrich(context, result)
        self.assertFalse(workspace_cause)
        self.assertEqual(result.status, "failed")
        self.assertIn("Native transport closed", result.error)
        self.assertIn("Git could not capture the patch", result.error)
        self.assertEqual(result.result["cumulativePatchError"], error.payload())
        self.assertEqual(original_reason, "transport-error")
        self.assertEqual(classify_termination(result, timed_out=False, cancel_requested=False), original_reason)

    def test_cumulative_patch_failure_keeps_cancelled_outcome(self):
        context = self.context()
        context.turn_input["context"] = {"microTaskInputCommit": "initial-commit"}
        seal = {"changedPaths": ["src/one.py"]}
        result = AdapterOutcome("cancelled", result={"workspaceSeal": seal, "modelStarted": True},
                                error="Stopped by Host", shutdown_confirmed=True)
        error = BoardError("WORKSPACE_PATCH_FAILED", "Git could not capture the patch", path="src/one.py")
        module = SimpleNamespace(cumulative_patch=Mock(side_effect=error), seal=Mock(return_value=seal))
        with patch("hey_my_buddy.blackboard.tasks.workflow.workspace_module", return_value=module):
            workspace_cause = enrich(context, result)
        self.assertFalse(workspace_cause)
        self.assertEqual(result.status, "cancelled")
        self.assertIn("Stopped by Host", result.error)
        self.assertEqual(result.result["cumulativePatchError"], error.payload())
        self.assertEqual(classify_termination(result, timed_out=False, cancel_requested=True,
                                               stop_initiator="host"), "forced-stop")

    def test_partial_seal_failure_keeps_harness_cause(self):
        error = BoardError("WORKSPACE_SCOPE_VIOLATION", "outside declared scope", paths=["src/forbidden.py"])
        result = AdapterOutcome("failed", result={"modelStarted": True, "code": "native-exit"},
                                error="Native runner exited", shutdown_confirmed=True)
        with patch("hey_my_buddy.blackboard.tasks.workflow.workspace_module",
                   return_value=SimpleNamespace(seal=Mock(side_effect=error))):
            capture(self.context(), result)
        self.assertEqual(result.error, "Native runner exited")
        self.assertEqual(result.result["partialSealError"], error.payload())
        self.assertEqual(classify_termination(result, timed_out=False, cancel_requested=False), "harness-error")

    def test_output_seal_failure_is_workspace_error_with_original_detail(self):
        result = AdapterOutcome("failed", result={"workspaceSealError": "WORKSPACE_SCOPE_VIOLATION: outside write scope"},
                                error="WORKSPACE_SCOPE_VIOLATION: outside write scope", shutdown_confirmed=True)
        self.assertEqual(classify_termination(result, timed_out=False, cancel_requested=False), "workspace-error")
        self.assertEqual(result.error, result.result["workspaceSealError"])
