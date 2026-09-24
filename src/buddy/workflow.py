"""Governed productivity workflow: Host-directed assistance over ordinary tasks.

The current contract is documented in ``docs/reference/workflow.md``. A
governed run *is* the existing logical task (``runId`` = ``taskId``): it uses the
ordinary task/attempt/worker/capacity/receipt machinery, never a second job engine.
What the coordinator adds is durable governance around it:

* an immutable original goal, a Host owner generation and a control capability;
* one durable turn per actual execution, with the bounded context and the validated
  structured outcome the coding adapter imported after confirmed shutdown;
* assistance/attention requests, explicit helper tasks, one-use automatic
  continuation authority and manual continuation inputs;
* workspace reservations keyed by the actual ``checkoutId`` (so sibling cwd
  directories of one checkout cannot bypass write exclusion);
* attempt-scoped credentials that the service enforces, and console-user authority
  that only a registered console session can present.

The workspace module (``buddy.workspace``) performs all Git/filesystem work and is
delivered separately. Every call to it happens outside a database transaction; the
resulting immutable manifests are pinned in authoritative records afterwards.
"""
from __future__ import annotations

import json
import threading
import uuid
from pathlib import Path
from typing import Any

from . import schemas
from .db import canonical_json, sha256_text
from .errors import BoardError

try:  # The workspace module is owned and delivered separately.
    from . import workspace as _workspace_module
except ImportError:  # pragma: no cover - exercised by tests without the module
    _workspace_module = None

#: Operations an attempt-scoped credential may call at all.
AGENT_OPERATIONS = frozenset(
    {
        "health",
        "capabilities",
        "workflow_get",
        "workflow_suggest",
        "task_get",
        "task_result",
        "artifact_list",
        "events_read",
        "message_post",
        "message_get",
        "message_list",
        "message_update",
        "inquiry_observe",
    }
)

#: Operations whose target run/task must be the credential's own run.
AGENT_RUN_BOUND_OPERATIONS = frozenset(
    {
        "workflow_get",
        "workflow_suggest",
        "task_get",
        "task_result",
        "artifact_list",
        "events_read",
        "message_post",
        "message_get",
        "message_list",
        "message_update",
        "inquiry_observe",
    }
)

TURN_INPUT_VERSION = 1
TURN_OUTPUT_VERSION = 1
TURN_DISPOSITIONS = ("completed", "assistance", "attention")
MAX_ACTIVE_TURNS_VIEW = 10
MAX_CHILD_VIEW = 32
MAX_ARTIFACT_VIEW = 32
MAX_REQUEST_VIEW = 5
MAX_CONTEXT_HELPERS = 8
MAX_CONTEXT_ARTIFACTS = 8
MAX_CONTEXT_TEXT = 4000

#: Bounded views of the lifecycle records added by the workspace-lifecycle slice.
MAX_CONFLICT_VIEW = 8
MAX_INTEGRATION_VIEW = 8
MAX_CONFLICT_PATH_VIEW = 32
#: A cleanup plan is a short-lived authorization, not a permanent permission.
CLEANUP_PLAN_TTL_SECONDS = 900
SCOPE_ACTIONS = ("restore", "adopt", "abandon")
INTEGRATION_STRATEGIES = ("patch", "cherry-pick", "merge")
FINAL_ARTIFACT_KINDS = ("output", "resolved-output")

#: ``workflow_get.routingHistory`` is one bounded newest-first page of a run's own
#: routing decisions, keyed by the immutable ``workflow_routes.rowid``.
ROUTING_HISTORY_FIELDS = frozenset({"limit", "before"})
DEFAULT_ROUTING_HISTORY_LIMIT = 20
MAX_ROUTING_HISTORY_LIMIT = 100

RUN_STATES = ("executing", "awaiting-host", "waiting-helpers", "delivered", "accepted", "cancelled", "failed")

#: Request-scoped authority resolved by the service for one operation call.
_SCOPE = threading.local()

CONTROL_KIND = "control"
CONSOLE_KIND = "console"
AGENT_KIND = "agent"
SERVICE_KIND = "service"


# -- request scope -----------------------------------------------------------
def set_request_scope(scope: dict | None) -> None:
    _SCOPE.value = scope


def current_scope() -> dict | None:
    return getattr(_SCOPE, "value", None)


def clear_request_scope() -> None:
    _SCOPE.value = None


def console_authority_from_scope() -> dict | None:
    """The validated console authority of the current request, if any."""
    scope = current_scope()
    if scope is not None and scope.get("kind") == CONSOLE_KIND:
        return {"sessionId": scope.get("sessionId")}
    return None


def workspace_module():
    """The workspace implementation, or an explicit unsupported error."""
    if _workspace_module is None:
        raise BoardError(
            "UNSUPPORTED",
            "The workspace module is not part of this build, so no governed workspace can be prepared or sealed",
        )
    return _workspace_module


def _turn_input_context(input_json: Any) -> dict:
    """The frozen context object of one turn input, or an empty object when unreadable."""
    try:
        document = json.loads(input_json) if input_json else None
    except (TypeError, ValueError):
        return {}
    context = document.get("context") if isinstance(document, dict) else None
    return context if isinstance(context, dict) else {}


def _frozen_turn_routing(context: dict) -> dict | None:
    """The routing identity frozen in one turn input, or ``None`` when it has none.

    A turn that predates the binding has no routing identity. It is never guessed
    from the current run, timestamps or a matching model name, and a malformed
    binding is reported as absent rather than invented.
    """
    routing = context.get("routing")
    if not isinstance(routing, dict) or "decisionId" not in routing:
        return None
    decision_id = routing.get("decisionId")
    revision = routing.get("executionConfigurationRevision")
    if decision_id is not None and (not isinstance(decision_id, str) or not decision_id.strip()):
        return None
    if isinstance(revision, bool) or not isinstance(revision, int) or revision < 0:
        return None
    return {"decisionId": decision_id, "executionConfigurationRevision": revision}


