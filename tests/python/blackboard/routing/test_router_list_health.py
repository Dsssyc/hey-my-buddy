"""Per-Router health statistics over R2 facts, receipts and pre-list records.

Every statistic here is projected by the read-only R2 interfaces: recorded
``router.answered``/``router.no_answer`` facts, old terminal select records
through their own frozen attribution, and immutable attempt receipts. No native
harness, model call, settings write or probe session is started, and reading
health never writes anything.
"""
from __future__ import annotations

import json
import unittest

from support import BoardTestCase, FakeClock
from hey_my_buddy.blackboard.catalog.catalog import permission_capabilities

from hey_my_buddy.blackboard.routing import router, router_history, router_sequence
from hey_my_buddy.blackboard.store.db import canonical_json

A = "dsh:fixture:alpha:max"
B = "dsh:fixture:bravo:max"
C = "dsh:fixture:charlie:max"
T0 = "2026-01-01T00:00:00.000Z"


class RouterListHealthTestCase(BoardTestCase):
    def setUp(self):
        super().setUp()
        self.clock = FakeClock(T0)
        self.board_ = self.board(clock=self.clock)
        self.db = self.board_.store.db
        self.sequence = 0

    # -- fixtures ------------------------------------------------------------
    def add_buddy(self, buddy_id, *, harness="dsh", provider="fixture", available=1, enabled=1):
        with self.db.write() as db:
            db.execute("INSERT INTO buddies(buddy_id,label,harness,provider,model,effort,"
                       "available,enabled,capabilities_json)"
                       " VALUES(?,?,?,?,?,?,?,?,?)",
                       (buddy_id, buddy_id, harness, provider, buddy_id, "max", available, enabled,
                        canonical_json(permission_capabilities(harness))))

    def set_settings(self, *, ids, interval=600, mode="fast"):
        with self.db.write() as db:
            merged = {**router.configuration(db), "routerBuddyIds": list(ids),
                      "defaultRoutingMode": mode}
            if interval is not None:
                merged["routerRetryIntervalSeconds"] = interval
            router._write_settings(db, merged)

    def record(self, buddy_id, *, outcome="no_answer", request=None, code="timeout", index=0,
               phase="runtime", now=None, task=None, attempt=None):
        """One real R2 fact with its idempotent receipt, through the recording entry.

        The fact rows stay unbound (no task/attempt ids) unless the caller
        created the referenced rows: events carry real foreign keys.
        """
        self.sequence += 1
        with self.db.write() as db:
            return router_history.record_outcome(
                db, buddy_id=buddy_id, plane="work", request_id=request or f"route-{self.sequence}",
                task_id=task, attempt_id=attempt,
                outcome=outcome, code=code, phase=phase,
                facts={"routerIndex": index}, now=now or self.clock.value)

    def state(self):
        with self.db.read() as db:
            return self.board_.store.decisions.health_summary()

    def entry(self, summary, buddy_id):
        return next(item for item in summary["routers"] if item["buddyId"] == buddy_id)

    def counts(self):
        with self.db.read() as db:
            events = int(db.execute("SELECT COUNT(*) FROM events").fetchone()[0])
            receipts = int(db.execute(
                "SELECT COUNT(*) FROM router_outcomes").fetchone()[0])
            return events, receipts

    def add_legacy_record(self, decision, *, buddy_id=None, kind="decision.failed", code="timeout",
                          at=T0, receipt=None, output=None, candidates=3, dispatched=False):
        """One pre-list routing record: its own frozen input, terminal event and receipt."""
        task_id = attempt_id = None
        if receipt is not None:
            self.sequence += 1
            task_id, attempt_id = f"legacy-task-{self.sequence}", f"legacy-attempt-{self.sequence}"
            with self.db.write() as db:
                db.execute(
                    "INSERT INTO tasks(task_id,request_id,owner,spec_json,spec_canonical_json,input_fingerprint,"
                    "fingerprint_version,adapter,cwd,router_budget_seconds,state,revision,created_at,updated_at)"
                    " VALUES(?,?,'host','{}','{}','digest',1,'decision','<private>',60,'completed',1,?,?)",
                    (task_id, task_id, at, at))
                db.execute("INSERT INTO attempts(attempt_id,task_id,generation,nonce_verifier,execution_state,adapter,result_json,shutdown_confirmed,started_at,created_at,updated_at) VALUES(?,?,0,'nonce','finished','decision',?,1,?,?,?)", (attempt_id, task_id, canonical_json(receipt), at, at, at))
        requested = {"kind": "select", "routerBuddyId": buddy_id,
                     "routingBasis": {"candidateCount": candidates}}
        with self.db.write() as db:
            db.execute(
                "INSERT INTO evaluation_decisions(decision_id,status,task,buddy_id,"
                "created_at) VALUES(?,?,?,?,?)",
                (decision, "failed", "task", buddy_id, at))
            db.execute(
                "INSERT INTO decision_requests(decision_id,request_id,kind,input_fingerprint,task_id,attempt_id,"
                "generation,requested_json,output_json,created_at,updated_at)"
                " VALUES(?,?,'select','digest',?,?,0,?,?,?,?)",
                (decision, f"legacy-request-{decision}", task_id, attempt_id,
                 canonical_json(requested), canonical_json(output or {}), at, at))
            if dispatched:
                router_sequence.freeze_request(db, decision, snapshot={
                    "facts": {"routerBuddyIds": [buddy_id]}, "baseInput": {}, "inspections": []}, now=at)
                buddy = db.execute("SELECT buddy_id,harness,provider,model,effort FROM buddies WHERE buddy_id=?",
                                   (buddy_id,)).fetchone()
                router_sequence.reserve_dispatch(db, decision_id=decision, task_id=task_id,
                                                 router_index=0, buddy={"buddyId": buddy["buddy_id"],
                                                 **{key: buddy[key] for key in ("harness", "provider", "model", "effort")}},
                                                 document={}, now=at)
            self.board_.store._append_event(db, kind, task_id=task_id, attempt_id=attempt_id,
                                            payload={"decisionId": decision,
                                                     **({"errorCode": code} if code else {})})


