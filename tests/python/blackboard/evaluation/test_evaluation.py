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


def family_key(profile: dict) -> dict:
    """The ``(adapter, provider, model)`` key a family-keyed row or patch uses."""
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

    def publication_params(self, board, expected: int | None = None) -> dict:
        return {"expectedRevision": self.snapshot(board)["tableRevision"] if expected is None else expected}

    def publish_with(self, board, params: dict, command_id: str, *, expected: int | None = None, **collections) -> dict:
        return board.console_call("user_policy_publish", {
            **params, "commandId": command_id,
            **({"expectedRevision": expected} if expected is not None else {}), **collections,
        })

    def publish(self, board, *, request_id: str = "write-1", command_id: str = "cmd-1", expected: int | None = None, **collections) -> dict:
        return self.publish_with(board, self.publication_params(board, expected), command_id, **collections)

    def seed_profiles(self, board, *profiles) -> dict:
        self.seed_catalog(board)
        selected = profiles or (PROFILE,)
        return self.publish(board, profileSettings=[{"profileId": item["profileId"], "enabled": True} for item in selected])


    def refused_publish(self, board, code: str, *, request_id: str = "refused", command_id: str = "refused", **collections) -> BoardError:
        return self.assert_code(code, self.publish, board, command_id=command_id, **collections)


