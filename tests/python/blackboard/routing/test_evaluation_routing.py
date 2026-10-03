"""Evaluation attribution follows the attempt, not a later route choice."""
import json

from blackboard.tasks.test_workflow import CONFIGURATION, WorkflowTestCase


class FrozenEvaluationIdentityTests(WorkflowTestCase):
    def test_partial_original_goal_records_the_frozen_attempt_configuration(self):
        board = self.board()
        submitted = board.call("workflow_submit", {
            "requestId": "unspecified-model", "hostId": "host-1", "task": "produce an artifact",
            "cwd": str(self.workdir()), "executionWorkspace": {"kind": "existing", "access": "write"},
        })
        self.controls[submitted["runId"]] = submitted["control"]
        self.assertTrue(submitted["activeRequest"]["routing"])
        self.continue_run(board, submitted, configuration=CONFIGURATION)
        self.register(board)
        claim = self.claim(board)
        self.finish_turn(board, claim)
        with board.store.db.read() as connection:
            task = connection.execute("SELECT * FROM tasks WHERE task_id=?", (submitted["runId"],)).fetchone()
            self.assertNotIn("model", json.loads(task["spec_json"]))
            actual = board.evaluation._task_identity(connection, submitted["runId"])
            self.assertEqual(actual["requested"], CONFIGURATION)
            # A task's mutable execution projection can refer to a later choice;
            # attribution of this named attempt still reads its frozen turn input.
            later = {**dict(task), "adapter": "zcode", "spec_json": json.dumps({"provider": "later", "model": "later", "effort": "high"})}
            attempt = board.store._selected_attempt(connection, task)
            frozen = board.evaluation._attempt_identity(connection, later, attempt)
            self.assertEqual(frozen["requested"], CONFIGURATION)
            self.assertEqual(frozen["adapter"], "dsh")
