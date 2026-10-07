"""The shared session-receipt verification every native protocol runs.

One implementation in :mod:`hey_my_buddy.buddy.harnesses.session_receipts`
decides the finish receipt, the signed tool-refusal envelope and the inquiry
receipts for the Worker role's session tools and both native drivers (the
ADR-025 step-5 cleanup): signature, attempt-identity binding, field sets, byte
budgets and refusal semantics are pinned here once, and the ZCode and DSH
protocol modules only convert :class:`ReceiptError` into their own failure
type. The drivers' genuinely registered judgment differences are carried as
:data:`BOUNDED_REFUSAL_DETAIL` / :data:`TYPED_REFUSAL_DETAIL` rule bundles the
core reads; the common stages run under both bundles here and
``RuleDifferenceTests`` pins each side's preserved difference. The
harness-specific native-stream authority — root identity, tool-call
association, stream integrity — stays in each harness's own suite.
"""
from __future__ import annotations

import json
import unittest

from hey_my_buddy.buddy.harnesses.session_receipts import (
    BOUNDED_REFUSAL_DETAIL,
    MAX_INQUIRY_RECEIPT_BYTES,
    MAX_TOOL_REFUSAL_BYTES,
    MAX_TOOL_REFUSAL_DETAIL_BYTES,
    MAX_TOOL_REFUSAL_PREFIX_BYTES,
    ReceiptError,
    TYPED_REFUSAL_DETAIL,
    refusal_shaped,
    sign_receipt,
    verify_inquiry_receipt,
    verify_receipt,
    verify_tool_refusal,
)
from hey_my_buddy.buddy.roles.session_mcp import respond
from hey_my_buddy.buddy.roles.turn_io import validate_outcome
from hey_my_buddy.json_codec import decode_strict_json

# The session tools one run actually mounts; refusal envelopes verify only
# against this set, never a fixed global one.
MOUNTED_TOOLS = ("buddy_checkpoint", "buddy_answer_inquiry", "buddy_finish_turn")

#: The two registered rule bundles, exercised under their registered names so
#: a common stage can never silently standardize one driver onto the other.
RULE_BUNDLES = (("bounded", BOUNDED_REFUSAL_DETAIL), ("typed", TYPED_REFUSAL_DETAIL))


