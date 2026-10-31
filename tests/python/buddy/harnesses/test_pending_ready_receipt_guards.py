"""Pending inquiry conflicts and finish receipt digest bindings.

The endpoint guards pending request and question identifiers before an owner
commit. Receipt verification keeps its distinct identity and signature checks.
"""
from __future__ import annotations

import json
from pathlib import Path
import tempfile
import threading
import time
import unittest

from buddy.harnesses.test_c_two_live import (
    QUEUED_REPLY, decode_reply, endpoint, inquiry_request,
)
from hey_my_buddy.buddy.harnesses.session_receipts import ReceiptError, sign_receipt, verify_receipt
from hey_my_buddy.buddy.harnesses import live as lv
from hey_my_buddy.buddy.roles.session_mcp import respond
from hey_my_buddy.buddy.roles.turn_io import validate_outcome
from hey_my_buddy.errors import BoardError

FINISH_IDENTITY = {"taskId": "micro-task", "attemptId": "attempt", "generation": 1, "turnId": "logical"}
FINISH_KEY = "b" * 64
FINISH_OUTCOME = {"disposition": "completed", "summary": "done", "remaining": [],
                  "decisions": [], "artifacts": [], "request": None}


class QueuedSameRequestConflictTests(unittest.TestCase):
    """V-C5: a queued request id refuses changed content before any commit.

    The committed replay-or-conflict gate over settled request ids is the
    existing suite's fact; this is the other conflict site — the same request
    id still sitting in the pending admission map — and it must refuse on the
    payload digest alone, while the first delivery is still in flight and
    nothing at all has been committed.
    """

    def setUp(self):
        self.endpoint = endpoint()
        self.run = self.endpoint.identity

    def raw_ask(self, request, *, timeout_ms=3000):
        return self.endpoint.request_local(request, timeout_ms=timeout_ms,
            deadline_monotonic=time.monotonic() + timeout_ms / 1000.0)

    def ask(self, request, **kwargs):
        return decode_reply(self.raw_ask(request, **kwargs))

    def wait_pending(self, request_id, *, timeout=3.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with self.endpoint._lock:
                if request_id in self.endpoint._pending:
                    return
            time.sleep(0.005)
        raise AssertionError("the request never became pending")

    def test_a_queued_request_id_refuses_changed_content_without_disturbing_the_delivery(self):
        box: list[str] = []
        thread = threading.Thread(target=lambda: box.append(
            self.raw_ask(inquiry_request(question_id="question-1", question="original question"),
                         timeout_ms=5000)), daemon=True)
        thread.start()
        self.wait_pending("request-1")
        # Still queued, nothing committed: the conflict is judged against the
        # pending slot's digest, not any settled index.
        with self.endpoint._lock:
            self.assertEqual(sorted(self.endpoint._pending), ["request-1"])
        self.assertEqual(self.endpoint._admitted, {})
        conflict = self.ask(inquiry_request(question_id="question-1", question="changed question"),
                            timeout_ms=500)
        self.assertEqual((conflict.status, conflict.reason_code),
                         ("unavailable", "request-payload-conflict"))
        # The refused ask never joined the delivery: one slot, one queue entry,
        # and the original wait keeps waiting for its owner.
        self.assertEqual(self.endpoint._queue.qsize(), 1)
        with self.endpoint._lock:
            self.assertEqual(sorted(self.endpoint._pending), ["request-1"])
        self.assertEqual(self.endpoint._admitted, {})
        self.assertTrue(thread.is_alive(),
                        "the original delivery must still be in flight, waiting for its owner")
        consumed = self.endpoint.consume_request(1.0)
        self.assertEqual((consumed.request_id, consumed.payload.question),
                         ("request-1", "original question"))
        self.endpoint.settle_request("request-1", lv.LiveReply(**QUEUED_REPLY))
        thread.join(timeout=5.0)
        self.assertFalse(thread.is_alive())
        delivered = decode_reply(box[0])
        self.assertEqual((delivered.status, delivered.state), ("queued", "queued"))
        self.assertEqual(self.endpoint._requests, {"request-1": "question-1"})


class QueuedSameQuestionConflictTests(unittest.TestCase):
    """V-C6: a queued question id refuses changed content under a fresh request id.

    The committed question's own conflict is the existing suite's fact; this is
    the queued site — the same question already in flight under another request
    id — where a different payload must be refused instead of joining that
    delivery as a budgeted alias.
    """

    def setUp(self):
        self.endpoint = endpoint()
        self.run = self.endpoint.identity

    def raw_ask(self, request, *, timeout_ms=3000):
        return self.endpoint.request_local(request, timeout_ms=timeout_ms,
            deadline_monotonic=time.monotonic() + timeout_ms / 1000.0)

    def ask(self, request, **kwargs):
        return decode_reply(self.raw_ask(request, **kwargs))

    def wait_pending(self, request_id, *, timeout=3.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with self.endpoint._lock:
                if request_id in self.endpoint._pending:
                    return
            time.sleep(0.005)
        raise AssertionError("the request never became pending")

    def test_a_queued_question_id_refuses_changed_content_without_joining_the_delivery(self):
        box: list[str] = []
        thread = threading.Thread(target=lambda: box.append(
            self.raw_ask(inquiry_request(request_id="request-1", question_id="question-1",
                                         question="original question"), timeout_ms=5000)),
            daemon=True)
        thread.start()
        self.wait_pending("request-1")
        self.assertEqual(self.endpoint._admitted, {})
        conflict = self.ask(inquiry_request(request_id="request-2", question_id="question-1",
                                            question="changed question"), timeout_ms=500)
        self.assertEqual((conflict.status, conflict.reason_code),
                         ("unavailable", "question-payload-conflict"))
        # The refused alias never joined: the original delivery stays alone and
        # the fresh request id binds nothing.
        self.assertEqual(self.endpoint._queue.qsize(), 1)
        with self.endpoint._lock:
            self.assertEqual(sorted(self.endpoint._pending), ["request-1"])
        self.assertEqual(self.endpoint._admitted, {})
        self.assertTrue(thread.is_alive(),
                        "the original delivery must still be in flight, waiting for its owner")
        consumed = self.endpoint.consume_request(1.0)
        self.assertEqual(consumed.request_id, "request-1")
        self.endpoint.settle_request("request-1", lv.LiveReply(**QUEUED_REPLY))
        thread.join(timeout=5.0)
        self.assertFalse(thread.is_alive())
        self.assertEqual((decode_reply(box[0]).status, decode_reply(box[0]).state),
                         ("queued", "queued"))
        self.assertEqual(self.endpoint._requests, {"request-1": "question-1"})
        self.assertEqual(sorted(self.endpoint._pending), [])


def minted_finish_receipt(input_sha256: str) -> tuple[str, dict]:
    """One real signed finish receipt minted by the session tool itself."""
    with tempfile.TemporaryDirectory(prefix="finish-receipt-attention-") as private:
        bridge = {"identity": dict(FINISH_IDENTITY), "inputSha256": input_sha256, "key": FINISH_KEY,
                  "attentionPath": str(Path(private) / "absent-attention.json")}
        result = respond({"id": 1, "method": "tools/call",
                          "params": {"name": "hey_my_buddy_finish_turn", "arguments": FINISH_OUTCOME}}, bridge)
    return result["result"]["content"][0]["text"], bridge


class FinishReceiptDigestBindingTests(unittest.TestCase):
    """V-C7: the two digest bindings of the finish receipt, verified separately.

    A receipt signed over a foreign input digest must be refused at the
    attempt-identity binding stage even though its signature is valid for the
    content it carries; a receipt whose signature digest is wrong must be
    refused at the signature stage even though the execution identity and the
    input digest match exactly. The stages are distinct guards with distinct
    messages, so each test leaves the other stage intact.
    """

    def test_a_validly_signed_receipt_over_a_foreign_input_digest_fails_the_binding(self):
        raw, _ = minted_finish_receipt("a" * 64)
        configuration = {"identity": dict(FINISH_IDENTITY), "inputSha256": "c" * 64,
                         "key": FINISH_KEY}
        with self.assertRaises(ReceiptError) as caught:
            verify_receipt(raw, configuration, validate_outcome)
        self.assertEqual(caught.exception.code, "invalid-finish")
        self.assertEqual(str(caught.exception),
                         "the finish tool receipt failed its attempt-identity binding")

    def test_a_wrong_signature_digest_under_the_same_identity_fails_the_signature(self):
        raw, bridge = minted_finish_receipt("a" * 64)
        forged = json.loads(raw)
        forged.pop("signature")
        forged["signature"] = sign_receipt(forged, "d" * 64)
        with self.assertRaises(ReceiptError) as caught:
            verify_receipt(json.dumps(forged), bridge, validate_outcome)
        self.assertEqual(caught.exception.code, "invalid-finish")
        self.assertEqual(str(caught.exception),
                         "the finish tool receipt failed its signature verification")

    def test_the_untampered_receipt_verifies_against_its_own_bridge(self):
        raw, bridge = minted_finish_receipt("a" * 64)
        self.assertEqual(verify_receipt(raw, bridge, validate_outcome)["outcome"], FINISH_OUTCOME)
