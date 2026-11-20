"""The stdio wire and direct query path share one verifiable attempt ledger."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from hey_my_buddy.buddy.harnesses.session_receipts import ReceiptError, SNAPSHOT_MCP_SERVER_NAME
from hey_my_buddy.buddy.roles.snapshot_mcp import SnapshotService, serve
from hey_my_buddy.json_codec import canonical_json, decode_strict_json
from hey_my_buddy.private_files import publish_bytes, publish_snapshot
from hey_my_buddy.protocol.snapshot_query import SnapshotQuery


class SnapshotServiceTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(TemporaryDirectory(prefix="snapshot-service-")))
        self.document = {"task": "冻结正文" * 9000, "domainNodes": [], "buddies": [], "preferences": []}
        self.configuration = {"identity": {"attemptId": "attempt", "generation": 1},
                              "inputSha256": "a" * 64, "key": "b" * 64}
        self.native = {"threadId": "thread", "turnId": "turn"}
        self.service = SnapshotService(SnapshotQuery(self.document), self.root / "invocation", self.configuration)

    def event(self, result, call_id, args, *, ok=True, direct=True):
        tool_name = "query_task" if direct else f"mcp__{SNAPSHOT_MCP_SERVER_NAME}__query_task"
        return {"nativeIdentity": self.native, "callId": call_id, "toolName": tool_name,
                "arguments": args, "result": result, "started": True, "completed": True, "ok": ok}

    def test_stdio_lists_only_mounted_tools_and_reads_verified_private_file(self):
        reference, _ = publish_snapshot(self.root, "routing", "decision", self.document, round=1)
        config = self.service.ledger.parent / "snapshot-mcp.json"
        publish_bytes(config, canonical_json({**self.configuration, "snapshot": {
            "stateDir": str(self.root), "namespace": "routing", "owner": "decision", "round": 1,
            "reference": reference}}).encode())
        service = SnapshotService.from_file(config)
        requests = ({"id": 1, "method": "initialize"}, {"id": 2, "method": "tools/list"},
                    {"method": "notifications/initialized"}, {"id": 3, "method": "ping"},
                    {"id": 4, "method": "tools/call", "params": {"name": "query_task", "arguments": {"limit": 5}}})
        source = BytesIO(b"".join(canonical_json({"jsonrpc": "2.0", **item}).encode() + b"\n" for item in requests))
        destination = BytesIO()
        serve(service, source, destination)
        init, listing, ping, reply = [decode_strict_json(line)["result"] for line in destination.getvalue().splitlines()]
        self.assertEqual(init["serverInfo"]["name"], SNAPSHOT_MCP_SERVER_NAME)
        self.assertNotIn("query_explore", {item["name"] for item in listing["tools"]})
        self.assertEqual(ping, {})
        self.assertEqual(decode_strict_json(reply["content"][0]["text"])["response"]["data"]["text"],
                         self.document["task"][:5])
        keys, coverage = service.verify_calls([self.event(reply, "mcp", {"limit": 5}, direct=False)], [self.native])
        self.assertEqual(keys, {(canonical_json(self.native), "mcp")})
        self.assertEqual(coverage[0]["ranges"][0]["end"], 5)

    def test_stdio_budget_includes_framing_and_refuses_unbounded_request_ids(self):
        request = {"jsonrpc": "2.0", "method": "tools/call", "params": {"name": "query_task", "arguments": {}}}
        source = BytesIO(b"".join(canonical_json({**request, "id": identifier}).encode() + b"\n"
                                  for identifier in ("a" * 300, "b" * 126)))
        destination = BytesIO()
        serve(self.service, source, destination)
        invalid, line = destination.getvalue().splitlines()
        self.assertEqual(decode_strict_json(invalid)["error"]["code"], -32600)
        self.assertLessEqual(len(line), 16 * 1024)
        reply = decode_strict_json(line)["result"]
        self.assertFalse(reply["isError"])
        _, coverage = self.service.verify_calls([self.event(reply, "valid", {}, direct=False)], [self.native])
        self.assertEqual(len(coverage), 1)

    def test_failed_call_is_attributed_without_coverage_and_masquerade_is_rejected(self):
        good = self.service.direct_call("query_task", {}, native_identity=self.native, call_id="ok")
        args = {"offset": len(self.document["task"]) + 1}
        failed = self.service.direct_call("query_task", args, native_identity=self.native, call_id="error")
        good_call = self.event(good, "ok", {})
        failed_call = self.event(failed, "error", args, ok=False)
        keys, coverage = self.service.verify_calls([good_call, failed_call], [self.native])
        self.assertEqual((len(keys), len(coverage)), (2, 1))
        for bad_calls in ([good_call], [good_call, failed_call, failed_call],
                          [good_call, {**failed_call, "ok": True}],
                          [good_call, {**failed_call, "result": {**failed, "success": True}}],
                          [good_call, {**failed_call, "completed": False}],
                          [good_call, {**failed_call, "callId": "replayed"}],
                          [good_call, {**failed_call, "arguments": {"offset": 0}}],
                          [good_call, {**failed_call, "nativeIdentity": {"threadId": "foreign"}}]):
            with self.subTest(calls=len(bad_calls)), self.assertRaises(ReceiptError):
                self.service.verify_calls(bad_calls, [self.native])

    def test_concurrent_direct_calls_have_unique_sequences_and_bounded_wire_pages(self):
        def invoke(index):
            args = {"offset": index}
            call_id = str(index)
            reply = self.service.direct_call("query_task", args, native_identity=self.native, call_id=call_id)
            return reply, self.event(reply, call_id, args)
        with ThreadPoolExecutor(max_workers=4) as executor:
            replies, calls = zip(*executor.map(invoke, range(8)))
        sequences = set()
        for reply in replies:
            self.assertTrue(reply["success"])
            self.assertLessEqual(len(canonical_json(reply).encode()), 16 * 1024)
            sequences.add(decode_strict_json(reply["contentItems"][0]["text"])["receipt"]["receiptSeq"])
        self.assertEqual(sequences, set(range(1, 9)))
        keys, coverage = self.service.verify_calls(calls, [self.native])
        self.assertEqual((len(keys), len(coverage)), (8, 8))
