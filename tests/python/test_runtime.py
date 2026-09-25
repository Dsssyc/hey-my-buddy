"""Runtime manifest, identity and materialization tests.

Every test owns private project and runtime roots built from a synthetic declared
manifest, so nothing here reads or installs into the operator's runtime root.
Resource names belong to each manifest, independently of the installed harnesses.
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
import select
import sys
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path
import tempfile
from threading import Event
import unittest
from unittest.mock import patch

from buddy import runtime
from buddy.errors import BoardError

REAL_ROOT = Path(__file__).resolve().parents[2]

DEFAULT_ASSETS = [
    {"path": "packaging/runtime-assets.json", "kind": "file"},
    {"path": "pyproject.toml", "kind": "file"},
    {"path": "src/buddy", "kind": "directory"},
    {"path": "harnesses/dsh/scripts", "kind": "directory"},
]
DEFAULT_RESOURCES = {
    "dsh.runner": "harnesses/dsh/scripts/run.mjs",
    "dsh.catalog": "harnesses/dsh/scripts/model-catalog.mjs",
    "dsh.decision": "harnesses/dsh/scripts/decision.mjs",
    "yaml.bridge": "src/buddy/yaml_bridge.py",
    "console.assets": "src/buddy/console_assets",
}


def write_assets(root: Path) -> None:
    """The minimal set of declared files a synthetic runtime root needs."""
    (root / "src" / "buddy" / "console_assets").mkdir(parents=True, exist_ok=True)
    (root / "src" / "buddy" / "yaml_bridge.py").write_text("# yaml bridge\n")
    (root / "src" / "buddy" / "console_assets" / "index.html").write_text("<html>console</html>\n")
    (root / "src" / "buddy" / "__pycache__").mkdir(parents=True, exist_ok=True)
    (root / "src" / "buddy" / "__pycache__" / "yaml_bridge.cpython-312.pyc").write_bytes(b"\0compiled")
    (root / "harnesses" / "dsh" / "scripts").mkdir(parents=True, exist_ok=True)
    for name in ("run.mjs", "model-catalog.mjs", "decision.mjs"):
        (root / "harnesses" / "dsh" / "scripts" / name).write_text(f"// {name}\n")
    (root / "pyproject.toml").write_text('[project]\nname = "synthetic"\nversion = "0.0.0"\n')


def write_manifest(
    root: Path,
    *,
    assets: list[dict] | None = None,
    resources: dict | None = None,
    extra: dict | None = None,
) -> Path:
    payload = {
        "format": runtime.RUNTIME_FORMAT,
        "assets": DEFAULT_ASSETS if assets is None else assets,
        "resources": DEFAULT_RESOURCES if resources is None else resources,
    }
    payload.update(extra or {})
    path = root / runtime.ASSET_MANIFEST
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n")
    return path


def fake_uv(directory: Path, *, gate: Path | None = None) -> Path:
    """A stand-in uv that publishes an interpreter without any dependency install."""
    directory.mkdir(parents=True, exist_ok=True)
    script = directory / "uv"
    script.write_text(
        f"#!{sys.executable}\n"
        "import os\n"
        "from pathlib import Path\n"
        "base = Path(__file__).parent\n"
        "with (base / 'invocations').open('a') as stream:\n"
        "    stream.write('sync\\n')\n"
        f"gate = {str(gate) if gate else None!r}\n"
        "if gate is not None:\n"
        "    try:\n"
        "        (base / 'gate.claimed').touch(exist_ok=False)\n"
        "    except FileExistsError:\n"
        "        pass\n"
        "    else:\n"
        "        with (Path(gate) / 'started').open('w') as stream:\n"
        "            stream.write('started\\n')\n"
        "        with (Path(gate) / 'release').open() as stream:\n"
        "            stream.readline()\n"
        "if (base / 'fail').exists():\n"
        "    raise SystemExit(7)\n"
        "interpreter = Path(os.environ['UV_PROJECT_ENVIRONMENT']) / 'bin' / 'python'\n"
        "interpreter.parent.mkdir(parents=True, exist_ok=True)\n"
        "interpreter.write_text('#!/bin/sh\\n')\n"
        "interpreter.chmod(0o755)\n"
    )
    script.chmod(0o755)
    return script


@contextmanager
def private_runtime_root(directory: Path):
    """Keep state, runtime discovery and fake uv free of inherited Buddy authority."""
    environment = {
        name: value
        for name, value in os.environ.items()
        if not name.startswith("BUDDY_") and name not in ("VIRTUAL_ENV", "UV_PROJECT_ENVIRONMENT")
    }
    environment.update(
        BUDDY_STATE_DIR=str(directory.parent / "state"),
        BUDDY_RUNTIME_ROOT=str(directory),
        BUDDY_DEV_SOURCE="1",
        BUDDY_CLAUDE_CLI=str(directory / "claude-not-installed"),
    )
    with patch.dict(os.environ, environment, clear=True):
        yield


class DeclaredManifestTests(unittest.TestCase):
    """The manifest is the only layout declaration; nothing is guessed."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="buddy-runtime-", dir="/tmp")
        self.addCleanup(self.temp.cleanup)
        base = Path(self.temp.name).resolve()
        self.root = base / "project"
        self.root.mkdir()
        self.runtime_root = base / "runtime"
        self.enterContext(private_runtime_root(self.runtime_root))
        write_assets(self.root)
        self.manifest_path = write_manifest(self.root)

    def test_the_manifest_declares_the_current_resources_and_assets(self):
        declared = runtime.load_manifest(self.root)
        self.assertEqual(declared["format"], runtime.RUNTIME_FORMAT)
        self.assertEqual(declared["resources"], DEFAULT_RESOURCES)
        self.assertEqual(len(declared["sha256"]), 64)
        for name, relative in declared["resources"].items():
            self.assertTrue((self.root / relative).exists(), name)
        self.assertEqual(runtime.declared_resources(self.root), declared["resources"])

    def test_a_missing_manifest_is_reported_instead_of_probing_an_old_layout(self):
        decoy = self.root / "scripts" / "run.mjs"
        decoy.parent.mkdir(parents=True)
        decoy.write_text("// old layout\n")
        (self.root / "python").mkdir()
        self.manifest_path.unlink()
        with self.assertRaises(BoardError) as failure:
            runtime.resource_path("dsh.runner", self.root)
        self.assertEqual(failure.exception.code, "RUNTIME_MANIFEST_MISSING")
        self.assertTrue(decoy.is_file(), "the old-layout decoy must not be consulted as a fallback")
        with self.assertRaises(BoardError) as failure:
            runtime.content_id(self.root)
        self.assertEqual(failure.exception.code, "RUNTIME_MANIFEST_MISSING")

    def test_an_undeclared_resource_name_is_refused(self):
        with self.assertRaises(BoardError) as failure:
            runtime.resource_path("dsh.legacy-runner", self.root)
        self.assertEqual(failure.exception.code, "RUNTIME_RESOURCE_UNDECLARED")

    def test_a_new_harness_declares_its_own_resources_without_dsh(self):
        relative = "harnesses/new-harness/runner.py"
        runner = self.root / relative
        runner.parent.mkdir(parents=True)
        runner.write_text("# a different harness\n")
        resources = {"new-harness.runner": relative}
        write_manifest(
            self.root,
            assets=[*DEFAULT_ASSETS, {"path": "harnesses/new-harness", "kind": "directory"}],
            resources=resources,
        )
        self.assertEqual(runtime.load_manifest(self.root)["resources"], resources)
        self.assertEqual(runtime.resource_path("new-harness.runner", self.root), runner)
        self.assertIn(relative, runtime.manifest(self.root)["files"])
        with self.assertRaises(BoardError) as failure:
            runtime.resource_path("dsh.runner", self.root)
        self.assertEqual(failure.exception.code, "RUNTIME_RESOURCE_UNDECLARED")

    def test_the_manifest_must_declare_its_own_file(self):
        assets = [entry for entry in DEFAULT_ASSETS if entry["path"] != runtime.ASSET_MANIFEST.as_posix()]
        for declaration in ([], [{"path": runtime.ASSET_MANIFEST.as_posix(), "kind": "directory"}]):
            with self.subTest(declaration=declaration):
                write_manifest(self.root, assets=[*assets, *declaration])
                with self.assertRaises(BoardError) as failure:
                    runtime.load_manifest(self.root)
                self.assertEqual(failure.exception.code, "RUNTIME_MANIFEST_INVALID")
                self.assertIn("declare itself as a file", failure.exception.message)

    def test_a_resource_must_be_covered_by_a_declared_asset(self):
        for relative in ("undeclared/runner.py", "src/buddy-neighbor/runner.py", "pyproject.toml/runner.py"):
            with self.subTest(relative=relative):
                write_manifest(self.root, resources={"new-harness.runner": relative})
                with self.assertRaises(BoardError) as failure:
                    runtime.load_manifest(self.root)
                self.assertEqual(failure.exception.code, "RUNTIME_MANIFEST_INVALID")
                self.assertIn("not covered", failure.exception.message)

    def test_a_declared_file_covers_its_exact_resource_path(self):
        write_manifest(self.root, resources={"project.config": "pyproject.toml"})
        self.assertEqual(runtime.resource_path("project.config", self.root), self.root / "pyproject.toml")

    def test_a_manifest_symlink_outside_the_root_is_refused(self):
        outside = self.root.parent / "outside-manifest.json"
        self.manifest_path.rename(outside)
        self.manifest_path.symlink_to(outside)
        with self.assertRaises(BoardError) as failure:
            runtime.load_manifest(self.root)
        self.assertEqual(failure.exception.code, "RUNTIME_MANIFEST_INVALID")
        self.assertIn("outside", failure.exception.message)

    def test_declared_asset_symlinks_outside_the_root_are_refused(self):
        for relative in ("pyproject.toml", "harnesses/dsh/scripts"):
            with self.subTest(relative=relative):
                asset = self.root / relative
                outside = self.root.parent / asset.name
                asset.rename(outside)
                asset.symlink_to(outside, target_is_directory=outside.is_dir())
                try:
                    with self.assertRaises(BoardError) as failure:
                        list(runtime.iter_assets(self.root))
                    self.assertEqual(failure.exception.code, "RUNTIME_MANIFEST_INVALID")
                    self.assertIn("outside", failure.exception.message)
                finally:
                    asset.unlink()
                    outside.rename(asset)

    def test_a_declared_resource_symlink_outside_the_root_is_refused(self):
        runner = self.root / DEFAULT_RESOURCES["dsh.runner"]
        outside = self.root.parent / "outside-runner.mjs"
        runner.rename(outside)
        runner.symlink_to(outside)
        with self.assertRaises(BoardError) as failure:
            runtime.resource_path("dsh.runner", self.root)
        self.assertEqual(failure.exception.code, "RUNTIME_MANIFEST_INVALID")
        self.assertIn("dsh.runner", failure.exception.message)

    def test_nested_asset_symlinks_outside_the_root_are_never_copied(self):
        outside = self.root.parent / "outside.py"
        outside.write_text("# not part of the declared root\n")
        linked = self.root / "src/buddy/external.py"
        linked.symlink_to(outside)
        destination = self.root.parent / "copied"
        with self.assertRaises(BoardError) as failure:
            runtime._copy_assets(self.root, destination)
        self.assertEqual(failure.exception.code, "RUNTIME_ASSET_OUTSIDE_ROOT")
        self.assertFalse((destination / "src/buddy/external.py").exists())

    def test_a_missing_declared_asset_is_reported_not_skipped(self):
        (self.root / "pyproject.toml").unlink()
        with self.assertRaises(BoardError) as failure:
            list(runtime.iter_assets(self.root))
        self.assertEqual(failure.exception.code, "RUNTIME_ASSET_MISSING")
        runtime.resource_path("dsh.runner", self.root).unlink()
        self.assertEqual(runtime.missing_resources(self.root), ["dsh.runner"])

    def test_iter_assets_copies_only_declared_content(self):
        (self.root / "tests").mkdir()
        (self.root / "tests" / "test_scratch.py").write_text("# not a runtime asset\n")
        (self.root / ".venv").mkdir()
        (self.root / ".venv" / "pyvenv.cfg").write_text("home = /nope\n")
        (self.root / "harnesses" / "dsh" / "scripts" / "__pycache__").mkdir()
        (self.root / "harnesses" / "dsh" / "scripts" / "__pycache__" / "stale.pyc").write_bytes(b"\0")
        relative = {path for path, _file in runtime.iter_assets(self.root)}
        self.assertEqual(
            relative,
            {
                "packaging/runtime-assets.json",
                "pyproject.toml",
                "src/buddy/yaml_bridge.py",
                "src/buddy/console_assets/index.html",
                "harnesses/dsh/scripts/run.mjs",
                "harnesses/dsh/scripts/model-catalog.mjs",
                "harnesses/dsh/scripts/decision.mjs",
            },
        )

    def test_content_id_covers_every_asset_and_the_manifest_itself(self):
        first = runtime.content_id(self.root)
        self.assertEqual(len(first), 32)
        (self.root / "harnesses" / "dsh" / "scripts" / "run.mjs").write_text("// changed\n")
        second = runtime.content_id(self.root)
        self.assertNotEqual(first, second)
        write_manifest(self.root, extra={"note": "identity covers the manifest bytes"})
        third = runtime.content_id(self.root)
        self.assertNotEqual(second, third)
        record = runtime.manifest(self.root)
        self.assertEqual(record["manifest"], runtime.ASSET_MANIFEST.as_posix())
        self.assertIn(runtime.ASSET_MANIFEST.as_posix(), record["files"])
        self.assertEqual(record["manifestSha256"], hashlib.sha256(self.manifest_path.read_bytes()).hexdigest())
        self.assertEqual(record["resources"], DEFAULT_RESOURCES)
        self.assertEqual(record["contentId"], third)

    def test_process_identity_exposes_declared_resources_not_dsh_globals(self):
        with patch.object(runtime, "project_root", return_value=self.root):
            actual = runtime.process_identity()
        self.assertEqual(set(actual["resources"]), set(DEFAULT_RESOURCES))
        self.assertEqual(actual["missingResources"], [])
        self.assertNotIn("adapterScript", actual)
        self.assertNotIn("yamlBridge", actual)
        for name, value in actual["resources"].items():
            self.assertTrue(Path(value).is_absolute(), name)
            self.assertTrue(Path(value).is_relative_to(self.root), name)