class WorkflowCoordinator:
    """Durable governance on top of :class:`buddy.store.BoardStore`."""

    def __init__(self, board, *, clock=None):
        self.board = board
        self.db = board.db
        self._clock = clock or board.now

    def now(self) -> str:
        return self._clock()

    # -- rows and views ------------------------------------------------------
    def _run_row(self, connection, run_id: str):
        row = connection.execute("SELECT * FROM workflow_runs WHERE run_id=?", (run_id,)).fetchone()
        if row is None:
            raise BoardError("NOT_FOUND", "This run is not a governed workflow run", runId=run_id)
        return row

    def _run_optional(self, connection, run_id: str):
        return connection.execute("SELECT * FROM workflow_runs WHERE run_id=?", (run_id,)).fetchone()

    def _request_row(self, connection, run_id: str, request_id: str):
        row = connection.execute(
            "SELECT * FROM workflow_requests WHERE run_id=? AND request_id=?", (run_id, request_id)
        ).fetchone()
        if row is None:
            raise BoardError("NOT_FOUND", "Unknown assistance request for this run", requestId=request_id)
        return row

    def _turn_row(self, connection, turn_id: str):
        row = connection.execute("SELECT * FROM workflow_turns WHERE turn_id=?", (turn_id,)).fetchone()
        if row is None:
            raise BoardError("NOT_FOUND", "Unknown turn", turnId=turn_id)
        return row

    @staticmethod
    def _turn_view(row, *, include_audit: bool = False, compact: bool = False) -> dict:
        outcome = json.loads(row["outcome_json"]) if row["outcome_json"] else None
        context = _turn_input_context(row["input_json"])
        view = {
            "turnId": row["turn_id"],
            "turnIndex": row["turn_index"],
            "attemptId": row["attempt_id"],
            "generation": row["generation"],
            "resumeMode": row["resume_mode"],
            "previousSessionId": row["previous_session_id"],
            "sessionId": row["session_id"],
            "state": row["state"],
            "disposition": row["disposition"],
            "createdAt": row["created_at"],
            "updatedAt": row["updated_at"],
            # This turn's own frozen execution configuration, never the current run's.
            "executionConfiguration": context.get("executionConfiguration"),
            # This turn's own frozen routing identity. An older turn input that
            # predates the binding stays null: it is never inferred from the current
            # run, wall-clock timestamps or a matching model name.
            "routing": _frozen_turn_routing(context),
        }
        if compact:
            # The default view is an index: the full input/outcome/provenance is in audit.
            return view
        if outcome is not None:
            summary = outcome.get("summary") or ""
            view["summary"] = summary if include_audit else summary[:2000]
            if not include_audit:
                view["summaryTruncated"] = len(summary) > 2000
            view["remaining"] = outcome.get("remaining", [])
            if include_audit:
                view["request"] = outcome.get("request")
                view["artifactReferences"] = outcome.get("artifacts", [])
        if include_audit:
            view["promptSha256"] = row["prompt_sha256"]
            view["inputSha256"] = row["input_sha256"]
            view["input"] = json.loads(row["input_json"])
            view["outcome"] = outcome
            view["provenance"] = json.loads(row["provenance_json"]) if row["provenance_json"] else None
            view["turnResultPath"] = row["turn_result_path"]
            view["sealedArtifacts"] = json.loads(row["sealed_artifacts_json"])
        return view

    @staticmethod
    def _request_view(row, *, include_audit: bool = False, compact: bool = False) -> dict:
        payload = json.loads(row["payload_json"])
        if compact:
            return {
                "requestId": row["request_id"],
                "kind": row["kind"],
                "state": row["state"],
                "createdAt": row["created_at"],
                "decidedAt": row["decided_at"],
                "childTaskId": row["child_task_id"],
                "proxy": payload.get("proxy"),
                "origin": payload.get("origin"),
                "routing": payload.get("source") == "routing",
            }
        view = {
            "requestId": row["request_id"],
            "kind": row["kind"],
            "state": row["state"],
            "summary": row["summary"],
            "attempted": payload.get("attempted"),
            "neededWork": payload.get("neededWork", []),
            "expectedArtifacts": payload.get("expectedArtifacts", []),
            "acceptance": payload.get("acceptance"),
            "suggestedProfileId": payload.get("suggestedProfileId"),
            "turnId": row["turn_id"],
            "attemptId": row["attempt_id"],
            "childTaskId": row["child_task_id"],
            "expectedRevision": row["expected_revision"],
            "createdAt": row["created_at"],
            "decidedAt": row["decided_at"],
            "proxy": payload.get("proxy"),
            "origin": payload.get("origin"),
            "routing": payload.get("source") == "routing",
        }
        if isinstance(payload.get("preparationError"), dict):
            view["preparationError"] = {
                "code": _head(payload["preparationError"].get("code"), 100),
                "message": _head(payload["preparationError"].get("message"), 2000),
            }
        if include_audit:
            view["decision"] = json.loads(row["decision_json"]) if row["decision_json"] else None
            view["decisionCommandId"] = row["decision_command_id"]
            view["payload"] = payload
        return view

    @staticmethod
    def _child_view(row) -> dict:
        return {
            "taskId": row["child_task_id"],
            "role": row["role"],
            "state": row["state"],
            "integrator": bool(row["integrator"]),
            "autoContinue": bool(row["auto_continue"]),
            "requestId": row["request_id"],
            "workspaceManifestSha256": row["workspace_manifest_sha256"],
            "createdAt": row["created_at"],
            "updatedAt": row["updated_at"],
        }

    @staticmethod
    def _artifact_view(row) -> dict:
        view = {
            "artifactId": row["artifact_id"],
            "kind": row["kind"],
            "attemptId": row["attempt_id"],
            "turnId": row["turn_id"],
            "sourceTaskId": row["source_task_id"],
            # For an output this is the sealed snapshot identity; for an input or a
            # pinned turn manifest it is that manifest's own digest.
            "manifestSha256": row["manifest_sha256"],
            "createdAt": row["created_at"],
        }
        try:
            manifest = json.loads(row["manifest_json"])
        except (TypeError, ValueError):
            return view
        if not isinstance(manifest, dict):
            return view
        if row["kind"] in ("output", "resolved-output"):
            view["outputCommit"] = manifest.get("commit")
            view["diffPath"] = manifest.get("diffPath")
            view["diffSha256"] = manifest.get("diffSha256")
            changed = manifest.get("changedPaths")
            if isinstance(changed, list):
                view["changedPaths"] = changed[:32]
            if row["kind"] == "resolved-output":
                view["action"] = manifest.get("action")
        else:
            view["path"] = manifest.get("path")
            view["baseCommit"] = manifest.get("baseCommit")
            view["inputCommit"] = manifest.get("inputCommit")
        return view

    def _workspace_view(self, run_row) -> dict | None:
        if not run_row["workspace_manifest_json"]:
            return None
        manifest = json.loads(run_row["workspace_manifest_json"])
        return {
            "workspaceId": run_row["workspace_id"],
            "path": manifest.get("path"),
            "kind": manifest.get("kind"),
            "checkoutId": manifest.get("checkoutId"),
            "repositoryId": manifest.get("repositoryId"),
            "access": manifest.get("access"),
            "baseCommit": manifest.get("baseCommit"),
            "inputCommit": manifest.get("inputCommit"),
            "manifestSha256": run_row["workspace_manifest_sha256"],
        }

    def _current_scope(self, connection, run_row) -> dict:
        """The current authorization scope version of one run.

        A run attached before this slice has no row and reads as version 1 with
        the manifest's own write scope, so the version is never invented from an
        ephemeral field. The scope version stays distinct from the input snapshot
        identity it was recorded alongside.
        """
        row = connection.execute(
            "SELECT * FROM workflow_scope_versions WHERE run_id=? ORDER BY scope_version DESC LIMIT 1",
            (run_row["run_id"],),
        ).fetchone()
        if row is not None:
            return {"scopeVersion": row["scope_version"], "writeScope": json.loads(row["write_scope_json"]),
                    "inputManifestSha256": row["manifest_sha256"], "actor": row["actor"], "reason": row["reason"],
                    "recorded": True, "updatedAt": row["created_at"]}
        manifest = json.loads(run_row["workspace_manifest_json"] or "{}")
        return {"scopeVersion": 1, "writeScope": list(manifest.get("writeScope") or []),
                "inputManifestSha256": manifest.get("manifestSha256"), "actor": None, "reason": "original submission",
                "recorded": False, "updatedAt": run_row["updated_at"]}

    def _open_conflict(self, connection, run_id: str):
        return connection.execute(
            "SELECT * FROM workflow_workspace_conflicts WHERE run_id=? AND state='open'"
            " ORDER BY created_at, conflict_id LIMIT 1",
            (run_id,),
        ).fetchone()

    @staticmethod
    def _conflict_view(row) -> dict:
        entries = []
        try:
            evidence = json.loads(row["evidence_json"])
        except (TypeError, ValueError):
            evidence = {}
        for entry in (evidence.get("entries") if isinstance(evidence, dict) else []) or []:
            if not isinstance(entry, dict):
                continue
            entries.append({"path": entry.get("path"), "authorized": entry.get("authorized"),
                            "authorizedIndex": entry.get("authorizedIndex"), "observed": entry.get("observed"),
                            "observedIndex": entry.get("observedIndex"), "changed": entry.get("changed"),
                            "indexChanged": entry.get("indexChanged"), "excludedChanged": entry.get("excludedChanged")})
        return {
            "conflictId": row["conflict_id"],
            "runId": row["run_id"],
            "attemptId": row["attempt_id"],
            "turnId": row["turn_id"],
            "manifestSha256": row["manifest_sha256"],
            "observedFingerprint": row["observed_fingerprint"],
            "blockingPaths": json.loads(row["blocking_paths_json"]),
            "paths": entries[:MAX_CONFLICT_PATH_VIEW],
            "pathsTruncated": len(entries) > MAX_CONFLICT_PATH_VIEW,
            "state": row["state"],
            "action": row["action"],
            "resolvedPaths": json.loads(row["resolved_paths_json"]),
            "conflictingPaths": json.loads(row["conflicting_paths_json"]),
            "artifactId": row["artifact_id"],
            "outputCommit": row["output_commit"],
            "outputTree": row["output_tree"],
            "actor": row["actor"],
            "reason": row["reason"],
            "createdAt": row["created_at"],
            "updatedAt": row["updated_at"],
        }

    @staticmethod
    def _integration_view(row) -> dict:
        try:
            verification = json.loads(row["verification_json"])
        except (TypeError, ValueError):
            verification = {}
        target = None
        if row["state"] != "not-required":
            target = {"kind": row["target_kind"], "path": row["target_path"], "ref": row["target_ref"],
                      "repositoryId": row["target_repository_id"], "checkoutId": row["target_checkout_id"]}
        return {
            "integrationId": row["integration_id"],
            "runId": row["run_id"],
            "artifactId": row["artifact_id"],
            "attemptId": row["attempt_id"],
            "state": row["state"],
            "strategy": row["strategy"],
            "target": target,
            "sourceCommit": row["source_commit"],
            "sourceTree": row["source_tree"],
            "beforeCommit": row["before_commit"],
            "afterCommit": row["after_commit"],
            "beforeTree": row["before_tree"],
            "afterTree": row["after_tree"],
            "verification": verification,
            "notRequired": row["state"] == "not-required",
            "reason": row["reason"],
            "actor": row["actor"],
            "createdAt": row["created_at"],
        }

    @staticmethod
    def _plan_view(row) -> dict:
        if row is None:
            return None
        return {
            "planId": row["plan_id"],
            "runId": row["run_id"],
            "workspaceId": row["workspace_id"],
            "checkoutId": row["checkout_id"],
            "repositoryId": row["repository_id"],
            "path": row["path"],
            "kind": row["kind"],
            "state": row["state"],
            "eligible": row["state"] == "planned",
            "reasons": json.loads(row["reasons_json"]),
            "evidence": json.loads(row["evidence_json"]),
            "retention": json.loads(row["retention_json"]),
            "result": json.loads(row["result_json"]) if row["result_json"] else None,
            "revision": row["revision"],
            "createdAt": row["created_at"],
            "appliedAt": row["applied_at"],
            "expiresAt": row["expires_at"],
        }

    def compact(self, connection, run_row, task_row=None) -> dict:
        """The compact governed view. Never contains control or scoped tokens."""
        task_row = task_row or connection.execute(
            "SELECT * FROM tasks WHERE task_id=?", (run_row["run_id"],)
        ).fetchone()
        turns = connection.execute(
            "SELECT * FROM workflow_turns WHERE run_id=? ORDER BY turn_index DESC LIMIT ?",
            (run_row["run_id"], MAX_ACTIVE_TURNS_VIEW),
        ).fetchall()
        requests = connection.execute(
            "SELECT * FROM workflow_requests WHERE run_id=? ORDER BY created_at DESC LIMIT ?",
            (run_row["run_id"], MAX_REQUEST_VIEW + 1),
        ).fetchall()
        children = connection.execute(
            "SELECT * FROM workflow_children WHERE parent_run_id=? ORDER BY created_at, child_task_id LIMIT ?",
            (run_row["run_id"], MAX_CHILD_VIEW + 1),
        ).fetchall()
        artifacts = connection.execute(
            "SELECT * FROM workflow_artifacts WHERE run_id=? ORDER BY created_at DESC LIMIT ?",
            (run_row["run_id"], MAX_ARTIFACT_VIEW + 1),
        ).fetchall()
        conflicts = connection.execute(
            "SELECT * FROM workflow_workspace_conflicts WHERE run_id=? ORDER BY created_at DESC, rowid DESC LIMIT ?",
            (run_row["run_id"], MAX_CONFLICT_VIEW),
        ).fetchall()
        integrations = connection.execute(
            "SELECT * FROM workflow_integrations WHERE run_id=? ORDER BY created_at DESC, rowid DESC LIMIT ?",
            (run_row["run_id"], MAX_INTEGRATION_VIEW),
        ).fetchall()
        cleanup = connection.execute(
            "SELECT * FROM workspace_cleanup_plans WHERE run_id=? ORDER BY rowid DESC LIMIT 1",
            (run_row["run_id"],),
        ).fetchone()
        turn_total = int(
            connection.execute(
                "SELECT COUNT(*) AS count FROM workflow_turns WHERE run_id=?", (run_row["run_id"],)
            ).fetchone()["count"]
        )
        request_total = int(
            connection.execute(
                "SELECT COUNT(*) AS count FROM workflow_requests WHERE run_id=?", (run_row["run_id"],)
            ).fetchone()["count"]
        )
        pending = connection.execute(
            "SELECT * FROM workflow_requests WHERE run_id=? AND state='open' ORDER BY created_at, request_id LIMIT ?",
            (run_row["run_id"], MAX_REQUEST_VIEW),
        ).fetchall()
        open_count = connection.execute("SELECT COUNT(*) FROM workflow_requests WHERE run_id=? AND state='open'", (run_row["run_id"],)).fetchone()[0]
        child_total = int(
            connection.execute(
                "SELECT COUNT(*) AS count FROM workflow_children WHERE parent_run_id=?", (run_row["run_id"],)
            ).fetchone()["count"]
        )
        artifact_total = int(
            connection.execute(
                "SELECT COUNT(*) AS count FROM workflow_artifacts WHERE run_id=?", (run_row["run_id"],)
            ).fetchone()["count"]
        )
        truncated = {
            "turns": max(0, turn_total - MAX_ACTIVE_TURNS_VIEW),
            "requests": max(0, request_total - MAX_REQUEST_VIEW),
            "children": max(0, child_total - MAX_CHILD_VIEW),
            "artifacts": max(0, artifact_total - MAX_ARTIFACT_VIEW),
            "pendingRequests": max(0, open_count - MAX_REQUEST_VIEW),
        }
        requests = requests[:MAX_REQUEST_VIEW]
        children = children[:MAX_CHILD_VIEW]
        artifacts = artifacts[:MAX_ARTIFACT_VIEW]
        active = self._open_boundary(connection, run_row)
        active_request = self._request_view(active) if active is not None else None
        goal = json.loads(run_row["goal_json"])
        state = run_row["state"]
        view = {
            "governed": True,
            "runId": run_row["run_id"],
            "taskId": run_row["run_id"],
            "requestId": task_row["request_id"],
            "hostId": run_row["host_id"],
            "ownerGeneration": run_row["owner_generation"],
            "state": state,
            "status": task_row["state"],
            "queueReason": task_row["queue_reason"],
            "awaitingHost": state == "awaiting-host",
            "waitReason": self._wait_reason(state, task_row, active_request),
            "goal": self._goal_view(goal, run_row["goal_fingerprint"]),
            "revision": run_row["revision"],
            "continuationCount": run_row["continuation_count"],
            "workspace": self._workspace_view(run_row),
            "executionWorkspace": json.loads(run_row["execution_workspace_json"]),
            "requestFingerprint": run_row["request_fingerprint"],
            "executionConfiguration": self._configuration(run_row),
            "executionConfigurationRevision": run_row["execution_configuration_revision"],
            "routing": self._routing_view(connection, run_row),
            "currentTurn": self._turn_view(turns[0]) if turns else None,
            "turns": [self._turn_view(row, compact=True) for row in turns],
            "activeRequest": active_request,
            "requests": [self._request_view(row, compact=True) for row in requests],
            "pendingRequests": [self._request_view(row, compact=True) for row in pending[:MAX_REQUEST_VIEW]],
            "children": [self._child_view(row) for row in children],
            "artifacts": [self._artifact_view(row) for row in artifacts],
            "scope": self._current_scope(connection, run_row),
            "workspaceConflicts": [self._conflict_view(row) for row in conflicts],
            "integrations": [self._integration_view(row) for row in integrations],
            "cleanup": self._plan_view(cleanup),
            "counts": {
                "turns": turn_total,
                "requests": request_total,
                "openRequests": open_count,
                "children": child_total,
                "artifacts": artifact_total,
            },
            "truncated": truncated,
            "finalArtifactId": run_row["final_artifact_id"],
            "finalAttemptId": run_row["final_attempt_id"],
            "createdAt": run_row["created_at"],
            "updatedAt": run_row["updated_at"],
            "auditAvailable": True,
            "shutdown": self.shutdown_summary(connection, run_row["run_id"]),
            "task": self._task_summary(connection, task_row),
        }
        return view

    @staticmethod
    def _goal_view(goal: dict, fingerprint: str) -> dict:
        """The bounded goal summary; the immutable original spec is in audit."""
        task = goal.get("task") if isinstance(goal.get("task"), str) else ""
        limit = 2000
        return {
            "task": task if len(task) <= limit else task[:limit],
            "taskTruncated": len(task) > limit,
            "taskBytes": len(task.encode("utf-8")),
            "adapter": goal.get("adapter"),
            "cwd": goal.get("cwd"),
            "fingerprint": fingerprint,
        }

    def _task_summary(self, connection, task_row) -> dict:
        """The bounded task view: no raw spec/task duplication, full record in audit."""
        decorated = self.board._decorate(connection, task_row)
        summary = {
            "runId": decorated["runId"],
            "requestId": decorated["requestId"],
            "owner": decorated["owner"],
            "adapter": decorated["adapter"],
            "cwd": decorated["cwd"],
            "status": decorated["status"],
            "state": decorated["state"],
            "queueReason": decorated["queueReason"],
            "revision": decorated["revision"],
            "timeoutSeconds": decorated["timeoutSeconds"],
            "selectedAttemptId": decorated["selectedAttemptId"],
            "activeAttemptId": decorated["activeAttemptId"],
            "resultAvailable": decorated["resultAvailable"],
            "shutdownConfirmed": decorated["shutdownConfirmed"],
            "acceptedAt": decorated["acceptedAt"],
            "acceptanceVerdict": decorated["acceptanceVerdict"],
            "acceptanceNote": decorated["acceptanceNote"],
            "artifactCount": decorated["artifactCount"],
            "inquiries": decorated["inquiries"],
            "delegation": decorated["delegation"],
            "createdAt": decorated["createdAt"],
            "updatedAt": decorated["updatedAt"],
        }
        if "attemptGeneration" in decorated:
            summary["attemptGeneration"] = decorated["attemptGeneration"]
            summary["attemptState"] = decorated["attemptState"]
        if "workflow" in decorated:
            summary["workflow"] = decorated["workflow"]
        if "workflowState" in decorated:
            # The governed marker the UI keys on, kept alongside the full record.
            summary["workflowState"] = decorated["workflowState"]
            summary["awaitingHost"] = decorated["awaitingHost"]
            summary["workflowShutdown"] = decorated["workflowShutdown"]
        return summary

    @staticmethod
    def _wait_reason(state: str, task_row, active_request) -> str:
        if state == "awaiting-host":
            if active_request is not None and active_request.get("routing") is True:
                return "model routing needs Host configuration; no new coding turn was started"
            if active_request is not None and active_request["kind"] in ("assistance", "helper-attention"):
                return "the current turn requested Host assistance"
            if active_request is not None and active_request["kind"] == "helper-report":
                return "all helpers reached a terminal state; the Host decides the next turn"
            return "the current turn concluded and the Host decides the next turn"
        if state == "waiting-helpers":
            return "authorized helpers are still running"
        if state == "delivered":
            return "the goal work is delivered and awaits final acknowledgement"
        if state == "accepted":
            return "the final artifact was acknowledged"
        if state == "cancelled":
            return "the run was cancelled"
        if state == "failed":
            return "the run failed without a Host decision boundary"
        if task_row["state"] == "running":
            return "a turn is executing"
        return "the run is queued for its next turn"

    # -- authority -----------------------------------------------------------
    def _authorize(self, connection, run_row, params: dict, *, console_authority: dict | None, action: str) -> str:
        """Validate Host control or console-user authority; return the actor label."""
        if console_authority is not None:
            return f"console:{console_authority.get('sessionId', 'session')}"
        control = schemas.normalize_control(params)
        if control is None:
            raise BoardError(
                "UNAUTHORIZED",
                f"{action} requires the Host control capability (hostId, ownerGeneration, controlToken) or an "
                "authenticated console session",
            )
        if control["ownerGeneration"] != run_row["owner_generation"]:
            raise BoardError(
                "STALE_GENERATION",
                "This control capability was superseded by a newer owner generation; a delayed decision is fenced",
                ownerGeneration=control["ownerGeneration"],
                currentGeneration=run_row["owner_generation"],
            )
        if control["hostId"] != run_row["host_id"]:
            raise BoardError(
                "UNAUTHORIZED",
                "This control capability belongs to a different Host; an owner name is attribution, not authority",
                hostId=control["hostId"],
            )
        import hmac

        expected = self.db.control_token_verifier(control["controlToken"])
        if not hmac.compare_digest(expected, run_row["control_verifier"]):
            raise BoardError("UNAUTHORIZED", "Invalid control capability for this run")
        return f"host:{control['hostId']}"

    def _expect_revision(self, run_row, expected: int | None) -> None:
        if expected is None:
            return
        if expected != run_row["revision"]:
            raise BoardError(
                "REVISION_CONFLICT",
                "The governed run changed since this decision was prepared; re-read it and retry",
                expectedRevision=expected,
                currentRevision=run_row["revision"],
            )

    # -- scoped credentials --------------------------------------------------
    def role_for(self, connection, payload: dict) -> str | None:
        """Classify a run as a parent run or a helper run."""
        row = connection.execute(
            "SELECT parent_run_id FROM workflow_children WHERE child_task_id=?", (payload["runId"],)
        ).fetchone()
        return "helper" if row is not None else "parent"

    def resolve_credential(self, token: str) -> dict:
        """Resolve an attempt-scoped credential; never falls back to service authority."""
        if not isinstance(token, str) or not token:
            raise BoardError("UNAUTHORIZED", "An empty attempt-scoped credential is invalid")
        verifier = self.db.agent_token_verifier(token)
        with self.db.read() as connection:
            row = connection.execute(
                "SELECT * FROM agent_credentials WHERE token_verifier=? AND state='active'", (verifier,)
            ).fetchone()
        if row is None:
            raise BoardError(
                "UNAUTHORIZED",
                "This attempt-scoped credential is unknown or revoked; the service never substitutes the "
                "administrator token for an invalid scoped credential",
            )
        if row["expires_at"] and row["expires_at"] < self.now():
            raise BoardError("UNAUTHORIZED", "This attempt-scoped credential has expired")
        return {
            "kind": AGENT_KIND,
            "credentialId": row["credential_id"],
            "runId": row["run_id"],
            "taskId": row["task_id"],
            "attemptId": row["attempt_id"],
            "generation": row["generation"],
            "turnId": row["turn_id"],
        }

    def authorize_agent_operation(self, operation: str, params: dict, scope: dict) -> None:
        """Enforce the permitted operation set of an attempt-scoped credential."""
        if operation not in AGENT_OPERATIONS:
            raise BoardError(
                "FORBIDDEN",
                f"An attempt-scoped credential cannot call {operation}; it may observe its own task and record "
                "suggestions, but it cannot submit tasks, create helpers, approve, take over, cancel, write "
                "evaluations or control other runs",
                operation=operation,
            )
        if operation not in AGENT_RUN_BOUND_OPERATIONS:
            return
        target = params.get("runId") or params.get("taskId")
        selectors = (
            ("requestId", "SELECT task_id FROM tasks WHERE request_id=?"),
            ("attemptId", "SELECT task_id FROM attempts WHERE attempt_id=?"),
            ("messageId", "SELECT task_id FROM messages WHERE message_id=?"),
        )
        with self.db.read() as connection:
            for key, query in selectors:
                if target is not None:
                    break
                value = params.get(key)
                if isinstance(value, str) and value:
                    row = connection.execute(query, (value,)).fetchone()
                    target = row["task_id"] if row is not None else "__unknown__"
        if target is not None and target != scope["runId"]:
            raise BoardError(
                "FORBIDDEN",
                "An attempt-scoped credential can only address its own run, whatever selector it supplies",
                runId=scope["runId"],
            )
        if target is None:
            params["runId"] = scope["runId"]

    # -- workspace reservations ---------------------------------------------
    def _held_writer(self, connection, checkout_id: str):
        return connection.execute(
            "SELECT * FROM workspace_reservations WHERE checkout_id=? AND state='held' AND access='write'",
            (checkout_id,),
        ).fetchone()

    def _reserve(
        self,
        connection,
        *,
        manifest: dict,
        holder_task_id: str,
        holder_kind: str,
        parent_run_id: str | None,
        access: str,
        now: str,
        transfer_from: str | None = None,
    ) -> None:
        checkout_id = manifest.get("checkoutId")
        manifest_sha = manifest.get("manifestSha256")
        if not checkout_id or not manifest_sha:
            raise BoardError("WORKSPACE_INVALID", "The prepared workspace manifest is missing checkoutId/manifestSha256")
        writer = self._held_writer(connection, checkout_id)
        if access == "write":
            if writer is not None and writer["holder_task_id"] != holder_task_id:
                if transfer_from is not None and writer["holder_task_id"] == transfer_from:
                    connection.execute(
                        "UPDATE workspace_reservations SET state='transferred', updated_at=? WHERE reservation_id=?",
                        (now, writer["reservation_id"]),
                    )
                else:
                    raise BoardError(
                        "PREPARATION_CONFLICT",
                        "Another writer holds this checkout; a sibling cwd path of one checkout cannot bypass the "
                        "write exclusion. Wait for its release or use an isolated worktree.",
                        checkoutId=checkout_id,
                        holderTaskId=writer["holder_task_id"],
                    )
        else:
            for row in connection.execute(
                "SELECT * FROM workspace_reservations WHERE checkout_id=? AND state='held'", (checkout_id,)
            ).fetchall():
                if row["manifest_sha256"] != manifest_sha:
                    raise BoardError(
                        "SNAPSHOT_CHANGED",
                        "Read sharing requires one unchanged snapshot; this checkout already has a reservation for "
                        "a different manifest",
                        checkoutId=checkout_id,
                    )
        existing = connection.execute(
            "SELECT * FROM workspace_reservations WHERE holder_task_id=? AND state='held'", (holder_task_id,)
        ).fetchone()
        if existing is not None and existing["checkout_id"] == checkout_id:
            return
        connection.execute(
            "INSERT INTO workspace_reservations(reservation_id, workspace_id, checkout_id, repository_id, path,"
            " access, holder_task_id, holder_kind, parent_run_id, manifest_sha256, state, created_at, updated_at,"
            " released_at) VALUES(?,?,?,?,?,?,?,?,?,?,'held',?,?,NULL)",
            (
                str(uuid.uuid4()),
                manifest.get("workspaceId") or checkout_id,
                checkout_id,
                manifest.get("repositoryId"),
                manifest.get("path"),
                access,
                holder_task_id,
                holder_kind,
                parent_run_id,
                manifest_sha,
                now,
                now,
            ),
        )

    def _release_reservations(self, connection, holder_task_id: str, now: str, *, state: str = "released") -> None:
        # A transferred reservation is still this task's logical ownership: cancelling
        # or accepting the goal releases it too, so no writer claim leaks forever.
        connection.execute(
            "UPDATE workspace_reservations SET state=?, updated_at=?, released_at=? WHERE holder_task_id=?"
            " AND state IN ('held','transferred')",
            (state, now, now if state == "released" else None, holder_task_id),
        )

    def _reclaim_manual_workspace(self, connection, task, run_row, now: str) -> None:
        """Reacquire released ownership under the same explicit continuation transaction."""
        manifest = json.loads(run_row["workspace_manifest_json"] or "{}")
        if not manifest:
            return
        reservations = connection.execute(
            "SELECT * FROM workspace_reservations WHERE holder_task_id=? ORDER BY rowid DESC", (task["task_id"],),
        ).fetchall()
        current = next((row for row in reservations if row["state"] in ("held", "transferred")), None)
        reservation = current or next((row for row in reservations if row["checkout_id"] == manifest.get("checkoutId")), None)
        if reservation is None or task["cwd"] != manifest.get("path") or any(
            reservation[key] != manifest.get(field) for key, field in (
                ("checkout_id", "checkoutId"), ("repository_id", "repositoryId"), ("path", "path"), ("access", "access"),
            )
        ):
            raise BoardError("PREPARATION_CONFLICT", "The continuation no longer owns its recorded checkout allocation")
        if reservation["state"] == "transferred":
            # The authorized helper still owns it. Its normal settlement restores
            # this reservation; a manual continuation cannot take it back early.
            return
        conflicts = connection.execute(
            "SELECT * FROM workspace_reservations WHERE checkout_id=? AND holder_task_id!=?"
            " AND (state='held' OR (?='released' AND state='transferred')) AND (access='write' OR ?='write')",
            (manifest["checkoutId"], task["task_id"], reservation["state"], manifest["access"]),
        ).fetchall()
        if conflicts:
            raise BoardError("PREPARATION_CONFLICT", "Another owner holds the continuation checkout; wait for release before continuing",
                             checkoutId=manifest["checkoutId"], holderTaskId=conflicts[0]["holder_task_id"])
        if reservation["state"] == "held":
            return
        if not self._stop_proven(connection, task):
            raise BoardError("SHUTDOWN_UNCONFIRMED", "A released checkout can only be reacquired after every attempt is confirmed stopped")
        self._reserve(connection, manifest=manifest, holder_task_id=task["task_id"],
                      holder_kind=reservation["holder_kind"], parent_run_id=reservation["parent_run_id"],
                      access=manifest["access"], now=now)
        self.board._append_event(connection, "workflow.workspace_reclaimed", task_id=task["task_id"],
                                 payload={"checkoutId": manifest["checkoutId"], "previousReservationId": reservation["reservation_id"]})

    def _stopped_parent(self, connection, run_id: str) -> bool:
        task = connection.execute("SELECT * FROM tasks WHERE task_id=?", (run_id,)).fetchone()
        if task is None or task["active_attempt_id"] is not None:
            return False
        attempt = self.board._selected_attempt(connection, task)
        return attempt is not None and attempt["execution_state"] == "finished" and bool(attempt["shutdown_confirmed"])

    def _pin_artifact(
        self,
        connection,
        *,
        run_id: str,
        kind: str,
        manifest: dict,
        attempt_id: str | None,
        turn_id: str | None,
        source_task_id: str | None,
        now: str,
    ) -> str | None:
        # A sealed output is identified by the snapshot it produced; the input
        # manifest digest it was based on stays inside the pinned manifest JSON.
        identity_key = "snapshotSha256" if kind in ("output", "resolved-output", "abandoned-site") else "manifestSha256"
        manifest_sha = manifest.get(identity_key)
        if not manifest_sha:
            raise BoardError(
                "WORKSPACE_INVALID",
                f"the pinned {kind} manifest has no {identity_key} identity",
                kind=kind,
            )
        row = connection.execute(
            "SELECT artifact_id FROM workflow_artifacts WHERE run_id=? AND attempt_id IS ? AND manifest_sha256=?",
            (run_id, attempt_id, manifest_sha),
        ).fetchone()
        if row is not None:
            return row["artifact_id"]
        artifact_id = str(uuid.uuid4())
        connection.execute(
            "INSERT INTO workflow_artifacts(artifact_id, run_id, turn_id, attempt_id, source_task_id, kind,"
            " manifest_json, manifest_sha256, created_at) VALUES(?,?,?,?,?,?,?,?,?)",
            (
                artifact_id,
                run_id,
                turn_id,
                attempt_id,
                source_task_id,
                kind,
                canonical_json(manifest),
                manifest_sha,
                now,
            ),
        )
        return artifact_id

    # -- submit --------------------------------------------------------------
    @staticmethod
    def _configuration(run_row) -> dict | None:
        value = run_row["execution_configuration_json"]
        return json.loads(value) if value else None

    def _validated_configuration(self, spec: dict) -> dict | None:
        """Inspect native configuration only outside a database write transaction."""
        constraints = schemas.configuration_constraints(spec)
        if constraints.get("adapter") in ("command", "external"):
            return constraints
        if len(constraints) != len(schemas.CONFIGURATION_FIELDS):
            return None
        from .catalog import validate_configuration

        return validate_configuration(schemas.normalize_configuration(constraints), directory=self.board.directory)

    @staticmethod
    def _execution_adapter(name: str):
        from .adapters import adapter

        return adapter(name)

    def effective_spec(self, connection, task) -> dict:
        """Execution projection; the stored original spec/fingerprint never changes."""
        spec = json.loads(task["spec_json"])
        run = self._run_optional(connection, task["task_id"])
        if run is not None:
            spec = {**spec, **(self._configuration(run) or {}), "cwd": task["cwd"]}
        return spec

    def _routing_view(self, connection, run) -> dict:
        goal = json.loads(run["goal_json"])
        preferences = goal.get("routingPreferences", [])
        if not run["current_routing_id"]:
            request = self._open_boundary(connection, run)
            needs_host = request is not None and json.loads(request["payload_json"]).get("source") == "routing"
            override = connection.execute(
                "SELECT payload_json FROM events WHERE task_id=? AND kind='workflow.configuration_overridden' ORDER BY seq DESC LIMIT 1",
                (run["run_id"],),
            ).fetchone()
            override_payload = json.loads(override["payload_json"]) if override else None
            source = ("host-override" if override_payload and
                      override_payload.get("configurationRevision") == run["execution_configuration_revision"]
                      else "original-explicit" if len(schemas.configuration_constraints(goal)) == len(schemas.CONFIGURATION_FIELDS)
                      else None)
            return {"status": "needs-host" if needs_host else "explicit", "decisionId": None, "taskId": None,
                    "constraints": schemas.configuration_constraints(goal), "routingPreferences": preferences,
                    "source": source}
        link = connection.execute("SELECT * FROM workflow_routes WHERE decision_id=?", (run["current_routing_id"],)).fetchone()
        decision = self.board.decisions._row(connection, run["current_routing_id"])
        status = decision["status"] if link["state"] == "pending" else {
            "resolved": "completed", "needs-host": "needs-host", "fenced": "fenced",
        }[link["state"]]
        return {
            "status": status, "decisionId": decision["decision_id"], "taskId": decision["decision_task_id"],
            "attemptId": decision["decision_attempt_id"], "generation": decision["decision_generation"],
            "selectedProfile": json.loads(decision["selected_json"]) if decision["selected_json"] else None,
            "tableRevision": decision["table_revision"], "configurationRevision": decision["configuration_revision"],
            "reason": link["reason"] or decision["reason"],
            "constraints": schemas.configuration_constraints(goal), "routingPreferences": preferences,
            "source": "model-selection",
            "preferenceOutcome": (json.loads(decision["selected_json"]).get("routingPreference")
                                  if decision["selected_json"] else None),
        }

    def _start_routing(self, connection, run_id: str, now: str) -> None:
        run = self._run_row(connection, run_id)
        sequence = connection.execute("SELECT COUNT(*) FROM workflow_routes WHERE run_id=?", (run_id,)).fetchone()[0] + 1
        response = self.board.decisions.route_workflow(
            connection, run_id=run_id, sequence=sequence, spec=json.loads(run["goal_json"]),
        )
        decision_id = response["decisionId"]
        connection.execute(
            "INSERT INTO workflow_routes(decision_id,run_id,owner_generation,state,created_at,updated_at)"
            " VALUES(?,?,?,'pending',?,?)", (decision_id, run_id, run["owner_generation"], now, now),
        )
        connection.execute(
            "UPDATE workflow_runs SET current_routing_id=?, execution_configuration_json=NULL,"
            " validated_configuration_revision=NULL WHERE run_id=?",
            (decision_id, run_id),
        )
        connection.execute("UPDATE tasks SET adapter='unresolved', queue_reason='awaiting-model-selection' WHERE task_id=?", (run_id,))
        self.board._append_event(connection, "workflow.routing_requested", task_id=run_id,
                                 payload={"decisionId": decision_id, "routingTaskId": response["runId"],
                                          "ownerGeneration": run["owner_generation"],
                                          "routingPreferences": json.loads(run["goal_json"]).get("routingPreferences", [])})
        self.routing_settled(connection, decision_id=decision_id, now=now)

    def routing_settled(self, connection, *, decision_id: str, now: str) -> None:
        """Adopt a frozen recommendation with its result, under owner/lineage fencing."""
        link = connection.execute("SELECT * FROM workflow_routes WHERE decision_id=?", (decision_id,)).fetchone()
        if link is None or link["state"] != "pending":
            return
        run = self._run_row(connection, link["run_id"])
        decision = self.board.decisions._row(connection, decision_id)
        if decision["status"] in ("queued", "running"):
            return
        if (run["current_routing_id"] != decision_id or run["owner_generation"] != link["owner_generation"]
                or run["state"] != "executing" or self._terminal_ancestor(connection, run["run_id"]) is not None):
            connection.execute("UPDATE workflow_routes SET state='fenced',updated_at=? WHERE decision_id=?", (now, decision_id))
            return
        selected = json.loads(decision["selected_json"]) if decision["selected_json"] else None
        if decision["status"] == "completed" and selected is not None:
            configuration = schemas.configuration_constraints(selected)
            constraints = schemas.configuration_constraints(json.loads(run["goal_json"]))
            if (len(configuration) == len(schemas.CONFIGURATION_FIELDS)
                    and configuration.get("adapter") in schemas.CODING_ADAPTERS
                    and all(configuration.get(key) == value for key, value in constraints.items())):
                connection.execute("UPDATE workflow_routes SET state='resolved',reason=?,updated_at=? WHERE decision_id=?",
                                   (decision["reason"], now, decision_id))
                connection.execute(
                    "UPDATE workflow_runs SET execution_configuration_json=?, execution_configuration_revision=execution_configuration_revision+1,"
                    " validated_configuration_revision=NULL,updated_at=?,revision=revision+1 WHERE run_id=?",
                    (canonical_json(configuration), now, run["run_id"]),
                )
                connection.execute("UPDATE tasks SET adapter=?,queue_reason='awaiting-configuration-validation' WHERE task_id=?",
                                   (configuration["adapter"], run["run_id"]))
                self.board._append_event(connection, "workflow.routing_resolved", task_id=run["run_id"],
                                         payload={"decisionId": decision_id, "configuration": configuration,
                                                  "profileId": decision["profile_id"], "tableRevision": decision["table_revision"],
                                                  "configurationRevision": decision["configuration_revision"],
                                                  "source": "model-selection",
                                                  "preferenceOutcome": selected.get("routingPreference")})
                return
        self._routing_attention(connection, run, now,
                                decision["error"] or decision["reason"] or "No legal coding configuration was selected")

    def _routing_attention(self, connection, run, now: str, reason: str) -> None:
        """A service boundary, with no invented agent turn or completed Goal."""
        reason = str(reason)[:2000]
        if run["current_routing_id"]:
            connection.execute("UPDATE workflow_routes SET state='needs-host',reason=?,updated_at=? WHERE decision_id=?",
                               (reason, now, run["current_routing_id"]))
        request_id = f"routing-{uuid.uuid4()}"
        payload = {"source": "routing", "decisionId": run["current_routing_id"], "summary": reason,
                   "neededWork": ["Continue with a complete configuration, or fix the selector/catalog and continue with reroute=true."],
                   "attempted": "Bounded model routing and native configuration validation",
                   "acceptance": "A legal adapter/provider/model/effort tuple is ready for execution"}
        connection.execute(
            "INSERT INTO workflow_requests(request_id,run_id,kind,summary,payload_json,state,expected_revision,created_at,updated_at)"
            " VALUES(?,?,'attention',?,?,'open',?,?,?)",
            (request_id, run["run_id"], reason, canonical_json(payload), run["revision"] + 1, now, now),
        )
        connection.execute("UPDATE workflow_runs SET state='awaiting-host',active_request_id=?,updated_at=?,revision=revision+1 WHERE run_id=?",
                           (request_id, now, run["run_id"]))
        connection.execute("UPDATE tasks SET queue_reason='awaiting-host' WHERE task_id=?", (run["run_id"],))
        self._propagate_boundary(connection, self._request_row(connection, run["run_id"], request_id), now)
        self.board._append_event(connection, "workflow.routing_needs_host", task_id=run["run_id"],
                                 payload={"requestId": request_id, "decisionId": run["current_routing_id"], "reason": reason})

    @staticmethod
    def _dispatch_identity(run) -> tuple:
        return (run["owner_generation"], run["revision"], run["current_routing_id"],
                run["execution_configuration_revision"], run["execution_configuration_json"])

    def prepare_dispatch(self, params: dict) -> None:
        """Validate each new resolved tuple once, outside write transactions.

        Persist the exact configuration revision under a snapshot fence. Polls do
        not repeat native discovery while waiting for capacity; the adapter's own
        prepare/readback is still the final native check at actual process start.
        """
        selector = params.get("taskId") or params.get("runId")
        with self.db.read() as connection:
            runs = connection.execute(
                "SELECT r.* FROM workflow_runs r JOIN tasks t ON t.task_id=r.run_id"
                " WHERE t.state='queued' AND r.state='executing' AND r.execution_configuration_json IS NOT NULL"
                " AND r.validated_configuration_revision IS NOT r.execution_configuration_revision"
                + (" AND r.run_id=?" if selector else " ORDER BY t.created_at,t.task_id LIMIT 50"),
                (selector,) if selector else (),
            ).fetchall()
        for run in runs:
            configuration = self._configuration(run)
            if configuration.get("adapter") not in schemas.CODING_ADAPTERS:
                continue
            try:
                checked = self._validated_configuration(configuration)
                if checked != configuration:
                    raise BoardError("INVALID_CONFIGURATION", "The installed native tuple changed; choose a current configuration")
            except BoardError as error:
                with self.db.write() as connection:
                    current = self._run_row(connection, run["run_id"])
                    task = connection.execute("SELECT * FROM tasks WHERE task_id=?", (run["run_id"],)).fetchone()
                    if (current["state"] == "executing" and task["state"] == "queued"
                            and self._dispatch_identity(current) == self._dispatch_identity(run)):
                        self._routing_attention(connection, current, self.now(), f"{error.code}: {error.message}")
                    head = self.board._head_of(connection)
                self.board._notify(head)
            else:
                with self.db.write() as connection:
                    current = self._run_row(connection, run["run_id"])
                    if (current["state"] == "executing" and self._dispatch_identity(current) == self._dispatch_identity(run)
                            and current["validated_configuration_revision"] != current["execution_configuration_revision"]):
                        connection.execute("UPDATE workflow_runs SET validated_configuration_revision=execution_configuration_revision WHERE run_id=?", (run["run_id"],))
                        self.board._append_event(connection, "workflow.configuration_validated", task_id=run["run_id"],
                                                 payload={"configurationRevision": run["execution_configuration_revision"],
                                                          "configuration": configuration})
                    head = self.board._head_of(connection)
                self.board._notify(head)

    def attach_governed_task(
        self,
        connection,
        task_row,
        *,
        hostId: str,
        spec: dict,
        executionWorkspace: dict,
        manifest: dict,
        goalFingerprint: str,
        requestFingerprint: str,
        executionConfiguration: dict | None = None,
        submissionToken: str | None = None,
        now: str,
    ) -> str:
        """Insert the run, its reservation and its pinned input manifest.

        Runs inside :meth:`buddy.store.BoardStore.task_submit`'s transaction, so the
        task and its governance record are one durable fact.
        """
        run_id = task_row["task_id"]
        access = executionWorkspace.get("access", "write")
        connection.execute(
            "INSERT INTO workflow_runs(run_id, host_id, owner_generation, control_verifier, goal_json,"
            " goal_fingerprint, request_fingerprint, submission_verifier, execution_workspace_json,"
            " workspace_manifest_json, workspace_id, workspace_manifest_sha256, state, revision, created_at,"
            " updated_at) VALUES(?,?,1,?,?,?,?,?,?,?,?,?,'executing',1,?,?)",
            (
                run_id,
                hostId,
                self.db.control_token_verifier(self.db.control_token(run_id, 1)),
                canonical_json(spec),
                goalFingerprint,
                requestFingerprint,
                self.db.agent_token_verifier(submissionToken) if submissionToken else None,
                canonical_json(executionWorkspace),
                canonical_json(manifest) if manifest else None,
                manifest.get("workspaceId") if manifest else None,
                manifest.get("manifestSha256") if manifest else None,
                now,
                now,
            ),
        )
        if manifest:
            # The column tracks the effective execution checkout (a prepared worktree
            # path), while spec_json/input_fingerprint keep the immutable original
            # spec. Admission and resource claims use the column.
            if manifest.get("path"):
                connection.execute(
                    "UPDATE tasks SET cwd=?, updated_at=? WHERE task_id=?",
                    (manifest["path"], now, run_id),
                )
            # The original authorization is recorded once as scope version 1; every
            # later amendment appends a new version instead of editing this one.
            connection.execute(
                "INSERT INTO workflow_scope_versions(run_id, scope_version, actor, reason, write_scope_json,"
                " manifest_sha256, stopped_evidence_json, created_at) VALUES(?,1,?,?,?,?,?,?)",
                (run_id, f"host:{hostId}", "original submission", canonical_json(list(manifest.get("writeScope") or [])),
                 manifest.get("manifestSha256"), canonical_json({"kind": "initial"}), now),
            )
            self._reserve(
                connection,
                manifest=manifest,
                holder_task_id=run_id,
                holder_kind="parent",
                parent_run_id=run_id,
                access=access,
                now=now,
            )
            self._pin_artifact(
                connection,
                run_id=run_id,
                kind="input",
                manifest=manifest,
                attempt_id=None,
                turn_id=None,
                source_task_id=None,
                now=now,
            )
        if executionConfiguration is None:
            self._start_routing(connection, run_id, now)
        else:
            connection.execute("UPDATE workflow_runs SET execution_configuration_json=?,execution_configuration_revision=1,validated_configuration_revision=1 WHERE run_id=?",
                               (canonical_json(executionConfiguration), run_id))
            self.board._append_event(connection, "workflow.configuration_selected", task_id=run_id,
                                     payload={"source": "original-explicit", "configuration": executionConfiguration,
                                              "configurationRevision": 1,
                                              "routingPreferences": spec.get("routingPreferences", [])})
        return run_id

    def submit(self, params: dict) -> dict:
        """Prepare the workspace, admit one ordinary governed task and return control.

        Authority is a private submission capability chosen by the caller before the
        first RPC, never the ``hostId`` label: a client that can read a run can never
        recover its owner capability by guessing attribution. On replay, control is
        returned only for the original owner generation and only to the caller that
        still presents the same submission capability.
        """
        normalized = schemas.normalize_workflow_submit(params)
        request_id = normalized["requestId"]
        spec = normalized["spec"]
        host_id = normalized["hostId"]
        submission_token = normalized["submissionToken"]
        fingerprint = schemas.spec_fingerprint(spec)
        request_fingerprint = schemas.workflow_request_fingerprint(
            spec, normalized["executionWorkspace"], host_id
        )

        # Recovery first: an idempotent submit must recover the original prepared
        # snapshot before it inspects a source tree that may have changed since.
        with self.db.read() as connection:
            existing = connection.execute("SELECT * FROM tasks WHERE request_id=?", (request_id,)).fetchone()
            if existing is not None:
                run_row = self._run_optional(connection, existing["task_id"])
                if run_row is None:
                    raise BoardError(
                        "CONFLICT",
                        "This requestId belongs to a task that is not a governed workflow run; use a new requestId",
                        requestId=request_id,
                        existingTaskId=existing["task_id"],
                    )
                if existing["input_fingerprint"] != fingerprint:
                    raise BoardError(
                        "CONFLICT",
                        "requestId already belongs to a governed run with a different specification",
                        requestId=request_id,
                        existingTaskId=existing["task_id"],
                    )
                if run_row["request_fingerprint"] != request_fingerprint:
                    raise BoardError(
                        "CONFLICT",
                        "requestId already belongs to a governed run with a different execution workspace or Host",
                        requestId=request_id,
                        existingTaskId=existing["task_id"],
                    )
                response = {**self.compact(connection, run_row, existing), "duplicate": True}
                control = self._recovered_control(run_row, submission_token)
                if control is not None:
                    response["control"] = control
                else:
                    response["controlAvailable"] = False
                return response

        configuration = self._validated_configuration(spec)
        manifest = workspace_module().prepare(self.board.directory, request_id, normalized["executionWorkspace"])

        store_params = {
            "requestId": request_id,
            "owner": normalized["owner"] or f"host:{host_id}",
            **{key: spec[key] for key in schemas.SUBMIT_FIELDS if key in spec},
        }
        task_id = str(uuid.uuid4())
        try:
            response = self.board.task_submit(
                store_params,
                task_id=task_id,
                governed={
                    "hostId": host_id,
                    "spec": spec,
                    "executionWorkspace": normalized["executionWorkspace"],
                    "manifest": manifest,
                    "goalFingerprint": fingerprint,
                    "requestFingerprint": request_fingerprint,
                    "submissionToken": submission_token,
                    "executionConfiguration": configuration,
                },
            )
        except BoardError as error:
            if error.code == "CONFLICT":
                # A concurrent identical submit won the race. Its pinned snapshot is
                # authoritative; ours stays on disk as a recoverable artifact keyed by
                # the request id and is reported in the event stream.
                with self.db.write() as connection:
                    task = connection.execute("SELECT * FROM tasks WHERE request_id=?", (request_id,)).fetchone()
                    run_row = self._run_optional(connection, task["task_id"]) if task is not None else None
                    identical = (
                        task is not None
                        and run_row is not None
                        and task["input_fingerprint"] == fingerprint
                        and run_row["request_fingerprint"] == request_fingerprint
                    )
                    if not identical:
                        # A different input for the same requestId is a conflict, never
                        # a silent reuse of another client's prepared snapshot.
                        raise
                    self.board._append_event(
                        connection,
                        "workflow.submit_race",
                        payload={"requestId": request_id, "preparedManifestSha256": manifest.get("manifestSha256")},
                    )
                    head = self.board._head_of(connection)
                self.board._notify(head)
                return {**self.compact_read(run_row["run_id"]), "duplicate": True}
            raise
        if response["duplicate"]:
            with self.db.read() as connection:
                run = self._run_row(connection, response["task"]["runId"])
                view = {**self.compact(connection, run), "duplicate": True}
                control = self._recovered_control(run, submission_token)
                if control is not None:
                    view["control"] = control
                else:
                    view["controlAvailable"] = False
                return view
        return self._submitted_view(response["task"]["runId"])

    def _recovered_control(self, run_row, submission_token: str | None) -> dict | None:
        """Control for an idempotent replay, fenced by the private submission capability.

        A replay never returns a post-takeover generation: after a takeover the new
        owner holds the capability, and the original submission cannot recover it.
        """
        import hmac

        if not submission_token or not run_row["submission_verifier"]:
            return None
        if run_row["owner_generation"] != 1:
            return None
        if not hmac.compare_digest(self.db.agent_token_verifier(submission_token), run_row["submission_verifier"]):
            return None
        return {
            "hostId": run_row["host_id"],
            "ownerGeneration": run_row["owner_generation"],
            "controlToken": self.db.control_token(run_row["run_id"], run_row["owner_generation"]),
        }

    def _submitted_view(self, run_id: str) -> dict:
        """One fresh submission returns its owner capability exactly once."""
        with self.db.read() as connection:
            run_row = self._run_row(connection, run_id)
            view = self.compact(connection, run_row)
            control = {
                "hostId": run_row["host_id"],
                "ownerGeneration": run_row["owner_generation"],
                "controlToken": self.db.control_token(run_id, run_row["owner_generation"]),
            }
        return {**view, "duplicate": False, "control": control}

    def compact_read(self, run_id: str) -> dict:
        with self.db.read() as connection:
            run_row = self._run_row(connection, run_id)
            return self.compact(connection, run_row)

    def get(self, params: dict) -> dict:
        schemas.reject_unknown(params, {"runId", "includeAudit", "routingHistory"}, "workflow.get")
        run_id = schemas.required_string(params, "runId", max_length=128)
        include_audit = schemas.optional_bool(params, "includeAudit", False)
        raw_history = params.get("routingHistory")
        history_limit: int | None = None
        history_before: int | None = None
        if raw_history is not None:
            history = schemas.require_object(raw_history, "routingHistory")
            schemas.reject_unknown(history, ROUTING_HISTORY_FIELDS, "routingHistory")
            history_limit = schemas.optional_int(
                history, "limit", DEFAULT_ROUTING_HISTORY_LIMIT, 1, MAX_ROUTING_HISTORY_LIMIT
            )
            history_before = schemas.optional_positive_int(history, "before")
        with self.db.read() as connection:
            task = connection.execute("SELECT * FROM tasks WHERE task_id=?", (run_id,)).fetchone()
            if task is None:
                raise BoardError("NOT_FOUND", "Unknown runId", runId=run_id)
            run_row = self._run_optional(connection, run_id)
            if run_row is None:
                view = {"governed": False, "runId": run_id, "task": self.board._decorate(connection, task)}
                if raw_history is not None:
                    view["routingHistory"] = {"entries": [], "nextCursor": None, "total": 0}
                return view
            view = self.compact(connection, run_row, task)
            if raw_history is not None:
                # Optional newest-first page of this run's own routing decisions;
                # without it the compact shape is unchanged.
                view["routingHistory"] = self._routing_history(
                    connection, run_row, limit=history_limit, before=history_before
                )
            if include_audit:
                view["audit"] = self._audit(connection, run_row)
        return view

    def _routing_history(self, connection, run_row, *, limit: int, before: int | None) -> dict:
        """One bounded page of this run's routing decisions, newest first.

        ``workflow_routes.rowid`` is the immutable insertion order, so it is the
        stable descending keyset: the run filter is applied before the limit, an
        explicit before-cursor never shifts when a newer decision arrives, and every
        earlier reroute stays reachable. ``total`` is the complete routing count of
        the run, independent of the cursor. Only compact decision fields are exposed;
        no task prompt, model input/output or table payload is embedded.
        """
        run_id = run_row["run_id"]
        total = int(
            connection.execute(
                "SELECT COUNT(*) AS count FROM workflow_routes WHERE run_id=?", (run_id,)
            ).fetchone()["count"]
        )
        query = (
            "SELECT r.rowid AS route_rowid, r.decision_id, r.owner_generation, r.state, r.reason,"
            " r.created_at, d.status AS decision_status, d.table_revision, d.reason AS decision_reason,"
            " q.task_id AS decision_task_id, q.configuration_revision, q.selected_json"
            " FROM workflow_routes r"
            " JOIN evaluation_decisions d ON d.decision_id=r.decision_id"
            " JOIN decision_requests q ON q.decision_id=r.decision_id"
            " WHERE r.run_id=?"
        )
        parameters: list[Any] = [run_id]
        if before is not None:
            query += " AND r.rowid<?"
            parameters.append(before)
        query += " ORDER BY r.rowid DESC LIMIT ?"
        parameters.append(limit)
        rows = connection.execute(query, parameters).fetchall()
        current_id = run_row["current_routing_id"]
        entries = [
            {
                "decisionId": row["decision_id"],
                # The same effective mapping the current routing view uses: a decision
                # still in flight reports its own status; a settled route reports what
                # it actually became.
                "status": (
                    row["decision_status"]
                    if row["state"] == "pending"
                    else {"resolved": "completed", "needs-host": "needs-host", "fenced": "fenced"}[row["state"]]
                ),
                "taskId": row["decision_task_id"],
                "selectedProfile": json.loads(row["selected_json"]) if row["selected_json"] else None,
                "source": "model-selection",
                "preferenceOutcome": (json.loads(row["selected_json"]).get("routingPreference")
                                      if row["selected_json"] else None),
                "tableRevision": int(row["table_revision"]),
                "configurationRevision": int(row["configuration_revision"]),
                "reason": row["reason"] or row["decision_reason"],
                "createdAt": row["created_at"],
                "ownerGeneration": int(row["owner_generation"]),
                "current": bool(current_id) and row["decision_id"] == current_id,
            }
            for row in rows
        ]
        next_cursor = None
        if len(rows) == limit:
            last_rowid = int(rows[-1]["route_rowid"])
            more = connection.execute(
                "SELECT 1 FROM workflow_routes WHERE run_id=? AND rowid<? LIMIT 1", (run_id, last_rowid)
            ).fetchone()
            if more is not None:
                next_cursor = last_rowid
        return {"entries": entries, "nextCursor": next_cursor, "total": total}

    def _audit(self, connection, run_row) -> dict:
        turns = connection.execute(
            "SELECT * FROM workflow_turns WHERE run_id=? ORDER BY turn_index", (run_row["run_id"],)
        ).fetchall()
        requests = connection.execute(
            "SELECT * FROM workflow_requests WHERE run_id=? ORDER BY created_at", (run_row["run_id"],)
        ).fetchall()
        continuations = connection.execute(
            "SELECT * FROM workflow_continuations WHERE run_id=? ORDER BY created_at", (run_row["run_id"],)
        ).fetchall()
        reservations = connection.execute(
            "SELECT * FROM workspace_reservations WHERE parent_run_id=? OR holder_task_id=? ORDER BY created_at",
            (run_row["run_id"], run_row["run_id"]),
        ).fetchall()
        suggestions = connection.execute(
            "SELECT * FROM workflow_suggestions WHERE run_id=? ORDER BY created_at LIMIT 50", (run_row["run_id"],)
        ).fetchall()
        return {
            "turns": [self._turn_view(row, include_audit=True) for row in turns],
            "requests": [self._request_view(row, include_audit=True) for row in requests],
            "continuations": [
                {
                    "continuationId": row["continuation_id"],
                    "authorizedBy": row["authorized_by"],
                    "state": row["state"],
                    "requestId": row["request_id"],
                    "reason": row["reason"],
                    "helperPolicy": row["helper_policy"],
                    "helperOutcomes": json.loads(row["helper_outcomes_json"]),
                    "input": row["input_text"],
                    "expectedRevision": row["expected_revision"],
                    "attemptId": row["attempt_id"],
                    "turnId": row["turn_id"],
                    "createdAt": row["created_at"],
                    "consumedAt": row["consumed_at"],
                }
                for row in continuations
            ],
            "reservations": [
                {
                    "reservationId": row["reservation_id"],
                    "checkoutId": row["checkout_id"],
                    "workspaceId": row["workspace_id"],
                    "path": row["path"],
                    "access": row["access"],
                    "holderTaskId": row["holder_task_id"],
                    "holderKind": row["holder_kind"],
                    "state": row["state"],
                    "manifestSha256": row["manifest_sha256"],
                    "createdAt": row["created_at"],
                    "releasedAt": row["released_at"],
                }
                for row in reservations
            ],
            "suggestions": [
                {
                    "suggestionId": row["suggestion_id"],
                    "attemptId": row["attempt_id"],
                    "author": row["author"],
                    "body": row["body"],
                    "createdAt": row["created_at"],
                }
                for row in suggestions
            ],
            "ownerGeneration": run_row["owner_generation"],
            "goalFingerprint": run_row["goal_fingerprint"],
            "goal": json.loads(run_row["goal_json"]),
            "spec": _original_spec(connection, run_row["run_id"]),
        }

    # -- decide --------------------------------------------------------------
    def decide(self, params: dict, *, console_authority: dict | None = None) -> dict:
        schemas.reject_unknown(
            params,
            {
                "runId",
                "requestId",
                "commandId",
                "expectedRevision",
                "decision",
                "reason",
                "helpers",
                "autoContinue",
                *schemas.CONTROL_FIELDS,
                schemas.CONSOLE_AUTHORITY_FIELD,
            },
            "workflow.decide",
        )
        schemas.reject_untrusted_override(params)
        run_id = schemas.required_string(params, "runId", max_length=128)
        request_id = schemas.required_string(params, "requestId", max_length=128)
        command_id = schemas.required_string(params, "commandId", max_length=128)
        expected = schemas.require_expected_revision(params)
        decision = schemas.required_string(params, "decision", max_length=16)
        if decision not in ("approve", "decline"):
            raise BoardError("INVALID_ARGUMENT", "decision must be 'approve' or 'decline'")
        reason = schemas.optional_string(params, "reason", max_length=schemas.MAX_WORKFLOW_REASON_BYTES) or ""
        auto_continue = schemas.optional_bool(params, "autoContinue", True)
        helpers = schemas.normalize_helpers(params)
        request_key = {
            "runId": run_id,
            "requestId": request_id,
            "decision": decision,
            "reason": reason,
            "autoContinue": auto_continue,
            "expectedRevision": expected,
            "helpers": [
                {
                    "requestId": item["requestId"],
                    "spec": item["spec"],
                    "executionWorkspace": item["executionWorkspace"],
                    "integrator": item["integrator"],
                    "inheritRoutingPreferences": item["inheritRoutingPreferences"],
                }
                for item in helpers
            ],
        }
        # Precheck before any Git work: an unauthorized caller, a stale revision, a
        # closed request or an already-recorded commandId must never trigger the
        # preparation of helper worktrees.
        precheck = self._decide_precheck(
            run_id, request_id, expected, command_id, request_key, params, console_authority
        )
        if precheck is not None:
            return precheck

        # Prepare every helper workspace outside any transaction. An interrupted or
        # losing preparation leaves its owned artifact on disk, keyed by requestId.
        prepared: list[dict] = []
        if decision == "approve":
            with self.db.read() as connection:
                parent_preferences = json.loads(self._run_row(connection, run_id)["goal_json"]).get("routingPreferences", [])
            for helper in helpers:
                if helper["inheritRoutingPreferences"]:
                    helper = {**helper, "spec": {**helper["spec"], "routingPreferences": parent_preferences}}
                configuration = self._validated_configuration(helper["spec"])
                manifest = workspace_module().prepare(
                    self.board.directory, helper["requestId"], helper["executionWorkspace"]
                )
                prepared.append({**helper, "manifest": manifest, "executionConfiguration": configuration})

        now = self.now()
        with self.db.write() as connection:
            run_row = self._run_row(connection, run_id)
            task = connection.execute("SELECT * FROM tasks WHERE task_id=?", (run_id,)).fetchone()
            actor = self._authorize(connection, run_row, params, console_authority=console_authority, action="A Host decision")
            receipt = self.board._receipt(connection, command_id, "workflow.decide", request_key)
            if receipt is not None:
                return {**receipt, "duplicate": True}
            self._expect_revision(run_row, expected)
            request_row = self._request_row(connection, run_id, request_id)
            if json.loads(request_row["payload_json"]).get("source") == "routing":
                raise BoardError("CONFIGURATION_REQUIRED", "A routing request is resolved with continue configuration or reroute, not an assistance decision")
            if request_row["state"] != "open":
                raise BoardError(
                    "CONFLICT",
                    "This assistance request is already decided; a Host decision is recorded once",
                    requestId=request_id,
                    requestState=request_row["state"],
                )
            active = self._open_boundary(connection, run_row)
            if active is None or active["request_id"] != request_id:
                # A request that is no longer the active Host boundary is closed.
                raise BoardError(
                    "CONFLICT",
                    "This request is no longer the active Host boundary of the run; re-read the run",
                    requestId=request_id,
                    activeRequestId=run_row["active_request_id"],
                )
            if request_row["expected_revision"] > run_row["revision"]:
                raise BoardError(
                    "REVISION_CONFLICT",
                    "The run changed after this request was recorded; re-read it before deciding",
                    expectedRevision=request_row["expected_revision"],
                    currentRevision=run_row["revision"],
                )
            if request_row["child_task_id"]:
                # The parent Host answers on behalf of a helper that yielded: the
                # child is resumed with the recorded decision using only the parent's
                # control capability. No child capability is ever issued or needed.
                self._resolve_helper_attention(
                    connection,
                    run_row,
                    request_row,
                    prepared,
                    decision=decision,
                    reason=reason,
                    actor=actor,
                    command_id=command_id,
                    now=now,
                )
            elif decision == "approve":
                children = self._create_helpers(connection, run_row, task, request_row, prepared, command_id, now,
                                                source_host_id=run_row["host_id"])
                connection.execute(
                    "UPDATE workflow_requests SET state='approved', decision_json=?, decision_command_id=?,"
                    " decided_at=?, updated_at=? WHERE request_id=?",
                    (
                        canonical_json({"decision": "approve", "reason": reason, "actor": actor, "helpers": children}),
                        command_id,
                        now,
                        now,
                        request_id,
                    ),
                )
                state = "waiting-helpers" if children else ("executing" if auto_continue else "awaiting-host")
                connection.execute(
                    "UPDATE workflow_runs SET state=?, active_request_id=NULL, updated_at=?,"
                    " revision=revision+1 WHERE run_id=? AND revision=?",
                    (state, now, run_id, run_row["revision"]),
                )
                if auto_continue:
                    approve_continuation = str(uuid.uuid4())
                    approval_input = (
                        reason or "Host approved assistance; continue the original goal with the helper results"
                    )
                    connection.execute(
                        "INSERT INTO workflow_continuations(continuation_id, run_id, command_id, authorized_by,"
                        " request_id, expected_revision, input_text, input_bytes, reason, helper_policy,"
                        " helper_outcomes_json, state, created_at) VALUES(?,?,?,'auto',?,?,?,?,?, 'keep','[]','recorded',?)",
                        (
                            approve_continuation,
                            run_id,
                            command_id,
                            request_id,
                            run_row["revision"],
                            approval_input,
                            len(approval_input.encode()),
                            reason or None,
                            now,
                        ),
                    )
                if not children and auto_continue:
                    # An approval with no helper work is the Host's authorization for
                    # the next turn; the same one-use continuation record applies.
                    self._requeue(
                        connection,
                        task,
                        self._run_row(connection, run_id),
                        reason=reason or "Host approved the next turn",
                        actor=actor,
                        now=now,
                        continuation_id=approve_continuation,
                    )
                self.board._append_event(
                    connection,
                    "workflow.request_approved",
                    task_id=run_id,
                    revision=run_row["revision"] + 1,
                    payload={
                        "requestId": request_id,
                        "actor": actor,
                        "helpers": [child["taskId"] for child in children],
                        "autoContinue": auto_continue,
                    },
                )
            else:
                connection.execute(
                    "UPDATE workflow_requests SET state='declined', decision_json=?, decision_command_id=?,"
                    " decided_at=?, updated_at=? WHERE request_id=?",
                    (
                        canonical_json({"decision": "decline", "reason": reason, "actor": actor}),
                        command_id,
                        now,
                        now,
                        request_id,
                    ),
                )
                if auto_continue:
                    input_text = reason or "Host declined the assistance request; continue with the current evidence"
                    decline_continuation = str(uuid.uuid4())
                    connection.execute(
                        "INSERT INTO workflow_continuations(continuation_id, run_id, command_id, authorized_by,"
                        " request_id, expected_revision, input_text, input_bytes, reason, helper_policy,"
                        " helper_outcomes_json, state, created_at) VALUES(?,?,?,'auto',?,?,?,?,?, 'keep','[]','recorded',?)",
                        (
                            decline_continuation,
                            run_id,
                            command_id,
                            request_id,
                            run_row["revision"],
                            input_text,
                            len(input_text.encode()),
                            reason or None,
                            now,
                        ),
                    )
                    self._requeue(
                        connection,
                        task,
                        run_row,
                        reason=reason or "Host declined assistance",
                        actor=actor,
                        now=now,
                        continuation_id=decline_continuation,
                    )
                else:
                    connection.execute(
                        "UPDATE workflow_runs SET state='awaiting-host', active_request_id=NULL, updated_at=?,"
                        " revision=revision+1 WHERE run_id=? AND revision=?",
                        (now, run_id, run_row["revision"]),
                    )
                self.board._append_event(
                    connection,
                    "workflow.request_declined",
                    task_id=run_id,
                    revision=run_row["revision"] + 1,
                    payload={"requestId": request_id, "actor": actor, "autoContinue": auto_continue, "reason": reason},
                )
            self._close_proxy_ancestors(connection, self._request_row(connection, run_id, request_id), now)
            self._sync_boundary(connection, run_id, now)
            run_row = self._run_row(connection, run_id)
            view = self.compact(connection, run_row)
            response = {
                **view,
                "decision": decision,
                "requestId": request_id,
                "duplicate": False,
            }
            self.board._store_receipt(
                connection, command_id, "workflow.decide", request_key, response, task_id=run_id
            )
            head = self.board._head_of(connection)
        self.board._notify(head)
        return response

    def _decide_precheck(
        self,
        run_id: str,
        request_id: str,
        expected: int,
        command_id: str,
        request_key: dict,
        params: dict,
        console_authority: dict | None,
    ) -> dict | None:
        """Read-only authority/revision/replay check before any workspace preparation."""
        with self.db.read() as connection:
            run_row = self._run_row(connection, run_id)
            # Authority is checked before a replay is returned; a delayed old owner
            # can never read back another Host's recorded response.
            self._authorize(
                connection, run_row, params, console_authority=console_authority, action="A Host decision"
            )
            receipt = self.board._receipt(connection, command_id, "workflow.decide", request_key)
            if receipt is not None:
                return {**receipt, "duplicate": True}
            self._expect_revision(run_row, expected)
            request_row = self._request_row(connection, run_id, request_id)
            active = self._open_boundary(connection, run_row)
            if json.loads(request_row["payload_json"]).get("source") == "routing":
                raise BoardError("CONFIGURATION_REQUIRED", "A routing request is resolved with continue configuration or reroute, not an assistance decision")
            if request_row["state"] != "open" or active is None or active["request_id"] != request_id:
                raise BoardError(
                    "CONFLICT",
                    "This assistance request is not the open Host boundary of the run",
                    requestId=request_id,
                    requestState=request_row["state"],
                    activeRequestId=run_row["active_request_id"],
                )
            if request_row["child_task_id"]:
                self._attention_chain(connection, run_row, request_row)
        return None

    def _open_boundary(self, connection, run_row):
        """Keep the selected open request stable, then choose a deterministic next."""
        if run_row["state"] in ("cancelled", "accepted"):
            return None
        return connection.execute(
            "SELECT * FROM workflow_requests WHERE run_id=? AND state='open'"
            " ORDER BY CASE WHEN request_id=? THEN 0 ELSE 1 END, created_at, request_id LIMIT 1",
            (run_row["run_id"], run_row["active_request_id"]),
        ).fetchone()

    def _sync_boundary(self, connection, run_id, now, *, default_state=None):
        run = self._run_row(connection, run_id)
        if run["state"] in ("cancelled", "accepted") or self._terminal_ancestor(connection, run_id) is not None:
            return None
        boundary = self._open_boundary(connection, run)
        if boundary is None and default_state is None:
            return None
        state = "awaiting-host" if boundary is not None else default_state
        request_id = boundary["request_id"] if boundary is not None else None
        connection.execute(
            "UPDATE workflow_runs SET state=?, active_request_id=?, updated_at=?, revision=revision+1 WHERE run_id=?",
            (state, request_id, now, run_id),
        )
        connection.execute(
            "UPDATE tasks SET queue_reason=?, updated_at=? WHERE task_id=?",
            ("helper-attention" if boundary is not None and boundary["child_task_id"] else "awaiting-host" if boundary is not None else "awaiting-helpers", now, run_id),
        )
        self.board._append_event(
            connection, "workflow.boundary_updated", task_id=run_id, revision=run["revision"] + 1,
            payload={"activeRequestId": request_id, "state": state},
        )
        return boundary

    @staticmethod
    def _proxy_id(parent_id, source_request_id):
        return "req-proxy-" + sha256_text(canonical_json([parent_id, source_request_id]))

    def _propagate_boundary(self, connection, source, now):
        """Persist a correlated proxy at every owned ancestor in this transaction."""
        seen = set()
        while source["run_id"] not in seen:
            seen.add(source["run_id"])
            child = connection.execute("SELECT * FROM workflow_children WHERE child_task_id=?", (source["run_id"],)).fetchone()
            if child is None:
                return
            parent = self._run_row(connection, child["parent_run_id"])
            if parent["state"] in ("cancelled", "accepted") or self._terminal_ancestor(connection, parent["run_id"]) is not None:
                return
            payload = json.loads(source["payload_json"])
            payload["origin"] = payload.get("origin") or {"runId": source["run_id"], "requestId": source["request_id"]}
            payload["proxy"] = {"runId": source["run_id"], "requestId": source["request_id"]}
            request_id = self._proxy_id(parent["run_id"], source["request_id"])
            connection.execute(
                "INSERT OR IGNORE INTO workflow_requests(request_id,run_id,turn_id,attempt_id,child_task_id,kind,summary,"
                "payload_json,state,expected_revision,created_at,updated_at) VALUES(?,?,?,?,?,'helper-attention',?,?,'open',?,?,?)",
                (request_id, parent["run_id"], source["turn_id"], source["attempt_id"], source["run_id"], source["summary"],
                 canonical_json(payload), parent["revision"], now, now),
            )
            source = self._request_row(connection, parent["run_id"], request_id)
            if source["state"] != "open":
                return  # A superseded proxy is not reopened by a delayed callback.
            connection.execute("UPDATE workflow_children SET state='attention', updated_at=?, revision=revision+1 WHERE child_task_id=?", (now, child["child_task_id"]))
            self._sync_boundary(connection, parent["run_id"], now)

    def _attention_chain(self, connection, run, request):
        chain, seen = [], set()
        while request["request_id"] not in seen:
            seen.add(request["request_id"])
            self._assert_lineage_open(connection, run["run_id"])
            if request["state"] != "open" or run["state"] in ("cancelled", "accepted"):
                raise BoardError("CONFLICT", "The proxied Host boundary is no longer open")
            chain.append((run, request))
            child_id = request["child_task_id"]
            if not child_id:
                return chain
            link = connection.execute("SELECT 1 FROM workflow_children WHERE child_task_id=? AND parent_run_id=?", (child_id, run["run_id"])).fetchone()
            if link is None:
                raise BoardError("CONFLICT", "The proxy does not name an owned child")
            run = self._run_row(connection, child_id)
            proxy = json.loads(request["payload_json"]).get("proxy")
            if proxy is not None and proxy.get("runId") != child_id:
                raise BoardError("CONFLICT", "The proxy source does not match its owned child")
            source_id = proxy.get("requestId") if proxy is not None else run["active_request_id"]
            request = self._request_row(connection, child_id, source_id)
        raise BoardError("CONFLICT", "The Host boundary proxy chain contains a cycle")

    def _close_proxy_ancestors(self, connection, source, now):
        """A local Host/console answer also closes its exact ancestor proxies."""
        seen = set()
        while source["state"] != "open" and source["run_id"] not in seen:
            seen.add(source["run_id"])
            link = connection.execute("SELECT parent_run_id FROM workflow_children WHERE child_task_id=?", (source["run_id"],)).fetchone()
            if link is None:
                return
            proxy_id = self._proxy_id(link["parent_run_id"], source["request_id"])
            proxy = connection.execute("SELECT * FROM workflow_requests WHERE request_id=? AND state='open'", (proxy_id,)).fetchone()
            if proxy is None:
                return
            connection.execute(
                "UPDATE workflow_requests SET state=?, decision_json=?, decision_command_id=?, decided_at=?, updated_at=? WHERE request_id=?",
                (source["state"], source["decision_json"], source["decision_command_id"], source["decided_at"], now, proxy_id),
            )
            self._sync_boundary(connection, proxy["run_id"], now, default_state="waiting-helpers")
            source = self._request_row(connection, proxy["run_id"], proxy_id)

    def _unfinished_helpers(self, connection, run_id):
        for child in self._owned_children(connection, run_id):
            run = self._run_row(connection, child["child_task_id"])
            task = connection.execute("SELECT * FROM tasks WHERE task_id=?", (child["child_task_id"],)).fetchone()
            if child["state"] in ("active", "attention") or run["state"] in ("executing", "awaiting-host", "waiting-helpers") or not self._stop_proven(connection, task):
                return True
        return False

    def _resolve_helper_attention(
        self, connection, parent_run, request_row, prepared: list[dict], *,
        decision: str, reason: str, actor: str, command_id: str, now: str,
    ) -> None:
        """Answer one exact proxy chain and resume its requesting leaf only."""
        chain = self._attention_chain(connection, parent_run, request_row)
        child_run, source_request = chain[-1]
        child_task_id = child_run["run_id"]
        child_task = connection.execute("SELECT * FROM tasks WHERE task_id=?", (child_task_id,)).fetchone()
        input_text = reason or (
            "The Host approved the requested assistance; continue with this guidance"
            if decision == "approve" else "The Host declined the requested assistance; continue with what you have"
        )
        decision_record = canonical_json({"decision": decision, "reason": reason, "actor": actor,
                                          "parentRunId": parent_run["run_id"], "targetRunId": child_task_id})
        for _, boundary in chain:
            connection.execute(
                "UPDATE workflow_requests SET state=?, decision_json=?, decision_command_id=?, decided_at=?, updated_at=?"
                " WHERE request_id=? AND state='open'",
                ("approved" if decision == "approve" else "declined", decision_record, command_id, now, now, boundary["request_id"]),
            )
        continuation_id = str(uuid.uuid4())
        connection.execute(
            "INSERT INTO workflow_continuations(continuation_id,run_id,command_id,authorized_by,request_id,"
            "expected_revision,input_text,input_bytes,reason,helper_policy,helper_outcomes_json,state,created_at)"
            " VALUES(?,?,?,'auto',?,?,?,?,?,'keep','[]','recorded',?)",
            (continuation_id, child_task_id, command_id, source_request["request_id"], child_run["revision"],
             input_text, len(input_text.encode()), reason or None, now),
        )
        extra = self._create_helpers(connection, child_run, child_task, source_request, prepared, command_id, now,
                                     source_host_id=parent_run["host_id"]) if decision == "approve" and prepared else []
        pending = self._open_boundary(connection, child_run)
        if extra or pending is not None or self._unfinished_helpers(connection, child_task_id):
            self._sync_boundary(connection, child_task_id, now, default_state="waiting-helpers")
        else:
            self._requeue(connection, child_task, child_run, reason=input_text, actor=actor, now=now,
                          continuation_id=continuation_id)
        # Existing automatic authority on intermediate parents is untouched. Each
        # still waits for its own dependency before it can consume that authority.
        for owner, _ in reversed(chain):
            if owner["run_id"] != child_task_id:
                self._sync_boundary(connection, owner["run_id"], now, default_state="waiting-helpers")
            current = self._run_row(connection, owner["run_id"])
            connection.execute(
                "UPDATE workflow_children SET state=?, updated_at=?, revision=revision+1 WHERE child_task_id=?",
                ("attention" if self._open_boundary(connection, current) is not None else "active", now, owner["run_id"]),
            )
        self.board._append_event(
            connection, "workflow.helper_resumed", task_id=child_task_id, revision=child_run["revision"] + 1,
            payload={"parentRunId": parent_run["run_id"], "requestId": request_row["request_id"],
                     "sourceRequestId": source_request["request_id"], "decision": decision,
                     "dependencyHelpers": [child["taskId"] for child in extra]},
        )

    def _create_helpers(
        self, connection, run_row, parent_task, request_row, prepared: list[dict], command_id: str, now: str,
        *, source_host_id: str,
    ) -> list[dict]:
        task_spec = json.loads(parent_task["spec_json"])
        children: list[dict] = []
        for item in prepared:
            helper_task_id = str(uuid.uuid4())
            manifest = item["manifest"]
            access = item["executionWorkspace"].get("access", "write")
            transfer_from = None
            if access == "write" and manifest.get("checkoutId") == json.loads(run_row["workspace_manifest_json"] or "{}").get("checkoutId"):
                # Sequential reuse of the parent's checkout requires stopped-parent proof
                # and an explicit Host transfer.
                if not self._stopped_parent(connection, run_row["run_id"]):
                    raise BoardError(
                        "SHUTDOWN_UNCONFIRMED",
                        "A helper cannot take over the parent's checkout while the parent execution may still run; "
                        "the Host must transfer it only after stopped-parent evidence exists",
                        parentRunId=run_row["run_id"],
                    )
                transfer_from = run_row["run_id"]
            child_task = self._admit_helper_task(
                connection,
                helper_task_id=helper_task_id,
                item=item,
                parent_task=parent_task,
                task_spec=task_spec,
                run_row=run_row,
                manifest=manifest,
                now=now,
                source_host_id=source_host_id,
            )
            self._reserve(
                connection,
                manifest=manifest,
                holder_task_id=helper_task_id,
                holder_kind="helper",
                parent_run_id=run_row["run_id"],
                access=access,
                now=now,
                transfer_from=transfer_from,
            )
            artifact_id = self._pin_artifact(
                connection,
                run_id=helper_task_id,
                kind="input",
                manifest=manifest,
                attempt_id=None,
                turn_id=None,
                source_task_id=None,
                now=now,
            )
            child_run = self._run_optional(connection, helper_task_id)
            connection.execute(
                "INSERT INTO workflow_children(child_task_id, parent_run_id, request_id, decision_command_id, role,"
                " integrator, state, auto_continue, workspace_intent_json, workspace_manifest_json,"
                " workspace_manifest_sha256, created_at, updated_at) VALUES(?,?,?,?,?,?,'active',0,?,?,?,?,?)",
                (
                    helper_task_id,
                    run_row["run_id"],
                    request_row["request_id"],
                    command_id,
                    item["role"],
                    1 if item["integrator"] else 0,
                    canonical_json(item["executionWorkspace"]),
                    canonical_json(manifest),
                    manifest.get("manifestSha256"),
                    now,
                    now,
                ),
            )
            if item["executionConfiguration"] is None:
                self._start_routing(connection, helper_task_id, now)
            else:
                connection.execute("UPDATE workflow_runs SET execution_configuration_json=?,execution_configuration_revision=1,validated_configuration_revision=1 WHERE run_id=?",
                                   (canonical_json(item["executionConfiguration"]), helper_task_id))
                self.board._append_event(connection, "workflow.configuration_selected", task_id=helper_task_id,
                                         payload={"source": "original-explicit", "configuration": item["executionConfiguration"],
                                                  "configurationRevision": 1,
                                                  "routingPreferences": item["spec"].get("routingPreferences", [])})
            children.append(
                {
                    "taskId": helper_task_id,
                    "requestId": item["requestId"],
                    "integrator": item["integrator"],
                    "workspaceId": manifest.get("workspaceId"),
                    "runId": child_run["run_id"] if child_run is not None else None,
                    "artifact": artifact_id,
                }
            )
        return children

    def _admit_helper_task(
        self, connection, *, helper_task_id, item, parent_task, task_spec, run_row, manifest, now, source_host_id
    ) -> None:
        spec = item["spec"]
        fingerprint = schemas.spec_fingerprint(spec)
        effective_cwd = manifest.get("path") or spec["cwd"]
        blocker = self.board._admission_blocker(connection, {**spec, "cwd": effective_cwd})
        request_id = item["requestId"]
        existing = connection.execute("SELECT * FROM tasks WHERE request_id=?", (request_id,)).fetchone()
        if existing is not None:
            if existing["input_fingerprint"] != fingerprint:
                raise BoardError(
                    "CONFLICT",
                    "A helper requestId already belongs to a different task input",
                    requestId=request_id,
                    existingTaskId=existing["task_id"],
                )
            return
        connection.execute(
            "INSERT INTO tasks(task_id, request_id, owner, spec_json, spec_canonical_json, input_fingerprint,"
            " fingerprint_version, adapter, required_capabilities, cwd, exclusive_resources, timeout_seconds,"
            " state, queue_reason, revision, created_at, updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,1,?,?)",
            (
                helper_task_id,
                request_id,
                f"helper:{run_row['run_id']}",
                canonical_json(spec),
                canonical_json(spec),
                fingerprint,
                schemas.FINGERPRINT_VERSION_CURRENT,
                spec.get("adapter") or "unresolved",
                canonical_json(spec.get("requiredCapabilities", [])),
                effective_cwd,
                canonical_json(spec.get("exclusiveResources", [])),
                spec["timeoutSeconds"],
                "queued",
                blocker or "awaiting-worker",
                now,
                now,
            ),
        )
        # A helper run uses the ordinary turn machinery: its goal is the helper spec,
        # its Host is the parent's Host and it can never be reached by a Host control
        # capability of its own (only the parent run is addressed by workflow ops).
        connection.execute(
            "INSERT INTO workflow_runs(run_id, host_id, owner_generation, control_verifier, goal_json,"
            " goal_fingerprint, request_fingerprint, execution_workspace_json, workspace_manifest_json, workspace_id,"
            " workspace_manifest_sha256, state, revision, created_at, updated_at)"
            " VALUES(?,?,1,?,?,?,?,?,?,?,?,'executing',1,?,?)",
            (
                helper_task_id,
                run_row["host_id"],
                self.db.control_token_verifier(self.db.control_token(helper_task_id, 1)),
                canonical_json(spec),
                fingerprint,
                schemas.workflow_request_fingerprint(spec, item["executionWorkspace"], run_row["host_id"]),
                canonical_json(item["executionWorkspace"]),
                canonical_json(manifest),
                manifest.get("workspaceId"),
                manifest.get("manifestSha256"),
                now,
                now,
            ),
        )
        self.board._append_event(
            connection,
            "workflow.helper_admitted",
            task_id=helper_task_id,
            revision=1,
            payload={"parentRunId": run_row["run_id"], "requestId": request_id, "integrator": item["integrator"],
                     "sourceHostId": source_host_id},
        )

    # -- continue ------------------------------------------------------------
    def _continuation_target(self, connection, owner, target_id: str):
        if target_id != owner["run_id"] and target_id not in {
                child["child_task_id"] for child in self._owned_children(connection, owner["run_id"])}:
            raise BoardError("UNAUTHORIZED", "targetRunId must be an authorized descendant of this owning Goal")
        return self._run_row(connection, target_id)

    @staticmethod
    def _configuration_matches_goal(run, configuration: dict | None) -> None:
        if configuration is None:
            return
        constraints = schemas.configuration_constraints(json.loads(run["goal_json"]))
        mismatches = [key for key, value in constraints.items() if configuration.get(key) != value]
        if mismatches:
            raise BoardError("CONFIGURATION_CONFLICT", "configuration must preserve the original Goal's hard constraints; submit a new Goal to change them",
                             fields=mismatches)

    def continue_run(self, params: dict, *, console_authority: dict | None = None) -> dict:
        schemas.reject_unknown(
            params,
            {
                "runId",
                "targetRunId",
                "commandId",
                "expectedRevision",
                "input",
                "helperPolicy",
                "reason",
                "configuration",
                "reroute",
                *schemas.CONTROL_FIELDS,
                schemas.CONSOLE_AUTHORITY_FIELD,
            },
            "workflow.continue",
        )
        schemas.reject_untrusted_override(params)
        run_id = schemas.required_string(params, "runId", max_length=128)
        owner_run_id = run_id
        target_id = schemas.optional_string(params, "targetRunId", max_length=128) or run_id
        command_id = schemas.required_string(params, "commandId", max_length=128)
        expected = schemas.require_expected_revision(params)
        input_text, input_bytes = _bounded_input(params)
        policy = schemas.optional_string(params, "helperPolicy")
        if policy is not None and policy not in ("cancel", "keep"):
            raise BoardError("INVALID_ARGUMENT", "helperPolicy must be 'cancel' or 'keep'")
        reason = schemas.optional_string(params, "reason", max_length=schemas.MAX_WORKFLOW_REASON_BYTES) or ""
        reroute = schemas.optional_bool(params, "reroute", False)
        supplied_configuration = schemas.normalize_configuration(params["configuration"]) if "configuration" in params else None
        if supplied_configuration is not None and reroute:
            raise BoardError("INVALID_ARGUMENT", "configuration and reroute are mutually exclusive")
        if supplied_configuration is not None and not reason:
            raise BoardError("INVALID_ARGUMENT", "a Host configuration override requires an explicit reason")
        request_key = {
            "runId": run_id,
            "targetRunId": target_id,
            "expectedRevision": expected,
            "inputSha256": sha256_text(input_text),
            "helperPolicy": policy,
            "configuration": supplied_configuration,
            "reason": reason,
            "reroute": reroute,
        }
        # Authenticate/replay before native catalog work. A concurrent takeover is
        # checked again after validation, before any durable mutation.
        with self.db.read() as connection:
            run = self._run_row(connection, run_id)
            self._authorize(connection, run, params, console_authority=console_authority, action="A continuation")
            receipt = self.board._receipt(connection, command_id, "workflow.continue", request_key)
            if receipt is not None:
                return {**receipt, "duplicate": True}
            self._expect_revision(run, expected)
            target_snapshot = self._continuation_target(connection, run, target_id)
            self._configuration_matches_goal(target_snapshot, supplied_configuration)
        configuration = self._validated_configuration(supplied_configuration) if supplied_configuration is not None else None
        now = self.now()
        with self.db.write() as connection:
            owner = self._run_row(connection, owner_run_id)
            actor = self._authorize(connection, owner, params, console_authority=console_authority, action="A continuation")
            receipt = self.board._receipt(connection, command_id, "workflow.continue", request_key)
            if receipt is not None:
                return {**receipt, "duplicate": True}
            self._expect_revision(owner, expected)
            run_row = self._continuation_target(connection, owner, target_id)
            self._expect_revision(run_row, target_snapshot["revision"])
            self._configuration_matches_goal(run_row, configuration)
            run_id = run_row["run_id"]
            task = connection.execute("SELECT * FROM tasks WHERE task_id=?", (run_id,)).fetchone()
            self._assert_lineage_open(connection, run_id)
            self._require_routing_stopped(connection, run_id)
            routing = self._routing_view(connection, run_row)
            if configuration is None and not reroute and (
                    self._configuration(run_row) is None or routing["status"] in ("needs-host", "fenced")):
                raise BoardError("CONFIGURATION_REQUIRED", "Continue with a complete configuration, or reroute after fixing the selector/catalog")
            live_helpers = sum(
                child["state"] in ("active", "attention")
                or not self._stop_proven(connection, connection.execute("SELECT * FROM tasks WHERE task_id=?", (child["child_task_id"],)).fetchone())
                for child in self._owned_children(connection, run_id)
            )
            if live_helpers and policy is None:
                raise BoardError(
                    "INVALID_ARGUMENT",
                    "helperPolicy is required while authorized helpers are still live: choose 'cancel' or 'keep' "
                    "explicitly so a continuation never silently keeps or kills running work",
                    liveHelpers=live_helpers,
                )
            if policy is None:
                policy = "keep"
            helper_outcomes = self._helper_outcomes(connection, run_id)
            if policy == "cancel":
                self._cancel_children(connection, run_id, reason or "manual continuation cancelled live helpers", now)
            # New manual input supersedes every older unconsumed intent, including
            # an earlier manual input that has not acquired an execution attempt.
            connection.execute(
                "UPDATE workflow_continuations SET state='invalidated' WHERE run_id=?"
                " AND state IN ('recorded','queued')",
                (run_id,),
            )
            # An open request the Host bypassed is recorded as superseded, not left dangling.
            open_requests = connection.execute("SELECT request_id FROM workflow_requests WHERE run_id=? AND state='open'", (run_id,)).fetchall()
            connection.execute(
                "UPDATE workflow_requests SET state='superseded', updated_at=? WHERE run_id=? AND state='open'",
                (now, run_id),
            )
            for request in open_requests:
                self._close_proxy_ancestors(connection, self._request_row(connection, run_id, request["request_id"]), now)
            continuation_id = str(uuid.uuid4())
            connection.execute(
                "INSERT INTO workflow_continuations(continuation_id, run_id, command_id, authorized_by,"
                " expected_revision, input_text, input_bytes, reason, helper_policy, helper_outcomes_json, state,"
                " created_at) VALUES(?,?,?,'manual',?,?,?,?,?,?, 'queued', ?)",
                (
                    continuation_id,
                    run_id,
                    command_id,
                    run_row["revision"],
                    input_text,
                    input_bytes,
                    reason or None,
                    policy,
                    canonical_json(helper_outcomes),
                    now,
                ),
            )
            self._requeue(
                connection,
                task,
                run_row,
                reason=reason or "Host continuation",
                actor=actor,
                now=now,
                continuation_id=continuation_id,
            )
            if configuration is not None:
                connection.execute("UPDATE workflow_runs SET execution_configuration_json=?,current_routing_id=NULL,"
                                   "execution_configuration_revision=execution_configuration_revision+1,"
                                   "validated_configuration_revision=execution_configuration_revision+1 WHERE run_id=?",
                                   (canonical_json(configuration), run_id))
                connection.execute("UPDATE tasks SET adapter=? WHERE task_id=?", (configuration["adapter"], run_id))
                updated_run = self._run_row(connection, run_id)
                self.board._append_event(
                    connection, "workflow.configuration_overridden", task_id=run_id,
                    payload={"source": "host-override", "actor": actor, "reason": reason,
                             "configuration": configuration,
                             "configurationRevision": updated_run["execution_configuration_revision"],
                             "routingPreferences": json.loads(updated_run["goal_json"]).get("routingPreferences", [])},
                )
            elif reroute:
                self._start_routing(connection, run_id, now)
            if run_id != owner_run_id:
                target = self._run_row(connection, run_id)
                connection.execute("UPDATE workflow_children SET state=?,updated_at=?,revision=revision+1 WHERE child_task_id=?",
                                   ("attention" if self._open_boundary(connection, target) is not None else "active", now, run_id))
            self.board._append_event(
                connection,
                "workflow.continued",
                task_id=run_id,
                revision=run_row["revision"] + 1,
                payload={
                    "continuationId": continuation_id,
                    "authorizedBy": "manual",
                    "helperPolicy": policy,
                    "actor": actor,
                    "controllingRunId": owner_run_id,
                },
            )
            run_row = self._run_row(connection, owner_run_id)
            view = self.compact(connection, run_row)
            response = {
                **view,
                "continuationId": continuation_id,
                "inputSha256": sha256_text(input_text),
                "helperPolicy": policy,
                "duplicate": False,
                "targetRunId": run_id,
            }
            self.board._store_receipt(
                connection, command_id, "workflow.continue", request_key, response, task_id=owner_run_id
            )
            head = self.board._head_of(connection)
        self.board._notify(head)
        return response

    def _helper_outcomes(self, connection, run_id: str) -> list[dict]:
        """The frozen handoff of every owned helper: identity, state and immutable refs.

        The parent's continuation must be able to name exactly what each helper
        produced without filesystem searching, so the child's selected attempt, its
        input manifest identity and its sealed output reference are frozen here.
        """
        rows = connection.execute(
            "SELECT c.*, t.state AS task_state, t.active_attempt_id, t.selected_attempt_id FROM workflow_children c"
            " JOIN tasks t ON t.task_id=c.child_task_id WHERE c.parent_run_id=? ORDER BY c.created_at",
            (run_id,),
        ).fetchall()
        outcomes: list[dict] = []
        for row in rows:
            attempt_id = row["active_attempt_id"] or row["selected_attempt_id"]
            turn = connection.execute(
                "SELECT * FROM workflow_turns WHERE run_id=? AND attempt_id=? ORDER BY turn_index DESC LIMIT 1",
                (row["child_task_id"], attempt_id),
            ).fetchone()
            outcome = json.loads(turn["outcome_json"]) if turn is not None and turn["outcome_json"] else None
            artifact = connection.execute(
                "SELECT * FROM workflow_artifacts WHERE run_id=? AND attempt_id=? AND kind='output'"
                " ORDER BY rowid DESC LIMIT 1",
                (row["child_task_id"], attempt_id),
            ).fetchone()
            child_run = self._run_optional(connection, row["child_task_id"])
            entry = {
                "taskId": row["child_task_id"],
                "role": row["role"],
                "state": row["state"],
                "taskState": row["task_state"],
                "integrator": bool(row["integrator"]),
                "disposition": turn["disposition"] if turn is not None else None,
                "summary": _head(outcome.get("summary") if outcome else None, 2000),
                "remaining": [str(item)[:500] for item in (outcome or {}).get("remaining", [])[:8]]
                if isinstance((outcome or {}).get("remaining"), list)
                else [],
                "attemptId": attempt_id,
                "generation": turn["generation"] if turn is not None else None,
                "workspaceManifestSha256": row["workspace_manifest_sha256"],
            }
            manifest = json.loads(turn["input_json"]).get("executionWorkspace") if turn is not None else None
            if manifest is None and child_run is not None and child_run["workspace_manifest_json"]:
                manifest = json.loads(child_run["workspace_manifest_json"])
            if manifest:
                entry["workspaceManifestSha256"] = manifest.get("manifestSha256")
                entry["workspace"] = {
                    "workspaceId": manifest.get("workspaceId"),
                    "path": manifest.get("path"),
                    "checkoutId": manifest.get("checkoutId"),
                    "manifestSha256": manifest.get("manifestSha256"),
                }
            if artifact is not None:
                seal = json.loads(artifact["manifest_json"])
                changed = seal.get("changedPaths")
                entry["artifact"] = {
                    "artifactId": artifact["artifact_id"],
                    "attemptId": artifact["attempt_id"],
                    "turnId": artifact["turn_id"],
                    "inputCommit": seal.get("inputCommit"),
                    "outputCommit": seal.get("commit"),
                    "snapshotSha256": artifact["manifest_sha256"],
                    "diffPath": seal.get("diffPath"),
                    "diffSha256": seal.get("diffSha256"),
                    "changedPaths": [str(item)[:500] for item in changed[:32]] if isinstance(changed, list) else [],
                }
            outcomes.append(entry)
        return outcomes

    def _requeue(
        self,
        connection,
        task,
        run_row,
        *,
        reason: str,
        actor: str,
        now: str,
        continuation_id: str | None = None,
    ) -> None:
        self._assert_lineage_open(connection, run_row["run_id"])
        spec = json.loads(task["spec_json"])
        attempt = self.board._selected_attempt(connection, task)
        if task["state"] in ("running", "cancelling"):
            raise BoardError(
                "CONFLICT",
                "This run still has an active turn; cancel or wait for a confirmed stop before continuing",
                state=task["state"],
                attemptId=attempt["attempt_id"] if attempt else None,
            )
        if attempt is not None and (
            attempt["execution_state"] != "finished" or not attempt["shutdown_confirmed"]
        ):
            raise BoardError(
                "SHUTDOWN_UNCONFIRMED",
                "The current turn's shutdown is unconfirmed; a continuation never replaces an execution that may "
                "still be running",
                attemptId=attempt["attempt_id"],
            )
        continuation = (connection.execute("SELECT * FROM workflow_continuations WHERE continuation_id=?", (continuation_id,)).fetchone()
                        if continuation_id is not None else None)
        if continuation is not None and continuation["authorized_by"] == "manual":
            self._reclaim_manual_workspace(connection, task, run_row, now)
        blocker = self.board._admission_blocker(connection, spec, exclude_task=task["task_id"])
        if task["accepted_at"]:
            # The review belonged to the attempt that was reviewed; a continuation
            # archives it exactly like an explicit retry, so a later turn can be
            # accepted as a new final artifact.
            self.board._append_event(
                connection,
                "task.review_archived",
                task_id=task["task_id"],
                attempt_id=attempt["attempt_id"] if attempt else None,
                revision=task["revision"] + 1,
                payload={
                    "acceptedAt": task["accepted_at"],
                    "verdict": task["acceptance_verdict"],
                    "note": task["acceptance_note"],
                },
            )
            connection.execute(
                "UPDATE tasks SET accepted_at=NULL, acceptance_note=NULL, acceptance_verdict=NULL WHERE task_id=?",
                (task["task_id"],),
            )
        if task["state"] == "completed":
            # A rejected final review reopens the governed logical task for another
            # turn. This is not an execution retry of completed work
            # (which stays refused); only a governed run at a Host boundary may do it.
            if run_row["state"] not in ("awaiting-host", "delivered"):
                raise BoardError(
                    "CONFLICT",
                    "A completed governed task can only be reopened from a Host boundary",
                    state=task["state"],
                    runState=run_row["state"],
                )
            cursor = connection.execute(
                "UPDATE tasks SET state='queued', updated_at=?, revision=revision+1 WHERE task_id=? AND revision=?",
                (now, task["task_id"], task["revision"]),
            )
            if cursor.rowcount != 1:
                raise BoardError("REVISION_CONFLICT", "The task changed concurrently; re-read it and retry")
        else:
            self.board._transition_task(connection, task, "queued")
        connection.execute(
            "UPDATE tasks SET queue_reason=?, active_attempt_id=NULL, updated_at=? WHERE task_id=?",
            (blocker or "awaiting-worker", now, task["task_id"]),
        )
        updated = connection.execute(
            "UPDATE workflow_runs SET state='executing', active_request_id=NULL,"
            " continuation_count=continuation_count+1, updated_at=?, revision=revision+1"
            " WHERE run_id=? AND revision=?",
            (now, run_row["run_id"], run_row["revision"]),
        )
        if updated.rowcount != 1:
            raise BoardError("REVISION_CONFLICT", "The governed run changed concurrently; re-read it and retry")
        if continuation_id is not None:
            connection.execute(
                "UPDATE workflow_continuations SET state='queued' WHERE continuation_id=?", (continuation_id,)
            )

    # -- takeover ------------------------------------------------------------
    def takeover(self, params: dict, *, console_authority: dict | None = None) -> dict:
        schemas.reject_unknown(
            params,
            {
                "runId",
                "commandId",
                "expectedOwnerGeneration",
                "newHostId",
                "expectedRevision",
                *schemas.CONTROL_FIELDS,
                schemas.CONSOLE_AUTHORITY_FIELD,
            },
            "workflow.takeover",
        )
        schemas.reject_untrusted_override(params)
        run_id = schemas.required_string(params, "runId", max_length=128)
        command_id = schemas.required_string(params, "commandId", max_length=128)
        new_host = schemas.required_string(params, "newHostId", max_length=256)
        expected_generation = params.get("expectedOwnerGeneration")
        if isinstance(expected_generation, bool) or not isinstance(expected_generation, int) or expected_generation < 1:
            raise BoardError("INVALID_ARGUMENT", "expectedOwnerGeneration must be a positive integer")
        expected = params.get("expectedRevision")
        if expected is not None and (isinstance(expected, bool) or not isinstance(expected, int) or expected < 0):
            raise BoardError("INVALID_ARGUMENT", "expectedRevision must be a nonnegative integer")
        request_key = {"runId": run_id, "newHostId": new_host, "expectedOwnerGeneration": expected_generation}
        now = self.now()
        with self.db.write() as connection:
            run_row = self._run_row(connection, run_id)
            receipt = self.board._receipt(connection, command_id, "workflow.takeover", request_key)
            if receipt is not None:
                # A lost reply must be recoverable by the caller that authorized the
                # original takeover: authenticate that receipt's original subject,
                # never the current owner, and never issue a newer generation.
                self._authorize_takeover_replay(
                    connection, run_row, params, console_authority, expected_generation, receipt
                )
                return {**receipt, "duplicate": True}
            actor = self._authorize(connection, run_row, params, console_authority=console_authority, action="A takeover")
            self._expect_revision(run_row, expected)
            if run_row["owner_generation"] != expected_generation:
                raise BoardError(
                    "STALE_GENERATION",
                    "The owner generation changed; re-read the run before taking over",
                    ownerGeneration=expected_generation,
                    currentGeneration=run_row["owner_generation"],
                )
            generation = run_row["owner_generation"] + 1
            connection.execute(
                "UPDATE workflow_runs SET host_id=?, owner_generation=?, control_verifier=?, updated_at=?,"
                " revision=revision+1 WHERE run_id=? AND revision=?",
                (
                    new_host,
                    generation,
                    self.db.control_token_verifier(self.db.control_token(run_id, generation)),
                    now,
                    run_id,
                    run_row["revision"],
                ),
            )
            # Routing calls are owned processes too. Fence every still-pending
            # selection before changing authority can admit a late recommendation.
            for owned_id in [run_id, *(child["child_task_id"] for child in self._owned_children(connection, run_id))]:
                if self._cancel_routes(connection, owned_id, "Host takeover fenced model selection", now):
                    owned = self._run_row(connection, owned_id)
                    if owned["state"] == "executing":
                        self._routing_attention(connection, owned, now, "Host takeover interrupted model selection; wait for its confirmed stop, then choose a configuration or reroute")
            self.board._append_event(
                connection,
                "workflow.takeover",
                task_id=run_id,
                revision=run_row["revision"] + 1,
                payload={"actor": actor, "newHostId": new_host, "ownerGeneration": generation,
                         "previousHostId": run_row["host_id"], "previousOwnerGeneration": run_row["owner_generation"]},
            )
            run_row = self._run_row(connection, run_id)
            response = {
                **self.compact(connection, run_row),
                "duplicate": False,
                "previousOwnerGeneration": expected_generation,
                "control": {
                    "hostId": new_host,
                    "ownerGeneration": generation,
                    "controlToken": self.db.control_token(run_id, generation),
                },
            }
            self.board._store_receipt(
                connection, command_id, "workflow.takeover", request_key, response, task_id=run_id
            )
            head = self.board._head_of(connection)
        self.board._notify(head)
        return response

    def _authorize_takeover_replay(
        self,
        connection,
        run_row,
        params: dict,
        console_authority: dict | None,
        expected_generation: int,
        receipt: dict,
    ) -> None:
        """Authenticate a replayed takeover by its original capability, not by current ownership."""
        import hmac

        issued = receipt.get("ownerGeneration")
        current = run_row["owner_generation"]
        if issued != expected_generation + 1 or current != issued:
            raise BoardError(
                "STALE_GENERATION",
                "This takeover receipt belongs to an older generation; a replay never grants a newer one",
                issuedGeneration=issued,
                currentGeneration=current,
            )
        issued_control = receipt.get("control") if isinstance(receipt.get("control"), dict) else {}
        if run_row["host_id"] != issued_control.get("hostId"):
            raise BoardError("STALE_GENERATION", "The current Host no longer matches this takeover receipt")
        if console_authority is not None:
            return
        control = schemas.normalize_control(params)
        if control is None or control["ownerGeneration"] != expected_generation:
            raise BoardError(
                "UNAUTHORIZED",
                "A takeover replay must present the original owner generation's control capability",
                expectedOwnerGeneration=expected_generation,
            )
        original = self.db.control_token(run_row["run_id"], expected_generation)
        if not hmac.compare_digest(
            self.db.control_token_verifier(original), self.db.control_token_verifier(control["controlToken"])
        ):
            raise BoardError("UNAUTHORIZED", "Invalid control capability for the original takeover")

    # -- cancel --------------------------------------------------------------
    def cancel(self, params: dict, *, console_authority: dict | None = None) -> dict:
        schemas.reject_unknown(
            params,
            {"runId", "commandId", "reason", *schemas.CONTROL_FIELDS, schemas.CONSOLE_AUTHORITY_FIELD},
            "workflow.cancel",
        )
        schemas.reject_untrusted_override(params)
        run_id = schemas.required_string(params, "runId", max_length=128)
        command_id = schemas.optional_string(params, "commandId", max_length=128)
        reason = schemas.optional_string(params, "reason", max_length=schemas.MAX_WORKFLOW_REASON_BYTES) or "Host cancelled"
        request_key = {"runId": run_id, "reason": reason}
        now = self.now()
        with self.db.write() as connection:
            run_row = self._run_row(connection, run_id)
            task = connection.execute("SELECT * FROM tasks WHERE task_id=?", (run_id,)).fetchone()
            actor = self._authorize(connection, run_row, params, console_authority=console_authority, action="Cancellation")
            if command_id:
                receipt = self.board._receipt(connection, command_id, "workflow.cancel", request_key)
                if receipt is not None:
                    return {**receipt, "duplicate": True}
            if run_row["state"] == "accepted":
                raise BoardError("CONFLICT", "This run is already acknowledged; cancellation cannot undo acceptance")
            # Fence the root before descendants settle: a transferred checkout
            # must not be handed back to a goal cancelled in this transaction.
            self._cancel_owned_run(connection, task, now, explicit=True)
            self._cancel_children(connection, run_id, reason, now)
            self.board._append_event(
                connection, "workflow.cancelled", task_id=run_id, revision=run_row["revision"] + 1,
                payload={"reason": reason, "actor": actor, "honestShutdown": True},
            )
            task = connection.execute("SELECT * FROM tasks WHERE task_id=?", (run_id,)).fetchone()
            child = connection.execute("SELECT * FROM workflow_children WHERE child_task_id=?", (run_id,)).fetchone()
            if child is not None and self._stop_proven(connection, task):
                self.child_settled(connection, task=task, attempt=self.board._selected_attempt(connection, task),
                                   payload=None, task_state=task["state"], now=now)
            elif self._stop_proven(connection, task):
                self._release_reservations(connection, run_id, now)
            run_row = self._run_row(connection, run_id)
            response = {
                **self.compact(connection, run_row, task),
                "cancelled": True,
                "duplicate": False,
                "note": (
                    "Cancellation is durable. An active execution becomes cancelling and its worker stops the owned "
                    "process group; this response never claims that a surviving process is already stopped."
                ),
            }
            if command_id:
                self.board._store_receipt(
                    connection, command_id, "workflow.cancel", request_key, response, task_id=run_id
                )
            head = self.board._head_of(connection)
        self.board._notify(head)
        return response

    def _cancel_children(self, connection, run_id: str, reason: str, now: str) -> list[str]:
        """Fence the whole owned graph, including work behind settled helpers."""
        cancelled: list[str] = []
        rows = self._owned_children(connection, run_id)
        for child in rows:
            task = connection.execute("SELECT * FROM tasks WHERE task_id=?", (child["child_task_id"],)).fetchone()
            if task is None:
                continue
            if self._cancel_owned_run(connection, task, now):
                self.board._append_event(
                    connection, "workflow.helper_cancelled", task_id=task["task_id"], revision=task["revision"] + 1,
                    payload={"parentRunId": child["parent_run_id"], "cancelledByRunId": run_id, "reason": reason},
                )
                cancelled.append(task["task_id"])
        # Every descendant is fenced before ownership can move upward. Settlement
        # walks through cancelled intermediaries but never reactivates one.
        for child in reversed(rows):
            task = connection.execute("SELECT * FROM tasks WHERE task_id=?", (child["child_task_id"],)).fetchone()
            self._settle_helper_reservation(connection, child, task, None, now)
        return cancelled

    def _owned_children(self, connection, run_id: str) -> list:
        return connection.execute(
            "WITH RECURSIVE owned(run_id) AS (SELECT ? UNION SELECT c.child_task_id FROM workflow_children c"
            " JOIN owned o ON c.parent_run_id=o.run_id) SELECT c.* FROM workflow_children c"
            " JOIN owned o ON c.child_task_id=o.run_id WHERE c.child_task_id != ? ORDER BY c.created_at, c.child_task_id",
            (run_id, run_id),
        ).fetchall()

    def _routing_tasks(self, connection, run_id: str, *, descendants: bool = False) -> list:
        ids = [run_id]
        if descendants:
            ids.extend(child["child_task_id"] for child in self._owned_children(connection, run_id))
        placeholders = ",".join("?" for _ in ids)
        return connection.execute(
            "SELECT t.* FROM tasks t JOIN decision_requests d ON d.task_id=t.task_id"
            f" JOIN workflow_routes r ON r.decision_id=d.decision_id WHERE r.run_id IN ({placeholders})",
            ids,
        ).fetchall()

    def _require_routing_stopped(self, connection, run_id: str) -> None:
        for task in self._routing_tasks(connection, run_id):
            if not self._stop_proven(connection, task):
                raise BoardError("SHUTDOWN_UNCONFIRMED", "The owned routing attempt has no confirmed stop; it cannot be replaced",
                                 routingTaskId=task["task_id"])
        pending = connection.execute(
            "SELECT r.decision_id FROM workflow_routes r JOIN evaluation_decisions d ON d.decision_id=r.decision_id"
            " WHERE r.run_id=? AND r.state='pending' AND d.status IN ('queued','running') LIMIT 1", (run_id,),
        ).fetchone()
        if pending is not None:
            raise BoardError("CONFLICT", "Model selection is still pending; wait or cancel its owning Goal before continuing")

    def _cancel_routes(self, connection, run_id: str, reason: str, now: str) -> bool:
        fenced = False
        for link in connection.execute("SELECT * FROM workflow_routes WHERE run_id=?", (run_id,)).fetchall():
            decision = self.board.decisions._row(connection, link["decision_id"])
            task = (connection.execute("SELECT * FROM tasks WHERE task_id=?", (decision["decision_task_id"],)).fetchone()
                    if decision["decision_task_id"] else None)
            unconfirmed = task is not None and not self._stop_proven(connection, task)
            if link["state"] != "pending" and not unconfirmed:
                continue
            fenced = True
            connection.execute("UPDATE workflow_routes SET state='fenced',reason=?,updated_at=? WHERE decision_id=?",
                               (reason, now, link["decision_id"]))
            if task is not None:
                if task["state"] not in ("completed", "failed", "cancelled", "cancelling"):
                    self.board._transition_task(connection, task, "cancelling" if unconfirmed else "cancelled")
                connection.execute("UPDATE tasks SET queue_reason=NULL WHERE task_id=?", (task["task_id"],))
                if unconfirmed:
                    connection.execute(
                        "UPDATE attempts SET cancel_requested_at=COALESCE(cancel_requested_at,?),updated_at=?,revision=revision+1"
                        " WHERE task_id=? AND (execution_state!='finished' OR shutdown_confirmed!=1)",
                        (now, now, task["task_id"]),
                    )
                self.board.decisions.cancelled(connection, task=task, reason=reason, now=now)
                self.board._append_event(connection, "task.cancel_requested" if unconfirmed else "task.cancelled",
                                         task_id=task["task_id"], payload={"reason": reason, "owningRunId": run_id})
        return fenced

    def _stop_proven(self, connection, task) -> bool:
        """Only unclaimed work or durable finished+confirmed attempts prove stop."""
        if task is None:
            return False
        evidence = connection.execute(
            "SELECT COUNT(*) AS total, SUM(CASE WHEN execution_state != 'finished' OR shutdown_confirmed != 1"
            " THEN 1 ELSE 0 END) AS unconfirmed FROM attempts WHERE task_id=?", (task["task_id"],),
        ).fetchone()
        if evidence["unconfirmed"]:
            return False
        for attempt_id in {task["active_attempt_id"], task["selected_attempt_id"]}:
            if attempt_id and connection.execute(
                "SELECT 1 FROM attempts WHERE attempt_id=? AND task_id=?", (attempt_id, task["task_id"]),
            ).fetchone() is None:
                return False
        return bool(evidence["total"]) or task["state"] not in ("running", "cancelling", "reconciliation-needed")

    def shutdown_summary(self, connection, run_id: str) -> dict:
        """Bounded aggregate evidence over the complete owned lineage, not PIDs."""
        self._run_row(connection, run_id)
        task = connection.execute("SELECT * FROM tasks WHERE task_id=?", (run_id,)).fetchone()
        own_stop = self._stop_proven(connection, task)
        unconfirmed = [] if own_stop else [run_id]
        for child in self._owned_children(connection, run_id):
            task = connection.execute("SELECT * FROM tasks WHERE task_id=?", (child["child_task_id"],)).fetchone()
            if not self._stop_proven(connection, task):
                unconfirmed.append(child["child_task_id"])
        for task in self._routing_tasks(connection, run_id, descendants=True):
            if not self._stop_proven(connection, task):
                unconfirmed.append(task["task_id"])
        return {"selfConfirmed": own_stop, "descendantsConfirmed": len(unconfirmed) == (0 if own_stop else 1),
                "unconfirmedRunIds": unconfirmed[:MAX_CHILD_VIEW], "unconfirmedCount": len(unconfirmed),
                "truncated": len(unconfirmed) > MAX_CHILD_VIEW}

    def _terminal_ancestor(self, connection, run_id: str):
        return connection.execute(
            "WITH RECURSIVE ancestors(run_id) AS (SELECT parent_run_id FROM workflow_children WHERE child_task_id=?"
            " UNION SELECT c.parent_run_id FROM workflow_children c JOIN ancestors a ON c.child_task_id=a.run_id)"
            " SELECT r.run_id, r.state FROM workflow_runs r JOIN ancestors a ON r.run_id=a.run_id"
            " WHERE r.state IN ('cancelled','accepted') LIMIT 1", (run_id,),
        ).fetchone()

    def _assert_lineage_open(self, connection, run_id: str) -> None:
        ancestor = self._terminal_ancestor(connection, run_id)
        if ancestor is not None:
            raise BoardError("ANCESTOR_TERMINAL", "An owned helper cannot continue beneath a cancelled or accepted ancestor",
                             runId=run_id, ancestorRunId=ancestor["run_id"], ancestorState=ancestor["state"])

    def _cancel_owned_run(self, connection, task, now: str, *, explicit: bool = False) -> bool:
        run_id = task["task_id"]
        run_row = self._run_row(connection, run_id)
        cancel_goal = explicit or run_row["state"] not in ("delivered", "accepted", "failed")
        connection.execute(
            "UPDATE workflow_requests SET state='cancelled', updated_at=? WHERE run_id=? AND state='open'", (now, run_id),
        )
        connection.execute(
            "UPDATE workflow_continuations SET state='cancelled' WHERE run_id=? AND state IN ('recorded','queued')", (run_id,),
        )
        self._revoke_credentials(connection, run_id, now)
        self._cancel_routes(connection, run_id, "Owning Goal cancelled", now)
        if cancel_goal:
            connection.execute(
                "UPDATE workflow_runs SET state='cancelled', active_request_id=NULL, updated_at=?, revision=revision+1 WHERE run_id=?",
                (now, run_id),
            )
            connection.execute(
                "UPDATE workflow_children SET state='cancelled', updated_at=?, revision=revision+1"
                " WHERE child_task_id=? AND state IN ('active','attention')", (now, run_id),
            )
        elif run_row["active_request_id"]:
            connection.execute(
                "UPDATE workflow_runs SET active_request_id=NULL, updated_at=?, revision=revision+1 WHERE run_id=?", (now, run_id),
            )
        stopped = self._stop_proven(connection, task)
        if task["state"] not in ("completed", "failed", "cancelled"):
            self.board._transition_task(connection, task, "cancelled" if stopped else "cancelling")
            connection.execute("UPDATE tasks SET queue_reason=NULL, updated_at=? WHERE task_id=?", (now, run_id))
        if not stopped:
            connection.execute(
                "UPDATE attempts SET cancel_requested_at=?, updated_at=?, revision=revision+1 WHERE task_id=?"
                " AND (execution_state != 'finished' OR shutdown_confirmed != 1) AND cancel_requested_at IS NULL",
                (now, now, run_id),
            )
        return cancel_goal

    def _late_cancelled_turn(self, connection, run_row, turn_row, attempt, payload, now):
        """Keep real receipt/artifact evidence without reopening a fenced goal."""
        turn = self.turn_outcome(payload)
        disposition = None
        if attempt["shutdown_confirmed"] and turn is not None and self._validate_turn(turn, turn_row, attempt) is None:
            disposition = turn["outcome"]["disposition"]
            connection.execute(
                "UPDATE workflow_turns SET state='concluded', disposition=?, outcome_json=?, provenance_json=?,"
                " session_id=?, prompt_sha256=?, input_sha256=?, turn_result_path=?, sealed_artifacts_json=?,"
                " updated_at=? WHERE turn_id=?",
                (disposition, canonical_json(turn["outcome"]), canonical_json(turn.get("provenance")),
                 turn.get("sessionId"), turn.get("promptSha256"), turn.get("inputSha256"),
                 self.result_payload(payload).get("turnResultPath"), canonical_json(self._sealed_artifacts(payload)),
                 now, turn_row["turn_id"]),
            )
            self._pin_result_artifacts(connection, run_row, attempt, payload, turn_row["turn_id"], now)
        else:
            connection.execute("UPDATE workflow_turns SET state='failed', updated_at=? WHERE turn_id=?", (now, turn_row["turn_id"]))
        self._revoke_credentials(connection, run_row["run_id"], now)
        self.board._append_event(
            connection, "workflow.late_turn", task_id=run_row["run_id"], attempt_id=attempt["attempt_id"],
            revision=run_row["revision"], payload={"turnId": turn_row["turn_id"], "state": run_row["state"], "disposition": disposition},
        )
        return {"accepted": False, "disposition": disposition, "state": run_row["state"], "late": True}

    def attempt_released(self, connection, *, task, attempt, now: str, reason: str) -> None:
        """Settle a Worker's durable never-spawned release inside its transaction."""
        if self._run_optional(connection, task["task_id"]) is None:
            return
        if not self._stop_proven(connection, task):
            raise BoardError("SHUTDOWN_UNCONFIRMED", "A workflow release requires durable finished and confirmed stop evidence")
        self.turn_concluded(connection, task=task, attempt=attempt, payload=None, task_state=task["state"], now=now)
        self.child_settled(connection, task=task, attempt=attempt, payload=None, task_state=task["state"], now=now)
        self._release_reservations(connection, task["task_id"], now)
        self._revoke_credentials(connection, task["task_id"], now)
        self.board._append_event(
            connection, "workflow.attempt_released", task_id=task["task_id"], attempt_id=attempt["attempt_id"],
            payload={"reason": reason, "shutdownConfirmed": True},
        )

    def _revoke_credentials(self, connection, run_id: str, now: str) -> None:
        connection.execute(
            "UPDATE agent_credentials SET state='revoked', revoked_at=?, revision=revision+1"
            " WHERE run_id=? AND state='active'",
            (now, run_id),
        )

    # -- acknowledge ---------------------------------------------------------
    def acknowledge(self, params: dict, *, console_authority: dict | None = None) -> dict:
        schemas.reject_unknown(
            params,
            {
                "runId",
                "commandId",
                "artifactId",
                "integrationId",
                "note",
                "verdict",
                "evidence",
                "acknowledgedBy",
                *schemas.CONTROL_FIELDS,
                schemas.CONSOLE_AUTHORITY_FIELD,
            },
            "workflow.acknowledge",
        )
        schemas.reject_untrusted_override(params)
        run_id = schemas.required_string(params, "runId", max_length=128)
        command_id = schemas.optional_string(params, "commandId", max_length=128)
        artifact_id = schemas.optional_string(params, "artifactId", max_length=128)
        integration_id = schemas.optional_string(params, "integrationId", max_length=128)
        note, _ = schemas.bounded_text(params, "note", max_bytes=schemas.MAX_NOTE_BYTES)
        verdict = schemas.optional_string(params, "verdict") or "accepted"
        if verdict not in ("accepted", "rejected"):
            raise BoardError("INVALID_ARGUMENT", "verdict must be 'accepted' or 'rejected'")
        evidence = schemas.string_list(params, "evidence", limit=32)
        request_key = {"runId": run_id, "artifactId": artifact_id, "note": note, "verdict": verdict}
        now = self.now()
        with self.db.write() as connection:
            run_row = self._run_row(connection, run_id)
            task = connection.execute("SELECT * FROM tasks WHERE task_id=?", (run_id,)).fetchone()
            actor = self._authorize(
                connection, run_row, params, console_authority=console_authority, action="Acknowledgement"
            )
            if command_id:
                receipt = self.board._receipt(connection, command_id, "workflow.acknowledge", request_key)
                if receipt is not None:
                    return {**receipt, "duplicate": True}
            # Acceptance is bound to the current delivered goal, never to a Host
            # decision boundary, an active helper, or an older attempt's output.
            if run_row["state"] not in ("delivered", "accepted"):
                raise BoardError(
                    "NOT_READY",
                    "Only a delivered goal can be acknowledged; a Host decision boundary or a running turn is "
                    "not a final artifact",
                    runId=run_id,
                    state=run_row["state"],
                )
            active_helpers = int(
                connection.execute(
                    "SELECT COUNT(*) AS count FROM workflow_children WHERE parent_run_id=? AND state='active'",
                    (run_id,),
                ).fetchone()["count"]
            )
            if active_helpers:
                raise BoardError(
                    "CONFLICT",
                    "Owned helpers are still running; settle or cancel them before final acknowledgement",
                    runId=run_id,
                    activeHelpers=active_helpers,
                )
            attempt = self.board._selected_attempt(connection, task)
            if attempt is None or attempt["result_json"] is None:
                raise BoardError("NOT_READY", "Inspect a persisted final result before acknowledging")
            if not attempt["shutdown_confirmed"]:
                raise BoardError(
                    "SHUTDOWN_UNCONFIRMED",
                    "Execution shutdown must be confirmed before acknowledgement; a surviving process is unknown",
                    attemptId=attempt["attempt_id"],
                )
            if run_row["final_attempt_id"] != attempt["attempt_id"]:
                raise BoardError(
                    "CONFLICT",
                    "The selected attempt is not the current final attempt of this goal",
                    attemptId=attempt["attempt_id"],
                    finalAttemptId=run_row["final_attempt_id"],
                )
            final_turn = connection.execute(
                "SELECT * FROM workflow_turns WHERE attempt_id=? AND state='concluded' AND disposition='completed'",
                (attempt["attempt_id"],),
            ).fetchone()
            if final_turn is None:
                raise BoardError(
                    "NOT_READY",
                    "The current final attempt has no concluded completed turn to accept",
                    attemptId=attempt["attempt_id"],
                )
            final_artifact = connection.execute(
                "SELECT * FROM workflow_artifacts WHERE run_id=? AND kind='output' AND attempt_id=?"
                " ORDER BY created_at DESC LIMIT 1",
                (run_id, attempt["attempt_id"]),
            ).fetchone()
            if final_artifact is None:
                raise BoardError(
                    "NOT_READY",
                    "The current final attempt produced no sealed output artifact to accept",
                    attemptId=attempt["attempt_id"],
                )
            if artifact_id and artifact_id != final_artifact["artifact_id"]:
                other = connection.execute(
                    "SELECT * FROM workflow_artifacts WHERE artifact_id=? AND run_id=?", (artifact_id, run_id)
                ).fetchone()
                if other is None:
                    raise BoardError("NOT_FOUND", "Unknown pinned artifact for this run", artifactId=artifact_id)
                raise BoardError(
                    "CONFLICT",
                    "Acknowledgement must bind the current final output artifact, not another artifact of this run",
                    artifactId=artifact_id,
                    finalArtifactId=final_artifact["artifact_id"],
                    artifactKind=other["kind"],
                )
            artifact = final_artifact
            integration = (
                self._require_integration(connection, run_id, artifact["artifact_id"], integration_id)
                if verdict == "accepted" else None
            )
            if task["accepted_at"]:
                if (
                    task["acceptance_note"] != note
                    or task["acceptance_verdict"] != verdict
                    or run_row["final_artifact_id"] != artifact["artifact_id"]
                ):
                    raise BoardError(
                        "CONFLICT",
                        "This run already has a different recorded acknowledgement; a reviewed outcome is not relabelled",
                        acceptedAt=task["accepted_at"],
                        verdict=task["acceptance_verdict"],
                    )
                return {**self.compact(connection, run_row, task), "duplicate": True}
            connection.execute(
                "UPDATE tasks SET accepted_at=?, acceptance_note=?, acceptance_verdict=?, updated_at=?,"
                " revision=revision+1 WHERE task_id=?",
                (now, note, verdict, now, run_id),
            )
            if verdict == "accepted":
                connection.execute(
                    "UPDATE workflow_runs SET state='accepted', final_artifact_id=?, final_attempt_id=?, updated_at=?,"
                    " revision=revision+1 WHERE run_id=? AND revision=?",
                    (
                        artifact["artifact_id"] if artifact is not None else None,
                        attempt["attempt_id"],
                        now,
                        run_id,
                        run_row["revision"],
                    ),
                )
                self._release_reservations(connection, run_id, now)
            else:
                # A rejection is a reviewed outcome, not a terminal goal: the Host may
                # continue, which archives this review before the next acceptance.
                connection.execute(
                    "UPDATE workflow_runs SET state='awaiting-host', final_attempt_id=?, updated_at=?,"
                    " revision=revision+1 WHERE run_id=? AND revision=?",
                    (attempt["attempt_id"], now, run_id, run_row["revision"]),
                )
            self.board._append_event(
                connection,
                "workflow.acknowledged",
                task_id=run_id,
                attempt_id=attempt["attempt_id"],
                revision=run_row["revision"] + 1,
                payload={
                    "verdict": verdict,
                    "actor": actor,
                    "artifactId": artifact["artifact_id"] if artifact is not None else None,
                    "integrationId": integration["integration_id"] if integration is not None else None,
                    "evidence": evidence,
                    "noteBytes": len(note.encode()),
                },
            )
            task = connection.execute("SELECT * FROM tasks WHERE task_id=?", (run_id,)).fetchone()
            run_row = self._run_row(connection, run_id)
            view = self.compact(connection, run_row, task)
            # An accepted attempt counts as one reviewed sample, exactly like the
            # infrastructure acknowledgement path; the verdict is never inherited implicitly.
            self.board.evaluation.refresh_task_evidence(connection, task, now)
            response = {**view, "verdict": verdict, "duplicate": False}
            if integration is not None:
                response["integration"] = self._integration_view(integration)
            if command_id:
                self.board._store_receipt(
                    connection, command_id, "workflow.acknowledge", request_key, response, task_id=run_id,
                    attempt_id=attempt["attempt_id"],
                )
            head = self.board._head_of(connection)
        self.board._notify(head)
        return response

    # -- workspace lifecycle (Host-owned) ------------------------------------
    def _bump_run(self, connection, run_row, now: str):
        """Advance the governed run revision for a recorded lifecycle mutation."""
        updated = connection.execute(
            "UPDATE workflow_runs SET updated_at=?, revision=revision+1 WHERE run_id=? AND revision=?",
            (now, run_row["run_id"], run_row["revision"]),
        )
        if updated.rowcount != 1:
            raise BoardError("REVISION_CONFLICT", "The governed run changed concurrently; re-read it and retry")
        return self._run_row(connection, run_row["run_id"])

    def _find_integration(self, connection, run_id: str, artifact_id: str, integration_id: str | None):
        """The newest verified integration record (or explicit not-required) of one artifact."""
        rows = connection.execute(
            "SELECT * FROM workflow_integrations WHERE run_id=? AND artifact_id=?"
            " AND state IN ('verified','not-required') ORDER BY created_at DESC, rowid DESC",
            (run_id, artifact_id),
        ).fetchall()
        if integration_id is not None:
            rows = [row for row in rows if row["integration_id"] == integration_id]
            if not rows:
                raise BoardError("NOT_FOUND", "This artifact has no matching verified integration record",
                                 artifactId=artifact_id, integrationId=integration_id)
        if not rows:
            return None
        record = rows[0]
        artifact = connection.execute("SELECT * FROM workflow_artifacts WHERE artifact_id=?", (artifact_id,)).fetchone()
        manifest = json.loads(artifact["manifest_json"]) if artifact is not None else {}
        if record["source_commit"] and manifest.get("commit") and record["source_commit"] != manifest.get("commit"):
            raise BoardError("CONFLICT", "The integration record was verified against a different artifact",
                             integrationId=record["integration_id"], artifactCommit=manifest.get("commit"))
        return record

    def _require_integration(self, connection, run_id: str, artifact_id: str, integration_id: str | None):
        record = self._find_integration(connection, run_id, artifact_id, integration_id)
        if record is None:
            raise BoardError(
                "INTEGRATION_REQUIRED",
                "An accepted goal must hold a verified integration record or an explicit not-required record for its "
                "final artifact; a delivered worker result alone is not integration evidence",
                runId=run_id,
                artifactId=artifact_id,
            )
        return record

    def _lineage_stop_evidence(self, connection, run_row, action: str) -> dict:
        summary = self.shutdown_summary(connection, run_row["run_id"])
        if not (summary["selfConfirmed"] and summary["descendantsConfirmed"]):
            raise BoardError(
                "SHUTDOWN_UNCONFIRMED",
                f"{action} is only possible after every related attempt and descendant is confirmed stopped",
                unconfirmedRunIds=summary["unconfirmedRunIds"],
                unconfirmedCount=summary["unconfirmedCount"],
            )
        return summary

    def _scope_precheck(self, connection, run_row, expected_scope: int, action: str) -> dict:
        scope = self._current_scope(connection, run_row)
        if scope["scopeVersion"] != expected_scope:
            raise BoardError(
                "REVISION_CONFLICT",
                f"{action} was prepared against a different authorized scope version; re-read it and retry",
                expectedScopeVersion=expected_scope,
                currentScopeVersion=scope["scopeVersion"],
            )
        if run_row["state"] in ("accepted", "cancelled"):
            raise BoardError("CONFLICT", "A finished goal cannot be reauthorized; submit a new goal instead",
                             state=run_row["state"])
        pending = int(connection.execute(
            "SELECT COUNT(*) AS count FROM workflow_continuations WHERE run_id=? AND state IN ('recorded','queued')",
            (run_row["run_id"],),
        ).fetchone()["count"])
        if pending:
            raise BoardError(
                "CONFLICT",
                "A prepared continuation already froze the earlier scope; consume or cancel it before amending",
                pendingContinuations=pending,
            )
        return self._lineage_stop_evidence(connection, run_row, action)

    def scope_amend(self, params: dict, *, console_authority: dict | None = None) -> dict:
        """Append one new authorization scope version for the next stage.

        The original submission, every earlier scope version and every already
        executed turn stay unchanged. An active attempt or descendant is never
        reauthorized retrospectively: the amendment waits for confirmed shutdown
        and only a continuation prepared afterwards uses the new scope.
        """
        schemas.reject_unknown(
            params,
            {
                "runId", "commandId", "expectedRevision", "writeScope", "expectedScopeVersion", "reason",
                *schemas.CONTROL_FIELDS, schemas.CONSOLE_AUTHORITY_FIELD,
            },
            "workflow.scope_amend",
        )
        schemas.reject_untrusted_override(params)
        run_id = schemas.required_string(params, "runId", max_length=128)
        command_id = schemas.required_string(params, "commandId", max_length=128)
        expected = schemas.require_expected_revision(params)
        raw_scope = schemas.string_list(params, "writeScope", limit=256)
        if not raw_scope:
            raise BoardError("INVALID_ARGUMENT", "writeScope must name at least one relative workspace path")
        write_scope = workspace_module().normalize_scope(raw_scope)
        expected_scope = params.get("expectedScopeVersion")
        if isinstance(expected_scope, bool) or not isinstance(expected_scope, int) or expected_scope < 1:
            raise BoardError("INVALID_ARGUMENT", "expectedScopeVersion must be a positive integer")
        reason, _size = schemas.bounded_text(params, "reason", max_bytes=schemas.MAX_WORKFLOW_REASON_BYTES)
        request_key = {"runId": run_id, "expectedScopeVersion": expected_scope, "writeScope": write_scope,
                       "reason": reason, "expectedRevision": expected}
        with self.db.read() as connection:
            run_row = self._run_row(connection, run_id)
            self._authorize(connection, run_row, params, console_authority=console_authority, action="A scope amendment")
            receipt = self.board._receipt(connection, command_id, "workflow.scope_amend", request_key)
            if receipt is not None:
                return {**receipt, "duplicate": True}
            self._expect_revision(run_row, expected)
            self._scope_precheck(connection, run_row, expected_scope, "A scope amendment")
        now = self.now()
        with self.db.write() as connection:
            run_row = self._run_row(connection, run_id)
            actor = self._authorize(connection, run_row, params, console_authority=console_authority, action="A scope amendment")
            receipt = self.board._receipt(connection, command_id, "workflow.scope_amend", request_key)
            if receipt is not None:
                return {**receipt, "duplicate": True}
            self._expect_revision(run_row, expected)
            evidence = self._scope_precheck(connection, run_row, expected_scope, "A scope amendment")
            scope = self._current_scope(connection, run_row)
            version = scope["scopeVersion"] + 1
            connection.execute(
                "INSERT INTO workflow_scope_versions(run_id, scope_version, command_id, actor, reason, write_scope_json,"
                " manifest_sha256, stopped_evidence_json, created_at) VALUES(?,?,?,?,?,?,?,?,?)",
                (run_id, version, command_id, actor, reason, canonical_json(write_scope),
                 scope["inputManifestSha256"], canonical_json(evidence), now),
            )
            self.board._append_event(
                connection,
                "workflow.scope_amended",
                task_id=run_id,
                revision=run_row["revision"] + 1,
                payload={"scopeVersion": version, "previousScopeVersion": scope["scopeVersion"],
                         "writeScope": write_scope, "actor": actor},
            )
            run_row = self._bump_run(connection, run_row, now)
            response = {
                **self.compact(connection, run_row),
                "duplicate": False,
                "previousScopeVersion": scope["scopeVersion"],
                "scopeVersion": version,
                "writeScope": write_scope,
                "inputManifestSha256": scope["inputManifestSha256"],
            }
            self.board._store_receipt(connection, command_id, "workflow.scope_amend", request_key, response, task_id=run_id)
            head = self.board._head_of(connection)
        self.board._notify(head)
        return response

    def _conflict_row(self, connection, run_id: str, conflict_id: str):
        row = connection.execute(
            "SELECT * FROM workflow_workspace_conflicts WHERE conflict_id=? AND run_id=?", (conflict_id, run_id)
        ).fetchone()
        if row is None:
            raise BoardError("NOT_FOUND", "Unknown recorded workspace conflict for this run", conflictId=conflict_id)
        return row

    def _conflict_manifest(self, connection, run_row, conflict) -> dict:
        """The exact execution manifest of the attempt the conflict belongs to."""
        execution = {}
        if conflict["turn_id"]:
            turn = connection.execute("SELECT * FROM workflow_turns WHERE turn_id=?", (conflict["turn_id"],)).fetchone()
            if turn is not None:
                try:
                    document = json.loads(turn["input_json"])
                except (TypeError, ValueError):
                    document = {}
                candidate = document.get("executionWorkspace") if isinstance(document, dict) else None
                if isinstance(candidate, dict):
                    execution = candidate
        if execution.get("manifestSha256") != conflict["manifest_sha256"]:
            execution = json.loads(run_row["workspace_manifest_json"] or "{}")
        if execution.get("manifestSha256") != conflict["manifest_sha256"]:
            raise BoardError("WORKSPACE_CONFLICT",
                             "The recorded conflict does not match the run's execution manifest",
                             conflictId=conflict["conflict_id"])
        return execution

    def workspace_resolve(self, params: dict, *, console_authority: dict | None = None) -> dict:
        """Settle one recorded out-of-scope failure site without another model turn.

        ``restore`` compares-and-swaps the selected paths back to their authorized
        content while preserving every other legal change, ``adopt`` records an
        explicit decision to promote the exact observed site, and ``abandon``
        preserves the site as evidence and returns the checkout to the authorized
        input state. Git work happens outside the transaction and the owner,
        revision, conflict and shutdown evidence are rechecked before recording.
        """
        schemas.reject_unknown(
            params,
            {
                "runId", "commandId", "expectedRevision", "conflictId", "action", "paths", "observedFingerprint",
                "reason", *schemas.CONTROL_FIELDS, schemas.CONSOLE_AUTHORITY_FIELD,
            },
            "workflow.workspace_resolve",
        )
        schemas.reject_untrusted_override(params)
        run_id = schemas.required_string(params, "runId", max_length=128)
        command_id = schemas.required_string(params, "commandId", max_length=128)
        expected = schemas.require_expected_revision(params)
        conflict_id = schemas.required_string(params, "conflictId", max_length=128)
        action = schemas.required_string(params, "action", max_length=32)
        if action not in SCOPE_ACTIONS:
            raise BoardError("INVALID_ARGUMENT", "action must be restore, adopt or abandon")
        paths = schemas.string_list(params, "paths", limit=256)
        observed = schemas.required_string(params, "observedFingerprint", max_length=64)
        if schemas.SHA256_PATTERN.fullmatch(observed) is None:
            raise BoardError("INVALID_ARGUMENT", "observedFingerprint must be a lowercase sha256 hex digest")
        reason = schemas.optional_string(params, "reason", max_length=schemas.MAX_WORKFLOW_REASON_BYTES) or ""
        if action in ("adopt", "abandon"):
            if paths:
                raise BoardError("INVALID_ARGUMENT", f"{action} binds the whole recorded site and takes no path selection")
            if not reason:
                raise BoardError("INVALID_ARGUMENT", f"{action} requires an explicit reason")
        request_key = {"runId": run_id, "conflictId": conflict_id, "action": action, "paths": paths,
                       "observedFingerprint": observed, "reason": reason, "expectedRevision": expected}
        with self.db.read() as connection:
            run_row = self._run_row(connection, run_id)
            actor = self._authorize(connection, run_row, params, console_authority=console_authority, action="A workspace resolution")
            receipt = self.board._receipt(connection, command_id, "workflow.workspace_resolve", request_key)
            if receipt is not None:
                return {**receipt, "duplicate": True}
            self._expect_revision(run_row, expected)
            conflict = self._conflict_row(connection, run_id, conflict_id)
            if conflict["state"] != "open":
                if conflict["action"] == action:
                    return self._resolved_duplicate(connection, run_row, conflict)
                raise BoardError("CONFLICT", "This conflict already has a different recorded resolution",
                                 conflictId=conflict_id, state=conflict["state"], action=conflict["action"])
            self._lineage_stop_evidence(connection, run_row, "A workspace resolution")
            manifest = self._conflict_manifest(connection, run_row, conflict)
        try:
            result = workspace_module().resolve(
                self.board.directory, manifest, task_id=run_id, attempt_id=conflict["attempt_id"], action=action,
                paths=paths, observed_fingerprint=observed, reason=reason, actor=actor,
            )
        except BoardError as error:
            if error.code == "WORKSPACE_CONFLICT" and error.details.get("conflictingPaths"):
                with self.db.write() as connection:
                    connection.execute(
                        "UPDATE workflow_workspace_conflicts SET conflicting_paths_json=?, updated_at=?"
                        " WHERE conflict_id=? AND state='open'",
                        (canonical_json(error.details["conflictingPaths"]), self.now(), conflict_id),
                    )
                    head = self.board._head_of(connection)
                self.board._notify(head)
            raise
        now = self.now()
        with self.db.write() as connection:
            run_row = self._run_row(connection, run_id)
            actor = self._authorize(connection, run_row, params, console_authority=console_authority, action="A workspace resolution")
            receipt = self.board._receipt(connection, command_id, "workflow.workspace_resolve", request_key)
            if receipt is not None:
                return {**receipt, "duplicate": True}
            self._expect_revision(run_row, expected)
            current = self._conflict_row(connection, run_id, conflict_id)
            if current["state"] != "open":
                raise BoardError("REVISION_CONFLICT", "The conflict changed while the resolution ran; re-read it and retry",
                                 conflictId=conflict_id)
            self._lineage_stop_evidence(connection, run_row, "A workspace resolution")
            record = result.get("artifact") if isinstance(result.get("artifact"), dict) else None
            artifact_id = None
            if record is not None:
                artifact_id = self._pin_artifact(
                    connection, run_id=run_id,
                    kind="abandoned-site" if action == "abandon" else "resolved-output",
                    manifest=record, attempt_id=current["attempt_id"], turn_id=current["turn_id"],
                    source_task_id=run_id, now=now,
                )
            connection.execute(
                "UPDATE workflow_workspace_conflicts SET state=?, action=?, resolved_paths_json=?,"
                " conflicting_paths_json=?, artifact_id=?, output_commit=?, output_tree=?, actor=?, reason=?,"
                " command_id=?, updated_at=? WHERE conflict_id=? AND state='open'",
                (result["state"], action, canonical_json(result["resolvedPaths"]),
                 canonical_json(result["remainingPaths"]), artifact_id, (record or {}).get("commit"),
                 (record or {}).get("tree"), actor, reason, command_id, now, conflict_id),
            )
            if result["state"] in ("restored", "adopted", "abandoned"):
                # The Host answered exactly this boundary; close only the requests
                # that pointed at this recorded conflict.
                for request in connection.execute(
                    "SELECT * FROM workflow_requests WHERE run_id=? AND state='open' AND kind='attention'", (run_id,)
                ).fetchall():
                    payload = json.loads(request["payload_json"] or "{}")
                    if conflict_id not in (payload.get("conflictIds") or []):
                        continue
                    connection.execute(
                        "UPDATE workflow_requests SET state='approved', decision_json=?, decided_at=?, updated_at=?"
                        " WHERE request_id=? AND state='open'",
                        (canonical_json({"resolution": {"conflictId": conflict_id, "action": action,
                                                        "state": result["state"], "actor": actor}}),
                         now, now, request["request_id"]),
                    )
            self.board._append_event(
                connection,
                "workflow.workspace_resolved",
                task_id=run_id,
                attempt_id=current["attempt_id"],
                revision=run_row["revision"] + 1,
                payload={"conflictId": conflict_id, "action": action, "state": result["state"],
                         "resolvedPaths": result["resolvedPaths"][:32], "remainingPaths": result["remainingPaths"][:32],
                         "artifactId": artifact_id, "actor": actor},
            )
            run_row = self._bump_run(connection, run_row, now)
            response = {
                **self.compact(connection, run_row),
                "duplicate": False,
                "conflictId": conflict_id,
                "action": action,
                "resolved": result["state"] in ("restored", "adopted", "abandoned"),
                "resolutionState": result["state"],
                "resolvedPaths": result["resolvedPaths"],
                "preservedPaths": result["preservedPaths"],
                "remainingPaths": result["remainingPaths"],
                "observedFingerprint": result["observedFingerprint"],
                "resolvedFingerprint": result["resolvedFingerprint"],
                "artifactId": artifact_id,
                "artifact": self._artifact_row_view(connection, artifact_id),
                "outputCommit": (record or {}).get("commit"),
                "outputTree": (record or {}).get("tree"),
                "diffPath": (record or {}).get("diffPath"),
                "diffSha256": (record or {}).get("diffSha256"),
                "snapshotSha256": (record or {}).get("snapshotSha256"),
                "manifestSha256": (record or {}).get("manifestSha256"),
                "baseCommit": (record or {}).get("baseCommit"),
                "inputCommit": (record or {}).get("inputCommit"),
                "changedPaths": (record or {}).get("changedPaths") or [],
                "adoptedPaths": (record or {}).get("adoptedPaths") or [],
            }
            self.board._store_receipt(connection, command_id, "workflow.workspace_resolve", request_key, response,
                                      task_id=run_id, attempt_id=current["attempt_id"])
            head = self.board._head_of(connection)
        self.board._notify(head)
        return response

    def _artifact_row_view(self, connection, artifact_id: str | None) -> dict | None:
        if not artifact_id:
            return None
        row = connection.execute("SELECT * FROM workflow_artifacts WHERE artifact_id=?", (artifact_id,)).fetchone()
        return self._artifact_view(row) if row is not None else None

    def _resolved_duplicate(self, connection, run_row, conflict) -> dict:
        view = self.compact(connection, run_row)
        return {
            **view,
            "duplicate": True,
            "conflictId": conflict["conflict_id"],
            "action": conflict["action"],
            "resolved": conflict["state"] in ("restored", "adopted", "abandoned"),
            "resolutionState": conflict["state"],
            "resolvedPaths": json.loads(conflict["resolved_paths_json"]),
            "preservedPaths": [],
            "remainingPaths": json.loads(conflict["conflicting_paths_json"]),
            "observedFingerprint": conflict["observed_fingerprint"],
            "resolvedFingerprint": None,
            "artifactId": conflict["artifact_id"],
            "artifact": self._artifact_row_view(connection, conflict["artifact_id"]),
            "outputCommit": conflict["output_commit"],
            "outputTree": conflict["output_tree"],
            "diffPath": None,
            "diffSha256": None,
            "snapshotSha256": None,
            "manifestSha256": conflict["manifest_sha256"],
            "baseCommit": None,
            "inputCommit": None,
            "changedPaths": [],
            "adoptedPaths": [],
        }

    def integration_record(self, params: dict, *, console_authority: dict | None = None) -> dict:
        """Bind one immutable output artifact to the actual integration target.

        A verified record resolves the target checkout identity, the ``ref`` commit
        and both trees from the repository itself and compares the artifact's
        per-path blob identities with the target tree. ``notRequired`` records an
        explicit decision with a reason and performs no Git work.
        """
        schemas.reject_unknown(
            params,
            {
                "runId", "commandId", "expectedRevision", "artifactId", "target", "strategy", "notRequired",
                "reason", "verification", "adjustedPaths", "beforeCommit", *schemas.CONTROL_FIELDS,
                schemas.CONSOLE_AUTHORITY_FIELD,
            },
            "workflow.integration_record",
        )
        schemas.reject_untrusted_override(params)
        run_id = schemas.required_string(params, "runId", max_length=128)
        command_id = schemas.required_string(params, "commandId", max_length=128)
        expected = schemas.require_expected_revision(params)
        artifact_id = schemas.required_string(params, "artifactId", max_length=128)
        strategy = schemas.optional_string(params, "strategy") or ""
        not_required = schemas.optional_bool(params, "notRequired", False)
        reason = schemas.optional_string(params, "reason", max_length=schemas.MAX_WORKFLOW_REASON_BYTES)
        adjusted = schemas.string_list(params, "adjustedPaths", limit=256)
        verification_text = ""
        if "verification" in params and params.get("verification") is not None:
            verification_text, _size = schemas.bounded_text(params, "verification", max_bytes=schemas.MAX_NOTE_BYTES,
                                                            allow_empty=True)
        target = None
        if not_required:
            if strategy not in ("", "not-required"):
                raise BoardError("INVALID_ARGUMENT", "A not-required record cannot name an integration strategy")
            if params.get("target") is not None:
                raise BoardError("INVALID_ARGUMENT", "A not-required record cannot name an integration target")
            if not reason:
                raise BoardError("INVALID_ARGUMENT", "A not-required record requires an explicit reason")
            if adjusted:
                raise BoardError("INVALID_ARGUMENT", "A not-required record cannot carry adjusted paths")
            strategy = "not-required"
        else:
            if strategy not in INTEGRATION_STRATEGIES:
                raise BoardError("INVALID_ARGUMENT", "strategy must be patch, cherry-pick or merge")
            target = schemas.require_object(params.get("target"), "target")
            schemas.reject_unknown(target, {"path", "ref", "repositoryId", "checkoutId"}, "integration target")
            target_path = schemas.required_string(target, "path", max_length=4096)
            if not target_path.startswith("/"):
                raise BoardError("INVALID_ARGUMENT", "target.path must be an absolute checkout path")
            schemas.required_string(target, "ref", max_length=512)
        before_commit = schemas.optional_string(params, "beforeCommit", max_length=128)
        request_key = {"runId": run_id, "artifactId": artifact_id, "strategy": strategy, "notRequired": not_required,
                       "reason": reason, "verification": verification_text, "adjustedPaths": adjusted,
                       "expectedRevision": expected, "beforeCommit": before_commit,
                       "target": ({key: target.get(key) for key in ("path", "ref", "repositoryId", "checkoutId")}
                                  if target is not None else None)}
        with self.db.read() as connection:
            run_row = self._run_row(connection, run_id)
            actor = self._authorize(connection, run_row, params, console_authority=console_authority, action="An integration record")
            receipt = self.board._receipt(connection, command_id, "workflow.integration_record", request_key)
            if receipt is not None:
                return {**receipt, "duplicate": True}
            self._expect_revision(run_row, expected)
            artifact = connection.execute(
                "SELECT * FROM workflow_artifacts WHERE artifact_id=? AND run_id=? AND kind IN ('output','resolved-output')",
                (artifact_id, run_id),
            ).fetchone()
            if artifact is None:
                raise BoardError("NOT_FOUND", "No sealed output artifact of this run has that identity",
                                 artifactId=artifact_id)
            manifest = json.loads(artifact["manifest_json"])
        verification: dict = {}
        binding: dict = {"runId": run_id, "artifactId": artifact_id, "strategy": strategy,
                         "sourceCommit": manifest.get("commit"), "sourceTree": manifest.get("tree")}
        if not_required:
            verification = {"verified": False, "notRequired": True, "reason": reason, "summary": verification_text}
            binding["reason"] = reason
        else:
            if not before_commit:
                raise BoardError("INVALID_ARGUMENT", "beforeCommit is required for a verified integration")
            verification = workspace_module().integration_verify(
                manifest, path=target["path"], ref=target["ref"], strategy=strategy, before_commit=before_commit,
                repository_id=target.get("repositoryId"), checkout_id=target.get("checkoutId"),
                adjusted_paths=adjusted, reason=reason,
            )
            verification["summary"] = verification_text
            if not verification["verified"]:
                raise BoardError("INTEGRATION_UNVERIFIED",
                                 "The artifact is not present in the target as claimed; the record was not created",
                                 artifactId=artifact_id, verification=verification)
            binding.update(beforeCommit=verification["beforeCommit"], afterCommit=verification["afterCommit"],
                           targetPath=verification["target"]["path"], targetRef=target["ref"])
        binding_sha = sha256_text(canonical_json(binding))
        now = self.now()
        with self.db.write() as connection:
            run_row = self._run_row(connection, run_id)
            actor = self._authorize(connection, run_row, params, console_authority=console_authority, action="An integration record")
            receipt = self.board._receipt(connection, command_id, "workflow.integration_record", request_key)
            if receipt is not None:
                return {**receipt, "duplicate": True}
            self._expect_revision(run_row, expected)
            existing = connection.execute(
                "SELECT * FROM workflow_integrations WHERE binding_sha256=?", (binding_sha,)
            ).fetchone()
            if existing is None:
                integration_id = f"int-{uuid.uuid4()}"
                connection.execute(
                    "INSERT INTO workflow_integrations(integration_id, run_id, artifact_id, attempt_id, state, strategy,"
                    " binding_sha256, target_kind, target_path, target_ref, target_repository_id, target_checkout_id,"
                    " source_commit, source_tree, before_commit, after_commit, before_tree, after_tree, verification_json,"
                    " reason, command_id, actor, created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        integration_id, run_id, artifact_id, artifact["attempt_id"],
                        "not-required" if not_required else "verified", strategy, binding_sha,
                        "not-required" if not_required else "checkout",
                        "" if not_required else verification["target"]["path"],
                        "" if not_required else target["ref"],
                        None if not_required else verification["target"]["repositoryId"],
                        None if not_required else verification["target"]["checkoutId"],
                        manifest.get("commit"), manifest.get("tree"),
                        None if not_required else verification["beforeCommit"],
                        None if not_required else verification["afterCommit"],
                        None if not_required else verification["beforeTree"],
                        None if not_required else verification["afterTree"],
                        canonical_json(verification), reason, command_id, actor, now,
                    ),
                )
                self.board._append_event(
                    connection,
                    "workflow.integration_recorded",
                    task_id=run_id,
                    attempt_id=artifact["attempt_id"],
                    revision=run_row["revision"] + 1,
                    payload={"integrationId": integration_id, "artifactId": artifact_id,
                             "state": "not-required" if not_required else "verified",
                             "strategy": strategy, "afterCommit": verification.get("afterCommit")},
                )
                run_row = self._bump_run(connection, run_row, now)
                response = {
                    **self.compact(connection, run_row),
                    "duplicate": False,
                    "integrationId": integration_id,
                    "integration": self._integration_view(connection.execute(
                        "SELECT * FROM workflow_integrations WHERE integration_id=?", (integration_id,)).fetchone()),
                }
            else:
                if existing["run_id"] != run_id or existing["artifact_id"] != artifact_id:
                    raise BoardError("CONFLICT", "This integration binding already belongs to another artifact",
                                     integrationId=existing["integration_id"])
                response = {
                    **self.compact(connection, run_row),
                    "duplicate": True,
                    "integrationId": existing["integration_id"],
                    "integration": self._integration_view(existing),
                }
            self.board._store_receipt(connection, command_id, "workflow.integration_record", request_key, response,
                                      task_id=run_id, attempt_id=artifact["attempt_id"])
            head = self.board._head_of(connection)
        self.board._notify(head)
        return response

    def _cleanup_reasons(self, connection, run_row, task, manifest: dict) -> tuple[list[str], dict, dict]:
        """Everything that must hold before one disposable checkout may be removed."""
        reasons = []
        if run_row["state"] != "accepted" or not task["accepted_at"] or task["acceptance_verdict"] != "accepted":
            reasons.append("not-accepted")
        final_artifact = None
        if run_row["final_artifact_id"]:
            final_artifact = connection.execute(
                "SELECT * FROM workflow_artifacts WHERE artifact_id=? AND run_id=?",
                (run_row["final_artifact_id"], run_row["run_id"]),
            ).fetchone()
        if final_artifact is None:
            reasons.append("final-artifact-missing")
        elif self._find_integration(connection, run_row["run_id"], final_artifact["artifact_id"], None) is None:
            reasons.append("integration-missing")
        summary = self.shutdown_summary(connection, run_row["run_id"])
        if not (summary["selfConfirmed"] and summary["descendantsConfirmed"]):
            reasons.append("shutdown-unconfirmed")
        if self._open_conflict(connection, run_row["run_id"]) is not None:
            reasons.append("unresolved-conflict")
        if int(connection.execute(
            "SELECT COUNT(*) AS count FROM workflow_requests WHERE run_id=? AND state='open'", (run_row["run_id"],)
        ).fetchone()["count"]):
            reasons.append("open-requests")
        if int(connection.execute(
            "SELECT COUNT(*) AS count FROM workflow_children WHERE parent_run_id=? AND state NOT IN"
            " ('succeeded','failed','cancelled')", (run_row["run_id"],)
        ).fetchone()["count"]):
            reasons.append("active-children")
        if int(connection.execute(
            "SELECT COUNT(*) AS count FROM workflow_continuations WHERE run_id=? AND state IN ('recorded','queued')",
            (run_row["run_id"],),
        ).fetchone()["count"]):
            reasons.append("pending-continuations")
        if manifest.get("checkoutId") and int(connection.execute(
            "SELECT COUNT(*) AS count FROM workspace_reservations WHERE checkout_id=? AND state IN ('held','transferred')"
            " AND holder_task_id!=?", (manifest["checkoutId"], run_row["run_id"]),
        ).fetchone()["count"]):
            reasons.append("workspace-dependency")
        evidence = {
            "acceptance": {"state": run_row["state"], "acceptedAt": task["accepted_at"],
                           "verdict": task["acceptance_verdict"]},
            "shutdown": summary,
            "finalArtifactId": run_row["final_artifact_id"],
            "integrationId": (self._find_integration(connection, run_row["run_id"], final_artifact["artifact_id"], None)["integration_id"]
                              if final_artifact is not None else None),
        }
        retention = {
            "workspaceDirectory": str(self.board.directory / "workspaces" / (manifest.get("workspaceId") or "")),
            "fixedRefs": [],
            "outputPatches": [],
            "artifactIds": [],
            "commandReceipts": True,
        }
        for row in connection.execute(
            "SELECT * FROM workflow_artifacts WHERE run_id=? ORDER BY rowid LIMIT 64", (run_row["run_id"],)
        ).fetchall():
            retention["artifactIds"].append(row["artifact_id"])
            try:
                pinned = json.loads(row["manifest_json"])
            except (TypeError, ValueError):
                continue
            if isinstance(pinned.get("diffPath"), str):
                retention["outputPatches"].append(pinned["diffPath"])
            if isinstance(pinned.get("ref"), str):
                retention["fixedRefs"].append(pinned["ref"])
        return reasons, evidence, retention

    def cleanup_plan(self, params: dict, *, console_authority: dict | None = None) -> dict:
        """Plan the removal of exactly one registered disposable Buddy checkout.

        The plan is evidence: it names the exact path and identity, states why the
        workspace is or is not eligible, lists everything that is retained, and
        expires. Repeated planning for the same workspace returns the same live
        plan instead of authorizing a second deletion.
        """
        schemas.reject_unknown(
            params,
            {"runId", "commandId", "expectedRevision", *schemas.CONTROL_FIELDS, schemas.CONSOLE_AUTHORITY_FIELD},
            "workflow.cleanup_plan",
        )
        schemas.reject_untrusted_override(params)
        run_id = schemas.required_string(params, "runId", max_length=128)
        command_id = schemas.required_string(params, "commandId", max_length=128)
        expected = schemas.require_expected_revision(params)
        request_key = {"runId": run_id, "expectedRevision": expected}
        with self.db.read() as connection:
            run_row = self._run_row(connection, run_id)
            actor = self._authorize(connection, run_row, params, console_authority=console_authority, action="A cleanup plan")
            receipt = self.board._receipt(connection, command_id, "workflow.cleanup_plan", request_key)
            if receipt is not None:
                return {**receipt, "duplicate": True}
            self._expect_revision(run_row, expected)
            task = connection.execute("SELECT * FROM tasks WHERE task_id=?", (run_id,)).fetchone()
            manifest = json.loads(run_row["workspace_manifest_json"] or "{}")
            if not manifest:
                raise BoardError("NOT_READY", "This run has no prepared workspace to clean up", runId=run_id)
            existing = connection.execute(
                "SELECT * FROM workspace_cleanup_plans WHERE run_id=? AND workspace_id=? ORDER BY rowid DESC LIMIT 1",
                (run_id, manifest.get("workspaceId")),
            ).fetchone()
            if existing is not None and existing["state"] in ("planned", "applying", "applied"):
                # An applied plan is its own result; a live planned or resuming plan
                # is still the one authorization for this exact path. An expired plan
                # authorizes nothing and is replaced by a fresh one.
                if existing["state"] != "planned" or existing["expires_at"] >= self.now():
                    return {**self.compact(connection, run_row), "duplicate": True, "plan": self._plan_view(existing)}
            reasons, evidence, retention = self._cleanup_reasons(connection, run_row, task, manifest)
            sealed = self._latest_handoff(connection, run_row, manifest)
        module = workspace_module()
        inspection = module.cleanup_inspect(self.board.directory, manifest, sealed=sealed)
        reasons = sorted(set(reasons) | set(inspection["reasons"]))
        if inspection["refs"]:
            retention["fixedRefs"] = sorted(set(retention["fixedRefs"]) | set(inspection["refs"]))
        retention["checkoutRoot"] = inspection["path"]
        retention["cwd"] = inspection["cwd"]
        evidence = {**evidence, "workspace": {key: inspection[key] for key in
                                              ("eligible", "reasons", "kind", "checkoutId", "repositoryId", "worktree",
                                               "locked", "unsealedPaths", "sealedObservation")}}
        now = self.now()
        plan_id = f"cln-{uuid.uuid4()}"
        expires_at = self._expiry(CLEANUP_PLAN_TTL_SECONDS)
        with self.db.write() as connection:
            run_row = self._run_row(connection, run_id)
            actor = self._authorize(connection, run_row, params, console_authority=console_authority, action="A cleanup plan")
            receipt = self.board._receipt(connection, command_id, "workflow.cleanup_plan", request_key)
            if receipt is not None:
                return {**receipt, "duplicate": True}
            self._expect_revision(run_row, expected)
            existing = connection.execute(
                "SELECT * FROM workspace_cleanup_plans WHERE run_id=? AND workspace_id=?"
                " AND state IN ('planned','applying','applied') ORDER BY rowid DESC LIMIT 1",
                (run_id, manifest.get("workspaceId")),
            ).fetchone()
            if existing is not None and (existing["state"] != "planned" or existing["expires_at"] >= now):
                return {**self.compact(connection, run_row), "duplicate": True, "plan": self._plan_view(existing)}
            connection.execute(
                "INSERT INTO workspace_cleanup_plans(plan_id, run_id, workspace_id, checkout_id, repository_id, path,"
                " kind, state, evidence_json, retention_json, reasons_json, command_id, actor, revision, created_at,"
                " expires_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,1,?,?)",
                (plan_id, run_id, manifest.get("workspaceId"), manifest.get("checkoutId"), manifest.get("repositoryId"),
                 inspection["path"], manifest.get("kind"),
                 "blocked" if reasons else "planned", canonical_json(evidence), canonical_json(retention),
                 canonical_json(reasons), command_id, actor, now, expires_at),
            )
            self.board._append_event(
                connection,
                "workflow.cleanup_planned",
                task_id=run_id,
                revision=run_row["revision"] + 1,
                payload={"planId": plan_id, "path": inspection["path"], "eligible": not reasons, "reasons": reasons},
            )
            plan = connection.execute("SELECT * FROM workspace_cleanup_plans WHERE plan_id=?", (plan_id,)).fetchone()
            run_row = self._bump_run(connection, run_row, now)
            response = {**self.compact(connection, run_row), "duplicate": False, "plan": self._plan_view(plan)}
            self.board._store_receipt(connection, command_id, "workflow.cleanup_plan", request_key, response, task_id=run_id)
            head = self.board._head_of(connection)
        self.board._notify(head)
        return response

    def _expiry(self, seconds: int) -> str:
        from datetime import datetime, timedelta, timezone
        moment = datetime.fromisoformat(self.now().replace("Z", "+00:00")) + timedelta(seconds=seconds)
        return moment.isoformat(timespec="milliseconds").replace("+00:00", "Z")

    def cleanup_apply(self, params: dict, *, console_authority: dict | None = None) -> dict:
        """Apply one unexpired cleanup plan to exactly its registered checkout.

        Acceptance, integration, shutdown, dependency and unsealed-change evidence
        are rechecked against the live board and the actual worktree immediately
        before the single ``git worktree remove`` of the exact path. Outputs,
        manifests, Git refs, patches and receipts are outside this deletion.
        """
        schemas.reject_unknown(
            params,
            {"runId", "planId", "commandId", "expectedRevision", "confirmPath", *schemas.CONTROL_FIELDS,
             schemas.CONSOLE_AUTHORITY_FIELD},
            "workflow.cleanup_apply",
        )
        schemas.reject_untrusted_override(params)
        run_id = schemas.required_string(params, "runId", max_length=128)
        plan_id = schemas.required_string(params, "planId", max_length=128)
        command_id = schemas.required_string(params, "commandId", max_length=128)
        expected = schemas.require_expected_revision(params)
        confirm_path = schemas.required_string(params, "confirmPath", max_length=4096)
        request_key = {"runId": run_id, "planId": plan_id, "confirmPath": confirm_path, "expectedRevision": expected}
        with self.db.read() as connection:
            run_row = self._run_row(connection, run_id)
            actor = self._authorize(connection, run_row, params, console_authority=console_authority, action="A cleanup apply")
            receipt = self.board._receipt(connection, command_id, "workflow.cleanup_apply", request_key)
            if receipt is not None:
                return {**receipt, "duplicate": True}
            self._expect_revision(run_row, expected)
            plan = connection.execute(
                "SELECT * FROM workspace_cleanup_plans WHERE plan_id=? AND run_id=?", (plan_id, run_id)
            ).fetchone()
            if plan is None:
                raise BoardError("NOT_FOUND", "Unknown cleanup plan for this run", planId=plan_id)
            if confirm_path != plan["path"]:
                raise BoardError("INVALID_ARGUMENT", "confirmPath must exactly match the planned checkout path",
                                 plannedPath=plan["path"])
            self._cleanup_apply_precheck(connection, run_row, plan)
            task = connection.execute("SELECT * FROM tasks WHERE task_id=?", (run_id,)).fetchone()
            manifest = json.loads(run_row["workspace_manifest_json"] or "{}")
            sealed = self._latest_handoff(connection, run_row, manifest)
        path = Path(plan["path"])
        inspection = None
        if path.exists():
            inspection = workspace_module().cleanup_inspect(self.board.directory, manifest, sealed=sealed)
            if inspection["reasons"]:
                raise BoardError("NOT_READY", "This checkout is not eligible for cleanup", reasons=inspection["reasons"])
        now = self.now()
        with self.db.write() as connection:
            run_row = self._run_row(connection, run_id)
            actor = self._authorize(connection, run_row, params, console_authority=console_authority, action="A cleanup apply")
            receipt = self.board._receipt(connection, command_id, "workflow.cleanup_apply", request_key)
            if receipt is not None:
                return {**receipt, "duplicate": True}
            self._expect_revision(run_row, expected)
            plan = connection.execute(
                "SELECT * FROM workspace_cleanup_plans WHERE plan_id=? AND run_id=?", (plan_id, run_id)
            ).fetchone()
            if plan["state"] == "applied":
                return {**self.compact(connection, run_row), "duplicate": True, "plan": self._plan_view(plan)}
            self._cleanup_apply_precheck(connection, run_row, plan)
            connection.execute(
                "UPDATE workspace_cleanup_plans SET state='applying', command_id=?, revision=revision+1 WHERE plan_id=?"
                " AND state IN ('planned','applying')",
                (command_id, plan_id),
            )
            head = self.board._head_of(connection)
        self.board._notify(head)
        removed = None
        if path.exists():
            removed = workspace_module().cleanup_remove(self.board.directory, manifest)
        with self.db.write() as connection:
            run_row = self._run_row(connection, run_id)
            plan = connection.execute(
                "SELECT * FROM workspace_cleanup_plans WHERE plan_id=? AND run_id=?", (plan_id, run_id)
            ).fetchone()
            if plan["state"] not in ("applying", "applied"):
                raise BoardError("REVISION_CONFLICT", "The cleanup plan changed while the removal ran", planId=plan_id)
            result = {"removed": True, "path": plan["path"], "alreadyRemoved": removed is None,
                      "repositoryId": plan["repository_id"], "checkoutId": plan["checkout_id"],
                      "retention": json.loads(plan["retention_json"])}
            if plan["state"] == "applying":
                connection.execute(
                    "UPDATE workspace_cleanup_plans SET state='applied', applied_at=?, result_json=?, revision=revision+1"
                    " WHERE plan_id=? AND state='applying'",
                    (now, canonical_json(result), plan_id),
                )
                self.board._append_event(
                    connection,
                    "workflow.cleanup_applied",
                    task_id=run_id,
                    revision=run_row["revision"] + 1,
                    payload={"planId": plan_id, "path": plan["path"], "alreadyRemoved": result["alreadyRemoved"]},
                )
                run_row = self._bump_run(connection, run_row, now)
            plan = connection.execute("SELECT * FROM workspace_cleanup_plans WHERE plan_id=?", (plan_id,)).fetchone()
            run_row = self._run_row(connection, run_id)
            response = {**self.compact(connection, run_row), "duplicate": False, "plan": self._plan_view(plan),
                        "removed": True, "retention": result["retention"]}
            self.board._store_receipt(connection, command_id, "workflow.cleanup_apply", request_key, response, task_id=run_id)
            head = self.board._head_of(connection)
        self.board._notify(head)
        return response

    def _cleanup_apply_precheck(self, connection, run_row, plan) -> bool:
        """Recheck plan state, expiry, acceptance and dependencies; True means resume."""
        if plan["state"] == "blocked":
            raise BoardError("NOT_READY", "This cleanup plan was blocked and never authorized a deletion",
                             reasons=json.loads(plan["reasons_json"]))
        if self.now() > plan["expires_at"]:
            raise BoardError("PLAN_EXPIRED", "This cleanup plan expired; plan again before deleting anything",
                             planId=plan["plan_id"], expiresAt=plan["expires_at"])
        task = connection.execute("SELECT * FROM tasks WHERE task_id=?", (run_row["run_id"],)).fetchone()
        manifest = json.loads(run_row["workspace_manifest_json"] or "{}")
        if (plan["workspace_id"] != manifest.get("workspaceId") or plan["checkout_id"] != manifest.get("checkoutId")
                or plan["repository_id"] != manifest.get("repositoryId")):
            raise BoardError("CONFLICT", "The cleanup plan no longer matches the run's workspace allocation",
                             planId=plan["plan_id"])
        resume = plan["state"] == "applying"
        reasons, _evidence, _retention = self._cleanup_reasons(connection, run_row, task, manifest)
        if reasons:
            raise BoardError("NOT_READY", "This checkout is not eligible for cleanup", reasons=reasons)
        return resume

    # -- agent suggestion ----------------------------------------------------
    def suggest(self, params: dict, *, scope: dict | None = None) -> dict:
        schemas.reject_unknown(params, {"runId", "body"}, "workflow.suggest")
        run_id = schemas.required_string(params, "runId", max_length=128)
        body, size = schemas.bounded_text(params, "body", max_bytes=schemas.MAX_QUESTION_BYTES)
        scope = scope or current_scope() or {}
        attempt_id = scope.get("attemptId")
        now = self.now()
        with self.db.write() as connection:
            self._run_row(connection, run_id)
            suggestion_id = f"sug-{uuid.uuid4()}"
            connection.execute(
                "INSERT INTO workflow_suggestions(suggestion_id, run_id, attempt_id, author, body, created_at)"
                " VALUES(?,?,?,?,?,?)",
                (suggestion_id, run_id, attempt_id, f"attempt:{attempt_id}" if attempt_id else "agent", body, now),
            )
            self.board._append_event(
                connection,
                "workflow.suggestion",
                task_id=run_id,
                attempt_id=attempt_id,
                payload={"suggestionId": suggestion_id, "bodyBytes": size},
            )
            head = self.board._head_of(connection)
        self.board._notify(head)
        return {"suggestionId": suggestion_id, "runId": run_id, "recorded": True}

    # -- task-view extension -------------------------------------------------
    def task_extension(self, connection, task_row) -> dict | None:
        run_row = self._run_optional(connection, task_row["task_id"])
        if run_row is None:
            return None
        extension = {
            "state": run_row["state"],
            "awaitingHost": run_row["state"] == "awaiting-host",
            "ownerGeneration": run_row["owner_generation"],
            "hostId": run_row["host_id"],
            "revision": run_row["revision"],
            "continuationCount": run_row["continuation_count"],
            "activeRequestId": run_row["active_request_id"],
        }
        if run_row["active_request_id"]:
            request = connection.execute(
                "SELECT kind, summary, payload_json FROM workflow_requests WHERE request_id=?", (run_row["active_request_id"],)
            ).fetchone()
            if request is not None:
                extension["requestKind"] = request["kind"]
                extension["requestSummary"] = request["summary"]
                payload = json.loads(request["payload_json"])
                extension["requestRouting"] = payload.get("source") == "routing"
                extension["requestTargetRunId"] = (payload.get("origin") or {}).get("runId") or payload.get("childTaskId")
        return extension

    # -- claim gate ----------------------------------------------------------
    def claim_blocker(self, connection, task_row) -> str | None:
        """Why a governed task may not be claimed right now (never a fake stop)."""
        run_row = self._run_optional(connection, task_row["task_id"])
        if run_row is None:
            route = connection.execute(
                "SELECT r.*, w.owner_generation AS current_owner, w.state AS run_state, w.current_routing_id"
                " FROM workflow_routes r JOIN decision_requests d ON d.decision_id=r.decision_id"
                " JOIN workflow_runs w ON w.run_id=r.run_id WHERE d.task_id=?", (task_row["task_id"],),
            ).fetchone()
            if route is not None and (route["state"] != "pending" or route["owner_generation"] != route["current_owner"]
                                      or route["current_routing_id"] != route["decision_id"] or route["run_state"] != "executing"
                                      or self._terminal_ancestor(connection, route["run_id"]) is not None):
                return "workflow-routing-fenced"
            return None
        ancestor = self._terminal_ancestor(connection, run_row["run_id"])
        if ancestor is not None:
            return f"workflow-ancestor-{ancestor['state']}"
        if run_row["state"] == "executing":
            configuration = self._configuration(run_row)
            if configuration is None:
                return "awaiting-model-selection"
            if configuration.get("adapter") in schemas.CODING_ADAPTERS and run_row["validated_configuration_revision"] != run_row["execution_configuration_revision"]:
                return "awaiting-configuration-validation"
            if any(not self._stop_proven(connection, task) for task in self._routing_tasks(connection, run_row["run_id"])):
                return "awaiting-routing-shutdown"
            continuation = connection.execute(
                "SELECT continuation_id, workspace_manifest_json FROM workflow_continuations"
                " WHERE run_id=? AND state='queued' ORDER BY created_at LIMIT 1",
                (run_row["run_id"],),
            ).fetchone()
            turn_count = connection.execute(
                "SELECT COUNT(*) AS count FROM workflow_turns WHERE run_id=?", (run_row["run_id"],)
            ).fetchone()["count"]
            if int(turn_count) > 0 and continuation is None:
                return "awaiting-host"
            if continuation is not None and not continuation["workspace_manifest_json"]:
                return "awaiting-workspace-preparation"
            return None
        if run_row["state"] == "awaiting-host":
            return "awaiting-host"
        if run_row["state"] == "waiting-helpers":
            return "awaiting-helpers"
        if run_row["state"] == "delivered":
            return "awaiting-acknowledgement"
        if run_row["state"] == "accepted":
            return "workflow-accepted"
        if run_row["state"] == "cancelled":
            return "workflow-cancelled"
        return "workflow-failed"

    # -- turn lifecycle ------------------------------------------------------
    def begin_turn(self, connection, *, task, attempt_id: str, generation: int, spec: dict, now: str) -> dict | None:
        """Create the durable turn and its scoped credential inside the claim transaction."""
        run_row = self._run_optional(connection, task["task_id"])
        if run_row is None:
            return None
        if run_row["state"] != "executing":
            raise BoardError(
                "CONFLICT",
                "This governed run is not ready for another turn",
                state=run_row["state"],
            )
        turn_count = int(
            connection.execute(
                "SELECT COUNT(*) AS count FROM workflow_turns WHERE run_id=?", (run_row["run_id"],)
            ).fetchone()["count"]
        )
        continuation = connection.execute(
            "SELECT * FROM workflow_continuations WHERE run_id=? AND state='queued' ORDER BY created_at LIMIT 1",
            (run_row["run_id"],),
        ).fetchone()
        if turn_count > 0 and continuation is None:
            raise BoardError(
                "CONFLICT",
                "A continuation input is required before another turn of this governed run can start",
                runId=run_row["run_id"],
            )
        previous = connection.execute(
            "SELECT * FROM workflow_turns WHERE run_id=? ORDER BY turn_index DESC LIMIT 1",
            (run_row["run_id"],),
        ).fetchone()
        turn_index = turn_count + 1
        resume_mode = "initial" if turn_index == 1 else "reconstructed-new-session"
        configuration = self._configuration(run_row)
        if previous is not None and previous["state"] == "concluded" and previous["session_id"] and configuration:
            previous_input = json.loads(previous["input_json"])
            if previous_input.get("context", {}).get("executionConfiguration") == configuration:
                executor = self._execution_adapter(configuration["adapter"])
                if executor.native_resume:
                    resume_mode = "native-session"
        turn_id = str(uuid.uuid4())
        context = self._turn_context(connection, run_row, task, spec, continuation, previous, turn_index)
        manifest = json.loads(run_row["workspace_manifest_json"]) if run_row["workspace_manifest_json"] else {}
        execution_workspace = self._turn_workspace(connection, run_row, manifest, continuation, turn_index)
        # Exactly the version-1 turn-input document: the runner validates these ten
        # fields, and the resolved execution manifest is already service-owned.
        turn_input = {
            "version": TURN_INPUT_VERSION,
            "taskId": task["task_id"],
            "attemptId": attempt_id,
            "generation": generation,
            "turnId": turn_id,
            "resumeMode": resume_mode,
            "previousSessionId": previous["session_id"] if previous is not None else None,
            "context": context,
            "executionWorkspace": execution_workspace,
        }
        input_json = canonical_json(turn_input)
        connection.execute(
            "INSERT INTO workflow_turns(turn_id, run_id, attempt_id, generation, turn_index, resume_mode,"
            " previous_session_id, input_json, state, created_at, updated_at)"
            " VALUES(?,?,?,?,?,?,?,?,'prepared',?,?)",
            (
                turn_id,
                run_row["run_id"],
                attempt_id,
                generation,
                turn_index,
                resume_mode,
                previous["session_id"] if previous is not None else None,
                input_json,
                now,
                now,
            ),
        )
        connection.execute(
            "UPDATE workflow_runs SET current_turn_id=?, current_attempt_id=?, updated_at=?, revision=revision+1"
            " WHERE run_id=? AND revision=?",
            (turn_id, attempt_id, now, run_row["run_id"], run_row["revision"]),
        )
        if continuation is not None:
            connection.execute(
                "UPDATE workflow_continuations SET state='consumed', attempt_id=?, turn_id=?, consumed_at=?"
                " WHERE continuation_id=?",
                (attempt_id, turn_id, now, continuation["continuation_id"]),
            )
        credential = self._issue_credential(connection, run_row, attempt_id, generation, turn_id, now)
        return {
            "turnId": turn_id,
            "turnIndex": turn_index,
            "executionConfiguration": self._configuration(run_row),
            "resumeMode": resume_mode,
            "inputSha256": sha256_text(input_json),
            "input": turn_input,
            "credential": credential,
            "runId": run_row["run_id"],
        }

    def _turn_workspace(self, connection, run_row, manifest: dict, continuation, turn_index: int) -> dict:
        """The resolved input workspace of one turn.

        Turn one uses the prepared manifest. A continuation uses the manifest this
        service already prepared from the previous sealed output (base = that commit,
        include list = what that seal captured), so a continuation never resumes on
        the original dirty baseline and the turn input is never an unresolved intent.
        """
        if turn_index <= 1 or continuation is None or not continuation["workspace_manifest_json"]:
            return manifest
        return json.loads(continuation["workspace_manifest_json"])

    def _latest_handoff(self, connection, run_row, manifest: dict) -> dict | None:
        """The newest fixed output produced on this run's physical checkout.

        workflow_artifacts is append-only (no delete/replace path); its SQLite rowid
        is publication order, independent of equal timestamps, wall-clock rollback
        and random artifact IDs. Check the output's actual checkout, including the
        parent's own older outputs after an allocation change.
        """
        checkout_id = manifest.get("checkoutId")
        if not checkout_id:
            return None
        for row in connection.execute(
            "SELECT a.manifest_json, t.input_json FROM workflow_artifacts a"
            " JOIN workflow_turns t ON t.run_id=a.run_id AND t.attempt_id=a.attempt_id"
            " WHERE a.kind IN ('output','resolved-output')"
            " AND (a.run_id=? OR EXISTS (SELECT 1 FROM workflow_children c"
            " WHERE c.parent_run_id=? AND c.child_task_id=a.run_id)) ORDER BY a.rowid DESC",
            (run_row["run_id"], run_row["run_id"]),
        ):
            sealed = json.loads(row["manifest_json"])
            # Real seals bind the immutable execution manifest by digest; unlike
            # the early workspace double, they do not repeat checkoutId themselves.
            execution = json.loads(row["input_json"]).get("executionWorkspace") or {}
            if (execution.get("checkoutId") == checkout_id
                    and execution.get("manifestSha256") == sealed.get("manifestSha256")
                    and sealed.get("checkoutId", checkout_id) == checkout_id):
                return sealed
        return None

    def _failed_turn_input(self, connection, run_row, continuation) -> str | None:
        """An explicit recovery may capture partial files only after a failed, stopped turn.

        A recorded scope conflict must first be settled by the Host: an unresolved
        failure site is never promoted to a baseline, and a resolution already pins
        the handoff that the next continuation prepares from.
        """
        if continuation["authorized_by"] != "manual":
            return None
        previous = connection.execute(
            "SELECT t.*, a.execution_state, a.shutdown_confirmed FROM workflow_turns t"
            " JOIN attempts a ON a.attempt_id=t.attempt_id WHERE t.run_id=? ORDER BY t.turn_index DESC LIMIT 1",
            (run_row["run_id"],),
        ).fetchone()
        if previous is None or previous["state"] != "failed" or previous["execution_state"] != "finished" or previous["shutdown_confirmed"] != 1:
            return None
        # Only a pinned artifact establishes a seal; a failed raw report cannot
        # invent one and thereby prevent an authorized recovery. A recorded
        # restore/adopt/abandon decision settles the failed site explicitly, so the
        # dirty-tree recovery path does not apply any more.
        if connection.execute(
            "SELECT 1 FROM workflow_workspace_conflicts WHERE run_id=? AND attempt_id=?"
            " AND state IN ('restored','adopted','abandoned') LIMIT 1",
            (run_row["run_id"], previous["attempt_id"]),
        ).fetchone() is not None:
            return None
        if connection.execute(
            "SELECT 1 FROM workflow_artifacts WHERE run_id=? AND attempt_id=? AND kind IN ('output','resolved-output') LIMIT 1",
            (run_row["run_id"], previous["attempt_id"]),
        ).fetchone() is not None:
            return None
        return previous["attempt_id"]

    def _continuation_intent(self, connection, run_row, continuation, *, recovered_attempt: str | None = None) -> dict | None:
        """The workspace intent of one continuation, or None when no fresh prepare is needed.

        A continuation keeps the *currently allocated physical checkout*: an isolated
        worktree stays the same worktree, so tasks.cwd, the write reservation and the
        adapter cwd never diverge. Only the logical baseline moves, to the newest fixed
        handoff produced on that checkout (the parent's own later seal, a Host
        resolution, or a helper's transferred output, whichever came last), and the
        current authorized scope version supplies the write scope of the new stage.
        """
        manifest = json.loads(run_row["workspace_manifest_json"]) if run_row["workspace_manifest_json"] else {}
        if not manifest or not manifest.get("path"):
            return None
        scope = self._current_scope(connection, run_row)
        if recovered_attempt is not None:
            conflict = self._open_conflict(connection, run_row["run_id"])
            if conflict is not None:
                raise BoardError(
                    "WORKSPACE_SCOPE_CONFLICT",
                    "The failed attempt left changes outside its authorized write scope; restore, adopt or abandon "
                    "the recorded conflict before continuing",
                    conflictId=conflict["conflict_id"],
                    paths=json.loads(conflict["blocking_paths_json"]),
                )
        sealed = self._latest_handoff(connection, run_row, manifest)
        if recovered_attempt is not None:
            # A failed site without a resolution is captured explicitly from the
            # stopped worktree; its out-of-scope paths were already rejected above.
            sealed = None
        if sealed is None and recovered_attempt is None:
            return None
        sealed = sealed or {}
        snapshot = sealed.get("snapshot") if isinstance(sealed.get("snapshot"), dict) else {}
        included = sealed.get("includedUntracked") or snapshot.get("included") or []
        return {
            # Reuse the allocated checkout instead of minting a new worktree.
            "kind": "existing",
            "cwd": manifest["path"],
            "access": manifest.get("access", "write"),
            "base": {"kind": "working-tree", **({"ref": sealed["commit"]} if sealed.get("commit") else {})},
            "includeUntracked": included,
            "integrator": manifest.get("integrator") or "host",
            "writeScope": scope["writeScope"] or manifest.get("writeScope") or ["."],
        }

    def prepare_continuation_workspace(self, params: dict) -> None:
        """Resolve a pending continuation's workspace outside every transaction.

        Called by the service before ``worker_claim`` so the exact canonical turn
        input already contains the resolved manifest; preparation is idempotent by
        the continuation identity, so concurrent workers cannot double-prepare.
        """
        def snapshot(connection, continuation_id):
            row = connection.execute(
                "SELECT * FROM workflow_continuations WHERE continuation_id=? AND state='queued'"
                " AND workspace_manifest_json IS NULL", (continuation_id,),
            ).fetchone()
            if row is None:
                return None
            run = self._run_optional(connection, row["run_id"])
            task = connection.execute("SELECT * FROM tasks WHERE task_id=?", (row["run_id"],)).fetchone()
            if run is None or run["state"] != "executing" or task["state"] != "queued" or task["active_attempt_id"]:
                return None
            attempt = self.board._selected_attempt(connection, task)
            if attempt is not None and (attempt["execution_state"] != "finished" or not attempt["shutdown_confirmed"]):
                return None
            previous = json.loads(run["workspace_manifest_json"] or "{}")
            reservations = connection.execute(
                "SELECT * FROM workspace_reservations WHERE checkout_id=? AND state='held' ORDER BY reservation_id",
                (previous.get("checkoutId"),),
            ).fetchall()
            own = [item for item in reservations if item["holder_task_id"] == row["run_id"]]
            owned_record = connection.execute(
                "SELECT * FROM workspace_reservations WHERE checkout_id=? AND holder_task_id=? ORDER BY rowid DESC LIMIT 1",
                (previous.get("checkoutId"), row["run_id"]),
            ).fetchone()
            if not own and owned_record is not None and owned_record["state"] == "transferred":
                # An authorized helper will return this ownership when it stops.
                return None
            ownership_problem = None
            if len(own) != 1 or any(
                item["holder_task_id"] != row["run_id"]
                and (item["access"] == "write" or previous.get("access") == "write") for item in reservations
            ):
                ownership_problem = {"code": "PREPARATION_CONFLICT", "message": "The continuation checkout is no longer exclusively owned; explicitly continue after its owner releases it"}
            elif any(own[0][key] != previous.get(field) for key, field in (
                ("checkout_id", "checkoutId"), ("repository_id", "repositoryId"),
                ("path", "path"), ("access", "access"),
            )) or task["cwd"] != previous.get("path"):
                ownership_problem = {"code": "PREPARATION_CONFLICT", "message": "The continuation reservation no longer matches its recorded checkout allocation"}
            task_fence = tuple(task[key] for key in ("revision", "state", "active_attempt_id", "selected_attempt_id", "cwd", "spec_json"))
            return dict(row), dict(run), task_fence, [dict(item) for item in reservations], dict(owned_record) if owned_record else None, ownership_problem

        selector = params.get("taskId") or params.get("runId")
        with self.db.read() as connection:
            rows = connection.execute(
                "SELECT c.continuation_id FROM workflow_continuations c JOIN tasks t ON t.task_id=c.run_id"
                " WHERE c.state='queued' AND c.workspace_manifest_json IS NULL AND t.state='queued'"
                + (" AND c.run_id=?" if isinstance(selector, str) and selector else "")
                + " ORDER BY c.rowid LIMIT 50",
                (selector,) if isinstance(selector, str) and selector else (),
            ).fetchall()
        for selected in rows:
            # Refresh immediately before each candidate, not once before a batch of
            # potentially slow Git work. No read or write transaction spans Git.
            with self.db.read() as connection:
                before = snapshot(connection, selected["continuation_id"])
                if before is None:
                    continue
                row, run, *_ = before
                previous = json.loads(run["workspace_manifest_json"])
                problem = None
                intent = None
                recovered_attempt = None
                try:
                    recovered_attempt = self._failed_turn_input(connection, run, row)
                    intent = self._continuation_intent(connection, run, row, recovered_attempt=recovered_attempt)
                except BoardError as error:
                    # An unresolved scope conflict is a durable Host boundary, not a
                    # claim error: record the attention request and keep the site.
                    problem = {"code": error.code, "message": _head(str(error), 2000)}
            if problem is None:
                try:
                    if before[-1] is not None:
                        raise BoardError(before[-1]["code"], before[-1]["message"])
                    if recovered_attempt is not None:
                        module = workspace_module()
                        module.verify(previous, require_unchanged=False)
                        # Enumerate only Git's nonignored paths, then pass exact files
                        # to the existing snapshot API. A directory selector could
                        # accidentally select ignored secrets or runtime caches.
                        included = module.recovery_inputs(previous)
                        intent["includeUntracked"] = schemas.string_list({"includeUntracked": included}, "includeUntracked", limit=256)
                        with self.db.read() as connection:
                            if snapshot(connection, row["continuation_id"]) != before:
                                continue
                    if intent is None:
                        manifest = previous
                    else:
                        manifest = workspace_module().prepare(
                            self.board.directory, f"{row['run_id']}:{row['continuation_id']}", intent
                        )
                    if any(manifest.get(field) != previous.get(field) for field in ("checkoutId", "checkoutRoot", "repositoryId", "path", "access")):
                        raise BoardError("PREPARATION_CONFLICT", "The prepared continuation changed its allocated checkout")
                    with self.db.read() as connection:
                        if snapshot(connection, row["continuation_id"]) != before:
                            continue
                    # A recovered preparation may already have a manifest on disk.
                    # Recheck ownership before scanning its execution fingerprint.
                    workspace_module().verify(manifest, require_unchanged=True)
                except Exception as error:
                    if isinstance(error, BoardError) and error.code == "WORKSPACE_BUSY":
                        # Another worker is preparing the same continuation. Leave it
                        # queued so that worker can publish, or a later claim can recover
                        # its durable files. Contention is not a changed input or failure.
                        continue
                    problem = {"code": getattr(error, "code", "WORKSPACE_PREPARE_FAILED"), "message": _head(str(error), 2000)}
            with self.db.write() as connection:
                if snapshot(connection, row["continuation_id"]) != before:
                    # Cancellation, takeover, override, allocation or ownership drift
                    # invalidates this activation. Keep all prepared recovery files.
                    continue
                now = self.now()
                if problem is not None:
                    request_id = f"prep-{row['continuation_id']}"
                    payload = {
                        "summary": "Continuation workspace preparation needs Host attention",
                        "attempted": f"Prepare continuation {row['continuation_id']}",
                        "neededWork": ["Resolve the workspace conflict and explicitly continue the goal"],
                        "expectedArtifacts": [], "acceptance": "The allocated checkout has a verified fixed input",
                        "continuationId": row["continuation_id"], "preparationError": problem,
                    }
                    connection.execute(
                        "INSERT INTO workflow_requests(request_id, run_id, kind, summary, payload_json, state,"
                        " expected_revision, created_at, updated_at) VALUES(?,?,'attention',?,?,'open',?,?,?)",
                        (request_id, row["run_id"], payload["summary"], canonical_json(payload), run["revision"] + 1, now, now),
                    )
                    connection.execute("UPDATE workflow_continuations SET state='invalidated' WHERE continuation_id=?", (row["continuation_id"],))
                    connection.execute(
                        "UPDATE workflow_runs SET state='awaiting-host', active_request_id=?, updated_at=?, revision=revision+1 WHERE run_id=?",
                        (request_id, now, row["run_id"]),
                    )
                    connection.execute("UPDATE tasks SET queue_reason='awaiting-host', updated_at=? WHERE task_id=?", (now, row["run_id"]),)
                    self._propagate_boundary(connection, self._request_row(connection, row["run_id"], request_id), now)
                    kind = "workflow.workspace_preparation_failed"
                    event = {"continuationId": row["continuation_id"], "requestId": request_id, "errorCode": problem["code"]}
                else:
                    frozen = canonical_json(manifest)
                    connection.execute("UPDATE workflow_continuations SET workspace_manifest_json=? WHERE continuation_id=?", (frozen, row["continuation_id"]))
                    connection.execute(
                        "UPDATE workflow_runs SET workspace_manifest_json=?, workspace_id=?, workspace_manifest_sha256=?,"
                        " updated_at=?, revision=revision+1 WHERE run_id=?",
                        (frozen, manifest["workspaceId"], manifest["manifestSha256"], now, row["run_id"]),
                    )
                    connection.execute(
                        "UPDATE workspace_reservations SET workspace_id=?, manifest_sha256=?, updated_at=?"
                        " WHERE holder_task_id=? AND checkout_id=? AND state='held'",
                        (manifest["workspaceId"], manifest["manifestSha256"], now, row["run_id"], manifest["checkoutId"]),
                    )
                    kind = "workflow.workspace_prepared"
                    event = {"continuationId": row["continuation_id"], "manifestSha256": manifest["manifestSha256"], "checkoutId": manifest["checkoutId"]}
                    if recovered_attempt is not None:
                        event["recoveredAttemptId"] = recovered_attempt
                self.board._append_event(connection, kind, task_id=row["run_id"], revision=run["revision"] + 1, payload=event)
                head = self.board._head_of(connection)
            self.board._notify(head)

    def _issue_credential(self, connection, run_row, attempt_id, generation, turn_id, now) -> str:
        token = self.db.agent_token(run_row["run_id"], attempt_id, generation)
        connection.execute(
            "UPDATE agent_credentials SET state='revoked', revoked_at=?, revision=revision+1"
            " WHERE attempt_id=? AND state='active'",
            (now, attempt_id),
        )
        connection.execute(
            "INSERT INTO agent_credentials(credential_id, run_id, task_id, attempt_id, generation, turn_id,"
            " token_verifier, scopes_json, state, created_at) VALUES(?,?,?,?,?,?,?,?,'active',?)",
            (
                f"cred-{attempt_id}",
                run_row["run_id"],
                run_row["run_id"],
                attempt_id,
                generation,
                turn_id,
                self.db.agent_token_verifier(token),
                canonical_json(sorted(AGENT_OPERATIONS)),
                now,
            ),
        )
        return token

    def _turn_context(self, connection, run_row, task, spec, continuation, previous, turn_index) -> dict:
        goal = json.loads(run_row["goal_json"])
        objective = goal.get("task") if isinstance(goal.get("task"), str) else ""
        context: dict[str, Any] = {
            # Bounded on purpose: the immutable full text stays in the task record.
            "objective": objective if len(objective) <= 8000 else objective[:8000],
            "objectiveTruncated": len(objective) > 8000,
            "taskId": run_row["run_id"],
            "turnIndex": turn_index,
            "executionConfiguration": self._configuration(run_row),
            # The frozen routing identity of this turn: the decision that produced this
            # exact execution configuration. Null means the configuration was explicit;
            # it is never inferred later from the current run, timestamps or model names.
            "routing": {
                "decisionId": run_row["current_routing_id"],
                "executionConfigurationRevision": int(run_row["execution_configuration_revision"]),
            },
        }
        if previous is not None:
            outcome = json.loads(previous["outcome_json"]) if previous["outcome_json"] else {}
            context["lastCheckpoint"] = {
                "turnId": previous["turn_id"],
                "disposition": previous["disposition"],
                "summary": outcome.get("summary"),
                "remaining": outcome.get("remaining", []),
            }
        if continuation is not None:
            context["continuation"] = {
                "authorizedBy": continuation["authorized_by"],
                "reason": continuation["reason"],
                "input": continuation["input_text"],
            }
        request_row = None
        if run_row["active_request_id"]:
            request_row = connection.execute(
                "SELECT * FROM workflow_requests WHERE request_id=?", (run_row["active_request_id"],)
            ).fetchone()
        if request_row is None:
            request_row = connection.execute(
                "SELECT * FROM workflow_requests WHERE run_id=? ORDER BY decided_at DESC LIMIT 1",
                (run_row["run_id"],),
            ).fetchone()
        if request_row is not None and request_row["decision_json"]:
            decision = json.loads(request_row["decision_json"])
            context["hostDecision"] = {
                "requestId": request_row["request_id"],
                "decision": decision.get("decision"),
                "reason": decision.get("reason"),
            }
        frozen = continuation["helper_outcomes_json"] if continuation is not None else None
        if frozen and frozen not in ("[]", ""):
            # Consume the snapshot taken when the continuation was recorded; a later
            # child mutation can never rewrite what this turn was authorized with.
            children = json.loads(frozen)
            context["helperOutcomesFrozen"] = True
        else:
            children = self._helper_outcomes(connection, run_row["run_id"])
        if children:
            context["helperOutcomes"] = children[:MAX_CONTEXT_HELPERS]
            context["helperOutcomeTotal"] = len(children)
        artifacts = connection.execute(
            "SELECT * FROM workflow_artifacts WHERE run_id=? ORDER BY created_at DESC LIMIT ?",
            (run_row["run_id"], MAX_CONTEXT_ARTIFACTS + 1),
        ).fetchall()
        if artifacts:
            context["artifacts"] = [self._artifact_view(row) for row in artifacts[:MAX_CONTEXT_ARTIFACTS]]
            context["artifactTotal"] = len(artifacts)
            if len(artifacts) > MAX_CONTEXT_ARTIFACTS:
                context["artifactsTruncated"] = True
        context["nextActions"] = [
            "Continue the original objective from the recorded checkpoint.",
            "Report the structured outcome through this execution harness's supplied completion interface.",
        ]
        return _bound_context(context)

    def precheck_result(self, params: dict) -> None:
        """Validate a governed result outside any database transaction.

        A sealed output manifest must verify, and a successful governed attempt must
        carry a turn record whose identity, input hash and disposition are importable.
        A failure here is turned into an honest failed result by the caller instead of
        publishing an unverified success.
        """
        attempt_id = params.get("attemptId")
        if not isinstance(attempt_id, str) or not attempt_id:
            return
        result = params.get("result") if isinstance(params.get("result"), dict) else {}
        with self.db.read() as connection:
            attempt = connection.execute("SELECT * FROM attempts WHERE attempt_id=?", (attempt_id,)).fetchone()
            if attempt is None:
                return
            run_row = self._run_optional(connection, attempt["task_id"])
            turn_row = (
                connection.execute("SELECT * FROM workflow_turns WHERE attempt_id=?", (attempt_id,)).fetchone()
                if run_row is not None
                else None
            )
        if run_row is None:
            return
        effective = result.get("workspaceManifest")
        if isinstance(effective, dict) and effective:
            workspace_module().verify(effective, require_unchanged=False)
        seal = result.get("workspaceSeal")
        if isinstance(seal, dict):
            # ``verify`` accepts input manifests; a seal is the immutable output
            # record. It must name this attempt and be based on the effective input.
            for field in ("workspaceId", "taskId", "attemptId", "manifestSha256", "commit", "tree", "snapshotSha256"):
                if not seal.get(field):
                    raise BoardError(
                        "WORKSPACE_INVALID", f"the sealed output has no {field}", field=field
                    )
            if seal["taskId"] != attempt["task_id"] or seal["attemptId"] != attempt_id:
                raise BoardError("WORKSPACE_INVALID", "the sealed output belongs to a different attempt")
            if isinstance(effective, dict) and effective and seal["manifestSha256"] != effective.get("manifestSha256"):
                raise BoardError(
                    "WORKSPACE_INVALID",
                    "the sealed output was not based on the effective turn manifest",
                    expectedManifestSha256=effective.get("manifestSha256"),
                    sealedManifestSha256=seal["manifestSha256"],
                )
        if params.get("status") != "ok" or not params.get("shutdownConfirmed"):
            return
        if turn_row is None:
            raise BoardError("TURN_INVALID", "the governed attempt has no service-owned turn to import")
        rejection = self._validate_turn(self.turn_outcome(result), turn_row, attempt)
        if rejection is not None:
            raise BoardError("TURN_INVALID", rejection, turnId=turn_row["turn_id"])

    @staticmethod
    def result_payload(payload: dict | None) -> dict:
        """The adapter's own runner payload from a stored result or a raw report."""
        if not isinstance(payload, dict):
            return {}
        inner = payload.get("result")
        return inner if isinstance(inner, dict) else payload

    @classmethod
    def turn_outcome(cls, payload: dict | None) -> dict | None:
        turn = cls.result_payload(payload).get("turn")
        return turn if isinstance(turn, dict) else None

    @classmethod
    def yield_disposition(cls, payload: dict | None) -> str | None:
        turn = cls.turn_outcome(payload)
        if turn is None:
            return None
        outcome = turn.get("outcome")
        if not isinstance(outcome, dict):
            return None
        disposition = outcome.get("disposition")
        return disposition if disposition in ("assistance", "attention") else None

    def turn_concluded(
        self, connection, *, task, attempt, payload: dict | None, task_state: str, now: str
    ) -> dict | None:
        """Import one validated structured outcome and advance the governed run."""
        run_row = self._run_optional(connection, task["task_id"])
        if run_row is None:
            return None
        turn_row = connection.execute(
            "SELECT * FROM workflow_turns WHERE attempt_id=?", (attempt["attempt_id"],)
        ).fetchone()
        turn = self.turn_outcome(payload)
        if turn_row is None:
            return None
        ancestor = self._terminal_ancestor(connection, run_row["run_id"])
        if ancestor is not None and run_row["state"] not in ("accepted", "cancelled"):
            self._cancel_owned_run(connection, task, now)
            run_row = self._run_row(connection, run_row["run_id"])
        if run_row["state"] in ("accepted", "cancelled") or ancestor is not None:
            return self._late_cancelled_turn(connection, run_row, turn_row, attempt, payload, now)
        if task_state in ("cancelled", "failed") or (
            not attempt["shutdown_confirmed"] and task_state == "reconciliation-needed"
        ):
            # A cancelled, failed or unconfirmed execution never imports a success.
            connection.execute(
                "UPDATE workflow_turns SET state='failed', updated_at=? WHERE turn_id=?", (now, turn_row["turn_id"])
            )
            self._revoke_credentials(connection, run_row["run_id"], now)
            terminal = "cancelled" if task_state == "cancelled" else "failed" if task_state == "failed" else None
            conflict_ids = (
                self._record_scope_conflicts(connection, run_row, turn_row, attempt, now)
                if task_state == "failed" else []
            )
            if conflict_ids:
                # Sealing refused managed changes outside the authorized scope. The
                # stopped site and its evidence stay recoverable, and the goal opens
                # an explicit Host boundary instead of silently absorbing them.
                request_id = self._record_scope_attention(connection, run_row, turn_row, attempt, conflict_ids, now)
                return {
                    "accepted": False,
                    "disposition": None,
                    "state": "awaiting-host",
                    "requestId": request_id,
                    "conflictIds": conflict_ids,
                }
            if terminal is not None:
                connection.execute(
                    "UPDATE workflow_runs SET state=?, updated_at=?, revision=revision+1 WHERE run_id=?",
                    (terminal, now, run_row["run_id"]),
                )
            return {
                "accepted": False,
                "disposition": None,
                "state": terminal or "uncertain",
            }
        rejection = self._validate_turn(turn, turn_row, attempt)
        if rejection is not None:
            connection.execute(
                "UPDATE workflow_turns SET state='failed', updated_at=? WHERE turn_id=?", (now, turn_row["turn_id"])
            )
            self._revoke_credentials(connection, run_row["run_id"], now)
            connection.execute(
                "UPDATE workflow_runs SET state='failed', updated_at=?, revision=revision+1 WHERE run_id=?",
                (now, run_row["run_id"]),
            )
            self.board._append_event(
                connection,
                "workflow.turn_rejected",
                task_id=run_row["run_id"],
                attempt_id=attempt["attempt_id"],
                revision=run_row["revision"] + 1,
                payload={"turnId": turn_row["turn_id"], "reason": rejection},
            )
            return {"accepted": False, "disposition": None, "state": "failed", "reason": rejection}
        outcome = turn["outcome"]
        disposition = outcome["disposition"]
        connection.execute(
            "UPDATE workflow_turns SET state='concluded', disposition=?, outcome_json=?, provenance_json=?,"
            " session_id=?, prompt_sha256=?, input_sha256=?, turn_result_path=?, sealed_artifacts_json=?,"
            " updated_at=? WHERE turn_id=?",
            (
                disposition,
                canonical_json(outcome),
                canonical_json(turn.get("provenance")),
                turn.get("sessionId"),
                turn.get("promptSha256"),
                turn.get("inputSha256") or turn_row["input_sha256"],
                self.result_payload(payload).get("turnResultPath"),
                canonical_json(self._sealed_artifacts(payload)),
                now,
                turn_row["turn_id"],
            ),
        )
        self._revoke_credentials(connection, run_row["run_id"], now)
        # Every accepted turn pins the manifest it executed on and the output it
        # sealed; a later continuation prepares from that sealed commit rather than
        # from the original baseline. Only a completed turn becomes the final artifact.
        artifact_id = self._pin_result_artifacts(
            connection, run_row, attempt, payload, turn_row["turn_id"], now
        )
        if disposition == "completed":
            connection.execute(
                "UPDATE workflow_runs SET state='delivered', final_attempt_id=?, final_artifact_id=COALESCE(?,"
                " final_artifact_id), current_turn_id=?, current_attempt_id=NULL, updated_at=?, revision=revision+1"
                " WHERE run_id=?",
                (attempt["attempt_id"], artifact_id, turn_row["turn_id"], now, run_row["run_id"]),
            )
            self.board._append_event(
                connection,
                "workflow.turn_concluded",
                task_id=run_row["run_id"],
                attempt_id=attempt["attempt_id"],
                revision=run_row["revision"] + 1,
                payload={"turnId": turn_row["turn_id"], "disposition": disposition},
            )
            return {"accepted": True, "disposition": disposition, "state": "delivered"}
        request_id = self._record_request(connection, run_row, turn_row, turn, disposition, now)
        self.board._append_event(
            connection,
            "workflow.turn_concluded",
            task_id=run_row["run_id"],
            attempt_id=attempt["attempt_id"],
            revision=run_row["revision"] + 1,
            payload={"turnId": turn_row["turn_id"], "disposition": disposition, "requestId": request_id},
        )
        return {"accepted": True, "disposition": disposition, "state": "awaiting-host", "requestId": request_id}

    def _validate_turn(self, turn: dict | None, turn_row, attempt) -> str | None:
        if turn is None:
            return "the runner produced no structured turn outcome"
        if turn.get("version") != TURN_OUTPUT_VERSION:
            return "the turn record version is not supported"
        for key, expected in (
            ("taskId", turn_row["run_id"]),
            ("attemptId", attempt["attempt_id"]),
            ("generation", attempt["generation"]),
            ("turnId", turn_row["turn_id"]),
            ("resumeMode", turn_row["resume_mode"]),
        ):
            if turn.get(key) != expected:
                return f"the turn record {key} does not match the service-owned execution identity"
        if turn.get("inputSha256") != sha256_text(turn_row["input_json"]):
            return "the turn record inputSha256 does not match the turn input the service wrote"
        if turn.get("previousSessionId") != turn_row["previous_session_id"]:
            return "the turn record previousSessionId does not match the exact previous turn"
        if not isinstance(turn.get("promptSha256"), str) or schemas.SHA256_PATTERN.fullmatch(turn["promptSha256"]) is None:
            return "the turn record has no valid promptSha256"
        outcome = turn.get("outcome")
        if not isinstance(outcome, dict):
            return "the turn record carries no outcome object"
        disposition = outcome.get("disposition")
        if disposition not in TURN_DISPOSITIONS:
            return "the turn disposition is not completed, assistance or attention"
        if not isinstance(outcome.get("summary"), str) or not outcome["summary"].strip():
            return "the turn outcome has no summary"
        if disposition in ("assistance", "attention"):
            request = outcome.get("request")
            if not isinstance(request, dict):
                return "an assistance or attention turn requires a request object"
            for field in ("summary", "attempted", "acceptance"):
                if not isinstance(request.get(field), str) or not request[field].strip():
                    return f"the turn request requires a nonempty {field}"
        provenance = turn.get("provenance")
        if not isinstance(provenance, dict):
            return "the turn record carries no provenance"
        if not isinstance(turn.get("sessionId"), str) or not turn["sessionId"]:
            return "the turn record carries no session identity"
        if turn_row["resume_mode"] == "native-session" and turn["sessionId"] != turn_row["previous_session_id"]:
            return "native resume did not retain the exact previous session identity"
        return self._execution_adapter(attempt["adapter"]).validate_turn_provenance(turn)

    def _record_scope_conflicts(self, connection, run_row, turn_row, attempt, now) -> list[str]:
        """Persist any bounded out-of-scope failure-site evidence of one stopped attempt.

        The record is the Host's handle on the preserved site: it names the exact
        observed fingerprint, blocking paths and per-path authorized/observed
        content. It never authorizes those changes and never rewrites the attempt's
        execution receipt.
        """
        if turn_row is None:
            return []
        try:
            module = workspace_module()
        except BoardError:
            return []
        if not hasattr(module, "scope_conflicts"):
            return []
        try:
            document = json.loads(turn_row["input_json"])
        except (TypeError, ValueError):
            document = {}
        execution = document.get("executionWorkspace") if isinstance(document, dict) else None
        manifest = execution if isinstance(execution, dict) else {}
        if not manifest.get("manifestSha256"):
            manifest = json.loads(run_row["workspace_manifest_json"] or "{}")
        if not manifest.get("manifestSha256"):
            return []
        try:
            records = module.scope_conflicts(self.board.directory, manifest, run_row["run_id"], attempt["attempt_id"])
        except BoardError:
            return []
        recorded = []
        for record in records[:4]:
            fingerprint = record.get("observedFingerprint")
            paths = record.get("blockingPaths")
            if not isinstance(fingerprint, str) or not isinstance(paths, list):
                continue
            existing = connection.execute(
                "SELECT conflict_id FROM workflow_workspace_conflicts WHERE run_id=? AND attempt_id=?"
                " AND observed_fingerprint=?",
                (run_row["run_id"], attempt["attempt_id"], fingerprint),
            ).fetchone()
            if existing is not None:
                recorded.append(existing["conflict_id"])
                continue
            conflict_id = f"wsc-{uuid.uuid4()}"
            connection.execute(
                "INSERT INTO workflow_workspace_conflicts(conflict_id, run_id, attempt_id, turn_id,"
                " manifest_sha256, observed_fingerprint, blocking_paths_json, evidence_json, state,"
                " resolved_paths_json, conflicting_paths_json, created_at, updated_at)"
                " VALUES(?,?,?,?,?,?,?,?,'open','[]','[]',?,?)",
                (
                    conflict_id,
                    run_row["run_id"],
                    attempt["attempt_id"],
                    turn_row["turn_id"],
                    manifest.get("manifestSha256"),
                    fingerprint,
                    canonical_json([path for path in paths if isinstance(path, str)][:256]),
                    canonical_json(record),
                    now,
                    now,
                ),
            )
            recorded.append(conflict_id)
        return recorded

    def _record_scope_attention(self, connection, run_row, turn_row, attempt, conflict_ids, now) -> str:
        conflicts = connection.execute(
            "SELECT * FROM workflow_workspace_conflicts WHERE conflict_id IN (%s)"
            % ",".join("?" for _ in conflict_ids),
            tuple(conflict_ids),
        ).fetchall()
        paths = sorted({path for row in conflicts for path in json.loads(row["blocking_paths_json"])})
        payload = {
            "summary": "Workspace changes outside the authorized write scope need a Host decision",
            "attempted": f"Seal attempt {attempt['attempt_id']} on turn {turn_row['turn_id']}",
            "neededWork": ["Record restore, adopt or abandon for the conflict, then continue the goal"],
            "expectedArtifacts": [],
            "acceptance": "The failed site has an explicit resolution and a fixed handoff",
            "conflictIds": list(conflict_ids),
            "paths": paths[:MAX_CONFLICT_PATH_VIEW],
            "reason": "workspace-scope-violation",
        }
        request_id = f"req-{uuid.uuid4()}"
        connection.execute(
            "INSERT INTO workflow_requests(request_id, run_id, turn_id, attempt_id, kind, summary, payload_json,"
            " state, expected_revision, created_at, updated_at) VALUES(?,?,?,?,'attention',?,?,'open',?,?,?)",
            (
                request_id,
                run_row["run_id"],
                turn_row["turn_id"],
                attempt["attempt_id"],
                payload["summary"],
                canonical_json(payload),
                run_row["revision"] + 1,
                now,
                now,
            ),
        )
        connection.execute(
            "UPDATE workflow_runs SET state='awaiting-host', active_request_id=?, current_attempt_id=NULL,"
            " updated_at=?, revision=revision+1 WHERE run_id=? AND revision=?",
            (request_id, now, run_row["run_id"], run_row["revision"]),
        )
        connection.execute(
            "UPDATE tasks SET queue_reason='awaiting-host', updated_at=? WHERE task_id=?",
            (now, run_row["run_id"]),
        )
        self.board._append_event(
            connection,
            "workflow.scope_conflict",
            task_id=run_row["run_id"],
            attempt_id=attempt["attempt_id"],
            revision=run_row["revision"] + 1,
            payload={"requestId": request_id, "conflictIds": list(conflict_ids), "paths": paths[:MAX_CONFLICT_PATH_VIEW]},
        )
        return request_id

    def _record_request(self, connection, run_row, turn_row, turn, disposition, now) -> str:
        outcome = turn["outcome"]
        request = outcome.get("request") or {}
        request_id = f"req-{uuid.uuid4()}"
        payload = {
            "summary": request.get("summary") or outcome.get("summary"),
            "attempted": request.get("attempted"),
            "neededWork": _string_list(request.get("neededWork")),
            "expectedArtifacts": _string_list(request.get("expectedArtifacts")),
            "acceptance": request.get("acceptance"),
            "suggestedProfileId": request.get("suggestedProfileId"),
        }
        connection.execute(
            "INSERT INTO workflow_requests(request_id, run_id, turn_id, attempt_id, kind, summary, payload_json,"
            " state, expected_revision, created_at, updated_at) VALUES(?,?,?,?,?,?,?,'open',?,?,?)",
            (
                request_id,
                run_row["run_id"],
                turn_row["turn_id"],
                turn_row["attempt_id"],
                disposition,
                payload["summary"],
                canonical_json(payload),
                # The request opens at the revision the run reaches in this same
                # transaction (the update below increments exactly once), so a Host
                # decision is fenced against the state it actually saw.
                run_row["revision"] + 1,
                now,
                now,
            ),
        )
        connection.execute(
            "UPDATE workflow_runs SET state='awaiting-host', active_request_id=?, current_turn_id=?,"
            " current_attempt_id=NULL, updated_at=?, revision=revision+1 WHERE run_id=?",
            (request_id, turn_row["turn_id"], now, run_row["run_id"]),
        )
        connection.execute(
            "UPDATE tasks SET queue_reason='awaiting-host', updated_at=? WHERE task_id=?",
            (now, run_row["run_id"]),
        )
        return request_id

    @classmethod
    def _sealed_artifacts(cls, payload: dict | None) -> list[dict]:
        seal = cls.result_payload(payload).get("workspaceSeal")
        if not isinstance(seal, dict):
            return []
        return [
            {
                "kind": "workspace-seal",
                # ``manifestSha256`` is the input digest the turn executed on;
                # ``snapshotSha256`` is the output identity that was pinned.
                "manifestSha256": seal.get("manifestSha256"),
                "snapshotSha256": seal.get("snapshotSha256"),
                "outputCommit": seal.get("commit") or seal.get("outputCommit"),
                "tree": seal.get("tree"),
                "changedPaths": seal.get("changedPaths", []),
                "outsideScope": seal.get("outsideScope", []),
            }
        ]

    def _pin_result_artifacts(self, connection, run_row, attempt, payload, turn_id, now) -> str | None:
        """Pin the turn's effective input manifest and its sealed output."""
        result = self.result_payload(payload)
        effective = result.get("workspaceManifest")
        if isinstance(effective, dict) and effective:
            # The manifest the turn actually executed on is pinned per turn; for a
            # continuation it is the freshly prepared one, never the original baseline.
            self._pin_artifact(
                connection,
                run_id=run_row["run_id"],
                kind="turn-input",
                manifest=effective,
                attempt_id=attempt["attempt_id"],
                turn_id=turn_id,
                source_task_id=run_row["run_id"],
                now=now,
            )
        seal = result.get("workspaceSeal")
        if not isinstance(seal, dict):
            return None
        return self._pin_artifact(
            connection,
            run_id=run_row["run_id"],
            kind="output",
            manifest=seal,
            attempt_id=attempt["attempt_id"],
            turn_id=turn_id,
            source_task_id=run_row["run_id"],
            now=now,
        )

    # -- helper settlement ---------------------------------------------------
    def child_settled(self, connection, *, task, attempt, payload: dict | None, task_state: str, now: str) -> None:
        """Fan a settled helper back into its parent run."""
        run_row = self._run_optional(connection, task["task_id"])
        fenced = run_row is not None and (
            run_row["state"] in ("accepted", "cancelled") or self._terminal_ancestor(connection, task["task_id"]) is not None
        )
        child = connection.execute(
            "SELECT * FROM workflow_children WHERE child_task_id=?", (task["task_id"],)
        ).fetchone()
        if child is None:
            if fenced and self._stop_proven(connection, task):
                self._release_reservations(connection, task["task_id"], now)
            return
        disposition = self.yield_disposition(payload)
        scope_conflict = connection.execute(
            "SELECT 1 FROM workflow_workspace_conflicts WHERE run_id=? AND state='open' LIMIT 1",
            (task["task_id"],),
        ).fetchone() is not None
        if fenced:
            state = child["state"] if child["state"] in ("succeeded", "failed", "cancelled") else "cancelled"
        elif disposition in ("assistance", "attention") or scope_conflict:
            # A failed seal that needs a Host decision keeps this helper's checkout
            # reserved: only a recorded resolution may hand it back.
            state = "attention"
        elif task_state == "completed":
            state = "succeeded"
        elif task_state == "cancelled":
            state = "cancelled"
        else:
            state = "failed"
        connection.execute(
            "UPDATE workflow_children SET state=?, updated_at=?, revision=revision+1 WHERE child_task_id=?",
            (state, now, task["task_id"]),
        )
        if state != "attention":
            # A yielded helper keeps its workspace ownership: it may be resumed by a
            # parent decision, and only a terminal settle hands the checkout back.
            self._settle_helper_reservation(connection, child, task, attempt, now)
        parent = self._run_row(connection, child["parent_run_id"])
        self.board._append_event(
            connection,
            "workflow.helper_settled",
            task_id=task["task_id"],
            attempt_id=attempt["attempt_id"] if attempt is not None else None,
            revision=task["revision"] + 1,
            payload={"parentRunId": parent["run_id"], "state": state, "disposition": disposition},
        )
        if parent["state"] in ("accepted", "cancelled") or self._terminal_ancestor(connection, parent["run_id"]) is not None:
            return
        if state == "attention":
            source = connection.execute(
                "SELECT * FROM workflow_requests WHERE run_id=? AND attempt_id=? AND state='open'"
                " AND kind IN ('assistance','attention') ORDER BY created_at, request_id LIMIT 1",
                (task["task_id"], attempt["attempt_id"]),
            ).fetchone()
            if source is not None:
                self._propagate_boundary(connection, source, now)
            return
        # Cancelling an intermediate helper closes its local requests. Remove
        # their still-open ancestor proxies before choosing the next boundary.
        for proxy in connection.execute(
            "SELECT * FROM workflow_requests WHERE run_id=? AND child_task_id=? AND state='open'",
            (parent["run_id"], task["task_id"]),
        ).fetchall():
            source_id = (json.loads(proxy["payload_json"]).get("proxy") or {}).get("requestId")
            if source_id:
                self._close_proxy_ancestors(connection, self._request_row(connection, task["task_id"], source_id), now)
        parent = self._run_row(connection, parent["run_id"])
        if self._sync_boundary(connection, parent["run_id"], now) is not None:
            return
        if self._unfinished_helpers(connection, parent["run_id"]):
            return
        auto = connection.execute(
            "SELECT * FROM workflow_continuations WHERE run_id=? AND authorized_by='auto' AND state='recorded'"
            " ORDER BY created_at LIMIT 1",
            (parent["run_id"],),
        ).fetchone()
        outcomes = self._helper_outcomes(connection, parent["run_id"])
        if auto is not None and parent["state"] == "waiting-helpers":
            connection.execute(
                "UPDATE workflow_continuations SET state='queued', helper_outcomes_json=? WHERE continuation_id=?",
                (canonical_json(outcomes), auto["continuation_id"]),
            )
            parent_task = connection.execute("SELECT * FROM tasks WHERE task_id=?", (parent["run_id"],)).fetchone()
            try:
                self._requeue(
                    connection,
                    parent_task,
                    parent,
                    reason="authorized helpers settled",
                    actor="workflow:auto",
                    now=now,
                )
                self.board._append_event(
                    connection,
                    "workflow.auto_continued",
                    task_id=parent["run_id"],
                    revision=parent["revision"] + 1,
                    payload={"continuationId": auto["continuation_id"], "helpers": [item["taskId"] for item in outcomes]},
                )
            except BoardError as error:
                # One-use authority stays recorded if the run cannot continue yet
                # (for example the parent's stop is not proven); the Host decides.
                connection.execute(
                    "UPDATE workflow_continuations SET state='recorded' WHERE continuation_id=?",
                    (auto["continuation_id"],),
                )
                self._record_helper_report(connection, parent, outcomes, reason=error.code, now=now)
            return
        if parent["state"] == "waiting-helpers":
            self._record_helper_report(connection, parent, outcomes, reason="helpers-settled", now=now)

    def _record_helper_report(self, connection, parent, outcomes: list[dict], *, reason: str, now: str) -> None:
        request_id = f"req-{uuid.uuid4()}"
        payload = {
            "summary": reason,
            "attempted": None,
            "neededWork": [],
            "expectedArtifacts": [],
            "acceptance": None,
            "helperOutcomes": outcomes,
        }
        connection.execute(
            "INSERT INTO workflow_requests(request_id, run_id, kind, summary, payload_json, state,"
            " expected_revision, created_at, updated_at) VALUES(?,?,'helper-report',?,?,'open',?,?,?)",
            (request_id, parent["run_id"], reason, canonical_json(payload), parent["revision"] + 1, now, now),
        )
        self._sync_boundary(connection, parent["run_id"], now)
        self._propagate_boundary(connection, self._request_row(connection, parent["run_id"], request_id), now)

    def _settle_helper_reservation(self, connection, child, task, attempt, now: str) -> None:
        if not self._stop_proven(connection, task):
            return
        reservations = connection.execute(
            "SELECT * FROM workspace_reservations WHERE holder_task_id=? AND state IN ('held','transferred')",
            (task["task_id"],),
        ).fetchall()
        self._release_reservations(connection, task["task_id"], now)
        for reservation in reservations:
            parent_id = child["parent_run_id"]
            seen = {task["task_id"]}
            while parent_id not in seen:
                seen.add(parent_id)
                parent = self._run_row(connection, parent_id)
                parent_task = connection.execute("SELECT * FROM tasks WHERE task_id=?", (parent_id,)).fetchone()
                closed = parent["state"] in ("accepted", "cancelled") or self._terminal_ancestor(connection, parent_id) is not None
                if closed:
                    if not self._stop_proven(connection, parent_task):
                        break
                    self._release_reservations(connection, parent_id, now)
                    link = connection.execute("SELECT parent_run_id FROM workflow_children WHERE child_task_id=?", (parent_id,)).fetchone()
                    if link is None:
                        break
                    parent_id = link["parent_run_id"]
                    continue
                parent_reservation = connection.execute(
                    "SELECT * FROM workspace_reservations WHERE holder_task_id=? AND checkout_id=? AND state='transferred'"
                    " ORDER BY created_at DESC LIMIT 1", (parent_id, reservation["checkout_id"]),
                ).fetchone()
                # A live descendant may still hold a checkout transferred through
                # several generations. Never reclaim it before that owner stops.
                if parent_reservation is not None and self._held_writer(connection, reservation["checkout_id"]) is None:
                    connection.execute(
                        "UPDATE workspace_reservations SET state='held', updated_at=?, released_at=NULL WHERE reservation_id=?",
                        (now, parent_reservation["reservation_id"]),
                    )
                break

    # -- primitive boundary --------------------------------------------------
    def guard_task_control(self, connection, task_row, params: dict, *, operation: str) -> None:
        """Low-level execution controls cannot bypass governed lineage/evidence."""
        run_row = self._run_optional(connection, task_row["task_id"])
        if run_row is None:
            return
        authority = params.get(schemas.CONSOLE_AUTHORITY_FIELD)
        if not (isinstance(authority, dict) and authority.get("sessionId")):
            authority = console_authority_from_scope()
        control = schemas.normalize_control(params)
        if control is None and authority is None:
            raise BoardError(
                "UNAUTHORIZED",
                f"{operation} on a governed run requires the Host control capability or an authenticated console "
                "session; use the workflow operations instead",
                runId=task_row["task_id"],
            )
        self._authorize(connection, run_row, params, console_authority=authority, action=operation)
        command, native_operation = {
            "task_cancel": ("cancel", "workflow_cancel"),
            "task_retry": ("continue", "workflow_continue"),
            "task_acknowledge": ("acknowledge", "workflow_acknowledge"),
        }[operation]
        raise BoardError("GOVERNED_REQUIRED", f"Use {command} ({native_operation}) for this governed Goal", runId=run_row["run_id"])


