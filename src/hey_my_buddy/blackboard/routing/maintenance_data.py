"""Bounded maintenance evidence and validated domain/summary writes."""
from __future__ import annotations

from collections import Counter, defaultdict
import json

from ...protocol.structured_schema import schema_errors
from ...protocol.task_types import TASK_TYPES
from ...errors import BoardError
from ...protocol.turn_input import turn_buddy
from .evidence import kind_domain_statistics
from .statistics import NEITHER, PROVIDER_REJECTIONS, accumulate_cell, round_classification



_ENDED_TURNS = ("returned", "failed", "rejected", "interrupted")


def _sql_values(values):
    return ",".join("'" + value.replace("'", "''") + "'" for value in sorted(values))


ABILITY_WHERE = f"""w.state IN ({_sql_values(_ENDED_TURNS)})
    AND a.result_json IS NOT NULL
    AND COALESCE(json_extract(a.result_json,'$.terminationReason'),'') NOT IN ({_sql_values(NEITHER)})
    AND COALESCE(json_extract(a.result_json,'$.result.code'),'') != 'harness-unavailable'
    AND COALESCE(json_extract(a.result_json,'$.result.providerRejection.code'),'')
        NOT IN ({_sql_values(PROVIDER_REJECTIONS)})"""

PENDING_WHERE = """r.state IN ('accepted','concluded') AND (
    NOT EXISTS (SELECT 1 FROM micro_task_classifications c WHERE c.run_id=r.run_id)
    OR EXISTS (
        SELECT 1 FROM workflow_turns w JOIN attempts a ON a.attempt_id=w.attempt_id
        WHERE w.run_id=r.run_id AND """ + ABILITY_WHERE + """
        AND w.input_json IS NOT NULL
        AND json_type(w.input_json,'$.context.buddy')='object'
        AND NOT EXISTS (
            SELECT 1 FROM kind_summary_evidence e WHERE e.run_id=r.run_id
            AND e.harness=json_extract(w.input_json,'$.context.buddy.harness')
            AND e.model=json_extract(w.input_json,'$.context.buddy.model')
            AND e.effort=json_extract(w.input_json,'$.context.buddy.effort')
        )
    )
)"""


def _kind(value):
    return (value["harness"], value["model"], value["effort"])


def _kind_id(value):
    return json.dumps(_kind(value), ensure_ascii=False, separators=(",", ":"))


def _node_rows(connection):
    return {row["node_id"]: dict(row) for row in connection.execute(
        "SELECT node_id,name,definition,parent_id,merged_into_id FROM domain_nodes")}


def pending(connection) -> bool:
    """Classification and every participating kind's reading must both exist."""
    return connection.execute(
        "SELECT 1 FROM workflow_runs r WHERE " + PENDING_WHERE + " LIMIT 1").fetchone() is not None


def pending_count(connection) -> int:
    return connection.execute("SELECT COUNT(*) FROM workflow_runs r WHERE " + PENDING_WHERE).fetchone()[0]


