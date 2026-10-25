"""Checks children isolate native homes without changing product discovery."""
from __future__ import annotations

import os
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
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
        self.child_root = self.root / "suite"
        (self.child_root / "tmp").mkdir(parents=True)
        self.home_root = self.root / "suite-home"

    def environment(self):
        return checks.child_environment(CHECKOUT, self.child_root, home_root=self.home_root)

    def test_every_native_home_is_private_even_for_in_process_calls(self):
        keys = ("HOME", "USERPROFILE", "HOMEPATH", "CODEX_HOME", "CLAUDE_CONFIG_DIR", "DSH_HOME",
                "ZCODE_DATA_BASE_DIR",
                "XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME", "XDG_STATE_HOME", "XDG_RUNTIME_DIR",
                "APPDATA", "LOCALAPPDATA")
        with patch.dict(os.environ, {key: "/users/fixture/daily" for key in keys}):
            values = self.environment()
        for key in keys:
            self.assertTrue(Path(values[key]).resolve().is_relative_to(self.root), key)
        completed = subprocess.run([sys.executable, "-c", "from pathlib import Path; print(Path.home())"],
                                   env=values, capture_output=True, text=True, check=True, encoding="utf-8")
        self.assertEqual(completed.stdout.strip(), values["HOME"])

    def test_isolation_does_not_override_discovery_or_fill_the_child_root(self):
        inherited = {key: '/fixture/inherited' for key in
                     ('HMB_MODEL_CATALOG_FILE', 'HMB_HARNESS_RECORD_FILE',
                      'ZCODE_BUILTIN_PROVIDER_CONFIG_FILE', 'ZCODE_PERSONAL_PROVIDER_CONFIG_FILE')}
        with patch.dict(os.environ, inherited):
            environment = self.environment()
        for key in inherited:
            self.assertNotIn(key, environment)
        from support import _child_environment
        with patch.dict(os.environ, inherited):
            fixture_environment = _child_environment(self.child_root)
        for key in inherited:
            self.assertNotIn(key, fixture_environment)
        # The support helper retains its pre-existing per-fixture child home;
        # the suite launcher leaves its own state root untouched.
        self.assertEqual(set(self.child_root.iterdir()), {self.child_root / 'tmp', self.child_root / 'home'})
        self.assertEqual(Path(environment['HOME']).parent, self.child_root.parent)
        self.assertFalse(Path(environment['HOME']).is_relative_to(self.child_root))

    def test_board_setup_keeps_the_upgrade_fixture_destination_empty(self):
        from support import BoardTestCase
        from blackboard.store.upgrade_fixture import load_fixture
        import shutil

        case = BoardTestCase()
        with patch.object(case, '_cleanup', lambda: shutil.rmtree(case.directory)):
            case.setUp()
        try:
            self.assertEqual(list(case.directory.iterdir()), [])
            load_fixture(case.directory)
            self.assertTrue((case.directory / 'board.sqlite3').is_file())
        finally:
            case.doCleanups()

    def test_frontend_children_use_the_runner_interpreter_and_private_home(self):
        checkout = self.directory / "checkout"
        console = checkout / "apps/console"
        probe = ("import json, os\n"
                 "print(json.dumps({key: os.environ[key] for key in "
                 "('HMB_PREVIEW_PYTHON', 'HOME', 'USERPROFILE')}))\n")
        for name in ("typescript/bin/tsc", "vitest/vitest.mjs"):
            script = console / "node_modules" / name
            script.parent.mkdir(parents=True)
            script.write_text(probe, encoding="utf-8", newline="\n")
        self.addCleanup(lambda: self.assertIsNone(checks.teardown_private_root(self.root)))
        with patch.dict(os.environ, {"HMB_PREVIEW_PYTHON": "untrusted-parent-python"}), \
                patch.object(checks.shutil, "which", return_value=sys.executable):
            outcome = checks.run_frontend_suite(checkout, self.root, set(), threading.Lock(), threading.Event())
        self.assertEqual(outcome.returncode, 0, outcome.stderr)
        replies = [json.loads(line) for line in outcome.stdout.splitlines()]
        self.assertEqual(len(replies), 2)
        for reply in replies:
            self.assertEqual(reply["HMB_PREVIEW_PYTHON"], sys.executable)
            self.assertEqual(Path(reply["HOME"]), self.root / "frontend-home")
            self.assertEqual(reply["USERPROFILE"], reply["HOME"])
