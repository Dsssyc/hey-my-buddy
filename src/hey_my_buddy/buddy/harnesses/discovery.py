"""Read-only local harness discovery and bounded, model-free health checks.

The service owns caching and persistence. This module returns JSON values only and
never consults shell startup files or inherited provider credentials.
"""
from __future__ import annotations

from dataclasses import dataclass
from importlib import import_module
import hashlib
import json
import logging
import os
import plistlib
from pathlib import Path
import re
import signal
import shutil
import subprocess
import sys
from ... import private_dirs
import time
import selectors
from xml.parsers.expat import ExpatError

from ...paths import is_absolute_path
from ...protocol.harness_names import HARNESS_NAMES
from .discovery_spec import AppEntry, DiscoverySpec
from ...protocol.reasons import reason as make_reason


_MAX_CANDIDATES = 96
_MAX_DIAGNOSTICS = 12
_MAX_OUTPUT = 16_384
_TIMEOUT = 5.0
_SCAN_TIMEOUT = 20.0
_VERSION = re.compile(r"(?<![\w])v?(\d+)\.(\d+)(?:\.(\d+))?(?:[-+][\w.-]+)?")
_VERSION_RECORD = re.compile(r"v?\d+(?:\.\d+){0,2}\Z")


def _home() -> Path:
    if os.name == "nt":
        return Path.home()
    import pwd

    return Path(pwd.getpwuid(os.getuid()).pw_dir)


def file_fingerprint(path: str | os.PathLike[str]) -> dict | None:
    """Return the identity used to invalidate a cached executable or record."""
    try:
        target = Path(path)
        if not target.is_file():
            return None
        stat = target.stat()
        return {"realPath": str(target.resolve()), "size": stat.st_size, "mtimeNs": stat.st_mtime_ns}
    except (OSError, ValueError, RuntimeError, TypeError):
        return None


def _record_text(path: Path) -> str | None:
    try:
        if path.stat().st_size > 4096:
            return None
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return None


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
        fingerprint = file_fingerprint(record)
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
        data = _record_text(record)
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
                    alias_root = home / ".nvm/alias"
                    try:
                        allowed = alias.resolve().is_relative_to(alias_root.resolve()) and alias.is_file()
                    except (OSError, RuntimeError):
                        allowed = False
                    if not allowed:
                        break
                    records.append({"manager": "nvm", "path": str(alias),
                                    "fingerprint": file_fingerprint(alias)})
                    value = (_record_text(alias) or "").strip().splitlines()
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


def _discovery_spec(harness: str) -> DiscoverySpec:
    return import_module(f".{harness}", __package__).DISCOVERY


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
    entry = _discovery_spec(harness).apps[sys.platform]
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
    names = [_discovery_spec(harness).command for harness in HARNESS_NAMES] + ["node"]
    return any(file_fingerprint(path) is not None for name in names
               for path in _variants(directory, name))


def candidate_snapshot(harness: str, *, manual_path: str | None = None,
                       environment: dict | None = None) -> dict:
    """Full candidate diagnostics and file identities, with PATH as a clue."""
    if harness not in (*HARNESS_NAMES, "node"):
        raise ValueError("unknown harness")
    home = _home()
    records, manager_dirs = _manager_records(home)
    name = "node" if harness == "node" else _discovery_spec(harness).command
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


