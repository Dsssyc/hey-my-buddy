"""Durable Router attempts with Python-owned frozen inputs and boundary checks.

The blackboard owns publication, reader leases and atomic terminal events. The
Worker runtime owns native handles, budgets, input verification and stop receipts.
Router answers choose a legal configuration or abstain; preference judgments are
recorded, never used as an additional veto. Historical maintenance records remain
readable but cannot start model work.
"""
from __future__ import annotations

from contextlib import nullcontext

import json
import re
import sqlite3
import uuid
from typing import Any

from . import schemas, user_policy
from . import selection_policy, router, tool_evidence
from .db import canonical_json, sha256_text
from .errors import BoardError

DECISION_ADAPTER = "decision"
#: ``maintain`` stays a readable historical kind; it is never created any more.
DECISION_KINDS = ("select", "maintain")
#: Durable decision states. ``needs-host`` is the honest abstention/out-of-policy
#: outcome; ``stale`` is a fenced result that may not become a current recommendation.
DECISION_STATUSES = ("queued", "running", "completed", "needs-host", "failed", "cancelled", "stale")
TERMINAL_DECISION_STATUSES = frozenset({"completed", "needs-host", "failed", "cancelled", "stale"})

SELECT_FIELDS = frozenset({"requestId", "task", "requiredCapabilities", "timeoutSeconds"})
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
ANSWER_VALIDATION_CODES = frozenset({
    "answer-empty", "answer-not-json", "answer-invalid-json", "answer-shape",
    "answer-unexpected-field",
})
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
    "Router 不可用：尚未设置可用的 Router buddy；请指定完整合法 buddy，"
    "或由用户更新 Router 设置后显式 reroute。"
)
NEEDS_HOST_NO_CANDIDATE = (
    "no enabled, available, capability-matching profile is a legal candidate under the published pins and "
    "excludes, so there is nothing honest to recommend"
)
#: The recorded reason when the frozen candidate set holds exactly one legal
#: candidate: the program selects it directly and no Router call is created.
SOLE_CANDIDATE_REASON = "唯一合法候选，未调用 Router"
#: Retain the complete supported catalog's exclusions. An oversized legacy table
#: still reports its exact total alongside the bounded entries.
MAX_FROZEN_EXCLUSIONS = MAX_DECISION_PROFILES

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
        from .harness_health import HARNESSES, read_health
        from .harness_runtime import bound
        with self.board.db.read() as db:
            records = [read_health(db, name) for name in HARNESSES]
        with bound(records):
            return DecisionAdapter().available()

    def selector_family(self, connection: sqlite3.Connection, spec: dict | None = None) -> tuple[str, str, str] | None:
        """The model family the fixed decision profile will actually consume.

        Resolved inside the caller's claim transaction from the same configured
        profile :meth:`claim` admits, so the store can quota-check the selector's
        own family *before* the claim commits and before the selection reader is
        granted, and freeze that exact tuple onto the routing attempt.
        """
        descriptor = decision_spec(spec or {})
        row = self._row(connection, descriptor["decisionId"]) if descriptor else None
        if row is None or row["status"] != "queued":
            return None
        profile_row, _problem = self._refresh_route(connection, row)
        if profile_row is None:
            return None
        return (profile_row["adapter"], profile_row["provider"], profile_row["model"])

    # -- request-time policy -------------------------------------------------
    def _decision_profile(self, connection: sqlite3.Connection) -> tuple[sqlite3.Row | None, str | None]:
        profile, _facts, problem = router.resolve(connection)
        return profile, problem["reason"] if problem else None

    def _refresh_route(self, connection, row):
        """Recheck only the admission snapshot; never change Router or mode."""
        request = json.loads(row["requested_json"])
        profile, _facts, problem = router.resolve(connection, frozen=request)
        return profile, problem

    @staticmethod
    def _candidate_bounds(*, required_capabilities, constraints, coding_only: bool = True) -> tuple[list[str], list[Any]]:
        """The hard-bound SQL clauses shared by the candidate and exclusion scans.

        Every bound here is program-enforced Python filtering: enabled and
        available profiles, a ready harness, required capabilities, fixed
        configuration fields and complete coding tuples. Preference policy is
        deliberately absent — the two scans apply it in opposite directions.
        """
        clauses = [
            "p.enabled=1", "p.available=1",
            "EXISTS(SELECT 1 FROM harness_health h WHERE h.adapter=p.adapter AND h.status='ready')",
        ]
        values: list[Any] = []
        for capability in sorted(set(required_capabilities or [])):
            clauses.append("EXISTS(SELECT 1 FROM json_each(p.capabilities_json) WHERE value=?)")
            values.append(capability)
        if coding_only:
            clauses.extend(["p.provider IS NOT NULL AND p.provider!=''", "p.model IS NOT NULL AND p.model!=''", "p.effort IS NOT NULL AND p.effort!=''"])
            clauses.append("p.adapter IN (" + ",".join("?" for _ in schemas.CODING_ADAPTERS) + ")")
            values.extend(schemas.CODING_ADAPTERS)
        for key, value in (constraints or {}).items():
            if key not in schemas.CONFIGURATION_FIELDS:
                raise BoardError("INVALID_ARGUMENT", "Unknown configuration constraint")
            clauses.append(f"p.{key}=?")
            values.append(value)
        return clauses, values

    @staticmethod
    def _select_candidates(connection: sqlite3.Connection, required_capabilities: list[str], *, constraints: dict | None = None, coding_only: bool = False) -> list[sqlite3.Row]:
        """Legal candidates without any quota retry: the read-only projection.

        Pin/exclude is enforced here in Python and never delegated to the model;
        ``prefer`` stays a soft ordering hint that only enters the bounded input.
        """
        rows, _retried = DecisionCoordinator._scan_candidates(
            connection, required_capabilities, constraints=constraints, coding_only=coding_only)
        return rows

    @staticmethod
    def _scan_candidates(connection: sqlite3.Connection, required_capabilities: list[str], *, constraints: dict | None = None, coding_only: bool = False, decision_id: str | None = None, consume_retries: bool = False, now: str | None = None, materialize_observations: bool = False) -> tuple[list[sqlite3.Row], list[str]]:
        """Legal candidates, plus the single-use quota retry windows a decision may use.

        Pin/exclude is enforced here in Python and never delegated to the model;
        ``prefer`` stays a soft ordering hint that only enters the bounded input.
        Without ``decision_id`` this is a pure read: exhausted configurations stay
        excluded and nothing is claimed. Inside a decision's write transaction a
        no-reset exhaustion whose retry window is open is admitted exactly once —
        the claim is durable in that same transaction, so a concurrent decision
        re-reads the marker and stays excluded. With ``consume_retries`` unset the
        same admission rule is evaluated without claiming anything, which is the
        publish-time bounds re-check: a window this decision already consumed
        keeps its selection legal.
        """
        clauses, values = DecisionCoordinator._candidate_bounds(
            required_capabilities=required_capabilities, constraints=constraints, coding_only=coding_only)
        clauses.append("COALESCE(f.mode,'')!='exclude'")
        clauses.append("(NOT EXISTS(SELECT 1 FROM effective_preferences WHERE mode='pin') OR f.mode='pin')")
        values.append(MAX_DECISION_PROFILES + 1)
        from .native_observations import exhausted
        from .quota_routing import claim, materialize
        selected: list[sqlite3.Row] = []
        retried: list[str] = []
        health_available = {}
        for row in connection.execute(
            "SELECT p.* FROM evaluation_profiles p LEFT JOIN effective_preferences f ON f.profile_id=p.profile_id "
            "WHERE " + " AND ".join(clauses) + " ORDER BY p.rowid LIMIT ?", values):
            from .harness_health import read_health
            if row['adapter'] not in health_available:
                health_available[row['adapter']] = read_health(connection, row['adapter'])['available']
            if not health_available[row['adapter']]:
                continue
            if materialize_observations:
                materialize(connection, row['adapter'], row['provider'], now=now)
            if exhausted(connection, row, now=now) is None:
                selected.append(row)
            elif decision_id is not None and claim(
                    connection, row, decision_id=decision_id, consume=consume_retries, now=now) is not None:
                # A profile admitted through a retry window is retried whether it
                # claimed the window itself or shares the record this decision
                # already claimed: both entered only because the window opened.
                selected.append(row)
                retried.append(row["profile_id"])
        return selected, retried

    def _excluded_profiles(self, connection: sqlite3.Connection, *, required_capabilities: list[str], constraints: dict | None = None) -> tuple[list[dict], int]:
        """The user-excluded configurations inside these hard bounds, frozen now.

        These are the exclusions that narrowed this request's candidate set: each
        profile passes every hard bound (enabled, available, healthy, complete
        coding tuple, required capabilities, fixed fields) and only the user's
        effective ``exclude`` preference removes it. Pins are not considered,
        because a pin narrows the set without excluding anything.
        """
        clauses, values = self._candidate_bounds(
            required_capabilities=required_capabilities, constraints=constraints)
        clauses.append("COALESCE(f.mode,'')='exclude'")
        from_sql = (" FROM evaluation_profiles p"
                    " LEFT JOIN effective_preferences f ON f.profile_id=p.profile_id"
                    " WHERE " + " AND ".join(clauses))
        total = int(connection.execute("SELECT COUNT(*) AS count" + from_sql, values).fetchone()["count"])
        rows = connection.execute(
            "SELECT p.profile_id, p.adapter, p.provider, p.model, p.effort,"
            " f.reason AS preference_reason, f.source AS preference_source" + from_sql +
            " ORDER BY p.rowid LIMIT ?",
            [*values, MAX_FROZEN_EXCLUSIONS],
        ).fetchall()
        entries = [
            {
                "profileId": row["profile_id"],
                "adapter": row["adapter"],
                "provider": row["provider"],
                "model": row["model"],
                "effort": row["effort"],
                "reason": row["preference_reason"],
                "source": row["preference_source"],
            }
            for row in rows
        ]
        return entries, total

    def _freeze_routing_basis(self, connection: sqlite3.Connection, *, candidates: list[sqlite3.Row], required_capabilities: list[str], constraints: dict | None = None, quota_retry: list[str] | None = None) -> dict:
        """Submission-time routing facts: the frozen candidate count and exclusions.

        The basis is recorded once with the request and never recomputed, so a
        later preference change cannot rewrite why an earlier route had few or
        no candidates. The submission's hard constraints stay in the request
        itself; this object carries what the constraints alone cannot show.
        """
        excluded, excluded_count = self._excluded_profiles(
            connection, required_capabilities=required_capabilities, constraints=constraints)
        basis = {"candidateCount": len(candidates), "excludedCount": excluded_count, "excludedProfiles": excluded}
        if quota_retry:
            basis["quotaRetryProfileIds"] = quota_retry
        return basis

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
            preferences = user_policy.effective_preferences(connection, profile_ids)
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
            # User notes are kept per model family; the family key is on every profile.
            "annotations": user_policy.family_annotations(connection, profile_ids),
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

    def _select_input(self, connection: sqlite3.Connection, *, request_id: str, revision: int, profile_row: sqlite3.Row, task_text: str, candidates: list[sqlite3.Row], hard_constraints: dict | None = None, routing_mode: str = "review") -> tuple[dict | None, str | None]:
        if len(candidates) > MAX_DECISION_PROFILES:
            return None, "The legal candidate set exceeds the bounded decision profile limit; narrow the task constraints"
        candidate_ids = {candidate["profile_id"] for candidate in candidates}
        markers = ",".join("?" for _ in candidate_ids) or "NULL"
        cards = [
            self._card_input(row)
            for row in connection.execute(f"SELECT * FROM evaluation_cards WHERE profile_id IN ({markers}) ORDER BY rowid", tuple(candidate_ids))
        ]
        evidence = self._referenced_evidence(connection, cards) if routing_mode == "review" else []
        table = self._table_input(
            connection, profiles=candidates, evidence_rows=evidence, include_preferences=True
        )
        # The program-computed preference truth for this request. It is request-local
        # (it depends on this candidate slice and the user's published prefer entries),
        # so it is part of the variable suffix the model sees after the stable table
        # snapshot. Task-local routing preferences are retired Host input (ADR-021).
        table["policyFacts"] = selection_policy.policy_facts(
            profiles=table["profiles"],
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
        for key in ("routingPreferences", "routingMode", "allowRoutingFallback"):
            if key in params:
                raise BoardError(
                    "INVALID_ARGUMENT",
                    f"{key} is retired Host routing input; " + schemas.PARTIAL_CONFIGURATION_HINT,
                    field=key,
                )
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
            "timeoutSeconds": timeout if "timeoutSeconds" in params else None,
        }
        return self._create(request_id, request)

    def _create(self, request_id: str, request: dict, *, connection=None, needs_host_reason: str | None = None) -> dict:
        # Keep the caller's admission input independent of the persisted snapshot,
        # including when an internal caller reuses the same dictionary on replay.
        request = dict(request)
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
            # The legal candidate set is frozen before any Router resolution: a
            # sole legal candidate is selected by the program with no Router
            # call, so that path cannot depend on Router availability. Candidate
            # inspection leaves retries open until a selection is adopted.
            candidates, quota_retry = self._scan_candidates(
                connection, request.get("requiredCapabilities", []),
                constraints=request.get("constraints"), coding_only=True,
                decision_id=decision_id, consume_retries=False, now=now, materialize_observations=True)
            request["routingBasis"] = self._freeze_routing_basis(
                connection, candidates=candidates,
                required_capabilities=request.get("requiredCapabilities", []),
                constraints=request.get("constraints"), quota_retry=quota_retry)
            status = "queued"
            # New decisions are always selection requests; there is no internal
            # maintenance request path left on the blackboard.
            reason = "queued for a worker; the selection reader is admitted when the run starts"
            error: str | None = None
            task_text = request.get("task") or ""
            expected_revision = int(state["table_revision"])
            sole_candidate: sqlite3.Row | None = None
            facts = {"routerProfileId": None, "routingMode": None, "budget": None,
                     "configurationRevision": int(state["configuration_revision"])}
            if len(candidates) == 1:
                # One frozen legal candidate leaves the Router nothing to compare,
                # so the program selects it directly: no Router task is created and
                # no Router configuration or model-input byte budget is needed.
                sole_candidate = candidates[0]
                facts["routerCalled"] = False
                request.update(facts)
                status, reason = "completed", SOLE_CANDIDATE_REASON
            elif not candidates:
                request.update(facts)
                status, reason = "needs-host", NEEDS_HOST_NO_CANDIDATE
            else:
                profile_row, facts, problem = router.resolve(connection)
                request.update(facts)
                # Even an unavailable published Router has a fixed identity. It
                # must not be replaced by another buddy when this request replays.
                published = connection.execute("SELECT * FROM evaluation_profiles WHERE profile_id=?",
                                               (facts["routerProfileId"],)).fetchone()
                request["routerProfile"] = ({key: published[key] for key in schemas.CONFIGURATION_FIELDS}
                                            if published is not None else None)
                request["routerProblem"] = problem
                if request["budget"] is not None:
                    request["budget"] = dict(request["budget"])
                    request["timeoutSeconds"] = (60 if facts["routingMode"] == "fast" else
                                                 request.get("timeoutSeconds") or request["budget"]["timeoutSeconds"])
                    request["budget"]["timeoutSeconds"] = request["timeoutSeconds"]
                    facts["budget"] = request["budget"]
                if needs_host_reason is not None:
                    status = "needs-host"
                    reason = needs_host_reason
                elif profile_row is None:
                    status = "needs-host"
                    reason = problem["reason"] if problem else NEEDS_HOST_NO_PROFILE
            frozen_input = None
            if status == "queued":
                frozen_input, problem = self._select_input(
                    connection, request_id=request_id, revision=expected_revision, profile_row=profile_row,
                    task_text=task_text, candidates=candidates,
                    hard_constraints=request.get("constraints") or {}, routing_mode=facts["routingMode"])
                if frozen_input is None:
                    status, reason = "needs-host", problem
                else:
                    frozen_input["budget"] = request["budget"]
                    frozen_input.update(facts)
                    frozen_input["routerProfile"] = request["routerProfile"]
                    if facts["routingMode"] == "review":
                        frozen_input["executionWorkspace"] = request.get("executionWorkspace")
                    else:
                        frozen_input.pop("evidence", None)
                    frozen_input["outputSchema"] = router.answer_schema([item["profileId"] for item in frozen_input["profiles"]], facts["routingMode"])
            if frozen_input is not None:
                from .accounts import identity, selection
                names = {item['adapter'] for item in frozen_input.get('profiles', [])}
                if frozen_input.get('profile', {}).get('adapter'):
                    names.add(frozen_input['profile']['adapter'])
                frozen_input['accounts'] = {name: identity(selection(connection, name)) for name in names}
                input_bytes = len(canonical_json(frozen_input).encode("utf-8"))
                if input_bytes > MAX_DECISION_INPUT_BYTES:
                    status, reason = "needs-host", (
                        f"The complete frozen routing input contains {input_bytes} UTF-8 bytes, above the "
                        f"{MAX_DECISION_INPUT_BYTES}-byte decision input ceiling; nothing was truncated or sent to a model.")
                    frozen_input = None
            task_id = None
            if status == "queued":
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
            if frozen_input is not None:
                connection.execute("UPDATE decision_requests SET input_json=?,input_sha256=? WHERE decision_id=?",
                                   (canonical_json(frozen_input), sha256_text(canonical_json(frozen_input)), decision_id))
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
                    **router.routing_facts(request),
                    **({"routerCalled": False} if sole_candidate is not None else {}),
                },
                revision=expected_revision,
            )
            if sole_candidate is not None:
                # The terminal transition and its atomic event happen inside this
                # same creation transaction; no worker, reader or attempt exists.
                self._adopt_sole_candidate(
                    connection, self._row(connection, decision_id),
                    candidate=sole_candidate, request=request, now=now)
            row = self._row(connection, decision_id)
            head = self.board._head_of(connection)
        if owns_transaction:
            self.board._notify(head)
        return self._request_response(connection=None if owns_transaction else connection, row=row, duplicate=False)

    def route_workflow(self, connection, *, run_id: str, sequence: int, spec: dict, manifest: dict | None = None) -> dict:
        """Admit a fixed-selector task with the Goal in its existing transaction.

        The selector itself is always the configured decision profile. Only the
        bounded business candidate set is filtered; no recursive workflow submit
        and no provider/model call occurs during admission.
        """
        task_text = spec["task"]
        continuation = connection.execute("SELECT input_text,helper_outcomes_json FROM workflow_continuations"
                                          " WHERE run_id=? AND state='queued' ORDER BY rowid DESC LIMIT 1", (run_id,)).fetchone()
        if continuation:
            task_text += "\n\nContinuation:\n" + continuation["input_text"]
            task_text += "\nHelper outcomes:\n" + continuation["helper_outcomes_json"]
        task_bytes = len(task_text.encode("utf-8"))
        request = {
            "kind": "select", "task": task_text,
            "executionWorkspace": manifest,
            "requiredCapabilities": spec.get("requiredCapabilities", []),
            "constraints": schemas.configuration_constraints(spec),
            "workflowRouting": True,
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
        profile_row, problem = self._refresh_route(connection, row)
        if profile_row is None:
            self._finish(connection, row, status="needs-host", reason=problem["reason"],
                         error=problem["code"], output={"status": "error", **problem}, now=now)
            self._cancel_queued_task(connection, task, now, problem["reason"])
            return "decision-closed", None
        document = json.loads(row["input_json"]) if row["input_json"] else None
        if not document:
            self._finish(connection, row, status="needs-host", reason="No frozen Router input is recorded", now=now)
            self._cancel_queued_task(connection, task, now, "missing frozen Router input")
            return "decision-closed", None
        from .accounts import identity, selection
        bindings = document.get('accounts') or {name: {'source': 'native', 'credentialRevision': 0}
            for name in {item['adapter'] for item in document.get('profiles', [])}}
        if any(account != identity(selection(connection, name)) for name, account in bindings.items()):
            self._finish(connection, row, status='stale', reason='The frozen candidate accounts changed before claim', now=now)
            self._cancel_queued_task(connection, task, now, 'Account selection changed')
            return 'decision-closed', None
        requested_profile = document.get("profile") or {}
        if any(profile_row[key] != requested_profile.get(key) for key in schemas.CONFIGURATION_FIELDS):
            self._finish(connection, row, status="stale", reason="The configured Router changed before claim", now=now)
            self._cancel_queued_task(connection, task, now, "Router configuration changed")
            return "decision-closed", None
        table_revision = int(document["tableRevision"])
        if table_revision != int(self._state(connection)["table_revision"]):
            self._finish(connection, row, status="needs-host", now=now,
                         reason="Router 不可用：admission 后评价表 revision 已变化；请由 Host 显式 reroute",
                         error="router-table-changed", output={"status": "error", "code": "router-table-changed"})
            self._cancel_queued_task(connection, task, now, "Frozen routing table changed")
            return "decision-closed", None
        # Freeze the local system-sandbox program fact for this attempt inside
        # the claim transaction (ADR-021 §4): publication judges tool evidence
        # against the board's own read_health fact captured here, never a value
        # the answer claims. The fact lives in this attempt's durable row, beside
        # the claim-frozen account binding, so the frozen model-input document
        # itself stays byte-identical from admission to publication.
        from .harness_health import read_health
        connection.execute(
            "INSERT INTO meta(key,value) VALUES(?,?)"
            " ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            ("attempt-tool-policy:" + attempt_id,
             canonical_json({"systemSandbox": bool(read_health(connection, profile_row["adapter"])["systemSandbox"])})),
        )
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
                int(row["configuration_revision"]),
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
        if (row["decision_attempt_id"] != attempt["attempt_id"]
                or row["decision_generation"] != attempt["generation"]):
            summary["late"] = True
            return summary
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
        helper_ok = (
            status == "ok"
            and shutdown_confirmed is True
            and isinstance(output, dict)
            and output.get("status") == "ok"
        )
        validation_code = (
            output.get("code")
            if isinstance(output, dict)
            and output.get("status") == "error"
            and isinstance(output.get("code"), str)
            and (output.get("code").startswith("router-") or output.get("code") in ANSWER_VALIDATION_CODES)
            else None
        )
        if status == "cancelled" or task["state"] == "cancelling":
            self._finish(
                connection, row, status="cancelled", output=output, now=now,
                reason="the decision run was cancelled; no recommendation is published",
            )
        elif shutdown_confirmed is not True:
            self._finish(connection, row, status="failed", output=output, now=now,
                         reason="The Router stop is unconfirmed; no recommendation is published",
                         error=error or "router-stop-unconfirmed")
        elif validation_code is not None:
            # The helper has already consumed its one bounded correction chance.
            # Publication does not restart the model or create another attempt.
            self._finish(
                connection, row, status="needs-host", output=output, now=now,
                reason=(
                    f"the decision helper refused the recommendation under the routing policy "
                    f"({validation_code}); the Host decides on the same goal"
                ),
                error=f"{validation_code}: routing answer boundary"[:2000],
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
        elif self._account_binding_changed(connection, attempt):
            self._finish(connection, row, status='stale', output=output, now=now,
                         reason='The frozen routing account changed; the old result is retained without adoption',
                         error='ACCOUNT_BINDING_CHANGED')
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

    @staticmethod
    def _account_binding_changed(connection, attempt):
        from .accounts import identity, selection
        row = connection.execute('SELECT value FROM meta WHERE key=?', ('attempt-routing-accounts:' + attempt['attempt_id'],)).fetchone()
        bindings = json.loads(row[0]) if row else {}
        return any(account != identity(selection(connection, name)) for name, account in bindings.items())

    @staticmethod
    def _attempt_tool_policy(connection: sqlite3.Connection, attempt_id: str) -> dict | None:
        """The toolPolicy this attempt froze at claim, or ``None`` for older attempts."""
        row = connection.execute("SELECT value FROM meta WHERE key=?", ("attempt-tool-policy:" + attempt_id,)).fetchone()
        return json.loads(row[0]) if row else None

    def _tool_evidence_problem(self, row: sqlite3.Row, document: dict, output: dict) -> dict | None:
        """The blackboard wrapper over the unified tool-evidence judgment.

        Not wired into publication yet: the existing per-mode checks stay in
        force until the Host switches them over in one step, and the retired
        ``zeroToolVerified`` receipt never serves as review evidence. This
        wrapper binds a receipt's ``toolEvidence`` to the frozen attempt before
        the pure judge runs: the binding must equal the frozen
        adapter/task/attempt/generation, every root identity the receipt reports
        must be one the evidence trusts, and the claim-frozen
        ``toolPolicy.systemSandbox`` boolean — never a receipt claim — selects
        the review category matrix. The document carries that boolean because
        the publication caller grafts :meth:`_attempt_tool_policy` onto the
        frozen input; a document from an attempt that froze no policy — an old
        or unbound result — cannot publish.
        """
        unverified = tool_evidence.TOOL_EVIDENCE_UNVERIFIED
        evidence = output.get("toolEvidence") if isinstance(output, dict) else None
        if evidence is None:
            return {"code": unverified, "reason": "Router publication requires unified tool evidence"}
        reported = output.get("nativeIdentity") if isinstance(output, dict) else None
        reported = reported if isinstance(reported, list) else ([reported] if isinstance(reported, dict) else None)
        if not reported:
            return {"code": unverified, "reason": "The receipt reports no native root identity"}
        expected_binding = {
            "adapter": (document.get("profile") or {}).get("adapter"),
            "taskId": row["decision_task_id"],
            "attemptId": row["decision_attempt_id"],
            "generation": row["decision_generation"],
        }
        if not isinstance(evidence, dict) or evidence.get("binding") != expected_binding:
            return {"code": unverified, "reason": "The tool evidence binding does not equal this attempt's frozen binding"}
        roots = evidence.get("nativeIdentity")
        if not isinstance(roots, list) or any(identity not in roots for identity in reported):
            return {"code": unverified, "reason": "The tool evidence does not cover the receipt's root identities"}
        policy = document.get("toolPolicy")
        sandbox = policy.get("systemSandbox") if isinstance(policy, dict) else None
        if not isinstance(sandbox, bool):
            # A result from before this attempt froze its tool policy — or from a
            # foreign attempt's document — has no board-owned sandbox fact to judge by.
            return {"code": unverified, "reason": "No claim-frozen systemSandbox fact exists for this attempt"}
        judged = tool_evidence.judge_tool_evidence(evidence, document.get("routingMode"), sandbox)
        if judged is None:
            return None
        reason = (
            "Router tool evidence is incomplete, inconsistent or malformed"
            if judged == unverified
            else "Router tool evidence records a call outside the frozen tool policy"
        )
        return {"code": judged, "reason": reason}

    def _publish_select(self, connection: sqlite3.Connection, row: sqlite3.Row, *, output: dict, now: str) -> None:
        """Validate one select recommendation against the frozen candidate set.

        The policy outcome is independently derived from the service's frozen
        input. Model/helper policy echoes cannot change it. Support and evidence
        still fence adoption; publication itself never starts a retry.
        """
        document = json.loads(row["input_json"]) if row["input_json"] else {}
        request = json.loads(row["requested_json"])
        profile, problem = self._refresh_route(connection, row)
        if profile is None:
            self._finish(connection, row, status="needs-host", now=now,
                         output={**output, "code": problem["code"]},
                         error=problem["code"], reason=problem["reason"])
            return
        stop = output.get("stopEvidence")
        if (not isinstance(stop, dict) or stop.get("shutdownConfirmed") is not True
                or not isinstance(stop.get("native"), dict)
                or stop["native"].get("shutdownConfirmed") is not True):
            self._finish(connection, row, status="needs-host", now=now,
                         output={**output, "code": "router-stop-unconfirmed"}, error="router-stop-unconfirmed",
                         reason="Router publication requires native and controller stop evidence")
            return
        usage = output.get("usage")
        budget = document.get("budget") or {}
        if document.get("routingMode") == "fast":
            if (not isinstance(usage, dict) or output.get("zeroToolVerified") is not True
                    or type(usage.get("toolCalls")) is not int or usage["toolCalls"] != 0):
                self._finish(connection, row, status="needs-host", now=now,
                             output={**output, "code": "router-tools-forbidden"}, error="router-tools-forbidden",
                             reason="Fast routing requires a complete zero-tool native receipt")
                return
        if (not isinstance(usage, dict) or type(usage.get("elapsedMs")) is not int
                or usage["elapsedMs"] < 0 or type(usage.get("toolCalls")) is not int
                or usage["toolCalls"] < 0):
            self._finish(connection, row, status="needs-host", now=now,
                         output={**output, "code": "router-tool-evidence-unverified"}, error="router-tool-evidence-unverified",
                         reason="Router publication requires elapsed time and native tool count evidence")
            return
        if (usage["elapsedMs"] > budget["timeoutSeconds"] * 1000
                or (document.get("routingMode") == "review" and usage["toolCalls"] > budget["toolCalls"])):
            self._finish(connection, row, status="needs-host", now=now,
                         output={**output, "code": "router-budget-exhausted"}, error="router-budget-exhausted",
                         reason="Router publication exceeds the frozen time or cumulative tool budget")
            return
        manifest = document.get("executionWorkspace")
        verification = output.get("inputVerification")
        if document.get("routingMode") == "review" and (
                not isinstance(verification, dict) or verification.get("unchanged") is not True
                or not verification.get("snapshotSha256")
                or verification.get("manifestSha256") != (manifest or {}).get("manifestSha256")):
            self._finish(connection, row, status="needs-host", now=now,
                         output={**output, "code": "router-input-changed"}, error="router-input-changed",
                         reason="Router input verification is missing or differs from the frozen manifest")
            return
        profiles = document.get("profiles") or []
        try:
            decision = router.validate_answer(output.get("decision"), [item["profileId"] for item in profiles], document.get("routingMode", "review"))
        except BoardError as failure:
            self._finish(connection, row, status="needs-host", now=now,
                         output={**output, "code": failure.code}, reason=failure.message,
                         error=failure.code)
            return
        reason, profile_id = decision["reason"], decision["profileId"]
        evidence_ids = decision["evidence"]
        if profile_id is None:
            self._finish(connection, row, status="needs-host", output={**output, "policyCheck": None},
                         now=now, reason=reason, evidence_ids=evidence_ids)
            return
        # Bounds re-check without claiming: a no-reset exhaustion whose retry this
        # very decision consumed stays legal for its own frozen answer, and a
        # window that opened after the freeze is legal but never burned here.
        current_rows, _retried = self._scan_candidates(
            connection, request.get("requiredCapabilities") or [],
            constraints=request.get("constraints"), coding_only=True,
            decision_id=row["decision_id"], consume_retries=False, now=now)
        current_ids = {item["profile_id"] for item in current_rows}
        if profile_id not in current_ids:
            self._finish(connection, row, status="needs-host", now=now,
                         output={**output, "code": "router-out-of-bounds"},
                         reason="The selected configuration no longer satisfies the original hard bounds",
                         error="router-out-of-bounds")
            return
        expected_facts = selection_policy.policy_facts(
            profiles=profiles,
            prefer_profile_ids=[item["profileId"] for item in document.get("preferences", [])
                                if item.get("mode") == "prefer"],
            hard_constraints=request.get("constraints") or {})
        policy_check = selection_policy.expected_policy_check(expected_facts, profile_id)
        output = {**output, "decision": decision, "policyCheck": policy_check}
        if (not self._reader_open(connection, row, now)
                or int(self._state(connection)["table_revision"]) != int(row["expected_revision"])):
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
        if status == "completed" and profile_id and selected:
            from .quota_routing import claim
            # Candidate inspection must not spend an unchosen provider's retry.
            # Selection and this single-use claim share the write transaction;
            # another selector either sees exhaustion or rechecks its bounds.
            if claim(connection, selected, decision_id=row["decision_id"], now=now) is None:
                status, profile_id, selected = "needs-host", None, None
                reason, error = "The selected quota retry was consumed by another decision", "router-out-of-bounds"
                output = {**(output or {}), "code": error}
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
        # Freeze only an exact, bounded machine code; never parse error prose.
        error_code = (output or {}).get("code")
        if not isinstance(error_code, str) or not re.fullmatch(r"[a-z][a-z0-9-]{0,79}", error_code):
            error_code = None
        self._append_event(
            connection,
            event,
            row["decision_id"],
            {
                "status": status,
                "profileId": profile_id,
                "reason": (reason or "")[:MAX_DECISION_REASON],
                "error": error,
                "errorCode": error_code,
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

    def _adopt_sole_candidate(self, connection: sqlite3.Connection, row: sqlite3.Row, *, candidate: sqlite3.Row, request: dict, now: str) -> None:
        """Select the single frozen legal candidate without any Router evidence.

        The program owns this choice outright: no Router attempt, model call,
        usage counter or stop evidence is recorded, because none happened. The
        hard constraints, required capabilities and the preference check are
        still derived from the frozen request and stored exactly as a Router
        answer's would be.
        """
        profile_id = candidate["profile_id"]
        profiles = [self._profile_input(candidate)]
        preferences = user_policy.effective_preferences(connection, [profile_id])
        facts = selection_policy.policy_facts(
            profiles=profiles,
            prefer_profile_ids=[entry["profileId"] for entry in preferences if entry["mode"] == "prefer"],
            hard_constraints=request.get("constraints") or {})
        policy_check = selection_policy.expected_policy_check(facts, profile_id)
        selected = dict(profiles[0])
        self._finish(
            connection,
            row,
            status="completed",
            now=now,
            reason=SOLE_CANDIDATE_REASON,
            profile_id=profile_id,
            evidence_ids=[],
            selected=selected,
            output={
                "programSelection": {
                    "code": "single-candidate",
                    "profileId": profile_id,
                    "candidateCount": 1,
                    "reason": SOLE_CANDIDATE_REASON,
                    "preferences": preferences,
                    "policyFacts": facts,
                },
                "policyCheck": policy_check,
            },
        )

    # -- views ---------------------------------------------------------------
    def health_summary(self) -> dict:
        """Bounded, model-free health over settled selection events, not task prose.

        Only an adopted Router success resets the failure streak. Neutral events
        and program selections cannot prove recovery. Immutable event codes and
        bound attempt receipts survive late changes to the retained output.
        """
        limit = 20
        kinds = "('decision.completed','decision.failed','decision.needs_host','decision.cancelled','decision.stale')"
        source = (
            " FROM events e JOIN decision_requests r ON r.decision_id=json_extract(e.payload_json,'$.decisionId')"
            " LEFT JOIN attempts a ON a.attempt_id=r.attempt_id AND a.task_id=r.task_id AND a.generation=r.generation"
            " WHERE e.kind IN " + kinds + " AND r.kind='select'"
        )
        router_success = (
            "e.kind='decision.completed' AND a.attempt_id IS NOT NULL"
            " AND json_extract(a.result_json,'$.status')='ok'"
            " AND json_extract(a.result_json,'$.shutdownConfirmed')=1"
            " AND json_extract(a.result_json,'$.result.status')='ok'"
            " AND json_extract(a.result_json,'$.result.modelStarted') IS NOT 0"
        )
        with self.db.read() as connection:
            rows = connection.execute(
                "SELECT e.kind,e.created_at,e.payload_json,r.decision_id,r.task_id,r.output_json,"
                " a.result_json AS receipt_json,(" + router_success + ") AS router_success,"
                " (SELECT w.run_id FROM workflow_routes w WHERE w.decision_id=r.decision_id LIMIT 1) AS goal_id"
                + source + " ORDER BY e.seq DESC LIMIT ?", (limit,),
            ).fetchall()
            success = connection.execute(
                "SELECT e.created_at,r.decision_id" + source
                + " AND " + router_success + " ORDER BY e.seq DESC LIMIT 1",
            ).fetchone()
        failed = []
        streak = 0
        still_failing = True
        all_timeouts = True
        timeout_codes = {"timeout", "call-timeout", "deadline", "router-timeout"}
        abstentions = cancellations = stale = 0
        special_counts = {"router-budget-exhausted": 0, "router-out-of-bounds": 0, "router-input-changed": 0}
        for row in rows:
            payload = json.loads(row["payload_json"])
            receipt = json.loads(row["receipt_json"]) if row["receipt_json"] else {}
            # The request output may be replaced by a late, fenced result. Prefer
            # the immutable receipt; old events without one retain their output.
            if receipt:
                output = receipt.get("result")
            else:
                output = json.loads(row["output_json"]) if row["output_json"] else {}
            output = output if isinstance(output, dict) else {}
            code = payload.get("errorCode") or output.get("code")
            if not isinstance(code, str) or not re.fullmatch(r"[a-z][a-z0-9-]{0,79}", code):
                code = "needs-host" if row["kind"] == "decision.needs_host" else "call-failed"
            decision = output.get("decision") or {}
            abstained = (row["kind"] == "decision.needs_host" and output.get("status") == "ok"
                         and isinstance(decision, dict) and decision.get("profileId", False) is None
                         and set(decision) == {"profileId", "reason", "evidence"})
            special = row["kind"] in ("decision.failed", "decision.needs_host") and code in special_counts
            if special:
                special_counts[code] += 1
            # Bounds and input changes remain independent diagnostic counters.
            # Budget exhaustion also means the Router failed to return an answer.
            is_failure = code not in ("router-out-of-bounds", "router-input-changed") and (row["kind"] == "decision.failed" or
                         (row["kind"] == "decision.needs_host" and not abstained))
            if still_failing and is_failure:
                streak += 1
                all_timeouts = all_timeouts and (code in timeout_codes or receipt.get("terminationReason") == "deadline")
            elif row["router_success"]:
                still_failing = False
            abstentions += int(abstained)
            cancellations += int(row["kind"] == "decision.cancelled")
            stale += int(row["kind"] == "decision.stale")
            if is_failure:
                failed.append({"decisionId":row["decision_id"], "runId":row["goal_id"] or row["task_id"],
                               "at":row["created_at"], "code":code})
        reason_code = ("router-consecutive-timeouts" if all_timeouts else "router-consecutive-failures") if streak >= 3 else None
        return {"available":streak < 3, "reasonCode":reason_code,
                "windowSize":limit, "sampleCount":len(rows), "failureCount":len(failed),
                "budgetExhaustedCount":special_counts["router-budget-exhausted"],
                "boundsRejectedCount":special_counts["router-out-of-bounds"],
                "inputChangedCount":special_counts["router-input-changed"],
                "consecutiveFailures":streak, "abstentionCount":abstentions,
                "cancelledCount":cancellations, "staleCount":stale,
                "lastSuccessAt":success["created_at"] if success else None,
                "lastSuccessDecisionId":success["decision_id"] if success else None,
                "recentFailures":failed[:5]}

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
        references = json.loads(row["evidence_ids_json"])
        evidence = references if isinstance(references, list) and all(
            isinstance(item, dict) and set(item) == {"kind", "ref"} for item in references) else None
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
            "evidence": evidence,
            "policyCheck": (output or {}).get("policyCheck"),
            "budget": request.get("budget"),
            "routingBasis": request.get("routingBasis"),
            "constraints": request.get("constraints", {}),
            "requiredCapabilities": request.get("requiredCapabilities", []),
            # False only for a recorded program selection; a record predating that
            # path stays null rather than claiming a Router ran.
            "routerCalled": request.get("routerCalled"),
            **router.routing_facts(request),
            "configurationRevision": int(row["configuration_revision"]),
            "usage": (output or {}).get("usage"),
            "nativeIdentity": (output or {}).get("nativeIdentity"),
            "stopEvidence": (output or {}).get("stopEvidence"),
            "inputVerification": (output or {}).get("inputVerification"),
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
