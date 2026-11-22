"""ZCode capability description; execution belongs to the registered run."""
from __future__ import annotations

import os
from pathlib import Path

from ....protocol.capabilities import adapter_capabilities
from ....protocol.harness_declarations import harness_declaration
from . import native_run
from .config import cli_command, provider_paths
from .protocol import NativeError


class ZcodeAdapter:
    """Native availability and facts, with no inherited execution entries."""

    name = "zcode"
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
        from ....harness_selection import selected
        record = selected("zcode")
        if record is not None:
            return record.get('status') == 'ready', (record.get('reason') or {}).get('code')
        try:
            command = cli_command()
            provider_paths(os.environ, command)
            return True, None
        except NativeError as error:
            return False, str(error)

    def discovery_available(self) -> tuple[bool, str | None]:
        return self.available()

    validate_turn_provenance = staticmethod(native_run.validate_turn_provenance)

    def discover_models(self, *, state_dir: Path) -> dict:
        from ...roles.run_execution import discover_models
        return discover_models(self.name, state_dir=state_dir)
