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

from .db import canonical_json, node_json, sha256_text
from .errors import BoardError

FINGERPRINT_VERSION_CURRENT = 2
FINGERPRINT_VERSION_LEGACY = 1

MAX_TASK_BYTES = 1024 * 1024
MAX_REQUEST_ID = 128
MAX_METADATA = 256
MIN_TIMEOUT_SECONDS = 10
MAX_TIMEOUT_SECONDS = 86400
DEFAULT_TIMEOUT_SECONDS = 1800
MAX_QUESTION_BYTES = 4000
MAX_ANSWER_BYTES = 4000
MAX_INQUIRIES_PER_RUN = 32
MAX_NOTE_BYTES = 10000
MAX_BODY_BYTES = 64 * 1024
MAX_ARGV = 256
MAX_ARG_BYTES = 32 * 1024
MAX_RESOURCES = 32
MAX_CAPABILITIES = 32

ADAPTERS = ("dsh", "command", "external")
INQUIRY_ID_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
RESOURCE_ID_PATTERN = re.compile(r"^[A-Za-z0-9._:/@+-]{1,512}$")

# -- governed workflow bounds ------------------------------------------------
MAX_WORKFLOW_INPUT_BYTES = 64 * 1024
MAX_WORKFLOW_REASON_BYTES = 4000
MAX_WORKFLOW_HELPERS = 8
MAX_WORKFLOW_ARTIFACTS = 32
MAX_CONTROL_TOKEN = 256

#: The Host control triple. ``hostId`` is attribution, ``ownerGeneration`` and
#: ``controlToken`` are the authority: an owner name alone never authorizes.
CONTROL_FIELDS = ("hostId", "ownerGeneration", "controlToken")

#: Every field ``workflow_submit`` accepts, in addition to the ordinary spec.
WORKFLOW_SUBMIT_FIELDS: frozenset[str] = frozenset()

#: Sentinel accepted only from the authenticated console layer and validated
#: against a server-side registry, never trusted from a C-Two/JSON caller.
CONSOLE_AUTHORITY_FIELD = "consoleAuthority"
FORBIDDEN_OVERRIDE_FIELDS = frozenset({"userOverride", "consoleUser", "adminOverride"})

WORKFLOW_HELPER_FIELDS: frozenset[str] = frozenset()


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
        "timeoutSeconds",
        "workspace",
        "owner",
        "requiredCapabilities",
        "exclusiveResources",
    }
)

TERMINAL_TASK_STATES = frozenset({"completed", "failed", "cancelled"})

#: Every field ``workflow_submit`` accepts in addition to the ordinary spec, and
#: every field one explicit helper specification accepts.
WORKFLOW_SUBMIT_FIELDS = SUBMIT_FIELDS | {"hostId", "executionWorkspace", "spec", "submissionToken"}
WORKFLOW_HELPER_FIELDS = SUBMIT_FIELDS | {"executionWorkspace", "integrator", "role", "spec"}
MIN_SUBMISSION_TOKEN = 16
MAX_SUBMISSION_TOKEN = 256


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


def _canonical_cwd(raw: Any) -> str:
    if not isinstance(raw, str) or not raw.startswith("/"):
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
    """Validate one submit request into the canonical specification.

    ``owner`` is attribution, not input: it is stored on the task but excluded
    from the input fingerprint, so the same request from a different client still
    resolves to the same task.
    """
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
        "cwd": _canonical_cwd(params.get("cwd")),
        "task": task_text,
        "timeoutSeconds": optional_int(
            params, "timeoutSeconds", DEFAULT_TIMEOUT_SECONDS, MIN_TIMEOUT_SECONDS, MAX_TIMEOUT_SECONDS
        ),
        "workspace": optional_bool(params, "workspace", True),
        "requiredCapabilities": string_list(params, "requiredCapabilities", limit=MAX_CAPABILITIES),
        "exclusiveResources": string_list(
            params, "exclusiveResources", limit=MAX_RESOURCES, pattern=RESOURCE_ID_PATTERN
        ),
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


