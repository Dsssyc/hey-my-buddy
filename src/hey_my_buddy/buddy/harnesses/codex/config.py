"""Codex executable selection without changing its account or global settings."""
from __future__ import annotations

import os


DEFAULT_MODEL_PROVIDER = "openai"


class CodexUnavailable(Exception):
    pass


def native_provider(configured: dict) -> str:
    provider = configured.get("model_provider")
    return provider if isinstance(provider, str) and provider else DEFAULT_MODEL_PROVIDER


def cli_command(environment: dict | None = None) -> list[str]:
    from ..runtime_selection import launch_for
    from ..discovery import EntryUnavailable
    from ....errors import BoardError

    try:
        return launch_for("codex", environment)[0]
    except BoardError as error:
        raise CodexUnavailable(error.message) from None
    except EntryUnavailable as error:
        failure = CodexUnavailable(str(error))
        failure.reason = error.reason
        raise failure from None


def native_environment(environment: dict) -> dict:
    """Use the native configuration with the shared service environment allowlist."""
    from ..runtime_selection import native_environment as clean
    result = clean(environment, harness="codex")
    if os.environ.get("HMB_DEV_SOURCE") == "1":
        for key in ("HMB_CODEX_FIXTURE_CASE", "HMB_CODEX_FIXTURE_STATE"):
            if key in environment:
                result[key] = environment[key]
    return result
