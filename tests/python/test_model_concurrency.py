"""The frozen model-family concurrency contract of ADR-011, backend only.

One machine-wide ceiling plus per-family limits, persistent user settings through
the authenticated console publication path, the frozen attempt identity, the
snapshot/model_profiles/health observation shapes and the routing quota check that
happens before a selection reader is ever admitted.
"""
from __future__ import annotations

import os
import unittest
from unittest import mock
from pathlib import Path

from test_store import StoreConcurrencyTestCase

from buddy.errors import BoardError

FAMILY_A = ("dsh", "deepseek-official", "deepseek-flash")
FAMILY_B = ("dsh", "deepseek-official", "deepseek-v4-pro")
DECISION_PROFILE = "dsh:deepseek-official:deepseek-flash:off"


class ModelConcurrencyTestCase(StoreConcurrencyTestCase):
    """Governed fixtures plus the authenticated console policy writer."""

    def use_decision_helper(self) -> None:
        from fixtures import mock_readonly
        mock_readonly.install(self)

    def seed_decision_profile(self, board) -> None:
        board.call("model_catalog_refresh", {"requestId": "catalog-seed"})
        revision = board.call("console_snapshot", {})["tableRevision"]
        grant = board.console_call(
            "evaluation_write_begin", {"requestId": "seed-policy", "expectedRevision": revision, "kind": "human"}
        )
        board.console_call(
            "user_policy_publish",
            {
                "commandId": "seed-policy-1",
                "writerId": grant["writerId"],
                "generation": grant["generation"],
                "writerToken": grant["writerToken"],
                "expectedRevision": grant["tableRevision"],
                # A second enabled candidate keeps every selection request on the
                # Router path; a sole candidate would be selected by the program
                # directly and never reach the family quota check under test.
                "profileSettings": [{"profileId": DECISION_PROFILE, "enabled": True},
                                    {"profileId": "dsh:deepseek-official:deepseek-v4-pro:off", "enabled": True}],
                "configuration": {"defaultRoutingMode": "review", "reviewRouterProfileId": DECISION_PROFILE},
            },
        )

    def publish_limits(self, board, *patches: dict, command: str = "limits", writer_request: str | None = None) -> dict:
        revision = board.call("console_snapshot", {})["tableRevision"]
        grant = board.console_call(
            "evaluation_write_begin",
            {"requestId": writer_request or f"policy-{command}", "expectedRevision": revision, "kind": "human"},
        )
        return board.console_call(
            "user_policy_publish",
            {
                "commandId": command,
                "writerId": grant["writerId"],
                "generation": grant["generation"],
                "writerToken": grant["writerToken"],
                "expectedRevision": grant["tableRevision"],
                "modelConcurrency": list(patches),
            },
        )

    def family_row(self, payload: dict, family: tuple[str, str, str]) -> dict:
        rows = payload.get("modelConcurrency", payload.get("models"))
        return next(
            row
            for row in rows
            if (row["adapter"], row["provider"], row["model"]) == family
        )


class PublicationInterfaceTests(ModelConcurrencyTestCase):
    def setUp(self):
        super().setUp()
        self.board_ = self.board(max_concurrent=8)
        self.board_.call("model_catalog_refresh", {"requestId": "catalog-seed"})

    def test_snapshot_reports_one_row_per_represented_family_with_defaults(self):
        snapshot = self.board_.call("console_snapshot", {})
        rows = {(row["adapter"], row["provider"], row["model"]): row for row in snapshot["modelConcurrency"]}
        # The fixture catalog represents two dsh families; every row carries exactly
        # the frozen fields, effort variants collapse, and defaults apply.
        self.assertEqual(set(rows), {FAMILY_A, FAMILY_B})
        for row in rows.values():
            self.assertEqual(set(row), {"adapter", "provider", "model", "limit", "active"})
            self.assertEqual(row["limit"], 2)
            self.assertEqual(row["active"], 0)

    def test_model_profiles_pages_carry_the_same_family_rows(self):
        first = self.board_.call("model_profiles", {"limit": 1})
        self.assertEqual(len(first["modelConcurrency"]), 1)
        self.assertEqual(set(first["modelConcurrency"][0]), {"adapter", "provider", "model", "limit", "active"})
        full = self.board_.call("model_profiles", {"limit": 200})
        self.assertEqual(
            {(row["adapter"], row["provider"], row["model"]) for row in full["modelConcurrency"]},
            {FAMILY_A, FAMILY_B},
        )

    def test_profile_page_and_limit_are_from_the_same_published_revision(self):
        from buddy import catalog_store
        original = catalog_store.profiles
        old_revision = self.board_.call("console_snapshot", {})["tableRevision"]

        def concurrent_save(evaluation, params):
            page = original(evaluation, params)
            self.publish_limits(self.board_, {
                "adapter": FAMILY_A[0], "provider": FAMILY_A[1], "model": FAMILY_A[2], "limit": 5,
            }, command="save-between-page-and-response")
            return page

        with mock.patch.object(catalog_store, "profiles", side_effect=concurrent_save):
            page = self.board_.call("model_profiles", {"limit": 200})
        self.assertEqual(page["tableRevision"], old_revision)
        self.assertEqual(self.family_row(page, FAMILY_A)["limit"], 2)
        fresh = self.board_.call("console_snapshot", {})
        self.assertEqual(self.family_row(fresh, FAMILY_A)["limit"], 5)
        self.assertGreater(fresh["tableRevision"], old_revision)

    def test_active_counts_unresolved_attempts_of_the_family(self):
        board = self.board_
        self.register(board, "w-obs1")
        self.register(board, "w-obs2")
        self.claim_governed(board, "w-obs1", "obs-1")
        self.claim_governed(board, "w-obs2", "obs-2", family=FAMILY_B)
        snapshot = board.call("console_snapshot", {})
        self.assertEqual(self.family_row(snapshot, FAMILY_A)["active"], 1)
        self.assertEqual(self.family_row(snapshot, FAMILY_B)["active"], 1)
        profiles = board.call("model_profiles", {"limit": 200})
        self.assertEqual(self.family_row(profiles, FAMILY_A)["active"], 1)

    def test_health_capacity_reports_the_total_ceiling_and_family_rows(self):
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
        self.assertEqual(self.family_row(health["capacity"], FAMILY_A)["active"], 1)


