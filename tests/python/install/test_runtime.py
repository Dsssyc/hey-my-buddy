"""Runtime manifest, identity and materialization tests.

Every test owns private project and runtime roots built from a synthetic declared
manifest, so nothing here reads or installs into the operator's runtime root.
Resource names belong to each manifest, independently of the installed harnesses.
"""
from __future__ import annotations

import hashlib
import json
import os
import ntpath
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path, PurePosixPath, PureWindowsPath
import tempfile
from types import SimpleNamespace
from threading import Event
import unittest
from unittest.mock import patch

from hey_my_buddy.install import runtime
from hey_my_buddy import locking
from hey_my_buddy.errors import BoardError


class RuntimeInterpreterTests(unittest.TestCase):
    def test_platform_interpreter_layout(self):
        directory = Path('/private/runtime')
        with patch.object(runtime, 'os', SimpleNamespace(name='nt')):
            self.assertEqual(str(runtime.runtime_python(directory)).replace('\\', '/'), '/private/runtime/venv/Scripts/python.exe')
        self.assertEqual(runtime.runtime_python(directory), directory / 'venv/bin/python')


class LexicalWindowsPath(PureWindowsPath):
    def resolve(self):
        raise OSError("no Windows filesystem in this lexical fixture")


class RuntimePathTests(unittest.TestCase):
    def test_windows_lexical_interpreter_containment(self):
        with patch.object(runtime, "Path", LexicalWindowsPath), patch.object(
            runtime, "os", SimpleNamespace(path=ntpath)
        ):
            for root, child in ((r"D:\runtime", "d:/RUNTIME/venv/Scripts/python.exe"),
                                (r"\\server\share\runtime", "//SERVER/share/runtime/venv/Scripts/python.exe")):
                with self.subTest(root=root):
                    self.assertTrue(runtime._inside(LexicalWindowsPath(root), child, allow_lexical=True))
                    self.assertFalse(runtime._inside(LexicalWindowsPath(root), child))
            for child in (r"D:\runtime-other\python.exe", r"E:\runtime\python.exe"):
                self.assertFalse(runtime._inside(LexicalWindowsPath(r"D:\runtime"), child, allow_lexical=True))

    def test_declared_relative_rejects_every_native_anchor(self):
        for flavor, anchored in (
            (PureWindowsPath, ("/x", "D:x", "D:/x", "//server/share/x", "D:")),
            (PurePosixPath, ("/x", "//x")),
        ):
            with self.subTest(platform=flavor.__name__), patch.object(runtime, "Path", flavor):
                for value in anchored:
                    with self.subTest(value=value), self.assertRaises(BoardError) as failure:
                        runtime._declared_relative(value, "asset", flavor("manifest.json"))
                    self.assertEqual(failure.exception.code, "RUNTIME_MANIFEST_INVALID")
                self.assertEqual(runtime._declared_relative("a/b", "asset", flavor("manifest.json")), "a/b")

REAL_ROOT = Path(__file__).resolve().parents[3]

DEFAULT_ASSETS = [
    {"path": "packaging/runtime-assets.json", "kind": "file"},
    {"path": "pyproject.toml", "kind": "file"},
    {"path": "src/hey_my_buddy", "kind": "directory"},
    {"path": "resources", "kind": "directory"},
]
DEFAULT_RESOURCES = {
    "fixture.runner": "resources/runner.py",
    "fixture.catalog": "resources/catalog.json",
    "fixture.bridge": "src/hey_my_buddy/fixture_bridge.py",
    "console.assets": "src/hey_my_buddy/console/assets",
}


