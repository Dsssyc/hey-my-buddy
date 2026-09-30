"""Packaging and launcher boundary tests for the shared ``buddy`` skill (ADR-015).

These build the real checkout with ``packaging/build-skill.py`` and run the skill's
launcher from unrelated working directories against private state/runtime roots.
Nothing here touches the operator's default state directory, daily board or home
skill directories, and building never writes into the source tree.
"""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
LAUNCHER = ROOT / "skills" / "buddy" / "scripts" / "buddy"
REQUIRED_RESOURCES = {"dsh.runner", "dsh.catalog", "yaml.bridge", "console.assets"}


def load_build_skill():
    spec = importlib.util.spec_from_file_location("buddy_build_skill", ROOT / "packaging" / "build-skill.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def current_environment(**overrides) -> dict:
    """A child environment with no inherited Buddy pin, credential or venv."""
    values = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("BUDDY_")
        and key not in {"VIRTUAL_ENV", "UV_PROJECT_ENVIRONMENT", "PYTHONPATH", "PLUGIN_DATA"}
    }
    values["BUDDY_CONSOLE_PORT"] = "0"
    values["BUDDY_CLAUDE_CLI"] = str(ROOT / "tests/python/fixtures/claude-not-installed")
    values.update(overrides)
    return values


class SingleEntrypointTests(unittest.TestCase):
    """One launcher, one CLI script and one declared runtime manifest."""

    def test_the_skill_carries_the_one_public_launcher(self):
        self.assertTrue(LAUNCHER.is_file(), "scripts/buddy is the skill launcher")
        self.assertTrue(os.access(LAUNCHER, os.X_OK))
        self.assertFalse((ROOT / "bin").exists())
        pyproject = (ROOT / "pyproject.toml").read_text()
        self.assertEqual(pyproject.count("[project.scripts]"), 1)
        self.assertIn('hey-my-buddy = "buddy.package_install:main"', pyproject)
        self.assertNotIn('\nbuddy =', pyproject)

    def test_the_only_agent_entrypoint_is_the_shared_skill_and_no_plugin_is_published(self):
        self.assertTrue((ROOT / "skills" / "buddy" / "SKILL.md").is_file())
        self.assertEqual(sorted(path.name for path in (ROOT / "skills").iterdir() if path.is_dir()), ["buddy"])
        for retired in (".codex-plugin", "plugin.json", ".agents", "packaging/stage-plugin.py"):
            self.assertFalse((ROOT / retired).exists(), retired)

    def test_the_declared_runtime_manifest_is_current_and_complete(self):
        from buddy import runtime

        declared = runtime.load_manifest(ROOT)
        self.assertEqual(set(declared["resources"]), REQUIRED_RESOURCES)
        self.assertEqual(runtime.missing_resources(ROOT), [])
        for name, relative in declared["resources"].items():
            self.assertTrue((ROOT / relative).exists(), name)
            self.assertEqual(runtime.resource_path(name), ROOT / relative)
        for relative, _digest in runtime.manifest(ROOT)["files"].items():
            self.assertTrue((ROOT / relative).is_file(), relative)
        self.assertIn("packaging/runtime-assets.json", runtime.manifest(ROOT)["files"])


class CwdIndependentCliTests(unittest.TestCase):
    """The single launcher reaches the same CLI from any working directory."""

    def setUp(self):
        base = Path(tempfile.mkdtemp(prefix="buddy-launch-", dir=os.environ.get("BUDDY_CHECKS_TMPDIR", "/tmp"))).resolve()
        self.base = base
        self.addCleanup(self.cleanup_private)
        self.state = base / "state"
        self.runtime_root = base / "runtime"
        self.first_cwd = base / "first"
        self.second_cwd = base / "second"
        self.first_cwd.mkdir()
        self.second_cwd.mkdir()

    def cleanup_private(self):
        from buddy.checks import teardown_private_root

        # The last pool slots may still be starting when health returns. Keep
        # state until process observation AND lifetime locks prove every slot
        # stopped; a one-time glob of existing lock files misses late starters.
        evidence = teardown_private_root(self.base)
        self.assertIsNone(evidence, f"Private launcher processes remain; preserved {self.base}")

    def environment(self) -> dict:
        # The launcher selects Python 3.12 for its uv project; give it a private
        # project environment so it can never recreate the interpreter environment
        # this test suite runs in.
        return current_environment(
            BUDDY_STATE_DIR=str(self.state),
            BUDDY_RUNTIME_ROOT=str(self.runtime_root),
            BUDDY_DEV_SOURCE="1",
            UV_PROJECT_ENVIRONMENT=str(self.base / "cli-venv"),
        )

    def launch(self, command: str, cwd: Path, timeout: int = 240) -> dict:
        completed = subprocess.run(
            ["/bin/sh", str(LAUNCHER), command],
            env=self.environment(),
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr + completed.stdout)
        return json.loads(completed.stdout)

    def test_the_launcher_reaches_the_private_cli_from_an_unrelated_cwd(self):
        health = self.launch("health", self.first_cwd)
        self.assertEqual(health["stateDir"], os.path.realpath(self.state))
        self.assertIsInstance(health["pid"], int)
        self.assertFalse(health["runtimeStable"])
        self.assertTrue(str(health["runtimeIdentity"]).startswith("source:"), health["runtimeIdentity"])

    def test_the_launcher_is_cwd_independent(self):
        first = self.launch("health", self.first_cwd)
        second = self.launch("runtime", self.second_cwd)
        self.assertEqual(first["stateDir"], os.path.realpath(self.state))
        self.assertEqual(second["identity"]["state"], "SOURCE")
        self.assertEqual(second["identity"]["identity"], first["runtimeIdentity"])
        self.assertEqual(set(second["identity"]["actual"]["resources"]), REQUIRED_RESOURCES)


