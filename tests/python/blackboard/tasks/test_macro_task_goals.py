"""Macro-task goals are Host-owned display history, separate from Worker work."""

import json

from blackboard.tasks.test_workflow import WorkflowTestCase
from hey_my_buddy.errors import BoardError
from hey_my_buddy.protocol import schemas
from support import macro_summaries


class MacroTaskGoalTests(WorkflowTestCase):
    def amend(self, board, run, goal, *, command_id="goal-2", expected=1, **control):
        return board.call("workflow_goal_amend", {
            "runId": run["runId"], "commandId": command_id,
            "goal": goal, "expectedGoalRevision": expected,
            **(control or self.control(run)),
        })

    def test_console_goal_history_reads_all_revisions_newest_first(self):
        board = self.board()
        run = self.submit(board, macroTask={"title": "Group", "goal": "  Original\ntext  "})
        self.amend(board, run, "第二版")
        self.amend(board, run, "Third", command_id="goal-3", expected=2)
        with board.store.db.read() as db:
            recorded = db.execute(
                "SELECT body,revision,created_at FROM macro_task_goals"
                " WHERE macro_task_id=? ORDER BY revision DESC", (run["macroTaskId"],),
            ).fetchall()
        history = board.call("macro_task_goal_history", {"macroTaskId": run["macroTaskId"]})
        self.assertEqual(history, {
            "macroTaskId": run["macroTaskId"],
            "goals": [{"body": row["body"], "revision": row["revision"], "createdAt": row["created_at"]}
                      for row in recorded],
        })
        self.assertEqual([goal["revision"] for goal in history["goals"]], [3, 2, 1])
        self.assertEqual(history["goals"][-1]["body"], "  Original\ntext  ")

        for params in ({}, {"macroTaskId": "run:standalone"},
                       {"macroTaskId": run["macroTaskId"], "extra": 1}):
            with self.subTest(params=params), self.assertRaises(BoardError) as invalid:
                board.call("macro_task_goal_history", params)
            self.assertEqual(invalid.exception.code, "INVALID_ARGUMENT")
        with self.assertRaises(BoardError) as absent:
            board.call("macro_task_goal_history", {"macroTaskId": "macro-missing"})
        self.assertEqual(absent.exception.code, "NOT_FOUND")

    def test_submission_requires_goal_and_rejects_description(self):
        board = self.board()
        for presentation in (
            {"title": "A group"},
            {"title": "A group", "goal": ""},
            {"title": "A group", "goal": " \n "},
            {"title": "A group", "goal": None},
        ):
            with self.subTest(presentation=presentation), self.assertRaises(BoardError) as caught:
                self.submit(board, macroTask=presentation)
            self.assertEqual(caught.exception.code, "INVALID_ARGUMENT")
        with self.assertRaises(BoardError) as description:
            self.submit(board, macroTask={"title": "A group", "goal": "Aim", "description": "old field"})
        self.assertEqual(description.exception.code, "INVALID_ARGUMENT")
        self.assertEqual(description.exception.details["field"], "description")
        self.assertIn("description", description.exception.message)
        self.assertEqual(board.store.count_tasks(), 0)

    def test_initial_goal_keeps_exact_body_utf8_limit_and_creation_time(self):
        board = self.board()
        body = "  First line.\n\n末行  "
        run = self.submit(board, macroTask={"title": "Readable title", "goal": body})
        with board.store.db.read() as db:
            created = db.execute(
                "SELECT revision,body,created_at FROM macro_task_goals WHERE macro_task_id=?",
                (run["macroTaskId"],),
            ).fetchone()
        goal = board.call("workflow_get", {"runId": run["runId"]})["goal"]
        self.assertEqual(goal, {"body": body, "revision": 1, "createdAt": created["created_at"]})
        self.assertEqual((created["revision"], created["body"]), (1, body))
        self.assertEqual(goal["createdAt"], run["createdAt"])
        self.assertEqual(board.call("macro_task_timeline", {"macroTaskId": run["macroTaskId"]})["macroTask"]["goal"], goal)
        self.assertNotIn("goal", macro_summaries(board.call("macro_task_list", {}))[0])

        maximum = schemas.MAX_TASK_BYTES
        at_limit = "界" * (maximum // 3) + "x" * (maximum % 3)
        self.assertEqual(len(at_limit.encode("utf-8")), maximum)
        large = self.submit(board, request_id="max-goal", kind="worktree",
                            macroTask={"title": "Large goal", "goal": at_limit})
        self.assertEqual(board.call("workflow_get", {"runId": large["runId"]})["goal"]["body"], at_limit)
        with self.assertRaises(BoardError) as caught:
            self.submit(board, request_id="over-goal", kind="worktree",
                        macroTask={"title": "Too large", "goal": at_limit + "x"})
        self.assertEqual(caught.exception.code, "INVALID_ARGUMENT")
        self.assertEqual(board.store.count_tasks(), 2)

    def test_large_goals_stay_out_of_list_replies_and_remain_in_details(self):
        board = self.board()
        body = "x" * schemas.MAX_TASK_BYTES
        runs = [self.submit(board, request_id=f"large-{index}", kind="worktree",
                            macroTask={"title": f"Large goal {index}", "goal": body})
                for index in range(9)]
        page = board.call("macro_task_list", {"limit": 20})
        self.assertEqual(len(macro_summaries(page)), 9)
        self.assertLess(len(json.dumps(page).encode("utf-8")), schemas.MAX_TASK_BYTES)
        self.assertTrue(all("goal" not in row for row in macro_summaries(page)))
        for run in runs:
            detail = board.call("macro_task_timeline", {"macroTaskId": run["macroTaskId"]})
            self.assertEqual(detail["macroTask"]["goal"]["body"], body)

    def test_amend_updates_all_member_views_without_changing_run_revision(self):
        board = self.board()
        first = self.submit(board, macroTask={"title": "Group", "goal": "Initial"})
        second = self.submit(board, request_id="second", kind="worktree", macroTaskId=first["macroTaskId"])
        before = {run["runId"]: board.call("workflow_get", {"runId": run["runId"]})["revision"]
                  for run in (first, second)}
        changed = "  Revised\ngoal  "
        result = self.amend(board, second, changed)
        self.assertEqual((result["previousGoalRevision"], result["goalRevision"]), (1, 2))
        self.assertFalse(result["duplicate"])
        with board.store.db.read() as db:
            rows = db.execute(
                "SELECT revision,body,created_at FROM macro_task_goals WHERE macro_task_id=? ORDER BY revision",
                (first["macroTaskId"],),
            ).fetchall()
        self.assertEqual([(row["revision"], row["body"]) for row in rows], [(1, "Initial"), (2, changed)])
        current = {"body": changed, "revision": 2, "createdAt": rows[1]["created_at"]}
        for run in (first, second):
            with self.subTest(runId=run["runId"]):
                view = board.call("workflow_get", {"runId": run["runId"]})
                self.assertEqual(view["goal"], current)
                self.assertEqual(view["revision"], before[run["runId"]])
        self.assertEqual(board.call("macro_task_timeline", {"macroTaskId": first["macroTaskId"]})["macroTask"]["goal"], current)
        self.assertNotIn("goal", macro_summaries(board.call("macro_task_list", {}))[0])

    def test_amend_replay_conflict_and_stale_revision_leave_history_unchanged(self):
        board = self.board()
        run = self.submit(board, macroTask={"title": "Group", "goal": "Initial"})
        first = self.amend(board, run, "Next")
        head = board.store.head()
        replay = self.amend(board, run, "Next")
        self.assertTrue(replay["duplicate"])
        self.assertEqual((replay["previousGoalRevision"], replay["goalRevision"]), (1, 2))
        self.assertEqual(board.store.head(), head)
        with self.assertRaises(BoardError) as changed:
            self.amend(board, run, "Different")
        self.assertEqual(changed.exception.code, "CONFLICT")
        with self.assertRaises(BoardError) as stale:
            self.amend(board, run, "Third", command_id="goal-3", expected=1)
        self.assertEqual(stale.exception.code, "GOAL_REVISION_CONFLICT")
        self.assertEqual(stale.exception.details,
                         {"expectedGoalRevision": 1, "currentGoalRevision": 2})
        self.assertEqual(board.store.head(), head)
        self.assertEqual(board.call("workflow_get", {"runId": run["runId"]})["goal"]["body"], "Next")
        self.assertEqual((first["previousGoalRevision"], first["goalRevision"]), (1, 2))

        third = self.amend(board, run, "Third", command_id="goal-3", expected=2)
        self.assertEqual(third["goalRevision"], 3)
        old_replay = self.amend(board, run, "Next")
        self.assertTrue(old_replay["duplicate"])
        self.assertEqual(old_replay["goalRevision"], 2)
        self.assertEqual(old_replay["goal"]["body"], "Third")

    def test_amend_requires_the_controlling_run_capability(self):
        board = self.board()
        run = self.submit(board, macroTask={"title": "Group", "goal": "Initial"})
        foreign = self.submit(board, request_id="foreign", host_id="host-2", kind="worktree",
                              cwd=str(self.workdir("foreign")))
        with self.assertRaises(BoardError) as missing:
            board.call("workflow_goal_amend", {
                "runId": run["runId"], "commandId": "missing-control",
                "goal": "Denied", "expectedGoalRevision": 1,
            })
        self.assertEqual(missing.exception.code, "UNAUTHORIZED")
        with self.assertRaises(BoardError) as wrong:
            self.amend(board, run, "Denied", command_id="foreign-control", **self.control(foreign))
        self.assertEqual(wrong.exception.code, "UNAUTHORIZED")
        with self.assertRaises(BoardError) as standalone:
            self.amend(board, foreign, "No group", command_id="standalone-goal", expected=0)
        self.assertEqual(standalone.exception.code, "MACRO_TASK_REQUIRED")
        self.assertEqual(board.call("workflow_get", {"runId": run["runId"]})["goal"]["body"], "Initial")

    def test_older_group_without_goal_accepts_first_revision(self):
        board = self.board()
        run = self.submit(board, macroTask={"title": "Older group", "goal": "Remove in fixture"})
        with board.store.db.write() as db:
            db.execute("DELETE FROM macro_task_goals WHERE macro_task_id=?", (run["macroTaskId"],))
        self.assertIsNone(board.call("workflow_get", {"runId": run["runId"]})["goal"])
        result = self.amend(board, run, "Restored goal", command_id="first-goal", expected=0)
        self.assertEqual((result["previousGoalRevision"], result["goalRevision"]), (0, 1))
        self.assertEqual(board.call("workflow_get", {"runId": run["runId"]})["goal"]["body"], "Restored goal")

    def test_deleting_group_rejects_goal_amend(self):
        board = self.board()
        run = self.submit(board, macroTask={"title": "Group", "goal": "Initial"})
        with board.store.db.write() as db:
            db.execute(
                "INSERT INTO macro_task_deletions(deletion_id,target_kind,target_id) VALUES(?,?,?)",
                ("deleting-group", "macro-task", run["macroTaskId"]),
            )
        with self.assertRaises(BoardError) as caught:
            self.amend(board, run, "Changed", command_id="blocked-goal")
        self.assertEqual(caught.exception.code, "TASK_DELETING")
        self.assertEqual(caught.exception.details["deletionId"], "deleting-group")
        self.assertEqual(board.call("workflow_get", {"runId": run["runId"]})["goal"]["body"], "Initial")

    def test_get_includes_every_inquiry_with_answer_and_timestamps(self):
        board = self.board()
        run = self.submit(board, macroTask={"title": "Group", "goal": "Initial"})
        posted = [board.store._message_post(run["runId"], f"question-{index:02}", f"Question {index}?")["message"]
                  for index in range(schemas.MAX_INQUIRIES_PER_RUN)]
        answer = {"text": "The answer", "bytes": 10, "via": "reply-tool", "toolCallId": None,
                  "at": "2026-01-01T00:00:00Z", "truncated": False, "source": "bridge-journal"}
        answered = board.store._message_update(run["runId"], "question-31", actor="bridge", answer=answer)["message"]
        with self.assertRaises(BoardError) as full:
            board.store._message_post(run["runId"], "question-32", "One too many?")
        self.assertEqual(full.exception.code, "TOO_MANY_INQUIRIES")
        inquiries = board.call("workflow_get", {"runId": run["runId"]})["inquiries"]
        self.assertEqual(len(inquiries), schemas.MAX_INQUIRIES_PER_RUN)
        self.assertEqual([item["inquiryId"] for item in inquiries],
                         [f"question-{index:02}" for index in range(schemas.MAX_INQUIRIES_PER_RUN)])
        self.assertEqual(inquiries[0], posted[0])
        self.assertEqual(inquiries[-1], answered)
        self.assertEqual(inquiries[-1]["answer"], answer)
        self.assertEqual(inquiries[-1]["state"], "answered")
        self.assertEqual(inquiries[-1]["question"], "Question 31?")
        self.assertTrue(all(item["createdAt"] and item["updatedAt"] for item in inquiries))
        self.assertTrue(all(item["questionBytes"] == len(item["question"].encode("utf-8")) for item in inquiries))

    def test_goal_is_absent_from_worker_input_and_agent_get(self):
        board = self.board()
        sentinel = "Goal only for the Host: 叶叶叶"
        run = self.submit(board, task="Worker instructions", macroTask={"title": "Group", "goal": sentinel})
        self.assertEqual(board.call("workflow_get", {"runId": run["runId"]})["goal"]["body"], sentinel)
        claim = self.claim(board, run_id=run["runId"])["claim"]
        self.assertNotIn(sentinel, json.dumps(claim, ensure_ascii=False))
        agent_view = board.call("workflow_get", {"runId": run["runId"],
                                                 "credential": claim["agentCredential"]})
        self.assertNotIn("goal", agent_view)
        self.assertNotIn(sentinel, json.dumps(agent_view, ensure_ascii=False))
