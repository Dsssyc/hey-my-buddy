"""L4-G1 admission snapshots and Host boundaries; no claim or model calls."""
from __future__ import annotations

import json
from unittest.mock import patch

from hey_my_buddy.blackboard.routing import router
from hey_my_buddy.protocol import schemas
from hey_my_buddy.blackboard.store.db import canonical_json, sha256_text
from hey_my_buddy.errors import BoardError
from fixtures import mock_readonly
from support import BoardTestCase
from test_decision import DecisionTestCase, PROFILE, PROFILE_ID, SECOND_PROFILE, SECOND_PROFILE_ID, THIRD_PROFILE, THIRD_PROFILE_ID
from test_workflow import CONFIGURATION, WorkflowTestCase
import test_workflow_routing


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
        revision = board.call("console_snapshot", {})["tableRevision"]
        return self.publish_user_patch(board, request_id=f"settings-{revision}", command_id=f"settings-{revision}",
                                       configuration=settings)

    def legacy(self, board):
        with board.store.db.write() as connection:
            connection.execute("UPDATE meta SET value='1' WHERE key='router_configuration_version'")

    def test_multi_candidate_freezes_complete_router_and_input_hash(self):
        board = self.board()
        self.seed(board)
        self.configure(board, routingBudget="deep")
        response = self.request(board)
        view = self.audit(board, response)
        self.assertEqual(response["status"], "queued")
        identity = {key: PROFILE[key] for key in schemas.CONFIGURATION_FIELDS}
        for document in (view, view["requested"], view["input"]):
            self.assertEqual(document["routerProfileIds"], [PROFILE_ID])
            self.assertEqual(document["routerIdentities"], [identity])
            self.assertEqual(document["routingMode"], "review")
            self.assertEqual(document["budget"], router.budget("deep"))
            self.assertEqual(document["configurationRevision"], view["configurationRevision"])
            self.assertNotIn("fallback", document)
            self.assertNotIn("requestedRoutingMode", document)
        self.assertEqual(view["input"]["profile"], identity)
        self.assertEqual(view["routerProfileId"], PROFILE_ID)
        self.assertEqual(view["routerProfile"], identity)
        self.assertEqual(view["inputSha256"], sha256_text(canonical_json(view["input"])))
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
        view = self.audit(board, self.request(board, timeoutSeconds=500))
        self.assertEqual(view["routerProfileId"], PROFILE_ID)
        self.assertEqual(view["routingMode"], "fast")
        self.assertEqual(view["budget"], router.FAST_BUDGET)
        self.assertEqual(view["requested"]["timeoutSeconds"], 60)
        self.assertNotIn("executionWorkspace", view["input"])
        self.assertNotIn("evidence", view["input"])
        self.assertNotIn("file", view["input"]["outputSchema"]["properties"]["evidence"]["items"]["properties"]["kind"]["enum"])

    def test_router_choice_is_independent_of_worker_bounds_and_preferences(self):
        board = self.board()
        self.seed(board, profiles=(PROFILE, SECOND_PROFILE, THIRD_PROFILE), preferences=[
            {"profileId": PROFILE_ID, "mode": "exclude", "reason": "Worker exclusion"}])
        view = self.audit(board, self.request(board, requiredCapabilities=["effort:high"]))
        self.assertEqual(view["status"], "queued")
        self.assertEqual(view["routerProfileId"], PROFILE_ID)
        self.assertEqual(view["routerProfile"]["effort"], "off")
        self.assertEqual({item["profileId"] for item in view["input"]["profiles"]}, {SECOND_PROFILE_ID, THIRD_PROFILE_ID})

    def test_review_timeout_override_is_frozen_and_can_be_rechecked(self):
        board = self.board()
        self.seed(board)
        view = self.audit(board, self.request(board, timeoutSeconds=77))
        self.assertEqual(view["budget"], {**router.budget(), "timeoutSeconds": 77})
        with board.store.db.read() as connection:
            resolution = router.current_router(connection, frozen=view["requested"])
        self.assertEqual(resolution.profile_id, PROFILE_ID)
        self.assertIsNone(resolution.problem)
        self.assertEqual(resolution.facts["budget"]["timeoutSeconds"], 77)
        self.assertEqual(view["input"]["budget"]["timeoutSeconds"], 77)

    def test_replay_after_setting_and_candidate_changes_keeps_every_snapshot(self):
        board = self.board()
        self.seed(board)
        first = self.request(board)
        original = self.audit(board, first)
        self.configure(board, routerProfileIds=[SECOND_PROFILE_ID], defaultRoutingMode="fast", routingBudget="brief")
        self.publish_user_patch(board, request_id="exclude", command_id="exclude",
                                preferenceChanges=[{"profileId": PROFILE_ID, "mode": "exclude", "reason": "later bound"}])
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
        request = {"kind": "select", "task": "change", "requiredCapabilities": [], "timeoutSeconds": None}
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
        self.seed(board, decision_profile=None)
        first = self.request(board)
        view = self.audit(board, first)
        self.assertEqual(first["status"], "needs-host")
        self.assertIsNone(first["runId"])
        self.assertEqual(view["routerProblem"]["code"], "router-not-configured")
        self.assertTrue(view["reason"].startswith("Router 不可用："))
        self.configure(board, routerProfileIds=[PROFILE_ID])
        self.assertEqual(self.audit(board, self.request(board)), view)
        with board.store.db.read() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM tasks").fetchone()[0], 0)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM attempts").fetchone()[0], 0)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM events WHERE kind='decision.fallback'").fetchone()[0], 0)

    def test_review_ineligible_does_not_fall_back_to_available_fast_capability(self):
        board = self.board()
        self.seed(board)
        with patch("hey_my_buddy.buddy.harnesses.dsh.adapter.DshAdapter.local_read_only_check", return_value={
                "eligible": False, "systemSandbox": False,
                "reasonCode": "readonly-tools-unrestricted", "reason": "fixture tools unrestricted"}):
            view = self.audit(board, self.request(board))
        self.assertEqual(view["status"], "needs-host")
        self.assertEqual(view["routingMode"], "review")
        self.assertIsNone(view["routerProfileId"])
        self.assertEqual(view["routerProfileIds"], [PROFILE_ID])
        self.assertEqual(view["routerProblem"]["code"], "router-unavailable")
        with board.store.db.read() as connection:
            record = json.loads(connection.execute("SELECT payload_json FROM events WHERE kind='router.no_answer'").fetchone()[0])
        self.assertEqual(record["code"], "router-review-unsupported")
        self.assertIsNone(view["runId"])
        self.assertNotIn("fallback", view)

    def test_legacy_settings_become_a_specific_multi_candidate_boundary(self):
        board = self.board()
        self.seed(board)
        self.legacy(board)
        view = self.audit(board, self.request(board))
        self.assertEqual(view["status"], "needs-host")
        self.assertEqual(view["routerProblem"]["code"], "router-settings-upgrade-required")
        self.assertIsNone(view["routingMode"])
        self.assertIsNone(view["budget"])
        self.assertIsNone(view["runId"])

    def test_sole_candidate_bypasses_even_legacy_router_settings(self):
        board = self.board()
        self.seed(board)
        self.legacy(board)
        with patch("hey_my_buddy.blackboard.routing.router.current_router", side_effect=AssertionError("direct path must not resolve")):
            view = self.audit(board, self.request(board, requiredCapabilities=["input:image"]))
        self.assertEqual(view["status"], "completed")
        self.assertEqual(view["profileId"], PROFILE_ID)
        self.assertIs(view["routerCalled"], False)
        self.assertIsNone(view["input"])
        self.assertIsNone(view["routingMode"])
        self.assertIsNone(view["runId"])

    def test_zero_candidates_keep_their_own_boundary_without_router_resolution(self):
        board = self.board()
        self.seed(board)
        self.legacy(board)
        with patch("hey_my_buddy.blackboard.routing.router.current_router", side_effect=AssertionError("empty bounds must not resolve")):
            view = self.audit(board, self.request(board, requiredCapabilities=["not-present"]))
        self.assertEqual(view["status"], "needs-host")
        self.assertIn("legal candidate", view["reason"])
        self.assertEqual(view["routingBasis"]["candidateCount"], 0)
        self.assertIsNone(view["routerProblem"])
        self.assertIsNone(view["runId"])

    def test_profile_problem_orders_identity_before_availability(self):
        board = self.board()
        self.seed(board)
        with board.store.db.write() as connection:
            connection.execute("UPDATE evaluation_profiles SET provider='',enabled=0 WHERE profile_id=?", (PROFILE_ID,))
            profile, code, reason = router.profile_problem(connection, PROFILE_ID, "review")
        self.assertIsNone(profile)
        self.assertEqual(code, "router-incomplete")
        self.assertIn("身份不完整", reason)

    def test_profile_problem_keeps_each_cached_unavailability_reason(self):
        board = self.board()
        self.seed(board)
        with board.store.db.write() as connection:
            for updates, code, text in (("enabled=0", "router-unavailable", "已禁用"),
                                        ("enabled=1,available=0", "router-unavailable", "已发布目录中不可用")):
                connection.execute(f"UPDATE evaluation_profiles SET {updates} WHERE profile_id=?", (PROFILE_ID,))
                profile, actual, reason = router.profile_problem(connection, PROFILE_ID, "review")
                self.assertIsNone(profile)
                self.assertEqual(actual, code)
                self.assertIn(text, reason)
            connection.execute("UPDATE evaluation_profiles SET available=1 WHERE profile_id=?", (PROFILE_ID,))
            with patch("hey_my_buddy.blackboard.service.harness_health.read_health", return_value={"available": False, "reasonCode": "executable-missing", "reason": "fixture missing"}):
                _, code, reason = router.profile_problem(connection, PROFILE_ID, "review")
            self.assertEqual(code, "router-unavailable")
            self.assertIn("executable-missing", reason)
            self.assertIn("fixture missing", reason)
            with patch("hey_my_buddy.blackboard.evaluation.native_observations.exhausted", return_value={"nativeCode": "quota"}):
                _, code, reason = router.profile_problem(connection, PROFILE_ID, "review")
            self.assertEqual(code, "router-quota-exhausted")
            self.assertIn("额度耗尽", reason)
            with patch("hey_my_buddy.buddy.harnesses.dsh.adapter.DshAdapter.no_tool_structured", False):
                _, code, reason = router.profile_problem(connection, PROFILE_ID, "fast")
            self.assertEqual(code, "router-no-tool-unsupported")
            self.assertIn("无工具结构化入口", reason)
            _, code, reason = router.profile_problem(connection, "unpublished", "review")
            self.assertEqual(code, "router-not-published")
            self.assertIn("未发布", reason)

    def test_frozen_current_router_keeps_snapshot_after_settings_changes(self):
        board = self.board()
        self.seed(board)
        snapshot = self.audit(board, self.request(board))["requested"]
        self.configure(board, routerProfileIds=[SECOND_PROFILE_ID], defaultRoutingMode="fast", routingBudget="brief")
        with board.store.db.read() as connection:
            resolution = router.current_router(connection, frozen=snapshot)
        self.assertEqual(resolution.profile_id, PROFILE_ID)
        self.assertIsNone(resolution.problem)
        self.assertEqual(resolution.facts, {key: snapshot[key] for key in (
            "routerProfileIds", "routerIdentities", "routingMode", "routingBudget",
            "routerRetryIntervalSeconds", "configurationRevision", "budget")})
        # The claim layer, covered by the settings-drift claim test, refuses
        # execution after this change. The shared resolver reads frozen facts.

    def test_frozen_current_router_rejects_changed_identity_and_rechecks_health(self):
        board = self.board()
        self.seed(board)
        snapshot = self.audit(board, self.request(board))["requested"]
        with board.store.db.write() as connection:
            connection.execute("UPDATE evaluation_profiles SET effort='high' WHERE profile_id=?", (PROFILE_ID,))
            resolution = router.current_router(connection, frozen=snapshot)
            self.assertIsNone(resolution.profile)
            self.assertEqual(resolution.problem["code"], "router-profile-changed")
            self.assertEqual(resolution.facts["routerProfileIds"], [PROFILE_ID])
            connection.execute("UPDATE evaluation_profiles SET effort='off',enabled=0 WHERE profile_id=?", (PROFILE_ID,))
            resolution = router.current_router(connection, frozen=snapshot)
            self.assertEqual(resolution.inspections[0]["code"], "router-unavailable")

    def test_historical_fallback_is_retained_only_in_audit(self):
        board = self.board()
        self.seed(board)
        response = self.request(board)
        view = self.audit(board, response)
        historical = {**view["requested"], "requestedRoutingMode": "review", "fallback": {"from": "review", "to": "fast", "code": "old"}}
        historical.pop("configurationRevision")
        with board.store.db.write() as connection:
            connection.execute("UPDATE decision_requests SET requested_json=? WHERE decision_id=?", (canonical_json(historical), response["decisionId"]))
        view = self.audit(board, response)
        self.assertEqual(view["requested"], historical)
        self.assertNotIn("fallback", view)
        self.assertNotIn("requestedRoutingMode", view)
        self.assertEqual(view["configurationRevision"], 1)

    def test_complete_input_byte_limit_includes_snapshot_schema_and_accounts(self):
        board = self.board()
        self.seed(board)
        ordinary = self.audit(board, self.request(board))
        document = ordinary["input"]
        # The old assembly fits this ceiling; the final schema/account/snapshot
        # additions do not. Admission must check the entire actual packet.
        limit = len(canonical_json(document).encode("utf-8")) - 1
        with patch("hey_my_buddy.blackboard.routing.decision.MAX_DECISION_INPUT_BYTES", limit):
            response = self.request(board, requestId="bounded-packet")
        view = self.audit(board, response)
        self.assertEqual(view["status"], "needs-host")
        self.assertEqual(view["error"], "router-input-too-large")
        self.assertIn("nothing was truncated or sent to a model", view["reason"])
        self.assertIsNone(view["input"])
        self.assertIsNone(view["runId"])

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
    routed = test_workflow_routing.TestWorkflowRouting.routed

    def setUp(self):
        super().setUp()
        self.catalog_fixture()
        mock_readonly.install(self)

    def test_router_unavailable_opens_same_goal_attention_and_complete_host_buddy_recovers(self):
        board = self.board()
        self.seed(board, decision_profile=None)
        view = self.routed(board)
        self.assertTrue(view["awaitingHost"])
        self.assertEqual(view["activeRequest"]["kind"], "attention")
        self.assertEqual(view["counts"]["turns"], 0)
        self.assertEqual(view["routing"]["routerProblem"]["code"], "router-not-configured")
        self.assertTrue(view["activeRequest"]["summary"].startswith("Router 不可用："))
        self.assertIsNone(view["routing"]["taskId"])
        replay = self.routed(board)
        self.assertEqual(replay["activeRequest"]["requestId"], view["activeRequest"]["requestId"])
        self.assertEqual(replay["routing"], view["routing"])
        with board.store.db.read() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM workflow_requests").fetchone()[0], 1)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM workflow_routes").fetchone()[0], 1)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM tasks").fetchone()[0], 1)
            payload = json.loads(connection.execute("SELECT payload_json FROM workflow_requests").fetchone()[0])
        self.assertEqual(payload["routerProblem"]["code"], "router-not-configured")
        self.assertIn("未调用 Router", payload["attempted"])
        continued = self.continue_run(board, view, configuration=CONFIGURATION)
        self.assertEqual(continued["runId"], view["runId"])
        self.assertEqual(continued["executionConfiguration"], CONFIGURATION)
        self.assertFalse(continued["awaitingHost"])
        self.register(board)
        self.assertEqual(self.claim(board, run_id=view["runId"])["claim"]["task"]["spec"]["model"], CONFIGURATION["model"])

    def test_queued_workflow_replay_and_history_show_original_router_snapshot(self):
        board = self.board()
        self.seed(board)
        original = self.routed(board)
        self.publish_user_patch(board, request_id="new-router", command_id="new-router",
                                configuration={"routerProfileIds": [SECOND_PROFILE_ID], "defaultRoutingMode": "fast", "routingBudget": "brief"})
        replay = self.routed(board)
        self.assertEqual(replay["routing"], original["routing"])
        history = board.call("workflow_get", {"runId": original["runId"], "routingHistory": {"limit": 10}})["routingHistory"]
        self.assertEqual(history["total"], 1)
        entry = history["entries"][0]
        for key in ("routerProfileId", "routerProfile", "routingMode", "budget", "configurationRevision"):
            self.assertEqual(entry[key], original["routing"][key])
        self.assertNotIn("fallback", entry)

    def test_complete_host_buddy_bypasses_router_and_legacy_settings(self):
        board = self.board()
        self.seed(board)
        with board.store.db.write() as connection:
            connection.execute("UPDATE meta SET value='1' WHERE key='router_configuration_version'")
        with patch("hey_my_buddy.blackboard.routing.router.current_router", side_effect=AssertionError("complete buddy must not resolve")):
            view = self.submit(board)
        self.assertEqual(view["routing"]["status"], "explicit")
        self.assertEqual(view["executionConfiguration"], CONFIGURATION)
        with board.store.db.read() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM decision_requests").fetchone()[0], 0)

    def test_explicit_reroute_after_user_setting_creates_one_new_snapshot(self):
        board = self.board()
        self.seed(board, decision_profile=None)
        first = self.routed(board)
        self.publish_user_patch(board, request_id="choose-router", command_id="choose-router", configuration={"routerProfileIds": [PROFILE_ID]})
        pending = self.continue_run(board, first, reroute=True)
        board.store.workflow.prepare_continuation_workspace({"runId": pending["runId"]})
        fresh = board.call("workflow_get", {"runId": first["runId"]})
        self.assertEqual(fresh["routing"]["status"], "queued")
        self.assertNotEqual(fresh["routing"]["decisionId"], first["routing"]["decisionId"])
        self.assertEqual(fresh["routing"]["routerProfileId"], PROFILE_ID)
        self.assertTrue(self.continue_run(board, first, reroute=True)["duplicate"])
        with board.store.db.read() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM workflow_routes").fetchone()[0], 2)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM attempts").fetchone()[0], 0)


if __name__ == "__main__":
    import unittest
    unittest.main()
