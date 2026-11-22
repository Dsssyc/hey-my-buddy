"""Static harness entries and read-only local location scans."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import plistlib
import re
import sys
from typing import Literal
from xml.parsers.expat import ExpatError

from .paths import is_absolute_path
from .protocol.harness_names import HARNESS_NAMES


@dataclass(frozen=True)
class AppEntry:
    kind: Literal["bundle", "uninstall", "msix"]
    identity: str
    cli_path: str


@dataclass(frozen=True)
class DiscoverySpec:
    command: str
    apps: dict[str, AppEntry | None]


DISCOVERY_SPECS: dict[str, DiscoverySpec] = {
    "claude": DiscoverySpec(
        command="claude",
        apps={
            "darwin": None,
            "win32": None,
            "linux": None,
        },
    ),
    "codex": DiscoverySpec(
        command="codex",
        apps={
            "darwin": AppEntry("bundle", "com.openai.codex", "Contents/Resources/codex-cli/bin/codex"),
            "win32": AppEntry("msix", "OpenAI.Codex_2p2nqsd0c76g0", "app/resources/codex.exe"),
            "linux": None,
        },
    ),
    "dsh": DiscoverySpec(
        command="dsh",
        apps={
            "darwin": AppEntry("bundle", "com.deepseek.dsh", "Contents/Resources/runtime/cli/bin/dsh"),
            "win32": AppEntry("uninstall", "1bf39983-50d0-5fe0-9ef4-cece76f67c5e", "resources/runtime/cli/bin/dsh.cmd"),
            "linux": None,
        },
    ),
    "zcode": DiscoverySpec(
        command="zcode",
        apps={
            "darwin": AppEntry("bundle", "dev.zcode.app", "Contents/Resources/glm/zcode.cjs"),
            "win32": None,
            "linux": None,
        },
    ),
}


def discovery_spec(harness: str) -> DiscoverySpec:
    return DISCOVERY_SPECS[harness]


_VERSION_RECORD = re.compile(r"v?\d+(?:\.\d+){0,2}\Z")


def _home() -> Path:
    if os.name == "nt":
        return Path.home()
    import pwd

    return Path(pwd.getpwuid(os.getuid()).pw_dir)


def file_fingerprint(path: str | os.PathLike[str], *, record: bool = False) -> dict | None:
    """Return a cheap executable identity or a content-backed manager record identity."""
    try:
        target = Path(path)
        if record:
            return _read_record(target)[1]
        if not target.is_file():
            return None
        stat = target.stat()
        fingerprint = {"realPath": str(target.resolve()), "size": stat.st_size,
                       "mtimeNs": stat.st_mtime_ns}
        return fingerprint
    except (OSError, ValueError, RuntimeError, TypeError):
        return None


def _read_record(path: Path) -> tuple[str | None, dict | None]:
    """Read a record once for both version selection and its content fingerprint."""
    fingerprint = file_fingerprint(path)
    if fingerprint is None:
        return None, None
    try:
        content = path.read_bytes()
    except OSError:
        return None, None
    fingerprint["sha256"] = hashlib.sha256(content).hexdigest()
    try:
        text = content.decode("utf-8")
    except UnicodeError:
        text = None
    return text, fingerprint


def _version_parts(value: str) -> tuple[int, ...] | None:
    if not _VERSION_RECORD.fullmatch(value):
        return None
    return tuple(int(part) for part in value.removeprefix("v").split("."))


def _installed_version_dirs(root: Path, version: str) -> list[Path]:
    """Resolve a numeric default prefix against installed versions, newest first."""
    wanted = () if version == "node" else _version_parts(version)
    if wanted is None:
        return []
    try:
        matches = [(parts, child) for child in root.iterdir() if child.is_dir()
                   if (parts := _version_parts(child.name)) is not None
                   and parts[:len(wanted)] == wanted]
    except OSError:
        return []
    return [child for _, child in sorted(matches, key=lambda item: item[0], reverse=True)]


def _manager_records(home: Path) -> tuple[list[dict], list[tuple[str, Path]]]:
    """Read only narrowly named default-version records; return no record content."""
    specs = [
        ("nvm", home / ".nvm/alias/default", home / ".nvm/versions/node"),
        ("fnm", home / ".fnm/aliases/default", home / ".fnm/node-versions"),
        ("fnm", home / ".local/share/fnm/aliases/default", home / ".local/share/fnm/node-versions"),
        ("asdf", home / ".tool-versions", home / ".asdf/installs/nodejs"),
        ("mise", home / ".config/mise/config.toml", home / ".local/share/mise/installs/node"),
        ("mise", home / ".mise.toml", home / ".local/share/mise/installs/node"),
        ("volta", home / ".volta/tools/user/platform.json", home / ".volta/tools/image/node"),
    ]
    if sys.platform == "darwin":
        root = home / "Library/Application Support/fnm"
        specs.append(("fnm", root / "aliases/default", root / "node-versions"))
    elif os.name == "nt":
        if os.environ.get("APPDATA"):
            root = Path(os.environ["APPDATA"]) / "fnm"
            specs.append(("fnm", root / "aliases/default", root / "node-versions"))
    records: list[dict] = []
    roots: list[tuple[str, Path]] = []
    for manager, record, root in specs:
        data, fingerprint = _read_record(record)
        if fingerprint is None and record.is_symlink():
            try:
                stat = record.lstat()
                fingerprint = {"realPath": str(record.resolve()), "size": stat.st_size,
                               "mtimeNs": stat.st_mtime_ns}
            except (OSError, RuntimeError):
                pass
        records.append({"manager": manager, "path": str(record), "fingerprint": fingerprint})
        if fingerprint is None:
            continue
        if manager == "fnm" and data is None and record.is_symlink():
            try:
                target = record.resolve()
                data = target.name if re.fullmatch(r"v?\d+(?:\.\d+){0,2}", target.name) else target.parent.name
            except (OSError, RuntimeError):
                pass
        version = None
        if data is None:
            continue
        if manager in ("nvm", "fnm"):
            version = data.strip().splitlines()[0] if data.strip() else None
            if manager == "nvm":
                for _ in range(3):
                    if not version or re.fullmatch(r"v?\d+(?:\.\d+){0,2}", version):
                        break
                    alias = home / ".nvm/alias" / version
                    if not alias.is_file():
                        break
                    text, alias_fingerprint = _read_record(alias)
                    records.append({"manager": "nvm", "path": str(alias),
                                    "fingerprint": alias_fingerprint})
                    value = (text or "").strip().splitlines()
                    version = value[0] if value else None
        elif manager == "asdf":
            version = next((part[1] for line in data.splitlines()
                            if (part := line.split()) and part[0] == "nodejs" and len(part) > 1), None)
        elif manager == "mise":
            try:
                import tomllib

                tools = tomllib.loads(data).get("tools", {})
                version = tools.get("node") if isinstance(tools, dict) else None
            except (ValueError, TypeError):
                pass
        else:
            try:
                version = json.loads(data).get("node", {}).get("version")
            except (ValueError, AttributeError):
                pass
        if not isinstance(version, str):
            continue
        for installed in _installed_version_dirs(root, version):
            base = installed / "installation" if manager == "fnm" else installed
            for directory in (base / "bin", base):
                if directory.is_dir():
                    roots.append((manager, directory))
        if manager == "volta" and _version_parts(version) is not None:
            directory = home / ".volta/bin"
            if directory.is_dir():
                roots.append((manager, directory))
    return records, roots


def _registry_paths() -> list[str]:
    if os.name != "nt":
        return []
    try:
        import winreg

        paths = []
        for hive, key in ((winreg.HKEY_CURRENT_USER, r"Environment"),
                          (winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment")):
            try:
                with winreg.OpenKey(hive, key) as handle:
                    value, kind = winreg.QueryValueEx(handle, "Path")
                    if kind == winreg.REG_EXPAND_SZ:
                        value = winreg.ExpandEnvironmentStrings(value)
                    paths.extend(value.split(os.pathsep))
            except OSError:
                continue
        return paths
    except ImportError:
        return []


def _common_dirs(home: Path, environment: dict | None = None) -> list[Path]:
    if os.name == "nt":
        env = os.environ if environment is None else environment
        return [Path(env[key]) / suffix for key, suffix in (
            ("APPDATA", "npm"), ("LOCALAPPDATA", "Programs/nodejs"), ("ProgramFiles", "nodejs"))
            if env.get(key)] + [home / ".local/bin"]
    return [Path("/opt/homebrew/bin"), Path("/usr/local/bin"), Path("/usr/bin"), home / ".local/bin",
            home / ".npm-global/bin", home / ".volta/bin"]


def _mac_app_paths(entry: AppEntry, roots: tuple[Path, ...]) -> list[Path]:
    paths = []
    for root in roots:
        try:
            bundles = sorted(root.glob("*.app"))
        except OSError:
            continue
        for bundle in bundles:
            try:
                with (bundle / "Contents/Info.plist").open("rb") as stream:
                    info = plistlib.load(stream)
            except (OSError, ValueError, plistlib.InvalidFileException, ExpatError):
                continue
            if isinstance(info, dict) and info.get("CFBundleIdentifier") == entry.identity:
                paths.append(bundle / entry.cli_path)
    return paths


def _windows_app_paths(entry: AppEntry, winreg) -> list[Path]:
    paths = []
    if entry.kind == "uninstall":
        key = rf"Software\Microsoft\Windows\CurrentVersion\Uninstall\{entry.identity}"
        for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
            for view in (winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY):
                try:
                    with winreg.OpenKey(hive, key, 0, winreg.KEY_READ | view) as handle:
                        location, _ = winreg.QueryValueEx(handle, "InstallLocation")
                except OSError:
                    continue
                if isinstance(location, str) and location:
                    paths.append(Path(location) / entry.cli_path)
    else:
        key = (r"Software\Classes\Local Settings\Software\Microsoft\Windows"
               r"\CurrentVersion\AppModel\Repository\Packages")
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key) as packages:
                names = [winreg.EnumKey(packages, index)
                         for index in range(winreg.QueryInfoKey(packages)[0])]
        except OSError:
            return []
        for name in sorted(names):
            parts = name.rsplit("_", 4)
            if len(parts) != 5 or f"{parts[0]}_{parts[4]}" != entry.identity:
                continue
            try:
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key + "\\" + name) as handle:
                    location, _ = winreg.QueryValueEx(handle, "PackageRootFolder")
            except OSError:
                continue
            if isinstance(location, str) and location:
                paths.append(Path(location) / entry.cli_path)
    return paths


def _app_paths(harness: str) -> list[Path]:
    if harness == "node":
        return []
    entry = discovery_spec(harness).apps[sys.platform]
    if entry is None:
        return []
    if sys.platform == "darwin":
        return _mac_app_paths(entry, (Path("/Applications"), _home() / "Applications"))
    if sys.platform == "win32":
        import winreg

        return _windows_app_paths(entry, winreg)
    return []


def _variants(directory: Path, name: str) -> list[Path]:
    if os.name == "nt":
        return [directory / (name + suffix) for suffix in (".exe", ".cmd")]
    return [directory / name]


def has_candidate(directory: Path) -> bool:
    """Whether this PATH directory currently holds a harness or Node candidate."""
    names = [discovery_spec(harness).command for harness in HARNESS_NAMES] + ["node"]
    return any(file_fingerprint(path) is not None for name in names
               for path in _variants(directory, name))


def candidate_snapshot(harness: str, *, manual_path: str | None = None,
                       environment: dict | None = None) -> dict:
    """Full candidate diagnostics and file identities, with PATH as a clue."""
    if harness not in (*HARNESS_NAMES, "node"):
        raise ValueError("unknown harness")
    home = _home()
    records, manager_dirs = _manager_records(home)
    name = "node" if harness == "node" else discovery_spec(harness).command
    places: list[tuple[str, Path]] = []
    if manual_path:
        places.append(("manual", Path(manual_path).expanduser()))
    for source, directory in manager_dirs:
        places.extend((source, path) for path in _variants(directory, name))
    places.extend(("common", path) for directory in _common_dirs(home, environment) for path in _variants(directory, name))
    places.extend(("registry", path) for directory in _registry_paths() for path in _variants(Path(directory), name))
    caller_path = (os.environ if environment is None else environment).get("PATH", "")
    caller_path = caller_path if isinstance(caller_path, str) else ""
    places.extend(("path", path) for directory in caller_path.split(os.pathsep) if directory and is_absolute_path(directory)
                  for path in _variants(Path(directory), name))
    places.extend(("app", path) for path in _app_paths(harness))
    candidates = []
    seen = set()
    for source, path in places:
        label = str(path)
        if label in seen:
            continue
        seen.add(label)
        fingerprint = file_fingerprint(path)
        candidates.append({"path": label, "source": source, "exists": fingerprint is not None,
                           "fingerprint": fingerprint})
    snapshot = {"harness": harness, "managerRecords": records, "candidates": candidates}
    if harness != "node":
        node_paths = [Path(item["path"]) for item in candidate_snapshot("node", environment=environment)["candidates"]]
        node_paths = [path for item in candidates if item["exists"] for path in _variants(
            Path(item["path"]).parent, "node")] + node_paths
        seen_nodes = set()
        snapshot["nodeCandidates"] = []
        for path in node_paths:
            if str(path) in seen_nodes:
                continue
            seen_nodes.add(str(path))
            snapshot["nodeCandidates"].append({"path": str(path), "fingerprint": file_fingerprint(path)})
    return snapshot


def _scan_hash(snapshot: dict) -> str:
    data = json.dumps(snapshot, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def location_fingerprint(snapshot: dict) -> str:
    """Hash what discovery can find, retaining the order of present candidates."""
    return _scan_hash({"harness": snapshot["harness"],
                       "managerRecords": snapshot["managerRecords"],
                       "candidates": [item for item in snapshot["candidates"] if item["exists"]],
                       "nodeCandidates": [item for item in snapshot.get("nodeCandidates", [])
                                          if item["fingerprint"] is not None]})
