"""Durable decision orchestration over the real Worker/attempt/capacity system.

A *selection* recommendation is not a second execution engine. The service records a
decision, admits the selection reader lease it needs, and then creates one ordinary
task whose adapter is the narrow :mod:`buddy.adapters.decision` helper. The existing
independent worker claims it, consumes real ``BUDDY_MAX_CONCURRENT`` capacity, owns
the helper process handle, enforces the deadline and reports a durable receipt.
Python remains the only writer of authoritative state: the decision row and the
reader release are committed inside the *same* transaction as the worker's result,
never as a side effect of a GET.

Evaluation maintenance is **not** executed here. An external Harness prepares
bounded facts through ``EvaluationStore.prepare``, synthesizes card text under the
buddy skill, and commits a card-only patch through the ordinary evaluation writer
gate. Historical ``kind='maintain'`` decisions remain read-only history through
``selection_get``/``selection_list``; no maintenance request spawns an internal
decision task, holds a writer lease or calls a model.

The split of responsibilities is deliberate:

* selection readers are admitted only when a queued selection can actually execute,
  on a complete current revision, and are fenced with the attempt lifecycle;
* every helper output is untrusted input: candidates and evidence references are
  re-validated in Python before one atomic recommendation, and anything outside the
  bounded policy becomes ``needs-host``.
"""
from __future__ import annotations

from contextlib import nullcontext

import json
import sqlite3
import uuid
from typing import Any

from . import schemas
from . import selection_policy
from .db import canonical_json, sha256_text
from .errors import BoardError

DECISION_ADAPTER = "decision"
#: ``maintain`` stays a readable historical kind; it is never created any more.
DECISION_KINDS = ("select", "maintain")
#: Durable decision states. ``needs-host`` is the honest abstention/out-of-policy
#: outcome; ``stale`` is a fenced result that may not become a current recommendation.
DECISION_STATUSES = ("queued", "running", "completed", "needs-host", "failed", "cancelled", "stale")
TERMINAL_DECISION_STATUSES = frozenset({"completed", "needs-host", "failed", "cancelled", "stale"})

SELECT_FIELDS = frozenset({"requestId", "task", "requiredCapabilities", "timeoutSeconds", "routingPreferences"})
GET_FIELDS = frozenset({"decisionId", "includeAudit"})
LIST_FIELDS = frozenset({"kind", "limit", "before"})
DEFAULT_LIST_LIMIT = 20
MAX_LIST_LIMIT = 100

#: The shared row projection for one decision. The paginated list reader adds
#: ``decision_requests.rowid`` as its stable keyset; nothing here persists anything.
_ROW_COLUMNS = (
    "d.decision_id, d.status, d.task, d.profile_id, d.table_revision, d.reason, d.evidence_ids_json,"
    " d.created_at, d.error, r.request_id, r.kind, r.input_fingerprint, r.configuration_revision,"
    " r.task_id AS decision_task_id, r.attempt_id AS decision_attempt_id,"
    " r.generation AS decision_generation, r.reader_id, r.writer_id, r.writer_generation,"
    " r.expected_revision, r.published_revision, r.selected_json, r.input_json, r.input_sha256, r.output_json, r.proposal_json,"
    " r.considered_evidence, r.pending_after, r.auto_publish, r.requested_json,"
    " r.created_at AS request_created_at, r.updated_at AS request_updated_at"
)
_ROW_FROM = " FROM decision_requests r JOIN evaluation_decisions d ON d.decision_id = r.decision_id"

MAX_DECISION_TASK_BYTES = 8192
MAX_DECISION_REASON = 2000
MAX_DECISION_EVIDENCE_IDS = 32
#: Harness bounds mirrored from the decision helper's request validation. They are
#: the hard ceiling for one bounded selection request, never a silent truncation
#: point: a complete current table above them becomes an explicit needs-host outcome.
MAX_DECISION_PROFILES = 200
MAX_DECISION_CARDS = 512
MAX_DECISION_PREFERENCES = 256
MAX_DECISION_EVIDENCE = 512
MAX_DECISION_ANNOTATIONS = 200
MAX_DECISION_INPUT_BYTES = 128 * 1024
MIN_TIMEOUT_SECONDS = 5
#: The helper refuses a value above 1800, so the service never forwards one.
MAX_TIMEOUT_SECONDS = 1800
DEFAULT_TIMEOUT_SECONDS = 300
#: Extra room after the helper's own bound: the worker deadline must not fire while
#: the helper is still shutting its detached child down.
TIMEOUT_GRACE_SECONDS = 10

