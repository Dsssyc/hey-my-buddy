"""Validated domain schemas for every C-Two board operation.

Each operation gets an explicit request validator and a canonical normalized
specification. Nothing reaches the store before it has been validated here, and
nothing is written through ``dispatch(method, json)``: the C-Two contract names one
method per operation (see ``contracts.py``).
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

from ..paths import is_absolute_path
from .harness_names import HARNESS_NAMES, ADAPTER_NAMES as ADAPTERS
from ..blackboard.store.db import canonical_json, sha256_text
from ..errors import BoardError

FINGERPRINT_VERSION_CURRENT = 2

MAX_TASK_BYTES = 1024 * 1024
MAX_REQUEST_ID = 128
MAX_METADATA = 256
MIN_EXPECTED_SECONDS = 1
MAX_EXPECTED_SECONDS = 86400
DEFAULT_EXPECTED_SECONDS = 1800
MAX_QUESTION_BYTES = 4000
MAX_ANSWER_BYTES = 4000
MAX_INQUIRIES_PER_RUN = 32
MAX_NOTE_BYTES = 10000
MAX_BODY_BYTES = 64 * 1024
MAX_ARGV = 256
MAX_ARG_BYTES = 32 * 1024
MAX_RESOURCES = 32
MAX_CAPABILITIES = 32

CODING_HARNESSES = HARNESS_NAMES
BUDDY_FIELDS = ("harness", "provider", "model", "effort")
INQUIRY_ID_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
RESOURCE_ID_PATTERN = re.compile(r"^[A-Za-z0-9._:/@+-]{1,512}$")

# -- micro-task workflow bounds ------------------------------------------------
MAX_WORKFLOW_HELPERS = 8
MAX_WORKFLOW_ARTIFACTS = 32

#: Sentinel accepted only from the authenticated console layer and validated
#: against a server-side registry, never trusted from a C-Two/JSON caller.
CONSOLE_AUTHORITY_FIELD = "consoleAuthority"


#: Every field ``task_submit`` accepts. Anything else is INVALID_ARGUMENT, so a
#: typo can never silently change what is executed.
SUBMIT_FIELDS = frozenset(
    {
        "requestId",
        "task",
        "cwd",
        "adapter",
        "argv",
        "model",
        "provider",
        "effort",
        "expectedSeconds",
        "requiredCapabilities",
    }
)

TERMINAL_TASK_STATES = frozenset({"completed", "concluded", "failed", "cancelled"})

def require_object(value: Any, what: str) -> dict:
    if not isinstance(value, dict):
        raise BoardError("INVALID_ARGUMENT", f"{what} must be a JSON object")
    return value


def reject_unknown(params: dict, allowed: frozenset[str] | set[str], what: str) -> None:
    unknown = sorted(set(params) - set(allowed))
    if unknown:
        raise BoardError("INVALID_ARGUMENT", f"Unknown {what} parameter: {unknown[0]}", field=unknown[0])


def optional_string(params: dict, name: str, *, max_length: int = MAX_METADATA, pattern: re.Pattern | None = None) -> str | None:
    value = params.get(name)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip() or "\0" in value:
        raise BoardError("INVALID_ARGUMENT", f"{name} must be a nonempty string")
    value = value.strip()
    if len(value) > max_length:
        raise BoardError("INVALID_ARGUMENT", f"{name} must be at most {max_length} characters")
    if pattern is not None and not pattern.match(value):
        raise BoardError("INVALID_ARGUMENT", f"{name} has an invalid format")
    return value


def required_string(params: dict, name: str, *, max_length: int = MAX_METADATA, pattern: re.Pattern | None = None) -> str:
    value = optional_string(params, name, max_length=max_length, pattern=pattern)
    if value is None:
        raise BoardError("INVALID_ARGUMENT", f"{name} is required")
    return value


def optional_bool(params: dict, name: str, default: bool) -> bool:
    value = params.get(name, default)
    if not isinstance(value, bool):
        raise BoardError("INVALID_ARGUMENT", f"{name} must be a boolean")
    return value


def optional_int(params: dict, name: str, default: int, minimum: int, maximum: int) -> int:
    value = params.get(name, default)
    if isinstance(value, bool) or not isinstance(value, int) or not (minimum <= value <= maximum):
        raise BoardError("INVALID_ARGUMENT", f"{name} must be an integer between {minimum} and {maximum}")
    return value


def expected_duration(params: dict) -> int:
    return optional_int(params, "expectedSeconds", DEFAULT_EXPECTED_SECONDS, MIN_EXPECTED_SECONDS, MAX_EXPECTED_SECONDS)


def optional_positive_int(params: dict, name: str) -> int | None:
    """An optional positive integer cursor (or row key); absent means not provided."""
    value = params.get(name)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 2**63 - 1:
        raise BoardError("INVALID_ARGUMENT", f"{name} must be a positive SQLite integer (at most 2^63-1)", field=name)
    return value


def optional_sha256(params: dict, name: str) -> str | None:
    value = params.get(name)
    if value is None:
        return None
    if not isinstance(value, str) or not SHA256_PATTERN.match(value):
        raise BoardError("INVALID_ARGUMENT", f"{name} must be a lowercase sha256 hex digest")
    return value


def string_list(params: dict, name: str, *, limit: int, pattern: re.Pattern | None = None) -> list[str]:
    value = params.get(name, [])
    if value is None:
        return []
    if not isinstance(value, list) or len(value) > limit:
        raise BoardError("INVALID_ARGUMENT", f"{name} must be a list of at most {limit} entries")
    out: list[str] = []
    for entry in value:
        if not isinstance(entry, str) or not entry.strip() or "\0" in entry:
            raise BoardError("INVALID_ARGUMENT", f"{name} entries must be nonempty strings")
        entry = entry.strip()
        if len(entry) > MAX_METADATA * 2 or (pattern is not None and not pattern.match(entry)):
            raise BoardError("INVALID_ARGUMENT", f"{name} entry has an invalid format")
        if entry not in out:
            out.append(entry)
    return out


def canonical_cwd(raw: Any) -> str:
    if not isinstance(raw, str) or not is_absolute_path(raw):
        raise BoardError("INVALID_ARGUMENT", "cwd must be an absolute path")
    try:
        resolved = Path(os.path.realpath(raw))
        if not resolved.is_dir():
            raise OSError("not a directory")
    except OSError as exc:
        raise BoardError("INVALID_ARGUMENT", "cwd must be an existing directory") from exc
    return str(resolved)


def _argv(params: dict, adapter: str) -> list[str]:
    raw = params.get("argv")
    if adapter == "command":
        if not isinstance(raw, list) or not raw or len(raw) > MAX_ARGV:
            raise BoardError("INVALID_ARGUMENT", "the command adapter requires argv with 1-256 entries")
    elif raw is not None:
        raise BoardError("INVALID_ARGUMENT", "argv is only valid for the command adapter")
    else:
        return []
    argv: list[str] = []
    for entry in raw:
        if not isinstance(entry, str) or "\0" in entry:
            raise BoardError("INVALID_ARGUMENT", "argv entries must be strings without NUL")
        if len(entry.encode("utf-8")) > MAX_ARG_BYTES:
            raise BoardError("INVALID_ARGUMENT", "an argv entry exceeds 32768 bytes")
        argv.append(entry)
    if not argv[0].strip():
        raise BoardError("INVALID_ARGUMENT", "argv[0] must be a nonempty executable")
    return argv


def normalize_spec(params: dict) -> dict:
    """Normalize the execution layer's explicit internal specification."""
    params = require_object(params, "submit request")
    reject_unknown(params, SUBMIT_FIELDS, "submit")
    request_id = required_string(params, "requestId", max_length=MAX_REQUEST_ID)
    task_text = params.get("task")
    if not isinstance(task_text, str) or not task_text.strip():
        raise BoardError("INVALID_ARGUMENT", "task must be a nonempty string")
    if len(task_text.encode("utf-8")) > MAX_TASK_BYTES:
        raise BoardError("INVALID_ARGUMENT", f"task must contain at most {MAX_TASK_BYTES} bytes")
    adapter = optional_string(params, "adapter") or "dsh"
    if adapter not in ADAPTERS:
        raise BoardError(
            "UNSUPPORTED_ADAPTER",
            f"Unknown adapter {adapter!r}; this build supports {', '.join(ADAPTERS)}",
            adapter=adapter,
        )
    spec: dict[str, Any] = {
        "adapter": adapter,
        "cwd": canonical_cwd(params.get("cwd")),
        "task": task_text,
        "expectedSeconds": expected_duration(params),
        "requiredCapabilities": string_list(params, "requiredCapabilities", limit=MAX_CAPABILITIES),
    }
    for name in ("model", "provider", "effort"):
        value = optional_string(params, name)
        if value is not None:
            spec[name] = value
    argv = _argv(params, adapter)
    if argv:
        spec["argv"] = argv
    return spec


