"""Human macro task metadata and bounded read projections, never execution input.

A macro task groups micro tasks for browsing and archival. It owns a
title, revisioned goal and project provenance: it schedules no work,
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

from . import fixes, micro_task, reminders
from .request_reasons import request_reason
from .macro_task_deletion_guard import reject_macro_task
from ..routing import router, router_sequence
from ...protocol import schemas
from ...protocol.turn_input import turn_buddy
from ...protocol.reasons import reason_from_json
from ...errors import BoardError

LIST_FIRST_LIMIT = 5
LIST_GROUP_LIMIT = 20
LIST_MAX_LIMIT = 100
TIMELINE_DEFAULT_LIMIT = 100
TIMELINE_MAX_LIMIT = 200
MAX_SPANS = 800
MAX_MARKERS = 800
ERROR_LIMIT = 300
TITLE_LIMIT = 200
CURSOR_VERSION = 3
LIST_SORTS = frozenset({"activity-desc", "activity-asc", "created-desc", "created-asc"})

MACRO_TASK_PATTERN = re.compile(r"^macro-[A-Za-z0-9-]{1,120}$")
STANDALONE_PATTERN = re.compile(r"^run:([A-Za-z0-9._:-]{1,128})$")
_CURSOR_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,512}$")

LIST_PARAMETERS = frozenset({"limit", "before", "group", "query", "filter", "sort"})
TIMELINE_PARAMETERS = frozenset({"macroTaskId", "limit", "query", "filter"})
GOAL_HISTORY_PARAMETERS = frozenset({"macroTaskId"})

#: Group ownership follows helpers and Lead-owned runs to their outermost owner.
RUN_GROUPS_SQL = """
WITH RECURSIVE parents(run_id, parent_run_id) AS (
    SELECT child_task_id, parent_run_id FROM workflow_children
    UNION SELECT owned_run_id, lead_run_id FROM workflow_lead_tasks
),
lineage(run_id, ancestor_id) AS (
    SELECT run_id, run_id FROM workflow_runs
    UNION
    SELECT lineage.run_id, parents.parent_run_id
      FROM lineage JOIN parents ON parents.run_id = lineage.ancestor_id
)
SELECT lineage.run_id,
       COALESCE(ancestor.macro_task_id, 'run:' || ancestor.run_id) AS group_id
  FROM lineage
  JOIN workflow_runs ancestor ON ancestor.run_id = lineage.ancestor_id
 WHERE NOT EXISTS (SELECT 1 FROM parents WHERE parents.run_id = lineage.ancestor_id)
"""

#: Every grouped execution, including helper and Router tasks. Plain execution
#: records without a micro task root belong to no group. Materialize history as
#: well as ownership: across history's many LEFT JOINs SQLite otherwise estimates
#: too few outer rows to build a lookup index for the computed root_run_id join.
MEMBERS_SQL = f"""
WITH history AS MATERIALIZED ({micro_task.TASK_HISTORY_SQL}),
ownership AS MATERIALIZED ({RUN_GROUPS_SQL})
SELECT h.*, ownership.group_id
  FROM history h JOIN ownership ON ownership.run_id = h.root_run_id
"""

# Build at import so source drift cannot silently remove the page restriction.
PAGE_HISTORY_SQL = micro_task.replace_sql_once(
    micro_task.replace_sql_once(
        micro_task.TASK_HISTORY_SQL, "t.*,", "t.rowid AS summary_task_rowid, t.*,",
    ),
    "FROM tasks t\n", "FROM tasks t JOIN page_tasks ON page_tasks.task_id=t.task_id\n",
)

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
}


# -- admission and activity ----------------------------------------------------


#: The console top bar's pending count: distinct micro-task-tree roots with an
#: awaiting-host run or a stopped failed/cancelled root, except Lead-owned trees.
#: It reuses the same Host predicate and lineage
#: walk as the history relation (``micro_task.RELATION_SQL``) and stays a bounded
#: aggregate over the whole board instead of decorating the latest task page.
PENDING_ROOTS_SQL = f"""
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
  JOIN workflow_runs awaiting ON awaiting.run_id = tasks.task_id
  LEFT JOIN roots ON roots.task_id = tasks.task_id
  LEFT JOIN workflow_children child ON child.child_task_id=tasks.task_id
 WHERE {micro_task.host_filter_sql(run_state='awaiting.state', task_state='tasks.state',
                                  parent_run_id='child.parent_run_id',
                                  root_run_id='COALESCE(roots.root_run_id, tasks.task_id)')}
