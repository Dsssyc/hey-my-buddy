"""Final-message delivery bounds summaries; completion tools still allow retry."""
from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from hey_my_buddy.buddy.harnesses.registry import worker_format
from hey_my_buddy.buddy.harnesses.run_contract import NativeIdentity
from hey_my_buddy.buddy.harnesses.session_receipts import verify_receipt
from hey_my_buddy.buddy.roles import run_execution, turn_io, worker_services
from hey_my_buddy.errors import BoardError
from hey_my_buddy.json_codec import canonical_json


def outcome(**changes):
    return {"disposition": "completed", "summary": "work done", "remaining": [],
            "decisions": [], "artifacts": [], "request": None, **changes}


class OutcomeTruncationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="hey-my-buddy-outcome-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.turn = {"taskId": "task", "attemptId": "attempt", "generation": 1,
                     "turnId": "turn", "resumeMode": "initial", "previousSessionId": None}
        self.facts = {"nativeTurnId": "native-turn"}

    def result(self, harness, value, *, parsed=True, facts=None):
        wrapper = {"outcome": value} if harness in ("codex", "claude") else value
        kind = "native-turn-facts" if harness in ("codex", "claude") else "turn-provenance"
        path = self.root / "facts.json"
        raw = canonical_json(self.facts if facts is None else facts).encode()
        path.write_bytes(raw)
        ref = SimpleNamespace(kind=kind, location=str(path), size_bytes=len(raw),
                              sha256=hashlib.sha256(raw).hexdigest())
        return SimpleNamespace(harness=harness, end=SimpleNamespace(status="ok"),
            value=SimpleNamespace(raw=canonical_json(wrapper), schema_status="valid",
                                  parsed=SimpleNamespace(value=wrapper) if parsed else None),
            evidence_refs=[ref], completion_evidence=None, native_identity=NativeIdentity(session_id="native-session"))

    def record(self, result):
        path = self.root / "output.json"
        path.unlink(missing_ok=True)
        record = run_execution._worker_result(result, {"outputFile": str(path)}, self.turn, "prompt")
        return record, path.read_bytes()

    def test_waiting_native_outcome_is_bound_to_the_lead_turn(self):
        for harness in ("codex", "claude"):
            with self.subTest(harness=harness):
                value = outcome(disposition="waiting")
                with self.assertRaises(BoardError):
                    self.record(self.result(harness, value))
                self.turn["context"] = {"delegationAuthority": True}
                record, _raw = self.record(self.result(harness, value))
                self.assertEqual(record["outcome"]["disposition"], "waiting")
                self.turn.pop("context")

    def test_final_message_item_count_is_truncated_and_recorded(self):
        for harness in ("codex", "claude"):
            for field in ("remaining", "decisions"):
                for parsed in (False, True):
                    with self.subTest(harness=harness, field=field, parsed=parsed):
                        value = outcome(**{field: [str(i) for i in range(37)]})
                        original = canonical_json(value)
                        record, _raw = self.record(self.result(harness, value, parsed=parsed))
                        self.assertEqual(record["outcome"][field], value[field][:32])
                        self.assertEqual(record["outcomeTruncated"],
                                         {field: {"droppedItems": 5, "shortenedItems": 0}})
                        self.assertEqual(canonical_json(value), original)
                        self.assertNotIn("outcomeTruncated", record["outcome"])
                        self.assertNotIn("outcomeTruncated", record["provenance"])

    def test_final_message_long_items_keep_complete_utf8_characters(self):
        for harness in ("codex", "claude"):
            for field in ("remaining", "decisions"):
                with self.subTest(harness=harness, field=field):
                    items = ["a" * 4095 + "汉", "a" * 4092 + "😀z", "汉" * 1366]
                    record, _raw = self.record(self.result(harness, outcome(**{field: items})))
                    self.assertEqual(record["outcome"][field],
                                     ["a" * 4095, "a" * 4092 + "😀", "汉" * 1365])
                    self.assertEqual(record["outcomeTruncated"],
                                     {field: {"droppedItems": 0, "shortenedItems": 3}})
                    self.assertTrue(all(len(item.encode()) <= 4096 for item in record["outcome"][field]))

    def test_both_arrays_report_dropped_and_retained_shortened_items(self):
        for harness in ("codex", "claude"):
            with self.subTest(harness=harness):
                value = outcome(decisions=["d" * 4097] + ["decision"] * 35 + ["d" * 4097],
                                remaining=["r" * 4097] * 2 + ["remaining"] * 32)
                facts = {"harness": harness, "eventSeq": 2, **self.facts}
                if harness == "codex":
                    facts.update(finalMessageCompleted=True, nativeTurnStarted=True, nativeTurnCompleted=True,
                                 nativeThreadId="native-session", finalItemId="final-item")
                else:
                    facts.update(resultIsError=False, resultSubtype="success", structuredOutputSource="json-schema",
                                 initObserved=True, backgroundSettled=True, nativeSessionId="native-session",
                                 modelUsage=[], permissionDenials=0)
                record, _raw = self.record(self.result(harness, value, facts=facts))
                self.assertEqual(record["outcomeTruncated"], {
                    "decisions": {"droppedItems": 5, "shortenedItems": 1},
                    "remaining": {"droppedItems": 2, "shortenedItems": 2}})
                self.assertEqual(len(record["outcome"]["decisions"]), 32)
                self.assertEqual(len(record["outcome"]["remaining"]), 32)
                self.assertIsNone(turn_io.validate_outcome(record["outcome"]))
                policy = worker_format(harness)
                self.assertEqual(record["provenance"],
                                 {**facts, "turnEnd": "completed", policy.validation_key: True})
    def test_untruncated_record_is_byte_identical_and_has_no_metadata(self):
        for harness in ("codex", "claude"):
            with self.subTest(harness=harness):
                value = outcome(decisions=["汉" * 1365 + "a"], remaining=["item"] * 32)
                record, raw = self.record(self.result(harness, value))
                expected = {"version": 1, **self.turn, "inputSha256": turn_io.input_hash(self.turn),
                    "promptSha256": hashlib.sha256(b"prompt").hexdigest(), "sessionId": "native-session",
                    "outcome": value, "provenance": {**self.facts, "turnEnd": "completed",
                                                       worker_format(harness).validation_key: True}}
                self.assertEqual(raw, canonical_json(expected).encode())
                self.assertNotIn("outcomeTruncated", record)

    def test_other_validation_failures_are_not_hidden_by_truncation(self):
        invalid = [outcome(summary=" "), outcome(extra=True), outcome(disposition="unknown"),
                   outcome(request={}), outcome(artifacts=["artifact"] * 33),
                   outcome(artifacts=["a" * 4097]), outcome(remaining="not an array"),
                   outcome(disposition="assistance", request={"summary": "help"}),
                   outcome(decisions=["d"] * 32 + [None]),
                   outcome(decisions=["d"] * 32 + [" "]),
                   outcome(decisions=["d" * 4097 + "\0"])]
        for harness in ("codex", "claude"):
            for i, value in enumerate(invalid):
                with self.subTest(harness=harness, case=i):
                    value["remaining"] = value["remaining"] or ["remaining"] * 33
                    with self.assertRaises(BoardError) as caught:
                        self.record(self.result(harness, value))
                    self.assertEqual(caught.exception.code, "invalid-result")
                    self.assertFalse((self.root / "output.json").exists())

    def test_complete_turn_record_is_bounded_before_publication(self):
        result = self.result("codex", outcome())
        oversized = {"nativeData": "x" * turn_io.MAX_RECORD_BYTES}
        with mock.patch.object(run_execution, "worker_delivery", return_value=(outcome(), oversized, {})):
            with self.assertRaises(BoardError) as caught:
                self.record(result)
        self.assertEqual(caught.exception.code, "invalid-result")
        self.assertIn("byte bound", caught.exception.message)
        self.assertFalse((self.root / "output.json").exists())

    def test_original_overall_byte_bound_remains_strict(self):
        for harness in ("codex", "claude"):
            for value in (outcome(summary="s" * 65536, decisions=["d"] * 33),
                          outcome(decisions=["d" * 65536])):
                with self.subTest(harness=harness, value_bytes=len(canonical_json(value).encode())):
                    with self.assertRaises(BoardError):
                        self.record(self.result(harness, value))

    def test_valid_help_request_survives_summary_truncation(self):
        request = {"summary": "help", "attempted": "tried", "neededWork": "decide",
                   "expectedArtifacts": [], "acceptance": "decided", "suggestedBuddyId": None}
        for harness in ("codex", "claude"):
            for disposition in ("assistance", "attention"):
                with self.subTest(harness=harness, disposition=disposition):
                    value = outcome(disposition=disposition, request=request, decisions=["d"] * 33)
                    record, _raw = self.record(self.result(harness, value))
                    self.assertEqual(record["outcome"]["request"], request)
                    self.assertEqual(record["outcome"]["disposition"], disposition)

    def test_receipt_fields_do_not_reparse_the_final_message(self):
        for harness in ("codex", "claude"):
            with self.subTest(harness=harness):
                policy = worker_format(harness)
                fields = policy.receipt_fields(self.result(harness, outcome(decisions=["d"] * 33)))
                self.assertNotIn("outcomeValidationError", fields)

    def test_controller_attention_replaces_outcome_without_stale_truncation_metadata(self):
        for harness, facts in (("codex", {"nativeRequestMethod": "interaction/request"}),
                               ("claude", {"permissionDenials": 1})):
            with self.subTest(harness=harness):
                record, _raw = self.record(self.result(harness, outcome(decisions=["d"] * 33), facts=facts))
                self.assertEqual(record["outcome"]["disposition"], "attention")
                self.assertNotIn("outcomeTruncated", record)

    def test_attention_without_denied_operation_fact_keeps_the_exact_original_outcome(self):
        for harness, facts, summary, attempted in (
                ("codex", {"nativeRequestMethod": "interaction/request"},
                 "Codex requested Host interaction through interaction/request",
                 "The owned controller declined the correlated native request without granting access"),
                ("claude", {"deniedControlRequestIds": ["denied-1"], "deniedToolNames": ["Bash"]},
                 "Claude requested Host interaction through a native tool permission (Bash)",
                 "The owned controller denied the correlated native permission request without granting access"),
                ("claude", {"permissionDenials": 1},
                 "Claude denied 1 native tool permission request(s) during the turn",
                 "The owned controller denied the correlated native permission request without granting access")):
            with self.subTest(harness=harness, facts=facts):
                expected = {"disposition": "attention", "summary": summary, "remaining": [], "decisions": [],
                    "artifacts": [], "request": {"summary": summary, "attempted": attempted,
                        "neededWork": "Host must review the requested action and choose a permitted continuation",
                        "expectedArtifacts": [],
                        "acceptance": "The Host resolves this boundary and starts an authorized continuation"}}
                self.assertEqual(canonical_json(worker_format(harness).attention_outcome(facts)), canonical_json(expected))

    def test_completion_tools_refuse_oversize_and_accept_corrected_retry(self):
        configuration = {"identity": self.turn, "inputSha256": "a" * 64, "key": "b" * 64,
                         "attentionPath": str(self.root / "attention.json")}
        for field in ("remaining", "decisions"):
            for items in (["item"] * 33, ["汉" * 1366]):
                with self.subTest(field=field, item_count=len(items)):
                    value = outcome(**{field: items})
                    raw = canonical_json(value)
                    refused = worker_services.call_session_tool("hey_my_buddy_finish_turn", value, configuration)
                    self.assertTrue(refused["isError"])
                    self.assertEqual(json.loads(refused["content"][0]["text"])["reason"], "invalid-arguments")
                    self.assertEqual(canonical_json(value), raw)
                    corrected = outcome(**{field: ["short"]})
                    accepted = worker_services.call_session_tool("hey_my_buddy_finish_turn", corrected, configuration)
                    receipt = verify_receipt(accepted["content"][0]["text"], configuration, turn_io.validate_outcome)
                    self.assertEqual(receipt["outcome"], corrected)
                    self.assertNotIn("outcomeTruncated", receipt)

    def test_completion_tool_role_delivery_is_unchanged_and_still_strict(self):
        for harness in ("dsh", "zcode"):
            with self.subTest(harness=harness):
                value = outcome()
                record, _raw = self.record(self.result(harness, value))
                self.assertEqual(record["outcome"], value)
                self.assertEqual(record["provenance"], self.facts)
                self.assertNotIn("outcomeTruncated", record)
                for invalid in (outcome(decisions=["d"] * 33), outcome(remaining=["r" * 4097])):
                    with self.assertRaises(BoardError) as caught:
                        run_execution.worker_delivery(self.result(harness, invalid))
                    self.assertEqual(caught.exception.code, "INVALID_ARGUMENT")
