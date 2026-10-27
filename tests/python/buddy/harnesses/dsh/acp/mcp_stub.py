"""Minimal stdio MCP server the fake agent mounts to prove the mounting path.

Answers initialize and tools/list with one declared tool, then exits on stdin
EOF. It runs under the private HOME the launch wrapper forces, inherited from
the fake agent's own environment.
"""
from __future__ import annotations

import json
import sys


def main() -> int:
    for line in sys.stdin:
        text = line.strip()
        if not text:
            continue
        try:
            message = json.loads(text)
        except ValueError:
            continue
        if "method" not in message:
            continue  # a response to nothing we sent
        if "id" not in message:
            continue  # notifications are never answered
        if message["method"] == "initialize":
            print(json.dumps({"jsonrpc": "2.0", "id": message["id"], "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}}, "serverInfo": {"name": "fake-mcp", "version": "0.0.1"}}}),
                flush=True)
        elif message["method"] == "tools/list":
            print(json.dumps({"jsonrpc": "2.0", "id": message["id"], "result": {"tools": [
                {"name": "deliver_outcome", "description": "test completion tool",
                 "inputSchema": {"type": "object", "properties": {}}}]}}), flush=True)
        else:
            print(json.dumps({"jsonrpc": "2.0", "id": message["id"],
                              "error": {"code": -32601, "message": "Method not found"}}), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