class ReceiptTests(unittest.TestCase):
    def setUp(self):
        self.bridge = {"identity": {"taskId": "goal", "attemptId": "attempt", "generation": 1, "turnId": "logical"},
                       "inputSha256": "a" * 64, "key": "b" * 64}
        self.outcome = {"disposition": "completed", "summary": "done", "remaining": [], "decisions": [], "artifacts": [], "request": None}

    def receipt(self):
        result = respond({"id": 1, "method": "tools/call", "params": {"name": "buddy_finish_turn", "arguments": self.outcome}}, self.bridge)
        self.assertNotIn("structuredContent", result["result"])
        return result["result"]["content"][0]["text"]

    def test_bridge_receipt_is_bound_to_input_identity_and_outcome(self):
        raw = self.receipt()
        self.assertEqual(verify_receipt(raw, self.bridge, validate_outcome)["outcome"], self.outcome)
        for key, value in (("inputSha256", "c" * 64), ("outcome", {**self.outcome, "summary": "forged"}),
                           ("identity", {**self.bridge["identity"], "generation": 2})):
            with self.subTest(key=key):
                changed = json.loads(raw)
                changed[key] = value
                with self.assertRaises(ReceiptError):
                    verify_receipt(json.dumps(changed), self.bridge, validate_outcome)
        other_attempt = {**self.bridge, "identity": {**self.bridge["identity"], "attemptId": "other"}}
        with self.assertRaises(ReceiptError):
            verify_receipt(raw, other_attempt, validate_outcome)

    def test_prose_duplicate_members_and_nonfinite_values_are_rejected(self):
        raw = self.receipt()
        for invalid in ("Here is the result: " + raw, "```json\n" + raw + "\n```", raw + raw):
            with self.assertRaises(ReceiptError):
                verify_receipt(invalid, self.bridge, validate_outcome)
        for invalid in ('{"a":1,"a":2}', '{"a":NaN}', '{"a":Infinity}'):
            with self.assertRaises(ValueError):
                decode_strict_json(invalid)

    def test_receipt_failures_distinguish_malformed_signature_identity_and_outcome(self):
        # Safe, bounded diagnostics: each stage names its own failure without
        # ever quoting the raw content.
        raw = self.receipt()
        malformed = {
            "prose": "the tool says no",
            "wrong object": json.dumps({"version": 1, "identity": {}, "not": "a receipt"}),
            "trailing data": raw + " trailing",
        }
        for name, content in malformed.items():
            with self.subTest(case=name):
                with self.assertRaises(ReceiptError) as error:
                    verify_receipt(content, self.bridge, validate_outcome)
                self.assertEqual(error.exception.code, "invalid-finish")
                message = str(error.exception)
                self.assertTrue(any(fragment in message for fragment in
                                    ("no bounded JSON receipt", "not the current signed receipt object", "malformed")), message)
        tampered = json.loads(raw)
        tampered["outcome"] = {**tampered["outcome"], "summary": "forged"}
        with self.assertRaises(ReceiptError) as error:
            verify_receipt(json.dumps(tampered), self.bridge, validate_outcome)
        self.assertIn("signature verification", str(error.exception))
        rekeyed = json.loads(raw)
        rekeyed.pop("signature")
        rekeyed["identity"] = {**rekeyed["identity"], "attemptId": "other"}
        rekeyed["signature"] = sign_receipt(rekeyed, self.bridge["key"])
        with self.assertRaises(ReceiptError) as error:
            verify_receipt(json.dumps(rekeyed), self.bridge, validate_outcome)
        self.assertIn("attempt-identity binding", str(error.exception))
        reoutcomed = json.loads(raw)
        reoutcomed.pop("signature")
        reoutcomed["outcome"]["disposition"] = "unknown"
        reoutcomed["signature"] = sign_receipt(reoutcomed, self.bridge["key"])
        with self.assertRaises(ReceiptError) as error:
            verify_receipt(json.dumps(reoutcomed), self.bridge, validate_outcome)
        self.assertIn("outcome validation", str(error.exception))


