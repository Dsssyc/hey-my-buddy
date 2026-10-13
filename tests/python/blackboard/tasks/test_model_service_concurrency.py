"""The frozen model-service concurrency contract of ADR-011, backend only.

One machine-wide ceiling plus per-model-service limits, persistent user settings through
the authenticated console publication path, the frozen attempt identity, the
snapshot/buddy_list/health observation shapes and the routing quota check that
happens before a selection reader is ever admitted.
"""
from __future__ import annotations

import os
import unittest
from unittest import mock
from pathlib import Path

from blackboard.store.test_store import StoreConcurrencyTestCase

from hey_my_buddy.errors import BoardError

MODEL_SERVICE_A = ("dsh", "deepseek-official", "deepseek-flash")
MODEL_SERVICE_B = ("dsh", "deepseek-official", "deepseek-v4-pro")
DECISION_BUDDY = "dsh:deepseek-official:deepseek-flash:off"


class ModelServiceConcurrencyTestCase(StoreConcurrencyTestCase):
    """Governed fixtures plus the authenticated console policy writer."""

    def use_decision_helper(self) -> None:
        from protocol.fixtures import mock_readonly
        mock_readonly.install(self)

    def seed_decision_buddy(self, board) -> None:
        board.call("model_catalog_refresh", {"requestId": "catalog-seed"})
        board.console_call(
            "user_policy_publish",
            {
                "commandId": "seed-policy-1",



                # A second enabled candidate keeps every selection request on the
                # Router path; a sole candidate would be selected by the program
                # directly and never reach the model service quota check under test.
                "buddySettings": [{"buddyId": DECISION_BUDDY, "enabled": True},
                                    {"buddyId": "dsh:deepseek-official:deepseek-v4-pro:off", "enabled": True}],
                "configuration": {"defaultRoutingMode": "review", "routerBuddyIds": [DECISION_BUDDY]},
            },
        )

    def publish_limits(self, board, *patches: dict, command: str = "limits", writer_request: str | None = None) -> dict:
        return board.console_call(
            "user_policy_publish",
            {
                "commandId": command,



                "modelServiceConcurrency": list(patches),
            },
        )

    def model_service_row(self, payload: dict, model_service: tuple[str, str, str]) -> dict:
        rows = payload.get("modelServiceConcurrency", payload.get("models"))
        return next(
            row
            for row in rows
            if (row["adapter"], row["provider"], row["model"]) == model_service
        )


