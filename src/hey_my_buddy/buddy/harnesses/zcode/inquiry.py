"""In-turn questions and the held queue of one private ZCode root session."""
from __future__ import annotations

import base64
import json
import time
import uuid
import zlib

from ....json_codec import decode_strict_json
from .protocol import NativeError, NativeRpcError

# CLI 0.17.0's v4 logical frame limits, including fragmented history snapshots.
MAX_SNAPSHOT_BYTES = 16 * 1024 * 1024
MAX_FRAGMENTS = 1024
_CHILD_FIELDS = ("source", "parentToolCallId", "childSessionId", "childToolCallId", "agentId", "background")


class NativeInquiry:
    def __init__(self, connection, evidence, bridge, answer_tool, session=None):
        self.connection, self.evidence, self.bridge = connection, evidence, bridge
        self.answer_tool, self.session = answer_tool, session
        self.session_id = evidence.session_id
        self.topic = "conversation/" + self.session_id
        self.client_id = "hey-my-buddy-" + str(uuid.uuid4())
        self.sent: dict[str, dict] = {}
        self.pending: dict[str, str] = {}
        self.recorded: set[str] = set()
        self._polling = self._reading = False
        self._snapshots = {}
        self._fragments = None
        self.cleared = None
        self.queue_held = False

    def command(self, kind, payload, snapshot=None, *, command_id=None):
        envelope = {"commandId": command_id or str(uuid.uuid4()), "clientId": self.client_id,
                    "sessionId": self.session_id, "type": kind, "payload": payload,
                    "issuedAt": int(time.time() * 1000)}
        if snapshot is not None:
            envelope.update(baseRevision=snapshot["revision"], baseLogEpoch=snapshot["logEpoch"])
        return self.connection.call("v4/command", envelope)

    def _wire(self, params):
        if not self._reading or params.get("topic") != self.topic or params.get("deliveryKind") != "initial":
            return
        if params.get("wireVersion") != 3:
            raise NativeError("invalid-inquiry-snapshot", "unrecognized ZCode queue wire version")
        subscription = params.get("subscriptionId")
        if not isinstance(subscription, str) or not subscription:
            raise NativeError("invalid-inquiry-snapshot", "ZCode queue frame has no subscription identity")
        kind = params.get("kind")
        if kind == "complete":
            frame = params.get("frame")
        elif kind == "fragment":
            count, size, index = (params.get(key) for key in ("fragmentCount", "logicalBytes", "fragmentIndex"))
            checksum = params.get("checksum")
            if (type(count) is not int or not 1 <= count <= MAX_FRAGMENTS
                    or type(size) is not int or not 1 <= size <= MAX_SNAPSHOT_BYTES
                    or type(index) is not int or not 0 <= index < count
                    or not isinstance(checksum, dict) or checksum.get("algorithm") != "crc32"):
                raise NativeError("invalid-inquiry-snapshot", "invalid ZCode queue fragment bounds")
            key = (params.get("subscriptionId"), params.get("logicalFrameId"), count, size, checksum.get("value"))
            if self._fragments is None:
                self._fragments = (key, {}, 0)
            previous, parts, total = self._fragments
            if previous != key or index in parts:
                raise NativeError("invalid-inquiry-snapshot", "conflicting ZCode queue fragments")
            try:
                part = base64.b64decode(params["dataBase64"], validate=True)
            except (ValueError, TypeError, KeyError):
                raise NativeError("invalid-inquiry-snapshot", "invalid ZCode queue fragment encoding") from None
            total += len(part)
            if total > size:
                raise NativeError("invalid-inquiry-snapshot", "ZCode queue fragments exceed their declared size")
            parts[index] = part
            self._fragments = (key, parts, total)
            if len(parts) != count:
                return
            raw = b"".join(parts[i] for i in range(count))
            if len(raw) != size or f"{zlib.crc32(raw):08x}" != checksum.get("value"):
                raise NativeError("invalid-inquiry-snapshot", "ZCode queue fragment checksum or size mismatch")
            try:
                frame = decode_strict_json(raw)
            except (ValueError, RecursionError):
                raise NativeError("invalid-inquiry-snapshot", "invalid ZCode queue snapshot JSON") from None
            self._fragments = None
        else:
            raise NativeError("invalid-inquiry-snapshot", "unrecognized ZCode queue wire kind")
        if (not isinstance(frame, dict) or frame.get("topic") != self.topic
                or frame.get("subscriptionId") != params.get("subscriptionId")):
            raise NativeError("invalid-inquiry-snapshot", "ZCode queue frame belongs to another subscription")
        payload = frame.get("payload")
        snapshot = payload.get("snapshot") if isinstance(payload, dict) and payload.get("kind") == "snapshot" else None
        if not isinstance(snapshot, dict) or snapshot.get("sessionId") != self.session_id:
            raise NativeError("invalid-inquiry-snapshot", "ZCode queue snapshot belongs to another session")
        queue = snapshot.get("queue")
        if (type(snapshot.get("revision")) is not int or snapshot["revision"] < 0
                or not isinstance(snapshot.get("logEpoch"), str) or not snapshot["logEpoch"]
                or not isinstance(queue, dict) or type(queue.get("autoDrain")) is not bool
                or not isinstance(queue.get("items"), list)
                or any(not isinstance(item, dict) or any(not isinstance(item.get(k), str) or not item[k]
                    for k in ("queueItemId", "sourceCommandId")) for item in queue["items"])):
            raise NativeError("invalid-inquiry-snapshot", "invalid ZCode queue snapshot fields")
        self._snapshots[params["subscriptionId"]] = {key: snapshot[key] for key in ("revision", "logEpoch", "queue")}

    def snapshot(self):
        self._reading = True
        try:
            response = self.connection.call("v4/conversation/subscribe", {"topic": self.topic,
                "connectionId": self.client_id, "clientMode": "web-remote-replayable"})
            ack = response.get("ack")
            sub = ack.get("subscriptionId") if isinstance(ack, dict) else None
            if not isinstance(sub, str) or not sub:
                raise NativeError("invalid-inquiry-snapshot", "ZCode did not acknowledge the queue subscription")
            while sub not in self._snapshots:
                self.connection.pump()
            result = self._snapshots.pop(sub)
            self.connection.call("v4/conversation/unsubscribe", {"topic": self.topic,
                "connectionId": self.client_id, "subscriptionId": sub})
            return result
        finally:
            self._reading = False
            self._fragments = None
            self._snapshots.clear()

    def hold_queue(self):
        self.queue_held = False
        try:
            snapshot = self.snapshot()
            result = self.command("setAutoDrain", {"autoDrain": False}, snapshot)
            if result.get("status") not in ("accepted", "noop") or self.snapshot()["queue"]["autoDrain"] is not False:
                raise NativeError("native-queue-hold-failed", "ZCode did not hold this private session's queue")
        except NativeError as error:
            # A rejected or changed optional v4 surface disables inquiries,
            # while the Worker's original input remains runnable.
            if error.code not in ("native-queue-hold-failed", "invalid-inquiry-snapshot", "native-rpc-error"):
                raise
            if self.bridge is not None:
                self.bridge.error = error.code
        else:
            self.queue_held = True

    def _record_input(self, command_id):
        if command_id not in self.recorded:
            self.recorded.add(command_id)
            if self.session is not None:
                self.session.record.project(self.session.record_input, self.session_id,
                    self.evidence.turn_id, command_id, self.sent[command_id]["text"])

    def observe(self, message, ordinal):
        params = message.get("params")
        if not isinstance(params, dict):
            return
        if message.get("method") == "v4/conversation/frame":
            self._wire(params)
            return
        if (message.get("method") != "session/event" or params.get("sessionId") != self.session_id
                or not self.evidence.turn_id or params.get("turnId") != self.evidence.turn_id):
            return
        data = params.get("payload")
        if (not isinstance(data, dict) or data.get("targetTurnId") != self.evidence.turn_id
                or any(params.get(k) or data.get(k) for k in _CHILD_FIELDS)):
            return
        if params.get("type") == "turn.steerQueued":
            command_id, pending = data.get("inputId"), data.get("pendingInputId")
            if isinstance(command_id, str) and command_id in self.sent and isinstance(pending, str) and pending:
                self.pending[pending] = command_id
                self._record_input(command_id)
        elif params.get("type") == "turn.steerDrained":
            pending_ids = data.get("pendingInputIds")
            if not isinstance(pending_ids, list) or not all(isinstance(v, str) for v in pending_ids):
                raise NativeError("invalid-inquiry-evidence", "invalid ZCode drained input identities")
            inputs = data.get("drainedInputs", [])
            if not isinstance(inputs, list) or not all(isinstance(item, dict) for item in inputs):
                raise NativeError("invalid-inquiry-evidence", "invalid ZCode drained input records")
            for item in inputs:
                intent = item.get("intent")
                command_id = intent.get("sourceCommandId") if isinstance(intent, dict) else None
                if isinstance(command_id, str) and command_id in self.sent and item.get("pendingInputId") in pending_ids:
                    self.pending[item["pendingInputId"]] = command_id
            for pending in pending_ids:
                command_id = self.pending.get(pending)
                if command_id is not None:
                    self._record_input(command_id)
                    self.bridge.deliver_native(self.sent[command_id]["inquiryId"], {
                        "sessionId": self.session_id, "turnId": self.evidence.turn_id,
                        "method": "v4/command", "commandId": command_id,
                        "pendingInputId": pending, "eventSeq": params["seq"]})

    def poll(self):
        if (self.bridge is None or not self.queue_held or self._polling or self._reading or not self.evidence.turn_id
                or self.evidence.call_id is not None or self.evidence.finish_failed or self.evidence.completed_ordinal):
            return
        if self.bridge.error and self.bridge.error.startswith("journal-unavailable"):
            raise NativeError("inquiry-journal-unavailable", self.bridge.error)
        self._polling = True
        try:
            attempted = {item["inquiryId"] for item in self.sent.values()}
            for entry in self.bridge.pending_inquiries():
                if self.evidence.call_id is not None or self.evidence.finish_failed or self.evidence.completed_ordinal:
                    break
                inquiry_id = entry["inquiryId"]
                if inquiry_id in attempted:
                    continue
                command_id = str(uuid.uuid4())
                text = (f"Host inquiry for this running root turn. Answer with {self.answer_tool}, "
                    "using this exact inquiryId, then continue the assigned work.\n"
                    + json.dumps({"inquiryId": inquiry_id, "question": entry["question"]}, ensure_ascii=False))
                self.sent[command_id] = {"inquiryId": inquiry_id, "text": text}
                try:
                    self.command("sendText", {"text": text, "requestedDelivery": "guide"}, command_id=command_id)
                except NativeRpcError as error:
                    if error.code != "native-rpc-error":
                        raise
        finally:
            self._polling = False

    def clear_held(self):
        """Delete only our remaining inputs; false forbids native continuation."""
        if self.cleared is not None:
            return self.cleared
        self.cleared = not self.sent
        if self.cleared:
            return True
        try:
            snapshot = self.snapshot()
            items = [item for item in snapshot["queue"]["items"] if item["sourceCommandId"] in self.sent]
            for item in items:
                self.command("deleteQueueItem", {"queueItemId": item["queueItemId"]}, snapshot)
                snapshot = self.snapshot()
                if any(v["queueItemId"] == item["queueItemId"] for v in snapshot["queue"]["items"]):
                    return False
            self.cleared = True
            return True
        except NativeRpcError:
            return False
        except NativeError as error:
            if error.code not in ("invalid-inquiry-snapshot", "transport-error", "cancelled", "timeout"):
                raise
            return False
