"""First-install distribution: the shared skill in private agent/Claude skill homes.

Every test points ``HMB_AGENT_SKILLS_DIR``, ``HMB_CLAUDE_SKILLS_DIR`` and
``HMB_STATE_DIR`` at private directories, so the operator's ``~/.agents``,
``~/.claude`` and daily board are never read or written. No service is running in
these roots, so installation places the skill and link without an upgrade.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from install.test_packaging import ROOT, current_environment, launcher_argv, load_build_skill

OPERATIONS = ROOT / "docs" / "reference" / "operations.md"


class SkillInstallTests(unittest.TestCase):
    def setUp(self):
        self.base = Path(tempfile.mkdtemp(prefix="hey-my-buddy-install-")).resolve()
        self.addCleanup(self.cleanup_private)
        self.agents = self.base / "agents/skills"
        self.claude = self.base / "claude/skills"
        self.environment = {"HMB_AGENT_SKILLS_DIR": str(self.agents),
                            "HMB_CLAUDE_SKILLS_DIR": str(self.claude),
                            "HMB_STATE_DIR": str(self.base / "state"),
                            "HMB_RUNTIME_ROOT": str(self.base / 'runtime'),
                            'HMB_MAX_CONCURRENT': '1', 'HMB_CONSOLE_PORT': '0',
                            "CODEX_HOME": str(self.base / "codex")}

    def cleanup_private(self):
        from support import cleanup_private_tree

        cleanup_private_tree(self.base, (self.base / "state",))

    def install(self, **extra):
        from hey_my_buddy.install.skill_install import install
        with patch.dict(os.environ, {**self.environment, **extra}):
            return install({})

    def test_first_install_places_the_skill_and_links_claude(self):
        result = self.install()
        skill = self.agents / "hey-my-buddy"
        self.assertEqual(result["skill"]["placement"], "installed")
        self.assertTrue((skill / "SKILL.md").is_file())
        self.assertTrue(os.access(skill / "scripts/hey-my-buddy", os.X_OK))
        self.assertEqual(result["claude"]["status"], "linked")
        link = self.claude / "hey-my-buddy"
        self.assertTrue(link.is_junction() if os.name == 'nt' else link.is_symlink())
        self.assertEqual(os.path.realpath(self.claude / "hey-my-buddy"), os.path.realpath(skill))
        self.assertEqual(result["service"]["action"], "none")

    def test_a_second_host_with_the_same_version_only_repairs_the_link(self):
        self.install()
        before = (self.agents / "hey-my-buddy/skill.json").stat().st_mtime_ns
        link = self.claude / "hey-my-buddy"
        link.rmdir() if os.name == 'nt' else link.unlink()
        result = self.install()
        self.assertEqual(result["skill"]["placement"], "already-current")
        self.assertEqual((self.agents / "hey-my-buddy/skill.json").stat().st_mtime_ns, before)
        self.assertEqual(result["claude"]["status"], "linked")
        self.assertEqual(self.install()["claude"]["status"], "already-linked")

    def test_a_different_version_replaces_the_skill_without_leftovers(self):
        self.install()
        marker = self.agents / "hey-my-buddy/skill.json"
        stale = json.loads(marker.read_text(encoding="utf-8"))
        marker.write_text(json.dumps({**stale, "version": "0.0.1"}), encoding="utf-8", newline="\n")
        self.assertEqual(self.install()["skill"]["placement"], "updated")
        self.assertEqual(json.loads(marker.read_text(encoding="utf-8"))["version"], stale["version"])
        self.assertEqual(sorted(path.name for path in self.agents.iterdir() if not path.name.endswith(".lock")), ["hey-my-buddy"])

    def test_first_install_reports_old_skill_cleanup_failure_after_publication(self):
        skill = self.agents / 'hey-my-buddy'
        skill.mkdir(parents=True)
        (skill / 'skill.json').write_text('{"name":"hey-my-buddy","version":"0.0.1"}', encoding='utf-8')
        (skill / 'SKILL.md').write_text('---\nname: hey-my-buddy\n---\nold skill', encoding='utf-8')
        remove = shutil.rmtree

        def refuse_previous(path, *args, **kwargs):
            if Path(path).name.startswith('.hey-my-buddy-install-previous-'):
                raise PermissionError('old skill cleanup denied')
            return remove(path, *args, **kwargs)

        with patch('hey_my_buddy.install.skill_install.shutil.rmtree', side_effect=refuse_previous):
            result = self.install()
        self.assertEqual(result['skill']['placement'], 'updated')
        self.assertEqual(result['service']['action'], 'none')
        self.assertIn('old skill cleanup denied', result['skillCleanupError'])
        self.assertTrue((self.base / 'state/active-runtime.json').is_file())
        link = self.claude / 'hey-my-buddy'
        self.assertTrue(link.is_junction() if os.name == 'nt' else link.is_symlink())
        self.assertEqual(len(list(self.agents.glob('.hey-my-buddy-install-previous-*'))), 1)

    def test_foreign_directories_are_never_replaced(self):
        from hey_my_buddy.errors import BoardError
        (self.claude / "hey-my-buddy").mkdir(parents=True)
        with self.assertRaises(BoardError) as caught:
            self.install()
        self.assertEqual(caught.exception.code, "SKILL_TARGET_CONFLICT")
        self.assertFalse((self.claude / "hey-my-buddy").is_symlink())
        (self.claude / "hey-my-buddy").rmdir()
        self.assertFalse((self.agents / "hey-my-buddy").exists())
        (self.agents / "hey-my-buddy").mkdir()
        (self.agents / "hey-my-buddy/SKILL.md").write_text("---\nname: someone-else\ndescription: x\n---\n", encoding="utf-8", newline="\n")
        with self.assertRaises(BoardError) as caught:
            self.install()
        self.assertEqual(caught.exception.code, "SKILL_TARGET_CONFLICT")

    def test_a_worker_credential_cannot_install(self):
        from hey_my_buddy.errors import BoardError
        credential = self.base / "attempt-credential.json"
        credential.write_text(json.dumps({"token": "attempt-token", "stateDir": self.environment["HMB_STATE_DIR"]}), encoding="utf-8", newline="\n")
        credential.chmod(0o600)
        with self.assertRaises(BoardError) as caught:
            self.install(HMB_AGENT_CREDENTIAL_FILE=str(credential))
        self.assertEqual(caught.exception.code, "UNAUTHORIZED")
        self.assertFalse(self.agents.exists())

    def test_an_installed_skill_copies_itself(self):
        built = load_build_skill().build(ROOT, self.base / "built/hey-my-buddy")
        with patch("hey_my_buddy.install.skill_install.project_root", return_value=built / "package"):
            result = self.install()
        self.assertEqual(result["skill"]["placement"], "installed")
        self.assertEqual(json.loads((self.agents / "hey-my-buddy/skill.json").read_text(encoding="utf-8")),
                         json.loads((built / "skill.json").read_text(encoding="utf-8")))

    def test_a_failed_claude_link_fails_before_the_service_and_rerun_recovers(self):
        from hey_my_buddy.errors import BoardError
        with patch("hey_my_buddy.install.skill_install._create_claude_link", side_effect=OSError(1, "Operation not permitted")), \
                patch("hey_my_buddy.install.upgrade.upgrade") as service:
            with self.assertRaises(BoardError) as caught:
                self.install()
            service.assert_not_called()
        self.assertEqual(caught.exception.code, "CLAUDE_LINK_FAILED")
        self.assertFalse((self.agents / "hey-my-buddy").exists())
        self.assertFalse((self.claude / "hey-my-buddy").exists())
        result = self.install()
        self.assertEqual(result["skill"]["placement"], "installed")
        self.assertEqual(result["claude"]["status"], "linked")

    def test_failed_publication_removes_only_the_new_claude_link_and_restores_the_skill(self):
        from hey_my_buddy.install import skill_install
        skill = self.agents / skill_install.SKILL
        skill.mkdir(parents=True)
        (skill / 'SKILL.md').write_text('---\nname: hey-my-buddy\n---\nold skill', encoding='utf-8')
        with patch.object(skill_install, 'write_runtime_hint', side_effect=OSError('publication denied')):
            with self.assertRaisesRegex(OSError, 'publication denied'):
                self.install()
        link = self.claude / skill_install.SKILL
        self.assertFalse(link.exists() or link.is_symlink() or link.is_junction())
        self.assertTrue((skill / 'SKILL.md').read_text(encoding='utf-8').endswith('old skill'))

    def test_windows_data_root_is_local_app_data(self):
        from pathlib import PurePosixPath
        from hey_my_buddy import home
        # Only the branch is exercised here; a WindowsPath cannot exist on POSIX.
        with patch.object(home.os, "name", "nt"), patch.object(home, "Path", PurePosixPath), \
                patch.dict(os.environ, {"LOCALAPPDATA": "/Local"}):
            self.assertEqual(home.default_state_dir(), PurePosixPath("/Local/hey-my-buddy/state"))
            self.assertEqual(home.default_runtime_root(), PurePosixPath("/Local/hey-my-buddy/runtime"))
        from types import SimpleNamespace
        expected_home = Path.home()
        with patch.object(home, "os", SimpleNamespace(name="posix")):
            self.assertEqual(home.data_root(), expected_home / ".local/share/hey-my-buddy")

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
        self.assertEqual(after["skill"]["launcher"], str(self.agents / "hey-my-buddy/scripts/hey-my-buddy"))
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
            launcher_argv("install"), cwd=cwd, capture_output=True, text=True, timeout=240,
            env=current_environment(**self.environment, HMB_DEV_SOURCE="1",
                                    UV_PROJECT_ENVIRONMENT=str(self.base / "cli-venv")), encoding="utf-8",
        )
        self.assertEqual(completed.returncode, 0, completed.stderr + completed.stdout)
        result = json.loads(completed.stdout)
        self.assertEqual(result["skill"]["placement"], "installed")
        self.assertTrue((self.agents / "hey-my-buddy/scripts/hey-my-buddy").is_file())


class DocumentationTests(unittest.TestCase):
    """Both READMEs and the owning reference describe the same first-install path."""

    def test_operations_document_the_shared_skill_install(self):
        text = OPERATIONS.read_text(encoding="utf-8")
        self.assertIn("hey_my_buddy.protocol.rpc_config", text)
        self.assertIn("## Installation", text)
        for fragment in ("~/.agents/skills/hey-my-buddy", "~/.claude/skills/hey-my-buddy", "scripts/hey-my-buddy install",
                         "packaging/build-skill.py", "codex plugin remove"):
            self.assertIn(fragment, text)
        self.assertNotIn("codex plugin add", text)

    def test_readmes_keep_first_install_parity(self):
        version = re.search(r'^version = "([^"]+)"', (ROOT / "pyproject.toml").read_text(encoding="utf-8"), re.MULTILINE).group(1)
        for name in ("README.md", "README.zh-CN.md"):
            text = (ROOT / name).read_text(encoding="utf-8")
            self.assertIn(f"uvx hey-my-buddy@{version} install", text, name)
            self.assertIn("~/.agents/skills/hey-my-buddy", text, name)
            self.assertIn("docs/reference/operations.md#installation", text, name)
            self.assertNotIn("codex plugin add", text, name)


if __name__ == "__main__":
    unittest.main()
