"""Release transport checks; no install command or daily state is touched."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import tarfile
import tempfile
import tomllib
import unittest
import zipfile

ROOT = Path(__file__).resolve().parents[3]


class DistributionTests(unittest.TestCase):
    def test_install_entry_and_private_bootstraps(self):
        config = tomllib.loads((ROOT / "pyproject.toml").read_text())
        self.assertEqual(config["project"]["scripts"], {"hey-my-buddy": "hey_my_buddy.install.package_install:main"})
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
            head = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "HEAD"],
                                  capture_output=True, text=True, check=True).stdout.strip()
            wheel = next((root / "dist").glob("*.whl"))
            sdist = next((root / "dist").glob("*.tar.gz"))
            with zipfile.ZipFile(wheel) as archive:
                wheel_files = set(archive.namelist())
                expected = {
                    "hey_my_buddy/_distribution/SKILL.md",
                    "hey_my_buddy/_distribution/skill.json",
                    "hey_my_buddy/_distribution/scripts/buddy",
                    "hey_my_buddy/_distribution/package/pyproject.toml",
                    "hey_my_buddy/_distribution/package/uv.lock",
                    "hey_my_buddy/_distribution/package/packaging/runtime-assets.json",
                    "hey_my_buddy/_distribution/package/packaging/hatch_build.py",
                    "hey_my_buddy/_distribution/package/src/hey_my_buddy/install/runtime.py",
                    "hey_my_buddy/_distribution/package/src/hey_my_buddy/console/assets/index.html",
                    "hey_my_buddy/install/package_install.py",
                }
                self.assertFalse(expected - wheel_files, expected - wheel_files)
                self.assertTrue(any(name.startswith("hey_my_buddy/_distribution/references/") for name in wheel_files))
                self.assertFalse(any(name.startswith("hey_my_buddy/_distribution/package/harnesses/dsh/")
                                     for name in wheel_files))
                self.assertTrue(any(name.startswith("hey_my_buddy/_distribution/package/src/hey_my_buddy/console/assets/assets/") for name in wheel_files))
                entry = next(name for name in wheel_files if name.endswith(".dist-info/entry_points.txt"))
                self.assertIn("hey-my-buddy = hey_my_buddy.install.package_install:main", archive.read(entry).decode())
                self.assertFalse(any(line.startswith("buddy =") for line in archive.read(entry).decode().splitlines()))
                self.assertFalse(any("node_modules" in name or "/tests/" in name or "/.venv/" in name for name in wheel_files))
                # A wheel built directly from the checkout carries its source commit.
                self.assertEqual(json.loads(archive.read("hey_my_buddy/_distribution/skill.json"))["sourceCommit"], head)
            with tarfile.open(sdist) as archive:
                names = archive.getnames()
                for suffix in ("packaging/hatch_build.py", "packaging/runtime-assets.json", "uv.lock",
                               "skills/buddy/SKILL.md", "docs/reference/architecture.md",
                               "src/hey_my_buddy/install/runtime.py", "src/hey_my_buddy/console/assets/index.html",
                               "src/hey_my_buddy/build-info.json"):
                    self.assertTrue(any(name.endswith("/" + suffix) for name in names), suffix)
                self.assertFalse(any("node_modules" in name or "/tests/" in name or "/.venv/" in name for name in names))
                # The sdist carries the source commit for a later Git-less wheel build.
                metadata = next(name for name in names if name.endswith("/src/hey_my_buddy/build-info.json"))
                self.assertEqual(json.loads(archive.extractfile(metadata).read())["sourceCommit"], head)
                archive.extractall(root / "sdist", filter="data")
            # A wheel built from the extracted sdist, with no Git checkout present,
            # must keep the original sourceCommit in its embedded skill marker.
            layout = next((root / "sdist").iterdir())
            from_sdist = subprocess.run(["uv", "build", "--wheel", "--out-dir", str(root / "from-sdist")],
                                        cwd=layout, env=env, capture_output=True, text=True, timeout=180)
            self.assertEqual(from_sdist.returncode, 0, from_sdist.stdout + from_sdist.stderr)
            with zipfile.ZipFile(next((root / "from-sdist").glob("*.whl"))) as archive:
                marker = json.loads(archive.read("hey_my_buddy/_distribution/skill.json"))
            self.assertEqual(marker["sourceCommit"], head)


if __name__ == "__main__":
    unittest.main()
