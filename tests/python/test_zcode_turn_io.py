"""Common governed input/receipt boundaries remain independent of each harness."""
import hashlib
import json
import unittest

from test_zcode import ZcodeFixtureCase
from buddy.adapters import turn_io
from buddy.adapters.dsh import DshAdapter


class SharedTurnIOTests(ZcodeFixtureCase):
    def record(self, context):
        return {**{k: v for k, v in context.turn_input.items() if k not in ("context", "executionWorkspace")},
                "inputSha256": turn_io.input_hash(context.turn_input), "sessionId": "dsh-session",
                "outcome": {"disposition": "completed", "summary": "finished", "remaining": [], "decisions": [], "artifacts": [], "request": None},
                "provenance": {"tool": "buddy_finish_turn", "turnEnd": "completed", "rootSessionMatched": True, "flush": "awaited"}}

    def test_canonical_input_and_scoped_credential_are_distinct_private_files(self):
        context = self.context()
        context.agent_credential = "private-agent-token"
        turn_io.prepare_turn(context)
        self.assertEqual(hashlib.sha256(context.turn_input_file().read_bytes()).hexdigest(), turn_io.input_hash(context.turn_input))
        self.assertNotIn(context.agent_credential, context.turn_input_file().read_text())
        self.assertEqual(context.credential_file().stat().st_mode & 0o777, 0o600)
        self.assertEqual(context.environment["BUDDY_AGENT_CREDENTIAL_FILE"], str(context.credential_file()))

    def test_adapter_specific_flush_does_not_bypass_common_identity_or_stop(self):
        context = self.context()
        context.spec["adapter"] = "dsh"
        turn_io.prepare_turn(context)
        record = self.record(context)
        context.turn_output_file().write_text(json.dumps(record))
        self.assertIsNone(turn_io.read_turn(context, True, 0, DshAdapter.validate_turn_provenance)[1])
        self.assertIsNotNone(turn_io.read_turn(context, False, 0, DshAdapter.validate_turn_provenance)[1])
        for field, value in (("attemptId", "other"), ("inputSha256", "0" * 64), ("resumeMode", "native-session")):
            invalid = {**record, field: value}
            context.turn_output_file().write_text(json.dumps(invalid))
            self.assertIsNotNone(turn_io.read_turn(context, True, 0, DshAdapter.validate_turn_provenance)[1])
        record["provenance"].pop("flush")
        context.turn_output_file().write_text(json.dumps(record))
        self.assertIn("flush", turn_io.read_turn(context, True, 0)[1], "the default path uses the real DSH validator")
        self.assertIn("flush", turn_io.read_turn(context, True, 0, DshAdapter.validate_turn_provenance)[1])
        record["provenance"] = {"tool": "other", "turnEnd": "completed", "rootSessionMatched": True, "flush": "awaited"}
        context.turn_output_file().write_text(json.dumps(record))
        self.assertIn("terminal tool", turn_io.read_turn(context, True, 0)[1])

        zcode_context = self.context(index=2)
        zcode_context.spec["adapter"] = "zcode"
        turn_io.prepare_turn(zcode_context)
        zcode_context.turn_output_file().write_text(json.dumps(self.record(zcode_context)))
        self.assertIn("ZCode", turn_io.read_turn(zcode_context, True, 0)[1])

    def test_immutable_receipts_cannot_overwrite_previous_evidence(self):
        path = self.root / "immutable.json"
        turn_io.private_json(path, {"accepted": 1}, exclusive=True)
        with self.assertRaises(FileExistsError):
            turn_io.private_json(path, {"accepted": 2}, exclusive=True)
        self.assertEqual(json.loads(path.read_text()), {"accepted": 1})

    def test_request_suggestion_null_means_no_suggestion_and_other_values_stay_refused(self):
        # The incident's invalid argument is valid now: an explicit null
        # suggestedProfileId is the honest spelling of "no suggestion" and
        # validates exactly like an absent key; non-string values still fail
        # the shared turn contract every adapter imports.
        def outcome(value=None, *, include: bool) -> dict:
            request = {"summary": "help", "attempted": "tried", "neededWork": "decide",
                       "expectedArtifacts": [], "acceptance": "decided"}
            if include:
                request["suggestedProfileId"] = value
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
