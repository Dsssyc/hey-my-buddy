"""Project live facts through the owning Worker and retain terminal journal evidence."""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ...protocol import schemas
from ...protocol.reasons import reason as make_reason
from ...errors import BoardError
from ..store.store import BoardStore
from ...private_dirs import attempt_root
from ...protocol.inquiry import (
    BRIDGE_ERRORS,
    DEFAULT_TRANSPORT_TIMEOUT_MS,
    MAX_TRANSPORT_TIMEOUT_MS,
    MIN_TRANSPORT_TIMEOUT_MS,
)
# The shared live frames of the registered-seam path (ADR-025 step 2-C2): the
# board builds and reads the common channel formats, never a specific harness.
from ...buddy.harnesses.live import MAX_OBSERVE_LIMIT, InquiryPayload, LiveRequest, LiveReply, LiveSnapshot

if TYPE_CHECKING:
    from ..service.live_registry import LiveRegistry

MAX_WAIT_MS = 30000
MAX_JOURNAL_BYTES = 1024 * 1024
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


def read_journal(results_path: str | None) -> dict:
    """Import the bridge journal as idempotent transport evidence."""
    if not results_path:
        return {"available": False, "reason": "no-journal-path", "entries": {}}
    path = Path(results_path)
    try:
        size = path.stat().st_size
    except OSError:
        return {"available": False, "reason": "journal-not-written", "entries": {}}
    if size > MAX_JOURNAL_BYTES:
        return {"available": False, "reason": "journal-exceeds-limit", "entries": {}}
    try:
        text = path.read_text(errors="replace")
    except OSError:
        return {"available": False, "reason": "journal-unreadable", "entries": {}}
    entries: dict[str, dict] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except ValueError:
            continue  # a torn line is ignored, never fatal
        if isinstance(record, dict) and isinstance(record.get("inquiryId"), str):
            entries[record["inquiryId"]] = record
    return {"available": True, "reason": None, "entries": entries}


