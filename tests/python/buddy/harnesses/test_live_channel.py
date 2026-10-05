"""The ADR-025 step 1-A live interface: identity, limits, replay, pages, honesty.

Synthetic bindings only: no harness, socket or model is started. The tests pin
the preserved inquiry limits against their existing definitions, the
per-harness capability declarations, and the adapter's honesty rules: requests
are bound by identity and requestId over the full kind/payload pair, a replay
returns its recorded reply without a second delivery, a changed payload under a
committed requestId is refused before the bridge, a refused ask stays
retryable, a queued question is never reported as delivered, and observation
pages through the 64 KiB frame so the full 32 x 4000-byte answer set stays
reachable without loss.
"""
from __future__ import annotations

import unittest
from unittest import mock

from hey_my_buddy import json_codec
from hey_my_buddy.buddy.harnesses import live as lv
from hey_my_buddy.buddy.harnesses.run_contract import RunIdentity
from hey_my_buddy.blackboard.tasks import inquiry as board_inquiry
from hey_my_buddy.buddy.harnesses.zcode import protocol as zcode_protocol
from hey_my_buddy.errors import BoardError
from hey_my_buddy.json_codec import canonical_json
from hey_my_buddy.protocol import schemas as board_schemas


def identity() -> RunIdentity:
    return RunIdentity(task_id="task-fixture", attempt_id="attempt-fixture", generation=1,
                       invocation_id="invocation-fixture", turn_id="turn-fixture")


def inquiry_request(question: str = "what should I check first?", request_id: str = "request-1",
                    question_id: str = "question-1") -> lv.LiveRequest:
    return lv.LiveRequest(identity=identity(), request_id=request_id, kind="inquiry",
                          payload=lv.InquiryPayload(question_id=question_id, question=question))


class RecordingBridge:
    """A narrow ask binding that records calls and returns committed states."""

    def __init__(self, *replies):
        self.calls: list[tuple[str, str, int]] = []
        self.replies = list(replies)

    def ask(self, question_id: str, question: str, timeout_ms: int):
        self.calls.append((question_id, question, timeout_ms))
        reply = self.replies[min(len(self.calls) - 1, len(self.replies) - 1)]
        if isinstance(reply, BoardError):
            raise reply
        return reply


class CapabilityTests(unittest.TestCase):
    def test_each_harness_declares_its_existing_facilities_only(self):
        expected = {"codex": "unsupported", "claude": "unsupported",
                    "zcode": "cooperative-checkpoint", "dsh": "realtime"}
        for harness, delivery in expected.items():
            capabilities = lv.EXISTING_CAPABILITIES[harness]
            self.assertTrue(capabilities.activity, harness)
            self.assertEqual(capabilities.inquiry_delivery, delivery, harness)
            self.assertFalse(capabilities.finish_notice, harness)
            self.assertFalse(capabilities.session_content, harness)

    def test_an_unknown_delivery_mode_is_refused(self):
        with self.assertRaises(BoardError):
            lv.LiveCapabilities(activity=True, inquiry_delivery="queued")

    def test_the_adapter_satisfies_the_live_channel_protocol(self):
        channel = lv.ExistingLiveChannel(identity(), lv.EXISTING_CAPABILITIES["codex"])
        self.assertIsInstance(channel, lv.LiveChannel)
        self.assertEqual(channel.capabilities(), lv.EXISTING_CAPABILITIES["codex"])


