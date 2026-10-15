"""Lead turns park without an execution and resume from stored owned-work facts."""
from __future__ import annotations

import json
from unittest.mock import patch

from blackboard.tasks import test_lead_owned as lead_helpers
from blackboard.tasks.test_workflow import BUDDY, WorkflowTestCase
from blackboard.routing.test_decision import DecisionTestCase
from support import FakeClock

from hey_my_buddy.errors import BoardError
from hey_my_buddy.blackboard.tasks import workflow


class LeadLifecycleTests(WorkflowTestCase):
    lead = lead_helpers.LeadOwnedTests.lead
    owned = lead_helpers.LeadOwnedTests.owned
    finish_owned = lead_helpers.LeadOwnedTests.finish_owned

    def wait(self, board, claim):
        return self.finish_turn(board, {"claim": claim}, outcome={
            "disposition": "waiting", "summary": "Waiting for owned work", "remaining": [],
            "decisions": [], "artifacts": [], "request": None,
        })

    def current(self, board, run):
        return board.call("workflow_get", {"runId": run["runId"]})

    def wakeups(self, board, lead):
        with board.store.db.read() as db:
            return db.execute("SELECT * FROM workflow_continuations WHERE run_id=? AND kind='lead-wakeup' ORDER BY rowid",
                              (lead["runId"],)).fetchall()

    def test_waiting_requires_lead_authority(self):
        board = self.board()
        self.register(board)
        run = self.submit(board)
        claim = self.claim(board)["claim"]
        result = self.wait(board, claim)
        self.assertFalse(result["workflow"]["accepted"])
        self.assertEqual(self.current(board, run)["state"], "failed")
        self.assertEqual(self.current(board, run)["currentTurn"]["state"], "failed")

    def test_waiting_parks_without_an_execution_or_capacity_and_claim_skips_it(self):
        board = self.board(max_concurrent=1)
        lead, claim = self.lead(board)
        child = self.owned(board, claim)
        self.wait(board, claim)
        current = self.current(board, lead)
        self.assertEqual(current["state"], "waiting-owned")
        self.assertIsNone(current["task"]["activeAttemptId"])
        with board.store.db.read() as db:
            self.assertIsNone(db.execute("SELECT current_attempt_id FROM workflow_runs WHERE run_id=?", (lead["runId"],)).fetchone()[0])
            self.assertEqual(db.execute("SELECT COUNT(*) FROM attempts WHERE execution_state!='finished' OR shutdown_confirmed!=1").fetchone()[0], 0)
        next_claim = self.claim(board, claim_request_id="claim-owned")
        self.assertEqual(next_claim["claim"]["task"]["runId"], child["runId"])

    def test_completed_with_unfinished_owned_is_service_attention_without_auto_loop(self):
        board = self.board()
        lead, claim = self.lead(board)
        child = self.owned(board, claim)
        response = self.finish_turn(board, {"claim": claim})
        self.assertFalse(response["workflow"]["accepted"])
        view = self.current(board, lead)
        self.assertEqual(view["state"], "awaiting-host")
        self.assertEqual(view["activeRequest"]["kind"], "attention")
        with board.store.db.read() as db:
            payload = json.loads(db.execute("SELECT payload_json FROM workflow_requests WHERE request_id=?", (view["activeRequest"]["requestId"],)).fetchone()[0])
        self.assertEqual(payload["source"], "lead-lifecycle")
        self.assertEqual(payload["unfinishedOwnedMicroTasks"], [{"runId": child["runId"], "state": "executing"}])
        self.assertEqual(view["activeRequest"]["unfinishedOwnedMicroTasks"], payload["unfinishedOwnedMicroTasks"])
        self.assertEqual(self.wakeups(board, lead), [])

    def test_waiting_without_ongoing_owned_work_is_service_attention(self):
        board = self.board()
        lead, claim = self.lead(board)
        result = self.wait(board, claim)
        self.assertFalse(result["workflow"]["accepted"])
        view = self.current(board, lead)
        self.assertEqual(view["state"], "awaiting-host")
        self.assertIn("still queued, running or stopping", view["activeRequest"]["summary"])
        self.assertEqual(self.wakeups(board, lead), [])

    def test_delivered_but_unaccepted_owned_does_not_allow_waiting(self):
        board = self.board()
        lead, claim = self.lead(board)
        child = self.owned(board, claim)
        self.finish_owned(board, child)
        self.wait(board, claim)
        self.assertEqual(self.current(board, lead)["state"], "awaiting-host")
        self.assertEqual(self.wakeups(board, lead), [])

    def test_completed_after_every_owned_task_is_accepted_or_concluded(self):
        board = self.board()
        lead, claim = self.lead(board)
        accepted = self.owned(board, claim, "accepted")
        finished, _ = self.finish_owned(board, accepted)
        self.accept_run(board, finished)
        concluded = self.owned(board, claim, "concluded")
        self.finish_owned(board, concluded)
        board.call("workflow_conclude", {"runId": concluded["runId"], "note": "Not needed", **self.control(concluded)})
        self.finish_turn(board, {"claim": claim})
        self.assertEqual(self.current(board, lead)["state"], "delivered")

    def test_each_owned_outcome_wakes_a_parked_lead(self):
        for disposition in ("completed", "assistance", "attention", "failed", "cancelled", "uncertain"):
            with self.subTest(disposition=disposition):
                board = self.board()
                lead, claim = self.lead(board, "lead-" + disposition)
                child = self.owned(board, claim, "owned-" + disposition)
                self.wait(board, claim)
                if disposition == "cancelled":
                    board.call("workflow_cancel", {"runId": child["runId"], **self.control(child)})
                else:
                    self.finish_owned(board, child, runner_status="failed" if disposition == "failed" else "ok",
                                      disposition="completed" if disposition in ("failed", "uncertain") else disposition,
                                      shutdown=disposition != "uncertain",
                                      exit_code=1 if disposition == "failed" else 0)
                self.assertEqual(self.current(board, lead)["state"], "executing")
                wakeups = self.wakeups(board, lead)
                self.assertEqual(len(wakeups), 1)
                self.assertEqual(wakeups[0]["kind"], "lead-wakeup")
                trigger = json.loads(wakeups[0]["trigger_event_json"])
                self.assertEqual(trigger["runId"], child["runId"])
                self.assertEqual(trigger["state"], self.current(board, child)["state"])
                with board.store.db.read() as db:
                    event = db.execute("SELECT task_id,revision FROM events WHERE seq=?", (trigger["seq"],)).fetchone()
                    self.assertEqual(event["task_id"], child["runId"])
                    self.assertEqual(event["revision"], self.current(board, child)["revision"])

    def test_proven_interruption_wakes_lead_once_with_partial_output(self):
        from blackboard.store.test_interruption_recovery import IDENTITY
        board = self.board()
        lead, claim = self.lead(board)
        child = self.owned(board, claim)
        self.wait(board, claim)
        self.register(board, 'w2')
        owned = self.claim(board, 'w2', claim_request_id='claim-interrupted-owned', run_id=child['runId'])['claim']
        board.call('worker_progress', {'workerId': 'w2', 'attemptId': owned['attempt']['attemptId'],
                                     'generation': owned['attempt']['generation'], 'nonce': 'n' * 16,
                                     'processIdentity': IDENTITY})
        with patch('hey_my_buddy.protocol.process_identity.inspect_process_identity', return_value='boot-changed'):
            board.store.reconcile_startup()
            board.store.recover_interrupted_attempts()
        view = self.current(board, child)
        self.assertEqual(view['currentTurn']['state'], 'interrupted')
        self.assertEqual(view['task']['interruptionEvidence']['kind'], 'boot-changed')
        self.assertEqual(self.current(board, lead)['state'], 'executing')
        self.assertEqual(len(self.wakeups(board, lead)), 1)

    def test_routing_attention_wakes_the_lead(self):
        board = self.board()
        lead, claim = self.lead(board)
        child = self.owned(board, claim)
        self.wait(board, claim)
        with board.store.db.write() as db:
            board.store.workflow._routing_attention(db, board.store.workflow._run_row(db, child["runId"]), board.store.now(), "No eligible buddy")
        self.assertEqual(self.current(board, lead)["state"], "executing")
        self.assertEqual(json.loads(self.wakeups(board, lead)[0]["trigger_event_json"])["kind"], "workflow.routing_needs_host")

    def test_helper_attention_propagates_through_owned_micro_task_and_wakes_lead(self):
        board = self.board()
        lead, claim = self.lead(board)
        child = self.owned(board, claim)
        view, _ = self.finish_owned(board, child, disposition="assistance")
        helpers = self.decide(board, view, view["activeRequest"]["requestId"], helpers=[{
            "requestId": "helper", "task": "Help owned work", "cwd": str(self.workdir("helper")),
            "executionWorkspace": {"kind": "existing", "access": "write"},
        }])
        self.wait(board, claim)
        # The earlier assistance happened after the first Lead input and already
        # caused a wake on waiting. Consume it and park again before the helper yields.
        next_claim = self.claim(board, claim_request_id="lead-second", run_id=lead["runId"])["claim"]
        self.wait(board, next_claim)
        helper = board.call("workflow_get", {"runId": helpers["children"][0]["taskId"]})
        self.finish_owned(board, helper, disposition="attention")
        self.assertEqual(self.current(board, lead)["state"], "executing")
        self.assertEqual(self.current(board, child)["activeRequest"]["kind"], "helper-attention")
        self.assertEqual(len(self.wakeups(board, lead)), 2)

    def test_multiple_boundaries_before_next_turn_coalesce_and_freeze_at_input_time(self):
        board = self.board()
        lead, claim = self.lead(board)
        first = self.owned(board, claim, "first")
        second = self.owned(board, claim, "second")
        third = self.owned(board, claim, "third")
        self.wait(board, claim)
        self.finish_owned(board, first)
        self.finish_owned(board, second, disposition="attention")
        self.assertEqual(len(self.wakeups(board, lead)), 1)
        next_claim = self.claim(board, claim_request_id="lead-second", run_id=lead["runId"])["claim"]
        context = next_claim["turn"]["input"]["context"]
        facts = {row["runId"]: row for row in context["ownedMicroTasks"]}
        self.assertEqual(context["ownedMicroTaskTotal"], 3)
        self.assertEqual(facts[first["runId"]]["state"], "delivered")
        self.assertEqual(facts[second["runId"]]["state"], "awaiting-host")
        self.assertTrue(all(row["changedSincePreviousTurn"] for row in facts.values()))
        self.assertTrue(all("controlFile" not in row for row in facts.values()))
        self.assertNotIn("controlToken", json.dumps(context))
        frozen = json.dumps(next_claim["turn"]["input"], sort_keys=True)
        self.finish_owned(board, third)
        with board.store.db.read() as db:
            stored = json.loads(db.execute("SELECT input_json FROM workflow_turns WHERE turn_id=?", (next_claim["turn"]["turnId"],)).fetchone()[0])
        self.assertEqual(json.dumps(stored, sort_keys=True), frozen)
        self.assertEqual(len(self.wakeups(board, lead)), 1)

    def test_boundary_while_running_is_carried_and_wakes_immediately_on_waiting(self):
        board = self.board()
        lead, claim = self.lead(board)
        done = self.owned(board, claim, "done")
        self.owned(board, claim, "ongoing")
        self.finish_owned(board, done)
        self.assertEqual(self.wakeups(board, lead), [])
        result = self.wait(board, claim)
        self.assertEqual(result["workflow"]["state"], "executing")
        self.assertEqual(len(self.wakeups(board, lead)), 1)

    def test_owned_events_do_not_wake_host_boundary_or_failed_lead_and_next_input_carries_them(self):
        for boundary in ("attention", "failed"):
            with self.subTest(boundary=boundary):
                board = self.board()
                lead, claim = self.lead(board, "lead-" + boundary)
                child = self.owned(board, claim, "owned-" + boundary)
                self.finish_turn(board, {"claim": claim}, disposition="attention",
                                 runner_status="failed" if boundary == "failed" else "ok", exit_code=1 if boundary == "failed" else 0)
                self.finish_owned(board, child)
                self.assertEqual(self.wakeups(board, lead), [])
                self.continue_run(board, self.current(board, lead), command_id="continue-" + boundary)
                next_claim = self.claim(board, claim_request_id="resume-" + boundary, run_id=lead["runId"])["claim"]
                facts = next_claim["turn"]["input"]["context"]["ownedMicroTasks"]
                self.assertEqual(facts[0]["state"], "delivered")
                self.assertTrue(facts[0]["changedSincePreviousTurn"])
                self.finish_turn(board, {"claim": next_claim}, disposition="attention")

    def test_manual_continue_waiting_lead_and_invalidate_unconsumed_wakeup(self):
        board = self.board()
        lead, claim = self.lead(board)
        child = self.owned(board, claim)
        self.wait(board, claim)
        self.continue_run(board, self.current(board, lead), command_id="manual-waiting")
        manual = self.claim(board, claim_request_id="manual-turn", run_id=lead["runId"])["claim"]
        self.assertEqual(manual["turn"]["input"]["context"]["continuation"]["kind"], "manual")
        self.wait(board, manual)
        self.finish_owned(board, child)
        self.continue_run(board, self.current(board, lead), command_id="replace-wakeup")
        self.assertEqual(self.wakeups(board, lead)[0]["state"], "invalidated")
        next_claim = self.claim(board, claim_request_id="replace-turn", run_id=lead["runId"])["claim"]
        self.assertEqual(next_claim["turn"]["input"]["context"]["ownedMicroTasks"][0]["state"], "delivered")

    def test_woken_lead_precedes_queued_owned_work(self):
        clock = FakeClock()
        board = self.board(max_concurrent=1, clock=clock)
        lead, claim = self.lead(board)
        clock.advance(1)
        trigger = self.owned(board, claim, "trigger")
        clock.advance(1)
        self.owned(board, claim, "queued")
        self.wait(board, claim)
        child_claim = self.claim(board, claim_request_id="child", run_id=trigger["runId"])
        self.finish_turn(board, child_claim)
        next_claim = self.claim(board, claim_request_id="pick")
        self.assertEqual(next_claim["claim"]["task"]["runId"], lead["runId"])

    def test_lead_waits_for_its_own_stop_without_waking_until_settlement(self):
        board = self.board()
        lead, claim = self.lead(board)
        child = self.owned(board, claim)
        self.register(board, "w2")
        child_claim = self.claim(board, "w2", claim_request_id="stopping-child", run_id=child["runId"])
        board.call("workflow_cancel", {"runId": child["runId"], "credential": claim["agentCredential"]})
        self.assertEqual(self.current(board, child)["task"]["state"], "cancelling")
        self.wait(board, claim)
        self.assertEqual(self.current(board, lead)["state"], "waiting-owned")
        self.assertEqual(self.wakeups(board, lead), [])
        self.finish_turn(board, child_claim, worker_id="w2", runner_status="cancelled", seal=False)
        self.assertEqual(self.current(board, lead)["state"], "executing")
        wakeups = self.wakeups(board, lead)
        self.assertEqual(len(wakeups), 1)
        self.assertEqual(json.loads(wakeups[0]["trigger_event_json"])["kind"], "workflow.late_turn")

    def test_manual_continuation_helper_policy_does_not_cancel_lead_owned_work(self):
        board = self.board()
        lead, claim = self.lead(board)
        child = self.owned(board, claim)
        self.wait(board, claim)
        self.continue_run(board, self.current(board, lead), helper_policy="cancel")
        self.assertEqual(self.current(board, child)["state"], "executing")
        self.assertEqual(self.current(board, child)["task"]["state"], "queued")

    def test_lead_waits_until_a_stopped_owned_micro_tasks_helpers_settle(self):
        self._lead_waits_for_helper_stop_report(shutdown=True)

    def test_unconfirmed_nested_stop_report_wakes_without_claiming_stopped(self):
        self._lead_waits_for_helper_stop_report(shutdown=False)

    def _lead_waits_for_helper_stop_report(self, *, shutdown):
        board = self.board()
        lead, claim = self.lead(board)
        child = self.owned(board, claim)
        view, _ = self.finish_owned(board, child, disposition="assistance")
        approved = self.decide(board, view, view["activeRequest"]["requestId"], credential=claim["agentCredential"],
                               helpers=[{"requestId": "stopping-helper", "task": "Help owned work",
                                         "cwd": str(self.workdir("stopping-helper")),
                                         "executionWorkspace": {"kind": "existing", "access": "write"}}])
        helper_id = approved["children"][0]["taskId"]
        helper_claim = self.claim(board, "w2", claim_request_id="helper-to-stop", run_id=helper_id)
        self.wait(board, claim)
        claim = self.claim(board, claim_request_id="lead-stops-helper-tree", run_id=lead["runId"])["claim"]
        prior_wakeups = len(self.wakeups(board, lead))
        board.call("workflow_cancel", {"runId": child["runId"], "credential": claim["agentCredential"]})
        self.assertFalse(self.current(board, child)["task"]["workflowShutdown"]["descendantsConfirmed"])
        self.wait(board, claim)
        self.assertEqual(self.current(board, lead)["state"], "waiting-owned")
        self.assertEqual(len(self.wakeups(board, lead)), prior_wakeups)
        self.finish_turn(board, helper_claim, worker_id="w2", runner_status="cancelled", seal=False, shutdown=shutdown)
        self.assertEqual(self.current(board, child)["task"]["workflowShutdown"]["descendantsConfirmed"], shutdown)
        self.assertEqual(self.current(board, lead)["state"], "executing")
        self.assertEqual(len(self.wakeups(board, lead)), prior_wakeups + 1)
        trigger = json.loads(self.wakeups(board, lead)[-1]["trigger_event_json"])
        with board.store.db.read() as connection:
            event = connection.execute("SELECT payload_json FROM events WHERE seq=?", (trigger["seq"],)).fetchone()
            self.assertEqual(json.loads(event["payload_json"])["stopConfirmed"], shutdown)

    def test_lead_waits_until_a_stopped_owned_micro_tasks_router_settles(self):
        self.catalog_fixture()
        DecisionTestCase.use_helper(self)
        board = self.board()
        DecisionTestCase.seed(self, board)
        lead, claim = self.lead(board)
        child = board.call("workflow_submit", {
            "requestId": "stopping-routed-owned", "task": "Select an executor",
            "cwd": str(self.workdir("stopping-routed-owned")),
            "executionWorkspace": {"kind": "existing", "access": "write"},
            "submissionToken": "r" * 32, "credential": claim["agentCredential"],
        })
        route_id = child["routing"]["taskId"]
        board.call("worker_register", {"workerId": "router", "adapter": "decision", "capabilities": ["decision"]})
        route_claim = self.claim(board, "router", claim_request_id="router-to-stop", run_id=route_id)["claim"]
        board.call("workflow_cancel", {"runId": child["runId"], "credential": claim["agentCredential"]})
        self.wait(board, claim)
        self.assertEqual(self.current(board, lead)["state"], "waiting-owned")
        self.assertEqual(self.wakeups(board, lead), [])
        attempt = route_claim["attempt"]
        board.call("worker_result", {
            "workerId": "router", "attemptId": attempt["attemptId"], "generation": attempt["generation"],
            "nonce": "n" * 16, "status": "cancelled", "result": {"status": "cancelled"}, "shutdownConfirmed": True,
        })
        self.assertEqual(self.current(board, lead)["state"], "executing")
        wakeups = self.wakeups(board, lead)
        self.assertEqual(len(wakeups), 1)
        trigger = json.loads(wakeups[0]["trigger_event_json"])
        self.assertEqual(trigger["kind"], "workflow.owned_stop_reported")
        with board.store.db.read() as connection:
            event = connection.execute("SELECT payload_json FROM events WHERE seq=?", (trigger["seq"],)).fetchone()
            source = json.loads(event["payload_json"])
            self.assertEqual(source["reportedTaskId"], route_id)
            self.assertTrue(source["stopConfirmed"])
            self.assertEqual(connection.execute("SELECT task_id FROM events WHERE seq=?", (source["sourceEventSeq"],)).fetchone()[0], route_id)

    def test_owned_input_is_bounded_and_marks_unchanged_facts_on_the_following_turn(self):
        board = self.board()
        lead, claim = self.lead(board)
        for index in range(workflow.MAX_CHILD_VIEW + 1):
            self.owned(board, claim, "owned-" + str(index))
        self.wait(board, claim)
        self.continue_run(board, self.current(board, lead), command_id="first-manual")
        first = self.claim(board, claim_request_id="second-turn", run_id=lead["runId"])["claim"]
        context = first["turn"]["input"]["context"]
        self.assertEqual(len(context["ownedMicroTasks"]), workflow.MAX_CHILD_VIEW)
        self.assertEqual(context["ownedMicroTaskTotal"], workflow.MAX_CHILD_VIEW + 1)
        self.assertEqual(context["ownedMicroTasksTruncated"], 1)
        large_paths = {**context, "ownedMicroTasks": [
            {**row, "requestId": "a" * 4090} for row in context["ownedMicroTasks"]]}
        bounded = workflow._bound_context(large_paths)
        self.assertLessEqual(len(workflow.canonical_json(bounded).encode()), workflow.schemas.MAX_WORKFLOW_INPUT_BYTES)
        self.assertEqual(bounded["ownedMicroTasksTruncated"],
                         bounded["ownedMicroTaskTotal"] - len(bounded["ownedMicroTasks"]))
        self.wait(board, first)
        self.continue_run(board, self.current(board, lead), command_id="second-manual")
        second = self.claim(board, claim_request_id="third-turn", run_id=lead["runId"])["claim"]
        self.assertFalse(any(row["changedSincePreviousTurn"] for row in second["turn"]["input"]["context"]["ownedMicroTasks"]))

    def test_queue_failure_uses_host_report_instead_of_another_execution_path(self):
        board = self.board()
        lead, claim = self.lead(board)
        child = self.owned(board, claim)
        self.wait(board, claim)
        with patch.object(board.store.workflow, "_requeue", side_effect=BoardError("SHUTDOWN_UNCONFIRMED", "unknown stop")):
            self.finish_owned(board, child)
        view = self.current(board, lead)
        self.assertEqual(view["state"], "awaiting-host")
        self.assertEqual(view["activeRequest"]["kind"], "helper-report")
        self.assertEqual(self.wakeups(board, lead)[0]["state"], "recorded")

    def test_storage_alone_reconstructs_lead_segments_wakeups_owned_turns_usage_and_takeover(self):
        clock = FakeClock()
        board = self.board(clock=clock)
        lead, claim = self.lead(board)
        child = self.owned(board, claim)
        clock.advance(2)
        self.wait(board, claim)
        waiting_at = clock()
        clock.advance(3)
        child_view, child_claim = self.finish_owned(board, child, result_mutator=lambda result: result.update(
            tokenUsage={"inputTokens": 10, "outputTokens": 3, "scope": "attempt", "inputBasis": "includes-cached",
                        "completeness": "complete", "source": "fixture"}))
        woken_at = clock()
        takeover = board.call("workflow_takeover", {"runId": lead["runId"], "commandId": "takeover", "newHostId": "next-host",
                                                    "expectedOwnerGeneration": 1, **lead["control"]})
        self.controls[lead["runId"]] = takeover["control"]
        clock.advance(1)
        resumed = self.claim(board, claim_request_id="resumed", run_id=lead["runId"])["claim"]
        clock.advance(2)
        self.finish_turn(board, {"claim": resumed}, disposition="attention")
        boundary_at = clock()
        self.continue_run(board, self.current(board, lead), command_id="after-review")
        final = self.claim(board, claim_request_id="final-turn", run_id=lead["runId"])["claim"]
        self.accept_run(board, child_view)
        clock.advance(2)
        self.finish_turn(board, {"claim": final})
        delivered_at = clock()
        clock.advance(5)
        self.accept_run(board, self.current(board, lead))
        accepted_at = clock()
        with board.store.db.read() as db:
            turns = db.execute("SELECT * FROM workflow_turns WHERE run_id=? ORDER BY turn_index", (lead["runId"],)).fetchall()
            events = db.execute("SELECT * FROM events WHERE task_id=? ORDER BY seq", (lead["runId"],)).fetchall()
            wakes = db.execute("SELECT * FROM workflow_continuations WHERE run_id=? AND kind='lead-wakeup'", (lead["runId"],)).fetchall()
            owned = db.execute("SELECT * FROM workflow_lead_tasks WHERE lead_run_id=?", (lead["runId"],)).fetchone()
            owned_turn = db.execute("SELECT t.*,a.token_usage_json FROM workflow_turns t JOIN attempts a USING(attempt_id) WHERE t.run_id=?", (owned["owned_run_id"],)).fetchone()
            trigger = db.execute("SELECT * FROM events WHERE seq=?", (json.loads(wakes[0]["trigger_event_json"])["seq"],)).fetchone()
        self.assertEqual(turns[0]["disposition"], "waiting")
        self.assertEqual(turns[0]["updated_at"], waiting_at)
        self.assertEqual(wakes[0]["created_at"], woken_at)
        self.assertEqual(turns[1]["disposition"], "attention")
        self.assertEqual(turns[1]["updated_at"], boundary_at)
        self.assertEqual(wakes[0]["turn_id"], turns[1]["turn_id"])
        self.assertEqual(trigger["task_id"], child["runId"])
        self.assertEqual(owned["lead_turn_id"], turns[0]["turn_id"])
        self.assertEqual(owned_turn["state"], "returned")
        self.assertEqual(json.loads(owned_turn["token_usage_json"])["outputTokens"], 3)
        self.assertTrue(owned_turn["created_at"] <= owned_turn["updated_at"])
        self.assertEqual(next(json.loads(row["payload_json"])["previousHostId"] for row in events if row["kind"] == "workflow.takeover"), "host-1")
        self.assertEqual(turns[2]["disposition"], "completed")
        self.assertEqual(turns[2]["updated_at"], delivered_at)
        self.assertEqual(next(row["created_at"] for row in events if row["kind"] == "workflow.acknowledged"), accepted_at)
        self.assertEqual(len(turns), 3)
