"""Current selected-buddy bounds through real claim and publication transactions."""
from admission import admit_execution
import json
from unittest.mock import patch

from hey_my_buddy.errors import BoardError

from blackboard.routing.test_router_dispatch import RouterDispatchTestCase, A, B, C
import blackboard.routing.test_router_failover_integration as _failover
from blackboard.routing.test_decision import DecisionTestCase, BUDDY_ID
from blackboard.routing.test_single_router_claims import stopped_answer


class SelectionPublicationTests(RouterDispatchTestCase):
    start = _failover.FailoverTests.start
    answer = _failover.FailoverTests.answer
    report = _failover.FailoverTests.report
    current_task = _failover.FailoverTests.current_task
    next_claim = _failover.FailoverTests.next_claim

    def claim(self, suffix="1", *, task_id=None):
        result = super().claim(suffix, task_id=task_id)
        if result["claim"]:
            from blackboard.routing.test_router_dispatch import NONCE
            result["claim"]["nonce"] = NONCE + suffix
        return result

    def invalidate(self, buddy, kind):
        if kind == "excluded":
            self.board_.console_call("user_policy_publish", {
                "commandId": "exclude-" + buddy,
                "preferenceChanges": [{"buddyId": buddy, "mode": "exclude", "reason": "User exclusion"}],
            })
        elif kind == "quota":
            from hey_my_buddy.blackboard.routing import quota_routing
            with self.board_.store.db.write() as db:
                quota_routing.record(db, "dsh", {"provider": "fixture", "scope": {"limitId": "bravo"},
                                    "balanceZero": True, "observedAt": self.clock.value, "source": "native"},
                                    now=self.clock.value)
        else:
            with self.board_.store.db.write() as db:
                if kind == "capability":
                    db.execute("UPDATE buddies SET capabilities_json='[]' WHERE buddy_id=?", (buddy,))
                else:
                    field = "enabled" if kind == "disabled" else "available"
                    db.execute(f"UPDATE buddies SET {field}=0 WHERE buddy_id=?", (buddy,))

    def assert_retry(self, kind, reason):
        request, first = self.start(three=True, micro_task=True)
        old_dispatch = self.dispatch_of(first["task"]["taskId"])
        original_snapshot = self.snapshot_of(request["decisionId"])
        self.invalidate(B, kind)
        response = self.report(first, self.answer(first, buddy_id=B))
        self.assertIn("outcome", response["decision"], response["decision"])
        self.assertEqual(response["decision"]["outcome"], "rerouted", response)
        self.assertEqual(response["decision"]["status"], "queued")
        self.assertEqual(self.dispatch_of(first["task"]["taskId"]), old_dispatch)
        self.assertEqual(self.snapshot_of(request["decisionId"]), original_snapshot)
        second = self.next_claim(request)
        self.assertEqual([p["buddyId"] for p in second["decisionInput"]["buddies"]], [A, C])
        self.assertNotEqual(second["task"]["taskId"], first["task"]["taskId"])
        with self.board_.store.db.read() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM workflow_runs").fetchone()[0], 1)
            self.assertEqual(db.execute("SELECT state FROM workflow_routes").fetchone()[0], "pending")
            events = [json.loads(r[0]) for r in db.execute("SELECT payload_json FROM events WHERE kind='decision.selection_rerouted'")]
            self.assertEqual(len(events), 1)
            self.assertNotIn("reason", events[0])
        self.report(second, self.answer(second, buddy_id=C))
        self.assertEqual(self.decision_row(request["decisionId"])["status"], "completed")
        self.assertEqual(self.decision_row(request["decisionId"])["buddyId"], C)

    def test_disabled_selected_buddy_reroutes_once(self):
        self.assert_retry("disabled", "disabled")

    def test_unavailable_selected_buddy_reroutes_once(self):
        self.assert_retry("unavailable", "unavailable")

    def test_excluded_selected_buddy_reroutes_once(self):
        self.assert_retry("excluded", "excluded by the user")

    def test_capability_loss_reroutes_once(self):
        self.assert_retry("capability", "missing a required task capability")

    def test_quota_loss_reroutes_without_adopting_exhausted_buddy(self):
        self.assert_retry("quota", "quota limits")

    def test_invalid_selection_with_unconfirmed_outer_stop_does_not_reroute(self):
        request, first = self.start(three=True, micro_task=True)
        self.invalidate(B, "disabled")
        response = self.report(first, self.answer(first, buddy_id=B), shutdown=False)
        self.assertEqual(response["decision"]["outcome"], "changed", response)
        self.assertEqual(response["decision"]["status"], "needs-host")
        self.assertEqual(self.current_task(request["decisionId"]), first["task"]["taskId"])
        with self.board_.store.db.read() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM router_dispatches").fetchone()[0], 1)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM events WHERE kind='decision.selection_rerouted'").fetchone()[0], 0)
            self.assertEqual(db.execute("SELECT shutdown_confirmed FROM attempts WHERE attempt_id=?",
                                        (first["attempt"]["attemptId"],)).fetchone()[0], 0)

    def test_selection_retry_dispatch_limit_retains_its_size_facts(self):
        request, first = self.start(three=True, micro_task=True)
        self.invalidate(B, "disabled")
        coordinator = self.board_.store.decisions
        with patch.object(coordinator, "_queue_router_dispatch", side_effect=BoardError(
                "router-input-too-large", "dispatch preparation diagnostic", bytes=200, limit=100)):
            response = self.report(first, self.answer(first, buddy_id=B))
        self.assertTrue(response["committed"])
        view = self.decision_row(request["decisionId"])
        self.assertEqual(view["reason"], {"code": "router-input-too-large", "params": {"bytes": 200, "limit": 100}})
        self.assertIsNone(view["note"])

    def test_second_invalid_selection_reaches_host_with_reason(self):
        request, first = self.start(three=True, micro_task=True)
        self.invalidate(B, "disabled")
        response = self.report(first, self.answer(first, buddy_id=B))
        self.assertIn("outcome", response["decision"], response["decision"])
        second = self.next_claim(request)
        self.invalidate(C, "excluded")
        response = self.report(second, self.answer(second, buddy_id=C))
        self.assertEqual(response["decision"]["status"], "needs-host", response)
        view = self.decision_row(request["decisionId"])
        self.assertEqual(view["reason"], {"code": "router-out-of-bounds", "params": {"bound": "excluded"}})
        self.assertEqual(view["routingBoundary"]["kind"], "routing-changed")
        self.assertEqual(view["routingBoundary"]["reason"], view["reason"])
        self.assertEqual([p["buddyId"] for p in view["routingBoundary"]["candidates"]], [A, C])
        self.assertEqual([t["taskId"] for t in view["routerTrials"] if t["attemptId"]], [second["task"]["taskId"]])
        with self.board_.store.db.read() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM router_dispatches").fetchone()[0], 2)
            self.assertEqual(db.execute("SELECT state FROM workflow_runs").fetchone()[0], "awaiting-host")

    def test_legal_answer_publishes_without_rerouting(self):
        request, first = self.start(three=True)
        self.report(first, self.answer(first, buddy_id=B))
        view = self.decision_row(request["decisionId"])
        self.assertEqual((view["status"], view["buddyId"]), ("completed", B))
        self.assertEqual(len(self.tasks()), 1)

    def test_sole_candidate_after_retry_does_not_attribute_old_router_answer(self):
        request, first = self.start(three=True)
        self.invalidate(B, "disabled")
        self.invalidate(C, "disabled")
        response = self.report(first, self.answer(first, buddy_id=B))
        self.assertEqual(response["decision"]["status"], "completed")
        view = self.decision_row(request["decisionId"])
        self.assertEqual(view["buddyId"], A)
        self.assertIsNone(view["runId"])
        self.assertFalse(view["routerCalled"])
        self.assertIsNone(self.board_.store.decisions.health_summary()["lastSuccessDecisionId"])
        with self.board_.store.db.read() as db:
            attempt = db.execute("SELECT result_json,shutdown_confirmed FROM attempts WHERE attempt_id=?", (first["attempt"]["attemptId"],)).fetchone()
            self.assertEqual(json.loads(attempt[0])["result"]["decision"]["buddyId"], B)
            self.assertEqual(attempt[1], 1)

    def test_router_list_and_mode_change_during_call_apply_to_next_route(self):
        request, first = self.start(three=True)
        self.set_settings(ids=[C], mode="review", budget="deep")
        self.report(first, self.answer(first, buddy_id=B))
        view = self.decision_row(request["decisionId"])
        self.assertEqual(view["status"], "completed")
        self.assertEqual(view["routerBuddyIds"], [A, B, C])
        self.assertEqual(view["routingMode"], "fast")


