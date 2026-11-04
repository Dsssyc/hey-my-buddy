"""Read-only micro task provenance and keyset task-history paging.

Everything runs against the real store and the real service through the in-process
board harness: private state directories, no model call, no worker process. The
workspace module is the deterministic double, exactly like the micro-task workflow
tests, so a temporary execution worktree can be distinguished from the source
project without running Git.
"""
from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from support import FakeClock
from blackboard.routing.test_decision import CATALOG_BUDDY, DecisionTestCase
from blackboard.tasks.test_workflow import BUDDY, NONCE, WorkflowTestCase

from hey_my_buddy.blackboard.tasks import micro_task
from hey_my_buddy.errors import BoardError


class MicroTaskTestCase(WorkflowTestCase):
    """Shared fixtures: micro tasks, ordinary tasks and helper/decision runs."""

    seed = DecisionTestCase.seed

    def setUp(self) -> None:
        super().setUp()
        self.catalog_fixture()
        DecisionTestCase.use_helper(self)

    def execution_task(self, board, request_id: str, *, cwd=None, task_text=None):
        return board.submit_execution(**{
                "requestId": request_id,
                "task": task_text or f"ordinary {request_id}",
                "cwd": str(cwd or self.workdir()),
                "adapter": "command",
                "argv": ["/bin/echo", request_id],
            })["task"]

    def task_view(self, board, run_id: str) -> dict:
        return board.call("task_get", {"runId": run_id})["task"]

    def micro_task_of(self, board, run_id: str) -> dict:
        return self.task_view(board, run_id)["lineage"]

    def history(self, board, **params):
        return board.call("task_list", params)

    def helper_spec(self, request_id="helper-1", **extra):
        return {"title": request_id, "task": "bounded helper", "cwd": str(self.workdir(request_id)),
                "workspace": {"kind": "worktree", "access": "write"}, **extra}

    def parent_and_helper(self, board, *, routed=False):
        self.register(board)
        parent = self.submit(board, request_id="parent", cwd=str(self.workdir("parent")))
        self.finish_turn(board, self.claim(board), disposition="assistance")
        view = board.call("workflow_get", {"runId": parent["runId"]})
        approved = self.decide(board, view, view["activeRequest"]["requestId"],
                               autoContinue=False,
                               helpers=[self.helper_spec(buddy="route" if routed else BUDDY)])
        return parent, approved["children"][0]["taskId"]

    def deliver_parent(self, board, parent):
        view = board.call("workflow_get", {"runId": parent["runId"]})
        self.continue_run(board, view,  helper_policy="keep", buddy=BUDDY)
        self.register(board, "parent-worker")
        claim = self.claim(board, "parent-worker", run_id=parent["runId"], claim_request_id="parent-result")
        self.finish_turn(board, claim, worker_id="parent-worker")
        return board.call("workflow_get", {"runId": parent["runId"]})

    def page_all(self, board, *, page_size: int, **params):
        """Walk the whole filtered history through nextCursor and return the rows."""
        collected: list[dict] = []
        cursor = None
        pages = 0
        while True:
            request = {**params, "limit": page_size}
            if cursor is not None:
                request["before"] = cursor
            page = self.history(board, **request)
            collected.extend(page["runs"])
            pages += 1
            cursor = page["nextCursor"]
            if cursor is None:
                return collected, pages

    def assign_lead_ownership(self, board, owned_run_id, lead_run_id):
        with board.store.db.write() as connection:
            turn = connection.execute(
                "SELECT turn_id FROM workflow_turns WHERE run_id=? ORDER BY turn_index DESC LIMIT 1",
                (lead_run_id,),
            ).fetchone()
            connection.execute(
                "INSERT INTO workflow_lead_tasks(owned_run_id,lead_run_id,lead_turn_id,created_at)"
                " VALUES(?,?,?,?)",
                (owned_run_id, lead_run_id, turn["turn_id"], board.store.now()),
            )


