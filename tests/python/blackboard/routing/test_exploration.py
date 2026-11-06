"""The exploration quota: counting, the quota-carrying input and publication."""
from __future__ import annotations

import json

from blackboard.routing.fixtures.router_tool_receipt import claim_tool_receipt
from blackboard.routing.test_decision import (DecisionTestCase, CATALOG_BUDDY, SECOND_BUDDY,
                                         THIRD_BUDDY, BUDDY_ID, SECOND_BUDDY_ID)
from blackboard.tasks.test_workflow import NONCE, WorkflowTestCase
from hey_my_buddy.blackboard.routing import router
from hey_my_buddy.errors import BoardError


def quota_decision(*, preferred, buddy_id, reselected, domain_node_id=None) -> dict:
    return {"domainNodeId": domain_node_id, "taskType": "change", "buddyId": buddy_id,
            "preferredBuddyId": preferred, "reselected": reselected,
            "note": "explore the least sampled kind", "evidence": []}


class ExplorationRoutingTests(WorkflowTestCase):
    seed = DecisionTestCase.seed

    def setUp(self):
        super().setUp()
        self.catalog_fixture()
        DecisionTestCase.use_helper(self)

    # -- helpers -------------------------------------------------------------
    def routed(self, board, request_id):
        return self.submit(board, request_id=request_id, task="Produce a verified implementation",
                           cwd=str(self.workdir(request_id)), buddy="route")

    def configure(self, board, every, command_id="exploration-settings"):
        board.console_call("user_policy_publish",
                           {"commandId": command_id, "configuration": {"explorationEvery": every}})

    def route_state(self, board, view):
        with board.store.db.read() as connection:
            route = connection.execute("SELECT * FROM workflow_routes WHERE decision_id=?",
                                       (view["routing"]["decisionId"],)).fetchone()
            snapshot = json.loads(connection.execute(
                "SELECT snapshot_json FROM router_requests WHERE decision_id=?",
                (view["routing"]["decisionId"],)).fetchone()[0])
        return route, snapshot

    def router_claim(self, board, view, *, claim_id="router-1"):
        return self.claim(board, "router", run_id=view["routing"]["taskId"], claim_request_id=claim_id)

    def select(self, board, claim, decision, *, shutdown=True):
        owned = claim["claim"]
        return board.call('execution_result', {
            'attemptId': owned['attempt']['attemptId'], 'generation': owned['attempt']['generation'],
            'nonce': NONCE, 'status': "ok", 'shutdownConfirmed': shutdown,
            'result': {**claim_tool_receipt(owned, state_dir=board.directory), 'status': 'ok',
                       'operation': 'select',
                       'stopEvidence': {'shutdownConfirmed': shutdown, 'native': {'shutdownConfirmed': shutdown}},
                       'usage': {'elapsedMs': 100, 'toolCalls': 0},
                       'inputVerification': {'unchanged': True, 'snapshotSha256': 'fixture-digest',
                                             'manifestSha256': owned['decisionInput']['executionWorkspace']['manifestSha256']},
                       'decision': decision}})

    def seed_cell_samples(self, board):
        """One judged cell where the preferred kind has samples and the other none."""
        task = board.submit_execution(
            requestId="exploration-maintenance", task="Synthetic maintenance", adapter="command",
            argv=["true"], cwd=str(self.workdir("maintenance")))["task"]["runId"]
        with board.store.db.write() as db:
            now = board.store.now()
            db.execute("INSERT INTO maintenance_runs VALUES('domains','manual',?,?,"
                       "'completed',NULL,NULL,?,?)", (BUDDY_ID, task, now, now))
            db.execute("INSERT INTO domain_nodes VALUES('ipc','IPC','IPC expertise',NULL,NULL,'domains')")
            db.execute("INSERT INTO kind_cell_statistics VALUES('dsh','deepseek-flash','off',"
                       "'ipc','change',2,2,2,0,0)")

    # -- counting and giving -------------------------------------------------
    def test_the_counted_routings_since_the_last_quota_decide_the_next_quota(self):
        board = self.board()
        self.seed(board)
        self.configure(board, 2)
        dispositions, snapshots = [], []
        for index in range(4):
            view = self.routed(board, f"r{index}")
            route, snapshot = self.route_state(board, view)
            dispositions.append(route["exploration"])
            snapshots.append(snapshot)
        self.assertEqual(dispositions, ["counted", "quota-kept", "counted", "quota-kept"])
        for snapshot in snapshots:
            self.assertEqual(snapshot["facts"]["exploration"]["every"], 2)
        quota_free, quota_input = snapshots[0]["baseInput"], snapshots[1]["baseInput"]
        self.assertNotIn("exploration", quota_free)
        self.assertEqual(quota_input["exploration"], {"quota": True})
        self.assertNotIn("preferredBuddyId", quota_free["outputSchema"]["properties"])
        self.assertEqual(sorted(quota_input["outputSchema"]["required"]),
                         ["buddyId", "domainNodeId", "evidence", "note", "preferredBuddyId",
                          "reselected", "taskType"])
        self.assertNotIn('"exploration"', router.render_prompt(quota_free))
        self.assertIn('"exploration":{"quota":true}', router.render_prompt(quota_input))

    def test_the_default_interval_is_five_and_null_turns_exploration_off(self):
        board = self.board()
        self.seed(board)
        dispositions = []
        for index in range(5):
            route, _ = self.route_state(board, self.routed(board, f"d{index}"))
            dispositions.append(route["exploration"])
        self.assertEqual(dispositions, ["counted"] * 4 + ["quota-kept"])
        self.configure(board, None, command_id="exploration-off")
        dispositions = []
        for index in range(3):
            route, snapshot = self.route_state(board, self.routed(board, f"o{index}"))
            dispositions.append(route["exploration"])
            self.assertNotIn("exploration", snapshot["baseInput"])
            self.assertEqual(snapshot["facts"]["exploration"]["every"], None)
        self.assertEqual(dispositions, ["counted"] * 3)

    def test_recovery_program_and_no_candidate_routings_never_count(self):
        board = self.board()
        # Before any buddy is enabled, the micro task has no candidate: the
        # routing is recorded and never counts.
        no_candidates = self.routed(board, "blocked")
        route, snapshot = self.route_state(board, no_candidates)
        self.assertEqual(route["exploration"], "ineligible")
        self.assertEqual(snapshot["facts"]["exploration"]["disposition"], "ineligible")
        # A manual continuation's reroute routes continued work, not a fresh
        # micro task, so it stays ineligible even with candidates available.
        self.seed(board)
        self.configure(board, 2)
        continued = self.continue_run(board, no_candidates, requestId=no_candidates["activeRequest"]["requestId"],
                                       buddy="route", note="Use the now available selector")
        board.store.workflow.prepare_continuation_workspace({"runId": continued["runId"]})
        reroute = board.call("workflow_get", {"runId": continued["runId"]})
        route, _ = self.route_state(board, reroute)
        self.assertEqual(route["exploration"], "ineligible")
        # Exactly the two fresh micro tasks count: the second carries the quota.
        dispositions = [self.route_state(board, self.routed(board, name))[0]["exploration"]
                        for name in ("fresh-1", "fresh-2")]
        self.assertEqual(dispositions, ["counted", "quota-kept"])

    def test_a_sole_legal_candidate_is_program_selection_and_never_counts(self):
        board = self.board()
        self.seed(board, buddies=(CATALOG_BUDDY,), decision_buddy=None)
        self.configure(board, 2)
        route, snapshot = self.route_state(board, self.routed(board, "sole"))
        self.assertEqual(route["exploration"], "ineligible")
        self.assertEqual(snapshot["facts"]["routerCalled"], False)

    # -- answer shape and publication ----------------------------------------
    def test_a_reselection_to_the_least_sampled_kind_publishes_and_is_recorded(self):
        board = self.board()
        self.seed(board)
        self.configure(board, 2)
        self.seed_cell_samples(board)
        first = self.routed(board, "warm-up")
        second = self.routed(board, "quota")
        self.assertEqual(self.route_state(board, second)[0]["exploration"], "quota-kept")
        claim = self.router_claim(board, second)
        self.assertEqual(claim["claim"]["decisionInput"]["exploration"], {"quota": True})
        self.select(board, claim, quota_decision(
            preferred=BUDDY_ID, buddy_id=SECOND_BUDDY_ID, reselected=True, domain_node_id="ipc"))
        decision = board.call("selection_get", {"decisionId": second["routing"]["decisionId"]})["decision"]
        self.assertEqual(decision["status"], "completed")
        self.assertEqual(decision["buddyId"], SECOND_BUDDY_ID)
        with board.store.db.read() as connection:
            self.assertEqual(connection.execute("SELECT exploration FROM workflow_routes WHERE decision_id=?",
                                                (second["routing"]["decisionId"],)).fetchone()[0],
                             "quota-reselected")
        history = board.call("workflow_get", {"runId": second["runId"], "routingHistory": {"limit": 10}})
        entry = next(item for item in history["routingHistory"]["entries"]
                     if item["decisionId"] == second["routing"]["decisionId"])
        self.assertEqual(entry["exploration"], {"quota": True, "reselected": True,
                                                "preferredBuddyId": BUDDY_ID})
        self.assertEqual(entry["selectedBuddy"]["buddyId"], SECOND_BUDDY_ID)

    def test_a_reselection_to_a_sampled_kind_is_refused_and_keeps_the_route_quota(self):
        board = self.board()
        self.seed(board)
        self.configure(board, 2)
        self.seed_cell_samples(board)
        self.routed(board, "warm-up")
        second = self.routed(board, "quota")
        claim = self.router_claim(board, second)
        self.select(board, claim, quota_decision(
            preferred=SECOND_BUDDY_ID, buddy_id=BUDDY_ID, reselected=True, domain_node_id="ipc"))
        decision = board.call("selection_get", {"decisionId": second["routing"]["decisionId"]})["decision"]
        self.assertEqual(decision["status"], "needs-host")
        self.assertEqual(decision["reason"]["code"], "router-exploration-out-of-bounds")
        with board.store.db.read() as connection:
            self.assertEqual(connection.execute("SELECT exploration FROM workflow_routes WHERE decision_id=?",
                                                (second["routing"]["decisionId"],)).fetchone()[0],
                             "quota-kept")
        history = board.call("workflow_get", {"runId": second["runId"], "routingHistory": {"limit": 10}})
        entry = next(item for item in history["routingHistory"]["entries"]
                     if item["decisionId"] == second["routing"]["decisionId"])
        self.assertEqual(entry["exploration"], {"quota": True, "reselected": False, "preferredBuddyId": None})

    def test_keeping_the_preferred_choice_publishes_and_stays_quota_kept(self):
        board = self.board()
        self.seed(board)
        self.configure(board, 2)
        self.routed(board, "warm-up")
        second = self.routed(board, "quota")
        claim = self.router_claim(board, second)
        self.select(board, claim, quota_decision(
            preferred=BUDDY_ID, buddy_id=BUDDY_ID, reselected=False))
        decision = board.call("selection_get", {"decisionId": second["routing"]["decisionId"]})["decision"]
        self.assertEqual(decision["status"], "completed")
        self.assertEqual(decision["buddyId"], BUDDY_ID)
        with board.store.db.read() as connection:
            self.assertEqual(connection.execute("SELECT exploration FROM workflow_routes WHERE decision_id=?",
                                                (second["routing"]["decisionId"],)).fetchone()[0],
                             "quota-kept")
        history = board.call("workflow_get", {"runId": second["runId"], "routingHistory": {"limit": 10}})
        entry = next(item for item in history["routingHistory"]["entries"]
                     if item["decisionId"] == second["routing"]["decisionId"])
        self.assertEqual(entry["exploration"], {"quota": True, "reselected": False,
                                                "preferredBuddyId": BUDDY_ID})

    def test_a_selection_retry_keeps_the_routes_quota_in_its_fresh_input(self):
        board = self.board()
        # A third buddy keeps two legal candidates after the selected one is
        # disabled, so the retry builds a complete fresh Router input.
        self.seed(board, buddies=(CATALOG_BUDDY, SECOND_BUDDY, THIRD_BUDDY))
        self.configure(board, 2)
        self.routed(board, "warm-up")
        second = self.routed(board, "quota")
        claim = self.router_claim(board, second)
        # The selected buddy becomes illegal after the frozen run: the answer is
        # refused as changed circumstances and the selection retries once with a
        # fresh input that still carries this route's quota.
        with board.store.db.write() as db:
            db.execute("UPDATE buddies SET enabled=0 WHERE buddy_id=?", (SECOND_BUDDY_ID,))
        self.select(board, claim, quota_decision(
            preferred=BUDDY_ID, buddy_id=SECOND_BUDDY_ID, reselected=True))
        decision = board.call("selection_get", {"decisionId": second["routing"]["decisionId"],
                                                "includeAudit": True})["decision"]
        self.assertTrue(decision["requested"].get("selectionRetry"))
        self.assertEqual(decision["input"]["exploration"], {"quota": True})
        self.assertIn("preferredBuddyId", decision["input"]["outputSchema"]["properties"])
        with board.store.db.read() as connection:
            self.assertEqual(connection.execute("SELECT exploration FROM workflow_routes WHERE decision_id=?",
                                                (second["routing"]["decisionId"],)).fetchone()[0],
                             "quota-kept")

    def test_a_quota_answer_may_only_reselect_or_keep_the_preferred_buddy(self):
        candidates = [BUDDY_ID, SECOND_BUDDY_ID]
        for answer in (
                quota_decision(preferred=BUDDY_ID, buddy_id=None, reselected=True),
                quota_decision(preferred=BUDDY_ID, buddy_id=BUDDY_ID, reselected=True),
                quota_decision(preferred=BUDDY_ID, buddy_id=SECOND_BUDDY_ID, reselected=False),
                {**quota_decision(preferred=BUDDY_ID, buddy_id=BUDDY_ID, reselected=False),
                 "preferredBuddyId": "outsider"},
                {key: value for key, value in
                 quota_decision(preferred=BUDDY_ID, buddy_id=BUDDY_ID, reselected=False).items()
                 if key != "reselected"},
        ):
            with self.subTest(answer=answer), self.assertRaises(BoardError) as failure:
                router.validate_answer(answer, candidates, "review", domain_node_ids=["ipc"],
                                       exploration=True)
            self.assertIn(failure.exception.code, ("answer-shape", "router-out-of-bounds"))
        kept = quota_decision(preferred=BUDDY_ID, buddy_id=BUDDY_ID, reselected=False, domain_node_id="ipc")
        self.assertEqual(router.validate_answer(kept, candidates, "review",
                                                domain_node_ids=["ipc"], exploration=True), kept)
        moved = quota_decision(preferred=BUDDY_ID, buddy_id=SECOND_BUDDY_ID, reselected=True,
                               domain_node_id="ipc")
        self.assertEqual(router.validate_answer(moved, candidates, "review",
                                                domain_node_ids=["ipc"], exploration=True), moved)
        # Without a quota the two fields stay outside the Schema entirely.
        without = {key: value for key, value in kept.items()
                   if key not in ("preferredBuddyId", "reselected")}
        self.assertEqual(router.validate_answer(without, candidates, "review",
                                                domain_node_ids=["ipc"]), without)
        with self.assertRaises(BoardError) as failure:
            router.validate_answer({**without, "reselected": False}, candidates, "review",
                                   domain_node_ids=["ipc"])
        self.assertEqual(failure.exception.code, "answer-shape")


if __name__ == "__main__":
    unittest.main()
