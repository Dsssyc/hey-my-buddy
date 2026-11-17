"""Macro-task goals are Host-owned display history, separate from Worker work."""

import json
import unittest

from blackboard.tasks.test_workflow import WorkflowTestCase
from hey_my_buddy.blackboard.store.store import BoardStore
from hey_my_buddy.blackboard.tasks import macro_tasks
from hey_my_buddy.errors import BoardError
from hey_my_buddy.protocol import schemas
from support import macro_summaries, private_state_dir


class EmptyMacroTaskGoalTests(unittest.TestCase):
    def setUp(self):
        root = self.enterContext(private_state_dir("empty-macro-goal-"))
        self.store = BoardStore(root / "state")
        self.store.initialize()
        self.addCleanup(self.store.db.close)
        self.params = {"cwd": str(root / "source"), "projectPath": str(root / "source"),
                       "projectId": "a" * 64, "projectKind": "git",
                       "repositoryPath": str(root / "source" / ".git"),
                       "title": "Prepare review", "goal": "First goal", "creationKey": "create-v1-key"}

    def test_empty_create_replay_and_amend_keep_history(self):
        first = macro_tasks.create_macro_task(self.store, self.params)
        self.assertFalse(first["alreadyExists"])
        self.assertEqual(first["runs"], [])
        replay = macro_tasks.create_macro_task(self.store, {**self.params, "goal": "Ignored replay"})
        self.assertEqual(replay["macroTaskId"], first["macroTaskId"])
        self.assertTrue(replay["alreadyExists"])
        self.assertEqual(replay["goal"]["body"], "First goal")
        changed = macro_tasks.amend_macro_task(self.store, {"macroTaskId": first["macroTaskId"],
                                                             "goal": "Revised goal"})
        self.assertEqual(changed["goal"]["revision"], 2)
        self.assertFalse(changed["alreadyCurrent"])
        again = macro_tasks.amend_macro_task(self.store, {"macroTaskId": first["macroTaskId"],
                                                           "goal": "Revised goal"})
        self.assertTrue(again["alreadyCurrent"])
        self.assertEqual(again["goal"]["revision"], 2)
        history = macro_tasks.macro_task_goal_history(self.store, {"macroTaskId": first["macroTaskId"]})
        self.assertEqual([entry["body"] for entry in history["goals"]], ["Revised goal", "First goal"])

    def test_empty_macro_is_listed_searched_and_has_an_empty_timeline(self):
        created = macro_tasks.create_macro_task(self.store, self.params)
        identifier = created["macroTaskId"]
        listed = macro_summaries(macro_tasks.macro_task_list(self.store, {}))
        self.assertEqual([row["macroTaskId"] for row in listed], [identifier])
        self.assertEqual(listed[0]["state"], "empty")
        self.assertEqual(listed[0]["counts"]["roots"], 0)
        self.assertEqual(macro_tasks.macro_task_list(self.store, {})["defaults"],
                         {"firstLimit": macro_tasks.LIST_FIRST_LIMIT,
                          "groupLimit": macro_tasks.LIST_GROUP_LIMIT})
        for query in ("review", "source", identifier):
            with self.subTest(query=query):
                result = macro_tasks.macro_task_list(self.store, {"query": query})
                self.assertEqual([row["macroTaskId"] for row in macro_summaries(result)], [identifier])
                self.assertEqual(result["totals"]["all"], 1)
        for name in ("active", "host", "review"):
            self.assertEqual(macro_summaries(macro_tasks.macro_task_list(self.store, {"filter": name})), [])
        timeline = macro_tasks.macro_task_timeline(self.store, {"macroTaskId": identifier})
        self.assertEqual(timeline["rows"], [])
        self.assertEqual(timeline["macroTask"]["goal"]["body"], "First goal")

    def test_deleted_empty_macro_releases_creation_key(self):
        from hey_my_buddy.blackboard.tasks import macro_task_deletion

        first = macro_tasks.create_macro_task(self.store, self.params)
        preview = macro_task_deletion.preview(self.store, first["macroTaskId"])
        self.assertEqual(preview["microTaskCount"], 0)
        self.assertEqual(preview["blockers"], [])
        deleted = macro_task_deletion.delete(self.store, first["macroTaskId"], 0)
        self.assertEqual(deleted["state"], "deleted")
        with self.store.db.read() as connection:
            self.assertEqual(connection.execute(
                "SELECT COUNT(*) FROM events WHERE json_extract(payload_json,'$.macroTaskId')=?",
                (first["macroTaskId"],)).fetchone()[0], 0)
        again = macro_tasks.create_macro_task(self.store, self.params)
        self.assertNotEqual(again["macroTaskId"], first["macroTaskId"])

    def test_project_read_pages_empty_macros_without_a_run(self):
        first = macro_tasks.create_macro_task(self.store, self.params)
        second = macro_tasks.create_macro_task(
            self.store, {**self.params, "title": "Second", "creationKey": "second-key"})
        page = macro_tasks.project_get(self.store, {"projectId": self.params["projectId"], "limit": 1})
        self.assertEqual(page["project"]["id"], self.params["projectId"])
        self.assertEqual(page["project"]["path"], self.params["cwd"])
        self.assertEqual(len(page["items"]), 1)
        self.assertIsNotNone(page["nextCursor"])
        next_page = macro_tasks.project_get(
            self.store, {"projectId": self.params["projectId"], "limit": 1,
                         "before": page["nextCursor"]})
        self.assertEqual({page["items"][0]["macroTaskId"], next_page["items"][0]["macroTaskId"]},
                         {first["macroTaskId"], second["macroTaskId"]})
        self.assertIsNone(next_page["nextCursor"])


