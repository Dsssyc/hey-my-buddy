"""Cumulative facts share lifecycle transactions and outlive deleted records."""
from contextlib import ExitStack
from datetime import datetime, timedelta
import sqlite3
from unittest.mock import patch

from blackboard.tasks.test_workflow import WorkflowTestCase, BUDDY
from hey_my_buddy.blackboard.routing import statistics, maintenance_data
from hey_my_buddy.blackboard.routing.evidence import domain_statistics
from hey_my_buddy.blackboard.tasks import macro_task_deletion


class StatisticsStorageTests(WorkflowTestCase):
    def setUp(self):
        super().setUp()
        self.board_ = self.board()
        self.run = self.submit(self.board_)
        claim = self.claim(self.board_, run_id=self.run["runId"])
        self.finish_turn(self.board_, claim)
        self.delivered = self.board_.call("workflow_get", {"runId": self.run["runId"]})

    def facts(self):
        with self.board_.store.db.read() as db:
            return domain_statistics(db, [{"buddy_id": "fixture", **BUDDY}])["fixture"]

    def maintenance(self):
        task_id = self.board_.submit_execution(
            requestId="maintenance", task="Model-free maintenance fixture", adapter="command", argv=["true"],
            cwd=str(self.workdir("maintenance")))["task"]["runId"]
        with self.board_.store.db.write() as db:
            now = self.board_.store.now()
            db.execute("INSERT INTO maintenance_runs VALUES('maintenance','manual','fixture',?,'completed',NULL,NULL,?,?)",
                       (task_id, now, now))
            packet = maintenance_data.build_packet(db)
        answer = {"newNodes": [{"id": "root", "name": "Root", "definition": "Work", "parentId": None}],
                  "merges": [], "classifications": [{"runId": self.run["runId"], "domainNodeId": "root", "taskType": "verify"}],
                  "summaries": [{**{field: kind[field] for field in statistics.KIND}, "text": "Observed work", "citations": []}
                                for kind in packet["kinds"]]}
        return packet, answer

    def test_acceptance_and_conclusion_roll_back_with_their_statistics(self):
        original = statistics._add_sample

        def fail_after_write(*args):
            original(*args)
            raise RuntimeError("after-statistics-write")

        for action in ("accept", "conclude"):
            with self.subTest(action=action):
                with patch.object(statistics, "_add_sample", side_effect=fail_after_write), self.assertLogs(level="ERROR"):
                    with self.assertRaisesRegex(RuntimeError, "after-statistics-write"):
                        if action == "accept":
                            self.accept_run(self.board_, self.delivered)
                        else:
                            self.board_.call("workflow_conclude", {"runId": self.run["runId"],
                                            "note": "Observed failure", **self.control(self.run)})
                self.assertEqual(self.facts()["overall"]["samples"], 0)
                with self.board_.store.db.read() as db:
                    self.assertEqual(db.execute("SELECT state FROM workflow_runs WHERE run_id=?", (self.run["runId"],)).fetchone()[0], "delivered")
                    self.assertEqual(db.execute("SELECT COUNT(*) FROM workflow_integrations").fetchone()[0], 0)
                    self.assertEqual(db.execute("SELECT COUNT(*) FROM workflow_host_conclusions").fetchone()[0], 0)
                    self.assertEqual(db.execute("SELECT COUNT(*) FROM kind_statistics_buckets").fetchone()[0], 0)
        self.accept_run(self.board_, self.delivered)
        self.assertEqual(self.facts()["overall"]["samples"], 1)

    def test_classification_and_cell_accumulation_roll_back_together(self):
        self.accept_run(self.board_, self.delivered)
        packet, answer = self.maintenance()
        original = maintenance_data.accumulate_cell

        def fail_after_write(*args):
            original(*args)
            raise RuntimeError("after-cell-write")

        with self.assertRaisesRegex(RuntimeError, "after-cell-write"):
            with self.board_.store.db.write() as db, patch.object(maintenance_data, "accumulate_cell", side_effect=fail_after_write):
                maintenance_data.apply_answer(db, packet=packet, answer=answer, maintenance_run_id="maintenance")
        self.assertEqual(self.facts()["overall"]["samples"], 1)
        self.assertEqual(self.facts()["byDomain"], {})
        with self.board_.store.db.write() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM micro_task_classifications").fetchone()[0], 0)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM kind_cell_statistics_buckets").fetchone()[0], 0)
            result = maintenance_data.apply_answer(db, packet=packet, answer=answer, maintenance_run_id="maintenance")
        self.assertEqual(result["errors"], [])
        self.assertEqual(self.facts()["byDomain"]["root"]["samples"], 1)

    def test_later_shutdown_proof_refreshes_time_without_counting_another_round(self):
        run = self.submit(self.board_, request_id="unconfirmed", cwd=str(self.workdir("unconfirmed")))
        claim = self.claim(self.board_, run_id=run["runId"])
        attempt_id = claim["claim"]["attempt"]["attemptId"]
        # Drive the store's service-owned import and its later shutdown supplement.
        # A native receipt with unknown shutdown stays deferred at the outer
        # service boundary, so this is the core settlement transaction directly.
        self.board_.store.execution_result({"attemptId": attempt_id, "status": "failed",
            "shutdownConfirmed": False, "terminationReason": "harness-error",
            "result": {"status": "failed", "code": "harness-unavailable"}}, stop_evidence={})
        self.assertEqual(self.facts()["infrastructureFailures"]["count"], 1)
        later = "2099-01-01T00:00:00.000Z"
        with patch.object(self.board_.store, "now", return_value=later):
            self.board_.store.confirm_execution_shutdown(attempt_id, {"kind": "identity-absent"})
        self.assertEqual(self.facts()["infrastructureFailures"], {"count": 1, "lastAt": later})
        self.board_.store.confirm_execution_shutdown(attempt_id, {"kind": "identity-absent"})
        self.assertEqual(self.facts()["infrastructureFailures"], {"count": 1, "lastAt": later})

    def test_deleting_the_micro_task_preserves_all_accumulated_facts(self):
        self.accept_run(self.board_, self.delivered)
        packet, answer = self.maintenance()
        with self.board_.store.db.write() as db:
            maintenance_data.apply_answer(db, packet=packet, answer=answer, maintenance_run_id="maintenance")
            statistics.accumulate_infrastructure(db, BUDDY,
                {"terminationReason": "harness-error", "result": {"code": "harness-unavailable"}}, self.board_.store.now())
        before = self.facts()
        with ExitStack() as stack:
            for stage in ("_delete_sessions", "_delete_workspaces", "_delete_private_roots", "_delete_cli_files"):
                stack.enter_context(patch.object(macro_task_deletion, stage, return_value=[]))
            deleted = macro_task_deletion.delete(self.board_.store, "run:" + self.run["runId"], 1)
        self.assertEqual(deleted["state"], "deleted")
        self.assertEqual(self.facts(), before)
        with self.board_.store.db.read() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM workflow_runs").fetchone()[0], 0)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM micro_task_classifications").fetchone()[0], 0)
            self.assertEqual(db.execute("PRAGMA foreign_key_check").fetchall(), [])

    def test_statistics_constraints_reject_invalid_counts_and_unbound_buckets(self):
        self.accept_run(self.board_, self.delivered)
        with self.board_.store.db.write() as db:
            for field, value in (("samples", 0), ("samples", 2), ("samples", 1.5),
                                 ("successes", -1), ("first_passes", 2), ("first_passes", -1),
                                 ("rework_rounds", -1), ("failures", 1), ("model", None)):
                with self.subTest(field=field, value=value), self.assertRaises(sqlite3.IntegrityError):
                    db.execute(f"UPDATE kind_statistics SET {field}=?", (value,))
            values = (*statistics.key(BUDDY, statistics.KIND), "input_tokens", 1, 0, 1)
            db.execute("INSERT INTO kind_statistics_buckets VALUES(?,?,?,?,?,?,?)", values)
            for field, value in (("count", 0), ("count", 1.5), ("sign", 2), ("sign", -1),
                                 ("bucket", 0.5), ("metric", "unknown"), ("model", "unbound")):
                with self.subTest(field=field, value=value), self.assertRaises(sqlite3.IntegrityError):
                    db.execute(f"UPDATE kind_statistics_buckets SET {field}=? WHERE metric='input_tokens'", (value,))

    def test_wall_clock_rollback_does_not_prevent_acceptance(self):
        with self.board_.store.db.write() as db:
            row = db.execute("SELECT attempt_id,started_at FROM attempts WHERE task_id=?", (self.run["runId"],)).fetchone()
            earlier = datetime.fromisoformat(row["started_at"].replace("Z", "+00:00")) - timedelta(seconds=10)
            db.execute("UPDATE attempts SET finished_at=? WHERE attempt_id=?",
                       (earlier.isoformat().replace("+00:00", "Z"), row["attempt_id"]))
        self.accept_run(self.board_, self.delivered)
        actual = self.facts()["overall"]["medianDeliverySeconds"]
        self.assertGreaterEqual(actual, -10 * 1.2**0.5)
        self.assertLessEqual(actual, -10 / 1.2**0.5)
