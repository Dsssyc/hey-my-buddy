"""Project-local paging of macro task summaries over a private board."""
from __future__ import annotations

import unittest

from support import private_state_dir
from blackboard.tasks.test_console_macro_task_fixture import fixture_module, SOURCE
from hey_my_buddy.blackboard.tasks import macro_tasks
from hey_my_buddy.errors import BoardError


class MacroTaskGroupPagingTests(unittest.TestCase):
    def test_initial_read_includes_every_project_even_when_there_are_more_than_five(self):
        recipe = fixture_module.Recipe(macros=7, members_per_macro=1, plain=0,
                                       pending_roots=0, large_results=0, evidence_files=0,
                                       routed_roots=0, accepted_roots=0)
        with private_state_dir("macro-task-project-groups-") as root:
            with fixture_module.SyntheticBoard(root / "state", root / "runtime", SOURCE,
                                                recipe=recipe, seed=614) as fixture:
                with fixture.board.store.db.write() as connection:
                    for index, macro_id in enumerate(fixture.macros):
                        project_id = "standalone" if index == 0 else f"fictional-project-{index}"
                        connection.execute("INSERT OR IGNORE INTO projects(project_id,kind,path) VALUES(?,'git',?)",
                                           (project_id, f"/fictional/project-{index}"))
                        connection.execute("UPDATE macro_tasks SET project_id=? WHERE macro_task_id=?",
                                           (project_id, macro_id))
                        connection.execute("UPDATE tasks SET spec_json=json_set(spec_json,'$.cwd',?) WHERE task_id=("
                                           "SELECT run_id FROM workflow_runs WHERE macro_task_id=? ORDER BY created_at,rowid LIMIT 1)",
                                           (f"/fictional/project-{index}", macro_id))
                page = fixture.board.call("macro_task_list", {})
                self.assertEqual(len(page["groups"]), 7)
                self.assertEqual({group["id"] for group in page["groups"]},
                                 {"project:standalone", *(f"project:fictional-project-{index}" for index in range(1, 7))})
                self.assertTrue(all(group["total"] == 1 and len(group["macroTasks"]) == 1
                                    for group in page["groups"]))

    def test_all_groups_and_project_local_pages(self):
        recipe = fixture_module.Recipe(macros=7, members_per_macro=1, plain=0,
                                       pending_roots=0, large_results=0, evidence_files=0,
                                       routed_roots=0, accepted_roots=0)
        with private_state_dir("macro-task-group-paging-") as root:
            with fixture_module.SyntheticBoard(root / "state", root / "runtime", SOURCE,
                                                recipe=recipe, seed=613) as fixture:
                board = fixture.board
                first = board.call("macro_task_list", {})
                self.assertEqual(first["totals"]["all"], 7)
                self.assertEqual(len(first["groups"]), 1)
                project = first["groups"][0]
                self.assertEqual(project["id"], "project:" + project["project"]["id"])
                self.assertEqual(project["total"], 7)
                self.assertEqual(len(project["macroTasks"]), 5)
                self.assertIsNotNone(project["nextCursor"])
                self.assertEqual(len({item["macroTaskId"] for item in project["macroTasks"]}), 5)

                more = board.call("macro_task_list", {"group": project["id"],
                                                       "before": project["nextCursor"]})
                self.assertEqual(len(more["groups"]), 1)
                self.assertEqual(more["groups"][0]["total"], 7)
                self.assertEqual(len(more["groups"][0]["macroTasks"]), 2)
                self.assertIsNone(more["groups"][0]["nextCursor"])
                refreshed = board.call("macro_task_list", {"group": project["id"], "limit": 7})
                self.assertEqual([item["macroTaskId"] for item in refreshed["groups"][0]["macroTasks"]],
                                 [item["macroTaskId"] for item in project["macroTasks"] + more["groups"][0]["macroTasks"]])

                for request in ({"before": project["nextCursor"]},
                                {"group": "standalone", "before": project["nextCursor"]},
                                {"group": project["id"], "before": project["nextCursor"], "sort": "created-asc"},
                                {"projectId": project["project"]["id"]},
                                {"group": "project:"}, {"limit": 101}):
                    with self.subTest(request=request), self.assertRaises(BoardError):
                        board.call("macro_task_list", request)

                empty = board.call("macro_task_list", {"group": "standalone"})["groups"]
                self.assertEqual(empty, [{"id": "standalone", "project": None, "total": 0,
                                          "macroTasks": [], "nextCursor": None}])

    def test_list_group_plan_uses_indexed_matches_for_search_and_polling(self):
        with private_state_dir("macro-task-list-plan-") as root:
            with fixture_module.SyntheticBoard(root / "state", root / "runtime", SOURCE,
                                              recipe=fixture_module.Recipe.smoke()) as fixture:
                with fixture.board.store.db.read() as connection:
                    for query in (None, "fictional"):
                        applied = {"query": query} if query else {}
                        statement, values = macro_tasks._list_groups_statement(applied, "activity-desc", 5, None)
                        plan = [row[3] for row in connection.execute("EXPLAIN QUERY PLAN " + statement, values)]
                        with self.subTest(query=query):
                            self.assertFalse(any(step.startswith("SCAN m ") and "LEFT-JOIN" in step
                                                 for step in plan), plan)
                            self.assertTrue(any(step.startswith("SEARCH m ") and "(group_id=?)" in step
                                                for step in plan), plan)
                            self.assertFalse(any("manifest_owners" in step for step in plan), plan)
                            if not query:
                                self.assertNotIn("SCAN t", plan)
                                self.assertTrue(any("tasks_state_idx" in step for step in plan), plan)
                                self.assertTrue(any("workflow_runs_state_idx" in step for step in plan), plan)

    def test_polling_and_search_that_matches_every_group_have_identical_pages(self):
        with private_state_dir("macro-task-list-equivalence-") as root:
            with fixture_module.SyntheticBoard(root / "state", root / "runtime", SOURCE,
                                              recipe=fixture_module.Recipe.smoke()) as fixture:
                self._assert_equivalent_pages(fixture)

    def _assert_equivalent_pages(self, fixture):
        board = fixture.board
        token = "universal-list-match"
        root_cwd = fixture.state / "fictional-project"
        empty = board.call("workflow_create", {"cwd": str(root_cwd), "title": token, "goal": "No runs"})
        # Cover an active child at a Lead boundary, a stopped child ready for
        # review, a top-level Host boundary and an ended standalone group.
        standalone = [fixture._submit(name, lead=True)["runId"]
                      for name in ("standalone-active", "standalone-review")]
        macro_leads = [fixture._submit(name, lead=True, macro_task=name)
                       for name in ("macro-active", "macro-review")]
        for lead in (*standalone, *(view["runId"] for view in macro_leads)):
            fixture._delegate_owned(lead, lead + "-child")
        host = fixture._submit("standalone-host")["runId"]
        fixture._finish(host, disposition="attention")
        other_host = fixture._submit("standalone-other-host")["runId"]
        fixture._finish(other_host, disposition="attention")
        ended = fixture._submit("standalone-ended")["runId"]
        fixture._finish(ended)
        with board.store.db.write() as connection:
            connection.execute("UPDATE macro_tasks SET title=title||?", (" " + token,))
            connection.execute("UPDATE workflow_runs SET title=title||?", (" " + token,))
            # Settle the parents so the children alone determine filter membership.
            for parent in (*standalone, *(view["runId"] for view in macro_leads), ended):
                connection.execute(
                    "UPDATE workflow_runs SET state='concluded',workspace_id=NULL,workspace_manifest_sha256=NULL"
                    " WHERE run_id=?", (parent,),
                )
                connection.execute("UPDATE tasks SET state='completed',accepted_at=updated_at WHERE task_id=?",
                                   (parent,))
            for child in (fixture.owned[-3], fixture.owned[-1]):
                connection.execute("UPDATE workflow_runs SET state='delivered' WHERE run_id=?", (child,))
                connection.execute("UPDATE tasks SET state='completed' WHERE task_id=?", (child,))

        def read(request, *, search):
            params = {**request, **({"query": token} if search else {})}
            return board.call("macro_task_list", params)

        def assert_same_page(fast, general, request):
            # The cursor fingerprint intentionally binds query. Verify every
            # other cursor field, then compare the complete response.
            for left, right in zip(fast["groups"], general["groups"]):
                if left["nextCursor"] and right["nextCursor"]:
                    filters = {"group": left["id"]}
                    if request["filter"] != "all":
                        filters["filter"] = request["filter"]
                    self.assertEqual(
                        macro_tasks._decode_cursor(left["nextCursor"], filters, request["sort"]),
                        macro_tasks._decode_cursor(right["nextCursor"], {**filters, "query": token}, request["sort"]),
                    )
                    right["nextCursor"] = left["nextCursor"]
            self.assertEqual(fast, general)

        expected_standalone = {
            "all": {*standalone, host, other_host, ended},
            "active": {standalone[0], host, other_host},
            "host": {host, other_host},
            "review": {standalone[1]},
        }
        for filter_name, expected in expected_standalone.items():
            for sort in sorted(macro_tasks.LIST_SORTS):
                with self.subTest(filter=filter_name, sort=sort):
                    request = {"filter": filter_name, "sort": sort, "limit": 1}
                    first = read(request, search=False)
                    reference = read(request, search=True)
                    cursors = {group["id"]: group["nextCursor"] for group in reference["groups"]}
                    assert_same_page(first, reference, request)
                    seen = {}
                    for group in first["groups"]:
                        group_id = group["id"]
                        seen[group_id] = ids = set()
                        fast_group, general_before = group, cursors[group_id]
                        while True:
                            ids.update(summary["macroTaskId"] for summary in fast_group["macroTasks"])
                            if not fast_group["nextCursor"]:
                                break
                            scoped = {**request, "group": group_id}
                            fast = read({**scoped, "before": fast_group["nextCursor"]}, search=False)
                            general = read({**scoped, "before": general_before}, search=True)
                            general_before = general["groups"][0]["nextCursor"]
                            assert_same_page(fast, general, request)
                            fast_group = fast["groups"][0]
                        self.assertEqual(len(ids), group["total"])
                    self.assertEqual(seen["standalone"], {"run:" + run for run in expected})
                    all_seen = set.union(*seen.values())
                    self.assertEqual(empty["macroTaskId"] in all_seen, filter_name == "all")
                    for view, child_filter in zip(macro_leads, ("active", "review")):
                        self.assertEqual(view["macroTaskId"] in all_seen, filter_name in ("all", child_filter))