def native_environment(environment: dict, *, command: tuple[str, ...] | list[str] = (),
                       entry: str | Path | None = None) -> dict:
    """Build an account-preserving, credential-free environment for native CLIs.

    CA certificate path variables are preserved alongside the proxy variables:
    they name trust roots, never credentials.
    """
    # The login name is identity, not a credential: Claude Code on macOS looks up
    # its keychain login by USER and reports "not logged in" without it.
    allowed = ("HOME", "USER", "LOGNAME", "USERNAME", "USERPROFILE", "HOMEDRIVE", "HOMEPATH", "APPDATA", "LOCALAPPDATA",
               "XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME", "XDG_STATE_HOME",
               "XDG_RUNTIME_DIR", "CODEX_HOME", "CLAUDE_CONFIG_DIR", "ZCODE_DATA_BASE_DIR", "DSH_HOME",
               "LANG", "LANGUAGE", "LC_ALL", "LC_CTYPE", "LC_MESSAGES", "LC_COLLATE",
               "LC_NUMERIC", "LC_TIME", "LC_MONETARY", "LC_PAPER", "LC_NAME",
               "LC_ADDRESS", "LC_TELEPHONE", "LC_MEASUREMENT", "LC_IDENTIFICATION",
               "TMPDIR", "TMP", "TEMP", "SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT",
               "TERM", "COLORTERM", "PATH", "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY",
               "NO_PROXY", "http_proxy", "https_proxy", "all_proxy", "no_proxy",
               "SSL_CERT_FILE", "SSL_CERT_DIR", "REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE",
               "NODE_EXTRA_CA_CERTS")
    result = {key: environment[key] for key in allowed if key in environment}
    prefixes = []
    if command:
        path = Path(command[0])
        if is_absolute_path(path) and file_fingerprint(path):
            prefixes.append(str(path.parent))
    inherited_path = result.get("PATH", "")
    result["PATH"] = os.pathsep.join([*prefixes, *([inherited_path] if inherited_path else [])])
    result.update(shim_environment(entry))
    return result


def _terminate_group(process: subprocess.Popen) -> bool:
    """Stop and reap a POSIX probe group, including children holding its pipe."""
    started = time.monotonic()
    stop_at = started + 1.0
    while True:
        now = time.monotonic()
        sig = signal.SIGTERM if now - started < 0.25 else signal.SIGKILL
        try:
            os.killpg(process.pid, sig)
        except OSError:
            # A signal failure is not stop evidence. Observe the group below;
            # a group with only unreaped members may reject signals until reaped.
            pass
        process.poll()
        try:
            os.killpg(process.pid, 0)
        except ProcessLookupError:
            return process.returncode is not None
        except OSError:
            # Unavailable observation stays unknown throughout the bounded wait.
            pass
        remaining = stop_at - time.monotonic()
        if remaining <= 0:
            return False
        time.sleep(min(0.01, remaining))


def _probe_posix(process: subprocess.Popen, stop_at: float,
                 deadline: float | None) -> tuple[str | None, bytes, bool]:
    """Keep the read outcome separate from the final group/pipe stop evidence."""
    assert process.stdout is not None
    chunks: list[bytes] = []
    count = 0
    reason = None
    eof = False
    selector = selectors.DefaultSelector()
    try:
        selector.register(process.stdout, selectors.EVENT_READ)
        while not eof:
            remaining = stop_at - time.monotonic()
            if remaining <= 0:
                reason = "scan-timeout" if deadline is not None and deadline <= stop_at else "timeout"
                break
            try:
                if not selector.select(remaining):
                    continue
                chunk = os.read(process.stdout.fileno(), min(4096, _MAX_OUTPUT - count + 1))
            except OSError:
                reason = "pipe-failed"
                break
            if not chunk:
                eof = True
                break
            count += len(chunk)
            if count > _MAX_OUTPUT:
                reason = "output-limit"
                break
            chunks.append(chunk)
        if reason is not None:
            _terminate_group(process)
            # A detached descendant still holding the pipe means containment is
            # unproven. Never report the original timeout as a clean shutdown.
            drain_until = time.monotonic() + 0.5
            while not eof and time.monotonic() < drain_until:
                try:
                    if not selector.select(max(0, drain_until - time.monotonic())):
                        break
                    eof = not os.read(process.stdout.fileno(), 4096)
                except OSError:
                    break
            contained = _terminate_group(process)
            return reason, b"", contained and eof
        try:
            process.wait(timeout=max(0.01, stop_at - time.monotonic()))
        except subprocess.TimeoutExpired:
            return "timeout", b"", _terminate_group(process)
        contained = _terminate_group(process)
        return ("probe-failed" if process.returncode else None), b"".join(chunks), contained
    except Exception as error:
        _terminate_group(process)
        drain_until = time.monotonic() + 0.5
        while not eof and time.monotonic() < drain_until:
            try:
                if not selector.select(max(0, drain_until - time.monotonic())):
                    break
                eof = not os.read(process.stdout.fileno(), 4096)
            except OSError:
                break
        contained = _terminate_group(process)
        error.discovery_shutdown_confirmed = contained and eof
        raise
    finally:
        selector.close()
        process.stdout.close()


