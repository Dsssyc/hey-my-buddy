"""Native provider identity and optional billing, with private synthetic files."""
from __future__ import annotations

import json
import os
import unittest
from pathlib import Path
from unittest import mock

from buddy.harnesses.zcode.test_native_run import NativeRunCase
from buddy.harnesses.zcode.test_zcode_tool_evidence import FAKE
from hey_my_buddy.buddy.harnesses.zcode import native_run
from hey_my_buddy.buddy.harnesses.zcode.adapter import ZcodeAdapter
from hey_my_buddy.buddy.harnesses.zcode.config import provider_access_types
from hey_my_buddy.buddy.roles.run_observers import FastCorrection, worker_observer
from hey_my_buddy.protocol.billing import zcode_access


class NativeConfigTests(NativeRunCase):
    def setUp(self):
        super().setUp()
        self.native_config = self.root / "用户 .配置 空格"
        self.fake = self.native_config / "bin/zcode"
        self.fake.parent.mkdir(parents=True)
        self.fake.write_text(FAKE)
        self.fake.chmod(0o755)
        self.builtin = self.native_config / "config/provider/zcode-builtin.json"
        self.builtin.parent.mkdir(parents=True)
        self.builtin.write_text(json.dumps({"config": {"providerConfigRules": {
            "templateRules": [], "providerRules": []}}}))
        self.personal = self.native_config / ".zcode/v2/provider_config.json"
        self.personal.parent.mkdir(parents=True)
        self.write_provider("zhipu-account")
        os.environ.update(BUDDY_ZCODE_CLI=str(self.fake), ZCODE_DATA_BASE_DIR=str(self.native_config))
        # These deliberately wrong shell pins must not override native defaults.
        os.environ.update(ZCODE_BUILTIN_PROVIDER_CONFIG_FILE=str(self.root / "missing-builtin"),
                          ZCODE_PERSONAL_PROVIDER_CONFIG_FILE=str(self.root / "missing-personal"))
        self.cwd = self.root / "检出 .有点 空格"
        self.cwd.mkdir()

    def write_provider(self, access, provider="fixture-account"):
        self.personal.write_text(json.dumps({"config": {"providerConfigRules": {"providerRules": [{
            "providerId": provider, "config": {"access": {
                "type": access, "apiKey": "secret-never-returned"},
                "api": {"baseUrl": "https://secret-endpoint.invalid"},
                "requestAuth": {"headers": {"Authorization": "secret-never-returned"}}}}]}}}))

    def discover(self):
        return native_run.run_discovery(cwd=str(self.cwd), invocation_root=self.base / "discover",
            native_root=self.base / "discovery-native", timeout_seconds=5, cancelled=lambda: False)

    def test_non_key_native_provider_is_discovered_and_selectable_with_no_snapshots(self):
        os.environ["BUDDY_ZCODE_TEST_CASE"] = "native-config"
        receipt = self.discover()
        provider = receipt["providers"][0]
        self.assertEqual(provider["provider"], "fixture-account")
        self.assertEqual(provider["accessType"], "unknown")
        request = self.fast_request("Choose a profile. Return only one JSON value matching this schema: {}")
        request = request.model_copy(update={"configuration": request.configuration.model_copy(
            update={"provider": provider["provider"]})})
        result = native_run.run(request, observer=worker_observer, services=None, cancelled=lambda: False)
        self.assertEqual(result.end.status, "ok", result)
        self.assertEqual(result.configuration.checked.provider.value, "fixture-account")
        env = json.loads(self.fake.with_name("launch-environment.json").read_text())
        self.assertNotIn("ZCODE_BUILTIN_PROVIDER_CONFIG_FILE", env)
        self.assertNotIn("ZCODE_PERSONAL_PROVIDER_CONFIG_FILE", env)
        self.assertEqual(env["ZCODE_STORAGE_DIR"], str(Path(request.private_state.native_root).resolve() / "storage"))
        self.assertEqual(env["ZCODE_SESSION_DB_PATH"], str(Path(request.private_state.native_root).resolve() / "sessions.sqlite"))
        for name in ("builtin-provider.json", "personal-provider.json"):
            self.assertEqual(list(self.base.rglob(name)), [])
        self.assertFalse((self.cwd / ".zcode/config.json").exists())
        self.assertNotIn("secret-never-returned", json.dumps(receipt))

    def test_unreadable_billing_files_preserve_native_catalog_and_names(self):
        os.environ["BUDDY_ZCODE_TEST_CASE"] = "discovery"
        self.write_provider("api-key", provider="fixture-api")
        before = self.discover()
        self.assertEqual(before["providers"][0]["accessType"], "api-key")
        self.personal.write_text("invalid JSON")
        after = self.discover()
        self.assertEqual([{**p, "accessType": "unknown"} for p in before["providers"]], after["providers"])
        self.assertEqual(after["providers"][0]["provider"], "fixture-api")
        self.assertEqual(zcode_access(after["providers"][0]["accessType"], "fixture")["kind"], "unknown")
        self.personal.unlink()
        self.assertEqual(self.discover()["providers"], after["providers"])
        for contents in ("null", "[]", '{"config":{"providerConfigRules":null}}'):
            self.personal.write_text(contents)
            self.assertEqual(self.discover()["providers"], after["providers"])
        with mock.patch.object(Path, "read_text", side_effect=PermissionError("private fixture")):
            self.assertEqual(provider_access_types(self.builtin, self.personal), {})

    def test_billing_only_projects_the_access_type_with_native_inheritance(self):
        for access, kind in (("api-key", "metered"), ("zhipu-coding-plan-api-key", "subscription"),
                             ("zhipu-account", "unknown")):
            with self.subTest(access=access):
                self.write_provider(access)
                value = provider_access_types(self.builtin, self.personal)
                self.assertEqual(set(value), {"fixture-account"})
                self.assertEqual(zcode_access(value["fixture-account"], "fixture")["kind"], kind)
                self.assertNotIn("secret", json.dumps(value))
        self.builtin.write_text(json.dumps({"config": {"providerConfigRules": {
            "templateRules": [{"templateId": "plan", "config": {"access": {"type": "zhipu-coding-plan-api-key"}}}],
            "providerRules": [{"providerId": "fixture-account", "templateId": "plan"}]}}}))
        self.personal.write_text(json.dumps({"config": {"providerConfigRules": {"providerRules": [{
            "providerId": "fixture-account", "config": {"api": {"baseUrl": "ignored"}}}]}}}))
        self.assertEqual(provider_access_types(self.builtin, self.personal),
                         {"fixture-account": "zhipu-coding-plan-api-key"})
        value = json.loads(self.personal.read_text())
        value["config"]["providerConfigRules"]["providerRules"][0]["config"]["access"] = None
        self.personal.write_text(json.dumps(value))
        self.assertEqual(provider_access_types(self.builtin, self.personal), {"fixture-account": "unknown"})

    def test_availability_and_preparation_do_not_read_provider_files(self):
        with mock.patch("hey_my_buddy.buddy.harnesses.runtime_selection.selected", return_value=None), \
             mock.patch.object(Path, "read_text", side_effect=AssertionError("provider read")):
            self.assertEqual(ZcodeAdapter().available(), (True, None))
            native_run.check_preparation({"provider": "fixture-account"}, dict(os.environ))