# -- module helpers ----------------------------------------------------------
def _original_spec(connection, run_id: str) -> dict | None:
    row = connection.execute("SELECT spec_json FROM tasks WHERE task_id=?", (run_id,)).fetchone()
    if row is None:
        return None
    try:
        return json.loads(row["spec_json"])
    except ValueError:  # pragma: no cover - a stored spec is always canonical JSON
        return None


def _bounded_input(params: dict) -> tuple[str, int]:
    value = params.get("input")
    if isinstance(value, str):
        text = value
    elif isinstance(value, dict):
        text = canonical_json(value)
    else:
        raise BoardError("INVALID_ARGUMENT", "input must be a nonempty string or a JSON object")
    size = len(text.encode("utf-8"))
    if size == 0 or not text.strip():
        raise BoardError("INVALID_ARGUMENT", "input must not be empty")
    if size > schemas.MAX_WORKFLOW_INPUT_BYTES:
        raise BoardError(
            "INVALID_ARGUMENT", f"input must be at most {schemas.MAX_WORKFLOW_INPUT_BYTES} UTF-8 bytes"
        )
    return text, size


def _head(value, limit: int):
    if not isinstance(value, str):
        return value
    return value if len(value) <= limit else value[:limit]


def _string_list(value) -> list[str]:
    """Normalize a bounded string list.

    The native runner contract allows ``neededWork`` to be one nonempty string, so a
    string is exposed as a one-item list; lists stay lists and anything else is
    dropped rather than guessed.
    """
    if isinstance(value, str) and value.strip():
        return [value.strip()[:500]]
    if not isinstance(value, list):
        return []
    out: list[str] = []
    for entry in value[:32]:
        if isinstance(entry, str) and entry.strip():
            out.append(entry.strip()[:500])
    return out


def _bound_context(context: dict) -> dict:
    """Bound the turn context so a Host decision never inflates a prompt."""
    serialized = canonical_json(context)
    if len(serialized.encode("utf-8")) <= schemas.MAX_WORKFLOW_INPUT_BYTES:
        return context
    trimmed = dict(context)
    for key in ("helperOutcomes", "artifacts", "nextActions"):
        if key in trimmed and isinstance(trimmed[key], list):
            trimmed[key] = trimmed[key][:4]
    text = canonical_json(trimmed)
    if len(text.encode("utf-8")) <= schemas.MAX_WORKFLOW_INPUT_BYTES:
        return trimmed
    trimmed["artifacts"] = []
    trimmed["helperOutcomes"] = [
        {k: item.get(k) for k in ("taskId", "state", "disposition", "summary")}
        for item in trimmed.get("helperOutcomes", [])[:4]
    ]
    trimmed["nextActions"] = trimmed.get("nextActions", [])[:1]
    return trimmed


__all__ = [
    "AGENT_OPERATIONS",
    "AGENT_RUN_BOUND_OPERATIONS",
    "WorkflowCoordinator",
    "clear_request_scope",
    "current_scope",
    "set_request_scope",
    "workspace_module",
]
