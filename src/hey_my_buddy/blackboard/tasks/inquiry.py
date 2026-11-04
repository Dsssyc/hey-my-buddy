"""Project live facts through the owning Worker and retain terminal journal evidence."""
from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

from ...protocol import schemas
from ...protocol.reasons import reason as make_reason
from ...errors import BoardError
from ..store.store import BoardStore
from ...protocol.inquiry import (
    BRIDGE_ERRORS,
    DEFAULT_TRANSPORT_TIMEOUT_MS,
    MAX_TRANSPORT_TIMEOUT_MS,
    MIN_TRANSPORT_TIMEOUT_MS,
)
# The shared live frames of the registered-seam path (ADR-025 step 2-C2): the
# board builds and reads the common channel formats, never a specific harness.
from ...buddy.harnesses.live import MAX_OBSERVE_LIMIT, InquiryPayload, LiveObservation, LiveRequest, LiveReply, LiveSnapshot
from ...protocol.harness_names import HARNESS_NAMES

if TYPE_CHECKING:
    from ..service.live_registry import LiveRegistry

MAX_WAIT_MS = 30000
POLL_INTERVAL_SECONDS = 0.3

#: These refusals cannot leave a question queued: either its turn ended or the
#: bridge could not record it for delivery. No new native turn is started.
TERMINAL_BRIDGE_ERRORS = ("agent-gone", "agent-not-running", "journal-unavailable")

LIMITS = {
    "maxQuestionBytes": schemas.MAX_QUESTION_BYTES,
    "maxAnswerBytes": schemas.MAX_ANSWER_BYTES,
    "maxInquiriesPerRun": schemas.MAX_INQUIRIES_PER_RUN,
    "maxWaitMs": MAX_WAIT_MS,
    "maxTransportTimeoutMs": MAX_TRANSPORT_TIMEOUT_MS,
    "correlation": "inquiryId",
    "exposesModelReasoning": False,
}


def adapter_capabilities(adapter: str) -> frozenset[str]:
    """The adapter registry's declared capability set, or empty when unknown."""
    try:
        from ...buddy.harnesses.registry import adapters

        return frozenset(adapters()[adapter].capabilities)
    except (KeyError, TypeError):
        return frozenset()


def inquiry_capable(adapter: str) -> bool:
    """Whether this adapter declares correlated ``inquiry`` in its registry.

    The registry is the single declaration point. A harness without it answers
    honestly that it has no inquiry capability instead of half-observing a bridge
    that was never mounted; the check never probes the native CLI or starts
    anything, so a read-only observation stays free of side effects.
    """
    return "inquiry" in adapter_capabilities(adapter)


def observe_capable(adapter: str) -> bool:
    """Whether bounded read-only native observation is declared and mounted."""
    capabilities = adapter_capabilities(adapter)
    return "inquiry" in capabilities or "observe" in capabilities


