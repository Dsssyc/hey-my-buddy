"""DSH capability description; execution belongs to the registered run.

The native execution body is :mod:`.native_run` through the shared run seam;
this description carries only what the registry, the Router preparation and the
execution process consume: the declared capabilities, the local eligibility, the
availability of the installed command (the service selection, or the same
bounded discovery the run seam uses when nothing is bound), the turn provenance
validator of the registered run, and discovery routed through the seam. The old
prepare/start/collect/cancel carrier and the direct-LLM no-tool controller are
deleted without a compatibility layer (ADR-025 decisions 1 and 11).
"""
from __future__ import annotations

from pathlib import Path

from ....errors import BoardError
from ....protocol.capabilities import adapter_capabilities
from ....protocol.harness_declarations import harness_declaration
from . import native_run


class DshAdapter:
    """Native availability and facts, with no inherited execution entries."""

    name = "dsh"
    # The shared static facts are read from the one protocol declaration, so
    # this description carries no second copy that can drift.
    declaration = harness_declaration(name)
    native_resume = declaration.native_resume
    interrupted_resume = declaration.interrupted_resume
    same_session_corrections = declaration.same_session_corrections
    native_resume_permission_change = declaration.native_resume_permission_change
    model_discovery = declaration.model_discovery

    @property
    def capabilities(self):
        return adapter_capabilities(self.name)

    def available(self) -> tuple[bool, str | None]:
        """A ready installed dsh command is this harness's only carrier.

        A service-selected record decides as pinned. Without one, the same
        bounded discovery that ``launch_for`` falls back to refreshes the
        selection and its verdict decides — the pre-existing unbound
        confirmation path, minus the retired Node runner requirement. A bound
        but unhealthy selection is still refused; nothing here sends a prompt
        or a model call.
        """
        from ....harness_selection import environment_for, selected
        record = selected("dsh")
        if record is not None:
            if record.get("status") == "ready" and record.get("command"):
                return True, None
            return False, (record.get("reason") or {}).get("code")
        from ..discovery import discover
        detected = discover("dsh", state_dir=Path(environment_for()["HMB_STATE_DIR"]))
        if not detected["available"]:
            return False, (detected.get("reason") or {}).get("code")
        return True, None

    def discovery_available(self) -> tuple[bool, str | None]:
        return self.available()

    validate_turn_provenance = staticmethod(native_run.validate_turn_provenance)

    def discover_models(self, *, state_dir: Path) -> dict:
        from ...roles.run_execution import discover_models
        return discover_models(self.name, state_dir=state_dir)


__all__ = ["DshAdapter"]
