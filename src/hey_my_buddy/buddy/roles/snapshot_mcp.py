"""Attempt-local frozen snapshot service for stdio MCP and direct queries."""
from __future__ import annotations

import os
from pathlib import Path
import sys

from ... import locking, private_dirs
from ...json_codec import canonical_json, decode_strict_json
from ...private_files import read_snapshot, read_text
from ...protocol.snapshot_query import QueryError, SnapshotQuery
from ...protocol.tool_evidence import TOOL_EVIDENCE_UNVERIFIED
from ..harnesses.session_receipts import (
    MAX_SNAPSHOT_RESULT_BYTES,
    SNAPSHOT_MCP_SERVER_NAME,
    ReceiptError,
    sign_snapshot_reply,
    snapshot_digest,
    verify_snapshot_reply,
)

_LEDGER_NAME = "snapshot-queries.jsonl"
_MCP_PREFIX = f"mcp__{SNAPSHOT_MCP_SERVER_NAME}__"
_MAX_REQUEST_BYTES = 64 * 1024
_RPC_HEADER_BYTES = 256


def _call_key(identity: dict, call_id: str) -> tuple[str, str]:
    if not isinstance(identity, dict) or not identity or not isinstance(call_id, str) or not call_id:
        raise ValueError("Snapshot call requires a native root identity and call ID")
    return canonical_json(identity), call_id


def _result(envelope: dict, *, ok: bool, direct: bool = False) -> dict:
    text = canonical_json(envelope)
    if direct:
        return {"success": ok, "contentItems": [{"type": "inputText", "text": text}]}
    return {"content": [{"type": "text", "text": text}], "isError": not ok}


