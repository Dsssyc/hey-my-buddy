"""Read-only, bounded pages of one execution's frozen routing or maintenance input."""
from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Iterable
from copy import deepcopy
from dataclasses import dataclass
from hashlib import sha256
from itertools import islice
import re
from urllib.parse import quote

from ..json_codec import canonical_json
from .task_types import TASK_TYPES

MAX_QUERY_RESULT_BYTES = 16 * 1024
MAX_QUERY_IDS = 16
_PAGE_BUDGET = MAX_QUERY_RESULT_BYTES - 1024
_MAX_PAGE_ITEMS = 32
_TEXT_LIMIT = MAX_QUERY_RESULT_BYTES
_CURSOR = re.compile(r"([0-9a-f]{32}):([0-9]{1,16}):([0-9]{1,16})")
_IDENTITY = ("harness", "provider", "model", "effort")
_KIND = ("harness", "modelId", "effort")
_DETAILS = (
    "contextWindow", "capabilities", "modelId", "modelDisplayName", "modelNames",
    "infrastructureFailures", "modelProfile", "summary", "preference",
)
MAINTENANCE_SECTIONS = (
    "task", "turns", "continuations", "hostBoundaryFacts", "acceptanceAndIntegration",
)
_KIND_DETAILS = ("unreadRunIds", "citationRunIds", "summary", "statistics")
_PAGED = (
    "Return items and nextCursor. Repeat the same filters and pass nextCursor until it is null. "
    "An oversized item has a fragment instead of its full fields. Join its fragment.text in offset order "
    "and parse the joined JSON to recover the complete item."
)


class QueryError(ValueError):
    """The model supplied an invalid tool name, filter or page position."""


def _string(maximum=1024, *, nullable=False):
    return {"type": ["string", "null"] if nullable else "string",
            "minLength": 1, "maxLength": maximum}


def _array(item, maximum=MAX_QUERY_IDS):
    return {"type": "array", "items": item, "minItems": 1, "maxItems": maximum, "uniqueItems": True}


def _fields(names):
    return _array({"type": "string", "enum": list(names)}, len(names))


def _integer(minimum, maximum=None):
    schema = {"type": "integer", "minimum": minimum}
    if maximum is not None:
        schema["maximum"] = maximum
    return schema


def _tool(name, description, properties, *, required=(), paged=True):
    if paged:
        properties = {**properties, "cursor": _string(66), "limit": _integer(1, _MAX_PAGE_ITEMS)}
        description += " " + _PAGED
    return {"name": name, "description": description, "inputSchema": {
        "type": "object", "properties": properties, "required": list(required),
        "additionalProperties": False,
    }}


_DOMAIN_FILTERS = {"parentId": _string(nullable=True), "search": _string(512)}
_ROUTING_TOOLS = {
    tool["name"]: tool for tool in (
        _tool("query_domains", "Browse the children of a domain, or search domain names and definitions. "
              "Omit both filters for top-level domains.", _DOMAIN_FILTERS),
        _tool("query_compare", "Compare legal candidates using frozen load, preferences, profile positioning and statistics. "
              "A domain selects its aggregate; a domain and task type select a cell.",
              {"domainNodeId": _string(nullable=True), "taskType": {"type": "string", "enum": list(TASK_TYPES)}}),
        _tool("query_explore", "List same-kind candidate groups in ascending sample count at the selected "
              "domain's first-level ancestor. Omit the domain for overall samples.",
              {"domainNodeId": _string(nullable=True)}),
        _tool("query_details", "Read full candidate details. Omit include for all fields.",
              {"buddyIds": _array(_string()), "include": _fields(_DETAILS)}, required=("buddyIds",)),
        _tool("query_task", "Read the complete task text by Unicode code point. "
              "Pass nextOffset as offset until nextOffset is null.",
              {"offset": _integer(0), "limit": _integer(1, _TEXT_LIMIT)}, paged=False),
    )
}
_MAINTENANCE_TOOLS = {
    tool["name"]: tool for tool in (
        _tool("query_domains", "Browse the children of a domain, or search by identifier, name or definition. "
              "Omit both filters for all active domains. Pass parentId: null for top-level domains. "
              "Set includeMerged to see merged nodes and their retained target.",
              {**_DOMAIN_FILTERS, "includeMerged": {"type": "boolean"}}),
        _tool("query_batch", "List micro tasks in this batch, their classification and participating kinds.", {}),
        _tool("query_micro_tasks", "Read frozen micro task sections. The task section includes its basic metadata. "
              "Omit sections to read all. Each returned coverage range applies to one run and section.",
              {"runIds": _array(_string()), "sections": _fields(MAINTENANCE_SECTIONS)}, required=("runIds",)),
        _tool("query_kinds", "Read summary targets and comparison kinds. Omit kindIds for all kinds "
              "and include for identity and targets’ unreadRunIds only. Request include fields for details. "
              "Use domainNodeIds to select domain aggregates and task-type cells when requesting statistics.",
              {"kindIds": _array(_string()), "domainNodeIds": _array(_string()), "include": _fields(_KIND_DETAILS)}),
    )
}
_TOOL_SETS = {"routing": _ROUTING_TOOLS, "maintenance": _MAINTENANCE_TOOLS}


