"""The stable, content-identified runtime the service and its workers execute from.

The Codex plugin cache may disappear on upgrade, so the default service, its
workers and the harness adapters must run from a versioned runtime outside that
cache. What a runtime contains is declared exactly once, in
``packaging/runtime-assets.json``:

* ``assets`` are current root-relative files/directories copied into the runtime;
* ``resources`` name the runtime resources each consumer resolves generically.

There is no second layout and no vendor-root anchor: a build without the declared
manifest, or without a declared resource, fails honestly instead of probing a
removed directory. Materialization is explicit:

1. copy the complete declared assets into the *final* content-addressed directory;
2. run ``uv sync --frozen`` there, with the environment inside that directory;
3. write ``READY.json`` last — the directory is unused until that marker exists.

Only declared runtime assets are materialized. Credentials, user data, tests,
node_modules and any existing virtual environment are never copied.
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
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from .errors import BoardError

RUNTIME_FORMAT = 1
READY_FILE = "READY.json"
DEFAULT_RUNTIME_ROOT = Path.home() / ".local/share/hey-my-buddy/runtime"

#: The one explicit asset manifest; nothing else describes the distributable layout.
ASSET_MANIFEST = Path("packaging") / "runtime-assets.json"
#: Names and suffixes never copied into a runtime, even inside a declared directory.
IGNORED_NAMES = ("__pycache__", ".venv", "node_modules", ".env", ".DS_Store")
IGNORED_SUFFIXES = (".pyc", ".pyo")


def project_root() -> Path:
    """The project directory that owns ``pyproject.toml`` and the asset manifest."""
    return Path(__file__).resolve().parents[2]


def manifest_path(root: Path | None = None) -> Path:
    """The single declared asset manifest for *root*."""
    return Path(root or project_root()) / ASSET_MANIFEST


def _invalid(message: str, path: Path) -> BoardError:
    return BoardError("RUNTIME_MANIFEST_INVALID", message, path=str(path))


def _declared_relative(value: Any, field: str, path: Path) -> str:
    if not isinstance(value, str) or not value.strip():
        raise _invalid(f"{field} must be a nonempty root-relative path", path)
    text = value.strip()
    if Path(text).is_absolute() or ".." in Path(text).parts or "\\" in text or text.startswith("~"):
        raise _invalid(f"{field} must stay inside the project: {text!r}", path)
    return text


def _covered(relative: str, declared: list[tuple[str, str]]) -> bool:
    for asset, kind in declared:
        if relative == asset:
            return True
        if kind == "directory" and relative.startswith(asset.rstrip("/") + "/"):
            return True
    return False


def load_manifest(root: Path | None = None) -> dict:
    """Read, validate and hash the one explicit asset manifest; never guess a layout."""
    base = Path(root or project_root())
    path = manifest_path(base)
    if not _inside(base, str(path)):
        raise _invalid("The runtime asset manifest resolves outside the project", path)
    try:
        raw = path.read_bytes()
    except OSError as error:
        raise BoardError(
            "RUNTIME_MANIFEST_MISSING",
            f"The runtime asset manifest is missing at {path}; this build has no declared runtime layout",
            path=str(path),
        ) from error
    try:
        value = json.loads(raw)
    except ValueError as error:
        raise _invalid(f"The runtime asset manifest {path} is not valid JSON", path) from error
    if not isinstance(value, dict):
        raise _invalid("The runtime asset manifest must be a JSON object", path)
    if value.get("format") != RUNTIME_FORMAT:
        raise _invalid(f"The runtime asset manifest format must be {RUNTIME_FORMAT}", path)
    raw_assets = value.get("assets")
    if not isinstance(raw_assets, list) or not raw_assets:
        raise _invalid("The runtime asset manifest declares no assets", path)
    declared: list[tuple[str, str]] = []
    seen: set[str] = set()
    for index, entry in enumerate(raw_assets):
        if not isinstance(entry, dict):
            raise _invalid(f"assets[{index}] must be an object", path)
        kind = entry.get("kind")
        if kind not in ("file", "directory"):
            raise _invalid(f"assets[{index}].kind must be 'file' or 'directory'", path)
        relative = _declared_relative(entry.get("path"), f"assets[{index}].path", path)
        if relative in seen:
            raise _invalid(f"assets[{index}].path is declared twice: {relative}", path)
        if not _inside(base, str(base / relative)):
            raise _invalid(f"assets[{index}].path resolves outside the project: {relative}", path)
        seen.add(relative)
        declared.append((relative, kind))
    if (ASSET_MANIFEST.as_posix(), "file") not in declared:
        raise _invalid("The runtime asset manifest must declare itself as a file asset", path)
    resources = value.get("resources")
    if not isinstance(resources, dict) or not resources:
        raise _invalid("The runtime asset manifest declares no resources", path)
    for name, target in resources.items():
        if not isinstance(name, str) or not name.strip():
            raise _invalid("Every declared resource needs a nonempty name", path)
        relative = _declared_relative(target, f"resources[{name!r}]", path)
        if not _covered(relative, declared):
            raise _invalid(f"resources[{name!r}] is not covered by a declared asset: {relative}", path)
        if not _inside(base, str(base / relative)):
            raise _invalid(f"resources[{name!r}] resolves outside the project: {relative}", path)
    return {**value, "path": str(path), "sha256": hashlib.sha256(raw).hexdigest()}


def declared_resources(root: Path | None = None) -> dict[str, str]:
    """Resource name -> current root-relative path, exactly as declared."""
    return {name: str(relative) for name, relative in load_manifest(root)["resources"].items()}


def resource_path(name: str, root: Path | None = None) -> Path:
    """The declared path of one named resource in *root*.

    A name that is not declared is refused: there is no directory probing and no
    fallback to a removed layout or a vendor-root anchor.
    """
    base = Path(root or project_root())
    relative = declared_resources(base).get(name)
    if relative is None:
        raise BoardError(
            "RUNTIME_RESOURCE_UNDECLARED",
            f"Resource {name!r} is not declared in {manifest_path(base)}",
            resource=name,
            manifest=str(manifest_path(base)),
        )
    return base / relative


def resource_paths(root: Path | None = None) -> dict[str, Path]:
    """Every declared resource, resolved under the single declared root."""
    base = Path(root or project_root())
    return {name: base / relative for name, relative in declared_resources(base).items()}


def missing_resources(root: Path | None = None) -> list[str]:
    """Declared resources that do not exist in *root*, by name."""
    return sorted(name for name, path in resource_paths(root).items() if not path.exists())


def _ignored(relative: Path) -> bool:
    if any(part in IGNORED_NAMES for part in relative.parts):
        return True
    return relative.suffix in IGNORED_SUFFIXES


def iter_assets(root: Path | None = None) -> Iterator[tuple[str, Path]]:
    """Every declared asset file under *root*, relative to it.

    Only declared files and directories are walked; a declared file that is absent
    fails honestly instead of being silently skipped.
    """
    base = Path(root or project_root())
    for entry in load_manifest(base)["assets"]:
        relative, kind = entry["path"], entry["kind"]
        path = base / relative
        if kind == "file":
            if not path.is_file():
                raise BoardError(
                    "RUNTIME_ASSET_MISSING",
                    f"Declared runtime asset is missing: {relative}",
                    path=str(path),
                )
            yield relative, path
            continue
        if not path.is_dir():
            raise BoardError(
                "RUNTIME_ASSET_MISSING",
                f"Declared runtime asset directory is missing: {relative}",
                path=str(path),
            )
        for candidate in sorted(path.rglob("*")):
            if _ignored(candidate.relative_to(path)):
                continue
            if not _inside(base, str(candidate)):
                raise BoardError(
                    "RUNTIME_ASSET_OUTSIDE_ROOT",
                    "Declared runtime asset resolves outside the project",
                    path=str(candidate),
                    root=str(base),
                )
            if candidate.is_file():
                yield candidate.relative_to(base).as_posix(), candidate


def content_id(root: Path | None = None) -> str:
    """Deterministic identity of the runtime assets, including the manifest itself."""
    base = Path(root or project_root())
    declared = load_manifest(base)
    digest = hashlib.sha256()
    digest.update(f"buddy-runtime-format:{RUNTIME_FORMAT}\n".encode())
    digest.update(f"assets-manifest:{ASSET_MANIFEST.as_posix()}\n".encode())
    digest.update(declared["sha256"].encode())
    digest.update(b"\n")
    for relative, path in sorted(iter_assets(base)):
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(hashlib.sha256(path.read_bytes()).hexdigest().encode())
        digest.update(b"\n")
    return digest.hexdigest()[:32]


def manifest(root: Path | None = None) -> dict:
    """The build record for *root*: declared files, resources and the hashed manifest."""
    base = Path(root or project_root())
    declared = load_manifest(base)
    files = {relative: hashlib.sha256(path.read_bytes()).hexdigest() for relative, path in sorted(iter_assets(base))}
    return {
        "format": RUNTIME_FORMAT,
        "manifest": ASSET_MANIFEST.as_posix(),
        "manifestSha256": declared["sha256"],
        "resources": dict(declared["resources"]),
        "files": files,
        "contentId": content_id(base),
    }


def runtime_root() -> Path:
    return Path(os.environ.get("BUDDY_RUNTIME_ROOT") or DEFAULT_RUNTIME_ROOT).expanduser()


def runtime_dir(root: Path | None = None, destination: Path | None = None) -> Path:
    base = Path(destination) if destination else runtime_root()
    return base / content_id(root)


def is_ready(directory: Path) -> bool:
    """True only for a complete runtime whose assets and resources stay inside it."""
    directory = Path(directory)
    try:
        value = json.loads((directory / READY_FILE).read_text())
    except (OSError, ValueError):
        return False
    if not isinstance(value, dict) or value.get("format") != RUNTIME_FORMAT:
        return False
    if value.get("state") != "READY" or value.get("contentId") != directory.name:
        return False
    interpreter = value.get("python", "")
    if not _inside(directory, interpreter, allow_lexical=True) or not Path(interpreter).is_file():
        return False
    try:
        # Validate declared directory contents too: an outward symlink must not
        # become a stable asset merely because its containing directory is local.
        for _relative, _path in iter_assets(directory):
            pass
        return not missing_resources(directory)
    except BoardError:
        return False


def materialize(
    root: Path | None = None,
    destination: Path | None = None,
    *,
    uv_bin: str | None = None,
    timeout_seconds: int = 900,
    log_path: Path | None = None,
) -> dict:
    """Install the stable runtime, or return the existing READY one unchanged.

    The complete declared assets are placed in the *final* content-addressed
    directory before ``uv sync --frozen`` runs there, so the environment, its
    ``pyvenv.cfg``, its ``.pth`` files and every script path refer to that final
    location. ``READY.json`` is written last and is the only thing that publishes it;
    an install lock makes concurrent installers cooperate instead of racing.
    """
    root = Path(root or project_root())
    load_manifest(root)  # refuse to install a runtime whose layout was never declared
    target = runtime_dir(root, destination)
    if is_ready(target):
        return {"runtime": read_ready(target), "installed": False, "runtimeDir": str(target)}
    target.parent.mkdir(parents=True, exist_ok=True)
    # Install into a temporary sibling first only because the final name must be a
    # complete directory atomically; every path recorded inside is rewritten to the
    # final location before installation starts.
    working: Path | None = None
    lock_path = target.parent / f".{target.name}.install.lock"
    lock_fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        if is_ready(target):
            return {"runtime": read_ready(target), "installed": False, "runtimeDir": str(target)}
        working = Path(tempfile.mkdtemp(prefix=f".{target.name}.build-", dir=target.parent))
        _copy_assets(root, working)
        absent = missing_resources(working)
        if absent:
            raise BoardError(
                "RUNTIME_ASSET_MISSING",
                "The declared runtime resources were not copied: " + ", ".join(absent),
                resources=absent,
            )
        record = manifest(root)
        (working / "manifest.json").write_text(json.dumps(record, indent=2, sort_keys=True) + "\n")
        # Publish the complete source tree under the FINAL content-addressed name
        # before any environment is created inside it.
        if target.exists():
            shutil.rmtree(target, ignore_errors=True)
        working.rename(target)
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
            "projectRoot": str(target),
            "environment": str(environment),
            "python": str(interpreter),
            "runtimeRoot": str(target.parent),
            "installedAt": _now(),
            "sourceCommit": _source_commit(root),
            "manifest": ASSET_MANIFEST.as_posix(),
            "manifestSha256": record["manifestSha256"],
            "resources": {name: str(path) for name, path in resource_paths(target).items()},
        }
        _rewrite_environment_paths(environment, target)
        (target / READY_FILE).write_text(json.dumps(ready, indent=2, sort_keys=True) + "\n")
        return {"runtime": read_ready(target), "installed": True, "runtimeDir": str(target)}
    finally:
        if working is not None and working.exists():
            shutil.rmtree(working, ignore_errors=True)
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        os.close(lock_fd)
        # Keep the lock inode. Unlinking it could let a later installer lock a
        # new inode while an existing waiter still holds this one open.


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
    for relative, path in iter_assets(root):
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
    """Where the *currently executing* code, interpreter and resources really live."""
    import buddy as package

    package_directory = Path(package.__file__).resolve().parent
    resources = resource_paths()
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
        # The resources this process actually resolves, by declared name. There are no
        # DSH-only globals here: any harness resource is declared in the manifest.
        "resources": {name: str(path) for name, path in resources.items()},
        "missingResources": sorted(name for name, path in resources.items() if not path.exists()),
    }


def _inside(directory: Path | None, candidate: str | None, *, allow_lexical: bool = False) -> bool:
    """True when *candidate* lives inside *directory*.

    Assets, resources and package paths must resolve inside the root. Only a
    venv's interpreter may use its lexical path while linking to a shared base
    interpreter. Resolving both sides also handles macOS ``/var`` aliases.
    """
    if not directory or not candidate:
        return False
    root_text = os.path.abspath(str(directory))
    candidate_text = os.path.abspath(str(candidate))
    if allow_lexical and (
        candidate_text == root_text or candidate_text.startswith(root_text.rstrip("/") + "/")
    ):
        return True
    try:
        resolved = Path(candidate).resolve()
        root = Path(directory).resolve()
    except (OSError, RuntimeError):
        return False
    return resolved == root or root in resolved.parents


def resolve_runtime() -> dict:
    """The runtime identity of *this process*, not merely of a directory on disk.

    ``BUDDY_RUNTIME`` pins an explicit runtime directory; otherwise the READY
    runtime matching the current assets is used. ``stable`` is only true when the
    process actually imported ``buddy`` from inside that runtime and every declared
    resource resolves inside it too: finding a ``READY.json`` proves nothing by
    itself.
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
        recorded = {name: str(value) for name, value in (record.get("resources") or {}).items()}
        declared_missing = sorted(name for name, value in recorded.items() if not Path(value).exists())
        running_from_runtime = (
            _inside(directory, actual["package"])
            and _inside(directory, actual["prefix"])
            and _inside(directory, actual["executable"], allow_lexical=True)
            and all(_inside(directory, value) for value in actual["resources"].values())
        )
        leaks = source_leaks({**record, "runtimeDir": str(directory)})
        return {
            **record,
            "declaredStable": True,
            "inUse": running_from_runtime,
            "stable": running_from_runtime and not leaks and not declared_missing and not actual["missingResources"],
            "actual": actual,
            "leaks": leaks,
            "resourcesMissing": sorted(set(declared_missing) | set(actual["missingResources"])),
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
        "projectRoot": str(project_root()),
        "manifest": ASSET_MANIFEST.as_posix(),
        "resources": {name: str(path) for name, path in resource_paths().items()},
        "declaredStable": False,
        "inUse": False,
        "stable": False,
        "actual": actual,
        "leaks": [],
        "resourcesMissing": actual["missingResources"],
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
        "pythonPath": str(project_root() / "src"),
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

    An editable install, a copied interpreter symlink or a resource that resolves
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
    for value in (runtime.get("source"), runtime.get("projectRoot"), runtime.get("runtimeDir"), runtime.get("python")):
        if isinstance(value, str) and "/plugins/cache/" in value:
            leaks.append(value)
    for name, value in (runtime.get("resources") or {}).items():
        if not isinstance(value, str):
            continue
        if "/plugins/cache/" in value:
            leaks.append(f"{name}: {value}")
        elif root and not _inside(root, value):
            leaks.append(f"{name} resolves outside the runtime: {value}")
    pyvenv = environment / "pyvenv.cfg"
    if pyvenv.is_file() and str(project_root()) in pyvenv.read_text(errors="replace"):
        leaks.append(f"{pyvenv}: references the project source tree")
    return leaks


def describe(root: Path | None = None, destination: Path | None = None) -> dict:
    base = Path(root or project_root())
    target = runtime_dir(base, destination)
    ready = is_ready(target)
    return {
        "contentId": content_id(base),
        "runtimeDir": str(target),
        "installed": ready,
        "runtime": read_ready(target) if ready else None,
        "assets": len(manifest(base)["files"]),
        "resources": {name: str(path) for name, path in resource_paths(base).items()},
        "identity": resolve_runtime(),
    }


def _now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _source_commit(root: Path) -> str | None:
    metadata = root / "src/buddy/build-info.json"
    if metadata.exists():
        return json.loads(metadata.read_text()).get("sourceCommit")
    try:
        return subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"], text=True, stderr=subprocess.DEVNULL).strip()
    except (OSError, subprocess.CalledProcessError):
        return None
