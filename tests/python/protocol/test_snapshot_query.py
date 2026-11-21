"""Snapshot queries bound every result and prove only the returned maintenance ranges."""
from __future__ import annotations

import json
import unittest

from hey_my_buddy.json_codec import canonical_json
from hey_my_buddy.protocol.snapshot_query import (
    MAINTENANCE_SECTIONS, MAX_QUERY_IDS, MAX_QUERY_RESULT_BYTES, QueryError, SnapshotQuery,
    kind_id, maintenance_coverage, verified_coverage,
)


def candidate(buddy_id, provider, model_id, *, overall, root, cell):
    return {
        "buddyId": buddy_id, "harness": "codex", "provider": provider, "model": model_id, "effort": "high",
        "billing": {"kind": "metered"}, "load": {"active": 1, "limit": 3, "queued": 0, "wait": {"seconds": 0}},
        "statistics": {"overall": {"samples": overall},
                       "byDomain": {"root": {"samples": root}, "child": {"samples": cell}},
                       "byDomainTaskType": {"child": {"debug": {"samples": cell}}}},
        "contextWindow": 128_000, "capabilities": ["read"], "modelId": model_id,
        "modelDisplayName": model_id, "modelNames": [model_id], "infrastructureFailures": [],
        "modelProfile": {"positioning": "model profile", "claims": []}, "summary": "summary",
    }


def routing_packet():
    return {
        "task": "任务 🏔️ \"test\\\"" * 12_000,
        "buddies": [candidate("one", "a", "same", overall=20, root=1, cell=0),
                    candidate("two", "b", "same", overall=20, root=1, cell=0),
                    candidate("three", "c", "other", overall=3, root=2, cell=1)],
        "preferences": [{"buddyId": "one", "mode": "prefer", "note": "偏好原因" * 5_000}],
        "domainNodes": [{"nodeId": "root", "name": "Systems", "definition": "System design", "parentId": None},
                        {"nodeId": "child", "name": "Storage", "definition": "Frozen evidence", "parentId": "root"}],
    }


def maintenance_packet():
    target = {"harness": "dsh", "modelId": "one", "effort": "high", "modelDisplayName": "One", "modelNames": ["one"]}
    comparison = {**target, "modelId": "two:~", "modelDisplayName": "Two"}
    statistics = {"overall": {"samples": 4}, "byDomain": {"root": {"samples": 4}, "leaf": {"samples": 2}},
                  "byDomainTaskType": {"leaf": {"debug": {"samples": 2}}}}
    result = {
        "state": "accepted", "conclusion": None, "acceptanceNote": "checked", "conclusionReason": None,
        "executionStatus": "completed", "continuationRounds": 2, "hostBoundaries": 1,
        "reassigned": False, "integration": {"checks": ["verified"]}, "createdAt": "2026-11-19",
        "finishedAt": "2026-11-20", "turns": [{"kind": target, "failureCategory": "ability"}],
        "continuations": [{"input": "输入\\\"\x01👇" * 6_000}],
        "hostBoundaryFacts": [{"summary": "边界"}],
    }
    return {
        "domainNodes": [{"node_id": node, "name": node, "definition": "domain",
                         "parent_id": parent, "merged_into_id": merged}
                        for node, parent, merged in (("root", None, None), ("oldest", None, "old"),
                                                     ("old", None, "root"), ("leaf", "old", None))],
        "microTasks": [
            {"runId": "run-a", "title": "微任务 A", "task": "正文🙂" * 14_000, "classification": None,
             "project": {"id": None, "path": "~"}, "result": result},
            {"runId": "run-b", "title": "Task B", "task": "Short task",
             "classification": {"domainNodeId": "leaf", "taskType": "debug"},
             "project": {"id": None, "path": "~"}, "result": {**result, "continuations": []}},
        ],
        "classificationRunIds": ["run-a"],
        "comparisonStatistics": [{**comparison, "statistics": statistics}],
        "kinds": [{**target, "kindId": kind_id(target), "statistics": statistics, "unreadRunIds": ["run-a"],
                   "citationRunIds": ["run-a", "run-b"],
                   "summary": {"text": "previous", "citations": ["old-run"]}}],
    }


