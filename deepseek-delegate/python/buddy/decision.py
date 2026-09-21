"""Durable decision orchestration over the real Worker/attempt/capacity system.

A decision (a *selection* recommendation or one bounded *evaluation maintenance*
proposal) is not a second execution engine. The service records a decision, admits
the admission/lease it needs, and then creates one ordinary task whose adapter is the
narrow :mod:`buddy.adapters.decision` helper. The existing independent worker claims
it, consumes real ``BUDDY_MAX_CONCURRENT`` capacity, owns the helper process handle,
enforces the deadline and reports a durable receipt. Python remains the only writer
of authoritative state: the decision row, the reader release or writer grant and the
published evaluation revision are all committed inside the *same* transaction as the
worker's result, never as a side effect of a GET.

The split of responsibilities is deliberate:

* selection readers are admitted only when a queued selection can actually execute,
  on a complete current revision, and are fenced with the attempt lifecycle;
* maintenance uses the fixed configured decision profile, bypasses selection
  admission, registers a fair writer intent, and holds renewable fenced writer
  authority so a queued selector can never be starved and a model job can never take
  the table from a human writer;
* every helper output is untrusted input: candidates, evidence references, preserved
  risks and the expected revision are re-validated in Python before one atomic
  publication, and anything outside the bounded policy becomes ``needs-host``.
"""
from __future__ import annotations

import json
import sqlite3
import uuid
from typing import Any

from . import schemas
from .db import canonical_json, sha256_text
from .errors import BoardError
from .evaluation import MAX_CARD_POINTS, MAX_EVIDENCE_IDS, MAX_POINT, MAX_SUMMARY

DECISION_ADAPTER = "decision"
DECISION_KINDS = ("select", "maintain")
#: Durable decision states. ``needs-host`` is the honest abstention/out-of-policy
#: outcome; ``stale`` is a fenced result that may not become a current recommendation.
DECISION_STATUSES = ("queued", "running", "completed", "needs-host", "failed", "cancelled", "stale")
TERMINAL_DECISION_STATUSES = frozenset({"completed", "needs-host", "failed", "cancelled", "stale"})

SELECT_FIELDS = frozenset({"requestId", "task", "requiredCapabilities", "timeoutSeconds"})
MAINTAIN_FIELDS = frozenset({"requestId", "timeoutSeconds"})
GET_FIELDS = frozenset({"decisionId", "includeAudit"})

MAX_DECISION_TASK_BYTES = 8192
MAX_DECISION_REASON = 2000
MAX_DECISION_EVIDENCE_IDS = 32
#: One bounded maintenance batch. The helper accepts more, but the service
#: deliberately sends a small complete batch and reports how many evidence rows
#: remain pending. A batch is never silently truncated: when even a single pending
#: row's card references cannot fit the bounded document, the decision reports an
#: honest oversized-batch outcome instead of dropping a reference.
MAX_DECISION_BATCH = 64
#: Harness bounds mirrored from the decision helper's request validation. They are
#: the hard ceiling for one bounded request, never a silent truncation point: a
#: complete current table above them becomes an explicit needs-host outcome.
MAX_DECISION_PROFILES = 200
MAX_DECISION_CARDS = 512
MAX_DECISION_PREFERENCES = 256
MAX_DECISION_EVIDENCE = 512
MAX_DECISION_INPUT_BYTES = 128 * 1024
MIN_TIMEOUT_SECONDS = 5
#: The helper refuses a value above 1800, so the service never forwards one.
MAX_TIMEOUT_SECONDS = 1800
DEFAULT_TIMEOUT_SECONDS = 300
#: Extra room after the helper's own bound: the worker deadline must not fire while
#: the helper is still shutting its detached child down.
TIMEOUT_GRACE_SECONDS = 10
#: How long a queued maintenance intent may wait for admitted readers before its
#: grant is allowed to expire and the decision fails honestly.
WRITER_QUEUE_GRACE_SECONDS = 120

QUEUE_WRITER_PENDING = (
    "waiting for the evaluation writer grant; new selection readers are closed and this selection stays queued and "
    "cancellable"
)
NEEDS_HOST_NO_PROFILE = (
    "no compatible fixed decision profile is configured; publish an available, enabled dsh profile as "
    "configuration.decisionProfileId. The service never guesses one and never selects a selector."
)
NEEDS_HOST_NO_CANDIDATE = (
    "no enabled, available, capability-matching profile is a legal candidate under the published pins and "
    "excludes, so there is nothing honest to recommend"
)

def decision_spec(spec: dict) -> dict | None:
    """The decision descriptor of a task specification, when this is a decision task."""
    value = spec.get("decision") if isinstance(spec, dict) else None
    if not isinstance(value, dict):
        return None
    decision_id = value.get("decisionId")
    kind = value.get("kind")
    if not isinstance(decision_id, str) or kind not in DECISION_KINDS:
        return None
    return value


