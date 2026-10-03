"""Private publication/failover matrix; native event fixtures, no model or CLI.

The real worker_result/release transactions retain immutable receipts and own
capacity. The coordinator checks program evidence, then either settles once or
queues a distinct frozen dispatch while the governed Goal remains pending.
"""
import copy
import json
import unittest
from unittest.mock import patch

from hey_my_buddy.blackboard.routing import router, router_history, router_sequence
from hey_my_buddy.blackboard.store.db import canonical_json
from hey_my_buddy.errors import BoardError
from hey_my_buddy.protocol.tool_evidence import ToolEventEvidence, normalize_tool_event
from fixtures.router_tool_receipt import claim_tool_receipt
from test_router_dispatch import RouterDispatchTestCase, A, B, C, T0, NONCE
import test_router_dispatch as dispatch_fixtures


class FailoverTests(RouterDispatchTestCase):
    def claim(self, suffix="1", *, task_id=None):
        result = super().claim(suffix, task_id=task_id)
        if result["claim"]:
            result["claim"]["nonce"] = NONCE + suffix
        return result

    def start(self, *, three=False, review=False, governed=False):
        if three:
            self.add_profile(C, model="charlie")
            self.set_settings(ids=[A, B, C])
        if review:
            self.set_settings(mode="review")
            # Review failover tests provide their own private capability for
            # every dispatch; the real DSH review carrier is deferred.
            self.enterContext(patch("hey_my_buddy.buddy.harnesses.dsh.adapter.DshAdapter.local_read_only_check", return_value={
                "eligible": True, "reasonCode": None, "reason": "private fixture",
                "systemSandbox": False, "sameAttemptContinuation": True}))
        with patch("hey_my_buddy.buddy.harnesses.dsh.adapter.DshAdapter.local_read_only_check", return_value={
                "eligible": True, "reasonCode": None, "reason": "private fixture",
                "systemSandbox": False, "sameAttemptContinuation": True}):
            request = (dispatch_fixtures.WorkflowWiringTests.route_goal(self) if governed else self.request())
            if governed:
                dispatch_fixtures.WorkflowWiringTests.attach_pending_link(self, request["decisionId"])
            claimed = self.claim("first", task_id=request["runId"])["claim"]
        self.assertIsNotNone(claimed)
        return request, claimed

    def answer(self, claim, *, profile_id=B, tools=0, **receipt_options):
        doc = claim["decisionInput"]
        return {**claim_tool_receipt(claim, tools, **receipt_options), "status": "ok", "operation": "select",
                "tableRevision": doc["tableRevision"], "usage": {"elapsedMs": 100, "toolCalls": tools, "bytesRead": None},
                "stopEvidence": {"shutdownConfirmed": True, "native": {"shutdownConfirmed": True}},
                "inputVerification": {"unchanged": True, "snapshotSha256": "private-fixture-copy",
                                      "manifestSha256": (doc.get("executionWorkspace") or {}).get("manifestSha256")},
                "decision": {"profileId": profile_id, "reason": "The frozen legal choice", "evidence": []}}

    def failure(self, claim, code="timeout"):
        return {**self.answer(claim), "status": "error", "code": code, "decision": None}

    def report(self, claim, output, *, status="ok", shutdown=True, command=None):
        params = {"workerId": "w-route", "attemptId": claim["attempt"]["attemptId"],
                  "generation": claim["attempt"]["generation"], "nonce": claim["nonce"],
                  "status": status, "shutdownConfirmed": shutdown, "result": output}
        if command:
            params["commandId"] = command
        return self.board_.call("worker_result", params)

    def current_task(self, decision_id):
        with self.board_.store.db.read() as db:
            return self.board_.store.decisions._row(db, decision_id)["decision_task_id"]

    def next_claim(self, request, suffix="next"):
        with patch("hey_my_buddy.buddy.harnesses.dsh.adapter.DshAdapter.local_read_only_check", return_value={
                "eligible": True, "reasonCode": None, "reason": "private fixture",
                "systemSandbox": False, "sameAttemptContinuation": True}):
            result = self.claim(suffix, task_id=self.current_task(request["decisionId"]))
        self.assertIsNotNone(result["claim"], result)
        return result["claim"]

    def outcomes(self):
        return self.router_events("router.no_answer", "router.answered")

    def assert_switch(self, request, claim, output, *, status="ok", code=None):
        old_dispatch = self.dispatch_of(claim["task"]["taskId"])
        response = self.report(claim, output, status=status)
        self.assertEqual(response["decision"]["outcome"], "no-answer", response)
        self.assertEqual(response["decision"]["status"], "queued")
        self.assertNotEqual(self.current_task(request["decisionId"]), claim["task"]["taskId"])
        self.assertEqual(self.dispatch_of(self.current_task(request["decisionId"]))["profileId"], B)
        self.assertEqual(self.dispatch_of(claim["task"]["taskId"]), old_dispatch)
        events = self.outcomes()
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0][0], "router.no_answer")
        self.assertEqual(events[0][1]["taskId"], claim["task"]["taskId"])
        self.assertEqual(events[0][1]["attemptId"], claim["attempt"]["attemptId"])
        self.assertEqual(events[0][1]["routerIndex"], 0)
        if code:
            self.assertEqual(events[0][1]["code"], code)
        with self.board_.store.db.read() as db:
            terminal = db.execute("SELECT COUNT(*) FROM events WHERE kind IN ('decision.needs_host','decision.failed','decision.completed')").fetchone()[0]
            receipt = json.loads(db.execute("SELECT result_json FROM attempts WHERE attempt_id=?",
                                           (claim["attempt"]["attemptId"],)).fetchone()[0])
        self.assertEqual(terminal, 0)
        self.assertEqual(receipt["result"], output)
        return response

    def assert_boundary(self, request, claim, output, *, outcome="changed", shutdown=True, status="ok"):
        response = self.report(claim, output, shutdown=shutdown, status=status)
        self.assertEqual(response["decision"]["outcome"], outcome, response)
        self.assertEqual(len(self.tasks()), 1)
        self.assertEqual(self.current_task(request["decisionId"]), claim["task"]["taskId"])
        self.assertEqual(self.outcomes(), [])
        return response

    def test_two_no_answers_keep_request_and_dispatch_receipts_without_loop(self):
        request, first = self.start()
        frozen = self.snapshot_of(request["decisionId"])
        before = self.dispatch_of(request["runId"])
        output = self.failure(first)
        self.assert_switch(request, first, output)
        second = self.next_claim(request)
        self.assertEqual(second["decisionInput"]["routerProfileId"], B)
        self.assertEqual(second["attempt"]["modelFamily"], {"adapter": "dsh", "provider": "fixture", "model": "bravo"})
        self.assertEqual(self.meta("attempt-routing-accounts:" + second["attempt"]["attemptId"]),
                         {"dsh": {"source": "native", "credentialRevision": 0}})
        self.assertEqual(self.meta("attempt-tool-policy:" + second["attempt"]["attemptId"]), {"systemSandbox": False})
        result = self.report(second, self.failure(second, "provider-error"), status="failed")
        self.assertEqual(result["decision"]["status"], "needs-host")
        self.assertEqual(len(self.tasks()), 2)

        self.assertEqual([e[1]["profileId"] for e in self.outcomes()], [A, B])
        self.assertEqual(self.snapshot_of(request["decisionId"]), frozen)
        self.assertEqual(self.dispatch_of(request["runId"]), before)
        with self.board_.store.db.read() as db:
            trials = router_sequence.dispatches(db, request["decisionId"])
            self.assertEqual([trial["routerIndex"] for trial in trials], [0, 1])
            trials[0]["document"]["task"] = "mutated projection"
            self.assertEqual(router_sequence.dispatches(db, request["decisionId"])[0], before)
            for item, expected in ((first, output), (second, self.failure(second, "provider-error"))):
                payload = json.loads(db.execute("SELECT result_json FROM attempts WHERE attempt_id=?",
                                               (item["attempt"]["attemptId"],)).fetchone()[0])
                self.assertEqual(payload["result"], expected)
        self.assertIsNone(self.claim("after-final")["claim"])
        self.assertEqual(len(self.tasks()), 2)

    def test_del_in_native_error_code_is_normalized_without_losing_failover(self):
        request, first = self.start()
        self.assert_switch(request, first, self.failure(first, "provider-error\u007f"), status="failed")
        self.assertEqual(self.outcomes()[0][1]["code"], "router-no-answer")

    def test_blank_native_error_code_is_normalized_without_losing_failover(self):
        request, first = self.start()
        self.assert_switch(request, first, self.failure(first, "   "), status="failed")
        self.assertEqual(self.outcomes()[0][1]["code"], "router-no-answer")

    def test_three_items_stop_at_first_valid_answer_and_keep_parent_pending_between(self):
        request, first = self.start(three=True, governed=True)
        self.assert_switch(request, first, self.failure(first))
        with self.board_.store.db.read() as db:
            self.assertEqual(db.execute("SELECT state FROM workflow_routes").fetchone()[0], "pending")
            self.assertEqual(db.execute("SELECT COUNT(*) FROM workflow_requests").fetchone()[0], 0)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM workflow_turns").fetchone()[0], 0)
            self.assertEqual(db.execute("SELECT adapter,queue_reason FROM tasks WHERE task_id='run-1'").fetchone()[0], "unresolved")
        second = self.next_claim(request)
        output = self.answer(second, profile_id=C)
        output["decision"]["evidence"] = [{"kind": "card", "ref": "unknown-advisory-card"}]
        output["policyCheck"] = {"modelEcho": "ignored"}
        done = self.report(second, output)
        self.assertEqual(done["decision"]["status"], "completed")
        self.assertEqual(len(self.tasks()), 2)
        self.assertEqual([kind for kind, _ in self.outcomes()], ["router.no_answer", "router.answered"])
        with self.board_.store.db.read() as db:
            self.assertEqual(db.execute("SELECT state FROM workflow_routes").fetchone()[0], "resolved")
            self.assertEqual(db.execute("SELECT COUNT(*) FROM workflow_turns").fetchone()[0], 0)

    def test_three_items_stop_at_valid_abstention_with_original_reason(self):
        request, first = self.start(three=True)
        self.assert_switch(request, first, self.failure(first))
        second = self.next_claim(request)
        output = self.answer(second, profile_id=None)
        output["decision"]["reason"] = "I cannot choose within these bounds"
        done = self.report(second, output)
        self.assertEqual(done["decision"]["outcome"], "abstained")
        self.assertEqual(done["decision"]["status"], "needs-host")
        self.assertEqual(done["decision"]["reason"], output["decision"]["reason"])
        self.assertEqual(len(self.tasks()), 2)
        self.assertEqual([kind for kind, _ in self.outcomes()], ["router.no_answer", "router.answered"])
        with self.board_.store.db.read() as db:
            self.assertEqual(router_history.router_state(db, profile_id=B, interval_seconds=600, now=T0)["consecutiveNoAnswers"], 0)

    def test_frozen_inside_candidate_lost_availability_is_changed(self):
        request, first = self.start()
        with self.board_.store.db.write() as db:
            db.execute("UPDATE evaluation_profiles SET available=0 WHERE profile_id=?", (B,))
        self.assert_boundary(request, first, self.answer(first))

    def test_frozen_inside_candidate_loses_required_capability(self):
        with self.board_.store.db.write() as db:
            db.execute("UPDATE evaluation_profiles SET capabilities_json='[\"coding\"]'")
        request = self.board_.call("selection_request", {"requestId": "capability", "task": "choose",
                                                         "requiredCapabilities": ["coding"]})
        first = self.claim("first", task_id=request["runId"])["claim"]
        with self.board_.store.db.write() as db:
            db.execute("UPDATE evaluation_profiles SET capabilities_json='[]' WHERE profile_id=?", (B,))
        self.assert_boundary(request, first, self.answer(first))
        self.assertEqual(self.decision_row(request["decisionId"])["error"], "router-out-of-bounds")

    def test_cached_router_health_loss_does_not_veto_valid_other_candidate(self):
        request, first = self.start()
        with self.board_.store.db.write() as db:
            db.execute("UPDATE harness_health SET status='unhealthy' WHERE adapter='dsh'")
        result = self.report(first, self.answer(first, profile_id=B))
        self.assertEqual(result["decision"]["status"], "completed")
        self.assertEqual(self.outcomes()[0][0], "router.answered")
        self.assertEqual(len(self.tasks()), 1)

    def test_invalid_null_profile_does_not_count_as_abstention(self):
        request, first = self.start()
        output = self.answer(first, profile_id=None)
        output["usage"]["toolCalls"] = None
        self.assert_switch(request, first, output, code="router-tool-evidence-unverified")

    def test_review_changed_verification_takes_priority_over_helper_failure(self):
        request, first = self.start(review=True)
        output = self.failure(first, "provider-error")
        output["inputVerification"]["unchanged"] = False
        self.assert_boundary(request, first, output, status="failed")

    def test_current_input_tamper_cannot_switch(self):
        request, first = self.start()
        with self.board_.store.db.write() as db:
            doc = copy.deepcopy(first["decisionInput"])
            doc["task"] = "changed"
            db.execute("UPDATE decision_requests SET input_json=? WHERE decision_id=?", (canonical_json(doc), request["decisionId"]))
        self.assert_boundary(request, first, self.failure(first))

    def test_final_atomic_quota_claim_refusal_never_records_answer_or_switches(self):
        request, first = self.start()
        with patch("hey_my_buddy.blackboard.routing.quota_routing.claim", return_value=None):
            result = self.report(first, self.answer(first))
        self.assertEqual(result["decision"]["outcome"], "changed")
        self.assertEqual(result["decision"]["status"], "needs-host")
        self.assertEqual(self.outcomes(), [])
        self.assertEqual(len(self.tasks()), 1)

    def test_result_request_and_outcome_replay_do_not_repeat_tasks_or_history(self):
        request, first = self.start()
        output = self.failure(first)
        self.assert_switch(request, first, output)
        duplicate = self.report(first, output)
        self.assertTrue(duplicate["duplicate"])
        duplicate_request = self.request()
        self.assertTrue(duplicate_request["duplicate"])
        with self.board_.store.db.write() as db:
            event = self.outcomes()[0][1]
            replay = router_history.record_outcome(db, profile_id=A, plane="work", request_id="pick-1",
                task_id=event["taskId"], attempt_id=event["attemptId"], outcome="no_answer", code=event["code"],
                phase=event["phase"], facts={"routerIndex": 0}, now=T0)
            self.assertFalse(replay["recorded"])
        self.assertEqual(len(self.tasks()), 2)
        self.assertEqual(len(self.outcomes()), 1)

    def test_late_old_task_attempt_generation_and_nonce_do_not_mutate_current(self):
        request, first = self.start()
        self.assert_switch(request, first, self.failure(first))
        second = self.next_claim(request)
        with self.board_.store.db.write() as db:
            coordinator = self.board_.store.decisions
            row = coordinator._row(db, request["decisionId"])
            task = db.execute("SELECT * FROM tasks WHERE task_id=?", (first["task"]["taskId"],)).fetchone()
            attempt = db.execute("SELECT * FROM attempts WHERE attempt_id=?", (first["attempt"]["attemptId"],)).fetchone()
            late = coordinator.complete(db, task=task, attempt=attempt, status="ok", result=self.answer(first), shutdown_confirmed=True, error=None, now=T0)
            self.assertTrue(late["late"])
            coordinator.cancelled(db, task=task, reason="old cancel", now=T0)
            coordinator.fence_attempt(db, attempt=attempt, reason="old fence", now=T0)
            coordinator.released(db, task=task, reason="old release", now=T0)
            task = db.execute("SELECT * FROM tasks WHERE task_id=?", (second["task"]["taskId"],)).fetchone()
            attempt = dict(db.execute("SELECT * FROM attempts WHERE attempt_id=?", (second["attempt"]["attemptId"],)).fetchone())
            for key, value in (("attempt_id", first["attempt"]["attemptId"]), ("generation", 0), ("nonce_verifier", "wrong")):
                late = coordinator.complete(db, task=task, attempt={**attempt, key: value}, status="ok",
                    result=self.answer(second), shutdown_confirmed=True, error=None, now=T0)
                self.assertTrue(late["late"])
            self.assertEqual(dict(coordinator._row(db, request["decisionId"])), dict(row))
        self.assertEqual(len(self.outcomes()), 1)
        done = self.report(second, self.answer(second))
        self.assertEqual(done["decision"]["status"], "completed")
        final = self.decision_row(request["decisionId"])
        with self.board_.store.db.write() as db:
            task = db.execute("SELECT * FROM tasks WHERE task_id=?", (second["task"]["taskId"],)).fetchone()
            attempt = db.execute("SELECT * FROM attempts WHERE attempt_id=?", (second["attempt"]["attemptId"],)).fetchone()
            self.board_.store.decisions.complete(db, task=task, attempt=attempt, status="failed", result={"late": True}, shutdown_confirmed=True, error=None, now=T0)
        self.assertEqual(self.decision_row(request["decisionId"]), final)

    def test_native_false_or_unknown_never_accepts_or_switches(self):
        # Separate dynamically installed cases below also cover every stop layer.
        request, first = self.start()
        output = self.answer(first)
        output["stopEvidence"]["native"]["shutdownConfirmed"] = False
        self.assert_boundary(request, first, output, outcome="stop-unconfirmed")

    def test_outer_unknown_stop_preserves_generic_capacity(self):
        request, first = self.start()
        self.assert_boundary(request, first, self.failure(first), outcome="stop-unconfirmed", shutdown=False)
        with self.board_.store.db.read() as db:
            attempt = db.execute("SELECT execution_state,shutdown_confirmed FROM attempts WHERE attempt_id=?", (first["attempt"]["attemptId"],)).fetchone()
            self.assertEqual(tuple(attempt), ("uncertain", 0))
            self.assertEqual(len(router_history.active_executions(db, profile_id=A)), 1)
        self.assertEqual(len(self.tasks()), 1)

    def test_release_real_never_spawned_proof_advances(self):
        request, first = self.start(review=True)
        response = self.board_.call("worker_release", {"workerId": "w-route", "attemptId": first["attempt"]["attemptId"],
            "generation": first["attempt"]["generation"], "nonce": first["nonce"], "reason": "local preflight refused",
            "evidence": {"spawnIntentWritten": False}})
        self.assertNotEqual(self.current_task(request["decisionId"]), first["task"]["taskId"])
        self.assertEqual(self.outcomes()[0][1]["phase"], "preflight")
        self.assertEqual(self.outcomes()[0][1]["profileId"], A)
        self.assertEqual(len(self.tasks()), 2)

    def test_release_without_never_spawned_proof_cannot_advance(self):
        request, first = self.start()
        self.board_.call("worker_release", {"workerId": "w-route", "attemptId": first["attempt"]["attemptId"],
            "generation": first["attempt"]["generation"], "nonce": first["nonce"], "reason": "unknown spawn"})
        self.assertEqual(len(self.tasks()), 1)
        self.assertEqual(self.outcomes(), [])
        self.assertEqual(self.decision_row(request["decisionId"])["error"], "router-stop-unconfirmed")

    def test_next_dispatch_input_ceiling_is_request_boundary_not_unrun_router_failure(self):
        with self.board_.store.db.write() as db:
            db.execute("UPDATE evaluation_profiles SET model=? WHERE profile_id=?", ("b" * 80, B))
        request, first = self.start(three=True)
        bound = len(canonical_json(first["decisionInput"]).encode("utf-8"))
        with patch("hey_my_buddy.blackboard.routing.decision.MAX_DECISION_INPUT_BYTES", bound):
            result = self.report(first, self.failure(first))
        self.assertEqual(result["decision"]["status"], "needs-host")
        self.assertEqual(self.decision_row(request["decisionId"])["error"], "router-input-too-large")
        self.assertEqual(len(self.tasks()), 1)
        self.assertEqual([event[1]["profileId"] for event in self.outcomes()], [A])

    def test_publication_savepoint_rolls_back_partial_dispatch_but_commits_worker_receipt(self):
        request, first = self.start()
        original = self.board_.store.decisions._queue_router_dispatch
        old = self.dispatch_of(request["runId"])
        output = self.failure(first)

        def fail_after_queue(db, row, **kwargs):
            original(db, row, **kwargs)
            raise BoardError("INTERNAL_ERROR", "private fixture publication failure")

        with patch.object(self.board_.store.decisions, "_queue_router_dispatch", side_effect=fail_after_queue):
            response = self.report(first, output)
        self.assertTrue(response["committed"])
        self.assertEqual(response["decision"]["status"], "needs-host")
        self.assertEqual(len(self.tasks()), 1)
        self.assertEqual(self.current_task(request["decisionId"]), request["runId"])
        self.assertEqual(self.dispatch_of(request["runId"]), old)
        self.assertEqual(self.outcomes(), [])
        with self.board_.store.db.read() as db:
            self.assertEqual(len(router_sequence.dispatches(db, request["decisionId"])), 1)
            receipt = json.loads(db.execute("SELECT result_json FROM attempts WHERE attempt_id=?",
                                           (first["attempt"]["attemptId"],)).fetchone()[0])
            self.assertEqual(receipt["result"], output)
        self.assertTrue(self.report(first, output)["duplicate"])
        self.assertEqual(len(self.tasks()), 1)

    def test_publish_select_is_dto_only_and_active_reader_is_normal(self):
        request, first = self.start()
        with self.board_.store.db.write() as db:
            row = self.board_.store.decisions._row(db, request["decisionId"])
            before = db.execute("SELECT COUNT(*) FROM events").fetchone()[0]
            dto = self.board_.store.decisions._publish_select(db, row, output=self.answer(first), now=T0)
            self.assertEqual(set(dto), {"answer_valid", "abstained", "changed", "code", "reason", "output", "profile_id", "selected", "evidence"})
            self.assertTrue(dto["answer_valid"])
            self.assertEqual(self.board_.store.decisions._row(db, request["decisionId"])["status"], "running")
            self.assertEqual(db.execute("SELECT COUNT(*) FROM events").fetchone()[0], before)
        self.assertEqual(self.report(first, self.answer(first))["decision"]["status"], "completed")


    def bad_native_stream(self, claim, kind):
        output = self.answer(claim, tools=1)
        binding = output["toolEvidence"]["binding"]
        root = output["nativeIdentity"]
        collector = ToolEventEvidence(binding)
        if kind == "late":
            collector.close_root(root)
        identity = {"sessionId": "child-session"} if kind == "child" else root
        phases = ("end", "start") if kind == "reversed" else ("start",) if kind == "unfinished" else ("start", "end")
        for phase in phases:
            collector.observe(normalize_tool_event("dsh", {"nativeIdentity": identity,
                "callId": "private-read", "toolName": "read", "phase": phase}))
        output["toolEvidence"] = collector.finish([root], True)
        if kind == "truncated":
            # Truncation is a controller stream fact, independent of root shape.
            output["toolEvidence"]["truncated"] = True
        return output

    def test_review_legal_read_and_unknown_bytes_can_publish(self):
        request, first = self.start(review=True)
        output = self.answer(first, tools=2)
        result = self.report(first, output)
        self.assertEqual(result["decision"]["outcome"], "answered")
        with self.board_.store.db.read() as db:
            row = self.board_.store.decisions._row(db, request["decisionId"])
            self.assertIsNone(json.loads(row["output_json"])["usage"]["bytesRead"])
        self.assertEqual(len(self.tasks()), 1)

    def test_model_started_false_does_not_substitute_for_spawn_or_native_stop_proof(self):
        request, first = self.start()
        output = {"status": "error", "code": "router-unavailable", "modelStarted": False}
        self.assert_boundary(request, first, output, outcome="stop-unconfirmed", status="failed")

    def test_release_cancelled_parent_never_advances_despite_never_spawned_proof(self):
        request, first = self.start(governed=True)
        with self.board_.store.db.write() as db:
            db.execute("UPDATE workflow_runs SET state='cancelled'")
        self.board_.call("worker_release", {"workerId": "w-route", "attemptId": first["attempt"]["attemptId"],
            "generation": first["attempt"]["generation"], "nonce": first["nonce"], "reason": "cancelled parent",
            "evidence": {"spawnIntentWritten": False}})
        self.assertEqual(self.decision_row(request["decisionId"])["status"], "cancelled")
        self.assertEqual(self.outcomes(), [])
        self.assertEqual(len(self.tasks()), 1)

    def test_actual_quota_retry_claim_race_is_changed_at_final_adoption(self):
        from hey_my_buddy.blackboard.routing import quota_routing
        request, first = self.start()
        quota = {"provider": "fixture", "scope": {"limitId": "bravo"}, "balanceZero": True,
                 "observedAt": "2025-12-31T22:00:00.000Z", "source": "native"}
        with self.board_.store.db.write() as db:
            quota_routing.record(db, "dsh", quota, now=T0)
        original = self.board_.store.decisions._finish

        def competing_claim(db, row, **kwargs):
            if kwargs["status"] == "completed":
                claimed = quota_routing.claim(db, kwargs["selected"], decision_id="other-decision", now=T0)
                self.assertIsNotNone(claimed)
                self.assertTrue(claimed[0]["claimed"])
            return original(db, row, **kwargs)

        with patch.object(self.board_.store.decisions, "_finish", side_effect=competing_claim):
            result = self.report(first, self.answer(first))
        self.assertEqual(result["decision"]["outcome"], "changed")
        self.assertEqual(self.decision_row(request["decisionId"])["error"], "router-out-of-bounds")
        self.assertEqual(self.outcomes(), [])
        self.assertEqual(len(self.tasks()), 1)
        with self.board_.store.db.read() as db:
            record = quota_routing.routing_facts(db, "dsh", now=T0)[0]
            self.assertEqual(record["retry"]["consumedBy"], "other-decision")

    def test_next_item_writer_gate_keeps_one_queued_dispatch(self):
        request, first = self.start(three=True)
        self.assert_switch(request, first, self.failure(first))
        with self.board_.store.db.write() as db:
            db.execute("INSERT INTO evaluation_writers(writer_id,request_id,kind,state,generation,"
                       "expected_revision,token_verifier,requested_at) VALUES('gate','gate','human','waiting',1,0,'verifier',?)", (T0,))
        response = self.claim("writer", task_id=self.current_task(request["decisionId"]))
        self.assertIsNone(response["claim"])
        self.assertEqual(response["reason"], "evaluation-writer-pending")
        self.assertEqual(len(self.tasks()), 2)
        self.assertEqual(len(self.outcomes()), 1)

    def busy_attempt(self, db, number, *, provider="busy", model="busy", profile_id=None):
        task_id, attempt_id = f"busy-{number}", f"busy-attempt-{number}"
        db.execute("INSERT INTO tasks(task_id,request_id,owner,spec_json,spec_canonical_json,input_fingerprint,"
                   "fingerprint_version,adapter,cwd,timeout_seconds,state,revision,created_at,updated_at)"
                   " VALUES(?,?,'host','{}','{}','digest',1,'dsh','/work',60,'running',1,?,?)", (task_id, task_id, T0, T0))
        db.execute("INSERT INTO attempts(attempt_id,task_id,generation,nonce_verifier,claim_request_id,execution_state,"
                   "adapter,model_adapter,model_provider,model_model,started_at,created_at,updated_at)"
                   " VALUES(?,?,1,'verifier',?,'executing','dsh','dsh',?,?,?,?,?)",
                   (attempt_id, task_id, f"busy-claim-{number}", provider, model, T0, T0, T0))
        if profile_id:
            self.board_.store._append_event(db, "router.claimed", task_id=task_id, attempt_id=attempt_id,
                payload={"profileId": profile_id, "plane": "work", "requestId": task_id, "routerIndex": 0,
                         "taskId": task_id, "attemptId": attempt_id, "generation": 1})

    def test_next_item_capacity_does_not_bypass_to_third(self):
        request, first = self.start(three=True)
        self.assert_switch(request, first, self.failure(first))
        with self.board_.store.db.write() as db:
            self.busy_attempt(db, 1)
            self.busy_attempt(db, 2)
        response = self.claim("capacity", task_id=self.current_task(request["decisionId"]))
        self.assertIsNone(response["claim"])
        self.assertEqual(response["reason"], "capacity")
        self.assertEqual(len(self.tasks()), 2)
        self.assertEqual(len(self.outcomes()), 1)

    def test_next_item_active_retry_queues_without_bypass(self):
        request, first = self.start(three=True)
        self.assert_switch(request, first, self.failure(first))
        with self.board_.store.db.write() as db:
            router_history.record_outcome(db, profile_id=B, plane="work", request_id="elsewhere",
                task_id=None, attempt_id=None, outcome="no_answer", code="timeout", phase="runtime", facts={"routerIndex": 0}, now=T0)
            self.busy_attempt(db, 1, provider="fixture", model="bravo", profile_id=B)
        response = self.claim("retry", task_id=self.current_task(request["decisionId"]))
        self.assertIsNone(response["claim"])
        self.assertEqual(response["reason"], "router-retry-in-progress")
        self.assertEqual(len(self.tasks()), 2)
        self.assertEqual(len([payload for _, payload in self.outcomes() if payload["requestId"] == "pick-1"]), 1)

    def test_real_preflight_failure_of_later_item_is_recorded_once_and_walks_forward(self):
        request, first = self.start(three=True)
        with self.board_.store.db.write() as db:
            db.execute("UPDATE evaluation_profiles SET enabled=0 WHERE profile_id=?", (B,))
        response = self.report(first, self.failure(first))
        self.assertEqual(response["decision"]["status"], "queued")
        next_task = self.current_task(request["decisionId"])
        self.assertEqual(self.dispatch_of(next_task)["profileId"], C)
        events = self.outcomes()
        self.assertEqual([(p["profileId"], p["phase"]) for _, p in events], [(A, "runtime"), (B, "preflight")])
        self.request()
        self.assertEqual(self.outcomes(), events)
        self.assertEqual(len(self.tasks()), 2)

    def test_new_skip_window_is_observed_without_recording_or_extension(self):
        request, first = self.start(three=True)
        with self.board_.store.db.write() as db:
            router_history.record_outcome(db, profile_id=B, plane="work", request_id="elsewhere",
                task_id=None, attempt_id=None, outcome="no_answer", code="timeout", phase="runtime", facts={"routerIndex": 0}, now=T0)
            before = router_history.router_state(db, profile_id=B, interval_seconds=600, now=T0)
        self.report(first, self.failure(first))
        self.assertEqual(self.dispatch_of(self.current_task(request["decisionId"]))["profileId"], C)
        with self.board_.store.db.read() as db:
            self.assertEqual(router_history.router_state(db, profile_id=B, interval_seconds=600, now=T0), before)
        self.assertEqual(len(self.outcomes()), 2)
        self.assertEqual(len(self.tasks()), 2)


