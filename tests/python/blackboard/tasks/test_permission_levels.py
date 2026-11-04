"""Explicit permission choices remain bound to each turn and real Git input."""
from __future__ import annotations

from unittest.mock import patch

from blackboard.tasks import workspace_lifecycle_case as lifecycle_fixtures
from blackboard.tasks.test_workflow import BUDDY, WorkflowTestCase
from blackboard.tasks.test_workflow_real import RealWorkspaceTestCase
from hey_my_buddy.blackboard.tasks import workspace as git_workspace
from hey_my_buddy.cli.main import _creation_key
from hey_my_buddy.errors import BoardError
from hey_my_buddy.protocol.capabilities import PERMISSION_LEVELS
from hey_my_buddy.protocol.requests import parse_request
from hey_my_buddy.protocol.workspace_manifest import turn_manifest
from support import enable_fixture_buddy


class PermissionLevelsTests(WorkflowTestCase):
    def test_missing_harness_permission_refuses_submit_and_next_turn(self):
        self.enterContext(patch.dict(PERMISSION_LEVELS, {"dsh": {
            level: declaration for level, declaration in PERMISSION_LEVELS["dsh"].items() if level != "full"}}))
        board = self.board()
        with self.assertRaises(BoardError) as rejected:
            self.submit(board, access="full")
        self.assertEqual(rejected.exception.code, "UNSUPPORTED_CAPABILITY")
        self.assertEqual(board.store.count_tasks(), 0)
        submitted = self.submit(board, access="read")
        self.finish_turn(board, self.claim(board))
        delivered = board.call("workflow_get", {"runId": submitted["runId"]})
        with self.assertRaises(BoardError) as rejected:
            board.call("workflow_continue", {"runId": submitted["runId"],
                                             "artifactId": delivered["finalArtifactId"],
                                             "input": "Continue with more access", "access": "full",
                                             "accessNote": "Need system writes", "writeScope": ["."]})
        self.assertEqual(rejected.exception.code, "UNSUPPORTED_CAPABILITY")
        self.assertEqual(board.call("workflow_get", {"runId": submitted["runId"]})["access"], "read")

    def test_permission_change_freezes_new_turn_without_rewriting_old_turn(self):
        board = self.board()
        submitted = self.submit(board, access="read")
        first_claim = self.claim(board)
        first_manifest = turn_manifest(self.directory, first_claim["claim"]["turn"]["input"])
        self.assertEqual(first_manifest["access"], "read")
        self.finish_turn(board, first_claim)
        delivered = board.call("workflow_get", {"runId": submitted["runId"]})
        board.call("workflow_continue", {"runId": submitted["runId"],
                                         "artifactId": delivered["finalArtifactId"],
                                         "input": "Finish with full access", "access": "full",
                                         "accessNote": "Need system writes", "writeScope": ["."]})
        second_claim = self.claim(board, claim_request_id="second")
        second_manifest = turn_manifest(self.directory, second_claim["claim"]["turn"]["input"])
        self.assertEqual(second_manifest["access"], "full")
        self.assertEqual(first_manifest["access"], "read")
        with board.store.db.read() as connection:
            rows = connection.execute("SELECT access,access_note FROM workflow_turns WHERE run_id=? ORDER BY turn_index",
                                      (submitted["runId"],)).fetchall()
        self.assertEqual([tuple(row) for row in rows], [("read", None), ("full", "Need system writes")])

    def test_invalid_access_combinations_stop_at_the_model_boundary(self):
        base = {"runId": "run-1", "artifactId": "art-1", "input": "More work"}
        for change in ({"access": "unknown"}, {"access": "read", "writeScope": ["."]},
                       {"access": "full"}, {"accessNote": "Reason"},
                       {"access": "full", "accessNote": "x" * 10001}):
            with self.subTest(change=change), self.assertRaises(BoardError) as rejected:
                parse_request("continue", {**base, **change})
            self.assertEqual(rejected.exception.code, "INVALID_ARGUMENT")


class GitPermissionLevelsTests(RealWorkspaceTestCase):
    register = lifecycle_fixtures.LifecycleTestCase.register
    claim = lifecycle_fixtures.LifecycleTestCase.claim
    turn_record = lifecycle_fixtures.LifecycleTestCase.turn_record
    finish_turn = lifecycle_fixtures.LifecycleTestCase.finish_turn

    def setUp(self):
        super().setUp()
        self.worker = "w-life"

    def test_explicit_untracked_input_is_frozen_under_read_access(self):
        (self.repo / "input.txt").write_text("Explicit input\n", encoding="utf-8", newline="\n")
        board = self.board()
        enable_fixture_buddy(board.store, BUDDY)
        project = git_workspace.ensure_project_id(git_workspace.repository_at(str(self.repo), allow_parent=True))
        task = "Read the explicit input"
        title = "Read input"
        submitted = board.call("workflow_submit", {
            "creationKey": _creation_key(["adr032-submit-v1", "project:" + project, title, task]),
            "cwd": str(self.repo), "macroTaskId": "standalone", "title": title, "task": task,
            "buddy": ":".join(BUDDY.values()), "checkout": "existing", "access": "read",
            "inputs": ["input.txt"], "expectedSeconds": 600,
        })
        claim = self.claim(board, submitted["runId"])
        manifest = turn_manifest(self.directory, claim["claim"]["turn"]["input"])
        self.assertEqual(git_workspace.execution_selection(manifest),
                         {"kind": "selectors", "selectors": ["input.txt"]})
        self.assertEqual(manifest["access"], "read")
        git_workspace.verify(manifest, require_unchanged=True, state_dir=self.directory)