def write_assets(root: Path) -> None:
    """The minimal set of declared files a synthetic runtime root needs."""
    bridge = root / "src" / "hey_my_buddy"
    bridge.mkdir(parents=True, exist_ok=True)
    (bridge / "fixture_bridge.py").write_text("# synthetic Python resource\n", encoding="utf-8", newline="\n")
    console = root / "src" / "hey_my_buddy" / "console" / "assets"
    console.mkdir(parents=True, exist_ok=True)
    (console / "index.html").write_text("<html>console</html>\n", encoding="utf-8", newline="\n")
    cache = root / "src" / "hey_my_buddy" / "__pycache__"
    cache.mkdir(parents=True, exist_ok=True)
    (cache / "fixture_bridge.cpython-312.pyc").write_bytes(b"\0compiled")
    (root / "resources").mkdir(parents=True, exist_ok=True)
    for name in ("runner.py", "catalog.json", "decision.py"):
        (root / "resources" / name).write_text("{}\n" if name.endswith(".json") else f"# {name}\n", encoding="utf-8", newline="\n")
    (root / "pyproject.toml").write_text('[project]\nname = "synthetic"\nversion = "0.0.0"\n', encoding="utf-8", newline="\n")


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
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8", newline="\n")
    return path


def fake_uv(directory: Path) -> Path:
    """A stand-in uv that publishes an interpreter without any dependency install."""
    directory.mkdir(parents=True, exist_ok=True)
    script = directory / "uv"
    script.write_text(
        f"#!{sys.executable}\n"
        "import os\n"
        "from pathlib import Path, PurePosixPath, PureWindowsPath\n"
        "base = Path(__file__).parent\n"
        "with (base / 'invocations').open('a', encoding='utf-8', newline='\\n') as stream:\n"
        "    stream.write('sync\\n')\n"
        "if (base / 'fail').exists():\n"
        "    raise SystemExit(7)\n"
        "interpreter = Path(os.environ['UV_PROJECT_ENVIRONMENT']) / 'bin' / 'python'\n"
        "interpreter.parent.mkdir(parents=True, exist_ok=True)\n"
        "interpreter.write_text('#!/bin/sh\\n', encoding='utf-8', newline='\\n')\n"
        "interpreter.chmod(0o755)\n", encoding="utf-8", newline="\n"
    )
    script.chmod(0o755)
    return script


@contextmanager
def private_runtime_root(directory: Path):
    """Keep state, runtime discovery and fake uv free of inherited Buddy authority."""
    environment = {
        name: value
        for name, value in os.environ.items()
        if not name.startswith("HMB_") and name not in ("VIRTUAL_ENV", "UV_PROJECT_ENVIRONMENT")
    }
    environment.update(
        HMB_CONSOLE_PORT="0",
        HMB_STATE_DIR=str(directory.parent / "state"),
        HMB_RUNTIME_ROOT=str(directory),
        HMB_DEV_SOURCE="1",
        HMB_CLAUDE_CLI=str(directory / "claude-not-installed"),
    )
    with patch.dict(os.environ, environment, clear=True):
        yield