class OverviewTests(RouterListHealthTestCase):
    def test_empty_list_reports_not_configured_without_the_old_threshold(self):
        summary = self.state()
        self.assertFalse(summary["available"])
        self.assertEqual(summary["reasonCode"], "router-not-configured")
        self.assertIsNone(summary["currentRouterBuddyId"])
        self.assertEqual(summary["routers"], [])

    def test_resolution_answers_availability_not_the_three_failure_rule(self):
        self.add_buddy(A)
        self.add_buddy(B)
        self.set_settings(ids=[A, B])
        for _ in range(5):
            self.record(A, code="timeout", index=0)
        summary = self.state()
        # A is inside its skip window, so B holds the role even with A's streak.
        self.assertTrue(summary["available"])
        self.assertIsNone(summary["reasonCode"])
        self.assertEqual(summary["currentRouterBuddyId"], B)

    def test_per_item_last_error_names_the_most_recent_no_answer(self):
        self.add_buddy(A)
        self.set_settings(ids=[A])
        self.record(A, code="router-out-of-bounds", now=T0)
        self.record(A, code="timeout", now=self.clock.advance(1))
        entry = self.entry(self.state(), A)
        self.assertEqual(entry["lastError"], {"code": "timeout", "at": entry["lastError"]["at"],
                                              "phase": "runtime"})
        self.assertEqual(entry["lastNoAnswerAt"], entry["lastError"]["at"])

    def test_total_window_counts_business_requests_and_lists_attempts(self):
        self.add_buddy(A)
        self.set_settings(ids=[A])
        # Two settled business requests, each with a bound attempt.
        self.add_legacy_record("dec-r1", buddy_id=A, kind="decision.failed", code="timeout",
                               receipt={"status": "failed", "result": {"status": "error", "code": "timeout"},
                                        "shutdownConfirmed": True}, dispatched=True)
        self.add_legacy_record("dec-r2", buddy_id=A, kind="decision.failed", code="timeout",
                               receipt={"status": "failed", "result": {"status": "error", "code": "timeout"},
                                        "shutdownConfirmed": True}, dispatched=True)
        summary = self.state()
        self.assertEqual(summary["sampleCount"], 2)
        self.assertEqual(summary["attemptCount"], 2)
        self.assertEqual(summary["failureCount"], 2)

    def test_timeout_and_readonly_budget_codes_count_as_failure_and_budget(self):
        self.add_buddy(A)
        self.set_settings(ids=[A])
        self.record(A, code="timeout")
        self.record(A, code="router-budget-exhausted")
        self.record(A, code="readonly-budget-exhausted")
        self.record(A, code="router-out-of-bounds")
        entry = self.entry(self.state(), A)
        self.assertEqual(entry["windowFailureCount"], 4)
        self.assertEqual(entry["windowBudgetExhaustedCount"], 3)
        self.assertEqual(entry["windowBoundsRejectedCount"], 1)
        self.assertEqual(entry["consecutiveNoAnswers"], 4)


