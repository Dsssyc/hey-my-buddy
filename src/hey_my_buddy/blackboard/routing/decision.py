"""Durable Router attempts with Python-owned frozen inputs and boundary checks.

The blackboard owns publication and atomic terminal events. The
Worker runtime owns native handles, budgets, input verification and stop receipts.
Router answers choose a legal buddy or abstain; preference judgments are
recorded, never used as an additional veto.
"""
from __future__ import annotations

from contextlib import nullcontext

import json
import re
import sqlite3
import uuid
from typing import Any

from ...protocol import schemas
from ..evaluation import user_policy
from . import selection_policy, router, router_sequence, router_failover
from ...protocol import tool_evidence
from ..store.db import canonical_json, sha256_text
from ...errors import BoardError

DECISION_ADAPTER = "decision"
DECISION_KINDS = ("select",)
#: Durable decision states. ``needs-host`` is the honest abstention/out-of-policy
#: outcome; unknown process outcomes cannot become a recommendation.
DECISION_STATUSES = ("queued", "running", "completed", "needs-host", "failed", "cancelled")
TERMINAL_DECISION_STATUSES = frozenset({"completed", "needs-host", "failed", "cancelled"})

SELECT_FIELDS = frozenset({"requestId", "task", "requiredCapabilities"})
GET_FIELDS = frozenset({"decisionId", "includeAudit"})
LIST_FIELDS = frozenset({"kind", "limit", "before"})
DEFAULT_LIST_LIMIT = 20
MAX_LIST_LIMIT = 100

#: The shared row projection for one decision. The paginated list reader adds
#: ``decision_requests.rowid`` as its stable keyset; nothing here persists anything.
_ROW_COLUMNS = (
    "d.decision_id, d.status, d.task, d.buddy_id, d.reason,"
    " d.created_at, d.error, r.request_id, r.kind, r.input_fingerprint,"
    " r.task_id AS decision_task_id, r.attempt_id AS decision_attempt_id,"
    " r.generation AS decision_generation,"
    " r.selected_json, r.input_json, r.input_sha256, r.output_json,"
    " r.requested_json,"
    " r.created_at AS request_created_at, r.updated_at AS request_updated_at"
)
_ROW_FROM = " FROM decision_requests r JOIN evaluation_decisions d ON d.decision_id = r.decision_id"

MAX_DECISION_TASK_BYTES = 8192
MAX_DECISION_REASON = 2000
ANSWER_VALIDATION_CODES = frozenset({
    "answer-empty", "answer-not-json", "answer-invalid-json", "answer-shape",
    "answer-unexpected-field",
})
#: Harness bounds mirrored from the decision helper's request validation. They are
#: the hard ceiling for one bounded selection request, never a silent truncation
#: point: a complete current table above them becomes an explicit needs-host outcome.
MAX_DECISION_BUDDIES = 200
MAX_DECISION_PREFERENCES = 256
MAX_DECISION_ANNOTATIONS = 200
MAX_DECISION_INPUT_BYTES = 128 * 1024
#: Extra room after the helper's own bound: the worker deadline must not fire while
#: the helper is still shutting its detached child down.
TIMEOUT_GRACE_SECONDS = 10

