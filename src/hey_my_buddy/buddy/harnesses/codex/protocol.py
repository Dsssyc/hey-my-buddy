"""Bounded Codex App Server JSONL transport and native turn observation."""
from __future__ import annotations

import os
import queue
import select
import threading
import time
from decimal import Decimal, InvalidOperation
from datetime import datetime, timezone

from ....json_codec import canonical_json, decode_strict_json

MAX_FRAME_BYTES = 8 * 1024 * 1024
#: The bound on one native error message carried into a refusal; nothing past
#: the ``error.message`` string itself (no ``error.data`` or other content).
MAX_NATIVE_ERROR_MESSAGE_CHARS = 256
_WINDOWS_PIPE = os.name == "nt"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class CodexProtocolError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


#: The one shared strict decode (duplicate members and non-finite numbers,
#: including the ``1e999`` overflow, refused); the caller keeps its own error
#: mapping around the ``ValueError`` this raises.
decode_json = decode_strict_json


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
        #: Optional in-process callback invoked after every pump step, the waits
        #: inside ``call`` included. The unified run seam takes the role
        #: observer's pending feedback here, so a stop requested on a recorded
        #: fact takes effect before any further native waiting.
        self.after_pump = None
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
        try:
            self._pump_one()
        finally:
            if self.after_pump is not None:
                self.after_pump()

    def _pump_one(self):
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
            # The native refusal keeps its own bounded message beside the
            # stable machine code; an error without a usable message keeps the
            # method-only fallback, and nothing else of the error is expanded.
            error = response.get("error")
            message = error.get("message") if isinstance(error, dict) else None
            if isinstance(message, str) and message.strip():
                raise CodexProtocolError("native-rpc-error",
                                         f"Codex rejected {method}: {message[:MAX_NATIVE_ERROR_MESSAGE_CHARS]}")
            raise CodexProtocolError("native-rpc-error", f"Codex rejected {method}")
        result = response.get("result")
        if not isinstance(result, dict):
            raise CodexProtocolError("invalid-protocol", f"Codex returned no object for {method}")
        return result


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
        from ....protocol.usage import classify_quota_code
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
        from ....protocol.usage import normalize_quota
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
