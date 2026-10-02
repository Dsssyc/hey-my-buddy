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

from support import BoardTestCase, FakeClock
from buddy import router, router_history
from buddy.db import canonical_json
from buddy.errors import BoardError

A = "dsh:fixture:alpha:max"
B = "dsh:fixture:bravo:max"
C = "dsh:fixture:charlie:max"
T0 = "2026-01-01T00:00:00.000Z"


class CurrentRouterTestCase(BoardTestCase):
    def setUp(self):
        super().setUp()
        self.clock = FakeClock(T0)
        self.board_ = self.board(clock=self.clock)

    # -- fixtures ------------------------------------------------------------
    def add_profile(self, profile_id, *, adapter="dsh", provider="fixture", available=1, enabled=1, model=None):
        with self.board_.store.db.write() as db:
            db.execute("INSERT INTO evaluation_profiles(profile_id,label,adapter,provider,model,effort,"
                       "available,enabled,capabilities_json,created_revision,updated_revision)"
                       " VALUES(?,?,?,?,?,?,?,?,'[]',0,0)",
                       (profile_id, profile_id, adapter, provider, model or profile_id, "max",
                        available, enabled))

    def set_settings(self, *, ids=None, mode=None, budget=None, interval=None):
        """Patch the shared version-3 settings the way a publication would."""
        with self.board_.store.db.write() as db:
            merged = {**router.configuration(db)}
            if ids is not None:
                merged["routerProfileIds"] = list(ids)
            if mode is not None:
                merged["defaultRoutingMode"] = mode
            if budget is not None:
                merged["routingBudget"] = budget
            if interval is not None:
                merged["routerRetryIntervalSeconds"] = interval
            router._write_settings(db, merged)
            revision = int(db.execute("SELECT configuration_revision FROM evaluation_state WHERE id=1").fetchone()[0])
            db.execute("UPDATE evaluation_state SET configuration_revision=? WHERE id=1", (revision + 1,))

    def record(self, profile_id, *, outcome="no_answer", plane="work", request="route-1", phase="preflight",
               code="timeout", index=0, task=None, attempt=None, now=T0, facts=None):
        with self.board_.store.db.write() as db:
            return router_history.record_outcome(
                db, profile_id=profile_id, plane=plane, request_id=request, task_id=task, attempt_id=attempt,
                outcome=outcome, code=code, phase=phase,
                facts={"routerIndex": index} if facts is None else facts, now=now)

    def event_count(self):
        with self.board_.store.db.read() as db:
            return int(db.execute("SELECT COUNT(*) FROM events WHERE kind IN"
                                  " ('router.answered','router.no_answer')").fetchone()[0])

    def add_task(self, task_id, *, at=T0, state="completed"):
        with self.board_.store.db.write() as db:
            db.execute(
                "INSERT INTO tasks(task_id,request_id,owner,spec_json,spec_canonical_json,input_fingerprint,"
                "fingerprint_version,adapter,cwd,timeout_seconds,state,revision,created_at,updated_at)"
                " VALUES(?,?,'host','{}','{}','digest',1,'decision','/work',60,?,1,?,?)",
                (task_id, f"task-request-{task_id}", state, at, at))

    def add_attempt(self, attempt_id, task_id, *, at=T0, state="finished", receipt=None):
        with self.board_.store.db.write() as db:
            db.execute(
                "INSERT INTO attempts(attempt_id,task_id,generation,nonce_verifier,claim_request_id,"
                "execution_state,adapter,result_json,shutdown_confirmed,started_at,created_at,updated_at)"
                " VALUES(?,?,0,'nonce',?,?,'decision',?,?,?,?,?)",
                (attempt_id, task_id, f"claim-{attempt_id}", state,
                 canonical_json(receipt) if receipt is not None else None,
                 1 if state == "finished" else 0, at, at, at))

    def add_routing_record(self, *, decision, request, kind, profile_id=None, code=None, at=T0,
                           attempt_state=None, receipt=None, output=None, called=None, candidates=3):
        """One pre-list routing record: its own frozen input, terminal event and receipt."""
        task_id = attempt_id = None
        if attempt_state is not None:
            task_id, attempt_id = f"task-{decision}", f"att-{decision}"
            self.add_task(task_id, at=at, state="running" if attempt_state != "finished" else "completed")
            self.add_attempt(attempt_id, task_id, at=at, state=attempt_state, receipt=receipt)
        requested = {"kind": "select", "routerProfileId": profile_id,
                     "routingBasis": {"candidateCount": candidates}}
        if called is not None:
            requested["routerCalled"] = called
        with self.board_.store.db.write() as db:
            db.execute(
                "INSERT INTO evaluation_decisions(decision_id,status,task,profile_id,table_revision,reason,"
                "evidence_ids_json,created_at) VALUES(?,?,?,?,?,?,?,?)",
                (decision, "failed", "task", profile_id, 0, "", "[]", at))
            db.execute(
                "INSERT INTO decision_requests(decision_id,request_id,kind,input_fingerprint,task_id,attempt_id,"
                "generation,expected_revision,requested_json,output_json,created_at,updated_at)"
                " VALUES(?,?,'select','digest',?,?,?,?,?,?,?,?)",
                (decision, request, task_id, attempt_id, 0, 0,
                 canonical_json(requested), canonical_json(output or {}), at, at))
            self.board_.store._append_event(db, kind, task_id=task_id, attempt_id=attempt_id,
                                            payload={"decisionId": decision,
                                                     **({"errorCode": code} if code else {})})

    def state(self, profile_id, *, interval=600, now=None):
        with self.board_.store.db.read() as db:
            return router_history.router_state(db, profile_id=profile_id, interval_seconds=interval,
                                               now=now or self.clock.value)