# ---------------------------------------------------------------------------
# Published revisions and validation
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
        self.assertEqual(snapshot["tableRevision"], 0)
        self.assertEqual({key: snapshot["configuration"][key] for key in ("revision", "routerProfileIds")}, {"revision": 0, "routerProfileIds": []})
        self.assertEqual(snapshot["configuration"]["routingBudget"], "standard")
        self.assertEqual(snapshot["profiles"], [])
        self.assertEqual(snapshot["familyPreferences"], [])
        self.assertEqual(snapshot["preferenceOverrides"], [])
        self.assertEqual(snapshot["preferences"], [])
        self.assertEqual(snapshot["familyAnnotations"], [])
        self.assertEqual(snapshot["decisions"], [])
        # A no-tool executor can offer selection without a configured Router;
        # fresh settings remain empty until the user chooses each slot.
        self.assertIsInstance(snapshot["capabilities"]["selection"], bool)
        self.assertEqual(snapshot["configuration"]["defaultRoutingMode"], "fast")
        self.assertEqual(snapshot["configuration"]["routerProfileIds"], [])
        self.assertIn("runs", snapshot["tasks"])

    def test_user_patches_preserve_omitted_fields(self):
        board = self.board()
        catalog = self.seed_catalog(board)
        first = self.publish(board, command_id="c1",
                             profileSettings=[{"profileId": PROFILE_ID, "enabled": True}],
                             preferenceChanges=[{"profileId": PROFILE_ID, "mode": "prefer", "reason": "cheap and fast"}],
                             familyAnnotationChanges=[{**family_key(PROFILE), "text": "human context"}])
        self.assertEqual(first["revision"], catalog["tableRevision"] + 1)
        self.publish(board, command_id="c2", profileSettings=[])
        snapshot = self.snapshot(board)
        self.assertTrue(next(item for item in snapshot["profiles"] if item["profileId"] == PROFILE_ID)["enabled"])
        self.assertEqual(snapshot["preferences"][0]["reason"], "cheap and fast")
        self.assertEqual(snapshot["familyAnnotations"][0]["text"], "human context")

    def test_program_fields_are_rejected(self):
        board = self.board()
        self.seed_profiles(board)
        self.refused_publish(board, "INVALID_ARGUMENT",
                             profileSettings=[{"profileId": PROFILE_ID, "enabled": True, "available": True}])

    def test_two_setting_drafts_conflict_without_partial_changes(self):
        board = self.board()
        self.seed_catalog(board)
        params = self.publication_params(board)
        first = self.publish_with(board, params, "first-tab", familyAnnotationChanges=[{**family_key(PROFILE), "text": "first"}])
        self.assert_code("REVISION_CONFLICT", self.publish_with, board, params, "second-tab",
                         profileSettings=[{"profileId": PROFILE_ID, "enabled": True}])
        snapshot = self.snapshot(board)
        self.assertEqual(snapshot["tableRevision"], first["revision"])
        self.assertFalse(next(p for p in snapshot["profiles"] if p["profileId"] == PROFILE_ID)["enabled"])
        self.assertEqual(snapshot["familyAnnotations"][0]["text"], "first")
        self.assertTrue(self.publish_with(board, params, "first-tab", familyAnnotationChanges=[{**family_key(PROFILE), "text": "first"}])["duplicate"])

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
                     familyAnnotationChanges=[{**family_key(PROFILE), "text": "other edit"}])
        snapshot = self.snapshot(board)
        self.assertEqual(snapshot["preferences"][0]["mode"], "pin")
        self.assertEqual(snapshot["configuration"]["routerProfileIds"], [SECOND_PROFILE_ID])
        self.assertEqual(snapshot["familyAnnotations"][0]["text"], "other edit")

    def test_configuration_carries_router_profile_and_budget_without_maintenance(self):
        from protocol.fixtures import mock_readonly
        mock_readonly.install(self)
        board = self.board()
        self.seed_profiles(board)
        published = self.publish(board, request_id="w2", command_id="c2",
                                 configuration={"routerProfileIds": [PROFILE_ID]})
        self.assertEqual(self.snapshot(board)["configuration"]["routerProfileIds"], [PROFILE_ID])
        self.assertEqual(self.snapshot(board)["configuration"]["revision"], 1)
        self.assertEqual(self.snapshot(board)["configuration"]["routingBudget"], "standard")
        self.refused_publish(board, "INVALID_ARGUMENT", request_id="w3", command_id="c3",
                             configuration={"routerProfileIds": [PROFILE_ID], "autoMaintain": True})
        self.refused_publish(board, "NOT_FOUND", request_id="w4", command_id="c4",
                             configuration={"routerProfileIds": ["dsh:nope:nope:off"]})
        self.assertEqual(self.snapshot(board)["tableRevision"], published["revision"])

    def test_availability_is_refreshed_by_catalog_and_new_enable_requires_it(self):
        board = self.board()
        self.refused_publish(board, "NOT_FOUND", request_id="w1", command_id="c1",
                             profileSettings=[{"profileId": PROFILE_ID, "enabled": True}])
        refreshed = self.seed_catalog(board)
        self.assertEqual(self.snapshot(board)["tableRevision"], refreshed["tableRevision"])
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
        payload = {**base, "familyAnnotationChanges": [{**family_key(PROFILE), "text": "first"}]}
        first = board.console_call("user_policy_publish", payload)
        self.assertTrue(board.console_call("user_policy_publish", payload)["duplicate"])
        self.assert_code("CONFLICT", board.console_call, "user_policy_publish",
                         {**base, "familyAnnotationChanges": []})
        self.assert_code("CONFLICT", board.console_call, "user_policy_publish",
                         {**base, "familyAnnotationChanges": None})
        other = self.publication_params(board)
        self.assert_code("INVALID_ARGUMENT", self.publish_with, board, other, "fresh-command",
                         familyAnnotationChanges=None)
        self.assertEqual(self.snapshot(board)["tableRevision"], first["revision"])


    def test_snapshot_never_leaks_credentials(self):
        board = self.board()
        self.seed_catalog(board)
        grant = self.publication_params(board)
        self.publish_with(board, grant, "c1",
                          familyAnnotationChanges=[{**family_key(PROFILE), "text": "visible opinion"}])
        raw = json.dumps(self.snapshot(board))
        self.assertNotIn(board.service.token, raw)
        self.assertNotIn("token_verifier", raw)

    def test_family_preference_covers_every_effort_and_a_none_override_wins(self):
        board = self.board()
        self.seed_catalog(board)
        published = self.publish(
            board, request_id="w1", command_id="c1",
            familyPreferenceChanges=[{**family_key(PROFILE), "mode": "prefer", "reason": "cheap for the family"}],
        )
        self.assertEqual(published["counts"]["familyPreferenceChanges"], 1)
        snapshot = self.snapshot(board)
        self.assertEqual(
            snapshot["familyPreferences"],
            [{**family_key(PROFILE), "mode": "prefer", "reason": "cheap for the family"}],
        )
        self.assertEqual(snapshot["preferenceOverrides"], [])
        efforts = [item for item in snapshot["profiles"] if item["model"] == PROFILE["model"]]
        self.assertGreater(len(efforts), 1, "the family has several effort profiles")
        self.assertEqual(
            {entry["profileId"] for entry in snapshot["preferences"]},
            {item["profileId"] for item in efforts},
        )
        self.assertTrue(all(
            (entry["mode"], entry["reason"], entry["source"]) == ("prefer", "cheap for the family", "family")
            for entry in snapshot["preferences"]
        ))
        # One effort opts out explicitly; only that effort leaves the effective view.
        self.publish(board, request_id="w2", command_id="c2",
                     preferenceChanges=[{"profileId": PROFILE_ID, "mode": "none", "reason": "this effort is not preferred"}])
        snapshot = self.snapshot(board)
        self.assertNotIn(PROFILE_ID, {entry["profileId"] for entry in snapshot["preferences"]})
        self.assertEqual(snapshot["preferenceOverrides"],
                         [{"profileId": PROFILE_ID, "mode": "none", "reason": "this effort is not preferred"}])
        self.assertTrue(all(entry["source"] == "family" for entry in snapshot["preferences"]))
        # Deleting the override restores the family default for that effort.
        self.publish(board, request_id="w3", command_id="c3",
                     preferenceChanges=[{"profileId": PROFILE_ID, "mode": None}])
        snapshot = self.snapshot(board)
        restored = next(entry for entry in snapshot["preferences"] if entry["profileId"] == PROFILE_ID)
        self.assertEqual((restored["mode"], restored["reason"], restored["source"]),
                         ("prefer", "cheap for the family", "family"))
        self.assertEqual(snapshot["preferenceOverrides"], [])

    def test_family_preference_null_clears_and_bad_families_are_refused(self):
        board = self.board()
        self.seed_catalog(board)
        self.publish(board, request_id="w1", command_id="c1",
                     familyPreferenceChanges=[{**family_key(PROFILE), "mode": "prefer"}])
        self.assertEqual(len(self.snapshot(board)["familyPreferences"]), 1)
        self.publish(board, request_id="w2", command_id="c2",
                     familyPreferenceChanges=[{**family_key(PROFILE), "mode": None}])
        snapshot = self.snapshot(board)
        self.assertEqual(snapshot["familyPreferences"], [])
        self.assertEqual(snapshot["preferences"], [])
        self.refused_publish(board, "NOT_FOUND", request_id="w3", command_id="c3",
                             familyPreferenceChanges=[{**family_key(PROFILE), "model": "no-such-model",
                                                       "mode": "prefer"}])
        self.refused_publish(board, "INVALID_ARGUMENT", request_id="w4", command_id="c4",
                             familyPreferenceChanges=[{**family_key(PROFILE), "mode": "prefer"},
                                                      {**family_key(PROFILE), "mode": "pin"}])
        self.assertEqual(self.snapshot(board)["familyPreferences"], [])

    def test_family_annotations_write_update_and_clear(self):
        board = self.board()
        self.seed_catalog(board)
        first = self.publish(board, request_id="w1", command_id="c1",
                             familyAnnotationChanges=[{**family_key(PROFILE), "text": "first note"}])
        self.assertEqual(first["counts"]["familyAnnotationChanges"], 1)
        note = self.snapshot(board)["familyAnnotations"][0]
        self.assertEqual((note["adapter"], note["provider"], note["model"]),
                         (PROFILE["adapter"], PROFILE["provider"], PROFILE["model"]))
        self.assertEqual((note["text"], note["revision"]), ("first note", first["revision"]))
        self.assertTrue(note["updatedAt"])
        second = self.publish(board, request_id="w2", command_id="c2",
                              familyAnnotationChanges=[{**family_key(PROFILE), "text": "second note"}])
        updated = self.snapshot(board)["familyAnnotations"][0]
        self.assertEqual((updated["text"], updated["revision"]), ("second note", second["revision"]))
        self.publish(board, request_id="w3", command_id="c3",
                     familyAnnotationChanges=[{**family_key(PROFILE), "text": ""}])
        self.assertEqual(self.snapshot(board)["familyAnnotations"], [])


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
        self.assertEqual(self.snapshot(board)["tableRevision"], refreshed["tableRevision"])
        self.assertEqual(len(self.snapshot(board)["profiles"]), len(proposals))
        # A new observation of identical native metadata keeps the table revision.
        repeated = board.call("model_catalog_refresh", {"requestId": "cat-2"})
        self.assertTrue(repeated["duplicate"])
        self.assertEqual(repeated["tableRevision"], refreshed["tableRevision"])
        self.assertEqual(self.snapshot(board)["tableRevision"], repeated["tableRevision"])

        # A failed discovery keeps the previous recorded catalog and the table.
        from hey_my_buddy.blackboard.catalog import catalog
        from hey_my_buddy.errors import BoardError as _BoardError

        original = catalog.discover
        catalog.discover = lambda **_kwargs: (_ for _ in ()).throw(_BoardError("CATALOG_UNAVAILABLE", "no harness"))
        try:
            self.assert_code("CATALOG_UNAVAILABLE", board.call, "model_catalog_refresh", {"requestId": "cat-3"})
        finally:
            catalog.discover = original
        self.assertEqual(self.snapshot(board)["tableRevision"], repeated["tableRevision"])


if __name__ == "__main__":
    unittest.main()


class ConsoleCoreTests(BoardTestCase):
    """console_core is the shared board-state core behind both snapshot reads."""

    def test_core_carries_the_state_keys_and_snapshot_wraps_them(self):
        board = self.board()
        core = board.evaluation.console_core()
        for key in ("tableRevision", "configuration", "configurationError", "profiles",
                    "modelConcurrency", "unavailableProfileCount", "preferences", "familyAnnotations",
                    "familyPreferences", "preferenceOverrides", "decisions"):
            self.assertIn(key, core)
        snapshot = board.evaluation.snapshot({})
        for key in core:
            self.assertEqual(snapshot[key], core[key], key)
        self.assertIn("runs", snapshot["tasks"])
        self.assertIn("capabilities", snapshot)
