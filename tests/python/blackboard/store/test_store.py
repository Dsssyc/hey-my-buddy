"""Current store facts: frozen attempt model services, shared admission, capacity.

Every test uses the production store and resource implementations against a private
state directory. Micro-task tasks use the deterministic workspace double, so real
admission, claim, attempt and result transactions run without a model call.
"""
from __future__ import annotations

import sqlite3
import unittest

from support import BoardTestCase
from blackboard.evaluation.test_evaluation import EvaluationTestCase as EvaluationFixtures

from hey_my_buddy.blackboard.store.db import SCHEMA_VERSION, Corruption
from hey_my_buddy.errors import BoardError
from hey_my_buddy.blackboard.store.store import BoardStore

MODEL_SERVICE_A = ("dsh", "deepseek-official", "deepseek-flash")
MODEL_SERVICE_B = ("dsh", "deepseek-official", "deepseek-v4-pro")


class StoreConcurrencyTestCase(EvaluationFixtures):
    """The evaluation fixtures plus micro-task submit/claim helpers."""

    def setUp(self):
        super().setUp()
        from unittest.mock import patch

        from hey_my_buddy.blackboard.tasks import workflow
        from mock_workspace import MockWorkspace

        self.model_workspace = MockWorkspace(self.directory)
        self.enterContext(patch.object(workflow, "_workspace_module", self.model_workspace))

    def submit_micro_task(self, board, request_id: str, model_service=MODEL_SERVICE_A, *, effort: str = "off") -> str:
        submitted = board.call(
            "workflow_submit",
            {
                "requestId": request_id,
                "hostId": "store-host",
                "task": "concurrency fixture",
                "cwd": str(self.workdir(request_id)),
                "harness": model_service[0],
                "provider": model_service[1],
                "model": model_service[2],
                "effort": effort,
                "executionWorkspace": {"kind": "existing", "access": "write"},
            },
        )
        return submitted["runId"]

    def claim_micro_task(self, board, worker_id: str, request_id: str, model_service=MODEL_SERVICE_A, *, effort: str = "off"):
        self.submit_micro_task(board, request_id, model_service, effort=effort)
        return board.client().claim(worker_id, f"claim-{request_id}", "n" * 32)

    def register(self, board, worker_id: str, *, adapter: str = "dsh"):
        board.client().register_worker(worker_id, adapter=adapter, capabilities=[adapter])

    def finish(self, board, worker_id: str, claim: dict, *, shutdown: bool = True):
        return board.client().submit_result(
            worker_id,
            claim["claim"]["attempt"]["attemptId"],
            claim["claim"]["attempt"]["generation"],
            "n" * 32,
            {"status": "ok", "result": {"finalText": "done"}, "shutdownConfirmed": shutdown, "exitCode": 0},
        )


class CurrentSchemaTests(BoardTestCase):
    def test_fresh_board_is_the_current_schema_with_the_model_service_concurrency_table(self):
        board = self.board()
        integrity = board.store.integrity()
        self.assertEqual(integrity["schemaVersion"], SCHEMA_VERSION)
        with board.store.db.read() as connection:
            columns = {
                row["name"]
                for row in connection.execute("PRAGMA table_info(model_service_concurrency)")
            }
            model_services = connection.execute("SELECT COUNT(*) AS count FROM model_service_concurrency").fetchone()["count"]
            attempt_columns = {
                row["name"]
                for row in connection.execute("PRAGMA table_info(attempts)")
            }
        self.assertEqual(
            columns,
            {"harness", "provider", "model", "concurrency_limit", "created_at", "updated_at"},
        )
        self.assertEqual(model_services, 0)
        self.assertTrue({"model_harness", "model_provider", "model_model"} <= attempt_columns)

    def test_an_older_schema_marker_is_refused_without_any_migration(self):
        directory = self.directory / "archive"
        store = BoardStore(directory)
        store.initialize()
        connection = sqlite3.connect(store.db.path)
        connection.execute("UPDATE meta SET value='10' WHERE key='schema_version'")
        connection.commit()
        connection.close()
        # Simulated schema-10 archive: the marker alone must make the current build
        # refuse to serve the file, with no importer and no conversion branch.
        with self.assertRaises(Corruption):
            BoardStore(directory).initialize()

    def test_capacity_report_is_the_total_ceiling_plus_model_service_rows(self):
        board = self.board(max_concurrent=4)
        capacity = board.store.capacity_report()
        self.assertEqual(capacity, {"totalLimit": 4, "totalActive": 0, "models": []})
        self.assertNotIn("business", capacity)
        self.assertNotIn("decision", capacity)


