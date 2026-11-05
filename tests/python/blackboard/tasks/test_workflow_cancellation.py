"""Cancellation follows owned descendants and preserves honest stop evidence."""
from __future__ import annotations

import json
import uuid

from hey_my_buddy.blackboard.tasks import workflow as workflow_module, workspace
from hey_my_buddy.errors import BoardError
from blackboard.tasks.test_workflow_real import BUDDY, RealWorkspaceTestCase, private_environment
from blackboard.tasks.test_workflow import WorkflowTestCase


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
            **BUDDY,
            "title": "helper-1", "task": "bounded helper", "cwd": str(root),
            "workspace": {"kind": kind, "access": "write"},
        }])
        return board, root, approved, approved["children"][0]["taskId"]

    def cancel(self, board, view):
        return board.call("workflow_cancel", {"runId": view["runId"], 'note': "Host stopped this micro task"})

    def test_root_proxy_can_transfer_child_checkout_to_grandchild(self):
        board, root, parent, child = self.parent_and_child()
        self.finish_turn(board, self.claim(board, run_id=child), disposition="attention")
        view = board.call("workflow_get", {"runId": parent["runId"]})
        self.decide(board, view, view["activeRequest"]["requestId"],  helpers=[{
            **BUDDY, "title": "proxied-grandchild", "task": "finish child work", "cwd": str(root),
            "workspace": {"kind": "existing", "access": "write"},
        }])
        with board.store.db.read() as connection:
            grandchild = connection.execute("SELECT child_task_id FROM workflow_children WHERE parent_run_id=?", (child,)).fetchone()[0]
            held = connection.execute("SELECT holder_task_id FROM workspace_reservations WHERE state='held'").fetchall()
            transferred = connection.execute("SELECT state FROM workspace_reservations WHERE holder_task_id=?", (child,)).fetchone()[0]
        self.assertEqual([row[0] for row in held], [grandchild])
        self.assertEqual(transferred, "transferred")

    def test_proxy_rejects_a_stale_prechecked_requesting_run(self):
        board, _root, parent, child = self.parent_and_child()
        self.finish_turn(board, self.claim(board, run_id=child), disposition="attention")
        view = board.call("workflow_get", {"runId": parent["runId"]})
        request_id = view["activeRequest"]["requestId"]
        self.cancel(board, parent)
        with self.assertRaises(BoardError):
            self.decide(board, view, request_id, helpers=[{
                "task": "late descendant", "cwd": view["task"]["cwd"],
                "workspace": {"kind": "existing", "access": "write"},
            }])
        with board.store.db.read() as connection:
            self.assertEqual(connection.execute(
                "SELECT COUNT(*) FROM workflow_children WHERE parent_run_id=?", (child,)
            ).fetchone()[0], 0)
        self.assertNotIn((child, "held"), self.reservations(board))

    def reservations(self, board):
        with board.store.db.read() as connection:
            return {(row["holder_task_id"], row["state"]) for row in connection.execute("SELECT * FROM workspace_reservations")}

    def summary(self, board, run_id):
        with board.store.db.read() as connection:
            return board.store.workflow.shutdown_summary(connection, run_id)

    def console_continue(self, board, view, *, policy="keep", command="console-continue"):
        params = {"runId": view["runId"], "input": "explicit Host continuation"}
        if view.get("activeRequest"):
            params["requestId"] = view["activeRequest"]["requestId"]
        elif view.get("finalArtifactId"):
            params["artifactId"] = view["finalArtifactId"]
        else:
            params["turnId"] = view["currentTurn"]["turnId"]
        with board.store.db.read() as connection:
            if board.store.workflow._unfinished_helpers(connection, view["runId"]):
                params["helperPolicy"] = policy
        return board.store.workflow.continue_run(params, console_authority={"sessionId": "test-console"})

    def grandchild(self, board, child, root, *, kind="worktree"):
        self.finish_turn(board, self.claim(board, claim_request_id="child-claim", run_id=child), disposition="attention")
        view = board.call("workflow_get", {"runId": child})
        approved = board.store.workflow.continue_run({
            "runId": child, "requestId": view["activeRequest"]["requestId"],
            "decision": "approve", "autoContinue": True, "helpers": [{
                "title": "grandchild", "task": "dependency", "cwd": str(root),
                "buddy": " / ".join(BUDDY.values()), "checkout": kind, "access": "write",
                "inputs": [], "writeScope": ["."], "expectedSeconds": 1800,
            }],
        }, console_authority={"sessionId": "test-console"})
        return board.call("workflow_get", {"runId": child})["children"][0]["taskId"]

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
        self.assertEqual(replacement["workspace"]["checkoutRoot"], parent["workspace"]["checkoutRoot"])

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
        artifact = next(row for row in current["artifacts"] if row["turnId"]==current["currentTurn"]["turnId"] and row["kind"]=="output")
        continued = self.continue_run(board, current, artifactId=artifact["artifactId"], helper_policy="cancel")
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

    def test_late_completed_artifact_is_retained_without_reviving_cancelled_micro_task(self):
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
        from hey_my_buddy.cli import cli_views

        board, _, parent, child = self.parent_and_child()
        root_id = parent["runId"]
        reason = "Host took the remaining work"
        cancelled = board.call("workflow_cancel", {
            "runId": root_id, 'note': reason,
        })
        expected = {"actor": {"role": "host"}, "note": reason}
        for cancellation in (
            board.call("workflow_get", {"runId": root_id})["cancellation"],
            board.call("workflow_get", {"runId": child})["cancellation"],
        ):
            self.assertEqual({key: cancellation[key] for key in expected}, expected)
        task = board.call("task_get", {"runId": child})["task"]
        self.assertEqual({key: task["cancellation"][key] for key in expected}, expected)
        rendered = cli_views.render("status", task)["cancellation"]
        self.assertEqual({key: rendered[key] for key in expected}, expected)

        # A later explicit cancellation of the already fenced helper is not its
        # original cancellation, including when read through an old receipt.
        board.store.workflow.cancel({"runId": child, "note": "later console action"},
                                    console_authority={"sessionId": "later"})
        current = board.call("workflow_get", {"runId": child})["cancellation"]
        self.assertEqual({key: current[key] for key in expected}, expected)
        replay = board.call("workflow_cancel", {
            "runId": root_id, 'note': reason,
        })
        self.assertEqual(replay["runId"], root_id)

    def test_console_stop_attribution_does_not_relabel_completed_result(self):
        from hey_my_buddy.cli import cli_views

        board, _, parent, _ = self.parent_and_child()
        root_id = parent["runId"]
        before = board.call("task_result", {"runId": root_id})
        stopped = board.store.workflow.cancel({"runId": root_id, "note": "Stopped from browser"},
                                              console_authority={"sessionId": "browser-1"})
        expected = {"actor": {"role": "console", "id": "browser-1"}, "note": "Stopped from browser"}
        current = board.call("workflow_get", {"runId": root_id})["cancellation"]
        self.assertEqual({key: current[key] for key in expected}, expected)
        after = board.call("task_result", {"runId": root_id})
        self.assertEqual(after["result"], before["result"])
        self.assertEqual({key: after["cancellation"][key] for key in expected}, expected)
        rendered = cli_views.render("result", after)["cancellation"]
        self.assertEqual({key: rendered[key] for key in expected}, expected)

    def test_missing_historical_helper_actor_is_not_filled_from_later_cancel(self):
        board, _, parent, child = self.parent_and_child()
        self.cancel(board, parent)
        with board.store.db.write() as connection:
            connection.execute(
                "UPDATE events SET actor_role=NULL,actor_id=NULL"
                " WHERE seq=(SELECT MIN(seq) FROM events WHERE task_id=? AND kind='workflow.helper_cancelled')",
                (child,),
            )
        board.store.workflow.cancel({"runId": child, "note": "later"},
                                    console_authority={"sessionId": "later"})
        cancellation = board.call("workflow_get", {"runId": child})["cancellation"]
        self.assertIsNone(cancellation["actor"])
        self.assertEqual(cancellation["note"], "Host stopped this micro task")





    def test_cancellation_traverses_completed_intermediary(self):
        board, root, parent, child = self.parent_and_child(kind="worktree")
        grandchild = self.grandchild(board, child, root)
        self.assertEqual(board.call("workflow_get", {"runId": child})["state"], "waiting-helpers")
        self.cancel(board, parent)
        self.assert_cancelled_run(board, grandchild)
        self.assert_cancelled_run(board, child)
        self.assertTrue(self.summary(board, parent["runId"])["descendantsConfirmed"])

    def test_manual_cancel_returns_transferred_checkout_through_cancelled_intermediary(self):
        board, root, parent, child = self.parent_and_child()
        grandchild = self.grandchild(board, child, root, kind="existing")
        view = board.call("workflow_get", {"runId": parent["runId"]})
        self.cancel(board, view)
        self.assert_cancelled_run(board, child)
        self.assert_cancelled_run(board, grandchild)
        self.assertEqual(board.call("workflow_get", {"runId": child})["cancellation"]["actor"]["role"], "host")
        self.assertEqual(board.call("workflow_get", {"runId": grandchild})["cancellation"]["actor"]["role"], "host")
        self.assertIn((parent["runId"], "released"), self.reservations(board))
        self.assertNotIn((child, "held"), self.reservations(board))

    def test_manual_nested_cancel_waits_for_active_grandchild_before_reclaiming_checkout(self):
        board, root, parent, child = self.parent_and_child()
        grandchild = self.grandchild(board, child, root, kind="existing")
        grandchild_claim = self.claim(board, claim_request_id="grandchild-claim", run_id=grandchild)
        view = board.call("workflow_get", {"runId": parent["runId"]})
        self.cancel(board, view)
        self.assertIn((grandchild, "held"), self.reservations(board))
        self.assertNotIn((parent["runId"], "held"), self.reservations(board))
        self.assertEqual(self.summary(board, parent["runId"])["unconfirmedRunIds"], [grandchild])
        self.finish_turn(board, grandchild_claim, runner_status="cancelled", seal=False)
        self.assertIn((parent["runId"], "released"), self.reservations(board))
        self.assertNotIn((child, "held"), self.reservations(board))
        self.assertEqual(board.call("workflow_get", {"runId": parent["runId"]})["state"], "cancelled")

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
            connection.execute("UPDATE attempts SET execution_state='uncertain',ownership='uncertain' WHERE task_id=?", (child,))
        self.cancel(board, parent)
        self.finish_turn(board, helper, runner_status="cancelled", shutdown=False, seal=False)
        self.assertEqual(self.summary(board, parent["runId"])["unconfirmedRunIds"], [child])
        self.assertIn((child, "held"), self.reservations(board))
        self.assert_cancelled_run(board, child)





    def test_release_hook_releases_never_spawned_attempt_and_keeps_cancellation(self):
        board, _, parent, child = self.parent_and_child()
        helper = self.claim(board, claim_request_id="helper-claim", run_id=child)
        attempt = helper["claim"]["attempt"]
        self.cancel(board, parent)
        board.store.release_execution(({'attemptId': attempt['attemptId'], 'generation': attempt['generation'], 'nonce': 'n' * 16, 'evidence': {'spawnIntentWritten': False}, 'reason': 'no process spawned'})["attemptId"], reason="execution-process-not-created")
        with board.store.db.write() as connection:
            task_row = connection.execute("SELECT * FROM tasks WHERE task_id=?", (child,)).fetchone()
            attempt_row = connection.execute("SELECT * FROM attempts WHERE attempt_id=?", (attempt["attemptId"],)).fetchone()
            board.store.workflow.attempt_released(connection, task=task_row, attempt=attempt_row, now=board.store.now())
        self.assert_cancelled_run(board, child)
        self.assertIn((child, "released"), self.reservations(board))
        self.assertNotIn((parent["runId"], "held"), self.reservations(board))
        self.assertTrue(self.summary(board, parent["runId"])["descendantsConfirmed"])
        with board.store.db.read() as connection:
            released = connection.execute(
                "SELECT kind,payload_json FROM events WHERE attempt_id=?"
                " AND kind IN ('attempt.released','workflow.attempt_released')",
                (attempt["attemptId"],),
            ).fetchall()
            self.assertEqual([event["kind"] for event in released], ["attempt.released"])
            self.assertEqual(json.loads(released[0]["payload_json"])["reason"],
                             {"code": "execution-process-not-created", "params": {}})
            self.assertIsNone(connection.execute(
                "SELECT result_json FROM attempts WHERE attempt_id=?", (attempt["attemptId"],),
            ).fetchone()["result_json"])

    def test_shutdown_summary_counts_all_descendants_but_bounds_ids(self):
        board, _, parent, _ = self.parent_and_child(kind="worktree")
        children = [self.submit(board, request_id=f"unknown-{number}", kind="worktree") for number in range(34)]
        with board.store.db.write() as connection:
            for child in children:
                run_id, attempt_id, now = child["runId"], str(uuid.uuid4()), board.store.now()
                connection.execute(
                    "INSERT INTO workflow_children(child_task_id,parent_run_id,request_id,state,"
                    "created_at,updated_at) VALUES(?,?,?,'active',?,?)",
                    (run_id, parent["runId"], "fixture-" + run_id, now, now),
                )
                connection.execute("INSERT INTO attempts(attempt_id,task_id,generation,nonce_verifier,adapter,execution_state,created_at,updated_at) VALUES(?,?,1,'fixture','dsh','uncertain',?,?)", (attempt_id, run_id, now, now))
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
            connection.execute("INSERT INTO attempts(attempt_id,task_id,generation,nonce_verifier,adapter,execution_state,created_at,updated_at) VALUES(?,?,2,'fixture','dsh','uncertain',?,?)", (str(uuid.uuid4()), parent['runId'], board.store.now(), board.store.now()))
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
        first = self.claim(board)
        (self.repo / "tracked.txt").write_text("meaningful uncommitted parent output\n", encoding="utf-8", newline="\n")
        self.finish_turn(board, first, disposition="assistance")
        view = board.call("workflow_get", {"runId": submitted["runId"]})
        approved = self.decide(board, view, view["activeRequest"]["requestId"], helpers=[{
            **BUDDY,
            "title": "real-helper", "task": "use the parent checkout", "cwd": str(self.repo),
            "workspace": {"kind": "existing", "access": "write"},
        }])
        before = self.state()
        board.call("workflow_cancel", {"runId": submitted["runId"]})
        replacement = self.submit(board, request_id="replacement-real")
        self.assertEqual(replacement["workspace"]["checkoutRoot"], submitted["workspace"]["checkoutRoot"])
        self.assertEqual(self.state(), before)
        claimed = self.claim(board, claim_request_id="replacement-claim", run_id=replacement["runId"])
        self.assertEqual(claimed["claim"]["attempt"]["taskId"], replacement["runId"])
