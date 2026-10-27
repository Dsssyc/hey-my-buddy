"""Router list setting choices are independent of native availability and state."""
from copy import deepcopy
import unittest

from hey_my_buddy.errors import BoardError
from hey_my_buddy.blackboard.routing.router_settings import DEFAULT_ROUTER_SETTINGS, validate_router_settings_patch

BUDDY = "codex:openai:example:high"
RETRY_MAX_SECONDS = 2147483647


class RouterSettingsTests(unittest.TestCase):
    def test_settings_defaults(self):
        self.assertEqual(DEFAULT_ROUTER_SETTINGS, {"routerBuddyIds": [], "routerRetryIntervalSeconds": 600,
                                                   "defaultRoutingMode": "fast", "routingBudget": "standard"})

    def test_patch_preserves_omissions_and_explicit_clear(self):
        self.assertEqual(validate_router_settings_patch({"routingBudget": "brief"}), {"routingBudget": "brief"})
        # The explicit clear is now an empty array; null is refused instead of clearing.
        self.assertEqual(validate_router_settings_patch({"routerBuddyIds": []}), {"routerBuddyIds": []})
        self.assertEqual(validate_router_settings_patch({"routerBuddyIds": [BUDDY]}),
                         {"routerBuddyIds": [BUDDY]})
        self.assertEqual(validate_router_settings_patch({"routerRetryIntervalSeconds": 30, "defaultRoutingMode": "review"}),
                         {"routerRetryIntervalSeconds": 30, "defaultRoutingMode": "review"})

    def test_patch_rejects_invalid_lists_and_values(self):
        for value in (None, [], {}, {"routerBuddyId": BUDDY}, {"fastRouterBuddyId": "old"},
                      {"reviewRouterBuddyId": None}, {"decisionBuddyId": "old"}, {"revision": 1},
                      {"routingBudgetLimits": {}}, {"routingBudget": "quick"}, {"routingBudget": None},
                      {"defaultRoutingMode": "auto"}, {"defaultRoutingMode": None},
                      {"routerBuddyIds": None}, {"routerBuddyIds": "solo"}, {"routerBuddyIds": (BUDDY,)},
                      {"routerBuddyIds": [BUDDY, None]}, {"routerBuddyIds": [1]},
                      {"routerBuddyIds": [" "]}, {"routerBuddyIds": ["bad/id"]},
                      {"routerBuddyIds": ["a" * 129]}, {"routerBuddyIds": [BUDDY, BUDDY]},
                      {"routerRetryIntervalSeconds": True}, {"routerRetryIntervalSeconds": 600.0},
                      {"routerRetryIntervalSeconds": "600"}, {"routerRetryIntervalSeconds": None},
                      {"routerRetryIntervalSeconds": 0}, {"routerRetryIntervalSeconds": -1},
                      {"routerRetryIntervalSeconds": RETRY_MAX_SECONDS + 1}):
            with self.subTest(value=value), self.assertRaises(BoardError) as failure:
                validate_router_settings_patch(value)
            self.assertEqual(failure.exception.code, "INVALID_ARGUMENT")
        for seconds in (1, RETRY_MAX_SECONDS):
            with self.subTest(seconds=seconds):
                self.assertEqual(validate_router_settings_patch({"routerRetryIntervalSeconds": seconds}),
                                 {"routerRetryIntervalSeconds": seconds})

    def test_patch_accepts_large_lists_without_item_limits(self):
        many = [f"provider:model-{index}:off" for index in range(200)]
        self.assertEqual(validate_router_settings_patch({"routerBuddyIds": many}), {"routerBuddyIds": many})

    def test_patch_validation_keeps_input_and_result_independent(self):
        ids = [BUDDY]
        entry = {"routerBuddyIds": ids, "routerRetryIntervalSeconds": 30, "routingBudget": "deep"}
        before = deepcopy(entry)
        result = validate_router_settings_patch(entry)
        self.assertEqual(entry, before)
        ids.append("changed:after:the:call")
        self.assertEqual(result["routerBuddyIds"], [BUDDY])









if __name__ == "__main__":
    unittest.main()
