"""Human macro task metadata and bounded read projections, never execution input.

A macro task groups micro tasks for browsing and archival. It owns a
title, optional description and original source attribution: it schedules no work,
grants no control and is never part of an execution spec, Worker turn input or
selector packet. The timeline is derived on every read from the recorded task,
attempt, turn, request, routing and event rows; the only stored projection is the
latest-activity sequence that orders the list.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import json
import re
import uuid
from typing import Any

from . import fixes, micro_task
from ..routing import router
from ...protocol import schemas
from ...errors import BoardError

LIST_DEFAULT_LIMIT = 50
LIST_MAX_LIMIT = 100
TIMELINE_DEFAULT_LIMIT = 100
TIMELINE_MAX_LIMIT = 200
MAX_SPANS = 800
MAX_MARKERS = 800
SUMMARY_LIMIT = 300
#: Display fallback when a micro task recorded no intent title or task line.
UNTITLED = "未命名微任务"
TITLE_LIMIT = 200
CURSOR_VERSION = 1

MACRO_TASK_PATTERN = re.compile(r"^macro-[A-Za-z0-9-]{1,120}$")
STANDALONE_PATTERN = re.compile(r"^run:([A-Za-z0-9._:-]{1,128})$")
_CURSOR_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,512}$")

LIST_PARAMETERS = frozenset({"limit", "before", "projectId", "hostId", "query", "filter"})
TIMELINE_PARAMETERS = frozenset({"macroTaskId", "limit", "query", "filter"})

#: Every task of a micro task tree with its group key. A helper inherits
#: its root's macro task, a routing decision belongs to the run it routed, and a root
#: without a macro task forms its own ``run:<rootRunId>`` group. Plain execution
#: records (no micro-task root) belong to no group.
MEMBERS_SQL = f"""
SELECT h.*,
       COALESCE(root_group.macro_task_id, 'run:' || h.root_run_id) AS group_id
  FROM ({micro_task.TASK_HISTORY_SQL}) AS h
  JOIN workflow_runs root_group ON root_group.run_id = h.root_run_id
"""

#: Disjoint micro task states, in the display priority 待决定 > 进行中 > 待验收 > 已结束.
_HOST_SQL = micro_task.FILTER_SQL["host"]
_ACTIVE_SQL = f"({micro_task.FILTER_SQL['active']} AND NOT {_HOST_SQL})"
_REVIEW_SQL = micro_task.FILTER_SQL["review"]

#: Event kinds shown as Host markers, with the display kind each one becomes.
HOST_MARKERS = {
    "task.submitted": "dispatch",
    "workflow.request_approved": "decide",
    "workflow.request_declined": "decide",
    "workflow.continued": "continue",
    "workflow.review_rejected": "reject",
    "workflow.integration_recorded": "integrate",
    "workflow.acknowledged": "accept",
    "workflow.host_concluded": "conclude",
    "workflow.cancelled": "cancel",
    "workflow.takeover": "takeover",
}
#: Display labels of the Host marker kinds (the verdict splits accept/reject).
MARKER_LABELS = {
    "dispatch": "派发", "decide": "决定", "continue": "续接", "integrate": "整合",
    "accept": "验收", "reject": "验收问题", "conclude": "结论", "cancel": "取消", "takeover": "接管",
}


# -- admission and activity ----------------------------------------------------


#: The console top bar's pending count: distinct micro-task-tree roots with an
#: awaiting-host run, except trees owned by a Lead. It reuses the same lineage
#: walk as the history relation (``micro_task.RELATION_SQL``) and stays a bounded
#: aggregate over the whole board instead of decorating the latest task page.
PENDING_ROOTS_SQL = """
WITH RECURSIVE lineage(task_id, root_run_id) AS (
    SELECT child_task_id, child_task_id FROM workflow_children
    UNION ALL
    SELECT lineage.task_id, parent.parent_run_id
      FROM lineage JOIN workflow_children parent ON parent.child_task_id = lineage.root_run_id
),
roots AS (
    SELECT lineage.task_id AS task_id, lineage.root_run_id AS root_run_id
      FROM lineage
     WHERE NOT EXISTS (
         SELECT 1 FROM workflow_children parent WHERE parent.child_task_id = lineage.root_run_id
     )
)
SELECT COUNT(DISTINCT COALESCE(roots.root_run_id, tasks.task_id)) AS pending
  FROM tasks
  JOIN workflow_runs awaiting ON awaiting.run_id = tasks.task_id AND awaiting.state = 'awaiting-host'
  LEFT JOIN roots ON roots.task_id = tasks.task_id
 WHERE NOT EXISTS (
       SELECT 1 FROM workflow_lead_tasks owned
        WHERE owned.owned_run_id = COALESCE(roots.root_run_id, tasks.task_id)
 )
