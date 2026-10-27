"""In-process live admission and publication semantics; no C-Two or harness runs."""
from __future__ import annotations

import json
import threading
import time
import unittest

from hey_my_buddy.buddy.harnesses import c_two_live as ctl
from hey_my_buddy.buddy.harnesses import live as lv
from hey_my_buddy.buddy.harnesses.run_contract import RunIdentity
from hey_my_buddy.errors import BoardError
from hey_my_buddy.json_codec import canonical_json, decode_strict_json

QUEUED_REPLY = dict(status="queued", observed=True, state="queued")


def endpoint(run: RunIdentity | None = None, *, delivery: str = "cooperative-checkpoint") -> ctl.CTwoLiveEndpoint:
    return ctl.CTwoLiveEndpoint(run or identity(), lv.LiveCapabilities(inquiry_delivery=delivery))


def local_request(endpoint, request, *, timeout_ms=3000, deadline_monotonic=None) -> str:
    return endpoint.request_local(request, timeout_ms=timeout_ms,
        deadline_monotonic=(time.monotonic() + timeout_ms / 1000.0
                            if deadline_monotonic is None else deadline_monotonic))


def identity(**overrides) -> RunIdentity:
    values = dict(task_id='task-live', attempt_id='attempt-live', generation=1, invocation_id='invocation-live', turn_id='turn-live', input_sha256=None)
    values.update(overrides)
    return RunIdentity(**values)

def inquiry_request(request_id: str='request-1', question_id: str='question-1', question: str='what should I check first?', run: RunIdentity | None=None) -> lv.LiveRequest:
    return lv.LiveRequest(identity=run or identity(), request_id=request_id, kind='inquiry', payload=lv.InquiryPayload(question_id=question_id, question=question))

def decode_reply(raw: str) -> lv.LiveReply:
    return lv.LiveReply.from_payload(decode_strict_json(raw))

def decode_snapshot(raw: str) -> lv.LiveSnapshot:
    return lv.LiveSnapshot.from_payload(decode_strict_json(raw))

class FakeClock:
    """A monotonic clock the window checks can actually see expire."""

    def __init__(self, step: float):
        self._step = step
        self._now = 1000.0

    def monotonic(self) -> float:
        self._now += self._step
        return self._now

