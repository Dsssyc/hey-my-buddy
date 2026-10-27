"""Read frozen Host routing evidence on private boards, without a native call.

The sealed publication fixtures exercise the production completion transaction.
Only the local catalog and workspace seam are substituted for micro-task commands.
"""
from admission import admit_board, admit_store
import copy
import json
import unittest
from unittest.mock import patch

from hey_my_buddy.blackboard.routing import router, router_history, router_sequence
from hey_my_buddy.blackboard.routing.router_boundary import build_boundary
from hey_my_buddy.blackboard.store.db import canonical_json
from hey_my_buddy.errors import BoardError
from hey_my_buddy.blackboard.routing.router_boundary_data import _candidate_problem, _continuation_context, router_trials, routing_boundary
from support import FakeClock
from blackboard.routing.test_router_dispatch import RouterDispatchTestCase, A, B, C, T0, NONCE
import blackboard.routing.test_router_failover_integration as failover
from blackboard.tasks.test_workflow import WorkflowTestCase

TRIAL_FIELDS = {"buddyId", "identity", "index", "phase", "outcome", "code", "nativeIdentity",
                "taskId", "attemptId", "shutdownConfirmed", "consecutiveNoAnswers", "retryAt", "usage"}
BOUNDARY_FIELDS = {"kind", "code", "reason", "routerTrials", "candidates", "facts", "retryAt", "commands", "userAction"}


class BoundaryTemplateIdentityTests(unittest.TestCase):
    def test_template_without_input_uses_its_new_command_digest(self):
        boundary = build_boundary(
            kind="router-unavailable", code=None, reason={"code": "provider-error", "params": {}},
            candidates=[{"buddyId": "buddy-stable", "harness": "dsh", "provider": "fixture",
                         "model": "alpha", "effort": "max"}],
            facts={}, router_trials=[], retry_at="2026-01-01T00:10:00.000Z",
            decision_id="decision-stable", run_id="run-stable", revision=7, shutdown_confirmed=True)
        choice = boundary["commands"]["continue"]["choices"][0]["params"]
        reroute = boundary["commands"]["reroute"]["params"]
        self.assertNotIn("note", choice)
        self.assertNotIn("note", reroute)
        self.assertNotIn("input", choice)
        self.assertNotIn("input", reroute)
        self.assertEqual(choice["commandId"],
                         "router-continue-1a89bba36c2c1d1fb5b75511d98ee75c4f4c01ef51d610fd10d0b4457b198318")
        self.assertEqual(reroute["commandId"],
                         "router-reroute-a6b2b2a4a2970872ddea9d4dead412cd38c68b34b70a2cc5edfa717d72f8f1fc")


