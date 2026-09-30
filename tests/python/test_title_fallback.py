"""ADR-012 XI.3 task-title fallback: the read-only workflow.resultSummary projection.

The projection must expose, on every governed task read, this run's newest
concluded turn outcome summary — selected by turn_index, never by clock
timestamps — bounded to 2000 characters, without hiding a concluded result
behind a newer prepared/running turn and without substituting a helper's or an
older nonempty result. Every test drives the real store and the real decorated
reads through the in-process board harness with private state directories; the
only direct SQL is private fixture repair for states the validated write path
cannot produce (a whitespace or non-string summary).
"""
from __future__ import annotations

import json
import unittest

from test_workflow import WorkflowTestCase


def governed_view(board, run_id: str) -> dict:
    return board.call("task_get", {"runId": run_id})["task"]


def workflow_summary(view: dict) -> str | None:
    return view["workflow"]["resultSummary"]


class TitleFallbackTests(WorkflowTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.catalog_fixture()

    def two_turn_run(self, board, *, second_summary="second result"):
        """Turn 1 concludes for assistance; turn 2 concludes completed."""
        self.register(board)
        submitted = self.submit(board, request_id="run-1", task="fix the login flow\nkeep the schema stable")
        run_id = submitted["runId"]
        self.finish_turn(board, self.claim(board), disposition="assistance")
        view = board.call("workflow_get", {"runId": run_id})
        self.continue_run(board, view)
        second = self.claim(board, claim_request_id="c2")
        done = self.finish_turn(
            board,
            second,
            session_id="sess-2",
            outcome={
                "disposition": "completed",
                "summary": second_summary,
                "remaining": [],
                "decisions": [],
                "artifacts": [],
                "request": None,
            },
        )
        self.assertEqual(done["taskState"], "completed")
        self.assertEqual(board.call("workflow_get", {"runId": run_id})["state"], "delivered")
        return run_id

    def test_newest_concluded_own_result_reaches_every_governed_read(self):
        board = self.board(max_concurrent=1)
        run_id = self.two_turn_run(board, second_summary="整合完成并已验证")
        summary = "整合完成并已验证"
        self.assertEqual(workflow_summary(governed_view(board, run_id)), summary)
        listed = board.call("task_list", {"limit": 50})["runs"]
        self.assertEqual([row["workflow"]["resultSummary"] for row in listed if row["runId"] == run_id], [summary])
        snapshot = board.call("console_snapshot", {})
        self.assertEqual(
            [row["workflow"]["resultSummary"] for row in snapshot["tasks"]["runs"] if row["runId"] == run_id],
            [summary],
        )
        fetched = board.call("workflow_get", {"runId": run_id})
        self.assertEqual(fetched["task"]["workflow"]["resultSummary"], summary)

    def test_no_turns_and_ungoverned_tasks_project_no_summary(self):
        board = self.board(max_concurrent=1)
        self.register(board)
        submitted = self.submit(board, request_id="untouched", task="never claimed\nsecond line")
        view = governed_view(board, submitted["runId"])
        self.assertIsNone(workflow_summary(view))
        self.assertIn("workflow", view)
        plain = board.call(
            "task_submit",
            {
                "requestId": "plain-1",
                "task": "ordinary command task",
                "cwd": str(self.workdir()),
                "adapter": "command",
                "argv": ["/bin/echo", "plain"],
            },
        )["task"]
        self.assertNotIn("workflow", plain)

    def test_running_turn_does_not_hide_the_last_concluded_result(self):
        board = self.board(max_concurrent=1)
        self.register(board)
        submitted = self.submit(board, request_id="run-1", task="resolve the incident")
        run_id = submitted["runId"]
        self.finish_turn(board, self.claim(board), disposition="assistance")
        view = board.call("workflow_get", {"runId": run_id})
        self.assertEqual(workflow_summary(governed_view(board, run_id)), "need the Host")
        self.continue_run(board, view)
        self.claim(board, claim_request_id="c2")
        self.assertEqual(board.call("workflow_get", {"runId": run_id})["state"], "executing")
        # The second turn is prepared/running; the first concluded summary still shows.
        self.assertEqual(workflow_summary(governed_view(board, run_id)), "need the Host")
        listed = board.call("task_list", {"limit": 10})["runs"]
        self.assertEqual([row["workflow"]["resultSummary"] for row in listed if row["runId"] == run_id], ["need the Host"])

    def test_whitespace_or_malformed_summary_yields_null_without_substitution(self):
        board = self.board(max_concurrent=1)
        run_id = self.two_turn_run(board, second_summary="valid second result")
        for broken in ("   ", "\n\t ", 7, None, False, {}, [], {"text": "not a summary"}):
            with board.store.db.write() as connection:
                connection.execute(
                    "UPDATE workflow_turns SET outcome_json=? WHERE run_id=? AND turn_index=2",
                    (json.dumps({"disposition": "completed", "summary": broken}), run_id),
                )
            self.assertIsNone(workflow_summary(governed_view(board, run_id)), broken)
            listed = board.call("task_list", {"limit": 10})["runs"]
            self.assertIsNone([row for row in listed if row["runId"] == run_id][0]["workflow"]["resultSummary"])
        # The older nonempty first-turn result is never substituted back in.
        with board.store.db.write() as connection:
            connection.execute(
                "UPDATE workflow_turns SET outcome_json=? WHERE run_id=? AND turn_index=2",
                (json.dumps({"disposition": "completed", "summary": "  "}), run_id),
            )
            connection.execute(
                "UPDATE workflow_turns SET outcome_json=? WHERE run_id=? AND turn_index=1",
                (json.dumps({"disposition": "assistance", "summary": "older nonempty"}), run_id),
            )
        self.assertIsNone(workflow_summary(governed_view(board, run_id)))

    def test_json_looking_text_remains_text(self):
        board = self.board(max_concurrent=1)
        run_id = self.two_turn_run(board, second_summary='{"message":"literal text"}')
        self.assertEqual(workflow_summary(governed_view(board, run_id)), '{"message":"literal text"}')

    def test_projection_does_not_add_a_per_row_query(self):
        board = self.board(max_concurrent=1)
        run_id = self.two_turn_run(board)
        with board.store.db.read() as connection:
            task = connection.execute("SELECT * FROM tasks WHERE task_id=?", (run_id,)).fetchone()
            statements = []
            connection.set_trace_callback(statements.append)
            try:
                self.assertEqual(board.store.workflow.task_extension(connection, task)["resultSummary"], "second result")
            finally:
                connection.set_trace_callback(None)
        reads = [sql for sql in statements if sql.lstrip().upper().startswith("SELECT")]
        self.assertEqual(len(reads), 1, "Title projection must share the existing workflow-row read")

    def test_selection_follows_turn_index_not_clock_timestamps(self):
        board = self.board(max_concurrent=1)
        run_id = self.two_turn_run(board, second_summary="newest by index")
        with board.store.db.write() as connection:
            # Scramble the clock stamps so they contradict the turn order.
            connection.execute(
                "UPDATE workflow_turns SET created_at=?, updated_at=? WHERE run_id=? AND turn_index=1",
                ("2026-01-01T00:09:00.000Z", "2026-01-01T00:09:00.000Z", run_id),
            )
            connection.execute(
                "UPDATE workflow_turns SET created_at=?, updated_at=? WHERE run_id=? AND turn_index=2",
                ("2026-01-01T00:01:00.000Z", "2026-01-01T00:01:00.000Z", run_id),
            )
        self.assertEqual(workflow_summary(governed_view(board, run_id)), "newest by index")

    def test_helper_results_never_leak_into_the_parent_summary(self):
        board = self.board(max_concurrent=1)
        self.register(board)
        parent = self.submit(board, request_id="parent", host_id="host-a", cwd=str(self.workdir("parent")))
        self.finish_turn(board, self.claim(board), disposition="assistance")
        view = board.call("workflow_get", {"runId": parent["runId"]})
        approved = self.decide(
            board,
            view,
            view["activeRequest"]["requestId"],
            helpers=[
                {
                    "requestId": "helper-1",
                    "task": "bounded helper\nfor the parent",
                    "cwd": str(self.workdir()),
                    "executionWorkspace": {"kind": "worktree", "access": "write"},
                }
            ],
        )
        helper_id = approved["children"][0]["taskId"]
        helper_claim = self.claim(board, claim_request_id="c2", run_id=helper_id)
        self.finish_turn(
            board,
            helper_claim,
            outcome={
                "disposition": "completed",
                "summary": "helper自己的结论",
                "remaining": [],
                "decisions": [],
                "artifacts": [],
                "request": None,
            },
        )
        self.assertEqual(workflow_summary(governed_view(board, parent["runId"])), "need the Host")
        self.assertEqual(workflow_summary(governed_view(board, helper_id)), "helper自己的结论")
        listed = {row["runId"]: row["workflow"]["resultSummary"] for row in board.call("task_list", {"limit": 10})["runs"]}
        self.assertEqual(listed[parent["runId"]], "need the Host")
        self.assertEqual(listed[helper_id], "helper自己的结论")

    def test_result_summary_is_bounded_to_2000_unicode_characters(self):
        board = self.board(max_concurrent=1)
        long_summary = "长" * 2500 + "tail"
        run_id = self.two_turn_run(board, second_summary=long_summary)
        projected = workflow_summary(governed_view(board, run_id))
        self.assertEqual(len(projected), 2000)
        self.assertEqual(projected, long_summary[:2000])
        listed = [row for row in board.call("task_list", {"limit": 10})["runs"] if row["runId"] == run_id][0]
        self.assertEqual(listed["workflow"]["resultSummary"], long_summary[:2000])

    def test_projection_reads_are_stable_and_read_only(self):
        board = self.board(max_concurrent=1)
        run_id = self.two_turn_run(board, second_summary="stable result")
        with board.store.db.read() as connection:
            before = {
                "events": connection.execute("SELECT COUNT(*) AS count FROM events").fetchone()["count"],
                "turns": connection.execute("SELECT COUNT(*) AS count FROM workflow_turns").fetchone()["count"],
                "revision": connection.execute(
                    "SELECT revision FROM workflow_runs WHERE run_id=?", (run_id,)
                ).fetchone()["revision"],
                "total": board.call("task_list", {"limit": 1})["total"],
            }
        for _ in range(2):
            self.assertEqual(workflow_summary(governed_view(board, run_id)), "stable result")
            board.call("task_list", {"limit": 50})
            board.call("workflow_get", {"runId": run_id})
            board.call("console_snapshot", {})
        with board.store.db.read() as connection:
            after = {
                "events": connection.execute("SELECT COUNT(*) AS count FROM events").fetchone()["count"],
                "turns": connection.execute("SELECT COUNT(*) AS count FROM workflow_turns").fetchone()["count"],
                "revision": connection.execute(
                    "SELECT revision FROM workflow_runs WHERE run_id=?", (run_id,)
                ).fetchone()["revision"],
                "total": board.call("task_list", {"limit": 1})["total"],
            }
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
