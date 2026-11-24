"""Bounded maintenance evidence and validated domain/summary writes."""
from __future__ import annotations

from collections import Counter, defaultdict
import json
from ...json_codec import canonical_json, decode_strict_json

from ...protocol.structured_schema import schema_issues
from ...protocol.task_types import TASK_TYPES
from ...protocol.snapshot_query import MAINTENANCE_SECTIONS, MaintenanceCoverage, kind_id
from ...errors import BoardError
from ...protocol.turn_input import turn_buddy
from .evidence import kind_domain_statistics
from ..evaluation.models import model_view
from .statistics import NEITHER, NON_ABILITY_PROVIDER_REJECTIONS, accumulate_cell, round_classification



MICRO_TASKS_MAX_BYTES = 128 * 1024
MAX_MICRO_TASKS = 16
MAX_SUMMARY_KINDS = 3
MAX_SUMMARY_TEXT_BYTES = 4096
MAX_SUMMARY_CITATIONS = 32

_ENDED_TURNS = ("returned", "failed", "rejected", "interrupted")


def _sql_values(values):
    return ",".join("'" + value.replace("'", "''") + "'" for value in sorted(values))


ABILITY_WHERE = f"""w.state IN ({_sql_values(_ENDED_TURNS)})
    AND a.result_json IS NOT NULL
    AND COALESCE(json_extract(a.result_json,'$.terminationReason'),'') NOT IN ({_sql_values(NEITHER)})
    AND COALESCE(json_extract(a.result_json,'$.result.code'),'') != 'harness-unavailable'
    AND COALESCE(json_extract(a.result_json,'$.result.providerRejection.code'),'')
        NOT IN ({_sql_values(NON_ABILITY_PROVIDER_REJECTIONS)})"""

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
            AND e.model_id=a.model_id
            AND e.effort=json_extract(w.input_json,'$.context.buddy.effort')
        )
    )
)"""


def _kind(value):
    return (value["harness"], value["model_id"] if "model_id" in value.keys() else value["modelId"],
            value["effort"])


def _kind_payload(connection, harness, model_id, effort):
    view = model_view(connection, model_id)
    return {"harness": harness, "modelId": model_id, "effort": effort,
            "modelDisplayName": view["displayName"], "modelNames": view["names"]}


def _node_rows(connection):
    return {row["node_id"]: dict(row) for row in connection.execute(
        "SELECT node_id,name,definition,parent_id,merged_into_id FROM domain_nodes")}


def pending(connection) -> bool:
    """Classification and every participating kind's reading must both exist."""
    return connection.execute(
        "SELECT 1 FROM workflow_runs r WHERE " + PENDING_WHERE + " LIMIT 1").fetchone() is not None


def pending_count(connection) -> int:
    return connection.execute("SELECT COUNT(*) FROM workflow_runs r WHERE " + PENDING_WHERE).fetchone()[0]


def _comparison_statistics(statistics, summary_kinds, comparison_kinds):
    """Other kinds' overall rows and samples in the summary targets' rows and cells."""
    domains = {node_id for kind in summary_kinds for node_id in statistics[kind]["byDomain"]}
    cells = {(node_id, task_type) for kind in summary_kinds
             for node_id, types in statistics[kind]["byDomainTaskType"].items() for task_type in types}
    comparisons = []
    for kind in comparison_kinds:
        key = _kind(kind)
        if key in summary_kinds:
            continue
        facts = statistics[key]
        by_type = {}
        for node_id, types in facts["byDomainTaskType"].items():
            selected = {task_type: fact for task_type, fact in types.items() if (node_id, task_type) in cells}
            if selected:
                by_type[node_id] = selected
        comparisons.append({**kind, "statistics": {
            "overall": facts["overall"],
            "byDomain": {node_id: fact for node_id, fact in facts["byDomain"].items() if node_id in domains},
            "byDomainTaskType": by_type,
        }})
    return comparisons


