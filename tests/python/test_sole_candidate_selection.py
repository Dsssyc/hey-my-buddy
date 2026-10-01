"""The sole legal candidate is selected by the program without a Router call.

Freezing the candidate set happens before any Router resolution: when exactly
one enabled, available, capability-matching, complete coding profile satisfies
the hard bounds, the program adopts it directly, records the precise reason
``唯一合法候选，未调用 Router`` and creates no Router task, usage or stop
evidence — none of which happened. Multiple candidates keep the ordinary Router
path, and zero candidates keep the Host boundary.
"""
from __future__ import annotations

import json

from buddy.decision import SOLE_CANDIDATE_REASON
from test_decision import (
    DecisionTestCase,
    PROFILE,
    PROFILE_ID,
    SECOND_PROFILE,
    SECOND_PROFILE_ID,
)
from test_workflow import CONFIGURATION, WorkflowTestCase


class SoleCandidateSelectionTests(DecisionTestCase):
    def test_sole_candidate_is_selected_by_the_program_without_a_router_call(self):
        board = self.board()
        self.seed(board)
        request = board.call("selection_request", {
            "requestId": "pick-sole", "task": "needs image input",
            "requiredCapabilities": ["input:image"],
        })
        self.assertEqual(request["status"], "completed")
        self.assertIsNone(request["runId"])
        decision = self.decision(board, request["decisionId"])
        self.assertEqual(decision["profileId"], PROFILE_ID)
        self.assertEqual(decision["reason"], SOLE_CANDIDATE_REASON)
        self.assertEqual(decision["selectedProfile"]["profileId"], PROFILE_ID)
        self.assertEqual({key: decision["selectedProfile"][key] for key in CONFIGURATION},
                         {key: PROFILE[key] for key in CONFIGURATION})
        # No model ran, so no model evidence exists: nothing is fabricated.
        self.assertIsNone(decision["input"])
        self.assertIsNone(decision["runId"])
        self.assertEqual(decision["evidence"], [])
        for absent in ("usage", "stopEvidence", "nativeIdentity", "inputVerification", "budget"):
            self.assertIsNone(decision[absent], absent)
        self.assertIs(decision["routerCalled"], False)
        self.assertEqual(decision["routingBasis"],
                         {"candidateCount": 1, "excludedCount": 0, "excludedProfiles": []})
        self.assertEqual(decision["output"]["programSelection"]["code"], "single-candidate")
        self.assertEqual(decision["policyCheck"],
                         {"hardConstraints": {}, "userPreference": "none"})
        self.readonly_start.assert_not_called()
        self.assertEqual(board.call("task_list", {"limit": 10})["runs"], [])
        with board.store.db.read() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM attempts").fetchone()[0], 0)
            kinds = [row[0] for row in connection.execute(
                "SELECT kind FROM events WHERE json_extract(payload_json,'$.decisionId')=? ORDER BY seq",
                (request["decisionId"],))]
        self.assertEqual(kinds, ["decision.requested", "decision.completed"])

    def test_sole_candidate_does_not_depend_on_router_availability(self):
        board = self.board()
        self.seed(board, decision_profile=None)
        sole = board.call("selection_request", {
            "requestId": "pick-sole-no-router", "task": "needs image input",
            "requiredCapabilities": ["input:image"],
        })
        self.assertEqual(sole["status"], "completed")
        self.assertEqual(self.decision(board, sole["decisionId"])["reason"], SOLE_CANDIDATE_REASON)
        # The same table without a Router still opens the Host boundary when the
        # program has more than one candidate to compare.
        multi = self.request(board, request_id="pick-multi-no-router")
        self.assertEqual(multi["status"], "needs-host")
        self.assertIn("尚未设置 Router", self.decision(board, multi["decisionId"])["reason"])
        self.assertEqual(self.decision(board, multi["decisionId"])["routerProblem"]["code"], "router-not-configured")

    def test_selection_source_reads_only_recorded_frozen_facts(self):
        from buddy.router import selection_source
        # The program's direct selection keeps its recorded marker.
        self.assertEqual(selection_source({"routerCalled": False}), "single-candidate")
        # A frozen empty candidate set names the zero-candidate Host boundary.
        self.assertEqual(selection_source({"routingBasis": {"candidateCount": 0}}), "no-candidate")
        # Router-path records and records predating the frozen basis are never
        # relabeled from later state.
        self.assertEqual(selection_source({"routingBasis": {"candidateCount": 2}}), "model-selection")
        self.assertEqual(selection_source({}), "model-selection")


    def test_sole_candidate_records_bounds_capabilities_and_preference_check(self):
        board = self.board()
        self.seed(board, preferences=[{"profileId": PROFILE_ID, "mode": "prefer", "reason": "prefer flash"}])
        request = board.store.decisions._create(
            "sole-bounds",
            {"kind": "select", "task": "bounded selection",
             "constraints": {"adapter": "dsh", "effort": "off"},
             "requiredCapabilities": ["execution:dsh"],
             "timeoutSeconds": 60},
        )
        self.assertEqual(request["status"], "completed")
        decision = self.decision(board, request["decisionId"])
        self.assertEqual(decision["requested"]["constraints"], {"adapter": "dsh", "effort": "off"})
        self.assertEqual(decision["requested"]["requiredCapabilities"], ["execution:dsh"])
        compact = board.call("selection_get", {"decisionId": request["decisionId"]})["decision"]
        self.assertEqual(compact["constraints"], {"adapter": "dsh", "effort": "off"})
        self.assertEqual(compact["requiredCapabilities"], ["execution:dsh"])
        self.assertNotIn("requested", compact)
        check = decision["policyCheck"]
        self.assertEqual(check["hardConstraints"], {"adapter": "dsh", "effort": "off"})
        # The program's preference check keeps the user's published prefer entries;
        # task-local routing preferences are retired and no longer contribute.
        self.assertEqual(check["userPreference"], "matched")
        self.assertNotIn("routingPreference", decision["selectedProfile"])

    def test_sole_candidate_replay_returns_the_same_completed_decision(self):
        board = self.board()
        self.seed(board)
        params = {"requestId": "pick-sole-replay", "task": "needs image input",
                  "requiredCapabilities": ["input:image"]}
        first = board.call("selection_request", params)
        replay = board.call("selection_request", params)
        self.assertTrue(replay["duplicate"])
        self.assertEqual(replay["decisionId"], first["decisionId"])
        self.assertEqual(replay["status"], "completed")
        with board.store.db.read() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM decision_requests").fetchone()[0], 1)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM attempts").fetchone()[0], 0)
            self.assertEqual(connection.execute(
                "SELECT COUNT(*) FROM events WHERE kind LIKE 'decision.%'").fetchone()[0], 2)
        self.assertEqual(board.call("task_list", {"limit": 10})["runs"], [])

    def test_routing_basis_freezes_the_user_exclusions_of_the_request(self):
        board = self.board()
        self.seed(board, preferences=[
            {"profileId": SECOND_PROFILE_ID, "mode": "exclude", "reason": "excluded for this test"}])
        request = board.store.decisions._create(
            "basis-freeze",
            {"kind": "select", "task": "bounded selection", "constraints": {"adapter": "dsh"},
             "requiredCapabilities": [], "timeoutSeconds": 60},
        )
        self.assertEqual(request["status"], "completed")
        decision = self.decision(board, request["decisionId"])
        self.assertEqual(decision["routingBasis"]["candidateCount"], 1)
        self.assertEqual(decision["routingBasis"]["excludedCount"], 1)
        [excluded] = decision["routingBasis"]["excludedProfiles"]
        self.assertEqual(excluded["profileId"], SECOND_PROFILE_ID)
        self.assertEqual(excluded["reason"], "excluded for this test")
        self.assertEqual(excluded["source"], "override")
        self.assertEqual({key: excluded[key] for key in CONFIGURATION},
                         {key: SECOND_PROFILE[key] for key in CONFIGURATION})
        # A later preference change never rewrites the frozen basis.
        self.publish_user_patch(
            board, request_id="basis-change", command_id="basis-change",
            preferenceChanges=[{"profileId": SECOND_PROFILE_ID, "mode": "prefer", "reason": "changed later"}])
        refreshed = self.decision(board, request["decisionId"])
        self.assertEqual(refreshed["routingBasis"], decision["routingBasis"])

    def test_multiple_candidates_still_create_a_router_task_and_call_it(self):
        board = self.board()
        self.seed(board)
        request = self.request(board, request_id="pick-multi")
        self.assertEqual(request["status"], "queued")
        self.assertTrue(request["runId"])
        decision = self.decision(board, request["decisionId"])
        self.assertIsNone(decision["routerCalled"])
        self.assertEqual(decision["routingBasis"]["candidateCount"], 2)
        self.run_worker(board)
        completed = self.decision(board, request["decisionId"])
        self.assertEqual(completed["status"], "completed")
        self.assertIn("mock select chose", completed["reason"])
        self.readonly_start.assert_called_once()
        task = board.call("task_get", {"runId": request["runId"]})["task"]
        self.assertEqual(task["status"], "completed")

    def test_direct_selection_retains_the_pin_that_narrowed_the_candidates(self):
        board = self.board()
        self.seed(board, preferences=[{"profileId": PROFILE_ID, "mode": "pin", "reason": "fixed by user"}])
        request = self.request(board, request_id="pinned-direct")
        original = self.decision(board, request["decisionId"])
        self.assertEqual(original["reason"], SOLE_CANDIDATE_REASON)
        frozen = original["output"]["programSelection"]["preferences"]
        self.assertEqual(frozen, [{"profileId": PROFILE_ID, "mode": "pin", "reason": "fixed by user", "source": "override"}])
        self.publish_user_patch(board, request_id="unpin", command_id="unpin",
            preferenceChanges=[{"profileId": PROFILE_ID, "mode": "none"}])
        self.assertEqual(self.decision(board, request["decisionId"])["output"]["programSelection"]["preferences"], frozen)