"""


def pending_root_count(store) -> int:
    """Micro task roots with at least one run awaiting its Host, board-wide.

    The console's old client-side count deduplicated the latest snapshot page
    by ``lineage.rootRunId || runId``; this is the same semantics computed
    over every run, so it stays correct past any page boundary.
    """
    with store.db.read() as connection:
        return int(connection.execute(PENDING_ROOTS_SQL).fetchone()["pending"])


def attach_macro_task(connection, presentation: dict, *, spec: dict, host_id: str, manifest: dict, now: str) -> str | None:
    """Run within the task's admission transaction; failed admission creates nothing."""
    project_id = manifest.get("repositoryId") or spec["cwd"]
    identifier = presentation.get("macroTaskId")
    if presentation.get("macroTaskOf") is not None:
        source = connection.execute("SELECT macro_task_id FROM workflow_runs WHERE run_id=?", (presentation["macroTaskOf"],)).fetchone()
        if source is None:
            raise BoardError("NOT_FOUND", "macroTaskOf names an unknown run")
        if source["macro_task_id"] is None:
            raise BoardError("MACRO_TASK_REQUIRED", "macroTaskOf must name a run belonging to a work macro task")
        identifier = source["macro_task_id"]
    if identifier is not None:
        row = connection.execute("SELECT * FROM macro_tasks WHERE macro_task_id=?", (identifier,)).fetchone()
        if row is None:
            raise BoardError("NOT_FOUND", "Unknown macroTaskId")
        if row["project_id"] != project_id or row["source_host_id"] != host_id:
            raise BoardError("CONFLICT", "The macro task belongs to another source project or submitting Host")
        return identifier
    if "macroTask" not in presentation:
        return None
    identifier = f"macro-{uuid.uuid4()}"
    connection.execute(
        "INSERT INTO macro_tasks(macro_task_id,title,project_id,project_path,source_host_id,created_at,activity_at)"
        " VALUES(?,?,?,?,?,?,?)", (identifier, presentation["macroTask"]["title"], project_id, spec["cwd"], host_id, now, now),
    )
    return identifier


def record_activity(connection, task_id: str | None, seq: int, at: str) -> None:
    """Maintain only the indexed read projection, in the original event transaction.

    Existing child/route links determine propagation. The UNION also terminates on
    duplicate lineage rows without turning a read index into a new lifecycle.
    """
    if task_id is None:
        return
    connection.execute(
        "WITH RECURSIVE owners(run_id) AS ("
        " SELECT run_id FROM workflow_runs WHERE run_id=?"
        " UNION SELECT r.run_id FROM workflow_routes r JOIN decision_requests d USING(decision_id) WHERE d.task_id=?"
        " UNION SELECT c.parent_run_id FROM workflow_children c JOIN owners o ON c.child_task_id=o.run_id)"
        " UPDATE workflow_runs SET activity_seq=?,activity_at=? WHERE run_id IN (SELECT run_id FROM owners)",
        (task_id, task_id, seq, at),
    )
    connection.execute(
        "UPDATE macro_tasks SET activity_seq=?,activity_at=? WHERE macro_task_id IN"
        " (SELECT macro_task_id FROM workflow_runs WHERE activity_seq=? AND macro_task_id IS NOT NULL)",
        (seq, at, seq),
    )


# -- shared read helpers -------------------------------------------------------


def _filters(params: dict) -> tuple[dict, list[str], list[Any]]:
    """Validated member filters and their SQL predicates over ``MEMBERS_SQL`` rows."""
    query = schemas.optional_string(params, "query", max_length=micro_task.QUERY_MAX_LENGTH)
    project_id = schemas.optional_string(params, "projectId", max_length=micro_task.PROJECT_ID_MAX_LENGTH)
    host_id = schemas.optional_string(params, "hostId", max_length=micro_task.HOST_ID_MAX_LENGTH)
    filter_name = schemas.optional_string(params, "filter", max_length=16) or "all"
    if filter_name not in micro_task.HISTORY_FILTERS:
        raise BoardError("INVALID_ARGUMENT", "filter must be one of: all, active, host, review")
    clauses, values = micro_task.history_where(
        project_id=project_id, host_id=host_id, query=query, filter_name=filter_name
    )
    applied = {key: value for key, value in (("query", query), ("projectId", project_id), ("hostId", host_id))
               if value}
    if filter_name != "all":
        applied["filter"] = filter_name
    return applied, clauses, values


def _fingerprint(applied: dict) -> str:
    return hashlib.sha256(json.dumps(applied, sort_keys=True, separators=(",", ":")).encode()).hexdigest()[:16]


def _encode_cursor(seq: int, group_id: str, applied: dict, head: int) -> str:
    payload = json.dumps({"v": CURSOR_VERSION, "s": seq, "g": group_id, "f": _fingerprint(applied), "h": head},
                         separators=(",", ":"), sort_keys=True)
    return base64.urlsafe_b64encode(payload.encode()).decode().rstrip("=")


