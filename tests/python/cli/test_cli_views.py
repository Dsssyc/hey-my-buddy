"""The CLI preserves authoritative action ids and validates local output."""
from __future__ import annotations

import unittest

from hey_my_buddy.cli import cli_views
from hey_my_buddy.errors import BoardError
from hey_my_buddy.protocol.requests import parse_output


class ViewTests(unittest.TestCase):
    def test_receipts_keep_current_actions_and_exact_response_identity(self):
        response = {"runId": "run-1", "artifactId": "art-2", "status": "delivered",
                    "actions": ["accept", "continue", "conclude"],
                    "shutdown": {"selfConfirmed": True, "descendantsConfirmed": True}}
        for mode in ("brief", "full"):
            self.assertIs(cli_views.render("get", response, mode), response)
            self.assertIs(cli_views.render("await", response, mode), response)
        self.assertNotIn("resultCommand", response)

    def test_get_brief_keeps_response_ids_and_drops_repeated_input(self):
        response = {"runId": "run-1", "state": "delivered", "actions": ["accept"],
                    "finalArtifactId": "art-2", "microTask": {"task": "long task text"},
                    "task": {"selectedAttempt": {"issues": ["stop-unknown"]}},
                    "artifacts": [{"artifactId": "art-2"}]}
        brief = cli_views.render("get", response)
        self.assertEqual(brief["finalArtifactId"], "art-2")
        self.assertEqual(brief["actions"], ["accept"])
        self.assertEqual(brief["artifacts"], [{"artifactId": "art-2"}])
        self.assertNotIn("microTask", brief)
        self.assertEqual(brief["task"], {"selectedAttempt": {"issues": ["stop-unknown"]}})
        self.assertIs(cli_views.render("get", response, "full"), response)

    def test_output_is_cli_local_and_model_validated(self):
        params = {"runId": "run-1", "output": "full"}
        remaining, mode = parse_output(params)
        self.assertEqual(mode, "full")
        self.assertEqual(remaining, {"runId": "run-1"})
        self.assertEqual(params, {"runId": "run-1", "output": "full"})
        with self.assertRaises(BoardError) as raised:
            parse_output({"output": "verbose"})
        self.assertEqual(raised.exception.code, "INVALID_ARGUMENT")


if __name__ == "__main__":
    unittest.main()
