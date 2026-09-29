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

    def test_out_of_scope_failure_preserves_conflict_instead_of_a_partial_output(self):
        module = SimpleNamespace(seal=Mock(side_effect=BoardError("WORKSPACE_SCOPE_VIOLATION", "outside declared scope")))
        result = AdapterOutcome("failed", result={"modelStarted": True}, shutdown_confirmed=True)
        with patch("buddy.workflow.workspace_module", return_value=module):
            capture(self.context(), result)
        self.assertEqual(result.result["partialSealError"]["code"], "WORKSPACE_SCOPE_VIOLATION")
        self.assertNotIn("partialWorkspaceSeal", result.result)
