"""Codex executable selection without changing its account or global settings."""
from __future__ import annotations

import os
import json
import sys
import shutil
from pathlib import Path

from ....json_codec import decode_strict_json
from ...roles.turn_io import private_json


class CodexUnavailable(Exception):
    pass


def cli_command(environment: dict | None = None) -> list[str]:
    env = os.environ if environment is None else environment
    if not (os.environ.get("BUDDY_DEV_SOURCE") == "1" and env.get("BUDDY_CODEX_CLI")):
        from ..runtime_selection import command_for
        from ....errors import BoardError
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
    from ..discovery import native_environment as clean
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


def policy_matches(actual, wanted):
    """Requested controls must match; native default metadata may coexist.

    Callers separately require the exact filesystem grant map so extra native
    metadata cannot authorize another path.
    """
    if isinstance(wanted, dict):
        return isinstance(actual, dict) and all(policy_matches(actual.get(key), value) for key, value in wanted.items())
    return type(actual) is type(wanted) and actual == wanted


# These switches cover the sources in codex-rs/core/src/tools/spec_plan.rs.
# ModelInfo additionally advertises clock and asynchronous message tools, so
# feature switches alone cannot establish an empty native inventory.
_TOOL_FEATURES = (
    "shell_tool", "unified_exec", "code_mode", "code_mode_host", "code_mode_only",
    "multi_agent", "multi_agent_v2", "apps",
    "plugins", "remote_plugin", "hooks", "view_image", "image_generation",
    "token_budget", "current_time_reminder", "sleep_tool", "send_message_to_user_async",
    "request_permissions_tool", "goals", "browser_use", "computer_use",
    "in_app_browser", "tool_suggest", "recommended_plugins", "tool_search",
    "standalone_web_search", "deferred_executor", "skill_search", "enable_mcp_apps",
    "memories", "agent_message_board", "send_async_message", "shell_snapshot",
    "default_mode_request_user_input", "tool_call_mcp_elicitation", "artifact",
    "in_app_local_automation", "realtime_conversation",
)


def _no_tool_config(catalog_path: Path) -> str:
    lines = [f"model_catalog_json = {json.dumps(str(catalog_path))}",
             'web_search = "disabled"', 'approval_policy = "never"',
             'project_doc_max_bytes = 0', 'developer_instructions = ""',
             'include_environment_context = false', 'include_permissions_instructions = false',
             'include_apps_instructions = false', 'include_collaboration_mode_instructions = false',
             '[skills]', 'include_instructions = false', '[skills.bundled]', 'enabled = false',
             '[tools.update_plan]', 'enabled = false',
             '[tools.experimental_request_user_input]', 'enabled = false',
             '[cloud.skills]', 'enabled = false', '[orchestrator.mcp]', 'enabled = false',
             '[features]']
    lines.extend(f"{name} = false" for name in _TOOL_FEATURES)
    lines.append('skip_host_skill_discovery = true')
    return "\n".join(lines) + "\n"


def prepare_no_tool_home(native_root: Path, environment: dict, spec: dict) -> Path:
    """The fast call's private home: public model metadata only, no tools.

    Copy only native public model metadata and rewrite its tool-related fields;
    the real account home is left intact and only its auth file is linked.
    """
    from .protocol import CodexProtocolError
    old_home = Path(environment.get("CODEX_HOME") or Path.home() / ".codex")
    home = native_root / "codex-home"
    home.mkdir(mode=0o700, parents=True, exist_ok=True)
    try:
        cache = decode_strict_json((old_home / "models_cache.json").read_bytes())
        models = cache["models"]
        matches = [model for model in models if isinstance(model, dict) and model.get("slug") == spec.get("model")]
        if not isinstance(cache, dict) or not isinstance(models, list) or len(matches) != 1:
            raise ValueError("missing or mismatched native model metadata")
        model = dict(matches[0])
        if not isinstance(model.get("supported_reasoning_levels"), list) or not any(
                entry.get("effort") == spec.get("effort") for entry in model["supported_reasoning_levels"]
                if isinstance(entry, dict)):
            raise ValueError("native effort metadata differs")
    except (OSError, ValueError, TypeError, KeyError, RecursionError):
        raise CodexProtocolError("no-tool-policy-unverified", "Codex native model metadata is unavailable or mismatched") from None
    model.update(shell_type="disabled", apply_patch_tool_type=None,
                 experimental_supported_tools=[], supports_search_tool=False,
                 node_repl_disabled=True, tool_mode="direct", multi_agent_version=None,
                 include_skills_usage_instructions=False, include_plugin_usage_instructions=False,
                 include_apps_usage_instructions=False)
    catalog_path = home / "no-tool-models.json"
    private_json(catalog_path, {"models": [model]})
    (home / "config.toml").write_text(_no_tool_config(catalog_path))
    os.chmod(home / "config.toml", 0o600)
    auth = old_home / "auth.json"
    if auth.is_file():
        (home / "auth.json").symlink_to(auth)
    return home
