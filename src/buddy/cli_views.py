"""Model-facing CLI projections.

Every CLI response is read by a Host model, stays in its context and is re-read on
each later request, so the default printout is a brief projection of the service
response rather than the complete record. The projection only removes material
the caller already supplied (its own packet and workspace intent), duplicates
(``taskId``/``runId``, the execution summary under ``task``, a plan echoed as both
``plan`` and ``cleanup``) and history that is not needed for the next decision.
Identifiers the next command needs, open boundaries, conflicts, errors,
unconfirmed shutdown and truncation are always kept, and a value the service did
not report is never replaced by a default.

The service response itself is unchanged: RPC clients, the console and
``BoardClient`` still receive the complete views, and the CLI-local option
``"output": "full"`` prints them unmodified. ``includeAudit`` and
``routingHistory`` on ``get`` are explicit requests and are passed through.
"""
from __future__ import annotations

from typing import Any, Callable

OUTPUT_FULL = "full"
OUTPUT_BRIEF = "brief"
OUTPUT_MODES = frozenset({OUTPUT_FULL, OUTPUT_BRIEF})

#: Governed mutations that return the governed view plus operation fields.
GOVERNED_RECEIPTS = frozenset(
    {
        "decide",
        "continue",
        "takeover",
        "cancel",
        "acknowledge",
        "scope-amend",
        "workspace-resolve",
        "integration-record",
        "workspace-cleanup-plan",
        "workspace-cleanup-apply",
        "suggest",
    }
)
#: Commands whose response is one execution task view.
TASK_VIEWS = frozenset({"status", "execution-submit", "execution-cancel", "execution-retry", "execution-acknowledge"})

_TITLE_LIMIT = 120


def pop_output_mode(params: object) -> str:
    """Remove and validate the CLI-local ``output`` option before any RPC."""
    if not isinstance(params, dict) or "output" not in params:
        return OUTPUT_BRIEF
    mode = params.pop("output")
    if mode not in OUTPUT_MODES:
        from .errors import BoardError

        raise BoardError("INVALID_ARGUMENT", 'output must be "brief" or "full"')
    return mode


def render(method: str, response: Any, mode: str = OUTPUT_BRIEF) -> Any:
    """The printed form of one successful CLI response."""
    if mode == OUTPUT_FULL or not isinstance(response, dict) or set(response) == {"error"}:
        return response
    projector = _PROJECTORS.get(method)
    if projector is None:
        return response
    return projector(response)


# -- small helpers -----------------------------------------------------------


def _present(value: Any) -> bool:
    return value is not None and value != [] and value != {}


def _pick(source: dict | None, keys: tuple[str, ...]) -> dict:
    """Copy the reported keys whose values carry information."""
    if not isinstance(source, dict):
        return {}
    return {key: source[key] for key in keys if key in source and _present(source[key])}


def _head(value: Any, limit: int) -> Any:
    if isinstance(value, str) and len(value) > limit:
        return value[:limit]
    return value


def _title(task_text: Any, summary: Any) -> str | None:
    for candidate in (summary, task_text):
        if isinstance(candidate, str) and candidate.strip():
            line = " ".join(candidate.strip().splitlines()[0].split())
            return line[:_TITLE_LIMIT]
    return None


def _shutdown(value: Any) -> Any:
    """Keep a complete shutdown report unless it is fully confirmed."""
    if not isinstance(value, dict):
        return value
    if value.get("selfConfirmed") is True and value.get("descendantsConfirmed") is True and not value.get("unconfirmedCount"):
        return {"selfConfirmed": True, "descendantsConfirmed": True}
    return value


def _nonzero(mapping: Any) -> dict:
    if not isinstance(mapping, dict):
        return {}
    return {key: value for key, value in mapping.items() if value}


# -- governed goal views -----------------------------------------------------