def observe(store: BoardStore, params: dict, *, live_registry: LiveRegistry) -> dict:
    """One bounded inquiry operation: observe, ask, or read a correlated answer."""
    from ...protocol.requests import parse_request, dump_request
    from ..store.db import canonical_json, sha256_text
    params = dump_request(parse_request("inquire", params, audience="service"))
    timeout_ms = params.get("timeoutMs", DEFAULT_TRANSPORT_TIMEOUT_MS)
    wait_ms = params.get("waitMs", 0)
    inquiry_id = params.get("inquiryId")
    question = params.get("question")
    read_existing = inquiry_id is not None
    if read_existing:
        message = store.message_get({"runId": params["runId"], "inquiryId": inquiry_id})["message"]
    if question is not None:
        inquiry_id = "inq-" + sha256_text(canonical_json(["adr032-inquiry-v1", params["runId"], question]))
    view = store.task_get({"runId": params.get("runId")})["task"]
    attempt = view.get("selectedAttempt") or {}
    adapter = view.get("adapter")
    duplicate = read_existing
    recorded = message if read_existing else None
    if question is not None:
        posted = store._message_post(view["runId"], inquiry_id, question)
        recorded = posted["message"]
        duplicate = bool(posted.get("duplicate"))

    terminal = view["state"] in ("completed", "concluded", "failed", "cancelled")
    attempt_ended = terminal or attempt.get("executionState") == "finished"
    can_ask = inquiry_capable(adapter)
    can_observe = observe_capable(adapter)
    extracted_live = adapter in HARNESS_NAMES
    channel = None if attempt_ended else _live_channel(live_registry, view)
    # A restarted service waits for the original holder to attach before it
    # admits active inquiry traffic or reads that attempt's journal.
    unbound_live = extracted_live and not attempt_ended and channel is None
    bridge = {"enabled": channel is not None, "observed": False, "reason": None, "error": None,
              "canObserve": can_observe, "canAsk": can_ask}
    live: dict[str, Any] = {
        "available": False,
        "error": None,
        "unavailable": ["sessionId", "agentStatus", "inbox", "lastEvent", "activity", "replyTool"],
        "limits": LIMITS,
    }

    def import_journal():
        with store.db.write() as connection:
            stored_attempt = store._attempt_row(connection, attempt["attemptId"])
            journal = store._import_inquiry_journal(connection, stored_attempt)
            head = store._head_of(connection)
        store._notify(head)
        return journal

    imported_replay = ()
    if channel is not None and duplicate and question is not None:
        imported_replay = import_journal().get("imported", ())
        recorded = store.message_get({"runId":view["runId"],"inquiryId":inquiry_id})["message"]
    if attempt_ended:
        pass
    elif inquiry_id is not None and not duplicate and not can_ask:
        # The adapter can be observed but cannot ask: record the honest capability
        # refusal instead of sending a question the native protocol cannot carry.
        bridge["capability"] = "observe"
        _mark_unavailable(store, view["runId"], inquiry_id, "unsupported" if can_observe else "unsupported-method")
    elif not can_observe:
        pass
    elif channel is not None:
        # Only the registered binding may inject into this attempt's native turn.
        # Durable question facts are imported from its private journal below.
        if (question is not None and recorded is not None
                and (not duplicate or recorded["state"] == "queued" and inquiry_id not in imported_replay)
                and recorded["state"] in ("queued", "claimed", "delivered")
                and recorded["attemptId"] == attempt.get("attemptId")):
            reply = channel.request(
                LiveRequest(identity=channel.identity, request_id=inquiry_id, kind="inquiry",
                            payload=InquiryPayload(question_id=inquiry_id, question=question)),
                timeout_ms=timeout_ms)
            if reply.observed:
                # An observed interaction projects its committed state whatever
                # it is — a withdrawn question's replay carries ``discarded``
                # and its delivery evidence, never a transport refusal.
                bridge["observed"] = True
            else:
                code = reply.error_code or reply.reason_code
                bridge["reason"] = "bridge-refused" if code in BRIDGE_ERRORS else reply.reason_code
                bridge["error"] = code if code in BRIDGE_ERRORS else None
                if code in TERMINAL_BRIDGE_ERRORS:
                    # The owned turn already ended: the question is refused, never
                    # injected into a new or idle turn and never left looking pending.
                    _mark_unavailable(store, view["runId"], inquiry_id, code)
        elif inquiry_id is None:
            snapshot = channel.observe(after_seq=None, limit=MAX_OBSERVE_LIMIT, timeout_ms=timeout_ms,
                                       fields=("observation",))
            if snapshot.observed is True and snapshot.observation is not None:
                bridge["observed"] = True
                live = _live(snapshot.observation)
            else:
                bridge["reason"] = snapshot.reason or "observation-unavailable"
                bridge["error"] = snapshot.error
    elif unbound_live:
        bridge["reason"] = "channel-unbound"

    # Durable question facts use the same attempt-bound reader while active and
    # finished. A missing active connection still grants no file-only fallback.
    if channel is not None or attempt_ended and attempt.get("attemptId"):
        journal = import_journal()
    else:
        journal = {"available": False, "reason": "channel-unbound" if unbound_live else "no-live-channel", "entries": 0}
    if inquiry_id is not None and wait_ms:
        deadline = time.monotonic() + wait_ms / 1000.0
        while time.monotonic() < deadline:
            current = store.message_get({"runId": view["runId"], "inquiryId": inquiry_id})["message"]
            if current["state"] in ("answered", "discarded", "unavailable"):
                break
            time.sleep(min(POLL_INTERVAL_SECONDS, max(0, deadline - time.monotonic())))
            if channel is not None:
                journal = import_journal()

    rejections = journal.get("rejections", {})
    if inquiry_id in rejections:
        bridge["journalRejected"] = rejections[inquiry_id]
    elif inquiry_id in journal.get("imported", []):
        bridge["journalImported"] = True
    view = store.task_get({"runId": view["runId"]})["task"]
    attempt = view.get("selectedAttempt") or {}
    terminal = view["state"] in ("completed", "concluded", "failed", "cancelled")
    message = None
    if inquiry_id is not None:
        message = store.message_get({"runId": view["runId"], "inquiryId": inquiry_id})["message"]
    pending = store.message_list({"runId": view["runId"], "limit": 50})["messages"]
    return {
        "runId": view["runId"],
        "inquiryId": inquiry_id,
        "requestId": view["requestId"],
        "status": view["state"],
        "phase": "terminal" if terminal else "active",
        "execution": {
            "status": view["state"],
            "revision": view["revision"],
            "createdAt": view["createdAt"],
            "updatedAt": view["updatedAt"],
            "resultAvailable": view["resultAvailable"],
            "shutdownConfirmed": view["shutdownConfirmed"],
            "cancelRequested": view.get("cancelRequested", False),
            "logPaths": view.get("logPaths"),
            "attemptId": attempt.get("attemptId"),
            "attemptState": attempt.get("executionState"),
            "generation": attempt.get("generation"),
        },
        **({"expectedSeconds": view["expectedSeconds"]} if view["adapter"] != "decision"
           else {"routerBudgetSeconds": view["routerBudgetSeconds"]}),
        "bridge": bridge,
        "live": live,
        "inquiry": _inquiry_view(message, duplicate) if message else None,
        "pendingInquiries": [
            {"inquiryId": item["inquiryId"], "state": item["state"], "submittedAt": item["createdAt"]}
            for item in pending
            if item["state"] in ("queued", "claimed", "delivered")
        ],
        "journal": {"available": journal["available"], "reason": journal["reason"],
                    "entries": journal["entries"],
                    **({"rejections": rejections} if rejections else {})},
        "limits": LIMITS,
    }


