"""Native checkpoint validation for stopped execution receipts."""
from __future__ import annotations

import hashlib

from ..json_codec import canonical_json
from .usage import MAX_ASSISTANT_MESSAGE_BYTES


def input_hash(value: dict) -> str:
    return hashlib.sha256(canonical_json(value).encode()).hexdigest()


def validated_checkpoint(payload: dict, turn_input: dict) -> dict | None:
    """Read only a stopped, exact-attempt native observation from its receipt."""
    value = payload.get("nativeCheckpoint")
    if not isinstance(value, dict) or value.get("version") != 1:
        return None
    expected = {key: turn_input[key] for key in ("taskId", "attemptId", "generation", "turnId")}
    expected["inputSha256"] = input_hash(turn_input)
    if any(type(value.get(key)) is not type(item) or value.get(key) != item for key, item in expected.items()):
        return None
    process = payload.get("processState")
    if (not isinstance(process, dict) or process.get("shutdownConfirmed") is not True
            or value.get("nativeTurnStarted") is not True
            or value.get("nativeTurnStatus") not in ("completed", "failed", "interrupted", "incomplete")
            or type(value.get("bindingSaved")) is not bool
            or type(value.get("eventSeq")) is not int or value["eventSeq"] < 2):
        return None
    for key in ("sessionId", "nativeTurnId"):
        if not isinstance(value.get(key), str) or not value[key] or value[key] != payload.get(key):
            return None
    message = value.get("lastAssistantMessage")
    if message is not None:
        if (not isinstance(message, dict) or not isinstance(message.get("itemId"), str) or not message["itemId"]
                or not isinstance(message.get("text"), str) or type(message.get("truncated")) is not bool
                or type(message.get("sourceBytes")) is not int
                or not isinstance(message.get("sha256"), str) or len(message["sha256"]) != 64
                or len(canonical_json(message["text"]).encode()) > MAX_ASSISTANT_MESSAGE_BYTES):
            return None
        raw = message["text"].encode()
        if message["sourceBytes"] < len(raw) or (not message["truncated"] and (
                message["sourceBytes"] != len(raw) or hashlib.sha256(raw).hexdigest() != message["sha256"])):
            return None
    return value


def checkpoint_resumable(payload: dict, checkpoint: dict) -> bool:
    process = payload.get("processState")
    return (checkpoint.get("bindingSaved") is True and isinstance(process, dict)
            and process.get("shutdownConfirmed") is True
            and (checkpoint.get("nativeTurnStatus") == "interrupted" or
                 checkpoint.get("nativeTurnStatus") == "completed"
                 and type(process.get("nativeExitCode")) is int and process["nativeExitCode"] == 0))