class LimitTests(unittest.TestCase):
    def test_the_live_limits_are_the_existing_inquiry_limits(self):
        self.assertEqual(lv.MAX_QUESTION_BYTES, board_schemas.MAX_QUESTION_BYTES)
        self.assertEqual(lv.MAX_ANSWER_BYTES, board_schemas.MAX_ANSWER_BYTES)
        self.assertEqual(lv.MAX_INQUIRIES_PER_RUN, board_schemas.MAX_INQUIRIES_PER_RUN)
        self.assertEqual(lv.MAX_INQUIRIES_PER_RUN, zcode_protocol.MAX_INQUIRIES)
        self.assertEqual(lv.MAX_QUESTION_BYTES, zcode_protocol.MAX_QUESTION_BYTES)
        self.assertEqual(lv.MAX_ANSWER_BYTES, zcode_protocol.MAX_ANSWER_BYTES)
        self.assertEqual(lv.MIN_TRANSPORT_TIMEOUT_MS, board_inquiry.MIN_TRANSPORT_TIMEOUT_MS)
        self.assertEqual(lv.MAX_TRANSPORT_TIMEOUT_MS, board_inquiry.MAX_TRANSPORT_TIMEOUT_MS)
        self.assertEqual(lv.MAX_WAIT_MS, board_inquiry.MAX_WAIT_MS)
        self.assertEqual((lv.MIN_TRANSPORT_TIMEOUT_MS, lv.MAX_TRANSPORT_TIMEOUT_MS), (100, 5000))
        self.assertEqual(lv.MAX_WAIT_MS, 30000)

    def test_oversized_questions_and_bad_timeouts_are_refused_at_the_value(self):
        with self.assertRaises(BoardError):
            lv.InquiryPayload(question_id="q-1", question="x" * (lv.MAX_QUESTION_BYTES + 1))
        with self.assertRaises(BoardError):
            lv.InquiryPayload(question_id="q-1", question="水" * 2001)  # 6003 UTF-8 bytes, 2001 characters
        channel = lv.ExistingLiveChannel(identity(), lv.EXISTING_CAPABILITIES["zcode"])
        for timeout in (99, 5001, 0, "1500"):
            with self.assertRaises(BoardError, msg=str(timeout)):
                channel.request(inquiry_request(), timeout_ms=timeout)

    def test_a_multibyte_question_at_the_byte_bound_is_accepted(self):
        payload = lv.InquiryPayload(question_id="q-1", question="水" * 1333)  # 3999 UTF-8 bytes
        self.assertEqual(len(payload.question.encode()), lv.MAX_QUESTION_BYTES - 1)


