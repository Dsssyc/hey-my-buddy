"""Claude Code executable selection and the strict isolated execution policy.

Nothing here writes global Claude settings. The user-approved P1 default is
isolated; an explicit unsupported override is refused rather than inherited.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from .claude_protocol import OUTCOME_SCHEMA, decode_json
from .turn_io import canonical_json

#: The effort spelling used for models without native effort levels. Execution
#: omits ``--effort`` for it, so a drifting native default cannot mix two
#: configurations into one profile's evidence.
DEFAULT_EFFORT = "default"

#: Catalog entries whose ``value`` is this alias are skipped; only the canonical
#: ``resolvedModel`` identity is usable, and ``default`` resolves to a drifting
#: model over time.
DEFAULT_MODEL_ALIAS = "default"

#: P1 supports only first-party Anthropic auth (subscription login or API key).
#: The real 2.1.282 CLI reports the readback value ``firstParty`` (Host-verified
#: initialize probe, 2026-09-26); ``anthropic`` covers documented API-key hosts.
FIRST_PARTY_API_PROVIDERS = ("anthropic", "firstParty")

#: Environment overrides that would silently redirect the native CLI to a
#: third-party gateway. Execution refuses them instead of stripping them, so the
#: provider identity the account readback reports is the identity actually used.
#: Names follow the documented env-vars page (code.claude.com/docs/en/env-vars).
THIRD_PARTY_OVERRIDE_VARIABLES = (
    "ANTHROPIC_BASE_URL",
    "ANTHROPIC_BEDROCK_BASE_URL",
    "ANTHROPIC_BEDROCK_MANTLE_BASE_URL",
    "ANTHROPIC_BEDROCK_REGION_PREFIX",
    "ANTHROPIC_VERTEX_BASE_URL",
    "ANTHROPIC_AWS_BASE_URL",
    "ANTHROPIC_FOUNDRY_BASE_URL",
    "ANTHROPIC_CUSTOM_HEADERS",
    "CLAUDE_CODE_USE_BEDROCK",
    "CLAUDE_CODE_USE_VERTEX",
    "CLAUDE_CODE_USE_FOUNDRY",
    "AZURE_API_INGESTION_URL",
)

#: Model- and effort-selection overrides that would silently replace the explicit
#: ``--model``/``--effort`` flags. They never reach the native child (the child
#: environment is allowlisted below); this set documents exactly what is dropped.
MODEL_FALLBACK_VARIABLES = (
    "CLAUDE_CODE_EFFORT_LEVEL",
    "ANTHROPIC_MODEL",
    "ANTHROPIC_DEFAULT_MODEL",
    "ANTHROPIC_DEFAULT_OPUS_MODEL",
    "ANTHROPIC_DEFAULT_SONNET_MODEL",
    "ANTHROPIC_DEFAULT_HAIKU_MODEL",
    "ANTHROPIC_DEFAULT_FABLE_MODEL",
    "ANTHROPIC_SMALL_FAST_MODEL",
    "ANTHROPIC_CUSTOM_MODEL_OPTION",
)

#: The bounded environment the native child receives. Host, worker and other
#: harness credentials (``BUDDY_*``) must never leak to a model-driven process,
#: so everything not named here is dropped. First-party auth is preserved: the
#: subscription login lives under the user's config dir, and ``ANTHROPIC_API_KEY``
#: is the documented first-party key path. CA certificate path variables name
#: trust roots, not credentials. ``BUDDY_CLAUDE_FIXTURE_*`` are local
#: test-fixture controls only; they carry no credentials and never exist in
#: production environments.
NATIVE_ENVIRONMENT_ALLOWLIST = (
    "PATH", "HOME", "SHELL", "USER", "LOGNAME", "TMPDIR", "TEMP", "TMP",
    "LANG", "TZ", "TERM", "TERMINFO", "TERM_PROGRAM", "NO_COLOR", "FORCE_COLOR",
    "XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME", "XDG_STATE_HOME",
    "SSL_CERT_FILE", "SSL_CERT_DIR", "REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE",
    "NODE_EXTRA_CA_CERTS",
    "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY", "http_proxy", "https_proxy", "no_proxy",
    "CLAUDE_CONFIG_DIR",
)

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

EMPTY_MCP_CONFIG = canonical_json({"mcpServers": {}})

TOOL_DENIAL_MESSAGE = ("This Buddy Worker cannot approve extra native tool permissions; "
                       "report the boundary as attention in the structured outcome")


class ClaudeUnavailable(Exception):
    pass


#: The bounded non-interactive auth readback. ``auth status --json`` prints the
#: local account state and exits; it never opens a prompt, a login flow or a
#: model turn.
AUTH_STATUS_ARGUMENTS = ("auth", "status", "--json")

#: The bounded wait for one auth status readback. The probe inspects local login
#: state only; a CLI that cannot answer within the wait fails closed.
AUTH_STATUS_TIMEOUT_SECONDS = 10.0

#: The auth status stdout bound. A real status object is far smaller; larger
#: output is refused unparsed so no unbounded payload is ever copied onward.
AUTH_STATUS_MAX_BYTES = 1 << 16


def cli_command(environment: dict | None = None) -> list[str]:
    env = os.environ if environment is None else environment
    if not (os.environ.get("BUDDY_DEV_SOURCE") == "1" and env.get("BUDDY_CLAUDE_CLI")):
        from ..harness_runtime import command_for
        from ..errors import BoardError
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


def third_party_overrides(environment: dict | None = None) -> list[str]:
    """The names of configured third-party provider overrides; never their values."""
    env = os.environ if environment is None else environment
    return [name for name in THIRD_PARTY_OVERRIDE_VARIABLES if env.get(name)]


def account_problem(account: object) -> str | None:
    """Reject any initialize account readback that is not first-party Anthropic.

    A first-party account whose ``tokenSource`` is absent or null also fails
    here; ``token_source_missing`` marks it as the one shape the runner may
    re-verify through one bounded auth status readback before refusing.
    """
    if not isinstance(account, dict):
        return "Claude Code returned no account identity in initialize"
    provider = account.get("apiProvider")
    if provider not in FIRST_PARTY_API_PROVIDERS:
        return "Claude Code account is not first-party Anthropic; Bedrock, Vertex and Foundry are unsupported in P1"
    token_source = account.get("tokenSource")
    if not isinstance(token_source, str) or not token_source or token_source == "none":
        return "Claude Code reports no first-party login (tokenSource none)"
    return None


def token_source_missing(account: object) -> bool:
    """The only fallback-eligible shape: first-party provider, tokenSource absent or null.

    The real CLI reports this after a claude.ai login (Host-verified 2.1.283,
    2026-09-26). An explicit ``"none"`` is a decision rather than an absence
    and stays refused without any readback, as does every other non-string,
    empty or non-first-party shape.
    """
    return (isinstance(account, dict)
            and account.get("apiProvider") in FIRST_PARTY_API_PROVIDERS
            and account.get("tokenSource") is None)


def auth_status_problem(payload: object) -> str | None:
    """Accept only a parsed status with loggedIn exactly true and a first-party provider.

    The provider must stay inside the same first-party set initialize already
    reported, so the readback corroborates the initialize account identity;
    anything unknown, logged out or third-party fails closed.
    """
    if not isinstance(payload, dict):
        return "Claude auth status returned no readable status object"
    if payload.get("loggedIn") is not True:
        return "Claude auth status does not report an active first-party login"
    if payload.get("apiProvider") not in FIRST_PARTY_API_PROVIDERS:
        return "Claude auth status is not first-party; Bedrock, Vertex and Foundry are unsupported in P1"
    return None


def read_auth_status(command: list[str], *, cwd: str, environment: dict,
                     timeout: float | None = None) -> str | None:
    """Prove a missing-tokenSource account through the same native CLI only.

    One bounded ``auth status --json`` child with the native execution's exact
    resolved executable, allowlisted environment and cwd; no prompt, no login
    attempt, no model call. Returns a bounded refusal reason, or None when the
    readback proves an explicit first-party login. Raw stdout, stderr and every
    parsed account field stay inside this function.
    """
    overrides = third_party_overrides(environment)
    if overrides:
        return "Claude refuses third-party provider overrides: " + ", ".join(overrides)
    try:
        completed = subprocess.run([*command, *AUTH_STATUS_ARGUMENTS], cwd=cwd, env=environment,
                                   capture_output=True,
                                   timeout=AUTH_STATUS_TIMEOUT_SECONDS if timeout is None else timeout)
    except subprocess.TimeoutExpired:
        return "the Claude auth status readback exceeded its time bound"
    except OSError:
        return "the Claude auth status readback could not start"
    if completed.returncode != 0:
        return "the Claude auth status readback exited nonzero"
    if len(completed.stdout) > AUTH_STATUS_MAX_BYTES:
        return "the Claude auth status readback exceeded its output bound"
    try:
        payload = decode_json(completed.stdout)
    except (ValueError, RecursionError):
        return "the Claude auth status readback was not valid JSON"
    return auth_status_problem(payload)


def settings_policy(environment: dict | None = None) -> str | None:
    env = os.environ if environment is None else environment
    value = env.get("BUDDY_CLAUDE_SETTINGS_POLICY", "isolated")
    return value if value == "isolated" else None


def sandbox_settings() -> dict:
    """Documented native sandbox settings: Bash sandboxed, registries only.

    Key names follow the primary sandboxing documentation
    (code.claude.com/docs/en/sandboxing): the domain list is
    ``network.allowedDomains`` with plain string entries, and
    ``strictAllowlist``/``allowUnsandboxedCommands: false`` deny any fallback
    out of the sandbox.
    """
    return {
        "sandbox": {
            "enabled": True,
            "failIfUnavailable": True,
            "autoAllowBashIfSandboxed": True,
            "allowUnsandboxedCommands": False,
            "excludedCommands": [],
            "network": {
                "strictAllowlist": True,
                "allowedDomains": list(PACKAGE_REGISTRY_DOMAINS),
            },
        }
    }


def base_args() -> list[str]:
    return ["-p", "--input-format", "stream-json", "--output-format", "stream-json", "--verbose"]


def discovery_args() -> list[str]:
    """Initialize-only invocation: no user message, no session persistence."""
    return [*base_args(), "--safe-mode", "--no-session-persistence", "--strict-mcp-config",
            "--mcp-config", EMPTY_MCP_CONFIG, "--setting-sources", ""]


def execution_args(*, session_id: str, model: str, effort: str, settings_path: str, read_only: bool, output_schema: dict | None = None) -> list[str]:
    """The strict isolated execution invocation; never ``--resume``, never global settings."""
    args = [*base_args(),
            "--safe-mode", "--strict-mcp-config", "--mcp-config", EMPTY_MCP_CONFIG,
            "--setting-sources", "",
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
    if output_schema is not None:
        # --tools does not limit MCP; keep the native deny layer explicit.
        index = args.index("--disallowedTools") + 1
        args[index] += ",mcp__*,WebFetch,WebSearch,Agent,Task"
    args += ["--json-schema", canonical_json(output_schema if output_schema is not None else OUTCOME_SCHEMA)]
    return args


def native_environment(environment: dict) -> dict:
    """Build the bounded native child environment from the allowlist.

    Provider routing overrides are rejected by name before this runs (never
    silently stripped), first-party auth variables survive, and every host,
    worker, agent-credential or model/effort fallback variable is dropped so a
    model-driven process cannot read Buddy credentials or silently reselect a
    configuration.
    """
    from ..harness_discovery import native_environment as clean
    result = clean(environment, command=cli_command(environment))
    if os.environ.get("BUDDY_DEV_SOURCE") == "1":
        for key in ("BUDDY_CLAUDE_FIXTURE_AUTH_STATUS", "BUDDY_CLAUDE_FIXTURE_CASE", "BUDDY_CLAUDE_FIXTURE_STATE"):
            if key in environment:
                result[key] = environment[key]
    return result