_TURN_BRIEF = ("turnId", "turnIndex", "state", "disposition", "resumeMode")
_ROUTING_BRIEF = ("status", "source", "reason", "selectedProfile", "preferenceOutcome", "decisionId")
_WORKSPACE_BRIEF = ("path", "kind", "access", "inputCommit")
_OUTPUT_ARTIFACT = (
    "artifactId",
    "kind",
    "turnId",
    "manifestSha256",
    "outputCommit",
    "diffPath",
    "diffSha256",
    "changedPaths",
    "action",
)
_CHILD_BRIEF = ("taskId", "role", "state", "integrator", "requestId")
_CONFLICT_BRIEF = ("conflictId", "state", "observedFingerprint", "blockingPaths", "conflictingPaths", "action", "artifactId")
_PLAN_BRIEF = ("planId", "state", "eligible", "reasons", "path", "kind", "expiresAt", "appliedAt")


def _integration_brief(row: Any) -> dict:
    if not isinstance(row, dict):
        return row
    brief = _pick(row, ("integrationId", "artifactId", "state", "strategy", "notRequired", "reason", "afterCommit"))
    target = row.get("target")
    if isinstance(target, dict):
        brief["target"] = _pick(target, ("path", "ref"))
    verification = row.get("verification")
    if isinstance(verification, dict) and verification:
        # The verification record can be large; its outcome fields are enough to act on.
        brief["verification"] = _pick(
            verification, ("state", "status", "verified", "ok", "method", "reason", "errors", "mismatches")
        ) or {"recorded": True}
    return brief


def _plan_brief(plan: Any) -> Any:
    if not isinstance(plan, dict):
        return plan
    brief = _pick(plan, _PLAN_BRIEF)
    retention = plan.get("retention")
    if isinstance(retention, dict):
        brief["retained"] = {
            key: len(value) if isinstance(value, list) else True
            for key, value in retention.items()
            if key in ("artifactIds", "fixedRefs", "outputPatches") and _present(value)
        }
    result = plan.get("result")
    if isinstance(result, dict):
        brief["result"] = _pick(result, ("removed", "alreadyRemoved", "path"))
    return brief


def _request_brief(request: Any) -> Any:
    """An open boundary keeps every reported field the Host needs to decide."""
    if not isinstance(request, dict):
        return request
    return {key: value for key, value in request.items() if value is not None and value != []}


