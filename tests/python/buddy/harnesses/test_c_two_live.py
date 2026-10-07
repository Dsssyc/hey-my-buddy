"""The ADR-025 step 5-A C-Two live backend: envelope, endpoint, channel, lifecycle.

The first half drives the backend in-process with no C-Two server at all: the
strict wire frames, the per-component identity/token/instance binding, the
owner-settled admission with its bounded indexes and honest refusal facts,
the read-only snapshot paging over published facts (journal availability fact
included as the owner published it), the ownership cleanup primitive's
refusal rules, the settings-before-register ordering and the ready-material
publication. The second half drives real C-Two subprocess peers
(``fixtures/c_two_live_peer.py``): one random-person-name endpoint per run,
routing by the address read back after registration, the owner hand-off
consumed and settled over real transport, the clean unregister+shutdown that
makes this run's socket file disappear, the same name on two addresses, the
rejections of old tokens, instances and every identity component, wrong and
oversized frames, the closed channel, the stalling and SIGKILLed endpoints
that are reported unavailable and never stopped, and the holder's
confirmed-vanished cleanup of exactly this endpoint's abandoned or replaced
file. No harness, model or credential is involved.
"""
from __future__ import annotations

import importlib.util
import json
import os
import shutil
import signal
import socket as socket_module
import stat
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

import c_two as cc

from hey_my_buddy.buddy.harnesses import c_two_live as ctl
from hey_my_buddy.buddy.harnesses import live as lv
from hey_my_buddy.buddy.harnesses.run_contract import RunIdentity
from hey_my_buddy.errors import BoardError
from hey_my_buddy.json_codec import canonical_json, decode_strict_json
from hey_my_buddy.protocol import rpc_config

REPO_ROOT = Path(__file__).resolve().parents[4]
FIXTURE_PATH = Path(__file__).resolve().parent / "fixtures" / "c_two_live_peer.py"
PEER_MODULE_NAME = "c_two_live_peer"

#: The peer is loaded under one module name in every process so the test CRM
#: is the same decorated object everywhere it is used.
PEER_LAUNCHER = (
    "import importlib.util, sys;"
    "spec = importlib.util.spec_from_file_location(sys.argv[2], sys.argv[1]);"
    "module = importlib.util.module_from_spec(spec);"
    "spec.loader.exec_module(module);"
    "sys.exit(module.main())"
)

SANITIZED_VARIABLES = (
    "BUDDY_STATE_DIR", "BUDDY_RUNTIME_ROOT", "BUDDY_RUNTIME", "BUDDY_RUNTIME_IDENTITY",
    "BUDDY_WORKER_STATE", "BUDDY_WORKER_ID", "BUDDY_AGENT_CREDENTIAL",
    "BUDDY_AGENT_CREDENTIAL_FILE", "VIRTUAL_ENV", "UV_PROJECT_ENVIRONMENT",
)

IDENTITY_WIRE_NAMES = {"task_id": "taskId", "attempt_id": "attemptId",
                       "generation": "generation", "invocation_id": "invocationId",
                       "turn_id": "turnId", "input_sha256": "inputSha256"}


