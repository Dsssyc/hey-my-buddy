"""Service admission is transient when idle and consumes each execution once."""
from __future__ import annotations

import unittest

from admission import admit_execution, admit_store
from support import BoardTestCase


NONCE = "a" * 32


def submit_command(board, request_id: str, cwd, task: str = "do") -> dict:
    return board.submit_execution(
        requestId=request_id, task=task, cwd=str(cwd), adapter="command", argv=["/bin/true"]
    )["task"]


class EmptyClaimTests(BoardTestCase):
    def attempt_count(self, board, run_id: str) -> int:
        with board.store.db.read() as connection:
            return connection.execute(
                "SELECT COUNT(*) FROM attempts WHERE task_id=?", (run_id,)
            ).fetchone()[0]

    def command_count(self, board) -> int:
        with board.store.db.read() as connection:
            return connection.execute("SELECT COUNT(*) FROM commands").fetchone()[0]

    def test_repeated_idle_admission_creates_no_attempt_or_command(self):
        board = self.board()
        client = board.client()
        for _ in range(15):
            result = admit_execution(client, NONCE)
            self.assertIsNone(result["claim"])
            self.assertEqual(result["reason"], "no-queued-work")
        self.assertEqual(self.command_count(board), 0)

    def test_idle_then_queued_admits_once_and_repeat_returns_empty(self):
        board = self.board()
        client = board.client()
        self.assertIsNone(admit_execution(client, NONCE)["claim"])
        task = submit_command(board, "after-empty", self.workdir("retry-work"))
        first = admit_execution(client, NONCE)
        self.assertEqual(first["claim"]["task"]["runId"], task["runId"])
        self.assertIsNone(admit_execution(client, NONCE)["claim"])
        self.assertEqual(self.attempt_count(board, task["runId"]), 1)
        self.assertEqual(self.command_count(board), 0)

    def test_restart_does_not_replay_a_consumed_admission(self):
        board = self.board()
        client = board.client()
        task = submit_command(board, "reopen-fixture", self.workdir("reopen-work"))
        first = admit_execution(client, NONCE)
        store_two = type(board.store)(self.directory, max_concurrent=2)
        self.addCleanup(store_two.db.close)
        store_two.initialize()
        self.assertIsNone(admit_store(store_two, {"nonce": NONCE})["claim"])
        self.assertEqual(self.attempt_count(board, task["runId"]), 1)
        self.assertEqual(first["claim"]["attempt"]["generation"], 1)

    def test_capacity_blocked_admission_retries_after_completion(self):
        board = self.board(max_concurrent=1)
        client = board.client()
        held = submit_command(board, "cap-held", self.workdir("cap-held"))
        first = admit_execution(client, NONCE)["claim"]
        waiting = submit_command(board, "cap-waiting", self.workdir("cap-waiting"))
        blocked = admit_execution(client, NONCE)
        self.assertIsNone(blocked["claim"])
        self.assertEqual(blocked["reason"], "capacity")
        attempt = first["attempt"]
        client.submit_result(attempt["attemptId"], attempt["generation"], NONCE, {
            "status": "failed", "result": {"status": "nonzero"},
            "shutdownConfirmed": True, "error": "fixture",
        })
        admitted = admit_execution(client, NONCE)
        self.assertEqual(admitted["claim"]["task"]["runId"], waiting["runId"])
        self.assertEqual(self.attempt_count(board, held["runId"]), 1)
        self.assertEqual(self.attempt_count(board, waiting["runId"]), 1)

    def test_overlapping_workspace_remains_queued_without_an_attempt(self):
        board = self.board(max_concurrent=4)
        client = board.client()
        parent = self.workdir("repo")
        nested = parent / "nested"
        nested.mkdir()
        submit_command(board, "ws-parent", parent)
        self.assertIsNotNone(admit_execution(client, NONCE)["claim"])
        child = submit_command(board, "ws-child", nested)
        blocked = admit_execution(client, NONCE)
        self.assertIsNone(blocked["claim"])
        self.assertEqual(blocked["reason"], "cwd-overlap")
        self.assertEqual(client.get(runId=child["runId"])["queueReason"], "cwd-overlap")
        self.assertEqual(self.attempt_count(board, child["runId"]), 0)


if __name__ == "__main__":
    unittest.main()
