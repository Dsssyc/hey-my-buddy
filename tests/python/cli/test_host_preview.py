#!/usr/bin/env python3
"""Preview-fixture regression for the ADR-018 console surface.

The synthetic preview data (`tests/probes/macro_task_console_preview.py`) once
lagged behind the console schema, and the new console refused to load it. This
test closes that gap:

1. the preview script's own ``--check`` shape/coverage invariants pass;
2. the emitted fixture tree is deterministic, so a shape change is reproducible;
3. the emitted tree is consumed by the *real* frontend parsers — not by a
   Python re-implementation — through ``createApi(...).snapshot()``,
   ``.macroTasks()``, ``.macroTaskTimeline()``, the ``workflow_get`` command
   path and ``parseWorkflowReply``, and the resulting report is asserted here.
   Three preview scenarios (normal/truncated/error) stay covered, and
   deliberately incompatible shapes are refused instead of silently accepted.

The frontend check needs the console's dev dependencies (``npm ci`` in
``apps/console``); a missing toolchain fails loudly rather than skipping,
because a skip would silently drop the cross-boundary guard.
"""
from __future__ import annotations

import json
import http.client
import importlib.util
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[3]
PREVIEW = ROOT / "tests" / "probes" / "macro_task_console_preview.py"
CONSOLE = ROOT / "apps" / "console"
FRONTEND_TEST = "src/preview-contract.test.ts"
VITEST = CONSOLE / "node_modules" / "vitest" / "vitest.mjs"

#: Variables an outer hey-my-buddy process may export; a child test never
#: inherits them (AGENTS.md verification rules).
_INHERITED_KEYS = (
    "HMB_STATE_DIR", "HMB_RUNTIME_ROOT", "HMB_RUNTIME", "HMB_RUNTIME_IDENTITY",
    "HMB_WORKER_STATE", "HMB_WORKER_ID",
    "HMB_AGENT_CREDENTIAL_FILE", "HMB_AGENT_CREDENTIAL_FD", "VIRTUAL_ENV", "UV_PROJECT_ENVIRONMENT",
)


def child_environment(overrides: dict[str, str] | None = None) -> dict[str, str]:
    environment = {key: value for key, value in os.environ.items() if key not in _INHERITED_KEYS}
    environment["UV_OFFLINE"] = "1"
    environment.update(overrides or {})
    return environment


def emit_fixtures(directory: Path) -> dict:
    """Run the preview script's emit mode and return its manifest."""
    result = subprocess.run(
        [sys.executable, str(PREVIEW), "--emit-fixtures", str(directory)],
        cwd=str(ROOT), env=child_environment(), capture_output=True, text=True, timeout=180,
    )
    if result.returncode != 0:
        raise AssertionError(f"--emit-fixtures failed ({result.returncode}): {result.stdout}{result.stderr}")
    manifest = directory / "manifest.json"
    if not manifest.is_file():
        raise AssertionError(f"--emit-fixtures wrote no manifest under {directory}")
    return json.loads(manifest.read_text(encoding="utf-8"))


def tree_files(root: Path) -> dict[str, bytes]:
    return {
        str(path.relative_to(root)): path.read_bytes()
        for path in sorted(root.rglob("*")) if path.is_file()
    }


