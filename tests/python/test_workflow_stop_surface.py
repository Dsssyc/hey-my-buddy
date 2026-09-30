"""Public receipt/wait boundaries preserve whole-goal stop evidence."""
import unittest

from buddy.blocking import await_run
from test_blocking import VirtualClock
from test_workflow import NONCE, WorkflowTestCase


class ReceiptStopTests(WorkflowTestCase):
    def test_cancel_racing_with_normal_yield_cannot_requeue(self):
        board = self.board()
        self.register(board)
        run = self.submit(board)
        claim = self.claim(board)
        board.call("workflow_cancel", {"runId": run["runId"], "commandId": "cancel", **self.control(run)})
        self.finish_turn(board, claim, disposition="attention")
        view = board.call("workflow_get", {"runId": run["runId"]})
        self.assertEqual(view["state"], "cancelled")
        self.assertEqual(view["task"]["status"], "cancelled")
        self.assertFalse(any(request["state"] == "open" for request in view["requests"]))
        self.assertTrue(view["shutdown"]["selfConfirmed"])

    def test_release_proof_clears_cancelled_workspace_reservation(self):
        board = self.board()
        self.register(board)
        run = self.submit(board)
        claim = self.claim(board)["claim"]["attempt"]
        board.call("workflow_cancel", {"runId": run["runId"], "commandId": "cancel", **self.control(run)})
        board.call("worker_release", {
            "workerId": "w1", "attemptId": claim["attemptId"], "generation": claim["generation"],
            "nonce": NONCE, "evidence": {"spawnIntentWritten": False}, "reason": "owner never spawned",
        })
        view = board.call("workflow_get", {"runId": run["runId"], "includeAudit": True})
        self.assertEqual(view["state"], "cancelled")
        self.assertEqual(view["task"]["status"], "cancelled")
        self.assertEqual(view["shutdown"]["unconfirmedCount"], 0)
        self.assertTrue(all(row["state"] == "released" for row in view["audit"]["reservations"]))
        replacement = self.submit(board, request_id="reuse-actual-checkout")
        self.assertNotEqual(replacement["runId"], run["runId"])

    def test_execution_result_named_turn_is_opaque_data(self):
        board = self.board()
        self.register(board)
        run = board.call("task_submit", {"requestId": "execution", "task": "opaque data", "cwd": str(self.workdir()),
                                       "adapter": "command", "argv": ["true"]})
        attempt = self.claim(board)["claim"]["attempt"]
        board.call("worker_result", {
            "workerId": "w1", "attemptId": attempt["attemptId"], "generation": attempt["generation"], "nonce": NONCE,
            "status": "ok", "shutdownConfirmed": True, "result": {"turn": {"outcome": {"disposition": "assistance"}}},
        })
        view = board.call("task_get", {"runId": run["task"]["runId"]})["task"]
        self.assertEqual(view["status"], "completed")
        self.assertNotIn("workflow", view)

    def test_current_summary_is_bounded_and_full_audit_preserved(self):
        board = self.board()
        self.register(board)
        run = self.submit(board)
        outcome = {"disposition": "completed", "summary": "s" * 6000, "remaining": [], "decisions": [], "artifacts": [], "request": None}
        self.finish_turn(board, self.claim(board), outcome=outcome)
        view = board.call("workflow_get", {"runId": run["runId"], "includeAudit": True})
        self.assertEqual(len(view["currentTurn"]["summary"]), 2000)
        self.assertTrue(view["currentTurn"]["summaryTruncated"])
        self.assertEqual(view["audit"]["turns"][0]["outcome"]["summary"], "s" * 6000)


