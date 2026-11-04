"""The public workspace access choice is validated once at the boundary."""
from __future__ import annotations

import unittest

from hey_my_buddy.errors import BoardError
from pathlib import Path
from hey_my_buddy.protocol.requests import parse_request, dump_request


class WorkspaceAccessTests(unittest.TestCase):
    def packet(self, **changes):
        return {"cwd":str(Path(__file__).resolve().parent),"title":"Access boundary","task":"Inspect the authorization",
                "macroTaskId":"standalone","buddy":"route","checkout":"worktree","inputs":[],"expectedSeconds":60,
                "access":"write","writeScope":["."],**changes}

    def test_access_is_explicit_and_full_note_is_preserved(self):
        packet = self.packet()
        del packet["access"]
        with self.assertRaises(BoardError):
            parse_request("submit",packet)
        packet = self.packet(access="full",accessNote="  Need system writes  ")
        value = dump_request(parse_request("submit",packet))
        self.assertEqual(value["accessNote"],packet["accessNote"])
        self.assertEqual(value["writeScope"],["."])

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
                parse_request("submit",self.packet(**intent))


if __name__ == "__main__":
    unittest.main()
