"""Bounded Codex App Server JSONL transport and native turn observation."""
from __future__ import annotations

import hashlib
import json
import os
import queue
import select
import threading
import time
from decimal import Decimal, InvalidOperation
from datetime import datetime, timezone

from .turn_io import canonical_json

MAX_FRAME_BYTES = 8 * 1024 * 1024
MAX_CHECKPOINT_MESSAGE_BYTES = 65536
_WINDOWS_PIPE = os.name == "nt"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class CodexProtocolError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def decode_json(raw: bytes | str) -> object:
    def pairs(items):
        value = {}
        for key, item in items:
            if key in value:
                raise ValueError("duplicate JSON member")
            value[key] = item
        return value
    return json.loads(raw, object_pairs_hook=pairs,
                      parse_constant=lambda _value: (_ for _ in ()).throw(ValueError("non-finite JSON")))


class Connection:
    def __init__(self, process, deadline: float, cancelled: threading.Event):
        self.process, self.deadline, self.cancelled = process, deadline, cancelled
        self.messages: queue.Queue = queue.Queue(maxsize=128)
        self.responses: dict[int, dict] = {}
        self.next_id = 1
        self.strict_responses = False
        self.pending_ids: set[int] = set()
        self.on_notification = lambda _message: None
        self.on_request = lambda _message: None
        os.set_blocking(process.stdin.fileno(), False)
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self):
        try:
            while raw := self.process.stdout.readline(MAX_FRAME_BYTES + 1):
                if len(raw) > MAX_FRAME_BYTES:
                    raise ValueError("oversized native frame")
                message = decode_json(raw)
                if not isinstance(message, dict):
                    raise ValueError("native frame is not an object")
                self.messages.put(message)
        except (OSError, ValueError, RecursionError):
            self.messages.put(CodexProtocolError("invalid-protocol", "Codex emitted invalid or oversized JSON"))
        finally:
            self.messages.put(None)

    def _remaining(self):
        if self.cancelled.is_set():
            raise CodexProtocolError("user-cancel", "the Codex execution was cancelled")
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise CodexProtocolError("deadline", "the Codex execution exceeded its deadline")
        return remaining

    def send(self, message: dict):
        raw = memoryview((canonical_json(message) + "\n").encode())
        if len(raw) > MAX_FRAME_BYTES:
            raise CodexProtocolError("invalid-protocol", "native request exceeds its byte bound")
        try:
            while raw:
                remaining = self._remaining()
                try:
                    size = os.write(self.process.stdin.fileno(), raw)
                    raw = raw[size:]
                except BlockingIOError:
                    if _WINDOWS_PIPE:
                        self.cancelled.wait(min(0.05, remaining))
                    else:
                        select.select([], [self.process.stdin.fileno()], [], min(0.1, remaining))
        except OSError:
            raise CodexProtocolError("transport-error", "Codex input closed") from None

    def pump(self):
        remaining = self._remaining()
        try:
            message = self.messages.get(timeout=min(remaining, 0.2))
        except queue.Empty:
            return
        if message is None:
            raise CodexProtocolError("transport-error", "Codex closed before the request settled")
        if isinstance(message, CodexProtocolError):
            raise message
        if "id" in message and "method" in message:
            self.on_request(message)
        elif "id" in message:
            if type(message["id"]) is not int or len(self.responses) >= 16:
                raise CodexProtocolError("invalid-protocol", "invalid native response identity")
            if self.strict_responses and (message["id"] not in self.pending_ids or message["id"] in self.responses):
                raise CodexProtocolError("invalid-protocol", "uncorrelated or duplicate native sandbox reply")
            self.responses[message["id"]] = message
        elif isinstance(message.get("method"), str):
            self.on_notification(message)
        else:
            raise CodexProtocolError("invalid-protocol", "unrecognized native message")

    def call(self, method: str, params: dict) -> dict:
        request_id = self.next_id
        self.next_id += 1
        self.pending_ids.add(request_id)
        self.send({"id": request_id, "method": method, "params": params})
        while request_id not in self.responses:
            self.pump()
        response = self.responses.pop(request_id)
        self.pending_ids.discard(request_id)
        if "error" in response:
            raise CodexProtocolError("native-rpc-error", f"Codex rejected {method}")
        result = response.get("result")
        if not isinstance(result, dict):
            raise CodexProtocolError("invalid-protocol", f"Codex returned no object for {method}")
        return result


