"""Bounded, board-local memoization of macro summaries over a SQLite snapshot.

The list still discovers membership, matching and ordering on every changed
read. Only _summary is reused. Markers contain derived scalar facts, receipt
presence/stop facts and bounded presentation fields, never execution results,
turn inputs, request bodies or manifests. Events are not a substitute for SQL
facts: correcting an existing description event also invalidates its group.
"""
from __future__ import annotations

import copy
import json
import threading
import time
from collections import OrderedDict

from . import micro_task

MAX_ENTRIES = 256
MAX_BYTES = 4 * 1024 * 1024
MAX_AGE_SECONDS = 3600
TEXT_BOUND = 4096
_CREATION_LOCK = threading.Lock()

# Reuse the authoritative lineage, source attribution, identity and stop-proof
# rules. Drop t.* before materializing: spec/input/result JSON does not belong
# in a list's change marker. Query-only columns remain inside SQLite, evaluated
# before the bounded scalar projection is materialized.
THIN_HISTORY_SQL = micro_task.TASK_HISTORY_SQL.replace(
    "t.*,",
    "t.rowid AS task_rowid, t.task_id, t.adapter, t.state, t.selected_attempt_id, t.active_attempt_id,"
    " t.created_at, t.accepted_at, t.revision, t.updated_at,",
)
MEMBER_COLUMNS = (
    "task_id", "task_rowid", "adapter", "state", "selected_attempt_id", "active_attempt_id",
    "created_at", "accepted_at", "revision", "updated_at", "micro_task_run_id",
    "run_state", "child_parent_run_id", "delegated_parent_run_id", "decision_id",
    "route_run_id", "root_run_id", "root_host_id", "root_owner_generation",
    "source_host_id", "current_host_id", "project_path", "project_id",
    "project_repository_path", "review_ready",
)


def for_store(store):
    """One cache per store, without a process-global map retaining old boards."""
    with _CREATION_LOCK:
        cache = getattr(store, "_macro_task_summary_cache", None)
        if cache is None:
            cache = store._macro_task_summary_cache = SummaryCache()
        return cache


class SummaryCache:
    """Serialize snapshot generation and bound both markers and owned values.

    Acquire lock before opening the read transaction. A writer may commit during
    compute, but every marker and summary still comes from that same WAL snapshot;
    a subsequent reader cannot publish an older snapshot over a newer one. The
    console's existing before/after data_version guard retries such a generation.
    Returned values are copies: callers cannot poison another read's summary.
    """

    def __init__(self, *, max_entries=MAX_ENTRIES, max_bytes=MAX_BYTES, clock=time.monotonic):
        self.lock = threading.RLock()
        self.max_entries, self.max_bytes, self.clock = max_entries, max_bytes, clock
        self.entries = OrderedDict()
        self.bytes = 0

    def get(self, key, marker, compute, *, reusable=True):
        now = self.clock()
        with self.lock:
            previous = self.entries.pop(key, None)
            if previous is not None:
                self.bytes -= previous[3]
                if reusable and previous[0] == marker and now - previous[2] < MAX_AGE_SECONDS:
                    self.entries[key] = previous
                    self.bytes += previous[3]
                    return copy.deepcopy(previous[1])
            value = compute()
            # Byte accounting covers the marker as well as the cached summary.
            size = len(json.dumps([key, marker, value], ensure_ascii=False).encode())
            if reusable and size <= self.max_bytes and self.max_entries > 0:
                self.entries[key] = (marker, copy.deepcopy(value), now, size)
                self.bytes += size
                while len(self.entries) > self.max_entries or self.bytes > self.max_bytes:
                    self.bytes -= self.entries.popitem(last=False)[1][3]
            return value


def _bounded(expression, *, kind=None):
    # JSON scalars and objects can have the same SQLite text representation.
    # Preserve their type too. A long field declines reuse instead of trusting a prefix.
    kind = kind or f"typeof(({expression}))"
    return f"substr(({expression}),1,{TEXT_BOUND}), length(({expression})), {kind}"