def build_packet(connection) -> dict | None:
    """The oldest 50 tasks still needing classification or a kind's reading."""
    runs = list(connection.execute(
        "SELECT r.run_id,r.title,r.state,r.created_at,r.updated_at,r.continuation_count,r.buddy_revision,"
        " t.spec_json,t.accepted_at,r.macro_task_id,c.note AS conclusion_note,"
        " c.reason_code AS conclusion_reason,c.execution_status,c.created_at AS concluded_at,"
        " x.domain_node_id,x.task_type,"
        " (SELECT i.verification_json FROM workflow_integrations i"
        " WHERE i.run_id=r.run_id AND i.artifact_id=r.final_artifact_id"
        " ORDER BY i.created_at DESC,i.integration_id DESC LIMIT 1) AS integration_json"
        " FROM workflow_runs r JOIN tasks t ON t.task_id=r.run_id"
        " LEFT JOIN workflow_host_conclusions c ON c.run_id=r.run_id"
        " LEFT JOIN micro_task_classifications x ON x.run_id=r.run_id"
        " WHERE " + PENDING_WHERE +
        " ORDER BY COALESCE(t.accepted_at,c.created_at),r.run_id LIMIT 50"))
    classification_ids = [row["run_id"] for row in runs if row["domain_node_id"] is None]
    if not runs:
        return None
    run_ids = [row["run_id"] for row in runs]
    placeholders = ",".join("?" for _ in run_ids)
    turns = defaultdict(list)
    kinds = {}
    unread = defaultdict(set)
    participating = defaultdict(set)
    read = {(_kind(row), row["run_id"]) for row in connection.execute(
        f"SELECT harness,model,effort,run_id FROM kind_summary_evidence WHERE run_id IN ({placeholders})", run_ids)}
    for row in connection.execute(
        "SELECT w.run_id,w.turn_index,w.state,w.input_json,a.started_at,a.finished_at,"
        " a.result_json FROM workflow_turns w"
        " LEFT JOIN attempts a ON a.attempt_id=w.attempt_id"
        f" WHERE w.run_id IN ({placeholders}) ORDER BY w.run_id,w.turn_index", run_ids):
        buddy = turn_buddy(row["input_json"])
        kind = {key: buddy[key] for key in ("harness", "model", "effort")} if buddy is not None else None
        receipt = json.loads(row["result_json"]) if row["result_json"] else None
        category = round_classification(receipt) if row["state"] in _ENDED_TURNS else None
        if kind is not None and category == "ability":
            participating[_kind(kind)].add(row["run_id"])
            if (_kind(kind), row["run_id"]) not in read:
                kinds[_kind(kind)] = kind
                unread[_kind(kind)].add(row["run_id"])
        result = (receipt or {}).get("result") or {}
        turns[row["run_id"]].append({
            "turnIndex": row["turn_index"], "state": row["state"], "kind": kind,
            "outcome": (result.get("turn") or {}).get("outcome"),
            "failureCategory": category,
            "terminationReason": (receipt or {}).get("terminationReason"),
            "failureReason": result.get("code"),
            "startedAt": row["started_at"], "finishedAt": row["finished_at"],
            "usage": result.get("tokenUsage"),
        })
    boundaries = defaultdict(list)
    for row in connection.execute(
        "SELECT run_id,request_id,kind,state,reason_code,summary,created_at,decided_at"
        f" FROM workflow_requests WHERE run_id IN ({placeholders})"
        " ORDER BY created_at,request_id", run_ids):
        boundaries[row["run_id"]].append({
            "requestId": row["request_id"], "kind": row["kind"], "state": row["state"],
            "reasonCode": row["reason_code"], "summary": row["summary"],
            "createdAt": row["created_at"], "decidedAt": row["decided_at"],
        })
    from ..tasks.macro_tasks import project as macro_project
    projects = {identifier: macro_project(connection, identifier)
                for identifier in {row["macro_task_id"] for row in runs if row["macro_task_id"] is not None}}
    micro_tasks = []
    for row in runs:
        spec = json.loads(row["spec_json"])
        project = (projects[row["macro_task_id"]] if row["macro_task_id"] is not None
                   else {"id": None, "path": spec["cwd"]})
        micro_tasks.append({
            "runId": row["run_id"], "title": row["title"], "task": spec.get("task"),
            "project": {"id": project["id"], "path": project["path"]},
            "classification": ({"domainNodeId": row["domain_node_id"], "taskType": row["task_type"]}
                               if row["domain_node_id"] is not None else None),
            "result": {"state": row["state"], "conclusion": row["conclusion_note"],
                       "conclusionReason": row["conclusion_reason"],
                       "executionStatus": row["execution_status"],
                       "continuationRounds": row["continuation_count"],
                       "hostBoundaries": len(boundaries[row["run_id"]]),
                       "hostBoundaryFacts": boundaries[row["run_id"]],
                       "reassigned": row["buddy_revision"] > 1,
                       "integration": json.loads(row["integration_json"]) if row["integration_json"] else None,
                       "createdAt": row["created_at"],
                       "finishedAt": row["accepted_at"] or row["concluded_at"],
                       "turns": turns[row["run_id"]]},
        })
    summaries = {}
    for row in connection.execute("SELECT harness,model,effort,text FROM kind_summaries"):
        key = _kind(row)
        if key in kinds:
            summaries[key] = {"text": row["text"], "citations": []}
    for row in connection.execute("SELECT harness,model,effort,run_id FROM kind_summary_evidence WHERE cited=1"):
        key = _kind(row)
        if key in summaries:
            summaries[key]["citations"].append(row["run_id"])
    statistics = kind_domain_statistics(connection, list(kinds.values()))
    return {
        "microTasks": micro_tasks,
        "classificationRunIds": classification_ids,
        "domainNodes": list(_node_rows(connection).values()),
        "taskTypes": [{"id": key, "definition": value} for key, value in TASK_TYPES.items()],
        "kinds": [{**kind, "kindId": _kind_id(kind), "unreadRunIds": sorted(unread[key]),
                   "citationRunIds": sorted(participating[key]), "summary": summaries.get(key),
                   "statistics": statistics[key]}
                  for key, kind in kinds.items()],
    }