def _probe_windows(process: subprocess.Popen, stop_at: float) -> tuple[str | None, bytes, bool]:
    """Poll a Windows pipe without a reader thread; forced tree stop is unverified."""
    import ctypes
    from ctypes import wintypes
    import msvcrt

    assert process.stdout is not None
    chunks: list[bytes] = []
    pipe = msvcrt.get_osfhandle(process.stdout.fileno())
    peek = ctypes.WinDLL("kernel32", use_last_error=True).PeekNamedPipe
    peek.argtypes = [wintypes.HANDLE, wintypes.LPVOID, wintypes.DWORD,
                     ctypes.POINTER(wintypes.DWORD), ctypes.POINTER(wintypes.DWORD),
                     ctypes.POINTER(wintypes.DWORD)]
    peek.restype = wintypes.BOOL
    available = wintypes.DWORD()
    reason = None
    while time.monotonic() < stop_at:
        readable = peek(pipe, None, 0, None, ctypes.byref(available), None)
        if not readable:
            # The child can close stdout before exiting. Broken pipe is EOF,
            # not a failed read; its exit still has the remaining probe budget.
            if ctypes.get_last_error() != 109:  # ERROR_BROKEN_PIPE
                reason = "pipe-failed"
            break
        if available.value:
            chunk = os.read(process.stdout.fileno(), min(4096, _MAX_OUTPUT - sum(map(len, chunks)) + 1))
            if not chunk:
                break
            chunks.append(chunk)
            if sum(map(len, chunks)) > _MAX_OUTPUT:
                reason = "output-limit"
                break
        else:
            time.sleep(0.01)
    else:
        reason = "timeout"
    if reason is None:
        try:
            process.wait(timeout=max(0, stop_at - time.monotonic()))
        except subprocess.TimeoutExpired:
            reason = "timeout"
    forced = reason is not None
    if forced:
        try:
            subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                           stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, timeout=1, check=False)
        except (OSError, subprocess.TimeoutExpired):
            pass
        if process.poll() is None:
            process.kill()
        process.wait()
    process.stdout.close()
    return (reason, b"", False) if forced else (
        ("probe-failed" if process.returncode else None), b"".join(chunks), True)


def probe(command: list[str], environment: dict, *, deadline: float | None = None,
           cwd: str | None = None, owned: list | None = None) -> tuple[str | dict | None, bytes]:
    """Run one noninteractive probe with bounded time, output and child lifetime."""
    receipt = {"created": False, "shutdown": True, "exit_code": None}
    if owned is not None:
        owned[:] = [receipt]
    start = time.monotonic()
    if deadline is not None and start >= deadline:
        return "scan-timeout", b""
    from ...protocol.process_identity import native_process_journal_active

    journalled = native_process_journal_active()
    from ...protocol.owned_process import WindowsCommandLineTooLong
    try:
        if journalled:
            from ...protocol.owned_process import owned_popen
            process = owned_popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                  stderr=subprocess.STDOUT, env=environment, cwd=cwd,
                                  start_new_session=os.name != "nt")
        else:
            from ...protocol.owned_process import check_windows_command_line
            check_windows_command_line(command)
            process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                       stderr=subprocess.STDOUT, env=environment, cwd=cwd,
                                       start_new_session=os.name != "nt",
                                       creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0)
    except WindowsCommandLineTooLong as error:
        return make_reason("harness.command-line-too-long", operation="probe", utf16Units=error.utf16_units, limit=error.limit), b""
    except (OSError, ValueError):
        return "launch-failed", b""
    if journalled:
        from .base import ProcessHandle
        try:
            ProcessHandle(process, own_group=True, log_paths={})
        except Exception:
            # Its durable intent remains unresolved until the held probe
            # confirms shutdown. A crash before that proof stays unknown.
            pass
    receipt.update(created=True, shutdown=False)
    stop_at = min(start + _TIMEOUT, deadline) if deadline is not None else start + _TIMEOUT
    try:
        code, output, stopped = (_probe_windows(process, stop_at) if os.name == "nt" else
                                 _probe_posix(process, stop_at, deadline))
    except Exception as error:
        receipt.update(shutdown=getattr(error, "discovery_shutdown_confirmed", False) is True,
                       exit_code=process.returncode)
        if journalled and receipt["shutdown"]:
            from ...protocol.process_identity import discard_confirmed_native_spawn
            discard_confirmed_native_spawn(process._buddy_native_intent)
        raise
    receipt.update(shutdown=stopped, exit_code=process.returncode)
    if journalled and stopped:
        from ...protocol.process_identity import discard_confirmed_native_spawn
        discard_confirmed_native_spawn(process._buddy_native_intent)
    return (code, output) if stopped else ("shutdown-unverified", b"")