def spec_fingerprint(spec: dict) -> str:
    """Version-2 fingerprint: the whole normalized specification."""
    return sha256_text(f"{FINGERPRINT_VERSION_CURRENT}:" + canonical_json(spec))


def encode(value: Any) -> str:
    """Serialize one response for the C-Two boundary."""
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))


def decode_request(request_json: Any, what: str = "request") -> dict:
    if not isinstance(request_json, str):
        raise BoardError("INVALID_ARGUMENT", f"{what} must be a JSON string")
    if len(request_json.encode("utf-8")) > 8 * 1024 * 1024:
        raise BoardError("MESSAGE_TOO_LARGE", "Request exceeds 8 MiB")
    try:
        value = json.loads(request_json)
    except ValueError as exc:
        raise BoardError("INVALID_ARGUMENT", f"{what} is not valid JSON") from exc
    return require_object(value, what)


def bounded_text(params: dict, name: str, *, max_bytes: int, allow_empty: bool = False) -> tuple[str, int]:
    value = params.get(name)
    if not isinstance(value, str) or (not allow_empty and not value.strip()):
        raise BoardError("INVALID_ARGUMENT", f"{name} must be a nonempty string")
    size = len(value.encode("utf-8"))
    if size > max_bytes:
        raise BoardError("INVALID_ARGUMENT", f"{name} must be at most {max_bytes} UTF-8 bytes")
    return value, size


