"""Session-private stdio MCP bridge for one ZCode root turn.

Exactly one tool is exposed to one native session: the structured finish tool. It
produces a signed receipt binding the attempt identity, so the owning controller
imports an outcome only after it verified the signature and correlated the native
root tool call. The tool never contacts or mutates the board.

The ZCode bridge has no inquiry reply tool: the installed native protocol has no
turn-bound in-turn input method, so this adapter observes only and never injects a
question. If the native session asked for an interactive capability this governed
turn cannot grant, the finish tool refuses a ``completed`` outcome and tells the
root to conclude with attention instead, so a refused native request can never be
silently delivered as completed work.
"""
from __future__ import annotations

import argparse
import json
import secrets
import sys
from pathlib import Path

from .turn_io import MAX_OUTCOME_BYTES, canonical_json, validate_outcome
from .zcode_protocol import decode_json, sign_receipt

MAX_ATTENTION_BYTES = 64 * 1024

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
FINISH_DESCRIPTION = (
    "Conclude the owning Buddy root turn with completed, assistance or attention. Include all six fields: "
    "disposition, summary, remaining, decisions, artifacts, request. For completed, request must be null. "
    "Correct validation errors and retry; stop after one successful receipt, once your work and internal "
    "subagents have settled. This tool does not dispatch other tasks."
)

ATTENTION_REFUSAL = (
    "This run refused a native interactive request because no Host approval channel is attached. Conclude the "
    "turn with disposition attention (or assistance) and describe the refused request in the outcome request; "
    "a completed outcome is not accepted while that request is unresolved."
)


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
        ]}
    elif method == "tools/call":
        params = message.get("params") or {}
        arguments = params.get("arguments")
        if params.get("name") == "buddy_finish_turn":
            error = validate_outcome(arguments)
            if error:
                result = {"isError": True, "content": [{"type": "text", "text": error}]}
            elif arguments["disposition"] == "completed" and attention_requests(configuration):
                # A refused native approval must reach the Host as attention, never
                # as silently completed work. The root is still live and can issue
                # the attention receipt in this same turn.
                result = {"isError": True, "content": [{"type": "text", "text": ATTENTION_REFUSAL}]}
            else:
                # Each call has its own receipt: a child calling this tool must not
                # prevent the owning root from later issuing its independent receipt.
                receipt = {"version": 1, "identity": configuration["identity"],
                           "inputSha256": configuration["inputSha256"], "outcome": arguments,
                           "receiptId": secrets.token_hex(16)}
                receipt["signature"] = sign_receipt(receipt, configuration["key"])
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
