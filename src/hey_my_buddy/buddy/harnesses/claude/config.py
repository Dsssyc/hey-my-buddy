"""Claude Code executable selection and native permission arguments."""
from __future__ import annotations

import os
import shutil
from pathlib import Path

from ...roles.turn_io import canonical_json

#: The effort spelling used for models without native effort levels. Execution
#: omits ``--effort`` for it, so a drifting native default cannot mix two
#: configurations into one profile's evidence.
DEFAULT_EFFORT = "default"

#: Catalog entries whose ``value`` is this alias are skipped; only the canonical
#: ``resolvedModel`` identity is usable, and ``default`` resolves to a drifting
#: model over time.
DEFAULT_MODEL_ALIAS = "default"

#: The only outbound network the sandbox allows: package-manager registries, so
#: a fresh worktree without ignored ``node_modules`` can still install deps.
PACKAGE_REGISTRY_DOMAINS = (
    "registry.npmjs.org",
    "registry.yarnpkg.com",
    "pypi.org",
    "files.pythonhosted.org",
    "crates.io",
    "index.crates.io",
    "static.crates.io",
)

#: Explicit built-in tools under ``--restricted``. File and Bash tools confine
#: file access to the allocated cwd; subagents stay available through both the
#: current ``Agent`` tool and the older ``Task`` spelling, and their models are
#: recorded only as an observed set. ``--json-schema`` contributes its own
#: StructuredOutput tool by itself.
WRITABLE_TOOLS = ("Agent", "Bash", "Edit", "Glob", "Grep", "LS", "MultiEdit",
                  "NotebookEdit", "Read", "Task", "TodoWrite", "Write")

#: A read-only workspace must refuse writes, and sandboxed Bash can still write
#: inside the cwd, so a read-only turn exposes no Bash and no write tools at all.
READONLY_TOOLS = ("Glob", "Grep", "LS", "Read")

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


def cli_command(environment: dict | None = None) -> list[str]:
    env = os.environ if environment is None else environment
    if not (os.environ.get("BUDDY_DEV_SOURCE") == "1" and env.get("BUDDY_CLAUDE_CLI")):
        from ..runtime_selection import command_for
        from ....errors import BoardError
        try:
            return command_for("claude", env)
        except BoardError as error:
            raise ClaudeUnavailable(error.message) from None
    value = env.get("BUDDY_CLAUDE_CLI") or shutil.which("claude", path=env.get("PATH"))
    if not value:
        raise ClaudeUnavailable("Claude Code CLI is missing; configure BUDDY_CLAUDE_CLI")
    path = Path(value).expanduser().resolve()
    if not path.is_file() or not os.access(path, os.X_OK):
        raise ClaudeUnavailable("the configured Claude Code CLI is not executable")
    return [str(path)]


def sandbox_settings(network_allowed_domains=None) -> dict:
    """Documented native sandbox settings: Bash sandboxed, registries only.

    Key names follow the primary sandboxing documentation
    (code.claude.com/docs/en/sandboxing): the domain list is
    ``network.allowedDomains`` with plain string entries, and
    ``strictAllowlist``/``allowUnsandboxedCommands: false`` deny any fallback
    out of the sandbox. ``network_allowed_domains`` is None for the native
    default allowlist (the package registries, the only value the legacy
    paths ever wrote); an explicit sequence — empty included — is written
    verbatim, so an offline run is a caller decision, never a scope
    inference.
    """
    domains = list(PACKAGE_REGISTRY_DOMAINS) if network_allowed_domains is None \
        else list(network_allowed_domains)
    return {
        "sandbox": {
            "enabled": True,
            "failIfUnavailable": True,
            "autoAllowBashIfSandboxed": True,
            "allowUnsandboxedCommands": False,
            "excludedCommands": [],
            "network": {
                "strictAllowlist": True,
                "allowedDomains": domains,
            },
        }
    }


def base_args() -> list[str]:
    return ["-p", "--input-format", "stream-json", "--output-format", "stream-json", "--verbose"]


def discovery_args() -> list[str]:
    """Initialize-only invocation: no user message, no session persistence."""
    return [*base_args(), "--no-session-persistence"]


def execution_args(*, session_id: str, model: str, effort: str, settings_path: str, read_only: bool,
                   output_schema: dict, additional_denied_tools=()) -> list[str]:
    """The native permission invocation; user configuration sources stay enabled.

    ``output_schema`` is the run's own ending contract and is always explicit:
    the governed turn passes the shared worker schema, the read-only call the
    caller's answer schema. ``additional_denied_tools`` is the caller's
    explicit sequence of extra session-wide denials — empty by default;
    nothing is ever derived from the schema's presence or the tool scope.
    """
    args = [*base_args(),
            "--no-session-persistence",
            "--restricted",
            "--tools", ",".join(READONLY_TOOLS if read_only else WRITABLE_TOOLS),
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
        # plus --restricted keeps edits confined to the allocated checkout;
        # Bash still runs only in the sandbox with no unsandboxed fallback, and
        # bypassPermissions is never used.
        args += ["--permission-mode", "acceptEdits"]
    if effort != DEFAULT_EFFORT:
        args += ["--effort", effort]
    if additional_denied_tools:
        values = list(additional_denied_tools)
        if "--disallowedTools" in args:
            index = args.index("--disallowedTools") + 1
            args[index] += "," + ",".join(values)
        else:
            args += ["--disallowedTools", ",".join(values)]
    args += ["--json-schema", canonical_json(output_schema)]
    return args


def native_environment(environment: dict) -> dict:
    """Use the shared service allowlist; Claude reads its own configuration files."""
    from ..discovery import native_environment as clean
    result = clean(environment, command=cli_command(environment), adapter='claude')
    if os.environ.get("BUDDY_DEV_SOURCE") == "1":
        for key in ("BUDDY_CLAUDE_FIXTURE_CASE", "BUDDY_CLAUDE_FIXTURE_STATE"):
            if key in environment:
                result[key] = environment[key]
    return result
