"""Read frozen Host routing evidence on private boards, without a native call.

The sealed publication fixtures exercise the production completion transaction.
Only the local catalog and workspace seam are substituted for governed commands.
"""
import copy
import json
import unittest
from unittest.mock import patch

from buddy import router, router_history, router_sequence
from buddy.db import canonical_json
from buddy.errors import BoardError
from buddy.router_boundary_data import routing_boundary
from support import FakeClock
from test_router_dispatch import RouterDispatchTestCase, A, B, C, T0, NONCE
import test_router_failover_integration as failover
from test_workflow import WorkflowTestCase

TRIAL_FIELDS = {"profileId", "identity", "index", "phase", "outcome", "code", "reason",
                "taskId", "attemptId", "shutdownConfirmed", "consecutiveNoAnswers", "retryAt", "usage"}
BOUNDARY_FIELDS = {"kind", "code", "reason", "routerTrials", "candidates", "facts", "retryAt", "commands", "userAction"}


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
                self.assertTrue(TRIAL_FIELDS <= set(trial), trial)
        return result

    def finish_all(self):
        request, first = self.start()
        self.report(first, self.failure(first), status="failed")
        second = self.next_claim(request)
        self.report(second, self.failure(second, "provider-error"), status="failed")
        return request, first, second

    def assert_packet(self, boundary, ids=(A, B), *, require_schema=True):
        self.assertEqual([item["profileId"] for item in boundary["candidates"]], list(ids))
        facts = boundary["facts"]
        self.assertEqual([item["profileId"] for item in facts["profiles"]], list(ids))
        self.assertIn("accounts", facts)
        self.assertIn("policyFacts", facts)
        if require_schema:
            self.assertIn("outputSchema", facts)
        self.assertIn("routingMode", facts)
        for candidate in boundary["candidates"]:
            self.assertTrue({"adapter", "provider", "model", "effort"} <= set(candidate))

    def test_all_no_answers_are_unavailable_with_each_immutable_actor_receipt(self):
        request, first, second = self.finish_all()
        result = self.boundary(request["decisionId"])
        self.assertEqual(result["kind"], "router-unavailable")
        self.assertIn("取消后重新提交没有用", result["reason"])
        self.assert_packet(result)
        self.assertEqual(result["retryAt"], "2026-01-01T00:10:00.000Z")
        for index, claim in enumerate((first, second)):
            trial = result["routerTrials"][index]
            self.assertEqual(trial["profileId"], (A, B)[index])
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
        answer = self.answer(second, profile_id=None)
        answer["decision"]["reason"] = "No justified choice within the frozen candidates"
        self.report(second, answer)
        boundary = self.boundary(request["decisionId"])
        self.assertEqual(boundary["kind"], "router-abstained")
        self.assertEqual(boundary["reason"], answer["decision"]["reason"])
        self.assertEqual(boundary["retryAt"], self.clock.value)
        self.assertEqual(boundary["routerTrials"][1]["outcome"], "answered")
        self.assertEqual(boundary["routerTrials"][1]["consecutiveNoAnswers"], 0)
        self.assertIsNone(boundary["routerTrials"][2]["attemptId"])
        self.assertNotEqual(boundary["routerTrials"][2]["outcome"], "no_answer")

    def test_invalid_null_is_no_answer_and_never_model_reason_abstention(self):
        request, first = self.start()
        answer = self.answer(first, profile_id=None)
        answer["decision"]["reason"] = "Router unavailable: model wording is not program classification"
        answer["usage"]["toolCalls"] = None
        self.report(first, answer)
        second = self.next_claim(request)
        self.report(second, self.failure(second), status="failed")
        boundary = self.boundary(request["decisionId"])
        self.assertEqual(boundary["kind"], "router-unavailable")
        self.assertEqual(boundary["routerTrials"][0]["code"], "router-tool-evidence-unverified")
        self.assertIsNone(boundary["routerTrials"][0]["reason"])

    def test_terminal_publication_error_does_not_make_unadopted_null_an_abstention(self):
        request, first = self.start()
        with patch.object(self.board_.store.decisions, "_publish_select", side_effect=BoardError(
                "FIXTURE_REJECTED", "Private program publication was refused")):
            self.report(first, self.answer(first, profile_id=None))
        boundary = self.boundary(request["decisionId"])
        self.assertEqual(boundary["kind"], "router-unavailable")
        self.assertNotEqual(boundary["routerTrials"][0]["outcome"], "answered")
        self.assertEqual(self.router_events("router.answered"), [])

    def test_completed_recommendation_with_changed_execution_context_uses_program_route_reason(self):
        request, first = self.start(governed=True)
        self.report(first, self.answer(first))
        with self.board_.store.db.write() as db:
            db.execute("UPDATE workflow_routes SET state='needs-host',reason='Cached execution validation changed' WHERE decision_id=?",
                       (request["decisionId"],))
        boundary = self.boundary(request["decisionId"])
        self.assertEqual(boundary["kind"], "routing-changed")
        self.assertIn("Cached execution validation changed", boundary["reason"])
        self.assertNotIn("The frozen legal choice", boundary["reason"])
        self.assertEqual(boundary["routerTrials"][0]["outcome"], "answered")

    def test_recorded_quota_reset_is_a_known_retry_time_without_a_new_fuse(self):
        from buddy.quota_routing import record
        self.add_profile(C, model="charlie")
        self.add_profile("dsh:fixture:delta:max", model="delta")
        with self.board_.store.db.write() as db:
            for model, minute in (("alpha", 20), ("bravo", 25)):
                record(db, "dsh", {"provider": "fixture", "scope": {"limitId": model},
                    "observedAt": T0, "ordinaryUsageAllowed": False, "reachedType": "quota-exceeded",
                    "resetsAt": f"2026-01-01T00:{minute}:00.000Z"}, now=T0)
        with patch("buddy.quota_routing.utc_now", return_value=T0):
            request = self.request()
        boundary = self.boundary(request["decisionId"])
        self.assertEqual(boundary["retryAt"], "2026-01-01T00:20:00Z")
        self.assertEqual([item["retryAt"] for item in boundary["routerTrials"]],
                         ["2026-01-01T00:20:00Z", "2026-01-01T00:25:00Z"])
        self.assertEqual(self.tasks(), [])

    def test_freeze_retains_additional_host_packet_without_rewriting_other_snapshot_fields(self):
        self.set_settings(ids=[])
        request = self.request()
        snapshot = self.snapshot_of(request["decisionId"])
        snapshot.pop("frozenAt")
        packet = {"profiles": [], "privateHostFact": "retained, never sent"}
        with self.board_.store.db.write() as db:
            frozen = router_sequence.freeze_request(db, "dec-private-extra", snapshot={**snapshot, "hostPacket": packet}, now=T0)
        self.assertEqual(frozen["hostPacket"], packet)
        self.assertEqual(frozen["baseInput"], snapshot["baseInput"])
        self.assertEqual(self.snapshot_of(request["decisionId"])["baseInput"], snapshot["baseInput"])

    def test_settings_and_table_changes_are_changed_without_model_reason_guessing(self):
        request, first = self.start()
        self.set_settings(ids=[B, A], mode="review", interval=30)
        self.report(first, self.failure(first), status="failed")
        boundary = self.boundary(request["decisionId"])
        self.assertEqual(boundary["kind"], "routing-changed")
        self.assertEqual(boundary["retryAt"], self.clock.value)
        self.assertEqual(boundary["facts"]["routerProfileIds"], [A, B])
        self.assertEqual(boundary["facts"]["routingMode"], "fast")
        self.assertEqual(boundary["facts"]["routerRetryIntervalSeconds"], 600)
        self.assertIn("取消后重新提交没有用", boundary["reason"])
        self.assertNotEqual(boundary["routerTrials"][0]["outcome"], "no_answer")

    def test_current_view_has_actor_b_even_queued_and_original_request_stays_actor_free(self):
        request, first = self.start()
        original = self.snapshot_of(request["decisionId"])
        self.report(first, self.failure(first), status="failed")
        current = self.decision_row(request["decisionId"])
        self.assertEqual(current["status"], "queued")
        self.assertEqual(current["routerProfileId"], B)
        self.assertEqual(current["routerProfile"]["model"], "bravo")
        self.assertEqual(current["routerIndex"], 1)
        self.assertEqual(current["decisionModel"]["requested"]["model"], "bravo")
        self.assertEqual(current["routerProfileIds"], [A, B])
        with self.board_.store.db.read() as db:
            row = self.board_.store.decisions._row(db, request["decisionId"])
            requested = json.loads(row["requested_json"])
        self.assertNotIn("routerProfileId", requested)
        self.assertNotIn("routerProfile", requested)
        self.assertEqual(self.snapshot_of(request["decisionId"]), original)

    def test_no_dispatch_does_not_invent_first_configured_actor(self):
        self.set_settings(ids=[A, B])
        with self.board_.store.db.write() as db:
            db.execute("UPDATE evaluation_profiles SET enabled=0")
        # Separate legal candidates preserve a real routing admission.
        self.add_profile(C, model="charlie")
        self.add_profile("dsh:fixture:delta:max", model="delta")
        request = self.request()
        current = self.decision_row(request["decisionId"])
        self.assertIsNone(current["routerProfileId"])
        self.assertIsNone(current["routerProfile"])
        self.assertIsNone(current["routerIndex"])
        self.assertIsNone(current["decisionModel"]["requested"])
        boundary = self.boundary(request["decisionId"])
        self.assertEqual(boundary["kind"], "router-unavailable")
        self.assertEqual(self.tasks(), [])
        self.assertTrue(all(trial["attemptId"] is None for trial in boundary["routerTrials"]))
        self.assertIsNone(boundary["retryAt"])
        self.assertTrue(all(trial["retryAt"] is None and "no recovery time" in trial["reason"]
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

    def test_old_settings_keep_frozen_packet_without_guessing_mode_or_converting_meta(self):
        with self.board_.store.db.write() as db:
            db.execute("UPDATE meta SET value='2' WHERE key='router_configuration_version'")
            prior = dict(db.execute("SELECT key,value FROM meta WHERE key GLOB 'router_*'").fetchall())
        request = self.request()
        boundary = self.boundary(request["decisionId"])
        self.assert_packet(boundary, require_schema=False)
        self.assertEqual(boundary["code"], "router-settings-upgrade-required")
        self.assertIsNone(boundary["facts"]["routingMode"])
        with self.board_.store.db.read() as db:
            self.assertEqual(dict(db.execute("SELECT key,value FROM meta WHERE key GLOB 'router_*'").fetchall()), prior)
        self.assertEqual(self.tasks(), [])

    def test_model_byte_and_entry_caps_preserve_untruncated_host_packet(self):
        delta = "dsh:fixture:delta:max"
        self.add_profile(C, model="charlie")
        self.add_profile(delta, model="delta")
        for ordinal, ceiling in enumerate(("MAX_DECISION_INPUT_BYTES", "MAX_DECISION_PROFILES")):
            with self.subTest(ceiling=ceiling), patch("buddy.decision." + ceiling, 1):
                request = self.request(request_id="oversize-" + str(ordinal))
            boundary = self.boundary(request["decisionId"])
            self.assert_packet(boundary, ids=(A, B, C, delta))
            self.assertEqual(boundary["kind"], "routing-changed")
            self.assertEqual(len(boundary["candidates"]), 4)
            self.assertEqual(self.tasks(), [])
            self.assertEqual(self.attempts(), [])

    def test_later_profiles_accounts_and_preferences_cannot_replace_frozen_facts(self):
        self.set_settings(ids=[])
        with self.board_.store.db.write() as db:
            db.execute("INSERT INTO family_annotations VALUES('dsh','fixture','alpha','private note',0,?)", (T0,))
            db.execute("INSERT INTO evaluation_preferences VALUES(?,'prefer','private preference',0)", (B,))
        request = self.request()
        frozen = self.boundary(request["decisionId"])
        with self.board_.store.db.write() as db:
            db.execute("UPDATE evaluation_profiles SET model='later-model'")
            db.execute("UPDATE evaluation_preferences SET reason='later preference'")
            db.execute("DELETE FROM family_annotations")
            db.execute("INSERT INTO meta(key,value) VALUES('account-selection:dsh',?)", (canonical_json({"source": "worker", "revision": 42}),))
        current = self.boundary(request["decisionId"])
        self.assertEqual(current["facts"], frozen["facts"])
        self.assertEqual(current["candidates"], frozen["candidates"])
        self.assertNotIn("later-model", canonical_json(current["facts"]))

    def test_skip_is_not_an_execution_or_new_failure_and_retry_time_does_not_roll(self):
        self.add_profile(C, model="charlie")
        with self.board_.store.db.write() as db:
            router_history.record_outcome(db, profile_id=A, plane="work", request_id="earlier", task_id=None,
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
        for ordinal, profile_id in enumerate((A, A, A, B, B)):
            if ordinal == 3:
                self.clock.advance(10)
            with self.board_.store.db.write() as db:
                router_history.record_outcome(db, profile_id=profile_id, plane="work", request_id="earlier-" + str(ordinal),
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
        self.add_profile(C, model="charlie")
        with self.board_.store.db.write() as db:
            db.execute("UPDATE evaluation_profiles SET enabled=0 WHERE profile_id=?", (A,))
        request = self.request()
        second = self.claim("second", task_id=request["runId"])["claim"]
        self.report(second, self.failure(second), status="failed")
        first = self.boundary(request["decisionId"])["routerTrials"][0]
        self.assertEqual(first["phase"], "preflight")
        self.assertEqual(first["outcome"], "no_answer")
        self.assertEqual(first["code"], "router-unavailable")
        self.assertEqual(first["consecutiveNoAnswers"], 1)
        self.assertIsNone(first["attemptId"])
        self.assertIsNone(first["usage"])

    def test_unknown_stop_blocks_governed_commands_and_does_not_claim_prior_output(self):
        request, first = self.start()
        self.report(first, self.failure(first), status="failed", shutdown=False)
        result = self.boundary(request["decisionId"], run_id="run-1", revision=3)
        trial = result["routerTrials"][0]
        self.assertEqual(trial["outcome"], "stopUnknown")
        self.assertFalse(trial["shutdownConfirmed"])
        for command in result["commands"].values():
            self.assertTrue(command["blocked"])
            self.assertTrue(command["reason"])
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
            db.execute("DELETE FROM evaluation_profiles WHERE profile_id=?", (B,))
        sole = self.request(request_id="sole")
        self.assertIsNone(self.boundary(sole["decisionId"]))
        self.assertFalse(self.decision_row(sole["decisionId"])["routerCalled"])
        with self.board_.store.db.write() as db:
            db.execute("UPDATE evaluation_profiles SET enabled=0")
        no_candidate = self.request(request_id="none-legal")
        self.assertIsNone(self.boundary(no_candidate["decisionId"]))
        self.assertIsNone(self.snapshot_of(no_candidate["decisionId"]))

    def test_account_and_reader_changes_are_program_changes_not_failed_router_trials(self):
        request, first = self.start()
        with self.board_.store.db.write() as db:
            db.execute("INSERT INTO meta(key,value) VALUES('account-selection:dsh',?)",
                       (canonical_json({"source": "worker", "revision": 1}),))
            db.execute("UPDATE evaluation_readers SET released_at=?", (T0,))
        self.report(first, self.failure(first), status="failed")
        boundary = self.boundary(request["decisionId"])
        self.assertEqual(boundary["kind"], "routing-changed")
        self.assertEqual(boundary["routerTrials"][0]["outcome"], "changed")
        self.assertEqual(boundary["facts"]["accounts"]["dsh"]["source"], "native")
        self.assertEqual(self.router_events("router.no_answer", "router.answered"), [])

    def test_cancelled_dispatch_stays_cancelled_without_new_router_failure(self):
        request, first = self.start()
        with self.board_.store.db.write() as db:
            db.execute("UPDATE attempts SET cancel_requested_at=? WHERE attempt_id=?", (T0, first["attempt"]["attemptId"]))
        self.report(first, self.failure(first), status="failed")
        boundary = self.boundary(request["decisionId"])
        self.assertEqual(boundary["routerTrials"][0]["outcome"], "cancelled")
        self.assertEqual(len(self.tasks()), 1)
        self.assertEqual(self.router_events("router.no_answer", "router.answered"), [])

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

    def test_malformed_unrelated_event_cannot_break_frozen_boundary_read(self):
        request, _, _ = self.finish_all()
        before = self.boundary(request["decisionId"])
        with self.board_.store.db.write() as db:
            db.execute("INSERT INTO events(kind,payload_json,created_at) VALUES('router.no_answer','{invalid',?)", (T0,))
            db.execute("INSERT INTO events(kind,payload_json,created_at) VALUES('decision.needs_host','{invalid',?)", (T0,))
        self.assertEqual(self.boundary(request["decisionId"]), before)


class GovernedBoundaryTests(WorkflowTestCase):
    answer = failover.FailoverTests.answer

    def setUp(self):
        super().setUp()
        self.clock = FakeClock(T0)
        self.board_ = self.board(clock=self.clock)
        with self.board_.store.db.write() as db:
            for profile_id, model in ((A, "alpha"), (B, "bravo")):
                db.execute("INSERT INTO evaluation_profiles(profile_id,label,adapter,provider,model,effort,"
                           "available,enabled,capabilities_json,created_revision,updated_revision) "
                           "VALUES(?,?,'dsh','fixture',?,'max',1,1,'[]',0,0)", (profile_id, profile_id, model))
            router._write_settings(db, {**router.configuration(db), "routerProfileIds": []})

    def routed(self, request_id="goal-1", *, access="write", kind="existing"):
        response = self.board_.call("workflow_submit", {"requestId": request_id, "hostId": "host-1",
            "submissionToken": "private-submission-token", "task": "Private routed implementation",
            "cwd": str(self.workdir(request_id)), "executionWorkspace": {"kind": kind, "access": access}})
        self.controls[response["runId"]] = response["control"]
        return response

    def template_params(self, view, template):
        params = copy.deepcopy(template)
        self.assertEqual(params.pop("controlFile"), "<saved-control-file>")
        params.update(self.control(view))
        return params

    def test_governed_boundary_has_post_attention_revision_and_real_continue_tuple(self):
        view = self.routed()
        boundary = view["routing"]["routingBoundary"]
        self.assertEqual(view["activeRequest"]["routingBoundary"], boundary)
        choices = boundary["commands"]["continue"]["choices"]
        self.assertFalse(boundary["commands"]["continue"]["blocked"])
        self.assertEqual([item["profileId"] for item in choices], [A, B])
        for choice in choices:
            params = choice["params"]
            self.assertEqual(params["runId"], view["runId"])
            self.assertEqual(params["expectedRevision"], view["revision"])
            self.assertTrue(params["input"])
            self.assertTrue(params["reason"])
            self.assertLessEqual(len(params["commandId"]), 128)
        chosen = choices[1]["params"]
        continued = self.board_.call("workflow_continue", self.template_params(view, chosen))
        self.assertEqual(continued["executionConfiguration"], chosen["configuration"])
        self.assertEqual(continued["runId"], view["runId"])
        self.assertFalse(continued["awaitingHost"])
        self.assertTrue(self.board_.call("workflow_continue", self.template_params(view, chosen))["duplicate"])

    def test_configuration_required_error_explains_same_boundary_commands(self):
        view = self.routed()
        with self.assertRaises(BoardError) as raised:
            self.continue_run(self.board_, view)
        self.assertEqual(raised.exception.code, "CONFIGURATION_REQUIRED")
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

    def test_frozen_candidate_is_retained_but_changed_identity_cannot_offer_legal_command(self):
        view = self.routed()
        original = view["routing"]["routingBoundary"]
        with self.board_.store.db.write() as db:
            db.execute("UPDATE evaluation_profiles SET model='changed-model' WHERE profile_id=?", (B,))
        latest = self.board_.call("workflow_get", {"runId": view["runId"]})["routing"]["routingBoundary"]
        self.assertEqual(latest["candidates"], original["candidates"])
        self.assertEqual(latest["facts"], original["facts"])
        choice = next(item for item in latest["commands"]["continue"]["choices"] if item["profileId"] == B)
        self.assertTrue(choice["blocked"])
        self.assertTrue(choice["reason"])
        self.assertEqual(choice["params"]["configuration"]["model"], "bravo")

    def test_cleanup_guard_blocks_templates_and_existing_continue_operation(self):
        view = self.routed()
        chosen = view["routing"]["routingBoundary"]["commands"]["continue"]["choices"][0]["params"]
        with self.board_.store.db.write() as db:
            db.execute("INSERT INTO workspace_cleanup_plans(plan_id,run_id,workspace_id,checkout_id,path,kind,state,"
                       "evidence_json,retention_json,actor,created_at,expires_at) "
                       "VALUES('private-cleanup',?,'workspace','checkout','/private-fixture','existing','applying',"
                       "'{}','{}','private-test',?,?)", (view["runId"], T0, T0))
        latest = self.board_.call("workflow_get", {"runId": view["runId"]})
        for command in latest["routing"]["routingBoundary"]["commands"].values():
            self.assertTrue(command["blocked"])
            self.assertTrue(command["reason"])
        with self.assertRaises(BoardError) as raised:
            self.board_.call("workflow_continue", self.template_params(view, chosen))
        self.assertEqual(raised.exception.code, "CONFLICT")

    def test_missing_workspace_ownership_blocks_templates_without_reacquiring_it(self):
        view = self.routed(kind="worktree")
        original = view["routing"]["routingBoundary"]
        params = original["commands"]["continue"]["choices"][0]["params"]
        with self.board_.store.db.write() as db:
            db.execute("DELETE FROM workspace_reservations WHERE holder_task_id=?", (view["runId"],))
        boundary = self.board_.call("workflow_get", {"runId": view["runId"]})["routing"]["routingBoundary"]
        self.assertTrue(boundary["commands"]["continue"]["blocked"])
        self.assertIn("allocation", boundary["commands"]["continue"]["reason"])
        self.assertEqual(boundary["candidates"], original["candidates"])
        with self.assertRaises(BoardError) as raised:
            self.board_.call("workflow_continue", self.template_params(view, params))
        self.assertEqual(raised.exception.code, "PREPARATION_CONFLICT")

    def test_locked_hard_tuple_keeps_frozen_candidates_and_blocks_other_choice(self):
        view = self.routed()
        original = view["routing"]["routingBoundary"]
        with self.board_.store.db.write() as db:
            row = db.execute("SELECT goal_json FROM workflow_runs WHERE run_id=?", (view["runId"],)).fetchone()
            goal = json.loads(row[0])
            goal.update({"adapter": "dsh", "provider": "fixture", "model": "alpha", "effort": "max"})
            db.execute("UPDATE workflow_runs SET goal_json=?,configuration_locked=1 WHERE run_id=?",
                       (canonical_json(goal), view["runId"]))
        latest = self.board_.call("workflow_get", {"runId": view["runId"]})["routing"]["routingBoundary"]
        self.assertEqual(latest["candidates"], original["candidates"])
        other = next(item for item in latest["commands"]["continue"]["choices"] if item["profileId"] == B)
        self.assertTrue(other["blocked"])
        self.assertTrue(other["reason"])
        with self.assertRaises(BoardError) as raised:
            self.board_.call("workflow_continue", self.template_params(view, other["params"]))
        self.assertEqual(raised.exception.code, "CONFIGURATION_CONFLICT")

    def test_nested_boundary_uses_root_control_and_current_owner_revision(self):
        root, leaf = self.routed("root", kind="worktree"), self.routed("leaf", kind="worktree")
        with self.board_.store.db.write() as db:
            db.execute("INSERT INTO workflow_children(child_task_id,parent_run_id,request_id,decision_command_id,state,"
                       "workspace_intent_json,created_at,updated_at) VALUES(?,?, 'private-request','private-authorization',"
                       "'attention','{}',?,?)", (leaf["runId"], root["runId"], T0, T0))
            source = self.board_.store.workflow._request_row(db, leaf["runId"], leaf["activeRequest"]["requestId"])
            self.board_.store.workflow._propagate_boundary(db, source, T0)
        root = self.board_.call("workflow_get", {"runId": root["runId"]})
        leaf = self.board_.call("workflow_get", {"runId": leaf["runId"]})
        for choice in root["routing"]["routingBoundary"]["commands"]["continue"]["choices"]:
            self.assertEqual(choice["params"]["helperPolicy"], "keep")
        proxy = next(item for item in root["pendingRequests"] if (item.get("proxy") or {}).get("runId") == leaf["runId"])
        self.assertEqual(proxy["routingBoundary"], leaf["routing"]["routingBoundary"])
        boundary = leaf["routing"]["routingBoundary"]
        chosen = boundary["commands"]["continue"]["choices"][0]["params"]
        self.assertEqual(chosen["runId"], root["runId"])
        self.assertEqual(chosen["targetRunId"], leaf["runId"])
        self.assertEqual(chosen["expectedRevision"], root["revision"])
        self.assertEqual(chosen["controlFile"], "<saved-control-file>")
        self.assertNotIn("controlToken", canonical_json(boundary))
        params = self.template_params(root, chosen)
        continued = self.board_.call("workflow_continue", params)
        self.assertEqual(continued["runId"], root["runId"])
        continued_leaf = self.board_.call("workflow_get", {"runId": leaf["runId"]})
        self.assertEqual(continued_leaf["executionConfiguration"], chosen["configuration"])
        self.assertFalse(continued_leaf["awaitingHost"])

    def test_terminal_ancestor_blocks_leaf_templates_and_continue_cannot_bypass_it(self):
        root, leaf = self.routed("root", kind="worktree"), self.routed("leaf", kind="worktree")
        with self.board_.store.db.write() as db:
            db.execute("INSERT INTO workflow_children(child_task_id,parent_run_id,request_id,decision_command_id,state,"
                       "workspace_intent_json,created_at,updated_at) VALUES(?,?, 'private-request','private-authorization',"
                       "'attention','{}',?,?)", (leaf["runId"], root["runId"], T0, T0))
        before = self.board_.call("workflow_get", {"runId": leaf["runId"]})
        chosen = before["routing"]["routingBoundary"]["commands"]["continue"]["choices"][0]["params"]
        with self.board_.store.db.write() as db:
            db.execute("UPDATE workflow_runs SET state='cancelled' WHERE run_id=?", (root["runId"],))
        after = self.board_.call("workflow_get", {"runId": leaf["runId"]})
        self.assertTrue(after["routing"]["routingBoundary"]["commands"]["continue"]["blocked"])
        with self.assertRaises(BoardError) as raised:
            self.board_.call("workflow_continue", self.template_params(root, chosen))
        self.assertEqual(raised.exception.code, "ANCESTOR_TERMINAL")

    def test_cli_brief_get_and_await_retain_only_existing_boundary_data(self):
        from buddy import cli, cli_views
        view = self.routed()
        boundary = view["routing"]["routingBoundary"]
        rendered = cli._render_output("get", view, cli_views.OUTPUT_BRIEF)
        self.assertEqual(rendered["routing"]["routingBoundary"], boundary)
        await_response = {"workflow": {"routingBoundary": boundary}, "request": {"routing": True},
                          "nextCommands": [{"method": "continue", "params": {"configuration": "old-placeholder"}}]}
        awaited = cli._render_output("await", await_response, cli_views.OUTPUT_BRIEF)
        self.assertEqual(awaited["routingBoundary"], boundary)
        self.assertEqual(awaited["request"]["routingBoundary"], boundary)
        self.assertTrue(all(isinstance(item["params"]["configuration"], dict)
                            for item in awaited["nextCommands"] if "configuration" in item.get("params", {})))

    def test_cached_health_account_mismatch_blocks_choices_without_replacing_frozen_account(self):
        view = self.routed()
        frozen = view["routing"]["routingBoundary"]
        with self.board_.store.db.write() as db:
            db.execute("UPDATE harness_health SET record_json=? WHERE adapter='dsh'",
                       (canonical_json({"account": {"source": "worker", "credentialRevision": 1}}),))
        current = self.board_.call("workflow_get", {"runId": view["runId"]})["routing"]["routingBoundary"]
        self.assertEqual(current["facts"], frozen["facts"])
        self.assertTrue(current["commands"]["continue"]["blocked"])
        self.assertTrue(all(choice["blocked"] and choice["reason"]
                            for choice in current["commands"]["continue"]["choices"]))
        self.assertEqual(current["facts"]["accounts"]["dsh"]["source"], "native")

    def test_governed_valid_abstention_payload_has_program_nature_before_answered_notification(self):
        with self.board_.store.db.write() as db:
            router._write_settings(db, {**router.configuration(db), "routerProfileIds": [A, B]})
        view = self.routed()
        self.board_.call("worker_register", {"workerId": "w-route", "adapter": "decision", "capabilities": ["decision"]})
        nonce = NONCE + "abstain"
        claim = self.board_.call("worker_claim", {"workerId": "w-route", "claimRequestId": "router-abstain",
            "nonce": nonce, "runId": view["routing"]["taskId"]})["claim"]
        answer = self.answer(claim, profile_id=None)
        reason = "The frozen packet does not justify choosing a buddy"
        answer["decision"]["reason"] = reason
        self.board_.call("worker_result", {"workerId": "w-route", "attemptId": claim["attempt"]["attemptId"],
            "generation": claim["attempt"]["generation"], "nonce": nonce, "status": "ok",
            "shutdownConfirmed": True, "result": answer})
        current = self.board_.call("workflow_get", {"runId": view["runId"]})
        boundary = current["routing"]["routingBoundary"]
        self.assertEqual(boundary["kind"], "router-abstained")
        self.assertEqual(boundary["reason"], reason)
        self.assertEqual(current["activeRequest"]["summary"], reason)
        self.assertEqual(current["activeRequest"]["routingBoundary"], boundary)
        self.assertEqual(boundary["retryAt"], self.clock.value)
        with self.board_.store.db.read() as db:
            payload = json.loads(db.execute("SELECT payload_json FROM workflow_requests WHERE request_id=?",
                                           (current["activeRequest"]["requestId"],)).fetchone()[0])
        self.assertEqual(payload["routingBoundary"]["kind"], "router-abstained")
        self.assertEqual(payload["routingBoundary"]["reason"], reason)

    def test_explicit_reroute_after_known_retry_time_continues_same_goal_without_model(self):
        with self.board_.store.db.write() as db:
            router._write_settings(db, {**router.configuration(db), "routerProfileIds": [A, B]})
        view = self.routed()
        self.board_.call("worker_register", {"workerId": "w-route", "adapter": "decision", "capabilities": ["decision"]})
        for ordinal in (0, 1):
            current = self.board_.call("workflow_get", {"runId": view["runId"]})
            nonce = NONCE + str(ordinal)
            claim = self.board_.call("worker_claim", {"workerId": "w-route", "claimRequestId": "router-" + str(ordinal),
                "nonce": nonce, "runId": current["routing"]["taskId"]})["claim"]
            self.assertIsNotNone(claim)
            claim["nonce"] = nonce
            output = failover.FailoverTests.failure(self, claim)
            self.board_.call("worker_result", {"workerId": "w-route", "attemptId": claim["attempt"]["attemptId"],
                "generation": claim["attempt"]["generation"], "nonce": nonce, "status": "failed",
                "shutdownConfirmed": True, "result": output})
        view = self.board_.call("workflow_get", {"runId": view["runId"]})
        boundary = view["routing"]["routingBoundary"]
        self.assertEqual(boundary["kind"], "router-unavailable")
        self.assertEqual(boundary["retryAt"], "2026-01-01T00:10:00.000Z")
        reroute = boundary["commands"]["reroute"]
        self.assertEqual(reroute["notBefore"], boundary["retryAt"])
        self.assertNotIn("notBefore", reroute["params"])
        self.clock.advance(601)
        view = self.board_.call("workflow_get", {"runId": view["runId"]})
        reroute = view["routing"]["routingBoundary"]["commands"]["reroute"]
        self.assertEqual(reroute["notBefore"], self.clock.value)
        continued = self.board_.call("workflow_continue", self.template_params(view, reroute["params"]))
        self.assertEqual(continued["runId"], view["runId"])
        self.assertIsNone(continued["executionConfiguration"])
        self.board_.store.workflow.prepare_continuation_workspace({"runId": view["runId"]})
        refreshed = self.board_.call("workflow_get", {"runId": view["runId"]})
        self.assertEqual(refreshed["routing"]["status"], "queued")
        self.assertNotEqual(refreshed["routing"]["decisionId"], view["routing"]["decisionId"])
        with self.board_.store.db.read() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM workflow_turns").fetchone()[0], 0)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM attempts").fetchone()[0], 2)


if __name__ == "__main__":
    unittest.main()
