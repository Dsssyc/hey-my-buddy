"""Session-private stdio MCP bridge for one ZCode root turn.

Three tools are exposed to one native session: the structured finish tool plus
the cooperative inquiry channel. The finish tool produces a signed receipt
binding the attempt identity, so the owning controller imports an outcome only
after it verified the signature and correlated the native root tool call.
``buddy_checkpoint`` exposes the Host questions currently queued for this turn
and ``buddy_answer_inquiry`` returns a signed tentative answer receipt; the
controller, never this handler, turns either into journal state after the root
turn's own tool evidence verified them. No tool here contacts or mutates the
board, and the journal is read-only from this process.

If the native session asked for an interactive capability this governed turn
cannot grant, the finish tool refuses a ``completed`` outcome and tells the root
to conclude with attention instead, so a refused native request can never be
silently delivered as completed work. A queued and still-unanswered Host
question blocks a ``completed`` outcome the same way until it is answered or
explicitly withdrawn.
"""
from __future__ import annotations

import argparse
import json
import secrets
import sys
from pathlib import Path

from .turn_io import MAX_OUTCOME_BYTES, canonical_json, validate_outcome
from .zcode_protocol import (MAX_ANSWER_BYTES, MAX_INQUIRIES, MAX_INQUIRY_ID_BYTES,
                             decode_json, sign_receipt)

MAX_ATTENTION_BYTES = 64 * 1024
MAX_JOURNAL_BYTES = 1024 * 1024
MAX_PENDING_IN_FINISH_REFUSAL = 4

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
ANSWER_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["inquiryId", "answer"],
    "properties": {
        "inquiryId": {"type": "string", "minLength": 1, "maxLength": 128,
                      "description": "The inquiryId exactly as exposed by buddy_checkpoint."},
        "answer": {"type": "string", "minLength": 1, "maxLength": 10000,
                   "description": "The bounded answer text for that inquiry; at most 4000 UTF-8 bytes are accepted."},
    },
}
CHECKPOINT_SCHEMA = {"type": "object", "additionalProperties": False, "properties": {}, "required": []}
FINISH_DESCRIPTION = (
    "Conclude the owning Buddy root turn with completed, assistance or attention. Include all six fields: "
    "disposition, summary, remaining, decisions, artifacts, request. For completed, request must be null. "
    "Correct validation errors and retry; stop after one successful receipt, once your work and internal "
    "subagents have settled. A completed outcome is refused while a Host inquiry is still unanswered. "
    "This tool does not dispatch other tasks."
)
CHECKPOINT_DESCRIPTION = (
    "Pick up queued Host inquiries for this governed turn. Returns a signed receipt listing every question "
    "still awaiting an answer (nothing pending returns an empty list). Call it at natural work milestones and "
    "again just before buddy_finish_turn; it is never required on a timer. Answer each listed question with "
    "buddy_answer_inquiry using its exact inquiryId."
)
ANSWER_DESCRIPTION = (
    "Answer one Host inquiry exposed by buddy_checkpoint, using its exact inquiryId and a bounded answer. "
    "Returns a signed tentative receipt; the recorded answer only becomes authoritative after the owning "
    "controller verifies it against this root turn's own tool evidence. A first accepted answer for an "
    "inquiry cannot be replaced."
)
ATTENTION_REFUSAL = (
    "This run refused a native interactive request because no Host approval channel is attached. Conclude the "
    "turn with disposition attention (or assistance) and describe the refused request in the outcome request; "
    "a completed outcome is not accepted while that request is unresolved."
)
INQUIRY_CHANNEL_ABSENT = "this turn has no mounted inquiry channel"


def attention_requests(configuration: dict) -> int:
    """Count refused native interactive requests, metadata only and never fatal."""
    path_value = configuration.get("attentionPath")
    if not isinstance(path_value, str) or not path_value:
        return 0
    try:
        path = Path(path_value)
        if path.stat().st_size > MAX_ATTENTION_BYTES:
            return 1  # an oversized record still means an unresolved request
        value = json.loads(path.read_text())
    except (OSError, ValueError):
        return 0
    requests = value.get("requests") if isinstance(value, dict) else None
    return len(requests) if isinstance(requests, list) else 0


