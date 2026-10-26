"""The console pairs a delivered continuation's adjacent review and input events."""
from unittest.mock import patch

from blackboard.tasks.test_workflow import WorkflowTestCase


class TimelineActionPairingTests(WorkflowTestCase):
    def delivered(self, board):
        submitted = self.submit(board, macroTask={"title": "Timeline action pairing"})
        self.finish_turn(board, self.claim(board))
        return board.call("workflow_get", {"runId": submitted["runId"]})

    def test_delivered_continuation_writes_adjacent_rejection_and_continuation_in_one_transaction(self):
        board = self.board()
        delivered = self.delivered(board)
        appended = []
        append = board.store._append_event

        def observe(connection, kind, **facts):
            seq = append(connection, kind, **facts)
            if kind in ("workflow.review_rejected", "workflow.continued"):
                appended.append((connection, connection.in_transaction, seq, kind, facts))
            return seq

        with patch.object(board.store, "_append_event", side_effect=observe):
            self.continue_run(board, delivered, input="Address review", note="Needs work")
        self.assertEqual([item[3] for item in appended], ["workflow.review_rejected", "workflow.continued"])
        rejected, continued = appended
        self.assertIs(rejected[0], continued[0])
        self.assertTrue(rejected[1] and continued[1])
        self.assertEqual(continued[2], rejected[2] + 1)
        self.assertEqual(rejected[4]["task_id"], continued[4]["task_id"])
        self.assertEqual(rejected[4]["revision"], continued[4]["revision"])
        timeline = board.call("macro_task_timeline", {"macroTaskId": delivered["macroTaskId"]})
        pair = [event for event in timeline["events"] if event["seq"] in (rejected[2], continued[2])]
        self.assertEqual([event["kind"] for event in pair], ["reject", "continue"])
        self.assertEqual(pair[0]["artifactId"], delivered["finalArtifactId"])
        self.assertEqual(pair[0]["note"], "Needs work")
        self.assertIsNotNone(pair[1]["continuationId"])

    def test_failure_after_the_pair_rolls_back_both_events(self):
        board = self.board()
        delivered = self.delivered(board)
        append = board.store._append_event

        def fail_after_continued(connection, kind, **facts):
            seq = append(connection, kind, **facts)
            if kind == "workflow.continued":
                raise RuntimeError("synthetic failure before commit")
            return seq

        with patch.object(board.store, "_append_event", side_effect=fail_after_continued):
            with self.assertRaisesRegex(RuntimeError, "synthetic failure before commit"):
                self.continue_run(board, delivered, input="Address review", note="Needs work")
        with board.store.db.read() as connection:
            self.assertEqual(connection.execute(
                "SELECT COUNT(*) FROM events WHERE task_id=? AND kind IN ('workflow.review_rejected','workflow.continued')",
                (delivered["runId"],),
            ).fetchone()[0], 0)
        current = board.call("workflow_get", {"runId": delivered["runId"]})
        self.assertEqual(current["state"], "delivered")
        self.assertIsNone(current["task"]["reviewRejectedAt"])