class InquiryReceiptTests(unittest.TestCase):
    """The receipt stages both drivers share, run under both registered bundles."""

    def setUp(self):
        self.bridge = {"identity": {"taskId": "goal", "attemptId": "attempt", "generation": 1, "turnId": "logical"},
                       "inputSha256": "a" * 64, "key": "b" * 64}

    def receipt(self, kind: str, payload: dict) -> str:
        receipt = {"version": 1, "kind": kind, "identity": self.bridge["identity"], "receiptId": "e" * 32, **payload}
        receipt["signature"] = sign_receipt(receipt, self.bridge["key"])
        return json.dumps(receipt)

    def test_checkpoint_and_answer_receipts_verify_and_bind_to_identity(self):
        for name, rules in RULE_BUNDLES:
            with self.subTest(rules=name):
                checkpoint = self.receipt("inquiry-checkpoint", {"inquiries": [
                    {"inquiryId": "q-1", "question": "what?", "questionSha256": "c" * 64, "state": "queued",
                     "askedAt": "2026-01-01T00:00:00Z"}]})
                verified = verify_inquiry_receipt(checkpoint, self.bridge, "inquiry-checkpoint", rules=rules)
                self.assertEqual(verified["inquiries"][0]["inquiryId"], "q-1")
                answer = self.receipt("inquiry-answer", {"inquiryId": "q-1", "questionSha256": "c" * 64, "answer": "this"})
                self.assertEqual(verify_inquiry_receipt(answer, self.bridge, "inquiry-answer", rules=rules)["answer"], "this")

    def test_forged_stale_or_malformed_receipts_are_rejected(self):
        for name, rules in RULE_BUNDLES:
            with self.subTest(rules=name):
                valid = self.receipt("inquiry-answer", {"inquiryId": "q-1", "questionSha256": "c" * 64, "answer": "this"})
                cases = {
                    "changed answer": json.dumps({**json.loads(valid), "answer": "that"}),
                    "changed signature": json.dumps({**json.loads(valid), "signature": "0" * 64}),
                    "foreign key": None,  # verified below with a different configuration
                    "foreign identity": json.dumps({**json.loads(valid),
                                                    "identity": {**self.bridge["identity"], "attemptId": "other"}}),
                    "wrong kind": json.dumps({**json.loads(valid), "kind": "inquiry-checkpoint"}),
                    "missing field": json.dumps({k: v for k, v in json.loads(valid).items() if k != "inquiryId"}),
                    "extra field": json.dumps({**json.loads(valid), "extra": 1}),
                    "oversized answer": self.receipt("inquiry-answer", {"inquiryId": "q-1", "questionSha256": "c" * 64,
                                                                        "answer": "x" * 4001}),
                    "bad hash": self.receipt("inquiry-answer", {"inquiryId": "q-1", "questionSha256": "not-hex", "answer": "x"}),
                    "prose": "the answer is: " + valid,
                }
                for case, raw in cases.items():
                    with self.subTest(case=case):
                        if raw is None:
                            with self.assertRaises(ReceiptError):
                                verify_inquiry_receipt(valid, {**self.bridge, "key": "d" * 64}, "inquiry-answer", rules=rules)
                            continue
                        with self.assertRaises(ReceiptError):
                            verify_inquiry_receipt(raw, self.bridge, "inquiry-answer", rules=rules)
                # A receipt still signed with this attempt's key but verified against
                # another attempt's identity fails the identity binding itself, not
                # only the signature — the isolated guard for the binding stage.
                other_attempt = {**self.bridge, "identity": {**self.bridge["identity"], "attemptId": "other"}}
                with self.assertRaises(ReceiptError) as error:
                    verify_inquiry_receipt(valid, other_attempt, "inquiry-answer", rules=rules)
                self.assertEqual(error.exception.code, "invalid-inquiry-receipt")
                self.assertIn("attempt-identity binding", str(error.exception))
                oversized = self.receipt("inquiry-checkpoint", {"inquiries": [
                    {"inquiryId": f"q-{index}", "question": "x" * 4000, "questionSha256": "c" * 64,
                     "state": "queued", "askedAt": "t"} for index in range(33)]})
                with self.assertRaises(ReceiptError):
                    verify_inquiry_receipt(oversized, self.bridge, "inquiry-checkpoint", rules=rules)

    def test_checkpoint_entries_must_be_well_formed(self):
        base = {"inquiryId": "q-1", "question": "what?", "questionSha256": "c" * 64, "state": "queued",
                "askedAt": "2026-01-01T00:00:00Z"}
        for name, rules in RULE_BUNDLES:
            for mutation in ({"state": "answered"}, {"question": " "}, {"questionSha256": "z" * 64},
                             {"inquiryId": ""}, {"askedAt": 5}, {"deliveredAt": 7}, {"unknownField": True}):
                with self.subTest(rules=name, mutation=mutation):
                    raw = self.receipt("inquiry-checkpoint", {"inquiries": [{**base, **mutation}]})
                    with self.assertRaises(ReceiptError):
                        verify_inquiry_receipt(raw, self.bridge, "inquiry-checkpoint", rules=rules)