class EndpointAdmissionTests(unittest.TestCase):
    """In-process admission, owner settlement, replay and window semantics."""

    def setUp(self):
        self.endpoint = endpoint()
        self.run = self.endpoint.identity

    def raw_ask(self, request: lv.LiveRequest, *, timeout_ms: int=3000) -> str:
        return local_request(self.endpoint, request, timeout_ms=timeout_ms)

    def ask(self, request: lv.LiveRequest, **kwargs) -> lv.LiveReply:
        return decode_reply(self.raw_ask(request, **kwargs))

    def wait_pending(self, request_id: str, *, timeout: float=3.0) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with self.endpoint._lock:
                if request_id in self.endpoint._pending:
                    return
            time.sleep(0.005)
        raise AssertionError('the request never became pending')

    def deliver(self, request: lv.LiveRequest, reply: lv.LiveReply | None=None, *, timeout_ms: int=3000) -> lv.LiveReply:
        """One ask driven through the real hand-off: enqueue, consume, settle."""
        box: list[str] = []
        thread = threading.Thread(target=lambda: box.append(self.raw_ask(request, timeout_ms=timeout_ms)), daemon=True)
        thread.start()
        self.wait_pending(request.request_id)
        consumed = self.endpoint.consume_request(1.0)
        self.assertIsNotNone(consumed)
        self.assertEqual(consumed.request_id, request.request_id)
        self.endpoint.settle_request(request.request_id, reply or lv.LiveReply(**QUEUED_REPLY))
        thread.join(timeout=5.0)
        self.assertFalse(thread.is_alive())
        return decode_reply(box[0])

    def observe_entries(self) -> tuple[lv.InquiryState, ...]:
        return decode_snapshot(self.endpoint.observe_local(limit=10)).inquiries

    def test_a_request_waits_for_the_owners_settlement_and_commits_once(self):
        box: list[str] = []
        thread = threading.Thread(target=lambda: box.append(self.raw_ask(inquiry_request())), daemon=True)
        thread.start()
        self.wait_pending('request-1')
        self.assertEqual(self.observe_entries(), ())
        consumed = self.endpoint.consume_request(1.0)
        self.assertEqual((consumed.request_id, consumed.payload.question_id), ('request-1', 'question-1'))
        self.assertIsNone(self.endpoint.consume_request(0.0))
        self.endpoint.settle_request('request-1', lv.LiveReply(**QUEUED_REPLY))
        thread.join(timeout=5.0)
        self.assertFalse(thread.is_alive())
        reply = decode_reply(box[0])
        self.assertEqual((reply.status, reply.observed, reply.state), ('queued', True, 'queued'))
        self.assertEqual(reply.native_correlation.value, {'questionId': 'question-1', 'duplicate': False})
        self.assertEqual([(entry.question_id, entry.status) for entry in self.observe_entries()], [('question-1', 'queued')])
        self.assertEqual(self.ask(inquiry_request()).native_correlation.value['duplicate'], True)
        self.assertIsNone(self.endpoint.consume_request(0.0))
        self.assertEqual(self.endpoint._pending, {})

    def test_an_owner_refusal_keeps_the_request_retryable(self):
        refused = self.deliver(inquiry_request(), lv.LiveReply(status='unavailable', reason_code='journal-unavailable', error_code='not-ready'))
        self.assertEqual((refused.status, refused.reason_code, refused.error_code), ('unavailable', 'journal-unavailable', 'not-ready'))
        self.assertEqual(self.observe_entries(), ())
        self.assertEqual((self.endpoint._admitted, self.endpoint._requests, self.endpoint._pending), ({}, {}, {}))
        retried = self.deliver(inquiry_request())
        self.assertEqual((retried.status, retried.state), ('queued', 'queued'))
        self.assertEqual([(entry.question_id, entry.status) for entry in self.observe_entries()], [('question-1', 'queued')])

    def test_a_window_expiry_drops_the_unconsumed_request(self):
        expired = self.ask(inquiry_request(), timeout_ms=150)
        self.assertEqual((expired.status, expired.reason_code), ('unavailable', 'request-window-expired'))
        self.assertIsNone(self.endpoint.consume_request(0.1))
        self.assertEqual(self.endpoint._pending, {})
        self.assertEqual(self.observe_entries(), ())
        retried = self.deliver(inquiry_request())
        self.assertEqual(retried.status, 'queued')

    def test_consume_checks_deadline_even_before_rpc_waiter_marks_expiry(self):
        request = inquiry_request()
        slot = ctl._PendingRequest(request.payload.question_id, ctl._request_digest(request), time.monotonic() - 1.0)
        self.endpoint._pending[request.request_id] = slot
        self.endpoint._queue.put_nowait((request, slot))
        self.assertFalse(slot.expired)
        self.assertIsNone(self.endpoint.consume_request(0.0))
        self.assertTrue(slot.expired)
        self.assertEqual(self.endpoint._pending, {})

    def test_close_wakes_waiting_requests_without_delivery(self):
        box: list[str] = []
        thread = threading.Thread(target=lambda: box.append(self.raw_ask(inquiry_request(), timeout_ms=5000)), daemon=True)
        thread.start()
        self.wait_pending('request-1')
        self.endpoint.close(reason='run-finished')
        thread.join(timeout=5.0)
        self.assertFalse(thread.is_alive())
        self.assertEqual((decode_reply(box[0]).status, decode_reply(box[0]).reason_code), ('unavailable', 'channel-closed'))
        self.assertIsNone(self.endpoint.consume_request(0.0))
        self.assertEqual(self.endpoint._pending, {})
        self.assertEqual(self.observe_entries(), ())

    def test_a_concurrent_duplicate_joins_the_in_flight_delivery(self):
        boxes: list[list[str]] = ([], [])
        threads = [threading.Thread(target=lambda index=index: boxes[index].append(self.raw_ask(inquiry_request(request_id=f'request-{index + 1}'), timeout_ms=5000)), daemon=True) for index in range(2)]
        for thread in threads:
            thread.start()
        self.wait_pending('request-1')
        time.sleep(0.2)
        self.assertEqual(sorted(self.endpoint._pending), ['request-1', 'request-2'])
        self.assertIs(self.endpoint._pending['request-1'], self.endpoint._pending['request-2'])
        self.assertEqual(self.endpoint._queue.qsize(), 1)
        consumed = self.endpoint.consume_request(1.0)
        self.assertEqual(consumed.request_id, 'request-1')
        self.endpoint.settle_request('request-1', lv.LiveReply(**QUEUED_REPLY))
        for thread in threads:
            thread.join(timeout=5.0)
            self.assertFalse(thread.is_alive())
        for box in boxes:
            self.assertEqual(decode_reply(box[0]).status, 'queued')
        self.assertEqual(self.endpoint._requests, {'request-1': 'question-1', 'request-2': 'question-1'})
        refused = self.ask(inquiry_request(request_id='request-2', question_id='question-2'))
        self.assertEqual((refused.status, refused.reason_code), ('unavailable', 'request-payload-conflict'))
        self.assertIsNone(self.endpoint.consume_request(0.0))
        self.assertEqual([(entry.question_id, entry.seq) for entry in self.observe_entries()], [('question-1', 1)])

    def test_a_closed_or_dead_entry_is_never_handed_to_the_owner(self):
        box: list[str] = []
        thread = threading.Thread(target=lambda: box.append(self.raw_ask(inquiry_request(), timeout_ms=5000)), daemon=True)
        thread.start()
        self.wait_pending('request-1')
        self.endpoint.close(reason='run-finished')
        thread.join(timeout=5.0)
        self.assertFalse(thread.is_alive())
        self.assertEqual(decode_reply(box[0]).reason_code, 'channel-closed')
        self.assertIsNone(self.endpoint.consume_request(0.0))
        fresh = endpoint()
        box2: list[str] = []
        thread2 = threading.Thread(target=lambda: box2.append(local_request(fresh, inquiry_request(request_id='request-2', question_id='question-2'), timeout_ms=5000)), daemon=True)
        thread2.start()
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline and 'request-2' not in fresh._pending:
            time.sleep(0.005)
        self.assertIn('request-2', fresh._pending)
        fresh.settle_request('request-2', lv.LiveReply(**QUEUED_REPLY))
        thread2.join(timeout=5.0)
        self.assertEqual(decode_reply(box2[0]).status, 'queued')
        self.assertIsNone(fresh.consume_request(0.0))
        self.assertEqual(fresh._pending, {})

    def test_zero_timeout_consumes_without_waiting(self):
        boxes: list[list[str]] = []
        waiting: list[threading.Thread] = []
        for index in range(3):
            box: list[str] = []
            boxes.append(box)
            thread = threading.Thread(target=lambda box=box, index=index: box.append(self.raw_ask(inquiry_request(request_id=f'r-{index}', question_id=f'q-{index}'), timeout_ms=5000)), daemon=True)
            waiting.append(thread)
            thread.start()
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline and len(self.endpoint._pending) < 3:
            time.sleep(0.005)
        self.assertEqual(len(self.endpoint._pending), 3)
        drained = []
        for index in range(3):
            started = time.monotonic()
            consumed = self.endpoint.consume_request(0.0)
            self.assertLess(time.monotonic() - started, 0.5)
            self.assertIsNotNone(consumed)
            drained.append(consumed.request_id)
        self.assertEqual(sorted(drained), ['r-0', 'r-1', 'r-2'])
        started = time.monotonic()
        self.assertIsNone(self.endpoint.consume_request(0.0))
        self.assertLess(time.monotonic() - started, 0.5)
        for request_id in drained:
            self.endpoint.settle_request(request_id, lv.LiveReply(**QUEUED_REPLY))
        for thread in waiting:
            thread.join(timeout=5.0)
            self.assertFalse(thread.is_alive())
        for box in boxes:
            self.assertTrue(box)
            self.assertEqual(decode_reply(box[0]).status, 'queued')

    def test_a_join_past_the_request_budget_is_refused(self):
        self.deliver(inquiry_request())
        for index in range(2, lv.MAX_INQUIRIES_PER_RUN):
            self.ask(inquiry_request(request_id=f'alias-{index}'))
        self.assertEqual(len(self.endpoint._requests), lv.MAX_INQUIRIES_PER_RUN - 1)
        box: list[str] = []
        thread = threading.Thread(target=lambda: box.append(self.raw_ask(inquiry_request(request_id='join-1', question_id='question-9'), timeout_ms=5000)), daemon=True)
        thread.start()
        self.wait_pending('join-1')
        refused = self.ask(inquiry_request(request_id='join-2', question_id='question-9'), timeout_ms=1500)
        self.assertEqual((refused.status, refused.reason_code), ('unavailable', 'request-limit'))
        self.assertEqual(sorted(self.endpoint._pending), ['join-1'])
        self.endpoint.settle_request('join-1', lv.LiveReply(**QUEUED_REPLY))
        thread.join(timeout=5.0)
        self.assertFalse(thread.is_alive())
        self.assertEqual(decode_reply(box[0]).status, 'queued')

    def test_settle_keeps_the_owners_published_state(self):
        delivery = {'requestedDelivery': None, 'admittedDelivery': 'cooperative-checkpoint', 'startsNewTurn': False, 'extendsDeadline': False, 'supported': True}
        box: list[str] = []
        thread = threading.Thread(target=lambda: box.append(self.raw_ask(inquiry_request())), daemon=True)
        thread.start()
        self.wait_pending('request-1')
        self.endpoint.consume_request(1.0)
        self.endpoint.publish_inquiry_state(lv.InquiryState(question_id='question-1', status='queued', delivery=delivery))
        self.endpoint.settle_request('request-1', lv.LiveReply(**QUEUED_REPLY))
        thread.join(timeout=5.0)
        self.assertEqual(decode_reply(box[0]).status, 'queued')
        entry = self.observe_entries()[0]
        self.assertEqual((entry.status, entry.delivery.value), ('queued', delivery))
        seq_before = entry.seq
        self.endpoint.settle_request('request-1', lv.LiveReply(**QUEUED_REPLY))
        again = self.observe_entries()[0]
        self.assertEqual((again.status, again.seq, again.delivery.value), ('queued', seq_before, delivery))
        self.endpoint.publish_inquiry_state(lv.InquiryState(question_id='question-1', status='answered', answer='yes', delivery=delivery))
        self.endpoint.settle_request('request-1', lv.LiveReply(**QUEUED_REPLY))
        answered = self.observe_entries()[0]
        self.assertEqual((answered.status, answered.answer, answered.delivery.value), ('answered', 'yes', delivery))
        self.deliver(inquiry_request(request_id='request-3', question_id='question-3'), lv.LiveReply(status='answered', observed=True))
        projected = [entry for entry in self.observe_entries() if entry.question_id == 'question-3'][0]
        self.assertEqual(projected.status, 'answered')

    def test_one_accepted_question_is_queued_once_and_observed(self):
        reply = self.deliver(inquiry_request())
        self.assertEqual((reply.status, reply.observed, reply.state), ('queued', True, 'queued'))
        self.assertEqual(reply.native_correlation.value, {'questionId': 'question-1', 'duplicate': False})
        self.assertIsNone(self.endpoint.consume_request(0.0))
        self.assertEqual(self.endpoint._pending, {})

    def test_replays_and_duplicates_never_deliver_twice(self):
        self.deliver(inquiry_request())
        self.assertEqual(self.ask(inquiry_request()).native_correlation.value['duplicate'], True)
        self.assertEqual(self.ask(inquiry_request(request_id='request-2')).native_correlation.value, {'questionId': 'question-1', 'duplicate': True})
        self.assertIsNone(self.endpoint.consume_request(0.0))
        self.assertEqual(self.endpoint._queue.qsize(), 0)
        self.endpoint.publish_inquiry_state(lv.InquiryState(question_id='question-1', status='delivered'))
        self.assertEqual(self.ask(inquiry_request(request_id='request-3')).state, 'delivered')

    def test_a_committed_request_id_binds_one_queue_entry(self):
        self.deliver(inquiry_request())
        self.assertEqual((self.endpoint._admitted, self.endpoint._requests), ({'question-1': self.endpoint._admitted['question-1']}, {'request-1': 'question-1'}))
        self.ask(inquiry_request())
        refused = self.ask(inquiry_request(question_id='question-2'))
        self.assertEqual((refused.status, refused.reason_code), ('unavailable', 'request-payload-conflict'))
        accepted = self.deliver(inquiry_request(request_id='request-4', question_id='question-2'))
        self.assertEqual(accepted.status, 'queued')
        self.assertEqual(self.ask(inquiry_request(request_id='request-5')).native_correlation.value, {'questionId': 'question-1', 'duplicate': True})
        refused = self.ask(inquiry_request(request_id='request-6', question='a different question'))
        self.assertEqual((refused.status, refused.reason_code), ('unavailable', 'question-payload-conflict'))
        refused = self.ask(inquiry_request(question='another different question'))
        self.assertEqual((refused.status, refused.reason_code), ('unavailable', 'request-payload-conflict'))
        self.assertEqual(self.endpoint._queue.qsize(), 0)
        self.assertEqual(self.endpoint._pending, {})
        self.assertEqual(sorted(self.endpoint._requests), ['request-1', 'request-4', 'request-5'])

    def test_the_request_id_budget_bounds_aliases_explicitly(self):
        self.deliver(inquiry_request())
        for index in range(2, lv.MAX_INQUIRIES_PER_RUN + 1):
            self.assertEqual(self.ask(inquiry_request(request_id=f'alias-{index}')).native_correlation.value, {'questionId': 'question-1', 'duplicate': True}, f'alias-{index}')
        self.assertEqual(len(self.endpoint._requests), lv.MAX_INQUIRIES_PER_RUN)
        refused = self.ask(inquiry_request(request_id='alias-late'))
        self.assertEqual((refused.status, refused.reason_code), ('unavailable', 'request-limit'))
        self.assertEqual(len(self.endpoint._requests), lv.MAX_INQUIRIES_PER_RUN)
        self.assertEqual(self.ask(inquiry_request()).native_correlation.value['duplicate'], True)
        refused = self.ask(inquiry_request(question='changed under a known id'))
        self.assertEqual(refused.reason_code, 'request-payload-conflict')
        self.assertIsNone(self.endpoint.consume_request(0.0))

    def test_changed_payloads_are_conflicts(self):
        self.deliver(inquiry_request())
        refused = self.ask(inquiry_request(question='a different question'))
        self.assertEqual((refused.status, refused.reason_code), ('unavailable', 'request-payload-conflict'))
        refused = self.ask(inquiry_request(request_id='request-9', question='a different question'))
        self.assertEqual((refused.status, refused.reason_code), ('unavailable', 'question-payload-conflict'))

    def test_closed_unsupported_limit_window_and_queue_full_are_explicit_facts(self):
        self.endpoint.close(reason='run-finished')
        reply = self.ask(inquiry_request())
        self.assertEqual((reply.status, reply.reason_code), ('unavailable', 'channel-closed'))
        fresh = endpoint(delivery='unsupported')
        reply = decode_reply(local_request(fresh, inquiry_request()))
        self.assertEqual((reply.status, reply.reason_code), ('unsupported', 'inquiry-unsupported'))
        limited = endpoint()
        boxes: list[list[str]] = []
        waiting: list[threading.Thread] = []
        for index in range(lv.MAX_INQUIRIES_PER_RUN):
            box: list[str] = []
            boxes.append(box)
            thread = threading.Thread(target=lambda box=box, index=index: box.append(local_request(limited, inquiry_request(request_id=f'r-{index}', question_id=f'q-{index}'), timeout_ms=5000)), daemon=True)
            waiting.append(thread)
            thread.start()
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline and len(limited._pending) < lv.MAX_INQUIRIES_PER_RUN:
            time.sleep(0.005)
        self.assertEqual(len(limited._pending), lv.MAX_INQUIRIES_PER_RUN)
        refused = decode_reply(local_request(limited, inquiry_request(request_id='r-late', question_id='q-late')))
        self.assertEqual((refused.status, refused.reason_code), ('unavailable', 'inquiry-limit'))
        settled = limited.consume_request(1.0)
        limited.settle_request(settled.request_id, lv.LiveReply(**QUEUED_REPLY))
        refused = decode_reply(local_request(limited, inquiry_request(request_id='r-late', question_id='q-late')))
        self.assertEqual(refused.reason_code, 'inquiry-limit')
        limited.close(reason='run-finished')
        for thread in waiting:
            thread.join(timeout=5.0)
            self.assertFalse(thread.is_alive())
        outcomes = sorted(('queued' if decode_reply(box[0]).reason_code is None else decode_reply(box[0]).reason_code for box in boxes))
        self.assertEqual(outcomes, ['channel-closed'] * (len(boxes) - 1) + ['queued'])
        self.assertEqual(limited._pending, {})
        windowed = endpoint()
        real_time = ctl.time
        ctl.time = FakeClock(step=0.5)
        try:
            refused = decode_reply(local_request(windowed, inquiry_request(), timeout_ms=100))
        finally:
            ctl.time = real_time
        self.assertEqual((refused.status, refused.reason_code), ('unavailable', 'request-window-expired'))
        stalled = endpoint()
        for _ in range(lv.MAX_INQUIRIES_PER_RUN):
            stalled._queue.put_nowait((inquiry_request(), None))
        refused = decode_reply(local_request(stalled, inquiry_request()))
        self.assertEqual((refused.status, refused.reason_code), ('unavailable', 'queue-full'))

