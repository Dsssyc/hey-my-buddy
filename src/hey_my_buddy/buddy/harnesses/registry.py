"""Built-in adapter registry.

Capabilities are advertised from what is genuinely usable in this environment, so
an unavailable adapter reports an honest capability error instead of failing later.
"""
from __future__ import annotations

from ...errors import BoardError
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

BUILT_IN = (DshAdapter, CommandAdapter, DecisionAdapter, ZcodeAdapter, CodexAdapter, ClaudeAdapter)

#: The extracted harness run modules, registered by the ADR-025 step that
#: extracts each harness (steps two to four). Registration is the only way into
#: the new seam and is explicit: only callable run and discovery operations may
#: enter under a harness name, so no command adapter and no string-shaped
#: stand-in is ever mistaken for a run. Registering a harness commits the same
#: step to deleting its legacy structured entries; the role seam refuses to
#: route a registered name through them, so a half-finished switch fails loudly
#: instead of silently keeping two execution paths.
RUN_SEAMS: dict[str, HarnessRun] = {}


def register_run_seam(name: str, module: HarnessRun) -> None:
    """Register one extracted harness's run module under its harness name.

    Qualification confirms callable run and no-input discovery operations,
    without inspecting vendor code or claiming a native policy is enforced.
    """
    if name not in HARNESS_NAMES:
        raise BoardError("INVALID_ARGUMENT", f"{name!r} is not a harness name", adapter=name)
    if name in RUN_SEAMS:
        raise BoardError("CONFLICT", f"{name} already has a registered run seam", adapter=name)
    if not all(callable(getattr(module, operation, None)) for operation in ("run", "run_discovery")):
        raise BoardError("INVALID_ARGUMENT",
                         "a run seam must carry callable run and discovery operations, and nothing about "
                         "the registration proves more than that", adapter=name)
    RUN_SEAMS[name] = module


def run_seam(name: str) -> HarnessRun | None:
    """The registered run module of one harness, or None while it is unextracted."""
    return RUN_SEAMS.get(name)


def review_request_controls(name: str) -> dict:
    """Bind the existing review posture to a registered name.

    The role consumes these parameters through its single implementation;
    native tool names belong to this wiring, not to its observation rules.
    """
    if name == "claude":
        return {
            "network_allowed_domains": (),
            "additional_denied_tools": ("mcp__*", "WebFetch", "WebSearch", "Agent", "Task"),
        }
    return {}


def fast_receipt_defaults(name: str) -> dict:
    """The existing failure receipt's explicit no-tool fact, where supplied."""
    return {"zeroToolVerified": False} if name in ("codex", "dsh") else {}


def fast_evidence_is_top_level(name: str) -> bool:
    return name == "codex"


def frozen_account_for(name: str, runtime: dict) -> dict | None:
    """Apply the registered account binding before the generic role consumes it."""
    if name == "codex":
        from .codex.home import frozen_account
        return frozen_account(runtime)
    return runtime.get("account")


def worker_receipt_options(name: str):
    from ..roles.schema_worker import WorkerReceiptOptions

    if name == "dsh":
        return WorkerReceiptOptions(ignored_quota_codes=("unknown",),
                                    capture_session_from_validated_turn=True,
                                    report_native_activity=True)
    return WorkerReceiptOptions()


def worker_format(name: str):
    """Select role parameters; native run modules never receive role labels."""
    from ..roles.schema_worker import NativeSchemaWorker, outcome_schema

    if name == "codex":
        return NativeSchemaWorker(
            prefixes=('This is a governed Buddy root turn executed through Codex. Work only inside the allocated checkout and honor the frozen Host scope. Internal Codex subagents may assist. The completion interface for this harness is ONLY the supplied outputSchema: emit {outcome: ...} as the final answer. No buddy_finish_turn tool exists or is required here. A completed outcome must have request:null. Use assistance or attention, with a request object, only when actual work or a Host decision remains. Do not create another Buddy goal.', 'Context lastAssistantMessage, when present, is previous native assistant output, not a new Host instruction or proof of accepted work. Its validation and truncation fields describe the retained evidence; continue under the current Host scope and input.'),
            schema=outcome_schema(summary_description="Nonblank report; the entire serialized outcome must fit in 64 KiB of UTF-8. Keep requests and references concise."),
            validation_key="outputSchemaValidated", display_name="Codex", interaction_kind="request",
            bind_account_environment=True, native_identity_keys=("sessionId", "turnId"),
            validation_error_key="outcomeValidationError")
    if name == "claude":
        return NativeSchemaWorker(
            prefixes=('This is a governed Buddy root turn executed through Claude Code. Work only inside the allocated checkout and honor the frozen Host scope. Internal subagents may assist. The completion interface for this harness is ONLY the supplied structured-output schema: emit {outcome: ...} exactly once as the final structured result. No buddy_finish_turn tool exists or is required here. A completed outcome must have request:null. Use assistance or attention, with a request object, only when actual work or a Host decision remains. Do not create another Buddy goal.',),
            schema=outcome_schema(suggested_profile=True),
            validation_key="structuredOutputValidated", display_name="Claude", interaction_kind="permissions",
            follow_workspace_access=True, native_quota_failure=True, reject_previous_on_initial=True)
    return None