def _decode_cursor(value: str, applied: dict) -> tuple[int, str, int]:
    invalid = BoardError("INVALID_ARGUMENT", "before must be the nextCursor of macro_task_list; read the first page again")
    if not isinstance(value, str) or not _CURSOR_PATTERN.match(value):
        raise invalid
    try:
        payload = json.loads(base64.b64decode(value + "=" * (-len(value) % 4), altchars=b"-_", validate=True))
    except (binascii.Error, ValueError, UnicodeDecodeError):
        raise invalid from None
    if not isinstance(payload, dict) or set(payload) != {"v", "s", "g", "f", "h"} or payload["v"] != CURSOR_VERSION:
        raise invalid
    seq, group_id, head = payload["s"], payload["g"], payload["h"]
    if any(isinstance(item, bool) or not isinstance(item, int) or item < 0 for item in (seq, head)):
        raise invalid
    if not isinstance(group_id, str) or not (MACRO_TASK_PATTERN.match(group_id) or STANDALONE_PATTERN.match(group_id)):
        raise invalid
    if payload["f"] != _fingerprint(applied):
        raise BoardError(
            "INVALID_ARGUMENT", "This cursor was issued for different filters; read the first page again",
            field="before",
        )
    return seq, group_id, head


def _head(connection) -> int:
    row = connection.execute("SELECT COALESCE(MAX(seq), 0) AS head FROM events").fetchone()
    return int(row["head"])


def _first_line(text: Any) -> str | None:
    if not isinstance(text, str) or not text.strip():
        return None
    return " ".join(text.strip().splitlines()[0].split())[:TITLE_LIMIT]


def _run_presentation(connection, run_id: str) -> tuple[str | None, str, str | None]:
    """Intent first; a recorded result summary is a separate secondary field."""
    row = connection.execute(
        "SELECT r.title,"
        " (SELECT CASE WHEN json_type(t.outcome_json,'$.summary')='text'"
        "  THEN json_extract(t.outcome_json,'$.summary') END FROM workflow_turns t"
        "  WHERE t.run_id=r.run_id AND t.state='returned' AND t.outcome_json IS NOT NULL"
        "  ORDER BY t.turn_index DESC LIMIT 1) AS summary,"
        " json_extract(r.micro_task_json,'$.task') AS task"
        " FROM workflow_runs r WHERE r.run_id=?",
        (run_id,),
    ).fetchone()
    if row is None:
        return None, "none", None
    summary = _first_line(row["summary"])
    if isinstance(row["title"], str) and row["title"].strip():
        return row["title"].strip()[:TITLE_LIMIT], "title", summary
    task = _first_line(row["task"])
    if task:
        return task, "task", summary
    return None, "none", summary


def _state(row) -> str:
    """One member's display state: host, active, review or ended."""
    run_state, state = row["run_state"], row["state"]
    if run_state == "awaiting-host" and row["root_lead_run_id"] is None:
        return "host"
    if state in ("queued", "running", "cancelling", "reconciliation-needed") or run_state in (
        "executing", "waiting-helpers", "waiting-owned", "awaiting-host"
    ):
        return "active"
    if (
        row["accepted_at"] is None
        and state in ("completed", "failed", "cancelled")
        and row["decision_id"] is None
        and row["adapter"] != "decision"
        and (row["micro_task_run_id"] is None or run_state == "delivered")
        and row["review_ready"] == 1
    ):
        return "review"
    return "ended"


# -- list ----------------------------------------------------------------------


