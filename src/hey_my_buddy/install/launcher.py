"""Package bootstrap: upgrades use new code; ordinary commands follow active runtime.

This module must load before C-Two contracts. Selection never strips an agent's
credential, and an explicit development-source or runtime pin remains explicit.
Service processes are started from the explicit allowlist below, never from the
whole Host session.
"""
from __future__ import annotations
import json
import os
from pathlib import Path
import re
import stat
import sys
import tempfile
import uuid

LAUNCH_KEYS = {'HMB_MAX_CONCURRENT': (1, 32), 'HMB_WAIT_CAPACITY': (1, 1024)}
HOST_INTERNAL_KEYS = frozenset({
    'HMB_RUNTIME', 'HMB_RUNTIME_IDENTITY',
    'HMB_TASK_ID', 'HMB_ATTEMPT_ID', 'HMB_HARNESS_RECORD_FILE',
})

#: The environment a long-lived hey-my-buddy service process may inherit. The daemon, an
#: upgrade-started daemon and every execution process are built from this explicit
#: allowlist instead of the Host session: Claude Code ``CLAUDE_CODE_*`` session
#: variables and tokens, Codex session identity, Host identity, provider routing
#: overrides and attempt credentials never reach a service. Native harness
#: authentication stays with each harness's own native configuration directory.
SERVICE_ENVIRONMENT_KEYS = frozenset({
    # Process basics every child, native harness and interpreter needs.
    'HOME', 'PATH', 'USER', 'LOGNAME', 'USERNAME', 'USERPROFILE', 'HOMEDRIVE', 'HOMEPATH',
    'APPDATA', 'LOCALAPPDATA', 'PROGRAMDATA', 'PROGRAMFILES', 'PROGRAMFILES(X86)',
    'SystemDrive', 'SystemRoot', 'SYSTEMROOT', 'windir', 'COMSPEC', 'PATHEXT',
    'NUMBER_OF_PROCESSORS', 'PROCESSOR_ARCHITECTURE', 'OS',
    'TMPDIR', 'TMP', 'TEMP',
    'LANG', 'LANGUAGE', 'TERM', 'COLORTERM',
    'LC_ALL', 'LC_CTYPE', 'LC_MESSAGES', 'LC_COLLATE', 'LC_NUMERIC', 'LC_TIME',
    'LC_MONETARY', 'LC_PAPER', 'LC_NAME', 'LC_ADDRESS', 'LC_TELEPHONE',
    'LC_MEASUREMENT', 'LC_IDENTIFICATION',
    'XDG_CONFIG_HOME', 'XDG_DATA_HOME', 'XDG_CACHE_HOME', 'XDG_STATE_HOME', 'XDG_RUNTIME_DIR',
    'HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY', 'NO_PROXY',
    'http_proxy', 'https_proxy', 'all_proxy', 'no_proxy',
    # CA certificate paths: they name trust roots, never credentials.
    'SSL_CERT_FILE', 'SSL_CERT_DIR', 'REQUESTS_CA_BUNDLE', 'CURL_CA_BUNDLE',
    'NODE_EXTRA_CA_CERTS',
    # Native harness account configuration. Model authentication stays native.
    'CLAUDE_CONFIG_DIR', 'CODEX_HOME', 'ZCODE_DATA_BASE_DIR', 'DSH_HOME',
    # This program's private roots, interpreter selection and launch settings.
    'HMB_STATE_DIR', 'HMB_RUNTIME_ROOT', 'HMB_RUNTIME', 'HMB_RUNTIME_IDENTITY',
    'HMB_MAX_CONCURRENT', 'HMB_WAIT_CAPACITY', 'HMB_CONSOLE_PORT',
    'HMB_MODEL_CATALOG_FILE', 'HMB_DEBUG',
    'HMB_AGENT_SKILLS_DIR', 'HMB_CLAUDE_SKILLS_DIR', 'UV_BIN',
})

