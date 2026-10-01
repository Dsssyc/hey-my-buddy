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
import uuid

LAUNCH_KEYS = {'BUDDY_MAX_CONCURRENT': (1, 32), 'BUDDY_WAIT_CAPACITY': (1, 1024)}
HOST_INTERNAL_KEYS = frozenset({
    'BUDDY_RUNTIME', 'BUDDY_RUNTIME_IDENTITY', 'BUDDY_PYTHON',
    'BUDDY_WORKER_STATE', 'BUDDY_WORKER_ID',
    'BUDDY_SUPERVISOR_START_ID', 'BUDDY_TASK_ID', 'BUDDY_ATTEMPT_ID', 'BUDDY_HARNESS_RECORD_FILE',
    'BUDDY_ACCOUNT_SELECTION',
})

#: The environment a long-lived Buddy service process may inherit. The daemon, an
#: upgrade-started daemon and every worker supervisor are built from this explicit
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
    'BUDDY_STATE_DIR', 'BUDDY_RUNTIME_ROOT', 'BUDDY_RUNTIME', 'BUDDY_RUNTIME_IDENTITY',
    'BUDDY_PYTHON', 'BUDDY_WORKER_ID', 'BUDDY_WORKER_STATE',
    'BUDDY_MAX_CONCURRENT', 'BUDDY_WAIT_CAPACITY', 'BUDDY_CONSOLE_PORT', 'BUDDY_LEASE_SECONDS',
    'BUDDY_MODEL_CATALOG_FILE', 'BUDDY_CLAUDE_SETTINGS_POLICY', 'BUDDY_DEBUG',
    'BUDDY_AGENT_SKILLS_DIR', 'BUDDY_CLAUDE_SKILLS_DIR', 'UV_BIN',
})

#: Extra variables only an explicit ``BUDDY_DEV_SOURCE=1`` session may pass into a
#: service. They select the checkout, its private test CLIs and its harness
#: fixtures for development and tests; a normal installation never reads them.
#: Test fixtures are named individually rather than passed through a ``BUDDY_*``
#: prefix rule.
DEVELOPMENT_ENVIRONMENT_KEYS = frozenset({
    'BUDDY_DEV_SOURCE', 'BUDDY_CLAUDE_CLI', 'BUDDY_CODEX_CLI', 'BUDDY_ZCODE_CLI',
    'BUDDY_NODE', 'BUDDY_RUNNER_PATH',
    'BUDDY_CLAUDE_FIXTURE_CASE', 'BUDDY_CLAUDE_FIXTURE_STATE', 'BUDDY_CLAUDE_FIXTURE_AUTH_STATUS',
    'BUDDY_CODEX_FIXTURE_CASE', 'BUDDY_CODEX_FIXTURE_STATE', 'BUDDY_ZCODE_TEST_CASE',
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
    scoped = 'BUDDY_AGENT_CREDENTIAL' in os.environ or 'BUDDY_AGENT_CREDENTIAL_FILE' in os.environ
    for key in list(os.environ):
        if (not scoped and key in HOST_INTERNAL_KEYS) or is_model_endpoint(key):
            os.environ.pop(key, None)


def read_private(path: Path) -> dict | None:
    from buddy.errors import BoardError
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
        from buddy.backup import sync_dir
        sync_dir(path.parent)
    finally:
        temporary.unlink(missing_ok=True)


def launch_defaults(state: Path) -> dict[str, str]:
    from buddy.errors import BoardError
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
        from buddy import runtime
        return runtime
    except ModuleNotFoundError:
        return None


def _runtime_root() -> Path:
    from buddy.home import default_runtime_root
    return Path(os.environ.get('BUDDY_RUNTIME_ROOT') or default_runtime_root()).expanduser().resolve()


def _installed_package() -> bool:
    runtime = _bootstrap_runtime()
    return (runtime.project_root() if runtime is not None else Path(__file__).resolve().parents[2]).name == 'package'


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


def selected_runtime(state: Path, *, allow_overrides: bool = True) -> Path | None:
    from buddy.errors import BoardError
    if allow_overrides and os.environ.get('BUDDY_RUNTIME'):
        target = Path(os.environ['BUDDY_RUNTIME']).expanduser().resolve()
    elif allow_overrides and os.environ.get('BUDDY_DEV_SOURCE') == '1':
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
                raise BoardError('DEV_SOURCE_REQUIRED', 'Source commands require BUDDY_DEV_SOURCE=1; use install for a stable runtime')
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
    development list is read only under an explicit ``BUDDY_DEV_SOURCE=1``.
    """
    allowed = SERVICE_ENVIRONMENT_KEYS
    if os.environ.get('BUDDY_DEV_SOURCE') == '1':
        allowed = allowed | DEVELOPMENT_ENVIRONMENT_KEYS
    environment = {key: value for key, value in os.environ.items() if key in allowed}
    if overrides:
        environment.update(overrides)
    return environment


def runtime_environment(target: Path) -> dict[str, str]:
    # This is the CLI invocation path, not a service start: preserve scoped Worker
    # credentials. Dropping them would turn a Worker into a service-token Host when
    # the selected CLI builds its request. The service allowlist above applies only
    # where a daemon or worker supervisor is created.
    env = {k:v for k,v in os.environ.items() if k not in {'PYTHONPATH','VIRTUAL_ENV','UV_PROJECT_ENVIRONMENT'}}
    env.update(BUDDY_RUNTIME=str(target), BUDDY_RUNTIME_IDENTITY='runtime:' + target.name,
               BUDDY_PYTHON=str(_runtime_python(target)))
    return env


def bootstrap_contract_version() -> str:
    # This bootstrap runs with the system Python before package dependencies exist.
    match = re.search(r'^CONTRACT_VERSION = "([^"]+)"',
                      (Path(__file__).parent / 'contracts.py').read_text(), re.MULTILINE)
    return match.group(1) if match else 'unknown'


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    try:
        clean_host_environment()
        from buddy.home import default_state_dir
        state = Path(os.environ.get('BUDDY_STATE_DIR') or default_state_dir()).expanduser().resolve()
        if args and args[0] not in {'upgrade','install','backup-preflight','--help','-h'}:
            if (state / 'upgrade.json').exists():
                from buddy.errors import BoardError
                raise BoardError('UPGRADE_IN_PROGRESS', 'The launcher is fenced until upgrade verification or recovery finishes')
            target = selected_runtime(state)
            if target is not None:
                python = str(_runtime_python(target))
                os.execve(python, [python, '-P', '-m', 'buddy.cli', *args], runtime_environment(target))
        from buddy.cli import main as cli_main
        return cli_main(args)
    except Exception as error:
        from buddy.errors import BoardError
        payload = error.payload() if isinstance(error, BoardError) else {'code':'LAUNCH_FAILED', 'message':str(error)}
        print(json.dumps({'contractVersion': bootstrap_contract_version(), 'error':payload}))
        return 1


if __name__ == '__main__':
    # Absolute script execution makes this package's upgrade bootstrap independent
    # of inherited PYTHONPATH or an older runtime pin.
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    raise SystemExit(main())
