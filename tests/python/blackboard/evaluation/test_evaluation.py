"""Published profiles, user settings and catalog discovery.

These tests exercise the production store and the production resource
implementations in-process against private state directories; only the C-Two
transport is skipped.
"""
from __future__ import annotations

import json
import unittest

from support import BoardTestCase

from hey_my_buddy.errors import BoardError

PROFILE_ID = "dsh:deepseek-official:deepseek-flash:off"
PROFILE = {
    "profileId": PROFILE_ID,
    "label": "DeepSeek-V41-Flash · off",
    "adapter": "dsh",
    "provider": "deepseek-official",
    "model": "deepseek-flash",
    "effort": "off",
    "available": True,
    "enabled": True,
    "capabilities": ["execution:dsh", "effort:off"],
    "contextWindow": 1000000,
    "description": "fixture profile",
    "source": "manual",
}
SECOND_PROFILE_ID = "dsh:deepseek-official:deepseek-v4-pro:high"
SECOND_PROFILE = {
    **PROFILE,
    "profileId": SECOND_PROFILE_ID,
    "label": "DeepSeek-V4-Pro · high",
    "model": "deepseek-v4-pro",
    "effort": "high",
}


def model_service_key(profile: dict) -> dict:
    """The ``(adapter, provider, model)`` key a model-service-keyed row or patch uses."""
    return {key: profile[key] for key in ("adapter", "provider", "model")}


