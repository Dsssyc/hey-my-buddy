#!/usr/bin/env python3
"""Stage the supported plugin: one current layout, no facades, no tests.

The plugin ships exactly what a user runs — the uv project (root ``pyproject.toml``
and ``uv.lock``), the single ``bin/buddy`` launcher, ``src/buddy``, the DSH runtime
assets declared in ``packaging/runtime-assets.json`` (that manifest included), the
one ``skills/buddy`` entrypoint, the ``.agents`` marketplace catalog and the current
documentation. Test suites, virtual environments, the React source frontend and local
scratch are excluded by construction, and the staged inventory is verified before it
replaces the destination.
"""
import argparse
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time

SOURCE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SOURCE_ROOT / "src"))

from buddy.runtime import iter_assets  # noqa: E402

#: Plugin metadata plus the current documentation and the one agent entrypoint. The
#: committed ``.agents`` catalog makes this repository a local/Git marketplace, and the
#: staged copy carries it too: a staged plugin directory is itself a marketplace root
#: that needs no personal or public catalog.
PLUGIN_PATHS = (
    ".agents",
    ".codex-plugin",
    "plugin.json",
    "LICENSE",
    "AGENTS.md",
    "README.md",
    "README.zh-CN.md",
    "docs",
    "skills/buddy",
)
#: Path parts that are never redistributed, even if a future manifest entry includes them.
UNSUPPORTED_PARTS = ("tests", "node_modules", ".venv", "__pycache__", ".git", ".dsh-skill-build", "apps")
IGNORED_COPY = ("__pycache__", "*.pyc", "*.pyo")
METADATA_KEYS = ("name", "version", "description", "author", "license")


def _copy_path(source: Path, staged: Path, relative: str) -> None:
    origin = source / relative
    if not origin.exists():
        raise SystemExit(f"The current layout is missing the supported plugin path {relative}")
    target = staged / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    if origin.is_dir():
        shutil.copytree(origin, target, ignore=shutil.ignore_patterns(*IGNORED_COPY))
    else:
        shutil.copy2(origin, target)


def _copy_asset(source: Path, staged: Path, relative: str) -> None:
    target = staged / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source / relative, target)


def refresh_portable_metadata(staged: Path) -> None:
    """One version identity: the portable plugin.json mirrors the Codex manifest."""
    identity = json.loads((staged / ".codex-plugin/plugin.json").read_text())
    portable = json.loads((staged / "plugin.json").read_text())
    portable.update({key: identity[key] for key in METADATA_KEYS})
    (staged / "plugin.json").write_text(json.dumps(portable, indent=2) + "\n")


def assert_supported_inventory(staged: Path) -> None:
    """The staged tree carries only the current layout: no suite, venv or scratch."""
    for path in sorted(staged.rglob("*")):
        parts = path.relative_to(staged).parts
        unsupported = next((part for part in parts if part in UNSUPPORTED_PARTS), None)
        if unsupported:
            raise SystemExit(f"Refusing to stage unsupported content: {'/'.join(parts)}")
        if path.suffix in (".pyc", ".pyo"):
            raise SystemExit(f"Refusing to stage a compiled Python file: {'/'.join(parts)}")


def stage(source: Path, destination: Path) -> Path:
    """Assemble the supported plugin next to *destination* and publish it atomically."""
    source = Path(source).resolve()
    dest = Path(destination).expanduser().absolute()
    if dest.name != "hey-my-buddy" or dest == source:
        raise SystemExit("Destination must be a separate hey-my-buddy plugin directory")
    assets = [relative for relative, _path in iter_assets(source)]
    dest.parent.mkdir(parents=True, exist_ok=True)
    staged = Path(tempfile.mkdtemp(prefix=".buddy-stage-", dir=dest.parent))
    try:
        for relative in PLUGIN_PATHS:
            _copy_path(source, staged, relative)
        for relative in assets:
            _copy_asset(source, staged, relative)
        try:
            commit = subprocess.check_output(["git", "-C", str(source), "rev-parse", "HEAD"], text=True, stderr=subprocess.DEVNULL).strip()
        except (OSError, subprocess.CalledProcessError):
            metadata = source / "src/buddy/build-info.json"
            commit = json.loads(metadata.read_text()).get("sourceCommit") if metadata.exists() else None
        (staged / "src/buddy/build-info.json").write_text(json.dumps({"sourceCommit": commit}) + "\n")
        assert_supported_inventory(staged)
        refresh_portable_metadata(staged)
        expected = {relative.split("/")[0] for relative in (*PLUGIN_PATHS, *assets)}
        actual = {path.name for path in staged.iterdir()}
        if actual != expected:
            raise SystemExit(f"Staged inventory {sorted(actual)} does not match {sorted(expected)}")
        if dest.is_symlink():
            if dest.resolve() != source:
                raise SystemExit("Refusing to replace an unrelated source link")
            dest.unlink()
        elif dest.exists():
            backup = dest.with_name(dest.name + ".previous-" + str(time.time_ns()))
            dest.rename(backup)
        staged.rename(dest)
        return dest
    finally:
        if staged.exists():
            shutil.rmtree(staged, ignore_errors=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--destination", type=Path, required=True)
    args = parser.parse_args()
    print(stage(Path(__file__).resolve().parents[1], args.destination))


if __name__ == "__main__":
    main()
