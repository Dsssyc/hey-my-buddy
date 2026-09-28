"""Codex executable selection without changing its account or global settings."""
from __future__ import annotations

import os
import shutil
from pathlib import Path


class CodexUnavailable(Exception):
    pass


def cli_command(environment: dict | None = None) -> list[str]:
    env = os.environ if environment is None else environment
    if not (os.environ.get("BUDDY_DEV_SOURCE") == "1" and env.get("BUDDY_CODEX_CLI")):
        from ..harness_runtime import command_for
        from ..errors import BoardError
        try:
            return command_for("codex", env)
        except BoardError as error:
            raise CodexUnavailable(error.message) from None
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
    from ..harness_discovery import native_environment as clean
    command = cli_command(environment)
    result = clean(environment, command=command)
    if os.environ.get("BUDDY_DEV_SOURCE") == "1":
        for key in ("BUDDY_CODEX_FIXTURE_CASE", "BUDDY_CODEX_FIXTURE_STATE"):
            if key in environment:
                result[key] = environment[key]
    return result
