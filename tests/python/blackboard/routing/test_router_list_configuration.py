"""Router list settings backend: stored list, strict patches and publication.

Only private boards are used. The list save path requires published,
identity-complete buddies and nothing more: enabled, availability, health, quota and
mode eligibility stay resolver concerns.
"""
import json
from copy import deepcopy
import unittest

from support import BoardTestCase
from hey_my_buddy.blackboard.routing import router
from hey_my_buddy.blackboard.store.db import canonical_json
from hey_my_buddy.errors import BoardError

A = "dsh:fixture:alpha:max"
B = "dsh:fixture:beta:max"
UNKNOWN = "dsh:fixture:ghost:max"


class RouterListConfigurationTests(BoardTestCase):
    def setUp(self):
        super().setUp()
        self.board_ = self.board()
        self.session = "registered-user-session"
        self.counter = 0
        self.board_.service.register_console_authority(self.session)
        with self.board_.store.db.write() as db:
            for buddy_id in (A, B):
                db.execute("INSERT INTO buddies(buddy_id,label,harness,provider,model,effort,"
                           "available,enabled,capabilities_json)"
                           " VALUES(?,?,?,?,?,?,1,1,?)",
                           (buddy_id, buddy_id, "dsh", "fixture", buddy_id, "max",
                            canonical_json([])))

    def stored(self):
        with self.board_.store.db.read() as db:
            return {
                "settings": tuple(db.execute("SELECT default_mode,budget_preset,retry_interval_seconds FROM router_settings WHERE id=1").fetchone()),
                "buddies": [tuple(row) for row in db.execute("SELECT position,buddy_id FROM router_buddies ORDER BY position")],
                "meta": {row[0]: row[1] for row in db.execute("SELECT key,value FROM meta")},
            }

    def user(self, operation, params):
        return self.board_.call(operation, {**params, "consoleAuthority": self.session})

    def publish(self, configuration=None, *, command=None, **changes):
        """One authenticated console publication."""
        self.counter += 1
        payload = {"commandId": command or f"publish-{self.counter}"}
        if configuration is not None:
            payload["configuration"] = configuration
        result = self.user("user_policy_publish", {**payload, **changes})
        return result

    def test_fresh_private_board_initializes_router_defaults(self):
        snapshot = self.board_.call("console_snapshot", {})
        self.assertEqual(snapshot["configuration"], {
            "routerBuddyIds": [], "routerRetryIntervalSeconds": 600,
            "defaultRoutingMode": "fast", "routingBudget": "standard",
            "routingBudgetLimits": {"preset": "standard", "timeoutSeconds": 300, "toolCalls": 24,
                                    "bytesRead": 524288},
        })
        self.assertIsNone(snapshot["configurationError"])
        stored = self.stored()
        self.assertEqual(stored["settings"], ("fast", "standard", 600))
        self.assertEqual(stored["buddies"], [])
        self.assertEqual(set(stored["meta"]), {"schema_version", "created_at", "capability_secret"})

    def test_publish_writes_list_settings_and_projection(self):
        result = self.publish({"routerBuddyIds": [A, B], "routerRetryIntervalSeconds": 30,
                               "defaultRoutingMode": "review", "routingBudget": "deep"})
        self.assertTrue(result["published"])
        snapshot = self.board_.call("console_snapshot", {})
        self.assertEqual(snapshot["configuration"]["routerBuddyIds"], [A, B])
        self.assertEqual(snapshot["configuration"]["routerRetryIntervalSeconds"], 30)
        self.assertEqual(snapshot["configuration"]["defaultRoutingMode"], "review")
        self.assertEqual(snapshot["configuration"]["routingBudget"], "deep")
        self.assertNotIn("revision", snapshot["configuration"])
        stored = self.stored()
        self.assertEqual(stored["buddies"], [(0, A), (1, B)])
        self.assertEqual(stored["settings"], ("review", "deep", 30))
        with self.board_.store.db.read() as db:
            events = db.execute("SELECT kind,payload_json FROM events WHERE kind='evaluation.routing_budget_changed'").fetchall()
        self.assertEqual(len(events), 1)
        self.assertEqual(json.loads(events[0]["payload_json"])["preset"], "deep")
        # Both configured buddies stay visible in the published buddy view.
        self.assertEqual({buddy["buddyId"] for buddy in snapshot["buddies"]}, {A, B})

    def test_strict_patch_semantics_over_the_publish_path(self):
        self.publish({"routerBuddyIds": [A, B], "routerRetryIntervalSeconds": 600})
        # Omitted fields preserve their stored values.
        self.publish({"routingBudget": "brief"})
        snapshot = self.board_.call("console_snapshot", {})
        self.assertEqual(snapshot["configuration"]["routerBuddyIds"], [A, B])
        self.assertEqual(snapshot["configuration"]["routingBudget"], "brief")
        # The explicit clear is an empty array.
        self.publish({"routerBuddyIds": []})
        self.assertEqual(self.board_.call("console_snapshot", {})["configuration"]["routerBuddyIds"], [])
        for value in ({"routerBuddyIds": None}, {"routerBuddyIds": [A, A]}, {"routerBuddyIds": [A, None]},
                      {"routerBuddyId": A}, {"fastRouterBuddyId": A}, {"reviewRouterBuddyId": B},
                      {"routerBuddyIds": "solo"}, {"routerRetryIntervalSeconds": True},
                      {"routerRetryIntervalSeconds": 0}, {"defaultRoutingMode": "auto"},
                      {"routingBudget": "quick"}, {"unknownField": 1}, {}, []):
            with self.subTest(value=value), self.assertRaises(BoardError) as caught:
                self.publish(value)
            self.assertEqual(caught.exception.code, "INVALID_ARGUMENT")
        # The rejected patches changed nothing.
        self.assertEqual(self.board_.call("console_snapshot", {})["configuration"]["routerBuddyIds"], [])

    def test_reorder_add_remove_and_scalar_settings_survive_real_save_and_read(self):
        self.publish({"routerBuddyIds": [A]})
        self.publish({"routerBuddyIds": [A, B]})
        self.assertEqual(self.board_.call("console_snapshot", {})["configuration"]["routerBuddyIds"], [A, B])
        self.publish({"routerBuddyIds": [B, A], "defaultRoutingMode": "review",
                      "routerRetryIntervalSeconds": 47})
        self.assertEqual(self.stored()["buddies"], [(0, B), (1, A)])
        self.publish({"routerBuddyIds": [B]})
        with self.board_.store.db.read() as db:
            self.assertEqual(router.configuration(db), {
                "routerBuddyIds": [B], "defaultRoutingMode": "review",
                "routingBudget": "standard", "routerRetryIntervalSeconds": 47})
        self.assertEqual(self.board_.call("console_snapshot", {})["configuration"]["routerBuddyIds"], [B])

    def test_list_items_need_published_complete_identity_and_nothing_more(self):
        with self.assertRaises(BoardError) as caught:
            self.publish({"routerBuddyIds": [UNKNOWN]})
        self.assertEqual(caught.exception.code, "NOT_FOUND")
        with self.board_.store.db.write() as db:
            db.execute("INSERT INTO buddies(buddy_id,label,harness,provider,model,effort,"
                       "available,enabled,capabilities_json)"
                       " VALUES('incomplete','incomplete','dsh','fixture','','',1,1,?)",
                       (canonical_json([]),))
        with self.assertRaises(BoardError) as caught:
            self.publish({"routerBuddyIds": ["incomplete"]})
        self.assertEqual(caught.exception.code, "INVALID_ARGUMENT")
        # Disabled, catalog-unavailable and mode-ineligible buddies may all be saved;
        # the resolver explains them per item later.
        with self.board_.store.db.write() as db:
            db.execute("INSERT INTO buddies(buddy_id,label,harness,provider,model,effort,"
                       "available,enabled,capabilities_json)"
                       " VALUES(?,?,?,?,?,?,?,1,?)",
                       ("disabled", "disabled", "dsh", "fixture", "disabled", "max", 1,
                        canonical_json([])))
            db.execute("UPDATE buddies SET enabled=0 WHERE buddy_id='disabled'")
            db.execute("INSERT INTO buddies(buddy_id,label,harness,provider,model,effort,"
                       "available,enabled,capabilities_json)"
                       " VALUES(?,?,?,?,?,?,0,1,?)",
                       ("offline", "offline", "dsh", "fixture", "offline", "max",
                        canonical_json([])))
            db.execute("INSERT INTO buddies(buddy_id,label,harness,provider,model,effort,"
                       "available,enabled,capabilities_json)"
                       " VALUES(?,?,?,?,?,?,1,1,?)",
                       ("reviewonly", "reviewonly", "zcode", "fixture", "reviewonly", "max",
                        canonical_json([])))
        self.publish({"routerBuddyIds": [A, "disabled", "offline", "reviewonly"], "defaultRoutingMode": "review"})
        self.assertEqual(self.board_.call("console_snapshot", {})["configuration"]["routerBuddyIds"],
                         [A, "disabled", "offline", "reviewonly"])

    def test_interval_and_budget_patches_do_not_require_availability(self):
        with self.board_.store.db.write() as db:
            db.execute("INSERT INTO buddies(buddy_id,label,harness,provider,model,effort,"
                       "available,enabled,capabilities_json)"
                       " VALUES(?,?,?,?,?,?,0,1,?)",
                       ("offline", "offline", "dsh", "fixture", "offline", "max",
                        canonical_json([])))
        self.publish({"routerBuddyIds": ["offline"]})
        self.publish({"routerRetryIntervalSeconds": 1, "routingBudget": "brief", "defaultRoutingMode": "review"})
        configuration = self.board_.call("console_snapshot", {})["configuration"]
        self.assertEqual(configuration["routerBuddyIds"], ["offline"])
        self.assertEqual(configuration["routerRetryIntervalSeconds"], 1)

    def test_configuration_reads_are_side_effect_free(self):
        self.publish({"routerBuddyIds": [A, B]})
        before = self.stored()
        for _ in range(3):
            with self.board_.store.db.read() as db:
                router.configuration(db)
                router.current_router(db, now="2026-01-01T01:00:00.000Z")
            self.board_.call("console_snapshot", {})
        self.assertEqual(self.stored(), before)

    def test_patch_input_dicts_are_never_mutated(self):
        entry = {"routerBuddyIds": [A], "routerRetryIntervalSeconds": 30}
        before = deepcopy(entry)
        self.publish(entry)
        self.assertEqual(entry, before)
        from hey_my_buddy.blackboard.routing.router_settings import validate_router_settings_patch
        result = validate_router_settings_patch(entry)
        entry["routerBuddyIds"].append("changed:after:the:call")
        self.assertEqual(result["routerBuddyIds"], [A])


if __name__ == "__main__":
    unittest.main()