class MicroTaskProvenanceTests(MicroTaskTestCase):
    def test_root_lineage_names_host_role_and_pinned_source_project(self):
        board = self.board()
        submitted = self.submit(board, request_id="micro-task-1", kind="worktree")
        run_id = submitted["runId"]
        view = self.task_view(board, run_id)
        metadata = view["lineage"]
        source = os.path.realpath(str(self.workdir()))
        self.assertNotEqual(view["cwd"], source)
        self.assertEqual(metadata["kind"], "micro-task")
        self.assertEqual(metadata["actor"], {"role": "host"})
        self.assertIsNone(metadata["parentRunId"])
        self.assertEqual(metadata["rootRunId"], run_id)
        self.assertEqual(metadata["project"], {"id": f"repo:{source}", "kind": "git",
                                               "path": source, "label": "work"})
        self.assertEqual(metadata["buddy"], BUDDY)
        self.assertEqual(view["workflowState"], "executing")
        for obsolete in ("sourceHostId", "currentHostId", "hostId"):
            self.assertNotIn(obsolete, metadata)

    def test_project_uses_pinned_root_input_after_new_turn_and_task_cwd_change(self):
        board = self.board()
        submitted = self.submit(board, request_id="pinned-project", kind="worktree")
        run_id = submitted["runId"]
        original = self.micro_task_of(board, run_id)["project"]
        initial = board.call("workflow_get", {"runId": run_id})["workspace"]
        self.finish_turn(board, self.claim(board, run_id=run_id))
        delivered = board.call("workflow_get", {"runId": run_id})
        self.continue_run(board, delivered)
        board.store.workflow.prepare_continuation_workspace({"runId": run_id})
        current = board.call("workflow_get", {"runId": run_id})["workspace"]
        self.assertNotEqual((current["workspaceId"], current["manifestSha256"]),
                            (initial["workspaceId"], initial["manifestSha256"]))
        with board.store.db.write() as connection:
            connection.execute("UPDATE tasks SET cwd='/later/checkout' WHERE task_id=?", (run_id,))
        self.assertEqual(self.micro_task_of(board, run_id)["project"], original)
        listed = next(row for row in self.history(board)["runs"] if row["runId"] == run_id)
        self.assertEqual(listed["lineage"]["project"], original)
        self.assertEqual(original["path"], os.path.realpath(str(self.workdir())))
        self.assertEqual(original["id"], f"repo:{original['path']}")

    def test_helper_inherits_root_actor_and_project_with_explicit_parent(self):
        board = self.board(max_concurrent=1)
        parent, helper_id = self.parent_and_helper(board)
        root = self.micro_task_of(board, parent["runId"])
        helper_view = self.task_view(board, helper_id)
        helper = helper_view["lineage"]
        self.assertEqual(helper["kind"], "helper")
        self.assertEqual(helper["parentRunId"], parent["runId"])
        self.assertEqual(helper["rootRunId"], parent["runId"])
        self.assertEqual(helper["actor"], {"role": "host"})
        self.assertEqual(helper["project"], root["project"])
        self.assertNotEqual(helper_view["cwd"], helper["project"]["path"])
        self.assertEqual(helper["buddy"], BUDDY)

    def test_nested_helper_keeps_root_lineage(self):
        board = self.board()
        parent, child = self.parent_and_helper(board)
        self.finish_turn(board, self.claim(board, run_id=child, claim_request_id="child-yields"),
                         disposition="assistance")
        root_view = board.call("workflow_get", {"runId": parent["runId"]})
        self.decide(board, root_view, root_view["activeRequest"]["requestId"],
                    helpers=[self.helper_spec("grandchild")])
        grandchild = board.call("workflow_get", {"runId": child})["children"][0]["taskId"]
        metadata = self.micro_task_of(board, grandchild)
        self.assertEqual(metadata["kind"], "helper")
        self.assertEqual(metadata["parentRunId"], child)
        self.assertEqual(metadata["rootRunId"], parent["runId"])
        self.assertEqual(metadata["actor"], {"role": "host"})
        self.assertEqual(metadata["project"], self.micro_task_of(board, parent["runId"])["project"])

    def test_same_named_projects_do_not_share_one_project_id(self):
        board = self.board()
        first = self.workdir("one")
        second = self.workdir("two")
        (first / "proj").mkdir()
        (second / "proj").mkdir()
        one = self.submit(board, request_id="micro-task-1", cwd=str(first / "proj"))
        two = self.submit(board, request_id="micro-task-2", cwd=str(second / "proj"))
        left = self.micro_task_of(board, one["runId"])
        right = self.micro_task_of(board, two["runId"])
        self.assertEqual(left["project"]["label"], "proj")
        self.assertEqual(right["project"]["label"], "proj")
        self.assertNotEqual(left["project"]["id"], right["project"]["id"])
        self.assertNotEqual(left["project"]["path"], right["project"]["path"])
        self.assertEqual([row["runId"] for row in self.history(board, projectId=left["project"]["id"])["runs"]],
                         [one["runId"]])

    def test_execution_task_has_no_micro_task_actor_or_lineage(self):
        board = self.board()
        created = self.execution_task(board, "plain-1")
        metadata = self.micro_task_of(board, created["runId"])
        self.assertEqual(metadata["kind"], "execution")
        self.assertIsNone(metadata["actor"])
        self.assertIsNone(metadata["parentRunId"])
        self.assertIsNone(metadata["rootRunId"])
        self.assertEqual(metadata["project"]["path"], os.path.realpath(str(self.workdir())))
        self.assertIsNone(metadata["project"]["id"])
        self.assertIsNone(metadata["buddy"])

    def test_router_decision_belongs_to_owning_micro_task(self):
        board = self.board()
        self.seed(board)
        with patch("hey_my_buddy.blackboard.routing.decision.DecisionCoordinator._adapter_available",
                   return_value=(True, None)):
            submitted = self.submit(board, request_id="route-1", buddy="route",
                                    title="Verified implementation",
                                    task="Produce a verified implementation\nWith details",
                                    create_macro={"title": "Release work", "goal": "Release work"})
        micro_task_id = submitted["runId"]
        router_id = board.call("workflow_get", {"runId": micro_task_id})["routing"]["taskId"]
        self.assertIsNotNone(router_id)
        root = self.micro_task_of(board, micro_task_id)
        router = self.micro_task_of(board, router_id)
        self.assertEqual(router["kind"], "decision")
        self.assertEqual(router["actor"], {"role": "host"})
        self.assertEqual(router["parentRunId"], micro_task_id)
        self.assertEqual(router["rootRunId"], micro_task_id)
        self.assertEqual(router["project"], root["project"])
        self.assertEqual(router["routingTask"], {"runId": micro_task_id,
                                                   "title": "Verified implementation", "titleSource": "title",
                                                   "macroTaskId": submitted["macroTaskId"],
                                                   "macroTaskTitle": "Release work"})
        self.assertEqual(router["buddy"], {key: CATALOG_BUDDY[key]
                                            for key in ("harness", "provider", "model", "effort")})
        self.assertEqual(next(item for item in self.history(board)["runs"]
                              if item["runId"] == router_id)["lineage"], router)
        self.assertEqual({row["runId"] for row in self.history(board, query="Verified implementation")["runs"]},
                         {micro_task_id, router_id})
        with board.store.db.write() as connection:
            connection.execute("UPDATE workflow_runs SET title=NULL WHERE run_id=?", (micro_task_id,))
        self.assertEqual(self.micro_task_of(board, router_id)["routingTask"]["titleSource"], "first-line")
        self.assertEqual([row["runId"] for row in self.history(board, rootsOnly=True)["runs"]],
                         [micro_task_id])

    def test_every_decorated_view_carries_the_same_metadata(self):
        board = self.board()
        submitted = self.submit(board, request_id="micro-task-1")
        run_id = submitted["runId"]
        with board.store.db.read() as connection:
            task = connection.execute("SELECT * FROM tasks WHERE task_id=?", (run_id,)).fetchone()
            resolved = micro_task.resolve(connection, task)
            self.assertEqual(resolved, self.micro_task_of(board, run_id))
            self.assertEqual(micro_task.resolve_many(connection, [run_id, "missing-run"]), {run_id: resolved})
            self.assertEqual(micro_task.resolve_many(connection, []), {})
        listed = self.history(board, limit=100)["runs"][0]["lineage"]
        self.assertEqual(listed, self.micro_task_of(board, run_id))
        timeline = board.call("macro_task_timeline", {"macroTaskId": f"run:{run_id}"})
        self.assertEqual(timeline["rows"][0]["actor"], listed["actor"])
        self.assertEqual(timeline["rows"][0]["kind"], listed["kind"])
        self.assertEqual(self.task_view(board, run_id)["lineage"], listed)
        compact_task = board.call("workflow_get", {"runId": run_id})["task"]
        self.assertEqual(compact_task["lineage"], listed)
        self.assertNotIn("task", compact_task)
        self.assertNotIn("spec", compact_task)


