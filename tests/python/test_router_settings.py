"""Router list setting choices are independent of native availability and state."""
from copy import deepcopy
from dataclasses import FrozenInstanceError
import unittest

from buddy.errors import BoardError
from buddy.router_settings import RouterSettings, convert_legacy_router_settings, validate_router_settings_patch

PROFILE = "codex:openai:example:high"
RETRY_MIN_SECONDS = 1
RETRY_MAX_SECONDS = 2147483647


class RouterSettingsTests(unittest.TestCase):
    def test_settings_defaults_and_public_projection(self):
        settings = RouterSettings()
        self.assertEqual(settings.router_profile_ids, ())
        self.assertEqual(settings.router_retry_interval_seconds, 600)
        self.assertEqual(settings.default_routing_mode, "fast")
        self.assertEqual(settings.routing_budget, "standard")
        self.assertEqual(settings.as_dict(), {"routerProfileIds": [], "routerRetryIntervalSeconds": 600,
                                              "defaultRoutingMode": "fast", "routingBudget": "standard"})

    def test_as_dict_returns_a_fresh_copy_each_call(self):
        settings = RouterSettings((PROFILE,), 30, "review", "brief")
        first = settings.as_dict()
        second = settings.as_dict()
        self.assertIsNot(first, second)
        self.assertIsNot(first["routerProfileIds"], second["routerProfileIds"])
        self.assertEqual(first, second)
        first["routerProfileIds"].append("mutated:after:the:fact")
        self.assertEqual(settings.router_profile_ids, (PROFILE,))
        self.assertEqual(second["routerProfileIds"], [PROFILE])

    def test_constructor_enforces_types_invariants_and_seconds_bounds(self):
        for kwargs in ({"router_profile_ids": ["not-a-tuple"]},
                       {"router_profile_ids": "solo"},
                       {"router_profile_ids": (PROFILE, None)},
                       {"router_profile_ids": (1,)},
                       {"router_profile_ids": ("bad/id",)},
                       {"router_profile_ids": ("a" * 129,)},
                       {"router_profile_ids": (PROFILE, PROFILE)},
                       {"router_retry_interval_seconds": True},
                       {"router_retry_interval_seconds": 600.0},
                       {"router_retry_interval_seconds": "600"},
                       {"router_retry_interval_seconds": None},
                       {"router_retry_interval_seconds": 0},
                       {"router_retry_interval_seconds": RETRY_MAX_SECONDS + 1},
                       {"default_routing_mode": "auto"},
                       {"default_routing_mode": None},
                       {"routing_budget": "quick"},
                       {"routing_budget": None}):
            with self.subTest(kwargs=kwargs), self.assertRaises(BoardError) as failure:
                RouterSettings(**kwargs)
            self.assertEqual(failure.exception.code, "INVALID_ARGUMENT")
        for seconds in (RETRY_MIN_SECONDS, RETRY_MAX_SECONDS):
            with self.subTest(seconds=seconds):
                self.assertEqual(RouterSettings(router_retry_interval_seconds=seconds).router_retry_interval_seconds,
                                 seconds)
        with self.assertRaises(FrozenInstanceError):
            RouterSettings((PROFILE,)).router_profile_ids = ()

    def test_patch_preserves_omissions_and_explicit_clear(self):
        self.assertEqual(validate_router_settings_patch({"routingBudget": "brief"}), {"routingBudget": "brief"})
        # The explicit clear is now an empty array; null is refused instead of clearing.
        self.assertEqual(validate_router_settings_patch({"routerProfileIds": []}), {"routerProfileIds": []})
        self.assertEqual(validate_router_settings_patch({"routerProfileIds": [PROFILE]}),
                         {"routerProfileIds": [PROFILE]})
        self.assertEqual(validate_router_settings_patch({"routerRetryIntervalSeconds": 30, "defaultRoutingMode": "review"}),
                         {"routerRetryIntervalSeconds": 30, "defaultRoutingMode": "review"})

    def test_patch_rejects_invalid_lists_and_values(self):
        for value in (None, [], {}, {"routerProfileId": PROFILE}, {"fastRouterProfileId": "old"},
                      {"reviewRouterProfileId": None}, {"decisionProfileId": "old"}, {"revision": 1},
                      {"routingBudgetLimits": {}}, {"routingBudget": "quick"}, {"routingBudget": None},
                      {"defaultRoutingMode": "auto"}, {"defaultRoutingMode": None},
                      {"routerProfileIds": None}, {"routerProfileIds": "solo"}, {"routerProfileIds": (PROFILE,)},
                      {"routerProfileIds": [PROFILE, None]}, {"routerProfileIds": [1]},
                      {"routerProfileIds": [" "]}, {"routerProfileIds": ["bad/id"]},
                      {"routerProfileIds": ["a" * 129]}, {"routerProfileIds": [PROFILE, PROFILE]},
                      {"routerRetryIntervalSeconds": True}, {"routerRetryIntervalSeconds": 600.0},
                      {"routerRetryIntervalSeconds": "600"}, {"routerRetryIntervalSeconds": None},
                      {"routerRetryIntervalSeconds": 0}, {"routerRetryIntervalSeconds": -1},
                      {"routerRetryIntervalSeconds": RETRY_MAX_SECONDS + 1}):
            with self.subTest(value=value), self.assertRaises(BoardError) as failure:
                validate_router_settings_patch(value)
            self.assertEqual(failure.exception.code, "INVALID_ARGUMENT")
        for seconds in (RETRY_MIN_SECONDS, RETRY_MAX_SECONDS):
            with self.subTest(seconds=seconds):
                self.assertEqual(validate_router_settings_patch({"routerRetryIntervalSeconds": seconds}),
                                 {"routerRetryIntervalSeconds": seconds})

    def test_patch_and_settings_accept_large_lists_without_item_limits(self):
        many = [f"provider:model-{index}:off" for index in range(200)]
        self.assertEqual(validate_router_settings_patch({"routerProfileIds": many}), {"routerProfileIds": many})
        settings = RouterSettings(tuple(many), 30, "review", "deep")
        self.assertEqual(settings.router_profile_ids, tuple(many))
        self.assertEqual(settings.as_dict()["routerProfileIds"], many)

    def test_patch_validation_keeps_input_and_result_independent(self):
        ids = [PROFILE]
        entry = {"routerProfileIds": ids, "routerRetryIntervalSeconds": 30, "routingBudget": "deep"}
        before = deepcopy(entry)
        result = validate_router_settings_patch(entry)
        self.assertEqual(entry, before)
        ids.append("changed:after:the:call")
        self.assertEqual(result["routerProfileIds"], [PROFILE])

    def test_each_default_mode_keeps_both_slots_in_mode_order(self):
        # Rewritten from the single-slot contract: the other slot is no longer
        # discarded; the default mode's slot leads and the other one follows.
        for mode, expected in (("fast", ("fast-id", "review-id")), ("review", ("review-id", "fast-id"))):
            with self.subTest(mode=mode):
                result = convert_legacy_router_settings({"fastRouterProfileId": "fast-id", "reviewRouterProfileId": "review-id",
                                                         "defaultRoutingMode": mode, "routingBudget": "deep"})
                self.assertEqual(result.settings.as_dict(),
                                 {"routerProfileIds": list(expected), "routerRetryIntervalSeconds": 600,
                                  "defaultRoutingMode": mode, "routingBudget": "deep"})
                self.assertEqual(result.source_slots, ("fast-id", "review-id"))

    def test_missing_or_null_selected_slot_keeps_the_other_slot_first(self):
        # Rewritten: the surviving other slot is kept (was discarded) and, when the
        # selected slot is empty or absent, it becomes the first list item.
        for mode, selected, other in (("fast", "fastRouterProfileId", "reviewRouterProfileId"),
                                      ("review", "reviewRouterProfileId", "fastRouterProfileId")):
            for present in (False, True):
                entry = {"defaultRoutingMode": mode, other: "other-id"}
                if present:
                    entry[selected] = None
                with self.subTest(mode=mode, present=present):
                    result = convert_legacy_router_settings(entry)
                    self.assertEqual(result.settings.router_profile_ids, ("other-id",))
                    self.assertEqual(result.settings.default_routing_mode, mode)
                    self.assertIsNone(result.source_slots[0 if mode == "fast" else 1])

    def test_conversion_matrix_keeps_order_deduplicates_and_audits_slots(self):
        cases = [
            ({}, (), (None, None)),
            ({"defaultRoutingMode": "fast"}, (), (None, None)),
            ({"defaultRoutingMode": "review"}, (), (None, None)),
            ({"fastRouterProfileId": "f"}, ("f",), ("f", None)),
            ({"reviewRouterProfileId": "r"}, ("r",), (None, "r")),
            ({"fastRouterProfileId": "f", "reviewRouterProfileId": "r"}, ("f", "r"), ("f", "r")),
            ({"fastRouterProfileId": "f", "reviewRouterProfileId": "r", "defaultRoutingMode": "review"},
             ("r", "f"), ("f", "r")),
            ({"fastRouterProfileId": "same", "reviewRouterProfileId": "same"}, ("same",), ("same", "same")),
            ({"fastRouterProfileId": "same", "reviewRouterProfileId": "same", "defaultRoutingMode": "review"},
             ("same",), ("same", "same")),
        ]
        for entry, expected_ids, expected_slots in cases:
            with self.subTest(entry=entry):
                result = convert_legacy_router_settings(entry)
                self.assertEqual(result.settings.router_profile_ids, expected_ids)
                self.assertEqual(result.source_slots, expected_slots)
                self.assertEqual(result.settings.default_routing_mode, entry.get("defaultRoutingMode", "fast"))
                self.assertEqual(result.settings.router_retry_interval_seconds, 600)
                self.assertEqual(result.settings.routing_budget, "standard")

    def test_conversion_defaults_and_old_budget_are_explicit(self):
        self.assertEqual(convert_legacy_router_settings({}).settings, RouterSettings())
        for preset, converted in (("brief", "brief"), ("standard", "standard"), ("deep", "deep"), ("quick", "brief")):
            with self.subTest(preset=preset):
                self.assertEqual(convert_legacy_router_settings({"routingBudget": preset}).settings.routing_budget,
                                 converted)

    def test_conversion_is_pure_repeatable_and_keeps_unavailable_identity(self):
        # No connection or harness exists here: even an unavailable/unknown ID
        # stays in the list at its slot order, rather than being screened out.
        entry = {"defaultRoutingMode": "review", "reviewRouterProfileId": "unavailable-id",
                 "fastRouterProfileId": "healthy-id", "routingBudget": "quick"}
        before = deepcopy(entry)
        first = convert_legacy_router_settings(entry)
        self.assertEqual(first, convert_legacy_router_settings(entry))
        self.assertEqual(entry, before)
        self.assertEqual(first.settings.router_profile_ids, ("unavailable-id", "healthy-id"))
        self.assertEqual(first.settings.routing_budget, "brief")
        with self.assertRaises(FrozenInstanceError):
            first.settings.router_profile_ids = ("healthy-id",)
        with self.assertRaises(FrozenInstanceError):
            first.source_slots = (None, None)

    def test_duplicate_slots_remain_a_deterministic_audit_record(self):
        # Rewritten: the duplicate is dropped from the list (first occurrence wins)
        # while source_slots keeps both raw slot values for audit.
        result = convert_legacy_router_settings({"fastRouterProfileId": "same", "reviewRouterProfileId": "same"})
        self.assertEqual(result.settings.router_profile_ids, ("same",))
        self.assertEqual(result.source_slots, ("same", "same"))

    def test_invalid_legacy_data_is_not_guessed(self):
        for entry in (None, [], {"decision_profile_id": "old"}, {"routerProfileId": "new"},
                      {"routerProfileIds": []}, {"routerRetryIntervalSeconds": 600},
                      {"defaultRoutingMode": None}, {"defaultRoutingMode": "auto"}, {"routingBudget": None},
                      {"routingBudget": "unlimited"}, {"fastRouterProfileId": False},
                      {"reviewRouterProfileId": "bad/id"}, {"fastRouterProfileId": None, "reviewRouterProfileId": 5}):
            with self.subTest(entry=entry), self.assertRaises(BoardError):
                convert_legacy_router_settings(entry)


if __name__ == "__main__":
    unittest.main()
