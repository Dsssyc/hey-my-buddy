"""Read-only delegation provenance and keyset task-history paging.

Everything runs against the real store and the real service through the in-process
board harness: private state directories, no model call, no worker process. The
workspace module is the deterministic double, exactly like the governed workflow
tests, so a temporary execution worktree can be distinguished from the source
project without running Git.
"""
from __future__ import annotations

import json
import os
import unittest
from unittest.mock import patch

from support import FakeClock
from test_decision import DecisionTestCase
from test_workflow import CONFIGURATION, NONCE, WorkflowTestCase

from buddy import delegation
from buddy.errors import BoardError


class DelegationTestCase(WorkflowTestCase):
    """Shared fixtures: governed goals, ordinary tasks and helper/decision runs."""

    seed = DecisionTestCase.seed

    def setUp(self) -> None:
        super().setUp()
        self.catalog_fixture()

    def execution_task(self, board, request_id: str, *, cwd=None, task_text=None):
        return board.call(
            "task_submit",
            {
                "requestId": request_id,
                "task": task_text or f"ordinary {request_id}",
                "cwd": str(cwd or self.workdir()),
                "adapter": "command",
                "argv": ["/bin/echo", request_id],
            },
        )["task"]

    def task_view(self, board, run_id: str) -> dict:
        return board.call("task_get", {"runId": run_id})["task"]

    def delegation_of(self, board, run_id: str) -> dict:
        return self.task_view(board, run_id)["delegation"]

    def history(self, board, **params):
        return board.call("task_list", params)

    def take_over(self, board, view, host, command, *, console=False):
        params = {"runId": view["runId"], "commandId": command, "newHostId": host,
                  "expectedOwnerGeneration": view["ownerGeneration"]}
        if console:
            response = board.store.workflow.takeover(params, console_authority={"sessionId": "private-test-console"})
        else:
            response = board.call("workflow_takeover", {**params, **self.control(view)})
        self.controls[view["runId"]] = response["control"]
        return response

    def helper_spec(self, request_id="helper-1", **extra):
        return {"requestId": request_id, "task": "bounded helper", "cwd": str(self.workdir(request_id)),
                "executionWorkspace": {"kind": "worktree", "access": "write"}, **extra}

    def parent_and_helper(self, board, *, routed=False):
        self.register(board)
        parent = self.submit(board, request_id="parent", host_id="host-a", cwd=str(self.workdir("parent")))
        self.finish_turn(board, self.claim(board), disposition="assistance")
        view = board.call("workflow_get", {"runId": parent["runId"]})
        params = {"runId": view["runId"], "requestId": view["activeRequest"]["requestId"],
                  "commandId": "delegate-helper", "expectedRevision": view["revision"], "decision": "approve",
                  "autoContinue": False, "helpers": [self.helper_spec(**({} if routed else CONFIGURATION))],
                  **self.control(parent)}
        approved = board.call("workflow_decide", params)
        return parent, approved["children"][0]["taskId"]

    def deliver_parent(self, board, parent):
        view = board.call("workflow_get", {"runId": parent["runId"]})
        self.continue_run(board, view, command_id="parent-continues", helper_policy="keep", configuration=CONFIGURATION)
        self.register(board, "parent-worker")
        claim = self.claim(board, "parent-worker", run_id=parent["runId"], claim_request_id="parent-result")
        self.finish_turn(board, claim, worker_id="parent-worker")
        return board.call("workflow_get", {"runId": parent["runId"]})

    def remove_source_evidence(self, board, run_id):
        # Reproduce records from before these optional event facts were written.
        # Existing owner/actor labels remain, and must never be parsed as evidence.
        with board.store.db.write() as connection:
            connection.execute("UPDATE events SET payload_json=json_remove(payload_json, '$.governed.sourceHostId',"
                               " '$.sourceHostId', '$.previousHostId', '$.previousOwnerGeneration') WHERE task_id=?", (run_id,))

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