def _live_channel(registry: LiveRegistry, view: dict):
    """Service restart leaves no live bindings until the same holder attaches again."""
    channel = registry.channel_for(view)
    return _ExecutionChannel(channel) if channel is not None else None


class _ExecutionChannel:
    """Decode the execution endpoint's string reply at the existing live boundary."""
    def __init__(self, channel):
        self.channel = channel
        self.identity = channel.identity

    def request(self, request, **kwargs):
        return LiveReply.from_payload(self.channel.request(request, **kwargs))

    def observe(self, **kwargs):
        return LiveSnapshot.from_payload(self.channel.observe(**kwargs))


def _mark_unavailable(store: BoardStore, task_id: str, inquiry_id: str, code: str) -> None:
    """Record the refusal of the question just posted by observe."""
    reason = (make_reason("inquiry-observe-only") if code == "unsupported"
              else make_reason("inquiry-root-turn-ended") if code in ("agent-gone", "agent-not-running")
              else make_reason("bridge-refused", errorCode=code))
    store._message_update(task_id, inquiry_id, actor="live-bridge", state="unavailable", reason=reason)
    return None


def _inquiry_view(message: dict, duplicate: bool) -> dict:
    answer = message.get("answer")
    state = message["state"]
    return {
        "inquiryId": message["inquiryId"],
        "state": state,
        "reason": message.get("reason"),
        "recorded": True,
        "duplicate": duplicate,
        "questionBytes": message["questionBytes"],
        "questionPreview": message["question"][:280],
        "submittedAt": message["createdAt"],
        "delivery": message.get("delivery"),
        "updatedAt": message["updatedAt"],
        "answer": (
            {"available": True, **answer} if answer else {"available": False}
        ),
        "correlation": "inquiryId",
    }


def _live(value: LiveObservation) -> dict:
    if not value.ready:
        return {
            "available": False,
            "error": value.error,
            "deliveryMode": value.delivery_mode,
            "unavailable": ["sessionId", "agentStatus", "inbox", "lastEvent", "activity", "replyTool"],
            "limits": LIMITS,
        }
    return {
        "available": True,
        "error": value.error,
        "observedAt": value.observed_at,
        "sessionId": value.session_id,
        "agentStatus": value.agent_status,
        "inbox": value.inbox.value if value.inbox else None,
        "lastEvent": value.last_event.to_payload() if value.last_event else None,
        "activity": [event.to_payload() for event in value.recent_activity],
        "activityDropped": value.activity_dropped,
        "replyTool": value.reply_tool.value if value.reply_tool else None,
        "capability": value.capability or ("inquiry" if value.reply_tool else "observe"),
        "supported": value.supported is not False,
        "deliveryMode": value.delivery_mode,
        "journal": value.journal.value if value.journal else None,
        # Bounded, metadata-only evidence that the native harness asked for an
        # interactive capability this adapter cannot grant. It is surfaced verbatim
        # for the Host boundary and never fabricated when absent.
        "attention": value.attention.value if value.attention else None,
        "unavailable": list(value.unavailable),
        "limits": LIMITS,
    }
