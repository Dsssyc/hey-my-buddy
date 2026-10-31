"""Shared cooperative session tools retained for DSH after ZCode moves to guide."""
import hashlib
import json
import unittest
from pathlib import Path
import tempfile
from hey_my_buddy.buddy.roles.session_mcp import respond
from hey_my_buddy.buddy.roles import turn_io
from hey_my_buddy.buddy.roles.worker_services import pending_inquiries, read_inquiry_entries, attention_requests
from hey_my_buddy.buddy.harnesses.session_receipts import (MAX_ANSWER_BYTES, MAX_INQUIRIES, ReceiptError,
    sign_receipt, verify_inquiry_receipt, verify_receipt, verify_tool_refusal)
from hey_my_buddy.protocol.inquiry_journal import MAX_JOURNAL_BYTES
from hey_my_buddy.paths import native_path
IDENTITY = {"taskId": "task-1", "attemptId": "attempt-1", "generation": 1, "turnId": "turn-1"}
OTHER_IDENTITY = {"taskId": "task-2", "attemptId": "attempt-9", "generation": 1, "turnId": "turn-9"}


class FinishToolTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="hey-my-buddy-zcode-finish-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.attention = self.root / "attention.json"
        self.journal = self.root / "inquiry.results.jsonl"
        self.config = {"identity": IDENTITY, "inputSha256": "a" * 64, "key": "b" * 64,
                       "attentionPath": str(self.attention), "inquiryJournalPath": str(self.journal)}

    def call(self, name: str, arguments: dict) -> dict:
        return respond({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                        "params": {"name": name, "arguments": arguments}}, self.config)["result"]

    def refusal(self, result: dict, tool: str) -> dict:
        """Verify the isError text as the signed refusal envelope it must be."""
        self.assertTrue(result.get("isError"), result)
        return verify_tool_refusal(result["content"][0]["text"], self.config, f"mcp__buddy_x__{tool}",
                                              ("hey_my_buddy_checkpoint", "hey_my_buddy_answer_inquiry", "hey_my_buddy_finish_turn"))

    @staticmethod
    def outcome(disposition: str) -> dict:
        request = None if disposition == "completed" else {
            "summary": "help", "attempted": "tried", "neededWork": "decide",
            "expectedArtifacts": [], "acceptance": "decided"}
        return {"disposition": disposition, "summary": "fixture", "remaining": [], "decisions": [],
                "artifacts": [], "request": request}

    def queue(self, inquiry_id="q-1", question="what is the deployment word?", *, identity=IDENTITY) -> None:
        import hashlib

        self.journal.parent.mkdir(parents=True, exist_ok=True)
        with self.journal.open("a", encoding="utf-8", newline="\n") as stream:
            stream.write(json.dumps({"version": 1, "inquiryId": inquiry_id, "state": "queued", "question": question,
                                     "questionSha256": hashlib.sha256(question.encode()).hexdigest(),
                                     "askedAt": "2026-01-01T00:00:00Z",
                                     "taskId": identity["taskId"], "attemptId": identity["attemptId"],
                                     "generation": identity["generation"], "turnId": identity["turnId"]}) + "\n")

    def test_all_three_session_tools_are_exposed(self):
        listed = respond({"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}, self.config)
        names = [tool["name"] for tool in listed["result"]["tools"]]
        self.assertEqual(names, ["hey_my_buddy_checkpoint", "hey_my_buddy_answer_inquiry", "hey_my_buddy_finish_turn"])

    def test_checkpoint_returns_a_signed_receipt_of_the_pending_questions(self):
        self.queue()
        result = self.call("hey_my_buddy_checkpoint", {})
        self.assertFalse(result.get("isError"), result)
        receipt = verify_inquiry_receipt(result["content"][0]["text"], self.config, "inquiry-checkpoint")
        self.assertEqual(receipt["inquiries"][0]["inquiryId"], "q-1")
        self.assertEqual(receipt["inquiries"][0]["question"], "what is the deployment word?")
        # The handler is read-only, so another checkpoint still exposes the same
        # unanswered question under an independent receipt.
        again = self.call("hey_my_buddy_checkpoint", {})
        second = verify_inquiry_receipt(again["content"][0]["text"], self.config, "inquiry-checkpoint")
        self.assertEqual(second["inquiries"], receipt["inquiries"])
        self.assertNotEqual(second["receiptId"], receipt["receiptId"])

    def test_a_foreign_journal_identity_is_never_exposed(self):
        self.queue("q-foreign", "other attempt question?", identity=OTHER_IDENTITY)
        result = self.call("hey_my_buddy_checkpoint", {})
        receipt = verify_inquiry_receipt(result["content"][0]["text"], self.config, "inquiry-checkpoint")
        self.assertEqual(receipt["inquiries"], [])
        refused = self.call("hey_my_buddy_answer_inquiry", {"inquiryId": "q-foreign", "answer": "nope"})
        envelope = self.refusal(refused, "hey_my_buddy_answer_inquiry")
        self.assertEqual(envelope["reason"], "unknown-inquiry")
        self.assertIn("unknown inquiryId", envelope["detail"])

    def test_answer_receipts_are_bound_and_bounded(self):
        self.queue()
        accepted = self.call("hey_my_buddy_answer_inquiry", {"inquiryId": "q-1", "answer": "deploy-ok"})
        self.assertFalse(accepted.get("isError"), accepted)
        receipt = verify_inquiry_receipt(accepted["content"][0]["text"], self.config, "inquiry-answer")
        self.assertEqual(receipt["answer"], "deploy-ok")
        for arguments, fragment, reason in (
            ({"inquiryId": "q-1", "answer": "  "}, "nonblank", "invalid-arguments"),
            ({"inquiryId": "q-1", "answer": "x" * 4001}, "4000-byte", "invalid-arguments"),
            ({"inquiryId": "q-missing", "answer": "fine"}, "unknown inquiryId", "unknown-inquiry"),
            ({"inquiryId": "q-1"}, "exactly", "invalid-arguments"),
            ({}, "exactly", "invalid-arguments"),
        ):
            with self.subTest(fragment=fragment):
                refused = self.call("hey_my_buddy_answer_inquiry", arguments)
                envelope = self.refusal(refused, "hey_my_buddy_answer_inquiry")
                self.assertEqual(envelope["reason"], reason)
                self.assertIn(fragment, envelope["detail"])
        # A tampered receipt must fail controller verification.
        tampered = json.loads(accepted["content"][0]["text"])
        tampered["answer"] = "changed after signing"
        with self.assertRaises(ReceiptError):
            verify_inquiry_receipt(json.dumps(tampered), self.config, "inquiry-answer")
        with self.assertRaises(ReceiptError):
            verify_inquiry_receipt(accepted["content"][0]["text"], {**self.config, "key": "d" * 64}, "inquiry-answer")

    def test_an_answered_inquiry_cannot_be_reanswered(self):
        import hashlib

        question = "what is the deployment word?"
        answered = {"version": 1, "inquiryId": "q-1", "state": "answered", "question": question,
                    "questionSha256": hashlib.sha256(question.encode()).hexdigest(),
                    "answer": {"text": "deploy-ok", "bytes": 10, "truncated": False,
                               "via": "tool:hey_my_buddy_answer_inquiry", "toolCallId": "root-call",
                               "at": "2026-01-01T00:00:00Z"}, **IDENTITY}
        with self.journal.open("a", encoding="utf-8", newline="\n") as stream:
            stream.write(json.dumps(answered) + "\n")
        refused = self.call("hey_my_buddy_answer_inquiry", {"inquiryId": "q-1", "answer": "another"})
        self.assertTrue(refused["isError"])
        self.assertIn("already has its recorded answer", refused["content"][0]["text"])

    def test_completed_is_refused_while_a_question_is_unanswered(self):
        self.queue()
        refused = self.call("hey_my_buddy_finish_turn", self.outcome("completed"))
        envelope = self.refusal(refused, "hey_my_buddy_finish_turn")
        self.assertEqual(envelope["reason"], "inquiry-pending")
        self.assertIn("what is the deployment word?", envelope["detail"])
        self.assertIn("q-1", envelope["detail"])
        self.assertIn("hey_my_buddy_checkpoint", envelope["detail"])
        # Assistance and attention stay legal with a pending question.
        for disposition in ("assistance", "attention"):
            with self.subTest(disposition=disposition):
                accepted = self.call("hey_my_buddy_finish_turn", self.outcome(disposition))
                self.assertFalse(accepted.get("isError"), accepted)
        # Once answered, completion passes again.
        import hashlib

        question = "what is the deployment word?"
        with self.journal.open("a", encoding="utf-8", newline="\n") as stream:
            stream.write(json.dumps({"version": 1, "inquiryId": "q-1", "state": "answered", "question": question,
                                     "questionSha256": hashlib.sha256(question.encode()).hexdigest(),
                                     "answer": {"text": "deploy-ok", "bytes": 10, "truncated": False,
                                                "via": "tool:hey_my_buddy_answer_inquiry", "toolCallId": "root-call",
                                                "at": "2026-01-01T00:00:00Z"}, **IDENTITY}) + "\n")
        settled = self.call("hey_my_buddy_finish_turn", self.outcome("completed"))
        self.assertFalse(settled.get("isError"), settled)
        verify_receipt(settled["content"][0]["text"], self.config, turn_io.validate_outcome)

    def test_foreign_or_unversioned_journal_records_are_never_exposed(self):
        import hashlib

        question = "what is the deployment word?"
        records = [
            # Current format, this attempt: replays.
            {"version": 1, "inquiryId": "q-mine", "state": "queued", "question": question,
             "questionSha256": hashlib.sha256(question.encode()).hexdigest(), "askedAt": "t", **IDENTITY},
            # Foreign attempt identity: ignored even with the current version.
            {"version": 1, "inquiryId": "q-foreign", "state": "answered", "question": "other",
             "questionSha256": "a" * 64, "askedAt": "t", **OTHER_IDENTITY,
             "answer": {"text": "foreign", "bytes": 7, "truncated": False, "via": "x", "toolCallId": "c", "at": "t"}},
            # Missing identity fields: unbound, ignored.
            {"version": 1, "inquiryId": "q-unbound", "state": "queued", "question": "no identity",
             "questionSha256": hashlib.sha256(b"after the crash?").hexdigest(), "askedAt": "t"},
            # Old/unversioned record shape: no fallback, ignored.
            {"inquiryId": "q-old", "state": "answered", "question": "legacy", "questionSha256": "c" * 64,
             "askedAt": "t", **IDENTITY, "answer": {"text": "legacy", "bytes": 6, "truncated": False,
                                                    "via": "x", "toolCallId": "c", "at": "t"}},
        ]
        with self.journal.open("a", encoding="utf-8", newline="\n") as stream:
            stream.write("not json at all\n" + "".join(json.dumps(record) + "\n" for record in records))
        result = self.call("hey_my_buddy_checkpoint", {})
        receipt = verify_inquiry_receipt(result["content"][0]["text"], self.config, "inquiry-checkpoint")
        self.assertEqual([item["inquiryId"] for item in receipt["inquiries"]], ["q-mine"])
        for inquiry_id in ("q-foreign", "q-unbound", "q-old"):
            with self.subTest(inquiryId=inquiry_id):
                refused = self.call("hey_my_buddy_answer_inquiry", {"inquiryId": inquiry_id, "answer": "x"})
                self.assertTrue(refused["isError"])
                self.assertIn("unknown inquiryId", refused["content"][0]["text"])
        # A finish is not blocked by a foreign "answered" or unversioned record.
        completed = self.call("hey_my_buddy_finish_turn", self.outcome("completed"))
        self.assertTrue(completed["isError"], "q-mine still blocks completion")
        with self.journal.open("a", encoding="utf-8", newline="\n") as stream:
            stream.write(json.dumps({"version": 1, "inquiryId": "q-mine", "state": "answered",
                                     "question": question, "questionSha256": hashlib.sha256(question.encode()).hexdigest(),
                                     "askedAt": "t", **IDENTITY,
                                     "answer": {"text": "done", "bytes": 4, "truncated": False,
                                                "via": "tool:hey_my_buddy_answer_inquiry", "toolCallId": "c", "at": "t"}}) + "\n")
        settled = self.call("hey_my_buddy_finish_turn", self.outcome("completed"))
        self.assertFalse(settled.get("isError"), settled)

    def test_pending_inquiries_is_none_without_a_journal(self):
        bare = {k: v for k, v in self.config.items() if k != "inquiryJournalPath"}
        self.assertIsNone(pending_inquiries(bare))
        checkpoint = respond({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                              "params": {"name": "hey_my_buddy_checkpoint", "arguments": {}}}, bare)["result"]
        self.assertTrue(checkpoint["isError"])
        refused = respond({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                           "params": {"name": "hey_my_buddy_answer_inquiry",
                                      "arguments": {"inquiryId": "q-1", "answer": "x"}}}, bare)["result"]
        self.assertTrue(refused["isError"])

    def test_completed_is_refused_while_a_native_request_is_unresolved(self):
        self.assertEqual(attention_requests(self.config), 0)
        self.attention.write_text(json.dumps({"version": 1, "requests": [
            {"kind": "unsupported-native-request", "method": "interaction/requestPermission"}]}), encoding="utf-8", newline="\n")
        self.assertEqual(attention_requests(self.config), 1)
        refused = self.call("hey_my_buddy_finish_turn", self.outcome("completed"))
        envelope = self.refusal(refused, "hey_my_buddy_finish_turn")
        self.assertEqual(envelope["reason"], "attention-outstanding")
        self.assertIn("attention", envelope["detail"])
        attention = self.call("hey_my_buddy_finish_turn", self.outcome("attention"))
        self.assertFalse(attention.get("isError"), attention)
        receipt = verify_receipt(attention["content"][0]["text"], self.config, turn_io.validate_outcome)
        self.assertEqual(receipt["outcome"]["disposition"], "attention")

    def test_a_completed_outcome_still_verifies_without_attention(self):
        accepted = self.call("hey_my_buddy_finish_turn", self.outcome("completed"))
        receipt = verify_receipt(accepted["content"][0]["text"], self.config, turn_io.validate_outcome)
        self.assertEqual(receipt["outcome"]["disposition"], "completed")
        tampered = json.loads(accepted["content"][0]["text"])
        tampered["outcome"]["summary"] = "changed"
        with self.assertRaises(ReceiptError):
            verify_receipt(json.dumps(tampered), self.config, turn_io.validate_outcome)

    def test_unknown_tools_and_invalid_outcomes_are_bounded_errors(self):
        unknown = self.call("hey_my_buddy_something_else", {})
        self.assertTrue(unknown["isError"])
        invalid = self.call("hey_my_buddy_finish_turn", {"disposition": "completed"})
        envelope = self.refusal(invalid, "hey_my_buddy_finish_turn")
        self.assertEqual(envelope["reason"], "invalid-arguments")

    def test_expected_refusals_are_signed_envelopes_bound_to_attempt_input_and_tool(self):
        # Every expected refusal this MCP can mint is a signed envelope that the
        # controller can verify on a wrapper-successful result, bound to this
        # attempt, this input and exactly the tool that refused.
        self.queue()
        refusals = [
            ("hey_my_buddy_finish_turn", self.outcome("completed"), "inquiry-pending"),
            ("hey_my_buddy_finish_turn", {"disposition": "guessing"}, "invalid-arguments"),
            ("hey_my_buddy_answer_inquiry", {"inquiryId": "q-none", "answer": "x"}, "unknown-inquiry"),
        ]
        for tool, arguments, reason in refusals:
            with self.subTest(tool=tool, reason=reason):
                result = self.call(tool, arguments)
                envelope = self.refusal(result, tool)
                self.assertEqual(envelope["reason"], reason)
                self.assertEqual(envelope["kind"], "tool-refusal")
                self.assertEqual(envelope["identity"], self.config["identity"])
                self.assertEqual(envelope["inputSha256"], self.config["inputSha256"])
                # The envelope is bound to its exact tool: another tool's native
                # name must not verify it.
                with self.assertRaises(ReceiptError) as error:
                    verify_tool_refusal(result["content"][0]["text"], self.config, "mcp__buddy_x__hey_my_buddy_checkpoint"
                                        if tool != "hey_my_buddy_checkpoint" else "mcp__buddy_x__hey_my_buddy_finish_turn",
                                        ("hey_my_buddy_checkpoint", "hey_my_buddy_answer_inquiry", "hey_my_buddy_finish_turn"))
                self.assertEqual(error.exception.code, "invalid-tool-refusal")
        # A foreign attempt configuration cannot verify another attempt's refusal.
        foreign = {**self.config, "identity": OTHER_IDENTITY, "key": "e" * 64}
        with self.assertRaises(ReceiptError):
            verify_tool_refusal(self.call("hey_my_buddy_finish_turn", self.outcome("completed"))["content"][0]["text"],
                                foreign, "mcp__buddy_x__hey_my_buddy_finish_turn",
                                ("hey_my_buddy_checkpoint", "hey_my_buddy_answer_inquiry", "hey_my_buddy_finish_turn"))

    def test_an_explicit_null_suggested_buddy_is_the_accepted_no_suggestion(self):
        # The incident's finish arguments are now valid: an explicit null
        # suggestedBuddyId means no suggestion and mints a success receipt,
        # while a non-string value is refused with a signed envelope whose
        # detail names the correction.
        with_null = self.outcome("attention")
        with_null["request"]["suggestedBuddyId"] = None
        accepted = self.call("hey_my_buddy_finish_turn", with_null)
        self.assertFalse(accepted.get("isError"), accepted)
        receipt = verify_receipt(accepted["content"][0]["text"], self.config, turn_io.validate_outcome)
        self.assertIsNone(receipt["outcome"]["request"]["suggestedBuddyId"])
        for invalid in (5, True, {}, []):
            with self.subTest(invalid=invalid):
                bad = self.outcome("attention")
                bad["request"]["suggestedBuddyId"] = invalid
                envelope = self.refusal(self.call("hey_my_buddy_finish_turn", bad), "hey_my_buddy_finish_turn")
                self.assertEqual(envelope["reason"], "invalid-arguments")
                self.assertIn("references are invalid", envelope["detail"])

    def test_pathological_questions_batch_into_a_verifiable_checkpoint(self):
        # JSON escaping can inflate an accepted 4000-byte question far past
        # the raw length, so 32 control-character questions cannot fit one
        # receipt. The checkpoint mints the longest serialized batch that
        # verifies, counts the overflow explicitly, and every question stays
        # queued — the finish refusal still names them all by count.
        for index in range(32):
            self.queue(f"q-{index}", chr(1) * 4000)
        checkpoint = self.call("hey_my_buddy_checkpoint", {})
        self.assertFalse(checkpoint.get("isError"), checkpoint)
        receipt = verify_inquiry_receipt(checkpoint["content"][0]["text"], self.config, "inquiry-checkpoint")
        self.assertLess(len(receipt["inquiries"]), 32)
        self.assertEqual(receipt["morePending"], 32 - len(receipt["inquiries"]))
        refused = self.call("hey_my_buddy_finish_turn", self.outcome("completed"))
        envelope = self.refusal(refused, "hey_my_buddy_finish_turn")
        self.assertEqual(envelope["reason"], "inquiry-pending")
        self.assertIn("(+28 more)", envelope["detail"])
        self.assertIn("Call hey_my_buddy_checkpoint", envelope["detail"])

    def test_realistic_questions_all_fit_one_checkpoint_receipt(self):
        for index in range(32):
            self.queue(f"q-{index}", "What is the deployment word? " * 130)
        checkpoint = self.call("hey_my_buddy_checkpoint", {})
        receipt = verify_inquiry_receipt(checkpoint["content"][0]["text"], self.config, "inquiry-checkpoint")
        self.assertEqual(len(receipt["inquiries"]), 32)
        self.assertEqual(receipt.get("morePending", 0), 0)
        self.assertLessEqual(len(checkpoint["content"][0]["text"].encode()), 200 * 1024)