"""


def pending_root_count(store) -> int:
    """Distinct roots awaiting their Host, including stopped root outcomes.

    Keep this as a board-wide aggregate: no decorated history or task payloads
    are needed to apply the same predicate as the Host filter.
    """
    with store.db.read() as connection:
        return int(connection.execute(PENDING_ROOTS_SQL).fetchone()["pending"])


def attach_macro_task(connection, macro_task_id: str | None, *, project_id: str) -> str | None:
    """Validate an explicit macro binding in the run admission transaction."""
    if macro_task_id is None:
        return None
    reject_macro_task(connection, macro_task_id)
    row = connection.execute("SELECT project_id FROM macro_tasks WHERE macro_task_id=?", (macro_task_id,)).fetchone()
    if row is None:
        raise BoardError("NOT_FOUND", "Unknown macroTaskId", macroTaskId=macro_task_id)
    if row["project_id"] != project_id:
        raise BoardError("CONFLICT", "The macro task belongs to another project", macroTaskId=macro_task_id)
    return macro_task_id


def create_macro_task(store, params: dict) -> dict:
    """Create one empty Git macro task; a repeated creation key reads the original."""
    project_id = params["projectId"]
    creation_key = params["creationKey"]
    now = store.now()
    with store.db.write() as connection:
        existing = connection.execute(
            "SELECT macro_task_id FROM macro_tasks WHERE creation_key=?", (creation_key,),
        ).fetchone()
        if existing is not None:
            return {**_macro_task_get(connection, existing["macro_task_id"]), "alreadyExists": True}
        identifier = f"macro-{uuid.uuid4()}"
        connection.execute(
            "INSERT INTO macro_tasks(macro_task_id,title,project_id,project_cwd,repository_path,"
            " creation_key,created_at,activity_at) VALUES(?,?,?,?,?,?,?,?)",
            (identifier, params["title"], project_id, params["cwd"], params["repositoryPath"],
             creation_key, now, now),
        )
        connection.execute(
            "INSERT INTO macro_task_goals(macro_task_id,revision,body,created_at) VALUES(?,1,?,?)",
            (identifier, params["goal"], now),
        )
        seq = store._append_event(connection, "macro_task.created",
                                  payload={"macroTaskId": identifier, "actor": {"role": "host"}})
        connection.execute("UPDATE macro_tasks SET activity_seq=? WHERE macro_task_id=?", (seq, identifier))
        result = _macro_task_get(connection, identifier)
        head = store._head_of(connection)
    store._notify(head)
    return {**result, "alreadyExists": False}


def amend_macro_task(store, params: dict) -> dict:
    """Append a goal version when its exact body differs from the current one."""
    identifier, goal = params["macroTaskId"], params["goal"]
    now = store.now()
    with store.db.write() as connection:
        reject_macro_task(connection, identifier)
        current = current_goal(connection, identifier)
        if connection.execute("SELECT 1 FROM macro_tasks WHERE macro_task_id=?", (identifier,)).fetchone() is None:
            raise BoardError("NOT_FOUND", "Unknown macroTaskId", macroTaskId=identifier)
        if current is not None and current["body"] == goal:
            return {**_macro_task_get(connection, identifier), "alreadyCurrent": True}
        revision = current["revision"] if current is not None else 0
        connection.execute(
            "INSERT INTO macro_task_goals(macro_task_id,revision,body,created_at) VALUES(?,?,?,?)",
            (identifier, revision + 1, goal, now),
        )
        seq = store._append_event(connection, "macro_task.goal_amended",
                                  payload={"macroTaskId": identifier, "goalRevision": revision + 1,
                                           "actor": {"role": "host"}})
        connection.execute("UPDATE macro_tasks SET activity_seq=?,activity_at=? WHERE macro_task_id=?",
                           (seq, now, identifier))
        result = _macro_task_get(connection, identifier)
        head = store._head_of(connection)
    store._notify(head)
    return {**result, "alreadyCurrent": False}


def current_goal(connection, macro_task_id: str | None) -> dict | None:
    row = connection.execute(
        "SELECT body,revision,created_at FROM macro_task_goals WHERE macro_task_id=? ORDER BY revision DESC LIMIT 1",
        (macro_task_id,),
    ).fetchone()
    return {"body": row["body"], "revision": row["revision"], "createdAt": row["created_at"]} if row else None


def macro_task_goal_history(store, params: dict) -> dict:
    """Read every goal revision for one macro task, newest first."""
    schemas.reject_unknown(params, GOAL_HISTORY_PARAMETERS, "macro_task.goal_history")
    macro_task_id = schemas.required_string(params, "macroTaskId", max_length=126)
    if not MACRO_TASK_PATTERN.fullmatch(macro_task_id):
        raise BoardError("INVALID_ARGUMENT", "macroTaskId must be macro-<id>")
    with store.db.read() as connection:
        if connection.execute("SELECT 1 FROM macro_tasks WHERE macro_task_id=?", (macro_task_id,)).fetchone() is None:
            raise BoardError("NOT_FOUND", "Unknown macroTaskId", macroTaskId=macro_task_id)
        rows = connection.execute(
            "SELECT body,revision,created_at FROM macro_task_goals"
            " WHERE macro_task_id=? ORDER BY revision DESC", (macro_task_id,),
        ).fetchall()
    return {"macroTaskId": macro_task_id,
            "goals": [{"body": row["body"], "revision": row["revision"], "createdAt": row["created_at"]}
                      for row in rows]}


def project(connection, macro_task_id: str) -> dict:
    """Use the macro task's durable source project, even before any run exists."""
    row = connection.execute(
        "SELECT project_id,project_cwd,repository_path FROM macro_tasks WHERE macro_task_id=?",
        (macro_task_id,),
    ).fetchone()
    return {"id": row["project_id"], "kind": "git", "path": row["project_cwd"],
            "label": micro_task.project_label(row["project_cwd"], row["repository_path"])}


def _macro_task_get(connection, identifier: str, *, limit: int = 100, before: str | None = None,
                    include_runs: bool = True, include_goal: bool = True) -> dict:
    row = connection.execute("SELECT * FROM macro_tasks WHERE macro_task_id=?", (identifier,)).fetchone()
    if row is None:
        raise BoardError("NOT_FOUND", "Unknown macroTaskId", macroTaskId=identifier)
    inputs = _summary_inputs(connection, [identifier], [], [])[identifier]
    summary = _summary(connection, identifier, inputs)
    total = len(inputs["members"])
    if before is not None:
        pivot = connection.execute("SELECT created_at,run_id FROM workflow_runs"
                                   " WHERE run_id=? AND macro_task_id=?", (before, identifier)).fetchone()
        if pivot is None:
            raise BoardError("INVALID_ARGUMENT", "before is not a member of this macro task", field="before")
    else:
        pivot = None
    roots = connection.execute(
        "SELECT run_id,state,title,created_at,activity_at FROM workflow_runs"
        " WHERE macro_task_id=? AND (? IS NULL OR created_at>? OR (created_at=? AND run_id>?))"
        " ORDER BY created_at,run_id LIMIT ?",
        (identifier, before, pivot["created_at"] if pivot else None,
         pivot["created_at"] if pivot else None, before, limit + 1 if include_runs else 0),
    ).fetchall() if include_runs else []
    goal = current_goal(connection, identifier) if include_goal else None
    goal_revision = (goal["revision"] if goal else connection.execute(
        "SELECT COALESCE(MAX(revision),0) FROM macro_task_goals WHERE macro_task_id=?",
        (identifier,),
    ).fetchone()[0])
    deleting = connection.execute("SELECT 1 FROM macro_task_deletions"
                                  " WHERE target_kind='macro-task' AND target_id=?", (identifier,)).fetchone()
    result = {"macroTaskId": identifier, "title": row["title"],
            "goalRevision": goal_revision,
            "project": project(connection, identifier), "createdAt": row["created_at"],
            "lastActivityAt": row["activity_at"], "state": summary["state"],
            "counts": summary["counts"], "memberCount": total,
            "runs": [{"runId": run["run_id"], "title": run["title"], "state": run["state"],
                      "createdAt": run["created_at"], "lastActivityAt": run["activity_at"]}
                     for run in roots[:limit]],
            "nextCursor": roots[limit - 1]["run_id"] if len(roots) > limit else None,
            "actions": ["get"] if deleting else ["amend", "submit", "get"]}
    if include_goal:
        result["goal"] = goal
    return result


