"""The shared role controller: one seam between the runtimes and every harness run.

ADR-025 step 1-C. The Worker runtime and the Router reach their harnesses only
through this module, which places the three things the accepted run contract left
to the roles: the preparation results of the Worker and of the Router's fast and
review modes, the observation policy over normalized run facts, and the narrow
session services the role holds while a run executes. The new run's one call
point is :func:`run_harness` over a :class:`HarnessRun` the registry has
registered explicitly; until a harness is extracted (steps two to four) its
transitional selection is the equally explicit carrier face below, and each
extraction deletes its old entry here. Nothing here judges a run: a ``RunResult``
stays a fact package, verdicts remain with the role collection paths and the
blackboard, and eligibility checks only confirm that a capability exists.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
from pathlib import Path
from typing import Any, Callable, Mapping

from ...errors import BoardError
from ..harnesses.base import (
    Adapter,
    AdapterOutcome,
    ExecutionContext,
    NoToolStructuredRequest,
    ProcessHandle,
    ReadOnlyStructuredRequest,
)
from ..harnesses.live import (
    MAX_ANSWER_BYTES,
    MAX_INQUIRIES_PER_RUN,
    MAX_QUESTION_BYTES,
    MAX_REQUEST_ID,
)
from ..harnesses.run_contract import HarnessRun, RunRequest, RunResult
from . import structured_call, turn_io

__all__ = [
    "ANSWER_CORRECTIONS",
    "AnswerCheck",
    "AnswerDecision",
    "CheckpointBatch",
    "FastPreparation",
    "FinishDecision",
    "OBSERVATION_FACTS",
    "OBSERVATION_KEYS",
    "ReviewPreparation",
    "RouterAnswerServices",
    "RouterObservation",
    "ROUTER_MODES",
    "WorkerObservation",
    "WorkerPreparation",
    "WorkerTurnServices",
    "prepare_worker_run",
    "read_observation",
    "run_harness",
    "start_router_preparation",
    "worker_cancel",
    "worker_collect",
    "worker_executor",
    "worker_start",
]


# -- the Worker runtime's execution seam -------------------------------------


@dataclass(frozen=True)
class WorkerPreparation:
    """What preparing one Worker attempt established, before any process exists.

    ``reason`` is the adapter's own availability fact and ``error`` its
    ``prepare()`` refusal; both stay ``None`` on a prepared attempt. The runtime
    maps them onto its exact receipts — the seam reports the result, the runtime
    keeps its receipt, retry and supervision rules.
    """

    name: str
    available: bool
    reason: str | None = None
    error: BoardError | None = None


def worker_executor(name: str) -> Adapter:
    """The Worker runtime's one executor selection point (ADR-025 decision 10).

    A harness extracted by steps two to four runs through its registered run
    seam and its old carrier entry is deleted in the same step, so a registered
    name must never fall through to the legacy carrier: this guard makes a
    half-finished switch loud instead of quietly reviving a deleted entry.
    ``command`` and ``external`` keep the registry carrier for good, and the
    registry stays the only place any executor is selected.
    """
    from ..harnesses.registry import adapter, run_seam

    if run_seam(name) is not None:
        raise BoardError(
            "ROLE_RUN_NOT_MIGRATED",
            f"{name} has a registered run seam; its role execution must replace this legacy carrier",
            adapter=name,
        )
    return adapter(name)


def prepare_worker_run(executor: Adapter, context: ExecutionContext) -> WorkerPreparation:
    """The adapter's availability fact and its own preparation, as one result."""
    usable, reason = executor.available()
    if not usable:
        return WorkerPreparation(executor.name, available=False, reason=reason)
    try:
        executor.prepare(context)
    except BoardError as error:
        return WorkerPreparation(executor.name, available=True, error=error)
    return WorkerPreparation(executor.name, available=True)


def worker_start(executor: Adapter, context: ExecutionContext) -> ProcessHandle:
    """Start one legacy-carrier attempt; the runtime keeps the returned handle."""
    return executor.start(context)


def worker_collect(executor: Adapter, handle: ProcessHandle, context: ExecutionContext) -> AdapterOutcome:
    return executor.collect(handle, context)


def worker_cancel(executor: Adapter, handle: ProcessHandle, *, grace_seconds: float | None = None) -> None:
    """Cancel through the executor; a None grace keeps each adapter's own default."""
    if grace_seconds is None:
        executor.cancel(handle)
    else:
        executor.cancel(handle, grace_seconds=grace_seconds)


