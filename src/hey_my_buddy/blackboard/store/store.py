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

from ...protocol import activity as activity_module
from ..tasks import micro_task
from ..tasks import scheduling
from ...protocol import schemas
from ...protocol.reasons import reason as make_reason, reason_from_json, validate_reason
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
from ...errors import BoardError

#: Documented task transition table. A transition outside this map is rejected.
TASK_TRANSITIONS: dict[str, frozenset[str]] = {
    "queued": frozenset({"queued", "running", "cancelling", "cancelled", "reconciliation-needed"}),
    # ``running -> queued`` is the micro-task yield: a turn returned a structured
    # assistance/attention outcome, the attempt stopped with confirmed shutdown and
    # the same logical task waits for the Host instead of pretending to be complete.
    "running": frozenset(
        {"running", "queued", "cancelling", "completed", "failed", "cancelled", "reconciliation-needed"}
    ),
    "cancelling": frozenset({"cancelling", "completed", "failed", "cancelled", "reconciliation-needed"}),
    "reconciliation-needed": frozenset({"reconciliation-needed", "queued", "cancelling", "cancelled", "failed", "completed"}),
    "completed": frozenset({"completed", "concluded"}),
    "failed": frozenset({"failed", "queued", "concluded"}),
    "cancelled": frozenset({"cancelled", "queued", "concluded"}),
    "concluded": frozenset({"concluded"}),
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
MAX_EVENT_PAGE = 200

#: Connection observations, never execution progress, clear these waiting reasons.
UNCERTAIN_QUEUE_REASON = "attempt-uncertain-after-restart"
UNKNOWN_QUEUE_REASON = "execution-connection-unknown"


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
        wait_capacity: int = 32,
        clock: Callable[[], str] = utc_now,
    ):
        self.directory = Path(directory)
        self.db = Database(self.directory)
        #: The one machine-wide concurrent-attempt ceiling (HMB_MAX_CONCURRENT).
        #: Routing/selection attempts and business attempts share it and the same
        #: per-model-service counters; there is no separate decision lane.
        self.max_concurrent = scheduling.clamp_total(max_concurrent)
        self.wait_capacity = max(1, int(wait_capacity))
        self._clock = clock
        self._condition = threading.Condition()
        self._head = 0
        self._wait_stopped = False
        self.on_change = None
        self.persistence_error: str | None = None
        # The board is the single composition root for durable evaluation and
        # decision state: both wrap this same store (one SQLite file, one event
        # stream) and neither opens a transaction of its own.
        from ..routing.decision import DecisionCoordinator
        from ..evaluation.evaluation import EvaluationStore
        from ..tasks.workflow import WorkflowCoordinator

        self.evaluation = EvaluationStore(self, clock=self._clock)
        self.decisions = DecisionCoordinator(self, self.evaluation)
        # The micro-task workflow layer wraps this same store: it opens no second
        # authority and every one of its mutations is one of these transactions.
        self.workflow = WorkflowCoordinator(self, clock=self._clock)
        self._recovery_lock = threading.Lock()

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
            changed = head > self._head
            self._head = max(self._head, head)
            self._condition.notify_all()
        if changed and self.on_change is not None:
            self.on_change()

    def stop_waiters(self) -> None:
        """Release all event waits before shutting down their service transport."""
        with self._condition:
            self._wait_stopped = True
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
        event_payload = dict(payload or {})
        actor = event_payload.pop("actor", None)
        cursor = connection.execute(
            "INSERT INTO events(task_id, attempt_id, revision, kind, payload_json, actor_role, actor_id, created_at) VALUES(?,?,?,?,?,?,?,?)",
            (task_id, attempt_id, revision, kind, canonical_json(event_payload),
             actor["role"] if actor is not None else None, actor["id"] if actor is not None else None, at),
        )
        seq = int(cursor.lastrowid)
        if kind == "attempt.activity":
            connection.execute(
                "DELETE FROM events WHERE task_id=? AND kind=? AND attempt_id=? AND seq<?",
                (task_id, kind, attempt_id, seq),
            )
        elif kind in ("harness.checked", "harness.probe_unknown", "harness.account_observed"):
            connection.execute(
                "DELETE FROM events WHERE task_id IS NULL AND kind=? AND json_extract(payload_json,'$.harness')=? AND seq<?",
                (kind, event_payload["harness"], seq),
            )
        from ..tasks.macro_tasks import record_activity
        record_activity(connection, task_id, seq, at)
        self.workflow.owned_event(connection, task_id=task_id, kind=kind, seq=seq, now=at)
        return seq

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

    def task_wait(self, params: dict) -> dict:
        schemas.reject_unknown(params, {"runId", "taskId", "afterRevision", "timeoutMs", "consumeReminders", "reminderWaitId"}, "task.wait")
        consume_reminders = schemas.optional_bool(params, "consumeReminders", False)
        if consume_reminders:
            schemas.required_string(params, "reminderWaitId", max_length=128)
        elif "reminderWaitId" in params:
            raise BoardError("INVALID_ARGUMENT", "reminderWaitId requires consumeReminders")
        timeout_ms = schemas.optional_int(params, "timeoutMs", 30000, 0, 30000)
        after_revision = params.get("afterRevision")
        if after_revision is not None and (
            isinstance(after_revision, bool) or not isinstance(after_revision, int) or after_revision < 0
        ):
            raise BoardError("INVALID_ARGUMENT", "afterRevision must be a nonnegative integer")
        deadline = self._monotonic() + timeout_ms / 1000.0
        while True:
            head = self.head()
            if consume_reminders:
                from ..tasks.reminders import consume
                reminder = consume(self, params)
                if reminder is not None:
                    view = self.task_get({"runId": params.get("runId"), "taskId": params.get("taskId")})["task"]
                    return {**view, "reminder": reminder}
            view = self.task_get({"runId": params.get("runId"), "taskId": params.get("taskId")})["task"]
            # Re-check after subscribing so a change between read and subscribe is
            # never lost; the recheck happens inside events_wait.
            if view["resultAvailable"] or timeout_ms == 0 or (
                after_revision is not None and view["revision"] > after_revision
            ):
                return view
            remaining = deadline - self._monotonic()
            if remaining <= 0:
                return view
            self.events_wait(
                {
                    "after": head,
                    "timeoutMs": int(max(1, remaining * 1000)),
                    "taskId": view["taskId"],
                    "limit": 1,
                }
            )

    def events_wait(self, params: dict, *, stop: threading.Event | None = None) -> dict:
        """Bounded event wait with a check/subscribe/recheck sequence.

        No database transaction and no exclusive resource lock is held while
        waiting, so unlimited concurrent waiters cannot block cancellation or
        result commits.
        """
        schemas.reject_unknown(params, {"after", "timeoutMs", "limit", "taskId", "runId"}, "events.wait")
        after = schemas.optional_int(params, "after", 0, 0, 2**62)
        timeout_ms = schemas.optional_int(params, "timeoutMs", 30000, 0, 30000)
        limit = schemas.optional_int(params, "limit", 100, 1, MAX_EVENT_PAGE)
        task_id = params.get("taskId") or params.get("runId")
        with self._condition:
            if self._wait_stopped:
                raise BoardError("SERVICE_STOPPING", "The service is stopping; no task state was changed")
        page = self.events_read({"after": after, "limit": limit, **({"taskId": task_id} if task_id else {})})
        # Compare against the *filtered* head: an unrelated task's later event must
        # not make this task's unchanged cursor look satisfied.
        if page["truncated"] or page["events"] or page["head"] > page["cursor"] or timeout_ms == 0:
            return {**page, "timedOut": False}
        deadline = self._monotonic() + timeout_ms / 1000.0
        with self._condition:
            while True:
                if self._wait_stopped:
                    raise BoardError("SERVICE_STOPPING", "The service is stopping; no task state was changed")
                if stop is not None and stop.is_set():
                    raise BoardError("WAIT_ABANDONED", "The wait was abandoned; no task state was changed")
                observed = self._filtered_head(task_id)
                if observed > page["cursor"]:
                    break
                remaining = deadline - self._monotonic()
                if remaining <= 0:
                    return {**page, "timedOut": True}
                self._condition.wait(remaining if stop is None else min(remaining, 1.0))
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
        payload = json.loads(row["payload_json"])
        if row["actor_role"] is not None:
            payload["actor"] = {"role": row["actor_role"], "id": row["actor_id"]}
        return {
            "seq": int(row["seq"]),
            "kind": row["kind"],
            "taskId": row["task_id"],
            "attemptId": row["attempt_id"],
            "revision": row["revision"],
            "payload": payload,
            "createdAt": row["created_at"],
        }

    # -- receipts ------------------------------------------------------------
    def _subject(self, nonce: str, attempt_id: str) -> str:
        """Bind a command replay to one execution's nonce verifier."""
        return f"{self.db.nonce_verifier(nonce)}:{attempt_id}"

    def _receipt_entry(
        self,
        connection: sqlite3.Connection,
        command_id: str,
        kind: str,
        request: dict | None,
        subject: str | None = None,
        *,
        deleted_only: bool = False,
    ) -> sqlite3.Row | None:
        row = connection.execute("SELECT * FROM commands WHERE command_id = ?", (command_id,)).fetchone()
        if row is None:
            return None
        if deleted_only and row["deletion_id"] is None:
            return None
        if row["subject"] and ((subject is None and row["deletion_id"] is not None)
                               or (subject is not None and not _constant_time_equal(row["subject"], subject))):
            raise BoardError(
                "UNAUTHORIZED",
                "This commandId belongs to a different execution identity; it cannot be replayed from here",
                commandId=command_id,
            )
        if row["kind"] != kind or (row["deletion_id"] is None and request is not None
                                    and row["request_hash"] != sha256_text(canonical_json(request))):
            raise BoardError(
                "CONFLICT",
                "This commandId already belongs to a different request; use a new commandId",
                commandId=command_id,
            )
        return row

    def _receipt_response(self, connection: sqlite3.Connection, row: sqlite3.Row | None) -> dict | None:
        if row is None:
            return None
        if row["deletion_id"] is not None:
            return {"commandId": row["command_id"], "deleted": True}
        from ..tasks.command_receipts import replay_response

        return replay_response(self.workflow, connection, row["kind"], json.loads(row["response_json"]))

    def _receipt(
        self,
        connection: sqlite3.Connection,
        command_id: str,
        kind: str,
        request: dict,
        subject: str | None = None,
    ) -> dict | None:
        return self._receipt_response(
            connection, self._receipt_entry(connection, command_id, kind, request, subject)
        )

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
        from ..tasks.command_receipts import stored_response

        connection.execute(
            "INSERT OR REPLACE INTO commands(command_id, task_id, attempt_id, kind, request_hash, response_json,"
            " subject, created_at) VALUES(?,?,?,?,?,?,?,?)",
            (
                command_id,
                task_id,
                attempt_id,
                kind,
                sha256_text(canonical_json(request)),
                canonical_json(stored_response(kind, response)),
                subject,
                self.now(),
            ),
        )

    # -- views ---------------------------------------------------------------
    @staticmethod
    def _task_view(row: sqlite3.Row, *, execution_spec: dict | None = None) -> dict:
        """Mechanical task facts; execution callers supply their admitted specification."""
        spec = json.loads(row["spec_json"]) if execution_spec is None else execution_spec
        view = {
            "runId": row["task_id"],
            "taskId": row["task_id"],
            "requestId": row["request_id"],
            "owner": row["owner"],
            "adapter": row["adapter"],
            "cwd": row["cwd"],
            "task": spec.get("task"),
            "spec": spec,
            **({"routerBudgetSeconds": row["router_budget_seconds"]} if row["adapter"] == "decision"
               else {"expectedSeconds": row["expected_seconds"]}),
            "inputFingerprint": row["input_fingerprint"],
            "fingerprintVersion": row["fingerprint_version"],
            "requiredCapabilities": json.loads(row["required_capabilities"]),
            "exclusiveResources": json.loads(row["exclusive_resources"]),
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
            "reviewRejectedAt": row["review_rejected_at"],
            "reviewRejectionNote": row["review_rejection_note"],
            "resultAvailable": False,
            "shutdownConfirmed": False,
            "terminationReason": None,
            "stopInitiator": None,
            "graceDeadline": None,
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
            "adapter": row["adapter"],
            "executionState": row["execution_state"],
            "ownership": row["ownership"],
            "runtimeIdentity": row["runtime_identity"],
            "logPaths": json.loads(row["log_paths"]),
            "resultAvailable": row["result_json"] is not None,
            "tokenUsage": json.loads(row["token_usage_json"]) if row["token_usage_json"] else None,
            "shutdownConfirmed": bool(row["shutdown_confirmed"]),
            "cancelRequested": row["cancel_requested_at"] is not None,
            "cancelRequestedAt": row["cancel_requested_at"],
            "stopInitiator": row["stop_initiator"],
            "graceDeadline": row["grace_deadline"],
            "forceReason": row["force_reason"],
            "forceRequestedAt": row["force_requested_at"],
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
            "reason": reason_from_json(row["reason_code"], row["reason_params_json"]),
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

    def _decorate(self, connection: sqlite3.Connection, task: sqlite3.Row, *, lineage_result: dict | None = None) -> dict:
        view = self._task_view(task)
        if task["state"] in ("cancelled", "cancelling"):
            from ..tasks.cancellation import for_run
            view["cancellation"] = for_run(connection, task["task_id"])
        attempt = self._selected_attempt(connection, task)
        view["tokenUsage"] = json.loads(attempt["token_usage_json"]) if attempt is not None and attempt["token_usage_json"] else None
        if attempt is not None:
            from ..evaluation.native_observations import failure_view
            recorded = json.loads(attempt["result_json"]) if attempt["result_json"] else {}
            view["quotaFailure"] = failure_view(recorded.get("result"), harness=attempt["adapter"])
            view["selectedAttempt"] = self._attempt_view(attempt)
            view["resultAvailable"] = attempt["result_json"] is not None
            view["shutdownConfirmed"] = bool(attempt["shutdown_confirmed"])
            view["terminationReason"] = recorded.get("terminationReason")
            view["interruptionEvidence"] = (recorded.get("result") or {}).get("interruptionEvidence")
            view["selectedAttempt"]["processIdentity"] = self._process_identity(connection, attempt["attempt_id"])
            evidence = connection.execute("SELECT * FROM attempt_shutdown_evidence WHERE attempt_id=?",
                                          (attempt["attempt_id"],)).fetchone()
            view["selectedAttempt"]["shutdownEvidence"] = ({"kind": evidence["evidence_kind"],
                "detail": json.loads(evidence["detail_json"]), "observedAt": evidence["observed_at"]}
                if evidence is not None else None)
            if evidence is not None and view["terminationReason"] is None:
                view["terminationReason"] = view["selectedAttempt"]["shutdownEvidence"]["detail"].get("terminationReason")
            view["selectedAttempt"]["issues"] = [
                {"stage": row["stage"], "code": row["code"], "message": row["message"],
                 "observedAt": row["observed_at"]}
                for row in connection.execute("SELECT * FROM execution_issues WHERE attempt_id=? ORDER BY stage",
                                              (attempt["attempt_id"],))]
            view["attemptGeneration"] = attempt["generation"]
            view["attemptState"] = attempt["execution_state"]
            view["logPaths"] = json.loads(attempt["log_paths"])
            view["cancelRequested"] = attempt["cancel_requested_at"] is not None
            view["stopInitiator"] = attempt["stop_initiator"]
            view["graceDeadline"] = attempt["grace_deadline"]
            # The projection is looked up by the *selected* attempt, so a replacement
            # generation never inherits the previous attempt's observation.
            view["activity"] = self._activity_latest(connection, attempt["attempt_id"])
            from ..tasks.reminders import status as reminder_status
            view["selectedAttempt"]["reminders"] = reminder_status(connection, attempt, view["activity"])
        artefacts = connection.execute(
            "SELECT * FROM artifacts WHERE task_id = ? ORDER BY created_at", (task["task_id"],)
        ).fetchall()
        view["artifacts"] = [self._artifact_view(row) for row in artefacts]
        view["artifactCount"] = len(artefacts)
        messages = connection.execute(
            "SELECT state, COUNT(*) AS count FROM messages WHERE task_id = ? GROUP BY state", (task["task_id"],)
        ).fetchall()
        view["inquiries"] = {row["state"]: row["count"] for row in messages}
        # Read-only micro task provenance (source/current Host, lineage and project)
        # derived from existing authoritative rows; a caller that already resolved the
        # page passes it in so one page costs one relation read.
        view["lineage"] = lineage_result if lineage_result is not None else micro_task.resolve(connection, task)
        workflow = self.workflow.task_extension(connection, task)
        if workflow is not None:
            view["workflowShutdown"] = self.workflow.shutdown_summary(connection, task["task_id"])
            view["workflow"] = workflow
            view["workflowState"] = workflow["state"]
            view["awaitingHost"] = workflow["awaitingHost"]
            if (workflow["state"] == "cancelled" or
                    (workflow["hostConclusion"] is not None and workflow["hostConclusion"]["executionStatus"] == "cancelled")) and "cancellation" not in view:
                from ..tasks.cancellation import for_run
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

    #: One bounded candidate scan. Tasks of model services that are already at their
    #: concurrent-attempt limit are filtered out *before* this LIMIT, so a deep
    #: full-model-service backlog cannot occupy the whole bounded window and hide unrelated
    #: runnable work.
    CLAIM_CANDIDATE_LIMIT = 50

    #: The queued-task model service as one SQL expression. It mirrors
    #: :func:`scheduling.model_service` over the task's effective specification: a
    #: micro-task run's resolved buddy wins over the immutable
    #: original request, and tasks without a complete harness/provider/model triple
    #: have no model service.
    _QUEUED_MODEL_SERVICE_PROVIDER = (
        "COALESCE(json_extract(r.buddy_json,'$.provider'),"
        " json_extract(t.spec_json,'$.provider'))"
    )
    _QUEUED_MODEL_SERVICE_MODEL = (
        "COALESCE(json_extract(r.buddy_json,'$.model'),"
        " json_extract(t.spec_json,'$.model'))"
    )

    def _total_active(self, connection: sqlite3.Connection) -> int:
        """Unresolved attempts against the machine-wide ceiling."""
        row = connection.execute(f"SELECT COUNT(*) AS total FROM attempts WHERE {self.UNRESOLVED_SQL}").fetchone()
        return int(row["total"])

    def _model_active_counts(self, connection: sqlite3.Connection) -> dict[tuple[str, str, str], int]:
        """Unresolved attempts per frozen model service.

        The count uses the model service frozen onto each attempt at claim time, so an
        uncertain attempt or an unconfirmed result keeps its model service slot exactly
        like its machine-wide slot.
        """
        rows = connection.execute(
            f"SELECT model_harness, model_provider, model_model, COUNT(*) AS active FROM attempts"
            f" WHERE ({self.UNRESOLVED_SQL}) AND model_model IS NOT NULL"
            " GROUP BY model_harness, model_provider, model_model"
        ).fetchall()
        return {
            (row["model_harness"], row["model_provider"], row["model_model"]): int(row["active"])
            for row in rows
        }

    def model_limits(self, connection: sqlite3.Connection) -> dict[tuple[str, str, str], int]:
        """Explicit per-model-service limits from the user-owned ``model_service_concurrency`` table."""
        rows = connection.execute("SELECT harness, provider, model, concurrency_limit FROM model_service_concurrency").fetchall()
        return {
            (row["harness"], row["provider"], row["model"]): scheduling.clamp_model_limit(row["concurrency_limit"])
            for row in rows
        }

    def _effective_model_limit(
        self, connection: sqlite3.Connection, model_service: tuple[str, str, str], *, limits: dict | None = None
    ) -> int:
        if limits is None:
            limits = self.model_limits(connection)
        return limits.get(model_service, scheduling.MODEL_LIMIT_DEFAULT)

    def model_capacity_rows(
        self, connection: sqlite3.Connection, model_services: Iterable[tuple[str, str, str]] | None = None
    ) -> list[dict]:
        """Observation rows ``{harness, provider, model, limit, active}`` for capacity views.

        ``model services`` restricts the rows to the model services represented in one response
        (a console snapshot page or a model-buddy page). Without it, the rows cover
        every model service relevant to capacity right now: explicit settings, model services of
        unresolved attempts and model services of queued work.
        """
        limits = self.model_limits(connection)
        active = self._model_active_counts(connection)
        if model_services is None:
            model_services = set(limits) | set(active) | set(self._queued_model_services(connection))
        rows = []
        for model_service in sorted(set(model_services)):
            harness, provider, model = model_service
            rows.append(
                {
                    "harness": harness,
                    "provider": provider,
                    "model": model,
                    "limit": limits.get(model_service, scheduling.MODEL_LIMIT_DEFAULT),
                    "active": active.get(model_service, 0),
                }
            )
        return rows

    def _queued_model_services(self, connection: sqlite3.Connection) -> set[tuple[str, str, str]]:
        rows = connection.execute(
            "SELECT DISTINCT t.adapter AS harness,"
            f" {self._QUEUED_MODEL_SERVICE_PROVIDER} AS provider, {self._QUEUED_MODEL_SERVICE_MODEL} AS model"
            " FROM tasks t LEFT JOIN workflow_runs r ON r.run_id = t.task_id WHERE t.state='queued'"
        ).fetchall()
        model_services = {
            (row["harness"], row["provider"], row["model"])
            for row in rows
            if row["provider"] and row["model"] and row["harness"] in schemas.CODING_HARNESSES
        }
        for row in connection.execute("SELECT r.input_json FROM decision_requests r JOIN tasks t ON t.task_id=r.task_id WHERE t.state='queued'"):
            buddy = (json.loads(row["input_json"]) if row["input_json"] else {}).get("buddy") or {}
            if all(buddy.get(key) for key in ("harness", "provider", "model")):
                model_services.add(tuple(buddy[key] for key in ("harness", "provider", "model")))
        return model_services

    def _full_model_services(self, connection: sqlite3.Connection) -> set[tuple[str, str, str]]:
        """Model services whose unresolved attempts already hold their effective limit."""
        active = self._model_active_counts(connection)
        if not active:
            return set()
        limits = self.model_limits(connection)
        return {
            model_service
            for model_service, count in active.items()
            if count >= limits.get(model_service, scheduling.MODEL_LIMIT_DEFAULT)
        }

    def capacity_report(self) -> dict:
        """The machine-wide ceiling and the per-model-service observation rows.

        ``totalLimit`` is the aggregate execution limit the daemon must reserve
        execution slots for; it is deliberately separate from the WAIT admission counts.
        ``models`` observes the model services relevant to pending work, active attempts
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

    def _held_claims(self, connection: sqlite3.Connection) -> list[sqlite3.Row]:
        return connection.execute(
            "SELECT * FROM resource_claims WHERE state IN ('held','retained')"
        ).fetchall()

    def _admission_blocker(self, connection: sqlite3.Connection, spec: dict, *, exclude_task: str | None = None) -> str | None:
        """Why this specification cannot start right now, or ``None`` when it can.

        The machine-wide ceiling is checked first, then the specification's own model
        model service, so the queue reason names the limit that is actually full. Workspace
        and exclusive-resource admission applies to all work unchanged.
        """
        if (self.directory / 'upgrade.json').exists():
            raise BoardError('UPGRADE_IN_PROGRESS', 'Admission is fenced until the coordinated upgrade finishes')
        if self._total_active(connection) >= self.max_concurrent:
            return scheduling.REASON_TOTAL_CAPACITY
        model_service = scheduling.model_service(spec)
        if model_service is not None:
            active = self._model_active_counts(connection)
            if active.get(model_service, 0) >= self._effective_model_limit(connection, model_service):
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

    def _model_service_admission_blocker(self, connection: sqlite3.Connection, model_service: tuple[str, str, str]) -> str | None:
        """The capacity blocker for one already-resolved model service (no spec needed).

        Used where the model service is resolved inside the claim transaction itself — the
        fixed decision buddy of a routing task — so its quota is checked *before*
        the claim commits and before any selection reader is admitted.
        """
        if self._total_active(connection) >= self.max_concurrent:
            return scheduling.REASON_TOTAL_CAPACITY
        active = self._model_active_counts(connection)
        if active.get(model_service, 0) >= self._effective_model_limit(connection, model_service):
            return scheduling.REASON_MODEL_CAPACITY
        return None

    def _claim_candidates(
        self, connection: sqlite3.Connection, *, after: dict | None = None, through_rowid: int | None = None
    ) -> tuple[list[sqlite3.Row], set[tuple[str, str, str]]]:
        """Bounded queued candidates in arrival order, skipping full model services.

        Tasks of a model service already at its concurrent-attempt limit are excluded by the
        SQL predicate *before* the candidate LIMIT, so >100 queued tasks blocked on
        one model service cannot hide a runnable task of another model service behind the bounded
        window. Routing tasks are scanned through their own bounded window: a
        decision task's model service is the mutable fixed selector resolved at claim time,
        so it cannot be pre-filtered, and a backlog of routing work blocked on the
        selector's model service must not hide runnable business work either. The full
        model services are returned so an empty claim can name the limit that is holding
        work instead of pretending the board is idle.
        """
        full = self._full_model_services(connection)
        model_service_clauses = []
        model_service_values: list[object] = []
        for harness, provider, model in sorted(full):
            # Null-safe on purpose: a task without a complete model service triple
            # (command work, an unresolved run, a routing task) is never
            # excluded — SQL's NOT over a NULL conjunction would otherwise drop it.
            model_service_clauses.append(
                f"({self._QUEUED_MODEL_SERVICE_PROVIDER} IS NULL OR {self._QUEUED_MODEL_SERVICE_MODEL} IS NULL"
                f" OR NOT (t.adapter=? AND {self._QUEUED_MODEL_SERVICE_PROVIDER}=? AND {self._QUEUED_MODEL_SERVICE_MODEL}=?))"
            )
            model_service_values.extend((harness, provider, model))
        rows: list[sqlite3.Row] = []
        for class_predicate in ("t.adapter IS NOT ?", "t.adapter IS ?"):
            cursor = (after or {}).get(class_predicate)
            rows.extend(
                connection.execute(
                    "SELECT t.* FROM tasks t LEFT JOIN workflow_runs r ON r.run_id=t.task_id"
                    f" WHERE t.state='queued' AND {class_predicate}"
                    + ((" AND " + " AND ".join(model_service_clauses)) if model_service_clauses else "")
                    + (" AND t.rowid<=?" if through_rowid is not None else "")
                    + (" AND (t.created_at,t.task_id)>(?,?)" if cursor is not None else "")
                    + " ORDER BY t.created_at, t.task_id LIMIT ?",
                    (scheduling.DECISION_ADAPTER, *model_service_values,
                     *((through_rowid,) if through_rowid is not None else ()),
                     *(cursor if cursor is not None else ()), self.CLAIM_CANDIDATE_LIMIT),
                ).fetchall()
            )
        rows.sort(key=lambda row: (row["created_at"], row["task_id"]))
        return rows, full

    def _remaining_candidates(self, connection, first, through_rowid):
        """Scan later windows when early queued work is blocked on resources."""
        rows = first
        cursors = {}
        while rows:
            for task in rows:
                key = "t.adapter IS ?" if task["adapter"] == scheduling.DECISION_ADAPTER else "t.adapter IS NOT ?"
                cursors[key] = (task["created_at"], task["task_id"])
                yield task
            rows, _full = self._claim_candidates(connection, after=cursors, through_rowid=through_rowid)

    def _retain_claims(self, connection: sqlite3.Connection, attempt_id: str) -> None:
        connection.execute(
            "UPDATE resource_claims SET state='retained' WHERE attempt_id = ? AND state='held'", (attempt_id,)
        )

    def _release_claims(self, connection: sqlite3.Connection, attempt_id: str) -> None:
        connection.execute(
            "UPDATE resource_claims SET state='released', released_at=? WHERE attempt_id = ? AND state IN ('held','retained')",
            (self.now(), attempt_id),
        )

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
        micro_task: dict | None = None,
    ) -> dict:
        """Admit one ordinary task.

        ``micro_task`` is the coordinator's hook: when present, the workflow run and its
        workspace reservation commit in the same transaction as the task row, so a
        micro-task task can never exist without its governance record.
        """
        spec = micro_task["spec"] if micro_task is not None else schemas.normalize_spec(params)
        if micro_task is None and spec["adapter"] in schemas.CODING_HARNESSES:
            raise BoardError("MICRO_TASK_REQUIRED", "Coding work requires submit (workflow_submit) so routing, ownership and turn evidence remain micro-task")
        request_id = schemas.required_string(params, "requestId", max_length=schemas.MAX_REQUEST_ID)
        owner = None if micro_task is not None else schemas.optional_string(params, "owner") or "cli"
        fingerprint = schemas.spec_fingerprint(spec)
        request = {"requestId": request_id, "fingerprint": fingerprint}

        with self.db.write() as connection:
            existing = connection.execute("SELECT * FROM tasks WHERE request_id = ?", (request_id,)).fetchone()
            run = self.workflow._run_optional(connection, existing["task_id"]) if existing is not None else None
            if run is not None:
                self.workflow._require_replayable_submission(connection, run, request_id)
            if command_id:
                receipt = self._receipt(connection, command_id, "task.submit", request)
                if receipt is not None:
                    return receipt
            if existing is not None:
                if micro_task is not None:
                    if run is None or run["request_fingerprint"] != micro_task["requestFingerprint"]:
                        raise BoardError("CONFLICT", "requestId already belongs to a different micro-task request")
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
                    " fingerprint_version, adapter, required_capabilities, cwd, exclusive_resources, expected_seconds,"
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
                        spec.get("harness") or spec.get("adapter") or "unresolved",
                        canonical_json(spec.get("requiredCapabilities", [])),
                        spec["cwd"],
                        canonical_json(spec.get("exclusiveResources", [])),
                        spec["expectedSeconds"],
                        "queued",
                        blocker or "awaiting-execution",
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
                        "adapter": spec.get("harness") or spec.get("adapter"),
                        "cwd": spec["cwd"],
                        "queueReason": blocker or "awaiting-execution",
                        "inputFingerprint": fingerprint,
                        **({"microTask": {"sourceHostId": micro_task["hostId"]},
                            "actor": ({"role": "lead", "id": micro_task["lead"]["runId"]}
                                      if micro_task.get("lead") else {"role": "host", "id": micro_task["hostId"]})}
                           if micro_task is not None else {}),
                    },
                )
                row = connection.execute("SELECT * FROM tasks WHERE task_id = ?", (task_id,)).fetchone()
                if micro_task is not None:
                    self.workflow.attach_micro_task(connection, row, now=now, **micro_task)
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
            from ..tasks.fixes import view as fix_view
            view = {**self._decorate(connection, row), **fix_view(connection, row["task_id"])}
            if "workflow" in view:
                view["resumeRecovery"] = self.workflow._resume_recovery(connection, row["task_id"])
            return {"task": view}

    def task_list(self, params: dict) -> dict:
        """Bounded task history with explicit filters and stable keyset paging.

        Every filter is one SQL predicate over the read-only micro task relation
        (:mod:`hey_my_buddy.blackboard.tasks.micro_task`), so it applies *before* LIMIT and ``total`` counts
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
        before_value = schemas.optional_string(params, "before", max_length=micro_task.CURSOR_MAX_LENGTH)
        before = micro_task.decode_cursor(before_value) if before_value is not None else None
        if before is not None and offset:
            raise BoardError(
                "INVALID_ARGUMENT",
                "before and a nonzero offset are mutually exclusive; use one paging mode",
            )
        roots_only = schemas.optional_bool(params, "rootsOnly", False)
        query = schemas.optional_string(params, "query", max_length=micro_task.QUERY_MAX_LENGTH)
        project_id = schemas.optional_string(params, "projectId", max_length=micro_task.PROJECT_ID_MAX_LENGTH)
        host_id = schemas.optional_string(params, "hostId", max_length=micro_task.HOST_ID_MAX_LENGTH)
        filter_name = schemas.optional_string(params, "filter", max_length=16) or "all"
        if filter_name not in micro_task.HISTORY_FILTERS:
            raise BoardError("INVALID_ARGUMENT", "filter must be one of: all, active, host, review")
        clauses, values = [], []
        from ..tasks.workflow import AGENT_KIND, current_scope
        scope = current_scope()
        if scope is not None and scope["kind"] == AGENT_KIND:
            clauses.append("EXISTS (SELECT 1 FROM workflow_lead_tasks owned WHERE owned.owned_run_id=task_id AND owned.lead_run_id=?)")
            values.append(scope["runId"])
        if state:
            clauses.append("state = ?")
            values.append(state)
        if adapter:
            clauses.append("adapter = ?")
            values.append(adapter)
        history_clauses, history_values = micro_task.history_where(
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
                f"SELECT COUNT(*) AS count FROM ({micro_task.TASK_HISTORY_SQL}) AS history {where}", values
            ).fetchone()["count"]
            page_clauses, parameters = list(clauses), list(values)
            if before is not None:
                keyset, keyset_values = micro_task.keyset_where(*before)
                page_clauses.append(keyset)
                parameters.extend(keyset_values)
            page_where = f"WHERE {' AND '.join(page_clauses)}" if page_clauses else ""
            statement = f"{micro_task.TASK_HISTORY_SQL} {page_where} ORDER BY created_at DESC, task_id DESC LIMIT ?"
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
                self._decorate(connection, row, lineage_result=micro_task.metadata_from_row(row)) for row in page
            ]
            next_cursor = (
                micro_task.encode_cursor(page[-1]["created_at"], page[-1]["task_id"]) if page and has_more else None
            )
            head = self._head_of(connection)
        return {"runs": tasks, "total": int(total), "cursor": head, "nextCursor": next_cursor}

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
            # Only the current execution receipt is unpacked. Its adapter result is
            # opaque data, even when it contains fields named result or turn.
            view["result"] = payload["result"]
            view["resultMeta"] = {key: value for key, value in payload.items() if key != "result"}
            view["attemptId"] = attempt["attempt_id"]
            view["resultDelivered"] = view["result"] is not None
        return view

    def task_cancel(self, params: dict, *, command_id: str | None = None, initiator: str = "unknown") -> dict:
        schemas.reject_unknown(
            params,
            {
                "runId",
                "taskId",
                "requestId",
                "note",
                "requestedBy",
                *schemas.CONTROL_FIELDS,
                schemas.CONSOLE_AUTHORITY_FIELD,
            },
            "task.cancel",
        )
        schemas.reject_untrusted_override(params)
        note = (schemas.bounded_text(params, "note", max_bytes=schemas.MAX_NOTE_BYTES)[0]
                if params.get("note") is not None else None)
        actor = {"role": "service" if initiator == "service" else "host",
                 "id": schemas.optional_string(params, "requestedBy") or "cli"}
        request = {"selector": {k: params[k] for k in ("runId", "taskId", "requestId") if k in params}, "note": note}
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
                        payload={"note": note, "actor": actor, "phase": "queued"},
                    )
                else:
                    if task["state"] not in ("cancelling",):
                        self._transition_task(connection, task, "cancelling")
                    if attempt is not None and attempt["cancel_requested_at"] is None:
                        self.workflow._request_attempt_stop(connection, task["task_id"], now, initiator=initiator,
                                                            wrap_up=False)
                    self._append_event(
                        connection,
                        "task.cancel_requested",
                        task_id=task["task_id"],
                        attempt_id=attempt["attempt_id"] if attempt else None,
                        revision=task["revision"] + 1,
                        payload={"note": note, "actor": actor},
                    )
                task = connection.execute("SELECT * FROM tasks WHERE task_id = ?", (task["task_id"],)).fetchone()
                # A decision run is an ordinary cancellable task. Cancelling it marks
                # the decision terminal in the same transaction, so a queued selection
                # that is cancelled can never become a current recommendation.
                self.decisions.cancelled(connection, task=task, reason=make_reason("decision-cancelled"), now=now)
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
        execution does not start anything by itself.

        An attempt whose shutdown is unconfirmed keeps its resource claims and
        refuses to be retried. There is deliberately **no operator override**: a
        written note is not evidence of termination. Recovery releases the claim
        only after verified process shutdown, or proof that no process was created.
        """
        schemas.reject_unknown(
            params,
            {
                "runId",
                "taskId",
                "requestId",
                "note",
                "requestedBy",
                *schemas.CONTROL_FIELDS,
                schemas.CONSOLE_AUTHORITY_FIELD,
            },
            "task.retry",
        )
        schemas.reject_untrusted_override(params)
        note = (schemas.bounded_text(params, "note", max_bytes=schemas.MAX_NOTE_BYTES)[0]
                if params.get("note") is not None else None)
        request = {
            "selector": {k: params[k] for k in ("runId", "taskId", "requestId") if k in params},
            "note": note,
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
                    "observed outcome; a written note is not evidence of termination.",
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
                " accepted_at=NULL, acceptance_note=NULL, acceptance_verdict=NULL,"
                " review_rejected_at=NULL, review_rejection_note=NULL WHERE task_id = ?",
                (blocker or "awaiting-execution", task["task_id"]),
            )
            if task["accepted_at"] or task["review_rejected_at"]:
                # The review belongs to the attempt that was reviewed; it is kept as
                # history in the event stream, never inherited by the new attempt.
                review = (
                    {"acceptedAt": task["accepted_at"], "verdict": "accepted", "note": task["acceptance_note"]}
                    if task["accepted_at"] else
                    {"reviewRejectedAt": task["review_rejected_at"], "verdict": "rejected",
                     "note": task["review_rejection_note"]}
                )
                self._append_event(
                    connection,
                    "task.review_archived",
                    task_id=task["task_id"],
                    attempt_id=attempt["attempt_id"] if attempt else None,
                    revision=task["revision"] + 1,
                    payload=review,
                )
            self._append_event(
                connection,
                "task.retried",
                task_id=task["task_id"],
                attempt_id=attempt["attempt_id"] if attempt else None,
                revision=task["revision"] + 1,
                payload={"note": note, "previousAttemptId": attempt["attempt_id"] if attempt else None},
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
        actor = {"role": "host", "id": schemas.optional_string(params, "acknowledgedBy") or "cli"}
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
            if task["accepted_at"] or task["review_rejected_at"]:
                recorded_verdict = "accepted" if task["accepted_at"] else "rejected"
                recorded_note = task["acceptance_note"] if task["accepted_at"] else task["review_rejection_note"]
                if recorded_note != note or recorded_verdict != verdict:
                    raise BoardError(
                        "CONFLICT",
                        "This run already has a different recorded review; a reviewed outcome is not relabelled",
                        acceptedAt=task["accepted_at"],
                        reviewRejectedAt=task["review_rejected_at"],
                        verdict=recorded_verdict,
                    )
                response = {"task": self._decorate(connection, task), "duplicate": True}
            else:
                now = self.now()
                if verdict == "accepted":
                    connection.execute(
                        "UPDATE tasks SET accepted_at=?, acceptance_note=?, acceptance_verdict='accepted',"
                        " updated_at=?, revision=revision+1 WHERE task_id = ?",
                        (now, note, now, task["task_id"]),
                    )
                else:
                    connection.execute(
                        "UPDATE tasks SET review_rejected_at=?, review_rejection_note=?,"
                        " updated_at=?, revision=revision+1 WHERE task_id = ?",
                        (now, note, now, task["task_id"]),
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
        if state == "finished" and attempt["execution_state"] != "finished":
            queued_questions = connection.execute(
                "SELECT message_id,inquiry_id FROM messages WHERE attempt_id=? AND state='queued'",
                (attempt["attempt_id"],),
            ).fetchall()
            if queued_questions:
                task = connection.execute("SELECT revision FROM tasks WHERE task_id=?", (attempt["task_id"],)).fetchone()
                for question in queued_questions:
                    connection.execute(
                        "UPDATE messages SET state='unavailable',reason_code=?,reason_params_json='{}',updated_at=?,revision=revision+1 WHERE message_id=?",
                        ("inquiry-queued-at-attempt-end", now, question["message_id"]),
                    )
                    self._append_event(connection, "message.updated", task_id=attempt["task_id"],
                        attempt_id=attempt["attempt_id"], revision=task["revision"],
                        payload={"inquiryId": question["inquiry_id"], "state": "unavailable",
                                 "actor": {"role": "service", "id": "attempt-finished"}})

    # -- service admission ---------------------------------------------------
    def admit_execution(self, nonce: str, task_id: str | None = None) -> dict:
        """Prepare dispatch outside SQLite, then admit one execution atomically."""
        params = {"taskId": task_id} if task_id is not None else {}
        self.workflow.prepare_dispatch(params)
        from ..tasks.workflow import ClaimStashCoverageRetry
        retry_selector = task_id
        for coverage_round in range(2):
            stash_facts = self.workflow.collect_claim_stash_facts(params, selector=retry_selector)
            try:
                return self._admit_in_transaction(
                    nonce=nonce, explicit_task=task_id, stash_facts=stash_facts,
                    restrict_to=retry_selector if coverage_round else None,
                )
            except ClaimStashCoverageRetry as retry:
                retry_selector = retry.task_id
        raise BoardError("CONFLICT", "Admission could not collect its chosen workspace's shared-stash observation")

    def _set_queue_reason(self, connection, task, reason: str) -> None:
        if task["queue_reason"] != reason:
            connection.execute("UPDATE tasks SET queue_reason=?,updated_at=? WHERE task_id=?",
                               (reason, self.now(), task["task_id"]))

    def _admit_in_transaction(
        self, *, nonce: str, explicit_task: str | None, stash_facts: dict,
        restrict_to: str | None = None,
    ) -> dict:
        """The claim's one write transaction; a stash-coverage miss rolls it back.

        Everything admitted here — attempt, resources and turn —
        commits together or not at all, exactly as before; the only new exits
        are the coverage fence below, which fires before anything is written on
        every pass, and ``restrict_to``: the bounded retry transaction admits
        only the one run it re-collected for, so a concurrent change can never
        make it start a different, unobserved run — or the same run whose
        targeted pass found it held and observed nothing.
        """
        from ..tasks.workflow import ClaimStashCoverageRetry
        from ...protocol.capabilities import adapter_capabilities

        with self.db.write() as connection:
            if self._total_active(connection) >= self.max_concurrent:
                return {"claim": None, "reason": scheduling.REASON_TOTAL_CAPACITY}
            full_model_services: set[tuple[str, str, str]] = set()
            if explicit_task:
                candidates = [
                    connection.execute("SELECT * FROM tasks WHERE task_id=?", (explicit_task,)).fetchone()
                ]
                # An explicit target is examined by ``_admission_blocker`` and the
                # decision model service check below; no candidate pre-filter applies to it.
            elif restrict_to is not None:
                # The coverage retry's own admission boundary: only the run whose
                # repository the retry pass just collected may start here. If that
                # run was legally taken away in between, this transaction observes
                # no admissible work and leaves everything to the next claim.
                candidates = [
                    connection.execute("SELECT * FROM tasks WHERE task_id=?", (restrict_to,)).fetchone()
                ]
            else:
                # Router preparation may enqueue a new dispatch in this same
                # transaction. Only work present at this scan's start belongs to
                # its candidate windows; the next admission sees the new dispatch.
                through_rowid = connection.execute("SELECT COALESCE(MAX(rowid),0) FROM tasks").fetchone()[0]
                candidates, full_model_services = self._claim_candidates(connection, through_rowid=through_rowid)
            had_candidates = bool(candidates)
            if not explicit_task and restrict_to is None:
                candidates = self._remaining_candidates(connection, candidates, through_rowid)
            chosen: sqlite3.Row | None = None
            chosen_generation = 0
            chosen_attempt_id: str | None = None
            chosen_input: dict | None = None
            chosen_model_service: tuple[str, str, str] | None = None
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
                    self._set_queue_reason(connection, task, workflow_reason)
                    continue
                supported = set(adapter_capabilities(task["adapter"]))
                if task["adapter"] in schemas.CODING_HARNESSES:
                    supported.update((f"execution:{task['adapter']}", f"effort:{spec['effort']}"))
                if not set(json.loads(task["required_capabilities"])).issubset(supported):
                    blocker = "capability-mismatch"
                    self._set_queue_reason(connection, task, blocker)
                    continue
                # A micro-task worktree runs in its allocated checkout, so its admission
                # and claims use tasks.cwd (the effective path), never the source cwd
                # recorded in the immutable spec_json.
                effective_spec = {**spec, "cwd": task["cwd"]} if task["cwd"] != spec["cwd"] else spec
                reason = self._admission_blocker(connection, effective_spec, exclude_task=task["task_id"])
                if reason is not None:
                    blocker = reason
                    self._set_queue_reason(connection, task, reason)
                    continue
                generation_row = connection.execute(
                    "SELECT COALESCE(MAX(generation), 0) AS generation FROM attempts WHERE task_id=?",
                    (task["task_id"],),
                ).fetchone()
                attempt_id = str(uuid.uuid4())
                generation = int(generation_row["generation"]) + 1
                # Pre-claim Router preparation runs before the selector model service and
                # its capacity check: a superseded, circumstances-changed or
                # before-model-unavailable dispatch is settled or advanced here, so
                # the model service below only ever freezes the current dispatch's tuple.
                # A returned reason keeps the task queued (or records its honest
                # closure) and this claim moves on to the next candidate.
                if task["adapter"] == scheduling.DECISION_ADAPTER:
                    preclaim = self.decisions.prepare_router_claim(connection, task=task, now=self.now())
                    if preclaim is not None:
                        blocker = preclaim
                        still_queued = connection.execute(
                            "SELECT state FROM tasks WHERE task_id=?", (task["task_id"],)
                        ).fetchone()["state"] == "queued"
                        if still_queued and preclaim != "decision-closed":
                            self._set_queue_reason(connection, task, preclaim)
                        continue
                # The model service this attempt is frozen to. A routing task has no
                # model service of its own in its specification: the current dispatch's
                # Router resolves one here, its quota is checked before the claim
                # commits, and the same tuple is frozen onto the attempt. Business
                # work uses the effective specification's model service, which a micro-task
                # run has already resolved and validated.
                model_service = scheduling.model_service(effective_spec)
                if task["adapter"] == scheduling.DECISION_ADAPTER:
                    model_service = self.decisions.selector_model_service(connection, spec)
                    if model_service is not None:
                        model_service_reason = self._model_service_admission_blocker(connection, model_service)
                        if model_service_reason is not None:
                            blocker = model_service_reason
                            self._set_queue_reason(connection, task, model_service_reason)
                            continue
                # A decision task is admitted *here*, inside the same transaction that
                # claims it: the selection reader is
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
                            self._set_queue_reason(connection, task, blocked)
                        continue
                chosen = task
                chosen_generation = generation
                chosen_attempt_id = attempt_id
                chosen_input = claim_input
                chosen_model_service = model_service
                break
            if chosen is None:
                if not had_candidates:
                    # Nothing was even scannable. Name the limit that is holding work
                    # instead of reporting that the board is idle.
                    if self._total_active(connection) >= self.max_concurrent:
                        blocker = scheduling.REASON_TOTAL_CAPACITY
                    elif full_model_services:
                        blocker = scheduling.REASON_MODEL_CAPACITY
                    else:
                        blocker = "no-queued-work"
                head = self._head_of(connection)
                response = {"claim": None, "reason": blocker}
                self._notify(head)
                return response
            # Coverage fence before anything is admitted, on every pass: the
            # chosen run's repository must be a key this claim's own
            # pre-transaction pass observed — the run's identity alone proves
            # nothing, because the targeted pass can legitimately find the run
            # not queued (another worker holds it) and observe no repository for
            # it at all. A miss rolls the whole transaction back for targeted
            # re-collection. Exhausting the bounded retry reports a conflict,
            # never an idle snapshot: another claim must collect fresh facts.
            # Nothing is admitted and no receipt is stored on a miss; a
            # repository whose reading genuinely failed is
            # present as a key and may start with its honest ``unknown``.
            repository = self.workflow.run_repository_path(connection, chosen["task_id"])
            if repository is not None and not (
                isinstance(stash_facts, dict)
                and repository in (stash_facts.get("repositories") or {})
            ):
                raise ClaimStashCoverageRetry(chosen["task_id"])
            spec = self.workflow.effective_spec(connection, chosen)
            generation = chosen_generation
            attempt_id = chosen_attempt_id or str(uuid.uuid4())
            now = self.now()
            connection.execute(
                "INSERT INTO attempts(attempt_id, task_id, generation, nonce_verifier,"
                " execution_state, ownership, adapter, model_harness, model_provider, model_model,"
                " started_at, created_at, updated_at, revision)"
                " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,1)",
                (
                    attempt_id,
                    chosen["task_id"],
                    generation,
                    self.db.nonce_verifier(nonce),
                    "starting",
                    "owned",
                    chosen["adapter"],
                    # Frozen model service identity: written by this claim transaction and
                    # never updated afterwards, so historical and shutdown-uncertain
                    # attempts cannot change which model service slot they occupy.
                    *(chosen_model_service if chosen_model_service is not None else (None, None, None)),
                    now,
                    now,
                    now,
                ),
            )
            connection.execute("UPDATE attempts SET cleanup_pending=1 WHERE attempt_id=?", (attempt_id,))
            effective_cwd = chosen["cwd"] or spec["cwd"]
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
            self._append_event(
                connection,
                "execution.admitted",
                task_id=chosen["task_id"],
                attempt_id=attempt_id,
                revision=chosen["revision"] + 1,
                payload={"generation": generation, "adapter": chosen["adapter"]},
            )
            task_row = connection.execute("SELECT * FROM tasks WHERE task_id=?", (chosen["task_id"],)).fetchone()
            attempt_row = connection.execute("SELECT * FROM attempts WHERE attempt_id=?", (attempt_id,)).fetchone()
            # Execution inputs start from mechanical facts, never the decorated read view.
            response = {
                "claim": {
                    "attempt": self._attempt_view(attempt_row, include_result=False),
                    "task": self._task_view(task_row, execution_spec=spec),
                },
                "reason": None,
            }
            if chosen_input is not None:
                # The bounded decision payload the service persisted for this attempt.
                # It travels only to the claiming worker and is written to the private
                # attempt directory by the decision adapter; adapters never see the
                # database, and no credential is part of this document.
                response["claim"]["decisionInput"] = chosen_input
            turn_claim = self.workflow.begin_turn(
                connection, task=chosen, attempt_id=attempt_id, generation=generation, spec=spec, now=now,
                stash_facts=stash_facts,
            )
            if turn_claim is not None:
                # The service-owned turn identity and bounded context travel to the
                # execution process, which receives them in its private startup material. The
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
            head = self._head_of(connection)
        self._notify(head)
        return response

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

    def _verify_attempt_actor(self, connection: sqlite3.Connection, params: dict) -> sqlite3.Row:
        """Authenticate one execution by its generation and nonce."""
        attempt_id = schemas.required_string(params, "attemptId", max_length=128)
        attempt = self._attempt_row(connection, attempt_id)
        generation = schemas.optional_int(params, "generation", None, 0, 2**63-1)
        if generation != attempt["generation"]:
            raise BoardError(
                "STALE_GENERATION",
                "This attempt was replaced by a newer generation; the older execution may not mutate state",
                attemptId=attempt_id,
                currentGeneration=attempt["generation"],
                providedGeneration=generation,
            )
        nonce = schemas.required_string(params, "nonce", max_length=256)
        if not _constant_time_equal(self.db.nonce_verifier(nonce), attempt["nonce_verifier"]):
            raise BoardError("UNAUTHORIZED", "Invalid attempt capability", attemptId=attempt_id)
        # Identity and generation are not enough: a released-then-retried task has a
        # newer attempt, and the superseded execution must not mutate the task even
        # though its nonce still verifies against its own immutable attempt row.
        self._require_effective_attempt(connection, attempt)
        if connection.execute("SELECT 1 FROM attempt_interruptions WHERE attempt_id=? AND state='sealing'",
                              (attempt_id,)).fetchone() is not None:
            raise BoardError('ATTEMPT_FINISHED', 'Proven stop has fenced this attempt for interruption settlement',
                             attemptId=attempt_id)
        return attempt

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
            "This attempt was superseded by a newer generation; the older execution may not mutate task state",
            attemptId=attempt["attempt_id"],
            taskId=attempt["task_id"],
            effectiveAttemptId=current,
        )

    def execution_progress(self, params: dict, *, verify_identity=None) -> dict:
        schemas.reject_unknown(
            params, {"attemptId", "generation", "nonce", "message", "phase", "data", "processIdentity"}, "execution.progress"
        )
        message = schemas.optional_string(params, "message")
        phase = schemas.optional_string(params, "phase")
        process_identity = params.get("processIdentity")
        if process_identity is not None:
            from ...protocol.process_identity import validate_process_identity
            validate_process_identity(process_identity)
        # ``data`` carries bounded native activity and the control waiting reason;
        # unknown keys cannot smuggle tool arguments into either projection.
        # Activity is stored only when
        # a caller actually publishes one - a routine heartbeat is never
        # turned into fake native progress.
        activity = None
        waiting_reason = None
        data = params.get("data")
        if data is not None:
            if not isinstance(data, dict):
                raise BoardError("INVALID_ARGUMENT", "data must be an object")
            schemas.reject_unknown(data, {"activity", "waitingReason"}, "execution.progress.data")
            if data.get("activity") is not None:
                activity = activity_module.normalize_activity(data["activity"])
                if 'waitingReason' in activity:
                    validate_reason(activity['waitingReason'], 'activity.waitingReason')
            if data.get('waitingReason') is not None:
                waiting_reason = validate_reason(data['waitingReason'], 'data.waitingReason')
                if waiting_reason['code'] != 'router-input-unknown':
                    raise BoardError('INVALID_ARGUMENT', 'data.waitingReason must be router-input-unknown',
                                     field='data.waitingReason.code')
        with self.db.write() as connection:
            attempt = self._verify_attempt_actor(connection, params)
            if attempt["execution_state"] == "finished":
                raise BoardError("ATTEMPT_FINISHED", "This attempt is already finished; progress is not accepted")
            if process_identity is not None:
                if verify_identity is not None and not verify_identity(attempt["attempt_id"], process_identity):
                    raise BoardError("UNAUTHORIZED", "Execution identity differs from the service-owned process")
                self._record_process_identity(connection, attempt["attempt_id"], process_identity)
                connection.execute("UPDATE attempts SET identity_confirmed_at=COALESCE(identity_confirmed_at,?)"
                                   " WHERE attempt_id=?", (self.now(), attempt["attempt_id"]))
            if phase in ("starting", "executing", "finalizing") and attempt["execution_state"] != "uncertain":
                self._transition_attempt(connection, attempt, phase)
            now = self.now()
            if data is not None and 'waitingReason' in data:
                waiting_code = waiting_reason['code'] if waiting_reason is not None else None
                current = connection.execute('SELECT queue_reason FROM tasks WHERE task_id=?',
                                             (attempt['task_id'],)).fetchone()['queue_reason']
                if current != waiting_code:
                    connection.execute('UPDATE tasks SET queue_reason=? WHERE task_id=?',
                                       (waiting_code, attempt['task_id']))
                    self._append_event(connection, 'attempt.waiting', task_id=attempt['task_id'],
                                       attempt_id=attempt['attempt_id'], payload={'reason': waiting_reason})
            connection.execute(
                "UPDATE attempts SET updated_at=?, revision=revision+1 WHERE attempt_id=?",
                (now, attempt["attempt_id"]),
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

    def execution_result(self, params: dict, *, recovered: bool = False, stop_evidence: dict | None = None) -> dict:
        """Commit result, artifacts, task/attempt state and the completion event together.

        Artifact registration validates the claimed file before any completed result
        is published. A failed commit publishes no success event.
        """
        schemas.reject_unknown(
            params,
            {
                "attemptId",
                "generation",
                "nonce",
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
            "execution.result",
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
        # A supplied termination classification must be one of the declared reasons: a completion that beat a cancellation is
        # recorded as completed rather than re-labelled by the losing race.
        termination_reason = schemas.optional_string(params, "terminationReason", max_length=32)
        if termination_reason is not None and termination_reason not in TERMINATION_REASONS:
            raise BoardError(
                "INVALID_ARGUMENT",
                f"terminationReason must be one of {', '.join(TERMINATION_REASONS)}",
                field="terminationReason",
            )
        if termination_reason == "interrupted" and not recovered:
            raise BoardError("INVALID_ARGUMENT", "Only service recovery can record an interrupted result",
                             field="terminationReason")
        stop_reasons = {"wrap-up", "grace-expired", "forced-stop", "console-stop", "service-stop", "unknown-stop"}
        if termination_reason in stop_reasons and not shutdown_confirmed:
            raise BoardError("INVALID_ARGUMENT", "A stop termination reason requires confirmed shutdown",
                             field="terminationReason")
        if termination_reason == "wrap-up" and status != "cancelled":
            raise BoardError("INVALID_ARGUMENT", "wrap-up requires a cancelled result",
                             field="terminationReason")
        error_text = schemas.optional_string(params, "error", max_length=4000)
        attempt_id_param = schemas.required_string(params, "attemptId", max_length=128)
        log_paths = params.get("logPaths")
        if log_paths is not None and not isinstance(log_paths, dict):
            raise BoardError("INVALID_ARGUMENT", "logPaths must be an object when provided")
        elapsed = params.get("elapsedSeconds")
        if elapsed is not None and (isinstance(elapsed, bool) or not isinstance(elapsed, (int, float))):
            raise BoardError("INVALID_ARGUMENT", "elapsedSeconds must be a number when provided")
        # Hashing a large artifact inside BEGIN IMMEDIATE would block progress, cancel
        # and other result writers for its whole duration, so the files are
        # validated and hashed first, outside any transaction.
        verified_artifacts = self._verify_artifacts(artifacts)
        # A micro-task turn's sealed output manifest is verified outside the transaction
        # too; a manifest that cannot be verified fails the attempt honestly instead
        # of publishing an unverified success.
        try:
            self.workflow.precheck_result(params)
        except BoardError as error:
            status = "cancelled" if termination_reason == "wrap-up" and shutdown_confirmed else "failed"
            if termination_reason == "wrap-up":
                termination_reason = "forced-stop" if shutdown_confirmed else None
            error_text = f"the micro-task result could not be imported: {error.message}"
            result = {
                **(result or {}),
                "microTaskError": {"code": error.code, "message": error.message},
            }
            if isinstance(result.get("partialOutput"), dict):
                result["partialOutput"] = {**result["partialOutput"], "reason": termination_reason}
        # The seal-side shared-stash observation of this attempt's repository is
        # read here, outside the result transaction; the turn's pinned fact binds
        # it below. A failed or partial seal keeps the same honest collection.
        seal_stash = self.workflow.collect_result_stash_facts(params)
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
        return self._commit_execution_result(
            params, request=request, recovered=recovered, seal_stash=seal_stash,
            completion_evidence=stop_evidence)

    def confirm_execution_shutdown(self, attempt_id: str, evidence: dict) -> dict:
        """Supplement a committed result with stop proof; its outcome stays immutable."""
        with self.db.read() as connection:
            attempt = self._attempt_row(connection, attempt_id)
            payload = json.loads(attempt["result_json"])
        # The report was validated on arrival. Only the newly effective stop proof
        # can make a deferred successful turn importable now.
        params = {**payload, "shutdownConfirmed": True, "attemptId": attempt_id}
        stop_output = {key: evidence[key] for key in ("partialWorkspaceSeal", "workspaceSealError") if key in evidence}
        self.workflow.precheck_result({**params, "result": {**(payload["result"] or {}), **stop_output}})
        request = {key: params[key] for key in (
            "attemptId", "status", "result", "artifacts", "shutdownConfirmed", "exitCode", "signal",
            "error", "logPaths", "runtimeIdentity", "terminationReason")}
        return self._commit_execution_result(
            params, request=request, recovered=True,
            seal_stash=self.workflow.collect_result_stash_facts(params), shutdown_evidence=evidence)

    def _commit_execution_result(self, params: dict, *, request: dict,
                                 recovered: bool, seal_stash, shutdown_evidence: dict | None = None,
                                 completion_evidence: dict | None = None) -> dict:
        status, result = request["status"], request["result"]
        verified_artifacts = request["artifacts"]
        shutdown_confirmed = request["shutdownConfirmed"]
        exit_code, signal_name = request["exitCode"], request["signal"]
        error_text, log_paths = request["error"], request["logPaths"]
        termination_reason = request["terminationReason"]
        attempt_id_param = request["attemptId"]
        with self.db.write() as connection:
            if recovered:
                attempt = self._attempt_row(connection, attempt_id_param)
                self._require_effective_attempt(connection, attempt)
            else:
                attempt = self._verify_attempt_actor(connection, params)
            if attempt["attempt_id"] != attempt_id_param:
                raise BoardError("INVALID_ARGUMENT", "attemptId does not match the request")
            if attempt["execution_state"] == "finished" and attempt["result_json"] is None:
                raise BoardError("ATTEMPT_FINISHED", "This execution was released before process creation")
            if termination_reason == "deadline" and attempt["adapter"] != "decision":
                raise BoardError("INVALID_ARGUMENT", "deadline is reserved for an exhausted Router budget", field="terminationReason")
            if termination_reason in ("wrap-up", "grace-expired") and attempt["grace_deadline"] is None:
                raise BoardError("INVALID_ARGUMENT", "Wrap-up requires a durable grace deadline",
                                 field="terminationReason")
            if termination_reason == "console-stop" and attempt["stop_initiator"] != "console":
                known_console_stop = connection.execute(
                    "SELECT 1 FROM events WHERE task_id=? AND kind IN ('workflow.cancelled','workflow.helper_cancelled')"
                    " AND json_extract(payload_json,'$.stopInitiator')='console' LIMIT 1",
                    (attempt["task_id"],),
                ).fetchone()
                if known_console_stop is None:
                    raise BoardError("INVALID_ARGUMENT", "console-stop requires a durable console stop",
                                     field="terminationReason")
            if attempt["result_json"] is not None and shutdown_evidence is None:
                previous = json.loads(attempt["result_json"])
                # A later service proof changes only shutdownConfirmed. An exact
                # replay of the execution's original unconfirmed report remains
                # idempotent after that proof has released its resources.
                evidence_row = connection.execute(
                    "SELECT detail_json FROM attempt_shutdown_evidence WHERE attempt_id=?", (attempt_id_param,)).fetchone()
                supplemented = (evidence_row is not None
                                and json.loads(evidence_row["detail_json"])["receiptShutdownConfirmed"] is False)
                original = {**previous, "shutdownConfirmed": False} if supplemented else previous
                if self._same_result(previous, request) or self._same_result(original, request):
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
            if shutdown_evidence is not None:
                if attempt["shutdown_confirmed"]:
                    task = connection.execute("SELECT * FROM tasks WHERE task_id=?", (attempt["task_id"],)).fetchone()
                    return {"attempt": self._attempt_view(attempt), "task": self._decorate(connection, task),
                            "duplicate": True, "committed": True}
                # Neither a new command nor a new result is submitted here. This
                # transaction supplements the original committed receipt only.
                previous = json.loads(attempt["result_json"])
                if not self._same_result(previous, {**request, "shutdownConfirmed": False}):
                    raise BoardError("CONFLICT", "Shutdown proof may not change the committed result")
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
            if shutdown_evidence is not None:
                payload = {**previous, "shutdownConfirmed": True}
            proof = shutdown_evidence or completion_evidence
            if proof is not None and shutdown_confirmed:
                proof = {**proof, "receiptShutdownConfirmed": (previous["shutdownConfirmed"]
                         if shutdown_evidence is not None else request["shutdownConfirmed"])}
                connection.execute("INSERT INTO attempt_shutdown_evidence VALUES(?,?,?,?)",
                    (attempt_id_param, proof["kind"], canonical_json(proof), now))
                self._append_event(connection, "execution.shutdown_confirmed", task_id=attempt["task_id"],
                    attempt_id=attempt_id_param, payload={"evidence": proof})
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
            if not shutdown_confirmed and (cancel_requested or attempt["stop_initiator"] is not None or status == "cancelled"):
                task_state = "reconciliation-needed"
            elif status == "cancelled" and shutdown_confirmed:
                task_state = "cancelled"
            elif status == "ok" and shutdown_confirmed:
                # A completion that beat a cancel request is the one legal durable
                # outcome; the race is recorded rather than re-labelled. A micro-task
                # yield with a validated structured outcome requeues the same logical
                # task instead of pretending the original micro task completed.
                micro_task_yield = (
                    self.workflow._run_optional(connection, task["task_id"]) is not None
                    and self.workflow.yield_disposition(result) is not None
                )
                # A normal yielded turn is not a completed micro task that can beat a
                # cancellation. Preserve its raw receipt, but do not requeue it.
                task_state = ("cancelled" if cancel_requested else "queued") if micro_task_yield else "completed"
            elif status == "ok" and not shutdown_confirmed:
                task_state = "reconciliation-needed"
            else:
                task_state = "failed"
            connection.execute(
                "UPDATE attempts SET result_json=?, error=?, exit_code=?, signal=?,"
                " shutdown_confirmed=?, runtime_identity=COALESCE(?, runtime_identity), log_paths=COALESCE(?, log_paths),"
                " stop_initiator=COALESCE(stop_initiator,?),finished_at=?, updated_at=?, revision=revision+1 WHERE attempt_id=?",
                (
                    canonical_json(payload),
                    error_text,
                    exit_code,
                    signal_name,
                    1 if shutdown_confirmed else 0,
                    schemas.optional_string(params, "runtimeIdentity"),
                    canonical_json(log_paths) if log_paths else None,
                    "unknown" if (termination_reason == "unknown-stop"
                                  or (shutdown_evidence or {}).get("terminationReason") == "unknown-stop") else None,
                    now,
                    now,
                    attempt["attempt_id"],
                ),
            )
            from ..evaluation.native_observations import persist
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
            if recovered and connection.execute(
                    "SELECT 1 FROM attempt_interruptions WHERE attempt_id=?", (attempt_id_param,)).fetchone() is not None:
                self.decisions.fence_attempt(connection, attempt=attempt_row,
                    reason=make_reason("interrupted-before-delivery"), now=now)
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
            # Micro-task import and helper fan-in commit with the result: the validated
            # turn outcome, the assistance request and the workspace seal are one
            # durable fact with the attempt that produced them.
            settlement_payload = payload
            if shutdown_evidence is not None:
                stop_output = {key: shutdown_evidence[key] for key in ("partialWorkspaceSeal", "workspaceSealError")
                               if key in shutdown_evidence}
                if stop_output:
                    settlement_payload = {**payload, "result": {**(payload["result"] or {}), **stop_output}}
            micro_task = self.workflow.settle_turn(
                connection,
                task=task_row,
                attempt=attempt_row,
                payload=settlement_payload,
                task_state=task_state,
                now=now,
                seal_stash=seal_stash,
            )
            if micro_task is not None:
                response["workflow"] = micro_task
            if micro_task is not None and micro_task.get("autoContinued"):
                task_row = connection.execute("SELECT * FROM tasks WHERE task_id=?", (task["task_id"],)).fetchone()
                response.update(task=self._decorate(connection, task_row), taskState=task_row["state"])
            else:
                self.workflow.child_settled(
                    connection,
                    task=task_row,
                    attempt=attempt_row,
                    payload=settlement_payload,
                    task_state=task_state,
                    now=now,
                )
            if recovered and shutdown_confirmed:
                connection.execute("UPDATE attempt_interruptions SET state='settled',seal_error=NULL WHERE attempt_id=?",
                                   (attempt_id_param,))
            head = self._head_of(connection)
        self._notify(head)
        return response

    def record_execution_deadline(self, attempt_id: str, reason: str) -> dict:
        """Persist the external force deadline independently of endpoint delivery."""
        with self.db.write() as connection:
            attempt = self._attempt_row(connection, attempt_id)
            if attempt["force_requested_at"] is not None or attempt["execution_state"] == "finished":
                return {"forceRequestedAt": attempt["force_requested_at"], "forceReason": attempt["force_reason"]}
            now = self.now()
            connection.execute("UPDATE attempts SET cancel_requested_at=COALESCE(cancel_requested_at,?),"
                               "force_requested_at=?,force_reason=?,stop_initiator=COALESCE(stop_initiator,'service'),"
                               "updated_at=?,revision=revision+1 WHERE attempt_id=?", (now, now, reason, now, attempt_id))
            self._append_event(connection, "execution.deadline", task_id=attempt["task_id"],
                               attempt_id=attempt_id, payload={"reason": reason})
            head = self._head_of(connection)
        self._notify(head)
        return {"forceRequestedAt": now, "forceReason": reason}

    def record_execution_issue(self, attempt_id: str, stage: str, error) -> None:
        """Retain a visible failure on one execution, deduplicated by stage and cause."""
        self._record_runtime_issue(stage, error, attempt_id=attempt_id)

    def execution_finished(self, attempt_id: str) -> bool:
        with self.db.read() as connection:
            return self._attempt_row(connection, attempt_id)["execution_state"] == "finished"

    def complete_execution_cleanup(self, attempt_id: str) -> None:
        with self.db.write() as connection:
            attempt = self._attempt_row(connection, attempt_id)
            if not attempt["cleanup_pending"]:
                return
            connection.execute("UPDATE attempts SET cleanup_pending=0 WHERE attempt_id=?", (attempt_id,))
            self._append_event(connection, "execution.materials_cleaned", task_id=attempt["task_id"],
                               attempt_id=attempt_id, payload={})
            head = self._head_of(connection)
        self._notify(head)

    def record_service_issue(self, stage: str, error) -> None:
        self._record_runtime_issue(stage, error)

    def service_issues(self) -> list[dict]:
        with self.db.read() as connection:
            return [{"stage": row["stage"], "code": row["code"], "message": row["message"],
                     "observedAt": row["observed_at"]}
                    for row in connection.execute("SELECT * FROM service_issues ORDER BY stage")]

    def clear_service_issue(self, stage: str) -> None:
        with self.db.write() as connection:
            removed = connection.execute("DELETE FROM service_issues WHERE stage=?", (stage,)).rowcount
            if not removed:
                return
            self._append_event(connection, "service.issue_resolved", payload={"stage": stage})
            head = self._head_of(connection)
        self._notify(head)

    def clear_execution_issue(self, attempt_id: str, stage: str) -> None:
        with self.db.write() as connection:
            removed = connection.execute("DELETE FROM execution_issues WHERE attempt_id=? AND stage=?",
                                         (attempt_id, stage)).rowcount
            if not removed:
                return
            task_id = self._attempt_row(connection, attempt_id)["task_id"]
            self._append_event(connection, "execution.issue_resolved", task_id=task_id, attempt_id=attempt_id,
                               payload={"stage": stage})
            head = self._head_of(connection)
        self._notify(head)

    def _record_runtime_issue(self, stage: str, error, *, attempt_id: str | None = None) -> None:
        code = error.code if isinstance(error, BoardError) else type(error).__name__
        message = (error.message if isinstance(error, BoardError) else str(error))[:4000]
        table = "execution_issues" if attempt_id is not None else "service_issues"
        selector = "attempt_id=? AND stage=?" if attempt_id is not None else "stage=?"
        keys = (attempt_id, stage) if attempt_id is not None else (stage,)
        with self.db.write() as connection:
            attempt = self._attempt_row(connection, attempt_id) if attempt_id is not None else None
            if attempt is not None and stage == "connection" and attempt["execution_state"] == "finished":
                return
            previous = connection.execute(f"SELECT code,message FROM {table} WHERE {selector}", keys).fetchone()
            if previous is not None and (previous["code"], previous["message"]) == (code, message):
                return
            now = self.now()
            task_id = attempt["task_id"] if attempt is not None else None
            if attempt_id is not None:
                connection.execute("INSERT INTO execution_issues VALUES(?,?,?,?,?) ON CONFLICT(attempt_id,stage)"
                    " DO UPDATE SET code=excluded.code,message=excluded.message,observed_at=excluded.observed_at",
                    (attempt_id, stage, code, message, now))
            else:
                connection.execute("INSERT INTO service_issues VALUES(?,?,?,?) ON CONFLICT(stage)"
                    " DO UPDATE SET code=excluded.code,message=excluded.message,observed_at=excluded.observed_at",
                    (stage, code, message, now))
            self._append_event(connection, "execution.issue" if attempt_id is not None else "service.issue",
                task_id=task_id, attempt_id=attempt_id, payload={"stage": stage, "code": code, "message": message})
            head = self._head_of(connection)
        self._notify(head)

    def release_execution(self, attempt_id: str, *, reason: str) -> dict:
        """Release a dispatch when its creator proves no execution process was created."""
        release_reason = {"code": reason, "params": {}}
        seal_stash = self.workflow.collect_result_stash_facts({"attemptId": attempt_id})
        with self.db.write() as connection:
            attempt = self._attempt_row(connection, attempt_id)
            self._require_effective_attempt(connection, attempt)
            if attempt["execution_state"] == "finished":
                task = connection.execute("SELECT * FROM tasks WHERE task_id=?", (attempt["task_id"],)).fetchone()
                return {"task": self._task_view(task), "released": True}
            if attempt["identity_confirmed_at"] is not None:
                raise BoardError("CONFLICT", "An acknowledged execution must be settled from process evidence")
            now = self.now()
            self._transition_attempt(connection, attempt, "finished")
            connection.execute(
                "UPDATE attempts SET finished_at=?, updated_at=?, shutdown_confirmed=1,"
                " revision=revision+1 WHERE attempt_id=?",
                (now, now, attempt["attempt_id"]),
            )
            connection.execute("INSERT INTO attempt_shutdown_evidence VALUES(?,?,?,?)",
                (attempt_id, "process-not-created", canonical_json({"kind": "process-not-created",
                 "receiptShutdownConfirmed": None}), now))
            task = connection.execute("SELECT * FROM tasks WHERE task_id=?", (attempt["task_id"],)).fetchone()
            micro_task = self.workflow._run_optional(connection, task["task_id"])
            cancelled = (task["state"] == "cancelling" or attempt["cancel_requested_at"] is not None
                         or micro_task is not None and micro_task["state"] == "cancelled")
            self._transition_task(connection, task, "cancelled" if cancelled else "failed")
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
                    "reason": release_reason,
                    "releaseEvidence": "processCreated=false",
                    "previousState": attempt["execution_state"],
                },
            )
            task_row = connection.execute("SELECT * FROM tasks WHERE task_id=?", (attempt["task_id"],)).fetchone()
            # An attempt released before its adapter ran means the decision never
            # reached a model: it fails honestly and releases its reader or writer.
            self.decisions.released(connection, task=task_row, reason=release_reason, now=now)
            released_attempt = connection.execute(
                "SELECT * FROM attempts WHERE attempt_id=?", (attempt["attempt_id"],)
            ).fetchone()
            self.workflow.attempt_released(
                connection, task=task_row, attempt=released_attempt, now=now,
                seal_stash=seal_stash,
            )
            head = self._head_of(connection)
        self._notify(head)
        from ..tasks.interruption import _cleanup_dead_material
        _cleanup_dead_material(self, dict(attempt), None, "process-not-created")
        return {"task": self._task_view(task_row), "released": True}

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
                reason_code = None if state == "queued" else "inquiry-no-live-agent"
                message_id = str(uuid.uuid4())
                connection.execute(
                    "INSERT INTO messages(message_id, task_id, attempt_id, inquiry_id, direction, author, recipient,"
                    " correlation_id, body, body_bytes, payload_hash, state, reason_code, reason_params_json, created_at, updated_at, revision)"
                    " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1)",
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
                        reason_code,
                        "{}" if reason_code else None,
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
        return self._message_update(params)

    def _message_update(self, params: dict, *, reason: dict | None = None) -> dict:
        """Apply one bounded delivery/answer observation.

        Every field is validated on a candidate copy before anything is written, a
        recorded answer is terminal, and an ``answered`` state without a nonblank
        answer is rejected instead of being committed half-answered.
        """
        schemas.reject_unknown(
            params,
            {"runId", "taskId", "messageId", "inquiryId", "state", "delivery", "answer", "actor"},
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
        actor = {"role": "bridge", "id": schemas.optional_string(params, "actor") or "bridge"}
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
                candidate["reason"] = reason
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
            if candidate["state"] not in ("delivered", "unavailable"):
                candidate["reason"] = None
            now = self.now()
            connection.execute(
                "UPDATE messages SET state=?, reason_code=?, reason_params_json=?, delivery_json=?, answer_json=?, attempts_count=attempts_count+1,"
                " updated_at=?, revision=revision+1 WHERE message_id=?",
                (
                    candidate["state"],
                    candidate["reason"]["code"] if candidate["reason"] else None,
                    canonical_json(candidate["reason"]["params"]) if candidate["reason"] else None,
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
    @staticmethod
    def _process_identity(connection, attempt_id: str) -> dict | None:
        from ..tasks.interruption import read_identity
        return read_identity(connection, attempt_id)

    @staticmethod
    def _record_process_identity(connection, attempt_id: str, record: dict) -> None:
        from ..tasks.interruption import record_identity
        record_identity(connection, attempt_id, record)

    def recover_executions(self, attempt_id: str | None = None, *, process_identity: dict | None = None,
                           stopped_evidence: dict | None = None) -> list[dict]:
        from ..tasks.interruption import recover, record_identity
        with self._recovery_lock:
            if process_identity is not None:
                with self.db.write() as connection:
                    record_identity(connection, attempt_id, process_identity)
            return recover(self, attempt_id=attempt_id, stopped_evidence=stopped_evidence)

    def execution_identity_confirmed(self, attempt_id: str) -> bool:
        with self.db.read() as connection:
            return self._attempt_row(connection, attempt_id)["identity_confirmed_at"] is not None

    def execution_observations(self) -> list[dict]:
        """Immutable bindings needed to observe pre-existing executions, without credentials."""
        with self.db.read() as connection:
            return [{"attempt": self._attempt_view(row, include_result=False),
                     "task": {"taskId": row["task_id"], "spec": {"adapter": row["adapter"]}},
                     "harness": row["model_harness"], "turnId": row["turn_id"],
                     "processIdentity": self._process_identity(connection, row["attempt_id"])}
                    for row in connection.execute(
                        "SELECT a.*,t.turn_id FROM attempts a LEFT JOIN workflow_turns t ON t.attempt_id=a.attempt_id"
                        " WHERE a.execution_state!='finished' ORDER BY a.created_at,a.attempt_id")]

    def execution_connection(self, attempt_id: str, *, connected: bool) -> None:
        """Project an authenticated long call, or the absence of one, without settling work."""
        with self.db.write() as connection:
            attempt = self._attempt_row(connection, attempt_id)
            if attempt["execution_state"] == "finished" or attempt["result_json"] is not None:
                return
            self._require_effective_attempt(connection, attempt)
            state, ownership = ("executing", "owned") if connected else ("uncertain", "uncertain")
            if (connected and attempt["execution_state"] != "uncertain"
                    or (attempt["execution_state"], attempt["ownership"]) == (state, ownership)):
                return
            now = self.now()
            self._transition_attempt(connection, attempt, state)
            connection.execute("UPDATE attempts SET ownership=? WHERE attempt_id=?", (ownership, attempt_id))
            connection.execute("UPDATE resource_claims SET state=? WHERE attempt_id=? AND state IN ('held','retained')",
                               ("held" if connected else "retained", attempt_id))
            if connected:
                connection.execute("UPDATE tasks SET queue_reason=NULL,updated_at=? WHERE task_id=?"
                                   " AND queue_reason IN (?,?)",
                                   (now, attempt["task_id"], UNCERTAIN_QUEUE_REASON, UNKNOWN_QUEUE_REASON))
            else:
                connection.execute("UPDATE tasks SET queue_reason=?,updated_at=? WHERE task_id=?",
                                   (UNKNOWN_QUEUE_REASON, now, attempt["task_id"]))
            self._append_event(connection, "execution.connected" if connected else "execution.unknown",
                               task_id=attempt["task_id"], attempt_id=attempt_id, payload={})
            head = self._head_of(connection)
        self._notify(head)

    def reconcile_startup(self) -> dict:
        """Make pre-restart in-flight attempts uncertain.

        It keeps resource claims until the scheduler authenticates a new long call
        or identity recovery proves shutdown. The execution may still submit its result.
        """
        summary = {"uncertain": 0, "retained": 0}
        with self.db.write() as connection:
            now = self.now()
            rows = connection.execute(
                "SELECT * FROM attempts WHERE execution_state IN ('starting','executing','finalizing')"
            ).fetchall()
            for attempt in rows:
                connection.execute(
                    "UPDATE attempts SET execution_state='uncertain', ownership='uncertain',"
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
                    payload={},
                )
            summary["retained"] = int(
                connection.execute(
                    "SELECT COUNT(*) AS count FROM resource_claims WHERE state='retained'"
                ).fetchone()["count"]
            )
            head = self._head_of(connection)
        if summary["uncertain"] or head:
            self._notify(head)
        summary["interrupted"] = len(self.recover_executions())
        return summary

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
