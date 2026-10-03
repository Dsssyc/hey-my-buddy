"""Shared-capacity admission: bounded candidate windows cannot starve work.

ADR-011 replaced the separate business/decision lanes with one machine-wide ceiling
plus per-model-family limits. This file keeps the starvation guarantees that the
lane split used to carry, re-expressed for families: admission must skip full model
families *before* the bounded candidate window, arrival-order fairness holds among
eligible work, and no backlog of blocked tasks — however deep — can hide unrelated
runnable work.
"""
from __future__ import annotations

import unittest

from blackboard.store.test_store import StoreConcurrencyTestCase

FAMILY_A = ("dsh", "deepseek-official", "deepseek-flash")
FAMILY_B = ("dsh", "deepseek-official", "deepseek-v4-pro")
#: Comfortably above the scheduler's 50-row candidate window.
BACKLOG = 101


class FullFamilyBacklogTests(StoreConcurrencyTestCase):
    def setUp(self):
        super().setUp()
        # Two active attempts hold family A at its default limit of 2.
        self.board_ = self.board(max_concurrent=8)
        for index in range(2):
            self.register(self.board_, f"w-full{index}")
            claim = self.claim_governed(self.board_, f"w-full{index}", f"hold-{index}")
            self.assertIsNotNone(claim["claim"])

    def test_a_full_family_backlog_over_one_hundred_cannot_hide_other_families(self):
        board = self.board_
        for index in range(BACKLOG):
            self.submit_governed(board, f"backlog-{index}", effort=("off", "low", "high", "max")[index % 4])
        # The runnable task of another family arrives *after* the whole backlog, so
        # only the pre-window family filter can reach it: the bounded scan of 50
        # rows is exhausted long before row 102 otherwise.
        self.submit_governed(board, "runnable-b", family=FAMILY_B)
        self.register(board, "w-free")
        claim = board.client().claim("w-free", "claim-free", "n" * 32)
        self.assertIsNotNone(claim["claim"], claim["reason"])
        self.assertEqual(
            claim["claim"]["attempt"]["modelFamily"],
            {"adapter": FAMILY_B[0], "provider": FAMILY_B[1], "model": FAMILY_B[2]},
        )

    def test_a_full_family_backlog_cannot_hide_model_less_command_work(self):
        board = self.board_
        for index in range(BACKLOG):
            self.submit_governed(board, f"cmd-backlog-{index}")
        client = board.client()
        client.register_worker("w-command", adapter="command", capabilities=["command"])
        submitted = client.submit(
            requestId="command-runnable",
            task="no model at all",
            cwd=str(self.workdir("command-runnable")),
            adapter="command",
            argv=["/bin/true"],
        )
        claim = client.claim("w-command", "claim-command", "n" * 32)
        self.assertIsNotNone(claim["claim"], claim["reason"])
        self.assertEqual(claim["claim"]["task"]["runId"], submitted["task"]["runId"])
        self.assertIsNone(claim["claim"]["attempt"]["modelFamily"])

    def test_blocked_backlog_tasks_report_the_family_queue_reason(self):
        board = self.board_
        for index in range(BACKLOG):
            self.submit_governed(board, f"reason-{index}")
        with board.store.db.read() as connection:
            reasons = {
                row["queue_reason"]
                for row in connection.execute(
                    "SELECT queue_reason FROM tasks WHERE request_id LIKE 'reason-%'"
                )
            }
        self.assertEqual(reasons, {"model-capacity"})

    def test_an_empty_claim_names_the_full_family_not_an_idle_board(self):
        board = self.board_
        for index in range(BACKLOG):
            self.submit_governed(board, f"idle-{index}")
        self.register(board, "w-empty")
        empty = board.client().claim("w-empty", "claim-empty", "n" * 32)
        self.assertIsNone(empty["claim"])
        self.assertEqual(empty["reason"], "model-capacity")


class ArrivalOrderTests(StoreConcurrencyTestCase):
    def test_eligible_work_is_claimed_in_arrival_order(self):
        board = self.board(max_concurrent=8)
        self.register(board, "w1")
        self.register(board, "w2")
        self.assertIsNotNone(self.claim_governed(board, "w1", "order-1", family=FAMILY_A)["claim"])
        self.assertIsNotNone(self.claim_governed(board, "w2", "order-2", family=FAMILY_A)["claim"])
        # Family A is full; families B and C are both eligible and queued in that
        # order, behind one more blocked family-A task. A scan claim must respect
        # the arrival order of eligible work, not adapter, family or any hidden
        # priority, and must skip the blocked task without visiting it.
        self.submit_governed(board, "order-3", family=FAMILY_A)
        self.submit_governed(board, "order-4", family=FAMILY_B)
        self.register(board, "w3")
        third = board.client().claim("w3", "claim-order-3", "n" * 32)["claim"]
        self.assertEqual(third["task"]["requestId"], "order-4")
        self.assertEqual(
            third["attempt"]["modelFamily"],
            {"adapter": FAMILY_B[0], "provider": FAMILY_B[1], "model": FAMILY_B[2]},
        )

    def test_the_machine_ceiling_is_shared_by_routing_and_business_attempts(self):
        board = self.board(max_concurrent=2)
        self.register(board, "w1")
        self.register(board, "w2")
        self.assertIsNotNone(self.claim_governed(board, "w1", "shared-1")["claim"])
        self.assertIsNotNone(self.claim_governed(board, "w2", "shared-2", family=FAMILY_B)["claim"])
        self.register(board, "w3")
        blocked = self.claim_governed(board, "w3", "shared-3")
        self.assertIsNone(blocked["claim"])
        self.assertEqual(blocked["reason"], "capacity")


if __name__ == "__main__":
    unittest.main()