class FrozenModelServiceTests(StoreConcurrencyTestCase):
    def test_a_claim_freezes_the_resolved_model_service_onto_the_attempt(self):
        board = self.board()
        self.register(board, "w1")
        claim = self.claim_micro_task(board, "w1", "freeze-1")
        attempt = claim["claim"]["attempt"]
        with board.store.db.read() as connection:
            row = connection.execute(
                "SELECT model_harness, model_provider, model_model FROM attempts WHERE attempt_id=?",
                (attempt["attemptId"],),
            ).fetchone()
            self.assertIsNone(connection.execute(
                "SELECT value FROM meta WHERE key=?",
                (f"attempt-harness:{attempt['attemptId']}",),
            ).fetchone())
        self.assertEqual(tuple(row), MODEL_SERVICE_A)

    def test_a_command_attempt_has_no_model_service_and_counts_only_against_the_total(self):
        board = self.board(max_concurrent=2)
        client = board.client()
        for index in range(2):
            client.register_worker(f"w-cmd{index}", adapter="command", capabilities=["command"])
            submitted = client.submit(
                requestId=f"cmd-{index}",
                task="no model",
                cwd=str(self.workdir(f"cmd-{index}")),
                adapter="command",
                argv=["/bin/true"],
            )
            claim = client.claim(f"w-cmd{index}", f"claim-cmd-{index}", f"{index}" * 32, task_id=submitted["task"]["runId"])
        third = client.submit(
            requestId="cmd-2", task="no model either", cwd=str(self.workdir("cmd-2")),
            adapter="command", argv=["/bin/true"],
        )["task"]
        self.assertEqual(third["queueReason"], "capacity")
        client.register_worker("w-cmd2", adapter="command", capabilities=["command"])
        empty = client.claim("w-cmd2", "claim-cmd-2", "c" * 32)
        self.assertIsNone(empty["claim"])
        self.assertEqual(empty["reason"], "capacity")


class ModelServiceQuotaTests(StoreConcurrencyTestCase):
    def test_a_model_service_at_its_default_limit_blocks_only_that_model_service(self):
        board = self.board(max_concurrent=8)
        for index in range(2):
            self.register(board, f"w-a{index}")
            claim = self.claim_micro_task(board, f"w-a{index}", f"fam-a-{index}")
            self.assertIsNotNone(claim["claim"])
        # The third task of the same model service waits on the model service limit, not the
        # machine ceiling: a different model service still claims.
        self.register(board, "w-a2")
        blocked = self.claim_micro_task(board, "w-a2", "fam-a-2")
        self.assertIsNone(blocked["claim"])
        self.assertEqual(blocked["reason"], "model-capacity")
        with board.store.db.read() as connection:
            reason = connection.execute(
                "SELECT queue_reason FROM tasks WHERE request_id='fam-a-2'"
            ).fetchone()["queue_reason"]
        self.assertEqual(reason, "model-capacity")
        self.register(board, "w-b")
        other = self.claim_micro_task(board, "w-b", "fam-b-1", model_service=MODEL_SERVICE_B)
        self.assertIsNotNone(other["claim"])

    def test_effort_variants_share_one_model_service_limit(self):
        board = self.board(max_concurrent=8)
        efforts = ("off", "low")
        for index, effort in enumerate(efforts):
            self.register(board, f"w-e{index}")
            claim = self.claim_micro_task(board, f"w-e{index}", f"effort-{effort}", effort=effort)
            self.assertIsNotNone(claim["claim"])
        self.register(board, "w-e2")
        blocked = self.claim_micro_task(board, "w-e2", "effort-high", effort="high")
        self.assertIsNone(blocked["claim"])
        self.assertEqual(blocked["reason"], "model-capacity")

    def test_the_machine_ceiling_holds_across_model_services(self):
        board = self.board(max_concurrent=2)
        self.register(board, "w1")
        self.register(board, "w2")
        self.assertIsNotNone(self.claim_micro_task(board, "w1", "cap-1")["claim"])
        self.assertIsNotNone(self.claim_micro_task(board, "w2", "cap-2", model_service=MODEL_SERVICE_B)["claim"])
        self.register(board, "w3")
        blocked = self.claim_micro_task(board, "w3", "cap-3")
        self.assertIsNone(blocked["claim"])
        self.assertEqual(blocked["reason"], "capacity")

    def test_an_unconfirmed_result_keeps_its_model_service_slot(self):
        board = self.board(max_concurrent=8)
        self.register(board, "w1")
        self.register(board, "w2")
        first = self.claim_micro_task(board, "w1", "uncertain-1")
        second = self.claim_micro_task(board, "w2", "uncertain-2")
        # A completed result with unconfirmed shutdown leaves the attempt
        # uncertain: a survivor may still be running, so its model service slot stays.
        self.finish(board, "w2", second, shutdown=False)
        self.register(board, "w3")
        blocked = self.claim_micro_task(board, "w3", "uncertain-3")
        self.assertIsNone(blocked["claim"])
        self.assertEqual(blocked["reason"], "model-capacity")
        with board.store.db.read() as connection:
            row = connection.execute(
                "SELECT execution_state, model_harness, model_provider, model_model FROM attempts WHERE attempt_id=?",
                (second["claim"]["attempt"]["attemptId"],),
            ).fetchone()
        self.assertEqual(row["execution_state"], "uncertain")
        self.assertEqual((row["model_harness"], row["model_provider"], row["model_model"]), MODEL_SERVICE_A)
        # A confirmed completion frees the slot for the next claim.
        self.finish(board, "w1", first, shutdown=True)
        self.register(board, "w4")
        released = self.claim_micro_task(board, "w4", "uncertain-4")
        self.assertIsNotNone(released["claim"])

    def test_capacity_report_counts_total_and_per_model_service_occupancy(self):
        board = self.board(max_concurrent=8)
        self.register(board, "w1")
        self.register(board, "w2")
        self.register(board, "w3")
        self.claim_micro_task(board, "w1", "report-1")
        self.claim_micro_task(board, "w2", "report-2")
        self.claim_micro_task(board, "w3", "report-3", model_service=MODEL_SERVICE_B)
        # One more queued task of model service A so pending model services appear too.
        self.submit_micro_task(board, "report-queued")
        capacity = board.store.capacity_report()
        self.assertEqual(capacity["totalLimit"], 8)
        self.assertEqual(capacity["totalActive"], 3)
        rows = {(row["harness"], row["provider"], row["model"]): row for row in capacity["models"]}
        self.assertEqual(rows[MODEL_SERVICE_A]["limit"], 2)
        self.assertEqual(rows[MODEL_SERVICE_A]["active"], 2)
        self.assertEqual(rows[MODEL_SERVICE_B]["limit"], 2)
        self.assertEqual(rows[MODEL_SERVICE_B]["active"], 1)