NEEDS_HOST_NO_PROFILE = (
    "no compatible fixed decision profile is configured; publish an available, enabled decision-capable profile as "
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
            f"SELECT {_ROW_COLUMNS}{_ROW_FROM} WHERE r.decision_id=?",
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

    def selector_family(self, connection: sqlite3.Connection) -> tuple[str, str, str] | None:
        """The model family the fixed decision profile will actually consume.

        Resolved inside the caller's claim transaction from the same configured
        profile :meth:`claim` admits, so the store can quota-check the selector's
        own family *before* the claim commits and before the selection reader is
        granted, and freeze that exact tuple onto the routing attempt.
        """
        profile_row, _reason = self._decision_profile(connection)
        if profile_row is None:
            return None
        return (profile_row["adapter"], profile_row["provider"], profile_row["model"])

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
        from .adapters import adapter
        try:
            implementation = adapter(row["adapter"])
        except BoardError:
            return None, f"the configured decision profile {profile_id} has no installed native adapter"
        if not implementation.decision_execution:
            return None, f"the configured decision profile {profile_id} has no verified tool-free decision capability"
        usable, reason = implementation.decision_available()
        if not usable:
            return None, reason or f"the configured decision profile {profile_id} is unavailable for decision execution"
        if not row["provider"] or not row["model"] or not row["effort"]:
            return None, f"the configured decision profile {profile_id} has no complete provider/model/effort identity"
        return row, None

    @staticmethod
    def _select_candidates(connection: sqlite3.Connection, required_capabilities: list[str], *, constraints: dict | None = None, coding_only: bool = False) -> list[sqlite3.Row]:
        """Legal candidates: enabled, available, capability-matching, pinned and not excluded.

        Pin/exclude is enforced here in Python and never delegated to the model;
        ``prefer`` stays a soft ordering hint that only enters the bounded input.
        """
        clauses = [
            "p.enabled=1", "p.available=1", "COALESCE(f.mode,'')!='exclude'",
            "(NOT EXISTS(SELECT 1 FROM evaluation_preferences WHERE mode='pin') OR f.mode='pin')",
        ]
        values = []
        for capability in sorted(set(required_capabilities)):
            clauses.append("EXISTS(SELECT 1 FROM json_each(p.capabilities_json) WHERE value=?)")
            values.append(capability)
        if coding_only:
            clauses.append("p.adapter IN (" + ",".join("?" for _ in schemas.CODING_ADAPTERS) + ")")
            values.extend(schemas.CODING_ADAPTERS)
        for key, value in (constraints or {}).items():
            if key not in schemas.CONFIGURATION_FIELDS:
                raise BoardError("INVALID_ARGUMENT", "Unknown configuration constraint")
            clauses.append(f"p.{key}=?")
            values.append(value)
        values.append(MAX_DECISION_PROFILES + 1)
        return list(connection.execute(
            "SELECT p.* FROM evaluation_profiles p LEFT JOIN evaluation_preferences f ON f.profile_id=p.profile_id "
            "WHERE " + " AND ".join(clauses) + " ORDER BY p.rowid LIMIT ?", values))

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
            "origin": row["origin"],
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
                for row in connection.execute(f"SELECT * FROM evaluation_preferences WHERE profile_id IN ({markers}) ORDER BY rowid", profile_ids)
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
            "annotations": [
                {"profileId": row["profile_id"], "text": row["text"],
                 "revision": int(row["revision"]), "updatedAt": row["updated_at"]}
                for row in connection.execute(
                    f"SELECT profile_id,text,revision,updated_at FROM evaluation_annotations WHERE profile_id IN ({markers}) ORDER BY profile_id",
                    profile_ids,
                )
            ],
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
            ("annotations", MAX_DECISION_ANNOTATIONS),
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
                "adapter": profile_row["adapter"],
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

    def _select_input(self, connection: sqlite3.Connection, *, request_id: str, revision: int, profile_row: sqlite3.Row, task_text: str, candidates: list[sqlite3.Row], routing_preferences: list[dict] | None = None, hard_constraints: dict | None = None) -> tuple[dict | None, str | None]:
        if len(candidates) > MAX_DECISION_PROFILES:
            return None, "The legal candidate set exceeds the bounded decision profile limit; narrow the task constraints"
        candidate_ids = {candidate["profile_id"] for candidate in candidates}
        markers = ",".join("?" for _ in candidate_ids) or "NULL"
        cards = [
            self._card_input(row)
            for row in connection.execute(f"SELECT * FROM evaluation_cards WHERE profile_id IN ({markers}) ORDER BY rowid", tuple(candidate_ids))
        ]
        evidence = self._referenced_evidence(connection, cards)
        table = self._table_input(
            connection, profiles=candidates, evidence_rows=evidence, include_preferences=True
        )
        table["routingPreferences"] = routing_preferences or []
        # The program-computed preference truth for this request. It is request-local
        # (it depends on this candidate slice and this task's preferences), so it is
        # part of the variable suffix the model sees after the stable table snapshot.
        table["policyFacts"] = selection_policy.policy_facts(
            profiles=table["profiles"],
            routing_preferences=table["routingPreferences"],
            prefer_profile_ids=[
                entry["profileId"] for entry in table["preferences"] if entry["mode"] == "prefer"
            ],
            hard_constraints=hard_constraints or {},
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
        """Release a historical maintenance decision's writer grant, if one is open.

        New decisions never take writer authority, but a ``kind='maintain'`` row
        created by an earlier build may still reference a grant; closing it here is
        what keeps a historical record from blocking the gate forever.
        """
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
        routing_preferences = schemas.normalize_routing_preferences(params.get("routingPreferences", []))
        timeout = schemas.optional_int(
            params, "timeoutSeconds", DEFAULT_TIMEOUT_SECONDS, MIN_TIMEOUT_SECONDS, MAX_TIMEOUT_SECONDS
        )
        request = {
            "kind": "select",
            "task": task_text,
            "requiredCapabilities": capabilities,
            "routingPreferences": routing_preferences,
            "timeoutSeconds": timeout,
        }
        return self._create(request_id, request)

    def _create(self, request_id: str, request: dict, *, connection=None, needs_host_reason: str | None = None) -> dict:
        fingerprint = sha256_text(canonical_json(request))
        kind = request["kind"]
        owns_transaction = connection is None
        with (self.board.db.write() if owns_transaction else nullcontext(connection)) as connection:
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
            available, unavailable_reason = self._adapter_available() if needs_host_reason is None else (True, None)
            profile_row, profile_reason = self._decision_profile(connection) if needs_host_reason is None else (None, None)
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
            # New decisions are always selection requests; there is no internal
            # maintenance request path left on the blackboard.
            reason = "queued for a worker; the selection reader is admitted when the run starts"
            error: str | None = None
            task_text = request.get("task") or ""
            expected_revision = int(state["table_revision"])
            if needs_host_reason is not None:
                status = "needs-host"
                reason = needs_host_reason
            elif not available:
                status = "failed"
                error = f"ADAPTER_UNAVAILABLE: {unavailable_reason or 'the bounded decision helper is unavailable'}"
                reason = "the bounded decision helper is not available in this build, so no model call was made"
            elif profile_row is None and not writer_pending:
                status = "needs-host"
                reason = profile_reason or NEEDS_HOST_NO_PROFILE
            elif (
                not writer_pending
                and not self._select_candidates(connection, request["requiredCapabilities"],
                                                constraints=request.get("constraints"),
                                                coding_only=bool(request.get("workflowRouting")))
            ):
                status = "needs-host"
                reason = NEEDS_HOST_NO_CANDIDATE
            create_task = status == "queued"
            task_id = None
            if create_task:
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
                    0,
                    now,
                    now,
                ),
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
        if owns_transaction:
            self.board._notify(head)
        return self._request_response(connection=None if owns_transaction else connection, row=row, duplicate=False)

    def route_workflow(self, connection, *, run_id: str, sequence: int, spec: dict) -> dict:
        """Admit a fixed-selector task with the Goal in its existing transaction.

        The selector itself is always the configured decision profile. Only the
        bounded business candidate set is filtered; no recursive workflow submit
        and no provider/model call occurs during admission.
        """
        task_text = spec["task"]
        task_bytes = len(task_text.encode("utf-8"))
        request = {
            "kind": "select", "task": task_text,
            "requiredCapabilities": spec.get("requiredCapabilities", []),
            "constraints": schemas.configuration_constraints(spec),
            "routingPreferences": spec.get("routingPreferences", []),
            "workflowRouting": True, "timeoutSeconds": DEFAULT_TIMEOUT_SECONDS,
        }
        needs_host_reason = None
        if task_bytes > MAX_DECISION_TASK_BYTES:
            # The immutable Goal owns the full text. A selector cannot choose from
            # an incomplete objective, and its bounded audit stores only a reference.
            request["task"] = f"Goal {run_id} (full task retained in workflow)"
            request["taskReference"] = {"runId": run_id, "bytes": task_bytes, "sha256": sha256_text(task_text)}
            needs_host_reason = (
                f"The complete Goal contains {task_bytes} UTF-8 bytes, above the selector's "
                f"{MAX_DECISION_TASK_BYTES}-byte task limit. No model call was made. "
                "Continue the same Goal with a complete configuration; its full objective remains unchanged."
            )
        return self._create(f"workflow:{run_id}:{sequence}", request, connection=connection,
                            needs_host_reason=needs_host_reason)

    def _create_task(self, connection: sqlite3.Connection, *, decision_id: str, request_id: str, kind: str, timeout_seconds: int, now: str) -> str:
        """Admit one ordinary selection task, using the real queue and capacity."""
        task_id = str(uuid.uuid4())
        spec = {
            "adapter": DECISION_ADAPTER,
            # A private per-decision directory the decision adapter creates; it never
            # overlaps a project cwd, so a decision run consumes capacity without
            # reserving any workspace a business task needs.
            "cwd": str(self.board.directory / "decisions" / decision_id),
            "task": "Bounded selection decision over the current evaluation table.",
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
        if row["kind"] != "select":
            # Historical maintenance decisions are never executed by the blackboard.
            # A stray row from an earlier build settles honestly instead of calling a
            # model, and its retained proposal stays readable through selection_get.
            self._finish(
                connection,
                row,
                status="needs-host",
                reason=(
                    "this is a historical maintenance decision; the blackboard no longer executes maintenance "
                    "model calls. Prepare bounded facts with evaluation_prepare and publish cards through the "
                    "ordinary writer gate instead."
                ),
                now=now,
            )
            self._cancel_queued_task(connection, task, now, "maintenance decisions are not executed by the blackboard")
            return "decision-closed", None
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
        candidates = self._select_candidates(connection, request.get("requiredCapabilities") or [],
                                             constraints=request.get("constraints"),
                                             coding_only=bool(request.get("workflowRouting")))
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
            routing_preferences=request.get("routingPreferences", []),
            hard_constraints=request.get("constraints") or {},
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

    def _mark_running(self, connection: sqlite3.Connection, row: sqlite3.Row, *, attempt_id: str, generation: int, reader_id: str | None, expected_revision: int, document: dict, now: str, considered: int = 0, remaining: int | None = None) -> None:
        digest = sha256_text(canonical_json(document))
        connection.execute(
            "UPDATE decision_requests SET configuration_revision=?, attempt_id=?, generation=?, reader_id=?, expected_revision=?,"
            " input_json=?, input_sha256=?, considered_evidence=?, pending_after=?, updated_at=?"
            " WHERE decision_id=?",
            (
                int(self._state(connection)["configuration_revision"]),
                attempt_id,
                generation,
                reader_id,
                expected_revision,
                canonical_json(document),
                digest,
                considered,
                remaining,
                now,
                row["decision_id"],
            ),
        )
        connection.execute(
            "UPDATE evaluation_decisions SET status='running', table_revision=?, reason=? WHERE decision_id=?",
            (
                expected_revision,
                "the selection reader is admitted on this complete revision",
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
        """Renew this decision's selection reader inside a worker renewal.

        Renewal keeps the fenced reader authority alive across one bounded model call;
        it never extends a decision whose attempt is already terminal.
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
        policy_code = (
            output.get("code")
            if isinstance(output, dict)
            and output.get("status") == "error"
            and isinstance(output.get("code"), str)
            and output.get("code").startswith("policy-")
            else None
        )
        if status == "cancelled" or task["state"] == "cancelling":
            self._finish(
                connection, row, status="cancelled", output=output, now=now,
                reason="the decision run was cancelled; no recommendation is published",
            )
        elif policy_code is not None:
            # A routing-policy refusal is a Host boundary on the same goal, not a
            # failure to retry: the helper answered, the answer is out of policy,
            # and the Worker's own receipt keeps its real outcome.
            self._finish(
                connection, row, status="needs-host", output=output, now=now,
                reason=(
                    f"the decision helper refused the recommendation under the routing policy "
                    f"({policy_code}); the Host decides on the same goal"
                ),
                error=f"{policy_code}: routing policy boundary"[:2000],
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
            # A historical maintenance result is retained for audit and never adopted
            # by the blackboard; only the external Harness publishes card patches now.
            self._finish(
                connection, row, status="needs-host", output=output, now=now,
                reason=(
                    "a historical maintenance result is retained as read-only history; the blackboard no longer "
                    "adopts maintenance proposals"
                ),
            )
        summary["status"] = self._status(connection, row["decision_id"])
        current = self._row(connection, row["decision_id"])
        if current is not None:
            summary["profileId"] = current["profile_id"]
            summary["reason"] = current["reason"]
            summary["publishedRevision"] = (
                int(current["published_revision"])
                if current["published_revision"] is not None
                else None
            )
        return summary

    def _publish_select(self, connection: sqlite3.Connection, row: sqlite3.Row, *, output: dict, now: str) -> None:
        """Validate one select recommendation against the frozen candidate set.

        The model's typed ``policyCheck`` and ``support`` are re-validated here
        against this service's own immutable input — independently of the helper's
        Node-side check — before adoption. Any policy violation settles
        ``needs-host`` on the same goal with the precise machine code; nothing is
        retried and the qualitative reason text is never parsed.
        """
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
        if set(decision) != {"profileId", "reason", "evidenceIds", "policyCheck", "support"}:
            # The current strict shape only; there is no legacy result fallback.
            self._finish(
                connection, row, status="needs-host", output=output, now=now,
                reason=(
                    "the recommendation does not carry the current decision shape "
                    "{profileId, reason, evidenceIds, policyCheck, support}; nothing was adopted"
                ),
            )
            return
        reason = self._bounded_text(decision.get("reason"), MAX_DECISION_REASON) or "the model recorded no reason"
        evidence_ids = decision.get("evidenceIds")
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
            _, _, failure = selection_policy.validate_decision(decision, {}, [], set(), set())
            if failure is not None:
                self._finish(
                    connection, row, status="needs-host", output=output, now=now,
                    reason=f"the abstention violated the routing policy ({failure[0]}: {failure[1]}); the Host decides",
                )
                return
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
        routing_preferences = document.get("routingPreferences") or []
        expected_facts = selection_policy.policy_facts(
            profiles=document.get("profiles") or [],
            routing_preferences=routing_preferences,
            prefer_profile_ids=[
                entry["profileId"]
                for entry in document.get("preferences") or []
                if isinstance(entry, dict) and entry.get("mode") == "prefer"
            ],
            # Re-derived from the frozen request, independent of the input document's
            # own stored copy: an invented or drifted constraint cannot pass here.
            hard_constraints=json.loads(row["requested_json"]).get("constraints") or {},
        )
        if document.get("policyFacts") != expected_facts:
            self._finish(
                connection, row, status="needs-host", output=output, now=now,
                reason=(
                    "the frozen decision input carries policy facts that disagree with its own bounded table "
                    "and request; nothing was adopted and the Host decides"
                ),
            )
            return
        card_profile_ids = {
            entry.get("profileId") for entry in document.get("cards") or [] if isinstance(entry, dict)
        }
        annotation_profile_ids = {
            entry.get("profileId") for entry in document.get("annotations") or [] if isinstance(entry, dict)
        }
        policy_check, _, failure = selection_policy.validate_decision(
            decision, expected_facts, routing_preferences, card_profile_ids, annotation_profile_ids
        )
        if failure is not None:
            self._finish(
                connection, row, status="needs-host", output=output, now=now,
                reason=(
                    f"the recommendation violated the routing policy ({failure[0]}: {failure[1]}); "
                    "the Host decides on the same goal"
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
        outcome = policy_check["taskPreference"]["outcome"]
        rule_index = policy_check["taskPreference"]["ruleIndex"]
        if outcome == "matched":
            preference_reason = routing_preferences[rule_index]["reason"]
        elif outcome == "alternative":
            preference_reason = "The selector chose another legal candidate despite a matching task preference"
        elif outcome == "fallback":
            preference_reason = "No task preference matched a legal candidate"
        else:
            preference_reason = "No task preference applies to this request"
        if selected is not None and routing_preferences:
            selected["routingPreference"] = {
                "status": outcome, "ruleIndex": rule_index, "reason": preference_reason,
            }
            reason = f"{reason[:MAX_DECISION_REASON - 160]} [task preference: {outcome}; rule {rule_index}]"
        self._finish(
            connection, row, status="completed", output=output, now=now,
            reason=reason, profile_id=profile_id, evidence_ids=evidence_ids, selected=selected,
        )

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
        self.board.workflow.routing_settled(connection, decision_id=row["decision_id"], now=now)

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

    def list_decisions(self, params: dict) -> dict:
        """One bounded newest-first page of decision history.

        This is a read: it admits no evaluation reader/writer lease, calls no model
        and persists nothing. The ``kind`` filter is applied in SQL *before* the
        limit and the cursor is ``decision_requests.rowid``, the immutable insertion
        order, so a newer decision never pushes an older one out of reach and a page
        never shifts, repeats or skips. ``total`` is the complete matching count,
        independent of the cursor; decisions that never acquired a run and older
        pages are returned exactly like any other.
        """
        schemas.reject_unknown(params, LIST_FIELDS, "selection.list")
        kind = schemas.optional_string(params, "kind")
        if kind is not None and kind not in DECISION_KINDS:
            raise BoardError("INVALID_ARGUMENT", "kind must be 'select' or 'maintain'", field="kind")
        limit = schemas.optional_int(params, "limit", DEFAULT_LIST_LIMIT, 1, MAX_LIST_LIMIT)
        before = schemas.optional_positive_int(params, "before")
        filter_sql = "r.kind=?" if kind is not None else None
        filter_values: tuple[Any, ...] = (kind,) if kind is not None else ()
        with self.board.db.read() as connection:
            total = int(
                connection.execute(
                    f"SELECT COUNT(*) AS count FROM decision_requests r"
                    + (f" WHERE {filter_sql}" if filter_sql else ""),
                    filter_values,
                ).fetchone()["count"]
            )
            query = f"SELECT r.rowid AS request_rowid, {_ROW_COLUMNS}{_ROW_FROM}"
            values: list[Any] = list(filter_values)
            if filter_sql:
                query += f" WHERE {filter_sql}"
                if before is not None:
                    query += " AND r.rowid<?"
            elif before is not None:
                query += " WHERE r.rowid<?"
            if before is not None:
                values.append(before)
            query += " ORDER BY r.rowid DESC LIMIT ?"
            values.append(limit)
            rows = connection.execute(query, values).fetchall()
            decisions = [self._view(connection, row, include_audit=False) for row in rows]
            next_cursor = None
            if len(rows) == limit:
                last_rowid = int(rows[-1]["request_rowid"])
                more = connection.execute(
                    "SELECT 1 FROM decision_requests r"
                    + (f" WHERE {filter_sql} AND r.rowid<?" if filter_sql else " WHERE r.rowid<?")
                    + " LIMIT 1",
                    (*filter_values, last_rowid),
                ).fetchone()
                if more is not None:
                    next_cursor = last_rowid
        return {"decisions": decisions, "nextCursor": next_cursor, "total": total}

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
        if "taskReference" in request:
            view["taskReference"] = request["taskReference"]
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