def build_packet(connection) -> dict | None:
    """Take an ordered prefix of pending tasks without truncating any task."""
    runs = list(connection.execute(
        "SELECT r.run_id,r.title,r.state,r.created_at,r.updated_at,r.continuation_count,r.buddy_revision,"
        " t.spec_json,t.accepted_at,t.acceptance_note,r.macro_task_id,c.note AS conclusion_note,"
        " c.reason_code AS conclusion_reason,c.execution_status,c.created_at AS concluded_at,"
        " x.domain_node_id,x.task_type,"
        " (SELECT i.verification_json FROM workflow_integrations i"
        " WHERE i.run_id=r.run_id AND i.artifact_id=r.final_artifact_id"
        " ORDER BY i.created_at DESC,i.integration_id DESC LIMIT 1) AS integration_json"
        " FROM workflow_runs r JOIN tasks t ON t.task_id=r.run_id"
        " LEFT JOIN workflow_host_conclusions c ON c.run_id=r.run_id"
        " LEFT JOIN micro_task_classifications x ON x.run_id=r.run_id"
        " WHERE " + PENDING_WHERE +
        " ORDER BY COALESCE(t.accepted_at,c.created_at),r.run_id LIMIT ?", (MAX_MICRO_TASKS,)))
    if not runs:
        return None
    run_ids = [row["run_id"] for row in runs]
    placeholders = ",".join("?" for _ in run_ids)
    turns = defaultdict(list)
    read = {(_kind(row), row["run_id"]) for row in connection.execute(
        f"SELECT harness,model_id,effort,run_id FROM kind_summary_evidence WHERE run_id IN ({placeholders})", run_ids)}
    for row in connection.execute(
        "SELECT w.run_id,w.turn_index,w.state,w.input_json,a.started_at,a.finished_at,"
        " a.result_json,a.model_id FROM workflow_turns w"
        " LEFT JOIN attempts a ON a.attempt_id=w.attempt_id"
        f" WHERE w.run_id IN ({placeholders}) ORDER BY w.run_id,w.turn_index", run_ids):
        buddy = turn_buddy(row["input_json"])
        kind = (_kind_payload(connection, buddy["harness"], row["model_id"], buddy["effort"])
                if buddy is not None and row["model_id"] is not None else None)
        receipt = json.loads(row["result_json"]) if row["result_json"] else None
        category = round_classification(receipt) if row["state"] in _ENDED_TURNS else None
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
        "SELECT run_id,request_id,state,reason_code,summary,created_at,decided_at"
        f" FROM workflow_requests WHERE run_id IN ({placeholders})"
        " ORDER BY created_at,request_id", run_ids):
        boundaries[row["run_id"]].append({
            "requestId": row["request_id"], "state": row["state"],
            "reasonCode": row["reason_code"], "summary": row["summary"],
            "createdAt": row["created_at"], "decidedAt": row["decided_at"],
        })
    continuations = defaultdict(list)
    for row in connection.execute(
        "SELECT c.run_id,c.continuation_id,c.kind,c.state,w.turn_index,c.input_text,c.note,"
        " c.created_at,c.consumed_at FROM workflow_continuations c"
        " LEFT JOIN workflow_turns w ON w.turn_id=c.turn_id"
        f" WHERE c.run_id IN ({placeholders}) ORDER BY c.created_at,c.continuation_id", run_ids):
        continuations[row["run_id"]].append({
            "continuationId": row["continuation_id"], "kind": row["kind"], "state": row["state"],
            "turnIndex": row["turn_index"], "input": row["input_text"], "note": row["note"],
            "createdAt": row["created_at"], "consumedAt": row["consumed_at"],
        })
    from ..tasks.macro_tasks import project as macro_project
    projects = {identifier: macro_project(connection, identifier)
                for identifier in {row["macro_task_id"] for row in runs if row["macro_task_id"] is not None}}
    micro_tasks = []
    micro_task_bytes = 2  # JSON array brackets
    for row in runs:
        spec = json.loads(row["spec_json"])
        project = (projects[row["macro_task_id"]] if row["macro_task_id"] is not None
                   else {"id": None, "path": spec["cwd"]})
        task = {
            "runId": row["run_id"], "title": row["title"], "task": spec.get("task"),
            "project": {"id": project["id"], "path": project["path"]},
            "classification": ({"domainNodeId": row["domain_node_id"], "taskType": row["task_type"]}
                               if row["domain_node_id"] is not None else None),
            "result": {"state": row["state"], "conclusion": row["conclusion_note"],
                       "acceptanceNote": row["acceptance_note"],
                       "conclusionReason": row["conclusion_reason"],
                       "executionStatus": row["execution_status"],
                       "continuationRounds": row["continuation_count"],
                       "continuations": continuations[row["run_id"]],
                       "hostBoundaries": len(boundaries[row["run_id"]]),
                       "hostBoundaryFacts": boundaries[row["run_id"]],
                       "reassigned": row["buddy_revision"] > 1,
                       "integration": json.loads(row["integration_json"]) if row["integration_json"] else None,
                       "createdAt": row["created_at"],
                       "finishedAt": row["accepted_at"] or row["concluded_at"],
                       "turns": turns[row["run_id"]]},
        }
        comma_bytes = 1 if micro_tasks else 0
        next_size = micro_task_bytes + comma_bytes + len(canonical_json(task).encode("utf-8"))
        if micro_tasks and next_size > MICRO_TASKS_MAX_BYTES:
            break
        micro_tasks.append(task)
        micro_task_bytes = next_size
        if micro_task_bytes >= MICRO_TASKS_MAX_BYTES:
            break
    kinds = {}
    unread = defaultdict(dict)
    participating = defaultdict(dict)
    for task in micro_tasks:
        run_id = task["runId"]
        for turn in task["result"]["turns"]:
            kind = turn["kind"]
            if kind is None or turn["failureCategory"] != "ability":
                continue
            key = _kind(kind)
            participating[key][run_id] = None
            if (key, run_id) not in read and (key in kinds or len(kinds) < MAX_SUMMARY_KINDS):
                kinds[key] = kind
                unread[key][run_id] = None
    summaries = {}
    for row in connection.execute("SELECT harness,model_id,effort,text FROM kind_summaries"):
        key = _kind(row)
        if key in kinds:
            summaries[key] = {"text": row["text"], "citations": []}
    for row in connection.execute("SELECT harness,model_id,effort,run_id FROM kind_summary_evidence WHERE cited=1"):
        key = _kind(row)
        if key in summaries:
            summaries[key]["citations"].append(row["run_id"])
    # Only the summary targets' existing samples select comparison rows and cells.
    # Newly classified tasks contribute their cells on a later maintenance run.
    comparison_kinds = [_kind_payload(connection, row["harness"], row["model_id"], row["effort"])
                        for row in connection.execute(
        "SELECT harness,model_id,effort FROM kind_statistics"
        " ORDER BY harness,model_id,effort")]
    statistics = kind_domain_statistics(connection, comparison_kinds + list(kinds.values()))
    return {
        "microTasks": micro_tasks,
        "classificationRunIds": [task["runId"] for task in micro_tasks if task["classification"] is None],
        "domainNodes": list(_node_rows(connection).values()),
        "taskTypes": [{"id": key, "definition": value} for key, value in TASK_TYPES.items()],
        "comparisonStatistics": _comparison_statistics(statistics, kinds, comparison_kinds),
        "kinds": [{**kind, "kindId": kind_id(kind), "unreadRunIds": list(unread[key]),
                   "citationRunIds": list(participating[key]), "summary": summaries.get(key),
                   "statistics": statistics[key]}
                  for key, kind in kinds.items()],
    }