def legacy_input_from_params(params: dict) -> dict | None:
    """Rebuild the legacy Node input object for an equivalent ``start`` request.

    Returns ``None`` when the request uses any field the legacy implementation did
    not have, because such a request can never be identical to a legacy one.
    """
    if params.get("adapter", "dsh") != "dsh" or params.get("argv") is not None:
        return None
    if params.get("requiredCapabilities") or params.get("exclusiveResources"):
        return None
    request_id = params.get("requestId")
    if not isinstance(request_id, str) or not request_id.strip() or len(request_id) > MAX_REQUEST_ID:
        return None
    task = params.get("task")
    if not isinstance(task, str) or not task.strip():
        return None
    cwd = params.get("cwd")
    if not isinstance(cwd, str) or not cwd.startswith("/"):
        return None
    try:
        resolved = str(Path(os.path.realpath(cwd)))
    except OSError:
        return None
    input_value: dict[str, Any] = {
        "cwd": resolved,
        "task": task,
        "timeoutSeconds": params.get("timeoutSeconds", DEFAULT_TIMEOUT_SECONDS),
        "workspace": params.get("workspace", True),
    }
    for name in ("model", "provider", "effort"):
        value = params.get(name)
        if value is not None:
            input_value[name] = value.strip() if isinstance(value, str) else value
    return input_value


def legacy_fingerprint(input_value: dict) -> str:
    """The exact hash the removed Node engine produced for one start request."""
    return sha256_text(node_json(input_value))


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


# -- governed workflow -------------------------------------------------------
def reject_untrusted_override(params: dict) -> None:
    """An untrusted caller-controlled override field is always invalid.

    Console-user authority is never a JSON flag: it is a session identity the
    console registered with the service, and the service validates it. Naming an
    override explicitly is rejected before any other check, so no caller can
    discover a path that skips Host control by guessing a field name.
    """
    present = sorted(set(params) & FORBIDDEN_OVERRIDE_FIELDS)
    if present:
        raise BoardError(
            "INVALID_ARGUMENT",
            f"{present[0]} is not an accepted authority field; authority is a validated control capability, "
            "never a caller-controlled override",
            field=present[0],
        )


def normalize_execution_workspace(params: dict) -> dict:
    """Validate one ``executionWorkspace`` intent.

    Only the shape is checked here; the workspace module resolves refs, snapshots
    the tree and reports unsupported cases with its own explicit errors.
    """
    raw = params.get("executionWorkspace")
    if raw is None:
        return {}
    intent = require_object(raw, "executionWorkspace")
    reject_unknown(
        intent,
        {"kind", "cwd", "access", "base", "includeUntracked", "writeScope", "integrator", "targetRef"},
        "executionWorkspace",
    )
    kind = optional_string(intent, "kind") or "existing"
    if kind not in ("existing", "worktree"):
        raise BoardError("INVALID_ARGUMENT", "executionWorkspace.kind must be 'existing' or 'worktree'")
    access = optional_string(intent, "access") or "write"
    if access not in ("read", "write"):
        raise BoardError("INVALID_ARGUMENT", "executionWorkspace.access must be 'read' or 'write'")
    normalized: dict[str, Any] = {"kind": kind, "access": access}
    cwd = optional_string(intent, "cwd", max_length=4096)
    if cwd is not None:
        if not cwd.startswith("/"):
            raise BoardError("INVALID_ARGUMENT", "executionWorkspace.cwd must be an absolute path")
        normalized["cwd"] = cwd
    base = intent.get("base")
    if base is None:
        # The documented default: the current working tree, with ref omitted (a
        # missing ref means HEAD; an explicit null is rejected by the module).
        normalized["base"] = {"kind": "working-tree"}
    else:
        base = require_object(base, "executionWorkspace.base")
        reject_unknown(base, {"kind", "ref"}, "executionWorkspace.base")
        base_kind = optional_string(base, "kind") or "working-tree"
        if base_kind not in ("commit", "working-tree"):
            raise BoardError("INVALID_ARGUMENT", "executionWorkspace.base.kind must be 'commit' or 'working-tree'")
        base_ref = optional_string(base, "ref", max_length=512)
        normalized["base"] = {"kind": base_kind}
        if base_ref is not None:
            normalized["base"]["ref"] = base_ref
    include_untracked = string_list(intent, "includeUntracked", limit=256)
    if include_untracked:
        normalized["includeUntracked"] = include_untracked
    if include_untracked and normalized["base"]["kind"] == "commit":
        raise BoardError("INVALID_ARGUMENT", "Untracked input requires a working-tree base")
    write_scope = string_list(intent, "writeScope", limit=256)
    if access == "write":
        # An ordinary governed task may write inside its own checkout; the module
        # requires the scope explicitly, so the default is the checkout root.
        normalized["writeScope"] = write_scope or ["."]
    elif write_scope:
        normalized["writeScope"] = write_scope
    integrator = optional_string(intent, "integrator")
    if integrator is not None:
        normalized["integrator"] = integrator
    target_ref = optional_string(intent, "targetRef")
    if target_ref is not None:
        normalized["targetRef"] = target_ref
    return normalized


