"""Router setting choices are independent of native availability and state."""
from copy import deepcopy
from dataclasses import FrozenInstanceError
import unittest

from buddy.errors import BoardError
from buddy.router_settings import RouterSettings, convert_legacy_router_settings, validate_router_settings_patch


class RouterSettingsTests(unittest.TestCase):
    def test_patch_preserves_omissions_and_explicit_clear(self):
        self.assertEqual(validate_router_settings_patch({"routingBudget": "brief"}), {"routingBudget": "brief"})
        self.assertEqual(validate_router_settings_patch({"routerProfileId": None}), {"routerProfileId": None})
        profile = "codex:openai:example:high"
        self.assertEqual(validate_router_settings_patch({"routerProfileId": profile}), {"routerProfileId": profile})

    def test_invalid_current_fields_and_values_are_refused(self):
        for value in (None, [], {}, {"fastRouterProfileId": "old"}, {"reviewRouterProfileId": None},
                      {"decisionProfileId": "old"}, {"revision": 1}, {"routingBudgetLimits": {}},
                      {"routingBudget": "quick"}, {"routingBudget": None}, {"defaultRoutingMode": "auto"},
                      {"defaultRoutingMode": None}, {"routerProfileId": " "}, {"routerProfileId": "bad/id"},
                      {"routerProfileId": 1}, {"routerProfileId": "a" * 129}):
            with self.subTest(value=value), self.assertRaises(BoardError) as failure:
                validate_router_settings_patch(value)
            self.assertEqual(failure.exception.code, "INVALID_ARGUMENT")

    def test_each_default_selects_only_its_own_slot(self):
        for mode, chosen, discarded in (("fast", "fast-id", "review-id"), ("review", "review-id", "fast-id")):
            with self.subTest(mode=mode):
                result = convert_legacy_router_settings({"fastRouterProfileId": "fast-id", "reviewRouterProfileId": "review-id",
                                                         "defaultRoutingMode": mode, "routingBudget": "deep"})
                self.assertEqual(result.settings.as_dict(), {"routerProfileId": chosen, "defaultRoutingMode": mode, "routingBudget": "deep"})
                self.assertEqual(result.discarded_profile_id, discarded)

    def test_missing_or_null_selected_slot_never_borrows_the_other(self):
        for mode, selected, other in (("fast", "fastRouterProfileId", "reviewRouterProfileId"),
                                      ("review", "reviewRouterProfileId", "fastRouterProfileId")):
            for present in (False, True):
                entry = {"defaultRoutingMode": mode, other: "other-id"}
                if present:
                    entry[selected] = None
                with self.subTest(mode=mode, present=present):
                    result = convert_legacy_router_settings(entry)
                    self.assertIsNone(result.settings.router_profile_id)
                    self.assertEqual(result.settings.default_routing_mode, mode)
                    self.assertEqual(result.discarded_profile_id, "other-id")

    def test_conversion_defaults_and_old_budget_are_explicit(self):
        self.assertEqual(convert_legacy_router_settings({}).settings, RouterSettings())
        for preset in ("brief", "standard", "deep", "quick"):
            with self.subTest(preset=preset):
                self.assertEqual(convert_legacy_router_settings({"routingBudget": preset}).settings.routing_budget,
                                 "brief" if preset == "quick" else preset)

    def test_conversion_is_pure_repeatable_and_keeps_unavailable_identity(self):
        # No connection or harness exists here: even an unavailable/unknown ID
        # must stay chosen, rather than being screened into the other slot.
        entry = {"defaultRoutingMode": "review", "reviewRouterProfileId": "unavailable-id",
                 "fastRouterProfileId": "healthy-id", "routingBudget": "quick"}
        before = deepcopy(entry)
        first = convert_legacy_router_settings(entry)
        self.assertEqual(first, convert_legacy_router_settings(entry))
        self.assertEqual(entry, before)
        self.assertEqual(first.settings.router_profile_id, "unavailable-id")
        with self.assertRaises(FrozenInstanceError):
            first.settings.router_profile_id = "healthy-id"

    def test_duplicate_slots_remain_a_deterministic_audit_record(self):
        result = convert_legacy_router_settings({"fastRouterProfileId": "same", "reviewRouterProfileId": "same"})
        self.assertEqual(result.settings.router_profile_id, "same")
        self.assertEqual(result.discarded_profile_id, "same")

    def test_invalid_legacy_data_is_not_guessed(self):
        for entry in (None, [], {"decision_profile_id": "old"}, {"routerProfileId": "new"},
                      {"defaultRoutingMode": None}, {"defaultRoutingMode": "auto"}, {"routingBudget": None},
                      {"routingBudget": "unlimited"}, {"fastRouterProfileId": False},
                      {"reviewRouterProfileId": "bad/id"}):
            with self.subTest(entry=entry), self.assertRaises(BoardError):
                convert_legacy_router_settings(entry)


if __name__ == "__main__":
    unittest.main()
