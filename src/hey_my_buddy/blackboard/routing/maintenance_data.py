"""Bounded maintenance evidence and validated domain/summary writes."""
from __future__ import annotations

from collections import Counter, defaultdict
import json

from ...buddy.roles.structured_call import schema_errors
from ...protocol.turn_input import turn_buddy
from .evidence import domain_statistics, round_classification


TASK_TYPES = {
    "create": "Build a new capability or artifact.",
    "change": "Change an existing capability or artifact.",
    "debug": "Find and repair a defect.",
    "verify": "Check behavior or evidence against a requirement.",
    "review": "Assess work and identify defects or risks.",
    "research": "Investigate a question and synthesize findings.",
    "write": "Write or revise explanatory content.",
}
TERMINAL_STATES = ("accepted", "concluded", "cancelled", "failed")
PENDING_WHERE = (
    "r.state IN ('accepted','concluded','cancelled','failed')"
    " AND NOT EXISTS (SELECT 1 FROM micro_task_classifications x WHERE x.run_id=r.run_id)"
)


def _kind(value):
    return (value["harness"], value["model"], value["effort"])


def _node_rows(connection):
    return {row["node_id"]: dict(row) for row in connection.execute(
        "SELECT node_id,name,definition,parent_id,merged_into_id FROM domain_nodes")}


def pending(connection) -> bool:
    """Whether a settled micro task still needs classification, without building a packet."""
    return connection.execute(
        "SELECT 1 FROM workflow_runs r WHERE " + PENDING_WHERE + " LIMIT 1").fetchone() is not None


def build_packet(connection) -> dict | None:
    """Read the oldest 50 ended, unclassified micro tasks and their relevant facts."""
    runs = list(connection.execute(
        "SELECT r.run_id,r.title,r.state,r.created_at,r.updated_at,r.continuation_count,"
        " t.spec_json,m.project_id,m.project_path,c.note AS conclusion_note,"
        " c.reason_code AS conclusion_reason,c.execution_status,"
        " (SELECT i.verification_json FROM workflow_integrations i"
        " WHERE i.run_id=r.run_id AND i.artifact_id=r.final_artifact_id"
        " ORDER BY i.created_at DESC,i.integration_id DESC LIMIT 1) AS integration_json"
        " FROM workflow_runs r JOIN tasks t ON t.task_id=r.run_id"
        " LEFT JOIN macro_tasks m ON m.macro_task_id=r.macro_task_id"
        " LEFT JOIN workflow_host_conclusions c ON c.run_id=r.run_id"
        " WHERE " + PENDING_WHERE +
        " ORDER BY r.updated_at,r.run_id LIMIT 50"))
    if not runs:
        return None
    run_ids = [row["run_id"] for row in runs]
    placeholders = ",".join("?" for _ in run_ids)
    turns = defaultdict(list)
    kinds = {}
    for row in connection.execute(
        "SELECT w.run_id,w.turn_index,w.state,w.input_json,a.started_at,a.finished_at,"
        " a.result_json FROM workflow_turns w"
        " LEFT JOIN attempts a ON a.attempt_id=w.attempt_id"
        f" WHERE w.run_id IN ({placeholders}) ORDER BY w.run_id,w.turn_index", run_ids):
        buddy = turn_buddy(row["input_json"])
        kind = {key: buddy[key] for key in ("harness", "model", "effort")}
        kinds[_kind(kind)] = kind
        receipt = json.loads(row["result_json"]) if row["result_json"] else None
        result = (receipt or {}).get("result") or {}
        turns[row["run_id"]].append({
            "turnIndex": row["turn_index"], "state": row["state"], "kind": kind,
            "outcome": (result.get("turn") or {}).get("outcome"),
            "failureCategory": round_classification(receipt) if row["finished_at"] else None,
            "terminationReason": (receipt or {}).get("terminationReason"),
            "startedAt": row["started_at"], "finishedAt": row["finished_at"],
            "usage": result.get("tokenUsage"),
        })
    boundaries = defaultdict(int)
    for row in connection.execute(
        "SELECT run_id,COUNT(*) AS total FROM workflow_continuations"
        f" WHERE run_id IN ({placeholders}) AND kind='manual' GROUP BY run_id", run_ids):
        boundaries[row["run_id"]] = row["total"]
    micro_tasks = []
    for row in runs:
        spec = json.loads(row["spec_json"])
        micro_tasks.append({
            "runId": row["run_id"], "title": row["title"], "task": spec.get("task"),
            "project": {"id": row["project_id"], "path": row["project_path"] or spec.get("cwd")},
            "result": {"state": row["state"], "conclusion": row["conclusion_note"],
                       "conclusionReason": row["conclusion_reason"],
                       "executionStatus": row["execution_status"],
                       "continuationRounds": row["continuation_count"],
                       "hostBoundaries": boundaries[row["run_id"]],
                       "integration": json.loads(row["integration_json"]) if row["integration_json"] else None,
                       "createdAt": row["created_at"], "finishedAt": row["updated_at"],
                       "turns": turns[row["run_id"]]},
        })
    summaries = {}
    for row in connection.execute("SELECT harness,model,effort,text FROM kind_summaries"):
        key = _kind(row)
        if key in kinds:
            summaries[key] = {"text": row["text"], "citations": []}
    for row in connection.execute("SELECT harness,model,effort,run_id FROM kind_summary_citations"):
        key = _kind(row)
        if key in summaries:
            summaries[key]["citations"].append(row["run_id"])
    buddy_ids = {key: str(index) for index, key in enumerate(kinds)}
    buddies = [{"buddy_id": buddy_ids[key], "provider": "", **kind}
               for key, kind in kinds.items()]
    statistics = domain_statistics(connection, buddies)
    return {
        "microTasks": micro_tasks,
        "domainNodes": list(_node_rows(connection).values()),
        "taskTypes": [{"id": key, "definition": value} for key, value in TASK_TYPES.items()],
        "kinds": [{**kind, "summary": summaries.get(key),
                   "statistics": statistics[buddy_ids[key]]}
                  for key, kind in kinds.items()],
    }