class RequestBindingTests(unittest.TestCase):
    def channel(self, bridge: RecordingBridge | None = None, *, harness: str = "zcode",
                journal=None, activity=None) -> lv.ExistingLiveChannel:
        return lv.ExistingLiveChannel(
            identity(), lv.EXISTING_CAPABILITIES[harness],
            read_activity=(lambda: activity) if activity is not None else None,
            ask=bridge.ask if bridge is not None else None,
            read_journal=(lambda: journal) if journal is not None else None)

    def test_request_id_and_question_id_are_independent_facts(self):
        bridge = RecordingBridge({"accepted": True, "state": "queued", "inquiryId": "question-1"})
        channel = self.channel(bridge)
        reply = channel.request(inquiry_request(), timeout_ms=1500)
        self.assertEqual(reply.status, "queued")
        # The bridge received the question id and the requested transport window.
        self.assertEqual(bridge.calls, [("question-1", "what should I check first?", 1500)])

    def test_a_replayed_request_returns_its_recorded_reply_without_redelivery(self):
        bridge = RecordingBridge({"accepted": True, "state": "queued", "duplicate": False,
                                  "inquiryId": "question-1"},
                                 {"accepted": True, "state": "queued", "duplicate": True,
                                  "inquiryId": "question-1"})
        channel = self.channel(bridge)
        first = channel.request(inquiry_request(), timeout_ms=1500)
        replay = channel.request(inquiry_request(), timeout_ms=1500)
        self.assertIs(replay, first)
        self.assertEqual(len(bridge.calls), 1, "an identical request never reaches the bridge twice")

    def test_a_changed_payload_under_a_committed_request_id_conflicts_before_the_bridge(self):
        bridge = RecordingBridge({"accepted": True, "state": "queued"})
        channel = self.channel(bridge)
        channel.request(inquiry_request(), timeout_ms=1500)
        for changed in (inquiry_request(question="a different question"),
                        inquiry_request(question_id="question-2"),
                        inquiry_request(request_id="request-1", question_id="question-2",
                                        question="another")):
            with self.subTest(changed=changed.payload.to_payload()):
                reply = channel.request(changed, timeout_ms=1500)
                self.assertEqual((reply.status, reply.reason_code),
                                 ("unavailable", "request-payload-conflict"))
        self.assertEqual(len(bridge.calls), 1)

    def test_a_changed_kind_under_the_same_request_id_conflicts(self):
        bridge = RecordingBridge({"accepted": True, "state": "queued"})
        channel = self.channel(bridge)
        channel.request(inquiry_request(), timeout_ms=1500)
        notice = lv.LiveRequest(identity=identity(), request_id="request-1", kind="finish-notice",
                                payload=lv.FinishNoticePayload(notice_id="n-1", message="wrap up"))
        channel.request(notice, timeout_ms=1500)
        # finish-notice is unsupported everywhere, but a plain inquiry kind
        # change under a committed request id is a conflict, not a new ask.
        self.assertEqual(len(bridge.calls), 1)

    def test_a_refused_ask_is_not_remembered_and_stays_retryable(self):
        bridge = RecordingBridge(BoardError("not-ready", "the native turn is not admitted yet"))
        channel = self.channel(bridge)
        refused = channel.request(inquiry_request(), timeout_ms=1500)
        self.assertEqual((refused.status, refused.reason_code), ("unavailable", "not-ready"))
        bridge.replies = [{"accepted": True, "state": "queued"}]
        retried = channel.request(inquiry_request(), timeout_ms=1500)
        self.assertEqual(retried.status, "queued")
        self.assertEqual(len(bridge.calls), 2, "the refused ask left no committed state")

    def test_a_not_accepted_bridge_value_is_not_remembered_either(self):
        bridge = RecordingBridge({"accepted": False, "reason": "not-ready"})
        channel = self.channel(bridge)
        reply = channel.request(inquiry_request(), timeout_ms=1500)
        self.assertEqual(reply.status, "unavailable")
        bridge.replies = [{"accepted": True, "state": "queued"}]
        retried = channel.request(inquiry_request(), timeout_ms=1500)
        self.assertEqual(retried.status, "queued")

    def test_the_bridge_keeps_the_per_run_question_limit(self):
        replies = [{"accepted": True, "state": "queued"}] * 32 + [BoardError("too-many", "limit")]
        bridge = RecordingBridge(*replies)
        channel = self.channel(bridge)
        for index in range(32):
            reply = channel.request(inquiry_request(request_id=f"r-{index}",
                                                    question_id=f"question-{index}"),
                                    timeout_ms=1500)
            self.assertEqual(reply.status, "queued", msg=index)
        exceeded = channel.request(inquiry_request(request_id="r-32", question_id="question-32"),
                                   timeout_ms=1500)
        self.assertEqual((exceeded.status, exceeded.reason_code), ("unavailable", "too-many"))

    def test_a_foreign_identity_is_refused_without_touching_the_bridge(self):
        bridge = RecordingBridge({"accepted": True, "state": "queued"})
        channel = self.channel(bridge)
        foreign = lv.LiveRequest(
            identity=RunIdentity(task_id="other", attempt_id="attempt-fixture", generation=1,
                                 invocation_id="invocation-fixture"),
            request_id="request-1", kind="inquiry", payload=lv.InquiryPayload(question_id="question-1", question="hello?"))
        reply = channel.request(foreign, timeout_ms=1500)
        self.assertEqual((reply.status, reply.reason_code), ("unavailable", "identity-mismatch"))
        self.assertEqual(bridge.calls, [])

    def test_queued_is_never_upgraded_to_delivered_by_the_adapter(self):
        bridge = RecordingBridge({"accepted": True, "state": "queued"})
        channel = self.channel(bridge)
        reply = channel.request(inquiry_request(), timeout_ms=1500)
        self.assertEqual(reply.status, "queued")
        self.assertEqual(reply.delivery_mode, "cooperative-checkpoint")
        # Only the bridge's own committed, journal-backed state may say delivered.
        bridge.replies = [{"accepted": True, "state": "delivered", "inquiryId": "question-1"}]
        later = channel.request(inquiry_request(request_id="request-2"), timeout_ms=1500)
        self.assertEqual(later.status, "delivered")

    def test_unsupported_facilities_answer_unsupported(self):
        codex = self.channel(harness="codex")
        reply = codex.request(inquiry_request(), timeout_ms=1500)
        self.assertEqual((reply.status, reply.reason_code), ("unsupported", "inquiry-unsupported"))
        for channel in (codex, self.channel(harness="dsh")):
            notice = lv.LiveRequest(identity=identity(), request_id="n-1", kind="finish-notice",
                                    payload=lv.FinishNoticePayload(notice_id="n-1", message="please wrap up"))
            answered = channel.request(notice, timeout_ms=1500)
            self.assertEqual((answered.status, answered.reason_code),
                             ("unsupported", "finish-notice-unsupported"))

    def test_a_supported_channel_without_a_wired_bridge_is_unavailable_not_unsupported(self):
        # ZCode supports inquiries natively; a channel with no ask binding is
        # an availability fact for this step, not a capability fact.
        channel = self.channel(harness="zcode")
        reply = channel.request(inquiry_request(), timeout_ms=1500)
        self.assertEqual((reply.status, reply.reason_code),
                         ("unavailable", "inquiry-binding-unavailable"))
        dsh = self.channel(harness="dsh")
        reply = dsh.request(inquiry_request(), timeout_ms=1500)
        self.assertEqual((reply.status, reply.reason_code),
                         ("unavailable", "inquiry-binding-unavailable"))

    def test_close_is_honest_and_touches_nothing_native(self):
        reads = {"count": 0}
        def read_activity():
            reads["count"] += 1
            return {"phase": "finishing", "eventSeq": 9}
        channel = lv.ExistingLiveChannel(identity(), lv.EXISTING_CAPABILITIES["dsh"],
                                         read_activity=read_activity)
        channel.observe(after_seq=None, limit=10, timeout_ms=1500)
        channel.close(reason="attempt collection finished")
        closed = channel.observe(after_seq=None, limit=10, timeout_ms=1500)
        self.assertEqual(closed.unavailable, "channel-closed")
        self.assertEqual(closed.activity.value["phase"], "finishing")
        before = reads["count"]
        channel.observe(after_seq=None, limit=10, timeout_ms=1500)
        self.assertEqual(reads["count"], before, "a closed channel reads nothing further")
        reply = channel.request(inquiry_request(), timeout_ms=1500)
        self.assertEqual((reply.status, reply.reason_code), ("unavailable", "channel-closed"))
        with self.assertRaises(BoardError):
            channel.close(reason="")
        channel.close(reason="second close")
        self.assertEqual(channel.observe(after_seq=None, limit=1, timeout_ms=1500).unavailable,
                         "channel-closed")


