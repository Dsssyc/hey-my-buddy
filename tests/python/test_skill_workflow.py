"""ADR-018 item 11: the shared skill keeps only the long-lived Host workflow.

SKILL.md must stay near 4 KB, carry no version or installation status, keep the
core delegation workflow (routing health, one work objective per agenda, waiting
pointers, Host acceptance) and route every detail into the shipped references. The
tests read the source and the packaging rewrite only; they install nothing and
change no user setting.
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

import hey_my_buddy.install.skill_package as skill_package

ROOT = Path(__file__).resolve().parents[2]
SKILL = ROOT / "skills" / "buddy" / "SKILL.md"
REFERENCES = ROOT / "docs" / "reference"
MAX_SKILL_BYTES = 4 * 1024
LINK = re.compile(r"\[([^\]]+)\]\(([^)\s]+)\)")


class SkillSizeTests(unittest.TestCase):
    def test_skill_stays_near_four_kilobytes(self):
        self.assertLessEqual(SKILL.stat().st_size, MAX_SKILL_BYTES, "SKILL.md grew past its ~4 KB budget")

    def test_version_and_installation_status_are_gone(self):
        text = SKILL.read_text(encoding="utf-8")
        self.assertIsNone(re.search(r"\b\d+\.\d+\.\d+\b", text), "SKILL.md must not name a release version")
        for removed in ("contract 0.", "schema 1", "尚未安装", "not yet installed", "already-current",
                        "active-runtime.json", "BUDDY_DEV_SOURCE", "uvx ", "SOURCE_DATE_EPOCH"):
            with self.subTest(removed=removed):
                self.assertNotIn(removed, text)
        # The packaging step adds the version marker; the source must not carry one.
        self.assertNotIn("metadata:", text)

    def test_frontmatter_still_names_the_skill_and_its_trigger(self):
        text = SKILL.read_text(encoding="utf-8")
        self.assertTrue(text.startswith("---\n"))
        head = text.split("\n---\n", 1)[0]
        self.assertIn("\nname: buddy", head)
        self.assertIn("\ndescription: ", head)


class SkillWorkflowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.text = SKILL.read_text(encoding="utf-8")

    def test_the_core_workflow_survives(self):
        for marker in (
            "health",  # routing state is read before routing is trusted
            "routingMode",  # the recorded mode is reported honestly
            "objectiveId",
            'objectiveOf: "<runId>"',  # joining an existing run's objective
            "controlFile",
            "integration-record",
            "acknowledge",
            "finalArtifactId",
            "await",
            "workspace-cleanup-plan",
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, self.text)

    def test_waiting_pointers_name_codex_and_claude_paths(self):
        """The skill routes waiting to the per-Host guides instead of describing either flow."""
        for guide in ("host-codex.md", "host-claude-code.md"):
            with self.subTest(guide=guide):
                self.assertIn(f"(../../docs/reference/{guide})", self.text)
        self.assertNotIn("#waiting-from-", self.text)

    def test_launcher_help_and_the_three_input_modes_are_documented(self):
        for marker in ("--params-file", "stdin", "buddy help", "output"):
            with self.subTest(marker=marker):
                self.assertIn(marker, self.text)

    def test_host_authority_and_acceptance_stay_explicit(self):
        self.assertIn("acceptance and authorization", self.text)
        self.assertIn("not acceptance", self.text)

    def test_details_keep_a_destination(self):
        """Everything moved out of SKILL.md still has an owning reference page."""
        for relative, marker in (
            ("operations.md", "installation"),
            ("operations.md", "backup"),
            ("console.md", "console"),
            ("workflow.md", "routing"),
            ("evaluation-maintenance.md", "assessment"),
            ("harnesses.md", "unavailable"),
        ):
            with self.subTest(reference=relative, marker=marker):
                page = (REFERENCES / relative).read_text(encoding="utf-8")
                self.assertIn(marker, page.lower())


class HostGuideTests(unittest.TestCase):
    """Each per-Host guide carries only its own waiting flow, sandbox allowance and limits."""

    MARKERS = {
        "host-codex.md": (
            "monitoring-only native subagent",  # one monitor per running delegation
            "fork_turns",  # the monitor spawns with an empty history
            "wait-timeout",  # the three separate wait limits
            "not a monitor failure",  # slow work keeps its monitor instead of moving to the foreground
            "~/.codex/rules",  # launcher sandbox allowance
            "prefix_rule",
            "not wake a closed Host session",  # the turn-end limit
        ),
        "host-claude-code.md": (
            "background `await`",  # one background wait per running goal
            "run_in_background: true",
            "wait-timeout",
            "sandbox.excludedCommands",  # launcher sandbox allowance
            "permissions.allow",
            "ANTHROPIC_BASE_URL",  # the cold-start limit for Claude Workers
        ),
    }

    def test_each_guide_carries_its_own_host_instructions(self):
        for name, markers in self.MARKERS.items():
            page = (REFERENCES / name).read_text(encoding="utf-8")
            for marker in markers:
                with self.subTest(guide=name, marker=marker):
                    self.assertIn(marker, page)

    def test_neither_guide_describes_the_other_host(self):
        with self.subTest(guide="host-codex.md"):
            self.assertNotIn("Claude", (REFERENCES / "host-codex.md").read_text(encoding="utf-8"))
        with self.subTest(guide="host-claude-code.md"):
            self.assertNotIn("Codex", (REFERENCES / "host-claude-code.md").read_text(encoding="utf-8"))


class SkillLinkTests(unittest.TestCase):
    def test_every_skill_link_points_into_a_shipped_reference_page(self):
        text = SKILL.read_text(encoding="utf-8")
        targets = LINK.findall(text)
        self.assertTrue(targets, "SKILL.md must route details into the references")
        for label, target in targets:
            with self.subTest(label=label):
                if re.match(r"[a-z]+://", target) or target.startswith("#"):
                    continue
                rewritten = skill_package._skill_link(target)
                self.assertIsNotNone(rewritten, f"{target} would be dropped from the installed skill")
                page = REFERENCES / rewritten.removeprefix("references/").split("#", 1)[0]
                self.assertTrue(page.is_file(), f"{target} has no owning reference page")

    def test_the_packaging_rewrite_keeps_every_link_inside_the_skill(self):
        installed = skill_package._rewrite(SKILL.read_text(encoding="utf-8"), skill_package._skill_link)
        for label, target in LINK.findall(installed):
            with self.subTest(label=label):
                if re.match(r"[a-z]+://", target):
                    continue
                self.assertFalse(target.startswith(".."), target)
                page = target.split("#", 1)[0]
                self.assertTrue((REFERENCES / page.removeprefix("references/")).is_file(), target)


if __name__ == "__main__":
    unittest.main()
