"""Fixed public model-profile data; web text never becomes an instruction."""
from __future__ import annotations

from datetime import date
import json
import re
from urllib.parse import urlsplit

from ...json_codec import decode_strict_json
from ...protocol.structured_schema import schema_errors
from ..evaluation.models import CURRENT_CORRESPONDENCES, SERVICE_FIELDS, append_correspondence, create_model, model_view


def _object(properties):
    return {"type": "object", "properties": properties, "required": list(properties), "additionalProperties": False}


def _text(limit=2000):
    return {"type": "string", "minLength": 1, "maxLength": limit}


def answer_schema():
    source = {"sourceUrl": _text(2048), "sourceDate": _text(10)}
    return _object({
        "positioning": _text(),
        "claims": {"type": "array", "maxItems": 64, "items": _object({
            "polarity": {"type": "string", "enum": ["strength", "weakness"]},
            "claim": _text(), **source,
            "sourceKind": {"type": "string", "enum": ["vendor", "third-party"]}})},
        "correspondences": {"type": "array", "maxItems": 128, "items": _object({
            **{key: _text(256) for key in SERVICE_FIELDS},
            "relation": {"type": "string", "enum": ["same", "successor"]}, **source})},
    })


def build_packet(connection, model_id):
    return {"model": model_view(connection, model_id), "candidates": [
        {**{key: row[key] for key in SERVICE_FIELDS}, "modelId": row["model_id"]}
        for row in connection.execute(
            'SELECT m.* FROM (' + CURRENT_CORRESPONDENCES + ') m WHERE m.model_id<>?'
            ' AND EXISTS(SELECT 1 FROM buddies b WHERE b.harness=m.harness AND b.provider=m.provider AND b.model=m.model)'
            ' ORDER BY m.harness,m.provider,m.model', (model_id,))]}


def _structure_errors(value, schema, path="answer"):
    # Apply the shared schema subset one node at a time so every error identifies
    # the field or list entry the Router must correct.
    shallow = {key: item for key, item in schema.items() if key not in ("properties", "items", "required")}
    if "properties" in schema:
        shallow["properties"] = {key: {} for key in schema["properties"]}
    errors = [f"{path}: {error}" for error in schema_errors(value, shallow)]
    if isinstance(value, dict) and schema.get("type") == "object":
        for key in schema["required"]:
            if key not in value:
                errors.append(f"{path}.{key}: required")
        for key, sub in schema["properties"].items():
            if key in value:
                errors.extend(_structure_errors(value[key], sub, f"{path}.{key}"))
    if isinstance(value, list) and schema.get("type") == "array":
        for index, item in enumerate(value):
            errors.extend(_structure_errors(item, schema["items"], f"{path}[{index}]"))
    if isinstance(value, str) and not value.strip():
        errors.append(f"{path}: must contain text")
    return errors


def validate_answer(packet, raw):
    if isinstance(raw, str):
        try:
            raw = decode_strict_json(raw)
        except (ValueError, RecursionError):
            return None, ["answer: invalid JSON"]
    errors = _structure_errors(raw, answer_schema())
    if errors:
        return None, errors
    candidates = {tuple(item[key] for key in SERVICE_FIELDS) for item in packet["candidates"]}
    seen = set()
    for group in ("claims", "correspondences"):
        for index, item in enumerate(raw[group]):
            path = f"answer.{group}[{index}]"
            try:
                url = urlsplit(item["sourceUrl"])
                valid = url.scheme in ("http", "https") and bool(url.hostname) and not any(c.isspace() for c in item["sourceUrl"])
            except ValueError:
                valid = False
            if not valid:
                errors.append(f"{path}.sourceUrl: must be an absolute http(s) URL")
            value = item["sourceDate"]
            try:
                if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value) is None:
                    raise ValueError
                date.fromisoformat(value)
            except ValueError:
                errors.append(f"{path}.sourceDate: must be a calendar date YYYY-MM-DD")
            if group == "correspondences":
                key = tuple(item[field] for field in SERVICE_FIELDS)
                if key not in candidates:
                    errors.append(f"{path}: model service is not in candidates")
                if key in seen:
                    errors.append(f"{path}: model service occurs more than once")
                seen.add(key)
    return (None if errors else raw), errors


def apply_answer(connection, *, model_id, run_id, answer, now):
    # Validation is all-or-nothing for this one model, including its correspondences.
    connection.execute('DELETE FROM model_profile_claims WHERE model_id=?', (model_id,))
    connection.execute(
        'INSERT INTO model_profiles(model_id,positioning,built_at,profile_run_id) VALUES(?,?,?,?)'
        ' ON CONFLICT(model_id) DO UPDATE SET positioning=excluded.positioning,built_at=excluded.built_at,profile_run_id=excluded.profile_run_id',
        (model_id, answer["positioning"], now, run_id))
    connection.executemany(
        'INSERT INTO model_profile_claims(model_id,ordinal,polarity,claim,source_url,source_date,source_kind) VALUES(?,?,?,?,?,?,?)',
        [(model_id, index, item["polarity"], item["claim"], item["sourceUrl"], item["sourceDate"], item["sourceKind"])
         for index, item in enumerate(answer["claims"])])
    for item in answer["correspondences"]:
        # A successor starts a new group from now; earlier samples stay frozen.
        target = model_id if item["relation"] == "same" else create_model(connection, item["model"])
        append_correspondence(connection, tuple(item[field] for field in SERVICE_FIELDS), target, now, 'profile',
                              evidence_url=item["sourceUrl"], evidence_date=item["sourceDate"])


def profile_view(connection, model_id):
    row = connection.execute('SELECT * FROM model_profiles WHERE model_id=?', (model_id,)).fetchone()
    if row is None:
        return None
    return {"positioning": row["positioning"], "builtAt": row["built_at"], "profileRunId": row["profile_run_id"],
            "claims": [{"polarity": item["polarity"], "claim": item["claim"], "sourceUrl": item["source_url"],
                        "sourceDate": item["source_date"], "sourceKind": item["source_kind"]}
                       for item in connection.execute('SELECT * FROM model_profile_claims WHERE model_id=? ORDER BY ordinal', (model_id,))]}


def console_model_view(connection, model_id):
    from ...protocol.reasons import reason_from_json
    latest = connection.execute('SELECT p.*,b.harness,b.provider,b.model,b.effort FROM profile_runs p JOIN buddies b ON b.buddy_id=p.buddy_id WHERE p.model_id=? ORDER BY p.rowid DESC LIMIT 1', (model_id,)).fetchone()
    run = None if latest is None else {
        "profileRunId": latest['profile_run_id'], "taskId": latest['task_id'], "buddyId": latest['buddy_id'],
        "buddy": {key: latest[key] for key in ('harness', 'provider', 'model', 'effort')},
        "state": latest['state'], "reason": reason_from_json(latest['reason_code'], latest['reason_params_json']),
        "startedAt": latest['started_at'], "finishedAt": latest['finished_at'], "correctionRounds": latest['correction_rounds'],
        "usage": json.loads(latest['usage_json']) if latest['usage_json'] else None}
    return {**model_view(connection, model_id), "profile": profile_view(connection, model_id), "latestProfileRun": run}