class ToolRefusalEnvelopeTests(unittest.TestCase):
    """The signed refusal envelope itself: exact binding, fatal forgery, dispatch."""

    def setUp(self):
        self.bridge = {"identity": {"taskId": "goal", "attemptId": "attempt", "generation": 1, "turnId": "logical"},
                       "inputSha256": "a" * 64, "key": "b" * 64}
        self.names = {"buddy_finish_turn": "mcp__buddy_x__buddy_finish_turn",
                      "buddy_checkpoint": "mcp__buddy_x__buddy_checkpoint",
                      "buddy_answer_inquiry": "mcp__buddy_x__buddy_answer_inquiry"}

    def refusal(self, **overrides) -> str:
        envelope = {"version": 1, "kind": "tool-refusal", "identity": self.bridge["identity"],
                    "inputSha256": self.bridge["inputSha256"], "tool": "buddy_finish_turn",
                    "reason": "invalid-arguments", "detail": "the turn request references are invalid",
                    "receiptId": "f" * 32}
        key = overrides.pop("key", None) or self.bridge["key"]
        envelope.update(overrides)
        envelope["signature"] = sign_receipt(envelope, key)
        return json.dumps(envelope)

    def test_a_well_bound_envelope_verifies_for_exactly_its_tool(self):
        for name, rules in RULE_BUNDLES:
            with self.subTest(rules=name):
                raw = self.refusal()
                self.assertTrue(refusal_shaped(raw))
                envelope = verify_tool_refusal(raw, self.bridge, self.names["buddy_finish_turn"], MOUNTED_TOOLS, rules=rules)
                self.assertEqual(envelope["reason"], "invalid-arguments")
                self.assertEqual(envelope["tool"], "buddy_finish_turn")
                for bare, native in self.names.items():
                    if bare == "buddy_finish_turn":
                        continue
                    with self.subTest(native=native):
                        with self.assertRaises(ReceiptError) as error:
                            verify_tool_refusal(raw, self.bridge, native, MOUNTED_TOOLS, rules=rules)
                        self.assertEqual(error.exception.code, "invalid-tool-refusal")
                        self.assertIn("different session tool", str(error.exception))

    def test_the_native_wrapper_error_header_is_tolerated_but_only_bounded(self):
        # The installed wrapper delivers an MCP isError text as a successful
        # result prefixed with one plain header line. Dispatch tolerates exactly
        # that bounded framing; the signature still decides everything else.
        for name, rules in RULE_BUNDLES:
            with self.subTest(rules=name):
                raw = self.refusal()
                framed = "MCP tool returned an error:\n" + raw
                self.assertTrue(refusal_shaped(framed))
                envelope = verify_tool_refusal(framed, self.bridge, self.names["buddy_finish_turn"], MOUNTED_TOOLS, rules=rules)
                self.assertEqual(envelope["reason"], "invalid-arguments")
                tampered = "MCP tool returned an error:\n" + json.dumps({**json.loads(raw), "detail": "changed after signing"})
                with self.assertRaises(ReceiptError) as error:
                    verify_tool_refusal(tampered, self.bridge, self.names["buddy_finish_turn"], MOUNTED_TOOLS, rules=rules)
                self.assertIn("signature", str(error.exception))
                for not_shaped in ("x" * 300 + raw, framed + " trailing", "error: {" + raw,
                                   "MCP tool returned an error:\nnot json", " ", ""):
                    with self.subTest(case=not_shaped[:40]):
                        self.assertFalse(refusal_shaped(not_shaped))

    def test_tampered_foreign_or_unbounded_envelopes_stay_fatal(self):
        # The blank-detail and oversized-detail shapes are the drivers'
        # registered difference and are pinned per rule in RuleDifferenceTests;
        # every shape here is fatal under both registered bundles.
        for name, rules in RULE_BUNDLES:
            with self.subTest(rules=name):
                cases = {
                    "changed detail": json.dumps({**json.loads(self.refusal()), "detail": "changed after signing"}),
                    "changed signature": json.dumps({**json.loads(self.refusal()), "signature": "0" * 64}),
                    "foreign identity": self.refusal(identity={"taskId": "goal", "attemptId": "other",
                                                               "generation": 1, "turnId": "logical"}),
                    "foreign input": self.refusal(inputSha256="c" * 64),
                    "unknown reason": self.refusal(reason="quota-exhausted"),
                    "extra field": json.dumps({**json.loads(self.refusal()), "extra": 1}),
                    "missing field": json.dumps({k: v for k, v in json.loads(self.refusal()).items() if k != "reason"}),
                    "not an object": json.dumps([1, 2, 3]),
                }
                for case, raw in cases.items():
                    with self.subTest(case=case):
                        if case == "not an object":
                            self.assertFalse(refusal_shaped(raw))
                        with self.assertRaises(ReceiptError) as error:
                            verify_tool_refusal(raw, self.bridge, self.names["buddy_finish_turn"], MOUNTED_TOOLS, rules=rules)
                        self.assertEqual(error.exception.code, "invalid-tool-refusal")
                # Prose before a genuinely signed envelope is just framing: the prefix
                # never enters the signature, so the envelope still verifies and can
                # only ever mean "correct the call and retry".
                prose_framed = "the tool refused: " + self.refusal()
                self.assertTrue(refusal_shaped(prose_framed))
                self.assertEqual(verify_tool_refusal(prose_framed, self.bridge,
                                                     self.names["buddy_finish_turn"], MOUNTED_TOOLS,
                                                     rules=rules)["reason"], "invalid-arguments")
                with self.assertRaises(ReceiptError):
                    verify_tool_refusal(self.refusal(), {**self.bridge, "key": "d" * 64}, self.names["buddy_finish_turn"],
                                        MOUNTED_TOOLS, rules=rules)
                with self.assertRaises(ReceiptError):
                    verify_tool_refusal("x" * 70001, self.bridge, self.names["buddy_finish_turn"], MOUNTED_TOOLS, rules=rules)

    def test_dispatch_never_treats_a_receipt_or_prose_as_a_refusal(self):
        outcome = {"disposition": "completed", "summary": "done", "remaining": [], "decisions": [],
                   "artifacts": [], "request": None}
        result = respond({"id": 1, "method": "tools/call", "params": {"name": "buddy_finish_turn",
                                                                      "arguments": outcome}}, self.bridge)["result"]
        self.assertFalse(refusal_shaped(result["content"][0]["text"]))
        self.assertFalse(refusal_shaped(None))
        self.assertFalse(refusal_shaped("plain tool error text"))
        # A genuine MCP refusal is both isError and refusal-shaped, and verifies.
        refused = respond({"id": 1, "method": "tools/call", "params": {"name": "buddy_finish_turn",
                                                                       "arguments": {"disposition": "completed"}}}, self.bridge)["result"]
        self.assertTrue(refused.get("isError"))
        self.assertTrue(refusal_shaped(refused["content"][0]["text"]))
        envelope = verify_tool_refusal(refused["content"][0]["text"], self.bridge, self.names["buddy_finish_turn"],
                                       MOUNTED_TOOLS, rules=BOUNDED_REFUSAL_DETAIL)
        self.assertEqual(envelope["reason"], "invalid-arguments")