def _probe_dsh_version(command: list[str], environment: dict, deadline: float, *,
                       launch_environment: dict | None = None) -> tuple[str | dict | None, bytes]:
    """Use the same owned DSH launcher for the no-input version check.

    Keep the caller's HOME, DSH_HOME and native environment unchanged, and
    retain the directory if the owned group cannot be confirmed stopped.
    """
    from .dsh.acp.launch import LaunchOwnershipError, LaunchRejected, launch
    from ...protocol.owned_process import WindowsCommandLineTooLong

    start = time.monotonic()
    if start >= deadline:
        return "scan-timeout", b""
    root = private_dirs.make_temporary_dir(prefix="hey-my-buddy-dsh-probe-").resolve()
    process = handle = None
    stopped = True  # No child exists until launch returns or transfers ownership.
    code, output = "launch-failed", b""
    failure = None
    try:
        try:
            process, handle = launch([*command, "--version"], private_root=root,
                                     source_environment=environment, launch_environment=launch_environment, merge_stderr=True)
        except LaunchOwnershipError as error:
            process, handle = error.process, error.handle
        except WindowsCommandLineTooLong as error:
            return make_reason("harness.command-line-too-long", operation="probe", utf16Units=error.utf16_units, limit=error.limit), b""
        except (LaunchRejected, OSError, ValueError):
            return code, output
        else:
            process.stdin.close()
            stop_at = min(start + _TIMEOUT, deadline)
            code, output, _ = (_probe_windows(process, stop_at) if os.name == "nt" else
                               _probe_posix(process, stop_at, deadline))
    except Exception as error:
        failure = error
        raise
    finally:
        if handle is not None:
            stopped = handle.shutdown_confirmed(settle_seconds=0.2)
            if not stopped:
                handle.terminate(grace_seconds=1)
                stopped = handle.shutdown_confirmed(settle_seconds=0.2)
            for stream in (process.stdin, process.stdout, process.stderr):
                if stream is not None:
                    stream.close()
        if failure is not None:
            failure.discovery_shutdown_confirmed = stopped
        if stopped:
            shutil.rmtree(root)
        else:
            logging.getLogger(__name__).warning("DSH probe stop unconfirmed; private directory retained: %s", root)
    return (code, output) if stopped else ("shutdown-unverified", b"")


def _version_key(version: str | None) -> tuple[int, int, int]:
    found = _VERSION.search(version or "")
    return tuple(int(value or 0) for value in found.groups()) if found else (-1, -1, -1)


def _priority(source: str) -> int:
    return {"manual": 0, "nvm": 1, "fnm": 1, "volta": 1, "asdf": 1, "mise": 1,
            "common": 2, "registry": 3, "path": 4, "app": 5}[source]


class EntryUnavailable(ValueError):
    """An entry cannot become a native argv; no entry process was created."""

    def __init__(self, code: str | dict):
        super().__init__(code)
        self.code = code
        self.reason = _probe_reason(code)


class EntryUnsupported(EntryUnavailable):
    def __init__(self):
        super().__init__("entry-unsupported")


@dataclass
class ScriptLaunch:
    # None is the npm/Node selector, resolved through the existing Node discovery.
    program: Path | None
    arguments: list[str]
    environment: dict[str, str]


def _shim_path(path: Path, value: str) -> Path:
    match = re.fullmatch(r'%(?:dp0%|~dp0)[\\/]?(.+)', value, re.I)
    if match is None or is_absolute_path(match[1]) or re.search(r'[%!"\x00]', match[1]):
        raise EntryUnsupported()
    return path.parent.joinpath(*match[1].replace('\\', '/').split('/')).resolve()


def _entry_exists(target: Path) -> bool:
    # Electron owns ASAR lookup. A real archive plus the normal version probe
    # verifies an entry without adding an archive reader or executing JS here.
    for parent in target.parents:
        if parent.suffix.lower() == '.asar':
            return parent.is_file()
    return target.is_file()


