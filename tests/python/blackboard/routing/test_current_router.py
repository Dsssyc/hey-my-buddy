"""The ordered Router resolution entry and its outcome facts, without any model calls.

Everything here runs on private boards against cached records only. The resolution
entry walks the user's ordered list, explains every skipped item, projects skip
windows from recorded facts without writing, and continues frozen request snapshots
without re-reading shared settings. Old pre-list routing records keep their own
frozen Router attribution and their immutable receipts' effective answers; unknown
nature or attribution stays unknown; old and new records deduplicate.
"""
import json
import unittest
from unittest.mock import patch

from support import BoardTestCase, FakeClock
from hey_my_buddy.blackboard.routing import router, router_history, router_sequence
from hey_my_buddy.blackboard.store.db import canonical_json
from hey_my_buddy.errors import BoardError

A = "dsh:fixture:alpha:max"
B = "dsh:fixture:bravo:max"
C = "dsh:fixture:charlie:max"
T0 = "2026-01-01T00:00:00.000Z"


class CurrentRouterTestCase(BoardTestCase):
    def setUp(self):
        super().setUp()
        self.clock = FakeClock(T0)
        self.board_ = self.board(clock=self.clock)
        # Ordered-resolution scenarios use only shared capability declarations.
        self.enterContext(patch.dict('hey_my_buddy.protocol.capabilities.PERMISSION_LEVELS', {'dsh': {'none': 'enforced', 'read': 'enforced', 'write': 'enforced', 'full': 'enforced'}}))

    # -- fixtures ------------------------------------------------------------
    def add_buddy(self, buddy_id, *, harness="dsh", provider="fixture", available=1, enabled=1, model=None):
        with self.board_.store.db.write() as db:
            db.execute("INSERT INTO buddies(buddy_id,label,harness,provider,model,effort,"
                       "available,enabled,capabilities_json)"
                       " VALUES(?,?,?,?,?,?,?,?,?)",
                       (buddy_id, buddy_id, harness, provider, model or buddy_id, "max",
                        available, enabled, canonical_json([])))

    def set_settings(self, *, ids=None, mode=None, budget=None, interval=None):
        """Patch the shared version-3 settings the way a publication would."""
        with self.board_.store.db.write() as db:
            merged = {**router.configuration(db)}
            if ids is not None:
                merged["routerBuddyIds"] = list(ids)
            if mode is not None:
                merged["defaultRoutingMode"] = mode
            if budget is not None:
                merged["routingBudget"] = budget
            if interval is not None:
                merged["routerRetryIntervalSeconds"] = interval
            router._write_settings(db, merged)

    def record(self, buddy_id, *, outcome="no_answer", request="route-1", phase="preflight",
               code="timeout", index=0, task=None, attempt=None, now=T0, facts=None):
        with self.board_.store.db.write() as db:
            return router_history.record_outcome(
                db, buddy_id=buddy_id, request_id=request, task_id=task, attempt_id=attempt,
                outcome=outcome, code=code, phase=phase,
                facts={"routerIndex": index} if facts is None else facts, now=now)

    def event_count(self):
        with self.board_.store.db.read() as db:
            return int(db.execute("SELECT COUNT(*) FROM events WHERE kind IN"
                                  " ('router.answered','router.no_answer')").fetchone()[0])

    def add_task(self, task_id, *, at=T0, state="completed"):
        with self.board_.store.db.write() as db:
            db.execute(
                "INSERT INTO tasks(task_id,request_id,owner,spec_json,input_fingerprint,"
                "fingerprint_version,adapter,cwd,router_budget_seconds,state,revision,created_at,updated_at)"
                " VALUES(?,?,'host','{}','digest',1,'decision','/work',60,?,1,?,?)",
                (task_id, f"task-request-{task_id}", state, at, at))

    def add_attempt(self, attempt_id, task_id, *, at=T0, state="finished", receipt=None):
        with self.board_.store.db.write() as db:
            db.execute("INSERT INTO attempts(attempt_id,task_id,generation,nonce_verifier,execution_state,adapter,result_json,shutdown_confirmed,started_at,created_at,updated_at) VALUES(?,?,1,'nonce',?,'decision',?,?,?,?,?)", (attempt_id, task_id, state, canonical_json(receipt) if receipt is not None else None, 1 if state == 'finished' else 0, at, at, at))

    def add_routing_record(self, *, decision, request, kind, buddy_id=None, code=None, at=T0,
                           attempt_state=None, receipt=None, output=None, called=None, candidates=3):
        """One pre-list routing record: its own frozen input, terminal event and receipt."""
        task_id = attempt_id = None
        if attempt_state is not None:
            task_id, attempt_id = f"task-{decision}", f"att-{decision}"
            self.add_task(task_id, at=at, state="running" if attempt_state != "finished" else "completed")
            self.add_attempt(attempt_id, task_id, at=at, state=attempt_state, receipt=receipt)
        requested = {"kind": "select", "routerBuddyId": buddy_id,
                     "routingBasis": {"candidateCount": candidates}}
        if called is not None:
            requested["routerCalled"] = called
        with self.board_.store.db.write() as db:
            db.execute(
                "INSERT INTO evaluation_decisions(decision_id,status,buddy_id,"
                "created_at) VALUES(?,?,?,?)",
                (decision, "failed", buddy_id, at))
            db.execute(
                "INSERT INTO decision_requests(decision_id,request_id,input_fingerprint,task_id,attempt_id,"
                "generation,requested_json,output_json,updated_at)"
                " VALUES(?,?,'digest',?,?,?,?,?,?)",
                (decision, request, task_id, attempt_id, 0,
                 canonical_json(requested), canonical_json(output or {}), at))
            router_sequence.freeze_request(db, decision, snapshot={"facts": requested,
                "baseInput": {"task": "task"}, "inspections": []}, now=at)
            if attempt_id is not None and buddy_id is not None:
                router_sequence.reserve_dispatch(
                    db, decision_id=decision, task_id=task_id, router_index=0,
                    buddy={"buddyId": buddy_id, "harness": "dsh", "provider": "fixture",
                           "model": buddy_id, "effort": "max"}, now=at)
                router_sequence.record_claim(db, task_id=task_id, attempt_id=attempt_id,
                                             generation=1, now=at)
            self.board_.store._append_event(db, kind, task_id=task_id, attempt_id=attempt_id,
                                            payload={"decisionId": decision,
                                                     **({"errorCode": code} if code else {})})

    def state(self, buddy_id, *, interval=600, now=None):
        with self.board_.store.db.read() as db:
            return router_history.router_state(db, buddy_id=buddy_id, interval_seconds=interval,
                                               now=now or self.clock.value)