class PolicyPatchTests(ModelConcurrencyTestCase):
    def setUp(self):
        super().setUp()
        self.board_ = self.board(max_concurrent=8)
        self.board_.call("model_catalog_refresh", {"requestId": "catalog-seed"})

    def test_the_authenticated_console_writer_sets_and_updates_limits(self):
        result = self.publish_limits(
            self.board_, {"adapter": FAMILY_A[0], "provider": FAMILY_A[1], "model": FAMILY_A[2], "limit": 5}
        )
        self.assertTrue(result["published"])
        self.assertEqual(result["counts"]["modelConcurrency"], 1)
        snapshot = self.board_.call("console_snapshot", {})
        self.assertEqual(self.family_row(snapshot, FAMILY_A)["limit"], 5)
        self.assertEqual(self.family_row(snapshot, FAMILY_B)["limit"], 2)
        # A later save updates only the named family; unrelated settings persist.
        self.publish_limits(
            self.board_,
            {"adapter": FAMILY_B[0], "provider": FAMILY_B[1], "model": FAMILY_B[2], "limit": 1},
            command="limits-2",
        )
        snapshot = self.board_.call("console_snapshot", {})
        self.assertEqual(self.family_row(snapshot, FAMILY_A)["limit"], 5)
        self.assertEqual(self.family_row(snapshot, FAMILY_B)["limit"], 1)

    def test_derived_and_unknown_fields_are_rejected(self):
        board = self.board_
        for patch in (
            {"adapter": FAMILY_A[0], "provider": FAMILY_A[1], "model": FAMILY_A[2], "limit": 2, "active": 0},
            {"adapter": FAMILY_A[0], "provider": FAMILY_A[1], "model": FAMILY_A[2], "effort": "off", "limit": 2},
            {"adapter": FAMILY_A[0], "provider": FAMILY_A[1], "limit": 2},
        ):
            with self.assertRaises(BoardError) as caught:
                self.publish_limits(board, patch)
            self.assertEqual(caught.exception.code, "INVALID_ARGUMENT")
        # Nothing was published by a rejected draft.
        with board.store.db.read() as connection:
            count = connection.execute("SELECT COUNT(*) AS count FROM model_concurrency").fetchone()["count"]
        self.assertEqual(count, 0)

    def test_limits_are_integers_between_one_and_thirty_two(self):
        board = self.board_
        for limit in (0, 33, -1, True, "4", 2.5, None):
            with self.assertRaises(BoardError):
                self.publish_limits(
                    board,
                    {"adapter": FAMILY_A[0], "provider": FAMILY_A[1], "model": FAMILY_A[2], "limit": limit},
                )

    def test_repeated_families_in_one_patch_are_refused(self):
        board = self.board_
        with self.assertRaises(BoardError) as caught:
            self.publish_limits(
                board,
                {"adapter": FAMILY_A[0], "provider": FAMILY_A[1], "model": FAMILY_A[2], "limit": 3},
                {"adapter": FAMILY_A[0], "provider": FAMILY_A[1], "model": FAMILY_A[2], "limit": 4},
            )
        self.assertEqual(caught.exception.code, "INVALID_ARGUMENT")

    def test_unknown_family_is_rejected_but_retained_unavailable_family_is_editable(self):
        with self.assertRaises(BoardError) as caught:
            self.publish_limits(self.board_, {
                "adapter": "zcode", "provider": "misspelled-provider", "model": "unknown", "limit": 3,
            })
        self.assertEqual(caught.exception.code, "NOT_FOUND")
        with self.board_.store.db.write() as connection:
            connection.execute("UPDATE evaluation_profiles SET available=0 WHERE model=?", (FAMILY_B[2],))
        self.publish_limits(self.board_, {
            "adapter": FAMILY_B[0], "provider": FAMILY_B[1], "model": FAMILY_B[2], "limit": 3,
        })
        page = self.board_.call("model_profiles", {"includeUnavailable": True})
        self.assertEqual(self.family_row(page, FAMILY_B)["limit"], 3)

    def test_maintenance_publications_cannot_touch_model_concurrency(self):
        board = self.board_
        revision = board.call("console_snapshot", {})["tableRevision"]
        grant = board.call(
            "evaluation_write_begin", {"requestId": "maint-1", "expectedRevision": revision, "kind": "maintenance"}
        )
        with self.assertRaises(BoardError) as caught:
            board.call(
                "assessment_publish",
                {
                    "commandId": "maint-1",
                    "writerId": grant["writerId"],
                    "generation": grant["generation"],
                    "writerToken": grant["writerToken"],
                    "expectedRevision": grant["tableRevision"],
                    "modelConcurrency": [
                        {"adapter": FAMILY_A[0], "provider": FAMILY_A[1], "model": FAMILY_A[2], "limit": 9}
                    ],
                },
            )
        self.assertEqual(caught.exception.code, "INVALID_ARGUMENT")

    def test_a_save_is_idempotent_under_its_command_receipt(self):
        board = self.board_
        revision = board.call("console_snapshot", {})["tableRevision"]
        grant = board.console_call(
            "evaluation_write_begin", {"requestId": "policy-idem", "expectedRevision": revision, "kind": "human"}
        )
        payload = {
            "commandId": "idem-1",
            "writerId": grant["writerId"],
            "generation": grant["generation"],
            "writerToken": grant["writerToken"],
            "expectedRevision": grant["tableRevision"],
            "modelConcurrency": [{"adapter": FAMILY_A[0], "provider": FAMILY_A[1], "model": FAMILY_A[2], "limit": 4}],
        }
        first = board.console_call("user_policy_publish", payload)
        self.assertFalse(first["duplicate"])
        # The exact replay returns the recorded response and writes nothing new.
        again = board.console_call("user_policy_publish", payload)
        self.assertTrue(again["duplicate"])
        with board.store.db.read() as connection:
            count = connection.execute("SELECT COUNT(*) AS count FROM model_concurrency").fetchone()["count"]
        self.assertEqual(count, 1)

    def test_a_setting_survives_discovery_marking_the_model_unavailable(self):
        board = self.board_
        self.publish_limits(board, {"adapter": FAMILY_B[0], "provider": FAMILY_B[1], "model": FAMILY_B[2], "limit": 7})
        with board.store.db.write() as connection:
            connection.execute(
                "UPDATE evaluation_profiles SET available=0 WHERE model=?", (FAMILY_B[2],)
            )
        # The unavailable family is no longer represented in the response, but the
        # stored setting remains and applies again when the family returns.
        snapshot = board.call("console_snapshot", {})
        self.assertNotIn(FAMILY_B, {(row["adapter"], row["provider"], row["model"]) for row in snapshot["modelConcurrency"]})
        with board.store.db.read() as connection:
            row = connection.execute(
                "SELECT concurrency_limit FROM model_concurrency WHERE model=?", (FAMILY_B[2],)
            ).fetchone()
        self.assertEqual(row["concurrency_limit"], 7)


