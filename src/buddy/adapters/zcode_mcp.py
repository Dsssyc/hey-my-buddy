"""Session-private stdio MCP bridge for one ZCode root turn.

Two tools are exposed to exactly one native session: the structured finish tool and
the correlated inquiry reply tool. Both produce signed receipts binding the attempt
identity, so the owning controller imports an outcome or an answer only after it
verified the signature and correlated the native root tool call. Neither tool
contacts or mutates the board; the journal is the controller's private transport
evidence.
"""
from __future__ import annotations

import argparse
import json
import secrets
import sys
from pathlib import Path

from .. import schemas
from .turn_io import MAX_OUTCOME_BYTES, canonical_json, validate_outcome
from .zcode_protocol import decode_json, sign_receipt

MAX_JOURNAL_BYTES = 1024 * 1024

STRINGS = {"type": "array", "maxItems": 32, "items": {"type": "string", "maxLength": 4096}}
OUTCOME_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["disposition", "summary", "remaining", "decisions", "artifacts", "request"],
    "properties": {
        "disposition": {"type": "string", "enum": ["completed", "assistance", "attention"]},
        "summary": {"type": "string", "minLength": 1, "maxLength": 8000},
        "remaining": STRINGS, "decisions": STRINGS,
        "artifacts": {"type": "array", "maxItems": 32, "items": {"anyOf": [{"type": "string"}, {"type": "object", "additionalProperties": True}]}},
        "request": {"description": "Required for every outcome. Use null when disposition is completed; otherwise provide the complete assistance or attention request.", "anyOf": [
            {"type": "null"},
            {"type": "object", "additionalProperties": False,
             "required": ["summary", "attempted", "neededWork", "expectedArtifacts", "acceptance"],
             "properties": {"summary": {"type": "string"}, "attempted": {"type": "string"},
                            "neededWork": {"type": "string"}, "expectedArtifacts": STRINGS,
                            "acceptance": {"type": "string"}, "suggestedProfileId": {"type": "string"}}},
        ]},
    },
}
REPLY_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["inquiryId", "answer"],
    "properties": {
        "inquiryId": {"type": "string", "minLength": 1, "maxLength": 128,
                      "description": "The exact inquiry id from the `buddy inquiry <id>` message."},
        "answer": {"type": "string", "minLength": 1,
                   "description": "The answer for the operator, as plain text."},
    },
}
FINISH_DESCRIPTION = (
    "Conclude the owning Buddy root turn with completed, assistance or attention. Include all six fields: "
    "disposition, summary, remaining, decisions, artifacts, request. For completed, request must be null. "
    "Correct validation errors and retry; stop after one successful receipt, once your work and internal "
    "subagents have settled. This tool does not dispatch other tasks."
)
REPLY_DESCRIPTION = (
    "Answer one operator progress inquiry that was asked in this run. Call it once with the exact inquiryId "
    "from the `buddy inquiry <id>` message and a plain-text answer; the owning controller correlates it with "
    "this root session. It never changes, restarts, extends or cancels the execution and does not dispatch "
    "other tasks."
)


class JournalError(Exception):
    """A bounded refusal reason for one reply attempt."""


def read_journal(path_value: object) -> dict:
    """Read the controller's latest bounded state per inquiry from its journal."""
    if not isinstance(path_value, str) or not path_value:
        raise JournalError("this run has no inquiry journal, so no question can be answered")
    path = Path(path_value)
    try:
        if path.stat().st_size > MAX_JOURNAL_BYTES:
            raise JournalError("the inquiry journal exceeds its bound")
        text = path.read_text(errors="replace")
    except OSError:
        raise JournalError("the inquiry journal is unavailable") from None
    entries: dict[str, dict] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            item = json.loads(line)
        except ValueError:
            continue  # a torn line is ignored, never fatal
        if isinstance(item, dict) and isinstance(item.get("inquiryId"), str):
            entries[item["inquiryId"]] = item
    return entries


