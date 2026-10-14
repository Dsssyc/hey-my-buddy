"""Declared ability to continue a confirmed interrupted native turn."""
import unittest

from hey_my_buddy.buddy.harnesses.registry import adapters


class InterruptedResumeCapabilityTests(unittest.TestCase):
    def test_built_in_declarations_match_interrupted_turn_evidence(self):
        available = adapters()
        self.assertEqual({name for name, adapter in available.items() if adapter.interrupted_resume},
                         {"codex", "dsh"})
        self.assertTrue(all(available[name].native_resume for name in ("codex", "dsh")))
