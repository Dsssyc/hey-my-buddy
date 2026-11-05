"""Host identity, response binding and terminal facts in the current workflow."""
from __future__ import annotations

import re
from unittest.mock import patch

from blackboard.tasks.test_workflow import BUDDY, WorkflowTestCase
from hey_my_buddy.errors import BoardError
from support import FakeClock, enable_fixture_buddy


class HostWorkflowTests(WorkflowTestCase):
    def test_empty_macro_creation_replay_and_goal_history(self):
        board = self.board()
        cwd = str(self.workdir())
        packet = {"cwd": cwd, "title": "Review plan", "goal": "First goal"}
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
            self.submit(board, buddy=" / ".join(replacement.values()), request_id="disabled")
        self.assertEqual(raised.exception.code, "BUDDY_UNAVAILABLE")

    def test_buddy_name_is_the_console_quad_and_never_the_catalog_primary_key(self):
        board = self.board()
        identity = {**BUDDY, "model": "fixture/model"}
        buddy_id = re.sub(r"[^A-Za-z0-9._:-]", "-", ":".join(identity.values()))
        with board.store.db.write() as connection:
            connection.execute("INSERT INTO buddies(buddy_id,label,harness,provider,model,effort,enabled,available)"
                               " VALUES(?,?,?,?,?,?,1,1)",
                               (buddy_id, "Sanitized fixture", *identity.values()))
        submitted = self.submit(board, buddy=" / ".join(identity.values()), request_id="quad-name")
        self.assertEqual(submitted["buddy"], identity)
        for name in (buddy_id, ":".join(identity.values()), "dsh / deepseek-official / absent-model / off"):
            with self.subTest(name=name), self.assertRaises(BoardError) as raised:
                self.submit(board, buddy=name, request_id="refused")
            self.assertEqual(raised.exception.code, "INVALID_ARGUMENT")
            self.assertIn("harness / provider / model / effort", raised.exception.message)
            self.assertIn("buddies", raised.exception.message)


class GetBuddiesTests(WorkflowTestCase):
    """`get` with its buddies selector reads the enabled buddies by name."""

    ENABLED = ("dsh:deepseek-official:deepseek-flash:high",
               "dsh:deepseek-official:deepseek-flash:off",
               "dsh:deepseek-official:deepseek-v4-pro:high")

    def setUp(self):
        super().setUp()
        self.catalog_fixture()
        self.clock = FakeClock("2026-01-01T00:00:00.000Z")
        board = self.board(clock=self.clock)
        board.evaluation._clock = self.clock
        board.call("model_catalog_refresh", {"requestId": "catalog"})
        board.console_call("user_policy_publish", {"commandId": "enable",
            "buddySettings": [{"buddyId": item, "enabled": True} for item in self.ENABLED],
            "configuration": {"defaultRoutingMode": "review", "routerBuddyIds": []}})
        self.enabled_board = board

    def names(self, reply):
        return [entry["name"] for entry in reply["buddies"]]

    def test_lists_only_enabled_buddies_by_complete_name_ready_for_work(self):
        reply = self.enabled_board.call("workflow_get", {"buddies": True})
        self.assertEqual(self.names(reply), [
            "dsh / deepseek-official / deepseek-flash / high",
            "dsh / deepseek-official / deepseek-flash / off",
            "dsh / deepseek-official / deepseek-v4-pro / high",
        ])
        for entry in reply["buddies"]:
            self.assertEqual(set(entry), {"name", "available"})
            self.assertTrue(entry["available"])
        self.assertIsNone(reply["nextCursor"])

    def test_pages_by_limit_and_before_and_refuses_a_cursor_that_names_no_buddy(self):
        first = self.enabled_board.call("workflow_get", {"buddies": True, "limit": 2})
        self.assertEqual(self.names(first), [
            "dsh / deepseek-official / deepseek-flash / high",
            "dsh / deepseek-official / deepseek-flash / off",
        ])
        self.assertEqual(first["nextCursor"], "dsh:deepseek-official:deepseek-flash:off")
        second = self.enabled_board.call("workflow_get", {"buddies": True, "limit": 2,
                                                          "before": first["nextCursor"]})
        self.assertEqual(self.names(second), ["dsh / deepseek-official / deepseek-v4-pro / high"])
        self.assertIsNone(second["nextCursor"])
        with self.assertRaises(BoardError) as raised:
            self.enabled_board.call("workflow_get", {"buddies": True, "before": "dsh:absent:model:off"})
        self.assertEqual(raised.exception.code, "INVALID_ARGUMENT")
        self.assertIn("before is not a recorded buddy", raised.exception.message)

    def test_a_buddy_that_cannot_take_work_carries_its_recorded_reasons(self):
        from hey_my_buddy.blackboard.evaluation.native_observations import persist
        with self.enabled_board.store.db.write() as connection:
            connection.execute("UPDATE buddies SET available=0, unavailable_code='catalog.model-absent',"
                               " unavailable_params_json='{}'"
                               " WHERE buddy_id='dsh:deepseek-official:deepseek-v4-pro:high'")
            persist(connection, {"attempt_id": "synthetic-denial", "adapter": "dsh", "model_harness": "dsh",
                                 "model_provider": "deepseek-official", "started_at": self.clock()},
                    {"providerRejection": {"code": "quota-exceeded", "nativeCode": "1310",
                                           "source": "synthetic/dsh-denial", "observedAt": self.clock(),
                                           "resetsAt": None, "rejectionPhase": "before-model"}},
                    status="failed", shutdown_confirmed=True, now=self.clock())
        with patch("hey_my_buddy.blackboard.routing.quota_routing.utc_now", self.clock):
            reply = self.enabled_board.call("workflow_get", {"buddies": True})
        by_name = {entry["name"]: entry for entry in reply["buddies"]}
        self.assertEqual(by_name["dsh / deepseek-official / deepseek-v4-pro / high"],
                         {"name": "dsh / deepseek-official / deepseek-v4-pro / high", "available": False,
                          "coolingUntil": "2026-01-01T00:30:00.000Z",
                          "unavailableReason": {"code": "catalog.model-absent", "params": {}}})
        for name in ("dsh / deepseek-official / deepseek-flash / high",
                     "dsh / deepseek-official / deepseek-flash / off"):
            self.assertEqual(by_name[name],
                             {"name": name, "available": False, "coolingUntil": "2026-01-01T00:30:00.000Z"})