#: Extra variables only an explicit ``HMB_DEV_SOURCE=1`` session may pass into a
#: service. They select the checkout, its private test CLIs and its harness
#: fixtures for development and tests; a normal installation never reads them.
#: Test fixtures are named individually rather than passed through a ``HMB_*``
#: prefix rule.
DEVELOPMENT_ENVIRONMENT_KEYS = frozenset({
    'HMB_DEV_SOURCE', 'HMB_DSH_CLI', 'HMB_CLAUDE_CLI', 'HMB_CODEX_CLI', 'HMB_ZCODE_CLI',
    'HMB_NODE',
    'HMB_CLAUDE_FIXTURE_CASE', 'HMB_CLAUDE_FIXTURE_STATE',
    'HMB_CODEX_FIXTURE_CASE', 'HMB_CODEX_FIXTURE_STATE', 'HMB_ZCODE_TEST_CASE',
})
HOST_ENDPOINT_KEYS = frozenset({
    'ANTHROPIC_BASE_URL', 'ANTHROPIC_BEDROCK_BASE_URL',
    'ANTHROPIC_BEDROCK_MANTLE_BASE_URL', 'ANTHROPIC_VERTEX_BASE_URL',
    'ANTHROPIC_AWS_BASE_URL', 'ANTHROPIC_FOUNDRY_BASE_URL',
    'OPENAI_BASE_URL', 'OPENAI_API_BASE', 'AZURE_OPENAI_ENDPOINT',
    'ANTHROPIC_BEDROCK_REGION_PREFIX', 'ANTHROPIC_CUSTOM_HEADERS',
    'CLAUDE_CODE_USE_BEDROCK', 'CLAUDE_CODE_USE_VERTEX', 'CLAUDE_CODE_USE_FOUNDRY',
    'AZURE_API_INGESTION_URL',
})
MODEL_ENDPOINT_PREFIXES = ('ANTHROPIC_', 'OPENAI_', 'AZURE_OPENAI_', 'GEMINI_', 'GOOGLE_GENAI_',
                           'ZAI_', 'ZCODE_', 'DEEPSEEK_', 'OPENROUTER_')


def is_model_endpoint(key: str) -> bool:
    return key in HOST_ENDPOINT_KEYS or (key.startswith(MODEL_ENDPOINT_PREFIXES)
                                         and key.endswith(('_BASE_URL', '_API_BASE', '_ENDPOINT')))


def clean_host_environment() -> None:
    """Scrub only a Host entry. An attempt credential always keeps Worker scope."""
    scoped = 'HMB_AGENT_CREDENTIAL_FILE' in os.environ
    os.environ.pop('HMB_AGENT_CREDENTIAL_FD', None)
    for key in list(os.environ):
        if (not scoped and key in HOST_INTERNAL_KEYS) or is_model_endpoint(key):
            os.environ.pop(key, None)


def _validate_agent_credential(value: object) -> dict:
    from hey_my_buddy.errors import BoardError
    if not isinstance(value, dict) or not isinstance(value.get('token'), str) or not value['token'].strip():
        raise BoardError('UNAUTHORIZED', 'The attempt credential file has no token')
    state = value.get('stateDir')
    if not isinstance(state, str) or not state or not Path(state).is_absolute():
        raise BoardError('UNAUTHORIZED', 'The attempt credential file has no absolute stateDir')
    return value


def agent_credential_document() -> dict | None:
    """Read the private attempt credential named by this command, if present."""
    from hey_my_buddy.errors import BoardError
    path_value = os.environ.get('HMB_AGENT_CREDENTIAL_FILE')
    if path_value is None:
        return None
    if not path_value.strip():
        raise BoardError('UNAUTHORIZED', 'HMB_AGENT_CREDENTIAL_FILE is empty')
    try:
        value = read_private(Path(path_value).expanduser())
    except (BoardError, OSError, ValueError) as error:
        raise BoardError('UNAUTHORIZED', 'The attempt credential file is unreadable') from error
    return _validate_agent_credential(value)