def _string(max_length):
    return {"type": "string", "minLength": 1, "maxLength": max_length}


def _object(properties):
    return {"type": "object", "properties": properties,
            "required": list(properties), "additionalProperties": False}


def answer_schema(packet) -> dict:
    """Use only the JSON Schema keywords supported by structured_call."""
    ids = [item["runId"] for item in packet["microTasks"]]
    existing = [node["node_id"] for node in packet["domainNodes"]]
    kind_schema = {key: {"type": "string", "enum": sorted({kind[key] for kind in packet["kinds"]})}
                   for key in ("harness", "model", "effort")}
    return _object({
        "newNodes": {"type": "array", "items": _object({
            "id": _string(128), "name": _string(120), "definition": _string(400),
            "parentId": {"type": ["string", "null"]}})},
        "merges": {"type": "array", "items": _object({
            "sourceId": _string(128), "targetId": _string(128)})},
        "classifications": {"type": "array", "items": _object({
            "runId": {"type": "string", "enum": ids},
            "domainNodeId": _string(128),
            "taskType": {"type": "string", "enum": list(TASK_TYPES)}})},
        "summaries": {"type": "array", "items": _object({
            **kind_schema, "text": _string(4000),
            "citations": {"type": "array", "items": {"type": "string"}}})},
    })


def _shape_errors(value, schema, path="answer"):
    """Retain a field path for each supported Schema violation."""
    direct = {key: part for key, part in schema.items() if key not in ("properties", "items")}
    if "properties" in schema:
        direct["properties"] = {key: {} for key in schema["properties"]}
    errors = [f"{path}: {keyword}" for keyword in schema_errors(value, direct)]
    if isinstance(value, dict):
        for key, child in schema.get("properties", {}).items():
            if key in value:
                errors.extend(_shape_errors(value[key], child, f"{path}.{key}"))
    if isinstance(value, list) and "items" in schema:
        for index, child in enumerate(value):
            errors.extend(_shape_errors(child, schema["items"], f"{path}[{index}]"))
    return errors


def _check_tree(nodes):
    active_names = set()
    for node in nodes.values():
        if node["merged_into_id"] is not None:
            target = nodes.get(node["merged_into_id"])
            if target is None or target["merged_into_id"] is not None:
                raise ValueError("merge target must be an active node")
            continue
        if node["name"] in active_names:
            raise ValueError(f"active domain name already exists: {node['name']}")
        active_names.add(node["name"])
        seen = set()
        cursor = node["node_id"]
        while cursor is not None:
            if cursor in seen:
                raise ValueError(f"domain parent cycle at {cursor}")
            seen.add(cursor)
            parent = nodes[cursor]["parent_id"]
            if parent is not None and (parent not in nodes or nodes[parent]["merged_into_id"] is not None):
                raise ValueError(f"parent must be an active node: {parent}")
            cursor = parent