def _valid(value, schema):
    if "enum" in schema:
        return value in schema["enum"]
    match schema["type"]:
        case "integer":
            return (type(value) is int and value >= schema["minimum"]
                    and ("maximum" not in schema or value <= schema["maximum"]))
        case "boolean":
            return type(value) is bool
        case "array":
            return (isinstance(value, list) and schema["minItems"] <= len(value) <= schema["maxItems"]
                    and all(_valid(item, schema["items"]) for item in value)
                    and len(set(value)) == len(value))
        case kind:
            if kind == ["string", "null"] and value is None:
                return True
            return (isinstance(value, str) and schema["minLength"] <= len(value) <= schema["maxLength"]
                    and not any("\ud800" <= char <= "\udfff" for char in value))


def _validate(args, schema):
    if not isinstance(args, dict):
        raise QueryError("Tool arguments must be an object.")
    properties = schema["properties"]
    if args.keys() - properties.keys() or set(schema["required"]) - args.keys():
        raise QueryError("Tool arguments contain an unknown field or omit a required field.")
    for key, value in args.items():
        if not _valid(value, properties[key]):
            raise QueryError(f"Invalid value for {key}.")


def _fits(result, _coverage=None):
    # Native text content wraps the canonical JSON once more in JSON. Account
    # for escaping there and leave room for the transport envelope and receipt.
    return len(canonical_json(canonical_json(result)).encode("utf-8")) <= _PAGE_BUDGET


def _prefix(start, end, fits):
    """Find the longest code point interval whose complete result fits."""
    if fits(end):
        return end
    low, high = start, end
    while high - low > 1:
        middle = (low + high) // 2
        if fits(middle):
            low = middle
        else:
            high = middle
    if low == start:
        raise ValueError("A snapshot page cannot fit one code point")
    return low


@dataclass(frozen=True)
class _Record:
    identity: dict
    value: object
    wrap: bool = False

    def item(self):
        if self.wrap:
            return {**self.identity, "value": self.value}
        return {**self.identity, **self.value}


def _cursor_position(plane, tool, args):
    filters = {key: value for key, value in args.items() if key not in ("cursor", "limit")}
    signature = sha256(canonical_json([plane, tool, filters]).encode("utf-8")).hexdigest()[:32]
    cursor = args.get("cursor")
    if cursor is None:
        return signature, 0, 0
    match = _CURSOR.fullmatch(cursor)
    if match is None or match[1] != signature:
        raise QueryError("Use nextCursor with the same query and filters.")
    return signature, int(match[2]), int(match[3])