def macro_task_list(store, params: dict) -> dict:
    """Bounded work-macro-task summaries ordered by latest recorded activity."""
    schemas.reject_unknown(params, LIST_PARAMETERS, "macro_task.list")
    limit = schemas.optional_int(params, "limit", LIST_DEFAULT_LIMIT, 1, LIST_MAX_LIMIT)
    applied, clauses, values = _filters(params)
    before_value = schemas.optional_string(params, "before", max_length=512)
    before = _decode_cursor(before_value, applied) if before_value is not None else None
    match = " AND ".join(["micro_task_run_id IS NOT NULL", *clauses])
    from . import macro_task_summary_cache as memo

    # The materialized relation carries only scalars consumed by the summary and
    # the SQL matching flag. In particular no t.* / result / manifest / outcome
    # body is copied merely to notice a change. One relation serves matching,
    # total, order, page membership and the per-group dependency marker.
    narrow = ",".join(f"h.{column}" for column in memo.MEMBER_COLUMNS)
    page_values = list(values)
    page_where = ""
    if before is not None:
        seq, group_id, _issued_head = before
        page_where = "WHERE (activity_seq < ? OR (activity_seq = ? AND group_id < ?))"
        page_values.extend([seq, seq, group_id])
    groups_sql = f"""
WITH members AS MATERIALIZED (
    SELECT {narrow}, COALESCE(root.macro_task_id, 'run:' || h.root_run_id) AS group_id,
           h.matching
      FROM (SELECT h.*, CASE WHEN {match} THEN 1 ELSE 0 END AS matching
              FROM ({memo.THIN_HISTORY_SQL}) h) h
      JOIN workflow_runs root ON root.run_id=h.root_run_id
),
matched AS (SELECT group_id FROM members WHERE matching=1 GROUP BY group_id),
groups AS (
    SELECT matched.group_id, COALESCE(o.activity_seq, root.activity_seq) AS activity_seq
      FROM matched
      LEFT JOIN macro_tasks o ON o.macro_task_id=matched.group_id
      LEFT JOIN workflow_runs root ON o.macro_task_id IS NULL
        AND matched.group_id LIKE 'run:%' AND root.run_id=substr(matched.group_id,5)
),
page AS (SELECT * FROM groups {page_where}
          ORDER BY activity_seq DESC, group_id DESC LIMIT ?),
stats AS (SELECT COUNT(*) AS total, COALESCE(MAX(activity_seq),0) AS latest FROM groups)
SELECT stats.total,stats.latest,page.group_id AS page_group_id,page.activity_seq,members.*
  FROM stats LEFT JOIN page ON 1=1 LEFT JOIN members ON members.group_id=page.group_id
 ORDER BY page.activity_seq DESC,page.group_id DESC,members.task_id
"""
    cache = memo.for_store(store)
    # Lock precedes the read transaction so concurrent list calls serialize
    # snapshots, not just insertion. WAL permits a concurrent writer; all facts
    # below (including a cache miss's _summary) remain from this one snapshot.
    with cache.lock, store.db.read() as connection:
        head = _head(connection)
        rows = connection.execute(groups_sql, [*page_values, limit + 1]).fetchall()
        total = int(rows[0]["total"])
        changed = before is not None and int(rows[0]["latest"]) > before[2]
        groups = {}
        for row in rows:
            if row["page_group_id"] is not None:
                groups.setdefault(row["page_group_id"], []).append(row)
        page = list(groups)[:limit]
        markers, reusable = memo.dependencies(connection, {group: groups[group] for group in page})
        filters = json.dumps(applied, sort_keys=True, ensure_ascii=False)
        information = store.db.path.stat()
        board = (information.st_dev, information.st_ino)
        summaries = [cache.get(
            (board, group, filters), markers[group],
            lambda group=group: _summary(connection, group, clauses, values),
            reusable=reusable[group],
        ) for group in page]
        issued_head = before[2] if before is not None else head
        next_cursor = (
            _encode_cursor(int(groups[page[-1]][0]["activity_seq"] or 0), page[-1], applied, issued_head)
            if len(groups) > limit else None
        )
    return {"macroTasks": summaries, "total": total, "nextCursor": next_cursor, "cursor": head, "changed": changed}


def _members(connection, group_id: str) -> list:
    return connection.execute(f"SELECT * FROM ({MEMBERS_SQL}) WHERE group_id = ?", (group_id,)).fetchall()


def _summary(connection, group_id: str, clauses: list[str], values: list[Any]) -> dict:
    result_summary = None
    description = None
    members = [row for row in _members(connection, group_id) if row["micro_task_run_id"] is not None]
    matching = connection.execute(
        f"SELECT COUNT(*) AS count FROM ({MEMBERS_SQL}) WHERE group_id = ? AND "
        + " AND ".join(["micro_task_run_id IS NOT NULL", *clauses]),
        [group_id, *values],
    ).fetchone()["count"]
    counts = {"roots": 0, "helpers": 0, "active": 0, "host": 0, "review": 0, "ended": 0, "accepted": 0,
              "stoppableRoots": 0, "stoppableHelpers": 0, "delivered": 0, "awaitingHost": 0}
    from .workflow import IN_PROGRESS_STATES
    for row in members:
        if row["run_state"] in IN_PROGRESS_STATES:
            counts["stoppableHelpers" if row["child_parent_run_id"] is not None else "stoppableRoots"] += 1
        counts["delivered"] += int(row["run_state"] == "delivered")
        counts["awaitingHost"] += int(row["run_state"] == "awaiting-host")
        counts[_state(row)] += 1
        counts["accepted"] += int(row["child_parent_run_id"] is None and row["run_state"] == "accepted")
        counts["helpers" if row["child_parent_run_id"] is not None else "roots"] += 1
    state = next((name for name in ("host", "active", "review") if counts[name]), "ended")
    if group_id.startswith("macro-"):
        macro_task = connection.execute("SELECT * FROM macro_tasks WHERE macro_task_id=?", (group_id,)).fetchone()
        title, title_source = macro_task["title"], "macro-task"
        repository_path = next((row["project_repository_path"] for row in members
                                if row["project_repository_path"]), None)
        project = {"id": macro_task["project_id"], "path": macro_task["project_path"],
                   "label": micro_task.project_label(macro_task["project_path"], repository_path, "未记录项目")}
        source_host, created_at = macro_task["source_host_id"], macro_task["created_at"]
        activity_seq, activity_at = macro_task["activity_seq"], macro_task["activity_at"]
        roots = [row["task_id"] for row in members if row["child_parent_run_id"] is None]
        first = connection.execute("SELECT run_id FROM workflow_runs WHERE macro_task_id=? ORDER BY created_at, rowid LIMIT 1", (group_id,)).fetchone()
        if first:
            event = connection.execute(
                "SELECT payload_json FROM events WHERE task_id=? AND kind='workflow.macro_task_created' ORDER BY seq LIMIT 1",
                (first["run_id"],),
            ).fetchone()
            if event:
                metadata = json.loads(event["payload_json"])
                if metadata.get("macroTaskId") == group_id:
                    description = metadata.get("description")
        kind = "macro-task"
    else:
        root_id = group_id[len("run:"):]
        root = next((row for row in members if row["task_id"] == root_id), None)
        title, title_source, result_summary = _run_presentation(connection, root_id)
        metadata = micro_task.metadata_from_row(root) if root is not None else {"project": None, "sourceHostId": None}
        project, source_host = metadata["project"], metadata["sourceHostId"]
        created_at = root["created_at"] if root is not None else None
        run = connection.execute("SELECT activity_seq, activity_at FROM workflow_runs WHERE run_id=?", (root_id,)).fetchone()
        activity_seq, activity_at = (run["activity_seq"], run["activity_at"]) if run is not None else (0, None)
        roots = [root_id]
        kind = "standalone"
    current_hosts = sorted({row["current_host_id"] for row in members if row["current_host_id"]})
    return {
        "macroTaskId": group_id,
        "kind": kind,
        "title": title or UNTITLED,
        "titleSource": title_source,
        "summary": result_summary,
        "description": description,
        "project": project,
        "sourceHostId": source_host,
        "currentHostIds": current_hosts,
        "createdAt": created_at,
        # The latest recorded event of any member; unknown history stays at sequence 0.
        "lastActivityAt": activity_at or created_at,
        "lastActivitySeq": int(activity_seq or 0),
        "state": state,
        "counts": counts,
        "matchingRuns": int(matching),
        "rootRunIds": roots,
    }