class BoundaryReadingTests(RouterDispatchTestCase):
    # Reuse driven helpers, never inherit the old suite or change its assertions.
    start = failover.FailoverTests.start
    answer = failover.FailoverTests.answer
    failure = failover.FailoverTests.failure
    report = failover.FailoverTests.report
    current_task = failover.FailoverTests.current_task
    next_claim = failover.FailoverTests.next_claim
    def claim(self, suffix="1", *, task_id=None):
        result = super().claim(suffix, task_id=task_id)
        if result["claim"]:
            result["claim"]["nonce"] = NONCE + suffix
        return result

    def boundary(self, decision_id, **context):
        with self.board_.store.db.read() as db:
            before = (db.total_changes, db.execute("SELECT COUNT(*) FROM events").fetchone()[0])
            row = self.board_.store.decisions._row(db, decision_id)
            result = routing_boundary(db, row, now=self.clock.value, **context)
            self.assertEqual((db.total_changes, db.execute("SELECT COUNT(*) FROM events").fetchone()[0]), before)
        if result is not None:
            self.assertEqual(set(result), BOUNDARY_FIELDS)
            self.assertEqual(result["userAction"], "settingsChangeOnly")
            for trial in result["routerTrials"]:
                self.assertEqual(TRIAL_FIELDS, set(trial), trial)
        return result

    def test_missing_snapshot_fails_loudly(self):
        decision_id = "dec-missing-snapshot"
        requested = {"kind": "select", "task": "Missing snapshot", "routerCalled": True}
        with self.board_.store.db.write() as db:
            db.execute("INSERT INTO evaluation_decisions(decision_id,status,created_at) VALUES(?,'failed',?)",
                       (decision_id, T0))
            db.execute("INSERT INTO decision_requests(decision_id,request_id,input_fingerprint,requested_json,updated_at) "
                       "VALUES(?,'missing-snapshot','fixture',?,?)", (decision_id, canonical_json(requested), T0))
            with self.assertRaises(KeyError):
                router_sequence.request_snapshot(db, decision_id)
        with self.assertRaises(BoardError):
            self.board_.call("selection_get", {"decisionId": decision_id, "includeAudit": True})

    def finish_all(self):
        request, first = self.start()
        self.report(first, self.failure(first), status="failed")
        second = self.next_claim(request)
        self.report(second, self.failure(second, "provider-error"), status="failed")
        return request, first, second

    def assert_packet(self, boundary, ids=(A, B), *, require_schema=True):
        self.assertEqual([item["buddyId"] for item in boundary["candidates"]], list(ids))
        facts = boundary["facts"]
        self.assertEqual([item["buddyId"] for item in facts["buddies"]], list(ids))
        self.assertIn("policyFacts", facts)
        if require_schema:
            self.assertIn("outputSchema", facts)
        self.assertIn("routingMode", facts)
        for candidate in boundary["candidates"]:
            self.assertTrue({"harness", "provider", "model", "effort"} <= set(candidate))

    def test_all_no_answers_are_unavailable_with_each_immutable_actor_receipt(self):
        request, first, second = self.finish_all()
        result = self.boundary(request["decisionId"])
        self.assertEqual(result["kind"], "router-unavailable")
        self.assertEqual(result["reason"], {"code": "provider-error", "params": {}})
        self.assert_packet(result)
        self.assertEqual(result["retryAt"], "2026-01-01T00:10:00.000Z")
        for index, claim in enumerate((first, second)):
            trial = result["routerTrials"][index]
            self.assertEqual(trial["buddyId"], (A, B)[index])
            self.assertEqual(trial["index"], index)
            self.assertEqual(trial["identity"]["model"], ("alpha", "bravo")[index])
            self.assertEqual(trial["taskId"], claim["task"]["taskId"])
            self.assertEqual(trial["attemptId"], claim["attempt"]["attemptId"])
            self.assertEqual(trial["phase"], "runtime")
            self.assertEqual(trial["outcome"], "no_answer")
            self.assertTrue(trial["shutdownConfirmed"])
            self.assertEqual(trial["consecutiveNoAnswers"], 1)
            self.assertEqual(trial["usage"]["elapsedMs"], 100)
        self.assertEqual([item["code"] for item in result["routerTrials"]], ["timeout", "provider-error"])
        self.assertEqual(self.boundary(request["decisionId"]), result)

    def test_valid_abstention_keeps_original_reason_and_stops_after_actual_actor_b(self):
        request, first = self.start(three=True)
        self.report(first, self.failure(first), status="failed")
        second = self.next_claim(request)
        answer = self.answer(second, buddy_id=None)
        answer["decision"]["note"] = "No justified choice within the frozen candidates"
        self.report(second, answer)
        boundary = self.boundary(request["decisionId"])
        self.assertEqual(boundary["kind"], "router-abstained")
        self.assertEqual(boundary["reason"], {"code": "router-abstained", "params": {}})
        self.assertEqual(boundary["retryAt"], self.clock.value)
        self.assertEqual(boundary["routerTrials"][1]["outcome"], "answered")
        self.assertEqual(boundary["routerTrials"][1]["consecutiveNoAnswers"], 0)
        self.assertIsNone(boundary["routerTrials"][2]["attemptId"])
        self.assertNotEqual(boundary["routerTrials"][2]["outcome"], "no_answer")
        self.assertIsNone(boundary["routerTrials"][2]["phase"])
        self.assertIsNone(boundary["routerTrials"][2]["code"])
        self.assertEqual(self.decision_row(request["decisionId"])["note"], answer["decision"]["note"])
        self.assertNotIn(answer["decision"]["note"], canonical_json(boundary["routerTrials"]))

    def test_runtime_diagnostics_stay_in_their_raw_receipts(self):
        request, first = self.start()
        output = {**self.failure(first), "reason": "private native reason", "error": "private native error"}
        self.report(first, output, status="failed")
        outputs = [(first, output)]
        second = self.next_claim(request)
        output = {**self.failure(second, "provider-error"), "reason": "second private diagnostic"}
        self.report(second, output, status="failed")
        outputs.append((second, output))
        trials = self.boundary(request["decisionId"])["routerTrials"]
        self.assertEqual([trial["code"] for trial in trials], ["timeout", "provider-error"])
        for _claim, output in outputs:
            for field in ("reason", "error"):
                if field in output:
                    self.assertNotIn(output[field], canonical_json(trials))
        with self.board_.store.db.read() as db:
            for claim, original in outputs:
                receipt = json.loads(db.execute("SELECT result_json FROM attempts WHERE attempt_id=?",
                                               (claim["attempt"]["attemptId"],)).fetchone()[0])
                self.assertEqual(receipt["result"], original)

    def test_old_attempt_does_not_borrow_a_new_attempts_decision_code(self):
        request, first = self.start(review=True)
        output = self.answer(first)
        output["inputVerification"]["unchanged"] = False
        self.report(first, output)
        with self.board_.store.db.read() as db:
            row = dict(self.board_.store.decisions._row(db, request["decisionId"]))
            self.assertEqual(router_trials(db, row, now=self.clock.value)[0]["code"], "router-input-changed")
            row.update(decision_attempt_id="newer-attempt", decision_generation=row["decision_generation"] + 1,
                       reason_code="router-out-of-bounds")
            trial = router_trials(db, row, now=self.clock.value)[0]
        self.assertEqual(trial["attemptId"], first["attempt"]["attemptId"])
        self.assertEqual(trial["outcome"], "unknown")
        self.assertIsNone(trial["code"])

    def test_invalid_null_is_no_answer_and_never_model_reason_abstention(self):
        request, first = self.start()
        answer = self.answer(first, buddy_id=None)
        answer["decision"]["note"] = "Router unavailable: model wording is not program classification"
        answer["usage"]["toolCalls"] = None
        self.report(first, answer)
        second = self.next_claim(request)
        self.report(second, self.failure(second), status="failed")
        boundary = self.boundary(request["decisionId"])
        self.assertEqual(boundary["kind"], "router-unavailable")
        self.assertEqual(boundary["routerTrials"][0]["code"], "router-tool-evidence-unverified")
        self.assertNotIn("reason", boundary["routerTrials"][0])

    def test_terminal_publication_error_does_not_make_unadopted_null_an_abstention(self):
        request, first = self.start()
        with patch.object(self.board_.store.decisions, "_publish_select", side_effect=BoardError(
                "FIXTURE_REJECTED", "Private program publication was refused")):
            self.report(first, self.answer(first, buddy_id=None))
        boundary = self.boundary(request["decisionId"])
        self.assertEqual(boundary["kind"], "router-unavailable")
        self.assertNotEqual(boundary["routerTrials"][0]["outcome"], "answered")
        self.assertEqual(self.router_events("router.answered"), [])

    def test_completed_recommendation_with_changed_execution_context_uses_program_route_reason(self):
        request, first = self.start(micro_task=True)
        self.report(first, self.answer(first))
        with self.board_.store.db.write() as db:
            db.execute("UPDATE workflow_routes SET state='needs-host' WHERE decision_id=?",
                       (request["decisionId"],))
        boundary = self.boundary(request["decisionId"])
        self.assertEqual(boundary["kind"], "routing-changed")
        self.assertIsNone(boundary["reason"])
        self.assertEqual(boundary["routerTrials"][0]["outcome"], "answered")

    def test_provider_cooling_deadline_is_a_known_retry_time_without_a_new_fuse(self):
        from hey_my_buddy.blackboard.routing.quota_routing import record_rejection
        self.add_buddy(C, model="charlie")
        self.add_buddy("dsh:fixture:delta:max", model="delta")
        with self.board_.store.db.write() as db:
            db.execute("UPDATE buddies SET provider='workers' WHERE model IN ('charlie','delta')")
            record_rejection(db, "dsh", "fixture", {"code": "quota-exceeded",
                "nativeCode": "fixture-denied", "source": "synthetic/router",
                "observedAt": T0, "resetsAt": "2026-01-01T00:20:00.000Z",
                "rejectionPhase": "before-model"}, now=T0)
        with patch("hey_my_buddy.blackboard.routing.quota_routing.utc_now", return_value=T0):
            request = self.request()
        boundary = self.boundary(request["decisionId"])
        self.assertEqual(boundary["retryAt"], "2026-01-01T00:20:00.000Z")
        self.assertEqual([item["retryAt"] for item in boundary["routerTrials"]],
                         ["2026-01-01T00:20:00.000Z", "2026-01-01T00:20:00.000Z"])
        self.assertEqual(self.tasks(), [])

    def test_queued_dispatch_preflight_failure_keeps_its_own_outcome_and_recovery(self):
        from hey_my_buddy.blackboard.routing.quota_routing import record_rejection
        with self.board_.store.db.write() as db:
            db.execute("UPDATE buddies SET provider='other-router' WHERE buddy_id=?", (B,))
        request = self.request()
        with self.board_.store.db.write() as db:
            record_rejection(db, "dsh", "fixture", {"code": "quota-exceeded",
                "nativeCode": "fixture-denied", "source": "synthetic/router",
                "observedAt": T0, "resetsAt": "2026-01-01T00:20:00.000Z",
                "rejectionPhase": "before-model"}, now=T0)
        with patch("hey_my_buddy.blackboard.routing.quota_routing.utc_now", return_value=T0):
            first = self.claim("preflight", task_id=request["runId"])
        self.assertIsNone(first["claim"])
        second = self.next_claim(request)
        self.report(second, self.failure(second), status="failed")
        trial = self.boundary(request["decisionId"])["routerTrials"][0]
        self.assertEqual(trial["taskId"], request["runId"])
        self.assertIsNone(trial["attemptId"])
        self.assertEqual(trial["phase"], "preflight")
        self.assertEqual(trial["outcome"], "no_answer")
        self.assertEqual(trial["code"], "router-quota-exhausted")
        self.assertEqual(trial["retryAt"], "2026-01-01T00:20:00.000Z")
        self.assertTrue(trial["shutdownConfirmed"])
        self.assertIsNone(trial["usage"])

    def test_settings_edit_does_not_change_the_frozen_no_answer_path(self):
        request, first = self.start()
        self.set_settings(ids=[B, A], mode="review", interval=30)
        self.report(first, self.failure(first), status="failed")
        self.assertIsNone(self.boundary(request["decisionId"]))
        self.assertEqual(self.decision_row(request["decisionId"])["status"], "queued")
        self.assertEqual(self.snapshot_of(request["decisionId"])["facts"]["routerBuddyIds"], [A, B])

    def test_current_view_has_actor_b_even_queued_and_original_request_stays_actor_free(self):
        request, first = self.start()
        original = self.snapshot_of(request["decisionId"])
        self.report(first, self.failure(first), status="failed")
        current = self.decision_row(request["decisionId"])
        self.assertEqual(current["status"], "queued")
        self.assertEqual(current["routerBuddyId"], B)
        self.assertEqual(current["routerBuddy"]["model"], "bravo")
        self.assertEqual(current["routerIndex"], 1)
        self.assertEqual(current["decisionModel"]["requested"]["model"], "bravo")
        self.assertEqual(current["routerBuddyIds"], [A, B])
        with self.board_.store.db.read() as db:
            row = self.board_.store.decisions._row(db, request["decisionId"])
            requested = json.loads(row["requested_json"])
        self.assertNotIn("routerBuddyId", requested)
        self.assertNotIn("routerBuddy", requested)
        self.assertEqual(self.snapshot_of(request["decisionId"]), original)

    def test_no_dispatch_does_not_invent_first_configured_actor(self):
        self.set_settings(ids=[A, B])
        with self.board_.store.db.write() as db:
            db.execute("UPDATE buddies SET enabled=0")
        # Separate legal candidates preserve a real routing admission.
        self.add_buddy(C, model="charlie")
        self.add_buddy("dsh:fixture:delta:max", model="delta")
        request = self.request()
        current = self.decision_row(request["decisionId"])
        self.assertIsNone(current["routerBuddyId"])
        self.assertIsNone(current["routerBuddy"])
        self.assertIsNone(current["routerIndex"])
        self.assertIsNone(current["decisionModel"]["requested"])
        boundary = self.boundary(request["decisionId"])
        self.assertEqual(boundary["kind"], "router-unavailable")
        self.assertEqual(self.tasks(), [])
        self.assertTrue(all(trial["attemptId"] is None for trial in boundary["routerTrials"]))
        self.assertIsNone(boundary["retryAt"])
        self.assertTrue(all(trial["retryAt"] is None and trial["code"] == "router-disabled"
                            for trial in boundary["routerTrials"]))

    def test_empty_router_list_keeps_complete_packet_without_model_or_business_commands(self):
        self.set_settings(ids=[])
        request = self.request()
        boundary = self.boundary(request["decisionId"])
        self.assertEqual(request["decision"]["routingBoundary"], boundary)
        self.assert_packet(boundary)
        self.assertEqual(boundary["routerTrials"], [])
        self.assertIsNone(boundary["retryAt"])
        self.assertTrue(boundary["commands"]["continue"]["blocked"])
        self.assertEqual(boundary["commands"]["continue"]["choices"], [])
        self.assertIsNone(boundary["commands"]["reroute"]["params"])
        self.assertEqual(self.tasks(), [])
        self.assertEqual(self.attempts(), [])

    def test_router_packet_keeps_all_candidates(self):
        delta = "dsh:fixture:delta:max"
        self.add_buddy(C, model="charlie")
        self.add_buddy(delta, model="delta")
        request = self.request(request_id="four-candidates")
        self.assertEqual(request["status"], "queued")
        with self.board_.store.db.read() as db:
            snapshot = router_sequence.request_snapshot(db, request["decisionId"])
        self.assertEqual([item["buddyId"] for item in snapshot["baseInput"]["buddies"]],
                         [A, B, C, delta])

    def test_later_buddies_and_preferences_cannot_replace_frozen_facts(self):
        self.set_settings(ids=[])
        with self.board_.store.db.write() as db:
            db.execute("INSERT INTO model_service_notes VALUES('dsh','fixture','alpha','private note',?)", (T0,))
            db.execute("INSERT INTO evaluation_preferences VALUES(?,'prefer','private preference')", (B,))
        request = self.request()
        frozen = self.boundary(request["decisionId"])
        with self.board_.store.db.write() as db:
            db.execute("UPDATE buddies SET model='later-model'")
            db.execute("UPDATE evaluation_preferences SET note='later preference'")
            db.execute("DELETE FROM model_service_notes")
        current = self.boundary(request["decisionId"])
        self.assertEqual(current["facts"], frozen["facts"])
        self.assertEqual(current["candidates"], frozen["candidates"])
        self.assertNotIn("later-model", canonical_json(current["facts"]))

    def test_skip_is_not_an_execution_or_new_failure_and_retry_time_does_not_roll(self):
        self.add_buddy(C, model="charlie")
        with self.board_.store.db.write() as db:
            router_history.record_outcome(db, buddy_id=A, request_id="earlier", task_id=None,
                attempt_id=None, outcome="no_answer", code="timeout", phase="runtime", facts={"routerIndex": 0}, now=T0)
        request = self.request()
        second = self.claim("second", task_id=request["runId"])["claim"]
        self.report(second, self.failure(second), status="failed")
        result = self.boundary(request["decisionId"])
        first = result["routerTrials"][0]
        self.assertEqual(first["outcome"], "skipped")
        self.assertIsNone(first["attemptId"])
        self.assertIsNone(first["taskId"])
        self.assertIsNone(first["usage"])
        self.assertEqual(first["consecutiveNoAnswers"], 1)
        self.assertEqual(first["retryAt"], "2026-01-01T00:10:00.000Z")
        self.clock.advance(60)
        repeated = self.boundary(request["decisionId"])
        self.assertEqual(repeated["retryAt"], result["retryAt"])
        self.assertEqual(repeated["routerTrials"][0]["retryAt"], first["retryAt"])
        self.assertEqual(len(self.router_events("router.no_answer")), 2)

    def test_all_skipped_uses_original_consecutive_counts_and_earliest_retry(self):
        for ordinal, buddy_id in enumerate((A, A, A, B, B)):
            if ordinal == 3:
                self.clock.advance(10)
            with self.board_.store.db.write() as db:
                router_history.record_outcome(db, buddy_id=buddy_id, request_id="earlier-" + str(ordinal),
                    task_id=None, attempt_id=None, outcome="no_answer", code="timeout", phase="runtime",
                    facts={"routerIndex": 0}, now=self.clock.value)
        request = self.request()
        boundary = self.boundary(request["decisionId"])
        self.assertEqual(boundary["kind"], "router-unavailable")
        self.assertEqual(boundary["retryAt"], "2026-01-01T00:10:00.000Z")
        self.assertEqual([trial["consecutiveNoAnswers"] for trial in boundary["routerTrials"]], [3, 2])
        self.assertEqual([trial["outcome"] for trial in boundary["routerTrials"]], ["skipped", "skipped"])
        self.assertEqual(len(self.router_events("router.no_answer")), 5)
        self.assertEqual(self.tasks(), [])
        self.assertEqual(self.attempts(), [])

    def test_real_preflight_no_answer_is_distinct_from_skipped_and_has_no_fake_attempt(self):
        self.add_buddy(C, model="charlie")
        with self.board_.store.db.write() as db:
            db.execute("UPDATE buddies SET enabled=0 WHERE buddy_id=?", (A,))
        request = self.request()
        second = self.claim("second", task_id=request["runId"])["claim"]
        self.report(second, self.failure(second), status="failed")
        first = self.boundary(request["decisionId"])["routerTrials"][0]
        self.assertEqual(first["phase"], "preflight")
        self.assertEqual(first["outcome"], "no_answer")
        self.assertEqual(first["code"], "router-disabled")
        self.assertEqual(first["consecutiveNoAnswers"], 1)
        self.assertIsNone(first["attemptId"])
        self.assertIsNone(first["usage"])

    def test_unknown_stop_blocks_micro_task_commands_and_does_not_claim_prior_output(self):
        request, first = self.start()
        self.report(first, self.failure(first), status="failed", shutdown=False)
        self.assertIsNone(self.boundary(request["decisionId"], run_id="run-1", revision=3))
        self.assertEqual(self.decision_row(request["decisionId"])["status"], "running")
        self.assertEqual(self.board_.client().get(runId=first["task"]["taskId"])["attemptState"], "finalizing")
        self.assertEqual(len(self.tasks()), 1)
        self.assertEqual(self.router_events("router.no_answer", "router.answered"), [])

    def test_incomplete_native_evidence_is_a_real_no_answer_with_unknown_usage_kept_null(self):
        request, first = self.start()
        self.report(first, self.answer(first, stream_complete=False))
        second = self.next_claim(request)
        output = self.failure(second)
        output["usage"]["bytesRead"] = None
        self.report(second, output, status="failed")
        result = self.boundary(request["decisionId"])
        self.assertEqual(result["routerTrials"][0]["code"], "router-tool-evidence-unverified")
        self.assertEqual(result["routerTrials"][0]["outcome"], "no_answer")
        self.assertIsNone(result["routerTrials"][1]["usage"]["bytesRead"])

    def test_success_and_sole_candidate_paths_do_not_invent_router_boundaries(self):
        request, first = self.start()
        self.report(first, self.answer(first))
        self.assertIsNone(self.boundary(request["decisionId"]))
        with self.board_.store.db.write() as db:
            db.execute("DELETE FROM buddies WHERE buddy_id=?", (B,))
        sole = self.request(request_id="sole")
        self.assertIsNone(self.boundary(sole["decisionId"]))
        self.assertFalse(self.decision_row(sole["decisionId"])["routerCalled"])
        with self.board_.store.db.write() as db:
            db.execute("UPDATE buddies SET enabled=0")
        no_candidate = self.request(request_id="none-legal")
        zero = self.boundary(no_candidate["decisionId"])
        self.assertEqual(zero["kind"], "routing-changed")
        self.assertEqual(zero["candidates"], [])
        self.assertEqual(zero["routerTrials"], [])
        self.assertEqual(self.snapshot_of(no_candidate["decisionId"])["baseInput"]["task"], "fix the parser")

    def test_cancelled_dispatch_stays_cancelled_without_new_router_failure(self):
        request, first = self.start()
        with self.board_.store.db.write() as db:
            db.execute("UPDATE attempts SET cancel_requested_at=? WHERE attempt_id=?", (T0, first["attempt"]["attemptId"]))
        self.report(first, self.failure(first), status="failed")
        boundary = self.boundary(request["decisionId"])
        self.assertEqual(boundary["routerTrials"][0]["outcome"], "cancelled")
        self.assertIsNone(boundary["routerTrials"][0]["code"])
        self.assertEqual(len(self.tasks()), 1)
        self.assertEqual(self.router_events("router.no_answer", "router.answered"), [])

    def test_cancelled_queued_dispatch_needs_only_its_phase_and_outcome(self):
        request = self.request()
        self.board_.call("task_cancel", {"runId": request["runId"]})
        trial = self.boundary(request["decisionId"])["routerTrials"][0]
        self.assertEqual(trial["phase"], "preflight")
        self.assertEqual(trial["outcome"], "cancelled")
        self.assertIsNone(trial["code"])
        self.assertIsNone(trial["attemptId"])

    def test_native_stop_unknown_is_not_made_confirmed_by_outer_finished_receipt(self):
        request, first = self.start()
        output = self.failure(first)
        output["stopEvidence"]["native"]["shutdownConfirmed"] = None
        self.report(first, output, status="failed")
        trial = self.boundary(request["decisionId"])["routerTrials"][0]
        self.assertEqual(trial["outcome"], "stopUnknown")
        self.assertIsNone(trial["shutdownConfirmed"])
        self.assertEqual(self.router_events("router.no_answer", "router.answered"), [])

    def test_native_explicit_false_stays_false_and_blocked(self):
        request, first = self.start()
        output = self.failure(first)
        output["stopEvidence"]["native"]["shutdownConfirmed"] = False
        self.report(first, output, status="failed")
        boundary = self.boundary(request["decisionId"])
        self.assertEqual(boundary["routerTrials"][0]["outcome"], "stopUnknown")
        self.assertIs(boundary["routerTrials"][0]["shutdownConfirmed"], False)
        self.assertTrue(boundary["commands"]["continue"]["blocked"])