class ResolutionTests(CurrentRouterTestCase):
    def test_empty_list_reports_not_configured_with_empty_facts(self):
        with self.board_.store.db.read() as db:
            resolution = router.current_router(db, now=T0)
        self.assertIsNone(resolution.buddy)
        self.assertIsNone(resolution.buddy_id)
        self.assertIsNone(resolution.router_index)
        self.assertEqual(resolution.problem["code"], "router-not-configured")
        self.assertEqual(resolution.facts["routerBuddyIds"], [])
        self.assertNotIn("configurationRevision", resolution.facts)
        self.assertEqual(resolution.inspections, ())

    def test_selects_first_eligible_and_snapshots_complete_facts(self):
        self.add_buddy(A, model="alpha")
        self.add_buddy(B, model="bravo")
        self.set_settings(ids=[A, B], interval=45, budget="deep")
        with self.board_.store.db.read() as db:
            resolution = router.current_router(db, now=T0)
        self.assertEqual(resolution.buddy_id, A)
        self.assertEqual(resolution.router_index, 0)
        self.assertIsNone(resolution.problem)
        self.assertEqual(resolution.facts, {
            "routerBuddyIds": [A, B],
            "routerIdentities": [{"harness": "dsh", "provider": "fixture", "model": "alpha", "effort": "max"},
                                 {"harness": "dsh", "provider": "fixture", "model": "bravo", "effort": "max"}],
            "routingMode": "fast", "routingBudget": "deep", "routerRetryIntervalSeconds": 45,
            "budget": {"timeoutSeconds": 60},
        })
        self.assertEqual(resolution.inspections[-1]["selected"], True)
        # The buddy is a copy of the published row; mutating it touches nothing.
        resolution.buddy["model"] = "mutated"
        with self.board_.store.db.read() as db:
            self.assertEqual(db.execute("SELECT model FROM buddies WHERE buddy_id=?",
                                        (A,)).fetchone()[0], "alpha")
        # Review mode expands the preset budget instead of the fixed fast budget.
        self.set_settings(mode="review", budget="brief")
        with self.board_.store.db.read() as db:
            review = router.current_router(db, now=T0)
        self.assertEqual(review.buddy_id, A)
        self.assertEqual(review.facts["budget"],
                         {"preset": "brief", "timeoutSeconds": 60, "toolCalls": 8, "bytesRead": 131072})

    def test_ineligible_items_are_skipped_in_order_with_reasons(self):
        self.add_buddy(A, enabled=0)
        self.add_buddy(B, harness="claude")
        self.add_buddy(C)
        self.set_settings(ids=[A, B, C])
        with self.board_.store.db.read() as db:
            resolution = router.current_router(db, now=T0)
        self.assertEqual(resolution.buddy_id, C)
        self.assertEqual(resolution.router_index, 2)
        self.assertEqual(resolution.inspections[0]["code"], "router-disabled")
        self.assertNotIn("reason", resolution.inspections[0])
        self.assertEqual(resolution.inspections[1]["code"], "router-no-tool-unsupported")

    def test_cooling_provider_is_skipped_until_its_deadline(self):
        self.add_buddy(A, model="alpha")
        self.add_buddy(B, provider="other")
        from hey_my_buddy.blackboard.routing.quota_routing import record_rejection
        with self.board_.store.db.write() as db:
            record_rejection(db, "dsh", "fixture", {"code": "quota-exceeded",
                "nativeCode": "fixture-denied", "source": "synthetic/test",
                "observedAt": T0, "resetsAt": None, "rejectionPhase": "before-model"}, now=T0)
        self.set_settings(ids=[A, B])
        with self.board_.store.db.read() as db:
            resolution = router.current_router(db, now=T0)
        self.assertEqual(resolution.buddy_id, B)
        self.assertEqual(resolution.inspections[0]["code"], "router-quota-exhausted")
        self.assertEqual(resolution.inspections[0]["retryAt"], "2026-01-01T00:30:00.000Z")

    def test_review_mode_accepts_the_shared_zcode_read_declaration(self):
        self.add_buddy(A, harness="zcode")
        self.add_buddy(B)
        self.set_settings(ids=[A, B], mode="review")
        with self.board_.store.db.read() as db:
            resolution = router.current_router(db, now=T0)
        self.assertEqual(resolution.buddy_id, A)
        self.assertIsNone(resolution.inspections[0]["code"])

    def test_skip_window_blocks_then_expires_and_reads_never_write(self):
        self.add_buddy(A)
        self.set_settings(ids=[A], interval=600)
        self.record(A, now=T0)
        with self.board_.store.db.read() as db:
            first = router.current_router(db, now=T0)
        self.assertIsNone(first.buddy)
        self.assertEqual(first.inspections[0]["code"], "router-skip-window")
        self.assertEqual(first.inspections[0]["retryAt"], "2026-01-01T00:10:00.000Z")
        self.assertEqual(first.inspections[0]["consecutiveNoAnswers"], 1)
        before = self.event_count()
        for _ in range(3):
            with self.board_.store.db.read() as db:
                again = router.current_router(db, now=T0)
        self.assertEqual(again.inspections[0]["retryAt"], first.inspections[0]["retryAt"])
        self.assertEqual(self.event_count(), before)
        self.clock.advance(601)
        with self.board_.store.db.read() as db:
            retried = router.current_router(db, now=self.clock.value)
        self.assertEqual(retried.buddy_id, A)
        self.assertIsNone(retried.problem)
        self.assertEqual(self.event_count(), before)

    def test_valid_answered_clears_and_abstention_counts_as_answered(self):
        self.add_buddy(A)
        self.set_settings(ids=[A])
        self.record(A, now=T0)
        self.record(A, outcome="answered", request="route-2", phase="runtime", code=None, now=T0)
        with self.board_.store.db.read() as db:
            resolution = router.current_router(db, now=T0)
        self.assertEqual(resolution.buddy_id, A)
        self.assertEqual(self.state(A)["consecutiveNoAnswers"], 0)
        # A valid abstention is also a completed routing: it clears the streak too.
        self.record(A, request="route-3", now=T0)
        self.record(A, outcome="answered", request="route-4", phase="runtime", code="abstained", now=T0)
        with self.board_.store.db.read() as db:
            self.assertIsNone(router.current_router(db, now=T0).problem)
        self.assertEqual(self.state(A)["consecutiveNoAnswers"], 0)

    def test_other_router_answered_does_not_clear_this_router(self):
        self.add_buddy(A)
        self.add_buddy(B)
        self.set_settings(ids=[A, B])
        self.record(A, request="route-a", now=T0)
        self.record(B, request="route-b", now=T0)
        self.record(B, outcome="answered", request="route-b2", phase="runtime", code=None, now=T0)
        with self.board_.store.db.read() as db:
            resolution = router.current_router(db, now=T0)
        self.assertEqual(resolution.buddy_id, B)
        self.assertEqual(self.state(A)["consecutiveNoAnswers"], 1)
        self.assertIsNotNone(self.state(A)["skipUntil"])
        self.assertEqual(self.state(B)["consecutiveNoAnswers"], 0)

    def test_consecutive_counts_are_not_cut_by_a_display_window(self):
        self.add_buddy(A)
        self.add_buddy(B)
        self.set_settings(ids=[A, B])
        for index in range(25):
            self.record(A, request=f"route-{index}", phase="runtime", now=T0)
        self.assertEqual(self.state(A)["consecutiveNoAnswers"], 25)
        self.assertEqual(self.state(A)["noAnswerCount"], 25)
        with self.board_.store.db.read() as db:
            resolution = router.current_router(db, now=T0)
        self.assertEqual(resolution.buddy_id, B)
        self.assertEqual(resolution.inspections[0]["consecutiveNoAnswers"], 25)

    def test_all_unavailable_reports_one_summary_problem(self):
        self.add_buddy(A, enabled=0)
        self.add_buddy(B, harness="claude")
        self.set_settings(ids=[A, B])
        with self.board_.store.db.read() as db:
            resolution = router.current_router(db, now=T0)
        self.assertIsNone(resolution.buddy)
        self.assertEqual(resolution.problem["code"], "router-all-unavailable")
        self.assertEqual(len(resolution.inspections), 2)
        self.assertTrue(all(entry["code"] for entry in resolution.inspections))

    def test_after_index_is_strictly_forward_and_never_wraps(self):
        self.add_buddy(A)
        self.add_buddy(B)
        self.add_buddy(C)
        self.set_settings(ids=[A, B, C])
        with self.board_.store.db.read() as db:
            self.assertEqual(router.current_router(db, after_index=0, now=T0).router_index, 1)
            self.assertEqual(router.current_router(db, after_index=1, now=T0).router_index, 2)
            exhausted = router.current_router(db, after_index=2, now=T0)
        self.assertIsNone(exhausted.buddy)
        self.assertEqual(exhausted.problem["code"], "router-list-exhausted")
        self.assertNotIn("reason", exhausted.problem)

    def test_unfrozen_read_uses_current_settings_without_version_marker(self):
        self.add_buddy(A)
        before = self.board_.call("console_snapshot", {})
        self.assertNotIn("configurationError", before)
        with self.board_.store.db.read() as db:
            resolution = router.current_router(db, now=T0)
        self.assertEqual(resolution.problem["code"], "router-not-configured")
        self.assertEqual(resolution.facts["routerBuddyIds"], before["configuration"]["routerBuddyIds"])


