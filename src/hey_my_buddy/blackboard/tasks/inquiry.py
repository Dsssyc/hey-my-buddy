"""Bounded, read-only observation of one attempt's inquiry bridge.

The ZCode and DSH controllers each bind the shared cooperative inquiry bridge
to their current native run. The socket client is the one shared transport
(:mod:`hey_my_buddy.protocol.inquiry_transport`); this module imports the
journal and projects the bridge's facts onto the durable board message rows,
which stay authoritative — the journal is idempotent transport evidence, and a
missing or unreachable bridge is reported honestly instead of being invented.

Two capabilities come from the adapter registry, never from a second hard-coded
adapter list here: ``inquiry`` means correlated questions are genuinely supported;
``observe`` means bounded read-only native activity is published.

Since ADR-025 step 2-C2 a harness whose registered run module declares a live
binding (ZCode today) is observed and asked through its LiveChannel, reached
over the role seam with the run's own stored request identity; the direct
transport remains the path of every other harness and of every execution whose
request is absent or foreign, so finished and older executions keep reading
their durable evidence by the existing rules and no native program is woken.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from ...protocol import schemas
from ...errors import BoardError
from ..store.store import BoardStore
from ...private_dirs import attempt_root
from ...protocol.inquiry_transport import (
    BRIDGE_ERRORS,
    DEFAULT_TRANSPORT_TIMEOUT_MS,
    MAX_TRANSPORT_TIMEOUT_MS,
    MIN_TRANSPORT_TIMEOUT_MS,
    bridge_request,
)
# The shared live frames of the registered-seam path (ADR-025 step 2-C2): the
# board builds and reads the common channel formats, never a specific harness.
from ...buddy.harnesses.live import MAX_OBSERVE_LIMIT, InquiryPayload, LiveRequest

MAX_WAIT_MS = 30000
MAX_JOURNAL_BYTES = 1024 * 1024
MAX_REASON_CHARS = 400
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

NOTE = (
    "Inquiry is bounded, read-only observation plus correlated questions. It never cancels, restarts, "
    "re-scopes or extends the attempt, and observation is not a percentage or a guaranteed ETA: fields the "
    "bridge cannot see are named in live.unavailable instead of being reported as zero. Repeating an inquiryId "
    "never injects the question twice; the same id with different text is a CONFLICT."
)


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


def inquiry_credentials(directory: Path) -> dict | None:
    try:
        value = json.loads((Path(directory) / "inquiry.json").read_text())
    except (OSError, ValueError):
        return None
    return value if isinstance(value, dict) else None


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


def _deadline(view: dict) -> dict:
    created = view.get("createdAt")
    timeout_seconds = view.get("timeoutSeconds")
    if type(timeout_seconds) is int and timeout_seconds == schemas.UNLIMITED_TIMEOUT_SECONDS:
        return {"available": False, "unlimited": True, "reason": "this execution has no deadline"}
    if not created or not isinstance(timeout_seconds, int):
        return {"available": False, "reason": "no recorded deadline for this task"}
    from datetime import datetime, timezone

    try:
        started = datetime.fromisoformat(created.replace("Z", "+00:00"))
    except ValueError:
        return {"available": False, "reason": "unreadable createdAt"}
    deadline = started.timestamp() + timeout_seconds
    now = datetime.now(timezone.utc).timestamp()
    return {
        "estimated": True,
        "exact": False,
        "kind": "estimated-runner-deadline-from-record-createdAt",
        "clockOrigin": "task record createdAt (before the adapter spawned anything)",
        "deadlineBasis": "createdAt + timeoutSeconds",
        "exactTimingAvailable": False,
        "timeoutSeconds": timeout_seconds,
        "startedAt": created,
        "deadlineAt": datetime.fromtimestamp(deadline, timezone.utc).isoformat().replace("+00:00", "Z"),
        "elapsedSeconds": round(max(0.0, now - started.timestamp()), 1),
        "remainingSeconds": round(max(0.0, deadline - now), 1),
        "expired": now >= deadline,
    }


def observe(store: BoardStore, params: dict) -> dict:
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
    credentials = (inquiry_credentials(attempt_root(store.directory, adapter, view["taskId"], attempt["attemptId"]))
                   if adapter in {"dsh", "zcode"} and attempt.get("attemptId") else None)
    if credentials is None:
        credentials = _credentials_from_log_paths(attempt)
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

    terminal = view["state"] in ("completed", "failed", "cancelled")
    can_ask = inquiry_capable(adapter)
    can_observe = observe_capable(adapter)
    from ...buddy.harnesses.registry import live_binding

    extracted_live = live_binding(adapter) is not None
    channel = (None if terminal or credentials is None
               else _live_channel(store, view, attempt, adapter, credentials))
    # An extracted harness's live path has no direct fallback: a live run whose
    # stored request does not verify reports an unbound channel, retried on the
    # next inquiry. Only unextracted harnesses bridge directly, and terminal
    # journal recovery keeps its existing file read.
    unbound_live = extracted_live and not terminal and channel is None
    bridge = {"enabled": credentials is not None, "observed": False, "reason": None, "error": None,
              "canObserve": can_observe, "canAsk": can_ask}
    live: dict[str, Any] = {
        "available": False,
        "reason": "no live bridge for this attempt",
        "unavailable": ["sessionId", "agentStatus", "inbox", "lastEvent", "activity", "replyTool"],
        "limits": LIMITS,
    }
    if not can_observe:
        bridge["reason"] = "this adapter has no observation or inquiry capability"
    elif terminal:
        bridge["reason"] = "the attempt is terminal; an idle or finished agent cannot be woken"
    elif credentials is None:
        bridge["reason"] = "the attempt has no inquiry bridge credentials yet"
    elif inquiry_id is not None and not can_ask:
        # The adapter can be observed but cannot ask: record the honest capability
        # refusal instead of sending a question the native protocol cannot carry.
        bridge["reason"] = "this adapter observes native activity but has no correlated inquiry capability"
        bridge["capability"] = "observe"
        _mark_unavailable(store, view["taskId"], inquiry_id, "unsupported", bridge["reason"])
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
                # and its native reason, never a transport refusal.
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
        bridge["reason"] = "the attempt's stored run request does not verify; the live channel stays unbound"
    else:
        if inquiry_id is not None:
            result = bridge_request(credentials, "ask", {"inquiryId": inquiry_id, "question": question}, timeout_ms=timeout_ms)
        else:
            result = bridge_request(credentials, "observe", {}, timeout_ms=timeout_ms)
        if result.get("ok"):
            bridge["observed"] = True
            if inquiry_id is None:
                live = _live(result["value"])
            else:
                _apply_bridge_answer(store, view["taskId"], inquiry_id, result["value"], result)
        else:
            bridge["reason"] = result.get("reason")
            bridge["error"] = result.get("code")
            if inquiry_id is not None and result.get("code") in TERMINAL_BRIDGE_ERRORS:
                # The owned turn already ended: the question is refused, never
                # injected into a new or idle turn and never left looking pending.
                _mark_unavailable(store, view["taskId"], inquiry_id, result.get("code"))
        live = _live(result.get("value")) if result.get("ok") and inquiry_id is None else live

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
        journal = read_journal(credentials.get("resultsPath") if credentials else str(evidence_journal) if evidence_journal else None)
    if inquiry_id is not None and channel is not None:
        if inquiry_id in rejections:
            # A record the binding refused under this very question is the
            # public rejection it always was, never a silent loss.
            bridge["journalRejected"] = rejections[inquiry_id]
        elif inquiry_id in projected:
            record = _journal_record(projected[inquiry_id])
            mismatch = _journal_identity_mismatch(record, view["taskId"], attempt.get("attemptId"))
            if mismatch is not None:
                bridge["journalRejected"] = mismatch
            else:
                _apply_journal(store, view["taskId"], inquiry_id, record)
                bridge["journalImported"] = True
    elif inquiry_id is not None and not unbound_live and inquiry_id in journal["entries"]:
        record = journal["entries"][inquiry_id]
        mismatch = _journal_identity_mismatch(record, view["taskId"], attempt.get("attemptId"))
        if mismatch is not None:
            # A journal record bound to another task/attempt is transport evidence
            # for a different execution; it can never answer this run's question.
            bridge["journalRejected"] = mismatch
        else:
            _apply_journal(store, view["taskId"], inquiry_id, record)
            bridge["journalImported"] = True

    if inquiry_id is not None and wait_ms:
        deadline = time.monotonic() + wait_ms / 1000.0
        while time.monotonic() < deadline:
            current = store.message_get({"runId": view["taskId"], "inquiryId": inquiry_id})["message"]
            if current["state"] in ("answered", "delivered", "discarded", "unavailable"):
                break
            if credentials is None or unbound_live:
                break
            if channel is not None:
                # The wait queries only this one question's native answer, the
                # same single roundtrip the direct path made.
                answer_view = channel.observe(inquiry_id=inquiry_id, timeout_ms=timeout_ms)
                if answer_view.inquiries:
                    _apply_bridge_answer(store, view["taskId"], inquiry_id,
                                         _live_answer_value(answer_view.inquiries[0]), None)
                    break
            else:
                result = bridge_request(credentials, "answer", {"inquiryId": inquiry_id}, timeout_ms=timeout_ms)
                if result.get("ok"):
                    _apply_bridge_answer(store, view["taskId"], inquiry_id, result["value"], result)
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
            "workerId": attempt.get("workerId"),
        },
        "deadline": _deadline(view),
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
                                else len(journal["entries"]))},
        "limits": LIMITS,
        "note": NOTE,
    }


def _live_channel(store: BoardStore, view: dict, attempt: dict, adapter: str, credentials: dict | None):
    """The live channel of one active registered harness run, or ``None``.

    The channel exists only for a harness whose registered run module declares
    a live binding, only while the attempt is live, and only when the stored
    public request verifies against the run's own private control binding and
    governed turn input (the helper checks invocation, turn id and input
    digest) and carries this attempt's task, attempt and generation. ``None``
    for such a run is an unbound channel, which the caller reports honestly —
    the direct bridge is never a fallback for an extracted harness; it stays
    only for unextracted ones and for terminal journal recovery.
    """
    if credentials is None or not attempt.get("attemptId"):
        return None
    try:
        from ...buddy.harnesses.registry import live_binding

        if live_binding(adapter) is None:
            return None
        from ...buddy.roles import live as role_live

        request = role_live.stored_run_request(
            attempt_root(store.directory, adapter, view["taskId"], attempt["attemptId"]))
    except (OSError, ValueError, BoardError, RecursionError, KeyError):
        return None
    if (request is None or request.harness != adapter
            or request.identity.task_id != view["taskId"]
            or request.identity.attempt_id != attempt.get("attemptId")
            or request.identity.generation != attempt.get("generation")):
        return None
    try:
        from ...buddy.roles import live as role_live

        return role_live.build_live_channel(
            adapter, request, credentials=credentials,
            activity_dir=store.directory / "attempts" / view["taskId"] / attempt["attemptId"],
            journal_path=credentials.get("resultsPath"))
    except (BoardError, TypeError, ValueError):
        return None


def _live_ask_value(reply) -> dict:
    """The ask reply's committed value, in the bridge's own ask-value shape.

    An observed reply projects its actual committed state — a withdrawn
    question's ``discarded`` included — with the bridge's own reason and
    delivery record from the correlation.
    """
    correlation = reply.native_correlation.value if reply.native_correlation is not None else {}
    value: dict[str, Any] = {"state": reply.state or reply.status}
    if isinstance(correlation.get("reason"), str) and correlation["reason"]:
        value["reason"] = correlation["reason"]
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
        value["answer"] = {"available": False, "reason": entry.reason or "no correlated answer yet"}
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
    if entry.reason is not None:
        record["reason"] = entry.reason
    if entry.limitation is not None:
        record["limitation"] = entry.limitation
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
            return f"the journal record belongs to another {label}"
    return None


def _mark_unavailable(store: BoardStore, task_id: str, inquiry_id: str, code: str, reason: str | None = None) -> None:
    """Record a question refused because the owned agent cannot receive it."""
    try:
        store.message_update(
            {
                "runId": task_id,
                "inquiryId": inquiry_id,
                "actor": "live-bridge",
                "state": "unavailable",
            "reason": (reason or f"the bridge cannot deliver this question ({code})")[:MAX_REASON_CHARS],
            }
        )
    except BoardError:
        pass


def _credentials_from_log_paths(attempt: dict) -> dict | None:
    paths = attempt.get("logPaths") or {}
    if not isinstance(paths, dict):
        return None
    socket_path = paths.get("inquirySocket") or paths.get("socketPath")
    if not isinstance(socket_path, str) or not socket_path:
        return None
    return {
        "socketPath": socket_path,
        "resultsPath": paths.get("inquiryResults") or paths.get("resultsPath"),
        "token": paths.get("inquiryToken"),
    }


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
            {"available": True, **answer} if answer else {"available": False, "reason": message.get("reason") or "no correlated answer yet"}
        ),
        "correlation": "inquiryId",
    }


def _apply_bridge_answer(store: BoardStore, task_id: str, inquiry_id: str, value: dict, result: dict) -> None:
    if not isinstance(value, dict):
        return
    record = value if "answer" in value else {**value, "answer": value.get("answer")}
    answer = normalize_journal_answer(record)
    patch: dict[str, Any] = {"runId": task_id, "inquiryId": inquiry_id, "actor": "live-bridge"}
    if answer is not None:
        patch["answer"] = {**answer, "source": "live-bridge"}
    state = value.get("state")
    if isinstance(state, str):
        if state == "answered" and answer is None:
            patch["state"] = "delivered"
            patch["reason"] = "the live bridge reported answered without usable answer text"
        else:
            patch["state"] = state
            reason = value.get("reason")
            if state != "answered" and isinstance(reason, str) and reason.strip():
                # A bridge may report a refusal inside a successful value (the dsh
                # bridge reports agent-gone/agent-not-running this way). Keep the
                # honest reason instead of a bare terminal state.
                patch["reason"] = reason[:200]
    if isinstance(value.get("delivery"), dict):
        patch["delivery"] = value["delivery"]
    if patch.keys() - {"runId", "inquiryId", "actor"}:
        try:
            store.message_update(patch)
        except BoardError:
            pass


def normalize_journal_answer(record: dict) -> dict | None:
    """Normalize one journal record's answer into the board's answer shape.

    The Node bridge writes the answer text as a string with ``via``, ``toolCallId``,
    ``answeredAt``, ``answerBytes``, ``truncated`` and ``messageId`` as sibling
    fields, while the live socket returns a nested object. Both are accepted here so
    an answered record can never be recorded without its correlated answer text and
    its reply-tool evidence.
    """
    answer = record.get("answer")
    if isinstance(answer, dict):
        text = answer.get("text")
        if not isinstance(text, str) or not text.strip():
            return None
        return {
            "text": text,
            "bytes": answer.get("bytes") if isinstance(answer.get("bytes"), int) else len(text.encode("utf-8")),
            "via": answer.get("via") if isinstance(answer.get("via"), str) else _as_text(record.get("via")),
            "toolCallId": answer.get("toolCallId")
            if isinstance(answer.get("toolCallId"), str)
            else _as_text(record.get("toolCallId")),
            "at": answer.get("at") if isinstance(answer.get("at"), str) else _as_text(record.get("answeredAt")),
            "truncated": answer.get("truncated") is True or record.get("truncated") is True,
            "source": "bridge-journal",
        }
    if isinstance(answer, str) and answer.strip():
        return {
            "text": answer,
            "bytes": record.get("answerBytes") if isinstance(record.get("answerBytes"), int) else len(answer.encode("utf-8")),
            "via": _as_text(record.get("via")),
            "toolCallId": _as_text(record.get("toolCallId")),
            "at": _as_text(record.get("answeredAt")),
            "truncated": record.get("truncated") is True,
            "source": "bridge-journal",
        }
    return None


def _as_text(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _apply_journal(store: BoardStore, task_id: str, inquiry_id: str, record: dict) -> None:
    patch: dict[str, Any] = {"runId": task_id, "inquiryId": inquiry_id, "actor": "bridge-journal"}
    if isinstance(record.get("delivery"), dict):
        patch["delivery"] = record["delivery"]
    if record.get("messageId") is not None and isinstance(record.get("messageId"), str):
        patch["delivery"] = {**(patch.get("delivery") or {}), "messageId": record["messageId"]}
    answer = normalize_journal_answer(record)
    if answer is not None:
        patch["answer"] = answer
    # ``answered`` is only recorded together with a normalized answer: an answered
    # record without usable text stays at its previous state with an explicit reason
    # instead of becoming a half-answered question.
    if isinstance(record.get("state"), str):
        if record["state"] == "answered" and answer is None:
            patch["reason"] = "the bridge recorded an answered journal entry without usable answer text"
            patch["state"] = "delivered"
        else:
            patch["state"] = record["state"]
            # A terminal refusal carries the bridge's own bounded reason; keep it
            # instead of a generic "unavailable" with no explanation.
            reason = record.get("limitation") or record.get("reason")
            if record["state"] == "unavailable" and isinstance(reason, str) and reason.strip():
                patch["reason"] = reason[:MAX_REASON_CHARS]
    if patch.keys() - {"runId", "inquiryId", "actor"}:
        try:
            store.message_update(patch)
        except BoardError:
            pass


def _activity_metadata(value: Any) -> list[dict]:
    """Publish only event metadata; native argument previews are never progress."""
    if not isinstance(value, list):
        return []
    fields = {"phase", "kind", "tool", "toolName", "callId", "at", "seq", "durationMs", "isError"}
    return [{key: item[key][:200] if isinstance(item[key], str) else item[key]
             for key in fields & item.keys() if item[key] is None or isinstance(item[key], (str, int, bool))}
            for item in value[-20:] if isinstance(item, dict)]


def _live(value: Any) -> dict:
    if not isinstance(value, dict) or value.get("ready") is not True:
        return {
            "available": False,
            "reason": (value or {}).get("error") if isinstance(value, dict) else "the bridge did not report readiness",
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
        "limitation": value.get("limitation") if isinstance(value.get("limitation"), str) else None,
        "journal": value.get("journal"),
        # Bounded, metadata-only evidence that the native harness asked for an
        # interactive capability this adapter cannot grant. It is surfaced verbatim
        # for the Host boundary and never fabricated when absent.
        "attention": value.get("attention") if isinstance(value.get("attention"), (dict, list)) else None,
        "unavailable": value.get("unavailable") or [],
        "limits": LIMITS,
    }
