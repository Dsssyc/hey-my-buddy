"""Schema 11 store facts: frozen attempt families, shared admission, capacity.

Every test uses the production store and resource implementations against a private
state directory. Governed tasks use the deterministic workspace double, so real
admission, claim, attempt and result transactions run without a model call.
"""
from __future__ import annotations

import sqlite3
import unittest

from support import BoardTestCase
from test_evaluation import EvaluationTestCase as EvaluationFixtures

from buddy.db import Corruption
from buddy.store import BoardStore

FAMILY_A = ("dsh", "deepseek-official", "deepseek-flash")
FAMILY_B = ("dsh", "deepseek-official", "deepseek-v4-pro")


class StoreConcurrencyTestCase(EvaluationFixtures):
    """The evaluation fixtures plus governed submit/claim helpers."""

    def setUp(self):
        super().setUp()
        from unittest.mock import patch

        from buddy import workflow
        from mock_workspace import MockWorkspace

        self.model_workspace = MockWorkspace(self.directory)
        self.enterContext(patch.object(workflow, "_workspace_module", self.model_workspace))

    def submit_governed(self, board, request_id: str, family=FAMILY_A, *, effort: str = "off") -> str:
        submitted = board.call(
            "workflow_submit",
            {
                "requestId": request_id,
                "hostId": "store-host",
                "task": "concurrency fixture",
                "cwd": str(self.workdir(request_id)),
                "adapter": family[0],
                "provider": family[1],
                "model": family[2],
                "effort": effort,
                "executionWorkspace": {"kind": "existing", "access": "write"},
            },
        )
        return submitted["runId"]

    def claim_governed(self, board, worker_id: str, request_id: str, family=FAMILY_A, *, effort: str = "off"):
        self.submit_governed(board, request_id, family, effort=effort)
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


class SchemaElevenTests(BoardTestCase):
    def test_fresh_board_is_schema_11_with_the_model_concurrency_table(self):
        board = self.board()
        integrity = board.store.integrity()
        self.assertEqual(integrity["schemaVersion"], 11)
        with board.store.db.read() as connection:
            columns = {
                row["name"]
                for row in connection.execute("PRAGMA table_info(model_concurrency)")
            }
            families = connection.execute("SELECT COUNT(*) AS count FROM model_concurrency").fetchone()["count"]
            attempt_columns = {
                row["name"]
                for row in connection.execute("PRAGMA table_info(attempts)")
            }
        self.assertEqual(
            columns,
            {"adapter", "provider", "model", "concurrency_limit", "updated_revision", "created_at", "updated_at"},
        )
        self.assertEqual(families, 0)
        self.assertTrue({"model_adapter", "model_provider", "model_model"} <= attempt_columns)

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

    def test_capacity_report_is_the_total_ceiling_plus_family_rows(self):
        board = self.board(max_concurrent=4)
        capacity = board.store.capacity_report()
        self.assertEqual(capacity, {"totalLimit": 4, "totalActive": 0, "models": []})
        self.assertNotIn("business", capacity)
        self.assertNotIn("decision", capacity)


class FrozenFamilyTests(StoreConcurrencyTestCase):
    def test_a_claim_freezes_the_resolved_family_onto_the_attempt(self):
        board = self.board()
        self.register(board, "w1")
        claim = self.claim_governed(board, "w1", "freeze-1")
        attempt = claim["claim"]["attempt"]
        self.assertEqual(
            attempt["modelFamily"],
            {"adapter": FAMILY_A[0], "provider": FAMILY_A[1], "model": FAMILY_A[2]},
        )
        with board.store.db.read() as connection:
            row = connection.execute(
                "SELECT model_adapter, model_provider, model_model FROM attempts WHERE attempt_id=?",
                (attempt["attemptId"],),
            ).fetchone()
        self.assertEqual(tuple(row), FAMILY_A)

    def test_a_command_attempt_has_no_family_and_counts_only_against_the_total(self):
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
            self.assertIsNone(claim["claim"]["attempt"]["modelFamily"])
        third = client.submit(
            requestId="cmd-2", task="no model either", cwd=str(self.workdir("cmd-2")),
            adapter="command", argv=["/bin/true"],
        )["task"]
        self.assertEqual(third["queueReason"], "capacity")
        client.register_worker("w-cmd2", adapter="command", capabilities=["command"])
        empty = client.claim("w-cmd2", "claim-cmd-2", "c" * 32)
        self.assertIsNone(empty["claim"])
        self.assertEqual(empty["reason"], "capacity")