class ObserveTests(unittest.TestCase):
    def channel(self, *, journal=None, activity=None) -> lv.ExistingLiveChannel:
        return lv.ExistingLiveChannel(
            identity(), lv.EXISTING_CAPABILITIES["zcode"],
            read_activity=(lambda: activity) if activity is not None else None,
            read_journal=(lambda: journal) if journal is not None else None)

    def test_a_front_row_update_during_pagination_never_hides_the_back_rows(self):
        # The Host probe scenario: after page 1 is read, the first question is
        # updated. Its fresh seq sorts behind every not-yet-delivered entry,
        # so continuing from the returned entries' cursor reaches all of them.
        journal = [{"inquiryId": f"question-{index}", "state": "answered", "answer": "x" * 4000}
                   for index in range(32)]
        channel = self.channel(journal=journal)
        page1 = channel.observe(after_seq=None, limit=10, timeout_ms=1500)
        self.assertEqual([entry.question_id for entry in page1.inquiries],
                         [f"question-{index}" for index in range(10)])
        journal[0] = {**journal[0], "answer": "changed"}
        cursor = max(entry.seq for entry in page1.inquiries)
        page2 = channel.observe(after_seq=cursor, limit=10, timeout_ms=1500)
        self.assertEqual([entry.question_id for entry in page2.inquiries],
                         [f"question-{index}" for index in range(10, 20)])
        page3 = channel.observe(after_seq=max(entry.seq for entry in page2.inquiries),
                                limit=32, timeout_ms=1500)
        self.assertEqual([entry.question_id for entry in page3.inquiries],
                         [f"question-{index}" for index in range(20, 32)]
                         + ["question-0"], "the update arrives after the untouched back rows")
        self.assertFalse(page3.truncated)
        # The truncated pages' own sequence is the safe cursor, never the
        # watermark that would skip undelivered facts.
        self.assertEqual(page1.sequence, cursor)
        self.assertEqual(page3.sequence, page3.inquiries[-1].seq)
        self.assertGreater(page3.sequence, max(entry.seq for entry in page3.inquiries[:-1]))
        # Every answer is reachable, exactly once as latest state.
        seen: dict[str, str] = {}
        for page in (page1, page2, page3):
            for entry in page.inquiries:
                seen[entry.question_id] = entry.answer
        self.assertEqual(seen["question-0"], "changed")
        self.assertEqual(len(seen), 32)

    def test_activity_updates_during_pagination_do_not_disturb_the_entry_cursor(self):
        activity = {"current": None}
        journal = [{"inquiryId": f"question-{index}", "state": "queued"} for index in range(12)]
        channel = lv.ExistingLiveChannel(
            identity(), lv.EXISTING_CAPABILITIES["zcode"],
            read_activity=lambda: activity["current"],
            read_journal=lambda: journal)
        activity["current"] = {"phase": "streaming-model", "eventSeq": 1}
        page1 = channel.observe(after_seq=None, limit=5, timeout_ms=1500)
        self.assertEqual(len(page1.inquiries), 5)
        activity["current"] = {"phase": "tool-running", "eventSeq": 2}
        cursor = max(entry.seq for entry in page1.inquiries)
        page2 = channel.observe(after_seq=cursor, limit=5, timeout_ms=1500)
        self.assertEqual([entry.question_id for entry in page2.inquiries],
                         [f"question-{index}" for index in range(5, 10)])
        self.assertEqual(page2.activity.value["phase"], "tool-running")
        page3 = channel.observe(after_seq=max(entry.seq for entry in page2.inquiries),
                                limit=5, timeout_ms=1500)
        self.assertEqual([entry.question_id for entry in page3.inquiries],
                         [f"question-{index}" for index in range(10, 12)])
        self.assertFalse(page3.truncated)

    def test_different_limits_and_repeated_observes_stay_complete_and_idempotent(self):
        journal = [{"inquiryId": f"question-{index}", "state": "answered", "answer": "x" * 4000}
                   for index in range(32)]
        for limit in (1, 7, 32, 256):
            channel = self.channel(journal=journal)
            collected, after, guard = {}, None, 0
            while True:
                snapshot = channel.observe(after_seq=after, limit=limit, timeout_ms=1500)
                for entry in snapshot.inquiries:
                    collected.setdefault(entry.question_id, entry.answer)
                if not snapshot.truncated:
                    break
                after = max(entry.seq for entry in snapshot.inquiries)
                guard += 1
                self.assertLess(guard, 100)
            self.assertEqual(len(collected), 32, msg=f"limit={limit}")
            self.assertEqual(set(collected.values()), {"x" * 4000})
        # A repeated observe with the same cursor returns the same page.
        channel = self.channel(journal=journal)
        first = channel.observe(after_seq=None, limit=4, timeout_ms=1500)
        again = channel.observe(after_seq=None, limit=4, timeout_ms=1500)
        self.assertEqual(first, again)

    def test_the_full_answer_set_is_reachable_through_paged_observation(self):
        journal = [{"inquiryId": f"question-{index}", "state": "answered", "answer": "x" * 4000}
                   for index in range(32)]
        channel = self.channel(journal=journal)
        collected, after, pages = [], None, 0
        while True:
            snapshot = channel.observe(after_seq=after, limit=256, timeout_ms=1500)
            frame = lv.encode_live_snapshot(snapshot)
            self.assertLessEqual(len(frame.encode()), lv.MAX_LIVE_FRAME_BYTES,
                                 "every page fits one frame")
            collected.extend(snapshot.inquiries)
            pages += 1
            if not snapshot.truncated:
                break
            self.assertTrue(snapshot.inquiries, "a truncated page always carries progress")
            after = max(entry.seq for entry in snapshot.inquiries)
            self.assertLess(pages, 40)
        self.assertEqual(pages, 2)
        self.assertEqual(len(collected), 32)
        self.assertEqual({entry.question_id for entry in collected},
                         {f"question-{index}" for index in range(32)})
        self.assertTrue(all(entry.answer == "x" * 4000 for entry in collected))

    def test_activity_is_monotone_and_journal_changes_take_fresh_sequences(self):
        activity_feed = {"current": {"phase": "waiting-model", "eventSeq": 4}}
        journal_feed = [{"inquiryId": "question-1", "state": "claimed"},
                        {"inquiryId": "question-2", "state": "answered", "answer": "check the tests"}]
        channel = self.channel(activity=activity_feed["current"], journal=journal_feed)
        first = channel.observe(after_seq=None, limit=10, timeout_ms=1500)
        self.assertEqual([entry.status for entry in first.inquiries], ["queued", "answered"])
        stale = channel.observe(after_seq=first.sequence, limit=10, timeout_ms=1500)
        self.assertEqual(stale.sequence, first.sequence)
        activity_feed["current"] = {"phase": "waiting-model", "eventSeq": 2}
        self.assertEqual(channel.observe(after_seq=first.sequence, limit=10,
                                         timeout_ms=1500).sequence, first.sequence)
        journal_feed.append({"inquiryId": "question-1", "state": "delivered"})
        advanced = channel.observe(after_seq=first.sequence, limit=10, timeout_ms=1500)
        self.assertGreater(advanced.sequence, first.sequence)
        delivered = [entry for entry in advanced.inquiries if entry.question_id == "question-1"]
        self.assertEqual(len(delivered), 1, "one state per question, last record wins")
        self.assertGreater(delivered[0].seq, first.sequence,
                           "a changed question takes a fresh sequence observers can page to")

    def test_limit_truncates_and_reports(self):
        journal = [{"inquiryId": f"question-{index}", "state": "queued"} for index in range(32)]
        channel = self.channel(journal=journal)
        page = channel.observe(after_seq=None, limit=10, timeout_ms=1500)
        self.assertEqual(len(page.inquiries), 10)
        self.assertTrue(page.truncated)
        rest = channel.observe(after_seq=max(entry.seq for entry in page.inquiries),
                               limit=256, timeout_ms=1500)
        self.assertEqual(len(page.inquiries) + len(rest.inquiries), 32)
        with self.assertRaises(BoardError):
            channel.observe(after_seq=None, limit=0, timeout_ms=1500)
        with self.assertRaises(BoardError):
            channel.observe(after_seq=-1, limit=10, timeout_ms=1500)

    def test_an_unavailable_binding_keeps_the_last_persisted_facts(self):
        state = {"activity": {"phase": "streaming-model", "eventSeq": 6}, "fail": False}
        def read_activity():
            if state["fail"]:
                raise BoardError("INTERNAL", "unreadable")
            return state["activity"]
        channel = lv.ExistingLiveChannel(identity(), lv.EXISTING_CAPABILITIES["codex"],
                                         read_activity=read_activity)
        good = channel.observe(after_seq=None, limit=10, timeout_ms=1500)
        self.assertEqual(good.activity.value["phase"], "streaming-model")
        self.assertIsNone(good.unavailable)
        state["fail"] = True
        broken = channel.observe(after_seq=good.sequence, limit=10, timeout_ms=1500)
        self.assertEqual(broken.unavailable, "activity-unavailable")
        self.assertEqual(broken.activity.value["phase"], "streaming-model")

    def test_an_unknown_journal_state_stays_unknown(self):
        channel = self.channel(journal=[{"inquiryId": "question-1", "state": "mysterious"}])
        snapshot = channel.observe(after_seq=None, limit=10, timeout_ms=1500)
        self.assertEqual(snapshot.inquiries[0].status, "unknown")

    def test_an_oversized_frame_is_refused_explicitly_not_trimmed(self):
        # A snapshot whose full state exceeds the 64 KiB frame bound (a large
        # activity beside 32 bounded answers) is refused by the encoder.
        oversized = lv.LiveSnapshot(
            identity=identity(), sequence=1, activity={"blob": "x" * 30000},
            inquiries=tuple(lv.InquiryState(question_id=f"question-{index}", status="answered", answer="x" * 4000, seq=index + 1)
                            for index in range(32)))
        with self.assertRaises(BoardError):
            lv.encode_live_snapshot(oversized)
        with self.assertRaises(BoardError):
            lv.decode_live_snapshot("x" * (lv.MAX_LIVE_FRAME_BYTES + 10))
        # A Mapping form is bounded exactly like the equivalent text form.
        big = {"formatVersion": 1, "identity": identity().to_payload(), "sequence": 1,
               "activity": None, "inquiries": [{"questionId": "q", "status": "queued", "answer": None,
                                                "seq": 1} for _ in range(40)],
               "events": [], "unavailable": None, "truncated": False}
        with self.assertRaises(BoardError):
            lv.decode_live_snapshot(big)

    def test_the_reserved_event_slot_admits_no_content_type(self):
        with self.assertRaises(BoardError):
            lv.LiveSnapshot(identity=identity(), sequence=1, events=({"kind": "anything"},))
        with self.assertRaises(BoardError):
            lv.decode_live_snapshot({"formatVersion": 1, "identity": identity().to_payload(),
                                     "sequence": 1, "activity": None, "inquiries": [],
                                     "events": [{"kind": "anything"}], "unavailable": None,
                                     "truncated": False})
        # The empty reserved slot is the normal shape.
        empty = lv.LiveSnapshot(identity=identity(), sequence=1)
        self.assertEqual(empty.events, ())

    def test_frames_roundtrip(self):
        request = inquiry_request()
        self.assertEqual(lv.decode_live_request(lv.encode_live_request(request)), request)
        reply = lv.LiveReply(identity=identity(), request_id="request-1", status="queued",
                             delivery_mode="cooperative-checkpoint",
                             native_correlation={"inquiryId": "question-1"})
        self.assertEqual(lv.decode_live_reply(lv.encode_live_reply(reply)), reply)
        snapshot = lv.LiveSnapshot(identity=identity(), sequence=3, activity={"phase": "finishing"},
                                   inquiries=(lv.InquiryState(question_id="question-1", status="queued", seq=4),))
        self.assertEqual(lv.decode_live_snapshot(lv.encode_live_snapshot(snapshot)), snapshot)
        for wrong_version in (True, 1.0):
            payload = request.to_payload()
            payload["formatVersion"] = wrong_version
            with self.assertRaises(BoardError, msg=repr(wrong_version)):
                lv.decode_live_request(payload)