def dependencies(connection, members):
    """Small facts for page groups, keyed through their current owned members.

    Derived review_ready includes all owned_executions/unconfirmed_stops, not
    just the selected attempt. Receipt markers below additionally over-invalidate
    on any related stop change. Run/request/turn identities are deliberately
    conservative; their large documents never cross this query boundary.
    """
    groups = {group: [tuple(row[column] for column in (*MEMBER_COLUMNS, "matching"))
                      for row in rows] for group, rows in members.items()}
    owners = {}
    for group, rows in members.items():
        for row in rows:
            owners.setdefault(row["task_id"], set()).add(group)
    identifiers = sorted(owners)
    for start in range(0, len(identifiers), 400):
        batch = identifiers[start:start + 400]
        placeholders = ",".join("?" for _ in batch)
        statements = (
            ("attempt", "SELECT task_id,attempt_id,generation,execution_state,shutdown_confirmed,"
             " result_json IS NOT NULL FROM attempts WHERE task_id IN ({}) ORDER BY task_id,generation"),
            ("run", "SELECT run_id,macro_task_id,state,host_id,owner_generation,active_request_id,current_turn_id,"
             " current_attempt_id,final_artifact_id,final_attempt_id,revision,created_at,activity_seq,activity_at"
             " FROM workflow_runs WHERE run_id IN ({}) ORDER BY run_id"),
            ("turn", "SELECT run_id,turn_id,turn_index,state,attempt_id,generation,disposition"
             " FROM workflow_turns WHERE run_id IN ({}) ORDER BY run_id,turn_index"),
            ("conclusion", "SELECT run_id,conclusion_id,execution_status"
             " FROM workflow_host_conclusions WHERE run_id IN ({}) ORDER BY run_id"),
            ("request", "SELECT run_id,request_id,turn_id,attempt_id,child_task_id,kind,state,expected_revision,decided_at"
             " FROM workflow_requests WHERE run_id IN ({}) ORDER BY run_id,request_id"),
            ("child", "SELECT child_task_id,parent_run_id,request_id,state,role,integrator"
             " FROM workflow_children WHERE child_task_id IN ({}) ORDER BY child_task_id"),
            ("route", "SELECT route.run_id,route.decision_id,decision.task_id,route.state,route.owner_generation"
             " FROM workflow_routes route JOIN decision_requests decision USING(decision_id)"
             " WHERE route.run_id IN ({}) ORDER BY route.run_id,route.decision_id"),
        )
        for kind, statement in statements:
            for row in connection.execute(statement.format(placeholders), batch):
                for group in owners[row[0]]:
                    groups[group].append((kind, *tuple(row)))
    reusable = dict.fromkeys(groups, True)
    for group in groups:
        if group.startswith("macro-"):
            row = connection.execute(
                "SELECT macro_task_id,title,project_id,project_path,source_host_id,created_at,activity_seq,activity_at"
                " FROM macro_tasks WHERE macro_task_id=?", (group,),
            ).fetchone()
            groups[group].append(("macro-task", *tuple(row)))
            # The first run follows the same created_at/rowid ordering as _summary.
            # The original event's description is part of the summary marker.
            event = connection.execute(
                "SELECT e.seq," + _bounded("json_extract(e.payload_json,'$.macroTaskId')",
                                           kind="json_type(e.payload_json,'$.macroTaskId')") + ","
                + _bounded("json_extract(e.payload_json,'$.description')",
                           kind="json_type(e.payload_json,'$.description')") +
                " FROM events e WHERE e.kind='workflow.macro_task_created' AND e.task_id=("
                " SELECT run_id FROM workflow_runs WHERE macro_task_id=? ORDER BY created_at,rowid LIMIT 1)"
                " ORDER BY e.seq LIMIT 1", (group,),
            ).fetchone()
            if event is not None:
                groups[group].append(("description", *tuple(event)))
                reusable[group] = all(
                    length is None or length <= TEXT_BOUND for length in (event[2], event[5])
                )

        else:
            row = connection.execute(
                "SELECT " + _bounded("r.title") + "," + _bounded("json_extract(t.spec_json,'$.task')",
                    kind="json_type(t.spec_json,'$.task')") + ","
                + _bounded("(SELECT CASE WHEN json_type(t.outcome_json,'$.summary')='text'"
                           " THEN json_extract(t.outcome_json,'$.summary') END FROM workflow_turns t"
                           " WHERE t.run_id=r.run_id AND t.state='returned' AND t.outcome_json IS NOT NULL"
                           " ORDER BY t.turn_index DESC LIMIT 1)") +
                " FROM workflow_runs r JOIN tasks t ON t.task_id=r.run_id WHERE r.run_id=?", (group[4:],),
            ).fetchone()
            groups[group].append(("presentation", *tuple(row)))
            reusable[group] = all(length is None or length <= TEXT_BOUND for length in (row[1], row[4], row[7]))
    return groups, reusable
