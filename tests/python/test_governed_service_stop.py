"""Service shutdown fences governed work through the authoritative workflow path."""
from __future__ import annotations

from hey_my_buddy.blackboard.service.daemon import Daemon
from hey_my_buddy.errors import BoardError
from test_workflow import WorkflowTestCase


class GovernedServiceStopTests(WorkflowTestCase):
    def stop_daemon(self, reason="test stop"):
        daemon = Daemon(self.directory)
        return daemon.on_stop({"reason": reason, "drainSeconds": 0})

    def test_queued_goal_is_fenced_but_passive_host_request_is_preserved(self):
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
                         ["service-stop"])
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
                         ["service-stop"])
        with board.store.db.read() as connection:
            attempt = connection.execute("SELECT cancel_requested_at FROM attempts WHERE task_id=?", (child_id,)).fetchone()
            self.assertIsNotNone(attempt["cancel_requested_at"])
        self.finish_turn(board, claim)
        settled = board.call("workflow_get", {"runId": parent["runId"]})
        self.assertTrue(settled["shutdown"]["descendantsConfirmed"])

    def test_public_task_cancel_cannot_bypass_governed_control(self):
        board = self.board()
        self.register(board)
        submitted = self.submit(board)
        claim = self.claim(board)
        for params, expected in (({}, "UNAUTHORIZED"), (self.control(submitted), "GOVERNED_REQUIRED"),
                                 ({"credential": claim["claim"]["agentCredential"]}, "FORBIDDEN")):
            with self.subTest(params=params):
                with self.assertRaises(BoardError) as raised:
                    board.call("task_cancel", {"runId": submitted["runId"], **params})
                self.assertEqual(raised.exception.code, expected)
        with self.assertRaises(BoardError) as raised:
            board.call("service_control", {"action": "stop", "credential": claim["claim"]["agentCredential"]})
        self.assertEqual(raised.exception.code, "FORBIDDEN")
        ordinary = board.call("task_submit", {
            "requestId": "ordinary", "adapter": "external", "task": "plain", "cwd": str(self.workdir("ordinary")),
        })
        self.assertEqual(board.call("task_cancel", {"runId": ordinary["task"]["runId"]})["task"]["state"],
                         "cancelled")
