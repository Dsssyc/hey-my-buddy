"""Router list settings backend: version-3 storage, strict patches, explicit upgrade.

Only private boards are used. Version-2 settings and the old single slots must
surface ``router-settings-upgrade-required`` from reads, startup initialization and
publications without ever running the pure conversion or migrating the old keys;
unrelated user fields stay publishable. The list save path requires published,
identity-complete buddies and nothing more: enabled, availability, health, quota and
mode eligibility stay resolver concerns.
"""
import json
from copy import deepcopy
import unittest
from unittest import mock

from support import BoardTestCase
from hey_my_buddy.blackboard.routing import router, router_settings
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
            for profile_id in (A, B):
                db.execute("INSERT INTO evaluation_profiles(profile_id,label,adapter,provider,model,effort,"
                           "available,enabled,capabilities_json,created_revision,updated_revision)"
                           " VALUES(?,?,?,?,?,?,1,1,'[]',0,0)",
                           (profile_id, profile_id, "dsh", "fixture", profile_id, "max"))

    def meta(self):
        with self.board_.store.db.read() as db:
            return {row[0]: row[1] for row in db.execute("SELECT key,value FROM meta")}

    def user(self, operation, params):
        return self.board_.call(operation, {**params, "consoleAuthority": self.session})

    def publish(self, configuration=None, *, command=None, **changes):
        """One authenticated console publication.

        A rejected publication leaves its writer grant active, so the same writer is
        reused until a publication succeeds; each attempt carries its own command id
        and the current table revision.
        """
        self.counter += 1
        revision = self.board_.call("console_snapshot", {})["tableRevision"]
        if getattr(self, "_writer", None) is None:
            self._writer = self.user("evaluation_write_begin", {"requestId": f"grant-{self.counter}",
                                                                "expectedRevision": revision, "kind": "human"})
        payload = {key: self._writer[key] for key in ("writerId", "generation", "writerToken")}
        payload |= {"expectedRevision": revision,
                    "commandId": command or f"publish-{self.counter}"}
        if configuration is not None:
            payload["configuration"] = configuration
        result = self.user("user_policy_publish", {**payload, **changes})
        if result.get("published"):
            self._writer = None
        return result

    def test_fresh_private_board_initializes_version_three_defaults(self):
        snapshot = self.board_.call("console_snapshot", {})
        self.assertEqual(snapshot["configuration"], {
            "revision": 0, "routerProfileIds": [], "routerRetryIntervalSeconds": 600,
            "defaultRoutingMode": "fast", "routingBudget": "standard",
            "routingBudgetLimits": {"preset": "standard", "timeoutSeconds": 300, "toolCalls": 24,
                                    "bytesRead": 524288},
        })
        self.assertIsNone(snapshot["configurationError"])
        meta = self.meta()
        self.assertEqual(meta["router_configuration_version"], "3")
        self.assertEqual(meta["router_profile_ids"], "[]")
        self.assertEqual(meta["router_retry_interval_seconds"], "600")
        self.assertEqual(meta["router_default_mode"], "fast")
        self.assertEqual(meta["router_budget_preset"], "standard")
        self.assertNotIn("router_profile_id", meta)

    def test_publish_writes_list_settings_and_projection(self):
        result = self.publish({"routerProfileIds": [A, B], "routerRetryIntervalSeconds": 30,
                               "defaultRoutingMode": "review", "routingBudget": "deep"})
        self.assertGreater(result["configurationRevision"], 0)
        snapshot = self.board_.call("console_snapshot", {})
        self.assertEqual(snapshot["configuration"]["routerProfileIds"], [A, B])
        self.assertEqual(snapshot["configuration"]["routerRetryIntervalSeconds"], 30)
        self.assertEqual(snapshot["configuration"]["defaultRoutingMode"], "review")
        self.assertEqual(snapshot["configuration"]["routingBudget"], "deep")
        self.assertEqual(snapshot["configuration"]["revision"], result["configurationRevision"])
        meta = self.meta()
        self.assertEqual(json.loads(meta["router_profile_ids"]), [A, B])
        self.assertEqual(meta["router_retry_interval_seconds"], "30")
        self.assertEqual(meta["router_default_mode"], "review")
        self.assertEqual(meta["router_budget_preset"], "deep")
        with self.board_.store.db.read() as db:
            events = db.execute("SELECT kind,payload_json FROM events WHERE kind='evaluation.routing_budget_changed'").fetchall()
        self.assertEqual(len(events), 1)
        self.assertEqual(json.loads(events[0]["payload_json"])["preset"], "deep")
        # Both configured buddies stay visible in the published profile view.
        self.assertEqual({profile["profileId"] for profile in snapshot["profiles"]}, {A, B})

    def test_strict_patch_semantics_over_the_publish_path(self):
        self.publish({"routerProfileIds": [A, B], "routerRetryIntervalSeconds": 600})
        # Omitted fields preserve their stored values.
        self.publish({"routingBudget": "brief"})
        snapshot = self.board_.call("console_snapshot", {})
        self.assertEqual(snapshot["configuration"]["routerProfileIds"], [A, B])
        self.assertEqual(snapshot["configuration"]["routingBudget"], "brief")
        # The explicit clear is an empty array.
        self.publish({"routerProfileIds": []})
        self.assertEqual(self.board_.call("console_snapshot", {})["configuration"]["routerProfileIds"], [])
        for value in ({"routerProfileIds": None}, {"routerProfileIds": [A, A]}, {"routerProfileIds": [A, None]},
                      {"routerProfileId": A}, {"fastRouterProfileId": A}, {"reviewRouterProfileId": B},
                      {"routerProfileIds": "solo"}, {"routerRetryIntervalSeconds": True},
                      {"routerRetryIntervalSeconds": 0}, {"defaultRoutingMode": "auto"},
                      {"routingBudget": "quick"}, {"unknownField": 1}, {}, []):
            with self.subTest(value=value), self.assertRaises(BoardError) as caught:
                self.publish(value)
            self.assertEqual(caught.exception.code, "INVALID_ARGUMENT")
        # The rejected patches changed nothing.
        self.assertEqual(self.board_.call("console_snapshot", {})["configuration"]["routerProfileIds"], [])

    def test_list_items_need_published_complete_identity_and_nothing_more(self):
        with self.assertRaises(BoardError) as caught:
            self.publish({"routerProfileIds": [UNKNOWN]})
        self.assertEqual(caught.exception.code, "NOT_FOUND")
        with self.board_.store.db.write() as db:
            db.execute("INSERT INTO evaluation_profiles(profile_id,label,adapter,provider,model,effort,"
                       "available,enabled,capabilities_json,created_revision,updated_revision)"
                       " VALUES('incomplete','incomplete','dsh','fixture','','',1,1,'[]',0,0)")
        with self.assertRaises(BoardError) as caught:
            self.publish({"routerProfileIds": ["incomplete"]})
        self.assertEqual(caught.exception.code, "INVALID_ARGUMENT")
        # Disabled, catalog-unavailable and mode-ineligible buddies may all be saved;
        # the resolver explains them per item later.
        with self.board_.store.db.write() as db:
            db.execute("INSERT INTO evaluation_profiles(profile_id,label,adapter,provider,model,effort,"
                       "available,enabled,capabilities_json,created_revision,updated_revision)"
                       " VALUES(?,?,?,?,?,?,?,1,'[]',0,0)",
                       ("disabled", "disabled", "dsh", "fixture", "disabled", "max", 1))
            db.execute("UPDATE evaluation_profiles SET enabled=0 WHERE profile_id='disabled'")
            db.execute("INSERT INTO evaluation_profiles(profile_id,label,adapter,provider,model,effort,"
                       "available,enabled,capabilities_json,created_revision,updated_revision)"
                       " VALUES(?,?,?,?,?,?,0,1,'[]',0,0)",
                       ("offline", "offline", "dsh", "fixture", "offline", "max"))
            db.execute("INSERT INTO evaluation_profiles(profile_id,label,adapter,provider,model,effort,"
                       "available,enabled,capabilities_json,created_revision,updated_revision)"
                       " VALUES(?,?,?,?,?,?,1,1,'[]',0,0)",
                       ("reviewonly", "reviewonly", "zcode", "fixture", "reviewonly", "max"))
        self.publish({"routerProfileIds": [A, "disabled", "offline", "reviewonly"], "defaultRoutingMode": "review"})
        self.assertEqual(self.board_.call("console_snapshot", {})["configuration"]["routerProfileIds"],
                         [A, "disabled", "offline", "reviewonly"])

    def test_interval_and_budget_patches_do_not_require_availability(self):
        with self.board_.store.db.write() as db:
            db.execute("INSERT INTO evaluation_profiles(profile_id,label,adapter,provider,model,effort,"
                       "available,enabled,capabilities_json,created_revision,updated_revision)"
                       " VALUES(?,?,?,?,?,?,0,1,'[]',0,0)",
                       ("offline", "offline", "dsh", "fixture", "offline", "max"))
        self.publish({"routerProfileIds": ["offline"]})
        self.publish({"routerRetryIntervalSeconds": 1, "routingBudget": "brief", "defaultRoutingMode": "review"})
        configuration = self.board_.call("console_snapshot", {})["configuration"]
        self.assertEqual(configuration["routerProfileIds"], ["offline"])
        self.assertEqual(configuration["routerRetryIntervalSeconds"], 1)

    def version_two_board(self):
        with self.board_.store.db.write() as db:
            db.execute("INSERT INTO meta(key,value) VALUES('router_configuration_version','2')"
                       " ON CONFLICT(key) DO UPDATE SET value=excluded.value")
            db.execute("INSERT INTO meta(key,value) VALUES('router_profile_id','old-slot')"
                       " ON CONFLICT(key) DO UPDATE SET value=excluded.value")

    def test_version_two_reads_report_upgrade_required_and_never_migrate(self):
        self.version_two_board()
        before = self.meta()
        tripwire = mock.patch.object(router_settings, "convert_legacy_router_settings",
                                     side_effect=AssertionError("pure conversion ran on a read path"))
        with tripwire:
            with self.assertRaises(BoardError) as caught:
                with self.board_.store.db.read() as db:
                    router.configuration(db)
            self.assertEqual(caught.exception.code, "router-settings-upgrade-required")
            with self.assertRaises(BoardError) as caught:
                with self.board_.store.db.write() as db:
                    router.initialize_configuration(db)
            self.assertEqual(caught.exception.code, "router-settings-upgrade-required")
            snapshot = self.board_.call("console_snapshot", {})
        self.assertIsNone(snapshot["configuration"])
        self.assertEqual(snapshot["configurationError"]["code"], "router-settings-upgrade-required")
        self.assertEqual(self.meta(), before)

    def test_version_two_publication_rejects_configuration_and_keeps_unrelated_fields(self):
        self.version_two_board()
        before = self.meta()
        tripwire = unittest.mock.patch.object(router_settings, "convert_legacy_router_settings",
                                              side_effect=AssertionError("pure conversion ran on a publish path"))
        with tripwire, self.assertRaises(BoardError) as caught:
            self.publish({"routerRetryIntervalSeconds": 30})
        self.assertEqual(caught.exception.code, "router-settings-upgrade-required")
        self.assertEqual(self.meta(), before)
        # An unrelated user patch still publishes.
        result = self.publish(None, familyAnnotationChanges=[{"adapter": "dsh", "provider": "fixture",
                                                              "model": A, "text": "user note"}])
        self.assertTrue(result["published"])

    def test_configuration_reads_are_side_effect_free(self):
        self.publish({"routerProfileIds": [A, B]})
        before = self.meta()
        for _ in range(3):
            with self.board_.store.db.read() as db:
                router.configuration(db)
                router.current_router(db, now="2026-01-01T01:00:00.000Z")
            self.board_.call("console_snapshot", {})
        self.assertEqual(self.meta(), before)

    def test_patch_input_dicts_are_never_mutated(self):
        entry = {"routerProfileIds": [A], "routerRetryIntervalSeconds": 30}
        before = deepcopy(entry)
        self.publish(entry)
        self.assertEqual(entry, before)
        from hey_my_buddy.blackboard.routing.router_settings import validate_router_settings_patch
        result = validate_router_settings_patch(entry)
        entry["routerProfileIds"].append("changed:after:the:call")
        self.assertEqual(result["routerProfileIds"], [A])


if __name__ == "__main__":
    unittest.main()