def governed_brief(view: dict, *, turn_summary: bool = True) -> dict:
    """Brief projection of the compact governed view."""
    if view.get("governed") is False:
        brief = {"governed": False, "runId": view.get("runId")}
        if isinstance(view.get("task"), dict):
            brief["task"] = task_brief(view["task"])
        for key in ("routingHistory",):
            if key in view:
                brief[key] = view[key]
        return brief
    brief: dict = {"view": OUTPUT_BRIEF}
    brief.update(
        _pick(
            view,
            (
                "runId",
                "requestId",
                "state",
                "status",
                "queueReason",
                "awaitingHost",
                "waitReason",
                "revision",
                "ownerGeneration",
                "continuationCount",
                "executionConfiguration",
                "objectiveId",
                "title",
            ),
        )
    )
    routing = view.get("routing")
    if isinstance(routing, dict):
        brief["routing"] = _pick(routing, _ROUTING_BRIEF)
    workspace = view.get("workspace")
    if isinstance(workspace, dict):
        brief["workspace"] = _pick(workspace, _WORKSPACE_BRIEF)
    scope = view.get("scope")
    if isinstance(scope, dict):
        brief["scope"] = _pick(scope, ("scopeVersion", "writeScope"))
    turn = view.get("currentTurn")
    if isinstance(turn, dict):
        current = _pick(turn, _TURN_BRIEF)
        if turn_summary:
            current.update(_pick(turn, ("summary", "summaryTruncated", "remaining")))
        brief["currentTurn"] = current
    if view.get("activeRequest") is not None:
        brief["activeRequest"] = _request_brief(view["activeRequest"])
    counts = view.get("counts") if isinstance(view.get("counts"), dict) else {}
    pending = view.get("pendingRequests")
    if isinstance(pending, list) and len(pending) > 1:
        brief["pendingRequests"] = [_pick(row, ("requestId", "kind", "state")) for row in pending]
    if counts.get("openRequests", 0) > 1:
        brief["openRequests"] = counts["openRequests"]
    children = view.get("children")
    if isinstance(children, list) and children:
        brief["children"] = [_pick(row, _CHILD_BRIEF) for row in children]
    artifacts = view.get("artifacts")
    if isinstance(artifacts, list) and artifacts:
        # Output artifacts carry what integration and acceptance need; pinned inputs
        # and turn manifests are counted and stay readable in the full view.
        outputs = [
            _pick(row, _OUTPUT_ARTIFACT) for row in artifacts if row.get("kind") in ("output", "resolved-output")
        ]
        brief["artifacts"] = outputs
        if len(outputs) < len(artifacts):
            brief["otherArtifacts"] = len(artifacts) - len(outputs)
    conflicts = view.get("workspaceConflicts")
    if isinstance(conflicts, list) and conflicts:
        brief["workspaceConflicts"] = [_pick(row, _CONFLICT_BRIEF) for row in conflicts]
    integrations = view.get("integrations")
    if isinstance(integrations, list) and integrations:
        brief["integrations"] = [_integration_brief(row) for row in integrations]
    if view.get("cleanup") is not None:
        brief["cleanup"] = _plan_brief(view["cleanup"])
    if view.get("finalArtifactId") is not None:
        brief["finalArtifactId"] = view["finalArtifactId"]
    task = view.get("task")
    if isinstance(task, dict):
        brief.update(_pick(task, ("acceptedAt", "acceptanceVerdict")))
    if "shutdown" in view:
        brief["shutdown"] = _shutdown(view["shutdown"])
    if counts:
        brief["counts"] = counts
    truncated = _nonzero(view.get("truncated"))
    if truncated:
        brief["truncated"] = truncated
    for key in ("audit", "routingHistory"):
        if key in view:
            brief[key] = view[key]
    return brief


def governed_receipt(response: dict) -> dict:
    """A mutation receipt: the resulting state plus the operation's own result."""
    if response.get("governed") is not True:
        return response
    receipt = governed_brief(response, turn_summary=False)
    receipt["view"] = "receipt"
    if response.get("duplicate"):
        receipt["duplicate"] = True
    for key in ("verdict", "integrationId", "removed", "controlFile"):
        if key in response and _present(response[key]):
            receipt[key] = response[key]
    if isinstance(response.get("integration"), dict):
        receipt["integration"] = _integration_brief(response["integration"])
    if response.get("plan") is not None:
        receipt["plan"] = _plan_brief(response["plan"])
        receipt.pop("cleanup", None)
    control = response.get("control")
    if isinstance(control, dict):
        receipt["control"] = _pick(control, ("hostId", "ownerGeneration", "controlFile"))
    target_run = response.get("targetRunId")
    if target_run is not None and target_run != response.get("runId"):
        receipt["targetRunId"] = target_run
        receipt["targetRevision"] = response.get("targetRevision")
    extra = sorted(set(response) - _KNOWN_GOVERNED_FIELDS - set(receipt))
    for key in extra:
        # A new operation field is never silently dropped from a receipt.
        if _present(response[key]):
            receipt[key] = response[key]
    return receipt


