"""Read-only delegation provenance and keyset history for task reads.

This module is a pure derivation over existing authoritative facts. It owns no
state, writes nothing and adds no lifecycle: the C-Two ``task_list``,
``console_snapshot`` and task views all read the same relation below, and the
ordinary governed workflow keeps ``workflow.task_extension``.

Everything here is grounded in durable rows:

* kind and lineage come from ``workflow_runs`` plus ``workflow_children`` (helpers)
  and ``decision_requests``/``workflow_routes`` (internal routing decisions);
* the original delegating Host comes from that run's first explicit admission
  source, its generation-1 takeover evidence, or its still-current generation-1
  row. Helpers use their own creation evidence, and routing decisions use their
  owning run's evidence. Missing historical evidence stays ``null``; owner/actor
  labels and a helper's root Host are never substitutes for its original source;
* the current Host is the root of the task's delegation tree, because a takeover
  fences the whole owned graph rather than one row;
* the project is the source Goal's immutable original ``cwd`` and the saved
  repository identity (pinned input manifest, then the run's manifest, then the
  checkout reservation); a temporary execution worktree is never the project.

``t.*`` is carried through the relation, so a resolved row is also a valid task row
for :meth:`buddy.store.BoardStore._decorate`.
"""
from __future__ import annotations

import base64
import binascii
import json
import os
import re
import sqlite3
from typing import Any, Sequence

from .errors import BoardError

#: Task kinds derived from durable relations, never from a label.
DELEGATION_KINDS = ("goal", "helper", "decision", "execution")

#: The four fields of a complete resolved execution configuration.
CONFIGURATION_FIELDS = ("adapter", "provider", "model", "effort")

#: Server-side task history filters. ``all`` adds no predicate.
HISTORY_FILTERS = ("all", "active", "host", "review")

QUERY_MAX_LENGTH = 200
# A task without a recorded repository identity uses its original absolute cwd.
PROJECT_ID_MAX_LENGTH = 4096
HOST_ID_MAX_LENGTH = 256
CURSOR_MAX_LENGTH = 512
CURSOR_VERSION = 1

TASK_ID_MAX_LENGTH = 128

_TASK_ID_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{1,%d}$" % TASK_ID_MAX_LENGTH)
_CURSOR_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,%d}$" % CURSOR_MAX_LENGTH)
_TIMESTAMP_PATTERN = re.compile(r"^[0-9A-Za-z:.+-]{1,64}$")

#: Columns matched by ``query``. The original project path and both Host
#: attributions are part of the contract; the execution adapter/model is the
#: resolved execution configuration with the task's effective adapter as fallback.
QUERY_COLUMNS = (
    "task_id",
    "objective",
    "project_path",
    "source_host_id",
    "current_host_id",
    "effective_adapter",
    "effective_model",
)

#: ``filter`` semantics, applied in SQL before any LIMIT:
FILTER_SQL = {
    "all": None,
    # Work that is still in flight: an open task, or a governed run between turns.
    "active": (
        "(state IN ('queued','running','cancelling','reconciliation-needed')"
        " OR run_state IN ('executing','awaiting-host','waiting-helpers'))"
    ),
    # The Host's own decision is the current boundary.
    "host": "(run_state = 'awaiting-host')",
    # A terminal result that is still unaccepted: the same condition the console's
    # review gate uses (delivered run or plain task, durable result, confirmed stop).
    # Governed review_ready also proves stop for every owned child and router.
    "review": (
        "(accepted_at IS NULL AND state IN ('completed','failed','cancelled')"
        " AND decision_id IS NULL AND adapter <> 'decision'"
        " AND (governed_run_id IS NULL OR run_state = 'delivered') AND review_ready = 1)"
    ),
}

