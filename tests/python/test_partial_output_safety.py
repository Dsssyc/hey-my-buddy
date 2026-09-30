"""A partial artifact never substitutes for process-stop or model-start evidence."""
from types import SimpleNamespace
import unittest
from unittest.mock import patch, Mock

from buddy.adapters.base import AdapterOutcome
from buddy.errors import BoardError
from buddy.partial_outputs import capture


class PartialOutputSafetyTests(unittest.TestCase):
    def context(self):
        return SimpleNamespace(turn_input={"executionWorkspace": {"path": "/private/fixture"}},
                               environment={"BUDDY_STATE_DIR": "/private/fixture-state"}, task_id="run", attempt_id="attempt")

    def test_unconfirmed_stop_and_explicit_pre_model_failure_never_scan_checkout(self):
        for stopped, payload in ((False, {"modelStarted": True}), (True, {"modelStarted": False})):
            with self.subTest(stopped=stopped, payload=payload), patch("buddy.workflow.workspace_module") as workspace:
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
        with patch("buddy.workflow.workspace_module", return_value=module):
            capture(self.context(), result)
        module.seal.assert_called_once()
        for key in ("partialWorkspaceSeal", "workspaceManifest", "partialOutput"):
            self.assertNotIn(key, result.result)

    def test_changed_paths_publish_the_partial_output_regardless_of_model_state(self):
        for payload in ({"modelStarted": True, "code": "transport-error"},
                        {"modelStarted": None, "code": "native-exit"}, {}):
            with self.subTest(payload=payload), patch(
                    "buddy.workflow.workspace_module",
                    return_value=SimpleNamespace(seal=Mock(return_value={"changedPaths": ["src/one.py"]}))):
                result = AdapterOutcome("failed", result=dict(payload), shutdown_confirmed=True)
                capture(self.context(), result)
                self.assertEqual(result.result["partialWorkspaceSeal"]["changedPaths"], ["src/one.py"])
                self.assertEqual(result.result["workspaceManifest"], {"path": "/private/fixture"})
                self.assertEqual(result.result["partialOutput"],
                                 {"partial": True, "verified": False, "final": False,
                                  "reason": payload.get("code") or "failed"})

    def test_out_of_scope_failure_preserves_conflict_instead_of_a_partial_output(self):
        module = SimpleNamespace(seal=Mock(side_effect=BoardError("WORKSPACE_SCOPE_VIOLATION", "outside declared scope")))
        result = AdapterOutcome("failed", result={"modelStarted": True}, shutdown_confirmed=True)
        with patch("buddy.workflow.workspace_module", return_value=module):
            capture(self.context(), result)
        self.assertEqual(result.result["partialSealError"]["code"], "WORKSPACE_SCOPE_VIOLATION")
        self.assertNotIn("partialWorkspaceSeal", result.result)