_KNOWN_GOVERNED_FIELDS = frozenset(
    {
        "governed",
        "runId",
        "taskId",
        "requestId",
        "hostId",
        "ownerGeneration",
        "state",
        "status",
        "queueReason",
        "awaitingHost",
        "waitReason",
        "goal",
        "revision",
        "continuationCount",
        "workspace",
        "executionWorkspace",
        "requestFingerprint",
        "executionConfiguration",
        "executionConfigurationRevision",
        "routing",
        "currentTurn",
        "turns",
        "activeRequest",
        "requests",
        "pendingRequests",
        "children",
        "artifacts",
        "scope",
        "workspaceConflicts",
        "integrations",
        "cleanup",
        "counts",
        "truncated",
        "finalArtifactId",
        "finalAttemptId",
        "createdAt",
        "updatedAt",
        "auditAvailable",
        "shutdown",
        "task",
        "duplicate",
        "control",
        "retention",
        "targetRunId",
        "targetRevision",
        "integration",
        "plan",
        "objectiveId",
        "title",
    }
)


def submit_receipt(response: dict) -> dict:
    receipt = governed_receipt(response)
    receipt["view"] = "receipt"
    if "controlFile" in response:
        receipt["controlFile"] = response["controlFile"]
    receipt.pop("control", None)
    return receipt


# -- execution record views --------------------------------------------------

_TASK_BRIEF = (
    "runId",
    # Execution records address the same identity as taskId; external callers use it.
    "taskId",
    "requestId",
    "adapter",
    "status",
    "state",
    "queueReason",
    "revision",
    "timeoutSeconds",
    "selectedAttemptId",
    "workerId",
    "attemptGeneration",
    "attemptState",
    "resultAvailable",
    "shutdownConfirmed",
    "cancelRequestedAt",
    "acceptedAt",
    "acceptanceVerdict",
    "artifactCount",
    "workflowState",
    "awaitingHost",
    "duplicate",
    "alreadyTerminal",
    "retry",
    "createdAt",
)


def task_brief(view: dict) -> dict:
    """Brief projection of one execution task view (no spec, task text or runner payload)."""
    brief = _pick(view, _TASK_BRIEF)
    delegation = view.get("delegation")
    if isinstance(delegation, dict):
        configuration = delegation.get("configuration")
        if isinstance(configuration, dict):
            brief["configuration"] = _pick(configuration, ("adapter", "provider", "model", "effort"))
        brief.update(_pick(delegation, ("kind", "parentRunId")))
    workflow = view.get("workflow")
    if isinstance(workflow, dict):
        brief["workflow"] = _pick(
            workflow,
            ("state", "awaitingHost", "ownerGeneration", "revision", "continuationCount", "activeRequestId",
             "resultSummary", "objectiveId", "title"),
        )
    if "workflowShutdown" in view:
        brief["workflowShutdown"] = _shutdown(view["workflowShutdown"])
    activity = view.get("activity")
    if isinstance(activity, dict) and activity:
        brief["activity"] = activity
    inquiries = view.get("inquiries")
    if isinstance(inquiries, dict) and inquiries:
        brief["inquiries"] = inquiries
    title = _title(view.get("task") or (view.get("spec") or {}).get("task"), None)
    if title:
        brief["title"] = title
    brief["view"] = OUTPUT_BRIEF
    return brief


def list_brief(response: dict) -> dict:
    rows = response.get("runs") if isinstance(response.get("runs"), list) else response.get("tasks")
    brief_rows = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        entry = _pick(row, ("runId", "requestId", "adapter", "status", "workflowState", "awaitingHost", "createdAt"))
        delegation = row.get("delegation")
        if isinstance(delegation, dict):
            configuration = delegation.get("configuration")
            if isinstance(configuration, dict) and configuration.get("model"):
                entry["model"] = configuration["model"]
            entry.update(_pick(delegation, ("kind", "parentRunId", "currentHostId")))
        workflow = row.get("workflow") if isinstance(row.get("workflow"), dict) else {}
        title = _title(row.get("task") or (row.get("spec") or {}).get("task"), workflow.get("resultSummary"))
        explicit = workflow.get("title")
        if isinstance(explicit, str) and explicit.strip():
            title = explicit.strip()[:_TITLE_LIMIT]
        if title:
            entry["title"] = title
        if workflow.get("objectiveId"):
            entry["objectiveId"] = workflow["objectiveId"]
        brief_rows.append(entry)
    brief = {"view": OUTPUT_BRIEF, "runs": brief_rows}
    brief.update(_pick(response, ("total", "cursor", "nextCursor")))
    for key in ("total", "nextCursor"):
        if key in response:
            brief[key] = response[key]
    return brief