def _string(max_length):
    return {"type": "string", "minLength": 1, "maxLength": max_length}


def _object(properties):
    return {"type": "object", "properties": properties,
            "required": list(properties), "additionalProperties": False}


def answer_schema() -> dict:
    """The answer shape is independent of the size of the frozen evidence."""
    return _object({
        "newNodes": {"type": "array", "items": _object({
            "id": _string(128), "name": _string(120), "definition": _string(400),
            "parentId": {"type": ["string", "null"], "maxLength": 128}})},
        "merges": {"type": "array", "items": _object({
            "sourceId": _string(128), "targetId": _string(128)})},
        "classifications": {"type": "array", "maxItems": MAX_MICRO_TASKS, "items": _object({
            "runId": {"type": "string", "minLength": 1},
            "domainNodeId": _string(128),
            "taskType": {"type": "string", "enum": list(TASK_TYPES)}})},
        "summaries": {"type": "array", "maxItems": MAX_SUMMARY_KINDS, "items": _object({
            "kindId": {"type": "string", "minLength": 1},
            "text": _string(4000),
            "citations": {"type": "array", "maxItems": MAX_SUMMARY_CITATIONS,
                          "items": {"type": "string", "minLength": 1}},
        })},
    })


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


def _merged_tree(nodes, source_id, target_id):
    if source_id not in nodes or nodes[source_id]["merged_into_id"] is not None:
        raise ValueError(f"merge source is not active: {source_id}")
    if target_id not in nodes or nodes[target_id]["merged_into_id"] is not None:
        raise ValueError(f"merge target is not active: {target_id}")
    merged = {key: dict(node) for key, node in nodes.items()}
    for node in merged.values():
        if node["parent_id"] == source_id:
            node["parent_id"] = target_id
        if node["merged_into_id"] == source_id:
            node["merged_into_id"] = target_id
    merged[source_id].update(merged_into_id=target_id, parent_id=None)
    _check_tree(merged)
    return merged