def load_test_crm() -> type:
    spec = importlib.util.spec_from_file_location(PEER_MODULE_NAME, FIXTURE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.TEST_CRM


TEST_CRM = load_test_crm()

QUEUED_REPLY = dict(status="queued", observed=True, state="queued")


def identity(**overrides) -> RunIdentity:
    values = dict(task_id="task-live", attempt_id="attempt-live", generation=1,
                  invocation_id="invocation-live", turn_id="turn-live", input_sha256=None)
    values.update(overrides)
    return RunIdentity(**values)


def inquiry_request(request_id: str = "request-1", question_id: str = "question-1",
                    question: str = "what should I check first?",
                    run: RunIdentity | None = None) -> lv.LiveRequest:
    return lv.LiveRequest(identity=run or identity(), request_id=request_id, kind="inquiry",
                          payload=lv.InquiryPayload(question_id=question_id, question=question))


def wire_request(request: lv.LiveRequest, *, token: str, instance_id: str,
                 timeout_ms: int = 1500) -> str:
    frame = ctl.LiveWireRequest(identity=request.identity, request_id=request.request_id,
                                kind=request.kind, payload=request.payload,
                                instance_id=instance_id, token=token, timeout_ms=timeout_ms)
    return canonical_json(frame.to_payload())


def wire_observe(run: RunIdentity, *, token: str, instance_id: str, **arguments) -> str:
    frame = ctl.LiveWireObserve(instance_id=instance_id, token=token, identity=run, **arguments)
    return canonical_json(frame.to_payload())


def endpoint(run: RunIdentity | None = None, *, delivery: str = "cooperative-checkpoint",
             **kwargs) -> ctl.CTwoLiveEndpoint:
    return ctl.CTwoLiveEndpoint(run or identity(), lv.LiveCapabilities(inquiry_delivery=delivery),
                                TEST_CRM, **kwargs)


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


class FakeC2:
    """A recording stand-in for the cc module the backend calls.

    Everything the backend reaches for beyond the recorded lifecycle calls —
    the concurrency types, for one — delegates to the real module.
    """

    def __init__(self, address: str | None = "ipc://cc" + "f" * 38):
        self.address = address
        self.registered: list[tuple] = []
        self.unregistered: list[str] = []
        self.shutdowns = 0
        self.roles_at_register: tuple | None = None

    def __getattr__(self, name):
        return getattr(cc, name)

    def register(self, contract, implementation, *, name, concurrency=None, **kwargs):
        self.roles_at_register = rpc_config.configured_roles()
        self.registered.append((contract, implementation, name, concurrency))

    def unregister(self, name):
        self.unregistered.append(name)

    def shutdown(self):
        self.shutdowns += 1

    def server_address(self):
        return self.address


class WireFrameTests(unittest.TestCase):
    def test_the_request_frame_is_the_request_plus_exactly_three_private_fields(self):
        self.assertEqual(set(ctl.LiveWireRequest.model_fields) - set(lv.LiveRequest.model_fields),
                         {"instance_id", "token", "timeout_ms"})
        frame = ctl.LiveWireRequest(identity=identity(), request_id="request-1", kind="inquiry",
                                    payload=lv.InquiryPayload(question_id="question-1",
                                                              question="hello?"),
                                    instance_id="a" * 64, token="b" * 64, timeout_ms=1500)
        payload = frame.to_payload()
        self.assertEqual(set(payload), {"identity", "requestId", "kind", "payload",
                                        "instanceId", "token", "timeoutMs"})
        self.assertEqual(ctl.LiveWireRequest.from_payload(
            decode_strict_json(canonical_json(payload))), frame)

    def test_the_observe_and_capabilities_frames_carry_only_envelope_and_selection(self):
        run = identity()
        observe = ctl.LiveWireObserve(instance_id="a" * 64, token="b" * 64, identity=run,
                                      after_seq=3, limit=10, fields=("activity", "inquiries"))
        self.assertEqual(set(observe.to_payload()),
                         {"instanceId", "token", "identity", "afterSeq", "limit", "fields",
                          "inquiryId"})
        query = ctl.LiveWireQuery(instance_id="a" * 64, token="b" * 64, identity=run)
        self.assertEqual(set(query.to_payload()), {"instanceId", "token", "identity"})
        # The frame models keep the shared strict base: extras and non-member
        # selections are refused, not folded in.
        for bad in ({"instanceId": "a" * 64, "token": "b" * 64, "identity": run.to_payload(),
                     "afterSeq": 0, "limit": 10, "extra": 1},
                    {"instanceId": "a" * 64, "token": "b" * 64, "identity": run.to_payload(),
                     "limit": 10, "fields": ["nope"]}):
            with self.assertRaises(BoardError):
                ctl.LiveWireObserve.from_payload(bad)

    def test_the_transport_window_bound_is_kept_on_the_wire(self):
        run = identity()
        for value in (99, 5001, True, 1500.0):
            with self.assertRaises(BoardError):
                ctl.LiveWireRequest(identity=run, request_id="r", kind="inquiry",
                                    payload=lv.InquiryPayload(question_id="q", question="x"),
                                    instance_id="a" * 64, token="b" * 64, timeout_ms=value)


class EndpointAdmissionTests(unittest.TestCase):
    """The request handler over the wire, without any C-Two server."""

    def setUp(self):
        self.endpoint = endpoint(instance_id="c" * 64, token="d" * 64)
        self.run = self.endpoint.identity

    def raw_ask(self, request: lv.LiveRequest, *, timeout_ms: int = 3000,
                token: str = "d" * 64, instance_id: str = "c" * 64) -> str:
        return self.endpoint.request(wire_request(request, token=token, instance_id=instance_id,
                                                  timeout_ms=timeout_ms))

    def ask(self, request: lv.LiveRequest, **kwargs) -> lv.LiveReply:
        return decode_reply(self.raw_ask(request, **kwargs))

    def wait_pending(self, request_id: str, *, timeout: float = 3.0) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with self.endpoint._lock:
                if request_id in self.endpoint._pending:
                    return
            time.sleep(0.005)
        raise AssertionError("the request never became pending")

    def deliver(self, request: lv.LiveRequest, reply: lv.LiveReply | None = None,
                *, timeout_ms: int = 3000) -> lv.LiveReply:
        """One ask driven through the real hand-off: enqueue, consume, settle."""
        box: list[str] = []
        thread = threading.Thread(target=lambda: box.append(
            self.raw_ask(request, timeout_ms=timeout_ms)), daemon=True)
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
        return decode_snapshot(self.endpoint.observe(wire_observe(
            self.run, token="d" * 64, instance_id="c" * 64, limit=10))).inquiries

    def test_a_request_waits_for_the_owners_settlement_and_commits_once(self):
        box: list[str] = []
        thread = threading.Thread(target=lambda: box.append(
            self.raw_ask(inquiry_request())), daemon=True)
        thread.start()
        self.wait_pending("request-1")
        # Before the owner's commit there is no inquiry state at all: the
        # buffer insertion is not a fact.
        self.assertEqual(self.observe_entries(), ())
        consumed = self.endpoint.consume_request(1.0)
        self.assertEqual((consumed.request_id, consumed.payload.question_id),
                         ("request-1", "question-1"))
        self.assertIsNone(self.endpoint.consume_request(0.0))
        self.endpoint.settle_request("request-1", lv.LiveReply(**QUEUED_REPLY))
        thread.join(timeout=5.0)
        self.assertFalse(thread.is_alive())
        reply = decode_reply(box[0])
        self.assertEqual((reply.status, reply.observed, reply.state), ("queued", True, "queued"))
        self.assertEqual(reply.native_correlation.value,
                         {"questionId": "question-1", "duplicate": False})
        self.assertEqual([(entry.question_id, entry.status) for entry in self.observe_entries()],
                         [("question-1", "queued")])
        # The committed replay answers from the state without a second delivery.
        self.assertEqual(self.ask(inquiry_request()).native_correlation.value["duplicate"], True)
        self.assertIsNone(self.endpoint.consume_request(0.0))
        self.assertEqual(self.endpoint._pending, {})

    def test_an_owner_refusal_keeps_the_request_retryable(self):
        refused = self.deliver(inquiry_request(), lv.LiveReply(
            status="unavailable", reason_code="journal-unavailable", error_code="not-ready"))
        self.assertEqual((refused.status, refused.reason_code, refused.error_code),
                         ("unavailable", "journal-unavailable", "not-ready"))
        # Nothing was committed: no state, no index binding, no residue.
        self.assertEqual(self.observe_entries(), ())
        self.assertEqual((self.endpoint._admitted, self.endpoint._requests,
                          self.endpoint._pending), ({}, {}, {}))
        retried = self.deliver(inquiry_request())
        self.assertEqual((retried.status, retried.state), ("queued", "queued"))
        self.assertEqual([(entry.question_id, entry.status) for entry in self.observe_entries()],
                         [("question-1", "queued")])

    def test_a_window_expiry_drops_the_unconsumed_request(self):
        expired = self.ask(inquiry_request(), timeout_ms=150)
        self.assertEqual((expired.status, expired.reason_code),
                         ("unavailable", "request-window-expired"))
        # The owner never sees it and nothing was committed.
        self.assertIsNone(self.endpoint.consume_request(0.1))
        self.assertEqual(self.endpoint._pending, {})
        self.assertEqual(self.observe_entries(), ())
        # The identical retry goes through the whole hand-off again.
        retried = self.deliver(inquiry_request())
        self.assertEqual(retried.status, "queued")

    def test_close_wakes_waiting_requests_without_delivery(self):
        box: list[str] = []
        thread = threading.Thread(target=lambda: box.append(
            self.raw_ask(inquiry_request(), timeout_ms=5000)), daemon=True)
        thread.start()
        self.wait_pending("request-1")
        self.endpoint.close(reason="run-finished")
        thread.join(timeout=5.0)
        self.assertFalse(thread.is_alive())
        self.assertEqual((decode_reply(box[0]).status, decode_reply(box[0]).reason_code),
                         ("unavailable", "channel-closed"))
        self.assertIsNone(self.endpoint.consume_request(0.0))
        self.assertEqual(self.endpoint._pending, {})
        self.assertEqual(self.observe_entries(), ())

    def test_a_concurrent_duplicate_joins_the_in_flight_delivery(self):
        boxes: list[list[str]] = ([], [])
        threads = [threading.Thread(target=lambda index=index: boxes[index].append(
            self.raw_ask(inquiry_request(request_id=f"request-{index + 1}"), timeout_ms=5000)),
            daemon=True) for index in range(2)]
        for thread in threads:
            thread.start()
        self.wait_pending("request-1")
        time.sleep(0.2)
        # One slot, one queue entry: the second request id joined the same
        # delivery instead of enqueueing its own, as a budgeted alias of it.
        self.assertEqual(sorted(self.endpoint._pending), ["request-1", "request-2"])
        self.assertIs(self.endpoint._pending["request-1"], self.endpoint._pending["request-2"])
        self.assertEqual(self.endpoint._queue.qsize(), 1)
        consumed = self.endpoint.consume_request(1.0)
        self.assertEqual(consumed.request_id, "request-1")
        self.endpoint.settle_request("request-1", lv.LiveReply(**QUEUED_REPLY))
        for thread in threads:
            thread.join(timeout=5.0)
            self.assertFalse(thread.is_alive())
        for box in boxes:
            self.assertEqual(decode_reply(box[0]).status, "queued")
        # The settled binding covers both joined ids, so each keeps the
        # whole-payload conflict gate.
        self.assertEqual(self.endpoint._requests,
                         {"request-1": "question-1", "request-2": "question-1"})
        refused = self.ask(inquiry_request(request_id="request-2",
                                           question_id="question-2"))
        self.assertEqual((refused.status, refused.reason_code),
                         ("unavailable", "request-payload-conflict"))
        self.assertIsNone(self.endpoint.consume_request(0.0))
        self.assertEqual([(entry.question_id, entry.seq) for entry in self.observe_entries()],
                         [("question-1", 1)])

    def test_a_closed_or_dead_entry_is_never_handed_to_the_owner(self):
        # A closed channel leaves its queued entry dead: the owner's drain
        # drops it instead of delivering late what the caller already learned
        # was closed.
        box: list[str] = []
        thread = threading.Thread(target=lambda: box.append(
            self.raw_ask(inquiry_request(), timeout_ms=5000)), daemon=True)
        thread.start()
        self.wait_pending("request-1")
        self.endpoint.close(reason="run-finished")
        thread.join(timeout=5.0)
        self.assertFalse(thread.is_alive())
        self.assertEqual(decode_reply(box[0]).reason_code, "channel-closed")
        self.assertIsNone(self.endpoint.consume_request(0.0))
        # A settled slot whose entry was never consumed — an owner wiring
        # fault — is equally dead: the answer already exists, so the queued
        # copy must not trigger a second native delivery. A fresh endpoint,
        # since the closed one refuses everything.
        fresh = endpoint(instance_id="c" * 64, token="d" * 64)
        box2: list[str] = []
        thread2 = threading.Thread(target=lambda: box2.append(
            fresh.request(wire_request(
                inquiry_request(request_id="request-2", question_id="question-2"),
                token="d" * 64, instance_id="c" * 64, timeout_ms=5000))), daemon=True)
        thread2.start()
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline and "request-2" not in fresh._pending:
            time.sleep(0.005)
        self.assertIn("request-2", fresh._pending)
        fresh.settle_request("request-2", lv.LiveReply(**QUEUED_REPLY))
        thread2.join(timeout=5.0)
        self.assertEqual(decode_reply(box2[0]).status, "queued")
        self.assertIsNone(fresh.consume_request(0.0))
        self.assertEqual(fresh._pending, {})

    def test_zero_timeout_consumes_without_waiting(self):
        boxes: list[list[str]] = []
        waiting: list[threading.Thread] = []
        for index in range(3):
            box: list[str] = []
            boxes.append(box)
            thread = threading.Thread(target=lambda box=box, index=index: box.append(
                self.raw_ask(inquiry_request(request_id=f"r-{index}",
                                             question_id=f"q-{index}"),
                             timeout_ms=5000)), daemon=True)
            waiting.append(thread)
            thread.start()
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline and len(self.endpoint._pending) < 3:
            time.sleep(0.005)
        self.assertEqual(len(self.endpoint._pending), 3)
        # Zero waits drain what is queued, one entry per call, without
        # blocking — the original non-blocking poll the owner's native loop
        # needs.
        drained = []
        for index in range(3):
            started = time.monotonic()
            consumed = self.endpoint.consume_request(0.0)
            self.assertLess(time.monotonic() - started, 0.5)
            self.assertIsNotNone(consumed)
            drained.append(consumed.request_id)
        self.assertEqual(sorted(drained), ["r-0", "r-1", "r-2"])
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
            self.assertEqual(decode_reply(box[0]).status, "queued")

    def test_a_join_past_the_request_budget_is_refused(self):
        self.deliver(inquiry_request())
        # Thirty committed request ids leave room for exactly one more: the
        # in-flight join, whose own second id then crosses the budget.
        for index in range(2, ctl.MAX_PENDING_LIVE_REQUESTS):
            self.ask(inquiry_request(request_id=f"alias-{index}"))
        self.assertEqual(len(self.endpoint._requests), ctl.MAX_PENDING_LIVE_REQUESTS - 1)
        # A fresh question still has an in-flight slot of its own.
        box: list[str] = []
        thread = threading.Thread(target=lambda: box.append(
            self.raw_ask(inquiry_request(request_id="join-1", question_id="question-9"),
                         timeout_ms=5000)), daemon=True)
        thread.start()
        self.wait_pending("join-1")
        # A second request id for that in-flight question cannot join past
        # the exhausted request budget.
        refused = self.ask(inquiry_request(request_id="join-2", question_id="question-9"),
                           timeout_ms=1500)
        self.assertEqual((refused.status, refused.reason_code),
                         ("unavailable", "request-limit"))
        self.assertEqual(sorted(self.endpoint._pending), ["join-1"])
        self.endpoint.settle_request("join-1", lv.LiveReply(**QUEUED_REPLY))
        thread.join(timeout=5.0)
        self.assertFalse(thread.is_alive())
        self.assertEqual(decode_reply(box[0]).status, "queued")

    def test_settle_keeps_the_owners_published_state(self):
        # The journal callback's natural order: the owner publishes the
        # question's source facts first, the settlement answers afterwards.
        delivery = {"requestedDelivery": None, "admittedDelivery": "cooperative-checkpoint",
                    "startsNewTurn": False, "extendsDeadline": False, "supported": True}
        box: list[str] = []
        thread = threading.Thread(target=lambda: box.append(
            self.raw_ask(inquiry_request())), daemon=True)
        thread.start()
        self.wait_pending("request-1")
        self.endpoint.consume_request(1.0)
        self.endpoint.publish_inquiry_state(lv.InquiryState(
            question_id="question-1", status="queued", delivery=delivery,
            limitation="only at the session's cooperative checkpoint"))
        self.endpoint.settle_request("request-1", lv.LiveReply(**QUEUED_REPLY))
        thread.join(timeout=5.0)
        self.assertEqual(decode_reply(box[0]).status, "queued")
        entry = self.observe_entries()[0]
        self.assertEqual((entry.status, entry.delivery.value, entry.limitation),
                         ("queued", delivery,
                          "only at the session's cooperative checkpoint"))
        seq_before = entry.seq
        # A settle arriving after the owner already published the answer never
        # downgrades it or bumps the sequence.
        self.endpoint.settle_request("request-1", lv.LiveReply(**QUEUED_REPLY))
        again = self.observe_entries()[0]
        self.assertEqual((again.status, again.seq, again.delivery.value, again.limitation),
                         ("queued", seq_before, delivery,
                          "only at the session's cooperative checkpoint"))
        self.endpoint.publish_inquiry_state(lv.InquiryState(
            question_id="question-1", status="answered", answer="yes", delivery=delivery))
        self.endpoint.settle_request("request-1", lv.LiveReply(**QUEUED_REPLY))
        answered = self.observe_entries()[0]
        self.assertEqual((answered.status, answered.answer, answered.delivery.value),
                         ("answered", "yes", delivery))
        # A status-only settlement — ``state`` absent — projects the actual
        # status instead of defaulting the first state to queued.
        self.deliver(inquiry_request(request_id="request-3", question_id="question-3"),
                     lv.LiveReply(status="answered", observed=True))
        projected = [entry for entry in self.observe_entries()
                     if entry.question_id == "question-3"][0]
        self.assertEqual(projected.status, "answered")

    def test_one_accepted_question_is_queued_once_and_observed(self):
        reply = self.deliver(inquiry_request())
        self.assertEqual((reply.status, reply.observed, reply.state), ("queued", True, "queued"))
        self.assertEqual(reply.native_correlation.value,
                         {"questionId": "question-1", "duplicate": False})
        self.assertIsNone(self.endpoint.consume_request(0.0))
        self.assertEqual(self.endpoint._pending, {})

    def test_replays_and_duplicates_never_deliver_twice(self):
        self.deliver(inquiry_request())
        # The same request again replays the question's current fact.
        self.assertEqual(self.ask(inquiry_request()).native_correlation.value["duplicate"], True)
        # A different requestId for the same question and payload is a duplicate too.
        self.assertEqual(self.ask(inquiry_request(request_id="request-2")).native_correlation.value,
                         {"questionId": "question-1", "duplicate": True})
        # Exactly one delivery ever reached the owner loop.
        self.assertIsNone(self.endpoint.consume_request(0.0))
        self.assertEqual(self.endpoint._queue.qsize(), 0)
        # The published state advances only through the owner's own facts.
        self.endpoint.publish_inquiry_state(lv.InquiryState(question_id="question-1",
                                                            status="delivered"))
        self.assertEqual(self.ask(inquiry_request(request_id="request-3")).state, "delivered")

    def test_a_committed_request_id_binds_one_queue_entry(self):
        self.deliver(inquiry_request())
        self.assertEqual((self.endpoint._admitted, self.endpoint._requests),
                         ({"question-1": self.endpoint._admitted["question-1"]},
                          {"request-1": "question-1"}))
        # The identical replay does not enqueue again.
        self.ask(inquiry_request())
        # A committed requestId with a different question id is a conflict over
        # the whole payload, before anything is enqueued.
        refused = self.ask(inquiry_request(question_id="question-2"))
        self.assertEqual((refused.status, refused.reason_code),
                         ("unavailable", "request-payload-conflict"))
        # The refused question id was never admitted, so its first honest ask
        # under a fresh request id is still admissible.
        accepted = self.deliver(inquiry_request(request_id="request-4", question_id="question-2"))
        self.assertEqual(accepted.status, "queued")
        # A different request id over the same committed question and payload is
        # a duplicate, and a different payload under the question its conflict.
        self.assertEqual(self.ask(inquiry_request(request_id="request-5")).native_correlation.value,
                         {"questionId": "question-1", "duplicate": True})
        refused = self.ask(inquiry_request(request_id="request-6",
                                           question="a different question"))
        self.assertEqual((refused.status, refused.reason_code),
                         ("unavailable", "question-payload-conflict"))
        # Later, the first committed request id with its own payload changed is
        # still its own conflict.
        refused = self.ask(inquiry_request(question="another different question"))
        self.assertEqual((refused.status, refused.reason_code),
                         ("unavailable", "request-payload-conflict"))
        self.assertEqual(self.endpoint._queue.qsize(), 0)
        self.assertEqual(self.endpoint._pending, {})
        self.assertEqual(sorted(self.endpoint._requests),
                         ["request-1", "request-4", "request-5"])

    def test_the_request_id_budget_bounds_aliases_explicitly(self):
        self.deliver(inquiry_request())
        for index in range(2, ctl.MAX_PENDING_LIVE_REQUESTS + 1):
            self.assertEqual(
                self.ask(inquiry_request(request_id=f"alias-{index}")).native_correlation.value,
                {"questionId": "question-1", "duplicate": True}, f"alias-{index}")
        self.assertEqual(len(self.endpoint._requests), ctl.MAX_PENDING_LIVE_REQUESTS)
        # Past the per-run request budget a NEW request id — even for a known
        # question and payload — is an explicit refusal, never a quiet eviction.
        refused = self.ask(inquiry_request(request_id="alias-late"))
        self.assertEqual((refused.status, refused.reason_code),
                         ("unavailable", "request-limit"))
        self.assertEqual(len(self.endpoint._requests), ctl.MAX_PENDING_LIVE_REQUESTS)
        # Known ids stay idempotent and conflicting exactly as before.
        self.assertEqual(self.ask(inquiry_request()).native_correlation.value["duplicate"], True)
        refused = self.ask(inquiry_request(question="changed under a known id"))
        self.assertEqual(refused.reason_code, "request-payload-conflict")
        self.assertIsNone(self.endpoint.consume_request(0.0))

    def test_changed_payloads_are_conflicts(self):
        self.deliver(inquiry_request())
        refused = self.ask(inquiry_request(question="a different question"))
        self.assertEqual((refused.status, refused.reason_code),
                         ("unavailable", "request-payload-conflict"))
        refused = self.ask(inquiry_request(request_id="request-9",
                                           question="a different question"))
        self.assertEqual((refused.status, refused.reason_code),
                         ("unavailable", "question-payload-conflict"))

    def test_every_identity_component_token_and_instance_binds_on_its_own(self):
        cases = [
            (identity(task_id="task-other"), "identity-mismatch:taskId"),
            (identity(attempt_id="attempt-other"), "identity-mismatch:attemptId"),
            (identity(generation=2), "identity-mismatch:generation"),
            (identity(invocation_id="invocation-other"), "identity-mismatch:invocationId"),
            (identity(turn_id="turn-other"), "identity-mismatch:turnId"),
            (identity(turn_id=None), "identity-mismatch:turnId"),
            (identity(input_sha256="ab" * 32), "identity-mismatch:inputSha256"),
        ]
        for foreign, reason in cases:
            reply = self.ask(inquiry_request(run=foreign))
            self.assertEqual((reply.status, reply.observed, reply.reason_code),
                             ("unavailable", False, reason), reason)
        reply = self.ask(inquiry_request(), token="e" * 64)
        self.assertEqual((reply.status, reply.reason_code), ("unavailable", "token-mismatch"))
        reply = self.ask(inquiry_request(), instance_id="f" * 64)
        self.assertEqual((reply.status, reply.reason_code), ("unavailable", "instance-mismatch"))

    def test_wrong_and_oversized_frames_are_refused_not_raised(self):
        for raw in ("not json at all", "{}", "[]",
                    canonical_json({"instanceId": "c" * 64, "token": "d" * 64})):
            reply = decode_reply(self.endpoint.request(raw))
            self.assertEqual((reply.status, reply.reason_code), ("unavailable", "frame-invalid"))
        oversized = canonical_json({"instanceId": "c" * 64, "token": "d" * 64,
                                    "identity": identity().to_payload(), "requestId": "r",
                                    "kind": "inquiry",
                                    "payload": {"questionId": "q", "question": "x" * 70000},
                                    "timeoutMs": 1500})
        self.assertGreater(len(oversized.encode()), lv.MAX_LIVE_FRAME_BYTES)
        reply = decode_reply(self.endpoint.request(oversized))
        self.assertEqual((reply.status, reply.reason_code), ("unavailable", "frame-too-large"))

    def test_closed_unsupported_limit_window_and_queue_full_are_explicit_facts(self):
        self.endpoint.close(reason="run-finished")
        reply = self.ask(inquiry_request())
        self.assertEqual((reply.status, reply.reason_code), ("unavailable", "channel-closed"))

        fresh = endpoint(delivery="unsupported", instance_id="c" * 64, token="d" * 64)
        reply = decode_reply(fresh.request(wire_request(inquiry_request(), token="d" * 64,
                                                        instance_id="c" * 64)))
        self.assertEqual((reply.status, reply.reason_code),
                         ("unsupported", "inquiry-unsupported"))

        limited = endpoint(instance_id="c" * 64, token="d" * 64)
        boxes: list[list[str]] = []
        waiting: list[threading.Thread] = []
        for index in range(lv.MAX_INQUIRIES_PER_RUN):
            box: list[str] = []
            boxes.append(box)
            thread = threading.Thread(target=lambda box=box, index=index: box.append(
                limited.request(wire_request(
                    inquiry_request(request_id=f"r-{index}", question_id=f"q-{index}"),
                    token="d" * 64, instance_id="c" * 64, timeout_ms=5000))), daemon=True)
            waiting.append(thread)
            thread.start()
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline and len(limited._pending) < lv.MAX_INQUIRIES_PER_RUN:
            time.sleep(0.005)
        self.assertEqual(len(limited._pending), lv.MAX_INQUIRIES_PER_RUN)
        refused = decode_reply(limited.request(wire_request(
            inquiry_request(request_id="r-late", question_id="q-late"),
            token="d" * 64, instance_id="c" * 64)))
        self.assertEqual((refused.status, refused.reason_code), ("unavailable", "inquiry-limit"))
        # One settled commit does not reopen the budget for a new question.
        settled = limited.consume_request(1.0)
        limited.settle_request(settled.request_id, lv.LiveReply(**QUEUED_REPLY))
        refused = decode_reply(limited.request(wire_request(
            inquiry_request(request_id="r-late", question_id="q-late"),
            token="d" * 64, instance_id="c" * 64)))
        self.assertEqual(refused.reason_code, "inquiry-limit")
        limited.close(reason="run-finished")
        for thread in waiting:
            thread.join(timeout=5.0)
            self.assertFalse(thread.is_alive())
        outcomes = sorted("queued" if decode_reply(box[0]).reason_code is None
                          else decode_reply(box[0]).reason_code for box in boxes)
        # One request was settled by its owner; the other thirty-one were
        # woken by the close without any delivery.
        self.assertEqual(outcomes, ["channel-closed"] * (len(boxes) - 1) + ["queued"])
        self.assertEqual(limited._pending, {})

        windowed = endpoint(instance_id="c" * 64, token="d" * 64)
        real_time = ctl.time
        ctl.time = FakeClock(step=0.5)
        try:
            refused = decode_reply(windowed.request(wire_request(
                inquiry_request(), token="d" * 64, instance_id="c" * 64, timeout_ms=100)))
        finally:
            ctl.time = real_time
        self.assertEqual((refused.status, refused.reason_code),
                         ("unavailable", "request-window-expired"))

        stalled = endpoint(instance_id="c" * 64, token="d" * 64)
        for _ in range(ctl.MAX_PENDING_LIVE_REQUESTS):
            stalled._queue.put_nowait((inquiry_request(), None))
        refused = decode_reply(stalled.request(wire_request(
            inquiry_request(), token="d" * 64, instance_id="c" * 64)))
        self.assertEqual((refused.status, refused.reason_code), ("unavailable", "queue-full"))

    def test_the_queue_never_carries_the_token(self):
        box: list[str] = []
        thread = threading.Thread(target=lambda: box.append(
            self.raw_ask(inquiry_request())), daemon=True)
        thread.start()
        self.wait_pending("request-1")
        consumed = self.endpoint.consume_request(1.0)
        self.endpoint.settle_request("request-1", lv.LiveReply(**QUEUED_REPLY))
        thread.join(timeout=5.0)
        self.assertNotIn("d" * 64, canonical_json(consumed.to_payload()))
        self.assertNotIsInstance(consumed, ctl.LiveWireRequest)

    def test_no_refusal_or_fact_mentions_the_token(self):
        produced = [self.endpoint.request(raw) for raw in ("not json", "{}")]
        settled = self.deliver(inquiry_request())
        produced.append(canonical_json(settled.to_payload()))
        produced.append(self.endpoint.request(
            wire_request(inquiry_request(), token="e" * 64, instance_id="c" * 64)))
        produced.append(self.endpoint.observe(wire_observe(
            self.run, token="d" * 64, instance_id="c" * 64, after_seq=0, limit=4)))
        self.endpoint.publish_inquiry_state(lv.InquiryState(question_id="question-1",
                                                            status="answered", answer="done"))
        produced.append(self.endpoint.observe(wire_observe(
            self.run, token="d" * 64, instance_id="c" * 64, after_seq=0, limit=4)))
        for text in produced:
            self.assertNotIn("d" * 64, text)


class EndpointObservationTests(unittest.TestCase):
    def setUp(self):
        self.endpoint = endpoint(instance_id="c" * 64, token="d" * 64)
        self.run = self.endpoint.identity

    def observe(self, **arguments) -> lv.LiveSnapshot:
        if "inquiry_id" not in arguments and "limit" not in arguments:
            arguments.setdefault("limit", 10)
        return decode_snapshot(self.endpoint.observe(wire_observe(
            self.run, token="d" * 64, instance_id="c" * 64, **arguments)))

    def test_snapshot_pages_the_full_32x4000_fact_set_within_one_frame(self):
        answer = "a" * 4000
        for index in range(lv.MAX_INQUIRIES_PER_RUN):
            self.endpoint.publish_inquiry_state(lv.InquiryState(
                question_id=f"q-{index:02d}", status="answered", answer=answer))
        collected: dict[str, lv.InquiryState] = {}
        after = None
        pages = 0
        while True:
            snapshot = self.observe(after_seq=after, limit=lv.MAX_OBSERVE_LIMIT)
            pages += 1
            for entry in snapshot.inquiries:
                collected.setdefault(entry.question_id, entry)
            self.assertLessEqual(len(canonical_json(snapshot.to_payload()).encode()),
                                 lv.MAX_LIVE_FRAME_BYTES)
            if not snapshot.truncated:
                break
            after = snapshot.inquiries[-1].seq
        self.assertEqual(len(collected), lv.MAX_INQUIRIES_PER_RUN)
        self.assertTrue(all(entry.answer == answer for entry in collected.values()))
        self.assertGreater(pages, 1)

    def test_only_newer_activity_is_kept_and_served(self):
        self.assertTrue(self.endpoint.publish_activity({"phase": "starting", "eventSeq": 1}))
        self.assertFalse(self.endpoint.publish_activity({"phase": "starting", "eventSeq": 1}))
        self.assertFalse(self.endpoint.publish_activity({"phase": "starting", "eventSeq": 0}))
        self.assertTrue(self.endpoint.publish_activity({"phase": "streaming-model",
                                                        "eventSeq": 2}))
        snapshot = self.observe(fields=("activity",))
        self.assertEqual(snapshot.activity.value, {"phase": "streaming-model", "eventSeq": 2})
        with self.assertRaises(BoardError):
            self.endpoint.publish_activity({"phase": "not-a-phase"})
        self.assertEqual(self.observe(fields=("activity",)).activity.value,
                         {"phase": "streaming-model", "eventSeq": 2})

    def test_selection_point_query_and_observation_facts(self):
        self.endpoint.publish_activity({"phase": "tool-running", "eventSeq": 4})
        self.endpoint.publish_observation(lv.LiveObservation(ready=True, agent_status="waiting"))
        self.endpoint.publish_inquiry_state(lv.InquiryState(question_id="q-1", status="answered",
                                                            answer="yes"))
        everything = self.observe(limit=10)
        self.assertEqual(everything.activity.value["phase"], "tool-running")
        self.assertEqual(everything.observation.agent_status, "waiting")
        self.assertEqual([entry.question_id for entry in everything.inquiries], ["q-1"])
        self.assertTrue(everything.observed)
        only_activity = self.observe(limit=10, fields=("activity",))
        self.assertIsNone(only_activity.observation)
        self.assertEqual(only_activity.inquiries, ())
        self.assertIsNone(only_activity.observed)
        point = self.observe(inquiry_id="q-1")
        self.assertEqual([entry.question_id for entry in point.inquiries], ["q-1"])
        self.assertEqual(point.inquiries[0].answer, "yes")
        missing = self.observe(inquiry_id="q-nope")
        self.assertEqual(missing.inquiries, ())
        self.assertTrue(missing.observed)

    def test_observation_read_facts_match_the_existing_three(self):
        # No source at all: the existing channel's own fact, never an
        # inferred success from the endpoint's own buffer.
        no_source = self.observe(limit=10)
        self.assertEqual((no_source.observed, no_source.reason, no_source.error,
                          no_source.observation),
                         (False, "observation-unavailable", None, None))
        # A successful read: the published value establishes observed=True.
        self.endpoint.publish_observation(lv.LiveObservation(ready=True,
                                                             agent_status="streaming"))
        success = self.observe(limit=10)
        self.assertEqual((success.observed, success.reason, success.error),
                         (True, None, None))
        self.assertEqual(success.observation.agent_status, "streaming")
        # A real failed read published by the owner: the transport
        # classification and the peer's specific code ride verbatim, the
        # last activity stays, and no look-alike observation is served.
        self.endpoint.publish_activity({"phase": "tool-running", "eventSeq": 3})
        self.endpoint.publish_snapshot(lv.LiveSnapshot(
            observed=False, reason="bridge-write-failed", error="peer-timeout"))
        failed = self.observe(limit=10)
        self.assertEqual((failed.observed, failed.reason, failed.error,
                          failed.observation),
                         (False, "bridge-write-failed", "peer-timeout", None))
        self.assertEqual(failed.activity.value["phase"], "tool-running")
        # A snapshot publish also adopts a recovered read and the journal
        # fact beside it, while the selection still gates what is carried.
        self.endpoint.publish_snapshot(lv.LiveSnapshot(
            observation=lv.LiveObservation(ready=True, agent_status="waiting"),
            journal=lv.LiveJournal(available=True, entries=2)))
        recovered = self.observe(limit=10)
        self.assertEqual((recovered.observed, recovered.reason),
                         (True, None))
        self.assertEqual(recovered.observation.agent_status, "waiting")
        self.assertEqual((recovered.journal.available, recovered.journal.entries), (True, 2))
        untouched = self.observe(limit=10, fields=("activity",))
        self.assertIsNone(untouched.observed)
        self.assertIsNone(untouched.reason)
        self.assertIsNone(untouched.observation)
        self.assertIsNone(untouched.journal)

    def test_the_journal_fact_is_what_the_owner_published(self):
        # An endpoint whose owner published nothing carries no journal fact —
        # absence is never folded into an invented availability or a zero.
        self.assertIsNone(self.observe(limit=10, fields=("inquiries",)).journal)
        self.endpoint.publish_journal(lv.LiveJournal(available=True, entries=0))
        empty = self.observe(limit=10, fields=("inquiries",))
        self.assertEqual((empty.journal.available, empty.journal.entries, empty.journal.reason),
                         (True, 0, None))
        # The selection gates the source exactly as it gates the others.
        self.assertIsNone(self.observe(limit=10, fields=("activity",)).journal)
        self.endpoint.publish_journal(lv.LiveJournal(available=False,
                                                    reason="journal-not-written"))
        unwritten = self.observe(limit=10, fields=("inquiries",))
        self.assertEqual((unwritten.journal.available, unwritten.journal.reason),
                         (False, "journal-not-written"))
        rejection = lv.InquiryJournalRejection(
            question_id="foreign",
            reason="the journal record belongs to another attempt")
        self.endpoint.publish_journal(lv.LiveJournal(available=True, entries=1,
                                                    rejections=(rejection,)))
        carried = self.observe(limit=10, fields=("inquiries",))
        self.assertEqual(carried.journal.entries, 1)
        self.assertEqual(carried.journal.rejections[0].question_id, "foreign")
        # The journal fact never bleeds into the inquiry states and refuses a
        # value its own model will not carry.
        self.assertEqual([entry.question_id for entry in carried.inquiries], [])
        with self.assertRaises(BoardError):
            self.endpoint.publish_journal({"available": "yes"})

    def test_an_overflowing_composition_reports_explicit_facts_and_stays_decodable(self):
        # Every value is legal inside its own field bound: control-heavy text
        # the canonical codec escapes six-fold in the answer, the same in the
        # entry's other text fields, a full twenty-event recent-activity
        # observation and a rejection-bearing journal — together more than one
        # 64 KiB frame can carry.
        entry = lv.InquiryState(question_id="q-big", status="answered",
                                answer="\x01" * 4000, limitation="\x01" * 2048,
                                reason="\x01" * 400, via="\x01" * 512,
                                tool_call_id="\x01" * 512)
        events = [lv.LiveEvent(at="\x01" * 64, kind="\x01" * 80, tool_name="\x01" * 120)
                  for _ in range(20)]
        observation = lv.LiveObservation(ready=True, recent_activity=tuple(events))
        rejections = tuple(lv.InquiryJournalRejection(
            question_id=f"foreign-{index:02d}",
            reason="\x01" * 40) for index in range(lv.MAX_INQUIRIES_PER_RUN))
        journal = lv.LiveJournal(available=True, entries=0, rejections=rejections)
        composed = lv.LiveSnapshot(inquiries=(entry,), observation=observation, journal=journal)
        self.assertGreater(len(canonical_json(composed.to_payload()).encode()),
                           lv.MAX_LIVE_FRAME_BYTES)
        self.endpoint.publish_inquiry_state(entry)
        self.endpoint.publish_observation(observation)
        self.endpoint.publish_journal(journal)
        # The whole-selection read refuses to trim: the page reports the frame
        # bound it could not meet, carries nothing it could not carry whole,
        # and the reply itself decodes inside the bound.
        everything = self.observe(limit=10)
        self.assertEqual(everything.unavailable, "frame-too-large")
        self.assertTrue(everything.observed)  # the observation was selected and read
        self.assertEqual(everything.inquiries, ())
        self.assertTrue(everything.truncated)
        raw = self.endpoint.observe(wire_observe(
            self.run, token="d" * 64, instance_id="c" * 64, limit=10))
        self.assertLessEqual(len(raw.encode()), lv.MAX_LIVE_FRAME_BYTES)
        self.assertIsNotNone(decode_snapshot(raw))
        # Narrowing the selection reaches every fact whole: the big entry and
        # the journal page together, the entry point-queries alone, and the
        # observation is served beside nothing that could push it over.
        inquiries_only = self.observe(limit=10, fields=("inquiries",))
        self.assertIsNone(inquiries_only.unavailable)
        self.assertFalse(inquiries_only.truncated)
        self.assertEqual(inquiries_only.inquiries[0].question_id, "q-big")
        self.assertEqual(inquiries_only.inquiries[0].answer, "\x01" * 4000)
        self.assertEqual(inquiries_only.journal.entries, 0)
        self.assertEqual(len(inquiries_only.journal.rejections), lv.MAX_INQUIRIES_PER_RUN)
        point = self.observe(inquiry_id="q-big")
        self.assertEqual(point.inquiries[0].answer, "\x01" * 4000)
        observation_only = self.observe(limit=10, fields=("observation",))
        self.assertIsNone(observation_only.unavailable)
        self.assertEqual(len(observation_only.observation.recent_activity), 20)

    def test_a_changed_state_takes_a_fresh_seq_and_closed_stays_readable(self):
        self.endpoint.publish_inquiry_state(lv.InquiryState(question_id="q-1", status="queued"))
        first = self.observe(limit=10).inquiries[0]
        self.endpoint.publish_inquiry_state(lv.InquiryState(question_id="q-1", status="queued"))
        unchanged = self.observe(limit=10).inquiries[0]
        self.assertEqual(unchanged.seq, first.seq)
        self.endpoint.publish_inquiry_state(lv.InquiryState(question_id="q-1", status="answered",
                                                            answer="yes"))
        changed = self.observe(limit=10).inquiries[0]
        self.assertGreater(changed.seq, first.seq)
        self.endpoint.close(reason="run-finished")
        snapshot = self.observe(limit=10)
        self.assertEqual(snapshot.unavailable, "channel-closed")
        self.assertEqual([entry.question_id for entry in snapshot.inquiries], ["q-1"])

    def test_capabilities_serves_the_fact_and_refuses_bad_calls(self):
        raw = canonical_json(ctl.LiveWireQuery(instance_id="c" * 64, token="d" * 64,
                                               identity=self.run).to_payload())
        self.assertEqual(lv.LiveCapabilities.from_payload(
            decode_strict_json(self.endpoint.capabilities(raw))).inquiry_delivery,
            "cooperative-checkpoint")
        with self.assertRaises(BoardError):
            self.endpoint.capabilities(canonical_json(
                ctl.LiveWireQuery(instance_id="c" * 64, token="d" * 64,
                                  identity=identity(attempt_id="other")).to_payload()))


class EndpointLifecycleTests(unittest.TestCase):
    def test_both_profiles_are_applied_before_the_register_call(self):
        fake = FakeC2()
        real_cc = ctl.cc
        ctl.cc = fake
        try:
            target = endpoint(name="Ava")
            described = target.start()
            self.assertEqual(fake.roles_at_register, ("client", "server"))
            contract, implementation, name, concurrency = fake.registered[0]
            self.assertIs(contract, TEST_CRM)
            self.assertIs(implementation, target)
            self.assertEqual(name, "Ava")
            self.assertEqual(concurrency.mode, cc.ConcurrencyMode.PARALLEL)
            self.assertEqual((described.address, described.name, described.host_pid),
                             (fake.address, "Ava", os.getpid()))
            self.assertIsNone(described.socket)
            started = endpoint(name="Bo")
            started.start()
            with self.assertRaises(BoardError):
                started.start()
            started.stop()
            started.stop()
        finally:
            ctl.cc = real_cc
        self.assertEqual(fake.unregistered, ["Bo"])
        self.assertEqual(fake.shutdowns, 1)

    def test_the_socket_identity_is_captured_from_the_registered_address(self):
        with tempfile.TemporaryDirectory() as directory:
            server_id = "cc" + "1" * 38
            socket_path = Path(directory) / f"{server_id}.sock"
            listener = socket_module.socket(socket_module.AF_UNIX, socket_module.SOCK_STREAM)
            listener.bind(str(socket_path))
            self.addCleanup(listener.close)
            real_directory = ctl.C_TWO_IPC_DIRECTORY
            real_cc = ctl.cc
            ctl.C_TWO_IPC_DIRECTORY = directory
            ctl.cc = FakeC2(address=f"ipc://{server_id}")
            try:
                described = endpoint(name="Dana").start()
            finally:
                ctl.cc = real_cc
                ctl.C_TWO_IPC_DIRECTORY = real_directory
            info = os.stat(socket_path)
            self.assertEqual((described.socket.device, described.socket.inode),
                             (info.st_dev, info.st_ino))
            # The captured identity is exactly what the cleanup later compares,
            # and the one confirmed-vanished evidence deletes only this file.
            evidence = ctl.ConfirmedProcessGone(pid=described.host_pid, exit_code=0,
                                                group_gone=True)
            self.assertEqual(ctl.cleanup_abandoned_socket(described, evidence).outcome,
                             "deleted")
            self.assertFalse(os.path.exists(socket_path))


class CleanupPrimitiveTests(unittest.TestCase):
    def setUp(self):
        self.descriptor = ctl.LiveEndpointDescriptor(
            address="ipc://cc" + "2" * 38, name="Edith", instance_id="a" * 64,
            host_pid=4242, socket=ctl.EndpointSocketFact(
                address="ipc://cc" + "2" * 38, path="/tmp/c_two_ipc/cc" + "2" * 38 + ".sock",
                device=1, inode=99))

    def gone(self, **overrides) -> ctl.ConfirmedProcessGone:
        values = dict(pid=4242, exit_code=-9, group_gone=True)
        values.update(overrides)
        return ctl.ConfirmedProcessGone(**values)

    def test_unconfirmed_or_foreign_evidence_refuses(self):
        cases = [
            (self.gone(exit_code=None), "vanishing-not-confirmed"),
            (self.gone(group_gone=False), "vanishing-not-confirmed"),
            (self.gone(pid=9999), "process-identity-mismatch"),
        ]
        for evidence, reason in cases:
            outcome = ctl.cleanup_abandoned_socket(self.descriptor, evidence)
            self.assertEqual((outcome.outcome, outcome.reason), ("refused", reason), reason)

    def test_an_unknown_identity_refuses_without_touching_anything(self):
        unknown = ctl.LiveEndpointDescriptor(address="ipc://cc" + "3" * 38, name="Fen",
                                             instance_id="a" * 64, host_pid=4242)
        self.assertEqual(ctl.cleanup_abandoned_socket(unknown, self.gone()).reason,
                         "socket-identity-unknown")

    def test_a_replaced_file_refuses_and_a_missing_file_is_already_absent(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "replaced.sock"
            path.write_text("someone else's file now")
            replaced = ctl.LiveEndpointDescriptor(
                address=self.descriptor.address, name="Edith", instance_id="a" * 64,
                host_pid=4242, socket=ctl.EndpointSocketFact(
                    address=self.descriptor.address, path=str(path), device=1, inode=99))
            outcome = ctl.cleanup_abandoned_socket(replaced, self.gone())
            self.assertEqual((outcome.outcome, outcome.reason),
                             ("refused", "socket-file-replaced"))
            self.assertTrue(path.exists())
            absent = ctl.LiveEndpointDescriptor(
                address=self.descriptor.address, name="Edith", instance_id="a" * 64,
                host_pid=4242, socket=ctl.EndpointSocketFact(
                    address=self.descriptor.address, path=str(Path(directory) / "gone.sock"),
                    device=1, inode=99))
            self.assertEqual(ctl.cleanup_abandoned_socket(absent, self.gone()).outcome,
                             "already-absent")


class ReadyMaterialTests(unittest.TestCase):
    def test_ready_material_is_one_fresh_private_file_without_the_token(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "live-ready.json"
            descriptor = ctl.LiveEndpointDescriptor(address="ipc://cc" + "4" * 38, name="Gaia",
                                                    instance_id="a" * 64, host_pid=os.getpid())
            ctl.write_ready_material(path, descriptor)
            info = os.stat(path)
            self.assertEqual(stat.S_IMODE(info.st_mode), 0o600)
            text = path.read_text()
            self.assertEqual(ctl.LiveEndpointDescriptor.from_payload(json.loads(text)),
                             descriptor)
            with self.assertRaises(OSError):
                ctl.write_ready_material(path, descriptor)


class StubPeer:
    """One scripted C-Two connection the channel tests call into.

    ``delay`` makes every operation stall like a peer that never answers, so
    the channel's own bounded-call mechanism — not a fake clock — faces a
    genuinely blocked transport.
    """

    def __init__(self, replies, delay: float = 0.0):
        self.replies = list(replies)
        self.delay = delay
        self.calls: list[tuple[str, str]] = []

    def __enter__(self):
        return self

    def __exit__(self, *exception):
        return False

    def capabilities(self, text):
        self.calls.append(("capabilities", text))
        return self._answer()

    def request(self, text):
        self.calls.append(("request", text))
        return self._answer()

    def observe(self, text):
        self.calls.append(("observe", text))
        return self._answer()

    def _answer(self):
        if self.delay:
            time.sleep(self.delay)
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


class ChannelUnitTests(unittest.TestCase):
    def setUp(self):
        self.run = identity()
        self.channel = ctl.CTwoLiveChannel(self.run, TEST_CRM, name="Hana",
                                           address="ipc://cc" + "5" * 38,
                                           instance_id="a" * 64, token="b" * 64)
        self._restore = ctl.cc

    def tearDown(self):
        ctl.cc = self._restore

    def connect(self, *replies, delay: float = 0.0) -> StubPeer:
        stub = StubPeer(replies, delay=delay)
        channel = self.channel

        def connect(contract, *, name, address):
            self.assertIs(contract, TEST_CRM)
            self.assertEqual((name, address), ("Hana", "ipc://cc" + "5" * 38))
            return stub

        ctl.cc = SimpleNamespace(connect=connect)
        return stub

    def test_request_maps_success_refusals_and_bad_replies(self):
        queued = canonical_json(lv.LiveReply(status="queued", observed=True,
                                             state="queued").to_payload())
        self.connect(queued, queued)
        self.assertEqual(self.channel.request(inquiry_request(), timeout_ms=1500).status,
                         "queued")
        self.assertEqual(self.channel.request(inquiry_request(), timeout_ms=1500).status,
                         "queued")
        self.connect(RuntimeError("connection refused"))
        reply = self.channel.request(inquiry_request(), timeout_ms=1500)
        self.assertEqual((reply.status, reply.reason_code),
                         ("unavailable", "transport-unreachable"))
        self.connect("not a reply frame")
        reply = self.channel.request(inquiry_request(), timeout_ms=1500)
        self.assertEqual((reply.status, reply.reason_code), ("unavailable", "reply-invalid"))
        with self.assertRaises(BoardError):
            self.channel.request(inquiry_request(), timeout_ms=99)
        foreign = ctl.CTwoLiveChannel(identity(attempt_id="other"), TEST_CRM, name="Hana",
                                      address="ipc://cc" + "5" * 38, instance_id="a" * 64,
                                      token="b" * 64)
        self.assertEqual(foreign.request(inquiry_request(), timeout_ms=1500).reason_code,
                         "identity-mismatch")

    def test_the_whole_connect_and_call_is_bounded_by_the_window(self):
        # A stalling peer against a short window: the bounded mechanism
        # returns within the window — not after the blocked call finally
        # answers — and reports the expiry, never a stopped peer.
        self.connect(*(["x"] * 4), delay=0.4)
        started = time.monotonic()
        reply = self.channel.request(inquiry_request(), timeout_ms=150)
        elapsed = time.monotonic() - started
        self.assertEqual((reply.status, reply.reason_code),
                         ("unavailable", "transport-window-expired"))
        self.assertLess(elapsed, 1.2)
        self.assertGreaterEqual(elapsed, 0.14)
        snapshot = self.channel.observe(limit=5, timeout_ms=150)
        self.assertEqual((snapshot.observed, snapshot.reason),
                         (False, "transport-window-expired"))
        with self.assertRaises(BoardError):
            self.channel.capabilities()

    def test_bounded_call_slots_report_busy_and_drain_back(self):
        baseline = threading.active_count()
        self.connect(*(["x"] * 16), delay=1.0)
        outcomes = []
        for _ in range(ctl.MAX_INFLIGHT_LIVE_CALLS):
            outcomes.append(self.channel.request(inquiry_request(), timeout_ms=100).reason_code)
        self.assertEqual(outcomes, ["transport-window-expired"] * ctl.MAX_INFLIGHT_LIVE_CALLS)
        # Every bounded slot is held by a stuck attempt: the next call spends
        # its whole window waiting for one and reports busyness instead of
        # growing another worker.
        started = time.monotonic()
        busy = self.channel.request(inquiry_request(), timeout_ms=100)
        elapsed = time.monotonic() - started
        self.assertEqual((busy.status, busy.reason_code), ("unavailable", "transport-busy"))
        self.assertLess(elapsed, 1.0)
        self.assertLessEqual(threading.active_count(),
                             baseline + ctl.MAX_INFLIGHT_LIVE_CALLS)
        # Once the stuck attempts drain, the slots serve calls again.
        time.sleep(1.2)
        reply = self.channel.request(inquiry_request(), timeout_ms=100)
        self.assertEqual(reply.reason_code, "transport-window-expired")
        deadline = time.monotonic() + 5
        while threading.active_count() > baseline and time.monotonic() < deadline:
            time.sleep(0.05)
        self.assertEqual(threading.active_count(), baseline)

    def test_observe_and_capabilities_map_the_same_taxonomy(self):
        empty = canonical_json(lv.LiveSnapshot(observed=True).to_payload())
        self.connect(empty)
        self.assertTrue(self.channel.observe(after_seq=0, limit=10, timeout_ms=1500).observed)
        self.connect(RuntimeError("gone"))
        self.assertEqual(self.channel.observe(limit=10, timeout_ms=1500).reason,
                         "transport-unreachable")
        self.connect("not a snapshot")
        self.assertEqual(self.channel.observe(limit=10, timeout_ms=1500).reason, "reply-invalid")
        for bad in (dict(limit=0), dict(limit=10, after_seq=-1), dict(limit=10, fields="activity"),
                    dict(limit=10, inquiry_id="")):
            with self.assertRaises(BoardError):
                self.channel.observe(timeout_ms=1500, **bad)
        caps = canonical_json(lv.LiveCapabilities(
            inquiry_delivery="cooperative-checkpoint").to_payload())
        self.connect(caps)
        self.assertEqual(self.channel.capabilities().inquiry_delivery,
                         "cooperative-checkpoint")
        self.connect(RuntimeError("dead"))
        with self.assertRaises(BoardError):
            self.channel.capabilities()

    def test_close_is_local_and_refuses_further_calls(self):
        self.channel.close(reason="worker-done")
        self.assertEqual(self.channel.request(inquiry_request(), timeout_ms=1500).reason_code,
                         "channel-closed")
        self.assertEqual(self.channel.observe(limit=10, timeout_ms=1500).unavailable,
                         "channel-closed")
        with self.assertRaises(BoardError):
            self.channel.capabilities()

    def test_the_channel_satisfies_the_live_channel_protocol(self):
        self.assertIsInstance(self.channel, lv.LiveChannel)


class LivePeer:
    """The subprocess driver: start, command, stop and reap one real endpoint."""

    def __init__(self, root: Path, *, name: str | None, run: RunIdentity,
                 delivery: str, rebind: str | None = None, stall: float | None = None):
        self.directory = Path(tempfile.mkdtemp(prefix="endpoint-", dir=root))
        environment = {key: value for key, value in os.environ.items()
                       if key not in SANITIZED_VARIABLES}
        source = str(REPO_ROOT / "src")
        tests = str(REPO_ROOT / "tests" / "python")
        inherited = environment.get("PYTHONPATH")
        environment["PYTHONPATH"] = os.pathsep.join(
            [source, tests] + ([inherited] if inherited else []))
        environment["BUDDY_DEV_SOURCE"] = "1"
        environment["HOME"] = str(self.directory / "home")
        environment["TMPDIR"] = str(self.directory / "tmp")
        environment["C2_RELAY_ANCHOR_ADDRESS"] = ""
        environment["C2_ENV_FILE"] = ""
        (self.directory / "home").mkdir(mode=0o700)
        (self.directory / "tmp").mkdir(mode=0o700)
        command = [sys.executable, "-c", PEER_LAUNCHER, str(FIXTURE_PATH), PEER_MODULE_NAME]
        if rebind is not None:
            command += ["rebind", rebind]
        self.process = subprocess.Popen(
            command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            env=environment, text=True, cwd=str(self.directory), start_new_session=True)
        self.ready_path = self.directory / "live-ready.json"
        self.token_path = self.directory / "live-token"
        start = {"op": "start", "identity": run.to_payload(), "delivery": delivery,
                 "name": name, "readyPath": str(self.ready_path),
                 "tokenPath": str(self.token_path)}
        if stall is not None:
            start["stallSeconds"] = stall
        reply = self.command(start)
        if not reply.get("ok"):
            self.reap()
            raise AssertionError(f"the peer did not start: {reply} {self.diagnostics()}")
        self.descriptor = ctl.LiveEndpointDescriptor.from_payload(reply["descriptor"])
        self.configured_roles = reply["configuredRoles"]

    def diagnostics(self) -> str:
        try:
            return self.process.stderr.read()
        except (OSError, ValueError):
            return ""

    @property
    def name(self) -> str:
        return self.descriptor.name

    @property
    def address(self) -> str:
        return self.descriptor.address

    @property
    def instance_id(self) -> str:
        return self.descriptor.instance_id

    @property
    def token(self) -> str:
        return self.token_path.read_text()

    def command(self, payload: dict, *, timeout: float = 15.0) -> dict:
        assert self.process.stdin and self.process.stdout
        self.process.stdin.write(canonical_json(payload) + "\n")
        self.process.stdin.flush()
        line = self.process.stdout.readline()
        if not line:
            raise AssertionError(f"the peer closed its output before answering: "
                                 f"{self.diagnostics()}")
        return json.loads(line)

    def stop(self) -> dict:
        reply = self.command({"op": "stop"})
        code = self.process.wait(timeout=15)
        if code != 0:
            raise AssertionError(f"the peer exited with {code}: {self.diagnostics()}")
        return reply

    def kill_group(self) -> int:
        pgid = os.getpgid(self.process.pid)
        os.killpg(pgid, signal.SIGKILL)
        code = self.process.wait(timeout=15)
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            try:
                os.killpg(pgid, 0)
            except ProcessLookupError:
                return code
            time.sleep(0.02)
        raise AssertionError("the killed peer's process group is still observable")

    def reap(self) -> None:
        if self.process.poll() is None:
            try:
                self.process.stdin.close()
            except OSError:
                pass
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5)
        for stream in (self.process.stdout, self.process.stderr):
            if stream is not None:
                stream.close()


class LivePeerCase(unittest.TestCase):
    """One private root and one real C-Two subprocess peer per test."""

    @classmethod
    def setUpClass(cls):
        cls.class_root = Path(tempfile.mkdtemp(prefix="c-two-live-"))

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.class_root, ignore_errors=True)

    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="peer-", dir=self.class_root))
        self.evidence_directory = self.root / "evidence"
        self.evidence_directory.mkdir()
        self.peers: list[LivePeer] = []
        self.identity = identity()

    def tearDown(self):
        for peer in self.peers:
            peer.reap()

    def record(self, name: str, facts: dict) -> None:
        facts = {"name": name, **facts}
        path = self.evidence_directory / f"{name}.json"
        path.write_text(canonical_json(facts))
        print(f"[c-two-live evidence] {path}: {canonical_json(facts)}")

    def spawn(self, *, name: str | None = None, run: RunIdentity | None = None,
              delivery: str = "cooperative-checkpoint", rebind: str | None = None,
              stall: float | None = None) -> LivePeer:
        peer = LivePeer(self.root, name=name, run=run or self.identity, delivery=delivery,
                        rebind=rebind, stall=stall)
        self.peers.append(peer)
        return peer

    def channel(self, peer: LivePeer, *, token: str | None = None,
                instance_id: str | None = None, address: str | None = None,
                run: RunIdentity | None = None) -> ctl.CTwoLiveChannel:
        return ctl.CTwoLiveChannel(run or self.identity, TEST_CRM, name=peer.name,
                                   address=address or peer.address,
                                   instance_id=instance_id or peer.instance_id,
                                   token=token if token is not None else peer.token)

    def deliver(self, peer: LivePeer, channel: ctl.CTwoLiveChannel, request: lv.LiveRequest,
                reply: dict | None = None, *, timeout_ms: int = 5000) -> lv.LiveReply:
        """One ask over real transport, consumed and settled by the peer's owner."""
        box: list[lv.LiveReply] = []
        thread = threading.Thread(target=lambda: box.append(
            channel.request(request, timeout_ms=timeout_ms)), daemon=True)
        thread.start()
        consumed = None
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            consumed = peer.command({"op": "consume", "timeout": 0.3})["request"]
            if consumed is not None:
                break
        self.assertIsNotNone(consumed)
        self.assertEqual(consumed["requestId"], request.request_id)
        peer.command({"op": "settle", "requestId": request.request_id,
                      "reply": reply or QUEUED_REPLY})
        thread.join(timeout=5.0)
        self.assertFalse(thread.is_alive())
        return box[0]

    def wait_pending(self, peer: LivePeer, count: int, *, timeout: float = 10.0) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if peer.command({"op": "pendingCount"})["count"] >= count:
                return
            time.sleep(0.02)
        raise AssertionError(f"the peer never held {count} pending requests")


