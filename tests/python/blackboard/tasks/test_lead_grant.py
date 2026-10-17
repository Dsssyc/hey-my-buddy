"""Host grants are micro-task facts, frozen into the ordinary Worker turn."""
from unittest.mock import patch

from blackboard.tasks.test_workflow import BUDDY, WorkflowTestCase
from support import enable_fixture_buddy
from hey_my_buddy.cli.cli_views import micro_task_brief
from hey_my_buddy.errors import BoardError
from hey_my_buddy.protocol import schemas


class LeadGrantTests(WorkflowTestCase):
    def params(self, **extra):
        return {**BUDDY, "requestId": "lead", "hostId": "host", "task": "Integrate the work",
                "submissionToken": "s" * 32,
                "cwd": str(self.workdir()), "delegationAuthority": True,
                "executionWorkspace": {"kind": "existing", "access": "full", "accessNote": "Lead checks"},
                **extra}

    def grant(self, board, **extra):
        enable_fixture_buddy(board.store, BUDDY)
        response = board.call("workflow_submit", self.params(**extra))
        self.controls[response["runId"]] = response["control"]
        return response

    def test_grant_requires_every_explicit_buddy_field_before_catalog_or_workspace_work(self):
        board = self.board()
        for absent in ((*schemas.BUDDY_FIELDS,), *((field,) for field in schemas.BUDDY_FIELDS)):
            for missing_value in ("omitted", None, ""):
                params = self.params()
                for field in absent:
                    if missing_value == "omitted":
                        params.pop(field)
                    else:
                        params[field] = missing_value
                with self.subTest(absent=absent, value=missing_value), self.assertRaises(BoardError) as caught:
                    board.call("workflow_submit", params)
                self.assertEqual(caught.exception.code, "INVALID_ARGUMENT")
                self.assertIn("delegationAuthority", caught.exception.message)
                self.assertIn("missing " + ", ".join(absent), caught.exception.message)
        self.catalog_validation.assert_not_called()
        self.assertEqual(board.store.count_tasks(), 0)

    def test_grant_requires_full_access_and_a_boolean(self):
        board = self.board()
        for access in (None, "read", "write"):
            workspace = {"kind": "existing"}
            if access is not None:
                workspace["access"] = access
            with self.subTest(access=access), self.assertRaises(BoardError) as caught:
                board.call("workflow_submit", self.params(executionWorkspace=workspace))
            self.assertIn("delegationAuthority requires executionWorkspace.access='full'", caught.exception.message)
        for value in (None, 0, 1, "true"):
            with self.subTest(value=value), self.assertRaises(BoardError) as caught:
                board.call("workflow_submit", self.params(delegationAuthority=value))
            self.assertIn("must be a boolean", caught.exception.message)
        self.catalog_validation.assert_not_called()

    def test_helpers_cannot_carry_grants_even_when_false(self):
        for value in (True, False):
            helper = self.params(delegationAuthority=value)
            helper.pop("hostId")
            with self.subTest(value=value), self.assertRaises(BoardError) as caught:
                schemas.normalize_helpers({"helpers": [helper]}, integrator={"role": "host", "id": "fixture-host"})
            self.assertIn("Only Host submissions", caught.exception.message)
            self.assertIn("helpers cannot carry it", caught.exception.message)

    def test_grant_requires_an_enabled_available_buddy(self):
        board = self.board()
        for enabled, available in ((0, 1), (1, 0)):
            enable_fixture_buddy(board.store, BUDDY)
            with board.store.db.write() as db:
                db.execute("UPDATE buddies SET enabled=?,available=?", (enabled, available))
            with self.subTest(enabled=enabled, available=available), self.assertRaises(BoardError) as caught:
                board.call("workflow_submit", self.params())
            self.assertEqual(caught.exception.code, "BUDDY_UNAVAILABLE")
            self.assertEqual(board.store.count_tasks(), 0)
        with patch("hey_my_buddy.blackboard.catalog.catalog.validate_buddy",
                   side_effect=BoardError("HARNESS_UNAVAILABLE", "fixture harness unavailable")):
            with self.assertRaises(BoardError) as caught:
                self.grant(board)
        self.assertEqual(caught.exception.code, "HARNESS_UNAVAILABLE")
        self.assertEqual(board.store.count_tasks(), 0)

    def test_grant_is_persisted_readable_and_frozen_with_lead_authority(self):
        from hey_my_buddy.json_codec import decode_strict_json
        board = self.board()
        submitted = self.grant(board)
        fetched = board.call("workflow_get", {"runId": submitted["runId"]})
        brief = micro_task_brief(fetched)
        listed = board.call("task_list", {})["runs"][0]
        for view in (submitted, fetched, brief, listed["lineage"]):
            self.assertTrue(view["delegationAuthority"])
            self.assertEqual(view["buddy"], BUDDY)
        self.register(board)
        claimed = self.claim(board)
        self.assertTrue(claimed["claim"]["turn"]["input"]["context"]["delegationAuthority"])
        self.assertEqual(claimed["claim"]["turn"]["input"]["executionWorkspace"]["access"], "full")
        with board.store.db.read() as db:
            self.assertEqual(db.execute("SELECT delegation_authority FROM workflow_runs").fetchone()[0], 1)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM workflow_routes").fetchone()[0], 0)
            row = db.execute(
                "SELECT payload_json,actor_role,actor_id FROM events WHERE kind='workflow.delegation_authority_granted'").fetchone()
            self.assertEqual(decode_strict_json(row["payload_json"]), {"buddy": BUDDY})
            self.assertEqual((row["actor_role"], row["actor_id"]), ("host", "host"))

    def test_absent_or_false_grant_has_one_identity_and_no_authority(self):
        board = self.board()
        ordinary = self.submit(board)
        replay = self.submit(board, delegationAuthority=False)
        self.assertTrue(replay["duplicate"])
        self.assertFalse(ordinary["delegationAuthority"])
        self.assertFalse(board.call("task_list", {})["runs"][0]["lineage"]["delegationAuthority"])
        self.register(board)
        claimed = self.claim(board)
        self.assertFalse(claimed["claim"]["turn"]["input"]["context"]["delegationAuthority"])

    def test_grant_is_part_of_submission_identity(self):
        board = self.board()
        submitted = self.grant(board)
        replay = self.grant(board)
        self.assertTrue(replay["duplicate"])
        self.assertEqual(submitted["control"], replay["control"])
        with self.assertRaises(BoardError) as caught:
            board.call("workflow_submit", self.params(delegationAuthority=False))
        self.assertEqual(caught.exception.code, "CONFLICT")

    def test_continuation_preserves_grant_and_requires_full_access_and_explicit_choice(self):
        board = self.board()
        submitted = self.grant(board)
        self.register(board)
        self.finish_turn(board, self.claim(board))
        current = board.call("workflow_get", {"runId": submitted["runId"]})
        for change in ({"access": "read"}, {"access": "write"}, {"reroute": True}):
            with self.subTest(change=change), self.assertRaises(BoardError) as caught:
                self.continue_run(board, current, **change)
            self.assertIn("delegationAuthority", caught.exception.message)
        replacement = {**BUDDY, "effort": "high"}
        continued = self.continue_run(board, current, buddy=replacement)
        self.assertTrue(continued["delegationAuthority"])
        self.assertEqual(continued["buddy"], replacement)
        claimed = self.claim(board, claim_request_id="next")
        self.assertTrue(claimed["claim"]["turn"]["input"]["context"]["delegationAuthority"])
        self.assertEqual(claimed["claim"]["turn"]["input"]["context"]["buddy"], replacement)
