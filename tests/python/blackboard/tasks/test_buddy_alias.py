"""Aliases resolve at the Host boundary and stay out of durable execution facts."""
from __future__ import annotations

import json

from blackboard.tasks.test_workflow import BUDDY, WorkflowTestCase
from blackboard.routing.test_routing_history import RoutingHistoryTestCase
from blackboard.routing.test_decision import BUDDY_ID, CATALOG_BUDDY, SECOND_BUDDY
from hey_my_buddy.blackboard.tasks.workflow import named_buddy
from hey_my_buddy.cli.cli_views import render
from hey_my_buddy.errors import BoardError
from hey_my_buddy.protocol.schemas import buddy_name
from support import enable_fixture_buddy


class HostAliasTests(WorkflowTestCase):
    def alias(self, board, identity, alias):
        enable_fixture_buddy(board.store, identity)
        with board.store.db.write() as connection:
            connection.execute("UPDATE buddies SET alias=? WHERE harness=? AND provider=? AND model=? AND effort=?",
                               (alias, *identity.values()))

    def test_three_spellings_resolve_and_mismatched_halves_report_both_identities(self):
        board = self.board()
        self.alias(board, BUDDY, "Straße助手")
        other = {**BUDDY, "effort": "high"}
        enable_fixture_buddy(board.store, other)
        self.alias(board, other, "另一个")
        quad = buddy_name(BUDDY)
        with board.store.db.read() as connection:
            for spelling in (quad, "STRASSE助手", f"{quad} [sTRASSE助手]"):
                with self.subTest(spelling=spelling):
                    self.assertEqual(named_buddy(connection, spelling), BUDDY)
            self.assertIsNone(named_buddy(connection, "route"))
            for spelling, complete, alias in (
                (f"{quad} [另一个]", BUDDY, other),
                (f"{quad} [unknown]", BUDDY, None),
                (f"missing / p / m / off [另一个]", None, other),
            ):
                with self.subTest(spelling=spelling), self.assertRaises(BoardError) as error:
                    named_buddy(connection, spelling)
                self.assertEqual(error.exception.code, "INVALID_ARGUMENT")
                self.assertEqual(error.exception.details["completeBuddy"], complete)
                self.assertEqual(error.exception.details["aliasBuddy"], alias)
            with self.assertRaises(BoardError):
                named_buddy(connection, "ROUTE")

    def test_submit_and_turn_replies_use_current_alias_without_rewriting_frozen_inputs(self):
        board = self.board()
        self.alias(board, BUDDY, "执行者")
        submitted = self.submit(board, buddy="执行者")
        self.assertEqual(submitted["buddy"], {**BUDDY, "alias": "执行者"})
        self.assertEqual(render("submit", submitted)["buddy"], f"{buddy_name(BUDDY)} [执行者]")
        claimed = self.claim(board)["claim"]
        frozen = claimed["turn"]["input"]
        self.assertEqual(frozen["context"]["buddy"], BUDDY)
        self.alias(board, BUDDY, "新名字")
        read = board.call("workflow_get", {"runId": submitted["runId"], "includeAudit": True})
        for view in (read, read["currentTurn"], read["turns"][0], read["audit"]["turns"][0]):
            self.assertEqual(view["buddy"], {**BUDDY, "alias": "新名字"})
        self.assertEqual(read["task"]["lineage"]["buddy"], {**BUDDY, "alias": "新名字"})
        self.assertEqual(read["audit"]["turns"][0]["input"], frozen)
        with board.store.db.read() as connection:
            self.assertEqual(json.loads(connection.execute("SELECT buddy_json FROM workflow_runs").fetchone()[0]), BUDDY)
            self.assertEqual(json.loads(connection.execute("SELECT input_json FROM workflow_turns").fetchone()[0]), frozen)

    def test_continuation_replay_compares_resolved_buddy_and_stores_only_complete_name(self):
        board = self.board()
        self.alias(board, BUDDY, "助手")
        submitted = self.submit(board)
        self.finish_turn(board, self.claim(board))
        view = board.call("workflow_get", {"runId": submitted["runId"]})
        packet = {"runId": submitted["runId"], "artifactId": view["finalArtifactId"],
                  "input": "Continue with the same identity", "note": "Explicit selection", "buddy": "助手"}
        first = board.call("workflow_continue", packet)
        for spelling in (buddy_name(BUDDY), buddy_name({**BUDDY, "alias": "助手"})):
            replay = board.call("workflow_continue", {**packet, "buddy": spelling})
            self.assertEqual(replay["continuationId"], first["continuationId"])
            self.assertTrue(replay["alreadyResponded"])
        self.alias(board, BUDDY, "改名")
        replay = board.call("workflow_continue", {**packet, "buddy": "改名"})
        self.assertEqual(replay["continuationId"], first["continuationId"])
        with board.store.db.read() as connection:
            stored = json.loads(connection.execute("SELECT buddy_selection_json FROM workflow_continuations").fetchone()[0])
            self.assertEqual(stored, buddy_name(BUDDY))
            self.assertNotIn("助手", connection.execute("SELECT buddy_json FROM workflow_runs").fetchone()[0])


class RoutedAliasTests(RoutingHistoryTestCase):
    def test_selected_buddy_is_identity_with_current_alias_and_frozen_candidate_stays_intact(self):
        board = self.board()
        self.seed(board, buddies=(CATALOG_BUDDY, SECOND_BUDDY))
        submitted = self.routed(board, request_id="alias-routing")
        self.select(board, self.router_claim(board, submitted))
        decision_id = submitted["routing"]["decisionId"]
        with board.store.db.read() as connection:
            original = connection.execute("SELECT selected_json FROM decision_requests WHERE decision_id=?", (decision_id,)).fetchone()[0]
        with board.store.db.write() as connection:
            connection.execute("UPDATE buddies SET alias='路由选择' WHERE buddy_id=?", (BUDDY_ID,))
        read = board.call("workflow_get", {"runId": submitted["runId"], "routingHistory": {}})
        expected = {**{key: CATALOG_BUDDY[key] for key in BUDDY}, "alias": "路由选择"}
        self.assertEqual(read["routing"]["selectedBuddy"], expected)
        self.assertEqual(read["routingHistory"]["entries"][0]["selectedBuddy"], expected)
        self.assertEqual(read["routing"]["routerBuddy"]["alias"], "路由选择")
        self.assertEqual(read["routingHistory"]["entries"][0]["routerBuddy"]["alias"], "路由选择")
        host = render("get", read, "full")
        self.assertEqual(host["routing"]["selectedBuddy"], buddy_name(expected))
        self.assertEqual(host["routingHistory"]["entries"][0]["selectedBuddy"], buddy_name(expected))
        with board.store.db.read() as connection:
            self.assertEqual(connection.execute("SELECT selected_json FROM decision_requests WHERE decision_id=?", (decision_id,)).fetchone()[0], original)