#: One row per task: the full task row plus its derived delegation facts.
#:
#: ``roots`` walks ``workflow_children`` upward; the terminal row of a lineage is the
#: root of that delegation tree. A routing decision points at its owning run through
#: ``workflow_routes``, and that run's root is used for the decision's project and
#: Host, so a decision is never misattributed to its own private decision directory.
RELATION_SQL = """
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
),
source_hosts AS (
    SELECT source.run_id,
           COALESCE(
               (SELECT CASE event.kind
                           WHEN 'task.submitted' THEN json_extract(event.payload_json, '$.governed.sourceHostId')
                           ELSE json_extract(event.payload_json, '$.sourceHostId')
                       END
                  FROM events event
                 WHERE event.task_id = source.run_id AND (
                     (event.kind = 'task.submitted'
                      AND json_type(event.payload_json, '$.governed.sourceHostId') = 'text'
                      AND length(json_extract(event.payload_json, '$.governed.sourceHostId')) > 0)
                     OR (event.kind = 'workflow.helper_admitted'
                         AND json_type(event.payload_json, '$.sourceHostId') = 'text'
                         AND length(json_extract(event.payload_json, '$.sourceHostId')) > 0)
                 ) ORDER BY event.seq LIMIT 1),
               (SELECT json_extract(event.payload_json, '$.previousHostId') FROM events event
                 WHERE event.task_id = source.run_id AND event.kind = 'workflow.takeover'
                   AND json_type(event.payload_json, '$.previousOwnerGeneration') = 'integer'
                   AND json_extract(event.payload_json, '$.previousOwnerGeneration') = 1
                   AND json_type(event.payload_json, '$.previousHostId') = 'text'
                   AND length(json_extract(event.payload_json, '$.previousHostId')) > 0
                 ORDER BY event.seq LIMIT 1),
               CASE WHEN source.owner_generation = 1 THEN source.host_id END
           ) AS source_host_id
      FROM workflow_runs source
),
owned_runs(owner_run_id, run_id) AS (
    SELECT run_id, run_id FROM workflow_runs
    UNION
    SELECT lineage.root_run_id, lineage.task_id FROM lineage
),
owned_executions(owner_run_id, task_id) AS (
    SELECT owner_run_id, run_id FROM owned_runs
    UNION
    SELECT owned.owner_run_id, decision.task_id FROM owned_runs owned
      JOIN workflow_routes route ON route.run_id = owned.run_id
      JOIN decision_requests decision ON decision.decision_id = route.decision_id
     WHERE decision.task_id IS NOT NULL
),
unconfirmed_stops AS (
    -- Equivalent to WorkflowCoordinator._stop_proven: all attempts must be
    -- finished and confirmed; dangling attempt identities and never-observed
    -- active tasks cannot serve as termination evidence.
    SELECT task.task_id FROM tasks task
     WHERE EXISTS (SELECT 1 FROM attempts attempt WHERE attempt.task_id = task.task_id
                    AND (attempt.execution_state <> 'finished' OR attempt.shutdown_confirmed <> 1))
        OR (task.active_attempt_id IS NOT NULL AND NOT EXISTS (
            SELECT 1 FROM attempts attempt WHERE attempt.task_id = task.task_id
              AND attempt.attempt_id = task.active_attempt_id))
        OR (task.selected_attempt_id IS NOT NULL AND NOT EXISTS (
            SELECT 1 FROM attempts attempt WHERE attempt.task_id = task.task_id
              AND attempt.attempt_id = task.selected_attempt_id))
        OR (task.state IN ('running','cancelling','reconciliation-needed') AND NOT EXISTS (
            SELECT 1 FROM attempts attempt WHERE attempt.task_id = task.task_id))
)
SELECT
    t.*,
    r.run_id AS governed_run_id,
    r.state AS run_state,
    r.execution_configuration_json AS run_configuration_json,
    c.parent_run_id AS child_parent_run_id,
    COALESCE(c.parent_run_id, wr.run_id) AS delegated_parent_run_id,
    dr.decision_id AS decision_id,
    wr.run_id AS route_run_id,
    COALESCE(rt.root_run_id, r.run_id, wr_roots.root_run_id, wr.run_id) AS root_run_id,
    root_run.host_id AS root_host_id,
    root_run.owner_generation AS root_owner_generation,
    source_run.source_host_id AS source_host_id,
    root_run.host_id AS current_host_id,
    CASE WHEN (dr.decision_id IS NOT NULL OR t.adapter = 'decision') AND root_run.run_id IS NULL
         THEN NULL
         ELSE COALESCE(json_extract(root_task.spec_json, '$.cwd'), json_extract(t.spec_json, '$.cwd')) END AS project_path,
    CASE WHEN (dr.decision_id IS NOT NULL OR t.adapter = 'decision') AND root_run.run_id IS NULL
         THEN 'internal-decisions'
         ELSE COALESCE(
        (SELECT json_extract(artifact.manifest_json, '$.repositoryId') FROM workflow_artifacts artifact
          WHERE artifact.run_id = root_run.run_id AND artifact.kind = 'input'
            AND json_extract(artifact.manifest_json, '$.repositoryId') IS NOT NULL
          ORDER BY artifact.rowid LIMIT 1),
        json_extract(root_run.workspace_manifest_json, '$.repositoryId'),
        (SELECT reservation.repository_id FROM workspace_reservations reservation
          WHERE reservation.holder_task_id = root_run.run_id
            AND reservation.repository_id IS NOT NULL
          ORDER BY reservation.rowid LIMIT 1),
        json_extract(root_task.spec_json, '$.cwd'),
        json_extract(t.spec_json, '$.cwd')
    ) END AS project_id,
    COALESCE(json_extract(r.goal_json, '$.task'), json_extract(t.spec_json, '$.task')) AS objective,
    COALESCE(json_extract(r.execution_configuration_json, '$.adapter'), t.adapter) AS effective_adapter,
    COALESCE(json_extract(r.execution_configuration_json, '$.model'), json_extract(t.spec_json, '$.model'))
        AS effective_model,
    EXISTS(
        SELECT 1 FROM attempts attempt
         WHERE attempt.task_id = t.task_id
           AND attempt.attempt_id = COALESCE(t.active_attempt_id, t.selected_attempt_id)
           AND attempt.result_json IS NOT NULL
           AND attempt.execution_state = 'finished'
           AND attempt.shutdown_confirmed = 1
    ) AND NOT EXISTS (
        SELECT 1 FROM owned_executions owned
          JOIN unconfirmed_stops pending ON pending.task_id = owned.task_id
         WHERE owned.owner_run_id = r.run_id
    ) AS review_ready
FROM tasks t
LEFT JOIN workflow_runs r ON r.run_id = t.task_id
LEFT JOIN workflow_children c ON c.child_task_id = t.task_id
LEFT JOIN roots rt ON rt.task_id = t.task_id
LEFT JOIN (
    SELECT task_id, MIN(decision_id) AS decision_id FROM decision_requests
     WHERE task_id IS NOT NULL
     GROUP BY task_id
) dr ON dr.task_id = t.task_id
LEFT JOIN workflow_routes wr ON wr.decision_id = dr.decision_id
LEFT JOIN roots wr_roots ON wr_roots.task_id = wr.run_id
LEFT JOIN source_hosts source_run ON source_run.run_id = COALESCE(r.run_id, wr.run_id)
LEFT JOIN tasks root_task ON root_task.task_id = COALESCE(rt.root_run_id, r.run_id, wr_roots.root_run_id, wr.run_id)
LEFT JOIN workflow_runs root_run ON root_run.run_id = COALESCE(rt.root_run_id, r.run_id, wr_roots.root_run_id, wr.run_id)
"""

