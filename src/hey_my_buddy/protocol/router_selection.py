"""Frozen Router selection output and validation for both sides of an attempt."""
from __future__ import annotations

import json
import unicodedata
from collections.abc import Iterator
from itertools import chain, islice

from ..errors import BoardError
from ..json_codec import decode_strict_json
from .structured_schema import schema_issues
from .task_types import TASK_TYPES

VERSION = 20
MAX_NOTE = 2000
MAX_REFERENCES = 32
MAX_FORMAT_ERRORS = 32
FORMAT_ERROR_CODES = frozenset({"answer-empty", "answer-invalid-json", "answer-shape"})
HARD_BOUND_CODES = frozenset({"router-out-of-bounds", "router-evidence-out-of-bounds", "router-exploration-out-of-bounds"})


def routing_facts(request: dict, *, actor: dict | None = None, include_wait: bool = True) -> dict:
    facts = {key: request.get(key) for key in
             ("routerBuddyIds", "routerIdentities", "routerBuddyId", "routerBuddy", "routerIndex",
              "routingMode", "routerRetryIntervalSeconds", "routerProblem")}
    if include_wait:
        facts["modelResponseWaitSeconds"] = request.get("modelResponseWaitSeconds")
    if actor is not None:
        facts.update(routerBuddyId=actor["buddyId"], routerBuddy=actor["buddy"],
                     routerIndex=actor["routerIndex"])
    return facts


def answer_schema(routing_mode: str, *, exploration: bool = False) -> dict:
    """The model's protocol-20 schema, independent of the frozen input's size."""
    evidence_kinds = ["preference", "statistics", "summary"]
    if routing_mode == "review":
        evidence_kinds.append("file")
    properties = {
        "buddyId": {"type": ["string", "null"], "minLength": 1},
        "domainNodeId": {"type": ["string", "null"], "minLength": 1},
        "taskType": {"type": "string", "enum": list(TASK_TYPES)},
        "note": {"type": "string", "minLength": 1, "maxLength": MAX_NOTE},
        "evidence": {"type": "array", "maxItems": MAX_REFERENCES, "items": {
            "type": "object", "additionalProperties": False, "required": ["kind", "ref"],
            "properties": {
                "kind": {"type": "string", "enum": evidence_kinds},
                "ref": {"type": "string", "minLength": 1, "maxLength": 1024},
            },
        }},
    }
    if exploration:
        properties["preferredBuddyId"] = {"type": "string", "minLength": 1}
    return {"type": "object", "additionalProperties": False,
            "required": list(properties), "properties": properties}


def _file_evidence(answer: dict) -> Iterator[tuple[int, dict]]:
    entries = answer.get("evidence")
    if isinstance(entries, list):
        for index, entry in enumerate(entries):
            if isinstance(entry, dict) and entry.get("kind") == "file":
                yield index, entry


def _relative_path(ref: str) -> bool:
    parts = ref.split("/")
    if ref.startswith("/") or ".." in parts or "\\" in ref or ":" in ref or ref == ".":
        return False
    return "." not in parts and all(unicodedata.category(char) != "Cc" for char in ref)


def reselected(answer: dict) -> bool:
    """A quota-bearing, validated answer can keep, reselect or abstain."""
    return ("preferredBuddyId" in answer and answer["buddyId"] is not None
            and answer["buddyId"] != answer["preferredBuddyId"])


def first_level_domain(nodes: list[dict], node_id: str) -> str:
    """The first-level ancestor of one node in the frozen domain tree."""
    parents = {node["nodeId"]: node["parentId"] for node in nodes}
    while parents[node_id] is not None:
        node_id = parents[node_id]
    return node_id


def validate_exploration(document: dict, answer: dict) -> None:
    """Selection can move only to a kind with strictly fewer frozen samples."""
    if not reselected(answer):
        return
    domain = answer["domainNodeId"]
    level = first_level_domain(document["domainNodes"], domain) if domain is not None else None
    samples_by_kind = {}
    kind_by_id = {}
    for candidate in document["buddies"]:
        kind = (candidate["harness"], candidate["modelId"], candidate["effort"])
        kind_by_id[candidate["buddyId"]] = kind
        statistics = candidate.get("statistics") or {}
        stats = (statistics.get("overall", {}) if level is None else
                 (statistics.get("byDomain") or {}).get(level, {}))
        samples_by_kind.setdefault(kind, stats.get("samples", 0))
    chosen = kind_by_id[answer["buddyId"]]
    preferred = kind_by_id[answer["preferredBuddyId"]]
    if samples_by_kind[chosen] >= samples_by_kind[preferred]:
        raise BoardError("router-exploration-out-of-bounds", "The selected kind must have fewer samples than the preferred kind",
                         bound="not-less-sampled")