class LeadHistoryFilterTests(MicroTaskTestCase):
    def test_host_filter_excludes_a_lead_owned_helper_tree(self):
        board = self.board()
        self.register(board)
        lead = self.submit(board, request_id="lead-filter-owner",
                           cwd=str(self.workdir("lead-filter-owner")))
        self.finish_turn(board, self.claim(board, run_id=lead["runId"], claim_request_id="lead-filter-claim"))

        root, helper = self.parent_and_helper(board)
        self.finish_turn(board, self.claim(board, run_id=helper, claim_request_id="lead-helper-yields"),
                         disposition="assistance")
        self.assign_lead_ownership(board, root["runId"], lead["runId"])

        pending = self.submit(board, request_id="unowned-host-boundary",
                              cwd=str(self.workdir("unowned-host-boundary")))
        self.finish_turn(board, self.claim(board, run_id=pending["runId"], claim_request_id="unowned-host-claim"),
                         disposition="assistance")

        hosted = self.history(board, filter="host", limit=100)
        self.assertEqual([row["runId"] for row in hosted["runs"]], [pending["runId"]])
        self.assertEqual(hosted["total"], 1)
        for run_id in (root["runId"], helper):
            lineage = self.task_view(board, run_id)["lineage"]
            self.assertEqual(lineage["rootLeadRunId"], lead["runId"])
        self.assertIsNone(self.task_view(board, helper)["lineage"]["leadOwnership"])
        self.assertIsNone(self.task_view(board, pending["runId"])["lineage"]["rootLeadRunId"])


