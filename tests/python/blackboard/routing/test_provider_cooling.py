"""Provider cooldown state and transparent pre-model rejection rerouting."""
from datetime import datetime, timedelta
import json
import unittest
from unittest.mock import patch

from hey_my_buddy.blackboard.routing import quota_routing
from hey_my_buddy.errors import BoardError
from support import BoardTestCase, FakeClock, FIXTURE_CATALOG
from blackboard.tasks.test_workflow import WorkflowTestCase, NONCE

T0 = "2026-01-01T00:00:00.000Z"


def moment(seconds):
    value = datetime.fromisoformat(T0.replace("Z", "+00:00")) + timedelta(seconds=seconds)
    return value.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def failure(*, phase="before-model", reset=None, code="quota-exceeded"):
    return {"code": code, "nativeCode": "usageLimitExceeded", "source": "synthetic/turn-error",
            "observedAt": T0, "resetsAt": reset, "rejectionPhase": phase}


class ProviderCoolingTests(BoardTestCase):
    def setUp(self):
        super().setUp()
        self.clock = FakeClock(T0)
        self.board_ = self.board(clock=self.clock)

    def reject(self, *, provider="fixture", reset=None):
        with self.board_.store.db.write() as db:
            quota_routing.record_rejection(db, "dsh", provider, failure(reset=reset), now=self.clock())
            return quota_routing.provider_state(db, "dsh", provider)

    def test_backoff_counts_every_committed_rejection_and_caps_at_one_day(self):
        for count, seconds in enumerate((1800, 3600, 7200, 14400, 28800, 57600, 86400, 86400), 1):
            row = self.reject()
            self.assertEqual(row, {"provider": "fixture", "consecutiveRejections": count,
                                   "coolingUntil": moment(seconds)})

    def test_future_reset_caps_cooling_and_past_invalid_reset_is_ignored(self):
        self.assertEqual(self.reject(reset=moment(60))["coolingUntil"], moment(60))
        self.assertEqual(self.reject(reset=moment(-1))["coolingUntil"], moment(3600))
        self.assertEqual(self.reject(reset="not-a-timestamp")["coolingUntil"], moment(7200))
        self.assertEqual(self.reject(reset=T0)["coolingUntil"], moment(14400))

    def test_manual_clear_and_expiry_keep_counts_and_allow_repeated_reads(self):
        self.reject()
        with self.board_.store.db.write() as db:
            self.assertEqual(quota_routing.redetect(db, "dsh", "fixture"),
                             {"harness": "dsh", "provider": "fixture", "cleared": True})
            self.assertFalse(quota_routing.redetect(db, "dsh", "fixture")["cleared"])
            self.assertEqual(quota_routing.provider_state(db, "dsh", "fixture"),
                             {"provider": "fixture", "consecutiveRejections": 1, "coolingUntil": None})
        self.assertEqual(self.reject()["consecutiveRejections"], 2)
        self.clock.advance(3600)
        with self.board_.store.db.read() as db:
            for _ in range(2):
                self.assertIsNone(quota_routing.cooling(db, {"harness": "dsh", "provider": "fixture"}, now=self.clock()))
            self.assertEqual(quota_routing.providers(db, "dsh")[0]["consecutiveRejections"], 2)

    def test_only_success_started_after_latest_rejection_clears_history(self):
        self.reject()
        with self.board_.store.db.write() as db:
            for started in (None, moment(-1), T0):
                quota_routing.clear_on_success(db, "dsh", "fixture", started_at=started)
                self.assertIsNotNone(quota_routing.provider_state(db, "dsh", "fixture"))
            quota_routing.clear_on_success(db, "dsh", "fixture", started_at=moment(0.001))
            self.assertIsNone(quota_routing.provider_state(db, "dsh", "fixture"))
        self.assertEqual(self.reject()["consecutiveRejections"], 1)

    def test_redetect_receipt_replay_cannot_clear_a_new_rejection(self):
        self.reject()
        params = {"requestId": "clear-one", "harness": "dsh", "provider": "fixture"}
        first = self.board_.call("quota_redetect", params)
        self.assertEqual(first, {"harness": "dsh", "provider": "fixture", "cleared": True, "duplicate": False})
        self.reject()
        replay = self.board_.call("quota_redetect", params)
        self.assertEqual(replay, {**first, "duplicate": True})
        with self.board_.store.db.read() as db:
            self.assertIsNotNone(quota_routing.cooling(db, {"harness": "dsh", "provider": "fixture"}, now=T0))
            self.assertEqual(db.execute("SELECT COUNT(*) FROM events WHERE kind='harness.quota_redetect'").fetchone()[0], 1)
        with self.assertRaises(BoardError) as conflict:
            self.board_.call("quota_redetect", {**params, "provider": "other"})
        self.assertEqual(conflict.exception.code, "CONFLICT")
        self.assertTrue(self.board_.call("quota_redetect", {**params, "requestId": "clear-two"})["cleared"])

    def test_redetect_absent_or_cleared_provider_and_validation(self):
        params = {"requestId": "clear-absent", "harness": "dsh", "provider": "fixture"}
        self.assertFalse(self.board_.call("quota_redetect", params)["cleared"])
        for invalid, code in (({"provider": "not an identifier"}, "INVALID_ARGUMENT"),
                              ({"harness": "unknown"}, "UNSUPPORTED_HARNESS"),
                              ({"other": "field"}, "INVALID_ARGUMENT")):
            with self.subTest(invalid=invalid), self.assertRaises(BoardError) as caught:
                self.board_.call("quota_redetect", {**params, **invalid})
            self.assertEqual(caught.exception.code, code)

    def test_console_clear_keeps_its_existing_write_authority(self):
        self.reject()
        params = {"requestId": "console-clear", "harness": "dsh", "provider": "fixture"}
        self.assertTrue(self.board_.console_call("quota_redetect", params)["cleared"])


