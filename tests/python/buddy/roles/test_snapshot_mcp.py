"""The stdio wire and direct query path share one verifiable attempt ledger."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import ANY, patch

from hey_my_buddy import locking
from hey_my_buddy.buddy.harnesses.snapshot_mount import (
    mounted_micro_task_coverage, mounted_snapshot, prepare_snapshot_mount,
)
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

    def test_running_coverage_reads_only_issued_micro_task_ranges_under_shared_lock(self):
        packet = {
            "domainNodes": [], "classificationRunIds": ["run"], "kinds": [], "comparisonStatistics": [],
            "microTasks": [{"runId": "run", "title": "Task", "task": "Read me",
                            "project": None, "classification": None,
                            "result": {"state": "accepted", "continuationRounds": 0, "turns": []}}],
        }
        reference, _ = publish_snapshot(self.root, "maintenance", "maint", packet)
        mount = prepare_snapshot_mount(
            invocation_root=self.root / "live-invocation", state_dir=self.root,
            identity=self.configuration["identity"], input_sha256=self.configuration["inputSha256"],
            snapshot={"namespace": "maintenance", "owner": "maint", "reference": reference})
        service = mounted_snapshot(mount)
        self.assertEqual(mounted_micro_task_coverage(mount), [])
        self.assertFalse(service.ledger.exists())
        service.mcp_call("query_batch", {})
        service.mcp_call("query_micro_tasks", {"runIds": ["missing"]})
        args = {"runIds": ["run"], "sections": ["task"]}
        reply = service.mcp_call("query_micro_tasks", args)
        data = decode_strict_json(reply["content"][0]["text"])["response"]["data"]
        before = service.ledger.read_bytes()
        with patch.object(locking, "lock", wraps=locking.lock) as lock:
            self.assertEqual(mounted_micro_task_coverage(mount), [
                {"tool": "query_micro_tasks", "ranges": data["coverage"]}])
            lock.assert_called_once_with(ANY, shared=True)
        self.assertEqual(service.ledger.read_bytes(), before)
        # Issued pages are observable before the native root calls match.
        with self.assertRaises(ReceiptError):
            service.verify_calls([], [self.native])
        rows = before.splitlines()
        first = decode_strict_json(rows[0])
        first["receiptSeq"] = 2
        service.ledger.write_bytes(b"\n".join([canonical_json(first).encode(), *rows[1:]]) + b"\n")
        with self.assertRaisesRegex(ValueError, "Invalid snapshot query ledger sequence"):
            mounted_micro_task_coverage(mount)

    def test_stdio_maintenance_pages_long_items_and_32_item_batches_within_signed_limit(self):
        long_run_id = "11111111-2222-3333-4444-555555555555"
        long_task = ("冻结维护证据A1\n\"quoted\"\\slash " * 1400)[:25000]
        small_runs = [f"{index:08x}-aaaa-bbbb-cccc-{index:012x}" for index in range(1, 33)]
        def make_run(run_id, task_text):
            return {
                "runId": run_id, "title": "Task", "project": None, "classification": None,
                "task": task_text,
                "result": {"state": "accepted", "continuationRounds": 0, "turns": [],
                           "continuations": [], "hostBoundaryFacts": []},
            }

        snapshot = {
            "domainNodes": [],
            "classificationRunIds": [long_run_id, *small_runs],
            "kinds": [],
            "comparisonStatistics": [],
            "microTasks": [make_run(long_run_id, long_task),
                           *(make_run(run_id, "ok") for run_id in small_runs)],
        }
        config = {"identity": {"harness": "codex", "taskId": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
                               "attemptId": "99999999-8888-7777-6666-555555555555", "generation": 1},
                  "inputSha256": "c" * 64, "key": "d" * 64}
        service = SnapshotService(SnapshotQuery(snapshot, plane="maintenance"),
                                  self.root / "maint-invocation", config)
        # 1. Page a 25,000-char ASCII+CJK maintenance task section to completion.
        fragments, cursor = [], None
        while True:
            args = {"runIds": [long_run_id], "sections": ["task"]}
            if cursor is not None:
                args["cursor"] = cursor
            reply = service.mcp_call("query_micro_tasks", args)
            self.assertFalse(reply["isError"], reply)
            self.assertLessEqual(len(canonical_json(reply).encode("utf-8")), 16 * 1024 - 256)
            data = decode_strict_json(reply["content"][0]["text"])["response"]["data"]
            self.assertEqual(len(data["items"]), 1)
            fragments.append(data["items"][0]["fragment"]["text"])
            cursor = data["nextCursor"]
            if cursor is None:
                break
        self.assertGreater(len(fragments), 1)
        reassembled = decode_strict_json("".join(fragments))
        self.assertEqual(reassembled["value"]["task"], long_task)

        # 2. Page 32 micro-task sections (16 runs x 2 sections) with production UUIDs.
        seen, cursor, pages = [], None, 0
        while True:
            args = {"runIds": small_runs[:16], "sections": ["task", "turns"], "limit": 32}
            if cursor is not None:
                args["cursor"] = cursor
            reply = service.mcp_call("query_micro_tasks", args)
            self.assertFalse(reply["isError"], reply)
            self.assertLessEqual(len(canonical_json(reply).encode("utf-8")), 16 * 1024 - 256)
            data = decode_strict_json(reply["content"][0]["text"])["response"]["data"]
            pages += 1
            seen.extend((item["runId"], item["section"]) for item in data["items"])
            cursor = data["nextCursor"]
            if cursor is None:
                break
        self.assertEqual(len(seen), 32)
        self.assertEqual(len(set(seen)), 32)
        self.assertGreaterEqual(pages, 1)
