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
MAX_CONTROL_TOKEN = 256

#: ADR-021 decision 5: the three ways a Host may express the Worker choice.
PARTIAL_BUDDY_HINT = (
    "supply the complete harness/provider/model/effort quadruple to delegate directly, "
    "or omit every buddy field and leave the choice to the Router, writing the "
    "task's needs into the task description"
)

#: The Host control triple. ``hostId`` is attribution, ``ownerGeneration`` and
#: ``controlToken`` are the authority: an owner name alone never authorizes.
CONTROL_FIELDS = ("hostId", "ownerGeneration", "controlToken")

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
        "owner",
        "requiredCapabilities",
        "exclusiveResources",
    }
)

TERMINAL_TASK_STATES = frozenset({"completed", "concluded", "failed", "cancelled"})

#: Fields accepted by workflow submission and explicit helper specifications.
WORKFLOW_SPEC_FIELDS = frozenset({
    "requestId", "task", "cwd", *BUDDY_FIELDS, "expectedSeconds", "owner", "requiredCapabilities", "exclusiveResources",
})
WORKFLOW_SUBMIT_FIELDS = WORKFLOW_SPEC_FIELDS | {"hostId", "executionWorkspace", "submissionToken", "title", "macroTask", "macroTaskId", "macroTaskOf", "buddyLocked", "delegationAuthority", "fixes"}
WORKFLOW_HELPER_FIELDS = WORKFLOW_SPEC_FIELDS | {"executionWorkspace", "role"}
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


def _canonical_cwd(raw: Any) -> str:
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
        "expectedSeconds": expected_duration(params),
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


def normalize_execution_workspace(params: dict) -> dict:
    """Validate one explicitly chosen ``executionWorkspace`` intent.

    Only the shape is checked here; the workspace module resolves refs, snapshots
    the tree and reports unsupported cases with its own explicit errors. ``cwd``
    and ``integrator`` are derived by the submitter from the ordinary spec's
    ``cwd`` and the submitting Host (ADR-021 decision 17).
    """
    raw = params.get("executionWorkspace")
    if raw is None:
        raise BoardError("INVALID_ARGUMENT", "executionWorkspace is required; choose kind 'existing' or 'worktree'",
                         field="executionWorkspace")
    intent = require_object(raw, "executionWorkspace")
    reject_unknown(
        intent,
        {"kind", "access", "accessNote", "base", "includeUntracked", "writeScope", "targetRef"},
        "executionWorkspace",
    )
    kind = optional_string(intent, "kind")
    if kind not in ("existing", "worktree"):
        raise BoardError("INVALID_ARGUMENT", "executionWorkspace.kind must explicitly be 'existing' or 'worktree'",
                         field="executionWorkspace.kind")
    access, access_note = normalize_access(intent)
    normalized: dict[str, Any] = {"kind": kind, "access": access}
    if access_note is not None:
        normalized["accessNote"] = access_note
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
    if access in ("write", "full"):
        # An ordinary micro-task task may write inside its own checkout; the module
        # requires the scope explicitly, so the default is the checkout root.
        normalized["writeScope"] = write_scope or ["."]
    elif write_scope:
        normalized["writeScope"] = write_scope
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


def _spec_fields(params: dict) -> dict:
    """Project the declared fields after the original request was validated."""
    spec_params: dict = {}
    for key in WORKFLOW_SPEC_FIELDS:
        if key in params:
            spec_params[key] = params[key]
    return spec_params


def workflow_request_fingerprint(spec: dict, execution_workspace: dict, host_id: str, *, presentation: dict | None = None, buddy_locked: bool = False, delegation_authority: bool = False, fixes: str | None = None) -> str:
    """The micro-task idempotency identity: micro task, execution workspace and Host.

    The task fingerprint stays the ordinary spec fingerprint; this one additionally
    fences a replay whose workspace intent, base or Host changed, so a stable
    requestId can never silently reuse a different prepared snapshot.
    """
    request = {"spec": spec, "executionWorkspace": execution_workspace, "hostId": host_id}
    if buddy_locked:
        request["buddyLocked"] = True
    if delegation_authority:
        request["delegationAuthority"] = True
    if fixes is not None:
        request["fixes"] = fixes
    if presentation:
        request["presentation"] = presentation
    return sha256_text("workflow-v1:" + canonical_json(request))


def normalize_presentation(params: dict) -> dict:
    """Human display metadata is separate from every execution/selector spec."""
    result = {}
    if sum(key in params for key in ("macroTask", "macroTaskId", "macroTaskOf")) > 1:
        raise BoardError("INVALID_ARGUMENT", "Supply only one of macroTask, macroTaskId or macroTaskOf")
    if "title" in params:
        result["title"] = " ".join(required_string(params, "title", max_length=200).split())
    if "macroTaskOf" in params:
        result["macroTaskOf"] = required_string(params, "macroTaskOf", max_length=128, pattern=IDENTIFIER_PATTERN)
    if "macroTaskId" in params:
        result["macroTaskId"] = required_string(params, "macroTaskId", max_length=128,
                                                pattern=re.compile(r"^macro-[A-Za-z0-9-]+$"))
    if "macroTask" in params:
        macro_task = require_object(params["macroTask"], "macroTask")
        reject_unknown(macro_task, {"title", "description"}, "macroTask")
        result["macroTask"] = {"title": " ".join(required_string(macro_task, "title", max_length=200).split())}
        if "description" in macro_task:
            result["macroTask"]["description"] = " ".join(required_string(macro_task, "description", max_length=300).split())
    return result


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