def _cmd_launch(path: Path) -> ScriptLaunch:
    text = _record_text(path)
    if text is None:
        raise EntryUnsupported()
    # Recognize npm's literal directory helper as a whole, so assignments
    # skipped by a goto never become child state. No batch flow is evaluated.
    helper = r':find_dp0\nset dp0=%~dp0\nexit /b'
    lines = '\n'.join(line.strip().lstrip('@') for line in text.splitlines())
    before = re.compile(r'(?im)^goto start\n' + helper + r'\n:start(?:\n|$)')
    lines, before_count = before.subn('', lines)
    after = re.compile(r'(?im)^' + helper + r'\s*$')
    lines, after_count = after.subn('', lines)
    if before_count + after_count:
        if before_count + after_count != 1:
            raise EntryUnsupported()
        lines, call_count = re.subn(r'(?im)^call :find_dp0(?:\n|$)', '', lines)
        if call_count != 1:
            raise EntryUnsupported()
    text = lines
    node_program = re.compile(r'(?:%(?:dp0%|~dp0)[\\/]?)?node(?:\.exe)?', re.I)
    assignments = list(re.finditer(r'(?im)^\s*@?set\s+"?([\w]+)=([^"\r\n]*)"?\s*$', text))
    program_variables = {name.lower() for name in re.findall(r'"%([\w]+)%"(?=\s)', text)}
    selectors = {match[1].lower() for match in assignments
                 if match[1].lower() in program_variables and node_program.fullmatch(match[2])}
    environment = {}
    for match in assignments:
        name, value = match[1], match[2]
        if name.lower() in selectors:
            if not node_program.fullmatch(value):
                raise EntryUnsupported()
            continue
        # npm's directory/extension selection scaffolding is not child state.
        if selectors and (name.lower(), value.lower()) in (('dp0', '%~dp0'), ('pathext', '%pathext:;.js;=;%')):
            continue
        if not re.fullmatch(r'@?set\s+"[\w]+=[^"\r\n]*"', match[0].strip(), re.I) or re.search(r'[%!\x00]', value):
            raise EntryUnsupported()
        environment[name] = value
    calls = []
    selector_block = False
    for line in text.splitlines():
        stripped = line.strip().lstrip('@')
        if calls and stripped and not re.fullmatch(r'exit /b(?: %errorlevel%)?', stripped, re.I) and not stripped.lower().startswith(('rem ', '::')):
            raise EntryUnsupported()
        if calls and re.fullmatch(r'exit /b(?: %errorlevel%)?', stripped, re.I):
            continue
        if not stripped or re.fullmatch(r'(?:echo off|setlocal(?: DisableDelayedExpansion)?)', stripped, re.I) or stripped.lower().startswith(('rem ', '::')):
            continue
        assignment = re.fullmatch(r'set\s+"?([\w]+)=([^"\r\n]*)"?', stripped, re.I)
        if assignment:
            if selector_block and assignment[1].lower() not in selectors and (assignment[1].lower(), assignment[2].lower()) != ('pathext', '%pathext:;.js;=;%'):
                raise EntryUnsupported()
            continue
        if re.fullmatch(r'if exist "%(?:dp0%|~dp0)[\\/]?node\.exe" \(', stripped, re.I):
            selector_block = True
            continue
        if selector_block and stripped.lower() == ') else (':
            continue
        if selector_block and stripped == ')':
            selector_block = False
            continue
        # npm appends its one invocation after this fixed forwarding prelude.
        line = re.sub(r'^\s*endlocal\s+&\s+goto\s+#_undefined_#\s+2>NUL\s+\|\|\s+title\s+%COMSPEC%\s+&\s*', '', line, flags=re.I)
        match = re.fullmatch(r'\s*@?"([^"\r\n]+)"\s+(.+?)\s+%\*\s*', line)
        if match is None or selector_block:
            raise EntryUnsupported()
        program, operands = match.groups()
        tokens = re.findall(r'"[^"\r\n]*"|[^\s"]+', operands)
        if not re.fullmatch(r'(?:"[^"\r\n]*"|[^\s"]+)(?:\s+(?:"[^"\r\n]*"|[^\s"]+))*', operands):
            raise EntryUnsupported()
        arguments = []
        targets = 0
        for token in tokens:
            if re.match(r'^"%(?:dp0%|~dp0)', token, re.I):
                target = _shim_path(path, token[1:-1])
                if target.suffix.lower() not in ('.js', '.cjs', '.mjs') or not _entry_exists(target):
                    raise EntryUnsupported()
                arguments.append(str(target))
                targets += 1
            else:
                quoted = token.startswith('"')
                literal = token[1:-1] if quoted else token
                if not literal or re.search(r'[%!"\x00]' if quoted else r'[%!^&|<>"\x00]', literal) or is_absolute_path(literal):
                    raise EntryUnsupported()
                arguments.append(literal)
        if targets != 1:
            raise EntryUnsupported()
        if node_program.fullmatch(program) or program.lower() in {'%' + name + '%' for name in selectors}:
            executable = None
        else:
            executable = _shim_path(path, program)
            if executable.suffix.lower() != '.exe' or not executable.is_file():
                raise EntryUnsupported()
        calls.append(ScriptLaunch(executable, arguments, environment))
    if len(calls) != 1 or selector_block:
        raise EntryUnsupported()
    return calls[0]


