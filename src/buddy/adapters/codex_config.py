"""Codex executable selection without changing its account or global settings."""
from __future__ import annotations

import os
import json
import sys
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


def read_only_config(cwd: str) -> str:
    """No implicit root/temp grant may widen a Router's frozen read scope."""
    mac_temp = '\n"/private/tmp" = "deny"' if sys.platform == 'darwin' else ''
    return ('web_search = "disabled"\napproval_policy = "never"\ndefault_permissions = "buddy-router"\n'
            'allow_login_shell = false\n'
            '[shell_environment_policy]\ninherit = "core"\nexperimental_use_profile = false\nignore_default_excludes = false\n'
            '[features]\napps = false\nmulti_agent = false\nplugins = false\nremote_plugin = false\n'
            'hooks = false\nshell_snapshot = false\ncode_mode_host = true\nbrowser_use = false\ncomputer_use = false\n'
            'in_app_browser = false\nworkspace_dependencies = false\nskill_mcp_dependency_install = false\n'
            'skip_host_skill_discovery = true\nimage_generation = false\nview_image = false\ngoals = false\n'
            'realtime_conversation = false\nskill_search = false\nin_app_local_automation = false\n'
            '[permissions.buddy-router.filesystem]\n":root" = "deny"\n":minimal" = "read"\n'
            '":tmpdir" = "deny"\n":slash_tmp" = "deny"' + mac_temp + '\n'
            + json.dumps(str(Path(cwd).resolve())) + ' = "read"\n'
            '[permissions.buddy-router.network]\nenabled = false\n')
