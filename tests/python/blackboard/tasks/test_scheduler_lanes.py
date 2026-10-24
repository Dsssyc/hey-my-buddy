"""Shared-capacity admission: bounded candidate windows cannot starve work.

ADR-011 replaced the separate business/decision lanes with one machine-wide ceiling
plus per-model-service limits. This file keeps the starvation guarantees that the
lane split used to carry, re-expressed for model services: admission must skip full model
services *before* the bounded candidate window, arrival-order fairness holds among
eligible work, and no backlog of blocked tasks — however deep — can hide unrelated
runnable work.
"""
from __future__ import annotations
from admission import admit_execution

import unittest

from blackboard.store.test_store import StoreConcurrencyTestCase

MODEL_SERVICE_A = ("dsh", "deepseek-official", "deepseek-flash")
MODEL_SERVICE_B = ("dsh", "deepseek-official", "deepseek-v4-pro")
#: Comfortably above the scheduler's 50-row candidate window.
BACKLOG = 101


class FullModelServiceBacklogTests(StoreConcurrencyTestCase):
    def setUp(self):
        super().setUp()
        # Two active attempts hold model service A at its default limit of 2.
        self.board_ = self.board(max_concurrent=8)
        for index in range(2):
            self.register(self.board_, f"w-full{index}")
            claim = self.claim_micro_task(self.board_, f"w-full{index}", f"hold-{index}")
            self.assertIsNotNone(claim["claim"])

    def test_a_full_model_service_backlog_over_one_hundred_cannot_hide_other_model_services(self):
        board = self.board_
        for index in range(BACKLOG):
            self.submit_micro_task(board, f"backlog-{index}", effort=("off", "low", "high", "max")[index % 4])
        # The runnable task of another model service arrives *after* the whole backlog, so
        # only the pre-window model service filter can reach it: the bounded scan of 50
        # rows is exhausted long before row 102 otherwise.
        self.submit_micro_task(board, "runnable-b", model_service=MODEL_SERVICE_B)
        self.register(board, "w-free")
        claim = admit_execution(board.client(), "n" * 32)
        self.assertIsNotNone(claim["claim"], claim["reason"])
        self.assertEqual(claim["claim"]["task"]["requestId"], "runnable-b")

    def test_a_full_model_service_backlog_cannot_hide_model_less_command_work(self):
        board = self.board_
        for index in range(BACKLOG):
            self.submit_micro_task(board, f"cmd-backlog-{index}")
        client = board.client()
        pass  # Admission is owned by the service.
        submitted = board.submit_execution(
            requestId="command-runnable",
            task="no model at all",
            cwd=str(self.workdir("command-runnable")),
            adapter="command",
            argv=["/bin/true"],
        )
        claim = admit_execution(client, "n" * 32)
        self.assertIsNotNone(claim["claim"], claim["reason"])
        self.assertEqual(claim["claim"]["task"]["taskId"], submitted["task"]["runId"])

    def test_blocked_backlog_tasks_report_the_model_service_queue_reason(self):
        board = self.board_
        for index in range(BACKLOG):
            self.submit_micro_task(board, f"reason-{index}")
        with board.store.db.read() as connection:
            reasons = {
                row["queue_reason"]
                for row in connection.execute(
                    "SELECT queue_reason FROM tasks WHERE request_id LIKE 'reason-%'"
                )
            }
        self.assertEqual(reasons, {"model-capacity"})

    def test_an_empty_claim_names_the_full_model_service_not_an_idle_board(self):
        board = self.board_
        for index in range(BACKLOG):
            self.submit_micro_task(board, f"idle-{index}")
        self.register(board, "w-empty")
        empty = admit_execution(board.client(), "n" * 32)
        self.assertIsNone(empty["claim"])
        self.assertEqual(empty["reason"], "model-capacity")


class ArrivalOrderTests(StoreConcurrencyTestCase):
    def test_eligible_work_is_claimed_in_arrival_order(self):
        board = self.board(max_concurrent=8)
        self.register(board, "w1")
        self.register(board, "w2")
        self.assertIsNotNone(self.claim_micro_task(board, "w1", "order-1", model_service=MODEL_SERVICE_A)["claim"])
        self.assertIsNotNone(self.claim_micro_task(board, "w2", "order-2", model_service=MODEL_SERVICE_A)["claim"])
        # Model service A is full; model services B and C are both eligible and queued in that
        # order, behind one more blocked model-service-A task. A scan claim must respect
        # the arrival order of eligible work, not adapter, model service or any hidden
        # priority, and must skip the blocked task without visiting it.
        self.submit_micro_task(board, "order-3", model_service=MODEL_SERVICE_A)
        self.submit_micro_task(board, "order-4", model_service=MODEL_SERVICE_B)
        self.register(board, "w3")
        third = admit_execution(board.client(), "n" * 32)["claim"]
        self.assertEqual(third["task"]["requestId"], "order-4")

    def test_the_machine_ceiling_is_shared_by_routing_and_business_attempts(self):
        board = self.board(max_concurrent=2)
        self.register(board, "w1")
        self.register(board, "w2")
        self.assertIsNotNone(self.claim_micro_task(board, "w1", "shared-1")["claim"])
        self.assertIsNotNone(self.claim_micro_task(board, "w2", "shared-2", model_service=MODEL_SERVICE_B)["claim"])
        self.register(board, "w3")
        blocked = self.claim_micro_task(board, "w3", "shared-3")
        self.assertIsNone(blocked["claim"])
        self.assertEqual(blocked["reason"], "capacity")


if __name__ == "__main__":
    unittest.main()
