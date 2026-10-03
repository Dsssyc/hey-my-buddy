"""First-install distribution: the shared skill in private agent/Claude skill homes.

Every test points ``BUDDY_AGENT_SKILLS_DIR``, ``BUDDY_CLAUDE_SKILLS_DIR`` and
``BUDDY_STATE_DIR`` at private directories, so the operator's ``~/.agents``,
``~/.claude`` and daily board are never read or written. No service is running in
these roots, so installation places the skill and link without an upgrade.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from install.test_packaging import LAUNCHER, ROOT, current_environment, load_build_skill

OPERATIONS = ROOT / "docs" / "reference" / "operations.md"


class SkillInstallTests(unittest.TestCase):
    def setUp(self):
        self.base = Path(tempfile.mkdtemp(prefix="buddy-install-")).resolve()
        self.addCleanup(self.cleanup_private)
        self.agents = self.base / "agents/skills"
        self.claude = self.base / "claude/skills"
        self.environment = {"BUDDY_AGENT_SKILLS_DIR": str(self.agents),
                            "BUDDY_CLAUDE_SKILLS_DIR": str(self.claude),
                            "BUDDY_STATE_DIR": str(self.base / "state"),
                            "BUDDY_RUNTIME_ROOT": str(self.base / 'runtime'),
                            'BUDDY_MAX_CONCURRENT': '1', 'BUDDY_CONSOLE_PORT': '0',
                            "CODEX_HOME": str(self.base / "codex")}

    def cleanup_private(self):
        from hey_my_buddy.cli.checks import teardown_private_root
        self.assertIsNone(teardown_private_root(self.base), 'Private install did not stop; evidence retained')

    def install(self, **extra):
        from hey_my_buddy.install.skill_install import install
        with patch.dict(os.environ, {**self.environment, **extra}):
            return install({})

    def test_first_install_places_the_skill_and_links_claude(self):
        result = self.install()
        skill = self.agents / "buddy"
        self.assertEqual(result["skill"]["placement"], "installed")
        self.assertTrue((skill / "SKILL.md").is_file())
        self.assertTrue(os.access(skill / "scripts/buddy", os.X_OK))
        self.assertEqual(result["claude"]["status"], "linked")
        self.assertTrue((self.claude / "buddy").is_symlink())
        self.assertEqual(os.path.realpath(self.claude / "buddy"), os.path.realpath(skill))
        self.assertEqual(result["service"]["action"], "none")
        self.assertNotIn("legacyPlugins", result)

    def test_a_second_host_with_the_same_version_only_repairs_the_link(self):
        self.install()
        before = (self.agents / "buddy/skill.json").stat().st_mtime_ns
        (self.claude / "buddy").unlink()
        result = self.install()
        self.assertEqual(result["skill"]["placement"], "already-current")
        self.assertEqual((self.agents / "buddy/skill.json").stat().st_mtime_ns, before)
        self.assertEqual(result["claude"]["status"], "linked")
        self.assertEqual(self.install()["claude"]["status"], "already-linked")

    def test_a_different_version_replaces_the_skill_without_leftovers(self):
        self.install()
        marker = self.agents / "buddy/skill.json"
        stale = json.loads(marker.read_text())
        marker.write_text(json.dumps({**stale, "version": "0.0.1"}))
        self.assertEqual(self.install()["skill"]["placement"], "updated")
        self.assertEqual(json.loads(marker.read_text())["version"], stale["version"])
        self.assertEqual(sorted(path.name for path in self.agents.iterdir() if not path.name.endswith(".lock")), ["buddy"])

    def test_foreign_directories_are_never_replaced(self):
        from hey_my_buddy.errors import BoardError
        (self.claude / "buddy").mkdir(parents=True)
        with self.assertRaises(BoardError) as caught:
            self.install()
        self.assertEqual(caught.exception.code, "SKILL_TARGET_CONFLICT")
        self.assertFalse((self.claude / "buddy").is_symlink())
        (self.claude / "buddy").rmdir()
        self.assertFalse((self.agents / "buddy").exists())
        (self.agents / "buddy").mkdir()
        (self.agents / "buddy/SKILL.md").write_text("---\nname: someone-else\ndescription: x\n---\n")
        with self.assertRaises(BoardError) as caught:
            self.install()
        self.assertEqual(caught.exception.code, "SKILL_TARGET_CONFLICT")

    def test_a_worker_credential_cannot_install(self):
        from hey_my_buddy.errors import BoardError
        with self.assertRaises(BoardError) as caught:
            self.install(BUDDY_AGENT_CREDENTIAL="attempt-token")
        self.assertEqual(caught.exception.code, "UNAUTHORIZED")
        self.assertFalse(self.agents.exists())

    def test_an_installed_skill_copies_itself_and_reports_a_legacy_plugin(self):
        built = load_build_skill().build(ROOT, self.base / "built/buddy")
        (self.base / "codex/plugins/cache/personal/hey-my-buddy").mkdir(parents=True)
        with patch("hey_my_buddy.install.skill_install.project_root", return_value=built / "package"):
            result = self.install()
        self.assertEqual(result["skill"]["placement"], "installed")
        self.assertEqual(json.loads((self.agents / "buddy/skill.json").read_text()),
                         json.loads((built / "skill.json").read_text()))
        self.assertEqual(result["legacyPlugins"], [str(self.base / "codex/plugins/cache/personal/hey-my-buddy")])
        self.assertIn("codex plugin remove", result["next"])

    def test_a_failed_claude_link_fails_before_the_service_and_rerun_recovers(self):
        from hey_my_buddy.errors import BoardError
        with patch("pathlib.Path.symlink_to", side_effect=OSError(1, "Operation not permitted")), \
                patch("hey_my_buddy.install.upgrade.upgrade") as service:
            with self.assertRaises(BoardError) as caught:
                self.install()
            service.assert_not_called()
        self.assertEqual(caught.exception.code, "CLAUDE_LINK_FAILED")
        self.assertFalse((self.agents / "buddy").exists())
        self.assertFalse((self.claude / "buddy").exists())
        result = self.install()
        self.assertEqual(result["skill"]["placement"], "installed")
        self.assertEqual(result["claude"]["status"], "linked")

    def test_windows_data_root_is_local_app_data(self):
        from pathlib import PurePosixPath
        from hey_my_buddy import home
        # Only the branch is exercised here; a WindowsPath cannot exist on POSIX.
        with patch.object(home.os, "name", "nt"), patch.object(home, "Path", PurePosixPath), \
                patch.dict(os.environ, {"LOCALAPPDATA": "/Local"}):
            self.assertEqual(home.default_state_dir(), PurePosixPath("/Local/hey-my-buddy/state"))
            self.assertEqual(home.default_runtime_root(), PurePosixPath("/Local/hey-my-buddy/runtime"))
        with patch.object(home.os, "name", "posix"):
            self.assertEqual(home.data_root(), Path.home() / ".local/share/hey-my-buddy")

    def test_paths_reports_skill_data_and_runtime_locations_without_starting_anything(self):
        from hey_my_buddy.install.skill_install import paths
        with patch.dict(os.environ, self.environment):
            before = paths({})
            self.install()
            after = paths({})
        self.assertFalse(before["skill"]["installed"])
        self.assertEqual(before["claude"]["status"], "missing")
        self.assertTrue(after["skill"]["installed"])
        self.assertEqual(after["claude"]["status"], "linked")
        self.assertEqual(after["skill"]["launcher"], str(self.agents / "buddy/scripts/buddy"))
        state = os.path.realpath(self.base / "state")
        self.assertEqual(after["data"]["stateDir"], state)
        self.assertEqual(after["data"]["board"], os.path.join(state, "board.sqlite3"))
        self.assertTrue(Path(after["runtime"]["active"]).is_relative_to(self.base / 'runtime'))
        self.assertTrue((self.base / "state/active-runtime.json").is_file())
        self.assertFalse((self.base / 'state/board.sqlite3').exists())

    def test_the_repository_launcher_installs_from_any_cwd(self):
        cwd = self.base / "elsewhere"
        cwd.mkdir()
        completed = subprocess.run(
            ["/bin/sh", str(LAUNCHER), "install"], cwd=cwd, capture_output=True, text=True, timeout=240,
            env=current_environment(**self.environment, BUDDY_DEV_SOURCE="1",
                                    UV_PROJECT_ENVIRONMENT=str(self.base / "cli-venv")),
        )
        self.assertEqual(completed.returncode, 0, completed.stderr + completed.stdout)
        result = json.loads(completed.stdout)
        self.assertEqual(result["skill"]["placement"], "installed")
        self.assertTrue((self.agents / "buddy/scripts/buddy").is_file())


class DocumentationTests(unittest.TestCase):
    """Both READMEs and the owning reference describe the same first-install path."""

    def test_operations_document_the_shared_skill_install(self):
        text = OPERATIONS.read_text()
        self.assertIn("hey_my_buddy.protocol.rpc_config", text)
        self.assertIn("## Installation", text)
        for fragment in ("~/.agents/skills/buddy", "~/.claude/skills/buddy", "scripts/buddy install",
                         "packaging/build-skill.py", "codex plugin remove"):
            self.assertIn(fragment, text)
        self.assertNotIn("codex plugin add", text)

    def test_readmes_keep_first_install_parity(self):
        for name in ("README.md", "README.zh-CN.md"):
            text = (ROOT / name).read_text()
            self.assertIn("uvx hey-my-buddy@0.21.0 install", text, name)
            self.assertIn("~/.agents/skills/buddy", text, name)
            self.assertIn("docs/reference/operations.md#installation", text, name)
            self.assertNotIn("codex plugin add", text, name)


if __name__ == "__main__":
    unittest.main()
