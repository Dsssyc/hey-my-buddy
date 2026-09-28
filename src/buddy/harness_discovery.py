"""Read-only local harness discovery and bounded, model-free health checks.

The service owns caching and persistence. This module returns JSON values only and
never consults shell startup files or inherited provider credentials.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import signal
import shutil
import subprocess
import sys
import time
import selectors


HARNESSES = ("dsh", "zcode", "codex", "claude")
_NAMES = {"dsh": "dsh", "zcode": "zcode", "codex": "codex", "claude": "claude", "node": "node"}
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
        root = home / "AppData/Roaming/fnm"
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
                    value, _ = winreg.QueryValueEx(handle, "Path")
                    paths.extend(value.split(os.pathsep))
            except OSError:
                continue
        return paths
    except ImportError:
        return []


def _common_dirs(home: Path) -> list[Path]:
    if os.name == "nt":
        return [home / "AppData/Roaming/npm", home / "AppData/Local/Programs/nodejs",
                Path(r"C:\Program Files\nodejs"), home / ".local/bin"]
    return [Path("/opt/homebrew/bin"), Path("/usr/local/bin"), Path("/usr/bin"), home / ".local/bin",
            home / ".npm-global/bin", home / ".volta/bin"]


def _app_paths(adapter: str) -> list[Path]:
    if sys.platform != "darwin":
        return []
    return {
        "codex": [Path("/Applications/ChatGPT.app/Contents/Resources/codex")],
        "zcode": [Path("/Applications/ZCode.app/Contents/Resources/glm/zcode.cjs")],
        "claude": [Path("/Applications/Claude.app/Contents/Resources/claude")],
    }.get(adapter, [])


def _variants(directory: Path, name: str) -> list[Path]:
    if os.name == "nt":
        return [directory / (name + suffix) for suffix in (".exe", ".cmd", ".ps1", "")]
    return [directory / name]


def candidate_snapshot(adapter: str, *, manual_path: str | None = None,
                       environment: dict | None = None) -> dict:
    """Cheap cache key inputs: file existence and fingerprints, with PATH as a clue."""
    if adapter not in _NAMES:
        raise ValueError("unknown harness")
    home = _home()
    records, manager_dirs = _manager_records(home)
    name = _NAMES[adapter]
    places: list[tuple[str, Path]] = []
    if manual_path:
        places.append(("manual", Path(manual_path).expanduser()))
    for source, directory in manager_dirs:
        places.extend((source, path) for path in _variants(directory, name))
    places.extend(("common", path) for directory in _common_dirs(home) for path in _variants(directory, name))
    places.extend(("app", path) for path in _app_paths(adapter))
    places.extend(("registry", path) for directory in _registry_paths() for path in _variants(Path(directory), name))
    caller_path = (os.environ if environment is None else environment).get("PATH", "")
    caller_path = caller_path if isinstance(caller_path, str) else ""
    places.extend(("path", path) for directory in caller_path.split(os.pathsep) if directory and Path(directory).is_absolute()
                  for path in _variants(Path(directory), name))
    candidates = []
    seen = set()
    for source, path in places:
        if len(candidates) >= _MAX_CANDIDATES:
            break
        label = str(path)
        if label in seen:
            continue
        seen.add(label)
        fingerprint = file_fingerprint(path)
        candidates.append({"path": label, "source": source, "exists": fingerprint is not None,
                           "fingerprint": fingerprint})
    snapshot = {"adapter": adapter, "managerRecords": records, "candidates": candidates}
    if adapter != "node":
        node_paths = [Path(item["path"]) for item in candidate_snapshot("node", environment=environment)["candidates"]]
        node_paths = [path for item in candidates if item["exists"] for path in _variants(
            Path(item["path"]).parent, "node")] + node_paths
        seen_nodes = set()
        snapshot["nodeCandidates"] = []
        for path in node_paths:
            if len(snapshot["nodeCandidates"]) >= _MAX_CANDIDATES:
                break
            if str(path) in seen_nodes:
                continue
            seen_nodes.add(str(path))
            snapshot["nodeCandidates"].append({"path": str(path), "fingerprint": file_fingerprint(path)})
    return snapshot


def _scan_hash(snapshot: dict) -> str:
    data = json.dumps(snapshot, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def native_environment(environment: dict, *, command: tuple[str, ...] | list[str] = ()) -> dict:
    """Build an account-preserving, credential-free environment for native CLIs."""
    allowed = ("HOME", "USERPROFILE", "HOMEDRIVE", "HOMEPATH", "APPDATA", "LOCALAPPDATA",
               "XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME", "XDG_STATE_HOME",
               "XDG_RUNTIME_DIR", "CODEX_HOME", "CLAUDE_CONFIG_DIR", "ZCODE_DATA_BASE_DIR",
               "LANG", "LANGUAGE", "LC_ALL", "LC_CTYPE", "LC_MESSAGES", "LC_COLLATE",
               "LC_NUMERIC", "LC_TIME", "LC_MONETARY", "LC_PAPER", "LC_NAME",
               "LC_ADDRESS", "LC_TELEPHONE", "LC_MEASUREMENT", "LC_IDENTIFICATION",
               "TMPDIR", "TMP", "TEMP", "SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT",
               "TERM", "COLORTERM", "PATH", "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY",
               "NO_PROXY", "http_proxy", "https_proxy", "all_proxy", "no_proxy")
    result = {key: environment[key] for key in allowed if key in environment}
    prefixes = []
    for item in reversed(command):
        if not isinstance(item, str):
            continue
        path = Path(item)
        if path.is_absolute() and file_fingerprint(path):
            parent = str(path.parent)
            if parent not in prefixes:
                prefixes.append(parent)
    inherited_path = result.get("PATH", "")
    result["PATH"] = os.pathsep.join([*prefixes, *([inherited_path] if inherited_path else [])])
    return result


def _terminate_group(process: subprocess.Popen) -> bool:
    """Stop and reap a POSIX probe group, including children holding its pipe."""
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(process.pid, sig)
        except ProcessLookupError:
            pass
        except OSError:
            return False
        try:
            process.wait(timeout=0.25)
        except subprocess.TimeoutExpired:
            pass
    try:
        process.wait(timeout=0.5)
        return True
    except subprocess.TimeoutExpired:
        return False


def _probe_posix(process: subprocess.Popen, stop_at: float, deadline: float | None) -> tuple[str | None, bytes]:
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
            contained = _terminate_group(process)
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
            if not eof or not contained:
                return "shutdown-unverified", b""
            return reason, b""
        try:
            process.wait(timeout=max(0.01, stop_at - time.monotonic()))
        except subprocess.TimeoutExpired:
            return ("timeout" if _terminate_group(process) else "shutdown-unverified"), b""
        return ("probe-failed" if process.returncode else None), b"".join(chunks)
    finally:
        selector.close()
        process.stdout.close()


def _probe_windows(process: subprocess.Popen, stop_at: float) -> tuple[str | None, bytes]:
    """Poll a Windows pipe without a reader thread; forced tree stop is unverified."""
    import ctypes
    from ctypes import wintypes
    import msvcrt

    assert process.stdout is not None
    chunks: list[bytes] = []
    pipe = msvcrt.get_osfhandle(process.stdout.fileno())
    peek = ctypes.windll.kernel32.PeekNamedPipe
    peek.argtypes = [wintypes.HANDLE, wintypes.LPVOID, wintypes.DWORD,
                     ctypes.POINTER(wintypes.DWORD), ctypes.POINTER(wintypes.DWORD),
                     ctypes.POINTER(wintypes.DWORD)]
    peek.restype = wintypes.BOOL
    available = wintypes.DWORD()
    reason = None
    while time.monotonic() < stop_at:
        readable = peek(pipe, None, 0, None, ctypes.byref(available), None)
        if not readable:
            if process.poll() is None:
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
    return ("shutdown-unverified", b"") if forced else (
        ("probe-failed" if process.returncode else None), b"".join(chunks))


def _probe(command: list[str], environment: dict, *, deadline: float | None = None) -> tuple[str | None, bytes]:
    """Run one noninteractive probe with bounded time, output and child lifetime."""
    start = time.monotonic()
    if deadline is not None and start >= deadline:
        return "scan-timeout", b""
    try:
        process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                   stderr=subprocess.STDOUT, env=environment,
                                   start_new_session=os.name != "nt",
                                   creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0)
    except (OSError, ValueError):
        return "launch-failed", b""
    stop_at = min(start + _TIMEOUT, deadline) if deadline is not None else start + _TIMEOUT
    return _probe_windows(process, stop_at) if os.name == "nt" else _probe_posix(process, stop_at, deadline)


def _version_key(version: str | None) -> tuple[int, int, int]:
    found = _VERSION.search(version or "")
    return tuple(int(value or 0) for value in found.groups()) if found else (-1, -1, -1)


def _priority(source: str) -> int:
    return {"manual": 0, "nvm": 1, "fnm": 1, "volta": 1, "asdf": 1, "mise": 1,
            "common": 2, "app": 2, "registry": 3, "path": 4}[source]


def _script_uses_node(path: Path) -> bool:
    try:
        if path.resolve().suffix.lower() in (".js", ".cjs", ".mjs"):
            return True
        if path.suffix.lower() in (".cmd", ".ps1", ".exe"):
            return False
        with path.open("rb") as stream:
            return b"node" in stream.readline(128).lower() and stream.tell() <= 128
    except (OSError, RuntimeError):
        return False


def _node_for(path: Path, environment: dict, deadline: float) -> tuple[str | None, str | None]:
    # Use the launcher directory before resolving npm's symlink into node_modules.
    adjacent = _variants(path.parent, "node")
    seen = set()
    failures = []
    for option in adjacent:
        if str(option) in seen or file_fingerprint(option) is None:
            continue
        seen.add(str(option))
        command = _launch_command(option, environment)
        if command is None:
            failures.append("interpreter-missing")
            continue
        code, output = _probe([*command, "--version"],
                              native_environment(environment, command=command), deadline=deadline)
        if code is None:
            match = _VERSION.search(output.decode("utf-8", "replace")[:256])
            return str(option), match.group(0) if match else "unknown"
        failures.append(code)
    found = _discover("node", environment=environment, deadline=deadline)
    if found["available"]:
        return found["executable"], found["version"]
    missing = "node-missing" if found["status"] == "missing" else found["reasonCode"]
    return None, failures[0] if failures else missing or "node-missing"


def _launch_command(path: Path, environment: dict) -> list[str] | None:
    if os.name == 'nt' and path.suffix.lower() == '.cmd':
        import ntpath
        command = ntpath.join(environment.get('SYSTEMROOT') or r'C:\Windows', 'System32', 'cmd.exe')
        return [command, '/d', '/s', '/c', str(path)]
    if path.suffix.lower() == ".ps1":
        shell = shutil.which("pwsh", path=environment.get("PATH")) or shutil.which(
            "powershell.exe", path=environment.get("PATH"))
        if shell is None and os.name == "nt":
            fallback = Path(r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe")
            shell = str(fallback) if fallback.is_file() else None
        return [shell, "-NoProfile", "-NonInteractive", "-File", str(path)] if shell else None
    if os.name != "nt" and not os.access(path, os.X_OK):
        return None
    return [str(path)]


def _candidate_handshake(adapter: str, candidate: dict, environment: dict, deadline: float) -> dict:
    path = Path(candidate["path"])
    result = {"path": str(path), "source": candidate["source"], "fingerprint": candidate["fingerprint"],
              "status": "unhealthy", "reasonCode": None, "version": None, "command": []}
    if not candidate["exists"]:
        result["status"] = "missing"
        result["reasonCode"] = "not-found"
        return result
    if adapter != "node" and _script_uses_node(path):
        node, problem = _node_for(path, environment, deadline)
        if node is None:
            result["reasonCode"] = problem
            return result
        command = [node, str(path)]
    else:
        command = _launch_command(path, environment)
        if command is None:
            result["reasonCode"] = "interpreter-missing"
            return result
    child_env = native_environment(environment, command=command)
    code, output = _probe([*command, "--version"], child_env, deadline=deadline)
    if code is not None:
        result["reasonCode"] = code
        return result
    match = _VERSION.search(output.decode("utf-8", "replace")[:256])
    result["version"] = match.group(0) if match else "unknown"
    if adapter == "codex":
        code, output = _probe([*command, "login", "status"], child_env, deadline=deadline)
        status = output.decode("utf-8", "replace").lower()
        if "not logged in" in status or "logged out" in status:
            result.update(status="login-required", reasonCode="login-required")
            return result
        if code is not None:
            result["reasonCode"] = code
            return result
        if "logged in" not in status:
            result["reasonCode"] = "auth-unverified"
            return result
    elif adapter == "claude":
        code, output = _probe([*command, "auth", "status", "--json"], child_env, deadline=deadline)
        try:
            account = json.loads(output)
        except (ValueError, UnicodeError):
            result["reasonCode"] = code or "auth-unverified"
            return result
        if not isinstance(account, dict) or account.get("loggedIn") is not True:
            result.update(status="login-required", reasonCode="login-required")
            return result
        if code is not None:
            result["reasonCode"] = code
            return result
        if account.get("apiProvider") not in ("anthropic", "firstParty"):
            result["reasonCode"] = "auth-unverified"
            return result
    result.update(status="ready", command=command,
                  reasonCode="version-unknown" if match is None else None)
    return result


_REMEDIES = {
    "not-found": "安装该 harness，或提供其可执行文件的手动路径后重新检测。",
    "node-missing": "安装与 CLI 配套的 Node.js，或将 node 放在 CLI 的 bin 目录。",
    "login-required": "使用所选 CLI 的原生登录命令登录后重新检测。",
    "interpreter-missing": "安装脚本所需的解释器后重新检测。",
    "timeout": "检查 CLI 本地启动是否卡住，然后重新检测。",
    "output-limit": "检查 CLI 的版本或登录检查输出，然后重新检测。",
    "auth-unverified": "检查 CLI 的本地登录状态与版本后重新检测。",
    "version-unknown": "CLI 握手通过，但版本文本未识别；请核对本地版本。",
    "scan-timeout": "候选检测超过总时间上限；请缩小候选范围后重新检测。",
    "shutdown-unverified": "探测子进程的停止状态未获确认；请检查残留进程后重新检测。",
    "pipe-failed": "探测输出管道失败；请检查 CLI 的本地启动。",
    "probe-failed": "检查 CLI 安装与本地启动依赖后重新检测。",
    "launch-failed": "检查可执行权限与安装路径后重新检测。",
}


def discover(adapter: str, *, manual_path: str | None = None, environment: dict | None = None) -> dict:
    """Bound the entire scan and avoid lower-priority work after a ready tier."""
    return _discover(adapter, manual_path=manual_path, environment=environment,
                     deadline=time.monotonic() + _SCAN_TIMEOUT)


def _discover(adapter: str, *, manual_path: str | None = None,
              environment: dict | None = None, deadline: float) -> dict:
    snapshot = candidate_snapshot(adapter, manual_path=manual_path, environment=environment)
    environment = os.environ if environment is None else environment
    candidates = sorted((item for item in snapshot["candidates"] if item["exists"]),
                        key=lambda item: _priority(item["source"]))
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
        attempt = _candidate_handshake(adapter, item, environment, deadline)
        attempts.append(attempt)
        if attempt["status"] == "ready":
            ready_tier = tier
        if attempt["reasonCode"] in ("scan-timeout", "shutdown-unverified"):
            incomplete = True
            incomplete_reason = attempt["reasonCode"]
            break
    ready = [item for item in attempts if item["status"] == "ready"]
    selected = min(ready, key=lambda item: (_priority(item["source"]),
                                            tuple(-part for part in _version_key(item["version"])))) if ready else None
    if incomplete:
        selected = None
        status, reason = "unhealthy", incomplete_reason
    elif selected is None:
        failure = next((item for item in attempts if item["status"] == "login-required"), None)
        failure = failure or (attempts[0] if attempts else None)
        status = failure["status"] if failure else "missing"
        reason = failure["reasonCode"] if failure else "not-found"
    else:
        status, reason = "ready", selected["reasonCode"]
    diagnostics = [{"path": item["path"], "source": item["source"], "status": item["status"],
                    "reasonCode": item["reasonCode"]} for item in attempts[:_MAX_DIAGNOSTICS]]
    if not attempts:
        diagnostics = [{"path": item["path"], "source": item["source"], "status": "missing",
                        "reasonCode": "not-found"} for item in snapshot["candidates"][:_MAX_DIAGNOSTICS]]
    return {"adapter": adapter, "status": status, "available": selected is not None,
            "command": selected["command"] if selected else [],
            "executable": selected["path"] if selected else None,
            "version": selected["version"] if selected else None,
            "source": selected["source"] if selected else None,
            "fingerprint": selected["fingerprint"] if selected else None,
            "scanFingerprint": _scan_hash(snapshot), "candidates": diagnostics,
            "reasonCode": reason, "remedy": _REMEDIES.get(reason) if reason else None}