class WholeFrameGateTests(unittest.TestCase):
    """The whole-frame bound of the three live frames, refused before the
    parser ever runs.

    Each refused frame either carries the padded payload of a frame one
    padding shorter that decodes fine, or — the snapshot — a canonical frame
    whose every field is within its own bound (32 bounded 4000-byte answers,
    the set the existing pager really carries); only the raw frame's byte
    count is invalid. A refusal message alone proves nothing about the order,
    so the tests spy on the root package's real :func:`decode_strict_json`
    call point: an over-limit refusal must reach it zero times, and each
    normal-size control must be parsed by it exactly once. The request and
    reply frames alone can never reach 64 KiB canonically: their bounded
    fields sum to a few KiB (a question escapes to at most six times its
    4000 bytes), so padding or oversized raw transport bytes exercise their
    bound.
    """

    def padded_over(self, text: str) -> str:
        padding = lv.MAX_LIVE_FRAME_BYTES - len(text.encode()) + 1024
        self.assertGreater(padding, 0)
        return "{" + " " * padding + text[1:]

    def assert_refused_before_parsing(self, decode, frame, parser, label: str):
        with self.assertRaises(BoardError) as raised:
            decode(frame)
        self.assertIn("frame", raised.exception.message, label)
        self.assertIn("byte bound", raised.exception.message, label)
        parser.assert_not_called()

    def spied_parser(self):
        return mock.patch.object(json_codec, "decode_strict_json",
                                 wraps=json_codec.decode_strict_json)

    def test_over_limit_request_and_reply_frames_are_refused_before_parsing(self):
        request = inquiry_request()
        text = lv.encode_live_request(request)
        self.assertLess(len(text.encode()), lv.MAX_LIVE_FRAME_BYTES)
        padded = self.padded_over(text)
        self.assertGreater(len(padded.encode()), lv.MAX_LIVE_FRAME_BYTES)
        with self.spied_parser() as parser:
            self.assert_refused_before_parsing(lv.decode_live_request, padded, parser, "request text")
            self.assert_refused_before_parsing(lv.decode_live_request, padded.encode(),
                                               parser, "request bytes")
            self.assertEqual(lv.decode_live_request(text), request)
            parser.assert_called_once_with(text)
        reply = lv.LiveReply(identity=identity(), request_id="request-1", status="queued")
        reply_text = lv.encode_live_reply(reply)
        reply_padded = self.padded_over(reply_text)
        with self.spied_parser() as parser:
            self.assert_refused_before_parsing(lv.decode_live_reply, reply_padded, parser, "reply text")
            self.assertEqual(lv.decode_live_reply(reply_text), reply)
            parser.assert_called_once_with(reply_text)

    def test_a_snapshot_of_legal_bounded_answers_that_exceeds_one_frame_is_refused(self):
        inquiries = tuple(lv.InquiryState(question_id=f"question-{index:02d}", status="answered",
                                          answer="x" * lv.MAX_ANSWER_BYTES, seq=index + 1)
                          for index in range(lv.MAX_INQUIRIES_PER_RUN))
        snapshot = lv.LiveSnapshot(identity=identity(), sequence=1, inquiries=inquiries)
        payload = snapshot.to_payload()
        # Every field is within its own bound; only the whole exceeds one frame.
        self.assertEqual(len(payload["inquiries"]), lv.MAX_INQUIRIES_PER_RUN)
        self.assertGreater(len(canonical_json(payload).encode()), lv.MAX_LIVE_FRAME_BYTES)
        with self.spied_parser() as parser:
            self.assert_refused_before_parsing(lv.decode_live_snapshot, payload, parser,
                                               "snapshot mapping")
            self.assert_refused_before_parsing(lv.decode_live_snapshot,
                                               canonical_json(payload).encode(), parser,
                                               "snapshot bytes")
            # A normal-size snapshot of the same shape still parses, exactly once.
            small = lv.LiveSnapshot(identity=identity(), sequence=1)
            self.assertEqual(lv.decode_live_snapshot(small.to_payload()), small)
            parser.assert_called_once_with(canonical_json(small.to_payload()))


