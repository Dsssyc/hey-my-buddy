"""Codex executable selection without changing its account or global settings."""
from __future__ import annotations

import os
import shutil
from pathlib import Path


class CodexUnavailable(Exception):
    pass


def cli_command(environment: dict | None = None) -> list[str]:
    env = os.environ if environment is None else environment
    value = env.get("BUDDY_CODEX_CLI") or shutil.which("codex", path=env.get("PATH"))
    if not value:
        fallback = Path("/Applications/ChatGPT.app/Contents/Resources/codex")
        value = str(fallback) if fallback.is_file() else None
    if not value:
        raise CodexUnavailable("Codex CLI is missing; configure BUDDY_CODEX_CLI")
    path = Path(value).expanduser().resolve()
    if not path.is_file() or not os.access(path, os.X_OK):
        raise CodexUnavailable("the configured Codex CLI is not executable")
    return [str(path)]


def native_environment(environment: dict) -> dict:
    """Use the installed native ChatGPT account, never a caller's API key fallback."""
    result = dict(environment)
    for key in ("OPENAI_API_KEY", "CODEX_API_KEY", "OPENAI_BASE_URL", "OPENAI_ORG_ID", "OPENAI_PROJECT_ID"):
        result.pop(key, None)
    return result