class RuleDifferenceTests(unittest.TestCase):
    """The drivers' registered judgment differences, kept unstandardized.

    One shared implementation carries both rules as data; each differing input
    keeps exactly the result its registered driver produced before the merge —
    accepted where that driver accepted it, rejected with that driver's own
    stage message where it rejected it. The reproducible original-vs-shared
    comparison for these shapes lives in the task's evidence directory.
    """

    def setUp(self):
        self.bridge = {"identity": {"taskId": "goal", "attemptId": "attempt", "generation": 1, "turnId": "logical"},
                       "inputSha256": "a" * 64, "key": "b" * 64}

    def refusal(self, detail: str) -> str:
        envelope = {"version": 1, "kind": "tool-refusal", "identity": self.bridge["identity"],
                    "inputSha256": self.bridge["inputSha256"], "tool": "buddy_finish_turn",
                    "reason": "invalid-arguments", "detail": detail, "receiptId": "f" * 32}
        envelope["signature"] = sign_receipt(envelope, self.bridge["key"])
        return json.dumps(envelope)

    def answer(self, inquiry_id: str) -> str:
        receipt = {"version": 1, "kind": "inquiry-answer", "identity": self.bridge["identity"],
                   "inquiryId": inquiry_id, "questionSha256": "c" * 64, "answer": "this", "receiptId": "e" * 32}
        receipt["signature"] = sign_receipt(receipt, self.bridge["key"])
        return json.dumps(receipt)

    def test_the_refusal_detail_keeps_each_registered_bound(self):
        # Blank, over-budget and NUL details: the bounded rule rejects with its
        # record message, the typed rule accepts them as its driver did.
        for detail in ("  ", "x" * (MAX_TOOL_REFUSAL_DETAIL_BYTES + 1), "broken\0detail"):
            with self.subTest(detail=detail[:20]):
                raw = self.refusal(detail)
                with self.assertRaises(ReceiptError) as error:
                    verify_tool_refusal(raw, self.bridge, "mcp__buddy_x__buddy_finish_turn", MOUNTED_TOOLS,
                                        rules=BOUNDED_REFUSAL_DETAIL)
                self.assertIn("bounded reason validation", str(error.exception))
                envelope = verify_tool_refusal(raw, self.bridge, "mcp__buddy_x__buddy_finish_turn", MOUNTED_TOOLS,
                                               rules=TYPED_REFUSAL_DETAIL)
                self.assertEqual(envelope["detail"], detail)

    def test_an_unusable_native_tool_name_fails_at_its_registered_stage(self):
        raw = self.refusal("the turn request references are invalid")
        for native_tool in (None, 42):
            with self.subTest(native_tool=native_tool):
                with self.assertRaises(ReceiptError) as error:
                    verify_tool_refusal(raw, self.bridge, native_tool, MOUNTED_TOOLS, rules=TYPED_REFUSAL_DETAIL)
                self.assertIn("no bounded JSON refusal envelope", str(error.exception))
                with self.assertRaises(ReceiptError) as error:
                    verify_tool_refusal(raw, self.bridge, native_tool, MOUNTED_TOOLS, rules=BOUNDED_REFUSAL_DETAIL)
                self.assertIn("different session tool", str(error.exception))

    def test_inquiry_ids_keep_their_registered_blank_rule(self):
        # A whitespace-only id: accepted where the rule judges bytes, rejected
        # where the rule strips; the empty and the over-bound id are refused by
        # both rules, each with its own driver's original stage message.
        with self.assertRaises(ReceiptError) as error:
            verify_inquiry_receipt(self.answer("  "), self.bridge, "inquiry-answer", rules=TYPED_REFUSAL_DETAIL)
        self.assertIn("answer binding", str(error.exception))
        self.assertEqual(verify_inquiry_receipt(self.answer("  "), self.bridge, "inquiry-answer",
                                                rules=BOUNDED_REFUSAL_DETAIL)["inquiryId"], "  ")
        for rules in (BOUNDED_REFUSAL_DETAIL, TYPED_REFUSAL_DETAIL):
            for inquiry_id in ("", "q" * 129):
                with self.subTest(rules=rules.detail_bounded, id_len=len(inquiry_id)):
                    with self.assertRaises(ReceiptError) as error:
                        verify_inquiry_receipt(self.answer(inquiry_id), self.bridge, "inquiry-answer", rules=rules)
                    self.assertIn("answer binding", str(error.exception))


