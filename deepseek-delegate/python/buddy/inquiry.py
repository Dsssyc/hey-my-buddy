"""Bounded, read-only observation of one dsh attempt's inquiry bridge.

The bridge socket itself lives in the Node dsh plugin, because upstream's plugin
runs in Node. This module is the Python client of that socket plus the importer of
its journal: the durable board message rows stay authoritative, the journal is
idempotent transport evidence, and a missing or unreachable bridge is reported
honestly instead of being invented.
"""
from __future__ import annotations

import json
import socket
import time
import uuid
from pathlib import Path
from typing import Any

from . import schemas
from .errors import BoardError
from .store import BoardStore

PROTOCOL_VERSION = 1
DEFAULT_TRANSPORT_TIMEOUT_MS = 1500
MIN_TRANSPORT_TIMEOUT_MS = 100
MAX_TRANSPORT_TIMEOUT_MS = 5000
MAX_WAIT_MS = 30000
MAX_RESPONSE_BYTES = 64 * 1024
MAX_JOURNAL_BYTES = 1024 * 1024
POLL_INTERVAL_SECONDS = 0.3

BRIDGE_ERRORS = (
    "bad-request",
    "unauthorized",
    "frame-too-large",
    "timeout",
    "unsupported-method",
    "not-ready",
    "agent-gone",
    "agent-not-running",
    "conflict",
    "too-many",
    "internal",
)

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


def inquiry_credentials(directory: Path) -> dict | None:
    try:
        value = json.loads((Path(directory) / "inquiry.json").read_text())
    except (OSError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def bridge_request(credentials: dict, method: str, payload: dict, *, timeout_ms: int = DEFAULT_TRANSPORT_TIMEOUT_MS) -> dict:
    """One bounded newline-terminated JSON frame over the bridge's Unix socket."""
    socket_path = credentials.get("socketPath")
    if not isinstance(socket_path, str) or not socket_path:
        return {"ok": False, "reason": "bridge-unreachable"}
    frame = {
        "version": PROTOCOL_VERSION,
        "id": str(uuid.uuid4()),
        "token": credentials.get("token"),
        "method": method,
        **payload,
    }
    raw = json.dumps(frame, ensure_ascii=False).encode("utf-8") + b"\n"
    if len(raw) > 16 * 1024:
        return {"ok": False, "reason": "bridge-response-too-large"}
    connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    connection.settimeout(max(MIN_TRANSPORT_TIMEOUT_MS, min(timeout_ms, MAX_TRANSPORT_TIMEOUT_MS)) / 1000.0)
    try:
        connection.connect(socket_path)
        connection.sendall(raw)
        chunks = bytearray()
        while b"\n" not in chunks:
            block = connection.recv(4096)
            if not block:
                break
            chunks.extend(block)
            if len(chunks) > MAX_RESPONSE_BYTES:
                return {"ok": False, "reason": "bridge-response-too-large"}
    except FileNotFoundError:
        return {"ok": False, "reason": "bridge-unreachable"}
    except ConnectionRefusedError:
        return {"ok": False, "reason": "bridge-unreachable"}
    except TimeoutError:
        return {"ok": False, "reason": "bridge-timeout"}
    except OSError:
        return {"ok": False, "reason": "bridge-write-failed"}
    finally:
        connection.close()
    try:
        reply = json.loads(bytes(chunks).split(b"\n", 1)[0])
    except ValueError:
        return {"ok": False, "reason": "bridge-invalid-response"}
    if not isinstance(reply, dict) or reply.get("id") != frame["id"]:
        return {"ok": False, "reason": "bridge-mismatched-response"}
    if reply.get("ok") is not True:
        code = reply.get("error")
        return {"ok": False, "reason": "bridge-refused", "code": code if code in BRIDGE_ERRORS else "internal"}
    return {"ok": True, "value": reply.get("value")}


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
    credentials = inquiry_credentials(store.directory / "attempts" / view["taskId"] / attempt.get("attemptId", ""))
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
    bridge = {"enabled": credentials is not None, "observed": False, "reason": None, "error": None}
    live: dict[str, Any] = {
        "available": False,
        "reason": "no live bridge for this attempt",
        "unavailable": ["sessionId", "agentStatus", "inbox", "lastEvent", "activity", "replyTool"],
        "limits": LIMITS,
    }
    if adapter != "dsh":
        bridge["reason"] = "this adapter has no inquiry capability"
    elif terminal:
        bridge["reason"] = "the attempt is terminal; an idle or finished agent cannot be woken"
    elif credentials is None:
        bridge["reason"] = "the attempt has no inquiry bridge credentials yet"
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
        live = _live(result.get("value")) if result.get("ok") and inquiry_id is None else live

    journal = read_journal(credentials.get("resultsPath") if credentials else None)
    if inquiry_id is not None and inquiry_id in journal["entries"]:
        record = journal["entries"][inquiry_id]
        _apply_journal(store, view["taskId"], inquiry_id, record)
        bridge["journalImported"] = True

    if inquiry_id is not None and wait_ms:
        deadline = time.monotonic() + wait_ms / 1000.0
        while time.monotonic() < deadline:
            current = store.message_get({"runId": view["taskId"], "inquiryId": inquiry_id})["message"]
            if current["state"] in ("answered", "delivered", "discarded", "unavailable"):
                break
            if credentials is None:
                break
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
        "journal": {"available": journal["available"], "reason": journal["reason"], "entries": len(journal["entries"])},
        "limits": LIMITS,
        "note": NOTE,
    }


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
    if patch.keys() - {"runId", "inquiryId", "actor"}:
        try:
            store.message_update(patch)
        except BoardError:
            pass


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
        "activity": (value.get("activity") or [])[:20],
        "activityDropped": value.get("activityDropped"),
        "replyTool": value.get("replyTool"),
        "journal": value.get("journal"),
        "unavailable": value.get("unavailable") or [],
        "limits": LIMITS,
    }