def install_no_answer_case(name, mutate, code, *, review=False, outer="ok"):
    def test(self):
        request, first = self.start(review=review)
        output = mutate(self, first, self.answer(first))
        self.assert_switch(request, first, output, status=outer, code=code)
    setattr(FailoverTests, "test_no_answer_" + name, test)


for name, code in (("timeout", "timeout"), ("provider", "provider-error"), ("native", "native-error"),
                   ("empty", "answer-empty"), ("bad_json", "answer-invalid-json"), ("budget_runtime", "router-budget-exhausted")):
    install_no_answer_case(name, lambda self, claim, output, code=code: self.failure(claim, code), code, outer="failed")
install_no_answer_case("no_choice", lambda self, claim, o: {**o, "decision": None}, "answer-shape")
install_no_answer_case("json_string", lambda self, claim, o: {**o, "decision": "{bad"}, "answer-invalid-json")
install_no_answer_case("extra_shape", lambda self, claim, o: {**o, "decision": {**o["decision"], "extra": True}}, "answer-shape")
install_no_answer_case("wrong_enum", lambda self, claim, o: {**o, "decision": {**o["decision"], "evidence": [{"kind": "unknown", "ref": "x"}]}}, "answer-shape")
install_no_answer_case("outside_id", lambda self, claim, o: {**o, "decision": {**o["decision"], "profileId": "outside"}}, "router-out-of-bounds")
install_no_answer_case("illegal_evidence", lambda self, claim, o: {**o, "decision": {**o["decision"], "evidence": [{"kind": "file", "ref": "../secret"}]}}, "answer-shape", review=True)
install_no_answer_case("fast_file_evidence", lambda self, claim, o: {**o, "decision": {**o["decision"], "evidence": [{"kind": "file", "ref": "src/foo"}]}}, "router-evidence-out-of-bounds")
install_no_answer_case("elapsed_budget", lambda self, claim, o: {**o, "usage": {"elapsedMs": 60001, "toolCalls": 0}}, "router-budget-exhausted")
install_no_answer_case("missing_counter", lambda self, claim, o: {**o, "usage": {"elapsedMs": 100}}, "router-tool-evidence-unverified")
install_no_answer_case("missing_evidence", lambda self, claim, o: {**o, "toolEvidence": None}, "router-tool-evidence-unverified")
install_no_answer_case("incomplete", lambda self, claim, o: self.answer(claim, stream_complete=False), "router-tool-evidence-unverified")
install_no_answer_case("forbidden_fast", lambda self, claim, o: self.answer(claim, tools=1), "router-tools-forbidden")
install_no_answer_case("forbidden_review", lambda self, claim, o: self.answer(claim, tools=1, tool_name="write"), "router-tools-forbidden", review=True)
install_no_answer_case("wrong_operation", lambda self, claim, o: {**o, "operation": "maintain"}, "answer-shape")
install_no_answer_case("wrong_revision", lambda self, claim, o: {**o, "tableRevision": 999}, "answer-shape")
install_no_answer_case("outer_failed_native_stopped", lambda self, claim, o: o, "router-no-answer", outer="failed")