# -- the Router's preparation results and carrier call point ------------------


@dataclass(frozen=True)
class FastPreparation:
    """The Router fast mode's prepared native call: everything but the start.

    ``native`` is the carrier selected once during preparation; the one call
    point below starts exactly this instance, so eligibility and selection are
    frozen and never recomputed.
    """

    harness: str
    native: Adapter
    request: NoToolStructuredRequest
    context: ExecutionContext
    #: The empty owner-private cwd the native call runs in, removed after a
    #: proven stop; unexpected native files are retained for inspection.
    no_tool_cwd: Path


@dataclass(frozen=True)
class ReviewPreparation:
    """The Router review mode's prepared native call over a frozen mirror.

    ``native`` is the carrier selected once during preparation; the one call
    point below starts exactly this instance, so eligibility and selection are
    frozen and never recomputed.
    """

    harness: str
    native: Adapter
    request: ReadOnlyStructuredRequest
    context: ExecutionContext
    #: The frozen input binding the collection re-verifies: (manifest, root, digest).
    mirror: tuple[dict | None, Path, str]


def start_router_preparation(preparation: FastPreparation | ReviewPreparation) -> ProcessHandle:
    """The one transitional call point of the Router's two structured entries.

    While a harness is unextracted this starts exactly the carrier the
    preparation selected — no re-resolution, so eligibility and selection are
    not recomputed; when its run seam is registered the old entries are deleted
    and the extracting step replaces this branch with its :func:`run_harness`
    execution. No third entry and no second carrier exists here.
    """
    from ..harnesses.registry import run_seam

    if run_seam(preparation.harness) is not None:
        raise BoardError(
            "ROLE_RUN_NOT_MIGRATED",
            f"{preparation.harness} has a registered run seam; its Router execution must replace this legacy entry",
            adapter=preparation.harness,
        )
    if isinstance(preparation, FastPreparation):
        return preparation.native.start_no_tool_structured(preparation.context, preparation.request)
    if isinstance(preparation, ReviewPreparation):
        return preparation.native.start_read_only_structured(preparation.context, preparation.request)
    raise BoardError("INVALID_ARGUMENT", "unknown Router preparation", harness=preparation.harness)


# -- the new run seam's one call point ----------------------------------------


def run_harness(
    module: HarnessRun,
    request: RunRequest,
    *,
    observer: Callable[[Mapping[str, Any]], bool],
    services: "WorkerTurnServices | RouterAnswerServices",
    cancelled: Callable[[], bool],
) -> RunResult:
    """The one common call point of the accepted run seam (ADR-025 decision 1).

    ``module`` must be exactly the run module the registry has registered under
    ``request.harness`` — never an attribute probe result, a look-alike, or an
    unregistered object — so a command adapter or an arbitrary object is never
    mistaken for a run. The observer receives normalized, retained fact mappings
    and answers whether to continue; the services are the role-held instances
    defined below; the result stays a fact package that carries no verdict.
    """
    from ..harnesses.registry import run_seam

    if run_seam(request.harness) is not module:
        raise BoardError("ROLE_RUN_UNREGISTERED",
                         f"the run of {request.harness} must go through its registered run seam",
                         harness=request.harness)
    return module.run(request, observer=observer, services=services, cancelled=cancelled)


# -- the observation policy over run facts ------------------------------------

#: The closed fact set an observer may receive, drawn from the accepted result
#: vocabulary: only facts a run module can normalize and retain while a run is
#: still producing them. A kind outside the set is a seam violation, not data.
OBSERVATION_FACTS = (
    "model-start",
    "value",
    "tool-event",
    "correction",
    "unknown-events",
    "denied-interaction",
    "native-failure",
    "activity",
)
#: The fixed envelope keys of one observation.
OBSERVATION_KEYS = frozenset({"fact", "sequence", "payload"})

#: The Router's two modes, whose in-run policies the current paths define.
ROUTER_MODES = ("fast", "review")
#: The correction codes a Router value check can name; an enum violation is
#: never correctable, so it is carried as ``None`` beside a failed check.
ANSWER_CORRECTIONS = ("answer-shape", "answer-invalid-json")