def normalize_control(params: dict) -> dict | None:
    """The explicit owner control triple, or ``None`` when none was supplied.

    A partial triple is invalid: attribution without authority is never accepted
    silently.
    """
    present = {name: params.get(name) for name in CONTROL_FIELDS if params.get(name) is not None}
    if not present:
        return None
    missing = [name for name in CONTROL_FIELDS if name not in present]
    if missing:
        raise BoardError(
            "INVALID_ARGUMENT",
            f"Control fields must be supplied together; missing {missing[0]}",
            field=missing[0],
        )
    host_id = required_string(params, "hostId", max_length=256)
    generation = params.get("ownerGeneration")
    if isinstance(generation, bool) or not isinstance(generation, int) or generation < 1:
        raise BoardError("INVALID_ARGUMENT", "ownerGeneration must be a positive integer")
    token = required_string(params, "controlToken", max_length=MAX_CONTROL_TOKEN)
    return {"hostId": host_id, "ownerGeneration": generation, "controlToken": token}


def require_expected_revision(params: dict) -> int:
    value = params.get("expectedRevision")
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise BoardError("INVALID_ARGUMENT", "expectedRevision must be a nonnegative integer")
    return value


def _spec_fields(params: dict, *, nested_key: str, allowed: frozenset[str], what: str) -> dict:
    """The ordinary spec fields, accepted flat or as one nested ``spec`` object.

    Both spellings are the same contract: a field given in both places must agree
    exactly, so a typo can never silently change what is executed.
    """
    spec_params = {key: params[key] for key in SUBMIT_FIELDS if key in params}
    nested = params.get(nested_key)
    if nested is None:
        return spec_params
    nested = require_object(nested, nested_key)
    reject_unknown(nested, SUBMIT_FIELDS, f"{what}.{nested_key}")
    for key, value in nested.items():
        if key in spec_params and spec_params[key] != value:
            raise BoardError(
                "INVALID_ARGUMENT",
                f"{key} is given both flat and inside {nested_key} with different values",
                field=key,
            )
        spec_params.setdefault(key, value)
    return spec_params


def workflow_request_fingerprint(spec: dict, execution_workspace: dict, host_id: str) -> str:
    """The governed idempotency identity: goal, execution workspace and Host.

    The task fingerprint stays the ordinary spec fingerprint; this one additionally
    fences a replay whose workspace intent, base or Host changed, so a stable
    requestId can never silently reuse a different prepared snapshot.
    """
    return sha256_text(
        "workflow-v1:"
        + canonical_json({"spec": spec, "executionWorkspace": execution_workspace, "hostId": host_id})
    )