class FrozenContinuationTests(CurrentRouterTestCase):
    def prepare(self):
        self.add_buddy(A, model="alpha")
        self.add_buddy(B, model="bravo")
        self.add_buddy(C, model="charlie")
        self.set_settings(ids=[A, B], interval=600)
        with self.board_.store.db.read() as db:
            return router.current_router(db, now=T0).facts

    def test_frozen_continues_the_original_list_and_interval(self):
        facts = self.prepare()
        # Later shared changes: the list, the mode and the interval all move on.
        self.set_settings(ids=[A, C], mode="review", interval=30)
        with self.board_.store.db.read() as db:
            resolution = router.current_router(db, frozen=facts, after_index=0, now=T0)
        self.assertEqual(resolution.buddy_id, B)
        self.assertEqual(resolution.router_index, 1)
        self.assertEqual(resolution.facts["routerRetryIntervalSeconds"], 600)
        self.assertEqual(resolution.facts["routingMode"], "fast")
        self.assertNotIn("configurationRevision", resolution.facts)

    def test_frozen_identity_drift_keeps_the_original_actor(self):
        facts = self.prepare()
        with self.board_.store.db.write() as db:
            db.execute("UPDATE buddies SET model='replaced' WHERE buddy_id=?", (B,))
        with self.board_.store.db.read() as db:
            resolution = router.current_router(db, frozen=facts, after_index=0, now=T0)
        self.assertIsNone(resolution.problem)
        self.assertEqual(resolution.buddy_id, B)
        self.assertEqual(resolution.buddy["model"], "bravo")

    def test_frozen_review_deadline_override_survives_next_router_resolution(self):
        facts = self.prepare()
        self.set_settings(mode="review")
        with self.board_.store.db.read() as db:
            facts = router.current_router(db, now=T0).facts
        facts["budget"]["timeoutSeconds"] = 77
        with self.board_.store.db.read() as db:
            resolution = router.current_router(db, frozen=facts, after_index=0, now=T0)
        self.assertEqual(resolution.buddy_id, B)
        self.assertEqual(resolution.facts["budget"], facts["budget"])
        resolution.facts["budget"]["timeoutSeconds"] = 88
        self.assertEqual(facts["budget"]["timeoutSeconds"], 77)