class IdentityImmutabilityTests(StoreConcurrencyTestCase):
    def test_an_historical_attempt_never_changes_its_frozen_model_service(self):
        board = self.board()
        self.register(board, "w1")
        claim = self.claim_micro_task(board, "w1", "history-1")
        attempt_id = claim["claim"]["attempt"]["attemptId"]
        self.finish(board, "w1", claim, shutdown=True)
        # The stored model service survives completion and stays exactly what was frozen.
        with board.store.db.read() as connection:
            row = connection.execute(
                "SELECT model_harness, model_provider, model_model, execution_state FROM attempts WHERE attempt_id=?",
                (attempt_id,),
            ).fetchone()
        self.assertEqual((row["model_harness"], row["model_provider"], row["model_model"]), MODEL_SERVICE_A)
        self.assertEqual(row["execution_state"], "finished")


if __name__ == "__main__":
    unittest.main()


class ExpectedDurationTests(BoardTestCase):
    def submit(self, board, request_id, **duration):
        return board.client().submit(requestId=request_id, task="duration fixture", cwd=str(self.workdir()),
                                     adapter="command", argv=["/bin/true"], **duration)["task"]

    def test_command_default_and_explicit_duration_are_authoritative_board_facts(self):
        board = self.board()
        default = self.submit(board, "duration-default")
        explicit = self.submit(board, "duration-explicit", expectedSeconds=1)
        self.assertEqual(default["expectedSeconds"], 1800)
        self.assertEqual(explicit["expectedSeconds"], 1)
        self.assertNotIn("timeoutSeconds", default)
        self.assertNotIn("routerBudgetSeconds", explicit)
        with board.store.db.read() as connection:
            rows = connection.execute("SELECT expected_seconds,router_budget_seconds FROM tasks ORDER BY request_id").fetchall()
            self.assertEqual([tuple(row) for row in rows], [(1800, None), (1, None)])
        self.assertEqual(board.call("task_get", {"runId": explicit["runId"]})["task"]["expectedSeconds"], 1)

    def test_command_duration_rejects_zero_and_renamed_field_even_together(self):
        board = self.board()
        for duration in ({"expectedSeconds": 0}, {"expectedSeconds": -1}, {"expectedSeconds": None}):
            with self.subTest(duration=duration), self.assertRaises(BoardError):
                self.submit(board, "bad-duration", **duration)
        for duration in ({"timeoutSeconds": 60}, {"timeoutSeconds": 60, "expectedSeconds": 60}):
            with self.subTest(duration=duration), self.assertRaises(BoardError) as raised:
                self.submit(board, "renamed-duration", **duration)
            self.assertIn("renamed to expectedSeconds", raised.exception.message)

    def test_storage_rejects_a_mixed_duration_meaning(self):
        board = self.board()
        task = self.submit(board, "duration-storage")
        for update in ("expected_seconds=0", "router_budget_seconds=60", "expected_seconds=NULL"):
            with self.subTest(update=update), self.assertRaises(sqlite3.IntegrityError):
                with board.store.db.write() as connection:
                    connection.execute(f"UPDATE tasks SET {update} WHERE task_id=?", (task["runId"],))

    def test_deadline_reason_is_reserved_for_router_calls(self):
        board = self.board()
        task = self.submit(board, "duration-reason")
        client = board.client()
        client.register_worker("duration-worker", adapter="command", capabilities=["command"])
        claim = client.claim("duration-worker", "duration-claim", "n" * 32, task_id=task["runId"])["claim"]
        with self.assertRaises(BoardError) as raised:
            client.submit_result("duration-worker", claim["attempt"]["attemptId"], claim["attempt"]["generation"],
                                 "n" * 32, {"status": "failed", "shutdownConfirmed": True, "terminationReason": "deadline"})
        self.assertIn("Router budget", raised.exception.message)
        self.assertFalse(client.get(runId=task["runId"])["shutdownConfirmed"])