def read_inquiry_entries(configuration: dict) -> dict[str, dict] | None:
    """Read-only merged view of the bridge journal for this attempt's turn.

    The controller is the only writer; this merge replays its linear records so
    a committed question hash, delivery or answer survives every later record.
    Only entries bound to this configuration's identity are returned, so a
    stale journal from another attempt or turn can never surface here.
    """
    journal = configuration.get("inquiryJournalPath")
    identity = configuration.get("identity")
    if not isinstance(journal, str) or not journal or not isinstance(identity, dict):
        return None
    try:
        path = Path(journal)
        if path.stat().st_size > MAX_JOURNAL_BYTES:
            return {}
        text = path.read_text(errors="replace")
    except OSError:
        return {}
    entries: dict[str, dict] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except ValueError:
            continue  # a torn line is ignored, never fatal
        if not isinstance(record, dict) or not isinstance(record.get("inquiryId"), str):
            continue
        if any(record.get(key) not in (None, identity.get(key)) for key in ("taskId", "attemptId", "generation", "turnId")):
            continue
        entries[record["inquiryId"]] = {**entries.get(record["inquiryId"], {}), **record}
    return entries


def pending_inquiries(configuration: dict) -> list[dict] | None:
    """The still-answerable Host questions for this turn, in journal order."""
    entries = read_inquiry_entries(configuration)
    if entries is None:
        return None
    pending = []
    for entry in entries.values():
        if entry.get("state") not in ("queued", "delivered") or not isinstance(entry.get("question"), str) or not entry["question"]:
            continue
        item = {"inquiryId": entry["inquiryId"], "question": entry["question"],
                "questionSha256": entry.get("questionSha256"), "state": entry.get("state"),
                "askedAt": entry.get("askedAt")}
        if isinstance(entry.get("deliveredAt"), str):
            item["deliveredAt"] = entry["deliveredAt"]
        pending.append(item)
    return pending[:MAX_INQUIRIES]


def inquiry_refusal(pending: list[dict]) -> str | None:
    """The bounded finish refusal naming the pending questions."""
    if not pending:
        return None
    listed = [
        f"[{item['inquiryId']}] {item['question']}"
        for item in pending[:MAX_PENDING_IN_FINISH_REFUSAL] if isinstance(item.get("question"), str)
    ]
    more = f" (+{len(pending) - len(listed)} more)" if len(pending) > len(listed) else ""
    return (
        "This turn cannot be completed while Host inquiries are still unanswered: "
        + " | ".join(listed)
        + f"{more}. Call buddy_checkpoint to pick them up, answer each with buddy_answer_inquiry, then retry "
          "the finish; a withdrawn or explicitly unavailable question no longer blocks completion."
    )[:MAX_OUTCOME_BYTES - 1]


