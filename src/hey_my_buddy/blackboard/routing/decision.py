"""Durable Router attempts with Python-owned frozen inputs and boundary checks.

The blackboard owns publication and atomic terminal events.
The execution process owns native handles, input verification and stop receipts;
the service enforces external deadlines.
Router answers choose a legal buddy or abstain; preference judgments are
recorded, never used as an additional veto.
"""
from __future__ import annotations

from ...protocol.capabilities import buddy_capabilities
from .candidate import candidate_bound

from contextlib import nullcontext

import json
import sqlite3
import uuid
from typing import Any

from ...protocol import schemas
from ..evaluation import user_policy
from . import selection_policy, router, router_sequence, router_failover
from ...protocol import tool_evidence
from ...protocol.reasons import reason as make_reason, reason_from_json
from ..store.db import canonical_json, sha256_text
from ...errors import BoardError

DECISION_ADAPTER = "decision"
#: Durable decision states. ``needs-host`` is the honest abstention/out-of-policy
#: outcome; unknown process outcomes cannot become a recommendation.
DECISION_STATUSES = ("queued", "running", "completed", "needs-host", "failed", "cancelled")
TERMINAL_DECISION_STATUSES = frozenset({"completed", "needs-host", "failed", "cancelled"})

SELECT_FIELDS = frozenset({"requestId", "task", "requiredCapabilities"})
GET_FIELDS = frozenset({"decisionId", "includeAudit"})
LIST_FIELDS = frozenset({"limit", "before"})
DEFAULT_LIST_LIMIT = 20
MAX_LIST_LIMIT = 100

#: The shared row projection for one decision. The paginated list reader adds
#: ``decision_requests.rowid`` as its stable keyset; nothing here persists anything.
_ROW_COLUMNS = (
    "d.decision_id, d.status, d.task, d.buddy_id, d.reason_code, d.reason_params_json, d.note,"
    " d.created_at, r.request_id, r.input_fingerprint,"
    " r.task_id AS decision_task_id, r.attempt_id AS decision_attempt_id,"
    " r.generation AS decision_generation,"
    " r.selected_json, r.input_json, r.input_sha256, r.output_json,"
    " r.requested_json,"
    " r.updated_at AS request_updated_at"
)
_ROW_FROM = " FROM decision_requests r JOIN evaluation_decisions d ON d.decision_id = r.decision_id"

MAX_DECISION_TASK_BYTES = 8192
ANSWER_VALIDATION_CODES = frozenset({
    "answer-empty", "answer-not-json", "answer-invalid-json", "answer-shape",
    "answer-unexpected-field",
})
MAX_DECISION_INPUT_BYTES = 128 * 1024
#: Extra room after the helper's own bound: the worker deadline must not fire while
#: the helper is still shutting its detached child down.
TIMEOUT_GRACE_SECONDS = 10

