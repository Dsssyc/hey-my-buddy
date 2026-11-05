"""Public workflow models reject invalid input before discovery or workspace work."""
from __future__ import annotations

import json
from unittest.mock import Mock

from blackboard.tasks.test_workflow import BUDDY, WorkflowTestCase
from hey_my_buddy.blackboard.service.service import BoardService
from support import enable_fixture_buddy


class WorkflowValidationTests(WorkflowTestCase):
    def service(self, board):
        service = BoardService(board.store, live_registry=board.live_registry, token="host", control={},
                               on_stop=lambda _: {}, on_restart=lambda _: {}, automatic_discovery=True)
        service.harnesses.get = Mock(return_value={"status": "unknown"})
        service.harnesses.refresh = Mock()
        service.harnesses.kick = Mock()
        self.addCleanup(service.harnesses.close)
        return service

    def packet(self):
        return {"cwd": str(self.workdir()), "macroTaskId": "standalone",
                "title": "Validate admission", "task": "Review fixture", "buddy": " / ".join(BUDDY.values()),
                "checkout": "existing", "access": "read", "inputs": [], "expectedSeconds": 600}

    def call(self, service, operation, params):
        return json.loads(getattr(service, operation)(json.dumps({"token": "host", **params})))

    def assert_no_effect(self, board, service):
        service.harnesses.get.assert_not_called()
        service.harnesses.refresh.assert_not_called()
        service.harnesses.kick.assert_not_called()
        self.assertEqual(self.workspace.prepare_calls, [])
        with board.store.db.read() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM tasks").fetchone()[0], 0)

    def test_invalid_submission_rejects_retired_fields_and_missing_authority_before_effects(self):
        board = self.board()
        service = self.service(board)
        packet = self.packet()
        invalid = ({"creationKey": "a" * 64}, {"hostId": "old-host"}, {"requestId": "old-request"},
                   {"executionWorkspace": {"kind": "existing"}}, {"buddyLocked": True},
                   {"access": None}, {"inputs": None}, {"expectedSeconds": 0})
        for change in invalid:
            with self.subTest(change=change):
                result = self.call(service, "workflow_submit", {**packet, **change})
                self.assertEqual(result["error"]["code"], "INVALID_ARGUMENT")
        self.assert_no_effect(board, service)

    def test_create_rejects_caller_key_and_old_host_ownership(self):
        board = self.board()
        service = self.service(board)
        for change in ({"creationKey": "bad"}, {"creationKey": "b" * 64}, {"hostId": "old-host"}):
            with self.subTest(change=change):
                result = self.call(service, "workflow_create", {"cwd": str(self.workdir()),
                                                                   "title": "Parent", "goal": "Do the work", **change})
                self.assertEqual(result["error"]["code"], "INVALID_ARGUMENT")
        self.assert_no_effect(board, service)

    def test_invalid_helper_packet_does_not_prepare_a_child(self):
        board = self.board()
        self.register(board)
        parent = self.submit(board)
        self.finish_turn(board, self.claim(board), disposition="assistance")
        view = board.call("workflow_get", {"runId": parent["runId"]})
        service = self.service(board)
        prepared = len(self.workspace.prepare_calls)
        helper = {"title": "Helper", "task": "Check fixture", "cwd": str(self.workdir("helper")),
                  "buddy": " / ".join(BUDDY.values()), "checkout": "worktree", "access": "read",
                  "inputs": [], "expectedSeconds": 600}
        result = self.call(service, "workflow_continue", {"runId": view["runId"],
                          "requestId": view["activeRequest"]["requestId"], "decision": "approve",
                          "helpers": [{**helper, "hostId": "forged"}], "autoContinue": False})
        self.assertEqual(result["error"]["code"], "INVALID_ARGUMENT")
        self.assertEqual(len(self.workspace.prepare_calls), prepared)
        with board.store.db.read() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM tasks").fetchone()[0], 1)

    def test_replayed_submit_returns_original_selection_without_workspace_preparation(self):
        board = self.board()
        enable_fixture_buddy(board.store, BUDDY)
        service = self.service(board)
        packet = self.packet()
        first = self.call(service, "workflow_submit", packet)
        self.assertNotIn("error", first)
        prepared = len(self.workspace.prepare_calls)
        replay = self.call(service, "workflow_submit", {**packet, "access": "write", "writeScope": ["."]})
        self.assertEqual(replay["runId"], first["runId"])
        self.assertTrue(replay["alreadyExists"])
        self.assertEqual(replay["access"], "read")
        self.assertEqual(replay["checkout"], "existing")
        self.assertEqual(len(self.workspace.prepare_calls), prepared)