class HistoryPagingTests(MicroTaskTestCase):
    def test_more_than_one_hundred_records_page_without_skips_or_duplicates(self):
        board = self.board(clock=FakeClock())
        for index in range(105):
            self.execution_task(board, f"bulk-{index:03d}")
        # Every row shares one created_at, so ordering is decided by the task_id tie.
        listing = self.history(board, limit=100)
        self.assertEqual(listing["total"], 105)
        self.assertEqual(len(listing["runs"]), 100)
        self.assertIsNotNone(listing["nextCursor"])
        collected, pages = self.page_all(board, page_size=40)
        self.assertEqual(len(collected), 105)
        self.assertEqual(pages, 3)
        identifiers = [row["runId"] for row in collected]
        self.assertEqual(len(set(identifiers)), 105)
        with board.store.db.read() as connection:
            expected = [
                row["task_id"]
                for row in connection.execute("SELECT task_id FROM tasks ORDER BY created_at DESC, task_id DESC")
            ]
        self.assertEqual(identifiers, expected)

    def test_a_new_insert_never_shifts_a_later_page(self):
        clock = FakeClock()
        board = self.board(clock=clock)
        for index in range(12):
            self.execution_task(board, f"bulk-{index:02d}")
        first = self.history(board, limit=5)
        cursor = first["nextCursor"]
        self.assertIsNotNone(cursor)
        clock.advance(1)
        fresh = self.execution_task(board, "inserted-later")
        second = self.history(board, limit=5, before=cursor)
        self.assertNotIn(fresh["runId"], [row["runId"] for row in first["runs"]])
        self.assertNotIn(fresh["runId"], [row["runId"] for row in second["runs"]])
        collected = [row["runId"] for row in first["runs"]] + [row["runId"] for row in second["runs"]]
        self.assertEqual(len(set(collected)), 10)
        # A complete walk still sees every row exactly once, with the insert newest.
        everything, _pages = self.page_all(board, page_size=5)
        self.assertEqual(len(everything), 13)
        self.assertEqual(len({row["runId"] for row in everything}), 13)
        self.assertEqual(everything[0]["runId"], fresh["runId"])

    def test_total_counts_the_filtered_set_before_the_page_limit(self):
        board = self.board()
        for index in range(4):
            self.execution_task(board, f"bulk-{index:02d}")
        cancelled = self.execution_task(board, "cancel-me")
        board.call("task_cancel", {"runId": cancelled["runId"], 'note': "test"})
        listing = self.history(board, limit=2, state="completed")
        self.assertEqual(listing["total"], 0)
        self.assertEqual(listing["runs"], [])
        self.assertIsNone(listing["nextCursor"])
        active = self.history(board, limit=1, state="cancelled")
        self.assertEqual(active["total"], 1)
        self.assertEqual(len(active["runs"]), 1)
        self.assertIsNone(active["nextCursor"])

    def test_filters_are_applied_before_pagination(self):
        board = self.board()
        project_a = self.workdir("project-a")
        project_b = self.workdir("project-b")
        (project_a / ".git").mkdir()
        (project_b / ".git").mkdir()
        first_group = [self.submit(board, request_id=f"a-{index}", cwd=str(project_a), kind="worktree")
                       for index in range(3)]
        for index in range(2):
            self.submit(board, request_id=f"b-{index}", cwd=str(project_b), kind="worktree")
        first_project = self.micro_task_of(board, first_group[0]["runId"])["project"]["id"]
        second_project = f"repo:{os.path.realpath(project_b)}"
        listing = self.history(board, projectId=first_project, filter="all", limit=1, rootsOnly=True)
        self.assertEqual(listing["total"], 3)
        self.assertEqual(len(listing["runs"]), 1)
        self.assertIsNotNone(listing["nextCursor"])
        collected, _pages = self.page_all(board, page_size=1, projectId=first_project, rootsOnly=True)
        self.assertEqual({row["runId"] for row in collected}, {row["runId"] for row in first_group})
        self.assertEqual(self.history(board, projectId=second_project, rootsOnly=True)["total"], 2)
        self.assertEqual(self.history(board, rootsOnly=True)["total"], 5)
        self.assertEqual(self.history(board, projectId="repo:/no/such/project")["total"], 0)

    def test_query_matches_task_project_and_buddy(self):
        board = self.board()
        work = self.workdir("searchable-project")
        submitted = self.submit(
            board, request_id="micro-task-1", cwd=str(work), task="Refactor the parser"
        )
        run_id = submitted["runId"]
        self.execution_task(board, "noise", cwd=self.workdir("unrelated"), task_text="unrelated workload")
        for value, expected in (
            ("refactor", [run_id]),
            ("parser", [run_id]),
            (run_id, [run_id]),
            ("searchable-project", [run_id]),
            ("deepseek-flash", [run_id]),
            ("dsh", [run_id]),
            ("nothing-matches-this", []),
        ):
            with self.subTest(value=value):
                listing = self.history(board, query=value)
                self.assertEqual([row["runId"] for row in listing["runs"]], expected)
                self.assertEqual(listing["total"], len(expected))

    def test_percent_and_underscore_in_a_query_are_literals(self):
        board = self.board()
        under = self.execution_task(board, "under_score")
        lookalike = self.execution_task(board, "underXscore")
        percent = self.execution_task(board, "percent", task_text="100% covered")
        thousand = self.execution_task(board, "thousand", task_text="1000 covered")
        matched = [row["runId"] for row in self.history(board, query="under_score")["runs"]]
        self.assertEqual(matched, [under["runId"]])
        self.assertNotIn(lookalike["runId"], matched)
        percent_matched = [row["runId"] for row in self.history(board, query="100%")["runs"]]
        self.assertEqual(percent_matched, [percent["runId"]])
        self.assertNotIn(thousand["runId"], percent_matched)

    def test_review_filter_follows_the_durable_result_and_acceptance(self):
        board = self.board()
        created = self.submit(board, request_id="review-me")
        run_id = created["runId"]
        self.assertEqual(self.history(board, filter="review")["total"], 0)
        self.finish_turn(board, self.claim(board, run_id=run_id))
        self.assertEqual([row["runId"] for row in self.history(board, filter="review")["runs"]], [run_id])
        delivered = board.call("workflow_get", {"runId": run_id})
        board.call("workflow_accept", {"runId": run_id, "artifactId": delivered["finalArtifactId"],
                                       "note": "checked the real output", "notRequired": "No separate target"})
        self.assertEqual(self.history(board, filter="review")["total"], 0)
        self.assertEqual(self.history(board, filter="active")["total"], 0)

    def test_cursor_and_parameter_validation_is_explicit(self):
        board = self.board()
        self.execution_task(board, "plain")
        page = self.history(board, limit=1)
        self.assertIsNone(page["nextCursor"])
        with self.assertRaises(BoardError) as raised:
            self.history(board, before="not-a-cursor")
        self.assertEqual(raised.exception.code, "INVALID_ARGUMENT")
        valid = micro_task.encode_cursor("2026-01-01T00:00:00.000Z", "run-1")
        with self.assertRaises(BoardError) as raised:
            self.history(board, limit=1, offset=1, before=valid)
        self.assertEqual(raised.exception.code, "INVALID_ARGUMENT")
        self.assertIn("mutually exclusive", str(raised.exception.message))
        for cursor in (
            "!!!!",
            "e30",  # {}
            valid + "=",
            "x" * (micro_task.CURSOR_MAX_LENGTH + 1),
            "W10",  # []
        ):
            with self.subTest(cursor=cursor):
                with self.assertRaises(BoardError) as raised:
                    self.history(board, before=cursor)
                self.assertEqual(raised.exception.code, "INVALID_ARGUMENT")
        for params in (
            {"query": "x" * (micro_task.QUERY_MAX_LENGTH + 1)},
            {"projectId": "x" * (micro_task.PROJECT_ID_MAX_LENGTH + 1)},
            {"hostId": "retired-host-filter"},
            {"filter": "everything"},
            {"rootsOnly": "true"},
            {"limit": 101},
            {"limit": 0},
            {"offset": -1},
            {"unknown": 1},
        ):
            with self.subTest(params=params):
                with self.assertRaises(BoardError) as raised:
                    self.history(board, **params)
                self.assertEqual(raised.exception.code, "INVALID_ARGUMENT")

    def test_keyset_and_offset_modes_agree_on_the_same_rows(self):
        board = self.board()
        for index in range(9):
            self.execution_task(board, f"bulk-{index}")
        first = self.history(board, limit=3)
        second = self.history(board, limit=3, before=first["nextCursor"])
        keyset_rows = [row["runId"] for row in [*first["runs"], *second["runs"]]]
        offset_rows = [row["runId"] for row in self.history(board, limit=6, offset=0)["runs"]]
        self.assertEqual(len(keyset_rows), 6)
        self.assertEqual(keyset_rows, offset_rows)
        window = self.history(board, limit=2, offset=2)
        self.assertEqual([row["runId"] for row in window["runs"]], offset_rows[2:4])
        self.assertEqual(window["total"], 9)

    def test_cursor_round_trips_through_its_own_encoding(self):
        cursor = micro_task.encode_cursor("2026-01-01T00:00:00.000Z", "run-1")
        self.assertEqual(micro_task.decode_cursor(cursor), ("2026-01-01T00:00:00.000Z", "run-1"))
        self.assertEqual(micro_task.keyset_where(*micro_task.decode_cursor(cursor))[1][2], "run-1")


