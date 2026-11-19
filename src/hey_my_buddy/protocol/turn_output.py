"""Worker turn outcome contracts for new delivery and sealed history."""
from __future__ import annotations

from ..json_codec import canonical_json

HISTORICAL_TURN_OUTPUT_VERSION = 1
TURN_OUTPUT_VERSION = 2
MAX_OUTCOME_BYTES = 65536


def _text(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip()) and "\0" not in value


def _strings(value: object) -> bool:
    return isinstance(value, list) and all(_text(item) for item in value)


def validate_outcome(outcome: object, *, delegation_authority: bool = False) -> str | None:
    """Validate the outcome shared by the harness and service receipt boundaries."""
    fields = {"disposition", "summary", "remaining", "decisions", "artifacts", "request"}
    if not isinstance(outcome, dict) or set(outcome) != fields:
        return "the outcome must contain exactly the current outcome fields"
    dispositions = ("completed", "attention", "waiting") if delegation_authority else ("completed", "attention")
    if outcome["disposition"] not in dispositions:
        return "the turn outcome requires a valid disposition"
    if not _text(outcome["summary"]):
        return "the turn summary is blank or contains NUL"
    if not _strings(outcome["remaining"]) or not _strings(outcome["decisions"]):
        return "remaining and decisions must be nonblank string arrays without NUL"
    artifacts = outcome["artifacts"]
    if not isinstance(artifacts, list):
        return "artifacts must be an array"
    if any(not (_text(item) or isinstance(item, dict) and bool(item)) for item in artifacts):
        return "artifacts must contain nonempty references"
    try:
        if len(canonical_json(outcome).encode()) > MAX_OUTCOME_BYTES:
            return "the outcome exceeds its byte bound"
    except (TypeError, ValueError, RecursionError):
        return "the outcome must contain finite JSON values"
    request = outcome["request"]
    if outcome["disposition"] in ("completed", "waiting"):
        return None if request is None else f"a {outcome['disposition']} outcome requires request: null"
    required = {"summary", "attempted", "neededWork", "expectedArtifacts", "acceptance"}
    if not isinstance(request, dict) or set(request) != required:
        return "an attention outcome requires exactly the current request fields"
    if any(not _text(request[key]) for key in ("summary", "attempted", "neededWork", "acceptance")):
        return "the turn request requires nonblank summary, attempted, neededWork and acceptance"
    if not _strings(request["expectedArtifacts"]):
        return "the turn request references are invalid"
    return None


def validate_historical_outcome(outcome: object, *, delegation_authority: bool = False) -> str | None:
    """Apply the original service validation to an already sealed v1 outcome."""
    if not isinstance(outcome, dict):
        return "the turn record carries no outcome object"
    disposition = outcome.get("disposition")
    if disposition not in ("completed", "assistance", "attention", "waiting"):
        return "the historical turn requires a valid disposition"
    if disposition == "waiting":
        if not delegation_authority:
            return "only a Lead turn can return waiting"
        if outcome.get("request") is not None:
            return "a waiting turn cannot name a request"
    if not isinstance(outcome.get("summary"), str) or not outcome["summary"].strip():
        return "the turn outcome has no summary"
    if disposition in ("assistance", "attention"):
        request = outcome.get("request")
        if not isinstance(request, dict):
            return "an assistance or attention turn requires a request object"
        for field in ("summary", "attempted", "acceptance"):
            if not isinstance(request.get(field), str) or not request[field].strip():
                return f"the turn request requires a nonempty {field}"
    return None