def take_agent_credential_document() -> dict | None:
    """Consume the launcher's inherited private descriptor, or read a direct CLI file."""
    from hey_my_buddy.errors import BoardError
    descriptor = os.environ.pop('HMB_AGENT_CREDENTIAL_FD', None)
    if descriptor is None:
        return agent_credential_document()
    if 'HMB_AGENT_CREDENTIAL_FILE' not in os.environ or not descriptor.isdecimal():
        raise BoardError('UNAUTHORIZED', 'The attempt credential descriptor is invalid')
    try:
        fd = int(descriptor)
        if fd < 3:
            raise BoardError('UNAUTHORIZED', 'The attempt credential descriptor is invalid')
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or (hasattr(os, 'getuid') and info.st_uid != os.getuid()) or (os.name != 'nt' and info.st_mode & 0o077):
            raise BoardError('UNAUTHORIZED', 'The attempt credential descriptor is not private')
        with os.fdopen(fd, 'rb') as stream:
            raw = stream.read(65537)
        value = json.loads(raw)
    except (OSError, ValueError, OverflowError) as error:
        raise BoardError('UNAUTHORIZED', 'The attempt credential descriptor is unreadable') from error
    return _validate_agent_credential(value)


def credential_state_dir(value: dict | None = None) -> Path | None:
    return Path(value['stateDir']).resolve() if value is not None else None


def read_private(path: Path) -> dict | None:
    from hey_my_buddy.errors import BoardError
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
    except FileNotFoundError:
        return None
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or (hasattr(os, 'getuid') and info.st_uid != os.getuid()) or (os.name != 'nt' and info.st_mode & 0o077):
            raise BoardError('LAUNCH_SETTINGS_INVALID', 'Runtime selection metadata must be owner-private')
        data = json.loads(os.read(fd, 65537))
        if not isinstance(data, dict):
            raise ValueError('Expected an object')
        return data
    finally:
        os.close(fd)


def write_private(path: Path, value: dict) -> None:
    temporary = path.with_name('.' + path.name + '.' + uuid.uuid4().hex)
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, 'w') as stream:
            json.dump(value, stream)
            stream.flush();os.fsync(stream.fileno())
        os.replace(temporary, path)
        from hey_my_buddy.blackboard.store.backup import sync_dir
        sync_dir(path.parent)
    finally:
        temporary.unlink(missing_ok=True)


def launch_defaults(state: Path) -> dict[str, str]:
    from hey_my_buddy.errors import BoardError
    value = read_private(state / 'launch-settings.json') or {}
    settings = value.get('environment', {})
    if not isinstance(settings, dict) or set(settings) - LAUNCH_KEYS.keys():
        raise BoardError('LAUNCH_SETTINGS_INVALID', 'Unknown persisted launch setting')
    result = {}
    for key, raw in settings.items():
        lower, upper = LAUNCH_KEYS[key]
        if not isinstance(raw, str) or not raw.isdecimal() or not lower <= int(raw) <= upper:
            raise BoardError('LAUNCH_SETTINGS_INVALID', 'Invalid persisted launch setting', field=key)
        result[key] = raw
    return result


def write_active_runtime(state: Path, target: Path, settings: dict[str, str] | None = None) -> None:
    write_private(state / 'active-runtime.json', {'format':1, 'runtimeDir':str(target), 'contentId':target.name})
    if settings is not None:
        write_private(state / 'launch-settings.json', {'environment':settings})


def _bootstrap_runtime() -> object | None:
    # A system Python need not have the runtime's third-party dependencies.
    try:
        from hey_my_buddy.install import runtime
        return runtime
    except ModuleNotFoundError:
        return None


def _runtime_root() -> Path:
    from hey_my_buddy.home import default_runtime_root
    return Path(os.environ.get('HMB_RUNTIME_ROOT') or default_runtime_root()).expanduser().resolve()


def _installed_package() -> bool:
    runtime = _bootstrap_runtime()
    return (runtime.project_root() if runtime is not None else Path(__file__).resolve().parents[3]).name == 'package'


def _runtime_python(target: Path) -> Path:
    return target / 'venv' / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')