class PerRouterTests(RouterListHealthTestCase):
    def test_four_timeouts_are_four_failures_with_consecutive_four(self):
        self.add_buddy(A)
        self.add_buddy(B)
        self.set_settings(ids=[A, B])
        for index in range(4):
            self.record(A, code="timeout", index=0, now=self.clock.advance(60))
        summary = self.state()
        entry = self.entry(summary, A)
        self.assertEqual(entry["windowFailureCount"], 4)
        self.assertEqual(entry["windowBudgetExhaustedCount"], 4)
        self.assertEqual(entry["consecutiveNoAnswers"], 4)
        self.assertEqual(entry["noAnswerCount"], 4)
        self.assertEqual(entry["answeredCount"], 0)
        self.assertIsNone(entry["lastAnsweredAt"])
        other = self.entry(summary, B)
        self.assertEqual(other["noAnswerCount"], 0)
        self.assertEqual(other["consecutiveNoAnswers"], 0)

    def test_a_buddy_success_recovers_only_its_own_streak(self):
        self.add_buddy(A)
        self.add_buddy(B)
        self.set_settings(ids=[A, B])
        for _ in range(3):
            self.record(A, code="timeout", index=0)
            self.record(B, code="timeout", index=1)
        self.record(A, outcome="answered", code=None, index=0)
        summary = self.state()
        self.assertEqual(self.entry(summary, A)["consecutiveNoAnswers"], 0)
        self.assertEqual(self.entry(summary, A)["answeredCount"], 1)
        self.assertEqual(self.entry(summary, A)["skipUntil"], None)
        self.assertEqual(self.entry(summary, B)["consecutiveNoAnswers"], 3)
        self.assertIsNotNone(self.entry(summary, B)["skipUntil"])

    def test_valid_abstention_is_an_answer_and_recovers(self):
        self.add_buddy(A)
        self.set_settings(ids=[A])
        self.record(A, code="timeout", index=0)
        # A valid abstention is recorded as answered by the publication layer.
        self.record(A, outcome="answered", code=None, index=0)
        summary = self.state()
        entry = self.entry(summary, A)
        self.assertEqual(entry["consecutiveNoAnswers"], 0)
        self.assertEqual(entry["answeredCount"], 1)
        self.assertIsNone(entry["skipUntil"])
        # The earlier no-answer stays history; recovery only resets the streak.
        self.assertEqual(entry["lastError"]["code"], "timeout")

    def test_consecutive_count_ignores_the_window_but_window_counts_do_not(self):
        self.add_buddy(A)
        self.set_settings(ids=[A])
        for index in range(25):
            self.record(A, code="timeout", index=0, now=self.clock.advance(1))
        summary = self.state()
        entry = self.entry(summary, A)
        self.assertEqual(entry["consecutiveNoAnswers"], 25)
        self.assertEqual(entry["noAnswerCount"], 25)
        self.assertEqual(entry["windowSize"], 20)
        self.assertEqual(entry["windowEntries"], 20)
        self.assertEqual(entry["windowFailureCount"], 20)
        self.assertEqual(entry["windowBudgetExhaustedCount"], 20)

    def test_window_entries_count_only_recorded_outcomes(self):
        self.add_buddy(A)
        self.set_settings(ids=[A])
        # A decision record without a Router outcome receipt does not enter the timeline.
        self.add_legacy_record("dec-unknown", buddy_id=A, kind="decision.completed",
                               receipt={"status": "failed", "result": {"status": "error"},
                                        "shutdownConfirmed": True})
        self.record(A, code="timeout", index=0)
        entry = self.entry(self.state(), A)
        self.assertEqual(entry["windowEntries"], 1)
        self.assertEqual(entry["windowFailureCount"], 1)
        self.assertEqual(entry["windowAnsweredCount"], 0)
        self.assertEqual(entry["consecutiveNoAnswers"], 1)