class PreviewFixtureTest(unittest.TestCase):
    maxDiff = 8000

    def test_preview_self_check_passes(self) -> None:
        result = subprocess.run(
            [sys.executable, str(PREVIEW), "--check"],
            cwd=str(ROOT), env=child_environment(), capture_output=True, text=True, timeout=180,
        )
        self.assertEqual(0, result.returncode, f"preview --check failed:\n{result.stdout}\n{result.stderr}")
        self.assertIn("CHECK OK", result.stdout)

    def test_preview_tree_is_deterministic(self) -> None:
        with tempfile.TemporaryDirectory(prefix="hey-my-buddy-preview-") as temporary:
            first, second = Path(temporary) / "first", Path(temporary) / "second"
            emit_fixtures(first)
            emit_fixtures(second)
            self.assertEqual(tree_files(first), tree_files(second))

    def test_frontend_parsers_consume_the_emitted_fixtures(self) -> None:
        node = os.environ.get("HMB_NODE") or shutil.which("node")
        self.assertTrue(node, "Node.js is required to run the console parser test")
        self.assertTrue(
            VITEST.is_file(),
            "console dev dependencies are not installed, so the preview fixtures cannot be consumed "
            f"by the real frontend parser ({VITEST}); run `npm ci` in {CONSOLE.relative_to(ROOT)}",
        )
        with tempfile.TemporaryDirectory(prefix="hey-my-buddy-preview-") as temporary:
            workspace = Path(temporary)
            emitted = workspace / "fixtures"
            manifest = emit_fixtures(emitted)
            report_path = workspace / "report.json"
            result = subprocess.run(
                [str(node), str(VITEST), "run", FRONTEND_TEST],
                cwd=str(CONSOLE),
                env=child_environment({
                    "HMB_PREVIEW_FIXTURES": str(emitted),
                    "HMB_PREVIEW_REPORT": str(report_path),
                    "CI": "1",
                    "NO_COLOR": "1",
                }),
                capture_output=True, text=True, timeout=600, encoding="utf-8",
            )
            self.assertEqual(
                0, result.returncode,
                f"the frontend preview-contract test failed:\n{result.stdout}\n{result.stderr}",
            )
            self.assertTrue(report_path.is_file(), "the frontend test wrote no parser report")
            report = json.loads(report_path.read_text(encoding="utf-8"))
            self._assert_report(report, manifest, emitted)

    def _assert_report(self, report: dict, manifest: dict, fixtures: Path) -> None:
        self.assertEqual(1, report["fixtureVersion"])
        self.assertEqual("tests/probes/macro_task_console_preview.py", manifest["source"])
        outcomes = {outcome["path"]: outcome for outcome in report["outcomes"]}
        self.assertEqual({entry["path"] for entry in manifest["files"]}, set(outcomes))
        for entry in manifest["files"]:
            outcome = outcomes[entry["path"]]
            if entry["expected"] == "accepted":
                self.assertTrue(outcome["accepted"], f"{entry['path']} must parse: {outcome['code']}")
                self.assertIsNone(outcome["code"], entry["path"])
            else:
                self.assertFalse(outcome["accepted"], f"{entry['path']} must be refused by the real parser")
                self.assertEqual(entry["expectedCode"], outcome["code"], entry["path"])

        # Catalog position is a required buddy fact, even when it is unknown.
        for entry in manifest["files"]:
            if entry["kind"] != "console-snapshot" or entry["expected"] != "accepted":
                continue
            snapshot = json.loads((fixtures / entry["path"]).read_text(encoding="utf-8"))
            positions = {}
            for buddy in snapshot["buddies"]:
                rank = buddy["effortRank"]
                self.assertIs(type(rank), int)
                self.assertGreaterEqual(rank, 0)
                identity = (buddy["harness"], buddy["provider"], buddy["model"])
                positions.setdefault(identity, []).append(rank)
            for ranks in positions.values():
                self.assertEqual(list(range(len(ranks))), sorted(ranks))
            self.assertEqual(
                [{"buddyId": buddy["buddyId"], "effortRank": buddy["effortRank"]} for buddy in snapshot["buddies"]],
                outcomes[entry["path"]]["facts"]["buddyEffortRanks"],
            )

        # Current preview scenarios stay covered, and the error scenario stays visible.
        scenarios = {entry["scenario"] for entry in manifest["files"]}
        self.assertEqual({"normal", "truncated", "error", "incompatible"}, scenarios)
        truncated = outcomes["truncated/timeline-macro-a.json"]
        self.assertTrue(truncated["facts"]["truncated"]["rows"])
        for path in ("error/timeline-macro-a.json", "error/workflow-preview-run-a1.json"):
            self.assertEqual("SERVICE_UNAVAILABLE", outcomes[path]["code"])
        self.assertTrue(outcomes["error/console.json"]["accepted"])

        # The current Router-list and model-service-note snapshot shape the parser requires.
        raw_snapshot = json.loads((fixtures / "normal/console.json").read_text(encoding="utf-8"))
        self.assertFalse(set(raw_snapshot) & {"gate", "cards", "evidence", "pendingEvidence", "sampleCounts"})
        self.assertFalse(set(raw_snapshot["capabilities"]) & {
            "maintenance", "maintenanceMode", "evaluationWriteGate", "readerAdmission", "evidenceRecord",
        })
        normal = outcomes["normal/console.json"]["facts"]
        configuration = normal["configuration"]
        self.assertNotIn("decisionBuddyId", configuration)
        for key in ("routerBuddyIds", "routerRetryIntervalSeconds", "defaultRoutingMode", "routingBudget"):
            self.assertIn(key, configuration)
        # The slim snapshot carries the server's board-wide pending count; the
        # full execution records stay on the paginated tasks read.
        self.assertTrue(normal["pendingCount"] > 0)
        tasks_page = outcomes["normal/tasks.json"]["facts"]
        self.assertTrue(tasks_page["runs"] > 0)
        self.assertTrue(tasks_page["nextCursor"] is None or isinstance(tasks_page["nextCursor"], str))
        version = outcomes["normal/runtime-version.json"]["facts"]
        self.assertEqual({"mode": "runtime", "softwareVersion": "0.0.0", "installed": True}, version)

        # A harness can have independent providers in different cooling states.
        harnesses = {row["harness"]: row for row in normal["harnesses"]}
        dsh = {row["provider"]: row for row in harnesses["dsh"]["providers"]}
        self.assertEqual({"deepseek-official", "deepseek-partner"}, set(dsh))
        self.assertIsNotNone(dsh["deepseek-official"]["coolingUntil"])
        self.assertIsNone(dsh["deepseek-partner"]["coolingUntil"])
        self.assertEqual(2, dsh["deepseek-official"]["consecutiveRejections"])
        self.assertLess(harnesses["zcode"]["providers"][0]["coolingUntil"], "2026-01-01")
        self.assertEqual([], harnesses["codex"]["providers"])
        self.assertTrue(all("quota" not in row and "quotaRouting" not in row
                            for row in raw_snapshot["harnesses"]))

        # Workflow facts straight from the frontend parser's report.
        workflows = {path: outcome["facts"] for path, outcome in outcomes.items()
                     if outcome["kind"] == "workflow-get" and outcome["accepted"]}
        self.assertTrue(workflows)
        locked = {facts["buddyLocked"] for facts in workflows.values()}
        self.assertIn(True, locked, "one micro task must record the user-locked buddy")
        self.assertIn(False, locked, "one micro task must record a Host-supplied buddy")
        for facts in workflows.values():
            self.assertIn("hostConclusion", facts)

        conclusions = [(path, facts["hostConclusion"]) for path, facts in workflows.items()
                       if facts["hostConclusion"]]
        self.assertTrue(conclusions, "the preview must record a failed or cancelled Host conclusion")
        for _path, conclusion in conclusions:
            self.assertIn(conclusion["statusLabel"], {"失败", "已取消"})
            self.assertNotIn("evidence", conclusion)
            self.assertNotIn("integrationId", conclusion)
        # A quota failure keeps its real failure label and its partial output: the
        # conclusion records the outcome and never turns the micro task into a success.
        failed_path, failed_conclusion = next(
            (path, conclusion) for path, conclusion in conclusions if conclusion["statusLabel"] == "失败")
        self.assertTrue(failed_conclusion["failed"])
        self.assertFalse(failed_conclusion["cancelled"])
        fixture = json.loads((fixtures / failed_path).read_text(encoding="utf-8"))
        self.assertEqual("failed", fixture["task"]["state"])
        self.assertEqual("failed", fixture["state"])
        self.assertIsNone(fixture["task"]["acceptedAt"])
        self.assertTrue(any(item["kind"] == "partial-output" and item.get("partial") is True
                            and item.get("verified") is False and item.get("final") is False
                            for item in fixture["artifacts"]),
                        "a quota failure must keep its sealed partial output")

        # The emitted Lead timeline is consumed by the real parser as well as
        # served to the browser; ownership must survive that projection.
        self.assertTrue(outcomes["normal/timeline-macro-b.json"]["accepted"])
        timeline = json.loads((fixtures / "normal/timeline-macro-b.json").read_text(encoding="utf-8"))
        rows = {row["runId"]: row for row in timeline["rows"]}
        task_rows = json.loads((fixtures / "normal/tasks.json").read_text(encoding="utf-8"))["runs"]
        tasks = {task["runId"]: task for task in task_rows}
        for run_id, row in rows.items():
            for key in ("rootLeadRunId", "leadOwnership", "delegationAuthority"):
                self.assertIn(key, row, f"{run_id}: TimelineRow must carry {key}, including explicit nulls")
                self.assertEqual(tasks[run_id]["lineage"][key], row[key], f"{run_id}: {key} projection drift")
            self.assertIs(type(row["delegationAuthority"]), bool)
        self.assertEqual(4, sum(row["rootLeadRunId"] == "preview-run-b3" for row in rows.values()))
        self.assertFalse(rows["preview-run-b10"]["delegationAuthority"])
        self.assertIsNone(rows["preview-run-b10"]["rootLeadRunId"])
        settled = json.loads((fixtures / "normal/timeline-macro-c.json").read_text(encoding="utf-8"))
        settled_rows = {row["runId"]: row for row in settled["rows"]}
        self.assertTrue(settled_rows["preview-run-d1"]["delegationAuthority"])
        self.assertFalse(any(row["lineage"]["rootLeadRunId"] == "preview-run-d1" for row in task_rows))
        self.assertIsNone(settled_rows["preview-run-d2"]["title"])
        self.assertEqual("none", settled_rows["preview-run-d2"]["titleSource"])
        released = json.loads((fixtures / "normal/workflow-preview-run-d2.json").read_text(encoding="utf-8"))
        self.assertEqual({"code": "service-stopped-before-launch", "params": {}},
                         released["task"]["selectedAttempt"]["releaseReason"])
        for timeline_path in ("timeline-macro-a.json", "timeline-macro-b.json", "timeline-macro-c.json", "timeline-standalone.json"):
            recorded = json.loads((fixtures / "normal" / timeline_path).read_text(encoding="utf-8"))
            for event in recorded["events"]:
                self.assertFalse({"label", "summary"} & event.keys())
                self.assertTrue({"decision", "verdict", "state", "strategy", "note"} <= event.keys())
        self.assertEqual("failed", rows["preview-run-b2"]["state"])
        self.assertEqual("preview-run-b1", rows["preview-run-b2"]["rootLeadRunId"])
        rejected = next(span for span in timeline["spans"] if span["spanId"] == "execution:att-b9-1")
        self.assertEqual("rejected", rejected["state"])
        self.assertEqual("failed", rejected["resultStatus"])
        lead, waiting, active = [json.loads((fixtures / f"normal/workflow-{run_id}.json").read_text(encoding="utf-8"))
                                 for run_id in ("preview-run-b3", "preview-run-b4", "preview-run-b5")]
        self.assertEqual("waiting-owned", rows[lead["runId"]]["state"])
        self.assertEqual("waiting-owned", lead["state"])
        self.assertEqual("completed", lead["task"]["state"])
        self.assertEqual("awaiting-owned", lead["queueReason"])
        self.assertEqual("awaiting-owned", lead["task"]["queueReason"])
        self.assertIsNone(lead["task"]["activeAttemptId"])
        self.assertTrue(lead["task"]["lineage"]["delegationAuthority"])
        self.assertTrue(lead["shutdown"]["selfConfirmed"])
        self.assertIsNone(lead["finalAttemptId"])
        self.assertIsNone(lead["finalArtifactId"])
        turn = lead["currentTurn"]
        self.assertEqual("waiting", turn["disposition"])
        self.assertEqual("returned", turn["state"])
        self.assertEqual(2, turn["turnIndex"])
        self.assertEqual(2, len(lead["turns"]))
        self.assertEqual(1, lead["continuationCount"])
        dispatch_turn = lead["turns"][-1]
        self.assertLess(dispatch_turn["updatedAt"], waiting["updatedAt"])
        self.assertLess(waiting["updatedAt"], turn["createdAt"])
        for owned in (waiting, active):
            row = rows[owned["runId"]]
            self.assertEqual(lead["runId"], row["rootLeadRunId"])
            self.assertEqual(lead["runId"], row["leadOwnership"]["leadRunId"])
            self.assertEqual(dispatch_turn["turnId"], row["leadOwnership"]["leadTurnId"])
            self.assertEqual(owned["createdAt"], row["leadOwnership"]["createdAt"])
            self.assertLessEqual(dispatch_turn["createdAt"], owned["createdAt"])
            self.assertLessEqual(owned["createdAt"], dispatch_turn["updatedAt"])
            self.assertLess(owned["updatedAt"], turn["createdAt"])
            self.assertIsNone(owned["hostId"])
            self.assertIsNone(owned["task"]["lineage"]["sourceHostId"])
            self.assertIsNone(owned["task"]["lineage"]["currentHostId"])
            self.assertIsNone(row["parentRunId"], "Lead-owned work is an independent micro task")
            self.assertEqual(owned["runId"], row["rootRunId"])
            self.assertIsNone(owned["finalAttemptId"])
            self.assertIsNone(owned["finalArtifactId"])
        self.assertEqual("awaiting-host", waiting["state"])
        self.assertEqual("assistance", waiting["currentTurn"]["disposition"])
        self.assertTrue(waiting["awaitingHost"])
        self.assertTrue(waiting["shutdown"]["selfConfirmed"])
        self.assertEqual("open", waiting["activeRequest"]["state"])
        wait_span = next(span for span in timeline["spans"]
                         if span["runId"] == waiting["runId"] and span["kind"] == "host")
        self.assertEqual(waiting["activeRequest"]["requestId"], wait_span["requestId"])
        self.assertEqual(waiting["activeRequest"]["summary"], wait_span["summary"])
        self.assertEqual(waiting["currentTurn"]["updatedAt"], wait_span["startAt"])
        self.assertIsNone(wait_span["endAt"])
        self.assertEqual("executing", active["state"])
        self.assertEqual("queued", active["task"]["state"])
        self.assertIsNone(active["task"]["selectedAttemptId"])
        self.assertIsNone(active["task"]["activeAttemptId"])
        self.assertIsNone(active["currentTurn"])
        self.assertEqual([], active["turns"])
        self.assertTrue(active["shutdown"]["selfConfirmed"])
        self.assertFalse(active["task"]["resultAvailable"])

        # Per-execution usage: known values name the cache subset, and an unrecorded
        # execution stays unknown instead of reading as zero.
        usages = [turn["tokenUsage"] for facts in workflows.values() for turn in facts["turns"]]
        self.assertTrue(any("含缓存" in usage for usage in usages))
        self.assertTrue(any("未知" in usage for usage in usages))
        for facts in workflows.values():
            for turn in facts["turns"]:
                if turn["source"] is None:
                    self.assertEqual("用量未记录", turn["tokenUsageTitle"])
                else:
                    self.assertIn("单次执行", turn["tokenUsageTitle"],
                                  "recorded usage must state its per-execution scope")
        self.assertFalse(any("会话累计：" in usage for usage in usages),
                         "no usage line may be a session cumulative total")

        # Partial outputs are marked unverified and non-final, and never become the
        # micro task's final artifact; cumulative patches keep their fixed base commit.
        artifacts = [item for facts in workflows.values() for item in facts["artifacts"]]
        partial = [item for item in artifacts if item["state"]["partial"]]
        self.assertTrue(partial, "the preview must record a partial output")
        for item in partial:
            self.assertFalse(item["state"]["verified"])
            self.assertFalse(item["state"]["final"])
        for facts in workflows.values():
            final_id = facts["finalArtifactId"]
            if final_id is None:
                continue
            final = next(item for item in facts["artifacts"] if item["artifactId"] == final_id)
            self.assertFalse(final["state"]["partial"])

        patches = [item["cumulativePatch"] for item in artifacts if item["cumulativePatch"]]
        self.assertTrue(patches, "the preview must record a cumulative patch")
        self.assertTrue(any(patch["complete"] for patch in patches))
        for path in workflows:
            fixture = json.loads((fixtures / path).read_text(encoding="utf-8"))
            base = fixture["workspace"]["inputCommit"]
            for item in fixture["artifacts"]:
                patch = item.get("cumulativePatch")
                if patch:
                    self.assertEqual(base, patch["baseCommit"],
                                     f"{path}: cumulative base must be the micro task input commit")

        host_paths = [entry for facts in workflows.values() for entry in facts["hostPaths"]]
        self.assertTrue(any(host_paths), "the preview must record one integration with Host paths")
        self.assertTrue(any(not entry for entry in host_paths), "the preview must also keep Host paths empty")


class SessionFixtureTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        spec = importlib.util.spec_from_file_location("synthetic_console_preview_sessions", PREVIEW)
        cls.preview = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.preview)

    def test_each_harness_session_matches_its_existing_attempt(self) -> None:
        preview = self.preview
        harnesses = {
            preview.SESSION_LONG: "codex",
            preview.SESSION_RUNNING: "claude",
            preview.SESSION_CLAUDE: "claude",
            preview.SESSION_ROUTER: "dsh",
            preview.SESSION_ROUTER_REVIEW: "codex",
            preview.SESSION_ZCODE: "zcode",
            preview.SESSION_ZCODE_OLD: "zcode",
            preview.SESSION_DSH: "dsh",
        }
        self.assertFalse(set(harnesses) & set(preview.SESSION_ERRORS))
        for pair, harness in harnesses.items():
            with self.subTest(pair=pair):
                attempt = next(item for item in preview.FIXTURES["runs"][pair[0]]["attempts"]
                               if item["attemptId"] == pair[1])
                self.assertEqual(harness, attempt["buddy"]["harness"])
                events = preview.session_read(*pair, {}, preview.PreviewState())["events"]
                self.assertTrue(events)
                self.assertTrue(all(event["sessionId"] == pair[1] for event in events))

        dsh_updates = [event["update"]["sessionUpdate"] for event in preview.session_events(preview.SESSION_DSH)]
        self.assertEqual(["user_message_chunk", "agent_message_chunk"], dsh_updates)
        old_tools = [event["update"] for event in preview.session_events(preview.SESSION_ZCODE_OLD)
                     if event["update"]["sessionUpdate"].startswith("tool_call")]
        new_tools = [event["update"] for event in preview.session_events(preview.SESSION_ZCODE)
                     if event["update"]["sessionUpdate"] == "tool_call"]
        self.assertTrue(old_tools)
        self.assertTrue(all("rawInput" not in update for update in old_tools))
        self.assertTrue(new_tools)
        self.assertTrue(all(update.get("rawInput") for update in new_tools))
        self.assertTrue(any(update["title"].endswith("hey_my_buddy_finish_turn") for update in new_tools))
        for pair in (preview.SESSION_RUNNING, preview.SESSION_CLAUDE):
            tools = [event["update"] for event in preview.session_events(pair)
                     if event["update"]["sessionUpdate"] == "tool_call"
                     and event["update"].get("kind") in {"read", "search"}]
            self.assertEqual({"read", "search"}, {tool["kind"] for tool in tools})
            self.assertTrue(all(tool.get("rawInput") for tool in tools))

    def test_structured_claude_end_and_router_answers(self) -> None:
        preview = self.preview
        claude = [event["update"] for event in preview.session_events(preview.SESSION_CLAUDE)]
        self.assertEqual("agent_message_chunk", claude[-4]["sessionUpdate"])
        self.assertIn("准备提交", claude[-4]["content"]["text"])
        delivery, first_update, last_update = claude[-3:]
        self.assertEqual(("tool_call", "StructuredOutput", "other"),
                         (delivery["sessionUpdate"], delivery["title"], delivery["kind"]))
        self.assertEqual({"disposition": "completed", "summary": "独立核对完成：路径示例与说明一致。",
                          "artifacts": ["docs/reference/path-guide.md"], "decisions": ["保留相对路径示例。"],
                          "remaining": [], "request": None}, delivery["rawInput"]["outcome"])
        self.assertEqual(["tool_call_update", "tool_call_update"],
                         [first_update["sessionUpdate"], last_update["sessionUpdate"]])
        self.assertEqual(["in_progress", "completed"], [first_update["status"], last_update["status"]])
        self.assertEqual({"claude-structured-output"},
                         {update["toolCallId"] for update in (delivery, first_update, last_update)})

        for pair, mode, buddy_id in ((preview.SESSION_ROUTER, "fast", "dsh:deepseek-official:deepseek-flash:max"),
                                     (preview.SESSION_ROUTER_REVIEW, "review", None)):
            with self.subTest(pair=pair):
                run = preview.FIXTURES["runs"][pair[0]]
                owner = preview.FIXTURES["runs"][run["parentRunId"]]
                recorded = next(span for span in owner["spans"] if span.get("decisionId") == run["decisionId"])
                self.assertEqual(pair[1], recorded["attemptId"])
                self.assertEqual(mode, recorded["routing"]["routingMode"])
                self.assertEqual(run["attempts"][0]["buddy"], recorded["routing"]["routerBuddy"])
                updates = [event["update"] for event in preview.session_events(pair)]
                self.assertTrue(updates[0]["content"]["text"].startswith("ROLE: Router buddy"))
                answer = json.loads(updates[-1]["content"]["text"])
                self.assertEqual(buddy_id, answer["buddyId"])
                self.assertTrue(answer["note"])
                self.assertTrue(answer["evidence"])

    def test_failed_codex_call_crosses_page_boundary_and_long_output_is_complete(self) -> None:
        preview = self.preview
        pair = preview.SESSION_LONG
        state = preview.PreviewState()
        latest = preview.session_page(pair, {}, state)
        earlier = preview.session_page(pair, {"before": latest["before"]}, state)
        self.assertEqual(100, len(latest["events"]))
        self.assertFalse(earlier["hasEarlier"])
        self.assertEqual(earlier["after"], latest["before"])
        self.assertEqual(preview.session_events(pair), earlier["events"] + latest["events"])
        opening = earlier["events"][-1]["update"]
        marker, failure = [event["update"] for event in latest["events"][:2]]
        self.assertEqual(("tool_call", "codex-failed-command", "in_progress"),
                         (opening["sessionUpdate"], opening["toolCallId"], opening["status"]))
        self.assertEqual(("agent_message_chunk", True),
                         (marker["sessionUpdate"], marker["_meta"]["marker"]))
        self.assertEqual("turn/diff/updated", marker["_meta"]["nativeType"])
        self.assertEqual("diff/0", marker["_meta"]["contentSegment"])
        self.assertTrue(marker["_meta"]["replace"])
        self.assertTrue(marker["content"]["text"].startswith("[turn/diff/updated]\ndiff --git "))
        self.assertIn("\n@@ -1,3 +1,3 @@\n unchanged context\n-old fixture line\n+new fixture line\n trailing context\n",
                      marker["content"]["text"])
        self.assertEqual(("tool_call_update", "codex-failed-command", "failed", 127),
                         (failure["sessionUpdate"], failure["toolCallId"], failure["status"],
                          failure["rawOutput"]["exitCode"]))
        commands = [event["update"] for event in preview.session_events(pair)
                    if event["update"]["sessionUpdate"] == "tool_call" and event["update"].get("kind") == "execute"]
        self.assertGreaterEqual(len(commands), 50)
        self.assertTrue(all(command.get("rawInput", {}).get("command") for command in commands))
        decomposed = next(command["rawInput"] for command in commands if command["toolCallId"] == "tool-long")
        self.assertEqual("pwd && synthetic-preview-check", decomposed["command"])
        self.assertEqual("/synthetic-preview/hey-my-buddy", decomposed["cwd"])
        self.assertEqual(["pwd", "synthetic-preview-check"],
                         [action["command"] for action in decomposed["commandActions"]])
        long_output = next(event["update"] for event in latest["events"]
                           if event["update"].get("toolCallId") == "tool-long"
                           and event["update"].get("_meta", {}).get("replace"))
        output = long_output["content"][0]["content"]["text"]
        self.assertEqual(2400, output.count("完整快照输出行。"))
        self.assertTrue(output.endswith("FULL_OUTPUT_END"))

    def test_session_result_shapes_cover_duplicate_bodies_and_missing_values(self) -> None:
        preview = self.preview

        def updates(pair):
            return [event["update"] for event in preview.session_events(pair)]

        codex = updates(preview.SESSION_LONG)
        command = next(update for update in codex if update.get("toolCallId") == "codex-setup-00"
                       and update["sessionUpdate"] == "tool_call_update")
        self.assertEqual(command["rawOutput"]["aggregatedOutput"], command["content"][0]["content"]["text"])
        no_output = next(update for update in codex if update.get("toolCallId") == "codex-no-output-command"
                         and update["sessionUpdate"] == "tool_call_update")
        self.assertNotIn("content", no_output)
        self.assertEqual({"aggregatedOutput": None, "exitCode": 0, "durationMs": 17, "output": None},
                         no_output["rawOutput"])
        edit = next(update for update in codex if update.get("toolCallId") == "codex-edit-fixture"
                    and update["sessionUpdate"] == "tool_call_update")
        self.assertEqual(edit["rawOutput"]["changes"][0]["diff"], edit["content"][0]["content"]["text"])
        markers = [update for update in codex if update.get("_meta", {}).get("nativeType")
                   in {"subAgentActivity", "contextCompaction"}]
        self.assertEqual(["subAgentActivity", "subAgentActivity", "contextCompaction"],
                         [marker["_meta"]["nativeType"] for marker in markers])
        self.assertTrue(all(marker["content"]["text"] == f'[{marker["_meta"]["nativeType"]}]'
                            for marker in markers))

        claude = updates(preview.SESSION_CLAUDE)
        read = next(update for update in claude if update.get("toolCallId") == "claude-read-guide"
                    and update["sessionUpdate"] == "tool_call_update")
        self.assertEqual(read["rawOutput"], read["content"][0]["content"]["text"])
        zcode = updates(preview.SESSION_ZCODE)
        read = next(update for update in zcode if update.get("toolCallId") == "zcode-read"
                    and update["sessionUpdate"] == "tool_call_update")
        self.assertEqual(read["rawOutput"]["content"], read["content"][0]["content"]["text"])
        self.assertEqual((True, False, 1),
                         (read["rawOutput"]["success"], read["rawOutput"]["truncated"],
                          read["rawOutput"]["lineCount"]))
        running = updates(preview.SESSION_RUNNING)
        self.assertTrue(any(update.get("toolCallId") == "claude-running-pending"
                            and update.get("status") == "in_progress" for update in running))


