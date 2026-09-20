"""Run the focused Python integration tests and the dependency-free Node suite.

The Python package directory is put first on ``PYTHONPATH`` so the tests and the
Node fixtures exercise this checkout rather than any installed copy, and the Node
suite is run with the same environment.
"""
import os
from pathlib import Path
import shutil
import subprocess
import sys


def main():
    root = Path(__file__).resolve().parents[2]
    source = str(root / "python")
    env = {
        **os.environ,
        "BUDDY_PYTHON": sys.executable,
        "PYTHONPATH": source + (os.pathsep + os.environ["PYTHONPATH"] if os.environ.get("PYTHONPATH") else ""),
        # Tests must exercise this checkout, not a stable runtime install.
        "BUDDY_DEV_SOURCE": "1",
    }
    subprocess.run(
        [sys.executable, "-m", "unittest", "discover", "-s", str(root / "python/tests"), "-v"],
        env=env,
        check=True,
    )
    node = env.get("BUDDY_NODE") or shutil.which("node")
    if not node:
        raise SystemExit("Node.js is required for the dsh process runner")
    subprocess.run([node, "--test"], cwd=root, env=env, check=True)


if __name__ == "__main__":
    main()
