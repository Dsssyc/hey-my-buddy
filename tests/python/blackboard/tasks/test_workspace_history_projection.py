"""Historical inline workspace views and current file references."""
from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from blackboard.store.upgrade_fixture import load_fixture
from hey_my_buddy.blackboard.store import schema_30_31, schema_31_32, schema_32_33, upgrade
from hey_my_buddy.blackboard.store.store import BoardStore
from hey_my_buddy.errors import BoardError


class WorkspaceHistoryProjectionTests(unittest.TestCase):
    def test_inline_history_omits_manifest_file_whether_the_old_file_exists_or_not(self):
        with tempfile.TemporaryDirectory(prefix="history-inline-projection-") as scratch:
            fixture = load_fixture(Path(scratch) / "fixture")
            state = fixture["state"]
            with sqlite3.connect(state / "board.sqlite3") as db:
                db.row_factory = sqlite3.Row
                db.execute("PRAGMA foreign_keys=OFF")
                with db:
                    schema_30_31.upgrade(db)
                    schema_31_32.upgrade(db)
                    schema_32_33.upgrade(db)
                historical = [row for row in db.execute(
                    "SELECT * FROM workflow_runs WHERE state IN ('accepted','concluded')")
                    if json.loads(row["workspace_manifest_json"])["kind"] == "existing"]
                self.assertTrue(historical)
                old_manifests = {
                    row["run_id"]: json.loads(row["workspace_manifest_json"]) for row in historical
                }
                old_files = {
                    run_id: state / "workspaces" / manifest["workspaceId"] / "manifest.json"
                    for run_id, manifest in old_manifests.items()
                }
                run_id = next(run for run, path in old_files.items() if path.exists())
                old_file = old_files[run_id]
            upgrade.upgrade_state(state)
            store = BoardStore(state)
            store.initialize()
            self.addCleanup(store.db.close)

            def historical_view(selected_run):
                with store.db.read() as connection:
                    row = connection.execute("SELECT * FROM workflow_runs WHERE run_id=?", (selected_run,)).fetchone()
                    return store.workflow._workspace_view(row, connection=connection)

            for selected_run in old_manifests:
                with self.subTest(run_id=selected_run):
                    present = historical_view(selected_run)
                    self.assertNotIn("manifestFile", present)
            present = historical_view(run_id)
            old_file.unlink()
            self.assertEqual(historical_view(run_id), present)

    def test_current_reference_advertises_its_file_and_requires_it(self):
        from blackboard.tasks.test_workflow import WorkflowTestCase

        case = WorkflowTestCase("runTest")
        case.setUp()
        try:
            board = case.board()
            submitted = case.submit(board, request_id="reference-projection")
            view = board.call("workflow_get", {"runId": submitted["runId"]})
            reference = view["workspace"]["manifestFile"]
            path = Path(reference["path"])
            self.assertTrue(path.exists())
            path.unlink()
            with self.assertRaises(BoardError) as raised:
                board.call("workflow_get", {"runId": submitted["runId"]})
            self.assertEqual(raised.exception.code, "WORKSPACE_MANIFEST_CHANGED")
        finally:
            case.doCleanups()