def read_observation(fact: Mapping[str, Any]) -> tuple[str, int, Mapping[str, Any]]:
    """Validate one observation envelope and return ``(fact, sequence, payload)``.

    The envelope shape and the fact set are closed, and the cumulative facts the
    roles consume carry their declared totals: an observation the role cannot
    recognize, or a consumed kind whose declared count is missing or not a
    nonnegative integer, is refused here instead of being silently ignored or
    read as a known zero, so a run module can never drop a fact past the roles.
    """
    if not isinstance(fact, Mapping) or set(fact) != OBSERVATION_KEYS:
        raise BoardError("INVALID_ARGUMENT", "an observation carries exactly fact, sequence and payload")
    kind = fact["fact"]
    if not isinstance(kind, str) or kind not in OBSERVATION_FACTS:
        raise BoardError("INVALID_ARGUMENT", f"observation fact {kind!r} is not in the closed fact set",
                         fact=kind if isinstance(kind, str) else None)
    sequence = fact["sequence"]
    if isinstance(sequence, bool) or not isinstance(sequence, int) or sequence < 0:
        raise BoardError("INVALID_ARGUMENT", "an observation sequence is a nonnegative integer")
    payload = fact["payload"]
    if not isinstance(payload, Mapping):
        raise BoardError("INVALID_ARGUMENT", "an observation payload is a bounded mapping")
    if kind in CUMULATIVE_FACT_FIELDS:
        _cumulative(payload.get(CUMULATIVE_FACT_FIELDS[kind]), None, CUMULATIVE_FACT_FIELDS[kind])
    return kind, sequence, payload


#: The consumed observation kinds whose payload carries the native projection's
#: run-cumulative count, deduplicated and retained by the driver before the
#: callback: the field name each payload must declare. Unconsumed kinds keep the
#: 1-A source boundary of their packages and stay unwired.
CUMULATIVE_FACT_FIELDS = {"tool-event": "toolCalls", "correction": "correctionCount",
                          "unknown-events": "total"}


def _cumulative(declared: Any, current: int | None, field: str) -> int:
    """Adopt one declared run-cumulative count; repeats are idempotent.

    A missing or non-integer count is refused rather than read as a known zero,
    and a decrease below the adopted value is refused because a run-cumulative
    count is monotone. The same value arriving again consumes no budget twice.
    """
    if isinstance(declared, bool) or not isinstance(declared, int) or declared < 0:
        raise BoardError("INVALID_ARGUMENT", f"observation {field} must be a nonnegative integer", field=field)
    if current is not None and declared < current:
        raise BoardError("INVALID_ARGUMENT",
                         f"observation {field} moved backwards from a run-cumulative count", field=field)
    return declared


def _unknown_total(payload: Mapping[str, Any]) -> int:
    """The declared run-cumulative unknown-event total; never estimated."""
    return _cumulative(payload.get("total"), None, "total")


@dataclass
class WorkerObservation:
    """One Worker turn's in-run facts, under exactly its current policy.

    A Worker turn ignores unrecognized events and never stops on an
    observation: interruptions come from the runtime's cancel, deadline and
    renewal paths, and the turn record is judged at collection. The consumed
    counts adopt the declared run-cumulative totals idempotently; denied native
    interactions are retained because the finish tool must refuse a
    ``completed`` outcome while one is unresolved.
    """

    tool_calls: int = 0
    corrections: int = 0
    unknown_events: int = 0
    denied_interactions: int = 0
    model_started: bool | None = None

    def observe(self, fact: Mapping[str, Any]) -> bool:
        kind, _sequence, payload = read_observation(fact)
        if kind == "tool-event":
            self.tool_calls = _cumulative(payload.get("toolCalls"), self.tool_calls, "toolCalls")
        elif kind == "correction":
            self.corrections = _cumulative(payload.get("correctionCount"), self.corrections, "correctionCount")
        elif kind == "unknown-events":
            self.unknown_events = _cumulative(payload.get("total"), self.unknown_events, "total")
        elif kind == "denied-interaction":
            self.denied_interactions += 1
        elif kind == "model-start":
            started = payload.get("started")
            if isinstance(started, bool):
                self.model_started = started
        return True