def normalize_workflow_submit(params: dict, *, lead_run_id: str | None = None) -> dict:
    """Validate one ``workflow_submit`` request into its canonical parts."""
    params = require_object(params, "workflow submit request")
    reject_unknown(params, WORKFLOW_SUBMIT_FIELDS, "workflow.submit")
    request_id = required_string(params, "requestId", max_length=MAX_REQUEST_ID)
    host_id = None if lead_run_id is not None else required_string(params, "hostId", max_length=256)
    submission_token = normalize_submission_token(params)
    delegation_authority = optional_bool(params, "delegationAuthority", False)
    if delegation_authority:
        missing = [key for key in BUDDY_FIELDS if not params.get(key)]
        if missing:
            raise BoardError("INVALID_ARGUMENT",
                             "delegationAuthority requires a complete explicit buddy, without routing "
                             f"(missing {', '.join(missing)})", field=missing[0])
    spec_params = _spec_fields(params)
    spec = normalize_workflow_spec(spec_params)
    workspace_intent = normalize_execution_workspace(params)
    if delegation_authority and workspace_intent["access"] != "full":
        raise BoardError("INVALID_ARGUMENT", "delegationAuthority requires executionWorkspace.access='full'",
                         field="executionWorkspace.access")
    # The workspace's source and integrator come from the spec and submitting Host.
    workspace_intent["cwd"] = spec["cwd"]
    workspace_intent["integrator"] = ({"role": "lead", "id": lead_run_id} if lead_run_id is not None
                                      else {"role": "host", "id": host_id})
    return {
        "requestId": request_id,
        "hostId": host_id,
        "submissionToken": submission_token,
        "spec": spec,
        "executionWorkspace": workspace_intent,
        "owner": spec_params.get("owner"),
        "presentation": normalize_presentation(params),
        "fixes": required_string(params, "fixes", max_length=128, pattern=IDENTIFIER_PATTERN) if "fixes" in params else None,
        "buddyLocked": optional_bool(params, "buddyLocked", False),
        "delegationAuthority": delegation_authority,
    }


def normalize_workflow_spec(params: dict) -> dict:
    """Normalize a micro task whose Worker choice is complete or absent, never partial.

    Ordinary internal task submission retains its execution defaults. A micro-task
    micro task preserves exactly the harness/provider/model/effort constraints the
    caller supplied, and ADR-021 decision 5 allows only the two complete
    expressions: the whole quadruple (delegated directly, no Router) or none of
    it (the Router chooses). Command execution records use the
    separate ordinary execution submission contract.
    """
    supplied = [key for key in BUDDY_FIELDS if params.get(key) is not None]
    if supplied and len(supplied) != len(BUDDY_FIELDS):
        missing = [key for key in BUDDY_FIELDS if key not in supplied]
        raise BoardError(
            "INVALID_ARGUMENT",
            "buddy fields are all-or-nothing: a coding buddy is the complete "
            f"harness/provider/model/effort quadruple (missing {', '.join(missing)}); " + PARTIAL_BUDDY_HINT,
            field=missing[0],
        )
    native_params = {key: value for key, value in params.items() if key != "harness"}
    if params.get("harness") is not None:
        harness = optional_string(params, "harness")
        if harness not in CODING_HARNESSES:
            raise BoardError("UNSUPPORTED_HARNESS", "Expected dsh, zcode, codex or claude", harness=harness)
        native_params["adapter"] = harness
    spec = normalize_spec(native_params)
    if params.get("harness") is not None:
        spec["harness"] = spec.pop("adapter")
    else:
        spec.pop("adapter")
    return spec


def buddy_constraints(spec: dict) -> dict:
    return {key: spec[key] for key in BUDDY_FIELDS if key in spec}


def normalize_buddy(value: Any) -> dict:
    value = require_object(value, "buddy")
    reject_unknown(value, set(BUDDY_FIELDS), "buddy")
    result = {key: required_string(value, key, max_length=256) for key in BUDDY_FIELDS}
    if result["harness"] not in CODING_HARNESSES:
        raise BoardError("INVALID_ARGUMENT", "buddy requires a coding harness: dsh, zcode, codex or claude")
    return result


def normalize_helpers(params: dict, *, integrator: dict) -> list[dict]:
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
        reject_unknown(entry, WORKFLOW_HELPER_FIELDS, f"helpers[{index}]")
        request_id = required_string(entry, "requestId", max_length=MAX_REQUEST_ID)
        if request_id in seen:
            raise BoardError("INVALID_ARGUMENT", f"helpers[{index}] repeats requestId {request_id!r}")
        seen.add(request_id)
        spec_params = _spec_fields(entry)
        spec = normalize_workflow_spec(spec_params)
        workspace_intent = normalize_execution_workspace(entry)
        # Same derivation as the parent submission: the helper's own ordinary
        # spec cwd is the source, and the integrator is the approving Host.
        workspace_intent["cwd"] = spec["cwd"]
        workspace_intent["integrator"] = integrator
        role = optional_string(entry, "role") or "helper"
        if role not in ("helper", "integrator"):
            raise BoardError("INVALID_ARGUMENT", f"helpers[{index}].role must be 'helper' or 'integrator'")
        helpers.append(
            {
                "requestId": request_id,
                "spec": spec,
                "executionWorkspace": workspace_intent,
                "integrator": role == "integrator",
                "role": role,
            }
        )
    if sum(helper["role"] == "integrator" for helper in helpers) > 1:
        raise BoardError("INVALID_ARGUMENT", "At most one helper can be the named integrator")
    return helpers
