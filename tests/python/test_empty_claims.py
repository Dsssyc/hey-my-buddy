"""Empty worker.claim results are transient observations, not allocation receipts.

0.10 store facts: a claim that admits no work — an idle board, a full machine
ceiling or a blocked workspace — writes no row into the commands ledger, so the
same claimRequestId can return later and observe newly queued work. A committed
(nonempty) claim keeps its durable receipt bound to worker, nonce and request
payload: it replays the identical attempt and capability across a store
re-open, while changed identity or payload is refused. Every test runs the
production store and service in process over a private state directory; the
command adapter and fixture reports only, never a model call.
"""
from __future__ import annotations

import json
import unittest

from support import BoardTestCase

from buddy.errors import BoardError

NONCE = "a" * 32


def submit_command(client, request_id: str, cwd, task: str = "do") -> dict:
    return client.submit(
        requestId=request_id, task=task, cwd=str(cwd), adapter="command", argv=["/bin/true"]
    )["task"]


def register_command_worker(client, worker_id: str) -> None:
    client.register_worker(worker_id, adapter="command", capabilities=["command"])


class EmptyClaimTests(BoardTestCase):
    """Each scenario asserts against its private board's own commands ledger."""

    def command_rows(self, board, kind: str | None = "worker.claim") -> list:
        with board.store.db.read() as connection:
            if kind is None:
                return connection.execute(
                    "SELECT command_id, task_id, attempt_id, kind, subject, response_json FROM commands"
                    " ORDER BY created_at, command_id"
                ).fetchall()
            return connection.execute(
                "SELECT command_id, task_id, attempt_id, kind, subject, response_json FROM commands"
                " WHERE kind=? ORDER BY created_at, command_id",
                (kind,),
            ).fetchall()

    def attempt_count(self, board, run_id: str) -> int:
        with board.store.db.read() as connection:
            return int(
                connection.execute(
                    "SELECT COUNT(*) AS count FROM attempts WHERE task_id=?", (run_id,)
                ).fetchone()["count"]
            )

    def test_many_empty_claims_grow_the_command_ledger_by_zero(self):
        board = self.board()
        client = board.client()
        register_command_worker(client, "w-poll")
        self.assertEqual(len(self.command_rows(board)), 0)
        for index in range(10):
            empty = client.claim("w-poll", f"claim-poll-{index}", NONCE)
            self.assertIsNone(empty["claim"])
            self.assertEqual(empty["reason"], "no-queued-work")
            self.assertEqual(empty["retryAfterMs"], 2000)
        # Repeating one id while idle rescans the board each time; the pre-0.10
        # ledger would hold one negative receipt that replays forever.
        for _ in range(5):
            empty = client.claim("w-poll", "claim-poll-repeat", NONCE)
            self.assertIsNone(empty["claim"])
            self.assertEqual(empty["reason"], "no-queued-work")
        self.assertEqual(len(self.command_rows(board)), 0)
        # Zero growth means zero rows of any kind: the ledger never saw a poll.
        self.assertEqual(len(self.command_rows(board, kind=None)), 0)

    def test_empty_then_admitted_commits_exactly_one_receipt_for_the_same_request_id(self):
        board = self.board()
        client = board.client()
        register_command_worker(client, "w-retry")
        first = client.claim("w-retry", "claim-retry-1", NONCE)
        self.assertIsNone(first["claim"])
        task = submit_command(client, "after-empty", self.workdir("retry-work"))
        second = client.claim("w-retry", "claim-retry-1", NONCE)
        self.assertFalse(second["replayed"])
        attempt_id = second["claim"]["attempt"]["attemptId"]
        self.assertEqual(second["claim"]["task"]["runId"], task["runId"])
        rows = self.command_rows(board)
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["command_id"], "claim-retry-1")
        self.assertEqual(row["kind"], "worker.claim")
        self.assertEqual(row["attempt_id"], attempt_id)
        # The receipt is bound to this worker and this nonce (verifier) only.
        self.assertEqual(row["subject"], f"w-retry:{board.store.db.nonce_verifier(NONCE)}:-")
        stored = json.loads(row["response_json"])
        self.assertEqual(stored["claim"]["attempt"]["attemptId"], attempt_id)
        # Verifier-only at rest: the capability is re-derived for the caller.
        self.assertNotIn("capability", stored["claim"])
        # Exactly one attempt exists and a further identical poll only replays it.
        third = client.claim("w-retry", "claim-retry-1", NONCE)
        self.assertTrue(third["replayed"])
        self.assertEqual(third["claim"]["attempt"]["attemptId"], attempt_id)
        self.assertEqual(self.attempt_count(board, task["runId"]), 1)

    def test_committed_claim_receipt_replays_after_store_reopen(self):
        board = self.board()
        client = board.client()
        task = submit_command(client, "reopen-fixture", self.workdir("reopen-work"))
        register_command_worker(client, "w-open")
        first = client.claim("w-open", "claim-reopen-1", NONCE)
        self.assertFalse(first["replayed"])
        # A fresh BoardStore over the same private state: restart-equivalent.
        store_two = type(board.store)(self.directory, max_concurrent=2)
        store_two.initialize()
        replay = store_two.worker_claim(
            {"workerId": "w-open", "claimRequestId": "claim-reopen-1", "nonce": NONCE}
        )
        self.assertTrue(replay["replayed"])
        self.assertEqual(replay["claim"]["attempt"]["attemptId"], first["claim"]["attempt"]["attemptId"])
        self.assertEqual(replay["claim"]["attempt"]["generation"], first["claim"]["attempt"]["generation"])
        self.assertEqual(replay["claim"]["capability"], first["claim"]["capability"])
        self.assertEqual(replay["claim"]["task"]["runId"], task["runId"])
        self.assertEqual(self.attempt_count(board, task["runId"]), 1)

    def test_changed_identity_or_payload_cannot_reuse_a_granted_claim(self):
        board = self.board()
        client = board.client()
        task = submit_command(client, "guard-held", self.workdir("guard-work"))
        other = submit_command(client, "guard-other", self.workdir("guard-other"))
        register_command_worker(client, "w-guard")
        register_command_worker(client, "w-other")
        first = client.claim("w-guard", "claim-guard-1", NONCE)
        self.assertIsNotNone(first["claim"])
        with self.assertRaises(BoardError) as caught:
            client.claim("w-guard", "claim-guard-1", "b" * 32)
        self.assertEqual(caught.exception.code, "UNAUTHORIZED")
        with self.assertRaises(BoardError) as caught:
            client.claim("w-other", "claim-guard-1", NONCE)
        self.assertEqual(caught.exception.code, "UNAUTHORIZED")
        with self.assertRaises(BoardError) as caught:
            client.claim("w-guard", "claim-guard-1", NONCE, task_id=other["runId"])
        # The receipt subject binds worker, nonce and the explicitly targeted
        # task, so a changed payload under a committed claim id is refused as
        # UNAUTHORIZED before the request hash is even compared.
        self.assertEqual(caught.exception.code, "UNAUTHORIZED")
        # The granted receipt is untouched: one row, same attempt, no re-mint.
        rows = self.command_rows(board)
        self.assertEqual([row["command_id"] for row in rows], ["claim-guard-1"])
        self.assertEqual(rows[0]["attempt_id"], first["claim"]["attempt"]["attemptId"])
        self.assertEqual(self.attempt_count(board, task["runId"]), 1)
        self.assertEqual(client.get(runId=task["runId"])["attemptGeneration"], 1)
        self.assertEqual(client.get(runId=other["runId"])["status"], "queued")

    def test_capacity_blocked_claim_stays_blocked_and_writes_no_receipt(self):
        board = self.board(max_concurrent=1)
        client = board.client()
        register_command_worker(client, "w-holder")
        register_command_worker(client, "w-waiter")
        held = submit_command(client, "cap-held", self.workdir("cap-held"))
        first = client.claim("w-holder", "claim-held-1", NONCE)
        self.assertIsNotNone(first["claim"])
        waiting = submit_command(client, "cap-waiting", self.workdir("cap-waiting"))
        self.assertEqual(waiting["status"], "queued")
        blocked = client.claim("w-waiter", "claim-waiter-1", NONCE)
        self.assertIsNone(blocked["claim"])
        self.assertEqual(blocked["reason"], "capacity")
        # The machine ceiling still binds and the ledger holds only the held
        # claim's committed receipt — the blocked poll stored nothing.
        self.assertEqual(client.get(runId=waiting["runId"])["status"], "queued")
        self.assertEqual([row["command_id"] for row in self.command_rows(board)], ["claim-held-1"])
        # Once capacity frees, the once-blocked request commits exactly one receipt.
        attempt = first["claim"]["attempt"]
        client.submit_result(
            "w-holder",
            attempt["attemptId"],
            attempt["generation"],
            NONCE,
            {"status": "failed", "result": {"status": "nonzero"}, "shutdownConfirmed": True, "error": "fixture"},
        )
        admitted = client.claim("w-waiter", "claim-waiter-1", NONCE)
        self.assertIsNotNone(admitted["claim"])
        self.assertFalse(admitted["replayed"])
        self.assertEqual(admitted["claim"]["task"]["runId"], waiting["runId"])
        rows = self.command_rows(board)
        self.assertEqual([row["command_id"] for row in rows], ["claim-held-1", "claim-waiter-1"])
        self.assertEqual(rows[1]["attempt_id"], admitted["claim"]["attempt"]["attemptId"])
        self.assertEqual(self.attempt_count(board, held["runId"]), 1)

    def test_workspace_blocked_claim_stays_blocked_and_writes_no_receipt(self):
        board = self.board(max_concurrent=4)
        client = board.client()
        parent = self.workdir("repo")
        nested = parent / "nested"
        nested.mkdir()
        register_command_worker(client, "w-parent")
        register_command_worker(client, "w-child")
        submit_command(client, "ws-parent", parent)
        self.assertIsNotNone(client.claim("w-parent", "claim-parent-1", NONCE)["claim"])
        child = submit_command(client, "ws-child", nested)
        self.assertEqual(child["queueReason"], "cwd-overlap")
        blocked = client.claim("w-child", "claim-child-1", NONCE)
        self.assertIsNone(blocked["claim"])
        self.assertEqual(blocked["reason"], "cwd-overlap")
        # The workspace protection is retained and the blocked poll stored nothing.
        self.assertEqual(client.get(runId=child["runId"])["status"], "queued")
        self.assertEqual(client.get(runId=child["runId"])["queueReason"], "cwd-overlap")
        self.assertEqual([row["command_id"] for row in self.command_rows(board)], ["claim-parent-1"])


if __name__ == "__main__":
    unittest.main()
