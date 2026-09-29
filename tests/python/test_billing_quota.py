"""Native billing labels and quota routing using private boards and mock CLIs."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
from unittest.mock import patch

from buddy.billing import claude_status, codex_account, zcode_access
from buddy.decision import DecisionCoordinator
from buddy.harness_health import HarnessHealth
from buddy.native_observations import exhausted, warnings
from buddy import router
from support import BoardTestCase
from test_decision import DecisionTestCase, PROFILE_ID, SECOND_PROFILE_ID


def stamp(offset=0):
    return (datetime.now(timezone.utc) + timedelta(seconds=offset)).isoformat().replace("+00:00", "Z")


class BillingQuotaTests(BoardTestCase):
    seed = DecisionTestCase.seed

    def setUp(self):
        super().setUp()
        self.catalog_fixture()

    def _quota(self, board, **changes):
        value = {"source": "native-fixture", "observedAt": stamp(), "provider": "deepseek-official",
                 "windows": [], **changes}
        with board.store.db.write() as db:
            db.execute("UPDATE harness_health SET quota_json=? WHERE adapter='dsh'", (json.dumps(value),))

    def _git_workdir(self):
        path = self.workdir()
        for args in (("init", "-q"), ("config", "user.name", "Fixture"),
                     ("config", "user.email", "fixture@example.invalid")):
            subprocess.run(["git", *args], cwd=path, check=True, capture_output=True)
        (path / "README.md").write_text("fixture\n")
        subprocess.run(["git", "add", "README.md"], cwd=path, check=True, capture_output=True)
        subprocess.run(["git", "commit", "-qm", "fixture"], cwd=path, check=True, capture_output=True)
        return path

    def test_native_labels_are_conservative_and_do_not_read_credentials(self):
        self.assertEqual(codex_account({"type": "chatgpt", "planType": "pro"}, stamp())["kind"], "subscription")
        self.assertEqual(codex_account({"type": "chatgpt", "planType": "free"}, stamp())["kind"], "unknown")
        self.assertEqual(codex_account({"type": "apiKey"}, stamp())["kind"], "metered")
        self.assertEqual(codex_account({"type": "other"}, stamp())["kind"], "unknown")
        self.assertEqual(claude_status({"loggedIn": True, "apiProvider": "firstParty",
                                       "authMethod": "claude.ai", "subscriptionType": "pro"}, stamp())["kind"], "subscription")
        self.assertEqual(claude_status({"loggedIn": True, "apiProvider": "firstParty",
                                       "authMethod": "api_key"}, stamp())["kind"], "metered")
        self.assertEqual(claude_status({"loggedIn": True, "apiProvider": "firstParty"}, stamp())["kind"], "unknown")
        self.assertEqual(zcode_access("zhipu-coding-plan-api-key", stamp())["kind"], "subscription")
        self.assertEqual(zcode_access("api-key", stamp())["kind"], "metered")
        self.assertEqual(zcode_access("oauth", stamp())["kind"], "unknown")

    def test_catalog_access_types_project_billing_per_provider(self):
        from copy import deepcopy
        from support import FIXTURE_CATALOG
        board = self.board()
        board.call("model_catalog_refresh", {"requestId": "dsh-billing"})
        with board.store.db.read() as db:
            from buddy.billing import for_provider
            self.assertEqual(for_provider(db, "dsh", "deepseek-official")["kind"], "metered")
        native = deepcopy(FIXTURE_CATALOG)
        provider = native["providers"][0]
        provider.update(adapter="zcode", provider="zhipu", accessType="zhipu-coding-plan-api-key")
        native["discoveries"] = [{"adapter": "zcode", "status": "complete"}]
        self.catalog_fixture(native)
        board.call("model_catalog_refresh", {"requestId": "zcode-billing"})
        with board.store.db.read() as db:
            from buddy.billing import for_provider
            self.assertEqual(for_provider(db, "zcode", "zhipu")["kind"], "subscription")
            self.assertEqual(for_provider(db, "zcode", "other")["kind"], "unknown")

    def test_mock_codex_account_read_has_no_model_turn(self):
        from buddy.codex_account_probe import read
        path = Path(__file__).parent / "fixtures/mock_codex.py"
        billing, quota = read([sys.executable, str(path)], {"HOME": str(self.directory), "PATH": os.environ.get("PATH", "")})
        self.assertEqual(billing["kind"], "subscription")
        self.assertEqual(quota["scope"]["provider"], "openai")
        self.assertEqual(quota["windows"][0]["usedPercent"], 42.5)
        self.assertNotIn("balanceZero", quota)  # A subscription credit balance of zero proves no exhaustion.

    def test_codex_on_demand_read_is_throttled_even_for_forced_refresh(self):
        from buddy.store import BoardStore
        board = BoardStore(self.directory / "health")
        board.initialize()
        health = HarnessHealth(board)
        health.initialize()
        with board.db.write() as db:
            db.execute("UPDATE harness_health SET record_json=? WHERE adapter='codex'",
                       (json.dumps({"reviewVerification": {"verified": True}}),))
        record = {"status": "ready", "command": ["mock-codex"], "fingerprint": {"id": 1}}
        with patch("buddy.harness_health._snapshot", return_value={"fixture": 1}), \
             patch("buddy.harness_health._discover", return_value=record), \
             patch("buddy.codex_account_probe.read", return_value=(codex_account({"type": "chatgpt", "planType": "pro"}, stamp()), None)) as probe:
            health.refresh("codex", force=True)
            health.refresh("codex", force=True)
            self.assertEqual(probe.call_count, 1)
            self.assertEqual(health.get("codex")["billingByProvider"]["openai"]["kind"], "subscription")
            self.assertFalse(health.get("codex")["reviewVerification"]['verified'])
        health.close()

    def test_only_fresh_matching_explicit_exhaustion_filters_candidates_and_router(self):
        board = self.board()
        self.seed(board, decision_profile=None)
        with board.store.db.read() as db:
            baseline = DecisionCoordinator._select_candidates(db, [], coding_only=True)
            self.assertEqual({row["profile_id"] for row in baseline}, {PROFILE_ID, SECOND_PROFILE_ID})
        self._quota(board, windows=[{"name": "hour", "usedPercent": 90, "resetsAt": stamp(3600)}])
        with board.store.db.read() as db:
            self.assertEqual(len(DecisionCoordinator._select_candidates(db, [], coding_only=True)), 2)
        self._quota(board, windows=[{'name': 'hour', 'usedPercent': 100, 'resetsAt': stamp(3600)}])
        with board.store.db.read() as db:
            self.assertEqual(len(DecisionCoordinator._select_candidates(db, [], coding_only=True)), 2)

        self._quota(board, reachedType="rate_limit_reached")
        with board.store.db.read() as db:
            self.assertEqual(len(DecisionCoordinator._select_candidates(db, [], coding_only=True)), 2)
            self.assertEqual(warnings(db, {"adapter": "dsh", "provider": "deepseek-official"})[0]["code"], "HARNESS_RATE_LIMIT_REPORTED")
        self._quota(board, reachedType="usage_limit_reached", scope={"provider": "deepseek-official", "limitId": "other-model"})
        with board.store.db.read() as db:
            self.assertEqual(len(DecisionCoordinator._select_candidates(db, [], coding_only=True)), 2)
        self._quota(board, reachedType="usage_limit_reached", observedAt=stamp(300))
        with board.store.db.read() as db:
            self.assertEqual(len(DecisionCoordinator._select_candidates(db, [], coding_only=True)), 2)
        self._quota(board, reachedType="usage_limit_reached", windows=[{"name": "hour", "usedPercent": 100, "resetsAt": "not-a-time"}])
        with board.store.db.read() as db:
            self.assertEqual(len(DecisionCoordinator._select_candidates(db, [], coding_only=True)), 0)
        self._quota(board, reachedType="usage_limit_reached")
        with board.store.db.read() as db:
            self.assertEqual(DecisionCoordinator._select_candidates(db, [], coding_only=True), [])
            self.assertEqual(router.profile_problem(db, PROFILE_ID, "fast")[1], "router-quota-exhausted")
            self.assertEqual(exhausted(db, {"adapter": "dsh", "provider": "other"}), None)
            self.assertEqual(warnings(db, {"adapter": "dsh", "provider": "deepseek-official"})[0]["code"], "HARNESS_QUOTA_EXHAUSTED")
        self._quota(board, reachedType="usage_limit_reached", observedAt=stamp(-60), windows=[{"name": "hour", "usedPercent": 100, "resetsAt": stamp(-1)}])
        with board.store.db.read() as db:
            self.assertEqual(len(DecisionCoordinator._select_candidates(db, [], coding_only=True)), 2)
        self._quota(board, ordinaryUsageAllowed=True)
        with board.store.db.read() as db:
            self.assertEqual(len(DecisionCoordinator._select_candidates(db, [], coding_only=True)), 2)

    def test_native_zero_balance_does_not_depend_on_billing_label(self):
        board = self.board()
        board.call("model_catalog_refresh", {"requestId": "billing-balance-catalog"})
        self._quota(board, balanceZero=True)
        with board.store.db.read() as db:
            self.assertEqual(exhausted(db, {"adapter": "dsh", "provider": "deepseek-official"})["code"], "HARNESS_BALANCE_ZERO")
        with board.store.db.write() as db:
            db.execute("UPDATE harness_health SET record_json=?, quota_json=? WHERE adapter='codex'",
                       (json.dumps({"billingByProvider": {"openai": codex_account({"type": "chatgpt", "planType": "pro"}, stamp())}}),
                        json.dumps({"source": "codex/app-server-rate-limits", "observedAt": stamp(),
                                    "provider": "openai", "balanceZero": True, "windows": []})))
        with board.store.db.read() as db:
            self.assertEqual(exhausted(db, {"adapter": "codex", "provider": "openai"})['code'], 'HARNESS_BALANCE_ZERO')
        with board.store.db.write() as db:
            db.execute("UPDATE harness_health SET quota_json=? WHERE adapter='codex'",
                       (json.dumps({"source": "codex/app-server-rate-limits", "observedAt": stamp(),
                                    "provider": "openai", "balanceZero": True,
                                    "ordinaryUsageAllowed": False, "windows": []}),))
        with board.store.db.read() as db:
            self.assertEqual(exhausted(db, {"adapter": "codex", "provider": "openai"})["code"], "HARNESS_BALANCE_ZERO")

    def test_pin_cannot_force_exhausted_candidate_but_host_explicit_choice_survives(self):
        board = self.board()
        self.seed(board, decision_profile=None, preferences=[{"profileId": PROFILE_ID, "mode": "pin"}])
        self._quota(board, reachedType="usage_limit_reached")
        with board.store.db.read() as db:
            self.assertEqual(DecisionCoordinator._select_candidates(db, [], coding_only=True), [])
        result = board.call("workflow_submit", {"requestId": "explicit-exhausted", "hostId": "host",
            "task": "Finish the bounded change", "cwd": str(self._git_workdir()),
            "executionWorkspace": {"kind": "existing", "access": "write"},
            "adapter": "dsh", "provider": "deepseek-official", "model": "deepseek-flash", "effort": "off"})
        self.assertEqual(result["executionConfiguration"]["model"], "deepseek-flash")
        self.assertEqual(result["quotaWarnings"][0]["code"], "HARNESS_QUOTA_EXHAUSTED")

    def test_exhaustion_outlives_display_freshness_and_unknown_cannot_clear_it(self):
        from buddy.quota_routing import record
        board = self.board()
        config = {'adapter': 'dsh', 'provider': 'deepseek-official', 'model': 'deepseek-flash'}
        with board.store.db.write() as db:
            record(db, 'dsh', {'provider': config['provider'], 'source': 'native-fixture', 'observedAt': stamp(-7200), 'reachedType': 'usage_limit_reached'})
            record(db, 'dsh', {'provider': config['provider'], 'source': 'native-fixture', 'observedAt': stamp(-3600), 'windows': []})
            self.assertIsNotNone(exhausted(db, config))
            record(db, 'dsh', {'provider': config['provider'], 'source': 'native-fixture', 'observedAt': stamp(-1), 'ordinaryUsageAllowed': True})
            self.assertIsNone(exhausted(db, config))

    def test_reset_and_provider_or_limit_scope_are_independent(self):
        from buddy.quota_routing import record
        board = self.board()
        config = {'adapter': 'codex', 'provider': 'openai', 'model': 'limited-model'}
        with board.store.db.write() as db:
            record(db, 'codex', {'provider': 'openai', 'source': 'native-fixture', 'observedAt': stamp(-60),
                'reachedType': 'usage_limit_reached', 'scope': {'limitId': 'limited-model'}, 'resetsAt': stamp(60)})
            self.assertIsNotNone(exhausted(db, config))
            self.assertIsNone(exhausted(db, {**config, 'model': 'other-model'}))
            self.assertIsNone(exhausted(db, {**config, 'provider': 'other-provider'}))
            self.assertIsNone(exhausted(db, config, now=stamp(120)))

    def test_account_reset_query_can_restore_a_previous_native_failure(self):
        from buddy.quota_routing import record
        board = self.board()
        config = {'adapter': 'codex', 'provider': 'openai', 'model': 'gpt-6-sol'}
        with board.store.db.write() as db:
            record(db, 'codex', {'provider': 'openai', 'source': 'native-fixture', 'observedAt': stamp(-60), 'reachedType': 'usage_limit_reached'})
            record(db, 'codex', {'provider': 'openai', 'source': 'native-fixture', 'observedAt': stamp(-1),
                'scope': {'limitId': 'codex'}, 'windows': [{'name': 'primary', 'usedPercent': 42, 'resetsAt': stamp(3600)}]})
            self.assertIsNone(exhausted(db, config))

    def test_multiple_native_pools_restore_only_when_every_reported_pool_is_available(self):
        from buddy.quota_routing import record
        from buddy.adapters.codex_protocol import quota_candidate_from_response
        from buddy.usage import normalize_quota
        board = self.board()
        config = {'adapter': 'codex', 'provider': 'openai', 'model': 'gpt-6-sol'}
        with board.store.db.write() as db:
            record(db, 'codex', {'provider': 'openai', 'source': 'native-fixture', 'observedAt': stamp(-60), 'reachedType': 'usage_limit_reached'})
            record(db, 'codex', {'provider': 'openai', 'source': 'native-fixture', 'observedAt': stamp(-60),
                'scope': {'limitId': 'gpt-6-sol'}, 'reachedType': 'usage_limit_reached'})
            response = {'rateLimitsByLimitId': {'codex': {'primary': {'usedPercent': 42}},
                'gpt-6-sol': {'primary': {'usedPercent': None}}}}
            unknown = normalize_quota(quota_candidate_from_response(response, observed_at=stamp(-10)))
            record(db, 'codex', {**unknown, 'provider': 'openai'})
            self.assertIsNotNone(exhausted(db, config))
            response['rateLimitsByLimitId']['gpt-6-sol']['primary']['usedPercent'] = 20
            usable = normalize_quota(quota_candidate_from_response(response, observed_at=stamp(-1)))
            record(db, 'codex', {**usable, 'provider': 'openai'})
            self.assertIsNone(exhausted(db, config))