class ResolutionTests(CurrentRouterTestCase):
    def test_empty_list_reports_not_configured_with_revision_facts(self):
        with self.board_.store.db.read() as db:
            resolution = router.current_router(db, now=T0)
        self.assertIsNone(resolution.profile)
        self.assertIsNone(resolution.profile_id)
        self.assertIsNone(resolution.router_index)
        self.assertEqual(resolution.problem["code"], "router-not-configured")
        self.assertEqual(resolution.facts["routerProfileIds"], [])
        self.assertEqual(resolution.facts["configurationRevision"], 0)
        self.assertEqual(resolution.inspections, ())

    def test_selects_first_eligible_and_snapshots_complete_facts(self):
        self.add_profile(A, model="alpha")
        self.add_profile(B, model="bravo")
        self.set_settings(ids=[A, B], interval=45, budget="deep")
        with self.board_.store.db.read() as db:
            resolution = router.current_router(db, now=T0)
        self.assertEqual(resolution.profile_id, A)
        self.assertEqual(resolution.router_index, 0)
        self.assertIsNone(resolution.problem)
        self.assertEqual(resolution.facts, {
            "routerProfileIds": [A, B],
            "routerIdentities": [{"adapter": "dsh", "provider": "fixture", "model": "alpha", "effort": "max"},
                                 {"adapter": "dsh", "provider": "fixture", "model": "bravo", "effort": "max"}],
            "routingMode": "fast", "routingBudget": "deep", "routerRetryIntervalSeconds": 45,
            "configurationRevision": 1, "budget": {"timeoutSeconds": 60},
        })
        self.assertEqual(resolution.inspections[-1]["selected"], True)
        # The profile is a copy of the published row; mutating it touches nothing.
        resolution.profile["model"] = "mutated"
        with self.board_.store.db.read() as db:
            self.assertEqual(db.execute("SELECT model FROM evaluation_profiles WHERE profile_id=?",
                                        (A,)).fetchone()[0], "alpha")
        # Review mode expands the preset budget instead of the fixed fast budget.
        self.set_settings(mode="review", budget="brief")
        with self.board_.store.db.read() as db:
            review = router.current_router(db, now=T0)
        self.assertEqual(review.profile_id, A)
        self.assertEqual(review.facts["budget"],
                         {"preset": "brief", "timeoutSeconds": 60, "toolCalls": 8, "bytesRead": 131072})

    def test_ineligible_items_are_skipped_in_order_with_reasons(self):
        self.add_profile(A, enabled=0)
        self.add_profile(B, adapter="claude")
        self.add_profile(C)
        self.set_settings(ids=[A, B, C])
        with self.board_.store.db.read() as db:
            resolution = router.current_router(db, now=T0)
        self.assertEqual(resolution.profile_id, C)
        self.assertEqual(resolution.router_index, 2)
        self.assertEqual(resolution.inspections[0]["code"], "router-unavailable")
        self.assertIn("已禁用", resolution.inspections[0]["reason"])
        self.assertEqual(resolution.inspections[1]["code"], "router-no-tool-unsupported")

    def test_quota_exhausted_item_is_skipped_from_recorded_observations(self):
        self.add_profile(A, model="alpha")
        self.add_profile(B, provider="other")
        from buddy.native_observations import record_quota
        with self.board_.store.db.write() as db:
            record_quota(db, "dsh", {"provider": "fixture", "observedAt": T0, "balanceZero": True,
                                     "source": "test"}, now=T0)
        self.set_settings(ids=[A, B])
        with self.board_.store.db.read() as db:
            resolution = router.current_router(db, now=T0)
        self.assertEqual(resolution.profile_id, B)
        self.assertEqual(resolution.inspections[0]["code"], "router-quota-exhausted")

    def test_review_mode_requires_the_local_review_mechanism(self):
        self.add_profile(A, adapter="zcode")
        self.add_profile(B)
        self.set_settings(ids=[A, B], mode="review")
        with self.board_.store.db.read() as db:
            resolution = router.current_router(db, now=T0)
        self.assertEqual(resolution.profile_id, B)
        self.assertEqual(resolution.inspections[0]["code"], "router-review-unsupported")

    def test_skip_window_blocks_then_expires_and_reads_never_write(self):
        self.add_profile(A)
        self.set_settings(ids=[A], interval=600)
        self.record(A, now=T0)
        with self.board_.store.db.read() as db:
            first = router.current_router(db, now=T0)
        self.assertIsNone(first.profile)
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
        self.assertEqual(retried.profile_id, A)
        self.assertIsNone(retried.problem)
        self.assertEqual(self.event_count(), before)

    def test_valid_answered_clears_and_abstention_counts_as_answered(self):
        self.add_profile(A)
        self.set_settings(ids=[A])
        self.record(A, now=T0)
        self.record(A, outcome="answered", request="route-2", phase="runtime", code=None, now=T0)
        with self.board_.store.db.read() as db:
            resolution = router.current_router(db, now=T0)
        self.assertEqual(resolution.profile_id, A)
        self.assertEqual(self.state(A)["consecutiveNoAnswers"], 0)
        # A valid abstention is also a completed routing: it clears the streak too.
        self.record(A, request="route-3", now=T0)
        self.record(A, outcome="answered", request="route-4", phase="runtime", code="abstained", now=T0)
        with self.board_.store.db.read() as db:
            self.assertIsNone(router.current_router(db, now=T0).problem)
        self.assertEqual(self.state(A)["consecutiveNoAnswers"], 0)

    def test_other_router_answered_does_not_clear_this_router(self):
        self.add_profile(A)
        self.add_profile(B)
        self.set_settings(ids=[A, B])
        self.record(A, request="route-a", now=T0)
        self.record(B, request="route-b", now=T0)
        self.record(B, outcome="answered", request="route-b2", phase="runtime", code=None, now=T0)
        with self.board_.store.db.read() as db:
            resolution = router.current_router(db, now=T0)
        self.assertEqual(resolution.profile_id, B)
        self.assertEqual(self.state(A)["consecutiveNoAnswers"], 1)
        self.assertIsNotNone(self.state(A)["skipUntil"])
        self.assertEqual(self.state(B)["consecutiveNoAnswers"], 0)

    def test_planes_share_one_recording_entry_and_one_current_router(self):
        self.add_profile(A)
        self.add_profile(B)
        self.set_settings(ids=[A, B])
        self.record(A, plane="maintenance", request="maintain-1", now=T0)
        self.record(A, plane="portrait", request="portrait-1", now=T0)
        with self.board_.store.db.read() as db:
            resolution = router.current_router(db, now=T0)
        self.assertEqual(resolution.profile_id, B)
        self.assertEqual(self.state(A)["noAnswerCount"], 2)

    def test_consecutive_counts_are_not_cut_by_a_display_window(self):
        self.add_profile(A)
        self.add_profile(B)
        self.set_settings(ids=[A, B])
        for index in range(25):
            self.record(A, request=f"route-{index}", phase="runtime", now=T0)
        self.assertEqual(self.state(A)["consecutiveNoAnswers"], 25)
        self.assertEqual(self.state(A)["noAnswerCount"], 25)
        with self.board_.store.db.read() as db:
            resolution = router.current_router(db, now=T0)
        self.assertEqual(resolution.profile_id, B)
        self.assertEqual(resolution.inspections[0]["consecutiveNoAnswers"], 25)

    def test_all_unavailable_reports_one_summary_problem(self):
        self.add_profile(A, enabled=0)
        self.add_profile(B, adapter="claude")
        self.set_settings(ids=[A, B])
        with self.board_.store.db.read() as db:
            resolution = router.current_router(db, now=T0)
        self.assertIsNone(resolution.profile)
        self.assertEqual(resolution.problem["code"], "router-unavailable")
        self.assertEqual(len(resolution.inspections), 2)
        self.assertTrue(all(entry["code"] for entry in resolution.inspections))

    def test_after_index_is_strictly_forward_and_never_wraps(self):
        self.add_profile(A)
        self.add_profile(B)
        self.add_profile(C)
        self.set_settings(ids=[A, B, C])
        with self.board_.store.db.read() as db:
            self.assertEqual(router.current_router(db, after_index=0, now=T0).router_index, 1)
            self.assertEqual(router.current_router(db, after_index=1, now=T0).router_index, 2)
            exhausted = router.current_router(db, after_index=2, now=T0)
        self.assertIsNone(exhausted.profile)
        self.assertEqual(exhausted.problem["code"], "router-unavailable")
        self.assertIn("没有更多", exhausted.problem["reason"])
        with self.assertRaises(BoardError) as caught:
            with self.board_.store.db.read() as db:
                router.current_router(db, after_index=-2, now=T0)
        self.assertEqual(caught.exception.code, "INVALID_ARGUMENT")

    def test_version_two_settings_fail_the_unfrozen_read_without_migrating(self):
        self.add_profile(A)
        with self.board_.store.db.write() as db:
            db.execute("INSERT INTO meta(key,value) VALUES('router_configuration_version','2')"
                       " ON CONFLICT(key) DO UPDATE SET value=excluded.value")
        before = self.board_.call("console_snapshot", {})
        self.assertEqual(before["configurationError"]["code"], "router-settings-upgrade-required")
        self.assertIsNone(before["configuration"])
        with self.board_.store.db.read() as db:
            resolution = router.current_router(db, now=T0)
        self.assertEqual(resolution.problem["code"], "router-settings-upgrade-required")
        self.assertEqual(resolution.facts["configurationRevision"], before["configurationError"]["revision"])