class DeclaredManifestTests(unittest.TestCase):
    """The manifest is the only layout declaration; nothing is guessed."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="hey-my-buddy-runtime-", dir=os.environ.get("HMB_CHECKS_TMPDIR", "/tmp"))
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
        decoy = self.root / "scripts" / "runner.py"
        decoy.parent.mkdir(parents=True)
        decoy.write_text("// old layout\n", encoding="utf-8", newline="\n")
        (self.root / "python").mkdir()
        self.manifest_path.unlink()
        with self.assertRaises(BoardError) as failure:
            runtime.resource_path("fixture.runner", self.root)
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
        runner.write_text("# a different harness\n", encoding="utf-8", newline="\n")
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
            runtime.resource_path("fixture.runner", self.root)
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
        for relative in ("undeclared/runner.py", "src/hey_my_buddy-neighbor/runner.py", "pyproject.toml/runner.py"):
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
        for relative in ("pyproject.toml", "resources"):
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
        runner = self.root / DEFAULT_RESOURCES["fixture.runner"]
        outside = self.root.parent / "outside-runner.mjs"
        runner.rename(outside)
        runner.symlink_to(outside)
        with self.assertRaises(BoardError) as failure:
            runtime.resource_path("fixture.runner", self.root)
        self.assertEqual(failure.exception.code, "RUNTIME_MANIFEST_INVALID")
        self.assertIn("fixture.runner", failure.exception.message)

    def test_nested_asset_symlinks_outside_the_root_are_never_copied(self):
        outside = self.root.parent / "outside.py"
        outside.write_text("# not part of the declared root\n", encoding="utf-8", newline="\n")
        linked = self.root / "src/hey_my_buddy/external.py"
        linked.symlink_to(outside)
        destination = self.root.parent / "copied"
        with self.assertRaises(BoardError) as failure:
            runtime._copy_assets(self.root, destination)
        self.assertEqual(failure.exception.code, "RUNTIME_ASSET_OUTSIDE_ROOT")
        self.assertFalse((destination / "src/hey_my_buddy/external.py").exists())

    def test_a_missing_declared_asset_is_reported_not_skipped(self):
        (self.root / "pyproject.toml").unlink()
        with self.assertRaises(BoardError) as failure:
            list(runtime.iter_assets(self.root))
        self.assertEqual(failure.exception.code, "RUNTIME_ASSET_MISSING")
        runtime.resource_path("fixture.runner", self.root).unlink()
        self.assertEqual(runtime.missing_resources(self.root), ["fixture.runner"])

    def test_iter_assets_copies_only_declared_content(self):
        (self.root / "tests").mkdir()
        (self.root / "tests" / "test_scratch.py").write_text("# not a runtime asset\n", encoding="utf-8", newline="\n")
        (self.root / ".venv").mkdir()
        (self.root / ".venv" / "pyvenv.cfg").write_text("home = /nope\n", encoding="utf-8", newline="\n")
        (self.root / "resources" / "__pycache__").mkdir()
        (self.root / "resources" / "__pycache__" / "stale.pyc").write_bytes(b"\0")
        relative = {path for path, _file in runtime.iter_assets(self.root)}
        self.assertEqual(
            relative,
            {
                "packaging/runtime-assets.json",
                "pyproject.toml",
                "src/hey_my_buddy/fixture_bridge.py",
                "src/hey_my_buddy/console/assets/index.html",
                "resources/runner.py",
                "resources/catalog.json",
                "resources/decision.py",
            },
        )

    def test_content_id_covers_every_asset_and_the_manifest_itself(self):
        first = runtime.content_id(self.root)
        self.assertEqual(len(first), 32)
        (self.root / "resources" / "runner.py").write_text("// changed\n", encoding="utf-8", newline="\n")
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
        self.temp = tempfile.TemporaryDirectory(prefix="hey-my-buddy-runtime-build-", dir=os.environ.get("HMB_CHECKS_TMPDIR", "/tmp"))
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
            "src/hey_my_buddy/fixture_bridge.py",
            "src/hey_my_buddy/console/assets/index.html",
            "resources/runner.py",
            "resources/catalog.json",
            "resources/decision.py",
        ):
            self.assertTrue((target / relative).is_file(), relative)
        self.assertFalse((target / "tests").exists())
        self.assertFalse((target / ".venv").exists())
        self.assertTrue((target.parent / runtime.INSTALL_LOCK).is_file())
        self.assertFalse((target / "src" / "hey_my_buddy" / "__pycache__").exists())
        self.assertTrue(Path(record["python"]).is_file())
        self.assertEqual(runtime.find_ready(self.root, self.runtime_root), target)
        again = runtime.materialize(self.root, destination=self.runtime_root, uv_bin=str(self.uv))
        self.assertFalse(again["installed"])
        self.assertEqual(Path(again["runtimeDir"]), target)

    def test_materialize_accepts_a_manifest_with_only_another_harness(self):
        resources = {"new-harness.runner": "src/hey_my_buddy/fixture_bridge.py"}
        write_manifest(self.root, resources=resources)
        target = self.materialize()
        self.assertTrue(runtime.is_ready(target))
        self.assertEqual(runtime.read_ready(target)["resources"], {name: str(target / path) for name, path in resources.items()})

    def test_concurrent_installers_share_one_lock_until_ready(self):
        first_building, release, second_attempted = Event(), Event(), Event()
        real_run, real_lock = runtime.subprocess.run, locking.lock
        acquisitions = []

        def controlled_run(command, **options):
            if command[0] == str(self.uv):
                first_building.set()
                if not release.wait(10):
                    raise AssertionError("first installer was never released")
            return real_run(command, **options)

        def observe_lock(fd, **options):
            acquisitions.append(fd)
            if len(acquisitions) == 2:
                second_attempted.set()
            return real_lock(fd, **options)

        options = {"destination": self.runtime_root, "uv_bin": str(self.uv)}
        target = runtime.runtime_dir(self.root, self.runtime_root)
        lock_path = target.parent / runtime.INSTALL_LOCK
        with ThreadPoolExecutor(max_workers=2) as pool, \
             patch.object(runtime.subprocess, "run", side_effect=controlled_run), \
             patch.object(locking, "lock", side_effect=observe_lock):
            first = pool.submit(runtime.materialize, self.root, **options)
            try:
                self.assertTrue(first_building.wait(5))
                second = pool.submit(runtime.materialize, self.root, **options)
                self.assertTrue(second_attempted.wait(5))
                probe_fd = os.open(lock_path, os.O_RDWR)
                try:
                    with self.assertRaises(BlockingIOError):
                        real_lock(probe_fd, blocking=False)
                finally:
                    os.close(probe_fd)
                self.assertFalse((target / runtime.READY_FILE).exists())
                self.assertFalse(second.done())
            finally:
                release.set()
            results = [first.result(timeout=15), second.result(timeout=15)]

        self.assertEqual(sorted(result["installed"] for result in results), [False, True])
        self.assertEqual(results[0]["runtime"], results[1]["runtime"])
        self.assertTrue(runtime.is_ready(target))
        self.assertEqual((self.uv.parent / "invocations").read_text(encoding="utf-8").splitlines(), ["sync"])
        self.assertTrue(lock_path.is_file())
        self.assertEqual(len(acquisitions), 2)

    def test_different_runtime_installers_use_the_same_root_lock(self):
        first_copying, release, second_attempted = Event(), Event(), Event()
        other = self.root.with_name("other-project")
        write_assets(other)
        write_manifest(other, resources={"other.runner": "resources/runner.py"})
        self.assertNotEqual(runtime.content_id(self.root), runtime.content_id(other))
        real_copy, real_lock = runtime._copy_assets, locking.lock
        acquisitions = []

        def controlled_copy(source, target):
            if source == self.root:
                first_copying.set()
                if not release.wait(10):
                    raise AssertionError("first installer was never released")
            else:
                self.assertTrue(release.is_set())
            return real_copy(source, target)

        def observe_lock(fd, **options):
            acquisitions.append(fd)
            if len(acquisitions) == 2:
                second_attempted.set()
            return real_lock(fd, **options)

        options = {"destination": self.runtime_root, "uv_bin": str(self.uv)}
        with ThreadPoolExecutor(max_workers=2) as pool, \
             patch.object(runtime, "_copy_assets", side_effect=controlled_copy), \
             patch.object(locking, "lock", side_effect=observe_lock):
            first = pool.submit(runtime.materialize, self.root, **options)
            try:
                self.assertTrue(first_copying.wait(5))
                second = pool.submit(runtime.materialize, other, **options)
                self.assertTrue(second_attempted.wait(5))
                self.assertFalse(second.done())
            finally:
                release.set()
            self.assertTrue(first.result(timeout=15)["installed"])
            self.assertTrue(second.result(timeout=15)["installed"])
        self.assertEqual(len(acquisitions), 2)
        self.assertTrue((self.runtime_root / runtime.INSTALL_LOCK).is_file())

    def test_installation_never_deletes_an_open_lock_even_when_windows_would_refuse(self):
        opened = set()
        real_lock, real_unlink = runtime.installation_lock, Path.unlink

        @contextmanager
        def tracked_lock(path, **options):
            with real_lock(path, **options) as fd:
                opened.add(path)
                try:
                    yield fd
                finally:
                    opened.remove(path)

        def windows_unlink(path, *args, **kwargs):
            if path in opened:
                raise PermissionError("Windows refuses deletion of an open file")
            return real_unlink(path, *args, **kwargs)

        with patch.object(runtime, "installation_lock", side_effect=tracked_lock), \
             patch.object(Path, "unlink", windows_unlink):
            failure = self.uv.parent / "fail"
            failure.touch()
            with self.assertRaises(BoardError):
                self.materialize()
            failure.unlink()
            self.materialize()
        self.assertTrue((self.runtime_root / runtime.INSTALL_LOCK).is_file())

    def test_a_failed_install_retries_and_preserves_its_lock_after_success(self):
        failure_marker = self.uv.parent / "fail"
        failure_marker.touch()
        target = runtime.runtime_dir(self.root, self.runtime_root)
        lock_path = target.parent / runtime.INSTALL_LOCK
        with self.assertRaises(BoardError) as failure:
            runtime.materialize(self.root, destination=self.runtime_root, uv_bin=str(self.uv))
        self.assertEqual(failure.exception.code, "RUNTIME_INSTALL_FAILED")
        self.assertFalse(runtime.is_ready(target))
        self.assertFalse(target.exists())
        self.assertTrue(lock_path.is_file())
        failure_marker.unlink()
        self.assertEqual(self.materialize(), target)
        self.assertTrue(runtime.is_ready(target))
        self.assertTrue(lock_path.is_file())
        self.assertEqual((self.uv.parent / "invocations").read_text(encoding="utf-8").splitlines(), ["sync", "sync"])

    def test_materialize_rebuilds_only_an_unpublished_directory(self):
        target = runtime.runtime_dir(self.root, self.runtime_root)
        target.mkdir(parents=True)
        partial = target / 'unfinished'
        partial.write_bytes(b'partial build')
        self.assertEqual(self.materialize(), target)
        self.assertFalse(partial.exists())

    def test_materialize_preserves_any_unusable_published_marker(self):
        target = self.materialize()
        marker = target / runtime.READY_FILE
        original = marker.read_bytes()
        interpreter = runtime.runtime_python(target)
        for replacement in (b'{', b'{}', b'[]', original.replace(b'"READY"', b'"BUILDING"'),
                            original.replace(target.name.encode(), b'0' * 32)):
            with self.subTest(marker=replacement[:30]):
                marker.write_bytes(replacement)
                with self.assertRaises(BoardError) as failure:
                    runtime.materialize(self.root, destination=self.runtime_root, uv_bin=str(self.uv))
                self.assertEqual(failure.exception.code, 'RUNTIME_NOT_READY')
                self.assertEqual(marker.read_bytes(), replacement)
                self.assertTrue(interpreter.is_file())
        marker.write_bytes(original)
        interpreter.unlink()
        with self.assertRaises(BoardError) as failure:
            runtime.materialize(self.root, destination=self.runtime_root, uv_bin=str(self.uv))
        self.assertEqual(failure.exception.code, 'RUNTIME_NOT_READY')
        self.assertEqual(marker.read_bytes(), original)

    def test_materialize_preserves_a_runtime_when_the_marker_is_unreadable(self):
        target = self.materialize()
        marker = target / runtime.READY_FILE
        original = marker.read_bytes()
        with patch.object(runtime, '_read_ready_file', side_effect=PermissionError('READY read denied')):
            with self.assertRaises(BoardError) as failure:
                runtime.materialize(self.root, destination=self.runtime_root, uv_bin=str(self.uv))
        self.assertEqual(failure.exception.code, 'RUNTIME_NOT_READY')
        self.assertEqual(marker.read_bytes(), original)

    def test_materialize_reports_a_failed_removal_before_replacing_partial_content(self):
        target = runtime.runtime_dir(self.root, self.runtime_root)
        target.mkdir(parents=True)
        sentinel = target / 'unfinished'
        sentinel.write_bytes(b'partial')
        remove = runtime.shutil.rmtree

        def refuse_target(path, *args, **kwargs):
            if path == target:
                raise PermissionError('partial removal denied')
            return remove(path, *args, **kwargs)

        with patch.object(runtime.shutil, 'rmtree', side_effect=refuse_target), self.assertRaises(PermissionError):
            runtime.materialize(self.root, destination=self.runtime_root, uv_bin=str(self.uv))
        self.assertEqual(sentinel.read_bytes(), b'partial')

    def test_runtime_and_skill_share_an_archive_source_identity(self):
        metadata = self.root / 'src/hey_my_buddy/build-info.json'
        metadata.write_text('{"sourceCommit":null}', encoding='utf-8')
        with patch('hey_my_buddy.install.skill_package.subprocess.check_output', return_value='a' * 40) as git:
            target = self.materialize()
        git.assert_not_called()
        self.assertIsNone(runtime.read_ready(target)['sourceCommit'])

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

    def test_installed_role_resources_and_prompts_survive_source_replacement(self):
        for relative, source in runtime.iter_assets(REAL_ROOT):
            copied = self.root / relative
            copied.parent.mkdir(parents=True, exist_ok=True)
            copied.write_bytes(source.read_bytes())
        target = self.materialize()
        expected = {name: runtime.resource_path("roles." + name, self.root).read_text(encoding="utf-8")
                    for name in ("worker", "router", "lead", "wrap_up")}
        replaced = self.root.with_name("project-replaced")
        self.root.rename(replaced)
        try:
            environment = {name: value for name, value in os.environ.items()
                           if not name.startswith("HMB_") and name not in ("VIRTUAL_ENV", "UV_PROJECT_ENVIRONMENT")}
            environment["PYTHONPATH"] = str(target / "src")
            probe = subprocess.run(
                [sys.executable, "-c",
                 "import json; from hey_my_buddy.install.runtime import resource_path; "
                 "from hey_my_buddy.buddy.roles.prompts import worker_prompt, router_prompt, wrap_up_prompt; "
                 "print(json.dumps({'resources': {n: [str(resource_path('roles.'+n)), "
                 "resource_path('roles.'+n).read_text(encoding='utf-8')] for n in ('worker','router','lead','wrap_up')}, "
                 "'worker': worker_prompt('ROLE: task', {}, delivery=()), 'router': router_prompt('ROLE: task'), "
                 "'wrap_up': wrap_up_prompt()}))"],
                cwd=target, env=environment, capture_output=True, text=True, timeout=30, encoding="utf-8")
            self.assertEqual(probe.returncode, 0, probe.stderr)
            actual = json.loads(probe.stdout)
            for name, (path, text) in actual["resources"].items():
                self.assertTrue(Path(path).is_relative_to(target), name)
                self.assertEqual(text, expected[name])
            self.assertTrue(actual["worker"].startswith(expected["worker"] + "\n"))
            self.assertTrue(actual["router"].startswith(expected["router"] + "\n"))
            self.assertEqual(actual["wrap_up"], expected["wrap_up"])
            self.assertTrue(runtime.is_ready(target))
        finally:
            replaced.rename(self.root)

    def test_a_published_runtime_reports_missing_resources_in_its_identity(self):
        target = self.materialize()
        Path(runtime.read_ready(target)["resources"]["fixture.runner"]).unlink()
        self.assertTrue(runtime.is_ready(target))
        with patch.dict(os.environ, {"HMB_RUNTIME": str(target)}), patch.object(runtime, "project_root", return_value=target):
            resolved = runtime.resolve_runtime()
        self.assertFalse(resolved["stable"])
        self.assertIn("fixture.runner", resolved["resourcesMissing"])

    def test_a_published_runtime_reports_an_outward_resource_symlink_as_a_leak(self):
        target = self.materialize()
        record = runtime.read_ready(target)
        runner = Path(record["resources"]["fixture.runner"])
        outside = self.root / DEFAULT_RESOURCES["fixture.runner"]
        runner.unlink()
        runner.symlink_to(outside)
        self.assertTrue(runtime.is_ready(target))
        self.assertTrue(any("fixture.runner resolves outside" in leak for leak in runtime.source_leaks(record)))
        with patch.dict(os.environ, {"HMB_RUNTIME": str(target)}), patch.object(runtime, "project_root", return_value=target):
            with self.assertRaises(BoardError) as failure:
                runtime.resolve_runtime()
        self.assertEqual(failure.exception.code, 'RUNTIME_MANIFEST_INVALID')

    def test_a_shared_base_interpreter_symlink_is_allowed(self):
        target = self.materialize()
        record = runtime.read_ready(target)
        interpreter = Path(record["python"])
        interpreter.unlink()
        interpreter.symlink_to(sys.executable)
        self.assertTrue(runtime.is_ready(target))
        actual = {
            "package": str(target / "src/hey_my_buddy"),
            "prefix": record["environment"],
            "executable": record["python"],
            "resources": record["resources"],
            "missingResources": [],
        }
        with patch.dict(os.environ, {"HMB_RUNTIME": str(target)}), patch.object(runtime, "process_identity", return_value=actual):
            resolved = runtime.resolve_runtime()
        self.assertTrue(resolved["stable"])
        self.assertEqual(resolved["leaks"], [])

    def test_resolve_runtime_does_not_trust_a_ready_marker_alone(self):
        target = self.materialize()
        with private_runtime_root(self.runtime_root), patch.dict(os.environ, {"HMB_RUNTIME": str(target)}), patch.object(
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
        (site / "buddy.pth").write_text(str(REAL_ROOT / "src") + "\n", encoding="utf-8", newline="\n")
        leaks = runtime.source_leaks({**record, "runtimeDir": str(target)})
        self.assertTrue(any("buddy.pth" in leak for leak in leaks), leaks)

    def test_source_leaks_find_a_resource_that_resolves_outside_the_runtime(self):
        target = self.materialize()
        record = runtime.read_ready(target)
        record["resources"] = {**record["resources"], "fixture.runner": str(REAL_ROOT / "src" / "hey_my_buddy" / "cli" / "main.py")}
        leaks = runtime.source_leaks({**record, "runtimeDir": str(target)})
        self.assertTrue(any("fixture.runner" in leak for leak in leaks), leaks)

    def test_source_leaks_does_not_hide_an_unreadable_pth_file(self):
        target = self.materialize()
        record = runtime.read_ready(target)
        site = Path(record['environment']) / 'lib/python3.12/site-packages'
        site.mkdir(parents=True)
        pth = site / 'runtime.pth'
        pth.write_bytes(b'fixture')
        read = Path.read_text

        def refuse_pth(path, *args, **kwargs):
            if path == pth:
                raise PermissionError('pth read denied')
            return read(path, *args, **kwargs)

        with patch.object(Path, 'read_text', refuse_pth), self.assertRaises(PermissionError):
            runtime.source_leaks(record)

    def test_source_leaks_find_a_pyvenv_that_references_the_project(self):
        target = self.materialize()
        record = runtime.read_ready(target)
        (Path(record["environment"]) / "pyvenv.cfg").write_text(f"home = /usr/bin\nproject = {REAL_ROOT}\n", encoding="utf-8", newline="\n")
        leaks = runtime.source_leaks({**record, "runtimeDir": str(target)})
        self.assertTrue(any("references the project source tree" in leak for leak in leaks), leaks)


class CheckoutRuntimeTests(unittest.TestCase):
    """The real checkout declares exactly the current resources and identity."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="hey-my-buddy-runtime-real-", dir=os.environ.get("HMB_CHECKS_TMPDIR", "/tmp"))
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
        with private_runtime_root(self.runtime_root), patch.dict(os.environ, {"HMB_DEV_SOURCE": "1"}):
            target = runtime.launch_target()
        self.assertFalse(target["stable"])
        self.assertEqual(target["pythonPath"], str(REAL_ROOT / "src"))
        self.assertFalse(self.runtime_root.exists(), "development source must not materialize a runtime")


if __name__ == "__main__":
    unittest.main()
