"""Host identity, response binding and terminal facts in the current workflow."""
from __future__ import annotations

import re

from blackboard.tasks.test_workflow import BUDDY, WorkflowTestCase
from hey_my_buddy.cli.main import _creation_key
from hey_my_buddy.errors import BoardError
from support import enable_fixture_buddy


class HostWorkflowTests(WorkflowTestCase):
    def test_empty_macro_creation_replay_and_goal_history(self):
        board = self.board()
        cwd = str(self.workdir())
        project = self.workspace.ensure_project_id(self.workspace.repository_at(cwd, allow_parent=True))
        key = _creation_key(["adr032-create-v1", project, "Review plan"])
        packet = {"cwd": cwd, "title": "Review plan", "goal": "First goal", "creationKey": key}
        first = board.call("workflow_create", packet)
        self.assertEqual(first["actions"], ["amend", "submit", "get"])
        self.assertEqual(board.store.count_tasks(), 0)
        replay = board.call("workflow_create", {**packet, "goal": "Ignored on replay"})
        self.assertEqual(replay["macroTaskId"], first["macroTaskId"])
        self.assertTrue(replay["alreadyExists"])
        self.assertEqual(board.call("workflow_get", {"macroTaskId": first["macroTaskId"]})["goal"]["body"], "First goal")
        same = board.call("workflow_amend", {"macroTaskId": first["macroTaskId"], "goal": "First goal"})
        self.assertTrue(same["alreadyCurrent"])
        changed = board.call("workflow_amend", {"macroTaskId": first["macroTaskId"], "goal": "Revised goal"})
        self.assertFalse(changed["alreadyCurrent"])
        self.assertEqual(board.call("workflow_get", {"macroTaskId": first["macroTaskId"]})["goal"]["body"], "Revised goal")

    def test_submit_replay_preserves_original_authorization_and_buddy(self):
        board = self.board()
        first = self.submit(board, request_id="replay", access="read")
        original_count = len(self.workspace.prepare_calls)
        replay = self.submit(board, request_id="replay", access="write")
        self.assertEqual(replay["runId"], first["runId"])
        self.assertTrue(replay["alreadyExists"])
        self.assertEqual(replay["access"], "read")
        self.assertEqual(replay["buddy"], BUDDY)
        self.assertEqual(len(self.workspace.prepare_calls), original_count)
        self.assertEqual(board.store.count_tasks(), 1)

    def test_delivered_artifact_is_one_response_slot_and_old_output_remains(self):
        board = self.board()
        submitted = self.submit(board)
        self.finish_turn(board, self.claim(board))
        delivered = board.call("workflow_get", {"runId": submitted["runId"]})
        artifact = delivered["finalArtifactId"]
        self.assertIn("accept", delivered["actions"])
        continued = board.call("workflow_continue", {"runId": submitted["runId"], "artifactId": artifact,
                                                       "input": "Check another edge case"})
        self.assertEqual(continued["runId"], submitted["runId"])
        current = board.call("workflow_get", {"runId": submitted["runId"]})
        self.assertIsNone(current["finalArtifactId"])
        self.assertIn(artifact, [item["artifactId"] for item in current["artifacts"]])
        with self.assertRaises(BoardError) as raised:
            board.call("workflow_accept", {"runId": submitted["runId"], "artifactId": artifact,
                                           "note": "Cannot accept superseded output", "notRequired": "fixture"})
        self.assertEqual(raised.exception.code, "CONFLICT")

    def test_conclusion_replay_is_same_fact_and_changed_note_conflicts(self):
        board = self.board()
        submitted = self.submit(board)
        self.finish_turn(board, self.claim(board))
        delivered = board.call("workflow_get", {"runId": submitted["runId"]})
        packet = {"runId": submitted["runId"], "artifactId": delivered["finalArtifactId"],
                  "note": "The delivered work is intentionally closed without acceptance"}
        first = board.call("workflow_conclude", packet)
        self.assertEqual(first["state"], "concluded")
        replay = board.call("workflow_conclude", packet)
        self.assertEqual(replay["conclusionId"], first["conclusionId"])
        self.assertTrue(replay["alreadyResponded"])
        with self.assertRaises(BoardError) as raised:
            board.call("workflow_conclude", {**packet, "note": "Different outcome"})
        self.assertEqual(raised.exception.code, "CONFLICT")
        with self.assertRaises(BoardError) as raised:
            board.call("workflow_accept", {"runId": submitted["runId"], "artifactId": packet["artifactId"],
                                           "note": "Late acceptance", "notRequired": "fixture"})
        self.assertEqual(raised.exception.code, "CONFLICT")

    def test_cancel_is_run_bound_and_replay_keeps_first_stop_identity(self):
        board = self.board()
        submitted = self.submit(board)
        first = board.call("workflow_cancel", {"runId": submitted["runId"], "note": "Stop now"})
        replay = board.call("workflow_cancel", {"runId": submitted["runId"], "note": "Different later note"})
        self.assertEqual(replay["stopEventId"], first["stopEventId"])
        self.assertTrue(replay["alreadyRequested"])

    def test_worker_credential_cannot_write_and_invalid_credential_never_downgrades(self):
        board = self.board()
        submitted = self.submit(board)
        claim = self.claim(board)["claim"]
        with self.assertRaises(BoardError) as raised:
            board.call("workflow_cancel", {"runId": submitted["runId"], "credential": claim["agentCredential"]})
        self.assertEqual(raised.exception.code, "FORBIDDEN")
        with self.assertRaises(BoardError) as raised:
            board.call("workflow_cancel", {"runId": submitted["runId"], "credential": "invalid-attempt-token"})
        self.assertEqual(raised.exception.code, "UNAUTHORIZED")
        self.assertEqual(board.call("workflow_get", {"runId": submitted["runId"]})["state"], "executing")

    def test_named_buddy_must_be_enabled_and_complete_catalog_identity(self):
        board = self.board()
        replacement = {**BUDDY, "effort": "high"}
        enable_fixture_buddy(board.store, replacement)
        with board.store.db.write() as connection:
            connection.execute("UPDATE buddies SET enabled=0 WHERE effort='high'")
        with self.assertRaises(BoardError) as raised:
            self.submit(board, buddy=":".join(replacement.values()), request_id="disabled")
        self.assertEqual(raised.exception.code, "BUDDY_UNAVAILABLE")

    def test_catalog_sanitized_buddy_id_resolves_to_its_complete_tuple(self):
        board = self.board()
        identity = {**BUDDY, "model": "fixture/model"}
        raw = ":".join(identity.values())
        buddy_id = re.sub(r"[^A-Za-z0-9._:-]", "-", raw)
        with board.store.db.write() as connection:
            connection.execute("INSERT INTO buddies(buddy_id,label,harness,provider,model,effort,enabled,available)"
                               " VALUES(?,?,?,?,?,?,1,1)",
                               (buddy_id, "Sanitized fixture", *identity.values()))
        submitted = self.submit(board, buddy=buddy_id, request_id="sanitized")
        self.assertEqual(submitted["buddy"], identity)
        with self.assertRaises(BoardError) as raised:
            self.submit(board, buddy=raw, request_id="raw-name")
        self.assertEqual(raised.exception.code, "INVALID_ARGUMENT")
