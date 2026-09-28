"""Release transport checks; no install command or daily state is touched."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import tarfile
import tempfile
import tomllib
import unittest
import zipfile

ROOT = Path(__file__).resolve().parents[2]


class DistributionTests(unittest.TestCase):
    def test_install_entry_and_private_bootstraps(self):
        config = tomllib.loads((ROOT / "pyproject.toml").read_text())
        self.assertEqual(config["project"]["scripts"], {"hey-my-buddy": "buddy.package_install:main"})
        for name in ("install.sh", "install.ps1"):
            script = (ROOT / name).read_text()
            self.assertIn("0.12.19", script)
            self.assertIn("580e9742bc1ca4f9a6da3c4aa3db2dcceb0d0be84d75647abdae91bff68e52fc", script)
            self.assertIn("hey-my-buddy", script)
            self.assertIn("sha256", script.lower())
            self.assertNotIn("tool install", script)
        self.assertIn("tool run --from", (ROOT / "install.sh").read_text())
        self.assertIn("tool run --from", (ROOT / "install.ps1").read_text())

    def test_wheel_and_sdist_are_complete_and_clean(self):
        with tempfile.TemporaryDirectory(prefix="buddy-dist-", dir=os.environ.get("BUDDY_CHECKS_TMPDIR")) as folder:
            root = Path(folder)
            local_build = os.environ.get("BUDDY_DISTRIBUTION_NO_BUILD_ISOLATION") == "1"
            env = {key: value for key, value in os.environ.items()
                   if not key.startswith("BUDDY_") and key not in {"VIRTUAL_ENV", "UV_PROJECT_ENVIRONMENT"}
                   and (key != "PYTHONPATH" or local_build)}
            env.update(UV_CACHE_DIR=str(root / "cache"), BUDDY_STATE_DIR=str(root / "state"),
                       BUDDY_RUNTIME_ROOT=str(root / "runtime"))
            command = ["uv", "build", "--out-dir", str(root / "dist")]
            if local_build:
                command.extend(["--no-build-isolation", "--python", "python3.12"])
            result = subprocess.run(command, cwd=ROOT, env=env,
                                    capture_output=True, text=True, timeout=180)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            wheel = next((root / "dist").glob("*.whl"))
            sdist = next((root / "dist").glob("*.tar.gz"))
            with zipfile.ZipFile(wheel) as archive:
                wheel_files = set(archive.namelist())
                expected = {
                    "buddy/_distribution/SKILL.md",
                    "buddy/_distribution/skill.json",
                    "buddy/_distribution/scripts/buddy",
                    "buddy/_distribution/package/pyproject.toml",
                    "buddy/_distribution/package/uv.lock",
                    "buddy/_distribution/package/packaging/runtime-assets.json",
                    "buddy/_distribution/package/packaging/hatch_build.py",
                    "buddy/_distribution/package/src/buddy/runtime.py",
                    "buddy/_distribution/package/src/buddy/console_assets/index.html",
                    "buddy/_distribution/package/harnesses/dsh/scripts/run.mjs",
                    "buddy/package_install.py",
                }
                self.assertFalse(expected - wheel_files, expected - wheel_files)
                self.assertTrue(any(name.startswith("buddy/_distribution/references/") for name in wheel_files))
                self.assertTrue(any(name.startswith("buddy/_distribution/package/harnesses/dsh/plugins/") for name in wheel_files))
                self.assertTrue(any(name.startswith("buddy/_distribution/package/src/buddy/console_assets/assets/") for name in wheel_files))
                entry = next(name for name in wheel_files if name.endswith(".dist-info/entry_points.txt"))
                self.assertIn("hey-my-buddy = buddy.package_install:main", archive.read(entry).decode())
                self.assertFalse(any(line.startswith("buddy =") for line in archive.read(entry).decode().splitlines()))
                self.assertFalse(any("node_modules" in name or "/tests/" in name or "/.venv/" in name for name in wheel_files))
            with tarfile.open(sdist) as archive:
                names = archive.getnames()
                for suffix in ("packaging/hatch_build.py", "packaging/runtime-assets.json", "uv.lock",
                               "skills/buddy/SKILL.md", "docs/reference/architecture.md",
                               "src/buddy/runtime.py", "src/buddy/console_assets/index.html",
                               "harnesses/dsh/scripts/run.mjs"):
                    self.assertTrue(any(name.endswith("/" + suffix) for name in names), suffix)
                self.assertFalse(any("node_modules" in name or "/tests/" in name or "/.venv/" in name for name in names))


if __name__ == "__main__":
    unittest.main()
