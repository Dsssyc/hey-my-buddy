"""Host boundary admission and the shared upgrade entry fence."""
from unittest import mock

from blackboard.tasks.test_workflow import BUDDY, WorkflowTestCase
from hey_my_buddy.blackboard.store import backup
from hey_my_buddy.blackboard.store.db import SCHEMA_VERSION
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
        self.assertEqual(upgrade.idle_snapshot(board.directory)["schema"], SCHEMA_VERSION)
        self.assertEqual(self.snapshot(board), before)
        self.assertIsNone(self.claim(board, claim_request_id="host-cannot-progress")["claim"])

    def test_attention_boundary_is_idle(self):
        board, _run, view = self.boundary(disposition="attention")
        self.assertEqual(view["state"], "awaiting-host")
        upgrade.idle_snapshot(board.directory)

    def test_schema_18_archive_refuses_a_stopped_host_boundary_without_changes(self):
        board, run, view = self.boundary()
        with board.store.db.write() as connection:
            connection.execute("UPDATE meta SET value='18' WHERE key='schema_version'")
        before = self.snapshot(board)
        files_before = self.files_snapshot(board)
        with self.assertRaises(BoardError) as caught:
            upgrade.require_archive_readiness(board.directory)
        self.assertEqual(caught.exception.code, "UPGRADE_NOT_IDLE")
        self.assertIn({"runId": run["runId"], "state": view["state"]}, caught.exception.details["active"])
        self.assertEqual(self.snapshot(board), before)
        self.assertEqual(self.files_snapshot(board), files_before)

    def files_snapshot(self, board):
        return {str(path.relative_to(board.directory)): path.read_bytes()
                for path in board.directory.rglob('*') if path.is_file()}

    def test_schema_18_archive_refuses_queued_and_running_work_without_file_changes(self):
        board = self.board()
        run = self.submit(board)
        self.register(board)
        for state in ('queued', 'running'):
            if state == 'running':
                self.assertIsNotNone(self.claim(board)['claim'])
            with board.store.db.write() as connection:
                connection.execute("UPDATE meta SET value='18' WHERE key='schema_version'")
            before = self.files_snapshot(board)
            with self.assertRaises(BoardError) as caught:
                upgrade.require_archive_readiness(board.directory)
            self.assertEqual(caught.exception.code, 'UPGRADE_NOT_IDLE')
            self.assertIn(run['runId'], [item['runId'] for item in caught.exception.details['active']])
            self.assertEqual(self.files_snapshot(board), before)

    def test_archive_admission_reads_live_wal_without_changing_its_sidecars(self):
        board = self.board()
        run = self.submit(board)
        with board.store.db.connect() as writer:
            writer.execute("UPDATE meta SET value='18' WHERE key='schema_version'")
            before = self.files_snapshot(board)
            self.assertTrue((board.directory / 'board.sqlite3-wal').stat().st_size)
            with self.assertRaises(BoardError) as caught:
                upgrade.require_archive_readiness(board.directory)
            self.assertEqual(caught.exception.code, 'UPGRADE_NOT_IDLE')
            self.assertIn(run['runId'], [item['runId'] for item in caught.exception.details['active']])
            self.assertEqual(self.files_snapshot(board), before)

    def test_schema_18_archive_refuses_waiting_helpers_without_file_changes(self):
        board, run, _view = self.boundary()
        with board.store.db.write() as connection:
            connection.execute("UPDATE meta SET value='18' WHERE key='schema_version'")
            connection.execute("UPDATE workflow_runs SET state='waiting-helpers' WHERE run_id=?", (run['runId'],))
        before = self.files_snapshot(board)
        with self.assertRaises(BoardError) as caught:
            upgrade.require_archive_readiness(board.directory)
        self.assertIn({'runId': run['runId'], 'state': 'waiting-helpers'}, caught.exception.details['active'])
        self.assertEqual(self.files_snapshot(board), before)

    def test_schema_18_archive_accepts_terminal_runs(self):
        board, run, _view = self.boundary()
        with board.store.db.write() as connection:
            connection.execute("UPDATE meta SET value='18' WHERE key='schema_version'")
        for terminal in ("accepted", "cancelled", "failed"):
            with self.subTest(state=terminal):
                with board.store.db.write() as connection:
                    connection.execute("UPDATE workflow_runs SET state=? WHERE run_id=?", (terminal, run["runId"]))
                    connection.execute("UPDATE tasks SET state=? WHERE task_id=?",
                                       ("completed" if terminal == "accepted" else terminal, run["runId"]))
                upgrade.require_archive_readiness(board.directory)

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

    def test_schema_18_archive_refuses_an_unconfirmed_attempt(self):
        board, _run, _view = self.boundary()
        with board.store.db.write() as connection:
            connection.execute("UPDATE meta SET value='18' WHERE key='schema_version'")
            connection.execute("UPDATE attempts SET shutdown_confirmed=0")
        before = self.snapshot(board)
        files_before = self.files_snapshot(board)
        with self.assertRaises(BoardError) as caught:
            upgrade.require_archive_readiness(board.directory)
        self.assertEqual(caught.exception.code, "UPGRADE_NOT_IDLE")
        self.assertTrue(caught.exception.details["unresolvedAttempts"])
        self.assertEqual(self.snapshot(board), before)
        self.assertEqual(self.files_snapshot(board), files_before)

    def test_schema_18_archive_refuses_a_managed_checkout(self):
        board = self.board()
        with board.store.db.write() as connection:
            connection.execute("UPDATE meta SET value='18' WHERE key='schema_version'")
        checkout = board.directory / "workspaces" / "ws-private" / "checkout"
        checkout.mkdir(parents=True)
        before = self.files_snapshot(board)
        with self.assertRaises(BoardError) as caught:
            upgrade.require_archive_readiness(board.directory)
        self.assertEqual(caught.exception.code, "UPGRADE_NOT_IDLE")
        self.assertEqual(caught.exception.details["managedCheckouts"],
                         [{"workspaceId": "ws-private", "path": str(checkout)}])
        self.assertEqual(self.files_snapshot(board), before)

    def test_all_host_commands_are_fenced_without_recording_anything(self):
        board, run, view = self.boundary()
        upgrade.write_journal(board.directory / "upgrade.json", {})
        before = self.snapshot(board)
        controlled = {"runId": run["runId"], **self.control(view)}
        command = {**controlled, "commandId": "same-command"}
        calls = (
            ("workflow_decide", {**command, "requestId": view["activeRequest"]["requestId"],
                                 "expectedRevision": view["revision"], "decision": "approve"}),
            ("workflow_continue", {**command, "expectedRevision": view["revision"],
                                   "input": "continue", "helperPolicy": "keep"}),
            ("workflow_accept", {**controlled, "artifactId": "fixture-output", "note": "reviewed", "notRequired": "fixture"}),
            ("workflow_conclude", {**controlled, "note": "reviewed"}),
            ("workflow_takeover", {**command, "expectedOwnerGeneration": 1, "newHostId": "host-2"}),
            ("workflow_cancel", command),
            ("workflow_scope_amend", {**command, "expectedRevision": view["revision"],
                                      "expectedScopeVersion": 1, "writeScope": ["src"], "reason": "reviewed"}),
            ("inquiry_observe", {"runId": run["runId"], "inquiryId": "same-inquiry", "question": "status?"}),
            ("workflow_submit", {**BUDDY, "requestId": "same-submission", "submissionToken": "t" * 32,
                                  "hostId": "host-1", "task": "fixture", "cwd": str(self.workdir()),
                                  "executionWorkspace": {"kind": "existing", "access": "write"}}),
        )
        for operation, params in calls:
            with self.subTest(operation=operation):
                with self.assertRaises(BoardError) as caught:
                    board.call(operation, params)
                self.assertEqual(caught.exception.code, "UPGRADE_IN_PROGRESS")
                self.assertIn("retry", str(caught.exception))
                self.assertEqual(self.snapshot(board), before)

    def test_launcher_fence_explains_retry(self):
        import contextlib
        import io
        import json
        import os

        board = self.board()
        upgrade.write_journal(board.directory / "upgrade.json", {})
        output = io.StringIO()
        with mock.patch.dict(os.environ, {"HMB_STATE_DIR": str(board.directory)}), \
                contextlib.redirect_stdout(output):
            self.assertEqual(launcher.main(["decide", "{}"]), 1)
        error = json.loads(output.getvalue())["error"]
        self.assertEqual(error["code"], "UPGRADE_IN_PROGRESS")
        self.assertIn("retry the same command", error["message"])
