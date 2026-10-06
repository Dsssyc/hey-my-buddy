"""Session-private stdio MCP carrier for one ZCode root turn.

This module is only the native carrier of the Worker role's session tools: it
frames the session's stdio JSON-RPC (``initialize``, ``tools/list``,
``tools/call``, ``ping``) and hands every tool call to
:func:`hey_my_buddy.buddy.roles.worker_services.call_session_tool`, which owns
the six-field finish contract, the attention-outstanding and inquiry-pending
completion refusals, the journal replay and the signed receipts and refusal
envelopes. Nothing here decides an outcome, reads the board or mutates state,
and the journal stays read-only from this process.

The native wrapper may surface an ``isError`` tool response as a *successful*
tool result, so the owning controller imports an outcome only after it
independently verified the signature and correlated the native root tool call
(``zcode_protocol.verify_receipt`` / ``verify_tool_refusal``); the role's
refusal envelope keeps that path recoverable for a corrected same-turn retry.
A success receipt, the native root completion, the session close and the real
shutdown evidence remain the separate facts they were.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from ....json_codec import canonical_json, decode_strict_json
from ..session_receipts import MAX_OUTCOME_BYTES
from ...roles import worker_services


def respond(message: dict, configuration: dict) -> dict | None:
    if "id" not in message:
        return None
    method = message.get("method")
    result: dict = {}
    if method == "initialize":
        result = {"protocolVersion": "2024-11-05", "capabilities": {"tools": {}},
                  "serverInfo": {"name": "buddy-finish-turn", "version": "1"}}
    elif method == "tools/list":
        result = {"tools": worker_services.session_tools()}
    elif method == "tools/call":
        params = message.get("params") or {}
        result = worker_services.call_session_tool(params.get("name"), params.get("arguments"), configuration)
        if result is None:
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
            message = decode_strict_json(raw)
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
