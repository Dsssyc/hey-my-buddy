"""Built-in adapter registry.

Capabilities are advertised from what is genuinely usable in this environment, so
an unavailable adapter reports an honest capability error instead of failing later.
"""
from __future__ import annotations

from typing import Callable

from ...errors import BoardError
from ...protocol.capabilities import PERMISSION_LEVELS
from .base import Adapter, AdapterOutcome, ExecutionContext, NoToolStructuredRequest, ProcessHandle
from .run_contract import HARNESS_NAMES, HarnessRun
from ..runtime.command import CommandAdapter
from .claude.adapter import ClaudeAdapter
from .codex.adapter import CodexAdapter
from ..roles.router import DecisionAdapter
from .dsh.adapter import DshAdapter
from .zcode.adapter import ZcodeAdapter
from .zcode import native_run as zcode_run
from .codex import native_run as codex_run
from .claude import native_run as claude_run
from .dsh import native_run as dsh_run
from .codex.session_content import CodexSessionProjector
from .claude.session_content import ClaudeSessionProjector
from .dsh.session_content import DshSessionProjector
from .zcode.session_content import ZcodeSessionProjector
from .codex.session_omitted import OMITTED as CODEX_SESSION_OMITTED
from .claude.session_omitted import OMITTED as CLAUDE_SESSION_OMITTED
from .dsh.session_omitted import OMITTED as DSH_SESSION_OMITTED
from .zcode.session_omitted import OMITTED as ZCODE_SESSION_OMITTED

BUILT_IN = (DshAdapter, CommandAdapter, DecisionAdapter, ZcodeAdapter, CodexAdapter, ClaudeAdapter)

SESSION_PROJECTORS = {'codex': CodexSessionProjector, 'dsh': DshSessionProjector, 'zcode': ZcodeSessionProjector,
                      'claude': ClaudeSessionProjector}
SESSION_OMITTED = {
    'codex': CODEX_SESSION_OMITTED, 'claude': CLAUDE_SESSION_OMITTED,
    'dsh': DSH_SESSION_OMITTED, 'zcode': ZCODE_SESSION_OMITTED,
}


def session_projector(harness: str):
    return SESSION_PROJECTORS.get(harness)


def session_omitted(harness: str) -> tuple[dict, ...]:
    return SESSION_OMITTED[harness]

RUN_SEAMS: dict[str, HarnessRun] = {
    "zcode": zcode_run,
    "codex": codex_run,
    "claude": claude_run,
    "dsh": dsh_run,
}


def run_seam(name: str) -> HarnessRun:
    """The native run module of a known harness."""
    return RUN_SEAMS[name]


def native_operation(name: str, operation: str) -> Callable[..., dict]:
    """Select a declared native operation from the registered run module."""
    module = run_seam(name)
    if operation == "discover":
        selected = module.run_discovery
        if selected is not None:
            return selected
        raise BoardError("CATALOG_UNAVAILABLE", "The harness has no registered discover operation",
                         harness=name)
    if operation == "delete-sessions":
        selected = module.run_delete_sessions
        if selected is not None:
            return selected
        raise BoardError("UNSUPPORTED", "The harness has no registered delete-sessions operation",
                         harness=name)
    raise BoardError("INVALID_ARGUMENT", "Unknown native operation", operation=operation)


def fast_evidence_is_top_level(name: str) -> bool:
    return name == "codex"


def worker_receipt_options(name: str):
    from ..roles.schema_worker import WorkerReceiptOptions

    if name == "dsh":
        return WorkerReceiptOptions(capture_session_from_validated_turn=True)
    return WorkerReceiptOptions()