def _string(max_length):
    return {"type": "string", "minLength": 1, "maxLength": max_length}


def _object(properties):
    return {"type": "object", "properties": properties,
            "required": list(properties), "additionalProperties": False}


def answer_schema(packet) -> dict:
    """Use only the JSON Schema keywords supported by structured_call."""
    ids = packet["classificationRunIds"]
    summary_properties = {}
    for kind in packet["kinds"]:
        original = (kind.get("summary") or {}).get("citations") or []
        citation_ids = sorted(set(kind["citationRunIds"]) | set(original))
        summary_properties[kind["kindId"]] = _object({
            "text": _string(4000),
            "citations": {"type": "array", "items": {"type": "string", "enum": citation_ids}},
        })
    schema = _object({
        "newNodes": {"type": "array", "items": _object({
            "id": _string(128), "name": _string(120), "definition": _string(400),
            "parentId": {"type": ["string", "null"]}})},
        "merges": {"type": "array", "items": _object({
            "sourceId": _string(128), "targetId": _string(128)})},
        "classifications": {"type": "array", "items": _object({
            "runId": {"type": "string", "enum": ids},
            "domainNodeId": _string(128),
            "taskType": {"type": "string", "enum": list(TASK_TYPES)}})},
        "summaries": {"type": "object", "properties": summary_properties,
                      "required": list(summary_properties), "additionalProperties": False},
    })
    if not ids:
        schema["properties"]["classifications"]["maxItems"] = 0
        schema["properties"]["classifications"]["items"]["properties"]["runId"] = {"type": "string"}
    return schema


def _shape_errors(value, schema, path="answer"):
    """Retain a field path for each supported Schema violation."""
    direct = {key: part for key, part in schema.items() if key not in ("properties", "items")}
    if "properties" in schema:
        direct["properties"] = {key: {} for key in schema["properties"]}
    missing = iter(key for key in schema.get("required", []) if isinstance(value, dict) and key not in value)
    errors = [f"{path}.{next(missing)}: required" if keyword == "required" else f"{path}: {keyword}"
              for keyword in schema_errors(value, direct)]
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
        name = node["name"]
        if name in active_names:
            raise ValueError(f"active domain name already exists: {name}")
        active_names.add(name)
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
    if not isinstance(node_id, str) or not node_id or len(node_id) > 128:
        raise BoardError("INVALID_ARGUMENT", "domain node id must have 1 to 128 characters")
    if not isinstance(name, str):
        raise BoardError("INVALID_ARGUMENT", "domain name must be a string")
    name = name.strip()
    if not name or len(name) > 120:
        raise BoardError("INVALID_ARGUMENT", "domain name must have 1 to 120 characters")
    if not isinstance(definition, str) or not definition.strip() or len(definition) > 400:
        raise BoardError("INVALID_ARGUMENT", "domain definition must have 1 to 400 characters")
    nodes = _node_rows(connection)
    if node_id not in nodes or nodes[node_id]["merged_into_id"] is not None:
        raise BoardError("INVALID_ARGUMENT", f"domain node is not active: {node_id}")
    nodes[node_id].update(name=name, definition=definition)
    try:
        _check_tree(nodes)
    except ValueError as exc:
        raise BoardError("INVALID_ARGUMENT", str(exc)) from exc
    connection.execute("UPDATE domain_nodes SET name=?,definition=? WHERE node_id=?",
                       (name, definition, node_id))


def merge_domain(connection, source_id, target_id):
    if not isinstance(source_id, str) or not source_id or len(source_id) > 128:
        raise BoardError("INVALID_ARGUMENT", "merge source id must have 1 to 128 characters")
    if not isinstance(target_id, str) or not target_id or len(target_id) > 128:
        raise BoardError("INVALID_ARGUMENT", "merge target id must have 1 to 128 characters")
    _merge_domains(connection, source_id, target_id)