class EndpointObservationTests(unittest.TestCase):

    def test_capabilities_serves_the_owner_declaration(self):
        capabilities = lv.LiveCapabilities.from_payload(
            decode_strict_json(self.endpoint.capabilities_local()))
        self.assertEqual(capabilities.inquiry_delivery, 'cooperative-checkpoint')

    def setUp(self):
        self.endpoint = endpoint()
        self.run = self.endpoint.identity

    def observe(self, **arguments) -> lv.LiveSnapshot:
        if 'inquiry_id' not in arguments and 'limit' not in arguments:
            arguments.setdefault('limit', 10)
        return decode_snapshot(self.endpoint.observe_local(**arguments))

    def test_snapshot_pages_the_full_32x4000_fact_set_within_one_frame(self):
        answer = 'a' * 4000
        for index in range(lv.MAX_INQUIRIES_PER_RUN):
            self.endpoint.publish_inquiry_state(lv.InquiryState(question_id=f'q-{index:02d}', status='answered', answer=answer))
        collected: dict[str, lv.InquiryState] = {}
        after = None
        pages = 0
        while True:
            snapshot = self.observe(after_seq=after, limit=lv.MAX_OBSERVE_LIMIT)
            pages += 1
            for entry in snapshot.inquiries:
                collected.setdefault(entry.question_id, entry)
            self.assertLessEqual(len(canonical_json(snapshot.to_payload()).encode()), lv.MAX_LIVE_FRAME_BYTES)
            if not snapshot.truncated:
                break
            after = snapshot.inquiries[-1].seq
        self.assertEqual(len(collected), lv.MAX_INQUIRIES_PER_RUN)
        self.assertTrue(all((entry.answer == answer for entry in collected.values())))
        self.assertGreater(pages, 1)

    def test_only_newer_activity_is_kept_and_served(self):
        self.assertTrue(self.endpoint.publish_activity({'phase': 'starting', 'eventSeq': 1}))
        self.assertFalse(self.endpoint.publish_activity({'phase': 'starting', 'eventSeq': 1}))
        self.assertFalse(self.endpoint.publish_activity({'phase': 'starting', 'eventSeq': 0}))
        self.assertTrue(self.endpoint.publish_activity({'phase': 'streaming-model', 'eventSeq': 2}))
        snapshot = self.observe(fields=('activity',))
        self.assertEqual(snapshot.activity.value, {'phase': 'streaming-model', 'eventSeq': 2})
        with self.assertRaises(BoardError):
            self.endpoint.publish_activity({'phase': 'not-a-phase'})
        self.assertEqual(self.observe(fields=('activity',)).activity.value, {'phase': 'streaming-model', 'eventSeq': 2})

    def test_selection_point_query_and_observation_facts(self):
        self.endpoint.publish_activity({'phase': 'tool-running', 'eventSeq': 4})
        self.endpoint.publish_observation(lv.LiveObservation(ready=True, agent_status='waiting'))
        self.endpoint.publish_inquiry_state(lv.InquiryState(question_id='q-1', status='answered', answer='yes'))
        everything = self.observe(limit=10)
        self.assertEqual(everything.activity.value['phase'], 'tool-running')
        self.assertEqual(everything.observation.agent_status, 'waiting')
        self.assertEqual([entry.question_id for entry in everything.inquiries], ['q-1'])
        self.assertTrue(everything.observed)
        only_activity = self.observe(limit=10, fields=('activity',))
        self.assertIsNone(only_activity.observation)
        self.assertEqual(only_activity.inquiries, ())
        self.assertIsNone(only_activity.observed)
        point = self.observe(inquiry_id='q-1')
        self.assertEqual([entry.question_id for entry in point.inquiries], ['q-1'])
        self.assertEqual(point.inquiries[0].answer, 'yes')
        missing = self.observe(inquiry_id='q-nope')
        self.assertEqual(missing.inquiries, ())
        self.assertTrue(missing.observed)

    def test_observation_read_facts_match_the_existing_three(self):
        no_source = self.observe(limit=10)
        self.assertEqual((no_source.observed, no_source.reason, no_source.error, no_source.observation), (False, 'observation-unavailable', None, None))
        self.endpoint.publish_observation(lv.LiveObservation(ready=True, agent_status='streaming'))
        success = self.observe(limit=10)
        self.assertEqual((success.observed, success.reason, success.error), (True, None, None))
        self.assertEqual(success.observation.agent_status, 'streaming')
        self.endpoint.publish_activity({'phase': 'tool-running', 'eventSeq': 3})
        self.endpoint.publish_snapshot(lv.LiveSnapshot(observed=False, reason='bridge-write-failed', error='peer-timeout'))
        failed = self.observe(limit=10)
        self.assertEqual((failed.observed, failed.reason, failed.error, failed.observation), (False, 'bridge-write-failed', 'peer-timeout', None))
        self.assertEqual(failed.activity.value['phase'], 'tool-running')
        self.endpoint.publish_snapshot(lv.LiveSnapshot(observation=lv.LiveObservation(ready=True, agent_status='waiting'), journal=lv.LiveJournal(available=True, entries=2)))
        recovered = self.observe(limit=10)
        self.assertEqual((recovered.observed, recovered.reason), (True, None))
        self.assertEqual(recovered.observation.agent_status, 'waiting')
        self.assertEqual((recovered.journal.available, recovered.journal.entries), (True, 2))
        untouched = self.observe(limit=10, fields=('activity',))
        self.assertIsNone(untouched.observed)
        self.assertIsNone(untouched.reason)
        self.assertIsNone(untouched.observation)
        self.assertIsNone(untouched.journal)

    def test_the_journal_fact_is_what_the_owner_published(self):
        self.assertIsNone(self.observe(limit=10, fields=('inquiries',)).journal)
        self.endpoint.publish_journal(lv.LiveJournal(available=True, entries=0))
        empty = self.observe(limit=10, fields=('inquiries',))
        self.assertEqual((empty.journal.available, empty.journal.entries, empty.journal.reason), (True, 0, None))
        self.assertIsNone(self.observe(limit=10, fields=('activity',)).journal)
        self.endpoint.publish_journal(lv.LiveJournal(available=False, reason='journal-not-written'))
        unwritten = self.observe(limit=10, fields=('inquiries',))
        self.assertEqual((unwritten.journal.available, unwritten.journal.reason), (False, 'journal-not-written'))
        rejection = lv.InquiryJournalRejection(question_id='foreign', reason='journal-attempt-mismatch')
        self.endpoint.publish_journal(lv.LiveJournal(available=True, entries=1, rejections=(rejection,)))
        carried = self.observe(limit=10, fields=('inquiries',))
        self.assertEqual(carried.journal.entries, 1)
        self.assertEqual(carried.journal.rejections[0].question_id, 'foreign')
        self.assertEqual([entry.question_id for entry in carried.inquiries], [])

    def test_an_overflowing_composition_reports_explicit_facts_and_stays_decodable(self):
        entry = lv.InquiryState(question_id='q-big', status='answered', answer='\x01' * 4000, via='\x01' * 512, tool_call_id='\x01' * 512)
        events = [lv.LiveEvent(at='\x01' * 64, kind='\x01' * 80, tool_name='\x01' * 120) for _ in range(20)]
        observation = lv.LiveObservation(ready=True, recent_activity=tuple(events), session_id='\x01' * 512, error='\x01' * 512)
        rejections = tuple((lv.InquiryJournalRejection(question_id=f'foreign-{index:02d}', reason='journal-attempt-mismatch') for index in range(lv.MAX_INQUIRIES_PER_RUN)))
        journal = lv.LiveJournal(available=True, entries=0, rejections=rejections)
        composed = lv.LiveSnapshot(inquiries=(entry,), observation=observation, journal=journal)
        self.assertGreater(len(canonical_json(composed.to_payload()).encode()), lv.MAX_LIVE_FRAME_BYTES)
        self.endpoint.publish_inquiry_state(entry)
        self.endpoint.publish_observation(observation)
        self.endpoint.publish_journal(journal)
        everything = self.observe(limit=10)
        self.assertEqual(everything.unavailable, 'frame-too-large')
        self.assertTrue(everything.observed)
        self.assertEqual(everything.inquiries, ())
        self.assertTrue(everything.truncated)
        raw = self.endpoint.observe_local(limit=10)
        self.assertLessEqual(len(raw.encode()), lv.MAX_LIVE_FRAME_BYTES)
        self.assertIsNotNone(decode_snapshot(raw))
        inquiries_only = self.observe(limit=10, fields=('inquiries',))
        self.assertIsNone(inquiries_only.unavailable)
        self.assertFalse(inquiries_only.truncated)
        self.assertEqual(inquiries_only.inquiries[0].question_id, 'q-big')
        self.assertEqual(inquiries_only.inquiries[0].answer, '\x01' * 4000)
        self.assertEqual(inquiries_only.journal.entries, 0)
        self.assertEqual(len(inquiries_only.journal.rejections), lv.MAX_INQUIRIES_PER_RUN)
        point = self.observe(inquiry_id='q-big')
        self.assertEqual(point.inquiries[0].answer, '\x01' * 4000)
        observation_only = self.observe(limit=10, fields=('observation',))
        self.assertIsNone(observation_only.unavailable)
        self.assertEqual(len(observation_only.observation.recent_activity), 20)

    def test_a_changed_state_takes_a_fresh_seq_and_closed_stays_readable(self):
        self.endpoint.publish_inquiry_state(lv.InquiryState(question_id='q-1', status='queued'))
        first = self.observe(limit=10).inquiries[0]
        self.endpoint.publish_inquiry_state(lv.InquiryState(question_id='q-1', status='queued'))
        unchanged = self.observe(limit=10).inquiries[0]
        self.assertEqual(unchanged.seq, first.seq)
        self.endpoint.publish_inquiry_state(lv.InquiryState(question_id='q-1', status='answered', answer='yes'))
        changed = self.observe(limit=10).inquiries[0]
        self.assertGreater(changed.seq, first.seq)
        self.endpoint.close(reason='run-finished')
        snapshot = self.observe(limit=10)
        self.assertEqual(snapshot.unavailable, 'channel-closed')
        self.assertEqual([entry.question_id for entry in snapshot.inquiries], ['q-1'])

class RichReplayIntegrationTests(unittest.TestCase):

    def test_owner_delivery_replay_is_a_json_value_and_keeps_the_published_state(self):
        entry = lv.InquiryState(question_id='inquiry', status='delivered', delivery={'admittedDelivery': 'cooperative-checkpoint', 'startsNewTurn': False})
        reply = ctl._question_reply(entry, duplicate=True)
        decoded = lv.LiveReply.from_payload(decode_strict_json(canonical_json(reply.to_payload())))
        self.assertEqual(decoded.status, 'delivered')
        self.assertTrue(decoded.observed)
        self.assertEqual(decoded.native_correlation.value['delivery'], entry.delivery.value)
        self.assertNotIn('reason', decoded.native_correlation.value)
