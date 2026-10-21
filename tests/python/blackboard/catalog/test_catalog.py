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
from hey_my_buddy.buddy.harnesses.registry import capability_report, require_permission
from hey_my_buddy.protocol.capabilities import PERMISSION_LEVELS
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
            result = catalog.discover(directory=self.directory, harnesses=[{
                "harness": "missing", "reason": {"code": "harness.not-checked", "params": {"operation": "probe"}}}])
        self.assertEqual(result["discoveries"], [{"harness": "missing", "status": "unknown", "accountStatus": "unknown",
                                                  "reason": {"code": "harness.not-checked", "params": {"operation": "probe"}},
                                                  "content": None}])

    def test_discovery_preserves_each_harness_status_and_reason(self):
        observations = {
            "codex": {"status": "unknown", "accountStatus": "confirmed",
                      "reason": {"code": "model-list-shape-unreadable", "params": {}}},
            "claude": {"status": "complete", "accountStatus": "not-applicable"},
            "dsh": {"status": "unknown", "accountStatus": "not-applicable"},
            "zcode": {"status": "complete", "accountStatus": "confirmed"},
        }
        instances = {}
        for name, fact in observations.items():
            payload = {"source": name + "-fixture", "providers": [{"harness": name, "provider": "fixture",
                       "models": [{"id": "alpha", "efforts": ["high"]}]}],
                       "discoveries": [{"harness": name, **fact}],
                       "warnings": [{"reason": {"code": "catalog.model-shape-unreadable", "params": {}},
                                     "diagnostic": {"event": name}}],
                       "harnessVersion": "fixture-1"}
            instances[name] = SimpleNamespace(model_discovery=True, discovery_available=lambda: (True, None),
                                              discover_models=lambda payload=payload: payload)
        with patch("hey_my_buddy.buddy.harnesses.registry.adapters", return_value=instances), \
             patch.object(catalog, "_override", return_value=None):
            result = catalog.discover(directory=self.directory, harnesses=[{
                "harness": name, "reason": None, "status": "ready"} for name in ("codex", "claude", "dsh", "zcode")])
        self.assertEqual(result["discoveries"], [
            {"harness": name, "status": fact["status"], "accountStatus": fact["accountStatus"],
             "reason": fact.get("reason") if fact["status"] == "unknown" else None,
             "content": None}
            for name, fact in observations.items()])
        self.assertEqual([entry["harness"] for entry in result["providers"]], list(observations))
        self.assertEqual(result["warnings"], [
            {"reason": {"code": "catalog.model-shape-unreadable", "params": {}},
             "diagnostic": {"event": name}, "harness": name}
            for name in observations])
        self.assertEqual(result["source"], "; ".join(name + "-fixture" for name in observations))
        self.assertEqual(result["harnessVersion"], "; ".join(name + ":fixture-1" for name in observations))

    def test_discovery_receipt_rejects_unregistered_or_malformed_reasons(self):
        payload = copy.deepcopy(FIXTURE_CATALOG)
        payload["discoveries"] = [{"harness": "dsh", "status": "unknown",
                                   "reason": "native-metadata-wait-expired"}]
        with self.assertRaisesRegex(BoardError, "discovery.reason"):
            catalog.discovery_payload(payload)
        payload["discoveries"][0]["reason"] = {"code": "catalog.model-shape-unreadable", "params": {"extra": "x"}}
        with self.assertRaisesRegex(BoardError, "discovery.reason"):
            catalog.discovery_payload(payload)
        payload["discoveries"][0]["reason"] = {"code": "native-metadata-wait-expired", "params": {}}
        payload["warnings"] = ["native warning"]
        with self.assertRaisesRegex(BoardError, "warning"):
            catalog.discovery_payload(payload)
        payload["warnings"] = [{"reason": {"code": "catalog.model-list-unreadable", "params": {}}}]
        with self.assertRaisesRegex(BoardError, "warning.reason"):
            catalog.discovery_payload(payload)

    def test_native_boundary_checks_model_reasons_content_and_receipt_shape(self):
        valid = copy.deepcopy(FIXTURE_CATALOG)
        valid["discoveries"] = [{"harness": "dsh", "status": "unknown",
                                  "reason": {"code": "model-list-shape-unreadable", "params": {}},
                                  "content": "native detail"}]
        self.assertEqual(catalog.discovery_payload(valid)["discoveries"][0]["content"], "native detail")
        invalid_values = [None, [], "catalog"]
        for content in (1, [], "x" * (catalog.MAX_TEXT + 1)):
            value = copy.deepcopy(valid)
            value["discoveries"][0]["content"] = content
            invalid_values.append(value)
        for cause in ({"code": "harness.not-checked", "params": {"operation": "probe"}},
                      {"code": "catalog.native-unavailable", "params": {"extra": "bad"}},
                      "native unavailable"):
            value = copy.deepcopy(valid)
            value["providers"][0]["models"][0].update(available=False, unavailableReason=cause)
            invalid_values.append(value)
        for cause in ("catalog.account-status-unknown", "catalog.native-unknown", "history.reason-unavailable"):
            value = copy.deepcopy(valid)
            params = {"recordId": "old"} if cause.startswith("history.") else {"harness": "dsh"}
            value["discoveries"][0]["reason"] = {"code": cause, "params": params}
            invalid_values.append(value)
        value = copy.deepcopy(valid)
        value["warnings"] = [{"reason": {"code": "catalog.model-shape-unreadable", "params": {}},
                              "diagnostic": 123}]
        invalid_values.append(value)
        for value in invalid_values:
            with self.subTest(value=value), self.assertRaises(BoardError) as caught:
                catalog.discovery_payload(value)
            self.assertEqual(caught.exception.code, "CATALOG_INVALID")

    def test_native_warning_limit_keeps_one_normalization_rule_and_diagnostics(self):
        value = copy.deepcopy(FIXTURE_CATALOG)
        value["warnings"] = [{"reason": {"code": "catalog.model-shape-unreadable", "params": {}},
                              "diagnostic": {"unknownCount": number}, "harness": ["not-native"], "content": 123}
                             for number in range(20)]
        native = catalog.discovery_payload(value)
        self.assertEqual(native["warnings"], [
            {"reason": item["reason"], "diagnostic": item["diagnostic"]} for item in value["warnings"][:16]])
        self.assertEqual(catalog.canonical_payload(native)["warnings"], native["warnings"])

    def test_capability_report_uses_health_reason_only_for_harnesses(self):
        def adapter():
            return SimpleNamespace(available=lambda: (True, None), capabilities=())
        stored = {"reason": {"code": "harness.login-required", "params": {"operation": "probe"}}}
        with patch("hey_my_buddy.buddy.harnesses.registry.adapters", return_value={
                "codex": adapter(), "dsh": adapter(), "command": adapter()}), \
             patch("hey_my_buddy.buddy.harnesses.runtime_selection.selected",
                   side_effect=lambda name: stored if name == "dsh" else None):
            report = capability_report()
        self.assertEqual(report["codex"]["reason"],
                         {"code": "harness.not-checked", "params": {"operation": "probe"}})
        self.assertEqual(report["dsh"]["reason"], stored["reason"])
        self.assertIsNone(report["command"]["reason"])

    def test_discovery_without_own_observation_keeps_complete_and_unknown_account(self):
        instance = SimpleNamespace(model_discovery=True, discovery_available=lambda: (True, None),
                                   discover_models=lambda: {"source": "fixture-native", "providers": [],
                                                            "discoveries": []})
        with patch("hey_my_buddy.buddy.harnesses.registry.adapters", return_value={"dsh": instance}), \
             patch.object(catalog, "_override", return_value=None):
            result = catalog.discover(directory=self.directory, harnesses=[{
                "harness": name, "reason": None, "status": "ready"} for name in ("codex", "claude", "dsh", "zcode")])
        self.assertEqual(result["discoveries"], [{"harness": "dsh", "status": "complete",
                                                 "accountStatus": "unknown", "reason": None, "content": None}])

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
            refreshed = catalog.discover(directory=self.directory, harnesses=[{
                "harness": "claude", "reason": None, "status": "ready"}])
            self.assertEqual(refreshed["discoveries"], [{"harness": "claude", "status": "complete",
                                                         "accountStatus": "not-applicable", "reason": None,
                                                         "content": None}])
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
                {"id": "basic", "efforts": ["off"], "available": False, "unavailableReason": {"code": "catalog.native-unavailable", "params": {}},
                 "unavailableContent": "Unsupported auth",
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
        unavailable = next(item for item in zcode if item["model"] == "basic")
        self.assertFalse(unavailable["available"])
        self.assertEqual(unavailable["unavailableReason"], {"code": "catalog.native-unavailable", "params": {}})
        self.assertEqual(unavailable["unavailableContent"], "Unsupported auth")

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
        for buddy in buddies:
            self.assertFalse(any(cap.startswith("permission:") or cap in ("decision", "routing:fast") for cap in buddy["capabilities"]))

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
        with patch.dict(PERMISSION_LEVELS, {"claude": {
                level: declaration for level, declaration in PERMISSION_LEVELS["claude"].items()
                if level != "full"}}):
            with self.assertRaises(BoardError) as caught:
                require_permission({"harness": "claude"}, "full")
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
            catalog.discovery_payload(payload)

    def test_model_bound_refuses_an_incomplete_catalog(self):
        payload = copy.deepcopy(FIXTURE_CATALOG)
        payload["providers"][0]["models"] = [{"id": f"model-{i}"} for i in range(catalog.MAX_MODELS + 1)]
        with self.assertRaisesRegex(BoardError, "at most"):
            catalog.discovery_payload(payload)

    def test_harness_identity_is_required_without_dsh_default(self):
        payload = copy.deepcopy(FIXTURE_CATALOG)
        del payload["providers"][0]["harness"]
        with self.assertRaises(BoardError):
            catalog.discovery_payload(payload)

    def test_explicit_validation_does_not_select_or_guess_an_effort(self):
        class Harness:
            model_discovery = True
        with tempfile.TemporaryDirectory(prefix="hey-my-buddy-catalog-test-") as temporary:
            directory = Path(temporary)
            file = directory / "catalog.json"
            file.write_text(json.dumps(self.payload()), encoding="utf-8", newline="\n")
            with patch.dict(os.environ, {catalog.CATALOG_FILE_ENV: str(file)}), patch("hey_my_buddy.buddy.harnesses.registry.adapter", return_value=Harness()):
                identity = dict(harness="zcode", provider="deepseek-official", model="deepseek-flash", effort="high")
                self.assertEqual(catalog.validate_buddy(identity, directory=directory), identity)
                for replacement in ({"effort": "max"}, {"provider": "missing"}, {"model": "basic", "effort": "off"}):
                    with self.subTest(replacement=replacement), self.assertRaises(BoardError):
                        catalog.validate_buddy({**identity, **replacement}, directory=directory)
                with self.assertRaises(BoardError):
                    catalog.validate_buddy({"model": "deepseek-flash"}, directory=directory)
            self.assertEqual(sorted(item.name for item in directory.iterdir()), ["catalog.json"])