def result_brief(response: dict) -> dict:
    brief = task_brief(response)
    # Reading a result is the explicit path to its logs: keep the verified execution
    # artifact records (kind, location, content hash, size).
    brief.update(_pick(response, ("attemptId", "resultDelivered", "artifacts")))
    meta = response.get("resultMeta")
    if isinstance(meta, dict):
        # Commit metadata stays whole except the artifact list, which `artifacts` reads.
        brief["resultMeta"] = {key: value for key, value in meta.items() if key != "artifacts" and value is not None}
        if isinstance(meta.get("artifacts"), list):
            brief["resultMeta"]["artifactCount"] = len(meta["artifacts"])
    if response.get("workflowState") is not None:
        from .blocking import _governed_result_view

        governed = _governed_result_view(response, response.get("runId"))
        if governed is not None:
            brief["result"] = _await_result(governed)
            return brief
    brief["result"] = response.get("result")
    return brief


# -- await -------------------------------------------------------------------

_AWAIT_DROP = frozenset({"note", "maxWaitSeconds", "createdAt", "cwd"})


def _await_result(result: Any) -> Any:
    if not isinstance(result, dict):
        return result
    brief = {key: value for key, value in result.items() if key not in ("note",) and value is not None}
    turn = brief.get("turn")
    if isinstance(turn, dict):
        brief["turn"] = {key: value for key, value in turn.items() if value is not None and value != []}
        if turn.get("summary"):
            # The structured turn summary is the Worker's report; the runner's stdout
            # head repeats it and stays readable through the result command.
            brief.pop("finalText", None)
            brief.pop("finalTextTruncated", None)
    if brief.get("finalTextTruncated") is False:
        brief.pop("finalTextTruncated")
    if brief.get("artifacts") == []:
        brief.pop("artifacts")
    if brief.get("request") is None:
        brief.pop("request", None)
    return brief


def await_brief(envelope: dict) -> dict:
    brief = {key: value for key, value in envelope.items() if key not in _AWAIT_DROP and value is not None}
    for key in ("timedOut", "reconnects"):
        if not brief.get(key):
            brief.pop(key, None)
    if "shutdown" in brief:
        brief["shutdown"] = _shutdown(brief["shutdown"])
    if brief.get("ok") is True:
        # Log paths and the recovery block matter when the result is not usable.
        brief.pop("logPaths", None)
    if "result" in brief:
        result = _await_result(brief["result"])
        if isinstance(result, dict):
            # A governed envelope lifts turn, artifacts and the result command to the
            # top level; the nested copies are identical and printed once.
            for key in ("turn", "artifacts", "resultCommand"):
                if key in result and key in envelope and _await_result({key: envelope[key]}).get(key) == result[key]:
                    result.pop(key)
            if brief.get("ok") is True:
                result.pop("logPaths", None)
        brief["result"] = result
    if isinstance(brief.get("turn"), dict):
        brief["turn"] = {key: value for key, value in brief["turn"].items() if value is not None and value != []}
    if brief.get("artifacts") == []:
        brief.pop("artifacts")
    if brief.get("resultCompact") is True:
        brief.pop("resultCompact")
    brief["view"] = OUTPUT_BRIEF
    return brief


def _task_response(response: dict) -> dict:
    return task_brief(response)


_PROJECTORS: dict[str, Callable[[dict], Any]] = {
    "get": governed_brief,
    "submit": submit_receipt,
    **{method: governed_receipt for method in GOVERNED_RECEIPTS},
    **{method: _task_response for method in TASK_VIEWS},
    "list": list_brief,
    "result": result_brief,
    "await": await_brief,
}