class CheckHarnessTests(unittest.TestCase):
    """The check entrypoint points at this layout and never inherits a production pin."""

    def test_the_check_harness_sanitizes_inherited_runtime_and_worker_variables(self):
        from buddy import checks

        inherited = {key: "/production" for key in checks.SANITIZED_VARIABLES}
        with patch.dict(os.environ, inherited, clear=False):
            env = checks.test_environment(ROOT)
        self.assertEqual(sorted(key for key in checks.SANITIZED_VARIABLES if key in env), [])
        self.assertEqual(
            env["PYTHONPATH"].split(os.pathsep)[:2],
            [str(ROOT / "src"), str(ROOT / "tests" / "python")],
        )
        self.assertEqual(env["BUDDY_DEV_SOURCE"], "1")

    def test_the_check_harness_covers_the_python_dsh_and_node_suites(self):
        self.assertTrue((ROOT / "tests" / "python").is_dir())
        self.assertTrue((ROOT / "src" / "buddy" / "checks.py").is_file())
        self.assertTrue(sorted((ROOT / "harnesses" / "dsh" / "tests").glob("*.test.mjs")))

    def test_unrelated_checks_cannot_inherit_a_real_claude_cli(self):
        from buddy import checks
        with patch.dict(os.environ, {"BUDDY_CLAUDE_CLI": "/real-user-installation/claude"}):
            env = checks.test_environment(ROOT)
        self.assertNotEqual(env["BUDDY_CLAUDE_CLI"], "/real-user-installation/claude")
        self.assertFalse(Path(env["BUDDY_CLAUDE_CLI"]).exists())


class SkillBuildTests(unittest.TestCase):
    """The built skill is self-contained and follows the Agent Skills layout."""

    @classmethod
    def setUpClass(cls):
        cls.base = Path(tempfile.mkdtemp(prefix="buddy-skill-build-")).resolve()
        cls.skill = load_build_skill().build(ROOT, cls.base / "buddy")

    @classmethod
    def tearDownClass(cls):
        import shutil
        shutil.rmtree(cls.base, ignore_errors=True)

    def test_layout_frontmatter_and_version_marker(self):
        from buddy.contracts import CONTRACT_VERSION
        self.assertEqual(sorted(path.name for path in self.skill.iterdir()),
                         ["SKILL.md", "package", "references", "scripts", "skill.json"])
        text = (self.skill / "SKILL.md").read_text()
        head = text.split("\n---\n", 1)[0]
        self.assertIn("\nname: buddy", head)
        version = re.search(r'version = "([^"]+)"', (ROOT / "pyproject.toml").read_text()).group(1)
        self.assertIn(f'version: "{version}"', head)
        marker = json.loads((self.skill / "skill.json").read_text())
        self.assertEqual((marker["name"], marker["version"], marker["contract"]), ("buddy", version, CONTRACT_VERSION))
        self.assertTrue(os.access(self.skill / "scripts" / "buddy", os.X_OK))

    def test_links_stay_inside_the_skill(self):
        for document in [self.skill / "SKILL.md", *sorted((self.skill / "references").glob("*.md"))]:
            for target in re.findall(r"\]\(([^)\s]+)\)", document.read_text()):
                if re.match(r"[a-z]+://", target) or target.startswith("#"):
                    continue
                path = target.split("#", 1)[0]
                self.assertNotIn("..", path, f"{document.name}: {target}")
                self.assertTrue((document.parent / path).is_file(), f"{document.name}: {target}")

    def test_package_carries_runtime_assets_and_no_unsupported_content(self):
        from buddy import runtime
        package = self.skill / "package"
        self.assertEqual(runtime.missing_resources(package), [])
        self.assertTrue((package / "LICENSE").is_file())
        self.assertTrue((package / "src/buddy/build-info.json").is_file())
        for path in self.skill.rglob("*"):
            parts = path.relative_to(self.skill).parts
            self.assertFalse({"tests", ".venv", "node_modules", "apps", "__pycache__"} & set(parts), path)

    def test_build_refuses_a_destination_not_named_buddy(self):
        from buddy.errors import BoardError
        with self.assertRaises(BoardError):
            load_build_skill().build(ROOT, self.base / "other")


if __name__ == "__main__":
    unittest.main()