def macro_task_get(store, params: dict) -> dict:
    identifier = params["macroTaskId"]
    with store.db.read() as connection:
        return _macro_task_get(connection, identifier, limit=params.get("limit", 100), before=params.get("before"))


def project_get(store, params: dict) -> dict:
    """Read bounded Git macro and standalone roots of one durable project id."""
    identifier = params["projectId"]
    limit = params.get("limit", 20)
    before = params.get("before")
    with store.db.read() as connection:
        base = """
WITH items AS (
    SELECT macro_task_id AS id,created_at,'macro-task' AS kind FROM macro_tasks WHERE project_id=?
    UNION ALL
    SELECT run_id AS id,created_at,'standalone' AS kind FROM workflow_runs
     WHERE standalone_project_id=? AND macro_task_id IS NULL
)
"""
        first = connection.execute(base + "SELECT id,kind FROM items ORDER BY created_at,id LIMIT 1",
                                   (identifier, identifier)).fetchone()
        if first is None:
            raise BoardError("NOT_FOUND", "Unknown project", projectId=identifier)
        before_row = None
        if before is not None:
            before_row = connection.execute(base + "SELECT id,created_at FROM items WHERE id=?",
                                            (identifier, identifier, before)).fetchone()
            if before_row is None:
                raise BoardError("INVALID_ARGUMENT", "before is not a project item", field="before")
        items = connection.execute(
            base + "SELECT id,kind,created_at FROM items WHERE (? IS NULL OR created_at < ?"
            " OR (created_at=? AND id<?)) ORDER BY created_at DESC,id DESC LIMIT ?",
            (identifier, identifier,
             before, before_row["created_at"] if before_row else None,
             before_row["created_at"] if before_row else None, before, limit + 1),
        ).fetchall()
        visible = items[:limit]
        if first["kind"] == "macro-task":
            project_view = project(connection, first["id"])
        else:
            history = connection.execute(f"{micro_task.TASK_HISTORY_SQL} WHERE task_id=?",
                                         (first["id"],)).fetchone()
            project_view = micro_task.metadata_from_row(history)["project"]
        rows = []
        for item in visible:
            if item["kind"] == "macro-task":
                rows.append({"kind": "macro-task", **_macro_task_get(connection, item["id"],
                                                                       include_runs=False, include_goal=False)})
            else:
                run = connection.execute("SELECT * FROM workflow_runs WHERE run_id=?",
                                         (item["id"],)).fetchone()
                rows.append({"kind": "standalone", "runId": run["run_id"], "title": run["title"],
                             "state": run["state"], "createdAt": run["created_at"],
                             "actions": store.workflow._actions(connection, run)})
        return {"project": project_view, "items": rows,
                "nextCursor": visible[-1]["id"] if len(items) > limit else None}


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
        " UNION SELECT c.parent_run_id FROM workflow_children c JOIN owners o ON c.child_task_id=o.run_id"
        " UNION SELECT l.lead_run_id FROM workflow_lead_tasks l JOIN owners o ON l.owned_run_id=o.run_id)"
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
    filter_name = schemas.optional_string(params, "filter", max_length=16) or "all"
    if filter_name not in micro_task.HISTORY_FILTERS:
        raise BoardError("INVALID_ARGUMENT", "filter must be one of: all, active, host, review")
    clauses, values = micro_task.history_where(query=query, filter_name=filter_name)
    applied = {"query": query} if query else {}
    if filter_name != "all":
        applied["filter"] = filter_name
    return applied, clauses, values


def _fingerprint(applied: dict) -> str:
    return hashlib.sha256(json.dumps(applied, sort_keys=True, separators=(",", ":")).encode()).hexdigest()[:16]


def _encode_cursor(key: int | str, group_id: str, applied: dict, head: int, sort: str) -> str:
    payload = json.dumps({"v": CURSOR_VERSION, "k": key, "g": group_id, "f": _fingerprint(applied),
                          "h": head, "o": sort},
                         separators=(",", ":"), sort_keys=True)
    return base64.urlsafe_b64encode(payload.encode()).decode().rstrip("=")


def _decode_cursor(value: str, applied: dict, sort: str) -> tuple[int | str, str, int]:
    invalid = BoardError("INVALID_ARGUMENT", "before must be the nextCursor of macro_task_list; read the first page again")
    if not isinstance(value, str) or not _CURSOR_PATTERN.match(value):
        raise invalid
    try:
        payload = json.loads(base64.b64decode(value + "=" * (-len(value) % 4), altchars=b"-_", validate=True))
    except (binascii.Error, ValueError, UnicodeDecodeError):
        raise invalid from None
    if not isinstance(payload, dict) or set(payload) != {"v", "k", "g", "f", "h", "o"} or payload["v"] != CURSOR_VERSION:
        raise invalid
    if payload["o"] != sort:
        raise BoardError("INVALID_ARGUMENT", "This cursor was issued for a different sort; read the first page again",
                         field="before")
    key, group_id, head = payload["k"], payload["g"], payload["h"]
    if isinstance(head, bool) or not isinstance(head, int) or head < 0:
        raise invalid
    if sort.startswith("activity-"):
        if isinstance(key, bool) or not isinstance(key, int) or key < 0:
            raise invalid
    elif not isinstance(key, str) or not micro_task._TIMESTAMP_PATTERN.match(key) or not any(c.isdigit() for c in key):
        raise invalid
    if not isinstance(group_id, str) or not (MACRO_TASK_PATTERN.match(group_id) or STANDALONE_PATTERN.match(group_id)):
        raise invalid
    if payload["f"] != _fingerprint(applied):
        raise BoardError(
            "INVALID_ARGUMENT", "This cursor was issued for different filters; read the first page again",
            field="before",
        )
    return key, group_id, head