class CurrentRoleTests(RouterListHealthTestCase):
    def test_current_router_moves_to_the_next_item_during_the_skip_window(self):
        self.add_buddy(A)
        self.add_buddy(B)
        self.set_settings(ids=[A, B], interval=600)
        self.record(A, code="timeout", index=0, now=T0)
        self.clock.advance(1)
        summary = self.state()
        self.assertEqual(summary["currentRouterBuddyId"], B)
        self.assertTrue(summary["available"])
        first = self.entry(summary, A)
        self.assertFalse(first["eligible"])
        self.assertEqual(first["code"], "router-skip-window")
        self.assertTrue(first["inSkipWindow"])
        self.assertEqual(first["retryAt"], "2026-01-01T00:10:00.000Z")
        self.assertEqual(self.entry(summary, B)["eligible"], True)

    def test_skip_window_expires_and_the_buddy_serves_again(self):
        self.add_buddy(A)
        self.add_buddy(B)
        self.set_settings(ids=[A, B], interval=600)
        self.record(A, code="timeout", index=0, now=T0)
        self.clock.advance(601)
        summary = self.state()
        self.assertEqual(summary["currentRouterBuddyId"], A)
        first = self.entry(summary, A)
        self.assertTrue(first["eligible"])
        self.assertFalse(first["inSkipWindow"])
        # The expired window facts stay visible as history, never rewritten.
        self.assertEqual(first["consecutiveNoAnswers"], 1)
        self.assertIsNotNone(first["skipUntil"])

    def test_retry_in_progress_names_the_active_execution(self):
        self.add_buddy(A)
        self.set_settings(ids=[A], interval=600)
        with self.db.write() as db:
            db.execute(
                "INSERT INTO tasks(task_id,request_id,owner,spec_json,spec_canonical_json,input_fingerprint,"
                "fingerprint_version,adapter,cwd,router_budget_seconds,state,revision,created_at,updated_at)"
                " VALUES('task-live','req-live','host','{}','{}','digest',1,'decision','<private>',60,"
                "'running',1,?,?)", (T0, T0))
            db.execute("INSERT INTO attempts(attempt_id,task_id,generation,nonce_verifier,execution_state,adapter,result_json,shutdown_confirmed,created_at,updated_at) VALUES('attempt-live','task-live',0,'nonce','executing','decision',NULL,0,?,?)", (T0, T0))
            db.execute(
                "INSERT INTO events(task_id,attempt_id,revision,kind,payload_json,created_at)"
                " VALUES('task-live','attempt-live',NULL,'router.claimed',?,?)",
                (canonical_json({"buddyId": A}), T0))
        self.record(A, code="timeout", index=0, now=T0, task="task-live", attempt="attempt-live")
        summary = self.state()
        entry = self.entry(summary, A)
        self.assertTrue(entry["retryInProgress"])
        self.assertEqual(entry["code"], "router-skip-window")

    def test_ineligible_items_carry_their_resolution_code(self):
        self.add_buddy(A, enabled=0)
        self.add_buddy(B, harness="claude")
        self.add_buddy(C)
        self.set_settings(ids=[A, B, C], mode="fast")
        summary = self.state()
        self.assertEqual(summary["currentRouterBuddyId"], C)
        self.assertEqual(self.entry(summary, A)["code"], "router-disabled")
        self.assertEqual(self.entry(summary, B)["code"], "router-no-tool-unsupported")
        self.assertTrue(self.entry(summary, C)["eligible"])

    def test_list_items_carry_their_complete_identity_and_index(self):
        self.add_buddy(A)
        self.add_buddy(B)
        self.set_settings(ids=[A, B])
        summary = self.state()
        self.assertEqual([item["index"] for item in summary["routers"]], [0, 1])
        self.assertEqual(self.entry(summary, A)["identity"],
                         {"harness": "dsh", "provider": "fixture", "model": A, "effort": "max"})