def rename_domain(connection, node_id, name, definition):
    if not isinstance(name, str) or not name.strip() or len(name) > 120:
        raise ValueError("domain name must have 1 to 120 characters")
    if not isinstance(definition, str) or not definition.strip() or len(definition) > 400:
        raise ValueError("domain definition must have 1 to 400 characters")
    nodes = _node_rows(connection)
    if node_id not in nodes or nodes[node_id]["merged_into_id"] is not None:
        raise ValueError(f"domain node is not active: {node_id}")
    nodes[node_id].update(name=name, definition=definition)
    _check_tree(nodes)
    connection.execute("UPDATE domain_nodes SET name=?,definition=? WHERE node_id=?",
                       (name, definition, node_id))


def merge_domain(connection, source_id, target_id):
    nodes = _node_rows(connection)
    if source_id not in nodes or nodes[source_id]["merged_into_id"] is not None:
        raise ValueError(f"merge source is not active: {source_id}")
    if target_id not in nodes or nodes[target_id]["merged_into_id"] is not None:
        raise ValueError(f"merge target is not active: {target_id}")
    if source_id == target_id:
        raise ValueError("merge source and target are the same")
    # A target below its source would cycle when the source's children move.
    cursor = target_id
    while cursor is not None:
        if cursor == source_id:
            raise ValueError("merge target is a descendant of source")
        cursor = nodes[cursor]["parent_id"]
    for node in nodes.values():
        if node["parent_id"] == source_id:
            node["parent_id"] = target_id
        if node["merged_into_id"] == source_id:
            node["merged_into_id"] = target_id
    nodes[source_id]["merged_into_id"] = target_id
    nodes[source_id]["parent_id"] = None
    _check_tree(nodes)
    connection.execute("UPDATE domain_nodes SET parent_id=? WHERE parent_id=?", (target_id, source_id))
    connection.execute("UPDATE domain_nodes SET merged_into_id=? WHERE merged_into_id=?", (target_id, source_id))
    connection.execute("UPDATE domain_nodes SET parent_id=NULL,merged_into_id=? WHERE node_id=?",
                       (target_id, source_id))


