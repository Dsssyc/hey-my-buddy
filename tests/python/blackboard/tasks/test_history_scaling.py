"""VM work, independent of machine load, for board-wide history consumers."""
from __future__ import annotations

import unittest

from support import private_state_dir
from blackboard.tasks.test_console_macro_task_fixture import fixture_module, SOURCE
from hey_my_buddy.blackboard.tasks import macro_tasks, macro_task_summary_cache as memo


class HistoryScalingTests(unittest.TestCase):
    def test_cold_list_and_timeline_steps_grow_less_than_quadratically(self):
        # Both boards fit the default 50-group page. Double every relationship,
        # including helpers and Routers; timeline displays the same four-run group.
        # 2.5 leaves room for sorting/index construction, but excludes a 4x scan.
        measurements = []
        with private_state_dir("history-scaling-") as root:
            for size in (20, 40):
                recipe = fixture_module.Recipe(
                    macros=size, members_per_macro=4, helpers=size, plain=size,
                    pending_roots=size * 3, routed_roots=size, accepted_roots=0,
                    large_results=0, evidence_files=0,
                )
                with fixture_module.SyntheticBoard(root / f"state-{size}", root / f"runtime-{size}",
                                                   SOURCE, recipe=recipe, seed=613) as fixture:
                    store = fixture.board.store
                    store._macro_task_summary_cache = memo.SummaryCache()
                    steps = []
                    for index, read in enumerate((lambda: macro_tasks.macro_task_list(store, {}),
                                 lambda: macro_tasks.macro_task_timeline(
                                     store, {"macroTaskId": fixture.macros[0]}))):
                        ticks = 0

                        def progress():
                            nonlocal ticks
                            ticks += 1
                            return 0

                        with store.db.read() as connection:
                            connection.set_progress_handler(progress, 100)
                        try:
                            result = read()
                        finally:
                            with store.db.read() as connection:
                                connection.set_progress_handler(None, 0)
                        self.assertEqual(len(result["macroTasks"] if index == 0 else result["rows"]),
                                         size if index == 0 else 4)
                        steps.append(ticks * 100)
                    measurements.append(steps)
        for index, name in enumerate(("cold list", "timeline")):
            small, large = (row[index] for row in measurements)
            with self.subTest(read=name, steps=(small, large)):
                self.assertLess(large, small * 2.5, f"{name}: {small} -> {large} VM steps")


if __name__ == "__main__":
    unittest.main()
