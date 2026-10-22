"""Codex executable selection without changing its account or global settings."""
from __future__ import annotations

import os


DEFAULT_MODEL_PROVIDER = "openai"


class CodexUnavailable(Exception):
    pass


def native_provider(configured: dict) -> str:
    provider = configured.get("model_provider")
    return provider if isinstance(provider, str) and provider else DEFAULT_MODEL_PROVIDER


def launch(environment: dict | None = None) -> tuple[list[str], dict]:
    """Resolve the native argv and its environment from one selected entry."""
    from ..runtime_selection import launch_with_environment
    from ..discovery import EntryUnavailable
    from ....errors import BoardError

    try:
        command, result = launch_with_environment("codex", environment)
    except BoardError as error:
        raise CodexUnavailable(error.message) from None
    except EntryUnavailable as error:
        failure = CodexUnavailable(str(error))
        failure.reason = error.reason
        raise failure from None
    if environment is not None and os.environ.get("HMB_DEV_SOURCE") == "1":
        for key in ("HMB_CODEX_FIXTURE_CASE", "HMB_CODEX_FIXTURE_STATE"):
            if key in environment:
                result[key] = environment[key]
    return command, result


def cli_command(environment: dict | None = None) -> list[str]:
    return launch(environment)[0]
