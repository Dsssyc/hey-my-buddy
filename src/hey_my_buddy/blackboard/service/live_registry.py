"""The blackboard-side in-memory live registry (ADR-025 step 5-B1).

One service instance's map from its currently attached Worker live endpoints to
their channels. The map lives only in memory: every fresh service instance
starts empty, nothing here writes a lease, result, event, schema or history row,
and a disconnected endpoint is reported as ``None``/unavailable instead of ever
becoming a stop fact. Registration reuses the existing attempt-actor proof
(:meth:`BoardStore._verify_attempt_actor`) inside a read-only transaction, so a
binding can only ever be created by the worker that currently holds a live,
non-uncertain attempt — a service restart fences every in-flight attempt as
uncertain, which is exactly the rule that makes a real Worker reconcile before
it re-attaches; a PID or address never adopts anything.

The frames are the shared ``protocol.worker_live`` pydantic models: strict,
closed, frozen, and with the worker address and ``liveToken`` carried on the
frame only — they never appear in a response, an error payload or a log line
here, and responses state the attached/detached facts and identifiers only.

The channel factory is injected, so this module has no runtime dependency on
the buddy side (the ``LiveChannel`` reference below is a typing-only import).
The Host binds the factory to the real ``WorkerRuntimeLive`` client; every
factory or peer call happens outside the database transaction and outside the
map lock. One channel is built lazily per binding and cached, so repeated
inquiries never rebuild transport resources, and the binding bound below keeps
both the map and the cached channels bounded. Replacing or removing a binding
closes the channel this map owned; a channel the map no longer stores is closed
before it can be handed out again.

Wiring that stays with the Host (this micro-task delivers the class only, and
no production consumer exists until these land): ``BoardService``
initialization assigns ``store.live_registry = LiveRegistry(store, factory)``
with the real factory; ``service.py`` routes the two declared ``worker_live_attach``
/ ``worker_live_detach`` contract operations through ``_guard`` to
:meth:`attach` and :meth:`detach`; ``tasks/inquiry.py`` reaches the Worker
endpoint only through :meth:`channel_for` over the real task view.
"""
from __future__ import annotations

import sqlite3
import threading
from typing import TYPE_CHECKING, Callable, Optional

from ..store.db import sha256_text
from ..store.store import BoardStore
from ...errors import BoardError
from ...protocol.worker_live import WorkerLiveAttach, WorkerLiveDetach

if TYPE_CHECKING:  # the channel interface only; never a runtime cross-side import
    from ...buddy.harnesses.live import LiveChannel

#: The bounded number of live bindings one service instance holds. Each binding
#: caches at most one channel, so this bound also bounds cached transport
#: resources; a further attach waits for a detach or a service restart.
MAX_LIVE_BINDINGS = 64

#: The attempt states under which a live endpoint may be attached and stay
#: answerable: every other state is finished, uncertain or unknown.
LIVE_ATTEMPT_STATES = frozenset({"starting", "executing", "finalizing"})

TERMINAL_TASK_STATES = frozenset({"completed", "failed", "cancelled"})


class _LiveBinding:
    """One attached endpoint frame and its lazily built, cached channel."""

    __slots__ = ("frame", "channel")

    def __init__(self, frame: WorkerLiveAttach):
        self.frame = frame
        self.channel: Optional["LiveChannel"] = None