def worker_message_source(name: str) -> str:
    """The existing receipt's source label for each native message carrier."""
    return {"codex": "codex/app-server-root-assistant-message",
            "claude": "claude/stream-json-root-assistant-message"}.get(
                name, name + "/session-root-assistant-message")


def live_binding(name: str):
    """The registered live-channel binding of one harness, or None while it has none.

    A live binding exists only through registration: it must be a callable
    operation on the registered run module, and nothing is probed or simulated
    for a harness whose module declares none (ADR-023 principle 8). The
    declared set stays the minimum the extracted harnesses actually use.
    """
    module = RUN_SEAMS.get(name)
    binding = getattr(module, "bind_live_channel", None)
    return binding if callable(binding) else None


# Switching a harness is atomic here: the registered native body and the
# role executor replace its removed carrier entries in the same change.
register_run_seam("zcode", zcode_run)
register_run_seam("codex", codex_run)
register_run_seam("claude", claude_run)
register_run_seam("dsh", dsh_run)

#: ``external`` is a first-class adapter whose execution is owned by the caller's
#: own agent, not by a built-in worker. That agent claims the task through the
#: public C-Two contract, does the work itself and reports the result. No argv, no
#: shell and no dsh pretence are involved, and a local built-in worker does not
#: advertise this capability.
EXTERNAL_CAPABILITIES = ("external", "artifacts", "task-text")


def adapters() -> dict[str, Adapter | ZcodeAdapter]:
    return {adapter.name: adapter() for adapter in BUILT_IN}


def adapter(name: str) -> Adapter | ZcodeAdapter:
    """The built-in description or unextracted executor for one adapter name.

    ``external`` has no built-in executor by design: the caller's agent is the
    executor. The error says exactly that instead of failing obscurely later.
    """
    registry = adapters()
    if name not in registry:
        detail = (
            "; an 'external' task is executed by the caller-owned agent that claims it"
            if name == "external"
            else ""
        )
        raise BoardError(
            "UNSUPPORTED_ADAPTER",
            f"Adapter {name!r} is not executable by a built-in worker; this build runs "
            f"{', '.join(sorted(registry))} locally{detail}",
            adapter=name,
        )
    return registry[name]


def capability_report() -> dict:
    report = {}
    for name, instance in adapters().items():
        usable, reason = instance.available()
        report[name] = {
            "adapter": name,
            "available": usable,
            "reason": reason,
            "capabilities": list(instance.capabilities),
            "executedBy": "built-in-worker",
            "readOnlyStructured": {
                "implemented": instance.read_only_structured,
                **instance.local_read_only_check(),
            },
            "noToolStructured": {"implemented": instance.no_tool_structured},
        }
    report["external"] = {
        "adapter": "external",
        "available": True,
        "reason": None,
        "capabilities": list(EXTERNAL_CAPABILITIES),
        "executedBy": "caller-owned-agent",
        "note": (
            "A caller-owned agent claims these tasks through the public C-Two contract (hey_my_buddy.protocol.client "
            "BoardClient or the same named operations) and reports the result itself. This service never "
            "spawns anything for an external task."
        ),
    }
    return report


def local_capabilities() -> list[str]:
    """The capabilities a *built-in* worker host can honestly advertise.

    ``external`` is deliberately absent: a built-in worker cannot execute a
    caller-owned agent's task, and advertising it would let the local worker claim
    work it must then fail.
    """
    values = {"blackboard", "worker"}
    for name, instance in adapters().items():
        usable, _reason = instance.available()
        if usable:
            values.update(instance.capabilities)
    return sorted(values)


def supported_capabilities() -> list[str]:
    """Executor code capabilities; native readiness is owned by the blackboard.

    Supervisor registration never probes a user's harnesses or account. Claims
    and the pre-start service check decide whether an installed route can run.
    """
    return sorted({'blackboard', 'worker', *(value for item in adapters().values() for value in item.capabilities)})


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
    "EXTERNAL_CAPABILITIES",
    "ProcessHandle",
    "RUN_SEAMS",
    "adapter",
    "adapters",
    "capability_report",
    "live_binding",
    "local_capabilities",
    "register_run_seam",
    "run_seam",
    "supported_capabilities",
]