class SoleCandidateWorkflowTests(WorkflowTestCase):
    seed = DecisionTestCase.seed
    use_helper = DecisionTestCase.use_helper
    publish_user_patch = DecisionTestCase.publish_user_patch

    def setUp(self):
        super().setUp()
        self.catalog_fixture()
        DecisionTestCase.use_helper(self)

    def routed(self, board, *, request_id="route-sole", task="Produce a verified implementation", **constraints):
        response = board.call("workflow_submit", {
            "requestId": request_id, "hostId": "host-1", "submissionToken": "submission-secret-1",
            "task": task, "cwd": str(self.workdir(request_id)),
            "executionWorkspace": {"kind": "existing", "access": "write"}, **constraints,
        })
        if response.get("control"):
            self.controls[response["runId"]] = response["control"]
        return response

    def test_sole_candidate_route_completes_without_a_router_attempt(self):
        board = self.board()
        self.seed(board)
        submitted = self.routed(board, requiredCapabilities=["effort:high"])
        routing = submitted["routing"]
        self.assertEqual(routing["status"], "completed")
        self.assertEqual(routing["source"], "single-candidate")
        self.assertEqual(routing["reason"], SOLE_CANDIDATE_REASON)
        self.assertIsNone(routing["taskId"])
        self.assertIsNone(routing["attemptId"])
        self.assertEqual(routing["routingBasis"],
                         {"candidateCount": 1, "excludedCount": 0, "excludedProfiles": []})
        self.assertEqual(submitted["executionConfiguration"],
                         {key: SECOND_PROFILE[key] for key in CONFIGURATION})
        self.assertEqual(submitted["counts"]["turns"], 0)
        self.assertFalse(submitted["awaitingHost"])
        with board.store.db.read() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM tasks").fetchone()[0], 1)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM attempts").fetchone()[0], 0)
            route = connection.execute("SELECT state, reason FROM workflow_routes").fetchone()
            self.assertEqual(route["state"], "resolved")
            self.assertEqual(route["reason"], SOLE_CANDIDATE_REASON)
            resolved = json.loads(connection.execute(
                "SELECT payload_json FROM events WHERE kind='workflow.routing_resolved'").fetchone()[0])
        self.assertEqual(resolved["source"], "single-candidate")
        self.assertEqual(resolved["configuration"], {key: SECOND_PROFILE[key] for key in CONFIGURATION})
        # Replaying the same submission is a duplicate, not a second route.
        self.assertTrue(self.routed(board, requiredCapabilities=["effort:high"])["duplicate"])
        # The run proceeds into ordinary execution under the adopted configuration.
        # The coding worker declares the capability that narrowed the candidates.
        board.call("worker_register", {"workerId": "w1", "capabilities": ["dsh", "command", "effort:high"]})
        claim = self.claim(board, run_id=submitted["runId"], claim_request_id="coding")
        self.assertEqual(claim["claim"]["task"]["spec"]["model"], SECOND_PROFILE["model"])
        self.assertEqual(claim["claim"]["turn"]["input"]["context"]["routing"]["decisionId"],
                         routing["decisionId"])

    def test_large_task_uses_the_sole_candidate_without_a_router_input(self):
        board = self.board()
        self.seed(board, decision_profile=None)
        task = "完整工作说明" * 3000
        submitted = self.routed(board, task=task, requiredCapabilities=["effort:high"])
        self.assertEqual(submitted["routing"]["status"], "completed")
        self.assertEqual(submitted["routing"]["requiredCapabilities"], ["effort:high"])
        self.assertEqual(submitted["routing"]["constraints"], {})
        decision = board.call("selection_get", {"decisionId": submitted["routing"]["decisionId"]})["decision"]
        self.assertIsNone(decision["runId"])
        self.assertEqual(decision["taskReference"]["bytes"], len(task.encode()))
        with board.store.db.read() as connection:
            goal = json.loads(connection.execute("SELECT goal_json FROM workflow_runs").fetchone()[0])
            self.assertEqual(goal["task"], task)

    def test_unlocked_reroute_freezes_its_own_candidate_basis(self):
        # The retired scenario narrowed the first route with a partial `effort`
        # constraint and watched an unlocked reroute drop it; a partial tuple is
        # rejected input now, so the surviving essence is per-route freezing: a
        # goal that named no buddy routes with empty constraints, and a later
        # table change never rewrites the frozen basis of the earlier route.
        board = self.board()
        self.seed(board)
        submitted = self.routed(board)
        self.assertEqual(submitted["routing"]["status"], "queued")
        self.assertEqual(submitted["routing"]["constraints"], {})
        # Settle the first Router answer so the reroute can replace the route.
        board.call("worker_register", {"workerId": "router", "adapter": "decision", "capabilities": ["decision"]})
        claim = self.claim(board, "router", run_id=submitted["routing"]["taskId"], claim_request_id="router-claim")["claim"]
        board.call("worker_result", {
            "workerId": "router", "attemptId": claim["attempt"]["attemptId"],
            "generation": claim["attempt"]["generation"], "nonce": "n" * 16,
            "status": "ok", "shutdownConfirmed": True,
            "result": {"status": "ok", "operation": "select",
                       "tableRevision": claim["decisionInput"]["tableRevision"],
                       "usage": {"elapsedMs": 100, "toolCalls": 0},
                       "stopEvidence": {"shutdownConfirmed": True, "native": {"shutdownConfirmed": True}},
                       "inputVerification": {"unchanged": True, "snapshotSha256": "fixture-digest",
                                             "manifestSha256": claim["decisionInput"]["executionWorkspace"]["manifestSha256"]},
                       "decision": {"profileId": PROFILE_ID, "reason": "fixture selection", "evidence": []}},
        })
        settled = board.call("workflow_get", {"runId": submitted["runId"]})
        continued = self.continue_run(board, settled, command_id="reroute-unlocked", reroute=True)
        board.store.workflow.prepare_continuation_workspace({"runId": continued["runId"]})
        self.publish_user_patch(
            board, request_id="exclude-one", command_id="exclude-one",
            preferenceChanges=[{"profileId": SECOND_PROFILE_ID, "mode": "exclude", "reason": "narrowed later"}])
        routed = board.call("workflow_get", {"runId": submitted["runId"]})
        self.assertEqual(routed["routing"]["status"], "queued")
        self.assertEqual(routed["routing"]["constraints"], {})
        original = board.call("selection_get", {"decisionId": submitted["routing"]["decisionId"]})["decision"]
        self.assertEqual(original["constraints"], {})
        self.assertEqual(original["routingBasis"]["candidateCount"], 2)
        history = board.call("workflow_get", {
            "runId": submitted["runId"], "routingHistory": {"limit": 10}})["routingHistory"]
        self.assertEqual(len(history["entries"]), 2)
        self.assertEqual(history["entries"][1]["decisionId"], submitted["routing"]["decisionId"])

    def test_reroute_after_a_preference_change_reaches_the_host_boundary(self):
        board = self.board()
        self.seed(board)
        submitted = self.routed(board, requiredCapabilities=["effort:high"])
        self.assertEqual(submitted["routing"]["status"], "completed")
        # The user now excludes the only effort-high candidate. The locked
        # constraint survives the reroute, so it finds no legal candidate and
        # stops at the Host boundary with the frozen basis naming the exclusion.
        self.publish_user_patch(
            board, request_id="exclude-high", command_id="exclude-high",
            preferenceChanges=[{"profileId": SECOND_PROFILE_ID, "mode": "exclude", "reason": "no longer wanted"}])
        current = board.call("workflow_get", {"runId": submitted["runId"]})
        pending = self.continue_run(board, current, command_id="reroute-after-change", reroute=True)
        board.store.workflow.prepare_continuation_workspace({"runId": pending["runId"]})
        continued = board.call("workflow_get", {"runId": submitted["runId"]})
        self.assertEqual(continued["routing"]["status"], "needs-host")
        # The reroute stopped at the Host boundary without a Router call, so its
        # recorded source names the empty candidate set instead of a model choice.
        self.assertEqual(continued["routing"]["source"], "no-candidate")
        self.assertIn("legal candidate", continued["routing"]["reason"])
        basis = continued["routing"]["routingBasis"]
        self.assertEqual(basis["candidateCount"], 0)
        self.assertEqual(basis["excludedCount"], 1)
        self.assertEqual(basis["excludedProfiles"][0]["profileId"], SECOND_PROFILE_ID)
        # The earlier program selection stays completed, and the history keeps
        # each route's own recorded source.
        old = board.call("selection_get", {"decisionId": submitted["routing"]["decisionId"]})["decision"]
        self.assertEqual(old["status"], "completed")
        history = board.call("workflow_get", {
            "runId": submitted["runId"], "routingHistory": {"limit": 10}})["routingHistory"]
        self.assertEqual([entry["source"] for entry in history["entries"]],
                         ["no-candidate", "single-candidate"])
        self.assertEqual(history["entries"][1]["decisionId"], submitted["routing"]["decisionId"])

    def test_multi_candidate_route_keeps_the_router_attempt_path(self):
        board = self.board()
        self.seed(board)
        submitted = self.routed(board)
        self.assertEqual(submitted["routing"]["status"], "queued")
        self.assertTrue(submitted["routing"]["taskId"])
        self.assertEqual(submitted["routing"]["source"], "model-selection")
        board.call("worker_register", {"workerId": "router", "adapter": "decision", "capabilities": ["decision"]})
        claim = self.claim(board, "router", run_id=submitted["routing"]["taskId"], claim_request_id="router-claim")["claim"]
        self.assertEqual([profile["profileId"] for profile in claim["decisionInput"]["profiles"]],
                         [PROFILE_ID, SECOND_PROFILE_ID])
        board.call("worker_result", {
            "workerId": "router", "attemptId": claim["attempt"]["attemptId"],
            "generation": claim["attempt"]["generation"], "nonce": "n" * 16,
            "status": "ok", "shutdownConfirmed": True,
            "result": {"status": "ok", "operation": "select",
                       "tableRevision": claim["decisionInput"]["tableRevision"],
                       "usage": {"elapsedMs": 100, "toolCalls": 0},
                       "stopEvidence": {"shutdownConfirmed": True, "native": {"shutdownConfirmed": True}},
                       "inputVerification": {"unchanged": True, "snapshotSha256": "fixture-digest",
                                             "manifestSha256": claim["decisionInput"]["executionWorkspace"]["manifestSha256"]},
                       "decision": {"profileId": PROFILE_ID, "reason": "fixture selection", "evidence": []}},
        })
        view = board.call("workflow_get", {"runId": submitted["runId"]})
        self.assertEqual(view["routing"]["status"], "completed")
        self.assertEqual(view["routing"]["source"], "model-selection")
        self.assertEqual(view["executionConfiguration"], CONFIGURATION)


if __name__ == "__main__":
    import unittest

    unittest.main()
