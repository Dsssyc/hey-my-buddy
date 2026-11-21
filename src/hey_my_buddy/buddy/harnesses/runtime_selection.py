"""Resolve the service-selected native command for adapters and controllers."""
import os
from pathlib import Path

from ... import harness_selection
from ...errors import BoardError


def launch_for(harness, environment=None):
    """Resolve argv and derive its environment from the same selected entry."""
    from .discovery import discover, shim_environment, _launch_command
    env = os.environ if environment is None else environment
    override = env.get(f'HMB_{harness.upper()}_CLI') if os.environ.get('HMB_DEV_SOURCE') == '1' else None
    if override:
        entry = Path(override).expanduser().absolute()
        return _launch_command(entry, env), shim_environment(entry)
    record = harness_selection.selected(harness, env)
    if record is None:
        record = discover(harness, state_dir=Path(env["HMB_STATE_DIR"]), environment=env)
    if record.get('status') != 'ready' or not record.get('command'):
        raise BoardError('HARNESS_UNAVAILABLE', 'The selected harness is unavailable',
                         harness=harness, reason=record.get('reason'))
    return list(record['command']), shim_environment(record.get('executable'))


def launch_with_environment(harness, environment=None):
    """Resolve argv and build its child environment from the same selected entry."""
    from .discovery import native_environment as clean
    source = harness_selection.environment_for(environment)
    command, overrides = launch_for(harness, source)
    result = clean(source, command=command)
    result.update(overrides)
    return command, result


_FIXTURE_ENVIRONMENT = {
    harness: tuple(f'HMB_{harness.upper()}_FIXTURE_{suffix}' for suffix in ('CASE', 'STATE'))
    for harness in ('codex', 'claude')
}


def _copy_fixture_environment(source, target, harnesses):
    for harness in harnesses:
        for key in _FIXTURE_ENVIRONMENT[harness]:
            if key in source:
                target[key] = source[key]


def launch(harness, unavailable_type, environment=None):
    """Translate a selected native launch into a harness-specific refusal."""
    from .discovery import EntryUnavailable

    try:
        command, result = launch_with_environment(harness, environment)
    except BoardError as error:
        raise unavailable_type(error.message) from None
    except EntryUnavailable as error:
        failure = unavailable_type(str(error))
        failure.reason = error.reason
        raise failure from None
    if environment is not None and os.environ.get('HMB_DEV_SOURCE') == '1':
        _copy_fixture_environment(environment, result, (harness,))
    return command, result


def controller_environment(directory, environment=None, *, read_only=False):
    """Only trusted Python controllers receive the private command selection."""
    source = (harness_selection._environment.get() or os.environ) if environment is None else environment
    from .discovery import native_environment
    env = native_environment(source)
    allowed = ('PYTHONPATH', 'PYTHONSAFEPATH', 'PYTHONUTF8', 'PYTHONIOENCODING',
               'ZCODE_BUILTIN_PROVIDER_CONFIG_FILE', 'ZCODE_PERSONAL_PROVIDER_CONFIG_FILE',
               'HMB_STATE_DIR', 'HMB_RUNTIME_ROOT', 'HMB_RUNTIME', 'HMB_RUNTIME_IDENTITY',
               harness_selection.RECORD_FILE)
    for key in allowed:
        if key in source:
            env[key] = source[key]
    if not read_only:
        for key in ('HMB_AGENT_CREDENTIAL_FILE',):
            if key in source:
                env[key] = source[key]
    if source.get('HMB_DEV_SOURCE') == '1':
        for key in ('HMB_DEV_SOURCE', 'HMB_CLAUDE_CLI', 'HMB_CODEX_CLI', 'HMB_ZCODE_CLI', 'HMB_DSH_CLI',
                    'HMB_NODE', 'HMB_ZCODE_TEST_CASE'):
            if key in source:
                env[key] = source[key]
        _copy_fixture_environment(source, env, ('codex', 'claude'))
    records = harness_selection._records.get()
    if records is not None:
        from ..roles.turn_io import private_json
        path = Path(directory) / 'harness-selection.json'
        private_json(path, records)
        env[harness_selection.RECORD_FILE] = str(path)
    return env