def _merge_domains(connection, source_id, target_id):
    try:
        _merged_tree(_node_rows(connection), source_id, target_id)
    except ValueError as exc:
        raise BoardError("INVALID_ARGUMENT", str(exc)) from exc
    _write_merge(connection, source_id, target_id)


def _write_merge(connection, source_id, target_id):
    connection.execute("UPDATE domain_nodes SET parent_id=? WHERE parent_id=?", (target_id, source_id))
    connection.execute("UPDATE domain_nodes SET merged_into_id=? WHERE merged_into_id=?", (target_id, source_id))
    connection.execute("UPDATE domain_nodes SET parent_id=NULL,merged_into_id=? WHERE node_id=?",
                       (target_id, source_id))


def _items(answer, errors):
    schema = answer_schema()
    fields = schema["properties"]
    valid = {field: [] for field in fields}
    if isinstance(answer, str):
        try:
            answer = decode_strict_json(answer)
        except (ValueError, RecursionError):
            errors.append("answer-invalid-json")
            return valid
    root = {**schema, "properties": {key: {} for key in fields}}
    errors.extend(f"{path}: {keyword}" for path, keyword in schema_issues(answer, root))
    if not isinstance(answer, dict):
        return valid
    for field, field_schema in fields.items():
        if field not in answer:
            continue
        items = answer[field]
        array_schema = {key: value for key, value in field_schema.items() if key != "items"}
        issues = list(schema_issues(items, array_schema, f"$.{field}"))
        errors.extend(f"{path}: {keyword}" for path, keyword in issues)
        if not isinstance(items, list):
            continue
        for index, item in enumerate(items):
            issues = list(schema_issues(item, field_schema["items"], f"$.{field}[{index}]"))
            if issues:
                errors.extend(f"{path}: {keyword}" for path, keyword in issues)
            else:
                try:
                    json.dumps(item, ensure_ascii=False).encode("utf-8")
                except UnicodeEncodeError:
                    errors.append(f"{field}[{index}]: invalid Unicode")
                else:
                    valid[field].append(item)
    return valid


def _unique(items, field, key, errors):
    counts = Counter(item[key] for item in items)
    errors.extend(f"{field}[{value}]: duplicate {key}" for value, count in counts.items() if count > 1)
    return {item[key]: item for item in items if counts[item[key]] == 1}