class CatalogAndPreferencePublicationTests(DecisionTestCase):
    def begin(self, board):
        self.seed(board)
        request = self.request(board)
        pass  # Admission is owned by the service.
        claim = admit_execution(board.client(), "n" * 32, task_id=request["runId"])["claim"]
        self.assertIsNotNone(claim)
        return request, claim

    def finish(self, board, request, claim):
        board.call('execution_result', {'attemptId': claim['attempt']['attemptId'], 'generation': claim['attempt']['generation'], 'nonce': 'n' * 32, 'status': 'ok', 'shutdownConfirmed': True, 'result': stopped_answer(claim)})
        view = self.decision(board, request["decisionId"])
        self.assertEqual((view["status"], view["buddyId"]), ("completed", BUDDY_ID))
        self.assertEqual(view["input"], claim["decisionInput"])

    def test_catalog_refresh_during_call_keeps_frozen_input_and_publishes(self):
        board = self.board()
        request, claim = self.begin(board)
        board.call("model_catalog_refresh", {"requestId": "reread-during-call"})
        self.finish(board, request, claim)

    def test_unrelated_preference_during_call_keeps_frozen_input_and_publishes(self):
        board = self.board()
        request, claim = self.begin(board)
        self.publish_user_patch(board, request_id="prefer", command_id="prefer",
            preferenceChanges=[{"buddyId": BUDDY_ID, "mode": "prefer", "reason": "Soft preference"}])
        self.finish(board, request, claim)
