"""Router list setting choices are independent of native availability and state."""
from copy import deepcopy
from dataclasses import FrozenInstanceError
import unittest

from hey_my_buddy.errors import BoardError
from hey_my_buddy.blackboard.routing.router_settings import RouterSettings, validate_router_settings_patch

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









if __name__ == "__main__":
    unittest.main()
