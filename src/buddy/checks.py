"""Run the focused Python integration tests and the dependency-free Node suite.

Both suites exercise *this checkout*: ``src`` and ``tests/python`` go first on
``PYTHONPATH``, the DSH Node tests run from ``harnesses/dsh/tests``, and inherited
runtime, worker and agent-credential variables are removed before either child starts.
A Worker-pinned production runtime must never leak into a test subprocess, and
``BUDDY_DEV_SOURCE=1`` alone cannot override one.
"""
from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import sys

#: Inherited variables that would otherwise point a test subprocess at a production
#: runtime, a Worker identity or an agent credential instead of its own private roots.
SANITIZED_VARIABLES = (
    "BUDDY_STATE_DIR",
    "BUDDY_RUNTIME_ROOT",
    "BUDDY_RUNTIME",
    "BUDDY_RUNTIME_IDENTITY",
    "BUDDY_WORKER_STATE",
    "BUDDY_WORKER_ID",
    "BUDDY_AGENT_CREDENTIAL",
    "BUDDY_AGENT_CREDENTIAL_FILE",
    "VIRTUAL_ENV",
    "UV_PROJECT_ENVIRONMENT",
)


def test_environment(root: Path) -> dict:
    """The child environment every test suite runs with: checkout first, no pins."""
    from .adapters.claude_config import THIRD_PARTY_OVERRIDE_VARIABLES

    # A Claude Code Host session exports ANTHROPIC_BASE_URL. Claude fixtures model the
    # first-party account explicitly, so an inherited gateway must not decide them.
    removed = {*SANITIZED_VARIABLES, *THIRD_PARTY_OVERRIDE_VARIABLES}
    values = {key: value for key, value in os.environ.items() if key not in removed}
    source = str(root / "src")
    tests = str(root / "tests" / "python")
    inherited = values.get("PYTHONPATH")
    values["PYTHONPATH"] = os.pathsep.join([source, tests] + ([inherited] if inherited else []))
    values["BUDDY_PYTHON"] = sys.executable
    # Tests must exercise this checkout, not a stable runtime install.
    values["BUDDY_DEV_SOURCE"] = "1"
    # Claude availability reads native account metadata. Unrelated tests must not
    # launch the user's CLI; Claude fixtures override this sentinel explicitly.
    values["BUDDY_CLAUDE_CLI"] = str(root / "tests/python/fixtures/claude-not-installed")
    return values


def main():
    root = Path(__file__).resolve().parents[2]
    env = test_environment(root)
    subprocess.run(
        [sys.executable, "-m", "unittest", "discover", "-s", str(root / "tests" / "python"), "-v"],
        cwd=str(root),
        env=env,
        check=True,
    )
    node = env.get("BUDDY_NODE") or shutil.which("node")
    if not node:
        raise SystemExit("Node.js is required for the dsh process runner")
    # Browser tests belong to Vitest; only the DSH Node suites under harnesses/dsh are
    # run here, and their support fixtures are not discovered as tests.
    node_tests = sorted(str(path) for path in (root / "harnesses" / "dsh" / "tests").glob("*.test.mjs"))
    subprocess.run([node, "--test", *node_tests], cwd=str(root), env=env, check=True)


if __name__ == "__main__":
    main()
