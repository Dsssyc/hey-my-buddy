"""The sole legal candidate is selected by the program without a Router call.

Freezing the candidate set happens before any Router resolution: when exactly
one enabled, available, capability-matching, complete coding buddy satisfies
the hard bounds, the program adopts it directly, records the precise reason
``唯一合法候选，未调用 Router`` and creates no Router task, usage or stop
evidence — none of which happened. Multiple candidates keep the ordinary Router
path, and zero candidates keep the Host boundary.
"""
from __future__ import annotations

import json

SOLE_CANDIDATE_REASON = {"code": "single-candidate", "params": {}}
from blackboard.routing.fixtures.router_tool_receipt import claim_tool_receipt
from blackboard.routing.test_decision import (
    DecisionTestCase,
    CATALOG_BUDDY,
    BUDDY_ID,
    SECOND_BUDDY,
    SECOND_BUDDY_ID,
)
from blackboard.tasks.test_workflow import BUDDY, WorkflowTestCase


class SoleCandidateSelectionTests(DecisionTestCase):
    def test_sole_candidate_is_selected_by_the_program_without_a_router_call(self):
        board = self.board()
        self.seed(board)
        request = board.store.decisions.request_select({
            "requestId": "pick-sole", "task": "needs image input",
            "requiredCapabilities": ["input:image"],
        })
        self.assertEqual(request["status"], "completed")
        self.assertIsNone(request["runId"])
        decision = self.decision(board, request["decisionId"])
        self.assertEqual(decision["buddyId"], BUDDY_ID)
        self.assertEqual(decision["reason"], SOLE_CANDIDATE_REASON)
        self.assertEqual(decision["selectedBuddy"]["buddyId"], BUDDY_ID)
        self.assertEqual({key: decision["selectedBuddy"][key] for key in BUDDY},
                         {key: CATALOG_BUDDY[key] for key in BUDDY})
        # No model ran, so no model evidence exists: nothing is fabricated.
        self.assertIsNone(decision["input"])
        self.assertIsNone(decision["runId"])
        self.assertEqual(decision["evidence"], [])
        for absent in ("usage", "stopEvidence", "nativeIdentity", "inputVerification", "budget"):
            self.assertIsNone(decision[absent], absent)
        self.assertIs(decision["routerCalled"], False)
        self.assertEqual(decision["routingBasis"],
                         {"candidateCount": 1, "excludedBuddies": [],
                          "coolingBuddies": [], "overloadedModelServices": []})
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
        self.seed(board, decision_buddy=None)
        sole = board.store.decisions.request_select({
            "requestId": "pick-sole-no-router", "task": "needs image input",
            "requiredCapabilities": ["input:image"],
        })
        self.assertEqual(sole["status"], "completed")
        self.assertEqual(self.decision(board, sole["decisionId"])["reason"], SOLE_CANDIDATE_REASON)
        # The same table without a Router still opens the Host boundary when the
        # program has more than one candidate to compare.
        multi = self.request(board, request_id="pick-multi-no-router")
        self.assertEqual(multi["status"], "needs-host")
        self.assertEqual(self.decision(board, multi["decisionId"])["reason"]["code"], "router-not-configured")
        self.assertEqual(self.decision(board, multi["decisionId"])["routerProblem"]["code"], "router-not-configured")

    def test_selection_source_reads_only_recorded_frozen_facts(self):
        from hey_my_buddy.blackboard.routing.router import selection_source
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
        self.seed(board, preferences=[{"buddyId": BUDDY_ID, "mode": "prefer", "note": "prefer flash"}])
        request = board.store.decisions._create(
            "sole-bounds",
            {"kind": "select", "task": "bounded selection",
             "constraints": {"harness": "dsh", "effort": "off"},
             "requiredCapabilities": ["execution:dsh"]},
        )
        self.assertEqual(request["status"], "completed")
        decision = self.decision(board, request["decisionId"])
        self.assertEqual(decision["requested"]["constraints"], {"harness": "dsh", "effort": "off"})
        self.assertEqual(decision["requested"]["requiredCapabilities"], ["execution:dsh"])
        compact = board.call("selection_get", {"decisionId": request["decisionId"]})["decision"]
        self.assertEqual(compact["constraints"], {"harness": "dsh", "effort": "off"})
        self.assertEqual(compact["requiredCapabilities"], ["execution:dsh"])
        self.assertNotIn("requested", compact)
        check = decision["policyCheck"]
        self.assertEqual(check["hardConstraints"], {"harness": "dsh", "effort": "off"})
        # The program's preference check keeps the user's published prefer entries;
        # task-local routing preferences are retired and no longer contribute.
        self.assertEqual(check["userPreference"], "matched")
        self.assertNotIn("routingPreference", decision["selectedBuddy"])

    def test_sole_candidate_replay_returns_the_same_completed_decision(self):
        board = self.board()
        self.seed(board)
        params = {"requestId": "pick-sole-replay", "task": "needs image input",
                  "requiredCapabilities": ["input:image"]}
        first = board.store.decisions.request_select(params)
        replay = board.store.decisions.request_select(params)
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
            {"buddyId": SECOND_BUDDY_ID, "mode": "exclude", "note": "excluded for this test"}])
        request = board.store.decisions._create(
            "basis-freeze",
            {"kind": "select", "task": "bounded selection", "constraints": {"harness": "dsh"},
             "requiredCapabilities": []},
        )
        self.assertEqual(request["status"], "completed")
        decision = self.decision(board, request["decisionId"])
        self.assertEqual(decision["routingBasis"]["candidateCount"], 1)
        self.assertEqual(len(decision["routingBasis"]["excludedBuddies"]), 1)
        [excluded] = decision["routingBasis"]["excludedBuddies"]
        self.assertEqual(excluded["buddyId"], SECOND_BUDDY_ID)
        self.assertEqual(excluded["note"], "excluded for this test")
        self.assertNotIn("source", excluded)
        self.assertEqual({key: excluded[key] for key in BUDDY},
                         {key: SECOND_BUDDY[key] for key in BUDDY})
        # A later preference change never rewrites the frozen basis.
        self.publish_user_patch(
            board, request_id="basis-change", command_id="basis-change",
            preferenceChanges=[{"buddyId": SECOND_BUDDY_ID, "mode": "prefer", "note": "changed later"}])
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
        self.assertIn("mock select chose", completed["note"])
        self.readonly_start.assert_called_once()
        task = board.call("task_get", {"runId": request["runId"]})["task"]
        self.assertEqual(task["state"], "completed")

    def test_direct_selection_retains_the_pin_that_narrowed_the_candidates(self):
        board = self.board()
        self.seed(board, preferences=[{"buddyId": BUDDY_ID, "mode": "pin", "note": "fixed by user"}])
        request = self.request(board, request_id="pinned-direct")
        original = self.decision(board, request["decisionId"])
        self.assertEqual(original["reason"], SOLE_CANDIDATE_REASON)
        frozen = original["output"]["programSelection"]["preferences"]
        self.assertEqual(frozen, [{"buddyId": BUDDY_ID, "mode": "pin", "note": "fixed by user"}])
        self.publish_user_patch(board, request_id="unpin", command_id="unpin",
            preferenceChanges=[{"buddyId": BUDDY_ID, "mode": None}])
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
        return self.submit(board, request_id=request_id, task=task,
                           cwd=str(self.workdir(request_id)), kind="existing", access="write",
                           buddy="route", **constraints)

    def test_sole_candidate_route_completes_without_a_router_attempt(self):
        board = self.board()
        self.seed(board, preferences=[{"buddyId": BUDDY_ID, "mode": "exclude", "note": "fixture filter"}])
        submitted = self.routed(board)
        routing = submitted["routing"]
        self.assertEqual(routing["status"], "completed")
        self.assertEqual(routing["source"], "single-candidate")
        self.assertEqual(routing["reason"], SOLE_CANDIDATE_REASON)
        self.assertIsNone(routing["taskId"])
        self.assertIsNone(routing["attemptId"])
        self.assertEqual(routing["routingBasis"],
                         {"candidateCount": 1, "excludedBuddies": [{"buddyId": BUDDY_ID,
                          "harness": CATALOG_BUDDY["harness"], "provider": CATALOG_BUDDY["provider"],
                          "model": CATALOG_BUDDY["model"], "effort": CATALOG_BUDDY["effort"],
                          "note": "fixture filter"}],
                          "coolingBuddies": [], "overloadedModelServices": []})
        self.assertEqual(submitted["buddy"],
                         {key: SECOND_BUDDY[key] for key in BUDDY})
        self.assertEqual(submitted["counts"]["turns"], 0)
        self.assertFalse(submitted["awaitingHost"])
        with board.store.db.read() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM tasks").fetchone()[0], 1)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM attempts").fetchone()[0], 0)
            route = connection.execute("SELECT state FROM workflow_routes").fetchone()
            self.assertEqual(route["state"], "resolved")
            resolved = json.loads(connection.execute(
                "SELECT payload_json FROM events WHERE kind='workflow.routing_resolved'").fetchone()[0])
        self.assertEqual(resolved["source"], "single-candidate")
        self.assertEqual(resolved["buddy"], {key: SECOND_BUDDY[key] for key in BUDDY})
        # Replaying the same submission is a duplicate, not a second route.
        self.assertTrue(self.routed(board)["alreadyExists"])
        # The run proceeds into ordinary execution under the adopted buddy.
        # The coding worker declares the capability that narrowed the candidates.
        pass  # Admission is owned by the service.
        claim = self.claim(board, run_id=submitted["runId"], claim_request_id="coding")
        self.assertEqual(claim["claim"]["task"]["spec"]["model"], SECOND_BUDDY["model"])
        self.assertEqual(claim["claim"]["turn"]["input"]["context"]["routing"]["decisionId"],
                         routing["decisionId"])

    def test_large_task_uses_the_sole_candidate_without_a_router_input(self):
        board = self.board()
        self.seed(board, decision_buddy=None, preferences=[{"buddyId": BUDDY_ID, "mode": "exclude", "note": "fixture filter"}])
        task = "完整工作说明" * 3000
        submitted = self.routed(board, task=task)
        self.assertEqual(submitted["routing"]["status"], "completed")
        self.assertEqual(submitted["routing"]["requiredCapabilities"], ["permission:write"])
        self.assertEqual(submitted["routing"]["constraints"], {})
        decision = board.call("selection_get", {"decisionId": submitted["routing"]["decisionId"]})["decision"]
        self.assertIsNone(decision["runId"])
        self.assertEqual(decision["task"], task)
        from hey_my_buddy.blackboard.routing import router_sequence
        with board.store.db.read() as connection:
            self.assertEqual(router_sequence.request_snapshot(connection, decision["decisionId"])
                             ["baseInput"]["task"], task)
        with board.store.db.read() as connection:
            micro_task = json.loads(connection.execute("SELECT spec_json FROM tasks").fetchone()[0])
            self.assertEqual(micro_task["task"], task)

    def test_each_route_freezes_its_own_candidate_basis(self):
        board = self.board()
        self.seed(board,preferences=[{"buddyId":BUDDY_ID,"mode":"exclude","note":"First route has one candidate"}])
        submitted = self.routed(board)
        first_id = submitted["routing"]["decisionId"]
        self.assertEqual(submitted["routing"]["routingBasis"]["candidateCount"],1)
        self.finish_turn(board,self.claim(board,run_id=submitted["runId"]))
        self.publish_user_patch(board,request_id="restore-candidate",command_id="restore-candidate",
                                preferenceChanges=[{"buddyId":BUDDY_ID,"mode":None}])
        current = board.call("workflow_get",{"runId":submitted["runId"]})
        self.continue_run(board,current,buddy="route",note="Route the next fixed turn with current settings")
        board.store.workflow.prepare_continuation_workspace({"runId":submitted["runId"]})
        routed = board.call("workflow_get",{"runId":submitted["runId"]})
        self.assertEqual(routed["routing"]["routingBasis"]["candidateCount"],2)
        second_id = routed["routing"]["decisionId"]
        self.publish_user_patch(board,request_id="later-change",command_id="later-change",
                                preferenceChanges=[{"buddyId":SECOND_BUDDY_ID,"mode":"exclude","note":"Changed after capture"}])
        first = board.call("selection_get",{"decisionId":first_id})["decision"]
        second = board.call("selection_get",{"decisionId":second_id})["decision"]
        self.assertEqual(first["routingBasis"]["candidateCount"],1)
        self.assertEqual(second["routingBasis"]["candidateCount"],2)
        history = board.call("workflow_get",{"runId":submitted["runId"],"routingHistory":{"limit":10}})["routingHistory"]
        self.assertEqual([item["decisionId"] for item in history["entries"]],[second_id,first_id])

    def test_route_after_user_excludes_every_candidate_stops_at_boundary(self):
        board = self.board()
        self.seed(board,preferences=[{"buddyId":BUDDY_ID,"mode":"exclude","note":"First exclusion"}])
        submitted = self.routed(board)
        first_id = submitted["routing"]["decisionId"]
        self.finish_turn(board,self.claim(board,run_id=submitted["runId"]))
        self.publish_user_patch(board,request_id="exclude-every-candidate",command_id="exclude-every-candidate",
                                preferenceChanges=[{"buddyId":SECOND_BUDDY_ID,"mode":"exclude","note":"Second exclusion"}])
        current = board.call("workflow_get",{"runId":submitted["runId"]})
        self.continue_run(board,current,buddy="route",note="Use the user's current candidate exclusions")
        board.store.workflow.prepare_continuation_workspace({"runId":submitted["runId"]})
        boundary = board.call("workflow_get",{"runId":submitted["runId"]})
        self.assertEqual(boundary["routing"]["status"],"needs-host")
        self.assertEqual(boundary["routing"]["source"],"no-candidate")
        self.assertEqual(boundary["routing"]["routingBasis"]["candidateCount"],0)
        self.assertEqual({item["buddyId"] for item in boundary["routing"]["routingBasis"]["excludedBuddies"]},
                         {BUDDY_ID,SECOND_BUDDY_ID})
        self.assertEqual(board.call("selection_get",{"decisionId":first_id})["decision"]["status"],"completed")
        history = board.call("workflow_get",{"runId":submitted["runId"],"routingHistory":{"limit":10}})["routingHistory"]
        self.assertEqual([item["source"] for item in history["entries"]],["no-candidate","single-candidate"])

    def test_multi_candidate_route_keeps_the_router_attempt_path(self):
        board = self.board()
        self.seed(board)
        submitted = self.routed(board)
        self.assertEqual(submitted["routing"]["status"], "queued")
        self.assertTrue(submitted["routing"]["taskId"])
        self.assertEqual(submitted["routing"]["source"], "model-selection")
        pass  # Admission is owned by the service.
        claim = self.claim(board, "router", run_id=submitted["routing"]["taskId"], claim_request_id="router-claim")["claim"]
        self.assertEqual([buddy["buddyId"] for buddy in claim["decisionInput"]["buddies"]],
                         [BUDDY_ID, SECOND_BUDDY_ID])
        board.call('execution_result', {'attemptId': claim['attempt']['attemptId'], 'generation': claim['attempt']['generation'], 'nonce': 'n' * 16, 'status': 'ok', 'shutdownConfirmed': True, 'result': {**claim_tool_receipt(claim, state_dir=board.directory), 'status': 'ok', 'operation': 'select', 'usage': {'elapsedMs': 100, 'toolCalls': 0}, 'stopEvidence': {'shutdownConfirmed': True, 'native': {'shutdownConfirmed': True}}, 'inputVerification': {'unchanged': True, 'snapshotSha256': 'fixture-digest', 'manifestSha256': claim['decisionInput']['executionWorkspace']['manifestSha256']}, 'decision': {"domainNodeId": None, "taskType": "change", 'buddyId': BUDDY_ID, "note": 'fixture selection', 'evidence': []}}})
        view = board.call("workflow_get", {"runId": submitted["runId"]})
        self.assertEqual(view["routing"]["status"], "completed")
        self.assertEqual(view["routing"]["source"], "model-selection")
        self.assertEqual(view["buddy"], BUDDY)


if __name__ == "__main__":
    import unittest

    unittest.main()
