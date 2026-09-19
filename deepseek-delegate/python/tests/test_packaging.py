"""Packaging and launcher boundary tests: CLI-only plugin, no MCP companion or SDK.

These run the retained launchers for real (unrelated cwd, minimal PATH) against a
private BUDDY_STATE_DIR, and assert the static packaging invariants of the CLI-only
design. Nothing here touches the operator's default state directory.
"""
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[3]
PROJECT = ROOT / "deepseek-delegate"


def load_stage_plugin():
    spec = importlib.util.spec_from_file_location("buddy_stage_plugin", ROOT / "scripts/stage-plugin.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class LauncherPortabilityTests(unittest.TestCase):
    """Every retained launcher must reach the CLI from anywhere with a minimal PATH."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="buddy-launch-", dir="/tmp")
        self.state = Path(self.temp.name) / "state"
        self.unrelated_cwd = Path(self.temp.name) / "elsewhere"
        self.unrelated_cwd.mkdir()

    def tearDown(self):
        code = subprocess.run([sys.executable, "-m", "buddy.cli", "stop"], capture_output=True, text=True,
                              env={**os.environ, "BUDDY_STATE_DIR": str(self.state)},
                              cwd=str(self.unrelated_cwd), timeout=60)
        self.assertIn(code.returncode, (0, 1), code.stderr)
        self.temp.cleanup()

    def test_shell_launcher_starts_from_unrelated_cwd_and_minimal_path(self):
        env = {"HOME": os.environ["HOME"], "PATH": "/usr/bin:/bin", "BUDDY_STATE_DIR": str(self.state)}
        process = subprocess.run(["/bin/sh", str(PROJECT / "scripts/launch-buddy.sh"), "health"],
                                 capture_output=True, text=True, env=env, cwd=str(self.unrelated_cwd), timeout=180)
        self.assertEqual(process.returncode, 0, process.stderr)
        health = json.loads(process.stdout)
        self.assertEqual(health["stateDir"], os.path.realpath(self.state))
        self.assertIsInstance(health["pid"], int)
        status = subprocess.run(["/bin/sh", str(PROJECT / "scripts/launch-buddy.sh"), "stop"],
                                capture_output=True, text=True, env=env, cwd=str(self.unrelated_cwd), timeout=120)
        self.assertEqual(status.returncode, 0, status.stderr)
        self.assertTrue(json.loads(status.stdout)["stopped"])

    def test_node_launcher_and_minimal_path_reach_the_same_cli(self):
        node = shutil.which("node")
        uv = shutil.which("uv")
        self.assertTrue(node and uv, "node and uv are required for the Node launcher")
        env = {**os.environ, "PATH": f"{Path(uv).parent}:{os.environ.get('PATH', '')}", "BUDDY_STATE_DIR": str(self.state)}
        process = subprocess.run([node, str(PROJECT / "scripts/buddy.mjs"), "health"],
                                 capture_output=True, text=True, env=env, cwd=str(self.unrelated_cwd), timeout=180)
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertEqual(json.loads(process.stdout)["stateDir"], os.path.realpath(self.state))

    def test_retained_launchers_never_target_buddy_mcp(self):
        for relative in ("deepseek-delegate/scripts/launch-buddy.sh", "deepseek-delegate/scripts/buddy.mjs"):
            text = (ROOT / relative).read_text()
            self.assertNotIn("buddy-mcp", text, relative)
            self.assertIn("buddy", text, relative)


class NoMcpCompanionTests(unittest.TestCase):
    def test_removed_mcp_files_and_entrypoints_are_gone(self):
        for relative in ("mcp.json", "deepseek-delegate/python/buddy/mcp_server.py",
                         "deepseek-delegate/python/buddy/native.py", "deepseek-delegate/python/buddy/receiver.py",
                         "deepseek-delegate/service/mcp.mjs", "deepseek-delegate/service/native-client.mjs"):
            self.assertFalse((ROOT / relative).exists(), f"{relative} must be removed")

    def test_project_has_no_mcp_dependency_entrypoint_or_lock_package(self):
        pyproject = (PROJECT / "pyproject.toml").read_text()
        self.assertNotIn("mcp", pyproject, "no MCP dependency or console entry may remain")
        lock = (PROJECT / "uv.lock").read_text()
        self.assertNotIn('name = "mcp"', lock)
        # The C-Two pin and the PyYAML constraint survive the regenerated lock.
        self.assertIn('{ name = "c-two", specifier = "==0.5.1" }', lock)
        self.assertIn('{ name = "pyyaml", specifier = ">=6,<7" }', lock)
        self.assertRegex(lock, r'\[\[package\]\]\nname = "c-two"\nversion = "0\.5\.1"')

    def test_mcp_sdk_is_not_importable_in_this_environment(self):
        self.assertIsNone(importlib.util.find_spec("mcp"))

    def test_stage_guard_refuses_any_tree_carrying_the_removed_mcp_paths(self):
        module = load_stage_plugin()
        self.assertEqual(module.mcp_companion_conflicts(ROOT), [], "the real tree must be CLI-only")
        with tempfile.TemporaryDirectory(prefix="buddy-stage-", dir="/tmp") as directory:
            fake = Path(directory)
            self.assertEqual(module.mcp_companion_conflicts(fake), [])
            (fake / "mcp.json").write_text("{}")
            self.assertEqual(module.mcp_companion_conflicts(fake), ["mcp.json"])
            with self.assertRaises(SystemExit) as failure:
                module.assert_no_mcp_companion(fake)
            self.assertIn("CLI-only", str(failure.exception))


if __name__ == "__main__":
    unittest.main()