def _page(plane, tool, args, records: Iterable[_Record], *, fits=_fits):
    signature, index, offset = _cursor_position(plane, tool, args)
    limit = args.get("limit", _MAX_PAGE_ITEMS)
    window = list(islice(records, index, index + limit + 1))
    if not window and "cursor" in args:
        raise QueryError("Cursor is outside this query.")

    items, ranges = [], []

    def render(item, record, *, position, start, end, total, next_index, next_offset=0):
        has_next = next_index < index + len(window)
        cursor = f"{signature}:{next_index}:{next_offset}" if has_next else None
        covered = {**record.identity, "itemIndex": position, "start": start, "end": end, "total": total}
        result = {"items": [*items, item], "nextCursor": cursor}
        if tool == "query_micro_tasks":
            result["coverage"] = [*ranges, covered]
        return result, covered

    result = {"items": [], "nextCursor": None}
    if tool == "query_micro_tasks":
        result["coverage"] = []
    for position, record in enumerate(window[:limit], start=index):
        item = record.item()
        raw = canonical_json(item)
        total = len(raw)
        if offset >= total:
            raise QueryError("Cursor offset is outside this item.")
        candidate, covered = render(item, record, position=position, start=0, end=total,
                                    total=total, next_index=position + 1)
        if not offset and fits(candidate, {"tool": tool, "ranges": [*ranges, covered]}):
            items.append(item)
            ranges.append(covered)
            result = candidate
            continue
        if items:
            break

        def fragment(end):
            piece = {**record.identity, "fragment": {
                "itemIndex": position, "offset": offset, "end": end,
                "totalCharacters": total, "text": raw[offset:end],
            }}
            return render(piece, record, position=position, start=offset, end=end, total=total,
                          next_index=position + 1 if end == total else position,
                          next_offset=0 if end == total else end)

        def fragment_fits(end):
            page, covered = fragment(end)
            return fits(page, {"tool": tool, "ranges": [*ranges, covered]})

        end = _prefix(offset, min(total, offset + _TEXT_LIMIT), fragment_fits)
        result, covered = fragment(end)
        ranges.append(covered)
        break
    return deepcopy(result), {"tool": tool, "ranges": ranges}


def kind_id(kind):
    """The maintenance identifier of a frozen harness, model and effort."""
    # quote always leaves tilde unescaped. Encode it to keep the key alphabet fixed.
    return ":".join(quote(kind[key], safe="-_.").replace("~", "%7E") for key in _KIND)


def _by_id(rows, key):
    return {row[key]: row for row in rows}


def _require_ids(index, identifiers, field):
    if any(identifier not in index for identifier in identifiers):
        raise QueryError(f"{field} contains an identifier outside this snapshot.")


def _domain_tree(rows, *, maintenance):
    if not maintenance:
        return _by_id(rows, "nodeId")
    domains = {
        row["node_id"]: {
            "nodeId": row["node_id"], "name": row["name"], "definition": row["definition"],
            "parentId": row["parent_id"], "mergedIntoId": row["merged_into_id"],
        }
        for row in rows
    }
    retained = {}

    def target(identifier):
        path = []
        while identifier is not None and domains[identifier]["mergedIntoId"] is not None:
            if identifier in retained:
                identifier = retained[identifier]
                break
            path.append(identifier)
            identifier = domains[identifier]["mergedIntoId"]
        retained.update(dict.fromkeys(path, identifier))
        return identifier

    for row in domains.values():
        if row["mergedIntoId"] is not None:
            target(row["nodeId"])
    for row in domains.values():
        row["mergedIntoId"] = retained.get(row["nodeId"])
        if row["mergedIntoId"] is None:
            row["parentId"] = retained.get(row["parentId"], row["parentId"])
    return domains


