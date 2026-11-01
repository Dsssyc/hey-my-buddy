"""Project-local paging of macro task summaries over a private board."""
from __future__ import annotations

import unittest

from support import private_state_dir
from blackboard.tasks.test_console_macro_task_fixture import fixture_module, SOURCE
from hey_my_buddy.errors import BoardError


class MacroTaskGroupPagingTests(unittest.TestCase):
    def test_initial_read_includes_every_project_even_when_there_are_more_than_five(self):
        recipe = fixture_module.Recipe(macros=7, members_per_macro=1, helpers=0, plain=0,
                                       pending_roots=0, large_results=0, evidence_files=0,
                                       routed_roots=0, accepted_roots=0)
        with private_state_dir("macro-task-project-groups-") as root:
            with fixture_module.SyntheticBoard(root / "state", root / "runtime", SOURCE,
                                                recipe=recipe, seed=614) as fixture:
                with fixture.board.store.db.write() as connection:
                    for index, macro_id in enumerate(fixture.macros):
                        connection.execute("UPDATE macro_tasks SET project_id=?,project_path=? WHERE macro_task_id=?",
                                           ("standalone" if index == 0 else f"fictional-project-{index}",
                                            f"/fictional/project-{index}", macro_id))
                page = fixture.board.call("macro_task_list", {})
                self.assertEqual(len(page["groups"]), 7)
                self.assertEqual({group["id"] for group in page["groups"]},
                                 {"project:standalone", *(f"project:fictional-project-{index}" for index in range(1, 7))})
                self.assertTrue(all(group["total"] == 1 and len(group["macroTasks"]) == 1
                                    for group in page["groups"]))

    def test_all_groups_and_project_local_pages(self):
        recipe = fixture_module.Recipe(macros=7, members_per_macro=1, helpers=0, plain=0,
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