def _head(connection) -> int:
    row = connection.execute("SELECT COALESCE(MAX(seq), 0) AS head FROM events").fetchone()
    return int(row["head"])


def _first_line(text: Any) -> str | None:
    if not isinstance(text, str) or not text.strip():
        return None
    return " ".join(text.strip().splitlines()[0].split())[:TITLE_LIMIT]


def _run_title(connection, run_id: str) -> tuple[str | None, str]:
    """Use the Host's title or task text for an index, without reading reports."""
    row = connection.execute(
        "SELECT r.title,json_extract(t.spec_json,'$.task') AS task"
        " FROM workflow_runs r JOIN tasks t ON t.task_id=r.run_id WHERE r.run_id=?",
        (run_id,),
    ).fetchone()
    if row is None:
        return None, "none"
    if isinstance(row["title"], str) and row["title"].strip():
        return row["title"].strip()[:TITLE_LIMIT], "title"
    task = _first_line(row["task"])
    if task:
        return task, "task"
    return None, "none"


def _state(row) -> str:
    """One member's display state: host, active, review or ended."""
    run_state, state = row["run_state"], row["state"]
    if run_state == "awaiting-host" and row["root_lead_run_id"] is None:
        return "host"
    if state in micro_task.ACTIVE_TASK_STATES or run_state in (
        "executing", "waiting-helpers", "waiting-owned", "awaiting-host"
    ):
        return "active"
    if run_state in micro_task.UNCONCLUDED_RESULT_STATES and row["child_parent_run_id"] is None:
        return "host" if row["root_lead_run_id"] is None else "active"
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
    """All matching project groups with bounded summaries in each group."""
    schemas.reject_unknown(params, LIST_PARAMETERS, "macro_task.list")
    group = schemas.optional_string(params, "group", max_length=micro_task.PROJECT_ID_MAX_LENGTH + 8)
    if group is not None and group != "standalone" and not group.startswith("project:"):
        raise BoardError("INVALID_ARGUMENT", "group must be project:<projectId> or standalone", field="group")
    if group == "project:":
        raise BoardError("INVALID_ARGUMENT", "group must name a project", field="group")
    limit = schemas.optional_int(params, "limit", LIST_GROUP_LIMIT if group else LIST_FIRST_LIMIT, 1, LIST_MAX_LIMIT)
    sort = schemas.optional_string(params, "sort", max_length=32) or "activity-desc"
    if sort not in LIST_SORTS:
        raise BoardError("INVALID_ARGUMENT", "sort must be one of: activity-desc, activity-asc, created-desc, created-asc",
                         field="sort")
    sort_column, direction = sort.split("-")
    order_column = "activity_seq" if sort_column == "activity" else "created_at"
    ordering = f"{order_column} {direction.upper()}, group_id {direction.upper()}"
    applied, clauses, values = _filters(params)
    common_clauses, common_values = micro_task.history_where(
        query=applied.get("query"),
    )
    before_value = schemas.optional_string(params, "before", max_length=512)
    if before_value is not None and group is None:
        raise BoardError("INVALID_ARGUMENT", "before requires group", field="before")
    if group is not None:
        applied["group"] = group
    before = _decode_cursor(before_value, applied, sort) if before_value is not None else None
    base_match = " AND ".join(["micro_task_run_id IS NOT NULL", *common_clauses])
    selected_filter = micro_task.FILTER_SQL[applied.get("filter", "all")]
    selected_match = (
        f"CASE WHEN base_matching=1 AND {selected_filter} THEN 1 ELSE 0 END"
        if selected_filter else "base_matching"
    )
    macro_query = applied.get("query")
    macro_pattern = f"%{micro_task._escape_like(macro_query)}%" if macro_query else None
    macro_match = ("1" if macro_pattern is None else
                   "(o.title LIKE ? ESCAPE '\\' OR EXISTS (SELECT 1 FROM macro_task_goals g"
                   " WHERE g.macro_task_id=o.macro_task_id AND g.body LIKE ? ESCAPE '\\'))")
    from . import macro_task_summary_cache as memo

    # The materialized relation carries only scalars consumed by the summary and
    # the SQL matching flag. In particular no t.* / result / manifest / outcome
    # body is copied merely to notice a change. One relation serves matching,
    # all four totals, order, page membership and the per-group dependency marker.
    narrow = ",".join(f"h.{column}" for column in memo.MEMBER_COLUMNS)
    page_values = []
    page_conditions = []
    if group is not None:
        page_conditions.append("project_group = ?")
        page_values.append(group)
    if before is not None:
        key, group_id, _issued_head = before
        comparison = "<" if direction == "desc" else ">"
        page_conditions.append(f"({order_column} {comparison} ? OR "
                               f"({order_column} = ? AND group_id {comparison} ?))")
        page_values.extend([key, key, group_id])
    page_where = "WHERE " + " AND ".join(page_conditions) if page_conditions else ""
    project_where = "WHERE project_group = ?" if group is not None else ""
    project_values = [group] if group is not None else []
    groups_sql = f"""
WITH history AS MATERIALIZED (
    SELECT {narrow}, CASE WHEN {base_match} THEN 1 ELSE 0 END AS base_matching
      FROM ({memo.THIN_HISTORY_SQL}) h
),
ownership AS MATERIALIZED ({RUN_GROUPS_SQL}),
members AS MATERIALIZED (
    SELECT base.*, {selected_match} AS matching
      FROM (SELECT {narrow}, h.base_matching, ownership.group_id FROM history h
              JOIN ownership ON ownership.run_id=h.root_run_id) base
),
matched AS (
    SELECT group_id, MAX(base_matching) AS all_match, MAX(matching) AS selected_match,
           MAX(CASE WHEN base_matching=1 AND {micro_task.FILTER_SQL['active']} THEN 1 ELSE 0 END) AS active_match,
           MAX(CASE WHEN base_matching=1 AND {micro_task.FILTER_SQL['host']} THEN 1 ELSE 0 END) AS host_match,
           MAX(CASE WHEN base_matching=1 AND {micro_task.FILTER_SQL['review']} THEN 1 ELSE 0 END) AS review_match,
           MAX(CASE WHEN {micro_task.FILTER_SQL['active']} THEN 1 ELSE 0 END) AS active_any,
           MAX(CASE WHEN {micro_task.FILTER_SQL['host']} THEN 1 ELSE 0 END) AS host_any,
           MAX(CASE WHEN {micro_task.FILTER_SQL['review']} THEN 1 ELSE 0 END) AS review_any
      FROM members GROUP BY group_id
),
macro_text AS (
    SELECT o.*, {macro_match} AS text_match FROM macro_tasks o
),
eligible AS (
    SELECT o.macro_task_id AS group_id,o.activity_seq,o.created_at,
           'project:' || o.project_id AS project_group,
           CASE WHEN COALESCE(m.all_match,0)=1 OR o.text_match THEN 1 ELSE 0 END AS all_match,
           CASE WHEN COALESCE(m.active_match,0)=1 OR (o.text_match AND COALESCE(m.active_any,0)=1)
                THEN 1 ELSE 0 END AS active_match,
           CASE WHEN COALESCE(m.host_match,0)=1 OR (o.text_match AND COALESCE(m.host_any,0)=1)
                THEN 1 ELSE 0 END AS host_match,
           CASE WHEN COALESCE(m.review_match,0)=1 OR (o.text_match AND COALESCE(m.review_any,0)=1)
                THEN 1 ELSE 0 END AS review_match
      FROM macro_text o LEFT JOIN matched m ON m.group_id=o.macro_task_id
    UNION ALL
    SELECT matched.group_id,root.activity_seq,root.created_at,'standalone' AS project_group,
           matched.all_match,matched.active_match,matched.host_match,matched.review_match
      FROM matched JOIN workflow_runs root ON matched.group_id='run:' || root.run_id
),
groups AS (
    SELECT * FROM eligible WHERE {applied.get("filter", "all")}_match=1
),
ordered AS (
    SELECT groups.*, ROW_NUMBER() OVER (PARTITION BY project_group ORDER BY {ordering}) AS ordinal
      FROM groups
),
projects AS (
    SELECT project_group, COUNT(*) AS project_total,
           MAX(CASE WHEN ordinal=1 THEN {order_column} END) AS first_key,
           MAX(CASE WHEN ordinal=1 THEN group_id END) AS first_group_id
      FROM ordered {project_where} GROUP BY project_group
),
page AS (
    SELECT * FROM (
        SELECT ordered.*, ROW_NUMBER() OVER (PARTITION BY project_group ORDER BY {ordering}) AS page_rank
          FROM ordered {page_where}
    ) WHERE page_rank <= ?
),
stats AS (SELECT COALESCE(SUM(all_match),0) AS all_total,
                COALESCE(SUM(active_match),0) AS active_total,
                COALESCE(SUM(host_match),0) AS host_total,
                COALESCE(SUM(review_match),0) AS review_total FROM eligible),
latest AS (SELECT COALESCE(MAX(activity_seq),0) AS latest FROM groups)
SELECT stats.*,latest.latest,projects.project_group,projects.project_total,
       projects.first_key,projects.first_group_id,
       page.group_id AS page_group_id,page.activity_seq,page.created_at AS page_created_at,members.*
  FROM stats CROSS JOIN latest LEFT JOIN projects ON 1=1
  LEFT JOIN page ON page.project_group=projects.project_group
  LEFT JOIN members ON members.group_id=page.group_id
 ORDER BY CASE WHEN projects.project_group='standalone' THEN 1 ELSE 0 END,
          projects.first_key {direction.upper()},projects.first_group_id {direction.upper()},
          page.{order_column} {direction.upper()},page.group_id {direction.upper()},members.task_id
"""
    cache = memo.for_store(store)
    # Lock precedes the read transaction so concurrent list calls serialize
    # snapshots, not just insertion. WAL permits a concurrent writer; all facts
    # below (including a cache miss's _summary) remain from this one snapshot.
    with cache.lock, store.db.read() as connection:
        head = _head(connection)
        rows = connection.execute(groups_sql, [*common_values, *([macro_pattern, macro_pattern] if macro_pattern else []),
                                               *project_values, *page_values, limit + 1]).fetchall()
        totals = {name: int(rows[0][f"{name}_total"]) for name in ("all", "active", "host", "review")}
        changed = before is not None and int(rows[0]["latest"]) > before[2]
        groups = {}
        projects = {}
        for row in rows:
            if row["project_group"] is not None:
                projects.setdefault(row["project_group"], row)
            if row["page_group_id"] is not None:
                groups.setdefault(row["page_group_id"], []).append(row)
        grouped = {project_id: [] for project_id in projects}
        for item, members in groups.items():
            grouped[members[0]["project_group"]].append(item)
        pages = {project_id: items[:limit] for project_id, items in grouped.items()}
        page = [item for project_page in pages.values() for item in project_page]
        markers, reusable = memo.dependencies(connection, {item: groups[item] for item in page})
        filters = json.dumps({key: value for key, value in applied.items() if key != "group"},
                             sort_keys=True, ensure_ascii=False)
        information = store.db.path.stat()
        board = (information.st_dev, information.st_ino)
        keys = {item: (board, item, filters) for item in page}
        cached = {item: cache.lookup(keys[item], markers[item], reusable=reusable[item])
                  for item in page}
        missing = [item for item in page if cached[item] is None]
        summary_inputs = _summary_inputs(connection, missing, clauses, values) if missing else {}
        summaries = {item: cached[item] if cached[item] is not None else cache.get(
            keys[item], markers[item],
            lambda item=item: _summary(connection, item, summary_inputs[item]),
            reusable=reusable[item],
        ) for item in page}
        issued_head = before[2] if before is not None else head
        result_groups = []
        for project_id, project_row in projects.items():
            identifiers = pages[project_id]
            group_project = None if project_id == "standalone" else (
                summaries[identifiers[0]]["project"] if identifiers else project(connection, project_row["first_group_id"])
            )
            next_cursor = None
            if len(grouped[project_id]) > limit:
                last = identifiers[-1]
                row = groups[last][0]
                next_cursor = _encode_cursor(row["activity_seq" if sort_column == "activity" else "page_created_at"],
                                             last, {**applied, "group": project_id}, issued_head, sort)
            result_groups.append({"id": project_id, "project": group_project,
                                  "total": int(project_row["project_total"]),
                                  "macroTasks": [summaries[item] for item in identifiers],
                                  "nextCursor": next_cursor})
        if group is not None and not result_groups:
            result_groups.append({"id": group, "project": None, "total": 0,
                                  "macroTasks": [], "nextCursor": None})
    return {"groups": result_groups, "totals": totals, "cursor": head, "changed": changed}


