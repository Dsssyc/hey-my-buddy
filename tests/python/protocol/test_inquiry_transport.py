"""Named live RPC transport through the actual bounded C-Two channel.

Only ``cc.connect`` is replaced: one connection points at the actual endpoint
handlers; malformed replies and SDK failures use the existing ``StubPeer``.
No socket framing or vendor correlation protocol is reproduced here. The
endpoint owner in the correlation test supplies a simulated settlement, not a
native journal commit or a signed delivery receipt. The SDK's own RPC reply
correlation belongs to the mature C-Two contract (ADR-023 decisions 7/8).
"""
from __future__ import annotations

import threading
import time
import unittest
from contextlib import nullcontext
from unittest.mock import patch

from hey_my_buddy.buddy.harnesses import c_two_live as ctl
from hey_my_buddy.buddy.harnesses import live as lv
from hey_my_buddy.errors import BoardError
from hey_my_buddy.json_codec import canonical_json, decode_strict_json
from tests.python.buddy.harnesses.test_c_two_live import (
    StubPeer,
    TEST_CRM,
    identity,
    inquiry_request,
)


class TransportTests(unittest.TestCase):
    def setUp(self):
        self.run = identity()
        self.endpoint = ctl.CTwoLiveEndpoint(
            self.run, lv.LiveCapabilities(inquiry_delivery="cooperative-checkpoint"),
            TEST_CRM, instance_id="a" * 64, token="b" * 64)
        self.channel = ctl.CTwoLiveChannel(
            self.run, TEST_CRM, name="Hana", address="ipc://cc" + "5" * 38,
            instance_id="a" * 64, token="b" * 64)
        self.addCleanup(self.endpoint.close, reason="test-finished")

    def connect(self, peer):
        """Replace SDK network connection only; named dispatch remains real."""
        patcher = patch.object(ctl.cc, "connect", return_value=nullcontext(peer))
        connection = patcher.start()
        self.addCleanup(patcher.stop)
        return connection

    def test_named_rpcs_keep_the_request_and_owner_settlement_correlated(self):
        connection = self.connect(self.endpoint)
        self.assertEqual(self.channel.capabilities().inquiry_delivery,
                         "cooperative-checkpoint")
        requests = [inquiry_request(request_id=f"r-{i}", question_id=f"q-{i}")
                    for i in range(2)]
        replies = [[], []]
        threads = [threading.Thread(target=lambda i=i: replies[i].append(
            self.channel.request(requests[i], timeout_ms=3000)), daemon=True)
            for i in range(2)]
        for thread in threads:
            thread.start()
        # These are actual owner queue entries, with no fabricated wire reply id.
        consumed = [self.endpoint.consume_request(1.0) for _ in requests]
        self.assertTrue(all(request is not None for request in consumed))
        self.assertEqual({request.request_id for request in consumed}, {"r-0", "r-1"})
        self.assertEqual(self.channel.observe(limit=10, timeout_ms=1500).inquiries, ())
        for request in reversed(consumed):
            self.endpoint.settle_request(request.request_id, lv.LiveReply(
                status="queued", observed=True, state="queued",
                native_correlation={"questionId": request.payload.question_id}))
        for thread in threads:
            thread.join(timeout=4)
            self.assertFalse(thread.is_alive())
        for index, box in enumerate(replies):
            self.assertEqual(len(box), 1)
            self.assertEqual((box[0].status, box[0].state), ("queued", "queued"))
            self.assertEqual(box[0].native_correlation.value["questionId"], f"q-{index}")
        snapshot = self.channel.observe(limit=10, timeout_ms=1500)
        self.assertEqual({entry.question_id for entry in snapshot.inquiries}, {"q-0", "q-1"})
        self.assertTrue(all(entry.status == "queued" for entry in snapshot.inquiries))
        for call in connection.call_args_list:
            self.assertEqual(call.args, (TEST_CRM,))
            self.assertEqual(call.kwargs, {"name": "Hana", "address": "ipc://cc" + "5" * 38})
        self.assertIsNone(self.endpoint.consume_request(0.0))

    def test_sdk_connection_failure_is_unavailable_for_every_named_rpc(self):
        self.connect(StubPeer([ConnectionError("connection refused")] * 3))
        reply = self.channel.request(inquiry_request(), timeout_ms=1500)
        self.assertEqual((reply.status, reply.reason_code),
                         ("unavailable", "transport-unreachable"))
        snapshot = self.channel.observe(limit=10, timeout_ms=1500)
        self.assertEqual((snapshot.observed, snapshot.reason), (False, "transport-unreachable"))
        with self.assertRaises(BoardError) as failure:
            self.channel.capabilities()
        self.assertEqual(failure.exception.code, "LIVE_UNAVAILABLE")

    def test_owner_refusal_preserves_its_specific_code(self):
        self.connect(self.endpoint)
        box = []
        thread = threading.Thread(target=lambda: box.append(
            self.channel.request(inquiry_request(), timeout_ms=3000)), daemon=True)
        thread.start()
        request = self.endpoint.consume_request(1.0)
        self.assertIsNotNone(request)
        self.endpoint.settle_request(request.request_id, lv.LiveReply(
            status="unavailable", reason_code="journal-unavailable", error_code="not-ready"))
        thread.join(timeout=4)
        self.assertFalse(thread.is_alive())
        self.assertEqual((box[0].status, box[0].reason_code, box[0].error_code),
                         ("unavailable", "journal-unavailable", "not-ready"))
        self.assertEqual(self.channel.observe(limit=10, timeout_ms=1500).inquiries, ())

    def test_unknown_owner_code_is_preserved_as_a_source_fact(self):
        refusal = lv.LiveReply(status="unavailable", reason_code="owner-unavailable",
                               error_code="owner-specific-code")
        peer = StubPeer([canonical_json(refusal.to_payload())])
        self.connect(peer)
        reply = self.channel.request(inquiry_request(), timeout_ms=1500)
        self.assertEqual((reply.reason_code, reply.error_code),
                         ("owner-unavailable", "owner-specific-code"))
        self.assertEqual(peer.calls[0][0], "request")
        frame = ctl.LiveWireRequest.from_payload(decode_strict_json(peer.calls[0][1]))
        self.assertEqual((frame.request_id, frame.identity), ("request-1", self.run))

    def test_non_json_and_wrong_dto_replies_are_invalid_for_every_named_rpc(self):
        for raw in ("not json", '{"extra":1}', '{"status":"queued","extra":1}',
                    '{"status":"queued","status":"delivered"}'):
            with self.subTest(reply=raw):
                self.connect(StubPeer([raw] * 3))
                reply = self.channel.request(inquiry_request(), timeout_ms=1500)
                self.assertEqual((reply.status, reply.reason_code), ("unavailable", "reply-invalid"))
                snapshot = self.channel.observe(limit=10, timeout_ms=1500)
                self.assertEqual((snapshot.observed, snapshot.reason), (False, "reply-invalid"))
                with self.assertRaises(BoardError):
                    self.channel.capabilities()

    def test_oversized_complete_reply_frames_are_refused_before_dto_decoding(self):
        self.assertEqual(lv.MAX_LIVE_FRAME_BYTES, 64 * 1024)
        # A DTO-valid reply enlarged with legal JSON whitespace catches the
        # full-frame byte bound, independently of any individual DTO field cap.
        values = (lv.LiveReply(status="queued", observed=True, state="queued"),
                  lv.LiveSnapshot(observed=True),
                  lv.LiveCapabilities(inquiry_delivery="cooperative-checkpoint"))
        replies = [canonical_json(value.to_payload()) + " " * lv.MAX_LIVE_FRAME_BYTES
                   for value in values]
        self.connect(StubPeer(replies))
        reply = self.channel.request(inquiry_request(), timeout_ms=1500)
        self.assertEqual((reply.status, reply.reason_code), ("unavailable", "reply-invalid"))
        snapshot = self.channel.observe(limit=10, timeout_ms=1500)
        self.assertEqual((snapshot.observed, snapshot.reason), (False, "reply-invalid"))
        with self.assertRaises(BoardError):
            self.channel.capabilities()

    def test_transport_windows_reject_invalid_values_before_sdk_connection(self):
        connection = self.connect(StubPeer([]))
        self.assertEqual((lv.MIN_TRANSPORT_TIMEOUT_MS, lv.MAX_TRANSPORT_TIMEOUT_MS), (100, 5000))
        for value in (99, 5001, 99_000, True, 1500.0):
            with self.subTest(timeout=value):
                with self.assertRaises(BoardError):
                    self.channel.request(inquiry_request(), timeout_ms=value)
                with self.assertRaises(BoardError):
                    self.channel.observe(limit=10, timeout_ms=value)
        connection.assert_not_called()

    def test_stalled_sdk_calls_expire_without_reporting_a_stopped_owner(self):
        peer = StubPeer(["unused"] * 2, delay=0.35)
        self.connect(peer)
        started = time.monotonic()
        reply = self.channel.request(inquiry_request(), timeout_ms=100)
        self.assertEqual((reply.status, reply.reason_code),
                         ("unavailable", "transport-window-expired"))
        self.assertGreaterEqual(time.monotonic() - started, 0.09)
        self.assertLess(time.monotonic() - started, 0.8)
        snapshot = self.channel.observe(limit=10, timeout_ms=100)
        self.assertEqual((snapshot.observed, snapshot.reason),
                         (False, "transport-window-expired"))
        # Drain the finite StubPeer stall before fixture cleanup. No subprocess
        # was started, and this result is never used as shutdown evidence.
        time.sleep(0.4)


if __name__ == "__main__":
    unittest.main()