class FrozenContinuationTests(CurrentRouterTestCase):
    def prepare(self):
        self.add_profile(A, model="alpha")
        self.add_profile(B, model="bravo")
        self.add_profile(C, model="charlie")
        self.set_settings(ids=[A, B], interval=600)
        with self.board_.store.db.read() as db:
            return router.current_router(db, now=T0).facts

    def test_frozen_continues_the_original_list_and_interval(self):
        facts = self.prepare()
        # Later shared changes: the list, the mode and the interval all move on.
        self.set_settings(ids=[A, C], mode="review", interval=30)
        with self.board_.store.db.read() as db:
            resolution = router.current_router(db, frozen=facts, after_index=0, now=T0)
        self.assertEqual(resolution.profile_id, B)
        self.assertEqual(resolution.router_index, 1)
        self.assertEqual(resolution.facts["routerRetryIntervalSeconds"], 600)
        self.assertEqual(resolution.facts["routingMode"], "fast")
        self.assertEqual(resolution.facts["configurationRevision"], facts["configurationRevision"])

    def test_frozen_identity_drift_is_refused_per_candidate(self):
        facts = self.prepare()
        with self.board_.store.db.write() as db:
            db.execute("UPDATE evaluation_profiles SET model='replaced' WHERE profile_id=?", (B,))
        with self.board_.store.db.read() as db:
            resolution = router.current_router(db, frozen=facts, after_index=0, now=T0)
        self.assertIsNone(resolution.profile)
        self.assertEqual(resolution.problem["code"], "router-profile-changed")
        self.assertEqual(resolution.problem["routerIndex"], 1)
        self.assertEqual(resolution.problem["profileId"], B)

    def test_frozen_snapshot_shape_is_validated(self):
        facts = self.prepare()
        for broken in (42, {}, {**facts, "routerProfileIds": "solo"},
                       {**facts, "routerIdentities": []},
                       {key: value for key, value in facts.items() if key != "routerRetryIntervalSeconds"},
                       {**facts, "routingMode": "auto"}, {**facts, "routerRetryIntervalSeconds": 0},
                       {**facts, "configurationRevision": -1}):
            with self.subTest(broken=broken):
                with self.assertRaises(BoardError) as caught:
                    with self.board_.store.db.read() as db:
                        router.current_router(db, frozen=broken, now=T0)
                self.assertEqual(caught.exception.code, "INVALID_ARGUMENT")


