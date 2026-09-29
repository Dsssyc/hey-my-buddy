"""Cancellation follows owned descendants and preserves honest stop evidence."""
from __future__ import annotations

import uuid

from buddy import workflow as workflow_module, workspace
from buddy.errors import BoardError
from test_workflow_real import CONFIGURATION, RealWorkspaceTestCase, private_environment
from test_workflow import WorkflowTestCase


class WorkflowCancellationTests(WorkflowTestCase):
    def setUp(self):
        super().setUp()
        private_environment(self)
        self.catalog_fixture()

    def parent_and_child(self, *, kind="existing", concurrency=1):
        board = self.board(max_concurrent=concurrency)
        root = self.workdir("repo")
        (root / ".git").mkdir()
        self.register(board)
        submitted = self.submit(board, cwd=str(root))
        self.finish_turn(board, self.claim(board), disposition="assistance")
        view = board.call("workflow_get", {"runId": submitted["runId"]})
        approved = self.decide(board, view, view["activeRequest"]["requestId"], helpers=[{
            **CONFIGURATION,
            "requestId": "helper-1", "task": "bounded helper", "cwd": str(root),
            "executionWorkspace": {"kind": kind, "access": "write"},
        }])
        return board, root, approved, approved["children"][0]["taskId"]

    def cancel(self, board, view):
        return board.call("workflow_cancel", {"runId": view["runId"], "reason": "Host stopped this goal", **self.control(view)})

    def reservations(self, board):
        with board.store.db.read() as connection:
            return {(row["holder_task_id"], row["state"]) for row in connection.execute("SELECT * FROM workspace_reservations")}

    def summary(self, board, run_id):
        with board.store.db.read() as connection:
            return board.store.workflow.shutdown_summary(connection, run_id)

    def console_continue(self, board, view, *, policy="keep", command="console-continue"):
        return board.store.workflow.continue_run({
            "runId": view["runId"], "commandId": command, "expectedRevision": view["revision"],
            "input": "explicit Host continuation", "helperPolicy": policy,
        }, console_authority={"sessionId": "test-console"})

    def grandchild(self, board, child, root, *, kind="worktree"):
        self.finish_turn(board, self.claim(board, claim_request_id="child-claim", run_id=child), disposition="attention")
        view = board.call("workflow_get", {"runId": child})
        approved = board.store.workflow.decide({
            "runId": child, "requestId": view["activeRequest"]["requestId"], "commandId": "dependency-approval",
            "expectedRevision": view["revision"], "decision": "approve", "helpers": [{
                **CONFIGURATION,
                "requestId": "grandchild", "task": "dependency", "cwd": str(root),
                "executionWorkspace": {"kind": kind, "access": "write"},
            }],
        }, console_authority={"sessionId": "test-console"})
        return approved["children"][0]["taskId"]

    def assert_cancelled_run(self, board, run_id):
        view = board.call("workflow_get", {"runId": run_id})
        self.assertEqual(view["state"], "cancelled")
        self.assertIsNone(view["activeRequest"])
        with board.store.db.read() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM workflow_requests WHERE run_id=? AND state='open'", (run_id,)).fetchone()[0], 0)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM workflow_continuations WHERE run_id=? AND state IN ('recorded','queued')", (run_id,)).fetchone()[0], 0)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM agent_credentials WHERE run_id=? AND state='active'", (run_id,)).fetchone()[0], 0)

    def test_queued_transferred_helper_cancellation_releases_checkout(self):
        board, root, parent, child = self.parent_and_child()
        self.assertIn((parent["runId"], "transferred"), self.reservations(board))
        self.assertIn((child, "held"), self.reservations(board))
        self.cancel(board, parent)
        self.assert_cancelled_run(board, child)
        self.assertEqual(self.summary(board, parent["runId"]), {
            "selfConfirmed": True, "descendantsConfirmed": True, "unconfirmedRunIds": [],
            "unconfirmedCount": 0, "truncated": False,
        })
        self.assertIn((parent["runId"], "released"), self.reservations(board))
        self.assertIn((child, "released"), self.reservations(board))
        replacement = self.submit(board, request_id="replacement", cwd=str(root))
        self.assertEqual(replacement["workspace"]["checkoutId"], parent["workspace"]["checkoutId"])

    def test_yielded_attention_helper_and_its_request_are_cancelled(self):
        board, _, parent, child = self.parent_and_child()
        helper = self.claim(board, claim_request_id="helper-claim", run_id=child)
        self.finish_turn(board, helper, disposition="attention")
        self.assertEqual(board.call("workflow_get", {"runId": child})["state"], "awaiting-host")
        self.cancel(board, parent)
        self.assert_cancelled_run(board, parent["runId"])
        self.assert_cancelled_run(board, child)
        self.assertIn((child, "released"), self.reservations(board))

    def test_manual_cancel_policy_cancels_attention_and_returns_parent_ownership(self):
        board, _, parent, child = self.parent_and_child()
        self.finish_turn(board, self.claim(board, claim_request_id="helper-claim", run_id=child), disposition="attention")
        current = board.call("workflow_get", {"runId": parent["runId"]})
        continued = self.continue_run(board, current, helper_policy="cancel")
        self.assertEqual(continued["state"], "executing")
        self.assert_cancelled_run(board, child)
        self.assertIn((parent["runId"], "held"), self.reservations(board))
        self.assertIn((child, "released"), self.reservations(board))

    def test_active_stop_retains_reservation_until_receipt_then_does_not_rehold_cancelled_parent(self):
        board, root, parent, child = self.parent_and_child()
        helper = self.claim(board, claim_request_id="helper-claim", run_id=child)
        self.cancel(board, parent)
        self.assert_cancelled_run(board, child)
        summary = self.summary(board, parent["runId"])
        self.assertTrue(summary["selfConfirmed"])
        self.assertFalse(summary["descendantsConfirmed"])
        self.assertEqual(summary["unconfirmedRunIds"], [child])
        self.assertIn((child, "held"), self.reservations(board))
        with self.assertRaises(BoardError) as blocked:
            self.submit(board, request_id="before-stop", cwd=str(root))
        self.assertEqual(blocked.exception.code, "PREPARATION_CONFLICT")
        self.finish_turn(board, helper, runner_status="cancelled", seal=False)
        self.assertTrue(self.summary(board, parent["runId"])["descendantsConfirmed"])
        self.assertNotIn((parent["runId"], "held"), self.reservations(board))
        self.assertIn((child, "released"), self.reservations(board))
        self.submit(board, request_id="after-stop", cwd=str(root))

    def test_late_completed_artifact_is_retained_without_reviving_cancelled_goal(self):
        board, _, parent, child = self.parent_and_child()
        helper = self.claim(board, claim_request_id="helper-claim", run_id=child)
        self.cancel(board, parent)
        result = self.finish_turn(board, helper)
        self.assertEqual(result["taskState"], "completed")
        self.assert_cancelled_run(board, parent["runId"])
        self.assert_cancelled_run(board, child)
        artifacts = board.call("workflow_get", {"runId": child})["artifacts"]
        self.assertIn("output", [artifact["kind"] for artifact in artifacts])
        self.assertNotIn((parent["runId"], "held"), self.reservations(board))

    def test_nested_dependency_cancellation_fences_all_governance(self):
        board, root, parent, child = self.parent_and_child()
        grandchild = self.grandchild(board, child, root)
        self.cancel(board, parent)
        for run_id in (parent["runId"], child, grandchild):
            self.assert_cancelled_run(board, run_id)
            self.assertIn((run_id, "released"), self.reservations(board))
        self.assertTrue(self.summary(board, parent["runId"])["descendantsConfirmed"])

    def test_cancellation_attribution_follows_own_event_and_survives_replay(self):
        from buddy import cli_views

        board, _, parent, child = self.parent_and_child()
        root_id = parent["runId"]
        reason = "Host took the remaining work"
        cancelled = board.call("workflow_cancel", {
            "runId": root_id, "commandId": "actor-cancel-1", "reason": reason, **self.control(parent),
        })
        expected = {"actor": "host:host-1", "reason": reason}
        self.assertEqual(cancelled["cancellation"], expected)
        self.assertEqual(board.call("workflow_get", {"runId": root_id})["cancellation"], expected)
        self.assertEqual(board.call("workflow_get", {"runId": child})["cancellation"], expected)
        self.assertEqual(cli_views.render("get", board.call("workflow_get", {"runId": child}))["cancellation"], expected)
        task = board.call("task_get", {"runId": child})["task"]
        self.assertEqual(task["cancellation"], expected)
        self.assertEqual(cli_views.render("status", task)["cancellation"], expected)

        # A later explicit cancellation of the already fenced helper is not its
        # original cancellation, including when read through an old receipt.
        board.store.workflow.cancel({"runId": child, "reason": "later console action"},
                                    console_authority={"sessionId": "later"})
        self.assertEqual(board.call("workflow_get", {"runId": child})["cancellation"], expected)
        replay = board.call("workflow_cancel", {
            "runId": root_id, "commandId": "actor-cancel-1", "reason": reason, **self.control(parent),
        })
        self.assertEqual(replay["cancellation"], expected)

    def test_console_stop_attribution_does_not_relabel_completed_result(self):
        from buddy import cli_views

        board, _, parent, _ = self.parent_and_child()
        root_id = parent["runId"]
        before = board.call("task_result", {"runId": root_id})
        stopped = board.store.workflow.cancel({"runId": root_id, "reason": "Stopped from browser"},
                                              console_authority={"sessionId": "browser-1"})
        expected = {"actor": "console:browser-1", "reason": "Stopped from browser"}
        self.assertEqual(stopped["cancellation"], expected)
        after = board.call("task_result", {"runId": root_id})
        self.assertEqual(after["result"], before["result"])
        self.assertEqual(after["cancellation"], expected)
        self.assertEqual(cli_views.render("result", after)["cancellation"], expected)

    def test_missing_historical_helper_actor_is_not_filled_from_later_cancel(self):
        board, _, parent, child = self.parent_and_child()
        self.cancel(board, parent)
        with board.store.db.write() as connection:
            connection.execute(
                "UPDATE events SET payload_json=json_remove(payload_json,'$.actor')"
                " WHERE seq=(SELECT MIN(seq) FROM events WHERE task_id=? AND kind='workflow.helper_cancelled')",
                (child,),
            )
        board.store.workflow.cancel({"runId": child, "reason": "later"},
                                    console_authority={"sessionId": "later"})
        self.assertEqual(board.call("workflow_get", {"runId": child})["cancellation"],
                         {"actor": None, "reason": "Host stopped this goal"})

    def test_cancellation_traverses_completed_intermediary(self):
        board, root, parent, child = self.parent_and_child(kind="worktree")
        grandchild = self.grandchild(board, child, root)
        child_view = board.call("workflow_get", {"runId": child})
        self.console_continue(board, child_view)
        self.finish_turn(board, self.claim(board, claim_request_id="child-again", run_id=child))
        self.assertEqual(board.call("workflow_get", {"runId": child})["state"], "delivered")
        self.cancel(board, parent)
        self.assert_cancelled_run(board, grandchild)
        self.assertEqual(board.call("workflow_get", {"runId": child})["state"], "delivered")
        self.assertTrue(self.summary(board, parent["runId"])["descendantsConfirmed"])

    def test_manual_cancel_returns_transferred_checkout_through_cancelled_intermediary(self):
        board, root, parent, child = self.parent_and_child()
        grandchild = self.grandchild(board, child, root, kind="existing")
        view = board.call("workflow_get", {"runId": parent["runId"]})
        self.continue_run(board, view, helper_policy="cancel")
        self.assert_cancelled_run(board, child)
        self.assert_cancelled_run(board, grandchild)
        self.assertEqual(board.call("workflow_get", {"runId": child})["cancellation"]["actor"], "host:host-1")
        self.assertEqual(board.call("workflow_get", {"runId": grandchild})["cancellation"]["actor"], "host:host-1")
        self.assertIn((parent["runId"], "held"), self.reservations(board))
        self.assertNotIn((child, "held"), self.reservations(board))

    def test_manual_nested_cancel_waits_for_active_grandchild_before_reclaiming_checkout(self):
        board, root, parent, child = self.parent_and_child()
        grandchild = self.grandchild(board, child, root, kind="existing")
        grandchild_claim = self.claim(board, claim_request_id="grandchild-claim", run_id=grandchild)
        view = board.call("workflow_get", {"runId": parent["runId"]})
        self.continue_run(board, view, helper_policy="cancel")
        self.assertIn((grandchild, "held"), self.reservations(board))
        self.assertNotIn((parent["runId"], "held"), self.reservations(board))
        self.assertEqual(self.summary(board, parent["runId"])["unconfirmedRunIds"], [grandchild])
        self.finish_turn(board, grandchild_claim, runner_status="cancelled", seal=False)
        self.assertIn((parent["runId"], "held"), self.reservations(board))
        self.assertNotIn((child, "held"), self.reservations(board))
        self.assertEqual(board.call("workflow_get", {"runId": parent["runId"]})["state"], "executing")

    def test_cancelled_helpers_notify_a_live_parent_after_actual_stop(self):
        board, _, parent, child = self.parent_and_child()
        helper = self.claim(board, claim_request_id="helper-claim", run_id=child)
        board.store.workflow.cancel({"runId": child}, console_authority={"sessionId": "test-console"})
        self.assertEqual(board.call("workflow_get", {"runId": parent["runId"]})["state"], "waiting-helpers")
        self.finish_turn(board, helper, runner_status="cancelled", seal=False)
        self.assertEqual(board.call("workflow_get", {"runId": parent["runId"]})["state"], "executing")
        self.assertIn((parent["runId"], "held"), self.reservations(board))

    def test_uncertain_result_and_expired_lease_do_not_prove_shutdown(self):
        board, _, parent, child = self.parent_and_child()
        helper = self.claim(board, claim_request_id="helper-claim", run_id=child)
        with board.store.db.write() as connection:
            connection.execute("UPDATE attempts SET lease_expires_at='1900-01-01T00:00:00Z' WHERE task_id=?", (child,))
            connection.execute("UPDATE workers SET pid=NULL WHERE worker_id='w1'")
        self.cancel(board, parent)
        self.finish_turn(board, helper, runner_status="cancelled", shutdown=False, seal=False)
        self.assertEqual(self.summary(board, parent["runId"])["unconfirmedRunIds"], [child])
        self.assertIn((child, "held"), self.reservations(board))
        self.assert_cancelled_run(board, child)

    def test_explicit_child_continue_is_fenced_but_root_continue_remains_explicit(self):
        board, _, parent, child = self.parent_and_child()
        self.cancel(board, parent)
        child_view = board.call("workflow_get", {"runId": child})
        with self.assertRaises(BoardError) as failure:
            self.console_continue(board, child_view)
        self.assertEqual(failure.exception.code, "ANCESTOR_TERMINAL")
        self.assertEqual(failure.exception.details["ancestorRunId"], parent["runId"])
        root_view = board.call("workflow_get", {"runId": parent["runId"]})
        self.assertEqual(self.continue_run(board, root_view)["state"], "executing")

    def test_late_attention_and_explicit_child_continue_cannot_reopen_accepted_ancestor(self):
        board, _, parent, child = self.parent_and_child(kind="worktree", concurrency=2)
        helper = self.claim(board, claim_request_id="helper-claim", run_id=child)
        self.continue_run(board, parent)
        self.register(board, "w2")
        root_claim = self.claim(board, "w2", claim_request_id="root-again", run_id=parent["runId"])
        self.finish_turn(board, root_claim, worker_id="w2")
        # Current acknowledgement rejects live helpers. Force an accepted ancestor
        # to test the independent late-callback/lineage fence.
        with board.store.db.write() as connection:
            connection.execute("UPDATE workflow_runs SET state='accepted', active_request_id=NULL WHERE run_id=?", (parent["runId"],))
            connection.execute("UPDATE tasks SET accepted_at=?, acceptance_verdict='accepted' WHERE task_id=?", (board.store.now(), parent["runId"]))
            board.store.workflow._release_reservations(connection, parent["runId"], board.store.now())
        self.finish_turn(board, helper, disposition="attention")
        accepted = board.call("workflow_get", {"runId": parent["runId"]})
        self.assertEqual(accepted["state"], "accepted")
        self.assertIsNone(accepted["activeRequest"])
        child_view = board.call("workflow_get", {"runId": child})
        with self.assertRaises(BoardError) as failure:
            self.console_continue(board, child_view, command="late-child")
        self.assertEqual(failure.exception.code, "ANCESTOR_TERMINAL")
        with board.store.db.read() as connection:
            task = connection.execute("SELECT * FROM tasks WHERE task_id=?", (child,)).fetchone()
            self.assertEqual(board.store.workflow.claim_blocker(connection, task), "workflow-ancestor-accepted")

    def test_release_hook_releases_never_spawned_attempt_and_keeps_cancellation(self):
        board, _, parent, child = self.parent_and_child()
        helper = self.claim(board, claim_request_id="helper-claim", run_id=child)
        attempt = helper["claim"]["attempt"]
        self.cancel(board, parent)
        board.call("worker_release", {"workerId": "w1", "attemptId": attempt["attemptId"], "generation": attempt["generation"],
                                      "nonce": "n" * 16, "evidence": {"spawnIntentWritten": False}, "reason": "no process spawned"})
        with board.store.db.write() as connection:
            task_row = connection.execute("SELECT * FROM tasks WHERE task_id=?", (child,)).fetchone()
            attempt_row = connection.execute("SELECT * FROM attempts WHERE attempt_id=?", (attempt["attemptId"],)).fetchone()
            board.store.workflow.attempt_released(connection, task=task_row, attempt=attempt_row, now=board.store.now(), reason="no process spawned")
        self.assert_cancelled_run(board, child)
        self.assertIn((child, "released"), self.reservations(board))
        self.assertNotIn((parent["runId"], "held"), self.reservations(board))
        self.assertTrue(self.summary(board, parent["runId"])["descendantsConfirmed"])

    def test_shutdown_summary_counts_all_descendants_but_bounds_ids(self):
        board, _, parent, _ = self.parent_and_child(kind="worktree")
        children = [self.submit(board, request_id=f"unknown-{number}", kind="worktree") for number in range(34)]
        with board.store.db.write() as connection:
            for child in children:
                run_id, attempt_id, now = child["runId"], str(uuid.uuid4()), board.store.now()
                connection.execute(
                    "INSERT INTO workflow_children(child_task_id,parent_run_id,request_id,decision_command_id,state,"
                    "workspace_intent_json,created_at,updated_at) VALUES(?,?,?,'summary-fixture','active','{}',?,?)",
                    (run_id, parent["runId"], child["requestId"], now, now),
                )
                connection.execute(
                    "INSERT INTO attempts(attempt_id,task_id,generation,nonce_verifier,claim_request_id,adapter,execution_state,"
                    "created_at,updated_at) VALUES(?,?,1,'fixture','fixture','dsh','uncertain',?,?)", (attempt_id, run_id, now, now),
                )
                connection.execute("UPDATE tasks SET selected_attempt_id=? WHERE task_id=?", (attempt_id, run_id))
        summary = self.summary(board, parent["runId"])
        self.assertTrue(summary["selfConfirmed"])
        self.assertFalse(summary["descendantsConfirmed"])
        self.assertEqual(summary["unconfirmedCount"], 34)
        self.assertEqual(len(summary["unconfirmedRunIds"]), 32)
        self.assertTrue(summary["truncated"])

    def test_shutdown_summary_does_not_hide_unresolved_attempt_behind_old_selected_result(self):
        board, _, parent, _ = self.parent_and_child()
        with board.store.db.write() as connection:
            connection.execute(
                "INSERT INTO attempts(attempt_id,task_id,generation,nonce_verifier,claim_request_id,adapter,execution_state,"
                "created_at,updated_at) VALUES(?,?,2,'fixture','fixture','dsh','uncertain',?,?)",
                (str(uuid.uuid4()), parent["runId"], board.store.now(), board.store.now()),
            )
        summary = self.summary(board, parent["runId"])
        self.assertFalse(summary["selfConfirmed"])
        self.assertTrue(summary["descendantsConfirmed"])
        self.assertEqual(summary["unconfirmedRunIds"], [parent["runId"]])


