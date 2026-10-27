"""Codex executable selection without changing its account or global settings."""
from __future__ import annotations

from ....protocol.contracts import CONTRACT_VERSION

DEFAULT_MODEL_PROVIDER = "openai"
INIT_CLIENT = {"clientInfo": {"name": "hey_my_buddy", "title": "Hey My Buddy", "version": CONTRACT_VERSION}}


class CodexUnavailable(Exception):
    pass


def native_provider(configured: dict) -> str:
    provider = configured.get("model_provider")
    return provider if isinstance(provider, str) and provider else DEFAULT_MODEL_PROVIDER


def launch(environment: dict | None = None) -> tuple[list[str], dict]:
    """Resolve the native argv and its environment from one selected entry."""
    from ..runtime_selection import launch as native_launch

    return native_launch("codex", CodexUnavailable, environment)


def cli_command(environment: dict | None = None) -> list[str]:
    return launch(environment)[0]
