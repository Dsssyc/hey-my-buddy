"""Checks share the caller's uv cache while retaining private test directories."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from hey_my_buddy.cli import checks

CHECKOUT = Path(__file__).resolve().parents[3]


class ChecksUvCacheTests(unittest.TestCase):
    def setUp(self):
        self.container = tempfile.TemporaryDirectory(prefix="checks-uv-cache-")
        self.addCleanup(self.container.cleanup)
        self.directory = Path(self.container.name).resolve()
        self.private_root = checks.create_private_root(directory=self.directory)
        self.child_root = self.private_root / "suite"
        (self.child_root / "tmp").mkdir(parents=True)
        self.home_root = self.private_root / "suite-home"

    def environment(self):
        return checks.child_environment(CHECKOUT, self.child_root, home_root=self.home_root)

    def child_values(self, environment):
        completed = subprocess.run(
            [sys.executable, "-c", "import json, os; print(json.dumps(dict(os.environ)))"],
            env=environment, capture_output=True, text=True, check=True, encoding="utf-8",
        )
        return json.loads(completed.stdout)

    def assert_private_directories(self, values):
        for key in (
            "HOME", "USERPROFILE", "HOMEPATH", "CODEX_HOME", "CLAUDE_CONFIG_DIR",
            "DSH_HOME", "ZCODE_DATA_BASE_DIR", "XDG_CONFIG_HOME", "XDG_DATA_HOME",
            "XDG_CACHE_HOME", "XDG_STATE_HOME", "XDG_RUNTIME_DIR", "APPDATA",
            "LOCALAPPDATA", "TMPDIR", "HMB_CHECKS_TMPDIR", "HMB_STATE_DIR",
            "HMB_RUNTIME_ROOT",
        ):
            with self.subTest(key=key):
                self.assertTrue(Path(values[key]).resolve().is_relative_to(self.private_root))

    def test_default_cache_is_resolved_from_the_caller_before_home_is_private(self):
        caller_home = self.directory / "caller-home"
        caller_cache = self.directory / "caller-cache"
        cache = caller_cache / "uv"
        caller = {"HOME": str(caller_home), "USERPROFILE": str(caller_home),
                  "XDG_CACHE_HOME": str(caller_cache)}
        observed = {}

        def locate_cache(command, **keywords):
            observed.update(keywords["env"])
            return subprocess.CompletedProcess(command, 0, f"{cache}\n", "")

        with patch.dict(os.environ, caller, clear=True), patch.object(
            checks.subprocess, "run", side_effect=locate_cache,
        ) as locate:
            environment = self.environment()
        locate.assert_called_once()
        arguments, _ = locate.call_args
        self.assertEqual(arguments, (["uv", "cache", "dir"],))
        self.assertEqual(observed["HOME"], str(caller_home))
        self.assertEqual(observed["XDG_CACHE_HOME"], str(caller_cache))
        self.assertNotIn("UV_CACHE_DIR", observed)
        values = self.child_values(environment)
        self.assertEqual(values["UV_CACHE_DIR"], str(cache))
        self.assertFalse(Path(values["UV_CACHE_DIR"]).is_relative_to(self.private_root))
        self.assert_private_directories(values)

    def test_explicit_cache_is_passed_verbatim_without_resolving_it(self):
        for cache in (str(self.directory / "caller cache"), "../caller cache"):
            with self.subTest(cache=cache):
                with patch.dict(os.environ, {"UV_CACHE_DIR": cache}), patch.object(
                    checks.subprocess, "run",
                ) as locate:
                    environment = self.environment()
                locate.assert_not_called()
                values = self.child_values(environment)
                self.assertEqual(values["UV_CACHE_DIR"], cache)
                self.assertFalse(Path(values["UV_CACHE_DIR"]).resolve().is_relative_to(self.private_root))
                self.assert_private_directories(values)

    def test_uv_selects_the_callers_default_cache(self):
        caller = {"HOME": str(self.directory / "caller-home"),
                  "USERPROFILE": str(self.directory / "caller-home"), "PATH": os.environ["PATH"]}
        with patch.dict(os.environ, caller, clear=True):
            expected = subprocess.run(
                ["uv", "cache", "dir"], capture_output=True, text=True, check=True, encoding="utf-8",
            ).stdout.strip()
            environment = self.environment()
        values = self.child_values(environment)
        self.assertEqual(values["UV_CACHE_DIR"], expected)
        self.assertFalse(Path(expected).resolve().is_relative_to(self.private_root))
        self.assert_private_directories(values)


if __name__ == "__main__":
    unittest.main()