def normalize_submission_token(params: dict) -> str | None:
    value = params.get("submissionToken")
    if value is None:
        return None
    if not isinstance(value, str) or "\0" in value:
        raise BoardError("INVALID_ARGUMENT", "submissionToken must be a string")
    if not (MIN_SUBMISSION_TOKEN <= len(value) <= MAX_SUBMISSION_TOKEN):
        raise BoardError(
            "INVALID_ARGUMENT",
            f"submissionToken must contain {MIN_SUBMISSION_TOKEN}-{MAX_SUBMISSION_TOKEN} characters",
        )
    return value


def normalize_workflow_submit(params: dict) -> dict:
    """Validate one ``workflow_submit`` request into its canonical parts."""
    params = require_object(params, "workflow submit request")
    reject_untrusted_override(params)
    reject_unknown(params, WORKFLOW_SUBMIT_FIELDS, "workflow.submit")
    request_id = required_string(params, "requestId", max_length=MAX_REQUEST_ID)
    host_id = required_string(params, "hostId", max_length=256)
    submission_token = normalize_submission_token(params)
    spec_params = _spec_fields(params, nested_key="spec", allowed=WORKFLOW_SUBMIT_FIELDS, what="workflow.submit")
    spec = normalize_spec(spec_params)
    workspace_intent = normalize_execution_workspace(params)
    if workspace_intent:
        if "cwd" not in workspace_intent:
            workspace_intent["cwd"] = spec["cwd"]
        # The integrator is attribution, never authority; the Host that submitted the
        # goal is the default named integrator.
        workspace_intent.setdefault("integrator", spec_params.get("owner") or f"host:{host_id}")
    return {
        "requestId": request_id,
        "hostId": host_id,
        "submissionToken": submission_token,
        "spec": spec,
        "executionWorkspace": workspace_intent,
        "owner": spec_params.get("owner"),
    }


def normalize_helpers(params: dict) -> list[dict]:
    """Validate the explicit helper specifications of one approval."""
    raw = params.get("helpers", [])
    if raw is None:
        return []
    if not isinstance(raw, list) or len(raw) > MAX_WORKFLOW_HELPERS:
        raise BoardError("INVALID_ARGUMENT", f"helpers must be a list of at most {MAX_WORKFLOW_HELPERS} entries")
    helpers: list[dict] = []
    seen: set[str] = set()
    for index, entry in enumerate(raw):
        if not isinstance(entry, dict):
            raise BoardError("INVALID_ARGUMENT", f"helpers[{index}] must be a JSON object")
        reject_untrusted_override(entry)
        reject_unknown(entry, WORKFLOW_HELPER_FIELDS, f"helpers[{index}]")
        request_id = required_string(entry, "requestId", max_length=MAX_REQUEST_ID)
        if request_id in seen:
            raise BoardError("INVALID_ARGUMENT", f"helpers[{index}] repeats requestId {request_id!r}")
        seen.add(request_id)
        spec_params = _spec_fields(
            entry, nested_key="spec", allowed=WORKFLOW_HELPER_FIELDS, what=f"helpers[{index}]"
        )
        spec = normalize_spec(spec_params)
        workspace_intent = normalize_execution_workspace(entry)
        if workspace_intent:
            if "cwd" not in workspace_intent:
                workspace_intent["cwd"] = spec["cwd"]
            workspace_intent.setdefault("integrator", spec_params.get("owner") or "host")
        integrator = optional_bool(entry, "integrator", False)
        role = optional_string(entry, "role") or "helper"
        if role not in ("helper", "integrator"):
            raise BoardError("INVALID_ARGUMENT", f"helpers[{index}].role must be 'helper' or 'integrator'")
        helpers.append(
            {
                "requestId": request_id,
                "spec": spec,
                "executionWorkspace": workspace_intent,
                "integrator": bool(integrator) or role == "integrator",
                "role": role,
            }
        )
    if helpers and sum(1 for helper in helpers if helper["integrator"]) > 1:
        raise BoardError("INVALID_ARGUMENT", "At most one helper can be the named integrator")
    return helpers