@dataclass
class RouterObservation:
    """One Router run's in-run facts: its two modes keep their current differences.

    The consumed counts are the declared run-cumulative totals the native
    projection deduplicated before the callback; the same value arriving again
    consumes no budget twice. Fast routing stops at the first tool fact —
    exactly the current fast paths' immediate refusal of any tool event — and
    at the first unrecognized event; its zero-tool verdict stays with the
    blackboard at publication. Review keeps its end-of-stream judgment:
    unrecognized events are retained for the fact package and do not stop the
    run, and the review budget is exhausted one deduplicated call past its
    limit, continuing the native counting and grace.
    """

    mode: str
    tool_call_limit: int | None = None
    tool_calls: int = 0
    corrections: int = 0
    unknown_events: int = 0
    denied_interactions: int = 0
    #: Why the observer last asked the run to stop, or None while it continues.
    stopped: str | None = None

    def __post_init__(self) -> None:
        if self.mode not in ROUTER_MODES:
            raise BoardError("INVALID_ARGUMENT", f"mode must be one of {', '.join(ROUTER_MODES)}", mode=self.mode)
        if self.tool_call_limit is not None and (
            isinstance(self.tool_call_limit, bool) or not isinstance(self.tool_call_limit, int)
            or self.tool_call_limit < 0
        ):
            raise BoardError("INVALID_ARGUMENT", "toolCallLimit must be a nonnegative integer or None")

    def observe(self, fact: Mapping[str, Any]) -> bool:
        kind, _sequence, payload = read_observation(fact)
        if kind == "tool-event":
            self.tool_calls = _cumulative(payload.get("toolCalls"), self.tool_calls, "toolCalls")
            if self.mode == "fast":
                self.stopped = "tool-fact"
                return False
            if self.tool_call_limit is not None and self.tool_calls > self.tool_call_limit:
                self.stopped = "tool-budget"
                return False
        elif kind == "correction":
            self.corrections = _cumulative(payload.get("correctionCount"), self.corrections, "correctionCount")
        elif kind == "unknown-events":
            self.unknown_events = _cumulative(payload.get("total"), self.unknown_events, "total")
            if self.mode == "fast":
                self.stopped = "unknown-events"
                return False
        elif kind == "denied-interaction":
            self.denied_interactions += 1
        return True


# -- the Worker's session services --------------------------------------------


@dataclass(frozen=True)
class FinishDecision:
    """One finish judgment: facts a harness driver renders and signs itself.

    ``reason`` is the closed refusal vocabulary of the current finish tool and
    ``pending`` the still-unanswered Host questions, so the driver's signed
    envelope can name them; ``detail`` is the outcome validator's own wording.
    """

    accepted: bool
    reason: str | None = None
    detail: str | None = None
    pending: tuple[Mapping[str, Any], ...] = ()


@dataclass(frozen=True)
class AnswerDecision:
    """One inquiry-answer judgment with the binding a receipt must carry."""

    accepted: bool
    reason: str | None = None
    inquiry_id: str | None = None
    question_sha256: str | None = None
    answer: str | None = None


@dataclass(frozen=True)
class CheckpointBatch:
    """The questions one checkpoint may carry, and how many stayed queued."""

    inquiries: tuple[Mapping[str, Any], ...]
    more_pending: int


