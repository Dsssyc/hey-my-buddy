"""ZCode discovery receipt conversion from a native-shaped fixture; no install."""
from __future__ import annotations

import unittest


class DiscoveryReceiptTests(unittest.TestCase):
    """The discovery receipt's account fact and its evidence boundary, no install."""

    def test_the_receipt_reports_the_not_applicable_account_fact(self):
        from hey_my_buddy.buddy.harnesses.zcode import native_run
        snapshot = {"settings": {"model": {"available": [{
            "ref": {"providerId": "fixture-api", "modelId": "fixture-model"}, "label": "Fixture model",
            "providerLabel": "Fixture API", "contextWindow": 200000,
            "reasoning": {"levels": [{"value": "low"}, {"value": "high"}], "defaultLevel": "low"},
            "properties": {"inputFormat": {"supportsText": True}}}]}}}
        receipt = native_run.catalog(snapshot, {"fixture-api": "api-key"}, "fixture")
        self.assertEqual(receipt["discoveries"],
                         [{"harness": "zcode", "status": "complete", "accountStatus": "not-applicable"}])
        self.assertTrue(any("account status is not applicable" in warning for warning in receipt["warnings"]),
                        receipt["warnings"])
        models = receipt["providers"][0]["models"]
        self.assertEqual([model["id"] for model in models], ["fixture-model"])
        self.assertEqual(models[0]["efforts"], ["low", "high"])


if __name__ == "__main__":
    unittest.main()
