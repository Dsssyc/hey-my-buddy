"""Checks children use private native homes and offline discovery defaults."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from hey_my_buddy.cli import checks

CHECKOUT = Path(__file__).resolve().parents[3]


class HarnessTestSafetyTests(unittest.TestCase):
    def setUp(self):
        self.container = tempfile.TemporaryDirectory(prefix="harness-test-safety-")
        self.addCleanup(self.container.cleanup)
        self.directory = Path(self.container.name).resolve()
        self.root = checks.create_private_root(directory=self.directory)

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