class CoolingWorkflowTests(WorkflowTestCase):
    def setUp(self):
        super().setUp()
        self.clock = FakeClock(T0)
        self.identities = [{"harness": "dsh", "provider": "cooling-" + name,
                            "model": "model-" + name, "effort": "off"} for name in ("a", "b", "c")]
        self.ids = [":".join(item[key] for key in ("harness", "provider", "model", "effort"))
                    for item in self.identities]
        self.catalog_fixture({**FIXTURE_CATALOG, "providers": [
            {"harness": item["harness"], "provider": item["provider"], "efforts": ["off"],
             "models": [{"id": item["model"], "name": item["model"]}]} for item in self.identities]})
        self.board_ = self.board(clock=self.clock)
        self.board_.evaluation._clock = self.clock
        self.board_.call("model_catalog_refresh", {"requestId": "catalog"})
        self.enable(0)

    def enable(self, index):
        self.board_.console_call("user_policy_publish", {"commandId": "enable-" + str(index),
            "buddySettings": [{"buddyId": self.ids[index], "enabled": True}],
            "configuration": {"routerBuddyIds": [], "defaultRoutingMode": "review"}})

    def routed(self, *, explicit=False, locked=False):
        params = {"requestId": "routed", "hostId": "host-1", "task": "synthetic quota failover",
                  "cwd": str(self.workdir()), "executionWorkspace": {"kind": "existing", "access": "write"}}
        if explicit:
            params.update(self.identities[0])
        if locked:
            params["buddyLocked"] = True
            params.update(self.identities[0])
        run = self.board_.call("workflow_submit", params)
        self.controls[run["runId"]] = run["control"]
        return run

    def reject_turn(self, claimed, *, phase="before-model", shutdown=True, code="quota-exceeded"):
        result = {"code": "harness-error", "quotaFailure": failure(phase=phase, code=code)}
        attempt = claimed["claim"]["attempt"]
        return self.board_.call("execution_result", {"attemptId": attempt["attemptId"],
            "generation": attempt["generation"], "nonce": NONCE, "status": "failed", "result": result,
            "shutdownConfirmed": shutdown, "exitCode": 1})

    def test_rejected_a_reroutes_to_b_in_same_run_and_success_has_no_old_failure(self):
        run = self.routed()
        first_decision = run["routing"]["decisionId"]
        first = self.claim(self.board_, run_id=run["runId"])
        self.enable(1)
        settled = self.reject_turn(first)
        self.assertTrue(settled["workflow"]["autoContinued"])
        self.assertTrue(self.reject_turn(first)["duplicate"])
        with self.board_.store.db.read() as db:
            self.assertEqual(quota_routing.provider_state(db, "dsh", self.identities[0]["provider"])["consecutiveRejections"], 1)
            self.assertEqual(db.execute("SELECT state FROM agent_credentials WHERE attempt_id=?",
                                        (first["claim"]["attempt"]["attemptId"],)).fetchone()[0], "revoked")
        after = self.board_.call("workflow_get", {"runId": run["runId"]})
        self.assertEqual(after["state"], "executing")
        self.assertEqual(after["buddy"], self.identities[1])
        self.assertNotEqual(after["routing"]["decisionId"], first_decision)
        self.assertEqual(after["workspace"], run["workspace"])
        second = self.claim(self.board_, run_id=run["runId"])
        self.assertEqual(second["claim"]["turn"]["resumeMode"], "initial")
        self.assertEqual(second["claim"]["turn"]["turnIndex"], 2)
        self.assertIsNone(second["claim"]["turn"]["input"]["previousSessionId"])
        done = self.finish_turn(self.board_, second)
        self.assertEqual(done["task"]["state"], "completed")
        final = self.board_.call("workflow_get", {"runId": run["runId"], "includeAudit": True})
        self.assertEqual(final["state"], "delivered")
        self.assertNotIn("quotaFailure", final)
        self.assertEqual(final["counts"]["turns"], 2)
        self.assertEqual([t["state"] for t in final["audit"]["turns"]], ["rejected", "returned"])
        timeline = self.board_.call("macro_task_timeline", {"macroTaskId": f"run:{run['runId']}"})
        execution_spans = [span for span in timeline["spans"] if span["kind"] == "execution"]
        self.assertEqual(len(execution_spans), 2)
        by_attempt = {span["attemptId"]: span for span in execution_spans}
        first_span = by_attempt[first["claim"]["attempt"]["attemptId"]]
        second_span = by_attempt[second["claim"]["attempt"]["attemptId"]]
        self.assertEqual((first_span["state"], second_span["state"]), ("rejected", "finished"))
        self.assertTrue(all(span["shutdownConfirmed"] and span["endAt"] for span in execution_spans))
        self.assertEqual((first_span["resultStatus"], second_span["resultStatus"]), ("failed", "ok"))
        with self.board_.store.db.read() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM agent_credentials WHERE state='active'").fetchone()[0], 0)

    def test_multiple_initial_rejections_keep_unique_physical_indices(self):
        run = self.routed()
        first = self.claim(self.board_, run_id=run["runId"])
        self.enable(1)
        self.reject_turn(first)
        second = self.claim(self.board_, run_id=run["runId"])
        self.enable(2)
        self.reject_turn(second)
        third = self.claim(self.board_, run_id=run["runId"])
        self.assertEqual(third["claim"]["turn"]["turnIndex"], 3)
        self.assertEqual(third["claim"]["turn"]["resumeMode"], "initial")
        self.assertEqual(third["claim"]["turn"]["input"]["context"]["buddy"], self.identities[2])
        self.finish_turn(self.board_, third)
        with self.board_.store.db.read() as db:
            self.assertEqual([row[0] for row in db.execute("SELECT turn_index FROM workflow_turns ORDER BY rowid")], [1, 2, 3])
            self.assertEqual(db.execute("SELECT COUNT(*) FROM workflow_routes WHERE run_id=?", (run["runId"],)).fetchone()[0], 3)

    def assert_host_failure(self, *, phase="before-model", explicit=False, locked=False,
                            code="quota-exceeded", shutdown=True, cancel=False):
        run = self.routed(explicit=explicit, locked=locked)
        turn = self.claim(self.board_, run_id=run["runId"])
        if cancel:
            with self.board_.store.db.write() as db:
                db.execute("UPDATE attempts SET cancel_requested_at=? WHERE attempt_id=?",
                           (T0, turn["claim"]["attempt"]["attemptId"]))
        settled = self.reject_turn(turn, phase=phase, code=code, shutdown=shutdown)
        self.assertFalse(settled["workflow"].get("autoContinued", False))
        self.assertEqual(self.board_.call("workflow_get", {"runId": run["runId"]})["state"], "failed")
        with self.board_.store.db.read() as db:
            state = quota_routing.provider_state(db, "dsh", self.identities[0]["provider"])
            self.assertEqual(state is not None, code == "quota-exceeded")
        return run

    def test_during_model_failure_stays_with_host(self):
        self.assert_host_failure(phase="during-model")

    def test_unknown_phase_stays_with_host(self):
        self.assert_host_failure(phase="unknown")

    def test_explicit_buddy_stays_with_host_and_shows_cooling(self):
        run = self.assert_host_failure(explicit=True)
        view = self.board_.call("workflow_get", {"runId": run["runId"]})
        self.assertEqual(view["buddy"], self.identities[0])
        self.assertEqual(view["providerCooling"]["coolingUntil"], moment(1800))

    def test_locked_buddy_stays_with_host(self):
        self.assert_host_failure(locked=True)

    def test_rate_limit_is_not_a_quota_reroute_or_cooldown(self):
        self.assert_host_failure(code="rate-limited")

    def test_unconfirmed_stop_stays_with_host(self):
        self.assert_host_failure(shutdown=False)

    def test_cancelled_attempt_does_not_reroute(self):
        self.assert_host_failure(cancel=True)

    def test_continuation_requeues_exact_input_helpers_and_workspace(self):
        run = self.routed()
        first = self.claim(self.board_, run_id=run["runId"])
        self.finish_turn(self.board_, first)
        delivered = self.board_.call("workflow_get", {"runId": run["runId"]})
        self.continue_run(self.board_, delivered, input="Continue the exact Host request", note="keep this note")
        helpers = [{"runId": "helper-1", "summary": "frozen helper result", "state": "delivered"}]
        with self.board_.store.db.write() as db:
            db.execute("UPDATE workflow_continuations SET helper_outcomes_json=? WHERE run_id=? AND state='queued'",
                       (json.dumps(helpers), run["runId"]))
        rejected = self.claim(self.board_, run_id=run["runId"])
        before_input = rejected["claim"]["turn"]["input"]
        with self.board_.store.db.read() as db:
            before = dict(db.execute("SELECT * FROM workflow_continuations WHERE turn_id=?",
                                    (before_input["turnId"],)).fetchone())
        self.enable(1)
        self.reject_turn(rejected)
        with self.board_.store.db.read() as db:
            queued = dict(db.execute("SELECT * FROM workflow_continuations WHERE continuation_id=?",
                                    (before["continuation_id"],)).fetchone())
            self.assertEqual(queued, {**before, "state": "queued", "attempt_id": None,
                                      "turn_id": None, "consumed_at": None})
            self.assertEqual(db.execute("SELECT continuation_count FROM workflow_runs WHERE run_id=?",
                                        (run["runId"],)).fetchone()[0], 1)
        resumed = self.claim(self.board_, run_id=run["runId"])
        after_input = resumed["claim"]["turn"]["input"]
        self.assertEqual(after_input["executionWorkspace"], before_input["executionWorkspace"])
        self.assertEqual(after_input["context"]["continuation"], before_input["context"]["continuation"])
        self.assertEqual(after_input["context"]["helperOutcomes"], helpers)
        self.assertEqual(after_input["context"]["lastCheckpoint"]["turnId"], first["claim"]["turn"]["turnId"])
        self.assertEqual(after_input["previousSessionId"], "sess-1")
        self.assertEqual(after_input["resumeMode"], "reconstructed-new-session")
        self.finish_turn(self.board_, resumed)

    def test_all_cooling_freezes_exclusions_and_earliest_time_before_truncation(self):
        self.enable(1)
        self.enable(2)
        with self.board_.store.db.write() as db:
            for index, identity in enumerate(self.identities):
                quota_routing.record_rejection(db, "dsh", identity["provider"], failure(reset=moment(300 - index * 60)), now=T0)
        with patch("hey_my_buddy.blackboard.routing.decision.MAX_FROZEN_EXCLUSIONS", 1):
            selected = self.board_.call("selection_request", {"requestId": "all-cooling", "task": "choose"})
        view = self.board_.call("selection_get", {"decisionId": selected["decisionId"]})["decision"]
        self.assertEqual(view["status"], "needs-host")
        self.assertEqual(view["reason"], {"code": "all-candidates-cooling", "params": {"retryAt": moment(180)}})
        self.assertEqual(view["routingBasis"]["coolingCount"], 3)
        self.assertEqual(len(view["routingBasis"]["coolingBuddies"]), 1)
        self.assertEqual(view["routingBasis"]["coolingBuddies"][0]["coolingUntil"], moment(300))
        self.assertNotIn("quotaRetryBuddyIds", view["routingBasis"])


if __name__ == "__main__":
    unittest.main()