class WorkerTurnServices:
    """The role-held session services of one governed Worker turn.

    The judgment lives here once: the six-field outcome validation, the
    attention and inquiry-pending finish refusals, the answer binding and
    conflict rules, and the checkpoint batching. A harness driver renders and
    signs its own refusal and receipt envelopes around these decisions and
    verifies them against its own native tool evidence; it never sees the board
    client or any credential. The bounds are the existing bridges' bounds,
    preserved verbatim from the accepted live contract. The role holds this
    instance for the run; the current ZCode and DSH drivers still run their own
    in-harness equivalents until their extraction steps rewire them here.
    """

    def __init__(self, observation: WorkerObservation):
        self.observation = observation
        self._questions: dict[str, dict] = {}
        self._order: list[str] = []

    # -- the role-held inquiry state -----------------------------------------
    def ask(self, inquiry_id: str, question: str) -> Mapping[str, Any]:
        """Commit one Host question as queued; the first hash is the binding."""
        _check_inquiry_id(inquiry_id)
        if not isinstance(question, str) or not question.strip() or "\0" in question \
                or len(question.encode()) > MAX_QUESTION_BYTES:
            raise BoardError("INVALID_ARGUMENT",
                             f"a question must be nonblank text of at most {MAX_QUESTION_BYTES} UTF-8 bytes")
        if inquiry_id in self._questions:
            raise BoardError("CONFLICT", f"inquiry {inquiry_id} is already queued in this turn", inquiryId=inquiry_id)
        if len(self._questions) >= MAX_INQUIRIES_PER_RUN:
            raise BoardError("INVALID_ARGUMENT",
                             f"this run already carries its limit of {MAX_INQUIRIES_PER_RUN} inquiries")
        entry = {"inquiryId": inquiry_id, "question": question, "state": "queued",
                 "questionSha256": hashlib.sha256(question.encode()).hexdigest()}
        self._questions[inquiry_id] = entry
        self._order.append(inquiry_id)
        return dict(entry)

    def mark_delivered(self, inquiry_ids: list[str]) -> None:
        """Mark questions delivered by a checkpoint; every id must be known."""
        for inquiry_id in inquiry_ids:
            entry = self._questions.get(inquiry_id)
            if entry is None:
                raise BoardError("INVALID_ARGUMENT",
                                 f"a checkpoint receipt referenced inquiry {inquiry_id} this turn never committed",
                                 inquiryId=inquiry_id)
            if entry["state"] == "queued":
                entry["state"] = "delivered"

    def record_answer(self, inquiry_id: str, answer: str) -> None:
        """Record the authoritative answer; the first answer cannot be replaced."""
        _check_inquiry_id(inquiry_id)
        _check_answer(answer)
        entry = self._questions.get(inquiry_id)
        if entry is None:
            raise BoardError("INVALID_ARGUMENT", f"unknown inquiry {inquiry_id} in this turn", inquiryId=inquiry_id)
        if entry["state"] == "answered":
            raise BoardError("CONFLICT",
                             f"inquiry {inquiry_id} already has its recorded answer; it cannot be replaced",
                             inquiryId=inquiry_id)
        if entry["state"] not in ("queued", "delivered"):
            raise BoardError("INVALID_ARGUMENT",
                             f"inquiry {inquiry_id} is {entry['state']} and can no longer be answered",
                             inquiryId=inquiry_id)
        entry["state"] = "answered"
        entry["answer"] = answer

    def withdraw(self, inquiry_id: str) -> None:
        """A withdrawn or explicitly unavailable question no longer blocks."""
        entry = self._questions.get(inquiry_id)
        if entry is None or entry["state"] in ("answered", "unavailable"):
            return
        entry["state"] = "unavailable"

    def pending(self) -> list[Mapping[str, Any]]:
        """The still-answerable questions in commit order, as bounded views."""
        waiting = []
        for inquiry_id in self._order:
            entry = self._questions[inquiry_id]
            if entry["state"] in ("queued", "delivered") and isinstance(entry.get("question"), str) \
                    and entry["question"]:
                item = {"inquiryId": inquiry_id, "question": entry["question"],
                        "questionSha256": entry["questionSha256"], "state": entry["state"]}
                waiting.append(item)
        return waiting

    # -- the three tools' judgments -------------------------------------------
    def evaluate_finish(self, outcome: Mapping[str, Any]) -> FinishDecision:
        """The finish tool's refusal order: fields, then attention, then inquiries."""
        error = turn_io.validate_outcome(outcome)
        if error:
            return FinishDecision(False, reason="invalid-arguments", detail=error)
        if outcome["disposition"] == "completed":
            if self.observation.denied_interactions:
                return FinishDecision(False, reason="attention-outstanding")
            waiting = self.pending()
            if waiting:
                return FinishDecision(False, reason="inquiry-pending", pending=tuple(waiting))
        return FinishDecision(True, pending=())

    def evaluate_answer(self, arguments: Mapping[str, Any]) -> AnswerDecision:
        """One answer tool call against the committed questions and their states."""
        if not isinstance(arguments, Mapping) or set(arguments) != {"inquiryId", "answer"}:
            return AnswerDecision(False, reason="invalid-arguments")
        inquiry_id, answer = arguments["inquiryId"], arguments["answer"]
        if not isinstance(inquiry_id, str) or not inquiry_id or "\0" in inquiry_id \
                or len(inquiry_id.encode()) > MAX_REQUEST_ID:
            return AnswerDecision(False, reason="invalid-arguments")
        if not isinstance(answer, str) or not answer.strip() or len(answer.encode()) > MAX_ANSWER_BYTES:
            return AnswerDecision(False, reason="invalid-arguments")
        entry = self._questions.get(inquiry_id)
        if entry is None:
            return AnswerDecision(False, reason="unknown-inquiry", inquiry_id=inquiry_id)
        if entry["state"] == "answered":
            return AnswerDecision(False, reason="inquiry-state", inquiry_id=inquiry_id,
                                  question_sha256=entry["questionSha256"])
        if entry["state"] not in ("queued", "delivered"):
            return AnswerDecision(False, reason="inquiry-state", inquiry_id=inquiry_id)
        return AnswerDecision(True, inquiry_id=inquiry_id, question_sha256=entry["questionSha256"], answer=answer)

    def checkpoint(self, *, budget_bytes: int) -> CheckpointBatch:
        """The longest pending prefix that fits the caller's serialized budget.

        Questions beyond the batch are not lost: they stay queued, block a
        completed finish by name, and arrive on a later checkpoint. The caller
        passes the space its own receipt framing leaves for the inquiry list.
        """
        waiting = self.pending()
        batch: list[Mapping[str, Any]] = []
        for item in waiting:
            if len(turn_io.canonical_json(list(batch) + [item]).encode()) > budget_bytes:
                break
            batch.append(item)
        return CheckpointBatch(tuple(batch), len(waiting) - len(batch))