# -- micro-task workflow -------------------------------------------------------
def normalize_access(intent: dict) -> tuple[str, str | None]:
    """Validate the Worker access choice and its full-access note."""
    access = optional_string(intent, "access") or "write"
    if "access" in intent and intent["access"] is None:
        raise BoardError("INVALID_ARGUMENT", "executionWorkspace.access cannot be null",
                         field="executionWorkspace.access")
    if access not in ("read", "write", "full"):
        raise BoardError("INVALID_ARGUMENT", "executionWorkspace.access must be 'read', 'write' or 'full'",
                         field="executionWorkspace.access")
    if access == "full":
        note, _ = bounded_text(intent, "accessNote", max_bytes=MAX_NOTE_BYTES)
        raw_note = intent["accessNote"]
        if raw_note.splitlines() != [raw_note]:
            raise BoardError("INVALID_ARGUMENT", "executionWorkspace.accessNote must be a single line",
                             field="executionWorkspace.accessNote")
        return access, note
    if "accessNote" in intent:
        raise BoardError("INVALID_ARGUMENT", "executionWorkspace.accessNote requires full access",
                         field="executionWorkspace.accessNote")
    return access, None




















def buddy_constraints(spec: dict) -> dict:
    return {key: spec[key] for key in BUDDY_FIELDS if key in spec}


def normalize_buddy(value: Any) -> dict:
    value = require_object(value, "buddy")
    reject_unknown(value, set(BUDDY_FIELDS), "buddy")
    result = {key: required_string(value, key, max_length=256) for key in BUDDY_FIELDS}
    if result["harness"] not in CODING_HARNESSES:
        raise BoardError("INVALID_ARGUMENT", "buddy requires a coding harness: dsh, zcode, codex or claude")
    return result