class DecisionCoordinator:
    """Durable decisions, their admission, and their atomic publication."""

    def __init__(self, board, evaluation):
        self.board = board
        self.evaluation = evaluation
        self.db = board.db

    # -- small helpers -------------------------------------------------------
    def _now(self) -> str:
        return self.board.now()

    def _state(self, connection: sqlite3.Connection) -> sqlite3.Row:
        return self.evaluation._state(connection)

    def _row(self, connection: sqlite3.Connection, decision_id: str) -> sqlite3.Row | None:
        return connection.execute(
            "SELECT d.decision_id, d.status, d.task, d.profile_id, d.table_revision, d.reason, d.evidence_ids_json,"
            " d.created_at, d.error, r.request_id, r.kind, r.input_fingerprint, r.configuration_revision,"
            " r.task_id AS decision_task_id, r.attempt_id AS decision_attempt_id,"
            " r.generation AS decision_generation, r.reader_id, r.writer_id, r.writer_generation,"
            " r.expected_revision, r.published_revision, r.selected_json, r.input_json, r.input_sha256, r.output_json, r.proposal_json,"
            " r.considered_evidence, r.pending_after, r.auto_publish, r.requested_json,"
            " r.created_at AS request_created_at, r.updated_at AS request_updated_at"
            " FROM decision_requests r JOIN evaluation_decisions d ON d.decision_id = r.decision_id"
            " WHERE r.decision_id=?",
            (decision_id,),
        ).fetchone()

    def _row_by_request(self, connection: sqlite3.Connection, request_id: str) -> sqlite3.Row | None:
        row = connection.execute(
            "SELECT decision_id FROM decision_requests WHERE request_id=?", (request_id,)
        ).fetchone()
        return self._row(connection, row["decision_id"]) if row is not None else None

    @staticmethod
    def _bounded_text(value: Any, limit: int) -> str | None:
        if not isinstance(value, str):
            return None
        text = value.strip()
        return text[:limit] if text else None

    def _append_event(self, connection: sqlite3.Connection, kind: str, decision_id: str, payload: dict, revision: int | None = None) -> None:
        self.board._append_event(connection, kind, revision=revision, payload={"decisionId": decision_id, **payload})

    def _adapter_available(self) -> tuple[bool, str | None]:
        from .adapters.decision import DecisionAdapter

        return DecisionAdapter().available()

    # -- request-time policy -------------------------------------------------
    def _decision_profile(self, connection: sqlite3.Connection) -> tuple[sqlite3.Row | None, str | None]:
        profile_id = self._state(connection)["decision_profile_id"]
        if not profile_id:
            return None, NEEDS_HOST_NO_PROFILE
        row = connection.execute(
            "SELECT * FROM evaluation_profiles WHERE profile_id=?", (profile_id,)
        ).fetchone()
        if row is None:
            return None, f"the configured decision profile {profile_id} is not published: {NEEDS_HOST_NO_PROFILE}"
        if not row["enabled"] or not row["available"]:
            return None, (
                f"the configured decision profile {profile_id} is not an available, enabled profile; fix the "
                "configuration before asking for a decision"
            )
        if row["adapter"] != "dsh":
            return None, (
                f"the configured decision profile {profile_id} uses the {row['adapter']} adapter; the bounded "
                "decision helper only invokes the installed dsh model path"
            )
        if not row["provider"] or not row["model"] or not row["effort"]:
            return None, f"the configured decision profile {profile_id} has no complete provider/model/effort identity"
        return row, None

    @staticmethod
    def _select_candidates(connection: sqlite3.Connection, required_capabilities: list[str]) -> list[sqlite3.Row]:
        """Legal candidates: enabled, available, capability-matching, pinned and not excluded.

        Pin/exclude is enforced here in Python and never delegated to the model;
        ``prefer`` stays a soft ordering hint that only enters the bounded input.
        """
        required = set(required_capabilities)
        preferences = {
            row["profile_id"]: row["mode"]
            for row in connection.execute("SELECT profile_id, mode FROM evaluation_preferences")
        }
        pinned = {profile_id for profile_id, mode in preferences.items() if mode == "pin"}
        excluded = {profile_id for profile_id, mode in preferences.items() if mode == "exclude"}
        legal = [
            row
            for row in connection.execute("SELECT * FROM evaluation_profiles ORDER BY rowid")
            if row["enabled"]
            and row["available"]
            and row["profile_id"] not in excluded
            and required.issubset(set(json.loads(row["capabilities_json"])))
        ]
        if pinned:
            legal = [row for row in legal if row["profile_id"] in pinned]
        return legal[:MAX_DECISION_PROFILES]

    # -- bounded model input -------------------------------------------------
    def _profile_input(self, row: sqlite3.Row) -> dict:
        entry = {
            "profileId": row["profile_id"],
            "label": row["label"],
            "adapter": row["adapter"],
            "provider": row["provider"],
            "model": row["model"],
            "effort": row["effort"],
            "available": bool(row["available"]),
            "enabled": bool(row["enabled"]),
            "capabilities": json.loads(row["capabilities_json"]),
            "contextWindow": row["context_window"],
            "description": row["description"],
            "source": row["source"],
        }
        if row["unavailable_reason"]:
            entry["unavailableReason"] = row["unavailable_reason"]
        return entry

    def _card_input(self, row: sqlite3.Row) -> dict:
        return {
            "profileId": row["profile_id"],
            "revision": int(row["revision"]),
            "summary": row["summary"],
            "strengths": json.loads(row["strengths_json"]),
            "limitations": json.loads(row["limitations_json"]),
            "risks": json.loads(row["risks_json"]),
            "evidenceIds": json.loads(row["evidence_ids_json"]),
            "sampleCount": int(row["sample_count"]),
            "updatedAt": row["updated_at"],
        }

    @staticmethod
    def _evidence_input(row: sqlite3.Row) -> dict:
        identity = json.loads(row["identity_json"]) if row["identity_json"] else {}
        return {
            "evidenceId": row["evidence_id"],
            "profileId": row["profile_id"],
            "kind": row["kind"],
            "summary": row["summary"],
            "project": row["project"],
            "conditions": json.loads(row["conditions_json"]),
            "source": row["source"],
            "runId": row["run_id"],
            "createdAt": row["created_at"],
            "verified": bool(row["verified"]),
            "counted": bool(row["counted"]),
            "attemptId": identity.get("attemptId"),
            "identityBasis": (identity.get("basis") or {}).get("source"),
        }

    def _table_input(
        self,
        connection: sqlite3.Connection,
        *,
        profiles: list[sqlite3.Row],
        evidence_rows: list[sqlite3.Row],
        include_preferences: bool,
    ) -> dict:
        """The complete bounded table slice for one request — never a silent truncation.

        Every legal candidate, its complete card, every preference and every
        referenced evidence row is included. When that complete slice exceeds the
        helper's request bounds, :meth:`_assemble_input` reports an explicit
        oversized outcome instead of picking rows by insertion order.
        """
        profile_ids = [row["profile_id"] for row in profiles]
        markers = ",".join("?" for _ in profile_ids) or "''"
        cards = [
            self._card_input(row)
            for row in connection.execute(
                f"SELECT * FROM evaluation_cards WHERE profile_id IN ({markers}) ORDER BY rowid", profile_ids
            )
        ]
        preferences: list[dict] = []
        if include_preferences:
            preferences = [
                {"profileId": row["profile_id"], "mode": row["mode"], "reason": row["reason"]}
                for row in connection.execute("SELECT * FROM evaluation_preferences ORDER BY rowid")
                if row["profile_id"] in set(profile_ids)
            ]
        seen: set[str] = set()
        evidence: list[dict] = []
        for row in evidence_rows:
            if row["evidence_id"] in seen:
                continue
            seen.add(row["evidence_id"])
            evidence.append(self._evidence_input(row))
        return {
            "profiles": [self._profile_input(row) for row in profiles],
            "cards": cards,
            "preferences": preferences,
            "evidence": evidence,
        }

    def _referenced_evidence(self, connection: sqlite3.Connection, cards: list[dict]) -> list[sqlite3.Row]:
        """Every evidence row referenced by these cards, in reference order."""
        ids: list[str] = []
        for card in cards:
            for evidence_id in card["evidenceIds"]:
                if evidence_id not in ids:
                    ids.append(evidence_id)
        if not ids:
            return []
        markers = ",".join("?" for _ in ids)
        rows = connection.execute(
            f"SELECT * FROM evaluation_evidence WHERE evidence_id IN ({markers})", ids
        ).fetchall()
        order = {evidence_id: index for index, evidence_id in enumerate(ids)}
        return sorted(rows, key=lambda row: order.get(row["evidence_id"], len(ids)))

    def _assemble_input(self, connection: sqlite3.Connection, *, kind: str, request_id: str, revision: int, profile_row: sqlite3.Row, table: dict, task_text: str | None) -> tuple[dict | None, str | None]:
        """Build the exact bounded document the model sees, or report why it cannot.

        Returns ``(document, oversized_reason)``. Nothing is dropped to make a
        document fit: exceeding a harness bound or the byte ceiling is an honest
        ``needs-host`` outcome.
        """
        for name, limit in (
            ("profiles", MAX_DECISION_PROFILES),
            ("cards", MAX_DECISION_CARDS),
            ("preferences", MAX_DECISION_PREFERENCES),
            ("evidence", MAX_DECISION_EVIDENCE),
        ):
            if len(table[name]) > limit:
                return None, (
                    f"the complete bounded table slice carries {len(table[name])} {name}, above the helper's "
                    f"{limit}-entry request bound; nothing was truncated and nothing was sent to a model. Curate "
                    "the published table or the maintenance batch first."
                )
        document: dict[str, Any] = {
            "operation": kind,
            "requestId": request_id,
            "profile": {
                "provider": profile_row["provider"],
                "model": profile_row["model"],
                "effort": profile_row["effort"],
            },
            "tableRevision": revision,
        }
        if kind == "select":
            document["task"] = task_text
        document.update(table)
        encoded = canonical_json(document)
        if len(encoded.encode("utf-8")) > MAX_DECISION_INPUT_BYTES:
            return None, (
                f"the complete bounded table slice is {len(encoded.encode('utf-8'))} bytes, above the "
                f"{MAX_DECISION_INPUT_BYTES}-byte decision input ceiling; nothing was truncated and nothing was "
                "sent to a model. Curate the published table first."
            )
        return document, None

    def _select_input(self, connection: sqlite3.Connection, *, request_id: str, revision: int, profile_row: sqlite3.Row, task_text: str, candidates: list[sqlite3.Row]) -> tuple[dict | None, str | None]:
        candidate_ids = {candidate["profile_id"] for candidate in candidates}
        cards = [
            self._card_input(row)
            for row in connection.execute("SELECT * FROM evaluation_cards ORDER BY rowid")
            if row["profile_id"] in candidate_ids
        ]
        evidence = self._referenced_evidence(connection, cards)
        table = self._table_input(
            connection, profiles=candidates, evidence_rows=evidence, include_preferences=True
        )
        return self._assemble_input(
            connection,
            kind="select",
            request_id=request_id,
            revision=revision,
            profile_row=profile_row,
            table=table,
            task_text=task_text,
        )

    def _pending_batch(self, connection: sqlite3.Connection, limit: int) -> list[sqlite3.Row]:
        return connection.execute(
            "SELECT e.* FROM evaluation_evidence_pending p JOIN evaluation_evidence e"
            " ON e.evidence_id = p.evidence_id ORDER BY p.created_at, p.evidence_id LIMIT ?",
            (limit,),
        ).fetchall()

    def _maintain_input(self, connection: sqlite3.Connection, *, request_id: str, revision: int, profile_row: sqlite3.Row) -> tuple[dict | None, str | None, int, int]:
        """One bounded maintenance batch, shrinking only the *pending* selection.

        The batch's card references are always complete: old risks and evidence
        references are part of the card the model must preserve, so they are never
        dropped to make room. When even a single pending row's card references do
        not fit, the outcome is an explicit oversized-batch reason.
        """
        total_pending = self.evaluation._pending_evidence(connection)
        if total_pending == 0:
            return None, None, 0, 0
        limit = min(MAX_DECISION_BATCH, total_pending)
        last_reason: str | None = None
        while limit >= 1:
            pending = self._pending_batch(connection, limit)
            profile_ids = {row["profile_id"] for row in pending}
            profiles = [
                row
                for row in connection.execute("SELECT * FROM evaluation_profiles ORDER BY rowid")
                if row["profile_id"] in profile_ids
            ]
            cards = [
                self._card_input(row)
                for row in connection.execute("SELECT * FROM evaluation_cards ORDER BY rowid")
                if row["profile_id"] in profile_ids
            ]
            referenced = self._referenced_evidence(connection, cards)
            seen = {row["evidence_id"] for row in pending}
            evidence = list(pending) + [row for row in referenced if row["evidence_id"] not in seen]
            table = self._table_input(
                connection, profiles=profiles, evidence_rows=evidence, include_preferences=True
            )
            document, reason = self._assemble_input(
                connection,
                kind="maintain",
                request_id=request_id,
                revision=revision,
                profile_row=profile_row,
                table=table,
                task_text=None,
            )
            if document is not None:
                remaining = max(0, total_pending - len(pending))
                return document, None, len(pending), remaining
            last_reason = reason
            if limit == 1:
                break
            limit = max(1, limit // 2)
        return None, last_reason, 0, total_pending

    # -- lease helpers -------------------------------------------------------
    def _admit_reader(self, connection: sqlite3.Connection, now: str, *, timeout_seconds: int) -> str:
        reader_id = str(uuid.uuid4())
        lease = max(self.evaluation.reader_lease_seconds, timeout_seconds + 60)
        expires_at = self.evaluation._plus(lease, now)
        connection.execute(
            "INSERT INTO evaluation_readers(reader_id, kind, admitted_at, expires_at, released_at, expired)"
            " VALUES(?,?,?,?,NULL,0)",
            (reader_id, "selection", now, expires_at),
        )
        self._append_event(
            connection,
            "evaluation.reader_admitted",
            None,
            {"readerId": reader_id, "kind": "selection", "expiresAt": expires_at},
            revision=int(self._state(connection)["table_revision"]),
        )
        return reader_id

    def _release_reader(self, connection: sqlite3.Connection, row: sqlite3.Row, now: str) -> None:
        if not row["reader_id"]:
            return
        updated = connection.execute(
            "UPDATE evaluation_readers SET released_at=? WHERE reader_id=? AND released_at IS NULL",
            (now, row["reader_id"]),
        ).rowcount
        if updated:
            self._append_event(
                connection,
                "evaluation.reader_released",
                row["decision_id"],
                {"readerId": row["reader_id"], "kind": "selection", "expired": False},
                revision=int(self._state(connection)["table_revision"]),
            )
            self.evaluation._promote(connection, now)

    def _release_writer(self, connection: sqlite3.Connection, row: sqlite3.Row, now: str, *, state: str = "aborted") -> None:
        if not row["writer_id"]:
            return
        writer = connection.execute(
            "SELECT * FROM evaluation_writers WHERE writer_id=?", (row["writer_id"],)
        ).fetchone()
        if writer is None or writer["state"] in ("published", "aborted", "expired"):
            return
        connection.execute(
            "UPDATE evaluation_writers SET state=?, released_at=? WHERE writer_id=?", (state, now, row["writer_id"])
        )
        self._append_event(
            connection,
            "evaluation.writer_aborted" if state == "aborted" else "evaluation.writer_expired",
            row["decision_id"],
            {"writerId": row["writer_id"], "generation": int(writer["generation"]), "kind": writer["kind"]},
            revision=int(self._state(connection)["table_revision"]),
        )
        self.evaluation._promote(connection, now)

    # -- request operations --------------------------------------------------
    def request_select(self, params: dict) -> dict:
        schemas.reject_unknown(params, SELECT_FIELDS, "selection.request")
        request_id = schemas.required_string(
            params, "requestId", max_length=128, pattern=schemas.IDENTIFIER_PATTERN
        )
        task_text, _size = schemas.bounded_text(params, "task", max_bytes=MAX_DECISION_TASK_BYTES)
        capabilities = schemas.string_list(params, "requiredCapabilities", limit=schemas.MAX_CAPABILITIES)
        timeout = schemas.optional_int(
            params, "timeoutSeconds", DEFAULT_TIMEOUT_SECONDS, MIN_TIMEOUT_SECONDS, MAX_TIMEOUT_SECONDS
        )
        request = {
            "kind": "select",
            "task": task_text,
            "requiredCapabilities": capabilities,
            "timeoutSeconds": timeout,
        }
        return self._create(request_id, request)

    def request_maintain(self, params: dict) -> dict:
        schemas.reject_unknown(params, MAINTAIN_FIELDS, "evaluation.maintain")
        request_id = schemas.required_string(
            params, "requestId", max_length=128, pattern=schemas.IDENTIFIER_PATTERN
        )
        timeout = schemas.optional_int(
            params, "timeoutSeconds", DEFAULT_TIMEOUT_SECONDS, MIN_TIMEOUT_SECONDS, MAX_TIMEOUT_SECONDS
        )
        request = {"kind": "maintain", "timeoutSeconds": timeout}
        return self._create(request_id, request)

    def _create(self, request_id: str, request: dict) -> dict:
        fingerprint = sha256_text(canonical_json(request))
        kind = request["kind"]
        with self.board.db.write() as connection:
            existing = self._row_by_request(connection, request_id)
            if existing is not None:
                if existing["input_fingerprint"] != fingerprint:
                    raise BoardError(
                        "CONFLICT",
                        "requestId already belongs to a different decision input; use a new requestId",
                        requestId=request_id,
                        decisionId=existing["decision_id"],
                    )
                return self._request_response(connection, existing, duplicate=True)
            decision_id = f"dec-{uuid.uuid4()}"
            now = self._now()
            state = self._state(connection)
            available, unavailable_reason = self._adapter_available()
            profile_row, profile_reason = self._decision_profile(connection)
            # A writer intent means the table is about to move. The request is created
            # queued and validated when it can actually run, on the revision the
            # writer publishes: a selection that arrives while the first profile or
            # the first evidence is being published must not be judged against the
            # old table.
            writer_pending = bool(
                connection.execute(
                    "SELECT COUNT(*) AS count FROM evaluation_writers WHERE state IN ('waiting','active')"
                ).fetchone()["count"]
            )
            status = "queued"
            reason = QUEUE_WRITER_PENDING if kind == "maintain" else "queued for a worker; the selection reader is admitted when the run starts"
            error: str | None = None
            task_text = request.get("task") if kind == "select" else "evaluation maintenance"
            writer_id: str | None = None
            writer_generation: int | None = None
            expected_revision = int(state["table_revision"])
            if not available:
                status = "failed"
                error = f"ADAPTER_UNAVAILABLE: {unavailable_reason or 'the bounded decision helper is unavailable'}"
                reason = "the bounded decision helper is not available in this build, so no model call was made"
            elif profile_row is None and not writer_pending:
                status = "needs-host"
                reason = profile_reason or NEEDS_HOST_NO_PROFILE
            elif (
                kind == "select"
                and not writer_pending
                and not self._select_candidates(connection, request["requiredCapabilities"])
            ):
                status = "needs-host"
                reason = NEEDS_HOST_NO_CANDIDATE
            elif kind == "maintain" and not writer_pending and self.evaluation._pending_evidence(connection) == 0:
                status = "needs-host"
                reason = "no pending evidence needs maintenance; nothing was sent to a model"
            create_task = status == "queued"
            task_id = None
            if create_task:
                if kind == "maintain":
                    writer_id, writer_generation = self._queue_writer(
                        connection, decision_id=decision_id, expected_revision=expected_revision,
                        now=now, timeout_seconds=request["timeoutSeconds"],
                    )
                task_id = self._create_task(
                    connection,
                    decision_id=decision_id,
                    request_id=request_id,
                    kind=kind,
                    timeout_seconds=request["timeoutSeconds"],
                    now=now,
                )
            connection.execute(
                "INSERT INTO evaluation_decisions(decision_id, status, task, profile_id, table_revision, reason,"
                " evidence_ids_json, created_at, error) VALUES(?,?,?,?,?,?,?,?,?)",
                (
                    decision_id,
                    status,
                    task_text,
                    None,
                    expected_revision,
                    reason,
                    canonical_json([]),
                    now,
                    error,
                ),
            )
            connection.execute(
                "INSERT INTO decision_requests(decision_id, request_id, kind, input_fingerprint,"
                " configuration_revision, task_id, expected_revision, requested_json, auto_publish, created_at,"
                " updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (
                    decision_id,
                    request_id,
                    kind,
                    fingerprint,
                    int(state["configuration_revision"]),
                    task_id,
                    expected_revision,
                    canonical_json(request),
                    1 if (kind == "maintain" and bool(state["auto_maintain"])) else 0,
                    now,
                    now,
                ),
            )
            if writer_id:
                connection.execute(
                    "UPDATE decision_requests SET writer_id=?, writer_generation=? WHERE decision_id=?",
                    (writer_id, writer_generation, decision_id),
                )
            self._append_event(
                connection,
                "decision.requested",
                decision_id,
                {
                    "requestId": request_id,
                    "kind": kind,
                    "status": status,
                    "taskId": task_id,
                    "tableRevision": expected_revision,
                    "reason": reason,
                    "error": error,
                },
                revision=expected_revision,
            )
            row = self._row(connection, decision_id)
            head = self.board._head_of(connection)
        self.board._notify(head)
        return self._request_response(connection=None, row=row, duplicate=False)

    def _queue_writer(self, connection: sqlite3.Connection, *, decision_id: str, expected_revision: int, now: str, timeout_seconds: int) -> tuple[str, int]:
        """Register a fair, fenced maintenance writer intent.

        The intent closes admission for new selection readers immediately. It is
        granted only after every already-admitted reader has settled, so the model
        never runs against a revision a human writer is still moving.
        """
        state = self._state(connection)
        sequence = int(state["writer_sequence"]) + 1
        writer_id = str(uuid.uuid4())
        token = self.db.writer_token(writer_id, sequence)
        expires_at = self.evaluation._plus(
            max(self.evaluation.writer_queue_seconds, timeout_seconds + WRITER_QUEUE_GRACE_SECONDS), now
        )
        connection.execute(
            "INSERT INTO evaluation_writers(writer_id, request_id, kind, state, generation, expected_revision,"
            " token_verifier, requested_at, granted_at, expires_at, released_at)"
            " VALUES(?,?,?,?,?,?,?,?,NULL,?,NULL)",
            (
                writer_id,
                f"decision:{decision_id}",
                "maintenance",
                "waiting",
                sequence,
                expected_revision,
                self.db.writer_token_verifier(token),
                now,
                expires_at,
            ),
        )
        connection.execute("UPDATE evaluation_state SET writer_sequence=?, updated_at=? WHERE id=1", (sequence, now))
        self._append_event(
            connection,
            "evaluation.writer_queued",
            decision_id,
            {"writerId": writer_id, "generation": sequence, "kind": "maintenance"},
            revision=expected_revision,
        )
        self.evaluation._promote(connection, now)
        return writer_id, sequence

    def _create_task(self, connection: sqlite3.Connection, *, decision_id: str, request_id: str, kind: str, timeout_seconds: int, now: str) -> str:
        """Admit one ordinary task for this decision, using the real queue and capacity."""
        task_id = str(uuid.uuid4())
        spec = {
            "adapter": DECISION_ADAPTER,
            # A private per-decision directory the decision adapter creates; it never
            # overlaps a project cwd, so a decision run consumes capacity without
            # reserving any workspace a business task needs.
            "cwd": str(self.board.directory / "decisions" / decision_id),
            "task": (
                "Bounded selection decision over the current evaluation table."
                if kind == "select"
                else "Bounded evaluation maintenance over the pending evidence batch."
            ),
            "timeoutSeconds": timeout_seconds + TIMEOUT_GRACE_SECONDS,
            "workspace": False,
            "requiredCapabilities": [DECISION_ADAPTER],
            "decision": {"decisionId": decision_id, "kind": kind, "timeoutSeconds": timeout_seconds},
        }
        fingerprint = schemas.spec_fingerprint(spec)
        blocker = self.board._admission_blocker(connection, spec)
        connection.execute(
            "INSERT INTO tasks(task_id, request_id, owner, spec_json, spec_canonical_json, input_fingerprint,"
            " fingerprint_version, adapter, required_capabilities, cwd, exclusive_resources, timeout_seconds,"
            " state, queue_reason, revision, created_at, updated_at)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                task_id,
                f"decision:{request_id}",
                "decision",
                canonical_json(spec),
                canonical_json(spec),
                fingerprint,
                schemas.FINGERPRINT_VERSION_CURRENT,
                DECISION_ADAPTER,
                canonical_json(spec["requiredCapabilities"]),
                spec["cwd"],
                canonical_json([]),
                spec["timeoutSeconds"],
                "queued",
                blocker or "awaiting-worker",
                1,
                now,
                now,
            ),
        )
        self.board._append_event(
            connection,
            "task.submitted",
            task_id=task_id,
            revision=1,
            payload={
                "requestId": f"decision:{request_id}",
                "adapter": DECISION_ADAPTER,
                "cwd": spec["cwd"],
                "queueReason": blocker or "awaiting-worker",
                "inputFingerprint": fingerprint,
                "decisionId": decision_id,
            },
        )
        return task_id

    # -- worker lifecycle hooks ---------------------------------------------
    def claim(self, connection: sqlite3.Connection, *, task: sqlite3.Row, spec: dict, attempt_id: str, generation: int, now: str) -> tuple[str | None, dict | None]:
        """Admit one decision attempt inside the claim transaction.

        Returns ``(blocker, input_document)``. A blocker keeps the task queued and
        consumes no execution slot; the queue reason is visible on the task view. On
        success the decision is ``running``, its reader or writer is bound to this
        attempt, and the exact model input is persisted for audit.
        """
        descriptor = decision_spec(spec) or {}
        decision_id = descriptor.get("decisionId")
        row = self._row(connection, decision_id) if decision_id else None
        if row is None:
            return "decision-missing", None
        if row["status"] != "queued":
            return "decision-closed", None
        request = json.loads(row["requested_json"])
        timeout = int(request.get("timeoutSeconds") or DEFAULT_TIMEOUT_SECONDS)
        if row["kind"] == "select":
            waiting = connection.execute(
                "SELECT COUNT(*) AS count FROM evaluation_writers WHERE state IN ('waiting','active')"
            ).fetchone()
            if int(waiting["count"]):
                return "evaluation-writer-pending", None
            profile_row, profile_reason = self._decision_profile(connection)
            if profile_row is None:
                self._finish(connection, row, status="needs-host", reason=profile_reason, now=now)
                self._cancel_queued_task(connection, task, now, "the decision had no compatible decision profile")
                return "decision-closed", None
            candidates = self._select_candidates(connection, request.get("requiredCapabilities") or [])
            if not candidates:
                self._finish(connection, row, status="needs-host", reason=NEEDS_HOST_NO_CANDIDATE, now=now)
                self._cancel_queued_task(connection, task, now, "the decision had no legal candidate profile")
                return "decision-closed", None
            table_revision = int(self._state(connection)["table_revision"])
            document, oversized = self._select_input(
                connection,
                request_id=row["request_id"],
                revision=table_revision,
                profile_row=profile_row,
                task_text=request.get("task") or "",
                candidates=candidates,
            )
            if document is None:
                self._finish(
                    connection,
                    row,
                    status="needs-host",
                    reason=oversized or (
                        "the bounded current table does not fit one decision input; curate the published cards and "
                        "evidence before asking for a selection"
                    ),
                    now=now,
                )
                self._cancel_queued_task(connection, task, now, "the bounded decision input would be oversized")
                return "decision-closed", None
            reader_id = self._admit_reader(connection, now, timeout_seconds=timeout)
            self._mark_running(
                connection, row, attempt_id=attempt_id, generation=generation, reader_id=reader_id,
                expected_revision=table_revision, document=document, now=now,
            )
            return None, document
        writer = connection.execute(
            "SELECT * FROM evaluation_writers WHERE writer_id=?", (row["writer_id"],)
        ).fetchone()
        if writer is None or writer["state"] in ("aborted", "expired", "published"):
            self._finish(
                connection,
                row,
                status="failed",
                reason="the maintenance writer grant expired or was released before this run could start",
                error="WRITER_NOT_ACTIVE",
                now=now,
            )
            self._cancel_queued_task(connection, task, now, "the maintenance writer grant is no longer valid")
            return "decision-closed", None
        if writer["state"] == "waiting":
            return "evaluation-writer-pending", None
        profile_row, profile_reason = self._decision_profile(connection)
        if profile_row is None:
            self._finish(connection, row, status="needs-host", reason=profile_reason, now=now)
            self._cancel_queued_task(connection, task, now, "the decision had no compatible decision profile")
            return "decision-closed", None
        table_revision = int(self._state(connection)["table_revision"])
        document, oversized, considered, remaining = self._maintain_input(
            connection, request_id=row["request_id"], revision=table_revision, profile_row=profile_row
        )
        if document is None:
            self._finish(
                connection,
                row,
                status="needs-host",
                reason=oversized or (
                    "no pending evidence remains for this maintenance batch; nothing was sent to a model"
                ),
                now=now,
            )
            self._cancel_queued_task(connection, task, now, "no maintainable evidence batch was available")
            return "decision-closed", None
        self._mark_running(
            connection, row, attempt_id=attempt_id, generation=generation, reader_id=None,
            expected_revision=table_revision, document=document, now=now,
            considered=considered, remaining=remaining, writer_generation=int(writer["generation"]),
        )
        return None, document

    def _mark_running(self, connection: sqlite3.Connection, row: sqlite3.Row, *, attempt_id: str, generation: int, reader_id: str | None, expected_revision: int, document: dict, now: str, considered: int = 0, remaining: int | None = None, writer_generation: int | None = None) -> None:
        digest = sha256_text(canonical_json(document))
        connection.execute(
            "UPDATE decision_requests SET attempt_id=?, generation=?, reader_id=?, expected_revision=?,"
            " input_json=?, input_sha256=?, considered_evidence=?, pending_after=?, writer_generation=?, updated_at=?"
            " WHERE decision_id=?",
            (
                attempt_id,
                generation,
                reader_id,
                expected_revision,
                canonical_json(document),
                digest,
                considered,
                remaining,
                writer_generation,
                now,
                row["decision_id"],
            ),
        )
        connection.execute(
            "UPDATE evaluation_decisions SET status='running', table_revision=?, reason=? WHERE decision_id=?",
            (
                expected_revision,
                (
                    "the maintenance writer grant is active on this complete revision"
                    if row["kind"] == "maintain"
                    else "the selection reader is admitted on this complete revision"
                ),
                row["decision_id"],
            ),
        )
        self._append_event(
            connection,
            "decision.started",
            row["decision_id"],
            {
                "attemptId": attempt_id,
                "generation": generation,
                "tableRevision": expected_revision,
                "inputSha256": digest,
            },
            revision=expected_revision,
        )

    def renew(self, connection: sqlite3.Connection, *, task: sqlite3.Row, now: str) -> None:
        """Renew this decision's reader or writer lease inside a worker renewal.

        Renewal keeps the fenced authority alive across one bounded model call; it
        never extends a decision whose attempt is already terminal.
        """
        spec = json.loads(task["spec_json"])
        descriptor = decision_spec(spec)
        if not descriptor:
            return
        row = self._row(connection, descriptor["decisionId"])
        if row is None or row["status"] != "running":
            return
        if row["reader_id"]:
            updated = connection.execute(
                "UPDATE evaluation_readers SET expires_at=? WHERE reader_id=? AND released_at IS NULL",
                (self.evaluation._plus(self.evaluation.reader_lease_seconds, now), row["reader_id"]),
            ).rowcount
            if updated:
                self._append_event(
                    connection, "evaluation.reader_renewed", row["decision_id"], {"readerId": row["reader_id"]}
                )
        if row["writer_id"]:
            connection.execute(
                "UPDATE evaluation_writers SET expires_at=? WHERE writer_id=? AND state='active'",
                (self.evaluation._plus(self.evaluation.writer_lease_seconds, now), row["writer_id"]),
            )

    def renew_open_writers(self, now: str) -> int:
        """Daemon sweep: keep live maintenance grants fenced instead of starving them.

        Waiting intents are renewed while their task is still queued and inside the
        decision's own bounded window; active grants are renewed only while the
        attempt that holds them is genuinely alive. A dead attempt's grant is left to
        expire, which reopens the gate instead of blocking every selector forever.
        """
        renewed = 0
        with self.board.db.write() as connection:
            rows = connection.execute(
                "SELECT r.decision_id, r.writer_id, r.attempt_id, r.created_at, r.expected_revision, r.requested_json,"
                " d.status, t.state AS task_state FROM decision_requests r"
                " JOIN evaluation_decisions d ON d.decision_id = r.decision_id"
                " LEFT JOIN tasks t ON t.task_id = r.task_id"
                " WHERE r.writer_id IS NOT NULL AND d.status IN ('queued','running')"
            ).fetchall()
            for row in rows:
                writer = connection.execute(
                    "SELECT * FROM evaluation_writers WHERE writer_id=?", (row["writer_id"],)
                ).fetchone()
                if writer is None or writer["state"] not in ("waiting", "active"):
                    continue
                if writer["state"] == "waiting":
                    if row["task_state"] != "queued":
                        continue
                    timeout = int(json.loads(row["requested_json"]).get("timeoutSeconds") or DEFAULT_TIMEOUT_SECONDS)
                    deadline = self.evaluation._plus(timeout + WRITER_QUEUE_GRACE_SECONDS * 2, row["created_at"])
                    if now > deadline:
                        continue
                    connection.execute(
                        "UPDATE evaluation_writers SET expires_at=? WHERE writer_id=?",
                        (self.evaluation._plus(self.evaluation.writer_queue_seconds, now), row["writer_id"]),
                    )
                else:
                    attempt = (
                        connection.execute(
                            "SELECT * FROM attempts WHERE attempt_id=?", (row["attempt_id"],)
                        ).fetchone()
                        if row["attempt_id"]
                        else None
                    )
                    if attempt is None or attempt["execution_state"] not in ("starting", "executing", "finalizing"):
                        continue
                    connection.execute(
                        "UPDATE evaluation_writers SET expires_at=? WHERE writer_id=?",
                        (self.evaluation._plus(self.evaluation.writer_lease_seconds, now), row["writer_id"]),
                    )
                renewed += 1
        return renewed

    def released(self, connection: sqlite3.Connection, *, task: sqlite3.Row, reason: str, now: str) -> dict | None:
        """The worker gave the attempt back before any model call happened."""
        row = self._decision_for_task(connection, task)
        if row is None:
            return None
        if row["status"] not in TERMINAL_DECISION_STATUSES:
            self._finish(
                connection,
                row,
                status="failed",
                reason="the worker released this decision attempt before the helper ran",
                error=reason[:500],
                now=now,
            )
        return {"decisionId": row["decision_id"], "status": self._status(connection, row["decision_id"])}

    def cancelled(self, connection: sqlite3.Connection, *, task: sqlite3.Row, reason: str, now: str) -> dict | None:
        """Durable cancel intent: the decision is cancelled, not silently forgotten."""
        row = self._decision_for_task(connection, task)
        if row is None:
            return None
        if row["status"] not in TERMINAL_DECISION_STATUSES:
            self._finish(
                connection,
                row,
                status="cancelled",
                reason=f"the decision run was cancelled: {reason}"[:MAX_DECISION_REASON],
                now=now,
            )
        return {"decisionId": row["decision_id"], "status": self._status(connection, row["decision_id"])}

    def fence_attempt(self, connection: sqlite3.Connection, *, attempt: sqlite3.Row, reason: str, now: str) -> None:
        """Fence a decision whose attempt became uncertain (restart or lease expiry).

        The result of a process this service no longer supervises may never become a
        current recommendation; it is recorded as ``stale`` and the reader or writer
        it held is released so the gate can move on.
        """
        task = connection.execute("SELECT * FROM tasks WHERE task_id=?", (attempt["task_id"],)).fetchone()
        if task is None:
            return
        row = self._decision_for_task(connection, task)
        if row is None:
            return
        if row["decision_attempt_id"] and row["decision_attempt_id"] != attempt["attempt_id"]:
            return
        if row["status"] not in TERMINAL_DECISION_STATUSES:
            self._finish(
                connection,
                row,
                status="stale",
                reason=(
                    f"{reason}; the helper's outcome is unknown to this service, so it cannot become a current "
                    "recommendation and no process is reported as stopped"
                ),
                now=now,
            )

    # -- completion ----------------------------------------------------------
    def complete(self, connection: sqlite3.Connection, *, task: sqlite3.Row, attempt: sqlite3.Row, status: str, result: Any, shutdown_confirmed: bool, error: str | None, now: str) -> dict | None:
        """Validate and publish (or refuse) one decision inside the result transaction.

        The publication runs under a savepoint: a validation failure rolls back to it
        and settles the decision honestly *inside the same transaction*, so one bad
        model output can never abort the worker's result commit and turn its durable
        receipt into an endless retry. The last complete evaluation revision stays
        byte-identical either way.
        """
        spec = json.loads(task["spec_json"])
        descriptor = decision_spec(spec)
        if not descriptor:
            return None
        row = self._row(connection, descriptor["decisionId"])
        if row is None:
            return None
        connection.execute("SAVEPOINT decision_publication")
        try:
            summary = self._complete(connection, row=row, task=task, attempt=attempt, status=status, result=result, shutdown_confirmed=shutdown_confirmed, error=error, now=now)
        except BoardError as failure:
            connection.execute("ROLLBACK TO decision_publication")
            connection.execute("RELEASE decision_publication")
            row = self._row(connection, descriptor["decisionId"])
            self._finish(
                connection,
                row,
                status="needs-host",
                now=now,
                reason=(
                    f"the model output could not be adopted ({failure.code}: {failure.message}); the last complete "
                    "revision is unchanged and the Host decides"
                ),
            )
            return {
                "decisionId": row["decision_id"],
                "kind": row["kind"],
                "status": "needs-host",
                "error": f"{failure.code}: {failure.message}"[:2000],
            }
        except Exception as failure:  # noqa: BLE001 - a decision bug must not poison the receipt
            connection.execute("ROLLBACK TO decision_publication")
            connection.execute("RELEASE decision_publication")
            row = self._row(connection, descriptor["decisionId"])
            self._finish(
                connection,
                row,
                status="failed",
                now=now,
                reason="the decision result could not be processed; the last complete revision is unchanged",
                error=f"{type(failure).__name__}: {failure}"[:2000],
            )
            return {
                "decisionId": row["decision_id"],
                "kind": row["kind"],
                "status": "failed",
                "error": f"{type(failure).__name__}: {failure}"[:2000],
            }
        connection.execute("RELEASE decision_publication")
        return summary

    def _complete(self, connection: sqlite3.Connection, *, row: sqlite3.Row, task: sqlite3.Row, attempt: sqlite3.Row, status: str, result: Any, shutdown_confirmed: bool, error: str | None, now: str) -> dict[str, Any]:
        summary: dict[str, Any] = {"decisionId": row["decision_id"], "kind": row["kind"], "status": row["status"]}
        output = result if isinstance(result, dict) else None
        if row["status"] in TERMINAL_DECISION_STATUSES:
            # A late or superseded result is retained for audit and never published.
            if output is not None:
                connection.execute(
                    "UPDATE decision_requests SET output_json=?, updated_at=? WHERE decision_id=?",
                    (canonical_json(output), now, row["decision_id"]),
                )
            summary["late"] = True
            summary["status"] = row["status"]
            return summary
        if row["decision_attempt_id"] != attempt["attempt_id"]:
            return summary
        helper_ok = (
            status == "ok"
            and shutdown_confirmed
            and isinstance(output, dict)
            and output.get("status") == "ok"
        )
        if status == "cancelled" or task["state"] == "cancelling":
            self._finish(
                connection, row, status="cancelled", output=output, now=now,
                reason="the decision run was cancelled; no recommendation is published",
            )
        elif not helper_ok:
            detail = error or (output or {}).get("error") or (output or {}).get("message") or "the decision helper failed"
            self._finish(
                connection, row, status="failed", output=output, now=now,
                reason="the bounded decision call did not produce a usable result",
                error=str(detail)[:2000],
            )
        elif output.get("operation") != row["kind"]:
            self._finish(
                connection, row, status="needs-host", output=output, now=now,
                reason="the helper answered a different operation than the one requested",
            )
        elif output.get("tableRevision") != row["expected_revision"]:
            self._finish(
                connection, row, status="stale", output=output, now=now,
                reason=(
                    "the helper answered for revision "
                    f"{output.get('tableRevision')} while this decision was admitted on revision "
                    f"{row['expected_revision']}; the result is retained but is not a current recommendation"
                ),
            )
        elif row["kind"] == "select":
            self._publish_select(connection, row, output=output, now=now)
        else:
            self._publish_maintain(connection, row, output=output, now=now)
        summary["status"] = self._status(connection, row["decision_id"])
        current = self._row(connection, row["decision_id"])
        if current is not None:
            summary["profileId"] = current["profile_id"]
            summary["reason"] = current["reason"]
            summary["publishedRevision"] = (
                int(json.loads(current["output_json"]).get("tableRevision"))
                if current["output_json"] and current["status"] == "completed" and current["kind"] == "maintain"
                else None
            )
        return summary

    def _publish_select(self, connection: sqlite3.Connection, row: sqlite3.Row, *, output: dict, now: str) -> None:
        """Validate one select recommendation against the frozen candidate set."""
        document = json.loads(row["input_json"]) if row["input_json"] else {}
        supplied = {
            profile["profileId"]: {
                evidence["evidenceId"]
                for evidence in document.get("evidence", [])
                if evidence.get("profileId") == profile["profileId"]
            }
            for profile in document.get("profiles", [])
        }
        decision = output.get("decision")
        if not isinstance(decision, dict):
            self._finish(
                connection, row, status="needs-host", output=output, now=now,
                reason="the helper returned no select decision; the Host decides",
            )
            return
        reason = self._bounded_text(decision.get("reason"), MAX_DECISION_REASON) or "the model recorded no reason"
        evidence_ids = decision.get("evidenceIds")
        if evidence_ids is None:
            evidence_ids = []
        if not isinstance(evidence_ids, list) or any(not isinstance(value, str) for value in evidence_ids):
            self._finish(
                connection, row, status="needs-host", output=output, now=now,
                reason="the recommendation carried malformed evidence references",
            )
            return
        if len(evidence_ids) > MAX_DECISION_EVIDENCE_IDS or len(set(evidence_ids)) != len(evidence_ids):
            # Never trimmed into shape: an out-of-bound or repeated reference list is
            # refused as returned, before any adoption.
            self._finish(
                connection, row, status="needs-host", output=output, now=now,
                reason=(
                    f"the recommendation carried {len(evidence_ids)} evidence references, above the "
                    f"{MAX_DECISION_EVIDENCE_IDS}-reference bound or with repeats; nothing was adopted"
                ),
            )
            return
        profile_id = decision.get("profileId")
        if profile_id is None:
            self._finish(connection, row, status="needs-host", output=output, now=now, reason=reason)
            return
        if not isinstance(profile_id, str) or profile_id not in supplied:
            self._finish(
                connection, row, status="needs-host", output=output, now=now,
                reason=f"the model recommended {profile_id!r}, which was not a legal candidate; the Host decides",
            )
            return
        current = connection.execute(
            "SELECT * FROM evaluation_profiles WHERE profile_id=?", (profile_id,)
        ).fetchone()
        if current is None or not current["enabled"] or not current["available"]:
            self._finish(
                connection, row, status="needs-host", output=output, now=now,
                reason=f"the recommended profile {profile_id} is no longer an enabled, available profile",
            )
            return
        foreign = [value for value in evidence_ids if value not in supplied[profile_id]]
        if foreign:
            self._finish(
                connection, row, status="needs-host", output=output, now=now,
                reason=(
                    f"the recommendation cited evidence {foreign[0]!r} that was not supplied for {profile_id}; "
                    "the Host decides"
                ),
            )
            return
        if not self._reader_open(connection, row, now):
            self._finish(
                connection, row, status="stale", output=output, now=now,
                reason=(
                    "the selection read was fenced before this result arrived (its bounded lease expired or a "
                    "writer drained it), so it is retained but is not a current recommendation"
                ),
            )
            return
        selected = next(
            (dict(profile) for profile in document.get("profiles", []) if profile.get("profileId") == profile_id),
            None,
        )
        self._finish(
            connection, row, status="completed", output=output, now=now,
            reason=reason, profile_id=profile_id, evidence_ids=evidence_ids, selected=selected,
        )

    def _publish_maintain(self, connection: sqlite3.Connection, row: sqlite3.Row, *, output: dict, now: str) -> None:
        """Validate one bounded card-only proposal, then adopt it or hand it back."""
        proposal = output.get("proposal")
        if not isinstance(proposal, dict):
            self._finish(
                connection, row, status="needs-host", output=output, now=now,
                reason="the helper returned no maintenance proposal; the Host decides",
            )
            return
        unexpected = sorted(set(proposal) - {"cards", "reason"})
        if unexpected:
            # Automatic adoption touches cards only: a proposal that carries
            # profiles, preferences, configuration or authority is refused whole.
            self._finish(
                connection, row, status="needs-host", output=output, now=now,
                reason=(
                    f"the proposal carries an unexpected field {unexpected[0]!r}; automatic adoption is limited to "
                    "cards and never changes profiles, preferences, configuration or authority"
                ),
            )
            return
        reason = self._bounded_text(proposal.get("reason"), MAX_DECISION_REASON) or "the model recorded no reason"
        cards = proposal.get("cards")
        if not isinstance(cards, list) or len(cards) > MAX_DECISION_CARDS:
            self._finish(
                connection, row, status="needs-host", output=output, now=now,
                reason="the proposal carried no usable card collection; nothing was adopted",
            )
            return
        document = json.loads(row["input_json"]) if row["input_json"] else {}
        supplied_profiles = {profile["profileId"] for profile in document.get("profiles", [])}
        supplied_evidence: dict[str, set[str]] = {}
        for evidence in document.get("evidence", []):
            supplied_evidence.setdefault(evidence["profileId"], set()).add(evidence["evidenceId"])
        current_cards = {
            card_row["profile_id"]: {
                # Exactly the publishable card fields: the derived revision, sample
                # count and timestamp belong to the read view, and republishing an
                # untouched card must not carry them back into the writer gate.
                "profileId": card_row["profile_id"],
                "summary": card_row["summary"],
                "strengths": json.loads(card_row["strengths_json"]),
                "limitations": json.loads(card_row["limitations_json"]),
                "risks": json.loads(card_row["risks_json"]),
                "evidenceIds": json.loads(card_row["evidence_ids_json"]),
            }
            for card_row in connection.execute("SELECT * FROM evaluation_cards")
        }
        merged: dict[str, dict] = dict(current_cards)
        seen: set[str] = set()
        for index, card in enumerate(cards):
            if not isinstance(card, dict):
                self._finish(connection, row, status="needs-host", output=output, now=now, reason=f"proposal.cards[{index}] is not an object")
                return
            unknown = sorted(set(card) - {"profileId", "summary", "strengths", "limitations", "risks", "evidenceIds"})
            if unknown:
                self._finish(
                    connection, row, status="needs-host", output=output, now=now,
                    reason=f"proposal.cards[{index}] tried to change {unknown[0]}; automatic adoption touches cards only",
                )
                return
            profile_id = card.get("profileId")
            if not isinstance(profile_id, str) or profile_id not in supplied_profiles or profile_id in seen:
                self._finish(
                    connection, row, status="needs-host", output=output, now=now,
                    reason=f"proposal.cards[{index}] refers to {profile_id!r}, which was not part of this bounded batch",
                )
                return
            seen.add(profile_id)
            summary = self._bounded_text(card.get("summary"), MAX_SUMMARY)
            if summary is None:
                self._finish(connection, row, status="needs-host", output=output, now=now, reason=f"proposal.cards[{index}] has no usable summary")
                return
            points: dict[str, list[str]] = {}
            for name in ("strengths", "limitations", "risks"):
                values = card.get(name, [])
                if not isinstance(values, list) or any(
                    not isinstance(value, str) or not value.strip() or len(value) > MAX_POINT for value in values
                ) or len(values) > MAX_CARD_POINTS:
                    self._finish(
                        connection, row, status="needs-host", output=output, now=now,
                        reason=f"proposal.cards[{index}].{name} is not a bounded text list",
                    )
                    return
                points[name] = [value.strip() for value in values]
            evidence_ids = card.get("evidenceIds", [])
            if (
                not isinstance(evidence_ids, list)
                or len(evidence_ids) > MAX_EVIDENCE_IDS
                or len(set(evidence_ids)) != len(evidence_ids)
                or any(not isinstance(value, str) for value in evidence_ids)
            ):
                self._finish(
                    connection, row, status="needs-host", output=output, now=now,
                    reason=(
                        f"proposal.cards[{index}].evidenceIds is not a bounded, de-duplicated identifier list of at "
                        f"most {MAX_EVIDENCE_IDS} real references"
                    ),
                )
                return
            prior = current_cards.get(profile_id)
            # The current card's own references plus this batch's supplied evidence for
            # that profile are the only real references automatic adoption may use.
            allowed_references = set(supplied_evidence.get(profile_id, set()))
            if prior is not None:
                allowed_references |= set(prior["evidenceIds"])
            fabricated = [value for value in evidence_ids if value not in allowed_references]
            if fabricated:
                self._finish(
                    connection, row, status="needs-host", output=output, now=now,
                    reason=(
                        f"proposal.cards[{index}] cites evidence {fabricated[0]!r} that was not supplied for "
                        f"{profile_id}; nothing was adopted"
                    ),
                )
                return
            if prior is not None:
                missing_risks = [risk for risk in prior["risks"] if risk not in points["risks"]]
                if missing_risks:
                    self._finish(
                        connection, row, status="needs-host", output=output, now=now,
                        reason=(
                            f"the proposal would drop the unresolved risk {missing_risks[0]!r} from {profile_id}; "
                            "removing an open risk needs an explicit Host decision"
                        ),
                    )
                    return
                missing_limitations = [item for item in prior["limitations"] if item not in points["limitations"]]
                if missing_limitations:
                    self._finish(
                        connection, row, status="needs-host", output=output, now=now,
                        reason=(
                            f"the proposal would drop the known limitation {missing_limitations[0]!r} from "
                            f"{profile_id}; automatic adoption preserves unresolved limitations. Old references may "
                            "be compacted, their text may not be cleared."
                        ),
                    )
                    return
            merged[profile_id] = {
                "profileId": profile_id,
                "summary": summary,
                "strengths": points["strengths"],
                "limitations": points["limitations"],
                "risks": points["risks"],
                "evidenceIds": evidence_ids,
            }
        # References this automatic publication retires are safely compacted: the
        # risk and limitation text they supported is still on the card, and every
        # retired id stays archived. They are recorded for audit and are *not*
        # requeued as pending, which keeps "pending" meaning "not yet processed".
        retired = sorted(
            {
                evidence_id
                for profile_id, prior in current_cards.items()
                if profile_id in seen
                for evidence_id in prior["evidenceIds"]
                if evidence_id not in set(merged[profile_id]["evidenceIds"])
            }
        )
        incorporated = sorted(
            {
                evidence_id
                for profile_id in seen
                for evidence_id in merged[profile_id]["evidenceIds"]
                if connection.execute(
                    "SELECT 1 FROM evaluation_evidence_pending WHERE evidence_id=?", (evidence_id,)
                ).fetchone()
                is not None
            }
        )
        normalized = {
            "cards": [merged[key] for key in sorted(merged)],
            "reason": reason,
            "basisRevision": int(row["expected_revision"]),
            "retiredEvidenceIds": retired,
            # Only the cited rows that were actually pending at publication time are
            # consumed; batch rows the model omitted stay pending and visible.
            "incorporatedEvidenceIds": incorporated,
        }
        changed = merged != current_cards
        writer = connection.execute(
            "SELECT * FROM evaluation_writers WHERE writer_id=?", (row["writer_id"],)
        ).fetchone()
        current_revision = int(self._state(connection)["table_revision"])
        if writer is None or writer["state"] != "active" or int(writer["generation"]) != int(row["writer_generation"] or -1):
            self._finish(
                connection, row, status="stale", output=output, now=now,
                reason="the fenced maintenance writer authority is no longer active; the old table stays intact",
                proposal=normalized,
            )
            return
        if current_revision != int(row["expected_revision"]):
            self._finish(
                connection, row, status="stale", output=output, now=now,
                reason=(
                    f"the evaluation table moved from revision {row['expected_revision']} to {current_revision} "
                    "while this proposal ran; the old table stays intact and the proposal is retained"
                ),
                proposal=normalized,
            )
            return
        if not bool(self._state(connection)["auto_maintain"]):
            self._finish(
                connection, row, status="needs-host", output=output, now=now,
                reason=(
                    "configuration.autoMaintain is false: the validated card-only proposal is retained for explicit "
                    "Host inspection and nothing was published"
                ),
                proposal=normalized,
            )
            return
        if not changed:
            # A valid proposal that changes nothing settles as a completed no-op
            # rather than a failure; any evidence it declined to incorporate stays
            # pending and is reported as remaining.
            self._finish(
                connection, row, status="completed", output=output, now=now,
                reason=f"no card change was needed: {reason}",
                proposal=normalized,
                published_revision=None,
            )
            return
        published = self.evaluation._publish_revision(
            connection,
            revision=current_revision + 1,
            writer=writer,
            now=now,
            params={"cards": normalized["cards"]},
            keep_consumed=frozenset(retired),
        )
        connection.execute(
            "UPDATE evaluation_writers SET state='published', released_at=? WHERE writer_id=?",
            (now, writer["writer_id"]),
        )
        self.board._append_event(
            connection,
            "evaluation.published",
            revision=published["revision"],
            payload={
                "writerId": writer["writer_id"],
                "generation": int(writer["generation"]),
                "kind": writer["kind"],
                "counts": published["counts"],
                "decisionId": row["decision_id"],
                "basisRevision": int(row["expected_revision"]),
                "retiredEvidenceIds": retired,
            },
        )
        self.evaluation._promote(connection, now)
        connection.execute(
            "UPDATE evaluation_decisions SET table_revision=? WHERE decision_id=?",
            (published["revision"], row["decision_id"]),
        )
        self._finish(
            connection, row, status="completed", output=output, now=now,
            reason=reason, proposal=normalized, published_revision=published["revision"],
        )

    # -- terminal helpers ----------------------------------------------------
    @staticmethod
    def _reader_open(connection: sqlite3.Connection, row: sqlite3.Row, now: str) -> bool:
        if not row["reader_id"]:
            return False
        reader = connection.execute(
            "SELECT * FROM evaluation_readers WHERE reader_id=?", (row["reader_id"],)
        ).fetchone()
        return bool(reader is not None and reader["released_at"] is None and reader["expires_at"] > now)

    def _decision_for_task(self, connection: sqlite3.Connection, task: sqlite3.Row) -> sqlite3.Row | None:
        spec = json.loads(task["spec_json"])
        descriptor = decision_spec(spec)
        if not descriptor:
            return None
        return self._row(connection, descriptor["decisionId"])

    def _status(self, connection: sqlite3.Connection, decision_id: str) -> str:
        return connection.execute(
            "SELECT status FROM evaluation_decisions WHERE decision_id=?", (decision_id,)
        ).fetchone()["status"]

    def _finish(self, connection: sqlite3.Connection, row: sqlite3.Row, *, status: str, now: str, reason: str | None = None, error: str | None = None, profile_id: str | None = None, evidence_ids: list[str] | None = None, output: dict | None = None, proposal: dict | None = None, published_revision: int | None = None, selected: dict | None = None) -> None:
        """One atomic terminal transition for a decision and its audit material."""
        if status not in DECISION_STATUSES:
            raise BoardError("INTERNAL_ERROR", f"Unknown decision status {status!r}")
        connection.execute(
            "UPDATE evaluation_decisions SET status=?, reason=?, profile_id=?, evidence_ids_json=?, error=?"
            " WHERE decision_id=?",
            (
                status,
                (reason or "")[:MAX_DECISION_REASON],
                profile_id,
                canonical_json(evidence_ids or []),
                error,
                row["decision_id"],
            ),
        )
        connection.execute(
            "UPDATE decision_requests SET output_json=COALESCE(?, output_json),"
            " proposal_json=COALESCE(?, proposal_json), selected_json=COALESCE(?, selected_json),"
            " pending_after=?, published_revision=?, updated_at=? WHERE decision_id=?",
            (
                canonical_json(output) if output is not None else None,
                canonical_json(proposal) if proposal is not None else None,
                canonical_json(selected) if selected is not None else None,
                self.evaluation._pending_evidence(connection),
                published_revision,
                now,
                row["decision_id"],
            ),
        )
        self._release_reader(connection, row, now)
        # A terminal decision never keeps a writer grant it cannot use: a waiting
        # selector is promoted instead of queueing behind a finished decision.
        self._release_writer(connection, row, now)
        event = {
            "completed": "decision.completed",
            "needs-host": "decision.needs_host",
            "failed": "decision.failed",
            "cancelled": "decision.cancelled",
            "stale": "decision.stale",
        }[status]
        self._append_event(
            connection,
            event,
            row["decision_id"],
            {
                "status": status,
                "profileId": profile_id,
                "reason": (reason or "")[:MAX_DECISION_REASON],
                "error": error,
                "tableRevision": int(self._state(connection)["table_revision"]),
            },
            revision=int(self._state(connection)["table_revision"]),
        )

    def _cancel_queued_task(self, connection: sqlite3.Connection, task: sqlite3.Row, now: str, reason: str) -> None:
        """Cancel one decision task that provably never started."""
        if task["state"] != "queued":
            return
        self.board._transition_task(connection, task, "cancelled")
        connection.execute(
            "UPDATE tasks SET queue_reason=NULL, updated_at=? WHERE task_id=?", (now, task["task_id"])
        )
        self.board._append_event(
            connection,
            "task.cancelled",
            task_id=task["task_id"],
            revision=task["revision"] + 1,
            payload={"reason": reason, "actor": "service", "phase": "queued"},
        )

    # -- views ---------------------------------------------------------------
    def _request_response(self, connection: sqlite3.Connection | None, row: sqlite3.Row, *, duplicate: bool) -> dict:
        view = self._view(connection, row, include_audit=False)
        return {
            "decision": view,
            "decisionId": row["decision_id"],
            "status": row["status"],
            "runId": row["decision_task_id"],
            "duplicate": duplicate,
        }

    def get(self, params: dict) -> dict:
        """One decision. Compact by default; ``includeAudit=true`` adds the audit.

        The compact summary is the low-token Host path: it carries the frozen
        selected worker configuration and the decision's own model identity as
        separate keys, and never the persisted model input, the helper envelope or
        the bounded table collections.
        """
        schemas.reject_unknown(params, GET_FIELDS, "selection.get")
        decision_id = schemas.required_string(
            params, "decisionId", max_length=128, pattern=schemas.IDENTIFIER_PATTERN
        )
        include_audit = schemas.optional_bool(params, "includeAudit", False)
        with self.board.db.read() as connection:
            row = self._row(connection, decision_id)
            if row is None:
                raise BoardError("NOT_FOUND", "Unknown decisionId", decisionId=decision_id)
            return {"decision": self._view(connection, row, include_audit=include_audit)}

    def _view(self, connection: sqlite3.Connection | None, row: sqlite3.Row, *, include_audit: bool) -> dict:
        output = json.loads(row["output_json"]) if row["output_json"] else None
        request = json.loads(row["requested_json"]) if row["requested_json"] else {}
        selected = json.loads(row["selected_json"]) if row["selected_json"] else None
        view: dict[str, Any] = {
            "decisionId": row["decision_id"],
            "requestId": row["request_id"],
            "kind": row["kind"],
            "status": row["status"],
            "task": row["task"],
            "runId": row["decision_task_id"],
            "profileId": row["profile_id"],
            # The frozen execution identity the Host can delegate with directly,
            # captured from the admitted revision. ``decisionModel`` is the decision
            # call's own requested/resolved identity and is deliberately separate.
            "selectedProfile": selected,
            "decisionModel": {
                "requested": (output or {}).get("requested"),
                "resolved": (output or {}).get("resolved"),
                "observed": (output or {}).get("observed"),
            },
            "tableRevision": int(row["table_revision"]),
            "expectedRevision": int(row["expected_revision"]),
            "publishedRevision": (
                int(row["published_revision"]) if row["published_revision"] is not None else None
            ),
            "noOp": bool(row["status"] == "completed" and row["published_revision"] is None and row["kind"] == "maintain"),
            "reason": row["reason"],
            "evidenceIds": json.loads(row["evidence_ids_json"]),
            "createdAt": row["created_at"],
            "updatedAt": row["request_updated_at"],
        }
        if row["error"]:
            view["error"] = row["error"]
        view["pendingEvidenceRemaining"] = (
            int(row["pending_after"])
            if row["pending_after"] is not None
            else (self.evaluation._pending_evidence(connection) if connection is not None else None)
        )
        if include_audit:
            proposal = json.loads(row["proposal_json"]) if row["proposal_json"] else None
            input_document = json.loads(row["input_json"]) if row["input_json"] else None
            view.update(
                {
                    "attemptId": row["decision_attempt_id"],
                    "generation": row["decision_generation"],
                    "configurationRevision": int(row["configuration_revision"]),
                    "readerId": row["reader_id"],
                    "writerId": row["writer_id"],
                    "writerGeneration": row["writer_generation"],
                    "requested": request,
                    "autoPublish": bool(row["auto_publish"]),
                    # The exact bounded fields sent to the model, retained for audit.
                    # It carries the published table snapshot and bounded evidence
                    # summaries only: no credentials and no raw project logs.
                    "input": input_document,
                    "inputSha256": row["input_sha256"],
                    "output": output,
                    "proposal": proposal,
                    # The decision call's own configuration, kept separate from the
                    # selected worker configuration above.
                    "requestedProfile": (output or {}).get("requested"),
                    "resolvedProfile": (output or {}).get("resolved"),
                    "observedProfile": (output or {}).get("observed"),
                    "usage": (output or {}).get("usage"),
                    "elapsedSeconds": (output or {}).get("elapsedSeconds"),
                    "helperShutdownConfirmed": (output or {}).get("shutdownConfirmed"),
                    "consideredEvidence": int(row["considered_evidence"]),
                }
            )
        return view


__all__ = ["DECISION_ADAPTER", "DECISION_STATUSES", "DecisionCoordinator", "decision_spec"]