class SaveAppliesAtClaimTests(ModelConcurrencyTestCase):
    def setUp(self):
        super().setUp()
        self.board_ = self.board(max_concurrent=8)
        self.board_.call("model_catalog_refresh", {"requestId": "catalog-for-live-limits"})
        # Two live family-A attempts hold the default limit exactly.
        for index in range(2):
            self.register(self.board_, f"w-live{index}")
            self.claim = self.claim_governed(self.board_, f"w-live{index}", f"live-{index}")

    def test_lowering_never_kills_live_work_and_holds_new_claims(self):
        board = self.board_
        self.publish_limits(
            board, {"adapter": FAMILY_A[0], "provider": FAMILY_A[1], "model": FAMILY_A[2], "limit": 1}
        )
        # The live attempts are untouched: no cancel, no state change.
        for index in range(2):
            view = board.call("task_get", {"requestId": f"live-{index}"})["task"]
            self.assertEqual(view["state"], "running")
        self.register(board, "w-next")
        blocked = self.claim_governed(board, "w-next", "next-1")
        self.assertIsNone(blocked["claim"])
        self.assertEqual(blocked["reason"], "model-capacity")

    def test_a_confirmed_completion_releases_the_lowered_family_slot(self):
        board = self.board_
        self.publish_limits(
            board, {"adapter": FAMILY_A[0], "provider": FAMILY_A[1], "model": FAMILY_A[2], "limit": 1}
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
            board, {"adapter": FAMILY_A[0], "provider": FAMILY_A[1], "model": FAMILY_A[2], "limit": 3}
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


class RoutingQuotaTests(ModelConcurrencyTestCase):
    def setUp(self):
        super().setUp()
        self.use_decision_helper()
        self.board_ = self.board(max_concurrent=8)
        self.seed_decision_profile(self.board_)

    def request_selection(self, board, request_id: str) -> str:
        response = board.call(
            "selection_request",
            {"requestId": request_id, "task": "pick a coding profile"},
        )
        return response["decisionId"]

    def test_the_selector_family_quota_precedes_the_claim_and_reader_grant(self):
        board = self.board_
        # The fixed decision profile consumes FAMILY_A; two live attempts fill it.
        for index in range(2):
            self.register(board, f"w-fill{index}")
            self.assertIsNotNone(self.claim_governed(board, f"w-fill{index}", f"fill-{index}")["claim"])
        decision_id = self.request_selection(board, "route-1")
        client = board.client()
        client.register_worker("w-dec", adapter="decision", capabilities=["decision"])
        blocked = client.claim("w-dec", "claim-route-1", "n" * 32)
        self.assertIsNone(blocked["claim"])
        self.assertEqual(blocked["reason"], "model-capacity")
        # Nothing was claimed, no reader was admitted and no attempt exists.
        with board.store.db.read() as connection:
            decision = connection.execute(
                "SELECT status FROM evaluation_decisions WHERE decision_id=?", (decision_id,)
            ).fetchone()
            readers = connection.execute("SELECT COUNT(*) AS count FROM evaluation_readers").fetchone()["count"]
            request_row = connection.execute(
                "SELECT attempt_id FROM decision_requests WHERE decision_id=?", (decision_id,)
            ).fetchone()
        self.assertEqual(decision["status"], "queued")
        self.assertEqual(readers, 0)
        self.assertIsNone(request_row["attempt_id"])

    def test_a_freed_family_slot_lets_the_routing_task_claim_and_freeze_the_selector(self):
        board = self.board_
        self.register(board, "w-fill0")
        claim_a = self.claim_governed(board, "w-fill0", "route-fill-1")["claim"]
        self.assertIsNotNone(claim_a)
        decision_id = self.request_selection(board, "route-2")
        client = board.client()
        client.register_worker("w-dec", adapter="decision", capabilities=["decision"])
        # FAMILY_A still has one free slot: the routing task claims now.
        claimed = client.claim("w-dec", "claim-route-2", "n" * 32)["claim"]
        self.assertIsNotNone(claimed)
        self.assertEqual(
            claimed["attempt"]["modelFamily"],
            {"adapter": FAMILY_A[0], "provider": FAMILY_A[1], "model": FAMILY_A[2]},
        )
        with board.store.db.read() as connection:
            readers = connection.execute("SELECT COUNT(*) AS count FROM evaluation_readers").fetchone()["count"]
            request_row = connection.execute(
                "SELECT attempt_id FROM decision_requests WHERE decision_id=?", (decision_id,)
            ).fetchone()
        self.assertEqual(readers, 1)
        self.assertEqual(request_row["attempt_id"], claimed["attempt"]["attemptId"])
        # The claimed routing attempt now itself holds a family slot: a second
        # governed FAMILY_A task is blocked behind it.
        self.register(board, "w-after")
        blocked = self.claim_governed(board, "w-after", "route-after-1")
        self.assertIsNone(blocked["claim"])
        self.assertEqual(blocked["reason"], "model-capacity")

    def test_a_blocked_routing_task_does_not_hide_runnable_business_work(self):
        board = self.board_
        for index in range(2):
            self.register(board, f"w-block{index}")
            self.assertIsNotNone(self.claim_governed(board, f"w-block{index}", f"block-{index}")["claim"])
        # The routing task is blocked on the selector family; a later, runnable
        # business task of another family must still be reached by the scan.
        self.request_selection(board, "route-3")
        self.submit_governed(board, "route-3-business", family=FAMILY_B)
        client = board.client()
        client.register_worker("w-biz", adapter="dsh", capabilities=["dsh"])
        claim = client.claim("w-biz", "claim-route-3", "n" * 32)["claim"]
        self.assertIsNotNone(claim, "a blocked routing task hid runnable business work")
        self.assertEqual(claim["task"]["requestId"], "route-3-business")


if __name__ == "__main__":
    unittest.main()