class AttributionTests(RouterListHealthTestCase):
    def test_decision_records_do_not_create_outcome_facts(self):
        self.add_buddy(A)
        self.add_buddy(B)
        self.set_settings(ids=[A, B])
        # An old decision record is not a Router outcome receipt.
        self.add_legacy_record("dec-old", buddy_id=B, kind="decision.failed", code="timeout",
                               receipt={"status": "failed", "result": {"status": "error", "code": "timeout"},
                                        "shutdownConfirmed": True})
        summary = self.state()
        self.assertEqual(self.entry(summary, B)["noAnswerCount"], 0)
        self.assertEqual(self.entry(summary, B)["consecutiveNoAnswers"], 0)
        self.assertEqual(self.entry(summary, A)["noAnswerCount"], 0)
        self.assertNotIn("unattributed", summary)

    def test_unattributable_decisions_do_not_affect_buddy_history(self):
        self.add_buddy(A)
        self.set_settings(ids=[A])
        # No frozen routerBuddyId although a model ran: attribution is unprovable.
        self.add_legacy_record("dec-mystery", buddy_id=None, kind="decision.failed", code="timeout",
                               receipt={"status": "failed", "result": {"status": "error", "code": "timeout"},
                                        "shutdownConfirmed": True})
        self.record(A, code="timeout", index=0)
        summary = self.state()
        self.assertNotIn("unattributedCount", summary)
        # Neither the buddy's counts nor the total claim it.
        self.assertEqual(self.entry(summary, A)["noAnswerCount"], 1)
        self.assertEqual(self.entry(summary, A)["windowEntries"], 1)

    def test_accepted_abstention_requires_an_outcome_receipt(self):
        self.add_buddy(A)
        self.add_buddy(B)
        self.set_settings(ids=[A, B])
        self.record(B, code="timeout", index=1)
        self.add_legacy_record("dec-abstain", buddy_id=A, kind="decision.needs_host", code=None,
                               output={"status": "ok", "decision": {"buddyId": None,
                                                                    "reason": "insufficient evidence",
                                                                    "evidence": []}})
        summary = self.state()
        self.assertEqual(self.entry(summary, A)["answeredCount"], 0)
        self.assertEqual(self.entry(summary, A)["consecutiveNoAnswers"], 0)
        self.assertEqual(self.entry(summary, B)["consecutiveNoAnswers"], 1)


class NoWriteTests(RouterListHealthTestCase):
    def test_reading_health_writes_nothing_and_never_extends_windows(self):
        self.add_buddy(A)
        self.add_buddy(B)
        self.set_settings(ids=[A, B], interval=600)
        self.record(A, code="timeout", index=0, now=T0)
        first = self.state()
        before = self.counts()
        for _ in range(3):
            again = self.state()
        self.assertEqual(self.counts(), before)
        # Repeated reads project the same window instead of rolling it forward.
        self.assertEqual(again, first)
        first_entry = self.entry(first, A)
        self.assertEqual(first_entry["retryAt"], "2026-01-01T00:10:00.000Z")
        self.assertEqual(self.entry(again, A)["retryAt"], first_entry["retryAt"])

    def test_health_read_leaves_settings_and_receipts_untouched(self):
        self.add_buddy(A)
        self.set_settings(ids=[A])
        self.record(A, outcome="answered", code=None, index=0)
        with self.db.read() as db:
            settings = router.configuration(db)
            receipts = db.execute("SELECT * FROM router_outcomes ORDER BY receipt_id").fetchall()
        self.state()
        with self.db.read() as db:
            self.assertEqual(router.configuration(db), settings)
            self.assertEqual(db.execute(
                "SELECT * FROM router_outcomes ORDER BY receipt_id").fetchall(), receipts)
            self.assertEqual(receipts[0]["outcome"], "answered")


if __name__ == "__main__":
    unittest.main()