def _members(connection, group_id: str) -> list:
    return connection.execute(f"SELECT * FROM ({MEMBERS_SQL}) WHERE group_id = ?", (group_id,)).fetchall()


def _summary_inputs(connection, groups: list[str], clauses: list[str], values: list[Any]) -> dict:
    """Derive the wanted groups' executions, matches and failed conclusions once.

    Resolve ownership with the shared relation, then restrict the task-history
    entry point to those runs and their Routers. Its lineage and stop-proof CTEs
    remain intact: review readiness still covers every owned execution.
    """
    placeholders = ",".join("?" for _ in groups)
    matching = " AND ".join(["micro_task_run_id IS NOT NULL", *clauses])
    rows = connection.execute(f"""
WITH page_runs AS MATERIALIZED (
    SELECT * FROM ({RUN_GROUPS_SQL}) WHERE group_id IN ({placeholders})
),
page_tasks AS (
    SELECT run_id AS task_id FROM page_runs
    UNION
    SELECT routing.task_id FROM ({micro_task.ROUTING_TASKS_SQL}) routing
      JOIN page_runs ON page_runs.run_id=routing.run_id
),
history AS MATERIALIZED ({PAGE_HISTORY_SQL})
SELECT h.*, ownership.group_id, conclusion.execution_status AS conclusion_status,
       CASE WHEN {matching} THEN 1 ELSE 0 END AS summary_matching
  FROM history h JOIN page_runs ownership ON ownership.run_id=h.root_run_id
  LEFT JOIN workflow_host_conclusions conclusion ON conclusion.run_id=h.micro_task_run_id
 ORDER BY h.summary_task_rowid
""", [*groups, *values]).fetchall()
    inputs = {group: {"members": [], "executions": [], "matching": 0, "failed": set()} for group in groups}
    for row in rows:
        group = inputs[row["group_id"]]
        group["executions"].append(row)
        if row["micro_task_run_id"] is not None:
            group["members"].append(row)
            group["matching"] += row["summary_matching"]
    # A conclusion belongs by the run's stored macro_task_id (or the exact
    # standalone root), independently of the display ownership relation above.
    roots = ",".join("(?,?)" for _ in groups)
    for row in connection.execute(
        f"WITH wanted(group_id,root_id) AS (VALUES {roots}) "
        "SELECT r.macro_task_id AS group_id,c.run_id FROM workflow_host_conclusions c"
        " JOIN workflow_runs r ON r.run_id=c.run_id"
        f" WHERE c.execution_status='failed' AND r.macro_task_id IN ({placeholders})"
        " UNION SELECT wanted.group_id,c.run_id FROM workflow_host_conclusions c"
        " JOIN wanted ON wanted.root_id=c.run_id WHERE c.execution_status='failed'",
        [*(value for group in groups for value in (group, group.removeprefix("run:"))), *groups],
    ):
        inputs[row["group_id"]]["failed"].add(row["run_id"])
    return inputs


