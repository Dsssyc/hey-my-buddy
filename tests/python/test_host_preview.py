#!/usr/bin/env python3
"""Preview-fixture regression for the ADR-018 console surface.

The synthetic preview data (`tests/probes/objective_console_preview.py`) once
lagged behind the console schema, and the new console refused to load it. This
test closes that gap:

1. the preview script's own ``--check`` shape/coverage invariants pass;
2. the emitted fixture tree is deterministic, so a shape change is reproducible;
3. the emitted tree is consumed by the *real* frontend parsers — not by a
   Python re-implementation — through ``createApi(...).snapshot()``,
   ``.objectives()``, ``.objectiveTimeline()``, the ``workflow_get`` command
   path and ``parseWorkflowReply``, and the resulting report is asserted here.
   Four preview scenarios (normal/readonly/truncated/error) stay covered, and
   deliberately incompatible shapes are refused instead of silently accepted.

The frontend check needs the console's dev dependencies (``npm ci`` in
``apps/console``); a missing toolchain fails loudly rather than skipping,
because a skip would silently drop the cross-boundary guard.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PREVIEW = ROOT / "tests" / "probes" / "objective_console_preview.py"
CONSOLE = ROOT / "apps" / "console"
FRONTEND_TEST = "src/preview-contract.test.ts"
VITEST = CONSOLE / "node_modules" / "vitest" / "vitest.mjs"

#: Variables an outer hey-my-buddy process may export; a child test never
#: inherits them (AGENTS.md verification rules).
_INHERITED_KEYS = (
    "BUDDY_STATE_DIR", "BUDDY_RUNTIME_ROOT", "BUDDY_RUNTIME", "BUDDY_RUNTIME_IDENTITY",
    "BUDDY_WORKER_STATE", "BUDDY_WORKER_ID", "BUDDY_AGENT_CREDENTIAL",
    "BUDDY_AGENT_CREDENTIAL_FILE", "VIRTUAL_ENV", "UV_PROJECT_ENVIRONMENT",
)


def child_environment(overrides: dict[str, str] | None = None) -> dict[str, str]:
    environment = {key: value for key, value in os.environ.items() if key not in _INHERITED_KEYS}
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
        with tempfile.TemporaryDirectory(prefix="buddy-preview-") as temporary:
            first, second = Path(temporary) / "first", Path(temporary) / "second"
            emit_fixtures(first)
            emit_fixtures(second)
            self.assertEqual(tree_files(first), tree_files(second))

    def test_frontend_parsers_consume_the_emitted_fixtures(self) -> None:
        node = os.environ.get("BUDDY_NODE") or shutil.which("node")
        self.assertTrue(node, "Node.js is required to run the console parser test")
        self.assertTrue(
            VITEST.is_file(),
            "console dev dependencies are not installed, so the preview fixtures cannot be consumed "
            f"by the real frontend parser ({VITEST}); run `npm ci` in {CONSOLE.relative_to(ROOT)}",
        )
        with tempfile.TemporaryDirectory(prefix="buddy-preview-") as temporary:
            workspace = Path(temporary)
            emitted = workspace / "fixtures"
            manifest = emit_fixtures(emitted)
            report_path = workspace / "report.json"
            result = subprocess.run(
                [str(node), str(VITEST), "run", FRONTEND_TEST],
                cwd=str(CONSOLE),
                env=child_environment({
                    "BUDDY_PREVIEW_FIXTURES": str(emitted),
                    "BUDDY_PREVIEW_REPORT": str(report_path),
                    "CI": "1",
                    "NO_COLOR": "1",
                }),
                capture_output=True, text=True, timeout=600,
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
        self.assertEqual("tests/probes/objective_console_preview.py", manifest["source"])
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

        # All four preview scenarios stay covered, and the error scenario stays visible.
        scenarios = {entry["scenario"] for entry in manifest["files"]}
        self.assertEqual({"normal", "readonly", "truncated", "error", "incompatible"}, scenarios)
        readonly = outcomes["readonly/console.json"]
        self.assertEqual("INVALID_RESPONSE", readonly["code"])
        truncated = outcomes["truncated/timeline-obj-a.json"]
        self.assertTrue(truncated["facts"]["truncated"]["rows"])
        for path in ("error/timeline-obj-a.json", "error/workflow-preview-run-a1.json"):
            self.assertEqual("SERVICE_UNAVAILABLE", outcomes[path]["code"])
        self.assertTrue(outcomes["error/console.json"]["accepted"])

        # The current two-Router + family-note snapshot shape the parser requires.
        normal = outcomes["normal/console.json"]["facts"]
        configuration = normal["configuration"]
        self.assertNotIn("decisionProfileId", configuration)
        for key in ("fastRouterProfileId", "reviewRouterProfileId", "defaultRoutingMode", "routingBudget"):
            self.assertIn(key, configuration)
        self.assertTrue(normal["taskCount"] > 0)

        # Recorded quota: a near-limit window, a stale unknown window, and a harness
        # with no observation at all are three distinct, explicit states.
        quotas = [row["quota"] for row in normal["harnesses"]]
        near = [quota for quota in quotas if quota and quota["alert"]]
        self.assertTrue(near, "the preview must record one near-limit quota window")
        self.assertTrue(any(window["nearLimit"] for quota in near for window in quota["windows"]))
        self.assertTrue(any(window["usedPercent"] >= 90 for quota in near for window in quota["windows"]))
        stale = [quota for quota in quotas if quota and quota["stale"]]
        self.assertTrue(stale, "the preview must record one stale quota observation")
        self.assertTrue(any(window["usedPercent"] is None for quota in stale for window in quota["windows"]))
        self.assertFalse(any(window["nearLimit"] for quota in stale for window in quota["windows"]
                             if window["usedPercent"] is None))
        self.assertIn(None, quotas, "the preview must record a harness without any quota observation")
        for quota in quotas:
            if quota is None:
                continue
            self.assertTrue(isinstance(quota["source"], str) and quota["source"],
                            "a recorded observation names its source")
            self.assertTrue(quota["windows"], "a recorded observation carries its windows")
            self.assertIn("实时", quota["note"], "the note must not claim live account quota")
            self.assertIn("不代表", quota["note"], "the note must state what the observation is not")

        # Workflow facts straight from the frontend parser's report.
        workflows = {path: outcome["facts"] for path, outcome in outcomes.items()
                     if outcome["kind"] == "workflow-get" and outcome["accepted"]}
        self.assertTrue(workflows)
        locked = {facts["configurationLocked"] for facts in workflows.values()}
        self.assertIn(True, locked, "one goal must record the user-locked configuration")
        self.assertIn(False, locked, "one goal must record a Host-supplied configuration")
        for facts in workflows.values():
            self.assertIn("hostConclusion", facts)

        conclusions = [(path, facts["hostConclusion"]) for path, facts in workflows.items()
                       if facts["hostConclusion"]]
        self.assertTrue(conclusions, "the preview must record a failed or cancelled Host conclusion")
        for _path, conclusion in conclusions:
            self.assertIn(conclusion["statusLabel"], {"失败", "已取消"})
            self.assertTrue(conclusion["evidence"])
        # A quota failure keeps its real failure label and its partial output: the
        # conclusion records the outcome and never turns the goal into a success.
        failed_path, failed_conclusion = next(
            (path, conclusion) for path, conclusion in conclusions if conclusion["statusLabel"] == "失败")
        self.assertTrue(failed_conclusion["failed"])
        self.assertFalse(failed_conclusion["cancelled"])
        fixture = json.loads((fixtures / failed_path).read_text(encoding="utf-8"))
        self.assertEqual("failed", fixture["task"]["status"])
        self.assertEqual("failed", fixture["state"])
        self.assertNotEqual("accepted", fixture["task"].get("acceptanceVerdict"))
        self.assertTrue(any(item["kind"] == "partial-output" and item.get("partial") is True
                            and item.get("verified") is False and item.get("final") is False
                            for item in fixture["artifacts"]),
                        "a quota failure must keep its sealed partial output")

        # Per-execution usage: known values name the cache subset, and an unrecorded
        # execution stays unknown instead of reading as zero.
        usages = [turn["tokenUsage"] for facts in workflows.values() for turn in facts["turns"]]
        titles = [turn["tokenUsageTitle"] for facts in workflows.values() for turn in facts["turns"]]
        self.assertTrue(any("含缓存" in usage for usage in usages))
        self.assertTrue(any("未知" in usage for usage in usages))
        self.assertTrue(all("单次执行" in title for title in titles),
                        "every usage line must state its per-execution scope")
        self.assertFalse(any("会话累计：" in usage for usage in usages),
                         "no usage line may be a session cumulative total")

        # Partial outputs are marked unverified and non-final, and never become the
        # goal's final artifact; cumulative patches keep their fixed base commit.
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
                                     f"{path}: cumulative base must be the goal input commit")

        host_paths = [entry for facts in workflows.values() for entry in facts["hostPaths"]]
        self.assertTrue(any(host_paths), "the preview must record one integration with Host paths")
        self.assertTrue(any(not entry for entry in host_paths), "the preview must also keep Host paths empty")


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).parent))
    unittest.main()