def observe(store: BoardStore, params: dict, *, live_registry: LiveRegistry) -> dict:
    """One bounded inquiry operation: observe, ask, or read a correlated answer."""
    schemas.reject_unknown(params, {"runId", "taskId", "inquiryId", "question", "timeoutMs", "waitMs"}, "inquire")
    if (params.get("inquiryId") is None) != (params.get("question") is None):
        raise BoardError("INVALID_ARGUMENT", "inquiryId and question must be supplied together")
    timeout_ms = schemas.optional_int(params, "timeoutMs", DEFAULT_TRANSPORT_TIMEOUT_MS, MIN_TRANSPORT_TIMEOUT_MS, MAX_TRANSPORT_TIMEOUT_MS)
    wait_ms = schemas.optional_int(params, "waitMs", 0, 0, MAX_WAIT_MS)
    inquiry_id = params.get("inquiryId")
    question = params.get("question")
    if inquiry_id is not None:
        schemas.required_string(params, "inquiryId", max_length=128, pattern=schemas.INQUIRY_ID_PATTERN)
        schemas.bounded_text(params, "question", max_bytes=schemas.MAX_QUESTION_BYTES)

    view = store.task_get({k: params[k] for k in ("runId", "taskId") if k in params})["task"]
    attempt = view.get("selectedAttempt") or {}
    adapter = view.get("adapter")
    recorded = None
    duplicate = False
    if inquiry_id is not None:
        posted = store.message_post(
            {
                "runId": view["taskId"],
                "inquiryId": inquiry_id,
                "question": question,
                "author": "cli",
                "waitMs": 0,
            }
        )
        recorded = posted["message"]
        duplicate = bool(posted.get("duplicate"))

    terminal = view["state"] in ("completed", "concluded", "failed", "cancelled")
    attempt_ended = terminal or attempt.get("executionState") == "finished"
    can_ask = inquiry_capable(adapter)
    can_observe = observe_capable(adapter)
    from ...buddy.harnesses.registry import live_binding

    extracted_live = live_binding(adapter) is not None
    channel = None if attempt_ended else _live_channel(live_registry, view)
    # A restarted service waits for the original holder to attach. Active facts
    # never fall back to files or a directly addressed controller.
    unbound_live = extracted_live and not attempt_ended and channel is None
    bridge = {"enabled": channel is not None, "observed": False, "reason": None, "error": None,
              "canObserve": can_observe, "canAsk": can_ask}
    live: dict[str, Any] = {
        "available": False,
        "reason": None,
        "unavailable": ["sessionId", "agentStatus", "inbox", "lastEvent", "activity", "replyTool"],
        "limits": LIMITS,
    }
    if not can_observe or attempt_ended:
        pass
    elif inquiry_id is not None and not can_ask:
        # The adapter can be observed but cannot ask: record the honest capability
        # refusal instead of sending a question the native protocol cannot carry.
        bridge["capability"] = "observe"
        _mark_unavailable(store, view["taskId"], inquiry_id, "unsupported")
    elif channel is not None:
        # The registered live binding of this very run: the question, the
        # observation and the journal projection all go through one channel
        # instead of a second reader beside it.
        if inquiry_id is not None:
            reply = channel.request(
                LiveRequest(identity=channel.identity, request_id=inquiry_id, kind="inquiry",
                            payload=InquiryPayload(question_id=inquiry_id, question=question)),
                timeout_ms=timeout_ms)
            if reply.observed:
                # An observed interaction projects its committed state whatever
                # it is — a withdrawn question's replay carries ``discarded``
                # and its delivery evidence, never a transport refusal.
                bridge["observed"] = True
                _apply_bridge_answer(store, view["taskId"], inquiry_id, _live_ask_value(reply), None)
            else:
                code = reply.error_code or reply.reason_code
                bridge["reason"] = "bridge-refused" if code in BRIDGE_ERRORS else reply.reason_code
                bridge["error"] = code if code in BRIDGE_ERRORS else None
                if code in TERMINAL_BRIDGE_ERRORS:
                    # The owned turn already ended: the question is refused, never
                    # injected into a new or idle turn and never left looking pending.
                    _mark_unavailable(store, view["taskId"], inquiry_id, code)
        else:
            snapshot = channel.observe(after_seq=None, limit=MAX_OBSERVE_LIMIT, timeout_ms=timeout_ms,
                                       fields=("observation",))
            if snapshot.observed is True and snapshot.observation is not None:
                bridge["observed"] = True
                live = _live(_observation_value(snapshot.observation))
            else:
                bridge["reason"] = snapshot.reason or "observation-unavailable"
                bridge["error"] = snapshot.error
    elif unbound_live:
        bridge["reason"] = "channel-unbound"
    evidence_journal = (store.directory / "attempts" / view["taskId"] / attempt["attemptId"] /
                        "inquiry.results.jsonl") if attempt.get("attemptId") else None
    rejections: dict[str, str] = {}
    projected: dict[str, Any] = {}
    if channel is not None:
        # The live run's journal facts come through the channel's bound
        # projection, consumed to the end of its pagination; no second reader
        # opens the same live source beside it, and the frame bound stays.
        journal = None
        after: int | None = None
        for _page in range(64):
            projection = channel.observe(after_seq=after, limit=MAX_OBSERVE_LIMIT,
                                         timeout_ms=timeout_ms, fields=("inquiries",))
            if "journal-unavailable" in (projection.unavailable or ""):
                break
            if projection.journal is not None:
                journal = {"available": projection.journal.available,
                           "reason": projection.journal.reason,
                           "entries": projection.journal.entries}
                rejections = {item.question_id: item.reason
                              for item in projection.journal.rejections}
            for entry in projection.inquiries:
                projected[entry.question_id] = entry
            if not projection.truncated or not projection.inquiries:
                break
            after = max(entry.seq for entry in projection.inquiries)
        if journal is None:
            journal = {"available": False, "reason": "journal-unavailable", "entries": 0}
    elif unbound_live:
        journal = {"available": False, "reason": "channel-unbound", "entries": 0}
    else:
        journal = (read_journal(str(evidence_journal) if evidence_journal else None) if attempt_ended
                   else {"available": False, "reason": "no-live-channel", "entries": {}})
    # Import every existing question's latest source fact even for run-only
    # observation. message_update refuses unknown ids, so orphan source records
    # cannot create questions or consume the run's inquiry allowance.
    records = ({key: _journal_record(entry) for key, entry in projected.items()}
               if channel is not None else journal["entries"]
               if isinstance(journal["entries"], dict) else {})
    if inquiry_id in rejections:
        bridge["journalRejected"] = rejections[inquiry_id]
    for question_id, record in records.items():
        if question_id in rejections:
            continue
        mismatch = (_journal_identity_mismatch(record, view["taskId"], attempt.get("attemptId"))
                    if channel is None else None)
        if mismatch is not None:
            rejections[question_id] = mismatch
            if question_id == inquiry_id:
                bridge["journalRejected"] = mismatch
            continue
        rejected = _apply_journal(store, view["taskId"], question_id, record)
        if rejected is not None:
            rejections[question_id] = rejected
            if question_id == inquiry_id:
                bridge["journalRejected"] = rejected
        elif question_id == inquiry_id:
            bridge["journalImported"] = True

    if inquiry_id is not None and wait_ms:
        deadline = time.monotonic() + wait_ms / 1000.0
        while time.monotonic() < deadline:
            current = store.message_get({"runId": view["taskId"], "inquiryId": inquiry_id})["message"]
            if current["state"] in ("answered", "delivered", "discarded", "unavailable"):
                break
            if channel is None:
                break
            if channel is not None:
                # The wait queries only this one question's native answer, the
                # same single roundtrip the direct path made.
                answer_view = channel.observe(inquiry_id=inquiry_id, timeout_ms=timeout_ms)
                if answer_view.inquiries:
                    _apply_bridge_answer(store, view["taskId"], inquiry_id,
                                         _live_answer_value(answer_view.inquiries[0]), None)
                    break
            time.sleep(POLL_INTERVAL_SECONDS)

    message = None
    if inquiry_id is not None:
        message = store.message_get({"runId": view["taskId"], "inquiryId": inquiry_id})["message"]
    pending = store.message_list({"runId": view["taskId"], "limit": 50})["messages"]
    return {
        "runId": view["taskId"],
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
            if item["state"] not in ("answered", "discarded")
        ],
        "journal": {"available": journal["available"], "reason": journal["reason"],
                    "entries": (journal["entries"] if isinstance(journal["entries"], int)
                                else len(journal["entries"])),
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


def _live_ask_value(reply) -> dict:
    """The ask reply's committed value, in the bridge's own ask-value shape.

    An observed reply projects its actual committed state — a withdrawn
    question's ``discarded`` included — with its delivery record from the correlation.
    """
    correlation = reply.native_correlation.value if reply.native_correlation is not None else {}
    value: dict[str, Any] = {"state": reply.state or reply.status}
    if isinstance(correlation.get("delivery"), dict):
        value["delivery"] = correlation["delivery"]
    return value


def _live_answer_value(entry) -> dict:
    """One projected answer entry back in the native answer view's shape."""
    value: dict[str, Any] = {"state": entry.status}
    if entry.answer is not None:
        value["answer"] = {"available": True, "text": entry.answer, "bytes": entry.bytes,
                           "via": entry.via, "toolCallId": entry.tool_call_id, "at": entry.at,
                           "truncated": entry.truncated is True}
    else:
        value["answer"] = {"available": False}
    return value


def _observation_value(observation) -> dict:
    """The bridge's raw observation value, rebuilt from the channel's typed model.

    ``_live`` keeps reading exactly the fields it always read; the channel's
    ``recentActivity`` metadata is handed over under the ``activity`` key it
    had on the wire.
    """
    value = observation.to_payload()
    value["activity"] = value.pop("recentActivity", [])
    return value


def _journal_record(entry: Any) -> dict:
    """One journal entry back in the record shape the existing importer reads.

    A projected channel entry carries its answer's own source fields; the
    importer's normalization and refusal rules are applied to that record
    unchanged.
    """
    if isinstance(entry, dict):
        return entry
    record: dict[str, Any] = {"inquiryId": entry.question_id, "state": entry.status}
    if entry.answer is not None:
        record["answer"] = {"text": entry.answer, "bytes": entry.bytes, "via": entry.via,
                            "toolCallId": entry.tool_call_id, "at": entry.at,
                            "truncated": entry.truncated is True}
    if entry.delivery is not None:
        record["delivery"] = entry.delivery.value
    return record


def _journal_identity_mismatch(record: dict, task_id: str, attempt_id: str | None) -> str | None:
    """Reject journal transport evidence bound to another task or attempt."""
    for key, expected, label in (("taskId", task_id, "task"), ("attemptId", attempt_id, "attempt")):
        value = record.get(key)
        if value in (None, ""):
            continue
        if not isinstance(value, str) or value != expected:
            return "journal-task-mismatch" if label == "task" else "journal-attempt-mismatch"
    if not record.get("taskId") or not record.get("attemptId"):
        return "journal-attempt-unbound"
    return None


def _known_question(store: BoardStore, task_id: str, inquiry_id: str) -> bool:
    with store.db.read() as connection:
        return connection.execute(
            "SELECT 1 FROM messages WHERE task_id=? AND inquiry_id=?", (task_id, inquiry_id)
        ).fetchone() is not None


def _mark_unavailable(store: BoardStore, task_id: str, inquiry_id: str, code: str) -> str | None:
    """Record a refusal only for a question the blackboard already owns."""
    if not _known_question(store, task_id, inquiry_id):
        return "journal-question-unknown"
    reason = (make_reason("inquiry-observe-only") if code == "unsupported"
              else make_reason("bridge-refused", error=code))
    store._message_update({"runId": task_id, "inquiryId": inquiry_id,
                           "actor": "live-bridge", "state": "unavailable"}, reason=reason)
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


def _apply_bridge_answer(store: BoardStore, task_id: str, inquiry_id: str, value: dict, result: dict | None) -> str | None:
    return _apply_inquiry_record(store, task_id, inquiry_id, value, actor="live-bridge")


def normalize_journal_answer(record: dict) -> dict | None:
    """Read the bridge's nested answer object."""
    answer = record.get("answer")
    if not isinstance(answer, dict):
        return None
    text = answer.get("text")
    if not isinstance(text, str) or not text.strip():
        return None
    return {
        "text": text,
        "bytes": answer.get("bytes") if isinstance(answer.get("bytes"), int) else len(text.encode("utf-8")),
        "via": answer.get("via"),
        "toolCallId": answer.get("toolCallId"),
        "at": answer.get("at"),
        "truncated": answer.get("truncated") is True,
        "source": "bridge-journal",
    }


def _apply_journal(store: BoardStore, task_id: str, inquiry_id: str, record: dict) -> str | None:
    return _apply_inquiry_record(store, task_id, inquiry_id, record, actor="bridge-journal")


def _apply_inquiry_record(store: BoardStore, task_id: str, inquiry_id: str, record: dict, *, actor: str) -> str | None:
    if not _known_question(store, task_id, inquiry_id):
        return "journal-question-unknown"
    patch: dict[str, Any] = {"runId": task_id, "inquiryId": inquiry_id, "actor": actor}
    if isinstance(record.get("delivery"), dict):
        patch["delivery"] = record["delivery"]
    answer = normalize_journal_answer(record)
    if answer is not None:
        patch["answer"] = {**answer, "source": actor}
    state = record.get("state")
    reason = None
    if isinstance(state, str):
        if state == "answered" and answer is None:
            patch["state"] = "delivered"
            reason = make_reason("inquiry-answer-empty")
        else:
            patch["state"] = state
            if state == "unavailable":
                reason = make_reason("inquiry-root-turn-ended")
    if patch.keys() - {"runId", "inquiryId", "actor"}:
        store._message_update(patch, reason=reason)
    return None


def _activity_metadata(value: Any) -> list[dict]:
    """Publish only event metadata; native argument previews are never progress."""
    if not isinstance(value, list):
        return []
    fields = {"phase", "kind", "tool", "toolName", "callId", "at", "seq", "durationMs", "isError"}
    return [{key: item[key][:200] if isinstance(item[key], str) else item[key]
             for key in fields & item.keys() if item[key] is None or isinstance(item[key], (str, int, bool))}
            for item in value[-20:] if isinstance(item, dict)]


def _live(value: dict) -> dict:
    if value.get("ready") is not True:
        return {
            "available": False,
            "reason": value.get("error"),
            "unavailable": ["sessionId", "agentStatus", "inbox", "lastEvent", "activity", "replyTool"],
            "limits": LIMITS,
        }
    return {
        "available": True,
        "reason": None,
        "observedAt": value.get("observedAt"),
        "sessionId": value.get("sessionId"),
        "agentStatus": value.get("agentStatus"),
        "inbox": value.get("inbox"),
        "lastEvent": value.get("lastEvent"),
        "activity": _activity_metadata(value.get("activity")),
        "activityDropped": value.get("activityDropped"),
        "replyTool": value.get("replyTool"),
        "capability": value.get("capability") or ("inquiry" if value.get("replyTool") else "observe"),
        "supported": value.get("supported") is not False,
        "deliveryMode": "cooperative-checkpoint" if value.get("deliveryMode") == "cooperative-checkpoint" else None,
        "journal": value.get("journal"),
        # Bounded, metadata-only evidence that the native harness asked for an
        # interactive capability this adapter cannot grant. It is surfaced verbatim
        # for the Host boundary and never fabricated when absent.
        "attention": value.get("attention") if isinstance(value.get("attention"), (dict, list)) else None,
        "unavailable": value.get("unavailable") or [],
        "limits": LIMITS,
    }
