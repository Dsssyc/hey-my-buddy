"""The public workspace access choice is validated once at the boundary."""
from __future__ import annotations

import unittest

from hey_my_buddy.errors import BoardError
from hey_my_buddy.protocol.schemas import normalize_access, normalize_execution_workspace


class WorkspaceAccessTests(unittest.TestCase):
    def test_defaults_and_full_reason_are_canonical(self):
        self.assertEqual(normalize_access({}), ("write", None))
        self.assertEqual(normalize_access({"access": "read"}), ("read", None))
        self.assertEqual(normalize_access({"access": "full", "accessReason": "  Need system writes  "}),
                         ("full", "Need system writes"))
        self.assertEqual(normalize_execution_workspace({"executionWorkspace": {
            "kind": "worktree", "access": "full", "accessReason": " Need system writes "}})["writeScope"], ["."])

    def test_reason_is_required_only_for_full_and_must_fit_one_line(self):
        invalid = (
            {"access": None}, {"access": ""}, {"access": "unknown"}, {"access": 123},
            {"access": "full"}, {"access": "full", "accessReason": "  "},
            {"access": "full", "accessReason": "a" * 1001},
            {"access": "full", "accessReason": "first\nsecond"},
            {"access": "full", "accessReason": "first\rsecond"},
            {"access": "full", "accessReason": "first\n"},
            {"access": "full", "accessReason": "first\u2028second"},
            {"access": "read", "accessReason": "why"},
            {"access": "write", "accessReason": "why"},
            {"accessReason": "why"},
        )
        for intent in invalid:
            with self.subTest(intent=intent), self.assertRaises(BoardError):
                normalize_execution_workspace({"executionWorkspace": {"kind": "existing", **intent}})


if __name__ == "__main__":
    unittest.main()
