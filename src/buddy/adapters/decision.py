"""Router attempts over the harness-neutral read-only structured-call protocol."""
from __future__ import annotations

from dataclasses import replace
import time

from ..errors import BoardError
from .. import router, router_input
from .base import Adapter, ExecutionContext, ProcessHandle, ReadOnlyStructuredRequest
from . import read_only


class DecisionAdapter(Adapter):
    name = "decision"
    capabilities = ("decision",)

    def available(self) -> tuple[bool, str | None]:
        from . import adapters
        for native in adapters().values():
            if native.read_only_structured and native.read_only_structured_verified and native.available()[0]:
                return True, None
        return False, "No native read-only structured configuration has been verified"

    def start(self, context: ExecutionContext) -> ProcessHandle:
        from . import adapter
        document = context.decision_input
        if not isinstance(document, dict):
            raise BoardError("INVALID_ARGUMENT", "The Router has no frozen input")
        profile = document.get("profile") or {}
        native = adapter(profile.get("adapter"))
        if not (native.read_only_structured and native.read_only_structured_verified):
            raise BoardError("UNSUPPORTED_ADAPTER", "This native read-only structured capability is unverified")
        context.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        manifest = document.get("executionWorkspace")
        root, digest = router_input.prepare(manifest, context.directory)
        budget = document["budget"]
        request = ReadOnlyStructuredRequest(str(root), router.render_prompt(document), document["outputSchema"], budget)
        child_context = replace(context, spec={**context.spec, **profile, "cwd": str(root)},
                                turn=None, agent_credential=None)
        started = time.monotonic()
        handle = native.start_read_only_structured(child_context, request)
        handle.router_input = (manifest, root, digest)
        handle.router_started = started
        return handle

    def collect(self, handle: ProcessHandle, context: ExecutionContext):
        outcome = read_only.collect(handle)
        native_result = outcome.result
        document = context.decision_input
        verification = None
        if outcome.shutdown_confirmed:
            verification = router_input.verify(*handle.router_input)
        usage = native_result.get("usage") or {
            "elapsedMs": round((time.monotonic() - handle.router_started) * 1000),
            "toolCalls": None, "bytesRead": None,
        }
        result = {
            "operation": "select", "status": "ok" if outcome.status == "ok" else "error",
            "tableRevision": document["tableRevision"], "requested": document["profile"],
            "resolved": native_result.get("resolved"), "observed": native_result.get("observed"),
            "nativeIdentity": native_result.get("nativeIdentity"), "usage": usage,
            "budget": document["budget"], "inputVerification": verification,
            "stopEvidence": {"shutdownConfirmed": outcome.shutdown_confirmed,
                             "native": native_result.get("processState"),
                             "nativeInterruptRequested": native_result.get("nativeInterruptRequested"),
                             "nativeInterruptAcknowledged": native_result.get("nativeInterruptAcknowledged")},
        }
        code = native_result.get("code")
        if code in ("deadline", "readonly-budget-exhausted"):
            code = "router-budget-exhausted"
        if verification is not None and not verification["unchanged"]:
            code = "router-input-changed"
        if code is None and outcome.status == "ok":
            try:
                result["decision"] = router.validate_answer(native_result.get("rawAnswer"),
                                                           [item["profileId"] for item in document["profiles"]])
            except BoardError as failure:
                code = failure.code
        if code:
            result.update(status="error", code=code)
            if outcome.status != "cancelled":
                outcome.status = "failed"
            outcome.error = code
        outcome.result = result
        return outcome

    def cancel(self, handle: ProcessHandle, *, grace_seconds: float = 12.0) -> None:
        # Controllers translate SIGTERM into native interrupt, then reap their own groups.
        handle.terminate(grace_seconds=grace_seconds)
