"""Native identities, hard legal options and metadata boundaries across harnesses."""
import copy
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from buddy import catalog
from buddy.errors import BoardError
from support import FIXTURE_CATALOG


class CatalogTests(unittest.TestCase):
    def payload(self):
        payload = copy.deepcopy(FIXTURE_CATALOG)
        payload["providers"].append({
            "adapter": "zcode", "provider": "deepseek-official", "displayName": "Separate harness",
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
        proposals = view.proposed_profiles()
        zcode = [item for item in proposals if item["adapter"] == "zcode"]
        self.assertEqual({item["effort"] for item in zcode if item["model"] == "deepseek-flash"}, {"low", "high"})
        self.assertTrue(all(item["profileId"].startswith("zcode:") for item in zcode))
        self.assertTrue(all(not item["enabled"] for item in proposals))
        self.assertFalse(next(item for item in zcode if item["model"] == "basic")["available"])

    def test_unknown_native_settings_never_enter_public_catalog_or_proposals(self):
        view = catalog.CatalogView.from_payload(self.payload())
        public = json.dumps([view.metadata(), view.proposed_profiles()])
        for forbidden in ("apiKey", "requestHeaders", "Authorization", "private-provider-secret", "private-model-secret"):
            self.assertNotIn(forbidden, public)

    def test_each_model_declares_its_own_legal_efforts(self):
        view = catalog.CatalogView.from_payload(self.payload())
        self.assertEqual(view.efforts_for("zcode", "deepseek-official", "basic"), ["off"])
        self.assertNotIn("max", view.efforts_for("zcode", "deepseek-official", "deepseek-flash"))

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

    def test_adapter_identity_is_required_without_dsh_default(self):
        payload = copy.deepcopy(FIXTURE_CATALOG)
        del payload["providers"][0]["adapter"]
        with self.assertRaises(BoardError):
            catalog.canonical_payload(payload)

    def test_explicit_validation_does_not_select_or_guess_an_effort(self):
        class Harness:
            model_discovery = True
        with tempfile.TemporaryDirectory(prefix="buddy-catalog-test-") as temporary:
            directory = Path(temporary)
            file = directory / "catalog.json"
            file.write_text(json.dumps(self.payload()))
            with patch.dict(os.environ, {catalog.CATALOG_FILE_ENV: str(file)}), patch("buddy.adapters.adapter", return_value=Harness()):
                identity = dict(adapter="zcode", provider="deepseek-official", model="deepseek-flash", effort="high")
                self.assertEqual(catalog.validate_configuration(identity, directory=directory), identity)
                for replacement in ({"effort": "max"}, {"provider": "missing"}, {"model": "basic", "effort": "off"}):
                    with self.subTest(replacement=replacement), self.assertRaises(BoardError):
                        catalog.validate_configuration({**identity, **replacement}, directory=directory)
                with self.assertRaises(BoardError):
                    catalog.validate_configuration({"model": "deepseek-flash"}, directory=directory)
            self.assertEqual(sorted(item.name for item in directory.iterdir()), ["catalog.json"])