# -- timeline ------------------------------------------------------------------


def macro_task_timeline(store, params: dict) -> dict:
    """The bounded, read-only timeline of one macro task or standalone micro task tree."""
    schemas.reject_unknown(params, TIMELINE_PARAMETERS, "macro_task.timeline")
    group_id = schemas.required_string(params, "macroTaskId", max_length=134)
    if not (MACRO_TASK_PATTERN.match(group_id) or STANDALONE_PATTERN.match(group_id)):
        raise BoardError("INVALID_ARGUMENT", "macroTaskId must be macro-<id> or run:<rootRunId>")
    limit = schemas.optional_int(params, "limit", TIMELINE_DEFAULT_LIMIT, 1, TIMELINE_MAX_LIMIT)
    applied, clauses, values = _filters(params)
    with store.db.read() as connection:
        observed_at = store.now()
        head = _head(connection)
        if group_id.startswith("macro-"):
            if connection.execute("SELECT 1 FROM macro_tasks WHERE macro_task_id=?", (group_id,)).fetchone() is None:
                raise BoardError("NOT_FOUND", "Unknown macroTaskId", macroTaskId=group_id)
        else:
            root = connection.execute(
                "SELECT macro_task_id FROM workflow_runs WHERE run_id=?", (group_id[len("run:"):],)
            ).fetchone()
            if root is None or root["macro_task_id"] is not None:
                # A root that belongs to a macro task is read through that macro task.
                raise BoardError("NOT_FOUND", "Unknown standalone micro task", macroTaskId=group_id)
        members = _members(connection, group_id)
        if not members:
            raise BoardError("NOT_FOUND", "The group has no readable micro tasks", macroTaskId=group_id)
        summary = _summary(connection, group_id, clauses, values)
        runs = [row for row in members if row["micro_task_run_id"] is not None]
        matching_ids = {
            row["task_id"] for row in connection.execute(
                f"SELECT task_id FROM ({MEMBERS_SQL}) WHERE group_id = ? AND "
                + " AND ".join(["micro_task_run_id IS NOT NULL", *clauses]),
                [group_id, *values],
            ).fetchall()
        }
        ordered = _tree_order(runs)
        selected = [(row, depth) for row, depth in ordered if row["task_id"] in matching_ids]
        shown = selected[:limit]
        shown_ids = [row["task_id"] for row, _depth in shown]
        rows = [_timeline_row(connection, row, depth) for row, depth in shown]
        shown = [row for row, _depth in shown]
        spans = []
        for row in shown:
            spans.extend(_run_spans(connection, row))
        routing_owner = {row["task_id"]: row["delegated_parent_run_id"] for row in members
                         if row["micro_task_run_id"] is None and row["decision_id"] is not None}
        for decision_task_id, owner in routing_owner.items():
            if owner in shown_ids:
                spans.extend(_routing_spans(connection, decision_task_id, owner))
        spans.sort(key=lambda span: (span["startAt"] or "", span["runId"], span["spanId"]))
        markers = _markers(connection, shown_ids)
        span_total, marker_total = len(spans), len(markers)
        spans, markers = spans[:MAX_SPANS], markers[:MAX_MARKERS]
    truncated = {
        "rows": len(selected) > len(shown),
        "spans": span_total > len(spans),
        "events": marker_total > len(markers),
    }
    filtered = bool(applied)
    return {
        "macroTask": summary,
        "observedAt": observed_at,
        "cursor": head,
        "rows": rows,
        "spans": spans,
        "events": markers,
        # Totals count the (filtered) scope; allRows counts every micro task of the group.
        "totals": {"rows": len(selected), "spans": span_total, "events": marker_total, "allRows": len(runs)},
        "truncated": truncated,
        "filtered": filtered,
        "scopeComplete": not filtered and not any(truncated.values()),
    }