class PublicationInterfaceTests(ModelServiceConcurrencyTestCase):
    def setUp(self):
        super().setUp()
        self.board_ = self.board(max_concurrent=8)
        self.board_.call("model_catalog_refresh", {"requestId": "catalog-seed"})

    def test_snapshot_reports_one_row_per_represented_model_service_with_defaults(self):
        snapshot = self.board_.call("console_snapshot", {})
        rows = {(row["adapter"], row["provider"], row["model"]): row for row in snapshot["modelServiceConcurrency"]}
        # The fixture catalog represents two dsh model services; every row carries exactly
        # the frozen fields, effort variants collapse, and defaults apply.
        self.assertEqual(set(rows), {MODEL_SERVICE_A, MODEL_SERVICE_B})
        for row in rows.values():
            self.assertEqual(set(row), {"adapter", "provider", "model", "limit", "active"})
            self.assertEqual(row["limit"], 2)
            self.assertEqual(row["active"], 0)

    def test_model_buddies_pages_carry_the_same_model_service_rows(self):
        first = self.board_.call("buddy_list", {"limit": 1})
        self.assertEqual(len(first["modelServiceConcurrency"]), 1)
        self.assertEqual(set(first["modelServiceConcurrency"][0]), {"adapter", "provider", "model", "limit", "active"})
        full = self.board_.call("buddy_list", {"limit": 200})
        self.assertEqual(
            {(row["adapter"], row["provider"], row["model"]) for row in full["modelServiceConcurrency"]},
            {MODEL_SERVICE_A, MODEL_SERVICE_B},
        )

    def test_buddy_page_is_a_consistent_snapshot_during_a_concurrent_save(self):
        from hey_my_buddy.blackboard.catalog import catalog_store
        original = catalog_store.buddies

        def concurrent_save(evaluation, params):
            page = original(evaluation, params)
            self.publish_limits(self.board_, {
                "adapter": MODEL_SERVICE_A[0], "provider": MODEL_SERVICE_A[1], "model": MODEL_SERVICE_A[2], "limit": 5,
            }, command="save-between-page-and-response")
            return page

        with mock.patch.object(catalog_store, "buddies", side_effect=concurrent_save):
            page = self.board_.call("buddy_list", {"limit": 200})
        self.assertEqual(self.model_service_row(page, MODEL_SERVICE_A)["limit"], 2)
        fresh = self.board_.call("console_snapshot", {})
        self.assertEqual(self.model_service_row(fresh, MODEL_SERVICE_A)["limit"], 5)

    def test_active_counts_unresolved_attempts_of_the_model_service(self):
        board = self.board_
        self.register(board, "w-obs1")
        self.register(board, "w-obs2")
        self.claim_governed(board, "w-obs1", "obs-1")
        self.claim_governed(board, "w-obs2", "obs-2", model_service=MODEL_SERVICE_B)
        snapshot = board.call("console_snapshot", {})
        self.assertEqual(self.model_service_row(snapshot, MODEL_SERVICE_A)["active"], 1)
        self.assertEqual(self.model_service_row(snapshot, MODEL_SERVICE_B)["active"], 1)
        buddies = board.call("buddy_list", {"limit": 200})
        self.assertEqual(self.model_service_row(buddies, MODEL_SERVICE_A)["active"], 1)

    def test_health_capacity_reports_the_total_ceiling_and_model_service_rows(self):
        board = self.board_
        health = board.call("health", {})
        self.assertEqual(health["maxConcurrent"], 8)
        self.assertEqual(
            set(health["capacity"]), {"totalLimit", "totalActive", "models"}
        )
        self.assertEqual(health["capacity"]["totalLimit"], 8)
        self.assertEqual(health["capacity"]["totalActive"], 0)
        self.register(board, "w-health")
        self.claim_governed(board, "w-health", "health-1")
        health = board.call("health", {})
        self.assertEqual(health["capacity"]["totalActive"], 1)
        self.assertEqual(self.model_service_row(health["capacity"], MODEL_SERVICE_A)["active"], 1)


