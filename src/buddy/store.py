"""Transactional blackboard store: the only writer of authoritative task facts.

Every state change is one short ``BEGIN IMMEDIATE`` transaction that also appends
its event, so a reader that sees new state also sees the event that explains it and
vice versa. No transaction here spans RPC, a subprocess, an LLM call or a wait.
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
import uuid
from pathlib import Path
from typing import Any, Callable, Iterable

from . import activity as activity_module
from . import delegation
from . import scheduling
from . import schemas
from .db import (
    ACTIVE_ATTEMPT_STATES,
    ATTEMPT_STATES,
    MESSAGE_STATES,
    TASK_STATES,
    TERMINAL_TASK_STATES,
    TERMINATION_REASONS,
    Database,
    canonical_json,
    sha256_text,
    utc_now,
)
from .errors import BoardError

#: Documented task transition table. A transition outside this map is rejected.
TASK_TRANSITIONS: dict[str, frozenset[str]] = {
    "queued": frozenset({"queued", "running", "cancelling", "cancelled", "reconciliation-needed"}),
    # ``running -> queued`` is the governed yield: a turn concluded with a structured
    # assistance/attention outcome, the attempt stopped with confirmed shutdown and
    # the same logical task waits for the Host instead of pretending to be complete.
    "running": frozenset(
        {"running", "queued", "cancelling", "completed", "failed", "cancelled", "reconciliation-needed"}
    ),
    "cancelling": frozenset({"cancelling", "completed", "failed", "cancelled", "reconciliation-needed"}),
    "reconciliation-needed": frozenset({"reconciliation-needed", "queued", "cancelling", "cancelled", "failed"}),
    "completed": frozenset({"completed"}),
    "failed": frozenset({"failed", "queued"}),
    "cancelled": frozenset({"cancelled", "queued"}),
}

#: Documented attempt transition table.
ATTEMPT_TRANSITIONS: dict[str, frozenset[str]] = {
    "starting": frozenset({"starting", "executing", "finalizing", "uncertain", "finished"}),
    "executing": frozenset({"executing", "finalizing", "uncertain", "finished"}),
    "finalizing": frozenset({"finalizing", "uncertain", "finished"}),
    "uncertain": frozenset({"uncertain", "executing", "finalizing", "finished"}),
    "finished": frozenset({"finished"}),
}

ACTIVE_ATTEMPT_STATES_SQL = tuple(sorted(ACTIVE_ATTEMPT_STATES))
DEFAULT_LEASE_SECONDS = 120
MAX_EVENT_PAGE = 200

#: The one stale waiting reason a service restart writes. It is cleared only when
#: the owning worker reattaches to the same attempt; a mere renewal never removes it.
UNCERTAIN_QUEUE_REASON = "attempt-uncertain-after-restart"


def _overlaps(left: str, right: str) -> bool:
    """True when one canonical path is the other or lives inside it."""
    if left == right:
        return True
    left_prefix = left.rstrip("/") + "/"
    right_prefix = right.rstrip("/") + "/"
    return left.startswith(right_prefix) or right.startswith(left_prefix)


class BoardStore:
    """Durable facts plus their in-process change notification.

    The notification is only a hint: every consumer can always resume from the
    persisted event cursor, so a missed notification never loses committed state.
    """

    def __init__(
        self,
        directory: str | Path,
        *,
        max_concurrent: int = scheduling.TOTAL_CONCURRENCY_DEFAULT,
        lease_seconds: int = DEFAULT_LEASE_SECONDS,
        wait_capacity: int = 32,
        clock: Callable[[], str] = utc_now,
    ):
        self.directory = Path(directory)
        self.db = Database(self.directory)
        #: The one machine-wide concurrent-attempt ceiling (BUDDY_MAX_CONCURRENT).
        #: Routing/selection attempts and business attempts share it and the same
        #: per-model-family counters; there is no separate decision lane.
        self.max_concurrent = scheduling.clamp_total(max_concurrent)
        self.lease_seconds = max(15, min(int(lease_seconds), 3600))
        self.wait_capacity = max(1, int(wait_capacity))
        self._clock = clock
        self._condition = threading.Condition()
        self._head = 0
        self.persistence_error: str | None = None
        # The board is the single composition root for durable evaluation and
        # decision state: both wrap this same store (one SQLite file, one event
        # stream) and neither opens a transaction of its own.
        from .decision import DecisionCoordinator
        from .evaluation import EvaluationStore
        from .workflow import WorkflowCoordinator

        self.evaluation = EvaluationStore(self, clock=self._clock)
        self.decisions = DecisionCoordinator(self, self.evaluation)
        # The governed workflow layer wraps this same store: it opens no second
        # authority and every one of its mutations is one of these transactions.
        self.workflow = WorkflowCoordinator(self, clock=self._clock)

    # -- lifecycle -----------------------------------------------------------
    def initialize(self) -> None:
        self.db.initialize()
        with self.db.read() as connection:
            row = connection.execute("SELECT COALESCE(MAX(seq), 0) AS head FROM events").fetchone()
            self._head = int(row["head"])
        self.reconcile_startup()

    def now(self) -> str:
        return self._clock()

    def _head_of(self, connection: sqlite3.Connection) -> int:
        row = connection.execute("SELECT COALESCE(MAX(seq), 0) AS head FROM events").fetchone()
        return int(row["head"])

    def _notify(self, head: int) -> None:
        with self._condition:
            self._head = max(self._head, head)
            self._condition.notify_all()

    # -- event helpers -------------------------------------------------------
    def _append_event(
        self,
        connection: sqlite3.Connection,
        kind: str,
        *,
        task_id: str | None = None,
        attempt_id: str | None = None,
        revision: int | None = None,
        payload: dict | None = None,
    ) -> int:
        at = self.now()
        cursor = connection.execute(
            "INSERT INTO events(task_id, attempt_id, revision, kind, payload_json, created_at) VALUES(?,?,?,?,?,?)",
            (task_id, attempt_id, revision, kind, canonical_json(payload or {}), at),
        )
        from .objectives import record_activity
        record_activity(connection, task_id, int(cursor.lastrowid), at)
        return int(cursor.lastrowid)

    def head(self) -> int:
        return self._head

    def events_read(self, params: dict) -> dict:
        schemas.reject_unknown(params, {"after", "limit", "taskId", "runId"}, "events.read")
        after = schemas.optional_int(params, "after", 0, 0, 2**62)
        limit = schemas.optional_int(params, "limit", 100, 1, MAX_EVENT_PAGE)
        task_id = params.get("taskId") or params.get("runId")
        if task_id is not None and not isinstance(task_id, str):
            raise BoardError("INVALID_ARGUMENT", "taskId must be a string")
        with self.db.read() as connection:
            if task_id:
                rows = connection.execute(
                    "SELECT * FROM events WHERE seq > ? AND task_id = ? ORDER BY seq LIMIT ?",
                    (after, task_id, limit),
                ).fetchall()
                head_row = connection.execute(
                    "SELECT COALESCE(MAX(seq), 0) AS head FROM events WHERE task_id = ?", (task_id,)
                ).fetchone()
            else:
                rows = connection.execute(
                    "SELECT * FROM events WHERE seq > ? ORDER BY seq LIMIT ?", (after, limit)
                ).fetchall()
                head_row = connection.execute("SELECT COALESCE(MAX(seq), 0) AS head FROM events").fetchone()
        events = [self._event_view(row) for row in rows]
        cursor = events[-1]["seq"] if events else after
        return {"events": events, "cursor": cursor, "head": int(head_row["head"]), "truncated": len(events) == limit}

    def events_wait(self, params: dict, *, stop: threading.Event | None = None) -> dict:
        """Bounded event wait with a check/subscribe/recheck sequence.

        No database transaction and no exclusive resource lock is held while
        waiting, so unlimited concurrent waiters cannot block cancel, renew or
        result commits.
        """
        schemas.reject_unknown(params, {"after", "timeoutMs", "limit", "taskId", "runId"}, "events.wait")
        after = schemas.optional_int(params, "after", 0, 0, 2**62)
        timeout_ms = schemas.optional_int(params, "timeoutMs", 30000, 0, 30000)
        limit = schemas.optional_int(params, "limit", 100, 1, MAX_EVENT_PAGE)
        task_id = params.get("taskId") or params.get("runId")
        page = self.events_read({"after": after, "limit": limit, **({"taskId": task_id} if task_id else {})})
        # Compare against the *filtered* head: an unrelated task's later event must
        # not make this task's unchanged cursor look satisfied.
        if page["truncated"] or page["events"] or page["head"] > page["cursor"] or timeout_ms == 0:
            return {**page, "timedOut": False}
        deadline = self._monotonic() + timeout_ms / 1000.0
        with self._condition:
            while True:
                if stop is not None and stop.is_set():
                    raise BoardError("WAIT_ABANDONED", "The wait was abandoned; no task state was changed")
                observed = self._filtered_head(task_id)
                if observed > page["cursor"]:
                    break
                remaining = deadline - self._monotonic()
                if remaining <= 0:
                    return {**page, "timedOut": True}
                self._condition.wait(min(remaining, 1.0))
        page = self.events_read({"after": after, "limit": limit, **({"taskId": task_id} if task_id else {})})
        return {**page, "timedOut": False}

    def _filtered_head(self, task_id: str | None) -> int:
        if not task_id:
            return self._head
        with self.db.read() as connection:
            row = connection.execute(
                "SELECT COALESCE(MAX(seq), 0) AS head FROM events WHERE task_id = ?", (task_id,)
            ).fetchone()
        return int(row["head"])

    def _monotonic(self) -> float:
        import time

        return time.monotonic()

    def _event_view(self, row: sqlite3.Row) -> dict:
        return {
            "seq": int(row["seq"]),
            "kind": row["kind"],
            "taskId": row["task_id"],
            "attemptId": row["attempt_id"],
            "revision": row["revision"],
            "payload": json.loads(row["payload_json"]),
            "createdAt": row["created_at"],
        }

    # -- receipts ------------------------------------------------------------
    def _subject(self, worker_id: str, nonce: str, attempt_id: str | None = None) -> str:
        """Bind an idempotent receipt to the exact caller that created it.

        A replayed command receipt must come from the same worker presenting the
        same nonce; without this, a different worker could read back another
        worker's claim (including its capability) by guessing a command id.
        """
        return f"{worker_id}:{self.db.nonce_verifier(nonce)}:{attempt_id or '-'}"

    def _attach_agent_credential(self, connection: sqlite3.Connection, claim: dict) -> dict:
        """Re-derive the attempt-scoped credential of a replayed governed claim.

        The token is deterministic in (secret, run, attempt, generation) and stays
        out of the stored receipt; it is returned only while the credential is still
        active, so a replay after conclusion cannot hand out a revoked capability.
        """
        turn = claim.get("turn")
        attempt = claim.get("attempt") or {}
        if not isinstance(turn, dict) or not turn.get("turnId") or not attempt.get("attemptId"):
            return claim
        row = connection.execute(
            "SELECT state FROM agent_credentials WHERE attempt_id=? AND state='active'",
            (attempt["attemptId"],),
        ).fetchone()
        if row is None:
            return claim
        return {
            **claim,
            "agentCredential": self.db.agent_token(
                attempt["taskId"], attempt["attemptId"], attempt["generation"]
            ),
        }

    def _receipt(
        self,
        connection: sqlite3.Connection,
        command_id: str,
        kind: str,
        request: dict,
        subject: str | None = None,
    ) -> dict | None:
        row = connection.execute("SELECT * FROM commands WHERE command_id = ?", (command_id,)).fetchone()
        if row is None:
            return None
        if subject is not None and row["subject"] and not _constant_time_equal(row["subject"], subject):
            raise BoardError(
                "UNAUTHORIZED",
                "This commandId belongs to a different worker identity; it cannot be replayed from here",
                commandId=command_id,
            )
        if row["kind"] != kind or row["request_hash"] != sha256_text(canonical_json(request)):
            raise BoardError(
                "CONFLICT",
                "This commandId already belongs to a different request; use a new commandId",
                commandId=command_id,
            )
        return json.loads(row["response_json"])

    def _store_receipt(
        self,
        connection: sqlite3.Connection,
        command_id: str,
        kind: str,
        request: dict,
        response: dict,
        *,
        task_id: str | None = None,
        attempt_id: str | None = None,
        subject: str | None = None,
    ) -> None:
        connection.execute(
            "INSERT OR REPLACE INTO commands(command_id, task_id, attempt_id, kind, request_hash, response_json,"
            " subject, created_at) VALUES(?,?,?,?,?,?,?,?)",
            (
                command_id,
                task_id,
                attempt_id,
                kind,
                sha256_text(canonical_json(request)),
                canonical_json(response),
                subject,
                self.now(),
            ),
        )

    # -- views ---------------------------------------------------------------
    @staticmethod
    def _task_view(row: sqlite3.Row) -> dict:
        spec = json.loads(row["spec_json"])
        view = {
            "runId": row["task_id"],
            "taskId": row["task_id"],
            "requestId": row["request_id"],
            "owner": row["owner"],
            "adapter": row["adapter"],
            "cwd": row["cwd"],
            "task": spec.get("task"),
            "spec": spec,
            "inputFingerprint": row["input_fingerprint"],
            "fingerprintVersion": row["fingerprint_version"],
            "requiredCapabilities": json.loads(row["required_capabilities"]),
            "exclusiveResources": json.loads(row["exclusive_resources"]),
            "timeoutSeconds": row["timeout_seconds"],
            "status": row["state"],
            "state": row["state"],
            "queueReason": row["queue_reason"],
            "revision": row["revision"],
            "selectedAttemptId": row["selected_attempt_id"],
            "activeAttemptId": row["active_attempt_id"],
            "createdAt": row["created_at"],
            "updatedAt": row["updated_at"],
            "acceptedAt": row["accepted_at"],
            "acceptanceNote": row["acceptance_note"],
            "acceptanceVerdict": row["acceptance_verdict"],
            "resultAvailable": False,
            "shutdownConfirmed": False,
            # The bounded latest native activity of the selected attempt, or null.
            # A task view always carries the field so a reader can distinguish
            # "no observation" from "field not supported".
            "activity": None,
        }
        return view

    @staticmethod
    def _attempt_view(row: sqlite3.Row, *, include_result: bool = True) -> dict:
        """Public attempt record. The claim capability and nonce verifier never appear."""
        view = {
            "attemptId": row["attempt_id"],
            "taskId": row["task_id"],
            "generation": row["generation"],
            "workerId": row["worker_id"],
            "workerIdentity": row["worker_identity"],
            "workerInstance": row["worker_instance"],
            "adapter": row["adapter"],
            "executionState": row["execution_state"],
            "ownership": row["ownership"],
            # The model family frozen onto this attempt at claim time (observation
            # only), or null for model-less work.
            "modelFamily": (
                {"adapter": row["model_adapter"], "provider": row["model_provider"], "model": row["model_model"]}
                if row["model_model"] is not None
                else None
            ),
            "runtimeIdentity": row["runtime_identity"],
            "leaseExpiresAt": row["lease_expires_at"],
            "leaseSeconds": row["lease_seconds"],
            "logPaths": json.loads(row["log_paths"]),
            "resultAvailable": row["result_json"] is not None,
            "tokenUsage": json.loads(row["token_usage_json"]) if row["token_usage_json"] else None,
            "shutdownConfirmed": bool(row["shutdown_confirmed"]),
            "cancelRequested": row["cancel_requested_at"] is not None,
            "cancelRequestedAt": row["cancel_requested_at"],
            "exitCode": row["exit_code"],
            "signal": row["signal"],
            "error": row["error"],
            "startedAt": row["started_at"],
            "finishedAt": row["finished_at"],
            "createdAt": row["created_at"],
            "updatedAt": row["updated_at"],
            "revision": row["revision"],
        }
        if include_result and row["result_json"] is not None:
            view["result"] = json.loads(row["result_json"])
        return view

    @staticmethod
    def _message_view(row: sqlite3.Row) -> dict:
        return {
            "messageId": row["message_id"],
            "taskId": row["task_id"],
            "attemptId": row["attempt_id"],
            "inquiryId": row["inquiry_id"],
            "direction": row["direction"],
            "author": row["author"],
            "recipient": row["recipient"],
            "correlationId": row["correlation_id"],
            "question": row["body"],
            "questionBytes": row["body_bytes"],
            "questionSha256": row["payload_hash"],
            "state": row["state"],
            "reason": row["reason"],
            "delivery": json.loads(row["delivery_json"]) if row["delivery_json"] else None,
            "answer": json.loads(row["answer_json"]) if row["answer_json"] else None,
            "attempts": row["attempts_count"],
            "createdAt": row["created_at"],
            "updatedAt": row["updated_at"],
            "revision": row["revision"],
        }

    @staticmethod
    def _artifact_view(row: sqlite3.Row) -> dict:
        return {
            "artifactId": row["artifact_id"],
            "taskId": row["task_id"],
            "attemptId": row["attempt_id"],
            "kind": row["kind"],
            "location": row["location"],
            "contentHash": row["content_hash"],
            "sizeBytes": row["size_bytes"],
            "verified": bool(row["verified"]),
            "createdAt": row["created_at"],
        }

    @staticmethod
    def _worker_view(row: sqlite3.Row) -> dict:
        return {
            "workerId": row["worker_id"],
            "identity": row["identity"],
            "adapter": row["adapter"],
            "capabilities": json.loads(row["capabilities"]),
            "host": row["host"],
            "pid": row["pid"],
            "state": row["state"],
            "currentAttemptId": row["current_attempt_id"],
            "registeredAt": row["registered_at"],
            "lastSeenAt": row["last_seen_at"],
            "revision": row["revision"],
        }

    def _task_row(self, connection: sqlite3.Connection, selector: dict) -> sqlite3.Row:
        task_id = selector.get("taskId") or selector.get("runId")
        request_id = selector.get("requestId")
        if isinstance(task_id, str) and task_id:
            row = connection.execute("SELECT * FROM tasks WHERE task_id = ?", (task_id,)).fetchone()
        elif isinstance(request_id, str) and request_id:
            row = connection.execute("SELECT * FROM tasks WHERE request_id = ?", (request_id,)).fetchone()
        else:
            raise BoardError("INVALID_ARGUMENT", "runId, taskId or requestId is required")
        if row is None:
            raise BoardError("NOT_FOUND", "Unknown runId or requestId")
        return row

    def _attempt_row(self, connection: sqlite3.Connection, attempt_id: str) -> sqlite3.Row:
        row = connection.execute("SELECT * FROM attempts WHERE attempt_id = ?", (attempt_id,)).fetchone()
        if row is None:
            raise BoardError("NOT_FOUND", "Unknown attemptId")
        return row

    def _selected_attempt(self, connection: sqlite3.Connection, task: sqlite3.Row) -> sqlite3.Row | None:
        """The one effective attempt of a task.

        ``active_attempt_id`` wins: after an explicit retry the task must be read,
        cancelled and questioned against its newest generation, never against the
        superseded attempt the retry replaced.
        """
        attempt_id = task["active_attempt_id"] or task["selected_attempt_id"]
        if not attempt_id:
            return None
        return connection.execute("SELECT * FROM attempts WHERE attempt_id = ?", (attempt_id,)).fetchone()

    def _decorate(self, connection: sqlite3.Connection, task: sqlite3.Row, *, delegation_result: dict | None = None) -> dict:
        view = self._task_view(task)
        if task["state"] in ("cancelled", "cancelling"):
            from .cancellation import for_run
            view["cancellation"] = for_run(connection, task["task_id"])
        attempt = self._selected_attempt(connection, task)
        view["tokenUsage"] = json.loads(attempt["token_usage_json"]) if attempt is not None and attempt["token_usage_json"] else None
        if attempt is not None:
            from .native_observations import failure_view
            recorded = json.loads(attempt["result_json"]) if attempt["result_json"] else {}
            view["quotaFailure"] = failure_view(recorded.get("result"), adapter=attempt["adapter"])
            view["selectedAttempt"] = self._attempt_view(attempt)
            from .accounts import attempt_account
            view['selectedAttempt']['account'] = attempt_account(connection, attempt)
            view["resultAvailable"] = attempt["result_json"] is not None
            view["shutdownConfirmed"] = bool(attempt["shutdown_confirmed"])
            view["attemptGeneration"] = attempt["generation"]
            view["attemptState"] = attempt["execution_state"]
            view["workerId"] = attempt["worker_id"]
            view["logPaths"] = json.loads(attempt["log_paths"])
            view["cancelRequested"] = attempt["cancel_requested_at"] is not None
            view["timeoutSeconds"] = task["timeout_seconds"]
            # The projection is looked up by the *selected* attempt, so a replacement
            # generation never inherits the previous attempt's observation.
            view["activity"] = self._activity_latest(connection, attempt["attempt_id"])
        artefacts = connection.execute(
            "SELECT * FROM artifacts WHERE task_id = ? ORDER BY created_at", (task["task_id"],)
        ).fetchall()
        view["artifacts"] = [self._artifact_view(row) for row in artefacts]
        view["artifactCount"] = len(artefacts)
        messages = connection.execute(
            "SELECT state, COUNT(*) AS count FROM messages WHERE task_id = ? GROUP BY state", (task["task_id"],)
        ).fetchall()
        view["inquiries"] = {row["state"]: row["count"] for row in messages}
        # Read-only delegation provenance (source/current Host, lineage and project)
        # derived from existing authoritative rows; a caller that already resolved the
        # page passes it in so one page costs one relation read.
        view["delegation"] = delegation_result if delegation_result is not None else delegation.resolve(connection, task)
        workflow = self.workflow.task_extension(connection, task)
        if workflow is not None:
            view["workflowShutdown"] = self.workflow.shutdown_summary(connection, task["task_id"])
            view["workflow"] = workflow
            view["workflowState"] = workflow["state"]
            view["awaitingHost"] = workflow["awaitingHost"]
            if workflow["state"] == "cancelled" and "cancellation" not in view:
                from .cancellation import for_run
                view["cancellation"] = for_run(connection, task["task_id"])
        return view

    # -- admission -----------------------------------------------------------
    #: An attempt still consumes capacity and appears in drain reporting while its
    #: ownership is unresolved: active execution states, or a committed result whose
    #: shutdown is unconfirmed (a survivor may still be writing).
    UNRESOLVED_SQL = (
        "execution_state IN ('starting','executing','finalizing','uncertain')"
        " OR (result_json IS NOT NULL AND shutdown_confirmed = 0)"
    )

    #: One bounded candidate scan. Tasks of families that are already at their
    #: concurrent-attempt limit are filtered out *before* this LIMIT, so a deep
    #: full-family backlog cannot occupy the whole bounded window and hide unrelated
    #: runnable work.
    CLAIM_CANDIDATE_LIMIT = 50

    #: The queued-task model family as one SQL expression. It mirrors
    #: :func:`scheduling.model_family` over the task's effective specification: a
    #: governed run's resolved execution configuration wins over the immutable
    #: original request, and tasks without a complete adapter/provider/model triple
    #: have no family.
    _QUEUED_FAMILY_PROVIDER = (
        "COALESCE(json_extract(r.execution_configuration_json,'$.provider'),"
        " json_extract(t.spec_json,'$.provider'))"
    )
    _QUEUED_FAMILY_MODEL = (
        "COALESCE(json_extract(r.execution_configuration_json,'$.model'),"
        " json_extract(t.spec_json,'$.model'))"
    )

    def _total_active(self, connection: sqlite3.Connection) -> int:
        """Unresolved attempts against the machine-wide ceiling."""
        row = connection.execute(f"SELECT COUNT(*) AS total FROM attempts WHERE {self.UNRESOLVED_SQL}").fetchone()
        return int(row["total"])

    def _model_active_counts(self, connection: sqlite3.Connection) -> dict[tuple[str, str, str], int]:
        """Unresolved attempts per frozen model family.

        The count uses the family frozen onto each attempt at claim time, so an
        uncertain attempt or an unconfirmed result keeps its family slot exactly
        like its machine-wide slot.
        """
        rows = connection.execute(
            f"SELECT model_adapter, model_provider, model_model, COUNT(*) AS active FROM attempts"
            f" WHERE ({self.UNRESOLVED_SQL}) AND model_model IS NOT NULL"
            " GROUP BY model_adapter, model_provider, model_model"
        ).fetchall()
        return {
            (row["model_adapter"], row["model_provider"], row["model_model"]): int(row["active"])
            for row in rows
        }

    def model_limits(self, connection: sqlite3.Connection) -> dict[tuple[str, str, str], int]:
        """Explicit per-family limits from the user-owned ``model_concurrency`` table."""
        rows = connection.execute("SELECT adapter, provider, model, concurrency_limit FROM model_concurrency").fetchall()
        return {
            (row["adapter"], row["provider"], row["model"]): scheduling.clamp_model_limit(row["concurrency_limit"])
            for row in rows
        }

    def _effective_model_limit(
        self, connection: sqlite3.Connection, family: tuple[str, str, str], *, limits: dict | None = None
    ) -> int:
        if limits is None:
            limits = self.model_limits(connection)
        return limits.get(family, scheduling.MODEL_LIMIT_DEFAULT)

    def model_capacity_rows(
        self, connection: sqlite3.Connection, families: Iterable[tuple[str, str, str]] | None = None
    ) -> list[dict]:
        """Observation rows ``{adapter, provider, model, limit, active}`` for capacity views.

        ``families`` restricts the rows to the families represented in one response
        (a console snapshot page or a model-profile page). Without it, the rows cover
        every family relevant to capacity right now: explicit settings, families of
        unresolved attempts and families of queued work.
        """
        limits = self.model_limits(connection)
        active = self._model_active_counts(connection)
        if families is None:
            families = set(limits) | set(active) | set(self._queued_families(connection))
        rows = []
        for family in sorted(set(families)):
            adapter, provider, model = family
            rows.append(
                {
                    "adapter": adapter,
                    "provider": provider,
                    "model": model,
                    "limit": limits.get(family, scheduling.MODEL_LIMIT_DEFAULT),
                    "active": active.get(family, 0),
                }
            )
        return rows

    def _queued_families(self, connection: sqlite3.Connection) -> set[tuple[str, str, str]]:
        rows = connection.execute(
            "SELECT DISTINCT t.adapter AS adapter,"
            f" {self._QUEUED_FAMILY_PROVIDER} AS provider, {self._QUEUED_FAMILY_MODEL} AS model"
            " FROM tasks t LEFT JOIN workflow_runs r ON r.run_id = t.task_id WHERE t.state='queued'"
        ).fetchall()
        families = {
            (row["adapter"], row["provider"], row["model"])
            for row in rows
            if row["provider"] and row["model"] and row["adapter"] in schemas.CODING_ADAPTERS
        }
        for row in connection.execute("SELECT r.input_json FROM decision_requests r JOIN tasks t ON t.task_id=r.task_id WHERE t.state='queued'"):
            profile = (json.loads(row["input_json"]) if row["input_json"] else {}).get("profile") or {}
            if all(profile.get(key) for key in ("adapter", "provider", "model")):
                families.add(tuple(profile[key] for key in ("adapter", "provider", "model")))
        return families

    def _full_families(self, connection: sqlite3.Connection) -> set[tuple[str, str, str]]:
        """Families whose unresolved attempts already hold their effective limit."""
        active = self._model_active_counts(connection)
        if not active:
            return set()
        limits = self.model_limits(connection)
        return {
            family
            for family, count in active.items()
            if count >= limits.get(family, scheduling.MODEL_LIMIT_DEFAULT)
        }

    def capacity_report(self) -> dict:
        """The machine-wide ceiling and the per-family observation rows.

        ``totalLimit`` is the aggregate execution limit the daemon must reserve
        worker slots for; it is deliberately separate from the WAIT admission counts.
        ``models`` observes the families relevant to pending work, active attempts
        and explicit settings; ``active`` is observation only.
        """
        with self.db.read() as connection:
            models = self.model_capacity_rows(connection)
            total_active = self._total_active(connection)
        return {
            "totalLimit": self.max_concurrent,
            "totalActive": total_active,
            "models": models,
        }

    def unresolved_worker_attempts(self, worker_id: str) -> list[str]:
        """Every attempt ``worker_id`` still owns under :data:`UNRESOLVED_SQL`.

        Used by pool reconciliation to decide whether a surplus supervisor may be
        asked to drain: while it owns an unresolved attempt (or holds a pending local
        receipt), its slot and receipts are retained rather than lost. A missing
        process is never inferred to have stopped any of these attempts.
        """
        with self.db.read() as connection:
            rows = connection.execute(
                f"SELECT attempt_id FROM attempts WHERE worker_id = ? AND ({self.UNRESOLVED_SQL})"
                " ORDER BY created_at DESC",
                (worker_id,),
            ).fetchall()
        return [row["attempt_id"] for row in rows]

    def unresolved_worker_attempt(self, worker_id: str) -> str | None:
        """The newest attempt ``worker_id`` still owns, or ``None``."""
        attempts = self.unresolved_worker_attempts(worker_id)
        return attempts[0] if attempts else None

    def _held_claims(self, connection: sqlite3.Connection) -> list[sqlite3.Row]:
        return connection.execute(
            "SELECT * FROM resource_claims WHERE state IN ('held','retained')"
        ).fetchall()

    def _admission_blocker(self, connection: sqlite3.Connection, spec: dict, *, exclude_task: str | None = None) -> str | None:
        """Why this specification cannot start right now, or ``None`` when it can.

        The machine-wide ceiling is checked first, then the specification's own model
        family, so the queue reason names the limit that is actually full. Workspace
        and exclusive-resource admission applies to all work unchanged.
        """
        if (self.directory / 'upgrade.json').exists():
            raise BoardError('UPGRADE_IN_PROGRESS', 'Admission is fenced until the coordinated upgrade finishes')
        if self._total_active(connection) >= self.max_concurrent:
            return scheduling.REASON_TOTAL_CAPACITY
        family = scheduling.model_family(spec)
        if family is not None:
            from .accounts import selection, assert_credentials_current, ADAPTERS
            if family[0] in ADAPTERS:
                try:
                    assert_credentials_current(connection, selection(connection, family[0]))
                except BoardError as error:
                    if error.code != 'ACCOUNT_IN_USE':
                        raise
                    return 'awaiting-configuration-validation'
            active = self._model_active_counts(connection)
            if active.get(family, 0) >= self._effective_model_limit(connection, family):
                return scheduling.REASON_MODEL_CAPACITY
        resources = {"cwd": [spec["cwd"]], "exclusive": list(spec.get("exclusiveResources", []))}
        for claim in self._held_claims(connection):
            if exclude_task and claim["task_id"] == exclude_task:
                continue
            if claim["kind"] == "cwd" and any(_overlaps(spec["cwd"], claim["resource"]) for _ in resources["cwd"]):
                return "cwd-overlap"
            if claim["kind"] == "exclusive" and claim["resource"] in resources["exclusive"]:
                return "exclusive-resource"
        return None

    def _family_admission_blocker(self, connection: sqlite3.Connection, family: tuple[str, str, str]) -> str | None:
        """The capacity blocker for one already-resolved family (no spec needed).

        Used where the family is resolved inside the claim transaction itself — the
        fixed decision profile of a routing task — so its quota is checked *before*
        the claim commits and before any selection reader is admitted.
        """
        if self._total_active(connection) >= self.max_concurrent:
            return scheduling.REASON_TOTAL_CAPACITY
        active = self._model_active_counts(connection)
        if active.get(family, 0) >= self._effective_model_limit(connection, family):
            return scheduling.REASON_MODEL_CAPACITY
        return None

    def _claim_candidates(
        self, connection: sqlite3.Connection
    ) -> tuple[list[sqlite3.Row], set[tuple[str, str, str]]]:
        """Bounded queued candidates in arrival order, skipping full model families.

        Tasks of a family already at its concurrent-attempt limit are excluded by the
        SQL predicate *before* the candidate LIMIT, so >100 queued tasks blocked on
        one family cannot hide a runnable task of another family behind the bounded
        window. Routing tasks are scanned through their own bounded window: a
        decision task's family is the mutable fixed selector resolved at claim time,
        so it cannot be pre-filtered, and a backlog of routing work blocked on the
        selector's family must not hide runnable business work either. The full
        families are returned so an empty claim can name the limit that is holding
        work instead of pretending the board is idle.
        """
        full = self._full_families(connection)
        family_clauses = []
        family_values: list[object] = []
        for adapter, provider, model in sorted(full):
            # Null-safe on purpose: a task without a complete family triple
            # (command/external work, an unresolved run, a routing task) is never
            # excluded — SQL's NOT over a NULL conjunction would otherwise drop it.
            family_clauses.append(
                f"({self._QUEUED_FAMILY_PROVIDER} IS NULL OR {self._QUEUED_FAMILY_MODEL} IS NULL"
                f" OR NOT (t.adapter=? AND {self._QUEUED_FAMILY_PROVIDER}=? AND {self._QUEUED_FAMILY_MODEL}=?))"
            )
            family_values.extend((adapter, provider, model))
        rows: list[sqlite3.Row] = []
        for class_predicate in ("t.adapter IS NOT ?", "t.adapter IS ?"):
            rows.extend(
                connection.execute(
                    "SELECT t.* FROM tasks t LEFT JOIN workflow_runs r ON r.run_id=t.task_id"
                    f" WHERE t.state='queued' AND {class_predicate}"
                    + ((" AND " + " AND ".join(family_clauses)) if family_clauses else "")
                    + " ORDER BY t.created_at, t.task_id LIMIT ?",
                    (scheduling.DECISION_ADAPTER, *family_values, self.CLAIM_CANDIDATE_LIMIT),
                ).fetchall()
            )
        rows.sort(key=lambda row: (row["created_at"], row["task_id"]))
        return rows, full

    def _retain_claims(self, connection: sqlite3.Connection, attempt_id: str) -> None:
        connection.execute(
            "UPDATE resource_claims SET state='retained' WHERE attempt_id = ? AND state='held'", (attempt_id,)
        )

    def _release_claims(self, connection: sqlite3.Connection, attempt_id: str) -> None:
        connection.execute(
            "UPDATE resource_claims SET state='released', released_at=? WHERE attempt_id = ? AND state IN ('held','retained')",
            (self.now(), attempt_id),
        )

    def _restore_claims(self, connection: sqlite3.Connection, attempt_id: str) -> None:
        """Return retained claims to held when the owning attempt resumes for real."""
        connection.execute(
            "UPDATE resource_claims SET state='held', released_at=NULL WHERE attempt_id = ? AND state='retained'",
            (attempt_id,),
        )

    # -- bounded activity projection -----------------------------------------
    def _activity_latest(self, connection: sqlite3.Connection, attempt_id: str) -> dict | None:
        """The newest stored activity of exactly this attempt, or ``None``."""
        row = connection.execute(
            "SELECT activity_json FROM attempt_activity WHERE attempt_id = ?", (attempt_id,)
        ).fetchone()
        if row is None:
            return None
        try:
            value = json.loads(row["activity_json"])
        except ValueError:
            return None
        return value if isinstance(value, dict) else None

    def _store_activity(self, connection: sqlite3.Connection, attempt: sqlite3.Row, activity: dict) -> bool:
        """Persist one monotone activity receipt.

        ``True`` means the projection advanced; an identical repeat and an older
        receipt are both idempotent no-ops, so a replayed sidecar cannot move the
        task view backwards or append another event.
        """
        previous = self._activity_latest(connection, attempt["attempt_id"])
        if previous is not None and not activity_module.is_newer(activity, previous):
            return False
        now = self.now()
        connection.execute(
            "INSERT INTO attempt_activity(attempt_id, task_id, generation, event_seq, observed_at, activity_json,"
            " created_at, updated_at, revision) VALUES(?,?,?,?,?,?,?,?,1)"
            " ON CONFLICT(attempt_id) DO UPDATE SET generation=excluded.generation, event_seq=excluded.event_seq,"
            " observed_at=excluded.observed_at, activity_json=excluded.activity_json, updated_at=excluded.updated_at,"
            " revision=revision+1",
            (
                attempt["attempt_id"],
                attempt["task_id"],
                attempt["generation"],
                activity.get("eventSeq"),
                activity.get("observedAt"),
                canonical_json(activity),
                now,
                now,
            ),
        )
        return True

    # -- tasks ---------------------------------------------------------------
    def task_submit(
        self,
        params: dict,
        *,
        command_id: str | None = None,
        task_id: str | None = None,
        governed: dict | None = None,
    ) -> dict:
        """Admit one ordinary task.

        ``governed`` is the coordinator's hook: when present, the workflow run and its
        workspace reservation commit in the same transaction as the task row, so a
        governed task can never exist without its governance record.
        """
        spec = governed["spec"] if governed is not None else schemas.normalize_spec(params)
        if governed is None and spec["adapter"] in schemas.CODING_ADAPTERS:
            raise BoardError("GOVERNED_REQUIRED", "Coding work requires submit (workflow_submit) so routing, ownership and turn evidence remain governed")
        request_id = schemas.required_string(params, "requestId", max_length=schemas.MAX_REQUEST_ID)
        owner = schemas.optional_string(params, "owner") or "cli"
        fingerprint = schemas.spec_fingerprint(spec)
        request = {"requestId": request_id, "fingerprint": fingerprint}

        with self.db.write() as connection:
            if command_id:
                receipt = self._receipt(connection, command_id, "task.submit", request)
                if receipt is not None:
                    return receipt
            existing = connection.execute("SELECT * FROM tasks WHERE request_id = ?", (request_id,)).fetchone()
            if existing is not None:
                if governed is not None:
                    run = self.workflow._run_optional(connection, existing["task_id"])
                    if run is None or run["request_fingerprint"] != governed["requestFingerprint"]:
                        raise BoardError("CONFLICT", "requestId already belongs to a different governed request")
                if existing["input_fingerprint"] == fingerprint:
                    response = {"task": self._decorate(connection, existing), "duplicate": True}
                else:
                    raise BoardError(
                        "CONFLICT",
                        "requestId already belongs to a different input; use a new requestId",
                        requestId=request_id,
                        existingTaskId=existing["task_id"],
                        existingFingerprint=existing["input_fingerprint"],
                        providedFingerprint=fingerprint,
                    )
                if command_id:
                    self._store_receipt(
                        connection, command_id, "task.submit", request, response, task_id=existing["task_id"]
                    )
                head = self._head_of(connection)
            else:
                task_id = task_id or str(uuid.uuid4())
                now = self.now()
                blocker = self._admission_blocker(connection, spec)
                connection.execute(
                    "INSERT INTO tasks(task_id, request_id, owner, spec_json, spec_canonical_json, input_fingerprint,"
                    " fingerprint_version, adapter, required_capabilities, cwd, exclusive_resources, timeout_seconds,"
                    " state, queue_reason, revision, created_at, updated_at)"
                    " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        task_id,
                        request_id,
                        owner,
                        canonical_json(spec),
                        canonical_json(spec),
                        fingerprint,
                        schemas.FINGERPRINT_VERSION_CURRENT,
                        spec.get("adapter") or "unresolved",
                        canonical_json(spec.get("requiredCapabilities", [])),
                        spec["cwd"],
                        canonical_json(spec.get("exclusiveResources", [])),
                        spec["timeoutSeconds"],
                        "queued",
                        blocker or "awaiting-worker",
                        1,
                        now,
                        now,
                    ),
                )
                self._append_event(
                    connection,
                    "task.submitted",
                    task_id=task_id,
                    revision=1,
                    payload={
                        "requestId": request_id,
                        "adapter": spec.get("adapter"),
                        "cwd": spec["cwd"],
                        "queueReason": blocker or "awaiting-worker",
                        "inputFingerprint": fingerprint,
                        **({"governed": {"sourceHostId": governed["hostId"]}} if governed is not None else {}),
                    },
                )
                row = connection.execute("SELECT * FROM tasks WHERE task_id = ?", (task_id,)).fetchone()
                if governed is not None:
                    self.workflow.attach_governed_task(connection, row, now=now, **governed)
                    row = connection.execute("SELECT * FROM tasks WHERE task_id = ?", (task_id,)).fetchone()
                response = {"task": self._decorate(connection, row), "duplicate": False}
                if command_id:
                    self._store_receipt(connection, command_id, "task.submit", request, response, task_id=task_id)
                head = self._head_of(connection)
        self._notify(head)
        return response

    def task_get(self, params: dict) -> dict:
        schemas.reject_unknown(params, {"runId", "taskId", "requestId"}, "task.get")
        with self.db.read() as connection:
            row = self._task_row(connection, params)
            return {"task": self._decorate(connection, row)}

    def task_list(self, params: dict) -> dict:
        """Bounded task history with explicit filters and stable keyset paging.

        Every filter is one SQL predicate over the read-only delegation relation
        (:mod:`buddy.delegation`), so it applies *before* LIMIT and ``total`` counts
        exactly the filtered set. ``before`` is a keyset cursor over
        ``(created_at DESC, task_id DESC)`` and is mutually exclusive with a nonzero
        ``offset``: a page never loads the board into Python to slice it, and a row
        inserted after the cursor was issued cannot shift, duplicate or skip the
        remaining pages. ``nextCursor`` is non-null exactly while more rows match.
        """
        schemas.reject_unknown(
            params,
            {
                "limit",
                "offset",
                "state",
                "adapter",
                "before",
                "rootsOnly",
                "query",
                "projectId",
                "hostId",
                "filter",
            },
            "task.list",
        )
        limit = schemas.optional_int(params, "limit", 20, 1, 100)
        offset = schemas.optional_int(params, "offset", 0, 0, 2**31)
        state = params.get("state")
        if state is not None and state not in TASK_STATES:
            raise BoardError("INVALID_ARGUMENT", "Unknown task state filter")
        adapter = params.get("adapter")
        if adapter is not None and adapter not in schemas.ADAPTERS:
            raise BoardError("INVALID_ARGUMENT", "Unknown adapter filter")
        before_value = schemas.optional_string(params, "before", max_length=delegation.CURSOR_MAX_LENGTH)
        before = delegation.decode_cursor(before_value) if before_value is not None else None
        if before is not None and offset:
            raise BoardError(
                "INVALID_ARGUMENT",
                "before and a nonzero offset are mutually exclusive; use one paging mode",
            )
        roots_only = schemas.optional_bool(params, "rootsOnly", False)
        query = schemas.optional_string(params, "query", max_length=delegation.QUERY_MAX_LENGTH)
        project_id = schemas.optional_string(params, "projectId", max_length=delegation.PROJECT_ID_MAX_LENGTH)
        host_id = schemas.optional_string(params, "hostId", max_length=delegation.HOST_ID_MAX_LENGTH)
        filter_name = schemas.optional_string(params, "filter", max_length=16) or "all"
        if filter_name not in delegation.HISTORY_FILTERS:
            raise BoardError("INVALID_ARGUMENT", "filter must be one of: all, active, host, review")
        clauses, values = [], []
        if state:
            clauses.append("state = ?")
            values.append(state)
        if adapter:
            clauses.append("adapter = ?")
            values.append(adapter)
        history_clauses, history_values = delegation.history_where(
            roots_only=roots_only,
            project_id=project_id,
            host_id=host_id,
            query=query,
            filter_name=filter_name,
        )
        clauses.extend(history_clauses)
        values.extend(history_values)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self.db.read() as connection:
            total = connection.execute(
                f"SELECT COUNT(*) AS count FROM ({delegation.TASK_HISTORY_SQL}) AS history {where}", values
            ).fetchone()["count"]
            page_clauses, parameters = list(clauses), list(values)
            if before is not None:
                keyset, keyset_values = delegation.keyset_where(*before)
                page_clauses.append(keyset)
                parameters.extend(keyset_values)
            page_where = f"WHERE {' AND '.join(page_clauses)}" if page_clauses else ""
            statement = f"{delegation.TASK_HISTORY_SQL} {page_where} ORDER BY created_at DESC, task_id DESC LIMIT ?"
            parameters.append(limit + 1)
            if before is None:
                statement += " OFFSET ?"
                parameters.append(offset)
            rows = connection.execute(statement, parameters).fetchall()
            # The extra row only proves another page exists; it is never returned and
            # never decorated.
            has_more = len(rows) > limit
            page = rows[:limit]
            tasks = [
                self._decorate(connection, row, delegation_result=delegation.metadata_from_row(row)) for row in page
            ]
            next_cursor = (
                delegation.encode_cursor(page[-1]["created_at"], page[-1]["task_id"]) if page and has_more else None
            )
            head = self._head_of(connection)
        return {"runs": tasks, "tasks": tasks, "total": int(total), "cursor": head, "nextCursor": next_cursor}

    def task_result(self, params: dict) -> dict:
        schemas.reject_unknown(params, {"runId", "taskId", "requestId"}, "task.result")
        with self.db.read() as connection:
            task = self._task_row(connection, params)
            attempt = self._selected_attempt(connection, task)
            if attempt is None:
                raise BoardError("NOT_READY", "This run has no attempt result yet")
            if attempt["result_json"] is None:
                raise BoardError("NOT_READY", "The runner result is not available yet")
            view = self._decorate(connection, task)
            payload = json.loads(attempt["result_json"])
            if not isinstance(payload, dict) or not {"status", "result", "shutdownConfirmed"}.issubset(payload):
                raise BoardError(
                    "UNSUPPORTED_RESULT_FORMAT",
                    "The stored result does not use the current receipt format; use a clean state directory",
                )
            # Only the current worker receipt is unpacked. Its adapter result is
            # opaque data, even when it contains fields named result or turn.
            view["result"] = payload["result"]
            view["resultMeta"] = {key: value for key, value in payload.items() if key != "result"}
            view["attemptId"] = attempt["attempt_id"]
            view["resultDelivered"] = view["result"] is not None
        return view

    def task_cancel(self, params: dict, *, command_id: str | None = None) -> dict:
        schemas.reject_unknown(
            params,
            {
                "runId",
                "taskId",
                "requestId",
                "reason",
                "requestedBy",
                *schemas.CONTROL_FIELDS,
                schemas.CONSOLE_AUTHORITY_FIELD,
            },
            "task.cancel",
        )
        schemas.reject_untrusted_override(params)
        reason = schemas.optional_string(params, "reason") or "operator request"
        actor = schemas.optional_string(params, "requestedBy") or "cli"
        request = {"selector": {k: params[k] for k in ("runId", "taskId", "requestId") if k in params}, "reason": reason}
        with self.db.write() as connection:
            if command_id:
                receipt = self._receipt(connection, command_id, "task.cancel", request)
                if receipt is not None:
                    return receipt
            task = self._task_row(connection, params)
            self.workflow.guard_task_control(connection, task, params, operation="task_cancel")
            now = self.now()
            already = task["state"] in TERMINAL_TASK_STATES
            if not already:
                attempt = self._selected_attempt(connection, task)
                if task["state"] == "queued" and (attempt is None or attempt["execution_state"] == "finished"):
                    self._transition_task(connection, task, "cancelled")
                    connection.execute(
                        "UPDATE tasks SET queue_reason = NULL WHERE task_id = ?", (task["task_id"],)
                    )
                    self._append_event(
                        connection,
                        "task.cancelled",
                        task_id=task["task_id"],
                        revision=task["revision"] + 1,
                        payload={"reason": reason, "actor": actor, "phase": "queued"},
                    )
                else:
                    if task["state"] not in ("cancelling",):
                        self._transition_task(connection, task, "cancelling")
                    if attempt is not None and attempt["cancel_requested_at"] is None:
                        connection.execute(
                            "UPDATE attempts SET cancel_requested_at=?, updated_at=?, revision=revision+1"
                            " WHERE attempt_id = ?",
                            (now, now, attempt["attempt_id"]),
                        )
                    self._append_event(
                        connection,
                        "task.cancel_requested",
                        task_id=task["task_id"],
                        attempt_id=attempt["attempt_id"] if attempt else None,
                        revision=task["revision"] + 1,
                        payload={"reason": reason, "actor": actor},
                    )
                task = connection.execute("SELECT * FROM tasks WHERE task_id = ?", (task["task_id"],)).fetchone()
                # A decision run is an ordinary cancellable task. Cancelling it marks
                # the decision terminal in the same transaction, so a queued selection
                # that is cancelled can never become a current recommendation.
                self.decisions.cancelled(connection, task=task, reason=reason, now=now)
            response = {"task": self._decorate(connection, task), "alreadyTerminal": already}
            if command_id:
                self._store_receipt(
                    connection, command_id, "task.cancel", request, response, task_id=task["task_id"]
                )
            head = self._head_of(connection)
        self._notify(head)
        return response

    def task_retry(self, params: dict, *, command_id: str | None = None) -> dict:
        """Explicitly create a new attempt generation for a non-running task.

        Retrying unknown or failed work is never automatic: a lost RPC or a lost
        worker does not start anything by itself.

        An attempt whose shutdown is unconfirmed keeps its resource claims and
        refuses to be retried. There is deliberately **no operator override**: a
        written reason is not evidence of termination. The only way such an attempt
        is released is verifiable evidence from the worker that owned the process
        handle — it reattaches with its own nonce and later commits a result whose
        ``shutdownConfirmed`` comes from observing that owned process group, or it
        releases an attempt that never crossed the durable spawn intent.
        """
        schemas.reject_unknown(
            params,
            {
                "runId",
                "taskId",
                "requestId",
                "reason",
                "requestedBy",
                *schemas.CONTROL_FIELDS,
                schemas.CONSOLE_AUTHORITY_FIELD,
            },
            "task.retry",
        )
        schemas.reject_untrusted_override(params)
        reason = schemas.optional_string(params, "reason") or "explicit retry"
        request = {
            "selector": {k: params[k] for k in ("runId", "taskId", "requestId") if k in params},
            "reason": reason,
        }
        with self.db.write() as connection:
            if command_id:
                receipt = self._receipt(connection, command_id, "task.retry", request)
                if receipt is not None:
                    return receipt
            task = self._task_row(connection, params)
            self.workflow.guard_task_control(connection, task, params, operation="task_retry")
            spec = json.loads(task["spec_json"])
            if spec.get("decision"):
                # A paid decision call is never replayed implicitly. A decision result
                # is retained and inspected; a new request is an explicit Host choice.
                raise BoardError(
                    "UNSUPPORTED",
                    "A decision run is not retried; submit a new selection_request "
                    "with a new requestId. Nothing was re-executed.",
                    taskId=task["task_id"],
                )
            attempt = self._selected_attempt(connection, task)
            uncertain = attempt is not None and (
                attempt["execution_state"] in ACTIVE_ATTEMPT_STATES or not attempt["shutdown_confirmed"]
            )
            if uncertain:
                raise BoardError(
                    "SHUTDOWN_UNCONFIRMED",
                    "The current attempt's shutdown is unconfirmed, so nothing is released and no replacement may "
                    "touch its resources. The worker that owns the process handle must reattach and report an "
                    "observed outcome; a written reason is not evidence of termination.",
                    attemptId=attempt["attempt_id"],
                    retainedClaims=[claim["resource"] for claim in self._held_claims(connection)],
                )
            if task["state"] in ("running", "cancelling"):
                raise BoardError(
                    "CONFLICT",
                    "This run is still active; cancel it before retrying",
                    state=task["state"],
                    attemptId=attempt["attempt_id"] if attempt else None,
                )
            if attempt is not None and attempt["result_json"] is not None and not attempt["shutdown_confirmed"]:
                raise BoardError(
                    "SHUTDOWN_UNCONFIRMED",
                    "The previous attempt has a result but unconfirmed shutdown; inspect survivors before retrying",
                    attemptId=attempt["attempt_id"],
                )
            spec = json.loads(task["spec_json"])
            if attempt is not None:
                self._release_claims(connection, attempt["attempt_id"])
            blocker = self._admission_blocker(connection, spec, exclude_task=task["task_id"])
            self._transition_task(connection, task, "queued")
            connection.execute(
                "UPDATE tasks SET queue_reason=?, active_attempt_id=NULL, selected_attempt_id=NULL,"
                " accepted_at=NULL, acceptance_note=NULL, acceptance_verdict=NULL WHERE task_id = ?",
                (blocker or "awaiting-worker", task["task_id"]),
            )
            if task["accepted_at"]:
                # The review belongs to the attempt that was reviewed; it is kept as
                # history in the event stream, never inherited by the new attempt.
                self._append_event(
                    connection,
                    "task.review_archived",
                    task_id=task["task_id"],
                    attempt_id=attempt["attempt_id"] if attempt else None,
                    revision=task["revision"] + 1,
                    payload={
                        "acceptedAt": task["accepted_at"],
                        "verdict": task["acceptance_verdict"],
                        "note": task["acceptance_note"],
                    },
                )
            self._append_event(
                connection,
                "task.retried",
                task_id=task["task_id"],
                attempt_id=attempt["attempt_id"] if attempt else None,
                revision=task["revision"] + 1,
                payload={"reason": reason, "previousAttemptId": attempt["attempt_id"] if attempt else None},
            )
            task = connection.execute("SELECT * FROM tasks WHERE task_id = ?", (task["task_id"],)).fetchone()
            response = {"task": self._decorate(connection, task), "retry": True}
            if command_id:
                self._store_receipt(connection, command_id, "task.retry", request, response, task_id=task["task_id"])
            head = self._head_of(connection)
        self._notify(head)
        return response

    def task_acknowledge(self, params: dict, *, command_id: str | None = None) -> dict:
        """Record that a human or agent reviewed the actual result.

        Acceptance never converts failure into success and never rewrites the
        execution status; it records the reviewed verdict plus the evidence that
        made it possible.
        """
        schemas.reject_unknown(
            params,
            {
                "runId",
                "taskId",
                "requestId",
                "note",
                "verdict",
                "evidence",
                "acknowledgedBy",
                *schemas.CONTROL_FIELDS,
                schemas.CONSOLE_AUTHORITY_FIELD,
            },
            "task.acknowledge",
        )
        schemas.reject_untrusted_override(params)
        note, _ = schemas.bounded_text(params, "note", max_bytes=schemas.MAX_NOTE_BYTES)
        verdict = schemas.optional_string(params, "verdict") or "accepted"
        if verdict not in ("accepted", "rejected"):
            raise BoardError("INVALID_ARGUMENT", "verdict must be 'accepted' or 'rejected'")
        evidence = schemas.string_list(params, "evidence", limit=32)
        actor = schemas.optional_string(params, "acknowledgedBy") or "cli"
        request = {"selector": {k: params[k] for k in ("runId", "taskId", "requestId") if k in params}, "note": note, "verdict": verdict, "evidence": evidence}
        with self.db.write() as connection:
            if command_id:
                receipt = self._receipt(connection, command_id, "task.acknowledge", request)
                if receipt is not None:
                    return receipt
            task = self._task_row(connection, params)
            self.workflow.guard_task_control(connection, task, params, operation="task_acknowledge")
            if json.loads(task["spec_json"]).get("decision"):
                # A model recommendation is not business work and never becomes
                # acceptance or performance evidence.
                raise BoardError(
                    "UNSUPPORTED",
                    "A decision run is not acknowledged as business work; its recommendation is recorded separately "
                    "and never counts as task acceptance or model-performance evidence.",
                    taskId=task["task_id"],
                )
            attempt = self._selected_attempt(connection, task)
            if attempt is None or attempt["result_json"] is None:
                raise BoardError("NOT_READY", "Inspect a persisted result before acknowledging")
            if not attempt["shutdown_confirmed"]:
                raise BoardError(
                    "SHUTDOWN_UNCONFIRMED",
                    "Runner shutdown must be confirmed before acknowledgement; surviving processes are unknown",
                    attemptId=attempt["attempt_id"],
                )
            if task["accepted_at"]:
                if task["acceptance_note"] != note or task["acceptance_verdict"] != verdict:
                    raise BoardError(
                        "CONFLICT",
                        "This run already has a different recorded acceptance; a reviewed outcome is not relabelled",
                        acceptedAt=task["accepted_at"],
                        verdict=task["acceptance_verdict"],
                    )
                response = {"task": self._decorate(connection, task), "duplicate": True}
            else:
                now = self.now()
                connection.execute(
                    "UPDATE tasks SET accepted_at=?, acceptance_note=?, acceptance_verdict=?, updated_at=?, revision=revision+1"
                    " WHERE task_id = ?",
                    (now, note, verdict, now, task["task_id"]),
                )
                self._append_event(
                    connection,
                    "task.accepted" if verdict == "accepted" else "task.rejected",
                    task_id=task["task_id"],
                    attempt_id=attempt["attempt_id"],
                    revision=task["revision"] + 1,
                    payload={"verdict": verdict, "actor": actor, "evidence": evidence, "noteBytes": len(note.encode())},
                )
                task = connection.execute("SELECT * FROM tasks WHERE task_id = ?", (task["task_id"],)).fetchone()
                response = {"task": self._decorate(connection, task), "duplicate": False}
            # The verdict can arrive after reports about this attempt were recorded:
            # re-derive their verification now, so an accepted attempt counts as one
            # sample and an unreviewed one still does not.
            self.evaluation.refresh_task_evidence(connection, task, self.now())
            if command_id:
                self._store_receipt(
                    connection, command_id, "task.acknowledge", request, response, task_id=task["task_id"]
                )
            head = self._head_of(connection)
        self._notify(head)
        return response

    # -- transitions ---------------------------------------------------------
    def _transition_task(self, connection: sqlite3.Connection, task: sqlite3.Row, state: str) -> None:
        if state not in TASK_STATES:
            raise BoardError("INVALID_ARGUMENT", f"Unknown task state {state!r}")
        allowed = TASK_TRANSITIONS.get(task["state"], frozenset())
        if state not in allowed:
            raise BoardError(
                "ILLEGAL_TRANSITION",
                f"A task in state {task['state']} cannot become {state}",
                fromState=task["state"],
                toState=state,
            )
        now = self.now()
        connection.execute(
            "UPDATE tasks SET state=?, updated_at=?, revision=revision+1 WHERE task_id=? AND revision=?",
            (state, now, task["task_id"], task["revision"]),
        )
        row = connection.execute("SELECT revision FROM tasks WHERE task_id=?", (task["task_id"],)).fetchone()
        if row is None or int(row["revision"]) != int(task["revision"]) + 1:
            raise BoardError(
                "REVISION_CONFLICT",
                "The task changed concurrently; re-read it and retry",
                expectedRevision=task["revision"],
            )

    def _transition_attempt(self, connection: sqlite3.Connection, attempt: sqlite3.Row, state: str) -> None:
        if state not in ATTEMPT_STATES:
            raise BoardError("INVALID_ARGUMENT", f"Unknown attempt state {state!r}")
        if state not in ATTEMPT_TRANSITIONS.get(attempt["execution_state"], frozenset()):
            raise BoardError(
                "ILLEGAL_TRANSITION",
                f"An attempt in state {attempt['execution_state']} cannot become {state}",
                fromState=attempt["execution_state"],
                toState=state,
            )
        now = self.now()
        cursor = connection.execute(
            "UPDATE attempts SET execution_state=?, updated_at=?, revision=revision+1 WHERE attempt_id=? AND revision=?",
            (state, now, attempt["attempt_id"], attempt["revision"]),
        )
        if cursor.rowcount != 1:
            raise BoardError("REVISION_CONFLICT", "The attempt changed concurrently; re-read it and retry")

    # -- workers -------------------------------------------------------------
    def worker_register(self, params: dict, *, command_id: str | None = None) -> dict:
        schemas.reject_unknown(
            params,
            {"workerId", "identity", "adapter", "capabilities", "host", "pid", "state", "commandId"},
            "worker.register",
        )
        worker_id = schemas.required_string(params, "workerId", max_length=128, pattern=schemas.IDENTIFIER_PATTERN)
        identity = schemas.optional_string(params, "identity") or f"worker:{worker_id}"
        adapter = schemas.optional_string(params, "adapter") or "dsh"
        capabilities = schemas.string_list(params, "capabilities", limit=schemas.MAX_CAPABILITIES)
        host = schemas.optional_string(params, "host")
        pid = params.get("pid")
        if pid is not None and (isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0):
            raise BoardError("INVALID_ARGUMENT", "pid must be a positive integer when provided")
        command_id = command_id or schemas.optional_string(params, "commandId", max_length=128)
        request = {"workerId": worker_id, "identity": identity, "adapter": adapter, "capabilities": capabilities, "pid": pid}
        with self.db.write() as connection:
            if command_id:
                receipt = self._receipt(connection, command_id, "worker.register", request)
                if receipt is not None:
                    return receipt
            now = self.now()
            row = connection.execute("SELECT * FROM workers WHERE worker_id=?", (worker_id,)).fetchone()
            if row is None:
                connection.execute(
                    "INSERT INTO workers(worker_id, identity, adapter, capabilities, host, pid, state, registered_at,"
                    " last_seen_at, revision) VALUES(?,?,?,?,?,?,?,?,?,1)",
                    (worker_id, identity, adapter, canonical_json(capabilities), host, pid, "idle", now, now),
                )
                event = "worker.registered"
            else:
                connection.execute(
                    "UPDATE workers SET identity=?, adapter=?, capabilities=?, host=?, pid=?, last_seen_at=?,"
                    " revision=revision+1 WHERE worker_id=?",
                    (identity, adapter, canonical_json(capabilities), host, pid, now, worker_id),
                )
                event = "worker.registered"
            self._append_event(connection, event, payload={"workerId": worker_id, "adapter": adapter, "capabilities": capabilities})
            row = connection.execute("SELECT * FROM workers WHERE worker_id=?", (worker_id,)).fetchone()
            response = {"worker": self._worker_view(row), "duplicate": False, "leaseSeconds": self.lease_seconds}
            if command_id:
                self._store_receipt(connection, command_id, "worker.register", request, response)
            head = self._head_of(connection)
        self._notify(head)
        return response

    def worker_claim(self, params: dict) -> dict:
        """Atomically admit one queued task and authorize exactly one attempt.

        The worker persists ``claimRequestId`` and its ``nonce`` before calling
        this, so a committed claim whose reply is lost is recoverable: the stored
        command receipt replays the identical attempt, generation and capability
        instead of minting a second generation. A claim that admits no work is a
        transient observation, not an allocation receipt: nothing is written to
        commands, so the same ``claimRequestId`` can return and observe work
        queued later, while a committed claim still rejects a different worker,
        nonce or payload under its id.
        """
        schemas.reject_unknown(
            params,
            {
                "workerId",
                "claimRequestId",
                "nonce",
                "taskId",
                "runId",
                "pid",
                "identity",
                "capabilities",
                "adapter",
                "workerInstance",
            },
            "worker.claim",
        )
        worker_id = schemas.required_string(params, "workerId", max_length=128, pattern=schemas.IDENTIFIER_PATTERN)
        claim_request_id = schemas.required_string(
            params, "claimRequestId", max_length=128, pattern=schemas.IDENTIFIER_PATTERN
        )
        nonce = schemas.required_string(params, "nonce", max_length=256)
        if len(nonce) < 16:
            raise BoardError("INVALID_ARGUMENT", "nonce must contain at least 16 characters")
        explicit_task = params.get("taskId") or params.get("runId")
        if explicit_task is not None and not isinstance(explicit_task, str):
            raise BoardError("INVALID_ARGUMENT", "taskId must be a string")
        request = {"workerId": worker_id, "claimRequestId": claim_request_id, "taskId": explicit_task}
        self.workflow.prepare_dispatch(params)
        with self.db.write() as connection:
            receipt = self._receipt(
                connection, claim_request_id, "worker.claim", request, self._subject(worker_id, nonce, explicit_task)
            )
            if receipt is not None:
                # The capability is derived again for the authenticated caller rather
                # than read back out of stored response JSON, so the durable receipt
                # stays verifier-only at rest.
                stored_claim = receipt.get("claim")
                if stored_claim is not None:
                    stored_claim = {
                        **stored_claim,
                        "capability": self.db.capability(
                            stored_claim["attempt"]["attemptId"],
                            stored_claim["attempt"]["generation"],
                            nonce,
                        ),
                    }
                    stored_claim = self._attach_agent_credential(connection, stored_claim)
                return {**receipt, "claim": stored_claim, "replayed": True}
            worker = connection.execute("SELECT * FROM workers WHERE worker_id=?", (worker_id,)).fetchone()
            if worker is None:
                raise BoardError("NOT_REGISTERED", "Register this worker before claiming work", workerId=worker_id)
            if worker["state"] == "stopping":
                raise BoardError("WORKER_STOPPING", "This worker is stopping and cannot claim new work")
            markers = ",".join("?" for _ in ACTIVE_ATTEMPT_STATES_SQL)
            held = connection.execute(
                f"SELECT attempt_id, task_id FROM attempts WHERE worker_id=? AND execution_state IN ({markers}) LIMIT 1",
                (worker_id, *ACTIVE_ATTEMPT_STATES_SQL),
            ).fetchone()
            if held is not None:
                raise BoardError(
                    "WORKER_BUSY",
                    "This worker already owns an active attempt; one worker runs one attempt at a time",
                    workerId=worker_id,
                    attemptId=held["attempt_id"],
                    taskId=held["task_id"],
                )
            capabilities = set(json.loads(worker["capabilities"]))
            adapter = worker["adapter"]
            full_families: set[tuple[str, str, str]] = set()
            if explicit_task:
                candidates = [
                    connection.execute("SELECT * FROM tasks WHERE task_id=?", (explicit_task,)).fetchone()
                ]
                # An explicit target is examined by ``_admission_blocker`` and the
                # decision family check below; no candidate pre-filter applies to it.
            else:
                candidates, full_families = self._claim_candidates(connection)
            chosen: sqlite3.Row | None = None
            chosen_generation = 0
            chosen_attempt_id: str | None = None
            chosen_input: dict | None = None
            chosen_family: tuple[str, str, str] | None = None
            blocker = "no-queued-work"
            for task in candidates:
                if task is None:
                    raise BoardError("NOT_FOUND", "Unknown taskId")
                spec = self.workflow.effective_spec(connection, task)
                if task["state"] != "queued":
                    blocker = "not-queued" if explicit_task else blocker
                    continue
                workflow_reason = self.workflow.claim_blocker(connection, task)
                if workflow_reason is not None:
                    blocker = workflow_reason
                    connection.execute("UPDATE tasks SET queue_reason=?,updated_at=? WHERE task_id=?",
                                       (workflow_reason, self.now(), task["task_id"]))
                    continue
                # A worker may run a task when it is the worker's primary adapter or
                # when it advertises that adapter as one of its capabilities.
                if (
                    task["adapter"] != adapter
                    and task["adapter"] not in capabilities
                    and f"adapter:{task['adapter']}" not in capabilities
                ):
                    blocker = "adapter-mismatch"
                    continue
                if not set(json.loads(task["required_capabilities"])).issubset(capabilities):
                    blocker = "capability-mismatch"
                    continue
                # A governed worktree runs in its allocated checkout, so its admission
                # and claims use tasks.cwd (the effective path), never the source cwd
                # recorded in the immutable spec_json.
                effective_spec = {**spec, "cwd": task["cwd"]} if task["cwd"] != spec["cwd"] else spec
                reason = self._admission_blocker(connection, effective_spec, exclude_task=task["task_id"])
                if reason is not None:
                    blocker = reason
                    connection.execute(
                        "UPDATE tasks SET queue_reason=?, updated_at=? WHERE task_id=?",
                        (reason, self.now(), task["task_id"]),
                    )
                    continue
                generation_row = connection.execute(
                    "SELECT COALESCE(MAX(generation), 0) AS generation FROM attempts WHERE task_id=?",
                    (task["task_id"],),
                ).fetchone()
                attempt_id = str(uuid.uuid4())
                generation = int(generation_row["generation"]) + 1
                # The model family this attempt is frozen to. A routing task has no
                # family of its own in its specification: the fixed decision profile
                # resolves one here, its quota is checked before the claim commits,
                # and the same tuple is frozen onto the attempt. Business work uses
                # the effective specification's family, which a governed run has
                # already resolved and validated.
                family = scheduling.model_family(effective_spec)
                if task["adapter"] == scheduling.DECISION_ADAPTER:
                    family = self.decisions.selector_family(connection, spec)
                    if family is not None:
                        family_reason = self._family_admission_blocker(connection, family)
                        if family_reason is not None:
                            blocker = family_reason
                            connection.execute(
                                "UPDATE tasks SET queue_reason=?, updated_at=? WHERE task_id=?",
                                (family_reason, self.now(), task["task_id"]),
                            )
                            continue
                # A decision task is admitted *here*, inside the same transaction that
                # claims it: the selection reader or the maintenance writer grant is
                # bound to this attempt and the exact bounded model input is persisted
                # before any process exists. A blocked decision stays queued, holds no
                # execution slot and is skipped for the next candidate.
                claim_input: dict | None = None
                if task["adapter"] == scheduling.DECISION_ADAPTER:
                    blocked, claim_input = self.decisions.claim(
                        connection,
                        task=task,
                        spec=spec,
                        attempt_id=attempt_id,
                        generation=generation,
                        now=self.now(),
                    )
                    if blocked:
                        blocker = blocked
                        if blocked != "decision-closed":
                            connection.execute(
                                "UPDATE tasks SET queue_reason=?, updated_at=? WHERE task_id=?",
                                (blocked, self.now(), task["task_id"]),
                            )
                        continue
                chosen = task
                chosen_generation = generation
                chosen_attempt_id = attempt_id
                chosen_input = claim_input
                chosen_family = family
                break
            if chosen is None:
                if not candidates:
                    # Nothing was even scannable. Name the limit that is holding work
                    # instead of reporting that the board is idle.
                    if self._total_active(connection) >= self.max_concurrent:
                        blocker = scheduling.REASON_TOTAL_CAPACITY
                    elif full_families:
                        blocker = scheduling.REASON_MODEL_CAPACITY
                    else:
                        blocker = "no-queued-work"
                response = {"claim": None, "reason": blocker, "retryAfterMs": 1000 if blocker != "no-queued-work" else 2000}
                head = self._head_of(connection)
                # An empty claim is a transient observation, never an allocation
                # receipt: no commands row exists to replay, so a retried
                # claimRequestId scans the board again and can observe newly
                # queued work. Only a committed (nonempty) claim stores one.
                self._notify(head)
                return response
            spec = self.workflow.effective_spec(connection, chosen)
            generation = chosen_generation
            attempt_id = chosen_attempt_id or str(uuid.uuid4())
            now = self.now()
            capability = self.db.capability(attempt_id, generation, nonce)
            lease_expires_at = self._lease_deadline()
            connection.execute(
                "INSERT INTO attempts(attempt_id, task_id, generation, worker_id, worker_identity, worker_instance,"
                " capability_version, nonce_verifier, claim_request_id, lease_expires_at, lease_seconds,"
                " execution_state, ownership, adapter, model_adapter, model_provider, model_model,"
                " started_at, created_at, updated_at, revision)"
                " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1)",
                (
                    attempt_id,
                    chosen["task_id"],
                    generation,
                    worker_id,
                    worker["identity"],
                    schemas.optional_string(params, "workerInstance", max_length=128),
                    1,
                    self.db.nonce_verifier(nonce),
                    claim_request_id,
                    lease_expires_at,
                    self.lease_seconds,
                    "starting",
                    "owned",
                    chosen["adapter"],
                    # Frozen family identity: written by this claim transaction and
                    # never updated afterwards, so historical and shutdown-uncertain
                    # attempts cannot change which family slot they occupy.
                    *(chosen_family if chosen_family is not None else (None, None, None)),
                    now,
                    now,
                    now,
                ),
            )
            effective_cwd = chosen["cwd"] or spec["cwd"]
            from .accounts import freeze
            account_adapter = chosen_family[0] if chosen_family else chosen['adapter']
            frozen_account = freeze(connection, attempt_id, account_adapter)
            if frozen_account is not None:
                from .harness_health import read_health
                connection.execute('INSERT INTO meta(key,value) VALUES(?,?)',
                                   ('attempt-harness:' + attempt_id, canonical_json(read_health(connection, account_adapter))))
            if chosen_input is not None:
                from .accounts import identity, selection
                names = {profile['adapter'] for profile in chosen_input.get('profiles', [])}
                if frozen_account is not None:
                    names.add(frozen_account['adapter'])
                connection.execute('INSERT INTO meta(key,value) VALUES(?,?)',
                                   ('attempt-routing-accounts:' + attempt_id,
                                    canonical_json(chosen_input.get('accounts') or {name: identity(selection(connection, name)) for name in names})))
            for kind, values in (("cwd", [effective_cwd]), ("exclusive", spec.get("exclusiveResources", []))):
                for resource in values:
                    # (task, resource) is unique, so a retried task re-holds its own
                    # previously released claim instead of leaving it released.
                    connection.execute(
                        "INSERT INTO resource_claims(claim_id, task_id, attempt_id, resource, kind, state, created_at,"
                        " released_at) VALUES(?,?,?,?,?,?,?,NULL)"
                        " ON CONFLICT(task_id, resource) DO UPDATE SET attempt_id=excluded.attempt_id,"
                        " kind=excluded.kind, state='held', created_at=excluded.created_at, released_at=NULL",
                        (str(uuid.uuid4()), chosen["task_id"], attempt_id, resource, kind, "held", now),
                    )
            self._transition_task(connection, chosen, "running")
            connection.execute(
                "UPDATE tasks SET active_attempt_id=?, selected_attempt_id=?, queue_reason=NULL WHERE task_id=?",
                (attempt_id, attempt_id, chosen["task_id"]),
            )
            connection.execute(
                "UPDATE workers SET state='busy', current_attempt_id=?, last_seen_at=?, revision=revision+1 WHERE worker_id=?",
                (attempt_id, now, worker_id),
            )
            self._append_event(
                connection,
                "attempt.claimed",
                task_id=chosen["task_id"],
                attempt_id=attempt_id,
                revision=chosen["revision"] + 1,
                payload={"workerId": worker_id, "generation": generation, "adapter": chosen["adapter"]},
            )
            task_row = connection.execute("SELECT * FROM tasks WHERE task_id=?", (chosen["task_id"],)).fetchone()
            attempt_row = connection.execute("SELECT * FROM attempts WHERE attempt_id=?", (attempt_id,)).fetchone()
            response = {
                "claim": {
                    "attempt": self._attempt_view(attempt_row, include_result=False),
                    "task": self._decorate(connection, task_row),
                    "capability": capability,
                    "claimRequestId": claim_request_id,
                    "leaseSeconds": self.lease_seconds,
                    "leaseExpiresAt": lease_expires_at,
                },
                "reason": None,
            }
            if frozen_account is not None:
                response['claim']['account'] = frozen_account
            if chosen_input is not None:
                # The bounded decision payload the service persisted for this attempt.
                # It travels only to the claiming worker and is written to the private
                # attempt directory by the decision adapter; adapters never see the
                # database, and no credential is part of this document.
                response["claim"]["decisionInput"] = chosen_input
            # Only the execution projection travels to a Worker. The original
            # partial request remains immutable in tasks/spec and workflow audit.
            response["claim"]["task"]["spec"] = spec
            # Display grouping does not travel to a Worker or its native context.
            for key in ("title", "objectiveId"):
                response["claim"]["task"].get("workflow", {}).pop(key, None)
            turn_claim = self.workflow.begin_turn(
                connection, task=chosen, attempt_id=attempt_id, generation=generation, spec=spec, now=now
            )
            if turn_claim is not None:
                # The service-owned turn identity and bounded context travel to the
                # worker, which writes them into the private attempt directory. The
                # attempt-scoped credential is handed over next to it and is stored
                # only as a verifier.
                response["claim"]["turn"] = {
                    "turnId": turn_claim["turnId"],
                    "turnIndex": turn_claim["turnIndex"],
                    "resumeMode": turn_claim["resumeMode"],
                    "inputSha256": turn_claim["inputSha256"],
                    "input": turn_claim["input"],
                }
                response["claim"]["agentCredential"] = turn_claim["credential"]
            # ``capability`` and ``agentCredential`` are deliberately absent from the
            # stored receipt: they are derived again for the authenticated caller on
            # replay, so the durable record stays verifier-only at rest.
            self._store_receipt(
                connection,
                claim_request_id,
                "worker.claim",
                request,
                {
                    **response,
                    "claim": {
                        key: value
                        for key, value in response["claim"].items()
                        if key not in ("capability", "agentCredential")
                    },
                },
                task_id=chosen["task_id"],
                attempt_id=attempt_id,
                subject=self._subject(worker_id, nonce, explicit_task),
            )
            head = self._head_of(connection)
        self._notify(head)
        return {**response, "replayed": False}

    def _lease_deadline(self) -> str:
        from datetime import datetime, timedelta, timezone

        moment = datetime.now(timezone.utc) + timedelta(seconds=self.lease_seconds)
        return moment.isoformat(timespec="milliseconds").replace("+00:00", "Z")

    def _verify_artifacts(self, artifacts: list) -> list[dict]:
        """Validate and hash claimed artifacts outside any database transaction."""
        verified: list[dict] = []
        for index, entry in enumerate(artifacts):
            if not isinstance(entry, dict):
                raise BoardError("INVALID_ARGUMENT", f"artifacts[{index}] must be an object")
            schemas.reject_unknown(
                entry, {"kind", "location", "contentHash", "sizeBytes", "role"}, f"artifacts[{index}]"
            )
            location = schemas.required_string(entry, "location", max_length=4096)
            content_hash = schemas.optional_sha256(entry, "contentHash")
            size_bytes = entry.get("sizeBytes")
            if size_bytes is not None and (
                isinstance(size_bytes, bool) or not isinstance(size_bytes, int) or size_bytes < 0
            ):
                raise BoardError("INVALID_ARGUMENT", f"artifacts[{index}].sizeBytes must be a nonnegative integer")
            kind = schemas.optional_string(entry, "kind") or "file"
            path = Path(location)
            if not path.is_absolute():
                raise BoardError(
                    "INVALID_ARGUMENT", f"artifacts[{index}].location must be an absolute path", location=location
                )
            try:
                resolved = Path(os.path.realpath(location))
                stat = resolved.stat()
            except OSError as exc:
                raise BoardError(
                    "ARTIFACT_MISSING",
                    "An artifact was claimed but does not exist; no completed result was published",
                    location=location,
                ) from exc
            if not resolved.is_file():
                raise BoardError(
                    "ARTIFACT_MISSING",
                    "An artifact location is not a regular file; no completed result was published",
                    location=location,
                )
            if size_bytes is not None and stat.st_size != size_bytes:
                raise BoardError(
                    "ARTIFACT_MISMATCH",
                    "Artifact size does not match the claimed size; no completed result was published",
                    location=location,
                    actualSize=stat.st_size,
                    claimedSize=size_bytes,
                )
            actual = _file_sha256(resolved)
            if content_hash is not None and actual != content_hash:
                raise BoardError(
                    "ARTIFACT_MISMATCH",
                    "Artifact content hash does not match; no completed result was published",
                    location=location,
                    actualHash=actual,
                    claimedHash=content_hash,
                )
            verified.append(
                {
                    "kind": kind,
                    "location": str(resolved),
                    "contentHash": actual,
                    "sizeBytes": stat.st_size,
                }
            )
        return verified

    @staticmethod
    def _same_result(previous: dict, request: dict) -> bool:
        """Complete semantic comparison: identical replays only."""
        return (
            previous.get("status") == request["status"]
            and previous.get("result") == request["result"]
            and previous.get("exitCode") == request["exitCode"]
            and previous.get("signal") == request["signal"]
            and previous.get("error") == request["error"]
            and bool(previous.get("shutdownConfirmed")) == bool(request["shutdownConfirmed"])
            and previous.get("artifacts", []) == request["artifacts"]
            and previous.get("logPaths") == request["logPaths"]
            and previous.get("runtimeIdentity") == request["runtimeIdentity"]
            and previous.get("terminationReason") == request.get("terminationReason")
        )

    def _verify_attempt_actor(self, connection: sqlite3.Connection, params: dict) -> tuple[sqlite3.Row, sqlite3.Row]:
        """Authenticate one worker against one attempt by identity, generation and nonce."""
        worker_id = schemas.required_string(params, "workerId", max_length=128, pattern=schemas.IDENTIFIER_PATTERN)
        attempt_id = schemas.required_string(params, "attemptId", max_length=128)
        attempt = self._attempt_row(connection, attempt_id)
        generation = params.get("generation")
        if generation is not None and generation != attempt["generation"]:
            raise BoardError(
                "STALE_GENERATION",
                "This attempt was replaced by a newer generation; the older worker may not mutate state",
                attemptId=attempt_id,
                currentGeneration=attempt["generation"],
                providedGeneration=generation,
            )
        if attempt["worker_id"] != worker_id:
            raise BoardError("UNAUTHORIZED", "This attempt belongs to a different worker", attemptId=attempt_id)
        nonce = schemas.required_string(params, "nonce", max_length=256)
        if not _constant_time_equal(self.db.nonce_verifier(nonce), attempt["nonce_verifier"]):
            raise BoardError("UNAUTHORIZED", "Invalid attempt capability", attemptId=attempt_id)
        # Identity and generation are not enough: a released-then-retried task has a
        # newer attempt, and the superseded worker must not mutate the task even
        # though its nonce still verifies against its own immutable attempt row.
        self._require_effective_attempt(connection, attempt)
        return attempt, connection.execute("SELECT * FROM workers WHERE worker_id=?", (worker_id,)).fetchone()

    def _require_effective_attempt(self, connection: sqlite3.Connection, attempt: sqlite3.Row) -> None:
        task = connection.execute("SELECT * FROM tasks WHERE task_id=?", (attempt["task_id"],)).fetchone()
        if task is None:
            raise BoardError("NOT_FOUND", "Unknown task for this attempt")
        current = task["active_attempt_id"]
        if current is not None:
            if current == attempt["attempt_id"]:
                return
        else:
            latest = connection.execute(
                "SELECT attempt_id FROM attempts WHERE task_id=? ORDER BY generation DESC LIMIT 1",
                (attempt["task_id"],),
            ).fetchone()
            if latest is not None and latest["attempt_id"] == attempt["attempt_id"]:
                return
        raise BoardError(
            "STALE_GENERATION",
            "This attempt was superseded by a newer generation; the older worker may not mutate task state",
            attemptId=attempt["attempt_id"],
            taskId=attempt["task_id"],
            effectiveAttemptId=current,
        )

    def worker_reconcile(self, params: dict) -> dict:
        """Reattach one legitimate worker after daemon downtime.

        Reattachment is by attempt identity plus the nonce the worker fsynced
        before claiming — never by PID matching. A replaced generation is rejected
        so it cannot mutate state.
        """
        schemas.reject_unknown(
            params,
            {
                "workerId",
                "attemptId",
                "generation",
                "nonce",
                "claimRequestId",
                "pid",
                "runtimeIdentity",
                "workerInstance",
            },
            "worker.reconcile",
        )
        with self.db.write() as connection:
            attempt, worker = self._verify_attempt_actor(connection, params)
            # Only the worker *process* that claimed the attempt can prove it still
            # holds the child handle. A fresh process reusing the same worker id has
            # no handle, so it may not resume or release anything.
            instance = schemas.optional_string(params, "workerInstance", max_length=128)
            if attempt["worker_instance"] and instance != attempt["worker_instance"]:
                raise BoardError(
                    "UNAUTHORIZED",
                    "This attempt was claimed by a different worker process instance, which is the only holder of "
                    "the child handle; this process has no evidence about the running work",
                    attemptId=attempt["attempt_id"],
                )
            capability = self.db.capability(attempt["attempt_id"], attempt["generation"], params["nonce"])
            task = connection.execute("SELECT * FROM tasks WHERE task_id=?", (attempt["task_id"],)).fetchone()
            if attempt["result_json"] is not None or attempt["execution_state"] == "finished":
                # A terminal attempt is immutable: reconcile reports it and changes
                # nothing - not the lease, not the worker's current attempt. A
                # released attempt (finished, no result) is terminal too.
                return {
                    "attempt": self._attempt_view(attempt),
                    "task": self._decorate(connection, task),
                    "capability": capability,
                    "leaseSeconds": attempt["lease_seconds"],
                    "leaseExpiresAt": attempt["lease_expires_at"],
                    "cancelRequested": attempt["cancel_requested_at"] is not None,
                    "finished": True,
                    "reattached": True,
                    "immutable": True,
                    "restored": False,
                }
            now = self.now()
            lease_expires_at = self._lease_deadline()
            previous_state = attempt["execution_state"]
            restored = previous_state == "uncertain"
            if restored:
                # The same worker *process* is alive again and still holds the child
                # handle. Its ownership was never reassigned, so the attempt returns
                # to executing, its retained resources return to held and the stale
                # restart waiting reason is cleared — all in this one transaction.
                self._transition_attempt(connection, attempt, "executing")
                self._restore_claims(connection, attempt["attempt_id"])
                if task["state"] in ("running", "cancelling") and task["queue_reason"] == UNCERTAIN_QUEUE_REASON:
                    connection.execute(
                        "UPDATE tasks SET queue_reason=NULL, updated_at=? WHERE task_id=?",
                        (now, attempt["task_id"]),
                    )
            connection.execute(
                "UPDATE attempts SET lease_expires_at=?, updated_at=?, ownership='owned',"
                " runtime_identity=COALESCE(?, runtime_identity), revision=revision+1 WHERE attempt_id=?",
                (lease_expires_at, now, schemas.optional_string(params, "runtimeIdentity"), attempt["attempt_id"]),
            )
            if worker is not None:
                pid = params.get("pid")
                connection.execute(
                    "UPDATE workers SET state='busy', current_attempt_id=?, last_seen_at=?, pid=COALESCE(?, pid),"
                    " revision=revision+1 WHERE worker_id=?",
                    (attempt["attempt_id"], now, pid if isinstance(pid, int) and not isinstance(pid, bool) else None, attempt["worker_id"]),
                )
            self._append_event(
                connection,
                "attempt.reconciled",
                task_id=attempt["task_id"],
                attempt_id=attempt["attempt_id"],
                revision=task["revision"],
                payload={
                    "workerId": attempt["worker_id"],
                    "generation": attempt["generation"],
                    "previousState": previous_state,
                    "restored": restored,
                },
            )
            attempt_row = connection.execute("SELECT * FROM attempts WHERE attempt_id=?", (attempt["attempt_id"],)).fetchone()
            task_row = connection.execute("SELECT * FROM tasks WHERE task_id=?", (attempt["task_id"],)).fetchone()
            response = {
                "attempt": self._attempt_view(attempt_row),
                "task": self._decorate(connection, task_row),
                "capability": capability,
                "leaseSeconds": self.lease_seconds,
                "leaseExpiresAt": lease_expires_at,
                "cancelRequested": attempt_row["cancel_requested_at"] is not None,
                "finished": attempt_row["execution_state"] == "finished",
                "reattached": True,
                "restored": restored,
                "queueReason": task_row["queue_reason"],
                "activity": self._activity_latest(connection, attempt["attempt_id"]),
            }
            head = self._head_of(connection)
        self._notify(head)
        return response

    def worker_renew(self, params: dict) -> dict:
        schemas.reject_unknown(
            params, {"workerId", "attemptId", "generation", "nonce", "phase", "pid"}, "worker.renew"
        )
        with self.db.write() as connection:
            attempt, worker = self._verify_attempt_actor(connection, params)
            if attempt["execution_state"] == "finished":
                task = connection.execute("SELECT * FROM tasks WHERE task_id=?", (attempt["task_id"],)).fetchone()
                return {
                    "attempt": self._attempt_view(attempt),
                    "task": self._decorate(connection, task),
                    "cancelRequested": attempt["cancel_requested_at"] is not None,
                    "finished": True,
                    "leaseExpiresAt": attempt["lease_expires_at"],
                }
            phase = schemas.optional_string(params, "phase")
            now = self.now()
            lease_expires_at = self._lease_deadline()
            # Renewal proves the operation succeeded, never that the attempt resumed
            # and never that the model advanced. After a service restart the attempt
            # is authoritative-uncertain; a renewal may not clear that. Only the
            # explicit, worker-instance-checked ``worker_reconcile`` may.
            uncertain = attempt["execution_state"] == "uncertain"
            if phase in ("executing", "finalizing") and not uncertain:
                self._transition_attempt(connection, attempt, phase)
            connection.execute(
                "UPDATE attempts SET lease_expires_at=?, updated_at=? WHERE attempt_id=?",
                (lease_expires_at, now, attempt["attempt_id"]),
            )
            if worker is not None:
                connection.execute(
                    "UPDATE workers SET last_seen_at=?, state='busy', current_attempt_id=?, revision=revision+1"
                    " WHERE worker_id=?",
                    (now, attempt["attempt_id"], attempt["worker_id"]),
                )
            # Keep this decision's fenced reader/writer lease alive for the length of
            # one bounded model call; renewal never resurrects a terminal decision.
            # An uncertain attempt was already fenced by the restart, so it waits for
            # its own reconciliation before it may hold the table again.
            renew_task = connection.execute(
                "SELECT * FROM tasks WHERE task_id=?", (attempt["task_id"],)
            ).fetchone()
            if not uncertain:
                self.decisions.renew(connection, task=renew_task, now=now)
            task = connection.execute("SELECT * FROM tasks WHERE task_id=?", (attempt["task_id"],)).fetchone()
            attempt_row = connection.execute("SELECT * FROM attempts WHERE attempt_id=?", (attempt["attempt_id"],)).fetchone()
            response = {
                "attempt": self._attempt_view(attempt_row, include_result=False),
                "task": self._decorate(connection, task),
                "cancelRequested": attempt_row["cancel_requested_at"] is not None,
                "finished": False,
                "leaseExpiresAt": lease_expires_at,
                "uncertain": uncertain,
                "reconciliationRequired": uncertain,
            }
            head = self._head_of(connection)
        self._notify(head)
        return response

    def worker_progress(self, params: dict) -> dict:
        schemas.reject_unknown(
            params, {"workerId", "attemptId", "generation", "nonce", "message", "phase", "data"}, "worker.progress"
        )
        message = schemas.optional_string(params, "message")
        phase = schemas.optional_string(params, "phase")
        # ``data`` carries the bounded native-activity projection, and activity is
        # its only accepted field: a typo can therefore never smuggle prose, tool
        # arguments or credentials into the projection. Activity is stored only when
        # a caller actually publishes one - a routine renewal or heartbeat is never
        # turned into fake native progress.
        activity = None
        data = params.get("data")
        if data is not None:
            if not isinstance(data, dict):
                raise BoardError("INVALID_ARGUMENT", "data must be an object")
            schemas.reject_unknown(data, {"activity"}, "worker.progress.data")
            if data.get("activity") is not None:
                activity = activity_module.normalize_activity(data["activity"])
        with self.db.write() as connection:
            attempt, _worker = self._verify_attempt_actor(connection, params)
            if attempt["execution_state"] == "finished":
                raise BoardError("ATTEMPT_FINISHED", "This attempt is already finished; progress is not accepted")
            if phase in ("starting", "executing", "finalizing") and attempt["execution_state"] != "uncertain":
                self._transition_attempt(connection, attempt, phase)
            now = self.now()
            connection.execute(
                "UPDATE attempts SET updated_at=?, lease_expires_at=?, revision=revision+1 WHERE attempt_id=?",
                (now, self._lease_deadline(), attempt["attempt_id"]),
            )
            activity_state = None
            if activity is not None:
                # Monotone and idempotent: an identical or older receipt changes
                # nothing and appends no event.
                activity_state = "recorded" if self._store_activity(connection, attempt, activity) else "unchanged"
            if activity_state == "recorded":
                self._append_event(
                    connection,
                    "attempt.activity",
                    task_id=attempt["task_id"],
                    attempt_id=attempt["attempt_id"],
                    payload={"activity": activity},
                )
            if message is not None or phase is not None:
                self._append_event(
                    connection,
                    "attempt.progress",
                    task_id=attempt["task_id"],
                    attempt_id=attempt["attempt_id"],
                    payload={"message": (message or "")[:2000], "phase": phase},
                )
            head = self._head_of(connection)
        self._notify(head)
        return {"recorded": True, "head": head, "activity": activity_state}

    def worker_result(self, params: dict) -> dict:
        """Commit result, artifacts, task/attempt state and the completion event together.

        Artifact registration validates the claimed file before any completed result
        is published. A failed commit publishes no success event.
        """
        schemas.reject_unknown(
            params,
            {
                "workerId",
                "attemptId",
                "generation",
                "nonce",
                "commandId",
                "status",
                "result",
                "error",
                "exitCode",
                "signal",
                "shutdownConfirmed",
                "artifacts",
                "runtimeIdentity",
                "elapsedSeconds",
                "logPaths",
                "terminationReason",
            },
            "worker.result",
        )
        status = schemas.required_string(params, "status", max_length=32)
        if status not in ("ok", "failed", "cancelled"):
            raise BoardError("INVALID_ARGUMENT", "status must be 'ok', 'failed' or 'cancelled'")
        result = params.get("result")
        if result is not None and not isinstance(result, dict):
            raise BoardError("INVALID_ARGUMENT", "result must be an object when provided")
        artifacts = params.get("artifacts", [])
        if not isinstance(artifacts, list) or len(artifacts) > 100:
            raise BoardError("INVALID_ARGUMENT", "artifacts must be a list of at most 100 entries")
        shutdown_confirmed = schemas.optional_bool(params, "shutdownConfirmed", False)
        exit_code = params.get("exitCode")
        if exit_code is not None and (isinstance(exit_code, bool) or not isinstance(exit_code, int)):
            raise BoardError("INVALID_ARGUMENT", "exitCode must be an integer or null")
        signal_name = schemas.optional_string(params, "signal", max_length=32)
        # The real reason this attempt stopped. It is optional so a caller-owned
        # external agent may report no classification, but a supplied value must be
        # one of the documented reasons: a completion that beat a cancellation is
        # recorded as completed rather than re-labelled by the losing race.
        termination_reason = schemas.optional_string(params, "terminationReason", max_length=32)
        if termination_reason is not None and termination_reason not in TERMINATION_REASONS:
            raise BoardError(
                "INVALID_ARGUMENT",
                f"terminationReason must be one of {', '.join(TERMINATION_REASONS)}",
                field="terminationReason",
            )
        error_text = schemas.optional_string(params, "error", max_length=4000)
        command_id = schemas.optional_string(params, "commandId", max_length=128) or f"result:{params.get('attemptId')}"
        attempt_id_param = schemas.required_string(params, "attemptId", max_length=128)
        log_paths = params.get("logPaths")
        if log_paths is not None and not isinstance(log_paths, dict):
            raise BoardError("INVALID_ARGUMENT", "logPaths must be an object when provided")
        elapsed = params.get("elapsedSeconds")
        if elapsed is not None and (isinstance(elapsed, bool) or not isinstance(elapsed, (int, float))):
            raise BoardError("INVALID_ARGUMENT", "elapsedSeconds must be a number when provided")
        # Hashing a large artifact inside BEGIN IMMEDIATE would block renew, cancel
        # and other result writers for its whole duration, so the files are
        # validated and hashed first, outside any transaction.
        verified_artifacts = self._verify_artifacts(artifacts)
        # A governed turn's sealed output manifest is verified outside the transaction
        # too; a manifest that cannot be verified fails the attempt honestly instead
        # of publishing an unverified success.
        try:
            self.workflow.precheck_result(params)
        except BoardError as error:
            status = "failed"
            error_text = f"the governed result could not be imported: {error.message}"
            result = {
                **(result or {}),
                "governedError": {"code": error.code, "message": error.message},
            }
        request = {
            "attemptId": attempt_id_param,
            "status": status,
            "result": result,
            "artifacts": verified_artifacts,
            "shutdownConfirmed": shutdown_confirmed,
            "exitCode": exit_code,
            "signal": signal_name,
            "error": error_text,
            "logPaths": log_paths,
            "runtimeIdentity": params.get("runtimeIdentity"),
            "terminationReason": termination_reason,
        }
        with self.db.write() as connection:
            attempt, _worker = self._verify_attempt_actor(connection, params)
            if attempt["attempt_id"] != attempt_id_param:
                raise BoardError("INVALID_ARGUMENT", "attemptId does not match the request")
            subject = self._subject(params["workerId"], params["nonce"], attempt["attempt_id"])
            if attempt["result_json"] is not None:
                previous = json.loads(attempt["result_json"])
                if self._same_result(previous, request):
                    task = connection.execute("SELECT * FROM tasks WHERE task_id=?", (attempt["task_id"],)).fetchone()
                    return {
                        "attempt": self._attempt_view(attempt),
                        "task": self._decorate(connection, task),
                        "duplicate": True,
                        "committed": True,
                    }
                raise BoardError(
                    "CONFLICT",
                    "This attempt already committed a different result; a terminal attempt is immutable. A replay "
                    "must repeat the identical status, result, artifacts, shutdown evidence, exit code and error.",
                    attemptId=attempt["attempt_id"],
                )
            receipt = self._receipt(connection, command_id, "worker.result", request, subject)
            if receipt is not None:
                # The capability is derived again for the authenticated caller rather
                # than read back out of stored response JSON, so the durable receipt
                # stays verifier-only at rest.
                stored_claim = receipt.get("claim")
                if stored_claim is not None:
                    stored_claim = {
                        **stored_claim,
                        "capability": self.db.capability(
                            stored_claim["attempt"]["attemptId"],
                            stored_claim["attempt"]["generation"],
                            nonce,
                        ),
                    }
                    stored_claim = self._attach_agent_credential(connection, stored_claim)
                return {**receipt, "claim": stored_claim, "replayed": True}
            now = self.now()
            for artifact in verified_artifacts:
                connection.execute(
                    "INSERT OR IGNORE INTO artifacts(artifact_id, task_id, attempt_id, kind, location, content_hash,"
                    " size_bytes, verified, created_at) VALUES(?,?,?,?,?,?,?,1,?)",
                    (
                        str(uuid.uuid4()),
                        attempt["task_id"],
                        attempt["attempt_id"],
                        artifact["kind"],
                        artifact["location"],
                        artifact["contentHash"],
                        artifact["sizeBytes"],
                        now,
                    ),
                )
            payload = {
                "status": status,
                "result": result,
                "error": error_text,
                "exitCode": exit_code,
                "signal": signal_name,
                "shutdownConfirmed": shutdown_confirmed,
                "logPaths": log_paths,
                "runtimeIdentity": params.get("runtimeIdentity"),
                "terminationReason": termination_reason,
                "artifacts": verified_artifacts,
                "completedAt": now,
            }
            if attempt["execution_state"] in ("starting", "executing", "uncertain"):
                self._transition_attempt(connection, attempt, "finalizing")
                attempt = connection.execute(
                    "SELECT * FROM attempts WHERE attempt_id=?", (attempt["attempt_id"],)
                ).fetchone()
            if attempt["execution_state"] == "finalizing":
                # A confirmed stop finishes the attempt. An unconfirmed one keeps an
                # explicit uncertain ownership so it stays counted in capacity, in
                # active-work reporting and in stop/drain until evidence arrives.
                self._transition_attempt(connection, attempt, "finished" if shutdown_confirmed else "uncertain")
            task = connection.execute("SELECT * FROM tasks WHERE task_id=?", (attempt["task_id"],)).fetchone()
            cancel_requested = attempt["cancel_requested_at"] is not None or task["state"] == "cancelling"
            if status == "cancelled" and shutdown_confirmed:
                task_state = "cancelled"
            elif status == "cancelled":
                # The attempt reports a cancellation whose shutdown is unconfirmed:
                # a surviving process is possible, so the task is honestly
                # reconciliation-needed and keeps its retained resource claims.
                task_state = "reconciliation-needed"
            elif status == "ok" and shutdown_confirmed:
                # A completion that beat a cancel request is the one legal durable
                # outcome; the race is recorded rather than re-labelled. A governed
                # yield with a validated structured outcome requeues the same logical
                # task instead of pretending the original goal completed.
                governed_yield = (
                    self.workflow._run_optional(connection, task["task_id"]) is not None
                    and self.workflow.yield_disposition(result) is not None
                )
                # A normal yielded turn is not a completed goal that can beat a
                # cancellation. Preserve its raw receipt, but do not requeue it.
                task_state = ("cancelled" if cancel_requested else "queued") if governed_yield else "completed"
            elif status == "ok" and not shutdown_confirmed:
                task_state = "reconciliation-needed"
            else:
                task_state = "failed"
            connection.execute(
                "UPDATE attempts SET result_json=?, result_command_id=?, error=?, exit_code=?, signal=?,"
                " shutdown_confirmed=?, runtime_identity=COALESCE(?, runtime_identity), log_paths=COALESCE(?, log_paths),"
                " finished_at=?, updated_at=?, revision=revision+1 WHERE attempt_id=?",
                (
                    canonical_json(payload),
                    command_id,
                    error_text,
                    exit_code,
                    signal_name,
                    1 if shutdown_confirmed else 0,
                    schemas.optional_string(params, "runtimeIdentity"),
                    canonical_json(log_paths) if log_paths else None,
                    now,
                    now,
                    attempt["attempt_id"],
                ),
            )
            from .native_observations import persist
            persist(connection, attempt, result)
            self._transition_task(connection, task, task_state)
            connection.execute(
                "UPDATE tasks SET active_attempt_id=NULL, queue_reason=NULL WHERE task_id=?",
                (attempt["task_id"],),
            )
            if shutdown_confirmed:
                # Resources are released only when the owned process group is
                # confirmed gone. A cancelled or failed attempt whose shutdown is
                # unconfirmed keeps its claims retained: a replacement must not
                # touch resources a survivor may still be writing.
                self._release_claims(connection, attempt["attempt_id"])
            else:
                self._retain_claims(connection, attempt["attempt_id"])
            worker_row = connection.execute(
                "SELECT * FROM workers WHERE worker_id=?", (attempt["worker_id"],)
            ).fetchone()
            if worker_row is not None:
                connection.execute(
                    "UPDATE workers SET state='idle', current_attempt_id=NULL, last_seen_at=?, revision=revision+1"
                    " WHERE worker_id=?",
                    (now, attempt["worker_id"]),
                )
            self._append_event(
                connection,
                "task.completed" if task_state == "completed" else f"task.{task_state}",
                task_id=attempt["task_id"],
                attempt_id=attempt["attempt_id"],
                revision=task["revision"] + 1,
                payload={
                    "status": status,
                    "taskState": task_state,
                    "shutdownConfirmed": shutdown_confirmed,
                    "artifacts": verified_artifacts,
                    "cancelRaced": bool(cancel_requested and task_state == "completed"),
                },
            )
            attempt_row = connection.execute("SELECT * FROM attempts WHERE attempt_id=?", (attempt["attempt_id"],)).fetchone()
            task_row = connection.execute("SELECT * FROM tasks WHERE task_id=?", (attempt["task_id"],)).fetchone()
            response = {
                "attempt": self._attempt_view(attempt_row),
                "task": self._decorate(connection, task_row),
                "duplicate": False,
                "committed": True,
                "taskState": task_state,
            }
            # Completion-to-decision publication happens *inside this transaction*: a
            # recommendation, its retained proposal and the evaluation revision it may
            # publish commit with the attempt result, never as a side effect of a read.
            decision = self.decisions.complete(
                connection,
                task=task_row,
                attempt=attempt_row,
                status=status,
                result=result,
                shutdown_confirmed=shutdown_confirmed,
                error=error_text,
                now=now,
            )
            if decision is not None:
                response["decision"] = decision
                if decision.get("preflightFallback"):
                    task_row = connection.execute("SELECT * FROM tasks WHERE task_id=?", (task["task_id"],)).fetchone()
                    response.update(task=self._decorate(connection, task_row), taskState=task_row["state"])
            # Governed import and helper fan-in commit with the result: the validated
            # turn outcome, the assistance request and the workspace seal are one
            # durable fact with the attempt that produced them.
            governed = self.workflow.turn_concluded(
                connection,
                task=task_row,
                attempt=attempt_row,
                payload=payload,
                task_state=task_state,
                now=now,
            )
            if governed is not None:
                response["workflow"] = governed
            self.workflow.child_settled(
                connection,
                task=task_row,
                attempt=attempt_row,
                payload=payload,
                task_state=task_state,
                now=now,
            )
            self._store_receipt(
                connection,
                command_id,
                "worker.result",
                request,
                response,
                task_id=attempt["task_id"],
                attempt_id=attempt["attempt_id"],
                subject=subject,
            )
            head = self._head_of(connection)
        self._notify(head)
        return response

    def worker_release(self, params: dict) -> dict:
        """Give up an attempt. Used before a child is ever spawned; claims are released."""
        schemas.reject_unknown(
            params, {"workerId", "attemptId", "generation", "nonce", "reason", "evidence", "workerInstance"},
            "worker.release",
        )
        reason = schemas.optional_string(params, "reason") or "worker released the attempt before starting"
        evidence = params.get("evidence")
        never_spawned = False
        if evidence is not None:
            if not isinstance(evidence, dict):
                raise BoardError("INVALID_ARGUMENT", "evidence must be an object")
            schemas.reject_unknown(evidence, {"spawnIntentWritten"}, "release evidence")
            if evidence.get("spawnIntentWritten") is False:
                # The only accepted release evidence: this worker's own durable
                # record proves it never reached the spawn boundary. A missing PID
                # or an expired lease is never accepted as evidence.
                never_spawned = True
        with self.db.write() as connection:
            attempt, _worker = self._verify_attempt_actor(connection, params)
            if attempt["execution_state"] == "finished":
                raise BoardError("ATTEMPT_FINISHED", "This attempt is already finished")
            instance = schemas.optional_string(params, "workerInstance", max_length=128)
            if never_spawned and attempt["worker_instance"] and instance != attempt["worker_instance"]:
                raise BoardError(
                    "UNAUTHORIZED",
                    "Only the worker process instance that claimed this attempt may release it",
                    attemptId=attempt["attempt_id"],
                )
            if not never_spawned and (
                attempt["started_at"] is not None and attempt["execution_state"] != "starting"
            ):
                raise BoardError(
                    "CONFLICT",
                    "An attempt that already started cannot be released; report its result or leave it uncertain",
                )
            now = self.now()
            self._transition_attempt(connection, attempt, "finished")
            connection.execute(
                "UPDATE attempts SET finished_at=?, updated_at=?, error=?, shutdown_confirmed=1,"
                " revision=revision+1 WHERE attempt_id=?",
                (now, now, reason, attempt["attempt_id"]),
            )
            task = connection.execute("SELECT * FROM tasks WHERE task_id=?", (attempt["task_id"],)).fetchone()
            governed = self.workflow._run_optional(connection, task["task_id"])
            cancelled_goal = governed is not None and (governed["state"] == "cancelled" or task["state"] == "cancelling")
            self._transition_task(connection, task, "cancelled" if cancelled_goal else "failed")
            connection.execute(
                "UPDATE tasks SET active_attempt_id=NULL, queue_reason=NULL, updated_at=? WHERE task_id=?",
                (now, attempt["task_id"]),
            )
            self._release_claims(connection, attempt["attempt_id"])
            self._append_event(
                connection,
                "attempt.released",
                task_id=attempt["task_id"],
                attempt_id=attempt["attempt_id"],
                revision=task["revision"] + 1,
                payload={
                    "reason": reason,
                    "releaseEvidence": "spawnIntentWritten=false" if never_spawned else None,
                    "previousState": attempt["execution_state"],
                },
            )
            task_row = connection.execute("SELECT * FROM tasks WHERE task_id=?", (attempt["task_id"],)).fetchone()
            # An attempt released before its adapter ran means the decision never
            # reached a model: it fails honestly and releases its reader or writer.
            self.decisions.released(connection, task=task_row, reason=reason, now=now)
            released_attempt = connection.execute(
                "SELECT * FROM attempts WHERE attempt_id=?", (attempt["attempt_id"],)
            ).fetchone()
            self.workflow.attempt_released(
                connection, task=task_row, attempt=released_attempt, now=now, reason=reason
            )
            head = self._head_of(connection)
        self._notify(head)
        return {"task": self._decorate(connection, task_row) if False else self._task_view(task_row), "released": True}

    def worker_list(self, params: dict) -> dict:
        schemas.reject_unknown(params, {"state", "adapter", "limit"}, "worker.list")
        limit = schemas.optional_int(params, "limit", 50, 1, 200)
        clauses, values = [], []
        state = params.get("state")
        if state is not None:
            if state not in ("starting", "idle", "busy", "stopping", "lost"):
                raise BoardError("INVALID_ARGUMENT", "Unknown worker state filter")
            clauses.append("state = ?")
            values.append(state)
        adapter = params.get("adapter")
        if adapter is not None:
            clauses.append("adapter = ?")
            values.append(adapter)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self.db.read() as connection:
            rows = connection.execute(
                f"SELECT * FROM workers {where} ORDER BY last_seen_at DESC LIMIT ?", (*values, limit)
            ).fetchall()
        return {"workers": [self._worker_view(row) for row in rows]}

    # -- messages ------------------------------------------------------------
    def message_post(self, params: dict, *, command_id: str | None = None) -> dict:
        schemas.reject_unknown(
            params,
            {"runId", "taskId", "requestId", "inquiryId", "question", "author", "recipient", "correlationId", "waitMs"},
            "message.post",
        )
        inquiry_id = schemas.required_string(
            params, "inquiryId", max_length=128, pattern=schemas.INQUIRY_ID_PATTERN
        )
        question, question_bytes = schemas.bounded_text(params, "question", max_bytes=schemas.MAX_QUESTION_BYTES)
        author = schemas.optional_string(params, "author") or "cli"
        recipient = schemas.optional_string(params, "recipient")
        correlation_id = schemas.optional_string(params, "correlationId") or inquiry_id
        wait_ms = schemas.optional_int(params, "waitMs", 0, 0, 30000)
        payload_hash = sha256_text(question)
        request = {
            "inquiryId": inquiry_id,
            "questionSha256": payload_hash,
            "author": author,
            "taskId": params.get("runId") or params.get("taskId") or params.get("requestId"),
        }
        with self.db.write() as connection:
            if command_id:
                receipt = self._receipt(connection, command_id, "message.post", request)
                if receipt is not None:
                    return receipt
            task = self._task_row(connection, params)
            existing = connection.execute(
                "SELECT * FROM messages WHERE task_id=? AND inquiry_id=?", (task["task_id"], inquiry_id)
            ).fetchone()
            if existing is not None:
                if existing["payload_hash"] != payload_hash:
                    raise BoardError(
                        "CONFLICT",
                        "inquiryId already belongs to a different question; repeat the identical text to read it back",
                        inquiryId=inquiry_id,
                    )
                response = {"message": self._message_view(existing), "duplicate": True}
                settled = existing["state"] in ("answered", "delivered", "discarded", "unavailable")
                if command_id:
                    self._store_receipt(
                        connection, command_id, "message.post", request, response, task_id=task["task_id"]
                    )
                head = self._head_of(connection)
            else:
                count = connection.execute(
                    "SELECT COUNT(*) AS count FROM messages WHERE task_id=?", (task["task_id"],)
                ).fetchone()["count"]
                if int(count) >= schemas.MAX_INQUIRIES_PER_RUN:
                    raise BoardError(
                        "TOO_MANY_INQUIRIES",
                        f"At most {schemas.MAX_INQUIRIES_PER_RUN} inquiries are retained per run",
                        inquiryId=inquiry_id,
                    )
                attempt = self._selected_attempt(connection, task)
                now = self.now()
                active = task["state"] in ("running", "cancelling")
                state = "queued" if active and task["adapter"] == "dsh" else ("unavailable" if not active else "queued")
                reason = None if state == "queued" else "no live agent to receive this question"
                message_id = str(uuid.uuid4())
                connection.execute(
                    "INSERT INTO messages(message_id, task_id, attempt_id, inquiry_id, direction, author, recipient,"
                    " correlation_id, body, body_bytes, payload_hash, state, reason, created_at, updated_at, revision)"
                    " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1)",
                    (
                        message_id,
                        task["task_id"],
                        attempt["attempt_id"] if attempt else None,
                        inquiry_id,
                        "question",
                        author,
                        recipient,
                        correlation_id,
                        question,
                        question_bytes,
                        payload_hash,
                        state,
                        reason,
                        now,
                        now,
                    ),
                )
                self._append_event(
                    connection,
                    "message.posted",
                    task_id=task["task_id"],
                    attempt_id=attempt["attempt_id"] if attempt else None,
                    revision=task["revision"],
                    payload={"inquiryId": inquiry_id, "state": state, "questionBytes": question_bytes},
                )
                row = connection.execute("SELECT * FROM messages WHERE message_id=?", (message_id,)).fetchone()
                response = {"message": self._message_view(row), "duplicate": False}
                settled = state in ("answered", "delivered", "discarded", "unavailable")
                if command_id:
                    self._store_receipt(
                        connection, command_id, "message.post", request, response, task_id=task["task_id"]
                    )
                head = self._head_of(connection)
        self._notify(head)
        # The wait is strictly outside the write transaction: holding BEGIN
        # IMMEDIATE here would block cancel, renew and result commits.
        if wait_ms:
            return {
                **self.message_wait({"runId": task["task_id"], "inquiryId": inquiry_id, "timeoutMs": wait_ms}),
                "duplicate": not settled,
            }
        return response

    def message_update(self, params: dict) -> dict:
        """Apply one bounded delivery/answer observation.

        Every field is validated on a candidate copy before anything is written, a
        recorded answer is terminal, and an ``answered`` state without a nonblank
        answer is rejected instead of being committed half-answered.
        """
        schemas.reject_unknown(
            params,
            {"runId", "taskId", "messageId", "inquiryId", "state", "reason", "delivery", "answer", "actor"},
            "message.update",
        )
        inquiry_id = schemas.optional_string(params, "inquiryId", max_length=128, pattern=schemas.INQUIRY_ID_PATTERN)
        message_id = schemas.optional_string(params, "messageId", max_length=128)
        if inquiry_id is None and message_id is None:
            raise BoardError("INVALID_ARGUMENT", "inquiryId or messageId is required")
        state = params.get("state")
        if state is not None and state not in MESSAGE_STATES:
            raise BoardError("INVALID_ARGUMENT", "Unknown message state")
        delivery = params.get("delivery")
        if delivery is not None and not isinstance(delivery, dict):
            raise BoardError("INVALID_ARGUMENT", "delivery must be an object or null")
        answer = params.get("answer")
        if answer is not None and not isinstance(answer, dict):
            raise BoardError("INVALID_ARGUMENT", "answer must be an object or null")
        actor = schemas.optional_string(params, "actor") or "bridge"
        reason = params.get("reason")
        if reason is not None and not isinstance(reason, str):
            raise BoardError("INVALID_ARGUMENT", "reason must be a string or null")
        with self.db.write() as connection:
            if inquiry_id is not None:
                task = self._task_row(connection, params)
                row = connection.execute(
                    "SELECT * FROM messages WHERE task_id=? AND inquiry_id=?", (task["task_id"], inquiry_id)
                ).fetchone()
            else:
                row = connection.execute("SELECT * FROM messages WHERE message_id=?", (message_id,)).fetchone()
                task = connection.execute("SELECT * FROM tasks WHERE task_id=?", (row["task_id"],)).fetchone() if row else None
            if row is None:
                raise BoardError("NOT_FOUND", "Unknown inquiryId or messageId")
            candidate = self._message_view(row)
            answered = candidate["answer"] is not None
            terminal = candidate["state"] in ("answered", "discarded", "unavailable")
            if state is not None and not terminal:
                candidate["state"] = state
            if reason is not None:
                candidate["reason"] = reason[:200]
            if delivery is not None:
                bounded = dict(candidate["delivery"] or {})
                for key in ("messageId", "agentStatusAtInject", "injectedAt", "insertedAt", "claimedAt", "deliveredAt", "observedAt", "observedAgentStatus"):
                    value = delivery.get(key)
                    if value in (None, ""):
                        continue
                    bounded[key] = str(value)[:200]
                candidate["delivery"] = bounded
            if answer is not None:
                text = answer.get("text")
                if not isinstance(text, str) or not text.strip():
                    raise BoardError("INVALID_ARGUMENT", "answer must carry nonblank text")
                size = len(text.encode("utf-8"))
                if size > schemas.MAX_ANSWER_BYTES:
                    raise BoardError("INVALID_ARGUMENT", "answer exceeds the size limit")
                if not answered:
                    candidate["answer"] = {
                        "text": text,
                        "bytes": size,
                        "via": (answer.get("via") or None) if isinstance(answer.get("via"), str) else None,
                        "toolCallId": (answer.get("toolCallId") or None) if isinstance(answer.get("toolCallId"), str) else None,
                        "at": (answer.get("at") or None) if isinstance(answer.get("at"), str) else None,
                        "truncated": answer.get("truncated") is True,
                        "source": (answer.get("source") or None) if isinstance(answer.get("source"), str) else None,
                    }
                    candidate["state"] = "answered"
            if candidate["state"] == "answered" and not (candidate["answer"] or {}).get("text", "").strip():
                raise BoardError("INVALID_ARGUMENT", "answered requires a nonblank answer")
            if candidate["state"] not in MESSAGE_STATES:
                raise BoardError("INVALID_ARGUMENT", "Unknown message state")
            now = self.now()
            connection.execute(
                "UPDATE messages SET state=?, reason=?, delivery_json=?, answer_json=?, attempts_count=attempts_count+1,"
                " updated_at=?, revision=revision+1 WHERE message_id=?",
                (
                    candidate["state"],
                    candidate["reason"],
                    canonical_json(candidate["delivery"]) if candidate["delivery"] else None,
                    canonical_json(candidate["answer"]) if candidate["answer"] else None,
                    now,
                    row["message_id"],
                ),
            )
            self._append_event(
                connection,
                "message.updated",
                task_id=row["task_id"],
                attempt_id=row["attempt_id"],
                revision=task["revision"] if task else None,
                payload={"inquiryId": row["inquiry_id"], "state": candidate["state"], "actor": actor},
            )
            updated = connection.execute("SELECT * FROM messages WHERE message_id=?", (row["message_id"],)).fetchone()
            head = self._head_of(connection)
        self._notify(head)
        return {"message": self._message_view(updated)}

    def message_get(self, params: dict) -> dict:
        schemas.reject_unknown(params, {"runId", "taskId", "requestId", "inquiryId", "messageId"}, "message.get")
        with self.db.read() as connection:
            if params.get("inquiryId"):
                task = self._task_row(connection, params)
                row = connection.execute(
                    "SELECT * FROM messages WHERE task_id=? AND inquiry_id=?", (task["task_id"], params["inquiryId"])
                ).fetchone()
            else:
                row = connection.execute(
                    "SELECT * FROM messages WHERE message_id=?", (schemas.required_string(params, "messageId", max_length=128),)
                ).fetchone()
            if row is None:
                raise BoardError("NOT_FOUND", "Unknown inquiryId or messageId")
            return {"message": self._message_view(row)}

    def message_list(self, params: dict) -> dict:
        schemas.reject_unknown(params, {"runId", "taskId", "state", "limit"}, "message.list")
        limit = schemas.optional_int(params, "limit", 50, 1, 200)
        clauses, values = [], []
        if params.get("runId") or params.get("taskId"):
            task = None
            with self.db.read() as connection:
                task = self._task_row(connection, params)
            clauses.append("task_id = ?")
            values.append(task["task_id"])
        state = params.get("state")
        if state is not None:
            if state not in MESSAGE_STATES:
                raise BoardError("INVALID_ARGUMENT", "Unknown message state filter")
            clauses.append("state = ?")
            values.append(state)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self.db.read() as connection:
            rows = connection.execute(
                f"SELECT * FROM messages {where} ORDER BY created_at DESC LIMIT ?", (*values, limit)
            ).fetchall()
            head = self._head_of(connection)
        return {"messages": [self._message_view(row) for row in rows], "cursor": head}

    def message_wait(self, params: dict) -> dict:
        schemas.reject_unknown(params, {"runId", "taskId", "inquiryId", "timeoutMs"}, "message.wait")
        inquiry_id = schemas.required_string(params, "inquiryId", max_length=128, pattern=schemas.INQUIRY_ID_PATTERN)
        timeout_ms = schemas.optional_int(params, "timeoutMs", 30000, 0, 30000)
        deadline = self._monotonic() + timeout_ms / 1000.0
        while True:
            message = self.message_get({**{k: params[k] for k in ("runId", "taskId") if k in params}, "inquiryId": inquiry_id})["message"]
            if message["state"] in ("answered", "delivered", "discarded", "unavailable"):
                return {"message": message, "timedOut": False}
            remaining = deadline - self._monotonic()
            if remaining <= 0:
                return {"message": message, "timedOut": True}
            try:
                self.events_wait(
                    {"after": self.head(), "timeoutMs": int(min(1000, remaining * 1000)), "taskId": message["taskId"], "limit": 1}
                )
            except BoardError as error:
                if error.code != "WAIT_ABANDONED":
                    raise
            if self._monotonic() >= deadline:
                message = self.message_get({"inquiryId": inquiry_id, "runId": message["taskId"]})["message"]
                return {"message": message, "timedOut": message["state"] not in ("answered", "delivered", "discarded", "unavailable")}

    # -- artifacts -----------------------------------------------------------
    def artifact_list(self, params: dict) -> dict:
        schemas.reject_unknown(params, {"runId", "taskId", "attemptId"}, "artifact.list")
        with self.db.read() as connection:
            if params.get("attemptId"):
                rows = connection.execute(
                    "SELECT * FROM artifacts WHERE attempt_id=? ORDER BY created_at", (params["attemptId"],)
                ).fetchall()
            else:
                task = self._task_row(connection, params)
                rows = connection.execute(
                    "SELECT * FROM artifacts WHERE task_id=? ORDER BY created_at", (task["task_id"],)
                ).fetchall()
        return {"artifacts": [self._artifact_view(row) for row in rows]}

    # -- recovery ------------------------------------------------------------
    def reconcile_startup(self) -> dict:
        """Make every pre-restart in-flight attempt honestly uncertain.

        A restarted service never infers "stopped" from an expired lease or a
        missing PID: it marks possible external work uncertain and keeps its
        resource claims. The legitimate worker reattaches with its own nonce and
        clears the uncertainty; only an explicit retry creates a new generation.
        """
        summary = {"uncertain": 0, "retained": 0}
        with self.db.write() as connection:
            now = self.now()
            rows = connection.execute(
                "SELECT * FROM attempts WHERE execution_state IN ('starting','executing','finalizing')"
            ).fetchall()
            for attempt in rows:
                connection.execute(
                    "UPDATE attempts SET execution_state='uncertain', ownership='uncertain', lease_expires_at=NULL,"
                    " updated_at=?, revision=revision+1 WHERE attempt_id=?",
                    (now, attempt["attempt_id"]),
                )
                self._retain_claims(connection, attempt["attempt_id"])
                summary["uncertain"] += 1
                task = connection.execute("SELECT * FROM tasks WHERE task_id=?", (attempt["task_id"],)).fetchone()
                if task is not None and task["state"] in ("running", "cancelling"):
                    connection.execute(
                        "UPDATE tasks SET queue_reason=?, updated_at=? WHERE task_id=?",
                        (UNCERTAIN_QUEUE_REASON, now, attempt["task_id"]),
                    )
                self._append_event(
                    connection,
                    "attempt.uncertain",
                    task_id=attempt["task_id"],
                    attempt_id=attempt["attempt_id"],
                    revision=task["revision"] if task else None,
                    payload={
                        "reason": "service restarted while this attempt owned possible external work; "
                        "its resource claims are retained until the worker reattaches or an operator retries"
                    },
                )
                # A decision whose helper this service no longer supervises may not
                # become a current recommendation: it is fenced as stale and its
                # admission or writer grant is released.
                self.decisions.fence_attempt(
                    connection,
                    attempt=attempt,
                    reason="the service restarted while this decision was running",
                    now=now,
                )
            summary["retained"] = int(
                connection.execute(
                    "SELECT COUNT(*) AS count FROM resource_claims WHERE state='retained'"
                ).fetchone()["count"]
            )
            stale = connection.execute(
                "SELECT * FROM workers WHERE state IN ('busy','starting') AND current_attempt_id IS NOT NULL"
            ).fetchall()
            for worker in stale:
                connection.execute(
                    "UPDATE workers SET state='lost', revision=revision+1 WHERE worker_id=?", (worker["worker_id"],)
                )
            head = self._head_of(connection)
        if summary["uncertain"] or head:
            self._notify(head)
        return summary

    def mark_expired_leases(self, *, grace_seconds: int = 0) -> int:
        """Lease expiry marks an execution uncertain; it never marks it stopped."""
        marked = 0
        threshold = self._now_minus(grace_seconds)
        with self.db.write() as connection:
            rows = connection.execute(
                "SELECT * FROM attempts WHERE execution_state IN ('starting','executing','finalizing')"
                " AND lease_expires_at IS NOT NULL AND lease_expires_at < ?",
                (threshold,),
            ).fetchall()
            for attempt in rows:
                connection.execute(
                    "UPDATE attempts SET execution_state='uncertain', ownership='uncertain', updated_at=?,"
                    " revision=revision+1 WHERE attempt_id=?",
                    (self.now(), attempt["attempt_id"]),
                )
                self._retain_claims(connection, attempt["attempt_id"])
                task = connection.execute("SELECT * FROM tasks WHERE task_id=?", (attempt["task_id"],)).fetchone()
                self._append_event(
                    connection,
                    "attempt.lease_expired",
                    task_id=attempt["task_id"],
                    attempt_id=attempt["attempt_id"],
                    revision=task["revision"] if task else None,
                    payload={"leaseExpiresAt": attempt["lease_expires_at"], "ownership": "uncertain"},
                )
                # Lease expiry is never evidence that a process stopped, so the
                # decision it belonged to is stale — its result is retained but can
                # no longer become a current recommendation.
                self.decisions.fence_attempt(
                    connection,
                    attempt=attempt,
                    reason="the attempt lease expired while this decision was running",
                    now=self.now(),
                )
                marked += 1
            head = self._head_of(connection) if marked else 0
        if marked:
            self._notify(head)
        return marked

    def _now_minus(self, seconds: int) -> str:
        from datetime import datetime, timedelta, timezone

        moment = datetime.now(timezone.utc) - timedelta(seconds=max(0, seconds))
        return moment.isoformat(timespec="milliseconds").replace("+00:00", "Z")

    def resource_claims(self, *, states: tuple[str, ...] = ("held", "retained")) -> list[dict]:
        """Public view of the reservations that block a replacement attempt."""
        markers = ",".join("?" for _ in states)
        with self.db.read() as connection:
            rows = connection.execute(
                f"SELECT * FROM resource_claims WHERE state IN ({markers}) ORDER BY created_at", states
            ).fetchall()
        return [
            {
                "claimId": row["claim_id"],
                "taskId": row["task_id"],
                "attemptId": row["attempt_id"],
                "resource": row["resource"],
                "kind": row["kind"],
                "state": row["state"],
                "createdAt": row["created_at"],
            }
            for row in rows
        ]

    def active_work(self) -> dict:
        with self.db.read() as connection:
            attempts = connection.execute(
                "SELECT a.*, t.state AS task_state, t.cwd AS cwd, t.request_id AS request_id FROM attempts a"
                f" JOIN tasks t ON t.task_id = a.task_id WHERE {self.UNRESOLVED_SQL.replace('execution_state', 'a.execution_state').replace('result_json', 'a.result_json').replace('shutdown_confirmed', 'a.shutdown_confirmed')}"
                " ORDER BY a.created_at"
            ).fetchall()
            queued = connection.execute("SELECT * FROM tasks WHERE state='queued' ORDER BY created_at").fetchall()
        return {
            "attempts": [
                {
                    **self._attempt_view(row, include_result=False),
                    "taskState": row["task_state"],
                    "cwd": row["cwd"],
                    "requestId": row["request_id"],
                }
                for row in attempts
            ],
            "queuedTasks": [self._task_view(row) for row in queued],
        }

    def count_tasks(self) -> int:
        with self.db.read() as connection:
            return int(connection.execute("SELECT COUNT(*) AS count FROM tasks").fetchone()["count"])

    def integrity(self) -> dict:
        with self.db.read() as connection:
            integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
            foreign_keys = connection.execute("PRAGMA foreign_key_check").fetchall()
            version = connection.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()["value"]
        return {
            "integrity": integrity,
            "foreignKeyViolations": len(foreign_keys),
            "schemaVersion": int(version),
        }


def _constant_time_equal(left: str, right: str) -> bool:
    import hmac

    return hmac.compare_digest(left or "", right or "")


def _file_sha256(path: Path) -> str:
    import hashlib

    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