class _Batch:
    """Live targets and a mutable in-memory projection of accepted tree edits."""

    def __init__(self, connection, packet):
        self.connection = connection
        self.run_ids = {task["runId"] for task in packet["microTasks"]}
        placeholders = ",".join("?" for _ in self.run_ids)
        ids = tuple(self.run_ids)
        self.surviving = {row[0] for row in connection.execute(
            f"SELECT run_id FROM workflow_runs WHERE run_id IN ({placeholders})", ids)}
        classified = {row[0] for row in connection.execute(
            f"SELECT run_id FROM micro_task_classifications WHERE run_id IN ({placeholders})", ids)}
        self.classifications = set(packet["classificationRunIds"])
        self.unclassified = (self.classifications & self.surviving) - classified
        self.kinds = {kind["kindId"]: kind for kind in packet["kinds"]}
        read = {(_kind(row), row["run_id"]) for row in connection.execute(
            f"SELECT harness,model_id,effort,run_id FROM kind_summary_evidence WHERE run_id IN ({placeholders})", ids)}
        self.unread = {identifier: {run_id for run_id in kind["unreadRunIds"]
                                    if run_id in self.surviving and (_kind(kind), run_id) not in read}
                       for identifier, kind in self.kinds.items()}
        self.nodes = _node_rows(connection)

    def tree_edit(self, field, item):
        if field == "merges":
            self.nodes = _merged_tree(self.nodes, item["sourceId"], item["targetId"])
            return
        node_id, parent = item["id"], item["parentId"]
        if node_id in self.nodes:
            raise ValueError("existing node cannot be changed")
        if any("\x00" in value for value in (node_id, item["name"], item["definition"])):
            raise ValueError("domain node fields must not contain NUL characters")
        name = item["name"].strip()
        if not name or not item["definition"].strip():
            raise ValueError("name and definition must be nonblank")
        candidate = {"node_id": node_id, "name": name, "definition": item["definition"],
                     "parent_id": parent, "merged_into_id": None}
        updated = {**self.nodes, node_id: candidate}
        _check_tree(updated)
        self.nodes = updated

    def classification_problem(self, item):
        node_id = item["domainNodeId"]
        if node_id not in self.nodes or self.nodes[node_id]["merged_into_id"] is not None:
            return f"domain node is not active: {node_id}"
        return None

    def summary_problem(self, item):
        kind = self.kinds[item["kindId"]]
        if len(item["text"].encode("utf-8")) > MAX_SUMMARY_TEXT_BYTES:
            return f"text exceeds {MAX_SUMMARY_TEXT_BYTES} UTF-8 bytes"
        cited = item["citations"]
        if len(cited) != len(set(cited)):
            return "duplicate citation"
        original = (kind["summary"] or {}).get("citations", [])
        if set(cited) - set(kind["citationRunIds"]) - set(original):
            return "citation is outside the frozen kind evidence"
        if any(not self.connection.execute("SELECT 1 FROM workflow_runs WHERE run_id=?", (run_id,)).fetchone()
               for run_id in cited):
            return "cited micro task was deleted; rewrite summary"
        return None


def _tree_edit(batch, field, identifier, item, staged, errors):
    try:
        batch.tree_edit(field, item)
    except ValueError as error:
        errors.append(f"{field}[{identifier}]: {error}")
    else:
        staged.append([field, identifier, item])


def _checked_tree(batch, items, previous, errors):
    tree = []
    for field, identifier, item in previous:
        _tree_edit(batch, field, identifier, item, tree, errors)
    existing = {(field, identifier): item for field, identifier, item in previous}
    for field, key in (("newNodes", "id"), ("merges", "sourceId")):
        pending = _unique(items[field], field, key, errors)
        while pending:
            ready = {identifier: item for identifier, item in pending.items()
                     if field == "merges" or item["parentId"] not in pending}
            if not ready:
                errors.extend(f"newNodes[{identifier}]: parent cycle" for identifier in pending)
                break
            for identifier, item in ready.items():
                del pending[identifier]
                earlier = existing.get((field, identifier))
                if earlier is None:
                    _tree_edit(batch, field, identifier, item, tree, errors)
                elif earlier != item:
                    errors.append(f"{field}[{identifier}]: conflicts with staged entry")
    return tree


def _check_answer(batch, answer, previous, coverage, *, final=False):
    errors = []
    items = _items(answer, errors)
    tree = _checked_tree(batch, items, previous["tree"], errors)
    checked = {"tree": tree}
    missing_reads = defaultdict(dict)
    for field, key, allowed, targets, problem, sections, read_ids in (
        ("classifications", "runId", batch.classifications, batch.unclassified,
         batch.classification_problem, ("task",), lambda run_id: [run_id]),
        ("summaries", "kindId", batch.kinds, {key for key, ids in batch.unread.items() if ids},
         batch.summary_problem, MAINTENANCE_SECTIONS, batch.unread.__getitem__),
    ):
        prior = previous[field]
        # Known targets already settled or deleted have no remaining answer obligation.
        active = [item for item in items[field] if item[key] in targets or item[key] not in allowed]
        submitted = _unique(active, field, key, errors)
        for identifier in submitted:
            if identifier in allowed:
                continue
            errors.append(f"{field}[{identifier}]: unknown {key}")
        accepted = {}
        for identifier in sorted(targets):
            earlier = prior.get(identifier)
            proposed = submitted.get(identifier)
            label = f"{field}[{identifier}]"
            if earlier is not None and not final and problem(earlier) is not None:
                if proposed is None:
                    errors.append(f"{label}: {problem(earlier)}")
                    continue
                earlier = None
            if earlier is not None and proposed is not None and earlier != proposed:
                errors.append(f"{label}: conflicts with staged entry")
            item = earlier if earlier is not None else proposed
            if item is None:
                errors.append(f"{label}: missing {key}")
                continue
            issue = problem(item)
            if issue:
                errors.append(f"{label}: {issue}")
                continue
            if earlier is not None and not final:
                accepted[identifier] = earlier
                continue
            gaps = list(coverage.gaps(read_ids(identifier), sections))
            if gaps:
                if final:
                    requirement = ("the complete task section" if field == "classifications"
                                   else "all five sections of every unread micro task in this batch")
                    errors.append(f"{label}: read {requirement}")
                else:
                    for run_id, section, cursor in gaps:
                        missing_reads[run_id][section] = cursor
            else:
                accepted[identifier] = item
        checked[field] = accepted
    reads = [{"runId": run_id, "sections": [
        {"section": section, "cursor": missing_reads[run_id][section]}
        for section in MAINTENANCE_SECTIONS if section in missing_reads[run_id]
    ]} for run_id in sorted(missing_reads)]
    return checked, errors, reads


