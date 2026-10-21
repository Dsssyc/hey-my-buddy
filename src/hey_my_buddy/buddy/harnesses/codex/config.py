"""Codex executable selection without changing its account or global settings."""
from __future__ import annotations

import os
from pathlib import Path


DEFAULT_MODEL_PROVIDER = "openai"


class CodexUnavailable(Exception):
    pass


def native_provider(configured: dict) -> str:
    provider = configured.get("model_provider")
    return provider if isinstance(provider, str) and provider else DEFAULT_MODEL_PROVIDER


def cli_command(environment: dict | None = None) -> list[str]:
    env = os.environ if environment is None else environment
    if not (os.environ.get("HMB_DEV_SOURCE") == "1" and env.get("HMB_CODEX_CLI")):
        from ..runtime_selection import command_for
        from ....errors import BoardError
        try:
            return command_for("codex", env)
        except BoardError as error:
            raise CodexUnavailable(error.message) from None
    from ..discovery import EntryUnavailable, _launch_command
    path = Path(env["HMB_CODEX_CLI"]).expanduser().absolute()
    try:
        return _launch_command(path, env)
    except EntryUnavailable as error:
        failure = CodexUnavailable(str(error))
        failure.reason = error.reason
        raise failure from None


def native_environment(environment: dict) -> dict:
    """Use the native configuration with the shared service environment allowlist."""
    from ..discovery import native_environment as clean
    command = cli_command(environment)
    result = clean(environment, command=command)
    if os.environ.get("HMB_DEV_SOURCE") == "1":
        for key in ("HMB_CODEX_FIXTURE_CASE", "HMB_CODEX_FIXTURE_STATE"):
            if key in environment:
                result[key] = environment[key]
    return result