_REQUEST_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["summary", "attempted", "neededWork", "expectedArtifacts", "acceptance"],
    "properties": {"summary": {"type": "string"}, "attempted": {"type": "string"},
                   "neededWork": {"type": "string"}, "expectedArtifacts": {"type": "array", "items": {"type": "string"}},
                   "acceptance": {"type": "string"}},
}


def _outcome_branch(dispositions, request_schema):
    return {
        "type": "object", "additionalProperties": False,
        "required": ["disposition", "summary", "remaining", "decisions", "artifacts", "request"],
        "properties": {
            "disposition": {"type": "string", "enum": dispositions},
            "summary": {"type": "string", "description": "Nonblank report; the entire serialized outcome must fit in 64 KiB of UTF-8. Keep requests and references concise."},
            "remaining": {"type": "array", "items": {"type": "string"}},
            "decisions": {"type": "array", "items": {"type": "string"}},
            "artifacts": {"type": "array", "items": {"type": "string"}},
            "request": request_schema,
        },
    }


# Structured Outputs permits a nested union, not a root union. The tagged
# branches prevent a "completed" result from carrying an unresolved request.
OUTCOME_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["outcome"],
    "properties": {"outcome": {"anyOf": [
        _outcome_branch(["completed"], {"type": "null"}),
        _outcome_branch(["assistance", "attention"], _REQUEST_SCHEMA),
    ]}},
}


def parse_outcome(text: str) -> dict:
    from .turn_io import validate_outcome
    value = decode_json(text)
    if not isinstance(value, dict) or set(value) != {"outcome"}:
        raise ValueError("The native result must contain exactly the structured outcome")
    outcome = value["outcome"]
    error = validate_outcome(outcome)
    if error:
        raise ValueError(error)
    return outcome