class FinalMessageUsageTests(NativeRunCase):
    def execute_rounds(self, case):
        os.environ["BUDDY_ZCODE_TEST_CASE"] = case
        request = self.fast_request("Choose a profile. Return only one JSON value matching this schema: {}")
        correction = FastCorrection(request.output_schema.value, request.input_text)
        return native_run.run(request, observer=correction.observer, services=None, cancelled=lambda: False)

    def test_success_and_format_correction_keep_all_native_usage(self):
        for case, rounds in (("usage-ok", 1), ("usage-correct", 2)):
            with self.subTest(case=case):
                result = self.execute_rounds(case)
                self.assertEqual(result.end.status, "ok", result)
                self.assertEqual(result.usage.value["inputTokens"], 12 * rounds)
                self.assertEqual(result.usage.value["outputTokens"], 3 * rounds)
                self.assertEqual(result.usage.value["cachedInputTokens"], 3 * rounds)
                self.assertEqual(result.usage.value["completeness"], "complete")

    def test_canonical_provider_refusal_keeps_failure_quota_and_usage(self):
        for case, rounds in (("usage-quota", 1), ("usage-projection-quota", 1), ("usage-correct-quota", 2)):
            with self.subTest(case=case):
                result = self.execute_rounds(case)
                self.assertEqual(result.end.reason_code, "native-turn-failed", result)
                self.assertEqual(result.native_error.value["attribution"]["providerErrorCode"], "1310")
                self.assertEqual(result.native_failure.value["code"], "quota-exceeded")
                self.assertEqual(result.native_failure.value["nativeCode"], "1310")
                self.assertEqual(result.usage.value["inputTokens"], 12 * rounds)
                self.assertNotIn("private-test-secret", str(result.native_error))


if __name__ == "__main__":
    unittest.main()