class MicroTaskBoundaryTests(WorkflowTestCase):
    answer = failover.FailoverTests.answer

    def setUp(self):
        super().setUp()
        self.clock = FakeClock(T0)
        self.board_ = self.board(clock=self.clock)
        with self.board_.store.db.write() as db:
            for buddy_id, model in ((A, "alpha"), (B, "bravo")):
                db.execute("INSERT INTO buddies(buddy_id,label,harness,provider,model,effort,"
                           "available,enabled,capabilities_json) "
                           "VALUES(?,?,'dsh','fixture',?,'max',1,1,?)",
                           (buddy_id, buddy_id, model, canonical_json([])))
            router._write_settings(db, {**router.configuration(db), "routerBuddyIds": []})

    def routed(self, request_id="micro-task-1", *, access="write", kind="existing"):
        response = self.board_.call("workflow_submit", {"requestId": request_id, "hostId": "host-1",
            "submissionToken": "private-submission-token", "task": "Private routed implementation",
            "cwd": str(self.workdir(request_id)), "executionWorkspace": {"kind": kind, "access": access}})
        self.controls[response["runId"]] = response["control"]
        return response

    def template_params(self, view, template, *, note=None):
        params = copy.deepcopy(template)
        self.assertEqual(params.pop("controlFile"), "<saved-control-file>")
        params.update(self.control(view))
        params["input"] = "Host continues the frozen micro task"
        if note is not None:
            params["note"] = note
        return params

    def test_micro_task_boundary_has_post_attention_revision_and_real_continue_tuple(self):
        view = self.routed()
        boundary = view["routing"]["routingBoundary"]
        self.assertEqual(view["activeRequest"]["routingBoundary"], boundary)
        choices = boundary["commands"]["continue"]["choices"]
        self.assertFalse(boundary["commands"]["continue"]["blocked"])
        self.assertEqual([item["buddyId"] for item in choices], [A, B])
        for choice in choices:
            params = choice["params"]
            self.assertEqual(params["runId"], view["runId"])
            self.assertEqual(params["expectedRevision"], view["revision"])
            self.assertNotIn("input", params)
            self.assertNotIn("note", params)
            self.assertLessEqual(len(params["commandId"]), 128)
        chosen = choices[1]["params"]
        host_params = self.template_params(view, chosen)
        host_params["note"] = "Host 选择冻结候选中的第二位 buddy 继续"
        self.assertEqual(host_params["commandId"], chosen["commandId"])
        continued = self.board_.call("workflow_continue", host_params)
        self.assertEqual(continued["buddy"], chosen["buddy"])
        self.assertEqual(continued["runId"], view["runId"])
        self.assertFalse(continued["awaitingHost"])
        self.assertTrue(self.board_.call("workflow_continue", host_params)["duplicate"])

    def test_zero_candidates_offer_only_an_explicit_buddy_continue_template(self):
        from hey_my_buddy.blackboard.routing.quota_routing import record_rejection
        for mode in ("disabled", "cooling"):
            with self.subTest(mode=mode):
                with self.board_.store.db.write() as db:
                    if mode == "disabled":
                        db.execute("UPDATE buddies SET enabled=0")
                    else:
                        db.execute("UPDATE buddies SET enabled=1")
                        record_rejection(db, "dsh", "fixture", {"code": "quota-exceeded",
                            "nativeCode": "fixture-denied", "source": "synthetic/zero-candidates",
                            "observedAt": T0, "resetsAt": None,
                            "rejectionPhase": "before-model"}, now=T0)
                view = self.routed("zero-" + mode)
                boundary = view["routing"]["routingBoundary"]
                self.assertEqual(boundary["candidates"], [])
                self.assertEqual(boundary["routerTrials"], [])
                command = boundary["commands"]["continue"]
                self.assertFalse(command["blocked"])
                self.assertEqual(len(command["choices"]), 1)
                choice = command["choices"][0]
                self.assertIsNone(choice["buddyId"])
                self.assertEqual(choice["params"]["buddy"],
                                 {key: f"<{key}>" for key in ("harness", "provider", "model", "effort")})
                self.assertEqual(choice["params"]["runId"], view["runId"])
                self.assertEqual(choice["params"]["expectedRevision"], view["revision"])
                self.assertTrue(boundary["commands"]["reroute"]["blocked"])
                self.assertIsNone(boundary["commands"]["reroute"]["params"])
                with self.board_.store.db.read() as db:
                    self.assertEqual(db.execute("SELECT COUNT(*) FROM router_dispatches").fetchone()[0], 0)
                    self.assertEqual(router_sequence.request_snapshot(db, view["routing"]["decisionId"])
                                     ["baseInput"]["task"], "Private routed implementation")
                if mode == "disabled":
                    with self.board_.store.db.write() as db:
                        db.execute("UPDATE buddies SET enabled=1")
                    params = self.template_params(view, choice["params"], note="Host supplies the now enabled buddy")
                    params["buddy"] = {"harness": "dsh", "provider": "fixture", "model": "bravo", "effort": "max"}
                    continued = self.board_.call("workflow_continue", params)
                    self.assertEqual(continued["runId"], view["runId"])
                    self.assertEqual(continued["buddy"], params["buddy"])
                    self.assertFalse(continued["awaitingHost"])

    def test_buddy_required_error_explains_same_boundary_commands(self):
        view = self.routed()
        with self.assertRaises(BoardError) as raised:
            self.continue_run(self.board_, view)
        self.assertEqual(raised.exception.code, "BUDDY_REQUIRED")
        self.assertEqual(raised.exception.details["routingBoundary"], view["routing"]["routingBoundary"])

    def test_get_rebuilds_stable_templates_when_owner_revision_changes(self):
        view = self.routed()
        before = view["routing"]["routingBoundary"]["commands"]["continue"]["choices"][0]["params"]
        with self.board_.store.db.write() as db:
            db.execute("UPDATE workflow_runs SET revision=revision+1 WHERE run_id=?", (view["runId"],))
        latest = self.board_.call("workflow_get", {"runId": view["runId"]})
        after = latest["routing"]["routingBoundary"]["commands"]["continue"]["choices"][0]["params"]
        self.assertEqual(after["expectedRevision"], latest["revision"])
        self.assertNotEqual(after["commandId"], before["commandId"])
        self.assertEqual(latest["activeRequest"]["routingBoundary"], latest["routing"]["routingBoundary"])
        self.assertEqual(self.board_.call("workflow_get", {"runId": view["runId"]})["routing"]["routingBoundary"],
                         latest["routing"]["routingBoundary"])

    def test_stored_command_input_hash_and_receipt_replay_are_unchanged_by_new_templates(self):
        view = self.routed()
        template = view["routing"]["routingBoundary"]["commands"]["continue"]["choices"][0]["params"]
        params = self.template_params(view, template, note="Host keeps the recorded continuation note")
        params.update(commandId="router-continue-50c19c335009bc53af3aee2c71a272510dd18cc3cb2848756e3fd64a5466dc19",
                      input="继续既定微任务。")
        self.assertNotEqual(params["commandId"], template["commandId"])
        receipt = self.board_.call("workflow_continue", params)
        with self.board_.store.db.read() as db:
            recorded = dict(db.execute("SELECT * FROM commands WHERE command_id=?", (params["commandId"],)).fetchone())
            continuation = dict(db.execute("SELECT * FROM workflow_continuations WHERE run_id=?",
                                           (view["runId"],)).fetchone())
        self.assertEqual(continuation["input_text"], params["input"])
        replay = self.board_.call("workflow_continue", params)
        self.assertEqual(replay, {**receipt, "duplicate": True})
        with self.board_.store.db.read() as db:
            self.assertEqual(dict(db.execute("SELECT * FROM commands WHERE command_id=?", (params["commandId"],)).fetchone()),
                             recorded)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM workflow_continuations WHERE run_id=?",
                                        (view["runId"],)).fetchone()[0], 1)

    def test_request_and_timeline_project_the_same_current_decision_reason(self):
        view = self.routed()
        for code, params in (("router-not-configured", {}), ("router-input-too-large", {"bytes": 200, "limit": 100})):
            with self.subTest(code=code):
                with self.board_.store.db.write() as db:
                    db.execute("UPDATE evaluation_decisions SET reason_code=?,reason_params_json=? WHERE decision_id=?",
                               (code, canonical_json(params), view["routing"]["decisionId"]))
                current = self.board_.call("workflow_get", {"runId": view["runId"]})
                timeline = self.board_.call("macro_task_timeline", {"macroTaskId": f"run:{view['runId']}"})
                host = next(span for span in timeline["spans"] if span.get("requestId") == view["activeRequest"]["requestId"])
                self.assertEqual(host["reason"], {"code": code, "params": params})
                self.assertEqual(host["reason"], current["activeRequest"]["reason"])

    def test_continuation_gates_report_codes_from_their_own_facts(self):
        view = self.routed()
        with self.board_.store.db.write() as db:
            row = self.board_.store.decisions._row(db, view["routing"]["decisionId"])
            self.assertEqual(_continuation_context(db, row, "missing-run", None)[:2],
                             (False, "micro-task-not-found"))
            self.assertEqual(_continuation_context(db, row, view["runId"], "another-owner")[:2],
                             (True, "micro-task-not-owned"))
            db.execute("UPDATE workflow_routes SET owner_generation=owner_generation+1 WHERE decision_id=?",
                       (row["decision_id"],))
            self.assertEqual(_continuation_context(db, row, view["runId"], None)[:2],
                             (True, "workflow-routing-fenced"))

    def test_candidate_gates_use_one_code_with_precise_bound_facts(self):
        from hey_my_buddy.blackboard.routing.quota_routing import record_rejection
        view = self.routed()
        candidate = view["routing"]["routingBoundary"]["candidates"][1]
        cases = (
            ("identity-changed", "UPDATE buddies SET model='changed' WHERE buddy_id=?", (B,), {}),
            ("disabled", "UPDATE buddies SET enabled=0 WHERE buddy_id=?", (B,), {}),
            ("unavailable", "UPDATE buddies SET available=0 WHERE buddy_id=?", (B,), {}),
            ("constraints", None, (), {"constraints": {"model": "other"}}),
            ("capability-missing", None, (), {"requiredCapabilities": ["fixture-only"]}),
            ("excluded", "INSERT INTO evaluation_preferences VALUES(?,'exclude','Host content')", (B,), {}),
            ("not-pinned", "INSERT INTO evaluation_preferences VALUES(?,'pin','Host content')", (A,), {}),
            ("harness-unavailable", "DELETE FROM harness_health WHERE harness='dsh'", (), {}),
            ("quota-exhausted", None, (), {}),
        )
        with self.board_.store.db.write() as db:
            for bound, statement, values, request in cases:
                with self.subTest(bound=bound):
                    db.execute("SAVEPOINT candidate_gate")
                    if statement:
                        db.execute(statement, values)
                    if bound == "quota-exhausted":
                        record_rejection(db, "dsh", "fixture", {"code": "quota-exceeded",
                            "nativeCode": "fixture-denied", "source": "synthetic/candidate",
                            "observedAt": self.clock.value, "resetsAt": None,
                            "rejectionPhase": "before-model"}, now=self.clock.value)
                    self.assertEqual(_candidate_problem(db, candidate, request, None, self.clock.value),
                                     {"code": "router-out-of-bounds", "bound": bound})
                    db.execute("ROLLBACK TO candidate_gate")
                    db.execute("RELEASE candidate_gate")

    def test_all_blocked_candidates_need_no_additional_command_code(self):
        view = self.routed()
        with self.board_.store.db.write() as db:
            db.execute("UPDATE buddies SET enabled=0")
        command = self.board_.call("workflow_get", {"runId": view["runId"]})["routing"]["routingBoundary"]["commands"]["continue"]
        self.assertTrue(command["blocked"])
        self.assertIsNone(command["code"])
        self.assertTrue(all(choice["blocked"] and choice["code"] == "router-out-of-bounds"
                            and choice["bound"] == "disabled" for choice in command["choices"]))
        self.assertNotIn("reason", command)

    def test_frozen_candidate_is_retained_but_changed_identity_cannot_offer_legal_command(self):
        view = self.routed()
        original = view["routing"]["routingBoundary"]
        with self.board_.store.db.write() as db:
            db.execute("UPDATE buddies SET model='changed-model' WHERE buddy_id=?", (B,))
        latest = self.board_.call("workflow_get", {"runId": view["runId"]})["routing"]["routingBoundary"]
        self.assertEqual(latest["candidates"], original["candidates"])
        self.assertEqual(latest["facts"], original["facts"])
        choice = next(item for item in latest["commands"]["continue"]["choices"] if item["buddyId"] == B)
        self.assertTrue(choice["blocked"])
        self.assertEqual(choice["code"], "router-out-of-bounds")
        self.assertEqual(choice["bound"], "identity-changed")
        self.assertNotIn("reason", choice)
        self.assertEqual(choice["params"]["buddy"]["model"], "bravo")

    def test_cleanup_guard_blocks_templates_and_existing_continue_operation(self):
        view = self.routed()
        chosen = view["routing"]["routingBoundary"]["commands"]["continue"]["choices"][0]["params"]
        with self.board_.store.db.write() as db:
            db.execute("INSERT INTO workspace_cleanup_plans(plan_id,run_id,workspace_id,checkout_id,path,kind,state,"
                       "evidence_json,retention_json,created_at,expires_at) "
                       "VALUES('private-cleanup',?,'workspace','checkout','/private-fixture','existing','applying',"
                       "'{}','{}',?,?)", (view["runId"], T0, T0))
        latest = self.board_.call("workflow_get", {"runId": view["runId"]})
        for command in latest["routing"]["routingBoundary"]["commands"].values():
            self.assertTrue(command["blocked"])
            self.assertEqual(command["code"], "workspace-cleanup-started")
            self.assertNotIn("reason", command)
        with self.assertRaises(BoardError) as raised:
            self.board_.call("workflow_continue", self.template_params(
                view, chosen, note="Host 选择候选时仍需先完成工作区清理"))
        self.assertEqual(raised.exception.code, "CONFLICT")

    def test_missing_workspace_ownership_blocks_templates_without_reacquiring_it(self):
        view = self.routed(kind="worktree")
        original = view["routing"]["routingBoundary"]
        params = original["commands"]["continue"]["choices"][0]["params"]
        with self.board_.store.db.write() as db:
            db.execute("DELETE FROM workspace_reservations WHERE holder_task_id=?", (view["runId"],))
        boundary = self.board_.call("workflow_get", {"runId": view["runId"]})["routing"]["routingBoundary"]
        self.assertTrue(boundary["commands"]["continue"]["blocked"])
        self.assertEqual(boundary["commands"]["continue"]["code"], "workspace-ownership-lost")
        self.assertEqual(boundary["candidates"], original["candidates"])
        with self.assertRaises(BoardError) as raised:
            self.board_.call("workflow_continue", self.template_params(
                view, params, note="Host 选择候选时工作区所有权已失效"))
        self.assertEqual(raised.exception.code, "PREPARATION_CONFLICT")

    def test_locked_hard_tuple_keeps_frozen_candidates_and_blocks_other_choice(self):
        view = self.routed()
        original = view["routing"]["routingBoundary"]
        with self.board_.store.db.write() as db:
            row = db.execute("SELECT spec_json FROM tasks WHERE task_id=?", (view["runId"],)).fetchone()
            micro_task = json.loads(row[0])
            micro_task.update({"harness": "dsh", "provider": "fixture", "model": "alpha", "effort": "max"})
            db.execute("UPDATE tasks SET spec_json=? WHERE task_id=?",
                       (canonical_json(micro_task), view["runId"]))
            db.execute("UPDATE workflow_runs SET buddy_locked=1 WHERE run_id=?", (view["runId"],))
        latest = self.board_.call("workflow_get", {"runId": view["runId"]})["routing"]["routingBoundary"]
        self.assertEqual(latest["candidates"], original["candidates"])
        other = next(item for item in latest["commands"]["continue"]["choices"] if item["buddyId"] == B)
        self.assertTrue(other["blocked"])
        self.assertEqual(other["code"], "router-out-of-bounds")
        self.assertEqual(other["bound"], "constraints")
        with self.assertRaises(BoardError) as raised:
            self.board_.call("workflow_continue", self.template_params(
                view, other["params"], note="Host 请求另一个冻结候选"))
        self.assertEqual(raised.exception.code, "BUDDY_CONFLICT")

    def test_nested_boundary_uses_root_control_and_current_owner_revision(self):
        root, leaf = self.routed("root", kind="worktree"), self.routed("leaf", kind="worktree")
        with self.board_.store.db.write() as db:
            db.execute("INSERT INTO workflow_children(child_task_id,parent_run_id,request_id,decision_command_id,state,"
                       "created_at,updated_at) VALUES(?,?, 'private-request','private-authorization',"
                       "'attention',?,?)", (leaf["runId"], root["runId"], T0, T0))
            source = self.board_.store.workflow._request_row(db, leaf["runId"], leaf["activeRequest"]["requestId"])
            self.board_.store.workflow._propagate_boundary(db, source, T0)
        root = self.board_.call("workflow_get", {"runId": root["runId"]})
        leaf = self.board_.call("workflow_get", {"runId": leaf["runId"]})
        for choice in root["routing"]["routingBoundary"]["commands"]["continue"]["choices"]:
            self.assertEqual(choice["params"]["helperPolicy"], "keep")
        proxy = next(item for item in root["pendingRequests"] if (item.get("proxy") or {}).get("runId") == leaf["runId"])
        self.assertEqual(proxy["routingBoundary"], leaf["routing"]["routingBoundary"])
        self.assertEqual(proxy["decisionId"], leaf["routing"]["decisionId"])
        self.assertEqual(proxy["reason"], leaf["activeRequest"]["reason"])
        with self.board_.store.db.read() as db:
            source, copied = (db.execute(
                "SELECT summary,reason_code,reason_params_json,decision_id,payload_json"
                " FROM workflow_requests WHERE request_id=?", (request_id,),
            ).fetchone() for request_id in (leaf["activeRequest"]["requestId"], proxy["requestId"]))
            self.assertEqual(source["decision_id"], leaf["routing"]["decisionId"])
            self.assertEqual(copied["decision_id"], source["decision_id"])
            self.assertIsNone(copied["summary"])
            self.assertIsNone(copied["reason_code"])
            self.assertIsNone(copied["reason_params_json"])
            self.assertEqual(json.loads(copied["payload_json"])["origin"]["runId"], leaf["runId"])
        boundary = leaf["routing"]["routingBoundary"]
        chosen = boundary["commands"]["continue"]["choices"][0]["params"]
        self.assertEqual(chosen["runId"], root["runId"])
        self.assertEqual(chosen["targetRunId"], leaf["runId"])
        self.assertEqual(chosen["expectedRevision"], root["revision"])
        self.assertEqual(chosen["controlFile"], "<saved-control-file>")
        self.assertNotIn("note", chosen)
        self.assertNotIn("controlToken", canonical_json(boundary))
        params = self.template_params(root, chosen)
        params["note"] = "Host 指定拥有的微任务使用冻结候选"
        continued = self.board_.call("workflow_continue", params)
        self.assertEqual(continued["runId"], root["runId"])
        continued_leaf = self.board_.call("workflow_get", {"runId": leaf["runId"]})
        self.assertEqual(continued_leaf["buddy"], chosen["buddy"])
        self.assertFalse(continued_leaf["awaitingHost"])

    def test_terminal_ancestor_blocks_leaf_templates_and_continue_cannot_bypass_it(self):
        root, leaf = self.routed("root", kind="worktree"), self.routed("leaf", kind="worktree")
        with self.board_.store.db.write() as db:
            db.execute("INSERT INTO workflow_children(child_task_id,parent_run_id,request_id,decision_command_id,state,"
                       "created_at,updated_at) VALUES(?,?, 'private-request','private-authorization',"
                       "'attention',?,?)", (leaf["runId"], root["runId"], T0, T0))
        before = self.board_.call("workflow_get", {"runId": leaf["runId"]})
        chosen = before["routing"]["routingBoundary"]["commands"]["continue"]["choices"][0]["params"]
        for terminal in ("cancelled", "concluded", "accepted"):
            with self.subTest(terminal=terminal):
                with self.board_.store.db.write() as db:
                    db.execute("UPDATE workflow_runs SET state=? WHERE run_id=?", (terminal, root["runId"]))
                after = self.board_.call("workflow_get", {"runId": leaf["runId"]})
                self.assertTrue(after["routing"]["routingBoundary"]["commands"]["continue"]["blocked"])
                self.assertEqual(after["routing"]["routingBoundary"]["commands"]["continue"]["code"],
                                 "ANCESTOR_TERMINAL")
                with self.assertRaises(BoardError) as raised:
                    self.board_.call("workflow_continue", self.template_params(
                        root, chosen, note="Host 请求已结束的拥有任务使用冻结候选"))
                self.assertEqual(raised.exception.code, "ANCESTOR_TERMINAL")

    def test_cli_brief_get_and_await_retain_only_existing_boundary_data(self):
        from hey_my_buddy.cli import blocking, cli_views
        from hey_my_buddy.protocol.transport import METHOD_MAP
        view = self.routed()
        boundary = view["routing"]["routingBoundary"]
        rendered = cli_views.render("get", view, cli_views.OUTPUT_BRIEF)
        self.assertEqual(rendered["routing"]["routingBoundary"], boundary)

        def service(method, params, state_dir):
            response = self.board_.call(METHOD_MAP[method][1], params)
            return response["task"] if method == "status" else response

        envelope = blocking.await_run({"runId": view["runId"]}, self.board_.directory, service=service)
        full = cli_views.render("await", envelope, cli_views.OUTPUT_FULL)
        brief = cli_views.render("await", envelope, cli_views.OUTPUT_BRIEF)
        self.assertEqual(brief["nextCommands"], full["nextCommands"])
        self.assertTrue(brief["nextCommands"])
        for awaited in (brief, full):
            self.assertEqual(awaited["routingBoundary"], boundary)
            self.assertEqual(awaited["request"]["routingBoundary"], boundary)
            self.assertTrue(all(isinstance(item["params"]["buddy"], dict)
                                for item in awaited["nextCommands"] if "buddy" in item["params"]))


    def test_micro_task_valid_abstention_payload_has_program_nature_before_answered_notification(self):
        with self.board_.store.db.write() as db:
            router._write_settings(db, {**router.configuration(db), "routerBuddyIds": [A, B]})
        view = self.routed()
        pass  # Admission is owned by the service.
        nonce = NONCE + "abstain"
        claim = admit_board(self.board_, {'workerId': 'w-route', 'claimRequestId': 'router-abstain', 'nonce': nonce, 'runId': view['routing']['taskId']})["claim"]
        answer = self.answer(claim, buddy_id=None)
        reason = "The frozen packet does not justify choosing a buddy"
        answer["decision"]["note"] = reason
        self.board_.call('execution_result', {'attemptId': claim['attempt']['attemptId'], 'generation': claim['attempt']['generation'], 'nonce': nonce, 'status': 'ok', 'shutdownConfirmed': True, 'result': answer})
        current = self.board_.call("workflow_get", {"runId": view["runId"]})
        boundary = current["routing"]["routingBoundary"]
        self.assertEqual(boundary["kind"], "router-abstained")
        self.assertEqual(boundary["routerTrials"][0]["outcome"], "answered")
        self.assertEqual(boundary["reason"], {"code": "router-abstained", "params": {}})
        self.assertIsNone(current["activeRequest"]["summary"])
        self.assertEqual(current["activeRequest"]["reason"], boundary["reason"])
        self.assertEqual(current["activeRequest"]["routingBoundary"], boundary)
        self.assertEqual(current["routing"]["note"], reason)
        self.assertNotIn(reason, canonical_json(boundary["routerTrials"]))
        self.assertEqual(boundary["retryAt"], self.clock.value)
        with self.board_.store.db.read() as db:
            payload = json.loads(db.execute("SELECT payload_json FROM workflow_requests WHERE request_id=?",
                                           (current["activeRequest"]["requestId"],)).fetchone()[0])
            sequence = {row["kind"]: row["seq"] for row in db.execute(
                "SELECT seq,kind FROM events WHERE kind IN ('router.answered','decision.needs_host','workflow.routing_needs_host')")}
        self.assertEqual(payload, {"source": "routing"})
        self.assertLess(sequence["router.answered"], sequence["decision.needs_host"])
        self.assertLess(sequence["router.answered"], sequence["workflow.routing_needs_host"])
        with self.board_.store.db.read() as db:
            self.assertEqual(db.execute("SELECT decision_id FROM workflow_requests WHERE request_id=?",
                                        (current["activeRequest"]["requestId"],)).fetchone()[0],
                             view["routing"]["decisionId"])

    def test_explicit_reroute_after_known_retry_time_continues_same_micro_task_without_model(self):
        with self.board_.store.db.write() as db:
            router._write_settings(db, {**router.configuration(db), "routerBuddyIds": [A, B]})
        view = self.routed()
        pass  # Admission is owned by the service.
        for ordinal in (0, 1):
            current = self.board_.call("workflow_get", {"runId": view["runId"]})
            nonce = NONCE + str(ordinal)
            claim = admit_board(self.board_, {'workerId': 'w-route', 'claimRequestId': 'router-' + str(ordinal), 'nonce': nonce, 'runId': current['routing']['taskId']})["claim"]
            self.assertIsNotNone(claim)
            claim["nonce"] = nonce
            output = failover.FailoverTests.failure(self, claim)
            self.board_.call('execution_result', {'attemptId': claim['attempt']['attemptId'], 'generation': claim['attempt']['generation'], 'nonce': nonce, 'status': 'failed', 'shutdownConfirmed': True, 'result': output})
        view = self.board_.call("workflow_get", {"runId": view["runId"]})
        boundary = view["routing"]["routingBoundary"]
        self.assertEqual(boundary["kind"], "router-unavailable")
        self.assertEqual(boundary["retryAt"], "2026-01-01T00:10:00.000Z")
        reroute = boundary["commands"]["reroute"]
        self.assertEqual(reroute["notBefore"], boundary["retryAt"])
        self.assertNotIn("notBefore", reroute["params"])
        self.assertNotIn("note", reroute["params"])
        self.clock.advance(601)
        view = self.board_.call("workflow_get", {"runId": view["runId"]})
        reroute = view["routing"]["routingBoundary"]["commands"]["reroute"]
        self.assertEqual(reroute["notBefore"], self.clock.value)
        self.assertNotIn("note", reroute["params"])
        continued = self.board_.call("workflow_continue", self.template_params(view, reroute["params"]))
        self.assertEqual(continued["runId"], view["runId"])
        self.assertIsNone(continued["buddy"])
        self.board_.store.workflow.prepare_continuation_workspace({"runId": view["runId"]})
        refreshed = self.board_.call("workflow_get", {"runId": view["runId"]})
        self.assertEqual(refreshed["routing"]["status"], "queued")
        self.assertNotEqual(refreshed["routing"]["decisionId"], view["routing"]["decisionId"])
        with self.board_.store.db.read() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM workflow_turns").fetchone()[0], 0)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM attempts").fetchone()[0], 2)


if __name__ == "__main__":
    unittest.main()