install_no_answer_case("review_failed_missing_verification", lambda self, claim, o: {**self.failure(claim, "provider-error"), "inputVerification": None},
                      "provider-error", review=True, outer="failed")
install_no_answer_case("review_answer_missing_verification", lambda self, claim, o: {**o, "inputVerification": None},
                      "router-input-changed", review=True)

for stream in ("child", "late", "reversed", "unfinished", "truncated"):
    install_no_answer_case("stream_" + stream, lambda self, claim, output, stream=stream: self.bad_native_stream(claim, stream),
                           "router-tool-evidence-unverified", review=True)
install_no_answer_case("foreign_binding", lambda self, claim, o: {**o, "toolEvidence": {**o["toolEvidence"],
                      "binding": {**o["toolEvidence"]["binding"], "attemptId": "foreign-attempt"}}}, "router-tool-evidence-unverified")
install_no_answer_case("root_mismatch", lambda self, claim, o: {**o, "nativeIdentity": {"sessionId": "foreign-root"}}, "router-tool-evidence-unverified")
install_no_answer_case("counter_mismatch", lambda self, claim, o: {**o, "usage": {"elapsedMs": 100, "toolCalls": 1}}, "router-tool-evidence-unverified")
install_no_answer_case("review_tool_budget", lambda self, claim, o: self.answer(claim, tools=claim["decisionInput"]["budget"]["toolCalls"] + 1),
                      "router-budget-exhausted", review=True)
