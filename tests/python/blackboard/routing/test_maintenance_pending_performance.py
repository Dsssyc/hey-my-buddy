"""Measure maintenance pending scans over a private board of ended micro tasks."""
from __future__ import annotations

import json
from pathlib import Path
from statistics import median
from time import perf_counter

from blackboard.tasks.test_workflow import BUDDY, WorkflowTestCase
from hey_my_buddy.blackboard.routing.maintenance_data import pending, pending_count


TASK_COUNT = 600
SAMPLES = 9
OUTPUT = Path(__file__).resolve().parents[4] / "tmp" / "maintenance-pending-benchmark.json"


class MaintenancePendingPerformanceTests(WorkflowTestCase):
    def test_pending_scans_with_600_ended_tasks(self):
        board = self.board()
        run_ids = []
        for number in range(TASK_COUNT):
            submitted = self.submit(
                board, request_id=f"pending-benchmark-{number}", task=f"Task {number}",
                cwd=str(self.workdir(f"task-{number}")), kind="worktree",
            )
            run_ids.append(submitted["runId"])
            self.finish_turn(board, self.claim(board, run_id=submitted["runId"]))
            if number % 2:
                board.call("workflow_conclude", {
                    "runId": submitted["runId"], "note": "Fixture task concluded",
                    **self.control(submitted),
                })
            else:
                self.accept_run(board, board.call("workflow_get", {"runId": submitted["runId"]}))

        def measure(connection, expected_pending, expected_count):
            samples = {"pendingMs": [], "pendingCountMs": []}
            for _ in range(SAMPLES):
                started = perf_counter()
                result = pending(connection)
                samples["pendingMs"].append((perf_counter() - started) * 1000)
                self.assertIs(result, expected_pending)

                started = perf_counter()
                count = pending_count(connection)
                samples["pendingCountMs"].append((perf_counter() - started) * 1000)
                self.assertEqual(count, expected_count)
            return {
                "pendingMs": {"samples": samples["pendingMs"], "median": median(samples["pendingMs"])},
                "pendingCountMs": {"samples": samples["pendingCountMs"],
                                   "median": median(samples["pendingCountMs"])},
            }

        with board.store.db.read() as connection:
            self.assertEqual(connection.execute(
                "SELECT COUNT(*) FROM workflow_runs WHERE state='accepted'").fetchone()[0], TASK_COUNT // 2)
            self.assertEqual(connection.execute(
                "SELECT COUNT(*) FROM workflow_runs WHERE state='concluded'").fetchone()[0], TASK_COUNT // 2)
            self.assertEqual(connection.execute(
                "SELECT COUNT(*) FROM tasks WHERE accepted_at IS NOT NULL").fetchone()[0], TASK_COUNT // 2)
            self.assertEqual(connection.execute(
                "SELECT COUNT(*) FROM workflow_host_conclusions").fetchone()[0], TASK_COUNT // 2)
            self.assertEqual(connection.execute(
                "SELECT COUNT(*) FROM micro_task_classifications").fetchone()[0], 0)
            self.assertEqual(connection.execute(
                "SELECT COUNT(*) FROM kind_summary_evidence").fetchone()[0], 0)
            unprocessed = measure(connection, True, TASK_COUNT)

        maintenance_task_id = board.submit_execution(
            requestId="pending-benchmark-maintenance", task="Synthetic maintenance record",
            adapter="command", argv=["true"], cwd=str(self.workdir("maintenance")),
        )["task"]["runId"]
        maintenance_id = "pending-benchmark-maintenance"
        kind = (BUDDY["harness"], BUDDY["model"], BUDDY["effort"])
        with board.store.db.write() as connection:
            now = board.store.now()
            connection.execute(
                "INSERT INTO maintenance_runs VALUES(?, 'manual','fixture',?, 'completed',NULL,NULL,?,?)",
                (maintenance_id, maintenance_task_id, now, now),
            )
            connection.execute(
                "INSERT INTO domain_nodes VALUES('benchmark-domain','Benchmark','Fixture',NULL,NULL,?)",
                (maintenance_id,),
            )
            connection.executemany(
                "INSERT INTO micro_task_classifications VALUES(?,'benchmark-domain','verify',?)",
                [(run_id, maintenance_id) for run_id in run_ids],
            )
            # Classification alone leaves every ability turn unread by its kind.
            self.assertEqual(pending_count(connection), TASK_COUNT)
            connection.execute(
                "INSERT INTO kind_summaries VALUES(?,?,?,?,?)",
                (*kind, "Fixture summary", maintenance_id),
            )
            connection.executemany(
                "INSERT INTO kind_summary_evidence VALUES(?,?,?,?,0)",
                [(*kind, run_id) for run_id in run_ids],
            )

        with board.store.db.read() as connection:
            processed = measure(connection, False, 0)

        OUTPUT.parent.mkdir(parents=True, exist_ok=True)
        OUTPUT.write_text(json.dumps({
            "microTasks": TASK_COUNT,
            "accepted": TASK_COUNT // 2,
            "concluded": TASK_COUNT // 2,
            "samplesPerQuery": SAMPLES,
            "unprocessed": unprocessed,
            "processed": processed,
        }, indent=2) + "\n", encoding="utf-8")