def _merge_domains(connection, source_id, target_id):
    nodes = _node_rows(connection)
    if source_id not in nodes or nodes[source_id]["merged_into_id"] is not None:
        raise BoardError("INVALID_ARGUMENT", f"merge source is not active: {source_id}")
    for node in nodes.values():
        if node["parent_id"] == source_id:
            node["parent_id"] = target_id
        if node["merged_into_id"] == source_id:
            node["merged_into_id"] = target_id
    nodes[source_id]["merged_into_id"] = target_id
    nodes[source_id]["parent_id"] = None
    try:
        _check_tree(nodes)
    except ValueError as exc:
        raise BoardError("INVALID_ARGUMENT", str(exc)) from exc
    connection.execute("UPDATE domain_nodes SET parent_id=? WHERE parent_id=?", (target_id, source_id))
    connection.execute("UPDATE domain_nodes SET merged_into_id=? WHERE merged_into_id=?", (target_id, source_id))
    connection.execute("UPDATE domain_nodes SET parent_id=NULL,merged_into_id=? WHERE node_id=?",
                       (target_id, source_id))


def apply_answer(connection, *, packet, answer, maintenance_run_id) -> dict:
    """Accept independently valid items and report precise repairable errors."""
    errors = []
    counts = {"nodes": 0, "merges": 0, "classifications": 0, "summaries": 0}
    if answer is None:
        return {"errors": ["answer-invalid-json"], "written": counts}
    if isinstance(answer, str):
        try:
            answer = json.loads(answer)
        except (ValueError, RecursionError):
            return {"errors": ["answer-invalid-json"], "written": counts}
    schema = answer_schema(packet)
    root = {key: value for key, value in schema.items() if key != "properties"}
    root["properties"] = {key: {} for key in schema["properties"]}
    errors.extend(f"answer: {item}" for item in schema_errors(answer, root))
    if not isinstance(answer, dict):
        return {"errors": errors, "written": counts}
    valid = {}
    for field, field_schema in schema["properties"].items():
        items = answer.get(field)
        if field == "summaries":
            valid[field] = {}
            summary_shape = {**field_schema, "properties": {key: {} for key in field_schema["properties"]}}
            errors.extend(_shape_errors(items, summary_shape, "answer.summaries"))
            if not isinstance(items, dict):
                continue
            for kind_id, item in items.items():
                item_schema = field_schema["properties"].get(kind_id)
                if item_schema is None:
                    continue
                item_errors = _shape_errors(item, item_schema, f"answer.summaries[{kind_id}]")
                if item_errors:
                    errors.extend(item_errors)
                else:
                    valid[field][kind_id] = item
            continue
        field_errors = _shape_errors(items, {key: part for key, part in field_schema.items() if key != "items"},
                                     f"answer.{field}")
        if field_errors:
            errors.extend(field_errors)
            valid[field] = []
            continue
        valid[field] = []
        for index, item in enumerate(items):
            item_errors = _shape_errors(item, field_schema["items"], f"answer.{field}[{index}]")
            if item_errors:
                errors.extend(item_errors)
            else:
                valid[field].append(item)
    packet_run_ids = {item["runId"] for item in packet["microTasks"]}
    placeholders = ",".join("?" for _ in packet_run_ids)
    surviving = {row["run_id"] for row in connection.execute(
        f"SELECT run_id FROM workflow_runs WHERE run_id IN ({placeholders})", tuple(packet_run_ids))}
    if not surviving:
        return {"errors": [], "written": counts}
    batch = set(packet["classificationRunIds"]) & surviving
    packet_kinds = {_kind(kind): kind for kind in packet["kinds"]
                    if set(kind["unreadRunIds"]) & surviving}
    kinds_by_id = {kind["kindId"]: _kind(kind) for kind in packet["kinds"]}
    read = {(_kind(row), row["run_id"]) for row in connection.execute(
        f"SELECT harness,model,effort,run_id FROM kind_summary_evidence WHERE run_id IN ({placeholders})", tuple(packet_run_ids))}
    missing_reads = {key: {run_id for run_id in kind["unreadRunIds"]
                          if run_id in surviving and (key, run_id) not in read}
                     for key, kind in packet_kinds.items()}
    nodes = _node_rows(connection)
    new_counts = Counter(item["id"] for item in valid["newNodes"])
    for node_id, count in new_counts.items():
        if count > 1:
            errors.append(f"newNodes[{node_id}]: duplicate id")
    new = {item["id"]: item for item in valid["newNodes"] if new_counts[item["id"]] == 1}
    pending = dict(new)
    while pending:
        progressed = False
        for node_id, item in list(pending.items()):
            parent = item["parentId"]
            if parent in pending:
                continue
            del pending[node_id]
            progressed = True
            name = item["name"].strip()
            if not name or not item["definition"].strip():
                errors.append(f"newNodes[{node_id}]: name and definition must be nonblank")
                continue
            if node_id in nodes:
                # A previously accepted node from this run survives correction.
                owner = connection.execute("SELECT maintenance_run_id FROM domain_nodes WHERE node_id=?",
                                           (node_id,)).fetchone()[0]
                resolved_parent = (nodes[parent]["merged_into_id"] or parent) if parent in nodes else parent
                if (owner != maintenance_run_id or nodes[node_id]["name"] != name
                        or nodes[node_id]["definition"] != item["definition"]
                        or nodes[node_id]["parent_id"] != resolved_parent):
                    errors.append(f"newNodes[{node_id}]: existing node cannot be changed")
                continue
            if parent is not None and (parent not in nodes or nodes[parent]["merged_into_id"] is not None):
                errors.append(f"newNodes[{node_id}]: parent is not active: {parent}")
                continue
            candidate = {"node_id": node_id, "name": name, "definition": item["definition"],
                         "parent_id": parent, "merged_into_id": None}
            try:
                _check_tree({**nodes, node_id: candidate})
            except ValueError as exc:
                errors.append(f"newNodes[{node_id}]: {exc}")
                continue
            connection.execute("INSERT INTO domain_nodes VALUES(?,?,?,?,NULL,?)",
                               (node_id, name, item["definition"], parent, maintenance_run_id))
            nodes[node_id] = candidate
            counts["nodes"] += 1
        if not progressed:
            errors.extend(f"newNodes[{node_id}]: parent cycle" for node_id in pending)
            break
    merge_counts = Counter(item["sourceId"] for item in valid["merges"])
    seen_merges = set()
    for item in valid["merges"]:
        source, target = item["sourceId"], item["targetId"]
        if merge_counts[source] > 1:
            if source not in seen_merges:
                errors.append(f"merges[{source}]: duplicate source")
            seen_merges.add(source)
            continue
        seen_merges.add(source)
        current_nodes = _node_rows(connection)
        resolved = (current_nodes[target]["merged_into_id"] or target) if target in current_nodes else target
        if source in current_nodes and current_nodes[source]["merged_into_id"] == resolved:
            continue
        try:
            _merge_domains(connection, source, target)
            counts["merges"] += 1
        except BoardError as exc:
            errors.append(f"merges[{source}]: {exc}")
    nodes = _node_rows(connection)
    classification_counts = Counter(item["runId"] for item in valid["classifications"])
    classified = set()
    for item in valid["classifications"]:
        run_id, node_id = item["runId"], item["domainNodeId"]
        if run_id not in surviving:
            continue
        if classification_counts[run_id] > 1:
            if run_id not in classified:
                errors.append(f"classifications[{run_id}]: duplicate runId")
            classified.add(run_id)
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
        accumulate_cell(connection, run_id, node_id, item["taskType"])
        counts["classifications"] += 1
    for run_id in batch - classified:
        if not connection.execute("SELECT 1 FROM micro_task_classifications WHERE run_id=?", (run_id,)).fetchone():
            errors.append(f"classifications[{run_id}]: missing classification")
    for kind_id, item in valid["summaries"].items():
        key = kinds_by_id[kind_id]
        if key not in packet_kinds:
            continue
        if not missing_reads[key]:
            continue
        cited = item["citations"]
        if len(cited) != len(set(cited)):
            errors.append(f"summaries[{key}]: duplicate citation")
            continue
        if cited:
            cited_placeholders = ",".join("?" for _ in cited)
            existing_citations = {row["run_id"] for row in connection.execute(
                f"SELECT run_id FROM workflow_runs WHERE run_id IN ({cited_placeholders})", cited)}
            if len(existing_citations) != len(cited):
                errors.append(f"summaries[{key}]: cited micro task was deleted; rewrite summary")
                continue
        connection.execute(
            "INSERT INTO kind_summaries(harness,model,effort,text,maintenance_run_id) VALUES(?,?,?,?,?)"
            " ON CONFLICT(harness,model,effort) DO UPDATE SET text=excluded.text,"
            " maintenance_run_id=excluded.maintenance_run_id", (*key, item["text"], maintenance_run_id))
        connection.execute("UPDATE kind_summary_evidence SET cited=0 WHERE harness=? AND model=? AND effort=?", key)
        connection.executemany(
            "INSERT INTO kind_summary_evidence(harness,model,effort,run_id,cited) VALUES(?,?,?,?,?)"
            " ON CONFLICT(harness,model,effort,run_id) DO UPDATE SET cited=excluded.cited",
            [(*key, run_id, int(run_id in cited)) for run_id in missing_reads[key] | set(cited)])
        counts["summaries"] += 1
    return {"errors": errors, "written": counts}
