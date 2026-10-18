"""A structured provider denial cools every model using that provider."""
from copy import deepcopy
from unittest.mock import patch

from hey_my_buddy.blackboard.evaluation.native_observations import persist
from hey_my_buddy.blackboard.routing.decision import DecisionCoordinator
from hey_my_buddy.blackboard.routing.quota_routing import cooling, provider_state
from support import BoardTestCase, FakeClock, FIXTURE_CATALOG


PROVIDER = "fixture-api"
BUDDY_IDS = [f"zcode:{PROVIDER}:{model}:low" for model in ("alpha", "beta")]
OTHER_PROVIDER_ID = "zcode:other-api:gamma:low"


class ProviderDenialRoutingTests(BoardTestCase):
    def setUp(self):
        super().setUp()
        catalog = deepcopy(FIXTURE_CATALOG)
        catalog["providers"] = [{"provider": provider, "harness": "zcode", "efforts": ["low"],
                                 "models": [{"id": model, "name": model} for model in models]}
                                for provider, models in ((PROVIDER, ("alpha", "beta")),
                                                         ("other-api", ("gamma",)))]
        self.catalog_fixture(catalog)
        self.clock = FakeClock("2026-01-01T00:00:00.000Z")
        self.board_ = self.board(clock=self.clock)
        self.board_.evaluation._clock = self.clock
        self.board_.call("model_catalog_refresh", {"requestId": "catalog"})
        self.board_.console_call("user_policy_publish", {"commandId": "enable",
            "buddySettings": [{"buddyId": item, "enabled": True}
                              for item in [*BUDDY_IDS, OTHER_PROVIDER_ID]],
            "configuration": {"defaultRoutingMode": "review", "routerBuddyIds": []}})
        self.assertEqual(self.candidate_ids(), [*BUDDY_IDS, OTHER_PROVIDER_ID])

    def record_failure(self, *, code="quota-exceeded", phase="before-model"):
        failure = {"code": code, "nativeCode": "1310", "source": "synthetic/zcode-denial",
                   "observedAt": self.clock(), "resetsAt": None,
                   "rejectionPhase": phase}
        with self.board_.store.db.write() as db:
            persist(db, {"attempt_id": "synthetic-denial", "adapter": "zcode",
                         "model_harness": "zcode", "model_provider": PROVIDER,
                         "started_at": self.clock()},
                    {"quotaFailure": failure}, status="failed", shutdown_confirmed=True,
                    now=self.clock())

    def candidate_ids(self):
        with self.board_.store.db.read() as db:
            rows, cooling_excluded = DecisionCoordinator._scan_candidates(
                db, [], coding_only=True, now=self.clock())
            return [row["buddy_id"] for row in rows]

    def test_denial_cools_both_models_and_preserves_other_provider(self):
        self.record_failure()
        with self.board_.store.db.read() as db:
            rows, excluded = DecisionCoordinator._scan_candidates(
                db, [], coding_only=True, now=self.clock())
            self.assertEqual([row["buddy_id"] for row in rows], [OTHER_PROVIDER_ID])
            self.assertEqual([item["buddyId"] for item in excluded], BUDDY_IDS)
            self.assertEqual({item["coolingUntil"] for item in excluded},
                             {"2026-01-01T00:30:00.000Z"})
            self.assertEqual(cooling(db, {"harness": "zcode", "provider": PROVIDER},
                                     now=self.clock())["consecutiveRejections"], 1)
            self.assertIsNone(cooling(db, {"harness": "zcode", "provider": "other-api"},
                                      now=self.clock()))
        with patch("hey_my_buddy.blackboard.routing.quota_routing.utc_now", self.clock):
            for operation in ("buddy_list", "console_snapshot"):
                views = {row["buddyId"]: row for row in self.board_.call(operation, {})["buddies"]}
                for buddy_id in BUDDY_IDS:
                    self.assertEqual(views[buddy_id]["providerCooling"]["coolingUntil"],
                                     "2026-01-01T00:30:00.000Z")
                self.assertIsNone(views[OTHER_PROVIDER_ID]["providerCooling"])
            self.clock.advance(1800)
            for operation in ("buddy_list", "console_snapshot"):
                views = {row["buddyId"]: row for row in self.board_.call(operation, {})["buddies"]}
                self.assertTrue(all(views[buddy_id]["providerCooling"] is None for buddy_id in BUDDY_IDS))

    def test_expiry_and_redetect_restore_all_models_without_single_use_claim(self):
        self.record_failure()
        self.clock.advance(1800)
        self.assertEqual(self.candidate_ids(), [*BUDDY_IDS, OTHER_PROVIDER_ID])
        self.assertEqual(self.candidate_ids(), [*BUDDY_IDS, OTHER_PROVIDER_ID])
        self.record_failure()
        self.assertEqual(self.candidate_ids(), [OTHER_PROVIDER_ID])
        reply = self.board_.call("quota_redetect", {"requestId": "redetect",
                          "harness": "zcode", "provider": PROVIDER})
        self.assertEqual({key: reply[key] for key in ("harness", "provider", "cleared")},
                         {"harness": "zcode", "provider": PROVIDER, "cleared": True})
        self.assertEqual(self.candidate_ids(), [*BUDDY_IDS, OTHER_PROVIDER_ID])
        self.assertEqual(self.candidate_ids(), [*BUDDY_IDS, OTHER_PROVIDER_ID])
        with self.board_.store.db.read() as db:
            self.assertEqual(provider_state(db, "zcode", PROVIDER)["consecutiveRejections"], 2)

    def test_non_quota_failure_does_not_cool_provider(self):
        self.record_failure(code="rate-limited")
        self.assertEqual(self.candidate_ids(), [*BUDDY_IDS, OTHER_PROVIDER_ID])
        with self.board_.store.db.read() as db:
            self.assertIsNone(provider_state(db, "zcode", PROVIDER))
