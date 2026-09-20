"""The stable, content-identified runtime the service and its workers execute from.

The Codex plugin cache may disappear on upgrade, so the default service, its
workers and the dsh adapter must run from a versioned runtime outside that cache.
Materialization is explicit:

1. copy the complete runtime assets into the *final* content-addressed directory;
2. run ``uv sync --frozen`` there, with the environment inside that directory;
3. write ``READY.json`` last — the directory is unused until that marker exists.

Only runtime assets are materialized. Credentials, user data, tests, node_modules
and any existing virtual environment are never copied.
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from .db import canonical_json
from .errors import BoardError

RUNTIME_FORMAT = 1
READY_FILE = "READY.json"
DEFAULT_RUNTIME_ROOT = Path.home() / ".local/share/hey-my-buddy/runtime"

#: Runtime assets, relative to the project (``deepseek-delegate``) root. Anything
#: not listed here is not part of the distributable runtime.
ASSET_FILES = (
    "pyproject.toml",
    "uv.lock",
    "package.json",
    "scripts/buddy.mjs",
    "scripts/run.mjs",
    "scripts/handoff.mjs",
)
ASSET_DIRECTORIES = (
    "python/buddy",
    "scripts/lib",
    "plugins",
)
IGNORED_NAMES = ("__pycache__", "*.pyc", "*.pyo", ".venv", "node_modules", ".env", ".DS_Store")


def project_root() -> Path:
    """The project directory that owns ``pyproject.toml`` and the runtime assets."""
    return Path(__file__).resolve().parents[2]


def runtime_root() -> Path:
    return Path(os.environ.get("BUDDY_RUNTIME_ROOT") or DEFAULT_RUNTIME_ROOT).expanduser()


def _iter_assets(root: Path):
    for relative in ASSET_FILES:
        path = root / relative
        if path.is_file():
            yield relative, path
    for relative in ASSET_DIRECTORIES:
        directory = root / relative
        if not directory.is_dir():
            continue
        for path in sorted(directory.rglob("*")):
            if not path.is_file():
                continue
            parts = set(path.parts)
            if any(ignore.strip("*") in name for name in parts for ignore in IGNORED_NAMES if ignore.strip("*")):
                continue
            if path.suffix in (".pyc", ".pyo"):
                continue
            yield str(path.relative_to(root)), path


def content_id(root: Path | None = None) -> str:
    """Deterministic identity of the runtime assets, independent of their location."""
    root = Path(root or project_root())
    digest = hashlib.sha256()
    digest.update(f"buddy-runtime-format:{RUNTIME_FORMAT}\n".encode())
    for relative, path in sorted(_iter_assets(root)):
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(hashlib.sha256(path.read_bytes()).hexdigest().encode())
        digest.update(b"\n")
    return digest.hexdigest()[:32]


def manifest(root: Path | None = None) -> dict:
    root = Path(root or project_root())
    files = {
        relative: hashlib.sha256(path.read_bytes()).hexdigest() for relative, path in sorted(_iter_assets(root))
    }
    return {"format": RUNTIME_FORMAT, "files": files, "contentId": content_id(root)}


def runtime_dir(root: Path | None = None, destination: Path | None = None) -> Path:
    base = Path(destination) if destination else runtime_root()
    return base / content_id(root)


def is_ready(directory: Path) -> bool:
    try:
        value = json.loads((directory / READY_FILE).read_text())
    except (OSError, ValueError):
        return False
    if not isinstance(value, dict) or value.get("format") != RUNTIME_FORMAT:
        return False
    if value.get("state") != "READY" or value.get("contentId") != directory.name:
        return False
    return Path(value.get("python", "")).is_file()


def materialize(
    root: Path | None = None,
    destination: Path | None = None,
    *,
    uv_bin: str | None = None,
    timeout_seconds: int = 900,
    log_path: Path | None = None,
) -> dict:
    """Install the stable runtime, or return the existing READY one unchanged.

    The complete assets are placed in the *final* content-addressed directory
    before ``uv sync --frozen`` runs there, so the environment, its ``pyvenv.cfg``,
    its ``.pth`` files and every script path refer to that final location.
    ``READY.json`` is written last and is the only thing that publishes it; an
    install lock makes concurrent installers cooperate instead of racing.
    """
    root = Path(root or project_root())
    target = runtime_dir(root, destination)
    if is_ready(target):
        return {"runtime": read_ready(target), "installed": False, "runtimeDir": str(target)}
    target.parent.mkdir(parents=True, exist_ok=True)
    # Install into a temporary sibling first only because the final name must be a
    # complete directory atomically; every path recorded inside is rewritten to the
    # final location before installation starts.
    working = Path(tempfile.mkdtemp(prefix=f".{target.name}.build-", dir=target.parent))
    lock_path = target.parent / f".{target.name}.install.lock"
    lock_fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        if is_ready(target):
            shutil.rmtree(working, ignore_errors=True)
            return {"runtime": read_ready(target), "installed": False, "runtimeDir": str(target)}
        _copy_assets(root, working)
        (working / "manifest.json").write_text(json.dumps(manifest(root), indent=2, sort_keys=True) + "\n")
        # Publish the complete source tree under the FINAL content-addressed name
        # before any environment is created inside it.
        if target.exists():
            shutil.rmtree(target, ignore_errors=True)
        working.rename(target)
    finally:
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        os.close(lock_fd)
        if working.exists():
            shutil.rmtree(working, ignore_errors=True)
        lock_path.unlink(missing_ok=True)

    environment = target / "venv"
    command = [
        uv_bin or os.environ.get("UV_BIN") or "uv",
        "sync",
        "--frozen",
        "--no-dev",
        "--python",
        "3.12",
        "--project",
        str(target),
    ]
    environment_variables = {**os.environ, "UV_PROJECT_ENVIRONMENT": str(environment), "VIRTUAL_ENV": ""}
    log_stream = log_path.open("ab") if log_path else subprocess.DEVNULL
    try:
        completed = subprocess.run(
            command,
            cwd=str(target),
            env=environment_variables,
            stdout=log_stream,
            stderr=subprocess.STDOUT if log_path else subprocess.DEVNULL,
            timeout=timeout_seconds,
            check=False,
        )
    except FileNotFoundError as exc:
        raise BoardError("UV_NOT_FOUND", "uv is required to install the stable runtime") from exc
    except subprocess.TimeoutExpired as exc:
        raise BoardError("RUNTIME_INSTALL_TIMEOUT", "Installing the stable runtime timed out") from exc
    finally:
        if log_path:
            log_stream.close()
    if completed.returncode != 0:
        # An incomplete runtime is never READY and is removed so nothing can attach.
        shutil.rmtree(target, ignore_errors=True)
        raise BoardError(
            "RUNTIME_INSTALL_FAILED",
            "uv sync failed while installing the stable runtime; see the install log",
            log=str(log_path) if log_path else None,
        )
    interpreter = environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    if not interpreter.is_file():
        shutil.rmtree(target, ignore_errors=True)
        raise BoardError("RUNTIME_INSTALL_FAILED", "The runtime environment has no Python interpreter")
    ready = {
        "format": RUNTIME_FORMAT,
        "state": "READY",
        "contentId": target.name,
        "source": str(target),
        "environment": str(environment),
        "python": str(interpreter),
        "runtimeRoot": str(target.parent),
        "installedAt": _now(),
        "contractSource": str(target / "python"),
    }
    _rewrite_environment_paths(environment, target)
    (target / READY_FILE).write_text(json.dumps(ready, indent=2, sort_keys=True) + "\n")
    return {"runtime": read_ready(target), "installed": True, "runtimeDir": str(target)}


def _rewrite_environment_paths(environment: Path, target: Path) -> None:
    """Point any absolute build-path reference at the final directory.

    ``uv`` normally records only the environment location, but a copied or
    pre-created environment could carry the build path; rewriting here keeps the
    runtime honest even then, and ``source_leaks`` verifies the result.
    """
    for configuration in (environment / "pyvenv.cfg",):
        if not configuration.is_file():
            continue
        text = configuration.read_text(errors="replace")
        if "build-" in text:
            configuration.write_text(text.replace(str(environment.parent), str(target)))


def _copy_assets(root: Path, destination: Path) -> None:
    for relative, path in _iter_assets(root):
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)


def read_ready(directory: Path) -> dict:
    value = json.loads((directory / READY_FILE).read_text())
    value["runtimeDir"] = str(directory)
    return value


def find_ready(root: Path | None = None, destination: Path | None = None) -> Path | None:
    target = runtime_dir(root, destination)
    return target if is_ready(target) else None


def process_identity() -> dict:
    """Where the *currently executing* code and interpreter really live."""
    import buddy as package

    package_directory = Path(package.__file__).resolve().parent
    return {
        # The venv's bin/python is normally a symlink to a shared base interpreter,
        # so ownership is decided from the lexical venv path and sys.prefix; the
        # resolved base interpreter is reported separately and is allowed to live in
        # a uv-managed shared location.
        "executable": os.path.abspath(sys.executable),
        "resolvedBaseInterpreter": str(Path(sys.executable).resolve()),
        "prefix": os.path.abspath(sys.prefix),
        "package": str(package_directory),
        "packageRoot": str(package_directory.parent),
        "adapterScript": str(Path(__file__).resolve().parents[2] / "scripts" / "run.mjs"),
        "yamlBridge": str(Path(__file__).resolve().parent / "yaml_bridge.py"),
    }


def _inside(directory: Path | None, candidate: str | None) -> bool:
    """True when *candidate* lives inside *directory*.

    Both a lexical and a fully resolved comparison are accepted, because a macOS
    ``/var`` -> ``/private/var`` symlink (or a venv ``bin/python`` pointing at a
    shared base interpreter) makes one of the two disagree for a legitimate path.
    """
    if not directory or not candidate:
        return False
    root_text = os.path.abspath(str(directory))
    candidate_text = os.path.abspath(str(candidate))
    if candidate_text == root_text or candidate_text.startswith(root_text.rstrip("/") + "/"):
        return True
    try:
        resolved = Path(candidate).resolve()
        root = Path(directory).resolve()
    except OSError:
        return False
    return resolved == root or root in resolved.parents


def resolve_runtime() -> dict:
    """The runtime identity of *this process*, not merely of a directory on disk.

    ``BUDDY_RUNTIME`` pins an explicit runtime directory; otherwise the READY
    runtime matching the current assets is used. ``stable`` is only true when the
    process actually imported ``buddy`` from inside that runtime and no asset or
    interpreter path leaks back into a plugin cache or the checkout: finding a
    ``READY.json`` proves nothing by itself.
    """
    actual = process_identity()
    pinned = os.environ.get("BUDDY_RUNTIME")
    directory: Path | None = None
    if pinned:
        directory = Path(pinned).expanduser()
        if not is_ready(directory):
            raise BoardError(
                "RUNTIME_NOT_READY",
                "BUDDY_RUNTIME points at a directory without a READY runtime marker",
                runtimeDir=str(directory),
            )
    else:
        directory = find_ready()

    if directory is not None and is_ready(directory):
        record = read_ready(directory)
        running_from_runtime = (
            _inside(directory, actual["package"])
            and _inside(directory, actual["adapterScript"])
            and _inside(directory, actual["prefix"])
            and _inside(directory, actual["executable"])
        )
        leaks = source_leaks({**record, "runtimeDir": str(directory)})
        return {
            **record,
            "declaredStable": True,
            "inUse": running_from_runtime,
            "stable": running_from_runtime and not leaks,
            "actual": actual,
            "leaks": leaks,
            "identity": f"runtime:{directory.name}" if running_from_runtime else f"source:{content_id()[:12]}",
            "note": (
                "this process executes from the content-addressed runtime"
                if running_from_runtime
                else "a READY runtime exists but this process still executes from the project source tree"
            ),
        }
    return {
        "format": RUNTIME_FORMAT,
        "state": "SOURCE",
        "contentId": content_id(),
        "environment": sys.prefix,
        "python": sys.executable,
        "contractSource": str(project_root() / "python"),
        "declaredStable": False,
        "inUse": False,
        "stable": False,
        "actual": actual,
        "leaks": [],
        "identity": f"source:{content_id()[:12]}",
        "note": "no READY stable runtime is installed; this process runs from the project source tree",
    }


def launch_target(*, log_path: Path | None = None) -> dict:
    """Interpreter and environment for a child that must survive the plugin cache.

    The default path installs the stable runtime on first use and then starts the
    daemon, the workers and the adapters from that runtime's own interpreter and
    installed package. ``BUDDY_DEV_SOURCE=1`` is the explicit development/test
    switch that runs from the checkout instead and reports ``stable=false``; it is
    never the default, so a plugin update cannot silently reintroduce the
    cache-source dependency.
    """
    identity = resolve_runtime()
    if not identity.get("declaredStable") and not os.environ.get("BUDDY_DEV_SOURCE"):
        materialize(log_path=log_path)
        identity = resolve_runtime()
    if identity.get("state") == "READY" and identity.get("declaredStable"):
        return {
            "python": identity["python"],
            "pythonPath": None,
            "runtime": identity,
            "identity": f"runtime:{Path(identity['runtimeDir']).name}",
            "stable": True,
            "installed": False,
        }
    return {
        "python": sys.executable,
        "pythonPath": str(project_root() / "python"),
        "runtime": identity,
        "identity": identity["identity"],
        "stable": False,
        "installed": identity.get("state") == "READY",
    }


def runtime_identity() -> str:
    try:
        return resolve_runtime()["identity"]
    except BoardError:
        return "runtime:unavailable"


def source_leaks(runtime: dict | None = None) -> list[str]:
    """Paths inside a runtime that still point at disposable plugin source.

    An editable install, a copied interpreter symlink or a bridge that resolves
    back into the checkout would make the stable runtime depend on the very cache
    it is supposed to survive.
    """
    runtime = runtime or resolve_runtime()
    leaks: list[str] = []
    environment = Path(runtime.get("environment", ""))
    if runtime.get("state") == "SOURCE":
        return leaks
    root = Path(runtime.get("runtimeDir") or runtime.get("source") or "").resolve()
    for pth in sorted(environment.glob("lib/python*/site-packages/*.pth")):
        try:
            text = pth.read_text(errors="replace")
        except OSError:
            continue
        for line in text.splitlines():
            candidate = line.strip()
            if not candidate or candidate.startswith("import "):
                continue
            try:
                resolved = Path(candidate).resolve()
            except OSError:
                continue
            if "/plugins/cache/" in candidate:
                leaks.append(f"{pth}: {candidate}")
                continue
            # An editable/relative reference is fine only when it stays inside this
            # runtime; anything pointing back into a checkout is a real dependency
            # on the disposable source tree.
            if root and root not in resolved.parents and resolved != root:
                leaks.append(f"{pth}: {candidate}")
    for key in ("source", "contractSource"):
        value = runtime.get(key)
        if not value:
            continue
        if "/plugins/cache/" in value:
            leaks.append(str(value))
    pyvenv = environment / "pyvenv.cfg"
    if pyvenv.is_file() and str(project_root()) in pyvenv.read_text(errors="replace"):
        leaks.append(f"{pyvenv}: references the project source tree")
    return leaks


def describe(root: Path | None = None, destination: Path | None = None) -> dict:
    root = Path(root or project_root())
    target = runtime_dir(root, destination)
    ready = is_ready(target)
    return {
        "contentId": content_id(root),
        "runtimeDir": str(target),
        "installed": ready,
        "runtime": read_ready(target) if ready else None,
        "assets": len(manifest(root)["files"]),
        "identity": resolve_runtime(),
    }


def _now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