class FamilyQuotaTests(StoreConcurrencyTestCase):
    def test_a_family_at_its_default_limit_blocks_only_that_family(self):
        board = self.board(max_concurrent=8)
        for index in range(2):
            self.register(board, f"w-a{index}")
            claim = self.claim_governed(board, f"w-a{index}", f"fam-a-{index}")
            self.assertIsNotNone(claim["claim"])
        # The third task of the same family waits on the family limit, not the
        # machine ceiling: a different family still claims.
        self.register(board, "w-a2")
        blocked = self.claim_governed(board, "w-a2", "fam-a-2")
        self.assertIsNone(blocked["claim"])
        self.assertEqual(blocked["reason"], "model-capacity")
        with board.store.db.read() as connection:
            reason = connection.execute(
                "SELECT queue_reason FROM tasks WHERE request_id='fam-a-2'"
            ).fetchone()["queue_reason"]
        self.assertEqual(reason, "model-capacity")
        self.register(board, "w-b")
        other = self.claim_governed(board, "w-b", "fam-b-1", family=FAMILY_B)
        self.assertIsNotNone(other["claim"])
        self.assertEqual(
            other["claim"]["attempt"]["modelFamily"],
            {"adapter": FAMILY_B[0], "provider": FAMILY_B[1], "model": FAMILY_B[2]},
        )

    def test_effort_variants_share_one_family_limit(self):
        board = self.board(max_concurrent=8)
        efforts = ("off", "low")
        for index, effort in enumerate(efforts):
            self.register(board, f"w-e{index}")
            claim = self.claim_governed(board, f"w-e{index}", f"effort-{effort}", effort=effort)
            self.assertIsNotNone(claim["claim"])
        self.register(board, "w-e2")
        blocked = self.claim_governed(board, "w-e2", "effort-high", effort="high")
        self.assertIsNone(blocked["claim"])
        self.assertEqual(blocked["reason"], "model-capacity")

    def test_the_machine_ceiling_holds_across_families(self):
        board = self.board(max_concurrent=2)
        self.register(board, "w1")
        self.register(board, "w2")
        self.assertIsNotNone(self.claim_governed(board, "w1", "cap-1")["claim"])
        self.assertIsNotNone(self.claim_governed(board, "w2", "cap-2", family=FAMILY_B)["claim"])
        self.register(board, "w3")
        blocked = self.claim_governed(board, "w3", "cap-3")
        self.assertIsNone(blocked["claim"])
        self.assertEqual(blocked["reason"], "capacity")

    def test_an_unconfirmed_result_keeps_its_family_slot(self):
        board = self.board(max_concurrent=8)
        self.register(board, "w1")
        self.register(board, "w2")
        first = self.claim_governed(board, "w1", "uncertain-1")
        second = self.claim_governed(board, "w2", "uncertain-2")
        # A completed result with unconfirmed shutdown leaves the attempt
        # uncertain: a survivor may still be running, so its family slot stays.
        self.finish(board, "w2", second, shutdown=False)
        self.register(board, "w3")
        blocked = self.claim_governed(board, "w3", "uncertain-3")
        self.assertIsNone(blocked["claim"])
        self.assertEqual(blocked["reason"], "model-capacity")
        with board.store.db.read() as connection:
            row = connection.execute(
                "SELECT execution_state, model_adapter, model_provider, model_model FROM attempts WHERE attempt_id=?",
                (second["claim"]["attempt"]["attemptId"],),
            ).fetchone()
        self.assertEqual(row["execution_state"], "uncertain")
        self.assertEqual((row["model_adapter"], row["model_provider"], row["model_model"]), FAMILY_A)
        # A confirmed completion frees the slot for the next claim.
        self.finish(board, "w1", first, shutdown=True)
        self.register(board, "w4")
        released = self.claim_governed(board, "w4", "uncertain-4")
        self.assertIsNotNone(released["claim"])

    def test_capacity_report_counts_total_and_per_family_occupancy(self):
        board = self.board(max_concurrent=8)
        self.register(board, "w1")
        self.register(board, "w2")
        self.register(board, "w3")
        self.claim_governed(board, "w1", "report-1")
        self.claim_governed(board, "w2", "report-2")
        self.claim_governed(board, "w3", "report-3", family=FAMILY_B)
        # One more queued task of family A so pending families appear too.
        self.submit_governed(board, "report-queued")
        capacity = board.store.capacity_report()
        self.assertEqual(capacity["totalLimit"], 8)
        self.assertEqual(capacity["totalActive"], 3)
        rows = {(row["adapter"], row["provider"], row["model"]): row for row in capacity["models"]}
        self.assertEqual(rows[FAMILY_A]["limit"], 2)
        self.assertEqual(rows[FAMILY_A]["active"], 2)
        self.assertEqual(rows[FAMILY_B]["limit"], 2)
        self.assertEqual(rows[FAMILY_B]["active"], 1)


class IdentityImmutabilityTests(StoreConcurrencyTestCase):
    def test_an_historical_attempt_never_changes_its_frozen_family(self):
        board = self.board()
        self.register(board, "w1")
        claim = self.claim_governed(board, "w1", "history-1")
        attempt_id = claim["claim"]["attempt"]["attemptId"]
        self.finish(board, "w1", claim, shutdown=True)
        # The stored family survives completion and stays exactly what was frozen.
        with board.store.db.read() as connection:
            row = connection.execute(
                "SELECT model_adapter, model_provider, model_model, execution_state FROM attempts WHERE attempt_id=?",
                (attempt_id,),
            ).fetchone()
        self.assertEqual((row["model_adapter"], row["model_provider"], row["model_model"]), FAMILY_A)
        self.assertEqual(row["execution_state"], "finished")


if __name__ == "__main__":
    unittest.main()
