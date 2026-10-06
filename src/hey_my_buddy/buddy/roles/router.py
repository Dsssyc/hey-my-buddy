"""Router attempts over the harness-neutral read-only structured-call protocol."""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import time

from ...errors import BoardError
from ...private_dirs import context_root, ensure_private_dir
from ...blackboard.routing import router
from . import router_input
from . import controller as role_seam
from ..harnesses.base import (
    Adapter,
    ExecutionContext,
    NoToolStructuredRequest,
    ProcessHandle,
    ReadOnlyStructuredRequest,
)
from . import structured_call as read_only


def prepare_router_fast(document: dict, profile: dict, native: Adapter,
                        context: ExecutionContext) -> role_seam.FastPreparation:
    """Everything the fast mode's native call needs, except the start itself.

    The eligibility check only confirms the declared capability exists; the
    zero-tool verdict stays with the blackboard at publication. The native call
    receives an empty owner-private cwd under its actual harness attempt; its
    no-tool profile disables project input.
    """
    if not getattr(native, "no_tool_structured", False):
        raise BoardError("router-no-tool-unsupported", "Router 不可用：harness 未实现无工具结构化入口")
    root = ensure_private_dir(context_root(context, native.name) / "no-tool-cwd")
    if any(root.iterdir()):
        raise BoardError("CONFLICT", "The private no-tool cwd already contains files")
    request = NoToolStructuredRequest(str(root), router.render_prompt(document), document["outputSchema"], timeout_seconds=60,
                                      capture_evidence=document.get("captureEvidence") is True)
    child_context = replace(context, spec={**context.spec, **profile, "cwd": str(root)}, turn=None, agent_credential=None)
    return role_seam.FastPreparation(harness=native.name, request=request, context=child_context,
                                     no_tool_cwd=root)


def prepare_router_review(document: dict, profile: dict, native: Adapter,
                          context: ExecutionContext) -> role_seam.ReviewPreparation:
    """Eligibility, the frozen mirror and the read-only request, before any start.

    The mirror is materialized from the immutable input tree before the native
    call exists, and the binding the collection re-verifies is frozen here.
    """
    eligibility = native.local_read_only_check()
    if not eligibility["eligible"]:
        detail = eligibility.get("reason") or "harness 不满足审阅模式本地资格"
        raise BoardError("router-review-unsupported", f"Router 不可用：{detail}")
    manifest = document.get("executionWorkspace")
    root, digest = router_input.prepare(manifest, ensure_private_dir(context_root(context, native.name)))
    request = ReadOnlyStructuredRequest(str(root), router.render_prompt(document), document["outputSchema"],
                                        document["budget"], capture_evidence=document.get("captureEvidence") is True)
    child_context = replace(context, spec={**context.spec, **profile, "cwd": str(root)}, turn=None, agent_credential=None)
    return role_seam.ReviewPreparation(harness=native.name, native=native, request=request, context=child_context,
                                       mirror=(manifest, root, digest))


class DecisionAdapter(Adapter):
    name = "decision"
    capabilities = ("decision",)

    def available(self) -> tuple[bool, str | None]:
        from ..harnesses.registry import adapters
        for native in adapters().values():
            if (getattr(native, "no_tool_structured", False) or native.local_read_only_check()["eligible"]) and native.available()[0]:
                return True, None
        return False, "No native adapter has an eligible structured Router entrypoint"

    def start(self, context: ExecutionContext) -> ProcessHandle:
        from ..harnesses.registry import adapter
        document = context.decision_input
        if not isinstance(document, dict):
            raise BoardError("INVALID_ARGUMENT", "The Router has no frozen input")
        profile = document.get("profile") or {}
        native = adapter(profile.get("adapter"))
        fast = document.get("routingMode") == "fast"
        context.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        if fast:
            preparation = prepare_router_fast(document, profile, native, context)
        else:
            preparation = prepare_router_review(document, profile, native, context)
        started = time.monotonic()
        handle = role_seam.start_router_preparation(preparation)
        handle.router_started = started
        if fast:
            handle.router_input = None
            handle.no_tool_cwd = preparation.no_tool_cwd
        else:
            handle.router_input = preparation.mirror
        return handle

    def collect(self, handle: ProcessHandle, context: ExecutionContext):
        outcome = read_only.collect(handle)
        native_result = outcome.result
        document = context.decision_input
        verification = None
        if outcome.shutdown_confirmed and getattr(handle, 'no_tool_cwd', None):
            try:
                handle.no_tool_cwd.rmdir()
            except OSError:
                pass  # Retain unexpected native files for inspection.
        if outcome.shutdown_confirmed and getattr(handle, 'router_input', None) is not None:
            verification = router_input.verify(*handle.router_input)
            # The mirror is a full copy of the frozen input. Once the native group is
            # proven stopped and the copy verified, the digests are the evidence; keep
            # a changed copy for inspection only.
            if verification["unchanged"]:
                router_input.discard(handle.router_input[1])
        usage = {
            "elapsedMs": round((time.monotonic() - handle.router_started) * 1000),
            "toolCalls": None, "bytesRead": None,
            **(native_result.get("usage") or {}),
        }
        result = {
            "operation": "select", "status": "ok" if outcome.status == "ok" else "error",
            "tableRevision": document["tableRevision"], "requested": document["profile"],
            "resolved": native_result.get("resolved"), "observed": native_result.get("observed"),
            "modelStarted": native_result.get("modelStarted"),
            "nativeIdentity": native_result.get("nativeIdentity"), "usage": usage,
            "toolEvidence": native_result.get("toolEvidence"),
            "harnessVersion": native_result.get("harnessVersion"),
            "nativeEvidence": native_result.get("nativeEvidence"),
            "nativeFailure": native_result.get("nativeFailure"),
            "correctionCount": native_result.get("correctionCount"),
            "budget": document["budget"], "inputVerification": verification,
            **router.routing_facts(document),
            "zeroToolVerified": native_result.get("zeroToolVerified") if document.get("routingMode") == "fast" else None,
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
            code = "router-input-changed"
        if code is None and outcome.status == "ok":
            try:
                result["decision"] = router.validate_answer(native_result.get("rawAnswer"),
                                                           [item["profileId"] for item in document["profiles"]], document.get("routingMode", "review"))
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
