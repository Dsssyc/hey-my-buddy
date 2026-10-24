"""L4-G1 admission snapshots and Host boundaries; no claim or model calls."""
from __future__ import annotations

import json
import uuid
from unittest.mock import patch

from hey_my_buddy.blackboard.routing import router, router_sequence
from hey_my_buddy.protocol import schemas
from hey_my_buddy.blackboard.store.db import canonical_json
from hey_my_buddy.errors import BoardError
from protocol.fixtures import mock_readonly
from support import BoardTestCase
from blackboard.routing.test_decision import DecisionTestCase, CATALOG_BUDDY, BUDDY_ID, SECOND_BUDDY, SECOND_BUDDY_ID, THIRD_BUDDY, THIRD_BUDDY_ID
from blackboard.tasks.test_workflow import BUDDY, WorkflowTestCase
import blackboard.tasks.test_workflow_routing


class SingleRouterRequestTests(BoardTestCase):
    seed = DecisionTestCase.seed
    publish_user_patch = DecisionTestCase.publish_user_patch

    def setUp(self):
        super().setUp()
        self.catalog_fixture()
        mock_readonly.install(self)

    def request(self, board, **extra):
        return board.call("selection_request", {"requestId": "selection", "task": "bounded change", **extra})

    def audit(self, board, response):
        return board.call("selection_get", {"decisionId": response["decisionId"], "includeAudit": True})["decision"]

    def configure(self, board, **settings):
        return self.publish_user_patch(board, request_id="settings-" + str(uuid.uuid4()), command_id="settings-" + str(uuid.uuid4()),
                                       configuration=settings)

    def test_multi_candidate_freezes_complete_router_and_input_hash(self):
        board = self.board()
        self.seed(board)
        self.configure(board, routingBudget="deep")
        response = self.request(board)
        view = self.audit(board, response)
        self.assertEqual(response["status"], "queued")
        identity = {key: CATALOG_BUDDY[key] for key in schemas.BUDDY_FIELDS}
        for document in (view, view["input"]):
            self.assertEqual(document["routerBuddyIds"], [BUDDY_ID])
            self.assertEqual(document["routerIdentities"], [identity])
            self.assertEqual(document["routingMode"], "review")
            self.assertEqual(document["budget"], router.budget("deep"))
            self.assertNotIn("fallback", document)
            self.assertNotIn("requestedRoutingMode", document)
        self.assertEqual(view["input"]["buddy"], identity)
        self.assertNotIn("routerBuddyIds", view["requested"])
        self.assertNotIn("budget", view["requested"])
        self.assertEqual(view["routerBuddyId"], BUDDY_ID)
        self.assertEqual(view["routerBuddy"], identity)
        self.assertNotIn("inputSha256", view)
        self.readonly_start.assert_not_called()
        with board.store.db.read() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM tasks").fetchone()[0], 1)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM attempts").fetchone()[0], 0)
            event = json.loads(connection.execute("SELECT payload_json FROM events WHERE kind='decision.requested'").fetchone()[0])
        self.assertEqual(event["routerIdentities"], [identity])
        self.assertEqual(event["budget"], router.budget("deep"))

    def test_fast_uses_same_router_and_sixty_second_repo_free_input(self):
        board = self.board()
        self.seed(board)
        self.configure(board, defaultRoutingMode="fast", routingBudget="deep")
        view = self.audit(board, self.request(board))
        self.assertEqual(view["routerBuddyId"], BUDDY_ID)
        self.assertEqual(view["routingMode"], "fast")
        self.assertEqual(view["budget"], router.FAST_BUDGET)
        self.assertNotIn("routerBudgetSeconds", view["requested"])
        self.assertEqual(view["input"]["budget"]["timeoutSeconds"], 60)
        self.assertNotIn("executionWorkspace", view["input"])
        self.assertNotIn("evidence", view["input"])
        self.assertNotIn("file", view["input"]["outputSchema"]["properties"]["evidence"]["items"]["properties"]["kind"]["enum"])

    def test_router_choice_is_independent_of_worker_bounds_and_preferences(self):
        board = self.board()
        self.seed(board, buddies=(CATALOG_BUDDY, SECOND_BUDDY, THIRD_BUDDY), preferences=[
            {"buddyId": BUDDY_ID, "mode": "exclude", "note": "Worker exclusion"}])
        view = self.audit(board, self.request(board, requiredCapabilities=["effort:high"]))
        self.assertEqual(view["status"], "queued")
        self.assertEqual(view["routerBuddyId"], BUDDY_ID)
        self.assertEqual(view["routerBuddy"]["effort"], "off")
        self.assertEqual({item["buddyId"] for item in view["input"]["buddies"]}, {SECOND_BUDDY_ID, THIRD_BUDDY_ID})

    def test_review_settings_budget_is_frozen_and_can_be_rechecked(self):
        board = self.board()
        self.seed(board)
        view = self.audit(board, self.request(board))
        self.assertEqual(view["budget"], router.budget())
        with board.store.db.read() as connection:
            snapshot = router_sequence.request_snapshot(connection, view["decisionId"])["facts"]
            resolution = router.current_router(connection, frozen=snapshot)
        self.assertEqual(resolution.buddy_id, BUDDY_ID)
        self.assertIsNone(resolution.problem)
        self.assertEqual(resolution.facts["budget"]["timeoutSeconds"], 300)
        self.assertEqual(view["input"]["budget"]["timeoutSeconds"], 300)

    def test_replay_after_setting_and_candidate_changes_keeps_every_snapshot(self):
        board = self.board()
        self.seed(board)
        first = self.request(board)
        original = self.audit(board, first)
        self.configure(board, routerBuddyIds=[SECOND_BUDDY_ID], defaultRoutingMode="fast", routingBudget="brief")
        self.publish_user_patch(board, request_id="exclude", command_id="exclude",
                                preferenceChanges=[{"buddyId": BUDDY_ID, "mode": "exclude", "note": "later bound"}])
        with patch("hey_my_buddy.blackboard.routing.router.current_router", side_effect=AssertionError("replay must not resolve")):
            replay = self.request(board)
        self.assertTrue(replay["duplicate"])
        self.assertEqual(replay["decisionId"], first["decisionId"])
        self.assertEqual(self.audit(board, replay), original)
        with board.store.db.read() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM decision_requests").fetchone()[0], 1)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM tasks").fetchone()[0], 1)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM events WHERE kind='decision.requested'").fetchone()[0], 1)

    def test_internal_create_does_not_mutate_its_input_or_conflict_on_replay(self):
        board = self.board()
        self.seed(board)
        request = {"kind": "select", "task": "change", "requiredCapabilities": []}
        before = dict(request)
        first = board.store.decisions._create("internal", request)
        self.assertEqual(request, before)
        replay = board.store.decisions._create("internal", request)
        self.assertTrue(replay["duplicate"])
        self.assertEqual(first["decisionId"], replay["decisionId"])
        with self.assertRaises(BoardError) as raised:
            board.store.decisions._create("internal", {**request, "task": "changed"})
        self.assertEqual(raised.exception.code, "CONFLICT")

    def test_unconfigured_multi_candidate_is_durable_without_router_task(self):
        board = self.board()
        self.seed(board, decision_buddy=None)
        first = self.request(board)
        view = self.audit(board, first)
        self.assertEqual(first["status"], "needs-host")
        self.assertIsNone(first["runId"])
        self.assertEqual(view["routerProblem"]["code"], "router-not-configured")
        self.assertEqual(view["reason"], {"code": "router-not-configured", "params": {}})
        self.configure(board, routerBuddyIds=[BUDDY_ID])
        self.assertEqual(self.audit(board, self.request(board)), view)
        with board.store.db.read() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM tasks").fetchone()[0], 0)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM attempts").fetchone()[0], 0)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM events WHERE kind='decision.fallback'").fetchone()[0], 0)

    def test_review_ineligible_does_not_fall_back_to_available_fast_capability(self):
        board = self.board()
        self.seed(board)
        with patch.dict('hey_my_buddy.protocol.capabilities.PERMISSION_LEVELS', {'dsh': {'none': 'enforced', 'write': 'enforced', 'full': 'enforced'}}):
            view = self.audit(board, self.request(board))
        self.assertEqual(view["status"], "needs-host")
        self.assertEqual(view["routingMode"], "review")
        self.assertIsNone(view["routerBuddyId"])
        self.assertEqual(view["routerBuddyIds"], [BUDDY_ID])
        self.assertEqual(view["routerProblem"]["code"], "router-all-unavailable")
        with board.store.db.read() as connection:
            record = json.loads(connection.execute("SELECT payload_json FROM events WHERE kind='router.no_answer'").fetchone()[0])
        self.assertEqual(record["code"], "router-review-unsupported")
        self.assertIsNone(view["runId"])
        self.assertNotIn("fallback", view)

    def test_zero_candidates_keep_their_own_boundary_without_router_resolution(self):
        board = self.board()
        self.seed(board)
        with patch("hey_my_buddy.blackboard.routing.router.current_router", side_effect=AssertionError("empty bounds must not resolve")):
            view = self.audit(board, self.request(board, requiredCapabilities=["not-present"]))
        self.assertEqual(view["status"], "needs-host")
        self.assertEqual(view["reason"], {"code": "no-legal-candidate", "params": {}})
        self.assertEqual(view["routingBasis"]["candidateCount"], 0)
        self.assertIsNone(view["routerProblem"])
        self.assertIsNone(view["runId"])

    def test_buddy_problem_keeps_each_cached_unavailability_reason(self):
        board = self.board()
        self.seed(board)
        with board.store.db.write() as connection:
            for updates, code in (("enabled=0", "router-disabled"),
                                  ("enabled=1,available=0", "router-catalog-unavailable")):
                connection.execute(f"UPDATE buddies SET {updates} WHERE buddy_id=?", (BUDDY_ID,))
                buddy, actual, _ = router.buddy_problem(connection, BUDDY_ID, "review")
                self.assertIsNone(buddy)
                self.assertEqual(actual, code)
            cause = {"code": "catalog.model-unavailable", "params": {"harness": "dsh"}}
            connection.execute("UPDATE buddies SET unavailable_code=?,unavailable_params_json=? WHERE buddy_id=?",
                               (cause["code"], canonical_json(cause["params"]), BUDDY_ID))
            _, code, facts = router.buddy_problem(connection, BUDDY_ID, "review")
            self.assertEqual(code, "router-catalog-unavailable")
            self.assertEqual(facts, {"cause": cause})
            resolution = router.current_router(connection)
            self.assertEqual(resolution.inspections[0]["cause"], cause)
            connection.execute("UPDATE buddies SET available=1 WHERE buddy_id=?", (BUDDY_ID,))
            with patch("hey_my_buddy.blackboard.service.harness_health.read_health", return_value={"status": "missing", "reason": {"code": "harness.not-found", "params": {"operation": "probe"}}}):
                _, code, facts = router.buddy_problem(connection, BUDDY_ID, "review")
            self.assertEqual(code, "router-harness-unavailable")
            self.assertEqual(facts, {"cause": {"code": "harness.not-found", "params": {"operation": "probe"}}})
            with patch("hey_my_buddy.blackboard.routing.quota_routing.cooling", return_value={"coolingUntil": "2026-01-01T00:30:00.000Z"}):
                _, code, _ = router.buddy_problem(connection, BUDDY_ID, "review")
            self.assertEqual(code, "router-quota-exhausted")
            with patch.dict('hey_my_buddy.protocol.capabilities.PERMISSION_LEVELS', {'dsh': {'read': 'enforced', 'write': 'enforced', 'full': 'enforced'}}):
                _, code, _ = router.buddy_problem(connection, BUDDY_ID, "fast")
            self.assertEqual(code, "router-no-tool-unsupported")
            with patch.dict('hey_my_buddy.protocol.capabilities.PERMISSION_LEVELS', {'dsh': {'none': 'enforced', 'write': 'enforced', 'full': 'enforced'}}):
                _, code, facts = router.buddy_problem(connection, BUDDY_ID, "review")
            self.assertEqual(code, "router-review-unsupported")
            self.assertEqual(facts, {})

    def test_frozen_current_router_keeps_snapshot_after_settings_changes(self):
        board = self.board()
        self.seed(board)
        decision = self.audit(board, self.request(board))
        self.configure(board, routerBuddyIds=[SECOND_BUDDY_ID], defaultRoutingMode="fast", routingBudget="brief")
        with board.store.db.read() as connection:
            snapshot = router_sequence.request_snapshot(connection, decision["decisionId"])["facts"]
            resolution = router.current_router(connection, frozen=snapshot)
        self.assertEqual(resolution.buddy_id, BUDDY_ID)
        self.assertIsNone(resolution.problem)
        self.assertEqual(resolution.facts, {key: snapshot[key] for key in (
            "routerBuddyIds", "routerIdentities", "routingMode", "routingBudget",
            "routerRetryIntervalSeconds", "budget")})
        # The claim layer, covered by the settings-drift claim test, refuses
        # execution after this change. The shared resolver reads frozen facts.

    def test_frozen_current_router_keeps_identity_and_rechecks_health(self):
        board = self.board()
        self.seed(board)
        decision = self.audit(board, self.request(board))
        with board.store.db.write() as connection:
            snapshot = router_sequence.request_snapshot(connection, decision["decisionId"])["facts"]
            connection.execute("UPDATE buddies SET effort='high' WHERE buddy_id=?", (BUDDY_ID,))
            resolution = router.current_router(connection, frozen=snapshot)
            self.assertEqual(resolution.buddy["effort"], "off")
            self.assertIsNone(resolution.problem)
            self.assertEqual(resolution.facts["routerBuddyIds"], [BUDDY_ID])
            connection.execute("UPDATE buddies SET effort='off',enabled=0 WHERE buddy_id=?", (BUDDY_ID,))
            resolution = router.current_router(connection, frozen=snapshot)
            self.assertEqual(resolution.inspections[0]["code"], "router-disabled")

    def test_historical_fallback_is_retained_only_in_audit(self):
        board = self.board()
        self.seed(board)
        response = self.request(board)
        view = self.audit(board, response)
        historical = {**view["requested"], "requestedRoutingMode": "review", "fallback": {"from": "review", "to": "fast", "code": "old"}}
        with board.store.db.write() as connection:
            connection.execute("UPDATE decision_requests SET requested_json=? WHERE decision_id=?", (canonical_json(historical), response["decisionId"]))
        view = self.audit(board, response)
        self.assertEqual(view["requested"], historical)
        self.assertNotIn("fallback", view)
        self.assertNotIn("requestedRoutingMode", view)

    def test_selection_request_accepts_task_beyond_old_router_limit(self):
        board = self.board()
        self.seed(board)
        response = board.call("selection_request", {"requestId": "large-selection",
                                                    "task": "x" * 9000})
        view = self.audit(board, response)
        self.assertEqual(view["status"], "queued")
        self.assertEqual(view["input"]["task"], "x" * 9000)
        self.assertIsNotNone(view["runId"])

    def test_selection_request_uses_shared_one_mib_task_bound(self):
        board = self.board()
        self.seed(board)
        task = "x" * schemas.MAX_TASK_BYTES
        accepted = board.call("selection_request", {"requestId": "exact-one-mib", "task": task})
        self.assertEqual(accepted["status"], "queued")
        self.assertEqual(self.audit(board, accepted)["input"]["task"], task)
        with self.assertRaises(BoardError) as raised:
            board.call("selection_request", {"requestId": "over-one-mib", "task": task + "x"})
        self.assertEqual(raised.exception.code, "INVALID_ARGUMENT")

    def test_admission_failure_rolls_back_request_task_and_event_together(self):
        board = self.board()
        self.seed(board)
        with patch.object(board.store.decisions, "_append_event", side_effect=RuntimeError("transaction fault")):
            with self.assertRaisesRegex(RuntimeError, "transaction fault"):
                board.store.decisions.request_select({"requestId": "selection", "task": "bounded change"})
        with board.store.db.read() as connection:
            for table in ("decision_requests", "evaluation_decisions", "tasks"):
                self.assertEqual(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0], 0)
        self.assertEqual(self.request(board)["status"], "queued")