class ShutdownHistoryTests(MicroTaskTestCase):
    def test_review_filter_waits_for_child_stop_before_paging_and_counts(self):
        clock = FakeClock()
        board = self.board(clock=clock, max_concurrent=2)
        self.register(board)
        older = self.submit(board, request_id="older-delivery", cwd=str(self.workdir("older")))
        self.finish_turn(board, self.claim(board, run_id=older["runId"], claim_request_id="older-result"))
        clock.advance(1)
        parent, helper_id = self.parent_and_helper(board)
        helper_claim = self.claim(board, run_id=helper_id, claim_request_id="helper-active")
        delivered = self.deliver_parent(board, parent)
        self.assertEqual(delivered["state"], "delivered")
        self.assertFalse(delivered["shutdown"]["descendantsConfirmed"])
        with patch.object(board.store, "_decorate", wraps=board.store._decorate) as decorate:
            page = self.history(board, filter="review", rootsOnly=True, limit=1)
        self.assertEqual(decorate.call_count, 1)
        self.assertEqual(page["total"], 1)
        self.assertEqual([row["runId"] for row in page["runs"]], [older["runId"]])
        self.assertEqual(board.store.recover_executions(), [])
        self.assertEqual(self.history(board, filter="review", rootsOnly=True)["total"], 1)
        self.finish_turn(board, helper_claim)
        self.assertTrue(board.call("workflow_get", {"runId": parent["runId"]})["shutdown"]["descendantsConfirmed"])
        page = self.history(board, filter="review", rootsOnly=True, limit=1)
        self.assertEqual(page["total"], 2)
        self.assertEqual([row["runId"] for row in page["runs"]], [parent["runId"]])
        self.assertIsNotNone(page["nextCursor"])

    def test_review_filter_excludes_a_parent_when_router_stop_reopens_a_boundary(self):
        board = self.board(max_concurrent=2)
        self.seed(board)
        with patch("hey_my_buddy.blackboard.routing.decision.DecisionCoordinator._adapter_available", return_value=(True, None)):
            parent, helper_id = self.parent_and_helper(board, routed=True)
        helper = board.call("workflow_get", {"runId": helper_id})
        router_id = helper["routing"]["taskId"]
        router = self.claim(board, "router", run_id=router_id, claim_request_id="router-active")["claim"]
        delivered = self.deliver_parent(board, parent)
        self.assertEqual(delivered["state"], "delivered")
        self.assertEqual(delivered["shutdown"]["unconfirmedRunIds"], [router_id])
        self.assertEqual(self.history(board, filter="review", rootsOnly=True)["total"], 0)
        board.call('execution_result', {'attemptId': router['attempt']['attemptId'], 'generation': router['attempt']['generation'], 'nonce': NONCE, 'status': 'cancelled', 'result': {}, 'shutdownConfirmed': True, 'exitCode': 0})
        stopped = board.call("workflow_get", {"runId": parent["runId"]})
        self.assertEqual(stopped["state"], "awaiting-host")
        self.assertEqual(stopped["shutdown"]["unconfirmedCount"], 0)
        self.assertEqual(self.history(board, filter="review", rootsOnly=True)["total"], 0)

    def test_active_filter_includes_execution_needing_reconciliation(self):
        board = self.board()
        task = self.execution_task(board, "uncertain-execution")
        claim = self.claim(board, "command-worker", run_id=task["runId"], claim_request_id="uncertain-claim")["claim"]
        board.call('execution_result', {'attemptId': claim['attempt']['attemptId'], 'generation': claim['attempt']['generation'], 'nonce': NONCE, 'status': 'cancelled', 'result': {'error': 'stop not observed'}, 'shutdownConfirmed': False})
        self.assertEqual(self.task_view(board, task["runId"])["state"], "running")
        self.assertEqual([row["runId"] for row in self.history(board, filter="active", limit=1)["runs"]], [task["runId"]])
        self.assertEqual(self.history(board, filter="review")["total"], 0)


