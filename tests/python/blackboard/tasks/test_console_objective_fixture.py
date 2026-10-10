"""Structural smoke for the fictional board; no timing assertions or models."""
from __future__ import annotations

import importlib.util
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from support import private_state_dir

# fixtures/ is an executable helper directory, not a new test package.
HELPER = Path(__file__).resolve().parents[2] / "fixtures/console_objective_board.py"
SPEC = importlib.util.spec_from_file_location("console_objective_board_fixture", HELPER)
fixture_module = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = fixture_module
SPEC.loader.exec_module(fixture_module)
SOURCE = Path(__file__).resolve().parents[4]


class ConsoleObjectiveFixtureTests(unittest.TestCase):
    def test_smoke_has_real_relationships_and_counts(self):
        with private_state_dir("console-objective-fixture-") as root:
            with fixture_module.SyntheticBoard(root / "state", root / "runtime", SOURCE,
                                                recipe=fixture_module.Recipe.smoke(), seed=613) as fixture:
                facts = fixture.initial_facts
                self.assertEqual(facts["schemaVersion"], 15)
                self.assertEqual(facts["governedRuns"], 12)
                self.assertEqual(facts["counts"]["objectives"], 3)
                self.assertEqual(facts["plainRecords"], 2)
                self.assertEqual(facts["pendingRoots"], 4)
                self.assertEqual(facts["counts"]["workflow_children"], 1)
                self.assertEqual(facts["counts"]["workflow_routes"], 1)
                self.assertEqual(facts["counts"]["decision_requests"], 1)
                self.assertEqual(facts["counts"]["attempts"], 13)
                self.assertEqual(facts["counts"]["workflow_turns"], 12)
                self.assertEqual(facts["largeResultBytes"], [8192, 8192])
                self.assertEqual(facts["declaredEvidenceFiles"], 40)
                self.assertLess(facts["databaseBytes"], 3_000_000)
                self.assertLess(facts["maxDisplaySummaryCharacters"], 200)
                self.assertEqual(facts["foreignKeyViolations"], [])
                self.assertEqual(facts["integrityCheck"], "ok")
                self.assertEqual(facts["subprocessLaunchAttempts"], 0)
                self.assertEqual(facts["runtimeEntries"], [])
                self.assertEqual(facts["workflowStates"].get("accepted"), 1)
                self.assertIn("workflow.request_approved", facts["eventKinds"])
                self.assertIn("workflow.turn_concluded", facts["eventKinds"])
                with fixture.board.store.db.read() as connection:
                    # Not just counts: selected receipt, turn and task are bound.
                    bound = connection.execute(
                        "SELECT COUNT(*) FROM workflow_turns t JOIN attempts a ON a.attempt_id=t.attempt_id"
                        " JOIN tasks task ON task.task_id=t.run_id"
                        " WHERE a.task_id=t.run_id AND task.selected_attempt_id=a.attempt_id"
                        " AND a.shutdown_confirmed=1 AND a.execution_state='finished'").fetchone()[0]
                    self.assertEqual(bound, 12)
                    child = connection.execute(
                        "SELECT parent.objective_id,child.objective_id FROM workflow_children link"
                        " JOIN workflow_runs parent ON parent.run_id=link.parent_run_id"
                        " JOIN workflow_runs child ON child.run_id=link.child_task_id").fetchone()
                    self.assertEqual(child[0], child[1])
                    linked = connection.execute(
                        "SELECT COUNT(*) FROM workflow_routes r JOIN decision_requests d USING(decision_id)"
                        " JOIN tasks task ON task.task_id=d.task_id JOIN attempts a ON a.task_id=task.task_id"
                        " WHERE a.shutdown_confirmed=1 AND a.execution_state='finished'").fetchone()[0]
                    self.assertEqual(linked, 1)
                self.assertEqual(fixture.launch_guard.call_count, 0)
            # The helper's lifecycle deliberately retains the SQL and evidence.
            self.assertTrue((root / "state/board.sqlite3").exists())

    def test_smoke_response_is_readable_over_real_console_http(self):
        with private_state_dir("console-objective-http-") as root:
            with fixture_module.SyntheticBoard(root / "state", root / "runtime", SOURCE,
                                                recipe=fixture_module.Recipe.smoke(), seed=613) as fixture:
                with fixture.board.store.db.read() as connection:
                    head = connection.execute("SELECT MAX(seq) FROM events").fetchone()[0]
                with fixture_module.HandlerProbe(fixture.board) as probe:
                    first, body = probe.request("identity")
                    self.assertEqual(first["status"], 200)
                    self.assertEqual(body["total"], 3)
                    self.assertEqual(len(body["objectives"]), 3)
                    self.assertTrue(first["etag"])
                    self.assertEqual(first["summaryCalls"], 3)
                    second, replay = probe.request("identity", first["etag"])
                    self.assertEqual(second["status"], 304)
                    self.assertIsNone(replay)
                    self.assertEqual(second["rawBodyBytes"], 0)
                    self.assertEqual(second["summaryCalls"], 0)
                    compressed, decoded = probe.request("gzip")
                    self.assertEqual(compressed["status"], 200)
                    self.assertEqual(compressed["contentEncoding"], "gzip")
                    self.assertEqual(compressed["rawSha256"], first["rawSha256"])
                    self.assertEqual(decoded, body)
                    self.assertLess(compressed["gzipBodyBytes"], compressed["rawBodyBytes"])
                    self.assertNotEqual(compressed["etag"], first["etag"])
                with fixture.board.store.db.read() as connection:
                    self.assertEqual(connection.execute("SELECT MAX(seq) FROM events").fetchone()[0], head)
                self.assertEqual(fixture.launch_guard.call_count, 0)

    def test_fresh_roots_are_independent_and_nonempty_reuse_is_refused(self):
        with private_state_dir("console-objective-isolation-") as root:
            with fixture_module.SyntheticBoard(root / "first-state", root / "first-runtime", SOURCE,
                                                recipe=fixture_module.Recipe.smoke(), seed=613) as first:
                first_ids = list(first.macros)
                initial = first.initial_facts
                with first.board.store.db.write() as connection:
                    connection.execute("INSERT INTO meta(key,value) VALUES('first-only','fictional')")
            with self.assertRaisesRegex(ValueError, "nonempty"):
                fixture_module.SyntheticBoard(root / "first-state", root / "fresh-runtime", SOURCE)
            with fixture_module.SyntheticBoard(root / "second-state", root / "second-runtime", SOURCE,
                                                recipe=fixture_module.Recipe.smoke(), seed=613) as second:
                self.assertEqual(first_ids, second.macros)
                self.assertEqual(initial["counts"], second.initial_facts["counts"])
                with second.board.store.db.read() as connection:
                    self.assertIsNone(connection.execute("SELECT value FROM meta WHERE key='first-only'").fetchone())
                self.assertEqual(second.launch_guard.call_count, 0)

    def test_inherited_authority_is_cleared_before_setting_private_paths(self):
        environment = {"BUDDY_UNKNOWN_FUTURE_CREDENTIAL": "fictional", "ANTHROPIC_API_KEY": "fictional",
                       "VIRTUAL_ENV": "fictional", "UV_PROJECT_ENVIRONMENT": "fictional", "PATH": "preserved"}
        self.assertEqual(fixture_module.clean_environment(environment), {"PATH": "preserved"})
        with private_state_dir("console-objective-environment-") as root:
            with patch.dict(os.environ, environment):
                with fixture_module.private_environment(root / "state", root / "runtime", SOURCE):
                    self.assertNotIn("BUDDY_UNKNOWN_FUTURE_CREDENTIAL", os.environ)
                    self.assertNotIn("ANTHROPIC_API_KEY", os.environ)
                    self.assertNotIn("VIRTUAL_ENV", os.environ)
                    self.assertNotIn("UV_PROJECT_ENVIRONMENT", os.environ)
                    self.assertEqual(os.environ["BUDDY_STATE_DIR"], str(root / "state"))
                    self.assertEqual(os.environ["BUDDY_RUNTIME_ROOT"], str(root / "runtime"))
                self.assertEqual(os.environ["BUDDY_UNKNOWN_FUTURE_CREDENTIAL"], "fictional")


if __name__ == "__main__":
    unittest.main()
