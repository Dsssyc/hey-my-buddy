"""The DSH-native-resume C2 guards: queued conflicts, readiness material, receipts.

The four guards this suite pins are the ones a native-resume attempt relies on
before anything is committed: a request id or question id that is still queued
(a pending admission slot, never a committed index) refuses a changed payload
instead of joining or starting a second delivery; the shared finish-receipt
verification refuses a foreign input digest at its own binding stage and a
wrong signature digest under an otherwise identical execution identity at its
own signature stage; and the holder's readiness-material reader refuses a
non-regular file and an over-bound file at its own two layers. The queued
conflicts and the receipt stages reuse the existing suite's fixture helpers
(:mod:`buddy.harnesses.test_c_two_live`, :class:`buddy.roles.session_mcp.respond`)
and call the real entries under test; nothing here mints a fact the production
code would not.
"""
from __future__ import annotations

import json
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from buddy.harnesses.test_c_two_live import (
    QUEUED_REPLY, decode_reply, endpoint, inquiry_request, wire_request,
)
from hey_my_buddy.buddy.harnesses.session_receipts import ReceiptError, sign_receipt, verify_receipt
from hey_my_buddy.buddy.harnesses import live as lv
from hey_my_buddy.buddy.roles.live import _private_regular_bytes
from hey_my_buddy.buddy.roles.session_mcp import respond
from hey_my_buddy.buddy.roles.turn_io import validate_outcome
from hey_my_buddy.errors import BoardError

READINESS_BOUND = 16384

FINISH_IDENTITY = {"taskId": "goal", "attemptId": "attempt", "generation": 1, "turnId": "logical"}
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
        self.endpoint = endpoint(instance_id="c" * 64, token="d" * 64)
        self.run = self.endpoint.identity

    def raw_ask(self, request, *, timeout_ms=3000):
        return self.endpoint.request(wire_request(request, token="d" * 64,
                                                  instance_id="c" * 64, timeout_ms=timeout_ms))

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
        self.endpoint = endpoint(instance_id="c" * 64, token="d" * 64)
        self.run = self.endpoint.identity

    def raw_ask(self, request, *, timeout_ms=3000):
        return self.endpoint.request(wire_request(request, token="d" * 64,
                                                  instance_id="c" * 64, timeout_ms=timeout_ms))

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
    bridge = {"identity": dict(FINISH_IDENTITY), "inputSha256": input_sha256, "key": FINISH_KEY}
    result = respond({"id": 1, "method": "tools/call",
                      "params": {"name": "buddy_finish_turn", "arguments": FINISH_OUTCOME}}, bridge)
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


class ReadinessMaterialBarrierTests(unittest.TestCase):
    """V-C8 and V-C9: the holder's readiness-material reader, called directly.

    ``_private_regular_bytes`` is the one reader both readiness materials go
    through, and these tests call it directly so a refusal is attributable to
    its own guards rather than swallowed into an unavailable binding. The
    non-regular-file case uses a real writer-less FIFO under the real open
    flags: ``O_RDONLY|O_NOFOLLOW|O_NONBLOCK`` opens a FIFO immediately, so the
    open mode rejects nothing here and the function's own regular-file judgment
    is demonstrably the refusing layer — no open-primitive isolation is used,
    and the isolation boundary is exactly this: everything below the path guard
    (open, fstat, read, close) runs unisolated against real descriptors. The
    two size layers each carry their own independent witness: an over-bound
    file is refused by the metadata layer before any read (its own refusal
    text), and a file that grows after its size check is refused by the
    read-length layer (its own refusal text), so each layer's single-point
    removal turns exactly its own test red.
    """

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="buddy-c2-readiness-",
                                                dir=os.environ.get("BUDDY_CHECKS_TMPDIR", "/tmp"))
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name).resolve()

    def test_readiness_material_that_is_not_a_regular_file_is_refused(self):
        fifo = self.directory / "live-ready.fifo"
        os.mkfifo(fifo)
        with self.assertRaises(BoardError) as caught:
            _private_regular_bytes(fifo, READINESS_BOUND)
        self.assertEqual(caught.exception.code, "INVALID_ARGUMENT")
        self.assertEqual(caught.exception.message,
                         "Live readiness material is not a bounded regular file")

    def test_readiness_material_over_its_frame_bound_is_refused(self):
        path = self.directory / "live-ready.json"
        path.write_bytes(b"x" * (READINESS_BOUND + 1))
        with self.assertRaises(BoardError) as caught:
            _private_regular_bytes(path, READINESS_BOUND)
        self.assertEqual(caught.exception.code, "INVALID_ARGUMENT")
        # The metadata layer's own witness: an over-bound regular file is
        # refused by its size before anything is read, under the refusal text
        # only that layer raises. When the fstat size bound is removed the
        # read-length layer still rejects the same bytes, but with the other
        # message — so this exact text is what makes the metadata layer's
        # single-point removal red.
        self.assertEqual(caught.exception.message,
                         "Live readiness material is not a bounded regular file")

    def test_readiness_material_that_grows_after_its_size_check_is_refused_at_the_read_bound(self):
        """The read-length layer's own witness: the bound holds across growth.

        Isolation boundary: exactly one low-level primitive, the ``os.fstat``
        call inside the function under test, is wrapped for this one call, and
        the wrapper is honest at both ends. It takes the real stat snapshot of
        the real regular file first (initial length within the bound, so the
        metadata layer genuinely passes it), then appends the extra byte
        through a real second descriptor on that same real file, then returns
        the pre-growth snapshot — the between-check growth the read-length
        guard exists for, coordinated at one boundary instead of a racing
        writer thread. Nothing else is patched: the open, the metadata guard,
        the buffered read and the whole function run real, and the bytes the
        read returns are the file's actual grown content, so removing
        ``len(raw) > maximum`` alone turns this red while the metadata guard
        stays intact.
        """
        path = self.directory / "growing-ready.json"
        path.write_bytes(b"x" * READINESS_BOUND)
        real_fstat = os.fstat

        def grow_after_snapshot(descriptor):
            snapshot = real_fstat(descriptor)
            append = os.open(path, os.O_WRONLY | os.O_APPEND)
            try:
                os.write(append, b"y")
            finally:
                os.close(append)
            return snapshot

        with mock.patch("hey_my_buddy.buddy.roles.live.os.fstat",
                        side_effect=grow_after_snapshot):
            with self.assertRaises(BoardError) as caught:
                _private_regular_bytes(path, READINESS_BOUND)
        self.assertEqual(caught.exception.code, "INVALID_ARGUMENT")
        self.assertEqual(caught.exception.message,
                         "Live readiness material exceeds its frame bound")


if __name__ == "__main__":
    unittest.main()
