"""Private test homes keep Git maintenance disabled after GIT_* is stripped."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import tempfile
import unittest

from hey_my_buddy.cli import checks
from support import _child_environment

CHECKOUT = Path(__file__).resolve().parents[3]


class GitConfigurationTests(unittest.TestCase):
    def assert_maintenance_disabled(self, environment):
        # Match workspace and router_input: Git-specific inherited state is removed.
        environment = {key: value for key, value in environment.items()
                       if not key.startswith("GIT_")}
        for key, kind, expected in (("maintenance.auto", "bool", "false"),
                                    ("gc.auto", "int", "0")):
            with self.subTest(key=key):
                actual = subprocess.run(
                    ["git", "config", f"--type={kind}", "--get", key],
                    cwd=environment["HOME"], env=environment,
                    capture_output=True, text=True,
                )
                self.assertEqual(actual.returncode, 0, actual.stderr)
                self.assertEqual(actual.stdout.strip(), expected)

    def test_running_suite_reads_maintenance_settings_without_git_environment(self):
        self.assert_maintenance_disabled(os.environ)

    def test_runner_configures_each_new_suite_home(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary).resolve()
            environment = checks.child_environment(
                CHECKOUT, directory / "suite", home_root=directory / "suite home 用户",
            )
            self.assert_maintenance_disabled(environment)

    def test_private_service_reads_maintenance_settings_without_git_environment(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary).resolve()
            self.assert_maintenance_disabled(_child_environment(directory))

    def test_private_service_configures_the_selected_home(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary).resolve()
            home = directory / "selected home 用户"
            home.mkdir()
            environment = _child_environment(directory, {"HOME": str(home)})
            self.assert_maintenance_disabled(environment)


if __name__ == "__main__":
    unittest.main()
