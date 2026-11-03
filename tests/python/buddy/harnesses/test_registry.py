"""The registry's capability view publishes the code-declared input limits.

Synthetic adapters only: no harness binary is probed and no model is called.
The declaration itself is pinned in the protocol tests; this file pins its
place in the one view the blackboard serves.
"""
from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest import mock

from hey_my_buddy.buddy.harnesses.registry import capability_report
from hey_my_buddy.protocol.capabilities import INPUT_LIMITS


def adapter():
    return SimpleNamespace(available=lambda: (True, None), capabilities=())


class CapabilityReportInputLimitTests(unittest.TestCase):
    def report(self):
        names = (*INPUT_LIMITS, "command", "decision")
        with mock.patch("hey_my_buddy.buddy.harnesses.registry.adapters",
                        return_value={name: adapter() for name in names}), \
             mock.patch("hey_my_buddy.buddy.harnesses.runtime_selection.selected",
                        return_value=None):
            return capability_report()

    def test_the_view_publishes_each_harnesses_exact_declaration(self):
        report = self.report()
        for name, declared in INPUT_LIMITS.items():
            with self.subTest(harness=name):
                self.assertEqual(report[name]["inputLimit"], declared.to_payload())

    def test_the_published_codex_limit_carries_the_verified_facts(self):
        entry = self.report()["codex"]
        self.assertEqual(entry["inputLimit"]["state"], "known")
        self.assertEqual(entry["inputLimit"]["value"], 1_048_576)
        self.assertEqual(entry["inputLimit"]["unit"], "unicode-scalars")
        self.assertEqual(entry["inputLimit"]["scope"], "turn-start-text")
        self.assertEqual(entry["inputLimit"]["versions"], ["0.164.0"])
        self.assertEqual(entry["inputLimit"]["source"], "codex-0.164.0-public-source")

    def test_the_other_harnesses_publish_unknown_and_adapters_without_a_channel_none(self):
        report = self.report()
        for name in ("claude", "dsh", "zcode"):
            with self.subTest(harness=name):
                self.assertEqual(report[name]["inputLimit"]["state"], "unknown")
                self.assertNotIn("value", report[name]["inputLimit"])
                self.assertIn("note", report[name]["inputLimit"])
        for name in ("command", "decision"):
            with self.subTest(adapter=name):
                self.assertIsNone(report[name]["inputLimit"])
        # The existing view keys stay beside the new one.
        self.assertIn("permissionLevels", report["codex"])
        self.assertIn("capabilities", report["codex"])


if __name__ == "__main__":
    unittest.main()