class OutcomeFactTests(CurrentRouterTestCase):
    def setUp(self):
        super().setUp()
        self.add_profile(A, model="alpha")
        self.add_profile(B, model="bravo")
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
                router_history.record_outcome(db, profile_id=A, plane="work", request_id="route-1", task_id=None,
                                              attempt_id=None, outcome="no_answer", code="timeout",
                                              phase="preflight", facts={"routerIndex": 0}, now=T0)
                raise AssertionError("rollback")
        self.assertEqual(self.event_count(), 0)
        with self.board_.store.db.read() as db:
            receipts = int(db.execute("SELECT COUNT(*) FROM meta WHERE key LIKE 'router-outcome:%'").fetchone()[0])
        self.assertEqual(receipts, 0)
        # After the rollback the same outcome records cleanly.
        self.record(A, request="route-1", now=T0)
        self.assertEqual(self.event_count(), 1)

    def test_only_real_outcomes_are_recordable(self):
        for kwargs in ({"outcome": "cancelled"}, {"outcome": "changed"}, {"plane": "execution"},
                       {"phase": ""}, {"code": "x" * 200}):
            with self.subTest(kwargs=kwargs), self.assertRaises(BoardError) as caught:
                self.record(A, request="route-x", **kwargs)
            self.assertEqual(caught.exception.code, "INVALID_ARGUMENT")
        with self.assertRaises(BoardError):
            self.record(A, request="route-x", facts={})
        self.assertEqual(self.event_count(), 0)

    def test_task_and_attempt_bind_onto_the_event(self):
        self.add_task("task-1", state="queued")
        result = self.record(A, request="route-1", task="task-1", attempt="att-1", now=T0)
        with self.board_.store.db.read() as db:
            row = db.execute("SELECT task_id,attempt_id,payload_json FROM events WHERE seq=?",
                             (result["seq"],)).fetchone()
        self.assertEqual(row["task_id"], "task-1")
        self.assertEqual(row["attempt_id"], "att-1")
        self.assertEqual(json.loads(row["payload_json"]),
                         {"plane": "work", "requestId": "route-1", "routerIndex": 0, "profileId": A,
                          "phase": "preflight", "taskId": "task-1", "attemptId": "att-1",
                          "outcome": "no_answer", "code": "timeout"})