def stage_answer(connection, *, packet, answer, previous, issued_events):
    """Stage only valid entries whose required pages have already been issued."""
    batch = _Batch(connection, packet)
    coverage = MaintenanceCoverage(packet, issued_events, batch.surviving, issued_only=True)
    staged, errors, reads = _check_answer(batch, answer, previous, coverage)
    return staged, {"errors": errors, "missingReads": reads, "staged": {
        "newNodes": [identifier for field, identifier, _ in staged["tree"] if field == "newNodes"],
        "merges": [identifier for field, identifier, _ in staged["tree"] if field == "merges"],
        "classifications": sorted(staged["classifications"]),
        "summaries": sorted(staged["summaries"]),
    }}


def commit_staged(connection, *, packet, answer, staged, verified_events, maintenance_run_id):
    """Recheck staging and the latest answer against live state and verified delivery."""
    batch = _Batch(connection, packet)
    coverage = MaintenanceCoverage(packet, verified_events, batch.surviving)
    checked, errors, _ = _check_answer(batch, answer, staged, coverage, final=True)
    counts = {"nodes": 0, "merges": 0, "classifications": 0, "summaries": 0}
    if batch.surviving:
        for field, identifier, item in checked["tree"]:
            if field == "newNodes":
                connection.execute("INSERT INTO domain_nodes VALUES(?,?,?,?,NULL,?)",
                                   (identifier, item["name"].strip(), item["definition"], item["parentId"], maintenance_run_id))
                counts["nodes"] += 1
            else:
                _write_merge(connection, identifier, item["targetId"])
                counts["merges"] += 1
    for run_id, item in checked["classifications"].items():
        node_id, task_type = item["domainNodeId"], item["taskType"]
        connection.execute("INSERT INTO micro_task_classifications VALUES(?,?,?,?)",
                           (run_id, node_id, task_type, maintenance_run_id))
        accumulate_cell(connection, run_id, node_id, task_type)
        counts["classifications"] += 1
    for kind_id, item in checked["summaries"].items():
        unread = batch.unread[kind_id]
        key = _kind(batch.kinds[kind_id])
        cited = set(item["citations"])
        connection.execute(
            "INSERT INTO kind_summaries(harness,model_id,effort,text,maintenance_run_id) VALUES(?,?,?,?,?)"
            " ON CONFLICT(harness,model_id,effort) DO UPDATE SET text=excluded.text,"
            " maintenance_run_id=excluded.maintenance_run_id", (*key, item["text"], maintenance_run_id))
        connection.execute("UPDATE kind_summary_evidence SET cited=0 WHERE harness=? AND model_id=? AND effort=?", key)
        connection.executemany(
            "INSERT INTO kind_summary_evidence(harness,model_id,effort,run_id,cited) VALUES(?,?,?,?,?)"
            " ON CONFLICT(harness,model_id,effort,run_id) DO UPDATE SET cited=excluded.cited",
            [(*key, run_id, int(run_id in cited)) for run_id in sorted(unread | cited)])
        counts["summaries"] += 1
    return counts, errors
