"""Live DTOs and owner-published facts through the actual C-Two live seam.

No model, harness or C-Two server is started. ``cc.connect`` and
``cc.with_call_options`` are replaced at the two SDK boundaries: the fictional
peer dispatches each named RPC to the real endpoint handler.
The real channel still builds Wire DTOs, makes its bounded calls and decodes
replies; the real endpoint owns admission, replay, settlement and pagination.
The fixture journal is a local fsynced JSONL file with synthetic records, not a
native receipt or proof of a harness delivery. Tests explicitly consume a
request, commit that fixture record, then settle/publish owner facts. Real
cross-process transport belongs to test_c_two_live, with concrete IDs recorded
in the migration acceptance table.
"""
from __future__ import annotations

import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from hey_my_buddy.buddy.harnesses import live as lv
from hey_my_buddy.buddy.harnesses import c_two_live as ctl
from hey_my_buddy.buddy.harnesses.run_contract import RunIdentity
from hey_my_buddy.blackboard.tasks import inquiry as board_inquiry
from hey_my_buddy.buddy.harnesses import session_receipts
from hey_my_buddy.errors import BoardError
from hey_my_buddy.json_codec import canonical_json, decode_strict_json
from hey_my_buddy.protocol import schemas as board_schemas
from buddy.harnesses.fixtures.c_two_live_peer import TEST_CRM


def identity() -> RunIdentity:
    return RunIdentity(task_id="task-fixture", attempt_id="attempt-fixture", generation=1,
                       invocation_id="invocation-fixture", turn_id="turn-fixture",
                       input_sha256="e" * 64)


def inquiry_request(question: str = "what should I check first?", request_id: str = "request-1",
                    question_id: str = "question-1") -> lv.LiveRequest:
    return lv.LiveRequest(identity=identity(), request_id=request_id, kind="inquiry",
                          payload=lv.InquiryPayload(question_id=question_id, question=question))


class EndpointConnection:
    """Fictional SDK connection; all three operations call actual handlers."""

    def __init__(self, endpoint):
        self.endpoint = endpoint
        self.calls = []

    def __enter__(self):
        return self

    def __exit__(self, *exception):
        return False

    def capabilities(self, text):
        self.calls.append(("capabilities", text))
        return self.endpoint.capabilities(text)

    def request(self, text):
        self.calls.append(("request", text))
        return self.endpoint.request(text)

    def observe(self, text):
        self.calls.append(("observe", text))
        return self.endpoint.observe(text)