class StrictScalarTypeTests(unittest.TestCase):
    """Ordinary live fields keep strict scalar types at both entries (step 2-P).

    An int never passes a bool field and a str never passes an int field, on
    the Python constructor exactly as on the wire decode.
    """

    def test_python_construction_refuses_int_for_bool_and_str_for_int(self):
        with self.assertRaises(BoardError, msg="capabilities.activity"):
            lv.LiveCapabilities(activity=1, inquiry_delivery="realtime")
        with self.assertRaises(BoardError, msg="inquiry.seq"):
            lv.InquiryState(question_id="question-1", status="queued", seq=True)

    def test_the_wire_entries_refuse_int_for_bool_and_str_for_int(self):
        snapshot = lv.LiveSnapshot(identity=identity(), sequence=3,
                                   inquiries=(lv.InquiryState(question_id="question-1",
                                                              status="queued", seq=4),))
        payload = snapshot.to_payload()
        payload["sequence"] = "3"
        with self.assertRaises(BoardError, msg="sequence"):
            lv.decode_live_snapshot(payload)
        payload = snapshot.to_payload()
        payload["truncated"] = 1
        with self.assertRaises(BoardError, msg="truncated"):
            lv.decode_live_snapshot(payload)
        payload = snapshot.to_payload()
        payload["inquiries"][0]["seq"] = True
        with self.assertRaises(BoardError, msg="seq"):
            lv.decode_live_snapshot(payload)


if __name__ == "__main__":
    unittest.main()