class SnapshotQuery:
    """Expose only the tools and data bound to one frozen execution input."""

    def __init__(self, snapshot: dict, *, plane: str = "routing"):
        self._snapshot = snapshot
        self._plane = plane
        self._tools = _TOOL_SETS[plane].copy()
        self._domains = _domain_tree(snapshot["domainNodes"], maintenance=plane == "maintenance")
        if plane == "routing":
            if not (snapshot.get("exploration") or {}).get("quota"):
                del self._tools["query_explore"]
            self._buddies = _by_id(snapshot["buddies"], "buddyId")
            self._preferences = _by_id(snapshot["preferences"], "buddyId")
        else:
            self._runs = _by_id(snapshot["microTasks"], "runId")
            self._classification = set(snapshot["classificationRunIds"])
            self._kinds = _by_id(snapshot["kinds"], "kindId")
            self._kinds.update({kind_id(row): row for row in snapshot["comparisonStatistics"]})

    def tools(self):
        return deepcopy(list(self._tools.values()))

    def call(self, tool, args, *, fits=None):
        if not isinstance(tool, str) or tool not in self._tools:
            raise QueryError("Unknown tool for this snapshot.")
        _validate(args, self._tools[tool]["inputSchema"])
        check = _fits if fits is None else fits
        if tool == "query_task":
            return self._task(args, fits=check)
        records = getattr(self, "_" + tool)(args)
        return _page(self._plane, tool, args, records, fits=check)

    def _task(self, args, *, fits=_fits):
        text = self._snapshot["task"]
        total = len(text)
        total_bytes = len(text.encode("utf-8"))
        start = args.get("offset", 0)
        if start > total:
            raise QueryError("Offset is outside the task text.")

        def render(end):
            remaining = {"start": end, "end": total} if end < total else None
            return {"text": text[start:end], "offset": start, "nextOffset": end if remaining else None,
                    "totalCharacters": total, "totalBytes": total_bytes, "remaining": remaining}

        def task_fits(end):
            ranges = [{"start": start, "end": end, "total": total}] if start < end else []
            return fits(render(end), {"tool": "query_task", "ranges": ranges})

        end = _prefix(start, min(total, start + args.get("limit", _TEXT_LIMIT)), task_fits)
        ranges = [{"start": start, "end": end, "total": total}] if start < end else []
        return render(end), {"tool": "query_task", "ranges": ranges}

    def _query_domains(self, args):
        maintenance = self._plane == "maintenance"
        include_merged = args.get("includeMerged", False)
        parent = args.get("parentId")
        if parent is not None:
            _require_ids(self._domains, [parent], "parentId")
            if self._domains[parent].get("mergedIntoId") and not include_merged:
                raise QueryError("parentId is merged. Set includeMerged to query it.")
        search = args.get("search", "").casefold()
        nodes = [row for row in self._domains.values() if include_merged or not row.get("mergedIntoId")]
        children = Counter(row["parentId"] for row in nodes)
        for row in nodes:
            if ("parentId" in args or (not maintenance and not search)) and row["parentId"] != parent:
                continue
            if search and not any(search in row[key].casefold() for key in ("nodeId", "name", "definition")):
                continue
            fields = {key: row[key] for key in ("name", "definition", "parentId")}
            if maintenance:
                fields["mergedIntoId"] = row["mergedIntoId"]
            yield _Record({"nodeId": row["nodeId"]}, {**fields, "childCount": children[row["nodeId"]]})

    def _query_compare(self, args):
        domain = args.get("domainNodeId")
        if domain is not None:
            _require_ids(self._domains, [domain], "domainNodeId")
        task_type = args.get("taskType")
        if task_type is not None and domain is None:
            raise QueryError("A task type requires domainNodeId.")
        for buddy_id, buddy in self._buddies.items():
            preference = self._preferences.get(buddy_id)
            if preference is not None:
                note = preference["note"] or ""
                preference = {"mode": preference["mode"], "notePreview": note[:160], "hasLongNote": len(note) > 160}
            statistics = buddy["statistics"]
            profile = buddy["modelProfile"]
            yield _Record({"buddyId": buddy_id}, {
                **{key: buddy[key] for key in _IDENTITY}, "billing": buddy["billing"],
                "load": buddy["load"], "preference": preference, "hasSummary": buddy["summary"] is not None,
                "hasProfile": profile is not None,
                "profilePositioning": profile["positioning"] if profile is not None else None,
                "statistics": {
                    "overall": statistics["overall"], "domain": statistics["byDomain"].get(domain),
                    "cell": statistics["byDomainTaskType"].get(domain, {}).get(task_type),
                },
            })

    def _query_explore(self, args):
        domain = args.get("domainNodeId")
        if domain is not None:
            _require_ids(self._domains, [domain], "domainNodeId")
            while self._domains[domain]["parentId"] is not None:
                domain = self._domains[domain]["parentId"]
        groups = {}
        for buddy_id, buddy in self._buddies.items():
            identity = tuple(buddy[key] for key in _KIND)
            if identity not in groups:
                statistics = buddy["statistics"]
                at_level = statistics["overall"] if domain is None else statistics["byDomain"].get(domain, {})
                groups[identity] = {"domainNodeId": domain, "samples": at_level.get("samples", 0), "buddyIds": []}
            groups[identity]["buddyIds"].append(buddy_id)
        for identity, group in sorted(groups.items(), key=lambda pair: (pair[1]["samples"], pair[0])):
            yield _Record(dict(zip(_KIND, identity, strict=True)), group)

    def _query_details(self, args):
        identifiers = args["buddyIds"]
        _require_ids(self._buddies, identifiers, "buddyIds")
        for buddy_id in identifiers:
            buddy = self._buddies[buddy_id]
            for field in args.get("include", _DETAILS):
                value = buddy[field] if field != "preference" else self._preferences.get(buddy_id)
                yield _Record({"buddyId": buddy_id, "field": field}, value, wrap=True)

    def _run_index(self, run_id, run):
        result = run["result"]
        participating = {kind_id(turn["kind"]) for turn in result["turns"]
                         if turn["kind"] is not None and turn["failureCategory"] == "ability"}
        return {
            "title": run["title"], "classification": run["classification"],
            "needsClassification": run_id in self._classification, "kindIds": sorted(participating),
            "state": result["state"], "reworkRounds": result["continuationRounds"],
        }

    def _query_batch(self, args):
        for run_id, run in self._runs.items():
            yield _Record({"runId": run_id}, self._run_index(run_id, run))

    def _query_micro_tasks(self, args):
        identifiers = args["runIds"]
        _require_ids(self._runs, identifiers, "runIds")
        for run_id in identifiers:
            run = self._runs[run_id]
            result = run["result"]
            for section in args.get("sections", MAINTENANCE_SECTIONS):
                if section == "task":
                    value = {**self._run_index(run_id, run), "project": run["project"], "task": run["task"]}
                elif section == "acceptanceAndIntegration":
                    value = {key: item for key, item in result.items()
                             if key not in ("turns", "continuations", "hostBoundaryFacts")}
                else:
                    value = result[section]
                yield _Record({"runId": run_id, "section": section}, value, wrap=True)

    def _query_kinds(self, args):
        identifiers = args.get("kindIds", self._kinds)
        _require_ids(self._kinds, identifiers, "kindIds")
        domains = args.get("domainNodeIds")
        if domains is not None:
            _require_ids(self._domains, domains, "domainNodeIds")
        include = args.get("include", ("unreadRunIds",))
        for identifier in identifiers:
            kind = self._kinds[identifier]
            identity = {key: kind[key] for key in (*_KIND, "modelDisplayName", "modelNames")}
            target = "unreadRunIds" in kind
            yield _Record({"kindId": identifier, "field": "identity"}, {**identity, "needsSummary": target}, wrap=True)
            for field in include:
                if field not in kind:
                    continue
                value = kind[field]
                if field == "statistics" and domains is not None:
                    value = {**value, **{
                        key: {domain: value[key][domain] for domain in domains if domain in value[key]}
                        for key in ("byDomain", "byDomainTaskType")
                    }}
                yield _Record({"kindId": identifier, "field": field}, value, wrap=True)


