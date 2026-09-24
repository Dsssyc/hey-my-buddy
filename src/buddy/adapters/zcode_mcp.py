"""Session-private stdio MCP finish tool. It never contacts or mutates the board."""
from __future__ import annotations

import argparse
import json
import secrets
import sys
from pathlib import Path

from .turn_io import MAX_OUTCOME_BYTES, canonical_json, validate_outcome
from .zcode_protocol import decode_json, sign_receipt

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


def respond(message: dict, configuration: dict) -> dict | None:
    if "id" not in message:
        return None
    method = message.get("method")
    result: dict = {}
    if method == "initialize":
        result = {"protocolVersion": "2024-11-05", "capabilities": {"tools": {}},
                  "serverInfo": {"name": "buddy-finish-turn", "version": "1"}}
    elif method == "tools/list":
        result = {"tools": [{"name": "buddy_finish_turn", "inputSchema": OUTCOME_SCHEMA,
                            "description": "Conclude the owning Buddy root turn with completed, assistance or attention. Include all six fields: disposition, summary, remaining, decisions, artifacts, request. For completed, request must be null. Correct validation errors and retry; stop after one successful receipt, once your work and internal subagents have settled. This tool does not dispatch other tasks."}]}
    elif method == "tools/call":
        params = message.get("params") or {}
        outcome = params.get("arguments")
        error = validate_outcome(outcome)
        if params.get("name") != "buddy_finish_turn" or error:
            result = {"isError": True, "content": [{"type": "text", "text": error or "unknown finish tool"}]}
        else:
            # Each call has its own receipt: a child calling this tool must not
            # prevent the owning root from later issuing its independent receipt.
            receipt = {"version": 1, "identity": configuration["identity"],
                       "inputSha256": configuration["inputSha256"], "outcome": outcome,
                       "receiptId": secrets.token_hex(16)}
            receipt["signature"] = sign_receipt(receipt, configuration["key"])
            result = {"content": [{"type": "text", "text": canonical_json(receipt)}]}
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