def native_checkpoint(evidence, turn_input: dict) -> dict:
    """Native history is evidence for continuation, never a workflow outcome."""
    from .turn_io import input_hash
    checkpoint = {key: turn_input[key] for key in ("taskId", "attemptId", "generation", "turnId")}
    checkpoint.update(version=1, inputSha256=input_hash(turn_input), sessionId=evidence.thread_id,
                      nativeTurnId=evidence.turn_id, nativeTurnStarted=evidence.started,
                      nativeTurnStatus=(evidence.completed or {}).get("status", "incomplete"), eventSeq=evidence.event_seq,
                      bindingSaved=False)
    item = evidence.final_item or getattr(evidence, "last_agent_item", None)
    if isinstance(item, dict) and isinstance(item.get("id"), str) and isinstance(item.get("text"), str):
        raw = item["text"].encode()
        text = raw[:MAX_CHECKPOINT_MESSAGE_BYTES].decode("utf-8", errors="ignore")
        # Bound the serialized string too: escaping can multiply its byte size.
        while len(canonical_json(text).encode()) > MAX_CHECKPOINT_MESSAGE_BYTES:
            text = text[:len(text) // 2]
        checkpoint["lastAssistantMessage"] = {"itemId": item["id"], "text": text, "phase": item.get("phase"), "sourceBytes": len(raw),
                                      "sha256": hashlib.sha256(raw).hexdigest(), "truncated": text != item["text"]}
    return checkpoint


def validated_checkpoint(payload: dict, turn_input: dict) -> dict | None:
    """Read only a stopped, exact-attempt native observation from its receipt."""
    from .turn_io import input_hash
    value = payload.get("nativeCheckpoint")
    if not isinstance(value, dict) or value.get("version") != 1:
        return None
    expected = {key: turn_input[key] for key in ("taskId", "attemptId", "generation", "turnId")}
    expected["inputSha256"] = input_hash(turn_input)
    if any(type(value.get(key)) is not type(item) or value.get(key) != item for key, item in expected.items()):
        return None
    process = payload.get("processState")
    if (not isinstance(process, dict) or process.get("shutdownConfirmed") is not True
            or value.get("nativeTurnStarted") is not True
            or value.get("nativeTurnStatus") not in ("completed", "failed", "interrupted", "incomplete")
            or type(value.get("bindingSaved")) is not bool
            or type(value.get("eventSeq")) is not int or value["eventSeq"] < 2):
        return None
    for key in ("sessionId", "nativeTurnId"):
        if not isinstance(value.get(key), str) or not value[key] or value[key] != payload.get(key):
            return None
    message = value.get("lastAssistantMessage")
    if message is not None:
        if (not isinstance(message, dict) or not isinstance(message.get("itemId"), str) or not message["itemId"]
                or not isinstance(message.get("text"), str) or type(message.get("truncated")) is not bool
                or type(message.get("sourceBytes")) is not int
                or not isinstance(message.get("sha256"), str) or len(message["sha256"]) != 64
                or len(canonical_json(message["text"]).encode()) > MAX_CHECKPOINT_MESSAGE_BYTES):
            return None
        raw = message["text"].encode()
        if message["sourceBytes"] < len(raw) or (not message["truncated"] and (
                message["sourceBytes"] != len(raw) or hashlib.sha256(raw).hexdigest() != message["sha256"])):
            return None
    return value


def checkpoint_resumable(payload: dict, checkpoint: dict) -> bool:
    process = payload.get("processState")
    return (checkpoint.get("nativeTurnStatus") == "completed" and checkpoint.get("bindingSaved") is True
            and isinstance(process, dict) and type(process.get("nativeExitCode")) is int and process["nativeExitCode"] == 0)


#: One native ``thread/tokenUsage/updated`` breakdown, in camel case.
_USAGE_KEYS = ("inputTokens", "cachedInputTokens", "outputTokens", "reasoningOutputTokens",
               "cacheWriteInputTokens", "totalTokens")
#: Codex ``input_tokens`` already contains ``cached_input_tokens``; these are the
#: fields every breakdown must carry for the delta arithmetic to be exact.
_USAGE_REQUIRED = ("inputTokens", "cachedInputTokens", "outputTokens", "reasoningOutputTokens", "totalTokens")


def _breakdown(value) -> dict | None:
    """One validated native usage breakdown, or ``None`` when it is unusable."""
    if not isinstance(value, dict):
        return None
    counters = {}
    for key in _USAGE_KEYS:
        item = value.get(key)
        if item is None and key not in _USAGE_REQUIRED:
            counters[key] = 0
            continue
        if isinstance(item, bool) or not isinstance(item, int) or item < 0:
            return None
        counters[key] = item
    return counters


def attempt_token_usage(evidence) -> dict | None:
    """This attempt's own Codex usage, never the thread-cumulative total.

    ``thread/tokenUsage/updated`` carries the latest request's breakdown in
    ``last`` and the thread-cumulative breakdown in ``total``. The first
    notification of the bound native turn establishes the baseline
    (``total - last``), and the attempt is the delta from that baseline to the
    latest observed total. A repeated or replayed notification has a total that
    does not advance, so it can never be counted twice. When no baseline is
    provable — missing, or self-contradictory native totals — the usage stays
    unknown instead of being estimated from the cumulative total.
    """
    baseline, totals, events = evidence.usage_baseline, evidence.usage_total, evidence.usage_events
    if baseline is None or totals is None or not events:
        return None
    delta = {}
    for key in _USAGE_KEYS:
        value = totals[key] - baseline[key]
        if value < 0:
            return None
        delta[key] = value
    if delta["cachedInputTokens"] > delta["inputTokens"]:
        return None
    return {
        "source": "codex/app-server-thread-token-usage",
        "scope": "attempt",
        "coverage": "native-root-thread",
        "inputBasis": "includes-cached",
        "inputTokens": delta["inputTokens"],
        "cachedInputTokens": delta["cachedInputTokens"],
        "outputTokens": delta["outputTokens"],
        "reasoningOutputTokens": delta["reasoningOutputTokens"],
        "nativeRecords": evidence.usage_events,
        "completeness": "complete" if not evidence.usage_anomaly and isinstance(evidence.completed, dict) and evidence.completed.get("status") == "completed" else "partial",
    }


#: The native quota slots a rate-limit snapshot may carry.
_RATE_WINDOW_SLOTS = ("primary", "secondary")


def _zero_balance(value) -> bool:
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        return False
    if isinstance(value, str) and (not value or len(value) > 32):
        return False
    try:
        amount = Decimal(str(value))
        return amount.is_finite() and amount == 0
    except InvalidOperation:
        return False


def _quota_candidate(buckets, *, observed_at: str, account_id=None, ordinary_usage_allowed=None) -> dict | None:
    """Build one quota candidate from native rate-limit buckets.

    Only natively reported values are carried. A slot without ``usedPercent``
    contributes no window, and a missing ``resetsAt`` stays unknown rather than
    becoming zero. Multi-bucket snapshots keep their native ``limitId`` on each
    window so two metered buckets can never be merged into one percentage.
    """
    if not buckets:
        return None
    candidate = {"source": "codex/app-server-rate-limits", "observedAt": observed_at,
                 "provider": "openai", "windows": []}
    if len(buckets) > 1:
        candidate["ambiguousLimits"] = True
        from ..usage import classify_quota_code
        def usable(bucket):
            if not isinstance(bucket, dict) or ordinary_usage_allowed is False:
                return False
            if classify_quota_code(bucket.get('rateLimitReachedType') or '') == 'quota-exceeded':
                return False
            windows = [bucket[slot] for slot in _RATE_WINDOW_SLOTS if isinstance(bucket.get(slot), dict)]
            return bool(windows and all(type(w.get('usedPercent')) in (int, float) and 0 <= w['usedPercent'] < 100
                for w in windows))
        if all(usable(bucket) for _limit, bucket in buckets):
            candidate['allLimitsAvailable'] = True
            candidate['coveredLimits'] = [limit_id for limit_id, _bucket in buckets if isinstance(limit_id, str)]
    if isinstance(account_id, str) and account_id:
        candidate["nativeAccountId"] = account_id
    if isinstance(ordinary_usage_allowed, bool):
        candidate["ordinaryUsageAllowed"] = ordinary_usage_allowed
    for limit_id, bucket in buckets:
        if not isinstance(bucket, dict):
            continue
        plan = bucket.get("planType")
        if candidate.get("planType") is None and isinstance(plan, str) and plan:
            candidate["planType"] = plan
        reached = bucket.get("rateLimitReachedType")
        if candidate.get("reachedType") is None and isinstance(reached, str) and reached:
            candidate["reachedType"] = reached
        if candidate.get("limitId") is None and limit_id:
            candidate["limitId"] = limit_id
        credits = bucket.get("credits")
        if (isinstance(credits, dict) and credits.get("hasCredits") is True
                and credits.get("unlimited") is False
                and ordinary_usage_allowed is False
                and _zero_balance(credits.get("balance"))):
            candidate["balanceZero"] = True
        for slot in _RATE_WINDOW_SLOTS:
            window = bucket.get(slot)
            used = window.get("usedPercent") if isinstance(window, dict) else None
            if isinstance(used, bool) or not isinstance(used, (int, float)):
                # A slot without a native percentage is not a window and is never a zero.
                continue
            entry = {"name": slot, "usedPercent": used}
            resets = window.get("resetsAt")
            if isinstance(resets, int) and not isinstance(resets, bool) and resets > 0:
                entry["resetsAt"] = resets
            duration = window.get("windowDurationMins")
            if isinstance(duration, int) and not isinstance(duration, bool) and duration > 0:
                entry["windowDurationMins"] = duration
            if limit_id:
                entry["limitId"] = limit_id
            candidate["windows"].append(entry)
    return candidate


def quota_candidate(snapshot, *, observed_at: str, account_id=None, ordinary_usage_allowed=None) -> dict | None:
    """One native ``RateLimitSnapshot`` (one bucket, as a rolling update carries)."""
    if not isinstance(snapshot, dict):
        return None
    limit_id = snapshot.get("limitId")
    return _quota_candidate([(limit_id if isinstance(limit_id, str) else None, snapshot)],
                            observed_at=observed_at, account_id=account_id,
                            ordinary_usage_allowed=ordinary_usage_allowed)


def quota_candidate_from_response(response, *, observed_at: str) -> dict | None:
    """One ``account/rateLimits/read`` response as an unnormalized candidate.

    The multi-bucket view is preferred when the backend supplies it; otherwise
    the backward-compatible single view is used. An empty response proves
    nothing and returns ``None`` instead of an empty observation.
    """
    if not isinstance(response, dict):
        return None
    buckets: list[tuple[str | None, dict]] = []
    by_id = response.get("rateLimitsByLimitId")
    single = response.get("rateLimits") if isinstance(response.get("rateLimits"), dict) else None
    if isinstance(by_id, dict) and by_id:
        buckets = [(str(limit_id), by_id[limit_id]) for limit_id in sorted(by_id)]
    elif single is not None:
        limit_id = single.get("limitId")
        buckets = [(limit_id if isinstance(limit_id, str) else None, single)]
    return _quota_candidate(buckets, observed_at=observed_at, account_id=response.get("accountId"),
                            ordinary_usage_allowed=response.get("ordinaryUsageAllowed"))


def turn_failure_code(turn) -> str | None:
    """The native structured error code of a failed native turn, when it has one."""
    error = turn.get("error") if isinstance(turn, dict) else None
    info = error.get("codexErrorInfo") if isinstance(error, dict) else None
    return info if isinstance(info, str) and info else None

class TurnEvidence:
    def __init__(self, thread_id: str, turn_id: str):
        self.thread_id, self.turn_id = thread_id, turn_id
        self.started = False
        self.completed = None
        self.final_item = None
        self.last_agent_item = None
        self.event_seq = 0
        self.model_turns = 0
        self.tool_calls = 0
        # Per-turn native token usage: the observed baseline and latest thread
        # totals, never a session-cumulative figure reported as one attempt.
        self.usage_baseline = None
        self.usage_total = None
        self.usage_events = 0
        self.usage_anomaly = False
        self.usage_unprovable = False
        # The most recent non-empty native quota observation for this execution.
        self.quota_candidate = None

    def _observe_token_usage(self, token_usage) -> None:
        """Fold one native usage notification into this attempt's delta."""
        if not isinstance(token_usage, dict) or self.usage_unprovable:
            return
        last = _breakdown(token_usage.get("last"))
        total = _breakdown(token_usage.get("total"))
        if last is None or total is None:
            return
        if self.usage_baseline is None:
            baseline = {key: total[key] - last[key] for key in _USAGE_KEYS}
            if any(value < 0 for value in baseline.values()):
                # The native totals contradict themselves; no interval is
                # provable, so this attempt's usage must stay unknown rather
                # than being estimated from the cumulative total.
                self.usage_unprovable = True
                return
            self.usage_baseline = baseline
        if self.usage_total is not None:
            if total["totalTokens"] <= self.usage_total["totalTokens"]:
                # An identical or replayed notification never counts twice.
                return
            if any(total[key] < self.usage_total[key] for key in _USAGE_KEYS):
                self.usage_anomaly = True
        self.usage_total = total
        self.usage_events += 1

    def _observe_rate_limits(self, snapshot) -> None:
        """Keep the newest quota observation a sparse rolling update can prove.

        A rolling update may omit fields; an update that proves nothing replaces
        nothing, so an earlier complete observation is never cleared by a
        partial one.
        """
        from ..usage import normalize_quota
        candidate = quota_candidate(snapshot, observed_at=utc_now())
        if candidate is not None and normalize_quota(candidate) is not None:
            self.quota_candidate = candidate

    def observe(self, message: dict):
        method, params = message.get("method"), message.get("params")
        if not isinstance(params, dict):
            return None
        if method == "account/rateLimits/updated":
            self._observe_rate_limits(params.get("rateLimits"))
            return None
        if params.get("threadId") != self.thread_id:
            return None
        turn = params.get("turn")
        native_turn_id = turn.get("id") if isinstance(turn, dict) else params.get("turnId")
        if native_turn_id != self.turn_id:
            return None
        self.event_seq += 1
        if method == "thread/tokenUsage/updated":
            self._observe_token_usage(params.get("tokenUsage"))
            return "streaming-model", None
        if method == "turn/started":
            self.started = True
            self.model_turns += 1
            return "waiting-model", None
        if method == "item/started":
            item = params.get("item") or {}
            if item.get("type") in ("commandExecution", "fileChange", "mcpToolCall", "dynamicToolCall", "collabAgentToolCall"):
                self.tool_calls += 1
                return "tool-running", item.get("type")
            return "streaming-model", None
        if method == "item/completed":
            item = params.get("item") or {}
            if item.get("type") == "agentMessage":
                self.last_agent_item = item
            if item.get("type") == "agentMessage" and item.get("phase") == "final_answer":
                if self.final_item is not None:
                    raise CodexProtocolError("invalid-result", "multiple native final messages completed")
                self.final_item = item
            return "streaming-model", None
        if method == "turn/completed":
            if self.completed is not None:
                raise CodexProtocolError("invalid-protocol", "duplicate native turn completion")
            self.completed = turn
            return "finishing", None
        return "streaming-model", None