def _section_prefixes(events: Iterable[dict], *, issued_only=False):
    """Find the first uncovered character of each delivered section."""
    by_section = defaultdict(list)
    for event in events:
        if not isinstance(event, dict) or not isinstance(event.get("tool"), str):
            raise QueryError("Invalid snapshot delivery event.")
        if event["tool"] != "query_micro_tasks":
            if issued_only:
                raise QueryError("Expected only query_micro_tasks delivery events.")
            continue
        ranges = event.get("ranges")
        if (set(event) != {"tool", "ranges"} or not isinstance(ranges, list)
                or not 1 <= len(ranges) <= _MAX_PAGE_ITEMS):
            raise QueryError("Invalid micro task delivery ranges.")
        previous_index = None
        seen = set()
        for span in ranges:
            if not isinstance(span, dict) or set(span) != {"runId", "section", "itemIndex", "start", "end", "total"}:
                raise QueryError("Invalid micro task delivery range.")
            run_id, section = span["runId"], span["section"]
            start, end, total = span["start"], span["end"], span["total"]
            if (not isinstance(run_id, str) or not run_id or section not in MAINTENANCE_SECTIONS
                    or any(type(index) is not int for index in (span["itemIndex"], start, end, total))
                    or not 0 <= span["itemIndex"] < MAX_QUERY_IDS * len(MAINTENANCE_SECTIONS)
                    or not 0 <= start < end <= total):
                raise QueryError("Invalid micro task delivery bounds.")
            key = (run_id, section)
            index = span["itemIndex"]
            if (key in seen or (previous_index is not None and index != previous_index + 1)
                    or (len(ranges) > 1 and (start != 0 or end != total))):
                raise QueryError("Invalid micro task delivery page.")
            seen.add(key)
            previous_index = index
            by_section[key].append((start, end, total))

    for key, spans in by_section.items():
        total = spans[0][2]
        if any(span[2] != total for span in spans):
            raise QueryError("Micro task delivery ranges disagree on section length.")
        covered = 0
        for start, end, _ in sorted(spans):
            if start > covered:
                break
            covered = max(covered, end)
        yield key, covered, total


