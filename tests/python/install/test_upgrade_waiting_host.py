"""Host boundary admission and the frozen commandId requirement's scope blocker."""
from unittest import mock

from blackboard.tasks.test_workflow import WorkflowTestCase
from hey_my_buddy.blackboard.store import backup
from hey_my_buddy.errors import BoardError
from hey_my_buddy.install import launcher, upgrade


class WaitingHostUpgradeTests(WorkflowTestCase):
    def boundary(self, *, disposition="assistance"):
        board = self.board()
        run = self.submit(board)
        self.register(board)
        claimed = self.claim(board)
        self.finish_turn(board, claimed, disposition=disposition)
        return board, run, board.call("workflow_get", {"runId": run["runId"]})

    def snapshot(self, board):
        with board.store.db.read() as connection:
            return backup.database_snapshot(connection)

    def test_stopped_host_boundaries_are_idle_and_leave_the_board_untouched(self):
        board, run, view = self.boundary()
        self.assertEqual(view["state"], "awaiting-host")
        self.assertEqual(board.call("task_get", {"runId": run["runId"]})["task"]["state"], "queued")
        before = self.snapshot(board)
        self.assertEqual(upgrade.idle_snapshot(board.directory)["schema"], 18)
        self.assertEqual(self.snapshot(board), before)
        self.assertIsNone(self.claim(board, claim_request_id="host-cannot-progress")["claim"])

    def test_attention_boundary_is_idle(self):
        board, _run, view = self.boundary(disposition="attention")
        self.assertEqual(view["state"], "awaiting-host")
        upgrade.idle_snapshot(board.directory)

    def test_queued_continuation_still_refuses_without_cancellation(self):
        board, run, view = self.boundary()
        self.decide(board, view, view["activeRequest"]["requestId"], autoContinue=False)
        view = board.call("workflow_get", {"runId": run["runId"]})
        self.continue_run(board, view)
        before = self.snapshot(board)
        with self.assertRaises(BoardError) as caught:
            upgrade.idle_snapshot(board.directory)
        self.assertEqual(caught.exception.code, "UPGRADE_NOT_IDLE")
        self.assertEqual(caught.exception.details["active"][0]["runId"], run["runId"])
        self.assertEqual(self.snapshot(board), before)
        with board.store.db.read() as connection:
            self.assertEqual(connection.execute("SELECT state FROM workflow_continuations WHERE run_id=?",
                                                (run["runId"],)).fetchone()[0], "queued")

    def test_recorded_continuation_at_a_host_boundary_still_blocks(self):
        board, run, view = self.boundary()
        self.decide(board, view, view["activeRequest"]["requestId"])
        # Preserve a pending automatic continuation while its run is at a Host
        # boundary: admission must inspect the pending work, not the label alone.
        with board.store.db.write() as connection:
            connection.execute("UPDATE workflow_runs SET state='awaiting-host' WHERE run_id=?", (run["runId"],))
            connection.execute("UPDATE workflow_continuations SET state='recorded' WHERE run_id=?", (run["runId"],))
        before = self.snapshot(board)
        with self.assertRaises(BoardError) as caught:
            upgrade.idle_snapshot(board.directory)
        self.assertEqual(caught.exception.code, "UPGRADE_NOT_IDLE")
        self.assertEqual(self.snapshot(board), before)

    def test_queued_and_running_work_both_refuse_without_cancellation(self):
        board = self.board()
        run = self.submit(board)
        self.register(board)
        for state in ("queued", "running"):
            with self.subTest(state=state):
                if state == "running":
                    self.assertIsNotNone(self.claim(board)["claim"])
                before = self.snapshot(board)
                with self.assertRaises(BoardError) as caught:
                    upgrade.idle_snapshot(board.directory)
                self.assertEqual(caught.exception.code, "UPGRADE_NOT_IDLE")
                self.assertEqual(caught.exception.details["active"][0]["state"], state)
                self.assertEqual(self.snapshot(board), before)
                task = board.call("task_get", {"runId": run["runId"]})["task"]
                self.assertEqual(task["state"], state)
                with board.store.db.read() as connection:
                    self.assertFalse(any(row[0] for row in connection.execute("SELECT cancel_requested_at FROM attempts")))

    def test_host_boundary_with_unconfirmed_stop_refuses(self):
        board, _run, _view = self.boundary()
        with board.store.db.write() as connection:
            connection.execute("UPDATE attempts SET shutdown_confirmed=0")
        before = self.snapshot(board)
        with self.assertRaises(BoardError) as caught:
            upgrade.idle_snapshot(board.directory)
        self.assertEqual(caught.exception.code, "UPGRADE_NOT_IDLE")
        self.assertTrue(caught.exception.details["unresolvedAttempts"])
        self.assertEqual(self.snapshot(board), before)

    def test_all_host_commands_are_fenced_without_recording_anything(self):
        board, run, _view = self.boundary()
        upgrade.write_journal(board.directory / "upgrade.json", {})
        before = self.snapshot(board)
        for operation in ("workflow_decide", "workflow_continue", "workflow_accept", "workflow_conclude",
                          "workflow_takeover", "workflow_cancel", "workflow_scope_amend", "inquiry_observe",
                          "workflow_submit"):
            with self.subTest(operation=operation):
                with self.assertRaises(BoardError) as caught:
                    board.call(operation, {"runId": run["runId"], "commandId": "same-command"})
                self.assertEqual(caught.exception.code, "UPGRADE_IN_PROGRESS")
                self.assertIn("retry", str(caught.exception))
                self.assertEqual(self.snapshot(board), before)

    def test_four_public_operations_do_not_accept_the_required_command_id(self):
        # This characterizes a scope blocker, not successful upgrade replay.
        # Their native identities differ; changing these validators is outside
        # this micro task's install-only source write scope.
        board, run, _view = self.boundary()
        for operation in ("workflow_accept", "workflow_conclude", "inquiry_observe", "workflow_submit"):
            with self.subTest(operation=operation):
                before = self.snapshot(board)
                with self.assertRaises(BoardError) as caught:
                    board.call(operation, {"runId": run["runId"], "commandId": "same-command"})
                self.assertEqual(caught.exception.code, "INVALID_ARGUMENT")
                self.assertIn("commandId", str(caught.exception))
                self.assertEqual(self.snapshot(board), before)

    def test_launcher_fence_explains_retry(self):
        import contextlib
        import io
        import json
        import os

        board = self.board()
        upgrade.write_journal(board.directory / "upgrade.json", {})
        output = io.StringIO()
        with mock.patch.dict(os.environ, {"BUDDY_STATE_DIR": str(board.directory)}), \
                contextlib.redirect_stdout(output):
            self.assertEqual(launcher.main(["decide", "{}"]), 1)
        error = json.loads(output.getvalue())["error"]
        self.assertEqual(error["code"], "UPGRADE_IN_PROGRESS")
        self.assertIn("retry the same command", error["message"])