class OutcomeFactTests(CurrentRouterTestCase):
    def setUp(self):
        super().setUp()
        self.add_buddy(A, model="alpha")
        self.add_buddy(B, model="bravo")
        self.set_settings(ids=[A, B])

    def test_replay_is_idempotent_and_different_facts_conflict(self):
        first = self.record(A, request="route-1", now=T0)
        self.assertTrue(first["recorded"])
        replay = self.record(A, request="route-1", now=T0)
        self.assertFalse(replay["recorded"])
        self.assertEqual(replay["seq"], first["seq"])
        self.assertEqual(self.event_count(), 1)
        with self.assertRaises(BoardError) as caught:
            self.record(A, request="route-1", code="provider-error", now=T0)
        self.assertEqual(caught.exception.code, "CONFLICT")
        self.assertEqual(self.event_count(), 1)

    def test_receipt_and_event_commit_in_one_transaction(self):
        with self.assertRaises(AssertionError):
            with self.board_.store.db.write() as db:
                router_history.record_outcome(db, buddy_id=A, request_id="route-1", task_id=None,
                                              attempt_id=None, outcome="no_answer", code="timeout",
                                              phase="preflight", facts={"routerIndex": 0}, now=T0)
                raise AssertionError("rollback")
        self.assertEqual(self.event_count(), 0)
        with self.board_.store.db.read() as db:
            receipts = int(db.execute("SELECT COUNT(*) FROM router_outcomes").fetchone()[0])
        self.assertEqual(receipts, 0)
        # After the rollback the same outcome records cleanly.
        self.record(A, request="route-1", now=T0)
        self.assertEqual(self.event_count(), 1)

    def test_task_and_attempt_bind_onto_the_event(self):
        self.add_task("task-1", state="queued")
        with self.board_.store.db.write() as db:
            db.execute("INSERT INTO attempts(attempt_id,task_id,generation,nonce_verifier,execution_state,adapter,created_at,updated_at) VALUES('att-1','task-1',1,'nonce','executing','decision',?,?)", (T0, T0))
        result = self.record(A, request="route-1", task="task-1", attempt="att-1", now=T0)
        with self.board_.store.db.read() as db:
            row = db.execute("SELECT task_id,attempt_id,payload_json FROM events WHERE seq=?",
                             (result["seq"],)).fetchone()
        self.assertEqual(row["task_id"], "task-1")
        self.assertEqual(row["attempt_id"], "att-1")
        self.assertEqual(json.loads(row["payload_json"]),
                         {"plane": "routing", "requestId": "route-1", "routerIndex": 0, "buddyId": A,
                          "phase": "preflight", "taskId": "task-1", "attemptId": "att-1",
                          "outcome": "no_answer", "code": "timeout"})


