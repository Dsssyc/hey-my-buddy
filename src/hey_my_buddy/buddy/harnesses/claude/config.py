"""Claude Code executable selection and native permission arguments."""
from __future__ import annotations

import os

from ...roles.turn_io import canonical_json

#: The effort spelling used for models without native effort levels. Execution
#: omits ``--effort`` for it, so a drifting native default cannot mix two
#: buddy identities into one buddy's evidence.
DEFAULT_EFFORT = "default"

#: Catalog entries whose ``value`` is this alias are skipped; only the canonical
#: ``resolvedModel`` identity is usable, and ``default`` resolves to a drifting
#: model over time.
DEFAULT_MODEL_ALIAS = "default"

#: Explicit built-in tools; native permissions govern file access and the
#: sandbox governs Bash. Skills and subagents stay available through Skill and the
#: current ``Agent`` tool and the older ``Task`` spelling, and their models are
#: recorded only as an observed set. ``--json-schema`` contributes its own
#: StructuredOutput tool by itself.
WRITABLE_TOOLS = ("Agent", "Bash", "Edit", "Glob", "Grep", "LS", "MultiEdit",
                  "NotebookEdit", "Read", "Skill", "Task", "TodoWrite", "Write")

#: A read-only workspace must refuse writes, and sandboxed Bash can still write
#: inside the cwd, so a read-only turn exposes no Bash and no write tools at all.
READONLY_TOOLS = ("Glob", "Grep", "LS", "Read", "Skill")

#: Session-wide hard denials for read-only turns. The disallow list binds native
#: child agents too, so read-only is enforced by the permission layer rather
#: than by model promises.
DENIED_WRITE_TOOLS = ("Bash", "Edit", "MultiEdit", "NotebookEdit", "Write")

TOOL_DENIAL_MESSAGE = ("This Buddy Worker cannot approve extra native tool permissions; "
                       "report the boundary as attention in the structured outcome")


class ClaudeUnavailable(Exception):
    pass


def provider_from_account(account: object) -> str:
    """Claude's firstParty API label names the existing Anthropic provider."""
    from ....protocol.billing import native_provider

    provider = native_provider(account, "apiProvider")
    return "anthropic" if provider == "firstParty" else provider


def launch(environment: dict | None = None) -> tuple[list[str], dict]:
    """Resolve the native argv and its environment from one selected entry."""
    from ..runtime_selection import launch_with_environment
    from ..discovery import EntryUnavailable
    from ....errors import BoardError

    try:
        command, result = launch_with_environment("claude", environment)
    except BoardError as error:
        raise ClaudeUnavailable(error.message) from None
    except EntryUnavailable as error:
        failure = ClaudeUnavailable(str(error))
        failure.reason = error.reason
        raise failure from None
    if environment is not None and os.environ.get("HMB_DEV_SOURCE") == "1":
        for key in ("HMB_CLAUDE_FIXTURE_CASE", "HMB_CLAUDE_FIXTURE_STATE"):
            if key in environment:
                result[key] = environment[key]
    return command, result


def cli_command(environment: dict | None = None) -> list[str]:
    return launch(environment)[0]


def base_args() -> list[str]:
    return ["-p", "--input-format", "stream-json", "--output-format", "stream-json", "--verbose"]


def permission_sandbox_settings(level: str) -> dict:
    """Express this run's file permission; read and write both allow networking."""
    if level == "full":
        return {"sandbox": {"enabled": False}}
    settings = {"sandbox": {
        "enabled": True, "failIfUnavailable": True,
        "autoAllowBashIfSandboxed": True, "allowUnsandboxedCommands": False,
        "excludedCommands": [],
        "network": {"strictAllowlist": False, "allowedDomains": ["*"],
                    "allowLocalBinding": True},
    }}
    if level == "write":
        settings["permissions"] = {"allow": ["Bash"]}
    return settings


def discovery_args() -> list[str]:
    """Initialize-only invocation: no user message, no session persistence."""
    return [*base_args(), "--no-session-persistence"]


def execution_args(*, session_id: str, model: str, effort: str, settings_path: str, read_only: bool,
                   output_schema: dict, full_access: bool = False,
                   mcp_config_path: str | None = None, inquiry_tools=()) -> list[str]:
    """The native permission invocation; user configuration sources stay enabled.

    ``output_schema`` is the run's own ending contract and is always explicit:
    the micro task turn passes the shared worker schema, the read-only call the
    caller's answer schema. Permission alone selects the native tool posture.
    """
    args = [*base_args(),
            "--no-session-persistence",
            "--tools", ",".join([*(READONLY_TOOLS if read_only else WRITABLE_TOOLS),
                                 *(("ToolSearch",) if mcp_config_path else ())]),
            "--permission-prompt-tool", "stdio",
            "--settings", settings_path,
            "--session-id", session_id,
            "--model", model]
    if read_only:
        # Mode stays default so any write ask is a real denial path, and the
        # session-wide disallow list hard-denies writes for child agents too.
        args += ["--permission-mode", "default", "--disallowedTools", ",".join(DENIED_WRITE_TOOLS)]
    else:
        # Ordinary in-worktree edits must not become manual approvals (default
        # mode would deny every edit and always end as attention). acceptEdits
        # auto-approves edits in native working directories; user allow rules
        # can extend access. Any remaining request is denied by the controller.
        # Full access skips native approvals with sandbox.enabled false;
        # Checkout-write allows Bash without prompting inside the sandbox,
        # with no unsandboxed fallback. Requests that still reach the
        # controller are denied.
        # This CLI mode overrides the user's permissions.defaultMode.
        args += ["--permission-mode", "bypassPermissions" if full_access else "acceptEdits"]
    if effort != DEFAULT_EFFORT:
        args += ["--effort", effort]
    if mcp_config_path is not None:
        args += ["--mcp-config", mcp_config_path, "--allowedTools", ",".join(inquiry_tools)]
    args += ["--json-schema", canonical_json(output_schema)]
    return args