class GoalProvenanceTests(DelegationTestCase):
    def test_goal_reports_source_and_current_host_with_the_source_project(self):
        board = self.board()
        submitted = self.submit(board, request_id="goal-1", host_id="host-a", kind="worktree")
        run_id = submitted["runId"]
        view = self.task_view(board, run_id)
        metadata = view["delegation"]
        source = os.path.realpath(str(self.workdir()))
        worktree = view["cwd"]
        # The execution checkout is a temporary worktree; the project stays the
        # source Goal's original cwd and its saved repository identity.
        self.assertNotEqual(worktree, source)
        self.assertEqual(metadata["kind"], "goal")
        self.assertEqual(metadata["sourceHostId"], "host-a")
        self.assertEqual(metadata["currentHostId"], "host-a")
        self.assertIsNone(metadata["parentRunId"])
        self.assertEqual(metadata["rootRunId"], run_id)
        self.assertEqual(metadata["project"]["path"], source)
        self.assertEqual(metadata["project"]["label"], "work")
        self.assertEqual(metadata["project"]["id"], f"repo:{source}")
        self.assertEqual(metadata["configuration"], CONFIGURATION)
        # The existing workflow extension is unchanged and still present.
        self.assertEqual(view["workflow"]["hostId"], "host-a")
        self.assertEqual(view["workflowState"], "executing")

    def test_takeover_never_passes_the_current_host_off_as_the_source(self):
        board = self.board()
        submitted = self.submit(board, request_id="goal-1", host_id="host-a")
        run_id = submitted["runId"]
        board.call(
            "workflow_takeover",
            {
                "runId": run_id,
                "commandId": "takeover-1",
                "newHostId": "host-b",
                "expectedOwnerGeneration": 1,
                **submitted["control"],
            },
        )
        metadata = self.delegation_of(board, run_id)
        self.assertEqual(metadata["currentHostId"], "host-b")
        self.assertEqual(self.task_view(board, run_id)["workflow"]["hostId"], "host-b")
        self.assertEqual(metadata["sourceHostId"], "host-a")
        with board.store.db.read() as connection:
            admission = json.loads(connection.execute("SELECT payload_json FROM events WHERE task_id=? AND kind='task.submitted'", (run_id,)).fetchone()[0])
            takeover = json.loads(connection.execute("SELECT payload_json FROM events WHERE task_id=? AND kind='workflow.takeover'", (run_id,)).fetchone()[0])
        self.assertEqual(admission["governed"]["sourceHostId"], "host-a")
        self.assertEqual(takeover["previousHostId"], "host-a")
        self.assertEqual(takeover["previousOwnerGeneration"], 1)
        # Root lineage and project survive the ownership change.
        self.assertEqual(metadata["rootRunId"], run_id)
        self.assertEqual(metadata["project"]["path"], os.path.realpath(str(self.workdir())))

    def test_helper_keeps_the_host_that_delegated_it_across_a_root_takeover(self):
        board = self.board(max_concurrent=1)
        self.register(board)
        submitted = self.submit(board, request_id="goal-1", host_id="host-a")
        goal_id = submitted["runId"]
        self.finish_turn(board, self.claim(board), disposition="assistance")
        view = board.call("workflow_get", {"runId": goal_id})
        approved = self.decide(
            board,
            view,
            view["activeRequest"]["requestId"],
            helpers=[
                {
                    "requestId": "helper-1",
                    "task": "help out",
                    "cwd": str(self.workdir()),
                    "executionWorkspace": {"kind": "worktree", "access": "write"},
                }
            ],
        )
        helper_id = approved["children"][0]["taskId"]
        self.assertEqual(self.delegation_of(board, helper_id)["sourceHostId"], "host-a")
        board.call(
            "workflow_takeover",
            {
                "runId": goal_id,
                "commandId": "takeover-1",
                "newHostId": "host-b",
                "expectedOwnerGeneration": 1,
                **submitted["control"],
            },
        )
        goal = self.delegation_of(board, goal_id)
        helper = self.delegation_of(board, helper_id)
        self.assertEqual(goal["sourceHostId"], "host-a")
        self.assertEqual(goal["currentHostId"], "host-b")
        # The helper's own generation-1 creation record still names the Host that
        # authorized it, so the original delegating Host is recovered instead of the
        # post-takeover controller.
        self.assertEqual(helper["sourceHostId"], "host-a")
        self.assertEqual(helper["currentHostId"], "host-b")
        self.assertEqual(helper["rootRunId"], goal_id)

    def test_old_generation_one_source_is_fixed_by_its_first_new_takeover(self):
        board = self.board()
        original = self.submit(board, host_id="host-a")
        self.remove_source_evidence(board, original["runId"])
        self.assertEqual(self.delegation_of(board, original["runId"])["sourceHostId"], "host-a")
        second = self.take_over(board, original, "host-b", "old-first-takeover")
        self.take_over(board, second, "host-c", "old-second-takeover")
        metadata = self.delegation_of(board, original["runId"])
        self.assertEqual(metadata["sourceHostId"], "host-a")
        self.assertEqual(metadata["currentHostId"], "host-c")

    def test_history_without_source_evidence_reports_unknown_after_takeover(self):
        board = self.board()
        original = self.submit(board, host_id="host-a")
        second = self.take_over(board, original, "host-b", "historical-takeover")
        self.remove_source_evidence(board, original["runId"])
        self.take_over(board, second, "host-c", "new-later-takeover")
        metadata = self.delegation_of(board, original["runId"])
        self.assertIsNone(metadata["sourceHostId"])
        self.assertEqual(metadata["currentHostId"], "host-c")
        self.assertEqual(self.history(board, hostId="host-a")["total"], 0)

    def test_helper_source_survives_its_own_takeovers_without_changing_root_controller(self):
        board = self.board()
        parent, child = self.parent_and_helper(board)
        child_view = board.call("workflow_get", {"runId": child})
        child_view = self.take_over(board, child_view, "child-host-b", "child-first", console=True)
        self.take_over(board, child_view, "child-host-c", "child-second", console=True)
        metadata = self.delegation_of(board, child)
        self.assertEqual(metadata["sourceHostId"], "host-a")
        self.assertEqual(metadata["currentHostId"], "host-a")
        self.assertEqual(metadata["rootRunId"], parent["runId"])

    def test_root_generation_one_cannot_supply_a_deep_helpers_missing_source(self):
        board = self.board()
        parent, child = self.parent_and_helper(board)
        self.finish_turn(board, self.claim(board, run_id=child, claim_request_id="child-yields"), disposition="assistance")
        view = board.call("workflow_get", {"runId": parent["runId"]})
        self.decide(board, view, view["activeRequest"]["requestId"], command_id="deep-helper", helpers=[self.helper_spec("grandchild")])
        grandchild = board.call("workflow_get", {"runId": child})["children"][0]["taskId"]
        view = board.call("workflow_get", {"runId": grandchild})
        view = self.take_over(board, view, "grandchild-host-b", "deep-first", console=True)
        self.take_over(board, view, "grandchild-host-c", "deep-second", console=True)
        self.remove_source_evidence(board, grandchild)
        metadata = self.delegation_of(board, grandchild)
        self.assertEqual(board.call("workflow_get", {"runId": parent["runId"]})["ownerGeneration"], 1)
        self.assertIsNone(metadata["sourceHostId"])
        self.assertEqual(metadata["currentHostId"], "host-a")
        self.assertEqual(metadata["parentRunId"], child)

    def test_proxy_delegation_records_the_actual_authorizing_host_after_root_takeover(self):
        board = self.board()
        self.seed(board)
        parent, child = self.parent_and_helper(board)
        self.finish_turn(board, self.claim(board, run_id=child, claim_request_id="old-leaf-yields"), disposition="assistance")
        view = board.call("workflow_get", {"runId": parent["runId"]})
        view = self.take_over(board, view, "host-b", "new-controller")
        with patch("buddy.decision.DecisionCoordinator._adapter_available", return_value=(True, None)):
            board.call("workflow_decide", {"runId": parent["runId"], "requestId": view["activeRequest"]["requestId"],
                       "commandId": "new-deep-helper", "expectedRevision": view["revision"], "decision": "approve",
                       "helpers": [self.helper_spec("new-grandchild")], **self.control(view)})
        grandchild = board.call("workflow_get", {"runId": child})["children"][0]["taskId"]
        created = board.call("workflow_get", {"runId": grandchild})
        router_id = created["routing"]["taskId"]
        self.assertEqual(created["hostId"], "host-a", "provenance must not change the helper's authorization contract")
        self.assertEqual(self.delegation_of(board, grandchild)["sourceHostId"], "host-b")
        self.assertEqual(self.delegation_of(board, router_id)["sourceHostId"], "host-b")
        root = board.call("workflow_get", {"runId": parent["runId"]})
        self.take_over(board, root, "host-c", "later-controller")
        self.assertEqual(self.delegation_of(board, child)["sourceHostId"], "host-a")
        for run_id in (grandchild, router_id):
            metadata = self.delegation_of(board, run_id)
            self.assertEqual(metadata["sourceHostId"], "host-b")
            self.assertEqual(metadata["currentHostId"], "host-c")

    def test_helper_takes_lineage_project_and_hosts_from_its_root_goal(self):
        board = self.board(max_concurrent=1)
        self.register(board)
        submitted = self.submit(board, request_id="goal-1", host_id="host-a", kind="worktree")
        goal_id = submitted["runId"]
        self.finish_turn(board, self.claim(board), disposition="assistance")
        view = board.call("workflow_get", {"runId": goal_id})
        helper_cwd = self.workdir("helper-project")
        approved = self.decide(
            board,
            view,
            view["activeRequest"]["requestId"],
            helpers=[
                {
                    "requestId": "helper-1",
                    "task": "an isolated helper",
                    "cwd": str(helper_cwd),
                    "executionWorkspace": {"kind": "worktree", "access": "write"},
                }
            ],
        )
        helper_id = approved["children"][0]["taskId"]
        helper_view = self.task_view(board, helper_id)
        goal = self.delegation_of(board, goal_id)
        helper = helper_view["delegation"]
        self.assertEqual(helper["kind"], "helper")
        self.assertEqual(helper["parentRunId"], goal_id)
        self.assertEqual(helper["rootRunId"], goal_id)
        self.assertEqual(helper["sourceHostId"], "host-a")
        self.assertEqual(helper["currentHostId"], "host-a")
        # The project is the root Goal's, never the helper's own temporary checkout
        # and never the helper's declared cwd.
        self.assertEqual(helper["project"], goal["project"])
        self.assertNotEqual(helper_view["cwd"], helper["project"]["path"])
        self.assertNotEqual(helper["project"]["id"], f"repo:{os.path.realpath(str(helper_cwd))}")
        self.assertEqual(helper["configuration"], CONFIGURATION)
        self.assertEqual(goal["kind"], "goal")

    def test_same_named_projects_do_not_share_one_project_id(self):
        board = self.board()
        first = self.workdir("one")
        second = self.workdir("two")
        (first / "proj").mkdir()
        (second / "proj").mkdir()
        one = self.submit(board, request_id="goal-1", host_id="host-a", cwd=str(first / "proj"))
        two = self.submit(board, request_id="goal-2", host_id="host-b", cwd=str(second / "proj"))
        left = self.delegation_of(board, one["runId"])
        right = self.delegation_of(board, two["runId"])
        self.assertEqual(left["project"]["label"], "proj")
        self.assertEqual(right["project"]["label"], "proj")
        self.assertNotEqual(left["project"]["id"], right["project"]["id"])
        self.assertNotEqual(left["project"]["path"], right["project"]["path"])
        self.assertEqual(
            [row["runId"] for row in self.history(board, projectId=left["project"]["id"])["runs"]],
            [one["runId"]],
        )

    def test_execution_task_without_a_source_goal_reports_unknown_hosts(self):
        board = self.board()
        created = self.execution_task(board, "plain-1")
        metadata = self.delegation_of(board, created["runId"])
        self.assertEqual(metadata["kind"], "execution")
        self.assertIsNone(metadata["sourceHostId"])
        self.assertIsNone(metadata["currentHostId"])
        self.assertIsNone(metadata["parentRunId"])
        self.assertIsNone(metadata["rootRunId"])
        self.assertEqual(metadata["project"]["path"], os.path.realpath(str(self.workdir())))
        self.assertEqual(metadata["project"]["id"], os.path.realpath(str(self.workdir())))
        # The command adapter has no complete execution configuration, so none is
        # invented for it.
        self.assertIsNone(metadata["configuration"])

    def test_internal_routing_decision_belongs_to_its_owning_goal(self):
        board = self.board()
        self.seed(board)
        with patch("buddy.decision.DecisionCoordinator._adapter_available", return_value=(True, None)):
            submitted = board.call(
                "workflow_submit",
                {
                    "requestId": "route-1",
                    "hostId": "host-a",
                    "submissionToken": "s" * 32,
                    "task": "Produce a verified implementation",
                    "cwd": str(self.workdir()),
                    "executionWorkspace": {"kind": "existing", "access": "write"},
                },
            )
        goal_id = submitted["runId"]
        router_id = board.call("workflow_get", {"runId": goal_id})["routing"]["taskId"]
        self.assertIsNotNone(router_id)
        goal = self.delegation_of(board, goal_id)
        router = self.delegation_of(board, router_id)
        self.assertEqual(router["kind"], "decision")
        # The relation is the authoritative workflow_routes link, never a parsed label.
        self.assertEqual(router["parentRunId"], goal_id)
        self.assertEqual(router["rootRunId"], goal_id)
        self.assertEqual(router["sourceHostId"], "host-a")
        self.assertEqual(router["currentHostId"], "host-a")
        self.assertEqual(router["project"], goal["project"])
        self.assertIsNone(router["configuration"])
        self.assertEqual([row["runId"] for row in self.history(board, rootsOnly=True)["runs"]], [goal_id])

    def test_selection_decision_outside_a_goal_is_classified_and_has_no_lineage(self):
        board = self.board()
        self.seed(board)
        created = board.call("selection_request", {"requestId": "pick-1", "task": "which model"})
        self.assertEqual(created["status"], "queued")
        with board.store.db.read() as connection:
            task_id = connection.execute(
                "SELECT task_id FROM decision_requests WHERE decision_id=?", (created["decisionId"],)
            ).fetchone()["task_id"]
        metadata = self.delegation_of(board, task_id)
        self.assertEqual(metadata["kind"], "decision")
        self.assertIsNone(metadata["parentRunId"])
        self.assertIsNone(metadata["rootRunId"])
        self.assertIsNone(metadata["sourceHostId"])
        self.assertIsNone(metadata["currentHostId"])
        self.assertEqual(metadata["project"], {"id": "internal-decisions", "path": None, "label": "内部决策"})
        self.assertEqual(self.history(board, rootsOnly=True)["total"], 0)

    def test_every_decorated_view_carries_the_same_metadata(self):
        board = self.board()
        submitted = self.submit(board, request_id="goal-1", host_id="host-a")
        run_id = submitted["runId"]
        with board.store.db.read() as connection:
            task = connection.execute("SELECT * FROM tasks WHERE task_id=?", (run_id,)).fetchone()
            resolved = delegation.resolve(connection, task)
            self.assertEqual(resolved, self.delegation_of(board, run_id))
            # The batch read returns exactly the rows that exist; an unknown id is
            # absent instead of being invented.
            self.assertEqual(delegation.resolve_many(connection, [run_id, "missing-run"]), {run_id: resolved})
            self.assertEqual(delegation.resolve_many(connection, []), {})
        listed = self.history(board, limit=100)["runs"][0]["delegation"]
        self.assertEqual(listed, self.delegation_of(board, run_id))
        snapshot = board.call("console_snapshot", {})
        self.assertEqual(snapshot["tasks"]["runs"][0]["delegation"], self.delegation_of(board, run_id))
        self.assertIsNone(snapshot["tasks"]["nextCursor"])
        self.assertEqual(self.task_view(board, run_id)["delegation"], listed)
        compact_task = board.call("workflow_get", {"runId": run_id})["task"]
        self.assertEqual(compact_task["delegation"], listed)
        self.assertNotIn("task", compact_task)
        self.assertNotIn("spec", compact_task)