def _check_inquiry_id(inquiry_id: str) -> None:
    if not isinstance(inquiry_id, str) or not inquiry_id or "\0" in inquiry_id \
            or len(inquiry_id.encode()) > MAX_REQUEST_ID:
        raise BoardError("INVALID_ARGUMENT",
                         f"an inquiry id must be nonempty text of at most {MAX_REQUEST_ID} UTF-8 bytes")


def _check_answer(answer: str) -> None:
    if not isinstance(answer, str) or not answer.strip() or "\0" in answer \
            or len(answer.encode()) > MAX_ANSWER_BYTES:
        raise BoardError("INVALID_ARGUMENT",
                         f"an answer must be nonblank text of at most {MAX_ANSWER_BYTES} UTF-8 bytes")


# -- the Router's answer services ---------------------------------------------


@dataclass(frozen=True)
class AnswerCheck:
    """One Router value check; the correction decision is the role's own.

    ``correction`` is one of :data:`ANSWER_CORRECTIONS` when the value may be
    corrected, and ``None`` both when it is valid and when it violates the enum
    — a choice outside the frozen candidates is never retried.
    """

    valid: bool
    correction: str | None = None
    errors: tuple[str, ...] = ()


class RouterAnswerServices:
    """The Router's schema and correction judgment over one run's final values.

    ``max_corrections`` carries each harness's current fact — the one format
    correction of today's fast loop, none where a harness does not correct —
    set by the caller, never an ``if``-branch here. The final answer judgment
    (candidates, evidence bounds, publication) stays with the Router collection
    path and the blackboard; nothing here can produce a verdict.
    """

    def __init__(self, *, schema: dict, observation: RouterObservation, max_corrections: int = 1):
        self.schema = schema
        self.observation = observation
        if isinstance(max_corrections, bool) or not isinstance(max_corrections, int) or max_corrections < 0:
            raise BoardError("INVALID_ARGUMENT", "maxCorrections must be a nonnegative integer")
        self.max_corrections = max_corrections

    def check(self, raw: object) -> AnswerCheck:
        """The current value check: plain decode, the shared schema subset, one code.

        The schema keywords are exactly the Router's supported subset and the
        decode is the ordinary ``json.loads`` the current callers use — no
        stricter validation enters here. A schema with unsupported keywords is
        a caller bug and raises, as it does on the current paths.
        """
        try:
            value = json.loads(raw) if isinstance(raw, str) else raw
        except (ValueError, RecursionError):
            return AnswerCheck(False, correction="answer-invalid-json")
        errors = tuple(structured_call.schema_errors(value, self.schema))
        if not errors:
            return AnswerCheck(True)
        if "enum" in errors:
            return AnswerCheck(False, correction=None, errors=errors)
        return AnswerCheck(False, correction="answer-shape", errors=errors)

    def allows_correction(self) -> bool:
        """Whether this run still has one of its corrections left."""
        return self.observation.corrections < self.max_corrections

    def budget_facts(self) -> dict:
        """The in-run budget consumption a projection reports as facts."""
        return {"toolCalls": self.observation.tool_calls, "toolCallLimit": self.observation.tool_call_limit,
                "corrections": self.observation.corrections}