class GoalStopWaitTests(unittest.TestCase):
    def test_cancelled_goal_never_becomes_success_from_a_late_completed_execution(self):
        run = {"runId": "parent", "status": "completed", "workflowState": "cancelled", "revision": 9,
               "resultAvailable": True, "shutdownConfirmed": True,
               "workflowShutdown": {"selfConfirmed": True, "descendantsConfirmed": True,
                                    "unconfirmedRunIds": [], "unconfirmedCount": 0, "truncated": False}}

        def service(method, params, _directory=None):
            if method == "status":
                return dict(run)
            if method == "result":
                return {**run, "result": {"status": "ok", "turn": {"outcome": {"disposition": "completed", "summary": "done"}}}}
            raise AssertionError(f"unexpected operation {method}")

        result = await_run({"runId": "parent", "waitSeconds": 1}, service=service, clock=VirtualClock())
        self.assertEqual(result["outcome"], "cancelled")
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["workflowState"], "cancelled")
        self.assertFalse(result["ok"])

    def test_cancelled_parent_waits_on_descendant_events_not_terminal_spin(self):
        clock = VirtualClock()
        pending = {"selfConfirmed": True, "descendantsConfirmed": False, "unconfirmedRunIds": ["child"], "unconfirmedCount": 1, "truncated": False}
        run = {"runId": "parent", "status": "cancelled", "workflowState": "cancelled", "revision": 9,
               "resultAvailable": True, "shutdownConfirmed": True, "workflowShutdown": pending}
        calls = []

        def service(method, params, _directory=None):
            calls.append((method, params))
            if method == "status":
                return dict(run)
            if method == "events":
                return {"head": 42, "events": []}
            if method == "watch":
                self.assertEqual(params["runId"], "child")
                self.assertEqual(params["after"], 42)
                clock.advance(0.1)
                run["workflowShutdown"] = {**pending, "descendantsConfirmed": True, "unconfirmedRunIds": [], "unconfirmedCount": 0}
                return {"head": 43, "events": [{"kind": "task.cancelled"}]}
            if method == "result":
                return {**run, "result": {"status": "cancelled"}}
            raise AssertionError(f"unexpected operation {method}")

        result = await_run({"runId": "parent", "waitSeconds": 1}, service=service, clock=clock)
        self.assertEqual(result["outcome"], "cancelled")
        self.assertTrue(result["shutdownConfirmed"])
        self.assertTrue(result["shutdown"]["descendantsConfirmed"])
        self.assertEqual(sum(method == "watch" for method, _ in calls), 1)
        self.assertFalse(any(method == "wait" for method, _ in calls))

    def test_unknown_descendant_stop_expires_only_the_wait(self):
        clock = VirtualClock()
        pending = {"selfConfirmed": True, "descendantsConfirmed": False, "unconfirmedRunIds": ["child"], "unconfirmedCount": 1, "truncated": False}
        run = {"runId": "parent", "status": "cancelled", "workflowState": "cancelled", "revision": 9,
               "resultAvailable": True, "shutdownConfirmed": True, "workflowShutdown": pending}

        def service(method, params, _directory=None):
            if method == "status":
                return dict(run)
            if method == "events":
                return {"head": 42, "events": []}
            if method == "watch":
                clock.advance(params["timeoutMs"] / 1000)
                return {"head": 42, "events": [], "timedOut": True}
            raise AssertionError(f"unexpected operation {method}")

        result = await_run({"runId": "parent", "waitSeconds": 1}, service=service, clock=clock)
        self.assertEqual(result["outcome"], "wait-timeout")
        self.assertFalse(result["shutdownConfirmed"])
        self.assertEqual(result["shutdown"]["unconfirmedRunIds"], ["child"])

    def test_goal_wait_timeout_expires_only_the_wait_and_names_current_commands(self):
        clock = VirtualClock()
        run = {"runId": "parent", "status": "running", "workflowState": "executing", "revision": 9,
               "resultAvailable": False, "shutdownConfirmed": False}
        calls = []

        def service(method, params, _directory=None):
            calls.append(method)
            if method == "status":
                return dict(run)
            if method == "wait":
                clock.advance(params["timeoutMs"] / 1000)
                return dict(run)
            raise AssertionError(f"await must stay read-only, but called {method!r}")

        result = await_run({"runId": "parent", "waitSeconds": 1}, service=service, clock=clock)
        self.assertEqual(result["outcome"], "wait-timeout")
        self.assertFalse(result["ok"])
        self.assertTrue(result["timedOut"])
        self.assertEqual(set(calls) - {"status", "wait"}, set(), "a timeout never starts or cancels the goal")
        commands = " ".join(result["recovery"]["commands"])
        self.assertIn("buddy status", commands)
        self.assertIn("buddy await", commands)
        self.assertNotIn("buddy run", commands)
        self.assertNotIn("buddy start", commands)
        self.assertNotIn("workflow-", commands)
        self.assertIn("execution-cancel", result["recovery"]["action"])