def shim_environment(path: str | Path | None) -> dict[str, str]:
    """Derive child state from the original entry already held in health records."""
    return _cmd_launch(Path(path)).environment if path and Path(path).suffix.lower() == '.cmd' else {}


def _script_launch(path: Path) -> ScriptLaunch | None:
    if path.suffix.lower() == '.cmd':
        return _cmd_launch(path)
    if os.name == "nt" and path.suffix.lower() not in (".js", ".cjs", ".mjs"):
        return None
    try:
        if path.resolve().suffix.lower() in (".js", ".cjs", ".mjs"):
            return ScriptLaunch(None, [str(path)], {})
        if path.suffix.lower() in (".ps1", ".bat", ".com", ".exe") or os.name == "nt":
            return None
        with path.open("rb") as stream:
            first_line = stream.readline(128).lower()
            return ScriptLaunch(None, [str(path)], {}) if first_line.startswith(b"#!") and b"node" in first_line else None
    except (OSError, RuntimeError):
        return None


def _node_for(path: Path, environment: dict, deadline: float) -> tuple[str | None, str | dict | None]:
    shutdown = True  # No child has been created in this scan stage.
    try:
        # Use the launcher directory before resolving npm's symlink into node_modules.
        adjacent = ([path.parent / "node.exe"] if os.name == "nt" else _variants(path.parent, "node"))
        seen = set()
        failures = []
        for option in adjacent:
            if str(option) in seen or file_fingerprint(option) is None:
                continue
            seen.add(str(option))
            try:
                command = _native_command(option)
            except EntryUnavailable as error:
                failures.append(error.code)
                continue
            child_env = native_environment(environment, command=command)
            shutdown = False
            code, output = probe([*command, "--version"], child_env, deadline=deadline)
            shutdown = code != "shutdown-unverified"
            if not shutdown:
                return None, code
            if code is None:
                match = _VERSION.search(output.decode("utf-8", "replace")[:256])
                return str(option), match.group(0) if match else "unknown"
            failures.append(code)
        shutdown = False
        found = _discover("node", environment=environment, deadline=deadline)
        shutdown = _code(found) != "shutdown-unverified"
        if found["available"]:
            return found["executable"], found["version"]
        if not shutdown:
            return None, _code(found)
        if found['status'] == 'unknown' or any(code in ('timeout', 'scan-timeout') for code in failures):
            return None, 'scan-timeout' if _code(found) == 'scan-timeout' else 'timeout'
        missing = "node-missing" if found["status"] == "missing" or _code(found) == "entry-unsupported" else found["reason"]
        return None, failures[0] if failures else missing or "node-missing"
    except Exception as error:
        if hasattr(error, "discovery_shutdown_confirmed"):
            shutdown = error.discovery_shutdown_confirmed
        error.discovery_shutdown_confirmed = shutdown
        raise


def _native_command(path: Path) -> list[str]:
    if os.name == "nt" and path.suffix.lower() != ".exe":
        raise EntryUnsupported()
    if os.name != "nt" and not os.access(path, os.X_OK):
        raise EntryUnavailable("interpreter-missing")
    return [str(path)]