class PolicyPatchTests(ModelServiceConcurrencyTestCase):
    def setUp(self):
        super().setUp()
        self.board_ = self.board(max_concurrent=8)
        self.board_.call("model_catalog_refresh", {"requestId": "catalog-seed"})

    def test_the_authenticated_console_writer_sets_and_updates_limits(self):
        result = self.publish_limits(
            self.board_, {"adapter": MODEL_SERVICE_A[0], "provider": MODEL_SERVICE_A[1], "model": MODEL_SERVICE_A[2], "limit": 5}
        )
        self.assertTrue(result["published"])
        self.assertEqual(result["counts"]["modelServiceConcurrency"], 1)
        snapshot = self.board_.call("console_snapshot", {})
        self.assertEqual(self.model_service_row(snapshot, MODEL_SERVICE_A)["limit"], 5)
        self.assertEqual(self.model_service_row(snapshot, MODEL_SERVICE_B)["limit"], 2)
        # A later save updates only the named model service; unrelated settings persist.
        self.publish_limits(
            self.board_,
            {"adapter": MODEL_SERVICE_B[0], "provider": MODEL_SERVICE_B[1], "model": MODEL_SERVICE_B[2], "limit": 1},
            command="limits-2",
        )
        snapshot = self.board_.call("console_snapshot", {})
        self.assertEqual(self.model_service_row(snapshot, MODEL_SERVICE_A)["limit"], 5)
        self.assertEqual(self.model_service_row(snapshot, MODEL_SERVICE_B)["limit"], 1)

    def test_derived_and_unknown_fields_are_rejected(self):
        board = self.board_
        for patch in (
            {"adapter": MODEL_SERVICE_A[0], "provider": MODEL_SERVICE_A[1], "model": MODEL_SERVICE_A[2], "limit": 2, "active": 0},
            {"adapter": MODEL_SERVICE_A[0], "provider": MODEL_SERVICE_A[1], "model": MODEL_SERVICE_A[2], "effort": "off", "limit": 2},
            {"adapter": MODEL_SERVICE_A[0], "provider": MODEL_SERVICE_A[1], "limit": 2},
        ):
            with self.assertRaises(BoardError) as caught:
                self.publish_limits(board, patch)
            self.assertEqual(caught.exception.code, "INVALID_ARGUMENT")
        # Nothing was published by a rejected draft.
        with board.store.db.read() as connection:
            count = connection.execute("SELECT COUNT(*) AS count FROM model_service_concurrency").fetchone()["count"]
        self.assertEqual(count, 0)

    def test_limits_are_integers_between_one_and_thirty_two(self):
        board = self.board_
        for limit in (0, 33, -1, True, "4", 2.5, None):
            with self.assertRaises(BoardError):
                self.publish_limits(
                    board,
                    {"adapter": MODEL_SERVICE_A[0], "provider": MODEL_SERVICE_A[1], "model": MODEL_SERVICE_A[2], "limit": limit},
                )

    def test_repeated_model_services_in_one_patch_are_refused(self):
        board = self.board_
        with self.assertRaises(BoardError) as caught:
            self.publish_limits(
                board,
                {"adapter": MODEL_SERVICE_A[0], "provider": MODEL_SERVICE_A[1], "model": MODEL_SERVICE_A[2], "limit": 3},
                {"adapter": MODEL_SERVICE_A[0], "provider": MODEL_SERVICE_A[1], "model": MODEL_SERVICE_A[2], "limit": 4},
            )
        self.assertEqual(caught.exception.code, "INVALID_ARGUMENT")

    def test_unknown_model_service_is_rejected_but_retained_unavailable_model_service_is_editable(self):
        with self.assertRaises(BoardError) as caught:
            self.publish_limits(self.board_, {
                "adapter": "zcode", "provider": "misspelled-provider", "model": "unknown", "limit": 3,
            })
        self.assertEqual(caught.exception.code, "NOT_FOUND")
        with self.board_.store.db.write() as connection:
            connection.execute("UPDATE buddies SET available=0 WHERE model=?", (MODEL_SERVICE_B[2],))
        self.publish_limits(self.board_, {
            "adapter": MODEL_SERVICE_B[0], "provider": MODEL_SERVICE_B[1], "model": MODEL_SERVICE_B[2], "limit": 3,
        })
        page = self.board_.call("buddy_list", {"includeUnavailable": True})
        self.assertEqual(self.model_service_row(page, MODEL_SERVICE_B)["limit"], 3)


    def test_a_save_is_idempotent_under_its_command_receipt(self):
        board = self.board_
        payload = {
            "commandId": "idem-1",



            "modelServiceConcurrency": [{"adapter": MODEL_SERVICE_A[0], "provider": MODEL_SERVICE_A[1], "model": MODEL_SERVICE_A[2], "limit": 4}],
        }
        first = board.console_call("user_policy_publish", payload)
        self.assertFalse(first["duplicate"])
        # The exact replay returns the recorded response and writes nothing new.
        again = board.console_call("user_policy_publish", payload)
        self.assertTrue(again["duplicate"])
        with board.store.db.read() as connection:
            count = connection.execute("SELECT COUNT(*) AS count FROM model_service_concurrency").fetchone()["count"]
        self.assertEqual(count, 1)

    def test_a_setting_survives_discovery_marking_the_model_unavailable(self):
        board = self.board_
        self.publish_limits(board, {"adapter": MODEL_SERVICE_B[0], "provider": MODEL_SERVICE_B[1], "model": MODEL_SERVICE_B[2], "limit": 7})
        with board.store.db.write() as connection:
            connection.execute(
                "UPDATE buddies SET available=0 WHERE model=?", (MODEL_SERVICE_B[2],)
            )
        # The unavailable model service is no longer represented in the response, but the
        # stored setting remains and applies again when the model service returns.
        snapshot = board.call("console_snapshot", {})
        self.assertNotIn(MODEL_SERVICE_B, {(row["adapter"], row["provider"], row["model"]) for row in snapshot["modelServiceConcurrency"]})
        with board.store.db.read() as connection:
            row = connection.execute(
                "SELECT concurrency_limit FROM model_service_concurrency WHERE model=?", (MODEL_SERVICE_B[2],)
            ).fetchone()
        self.assertEqual(row["concurrency_limit"], 7)