def _tree_order(runs: list) -> list:
    """(row, depth) pairs: roots by creation, each followed depth-first by its helpers."""
    children: dict[str | None, list] = {}
    known = {row["task_id"] for row in runs}
    for row in runs:
        parent = row["child_parent_run_id"] if row["child_parent_run_id"] in known else None
        children.setdefault(parent, []).append(row)
    for items in children.values():
        items.sort(key=lambda row: (row["created_at"], row["task_id"]))
    ordered: list = []

    def visit(row, depth: int) -> None:
        ordered.append((row, depth))
        for child in children.get(row["task_id"], []):
            visit(child, depth + 1)

    for root in children.get(None, []):
        visit(root, 0)
    return ordered


def _timeline_row(connection, row, depth: int) -> dict:
    metadata = micro_task.metadata_from_row(row)
    title, title_source, result_summary = _run_presentation(connection, row["task_id"])
    task_text = connection.execute("SELECT json_extract(micro_task_json,'$.task') FROM workflow_runs WHERE run_id=?", (row["task_id"],)).fetchone()
    shutdown = connection.execute(
        "SELECT COUNT(*) AS attempts,"
        " SUM(CASE WHEN execution_state='finished' AND shutdown_confirmed=1 THEN 1 ELSE 0 END) AS confirmed"
        " FROM attempts WHERE task_id=?",
        (row["task_id"],),
    ).fetchone()
    attempts, confirmed = int(shutdown["attempts"] or 0), int(shutdown["confirmed"] or 0)
    # Unknown stop is never counted as ended: every attempt must prove its stop, and a
    # task that claims to run without any attempt record is not proven stopped either.
    unproven = row["state"] in ("running", "cancelling", "reconciliation-needed") and attempts == 0
    return {
        **fixes.view(connection, row["task_id"]),
        "runId": row["task_id"],
        "parentRunId": row["child_parent_run_id"],
        "rootRunId": row["root_run_id"],
        "title": title or UNTITLED,
        "titleSource": title_source,
        "summary": result_summary,
        "taskSummary": _first_line(task_text[0]) if task_text else None,
        "createdAt": row["created_at"],
        "state": row["run_state"],
        "status": row["state"],
        "category": _state(row),
        "shutdownConfirmed": attempts == confirmed and not unproven,
        "depth": depth,
        "kind": metadata["kind"],
        "leadOwnership": metadata["leadOwnership"],
        "rootLeadRunId": metadata["rootLeadRunId"],
        "buddy": _full_buddy(metadata["buddy"]),
        "acceptedAt": row["accepted_at"],
        "acceptanceVerdict": row["acceptance_verdict"],
        "reviewRejectedAt": row["review_rejected_at"],
        "reviewRejectionNote": row["review_rejection_note"],
    }


def _full_buddy(value: dict | None) -> dict | None:
    if not isinstance(value, dict) or not value:
        return None
    return {key: value.get(key) or None for key in micro_task.BUDDY_FIELDS}


def _reversed(start: Any, end: Any) -> bool:
    return isinstance(start, str) and bool(start) and isinstance(end, str) and bool(end) and end < start


def _span(kind: str, span_id: str, run_id: str, start: Any, end: Any, state: str, *,
          attempt_id: str | None = None, turn_id: str | None = None, turn_index: int | None = None,
          request_id: str | None = None, buddy: dict | None = None,
          shutdown_confirmed: bool | None = None, uncertain: bool = False, **extra) -> dict:
    """One recorded interval. Missing or reversed timing is reported, never repaired."""
    span = {
        "spanId": span_id,
        "runId": run_id,
        "kind": kind,
        "startAt": start or None,
        "endAt": end or None,
        "state": state,
        "attemptId": attempt_id,
        "turnId": turn_id,
        "turnIndex": turn_index,
        "requestId": request_id,
        "buddy": _full_buddy(buddy),
        "shutdownConfirmed": shutdown_confirmed,
        "uncertain": uncertain,
        "clockSkew": _reversed(start, end),
    }
    span.update({key: value for key, value in extra.items() if value is not None})
    return span


def _attempt_state(attempt) -> tuple[str, bool, bool]:
    """(state, shutdownConfirmed, uncertain) from the attempt's own recorded facts.

    A confirmed stop settles the end whatever ownership says: a lease may have
    expired and marked the attempt's ownership uncertain before the worker
    reported in, but its recorded result and stop evidence are the later fact.
    Without that evidence neither an uncertain ownership nor an unconfirmed
    finish may claim an end.
    """
    finished = attempt["execution_state"] == "finished"
    confirmed = finished and attempt["shutdown_confirmed"] == 1
    uncertain = not confirmed and (
        attempt["execution_state"] == "uncertain" or attempt["ownership"] == "uncertain" or finished
    )
    return attempt["execution_state"], confirmed, uncertain