#: The relation wrapped as a table expression: ``SELECT * FROM (task_history) AS resolved``.
TASK_HISTORY_SQL = f"SELECT * FROM ({RELATION_SQL}) AS resolved"


def _complete_configuration(value: Any) -> dict | None:
    """The four-field execution configuration, or ``None`` when it is not complete."""
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return None
    if not isinstance(value, dict):
        return None
    configuration = {field: value.get(field) for field in CONFIGURATION_FIELDS}
    if any(not isinstance(item, str) or not item for item in configuration.values()):
        return None
    return configuration


def project_label(path: str | None, fallback: str) -> str:
    """Display label for a project path; never a fabricated identity."""
    if not path:
        return fallback
    name = os.path.basename(path.rstrip("/"))
    return name or path


def metadata_from_row(row: sqlite3.Row) -> dict:
    """Derive one task's delegation metadata from a resolved relation row."""
    governed = row["governed_run_id"] is not None
    if governed:
        kind = "helper" if row["child_parent_run_id"] else "goal"
    elif row["decision_id"] is not None or row["adapter"] == "decision":
        kind = "decision"
    else:
        kind = "execution"
    path = row["project_path"] if isinstance(row["project_path"], str) and row["project_path"] else None
    identifier = row["project_id"] if isinstance(row["project_id"], str) and row["project_id"] else None
    source_host = row["source_host_id"] if isinstance(row["source_host_id"], str) and row["source_host_id"] else None
    current_host = row["current_host_id"] if isinstance(row["current_host_id"], str) and row["current_host_id"] else None
    parent_run = row["delegated_parent_run_id"] if isinstance(row["delegated_parent_run_id"], str) else None
    root_run = row["root_run_id"] if isinstance(row["root_run_id"], str) else None
    configuration = _complete_configuration(row["run_configuration_json"] if governed else row["spec_json"])
    return {
        "kind": kind,
        "sourceHostId": source_host if root_run else None,
        "currentHostId": current_host if root_run else None,
        "parentRunId": parent_run,
        "rootRunId": root_run,
        "project": {
            "id": identifier or path or row["task_id"],
            "path": path,
            "label": project_label(path, "内部决策" if kind == "decision" and root_run is None else "未记录项目"),
        },
        "configuration": configuration,
    }


