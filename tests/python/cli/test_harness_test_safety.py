"""Checks children use private native homes and refuse installed harnesses."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from hey_my_buddy.cli import checks
from checks_guard import check_launch

CHECKOUT = Path(__file__).resolve().parents[3]


class HarnessTestSafetyTests(unittest.TestCase):
    def setUp(self):
        self.container = tempfile.TemporaryDirectory(prefix="harness-test-safety-")
        self.addCleanup(self.container.cleanup)
        self.directory = Path(self.container.name).resolve()
        self.root = checks.create_private_root(directory=self.directory)
        self.external = self.directory / "installed"
        self.external.mkdir()

    def environment(self):
        return checks.child_environment(CHECKOUT, self.root)

    def test_every_native_home_is_private_even_for_in_process_calls(self):
        keys = ("HOME", "USERPROFILE", "HOMEPATH", "CODEX_HOME", "CLAUDE_CONFIG_DIR", "DSH_HOME",
                "ZCODE_DATA_BASE_DIR", "ZCODE_BUILTIN_PROVIDER_CONFIG_FILE", "ZCODE_PERSONAL_PROVIDER_CONFIG_FILE",
                "XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME", "XDG_STATE_HOME", "XDG_RUNTIME_DIR",
                "APPDATA", "LOCALAPPDATA")
        with patch.dict(os.environ, {key: "/users/fixture/daily" for key in keys}):
            values = self.environment()
        for key in keys:
            self.assertTrue(Path(values[key]).resolve().is_relative_to(self.root), key)
        completed = subprocess.run([sys.executable, "-c", "from pathlib import Path; print(Path.home())"],
                                   env=values, capture_output=True, text=True, check=True)
        self.assertEqual(completed.stdout.strip(), values["HOME"])

    def test_automatic_child_guard_refuses_a_native_cli_before_any_side_effect(self):
        marker = self.external / "marker"
        cli = self.external / "dsh"
        cli.write_text(f"#!{sys.executable}\nfrom pathlib import Path\nPath({str(marker)!r}).write_text('bad')\n")
        cli.chmod(0o700)
        completed = subprocess.run([sys.executable, "-c", f"import subprocess; subprocess.run([{str(cli)!r}])"],
                                   env=self.environment(), capture_output=True, text=True)
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("check suite refused an installed harness", completed.stderr)
        self.assertFalse(marker.exists())

    def test_default_metadata_reads_have_no_native_process_to_start(self):
        code = """
from unittest.mock import patch
from hey_my_buddy.blackboard.catalog import catalog
from hey_my_buddy.buddy.harnesses.registry import adapters
with patch('subprocess.Popen', side_effect=AssertionError('metadata must not spawn')):
    assert not catalog.discovery_available()
    assert not any(item.available()[0] for item in adapters().values() if item.model_discovery)
"""
        subprocess.run([sys.executable, "-c", code], env=self.environment(),
                       capture_output=True, text=True, check=True)

    def test_node_entries_shell_wrappers_and_aliases_cannot_hide_an_installation(self):
        cli = self.external / "zcode"
        cli.write_text("installed fixture, never executed")
        wrapper = self.root / "wrapper"
        wrapper.write_text(f'#!/bin/sh\nexec node "{cli}" "$@"\n')
        alias = self.root / "owned-alias"
        alias.symlink_to(cli)
        for command in (["node", str(cli)], [str(wrapper)], [str(alias)],
                        ["/bin/sh", "-c", f'exec "{cli}"']):
            with self.subTest(command=command), self.assertRaisesRegex(AssertionError, "installed harness"):
                check_launch(command[0], command, None, self.environment(),
                             private_root=self.root, checkout=CHECKOUT)

    def test_a_test_owned_native_program_still_runs_with_its_private_home(self):
        cli = self.root / "dsh"
        cli.write_text(f"#!{sys.executable}\nimport os; print(os.environ['DSH_HOME'])\n")
        cli.chmod(0o700)
        completed = subprocess.run([sys.executable, "-c", f"import subprocess; subprocess.run([{str(cli)!r}], check=True)"],
                                   env=self.environment(), capture_output=True, text=True, check=True)
        self.assertEqual(completed.stdout.strip(), self.environment()["DSH_HOME"])