def _turn_buddy(input_json: Any) -> dict | None:
    try:
        payload = json.loads(input_json) if isinstance(input_json, str) else None
    except ValueError:
        return None
    buddy = payload.get("buddy") if isinstance(payload, dict) else None
    if not isinstance(buddy, dict):
        return None
    return {key: buddy.get(key) for key in micro_task.BUDDY_FIELDS if buddy.get(key)}


def _run_spans(connection, row) -> list[dict]:
    """Queue, execution and waiting-Host spans of one micro-task run, from recorded rows."""
    run_id = row["task_id"]
    spans: list[dict] = []
    attempts = connection.execute(
        "SELECT a.*, json_extract(a.result_json,'$.status') AS result_status,"
        " t.turn_id, t.turn_index, t.input_json, t.disposition"
        " FROM attempts a LEFT JOIN workflow_turns t ON t.attempt_id = a.attempt_id"
        " WHERE a.task_id=? ORDER BY a.generation, a.created_at",
        (run_id,),
    ).fetchall()
    continuations = {
        item["attempt_id"]: item for item in connection.execute(
            "SELECT continuation_id, attempt_id, created_at FROM workflow_continuations"
            " WHERE run_id=? AND attempt_id IS NOT NULL", (run_id,)
        ).fetchall()
    }
    queued_from = row["created_at"]
    for attempt in attempts:
        continuation = continuations.get(attempt["attempt_id"])
        queue_start = continuation["created_at"] if continuation is not None else queued_from
        claimed = attempt["created_at"]
        if queue_start and claimed and queue_start != claimed:
            spans.append(_span("queue", f"queue:{attempt['attempt_id']}", run_id, queue_start, claimed, "claimed",
                               attempt_id=attempt["attempt_id"]))
        state, confirmed, uncertain = _attempt_state(attempt)
        buddy = _turn_buddy(attempt["input_json"]) or (
            {"harness": attempt["model_harness"], "provider": attempt["model_provider"], "model": attempt["model_model"]}
            if attempt["model_harness"] else {"adapter": attempt["adapter"]}
        )
        spans.append(_span(
            "execution", f"execution:{attempt['attempt_id']}", run_id,
            attempt["started_at"] or attempt["created_at"], attempt["finished_at"] if state == "finished" else None,
            state, attempt_id=attempt["attempt_id"], turn_id=attempt["turn_id"], turn_index=attempt["turn_index"],
            buddy=buddy, shutdown_confirmed=confirmed, uncertain=uncertain,
            generation=attempt["generation"], disposition=attempt["disposition"],
            resultStatus=attempt["result_status"],
            tokenUsage=json.loads(attempt["token_usage_json"]) if attempt["token_usage_json"] else None,
            error=(str(attempt["error"])[:SUMMARY_LIMIT] if attempt["error"] else None),
        ))
        queued_from = attempt["finished_at"] or attempt["created_at"]
    if row["state"] == "queued" and row["run_state"] != "awaiting-host":
        # A requeue after a Host wait starts when that wait closed (a decision,
        # continuation or cancellation), never under the wait it followed.
        closed = connection.execute(
            "SELECT MAX(COALESCE(decided_at, CASE WHEN state IN ('cancelled','superseded') THEN updated_at END))"
            " FROM workflow_requests WHERE run_id=? AND state != 'open'", (run_id,)).fetchone()[0]
        pending = connection.execute(
            "SELECT MAX(created_at) FROM workflow_continuations WHERE run_id=? AND attempt_id IS NULL",
            (run_id,)).fetchone()[0]
        for later in (closed, pending):
            if later and (not queued_from or later > queued_from):
                queued_from = later
        # Waiting for a claim right now: the queue interval is still open. A run
        # parked on its Host is not waiting for a Worker; its open host span
        # below is that interval, and a second open span would cover its label.
        spans.append(_span("queue", f"queue:open:{run_id}", run_id, queued_from, None, "queued"))
    for request in connection.execute(
        "SELECT request_id, kind, state, summary, created_at, decided_at, updated_at FROM workflow_requests"
        " WHERE run_id=? ORDER BY created_at, request_id", (run_id,)
    ).fetchall():
        open_request = request["state"] == "open"
        # A cancellation or a Host continuation closes an open request without a
        # decision, in the same transaction that stamps its updated_at; that
        # recorded time is the wait's end.
        closed_at = request["decided_at"] or (
            request["updated_at"] if request["state"] in ("cancelled", "superseded") else None)
        spans.append(_span(
            "host", f"host:{request['request_id']}", run_id, request["created_at"],
            None if open_request else (closed_at or None), request["state"],
            request_id=request["request_id"], requestKind=request["kind"],
            summary=(request["summary"] or "")[:SUMMARY_LIMIT] or None,
        ))
    return spans