install_no_answer_case("review_time_budget", lambda self, claim, o: {**o, "usage": {"elapsedMs": claim["decisionInput"]["budget"]["timeoutSeconds"] * 1000 + 1, "toolCalls": 0}},
                      "router-budget-exhausted", review=True)


def install_change_case(name, mutate, *, review=False, governed=False, outcome="changed"):
    def test(self):
        request, first = self.start(review=review, governed=governed)
        output = self.failure(first)
        mutate(self, request, first, output)
        self.assert_boundary(request, first, output, outcome=outcome)
    setattr(FailoverTests, "test_boundary_" + name, test)


def execute_change(sql, params=()):
    def mutate(self, request, claim, output):
        with self.board_.store.db.write() as db:
            db.execute(sql, params)
    return mutate


for name, kwargs in (("list", {"ids": [B, A]}), ("mode", {"mode": "review"}), ("preset", {"budget": "brief"}), ("interval", {"interval": 30})):
    install_change_case(name, lambda self, request, claim, output, kwargs=kwargs: self.set_settings(**kwargs))
install_change_case("revision", execute_change("UPDATE evaluation_state SET configuration_revision=configuration_revision+1 WHERE id=1"))
install_change_case("table", execute_change("UPDATE evaluation_state SET table_revision=table_revision+1 WHERE id=1"))
install_change_case("account", execute_change("INSERT INTO meta(key,value) VALUES('account-selection:dsh',?)", (canonical_json({"source": "worker", "revision": 1}),)))
install_change_case("reader_release", execute_change("UPDATE evaluation_readers SET released_at=?", (T0,)))
install_change_case("reader_lease", execute_change("UPDATE evaluation_readers SET expires_at=?", (T0,)))
install_change_case("identity", execute_change("UPDATE evaluation_profiles SET model='replacement' WHERE profile_id=?", (A,)))
install_change_case("owner", execute_change("UPDATE workflow_runs SET owner_generation=2"), governed=True)
install_change_case("route", execute_change("UPDATE workflow_routes SET state='fenced'"), governed=True)
install_change_case("parent_cancel", execute_change("UPDATE workflow_runs SET state='cancelled'"), governed=True, outcome="cancelled")
install_change_case("task_cancel_race", execute_change("UPDATE tasks SET state='cancelling' WHERE owner='decision'"), outcome="cancelled")
install_change_case("attempt_cancel_race", execute_change("UPDATE attempts SET cancel_requested_at=?", (T0,)), outcome="cancelled")
install_change_case("copy", lambda self, request, claim, output: output["inputVerification"].update(unchanged=False), review=True)
install_change_case("manifest", lambda self, request, claim, output: output["inputVerification"].update(manifestSha256="changed"), review=True)