class RuntimeMaterializationTests(unittest.TestCase):
    """A runtime is published only from declared assets and keeps its own paths."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="buddy-runtime-build-", dir="/tmp")
        self.addCleanup(self.temp.cleanup)
        base = Path(self.temp.name).resolve()
        self.root = base / "project"
        self.root.mkdir()
        self.runtime_root = base / "runtime"
        self.enterContext(private_runtime_root(self.runtime_root))
        self.uv = fake_uv(base / "bin")
        write_assets(self.root)
        write_manifest(self.root)

    def materialize(self) -> Path:
        result = runtime.materialize(self.root, destination=self.runtime_root, uv_bin=str(self.uv))
        self.assertTrue(result["installed"])
        return Path(result["runtimeDir"])

    def test_materialize_copies_declared_assets_and_publishes_ready_last(self):
        target = self.materialize()
        self.assertTrue(runtime.is_ready(target))
        record = runtime.read_ready(target)
        self.assertEqual(record["state"], "READY")
        self.assertEqual(record["contentId"], target.name)
        self.assertEqual(record["projectRoot"], str(target))
        self.assertEqual(set(record["resources"]), set(DEFAULT_RESOURCES))
        for name, value in record["resources"].items():
            self.assertTrue(Path(value).is_relative_to(target), name)
        for relative in (
            "pyproject.toml",
            "packaging/runtime-assets.json",
            "src/buddy/yaml_bridge.py",
            "src/buddy/console_assets/index.html",
            "harnesses/dsh/scripts/run.mjs",
            "harnesses/dsh/scripts/model-catalog.mjs",
            "harnesses/dsh/scripts/decision.mjs",
        ):
            self.assertTrue((target / relative).is_file(), relative)
        self.assertFalse((target / "tests").exists())
        self.assertFalse((target / ".venv").exists())
        self.assertFalse((target / "src" / "buddy" / "__pycache__").exists())
        self.assertTrue(Path(record["python"]).is_file())
        self.assertEqual(runtime.find_ready(self.root, self.runtime_root), target)
        again = runtime.materialize(self.root, destination=self.runtime_root, uv_bin=str(self.uv))
        self.assertFalse(again["installed"])
        self.assertEqual(Path(again["runtimeDir"]), target)

    def test_materialize_accepts_a_manifest_with_only_another_harness(self):
        resources = {"new-harness.runner": "src/buddy/yaml_bridge.py"}
        write_manifest(self.root, resources=resources)
        target = self.materialize()
        self.assertTrue(runtime.is_ready(target))
        self.assertEqual(runtime.read_ready(target)["resources"], {name: str(target / path) for name, path in resources.items()})

    def test_concurrent_installers_share_one_lock_until_ready(self):
        gate = self.root.parent / "uv-gate"
        gate.mkdir()
        for name in ("started", "release"):
            os.mkfifo(gate / name)
        started_fd = os.open(gate / "started", os.O_RDWR | os.O_NONBLOCK)
        release_fd = os.open(gate / "release", os.O_RDWR | os.O_NONBLOCK)
        self.addCleanup(os.close, started_fd)
        self.addCleanup(os.close, release_fd)
        fake_uv(self.uv.parent, gate=gate)
        target = runtime.runtime_dir(self.root, self.runtime_root)
        lock_path = target.parent / f".{target.name}.install.lock"
        second_attempted = Event()
        locked_inodes = []
        real_flock = fcntl.flock

        def observe_lock(fd, operation):
            if operation == fcntl.LOCK_EX:
                locked_inodes.append(os.fstat(fd).st_ino)
                if len(locked_inodes) == 2:
                    second_attempted.set()
            return real_flock(fd, operation)

        options = {"destination": self.runtime_root, "uv_bin": str(self.uv), "timeout_seconds": 10}
        with ThreadPoolExecutor(max_workers=2) as pool, patch.object(runtime.fcntl, "flock", side_effect=observe_lock):
            first = pool.submit(runtime.materialize, self.root, **options)
            try:
                readable, _, _ = select.select([started_fd], [], [], 5)
                self.assertTrue(readable, "the first fake uv did not reach its controlled wait")
                self.assertEqual(os.read(started_fd, 64), b"started\n")
                second = pool.submit(runtime.materialize, self.root, **options)
                self.assertTrue(second_attempted.wait(5), "the second installer did not reach the lock")
                self.assertEqual(locked_inodes, [lock_path.stat().st_ino] * 2)
                probe_fd = os.open(lock_path, os.O_RDWR)
                try:
                    with self.assertRaises(BlockingIOError):
                        real_flock(probe_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                finally:
                    os.close(probe_fd)
                self.assertFalse((target / runtime.READY_FILE).exists())
                self.assertFalse(second.done())
            finally:
                os.write(release_fd, b"continue\n")
            results = [first.result(timeout=15), second.result(timeout=15)]

        self.assertEqual(sorted(result["installed"] for result in results), [False, True])
        self.assertEqual(results[0]["runtime"], results[1]["runtime"])
        self.assertEqual(results[0]["runtimeDir"], results[1]["runtimeDir"])
        self.assertTrue(runtime.is_ready(target))
        self.assertEqual((self.uv.parent / "invocations").read_text().splitlines(), ["sync"])
        self.assertEqual(lock_path.stat().st_ino, locked_inodes[0], "the lock inode must survive publication")

    def test_a_failed_install_can_retry_using_the_same_lock_inode(self):
        failure_marker = self.uv.parent / "fail"
        failure_marker.touch()
        target = runtime.runtime_dir(self.root, self.runtime_root)
        lock_path = target.parent / f".{target.name}.install.lock"
        with self.assertRaises(BoardError) as failure:
            runtime.materialize(self.root, destination=self.runtime_root, uv_bin=str(self.uv))
        self.assertEqual(failure.exception.code, "RUNTIME_INSTALL_FAILED")
        self.assertFalse(runtime.is_ready(target))
        self.assertFalse(target.exists())
        first_inode = lock_path.stat().st_ino
        failure_marker.unlink()
        self.assertEqual(self.materialize(), target)
        self.assertTrue(runtime.is_ready(target))
        self.assertEqual(lock_path.stat().st_ino, first_inode)
        self.assertEqual((self.uv.parent / "invocations").read_text().splitlines(), ["sync", "sync"])

    def test_a_ready_runtime_keeps_its_paths_after_the_source_tree_is_replaced(self):
        target = self.materialize()
        record = runtime.read_ready(target)
        replaced = self.root.with_name("project-replaced")
        self.root.rename(replaced)
        try:
            self.assertTrue(runtime.is_ready(target), "a READY runtime must not depend on the source tree")
            self.assertEqual(runtime.read_ready(target)["resources"], record["resources"])
            for value in record["resources"].values():
                self.assertTrue(Path(value).is_relative_to(target), value)
        finally:
            replaced.rename(self.root)

    def test_a_ready_runtime_with_a_missing_resource_is_not_ready(self):
        target = self.materialize()
        Path(runtime.read_ready(target)["resources"]["dsh.runner"]).unlink()
        self.assertFalse(runtime.is_ready(target))

    def test_a_ready_runtime_rejects_an_outward_resource_symlink(self):
        target = self.materialize()
        record = runtime.read_ready(target)
        runner = Path(record["resources"]["dsh.runner"])
        outside = self.root / DEFAULT_RESOURCES["dsh.runner"]
        runner.unlink()
        runner.symlink_to(outside)
        self.assertFalse(runtime.is_ready(target))
        self.assertTrue(any("dsh.runner resolves outside" in leak for leak in runtime.source_leaks(record)))
        with patch.dict(os.environ, {"BUDDY_RUNTIME": str(target)}):
            with self.assertRaises(BoardError) as failure:
                runtime.resolve_runtime()
        self.assertEqual(failure.exception.code, "RUNTIME_NOT_READY")

    def test_a_ready_runtime_rejects_an_outward_symlink_inside_an_asset_directory(self):
        target = self.materialize()
        (target / "src/buddy/external.py").symlink_to(self.root / "src/buddy/yaml_bridge.py")
        self.assertFalse(runtime.is_ready(target))

    def test_a_shared_base_interpreter_symlink_is_allowed(self):
        target = self.materialize()
        record = runtime.read_ready(target)
        interpreter = Path(record["python"])
        interpreter.unlink()
        interpreter.symlink_to(sys.executable)
        self.assertTrue(runtime.is_ready(target))
        actual = {
            "package": str(target / "src/buddy"),
            "prefix": record["environment"],
            "executable": record["python"],
            "resources": record["resources"],
            "missingResources": [],
        }
        with patch.dict(os.environ, {"BUDDY_RUNTIME": str(target)}), patch.object(runtime, "process_identity", return_value=actual):
            resolved = runtime.resolve_runtime()
        self.assertTrue(resolved["stable"])
        self.assertEqual(resolved["leaks"], [])

    def test_resolve_runtime_does_not_trust_a_ready_marker_alone(self):
        target = self.materialize()
        with private_runtime_root(self.runtime_root), patch.dict(os.environ, {"BUDDY_RUNTIME": str(target)}), patch.object(
            runtime, "project_root", return_value=target
        ):
            resolved = runtime.resolve_runtime()
        self.assertEqual(resolved["state"], "READY")
        self.assertTrue(resolved["declaredStable"])
        self.assertFalse(resolved["inUse"], "the test process does not execute from the synthetic runtime")
        self.assertFalse(resolved["stable"], "a READY marker alone never proves stability")
        self.assertTrue(resolved["identity"].startswith("source:"), resolved["identity"])
        self.assertEqual(resolved["resourcesMissing"], [])

    def test_source_leaks_find_an_editable_path_back_into_the_checkout(self):
        target = self.materialize()
        record = runtime.read_ready(target)
        site = Path(record["environment"]) / "lib" / "python3.12" / "site-packages"
        site.mkdir(parents=True)
        (site / "buddy.pth").write_text(str(REAL_ROOT / "src") + "\n")
        leaks = runtime.source_leaks({**record, "runtimeDir": str(target)})
        self.assertTrue(any("buddy.pth" in leak for leak in leaks), leaks)

    def test_source_leaks_find_a_resource_that_resolves_outside_the_runtime(self):
        target = self.materialize()
        record = runtime.read_ready(target)
        record["resources"] = {**record["resources"], "dsh.runner": str(REAL_ROOT / "harnesses" / "dsh" / "scripts" / "run.mjs")}
        leaks = runtime.source_leaks({**record, "runtimeDir": str(target)})
        self.assertTrue(any("dsh.runner" in leak for leak in leaks), leaks)

    def test_source_leaks_find_a_plugin_cache_path(self):
        target = self.materialize()
        record = runtime.read_ready(target)
        record["resources"] = {
            **record["resources"],
            "console.assets": "/Users/someone/.codex/plugins/cache/hey-my-buddy/console_assets",
        }
        leaks = runtime.source_leaks({**record, "runtimeDir": str(target)})
        self.assertTrue(any("plugins/cache" in leak for leak in leaks), leaks)

    def test_source_leaks_find_a_pyvenv_that_references_the_project(self):
        target = self.materialize()
        record = runtime.read_ready(target)
        (Path(record["environment"]) / "pyvenv.cfg").write_text(f"home = /usr/bin\nproject = {REAL_ROOT}\n")
        leaks = runtime.source_leaks({**record, "runtimeDir": str(target)})
        self.assertTrue(any("references the project source tree" in leak for leak in leaks), leaks)


class CheckoutRuntimeTests(unittest.TestCase):
    """The real checkout declares exactly the current resources and identity."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="buddy-runtime-real-", dir="/tmp")
        self.addCleanup(self.temp.cleanup)
        self.runtime_root = Path(self.temp.name) / "runtime"
        self.enterContext(private_runtime_root(self.runtime_root))

    def test_the_checkout_identity_uses_the_declared_manifest(self):
        with private_runtime_root(self.runtime_root):
            described = runtime.describe()
            resolved = runtime.resolve_runtime()
        self.assertEqual(len(described["contentId"]), 32)
        self.assertFalse(described["installed"])
        self.assertEqual(set(described["resources"]), set(runtime.declared_resources(REAL_ROOT)))
        for name, value in described["resources"].items():
            self.assertTrue(Path(value).is_relative_to(REAL_ROOT), name)
            self.assertTrue(Path(value).exists(), name)
        self.assertEqual(resolved["state"], "SOURCE")
        self.assertFalse(resolved["stable"])
        self.assertTrue(resolved["identity"].startswith("source:"))
        self.assertEqual(resolved["resourcesMissing"], [])
        self.assertEqual(runtime.source_leaks(resolved), [])
        self.assertEqual(set(resolved["actual"]["resources"]), set(runtime.declared_resources(REAL_ROOT)))
        self.assertNotIn("adapterScript", resolved["actual"])
        self.assertNotIn("yamlBridge", resolved["actual"])

    def test_launch_target_uses_the_current_source_tree(self):
        with private_runtime_root(self.runtime_root), patch.dict(os.environ, {"BUDDY_DEV_SOURCE": "1"}):
            target = runtime.launch_target()
        self.assertFalse(target["stable"])
        self.assertEqual(target["pythonPath"], str(REAL_ROOT / "src"))
        self.assertFalse(self.runtime_root.exists(), "development source must not materialize a runtime")


if __name__ == "__main__":
    unittest.main()
