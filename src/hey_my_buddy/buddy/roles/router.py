"""Router attempts over the harness-neutral read-only structured-call protocol."""
from __future__ import annotations

from dataclasses import replace
import time

from ...errors import BoardError
from ...protocol.capabilities import PERMISSION_LEVELS, adapter_capabilities
from ...private_dirs import context_root, directory_mode, ensure_private_dir
from ...blackboard.routing import router
from . import router_input
from .prompts import router_prompt
from . import controller as role_seam
from ..harnesses.base import (
    Adapter,
    ExecutionContext,
    NoToolStructuredRequest,
    ProcessHandle,
    ReadOnlyStructuredRequest,
)
from . import structured_call as read_only

ROUTER_STOP_GRACE_SECONDS = 12.0


def prepare_router_fast(document: dict, buddy: dict, native: Adapter,
                        context: ExecutionContext) -> role_seam.FastPreparation:
    """Everything the fast mode's native call needs, except the start itself.

    The zero-tool verdict stays with the blackboard at publication. The native call
    receives an empty owner-private cwd under its actual harness attempt; its
    no-tool buddy disables project input.
    """
    root = ensure_private_dir(context_root(context, native.name) / "no-tool-cwd")
    if any(root.iterdir()):
        raise BoardError("CONFLICT", "The private no-tool cwd already contains files")
    request = NoToolStructuredRequest(str(root), router_prompt(router.render_prompt(document)), document["outputSchema"], timeout_seconds=60,
                                      capture_evidence=document.get("captureEvidence") is True)
    child_context = replace(context, spec={**context.spec, **buddy, "cwd": str(root)}, turn=None, agent_credential=None)
    return role_seam.FastPreparation(harness=native.name, request=request, context=child_context,
                                     no_tool_cwd=root)


def prepare_router_review(document: dict, buddy: dict, native: Adapter,
                          context: ExecutionContext) -> role_seam.ReviewPreparation:
    """The frozen mirror and read-only request, before any start.

    The mirror is materialized from the immutable input tree before the native
    call exists, and the binding the collection re-verifies is frozen here.
    """
    manifest = document.get("executionWorkspace")
    root, digest = router_input.prepare(
        manifest, ensure_private_dir(context_root(context, native.name)),
        deadline=time.monotonic() + document["budget"]["timeoutSeconds"])
    try:
        request = ReadOnlyStructuredRequest(str(root), router_prompt(router.render_prompt(document)), document["outputSchema"],
                                            document["budget"], capture_evidence=document.get("captureEvidence") is True)
        child_context = replace(context, spec={**context.spec, **buddy, "cwd": str(root)}, turn=None, agent_credential=None)
        return role_seam.ReviewPreparation(harness=native.name, native=native, request=request, context=child_context,
                                           mirror=(manifest, root, digest))
    except Exception:
        router_input.discard(root)
        raise