def resolve(connection: sqlite3.Connection, task: sqlite3.Row) -> dict:
    """Delegation metadata for one task row, read from the authoritative relations."""
    row = connection.execute(
        f"{TASK_HISTORY_SQL} WHERE task_id = ?", (task["task_id"],)
    ).fetchone()
    if row is None:
        raise BoardError("NOT_FOUND", "The task row no longer resolves to a readable history record")
    return metadata_from_row(row)


def resolve_many(connection: sqlite3.Connection, task_ids: Sequence[str]) -> dict[str, dict]:
    """Delegation metadata for a bounded set of task ids, in one relation query."""
    identifiers = [task_id for task_id in dict.fromkeys(task_ids) if isinstance(task_id, str) and task_id]
    if not identifiers:
        return {}
    placeholders = ",".join("?" for _ in identifiers)
    rows = connection.execute(
        f"{TASK_HISTORY_SQL} WHERE task_id IN ({placeholders})", identifiers
    ).fetchall()
    return {row["task_id"]: metadata_from_row(row) for row in rows}


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def history_where(
    *,
    roots_only: bool = False,
    project_id: str | None = None,
    host_id: str | None = None,
    query: str | None = None,
    filter_name: str = "all",
) -> tuple[list[str], list[Any]]:
    """SQL predicates and values for the history filters, applied before any LIMIT."""
    clauses: list[str] = []
    values: list[Any] = []
    if roots_only:
        # Main governed goals only: helpers (a workflow child) and internal decision
        # tasks (no governed run) are excluded.
        clauses.append("governed_run_id IS NOT NULL AND child_parent_run_id IS NULL")
    if project_id:
        clauses.append("project_id = ?")
        values.append(project_id)
    if host_id:
        clauses.append("(source_host_id = ? OR current_host_id = ?)")
        values.extend([host_id, host_id])
    if query:
        pattern = f"%{_escape_like(query)}%"
        clauses.append("(" + " OR ".join(f"{column} LIKE ? ESCAPE '\\'" for column in QUERY_COLUMNS) + ")")
        values.extend([pattern] * len(QUERY_COLUMNS))
    clause = FILTER_SQL.get(filter_name)
    if clause:
        clauses.append(clause)
    return clauses, values


def _invalid_cursor() -> BoardError:
    return BoardError(
        "INVALID_ARGUMENT",
        "before must be the opaque nextCursor returned by task_list; use a fresh read to recover",
    )


def encode_cursor(created_at: str, task_id: str) -> str:
    """Opaque keyset cursor for ``(created_at DESC, task_id DESC)`` paging."""
    payload = json.dumps(
        {"createdAt": created_at, "taskId": task_id, "v": CURSOR_VERSION},
        separators=(",", ":"),
        sort_keys=True,
    )
    return base64.urlsafe_b64encode(payload.encode("utf-8")).decode("ascii").rstrip("=")


def decode_cursor(value: str) -> tuple[str, str]:
    """Validate and decode one opaque cursor; malformed input is an argument error."""
    if not isinstance(value, str) or not _CURSOR_PATTERN.match(value):
        raise _invalid_cursor()
    try:
        raw = base64.b64decode(value + "=" * (-len(value) % 4), altchars=b"-_", validate=True)
        payload = json.loads(raw.decode("utf-8"))
    except (binascii.Error, ValueError, UnicodeDecodeError):
        raise _invalid_cursor() from None
    if not isinstance(payload, dict) or set(payload) != {"v", "createdAt", "taskId"}:
        raise _invalid_cursor()
    if payload["v"] != CURSOR_VERSION or isinstance(payload["v"], bool):
        raise _invalid_cursor()
    created_at, task_id = payload["createdAt"], payload["taskId"]
    if not isinstance(created_at, str) or not _TIMESTAMP_PATTERN.match(created_at) or not any(
        character.isdigit() for character in created_at
    ):
        raise _invalid_cursor()
    if not isinstance(task_id, str) or not _TASK_ID_PATTERN.match(task_id):
        raise _invalid_cursor()
    return created_at, task_id


def keyset_where(created_at: str, task_id: str) -> tuple[str, list[str]]:
    """The strict keyset predicate for the next page in ``DESC`` order.

    A row inserted after the cursor was issued is newer than the cursor and can
    never shift, duplicate or skip the remaining rows; equal ``created_at`` values
    are ordered by ``task_id``.
    """
    return "(created_at < ? OR (created_at = ? AND task_id < ?))", [created_at, created_at, task_id]
