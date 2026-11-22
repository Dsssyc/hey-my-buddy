"""Read-only local harness discovery and bounded, model-free health checks.

The service owns caching and persistence. This module returns JSON values only and
never consults shell startup files or inherited provider credentials.
"""
from __future__ import annotations

from dataclasses import dataclass
from functools import partial
import json
import logging
import os
from pathlib import Path
import re
import shutil
import subprocess
import time
import selectors

from ... import harness_locations, private_dirs
from ...paths import is_absolute_path
from ...protocol.reasons import reason as make_reason


_MAX_CANDIDATES = 96
_MAX_DIAGNOSTICS = 12
_MAX_OUTPUT = 16_384
_TIMEOUT = 5.0
_SCAN_TIMEOUT = 20.0
_VERSION = re.compile(r"(?<![\w])v?(\d+)\.(\d+)(?:\.(\d+))?(?:[-+][\w.-]+)?")


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
        if is_absolute_path(path) and harness_locations.file_fingerprint(path):
            prefixes.append(str(path.parent))
    inherited_path = result.get("PATH", "")
    result["PATH"] = os.pathsep.join([*prefixes, *([inherited_path] if inherited_path else [])])
    result.update(shim_environment(entry))
    return result


def _probe_posix(process: subprocess.Popen, stop_at: float,
                 deadline: float | None, handle) -> tuple[str | None, bytes, bool]:
    """Keep the read outcome separate from the final session/pipe stop evidence."""
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
            handle.terminate(grace_seconds=0.25, settle_seconds=0.75)
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
            contained = handle.shutdown_confirmed(settle_seconds=0)
            return reason, b"", contained and eof
        if handle.wait(timeout=max(0.01, stop_at - time.monotonic())) is None:
            contained, _ = handle.terminate(grace_seconds=0.25, settle_seconds=0.75)
            return "timeout", b"", contained
        contained, _ = handle.terminate(grace_seconds=0.25, settle_seconds=0.75)
        return ("probe-failed" if process.returncode else None), b"".join(chunks), contained
    except Exception as error:
        handle.terminate(grace_seconds=0.25, settle_seconds=0.75)
        drain_until = time.monotonic() + 0.5
        while not eof and time.monotonic() < drain_until:
            try:
                if not selector.select(max(0, drain_until - time.monotonic())):
                    break
                eof = not os.read(process.stdout.fileno(), 4096)
            except OSError:
                break
        contained = handle.shutdown_confirmed(settle_seconds=0)
        error.discovery_shutdown_confirmed = contained and eof
        raise
    finally:
        selector.close()
        process.stdout.close()


def _probe_windows(process: subprocess.Popen, stop_at: float, handle,
                   deadline: float | None) -> tuple[str | None, bytes, bool]:
    """Poll the pipe, then stop and observe the entire held Job, including conhost."""
    failure = None
    stopped = False
    try:
        import msvcrt
        import pywintypes
        import win32pipe

        assert process.stdout is not None
        chunks: list[bytes] = []
        pipe = msvcrt.get_osfhandle(process.stdout.fileno())
        reason = None
        while time.monotonic() < stop_at:
            try:
                _, available, _ = win32pipe.PeekNamedPipe(pipe, 0)
            except pywintypes.error as error:
                # stdout can close before the process exits; retain its exit budget.
                if error.winerror != 109:  # ERROR_BROKEN_PIPE
                    reason = "pipe-failed"
                break
            if available:
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
            reason = "scan-timeout" if deadline is not None and deadline <= stop_at else "timeout"
        if reason is None:
            try:
                process.wait(timeout=max(0, stop_at - time.monotonic()))
            except subprocess.TimeoutExpired:
                reason = "scan-timeout" if deadline is not None and deadline <= stop_at else "timeout"
    except Exception as error:
        failure = error
        raise
    finally:
        try:
            stopped, _ = handle.terminate(grace_seconds=0)
        finally:
            process.stdout.close()
            if failure is not None:
                failure.discovery_shutdown_confirmed = stopped
    return (reason, b"", stopped) if reason is not None else (
        ("probe-failed" if process.returncode else None), b"".join(chunks), stopped)


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
    from ...protocol.owned_process import WindowsCommandLineTooLong, owned_popen
    try:
        process = owned_popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                              stderr=subprocess.STDOUT, env=environment, cwd=cwd,
                              start_new_session=os.name != "nt")
    except Exception as error:
        creation = getattr(error, "owned_popen_evidence", None)
        if creation is not None:
            receipt.update(created=creation.created, shutdown=creation.shutdown_confirmed,
                           exit_code=creation.exit_code)
        if isinstance(error, WindowsCommandLineTooLong):
            return make_reason("harness.command-line-too-long", operation="probe", utf16Units=error.utf16_units, limit=error.limit), b""
        if isinstance(error, (OSError, ValueError)):
            return "launch-failed", b""
        raise
    receipt.update(created=True, shutdown=False)
    stop_at = min(start + _TIMEOUT, deadline) if deadline is not None else start + _TIMEOUT
    try:
        from .base import ProcessHandle
        try:
            handle = ProcessHandle(process, stop_channel="signal", log_paths={})
        except Exception:
            process.stdout.close()
            raise
        code, output, stopped = (_probe_windows(process, stop_at, handle, deadline) if os.name == "nt" else
                                 _probe_posix(process, stop_at, deadline, handle))
    except Exception as error:
        creation = getattr(error, "owned_popen_evidence", None)
        receipt.update(shutdown=(creation.shutdown_confirmed if creation is not None else
                                 getattr(error, "discovery_shutdown_confirmed", False) is True),
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
                       state_dir: Path,
                       launch_environment: dict | None = None) -> tuple[str | dict | None, bytes]:
    """Use the same owned DSH launcher for the no-input version check.

    Keep the caller's HOME, DSH_HOME and native environment unchanged, and
    retain the directory if the owned group cannot be confirmed stopped.
    """
    from .dsh.acp.launch import LaunchOwnershipError, LaunchRejected, launch
    from ...protocol.owned_process import WindowsCommandLineTooLong, WorkingDirectoryRejected

    start = time.monotonic()
    if start >= deadline:
        return "scan-timeout", b""
    root = private_dirs.make_temporary_dir(prefix="hey-my-buddy-dsh-probe-",
        directory=private_dirs.ensure_private_dir(state_dir / "tmp")).resolve()
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
        except WorkingDirectoryRejected as error:
            return error.reason, b""
        except (LaunchRejected, OSError, ValueError):
            return code, output
        else:
            process.stdin.close()
            stop_at = min(start + _TIMEOUT, deadline)
            code, output, _ = (_probe_windows(process, stop_at, handle, deadline) if os.name == "nt" else
                               _probe_posix(process, stop_at, deadline, handle))
    except Exception as error:
        failure = error
        raise
    finally:
        if handle is not None:
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