class SubprocessLifecycleTests(LivePeerCase):
    def test_one_run_one_endpoint_with_a_clean_stop(self):
        peer = self.spawn()
        socket_path = Path(peer.descriptor.socket.path)
        self.assertEqual(peer.configured_roles, ["client", "server"])
        self.assertTrue(socket_path.exists())
        self.assertTrue(peer.ready_path.exists())
        self.assertEqual(stat.S_IMODE(os.stat(peer.ready_path).st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(os.stat(peer.token_path).st_mode), 0o600)
        self.assertEqual(ctl.LiveEndpointDescriptor.from_payload(
            json.loads(peer.ready_path.read_text())), peer.descriptor)
        self.assertNotIn(peer.token, peer.ready_path.read_text())
        channel = self.channel(peer)
        reply = self.deliver(peer, channel, inquiry_request())
        self.assertEqual((reply.status, reply.state), ("queued", "queued"))
        peer.command({"op": "publishInquiry", "state": {"questionId": "question-1",
                                                        "status": "delivered"}})
        peer.command({"op": "publishInquiry", "state": {"questionId": "question-1",
                                                        "status": "answered", "answer": "yes"}})
        snapshot = channel.observe(after_seq=0, limit=10, timeout_ms=1500)
        self.assertEqual(snapshot.inquiries[-1].status, "answered")
        self.assertEqual(channel.capabilities().inquiry_delivery, "cooperative-checkpoint")
        stop_reply = peer.stop()
        self.assertIsNone(stop_reply["addressAfter"])
        self.record("normal-lifecycle", {
            "create": {"path": str(socket_path), "inode": peer.descriptor.socket.inode},
            "stop": {"exitCode": peer.process.returncode, "addressAfter": None},
            "delete": {"exists": socket_path.exists()},
        })
        self.assertFalse(socket_path.exists())

    def test_the_owner_settles_the_real_handoff_with_refusal_then_retry(self):
        peer = self.spawn()
        channel = self.channel(peer)
        refused = self.deliver(peer, channel, inquiry_request(),
                               dict(status="unavailable", reason_code="journal-unavailable",
                                    error_code="not-ready"))
        self.assertEqual((refused.status, refused.reason_code, refused.error_code),
                         ("unavailable", "journal-unavailable", "not-ready"))
        # Nothing was committed over real transport either.
        self.assertEqual(peer.command({"op": "pendingCount"})["count"], 0)
        snapshot = channel.observe(limit=10, timeout_ms=1500)
        self.assertEqual(snapshot.inquiries, ())
        retried = self.deliver(peer, channel, inquiry_request())
        self.assertEqual((retried.status, retried.state), ("queued", "queued"))
        self.assertEqual(channel.observe(inquiry_id="question-1", timeout_ms=1500)
                         .inquiries[0].status, "queued")
        # Exactly one queue entry was ever consumed for each attempt.
        self.assertIsNone(peer.command({"op": "consume", "timeout": 0.2})["request"])
        peer.stop()

    def test_the_same_name_on_two_addresses_does_not_cross_wire(self):
        second_identity = identity(attempt_id="attempt-second")
        first = self.spawn(name="SameName")
        second = self.spawn(name="SameName", run=second_identity)
        self.assertNotEqual(first.address, second.address)
        self.assertEqual(first.name, second.name)
        first_channel = self.channel(first)
        second_channel = self.channel(second, run=second_identity)
        self.assertEqual(self.deliver(first, first_channel, inquiry_request()).status, "queued")
        self.assertEqual(self.deliver(second, second_channel, inquiry_request(
            request_id="r-2", question_id="q-2", run=second_identity)).status, "queued")
        # The second run's identity sent to the first endpoint's address is
        # refused by that endpoint's own binding.
        crossing = ctl.CTwoLiveChannel(second_identity, TEST_CRM,
                                       name="SameName", address=first.address,
                                       instance_id=first.instance_id, token=first.token)
        refused = crossing.request(inquiry_request(run=second_identity), timeout_ms=1500)
        self.assertEqual((refused.status, refused.reason_code),
                         ("unavailable", "identity-mismatch:attemptId"))
        self.assertEqual(first_channel.capabilities().inquiry_delivery,
                         second_channel.capabilities().inquiry_delivery)
        first.stop()
        second.stop()

    def test_old_tokens_instances_and_identity_components_are_rejected(self):
        peer = self.spawn()
        channel = self.channel(peer)
        self.assertEqual(self.deliver(peer, channel, inquiry_request()).status, "queued")
        stale_token = self.channel(peer, token="e" * 64)
        self.assertEqual(stale_token.request(inquiry_request(), timeout_ms=1500).reason_code,
                         "token-mismatch")
        self.assertEqual(stale_token.observe(limit=5, timeout_ms=1500).reason, "token-mismatch")
        stale_instance = self.channel(peer, instance_id="f" * 64)
        self.assertEqual(stale_instance.request(inquiry_request(), timeout_ms=1500).reason_code,
                         "instance-mismatch")
        for field, value in (("task_id", "task-other"), ("attempt_id", "attempt-other"),
                             ("generation", 7), ("invocation_id", "invocation-other"),
                             ("turn_id", "turn-other"), ("turn_id", None),
                             ("input_sha256", "cd" * 32)):
            foreign = identity(**{field: value})
            reply = self.channel(peer, run=foreign).request(inquiry_request(run=foreign),
                                                            timeout_ms=1500)
            self.assertEqual((reply.status, reply.reason_code),
                             ("unavailable", f"identity-mismatch:{IDENTITY_WIRE_NAMES[field]}"),
                             field)
        peer.stop()

    def test_wrong_and_oversized_frames_over_real_transport(self):
        peer = self.spawn()
        rpc_config.configure_client()
        oversized = "x" * (lv.MAX_LIVE_FRAME_BYTES + 1)
        with cc.connect(TEST_CRM, name=peer.name, address=peer.address) as raw:
            for raw_text, expected in (("not json", "frame-invalid"), ("{}", "frame-invalid"),
                                       ('{"instanceId":1}', "frame-invalid"),
                                       (oversized, "frame-too-large")):
                reply = decode_reply(raw.request(raw_text))
                self.assertEqual((reply.status, reply.reason_code),
                                 ("unavailable", expected), raw_text[:16])
                snapshot = decode_snapshot(raw.observe(raw_text))
                self.assertEqual((snapshot.observed, snapshot.unavailable),
                                 (False, expected), raw_text[:16])
        peer.stop()

    def test_a_closed_endpoint_refuses_but_keeps_serving_facts(self):
        peer = self.spawn()
        channel = self.channel(peer)
        self.deliver(peer, channel, inquiry_request())
        peer.command({"op": "publishInquiry", "state": {"questionId": "question-1",
                                                        "status": "delivered"}})
        peer.command({"op": "close", "reason": "run-finished"})
        reply = channel.request(inquiry_request(request_id="r-late", question_id="q-late"),
                                timeout_ms=1500)
        self.assertEqual((reply.status, reply.reason_code), ("unavailable", "channel-closed"))
        snapshot = channel.observe(after_seq=0, limit=10, timeout_ms=1500)
        self.assertEqual(snapshot.unavailable, "channel-closed")
        self.assertEqual(snapshot.inquiries[-1].status, "delivered")
        peer.stop()

    def test_the_per_run_inquiry_limit_over_real_transport(self):
        peer = self.spawn()
        channel = self.channel(peer)
        # The peer's simulated owner loop settles each committed question as
        # its real controller would, so the run's budget fills for real.
        peer.command({"op": "autoSettle", "reply": QUEUED_REPLY, "seconds": 60})
        for index in range(lv.MAX_INQUIRIES_PER_RUN):
            reply = channel.request(inquiry_request(request_id=f"r-{index}",
                                                    question_id=f"q-{index}"),
                                    timeout_ms=5000)
            self.assertEqual((reply.status, reply.state), ("queued", "queued"), index)
        refused = channel.request(inquiry_request(request_id="r-late", question_id="q-late"),
                                  timeout_ms=1500)
        self.assertEqual((refused.status, refused.reason_code), ("unavailable", "inquiry-limit"))
        # Committed replays stay idempotent at the full budget.
        self.assertEqual(
            channel.request(inquiry_request(request_id="r-0", question_id="q-0"),
                            timeout_ms=1500).native_correlation.value["duplicate"], True)
        peer.command({"op": "close", "reason": "run-finished"})
        self.assertEqual(peer.command({"op": "pendingCount"})["count"], 0)
        peer.stop()

    def test_a_killed_endpoint_is_unavailable_and_never_stopped(self):
        peer = self.spawn()
        channel = self.channel(peer)
        socket_path = Path(peer.descriptor.socket.path)
        inode = peer.descriptor.socket.inode
        self.assertTrue(socket_path.exists())
        self.assertEqual(self.deliver(peer, channel, inquiry_request()).status, "queued")
        exit_code = peer.kill_group()
        self.assertLess(exit_code, 0)
        reply = channel.request(inquiry_request(request_id="r-after", question_id="q-after"),
                                timeout_ms=1500)
        self.assertEqual((reply.status, reply.reason_code),
                         ("unavailable", "transport-unreachable"))
        snapshot = channel.observe(limit=10, timeout_ms=1500)
        self.assertEqual((snapshot.observed, snapshot.reason),
                         (False, "transport-unreachable"))
        with self.assertRaises(BoardError):
            channel.capabilities()
        evidence = ctl.ConfirmedProcessGone(pid=peer.process.pid, exit_code=exit_code,
                                            group_gone=True)
        outcome = ctl.cleanup_abandoned_socket(peer.descriptor, evidence)
        self.record("sigkill-cleanup", {
            "create": {"path": str(socket_path), "inode": inode},
            "stop": {"exitCode": exit_code, "killedBy": "SIGKILL"},
            "delete": {"outcome": outcome.outcome, "reason": outcome.reason,
                       "exists": socket_path.exists()},
        })
        self.assertEqual(outcome.outcome, "deleted")
        self.assertFalse(socket_path.exists())
        # The client side is still only closed, never told the process stopped.
        channel.close(reason="worker-gave-up")
        self.assertEqual(channel.request(inquiry_request(), timeout_ms=1500).reason_code,
                         "channel-closed")

    def test_a_replaced_socket_file_is_refused_by_the_cleanup(self):
        first = self.spawn()
        socket_path = Path(first.descriptor.socket.path)
        self.assertTrue(socket_path.exists())
        exit_code = first.kill_group()
        self.assertLess(exit_code, 0)
        server_id = first.address[len("ipc://"):]
        replacement = self.spawn(rebind=server_id)
        self.assertEqual(replacement.address, first.address)
        self.assertNotEqual(replacement.descriptor.socket.inode,
                            first.descriptor.socket.inode)
        evidence = ctl.ConfirmedProcessGone(pid=first.process.pid, exit_code=exit_code,
                                            group_gone=True)
        outcome = ctl.cleanup_abandoned_socket(first.descriptor, evidence)
        self.record("replaced-file-cleanup", {
            "create": {"path": str(socket_path), "inode": first.descriptor.socket.inode},
            "stop": {"exitCode": exit_code},
            "replace": {"inode": replacement.descriptor.socket.inode},
            "delete": {"outcome": outcome.outcome, "reason": outcome.reason,
                       "exists": socket_path.exists()},
        })
        self.assertEqual((outcome.outcome, outcome.reason),
                         ("refused", "socket-file-replaced"))
        self.assertTrue(socket_path.exists())
        replacement.stop()
        self.assertFalse(socket_path.exists())

    def test_a_committed_request_id_holds_one_real_queue_entry(self):
        peer = self.spawn()
        channel = self.channel(peer)
        self.assertEqual(self.deliver(peer, channel, inquiry_request()).status, "queued")
        # The identical replay and a duplicate under a new request id never
        # enqueue a second delivery.
        self.assertEqual(channel.request(inquiry_request(), timeout_ms=1500)
                         .native_correlation.value["duplicate"], True)
        self.assertEqual(channel.request(inquiry_request(request_id="request-2"),
                                         timeout_ms=1500).native_correlation.value["duplicate"],
                         True)
        # The committed request id conflicts over its whole payload — a
        # different question id included — without enqueuing it.
        refused = channel.request(inquiry_request(question_id="question-2"), timeout_ms=1500)
        self.assertEqual((refused.status, refused.reason_code),
                         ("unavailable", "request-payload-conflict"))
        self.assertIsNone(peer.command({"op": "consume", "timeout": 0.2})["request"])
        self.assertEqual(peer.command({"op": "pendingCount"})["count"], 0)
        peer.stop()

    def test_a_stalling_endpoint_returns_within_the_window_and_stays_bounded(self):
        peer = self.spawn(stall=2.0)
        channel = self.channel(peer)
        for index in range(3):
            started = time.monotonic()
            reply = channel.request(inquiry_request(request_id=f"r-{index}",
                                                    question_id=f"q-{index}"),
                                    timeout_ms=300)
            elapsed = time.monotonic() - started
            self.assertEqual((reply.status, reply.reason_code),
                             ("unavailable", "transport-window-expired"))
            self.assertLess(elapsed, 1.5)
            self.assertGreaterEqual(elapsed, 0.28)
        snapshot = channel.observe(limit=5, timeout_ms=300)
        self.assertEqual((snapshot.observed, snapshot.reason),
                         (False, "transport-window-expired"))
        with self.assertRaises(BoardError):
            channel.capabilities()
        # Scheduling tolerance for the drain: the stalling calls finish, the
        # bounded slots release, and the clean stop is not blocked by them.
        time.sleep(2.2)
        stop_reply = peer.stop()
        self.record("stalling-endpoint", {
            "stallSeconds": 2.0,
            "windowMs": 300,
            "bounded": {"requests": 3, "observe": 1, "capabilities": 1},
            "stop": {"exitCode": peer.process.returncode,
                     "addressAfter": stop_reply["addressAfter"]},
        })
        self.assertIsNone(stop_reply["addressAfter"])


if __name__ == "__main__":
    unittest.main()