class SaveAppliesAtClaimTests(ModelServiceConcurrencyTestCase):
    def setUp(self):
        super().setUp()
        self.board_ = self.board(max_concurrent=8)
        self.board_.call("model_catalog_refresh", {"requestId": "catalog-for-live-limits"})
        # Two live model-service-A attempts hold the default limit exactly.
        for index in range(2):
            self.register(self.board_, f"w-live{index}")
            self.claim = self.claim_governed(self.board_, f"w-live{index}", f"live-{index}")

    def test_lowering_never_kills_live_work_and_holds_new_claims(self):
        board = self.board_
        self.publish_limits(
            board, {"adapter": MODEL_SERVICE_A[0], "provider": MODEL_SERVICE_A[1], "model": MODEL_SERVICE_A[2], "limit": 1}
        )
        # The live attempts are untouched: no cancel, no state change.
        for index in range(2):
            view = board.call("task_get", {"requestId": f"live-{index}"})["task"]
            self.assertEqual(view["state"], "running")
        self.register(board, "w-next")
        blocked = self.claim_governed(board, "w-next", "next-1")
        self.assertIsNone(blocked["claim"])
        self.assertEqual(blocked["reason"], "model-capacity")

    def test_a_confirmed_completion_releases_the_lowered_model_service_slot(self):
        board = self.board_
        self.publish_limits(
            board, {"adapter": MODEL_SERVICE_A[0], "provider": MODEL_SERVICE_A[1], "model": MODEL_SERVICE_A[2], "limit": 1}
        )
        # Occupancy must drop *below* the lowered limit: both live attempts end
        # with confirmed shutdown before a new claim is admitted.
        self.finish(board, "w-live0", self.claim_governed_claim(board, "live-0"), shutdown=True)
        self.finish(board, "w-live1", self.claim_governed_claim(board, "live-1"), shutdown=True)
        self.register(board, "w-after")
        claim = self.claim_governed(board, "w-after", "after-1")
        self.assertIsNotNone(claim["claim"])

    def test_raising_applies_at_the_next_claim_without_a_restart(self):
        board = self.board_
        self.publish_limits(
            board, {"adapter": MODEL_SERVICE_A[0], "provider": MODEL_SERVICE_A[1], "model": MODEL_SERVICE_A[2], "limit": 3}
        )
        self.register(board, "w-raise")
        claim = self.claim_governed(board, "w-raise", "raise-1")
        self.assertIsNotNone(claim["claim"], claim)

    def claim_governed_claim(self, board, request_id: str):
        """Re-fetch the stored claim of an already-claimed fixture task."""
        with board.store.db.read() as connection:
            row = connection.execute(
                "SELECT attempt_id, generation FROM attempts WHERE task_id="
                "(SELECT task_id FROM tasks WHERE request_id=?)",
                (request_id,),
            ).fetchone()
        return {"claim": {"attempt": {"attemptId": row["attempt_id"], "generation": row["generation"]}}}


