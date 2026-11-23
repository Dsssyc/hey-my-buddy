"""Codex capability description; execution belongs to the registered run."""
from __future__ import annotations

from pathlib import Path

from ....protocol.capabilities import adapter_capabilities
from ....protocol.harness_declarations import harness_declaration
from . import native_run
from .config import CodexUnavailable, cli_command


class CodexAdapter:
    """Native availability and facts, with no inherited execution entries."""

    name = "codex"
    # The shared static facts are read from the one protocol declaration, so
    # this description carries no second copy that can drift.
    declaration = harness_declaration(name)
    native_resume = declaration.native_resume
    interrupted_resume = declaration.interrupted_resume
    native_resume_permission_change = declaration.native_resume_permission_change
    same_session_corrections = declaration.same_session_corrections
    model_discovery = declaration.model_discovery

    @property
    def capabilities(self):
        return adapter_capabilities(self.name)

    def available(self) -> tuple[bool, str | None]:
        from ....harness_selection import selected
        record = selected("codex")
        if record is not None:
            return record.get('status') == 'ready', (record.get('reason') or {}).get('code')
        try:
            cli_command()
            return True, None
        except CodexUnavailable as error:
            return False, str(error)

    def discovery_available(self) -> tuple[bool, str | None]:
        return self.available()

    validate_turn_provenance = staticmethod(native_run.validate_turn_provenance)

    def discover_models(self, *, state_dir: Path) -> dict:
        from ...roles.run_execution import discover_models
        return discover_models(self.name, state_dir=state_dir)

    def delete_sessions(self, session_ids: list[str], *, state_dir: Path) -> dict[str, str]:
        from ...roles.run_execution import delete_sessions
        return delete_sessions(self.name, session_ids, state_dir=state_dir)