def install_candidate_change(name, mutate):
    def test(self):
        request, first = self.start()
        mutate(self)
        response = self.assert_boundary(request, first, self.answer(first))
        self.assertEqual(self.decision_row(request["decisionId"])["error"], "router-out-of-bounds")
    setattr(FailoverTests, "test_inside_candidate_changed_" + name, test)


def candidate_sql(sql, params=()):
    def mutate(self):
        with self.board_.store.db.write() as db:
            db.execute(sql, params)
    return mutate


install_candidate_change("disabled", candidate_sql("UPDATE evaluation_profiles SET enabled=0 WHERE profile_id=?", (B,)))
install_candidate_change("exclude", candidate_sql("INSERT INTO evaluation_preferences VALUES(?,'exclude','fixture boundary',0)", (B,)))
install_candidate_change("pin", candidate_sql("INSERT INTO evaluation_preferences VALUES(?,'pin','fixture boundary',0)", (A,)))
install_candidate_change("identity", candidate_sql("UPDATE evaluation_profiles SET effort='low' WHERE profile_id=?", (B,)))


def quota_closed(self):
    from hey_my_buddy.blackboard.routing import quota_routing
    with self.board_.store.db.write() as db:
        quota_routing.record(db, "dsh", {"provider": "fixture", "scope": {"limitId": "bravo"},
                            "balanceZero": True, "observedAt": T0, "source": "native"}, now=T0)


install_candidate_change("quota", quota_closed)

for layer in ("controller", "native"):
    for value in (False, None):
        def stop_test(self, layer=layer, value=value):
            request, first = self.start()
            output = self.failure(first)
            target = output["stopEvidence"] if layer == "controller" else output["stopEvidence"]["native"]
            target["shutdownConfirmed"] = value
            self.assert_boundary(request, first, output, outcome="stop-unconfirmed")
        setattr(FailoverTests, f"test_stop_{layer}_{value}", stop_test)


if __name__ == "__main__":
    unittest.main()