class LiveRegistry:
    """The in-memory live endpoint registrations of one service instance.

    The registry verifies identity and state only: it reads the authoritative
    rows inside one short read transaction and never writes any. The channel
    factory receives the full stored attach frame (the Worker endpoint address
    and live token included) and returns the channel for it, or raises — a
    raised factory or a ``None`` return is an unavailable endpoint, reported as
    ``None`` here and never recorded as a stop fact.
    """

    def __init__(self, store: BoardStore,
                 channel_factory: Callable[[WorkerLiveAttach], Optional["LiveChannel"]]):
        self.store = store
        self._channel_factory = channel_factory
        self._lock = threading.Lock()
        self._bindings: dict[str, _LiveBinding] = {}

    # -- registration --------------------------------------------------------
    def attach(self, params: dict | WorkerLiveAttach) -> dict:
        """Register one Worker live endpoint for the attempt its actor holds.

        The actor proof (worker, generation, nonce, effective attempt) and the
        recorded worker instance are the existing store verifications; the
        frame's execution identity must be the same run's, and a governed turn
        identity must be the recorded turn of this very attempt and generation.
        The identical binding again is an idempotent replay; a changed binding
        for the same attempt replaces the old one and closes the channel this
        registry owned for it.
        """
        frame = params if isinstance(params, WorkerLiveAttach) else WorkerLiveAttach.from_payload(params)
        request = params if isinstance(params, dict) else frame.to_payload()
        with self.store.db.read() as connection:
            attempt, _worker = self.store._verify_attempt_actor(connection, request)
            self._verify_live_state(connection, attempt, frame)
        with self._lock:
            existing = self._bindings.get(frame.attempt_id)
            if existing is not None and existing.frame == frame:
                return {"attached": True, "attemptId": frame.attempt_id,
                        "instanceId": frame.instance_id, "replayed": True}
            if existing is None and len(self._bindings) >= MAX_LIVE_BINDINGS:
                raise BoardError(
                    "NOT_READY",
                    "the live registry holds its binding bound; a detach or a restart frees capacity",
                    attemptId=frame.attempt_id,
                )
            self._bindings[frame.attempt_id] = _LiveBinding(frame)
        if existing is not None:
            self._close(existing.channel, "replaced")
        return {"attached": True, "attemptId": frame.attempt_id,
                "instanceId": frame.instance_id, "replayed": False}

    def detach(self, params: dict | WorkerLiveDetach) -> dict:
        """Remove the one endpoint whose identity and instance match exactly.

        The actor proof is required even for a finished attempt, so a wrong
        actor, a superseded attempt or another process instance can never
        remove anything. A stored binding with a different execution identity
        or a newer endpoint instance is refused rather than removed: an old
        full identity cannot delete a newer binding. Detaching an attempt with
        no binding is the honest ``detached: False`` fact.
        """
        frame = params if isinstance(params, WorkerLiveDetach) else WorkerLiveDetach.from_payload(params)
        request = params if isinstance(params, dict) else frame.to_payload()
        with self.store.db.read() as connection:
            attempt, _worker = self.store._verify_attempt_actor(connection, request)
            self._verify_worker_instance(attempt, frame)
            self._verify_actor_identity(attempt, frame)
        with self._lock:
            binding = self._bindings.get(frame.attempt_id)
            if binding is None:
                return {"detached": False, "attemptId": frame.attempt_id, "instanceId": frame.instance_id}
            if binding.frame.identity != frame.identity:
                raise BoardError(
                    "CONFLICT",
                    "the live binding of this attempt carries a different execution identity",
                    attemptId=frame.attempt_id,
                )
            if binding.frame.instance_id != frame.instance_id:
                raise BoardError(
                    "CONFLICT",
                    "the live binding of this attempt belongs to a newer endpoint instance",
                    attemptId=frame.attempt_id,
                )
            self._bindings.pop(frame.attempt_id)
            channel = binding.channel
        self._close(channel, "detached")
        return {"detached": True, "attemptId": frame.attempt_id, "instanceId": frame.instance_id}

    # -- reachability --------------------------------------------------------
    def channel_for(self, view: dict) -> Optional["LiveChannel"]:
        """The cached channel of this view's selected attempt, or ``None``.

        The view is the real decorated task view. The stored binding is
        re-checked against it on every call — the current actor, the run
        identity and the non-terminal, non-uncertain attempt state — so a
        stale, foreign, terminal or uncertain binding never hands out a
        channel. A missing or refused binding is ``None``; no stop fact is
        ever written. The factory runs outside the map lock, and the first
        successful channel for a binding is the one every later call reuses.
        """
        attempt = view.get("selectedAttempt") if isinstance(view, dict) else None
        attempt_id = attempt.get("attemptId") if isinstance(attempt, dict) else None
        task_id = view.get("taskId") if isinstance(view, dict) else None
        if not attempt_id or not task_id:
            return None
        with self._lock:
            binding = self._bindings.get(attempt_id)
            if binding is None or not self._matches_view(binding.frame, task_id, attempt_id, attempt, view):
                return None
            frame = binding.frame
            if binding.channel is not None:
                return binding.channel
        try:
            built = self._channel_factory(frame)
        except Exception:
            return None
        if built is None:
            return None
        with self._lock:
            current = self._bindings.get(attempt_id)
            if current is not None and current.frame == frame and current.channel is None:
                current.channel = built
                return built
            winner = current.channel if current is not None and current.frame == frame else None
        self._close(built, "superseded")
        return winner

    # -- verification --------------------------------------------------------
    @staticmethod
    def _verify_worker_instance(attempt: sqlite3.Row,
                                frame: WorkerLiveAttach | WorkerLiveDetach) -> None:
        """Unknown process ownership is never adopted from an endpoint caller."""
        if not attempt["worker_instance"]:
            raise BoardError(
                "UNAUTHORIZED",
                "this attempt has no recorded worker process instance, so it has no live capability",
                attemptId=attempt["attempt_id"],
            )
        if frame.worker_instance != attempt["worker_instance"]:
            raise BoardError(
                "UNAUTHORIZED",
                "this attempt was claimed by a different worker process instance, which is the only holder of "
                "the child handle",
                attemptId=attempt["attempt_id"],
            )

    @staticmethod
    def _verify_actor_identity(attempt: sqlite3.Row,
                               frame: WorkerLiveAttach | WorkerLiveDetach) -> None:
        identity = frame.identity
        if (identity.task_id != attempt["task_id"] or identity.attempt_id != frame.attempt_id
                or identity.generation != attempt["generation"]):
            raise BoardError(
                "UNAUTHORIZED",
                "the live frame's execution identity does not belong to this attempt actor",
                attemptId=attempt["attempt_id"],
            )

    def _verify_live_state(self, connection: sqlite3.Connection,
                           attempt: sqlite3.Row, frame: WorkerLiveAttach) -> None:
        """The read-only state and identity gates of one attach, in-transaction."""
        self._verify_worker_instance(attempt, frame)
        self._verify_actor_identity(attempt, frame)
        if attempt["result_json"] is not None or attempt["execution_state"] == "finished":
            raise BoardError(
                "ATTEMPT_FINISHED", "this attempt is already finished; a live endpoint cannot attach",
                attemptId=attempt["attempt_id"],
            )
        if attempt["ownership"] == "uncertain" or attempt["execution_state"] == "uncertain":
            raise BoardError(
                "ATTEMPT_UNCERTAIN",
                "this attempt is shutdown-uncertain; the worker must reconcile before it attaches",
                attemptId=attempt["attempt_id"],
            )
        identity = frame.identity
        # The authoritative governed turn of this attempt is the run's current
        # turn bound to it — never just any workflow_turns row of the run. While
        # it exists, the identity must carry exactly its id and the digest of the
        # input the service wrote (sha256 over the canonical input_json text; the
        # stored receipt-time column, once filled, must agree with it). A run
        # without a governed turn keeps the both-null rule.
        turn = connection.execute(
            "SELECT r.current_turn_id, r.current_attempt_id, t.turn_id, t.attempt_id,"
            " t.generation, t.input_json, t.input_sha256 FROM workflow_runs r"
            " LEFT JOIN workflow_turns t ON t.run_id=r.run_id AND t.turn_id=r.current_turn_id"
            " WHERE r.run_id=?",
            (attempt["task_id"],),
        ).fetchone()
        if turn is None or turn["current_turn_id"] is None:
            if identity.turn_id is not None or identity.input_sha256 is not None:
                raise BoardError(
                    "UNAUTHORIZED",
                    "this attempt has no governed turn; its live identity may not carry a turn id or input digest",
                    attemptId=attempt["attempt_id"],
                )
            return
        if (turn["current_attempt_id"] != attempt["attempt_id"]
                or turn["attempt_id"] != attempt["attempt_id"]
                or turn["generation"] != attempt["generation"]):
            raise BoardError(
                "UNAUTHORIZED",
                "the current governed turn is not bound to this attempt and generation",
                attemptId=attempt["attempt_id"], turnId=turn["turn_id"],
            )
        if turn["turn_id"] != identity.turn_id:
            raise BoardError(
                "UNAUTHORIZED",
                "the live identity must carry the attempt's current governed turn",
                attemptId=attempt["attempt_id"], turnId=turn["turn_id"],
            )
        computed = sha256_text(turn["input_json"])
        if identity.input_sha256 != computed:
            raise BoardError(
                "UNAUTHORIZED",
                "the live identity's input digest does not match the turn input the service wrote",
                attemptId=attempt["attempt_id"], turnId=turn["turn_id"],
            )
        if turn["input_sha256"] is not None and turn["input_sha256"] != computed:
            raise BoardError(
                "UNAUTHORIZED",
                "the turn's recorded digest conflicts with the turn input the service wrote",
                attemptId=attempt["attempt_id"], turnId=turn["turn_id"],
            )

    @staticmethod
    def _matches_view(frame: WorkerLiveAttach, task_id: str, attempt_id: str,
                      attempt: dict, view: dict) -> bool:
        """Whether one stored binding is still this view's current, live actor."""
        return (
            frame.identity.task_id == task_id
            and frame.identity.attempt_id == attempt_id
            and frame.generation == attempt.get("generation")
            and frame.worker_id == attempt.get("workerId")
            and frame.worker_instance == attempt.get("workerInstance")
            and view.get("state") not in TERMINAL_TASK_STATES
            and attempt.get("executionState") in LIVE_ATTEMPT_STATES
            and attempt.get("ownership") != "uncertain"
        )

    @staticmethod
    def _close(channel: Optional["LiveChannel"], reason: str) -> None:
        """Close a channel this registry owns, never while a lock is held."""
        if channel is not None:
            channel.close(reason=reason)
