"""The exploration quota: counting, the quota-carrying input and publication."""
from __future__ import annotations

import json

from blackboard.routing.fixtures.router_tool_receipt import claim_tool_receipt
from blackboard.routing.test_decision import (DecisionTestCase, CATALOG_BUDDY, SECOND_BUDDY,
                                         THIRD_BUDDY, THIRD_BUDDY_ID, BUDDY_ID, SECOND_BUDDY_ID)
from blackboard.tasks.test_workflow import BUDDY, NONCE, WorkflowTestCase
from hey_my_buddy.blackboard.routing import router
from hey_my_buddy.protocol import router_selection
from hey_my_buddy.buddy.roles.router import render_selection_prompt
from blackboard.routing.fixtures.selection import frozen_input
from hey_my_buddy.blackboard.evaluation.models import ensure_model_service
from hey_my_buddy.errors import BoardError


def quota_decision(*, preferred, buddy_id, domain_node_id=None) -> dict:
    return {"domainNodeId": domain_node_id, "taskType": "change", "buddyId": buddy_id,
            "preferredBuddyId": preferred,
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

    def seed_domains(self, board, *, nodes, cells=(), overall=()):
        """Insert a maintenance-run-owned domain tree and kind statistics.

        ``nodes`` are (nodeId, name, parentId) triples, ``cells`` are
        (buddy, nodeId, taskType, samples) kind cells and ``overall`` are
        (buddy, samples) overall rows; every sample succeeds first-pass.
        """
        task = board.submit_execution(
            requestId="exploration-maintenance", task="Synthetic maintenance", adapter="command",
            argv=["true"], cwd=str(self.workdir("maintenance")))["task"]["runId"]
        with board.store.db.write() as db:
            now = board.store.now()
            db.execute("INSERT INTO maintenance_runs VALUES('domains','manual',?,?,"
                       "'completed',NULL,NULL,?,?)", (BUDDY_ID, task, now, now))
            for node_id, name, parent in nodes:
                db.execute("INSERT INTO domain_nodes VALUES(?,?,?,?,NULL,'domains')",
                           (node_id, name, name + " expertise", parent))
            for buddy, node_id, task_type, samples in cells:
                db.execute("INSERT INTO kind_cell_statistics(harness,model_id,effort,domain_node_id,task_type,"
                           "samples,successes,first_passes,rework_rounds,failures) VALUES(?,?,?,?,?,?,?,?,?,?)",
                           (buddy["harness"], ensure_model_service(db, buddy["harness"], buddy["provider"], buddy["model"], now), buddy["effort"],
                            node_id, task_type, samples, samples, samples, 0, 0))
            for buddy, samples in overall:
                db.execute("INSERT INTO kind_statistics(harness,model_id,effort,samples,successes,first_passes,"
                           "rework_rounds,failures) VALUES(?,?,?,?,?,?,?,?)",
                           (buddy["harness"], ensure_model_service(db, buddy["harness"], buddy["provider"], buddy["model"], now), buddy["effort"],
                            samples, samples, samples, 0, 0))

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
            # The route row is the one record of the disposition; the frozen
            # snapshot keeps only what the Router itself saw.
            self.assertNotIn("exploration", snapshot["facts"])
        quota_free, quota_input = snapshots[0]["baseInput"], snapshots[1]["baseInput"]
        self.assertNotIn("exploration", quota_free)
        self.assertEqual(quota_input["exploration"], {"quota": True})
        self.assertNotIn("outputSchema", quota_free)
        self.assertEqual(sorted(router_selection.answer_schema(quota_input["routingMode"], exploration=True)["required"]),
                         ["buddyId", "domainNodeId", "evidence", "note", "preferredBuddyId", "taskType"])
        self.assertNotIn('"exploration"', render_selection_prompt(quota_free))
        self.assertIn('"exploration":{"quota":true}', render_selection_prompt(quota_input))

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
            self.assertNotIn("exploration", snapshot["facts"])
        self.assertEqual(dispositions, ["counted"] * 3)

    def test_recovery_program_and_no_candidate_routings_never_count(self):
        board = self.board()
        # Before any buddy is enabled, the micro task has no candidate: the
        # routing is recorded and never counts.
        no_candidates = self.routed(board, "blocked")
        route, snapshot = self.route_state(board, no_candidates)
        self.assertEqual(route["exploration"], "ineligible")
        self.assertNotIn("exploration", snapshot["facts"])
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
        self.assertNotIn("exploration", snapshot["facts"])

    # -- answer shape and publication ----------------------------------------
    def test_a_reselection_to_a_less_sampled_kind_publishes_and_is_recorded(self):
        board = self.board()
        self.seed(board)
        self.configure(board, 2)
        # The preferred kind's five samples live on the sibling sub-domain, so
        # the comparison must run at the judged node's first-level ancestor:
        # at the judged sub-domain itself the preferred kind has none.
        self.seed_domains(board, nodes=[("systems", "Systems", None), ("ipc", "IPC", "systems"),
                                        ("concurrency", "Concurrency", "systems")],
                          cells=[(CATALOG_BUDDY, "concurrency", "change", 5)])
        self.routed(board, "warm-up")
        second = self.routed(board, "quota")
        self.assertEqual(self.route_state(board, second)[0]["exploration"], "quota-kept")
        claim = self.router_claim(board, second)
        self.assertEqual(claim["claim"]["decisionInput"]["exploration"], {"quota": True})
        self.assertEqual(frozen_input(board, claim["claim"]["decisionInput"])["domainNodes"], [
            {"nodeId": "concurrency", "name": "Concurrency", "definition": "Concurrency expertise", "parentId": "systems"},
            {"nodeId": "ipc", "name": "IPC", "definition": "IPC expertise", "parentId": "systems"},
            {"nodeId": "systems", "name": "Systems", "definition": "Systems expertise", "parentId": None}])
        self.select(board, claim, quota_decision(
            preferred=BUDDY_ID, buddy_id=SECOND_BUDDY_ID, domain_node_id="ipc"))
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
        self.assertEqual(entry["selectedBuddy"],
                         {**{key: SECOND_BUDDY[key] for key in BUDDY}, "alias": None})

    def test_a_less_sampled_group_need_not_be_the_least_sampled(self):
        board = self.board()
        # Three kinds at one first-level domain: 5 samples, 1 sample and none.
        # The 1-sample kind is not the least sampled, yet 1 is fewer than 5.
        self.seed(board, buddies=(CATALOG_BUDDY, SECOND_BUDDY, THIRD_BUDDY))
        self.configure(board, 2)
        self.seed_domains(board, nodes=[("systems", "Systems", None), ("ipc", "IPC", "systems")],
                          cells=[(CATALOG_BUDDY, "ipc", "change", 5), (THIRD_BUDDY, "ipc", "change", 1)])
        self.routed(board, "warm-up")
        second = self.routed(board, "quota")
        claim = self.router_claim(board, second)
        self.select(board, claim, quota_decision(
            preferred=BUDDY_ID, buddy_id=THIRD_BUDDY_ID, domain_node_id="ipc"))
        decision = board.call("selection_get", {"decisionId": second["routing"]["decisionId"]})["decision"]
        self.assertEqual(decision["status"], "completed")
        self.assertEqual(decision["buddyId"], THIRD_BUDDY_ID)
        with board.store.db.read() as connection:
            self.assertEqual(connection.execute("SELECT exploration FROM workflow_routes WHERE decision_id=?",
                                                (second["routing"]["decisionId"],)).fetchone()[0],
                             "quota-reselected")

    def test_a_reselection_to_a_group_not_less_sampled_than_the_preferred_is_refused(self):
        board = self.board()
        self.seed(board)
        self.configure(board, 2)
        self.seed_domains(board, nodes=[("systems", "Systems", None), ("ipc", "IPC", "systems")],
                          cells=[(CATALOG_BUDDY, "ipc", "change", 5)])
        self.routed(board, "warm-up")
        second = self.routed(board, "quota")
        claim = self.router_claim(board, second)
        self.select(board, claim, quota_decision(
            preferred=SECOND_BUDDY_ID, buddy_id=BUDDY_ID, domain_node_id="ipc"))
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

    def test_an_unclassified_judgment_compares_overall_statistics(self):
        board = self.board()
        self.seed(board)
        self.configure(board, 2)
        self.seed_domains(board, nodes=[("systems", "Systems", None)],
                          overall=[(CATALOG_BUDDY, 3)])
        self.routed(board, "warm-up")
        accepted = self.routed(board, "quota-accepted")
        claim = self.router_claim(board, accepted)
        self.select(board, claim, quota_decision(
            preferred=BUDDY_ID, buddy_id=SECOND_BUDDY_ID))
        decision = board.call("selection_get", {"decisionId": accepted["routing"]["decisionId"]})["decision"]
        self.assertEqual(decision["status"], "completed")
        self.assertEqual(decision["buddyId"], SECOND_BUDDY_ID)
        # The refused direction compares the same way: 3 is not fewer than 0.
        self.routed(board, "counted")
        refused = self.routed(board, "quota-refused")
        claim = self.router_claim(board, refused, claim_id="router-2")
        self.select(board, claim, quota_decision(
            preferred=SECOND_BUDDY_ID, buddy_id=BUDDY_ID))
        decision = board.call("selection_get", {"decisionId": refused["routing"]["decisionId"]})["decision"]
        self.assertEqual(decision["status"], "needs-host")
        self.assertEqual(decision["reason"]["code"], "router-exploration-out-of-bounds")

    def test_keeping_the_preferred_choice_publishes_and_stays_quota_kept(self):
        board = self.board()
        self.seed(board)
        self.configure(board, 2)
        self.routed(board, "warm-up")
        second = self.routed(board, "quota")
        claim = self.router_claim(board, second)
        self.select(board, claim, quota_decision(
            preferred=BUDDY_ID, buddy_id=BUDDY_ID))
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
        # The reselection must clear the quota's own check (fewer overall
        # samples than the preferred kind) so the disabled-buddy refusal, not
        # the exploration refusal, triggers the retry.
        self.seed_domains(board, nodes=[("systems", "Systems", None)],
                          overall=[(CATALOG_BUDDY, 3)])
        self.routed(board, "warm-up")
        second = self.routed(board, "quota")
        claim = self.router_claim(board, second)
        # The selected buddy becomes illegal after the frozen run: the answer is
        # refused as changed circumstances and the selection retries once with a
        # fresh input that still carries this route's quota.
        with board.store.db.write() as db:
            db.execute("UPDATE buddies SET enabled=0 WHERE buddy_id=?", (SECOND_BUDDY_ID,))
        self.select(board, claim, quota_decision(
            preferred=BUDDY_ID, buddy_id=SECOND_BUDDY_ID))
        decision = board.call("selection_get", {"decisionId": second["routing"]["decisionId"],
                                                "includeAudit": True})["decision"]
        self.assertTrue(decision["requested"].get("selectionRetry"))
        self.assertEqual(decision["input"]["exploration"], {"quota": True})
        self.assertEqual(decision["input"]["protocolVersion"], 20)
        self.assertNotIn("outputSchema", decision["input"])
        with board.store.db.read() as connection:
            self.assertEqual(connection.execute("SELECT exploration FROM workflow_routes WHERE decision_id=?",
                                                (second["routing"]["decisionId"],)).fetchone()[0],
                             "quota-kept")

    def test_quota_model_schema_has_no_redundant_reselection_field(self):
        schema = router_selection.answer_schema("review", exploration=True)
        self.assertIn("preferredBuddyId", schema["required"])
        self.assertNotIn("reselected", schema["properties"])