class RealCheckoutCancellationTests(RealWorkspaceTestCase, WorkflowTestCase):
    def setUp(self):
        super().setUp()
        self.workspace = workspace
        workflow_module._workspace_module = workspace

    def test_original_git_checkout_can_be_reassigned_after_queued_helper_cancel(self):
        board = self.board(max_concurrent=1)
        self.register(board)
        submitted = self.submit(board)
        self.controls[submitted["runId"]] = submitted["control"]
        first = self.claim(board)
        (self.repo / "tracked.txt").write_text("meaningful uncommitted parent output\n")
        self.finish_turn(board, first, disposition="assistance")
        view = board.call("workflow_get", {"runId": submitted["runId"]})
        approved = self.decide(board, view, view["activeRequest"]["requestId"], helpers=[{
            **CONFIGURATION,
            "requestId": "real-helper", "task": "use the parent checkout", "cwd": str(self.repo),
            "executionWorkspace": {"kind": "existing", "access": "write"},
        }])
        before = self.state()
        board.call("workflow_cancel", {"runId": submitted["runId"], **self.control(approved)})
        replacement = self.submit(board, request_id="replacement-real")
        self.assertEqual(replacement["workspace"]["checkoutId"], submitted["workspace"]["checkoutId"])
        self.assertEqual(self.state(), before)
        claimed = self.claim(board, claim_request_id="replacement-claim", run_id=replacement["runId"])
        self.assertEqual(claimed["claim"]["attempt"]["taskId"], replacement["runId"])
