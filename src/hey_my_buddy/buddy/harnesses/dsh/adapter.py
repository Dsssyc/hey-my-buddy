"""DSH capability description; execution belongs to the registered run.

The native execution body is :mod:`.native_run` through the shared run seam;
this description carries only what the registry, the Router preparation and the
Worker runtime consume: the declared capabilities, the local eligibility, the
availability of the installed command (the service selection, or the same
bounded discovery the run seam uses when nothing is bound), the turn provenance
validator of the registered run, and discovery routed through the seam. The old
prepare/start/collect/cancel carrier and the direct-LLM no-tool controller are
deleted without a compatibility layer (ADR-025 decisions 1 and 11).
"""
from __future__ import annotations

from ....errors import BoardError
from . import native_run


class DshAdapter:
    """Native availability and facts, with no inherited execution entries."""

    name = "dsh"
    capabilities = ("dsh", "inquiry", "workspace", "cancel", "artifacts", "deadline")
    native_resume = False
    model_discovery = True
    no_tool_structured = True
    read_only_structured = False
    read_only_structured_resume = False

    def local_read_only_check(self) -> dict:
        return {"eligible": False, "reasonCode": "readonly-worker-carrier-unimplemented",
                "reason": "DSH review on the Worker carrier is not implemented; its separate read-only channel was removed from stage 2",
                "systemSandbox": False, "sameAttemptContinuation": self.read_only_structured_resume}

    def available(self) -> tuple[bool, str | None]:
        """A ready installed dsh command is this harness's only carrier.

        A service-selected record decides as pinned. Without one, the same
        bounded discovery that ``command_for`` falls back to refreshes the
        selection and its verdict decides — the pre-existing unbound
        confirmation path, minus the retired Node runner requirement. A bound
        but unhealthy selection is still refused; nothing here sends a prompt
        or a model call.
        """
        from ..runtime_selection import selected
        record = selected("dsh")
        if record is not None:
            if record.get("status") == "ready" and record.get("command"):
                return True, None
            return False, record.get("remedy") or record.get("reasonCode")
        from ..discovery import discover
        detected = discover("dsh")
        if not detected["available"]:
            return False, detected.get("remedy") or detected.get("reasonCode")
        return True, None

    def discovery_available(self) -> tuple[bool, str | None]:
        return self.available()

    validate_turn_provenance = staticmethod(native_run.validate_turn_provenance)

    def discover_models(self) -> dict:
        from ...roles.run_execution import discover_models
        return discover_models(self.name)


__all__ = ["DshAdapter"]