class DecisionAdapter(Adapter):
    name = "decision"
    capabilities = adapter_capabilities("decision")

    def available(self) -> tuple[bool, str | None]:
        from ..harnesses.registry import adapters
        for native in adapters().values():
            if {"none", "read"} & PERMISSION_LEVELS.get(native.name, {}).keys() and native.available()[0]:
                return True, None
        return False, "No native harness has an eligible structured Router entrypoint"

    def prepare(self, context: ExecutionContext) -> None:
        from ..harnesses.registry import adapter
        if hasattr(self, "_preparation"):
            raise BoardError("CONFLICT", "The Router already has a prepared call")
        document = context.decision_input
        if not isinstance(document, dict):
            raise BoardError("INVALID_ARGUMENT", "The Router has no frozen input")
        buddy = document.get("buddy") or {}
        native = adapter(buddy.get("harness"))
        fast = document.get("routingMode") == "fast"
        context.directory.mkdir(mode=directory_mode(), parents=True, exist_ok=True)
        if fast:
            self._preparation = prepare_router_fast(document, buddy, native, context)
        else:
            self._preparation = prepare_router_review(document, buddy, native, context)
        self._prepared_context = context

    def start(self, context: ExecutionContext) -> ProcessHandle:
        if getattr(self, "_prepared_context", None) is not context:
            raise BoardError("INVALID_ARGUMENT", "The Router preparation belongs to another attempt")
        preparation = self._preparation
        del self._preparation
        del self._prepared_context
        fast = isinstance(preparation, role_seam.FastPreparation)
        handle = role_seam.start_router_preparation(preparation)
        if fast:
            handle.router_input = None
            handle.no_tool_cwd = preparation.no_tool_cwd
        else:
            handle.router_input = preparation.mirror
        return handle

    def collect(self, handle: ProcessHandle, context: ExecutionContext):
        outcome = getattr(handle, "router_native_outcome", None)
        if outcome is None:
            outcome = read_only.collect(handle)
            handle.router_native_outcome = outcome
        native_result = outcome.result
        document = context.decision_input
        verification = None
        if outcome.shutdown_confirmed and getattr(handle, 'no_tool_cwd', None):
            try:
                handle.no_tool_cwd.rmdir()
            except OSError:
                pass  # Retain unexpected native files for inspection.
        if outcome.shutdown_confirmed and getattr(handle, 'router_input', None) is not None:
            verification = router_input.verify(
                *handle.router_input, deadline=time.monotonic() + document["budget"]["timeoutSeconds"])
            if verification["unchanged"] is None:
                raise BoardError("router-input-unknown", "The frozen Router input could not yet be verified")
            # The mirror is a full copy of the frozen input. Once the native group is
            # proven stopped and the copy verified, the digests are the evidence; keep
            # an unverified or changed copy for inspection only.
            if verification["unchanged"]:
                router_input.discard(handle.router_input[1])
        usage = {
            "elapsedMs": None,
            "toolCalls": None, "bytesRead": None,
            **(native_result.get("usage") or {}),
        }
        result = {
            "operation": "select", "status": "ok" if outcome.status == "ok" else "error",
            "requested": document["buddy"], "resolved": native_result.get("resolved"),
            "modelStarted": native_result.get("modelStarted"),
            "nativeIdentity": native_result.get("nativeIdentity"), "usage": usage,
            "toolEvidence": native_result.get("toolEvidence"),
            "harnessVersion": native_result.get("harnessVersion"),
            "nativeEvidence": native_result.get("nativeEvidence"),
            "nativeFailure": native_result.get("nativeFailure"),
            "correctionCount": native_result.get("correctionCount"),
            "budget": document["budget"], "inputVerification": verification,
            **router.routing_facts(document),
            "stopEvidence": {"shutdownConfirmed": outcome.shutdown_confirmed,
                             "native": native_result.get("processState"),
                             "nativeInterruptRequested": native_result.get("nativeInterruptRequested"),
                             "nativeInterruptAcknowledged": native_result.get("nativeInterruptAcknowledged")},
        }
        code = native_result.get("code")
        if code in ("deadline", "timeout", "readonly-budget-exhausted"):
            code = "router-budget-exhausted"
        if code == "no-tool-violation":
            code = "router-tools-forbidden"
        if code == "readonly-policy-unverified":
            code = "router-review-unavailable"
            result.update(reason="The native review permission policy could not be verified")
        if verification is not None and not verification["unchanged"]:
            code = verification["code"]
        if code is None and outcome.status == "ok":
            try:
                result["decision"] = router.validate_answer(native_result.get("rawAnswer"),
                                                           [item["buddyId"] for item in document["buddies"]], document.get("routingMode", "review"))
            except BoardError as failure:
                code = failure.code
        if code:
            result.update(status="error", code=code)
            if outcome.status != "cancelled":
                outcome.status = "failed"
            outcome.error = code
        outcome.result = result
        return outcome

    def cancel(self, handle: ProcessHandle, *, grace_seconds: float = ROUTER_STOP_GRACE_SECONDS) -> None:
        # Controllers translate SIGTERM into native interrupt, then reap their own groups.
        handle.terminate(grace_seconds=grace_seconds)
