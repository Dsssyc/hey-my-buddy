"""Read-only Router diagnostics over private durable receipts and terminal events.

These fixtures deliberately bypass request/claim/publication setup, which is being
migrated separately by G1/G2. No native harness or model is started.
"""
from __future__ import annotations

import json
import unittest

from support import BoardTestCase, FakeClock

from buddy.db import canonical_json, sha256_text


class RoutingHealthTests(BoardTestCase):
    def setUp(self):
        super().setUp()
        self.clock = FakeClock()
        self.private_board = self.board(clock=self.clock)
        self.coordinator = self.private_board.store.decisions
        self.db = self.private_board.store.db
        self.sequence = 0

    def settle(self, status="needs-host", *, code=None, termination="harness-error",
               output=None, error=None, invoked=True, kind="select"):
        """Persist a bound, stopped receipt and use the real atomic terminal writer."""
        self.sequence += 1
        decision_id = f"dec-health-{self.sequence}"
        task_id = f"task-health-{self.sequence}" if invoked else None
        attempt_id = f"attempt-health-{self.sequence}" if invoked else None
        now = self.clock.advance(1)
        if output is None:
            output = {"operation": kind, "status": "ok" if status in ("completed", "stale") else "error"}
            if status in ("completed", "stale"):
                output["decision"] = {"profileId": "worker-fixture", "reason": "fixture choice", "evidence": []}
        if code is not None:
            output = {**output, "code": code}
        receipt_status = "cancelled" if status == "cancelled" else "ok" if output.get("status") == "ok" else "failed"
        receipt = {"status": receipt_status, "result": output, "error": error,
                   "shutdownConfirmed": True, "terminationReason": termination,
                   "completedAt": now}
        with self.db.write() as connection:
            connection.execute(
                "INSERT INTO evaluation_decisions(decision_id,status,task,table_revision,created_at)"
                " VALUES(?,'running','fixture selection',0,?)", (decision_id, now))
            if invoked:
                spec = {"adapter": "decision", "task": "fixture selection",
                        "decision": {"decisionId": decision_id, "kind": kind, "timeoutSeconds": 60}}
                encoded = canonical_json(spec)
                task_state = "completed" if receipt_status == "ok" else receipt_status
                connection.execute(
                    "INSERT INTO tasks(task_id,request_id,owner,spec_json,spec_canonical_json,"
                    " input_fingerprint,fingerprint_version,adapter,cwd,timeout_seconds,state,"
                    " revision,created_at,updated_at) VALUES(?,?,?,?,?,?,1,'decision','<private-cwd>',60,?,1,?,?)",
                    (task_id, task_id, "fixture-host", encoded, encoded, sha256_text(encoded), task_state, now, now))
                connection.execute(
                    "INSERT INTO attempts(attempt_id,task_id,generation,nonce_verifier,claim_request_id,"
                    " execution_state,adapter,result_json,shutdown_confirmed,finished_at,created_at,updated_at)"
                    " VALUES(?,?,1,'fixture-nonce','fixture-claim','finished','decision',?,1,?,?,?)",
                    (attempt_id, task_id, canonical_json(receipt), now, now, now))
            connection.execute(
                "INSERT INTO decision_requests(decision_id,request_id,kind,input_fingerprint,"
                " task_id,attempt_id,generation,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
                (decision_id, decision_id, kind, sha256_text(decision_id), task_id, attempt_id,
                 1 if invoked else None, now, now))
            self.coordinator._finish(connection, self.coordinator._row(connection, decision_id),
                                     status=status, now=now, output=output, error=error,
                                     profile_id="worker-fixture" if status == "completed" else None)
            self.assertEqual(connection.execute("PRAGMA foreign_key_check").fetchall(), [])
        return decision_id, now

    def timeout(self):
        return self.settle(code="router-budget-exhausted", termination="deadline")

    def test_four_timeouts_count_as_failures_while_availability_uses_current_router(self):
        for count in range(1, 5):
            decision_id, now = self.timeout()
            with self.subTest(count=count):
                health = self.coordinator.health_summary()
                self.assertIsNone(health["currentRouterProfileId"])
                self.assertEqual(health["budgetExhaustedCount"], count)
                self.assertEqual(health["failureCount"], count)
                self.assertEqual(health["consecutiveFailures"], count)
                self.assertFalse(health["available"])
                self.assertEqual(health["reasonCode"], "router-not-configured")
                self.assertEqual(health["recentFailures"][0], {
                    "decisionId": decision_id, "runId": f"task-health-{count}",
                    "at": now, "code": "router-budget-exhausted"})

    def test_cancel_stale_and_program_selection_cannot_restore_health(self):
        for _ in range(3):
            self.timeout()
            self.settle("cancelled", code="router-budget-exhausted")
            self.settle("stale", code="router-budget-exhausted")
        self.settle("completed", invoked=False, output={
            "programSelection": {"code": "single-candidate", "profileId": "worker-fixture", "candidateCount": 1}})
        health = self.coordinator.health_summary()
        self.assertFalse(health["available"])
        self.assertEqual(health["reasonCode"], "router-not-configured")
        self.assertEqual(health["failureCount"], 3)
        self.assertEqual(health["budgetExhaustedCount"], 3)
        self.assertEqual(health["consecutiveFailures"], 3)
        self.assertEqual(health["cancelledCount"], 3)
        self.assertEqual(health["staleCount"], 3)
        self.assertIsNone(health["lastSuccessAt"])
        self.assertIsNone(health["lastSuccessDecisionId"])
        success_id, success_at = self.settle("completed", termination="completed")
        self.settle("completed", invoked=False, output={"programSelection": {"code": "single-candidate"}})
        recovered = self.coordinator.health_summary()
        self.assertFalse(recovered["available"])
        self.assertEqual(recovered["reasonCode"], "router-not-configured")
        self.assertEqual(recovered["consecutiveFailures"], 0)
        self.assertEqual(recovered["failureCount"], 3)
        self.assertEqual(recovered["lastSuccessAt"], success_at)
        self.assertEqual(recovered["lastSuccessDecisionId"], success_id)
        self.timeout()
        self.assertEqual(self.coordinator.health_summary()["consecutiveFailures"], 1)

    def test_ordinary_and_mixed_errors_have_failure_reason(self):
        self.settle("failed", code="invalid-native-result")
        self.timeout()
        self.settle("failed", code="transport-error")
        health = self.coordinator.health_summary()
        self.assertFalse(health["available"])
        self.assertEqual(health["reasonCode"], "router-not-configured")
        self.assertEqual(health["failureCount"], 3)
        self.assertEqual(health["consecutiveFailures"], 3)
        self.assertEqual(health["budgetExhaustedCount"], 1)

    def test_bounds_input_and_abstention_keep_independent_counts_without_recovery(self):
        for _ in range(3):
            self.timeout()
            self.settle(code="router-out-of-bounds")
            self.settle(code="router-input-changed")
            self.settle(output={"status": "ok", "operation": "select", "decision": {
                "profileId": None, "reason": "insufficient evidence", "evidence": []}})
        health = self.coordinator.health_summary()
        self.assertFalse(health["available"])
        self.assertEqual(health["consecutiveFailures"], 3)
        self.assertEqual(health["failureCount"], 3)
        self.assertEqual(health["budgetExhaustedCount"], 3)
        self.assertEqual(health["boundsRejectedCount"], 3)
        self.assertEqual(health["inputChangedCount"], 3)
        self.assertEqual(health["abstentionCount"], 3)

    def test_budget_without_timeout_evidence_is_failure_not_invented_timeout(self):
        for _ in range(3):
            self.settle(code="router-budget-exhausted", error="provider says timeout deadline")
        health = self.coordinator.health_summary()
        self.assertEqual(health["budgetExhaustedCount"], 3)
        self.assertEqual(health["failureCount"], 3)
        self.assertEqual(health["reasonCode"], "router-not-configured")

    def test_rejected_null_profile_cannot_hide_a_failed_answer_in_the_overview(self):
        self.settle(output={"status": "ok", "operation": "select", "code": "router-tools-forbidden",
                            "decision": {"profileId": None, "reason": "cannot choose", "evidence": []}})
        health = self.coordinator.health_summary()
        self.assertEqual(health["failureCount"], 1)
        self.assertEqual(health["abstentionCount"], 0)

    def test_explicit_machine_timeout_codes_need_no_provider_prose(self):
        for code in ("timeout", "call-timeout", "deadline"):
            self.settle("failed", code=code)
        health = self.coordinator.health_summary()
        self.assertEqual(health["reasonCode"], "router-not-configured")
        self.assertEqual(health["budgetExhaustedCount"], 0)

    def test_bounded_event_error_code_and_generic_failure_fallback(self):
        for code, expected in (("timeout", "timeout"), ("timeout from provider", "call-failed"),
                               ("x" * 81, "call-failed"), ("TOKEN\nsecret", "call-failed"),
                               (None, "call-failed"), ([], "call-failed")):
            with self.subTest(code=code):
                decision_id, _ = self.settle("failed", output={}, code=code, error="timeout")
                with self.db.read() as connection:
                    event = json.loads(connection.execute(
                        "SELECT payload_json FROM events WHERE kind='decision.failed' ORDER BY seq DESC LIMIT 1").fetchone()[0])
                self.assertEqual(event["errorCode"], code if expected == "timeout" else None)
                recent = self.coordinator.health_summary()["recentFailures"][0]
                self.assertEqual(recent["decisionId"], decision_id)
                self.assertEqual(recent["code"], expected)

    def test_late_output_cannot_reclassify_terminal_receipt_or_move_success(self):
        success_id, success_at = self.settle("completed", termination="completed")
        failure_id, _ = self.timeout()
        with self.db.write() as connection:
            connection.execute("UPDATE decision_requests SET output_json=? WHERE decision_id=?",
                               (canonical_json({"status": "ok", "decision": {"profileId": "worker-fixture"}}), failure_id))
            connection.execute("UPDATE decision_requests SET output_json=?,updated_at=? WHERE decision_id=?",
                               (canonical_json({"status": "error", "code": "timeout"}), self.clock.advance(1), success_id))
        health = self.coordinator.health_summary()
        self.assertEqual(health["budgetExhaustedCount"], 1)
        self.assertEqual(health["failureCount"], 1)
        self.assertEqual(health["lastSuccessAt"], success_at)
        self.assertEqual(health["lastSuccessDecisionId"], success_id)

    def test_window_and_failure_details_are_bounded_but_last_success_is_retained(self):
        success_id, success_at = self.settle("completed", termination="completed")
        for _ in range(24):
            self.timeout()
        health = self.coordinator.health_summary()
        self.assertEqual(health["windowSize"], 20)
        self.assertEqual(health["sampleCount"], 20)
        self.assertEqual(health["failureCount"], 20)
        self.assertEqual(health["budgetExhaustedCount"], 20)
        self.assertEqual(health["consecutiveFailures"], 20)
        self.assertEqual(len(health["recentFailures"]), 5)
        self.assertEqual(health["lastSuccessAt"], success_at)
        self.assertEqual(health["lastSuccessDecisionId"], success_id)

    def test_reads_are_diagnostic_and_maintenance_is_outside_selection_health(self):
        self.settle("failed", code="timeout", kind="maintain")
        with self.db.read() as connection:
            before = list(connection.iterdump())
        empty = self.coordinator.health_summary()
        self.assertFalse(empty["available"])
        self.assertEqual(empty["reasonCode"], "router-not-configured")
        self.assertEqual(empty["sampleCount"], 0)
        self.assertIsNone(empty["lastSuccessAt"])
        self.assertEqual(self.coordinator.health_summary(), empty)
        with self.db.read() as connection:
            self.assertEqual(list(connection.iterdump()), before)


if __name__ == "__main__":
    unittest.main()