class PreviewDeletionTest(unittest.TestCase):
    """Exercise only the loopback synthetic server; it never imports product operations."""

    @classmethod
    def setUpClass(cls) -> None:
        spec = importlib.util.spec_from_file_location("synthetic_console_preview", PREVIEW)
        cls.preview = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.preview)

    @contextmanager
    def server(self, scenario: str = "normal"):
        with tempfile.TemporaryDirectory(prefix="hey-my-buddy-preview-http-") as temporary:
            server = self.preview.PreviewHTTPServer(("127.0.0.1", 0), assets=Path(temporary), scenario=scenario, verbose=False)
            thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
            thread.start()
            try:
                yield server
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)
                self.assertFalse(thread.is_alive(), "the private preview thread must stop")

    def call(self, server, path: str, operation: str | None = None, params: dict | None = None,
             *, csrf: str | None = None) -> tuple[int, dict]:
        connection = http.client.HTTPConnection(*server.server_address, timeout=10)
        headers = {"Origin": f"http://127.0.0.1:{server.server_address[1]}",
                   "X-Hey-My-Buddy-CSRF": self.preview.SYNTHETIC_CSRF if csrf is None else csrf,
                   "Content-Type": "application/json"}
        body = json.dumps({"operation": operation, "params": params or {}}) if operation else None
        try:
            connection.request("POST" if operation else "GET", path, body=body, headers=headers)
            response = connection.getresponse()
            self.assertEqual(self.preview.PREVIEW_VALUE, response.getheader(self.preview.PREVIEW_HEADER))
            self.assertIsNone(response.getheader("Set-Cookie"))
            return response.status, json.loads(response.read())
        finally:
            connection.close()

    def test_macro_task_list_sort_matches_requested_order(self) -> None:
        preview = self.preview
        expected = {
            "activity-desc": [preview.MACRO_B, preview.MACRO_A, preview.STANDALONE_GROUP, preview.MACRO_C],
            "activity-asc": [preview.MACRO_C, preview.STANDALONE_GROUP, preview.MACRO_A, preview.MACRO_B],
            "created-desc": [preview.MACRO_A, preview.STANDALONE_GROUP, preview.MACRO_B, preview.MACRO_C],
            "created-asc": [preview.MACRO_C, preview.MACRO_B, preview.STANDALONE_GROUP, preview.MACRO_A],
        }
        with self.server() as server:
            status, default = self.call(server, "/api/macro-tasks")
            self.assertEqual(200, status)
            self.assertEqual(expected["activity-desc"], [row["macroTaskId"] for row in default["macroTasks"]])
            for sort, order in expected.items():
                with self.subTest(sort=sort):
                    status, result = self.call(server, f"/api/macro-tasks?sort={sort}")
                    self.assertEqual(200, status)
                    self.assertEqual(order, [row["macroTaskId"] for row in result["macroTasks"]])
                    self.assertEqual(default["totals"], result["totals"])
            for sort in ("random", "", "created-desc&sort=activity-asc"):
                status, result = self.call(server, f"/api/macro-tasks?sort={sort}")
                self.assertEqual((400, "INVALID_ARGUMENT"), (status, result["error"]["code"]))

    def test_provider_clear_replies_are_flat_and_idempotent(self) -> None:
        with self.server() as server:
            params = {"requestId": "synthetic-clear-1", "harness": "dsh", "provider": "deepseek-official"}
            status, first = self.call(server, "/api/command", "quota_redetect", params)
            self.assertEqual(200, status)
            self.assertEqual({"harness": "dsh", "provider": "deepseek-official", "cleared": True,
                              "duplicate": False}, first["result"])
            _, replay = self.call(server, "/api/command", "quota_redetect", params)
            self.assertEqual({**first["result"], "duplicate": True}, replay["result"])
            _, none = self.call(server, "/api/command", "quota_redetect", {**params, "requestId": "synthetic-clear-2"})
            self.assertEqual({"harness": "dsh", "provider": "deepseek-official", "cleared": False,
                              "duplicate": False}, none["result"])
            _, snapshot = self.call(server, "/api/console")
            self.assertIsNone(snapshot["harnesses"][0]["providers"][0]["coolingUntil"])
            _, expired = self.call(server, "/api/command", "quota_redetect", {
                "requestId": "synthetic-clear-expired", "harness": "zcode", "provider": "zai"})
            self.assertEqual({"harness": "zcode", "provider": "zai", "cleared": True,
                              "duplicate": False}, expired["result"])

    def test_message_list_uses_the_current_task_selector(self) -> None:
        with self.server() as server:
            run_id = 'preview-run-a1'
            status, reply = self.call(server, '/api/command', 'message_list', {'runId': run_id})
            self.assertEqual(status, 200, reply)
            self.assertIn('messages', reply['result'])
            status, reply = self.call(server, '/api/command', 'message_list', {'taskId': run_id})
            self.assertEqual(status, 400, reply)
            self.assertEqual(reply['error']['code'], 'INVALID_ARGUMENT')

    def test_preview_scope_and_blocker_titles(self) -> None:
        with self.server() as server:
            for group in self.preview.FIXTURES["groups"]:
                status, response = self.call(server, "/api/command", "macro_task_deletion_preview", {"macroTaskId": group})
                self.assertEqual(200, status, response)
                preview = response["result"]
                self.assertEqual({"macroTaskId", "microTaskCount", "unconcludedFailureCount", "blockerTotal",
                                  "blockers", "scope", "backupRetention", "deletionState"}, set(preview))
                scope = preview["scope"]
                self.assertEqual({"artifacts", "sessions", "checkouts", "gitRefs"}, set(scope))
                for kind in ("artifacts", "sessions"):
                    self.assertEqual({"count", "bytes"}, set(scope[kind]))
                for value in [*scope["artifacts"].values(), *scope["sessions"].values(), scope["checkouts"], scope["gitRefs"]]:
                    if value is not None:
                        self.assertIs(type(value), int)
                        self.assertGreaterEqual(value, 0)
                self.assertEqual(min(50, preview["blockerTotal"]), len(preview["blockers"]))
                self.assertEqual(sorted(preview["blockers"], key=lambda row: (row["taskId"], row["reason"]["code"])),
                                 preview["blockers"])
                for blocker in preview["blockers"]:
                    self.assertEqual({"runId", "taskId", "title", "titleSource", "state", "runState", "reason", "attemptIds"},
                                     set(blocker))
                    self.assertEqual({"code", "params"}, set(blocker["reason"]))
                    self.assertEqual({}, blocker["reason"]["params"])
                    entry = self.preview.FIXTURES["runs"][blocker["taskId"]]
                    self.assertEqual(entry["title"], blocker["title"])
                    self.assertEqual(entry["titleSource"], blocker["titleSource"])
                if group == self.preview.MACRO_B:
                    long = next(row for row in preview["blockers"] if row["runId"] == "preview-run-b1")
                    self.assertEqual(200, len(long["title"]))
                elif group == self.preview.STANDALONE_GROUP:
                    untitled = next(row for row in preview["blockers"] if row["runId"] == "preview-run-c1-h1")
                    self.assertIsNone(untitled["title"])
                    self.assertEqual("none", untitled["titleSource"])
                elif group == self.preview.MACRO_C:
                    self.assertEqual(0, preview["blockerTotal"])
                    self.assertEqual(1, preview["unconcludedFailureCount"])
                    self.assertGreater(scope["artifacts"]["count"], 0)
                    self.assertEqual(16384, scope["artifacts"]["bytes"])
                    self.assertIsNone(scope["sessions"]["bytes"])
                    self.assertEqual(0, scope["gitRefs"])

    def test_deletion_confirmation_and_existing_safety_boundary(self) -> None:
        with self.server() as server:
            target = self.preview.MACRO_C
            confirmed = {"macroTaskId": target, "microTaskCount": 2, "confirm": True}
            before = self.call(server, "/api/macro-tasks")
            for patch in ({"confirm": False}, {"confirm": 1}, {"confirm": "true"},
                          {"microTaskCount": True}, {"microTaskCount": -1}, {"microTaskCount": 1.5},
                          {"commandId": "not-in-the-contract"}):
                status, response = self.call(server, "/api/command", "macro_task_delete", {**confirmed, **patch})
                self.assertEqual((400, "INVALID_ARGUMENT"), (status, response["error"]["code"]))
            for key in ("confirm", "microTaskCount"):
                params = {name: value for name, value in confirmed.items() if name != key}
                status, response = self.call(server, "/api/command", "macro_task_delete", params)
                self.assertEqual((400, "INVALID_ARGUMENT"), (status, response["error"]["code"]))
            status, response = self.call(server, "/api/command", "macro_task_delete", {**confirmed, "microTaskCount": 1})
            self.assertEqual((409, "REVISION_CONFLICT"), (status, response["error"]["code"]))
            status, response = self.call(server, "/api/command", "macro_task_delete", confirmed, csrf="wrong-synthetic-csrf")
            self.assertEqual((403, "FORBIDDEN"), (status, response["error"]["code"]))
            for group in (self.preview.MACRO_A, self.preview.MACRO_B, self.preview.STANDALONE_GROUP):
                count = len(self.preview.FIXTURES["groups"][group])
                status, response = self.call(server, "/api/command", "macro_task_delete",
                                             {**confirmed, "macroTaskId": group, "microTaskCount": count})
                self.assertEqual((409, "MACRO_TASK_NOT_SETTLED"), (status, response["error"]["code"]))
                self.assertGreater(response["error"]["details"]["blockerTotal"], 0)
            status, response = self.call(server, "/api/command", "workflow_cancel", {"runId": "preview-run-d1"})
            self.assertEqual((404, "METHOD_NOT_FOUND"), (status, response["error"]["code"]))
            self.assertEqual(before, self.call(server, "/api/macro-tasks"))

    def test_deleted_target_disappears_from_later_reads_without_changing_other_instances(self) -> None:
        with self.server() as server, self.server() as untouched:
            target = self.preview.MACRO_C
            _, before = self.call(server, "/api/macro-tasks")
            _, snapshot_before = self.call(server, "/api/console")
            confirmed = {"macroTaskId": target, "microTaskCount": 2, "confirm": True}
            status, response = self.call(server, "/api/command", "macro_task_delete", confirmed)
            self.assertEqual(200, status, response)
            receipt = response["result"]
            self.assertEqual({"deletionId", "macroTaskId", "microTaskCount", "state", "deletedAt", "duplicate", "skipped"}, set(receipt))
            self.assertEqual("deleted", receipt["state"])
            self.assertFalse(receipt["duplicate"])
            self.assertEqual([], receipt["skipped"])
            _, after = self.call(server, "/api/macro-tasks")
            self.assertNotIn(target, [row["macroTaskId"] for row in after["macroTasks"]])
            self.assertEqual(before["totals"]["all"] - 1, after["totals"]["all"])
            self.assertEqual(before["cursor"] + 1, after["cursor"])
            _, snapshot_after = self.call(server, "/api/console")
            self.assertEqual(snapshot_before["eventCursor"] + 1, snapshot_after["eventCursor"])
            self.assertEqual(snapshot_before["tasks"], snapshot_after["tasks"])
            for run_id in ("preview-run-d1", "preview-run-d2"):
                self.assertEqual(404, self.call(server, f"/api/tasks/{run_id}")[0])
                for operation in ("workflow_get", "message_list"):
                    self.assertEqual(404, self.call(server, "/api/command", operation, {"runId": run_id})[0])
            self.assertEqual(404, self.call(server, f"/api/macro-tasks/{quote(target, safe='')}/timeline")[0])
            for operation, params in (("macro_task_deletion_preview", {"macroTaskId": target}),
                                      ("macro_task_delete", confirmed)):
                status, response = self.call(server, "/api/command", operation, params)
                self.assertEqual(404, status, response)
                self.assertEqual("NOT_FOUND", response["error"]["code"])
            self.assertEqual(after, self.call(server, "/api/macro-tasks")[1])
            self.assertEqual(before, self.call(untouched, "/api/macro-tasks")[1])

    def test_error_scenario_never_applies_a_deletion(self) -> None:
        with self.server("error") as server:
            before = self.call(server, "/api/macro-tasks")
            for operation in ("macro_task_deletion_preview", "macro_task_delete"):
                params = {"macroTaskId": self.preview.MACRO_C}
                if operation == "macro_task_delete":
                    params.update(microTaskCount=2, confirm=True)
                status, response = self.call(server, "/api/command", operation, params)
                self.assertEqual((503, "SERVICE_UNAVAILABLE"), (status, response["error"]["code"]))
            self.assertEqual(before, self.call(server, "/api/macro-tasks"))


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).parent))
    unittest.main()
