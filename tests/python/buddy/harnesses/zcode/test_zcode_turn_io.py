"""Common micro task input/receipt boundaries remain independent of each harness.

The turn records here are synthetic native facts in the current provenance
formats (``protocol.fixtures.native_turn``): they pass the real registered
validators, but no harness ran and nothing here is real native verification.
"""
import hashlib
import json
import unittest

from buddy.harnesses.zcode.test_zcode import ZcodeFixtureCase
from protocol.fixtures import native_turn
from hey_my_buddy.buddy.roles import turn_io
from private_acl import assert_private_path


class SharedTurnIOTests(ZcodeFixtureCase):
    def test_private_turn_input_keeps_content_beyond_a_message_frame(self):
        context = self.context()
        context.turn_input["version"] = 3
        context.turn_input["context"] = {"evidence": "汉" * 100000, "tail": "complete-input"}
        turn_io.prepare_turn(context)
        self.assertGreater(context.turn_input_file().stat().st_size, 256 * 1024)
        self.assertEqual(json.loads(context.turn_input_file().read_text()), context.turn_input)

    def test_canonical_input_and_scoped_credential_are_distinct_private_files(self):
        context = self.context()
        context.spec.update(adapter="zcode", harness="zcode")
        context.agent_credential = "private-agent-token"
        turn_io.prepare_turn(context)
        self.assertEqual(hashlib.sha256(context.turn_input_file().read_bytes()).hexdigest(), turn_io.input_hash(context.turn_input))
        self.assertNotIn(context.agent_credential, context.turn_input_file().read_text(encoding="utf-8"))
        assert_private_path(self, context.credential_file(), 0o600)
        self.assertNotIn("HMB_AGENT_CREDENTIAL_FILE", context.environment)
        self.assertEqual(json.loads(context.credential_file().read_text(encoding="utf-8"))["stateDir"],
                         context.environment["HMB_STATE_DIR"])
        from hey_my_buddy.private_dirs import context_root
        self.assertEqual(context.credential_file().parent, context_root(context, "zcode"))
        self.assertFalse(context.credential_file().is_relative_to(context.directory))
        self.assertFalse((context.directory / "agent-credential.json").exists())

    def test_immutable_receipts_cannot_overwrite_previous_evidence(self):
        path = self.root / "immutable.json"
        turn_io.private_json(path, {"accepted": 1}, exclusive=True)
        with self.assertRaises(FileExistsError):
            turn_io.private_json(path, {"accepted": 2}, exclusive=True)
        self.assertEqual(json.loads(path.read_text(encoding="utf-8")), {"accepted": 1})

    def test_request_suggestion_null_means_no_suggestion_and_other_values_stay_refused(self):
        # The incident's invalid argument is valid now: an explicit null
        # suggestedBuddyId is the honest spelling of "no suggestion" and
        # validates exactly like an absent key; non-string values still fail
        # the shared turn contract every adapter imports.
        def outcome(value=None, *, include: bool) -> dict:
            request = {"summary": "help", "attempted": "tried", "neededWork": "decide",
                       "expectedArtifacts": [], "acceptance": "decided"}
            if include:
                request["suggestedBuddyId"] = value
            return {"disposition": "attention", "summary": "fixture", "remaining": [], "decisions": [],
                    "artifacts": [], "request": request}

        self.assertIsNone(turn_io.validate_outcome(outcome(include=False)))
        self.assertIsNone(turn_io.validate_outcome(outcome(None, include=True)))
        for invalid in (5, True, 1.5, "", " ", ["x"], {"id": 1}, b"bytes"):
            with self.subTest(invalid=invalid):
                self.assertIsNotNone(turn_io.validate_outcome(outcome(invalid, include=True)))
        self.assertIsNone(turn_io.validate_outcome(outcome("coder-helper", include=True)))


if __name__ == "__main__":
    unittest.main()