def _runtime_ready(target: Path) -> bool:
    runtime = _bootstrap_runtime()
    if runtime is not None:
        return runtime.is_ready(target)
    try:
        marker = read_private(target / 'READY.json')
    except Exception:
        # The runtime marker need not use active-pointer permissions.
        try:
            if not stat.S_ISREG(os.lstat(target / 'READY.json').st_mode):
                return False
            marker = json.loads((target / 'READY.json').read_text())
        except (OSError, ValueError):
            return False
    return isinstance(marker, dict) and marker.get('state') == 'READY' and marker.get('contentId') == target.name and _runtime_python(target).is_file()


def entry_modules(target: Path) -> dict[str, str]:
    """The entry modules of a materialized runtime's *own* package layout.

    The lookup reads only the target's shipped ``src`` tree, so a coordinator
    addresses only the current package layout. An unrecognized target fails
    explicitly. This runs in the
    system-Python bootstrap, so the shared table is imported lazily: the module
    top level stays stdlib-only and works when the launcher is executed
    directly as a script, before any package dependency exists.
    """
    from hey_my_buddy.errors import BoardError
    from hey_my_buddy.install.entrypoints import COORDINATED_ROLES, ENTRY_MODULES
    found = [modules for package, modules in ENTRY_MODULES.items()
             if (target / 'src' / package).is_dir()]
    if len(found) != 1:
        raise BoardError('RUNTIME_LAYOUT_UNKNOWN',
                         'The runtime ships no single package layout this coordinator can address',
                         runtimeDir=str(target), known=sorted(ENTRY_MODULES))
    return {role: found[0][role] for role in COORDINATED_ROLES}


def selected_runtime(state: Path, *, allow_overrides: bool = True) -> Path | None:
    from hey_my_buddy.errors import BoardError
    if allow_overrides and os.environ.get('HMB_RUNTIME'):
        target = Path(os.environ['HMB_RUNTIME']).expanduser().resolve()
    elif allow_overrides and os.environ.get('HMB_DEV_SOURCE') == '1':
        return None
    else:
        try:
            pointer = read_private(state / 'active-runtime.json')
        except (ValueError, OSError) as error:
            raise BoardError('ACTIVE_RUNTIME_INVALID', 'Active runtime metadata is unreadable; rerun the fixed-version package install command') from error
        if pointer is not None:
            if not isinstance(pointer.get('runtimeDir'), str):
                raise BoardError('ACTIVE_RUNTIME_INVALID', 'Active runtime metadata is inconsistent')
            target = Path(pointer['runtimeDir'])
            if pointer.get('format') != 1 or pointer.get('contentId') != target.name:
                raise BoardError('ACTIVE_RUNTIME_INVALID', 'Active runtime metadata is inconsistent')
        else:
            # First upgrade from a release without a pointer: follow its recorded
            # stable runtime with that version's client; never invent a facade.
            endpoint = read_private(state / 'control.json') or {}
            identity = endpoint.get('runtimeIdentity', '')
            if not isinstance(identity, str) or not identity.startswith('runtime:'):
                if _installed_package():
                    raise BoardError('ACTIVE_RUNTIME_MISSING', 'Installed runtime pointer is missing; rerun the fixed-version package install command')
                raise BoardError('DEV_SOURCE_REQUIRED', 'Source commands require HMB_DEV_SOURCE=1; use install for a stable runtime')
            target = _runtime_root() / identity.removeprefix('runtime:')
        if (not target.is_absolute() or target.parent.resolve() != _runtime_root()
                or not re.fullmatch(r'[0-9a-f]{32}', target.name) or target.is_symlink()):
            raise BoardError('ACTIVE_RUNTIME_INVALID', 'Active runtime is outside the configured root')
    if not _runtime_ready(target):
        raise BoardError('ACTIVE_RUNTIME_UNAVAILABLE', 'Selected runtime is not READY; use the new package upgrade command to recover')
    return target