def verified_coverage(plane: str, events: Iterable[dict]) -> dict[str, set[str]]:
    """Reduce ledger events already verified against native root call evidence."""
    complete = defaultdict(set)
    if plane == "maintenance":
        for (run_id, section), covered, total in _section_prefixes(events):
            if covered == total:
                complete[run_id].add(section)
    return dict(complete)


class MaintenanceCoverage:
    """Coverage of frozen sections, with cursors independent of the original query filters."""

    def __init__(self, snapshot, events, surviving_run_ids, *, issued_only=False):
        self._complete = defaultdict(set)
        self._prefixes = {}
        self._surviving = set(surviving_run_ids)
        query = SnapshotQuery(snapshot, plane="maintenance")
        for (run_id, section), covered, total in _section_prefixes(events, issued_only=issued_only):
            args = {"runIds": [run_id], "sections": [section]}
            record, = query._query_micro_tasks(args)
            if total != len(canonical_json(record.item())):
                raise QueryError("Micro task delivery length differs from the frozen section.")
            if covered == total:
                self._complete[run_id].add(section)
            elif covered > 0:
                signature, _, _ = _cursor_position("maintenance", "query_micro_tasks", args)
                self._prefixes[run_id, section] = f"{signature}:0:{covered}"

    def gaps(self, run_ids, sections):
        """Yield the unread sections of the requested surviving tasks."""
        for run_id in run_ids:
            if run_id in self._surviving:
                for section in sections:
                    if section not in self._complete.get(run_id, ()):
                        yield run_id, section, self._prefixes.get((run_id, section))



def maintenance_coverage(snapshot: dict, events: Iterable[dict], *,
                         surviving_run_ids: Iterable[str] | None = None) -> tuple[set[str], set[str]]:
    """Return eligible classification and summary targets from verified deliveries."""
    surviving = ({task["runId"] for task in snapshot["microTasks"]}
                 if surviving_run_ids is None else set(surviving_run_ids))
    coverage = MaintenanceCoverage(snapshot, events, surviving)
    classifications = {run_id for run_id in snapshot["classificationRunIds"]
                       if run_id in surviving and not any(coverage.gaps([run_id], ("task",)))}
    summaries = set()
    for kind in snapshot["kinds"]:
        unread = surviving.intersection(kind["unreadRunIds"])
        if unread and not any(coverage.gaps(unread, MAINTENANCE_SECTIONS)):
            summaries.add(kind["kindId"])
    return classifications, summaries