class HistoryPagingTests(DelegationTestCase):
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
        board.call("task_cancel", {"runId": cancelled["runId"], "reason": "test"})
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
        host_a = [
            self.submit(
                board,
                request_id=f"a-{index}",
                host_id="host-a",
                cwd=str(self.workdir(f"a-{index}")),
                kind="worktree",
            )
            for index in range(3)
        ]
        self.submit(
            board, request_id="b-0", host_id="host-b", cwd=str(self.workdir("b-0")), kind="worktree"
        )
        self.submit(
            board, request_id="b-1", host_id="host-b", cwd=str(self.workdir("b-1")), kind="worktree"
        )
        listing = self.history(board, hostId="host-a", filter="all", limit=1, rootsOnly=True)
        self.assertEqual(listing["total"], 3)
        self.assertEqual(len(listing["runs"]), 1)
        self.assertIsNotNone(listing["nextCursor"])
        collected, _pages = self.page_all(board, page_size=1, hostId="host-a", rootsOnly=True)
        self.assertEqual({row["runId"] for row in collected}, {row["runId"] for row in host_a})
        self.assertEqual(self.history(board, hostId="host-b", rootsOnly=True)["total"], 2)
        self.assertEqual(self.history(board, rootsOnly=True)["total"], 5)
        self.assertEqual(self.history(board, projectId="repo:/no/such/project")["total"], 0)

    def test_query_matches_objective_project_hosts_and_configuration(self):
        board = self.board()
        work = self.workdir("searchable-project")
        submitted = self.submit(
            board, request_id="goal-1", host_id="host-zed", cwd=str(work), task="Refactor the parser"
        )
        run_id = submitted["runId"]
        self.execution_task(board, "noise", cwd=self.workdir("unrelated"), task_text="unrelated workload")
        for value, expected in (
            ("refactor", [run_id]),
            ("parser", [run_id]),
            (run_id, [run_id]),
            ("searchable-project", [run_id]),
            ("host-zed", [run_id]),
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
        created = self.execution_task(board, "review-me")
        run_id = created["runId"]
        self.assertEqual(self.history(board, filter="review")["total"], 0)
        client = board.client()
        client.register_worker("worker-1", adapter="command", capabilities=["command"])
        claim = client.claim("worker-1", "claim-1", "n" * 32, task_id=run_id)["claim"]
        client.submit_result(
            "worker-1",
            claim["attempt"]["attemptId"],
            claim["attempt"]["generation"],
            "n" * 32,
            {"status": "ok", "result": {"finalText": "done"}, "shutdownConfirmed": True, "exitCode": 0},
        )
        self.assertEqual([row["runId"] for row in self.history(board, filter="review")["runs"]], [run_id])
        board.call("task_acknowledge", {"runId": run_id, "verdict": "accepted", "note": "checked the real output"})
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
        valid = delegation.encode_cursor("2026-01-01T00:00:00.000Z", "run-1")
        with self.assertRaises(BoardError) as raised:
            self.history(board, limit=1, offset=1, before=valid)
        self.assertEqual(raised.exception.code, "INVALID_ARGUMENT")
        self.assertIn("mutually exclusive", str(raised.exception.message))
        for cursor in (
            "!!!!",
            "e30",  # {}
            valid + "=",
            "x" * (delegation.CURSOR_MAX_LENGTH + 1),
            "W10",  # []
        ):
            with self.subTest(cursor=cursor):
                with self.assertRaises(BoardError) as raised:
                    self.history(board, before=cursor)
                self.assertEqual(raised.exception.code, "INVALID_ARGUMENT")
        for params in (
            {"query": "x" * (delegation.QUERY_MAX_LENGTH + 1)},
            {"projectId": "x" * (delegation.PROJECT_ID_MAX_LENGTH + 1)},
            {"hostId": "x" * (delegation.HOST_ID_MAX_LENGTH + 1)},
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
        cursor = delegation.encode_cursor("2026-01-01T00:00:00.000Z", "run-1")
        self.assertEqual(delegation.decode_cursor(cursor), ("2026-01-01T00:00:00.000Z", "run-1"))
        self.assertEqual(delegation.keyset_where(*delegation.decode_cursor(cursor))[1][2], "run-1")


class ShutdownHistoryTests(DelegationTestCase):
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
        with patch.object(board.store, "_now_minus", return_value="9999-01-01T00:00:00.000Z"):
            self.assertEqual(board.store.mark_expired_leases(), 1)
        self.assertEqual(self.history(board, filter="review", rootsOnly=True)["total"], 1)
        self.finish_turn(board, helper_claim)
        self.assertTrue(board.call("workflow_get", {"runId": parent["runId"]})["shutdown"]["descendantsConfirmed"])
        page = self.history(board, filter="review", rootsOnly=True, limit=1)
        self.assertEqual(page["total"], 2)
        self.assertEqual([row["runId"] for row in page["runs"]], [parent["runId"]])
        self.assertIsNotNone(page["nextCursor"])

    def test_review_filter_waits_for_a_descendants_fenced_router_to_stop(self):
        board = self.board(max_concurrent=2)
        self.seed(board)
        with patch("buddy.decision.DecisionCoordinator._adapter_available", return_value=(True, None)):
            parent, helper_id = self.parent_and_helper(board, routed=True)
        helper = board.call("workflow_get", {"runId": helper_id})
        router_id = helper["routing"]["taskId"]
        board.call("worker_register", {"workerId": "router", "adapter": "decision", "capabilities": ["decision"]})
        router = self.claim(board, "router", run_id=router_id, claim_request_id="router-active")["claim"]
        view = board.call("workflow_get", {"runId": parent["runId"]})
        self.take_over(board, view, "host-b", "fence-selection")
        delivered = self.deliver_parent(board, parent)
        self.assertEqual(delivered["state"], "delivered")
        self.assertEqual(delivered["shutdown"]["unconfirmedRunIds"], [router_id])
        self.assertEqual(self.history(board, filter="review", rootsOnly=True)["total"], 0)
        board.call("worker_result", {"workerId": "router", "attemptId": router["attempt"]["attemptId"],
                   "generation": router["attempt"]["generation"], "nonce": NONCE, "status": "cancelled",
                   "result": {}, "shutdownConfirmed": True, "exitCode": 0})
        stopped = board.call("workflow_get", {"runId": parent["runId"]})
        self.assertEqual(stopped["state"], "delivered")
        self.assertEqual(stopped["shutdown"]["unconfirmedCount"], 0)
        self.assertEqual([row["runId"] for row in self.history(board, filter="review", rootsOnly=True)["runs"]], [parent["runId"]])

    def test_active_filter_includes_execution_needing_reconciliation(self):
        board = self.board()
        task = self.execution_task(board, "uncertain-execution")
        board.call("worker_register", {"workerId": "command-worker", "adapter": "command", "capabilities": ["command"]})
        claim = self.claim(board, "command-worker", run_id=task["runId"], claim_request_id="uncertain-claim")["claim"]
        board.call("worker_result", {"workerId": "command-worker", "attemptId": claim["attempt"]["attemptId"],
                   "generation": claim["attempt"]["generation"], "nonce": NONCE, "status": "cancelled",
                   "result": {"error": "stop not observed"}, "shutdownConfirmed": False})
        self.assertEqual(self.task_view(board, task["runId"])["state"], "reconciliation-needed")
        self.assertEqual([row["runId"] for row in self.history(board, filter="active", limit=1)["runs"]], [task["runId"]])
        self.assertEqual(self.history(board, filter="review")["total"], 0)


class BulkDecoratedViewsTests(DelegationTestCase):
    def test_task_list_and_snapshot_keep_helper_and_decision_rows(self):
        board = self.board(max_concurrent=1)
        self.register(board)
        submitted = self.submit(board, request_id="goal-1", host_id="host-a")
        goal_id = submitted["runId"]
        self.finish_turn(board, self.claim(board), disposition="assistance")
        view = board.call("workflow_get", {"runId": goal_id})
        approved = self.decide(
            board,
            view,
            view["activeRequest"]["requestId"],
            helpers=[
                {
                    "requestId": "helper-1",
                    "task": "help out",
                    "cwd": str(self.workdir()),
                    "executionWorkspace": {"kind": "worktree", "access": "write"},
                }
            ],
        )
        helper_id = approved["children"][0]["taskId"]
        all_rows = self.history(board, limit=100)
        kinds = {row["runId"]: row["delegation"]["kind"] for row in all_rows["runs"]}
        self.assertEqual(kinds[goal_id], "goal")
        self.assertEqual(kinds[helper_id], "helper")
        snapshot = board.call("console_snapshot", {})
        snapshot_kinds = {row["runId"]: row["delegation"]["kind"] for row in snapshot["tasks"]["runs"]}
        self.assertEqual(snapshot_kinds[helper_id], "helper")
        self.assertEqual(snapshot["tasks"]["total"], 2)
        self.assertIsNone(snapshot["tasks"]["nextCursor"])
        roots = self.history(board, rootsOnly=True)
        self.assertEqual([row["runId"] for row in roots["runs"]], [goal_id])

    def test_helpers_and_decisions_never_appear_in_root_history(self):
        board = self.board()
        self.seed(board)
        with patch("buddy.decision.DecisionCoordinator._adapter_available", return_value=(True, None)):
            routed = board.call(
                "workflow_submit",
                {
                    "requestId": "route-1",
                    "hostId": "host-a",
                    "submissionToken": "s" * 32,
                    "task": "Produce a verified implementation",
                    "cwd": str(self.workdir()),
                    "executionWorkspace": {"kind": "existing", "access": "write"},
                },
            )
        listing = self.history(board, rootsOnly=True)
        self.assertEqual([row["runId"] for row in listing["runs"]], [routed["runId"]])
        self.assertEqual(listing["total"], 1)
        self.assertEqual(self.history(board)["total"], 2)


if __name__ == "__main__":
    unittest.main()
