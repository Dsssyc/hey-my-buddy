"""Native billing labels using private boards and mock CLIs."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import sys
from unittest.mock import patch

from hey_my_buddy.protocol.billing import claude_status, codex_account, native_provider, zcode_access
from hey_my_buddy.blackboard.service.harness_health import HarnessHealth
from support import BoardTestCase


def stamp(offset=0):
    return (datetime.now(timezone.utc) + timedelta(seconds=offset)).isoformat().replace("+00:00", "Z")


class BillingTests(BoardTestCase):
    def setUp(self):
        super().setUp()
        self.catalog_fixture()

    def test_native_labels_are_conservative_and_do_not_read_credentials(self):
        self.assertEqual(codex_account({"type": "chatgpt", "planType": "pro"}, stamp())["kind"], "subscription")
        self.assertEqual(codex_account({"type": "chatgpt", "planType": "free"}, stamp())["kind"], "unknown")
        self.assertEqual(codex_account({"type": "apiKey"}, stamp())["kind"], "metered")
        self.assertEqual(codex_account({"type": "other"}, stamp())["kind"], "unknown")
        self.assertEqual(claude_status({"loggedIn": True, "apiProvider": "anthropic",
                                       "authMethod": "claude.ai", "subscriptionType": "pro"}, stamp())["kind"], "subscription")
        self.assertEqual(claude_status({"loggedIn": True, "apiProvider": "anthropic",
                                       "authMethod": "api_key"}, stamp())["kind"], "unknown")
        self.assertEqual(claude_status({"loggedIn": True, "apiProvider": "anthropic"}, stamp())["kind"], "unknown")
        self.assertEqual(zcode_access("zhipu-coding-plan-api-key", stamp())["kind"], "subscription")
        self.assertEqual(zcode_access("api-key", stamp())["kind"], "metered")
        self.assertEqual(zcode_access("oauth", stamp())["kind"], "unknown")

    def test_native_provider_spelling_and_unknown_gateway_funding_are_preserved(self):
        for provider in ("bedrock", "vertex", "foundry", "custom-gateway", "firstParty"):
            self.assertEqual(native_provider({"apiProvider": provider}, "apiProvider"), provider)
        for account in (None, {}, {"apiProvider": None}, {"apiProvider": ""}):
            self.assertEqual(native_provider(account, "apiProvider"), "unknown")
        for provider in ("anthropic", "bedrock", "vertex", "foundry", "custom-gateway", None):
            billing = claude_status({"loggedIn": True, "apiProvider": provider, "authMethod": "api_key"}, stamp())
            self.assertEqual(billing["kind"], "unknown")
            self.assertEqual(billing["source"], "claude/auth-status")

    def test_catalog_access_types_project_billing_per_provider(self):
        from copy import deepcopy
        from support import FIXTURE_CATALOG
        board = self.board()
        board.call("model_catalog_refresh", {"requestId": "dsh-billing"})
        with board.store.db.read() as db:
            from hey_my_buddy.protocol.billing import for_provider
            self.assertEqual(for_provider(db, "dsh", "deepseek-official")["kind"], "metered")
        native = deepcopy(FIXTURE_CATALOG)
        provider = native["providers"][0]
        provider.update(harness="zcode", provider="zhipu", accessType="zhipu-coding-plan-api-key")
        native["discoveries"] = [{"harness": "zcode", "status": "complete"}]
        self.catalog_fixture(native)
        board.call("model_catalog_refresh", {"requestId": "zcode-billing"})
        with board.store.db.read() as db:
            from hey_my_buddy.protocol.billing import for_provider
            self.assertEqual(for_provider(db, "zcode", "zhipu")["kind"], "subscription")
            self.assertEqual(for_provider(db, "zcode", "other")["kind"], "unknown")

    def test_mock_codex_account_read_has_no_model_turn(self):
        from hey_my_buddy.buddy.harnesses.codex.account_probe import read
        path = Path(__file__).resolve().parents[1] / "buddy/harnesses/codex/fixtures/mock_codex.py"
        billing = read([sys.executable, str(path)], {"HOME": str(self.directory),
                                                          "USERPROFILE": str(self.directory),
                                                          "PATH": os.environ.get("PATH", "")})
        self.assertEqual(billing["kind"], "subscription")
        self.assertEqual(billing["provider"], "openai")

    def test_codex_on_demand_read_is_throttled_even_for_forced_refresh(self):
        from hey_my_buddy.blackboard.store.store import BoardStore
        board = BoardStore(self.directory / "health")
        self.addCleanup(board.db.close)
        board.initialize()
        health = HarnessHealth(board)
        self.addCleanup(health.close)
        health.initialize()
        with board.db.write() as db:
            db.execute("UPDATE harness_health SET record_json=? WHERE harness='codex'",
                       (json.dumps({"reviewVerification": {"verified": True}}),))
        record = {"status": "ready", "command": ["mock-codex"], "fingerprint": {"id": 1}}
        with patch("hey_my_buddy.blackboard.service.harness_health._snapshot", return_value={"harness": "codex", "managerRecords": [], "candidates": []}), \
             patch("hey_my_buddy.blackboard.service.harness_health._discover", return_value=record), \
             patch("hey_my_buddy.buddy.harnesses.codex.account_probe.read", return_value=({**codex_account({"type": "chatgpt", "planType": "pro"}, stamp()), "provider": "openai"})) as probe:
            health.refresh("codex", force=True)
            health.refresh("codex", force=True)
            self.assertEqual(probe.call_count, 1)
            self.assertEqual(health.get("codex")["billingByProvider"]["openai"]["kind"], "subscription")
            self.assertNotIn("reviewVerification", health.get("codex"))


if __name__ == "__main__":
    import unittest
    unittest.main()