def _launch_command(path: Path, environment: dict, *, deadline: float | None = None) -> list[str]:
    """Resolve one entry into native argv without invoking a command shell."""
    if not path.is_file():
        raise EntryUnavailable("not-found")
    entry = _script_launch(path)
    if entry is not None:
        if entry.program is None:
            node, problem = _node_for(path, environment, deadline if deadline is not None else time.monotonic() + _SCAN_TIMEOUT)
            if node is None:
                raise EntryUnavailable(problem)
            command = [node]
        else:
            command = _native_command(entry.program)
        return [*command, *entry.arguments]
    return _native_command(path)


PROBE_REASON_CODES: dict[str, str] = {
    "not-found": "harness.not-found",
    "entry-unsupported": "harness.entry-unsupported",
    "command-line-too-long": "harness.command-line-too-long",
    "node-missing": "harness.node-missing",
    "login-required": "harness.login-required",
    "interpreter-missing": "harness.interpreter-missing",
    "timeout": "harness.timeout",
    "output-limit": "harness.output-limit",
    "auth-unverified": "harness.auth-unverified",
    "version-unknown": "harness.version-unknown",
    "scan-timeout": "harness.scan-timeout",
    "shutdown-unverified": "harness.shutdown-unverified",
    "pipe-failed": "harness.pipe-failed",
    "probe-failed": "harness.probe-failed",
    "launch-failed": "harness.launch-failed",
}


def _probe_reason(code: str | dict | None) -> dict | None:
    if isinstance(code, dict):
        return code
    return make_reason(PROBE_REASON_CODES[code], operation="probe") if code else None


def _code(record: dict) -> str | None:
    value = record.get("reason")
    return value["code"].removeprefix("harness.") if value else None


def _candidate_handshake(harness: str, candidate: dict, environment: dict, deadline: float) -> dict:
    shutdown = True  # No child has been created in this scan stage.
    try:
        path = Path(candidate["path"])
        result = {"path": str(path), "source": candidate["source"], "fingerprint": candidate["fingerprint"],
                  "status": "unhealthy", "reason": None, "version": None, "command": []}
        if not candidate["exists"]:
            result["status"] = "missing"
            result["reason"] = _probe_reason("not-found")
            return result
        try:
            command = (_native_command(path) if harness == "node" else
                       _launch_command(path, environment, deadline=deadline))
        except EntryUnavailable as error:
            if error.code in ("timeout", "scan-timeout"):
                result["status"] = "unknown"
            result["reason"] = _probe_reason(error.code)
            return result
        launch_environment = shim_environment(path)
        child_env = native_environment(environment, command=command)
        child_env.update(launch_environment)
        shutdown = False
        code, output = (_probe_dsh_version(command, child_env, deadline, launch_environment=launch_environment) if harness == "dsh" else
                        probe([*command, "--version"], child_env, deadline=deadline))
        shutdown = code != "shutdown-unverified"
        if code is not None:
            if code in ("timeout", "scan-timeout"):
                result["status"] = "unknown"
            result["reason"] = _probe_reason(code)
            return result
        match = _VERSION.search(output.decode("utf-8", "replace")[:256])
        result["version"] = match.group(0) if match else "unknown"
        if harness == "codex":
            shutdown = False
            code, output = probe([*command, "login", "status"], child_env, deadline=deadline)
            shutdown = code != "shutdown-unverified"
            if not shutdown:
                result["reason"] = _probe_reason(code)
                return result
            status = output.decode("utf-8", "replace").lower()
            if "not logged in" in status or "logged out" in status:
                result.update(status="login-required", reason=_probe_reason("login-required"))
                return result
            if code is not None:
                if code in ("timeout", "scan-timeout"):
                    result["status"] = "unknown"
                result["reason"] = _probe_reason(code)
                return result
            if "logged in" not in status:
                result["reason"] = _probe_reason("auth-unverified")
                return result
        elif harness == "claude":
            shutdown = False
            code, output = probe([*command, "auth", "status", "--json"], child_env, deadline=deadline)
            shutdown = code != "shutdown-unverified"
            if not shutdown:
                result["reason"] = _probe_reason(code)
                return result
            if code in ("timeout", "scan-timeout"):
                result.update(status="unknown", reason=_probe_reason(code))
                return result
            try:
                account = json.loads(output)
            except (ValueError, UnicodeError):
                result["reason"] = _probe_reason(code or "auth-unverified")
                return result
            if not isinstance(account, dict) or account.get("loggedIn") is not True:
                result.update(status="login-required", reason=_probe_reason("login-required"))
                return result
            if code is not None:
                result["reason"] = _probe_reason(code)
                return result
            from .claude.config import provider_from_account
            from ...protocol.billing import claude_status
            from datetime import datetime, timezone
            observed_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
            provider = provider_from_account(account)
            result["billingByProvider"] = {provider: claude_status({**account, "apiProvider": provider}, observed_at)}
        result.update(status="ready", command=command,
                      reason=_probe_reason("version-unknown") if match is None else None)
        return result
    except Exception as error:
        if hasattr(error, "discovery_shutdown_confirmed"):
            shutdown = error.discovery_shutdown_confirmed
        error.discovery_shutdown_confirmed = shutdown
        raise


