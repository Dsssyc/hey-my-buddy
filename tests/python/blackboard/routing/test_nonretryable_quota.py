"""Provider denial reaches the harness/provider quota route on private boards."""
from copy import deepcopy
from unittest.mock import patch

from hey_my_buddy.blackboard.evaluation.native_observations import exhausted, latest_quota, persist
from hey_my_buddy.blackboard.routing.decision import DecisionCoordinator
from hey_my_buddy.blackboard.routing.quota_routing import RETRY_WAIT_SECONDS, routing_facts
from hey_my_buddy.buddy.harnesses.zcode.protocol import quota_failure
from support import BoardTestCase, FakeClock, FIXTURE_CATALOG
from buddy.harnesses.test_quota_failures import PROVIDER_RATE_LIMIT_FAILURE


PROVIDER = "fixture-api"
BUDDY_IDS = [f"zcode:{PROVIDER}:{model}:low" for model in ("alpha", "beta")]
OTHER_PROVIDER_ID = "zcode:other-api:gamma:low"
CONFIG = {"harness": "zcode", "provider": PROVIDER, "model": "alpha", "effort": "low"}


class NonretryableQuotaRoutingTests(BoardTestCase):
    def setUp(self):
        super().setUp()
        catalog = deepcopy(FIXTURE_CATALOG)
        catalog["providers"] = [{"provider": provider, "harness": "zcode", "efforts": ["low"],
                                "models": [{"id": model, "name": model} for model in models]}
                               for provider, models in ((PROVIDER, ("alpha", "beta")), ("other-api", ("gamma",)))]
        self.catalog_fixture(catalog)
        self.clock = FakeClock()
        self.enterContext(patch("hey_my_buddy.blackboard.routing.quota_routing.utc_now", self.clock))
        self.board_ = self.board(clock=self.clock)
        self.board_.evaluation._clock = self.clock
        self.board_.call("model_catalog_refresh", {"requestId": "catalog"})
        self.board_.console_call("user_policy_publish", {"commandId": "enable",
                        "buddySettings": [{"buddyId": item, "enabled": True} for item in [*BUDDY_IDS, OTHER_PROVIDER_ID]],
            "configuration": {"defaultRoutingMode": "review", "routerBuddyIds": []}})
        self.assertEqual(self.candidates()[0], [*BUDDY_IDS, OTHER_PROVIDER_ID])

    def record_failure(self, raw=None):
        failure = quota_failure(PROVIDER_RATE_LIMIT_FAILURE if raw is None else raw)
        with self.board_.store.db.write() as db:
            persist(db, {"attempt_id": "native-denial", "adapter": "zcode", "model_harness": "zcode",
                         "model_provider": PROVIDER},
                    {"quotaFailure": {**failure, "source": "zcode/session-turn-failed", "observedAt": self.clock()}})

    def candidates(self, decision_id=None):
        with self.board_.store.db.write() as db:
            rows, retries = DecisionCoordinator._scan_candidates(db, [], coding_only=True,
                decision_id=decision_id, consume_retries=True, now=self.clock())
            return [row["buddy_id"] for row in rows], retries

    def test_failure_blocks_both_buddies_and_projects_exhaustion_without_reset(self):
        self.record_failure()
        self.assertEqual(self.candidates(), ([OTHER_PROVIDER_ID], []))
        with self.board_.store.db.read() as db:
            quota = latest_quota(db, "zcode")
            self.assertIs(quota["ordinaryUsageAllowed"], False)
            self.assertEqual(quota["reachedType"], "1310")
            self.assertEqual(quota["windows"], [])
            self.assertNotIn("resetsAt", quota)
            facts = routing_facts(db, "zcode", now=self.clock())
            self.assertEqual(len(facts), 1)
            self.assertTrue(facts[0]["blocked"])
            self.assertIsNone(facts[0]["resetsAt"])
            self.assertFalse(facts[0]["retry"]["open"])
        for operation in ("buddy_list", "console_snapshot"):
            rows = self.board_.call(operation, {})["buddies"]
            views = {row["buddyId"]: row for row in rows}
            for buddy_id in BUDDY_IDS:
                self.assertTrue(views[buddy_id]["quotaExhausted"])
                self.assertFalse(views[buddy_id]["quotaRetry"]["open"])
            self.assertFalse(views[OTHER_PROVIDER_ID]["quotaExhausted"])

    def test_no_reset_failure_opens_one_natural_retry_per_wait_interval(self):
        self.record_failure()
        self.clock.advance(RETRY_WAIT_SECONDS - 1)
        self.assertEqual(self.candidates("too-early"), ([OTHER_PROVIDER_ID], []))
        self.clock.advance(1)
        self.assertEqual(self.candidates("first-retry"), ([*BUDDY_IDS, OTHER_PROVIDER_ID], BUDDY_IDS))
        self.assertEqual(self.candidates("second-retry"), ([OTHER_PROVIDER_ID], []))
        self.clock.advance(RETRY_WAIT_SECONDS)
        self.assertEqual(self.candidates("later-retry"), ([*BUDDY_IDS, OTHER_PROVIDER_ID], BUDDY_IDS))
        with self.board_.store.db.read() as db:
            self.assertIsNotNone(exhausted(db, CONFIG, now=self.clock()))

    def test_redetect_opens_one_early_retry_and_preserves_exhaustion(self):
        self.record_failure()
        reply = self.board_.call("quota_redetect", {"requestId": "redetect", "harness": "zcode", "provider": PROVIDER})
        self.assertEqual(reply["quota"]["opened"], [None])
        self.assertEqual(self.candidates("manual-retry"), ([*BUDDY_IDS, OTHER_PROVIDER_ID], BUDDY_IDS))
        self.assertEqual(self.candidates("next-retry"), ([OTHER_PROVIDER_ID], []))
        with self.board_.store.db.read() as db:
            self.assertIsNotNone(exhausted(db, CONFIG, now=self.clock()))

    def test_other_provider_keeps_its_candidates(self):
        self.record_failure()
        self.assertEqual(self.candidates(), ([OTHER_PROVIDER_ID], []))
        with self.board_.store.db.read() as db:
            self.assertIsNone(exhausted(db, {**CONFIG, "provider": "other-api"}, now=self.clock()))
            self.assertIsNotNone(exhausted(db, CONFIG, now=self.clock()))
            facts = routing_facts(db, "zcode", now=self.clock())
            self.assertEqual([item["provider"] for item in facts], [PROVIDER])

    def test_retryable_or_unspecified_rate_limits_do_not_create_blocking_observations(self):
        for retryable in (True, None):
            with self.subTest(retryable=retryable):
                raw = deepcopy(PROVIDER_RATE_LIMIT_FAILURE)
                if retryable is None:
                    del raw["attribution"]["retryable"]
                else:
                    raw["attribution"]["retryable"] = retryable
                self.record_failure(raw)
                self.assertEqual(self.candidates(), ([*BUDDY_IDS, OTHER_PROVIDER_ID], []))
                with self.board_.store.db.read() as db:
                    self.assertIsNone(latest_quota(db, "zcode"))
                    self.assertEqual(routing_facts(db, "zcode", now=self.clock()), [])
