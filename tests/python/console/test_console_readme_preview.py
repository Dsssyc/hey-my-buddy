"""README story retains the ownership, ordering and result evidence it depicts."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import subprocess
import tempfile
import unittest

PROBES = Path(__file__).resolve().parents[2] / "probes"


def load(name):
    spec = importlib.util.spec_from_file_location(name, PROBES / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class ReadmeSceneTests(unittest.TestCase):
    def scene(self, language):
        preview = load("macro_task_console_preview")
        load("readme_console_scene").configure(preview, language)
        return preview

    def test_readme_scenario_supports_the_existing_read_only_smoke_mode(self):
        with tempfile.TemporaryDirectory(prefix="readme-preview-assets-") as assets:
            for language in ("en", "zh"):
                with self.subTest(language=language):
                    result = subprocess.run([sys.executable, str(PROBES / "macro_task_console_preview.py"),
                                             "--scenario", "readme", "--language", language,
                                             "--assets", assets, "--smoke"],
                                            capture_output=True, text=True, timeout=30)
                    self.assertEqual(0, result.returncode, result.stdout + result.stderr)
                    self.assertIn("preview smoke: 0 failure(s) with scenario=readme", result.stdout)

    def test_turns_and_artifacts_have_ordered_evidence(self):
        for language in ("en", "zh"):
            with self.subTest(language=language):
                p = self.scene(language)
                timeline = p.macro_task_timeline({"macroTaskId": p.MACRO_A}, "readme")
                self.assertEqual(9, len(timeline["rows"]))
                self.assertTrue(timeline["scopeComplete"])
                for entry in p.FIXTURES["runs"].values():
                    previous_end = None
                    attempts = {item["attemptId"]: item for item in entry["attempts"]}
                    for item in entry["attempts"]:
                        self.assertLessEqual(entry["createdAt"], item["startedAt"])
                        if previous_end is not None:
                            self.assertLessEqual(previous_end, item["startedAt"])
                        if item["finishedAt"] is not None:
                            self.assertLessEqual(item["startedAt"], item["finishedAt"])
                            self.assertTrue(item["shutdownConfirmed"])
                        previous_end = item["finishedAt"]
                    for artifact in entry["artifacts"]:
                        self.assertEqual(attempts[artifact["attemptId"]]["finishedAt"], artifact["createdAt"])
                    if entry["state"] in ("delivered", "accepted"):
                        view = p.workflow_view(entry)
                        self.assertIsNotNone(view["finalArtifactId"])
                        self.assertEqual(entry["attempts"][-1]["attemptId"], view["finalAttemptId"])
                    if entry["acceptedAt"]:
                        accepted = next(event for event in entry["markers"] if event["kind"] == "accept")
                        integrated = next(record for record in entry["integrations"]
                                          if record["integrationId"] == accepted["integrationId"])
                        self.assertEqual(entry["artifacts"][-1]["artifactId"], integrated["artifactId"])
                        self.assertEqual(entry["attempts"][-1]["attemptId"], accepted["attemptId"])
                        self.assertEqual(entry["acceptedAt"], integrated["createdAt"])
                    if entry["leadOwner"]:
                        lead = p.FIXTURES["runs"][entry["leadOwner"][0]]
                        self.assertTrue(lead["delegationAuthority"])
                        turn = next(item for item in lead["attempts"] if item["turnId"] == entry["leadOwner"][1])
                        self.assertLessEqual(turn["startedAt"], entry["createdAt"])
                        self.assertIsNone(turn["finishedAt"])
                    if entry["activeRequest"]:
                        self.assertEqual("assistance", entry["attempts"][-1]["disposition"])
                        self.assertEqual(entry["attempts"][-1]["finishedAt"], entry["activeRequest"]["createdAt"])

    def test_rejection_changes_buddy_in_the_same_task_and_preserves_event_order(self):
        p = self.scene("en")
        entry = p.FIXTURES["runs"]["readme-tokenizer"]
        self.assertEqual(["zcode", "claude"], [item["buddy"]["harness"] for item in entry["attempts"]])
        reject, continued = [item for item in entry["markers"] if item["kind"] in ("reject", "continue")]
        self.assertEqual("workflow.review_rejected", reject["eventKind"])
        self.assertEqual(reject["at"], continued["at"])
        self.assertLess(reject["seq"], continued["seq"])
        self.assertLess(continued["at"], entry["attempts"][1]["startedAt"])
        self.assertEqual(entry["attempts"][1]["attemptId"], p.workflow_view(entry)["finalAttemptId"])
        self.assertEqual("readme-tokenizer-v2", p.workflow_view(entry)["finalArtifactId"])
        target = p.FIXTURES["runs"][p.PREVIEW_FIXES["readme-corpus-fix"]]
        self.assertEqual("failed", target["hostConclusion"]["executionStatus"])
        self.assertEqual(("concluded", "concluded"), (target["state"], target["status"]))
        conclusion = next(event for event in target["markers"] if event["kind"] == "conclude")
        self.assertEqual(target["attempts"][0]["attemptId"], conclusion["attemptId"])

    def test_authored_titles_and_clock_are_localized_and_repeatable(self):
        for language, title in (("en", "Ship offline search"), ("zh", "交付离线搜索")):
            p = self.scene(language)
            first = p.encode_json(p.macro_task_timeline({"macroTaskId": p.MACRO_A}, "readme"))
            self.assertEqual(title, p.MACRO_TASKS[p.MACRO_A]["title"])
            self.assertTrue(all(project["path"].startswith("~/") for project in p.PROJECTS.values()))
            p = self.scene(language)
            self.assertEqual(first, p.encode_json(p.macro_task_timeline({"macroTaskId": p.MACRO_A}, "readme")))
