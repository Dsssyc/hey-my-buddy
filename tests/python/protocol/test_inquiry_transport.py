"""Concurrent local inquiry handoff and owner settlement semantics."""
from __future__ import annotations

import threading
import time
import unittest

from hey_my_buddy.buddy.harnesses import c_two_live as ctl
from hey_my_buddy.buddy.harnesses import live as lv
from hey_my_buddy.json_codec import decode_strict_json
from buddy.harnesses.test_c_two_live import identity, inquiry_request


class LocalHandoffTests(unittest.TestCase):
    def setUp(self):
        self.endpoint = ctl.CTwoLiveEndpoint(
            identity(), lv.LiveCapabilities(inquiry_delivery="cooperative-checkpoint"))

    def ask(self, request, *, timeout_ms=3000):
        return lv.LiveReply.from_payload(decode_strict_json(self.endpoint.request_local(
            request, timeout_ms=timeout_ms,
            deadline_monotonic=time.monotonic() + timeout_ms / 1000.0)))

    def snapshot(self):
        return lv.LiveSnapshot.from_payload(decode_strict_json(
            self.endpoint.observe_local(limit=10)))

    def test_concurrent_requests_keep_owner_settlements_correlated(self):
        requests = [inquiry_request(request_id=f"r-{i}", question_id=f"q-{i}")
                    for i in range(2)]
        replies = [[], []]
        threads = [threading.Thread(target=lambda i=i: replies[i].append(
            self.ask(requests[i])), daemon=True) for i in range(2)]
        for thread in threads:
            thread.start()
        consumed = [self.endpoint.consume_request(1.0) for _ in requests]
        self.assertTrue(all(request is not None for request in consumed))
        self.assertEqual({request.request_id for request in consumed}, {"r-0", "r-1"})
        self.assertEqual(self.snapshot().inquiries, ())
        for request in reversed(consumed):
            self.endpoint.publish_inquiry_state(lv.InquiryState(
                question_id=request.payload.question_id, status="queued"))
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
        self.assertEqual({entry.question_id for entry in self.snapshot().inquiries}, {"q-0", "q-1"})
        self.assertIsNone(self.endpoint.consume_request(0.0))

    def test_owner_refusal_keeps_its_specific_code_and_is_retryable(self):
        box = []
        thread = threading.Thread(target=lambda: box.append(
            self.ask(inquiry_request())), daemon=True)
        thread.start()
        request = self.endpoint.consume_request(1.0)
        self.assertIsNotNone(request)
        self.endpoint.settle_request(request.request_id, lv.LiveReply(
            status="unavailable", reason_code="journal-unavailable", error_code="not-ready"))
        thread.join(timeout=4)
        self.assertFalse(thread.is_alive())
        self.assertEqual((box[0].status, box[0].reason_code, box[0].error_code),
                         ("unavailable", "journal-unavailable", "not-ready"))
        self.assertEqual(self.snapshot().inquiries, ())
        retry = []
        thread = threading.Thread(target=lambda: retry.append(
            self.ask(inquiry_request())), daemon=True)
        thread.start()
        self.assertEqual(self.endpoint.consume_request(1.0), request)
        self.endpoint.publish_inquiry_state(lv.InquiryState(question_id="question-1", status="queued"))
        self.endpoint.settle_request(request.request_id, lv.LiveReply(
            status="queued", observed=True, state="queued"))
        thread.join(timeout=4)
        self.assertFalse(thread.is_alive())
        self.assertEqual(retry[0].status, "queued")


if __name__ == "__main__":
    unittest.main()