class RoutingQuotaTests(ModelServiceConcurrencyTestCase):
    def setUp(self):
        super().setUp()
        self.use_decision_helper()
        self.board_ = self.board(max_concurrent=8)
        self.seed_decision_buddy(self.board_)

    def request_selection(self, board, request_id: str) -> str:
        response = board.call(
            "selection_request",
            {"requestId": request_id, "task": "pick a coding buddy"},
        )
        return response["decisionId"]

    def test_the_selector_model_service_quota_precedes_the_claim(self):
        board = self.board_
        # The fixed decision buddy consumes MODEL_SERVICE_A; two live attempts fill it.
        for index in range(2):
            self.register(board, f"w-fill{index}")
            self.assertIsNotNone(self.claim_governed(board, f"w-fill{index}", f"fill-{index}")["claim"])
        decision_id = self.request_selection(board, "route-1")
        client = board.client()
        client.register_worker("w-dec", adapter="decision", capabilities=["decision"])
        blocked = client.claim("w-dec", "claim-route-1", "n" * 32)
        self.assertIsNone(blocked["claim"])
        self.assertEqual(blocked["reason"], "model-capacity")
        # Nothing was claimed and no attempt exists.
        with board.store.db.read() as connection:
            decision = connection.execute(
                "SELECT status FROM evaluation_decisions WHERE decision_id=?", (decision_id,)
            ).fetchone()
            request_row = connection.execute(
                "SELECT attempt_id FROM decision_requests WHERE decision_id=?", (decision_id,)
            ).fetchone()
        self.assertEqual(decision["status"], "queued")
        self.assertIsNone(request_row["attempt_id"])

    def test_a_freed_model_service_slot_lets_the_routing_task_claim_and_freeze_the_selector(self):
        board = self.board_
        self.register(board, "w-fill0")
        claim_a = self.claim_governed(board, "w-fill0", "route-fill-1")["claim"]
        self.assertIsNotNone(claim_a)
        decision_id = self.request_selection(board, "route-2")
        client = board.client()
        client.register_worker("w-dec", adapter="decision", capabilities=["decision"])
        # MODEL_SERVICE_A still has one free slot: the routing task claims now.
        claimed = client.claim("w-dec", "claim-route-2", "n" * 32)["claim"]
        self.assertIsNotNone(claimed)
        with board.store.db.read() as connection:
            request_row = connection.execute(
                "SELECT attempt_id FROM decision_requests WHERE decision_id=?", (decision_id,)
            ).fetchone()
        self.assertEqual(request_row["attempt_id"], claimed["attempt"]["attemptId"])
        # The claimed routing attempt now itself holds a model service slot: a second
        # governed MODEL_SERVICE_A task is blocked behind it.
        self.register(board, "w-after")
        blocked = self.claim_governed(board, "w-after", "route-after-1")
        self.assertIsNone(blocked["claim"])
        self.assertEqual(blocked["reason"], "model-capacity")

    def test_a_blocked_routing_task_does_not_hide_runnable_business_work(self):
        board = self.board_
        for index in range(2):
            self.register(board, f"w-block{index}")
            self.assertIsNotNone(self.claim_governed(board, f"w-block{index}", f"block-{index}")["claim"])
        # The routing task is blocked on the selector model service; a later, runnable
        # business task of another model service must still be reached by the scan.
        self.request_selection(board, "route-3")
        self.submit_governed(board, "route-3-business", model_service=MODEL_SERVICE_B)
        client = board.client()
        client.register_worker("w-biz", adapter="dsh", capabilities=["dsh"])
        claim = client.claim("w-biz", "claim-route-3", "n" * 32)["claim"]
        self.assertIsNotNone(claim, "a blocked routing task hid runnable business work")
        self.assertEqual(claim["task"]["requestId"], "route-3-business")


if __name__ == "__main__":
    unittest.main()
