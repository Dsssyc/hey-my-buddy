"""The frontend step names and refuses a Node outside the console's engines range."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shlex
import shutil
import tempfile
import threading
import unittest
from unittest.mock import patch

from hey_my_buddy.cli import checks

CHECKOUT = Path(__file__).resolve().parents[3]
CONSOLE = CHECKOUT / "apps" / "console"
#: The gate enforces exactly the range the console itself declares.
ENGINES_NODE = json.loads((CONSOLE / "package.json").read_text(encoding="utf-8"))["engines"]["node"]
#: A real Node that can run npm's semver judge; the fake reports its own version.
REAL_NODE = os.environ.get("HMB_NODE") or shutil.which("node")
REAL_SEMVER = CONSOLE / "node_modules" / "semver" / "package.json"


def fake_node(directory: Path, version: str) -> Path:
    """A Node double that reports ``version`` and delegates the rest to a real Node."""
    path = directory / f"node-{version}"
    path.write_text(
        "#!/bin/sh\n"
        'if [ "$1" = "--version" ]; then\n'
        f"    printf '{version}\\n'\n"
        "    exit 0\n"
        "fi\n"
        f"exec {shlex.quote(REAL_NODE)} \"$@\"\n",
        encoding="utf-8", newline="\n",
    )
    path.chmod(0o755)
    return path


def console_with_dependencies(checkout: Path) -> None:
    """The console as the frontend step needs it: real semver, scripted toolchain."""
    console = checkout / "apps" / "console"
    for name in ("typescript/bin/tsc", "vitest/vitest.mjs", "vite/bin/vite.js"):
        script = console / "node_modules" / name
        script.parent.mkdir(parents=True)
        script.write_text('console.log("toolchain-ran");\n', encoding="utf-8", newline="\n")
    shutil.copytree(CONSOLE / "node_modules" / "semver", console / "node_modules" / "semver")
    shutil.copy2(CONSOLE / "package.json", console / "package.json")


class NodeVersionGateTests(unittest.TestCase):
    def setUp(self):
        if os.name == "nt":
            self.skipTest("the fake Node is a POSIX shell script")
        if not REAL_NODE or not REAL_SEMVER.is_file():
            self.skipTest("Node.js and the console dev dependencies are required")
        self.container = tempfile.TemporaryDirectory(prefix="checks-node-version-")
        self.addCleanup(self.container.cleanup)
        self.directory = Path(self.container.name).resolve()
        self.root = checks.create_private_root(directory=self.directory)
        self.addCleanup(lambda: self.assertIsNone(checks.teardown_private_root(self.root)))

    def run_frontend_suite(self, checkout: Path, node: Path):
        with patch.dict(os.environ, {"HMB_NODE": str(node)}):
            return checks.run_frontend_suite(
                checkout, self.root, set(), threading.Lock(), threading.Event())

    def test_the_step_refuses_a_node_below_the_engines_range(self):
        checkout = self.directory / "checkout"
        console_with_dependencies(checkout)
        node = fake_node(self.directory, "v10.24.1")
        outcome = self.run_frontend_suite(checkout, node)
        self.assertEqual(outcome.returncode, 1)
        for expected in (str(node), "v10.24.1", ENGINES_NODE):
            self.assertIn(expected, outcome.stderr)
        self.assertNotIn("toolchain-ran", outcome.stdout)

    def test_a_node_inside_the_range_runs_the_frontend_children(self):
        checkout = self.directory / "checkout"
        console_with_dependencies(checkout)
        node = fake_node(self.directory, "v24.21.0")
        outcome = self.run_frontend_suite(checkout, node)
        self.assertEqual(outcome.returncode, 0, outcome.stderr)
        self.assertEqual(outcome.stdout.count("toolchain-ran"), 3)


if __name__ == "__main__":
    unittest.main()
