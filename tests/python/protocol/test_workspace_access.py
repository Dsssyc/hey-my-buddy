"""The public workspace access choice is validated once at the boundary."""
from __future__ import annotations

import unittest

from hey_my_buddy.errors import BoardError
from hey_my_buddy.protocol.schemas import normalize_access, normalize_execution_workspace


class WorkspaceAccessTests(unittest.TestCase):
    def test_defaults_and_full_note_are_canonical(self):
        self.assertEqual(normalize_access({}), ("write", None))
        self.assertEqual(normalize_access({"access": "read"}), ("read", None))
        self.assertEqual(normalize_access({"access": "full", "accessNote": "  Need system writes  "}),
                         ("full", "  Need system writes  "))
        self.assertEqual(normalize_execution_workspace({"executionWorkspace": {
            "kind": "worktree", "access": "full", "accessNote": " Need system writes "}})["writeScope"], ["."])

    def test_note_is_required_only_for_full_and_must_fit_one_line(self):
        invalid = (
            {"access": None}, {"access": ""}, {"access": "unknown"}, {"access": 123},
            {"access": "full"}, {"access": "full", "accessNote": "  "},
            {"access": "full", "accessNote": "a" * 10001},
            {"access": "full", "accessNote": "first\nsecond"},
            {"access": "full", "accessNote": "first\rsecond"},
            {"access": "full", "accessNote": "first\n"},
            {"access": "full", "accessNote": "first\u2028second"},
            {"access": "read", "accessNote": "why"},
            {"access": "write", "accessNote": "why"},
            {"accessNote": "why"},
        )
        for intent in invalid:
            with self.subTest(intent=intent), self.assertRaises(BoardError):
                normalize_execution_workspace({"executionWorkspace": {"kind": "existing", **intent}})


if __name__ == "__main__":
    unittest.main()