def _routing_spans(connection, decision_task_id: str, owner_run_id: str) -> list[dict]:
    # Resolve the immutable decision from its own calculation task, never from
    # the owner's current workflow route (which may have been retried).
    record = connection.execute(
        "SELECT d.decision_id, d.status, d.buddy_id, d.reason, r.selected_json, r.output_json, r.requested_json"
        " FROM decision_requests r JOIN evaluation_decisions d USING(decision_id)"
        " WHERE r.task_id=?", (decision_task_id,)
    ).fetchone()
    output = json.loads(record["output_json"]) if record and record["output_json"] else {}
    output = output if isinstance(output, dict) else {}
    usage = output.get("usage") if isinstance(output.get("usage"), dict) else {}
    routing = {
        **router.routing_facts(json.loads(record["requested_json"]) if record else {}),
        "selectedBuddy": json.loads(record["selected_json"]) if record and record["selected_json"] else None,
        "reason": (record["reason"] or None) if record else None,
        "policyCheck": output.get("policyCheck"),
        "budget": output.get("budget"),
        "usage": {key: usage.get(key) for key in ("elapsedMs", "toolCalls", "bytesRead")},
    }
    abstention = False
    if record and record["status"] == "needs-host" and output.get("status") == "ok" and record["buddy_id"] is None:
        try:
            answer = router.validate_answer(output.get("decision"), [])
            abstention = answer["buddyId"] is None
        except BoardError:
            pass
    spans = []
    for attempt in connection.execute(
        "SELECT attempt_id, execution_state, shutdown_confirmed, ownership, started_at, finished_at, created_at,"
        " json_extract(result_json,'$.status') AS result_status, result_json, error, token_usage_json"
        " FROM attempts WHERE task_id=? ORDER BY generation", (decision_task_id,)
    ).fetchall():
        state, confirmed, uncertain = _attempt_state(attempt)
        attempt_output = (json.loads(attempt['result_json']) if attempt['result_json'] else {}).get('result') or {}
        span_routing = routing
        if isinstance(attempt_output, dict) and 'routingMode' in attempt_output:
            span_routing = {**routing, **router.routing_facts(attempt_output)}
            if attempt_output.get('code') == 'router-review-unavailable':
                span_routing.update(selectedBuddy=None, reason=attempt_output.get('reason'),
                                    policyCheck=None, budget=attempt_output.get('budget'), usage=None)
        spans.append(_span(
            "routing", f"routing:{attempt['attempt_id']}", owner_run_id,
            attempt["started_at"] or attempt["created_at"], attempt["finished_at"] if state == "finished" and not uncertain else None,
            state, attempt_id=attempt["attempt_id"], shutdown_confirmed=confirmed, uncertain=uncertain,
            decisionTaskId=decision_task_id, resultStatus=attempt["result_status"],
            routing=span_routing,
            tokenUsage=json.loads(attempt["token_usage_json"]) if attempt["token_usage_json"] else None,
            disposition=("abstention" if abstention and attempt["result_status"] != "cancelled"
                         else "routing-failed" if record and record["status"] in ("failed", "needs-host") and output.get("code")
                         else None),
            error=(str(attempt["error"])[:SUMMARY_LIMIT] if attempt["error"] else None),
        ))
        spans[-1]["decisionId"] = record["decision_id"] if record else None
    return spans


def _markers(connection, run_ids: list[str]) -> list[dict]:
    if not run_ids:
        return []
    placeholders = ",".join("?" for _ in run_ids)
    kinds = ",".join("?" for _ in HOST_MARKERS)
    rows = connection.execute(
        f"SELECT seq, task_id, attempt_id, kind, payload_json, created_at, actor_role, actor_id FROM events"
        f" WHERE task_id IN ({placeholders}) AND kind IN ({kinds}) ORDER BY seq",
        [*run_ids, *HOST_MARKERS],
    ).fetchall()
    markers = []
    for row in rows:
        try:
            payload = json.loads(row["payload_json"]) if row["payload_json"] else {}
        except ValueError:
            payload = {}
        payload = payload if isinstance(payload, dict) else {}
        kind = HOST_MARKERS[row["kind"]]
        if kind == "accept" and payload.get("verdict") == "rejected":
            kind = "reject"

        def text(key: str) -> str | None:
            value = payload.get(key)
            return value[:SUMMARY_LIMIT] if isinstance(value, str) and value else None

        micro_task = payload.get("microTask") if isinstance(payload.get("microTask"), dict) else {}
        fallback_host = text("newHostId") or (
            micro_task.get("sourceHostId") if isinstance(micro_task.get("sourceHostId"), str) else None)
        actor = ({"role": row["actor_role"], "id": row["actor_id"]} if row["actor_role"] else
                 {"role": "host", "id": fallback_host} if fallback_host else None)
        details = [part for part in (text("decision"), text("verdict"), text("state"), text("strategy"),
                                     text("reason")) if part]
        markers.append({
            "seq": row["seq"],
            "runId": row["task_id"],
            "kind": kind,
            "at": row["created_at"],
            "label": MARKER_LABELS[kind],
            "summary": " · ".join(details)[:SUMMARY_LIMIT],
            "actor": actor,
            "attemptId": row["attempt_id"],
            "requestId": text("requestId"),
            "artifactId": text("artifactId"),
            "integrationId": text("integrationId"),
            "continuationId": text("continuationId"),
            "stopInitiator": text("stopInitiator"),
            "eventKind": row["kind"],
        })
    return markers