class HistoryProjectionTests(CurrentRouterTestCase):
    def setUp(self):
        super().setUp()
        self.set_settings(ids=[A, B])

    def test_four_old_timeouts_stay_attributed_and_counted(self):
        for index in range(4):
            self.add_routing_record(decision=f"dec-{index}", request=f"route-{index}", kind="decision.failed",
                                    profile_id=A, code="timeout",
                                    receipt={"status": "error", "result": {"status": "error"},
                                             "shutdownConfirmed": True},
                                    output={"code": "timeout"}, attempt_state="finished", at=T0)
        state = self.state(A)
        self.assertEqual(state["noAnswerCount"], 4)
        self.assertEqual(state["consecutiveNoAnswers"], 4)
        self.assertEqual(state["inSkipWindow"], True)
        other = self.state(B)
        self.assertEqual(other["noAnswerCount"], 0)
        self.assertEqual(other["consecutiveNoAnswers"], 0)
        self.assertEqual(other["inSkipWindow"], False)
        self.assertIsNone(other["lastNoAnswerAt"])

    def test_old_accepted_answer_stays_answered_without_new_evidence_rules(self):
        self.add_routing_record(decision="dec-ok", request="route-ok", kind="decision.completed", profile_id=A,
                                receipt={"status": "ok",
                                         "result": {"status": "ok", "modelStarted": True,
                                                    "decision": {"profileId": "worker"}},
                                         "shutdownConfirmed": True},
                                output={"status": "ok"}, attempt_state="finished", at=T0)
        state = self.state(A)
        self.assertEqual(state["answeredCount"], 1)
        self.assertEqual(state["consecutiveNoAnswers"], 0)
        self.assertIsNone(state["skipUntil"])

    def test_old_abstention_and_changed_records_do_not_fabricate_failures(self):
        self.add_routing_record(decision="dec-abs", request="route-abs", kind="decision.needs_host", profile_id=A,
                                output={"status": "ok", "decision": {"profileId": None, "reason": "insufficient",
                                                                     "evidence": []}}, at=T0)
        self.add_routing_record(decision="dec-chg", request="route-chg", kind="decision.failed", profile_id=A,
                                code="router-input-changed", output={"code": "router-input-changed"}, at=T0)
        self.add_routing_record(decision="dec-cancel", request="route-cancel", kind="decision.cancelled",
                                profile_id=A, at=T0)
        state = self.state(A)
        self.assertEqual(state["answeredCount"], 1)
        self.assertEqual(state["noAnswerCount"], 0)
        self.assertEqual(state["consecutiveNoAnswers"], 0)

    def test_old_and_new_records_dedupe_by_request_and_attempt(self):
        self.add_routing_record(decision="dec-1", request="route-1", kind="decision.failed", profile_id=A,
                                code="timeout", output={"code": "timeout"}, attempt_state="finished", at=T0)
        self.record(A, request="route-1", task="task-dec-1", attempt="att-dec-1", phase="runtime", now=T0)
        self.assertEqual(self.state(A)["noAnswerCount"], 1)
        # A fact carrying only the attempt id also suppresses the old record.
        self.record(A, request="route-2-fact", attempt="att-dec-1", phase="runtime", now=T0)
        self.assertEqual(self.state(A)["noAnswerCount"], 2)

    def test_unattributed_and_program_records_stay_unknown(self):
        self.add_routing_record(decision="dec-none", request="route-none", kind="decision.needs_host",
                                output={"code": "router-not-configured"}, at=T0)
        self.add_routing_record(decision="dec-single", request="route-single", kind="decision.completed",
                                called=False, output={"status": "ok"}, at=T0)
        # A record whose frozen basis proves zero candidates never reached a Router.
        self.add_routing_record(decision="dec-zero", request="route-zero", kind="decision.needs_host",
                                candidates=0, output={"code": "no-legal-candidates"}, at=T0)
        with self.board_.store.db.read() as db:
            unattributed = router_history.unattributed_records(db)
        self.assertEqual([entry["decisionId"] for entry in unattributed], ["dec-none"])
        self.assertEqual(unattributed[0]["outcome"], "no_answer")
        # None of the records is guessed onto today's first list item.
        self.assertEqual(self.state(A)["noAnswerCount"], 0)
        self.assertEqual(self.state(A)["answeredCount"], 0)

    def test_bad_events_cannot_break_other_projections(self):
        self.record(A, request="route-good", now=T0)
        with self.board_.store.db.write() as db:
            db.execute("INSERT INTO events(task_id,attempt_id,revision,kind,payload_json,created_at)"
                       " VALUES(NULL,NULL,NULL,'router.no_answer','{broken json',?)", (T0,))
            db.execute("INSERT INTO events(task_id,attempt_id,revision,kind,payload_json,created_at)"
                       " VALUES(NULL,NULL,NULL,'router.answered','{\"unrelated\": true}',?)", (T0,))
            db.execute("INSERT INTO evaluation_decisions(decision_id,status,task,profile_id,table_revision,"
                       "reason,evidence_ids_json,created_at) VALUES('dec-bad','failed','t',NULL,0,'','[]',?)", (T0,))
            db.execute("INSERT INTO decision_requests(decision_id,request_id,kind,input_fingerprint,"
                       "expected_revision,requested_json,output_json,created_at,updated_at)"
                       " VALUES('dec-bad','route-bad','select','digest',0,'{broken','{}',?,?)", (T0, T0))
            self.board_.store._append_event(db, "decision.failed", payload={"decisionId": "dec-bad"})
        with self.board_.store.db.read() as db:
            timeline = router_history.profile_timeline(db, profile_id=A)
            unattributed = router_history.unattributed_records(db)
            state = router_history.router_state(db, profile_id=B, interval_seconds=600, now=T0)
        self.assertEqual([entry["outcome"] for entry in timeline], ["no_answer"])
        self.assertEqual(state["noAnswerCount"], 0)
        # The malformed record is carried in the separate unknown listing, never
        # counted for any buddy, and it breaks neither projection.
        self.assertEqual([entry["decisionId"] for entry in unattributed], ["dec-bad"])
        self.assertEqual(self.state(A)["noAnswerCount"], 1)

    def test_immutable_terminal_facts_win_over_late_output(self):
        receipt = {'status': 'ok', 'shutdownConfirmed': True, 'result': {'status': 'ok'}}
        self.assertEqual(router_history._classify_record('decision.completed', {}, receipt), ('answered', None))
        abstention = {'status': 'ok', 'decision': {'profileId': None, 'reason': 'cannot choose', 'evidence': []}}
        for code in ('router-budget-exhausted', 'router-tool-evidence-unverified'):
            with self.subTest(code=code):
                self.assertEqual(router_history._classify_record('decision.needs_host', abstention, None,
                                 {'errorCode': code}), ('no_answer', code))
        old = {'status': 'failed', 'result': {'status': 'error', 'code': 'timeout'}}
        self.assertEqual(router_history._classify_record('decision.failed',
                         {'code': 'router-input-changed'}, old, {'errorCode': 'timeout'}), ('no_answer', 'timeout'))

    def test_per_profile_reads_use_the_partial_outcome_index(self):
        """Per-buddy reads must serve the partial index, not scan unrelated events.

        ``events_router_outcome_idx`` indexes the guarded profile expression over the
        literal fact kinds; the projection must render exactly that predicate so a
        bounded per-item read stays an index-only range.
        """
        with self.board_.store.db.write() as connection:
            for index in range(300):
                self.board_.store._append_event(connection, "task.progress", payload={"index": index})
            query = ("SELECT seq FROM events WHERE kind IN ('router.answered','router.no_answer')"
                     f" AND {router_history._PROFILE_EXPRESSION}=? ORDER BY seq")
            plan = " ".join(row["detail"] for row in connection.execute("EXPLAIN QUERY PLAN " + query,
                                                                        (A,)).fetchall())
        self.assertIn("events_router_outcome_idx", plan)
        self.assertNotIn("SCAN event", plan)
        with self.board_.store.db.read() as connection:
            query = ("SELECT seq FROM events WHERE kind='router.claimed'"
                     f" AND {router_history._PROFILE_EXPRESSION}=? ORDER BY seq")
            claimed = " ".join(row['detail'] for row in connection.execute('EXPLAIN QUERY PLAN ' + query, (A,)))
        self.assertIn('events_router_claimed_idx', claimed)
        self.assertNotIn('SCAN e', claimed)

    def test_active_attempts_project_retry_in_progress(self):
        self.add_profile(A, model="alpha")
        self.add_routing_record(decision="dec-live", request="route-live", kind="decision.failed", profile_id=A,
                                code="timeout", output={"code": "timeout"}, attempt_state="executing", at=T0)
        self.record(A, request="route-recorded", phase="preflight", now=T0)
        state = self.state(A)
        self.assertTrue(state["retryInProgress"])
        self.assertEqual(state["activeAttempts"][0]["attemptId"], "att-dec-live")
        self.assertEqual(state["activeAttempts"][0]["executionState"], "executing")
        # A finished attempt is not an active execution; the live one still is.
        self.add_routing_record(decision="dec-done", request="route-done", kind="decision.failed", profile_id=A,
                                code="timeout", output={"code": "timeout"}, attempt_state="finished", at=T0)
        self.assertTrue(self.state(A)["retryInProgress"])
        # Shutdown-unconfirmed attempts still own capacity.
        self.add_routing_record(decision="dec-uncertain", request="route-uncertain", kind="decision.failed",
                                profile_id=B, code="timeout", output={"code": "timeout"},
                                attempt_state="uncertain", at=T0)
        self.record(B, request="route-recorded-b", phase="preflight", now=T0)
        self.assertTrue(self.state(B)["retryInProgress"])
        # After a valid answered the same active execution is capacity, not a retry.
        self.record(A, outcome="answered", request="route-recorded-2", phase="runtime", code=None, now=T0)
        self.assertFalse(self.state(A)["retryInProgress"])

    def test_claimed_events_bind_active_executions(self):
        self.add_profile(A, model="alpha")
        self.add_task("task-claim", state="queued")
        with self.board_.store.db.write() as db:
            db.execute(
                "INSERT INTO attempts(attempt_id,task_id,generation,nonce_verifier,claim_request_id,"
                "execution_state,adapter,created_at,updated_at)"
                " VALUES('att-claim','task-claim',0,'nonce','claim-1','executing','decision',?,?)", (T0, T0))
            self.board_.store._append_event(db, "router.claimed", task_id="task-claim", attempt_id="att-claim",
                                            payload={"profileId": A, "routerIndex": 0})
        self.record(A, request="route-recorded", phase="preflight", now=T0)
        state = self.state(A)
        self.assertTrue(state["retryInProgress"])
        self.assertEqual(state["activeAttempts"][0]["source"], "claimed")


if __name__ == "__main__":
    unittest.main()