def _hard_bounds(answer: dict, document: dict, *, exploration: bool) -> None:
    candidates = {candidate["buddyId"] for candidate in document["buddies"]}
    for field in ("buddyId", "preferredBuddyId"):
        value = answer.get(field)
        if isinstance(value, str) and value and value not in candidates:
            raise BoardError("router-out-of-bounds", f"{field} is outside the frozen candidates",
                             field=f"$.{field}", bound="not-a-candidate")
    domain = answer.get("domainNodeId")
    domain_valid = domain is None and "domainNodeId" in answer
    if isinstance(domain, str) and domain:
        if not any(node["nodeId"] == domain for node in document["domainNodes"]):
            raise BoardError("router-out-of-bounds", "domainNodeId is outside the frozen active domains",
                             field="$.domainNodeId", bound="not-an-active-domain")
        domain_valid = True
    if document["routingMode"] == "fast":
        file_entry = next(_file_evidence(answer), None)
        if file_entry is not None:
            index, _ = file_entry
            raise BoardError("router-evidence-out-of-bounds", "Fast routing cannot cite repository files",
                             field=f"$.evidence[{index}].kind")
    if (exploration and domain_valid and isinstance(answer.get("buddyId"), str) and answer["buddyId"]
            and isinstance(answer.get("preferredBuddyId"), str) and answer["preferredBuddyId"]):
        validate_exploration(document, answer)


def _valid_text(text: str) -> bool:
    try:
        text.encode("utf-8")
    except UnicodeEncodeError:
        # JSON text may convey an escaped lone surrogate.
        return False
    return True


def _text_issues(answer: object) -> Iterator[dict]:
    if not isinstance(answer, dict):
        return
    note = answer.get("note")
    if isinstance(note, str) and len(note) <= MAX_NOTE and not _valid_text(note):
        yield {"field": "$.note", "reason": "invalid-unicode", "message": "Use valid Unicode text"}
    entries = answer.get("evidence")
    if isinstance(entries, list):
        for index, entry in enumerate(entries[:MAX_REFERENCES]):
            ref = entry.get("ref") if isinstance(entry, dict) else None
            if not isinstance(ref, str) or not 0 < len(ref) <= 1024:
                continue
            field = f"$.evidence[{index}].ref"
            if not _valid_text(ref):
                yield {"field": field, "reason": "invalid-unicode", "message": "Use valid Unicode text"}
            elif entry.get("kind") == "file" and not _relative_path(ref):
                yield {"field": field, "reason": "relative-path",
                       "message": "Use only a checkout-relative path without a line number"}


def _format_error(code: str, errors: list[dict]) -> BoardError:
    first = errors[0]
    location = {key: first[key] for key in ("field", "line", "column") if key in first}
    return BoardError(code, first["message"], errors=errors, **location)


def validate_answer(raw: object, document: dict) -> dict:
    """Validate protocol 20, checking hard bounds before correctable format issues."""
    if raw is None:
        raise BoardError("router-no-answer", "The native run returned no Router answer")
    if isinstance(raw, str):
        if not raw.strip():
            raise _format_error("answer-empty", [{"field": "$", "reason": "empty", "message": "The Router answer is empty"}])
        try:
            raw = decode_strict_json(raw)
        except json.JSONDecodeError as error:
            raise _format_error("answer-invalid-json", [{"line": error.lineno, "column": error.colno,
                                "reason": "invalid-json", "message": error.msg}]) from None
        except (ValueError, RecursionError):
            raise _format_error("answer-invalid-json", [{"field": "$", "reason": "invalid-json",
                                "message": "The Router answer is not valid JSON"}]) from None
    exploration = (document.get("exploration") or {}).get("quota") is True
    if isinstance(raw, dict):
        _hard_bounds(raw, document, exploration=exploration)
    schema = answer_schema(document["routingMode"], exploration=exploration)
    shape_issues = ({"field": field, "reason": keyword, "message": f"{field} violates the JSON Schema ({keyword})"}
                    for field, keyword in schema_issues(raw, schema))
    errors = list(islice(chain(shape_issues, _text_issues(raw)), MAX_FORMAT_ERRORS))
    if errors:
        raise _format_error("answer-shape", errors)
    return {key: raw[key] for key in schema["properties"]}