class SnapshotQueryTests(unittest.TestCase):
    def bounded(self, result):
        encoded_text = canonical_json(canonical_json(result)).encode()
        self.assertLessEqual(len(encoded_text) + 1024, MAX_QUERY_RESULT_BYTES)

    def read_items(self, query, tool, args):
        events, items, fragments = [], [], []
        for _ in range(100):
            result, event = query.call(tool, args)
            self.bounded(result)
            events.append(event)
            for item in result["items"]:
                piece = item.get("fragment")
                if piece is None:
                    self.assertFalse(fragments)
                    items.append(item)
                    continue
                self.assertEqual(piece["offset"], sum(map(len, fragments)))
                fragments.append(piece["text"])
                self.assertEqual(piece["end"], sum(map(len, fragments)))
                if piece["end"] == piece["totalCharacters"]:
                    items.append(json.loads("".join(fragments)))
                    fragments.clear()
            if result["nextCursor"] is None:
                self.assertFalse(fragments)
                return items, events
            args = {**args, "cursor": result["nextCursor"]}
        self.fail("Tool pagination did not finish")

    def test_routing_tools_freeze_filters_and_only_expose_exploration_when_allocated(self):
        snapshot = routing_packet()
        plain = SnapshotQuery(snapshot)
        rebuilt = SnapshotQuery({**snapshot, "buddies": snapshot["buddies"][:1]})
        self.assertEqual(plain.tools(), rebuilt.tools())
        with self.assertRaises(QueryError):
            plain.call("query_explore", {})
        query = SnapshotQuery({**snapshot, "exploration": {"quota": True}})

        roots, _ = query.call("query_domains", {})
        self.assertEqual(roots["items"], [{**snapshot["domainNodes"][0], "childCount": 1}])
        matches, _ = query.call("query_domains", {"search": "eVidEncE"})
        self.assertEqual(matches["items"][0]["nodeId"], "child")

        args = {"domainNodeId": "child", "taskType": "debug", "limit": 1}
        first, _ = query.call("query_compare", args)
        self.assertIsNotNone(first["nextCursor"])
        with self.assertRaises(QueryError):
            query.call("query_compare", {**args, "domainNodeId": "root", "cursor": first["nextCursor"]})
        candidates, _ = self.read_items(query, "query_compare", args)
        self.assertEqual([item["buddyId"] for item in candidates], ["one", "two", "three"])
        self.assertEqual(candidates[0]["statistics"], {
            "overall": {"samples": 20}, "domain": {"samples": 0}, "cell": {"samples": 0},
        })
        self.assertEqual(candidates[0]["load"], snapshot["buddies"][0]["load"])
        self.assertEqual(candidates[0]["preference"], {"mode": "prefer", "notePreview": "偏好原因" * 40,
                                                     "hasLongNote": True})
        self.assertTrue(candidates[0]["hasProfile"])
        self.assertTrue(candidates[0]["hasSummary"])

        groups, _ = self.read_items(query, "query_explore", {"domainNodeId": "child", "limit": 1})
        self.assertEqual([(item["domainNodeId"], item["samples"], item["buddyIds"]) for item in groups],
                         [("root", 1, ["one", "two"]), ("root", 2, ["three"])])
        groups, _ = self.read_items(query, "query_explore", {})
        self.assertEqual([item["samples"] for item in groups], [3, 20])
        details, _ = self.read_items(query, "query_details", {
            "buddyIds": ["one"], "include": ["contextWindow", "preference", "modelProfile"],
        })
        self.assertEqual([item["value"] for item in details], [128_000, snapshot["preferences"][0],
                                                             snapshot["buddies"][0]["modelProfile"]])

    def test_routing_task_can_be_read_from_explicit_unicode_offsets_without_loss(self):
        snapshot = routing_packet()
        query = SnapshotQuery(snapshot)
        offset, parts = 1, []
        while offset is not None:
            result, _ = query.call("query_task", {"offset": offset})
            self.bounded(result)
            self.assertEqual(result["offset"], offset)
            self.assertEqual(result["totalBytes"], len(snapshot["task"].encode()))
            self.assertEqual(result["totalCharacters"], len(snapshot["task"]))
            parts.append(result["text"])
            offset = result["nextOffset"]
            self.assertEqual(result["remaining"], {"start": offset, "end": len(snapshot["task"])}
                             if offset is not None else None)
        self.assertGreater(len(parts), 1)
        self.assertEqual("".join(parts), snapshot["task"][1:])

    def test_query_boundary_rejects_unknown_fields_ids_and_invalid_positions(self):
        query = SnapshotQuery(routing_packet())
        for tool, args in [
            ("query_domains", {"limit": True}),
            ("query_compare", {"domainNodeId": "missing"}),
            ("query_compare", {"future": "unknown"}),
            ("query_details", {"buddyIds": ["one", "missing"]}),
            ("query_details", {"buddyIds": ["one", "one"]}),
            ("query_details", {"buddyIds": [str(i) for i in range(MAX_QUERY_IDS + 1)]}),
            ("query_task", {"offset": 1_000_000}),
        ]:
            with self.subTest(tool=tool, args=args), self.assertRaises(QueryError):
                query.call(tool, args)
        result, event = query.call("query_domains", {"search": "does not occur"})
        self.assertEqual(result, {"items": [], "nextCursor": None})
        self.assertEqual(event["ranges"], [])

    def test_maintenance_exposes_index_merged_target_and_comparison_kind(self):
        snapshot = maintenance_packet()
        query = SnapshotQuery(snapshot, plane="maintenance")
        index, _ = self.read_items(query, "query_batch", {"limit": 1})
        self.assertEqual(index, [
            {"runId": "run-a", "title": "微任务 A", "classification": None, "needsClassification": True,
             "kindIds": ["dsh:one:high"], "state": "accepted", "reworkRounds": 2},
            {"runId": "run-b", "title": "Task B", "classification": {"domainNodeId": "leaf", "taskType": "debug"},
             "needsClassification": False, "kindIds": ["dsh:one:high"], "state": "accepted", "reworkRounds": 2},
        ])
        merged, _ = query.call("query_domains", {"search": "oldest", "includeMerged": True})
        self.assertEqual(merged["items"][0]["mergedIntoId"], "root")
        children, _ = query.call("query_domains", {"parentId": "root"})
        self.assertEqual([node["nodeId"] for node in children["items"]], ["leaf"])

        items, _ = self.read_items(query, "query_kinds", {"domainNodeIds": ["leaf"], "limit": 2})
        fields = {(item["kindId"], item["field"]): item["value"] for item in items}
        self.assertEqual(fields["dsh:one:high", "unreadRunIds"], ["run-a"])
        self.assertEqual(fields["dsh:one:high", "summary"]["citations"], ["old-run"])
        comparison_id = kind_id(snapshot["comparisonStatistics"][0])
        self.assertEqual(comparison_id, "dsh:two%3A%7E:high")
        self.assertFalse(fields[comparison_id, "identity"]["needsSummary"])
        self.assertEqual(fields[comparison_id, "statistics"]["byDomain"], {"leaf": {"samples": 2}})
        self.assertEqual(fields[comparison_id, "statistics"]["byDomainTaskType"], {"leaf": {"debug": {"samples": 2}}})
        with self.assertRaises(QueryError):
            query.call("query_micro_tasks", {"runIds": ["old-run"]})

    def test_maintenance_coverage_requires_every_unicode_page_for_each_returned_section(self):
        snapshot = maintenance_packet()
        query = SnapshotQuery(snapshot, plane="maintenance")
        _, titles = query.call("query_batch", {})
        items, events = self.read_items(query, "query_micro_tasks", {"runIds": ["run-a", "run-b"], "limit": 1})
        by_section = {(item["runId"], item["section"]): item["value"] for item in items}
        run = snapshot["microTasks"][0]
        task = by_section["run-a", "task"]
        self.assertEqual(task["task"], run["task"])
        self.assertEqual(task["project"], run["project"])
        self.assertTrue(task["needsClassification"])
        self.assertEqual(by_section["run-a", "continuations"], run["result"]["continuations"])
        self.assertEqual(by_section["run-b", "continuations"], [])
        self.assertEqual(by_section["run-a", "acceptanceAndIntegration"]["integration"], run["result"]["integration"])
        self.assertEqual(verified_coverage("maintenance", [titles]), {})
        first = events[0]["ranges"][0]
        self.assertEqual((first["runId"], first["section"], first["start"]), ("run-a", "task", 0))
        self.assertLess(first["end"], first["total"])
        self.assertEqual(verified_coverage("maintenance", [events[0], events[0]]), {})
        expected = set(MAINTENANCE_SECTIONS)
        self.assertEqual(verified_coverage("maintenance", reversed(events)), {"run-a": expected, "run-b": expected})
        self.assertEqual(verified_coverage("maintenance", [titles, *events[1:]]),
                         {"run-a": expected - {"task"}, "run-b": expected})

    def test_malformed_verified_range_cannot_be_counted_as_read(self):
        snapshot = maintenance_packet()
        query = SnapshotQuery(snapshot, plane="maintenance")
        _, event = query.call("query_micro_tasks", {"runIds": ["run-a"], "sections": ["task"]})
        span = event["ranges"][0]
        with self.assertRaises(QueryError):
            maintenance_coverage(snapshot, [{**event, "ranges": [{**span, "end": True}]}])

    def test_first_page_of_batched_sections_only_proves_the_returned_run(self):
        query = SnapshotQuery(maintenance_packet(), plane="maintenance")
        result, event = query.call("query_micro_tasks", {
            "runIds": ["run-b", "run-a"], "sections": ["turns"], "limit": 1,
        })
        self.assertEqual(result["coverage"], event["ranges"])
        self.assertEqual(result["items"][0]["runId"], "run-b")
        self.assertEqual(verified_coverage("maintenance", [event]), {"run-b": {"turns"}})

    def test_summary_coverage_requires_every_section_of_every_surviving_unread_run(self):
        snapshot = maintenance_packet()
        target = snapshot["kinds"][0]
        second = {**target, "modelId": "another", "unreadRunIds": ["run-a", "run-b"]}
        snapshot["kinds"].append({**second, "kindId": kind_id(second)})
        query = SnapshotQuery(snapshot, plane="maintenance")
        _, task_pages = self.read_items(query, "query_micro_tasks", {"runIds": ["run-a"], "sections": ["task"]})
        _, active_pages = self.read_items(query, "query_micro_tasks", {"runIds": ["run-b"]})

        self.assertEqual(maintenance_coverage(snapshot, [*task_pages, *active_pages]), ({"run-a"}, set()))
        self.assertEqual(maintenance_coverage(snapshot, active_pages, surviving_run_ids={"run-b"}),
                         (set(), {kind_id(second)}))
        _, all_pages = self.read_items(query, "query_micro_tasks", {"runIds": ["run-a", "run-b"]})
        self.assertEqual(maintenance_coverage(snapshot, all_pages), ({"run-a"}, {kind_id(target), kind_id(second)}))