class HistoryProjectionTests(CurrentRouterTestCase):
    def setUp(self):
        super().setUp()
        self.set_settings(ids=[A, B])

    def test_only_recorded_outcomes_affect_retry_state(self):
        self.add_routing_record(decision="dec-old", request="route-old", kind="decision.failed",
                                buddy_id=A, code="timeout", output={"code": "timeout"}, at=T0)
        self.assertEqual(self.state(A)["noAnswerCount"], 0)
        self.record(A, request="route-new", now=T0)
        self.assertEqual(self.state(A)["noAnswerCount"], 1)
        self.assertEqual(self.state(B)["noAnswerCount"], 0)

    def test_unattributed_decisions_do_not_create_router_outcomes(self):
        self.add_routing_record(decision="dec-none", request="route-none", kind="decision.needs_host",
                                output={"code": "router-not-configured"}, at=T0)
        self.assertEqual(self.state(A)["answeredCount"], 0)
        self.assertEqual(self.state(A)["noAnswerCount"], 0)

    def test_per_buddy_reads_use_outcome_index(self):
        with self.board_.store.db.read() as connection:
            query = "SELECT event_seq FROM router_outcomes WHERE buddy_id=? ORDER BY event_seq"
            plan = " ".join(row["detail"] for row in connection.execute("EXPLAIN QUERY PLAN " + query,
                                                                        (A,)).fetchall())
        self.assertIn("router_outcomes_buddy_idx", plan)

    def test_active_attempts_project_retry_in_progress(self):
        self.add_buddy(A, model="alpha")
        self.add_routing_record(decision="dec-live", request="route-live", kind="decision.failed", buddy_id=A,
                                code="timeout", output={"code": "timeout"}, attempt_state="executing", at=T0)
        self.record(A, request="route-recorded", phase="preflight", now=T0)
        state = self.state(A)
        self.assertTrue(state["retryInProgress"])
        self.assertEqual(state["activeAttempts"][0]["attemptId"], "att-dec-live")
        self.assertEqual(state["activeAttempts"][0]["executionState"], "executing")
        # A finished attempt is not an active execution; the live one still is.
        self.add_routing_record(decision="dec-done", request="route-done", kind="decision.failed", buddy_id=A,
                                code="timeout", output={"code": "timeout"}, attempt_state="finished", at=T0)
        self.assertTrue(self.state(A)["retryInProgress"])
        # Shutdown-unconfirmed attempts still own capacity.
        self.add_routing_record(decision="dec-uncertain", request="route-uncertain", kind="decision.failed",
                                buddy_id=B, code="timeout", output={"code": "timeout"},
                                attempt_state="uncertain", at=T0)
        self.record(B, request="route-recorded-b", phase="preflight", now=T0)
        self.assertTrue(self.state(B)["retryInProgress"])
        # After a valid answered the same active execution is capacity, not a retry.
        self.record(A, outcome="answered", request="route-recorded-2", phase="runtime", code=None, now=T0)
        self.assertFalse(self.state(A)["retryInProgress"])

if __name__ == "__main__":
    unittest.main()
