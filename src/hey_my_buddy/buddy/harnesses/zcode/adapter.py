"""ZCode capability description; execution belongs to the registered run."""
from __future__ import annotations

import os

from . import native_run
from .config import cli_command, provider_paths
from .protocol import NativeError


class ZcodeAdapter:
    """Native availability and facts, with no inherited execution entries."""

    name = "zcode"
    capabilities = ("zcode", "observe", "inquiry", "workspace", "cancel", "artifacts", "deadline", "native-session")
    native_resume = True
    interrupted_resume = False
    native_resume_permission_change = True
    model_discovery = True
    no_tool_structured = True
    read_only_structured = False
    read_only_structured_resume = False

    def __init__(self):
        from ..registry import worker_permission_capabilities
        self.capabilities = (*type(self).capabilities, *worker_permission_capabilities(self.name))

    def local_read_only_check(self) -> dict:
        return {"eligible": False, "reasonCode": "readonly-worker-carrier-unimplemented",
                "reason": "ZCode review on the Worker carrier is not implemented; its separate read-only channel was removed from stage 2",
                "systemSandbox": False, "sameAttemptContinuation": self.read_only_structured_resume}

    def available(self) -> tuple[bool, str | None]:
        from ..runtime_selection import selected
        record = selected("zcode")
        if record is not None:
            return record.get('status') == 'ready', record.get('reasonCode')
        try:
            command = cli_command()
            provider_paths(os.environ, command)
            return True, None
        except NativeError as error:
            return False, str(error)

    def discovery_available(self) -> tuple[bool, str | None]:
        return self.available()

    validate_turn_provenance = staticmethod(native_run.validate_turn_provenance)

    def discover_models(self) -> dict:
        from ...roles.run_execution import discover_models
        return discover_models(self.name)