def reply_receipt(inquiry_id: object, answer: object, configuration: dict) -> dict:
    """Validate one reply against the committed question and sign its receipt."""
    if not isinstance(inquiry_id, str) or not schemas.INQUIRY_ID_PATTERN.match(inquiry_id):
        raise JournalError("buddy_inquiry_reply requires a bounded inquiryId")
    if not isinstance(answer, str) or not answer.strip():
        raise JournalError("buddy_inquiry_reply requires a nonblank answer")
    size = len(answer.encode("utf-8"))
    if size > schemas.MAX_ANSWER_BYTES:
        raise JournalError("the answer exceeds the inquiry answer bound")
    identity = configuration.get("identity") or {}
    entry = read_journal(configuration.get("journalPath")).get(inquiry_id)
    if entry is None:
        raise JournalError("unknown inquiry id: no question with that id was asked in this run")
    for key, expected, label in (("taskId", identity.get("taskId"), "task"),
                                 ("attemptId", identity.get("attemptId"), "attempt")):
        value = entry.get(key)
        if value not in (None, "") and value != expected:
            raise JournalError(f"the inquiry journal entry belongs to another {label}")
    if entry.get("state") == "answered":
        raise JournalError("that inquiry already has a recorded answer")
    if entry.get("state") != "delivered":
        raise JournalError("that inquiry has no committed delivery in this run, so it cannot be answered")
    receipt = {
        "version": 1, "kind": "inquiry-reply", "identity": identity,
        "inputSha256": configuration.get("inputSha256"),
        "inquiryId": inquiry_id, "answer": answer, "answerBytes": size,
        "receiptId": secrets.token_hex(16),
    }
    receipt["signature"] = sign_receipt(receipt, configuration["key"])
    return receipt


def respond(message: dict, configuration: dict) -> dict | None:
    if "id" not in message:
        return None
    method = message.get("method")
    result: dict = {}
    if method == "initialize":
        result = {"protocolVersion": "2024-11-05", "capabilities": {"tools": {}},
                  "serverInfo": {"name": "buddy-finish-turn", "version": "1"}}
    elif method == "tools/list":
        result = {"tools": [
            {"name": "buddy_finish_turn", "inputSchema": OUTCOME_SCHEMA, "description": FINISH_DESCRIPTION},
            {"name": "buddy_inquiry_reply", "inputSchema": REPLY_SCHEMA, "description": REPLY_DESCRIPTION},
        ]}
    elif method == "tools/call":
        params = message.get("params") or {}
        arguments = params.get("arguments")
        if params.get("name") == "buddy_finish_turn":
            error = validate_outcome(arguments)
            if error:
                result = {"isError": True, "content": [{"type": "text", "text": error}]}
            else:
                # Each call has its own receipt: a child calling this tool must not
                # prevent the owning root from later issuing its independent receipt.
                receipt = {"version": 1, "identity": configuration["identity"],
                           "inputSha256": configuration["inputSha256"], "outcome": arguments,
                           "receiptId": secrets.token_hex(16)}
                receipt["signature"] = sign_receipt(receipt, configuration["key"])
                result = {"content": [{"type": "text", "text": canonical_json(receipt)}]}
        elif params.get("name") == "buddy_inquiry_reply":
            arguments = arguments if isinstance(arguments, dict) else {}
            try:
                receipt = reply_receipt(arguments.get("inquiryId"), arguments.get("answer"), configuration)
            except (JournalError, KeyError, TypeError, ValueError) as error:
                text = str(error) if isinstance(error, JournalError) else "the reply could not be validated"
                result = {"isError": True, "content": [{"type": "text", "text": text}]}
            else:
                result = {"content": [{"type": "text", "text": canonical_json(receipt)}]}
        else:
            result = {"isError": True, "content": [{"type": "text", "text": "unknown finish tool"}]}
    elif method != "ping":
        return {"jsonrpc": "2.0", "id": message["id"], "error": {"code": -32601, "message": "unknown method"}}
    return {"jsonrpc": "2.0", "id": message["id"], "result": result}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    args = parser.parse_args()
    configuration = json.loads(Path(args.config).read_text())
    while raw := sys.stdin.buffer.readline(MAX_OUTCOME_BYTES + 16384):
        try:
            message = decode_json(raw)
            if not isinstance(message, dict):
                return 1
            response = respond(message, configuration)
            if response is not None:
                sys.stdout.write(canonical_json(response) + "\n")
                sys.stdout.flush()
        except (ValueError, TypeError, KeyError, RecursionError):
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