class SingleRouterWorkflowRequestTests(WorkflowTestCase):
    seed = DecisionTestCase.seed
    publish_user_patch = DecisionTestCase.publish_user_patch
    routed = blackboard.tasks.test_workflow_routing.TestWorkflowRouting.routed

    def setUp(self):
        super().setUp()
        self.catalog_fixture()
        mock_readonly.install(self)

    def test_router_unavailable_opens_same_micro_task_attention_and_complete_host_buddy_recovers(self):
        board = self.board()
        self.seed(board, decision_buddy=None)
        view = self.routed(board)
        self.assertTrue(view["awaitingHost"])
        self.assertEqual(view["activeRequest"]["kind"], "attention")
        self.assertEqual(view["counts"]["turns"], 0)
        self.assertEqual(view["routing"]["routerProblem"]["code"], "router-not-configured")
        self.assertIsNone(view["activeRequest"]["summary"])
        self.assertEqual(view["activeRequest"]["reason"], {"code": "router-not-configured", "params": {}})
        self.assertIsNone(view["routing"]["taskId"])
        replay = self.routed(board)
        self.assertEqual(replay["activeRequest"]["requestId"], view["activeRequest"]["requestId"])
        self.assertEqual(replay["routing"], view["routing"])
        with board.store.db.read() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM workflow_requests").fetchone()[0], 1)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM workflow_routes").fetchone()[0], 1)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM tasks").fetchone()[0], 1)
            request_row = connection.execute("SELECT decision_id,payload_json FROM workflow_requests").fetchone()
        self.assertEqual(request_row["decision_id"], view["routing"]["decisionId"])
        self.assertEqual(json.loads(request_row["payload_json"]), {"source": "routing"})
        continued = self.continue_run(board, view, buddy=BUDDY)
        self.assertEqual(continued["runId"], view["runId"])
        self.assertEqual(continued["buddy"], BUDDY)
        self.assertFalse(continued["awaitingHost"])
        self.register(board)
        self.assertEqual(self.claim(board, run_id=view["runId"])["claim"]["task"]["spec"]["model"], BUDDY["model"])

    def test_queued_workflow_replay_and_history_show_original_router_snapshot(self):
        board = self.board()
        self.seed(board)
        original = self.routed(board)
        self.publish_user_patch(board, request_id="new-router", command_id="new-router",
                                configuration={"routerBuddyIds": [SECOND_BUDDY_ID], "defaultRoutingMode": "fast", "routingBudget": "brief"})
        replay = self.routed(board)
        self.assertEqual(replay["routing"], original["routing"])
        history = board.call("workflow_get", {"runId": original["runId"], "routingHistory": {"limit": 10}})["routingHistory"]
        self.assertEqual(history["total"], 1)
        entry = history["entries"][0]
        for key in ("routerBuddyId", "routerBuddy", "routingMode", "budget"):
            self.assertEqual(entry[key], original["routing"][key])
        self.assertNotIn("fallback", entry)

    def test_complete_host_buddy_bypasses_router_settings(self):
        board = self.board()
        self.seed(board)
        with patch("hey_my_buddy.blackboard.routing.router.current_router", side_effect=AssertionError("complete buddy must not resolve")):
            view = self.submit(board)
        self.assertEqual(view["routing"]["status"], "explicit")
        self.assertEqual(view["buddy"], BUDDY)
        with board.store.db.read() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM decision_requests").fetchone()[0], 0)

    def test_explicit_reroute_after_user_setting_creates_one_new_snapshot(self):
        board = self.board()
        self.seed(board, decision_buddy=None)
        first = self.routed(board)
        self.publish_user_patch(board, request_id="choose-router", command_id="choose-router", configuration={"routerBuddyIds": [BUDDY_ID]})
        pending = self.continue_run(board, first, reroute=True)
        board.store.workflow.prepare_continuation_workspace({"runId": pending["runId"]})
        fresh = board.call("workflow_get", {"runId": first["runId"]})
        self.assertEqual(fresh["routing"]["status"], "queued")
        self.assertNotEqual(fresh["routing"]["decisionId"], first["routing"]["decisionId"])
        self.assertEqual(fresh["routing"]["routerBuddyId"], BUDDY_ID)
        self.assertTrue(self.continue_run(board, first, reroute=True)["duplicate"])
        with board.store.db.read() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM workflow_routes").fetchone()[0], 2)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM attempts").fetchone()[0], 0)


if __name__ == "__main__":
    import unittest
    unittest.main()
