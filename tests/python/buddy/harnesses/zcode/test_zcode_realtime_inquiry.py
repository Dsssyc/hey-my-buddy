"""Delivery and held-queue boundaries using recorded CLI 0.17.0 shapes."""
import base64
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest import mock
import uuid
import zlib

from temporary import TemporaryDirectory
from hey_my_buddy.buddy.harnesses.inquiry_bridge import InquiryBridge, _owner_response
from hey_my_buddy.buddy.harnesses.zcode.inquiry import NativeInquiry, MAX_SNAPSHOT_BYTES
from hey_my_buddy.buddy.harnesses.zcode.protocol import NativeError, NativeRpcError

FIXTURE = Path(__file__).parent / "fixtures/inquiry-live-app-server-0.17.0.json"


class Peer:
    def __init__(self):
        self.requests = []
        self.queue = {"items": [], "autoDrain": True}
        self.revision = 1
        self.status = "accepted"
        self.delivery = "queue"
        self.failure = None
        self.lying_delete = False
        self.on_send = lambda _: None
        self.fragment = False
        self.corrupt = False

    def call(self, method, params):
        self.requests.append((method, params))
        if method == "v4/conversation/subscribe":
            sub = "sub-" + str(len(self.requests))
            frame = {"topic": self.driver.topic, "subscriptionId": sub, "payload": {"kind": "snapshot", "snapshot": {
                "sessionId": self.driver.session_id, "revision": self.revision, "logEpoch": "epoch",
                "queue": json.loads(json.dumps(self.queue))}}}
            shell = {"wireVersion": 3, "deliveryKind": "initial", "topic": self.driver.topic,
                     "subscriptionId": sub, "logicalFrameId": sub + "-lf-1"}
            if self.fragment:
                raw = json.dumps(frame).encode()
                checksum = f"{zlib.crc32(raw):08x}" if not self.corrupt else "00000000"
                parts = [raw[:len(raw)//2], raw[len(raw)//2:]]
                for i, part in enumerate(parts):
                    self.emit({**shell, "kind": "fragment", "fragmentIndex": i, "fragmentCount": 2,
                        "logicalBytes": len(raw), "checksum": {"algorithm": "crc32", "value": checksum},
                        "dataBase64": base64.b64encode(part).decode()})
            else:
                self.emit({**shell, "kind": "complete", "frame": frame})
            return {"ack": {"subscriptionId": sub}}
        if method == "v4/conversation/unsubscribe":
            return {}
        if self.failure is not None:
            raise self.failure
        kind = params["type"]
        if kind == "setAutoDrain":
            self.queue["autoDrain"] = params["payload"]["autoDrain"]
            self.revision += 1
        elif kind == "deleteQueueItem":
            assert params["baseRevision"] == self.revision
            assert params["baseLogEpoch"] == "epoch"
            if not self.lying_delete:
                self.queue["items"] = [v for v in self.queue["items"] if v["queueItemId"] != params["payload"]["queueItemId"]]
            self.revision += 1
        elif kind == "sendText":
            self.on_send(params)
        return {"status": self.status, "result": {"delivery": self.delivery, "inputId": params["commandId"]}}

    def emit(self, wire):
        self.driver.observe({"method": "v4/conversation/frame", "params": wire}, 1)

    def pump(self):
        raise AssertionError("fixture did not emit the required snapshot")


class RealtimeInquiryTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        identity = {"taskId": str(uuid.uuid4()), "attemptId": str(uuid.uuid4()), "generation": 1, "turnId": str(uuid.uuid4())}
        self.bridge = InquiryBridge(identity=identity, journal_path=str(Path(self.temp.name)/"inquiries.jsonl"),
            error_factory=NativeError, event_metadata=lambda _: {"kind": "test"}, delivery_mode="realtime", checkpoint_tool=None)
        self.bridge.start()
        self.bridge.activate("session")
        self.addCleanup(self.bridge.close)
        self.peer = Peer()
        self.evidence = SimpleNamespace(session_id="session", turn_id="turn", call_id=None,
                                        finish_failed=False, completed_ordinal=0)
        self.driver = NativeInquiry(self.peer, self.evidence, self.bridge, "mcp__test__hey_my_buddy_answer_inquiry")
        self.peer.driver = self.driver
        self.driver.hold_queue()

    def ask(self, inquiry="q"):
        self.assertTrue(self.bridge._ask({"inquiryId": inquiry, "question": "Status?"}, _owner_response)["ok"])

    def event(self, kind, payload, *, session="session", turn="turn"):
        self.driver.observe({"method": "session/event", "params": {"sessionId": session, "turnId": turn,
            "seq": 2, "type": kind, "payload": payload}}, 2)

    def test_each_ack_status_and_delivery_remain_queued_and_are_never_resent(self):
        for status in ("accepted", "noop", "duplicate", "stale", "rejected", "failed"):
            for delivery in ("guide", "queue", "startNow"):
                with self.subTest(status=status, delivery=delivery):
                    inquiry = status + "-" + delivery
                    self.ask(inquiry)
                    self.peer.status, self.peer.delivery = status, delivery
                    before = len(self.driver.sent)
                    self.driver.poll()
                    self.driver.poll()
                    self.assertEqual(len(self.driver.sent), before + 1)
                    self.assertEqual(self.bridge.entries[inquiry]["state"], "queued")
                    sent = self.peer.requests[-1][1]
                    self.assertEqual(sent["payload"]["requestedDelivery"], "guide")
                    self.assertNotIn("baseRevision", sent)

    def test_rpc_rejection_does_not_fail_the_root_but_real_native_failure_does(self):
        self.ask()
        self.peer.failure = NativeRpcError("native-rpc-error", "rejected")
        self.driver.poll(); self.driver.poll()
        self.assertEqual(len(self.driver.sent), 1)
        self.assertEqual(self.bridge.entries["q"]["state"], "queued")
        self.ask("other")
        self.peer.failure = NativeError("native-turn-failed", "root failed")
        with self.assertRaises(NativeError):
            self.driver.poll()

    def test_preparation_failures_disable_sending_and_preserve_the_failure_fact(self):
        self.ask()
        for error in (NativeError("native-queue-hold-failed", "rejected"),
                      NativeError("invalid-inquiry-snapshot", "invalid wire"),
                      NativeRpcError("native-rpc-error", "unsupported command")):
            with self.subTest(code=error.code), mock.patch.object(self.driver, "snapshot", side_effect=error):
                self.driver.hold_queue()
                self.driver.poll()
                self.assertFalse(self.driver.sent)
                self.assertEqual(self.bridge.entries["q"]["state"], "queued")
                self.assertEqual(self.bridge.report()["error"], error.code)

    def test_preparation_still_propagates_cancellation_deadlines_and_native_root_failures(self):
        for error in (NativeError("cancelled", "cancelled"), NativeRpcError("timeout", "deadline"),
                      NativeError("wrong-native-turn", "wrong root")):
            with self.subTest(code=error.code), mock.patch.object(self.driver, "snapshot", side_effect=error):
                with self.assertRaises(NativeError) as raised:
                    self.driver.hold_queue()
                self.assertIs(raised.exception, error)

    def test_recorded_drained_event_proves_delivery_without_queued_event(self):
        record = json.loads(FIXTURE.read_text())
        command = next(c["request"]["commandId"] for c in record["commands"]
                       if c["request"]["payload"].get("requestedDelivery") == "guide")
        drained = next(e for e in record["events"] if e["type"] == "turn.steerDrained")
        self.ask()
        self.evidence.session_id = self.driver.session_id = drained["sessionId"]
        self.evidence.turn_id = drained["turnId"]
        self.driver.sent[command] = {"inquiryId": "q", "text": "Status?"}
        self.driver.observe({"method": "session/event", "params": drained}, 1)
        self.assertEqual(self.bridge.entries["q"]["state"], "delivered")

    def test_events_before_ack_deliver_and_pump_reentry_does_not_resend(self):
        self.ask()
        def send(params):
            self.driver.poll()
            self.event("turn.steerQueued", {"inputId": params["commandId"], "pendingInputId": "p", "targetTurnId": "turn"})
            self.assertEqual(self.bridge.entries["q"]["state"], "queued")
            self.event("turn.steerDrained", {"pendingInputIds": ["p"], "targetTurnId": "turn"})
        self.peer.on_send = send
        self.driver.poll()
        self.assertEqual(self.bridge.entries["q"]["state"], "delivered")
        self.assertEqual(len(self.driver.sent), 1)

    def test_a_verified_answer_before_ack_needs_no_delivery_receipt(self):
        self.ask()
        def send(params):
            # RootTurnEvidence verifies the signature before this callback.
            entry = self.bridge.entries["q"]
            self.bridge.record_answer({"inquiryId": "q", "questionSha256": entry["questionSha256"],
                                       "answer": "already answered"}, "answer-call")
            self.event("turn.steerQueued", {"inputId": params["commandId"], "pendingInputId": "p", "targetTurnId": "turn"})
            self.event("turn.steerDrained", {"pendingInputIds": ["p"], "targetTurnId": "turn"})
        self.peer.on_send = send
        self.driver.poll()
        self.assertEqual(self.bridge.entries["q"]["state"], "answered")
        self.assertEqual(self.bridge.entries["q"]["answer"]["text"], "already answered")

    def test_foreign_and_child_events_do_not_prove_delivery(self):
        self.ask(); self.driver.poll()
        command = next(iter(self.driver.sent))
        self.event("turn.steerQueued", {"inputId": command, "pendingInputId": "p", "targetTurnId": "turn"})
        for session, turn, extra in (("other", "turn", {}), ("session", "other", {}),
                                      ("session", "turn", {"source": "child"}),
                                      ("session", "turn", {"targetTurnId": "other"})):
            self.event("turn.steerDrained", {"targetTurnId": "turn", "pendingInputIds": ["p"], **extra}, session=session, turn=turn)
            self.assertEqual(self.bridge.entries["q"]["state"], "queued")

    def test_finish_and_terminal_windows_do_not_send(self):
        self.ask()
        self.evidence.call_id = "finish"
        self.driver.poll()
        self.evidence.call_id = None; self.evidence.finish_failed = True
        self.driver.poll()  # a refused finish must not reopen inquiry admission
        self.evidence.finish_failed = False; self.evidence.completed_ordinal = 1
        self.driver.poll()
        self.assertFalse(self.driver.sent)

    def test_root_ending_before_ack_leaves_the_inflight_item_held_and_sends_no_next_question(self):
        self.ask("first"); self.ask("next")
        def send(params):
            self.peer.queue["items"].append({"queueItemId": "held", "sourceCommandId": params["commandId"]})
            self.evidence.completed_ordinal = 1
        self.peer.on_send = send
        self.driver.poll()
        self.assertEqual([v["inquiryId"] for v in self.driver.sent.values()], ["first"])
        self.assertEqual([v["state"] for v in self.bridge.entries.values()], ["queued", "queued"])
        self.assertTrue(self.driver.clear_held())
        self.assertEqual(self.peer.queue["items"], [])
        self.bridge.close()
        self.assertEqual([v["state"] for v in self.bridge.entries.values()], ["unavailable", "unavailable"])

    def test_hold_and_delete_use_native_snapshot_cas_and_leave_foreign_input(self):
        self.driver.hold_queue()
        self.assertFalse(self.peer.queue["autoDrain"])
        self.ask(); self.driver.poll()
        command = next(iter(self.driver.sent))
        self.peer.queue["items"] = [{"queueItemId": "ours", "sourceCommandId": command},
                                    {"queueItemId": "foreign", "sourceCommandId": "other"}]
        self.assertTrue(self.driver.clear_held())
        self.assertEqual(self.peer.queue["items"], [{"queueItemId": "foreign", "sourceCommandId": "other"}])
        count = len(self.peer.requests)
        self.assertTrue(self.driver.clear_held())
        self.assertEqual(len(self.peer.requests), count)

    def test_a_successful_delete_ack_with_the_item_still_present_fails_cleanup(self):
        self.ask(); self.driver.poll()
        self.peer.queue["items"] = [{"queueItemId": "ours", "sourceCommandId": next(iter(self.driver.sent))}]
        self.peer.lying_delete = True
        self.assertFalse(self.driver.clear_held())
        self.assertEqual(sum(p.get("type") == "deleteQueueItem" for _, p in self.peer.requests), 1)

    def test_unreadable_cleanup_disables_continuation_without_hiding_native_failures(self):
        self.ask(); self.driver.poll()
        for error in (NativeRpcError("native-rpc-error", "rejected"),
                      NativeError("invalid-inquiry-snapshot", "missing subscription")):
            with self.subTest(code=error.code), mock.patch.object(self.driver, "snapshot", side_effect=error):
                self.driver.cleared = None
                self.assertFalse(self.driver.clear_held())
        with mock.patch.object(self.driver, "snapshot", side_effect=NativeError("wrong-native-turn", "second root")):
            self.driver.cleared = None
            with self.assertRaisesRegex(NativeError, "second root"):
                self.driver.clear_held()

    def test_snapshot_requires_an_acknowledged_subscription_identity(self):
        with mock.patch.object(self.peer, "call", return_value={}):
            with self.assertRaisesRegex(NativeError, "acknowledge"):
                self.driver.snapshot()
        self.driver._reading = True
        with self.assertRaisesRegex(NativeError, "subscription identity"):
            self.peer.emit({"wireVersion": 3, "kind": "complete", "deliveryKind": "initial", "topic": self.driver.topic})

    def test_fragmented_snapshot_and_corruption(self):
        self.peer.fragment = True
        self.assertEqual(self.driver.snapshot()["revision"], self.peer.revision)
        self.peer.corrupt = True
        with self.assertRaisesRegex(NativeError, "checksum"):
            self.driver.snapshot()
        self.driver._reading = True
        with self.assertRaisesRegex(NativeError, "bounds"):
            self.peer.emit({"wireVersion": 3, "kind": "fragment", "deliveryKind": "initial", "topic": self.driver.topic,
                "subscriptionId": "sub", "logicalFrameId": "sub-lf-1",
                "fragmentCount": 1, "fragmentIndex": 0, "logicalBytes": MAX_SNAPSHOT_BYTES+1,
                "checksum": {"algorithm": "crc32", "value": "00000000"}})