class LiveSeamCase(unittest.TestCase):
    def setUp(self):
        self.endpoint = ctl.CTwoLiveEndpoint(identity(), lv.EXISTING_CAPABILITIES["zcode"],
                                            TEST_CRM, instance_id="a" * 64, token="b" * 64,
                                            name="Ada", state_dir=Path(os.environ["BUDDY_STATE_DIR"]))
        self.peer = EndpointConnection(self.endpoint)
        def connect(*args, timeout, **kwargs):
            self.assertGreaterEqual(timeout, 0)
            return self.peer
        def with_call_options(peer, *, timeout):
            self.assertIs(peer, self.peer)
            self.assertGreaterEqual(timeout, 0)
            return peer
        self.connection = self.enterContext(patch.object(ctl.cc, "connect", side_effect=connect))
        self.enterContext(patch.object(ctl.cc, "with_call_options", side_effect=with_call_options))
        self.channel = ctl.CTwoLiveChannel(identity(), TEST_CRM, name="Ada", address="ipc://fixture",
                                           instance_id="a" * 64, token="b" * 64, state_dir=Path(os.environ["BUDDY_STATE_DIR"]))
        self.addCleanup(self.endpoint.close, reason="fixture finished")
        self.directory = self.enterContext(tempfile.TemporaryDirectory(prefix="live-fixture-"))
        self.journal = Path(self.directory) / "inquiries.jsonl"

    def commit(self, record):
        """Only fixture persistence: a complete fsynced record before settlement."""
        with self.journal.open("a", encoding="utf-8") as stream:
            stream.write(canonical_json(record) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        records = [decode_strict_json(line) for line in self.journal.read_text().splitlines()]
        for state in lv._journal_states(records):
            self.endpoint.publish_inquiry_state(state)
        self.endpoint.publish_journal(lv.LiveJournal(available=True, entries=len(
            {record["inquiryId"] for record in records})))

    def handoff(self, request=None, *, refusal=None, status="queued"):
        request = request or inquiry_request()
        result = []
        thread = threading.Thread(target=lambda: result.append(
            self.channel.request(request, timeout_ms=1500)))
        thread.start()
        self.addCleanup(thread.join, 2)
        received = self.endpoint.consume_request(timeout_s=1)
        self.assertIsNotNone(received, "the actual owner queue must receive the request")
        self.assertEqual(received, request)
        if refusal is not None:
            reply = refusal
        else:
            self.commit({"inquiryId": received.payload.question_id, "state": status})
            reply = lv.LiveReply(status=status, state=status, observed=True)
        self.endpoint.settle_request(received.request_id, reply)
        thread.join(2)
        self.assertFalse(thread.is_alive())
        self.assertEqual(len(result), 1)
        return result[0]

    def publish_records(self, records):
        for state in lv._journal_states(records):
            self.endpoint.publish_inquiry_state(state)


class CapabilityTests(unittest.TestCase):
    def test_each_harness_declares_its_existing_facilities_only(self):
            # DSH moved off the retired Node realtime channel with step four: its
            # questions now arrive at the session's cooperative checkpoint too.
        expected = {"codex": "unsupported", "claude": "unsupported",
                    "zcode": "cooperative-checkpoint", "dsh": "cooperative-checkpoint"}
        for harness, delivery in expected.items():
            capabilities = lv.EXISTING_CAPABILITIES[harness]
            self.assertEqual(capabilities.inquiry_delivery, delivery, harness)
        # The declared fact is how a question is delivered; the never-read
        # activity/finish/session bits went with step 2-D.
        self.assertEqual(sorted(lv.LiveCapabilities.model_fields), ["inquiry_delivery"])
        for name in ("activity", "finish_notice", "session_content"):
            self.assertNotIn(name, lv.LiveCapabilities.model_fields, name)

    def test_an_unknown_delivery_mode_is_refused(self):
        with self.assertRaises(BoardError):
            lv.LiveCapabilities(activity=True, inquiry_delivery="queued")


class LimitTests(unittest.TestCase):
    def test_the_live_limits_are_the_existing_inquiry_limits(self):
        self.assertEqual(lv.MAX_QUESTION_BYTES, board_schemas.MAX_QUESTION_BYTES)
        self.assertEqual(lv.MAX_ANSWER_BYTES, board_schemas.MAX_ANSWER_BYTES)
        self.assertEqual(lv.MAX_INQUIRIES_PER_RUN, board_schemas.MAX_INQUIRIES_PER_RUN)
        self.assertEqual(lv.MAX_INQUIRIES_PER_RUN, session_receipts.MAX_INQUIRIES)
        self.assertEqual(lv.MAX_QUESTION_BYTES, session_receipts.MAX_QUESTION_BYTES)
        self.assertEqual(lv.MAX_ANSWER_BYTES, session_receipts.MAX_ANSWER_BYTES)
        self.assertEqual(lv.MIN_TRANSPORT_TIMEOUT_MS, board_inquiry.MIN_TRANSPORT_TIMEOUT_MS)
        self.assertEqual(lv.MAX_TRANSPORT_TIMEOUT_MS, board_inquiry.MAX_TRANSPORT_TIMEOUT_MS)
        self.assertEqual((lv.MIN_TRANSPORT_TIMEOUT_MS, lv.MAX_TRANSPORT_TIMEOUT_MS), (100, 5000))
        # The wait bound stays owned by the board's inquiry operation, the one
        # place a wait is actually taken.
        self.assertEqual(board_inquiry.MAX_WAIT_MS, 30000)

    def test_oversized_questions_and_bad_timeouts_are_refused_at_the_value(self):
        with self.assertRaises(BoardError):
            lv.InquiryPayload(question_id="q-1", question="x" * (lv.MAX_QUESTION_BYTES + 1))
        with self.assertRaises(BoardError):
            lv.InquiryPayload(question_id="q-1", question="水" * 2001)  # 6003 UTF-8 bytes, 2001 characters
        channel = ctl.CTwoLiveChannel(identity(), TEST_CRM, name="Ada", address="ipc://fixture",
                                       instance_id="a" * 64, token="b" * 64, state_dir=Path(os.environ["BUDDY_STATE_DIR"]))
        for timeout in (99, 5001, 0, "1500"):
            with self.assertRaises(BoardError, msg=str(timeout)):
                channel.request(inquiry_request(), timeout_ms=timeout)

    def test_a_multibyte_question_at_the_byte_bound_is_accepted(self):
        payload = lv.InquiryPayload(question_id="q-1", question="水" * 1333)  # 3999 UTF-8 bytes
        self.assertEqual(len(payload.question.encode()), lv.MAX_QUESTION_BYTES - 1)


class StrictScalarTypeTests(unittest.TestCase):
    def test_python_construction_refuses_int_for_bool_and_str_for_int(self):
        with self.assertRaises(BoardError, msg="observation.ready"):
            lv.LiveObservation(ready=1)
        with self.assertRaises(BoardError, msg="inquiry.seq"):
            lv.InquiryState(question_id="question-1", status="queued", seq=True)

    def test_the_model_boundary_refuses_int_for_bool_and_str_for_int(self):
        snapshot = lv.LiveSnapshot(
            inquiries=(lv.InquiryState(question_id="question-1", status="queued", seq=4),))
        payload = snapshot.to_payload()
        payload["truncated"] = 1
        with self.assertRaises(BoardError, msg="truncated"):
            lv.LiveSnapshot.from_payload(payload)
        payload = snapshot.to_payload()
        payload["inquiries"][0]["seq"] = True
        with self.assertRaises(BoardError, msg="seq"):
            lv.LiveSnapshot.from_payload(payload)


class RequestBindingTests(LiveSeamCase):
    def test_request_id_and_question_id_are_independent_facts(self):
        reply = self.handoff()
        self.assertEqual(reply.status, "queued")
        wire = ctl.LiveWireRequest.from_payload(decode_strict_json(self.peer.calls[0][1]))
        self.assertEqual((wire.request_id, wire.payload.question_id, wire.timeout_ms),
                         ("request-1", "question-1", 1500))
        self.assertEqual(self.journal.read_text().count("\n"), 1)

    def test_a_replayed_request_returns_its_recorded_reply_without_redelivery(self):
        first = self.handoff()
        replay = self.channel.request(inquiry_request(), timeout_ms=1500)
        self.assertEqual((first.status, replay.status), ("queued", "queued"))
        self.assertTrue(replay.native_correlation.value["duplicate"])
        self.assertIsNone(self.endpoint.consume_request(0))
        self.assertEqual(self.journal.read_text().count("\n"), 1)

    def test_a_changed_payload_under_a_committed_request_id_conflicts_before_the_bridge(self):
        self.handoff()
        for changed in (inquiry_request(question="a different question"),
                        inquiry_request(question_id="question-2")):
            with self.subTest(changed=changed.payload.to_payload()):
                reply = self.channel.request(changed, timeout_ms=1500)
                self.assertEqual((reply.status, reply.reason_code),
                                 ("unavailable", "request-payload-conflict"))
                self.assertIsNone(self.endpoint.consume_request(0))
        self.assertEqual(self.journal.read_text().count("\n"), 1)

    def test_a_refused_ask_is_not_remembered_and_stays_retryable(self):
        refused = self.handoff(refusal=lv.LiveReply(status="unavailable", reason_code="not-ready"))
        self.assertEqual((refused.status, refused.reason_code), ("unavailable", "not-ready"))
        self.assertFalse(self.journal.exists())
        self.assertEqual(self.handoff().status, "queued")
        self.assertEqual(self.journal.read_text().count("\n"), 1)

    def test_a_foreign_identity_is_refused_without_touching_the_bridge(self):
        foreign = inquiry_request().model_copy(update={"identity": identity().model_copy(
            update={"task_id": "other"})})
        reply = self.channel.request(foreign, timeout_ms=1500)
        self.assertEqual((reply.status, reply.reason_code), ("unavailable", "identity-mismatch"))
        self.assertEqual(self.peer.calls, [])
        self.assertIsNone(self.endpoint.consume_request(0))

    def test_queued_is_never_upgraded_to_delivered_by_the_adapter(self):
        queued = self.handoff()
        self.assertEqual((queued.status, queued.state), ("queued", "queued"))
        self.assertEqual(self.channel.observe(inquiry_id="question-1", timeout_ms=1500)
                         .inquiries[0].status, "queued")
        self.commit({"inquiryId": "question-1", "state": "delivered"})
        later = self.channel.request(inquiry_request(request_id="request-2"), timeout_ms=1500)
        self.assertEqual((later.status, later.state), ("delivered", "delivered"))
        self.assertIsNone(self.endpoint.consume_request(0), "owner publication is the delivery fact")

    def test_unsupported_facilities_answer_unsupported(self):
        unsupported = ctl.CTwoLiveEndpoint(identity(), lv.EXISTING_CAPABILITIES["codex"],
                                           TEST_CRM, instance_id="a" * 64, token="b" * 64, state_dir=Path(os.environ["BUDDY_STATE_DIR"]))
        self.peer.endpoint = unsupported
        reply = self.channel.request(inquiry_request(), timeout_ms=1500)
        self.assertEqual((reply.status, reply.reason_code), ("unsupported", "inquiry-unsupported"))
        self.assertIsNone(unsupported.consume_request(0))

    def test_close_is_honest_and_touches_nothing_native(self):
        self.endpoint.publish_activity({"phase": "finishing", "eventSeq": 9})
        self.assertEqual(self.channel.observe(after_seq=None, limit=10, timeout_ms=1500)
                         .activity.value["phase"], "finishing")
        before = len(self.peer.calls)
        self.channel.close(reason="attempt collection finished")
        self.assertEqual(self.channel.observe(after_seq=None, limit=10, timeout_ms=1500)
                         .unavailable, "channel-closed")
        reply = self.channel.request(inquiry_request(), timeout_ms=1500)
        self.assertEqual((reply.status, reply.reason_code), ("unavailable", "channel-closed"))
        self.assertEqual(len(self.peer.calls), before)
        endpoint_snapshot = lv.LiveSnapshot.from_payload(decode_strict_json(self.endpoint.observe(
            ctl.LiveWireObserve(identity=identity(), instance_id="a" * 64, token="b" * 64,
                                limit=1, fields=("activity",)).model_dump_json())))
        self.assertEqual(endpoint_snapshot.activity.value["phase"], "finishing")
        with self.assertRaises(BoardError):
            self.channel.close(reason="")
        self.channel.close(reason="second close")


class ObserveTests(LiveSeamCase):
    def test_a_front_row_update_during_pagination_never_hides_the_back_rows(self):
        self.publish_records([{"inquiryId": f"question-{index}", "state": "answered", "answer": "x" * 4000}
                              for index in range(32)])
        page1 = self.channel.observe(after_seq=None, limit=10, timeout_ms=1500)
        self.assertEqual([entry.question_id for entry in page1.inquiries],
                         [f"question-{index}" for index in range(10)])
        self.publish_records([{"inquiryId": "question-0", "state": "answered", "answer": "changed"}])
        cursor = max(entry.seq for entry in page1.inquiries)
        page2 = self.channel.observe(after_seq=cursor, limit=10, timeout_ms=1500)
        page3 = self.channel.observe(after_seq=max(entry.seq for entry in page2.inquiries),
                                     limit=32, timeout_ms=1500)
        self.assertEqual([entry.question_id for entry in page2.inquiries],
                         [f"question-{index}" for index in range(10, 20)])
        self.assertEqual([entry.question_id for entry in page3.inquiries],
                         [f"question-{index}" for index in range(20, 32)] + ["question-0"])
        self.assertFalse(page3.truncated)
        self.assertTrue(page1.truncated)
        seen = {entry.question_id: entry.answer for page in (page1, page2, page3) for entry in page.inquiries}
        self.assertEqual((len(seen), seen["question-0"]), (32, "changed"))

    def test_activity_updates_during_pagination_do_not_disturb_the_entry_cursor(self):
        self.publish_records([{"inquiryId": f"question-{index}", "state": "queued"} for index in range(12)])
        self.endpoint.publish_activity({"phase": "streaming-model", "eventSeq": 1})
        first = self.channel.observe(after_seq=None, limit=5, timeout_ms=1500)
        self.endpoint.publish_activity({"phase": "tool-running", "eventSeq": 2})
        second = self.channel.observe(after_seq=max(entry.seq for entry in first.inquiries),
                                       limit=5, timeout_ms=1500)
        self.assertEqual([entry.question_id for entry in second.inquiries],
                         [f"question-{index}" for index in range(5, 10)])
        self.assertEqual(second.activity.value["phase"], "tool-running")
        last = self.channel.observe(after_seq=max(entry.seq for entry in second.inquiries),
                                     limit=5, timeout_ms=1500)
        self.assertEqual([entry.question_id for entry in last.inquiries], ["question-10", "question-11"])
        self.assertFalse(last.truncated)

    def test_the_full_answer_set_is_reachable_through_paged_observation(self):
        self.publish_records([{"inquiryId": f"question-{index}", "state": "answered", "answer": "x" * 4000}
                              for index in range(32)])
        first = self.channel.observe(after_seq=None, limit=4, timeout_ms=1500)
        self.assertEqual(self.channel.observe(after_seq=None, limit=4, timeout_ms=1500), first)
        for limit in (1, 7, 32, 256):
            with self.subTest(limit=limit):
                collected, after, pages = [], None, 0
                while True:
                    snapshot = self.channel.observe(after_seq=after, limit=limit, timeout_ms=1500)
                    self.assertLessEqual(len(canonical_json(snapshot.to_payload()).encode()),
                                         lv.MAX_LIVE_FRAME_BYTES)
                    collected.extend(snapshot.inquiries)
                    pages += 1
                    if not snapshot.truncated:
                        break
                    self.assertTrue(snapshot.inquiries, "a truncated page always carries progress")
                    after = max(entry.seq for entry in snapshot.inquiries)
                    self.assertLess(pages, 40)
                self.assertGreaterEqual(pages, 2)
                self.assertEqual(len(collected), 32)
                self.assertEqual({entry.question_id for entry in collected},
                                 {f"question-{index}" for index in range(32)})
                self.assertTrue(all(entry.answer == "x" * 4000 for entry in collected))

    def test_activity_is_monotone_and_journal_changes_take_fresh_sequences(self):
        self.publish_records([{"inquiryId": "question-1", "state": "claimed"},
                              {"inquiryId": "question-2", "state": "answered", "answer": "check the tests"}])
        self.endpoint.publish_activity({"phase": "waiting-model", "eventSeq": 4})
        first = self.channel.observe(after_seq=None, limit=10, timeout_ms=1500)
        self.assertEqual([entry.status for entry in first.inquiries], ["queued", "answered"])
        cursor = max(entry.seq for entry in first.inquiries)
        self.assertFalse(self.endpoint.publish_activity({"phase": "waiting-model", "eventSeq": 2}))
        stale = self.channel.observe(after_seq=cursor, limit=10, timeout_ms=1500)
        self.assertEqual(stale.inquiries, ())
        self.assertEqual(stale.activity.value["eventSeq"], 4)
        self.publish_records([{"inquiryId": "question-1", "state": "delivered"}])
        advanced = self.channel.observe(after_seq=cursor, limit=10, timeout_ms=1500)
        self.assertEqual([entry.question_id for entry in advanced.inquiries], ["question-1"])
        self.assertGreater(advanced.inquiries[0].seq, cursor)

    def test_limit_truncates_and_reports(self):
        self.publish_records([{"inquiryId": f"question-{index}", "state": "queued"} for index in range(32)])
        page = self.channel.observe(after_seq=None, limit=10, timeout_ms=1500)
        self.assertEqual(len(page.inquiries), 10)
        self.assertTrue(page.truncated)
        rest = self.channel.observe(after_seq=max(entry.seq for entry in page.inquiries), limit=256, timeout_ms=1500)
        self.assertEqual(len(page.inquiries) + len(rest.inquiries), 32)
        for arguments in ({"limit": 0}, {"after_seq": -1, "limit": 10}):
            with self.assertRaises(BoardError):
                self.channel.observe(timeout_ms=1500, **arguments)

    def test_an_unknown_journal_state_stays_unknown(self):
        self.handoff()
        self.publish_records([{"inquiryId": "question-1", "state": "mysterious"}])
        snapshot = self.channel.observe(after_seq=None, limit=10, timeout_ms=1500)
        self.assertEqual(snapshot.inquiries[0].status, "unknown")
        reply = self.channel.request(inquiry_request(), timeout_ms=1500)
        self.assertEqual((reply.status, reply.reason_code), ("unavailable", "question-state-unknown"))
        self.assertIsNone(self.endpoint.consume_request(0))

    def test_model_payload_roundtrip_on_the_actual_interface(self):
                # The live models have no separate wire codec; their payload projection
                # and validation are the model boundary itself. Unknown members (a
                # former frame envelope key included) are refused there.
        request = inquiry_request()
        self.assertEqual(lv.LiveRequest.from_payload(request.to_payload()), request)
        reply = lv.LiveReply(status="queued", observed=True,
                             native_correlation={"inquiryId": "question-1"})
        self.assertEqual(lv.LiveReply.from_payload(reply.to_payload()), reply)
        snapshot = lv.LiveSnapshot(activity={"phase": "finishing"},
                                   inquiries=(lv.InquiryState(question_id="question-1", status="queued", seq=4),))
        self.assertEqual(lv.LiveSnapshot.from_payload(snapshot.to_payload()), snapshot)
        for extra in ("formatVersion", "identity", "sequence"):
            with self.assertRaises(BoardError, msg=extra):
                lv.LiveSnapshot.from_payload({**snapshot.to_payload(), extra: 1})



class TransportFactTests(LiveSeamCase):
    def test_a_transport_refusal_keeps_reason_and_the_specific_code(self):
        reply = self.handoff(refusal=lv.LiveReply(status="unavailable", reason_code="bridge-refused",
                                                 error_code="not-ready"))
        self.assertEqual((reply.status, reply.reason_code, reply.error_code),
                         ("unavailable", "bridge-refused", "not-ready"))
        self.assertFalse(self.journal.exists())
        self.assertEqual(self.handoff().status, "queued")

    def test_a_committed_withdrawal_is_an_observed_discarded_fact_with_its_reason(self):
        self.handoff()
        reason = "withdrawn by the asking side; it no longer blocks turn completion"
        self.commit({"inquiryId": "question-1", "state": "discarded", "reason": reason})
        reply = self.channel.request(inquiry_request(), timeout_ms=1500)
        self.assertEqual((reply.status, reply.observed, reply.state), ("discarded", True, "discarded"))
        self.assertIsNone(reply.error_code)
        self.assertEqual(reply.native_correlation.value["reason"], reason)
        self.assertIsNone(self.endpoint.consume_request(0))

    def test_an_unreachable_transport_reads_as_a_fact_on_request_and_snapshot(self):
        with patch.object(ctl.cc, "connect", side_effect=OSError("fictional SDK connection unavailable")):
            reply = self.channel.request(inquiry_request(), timeout_ms=1500)
            snapshot = self.channel.observe(after_seq=None, limit=1, timeout_ms=1500,
                                            fields=("observation",))
        self.assertEqual((reply.status, reply.reason_code, reply.error_code),
                         ("unavailable", "transport-unreachable", None))
        self.assertIs(snapshot.observed, False)
        self.assertEqual(snapshot.reason, "transport-unreachable")
        self.assertIsNone(snapshot.error)
        self.assertIsNone(snapshot.observation)

    def test_an_invalid_observation_value_is_unavailable_not_reshaped(self):
        with self.assertRaises(BoardError):
            self.endpoint.publish_observation({"ready": "yes"})
        snapshot = self.channel.observe(after_seq=None, limit=1, timeout_ms=1500,
                                        fields=("observation",))
        self.assertIs(snapshot.observed, False)
        self.assertEqual(snapshot.reason, "observation-unavailable")
        self.assertIsNone(snapshot.observation)

    def test_observed_must_agree_with_the_status(self):
        with self.assertRaises(BoardError, msg="queued without observed"):
            lv.LiveReply(status="queued")
        with self.assertRaises(BoardError, msg="unavailable with observed"):
            lv.LiveReply(status="unavailable", observed=True,
                         reason_code="bridge-unreachable")
        with self.assertRaises(BoardError, msg="state without observed"):
            lv.LiveReply(status="unavailable", state="discarded")



class ObserveSelectionTests(LiveSeamCase):
    def test_an_omitted_selection_reads_everything_and_selections_narrow_it(self):
        # Publication is performed by the owner; an RPC reads its bounded cache.
        self.endpoint.publish_activity({"phase": "streaming-model", "eventSeq": 3})
        self.publish_records([{"inquiryId": "question-1", "state": "queued"}])
        self.endpoint.publish_journal(lv.LiveJournal(available=True, entries=1))
        self.endpoint.publish_observation(lv.LiveObservation(ready=True, agent_status="running"))
        everything = self.channel.observe(after_seq=None, limit=8, timeout_ms=1500)
        self.assertIsNotNone(everything.activity)
        self.assertEqual(everything.inquiries[0].question_id, "question-1")
        self.assertIsNotNone(everything.observation)
        for field in ("activity", "observation", "inquiries"):
            with self.subTest(field=field):
                snapshot = self.channel.observe(after_seq=None, limit=8, timeout_ms=1500, fields=(field,))
                self.assertEqual(snapshot.activity is not None, field == "activity")
                self.assertEqual(snapshot.observation is not None, field == "observation")
                self.assertEqual(bool(snapshot.inquiries), field == "inquiries")
                self.assertEqual(snapshot.journal is not None, field == "inquiries")
        self.assertEqual(self.channel.observe(after_seq=None, limit=8, timeout_ms=1500, fields=()), everything)
        for bad in (("activity", "events"), "activity", ("activity", 1)):
            with self.assertRaises(BoardError, msg=repr(bad)):
                self.channel.observe(after_seq=None, limit=8, timeout_ms=1500, fields=bad)

    def test_a_selection_that_names_an_absent_source_reports_it_honestly(self):
        snapshot = self.channel.observe(after_seq=None, limit=8, timeout_ms=1500, fields=("observation",))
        self.assertIs(snapshot.observed, False)
        self.assertEqual(snapshot.reason, "observation-unavailable")

    def test_the_single_answer_point_query_reads_neither_activity_nor_journal(self):
        self.endpoint.publish_activity({"phase": "streaming-model", "eventSeq": 3})
        self.endpoint.publish_journal(lv.LiveJournal(available=True, entries=1))
        self.endpoint.publish_observation(lv.LiveObservation(ready=True))
        self.publish_records([{"inquiryId": "question-9", "state": "answered",
                              "answer": {"text": "42", "bytes": 2, "via": "tool:buddy_answer_inquiry",
                                         "toolCallId": "call-9", "at": "2026-10-06T00:00:01Z",
                                         "truncated": False}}])
        snapshot = self.channel.observe(inquiry_id="question-9", timeout_ms=1500)
        self.assertIsNone(snapshot.activity)
        self.assertIsNone(snapshot.journal)
        self.assertIsNone(snapshot.observation)
        entry = snapshot.inquiries[0]
        self.assertEqual((entry.status, entry.answer, entry.tool_call_id, entry.bytes, entry.truncated),
                         ("answered", "42", "call-9", 2, False))
        self.assertEqual(entry.via, "tool:buddy_answer_inquiry")
        self.assertTrue(entry.at)
        for bad in ("", "x" * (lv.MAX_REQUEST_ID + 1)):
            with self.assertRaises(BoardError):
                self.channel.observe(inquiry_id=bad, timeout_ms=1500)

    def test_journal_source_fields_and_delivery_survive_the_projection(self):
        # This pure function remains used by real owners. Neither fixture form
        # asserts that a native SDK actually committed these synthetic records.
        journal = [
            {"inquiryId": "question-1", "state": "queued",
             "delivery": {"requestedDelivery": None, "admittedDelivery": "cooperative-checkpoint"}},
            {"inquiryId": "question-1", "state": "answered",
             "answer": {"text": "the answer", "bytes": 10, "via": "tool:buddy_answer_inquiry",
                        "toolCallId": "call-1", "at": "2026-10-06T00:00:02Z", "truncated": False}},
        ]
        entry, = lv._journal_states(journal)
        self.assertEqual((entry.status, entry.answer, entry.bytes, entry.truncated),
                         ("answered", "the answer", 10, False))
        self.assertEqual(entry.tool_call_id, "call-1")
        self.assertEqual(entry.delivery.value["admittedDelivery"], "cooperative-checkpoint")
        bare = [{"inquiryId": "question-2", "state": "answered", "answer": "from the node journal",
                 "answerBytes": 21, "via": "tool:buddy_inquiry_reply", "toolCallId": "call-2",
                 "answeredAt": "2026-09-19T05:00:04.000Z", "truncated": True}]
        entry, = lv._journal_states(bare)
        self.assertEqual((entry.status, entry.answer, entry.bytes, entry.truncated, entry.via,
                          entry.tool_call_id, entry.at),
                         ("answered", "from the node journal", 21, True, "tool:buddy_inquiry_reply",
                          "call-2", "2026-09-19T05:00:04.000Z"))

    def test_the_journal_fact_reports_availability_count_and_rejections(self):
        self.endpoint.publish_journal(lv.LiveJournal(available=False, reason="journal-not-written", entries=0))
        snapshot = self.channel.observe(after_seq=None, limit=8, timeout_ms=1500, fields=("inquiries",))
        self.assertEqual((snapshot.journal.available, snapshot.journal.reason, snapshot.journal.entries),
                         (False, "journal-not-written", 0))
        self.assertEqual(snapshot.inquiries, ())
        self.publish_records([{"inquiryId": "question-1", "state": "queued"}])
        self.endpoint.publish_journal({"available": True, "reason": None, "entries": 2,
                                       "rejections": [{"questionId": "question-foreign",
                                                       "reason": "the journal record belongs to another task"}]})
        snapshot = self.channel.observe(after_seq=None, limit=8, timeout_ms=1500, fields=("inquiries",))
        self.assertEqual((snapshot.journal.available, snapshot.journal.entries), (True, 2))
        self.assertEqual([item.question_id for item in snapshot.journal.rejections], ["question-foreign"])
        self.assertEqual(snapshot.journal.rejections[0].reason, "the journal record belongs to another task")
        self.assertEqual([entry.question_id for entry in snapshot.inquiries], ["question-1"])
        for invalid in ({"available": False, "reason": "mysterious", "entries": 0, "rejections": []},
                        [{"inquiryId": "question-1", "state": "queued"}]):
            with self.assertRaises(BoardError):
                self.endpoint.publish_journal(invalid)

    def test_a_bound_record_keeps_its_limitation_for_the_public_reason(self):
        note = "Host questions are queued by the bridge and delivered only at the root's next checkpoint"
        journal = [{"inquiryId": "question-1", "state": "unavailable",
                    "reason": "the governed root turn ended before this inquiry was answered", "limitation": note}]
        self.publish_records(journal)
        entry = self.channel.observe(after_seq=None, limit=8, timeout_ms=1500,
                                      fields=("inquiries",)).inquiries[0]
        self.assertEqual((entry.status, entry.reason, entry.limitation),
                         ("unavailable", "the governed root turn ended before this inquiry was answered", note))

    def test_failed_owner_observation_keeps_its_facts_without_clearing_activity(self):
        self.endpoint.publish_activity({"phase": "streaming-model", "eventSeq": 6})
        self.endpoint.publish_observation(lv.LiveObservation(ready=True))
        self.endpoint.publish_snapshot(lv.LiveSnapshot(observed=False, reason="bridge-refused", error="not-ready"))
        broken = self.channel.observe(after_seq=None, limit=8, timeout_ms=1500)
        self.assertEqual((broken.observed, broken.reason, broken.error), (False, "bridge-refused", "not-ready"))
        self.assertIsNone(broken.observation)
        self.assertEqual(broken.activity.value["eventSeq"], 6)
        narrow = self.channel.observe(after_seq=None, limit=8, timeout_ms=1500, fields=("activity",))
        self.assertEqual((narrow.observed, narrow.reason, narrow.error), (None, None, None))

    def test_live_event_bounds_are_the_producers_character_truncations(self):
        def event(kind: str, tool_name: str | None = None):
            return lv.LiveEvent(at="2026-10-06T00:00:00Z", kind=kind, tool_name=tool_name)
        self.assertEqual(event("k" * 80, "t" * 120).tool_name, "t" * 120)
        with self.assertRaises(BoardError):
            event("k" * 81)
        with self.assertRaises(BoardError):
            event("k", "t" * 121)
        with self.assertRaises(BoardError):
            event("")
        # A multibyte kind at the character bound is not a byte-bound victim.
        self.assertEqual(event("水" * 80).kind, "水" * 80)

    def test_observation_payload_roundtrip_with_their_new_fields(self):
        snapshot = lv.LiveSnapshot(
            observation=lv.LiveObservation(ready=True, observed_at="2026-10-06T00:00:00Z",
                                           agent_status="running", delivery_mode="cooperative-checkpoint",
                                           recent_activity=(lv.LiveEvent(at="2026-10-06T00:00:00Z",
                                                                         kind="tool.updated",
                                                                         tool_name="read"),)),
            observed=True)
        self.assertEqual(lv.LiveSnapshot.from_payload(snapshot.to_payload()), snapshot)
        payload = snapshot.to_payload()["observation"]
        self.assertIn("recentActivity", payload)
        self.assertIn("observed", snapshot.to_payload())
        reply = lv.LiveReply(status="unavailable",
                             reason_code="bridge-refused", error_code="not-ready")
        self.assertEqual(lv.LiveReply.from_payload(reply.to_payload()), reply)





if __name__ == "__main__":
    unittest.main()
