"""Packaging and launcher boundary tests for the current single-entrypoint plugin.

These stage the real checkout with ``packaging/stage-plugin.py`` and run the one
public launcher from unrelated working directories against private state/runtime
roots. Nothing here touches the operator's default state directory or daily board,
and staging never writes into the source tree.
"""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
LAUNCHER = ROOT / "bin" / "buddy"
REQUIRED_RESOURCES = {"dsh.runner", "dsh.catalog", "dsh.decision", "yaml.bridge", "console.assets"}


def load_stage_plugin():
    spec = importlib.util.spec_from_file_location("buddy_stage_plugin", ROOT / "packaging" / "stage-plugin.py")
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

    def test_the_plugin_has_exactly_one_public_launcher(self):
        self.assertTrue(LAUNCHER.is_file(), "bin/buddy is the bundled launcher")
        launchers = [path.name for path in (ROOT / "bin").iterdir() if path.is_file()]
        self.assertEqual(launchers, ["buddy"])
        pyproject = (ROOT / "pyproject.toml").read_text()
        self.assertEqual(pyproject.count("[project.scripts]"), 1)
        self.assertIn('buddy = "buddy.launcher:main"', pyproject)

    def test_the_only_agent_entrypoint_is_the_plugin_skill(self):
        self.assertTrue((ROOT / "skills" / "buddy" / "SKILL.md").is_file())
        self.assertEqual(sorted(path.name for path in (ROOT / "skills").iterdir() if path.is_dir()), ["buddy"])

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


class StagedPluginInventoryTests(unittest.TestCase):
    """Staging ships the supported layout only, and never writes into the source."""

    def stage(self):
        directory = tempfile.TemporaryDirectory(prefix="buddy-plugin-", dir=os.environ.get("BUDDY_CHECKS_TMPDIR", "/tmp"))
        self.addCleanup(directory.cleanup)
        destination = Path(directory.name) / "hey-my-buddy"
        return load_stage_plugin().stage(ROOT, destination)

    def test_the_supported_plugin_stages_completely(self):
        before = (ROOT / "plugin.json").read_bytes()
        staged = self.stage()
        self.assertEqual(staged.name, "hey-my-buddy")
        for relative in (
            "pyproject.toml",
            "uv.lock",
            "bin/buddy",
            "src/buddy/cli.py",
            "src/buddy/console_assets/index.html",
            "harnesses/dsh/scripts/run.mjs",
            "harnesses/dsh/plugins/turn-result.mjs",
            "skills/buddy/SKILL.md",
            "packaging/runtime-assets.json",
            "plugin.json",
            ".codex-plugin/plugin.json",
            "docs/README.md",
            "README.md",
            "README.zh-CN.md",
            "LICENSE",
        ):
            self.assertTrue((staged / relative).exists(), relative)
        manifest = json.loads((staged / "packaging/runtime-assets.json").read_text())
        self.assertEqual(set(manifest["resources"]), REQUIRED_RESOURCES)
        for relative in manifest["resources"].values():
            self.assertTrue((staged / relative).exists(), relative)
        self.assertEqual((ROOT / "plugin.json").read_bytes(), before, "staging must not rewrite the source metadata")

    def test_the_staged_plugin_excludes_tests_venvs_frontend_and_scratch(self):
        staged = self.stage()
        unsupported = {"tests", "node_modules", ".venv", "__pycache__", ".git", ".dsh-skill-build", "apps"}
        for path in staged.rglob("*"):
            relative = path.relative_to(staged)
            self.assertFalse(set(relative.parts) & unsupported, relative)
            self.assertNotIn(path.suffix, (".pyc", ".pyo"), relative)
        self.assertFalse((staged / "apps/console/src").exists())
        self.assertFalse((staged / "deepseek-delegate").exists())

    def test_the_portable_metadata_matches_the_plugin_manifest(self):
        staged = self.stage()
        identity = json.loads((staged / ".codex-plugin/plugin.json").read_text())
        portable = json.loads((staged / "plugin.json").read_text())
        for key in ("name", "version", "description", "author", "license"):
            self.assertEqual(portable[key], identity[key], key)


if __name__ == "__main__":
    unittest.main()