def decision_spec(spec: dict) -> dict | None:
    """The decision descriptor of a task specification, when this is a decision task."""
    return spec.get("decision")


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
    @staticmethod
    def _scan_candidates(connection: sqlite3.Connection, required_capabilities: list[str], *, constraints: dict | None = None, coding_only: bool = False, now: str | None = None) -> tuple[list[sqlite3.Row], list[dict]]:
        """Freeze eligible candidates and the otherwise eligible buddies cooling down."""
        from .quota_routing import cooling
        request = {"requiredCapabilities": required_capabilities, "constraints": constraints or {},
                   "codingOnly": coding_only}
        selected, cooling_buddies = [], []
        has_pins = connection.execute("SELECT 1 FROM evaluation_preferences WHERE mode='pin' LIMIT 1").fetchone() is not None
        for row in connection.execute(
            "SELECT p.*,f.note AS preference_note FROM buddies p"
            " LEFT JOIN evaluation_preferences f ON f.buddy_id=p.buddy_id ORDER BY p.rowid"):
            bound = candidate_bound(connection, row, request, now, has_pins=has_pins)
            if bound is None:
                selected.append(row)
            elif bound == "quota-exhausted":
                state = cooling(connection, row, now=now)
                cooling_buddies.append({"buddyId": row["buddy_id"],
                    **{key: row[key] for key in schemas.BUDDY_FIELDS},
                    "note": row["preference_note"], "coolingUntil": state["coolingUntil"]})
        return selected, cooling_buddies

    @staticmethod
    def _no_candidates_reason(cooling_buddies):
        if cooling_buddies:
            return make_reason("all-candidates-cooling", retryAt=min(item["coolingUntil"] for item in cooling_buddies))
        return make_reason("no-legal-candidate")

    def _excluded_buddies(self, connection: sqlite3.Connection, *, required_capabilities: list[str], constraints: dict | None = None) -> tuple[list[dict], int]:
        """The user-excluded buddies inside these hard bounds, frozen now.

        These are the exclusions that narrowed this request's candidate set: each
        buddy passes every hard bound (enabled, available, healthy, complete
        coding tuple, required capabilities, fixed fields) and only the user's
        effective ``exclude`` preference removes it. Pins are not considered,
        because a pin narrows the set without excluding anything.
        """
        request = {"requiredCapabilities": required_capabilities, "constraints": constraints or {}}
        rows = [row for row in connection.execute(
            "SELECT p.*, f.note AS preference_note FROM buddies p"
            " JOIN evaluation_preferences f ON f.buddy_id=p.buddy_id WHERE f.mode='exclude' ORDER BY p.rowid")
            if candidate_bound(connection, row, request, self._now(), apply_preferences=False, check_cooling=False) is None]
        entries = [
            {
                "buddyId": row["buddy_id"],
                "harness": row["harness"],
                "provider": row["provider"],
                "model": row["model"],
                "effort": row["effort"],
                "note": row["preference_note"],
            }
            for row in rows
        ]
        return entries, len(entries)

    def _freeze_routing_basis(self, connection: sqlite3.Connection, *, candidates: list[sqlite3.Row], required_capabilities: list[str], constraints: dict | None = None, cooling_buddies: list[dict]) -> dict:
        """Submission-time routing facts: the frozen candidate count and exclusions.

        The basis is recorded once with the request and never recomputed, so a
        later preference change cannot rewrite why an earlier route had few or
        no candidates. The submission's hard constraints stay in the request
        itself; this object carries what the constraints alone cannot show.
        """
        excluded, excluded_count = self._excluded_buddies(
            connection, required_capabilities=required_capabilities, constraints=constraints)
        return {"candidateCount": len(candidates), "excludedCount": excluded_count, "excludedBuddies": excluded,
                "coolingCount": len(cooling_buddies), "coolingBuddies": cooling_buddies}

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
            "capabilities": buddy_capabilities(row["harness"], json.loads(row["capabilities_json"])),
            "contextWindow": row["context_window"],
            "description": row["description"],
            "source": row["source"],
        }
        if row["unavailable_code"]:
            entry["unavailableReason"] = reason_from_json(row["unavailable_code"], row["unavailable_params_json"])
            if row["unavailable_content"] is not None:
                entry["unavailableContent"] = row["unavailable_content"]
        return entry

    def _table_input(
        self,
        connection: sqlite3.Connection,
        *,
        buddies: list[sqlite3.Row],
        include_preferences: bool,
    ) -> dict:
        """The complete bounded table slice for one request — never a silent truncation.

        Every legal candidate, preference and model note is included. The final
        dispatch checks its byte ceiling without dropping entries.
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

    def _select_input(self, connection: sqlite3.Connection, *, request_id: str, task_text: str, candidates: list[sqlite3.Row], hard_constraints: dict | None = None) -> dict:
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
        return {"operation": "select", "requestId": request_id, "task": task_text, **table}

    # -- request operations --------------------------------------------------
    def request_select(self, params: dict) -> dict:
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

    def _create(self, request_id: str, request: dict, *, connection=None, needs_host_reason: dict | None = None) -> dict:
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
            # call, so that path cannot depend on Router availability.
            candidates, cooling_buddies = self._scan_candidates(
                connection, request.get("requiredCapabilities", []),
                constraints=request.get("constraints"), coding_only=True, now=now)
            request["routingBasis"] = self._freeze_routing_basis(
                connection, candidates=candidates,
                required_capabilities=request.get("requiredCapabilities", []),
                constraints=request.get("constraints"), cooling_buddies=cooling_buddies)
            status = "queued"
            # New decisions are always selection requests; there is no internal
            # maintenance request path left on the blackboard.
            reason = None
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
                status, reason = "completed", make_reason("single-candidate")
            elif not candidates:
                request.update(facts)
                status, reason = "needs-host", self._no_candidates_reason(cooling_buddies)
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
                base_input = self._select_input(
                    connection, request_id=request_id,
                    task_text=task_text, candidates=candidates,
                    hard_constraints=request.get("constraints") or {})
                base_input.update(facts)
                base_input["routingBasis"] = request["routingBasis"]
                base_input["constraints"] = request.get("constraints", {})
                base_input["requiredCapabilities"] = request.get("requiredCapabilities", [])
                # Review routing includes the frozen execution workspace.
                if facts["routingMode"] != "fast":
                    base_input["executionWorkspace"] = request.get("executionWorkspace")
                if facts["routingMode"] is not None:
                    base_input["outputSchema"] = router.answer_schema(
                        [item["buddyId"] for item in base_input["buddies"]], facts["routingMode"])
                snapshot = {"facts": facts, "baseInput": base_input,
                            "inspections": [dict(entry) for entry in resolution.inspections]}
                if needs_host_reason is not None:
                    status, reason = "needs-host", needs_host_reason
                elif facts["routingMode"] is None or resolution.buddy is None:
                    status, reason = "needs-host", resolution.problem
            task_id = None
            connection.execute(
                "INSERT INTO evaluation_decisions(decision_id, status, task, buddy_id, reason_code,"
                " reason_params_json, created_at) VALUES(?,?,?,?,?,?,?)",
                (
                    decision_id,
                    status,
                    task_text,
                    None,
                    reason["code"] if reason else None,
                    canonical_json(reason["params"]) if reason else None,
                    now,
                ),
            )
            connection.execute(
                "INSERT INTO decision_requests(decision_id, request_id, input_fingerprint,"
                " task_id, requested_json, updated_at) VALUES(?,?,?,?,?,?)",
                (
                    decision_id,
                    request_id,
                    fingerprint,
                    None,
                    canonical_json(request),
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
                task_id, problem = self._queue_router_dispatch(
                    connection, self._row(connection, decision_id), resolution=resolution, now=now)
                if problem is not None:
                    status, reason = "needs-host", problem
                    connection.execute("UPDATE evaluation_decisions SET status=?,reason_code=?,reason_params_json=? WHERE decision_id=?",
                                       (status, reason["code"], canonical_json(reason["params"]), decision_id))
            self._append_event(
                connection,
                "decision.requested",
                decision_id,
                {
                    "requestId": request_id,
                    "kind": kind,
                    "status": status,
                    "taskId": task_id,
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
            needs_host_reason = make_reason("router-input-too-large", bytes=task_bytes, limit=MAX_DECISION_TASK_BYTES)
        return self._create(f"workflow:{run_id}:{sequence}", request, connection=connection,
                            needs_host_reason=needs_host_reason)

    def _create_task(self, connection: sqlite3.Connection, *, decision_id: str, request_id: str, router_budget_seconds: int, now: str, request_key: str | None = None) -> str:
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
            "decision": {"decisionId": decision_id, "kind": "select", "routerBudgetSeconds": router_budget_seconds},
        }
        fingerprint = schemas.spec_fingerprint(spec)
        blocker = self.board._admission_blocker(connection, spec)
        connection.execute(
            "INSERT INTO tasks(task_id, request_id, owner, spec_json, input_fingerprint,"
            " fingerprint_version, adapter, required_capabilities, cwd, exclusive_resources, router_budget_seconds,"
            " state, queue_reason, revision, created_at, updated_at)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                task_id,
                request_key,
                "decision",
                canonical_json(spec),
                fingerprint,
                schemas.FINGERPRINT_VERSION_CURRENT,
                DECISION_ADAPTER,
                canonical_json(spec["requiredCapabilities"]),
                spec["cwd"],
                canonical_json([]),
                spec["routerBudgetSeconds"],
                "queued",
                blocker or "awaiting-execution",
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
                "queueReason": blocker or "awaiting-execution",
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
                plane="routing",
                request_id=request_id,
                task_id=None,
                attempt_id=None,
                outcome="no_answer",
                code=code,
                phase="preflight",
                facts={"routerIndex": entry["index"], "selectionRetry": selection_retry},
                now=now,
            )

    def _queue_router_dispatch(self, connection: sqlite3.Connection, row: sqlite3.Row, *, resolution, now: str) -> tuple[str | None, dict | None]:
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
            return None, make_reason("router-input-too-large", bytes=input_bytes, limit=MAX_DECISION_INPUT_BYTES)
        request = json.loads(row["requested_json"])
        router_budget_seconds = int(request["routerBudgetSeconds"])
        task_id = self._create_task(
            connection,
            decision_id=decision_id,
            request_id=row["request_id"],
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
        connection.execute("UPDATE evaluation_decisions SET status='queued',buddy_id=NULL,reason_code=NULL,reason_params_json=NULL,note=NULL WHERE decision_id=?",
                           (decision_id,))
        return task_id, None

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
        row = self._row(connection, descriptor["decisionId"])
        if row["status"] != "queued":
            return "decision-closed"
        if row["decision_task_id"] != task["task_id"]:
            # A superseded dispatch never claims; its queued shell closes honestly.
            self._cancel_queued_task(connection, task, now)
            return "decision-dispatch-superseded"
        dispatch = router_sequence.dispatch(connection, task["task_id"])
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
        self._cancel_queued_task(connection, task, now)
        advance = router.current_router(connection, frozen=facts, after_index=dispatch["routerIndex"], now=now)
        self._record_preflight_walk(connection, row["request_id"], advance.inspections, now=now, selection_retry=bool(json.loads(row["requested_json"]).get("selectionRetry")))
        if advance.buddy is None:
            self._finish(connection, row, status="needs-host", now=now,
                         reason=advance.problem,
                         output={"status": "error", "code": advance.problem["code"]})
            return "decision-closed"
        _task_id, problem = self._queue_router_dispatch(connection, row, resolution=advance, now=now)
        if problem is not None:
            self._finish(connection, row, status="needs-host", now=now, reason=problem, output={"status": "error", "code": problem["code"]})
            return "decision-closed"
        return "decision-dispatch-advanced"

    # -- worker lifecycle hooks ---------------------------------------------
    def claim(self, connection: sqlite3.Connection, *, task: sqlite3.Row, spec: dict, attempt_id: str, generation: int, now: str) -> dict:
        """Admit one decision attempt inside the claim transaction.

        Every case that keeps the task queued was already settled by
        :meth:`prepare_router_claim` above, so this only succeeds. The decision
        becomes ``running``, the attempt is bound to the dispatch, and the exact
        model input is persisted for audit.
        """
        row = self._row(connection, decision_spec(spec)["decisionId"])
        dispatch = router_sequence.dispatch(connection, task["task_id"])
        document = dispatch["document"]
        self._mark_running(
            connection, row, attempt_id=attempt_id, generation=generation,
            document=document, now=now,
        )
        # The attempt's Router binding and its ``router.claimed`` fact commit in
        # this same claim transaction: one atomic dispatch-to-attempt binding.
        router_sequence.record_claim(
            connection, task_id=task["task_id"], attempt_id=attempt_id, generation=generation, now=now)
        return document

    def _mark_running(self, connection: sqlite3.Connection, row: sqlite3.Row, *, attempt_id: str, generation: int, document: dict, now: str) -> None:
        digest = sha256_text(canonical_json(document))
        connection.execute(
            "UPDATE decision_requests SET attempt_id=?, generation=?, input_json=?, input_sha256=?, updated_at=? WHERE decision_id=?",
            (attempt_id, generation, canonical_json(document), digest, now, row["decision_id"]),
        )
        connection.execute("UPDATE evaluation_decisions SET status='running', reason_code=NULL,reason_params_json=NULL WHERE decision_id=?",
                           (row["decision_id"],))
        self._append_event(connection, "decision.started", row["decision_id"],
                           {"attemptId": attempt_id, "generation": generation, "inputSha256": digest})

    def released(self, connection: sqlite3.Connection, *, task: sqlite3.Row, reason: dict, now: str) -> dict | None:
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
        # Only the service's explicit pre-spawn release evidence authorizes the
        # next Router; a generic shutdown flag alone does not.
        stopped = (proof.get("releaseEvidence") == "processCreated=false"
                   and attempt["shutdown_confirmed"] == 1 and attempt["execution_state"] == "finished")
        return self._complete(connection, row=row, task=task, attempt=attempt, status="failed",
                              result={"status": "error", "code": reason["code"]},
                              shutdown_confirmed=stopped, error=None, now=now,
                              release_stopped=stopped, released=True)

    def cancelled(self, connection: sqlite3.Connection, *, task: sqlite3.Row, reason: dict, now: str) -> dict | None:
        """Durable cancel intent: the decision is cancelled, not silently forgotten."""
        row = self._decision_for_task(connection, task)
        if row is None:
            return None
        if row["status"] not in TERMINAL_DECISION_STATUSES:
            self._finish(
                connection,
                row,
                status="cancelled",
                reason=reason,
                now=now,
            )
        return {"decisionId": row["decision_id"], "status": self._status(connection, row["decision_id"])}

    def fence_attempt(self, connection: sqlite3.Connection, *, attempt: sqlite3.Row, reason: dict, now: str) -> None:
        """Fence a decision when its execution is settled as interrupted."""
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
                reason=reason,
                now=now,
            )

    # -- completion ----------------------------------------------------------
    def complete(self, connection: sqlite3.Connection, *, task: sqlite3.Row, attempt: sqlite3.Row, status: str, result: Any, shutdown_confirmed: bool, error: str | None, now: str) -> dict | None:
        """Validate and publish (or refuse) one decision inside the result transaction.

        A BoardError rolls publication back to its savepoint and settles the
        decision inside the result transaction. Programming errors propagate and
        roll back that transaction: the service returns retryable INTERNAL_ERROR,
        while the execution retains and redelivers its durable receipt. That
        receipt can settle only after the programming error is fixed.
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
            self._record_publication_issue(connection, attempt, failure, now)
            failure_reason = make_reason("decision-publication-failed", errorCode=failure.code)
            self._finish(
                connection,
                row,
                status="needs-host",
                now=now,
                reason=failure_reason,
            )
            return {
                "decisionId": row["decision_id"],
                "status": "needs-host",
                "reason": failure_reason,
            }
        connection.execute("RELEASE decision_publication")
        return summary

    def _record_publication_issue(self, connection, attempt, failure, now: str) -> None:
        code = failure.code
        message = failure.message
        connection.execute(
            "INSERT INTO execution_issues(attempt_id,stage,code,message,observed_at) VALUES(?,?,?,?,?)"
            " ON CONFLICT(attempt_id,stage) DO UPDATE SET code=excluded.code,message=excluded.message,"
            " observed_at=excluded.observed_at",
            (attempt["attempt_id"], "decision-publication", code, message[:4000], now))
        self.board._append_event(connection, "execution.issue", task_id=attempt["task_id"],
                                 attempt_id=attempt["attempt_id"],
                                 payload={"stage": "decision-publication", "code": code, "message": message[:4000]})

    def _complete(self, connection: sqlite3.Connection, *, row: sqlite3.Row, task: sqlite3.Row, attempt: sqlite3.Row, status: str, result: Any, shutdown_confirmed: bool, error: str | None, now: str, release_stopped: bool = False, released: bool = False) -> dict[str, Any]:
        summary: dict[str, Any] = {"decisionId": row["decision_id"], "status": row["status"]}
        if (row["decision_task_id"] != task["task_id"]
                or row["decision_attempt_id"] != attempt["attempt_id"]
                or row["decision_generation"] != attempt["generation"]
                or row["status"] in TERMINAL_DECISION_STATUSES):
            # The immutable attempt receipt already retains the late result.
            # Never overwrite the current request's output, input or health.
            return {**summary, "late": True}
        output = result if isinstance(result, dict) else None
        dispatch = router_sequence.dispatch(connection, task["task_id"])
        checked = {"answer_valid": False, "abstained": False, "changed": False,
                   "code": output["code"] if released else "router-no-answer",
                   "params": {}, "note": None,
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
                checked.update(changed=True, code="workflow-routing-fenced")
        if (row["input_sha256"] != dispatch["inputSha256"]
                or row["input_json"] != canonical_json(dispatch["document"])):
            checked.update(changed=True, code="router-input-changed")
        if not cancelled and not checked["changed"] and not released:
            checked = self._publish_select(connection, row, output=output or {}, now=now)

        stop = (output or {}).get("stopEvidence")
        native_stopped = (isinstance(stop, dict) and stop.get("shutdownConfirmed") is True
                          and isinstance(stop.get("native"), dict)
                          and stop["native"].get("shutdownConfirmed") is True)
        stopped = shutdown_confirmed is True and attempt["shutdown_confirmed"] == 1 and (native_stopped or release_stopped)
        if status != "ok" or (output or {}).get("status") != "ok":
            if checked["answer_valid"]:
                checked.update(code="router-no-answer", params={})
            checked["answer_valid"] = False
        stage = "preflight" if released else ("publication" if (output or {}).get("status") == "ok" else "runtime")
        outcome = router_failover.classify_outcome(
            stage=stage, code=checked["code"], answer_valid=checked["answer_valid"],
            abstained=checked["abstained"], cancelled=bool(cancelled),
            circumstances_changed=checked["changed"], shutdown_confirmed=bool(stopped))
        from .router_history import record_outcome

        def record(value):
            record_outcome(connection, buddy_id=dispatch["buddyId"], plane="routing", request_id=row["request_id"],
                           task_id=task["task_id"], attempt_id=attempt["attempt_id"], outcome=value,
                           code=checked["code"] if value == "no_answer" else None, phase=stage,
                           facts={"routerIndex": dispatch["routerIndex"], "selectionRetry": bool(json.loads(row["requested_json"]).get("selectionRetry"))}, now=now)

        if outcome == "changed" and checked["code"] == "router-out-of-bounds" and stopped:
            request = json.loads(row["requested_json"])
            if not request.get("selectionRetry"):
                self._retry_selection(connection, row, reason={"code": checked["code"], "params": checked["params"]}, now=now)
                outcome = "rerouted"
            else:
                self._finish(connection, row, status="needs-host", now=now,
                             reason={"code": checked["code"], "params": checked["params"]},
                             output=checked["output"])
        elif outcome == "no-answer":
            record("no_answer")
            advance = router.current_router(connection, frozen=json.loads(row["requested_json"]),
                                            after_index=dispatch["routerIndex"], now=now)
            self._record_preflight_walk(connection, row["request_id"], advance.inspections, now=now, selection_retry=bool(json.loads(row["requested_json"]).get("selectionRetry")))
            if advance.buddy is not None:
                _task_id, problem = self._queue_router_dispatch(connection, row, resolution=advance, now=now)
                if problem is not None:
                    self._finish(connection, row, status="needs-host", now=now,
                                 reason=problem, output={"status": "error", "code": problem["code"]})
            else:
                self._finish(connection, row, status="needs-host", now=now,
                             reason={"code": checked["code"], "params": checked["params"]}, output={**(output or {}), "routerProblem": advance.problem["code"],
                                                          "code": checked["code"]})
        elif outcome in ("answered", "abstained"):
            record("answered")
            self._finish(connection, row, status="completed" if outcome == "answered" else "needs-host", now=now,
                         reason=make_reason("router-abstained") if outcome == "abstained" else None,
                         note=checked["note"], output=checked["output"], buddy_id=checked["buddy_id"],
                         selected=checked["selected"])
        else:
            code = ("router-stop-unconfirmed" if outcome == "stop-unconfirmed" else
                    "decision-cancelled" if outcome == "cancelled" else checked["code"])
            self._finish(connection, row, status="cancelled" if outcome == "cancelled" else "needs-host" if outcome == "changed" else "failed" if shutdown_confirmed is not True else "needs-host",
                         output={**(output or {}), "code": code}, now=now,
                         reason={"code": code, "params": checked["params"] if code == checked["code"] else {}})
        current = self._row(connection, row["decision_id"])
        return {**summary, "status": current["status"], "outcome": outcome, "buddyId": current["buddy_id"],
                "reason": reason_from_json(current["reason_code"], current["reason_params_json"]), "note": current["note"]}


    def _retry_selection(self, connection: sqlite3.Connection, row: sqlite3.Row, *, reason: dict, now: str) -> None:
        """Route the same micro task once with fresh bounds and Router settings.

        Each dispatch keeps its immutable input; the first request snapshot remains
        historical evidence. The current request and new dispatch carry the retry's
        fresh packet, which subsequent failover dispatches also copy.
        """
        request = json.loads(row["requested_json"])
        request["selectionRetry"] = True
        candidates, cooling_buddies = self._scan_candidates(
            connection, request.get("requiredCapabilities", []), constraints=request.get("constraints"),
            coding_only=True, now=now)
        request["routingBasis"] = self._freeze_routing_basis(
            connection, candidates=candidates, required_capabilities=request.get("requiredCapabilities", []),
            constraints=request.get("constraints"), cooling_buddies=cooling_buddies)
        self._append_event(connection, "decision.selection_rerouted", row["decision_id"], {})
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
                no_candidates = self._no_candidates_reason(cooling_buddies) if cooling_buddies else reason
                self._finish(connection, row, status="needs-host", now=now,
                             reason=no_candidates, output={"code": no_candidates["code"]})
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
        document = self._select_input(
            connection, request_id=row["request_id"], task_text=request["task"],
            candidates=candidates, hard_constraints=request.get("constraints") or {})
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
        if resolution.buddy is None:
            problem = resolution.problem
            self._finish(connection, self._row(connection, row["decision_id"]), status="needs-host", now=now,
                         reason=problem,
                         output={"code": problem["code"]})
            return
        self._record_preflight_walk(connection, row["request_id"], resolution.inspections, now=now, selection_retry=True)
        _task_id, problem = self._queue_router_dispatch(connection, self._row(connection, row["decision_id"]), resolution=resolution, now=now)
        if problem is not None:
            self._finish(connection, self._row(connection, row["decision_id"]), status="needs-host", now=now,
                         reason=problem, output={"code": problem["code"]})

    def _tool_evidence_problem(self, row: sqlite3.Row, document: dict, output: dict) -> dict | None:
        """The blackboard wrapper over the unified tool-evidence judgment.

        Bind native evidence to the frozen attempt; the input's routing mode
        determines its permission without another stored policy fact.
        """
        unverified = tool_evidence.TOOL_EVIDENCE_UNVERIFIED
        evidence = output.get("toolEvidence") if isinstance(output, dict) else None
        if evidence is None:
            return {"code": unverified}
        reported = output.get("nativeIdentity") if isinstance(output, dict) else None
        reported = reported if isinstance(reported, list) else ([reported] if isinstance(reported, dict) else None)
        if not reported:
            return {"code": unverified}
        expected_binding = {
            "harness": (document.get("buddy") or {}).get("harness"),
            "taskId": row["decision_task_id"],
            "attemptId": row["decision_attempt_id"],
            "generation": row["decision_generation"],
        }
        if not isinstance(evidence, dict) or evidence.get("binding") != expected_binding:
            return {"code": unverified}
        roots = evidence.get("nativeIdentity")
        if not isinstance(roots, list) or any(identity not in roots for identity in reported):
            return {"code": unverified}
        permission = {"fast": "none", "review": "read"}[document["routingMode"]]
        judged = tool_evidence.judge_tool_evidence(evidence, permission)
        if judged is None:
            return None
        return {"code": judged}

    def _publish_select(self, connection: sqlite3.Connection, row: sqlite3.Row, *, output: dict, now: str) -> dict:
        """Check program facts without settling the request or judging the choice."""
        document = json.loads(row["input_json"]) if row["input_json"] else {}
        request = json.loads(row["requested_json"])
        checked = {"answer_valid": False, "abstained": False, "changed": False,
                   "code": None, "params": {}, "note": None, "output": output, "buddy_id": None,
                   "selected": None, "evidence": []}

        def refuse(code, *, changed=False, **params):
            return {**checked, "code": code, "params": params, "changed": changed,
                    "output": {**output, "code": code}}

        # Check review input even when the helper failed: an explicit changed
        # copy/manifest is a circumstance boundary, never a Router no-answer.
        verification = output.get("inputVerification")
        manifest = document.get("executionWorkspace") or {}
        review = document.get("routingMode") == "review"
        if review and isinstance(verification, dict) and (
                verification.get("unchanged") is False
                or ("manifestSha256" in verification and verification["manifestSha256"] != manifest.get("manifestSha256"))):
            return refuse("router-input-changed", changed=True)
        stop = output.get("stopEvidence")
        if (not isinstance(stop, dict) or stop.get("shutdownConfirmed") is not True
                or not isinstance(stop.get("native"), dict) or stop["native"].get("shutdownConfirmed") is not True):
            return refuse("router-stop-unconfirmed")
        if output.get("status") != "ok":
            code = output.get("code")
            code = code if (isinstance(code, str) and code.strip() and len(code) <= 128
                            and not any(ord(c) < 32 or ord(c) == 127 for c in code)) else "router-no-answer"
            return refuse(code, **({"bound": "not-a-candidate"} if code == "router-out-of-bounds" else {}))
        if review and (not isinstance(verification, dict) or verification.get("unchanged") is not True
                       or not isinstance(verification.get("snapshotSha256"), str) or not verification["snapshotSha256"]
                       or "manifestSha256" not in verification):
            # Missing/malformed proof invalidates an answer; it does not prove
            # that the input changed. Failed calls need no successful answer proof.
            return refuse("router-input-changed")
        if output.get("operation") != "select":
            return refuse("answer-shape")
        usage = output.get("usage")
        budget = document.get("budget") or {}
        if (not isinstance(usage, dict) or type(usage.get("elapsedMs")) is not int
                or usage["elapsedMs"] < 0 or type(usage.get("toolCalls")) is not int or usage["toolCalls"] < 0):
            return refuse("router-tool-evidence-unverified")
        if (usage["elapsedMs"] > budget["timeoutSeconds"] * 1000
                or (document.get("routingMode") == "review" and usage["toolCalls"] > budget["toolCalls"])):
            return refuse("router-budget-exhausted")
        problem = self._tool_evidence_problem(row, document, output)
        if problem is None and usage["toolCalls"] != output["toolEvidence"]["toolCalls"]:
            problem = {"code": "router-tool-evidence-unverified"}
        if problem is not None:
            return refuse(problem["code"])
        buddies = document.get("buddies") or []
        try:
            decision = router.validate_answer(output.get("decision"), [item["buddyId"] for item in buddies], document.get("routingMode", "review"))
        except BoardError as failure:
            return refuse(failure.code, **failure.details)
        note, buddy_id = decision["note"], decision["buddyId"]
        if buddy_id is None:
            return {**checked, "answer_valid": True, "abstained": True, "note": note,
                    "output": {**output, "decision": decision, "policyCheck": None}, "evidence": decision["evidence"]}
        selected = next(dict(item) for item in buddies if item["buddyId"] == buddy_id)
        candidate = connection.execute("SELECT * FROM buddies WHERE buddy_id=?", (buddy_id,)).fetchone()
        # A changed cached harness health does not invalidate an answer already run.
        bound = candidate_bound(connection, candidate, request, now, frozen=selected, require_health=False)
        if bound is not None:
            return refuse("router-out-of-bounds", changed=True, bound=bound)
        expected_facts = document["policyFacts"]
        policy_check = selection_policy.expected_policy_check(expected_facts, buddy_id)
        return {**checked, "answer_valid": True, "note": note, "buddy_id": buddy_id,
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

    def _finish(self, connection: sqlite3.Connection, row: sqlite3.Row, *, status: str, now: str, reason: dict | None = None, note: str | None = None, buddy_id: str | None = None, output: dict | None = None, selected: dict | None = None) -> None:
        """One atomic terminal transition for a decision and its audit material."""
        current = self._row(connection, row["decision_id"])
        if (current is None or (current["decision_task_id"] is not None and current["status"] in TERMINAL_DECISION_STATUSES)
                or current["decision_task_id"] != row["decision_task_id"]
                or current["decision_attempt_id"] != row["decision_attempt_id"]
                or current["decision_generation"] != row["decision_generation"]):
            return
        connection.execute(
            "UPDATE evaluation_decisions SET status=?, reason_code=?, reason_params_json=?, note=?, buddy_id=?"
            " WHERE decision_id=?",
            (
                status,
                reason["code"] if reason else None,
                canonical_json(reason["params"]) if reason else None,
                note,
                buddy_id,
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
        self._append_event(
            connection,
            event,
            row["decision_id"],
            {
                "status": status,
                "buddyId": buddy_id,
            },
        )
        self.board.workflow.routing_settled(connection, decision_id=row["decision_id"], now=now)

    def _cancel_queued_task(self, connection: sqlite3.Connection, task: sqlite3.Row, now: str) -> None:
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
            payload={"decisionId": json.loads(task["spec_json"])["decision"]["decisionId"], "actor": {"role": "service", "id": "service"}, "phase": "queued"},
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
            reason=make_reason("single-candidate"),
            buddy_id=buddy_id,
            selected=selected,
            output={
                "programSelection": {
                    "code": "single-candidate",
                    "buddyId": buddy_id,
                    "candidateCount": 1,
                    "preferences": preferences,
                    "policyFacts": facts,
                },
                "policyCheck": policy_check,
            },
        )

    # -- views ---------------------------------------------------------------
    def health_summary(self) -> dict:
        """Read terminal decision facts and each Router's recorded outcomes."""
        from .router_history import buddy_timeline, router_state
        limit = 20
        source = (
            " FROM evaluation_decisions d JOIN decision_requests r ON r.decision_id=d.decision_id"
            " WHERE d.status IN ('completed','needs-host','failed','cancelled')"
            " AND EXISTS(SELECT 1 FROM router_dispatches dispatched WHERE dispatched.decision_id=d.decision_id)"
        )
        router_success = (
            "d.status='completed' AND EXISTS(SELECT 1 FROM router_outcomes o"
            " WHERE o.plane='routing' AND o.request_id=r.request_id AND o.outcome='answered'"
            " AND o.task_id=r.task_id AND o.attempt_id=r.attempt_id)"
        )
        with self.db.read() as connection:
            now = self._now()
            rows = connection.execute(
                "SELECT d.status,d.reason_code,r.updated_at AS created_at,r.decision_id,r.task_id,"
                " (" + router_success + ") AS router_success,"
                " (r.attempt_id IS NOT NULL) AS has_attempt,"
                " (SELECT w.run_id FROM workflow_routes w WHERE w.decision_id=r.decision_id LIMIT 1) AS micro_task_id"
                + source + " ORDER BY r.updated_at DESC,r.rowid DESC LIMIT ?", (limit,),
            ).fetchall()
            success = connection.execute(
                "SELECT r.updated_at AS created_at,r.decision_id" + source
                + " AND " + router_success + " ORDER BY r.updated_at DESC,r.rowid DESC LIMIT 1",
            ).fetchone()
            resolution = router.current_router(connection, now=now)
            failed = []
            streak = 0
            still_failing = True
            abstentions = cancellations = 0
            special_counts = {"router-budget-exhausted": 0, "router-out-of-bounds": 0, "router-input-changed": 0}
            for row in rows:
                code = row["reason_code"]
                abstained = code == "router-abstained"
                if row["status"] in ("failed", "needs-host") and code in special_counts:
                    special_counts[code] += 1
                is_failure = code not in ("router-out-of-bounds", "router-input-changed") and (
                    row["status"] == "failed" or (row["status"] == "needs-host" and not abstained))
                if still_failing and is_failure:
                    streak += 1
                elif row["router_success"]:
                    still_failing = False
                abstentions += int(abstained)
                cancellations += int(row["status"] == "cancelled")
                if is_failure:
                    failed.append({"decisionId": row["decision_id"], "runId": row["micro_task_id"] or row["task_id"],
                                   "at": row["created_at"], "code": code})
            facts = resolution.facts
            interval = facts.get("routerRetryIntervalSeconds")
            mode = facts.get("routingMode")
            ids = facts.get("routerBuddyIds") or []
            identities = facts.get("routerIdentities") or []
            inspected = {entry["index"]: entry for entry in resolution.inspections}
            budget_codes = frozenset({"router-budget-exhausted"})
            routers = []
            for index, buddy_id in enumerate(ids):
                # A nonempty list always carries validated settings, so the
                # interval is an integer; an absent one has no entries to project.
                state = router_state(connection, buddy_id=buddy_id,
                                     interval_seconds=interval, now=now)
                window = buddy_timeline(connection, buddy_id=buddy_id)[-limit:]
                inspection = inspected.get(index)
                if inspection is not None:
                    eligible, code = inspection["eligible"], inspection["code"]
                elif state["inSkipWindow"]:
                    eligible, code = False, "router-skip-window"
                else:
                    buddy, code, _facts = router.buddy_problem(connection, buddy_id, mode, now=now)
                    eligible = buddy is not None
                last_error = next((entry for entry in reversed(window) if entry["outcome"] == "no_answer"), None)
                routers.append({
                    "buddyId": buddy_id, "index": index,
                    "identity": identities[index],
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
        return {"available":resolution.buddy_id is not None,
                "reason":resolution.problem,
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

        This read calls no model and persists nothing.
        The cursor is ``decision_requests.rowid``, the immutable insertion
        order, so a newer decision never pushes an older one out of reach and a page
        never shifts, repeats or skips. ``total`` is the complete matching count,
        independent of the cursor; decisions that never acquired a run and older
        pages are returned exactly like any other.
        """
        schemas.reject_unknown(params, LIST_FIELDS, "selection.list")
        limit = schemas.optional_int(params, "limit", DEFAULT_LIST_LIMIT, 1, MAX_LIST_LIMIT)
        before = schemas.optional_positive_int(params, "before")
        with self.board.db.read() as connection:
            total = int(
                connection.execute(
                    "SELECT COUNT(*) AS count FROM decision_requests",
                ).fetchone()["count"]
            )
            query = f"SELECT r.rowid AS request_rowid, {_ROW_COLUMNS}{_ROW_FROM}"
            values: list[Any] = []
            if before is not None:
                query += " WHERE r.rowid<?"
                values.append(before)
            query += " ORDER BY r.rowid DESC LIMIT ?"
            values.append(limit)
            rows = connection.execute(query, values).fetchall()
            decisions = [self._view(connection, row, include_audit=False) for row in rows]
            next_cursor = None
            if len(rows) == limit:
                last_rowid = int(rows[-1]["request_rowid"])
                more = connection.execute(
                    "SELECT 1 FROM decision_requests r WHERE r.rowid<? LIMIT 1",
                    (last_rowid,),
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
                 if row["decision_task_id"] else None)
        selected = json.loads(row["selected_json"]) if row["selected_json"] else None
        answer = (output or {}).get("decision")
        evidence = (answer["evidence"] if row["status"] in ("completed", "needs-host")
                    and isinstance(output, dict) and output.get("status") == "ok" and "code" not in output
                    and isinstance(answer, dict) and isinstance(answer.get("evidence"), list) else [])
        view: dict[str, Any] = {
            "decisionId": row["decision_id"],
            "requestId": row["request_id"],
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
            "reason": reason_from_json(row["reason_code"], row["reason_params_json"]),
            "note": row["note"],
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