class BulkDecoratedViewsTests(MicroTaskTestCase):
    def test_task_list_and_console_timeline_keep_helper_rows(self):
        board = self.board(max_concurrent=1)
        self.register(board)
        submitted = self.submit(board, request_id="micro-task-1")
        micro_task_id = submitted["runId"]
        self.finish_turn(board, self.claim(board), disposition="assistance")
        view = board.call("workflow_get", {"runId": micro_task_id})
        approved = self.decide(
            board,
            view,
            view["activeRequest"]["requestId"],
            helpers=[
                {
                    "title": "helper-1",
                    "task": "help out",
                    "cwd": str(self.workdir()),
                    "workspace": {"kind": "worktree", "access": "write"},
                }
            ],
        )
        helper_id = approved["children"][0]["taskId"]
        all_rows = self.history(board, limit=100)
        kinds = {row["runId"]: row["lineage"]["kind"] for row in all_rows["runs"]}
        self.assertEqual(kinds[micro_task_id], "micro-task")
        self.assertEqual(kinds[helper_id], "helper")
        timeline = board.call("macro_task_timeline", {"macroTaskId": f"run:{micro_task_id}"})
        timeline_kinds = {row["runId"]: row["kind"] for row in timeline["rows"]}
        self.assertEqual(timeline_kinds, {micro_task_id: "micro-task", helper_id: "helper"})
        self.assertEqual(timeline["totals"]["rows"], 2)
        self.assertFalse(timeline["truncated"]["rows"])
        roots = self.history(board, rootsOnly=True)
        self.assertEqual([row["runId"] for row in roots["runs"]], [micro_task_id])

    def test_helpers_and_decisions_never_appear_in_root_history(self):
        board = self.board()
        self.seed(board)
        with patch("hey_my_buddy.blackboard.routing.decision.DecisionCoordinator._adapter_available", return_value=(True, None)):
            routed = self.submit(board, request_id="route-1", buddy="route",
                                 task="Produce a verified implementation")
        listing = self.history(board, rootsOnly=True)
        self.assertEqual([row["runId"] for row in listing["runs"]], [routed["runId"]])
        self.assertEqual(listing["total"], 1)
        self.assertEqual(self.history(board)["total"], 2)


if __name__ == "__main__":
    unittest.main()