def service_environment(overrides: dict[str, str] | None = None) -> dict[str, str]:
    """Build a service process environment from the explicit allowlist only.

    The caller adds the private state/runtime selections, interpreter paths and
    ``PYTHONPATH`` it needs; nothing else from the Host session is copied. The
    development list is read only under an explicit ``HMB_DEV_SOURCE=1``.
    """
    allowed = SERVICE_ENVIRONMENT_KEYS
    if os.environ.get('HMB_DEV_SOURCE') == '1':
        allowed = allowed | DEVELOPMENT_ENVIRONMENT_KEYS
    environment = {key: value for key, value in os.environ.items() if key in allowed}
    if 'PATH' in environment and sys.prefix != sys.base_prefix:
        executable_directory = os.path.join(sys.prefix, 'Scripts' if os.name == 'nt' else 'bin')
        environment['PATH'] = os.pathsep.join(
            entry for entry in environment['PATH'].split(os.pathsep)
            if os.path.normcase(os.path.abspath(entry)) != os.path.normcase(os.path.abspath(executable_directory)))
    if overrides:
        environment.update(overrides)
    return environment


def runtime_environment(target: Path) -> dict[str, str]:
    # This is the CLI invocation path: preserve the scoped attempt credential
    # while discarding interpreter pins and retired Worker identity variables.
    env = {k:v for k,v in os.environ.items()
           if k not in {'PYTHONPATH', 'VIRTUAL_ENV', 'UV_PROJECT_ENVIRONMENT'}}
    env.update(HMB_RUNTIME=str(target), HMB_RUNTIME_IDENTITY='runtime:' + target.name)
    return env


def bootstrap_contract_version() -> str:
    # This bootstrap runs with the system Python before package dependencies exist.
    match = re.search(r'^CONTRACT_VERSION = "([^"]+)"',
                      (Path(__file__).resolve().parents[1] / 'protocol' / 'contracts.py').read_text(), re.MULTILINE)
    return match.group(1) if match else 'unknown'


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    try:
        clean_host_environment()
        from hey_my_buddy.home import default_state_dir
        credential = agent_credential_document()
        scoped_state = credential_state_dir(credential)
        state = scoped_state or Path(os.environ.get('HMB_STATE_DIR') or default_state_dir()).expanduser().resolve()
        if scoped_state is not None:
            os.environ['HMB_STATE_DIR'] = str(scoped_state)
        if args and args[0] not in {'upgrade','install','backup-preflight','--help','-h'}:
            marker = state / 'upgrade.json'
            if marker.exists() and json.loads(marker.read_text()).get('phase') == 'verified':
                from .upgrade import upgrade
                upgrade({})
            if marker.exists():
                from hey_my_buddy.errors import BoardError
                raise BoardError('UPGRADE_IN_PROGRESS', 'Upgrade verification or recovery is in progress; retry the same command and identity after it finishes')
            target = selected_runtime(state)
            if target is not None:
                python = str(_runtime_python(target))
                environment = runtime_environment(target)
                if credential is not None:
                    # The source file was read once. Transfer its validated contents
                    # through an anonymous private descriptor across exec, never argv/env.
                    with tempfile.TemporaryFile(mode='w+b') as transfer:
                        transfer.write(json.dumps(credential).encode('utf-8'))
                        transfer.seek(0)
                        os.set_inheritable(transfer.fileno(), True)
                        environment['HMB_AGENT_CREDENTIAL_FD'] = str(transfer.fileno())
                        os.execve(python, [python, '-P', '-m', entry_modules(target)['cli'], *args], environment)
                os.execve(python, [python, '-P', '-m', entry_modules(target)['cli'], *args], environment)
        from hey_my_buddy.cli.main import main as cli_main
        return cli_main(args, credential_document=credential)
    except Exception as error:
        from hey_my_buddy.errors import BoardError
        payload = error.payload() if isinstance(error, BoardError) else {'code':'LAUNCH_FAILED', 'message':str(error)}
        print(json.dumps({'contractVersion': bootstrap_contract_version(), 'error':payload}))
        return 1


if __name__ == '__main__':
    # Absolute script execution makes this package's upgrade bootstrap independent
    # of inherited PYTHONPATH or an older runtime pin.
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    raise SystemExit(main())