def _signed(payload: dict, configuration: dict) -> str:
    receipt = {**payload, "receiptId": secrets.token_hex(16)}
    receipt["signature"] = sign_receipt(receipt, configuration["key"])
    return canonical_json(receipt)


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
            {"name": "buddy_checkpoint", "inputSchema": CHECKPOINT_SCHEMA, "description": CHECKPOINT_DESCRIPTION},
            {"name": "buddy_answer_inquiry", "inputSchema": ANSWER_SCHEMA, "description": ANSWER_DESCRIPTION},
            {"name": "buddy_finish_turn", "inputSchema": OUTCOME_SCHEMA, "description": FINISH_DESCRIPTION},
        ]}
    elif method == "tools/call":
        params = message.get("params") or {}
        arguments = params.get("arguments")
        name = params.get("name")
        if name == "buddy_checkpoint":
            pending = pending_inquiries(configuration)
            if pending is None:
                result = {"isError": True, "content": [{"type": "text", "text": INQUIRY_CHANNEL_ABSENT}]}
            else:
                receipt = _signed({"version": 1, "kind": "inquiry-checkpoint", "identity": configuration["identity"],
                                   "inquiries": pending}, configuration)
                result = {"content": [{"type": "text", "text": receipt}]}
        elif name == "buddy_answer_inquiry":
            result = _answer_inquiry(arguments, configuration)
        elif name == "buddy_finish_turn":
            error = validate_outcome(arguments)
            if error:
                result = {"isError": True, "content": [{"type": "text", "text": error}]}
            elif arguments["disposition"] == "completed" and attention_requests(configuration):
                # A refused native approval must reach the Host as attention, never
                # as silently completed work. The root is still live and can issue
                # the attention receipt in this same turn.
                result = {"isError": True, "content": [{"type": "text", "text": ATTENTION_REFUSAL}]}
            elif arguments["disposition"] == "completed":
                refusal = inquiry_refusal(pending_inquiries(configuration) or [])
                if refusal:
                    # Same contract as a refused native approval: the root is still
                    # live, so it can checkpoint, answer and retry in this turn.
                    result = {"isError": True, "content": [{"type": "text", "text": refusal}]}
                else:
                    result = _finish_receipt(arguments, configuration)
            else:
                result = _finish_receipt(arguments, configuration)
        else:
            result = {"isError": True, "content": [{"type": "text", "text": "unknown finish tool"}]}
    elif method != "ping":
        return {"jsonrpc": "2.0", "id": message["id"], "error": {"code": -32601, "message": "unknown method"}}
    return {"jsonrpc": "2.0", "id": message["id"], "result": result}


def _finish_receipt(arguments: dict, configuration: dict) -> dict:
    # Each call has its own receipt: a child calling this tool must not
    # prevent the owning root from later issuing its independent receipt.
    receipt = {"version": 1, "identity": configuration["identity"],
               "inputSha256": configuration["inputSha256"], "outcome": arguments,
               "receiptId": secrets.token_hex(16)}
    receipt["signature"] = sign_receipt(receipt, configuration["key"])
    return {"content": [{"type": "text", "text": canonical_json(receipt)}]}


def _answer_inquiry(arguments: object, configuration: dict) -> dict:
    """Validate one answer against the journal view and return a signed receipt.

    This only mints the tentative receipt: the controller verifies the native
    root tool evidence and the receipt binding before the journal records the
    answer, so this handler never writes inquiry state itself.
    """
    def failure(text: str) -> dict:
        return {"isError": True, "content": [{"type": "text", "text": text}]}
    if not isinstance(arguments, dict) or set(arguments) != {"inquiryId", "answer"}:
        return failure("buddy_answer_inquiry requires exactly inquiryId and answer")
    inquiry_id, answer = arguments["inquiryId"], arguments["answer"]
    if not isinstance(inquiry_id, str) or not inquiry_id or len(inquiry_id.encode()) > MAX_INQUIRY_ID_BYTES:
        return failure("buddy_answer_inquiry requires a bounded nonempty inquiryId")
    if not isinstance(answer, str) or not answer.strip():
        return failure("buddy_answer_inquiry requires a nonblank answer")
    if len(answer.encode()) > MAX_ANSWER_BYTES:
        return failure(f"the answer exceeds its {MAX_ANSWER_BYTES}-byte UTF-8 bound")
    entries = read_inquiry_entries(configuration)
    if entries is None:
        return failure(INQUIRY_CHANNEL_ABSENT)
    entry = entries.get(inquiry_id)
    if entry is None:
        return failure(f"unknown inquiryId {inquiry_id}: no Host question with that id was queued in this turn")
    if entry.get("state") == "answered":
        return failure(f"inquiry {inquiry_id} already has its recorded answer; it cannot be replaced")
    if entry.get("state") not in ("queued", "delivered"):
        return failure(f"inquiry {inquiry_id} is {entry.get('state')} and can no longer be answered")
    receipt = _signed({"version": 1, "kind": "inquiry-answer", "identity": configuration["identity"],
                       "inquiryId": inquiry_id, "questionSha256": entry.get("questionSha256"),
                       "answer": answer}, configuration)
    return {"content": [{"type": "text", "text": receipt}]}


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
