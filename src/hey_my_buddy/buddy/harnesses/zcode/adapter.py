"""ZCode capability description; execution belongs to the registered run."""
from __future__ import annotations

from . import native_run
from .config import SUPPORTED_ACCESS, cli_command, provider_access_types, provider_paths
from .protocol import NativeError


class ZcodeAdapter:
    """Native availability and facts, with no inherited execution entries."""

    name = "zcode"
    capabilities = ("zcode", "observe", "inquiry", "workspace", "cancel", "artifacts", "deadline", "native-session")
    native_resume = True
    model_discovery = True
    no_tool_structured = True
    read_only_structured = False
    read_only_structured_resume = False

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
            cli_command()
            paths = provider_paths()
            if not any(t in SUPPORTED_ACCESS for t in provider_access_types(*paths).values()):
                return False, "ZCode has no configured API-key provider; OAuth account providers are unavailable through this adapter"
            return True, None
        except NativeError as error:
            return False, str(error)

    def discovery_available(self) -> tuple[bool, str | None]:
        return self.available()

    validate_turn_provenance = staticmethod(native_run.validate_turn_provenance)

    def discover_models(self) -> dict:
        from ...roles.run_execution import discover_models
        return discover_models(self.name)
