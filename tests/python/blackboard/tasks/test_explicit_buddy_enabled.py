"""Explicit choices meet the user's enabled gate before workspace preparation."""
from unittest.mock import patch

from blackboard.tasks.test_workflow import BUDDY, WorkflowTestCase
from blackboard.tasks.test_workflow_real import RealWorkspaceTestCase
from hey_my_buddy.errors import BoardError
from support import enable_fixture_buddy


class ExplicitSubmitTests(RealWorkspaceTestCase):
    def test_disabled_submit_resolves_project_without_admitting_work(self):
        board = self.board()
        with board.store.db.write() as db:
            db.execute("UPDATE buddies SET enabled=0")
        tables = ("tasks", "macro_tasks", "workflow_runs", "workflow_artifacts", "workflow_turns",
                  "workflow_requests", "workspace_reservations", "events", "commands")
        with board.store.db.read() as db:
            before = {table: [tuple(row) for row in db.execute(f"SELECT * FROM {table}")] for table in tables}
        self.assertEqual(self.git('for-each-ref', 'refs/hey-my-buddy'), '')
        params = {"cwd": str(self.repo), "title": "disabled", "task": "do the work",
                  "macroTaskId": "standalone", "buddy": ":".join(BUDDY.values()),
                  "checkout": "worktree", "access": "write", "inputs": [], "writeScope": ["."],
                  "expectedSeconds": 1800}
        with patch.object(board.store.workflow, "_submission_preparation") as prepare:
            with self.assertRaises(BoardError) as rejected:
                board.call("workflow_submit", params)
        prepare.assert_not_called()
        self.assertEqual(rejected.exception.code, "BUDDY_UNAVAILABLE")
        self.assertEqual(rejected.exception.details["reason"], {"code": "not-enabled", "params": {}})
        self.assertIs(rejected.exception.details["enabled"], False)
        self.assertEqual(self.git('for-each-ref', '--format=%(refname)', 'refs/hey-my-buddy').splitlines(),
                         ['refs/hey-my-buddy/project'])
        self.assertFalse((board.directory / "workspaces").exists())
        with board.store.db.read() as db:
            after = {table: [tuple(row) for row in db.execute(f"SELECT * FROM {table}")] for table in tables}
        self.assertEqual(after, before)
        enable_fixture_buddy(board.store, BUDDY)
        admitted = board.call("workflow_submit", params)
        self.assertEqual(admitted["buddy"], BUDDY)
        self.assertTrue((board.directory / "workspaces").exists())


class ExplicitHelperTests(WorkflowTestCase):
    def test_disabled_second_helper_is_rejected_before_any_preparation(self):
        board = self.board()
        parent = self.submit(board)
        self.finish_turn(board, self.claim(board), disposition="assistance")
        view = board.call("workflow_get", {"runId": parent["runId"]})
        disabled = {**BUDDY, "effort": "high"}
        enable_fixture_buddy(board.store, disabled)
        with board.store.db.write() as db:
            db.execute("UPDATE buddies SET enabled=0 WHERE effort='high'")
        helpers = [{"title": f"helper-{index}", "task": "review", "cwd": str(self.workdir(f"helper-{index}")),
                    "buddy": ":".join(buddy.values()), "checkout": "worktree", "access": "read",
                    "inputs": [], "expectedSeconds": 1800}
                   for index, buddy in enumerate((BUDDY, disabled))]
        before_calls = list(self.workspace.prepare_calls)
        tables = ("tasks", "workflow_children", "workflow_requests", "events", "commands", "workflow_artifacts")
        with board.store.db.read() as db:
            before = {table: [tuple(row) for row in db.execute(f"SELECT * FROM {table}")] for table in tables}
        with self.assertRaises(BoardError) as rejected:
            board.call("workflow_continue", {
                "runId": parent["runId"], "requestId": view["activeRequest"]["requestId"],
                "decision": "approve", "helpers": helpers, "autoContinue": True,
            })
        self.assertEqual(rejected.exception.code, "BUDDY_UNAVAILABLE")
        self.assertIs(rejected.exception.details["enabled"], False)
        self.assertEqual(rejected.exception.details["reason"]["code"], "not-enabled")
        self.assertEqual(self.workspace.prepare_calls, before_calls)
        with board.store.db.read() as db:
            after = {table: [tuple(row) for row in db.execute(f"SELECT * FROM {table}")] for table in tables}
        self.assertEqual(after, before)

    def test_dispatch_revalidation_checks_enabled_choice(self):
        board = self.board()
        run = self.submit(board)
        with board.store.db.write() as db:
            db.execute("UPDATE buddies SET enabled=0")
            db.execute("UPDATE workflow_runs SET validated_buddy_revision=NULL WHERE run_id=?", (run["runId"],))
        before = list(self.workspace.prepare_calls)
        board.store.workflow.prepare_dispatch({"runId": run["runId"]})
        view = board.call("workflow_get", {"runId": run["runId"]})
        self.assertEqual(view["state"], "awaiting-host")
        cause = view["activeRequest"]["reason"]["params"]["cause"]
        self.assertEqual(cause["code"], "not-enabled")
        self.assertEqual(self.workspace.prepare_calls, before)