def _summary(connection, group_id: str, inputs: dict) -> dict:
    members, matching, failed_conclusions = inputs["members"], inputs["matching"], inputs["failed"]
    counts = {"roots": 0, "helpers": 0, "active": 0, "host": 0, "review": 0, "ended": 0, "accepted": 0,
              "failed": 0,
              "stoppableRoots": 0, "stoppableHelpers": 0, "delivered": 0, "awaitingHost": 0}
    from .workflow import IN_PROGRESS_STATES
    for row in members:
        if row["run_state"] in IN_PROGRESS_STATES:
            counts["stoppableHelpers" if row["child_parent_run_id"] is not None else "stoppableRoots"] += 1
        counts["delivered"] += int(row["run_state"] == "delivered")
        counts["awaitingHost"] += int(row["run_state"] == "awaiting-host")
        counts[_state(row)] += 1
        counts["accepted"] += int(row["child_parent_run_id"] is None and row["run_state"] == "accepted")
        counts["failed"] += int(row["child_parent_run_id"] is None and row["task_id"] in failed_conclusions)
        counts["helpers" if row["child_parent_run_id"] is not None else "roots"] += 1
    state = next((name for name in ("host", "active", "review") if counts[name]), "ended")
    if group_id.startswith("macro-"):
        macro_task = connection.execute("SELECT * FROM macro_tasks WHERE macro_task_id=?", (group_id,)).fetchone()
        title, title_source = macro_task["title"], "macro-task"
        group_project = project(connection, group_id)
        created_at = macro_task["created_at"]
        activity_seq, activity_at = macro_task["activity_seq"], macro_task["activity_at"]
        roots = [row["task_id"] for row in members if row["child_parent_run_id"] is None]
        kind = "macro-task"
    else:
        root_id = group_id[len("run:"):]
        root = next((row for row in members if row["task_id"] == root_id), None)
        title, title_source = _run_title(connection, root_id)
        metadata = micro_task.metadata_from_row(root) if root is not None else {"project": None}
        group_project = metadata["project"]
        created_at = root["created_at"] if root is not None else None
        run = connection.execute("SELECT activity_seq, activity_at FROM workflow_runs WHERE run_id=?", (root_id,)).fetchone()
        activity_seq, activity_at = (run["activity_seq"], run["activity_at"]) if run is not None else (0, None)
        roots = [root_id]
        kind = "standalone"
    return {
        "macroTaskId": group_id,
        "kind": kind,
        "title": title,
        "titleSource": title_source,
        "project": group_project,
        "createdAt": created_at,
        # The latest recorded event of any member; unknown history stays at sequence 0.
        "lastActivityAt": activity_at or created_at,
        "lastActivitySeq": int(activity_seq or 0),
        "state": "empty" if not members and kind == "macro-task" else state,
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
        inputs = _summary_inputs(connection, [group_id], clauses, values)[group_id]
        members = inputs["executions"]
        if not members and not group_id.startswith("macro-"):
            raise BoardError("NOT_FOUND", "The group has no readable micro tasks", macroTaskId=group_id)
        summary = {**_summary(connection, group_id, inputs), "goal": current_goal(connection, group_id)}
        runs = inputs["members"]
        matching_ids = {row["task_id"] for row in runs if row["summary_matching"]}
        ordered = _tree_order(runs)
        selected = [(row, depth) for row, depth in ordered if row["task_id"] in matching_ids]
        shown = selected[:limit]
        shown_ids = [row["task_id"] for row, _depth in shown]
        rows = [_timeline_row(connection, row, depth) for row, depth in shown]
        shown = [row for row, _depth in shown]
        spans = []
        for row in shown:
            spans.extend(_run_spans(store, connection, row))
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
    title, title_source = _run_title(connection, row["task_id"])
    fix_relation = fixes.view(connection, row["task_id"])
    if "fixes" in fix_relation:
        fixes_title, fixes_title_source = _run_title(connection, fix_relation["fixes"])
        fix_relation["fixesTitle"] = fixes_title
        fix_relation["fixesTitleSource"] = fixes_title_source
    task_text = connection.execute("SELECT json_extract(spec_json,'$.task') FROM tasks WHERE task_id=?", (row["task_id"],)).fetchone()
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
        **fix_relation,
        "runId": row["task_id"],
        "parentRunId": row["child_parent_run_id"],
        "rootRunId": row["root_run_id"],
        "title": title,
        "titleSource": title_source,
        "taskSummary": _first_line(task_text[0]) if task_text else None,
        "createdAt": row["created_at"],
        "state": row["run_state"],
        "status": row["state"],
        "conclusionStatus": row["conclusion_status"],
        "category": _state(row),
        "shutdownConfirmed": attempts == confirmed and not unproven,
        "depth": depth,
        "kind": metadata["kind"],
        "actor": metadata["actor"],
        "delegationAuthority": metadata["delegationAuthority"],
        "leadOwnership": metadata["leadOwnership"],
        "rootLeadRunId": metadata["rootLeadRunId"],
        "buddy": _full_buddy(metadata["buddy"]),
        "acceptedAt": row["accepted_at"],
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
          shutdown_confirmed: bool | None = None, uncertain: bool = False,
          expected_seconds: int | None = None, reminder_status: dict | None = None,
          inquiries: list[dict] | None = None, **extra) -> dict:
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
        "expectedSeconds": expected_seconds,
        "reminders": reminder_status,
        "inquiries": inquiries,
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


