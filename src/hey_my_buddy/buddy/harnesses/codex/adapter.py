"""Codex capability description; execution belongs to the registered run."""
from __future__ import annotations

from ....protocol.capabilities import adapter_capabilities
from . import native_run
from .config import CodexUnavailable, cli_command


class CodexAdapter:
    """Native availability and facts, with no inherited execution entries."""

    name = "codex"
    native_resume = True
    interrupted_resume = True
    native_resume_permission_change = True
    model_discovery = True
    read_only_structured = True
    system_sandbox_platforms = ("darwin", "linux", "win32")
    read_only_structured_resume = True
    no_tool_structured = True

    @property
    def capabilities(self):
        return adapter_capabilities(self.name)

    def local_read_only_check(self) -> dict:
        # Qualification confirms this project's registered mechanism exists;
        # the native policy readback belongs to the run itself and no vendor
        # source is examined here.
        from ..base import registered_read_only_check
        return registered_read_only_check(self)

    def available(self) -> tuple[bool, str | None]:
        from ..runtime_selection import selected
        record = selected("codex")
        if record is not None:
            return record.get('status') == 'ready', record.get('reasonCode')
        try:
            cli_command()
            return True, None
        except CodexUnavailable as error:
            return False, str(error)

    def discovery_available(self) -> tuple[bool, str | None]:
        return self.available()

    validate_turn_provenance = staticmethod(native_run.validate_turn_provenance)

    def discover_models(self) -> dict:
        from ...roles.run_execution import discover_models
        return discover_models(self.name)

    def delete_sessions(self, session_ids: list[str]) -> dict[str, str]:
        from ...roles.run_execution import delete_sessions
        return delete_sessions(self.name, session_ids)