def _probe_cli_version(command: list[str], environment: dict, deadline: float, *,
                       launch_environment: dict) -> tuple[str | dict | None, bytes]:
    return probe([*command, "--version"], {**environment, **launch_environment}, deadline=deadline)


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
    text, _ = harness_locations._read_record(path)
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
        adjacent = ([path.parent / "node.exe"] if os.name == "nt" else harness_locations._variants(path.parent, "node"))
        seen = set()
        failures = []
        for option in adjacent:
            if str(option) in seen or harness_locations.file_fingerprint(option) is None:
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
        found = _discover("node", environment=environment, deadline=deadline,
                          version_probe=_probe_cli_version)
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


def _candidate_handshake(harness: str, candidate: dict, environment: dict, deadline: float, *,
                         version_probe) -> dict:
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
        code, output = version_probe(command, child_env, deadline, launch_environment=launch_environment)
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


def discover(harness: str, *, state_dir: Path, manual_path: str | None = None,
             environment: dict | None = None) -> dict:
    """Bound the entire scan and avoid lower-priority work after a ready tier."""
    return _discover(harness, manual_path=manual_path, environment=environment,
                     version_probe=partial(_probe_dsh_version, state_dir=state_dir)
                     if harness == "dsh" else _probe_cli_version,
                     deadline=time.monotonic() + _SCAN_TIMEOUT)


def _discover(harness: str, *, manual_path: str | None = None,
              environment: dict | None = None, deadline: float, version_probe) -> dict:
    shutdown = True  # No child has been created in this scan stage.
    try:
        snapshot = harness_locations.candidate_snapshot(harness, manual_path=manual_path, environment=environment)
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
            attempt = _candidate_handshake(harness, item, environment, deadline, version_probe=version_probe)
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
        if attempts:
            diagnostics = [{"path": item["path"], "source": item["source"], "status": item["status"],
                            "reason": item["reason"]} for item in attempts[:_MAX_DIAGNOSTICS]]
        else:
            diagnostics = [{"path": item["path"], "source": item["source"], "status": "missing",
                            "reason": _probe_reason("not-found")} for item in snapshot["candidates"][:_MAX_DIAGNOSTICS]]
        return {"harness": harness, "status": status, "available": selected is not None,
                "command": selected["command"] if selected else [],
                "executable": selected["path"] if selected else None,
                "version": selected["version"] if selected else None,
                "source": selected["source"] if selected else None,
                "fingerprint": selected["fingerprint"] if selected else None,
                "scanFingerprint": harness_locations._scan_hash(snapshot), "candidates": diagnostics,
                **({"billingByProvider": selected["billingByProvider"]}
                   if selected is not None and "billingByProvider" in selected else {}),
                "reason": _probe_reason(reason)}
    except Exception as error:
        if hasattr(error, "discovery_shutdown_confirmed"):
            shutdown = error.discovery_shutdown_confirmed
        error.discovery_shutdown_confirmed = shutdown
        raise
