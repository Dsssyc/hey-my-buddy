"""Service shutdown fences micro-task work through the authoritative workflow path."""
from __future__ import annotations

from unittest.mock import patch

from hey_my_buddy.blackboard.service.daemon import Daemon
from hey_my_buddy.blackboard.store import backup
from hey_my_buddy.errors import BoardError
from hey_my_buddy.install import upgrade
from blackboard.tasks.test_workflow import WorkflowTestCase
from support import request_private_service_stop


class MicroTaskServiceStopTests(WorkflowTestCase):
    def stop_daemon(self, note="test stop"):
        daemon = Daemon(self.directory)
        self.addCleanup(daemon.store.db.close)
        with patch.object(daemon.store, "task_cancel", wraps=daemon.store.task_cancel) as cancel:
            reply = daemon.on_stop({"note": note, "drainSeconds": 0})
        cancel.assert_not_called()
        self.assertEqual(daemon.store.service_issues(), [])
        return reply

    def test_host_boundary_only_stop_is_idle_and_preserves_the_board(self):
        board = self.board()
        for disposition in ("assistance", "attention"):
            run = self.submit(board, request_id=disposition, cwd=str(self.workdir(disposition)))
            claimed = self.claim(board, run_id=run["runId"])
            self.finish_turn(board, claimed, disposition=disposition)
            board.store.complete_execution_cleanup(claimed["claim"]["attempt"]["attemptId"])
        with board.store.db.read() as connection:
            before = backup.database_snapshot(connection)
        upgrade.idle_snapshot(board.directory)
        reply = self.stop_daemon()
        self.assertTrue(reply["stopped"])
        self.assertEqual((reply["cancelledQueued"], reply["cancelRequested"]), (0, 0))
        with board.store.db.read() as connection:
            self.assertEqual(backup.database_snapshot(connection), before)

        board.store.record_service_issue("cancel-queued", BoardError("UNAUTHORIZED", "earlier stop"))
        self.stop_daemon()
        with board.store.db.read() as connection:
            self.assertEqual(connection.execute(
                "SELECT kind FROM events ORDER BY seq DESC LIMIT 1").fetchone()[0], "service.issue_resolved")
            self.assertEqual([row[0] for row in connection.execute("SELECT state FROM workflow_runs")],
                             ["awaiting-host", "awaiting-host"])

    def test_pending_continuations_block_install_and_stop_through_workflow(self):
        board = self.board()
        runs = {}
        for state in ("recorded", "queued", "executing", "passive"):
            run = self.submit(board, request_id=state, cwd=str(self.workdir(state)))
            runs[state] = run["runId"]
            claimed = self.claim(board, run_id=run["runId"])
            self.finish_turn(board, claimed, disposition="assistance")
            board.store.complete_execution_cleanup(claimed["claim"]["attempt"]["attemptId"])
            if state == "passive":
                continue
            view = board.call("workflow_get", {"runId": run["runId"]})
            self.decide(board, view, view["activeRequest"]["requestId"], command_id=state)
            if state in ("recorded", "queued"):
                # Keep the durable continuation before its run leaves the Host boundary.
                with board.store.db.write() as connection:
                    connection.execute("UPDATE workflow_runs SET state='awaiting-host' WHERE run_id=?", (run["runId"],))
                    connection.execute("UPDATE workflow_continuations SET state=? WHERE run_id=?", (state, run["runId"]))
        ordinary = board.submit_execution(**{
            "requestId": "ordinary", "adapter": "command", "task": "plain",
            "cwd": str(self.workdir("ordinary")), "argv": ["/bin/true"],
        })["task"]["runId"]
        with self.assertRaises(BoardError) as caught:
            upgrade.idle_snapshot(board.directory)
        self.assertEqual(caught.exception.code, "UPGRADE_NOT_IDLE")
        self.assertEqual({task["runId"] for task in caught.exception.details["active"]},
                         {runs["recorded"], runs["queued"], runs["executing"], ordinary})

        daemon = Daemon(self.directory)
        self.addCleanup(daemon.store.db.close)
        with patch.object(daemon.store, "task_cancel", wraps=daemon.store.task_cancel) as cancel:
            reply = daemon.on_stop({"note": "test stop", "drainSeconds": 0})
        cancel.assert_called_once_with({"runId": ordinary, "note": "test stop"}, initiator="service")
        self.assertEqual(daemon.store.service_issues(), [])
        self.assertEqual(reply["cancelledQueued"], 4)
        self.assertEqual(board.call("task_get", {"runId": ordinary})["task"]["state"], "cancelled")
        for state, run_id in runs.items():
            self.assertEqual(board.call("workflow_get", {"runId": run_id})["state"],
                             "awaiting-host" if state == "passive" else "cancelled")
        with board.store.db.read() as connection:
            self.assertEqual(connection.execute(
                "SELECT COUNT(*) FROM workflow_continuations WHERE state IN ('recorded','queued')").fetchone()[0], 0)
        upgrade.idle_snapshot(board.directory)

    def test_queued_micro_task_is_fenced_but_passive_host_request_is_preserved(self):
        board = self.board()
        queued = self.submit(board, request_id="queued", cwd=str(self.workdir("queued")))
        passive = self.submit(board, request_id="passive", cwd=str(self.workdir("passive")))
        delivered = self.submit(board, request_id="delivered", cwd=str(self.workdir("delivered")))
        self.register(board)
        self.finish_turn(board, self.claim(board, run_id=passive["runId"]), disposition="assistance")
        self.finish_turn(board, self.claim(board, claim_request_id="delivered-claim", run_id=delivered["runId"]))
        self.assertEqual(board.call("workflow_get", {"runId": passive["runId"]})["state"], "awaiting-host")
        self.assertEqual(board.call("workflow_get", {"runId": delivered["runId"]})["state"], "delivered")

        reply = self.stop_daemon()
        self.assertEqual(reply["cancelledQueued"], 1)
        self.assertEqual(board.call("workflow_get", {"runId": queued["runId"]})["state"], "cancelled")
        self.assertEqual(board.call("workflow_get", {"runId": passive["runId"]})["state"], "awaiting-host")
        self.assertEqual(board.call("workflow_get", {"runId": delivered["runId"]})["state"], "delivered")
        events = board.call("events_read", {"runId": queued["runId"]})["events"]
        self.assertEqual([event["payload"]["actor"] for event in events if event["kind"] == "workflow.cancelled"],
                         [{"role": "service", "id": "stop"}])
        repeated = self.stop_daemon()
        self.assertEqual(repeated["cancelledQueued"], 0)
        events = board.call("events_read", {"runId": queued["runId"]})["events"]
        self.assertEqual(sum(event["kind"] == "workflow.cancelled" for event in events), 1)

    def test_running_helper_behind_yielded_parent_requires_real_stop_receipt(self):
        board = self.board()
        self.register(board)
        parent = self.submit(board)
        self.finish_turn(board, self.claim(board), disposition="assistance")
        waiting = board.call("workflow_get", {"runId": parent["runId"]})
        approved = self.decide(board, waiting, waiting["activeRequest"]["requestId"], helpers=[{
            "requestId": "helper-1", "task": "helper", "cwd": str(self.workdir("helper")),
            "executionWorkspace": {"kind": "worktree", "access": "write"},
        }])
        child_id = approved["children"][0]["taskId"]
        claim = self.claim(board, claim_request_id="helper-claim", run_id=child_id)

        reply = self.stop_daemon()
        self.assertFalse(reply["stopped"])
        self.assertEqual(reply["cancelRequested"], 1)
        self.assertEqual(reply["unresolvedAttempts"][0]["taskId"], child_id)
        view = board.call("workflow_get", {"runId": parent["runId"]})
        self.assertEqual(view["state"], "cancelled")
        self.assertEqual(view["children"][0]["state"], "cancelled")
        self.assertFalse(view["shutdown"]["descendantsConfirmed"])
        events = board.call("events_read", {"runId": child_id})["events"]
        self.assertEqual([event["payload"]["actor"] for event in events if event["kind"] == "workflow.helper_cancelled"],
                         [{"role": "service", "id": "stop"}])
        with board.store.db.read() as connection:
            attempt = connection.execute("SELECT cancel_requested_at FROM attempts WHERE task_id=?", (child_id,)).fetchone()
            self.assertIsNotNone(attempt["cancel_requested_at"])
        self.finish_turn(board, claim)
        settled = board.call("workflow_get", {"runId": parent["runId"]})
        self.assertTrue(settled["shutdown"]["descendantsConfirmed"])

    def test_public_task_cancel_cannot_bypass_micro_task_control(self):
        board = self.board()
        self.register(board)
        submitted = self.submit(board)
        claim = self.claim(board)
        for params, expected in (({}, "UNAUTHORIZED"), (self.control(submitted), "MICRO_TASK_REQUIRED"),
                                 ({"credential": claim["claim"]["agentCredential"]}, "FORBIDDEN")):
            with self.subTest(params=params):
                with self.assertRaises(BoardError) as raised:
                    board.call("task_cancel", {"runId": submitted["runId"], **params})
                self.assertEqual(raised.exception.code, expected)
        with self.assertRaises(BoardError) as raised:
            request_private_service_stop(self.directory, board.call, credential=claim["claim"]["agentCredential"])
        self.assertEqual(raised.exception.code, "FORBIDDEN")
        ordinary = board.submit_execution(**{
            "requestId": "ordinary", "adapter": "command", "task": "plain",
            "cwd": str(self.workdir("ordinary")), "argv": ["/bin/true"],
        })
        self.assertEqual(board.call("task_cancel", {"runId": ordinary["task"]["runId"]})["task"]["state"],
                         "cancelled")