def _run_spans(store, connection, row) -> list[dict]:
    """Queue, execution and waiting-Host spans of one micro-task run, from recorded rows."""
    run_id = row["task_id"]
    spans: list[dict] = []
    attempts = connection.execute(
        "SELECT a.*, json_extract(a.result_json,'$.status') AS result_status,"
        " t.turn_id, t.turn_index, t.input_json, t.state AS turn_state,"
        " CASE WHEN t.state='returned' THEN json_extract(a.result_json,'$.result.turn.outcome.disposition') END AS disposition,"
        " t.expected_seconds AS turn_expected_seconds"
        " FROM attempts a LEFT JOIN workflow_turns t ON t.attempt_id = a.attempt_id"
        " WHERE a.task_id=? ORDER BY a.generation, a.created_at",
        (run_id,),
    ).fetchall()
    selected_id = row["active_attempt_id"] or row["selected_attempt_id"]
    inquiries_by_attempt: dict[str, list[dict]] = {}
    for question in connection.execute(
        "SELECT attempt_id, inquiry_id, created_at, state FROM messages"
        " WHERE task_id=? AND direction='question' AND attempt_id IS NOT NULL"
        " ORDER BY created_at, message_id", (run_id,),
    ):
        inquiries_by_attempt.setdefault(question["attempt_id"], []).append({
            "inquiryId": question["inquiry_id"], "createdAt": question["created_at"],
            "state": question["state"],
        })
    continuations = {
        item["attempt_id"]: item for item in connection.execute(
            "SELECT continuation_id, attempt_id, created_at FROM workflow_continuations"
            " WHERE run_id=? AND attempt_id IS NOT NULL", (run_id,)
        ).fetchall()
    }
    queued_from = row["created_at"]
    for attempt in attempts:
        receipt = json.loads(attempt["result_json"]) if attempt["result_json"] else {}
        error = receipt.get("error")
        continuation = continuations.get(attempt["attempt_id"])
        queue_start = continuation["created_at"] if continuation is not None else queued_from
        admitted = attempt["created_at"]
        if queue_start and admitted and queue_start != admitted:
            spans.append(_span("queue", f"queue:{attempt['attempt_id']}", run_id, queue_start, admitted, "admitted",
                               attempt_id=attempt["attempt_id"]))
        state, confirmed, uncertain = _attempt_state(attempt)
        buddy = turn_buddy(attempt["input_json"]) or (
            {"harness": attempt["model_harness"], "provider": attempt["model_provider"], "model": attempt["model_model"]}
            if attempt["model_harness"] else None
        )
        current = (row["state"] == "running" and attempt["attempt_id"] == selected_id
                   and attempt["execution_state"] != "finished" and attempt["result_json"] is None)
        reminder_status = reminders.status(
            connection, attempt, store._activity_latest(connection, attempt["attempt_id"])
        ) if current else None
        spans.append(_span(
            "execution", f"execution:{attempt['attempt_id']}", run_id,
            attempt["started_at"] or attempt["created_at"], attempt["finished_at"] if state == "finished" else None,
            "rejected" if attempt["turn_state"] == "rejected" else state,
            attempt_id=attempt["attempt_id"], turn_id=attempt["turn_id"], turn_index=attempt["turn_index"],
            buddy=buddy, shutdown_confirmed=confirmed, uncertain=uncertain,
            expected_seconds=attempt["turn_expected_seconds"], reminder_status=reminder_status,
            inquiries=inquiries_by_attempt.get(attempt["attempt_id"], []),
            generation=attempt["generation"], disposition=attempt["disposition"],
            resultStatus=attempt["result_status"],
            tokenUsage=(receipt.get("result") or {}).get("tokenUsage"),
            error=(str(error)[:ERROR_LIMIT] if error else None),
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
        # Waiting for admission right now: the queue interval is still open. A
        # run parked on its Host is not waiting for a Worker; its open host span
        # below is that interval, and a second open span would cover its label.
        spans.append(_span("queue", f"queue:open:{run_id}", run_id, queued_from, None, "queued"))
    for request in connection.execute(
        "SELECT w.request_id,w.kind,w.state,w.decision_id,w.created_at,w.decided_at,w.updated_at,"
        " w.reason_code,w.reason_params_json FROM workflow_requests w"
        " WHERE w.run_id=? ORDER BY w.created_at,w.request_id", (run_id,)
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
            request_id=request["request_id"], requestKind=request["kind"], decisionId=request["decision_id"],
            reason=request_reason(connection, request),
        ))
    return spans


def _routing_spans(connection, decision_task_id: str, owner_run_id: str) -> list[dict]:
    # Resolve every Router task through its immutable dispatch, including tasks
    # replaced by a later Router in the same decision.
    record = connection.execute(
        "SELECT d.decision_id, d.status, d.buddy_id, d.reason_code, d.reason_params_json, d.note,"
        " r.selected_json, r.output_json, r.requested_json, dispatch.round AS routing_round"
        f" FROM ({micro_task.ROUTING_TASKS_SQL}) routing"
        " JOIN router_dispatches dispatch ON dispatch.task_id=routing.task_id"
        " JOIN decision_requests r ON r.decision_id=routing.decision_id"
        " JOIN evaluation_decisions d ON d.decision_id=routing.decision_id"
        " WHERE routing.task_id=?", (decision_task_id,)
    ).fetchone()
    output = json.loads(record["output_json"]) if record and record["output_json"] else {}
    output = output if isinstance(output, dict) else {}
    usage = output.get("usage") if isinstance(output.get("usage"), dict) else {}
    round_facts = router_sequence.request_snapshot(connection, record["decision_id"], record["routing_round"])["facts"] if record else {}
    routing = {
        **router.routing_facts({**json.loads(record["requested_json"]),
                                **round_facts}
                               if record else {}),
        "selectedBuddy": json.loads(record["selected_json"]) if record and record["selected_json"] else None,
        "reason": reason_from_json(record["reason_code"], record["reason_params_json"]) if record else None,
        "note": record["note"] if record else None,
        "policyCheck": output.get("policyCheck"),
        "usage": {key: usage.get(key) for key in ("elapsedMs", "toolCalls")},
    }
    abstention = bool(record and record["reason_code"] == "router-abstained")
    spans = []
    for attempt in connection.execute(
        "SELECT attempt_id, execution_state, shutdown_confirmed, ownership, started_at, finished_at, created_at,"
        " json_extract(result_json,'$.status') AS result_status, result_json"
        " FROM attempts WHERE task_id=? ORDER BY generation", (decision_task_id,)
    ).fetchall():
        state, confirmed, uncertain = _attempt_state(attempt)
        receipt = json.loads(attempt["result_json"]) if attempt["result_json"] else {}
        attempt_output = receipt.get("result") or {}
        error = receipt.get("error")
        span_routing = routing
        if isinstance(attempt_output, dict) and 'routingMode' in attempt_output:
            span_routing = {**routing, **router.routing_facts(attempt_output, include_budget=False)}
            if attempt_output.get('code') == 'router-review-unavailable':
                span_routing.update(selectedBuddy=None,
                                    policyCheck=None, usage=None)
        spans.append(_span(
            "routing", f"routing:{attempt['attempt_id']}", owner_run_id,
            attempt["started_at"] or attempt["created_at"], attempt["finished_at"] if state == "finished" and not uncertain else None,
            state, attempt_id=attempt["attempt_id"], shutdown_confirmed=confirmed, uncertain=uncertain,
            decisionTaskId=decision_task_id, resultStatus=attempt["result_status"],
            routing=span_routing,
            tokenUsage=attempt_output.get("tokenUsage"),
            disposition=("abstention" if abstention and attempt["result_status"] != "cancelled"
                         else "routing-failed" if record and record["status"] in ("failed", "needs-host") and output.get("code")
                         else None),
            error=(str(error)[:ERROR_LIMIT] if error else None),
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
        payload = json.loads(row["payload_json"])
        kind = HOST_MARKERS[row["kind"]]
        if kind == "accept" and payload.get("verdict") == "rejected":
            kind = "reject"

        actor = ({"role": row["actor_role"],
                  **({"id": row["actor_id"]} if row["actor_role"] != "host" and row["actor_id"] else {})}
                 if row["actor_role"] else None)
        markers.append({
            "seq": row["seq"],
            "runId": row["task_id"],
            "kind": kind,
            "at": row["created_at"],
            **{key: payload.get(key) for key in ("decision", "verdict", "state", "strategy", "note")},
            "actor": actor,
            "attemptId": row["attempt_id"],
            "requestId": payload.get("requestId"),
            "artifactId": payload.get("artifactId"),
            "integrationId": payload.get("integrationId"),
            "continuationId": payload.get("continuationId"),
            "stopInitiator": payload.get("stopInitiator"),
            "eventKind": row["kind"],
        })
    return markers