class EvaluationTestCase(BoardTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.catalog_fixture()

    # -- helpers -------------------------------------------------------------
    def assert_code(self, code: str, callable_, *args, **kwargs) -> BoardError:
        with self.assertRaises(BoardError) as caught:
            callable_(*args, **kwargs)
        self.assertEqual(caught.exception.code, code, caught.exception.message)
        return caught.exception

    def snapshot(self, board) -> dict:
        return board.call("console_snapshot", {})

    def seed_catalog(self, board) -> dict:
        return board.call("model_catalog_refresh", {"requestId": "catalog-seed"})

    def publication_params(self, board) -> dict:
        return {}

    def publish_with(self, board, params: dict, command_id: str, **collections) -> dict:
        return board.console_call("user_policy_publish", {
            **params, "commandId": command_id, **collections,
        })

    def publish(self, board, *, request_id: str = "write-1", command_id: str = "cmd-1", **collections) -> dict:
        return self.publish_with(board, self.publication_params(board), command_id, **collections)

    def seed_profiles(self, board, *profiles) -> dict:
        self.seed_catalog(board)
        selected = profiles or (PROFILE,)
        return self.publish(board, profileSettings=[{"profileId": item["profileId"], "enabled": True} for item in selected])


    def refused_publish(self, board, code: str, *, request_id: str = "refused", command_id: str = "refused", **collections) -> BoardError:
        return self.assert_code(code, self.publish, board, command_id=command_id, **collections)


# ---------------------------------------------------------------------------
# Published settings and validation
# ---------------------------------------------------------------------------
class EvaluationPublishTests(EvaluationTestCase):
    def test_catalog_permissions_persist_through_user_publication(self):
        board = self.board()
        catalog_result = self.seed_catalog(board)
        profile = next(item for item in catalog_result["profiles"] if item["profileId"] == PROFILE_ID)
        expected_permissions = {"permission:read", "permission:write", "permission:full"}
        self.assertEqual({cap for cap in profile["capabilities"] if cap.startswith("permission:")},
                         expected_permissions)
        self.publish(board, profileSettings=[{"profileId": PROFILE_ID, "enabled": True}])
        published = next(item for item in self.snapshot(board)["profiles"] if item["profileId"] == PROFILE_ID)
        self.assertEqual({cap for cap in published["capabilities"] if cap.startswith("permission:")},
                         expected_permissions)

    def test_fresh_snapshot_is_unconfigured_without_a_verified_router(self):
        board = self.board()
        snapshot = self.snapshot(board)
        self.assertNotIn("tableRevision", snapshot)
        self.assertEqual(snapshot["configuration"]["routerProfileIds"], [])
        self.assertEqual(snapshot["configuration"]["routingBudget"], "standard")
        self.assertEqual(snapshot["profiles"], [])
        self.assertEqual(snapshot["preferences"], [])
        self.assertEqual(snapshot["modelServiceNotes"], [])
        self.assertEqual(snapshot["decisions"], [])
        # A no-tool executor can offer selection without a configured Router;
        # fresh settings remain empty until the user chooses each slot.
        self.assertIsInstance(snapshot["capabilities"]["selection"], bool)
        self.assertEqual(snapshot["configuration"]["defaultRoutingMode"], "fast")
        self.assertEqual(snapshot["configuration"]["routerProfileIds"], [])
        self.assertIn("runs", snapshot["tasks"])

    def test_user_patches_preserve_omitted_fields(self):
        board = self.board()
        self.seed_catalog(board)
        first = self.publish(board, command_id="c1",
                             profileSettings=[{"profileId": PROFILE_ID, "enabled": True}],
                             preferenceChanges=[{"profileId": PROFILE_ID, "mode": "prefer", "reason": "cheap and fast"}],
                             modelServiceNoteChanges=[{**model_service_key(PROFILE), "text": "human context"}])
        self.assertTrue(first["published"])
        self.publish(board, command_id="c2", profileSettings=[])
        snapshot = self.snapshot(board)
        self.assertTrue(next(item for item in snapshot["profiles"] if item["profileId"] == PROFILE_ID)["enabled"])
        self.assertEqual(snapshot["preferences"][0]["reason"], "cheap and fast")
        self.assertEqual(snapshot["modelServiceNotes"][0]["text"], "human context")

    def test_program_fields_are_rejected(self):
        board = self.board()
        self.seed_profiles(board)
        self.refused_publish(board, "INVALID_ARGUMENT",
                             profileSettings=[{"profileId": PROFILE_ID, "enabled": True, "available": True}])

    def test_two_setting_drafts_last_write_wins_and_command_replays(self):
        board = self.board()
        self.seed_catalog(board)
        params = self.publication_params(board)
        first = self.publish_with(board, params, "first-tab", modelServiceNoteChanges=[{**model_service_key(PROFILE), "text": "first"}])
        second = self.publish_with(board, params, "second-tab",
                                   modelServiceNoteChanges=[{**model_service_key(PROFILE), "text": "second"}])
        snapshot = self.snapshot(board)
        self.assertTrue(first["published"] and second["published"])
        self.assertFalse(next(p for p in snapshot["profiles"] if p["profileId"] == PROFILE_ID)["enabled"])
        self.assertEqual(snapshot["modelServiceNotes"][0]["text"], "second")
        self.assertTrue(self.publish_with(board, params, "first-tab", modelServiceNoteChanges=[{**model_service_key(PROFILE), "text": "first"}])["duplicate"])
        self.assertEqual(self.snapshot(board)["modelServiceNotes"][0]["text"], "second")

    def test_profile_execution_identity_is_program_owned(self):
        board = self.board()
        self.seed_profiles(board)
        original = next(item for item in self.snapshot(board)["profiles"] if item["profileId"] == PROFILE_ID)
        self.refused_publish(board, "INVALID_ARGUMENT", request_id="w2", command_id="c2",
                             profiles=[{**PROFILE, "model": "deepseek-v4-pro"}])
        current = next(item for item in self.snapshot(board)["profiles"] if item["profileId"] == PROFILE_ID)
        self.assertEqual(current["model"], original["model"])
        self.assertEqual(current["source"], original["source"])

    def test_dangling_references_are_refused_and_existing_intent_is_retained(self):
        from protocol.fixtures import mock_readonly
        mock_readonly.install(self)
        board = self.board()
        self.seed_profiles(board, PROFILE, SECOND_PROFILE)
        self.refused_publish(board, "NOT_FOUND", request_id="w2", command_id="c2",
                             preferenceChanges=[{"profileId": "dsh:nope:nope:off", "mode": "prefer", "reason": "unknown"}])
        self.publish(board, request_id="w3", command_id="c3",
                     preferenceChanges=[{"profileId": SECOND_PROFILE_ID, "mode": "pin", "reason": "user pin"}],
                     configuration={"routerProfileIds": [SECOND_PROFILE_ID]})
        self.publish(board, request_id="w4", command_id="c4",
                     profileSettings=[{"profileId": SECOND_PROFILE_ID, "enabled": False}],
                     modelServiceNoteChanges=[{**model_service_key(PROFILE), "text": "other edit"}])
        snapshot = self.snapshot(board)
        self.assertEqual(snapshot["preferences"][0]["mode"], "pin")
        self.assertEqual(snapshot["configuration"]["routerProfileIds"], [SECOND_PROFILE_ID])
        self.assertEqual(snapshot["modelServiceNotes"][0]["text"], "other edit")

    def test_configuration_carries_router_profile_and_budget_without_maintenance(self):
        from protocol.fixtures import mock_readonly
        mock_readonly.install(self)
        board = self.board()
        self.seed_profiles(board)
        self.publish(board, request_id="w2", command_id="c2",
                     configuration={"routerProfileIds": [PROFILE_ID]})
        self.assertEqual(self.snapshot(board)["configuration"]["routerProfileIds"], [PROFILE_ID])
        self.assertEqual(self.snapshot(board)["configuration"]["routingBudget"], "standard")
        self.refused_publish(board, "INVALID_ARGUMENT", request_id="w3", command_id="c3",
                             configuration={"routerProfileIds": [PROFILE_ID], "autoMaintain": True})
        self.refused_publish(board, "NOT_FOUND", request_id="w4", command_id="c4",
                             configuration={"routerProfileIds": ["dsh:nope:nope:off"]})
        self.assertEqual(self.snapshot(board)["configuration"]["routerProfileIds"], [PROFILE_ID])

    def test_availability_is_refreshed_by_catalog_and_new_enable_requires_it(self):
        board = self.board()
        self.refused_publish(board, "NOT_FOUND", request_id="w1", command_id="c1",
                             profileSettings=[{"profileId": PROFILE_ID, "enabled": True}])
        self.seed_catalog(board)
        self.assertTrue(next(item for item in self.snapshot(board)["profiles"] if item["profileId"] == PROFILE_ID)["available"])
        self.refused_publish(board, "NOT_FOUND", request_id="w2", command_id="c2",
                             profileSettings=[{"profileId": "dsh:nope:nope:off", "enabled": True}])

    def test_pin_requires_an_available_enabled_profile(self):
        board = self.board()
        self.seed_catalog(board)
        self.refused_publish(board, "CONFIGURATION_UNAVAILABLE", request_id="w1", command_id="c1",
                             preferenceChanges=[{"profileId": PROFILE_ID, "mode": "pin", "reason": "wanted"}])
        self.publish(board, request_id="w2", command_id="c2",
                     profileSettings=[{"profileId": PROFILE_ID, "enabled": True}])
        self.publish(board, request_id="w3", command_id="c3",
                     preferenceChanges=[{"profileId": PROFILE_ID, "mode": "pin", "reason": "wanted"}])
        self.assertEqual(self.snapshot(board)["preferences"][0]["mode"], "pin")

    def test_omitted_collections_and_explicit_null_are_distinct_commands(self):
        board = self.board()
        self.seed_catalog(board)
        grant = self.publication_params(board)
        base = {"commandId": "same-command", **grant}
        payload = {**base, "modelServiceNoteChanges": [{**model_service_key(PROFILE), "text": "first"}]}
        board.console_call("user_policy_publish", payload)
        self.assertTrue(board.console_call("user_policy_publish", payload)["duplicate"])
        self.assert_code("CONFLICT", board.console_call, "user_policy_publish",
                         {**base, "modelServiceNoteChanges": []})
        self.assert_code("CONFLICT", board.console_call, "user_policy_publish",
                         {**base, "modelServiceNoteChanges": None})
        other = self.publication_params(board)
        self.assert_code("INVALID_ARGUMENT", self.publish_with, board, other, "fresh-command",
                         modelServiceNoteChanges=None)
        self.assertEqual(self.snapshot(board)["modelServiceNotes"][0]["text"], "first")


    def test_snapshot_never_leaks_credentials(self):
        board = self.board()
        self.seed_catalog(board)
        grant = self.publication_params(board)
        self.publish_with(board, grant, "c1",
                          modelServiceNoteChanges=[{**model_service_key(PROFILE), "text": "visible opinion"}])
        raw = json.dumps(self.snapshot(board))
        self.assertNotIn(board.service.token, raw)
        self.assertNotIn("token_verifier", raw)

    def test_buddy_preferences_are_saved_per_profile_and_can_be_cleared(self):
        board = self.board()
        self.seed_catalog(board)
        other_effort = next(item for item in self.snapshot(board)["profiles"]
                            if item["model"] == PROFILE["model"] and item["profileId"] != PROFILE_ID)
        published = self.publish(board, request_id="w1", command_id="c1",
                                 preferenceChanges=[{"profileId": PROFILE_ID, "mode": "prefer",
                                                     "reason": "cheap and fast"}])
        self.assertEqual(published["counts"]["preferenceChanges"], 1)
        snapshot = self.snapshot(board)
        self.assertEqual(snapshot["preferences"],
                         [{"profileId": PROFILE_ID, "mode": "prefer", "reason": "cheap and fast"}])
        self.assertNotIn(other_effort["profileId"],
                         {entry["profileId"] for entry in snapshot["preferences"]})
        self.publish(board, request_id="w2", command_id="c2",
                     preferenceChanges=[{"profileId": other_effort["profileId"], "mode": "prefer",
                                         "reason": "higher effort"},
                                        {"profileId": PROFILE_ID, "mode": "exclude", "reason": "too costly"}])
        other_preference = {"profileId": other_effort["profileId"], "mode": "prefer",
                            "reason": "higher effort"}
        self.assertEqual(self.snapshot(board)["preferences"],
                         sorted([other_preference,
                                 {"profileId": PROFILE_ID, "mode": "exclude", "reason": "too costly"}],
                                key=lambda entry: entry["profileId"]))
        self.publish(board, request_id="w3", command_id="c3",
                     preferenceChanges=[{"profileId": PROFILE_ID, "mode": None}])
        self.assertEqual(self.snapshot(board)["preferences"], [other_preference])

    def test_retired_preference_shapes_are_rejected_without_changes(self):
        board = self.board()
        self.seed_catalog(board)
        self.refused_publish(board, "INVALID_ARGUMENT", request_id="w1", command_id="c1",
                             familyPreferenceChanges=[{**model_service_key(PROFILE), "mode": "prefer"}])
        self.refused_publish(board, "INVALID_ARGUMENT", request_id="w2", command_id="c2",
                             preferenceChanges=[{"profileId": PROFILE_ID, "mode": "none"}])
        self.assertEqual(self.snapshot(board)["preferences"], [])

    def test_model_service_notes_write_update_and_clear(self):
        board = self.board()
        self.seed_catalog(board)
        first = self.publish(board, request_id="w1", command_id="c1",
                             modelServiceNoteChanges=[{**model_service_key(PROFILE), "text": "first note"}])
        self.assertEqual(first["counts"]["modelServiceNoteChanges"], 1)
        note = self.snapshot(board)["modelServiceNotes"][0]
        self.assertEqual((note["adapter"], note["provider"], note["model"]),
                         (PROFILE["adapter"], PROFILE["provider"], PROFILE["model"]))
        self.assertEqual(note["text"], "first note")
        self.assertTrue(note["updatedAt"])
        self.publish(board, request_id="w2", command_id="c2",
                     modelServiceNoteChanges=[{**model_service_key(PROFILE), "text": "second note"}])
        updated = self.snapshot(board)["modelServiceNotes"][0]
        self.assertEqual(updated["text"], "second note")
        self.publish(board, request_id="w3", command_id="c3",
                     modelServiceNoteChanges=[{**model_service_key(PROFILE), "text": ""}])
        self.assertEqual(self.snapshot(board)["modelServiceNotes"], [])


class CatalogRefreshTests(EvaluationTestCase):


    def test_catalog_refresh_is_explicit_attributed_and_failure_safe(self):
        board = self.board()
        refreshed = board.call("model_catalog_refresh", {"requestId": "cat-1"})
        self.assertEqual(refreshed["catalog"]["source"], f"file:{self.directory / 'model-catalog.json'}")
        self.assertEqual(refreshed["catalog"]["harnessVersion"], "test-harness-1")
        self.assertEqual(refreshed["catalog"]["providers"][0]["efforts"], ["off", "low", "high", "max"])
        proposals = refreshed["profiles"]
        self.assertTrue(proposals)
        self.assertTrue(all(item["enabled"] is False for item in proposals), "discovered profiles start disabled")
        self.assertTrue(all(item["available"] is True for item in proposals))
        self.assertIn("refreshed", refreshed["note"])
        self.assertNotIn("tableRevision", refreshed)
        self.assertEqual(len(self.snapshot(board)["profiles"]), len(proposals))
        profile_ids = [profile["profileId"] for profile in self.snapshot(board)["profiles"]]
        # A new observation of identical native metadata keeps the same profiles.
        repeated = board.call("model_catalog_refresh", {"requestId": "cat-2"})
        self.assertTrue(repeated["duplicate"])
        self.assertEqual([profile["profileId"] for profile in self.snapshot(board)["profiles"]], profile_ids)

        # A failed discovery keeps the previous recorded catalog and the table.
        from hey_my_buddy.blackboard.catalog import catalog
        from hey_my_buddy.errors import BoardError as _BoardError

        original = catalog.discover
        catalog.discover = lambda **_kwargs: (_ for _ in ()).throw(_BoardError("CATALOG_UNAVAILABLE", "no harness"))
        try:
            self.assert_code("CATALOG_UNAVAILABLE", board.call, "model_catalog_refresh", {"requestId": "cat-3"})
        finally:
            catalog.discover = original
        self.assertEqual(len(self.snapshot(board)["profiles"]), len(proposals))


if __name__ == "__main__":
    unittest.main()


class ConsoleCoreTests(BoardTestCase):
    """console_core is the shared board-state core behind both snapshot reads."""

    def test_core_carries_the_state_keys_and_snapshot_wraps_them(self):
        board = self.board()
        core = board.evaluation.console_core()
        for key in ("configuration", "configurationError", "profiles",
                    "modelServiceConcurrency", "unavailableProfileCount", "preferences", "modelServiceNotes",
                    "decisions"):
            self.assertIn(key, core)
        snapshot = board.evaluation.snapshot({})
        for key in core:
            self.assertEqual(snapshot[key], core[key], key)
        self.assertIn("runs", snapshot["tasks"])
        self.assertIn("capabilities", snapshot)
