"""Explicit Lead grants stay frozen while submission keys identify created work."""
from unittest.mock import patch

from blackboard.tasks.test_workflow import BUDDY, WorkflowTestCase
from support import enable_fixture_buddy
from hey_my_buddy.errors import BoardError
from hey_my_buddy.protocol.requests import parse_request
from hey_my_buddy.protocol.workspace_manifest import turn_manifest


class LeadGrantTests(WorkflowTestCase):
    def params(self, **extra):
        cwd = str(self.workdir())
        return {"title": "Lead", "task": "Integrate the work", "cwd": cwd,
                "buddy": ":".join(BUDDY.values()), "macroTaskId": "standalone", "checkout": "existing",
                "inputs": [], "access": "full", "accessNote": "Lead checks", "writeScope": ["."],
                "expectedSeconds": 1800, "delegationAuthority": True, **extra}

    def grant(self, board, **extra):
        enable_fixture_buddy(board.store, BUDDY)
        response = board.call("workflow_submit", self.params(**extra))
        return {**board.call("workflow_get", {"runId": response["runId"]}), **response}

    def test_grant_requires_an_explicit_named_buddy_before_catalog_or_workspace(self):
        board = self.board()
        for value in (None, "", "route"):
            params = self.params(buddy=value)
            with self.subTest(value=value), self.assertRaises(BoardError) as caught:
                board.call("workflow_submit", params)
            self.assertEqual(caught.exception.code, "INVALID_ARGUMENT")
        params = self.params()
        del params["buddy"]
        with self.assertRaises(BoardError):
            board.call("workflow_submit", params)
        self.catalog_validation.assert_not_called()
        self.assertEqual(self.workspace.prepare_calls, [])
        self.assertEqual(board.store.count_tasks(), 0)

    def test_grant_requires_full_access_and_only_explicit_true(self):
        board = self.board()
        for value in (None, False, 0, 1, "true"):
            with self.subTest(value=value), self.assertRaises(BoardError):
                board.call("workflow_submit", self.params(delegationAuthority=value))
        for access in ("read", "write"):
            params = self.params(access=access)
            del params["accessNote"]
            if access == "read":
                del params["writeScope"]
            with self.subTest(access=access), self.assertRaises(BoardError):
                board.call("workflow_submit", params)
        self.catalog_validation.assert_not_called()

    def test_helpers_cannot_carry_grants_even_when_false(self):
        packet = self.params()
        del packet["macroTaskId"]
        for value in (True, False):
            packet["delegationAuthority"] = value
            with self.subTest(value=value), self.assertRaises(BoardError) as caught:
                parse_request("continue", {"runId": "parent", "requestId": "request", "decision": "approve",
                    "helpers": [packet], "autoContinue": True})
            self.assertIn("delegationAuthority", caught.exception.message)

    def test_grant_requires_an_enabled_available_buddy(self):
        board = self.board()
        enable_fixture_buddy(board.store, BUDDY)
        with board.store.db.write() as connection:
            connection.execute("UPDATE buddies SET enabled=0")
        with self.assertRaises(BoardError) as caught:
            board.call("workflow_submit", self.params())
        self.assertEqual(caught.exception.code, "BUDDY_UNAVAILABLE")
        self.assertEqual(board.store.count_tasks(), 0)
        enable_fixture_buddy(board.store, BUDDY)
        for code in ("BUDDY_UNAVAILABLE", "HARNESS_UNAVAILABLE"):
            with patch("hey_my_buddy.blackboard.catalog.catalog.validate_buddy", side_effect=BoardError(code, "unavailable")):
                with self.assertRaises(BoardError) as caught:
                    board.call("workflow_submit", self.params())
                self.assertEqual(caught.exception.code, code)
        self.assertEqual(board.store.count_tasks(), 0)

    def test_grant_is_persisted_readable_and_frozen_with_lead_authority(self):
        board = self.board()
        submitted = self.grant(board)
        self.assertTrue(submitted["delegationAuthority"])
        self.assertEqual(submitted["buddy"], BUDDY)
        claim = self.claim(board)["claim"]
        self.assertTrue(claim["turn"]["input"]["context"]["delegationAuthority"])
        self.assertEqual(turn_manifest(self.directory, claim["turn"]["input"])["access"], "full")
        with board.store.db.read() as connection:
            event = connection.execute("SELECT actor_role,actor_id FROM events WHERE kind='workflow.delegation_authority_granted'").fetchone()
            self.assertEqual(tuple(event), ("host", None))
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM workflow_routes").fetchone()[0], 0)

    def test_ordinary_work_has_no_grant_and_explicit_false_is_rejected(self):
        board = self.board()
        ordinary = self.submit(board)
        self.assertFalse(ordinary["delegationAuthority"])
        with self.assertRaises(BoardError):
            self.submit(board, delegationAuthority=False)
        claim = self.claim(board)["claim"]
        self.assertFalse(claim["turn"]["input"]["context"]["delegationAuthority"])

    def test_same_key_preserves_the_first_grant_and_access(self):
        board = self.board()
        submitted = self.grant(board)
        params = self.params()
        del params["delegationAuthority"]
        params.update(access="read")
        del params["writeScope"]
        del params["accessNote"]
        replay = board.call("workflow_submit", params)
        self.assertEqual(replay["runId"], submitted["runId"])
        self.assertTrue(replay["alreadyExists"])
        self.assertEqual(replay["access"], "full")
        self.assertTrue(board.call("workflow_get", {"runId": submitted["runId"]})["delegationAuthority"])

    def test_continuation_preserves_grant_and_requires_full_explicit_choice(self):
        board = self.board()
        submitted = self.grant(board)
        self.finish_turn(board, self.claim(board))
        current = board.call("workflow_get", {"runId": submitted["runId"]})
        for change in ({"access": "read"}, {"access": "write"}, {"buddy": "route"}):
            with self.subTest(change=change), self.assertRaises(BoardError) as caught:
                self.continue_run(board, current, **change)
            self.assertEqual(caught.exception.code, "INVALID_ARGUMENT")
        replacement = {**BUDDY, "effort": "high"}
        continued = self.continue_run(board, current, buddy=replacement)
        self.assertTrue(continued["delegationAuthority"])
        self.assertEqual(continued["buddy"], replacement)
        claimed = self.claim(board, claim_request_id="next")["claim"]
        self.assertTrue(claimed["turn"]["input"]["context"]["delegationAuthority"])
        self.assertEqual(claimed["turn"]["input"]["context"]["buddy"], replacement)