def discover(harness: str, *, manual_path: str | None = None, environment: dict | None = None) -> dict:
    """Bound the entire scan and avoid lower-priority work after a ready tier."""
    return _discover(harness, manual_path=manual_path, environment=environment,
                     deadline=time.monotonic() + _SCAN_TIMEOUT)


def _discover(harness: str, *, manual_path: str | None = None,
              environment: dict | None = None, deadline: float) -> dict:
    shutdown = True  # No child has been created in this scan stage.
    try:
        snapshot = candidate_snapshot(harness, manual_path=manual_path, environment=environment)
        environment = os.environ if environment is None else environment
        candidates = sorted((item for item in snapshot["candidates"] if item["exists"]),
                            key=lambda item: _priority(item["source"]))[:_MAX_CANDIDATES]
        attempts = []
        ready_tier = None
        incomplete = False
        incomplete_reason = None
        for item in candidates:
            tier = _priority(item["source"])
            if ready_tier is not None and tier > ready_tier:
                break
            if time.monotonic() >= deadline:
                incomplete = True
                incomplete_reason = "scan-timeout"
                break
            shutdown = False
            attempt = _candidate_handshake(harness, item, environment, deadline)
            shutdown = _code(attempt) != "shutdown-unverified"
            attempts.append(attempt)
            if attempt["status"] == "ready":
                ready_tier = tier
            if _code(attempt) in ("scan-timeout", "shutdown-unverified"):
                incomplete = True
                incomplete_reason = _code(attempt)
                break
        ready = [item for item in attempts if item["status"] == "ready"]
        selected = min(ready, key=lambda item: (_priority(item["source"]),
                                                tuple(-part for part in _version_key(item["version"])))) if ready else None
        if incomplete:
            selected = None
            status = "unknown" if incomplete_reason == "scan-timeout" else "unhealthy"
            reason = incomplete_reason
        elif selected is None:
            failure = next((item for item in attempts if item["status"] == "unknown"), None)
            failure = failure or next((item for item in attempts if item["status"] == "login-required"), None)
            failure = failure or (attempts[0] if attempts else None)
            status = failure["status"] if failure else "missing"
            reason = failure["reason"] if failure else "not-found"
        else:
            status, reason = "ready", selected["reason"]
        diagnostics = [{"path": item["path"], "source": item["source"], "status": item["status"],
                        "reason": item["reason"]} for item in attempts[:_MAX_DIAGNOSTICS]]
        if not attempts:
            diagnostics = [{"path": item["path"], "source": item["source"], "status": "missing",
                            "reason": _probe_reason("not-found")} for item in snapshot["candidates"][:_MAX_DIAGNOSTICS]]
        return {"harness": harness, "status": status, "available": selected is not None,
                "command": selected["command"] if selected else [],
                "executable": selected["path"] if selected else None,
                "version": selected["version"] if selected else None,
                "source": selected["source"] if selected else None,
                "fingerprint": selected["fingerprint"] if selected else None,
                "scanFingerprint": _scan_hash(snapshot), "candidates": diagnostics,
                **({"billingByProvider": selected["billingByProvider"]}
                   if selected is not None and "billingByProvider" in selected else {}),
                "reason": _probe_reason(reason)}
    except Exception as error:
        if hasattr(error, "discovery_shutdown_confirmed"):
            shutdown = error.discovery_shutdown_confirmed
        error.discovery_shutdown_confirmed = shutdown
        raise