class SnapshotService:
    """The only writer of this attempt's signed delivery ledger."""

    def __init__(self, query: SnapshotQuery, invocation_root: Path, configuration: dict):
        self.query = query
        self.configuration = configuration
        # A new attempt receives a new invocation root and a new HMAC key.
        self.ledger = private_dirs.ensure_private_dir(invocation_root) / _LEDGER_NAME
        self.tools = query.tools()
        self.tool_names = {tool["name"] for tool in self.tools}

    @classmethod
    def from_file(cls, config_path: Path):
        configuration = decode_strict_json(read_text(config_path))
        source = configuration["snapshot"]
        document = read_snapshot(Path(source["stateDir"]), source["namespace"], source["owner"],
                                 source["reference"], round=source.get("round"))
        query = SnapshotQuery(document, plane=source["namespace"])
        return cls(query, config_path.parent, configuration)

    def _last_sequence(self, stream) -> int:
        size = stream.seek(0, os.SEEK_END)
        if not size:
            return 0
        # A ledger row is bounded by the page that delivered its coverage.
        stream.seek(max(0, size - _MAX_REQUEST_BYTES))
        tail = stream.read()
        if not tail.endswith(b"\n"):
            raise ValueError("Incomplete snapshot query ledger row")
        return decode_strict_json(tail.rstrip(b"\n").rsplit(b"\n", 1)[-1])["receiptSeq"]

    def call(self, tool_name: str, args: object, *, native_identity: dict | None = None,
             call_id: str | None = None) -> tuple[dict, bool]:
        """Issue one signed result. Direct callers supply their native call key."""
        if tool_name not in self.tool_names:
            raise QueryError("Unknown snapshot query tool")
        key = None
        if native_identity is not None or call_id is not None:
            key = _call_key(native_identity, call_id)
        args_digest = snapshot_digest(args)
        direct = key is not None
        byte_limit = MAX_SNAPSHOT_RESULT_BYTES - (0 if direct else _RPC_HEADER_BYTES)

        def fits(data: dict, coverage: dict) -> bool:
            effective = {} if data.get("items") == [] or data.get("text") == "" else coverage
            preview_row = {"receiptSeq": 9_999_999_999, "toolName": tool_name, "argsDigest": args_digest,
                           "ok": True, "coverage": effective,
                           "directCall": list(key) if key is not None else None}
            preview = sign_snapshot_reply(self.configuration, preview_row, {"data": data})
            return len(canonical_json(_result(preview, ok=True, direct=direct)).encode("utf-8")) <= byte_limit

        try:
            data, coverage = self.query.call(tool_name, args, fits=fits)
            response, ok = {"data": data}, True
        except QueryError as error:
            response, ok, coverage = {"error": str(error)[:256]}, False, {}
        fd = private_dirs.open_regular_fd(self.ledger, os.O_RDWR | os.O_CREAT | os.O_APPEND)
        with os.fdopen(fd, "a+b") as stream:
            locking.lock(stream.fileno())
            row = {"receiptSeq": self._last_sequence(stream) + 1, "toolName": tool_name,
                   "argsDigest": args_digest, "ok": ok, "coverage": coverage,
                   "directCall": list(key) if key is not None else None}
            if ok:
                # An empty page is an attributed call without a delivery claim.
                payload = response["data"]
                if payload.get("items") == [] or payload.get("text") == "":
                    row["coverage"] = {}
            envelope = sign_snapshot_reply(self.configuration, row, response)
            if len(canonical_json(_result(envelope, ok=ok, direct=direct)).encode()) > byte_limit:
                row.update(ok=False, coverage={})
                ok = False
                envelope = sign_snapshot_reply(self.configuration, row, {"error": "Snapshot result exceeded its byte limit"})
                if len(canonical_json(_result(envelope, ok=False, direct=direct)).encode()) > byte_limit:
                    raise ValueError("Snapshot receipt configuration exceeded its byte limit")
            encoded = canonical_json(row).encode() + b"\n"
            if len(encoded) > _MAX_REQUEST_BYTES:
                raise ValueError("Snapshot query ledger row exceeded its byte limit")
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        return envelope, ok

    def mcp_call(self, tool_name: str, args: object) -> dict:
        envelope, ok = self.call(tool_name, args)
        return _result(envelope, ok=ok)

    def direct_call(self, tool_name: str, args: object, *, native_identity: dict, call_id: str) -> dict:
        envelope, ok = self.call(tool_name, args, native_identity=native_identity, call_id=call_id)
        return _result(envelope, ok=ok, direct=True)

    @staticmethod
    def _read_ledger(path: Path) -> dict:
        try:
            fd = private_dirs.open_regular_fd(path, os.O_RDONLY)
        except FileNotFoundError:
            return {}
        with os.fdopen(fd, "rb") as stream:
            locking.lock(stream.fileno(), shared=True)
            rows = {}
            while line := stream.readline(_MAX_REQUEST_BYTES + 1):
                if len(line) > _MAX_REQUEST_BYTES or not line.endswith(b"\n"):
                    raise ValueError("Invalid snapshot query ledger frame")
                row = decode_strict_json(line)
                sequence = row["receiptSeq"]
                if type(sequence) is not int or sequence != len(rows) + 1:
                    raise ValueError("Invalid snapshot query ledger sequence")
                rows[sequence] = row
            return rows

    @classmethod
    def issued_micro_task_coverage(cls, invocation_root: Path) -> list[dict]:
        """Read issued pages for correction before native call evidence settles."""
        rows = cls._read_ledger(invocation_root / _LEDGER_NAME)
        return [row["coverage"] for row in rows.values()
                if row["ok"] is True and row["toolName"] == "query_micro_tasks" and row["coverage"]]

    def verify_calls(self, calls, native_roots) -> tuple[frozenset[tuple[str, str]], list[dict]]:
        """Pair complete mounted-tool calls and ledger rows in both directions.

        Each call supplies nativeIdentity, callId, toolName, arguments, result,
        started, completed and ok, all obtained from native root evidence.
        """
        try:
            roots = {canonical_json(root) for root in native_roots}
            unmatched = self._read_ledger(self.ledger)
            keys = set()
            accepted = {}
            for call in calls:
                key = _call_key(call["nativeIdentity"], call["callId"])
                tool = call["toolName"]
                if (key[0] not in roots or key in keys
                        or call["started"] is not True or call["completed"] is not True):
                    raise ValueError
                # No suffix matching against an arbitrary external server.
                name = tool.removeprefix(_MCP_PREFIX)
                if name not in self.tool_names:
                    raise ValueError
                receipt = verify_snapshot_reply(call["result"], self.configuration, name, ok=call["ok"])
                sequence = receipt["receiptSeq"]
                row = unmatched.pop(sequence)
                if (row["toolName"] != name or row["ok"] is not call["ok"]
                        or row["argsDigest"] != receipt["argsDigest"]
                        or row["argsDigest"] != snapshot_digest(call["arguments"])
                        or snapshot_digest(row["coverage"]) != receipt["coverageDigest"]
                        or row["directCall"] != (None if tool.startswith(_MCP_PREFIX) else list(key))
                        or (not row["ok"] and row["coverage"])):
                    raise ValueError
                keys.add(key)
                if row["ok"] and row["coverage"]:
                    accepted[sequence] = row["coverage"]
            if unmatched:
                raise ValueError
            return frozenset(keys), [accepted[sequence] for sequence in sorted(accepted)]
        except (ValueError, TypeError, KeyError, AttributeError, RecursionError):
            raise ReceiptError(TOOL_EVIDENCE_UNVERIFIED, "Snapshot query ledger and native root calls do not match") from None


