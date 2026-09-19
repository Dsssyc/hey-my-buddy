"""Built-in adapter registry.

Capabilities are advertised from what is genuinely usable in this environment, so
an unavailable adapter reports an honest capability error instead of failing later.
"""
from __future__ import annotations

from .base import Adapter, AdapterOutcome, ExecutionContext, ProcessHandle
from .command import CommandAdapter
from .dsh import DshAdapter

BUILT_IN = (DshAdapter, CommandAdapter)

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
        from ..errors import BoardError

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
        }
    report["external"] = {
        "adapter": "external",
        "available": True,
        "reason": None,
        "capabilities": list(EXTERNAL_CAPABILITIES),
        "executedBy": "caller-owned-agent",
        "note": (
            "A caller-owned agent claims these tasks through the public C-Two contract (buddy.client "
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


__all__ = [
    "Adapter",
    "AdapterOutcome",
    "CommandAdapter",
    "DshAdapter",
    "ExecutionContext",
    "EXTERNAL_CAPABILITIES",
    "ProcessHandle",
    "adapter",
    "adapters",
    "capability_report",
    "local_capabilities",
]