NEEDS_HOST_NO_BUDDY = (
    "Router 不可用：尚未设置可用的 Router buddy；请指定完整合法 buddy，"
    "或由用户更新 Router 设置后显式 reroute。"
)
NEEDS_HOST_NO_CANDIDATE = (
    "no enabled, available, capability-matching buddy is a legal candidate under the published pins and "
    "excludes, so there is nothing honest to recommend"
)
#: The recorded reason when the frozen candidate set holds exactly one legal
#: candidate: the program selects it directly and no Router call is created.
SOLE_CANDIDATE_REASON = "唯一合法候选，未调用 Router"
#: Retain the complete supported catalog's exclusions. An oversized legacy table
#: still reports its exact total alongside the bounded entries.
MAX_FROZEN_EXCLUSIONS = MAX_DECISION_BUDDIES

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

    def _append_event(self, connection: sqlite3.Connection, kind: str, decision_id: str, payload: dict) -> None:
        self.board._append_event(connection, kind, payload={"decisionId": decision_id, **payload})

    def _adapter_available(self) -> tuple[bool, str | None]:
        from ...buddy.roles.router import DecisionAdapter
        from ..service.harness_health import HARNESSES, read_health
        from ...buddy.harnesses.runtime_selection import bound
        with self.board.db.read() as db:
            records = [read_health(db, name) for name in HARNESSES]
        with bound(records):
            return DecisionAdapter().available()

    def selector_model_service(self, connection: sqlite3.Connection, spec: dict | None = None) -> tuple[str, str, str] | None:
        """The model service the current dispatch will actually consume.

        Read from the decision's current immutable dispatch — never a fresh
        resolve — so the store can quota-check the model service this request is frozen
        to *before* the claim commits and before the dispatch is claimed.
        """
        descriptor = decision_spec(spec or {})
        row = self._row(connection, descriptor["decisionId"]) if descriptor else None
        if row is None or row["status"] != "queued":
            return None
        dispatch = (router_sequence.dispatch(connection, row["decision_task_id"])
                    if row["decision_task_id"] else None)
        if dispatch is None:
            return None
        identity = dispatch["buddy"]
        return (identity["harness"], identity["provider"], identity["model"])

    # -- request-time policy -------------------------------------------------
    def _decision_buddy(self, connection: sqlite3.Connection) -> tuple[dict | None, str | None]:
        """The current Router for read-only projections; resolving changes nothing."""
        resolution = router.current_router(connection)
        return resolution.buddy, resolution.problem["reason"] if resolution.problem else None

    @staticmethod
    def _candidate_bounds(*, required_capabilities, constraints, coding_only: bool = True) -> tuple[list[str], list[Any]]:
        """The hard-bound SQL clauses shared by the candidate and exclusion scans.

        Every bound here is program-enforced Python filtering: enabled and
        available buddies, a ready harness, required capabilities, fixed
        buddy fields and complete coding tuples. Preference policy is
        deliberately absent — the two scans apply it in opposite directions.
        """
        clauses = [
            "p.enabled=1", "p.available=1",
            "EXISTS(SELECT 1 FROM harness_health h WHERE h.harness=p.harness AND h.status='ready')",
        ]
        values: list[Any] = []
        for capability in sorted(set(required_capabilities or [])):
            clauses.append("EXISTS(SELECT 1 FROM json_each(p.capabilities_json) WHERE value=?)")
            values.append(capability)
        if coding_only:
            clauses.extend(["p.provider IS NOT NULL AND p.provider!=''", "p.model IS NOT NULL AND p.model!=''", "p.effort IS NOT NULL AND p.effort!=''"])
            clauses.append("p.harness IN (" + ",".join("?" for _ in schemas.CODING_HARNESSES) + ")")
            values.extend(schemas.CODING_HARNESSES)
        for key, value in (constraints or {}).items():
            if key not in schemas.BUDDY_FIELDS:
                raise BoardError("INVALID_ARGUMENT", "Unknown buddy constraint")
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
    def _scan_candidates(connection: sqlite3.Connection, required_capabilities: list[str], *, constraints: dict | None = None, coding_only: bool = False, decision_id: str | None = None, consume_retries: bool = False, now: str | None = None) -> tuple[list[sqlite3.Row], list[str]]:
        """Legal candidates, plus the single-use quota retry windows a decision may use.

        Pin/exclude is enforced here in Python and never delegated to the model;
        ``prefer`` stays a soft ordering hint that only enters the bounded input.
        Without ``decision_id`` this is a pure read: exhausted buddies stay
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
        clauses.append("(NOT EXISTS(SELECT 1 FROM evaluation_preferences WHERE mode='pin') OR f.mode='pin')")
        values.append(MAX_DECISION_BUDDIES + 1)
        from ..evaluation.native_observations import exhausted
        from .quota_routing import claim
        selected: list[sqlite3.Row] = []
        retried: list[str] = []
        health_available = {}
        for row in connection.execute(
            "SELECT p.* FROM buddies p LEFT JOIN evaluation_preferences f ON f.buddy_id=p.buddy_id "
            "WHERE " + " AND ".join(clauses) + " ORDER BY p.rowid LIMIT ?", values):
            from ..service.harness_health import read_health
            if row['harness'] not in health_available:
                health_available[row['harness']] = read_health(connection, row['harness'])['available']
            if not health_available[row['harness']]:
                continue
            if exhausted(connection, row, now=now) is None:
                selected.append(row)
            elif decision_id is not None and claim(
                    connection, row, decision_id=decision_id, consume=consume_retries, now=now) is not None:
                # A buddy admitted through a retry window is retried whether it
                # claimed the window itself or shares the record this decision
                # already claimed: both entered only because the window opened.
                selected.append(row)
                retried.append(row["buddy_id"])
        return selected, retried

    def _excluded_buddies(self, connection: sqlite3.Connection, *, required_capabilities: list[str], constraints: dict | None = None) -> tuple[list[dict], int]:
        """The user-excluded buddies inside these hard bounds, frozen now.

        These are the exclusions that narrowed this request's candidate set: each
        buddy passes every hard bound (enabled, available, healthy, complete
        coding tuple, required capabilities, fixed fields) and only the user's
        effective ``exclude`` preference removes it. Pins are not considered,
        because a pin narrows the set without excluding anything.
        """
        clauses, values = self._candidate_bounds(
            required_capabilities=required_capabilities, constraints=constraints)
        clauses.append("COALESCE(f.mode,'')='exclude'")
        from_sql = (" FROM buddies p"
                    " LEFT JOIN evaluation_preferences f ON f.buddy_id=p.buddy_id"
                    " WHERE " + " AND ".join(clauses))
        total = int(connection.execute("SELECT COUNT(*) AS count" + from_sql, values).fetchone()["count"])
        rows = connection.execute(
            "SELECT p.buddy_id, p.harness, p.provider, p.model, p.effort,"
            " f.reason AS preference_reason" + from_sql +
            " ORDER BY p.rowid LIMIT ?",
            [*values, MAX_FROZEN_EXCLUSIONS],
        ).fetchall()
        entries = [
            {
                "buddyId": row["buddy_id"],
                "harness": row["harness"],
                "provider": row["provider"],
                "model": row["model"],
                "effort": row["effort"],
                "reason": row["preference_reason"],
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
        excluded, excluded_count = self._excluded_buddies(
            connection, required_capabilities=required_capabilities, constraints=constraints)
        basis = {"candidateCount": len(candidates), "excludedCount": excluded_count, "excludedBuddies": excluded}
        if quota_retry:
            basis["quotaRetryBuddyIds"] = quota_retry
        return basis

    # -- bounded model input -------------------------------------------------
    def _buddy_input(self, row: sqlite3.Row) -> dict:
        entry = {
            "buddyId": row["buddy_id"],
            "label": row["label"],
            "harness": row["harness"],
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

    def _table_input(
        self,
        connection: sqlite3.Connection,
        *,
        buddies: list[sqlite3.Row],
        include_preferences: bool,
    ) -> dict:
        """The complete bounded table slice for one request — never a silent truncation.

        Every legal candidate, preference and model note is included. When that complete slice exceeds the
        helper's request bounds, :meth:`_assemble_input` reports an explicit
        oversized outcome instead of picking rows by insertion order.
        """
        buddy_ids = [row["buddy_id"] for row in buddies]
        preferences: list[dict] = []
        if include_preferences:
            preferences = user_policy.preferences(connection, buddy_ids)
        return {
            "buddies": [self._buddy_input(row) for row in buddies],
            "preferences": preferences,
            # User notes are kept per model service; the model service key is on every buddy.
            "annotations": user_policy.model_service_notes(connection, buddy_ids),
        }

    def _assemble_input(self, connection: sqlite3.Connection, *, kind: str, request_id: str, buddy_row: sqlite3.Row | None, table: dict, task_text: str | None, preserve_oversized: bool = False) -> tuple[dict | None, str | None]:
        """Build the exact bounded document the model sees, or report why it cannot.

        Returns ``(document, oversized_reason)``. Nothing is dropped to make a
        document fit: exceeding a harness bound or the byte ceiling is an honest
        ``needs-host`` outcome. ``buddy_row=None`` builds the request's common
        packet without the current actor — the base input every dispatch copies,
        where only the actor and its same-tier budget vary per item.
        """
        document: dict[str, Any] = {
            "operation": kind, "requestId": request_id,
        }
        if buddy_row is not None:
            document["buddy"] = {key: buddy_row[key] for key in schemas.BUDDY_FIELDS}
        if kind == "select":
            document["task"] = task_text
        document.update(table)
        for name, limit in (
            ("buddies", MAX_DECISION_BUDDIES),
            ("preferences", MAX_DECISION_PREFERENCES),
            ("annotations", MAX_DECISION_ANNOTATIONS),
        ):
            if len(table[name]) > limit:
                return document if preserve_oversized else None, (
                    f"the complete bounded table slice carries {len(table[name])} {name}, above the helper's "
                    f"{limit}-entry request bound; nothing was truncated and nothing was sent to a model. Curate "
                    "the published candidates or preferences first."
                )
        encoded = canonical_json(document)
        if len(encoded.encode("utf-8")) > MAX_DECISION_INPUT_BYTES:
            return document if preserve_oversized else None, (
                f"the complete bounded table slice is {len(encoded.encode('utf-8'))} bytes, above the "
                f"{MAX_DECISION_INPUT_BYTES}-byte decision input ceiling; nothing was truncated and nothing was "
                "sent to a model. Curate the published table first."
            )
        return document, None

    def _select_input(self, connection: sqlite3.Connection, *, request_id: str, buddy_row: sqlite3.Row | None, task_text: str, candidates: list[sqlite3.Row], hard_constraints: dict | None = None, preserve_oversized: bool = False) -> tuple[dict | None, str | None]:
        if len(candidates) > MAX_DECISION_BUDDIES and not preserve_oversized:
            return None, "The legal candidate set exceeds the bounded decision buddy limit; narrow the task constraints"
        table = self._table_input(
            connection, buddies=candidates, include_preferences=True
        )
        # The program-computed preference truth for this request. It is request-local
        # (it depends on this candidate slice and the user's published prefer entries),
        # so it is part of the variable suffix the model sees after the stable table
        # snapshot. Task-local routing preferences are retired Host input (ADR-021).
        table["policyFacts"] = selection_policy.policy_facts(
            buddies=table["buddies"],
            prefer_buddy_ids=[
                entry["buddyId"] for entry in table["preferences"] if entry["mode"] == "prefer"
            ],
            hard_constraints=hard_constraints or {},
        )
        return self._assemble_input(
            connection,
            kind="select",
            request_id=request_id,
            buddy_row=buddy_row,
            table=table,
            task_text=task_text,
            preserve_oversized=preserve_oversized,
        )

    # -- request operations --------------------------------------------------
    def request_select(self, params: dict) -> dict:
        for key in ("routingPreferences", "routingMode", "allowRoutingFallback"):
            if key in params:
                raise BoardError(
                    "INVALID_ARGUMENT",
                    f"{key} is retired Host routing input; " + schemas.PARTIAL_BUDDY_HINT,
                    field=key,
                )
        schemas.reject_unknown(params, SELECT_FIELDS, "selection.request")
        request_id = schemas.required_string(
            params, "requestId", max_length=128, pattern=schemas.IDENTIFIER_PATTERN
        )
        task_text, _size = schemas.bounded_text(params, "task", max_bytes=MAX_DECISION_TASK_BYTES)
        capabilities = schemas.string_list(params, "requiredCapabilities", limit=schemas.MAX_CAPABILITIES)
        request = {
            "kind": "select",
            "task": task_text,
            "requiredCapabilities": capabilities,
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
            # The legal candidate set is frozen before any Router resolution: a
            # sole legal candidate is selected by the program with no Router
            # call, so that path cannot depend on Router availability. Candidate
            # inspection leaves retries open until a selection is adopted.
            candidates, quota_retry = self._scan_candidates(
                connection, request.get("requiredCapabilities", []),
                constraints=request.get("constraints"), coding_only=True,
                decision_id=decision_id, consume_retries=False, now=now)
            request["routingBasis"] = self._freeze_routing_basis(
                connection, candidates=candidates,
                required_capabilities=request.get("requiredCapabilities", []),
                constraints=request.get("constraints"), quota_retry=quota_retry)
            status = "queued"
            # New decisions are always selection requests; there is no internal
            # maintenance request path left on the blackboard.
            reason = "queued for a Router worker"
            error: str | None = None
            task_text = request.get("task") or ""
            sole_candidate: sqlite3.Row | None = None
            resolution = None
            snapshot: dict | None = None
            facts = {"routerBuddyId": None, "routingMode": None, "budget": None}
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
                # Admission freezes the complete common packet — candidates,
                # preferences, program facts, every Router identity
                # and the input manifest — before the
                # resolution selects an item, so even a fully unavailable list
                # leaves the Host the same frozen packet, never only a reason.
                resolution = router.current_router(connection, now=now)
                facts = resolution.facts
                request.update(facts)
                if resolution.problem is not None:
                    request["routerProblem"] = resolution.problem
                if facts["routingMode"] is not None:
                    request["routerBudgetSeconds"] = (60 if facts["routingMode"] == "fast" else
                                                 facts["budget"]["timeoutSeconds"])
                    facts["budget"] = {**facts["budget"], "timeoutSeconds": request["routerBudgetSeconds"]}
                    request["budget"] = dict(facts["budget"])
                # The admission scan's limit protects model routing. For an
                # oversized Host packet only, retain the entire legal slice,
                # without changing admission or consuming any quota retry.
                if len(candidates) > MAX_DECISION_BUDDIES:
                    clauses, values = self._candidate_bounds(
                        required_capabilities=request.get("requiredCapabilities", []),
                        constraints=request.get("constraints"), coding_only=True)
                    clauses.extend(["COALESCE(f.mode,'')!='exclude'",
                                    "(NOT EXISTS(SELECT 1 FROM evaluation_preferences WHERE mode='pin') OR f.mode='pin')"])
                    from ..evaluation.native_observations import exhausted
                    from .quota_routing import claim
                    from ..service.harness_health import read_health
                    complete_candidates, complete_retries = [], []
                    for item in connection.execute(
                        "SELECT p.* FROM buddies p LEFT JOIN evaluation_preferences f ON f.buddy_id=p.buddy_id WHERE "
                        + " AND ".join(clauses) + " ORDER BY p.rowid", values):
                        if not read_health(connection, item["harness"])["available"]:
                            continue
                        if exhausted(connection, item, now=now) is not None:
                            if claim(connection, item, decision_id=decision_id, consume=False, now=now) is None:
                                continue
                            complete_retries.append(item["buddy_id"])
                        complete_candidates.append(item)
                    candidates = complete_candidates
                    request["routingBasis"] = self._freeze_routing_basis(
                        connection, candidates=candidates,
                        required_capabilities=request.get("requiredCapabilities", []),
                        constraints=request.get("constraints"), quota_retry=complete_retries)
                base_input, problem = self._select_input(
                    connection, request_id=request_id, buddy_row=None,
                    task_text=task_text, candidates=candidates,
                    hard_constraints=request.get("constraints") or {},
                    preserve_oversized=True)
                base_input.update(facts)
                base_input["routingBasis"] = request["routingBasis"]
                base_input["constraints"] = request.get("constraints", {})
                base_input["requiredCapabilities"] = request.get("requiredCapabilities", [])
                # Unknown legacy mode stays unknown. Its Host packet retains the
                # admission facts but is never eligible for a model dispatch.
                if facts["routingMode"] != "fast":
                    base_input["executionWorkspace"] = request.get("executionWorkspace")
                if facts["routingMode"] is not None:
                    base_input["outputSchema"] = router.answer_schema(
                        [item["buddyId"] for item in base_input["buddies"]], facts["routingMode"])
                snapshot = {"facts": facts, "baseInput": base_input,
                            "inspections": [dict(entry) for entry in resolution.inspections]}
                input_bytes = len(canonical_json(base_input).encode("utf-8"))
                if problem is not None or input_bytes > MAX_DECISION_INPUT_BYTES:
                    status, error = "needs-host", "router-input-too-large"
                    reason = problem or (
                        f"The complete frozen routing input contains {input_bytes} UTF-8 bytes, above the "
                        f"{MAX_DECISION_INPUT_BYTES}-byte decision input ceiling; nothing was truncated or sent to a model.")
                elif needs_host_reason is not None:
                    status, reason = "needs-host", needs_host_reason
                elif facts["routingMode"] is None or resolution.buddy is None:
                    status, reason = "needs-host", (
                        resolution.problem["reason"] if resolution.problem else NEEDS_HOST_NO_BUDDY)
            task_id = None
            connection.execute(
                "INSERT INTO evaluation_decisions(decision_id, status, task, buddy_id, reason,"
                " created_at, error) VALUES(?,?,?,?,?,?,?)",
                (
                    decision_id,
                    status,
                    task_text,
                    None,
                    reason,
                    now,
                    error,
                ),
            )
            connection.execute(
                "INSERT INTO decision_requests(decision_id, request_id, kind, input_fingerprint,"
                " task_id, requested_json, created_at,"
                " updated_at) VALUES(?,?,?,?,?,?,?,?)",
                (
                    decision_id,
                    request_id,
                    kind,
                    fingerprint,
                    None,
                    canonical_json(request),
                    now,
                    now,
                ),
            )
            if snapshot is not None:
                # The request row exists before its first dispatch is queued; the
                # frozen snapshot and any admission preflight facts settle in this
                # same transaction.
                router_sequence.freeze_request(connection, decision_id, snapshot=snapshot, now=now)
                self._record_preflight_walk(connection, request_id, resolution.inspections, now=now)
            if status == "queued" and resolution is not None and resolution.buddy is not None:
                try:
                    task_id = self._queue_router_dispatch(
                        connection, self._row(connection, decision_id), resolution=resolution, now=now)
                except BoardError as failure:
                    if failure.code != "router-input-too-large":
                        raise
                    status, reason, error = "needs-host", failure.message, failure.code
                    connection.execute("UPDATE evaluation_decisions SET status=?,reason=?,error=? WHERE decision_id=?",
                                       (status, reason, error, decision_id))
            self._append_event(
                connection,
                "decision.requested",
                decision_id,
                {
                    "requestId": request_id,
                    "kind": kind,
                    "status": status,
                    "taskId": task_id,
                    "reason": reason,
                    "error": error,
                    **router.routing_facts(request),
                    **({"routerCalled": False} if sole_candidate is not None else {}),
                },
            )
            if sole_candidate is not None:
                # The terminal transition and its atomic event happen inside this
                # same creation transaction; no worker or attempt exists.
                self._adopt_sole_candidate(
                    connection, self._row(connection, decision_id),
                    candidate=sole_candidate, request=request, now=now)
            row = self._row(connection, decision_id)
            head = self.board._head_of(connection)
        if owns_transaction:
            self.board._notify(head)
        return self._request_response(connection=None if owns_transaction else connection, row=row, duplicate=False)

    def route_workflow(self, connection, *, run_id: str, sequence: int, spec: dict, manifest: dict | None = None) -> dict:
        """Admit a fixed-selector task with the Micro task in its existing transaction.

        The selector itself is always the configured decision buddy. Only the
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
        access = manifest.get("access", "write") if manifest is not None else "write"
        required_capabilities = list(spec.get("requiredCapabilities", []))
        if f"permission:{access}" not in required_capabilities:
            required_capabilities.append(f"permission:{access}")
        request = {
            "kind": "select", "task": task_text,
            "executionWorkspace": manifest,
            "requiredCapabilities": required_capabilities,
            "constraints": schemas.buddy_constraints(spec),
            "workflowRouting": True,
        }
        needs_host_reason = None
        if task_bytes > MAX_DECISION_TASK_BYTES:
            # The immutable Micro task owns the full text. A selector cannot choose from
            # an incomplete task, and its bounded audit stores only a reference.
            request["task"] = f"Micro task {run_id} (full task retained in workflow)"
            request["taskReference"] = {"runId": run_id, "bytes": task_bytes, "sha256": sha256_text(task_text)}
            needs_host_reason = (
                f"The complete Micro task contains {task_bytes} UTF-8 bytes, above the selector's "
                f"{MAX_DECISION_TASK_BYTES}-byte task limit. No model call was made. "
                "Continue the same Micro task with a complete buddy; its full task remains unchanged."
            )
        return self._create(f"workflow:{run_id}:{sequence}", request, connection=connection,
                            needs_host_reason=needs_host_reason)

    def _create_task(self, connection: sqlite3.Connection, *, decision_id: str, request_id: str, kind: str, router_budget_seconds: int, now: str, request_key: str | None = None) -> str:
        """Admit one ordinary selection task, using the real queue and capacity.

        Each dispatched Router item owns its own internal task; ``request_key``
        keeps the per-task request identity unique when one request dispatches
        more than once, while the decision's own requestId stays untouched.
        """
        task_id = str(uuid.uuid4())
        request_key = request_key or f"decision:{request_id}"
        spec = {
            "adapter": DECISION_ADAPTER,
            # A private per-decision directory the decision adapter creates; it never
            # overlaps a project cwd, so a decision run consumes capacity without
            # reserving any workspace a business task needs.
            "cwd": str(self.board.directory / "decisions" / decision_id),
            "task": "Bounded selection decision over the current evaluation table.",
            "routerBudgetSeconds": router_budget_seconds + TIMEOUT_GRACE_SECONDS,
            "requiredCapabilities": [DECISION_ADAPTER],
            "decision": {"decisionId": decision_id, "kind": kind, "routerBudgetSeconds": router_budget_seconds},
        }
        fingerprint = schemas.spec_fingerprint(spec)
        blocker = self.board._admission_blocker(connection, spec)
        connection.execute(
            "INSERT INTO tasks(task_id, request_id, owner, spec_json, spec_canonical_json, input_fingerprint,"
            " fingerprint_version, adapter, required_capabilities, cwd, exclusive_resources, router_budget_seconds,"
            " state, queue_reason, revision, created_at, updated_at)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                task_id,
                request_key,
                "decision",
                canonical_json(spec),
                canonical_json(spec),
                fingerprint,
                schemas.FINGERPRINT_VERSION_CURRENT,
                DECISION_ADAPTER,
                canonical_json(spec["requiredCapabilities"]),
                spec["cwd"],
                canonical_json([]),
                spec["routerBudgetSeconds"],
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
                "requestId": request_key,
                "adapter": DECISION_ADAPTER,
                "cwd": spec["cwd"],
                "queueReason": blocker or "awaiting-worker",
                "inputFingerprint": fingerprint,
                "decisionId": decision_id,
            },
        )
        return task_id

    def _record_preflight_walk(self, connection, request_id: str, inspections, *, now: str, selection_retry: bool = False) -> None:
        """Record the real preflight no-answers one resolution walk observed.

        A skip-window observation is an existing window, not a new failure: it is
        never recorded and never extends. The outcome receipts make repeated walks
        over the same request and item idempotent, so a skip observed at admission
        is not recorded again by a later advance.
        """
        from .router_history import record_outcome
        for entry in inspections:
            code = entry.get("code")
            if not code or code == "router-skip-window":
                continue
            record_outcome(
                connection,
                buddy_id=entry["buddyId"],
                plane="work",
                request_id=request_id,
                task_id=None,
                attempt_id=None,
                outcome="no_answer",
                code=code,
                phase="preflight",
                facts={"routerIndex": entry["index"], "selectionRetry": selection_retry},
                now=now,
            )

    def _queue_router_dispatch(self, connection: sqlite3.Connection, row: sqlite3.Row, *, resolution, now: str) -> str:
        """Advance one request to its next actual Router through a fresh internal task.

        Reused at request creation, at pre-claim advancement and (once the
        confirmed-stop failover consumes it) after a completed item: it always
        copies the frozen base input, grafts only this item's actor identity and
        same-tier budget, records the immutable dispatch under the new internal
        task, and re-points the request — and its pending workflow route — at that
        task while clearing the previous attempt and output bindings. The
        micro task keeps waiting for routing; no business Worker turn opens.
        """
        decision_id = row["decision_id"]
        snapshot = router_sequence.request_snapshot(connection, decision_id)
        if snapshot is None:
            raise BoardError("INTERNAL_ERROR", "A dispatch requires a frozen request snapshot",
                             decisionId=decision_id)
        # Re-dispatching the same still-queued item must not mint a second task.
        current_task = row["decision_task_id"]
        if current_task is not None:
            existing = router_sequence.dispatch(connection, current_task)
            state = connection.execute("SELECT state FROM tasks WHERE task_id=?", (current_task,)).fetchone()
            if (existing is not None and state is not None and state["state"] == "queued"
                    and existing["routerIndex"] == resolution.router_index
                    and existing["buddyId"] == resolution.buddy_id):
                return current_task
        identity_map = {key: resolution.buddy[key] for key in schemas.BUDDY_FIELDS}
        document = {
            **(json.loads(row["input_json"]) if row["input_json"] else snapshot["baseInput"]),
            "buddy": identity_map,
            "routerBuddyId": resolution.buddy_id,
            "routerBuddy": identity_map,
            "routerIndex": resolution.router_index,
        }
        input_bytes = len(canonical_json(document).encode("utf-8"))
        if input_bytes > MAX_DECISION_INPUT_BYTES:
            raise BoardError("router-input-too-large",
                             f"The complete frozen Router dispatch contains {input_bytes} UTF-8 bytes, above the "
                             f"{MAX_DECISION_INPUT_BYTES}-byte input ceiling; nothing was truncated or sent to a model.")
        request = json.loads(row["requested_json"])
        router_budget_seconds = int(request["routerBudgetSeconds"])
        task_id = self._create_task(
            connection,
            decision_id=decision_id,
            request_id=row["request_id"],
            kind=row["kind"],
            router_budget_seconds=router_budget_seconds,
            now=now,
            request_key=f"decision:{row['request_id']}:{int(bool(request.get('selectionRetry')))}:{resolution.router_index}",
        )
        router_sequence.reserve_dispatch(
            connection,
            decision_id=decision_id,
            task_id=task_id,
            router_index=resolution.router_index,
            buddy={"buddyId": resolution.buddy_id, **identity_map},
            document=document,
            now=now,
        )
        digest = sha256_text(canonical_json(document))
        connection.execute(
            "UPDATE decision_requests SET task_id=?, attempt_id=NULL, generation=NULL,"
            " output_json=NULL, input_json=?, input_sha256=?, updated_at=? WHERE decision_id=?",
            (task_id, canonical_json(document), digest, now, decision_id),
        )
        connection.execute("UPDATE evaluation_decisions SET status='queued',buddy_id=NULL,error=NULL,reason=? WHERE decision_id=?",
                           ("queued for the next frozen Router dispatch", decision_id))
        self.board.workflow.routing_dispatched(connection, decision_id=decision_id, task_id=task_id, now=now)
        return task_id

    def prepare_router_claim(self, connection: sqlite3.Connection, *, task: sqlite3.Row, now: str) -> str | None:
        """Pre-claim preparation inside the claim transaction, before model service capacity.

        Verifies the task is this request's current dispatch inside its frozen
        snapshot. Changed circumstances close the old queued task at an honest
        boundary without switching Router; an eligibility loss of the current item
        before any model call records its real preflight no-answer and queues the
        next item's dispatch; retry-in-progress and unconfirmed-stop
        observations only queue. Nothing here starts a process or fabricates an
        attempt, and capacity is never bypassed with a spare task.
        """
        spec = json.loads(task["spec_json"])
        descriptor = decision_spec(spec)
        row = self._row(connection, descriptor["decisionId"]) if descriptor else None
        if row is None:
            return "decision-missing"
        if row["status"] != "queued":
            return "decision-closed"
        if row["decision_task_id"] != task["task_id"]:
            # A superseded dispatch never claims; its queued shell closes honestly.
            self._cancel_queued_task(connection, task, now, "superseded by a newer Router dispatch")
            return "decision-dispatch-superseded"
        snapshot = router_sequence.request_snapshot(connection, row["decision_id"])
        dispatch = router_sequence.dispatch(connection, task["task_id"])
        if snapshot is None or dispatch is None:
            self._finish(connection, row, status="needs-host", now=now,
                         reason="No frozen Router request snapshot is recorded for this dispatch")
            self._cancel_queued_task(connection, task, now, "missing frozen Router request snapshot")
            return "decision-closed"
        facts = json.loads(row["requested_json"])
        from .router_history import router_state
        state = router_state(connection, buddy_id=dispatch["buddyId"],
                             interval_seconds=facts["routerRetryIntervalSeconds"], now=now)
        if state["retryInProgress"]:
            # One in-flight retry per buddy: this request queues behind it without
            # consuming a slot and without recording anything about the item.
            return "router-retry-in-progress"
        recheck = router.current_router(
            connection, frozen=facts, after_index=dispatch["routerIndex"] - 1, now=now)
        if recheck.buddy is not None and recheck.router_index == dispatch["routerIndex"]:
            return None
        started = connection.execute(
            "SELECT 1 FROM attempts WHERE task_id=? LIMIT 1", (task["task_id"],)
        ).fetchone()
        if started is not None:
            # An attempt exists for this dispatch, so only its own confirmed stop
            # can move the request on; a preflight observation never may.
            return "router-dispatch-unfinished"
        for entry in recheck.inspections:
            if entry.get("index") == dispatch["routerIndex"]:
                # The item's real before-model failure is recorded once; a
                # skip-window observation here is an existing window and stays
                # unrecorded.
                self._record_preflight_walk(connection, row["request_id"], [entry], now=now, selection_retry=bool(facts.get("selectionRetry")))
        self._cancel_queued_task(connection, task, now, "the Router item is unavailable before the model call")
        advance = router.current_router(connection, frozen=facts, after_index=dispatch["routerIndex"], now=now)
        self._record_preflight_walk(connection, row["request_id"], advance.inspections, now=now, selection_retry=bool(json.loads(row["requested_json"]).get("selectionRetry")))
        if advance.buddy is None:
            self._finish(connection, row, status="needs-host", now=now,
                         reason=advance.problem["reason"], error=advance.problem["code"],
                         output={"status": "error", "code": advance.problem["code"]})
            return "decision-closed"
        try:
            self._queue_router_dispatch(connection, row, resolution=advance, now=now)
        except BoardError as failure:
            if failure.code != "router-input-too-large":
                raise
            self._finish(connection, row, status="needs-host", now=now, reason=failure.message,
                         error=failure.code, output={"status": "error", "code": failure.code})
            return "decision-closed"
        return "decision-dispatch-advanced"

    # -- worker lifecycle hooks ---------------------------------------------
    def claim(self, connection: sqlite3.Connection, *, task: sqlite3.Row, spec: dict, attempt_id: str, generation: int, now: str) -> tuple[str | None, dict | None]:
        """Admit one decision attempt inside the claim transaction.

        Returns ``(blocker, input_document)``. A blocker keeps the task queued and
        consumes no execution slot; the queue reason is visible on the task view. On
        success the decision is ``running``, the attempt is bound to the dispatch, and the exact model input is persisted for audit.
        """
        descriptor = decision_spec(spec) or {}
        decision_id = descriptor.get("decisionId")
        row = self._row(connection, decision_id) if decision_id else None
        if row is None:
            return "decision-missing", None
        if row["status"] != "queued":
            return "decision-closed", None
        if row["decision_task_id"] != task["task_id"]:
            # A late claim of a superseded dispatch never runs; the pre-claim step
            # has already closed its queued shell.
            return "decision-closed", None
        dispatch = router_sequence.dispatch(connection, task["task_id"])
        if dispatch is None:
            self._finish(connection, row, status="needs-host", reason="No frozen Router dispatch is recorded", now=now)
            self._cancel_queued_task(connection, task, now, "missing frozen Router dispatch")
            return "decision-closed", None
        document = dispatch["document"]
        # Freeze the local system-sandbox program fact for this attempt inside
        # the claim transaction (ADR-021 §4): publication judges tool evidence
        # against the board's own read_health fact captured here, never a value
        # the answer claims. The fact lives in this attempt's durable row, beside
        # its frozen model identity, so the frozen model-input document
        # itself stays byte-identical from admission to publication.
        from ..service.harness_health import read_health
        tool_policy = {"systemSandbox": bool(read_health(connection, dispatch["buddy"]["harness"])["systemSandbox"])}
        self._mark_running(
            connection, row, attempt_id=attempt_id, generation=generation,
            document=document, now=now,
        )
        # The attempt's Router binding and its ``router.claimed`` fact commit in
        # this same claim transaction: one atomic dispatch-to-attempt binding.
        router_sequence.record_claim(
            connection, task_id=task["task_id"], attempt_id=attempt_id, generation=generation, now=now,
            tool_policy=tool_policy)
        return None, document

    def _mark_running(self, connection: sqlite3.Connection, row: sqlite3.Row, *, attempt_id: str, generation: int, document: dict, now: str) -> None:
        digest = sha256_text(canonical_json(document))
        connection.execute(
            "UPDATE decision_requests SET attempt_id=?, generation=?, input_json=?, input_sha256=?, updated_at=? WHERE decision_id=?",
            (attempt_id, generation, canonical_json(document), digest, now, row["decision_id"]),
        )
        connection.execute("UPDATE evaluation_decisions SET status='running', reason=? WHERE decision_id=?",
                           ("Router is running over its frozen input", row["decision_id"]))
        self._append_event(connection, "decision.started", row["decision_id"],
                           {"attemptId": attempt_id, "generation": generation, "inputSha256": digest})

    def released(self, connection: sqlite3.Connection, *, task: sqlite3.Row, reason: str, now: str) -> dict | None:
        """Advance only on the Worker's recorded proof it never crossed spawn."""
        row = self._decision_for_task(connection, task)
        if row is None or row["status"] in TERMINAL_DECISION_STATUSES:
            return None
        attempt = connection.execute("SELECT * FROM attempts WHERE attempt_id=?",
                                     (row["decision_attempt_id"],)).fetchone()
        if attempt is None:
            return None
        event = connection.execute(
            "SELECT payload_json FROM events WHERE kind='attempt.released' AND task_id=? AND attempt_id=?"
            " ORDER BY seq DESC LIMIT 1", (task["task_id"], attempt["attempt_id"])).fetchone()
        proof = json.loads(event[0]) if event else {}
        # worker_release writes shutdown_confirmed even without never-spawned
        # proof. That generic fact alone cannot authorize a new native execution.
        stopped = (proof.get("releaseEvidence") == "spawnIntentWritten=false"
                   and attempt["shutdown_confirmed"] == 1 and attempt["execution_state"] == "finished")
        return self._complete(connection, row=row, task=task, attempt=attempt, status="failed",
                              result={"status": "error", "code": "router-unavailable", "reason": reason},
                              shutdown_confirmed=stopped, error=reason, now=now,
                              release_stopped=stopped, released=True)

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
        current recommendation; it is recorded as failed with the unknown stop fact.
        """
        task = connection.execute("SELECT * FROM tasks WHERE task_id=?", (attempt["task_id"],)).fetchone()
        if task is None:
            return
        row = self._decision_for_task(connection, task)
        if row is None:
            return
        if (row["decision_attempt_id"] != attempt["attempt_id"]
                or row["decision_generation"] != attempt["generation"]):
            return
        if row["status"] not in TERMINAL_DECISION_STATUSES:
            self._finish(
                connection,
                row,
                status="failed",
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
        receipt into an endless retry. The worker receipt remains durable either way.
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
                    f"the model output could not be adopted ({failure.code}: {failure.message}); the Host decides"
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
                reason="the decision result could not be processed; no recommendation is published",
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

    def _complete(self, connection: sqlite3.Connection, *, row: sqlite3.Row, task: sqlite3.Row, attempt: sqlite3.Row, status: str, result: Any, shutdown_confirmed: bool, error: str | None, now: str, release_stopped: bool = False, released: bool = False) -> dict[str, Any]:
        summary: dict[str, Any] = {"decisionId": row["decision_id"], "kind": row["kind"], "status": row["status"]}
        stored = connection.execute("SELECT * FROM attempts WHERE attempt_id=?", (attempt["attempt_id"],)).fetchone()
        if (row["decision_task_id"] != task["task_id"] or attempt["task_id"] != task["task_id"]
                or row["decision_attempt_id"] != attempt["attempt_id"]
                or row["decision_generation"] != attempt["generation"]
                or stored is None or stored["task_id"] != task["task_id"]
                or stored["nonce_verifier"] != attempt["nonce_verifier"]
                or stored["generation"] != attempt["generation"]
                or row["status"] in TERMINAL_DECISION_STATUSES):
            # The immutable attempt receipt already retains the late result.
            # Never overwrite the current request's output, input or health.
            return {**summary, "late": True}
        output = result if isinstance(result, dict) else None
        snapshot = router_sequence.request_snapshot(connection, row["decision_id"])
        dispatch = router_sequence.dispatch(connection, task["task_id"])
        checked = {"answer_valid": False, "abstained": False, "changed": False,
                   "code": "router-unavailable" if released else "router-no-answer",
                   "reason": error or "The bounded Router call produced no usable answer",
                   "output": output, "buddy_id": None, "selected": None, "evidence": []}
        completion_event = connection.execute(
            "SELECT payload_json FROM events WHERE task_id=? AND attempt_id=? AND kind='task.completed'"
            " ORDER BY seq DESC LIMIT 1", (task["task_id"], attempt["attempt_id"])).fetchone()
        cancel_raced = completion_event is not None and json.loads(completion_event[0]).get("cancelRaced") is True
        cancelled = cancel_raced or status == "cancelled" or task["state"] in ("cancelling", "cancelled") or attempt["cancel_requested_at"] is not None
        route = connection.execute(
            "SELECT r.*,w.state AS run_state,w.owner_generation AS current_owner,w.current_routing_id"
            " FROM workflow_routes r JOIN workflow_runs w ON w.run_id=r.run_id WHERE r.decision_id=?",
            (row["decision_id"],)).fetchone()
        if route is not None:
            ancestor = self.board.workflow._terminal_ancestor(connection, route["run_id"])
            cancelled = cancelled or route["run_state"] == "cancelled" or (ancestor is not None and ancestor["state"] == "cancelled")
            if (route["state"] != "pending" or route["current_owner"] != route["owner_generation"]
                    or route["current_routing_id"] != row["decision_id"] or route["run_state"] != "executing"
                    or ancestor is not None):
                checked.update(changed=True, code="workflow-routing-fenced",
                               reason="The parent route or its owner was fenced before publication")
        if snapshot is None or dispatch is None:
            checked.update(changed=True, code="router-dispatch-unverified",
                           reason="The current attempt has no immutable Router dispatch")
        else:
            if (row["input_sha256"] != dispatch["inputSha256"]
                    or row["input_json"] != canonical_json(dispatch["document"])):
                checked.update(changed=True, code="router-input-changed",
                               reason="The current input differs from the immutable dispatch")
        if not cancelled and not checked["changed"] and not released:
            checked = self._publish_select(connection, row, output=output or {}, now=now)

        stop = (output or {}).get("stopEvidence")
        native_stopped = (isinstance(stop, dict) and stop.get("shutdownConfirmed") is True
                          and isinstance(stop.get("native"), dict)
                          and stop["native"].get("shutdownConfirmed") is True)
        stopped = shutdown_confirmed is True and stored["shutdown_confirmed"] == 1 and (native_stopped or release_stopped)
        if status != "ok" or (output or {}).get("status") != "ok":
            if checked["answer_valid"]:
                checked.update(code="router-no-answer", reason=error or "The outer Worker reported a failed Router call")
            checked["answer_valid"] = False
        stage = "preflight" if released else ("publication" if (output or {}).get("status") == "ok" else "runtime")
        outcome = router_failover.classify_outcome(
            stage=stage, code=checked["code"], answer_valid=checked["answer_valid"],
            abstained=checked["abstained"], cancelled=bool(cancelled),
            circumstances_changed=checked["changed"], shutdown_confirmed=bool(stopped))
        from .router_history import record_outcome

        def record(value):
            record_outcome(connection, buddy_id=dispatch["buddyId"], plane="work", request_id=row["request_id"],
                           task_id=task["task_id"], attempt_id=attempt["attempt_id"], outcome=value,
                           code=checked["code"] if value == "no_answer" else None, phase=stage,
                           facts={"routerIndex": dispatch["routerIndex"], "selectionRetry": bool(json.loads(row["requested_json"]).get("selectionRetry"))}, now=now)

        if outcome == "changed" and checked["code"] == "router-out-of-bounds" and stopped:
            request = json.loads(row["requested_json"])
            if not request.get("selectionRetry"):
                self._retry_selection(connection, row, reason=checked["reason"], now=now)
                outcome = "rerouted"
            else:
                self._finish(connection, row, status="needs-host", now=now, error=checked["code"],
                             reason="The selected buddy remains invalid after rerouting: " + checked["reason"],
                             output=checked["output"])
        elif outcome == "no-answer":
            record("no_answer")
            advance = router.current_router(connection, frozen=json.loads(row["requested_json"]),
                                            after_index=dispatch["routerIndex"], now=now)
            self._record_preflight_walk(connection, row["request_id"], advance.inspections, now=now, selection_retry=bool(json.loads(row["requested_json"]).get("selectionRetry")))
            if advance.buddy is not None:
                try:
                    self._queue_router_dispatch(connection, row, resolution=advance, now=now)
                except BoardError as failure:
                    if failure.code != "router-input-too-large":
                        raise
                    # This is request preparation, not a failure of an unrun item.
                    self._finish(connection, row, status="needs-host", now=now, error=failure.code,
                                 reason=failure.message, output={"status": "error", "code": failure.code})
            else:
                self._finish(connection, row, status="needs-host", now=now, error=checked["code"],
                             reason=advance.problem["reason"], output={**(output or {}), "routerProblem": advance.problem["code"],
                                                          "code": checked["code"]})
        elif outcome in ("answered", "abstained"):
            self._finish(connection, row, status="completed" if outcome == "answered" else "needs-host", now=now,
                         reason=checked["reason"], output=checked["output"], buddy_id=checked["buddy_id"],
                         selected=checked["selected"])
            # _finish owns the final atomic quota claim. Do not record success if
            # another decision already consumed that retry at this last gate.
            if outcome == "abstained" or self._status(connection, row["decision_id"]) == "completed":
                record("answered")
            else:
                outcome = "changed"
        else:
            code = "router-stop-unconfirmed" if outcome == "stop-unconfirmed" else checked["code"]
            reason = ("The Router stop is unconfirmed; no recommendation is published" if outcome == "stop-unconfirmed"
                      else "The routing request was cancelled; no recommendation is published" if outcome == "cancelled"
                      else checked["reason"])
            self._finish(connection, row, status="cancelled" if outcome == "cancelled" else "needs-host" if outcome == "changed" else "failed" if shutdown_confirmed is not True else "needs-host",
                         output={**(output or {}), "code": code}, now=now, reason=reason, error=code)
        current = self._row(connection, row["decision_id"])
        return {**summary, "status": current["status"], "outcome": outcome, "buddyId": current["buddy_id"],
                "reason": current["reason"]}


    def _retry_selection(self, connection: sqlite3.Connection, row: sqlite3.Row, *, reason: str, now: str) -> None:
        """Route the same micro task once with fresh bounds and Router settings.

        Each dispatch keeps its immutable input; the first request snapshot remains
        historical evidence. The current request and new dispatch carry the retry's
        fresh packet, which subsequent failover dispatches also copy.
        """
        request = json.loads(row["requested_json"])
        request["selectionRetry"] = True
        candidates, quota_retry = self._scan_candidates(
            connection, request.get("requiredCapabilities", []), constraints=request.get("constraints"),
            coding_only=True, decision_id=row["decision_id"], now=now)
        request["routingBasis"] = self._freeze_routing_basis(
            connection, candidates=candidates, required_capabilities=request.get("requiredCapabilities", []),
            constraints=request.get("constraints"), quota_retry=quota_retry)
        self._append_event(connection, "decision.selection_rerouted", row["decision_id"], {"reason": reason})
        if len(candidates) <= 1:
            connection.execute("UPDATE decision_requests SET requested_json=? WHERE decision_id=?",
                               (canonical_json(request), row["decision_id"]))
            if candidates:
                request["routerCalled"] = False
                connection.execute(
                    "UPDATE decision_requests SET task_id=NULL,attempt_id=NULL,generation=NULL,input_json=NULL,"
                    " input_sha256=NULL,output_json=NULL,selected_json=NULL,requested_json=? WHERE decision_id=?",
                    (canonical_json(request), row["decision_id"]),
                )
                self._adopt_sole_candidate(connection, self._row(connection, row["decision_id"]),
                                           candidate=candidates[0], request=request, now=now)
            else:
                self._finish(connection, row, status="needs-host", now=now, error="router-out-of-bounds",
                             reason=reason + "; no legal candidate remains after rerouting", output={"code": "router-out-of-bounds"})
            return
        resolution = router.current_router(connection, now=now)
        request.update(resolution.facts)
        request["selectionRetryInspections"] = [dict(entry) for entry in resolution.inspections]
        if resolution.problem is not None:
            request["routerProblem"] = resolution.problem
        else:
            request.pop("routerProblem", None)
        if resolution.facts["routingMode"] is not None:
            request["routerBudgetSeconds"] = (60 if resolution.facts["routingMode"] == "fast"
                                         else resolution.facts["budget"]["timeoutSeconds"])
            request["budget"] = {**resolution.facts["budget"], "timeoutSeconds": request["routerBudgetSeconds"]}
        document, oversized = self._select_input(
            connection, request_id=row["request_id"], buddy_row=None, task_text=request["task"],
            candidates=candidates, hard_constraints=request.get("constraints") or {}, preserve_oversized=True)
        document.update({**resolution.facts, "budget": request.get("budget"),
                         "routingBasis": request["routingBasis"], "constraints": request.get("constraints", {}),
                         "requiredCapabilities": request.get("requiredCapabilities", [])})
        if resolution.facts["routingMode"] != "fast":
            document["executionWorkspace"] = request.get("executionWorkspace")
        if resolution.facts["routingMode"] is not None:
            document["outputSchema"] = router.answer_schema(
                [item["buddyId"] for item in document["buddies"]], resolution.facts["routingMode"])
        connection.execute("UPDATE decision_requests SET requested_json=?, input_json=? WHERE decision_id=?",
                           (canonical_json(request), canonical_json(document), row["decision_id"]))
        if oversized or resolution.buddy is None:
            problem = resolution.problem or {"code": "router-input-too-large", "reason": oversized}
            self._finish(connection, self._row(connection, row["decision_id"]), status="needs-host", now=now,
                         error=problem["code"], reason=reason + "; rerouting cannot continue: " + problem["reason"],
                         output={"code": problem["code"]})
            return
        self._record_preflight_walk(connection, row["request_id"], resolution.inspections, now=now, selection_retry=True)
        self._queue_router_dispatch(connection, self._row(connection, row["decision_id"]), resolution=resolution, now=now)

    @staticmethod
    def _attempt_tool_policy(connection: sqlite3.Connection, attempt_id: str) -> dict | None:
        """The toolPolicy this attempt froze at claim, or ``None`` before a claim."""
        row = connection.execute("SELECT tool_policy_json FROM router_claims WHERE attempt_id=?", (attempt_id,)).fetchone()
        return json.loads(row[0]) if row is not None and row[0] is not None else None

    def _tool_evidence_problem(self, row: sqlite3.Row, document: dict, output: dict) -> dict | None:
        """The blackboard wrapper over the unified tool-evidence judgment.

        ``zeroToolVerified`` never substitutes for the event stream. This
        wrapper binds a receipt's ``toolEvidence`` to the frozen attempt before
        the pure judge runs: the binding must equal the frozen
        harness/task/attempt/generation, every root identity the receipt reports
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
            "harness": (document.get("buddy") or {}).get("harness"),
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

    def _publish_select(self, connection: sqlite3.Connection, row: sqlite3.Row, *, output: dict, now: str) -> dict:
        """Check program facts without settling the request or judging the choice."""
        document = json.loads(row["input_json"]) if row["input_json"] else {}
        request = json.loads(row["requested_json"])
        checked = {"answer_valid": False, "abstained": False, "changed": False,
                   "code": None, "reason": "", "output": output, "buddy_id": None,
                   "selected": None, "evidence": []}

        def refuse(code, reason, *, changed=False):
            return {**checked, "code": code, "reason": reason, "changed": changed,
                    "output": {**output, "code": code}}

        # Check review input even when the helper failed: an explicit changed
        # copy/manifest is a circumstance boundary, never a Router no-answer.
        verification = output.get("inputVerification")
        manifest = document.get("executionWorkspace") or {}
        review = document.get("routingMode") == "review"
        if review and isinstance(verification, dict) and (
                verification.get("unchanged") is False
                or ("manifestSha256" in verification and verification["manifestSha256"] != manifest.get("manifestSha256"))):
            return refuse("router-input-changed", "Router input verification differs from the frozen copy or manifest", changed=True)
        stop = output.get("stopEvidence")
        if (not isinstance(stop, dict) or stop.get("shutdownConfirmed") is not True
                or not isinstance(stop.get("native"), dict) or stop["native"].get("shutdownConfirmed") is not True):
            return refuse("router-stop-unconfirmed", "Router publication requires native and controller stop evidence")
        if output.get("status") != "ok":
            code = output.get("code")
            code = code if (isinstance(code, str) and code.strip() and len(code) <= 128
                            and not any(ord(c) < 32 or ord(c) == 127 for c in code)) else "router-no-answer"
            return refuse(code, str(output.get("reason") or output.get("error") or "The bounded Router call produced no usable answer")[:MAX_DECISION_REASON])
        if review and (not isinstance(verification, dict) or verification.get("unchanged") is not True
                       or not isinstance(verification.get("snapshotSha256"), str) or not verification["snapshotSha256"]
                       or "manifestSha256" not in verification):
            # Missing/malformed proof invalidates an answer; it does not prove
            # that the input changed. Failed calls need no successful answer proof.
            return refuse("router-input-changed", "Router publication requires complete frozen input verification")
        if output.get("operation") != row["kind"]:
            return refuse("answer-shape", "The helper answered a different operation")
        usage = output.get("usage")
        budget = document.get("budget") or {}
        if (not isinstance(usage, dict) or type(usage.get("elapsedMs")) is not int
                or usage["elapsedMs"] < 0 or type(usage.get("toolCalls")) is not int or usage["toolCalls"] < 0):
            return refuse("router-tool-evidence-unverified", "Router publication requires elapsed time and native tool count evidence")
        if (usage["elapsedMs"] > budget["timeoutSeconds"] * 1000
                or (document.get("routingMode") == "review" and usage["toolCalls"] > budget["toolCalls"])):
            return refuse("router-budget-exhausted", "Router publication exceeds the frozen time or cumulative tool budget")
        policy = self._attempt_tool_policy(connection, row["decision_attempt_id"])
        problem = self._tool_evidence_problem(row, {**document, "toolPolicy": policy}, output)
        if problem is None and usage["toolCalls"] != output["toolEvidence"]["toolCalls"]:
            problem = {"code": "router-tool-evidence-unverified", "reason": "The receipt's tool count differs from its native events"}
        if problem is not None:
            return refuse(problem["code"], problem["reason"])
        buddies = document.get("buddies") or []
        try:
            decision = router.validate_answer(output.get("decision"), [item["buddyId"] for item in buddies], document.get("routingMode", "review"))
        except BoardError as failure:
            return refuse(failure.code, failure.message)
        reason, buddy_id = decision["reason"], decision["buddyId"]
        if buddy_id is None:
            return {**checked, "answer_valid": True, "abstained": True, "reason": reason,
                    "output": {**output, "decision": decision, "policyCheck": None}, "evidence": decision["evidence"]}
        # Recheck hard bounds only. Cached harness health/Router qualification
        # can change during a valid native call; they govern the next request,
        # not adoption of this answer. Keep the original quota retry inspection.
        clauses, values = self._candidate_bounds(
            required_capabilities=request.get("requiredCapabilities") or [],
            constraints=request.get("constraints"), coding_only=True)
        clauses = [clause for clause in clauses if clause !=
                   "EXISTS(SELECT 1 FROM harness_health h WHERE h.harness=p.harness AND h.status='ready')"]
        clauses.extend(["p.buddy_id=?", "COALESCE(f.mode,'')!='exclude'",
                        "(NOT EXISTS(SELECT 1 FROM evaluation_preferences WHERE mode='pin') OR f.mode='pin')"])
        candidate = connection.execute(
            "SELECT p.* FROM buddies p LEFT JOIN evaluation_preferences f ON f.buddy_id=p.buddy_id WHERE "
            + " AND ".join(clauses), [*values, buddy_id]).fetchone()
        selected = next(dict(item) for item in buddies if item["buddyId"] == buddy_id)
        from ..evaluation.native_observations import exhausted
        from .quota_routing import claim
        if (candidate is None or any(candidate[key] != selected[key] for key in schemas.BUDDY_FIELDS)
                or (exhausted(connection, candidate, now=now) is not None
                    and claim(connection, candidate, decision_id=row["decision_id"], consume=False, now=now) is None)):
            current = connection.execute("SELECT p.*,f.mode AS preference_mode FROM buddies p"
                                         " LEFT JOIN evaluation_preferences f ON f.buddy_id=p.buddy_id WHERE p.buddy_id=?",
                                         (buddy_id,)).fetchone()
            problem = ("removed from the candidate table" if current is None else
                       "disabled" if not current["enabled"] else
                       "unavailable" if not current["available"] else
                       "excluded by the user" if current["preference_mode"] == "exclude" else
                       "missing a required task capability" if not set(request.get("requiredCapabilities") or []) <= set(json.loads(current["capabilities_json"])) else
                       "outside current pins, task constraints, or quota limits")
            return refuse("router-out-of-bounds", f"Selected buddy {buddy_id} is {problem}", changed=True)
        expected_facts = selection_policy.policy_facts(
            buddies=buddies, prefer_buddy_ids=[item["buddyId"] for item in document.get("preferences", []) if item.get("mode") == "prefer"],
            hard_constraints=request.get("constraints") or {})
        policy_check = selection_policy.expected_policy_check(expected_facts, buddy_id)
        return {**checked, "answer_valid": True, "reason": reason, "buddy_id": buddy_id,
                "evidence": decision["evidence"], "selected": selected,
                "output": {**output, "decision": decision, "policyCheck": policy_check}}

    def _decision_for_task(self, connection: sqlite3.Connection, task: sqlite3.Row) -> sqlite3.Row | None:
        spec = json.loads(task["spec_json"])
        descriptor = decision_spec(spec)
        if not descriptor:
            return None
        row = self._row(connection, descriptor["decisionId"])
        return row if row is not None and row["decision_task_id"] == task["task_id"] else None

    def _status(self, connection: sqlite3.Connection, decision_id: str) -> str:
        return connection.execute(
            "SELECT status FROM evaluation_decisions WHERE decision_id=?", (decision_id,)
        ).fetchone()["status"]

    def _finish(self, connection: sqlite3.Connection, row: sqlite3.Row, *, status: str, now: str, reason: str | None = None, error: str | None = None, buddy_id: str | None = None, output: dict | None = None, selected: dict | None = None) -> None:
        """One atomic terminal transition for a decision and its audit material."""
        current = self._row(connection, row["decision_id"])
        if (current is None or (current["decision_task_id"] is not None and current["status"] in TERMINAL_DECISION_STATUSES)
                or current["decision_task_id"] != row["decision_task_id"]
                or current["decision_attempt_id"] != row["decision_attempt_id"]
                or current["decision_generation"] != row["decision_generation"]):
            return
        if status == "completed" and buddy_id and selected:
            from .quota_routing import claim
            # Candidate inspection must not spend an unchosen provider's retry.
            # Selection and this single-use claim share the write transaction;
            # another selector either sees exhaustion or rechecks its bounds.
            if claim(connection, selected, decision_id=row["decision_id"], now=now) is None:
                status, buddy_id, selected = "needs-host", None, None
                reason, error = "The selected quota retry was consumed by another decision", "router-out-of-bounds"
                output = {**(output or {}), "code": error}
        if status not in DECISION_STATUSES:
            raise BoardError("INTERNAL_ERROR", f"Unknown decision status {status!r}")
        connection.execute(
            "UPDATE evaluation_decisions SET status=?, reason=?, buddy_id=?, error=?"
            " WHERE decision_id=?",
            (
                status,
                (reason or "")[:MAX_DECISION_REASON],
                buddy_id,
                error,
                row["decision_id"],
            ),
        )
        connection.execute(
            "UPDATE decision_requests SET output_json=COALESCE(?, output_json),"
            " selected_json=COALESCE(?, selected_json), updated_at=? WHERE decision_id=?",
            (
                canonical_json(output) if output is not None else None,
                canonical_json(selected) if selected is not None else None,
                now,
                row["decision_id"],
            ),
        )
        event = {
            "completed": "decision.completed",
            "needs-host": "decision.needs_host",
            "failed": "decision.failed",
            "cancelled": "decision.cancelled",
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
                "buddyId": buddy_id,
                "reason": (reason or "")[:MAX_DECISION_REASON],
                "error": error,
                "errorCode": error_code,
            },
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
            payload={"reason": reason, "actor": {"role": "service", "id": "service"}, "phase": "queued"},
        )

    def _adopt_sole_candidate(self, connection: sqlite3.Connection, row: sqlite3.Row, *, candidate: sqlite3.Row, request: dict, now: str) -> None:
        """Select the single frozen legal candidate without any Router evidence.

        The program owns this choice outright: no Router attempt, model call,
        usage counter or stop evidence is recorded, because none happened. The
        hard constraints, required capabilities and the preference check are
        still derived from the frozen request and stored exactly as a Router
        answer's would be.
        """
        buddy_id = candidate["buddy_id"]
        buddies = [self._buddy_input(candidate)]
        preferences = user_policy.preferences(connection, [buddy_id])
        facts = selection_policy.policy_facts(
            buddies=buddies,
            prefer_buddy_ids=[entry["buddyId"] for entry in preferences if entry["mode"] == "prefer"],
            hard_constraints=request.get("constraints") or {})
        policy_check = selection_policy.expected_policy_check(facts, buddy_id)
        selected = dict(buddies[0])
        self._finish(
            connection,
            row,
            status="completed",
            now=now,
            reason=SOLE_CANDIDATE_REASON,
            buddy_id=buddy_id,
            selected=selected,
            output={
                "programSelection": {
                    "code": "single-candidate",
                    "buddyId": buddy_id,
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
        """Bounded, model-free health: the request overview plus per-buddy facts.

        The total window keeps its original diagnostic meaning: one settled
        business request is one window entry however many dispatch attempts it
        had, and ``attemptCount`` names the entries that reached a model. Only an
        adopted Router success resets the failure streak; neutral events and
        program selections cannot prove recovery, and immutable event codes and
        bound attempt receipts survive late changes to the retained output.

        ``available``, ``reasonCode`` and ``currentRouterBuddyId`` come from the
        one ordered-list resolution entry and its skip periods, never from the old
        three-failure diagnostic threshold. Each ``routers`` entry is projected by
        the R2 history interfaces from that buddy's own immutable facts and
        receipts: a success recovers only its own buddy, a valid abstention is an
        answer, the consecutive count ignores the display window, and budget
        exhaustion (including timeout codes and the read-only variant) counts as
        failures too. Records whose attribution or nature cannot be proven are
        listed under ``unattributed`` and never guessed onto any buddy. Reading
        writes no events, timers, settings or probe sessions.
        """
        from .router_history import buddy_timeline, router_state, unattributed_records
        limit = 20
        kinds = "('decision.completed','decision.failed','decision.needs_host','decision.cancelled')"
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
            now = self._now()
            rows = connection.execute(
                "SELECT e.kind,e.created_at,e.payload_json,r.decision_id,r.task_id,r.output_json,"
                " a.result_json AS receipt_json,(" + router_success + ") AS router_success,"
                " (a.attempt_id IS NOT NULL) AS has_attempt,"
                " (SELECT w.run_id FROM workflow_routes w WHERE w.decision_id=r.decision_id LIMIT 1) AS micro_task_id"
                + source + " ORDER BY e.seq DESC LIMIT ?", (limit,),
            ).fetchall()
            success = connection.execute(
                "SELECT e.created_at,r.decision_id" + source
                + " AND " + router_success + " ORDER BY e.seq DESC LIMIT 1",
            ).fetchone()
            resolution = router.current_router(connection, now=now)
            failed = []
            streak = 0
            still_failing = True
            all_timeouts = True
            timeout_codes = {"timeout", "call-timeout", "deadline", "router-timeout"}
            abstentions = cancellations = 0
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
                             and payload.get("errorCode") is None and output.get("code") is None
                             and receipt.get("status") == "ok" and receipt.get("shutdownConfirmed") is True
                             and output.get("modelStarted") is not False
                             and isinstance(decision, dict) and decision.get("buddyId", False) is None
                             and set(decision) == {"buddyId", "reason", "evidence"})
                original_code = output.get("code")
                diagnostic_code = original_code if isinstance(original_code, str) and original_code in special_counts else code
                special = row["kind"] in ("decision.failed", "decision.needs_host") and diagnostic_code in special_counts
                if special:
                    special_counts[diagnostic_code] += 1
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
                if is_failure:
                    failed.append({"decisionId":row["decision_id"], "runId":row["micro_task_id"] or row["task_id"],
                                   "at":row["created_at"], "code":code})
            facts = resolution.facts
            interval = facts.get("routerRetryIntervalSeconds")
            mode = facts.get("routingMode")
            ids = facts.get("routerBuddyIds") or []
            identities = facts.get("routerIdentities") or []
            inspected = {entry["index"]: entry for entry in resolution.inspections}
            budget_codes = frozenset({*timeout_codes, "router-budget-exhausted", "readonly-budget-exhausted"})
            routers = []
            for index, buddy_id in enumerate(ids):
                # A nonempty list always carries validated settings, so the
                # interval is an integer; an absent one has no entries to project.
                state = router_state(connection, buddy_id=buddy_id,
                                     interval_seconds=interval if isinstance(interval, int) else 1, now=now)
                window = buddy_timeline(connection, buddy_id=buddy_id)[-limit:]
                inspection = inspected.get(index)
                if inspection is not None:
                    eligible, code = inspection["eligible"], inspection["code"]
                elif state["inSkipWindow"]:
                    eligible, code = False, "router-skip-window"
                else:
                    buddy, code, _reason = router.buddy_problem(connection, buddy_id, mode)
                    eligible = buddy is not None
                last_error = next((entry for entry in reversed(window) if entry["outcome"] == "no_answer"), None)
                routers.append({
                    "buddyId": buddy_id, "index": index,
                    "identity": identities[index] if index < len(identities) else None,
                    "eligible": eligible, "code": code,
                    "inSkipWindow": state["inSkipWindow"], "skipUntil": state["skipUntil"],
                    "retryAt": state["retryAt"], "retryInProgress": state["retryInProgress"],
                    "answeredCount": state["answeredCount"], "noAnswerCount": state["noAnswerCount"],
                    "lastAnsweredAt": state["lastAnsweredAt"], "lastNoAnswerAt": state["lastNoAnswerAt"],
                    "consecutiveNoAnswers": state["consecutiveNoAnswers"],
                    "windowSize": limit, "windowEntries": len(window),
                    "windowAnsweredCount": sum(entry["outcome"] == "answered" for entry in window),
                    "windowFailureCount": sum(entry["outcome"] == "no_answer" for entry in window),
                    "windowBudgetExhaustedCount": sum(entry["outcome"] == "no_answer"
                                                      and entry.get("code") in budget_codes for entry in window),
                    "windowBoundsRejectedCount": sum(entry["outcome"] == "no_answer"
                                                     and entry.get("code") == "router-out-of-bounds"
                                                     for entry in window),
                    "windowAttemptCount": sum(entry.get("attemptId") is not None for entry in window),
                    "lastError": None if last_error is None else {
                        "code": last_error.get("code"), "at": last_error["at"], "phase": last_error.get("phase")},
                })
            unattributed = unattributed_records(connection)
        return {"available":resolution.buddy_id is not None,
                "reasonCode":resolution.problem["code"] if resolution.problem else None,
                "currentRouterBuddyId":resolution.buddy_id,
                "routers":routers,
                "windowSize":limit, "sampleCount":len(rows), "failureCount":len(failed),
                "attemptCount":sum(bool(row["has_attempt"]) for row in rows),
                "budgetExhaustedCount":special_counts["router-budget-exhausted"],
                "boundsRejectedCount":special_counts["router-out-of-bounds"],
                "inputChangedCount":special_counts["router-input-changed"],
                "consecutiveFailures":streak, "abstentionCount":abstentions,
                "cancelledCount":cancellations,
                "lastSuccessAt":success["created_at"] if success else None,
                "lastSuccessDecisionId":success["decision_id"] if success else None,
                "recentFailures":failed[:5],
                "unattributedCount":len(unattributed),
                "unattributed":[{"decisionId":entry["decisionId"], "at":entry["at"],
                                 "outcome":entry["outcome"], "code":entry["code"]}
                                for entry in reversed(unattributed[-5:])]}

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
        selected worker buddy and the decision's own model identity as
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

        This is a read: it calls no model
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
            raise BoardError("INVALID_ARGUMENT", "kind must be 'select'", field="kind")
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
        if connection is None:
            # Admission responses are assembled after their write transaction.
            # Read the same immutable dispatch/packet projection as get without
            # retaining a closed connection or probing any harness.
            with self.board.db.read() as reading:
                return self._view(reading, row, include_audit=include_audit)
        output = json.loads(row["output_json"]) if row["output_json"] else None
        request = json.loads(row["requested_json"]) if row["requested_json"] else {}
        actor = (router_sequence.dispatch(connection, row["decision_task_id"])
                 if connection is not None and row["decision_task_id"] else None)
        selected = json.loads(row["selected_json"]) if row["selected_json"] else None
        answer = (output or {}).get("decision")
        evidence = (answer["evidence"] if row["status"] in ("completed", "needs-host")
                    and isinstance(output, dict) and output.get("status") == "ok" and "code" not in output
                    and isinstance(answer, dict) and isinstance(answer.get("evidence"), list) else [])
        view: dict[str, Any] = {
            "decisionId": row["decision_id"],
            "requestId": row["request_id"],
            "kind": row["kind"],
            "status": row["status"],
            "task": row["task"],
            "runId": row["decision_task_id"],
            "buddyId": row["buddy_id"],
            # The frozen execution identity the Host can delegate with directly,
            # captured from the admitted input. ``decisionModel`` is the decision
            # call's own requested/resolved identity and is deliberately separate.
            "selectedBuddy": selected,
            "decisionModel": {
                "requested": actor["buddy"] if actor else None,
                "resolved": (output or {}).get("resolved"),
                "observed": (output or {}).get("observed"),
            },
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
            **router.routing_facts(request, actor=actor),
            "usage": (output or {}).get("usage"),
            "nativeIdentity": (output or {}).get("nativeIdentity"),
            "stopEvidence": (output or {}).get("stopEvidence"),
            "inputVerification": (output or {}).get("inputVerification"),
            "createdAt": row["created_at"],
            "updatedAt": row["request_updated_at"],
        }
        if actor is None:
            view.update(routerBuddyId=None, routerBuddy=None, routerIndex=None)
        if row["error"]:
            view["error"] = row["error"]
        if connection is not None:
            from .router_boundary_data import routing_boundary, router_trials
            projection_now = self._now()
            view["routerTrials"] = router_trials(connection, row, now=projection_now)
            boundary = routing_boundary(connection, row, now=projection_now)
            if boundary is not None:
                view["routingBoundary"] = boundary
        if "taskReference" in request:
            view["taskReference"] = request["taskReference"]
        if include_audit:
            input_document = json.loads(row["input_json"]) if row["input_json"] else None
            view.update(
                {
                    "attemptId": row["decision_attempt_id"],
                    "generation": row["decision_generation"],
                    "requested": request,
                    # The exact bounded fields sent to the model, retained for audit.
                    "input": input_document,
                    "inputSha256": row["input_sha256"],
                    "output": output,
                    # The decision call's own buddy, kept separate from the
                    # selected worker buddy above.
                    "requestedBuddy": actor["buddy"] if actor else None,
                    "resolvedBuddy": (output or {}).get("resolved"),
                    "observedBuddy": (output or {}).get("observed"),
                    "usage": (output or {}).get("usage"),
                    "elapsedSeconds": (output or {}).get("elapsedSeconds"),
                    "helperShutdownConfirmed": (output or {}).get("shutdownConfirmed"),
                }
            )
        return view


__all__ = ["DECISION_ADAPTER", "DECISION_STATUSES", "DecisionCoordinator", "decision_spec"]