def _rpc(service: SnapshotService, request: dict) -> dict | None:
    if not isinstance(request, dict) or request.get("jsonrpc") != "2.0" or not isinstance(request.get("method"), str):
        return {"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "Invalid Request"}}
    if "id" not in request:
        return None
    identifier = request["id"]
    if not (isinstance(identifier, str) and len(canonical_json(identifier).encode()) <= 128
            or type(identifier) is int and abs(identifier) < 2**53):
        return {"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "Invalid Request ID"}}
    response = {"jsonrpc": "2.0", "id": identifier}
    method = request["method"]
    if method == "initialize":
        params = request.get("params", {})
        if not isinstance(params, dict):
            return {**response, "error": {"code": -32602, "message": "Invalid initialize parameters"}}
        version = params.get("protocolVersion")
        result = {"protocolVersion": version if version in ("2024-11-05", "2025-03-26", "2025-06-18") else "2025-06-18",
                  "capabilities": {"tools": {}},
                  "serverInfo": {"name": SNAPSHOT_MCP_SERVER_NAME, "version": "1"}}
    elif method == "ping":
        result = {}
    elif method == "tools/list":
        result = {"tools": service.tools}
    elif method == "tools/call":
        params = request.get("params")
        if not isinstance(params, dict) or not isinstance(params.get("name"), str) or params["name"] not in service.tool_names:
            return {**response, "error": {"code": -32602, "message": "Unknown snapshot tool or invalid parameters"}}
        result = service.mcp_call(params["name"], params.get("arguments", {}))
    else:
        return {**response, "error": {"code": -32601, "message": "Method not found"}}
    return {**response, "result": result}


def serve(service: SnapshotService, source, destination) -> None:
    """Serve newline-framed MCP on binary streams."""
    while line := source.readline(_MAX_REQUEST_BYTES + 1):
        if len(line) > _MAX_REQUEST_BYTES:
            while line and not line.endswith(b"\n"):
                line = source.readline(_MAX_REQUEST_BYTES + 1)
            reply = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Request too large"}}
        else:
            try:
                request = decode_strict_json(line.decode("utf-8"))
                # Reject lone surrogate code points before dispatch.
                canonical_json(request).encode()
            except (ValueError, RecursionError):
                reply = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}}
            else:
                reply = _rpc(service, request)
        if reply is not None:
            destination.write(canonical_json(reply).encode() + b"\n")
            destination.flush()


if __name__ == "__main__":
    serve(SnapshotService.from_file(Path(sys.argv[1])), sys.stdin.buffer, sys.stdout.buffer)