class RefusalWireBudgetTests(unittest.TestCase):
    """Every envelope this MCP mints fits the wire budget its verification enforces.

    The review reproduced a real mismatch: raw UTF-8 length does not bound
    canonical JSON escaping (a control character costs six bytes, a quote or
    backslash two) and the native wrapper adds its error header, so a minted
    refusal could exceed the very budget ``verify_tool_refusal`` enforces. The
    budgeted mint is checked under the bounded-detail rule, the bundle that
    owns those budgets.
    """

    def setUp(self):
        self.bridge = {"identity": {"taskId": "goal", "attemptId": "attempt", "generation": 1, "turnId": "logical"},
                       "inputSha256": "a" * 64, "key": "b" * 64}
        self.finish = "mcp__buddy_x__buddy_finish_turn"

    def mint(self, detail: str, *, tool: str = "buddy_finish_turn", reason: str = "invalid-arguments") -> str:
        from hey_my_buddy.buddy.roles.worker_services import _refusal
        return _refusal(self.bridge, tool, reason, detail)["content"][0]["text"]

    def test_pathological_details_mint_envelopes_that_verify_byte_for_byte(self):
        cases = {
            "quotes (review repro)": '"' * 65536,
            "backslashes": "\\" * 65536,
            "c0 controls": chr(1) * 65536,
            "mixed escapes": '\\"世🎉\x01\x1f' * 6000,
            "non-ascii literal": "世" * 21845,
            "plain at the raw bound": "x" * MAX_TOOL_REFUSAL_DETAIL_BYTES,
            "plain past the raw bound": "x" * (MAX_TOOL_REFUSAL_DETAIL_BYTES + 5000),
        }
        for name, detail in cases.items():
            with self.subTest(case=name):
                text = self.mint(detail)
                self.assertLessEqual(len(text.encode()) + MAX_TOOL_REFUSAL_PREFIX_BYTES, MAX_TOOL_REFUSAL_BYTES)
                envelope = verify_tool_refusal(text, self.bridge, self.finish, MOUNTED_TOOLS,
                                               rules=BOUNDED_REFUSAL_DETAIL)
                self.assertTrue(envelope["detail"].strip())
                # The kept detail is a prefix of the original, so the leading
                # correction guidance survives and nothing is rewritten.
                self.assertTrue(detail.startswith(envelope["detail"]), name)

    def test_the_review_repro_wire_sizes_now_verify(self):
        text = self.mint('"' * 65536)
        envelope = verify_tool_refusal(text, self.bridge, self.finish, MOUNTED_TOOLS, rules=BOUNDED_REFUSAL_DETAIL)
        # The unfixed mint emitted 131,450 wire bytes and was refused; the
        # fitted mint now uses most of the budget without exceeding it.
        self.assertGreater(len(text.encode()), MAX_TOOL_REFUSAL_BYTES // 2)
        self.assertEqual(envelope["reason"], "invalid-arguments")

    def test_a_whitespace_detail_falls_back_to_the_retry_instruction(self):
        envelope = verify_tool_refusal(self.mint(" " * 70000), self.bridge, self.finish, MOUNTED_TOOLS,
                                       rules=BOUNDED_REFUSAL_DETAIL)
        self.assertIn("correct it and retry", envelope["detail"])

    def test_checkpoint_receipts_batch_serialized_overflow_explicitly(self):
        from hey_my_buddy.buddy.roles.worker_services import _checkpoint_batch
        from hey_my_buddy.buddy.roles.turn_io import canonical_json
        # 32 accepted-but-pathological questions (control characters expand
        # sixfold) cannot fit one receipt: the batch is the longest serialized
        # prefix and the overflow is counted, never silently dropped.
        pathological = [{"inquiryId": f"q-{index}", "question": chr(1) * 4000, "questionSha256": "c" * 64,
                         "state": "queued", "askedAt": "t"} for index in range(32)]
        batch, more = _checkpoint_batch(pathological)
        self.assertLess(len(batch), 32)
        self.assertEqual(more, 32 - len(batch))
        self.assertLessEqual(len(canonical_json(batch).encode()), MAX_INQUIRY_RECEIPT_BYTES - 1024)
        # Realistic questions at the question budget all fit one receipt.
        realistic = [{"inquiryId": f"q-{index}", "question": "y" * 3990, "questionSha256": "c" * 64,
                      "state": "queued", "askedAt": "t"} for index in range(32)]
        batch, more = _checkpoint_batch(realistic)
        self.assertEqual((len(batch), more), (32, 0))


if __name__ == "__main__":
    unittest.main()
