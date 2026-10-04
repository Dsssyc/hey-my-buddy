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

BUILT_IN = (DshAdapter, CommandAdapter, DecisionAdapter, ZcodeAdapter, CodexAdapter, ClaudeAdapter)

#: The extracted harness run modules, registered by the ADR-025 step that
#: extracts each harness (steps two to four). Registration is the only way into
#: the new seam and is explicit: only an object whose ``run`` and ``discover``
#: are callable may enter under a harness name, so no command adapter and no
#: string-shaped stand-in is ever mistaken for a run. Registering a harness
#: commits the same step to deleting its legacy structured entries; the role
#: seam refuses to route a registered name through them, so a half-finished
#: switch fails loudly instead of silently keeping two execution paths.
RUN_SEAMS: dict[str, HarnessRun] = {}


def register_run_seam(name: str, module: HarnessRun) -> None:
    """Register one extracted harness's run module under its harness name.

    The capability check is the honest local one: ``run`` and ``discover`` must
    be callable on the registered object, and nothing more — no attribute-shape
    trust, no static analysis of anyone's package.
    """
    if name not in HARNESS_NAMES:
        raise BoardError("INVALID_ARGUMENT", f"{name!r} is not a harness name", adapter=name)
    if name in RUN_SEAMS:
        raise BoardError("CONFLICT", f"{name} already has a registered run seam", adapter=name)
    for method in ("run", "discover"):
        if not callable(getattr(module, method, None)):
            raise BoardError("INVALID_ARGUMENT",
                             f"a run seam must carry a callable {method}, and nothing about "
                             "the registration proves more than that", adapter=name)
    RUN_SEAMS[name] = module


def run_seam(name: str) -> HarnessRun | None:
    """The registered run module of one harness, or None while it is unextracted."""
    return RUN_SEAMS.get(name)

#: ``external`` is a first-class adapter whose execution is owned by the caller's
#: own agent, not by a built-in worker. That agent claims the task through the
#: public C-Two contract, does the work itself and reports the result. No argv, no
#: shell and no dsh pretence are involved, and a local built-in worker does not
#: advertise this capability.
EXTERNAL_CAPABILITIES = ("external", "artifacts", "task-text")


def adapters() -> dict[str, Adapter]:
    return {adapter.name: adapter() for adapter in BUILT_IN}


def adapter(name: str) -> Adapter:
    """The built-in executor for one adapter name.

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
    "local_capabilities",
    "register_run_seam",
    "run_seam",
    "supported_capabilities",
]