def apply_answer(connection, *, packet, answer, maintenance_run_id) -> dict:
    """Accept independently valid items and report precise repairable errors."""
    errors = []
    counts = {"nodes": 0, "merges": 0, "classifications": 0, "summaries": 0}
    shape = _shape_errors(answer, answer_schema(packet))
    if shape:
        return {"errors": shape, "written": counts}
    batch = {item["runId"] for item in packet["microTasks"]}
    packet_kinds = {_kind(kind): kind for kind in packet["kinds"]}
    nodes = _node_rows(connection)
    new_counts = Counter(item["id"] for item in answer["newNodes"])
    for node_id, count in new_counts.items():
        if count > 1:
            errors.append(f"newNodes[{node_id}]: duplicate id")
    new = {item["id"]: item for item in answer["newNodes"] if new_counts[item["id"]] == 1}
    pending = dict(new)
    while pending:
        progressed = False
        for node_id, item in list(pending.items()):
            parent = item["parentId"]
            if parent in pending:
                continue
            del pending[node_id]
            progressed = True
            if node_id in nodes:
                # A previously accepted node from this run survives correction.
                owner = connection.execute("SELECT maintenance_run_id FROM domain_nodes WHERE node_id=?",
                                           (node_id,)).fetchone()[0]
                if owner != maintenance_run_id or nodes[node_id]["name"] != item["name"] or nodes[node_id]["definition"] != item["definition"]:
                    errors.append(f"newNodes[{node_id}]: existing node cannot be changed")
                continue
            if parent is not None and (parent not in nodes or nodes[parent]["merged_into_id"] is not None):
                errors.append(f"newNodes[{node_id}]: parent is not active: {parent}")
                continue
            candidate = {"node_id": node_id, "name": item["name"], "definition": item["definition"],
                         "parent_id": parent, "merged_into_id": None}
            try:
                _check_tree({**nodes, node_id: candidate})
            except ValueError as exc:
                errors.append(f"newNodes[{node_id}]: {exc}")
                continue
            connection.execute("INSERT INTO domain_nodes VALUES(?,?,?,?,NULL,?)",
                               (node_id, item["name"], item["definition"], parent, maintenance_run_id))
            nodes[node_id] = candidate
            counts["nodes"] += 1
        if not progressed:
            errors.extend(f"newNodes[{node_id}]: parent cycle" for node_id in pending)
            break
    merge_counts = Counter(item["sourceId"] for item in answer["merges"])
    seen_merges = set()
    for item in answer["merges"]:
        source, target = item["sourceId"], item["targetId"]
        if merge_counts[source] > 1:
            if source not in seen_merges:
                errors.append(f"merges[{source}]: duplicate source")
            seen_merges.add(source)
            continue
        if source in seen_merges:
            errors.append(f"merges[{source}]: duplicate source")
            continue
        seen_merges.add(source)
        current_nodes = _node_rows(connection)
        resolved = target
        while resolved in current_nodes and current_nodes[resolved]["merged_into_id"] is not None:
            resolved = current_nodes[resolved]["merged_into_id"]
        if source in current_nodes and current_nodes[source]["merged_into_id"] == resolved:
            continue
        try:
            merge_domain(connection, source, target)
            counts["merges"] += 1
        except ValueError as exc:
            errors.append(f"merges[{source}]: {exc}")
    nodes = _node_rows(connection)
    classification_counts = Counter(item["runId"] for item in answer["classifications"])
    classified = set()
    for item in answer["classifications"]:
        run_id, node_id = item["runId"], item["domainNodeId"]
        if classification_counts[run_id] > 1:
            if run_id not in classified:
                errors.append(f"classifications[{run_id}]: duplicate runId")
            classified.add(run_id)
            continue
        if run_id in classified:
            errors.append(f"classifications[{run_id}]: duplicate runId")
            continue
        classified.add(run_id)
        prior = connection.execute("SELECT 1 FROM micro_task_classifications WHERE run_id=?", (run_id,)).fetchone()
        if prior:
            continue
        if node_id not in nodes or nodes[node_id]["merged_into_id"] is not None:
            errors.append(f"classifications[{run_id}]: domain node is not active: {node_id}")
            continue
        connection.execute("INSERT INTO micro_task_classifications VALUES(?,?,?,?)",
                           (run_id, node_id, item["taskType"], maintenance_run_id))
        counts["classifications"] += 1
    for run_id in batch - classified:
        if not connection.execute("SELECT 1 FROM micro_task_classifications WHERE run_id=?", (run_id,)).fetchone():
            errors.append(f"classifications[{run_id}]: missing classification")
    summary_counts = Counter(_kind(item) for item in answer["summaries"])
    seen_kinds = set()
    for item in answer["summaries"]:
        key = _kind(item)
        if summary_counts[key] > 1:
            if key not in seen_kinds:
                errors.append(f"summaries[{key}]: duplicate kind")
            seen_kinds.add(key)
            continue
        if key in seen_kinds:
            errors.append(f"summaries[{key}]: duplicate kind")
            continue
        seen_kinds.add(key)
        if key not in packet_kinds:
            errors.append(f"summaries[{key}]: kind is not in packet")
            continue
        previous = connection.execute(
            "SELECT maintenance_run_id FROM kind_summaries WHERE harness=? AND model=? AND effort=?", key).fetchone()
        if previous and previous["maintenance_run_id"] == maintenance_run_id:
            continue
        original = set((packet_kinds[key].get("summary") or {}).get("citations") or ())
        cited = item["citations"]
        if len(cited) != len(set(cited)) or not set(cited) <= batch | original:
            errors.append(f"summaries[{key}]: citations must be unique and from batch or original summary")
            continue
        connection.execute(
            "INSERT INTO kind_summaries(harness,model,effort,text,maintenance_run_id) VALUES(?,?,?,?,?)"
            " ON CONFLICT(harness,model,effort) DO UPDATE SET text=excluded.text,"
            " maintenance_run_id=excluded.maintenance_run_id", (*key, item["text"], maintenance_run_id))
        connection.execute("DELETE FROM kind_summary_citations WHERE harness=? AND model=? AND effort=?", key)
        connection.executemany("INSERT INTO kind_summary_citations VALUES(?,?,?,?)",
                               [(*key, run_id) for run_id in cited])
        counts["summaries"] += 1
    return {"errors": errors, "written": counts}