class MacroTaskGoalTests(WorkflowTestCase):
    def amend(self, board, run, goal):
        return board.call("workflow_amend", {"macroTaskId": run["macroTaskId"], "goal": goal})

    def test_console_goal_history_reads_all_revisions_newest_first(self):
        board = self.board()
        run = self.submit(board, create_macro={"title": "Group", "goal": "  Original\ntext  "})
        self.amend(board, run, "第二版")
        self.amend(board, run, "Third")
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

    def test_create_requires_goal_and_rejects_description(self):
        board = self.board()
        cwd = str(self.workdir())
        for presentation in (
            {"title": "A group"},
            {"title": "A group", "goal": ""},
            {"title": "A group", "goal": " \n "},
            {"title": "A group", "goal": None},
        ):
            with self.subTest(presentation=presentation), self.assertRaises(BoardError) as caught:
                board.call("workflow_create", {"cwd": cwd, **presentation})
            self.assertEqual(caught.exception.code, "INVALID_ARGUMENT")
        with self.assertRaises(BoardError) as description:
            board.call("workflow_create", {"cwd": cwd, "title": "A group", "goal": "Aim",
                                           "description": "old field"})
        self.assertEqual(description.exception.code, "INVALID_ARGUMENT")
        self.assertIn("description", description.exception.message)
        self.assertEqual(board.store.count_tasks(), 0)

    def test_initial_goal_keeps_exact_body_utf8_limit_and_creation_time(self):
        board = self.board()
        body = "  First line.\n\n末行  "
        run = self.submit(board, create_macro={"title": "Readable title", "goal": body})
        with board.store.db.read() as db:
            created = db.execute(
                "SELECT revision,body,created_at FROM macro_task_goals WHERE macro_task_id=?",
                (run["macroTaskId"],),
            ).fetchone()
        goal = board.call("workflow_get", {"runId": run["runId"]})["goal"]
        self.assertEqual(goal, {"body": body, "revision": 1, "createdAt": created["created_at"]})
        self.assertEqual((created["revision"], created["body"]), (1, body))
        self.assertEqual(goal["createdAt"], board.call("workflow_get", {"macroTaskId": run["macroTaskId"]})["createdAt"])
        self.assertEqual(board.call("macro_task_timeline", {"macroTaskId": run["macroTaskId"]})["macroTask"]["goal"], goal)
        self.assertNotIn("goal", macro_summaries(board.call("macro_task_list", {}))[0])

        maximum = schemas.MAX_TASK_BYTES
        at_limit = "界" * (maximum // 3) + "x" * (maximum % 3)
        self.assertEqual(len(at_limit.encode("utf-8")), maximum)
        large = self.submit(board, request_id="max-goal", kind="worktree",
                            create_macro={"title": "Large goal", "goal": at_limit})
        self.assertEqual(board.call("workflow_get", {"runId": large["runId"]})["goal"]["body"], at_limit)
        with self.assertRaises(BoardError) as caught:
            self.submit(board, request_id="over-goal", kind="worktree",
                        create_macro={"title": "Too large", "goal": at_limit + "x"})
        self.assertEqual(caught.exception.code, "INVALID_ARGUMENT")
        self.assertEqual(board.store.count_tasks(), 2)

    def test_large_goals_stay_out_of_list_replies_and_remain_in_details(self):
        board = self.board()
        body = "x" * schemas.MAX_TASK_BYTES
        runs = [self.submit(board, request_id=f"large-{index}", kind="worktree",
                            create_macro={"title": f"Large goal {index}", "goal": body})
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
        first = self.submit(board, create_macro={"title": "Group", "goal": "Initial"})
        second = self.submit(board, request_id="second", kind="worktree", macroTaskId=first["macroTaskId"])
        before = {run["runId"]: board.call("task_get", {"runId": run["runId"]})["task"]["revision"]
                  for run in (first, second)}
        changed = "  Revised\ngoal  "
        result = self.amend(board, second, changed)
        self.assertEqual(result["goalRevision"], 2)
        self.assertFalse(result["alreadyCurrent"])
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
                self.assertEqual(view["task"]["revision"], before[run["runId"]])
        self.assertEqual(board.call("macro_task_timeline", {"macroTaskId": first["macroTaskId"]})["macroTask"]["goal"], current)
        self.assertNotIn("goal", macro_summaries(board.call("macro_task_list", {}))[0])

    def test_amend_replay_and_distinct_goals_append_without_revision_input(self):
        board = self.board()
        run = self.submit(board, create_macro={"title": "Group", "goal": "Initial"})
        first = self.amend(board, run, "Next")
        head = board.store.head()
        replay = self.amend(board, run, "Next")
        self.assertTrue(replay["alreadyCurrent"])
        self.assertEqual(replay["goalRevision"], 2)
        self.assertEqual(board.store.head(), head)
        self.assertEqual(board.call("workflow_get", {"runId": run["runId"]})["goal"]["body"], "Next")
        self.assertEqual(first["goalRevision"], 2)

        third = self.amend(board, run, "Third")
        self.assertEqual(third["goalRevision"], 3)
        changed_again = self.amend(board, run, "Next")
        self.assertFalse(changed_again["alreadyCurrent"])
        self.assertEqual(changed_again["goalRevision"], 4)
        self.assertEqual(board.call("workflow_get", {"runId": run["runId"]})["goal"]["body"], "Next")

    def test_amend_uses_macro_id_without_run_control(self):
        board = self.board()
        run = self.submit(board, create_macro={"title": "Group", "goal": "Initial"})
        changed = board.call("workflow_amend", {"macroTaskId": run["macroTaskId"], "goal": "Changed"})
        self.assertEqual(changed["goalRevision"], 2)
        with self.assertRaises(BoardError) as unknown:
            board.call("workflow_amend", {"macroTaskId": "macro-missing", "goal": "No group"})
        self.assertEqual(unknown.exception.code, "NOT_FOUND")

    def test_older_group_without_goal_accepts_first_revision(self):
        board = self.board()
        run = self.submit(board, create_macro={"title": "Older group", "goal": "Remove in fixture"})
        with board.store.db.write() as db:
            db.execute("DELETE FROM macro_task_goals WHERE macro_task_id=?", (run["macroTaskId"],))
        self.assertIsNone(board.call("workflow_get", {"runId": run["runId"]})["goal"])
        result = self.amend(board, run, "Restored goal")
        self.assertEqual(result["goalRevision"], 1)
        self.assertEqual(board.call("workflow_get", {"runId": run["runId"]})["goal"]["body"], "Restored goal")

    def test_deleting_group_rejects_goal_amend(self):
        board = self.board()
        run = self.submit(board, create_macro={"title": "Group", "goal": "Initial"})
        with board.store.db.write() as db:
            db.execute(
                "INSERT INTO macro_task_deletions(deletion_id,target_kind,target_id) VALUES(?,?,?)",
                ("deleting-group", "macro-task", run["macroTaskId"]),
            )
        with self.assertRaises(BoardError) as caught:
            self.amend(board, run, "Changed")
        self.assertEqual(caught.exception.code, "MACRO_TASK_DELETING")
        self.assertEqual(caught.exception.details["macroTaskId"], run["macroTaskId"])
        self.assertEqual(board.call("workflow_get", {"runId": run["runId"]})["goal"]["body"], "Initial")

    def test_get_includes_every_inquiry_with_answer_and_timestamps(self):
        board = self.board()
        run = self.submit(board, create_macro={"title": "Group", "goal": "Initial"})
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
        run = self.submit(board, task="Worker instructions", create_macro={"title": "Group", "goal": sentinel})
        self.assertEqual(board.call("workflow_get", {"runId": run["runId"]})["goal"]["body"], sentinel)
        claim = self.claim(board, run_id=run["runId"])["claim"]
        self.assertNotIn(sentinel, json.dumps(claim, ensure_ascii=False))
        agent_view = board.call("workflow_get", {"runId": run["runId"],
                                                 "credential": claim["agentCredential"]})
        self.assertNotIn("goal", agent_view)
        self.assertNotIn(sentinel, json.dumps(agent_view, ensure_ascii=False))