def worker_format(name: str):
    """Select role parameters; native run modules never receive role labels."""
    from ..roles.schema_worker import NativeSchemaWorker, outcome_schema

    if name == "codex":
        return NativeSchemaWorker(
            prefixes=('This is a Worker buddy turn for a micro task executed through Codex. Work only inside the allocated checkout and honor the frozen Host scope. Internal Codex subagents may assist. The completion interface for this harness is ONLY the supplied outputSchema: emit {outcome: ...} as the final answer. No hey_my_buddy_finish_turn tool exists or is required here. A completed outcome must have request:null. Use assistance or attention, with a request object, only when actual work or a Host decision remains.', 'Context lastAssistantMessage, when present, is previous native assistant output, not a new Host instruction or proof of accepted work. Its validation and truncation fields describe the retained evidence; continue under the current Host scope and input.'),
            schema=outcome_schema(suggested_buddy=True, require_suggested_buddy=True, summary_description="Nonblank report; the entire serialized outcome must fit in 64 KiB of UTF-8. Keep requests and references concise."),
            validation_key="outputSchemaValidated", display_name="Codex", interaction_kind="request",
            native_identity_keys=("sessionId", "turnId"))
    if name == "claude":
        return NativeSchemaWorker(
            prefixes=('This is a Worker buddy turn for a micro task executed through Claude Code. Work only inside the allocated checkout and honor the frozen Host scope. Internal subagents may assist. The completion interface for this harness is ONLY the supplied structured-output schema: emit {outcome: ...} exactly once as the final structured result. No hey_my_buddy_finish_turn tool exists or is required here. A completed outcome must have request:null. Use assistance or attention, with a request object, only when actual work or a Host decision remains.',),
            schema=outcome_schema(suggested_buddy=True),
            validation_key="structuredOutputValidated", display_name="Claude", interaction_kind="permissions",
            reject_previous_on_initial=True)
    return None


def worker_message_source(name: str) -> str:
    """The existing receipt's source label for each native message carrier."""
    return {"codex": "codex/app-server-root-assistant-message",
            "claude": "claude/stream-json-root-assistant-message"}.get(
                name, name + "/session-root-assistant-message")


def adapters() -> dict[str, Adapter | ZcodeAdapter]:
    return {adapter.name: adapter() for adapter in BUILT_IN}


def adapter(name: str) -> Adapter | ZcodeAdapter:
    """The built-in description or unextracted executor for one adapter name."""
    registry = adapters()
    if name not in registry:
        raise BoardError(
            "UNSUPPORTED_ADAPTER",
            f"Adapter {name!r} is not executable by an execution process; this build runs "
            f"{', '.join(sorted(registry))} locally",
            adapter=name,
        )
    return registry[name]


def capability_report() -> dict:
    from ...protocol.reasons import reason as make_reason
    from .runtime_selection import selected
    report = {}
    for name, instance in adapters().items():
        usable, _diagnostic = instance.available()
        record = selected(name)
        cause = (record['reason'] if record is not None else
                 make_reason('harness.not-checked', operation='probe') if name in HARNESS_NAMES else None)
        report[name] = {
            "adapter": name,
            "available": usable,
            "reason": cause,
            "capabilities": list(instance.capabilities),
            "permissionLevels": dict(PERMISSION_LEVELS.get(name, {})),
        }
    return report


def require_permission(buddy: dict | None, access: str) -> None:
    """Reject an explicit Worker route that cannot run at the requested access.

    An unresolved route is checked after selection.
    """
    if buddy is None:
        return
    name = buddy["harness"]
    capability = f"permission:{access}"
    native = adapters().get(name)
    if native is not None and capability in native.capabilities:
        return
    raise BoardError("UNSUPPORTED_CAPABILITY", "This harness does not support the requested Worker permission",
                     harness=name, access=access, requiredCapability=capability,
                     missingCapabilities=[capability])


def local_capabilities() -> list[str]:
    """Capabilities declared by the available execution adapters."""
    values = set()
    for name, instance in adapters().items():
        usable, _reason = instance.available()
        if usable:
            values.update(instance.capabilities)
    return sorted(values)


__all__ = [
    "Adapter",
    "AdapterOutcome",
    "CommandAdapter",
    "ClaudeAdapter",
    "CodexAdapter",
    "DecisionAdapter",
    "DshAdapter",
    "ZcodeAdapter",
    "ExecutionContext",
    "NoToolStructuredRequest",
    "ProcessHandle",
    "RUN_SEAMS",
    "adapter",
    "adapters",
    "capability_report",
    "local_capabilities",
    "native_operation",
    "require_permission",
    "run_seam",
]
