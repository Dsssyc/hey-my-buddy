"""Native identities, hard legal options and metadata boundaries across harnesses."""
import copy
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from hey_my_buddy.blackboard.catalog import catalog
from hey_my_buddy.buddy.harnesses.registry import _WORKER_PERMISSIONS, require_permission
from hey_my_buddy.protocol import schemas
from hey_my_buddy.errors import BoardError
from support import FIXTURE_CATALOG


class CatalogTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)

    def test_missing_harness_is_not_started_by_explicit_discovery(self):
        from hey_my_buddy.buddy.harnesses.base import Adapter

        class MissingHarness(Adapter):
            model_discovery = True

            def available(self):
                return False, "fixture executable missing"

            def discover_models(self):
                raise AssertionError("No controller should be spawned for a missing executable")

        with patch("hey_my_buddy.buddy.harnesses.registry.adapters", return_value={"missing": MissingHarness()}), \
             patch.object(catalog, "_override", return_value=None):
            result = catalog.discover(directory=self.directory)
        self.assertEqual(result["discoveries"], [{"harness": "missing", "status": "unknown", "accountStatus": "unknown",
                                                  "reason": "fixture executable missing"}])

    def test_discovery_preserves_each_harness_status_and_reason(self):
        observations = {
            "codex": {"status": "unknown", "accountStatus": "confirmed", "reason": "native model shape unreadable"},
            "claude": {"status": "complete", "accountStatus": "not-applicable"},
            "dsh": {"status": "unknown", "accountStatus": "not-applicable"},
            "zcode": {"status": "complete", "accountStatus": "confirmed", "reason": "native discovery note"},
        }
        instances = {}
        for name, fact in observations.items():
            payload = {"source": name + "-fixture", "providers": [{"harness": name, "provider": "fixture",
                       "models": [{"id": "alpha", "efforts": ["high"]}]}],
                       "discoveries": [{"harness": name, **fact}], "warnings": [name + " warning"],
                       "harnessVersion": "fixture-1"}
            instances[name] = SimpleNamespace(model_discovery=True, discovery_available=lambda: (True, None),
                                              discover_models=lambda payload=payload: payload)
        with patch("hey_my_buddy.buddy.harnesses.registry.adapters", return_value=instances), \
             patch.object(catalog, "_override", return_value=None):
            result = catalog.discover(directory=self.directory)
        self.assertEqual(result["discoveries"], [{"harness": name, **fact, "reason": fact.get("reason")}
                                                 for name, fact in observations.items()])
        self.assertEqual([entry["harness"] for entry in result["providers"]], list(observations))
        self.assertEqual(result["warnings"], [name + " warning" for name in observations])
        self.assertEqual(result["source"], "; ".join(name + "-fixture" for name in observations))
        self.assertEqual(result["harnessVersion"], "; ".join(name + ":fixture-1" for name in observations))

    def test_discovery_without_own_observation_keeps_complete_and_unknown_account(self):
        instance = SimpleNamespace(model_discovery=True, discovery_available=lambda: (True, None),
                                   discover_models=lambda: {"source": "fixture-native", "providers": [],
                                                            "discoveries": []})
        with patch("hey_my_buddy.buddy.harnesses.registry.adapters", return_value={"dsh": instance}), \
             patch.object(catalog, "_override", return_value=None):
            result = catalog.discover(directory=self.directory)
        self.assertEqual(result["discoveries"], [{"harness": "dsh", "status": "complete",
                                                 "accountStatus": "unknown", "reason": None}])

    def test_explicit_discovery_refreshes_cached_unavailable_authentication(self):
        from hey_my_buddy.buddy.harnesses.claude import adapter as claude
        native = {"source": "claude-fixture", "providers": [{"harness": "claude", "provider": "anthropic",
                  "models": [{"id": "fixture-opus", "efforts": ["high"]}]}],
                  "discoveries": [{"harness": "claude", "status": "complete", "accountStatus": "not-applicable"}]}
        instance = claude.ClaudeAdapter()
        claude._reset_metadata_cache()
        self.addCleanup(claude._reset_metadata_cache)
        with patch.object(claude, "cli_command", return_value=["/fixture/claude"]), \
             patch.object(claude, "_probe_native_metadata", side_effect=[
                 BoardError("HARNESS_UNAVAILABLE", "fixture not logged in"), {"catalog": native}]) as probe, \
             patch("hey_my_buddy.buddy.harnesses.registry.adapters", return_value={"claude": instance}), \
             patch.object(catalog, "_override", return_value=None):
            self.assertFalse(instance.available()[0])
            self.assertFalse(instance.available()[0])
            self.assertEqual(probe.call_count, 1)
            refreshed = catalog.discover(directory=self.directory)
            self.assertEqual(refreshed["discoveries"], [{"harness": "claude", "status": "complete",
                                                         "accountStatus": "not-applicable", "reason": None}])
            self.assertEqual(probe.call_count, 2, "Explicit discovery must bypass the old auth failure")
            self.assertTrue(instance.available()[0])
            buddies = catalog.CatalogView.from_payload(refreshed).proposed_buddies()
            self.assertEqual(len(buddies), 1)
            self.assertFalse(buddies[0]["enabled"], "Discovery cannot grant execution permission")

    def payload(self):
        payload = copy.deepcopy(FIXTURE_CATALOG)
        payload["providers"].append({
            "harness": "zcode", "provider": "deepseek-official", "displayName": "Separate harness",
            "apiKey": "private-provider-secret",
            "models": [
                {"id": "deepseek-flash", "name": "Same model id", "efforts": ["low", "high"], "contextWindow": 1000},
                {"id": "basic", "efforts": ["off"], "available": False, "unavailableReason": "Unsupported auth",
                 "requestHeaders": {"Authorization": "private-model-secret"}},
            ],
        })
        return payload

    def test_same_provider_and_model_in_different_harnesses_stay_distinct(self):
        view = catalog.CatalogView.from_payload(self.payload())
        self.assertEqual(view.efforts_for("dsh", "deepseek-official", "deepseek-flash"), ["off", "low", "high", "max"])
        self.assertEqual(view.efforts_for("zcode", "deepseek-official", "deepseek-flash"), ["low", "high"])
        self.assertIsNone(view.lookup("command", "deepseek-official", "deepseek-flash"))
        proposals = view.proposed_buddies()
        zcode = [item for item in proposals if item["harness"] == "zcode"]
        self.assertEqual({item["effort"] for item in zcode if item["model"] == "deepseek-flash"}, {"low", "high"})
        self.assertTrue(all(item["buddyId"].startswith("zcode:") for item in zcode))
        self.assertTrue(all(not item["enabled"] for item in proposals))
        self.assertFalse(next(item for item in zcode if item["model"] == "basic")["available"])

    def test_unknown_native_settings_never_enter_public_catalog_or_proposals(self):
        view = catalog.CatalogView.from_payload(self.payload())
        public = json.dumps([view.metadata(), view.proposed_buddies()])
        for forbidden in ("apiKey", "requestHeaders", "Authorization", "private-provider-secret", "private-model-secret"):
            self.assertNotIn(forbidden, public)

    def test_each_model_declares_its_own_legal_efforts(self):
        view = catalog.CatalogView.from_payload(self.payload())
        self.assertEqual(view.efforts_for("zcode", "deepseek-official", "basic"), ["off"])
        self.assertNotIn("max", view.efforts_for("zcode", "deepseek-official", "deepseek-flash"))

    def test_buddy_permissions_follow_worker_adapter_declarations(self):
        payload = {"source": "permission-fixture", "providers": [
            {"harness": name, "provider": "fixture", "models": [{"id": "model", "efforts": ["off"]}]}
            for name in ("claude", "codex", "dsh", "zcode")
        ]}
        buddies = catalog.CatalogView.from_payload(payload).proposed_buddies()
        permissions = {buddy["harness"]: {cap for cap in buddy["capabilities"]
                                            if cap.startswith("permission:")} for buddy in buddies}
        self.assertEqual(permissions, {
            "claude": {"permission:read", "permission:write", "permission:full"},
            "codex": {"permission:read", "permission:write", "permission:full"},
            "dsh": {"permission:read", "permission:write", "permission:full"},
            "zcode": {"permission:read", "permission:write", "permission:full"},
        })
        with patch.dict(_WORKER_PERMISSIONS, {"claude": {
                level: declaration for level, declaration in _WORKER_PERMISSIONS["claude"].items()
                if level != "full"}}):
            restricted = catalog.CatalogView.from_payload(payload).proposed_buddies()
        claude = next(buddy for buddy in restricted if buddy["harness"] == "claude")
        self.assertNotIn("permission:full", claude["capabilities"])

    def test_explicit_permission_gate_uses_harness_declaration(self):
        require_permission(None, "full")
        require_permission({"harness": "claude"}, "read")
        require_permission({"harness": "codex"}, "read")
        require_permission({"harness": "dsh"}, "read")
        require_permission({"harness": "zcode"}, "read")
        require_permission({"harness": "codex"}, "write")
        require_permission({"harness": "codex"}, "full")
        require_permission({"harness": "claude"}, "full")
        require_permission({"harness": "dsh"}, "full")
        require_permission({"harness": "zcode"}, "full")
        require_permission({"adapter": "external"}, "read")
        require_permission({"adapter": "command"}, "write")
        with patch.dict(_WORKER_PERMISSIONS, {"claude": {
                level: declaration for level, declaration in _WORKER_PERMISSIONS["claude"].items()
                if level != "full"}}):
            with self.assertRaises(BoardError) as caught:
                require_permission({"harness": "claude"}, "full")
            self.assertEqual(caught.exception.code, "UNSUPPORTED_CAPABILITY")
            self.assertEqual(caught.exception.details["requiredCapability"], "permission:full")
        with self.assertRaises(BoardError) as caught:
            require_permission({"adapter": "external"}, "full")
        self.assertEqual(caught.exception.code, "UNSUPPORTED_CAPABILITY")
        self.assertEqual(caught.exception.details["requiredCapability"], "permission:full")

    def test_native_ids_with_context_suffixes_keep_legal_distinct_buddy_ids(self):
        payload = {"source": "claude-native-fixture", "providers": [{
            "harness": "claude", "provider": "anthropic", "models": [
                {"id": "claude-opus-5-5[1m]", "efforts": ["high"]},
                {"id": "claude-opus-5-5-1m-", "efforts": ["high"]},
                {"id": "claude-haiku-4-5-20251001", "efforts": ["default"]},
            ],
        }]}
        buddies = catalog.CatalogView.from_payload(payload).proposed_buddies()
        self.assertEqual([p["model"] for p in buddies], [m["id"] for m in payload["providers"][0]["models"]])
        self.assertEqual(len({p["buddyId"] for p in buddies}), 3)
        for buddy in buddies:
            # These IDs must survive card/preference/evidence publication, whose
            # identifier contract is deliberately narrower than native model IDs.
            schemas.required_string(buddy, "buddyId", max_length=128, pattern=schemas.IDENTIFIER_PATTERN)
        self.assertEqual(buddies, catalog.CatalogView.from_payload(payload).proposed_buddies())
        self.assertEqual(buddies[2]["effort"], "default")
        self.assertEqual(buddies[2]["buddyId"], "claude:anthropic:claude-haiku-4-5-20251001:default")

    def test_duplicate_harness_provider_is_rejected_instead_of_shadowed(self):
        payload = self.payload()
        payload["providers"].append(copy.deepcopy(payload["providers"][0]))
        with self.assertRaisesRegex(BoardError, "Repeated catalog provider"):
            catalog.canonical_payload(payload)

    def test_model_bound_refuses_an_incomplete_catalog(self):
        payload = copy.deepcopy(FIXTURE_CATALOG)
        payload["providers"][0]["models"] = [{"id": f"model-{i}"} for i in range(catalog.MAX_MODELS + 1)]
        with self.assertRaisesRegex(BoardError, "at most"):
            catalog.canonical_payload(payload)

    def test_harness_identity_is_required_without_dsh_default(self):
        payload = copy.deepcopy(FIXTURE_CATALOG)
        del payload["providers"][0]["harness"]
        with self.assertRaises(BoardError):
            catalog.canonical_payload(payload)

    def test_explicit_validation_does_not_select_or_guess_an_effort(self):
        class Harness:
            model_discovery = True
        with tempfile.TemporaryDirectory(prefix="hey-my-buddy-catalog-test-") as temporary:
            directory = Path(temporary)
            file = directory / "catalog.json"
            file.write_text(json.dumps(self.payload()))
            with patch.dict(os.environ, {catalog.CATALOG_FILE_ENV: str(file)}), patch("hey_my_buddy.buddy.harnesses.registry.adapter", return_value=Harness()):
                identity = dict(harness="zcode", provider="deepseek-official", model="deepseek-flash", effort="high")
                self.assertEqual(catalog.validate_buddy(identity, directory=directory), identity)
                for replacement in ({"effort": "max"}, {"provider": "missing"}, {"model": "basic", "effort": "off"}):
                    with self.subTest(replacement=replacement), self.assertRaises(BoardError):
                        catalog.validate_buddy({**identity, **replacement}, directory=directory)
                with self.assertRaises(BoardError):
                    catalog.validate_buddy({"model": "deepseek-flash"}, directory=directory)
            self.assertEqual(sorted(item.name for item in directory.iterdir()), ["catalog.json"])
