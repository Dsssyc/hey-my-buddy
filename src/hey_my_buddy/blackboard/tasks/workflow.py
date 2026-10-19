"""Micro-task productivity workflow: Host-directed assistance over ordinary tasks.

The current contract is documented in ``docs/reference/workflow.md``. A
micro-task run *is* the existing logical task (``runId`` = ``taskId``): it uses the
ordinary task/attempt/worker/capacity/receipt machinery, never a second job engine.
What the coordinator adds is durable governance around it:

* an immutable original micro task, a Host owner generation and a control capability;
* one durable turn per actual execution, with the bounded context and the validated
  structured outcome the coding adapter imported after confirmed shutdown;
* assistance/attention requests, explicit helper tasks, one-use automatic
  continuation authority and manual continuation inputs;
* workspace reservations keyed by the actual ``checkoutId`` (so sibling cwd
  directories of one checkout cannot bypass write exclusion);
* attempt-scoped credentials that the service enforces, and console-user authority
  that only a registered console session can present.

The workspace module (``hey_my_buddy.blackboard.tasks.workspace``) performs all Git/filesystem work and is
delivered separately. Preparation and sealing happen outside a database transaction;
immutable manifests are pinned in authoritative records afterwards. Revoking an
unregistered allocation holds the database writer fence through final proof and
exact removal so concurrent admission cannot register a checkout being removed.
"""
from __future__ import annotations

from contextlib import ExitStack, contextmanager
from datetime import datetime, timedelta
import hashlib
import json
import re
import threading
import uuid
from pathlib import Path
from typing import Any

from ...protocol import schemas
from ...protocol.reasons import reason as make_reason, reason_from_json
from ...protocol.stop import WRAP_UP_INITIATORS
from . import fixes as task_fixes
from . import host_conclusions
from . import micro_task
from .request_reasons import request_reason
from .macro_task_deletion_guard import reject_deleting_run, reject_macro_task
from ..store.db import canonical_json, sha256_text
from ...errors import BoardError
from ... import private_dirs

try:  # The workspace module is owned and delivered separately.
    from . import workspace as _workspace_module
except ImportError:  # pragma: no cover - exercised by tests without the module
    _workspace_module = None

# Errors from _requeue and its automatic-path callees: lineage, admission and
# task/run transitions. Manual workspace reclamation is not on either path.
_AUTO_CONTINUE_FAILURE_CODES = {
    "ANCESTOR_TERMINAL": "ANCESTOR_TERMINAL",
    "CONFLICT": "CONFLICT",
    "SHUTDOWN_UNCONFIRMED": "SHUTDOWN_UNCONFIRMED",
    "REVISION_CONFLICT": "REVISION_CONFLICT",
    "UPGRADE_IN_PROGRESS": "UPGRADE_IN_PROGRESS",
    "ILLEGAL_TRANSITION": "ILLEGAL_TRANSITION",
}


#: Operations an attempt-scoped credential may call at all.
AGENT_OPERATIONS = frozenset(
    {
        "ping",
        "health",
        "capabilities",
        "workflow_get",
        "workflow_suggest",
        "task_get",
        "task_result",
        "artifact_list",
        "events_read",
        "message_get",
        "message_list",
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
        "message_get",
        "message_list",
        "inquiry_observe",
    }
)

#: Additional Host operations for a granted run's execution credential only.
LEAD_OPERATIONS = frozenset({
    "workflow_submit", "workflow_decide", "workflow_continue", "workflow_cancel",
    "workflow_accept", "workflow_conclude", "workflow_reclaim", "workflow_scope_amend",
    "workflow_workspace_resolve", "task_list",
})
LEAD_OWNED_OPERATIONS = LEAD_OPERATIONS - {"workflow_submit"} | (
    AGENT_RUN_BOUND_OPERATIONS - {"workflow_suggest"}
)

TURN_INPUT_VERSION = 2
TURN_OUTPUT_VERSION = 1
TURN_DISPOSITIONS = ("completed", "assistance", "attention", "waiting")
MAX_ACTIVE_TURNS_VIEW = 10
MAX_CHILD_VIEW = 32
MAX_ARTIFACT_VIEW = 32
MAX_REQUEST_VIEW = 5
MAX_CONTEXT_HELPERS = 8
MAX_CONTEXT_ARTIFACTS = 8
MAX_CONTEXT_TEXT = 4000
#: The micro-task task-title fallback is bounded like the turn-view summary it
#: projects; the stored outcome itself may be longer and stays intact in audit.
MAX_RESULT_SUMMARY = 2000
WRAP_UP_SECONDS = 300

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

IN_PROGRESS_STATES = ("executing", "waiting-helpers", "waiting-owned")
RUN_STATES = ("executing", "awaiting-host", "waiting-helpers", "waiting-owned", "delivered", "accepted", "concluded", "cancelled", "failed")

# These events follow the corresponding workflow mutation in the same transaction.
OWNED_WAKE_EVENTS = frozenset({
    "workflow.turn_returned", "workflow.turn_rejected", "workflow.turn_failed",
    "workflow.boundary_updated", "workflow.routing_needs_host", "workflow.scope_conflict",
    "workflow.cancelled", "workflow.helper_cancelled", "workflow.lead_task_cancelled", "workflow.workspace_preparation_failed",
    "workflow.late_turn", "workflow.owned_stop_reported",
})
OWNED_WAKE_FILTER_SQL = (
    "(e.kind!='workflow.boundary_updated' OR json_extract(e.payload_json,'$.state')='awaiting-host')"
    " AND (e.kind NOT IN ('workflow.cancelled','workflow.helper_cancelled','workflow.lead_task_cancelled')"
    " OR COALESCE(e.actor_role,'') != 'lead' OR COALESCE(e.actor_id,'') != l.lead_run_id)"
)

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
            "The workspace module is not part of this build, so no micro-task workspace can be prepared or sealed",
        )
    return _workspace_module


def _turn_input_context(input_json: Any) -> dict:
    """The frozen context object of one turn input, or an empty object when unreadable."""
    try:
        document = json.loads(input_json) if input_json else None
    except (TypeError, ValueError):
        return {}
    if isinstance(document, dict) and (type(document.get("version")) is not int
                                      or document["version"] not in (1, TURN_INPUT_VERSION)):
        raise BoardError("TURN_INVALID", "The frozen turn input version is not supported")
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
    revision = routing.get("buddyRevision")
    if decision_id is not None and (not isinstance(decision_id, str) or not decision_id.strip()):
        return None
    if isinstance(revision, bool) or not isinstance(revision, int) or revision < 0:
        return None
    return {"decisionId": decision_id, "buddyRevision": revision}


def _frozen_turn_shared_stash(context: dict, *, compact: bool = False) -> dict | None:
    """The shared-stash observation frozen in one turn input, or ``None``.

    A turn that predates the freezing has no stash fact; it is never re-derived
    from a later reading, wall-clock time or the current repository state. A
    malformed binding is reported as absent rather than invented, and the
    compact view carries states and counts only — the complete bounded record
    stays in the frozen input itself.
    """
    value = context.get("sharedStash")
    if not isinstance(value, dict) or not isinstance(value.get("start"), dict):
        return None
    start = value["start"]
    entries = start.get("entries")
    summary = {
        "repositoryPath": value.get("repositoryPath") if isinstance(value.get("repositoryPath"), str) else None,
        "manifestSha256": value.get("manifestSha256") if isinstance(value.get("manifestSha256"), str) else None,
        "start": {"state": start.get("state"), "entryCount": len(entries) if isinstance(entries, list) else None,
                  "totalCount": start.get("totalCount"), "truncated": bool(start.get("truncated"))},
    }
    if not compact:
        summary["start"]["entries"] = entries if isinstance(entries, list) else None
    return summary


class ClaimStashCoverageRetry(Exception):
    """The claim chose a run whose repository this pass did not observe.

    Raised inside the claim transaction before anything is admitted, so the
    transaction rolls back and the claim retries once with the chosen run as an
    explicit collection selector: every truly-started turn then freezes a real
    observation of its own repository instead of a coverage miss dressed up as
    a reading. Never a :class:`BoardError` — it must escape the store's error
    handling, never reach a caller, and cost nothing when coverage held.
    """

    def __init__(self, task_id: str):
        super().__init__(task_id)
        self.task_id = task_id


class WorkflowCoordinator:
    """Durable governance on top of :class:`hey_my_buddy.blackboard.store.store.BoardStore`."""

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
            raise BoardError("NOT_FOUND", "This run is not a micro-task workflow run", runId=run_id)
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
        adapter = row["execution_adapter"]
        frozen_buddy = context.get("buddy")
        if adapter in schemas.CODING_HARNESSES:
            buddy = schemas.normalize_buddy(frozen_buddy)
            if buddy["harness"] != adapter:
                raise BoardError("TURN_INVALID", "The frozen buddy does not match the turn's execution harness")
        elif adapter in ("command", "external") and frozen_buddy == {"adapter": adapter}:
            buddy = None
        else:
            raise BoardError("TURN_INVALID", "The turn has an unknown execution or frozen buddy shape")
        view = {
            "turnId": row["turn_id"],
            "turnIndex": row["turn_index"],
            "expectedSeconds": row["expected_seconds"],
            "access": row["access"],
            "accessNote": row["access_note"],
            "attemptId": row["attempt_id"],
            "generation": row["generation"],
            "resumeMode": row["resume_mode"],
            "resumeReason": context.get("resumeReason"),
            "resumeDetail": context.get("resumeDetail"),
            "previousSessionId": row["previous_session_id"],
            "sessionId": row["session_id"],
            "state": row["state"],
            "disposition": row["disposition"],
            "outcomeTruncated": json.loads(row["outcome_truncated_json"]) if row["outcome_truncated_json"] else None,
            "createdAt": row["created_at"],
            "updatedAt": row["updated_at"],
            # This turn's own frozen buddy, never the current run's.
            "buddy": buddy,
            # This turn's own frozen routing identity. An older turn input that
            # predates the binding stays null: it is never inferred from the current
            # run, wall-clock timestamps or a matching model name.
            "routing": _frozen_turn_routing(context),
            # This turn's own frozen shared-stash observation, taken before the turn
            # started; never re-derived, and absent for turns that predate it.
            "sharedStash": _frozen_turn_shared_stash(context, compact=compact),
            "tokenUsage": (json.loads(row["token_usage_json"]) if "token_usage_json" in row.keys() and row["token_usage_json"] else None),
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

    def _request_view(self, row, *, include_audit: bool = False, compact: bool = False, connection=None, now=None) -> dict:
        payload = json.loads(row["payload_json"])
        boundary = None
        decision_id = row["decision_id"]
        projected_reason = request_reason(connection, row)
        if connection is not None and payload.get("source") == "routing" and decision_id:
            # Proxy rows retain the leaf's frozen decision. Only command fencing
            # is refreshed from the current owned lineage, never its frozen packet.
            target_id = (payload.get("origin") or {}).get("runId") or row["run_id"]
            target = self._run_optional(connection, target_id)
            decision = self.board.decisions._row(connection, decision_id)
            if target is not None and decision is not None:
                boundary = self._routing_boundary(connection, decision, target, now=now)
        if compact:
            view = {
                "requestId": row["request_id"],
                "kind": row["kind"],
                "state": row["state"],
                "createdAt": row["created_at"],
                "decidedAt": row["decided_at"],
                "childTaskId": row["child_task_id"],
                "proxy": payload.get("proxy"),
                "origin": payload.get("origin"),
                "routing": payload.get("source") == "routing",
                "decisionId": decision_id,
                "reason": projected_reason,
            }
            if boundary is not None:
                view["routingBoundary"] = boundary
            return view
        view = {
            "requestId": row["request_id"],
            "kind": row["kind"],
            "state": row["state"],
            "summary": row["summary"],
            "reason": projected_reason,
            "decisionId": decision_id,
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
        if boundary is not None:
            view["routingBoundary"] = boundary
        if row["summary"] is not None:
            view.update({"attempted": payload.get("attempted"), "neededWork": payload.get("neededWork", []),
                         "expectedArtifacts": payload.get("expectedArtifacts", []),
                         "acceptance": payload.get("acceptance"), "suggestedBuddyId": payload.get("suggestedBuddyId")})
        if "helperOutcomes" in payload:
            view["helperOutcomes"] = payload["helperOutcomes"]
        if isinstance(payload.get("preparationError"), dict):
            problem = payload["preparationError"]
            view["preparationError"] = {
                "code": _head(problem.get("code"), 100),
                "message": _head(problem.get("message"), 2000),
            }
            reason = _preparation_reason(problem.get("details"))
            if reason is not None:
                view["preparationError"]["reason"] = reason
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
        if row["kind"] in ("output", "resolved-output", "partial-output"):
            if row["kind"] == "partial-output":
                view.update(partial=True, verified=False, final=False)
            cumulative = manifest.get("cumulativePatch")
            if isinstance(cumulative, dict):
                paths = cumulative.get("changedPaths") or []
                view["cumulativePatch"] = {**cumulative, "changedPaths": paths[:32], "changedPathsTruncated": len(paths) > 32}
            view["outputCommit"] = manifest.get("commit")
            view["diffPath"] = manifest.get("diffPath")
            view["diffSha256"] = manifest.get("diffSha256")
            changed = manifest.get("changedPaths")
            if isinstance(changed, list):
                view["changedPaths"] = changed[:32]
            if row["kind"] == "resolved-output":
                view["action"] = manifest.get("action")
        elif row["kind"] == WorkflowCoordinator.SHARED_REFS_KIND:
            # The bounded Host projection of the turn's shared-stash facts; the
            # full frozen record stays in the pinned manifest JSON.
            summary = WorkflowCoordinator._shared_refs_summary(manifest)
            if summary is not None:
                view["sharedStash"] = summary
        else:
            view["path"] = manifest.get("path")
            view["baseCommit"] = manifest.get("baseCommit")
            view["inputCommit"] = manifest.get("inputCommit")
        return view

    def _workspace_view(self, run_row, *, connection=None) -> dict | None:
        if not run_row["workspace_manifest_json"]:
            return None
        manifest = json.loads(run_row["workspace_manifest_json"])
        view = {
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
        return view

    def _current_scope(self, connection, run_row) -> dict:
        """The current authorization scope version of one run.

        Every current-contract run records scope version 1 when it is created, and
        later amendments append rows. A run without a scope row is not interpreted
        through a legacy fallback: the state is reported as an integrity error so a
        pre-slice board must be converted offline instead of silently reauthorized.
        """
        row = connection.execute(
            "SELECT * FROM workflow_scope_versions WHERE run_id=? ORDER BY scope_version DESC LIMIT 1",
            (run_row["run_id"],),
        ).fetchone()
        if row is None:
            raise BoardError(
                "STATE_INTEGRITY",
                "This micro-task run has no recorded authorization scope; a current-contract board records scope "
                "version 1 for every creation path, so this state must be converted offline",
                runId=run_row["run_id"],
            )
        return {"scopeVersion": row["scope_version"], "writeScope": json.loads(row["write_scope_json"]),
                "inputManifestSha256": row["manifest_sha256"], "actor": {"role": row["actor_role"], "id": row["actor_id"]} if row["actor_role"] else None, "note": row["note"],
                "recorded": True, "updatedAt": row["created_at"]}

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
        delivery = None
        try:
            candidate = json.loads(row["delivery_json"]) if row["delivery_json"] else None
            delivery = candidate if isinstance(candidate, dict) else None
        except (TypeError, ValueError):
            delivery = None
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
            "actor": {"role": row["actor_role"], "id": row["actor_id"]} if row["actor_role"] else None,
            "note": row["note"],
            "delivery": delivery,
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
            "note": row["note"],
            "actor": {"role": row["actor_role"], "id": row["actor_id"]} if row["actor_role"] else None,
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
        """The compact micro-task view. Never contains control or scoped tokens."""
        task_row = task_row or connection.execute(
            "SELECT * FROM tasks WHERE task_id=?", (run_row["run_id"],)
        ).fetchone()
        host_conclusion = host_conclusions.current(connection, run_row)
        turns = connection.execute(
            "SELECT t.*,a.token_usage_json,a.adapter AS execution_adapter FROM workflow_turns t LEFT JOIN attempts a ON a.attempt_id=t.attempt_id WHERE t.run_id=? ORDER BY t.turn_index DESC LIMIT ?",
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
        owned = connection.execute(
            "SELECT l.*, r.state FROM workflow_lead_tasks l JOIN workflow_runs r ON r.run_id=l.owned_run_id"
            " WHERE l.lead_run_id=? ORDER BY l.created_at,l.owned_run_id LIMIT ?",
            (run_row["run_id"], MAX_CHILD_VIEW + 1),
        ).fetchall()
        owned_total = connection.execute("SELECT COUNT(*) FROM workflow_lead_tasks WHERE lead_run_id=?",
                                         (run_row["run_id"],)).fetchone()[0]
        ownership = connection.execute("SELECT * FROM workflow_lead_tasks WHERE owned_run_id=?",
                                       (run_row["run_id"],)).fetchone()
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
            "ownedMicroTasks": max(0, owned_total - MAX_CHILD_VIEW),
            "artifacts": max(0, artifact_total - MAX_ARTIFACT_VIEW),
            "pendingRequests": max(0, open_count - MAX_REQUEST_VIEW),
        }
        requests = requests[:MAX_REQUEST_VIEW]
        children = children[:MAX_CHILD_VIEW]
        artifacts = artifacts[:MAX_ARTIFACT_VIEW]
        active = self._open_boundary(connection, run_row)
        projection_now = self.now()
        active_request = self._request_view(active, connection=connection, now=projection_now) if active is not None else None
        micro_task = json.loads(run_row["micro_task_json"])
        state = run_row["state"]
        from ..routing.quota_routing import cooling
        view = {
            "runId": run_row["run_id"],
            "taskId": run_row["run_id"],
            "requestId": task_row["request_id"],
            "hostId": run_row["host_id"],
            "ownerGeneration": run_row["owner_generation"],
            "state": state,
            "cancellation": self._cancellation(connection, run_row, host_conclusion),
            "status": task_row["state"],
            "queueReason": task_row["queue_reason"],
            "awaitingHost": state == "awaiting-host",
            "microTask": self._micro_task_view(micro_task, run_row["micro_task_fingerprint"]),
            "revision": run_row["revision"],
            "continuationCount": run_row["continuation_count"],
            "resumeRecovery": self._resume_recovery(connection, run_row["run_id"]),
            "workspace": self._workspace_view(run_row, connection=connection),
            "executionWorkspace": json.loads(run_row["execution_workspace_json"]),
            "access": json.loads(run_row["execution_workspace_json"])["access"],
            "accessNote": json.loads(run_row["execution_workspace_json"]).get("accessNote"),
            "requestFingerprint": run_row["request_fingerprint"],
            "buddy": self._buddy(run_row),
            "buddyLocked": bool(run_row["buddy_locked"]),
            "delegationAuthority": bool(run_row["delegation_authority"]),
            "leadOwnership": ({"leadRunId": ownership["lead_run_id"], "leadTurnId": ownership["lead_turn_id"],
                               "createdAt": ownership["created_at"]} if ownership is not None else None),
            "ownedMicroTasks": [{"runId": row["owned_run_id"], "leadTurnId": row["lead_turn_id"],
                                 "createdAt": row["created_at"], "state": row["state"]}
                                for row in owned[:MAX_CHILD_VIEW]],
            "buddyRevision": run_row["buddy_revision"],
            "providerCooling": cooling(connection, self._buddy(run_row), now=projection_now),
            "routing": self._routing_view(connection, run_row, now=projection_now),
            "currentTurn": self._turn_view(turns[0]) if turns else None,
            "turns": [self._turn_view(row, compact=True) for row in turns],
            "activeRequest": active_request,
            "requests": [self._request_view(row, compact=True, connection=connection, now=projection_now) for row in requests],
            "pendingRequests": [self._request_view(row, compact=True, connection=connection, now=projection_now) for row in pending[:MAX_REQUEST_VIEW]],
            "children": [self._child_view(row) for row in children],
            "artifacts": [self._artifact_view(row) for row in artifacts],
            "scope": self._current_scope(connection, run_row),
            "workspaceConflicts": [self._conflict_view(row) for row in conflicts],
            "integrations": [self._integration_view(row) for row in integrations],
            "cleanup": self._plan_view(cleanup),
            "hostConclusion": host_conclusions.view(host_conclusion),
            "counts": {
                "turns": turn_total,
                "requests": request_total,
                "openRequests": open_count,
                "children": child_total,
                "ownedMicroTasks": owned_total,
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
        view.update(task_fixes.view(connection, run_row["run_id"]))
        if view["task"].get("quotaFailure") is not None:
            view["quotaFailure"] = view["task"]["quotaFailure"]
        if (current_scope() or {}).get("kind") != AGENT_KIND:
            view.update(title=run_row["title"], macroTaskId=run_row["macro_task_id"])
        return view

    @staticmethod
    def _cancellation(connection, run_row, conclusion):
        if run_row["state"] != "cancelled" and (conclusion is None or conclusion["execution_status"] != "cancelled"):
            return None
        from .cancellation import for_run
        return for_run(connection, run_row["run_id"])

    @staticmethod
    def _micro_task_view(micro_task: dict, fingerprint: str) -> dict:
        """The bounded micro task summary; the immutable original spec is in audit."""
        task = micro_task.get("task") if isinstance(micro_task.get("task"), str) else ""
        limit = 2000
        return {
            "task": task if len(task) <= limit else task[:limit],
            "taskTruncated": len(task) > limit,
            "taskBytes": len(task.encode("utf-8")),
            "harness": micro_task.get("harness"),
            "cwd": micro_task.get("cwd"),
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
            "cancellation": decorated.get("cancellation"),
            "state": decorated["state"],
            "queueReason": decorated["queueReason"],
            "revision": decorated["revision"],
            "expectedSeconds": decorated["expectedSeconds"],
            "selectedAttemptId": decorated["selectedAttemptId"],
            "activeAttemptId": decorated["activeAttemptId"],
            "resultAvailable": decorated["resultAvailable"],
            "tokenUsage": decorated.get("tokenUsage"),
            "quotaFailure": decorated.get("quotaFailure"),
            "shutdownConfirmed": decorated["shutdownConfirmed"],
            "terminationReason": decorated["terminationReason"],
            "interruptionEvidence": decorated.get("interruptionEvidence"),
            "stopInitiator": decorated["stopInitiator"],
            "graceDeadline": decorated["graceDeadline"],
            "acceptedAt": decorated["acceptedAt"],
            "acceptanceVerdict": decorated["acceptanceVerdict"],
            "acceptanceNote": decorated["acceptanceNote"],
            "reviewRejectedAt": decorated["reviewRejectedAt"],
            "reviewRejectionNote": decorated["reviewRejectionNote"],
            "artifactCount": decorated["artifactCount"],
            "inquiries": decorated["inquiries"],
            "activity": decorated["activity"],
            "lineage": decorated["lineage"],
            "createdAt": decorated["createdAt"],
            "updatedAt": decorated["updatedAt"],
        }
        if "attemptGeneration" in decorated:
            summary["attemptGeneration"] = decorated["attemptGeneration"]
            summary["attemptState"] = decorated["attemptState"]
        if "workflow" in decorated:
            summary["workflow"] = decorated["workflow"]
        if "workflowState" in decorated:
            # The micro-task marker the UI keys on, kept alongside the full record.
            summary["workflowState"] = decorated["workflowState"]
            summary["awaitingHost"] = decorated["awaitingHost"]
            summary["workflowShutdown"] = decorated["workflowShutdown"]
        return summary

    # -- authority -----------------------------------------------------------
    def _authorize(self, connection, run_row, params: dict, *, console_authority: dict | None, action: str) -> tuple[dict, str]:
        """Resolve execution authority or validate Host control, returning its role and identity."""
        if console_authority is not None:
            return {"role": "console", "id": console_authority.get("sessionId", "session")}, "console"
        scope = current_scope()
        if scope is not None and scope["kind"] == AGENT_KIND:
            self._active_credential(connection, scope)
            return {"role": "lead", "id": scope["runId"]}, "lead"
        if run_row["host_id"] is None:
            raise BoardError("UNAUTHORIZED", "A Lead-owned micro task requires an active execution credential or authenticated console")
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
        return {"role": "host", "id": control["hostId"]}, "host"

    def _owner_actor(self, connection, run_row) -> dict:
        """Current ownership comes from the controlling run and its Lead relation."""
        row = connection.execute(
            "WITH RECURSIVE owners(run_id) AS (SELECT ? UNION ALL "
            "SELECT c.parent_run_id FROM workflow_children c JOIN owners o ON c.child_task_id=o.run_id) "
            "SELECT r.host_id,l.lead_run_id FROM owners o JOIN workflow_runs r USING(run_id) "
            "LEFT JOIN workflow_lead_tasks l ON l.owned_run_id=r.run_id "
            "WHERE NOT EXISTS (SELECT 1 FROM workflow_children c WHERE c.child_task_id=r.run_id)",
            (run_row["run_id"],),
        ).fetchone()
        if row["host_id"] is not None:
            return {"role": "host", "id": row["host_id"]}
        if row["lead_run_id"] is not None:
            return {"role": "lead", "id": row["lead_run_id"]}
        raise BoardError("STATE_INTEGRITY", "A micro task has no recorded current owner")

    def _expect_revision(self, run_row, expected: int | None) -> None:
        if expected is None:
            return
        if expected != run_row["revision"]:
            raise BoardError(
                "REVISION_CONFLICT",
                "The micro-task run changed since this decision was prepared; re-read it and retry",
                expectedRevision=expected,
                currentRevision=run_row["revision"],
            )

    # -- scoped credentials --------------------------------------------------
    @staticmethod
    def _active_credential(connection, scope: dict) -> None:
        """Fence a prepared mutation against execution revocation at commit."""
        if connection.execute("SELECT 1 FROM agent_credentials WHERE credential_id=? AND state='active'",
                              (scope["credentialId"],)).fetchone() is None:
            raise BoardError("UNAUTHORIZED", "The Lead execution credential was revoked before the operation committed")

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
                "SELECT c.*, r.delegation_authority FROM agent_credentials c"
                " JOIN workflow_runs r ON r.run_id=c.run_id"
                " WHERE c.token_verifier=? AND c.state='active'", (verifier,)
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
            "delegationAuthority": bool(row["delegation_authority"]),
        }

    def authorize_agent_operation(self, operation: str, params: dict, scope: dict) -> None:
        """Enforce the permitted operation set of an attempt-scoped credential."""
        lead = scope["delegationAuthority"]
        if operation not in AGENT_OPERATIONS | (LEAD_OPERATIONS if lead else frozenset()):
            raise BoardError(
                "FORBIDDEN",
                f"This attempt-scoped credential cannot call {operation}",
                operation=operation,
            )
        if operation not in AGENT_RUN_BOUND_OPERATIONS | LEAD_OWNED_OPERATIONS:
            return
        targets = []
        for key in ("runId", "taskId"):
            if key in params:
                targets.append(schemas.required_string(params, key, max_length=128))
        selectors = (
            ("requestId", "SELECT task_id FROM tasks WHERE request_id=?"),
            ("attemptId", "SELECT task_id FROM attempts WHERE attempt_id=?"),
            ("messageId", "SELECT task_id FROM messages WHERE message_id=?"),
        )
        with self.db.read() as connection:
            for key, query in selectors:
                # A decision request belongs to the bound run; its handler checks
                # that request and any helper target through the existing tree.
                if operation == "workflow_decide" and key == "requestId":
                    continue
                value = params.get(key)
                if isinstance(value, str) and value:
                    row = connection.execute(query, (value,)).fetchone()
                    targets.append(row["task_id"] if row is not None else "__unknown__")
            for target in targets:
                own = operation in AGENT_RUN_BOUND_OPERATIONS and target == scope["runId"]
                owned = lead and operation in LEAD_OWNED_OPERATIONS and connection.execute(
                    "SELECT 1 FROM workflow_lead_tasks WHERE owned_run_id=? AND lead_run_id=?",
                    (target, scope["runId"]),
                ).fetchone() is not None
                if not (own or owned):
                    raise BoardError("FORBIDDEN", "An execution credential can only address its own permitted runs, whatever selector it supplies",
                                     runId=scope["runId"])
        if not targets:
            if operation == "task_list":
                # The list query binds every returned row to this Lead's ownership.
                return
            if operation not in AGENT_RUN_BOUND_OPERATIONS:
                raise BoardError("FORBIDDEN", "A Lead operation requires an owned micro task target")
            params["runId"] = scope["runId"]

    # -- workspace reservations ---------------------------------------------
    def _cleanup_fence(self, path: str | None) -> None:
        """Refuse a source capture on a checkout whose cleanup plan is applying.

        This is the early half of the same fence reservation admission enforces
        atomically: it keeps a new input from even being snapshotted out of a
        checkout that is being removed. The final claim is still refused inside the
        write transaction, so the guarantee never depends on this read.
        """
        if not path:
            return
        with self.db.read() as connection:
            plan = connection.execute(
                "SELECT plan_id, checkout_id FROM workspace_cleanup_plans WHERE path=? AND state='applying'"
                " ORDER BY rowid DESC LIMIT 1",
                (str(Path(path).resolve()),),
            ).fetchone()
        if plan is not None:
            raise BoardError(
                "PREPARATION_CONFLICT",
                "This checkout is being removed by an applying cleanup plan; it cannot be captured as new input",
                checkoutId=plan["checkout_id"],
                planId=plan["plan_id"],
                path=str(path),
            )

    def _held_writer(self, connection, checkout_id: str):
        return connection.execute(
            "SELECT * FROM workspace_reservations WHERE checkout_id=?"
            " AND state='held' AND access='write' ORDER BY rowid",
            (checkout_id,),
        ).fetchone()

    def _unproven_checkout_holder(self, connection, manifest, checkout_id):
        """Refuse a conflicting reservation that records the same checkout path."""
        root = manifest.get("checkoutRoot")
        if not root:
            return None
        rows = connection.execute(
            "SELECT r.*, run.workspace_manifest_json AS run_manifest, child.workspace_manifest_json AS child_manifest,"
            " (SELECT a.manifest_json FROM workflow_artifacts a WHERE a.run_id=r.holder_task_id"
            " AND a.kind='input' ORDER BY a.rowid LIMIT 1) AS initial_manifest"
            " FROM workspace_reservations r LEFT JOIN workflow_runs run ON run.run_id=r.holder_task_id"
            " LEFT JOIN workflow_children child ON child.child_task_id=r.holder_task_id"
            " WHERE r.state IN ('held','transferred') AND r.checkout_id!=?", (checkout_id,),
        ).fetchall()
        for row in rows:
            roots = []
            for field in ("run_manifest", "child_manifest", "initial_manifest"):
                try:
                    recorded = json.loads(row[field] or "{}")
                except (ValueError, TypeError):
                    continue
                if isinstance(recorded, dict):
                    roots.append(recorded.get("checkoutRoot"))
            if root in roots or row["path"] == manifest.get("path"):
                return row
        return None

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
        # Reservation admission is the one atomic claim point for a checkout. A cleanup
        # plan in ``applying`` has already passed its inspection but its removal has not
        # finished, so no new writer may take the checkout across that boundary; the
        # check and the insert below commit in the same SQLite write transaction.
        cleanup = connection.execute(
            "SELECT plan_id, path FROM workspace_cleanup_plans WHERE (checkout_id=? OR path=?)"
            " AND state='applying' ORDER BY rowid DESC LIMIT 1",
            (checkout_id, manifest.get("checkoutRoot")),
        ).fetchone()
        if cleanup is not None:
            raise BoardError(
                "PREPARATION_CONFLICT",
                "This checkout is being removed by an applying cleanup plan; no writer may claim it",
                checkoutId=checkout_id,
                planId=cleanup["plan_id"],
                path=cleanup["path"],
            )
        unproven = self._unproven_checkout_holder(connection, manifest, checkout_id)
        if unproven is not None:
            raise BoardError(
                "PREPARATION_CONFLICT", "This checkout still has a reservation whose recorded identity is unproven; resolve that ownership before claiming it",
                checkoutId=checkout_id, holderTaskId=unproven["holder_task_id"],
                recordedCheckoutId=unproven["checkout_id"],
            )
        held = connection.execute(
            "SELECT * FROM workspace_reservations WHERE checkout_id=? AND state='held' ORDER BY rowid",
            (checkout_id,),
        ).fetchall()
        writers = [row for row in held if row["access"] == "write"]
        if len(writers) > 1:
            raise BoardError(
                "PREPARATION_CONFLICT", "Several reservations still hold this checkout; resolve their ownership before claiming it",
                checkoutId=checkout_id,
                holderTaskIds=sorted({row["holder_task_id"] for row in writers}),
            )
        writer = writers[0] if writers else None
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
            for row in held:
                if row["manifest_sha256"] != manifest_sha:
                    raise BoardError(
                        "SNAPSHOT_CHANGED",
                        "Read sharing requires one unchanged snapshot; this checkout already has a reservation for "
                        "a different manifest",
                        checkoutId=checkout_id,
                    )
        existing = [row for row in held if row["holder_task_id"] == holder_task_id]
        if len(existing) > 1:
            raise BoardError("PREPARATION_CONFLICT", "This owner has duplicate reservations for one checkout",
                             checkoutId=checkout_id, holderTaskId=holder_task_id)
        if existing:
            if (existing[0]["path"] != manifest.get("path") or existing[0]["access"] != access
                    or existing[0]["repository_id"] != manifest.get("repositoryId")):
                raise BoardError("PREPARATION_CONFLICT", "The held reservation no longer matches the prepared checkout allocation",
                                 checkoutId=checkout_id, holderTaskId=holder_task_id)
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
        # or accepting the micro task releases it too, so no writer claim leaks forever.
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
        if (reservation is None or task["cwd"] != manifest.get("path")
                or reservation["checkout_id"] != manifest.get("checkoutId")
                or reservation["repository_id"] != manifest.get("repositoryId")
                or reservation["path"] != manifest.get("path") or reservation["access"] != ("read" if manifest["access"] == "read" else "write")):
            raise BoardError("PREPARATION_CONFLICT", "The continuation no longer owns its recorded checkout allocation")
        if reservation["state"] == "transferred":
            # The authorized helper still owns it. Its normal settlement restores
            # this reservation; a manual continuation cannot take it back early.
            return
        conflicts = connection.execute(
            "SELECT * FROM workspace_reservations WHERE checkout_id=? AND holder_task_id!=?"
            " AND (state='held' OR (?='released' AND state='transferred')) AND (access='write' OR ?='write')",
            (manifest["checkoutId"], task["task_id"], reservation["state"], "read" if manifest["access"] == "read" else "write"),
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
                      access="read" if manifest["access"] == "read" else "write", now=now)
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
        identity_key = "snapshotSha256" if kind in ("output", "resolved-output", "partial-output", "abandoned-site") else "manifestSha256"
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
    def _buddy(run_row) -> dict | None:
        value = run_row["buddy_json"]
        return json.loads(value) if value else None

    def _validated_buddy(self, spec: dict) -> dict | None:
        """Inspect native buddy only outside a database write transaction."""
        constraints = schemas.buddy_constraints(spec)
        if len(constraints) != len(schemas.BUDDY_FIELDS):
            return None
        from ..catalog.catalog import validate_buddy

        return validate_buddy(schemas.normalize_buddy(constraints), directory=self.board.directory)

    @staticmethod
    def _require_permission(buddy: dict | None, access: str) -> None:
        from ...buddy.harnesses.registry import require_permission
        require_permission(buddy, access)

    @staticmethod
    def _execution_adapter(name: str):
        from ...buddy.harnesses.registry import adapter

        return adapter(name)

    def effective_spec(self, connection, task) -> dict:
        """Execution projection; the stored original spec/fingerprint never changes."""
        spec = json.loads(task["spec_json"])
        run = self._run_optional(connection, task["task_id"])
        if run is not None:
            spec = {**spec, **(self._buddy(run) or {}), "adapter": task["adapter"], "cwd": task["cwd"]}
        return spec

    def _routing_boundary(self, connection, decision, run, *, post_transition: bool = False, now=None):
        """Project a frozen decision with the current owning Host's command fence."""
        from ..routing.router_boundary_data import routing_boundary

        owner, seen = run, set()
        while owner["run_id"] not in seen:
            seen.add(owner["run_id"])
            parent = connection.execute(
                "SELECT parent_run_id FROM workflow_children WHERE child_task_id=?", (owner["run_id"],)
            ).fetchone()
            if parent is None:
                break
            candidate = self._run_optional(connection, parent["parent_run_id"])
            if candidate is None:
                break
            owner = candidate
        # Establishing the leaf boundary increments its revision and propagates
        # one boundary transition to each ancestor in the same transaction.
        increment = 1 if post_transition else 0
        return routing_boundary(
            connection, decision, now=now if now is not None else self.now(), run_id=run["run_id"],
            revision=run["revision"] + increment, owner_run_id=owner["run_id"],
            owner_revision=owner["revision"] + increment,
        )

    def _routing_view(self, connection, run, *, now=None) -> dict:
        from ..routing.router import selection_source
        micro_task = json.loads(run["micro_task_json"])
        if not run["current_routing_id"]:
            request = self._open_boundary(connection, run)
            needs_host = request is not None and json.loads(request["payload_json"]).get("source") == "routing"
            override = connection.execute(
                "SELECT payload_json FROM events WHERE task_id=? AND kind='workflow.buddy_overridden' ORDER BY seq DESC LIMIT 1",
                (run["run_id"],),
            ).fetchone()
            override_payload = json.loads(override["payload_json"]) if override else None
            source = ("host-override" if override_payload and
                      override_payload.get("buddyRevision") == run["buddy_revision"]
                      else "original-explicit" if len(schemas.buddy_constraints(micro_task)) == len(schemas.BUDDY_FIELDS)
                      else None)
            preparing = (run["state"] == "executing" and self._buddy(run) is None
                         and connection.execute("SELECT 1 FROM workflow_continuations WHERE run_id=? AND state='queued'"
                                                " AND workspace_manifest_json IS NULL LIMIT 1", (run["run_id"],)).fetchone() is not None)
            return {"status": "needs-host" if needs_host else "queued" if preparing else "explicit", "decisionId": None, "taskId": None,
                    "constraints": schemas.buddy_constraints(micro_task),
                    "source": None if preparing else source}
        link = connection.execute("SELECT * FROM workflow_routes WHERE decision_id=?", (run["current_routing_id"],)).fetchone()
        decision = self.board.decisions._row(connection, run["current_routing_id"])
        status = decision["status"] if link["state"] == "pending" else {
            "resolved": "completed", "needs-host": "needs-host", "fenced": "fenced",
        }[link["state"]]
        requested = json.loads(decision["requested_json"])
        current = self.board.decisions._view(connection, decision, include_audit=False)
        view = {
            "status": status, "decisionId": decision["decision_id"], "taskId": decision["decision_task_id"],
            "attemptId": decision["decision_attempt_id"], "generation": decision["decision_generation"],
            "selectedBuddy": json.loads(decision["selected_json"]) if decision["selected_json"] else None,
            "reason": current["reason"],
            "note": current["note"],
            "constraints": requested.get("constraints", schemas.buddy_constraints(micro_task)),
            "requiredCapabilities": requested.get("requiredCapabilities", []),
            "source": selection_source(requested),
            **{key: current[key] for key in (
                "routerBuddyIds", "routerIdentities", "routingMode", "routingBudget", "budget",
                "routerRetryIntervalSeconds", "routerProblem",
                "routerBuddyId", "routerBuddy", "routerIndex", "decisionModel", "routerTrials",
            ) if key in current},
            "routingBasis": requested.get("routingBasis"),
        }
        boundary = self._routing_boundary(connection, decision, run, now=now)
        if boundary is not None:
            view["routingBoundary"] = boundary
        return view

    def _start_routing(self, connection, run_id: str, now: str) -> None:
        run = self._run_row(connection, run_id)
        sequence = connection.execute("SELECT COUNT(*) FROM workflow_routes WHERE run_id=?", (run_id,)).fetchone()[0] + 1
        micro_task = json.loads(run["micro_task_json"])
        if run["continuation_count"] and not run["buddy_locked"]:
            micro_task = {key: value for key, value in micro_task.items() if key not in schemas.BUDDY_FIELDS}
        response = self.board.decisions.route_workflow(
            connection, run_id=run_id, sequence=sequence, spec=micro_task,
            manifest=json.loads(run["workspace_manifest_json"]),
        )
        decision_id = response["decisionId"]
        connection.execute(
            "INSERT INTO workflow_routes(decision_id,run_id,owner_generation,state,created_at,updated_at)"
            " VALUES(?,?,?,'pending',?,?)", (decision_id, run_id, run["owner_generation"], now, now),
        )
        connection.execute(
            "UPDATE workflow_runs SET current_routing_id=?, buddy_json=NULL,"
            " validated_buddy_revision=NULL WHERE run_id=?",
            (decision_id, run_id),
        )
        connection.execute("UPDATE tasks SET adapter='unresolved', queue_reason='awaiting-model-selection' WHERE task_id=?", (run_id,))
        self.board._append_event(connection, "workflow.routing_requested", task_id=run_id,
                                 payload={"decisionId": decision_id, "routingTaskId": response["runId"],
                                          "ownerGeneration": run["owner_generation"]})
        self.routing_settled(connection, decision_id=decision_id, now=now)

    def routing_dispatched(self, connection, *, decision_id: str, task_id: str, now: str) -> None:
        """Record that one pending route's internal dispatch task moved.

        The route's internal task pointer is the decision request's current task,
        which the dispatch step itself re-points; a pending route only records the
        movement. The micro task keeps waiting for routing — no business Worker
        turn opens here, and a link that does not exist yet (first routing of a
        Micro task) is not fabricated.
        """
        connection.execute(
            "UPDATE workflow_routes SET updated_at=? WHERE decision_id=? AND state='pending'",
            (now, decision_id),
        )

    def routing_settled(self, connection, *, decision_id: str, now: str) -> None:
        """Adopt a frozen recommendation with its result, under owner/lineage fencing."""
        from ..routing.router import selection_source
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
            buddy = schemas.buddy_constraints(selected)
            constraints = (schemas.buddy_constraints(json.loads(run["micro_task_json"]))
                           if run["buddy_locked"] or not run["continuation_count"] else {})
            if (len(buddy) == len(schemas.BUDDY_FIELDS)
                    and buddy.get("harness") in schemas.CODING_HARNESSES
                    and all(buddy.get(key) == value for key, value in constraints.items())):
                connection.execute("UPDATE workflow_routes SET state='resolved',updated_at=? WHERE decision_id=?",
                                   (now, decision_id))
                connection.execute(
                    "UPDATE workflow_runs SET buddy_json=?, buddy_revision=buddy_revision+1,"
                    " validated_buddy_revision=NULL,updated_at=?,revision=revision+1 WHERE run_id=?",
                    (canonical_json(buddy), now, run["run_id"]),
                )
                connection.execute("UPDATE tasks SET adapter=?,queue_reason='awaiting-buddy-validation' WHERE task_id=?",
                                   (buddy["harness"], run["run_id"]))
                self.board._append_event(connection, "workflow.routing_resolved", task_id=run["run_id"],
                                         payload={"decisionId": decision_id, "buddy": buddy,
                                                  "buddyId": decision["buddy_id"],
                                                  "source": selection_source(json.loads(decision["requested_json"]))})
                return
        own_reason = (make_reason("routing-unusable-selection") if decision["status"] == "completed" else None)
        self._routing_attention(connection, run, now, own_reason)

    def _routing_attention(self, connection, run, now: str, reason: dict | None = None) -> None:
        """A service boundary, with no invented agent turn or completed Micro task."""
        if run["current_routing_id"]:
            connection.execute("UPDATE workflow_routes SET state='needs-host',updated_at=? WHERE decision_id=?",
                               (now, run["current_routing_id"]))
        request_id = f"routing-{uuid.uuid4()}"
        decision_id = run["current_routing_id"]
        payload = {"source": "routing"}
        connection.execute("UPDATE workflow_runs SET state='awaiting-host',active_request_id=?,updated_at=? WHERE run_id=?",
                           (request_id, now, run["run_id"]))
        self._record_system_request(connection, run, reason, payload, now,
                                    request_id=request_id, decision_id=decision_id)
        self.board._append_event(connection, "workflow.routing_needs_host", task_id=run["run_id"],
                                 payload={"requestId": request_id, "decisionId": run["current_routing_id"]})

    @staticmethod
    def _dispatch_identity(run) -> tuple:
        return (run["owner_generation"], run["revision"], run["current_routing_id"],
                run["buddy_revision"], run["buddy_json"])

    def prepare_dispatch(self, params: dict) -> None:
        """Validate each new resolved tuple once, outside write transactions.

        Persist the exact buddy revision under a snapshot fence. Polls do
        not repeat native discovery while waiting for capacity; the adapter's own
        prepare/readback is still the final native check at actual process start.
        """
        selector = params.get("taskId") or params.get("runId")
        with self.db.read() as connection:
            runs = connection.execute(
                "SELECT r.* FROM workflow_runs r JOIN tasks t ON t.task_id=r.run_id"
                " WHERE t.state='queued' AND r.state='executing' AND r.buddy_json IS NOT NULL"
                " AND r.validated_buddy_revision IS NOT r.buddy_revision"
                + (" AND r.run_id=?" if selector else " ORDER BY t.created_at,t.task_id LIMIT 50"),
                (selector,) if selector else (),
            ).fetchall()
        for run in runs:
            buddy = self._buddy(run)
            try:
                self._require_permission(buddy, json.loads(run["execution_workspace_json"])["access"])
                checked = self._validated_buddy(buddy)
                if checked != buddy:
                    raise BoardError("INVALID_BUDDY", "The installed native tuple changed; choose a current buddy")
            except BoardError as error:
                with self.db.write() as connection:
                    current = self._run_row(connection, run["run_id"])
                    task = connection.execute("SELECT * FROM tasks WHERE task_id=?", (run["run_id"],)).fetchone()
                    if (current["state"] == "executing" and task["state"] == "queued"
                            and self._dispatch_identity(current) == self._dispatch_identity(run)):
                        facts = {key: error.details[key] for key in (
                            "buddy", "enabled", "catalogReadAt", "legalEfforts", "access",
                            "requiredCapability", "missingCapabilities", "adapter",
                        ) if key in error.details}
                        if error.code in ("HARNESS_UNAVAILABLE", "UNSUPPORTED_CAPABILITY"):
                            facts["harness"] = buddy["harness"]
                        rejection = make_reason("dispatch.buddy-rejected", errorCode=error.code)
                        rejection["params"].update(facts)
                        if error.code in ("BUDDY_UNAVAILABLE", "CATALOG_UNAVAILABLE", "HARNESS_UNAVAILABLE"):
                            cause = error.details.get("reason")
                            if cause is not None:
                                rejection["params"]["cause"] = cause
                        self._routing_attention(connection, current, self.now(), rejection)
                    head = self.board._head_of(connection)
                self.board._notify(head)
            else:
                with self.db.write() as connection:
                    current = self._run_row(connection, run["run_id"])
                    if (current["state"] == "executing" and self._dispatch_identity(current) == self._dispatch_identity(run)
                            and current["validated_buddy_revision"] != current["buddy_revision"]):
                        connection.execute("UPDATE workflow_runs SET validated_buddy_revision=buddy_revision WHERE run_id=?", (run["run_id"],))
                        self.board._append_event(connection, "workflow.buddy_validated", task_id=run["run_id"],
                                                 payload={"buddyRevision": run["buddy_revision"],
                                                          "buddy": buddy})
                    head = self.board._head_of(connection)
                self.board._notify(head)

    def attach_micro_task(
        self,
        connection,
        task_row,
        *,
        hostId: str,
        spec: dict,
        executionWorkspace: dict,
        manifest: dict,
        microTaskFingerprint: str,
        requestFingerprint: str,
        buddy: dict | None = None,
        submissionToken: str | None = None,
        presentation: dict | None = None,
        buddyLocked: bool = False,
        delegationAuthority: bool = False,
        lead: dict | None = None,
        fixes: str | None = None,
        now: str,
    ) -> str:
        """Insert the run, its reservation and its pinned input manifest.

        Runs inside :meth:`hey_my_buddy.blackboard.store.store.BoardStore.task_submit`'s transaction, so the
        task and its governance record are one durable fact.
        """
        run_id = task_row["task_id"]
        access = "read" if executionWorkspace["access"] == "read" else "write"
        from .macro_tasks import attach_macro_task
        presentation = presentation or {}
        if delegationAuthority:
            self._require_enabled_buddy(connection, buddy)
        if lead is not None:
            self._active_credential(connection, lead)
        fixed_run = self._run_row(connection, fixes) if fixes is not None else None
        if fixed_run is not None:
            actor = {"role": "lead", "id": lead["runId"]} if lead else {"role": "host", "id": hostId}
            if self._owner_actor(connection, fixed_run) != actor:
                raise BoardError("FORBIDDEN", "fixes must name a micro task currently owned by the submitting Host or Lead")
            if fixed_run["state"] != "accepted":
                raise BoardError("INVALID_STATE", "fixes must name an accepted micro task", fixes=fixes)
        macro_task_id = (self._run_row(connection, lead["runId"])["macro_task_id"] if lead is not None else
                         attach_macro_task(connection, presentation, spec=spec, host_id=hostId,
                                           manifest=manifest, now=now, fixed_run=fixed_run))
        connection.execute(
            "INSERT INTO workflow_runs(run_id, host_id, owner_generation, control_verifier, micro_task_json,"
            " micro_task_fingerprint, request_fingerprint, submission_verifier, execution_workspace_json,"
            " workspace_manifest_json, workspace_id, workspace_manifest_sha256, state, revision, created_at,"
            " updated_at) VALUES(?,?,1,?,?,?,?,?,?,?,?,?,'executing',1,?,?)",
            (
                run_id,
                hostId,
                self.db.control_token_verifier(self.db.control_token(run_id, 1)),
                canonical_json(spec),
                microTaskFingerprint,
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
        connection.execute("UPDATE workflow_runs SET macro_task_id=?,title=?,buddy_locked=?,delegation_authority=? WHERE run_id=?",
                           (macro_task_id, presentation.get("title"), int(buddyLocked), int(delegationAuthority), run_id))
        if fixes is not None:
            connection.execute(
                "INSERT INTO workflow_fixes(run_id,fixed_run_id,created_at) VALUES(?,?,?)",
                (run_id, fixes, now),
            )
            self.board._append_event(connection, "workflow.fix_submitted", task_id=run_id,
                                     payload={"fixes": fixes})
        if lead is not None:
            connection.execute(
                "INSERT INTO workflow_lead_tasks(owned_run_id,lead_run_id,lead_turn_id,created_at) VALUES(?,?,?,?)",
                (run_id, lead["runId"], lead["turnId"], now),
            )
            self.board._append_event(connection, "workflow.lead_task_created", task_id=run_id,
                                     payload={"leadRunId": lead["runId"], "leadTurnId": lead["turnId"]})
        if delegationAuthority:
            self.board._append_event(connection, "workflow.delegation_authority_granted", task_id=run_id,
                                     payload={"actor": {"role": "host", "id": hostId}, "buddy": buddy})
        if "macroTask" in presentation:
            self.board._append_event(
                connection, "workflow.macro_task_created", task_id=run_id,
                payload={"macroTaskId": macro_task_id,
                         "description": presentation["macroTask"].get("description")},
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
                "INSERT INTO workflow_scope_versions(run_id, scope_version, actor_role, actor_id, note, write_scope_json,"
                " manifest_sha256, stopped_evidence_json, created_at) VALUES(?,1,?,?,?,?,?,?,?)",
                (run_id, "lead" if lead else "host", lead["runId"] if lead else hostId, None, canonical_json(list(manifest.get("writeScope") or [])),
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
        if buddy is None:
            self._start_routing(connection, run_id, now)
        else:
            connection.execute("UPDATE workflow_runs SET buddy_json=?,buddy_revision=1,validated_buddy_revision=1 WHERE run_id=?",
                               (canonical_json(buddy), run_id))
            self.board._append_event(connection, "workflow.buddy_selected", task_id=run_id,
                                     payload={"source": "original-explicit", "buddy": buddy,
                                              "buddyRevision": 1})
        return run_id

    def normalize_submission(self, params: dict) -> dict:
        """Validate the original request before native discovery or workspace work."""
        scope = current_scope()
        lead = scope if scope is not None and scope["kind"] == AGENT_KIND else None
        if lead is not None:
            for field in ("hostId", "owner", "macroTask", "macroTaskId", "macroTaskOf"):
                if field in params:
                    raise BoardError("INVALID_ARGUMENT", f"A Lead submission derives {field} from its execution credential", field=field)
        normalized = schemas.normalize_workflow_submit(params, lead_run_id=lead["runId"] if lead else None)
        if lead is not None and normalized["delegationAuthority"]:
            raise BoardError("FORBIDDEN", "A Lead cannot grant delegationAuthority to an owned micro task")
        return normalized

    def submit(self, params: dict) -> dict:
        return self.submit_normalized(params, self.normalize_submission(params))

    def submit_normalized(self, params: dict, normalized: dict) -> dict:
        """Prepare the workspace, admit one ordinary micro-task task and return control.

        Authority is a private submission capability chosen by the caller before the
        first RPC, never the ``hostId`` label: a client that can read a run can never
        recover its owner capability by guessing attribution. On replay, control is
        returned only for the original owner generation and only to the caller that
        still presents the same submission capability.
        """
        scope = current_scope()
        lead = scope if scope is not None and scope["kind"] == AGENT_KIND else None
        request_id = normalized["requestId"]
        spec = normalized["spec"]
        host_id = normalized["hostId"]
        submission_token = normalized["submissionToken"]
        fingerprint = schemas.spec_fingerprint(spec)
        request_fingerprint = schemas.workflow_request_fingerprint(
            spec, normalized["executionWorkspace"], host_id, presentation=normalized["presentation"],
            buddy_locked=normalized["buddyLocked"], delegation_authority=normalized["delegationAuthority"],
            fixes=normalized["fixes"]
        )

        # Recovery first: an idempotent submit must recover the original prepared
        # snapshot before it inspects a source tree that may have changed since.
        with self.db.read() as connection:
            existing = connection.execute("SELECT * FROM tasks WHERE request_id=?", (request_id,)).fetchone()
            if existing is not None:
                if lead is not None and connection.execute(
                    "SELECT 1 FROM workflow_lead_tasks WHERE owned_run_id=? AND lead_run_id=?",
                    (existing["task_id"], lead["runId"]),
                ).fetchone() is None:
                    raise BoardError("FORBIDDEN", "This requestId belongs to a micro task outside the Lead's ownership")
                run_row = self._run_optional(connection, existing["task_id"])
                if run_row is None:
                    raise BoardError(
                        "CONFLICT",
                        "This requestId belongs to a task that is not a micro-task workflow run; use a new requestId",
                        requestId=request_id,
                        existingTaskId=existing["task_id"],
                    )
                self._require_replayable_submission(connection, run_row, request_id)
                if existing["input_fingerprint"] != fingerprint:
                    raise BoardError(
                        "CONFLICT",
                        "requestId already belongs to a micro-task run with a different specification",
                        requestId=request_id,
                        existingTaskId=existing["task_id"],
                    )
                if run_row["request_fingerprint"] != request_fingerprint:
                    raise BoardError(
                        "CONFLICT",
                        "requestId already belongs to a micro-task run with a different execution workspace or Host",
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

        self._require_permission(schemas.buddy_constraints(spec) or None, normalized["executionWorkspace"]["access"])
        buddy = self._validated_buddy(spec)
        self._cleanup_fence(normalized["executionWorkspace"].get("cwd") or spec.get("cwd"))
        with self._submission_preparation(request_id, normalized["executionWorkspace"]) as prepared:
            manifest = prepared["manifest"]
            try:
                store_params = {
                    "requestId": request_id,
                                        **{key: spec[key] for key in schemas.SUBMIT_FIELDS | {"harness"} if key in spec},
                }
                task_id = str(uuid.uuid4())
                try:
                    response = self.board.task_submit(
                        store_params,
                        task_id=task_id,
                        micro_task={
                            "hostId": host_id,
                            "spec": spec,
                            "executionWorkspace": normalized["executionWorkspace"],
                            "manifest": manifest,
                            "microTaskFingerprint": fingerprint,
                            "requestFingerprint": request_fingerprint,
                            "submissionToken": submission_token,
                            "buddy": buddy,
                            "presentation": normalized["presentation"],
                            "buddyLocked": normalized["buddyLocked"],
                            "delegationAuthority": normalized["delegationAuthority"],
                            "lead": lead,
                            "fixes": normalized["fixes"],
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
                            if run_row is not None:
                                self._require_replayable_submission(connection, run_row, request_id)
                            identical = (
                                task is not None
                                and run_row is not None
                                and task["input_fingerprint"] == fingerprint
                                and run_row["request_fingerprint"] == request_fingerprint
                                and (lead is None or connection.execute(
                                    "SELECT 1 FROM workflow_lead_tasks WHERE owned_run_id=? AND lead_run_id=?",
                                    (run_row["run_id"], lead["runId"]),
                                ).fetchone() is not None)
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
            except Exception as error:
                if prepared["newAllocation"]:
                    self._revoke_submission_allocation(request_id, manifest, error)
                raise

    @contextmanager
    def _submission_preparation(self, request_id, intent, *, shared_locks=None):
        module = workspace_module()
        preparation = getattr(module, "submission_preparation", None)
        if preparation is None:
            # The test workspace stand-in has no physical allocation.
            yield {"manifest": module.prepare(self.board.directory, request_id, intent), "newAllocation": False}
        else:
            with preparation(self.board.directory, request_id, intent, shared_locks=shared_locks) as prepared:
                yield prepared

    def _revoke_submission_allocation(self, request_id, manifest, original_error):
        """Preserve admission failure while revoking this call's unregistered checkout.

        The preparation lock is still held. BEGIN IMMEDIATE fences any database
        registration, including a committed same-request winner, until removal
        finishes; uncertainty retains the allocation and its recovery facts.
        """
        from . import storage
        module = workspace_module()
        facts = {"requestId": request_id, "workspaceId": manifest["workspaceId"],
                 "path": manifest["checkoutRoot"], "manifestSha256": manifest["manifestSha256"],
                 "removed": False}
        try:
            with self.db.write() as connection:
                reasons = storage.allocation_references(connection, manifest, request_id=request_id)
                if reasons:
                    facts["reasons"] = reasons
                else:
                    directory = Path(self.board.directory) / "workspaces" / manifest["workspaceId"]
                    inspected = storage._orphan_workspace(self.board, directory)
                    if inspected["reasons"]:
                        facts["reasons"] = inspected["reasons"]
                        if inspected.get("proofError"):
                            facts["cleanupError"] = inspected["proofError"]
                    else:
                        facts.update(module.cleanup_remove(self.board.directory, manifest))
        except Exception as cleanup_error:
            facts["cleanupError"] = (cleanup_error.payload() if isinstance(cleanup_error, BoardError)
                                     else {"code": type(cleanup_error).__name__, "message": str(cleanup_error)[:2000]})
        if isinstance(original_error, BoardError):
            original_error.details["submissionCleanup"] = facts
        else:
            original_error.add_note("Submission cleanup: " + json.dumps(facts, sort_keys=True))
        try:
            directory = Path(self.board.directory) / "workspaces" / manifest["workspaceId"]
            module._write_once(directory / ("submission-cleanup-" + uuid.uuid4().hex + ".json"), module._json(facts))
        except Exception as record_error:
            original_error.add_note("Submission cleanup receipt failed: " + str(record_error)[:2000])
        return facts

    @staticmethod
    def _require_replayable_submission(connection, run_row, request_id: str) -> None:
        if run_row["macro_task_id"] is not None:
            reject_macro_task(connection, run_row["macro_task_id"])
        else:
            reject_deleting_run(connection, run_row["run_id"])
        if run_row["request_fingerprint"] is None:
            raise BoardError(
                "SUBMISSION_REPLAY_UNAVAILABLE",
                "This submission predates schema 21 and cannot be replayed; use a new requestId",
                requestId=request_id,
                runId=run_row["run_id"],
            )

    def _recovered_control(self, run_row, submission_token: str | None) -> dict | None:
        """Control for an idempotent replay, fenced by the private submission capability.

        A replay never returns a post-takeover generation: after a takeover the new
        owner holds the capability, and the original submission cannot recover it.
        """
        import hmac

        if run_row["host_id"] is None or not submission_token or not run_row["submission_verifier"]:
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
            if run_row["host_id"] is None:
                return {**view, "duplicate": False}
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
                view = {"runId": run_id, "task": self.board._decorate(connection, task)}
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
        from ..routing.router import routing_facts, selection_source
        from ..routing.router_sequence import dispatch
        run_id = run_row["run_id"]
        total = int(
            connection.execute(
                "SELECT COUNT(*) AS count FROM workflow_routes WHERE run_id=?", (run_id,)
            ).fetchone()["count"]
        )
        query = (
            "SELECT r.rowid AS route_rowid, r.decision_id, r.owner_generation, r.state,"
            " r.created_at, d.status AS decision_status, d.reason_code, d.reason_params_json, d.note,"
            " q.task_id AS decision_task_id, q.selected_json, q.requested_json"
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
        entries = []
        for row in rows:
            requested = json.loads(row["requested_json"])
            entries.append(
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
                    "selectedBuddy": json.loads(row["selected_json"]) if row["selected_json"] else None,
                    "source": selection_source(requested),
                    "reason": reason_from_json(row["reason_code"], row["reason_params_json"]),
                    "note": row["note"],
                    "createdAt": row["created_at"],
                    "ownerGeneration": int(row["owner_generation"]),
                    "current": bool(current_id) and row["decision_id"] == current_id,
                    **routing_facts(requested, actor=dispatch(connection, row["decision_task_id"]) if row["decision_task_id"] else None),
                }
            )
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
            "SELECT t.*,a.token_usage_json,a.adapter AS execution_adapter FROM workflow_turns t LEFT JOIN attempts a ON a.attempt_id=t.attempt_id WHERE t.run_id=? ORDER BY t.turn_index", (run_row["run_id"],)
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
        conclusions = connection.execute("SELECT * FROM workflow_host_conclusions WHERE run_id=? ORDER BY rowid DESC LIMIT 51", (run_row["run_id"],)).fetchall()
        return {
            "hostConclusions": [host_conclusions.view(row) for row in conclusions[:50]],
            "hostConclusionsTruncated": len(conclusions) > 50,
            "turns": [self._turn_view(row, include_audit=True) for row in turns],
            "requests": [self._request_view(row, include_audit=True, connection=connection) for row in requests],
            "continuations": [
                {
                    "continuationId": row["continuation_id"],
                    "kind": row["kind"],
                    "state": row["state"],
                    "requestId": row["request_id"],
                    "note": row["note"],
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
            "microTaskFingerprint": run_row["micro_task_fingerprint"],
            "microTask": json.loads(run_row["micro_task_json"]),
            "spec": _original_spec(connection, run_row["run_id"]),
        }

    # -- decide --------------------------------------------------------------
    def normalize_decision(self, params: dict, *, console_authority: dict | None = None) -> dict:
        schemas.reject_unknown(
            params,
            {
                "runId",
                "requestId",
                "commandId",
                "expectedRevision",
                "decision",
                "note",
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
        note = (schemas.bounded_text(params, "note", max_bytes=schemas.MAX_NOTE_BYTES)[0]
                if params.get("note") is not None else None)
        auto_continue = schemas.optional_bool(params, "autoContinue", True)
        with self.db.read() as connection:
            receipt_entry = self.board._receipt_entry(connection, command_id, "workflow.decide", None, deleted_only=True)
            if receipt_entry is not None:
                return {"deletedReceipt": self.board._receipt_response(connection, receipt_entry), "helpers": []}
            owner = self._run_row(connection, run_id)
            self._authorize(connection, owner, params, console_authority=console_authority, action="A Host decision")
            integrator = self._owner_actor(connection, owner)
        helpers = schemas.normalize_helpers(params, integrator=integrator)
        return {"runId": run_id, "requestId": request_id, "commandId": command_id,
                "expectedRevision": expected, "decision": decision, "note": note,
                "autoContinue": auto_continue, "helpers": helpers}

    def decide(self, params: dict, *, console_authority: dict | None = None) -> dict:
        normalized = self.normalize_decision(params, console_authority=console_authority)
        return self.decide_normalized(params, normalized, console_authority=console_authority)

    def decide_normalized(self, params: dict, normalized: dict, *, console_authority: dict | None = None) -> dict:
        if "deletedReceipt" in normalized:
            return normalized["deletedReceipt"]
        run_id, request_id, command_id = (normalized[key] for key in ("runId", "requestId", "commandId"))
        expected, decision, note = (normalized[key] for key in ("expectedRevision", "decision", "note"))
        auto_continue, helpers = normalized["autoContinue"], normalized["helpers"]
        request_key = {
            "runId": run_id,
            "requestId": request_id,
            "decision": decision,
            "note": note,
            "autoContinue": auto_continue,
            "expectedRevision": expected,
            "helpers": [
                {
                    "requestId": item["requestId"],
                    "spec": item["spec"],
                    "executionWorkspace": item["executionWorkspace"],
                    "integrator": item["integrator"],
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

        # Keep every prepared allocation locked until the helper admission
        # transaction commits. Shared physical checkouts reuse the same lock.
        with self._helper_preparations(helpers if decision == "approve" else []) as prepared:
            now = self.now()
            with self.db.write() as connection:
                receipt_entry = self.board._receipt_entry(connection, command_id, "workflow.decide", request_key)
                if receipt_entry is not None and receipt_entry["deletion_id"] is not None:
                    return self.board._receipt_response(connection, receipt_entry)
                run_row = self._run_row(connection, run_id)
                task = connection.execute("SELECT * FROM tasks WHERE task_id=?", (run_id,)).fetchone()
                actor, _initiator = self._authorize(connection, run_row, params, console_authority=console_authority, action="A Host decision")
                receipt = self.board._receipt_response(connection, receipt_entry)
                if receipt is not None:
                    return {**receipt, "duplicate": True}
                self._expect_revision(run_row, expected)
                request_row = self._request_row(connection, run_id, request_id)
                if json.loads(request_row["payload_json"]).get("source") == "routing":
                    raise BoardError(
                        "BUDDY_REQUIRED",
                        "Resolve this routing boundary with a complete buddy buddy or explicit reroute on the same micro task; only changing shared Router settings needs the user",
                        routingBoundary=self._request_view(request_row, connection=connection).get("routingBoundary"),
                    )
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
                        note=note,
                        actor=actor,
                        command_id=command_id,
                        now=now,
                    )
                elif decision == "approve":
                    children = self._create_helpers(connection, run_row, task, request_row, prepared, command_id, now,
                                                    source_host_id=run_row["host_id"], actor=actor)
                    connection.execute(
                        "UPDATE workflow_requests SET state='approved', decision_json=?, decision_command_id=?,"
                        " decided_at=?, updated_at=? WHERE request_id=?",
                        (
                            canonical_json({"decision": "approve", "note": note, "actor": actor, "helpers": children}),
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
                            note or "Host approved assistance; continue the original micro task with the helper results"
                        )
                        connection.execute(
                            "INSERT INTO workflow_continuations(continuation_id, run_id, command_id,"
                            " kind, request_id, expected_revision, input_text, input_bytes, note, helper_policy,"
                            " helper_outcomes_json, state, created_at) VALUES(?,?,?,'helper',?,?,?,?,?, 'keep','[]','recorded',?)",
                            (
                                approve_continuation,
                                run_id,
                                command_id,
                                request_id,
                                run_row["revision"],
                                approval_input,
                                len(approval_input.encode()),
                                note or None,
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
                            canonical_json({"decision": "decline", "note": note, "actor": actor}),
                            command_id,
                            now,
                            now,
                            request_id,
                        ),
                    )
                    if auto_continue:
                        input_text = note or "Host declined the assistance request; continue with the current evidence"
                        decline_continuation = str(uuid.uuid4())
                        connection.execute(
                            "INSERT INTO workflow_continuations(continuation_id, run_id, command_id,"
                            " kind, request_id, expected_revision, input_text, input_bytes, note, helper_policy,"
                            " helper_outcomes_json, state, created_at) VALUES(?,?,?,'helper',?,?,?,?,?, 'keep','[]','recorded',?)",
                            (
                                decline_continuation,
                                run_id,
                                command_id,
                                request_id,
                                run_row["revision"],
                                input_text,
                                len(input_text.encode()),
                                note or None,
                                now,
                            ),
                        )
                        self._requeue(
                            connection,
                            task,
                            run_row,
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
                        payload={"requestId": request_id, "actor": actor, "autoContinue": auto_continue, "note": note},
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

    @contextmanager
    def _helper_preparations(self, helpers):
        prepared = []
        with ExitStack() as locks, ExitStack() as preparations:
            shared_locks = (locks, set())
            try:
                for helper in helpers:
                    self._require_permission(schemas.buddy_constraints(helper["spec"]) or None, helper["executionWorkspace"]["access"])
                    buddy = self._validated_buddy(helper["spec"])
                    self._cleanup_fence(helper["executionWorkspace"].get("cwd") or helper["spec"].get("cwd"))
                    allocation = preparations.enter_context(self._submission_preparation(
                        helper["requestId"], helper["executionWorkspace"], shared_locks=shared_locks))
                    prepared.append({**helper, "manifest": allocation["manifest"],
                                     "newAllocation": allocation["newAllocation"],
                                     "buddy": buddy})
                yield prepared
            except Exception as error:
                cleanups = []
                for helper in prepared:
                    if helper["newAllocation"]:
                        cleanups.append(self._revoke_submission_allocation(helper["requestId"], helper["manifest"], error))
                if cleanups and isinstance(error, BoardError):
                    error.details["helperPreparationCleanup"] = cleanups
                raise

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
            receipt_entry = self.board._receipt_entry(connection, command_id, "workflow.decide", request_key)
            if receipt_entry is not None and receipt_entry["deletion_id"] is not None:
                return self.board._receipt_response(connection, receipt_entry)
            run_row = self._run_row(connection, run_id)
            # Authority is checked before a replay is returned; a delayed old owner
            # can never read back another Host's recorded response.
            self._authorize(
                connection, run_row, params, console_authority=console_authority, action="A Host decision"
            )
            receipt = self.board._receipt_response(connection, receipt_entry)
            if receipt is not None:
                return {**receipt, "duplicate": True}
            self._expect_revision(run_row, expected)
            request_row = self._request_row(connection, run_id, request_id)
            active = self._open_boundary(connection, run_row)
            if json.loads(request_row["payload_json"]).get("source") == "routing":
                raise BoardError(
                    "BUDDY_REQUIRED",
                    "Resolve this routing boundary with a complete buddy buddy or explicit reroute on the same micro task; only changing shared Router settings needs the user",
                    routingBoundary=self._request_view(request_row, connection=connection).get("routingBoundary"),
                )
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
        if run_row["state"] in ("cancelled", "accepted", "concluded"):
            return None
        return connection.execute(
            "SELECT * FROM workflow_requests WHERE run_id=? AND state='open'"
            " ORDER BY CASE WHEN request_id=? THEN 0 ELSE 1 END, created_at, request_id LIMIT 1",
            (run_row["run_id"], run_row["active_request_id"]),
        ).fetchone()

    def _sync_boundary(self, connection, run_id, now, *, default_state=None):
        run = self._run_row(connection, run_id)
        if run["state"] in ("cancelled", "accepted", "concluded") or self._terminal_ancestor(connection, run_id) is not None:
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
            if parent["state"] in ("cancelled", "accepted", "concluded") or self._terminal_ancestor(connection, parent["run_id"]) is not None:
                return
            payload = json.loads(source["payload_json"])
            payload["origin"] = payload.get("origin") or {"runId": source["run_id"], "requestId": source["request_id"]}
            payload["proxy"] = {"runId": source["run_id"], "requestId": source["request_id"]}
            request_id = self._proxy_id(parent["run_id"], source["request_id"])
            connection.execute(
                "INSERT OR IGNORE INTO workflow_requests(request_id,run_id,turn_id,attempt_id,child_task_id,kind,summary,"
                "reason_code,reason_params_json,decision_id,payload_json,state,expected_revision,created_at,updated_at)"
                " VALUES(?,?,?,?,?,'helper-attention',?,?,?,?,?,'open',?,?,?)",
                (request_id, parent["run_id"], source["turn_id"], source["attempt_id"], source["run_id"], source["summary"],
                 source["reason_code"], source["reason_params_json"], source["decision_id"], canonical_json(payload),
                 parent["revision"], now, now),
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
            if request["state"] != "open" or run["state"] in ("cancelled", "accepted", "concluded"):
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
        for child in self._owned_children(connection, run_id, include_lead=False):
            run = self._run_row(connection, child["child_task_id"])
            task = connection.execute("SELECT * FROM tasks WHERE task_id=?", (child["child_task_id"],)).fetchone()
            if child["state"] in ("active", "attention") or run["state"] in ("executing", "awaiting-host", "waiting-helpers") or not self._stop_proven(connection, task):
                return True
        return False

    def _resolve_helper_attention(
        self, connection, parent_run, request_row, prepared: list[dict], *,
        decision: str, note: str | None, actor: dict, command_id: str, now: str,
    ) -> None:
        """Answer one exact proxy chain and resume its requesting leaf only."""
        chain = self._attention_chain(connection, parent_run, request_row)
        child_run, source_request = chain[-1]
        child_task_id = child_run["run_id"]
        child_task = connection.execute("SELECT * FROM tasks WHERE task_id=?", (child_task_id,)).fetchone()
        input_text = note or (
            "The Host approved the requested assistance; continue with this guidance"
            if decision == "approve" else "The Host declined the requested assistance; continue with what you have"
        )
        decision_record = canonical_json({"decision": decision, "note": note, "actor": actor,
                                          "parentRunId": parent_run["run_id"], "targetRunId": child_task_id})
        for _, boundary in chain:
            connection.execute(
                "UPDATE workflow_requests SET state=?, decision_json=?, decision_command_id=?, decided_at=?, updated_at=?"
                " WHERE request_id=? AND state='open'",
                ("approved" if decision == "approve" else "declined", decision_record, command_id, now, now, boundary["request_id"]),
            )
        continuation_id = str(uuid.uuid4())
        connection.execute(
            "INSERT INTO workflow_continuations(continuation_id,run_id,command_id,kind,request_id,"
            "expected_revision,input_text,input_bytes,note,helper_policy,helper_outcomes_json,state,created_at)"
            " VALUES(?,?,?,'helper',?,?,?,?,?,'keep','[]','recorded',?)",
            (continuation_id, child_task_id, command_id, source_request["request_id"], child_run["revision"],
             input_text, len(input_text.encode()), note or None, now),
        )
        extra = self._create_helpers(connection, child_run, child_task, source_request, prepared, command_id, now,
                                     source_host_id=parent_run["host_id"], actor=actor) if decision == "approve" and prepared else []
        pending = self._open_boundary(connection, child_run)
        if extra or pending is not None or self._unfinished_helpers(connection, child_task_id):
            self._sync_boundary(connection, child_task_id, now, default_state="waiting-helpers")
        else:
            self._requeue(connection, child_task, child_run, actor=actor, now=now,
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
        *, source_host_id: str | None, actor: dict,
    ) -> list[dict]:
        task_spec = json.loads(parent_task["spec_json"])
        children: list[dict] = []
        for item in prepared:
            helper_task_id = str(uuid.uuid4())
            manifest = item["manifest"]
            access = "read" if item["executionWorkspace"]["access"] == "read" else "write"
            transfer_from = None
            if access == "write" and manifest.get("checkoutId") == json.loads(
                    run_row["workspace_manifest_json"] or "{}").get("checkoutId"):
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
            self._admit_helper_task(
                connection,
                helper_task_id=helper_task_id,
                item=item,
                parent_task=parent_task,
                task_spec=task_spec,
                run_row=run_row,
                manifest=manifest,
                now=now,
                source_host_id=source_host_id,
                actor=actor,
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
            if item["buddy"] is None:
                self._start_routing(connection, helper_task_id, now)
            else:
                connection.execute("UPDATE workflow_runs SET buddy_json=?,buddy_revision=1,validated_buddy_revision=1 WHERE run_id=?",
                                   (canonical_json(item["buddy"]), helper_task_id))
                self.board._append_event(connection, "workflow.buddy_selected", task_id=helper_task_id,
                                         payload={"source": "original-explicit", "buddy": item["buddy"],
                                                  "buddyRevision": 1})
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
        self, connection, *, helper_task_id, item, parent_task, task_spec, run_row, manifest, now, source_host_id, actor
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
            " fingerprint_version, adapter, required_capabilities, cwd, exclusive_resources, expected_seconds,"
            " state, queue_reason, revision, created_at, updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,1,?,?)",
            (
                helper_task_id,
                request_id,
                None,
                canonical_json(spec),
                canonical_json(spec),
                fingerprint,
                schemas.FINGERPRINT_VERSION_CURRENT,
                spec.get("harness") or "unresolved",
                canonical_json(spec.get("requiredCapabilities", [])),
                effective_cwd,
                canonical_json(spec.get("exclusiveResources", [])),
                spec["expectedSeconds"],
                "queued",
                blocker or "awaiting-execution",
                now,
                now,
            ),
        )
        # A helper run uses the ordinary turn machinery: its micro task is the helper spec,
        # its Host is the parent's Host and it can never be reached by a Host control
        # capability of its own (only the parent run is addressed by workflow ops).
        connection.execute(
            "INSERT INTO workflow_runs(run_id, host_id, owner_generation, control_verifier, micro_task_json,"
            " micro_task_fingerprint, request_fingerprint, execution_workspace_json, workspace_manifest_json, workspace_id,"
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
        # A helper is a current-contract run too: its own scope version 1 is recorded
        # with its admission so no creation path can exist without a real scope row.
        connection.execute("UPDATE workflow_runs SET macro_task_id=? WHERE run_id=?", (run_row["macro_task_id"], helper_task_id))
        connection.execute(
            "INSERT INTO workflow_scope_versions(run_id, scope_version, actor_role, actor_id, note, write_scope_json,"
            " manifest_sha256, stopped_evidence_json, created_at) VALUES(?,1,?,?,?,?,?,?,?)",
            (helper_task_id, actor["role"], actor["id"], None,
             canonical_json(list(manifest.get("writeScope") or [])), manifest.get("manifestSha256"),
             canonical_json({"kind": "helper", "parentRunId": run_row["run_id"]}), now),
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
    def _target_run(self, connection, owner, target_id: str, *, include_lead: bool = False):
        """Resolve the run one Host control command acts on.

        ``runId`` always names the controlling root: only its control capability
        authenticates the caller and only its ``expectedRevision`` fences the
        command. ``targetRunId`` may name that same run or an owned descendant
        (a helper). The descent is re-walked on every call, so a sibling, a
        foreign run, or a target that has since been detached from this Micro task is
        refused instead of acted on. Ownership is the ``workflow_children`` link,
        not the helper's admission-time host label: a root takeover fences the old
        capability while the current owner can still act on the same helper. No
        helper control token is ever needed or exposed.
        """
        if target_id != owner["run_id"] and target_id not in {
                child["child_task_id"] for child in self._owned_children(connection, owner["run_id"], include_lead=include_lead)}:
            raise BoardError("UNAUTHORIZED", "targetRunId must be an authorized descendant of this owning Micro task")
        return self._run_row(connection, target_id)

    def _continuation_target(self, connection, owner, target_id: str):
        return self._target_run(connection, owner, target_id)

    @staticmethod
    def _buddy_matches_micro_task(run, buddy: dict | None) -> None:
        if buddy is None or not run["buddy_locked"]:
            return
        constraints = schemas.buddy_constraints(json.loads(run["micro_task_json"]))
        mismatches = [key for key, value in constraints.items() if buddy.get(key) != value]
        if mismatches:
            raise BoardError("BUDDY_CONFLICT", "buddy must preserve the original Micro task's hard constraints; submit a new Micro task to change them",
                             fields=mismatches)

    @staticmethod
    def _require_enabled_buddy(connection, buddy):
        """Recheck current availability and the user's independent enabled gate.

        ADR-027: catalog facts — including the one bounded re-read — were already
        applied by ``_validated_buddy`` outside the write transaction.
        A concurrent confirmed absence must still fence the write, without any
        native call here. Neither a refresh nor the override flips enablement.
        """
        if buddy is None:
            return
        row = connection.execute(
            "SELECT enabled,available FROM buddies WHERE harness=? AND provider=? AND model=? AND effort=?",
            tuple(buddy[key] for key in schemas.BUDDY_FIELDS),
        ).fetchone()
        from ..catalog.catalog import catalog_read_at

        read_at = catalog_read_at(connection, buddy["harness"])
        if row is not None and not row["available"]:
            details = {"buddy": buddy, "reason": make_reason("catalog-unavailable")}
            if read_at:
                details["catalogReadAt"] = read_at
            raise BoardError("BUDDY_UNAVAILABLE", "The requested native model route is no longer available", **details)
        if row is not None and row["enabled"]:
            return
        details = {
            "buddy": buddy,
            "enabled": False,
            "reason": make_reason("not-enabled"),
        }
        if read_at:
            details["catalogReadAt"] = read_at
        raise BoardError("BUDDY_UNAVAILABLE",
                         "The Host must name a buddy the user enabled; the catalog route itself is admitted",
                         **details)

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
                "note",
                "buddy",
                "reroute",
                "access",
                "accessNote",
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
        note = (schemas.bounded_text(params, "note", max_bytes=schemas.MAX_NOTE_BYTES)[0]
                if params.get("note") is not None else None)
        reroute = schemas.optional_bool(params, "reroute", False)
        access_selection = schemas.normalize_access(params) if "access" in params or "accessNote" in params else None
        supplied_buddy = schemas.normalize_buddy(params["buddy"]) if "buddy" in params else None
        if supplied_buddy is not None and reroute:
            raise BoardError("INVALID_ARGUMENT", "buddy and reroute are mutually exclusive")
        if supplied_buddy is not None and not note:
            raise BoardError("INVALID_ARGUMENT", "a Host buddy override requires an explicit note")
        request_key = {
            "runId": run_id,
            "targetRunId": target_id,
            "expectedRevision": expected,
            "inputSha256": sha256_text(input_text),
            "helperPolicy": policy,
            "buddy": supplied_buddy,
            "note": note,
            "reroute": reroute,
            **({"access": access_selection[0], "accessNote": access_selection[1]} if access_selection else {}),
        }
        # Authenticate/replay before native catalog work. A concurrent takeover is
        # checked again after validation, before any durable mutation.
        with self.db.read() as connection:
            receipt_entry = self.board._receipt_entry(connection, command_id, "workflow.continue", request_key)
            if receipt_entry is not None and receipt_entry["deletion_id"] is not None:
                return self.board._receipt_response(connection, receipt_entry)
            run = self._run_row(connection, run_id)
            self._authorize(connection, run, params, console_authority=console_authority, action="A continuation")
            receipt = self.board._receipt_response(connection, receipt_entry)
            if receipt is not None:
                return {**receipt, "duplicate": True}
            self._expect_revision(run, expected)
            target_snapshot = self._continuation_target(connection, run, target_id)
            self._buddy_matches_micro_task(target_snapshot, supplied_buddy)
            current_choice = json.loads(target_snapshot["execution_workspace_json"])
            if target_snapshot["delegation_authority"]:
                if reroute:
                    raise BoardError("INVALID_ARGUMENT", "delegationAuthority requires an explicit buddy; a Lead cannot be routed")
                if access_selection is not None and access_selection[0] != "full":
                    raise BoardError("INVALID_ARGUMENT", "delegationAuthority requires access='full'", field="access")
            self._require_permission(supplied_buddy if supplied_buddy is not None else
                                     (None if reroute else self._buddy(target_snapshot)),
                                     access_selection[0] if access_selection else current_choice["access"])
        # ADR-027 rule 3: an explicit override meets the same catalog facts as a
        # submission — one bounded re-read of its harness before any catalog
        # rejection — and only afterwards the user's independent enabled gate,
        # which is re-checked inside the write transaction below.
        buddy = self._validated_buddy(supplied_buddy) if supplied_buddy is not None else None
        now = self.now()
        with self.db.write() as connection:
            receipt_entry = self.board._receipt_entry(connection, command_id, "workflow.continue", request_key)
            if receipt_entry is not None and receipt_entry["deletion_id"] is not None:
                return self.board._receipt_response(connection, receipt_entry)
            owner = self._run_row(connection, owner_run_id)
            actor, initiator = self._authorize(connection, owner, params, console_authority=console_authority, action="A continuation")
            receipt = self.board._receipt_response(connection, receipt_entry)
            if receipt is not None:
                return {**receipt, "duplicate": True}
            reject_deleting_run(connection, owner_run_id)
            reject_deleting_run(connection, target_id)
            self._expect_revision(owner, expected)
            run_row = self._continuation_target(connection, owner, target_id)
            self._expect_revision(run_row, target_snapshot["revision"])
            self._buddy_matches_micro_task(run_row, buddy)
            self._require_enabled_buddy(connection, buddy)
            current_workspace = json.loads(run_row["execution_workspace_json"])
            next_access, next_note = access_selection or (current_workspace["access"], current_workspace.get("accessNote"))
            run_id = run_row["run_id"]
            task = connection.execute("SELECT * FROM tasks WHERE task_id=?", (run_id,)).fetchone()
            self._assert_lineage_open(connection, run_id)
            if run_row["state"] in ("accepted", "concluded"):
                raise BoardError("CONFLICT", "A finished micro task cannot be continued")
            cleanup = connection.execute("SELECT state FROM workspace_cleanup_plans WHERE run_id=? AND state IN ('applying','applied') LIMIT 1", (run_id,)).fetchone()
            if cleanup is not None:
                raise BoardError("CONFLICT", "This micro task's checkout is being cleaned or has been removed")
            self._require_routing_stopped(connection, run_id)
            routing = self._routing_view(connection, run_row)
            if buddy is None and not reroute and (
                    self._buddy(run_row) is None or routing["status"] in ("needs-host", "fenced")):
                raise BoardError(
                    "BUDDY_REQUIRED",
                    "Continue the same micro task with a complete buddy buddy, or explicitly reroute at the boundary's retry time; only changing shared Router settings needs the user",
                    routingBoundary=routing.get("routingBoundary"),
                )
            live_helpers = sum(
                child["state"] in ("active", "attention")
                or not self._stop_proven(connection, connection.execute("SELECT * FROM tasks WHERE task_id=?", (child["child_task_id"],)).fetchone())
                for child in self._owned_children(connection, run_id, include_lead=False)
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
            continuation_id = str(uuid.uuid4())
            if policy == "cancel":
                self._cancel_children(connection, run_id, note, now,
                                      actor=actor, initiator=initiator, include_lead=False,
                                      stop_facts={"continuationId": continuation_id})
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
            connection.execute(
                "INSERT INTO workflow_continuations(continuation_id, run_id, command_id,"
                " kind, expected_revision, input_text, input_bytes, note, helper_policy, helper_outcomes_json, state,"
                " created_at) VALUES(?,?,?,'manual',?,?,?,?,?,?, 'queued', ?)",
                (
                    continuation_id,
                    run_id,
                    command_id,
                    run_row["revision"],
                    input_text,
                    input_bytes,
                    note or None,
                    policy,
                    canonical_json(helper_outcomes),
                    now,
                ),
            )
            self._requeue(
                connection,
                task,
                run_row,
                actor=actor,
                now=now,
                continuation_id=continuation_id,
            )
            if run_row["state"] == "delivered" and run_row["final_artifact_id"]:
                # Ending a delivered outcome without acceptance records the negative
                # review and the Host's opinion next to the immutable artifact; the
                # artifact itself and every earlier review stay in history.
                rejection_note = note
                connection.execute(
                    "UPDATE tasks SET review_rejected_at=?, review_rejection_note=?,"
                    " updated_at=? WHERE task_id=?",
                    (now, rejection_note, now, run_id),
                )
                self.board._append_event(
                    connection, "workflow.review_rejected", task_id=run_id,
                    attempt_id=run_row["final_attempt_id"], revision=run_row["revision"] + 1,
                    payload={"artifactId": run_row["final_artifact_id"], "verdict": "rejected",
                             "actor": actor, "note": note, "opinion": _head(input_text, 512)},
                )
            if access_selection is not None:
                updated_workspace = {**current_workspace, "access": next_access}
                updated_workspace.pop("accessNote", None)
                if next_note is not None:
                    updated_workspace["accessNote"] = next_note
                connection.execute("UPDATE workflow_runs SET execution_workspace_json=? WHERE run_id=?",
                                   (canonical_json(updated_workspace), run_id))
                if (current_workspace["access"], current_workspace.get("accessNote")) != (next_access, next_note):
                    self.board._append_event(
                        connection, "workflow.access_changed", task_id=run_id,
                        revision=run_row["revision"] + 1,
                        payload={"previousAccess": current_workspace["access"], "access": next_access,
                                 "accessNote": next_note, "actor": actor, "continuationId": continuation_id})
            if buddy is not None:
                connection.execute("UPDATE workflow_runs SET buddy_json=?,current_routing_id=NULL,"
                                   "buddy_revision=buddy_revision+1,"
                                   "validated_buddy_revision=buddy_revision+1 WHERE run_id=?",
                                   (canonical_json(buddy), run_id))
                connection.execute("UPDATE tasks SET adapter=? WHERE task_id=?", (buddy["harness"], run_id))
                updated_run = self._run_row(connection, run_id)
                self.board._append_event(
                    connection, "workflow.buddy_overridden", task_id=run_id,
                    payload={"source": "host-override", "actor": actor, "note": note,
                             "buddy": buddy,
                             "buddyRevision": updated_run["buddy_revision"]},
                )
            elif reroute:
                # The durable continuation event below records the intent. No Router
                # is allowed to inspect the previous turn's input while preparation waits.
                connection.execute("UPDATE workflow_runs SET buddy_json=NULL,"
                                   " current_routing_id=NULL,validated_buddy_revision=NULL WHERE run_id=?", (run_id,))
                connection.execute("UPDATE tasks SET adapter='unresolved',queue_reason='awaiting-workspace-preparation'"
                                   " WHERE task_id=?", (run_id,))
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
                    "kind": "manual",
                    "reroute": reroute,
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
                "SELECT * FROM workflow_artifacts WHERE run_id=? AND attempt_id=? AND kind IN ('output','resolved-output','partial-output')"
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
                if artifact["kind"] == "partial-output":
                    entry["artifact"].update(kind="partial-output", partial=True, verified=False, final=False)
            outcomes.append(entry)
        return outcomes

    def _requeue(
        self,
        connection,
        task,
        run_row,
        *,
        actor: dict,
        now: str,
        continuation_id: str | None = None,
    ) -> None:
        self._assert_lineage_open(connection, run_row["run_id"])
        connection.execute("UPDATE workspace_cleanup_plans SET state='blocked', reasons_json='[\"continued\"]' WHERE run_id=? AND state='planned'", (run_row["run_id"],))
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
        if continuation is not None and continuation["kind"] == "manual":
            self._reclaim_manual_workspace(connection, task, run_row, now)
        blocker = self.board._admission_blocker(connection, spec, exclude_task=task["task_id"])
        if task["review_rejected_at"]:
            self.board._append_event(
                connection, "task.review_archived", task_id=task["task_id"],
                attempt_id=attempt["attempt_id"] if attempt else None,
                revision=task["revision"] + 1,
                payload={"reviewRejectedAt": task["review_rejected_at"], "verdict": "rejected",
                         "note": task["review_rejection_note"]},
            )
            connection.execute(
                "UPDATE tasks SET review_rejected_at=NULL, review_rejection_note=NULL WHERE task_id=?",
                (task["task_id"],),
            )
        if task["state"] == "completed":
            # A rejected final review reopens the micro-task logical task for another
            # turn. This is not an execution retry of completed work
            # (which stays refused); only a micro-task run at a Host boundary may do it.
            if run_row["state"] not in ("awaiting-host", "delivered", "waiting-owned"):
                raise BoardError(
                    "CONFLICT",
                    "A completed micro-task task can only be reopened from a Host boundary",
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
            (blocker or "awaiting-execution", now, task["task_id"]),
        )
        updated = connection.execute(
            "UPDATE workflow_runs SET state='executing', active_request_id=NULL, final_artifact_id=NULL, final_attempt_id=NULL,"
            " continuation_count=continuation_count+1, updated_at=?, revision=revision+1"
            " WHERE run_id=? AND revision=?",
            (now, run_row["run_id"], run_row["revision"]),
        )
        if updated.rowcount != 1:
            raise BoardError("REVISION_CONFLICT", "The micro-task run changed concurrently; re-read it and retry")
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
            receipt_entry = self.board._receipt_entry(connection, command_id, "workflow.takeover", request_key)
            if receipt_entry is not None and receipt_entry["deletion_id"] is not None:
                return self.board._receipt_response(connection, receipt_entry)
            run_row = self._run_row(connection, run_id)
            receipt = self.board._receipt_response(connection, receipt_entry)
            if receipt is not None:
                # A lost reply must be recoverable by the caller that authorized the
                # original takeover: authenticate that receipt's original subject,
                # never the current owner, and never issue a newer generation.
                self._authorize_takeover_replay(
                    connection, run_row, params, console_authority, expected_generation, receipt
                )
                return {**receipt, "duplicate": True}
            actor, _initiator = self._authorize(connection, run_row, params, console_authority=console_authority, action="A takeover")
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
                if self._cancel_routes(connection, owned_id, make_reason("routing-taken-over"), now):
                    owned = self._run_row(connection, owned_id)
                    if owned["state"] == "executing":
                        self._routing_attention(connection, owned, now, make_reason("routing-taken-over"))
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
        if run_row["host_id"] is None:
            raise BoardError("UNAUTHORIZED", "A Lead-owned micro task has no Host control capability")
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
            {"runId", "commandId", "note", *schemas.CONTROL_FIELDS, schemas.CONSOLE_AUTHORITY_FIELD},
            "workflow.cancel",
        )
        schemas.reject_untrusted_override(params)
        run_id = schemas.required_string(params, "runId", max_length=128)
        command_id = schemas.optional_string(params, "commandId", max_length=128)
        note = (schemas.bounded_text(params, "note", max_bytes=schemas.MAX_NOTE_BYTES)[0]
                if params.get("note") is not None else None)
        request_key = {"runId": run_id, "note": note}
        now = self.now()
        with self.db.write() as connection:
            if command_id:
                receipt_entry = self.board._receipt_entry(connection, command_id, "workflow.cancel", request_key)
                if receipt_entry is not None and receipt_entry["deletion_id"] is not None:
                    return self.board._receipt_response(connection, receipt_entry)
            run_row = self._run_row(connection, run_id)
            task = connection.execute("SELECT * FROM tasks WHERE task_id=?", (run_id,)).fetchone()
            actor, initiator = self._authorize(connection, run_row, params, console_authority=console_authority, action="Cancellation")
            if command_id:
                receipt = self.board._receipt_response(connection, receipt_entry)
                if receipt is not None:
                    return {**receipt, "duplicate": True}
            if run_row["state"] in ("accepted", "concluded"):
                raise BoardError("CONFLICT", "This run is already closed; cancellation cannot undo it")
            response = self._cancel_in_transaction(connection, run_row, task, note, actor, initiator, now)
            if command_id:
                self.board._store_receipt(
                    connection, command_id, "workflow.cancel", request_key, response, task_id=run_id
                )
            head = self.board._head_of(connection)
        self.board._notify(head)
        return response

    def stop_macro_task(self, params: dict, *, console_authority: dict | None = None) -> dict:
        """One browser-only cancellation scope, resolved and fenced atomically."""
        schemas.reject_unknown(params, {"macroTaskId", "commandId", "note"}, "macro_task.stop")
        if console_authority is None:
            raise BoardError("UNAUTHORIZED", "Stopping a macro task requires an authenticated console writer")
        identifier = schemas.required_string(params, "macroTaskId", max_length=134)
        command_id = schemas.required_string(params, "commandId", max_length=128)
        note = (schemas.bounded_text(params, "note", max_bytes=schemas.MAX_NOTE_BYTES)[0]
                if params.get("note") is not None else None)
        request = {"macroTaskId": identifier, "note": note}
        now = self.now()
        with self.db.write() as connection:
            receipt_entry = self.board._receipt_entry(connection, command_id, "macro_task.stop", request)
            if receipt_entry is not None and receipt_entry["deletion_id"] is not None:
                return self.board._receipt_response(connection, receipt_entry)
            receipt = self.board._receipt_response(connection, receipt_entry)
            if receipt is not None:
                return {**receipt, "duplicate": True}
            if identifier.startswith("macro-"):
                roots = connection.execute(
                    "SELECT r.* FROM workflow_runs r WHERE r.macro_task_id=?"
                    " AND NOT EXISTS (SELECT 1 FROM workflow_children c WHERE c.child_task_id=r.run_id)"
                    " AND NOT EXISTS (SELECT 1 FROM workflow_lead_tasks l WHERE l.owned_run_id=r.run_id)"
                    " ORDER BY r.created_at,r.run_id", (identifier,),
                ).fetchall()
            elif identifier.startswith("run:"):
                roots = connection.execute(
                    "SELECT r.* FROM workflow_runs r WHERE r.run_id=? AND r.macro_task_id IS NULL"
                    " AND NOT EXISTS (SELECT 1 FROM workflow_children c WHERE c.child_task_id=r.run_id)",
                    (identifier[4:],),
                ).fetchall()
            else:
                raise BoardError("INVALID_ARGUMENT", "macroTaskId must identify a macro task or standalone root")
            if not roots:
                raise BoardError("NOT_FOUND", "Unknown work macro task")
            # Freeze the whole ownership graph before cancellation emits events.
            # Fence active parents first so descendant settlement cannot wake them.
            scope = []
            for root in roots:
                scope.append(root)
                scope.extend(self._run_row(connection, child["child_task_id"])
                             for child in self._owned_children(connection, root["run_id"], include_lead=True))
            actor = {"role": "console", "id": console_authority["sessionId"]}
            for root in roots:
                if root["state"] in IN_PROGRESS_STATES:
                    task = connection.execute("SELECT * FROM tasks WHERE task_id=?", (root["run_id"],)).fetchone()
                    self._cancel_in_transaction(connection, root, task, note, actor, "console", now,
                                                active_only=True)
                else:
                    self._cancel_children(connection, root["run_id"], note, now, actor=actor,
                                          initiator="console", include_lead=True, active_only=True)
            results = []
            for original in scope:
                stopped = original["state"] in IN_PROGRESS_STATES
                current = self._run_row(connection, original["run_id"])
                results.append({"runId": current["run_id"], "state": current["state"], "stopped": stopped})
            response = {"macroTaskId": identifier, "results": results, "duplicate": False}
            self.board._store_receipt(connection, command_id, "macro_task.stop", request, response)
            head = self.board._head_of(connection)
        self.board._notify(head)
        return response

    def cancel_for_service_stop(self, note: str | None) -> tuple[dict[str, int], set[str]]:
        """Internal daemon shutdown path; no request fields or caller authority enter here."""
        with self.db.write() as connection:
            unresolved = self.board.UNRESOLVED_SQL.replace("execution_state", "a.execution_state").replace(
                "result_json", "a.result_json").replace("shutdown_confirmed", "a.shutdown_confirmed")
            candidates: dict[str, tuple[str, bool]] = {}
            for row in connection.execute(
                "SELECT t.task_id,t.state,r.state AS workflow_state,"
                f" EXISTS(SELECT 1 FROM attempts a WHERE a.task_id=t.task_id AND ({unresolved})) AS unresolved"
                " FROM tasks t JOIN workflow_runs r ON r.run_id=t.task_id"
                " WHERE t.state='queued' OR EXISTS(SELECT 1 FROM attempts a"
                f" WHERE a.task_id=t.task_id AND ({unresolved}))"
            ).fetchall():
                if row["unresolved"] or (row["state"] == "queued" and row["workflow_state"] == "executing"):
                    candidates[row["task_id"]] = (row["task_id"], bool(row["unresolved"]))
            for row in connection.execute(
                "SELECT t.task_id,t.state,r.run_id,"
                f" EXISTS(SELECT 1 FROM attempts a WHERE a.task_id=t.task_id AND ({unresolved})) AS unresolved"
                " FROM tasks t JOIN decision_requests d ON d.task_id=t.task_id"
                " JOIN workflow_routes r ON r.decision_id=d.decision_id"
                " WHERE (r.state='pending' AND t.state='queued') OR EXISTS(SELECT 1 FROM attempts a"
                f" WHERE a.task_id=t.task_id AND ({unresolved}))"
            ).fetchall():
                candidates[row["task_id"]] = (row["run_id"], bool(row["unresolved"]))
            candidate_roots: dict[str, str] = {}
            for task_id, (owning_run_id, _unresolved) in candidates.items():
                root_id = owning_run_id
                while True:
                    parent = connection.execute(
                        "SELECT parent_run_id FROM workflow_children WHERE child_task_id=?"
                        " UNION ALL SELECT lead_run_id FROM workflow_lead_tasks WHERE owned_run_id=?", (root_id, root_id)
                    ).fetchone()
                    if parent is None:
                        break
                    root_id = parent["parent_run_id"]
                candidate_roots[task_id] = root_id
            cancelled_roots: set[str] = set()
            now = self.now()
            for root_id in sorted(set(candidate_roots.values())):
                run_row = self._run_row(connection, root_id)
                if run_row["state"] == "cancelled":
                    continue
                if run_row["state"] in ("accepted", "concluded"):
                    raise BoardError("CONFLICT", "Closed Micro task still owns active work during service stop", runId=root_id)
                task = connection.execute("SELECT * FROM tasks WHERE task_id=?", (root_id,)).fetchone()
                self._cancel_in_transaction(connection, run_row, task, note, {"role": "service", "id": "stop"}, "service", now)
                cancelled_roots.add(root_id)
            head = self.board._head_of(connection)
            counts = {"queued": 0, "active": 0}
            for task_id, (_owning_run_id, unresolved_attempt) in candidates.items():
                if candidate_roots[task_id] in cancelled_roots:
                    counts["active" if unresolved_attempt else "queued"] += 1
        if cancelled_roots:
            self.board._notify(head)
        return counts, set(candidates)

    def _cancel_in_transaction(self, connection, run_row, task, note: str | None, actor: dict, initiator: str, now: str, *,
                               active_only: bool = False, stop_facts: dict | None = None) -> dict:
        run_id = run_row["run_id"]
        # Fence the root before descendants settle: a transferred checkout must
        # not be handed back to a micro task cancelled in this transaction.
        self._cancel_owned_run(connection, task, now, explicit=True, actor=actor, initiator=initiator)
        self._cancel_children(connection, run_id, note, now, actor=actor, initiator=initiator, include_lead=True,
                              active_only=active_only, stop_facts=stop_facts)
        self.board._append_event(
            connection, "workflow.cancelled", task_id=run_id, revision=run_row["revision"] + 1,
            payload={"note": note, "actor": actor, "stopInitiator": initiator, "honestShutdown": True, **(stop_facts or {})},
        )
        task = connection.execute("SELECT * FROM tasks WHERE task_id=?", (run_id,)).fetchone()
        child = connection.execute("SELECT * FROM workflow_children WHERE child_task_id=?", (run_id,)).fetchone()
        if child is not None and self._stop_proven(connection, task):
            self.child_settled(connection, task=task, attempt=self.board._selected_attempt(connection, task),
                               payload=None, task_state=task["state"], now=now)
        elif self._stop_proven(connection, task):
            self._release_reservations(connection, run_id, now)
        run_row = self._run_row(connection, run_id)
        return {
            **self.compact(connection, run_row, task), "cancelled": True, "duplicate": False,
        }

    def _cancel_children(self, connection, run_id: str, note: str | None, now: str, *,
                         actor: dict | None = None, initiator: str = "unknown", include_lead: bool = False,
                         active_only: bool = False, stop_facts: dict | None = None) -> list[str]:
        """Fence the whole owned graph, including work behind settled helpers."""
        cancelled: list[str] = []
        rows = self._owned_children(connection, run_id, include_lead=include_lead)
        for child in rows:
            task = connection.execute("SELECT * FROM tasks WHERE task_id=?", (child["child_task_id"],)).fetchone()
            if task is None:
                continue
            if active_only and self._run_row(connection, task["task_id"])["state"] not in IN_PROGRESS_STATES:
                continue
            if self._cancel_owned_run(connection, task, now, actor=actor, initiator=initiator):
                self.board._append_event(
                    connection, "workflow.helper_cancelled" if child["kind"] == "helper" else "workflow.lead_task_cancelled",
                    task_id=task["task_id"], revision=task["revision"] + 1,
                    payload={"parentRunId": child["parent_run_id"], "cancelledByRunId": run_id, "note": note,
                             "stopInitiator": initiator, **(stop_facts or {}), **({"actor": actor} if actor is not None else {})},
                )
                cancelled.append(task["task_id"])
        # Every descendant is fenced before ownership can move upward. Settlement
        # walks through cancelled intermediaries but never reactivates one.
        for child in reversed(rows):
            if active_only and child["child_task_id"] not in cancelled:
                continue
            task = connection.execute("SELECT * FROM tasks WHERE task_id=?", (child["child_task_id"],)).fetchone()
            if child["kind"] == "helper":
                self._settle_helper_reservation(connection, child, task, None, now)
            elif self._stop_proven(connection, task):
                self._release_reservations(connection, child["child_task_id"], now)
        return cancelled

    def _owned_children(self, connection, run_id: str, *, include_lead: bool = False) -> list:
        return connection.execute(
            "WITH RECURSIVE edges(parent_run_id,child_task_id,state,created_at,kind) AS ("
            " SELECT parent_run_id,child_task_id,state,created_at,'helper' FROM workflow_children"
            " UNION ALL SELECT l.lead_run_id,l.owned_run_id,r.state,l.created_at,'lead'"
            " FROM workflow_lead_tasks l JOIN workflow_runs r ON r.run_id=l.owned_run_id WHERE ?),"
            " owned(run_id,depth) AS (SELECT ?,0 UNION ALL SELECT e.child_task_id,o.depth+1 FROM edges e"
            " JOIN owned o ON e.parent_run_id=o.run_id) SELECT e.*,o.depth FROM edges e"
            " JOIN owned o ON e.child_task_id=o.run_id WHERE e.child_task_id != ? ORDER BY o.depth,e.created_at,e.child_task_id",
            (include_lead, run_id, run_id),
        ).fetchall()

    def _routing_tasks(self, connection, run_id: str, *, descendants: bool = False, include_lead: bool = False) -> list:
        ids = [run_id]
        if descendants:
            ids.extend(child["child_task_id"] for child in self._owned_children(connection, run_id, include_lead=include_lead))
        placeholders = ",".join("?" for _ in ids)
        return connection.execute(
            f"SELECT t.* FROM tasks t JOIN ({micro_task.ROUTING_TASKS_SQL}) r ON r.task_id=t.task_id"
            f" WHERE r.run_id IN ({placeholders})",
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
            raise BoardError("CONFLICT", "Model selection is still pending; wait or cancel its owning Micro task before continuing")

    def _cancel_routes(self, connection, run_id: str, reason: dict, now: str, *,
                       actor: dict | None = None, initiator: str = "unknown") -> bool:
        fenced = False
        for link in connection.execute("SELECT * FROM workflow_routes WHERE run_id=?", (run_id,)).fetchall():
            decision = self.board.decisions._row(connection, link["decision_id"])
            task = (connection.execute("SELECT * FROM tasks WHERE task_id=?", (decision["decision_task_id"],)).fetchone()
                    if decision["decision_task_id"] else None)
            unconfirmed = task is not None and not self._stop_proven(connection, task)
            if link["state"] != "pending" and not unconfirmed:
                continue
            fenced = True
            connection.execute("UPDATE workflow_routes SET state='fenced',updated_at=? WHERE decision_id=?",
                               (now, link["decision_id"]))
            if task is not None:
                if task["state"] not in ("completed", "failed", "cancelled", "cancelling"):
                    self.board._transition_task(connection, task, "cancelling" if unconfirmed else "cancelled")
                connection.execute("UPDATE tasks SET queue_reason=NULL WHERE task_id=?", (task["task_id"],))
                if unconfirmed:
                    self._request_attempt_stop(connection, task["task_id"], now, initiator=initiator, wrap_up=False)
                self.board.decisions.cancelled(connection, task=task, reason=reason, now=now)
                self.board._append_event(connection, "task.cancel_requested" if unconfirmed else "task.cancelled",
                                         task_id=task["task_id"], payload={"decisionId": link["decision_id"],
                                                                            "owningRunId": run_id})
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
        for child in self._owned_children(connection, run_id, include_lead=True):
            task = connection.execute("SELECT * FROM tasks WHERE task_id=?", (child["child_task_id"],)).fetchone()
            if not self._stop_proven(connection, task):
                unconfirmed.append(child["child_task_id"])
        for task in self._routing_tasks(connection, run_id, descendants=True, include_lead=True):
            if not self._stop_proven(connection, task):
                unconfirmed.append(task["task_id"])
        return {"selfConfirmed": own_stop, "descendantsConfirmed": len(unconfirmed) == (0 if own_stop else 1),
                "unconfirmedRunIds": unconfirmed[:MAX_CHILD_VIEW], "unconfirmedCount": len(unconfirmed),
                "truncated": len(unconfirmed) > MAX_CHILD_VIEW}

    def _terminal_ancestor(self, connection, run_id: str):
        return connection.execute(
            "WITH RECURSIVE edges(parent_run_id,child_task_id) AS ("
            " SELECT parent_run_id,child_task_id FROM workflow_children"
            " UNION ALL SELECT lead_run_id,owned_run_id FROM workflow_lead_tasks),"
            " ancestors(run_id) AS (SELECT parent_run_id FROM edges WHERE child_task_id=?"
            " UNION SELECT e.parent_run_id FROM edges e JOIN ancestors a ON e.child_task_id=a.run_id)"
            " SELECT r.run_id, r.state FROM workflow_runs r JOIN ancestors a ON r.run_id=a.run_id"
            " WHERE r.state IN ('cancelled','accepted','concluded') LIMIT 1", (run_id,),
        ).fetchone()

    def _assert_lineage_open(self, connection, run_id: str) -> None:
        ancestor = self._terminal_ancestor(connection, run_id)
        if ancestor is not None:
            raise BoardError("ANCESTOR_TERMINAL", "An owned helper cannot continue beneath a cancelled or accepted ancestor",
                             runId=run_id, ancestorRunId=ancestor["run_id"], ancestorState=ancestor["state"])

    def _request_attempt_stop(self, connection, task_id: str, now: str, *, initiator: str,
                              wrap_up: bool = True) -> None:
        for attempt in connection.execute(
            "SELECT * FROM attempts WHERE task_id=? AND (execution_state!='finished' OR shutdown_confirmed!=1)",
            (task_id,),
        ).fetchall():
            if attempt["cancel_requested_at"] is not None and initiator in WRAP_UP_INITIATORS:
                continue
            deadline = attempt["grace_deadline"]
            if (wrap_up and initiator in WRAP_UP_INITIATORS
                    and self._execution_adapter(attempt["adapter"]).interrupted_resume
                    and attempt["execution_state"] in ("starting", "executing")):
                deadline = (datetime.fromisoformat(now.replace("Z", "+00:00")) + timedelta(seconds=WRAP_UP_SECONDS)).isoformat(
                    timespec="milliseconds").replace("+00:00", "Z")
            connection.execute(
                "UPDATE attempts SET cancel_requested_at=COALESCE(cancel_requested_at,?),stop_initiator=?,"
                " grace_deadline=?,updated_at=?,revision=revision+1 WHERE attempt_id=?",
                (now, initiator, deadline, now, attempt["attempt_id"]),
            )

    def _cancel_owned_run(self, connection, task, now: str, *, explicit: bool = False,
                          actor: dict | None = None, initiator: str = "unknown") -> bool:
        run_id = task["task_id"]
        run_row = self._run_row(connection, run_id)
        cancel_micro_task = explicit or run_row["state"] not in ("delivered", "accepted", "concluded", "failed")
        connection.execute(
            "UPDATE workflow_requests SET state='cancelled', updated_at=? WHERE run_id=? AND state='open'", (now, run_id),
        )
        connection.execute(
            "UPDATE workflow_continuations SET state='cancelled' WHERE run_id=? AND state IN ('recorded','queued')", (run_id,),
        )
        self._revoke_credentials(connection, run_id, now)
        self._cancel_routes(connection, run_id, make_reason("micro-task-cancelled"), now, actor=actor,
                            initiator=initiator)
        if cancel_micro_task:
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
        if task["state"] not in ("completed", "concluded", "failed", "cancelled"):
            self.board._transition_task(connection, task, "cancelled" if stopped else "cancelling")
            connection.execute("UPDATE tasks SET queue_reason=NULL, updated_at=? WHERE task_id=?", (now, run_id))
        if not stopped:
            self._request_attempt_stop(connection, run_id, now, initiator=initiator)
        return cancel_micro_task

    def _late_cancelled_turn(self, connection, run_row, turn_row, attempt, payload, now):
        """Keep real receipt/artifact evidence without reopening a fenced micro task."""
        turn = self.turn_outcome(payload)
        disposition = None
        if (attempt["shutdown_confirmed"] and payload is not None
                and payload.get("terminationReason") != "grace-expired"
                and turn is not None and self._validate_turn(turn, turn_row, attempt) is None):
            disposition = turn["outcome"]["disposition"]
            connection.execute(
                "UPDATE workflow_turns SET state='returned', disposition=?, outcome_json=?, outcome_truncated_json=?, provenance_json=?,"
                " session_id=?, prompt_sha256=?, input_sha256=?, turn_result_path=?, sealed_artifacts_json=?,"
                " updated_at=? WHERE turn_id=?",
                (disposition, canonical_json(turn["outcome"]), canonical_json(turn["outcomeTruncated"]) if "outcomeTruncated" in turn else None,
                 canonical_json(turn.get("provenance")),
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

    def attempt_released(self, connection, *, task, attempt, now: str,
                         seal_stash: dict | None = None) -> None:
        """Settle a Worker's durable never-spawned release inside its transaction."""
        if self._run_optional(connection, task["task_id"]) is None:
            return
        if not self._stop_proven(connection, task):
            raise BoardError("SHUTDOWN_UNCONFIRMED", "A workflow release requires durable finished and confirmed stop evidence")
        self.settle_turn(connection, task=task, attempt=attempt, payload=None, task_state=task["state"], now=now,
                            seal_stash=seal_stash)
        self.child_settled(connection, task=task, attempt=attempt, payload=None, task_state=task["state"], now=now)
        self._release_reservations(connection, task["task_id"], now)
        self._revoke_credentials(connection, task["task_id"], now)

    def _revoke_credentials(self, connection, run_id: str, now: str) -> None:
        connection.execute(
            "UPDATE agent_credentials SET state='revoked', revoked_at=?, revision=revision+1"
            " WHERE run_id=? AND state='active'",
            (now, run_id),
        )

    # -- acknowledge ---------------------------------------------------------
    def _recorded_acceptance_integration(self, connection, run_id: str) -> str | None:
        """The integration record the recorded acceptance was bound to, if any.

        The acceptance event is append-only and carries the exact integration id
        that was current when the verdict was recorded, so a later command can
        never relabel an accepted outcome with a different claimed integration.
        """
        row = connection.execute(
            "SELECT payload_json FROM events WHERE task_id=? AND kind='workflow.acknowledged'"
            " ORDER BY seq DESC LIMIT 1",
            (run_id,),
        ).fetchone()
        if row is None:
            return None
        try:
            payload = json.loads(row["payload_json"])
        except (TypeError, ValueError):
            return None
        value = payload.get("integrationId") if isinstance(payload, dict) else None
        return value if isinstance(value, str) and value else None

    def accept(self, params: dict, *, console_authority: dict | None = None) -> dict:
        """Accept one delivered artifact: verify its integration, record, reclaim.

        The Host binds the exact ``artifactId`` and either the real integration
        ``target`` (checkout path plus ref) or a reasoned ``notRequired`` string.
        The service derives the integration itself: the target identity, commits
        and trees are resolved from the repository, and every path the artifact
        changed must already carry that content in the target tree. Differing
        paths are refused with their names first; the Host confirms they are its
        own adjustments with ``adjusted: true`` (the note is the reason) instead
        of listing paths. Optional ``hostPaths`` with ``beforeCommit`` still
        verify real Host additions outside the artifact's paths. Without
        ``keepCheckout`` the registered managed checkout is reclaimed after the
        acceptance is durable; a blocked reclaim keeps the acceptance and reports
        its reasons, and a later ``reclaim`` retries. The same run/artifact
        payload replays its recorded response; a changed payload is a conflict.
        """
        schemas.reject_unknown(
            params,
            {
                "runId", "targetRunId", "artifactId", "note", "target", "notRequired", "adjusted",
                "hostPaths", "beforeCommit", "keepCheckout", *schemas.CONTROL_FIELDS,
                schemas.CONSOLE_AUTHORITY_FIELD,
            },
            "workflow.accept",
        )
        schemas.reject_untrusted_override(params)
        owner_run_id = run_id = schemas.required_string(params, "runId", max_length=128)
        target_id = schemas.optional_string(params, "targetRunId", max_length=128) or run_id
        artifact_id = schemas.required_string(params, "artifactId", max_length=128)
        note, _ = schemas.bounded_text(params, "note", max_bytes=schemas.MAX_NOTE_BYTES)
        not_required = params.get("notRequired")
        if not_required is not None and (not isinstance(not_required, str) or not not_required.strip()):
            raise BoardError("INVALID_ARGUMENT", "notRequired must be a nonempty note string")
        not_required = (schemas.bounded_text(params, "notRequired", max_bytes=schemas.MAX_NOTE_BYTES)[0]
                        if not_required is not None else None)
        adjusted = schemas.optional_bool(params, "adjusted", False)
        host_paths = schemas.string_list(params, "hostPaths", limit=256)
        before_commit = schemas.optional_string(params, "beforeCommit", max_length=128)
        keep_checkout = schemas.optional_bool(params, "keepCheckout", False)
        target = None
        if not_required is not None:
            if params.get("target") is not None:
                raise BoardError("INVALID_ARGUMENT", "A not-required acceptance cannot name an integration target")
            if adjusted or host_paths or before_commit:
                raise BoardError("INVALID_ARGUMENT", "A not-required acceptance cannot carry adjustment evidence")
            if host_paths and not before_commit:
                raise BoardError("INVALID_ARGUMENT", "hostPaths verification requires the beforeCommit baseline")
        else:
            if host_paths and not before_commit:
                raise BoardError("INVALID_ARGUMENT", "hostPaths verification requires the beforeCommit baseline")
            target = schemas.require_object(params.get("target"), "target")
            schemas.reject_unknown(target, {"path", "ref", "repositoryId", "checkoutId"}, "integration target")
            target_path = schemas.required_string(target, "path", max_length=4096)
            if not target_path.startswith("/"):
                raise BoardError("INVALID_ARGUMENT", "target.path must be an absolute checkout path")
            schemas.required_string(target, "ref", max_length=512)
        normalized_target = ({key: target.get(key) for key in ("path", "ref", "repositoryId", "checkoutId")}
                             if target is not None else None)
        # Acceptance is keyed by run and artifact: the derived receipt replays the
        # same request without a Host-chosen command number, and any changed
        # payload derives a different key that conflicts against the recorded
        # acceptance instead of relabelling it.
        request_key = {"runId": owner_run_id, "targetRunId": target_id, "artifactId": artifact_id, "note": note,
                       "notRequired": not_required, "target": normalized_target, "adjusted": adjusted,
                       "hostPaths": sorted(host_paths), "beforeCommit": before_commit,
                       "keepCheckout": keep_checkout}
        command_id = "accept-" + sha256_text(canonical_json(request_key))[:32]
        verification_inputs = None
        duplicate = None
        with self.db.read() as connection:
            receipt_entry = self.board._receipt_entry(connection, command_id, "workflow.accept", request_key)
            if receipt_entry is not None and receipt_entry["deletion_id"] is not None:
                return self.board._receipt_response(connection, receipt_entry)
            owner = self._run_row(connection, owner_run_id)
            actor, _initiator = self._authorize(connection, owner, params, console_authority=console_authority, action="An acceptance")
            receipt = self.board._receipt_response(connection, receipt_entry)
            if receipt is not None:
                duplicate = {**receipt, "duplicate": True}
            else:
                # Ownership first: an illegal or detached targetRunId is refused
                # before any Git verification work runs.
                self._target_run(connection, owner, target_id)
            if duplicate is None and not_required is None:
                # The Git comparison runs outside every transaction; the write below
                # re-checks ownership, state and artifact identity before recording.
                # Its refusals (unknown artifact, unverifiable target) come before a
                # recorded-acceptance conflict so the Host always sees the concrete
                # integration reason first.
                verification_inputs = self._accept_verification_inputs(connection, target_id, artifact_id)
                if self._accept_conflicts_with_recorded(connection, target_id, artifact_id, note,
                                                        not_required, normalized_target):
                    raise BoardError(
                        "CONFLICT",
                        "This run already has a different recorded acceptance; a reviewed outcome is not relabelled",
                        runId=target_id,
                    )
            elif self._accept_conflicts_with_recorded(connection, target_id, artifact_id, note,
                                                      not_required, normalized_target):
                raise BoardError(
                    "CONFLICT",
                    "This run already has a different recorded acceptance; a reviewed outcome is not relabelled",
                    runId=target_id,
                )
        verification: dict = {}
        if duplicate is None and not_required is None:
            original_manifest, final_manifest, manifest = verification_inputs
            verification = self._accept_verify(
                manifest, original_input=original_manifest, final_input=final_manifest,
                target=target, host_paths=host_paths, before_commit=before_commit,
                adjusted=adjusted, note=note)
        now = self.now()
        with self.db.write() as connection:
            receipt_entry = self.board._receipt_entry(connection, command_id, "workflow.accept", request_key)
            if receipt_entry is not None and receipt_entry["deletion_id"] is not None:
                return self.board._receipt_response(connection, receipt_entry)
            owner = self._run_row(connection, owner_run_id)
            actor, _initiator = self._authorize(connection, owner, params, console_authority=console_authority, action="An acceptance")
            receipt = self.board._receipt_response(connection, receipt_entry)
            if receipt is not None:
                duplicate = {**receipt, "duplicate": True}
            run_row = self._target_run(connection, owner, target_id)
            run_id = target_id
            task = connection.execute("SELECT * FROM tasks WHERE task_id=?", (run_id,)).fetchone()
            if duplicate is None:
                response, _, _ = self._accept_record(
                    connection, owner_run_id=owner_run_id, run_row=run_row, task=task, actor=actor, now=now,
                    artifact_id=artifact_id, note=note, command_id=command_id, request_key=request_key,
                    verification=verification, not_required=not_required)
            else:
                response = duplicate
            head = self.board._head_of(connection)
        self.board._notify(head)
        response = dict(response)
        if duplicate is not None:
            # A replay returns the recorded outcome and never repeats the removal;
            # a lost reclaim retry goes through the dedicated reclaim operation.
            response.setdefault("reclaim", {"removed": False, "reasons": ["replayed"], "retry": "reclaim"})
        elif not keep_checkout:
            response["reclaim"] = self._reclaim_checkout(owner_run_id=owner_run_id, run_id=run_id,
                                                         purpose="accept", control=params, console_authority=console_authority)
        # The reclaim journals its removal under fresh run revisions; the returned
        # view must match the board a caller sees after this call.
        with self.db.read() as connection:
            run_row = self._run_row(connection, run_id)
            task = connection.execute("SELECT * FROM tasks WHERE task_id=?", (run_id,)).fetchone()
            response.update(self.compact(connection, run_row, task))
            response["targetRevision"] = run_row["revision"]
        return response

    def _accept_conflicts_with_recorded(self, connection, run_id: str, artifact_id: str, note: str,
                                        not_required: str | None, target: dict | None) -> bool:
        """Whether an already recorded acceptance contradicts this request.

        Checked before any Git work: a changed payload against a reviewed outcome
        is a conflict, never a relabel, and never a second integration attempt.
        """
        if connection.execute("SELECT 1 FROM workflow_runs WHERE run_id=?", (run_id,)).fetchone() is None:
            return False
        task = connection.execute("SELECT accepted_at, acceptance_verdict, acceptance_note FROM tasks WHERE task_id=?", (run_id,)).fetchone()
        if not task["accepted_at"] or task["acceptance_verdict"] != "accepted":
            return False
        if task["acceptance_note"] != note:
            return True
        run_row = self._run_row(connection, run_id)
        if run_row["final_artifact_id"] != artifact_id:
            return True
        recorded = self._find_integration(connection, run_id, artifact_id)
        if recorded is None:
            return True
        if not_required is not None:
            return recorded["state"] != "not-required" or (recorded["note"] or "") != not_required
        if recorded["state"] != "verified":
            return True
        # The recorded path is the repository-resolved identity; compare the
        # request in the same normalized form so a path alias is not a new claim.
        recorded_target = Path(recorded["target_path"]).resolve()
        requested_target = Path(target.get("path") or "").resolve()
        return (recorded_target != requested_target
                or recorded["target_ref"] != target.get("ref"))

    def _accept_verification_inputs(self, connection, run_id: str, artifact_id: str):
        """Pin the exact manifests one verified acceptance compares, before any Git work."""
        artifact = connection.execute(
            "SELECT * FROM workflow_artifacts WHERE artifact_id=? AND run_id=? AND kind IN ('output','resolved-output','partial-output')",
            (artifact_id, run_id),
        ).fetchone()
        if artifact is None:
            raise BoardError("NOT_FOUND", "No sealed output artifact of this run has that identity",
                             artifactId=artifact_id)
        manifest = json.loads(artifact["manifest_json"])
        if manifest.get("snapshotSha256") != artifact["manifest_sha256"]:
            raise BoardError("WORKSPACE_MANIFEST_CHANGED", "The retained output identity changed")
        original = connection.execute(
            "SELECT * FROM workflow_artifacts WHERE run_id=? AND kind='input' ORDER BY rowid LIMIT 1",
            (run_id,),
        ).fetchone()
        turn = connection.execute(
            "SELECT * FROM workflow_turns WHERE run_id=? AND turn_id=? AND attempt_id=?",
            (run_id, artifact["turn_id"], artifact["attempt_id"]),
        ).fetchone()
        if (original is None or turn is None
                or (turn["input_sha256"] is not None
                    and sha256_text(turn["input_json"]) != turn["input_sha256"])):
            raise BoardError("WORKSPACE_MANIFEST_CHANGED", "The final output has no proven input manifest")
        original_manifest = json.loads(original["manifest_json"])
        final_manifest = json.loads(turn["input_json"]).get("executionWorkspace")
        if not isinstance(final_manifest, dict):
            raise BoardError("WORKSPACE_MANIFEST_CHANGED", "The final turn has no execution workspace")
        if (original_manifest.get("manifestSha256") != original["manifest_sha256"]
                or final_manifest.get("manifestSha256") != manifest.get("manifestSha256")):
            raise BoardError("WORKSPACE_MANIFEST_CHANGED", "A retained input manifest identity changed")
        return original_manifest, final_manifest, manifest

    def _accept_verify(self, manifest: dict, *, original_input: dict, final_input: dict,
                       target: dict, host_paths: list[str], before_commit: str | None,
                       adjusted: bool, note: str) -> dict:
        """Compare the immutable artifact with the real target and name every difference.

        Without ``adjusted`` differing paths are refused; with it the same
        comparison records those exact paths as Host adjustments bound to the note.
        """
        module = workspace_module()
        verification = module.integration_verify(
            manifest, original_input=original_input, final_input=final_input,
            path=target["path"], ref=target["ref"], strategy="patch", before_commit=before_commit,
            repository_id=target.get("repositoryId"), checkout_id=target.get("checkoutId"),
            host_paths=host_paths, note=note, _confirm_adjustments=adjusted,
        )
        unrecorded = sorted({item["path"] for item in verification.get("differingPaths") or []}
                            | set(verification.get("missingPaths") or []))
        if not verification.get("verified") and not unrecorded:
            # Content matched but the claimed commit interval does not hold (for
            # example a beforeCommit unrelated to the target ref): the comparison
            # proves nothing and must never be recorded as a verified integration.
            raise BoardError(
                "INTEGRATION_UNVERIFIED",
                "The target comparison does not hold; the artifact content matched but the named commit interval "
                "does not, so no integration was recorded",
                paths=[],
                verification=verification,
            )
        if not unrecorded:
            verification["strategy"] = "merge" if verification.get("artifactAncestor") else "patch"
            verification["summary"] = ""
            return verification
        if not adjusted:
            raise BoardError(
                "INTEGRATION_UNVERIFIED",
                "The target does not contain the artifact as delivered; confirm these are your own adjustments "
                "with adjusted:true instead of listing paths",
                paths=unrecorded,
                verification=verification,
            )
        if not verification.get("verified"):
            raise BoardError(
                "INTEGRATION_UNVERIFIED",
                "Even with the Host's confirmed adjustments the target comparison does not hold; no integration "
                "was recorded",
                paths=unrecorded,
                verification=verification,
            )
        verification["strategy"] = "merge" if verification.get("artifactAncestor") else "patch"
        verification["summary"] = ""
        return verification

    def _accept_record(self, connection, *, owner_run_id: str, run_row, task, actor: dict, now: str,
                       artifact_id: str, note: str, command_id: str, request_key: dict,
                       verification: dict, not_required: str | None):
        """Record one acceptance, its integration binding and the events, in one transaction."""
        run_id = run_row["run_id"]
        # Acceptance is bound to the current delivered micro task, never to a Host
        # decision boundary, an active helper, or an older attempt's output.
        if run_row["state"] == "concluded":
            raise BoardError("CONFLICT", "This micro task already has a recorded conclusion; it is closed without acceptance", runId=run_id)
        host_completion = run_row["state"] == "awaiting-host"
        if run_row["state"] not in ("delivered", "accepted") and not host_completion:
            raise BoardError(
                "NOT_READY",
                "Only a delivered micro task can be accepted; a Host decision boundary or a running turn is "
                "not a final artifact",
                runId=run_id,
                state=run_row["state"],
            )
        if run_row["delegation_authority"]:
            unfinished = connection.execute(
                "SELECT r.run_id,r.state,COUNT(*) OVER() AS total FROM workflow_lead_tasks l"
                " JOIN workflow_runs r ON r.run_id=l.owned_run_id"
                " WHERE l.lead_run_id=? AND r.state NOT IN ('accepted','concluded')"
                " ORDER BY l.created_at,r.run_id LIMIT ?", (run_id, MAX_CHILD_VIEW),
            ).fetchall()
            if unfinished:
                raise BoardError(
                    "NOT_READY", "Accept or conclude every owned micro task before accepting its Lead; continue or conclude the Lead",
                    unfinishedOwnedMicroTasks=[{"runId": row["run_id"], "state": row["state"]} for row in unfinished],
                    unfinishedOwnedMicroTaskTotal=unfinished[0]["total"],
                )
        if host_completion:
            self._lineage_stop_evidence(connection, run_row, "Host completion")
            if self._open_conflict(connection, run_id) is not None:
                raise BoardError("CONFLICT", "Resolve the workspace conflict before Host completion")
            if connection.execute("SELECT 1 FROM workflow_continuations WHERE run_id=? AND state IN ('recorded','queued') LIMIT 1", (run_id,)).fetchone():
                raise BoardError("CONFLICT", "A continuation is still pending")
            if connection.execute("SELECT 1 FROM workflow_children WHERE parent_run_id=? AND state IN ('active','attention') LIMIT 1", (run_id,)).fetchone():
                raise BoardError("CONFLICT", "Settle owned helpers before Host completion")
        active_helpers = int(
            connection.execute(
                "SELECT COUNT(*) AS count FROM workflow_children WHERE parent_run_id=? AND state='active'",
                (run_id,),
            ).fetchone()["count"]
        )
        if active_helpers:
            raise BoardError(
                "CONFLICT",
                "Owned helpers are still running; settle or cancel them before accepting",
                runId=run_id,
                activeHelpers=active_helpers,
            )
        attempt = self.board._selected_attempt(connection, task)
        if attempt is None or attempt["result_json"] is None:
            raise BoardError("NOT_READY", "Inspect a persisted final result before accepting")
        if not attempt["shutdown_confirmed"]:
            raise BoardError(
                "SHUTDOWN_UNCONFIRMED",
                "Execution shutdown must be confirmed before accepting; a surviving process is unknown",
                attemptId=attempt["attempt_id"],
            )
        if not host_completion and run_row["final_attempt_id"] != attempt["attempt_id"]:
            raise BoardError(
                "CONFLICT",
                "The selected attempt is not the current final attempt of this micro task",
                attemptId=attempt["attempt_id"],
                finalAttemptId=run_row["final_attempt_id"],
            )
        final_artifact = None
        if not host_completion and run_row["final_artifact_id"]:
            final_artifact = connection.execute(
                "SELECT * FROM workflow_artifacts WHERE artifact_id=? AND run_id=?"
                " AND kind IN ('output','resolved-output')",
                (run_row["final_artifact_id"], run_id),
            ).fetchone()
        if final_artifact is None:
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
        if final_artifact["attempt_id"] != attempt["attempt_id"]:
            raise BoardError(
                "CONFLICT",
                "The recorded final artifact was not produced by the current final attempt",
                artifactId=final_artifact["artifact_id"],
                artifactAttemptId=final_artifact["attempt_id"],
                attemptId=attempt["attempt_id"],
            )
        if artifact_id != final_artifact["artifact_id"]:
            other = connection.execute(
                "SELECT * FROM workflow_artifacts WHERE artifact_id=? AND run_id=?", (artifact_id, run_id)
            ).fetchone()
            if other is None:
                raise BoardError("NOT_FOUND", "Unknown pinned artifact for this run", artifactId=artifact_id)
            raise BoardError(
                "CONFLICT",
                "Acceptance must bind the current final output artifact, not another artifact of this run",
                artifactId=artifact_id,
                finalArtifactId=final_artifact["artifact_id"],
                artifactKind=other["kind"],
            )
        artifact = final_artifact
        # A normal delivery records a returned completed turn. A Host-resolution delivery
        # is the one alternative: the failed attempt's own native completed outcome
        # is preserved in its immutable result, recorded separately on the
        # conflict, and revalidated here. The failed turn row itself stays failed.
        resolution = None
        previous_host_completion = connection.execute(
            "SELECT payload_json FROM events WHERE task_id=? AND attempt_id=? AND kind='workflow.host_completed' ORDER BY seq DESC LIMIT 1",
            (run_id, attempt["attempt_id"]),
        ).fetchone()
        host_proof = (previous_host_completion is not None and json.loads(previous_host_completion[0]).get("artifactId") == artifact["artifact_id"])
        if host_completion:
            current = connection.execute("SELECT * FROM workflow_turns WHERE turn_id=? AND attempt_id=?", (run_row["current_turn_id"], attempt["attempt_id"])).fetchone()
            if current is None or current["state"] != "returned" or current["disposition"] not in ("assistance", "attention", "completed"):
                raise BoardError("NOT_READY", "Host completion requires the current stopped and sealed turn")
        if not (host_completion or host_proof) and connection.execute(
            "SELECT * FROM workflow_turns WHERE attempt_id=? AND state='returned' AND disposition='completed'",
            (attempt["attempt_id"],),
        ).fetchone() is None:
            resolution = self._resolution_delivery(connection, run_row, attempt, artifact)
            if resolution is None:
                raise BoardError(
                    "NOT_READY",
                    "The current final attempt has no returned completed turn to accept",
                    attemptId=attempt["attempt_id"],
                )
        if task["review_rejected_at"]:
            self.board._append_event(connection, "task.review_archived", task_id=run_id,
                attempt_id=attempt["attempt_id"],
                payload={"reviewRejectedAt": task["review_rejected_at"], "verdict": "rejected",
                         "note": task["review_rejection_note"], "artifactId": run_row["final_artifact_id"]})
        if task["accepted_at"]:
            if (
                task["acceptance_note"] != note
                or run_row["final_artifact_id"] != artifact["artifact_id"]
            ):
                raise BoardError(
                    "CONFLICT",
                    "This run already has a different recorded acceptance; a reviewed outcome is not relabelled",
                    acceptedAt=task["accepted_at"],
                    verdict=task["acceptance_verdict"],
                )
            response = {**self.compact(connection, run_row, task), "verdict": "accepted", "duplicate": True,
                        "targetRunId": run_id, "targetRevision": run_row["revision"]}
            if resolution is not None:
                response["resolutionDelivery"] = self._resolution_delivery_result(resolution)
            return response, artifact, None
        # The integration binding is content-addressed: repeating the same verified
        # comparison finds the existing record instead of minting a second one.
        manifest = json.loads(artifact["manifest_json"])
        binding = {"runId": run_id, "artifactId": artifact_id,
                   "strategy": "not-required" if not_required is not None else verification["strategy"],
                   "sourceCommit": manifest.get("commit"), "sourceTree": manifest.get("tree")}
        if not_required is not None:
            binding["reason"] = not_required
        else:
            binding.update(beforeCommit=verification.get("beforeCommit"), afterCommit=verification.get("afterCommit"),
                           targetPath=verification["target"]["path"], targetRef=verification["target"]["ref"])
        binding["hostPaths"] = sorted(request_key["hostPaths"])
        binding_sha = sha256_text(canonical_json(binding))
        existing = connection.execute(
            "SELECT * FROM workflow_integrations WHERE binding_sha256=?", (binding_sha,)
        ).fetchone()
        if existing is None:
            integration_id = f"int-{uuid.uuid4()}"
            connection.execute(
                "INSERT INTO workflow_integrations(integration_id, run_id, artifact_id, attempt_id, state, strategy,"
                " binding_sha256, target_kind, target_path, target_ref, target_repository_id, target_checkout_id,"
                " source_commit, source_tree, before_commit, after_commit, before_tree, after_tree, verification_json,"
                " note, command_id, actor_role, actor_id, created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    integration_id, run_id, artifact_id, artifact["attempt_id"],
                    "not-required" if not_required is not None else "verified",
                    "not-required" if not_required is not None else verification["strategy"], binding_sha,
                    "not-required" if not_required is not None else "checkout",
                    "" if not_required is not None else verification["target"]["path"],
                    "" if not_required is not None else verification["target"]["ref"],
                    None if not_required is not None else verification["target"]["repositoryId"],
                    None if not_required is not None else verification["target"]["checkoutId"],
                    verification.get("sourceCommit"), verification.get("sourceTree"),
                    None if not_required is not None else verification.get("beforeCommit"),
                    None if not_required is not None else verification.get("afterCommit"),
                    None if not_required is not None else verification.get("beforeTree"),
                    None if not_required is not None else verification.get("afterTree"),
                    canonical_json(verification), not_required, command_id, actor["role"], actor["id"], now,
                ),
            )
            self.board._append_event(
                connection,
                "workflow.integration_recorded",
                task_id=run_id,
                attempt_id=artifact["attempt_id"],
                revision=run_row["revision"] + 1,
                payload={"integrationId": integration_id, "artifactId": artifact_id,
                         "state": "not-required" if not_required is not None else "verified",
                         "strategy": binding["strategy"], "afterCommit": verification.get("afterCommit")},
            )
            integration = connection.execute(
                "SELECT * FROM workflow_integrations WHERE integration_id=?", (integration_id,)
            ).fetchone()
        else:
            if existing["run_id"] != run_id or existing["artifact_id"] != artifact_id:
                raise BoardError("CONFLICT", "This integration binding already belongs to another artifact",
                                 integrationId=existing["integration_id"])
            integration = existing
        updated = connection.execute(
            "UPDATE tasks SET accepted_at=?, acceptance_note=?, acceptance_verdict='accepted',"
            " review_rejected_at=NULL, review_rejection_note=NULL, updated_at=?,"
            " revision=revision+1 WHERE task_id=?",
            (now, note, now, run_id),
        )
        if updated.rowcount != 1:
            raise BoardError("REVISION_CONFLICT", "The micro task changed concurrently; re-read it and retry")
        if host_completion:
            connection.execute("UPDATE tasks SET state='completed',queue_reason=NULL WHERE task_id=?", (run_id,))
            open_requests = connection.execute("SELECT request_id FROM workflow_requests WHERE run_id=? AND state='open'", (run_id,)).fetchall()
            connection.execute("UPDATE workflow_requests SET state='superseded',updated_at=? WHERE run_id=? AND state='open'", (now, run_id))
            for request in open_requests:
                self._close_proxy_ancestors(connection, self._request_row(connection, run_id, request["request_id"]), now)
            connection.execute("UPDATE workflow_runs SET active_request_id=NULL WHERE run_id=?", (run_id,))
            self.board._append_event(connection, "workflow.host_completed", task_id=run_id, attempt_id=attempt["attempt_id"],
                                     payload={"artifactId": artifact["artifact_id"], "integrationId": integration["integration_id"], "actor": actor})
        updated = connection.execute(
            "UPDATE workflow_runs SET state='accepted', final_artifact_id=?, final_attempt_id=?, updated_at=?,"
            " revision=revision+1 WHERE run_id=? AND revision=?",
            (
                artifact["artifact_id"],
                attempt["attempt_id"],
                now,
                run_id,
                run_row["revision"],
            ),
        )
        if updated.rowcount != 1:
            raise BoardError("REVISION_CONFLICT", "The micro-task run changed concurrently; re-read it and retry")
        if not host_completion:
            # Logical ownership ends with the recorded acceptance exactly as before;
            # keepCheckout keeps the checkout directory, not the writer exclusion.
            self._release_reservations(connection, run_id, now)
        else:
            updated_task = connection.execute("SELECT * FROM tasks WHERE task_id=?", (run_id,)).fetchone()
            self.child_settled(connection, task=updated_task, attempt=attempt, payload=None, task_state="completed", now=now)
        self.board._append_event(
            connection,
            "workflow.acknowledged",
            task_id=run_id,
            attempt_id=attempt["attempt_id"],
            revision=run_row["revision"] + 1,
            payload={
                "verdict": "accepted",
                "actor": actor,
                "artifactId": artifact["artifact_id"],
                "integrationId": integration["integration_id"],
                "resolutionDelivery": (self._resolution_delivery_result(resolution)
                                       if resolution is not None else None),
                "noteBytes": len(note.encode()),
            },
        )
        run_row = self._run_row(connection, run_id)
        task = connection.execute("SELECT * FROM tasks WHERE task_id=?", (run_id,)).fetchone()
        view = self.compact(connection, run_row, task)
        response = {**view, "verdict": "accepted", "duplicate": False,
                    "targetRunId": run_id, "targetRevision": run_row["revision"],
                    "integrationId": integration["integration_id"]}
        if integration is not None:
            response["integration"] = self._integration_view(integration)
        if resolution is not None:
            response["resolutionDelivery"] = self._resolution_delivery_result(resolution)
        self.board._store_receipt(
            connection, command_id, "workflow.accept", request_key, response, task_id=run_id,
            attempt_id=attempt["attempt_id"],
        )
        return response, artifact, integration

    def _adopted_board_paths(self, connection, run_id: str, manifest: dict) -> list[str]:
        """The exact adopted paths proven by board-bound adoption artifacts.

        Only pinned `resolved-output` records bound to this manifest and verified
        against their fixed Git objects authorize out-of-scope paths; local files
        alone never do. The named paths must sit inside the record's provably
        changed set, so a forged or drifted local record authorizes nothing.
        """
        if not manifest.get("manifestSha256"):
            return []
        module = workspace_module()
        adopted: set[str] = set()
        for row in connection.execute(
            "SELECT manifest_json, manifest_sha256 FROM workflow_artifacts WHERE run_id=? AND kind='resolved-output'",
            (run_id,),
        ).fetchall():
            try:
                record = json.loads(row["manifest_json"])
            except (TypeError, ValueError):
                continue
            if not isinstance(record, dict) or record.get("kind") != "resolution" or record.get("action") != "adopt":
                continue
            if (record.get("snapshotSha256") != row["manifest_sha256"]
                    or record.get("manifestSha256") != manifest["manifestSha256"]):
                continue
            try:
                module.verify_sealed_output(manifest, manifest, record)
            except BoardError as error:
                if error.code == "WORKSPACE_GIT_UNKNOWN":
                    raise
                continue
            values = record.get("adoptedPaths")
            if not isinstance(values, list):
                snapshot = record.get("snapshot")
                values = snapshot.get("adoptedPaths") if isinstance(snapshot, dict) else None
            if not isinstance(values, list):
                continue
            candidates = {value for value in values if isinstance(value, str) and value}
            if candidates and candidates <= set(record.get("changedPaths") or []):
                adopted |= candidates
        return sorted(adopted)

    def _board_adopted_paths(self, run_id: str, manifest: dict) -> list[str]:
        with self.db.read() as connection:
            return self._adopted_board_paths(connection, run_id, manifest)

    def conclude(self, params: dict, *, console_authority: dict | None = None) -> dict:
        """End one micro task that will not be accepted, then reclaim its checkout.

        A failed, cancelled or delivered-but-unaccepted micro task receives one
        independent Host conclusion next to the real execution result; an
        accepted micro task is already closed. After the stop evidence holds, managed
        changes that no fixed output has sealed yet become an independent Host
        partial output (never overwriting an earlier partial of the same
        attempt), the conclusion and that seal commit together, and the
        registered checkout is reclaimed. An unresolved workspace conflict, a
        pending continuation or a live helper refuses the conclusion instead of
        being bypassed by it. The same note replays; a changed note conflicts.
        """
        schemas.reject_unknown(
            params,
            {"runId", "targetRunId", "note", *schemas.CONTROL_FIELDS, schemas.CONSOLE_AUTHORITY_FIELD},
            "workflow.conclude",
        )
        schemas.reject_untrusted_override(params)
        owner_run_id = run_id = schemas.required_string(params, "runId", max_length=128)
        target_id = schemas.optional_string(params, "targetRunId", max_length=128) or run_id
        note, _ = schemas.bounded_text(params, "note", max_bytes=schemas.MAX_NOTE_BYTES)
        # The receipt binds the reviewed execution and note; a closed micro task only replays it.
        descendants = self._stop_lead_for_conclusion(owner_run_id, target_id, params, console_authority)
        seal_work = None
        owned_work = []
        duplicate = None
        with self.db.read() as connection:
            owner = self._run_row(connection, owner_run_id)
            actor, _initiator = self._authorize(connection, owner, params, console_authority=console_authority, action="A Host conclusion")
            target_snapshot = self._target_run(connection, owner, target_id)
            task = connection.execute("SELECT * FROM tasks WHERE task_id=?", (target_id,)).fetchone()
            request_key = {"runId": owner_run_id, "targetRunId": target_id, "note": note,
                           "attemptId": task["selected_attempt_id"]}
            command_id = "conclude-" + sha256_text(canonical_json(request_key))[:32]
            receipt = self.board._receipt(connection, command_id, "workflow.conclude", request_key)
            if receipt is not None:
                duplicate = {**receipt, "duplicate": True}
            else:
                task = self._conclude_precheck(connection, target_snapshot, task)
                seal_work = self._conclude_seal_work(connection, target_snapshot, task)
                for child_id in descendants:
                    child = self._run_row(connection, child_id)
                    child_task = self._conclude_precheck(connection, child, None)
                    child_reason = make_reason("lead.owned-concluded", leadRunId=target_id)
                    child_key = {"runId": owner_run_id, "targetRunId": child_id, "reason": child_reason,
                                 "attemptId": child_task["selected_attempt_id"]}
                    owned_work.append({"snapshot": child, "request": child_key,
                                       "sealWork": self._conclude_seal_work(connection, child, child_task)})
        sealed_record = self._seal_conclusion(target_id, seal_work)
        for child in owned_work:
            child["seal"] = self._seal_conclusion(child["request"]["targetRunId"], child["sealWork"])
        now = self.now()
        with self.db.write() as connection:
            owner = self._run_row(connection, owner_run_id)
            actor, _initiator = self._authorize(connection, owner, params, console_authority=console_authority, action="A Host conclusion")
            receipt = self.board._receipt(connection, command_id, "workflow.conclude", request_key)
            if receipt is not None:
                duplicate = {**receipt, "duplicate": True}
            run_row = self._target_run(connection, owner, target_id)
            run_id = run_row["run_id"]
            task = connection.execute("SELECT * FROM tasks WHERE task_id=?", (run_id,)).fetchone()
            if duplicate is None:
                # The receipt, seal and conclusion must describe one execution.
                # A concurrent continuation can finish a later attempt while the
                # Git seal runs; it cannot inherit this earlier Host review.
                self._expect_revision(run_row, target_snapshot["revision"])
                if (task["selected_attempt_id"] != request_key["attemptId"]
                        or run_row["workspace_manifest_json"] != target_snapshot["workspace_manifest_json"]):
                    raise BoardError("REVISION_CONFLICT", "The execution or workspace changed while concluding; re-read it and retry")
                for child in owned_work:
                    child_key = child["request"]
                    child_run = self._run_row(connection, child_key["targetRunId"])
                    child_task = connection.execute("SELECT * FROM tasks WHERE task_id=?", (child_run["run_id"],)).fetchone()
                    self._expect_revision(child_run, child["snapshot"]["revision"])
                    if (child_task["selected_attempt_id"] != child_key["attemptId"]
                            or child_run["workspace_manifest_json"] != child["snapshot"]["workspace_manifest_json"]):
                        raise BoardError("REVISION_CONFLICT", "An owned execution or workspace changed while concluding")
                    self._conclude_record(
                        connection, run_row=child_run, task=child_task, actor=actor, now=now, note=None, reason=child_key["reason"],
                        command_id="conclude-" + sha256_text(canonical_json(child_key))[:32], request_key=child_key,
                        sealed_record=child["seal"])
                response = self._conclude_record(
                    connection, run_row=run_row, task=task, actor=actor, now=now, note=note,
                    command_id=command_id, request_key=request_key, sealed_record=sealed_record)
            else:
                response = duplicate
            head = self.board._head_of(connection)
        self.board._notify(head)
        response = dict(response)
        owned_reclaims = []
        for child_id in descendants:
            reclaimed = self._reclaim_checkout(owner_run_id=owner_run_id, run_id=child_id,
                                                purpose="conclude", control=params, lead_conclusion=True,
                                                console_authority=console_authority)
            if len(owned_reclaims) < MAX_CHILD_VIEW:
                owned_reclaims.append({"runId": child_id, **reclaimed})
        if descendants:
            response["ownedReclaims"] = owned_reclaims
            response["ownedReclaimTotal"] = len(descendants)
        if duplicate is not None:
            response.setdefault("reclaim", {"removed": False, "reasons": ["replayed"], "retry": "reclaim"})
        else:
            response["reclaim"] = self._reclaim_checkout(owner_run_id=owner_run_id, run_id=run_id,
                                                         purpose="conclude", control=params, console_authority=console_authority)
        with self.db.read() as connection:
            run_row = self._run_row(connection, run_id)
            task = connection.execute("SELECT * FROM tasks WHERE task_id=?", (run_id,)).fetchone()
            response.update(self.compact(connection, run_row, task))
            response["targetRevision"] = run_row["revision"]
        return response

    def _stop_lead_for_conclusion(self, owner_run_id, target_id, params, console_authority) -> list[str]:
        """Stop the complete graph before preparing its atomic Host conclusions."""
        with self.db.write() as connection:
            owner = self._run_row(connection, owner_run_id)
            actor, initiator = self._authorize(connection, owner, params, console_authority=console_authority,
                                                action="A Host conclusion")
            lead = self._target_run(connection, owner, target_id)
            if not lead["delegation_authority"] or lead["state"] == "accepted":
                return []
            if lead["state"] == "concluded":
                # A replay may retry the cascade's blocked checkout removals;
                # it never records another conclusion or broadens public targets.
                return [row["child_task_id"] for row in reversed(self._owned_children(connection, target_id, include_lead=True))
                        if self._run_row(connection, row["child_task_id"])["state"] == "concluded"]
            conflict = self._open_conflict(connection, target_id)
            if conflict is not None:
                raise BoardError("CONFLICT", "Resolve the Lead workspace conflict before concluding its owned work",
                                 conflictId=conflict["conflict_id"], paths=json.loads(conflict["blocking_paths_json"]))
            task = connection.execute("SELECT * FROM tasks WHERE task_id=?", (target_id,)).fetchone()
            if lead["state"] not in ("failed", "cancelled", "delivered") or not self._stop_proven(connection, task):
                self._cancel_in_transaction(connection, lead, task, None, actor, initiator, self.now(),
                                            stop_facts={"leadRunId": target_id})
            else:
                self._cancel_children(connection, target_id, None, self.now(), actor=actor, initiator=initiator, include_lead=True,
                                      stop_facts={"leadRunId": target_id})
            descendants = [row["child_task_id"] for row in reversed(self._owned_children(connection, target_id, include_lead=True))
                           if self._run_row(connection, row["child_task_id"])["state"] not in ("accepted", "concluded")]
            summary = self.shutdown_summary(connection, target_id)
            head = self.board._head_of(connection)
        self.board._notify(head)
        if not summary["selfConfirmed"] or not summary["descendantsConfirmed"]:
            raise BoardError("SHUTDOWN_UNCONFIRMED", "The Lead and its owned work are stopping; retry the conclusion after every stop is confirmed",
                             unconfirmedRunIds=summary["unconfirmedRunIds"], unconfirmedCount=summary["unconfirmedCount"])
        return descendants

    def _seal_conclusion(self, run_id: str, work) -> dict | None:
        if work is None:
            return None
        manifest, allowed, attempt_id = work
        try:
            return workspace_module().host_seal(self.board.directory, manifest, run_id, attempt_id, allowed_paths=allowed)
        except BoardError as error:
            if error.code == "WORKSPACE_SCOPE_VIOLATION":
                raise BoardError(
                    "WORKSPACE_SCOPE_VIOLATION",
                    "The checkout holds changes outside the authorized write scope; conclude cannot seal them as a partial output",
                    paths=error.details.get("paths"),
                ) from error
            raise

    def _conclude_precheck(self, connection, run_row, task) -> dict:
        """Everything that must hold before one micro task may be concluded."""
        run_id = run_row["run_id"]
        if run_row["state"] in ("accepted", "concluded"):
            raise BoardError("CONFLICT", "A finished micro task cannot receive another conclusion", runId=run_id)
        if run_row["state"] not in ("failed", "cancelled", "delivered"):
            raise BoardError(
                "NOT_READY",
                "Only a failed, cancelled or delivered micro task can be concluded; a running turn or a Host decision "
                "boundary is not an execution result",
                runId=run_id,
                state=run_row["state"],
            )
        task = task or connection.execute("SELECT * FROM tasks WHERE task_id=?", (run_id,)).fetchone()
        self._require_routing_stopped(connection, run_id)
        self._lineage_stop_evidence(connection, run_row, "A Host conclusion")
        conflict = self._open_conflict(connection, run_id)
        if conflict is not None:
            raise BoardError(
                "CONFLICT",
                "Resolve the workspace conflict before concluding; an unresolved out-of-scope site is never "
                "sealed by a conclusion",
                conflictId=conflict["conflict_id"],
                paths=json.loads(conflict["blocking_paths_json"]),
            )
        if connection.execute("SELECT 1 FROM workflow_continuations WHERE run_id=? AND state IN ('recorded','queued') LIMIT 1", (run_id,)).fetchone():
            raise BoardError("CONFLICT", "A continuation is still pending")
        if connection.execute("SELECT 1 FROM workflow_children WHERE parent_run_id=? AND state IN ('active','attention') LIMIT 1", (run_id,)).fetchone():
            raise BoardError("CONFLICT", "Settle owned helpers before concluding")
        return task

    def _conclude_seal_work(self, connection, run_row, task):
        """The manifest, allowed paths and attempt of one Host partial seal, or None.

        ``None`` means there is no prepared workspace whose managed changes could
        be unsealed; a cancelled-before-preparation micro task concludes without a seal.
        """
        manifest_value = run_row["workspace_manifest_json"]
        if not manifest_value:
            return None
        manifest = json.loads(manifest_value)
        if not manifest.get("manifestSha256"):
            return None
        if not task["selected_attempt_id"]:
            # No execution attempt ever ran on this checkout; there is nothing that
            # could be unsealed, and a partial output needs an attempt identity.
            return None
        allowed = self._board_adopted_paths(run_row["run_id"], manifest)
        return manifest, allowed, task["selected_attempt_id"]

    def _conclude_record(self, connection, *, run_row, task, actor: dict, now: str, note: str | None,
                         command_id: str, request_key: dict, sealed_record: dict | None, reason: dict | None = None) -> dict:
        """Insert the conclusion row, the Host partial artifact and the events atomically."""
        run_id = run_row["run_id"]
        task = self._conclude_precheck(connection, run_row, task)
        attempt_id = task["selected_attempt_id"]
        turn = connection.execute(
            "SELECT * FROM workflow_turns WHERE run_id=? AND attempt_id=? ORDER BY turn_index DESC LIMIT 1",
            (run_id, attempt_id),
        ).fetchone()
        partial_artifact_id = None
        if sealed_record is not None:
            partial_artifact_id = self._pin_artifact(
                connection, run_id=run_id, kind="partial-output", manifest=sealed_record,
                attempt_id=attempt_id, turn_id=turn["turn_id"] if turn is not None else None,
                source_task_id=run_id, now=now,
            )
        identifier = "conclusion-" + str(uuid.uuid4())
        connection.execute(
            "INSERT INTO workflow_host_conclusions(conclusion_id,run_id,attempt_id,run_revision,owner_generation,"
            "execution_status,note,reason_code,reason_params_json,evidence_json,artifact_id,integration_id,actor_role,actor_id,command_id,created_at)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (identifier, run_id, attempt_id, run_row["revision"] + 1, run_row["owner_generation"], run_row["state"],
             note, reason["code"] if reason else None, canonical_json(reason["params"]) if reason else None,
             canonical_json({"references": []}),
             partial_artifact_id, None, actor["role"], actor["id"], command_id, now),
        )
        if sealed_record is not None:
            self.board._append_event(
                connection, "workflow.partial_output_preserved", task_id=run_id, attempt_id=attempt_id,
                payload={"partial": True, "verified": True, "final": False, "artifactId": partial_artifact_id,
                         "actor": actor},
            )
        self.board._append_event(
            connection, "workflow.host_concluded", task_id=run_id,
            attempt_id=attempt_id, revision=run_row["revision"] + 1,
            payload={"conclusionId": identifier, "executionStatus": run_row["state"], "actor": actor,
                     "partialArtifactId": partial_artifact_id, **({"reason": reason} if reason else {})},
        )
        self.board._transition_task(connection, task, "concluded")
        connection.execute("UPDATE tasks SET queue_reason=NULL WHERE task_id=?", (run_id,))
        connection.execute("UPDATE workflow_runs SET state='concluded' WHERE run_id=?", (run_id,))
        run_row = self._bump_run(connection, run_row, now)
        task = connection.execute("SELECT * FROM tasks WHERE task_id=?", (run_id,)).fetchone()
        view = self.compact(connection, run_row, task)
        response = {
            **view,
            "verdict": "concluded",
            "conclusion": host_conclusions.view(connection.execute(
                "SELECT * FROM workflow_host_conclusions WHERE conclusion_id=?", (identifier,)).fetchone()),
            "partialArtifactId": partial_artifact_id,
            "duplicate": False,
            "targetRunId": run_id,
            "targetRevision": run_row["revision"],
        }
        self.board._store_receipt(connection, command_id, "workflow.conclude", request_key, response,
                                  task_id=run_id, attempt_id=attempt_id)
        return response

    def reclaim(self, params: dict, *, console_authority: dict | None = None) -> dict:
        """Reclaim this micro task's own registered managed checkout.

        Used after an acceptance that kept the checkout, or after a reclaim that
        was blocked and whose blocker is now gone. Only the checkout this run
        registered is ever a deletion target; the same eligibility proof,
        convergence and interruption recovery as the internal reclaim apply. A
        blocked removal reports its concrete reasons and changes nothing.
        """
        schemas.reject_unknown(
            params,
            {"runId", "targetRunId", *schemas.CONTROL_FIELDS, schemas.CONSOLE_AUTHORITY_FIELD},
            "workflow.reclaim",
        )
        schemas.reject_untrusted_override(params)
        run_id = schemas.required_string(params, "runId", max_length=128)
        target_id = schemas.optional_string(params, "targetRunId", max_length=128) or run_id
        with self.db.read() as connection:
            owner = self._run_row(connection, run_id)
            self._authorize(connection, owner, params, console_authority=console_authority, action="A checkout reclaim")
            run_row = self._target_run(connection, owner, target_id)
            target_id = run_row["run_id"]
        result = self._reclaim_checkout(owner_run_id=run_id, run_id=target_id, purpose="reclaim", control=params,
                                       console_authority=console_authority)
        with self.db.read() as connection:
            run_row = self._run_row(connection, target_id)
            response = {**self.compact(connection, run_row), "targetRunId": target_id,
                        "targetRevision": run_row["revision"], **result}
        if not result.get("removed"):
            raise BoardError("NOT_READY", "This checkout is not eligible for reclaim", reasons=result.get("reasons"),
                             runId=run_id, reclaim=result)
        return response

    def _reclaim_checkout(self, *, owner_run_id: str, run_id: str, purpose: str, control: dict,
                          lead_conclusion: bool = False, console_authority: dict | None = None) -> dict:
        """Reclaim one owned checkout through the guarded internal removal journal.

        The two internal steps reuse the expiring plan only as the durable journal
        of one exact deletion: an applying plan fences concurrent capture and
        writer claims on that checkout, the removal converges when another owner
        completes it first, and an interrupted apply resumes. They are authorized
        by the originating Host's exact control triple against the controlling
        root or the authenticated console session, so a control takeover between
        the recorded review and the removal only
        blocks the removal (the review stays recorded) until the new owner
        retries; no synthetic authority plans a deletion. No Host-visible
        two-step path exists. A blocked removal keeps every recorded acceptance
        or conclusion and reports the concrete reasons. Only a committed Lead
        conclusion may reclaim its owned micro tasks through the larger stop
        graph; public targets remain confined to the helper graph.
        """
        with self.db.read() as connection:
            run_row = self._run_optional(connection, run_id)
            if run_row is None or not run_row["workspace_manifest_json"]:
                return {"removed": False, "reasons": ["workspace-not-prepared"]}
            revision = self._run_row(connection, owner_run_id)["revision"]
        command = f"reclaim-{purpose}-" + uuid.uuid4().hex
        control_triple = {key: control[key] for key in schemas.CONTROL_FIELDS if key in control}
        params = {"runId": owner_run_id, "targetRunId": run_id, "expectedRevision": revision,
                  "commandId": command, **control_triple}
        try:
            planned = self.cleanup_plan(params, console_authority=console_authority, _lead_conclusion=lead_conclusion)
        except BoardError as error:
            if error.code == "TASK_DELETING":
                raise
            return {"removed": False, "reasons": [error.code], "detail": error.message}
        plan = planned["plan"]
        if plan["state"] == "applied":
            result = (plan.get("result") or {})
            return {"removed": True, "alreadyRemoved": bool(result.get("alreadyRemoved")),
                    "path": plan["path"], "planId": plan["planId"], "retention": result["retention"]}
        if plan["state"] not in ("planned", "applying"):
            # An applying plan is not re-admitted here: cleanup_apply resumes it.
            blocked = {"removed": False, "reasons": plan.get("reasons") or ["blocked"], "planId": plan["planId"],
                       "path": plan["path"]}
            return blocked
        try:
            # Planning advances the target only. An independently allocated
            # helper's revision cannot stand in for its controlling root's.
            apply_revision = planned["targetRevision"] if owner_run_id == run_id else revision
            applied = self.cleanup_apply(
                {**params, "commandId": command + "-apply", "expectedRevision": apply_revision,
                 "planId": plan["planId"], "confirmPath": plan["path"]},
                console_authority=console_authority,
                _lead_conclusion=lead_conclusion,
            )
        except BoardError as error:
            if error.code == "TASK_DELETING":
                raise
            if error.code == "NOT_READY":
                blocked = {"removed": False, "reasons": error.details.get("reasons") or [error.code],
                           "planId": plan["planId"], "path": plan["path"]}
                if error.details.get("attachedBranch") is not None:
                    blocked["attachedBranch"] = error.details["attachedBranch"]
                if error.details.get("unsealedPaths"):
                    blocked["unsealedPaths"] = error.details["unsealedPaths"]
                return blocked
            return {"removed": False, "reasons": [error.code], "detail": error.message,
                    "planId": plan["planId"], "path": plan["path"]}
        now = self.now()
        with self.db.write() as connection:
            self._release_reservations(connection, run_id, now)
            head = self.board._head_of(connection)
        self.board._notify(head)
        return {"removed": True, "alreadyRemoved": bool(applied.get("plan", {}).get("result", {}).get("alreadyRemoved")),
                "path": plan["path"], "planId": plan["planId"], "retention": applied.get("retention")}

    # -- workspace lifecycle (Host-owned) ------------------------------------
    def _bump_run(self, connection, run_row, now: str):
        """Advance the micro-task run revision for a recorded lifecycle mutation."""
        updated = connection.execute(
            "UPDATE workflow_runs SET updated_at=?, revision=revision+1 WHERE run_id=? AND revision=?",
            (now, run_row["run_id"], run_row["revision"]),
        )
        if updated.rowcount != 1:
            raise BoardError("REVISION_CONFLICT", "The micro-task run changed concurrently; re-read it and retry")
        return self._run_row(connection, run_row["run_id"])

    def _find_integration(self, connection, run_id: str, artifact_id: str):
        """The newest verified integration record (or explicit not-required) of one artifact."""
        rows = connection.execute(
            "SELECT * FROM workflow_integrations WHERE run_id=? AND artifact_id=?"
            " AND state IN ('verified','not-required') ORDER BY created_at DESC, rowid DESC",
            (run_id, artifact_id),
        ).fetchall()
        if not rows:
            return None
        record = rows[0]
        artifact = connection.execute("SELECT * FROM workflow_artifacts WHERE artifact_id=?", (artifact_id,)).fetchone()
        manifest = json.loads(artifact["manifest_json"]) if artifact is not None else {}
        if record["source_commit"] and manifest.get("commit") and record["source_commit"] != manifest.get("commit"):
            raise BoardError("CONFLICT", "The integration record was verified against a different artifact",
                             integrationId=record["integration_id"], artifactCommit=manifest.get("commit"))
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
        if run_row["state"] in ("accepted", "concluded", "cancelled"):
            raise BoardError("CONFLICT", "A finished micro task cannot be reauthorized; submit a new micro task instead",
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
                "runId", "targetRunId", "commandId", "expectedRevision", "writeScope", "expectedScopeVersion",
                "note", *schemas.CONTROL_FIELDS, schemas.CONSOLE_AUTHORITY_FIELD,
            },
            "workflow.scope_amend",
        )
        schemas.reject_untrusted_override(params)
        owner_run_id = run_id = schemas.required_string(params, "runId", max_length=128)
        target_id = schemas.optional_string(params, "targetRunId", max_length=128) or run_id
        command_id = schemas.required_string(params, "commandId", max_length=128)
        expected = schemas.require_expected_revision(params)
        raw_scope = schemas.string_list(params, "writeScope", limit=256)
        if not raw_scope:
            raise BoardError("INVALID_ARGUMENT", "writeScope must name at least one relative workspace path")
        write_scope = workspace_module().normalize_scope(raw_scope)
        expected_scope = params.get("expectedScopeVersion")
        if isinstance(expected_scope, bool) or not isinstance(expected_scope, int) or expected_scope < 1:
            raise BoardError("INVALID_ARGUMENT", "expectedScopeVersion must be a positive integer")
        note, _size = schemas.bounded_text(params, "note", max_bytes=schemas.MAX_NOTE_BYTES)
        request_key = {"runId": run_id, "targetRunId": target_id, "expectedScopeVersion": expected_scope,
                       "writeScope": write_scope, "note": note, "expectedRevision": expected}
        with self.db.read() as connection:
            receipt_entry = self.board._receipt_entry(connection, command_id, "workflow.scope_amend", request_key)
            if receipt_entry is not None and receipt_entry["deletion_id"] is not None:
                return self.board._receipt_response(connection, receipt_entry)
            owner = self._run_row(connection, run_id)
            self._authorize(connection, owner, params, console_authority=console_authority, action="A scope amendment")
            receipt = self.board._receipt_response(connection, receipt_entry)
            if receipt is not None:
                return {**receipt, "duplicate": True}
            self._expect_revision(owner, expected)
            target_snapshot = self._target_run(connection, owner, target_id)
            self._scope_precheck(connection, target_snapshot, expected_scope, "A scope amendment")
        now = self.now()
        with self.db.write() as connection:
            receipt_entry = self.board._receipt_entry(connection, command_id, "workflow.scope_amend", request_key)
            if receipt_entry is not None and receipt_entry["deletion_id"] is not None:
                return self.board._receipt_response(connection, receipt_entry)
            owner = self._run_row(connection, owner_run_id)
            actor, _initiator = self._authorize(connection, owner, params, console_authority=console_authority, action="A scope amendment")
            receipt = self.board._receipt_response(connection, receipt_entry)
            if receipt is not None:
                return {**receipt, "duplicate": True}
            self._expect_revision(owner, expected)
            run_row = self._target_run(connection, owner, target_id)
            run_id = run_row["run_id"]
            self._expect_revision(run_row, target_snapshot["revision"])
            evidence = self._scope_precheck(connection, run_row, expected_scope, "A scope amendment")
            scope = self._current_scope(connection, run_row)
            version = scope["scopeVersion"] + 1
            connection.execute(
                "INSERT INTO workflow_scope_versions(run_id, scope_version, command_id, actor_role, actor_id, note, write_scope_json,"
                " manifest_sha256, stopped_evidence_json, created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (run_id, version, command_id, actor["role"], actor["id"], note, canonical_json(write_scope),
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
                "targetRunId": run_id,
                "targetRevision": run_row["revision"],
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

    @staticmethod
    def _resolution_delivery_view(conflict) -> dict | None:
        try:
            delivery = json.loads(conflict["delivery_json"]) if conflict["delivery_json"] else None
        except (TypeError, ValueError):
            return None
        return delivery if isinstance(delivery, dict) and delivery.get("state") == "delivered" else None

    @staticmethod
    def _resolution_delivery_result(resolution) -> dict:
        delivery = resolution["delivery"]
        return {
            "conflictId": resolution["conflict"]["conflict_id"],
            "state": delivery.get("state"),
            "kind": delivery.get("kind"),
            "action": delivery.get("action"),
            "artifactId": delivery.get("artifactId"),
            "attemptId": delivery.get("attemptId"),
            "turnId": delivery.get("turnId"),
            "turnOutcome": delivery.get("turnOutcome"),
            "deliveredAt": delivery.get("deliveredAt"),
        }

    def _resolution_native_completion(self, connection, run_row, conflict):
        """The failed attempt's own untouched native completed outcome, when the only failure was the seal.

        The attempt, its turn and its command receipts are never rewritten: this
        reads the immutable reported payload and revalidates the turn record against
        the service-owned turn row. A missing turn, an unconfirmed shutdown, a
        different failure, or a turn record that no longer validates returns None,
        so a genuine harness failure can never be converted into a delivery.
        """
        attempt = connection.execute(
            "SELECT * FROM attempts WHERE attempt_id=?", (conflict["attempt_id"],)
        ).fetchone()
        if attempt is None or attempt["result_json"] is None or not attempt["shutdown_confirmed"]:
            return None
        try:
            stored = json.loads(attempt["result_json"])
        except (TypeError, ValueError):
            return None
        if not isinstance(stored, dict) or stored.get("status") != "failed":
            return None
        envelope_error = stored.get("error")
        if not isinstance(envelope_error, str) or not envelope_error.startswith("WORKSPACE_SCOPE_VIOLATION:"):
            return None
        payload = self.result_payload(stored)
        seal_error = payload.get("workspaceSealError")
        if not isinstance(seal_error, str) or not seal_error.startswith("WORKSPACE_SCOPE_VIOLATION:"):
            return None
        if payload.get("workspaceSeal") is not None or payload.get("turnError") is not None:
            return None
        runner_error = payload.get("error")
        if runner_error is not None and runner_error != seal_error:
            return None
        record = payload.get("turn")
        if not isinstance(record, dict):
            return None
        turn_row = connection.execute(
            "SELECT * FROM workflow_turns WHERE turn_id=?", (conflict["turn_id"],)
        ).fetchone()
        if (turn_row is None or turn_row["run_id"] != run_row["run_id"]
                or turn_row["attempt_id"] != attempt["attempt_id"]):
            return None
        try:
            rejection = self._validate_turn(record, turn_row, attempt)
        except BoardError:
            return None
        if rejection is not None:
            return None
        outcome = record.get("outcome") if isinstance(record.get("outcome"), dict) else {}
        disposition = outcome.get("disposition")
        if disposition not in ("completed", "assistance", "attention"):
            return None
        return {"attempt": attempt, "turnRow": turn_row, "turn": record, "disposition": disposition,
                "sealError": seal_error}

    def _resolution_delivery(self, connection, run_row, attempt, artifact):
        """The recorded Host-resolution delivery of one resolved artifact, revalidated.

        Acknowledgement accepts a resolved artifact only through this record: the
        conflict must be a completed restore or adopt, its delivery record must name
        this artifact, and the original native completed outcome must still validate.
        """
        if artifact["kind"] != "resolved-output":
            return None
        conflicts = connection.execute(
            "SELECT * FROM workflow_workspace_conflicts WHERE run_id=? AND attempt_id=? AND artifact_id=?"
            " AND state IN ('restored','adopted') ORDER BY rowid DESC",
            (run_row["run_id"], attempt["attempt_id"], artifact["artifact_id"]),
        ).fetchall()
        for conflict in conflicts:
            delivery = self._resolution_delivery_view(conflict)
            if delivery is None or delivery.get("artifactId") != artifact["artifact_id"]:
                continue
            proof = self._resolution_native_completion(connection, run_row, conflict)
            if proof is None or proof["disposition"] != "completed":
                continue
            return {"conflict": conflict, "delivery": delivery, "proof": proof}
        return None

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
                "runId", "targetRunId", "commandId", "expectedRevision", "conflictId", "action", "paths",
                "observedFingerprint", "note", *schemas.CONTROL_FIELDS, schemas.CONSOLE_AUTHORITY_FIELD,
            },
            "workflow.workspace_resolve",
        )
        schemas.reject_untrusted_override(params)
        owner_run_id = run_id = schemas.required_string(params, "runId", max_length=128)
        target_id = schemas.optional_string(params, "targetRunId", max_length=128) or run_id
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
        note = (schemas.bounded_text(params, "note", max_bytes=schemas.MAX_NOTE_BYTES)[0]
                if params.get("note") is not None else None)
        if action in ("adopt", "abandon"):
            if paths:
                raise BoardError("INVALID_ARGUMENT", f"{action} binds the whole recorded site and takes no path selection")
            if not note:
                raise BoardError("INVALID_ARGUMENT", f"{action} requires an explicit note")
        request_key = {"runId": run_id, "targetRunId": target_id, "conflictId": conflict_id, "action": action,
                       "paths": paths, "observedFingerprint": observed, "note": note, "expectedRevision": expected}
        with self.db.read() as connection:
            receipt_entry = self.board._receipt_entry(connection, command_id, "workflow.workspace_resolve", request_key)
            if receipt_entry is not None and receipt_entry["deletion_id"] is not None:
                return self.board._receipt_response(connection, receipt_entry)
            owner = self._run_row(connection, run_id)
            actor, _initiator = self._authorize(connection, owner, params, console_authority=console_authority, action="A workspace resolution")
            receipt = self.board._receipt_response(connection, receipt_entry)
            if receipt is not None:
                return {**receipt, "duplicate": True}
            self._expect_revision(owner, expected)
            target_snapshot = self._target_run(connection, owner, target_id)
            conflict = self._conflict_row(connection, target_id, conflict_id)
            if conflict["state"] != "open":
                if conflict["action"] == action:
                    return self._resolved_duplicate(connection, target_snapshot, conflict)
                raise BoardError("CONFLICT", "This conflict already has a different recorded resolution",
                                 conflictId=conflict_id, state=conflict["state"], action=conflict["action"])
            self._lineage_stop_evidence(connection, target_snapshot, "A workspace resolution")
            manifest = self._conflict_manifest(connection, target_snapshot, conflict)
            original = connection.execute(
                "SELECT manifest_json FROM workflow_artifacts WHERE run_id=? AND kind='input' ORDER BY rowid LIMIT 1",
                (target_id,),
            ).fetchone()
            if original is None:
                raise BoardError("NOT_READY", "Workspace resolution requires the micro task's fixed original input")
            original_commit = json.loads(original["manifest_json"])["inputCommit"]
        try:
            result = workspace_module().resolve(
                self.board.directory, manifest, task_id=target_id,
                attempt_id=conflict["attempt_id"], action=action, paths=paths,
                observed_fingerprint=observed, note=note, actor=actor,
            )
            resolved_artifact = result.get("artifact")
            if action != "abandon" and isinstance(resolved_artifact, dict):
                resolved_artifact["cumulativePatch"] = workspace_module().cumulative_patch(
                    manifest, resolved_artifact, original_commit)
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
            receipt_entry = self.board._receipt_entry(connection, command_id, "workflow.workspace_resolve", request_key)
            if receipt_entry is not None and receipt_entry["deletion_id"] is not None:
                return self.board._receipt_response(connection, receipt_entry)
            owner = self._run_row(connection, owner_run_id)
            actor, _initiator = self._authorize(connection, owner, params, console_authority=console_authority, action="A workspace resolution")
            receipt = self.board._receipt_response(connection, receipt_entry)
            if receipt is not None:
                return {**receipt, "duplicate": True}
            self._expect_revision(owner, expected)
            run_row = self._target_run(connection, owner, target_id)
            run_id = run_row["run_id"]
            self._expect_revision(run_row, target_snapshot["revision"])
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
            proof = (self._resolution_native_completion(connection, run_row, current)
                     if result["state"] in ("restored", "adopted") else None)
            delivery = None
            if proof is not None and proof["disposition"] == "completed":
                # The only failure was the refused scope seal and this attempt's own
                # native completed outcome survives intact, so the resolved artifact is
                # the delivered result of this micro task: it can be integrated and
                # acknowledged without another model turn. The failed attempt, its
                # failed turn and its command receipts stay exactly as they were; the
                # Host-resolution delivery is recorded separately on the conflict.
                delivery = {"state": "delivered", "kind": "host-resolution", "action": action,
                            "artifactId": artifact_id, "attemptId": current["attempt_id"],
                            "turnId": current["turn_id"], "turnOutcome": proof["disposition"],
                            "resolvedFingerprint": result["resolvedFingerprint"], "commandId": command_id,
                            "deliveredAt": now}
            connection.execute(
                "UPDATE workflow_workspace_conflicts SET state=?, action=?, resolved_paths_json=?,"
                " conflicting_paths_json=?, artifact_id=?, output_commit=?, output_tree=?, actor_role=?, actor_id=?, note=?,"
                " command_id=?, delivery_json=?, updated_at=? WHERE conflict_id=? AND state='open'",
                (result["state"], action, canonical_json(result["resolvedPaths"]),
                 canonical_json(result["remainingPaths"]), artifact_id, (record or {}).get("commit"),
                 (record or {}).get("tree"), actor["role"], actor["id"], note, command_id,
                 canonical_json(delivery) if delivery is not None else None, now, conflict_id),
            )
            closed_requests = []
            if result["state"] in ("restored", "adopted", "abandoned"):
                # The Host answered exactly this boundary; close only the requests
                # that pointed at this recorded conflict.
                for request in connection.execute(
                    "SELECT * FROM workflow_requests WHERE run_id=? AND state='open' AND kind='attention'", (run_id,)
                ).fetchall():
                    if request["reason_code"] != "workspace-scope-violation":
                        continue
                    if conflict_id not in json.loads(request["reason_params_json"])["conflictIds"]:
                        continue
                    connection.execute(
                        "UPDATE workflow_requests SET state='approved', decision_json=?, decided_at=?, updated_at=?"
                        " WHERE request_id=? AND state='open'",
                        (canonical_json({"resolution": {"conflictId": conflict_id, "action": action,
                                                        "state": result["state"], "actor": actor}}),
                         now, now, request["request_id"]),
                    )
                    closed_requests.append(request["request_id"])
            for closed_id in closed_requests:
                self._close_proxy_ancestors(connection, self._request_row(connection, run_id, closed_id), now)
            self.board._append_event(
                connection,
                "workflow.workspace_resolved",
                task_id=run_id,
                attempt_id=current["attempt_id"],
                revision=run_row["revision"] + 1,
                payload={"conflictId": conflict_id, "action": action, "state": result["state"],
                         "resolvedPaths": result["resolvedPaths"][:32], "remainingPaths": result["remainingPaths"][:32],
                         "artifactId": artifact_id, "actor": actor, "delivered": delivery is not None},
            )
            boundary_request_id = None
            if delivery is not None:
                connection.execute(
                    "UPDATE workflow_runs SET state='delivered', final_attempt_id=?, final_artifact_id=?,"
                    " current_turn_id=?, current_attempt_id=NULL, active_request_id=NULL, updated_at=?"
                    " WHERE run_id=?",
                    (current["attempt_id"], artifact_id, current["turn_id"], now, run_id),
                )
                connection.execute("UPDATE tasks SET queue_reason=NULL, updated_at=? WHERE task_id=?", (now, run_id))
                run_row = self._bump_run(connection, run_row, now)
            elif proof is not None and proof["disposition"] in ("assistance", "attention"):
                # The native turn genuinely yielded to the Host. The scope conflict is
                # settled, but that request is the next logical boundary and is opened
                # exactly as the completed turn would have opened it.
                boundary_request_id = self._record_request(
                    connection, run_row, proof["turnRow"], proof["turn"], proof["disposition"], now
                )
                connection.execute("UPDATE tasks SET queue_reason='awaiting-host', updated_at=? WHERE task_id=?",
                                   (now, run_id))
                self._propagate_boundary(connection, self._request_row(connection, run_id, boundary_request_id), now)
                run_row = self._run_row(connection, run_id)
            else:
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
                "delivered": delivery is not None,
                "delivery": delivery,
                "nextBoundaryRequestId": boundary_request_id,
                "targetRunId": run_id,
                "targetRevision": run_row["revision"],
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
        delivery = self._resolution_delivery_view(conflict)
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
            "delivered": delivery is not None,
            "delivery": delivery,
            "nextBoundaryRequestId": None,
            "targetRunId": run_row["run_id"],
            "targetRevision": run_row["revision"],
        }

    @staticmethod
    def _allocation_provenance(connection, run_row) -> list[dict]:
        """Authoritative retained manifests that can prove one run's physical allocation.

        Only material the board already pinned for this run is used: pinned input
        artifacts, the canonical input of every turn and every prepared
        continuation manifest. A bare cwd that happens to sit under
        ``state/workspaces`` is never provenance, and a helper run that merely
        borrowed its parent's checkout has no worktree manifest of its own.
        """
        manifests: list[dict] = []
        seen: set[str] = set()
        for query in (
            "SELECT manifest_json AS body FROM workflow_artifacts WHERE run_id=? ORDER BY rowid LIMIT 128",
            "SELECT input_json AS body FROM workflow_turns WHERE run_id=? ORDER BY turn_index LIMIT 128",
            "SELECT workspace_manifest_json AS body FROM workflow_continuations WHERE run_id=? ORDER BY rowid LIMIT 128",
        ):
            for row in connection.execute(query, (run_row["run_id"],)).fetchall():
                body = row["body"]
                if not body:
                    continue
                try:
                    document = json.loads(body)
                except (TypeError, ValueError):
                    continue
                if not isinstance(document, dict):
                    continue
                candidate = document.get("executionWorkspace")
                if not isinstance(candidate, dict):
                    candidate = document if "workspaceId" in document and "snapshot" in document else None
                if not isinstance(candidate, dict):
                    continue
                identity = str(candidate.get("manifestSha256") or candidate.get("workspaceId") or "")
                if identity and identity in seen:
                    continue
                seen.add(identity)
                manifests.append(candidate)
        return manifests

    def _cleanup_reasons(self, connection, run_row, task, manifest: dict, *,
                         allocation: dict | None = None) -> tuple[list[str], dict, dict]:
        """Everything that must hold before one disposable checkout may be removed."""
        reasons = []
        conclusion = host_conclusions.current(connection, run_row)
        if run_row["state"] not in ("accepted", "concluded"):
            reasons.append("not-accepted")
        final_artifact = None
        if run_row["final_artifact_id"]:
            final_artifact = connection.execute(
                "SELECT * FROM workflow_artifacts WHERE artifact_id=? AND run_id=?",
                (run_row["final_artifact_id"], run_row["run_id"]),
            ).fetchone()
        integration = (self._find_integration(connection, run_row["run_id"], final_artifact["artifact_id"])
                       if final_artifact is not None else None)
        if final_artifact is None and conclusion is None:
            reasons.append("final-artifact-missing")
        elif integration is None and conclusion is None:
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
        if manifest.get("checkoutId"):
            if int(connection.execute(
                "SELECT COUNT(*) AS count FROM workspace_reservations WHERE checkout_id=?"
                " AND state IN ('held','transferred') AND holder_task_id!=?",
                (manifest["checkoutId"], run_row["run_id"]),
            ).fetchone()["count"]):
                reasons.append("workspace-dependency")
        evidence = {
            "hostConclusion": host_conclusions.view(conclusion),
            "ownerGeneration": run_row["owner_generation"],
            "attemptId": task["selected_attempt_id"],
            "acceptance": {"state": run_row["state"], "acceptedAt": task["accepted_at"],
                           "verdict": task["acceptance_verdict"]},
            "shutdown": summary,
            "finalArtifactId": run_row["final_artifact_id"],
            "integrationId": integration["integration_id"] if integration is not None else None,
        }
        retention = {
            # The physical allocation owns the disposable worktree; the logical
            # directory of the current turn/input snapshot is retained alongside it.
            "workspaceDirectory": str(self.board.directory / "workspaces"
                                      / ((allocation or {}).get("workspaceId") or manifest.get("workspaceId") or "")),
            "logicalWorkspaceDirectory": str(self.board.directory / "workspaces" / (manifest.get("workspaceId") or "")),
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
            cumulative = pinned.get("cumulativePatch")
            if isinstance(cumulative, dict) and isinstance(cumulative.get("path"), str):
                retention["outputPatches"].append(cumulative["path"])
            if isinstance(pinned.get("ref"), str):
                retention["fixedRefs"].append(pinned["ref"])
        return reasons, evidence, retention

    def cleanup_plan(self, params: dict, *, console_authority: dict | None = None,
                     _lead_conclusion: bool = False) -> dict:
        """Plan the removal of exactly one registered disposable hey-my-buddy checkout.

        The plan names the allocation and checks board eligibility without reading
        Git. Execution proves the live checkout and records its retention evidence
        before entering applying. Repeated planning returns the same live plan.
        """
        schemas.reject_unknown(
            params,
            {"runId", "targetRunId", "commandId", "expectedRevision", *schemas.CONTROL_FIELDS,
             schemas.CONSOLE_AUTHORITY_FIELD},
            "workflow.cleanup_plan",
        )
        schemas.reject_untrusted_override(params)
        owner_run_id = run_id = schemas.required_string(params, "runId", max_length=128)
        target_id = schemas.optional_string(params, "targetRunId", max_length=128) or run_id
        command_id = schemas.required_string(params, "commandId", max_length=128)
        expected = schemas.require_expected_revision(params)
        request_key = {"runId": run_id, "targetRunId": target_id, "expectedRevision": expected}
        with self.db.read() as connection:
            receipt_entry = self.board._receipt_entry(connection, command_id, "workflow.cleanup_plan", request_key)
            if receipt_entry is not None and receipt_entry["deletion_id"] is not None:
                return self.board._receipt_response(connection, receipt_entry)
            owner = self._run_row(connection, run_id)
            actor, _initiator = self._authorize(connection, owner, params, console_authority=console_authority, action="A cleanup plan")
            receipt = self.board._receipt_response(connection, receipt_entry)
            if receipt is not None:
                return {**receipt, "duplicate": True}
            self._expect_revision(owner, expected)
            run_row = self._target_run(connection, owner, target_id, include_lead=_lead_conclusion)
            run_id = target_id
            target_snapshot = run_row
            task = connection.execute("SELECT * FROM tasks WHERE task_id=?", (run_id,)).fetchone()
            manifest = json.loads(run_row["workspace_manifest_json"] or "{}")
            if not manifest:
                raise BoardError("NOT_READY", "This run has no prepared workspace to clean up", runId=run_id)
            retained = self._allocation_provenance(connection, run_row)
            reasons, evidence, retention = self._cleanup_reasons(connection, run_row, task, manifest)
        module = workspace_module()
        allocation = module.resolve_allocation(self.board.directory, manifest, retained)
        workspace = {
            "workspaceId": (allocation or manifest)["workspaceId"],
            "manifestWorkspaceId": manifest["workspaceId"],
            "kind": (allocation or manifest)["kind"],
            "checkoutId": manifest["checkoutId"], "repositoryId": manifest["repositoryId"],
            "path": allocation["path"] if allocation else manifest["checkoutRoot"],
            "cwd": manifest["path"], "allocation": allocation,
        }
        if allocation is None:
            reasons.append("not-a-managed-worktree")
            if (manifest["kind"] == "worktree"
                    and Path(manifest["checkoutRoot"]) != self.board.directory / "workspaces" / manifest["workspaceId"] / "checkout"):
                reasons.append("unsafe-path")
        elif not module._inside(manifest["path"], allocation["path"]):
            reasons.append("unsafe-path")
        if allocation is not None:
            retention["workspaceDirectory"] = str(self.board.directory / "workspaces"
                                                  / allocation["workspaceId"])
        reasons = sorted(set(reasons))
        retention["checkoutRoot"] = workspace["path"]
        retention["cwd"] = workspace["cwd"]
        evidence = {**evidence, "workspace": workspace}
        now = self.now()
        plan_id = f"cln-{uuid.uuid4()}"
        expires_at = self._expiry(CLEANUP_PLAN_TTL_SECONDS)
        with self.db.write() as connection:
            receipt_entry = self.board._receipt_entry(connection, command_id, "workflow.cleanup_plan", request_key)
            if receipt_entry is not None and receipt_entry["deletion_id"] is not None:
                return self.board._receipt_response(connection, receipt_entry)
            owner = self._run_row(connection, owner_run_id)
            actor, _initiator = self._authorize(connection, owner, params, console_authority=console_authority, action="A cleanup plan")
            receipt = self.board._receipt_response(connection, receipt_entry)
            if receipt is not None:
                return {**receipt, "duplicate": True}
            reject_deleting_run(connection, target_id)
            self._expect_revision(owner, expected)
            run_row = self._target_run(connection, owner, target_id, include_lead=_lead_conclusion)
            run_id = run_row["run_id"]
            self._expect_revision(run_row, target_snapshot["revision"])
            existing = connection.execute(
                "SELECT * FROM workspace_cleanup_plans WHERE run_id=? AND checkout_id=?"
                " AND state IN ('planned','applying','applied') ORDER BY rowid DESC LIMIT 1",
                (run_id, manifest.get("checkoutId")),
            ).fetchone()
            if existing is not None and (existing["state"] != "planned" or existing["expires_at"] >= now):
                return {**self.compact(connection, run_row), "duplicate": True, "plan": self._plan_view(existing),
                        "targetRunId": run_id, "targetRevision": run_row["revision"]}
            connection.execute(
                "INSERT INTO workspace_cleanup_plans(plan_id, run_id, workspace_id, checkout_id, repository_id, path,"
                " kind, state, evidence_json, retention_json, reasons_json, command_id, actor_role, actor_id, revision, created_at,"
                " expires_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,1,?,?)",
                (plan_id, run_id, workspace["workspaceId"], workspace["checkoutId"], workspace["repositoryId"],
                 workspace["path"], workspace["kind"],
                 "blocked" if reasons else "planned", canonical_json(evidence), canonical_json(retention),
                 canonical_json(reasons), command_id, actor["role"], actor["id"], now, expires_at),
            )
            self.board._append_event(
                connection,
                "workflow.cleanup_planned",
                task_id=run_id,
                revision=run_row["revision"] + 1,
                payload={"planId": plan_id, "path": workspace["path"], "eligible": not reasons, "reasons": reasons},
            )
            plan = connection.execute("SELECT * FROM workspace_cleanup_plans WHERE plan_id=?", (plan_id,)).fetchone()
            run_row = self._bump_run(connection, run_row, now)
            response = {**self.compact(connection, run_row), "duplicate": False, "plan": self._plan_view(plan),
                        "targetRunId": run_id, "targetRevision": run_row["revision"]}
            self.board._store_receipt(connection, command_id, "workflow.cleanup_plan", request_key, response, task_id=run_id)
            head = self.board._head_of(connection)
        self.board._notify(head)
        return response

    def _expiry(self, seconds: int) -> str:
        from datetime import datetime, timedelta
        moment = datetime.fromisoformat(self.now().replace("Z", "+00:00")) + timedelta(seconds=seconds)
        return moment.isoformat(timespec="milliseconds").replace("+00:00", "Z")

    @staticmethod
    def _failed_attempt_ids(connection, micro_task_id: str) -> frozenset[str]:
        """Only a successful, explicitly completed result permits evidence reclamation."""
        failed = set()
        for row in connection.execute(
            'SELECT attempt_id, result_json FROM attempts WHERE task_id=?', (micro_task_id,)
        ):
            result = json.loads(row['result_json'] or '{}')
            if result.get('status') != 'ok' or result.get('terminationReason') != 'completed':
                failed.add(row['attempt_id'])
        return frozenset(failed)

    @staticmethod
    @contextmanager
    def _cleanup_allocation_lock(state_dir: Path, allocation: dict | None):
        """Share the original allocation fence with macro task deletion marking."""
        module = workspace_module()
        directory = module.allocation_lock_directory(
            state_dir, allocation["workspaceId"] if allocation is not None else None
        )
        if directory is None:
            yield
            return
        with module._lock(directory, create=False):
            yield

    def cleanup_apply(self, params: dict, *, console_authority: dict | None = None,
                      _lead_conclusion: bool = False) -> dict:
        """Apply one unexpired cleanup plan to exactly its registered checkout.

        Acceptance, integration, shutdown, dependency and unsealed-change evidence
        are rechecked against the live board and the actual worktree immediately
        before the single ``git worktree remove`` of the exact path. Outputs,
        manifests, Git refs, patches and receipts are outside this deletion.
        """
        schemas.reject_unknown(
            params,
            {"runId", "targetRunId", "planId", "commandId", "expectedRevision", "confirmPath", *schemas.CONTROL_FIELDS,
             schemas.CONSOLE_AUTHORITY_FIELD},
            "workflow.cleanup_apply",
        )
        schemas.reject_untrusted_override(params)
        owner_run_id = run_id = schemas.required_string(params, "runId", max_length=128)
        target_id = schemas.optional_string(params, "targetRunId", max_length=128) or run_id
        plan_id = schemas.required_string(params, "planId", max_length=128)
        command_id = schemas.required_string(params, "commandId", max_length=128)
        expected = schemas.require_expected_revision(params)
        confirm_path = schemas.required_string(params, "confirmPath", max_length=4096)
        request_key = {"runId": run_id, "targetRunId": target_id, "planId": plan_id, "confirmPath": confirm_path,
                       "expectedRevision": expected}
        with self.db.read() as connection:
            receipt_entry = self.board._receipt_entry(connection, command_id, "workflow.cleanup_apply", request_key)
            if receipt_entry is not None and receipt_entry["deletion_id"] is not None:
                return self.board._receipt_response(connection, receipt_entry)
            owner = self._run_row(connection, run_id)
            self._authorize(connection, owner, params, console_authority=console_authority, action="A cleanup apply")
            receipt = self.board._receipt_response(connection, receipt_entry)
            if receipt is not None:
                return {**receipt, "duplicate": True}
            self._expect_revision(owner, expected)
            run_row = self._target_run(connection, owner, target_id, include_lead=_lead_conclusion)
            run_id = target_id
            target_snapshot = run_row
            plan = connection.execute(
                "SELECT * FROM workspace_cleanup_plans WHERE plan_id=? AND run_id=?", (plan_id, run_id)
            ).fetchone()
            if plan is None:
                raise BoardError("NOT_FOUND", "Unknown cleanup plan for this run", planId=plan_id)
            if confirm_path != plan["path"]:
                raise BoardError("INVALID_ARGUMENT", "confirmPath must exactly match the planned checkout path",
                                 plannedPath=plan["path"])
            resume = self._cleanup_apply_precheck(connection, run_row, plan)
            task = connection.execute("SELECT * FROM tasks WHERE task_id=?", (run_id,)).fetchone()
            manifest = json.loads(run_row["workspace_manifest_json"] or "{}")
            retained = self._allocation_provenance(connection, run_row)
            allocation = workspace_module().resolve_allocation(self.board.directory, manifest, retained)
            sealed = self._latest_handoff(connection, run_row, manifest)
            # Board eligibility proves the whole owned lineage stopped. Reclaim
            # only sessions bound to the checkout being removed, including
            # routing tasks and helpers that borrowed this exact allocation.
            private_micro_tasks = [run_id]
            for child in self._owned_children(connection, run_id):
                child_run = connection.execute("SELECT workspace_manifest_json FROM workflow_runs WHERE run_id=?",
                                               (child['child_task_id'],)).fetchone()
                if child_run is not None:
                    child_manifest = json.loads(child_run['workspace_manifest_json'] or '{}')
                    if child_manifest.get('checkoutRoot') == plan['path']:
                        private_micro_tasks.append(child['child_task_id'])
            for micro_task_id in list(private_micro_tasks):
                private_micro_tasks.extend(task['task_id'] for task in self._routing_tasks(connection, micro_task_id))
            failed_attempts = {micro_task_id: self._failed_attempt_ids(connection, micro_task_id)
                               for micro_task_id in dict.fromkeys(private_micro_tasks)}
            session_attempts = {micro_task_id: tuple(row['attempt_id'] for row in connection.execute(
                'SELECT attempt_id FROM attempts WHERE task_id=?', (micro_task_id,)))
                for micro_task_id in failed_attempts}
        with self._cleanup_allocation_lock(self.board.directory, allocation):
            with self.db.read() as connection:
                reject_deleting_run(connection, target_id)
            path = Path(plan["path"])
            inspection = None
            if path.exists() or path.is_symlink():
                inspection = workspace_module().cleanup_inspect(
                    self.board.directory, manifest, sealed=sealed,
                    retained=retained, allowed=self._board_adopted_paths(run_id, manifest), resume=resume)
                # The registered owner of this allocation may complete the same removal
                # while the eligibility is proven, or the checkout may already have been
                # deleted out of band. A path that is factually gone leaves nothing to
                # delete, so its reasons cannot block; anything still occupying the exact
                # planned path keeps every NOT_READY reason.
            blocked = inspection is not None and bool(inspection["reasons"]) and (path.exists() or path.is_symlink())
            if blocked:
                details = {"reasons": inspection["reasons"], "unsealedPaths": inspection["unsealedPaths"]}
                if inspection.get("attachedBranch") is not None:
                    details["attachedBranch"] = inspection["attachedBranch"]
                refusal = BoardError("NOT_READY", "This checkout is not eligible for cleanup", **details)
            now = self.now()
            with self.db.write() as connection:
                receipt_entry = self.board._receipt_entry(connection, command_id, "workflow.cleanup_apply", request_key)
                if receipt_entry is not None and receipt_entry["deletion_id"] is not None:
                    return self.board._receipt_response(connection, receipt_entry)
                owner = self._run_row(connection, owner_run_id)
                self._authorize(connection, owner, params, console_authority=console_authority, action="A cleanup apply")
                receipt = self.board._receipt_response(connection, receipt_entry)
                if receipt is not None:
                    return {**receipt, "duplicate": True}
                self._expect_revision(owner, expected)
                run_row = self._target_run(connection, owner, target_id, include_lead=_lead_conclusion)
                run_id = run_row["run_id"]
                self._expect_revision(run_row, target_snapshot["revision"])
                plan = connection.execute(
                    "SELECT * FROM workspace_cleanup_plans WHERE plan_id=? AND run_id=?", (plan_id, run_id)
                ).fetchone()
                if plan["state"] == "applied":
                    return {**self.compact(connection, run_row), "duplicate": True, "plan": self._plan_view(plan),
                            "targetRunId": run_id, "targetRevision": run_row["revision"]}
                self._cleanup_apply_precheck(connection, run_row, plan)
                if blocked and plan["state"] == "applying":
                    # A started removal keeps its fence even when its resumption is
                    # refused; no part of that journal may be rewritten here.
                    raise refusal
                if plan["state"] == "planned":
                    evidence = json.loads(plan["evidence_json"])
                    retention = json.loads(plan["retention_json"])
                    if inspection is not None:
                        evidence["workspace"] = {key: inspection.get(key) for key in
                                                 ("eligible", "reasons", "kind", "checkoutId", "repositoryId", "worktree",
                                                  "locked", "unsealedPaths", "sealedObservation", "workspaceId",
                                                  "manifestWorkspaceId", "allocation", "attachedBranch")}
                        retention["fixedRefs"] = sorted(set(retention["fixedRefs"]) | set(inspection["refs"]))
                        retention["checkoutRoot"] = inspection["path"]
                        retention["cwd"] = inspection["cwd"]
                    connection.execute(
                        "UPDATE workspace_cleanup_plans SET state=?, evidence_json=?, retention_json=?, reasons_json=?,"
                        " command_id=?, revision=revision+1 WHERE plan_id=? AND state='planned'",
                        ("blocked" if blocked else "applying", canonical_json(evidence), canonical_json(retention),
                         canonical_json(inspection["reasons"] if blocked else []), command_id, plan_id),
                    )
                head = self.board._head_of(connection)
            self.board._notify(head)
        if blocked:
            raise refusal
        removed = None
        if path.exists() or path.is_symlink():
            removed = workspace_module().cleanup_remove(self.board.directory, manifest, retained=retained, resume=resume)
        from ... import private_dirs
        for micro_task_id, attempt_ids in failed_attempts.items():
            for adapter in private_dirs.ADAPTERS:
                for attempt_id in attempt_ids:
                    private_dirs.cleanup_attempt_credentials(self.board.directory, adapter, micro_task_id, attempt_id)
                private_dirs.remove_tree(
                    private_dirs.micro_task_root(self.board.directory, adapter, micro_task_id),
                    keep=frozenset(private_dirs.attempt_root(self.board.directory, adapter, micro_task_id, attempt_id)
                                   for attempt_id in attempt_ids) | frozenset(
                        record for attempt_id in session_attempts[micro_task_id]
                        if private_dirs.linked_component(record := private_dirs.attempt_root(
                            self.board.directory, adapter, micro_task_id, attempt_id) / 'session.jsonl') is None
                        and record.is_file()))
        with self.db.write() as connection:
            run_row = self._run_row(connection, run_id)
            plan = connection.execute(
                "SELECT * FROM workspace_cleanup_plans WHERE plan_id=? AND run_id=?", (plan_id, run_id)
            ).fetchone()
            if plan["state"] not in ("applying", "applied"):
                raise BoardError("REVISION_CONFLICT", "The cleanup plan changed while the removal ran", planId=plan_id)
            result = {"removed": True, "path": plan["path"],
                      "alreadyRemoved": removed is None or bool(removed.get("alreadyRemoved")),
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
                        "removed": True, "retention": result["retention"],
                        "targetRunId": run_id, "targetRevision": run_row["revision"]}
            self.board._store_receipt(connection, command_id, "workflow.cleanup_apply", request_key, response, task_id=run_id)
            head = self.board._head_of(connection)
        self.board._notify(head)
        return response

    def _cleanup_apply_precheck(self, connection, run_row, plan) -> bool:
        """Recheck plan state, expiry, acceptance and dependencies; True means resume."""
        if plan["state"] == "blocked":
            raise BoardError("NOT_READY", "This cleanup plan was blocked and never authorized a deletion",
                             reasons=json.loads(plan["reasons_json"]))
        # Expiry limits admission of a deletion, not completion of one already
        # recorded as applying. A crash after removal must still be recoverable;
        # current authority, identity and dependency checks below remain required.
        if plan["state"] == "planned" and self.now() > plan["expires_at"]:
            raise BoardError("PLAN_EXPIRED", "This cleanup plan expired; plan again before deleting anything",
                             planId=plan["plan_id"], expiresAt=plan["expires_at"])
        task = connection.execute("SELECT * FROM tasks WHERE task_id=?", (run_row["run_id"],)).fetchone()
        manifest = json.loads(run_row["workspace_manifest_json"] or "{}")
        # The plan is bound to the physical allocation, not to whatever logical
        # turn/input snapshot currently runs on it; both must still agree here.
        retained = self._allocation_provenance(connection, run_row)
        allocation = workspace_module().resolve_allocation(self.board.directory, manifest, retained)
        identity = allocation or manifest
        path = Path(plan["path"])
        if (plan["workspace_id"] != identity["workspaceId"]
                or plan["checkout_id"] != identity["checkoutId"]
                or plan["repository_id"] != identity["repositoryId"]
                or plan["path"] != identity["path" if allocation else "checkoutRoot"]
                or (allocation is None and (path.exists() or path.is_symlink()))):
            raise BoardError("CONFLICT", "The cleanup plan no longer matches the run's workspace allocation",
                             planId=plan["plan_id"])
        resume = plan["state"] == "applying"
        reasons, _evidence, _retention = self._cleanup_reasons(connection, run_row, task, manifest, allocation=allocation)
        planned = json.loads(plan["evidence_json"])
        if ((not resume and planned.get("ownerGeneration", run_row["owner_generation"]) != run_row["owner_generation"])
                or planned["acceptance"]["state"] != run_row["state"]):
            reasons.append("review-changed")
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
        # Share the existing run read; the indexed subquery returns at most one
        # own outcome. json_type distinguishes text from serialized objects/arrays.
        run_row = connection.execute(
            "SELECT r.run_id,r.state,r.owner_generation,r.host_id,r.revision,r.continuation_count,r.active_request_id,r.title,r.macro_task_id,"
            " (SELECT CASE WHEN json_type(t.outcome_json,'$.summary')='text'"
            " THEN json_extract(t.outcome_json,'$.summary') END FROM workflow_turns t"
            " WHERE t.run_id=r.run_id AND t.state='returned' AND t.outcome_json IS NOT NULL"
            " ORDER BY t.turn_index DESC LIMIT 1) AS result_summary"
            " FROM workflow_runs r WHERE r.run_id=?", (task_row["task_id"],)
        ).fetchone()
        if run_row is None:
            return None
        summary = run_row["result_summary"]
        summary = summary.strip()[:MAX_RESULT_SUMMARY] if isinstance(summary, str) else ""
        extension = {
            "state": run_row["state"],
            "awaitingHost": run_row["state"] == "awaiting-host",
            "ownerGeneration": run_row["owner_generation"],
            "hostId": run_row["host_id"],
            "revision": run_row["revision"],
            "continuationCount": run_row["continuation_count"],
            "activeRequestId": run_row["active_request_id"],
            "resultSummary": summary or None,
            "hostConclusion": host_conclusions.view(host_conclusions.current(connection, run_row)),
        }
        if (current_scope() or {}).get("kind") != AGENT_KIND:
            extension.update(title=run_row["title"], macroTaskId=run_row["macro_task_id"])
        if run_row["active_request_id"]:
            request = connection.execute(
                "SELECT * FROM workflow_requests WHERE request_id=?", (run_row["active_request_id"],)
            ).fetchone()
            if request is not None:
                extension["requestKind"] = request["kind"]
                extension["requestSummary"] = request["summary"]
                payload = json.loads(request["payload_json"])
                extension["requestRouting"] = payload.get("source") == "routing"
                extension["requestTargetRunId"] = (payload.get("origin") or {}).get("runId") or payload.get("childTaskId")
                boundary = self._request_view(request, connection=connection).get("routingBoundary")
                if boundary is not None:
                    extension["routingBoundary"] = boundary
        return extension

    @staticmethod
    def _resume_recovery(connection, run_id: str) -> dict | None:
        event = connection.execute(
            "SELECT seq,attempt_id,payload_json FROM events WHERE task_id=?"
            " AND kind='workflow.auto_continued'"
            " AND json_extract(payload_json,'$.reason')='native-resume-refused' ORDER BY seq DESC LIMIT 1",
            (run_id,),
        ).fetchone()
        return ({**json.loads(event["payload_json"]), "attemptId": event["attempt_id"], "eventSeq": event["seq"]}
                if event is not None else None)

    # -- claim gate ----------------------------------------------------------
    def claim_blocker(self, connection, task_row) -> str | None:
        """Why a micro-task task may not be claimed right now (never a fake stop)."""
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
            buddy = self._buddy(run_row)
            if buddy is None:
                return "awaiting-model-selection"
            if run_row["validated_buddy_revision"] != run_row["buddy_revision"]:
                return "awaiting-buddy-validation"
            if any(not self._stop_proven(connection, task) for task in self._routing_tasks(connection, run_row["run_id"])):
                return "awaiting-routing-shutdown"
            continuation = connection.execute(
                "SELECT continuation_id, workspace_manifest_json FROM workflow_continuations"
                " WHERE run_id=? AND state='queued' ORDER BY created_at LIMIT 1",
                (run_row["run_id"],),
            ).fetchone()
            turn_count = connection.execute(
                "SELECT COUNT(*) AS count FROM workflow_turns WHERE run_id=? AND state!='rejected'", (run_row["run_id"],)
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
        if run_row["state"] == "waiting-owned":
            return "awaiting-owned"
        if run_row["state"] == "delivered":
            return "awaiting-acknowledgement"
        if run_row["state"] == "concluded":
            return "workflow-concluded"
        if run_row["state"] == "accepted":
            return "workflow-accepted"
        if run_row["state"] == "cancelled":
            return "workflow-cancelled"
        return "workflow-failed"

    # -- turn lifecycle ------------------------------------------------------
    #: Bound on distinct repositories one claim's outside-transaction stash
    #: collection reads per pass. A chosen run beyond the bound is covered by
    #: the claim's coverage retry, never silently missed.
    _CLAIM_STASH_REPOSITORIES = 8

    def collect_claim_stash_facts(self, params: dict, *, selector: str | None = None) -> dict:
        """Read the shared stash state of the claim's actual candidates, outside every transaction.

        The candidate set mirrors the claim scan itself — the same bounded
        queued-candidate window in the same arrival order (model service exclusions are
        not repeated: they can only shrink the claim's own choice, and the
        retry below covers any drift) — so the observation the claim's write
        transaction freezes is normally the observation of exactly the run it
        admits, and changes that happened while the task merely sat queued are
        already part of the starting fact. An explicit ``selector`` reads only
        that run's repository: the retry pass after a coverage miss uses it to
        collect the chosen run itself. Git is never read inside a write
        transaction, and a repository that cannot be read keeps an honest
        per-repository ``unknown``. Collection itself never fails a claim.
        """
        target = selector if isinstance(selector, str) and selector else None
        if target is None:
            target = params.get("taskId") or params.get("runId")
            target = target if isinstance(target, str) and target else None
        try:
            with self.db.read() as connection:
                if target is not None:
                    rows = connection.execute(
                        "SELECT r.workspace_manifest_json FROM workflow_runs r JOIN tasks t ON t.task_id=r.run_id"
                        " WHERE t.state='queued' AND r.workspace_manifest_json IS NOT NULL AND r.run_id=?",
                        (target,),
                    ).fetchall()
                else:
                    # The claim scan's own candidate window: both adapter classes
                    # in arrival order under the same LIMIT the claim uses.
                    from .scheduling import DECISION_ADAPTER
                    rows = []
                    for class_predicate in ("t.adapter IS NOT ?", "t.adapter IS ?"):
                        rows.extend(connection.execute(
                            "SELECT r.workspace_manifest_json, t.created_at, t.task_id"
                            " FROM workflow_runs r JOIN tasks t ON t.task_id=r.run_id"
                            f" WHERE t.state='queued' AND r.workspace_manifest_json IS NOT NULL AND {class_predicate}"
                            " ORDER BY t.created_at, t.task_id LIMIT ?",
                            (DECISION_ADAPTER, self.board.CLAIM_CANDIDATE_LIMIT),
                        ).fetchall())
                    rows.sort(key=lambda row: (row["created_at"], row["task_id"]))
            repositories: dict[str, dict | None] = {}
            for row in rows:
                try:
                    manifest = json.loads(row["workspace_manifest_json"])
                except (TypeError, ValueError):
                    continue
                path = (manifest.get("snapshot") or {}).get("repositoryPath") if isinstance(manifest, dict) else None
                if isinstance(path, str) and path and path not in repositories and len(repositories) < self._CLAIM_STASH_REPOSITORIES:
                    repositories[path] = None
            module = workspace_module()
            if not hasattr(module, "shared_stash_inventory"):
                return {"repositories": repositories}
            pass_reason = None
            try:
                for path in list(repositories):
                    repositories[path] = module.shared_stash_inventory(path)
            except BoardError as error:
                pass_reason = _head(f"{error.code}: {error.message}", 512)
            except (OSError, TypeError, ValueError) as error:
                pass_reason = _head(str(error), 512)
            if pass_reason is not None and hasattr(module, "stash_observation_unknown"):
                # A failed reading is an honest per-repository ``unknown`` whose
                # key is present, exactly like a repository that answered with a
                # failure of its own: the claim may start on it, and a coverage
                # miss can never hide inside a failed pass.
                for path, value in repositories.items():
                    if not isinstance(value, dict):
                        repositories[path] = module.stash_observation_unknown(path, pass_reason)
            return {"repositories": repositories}
        except BoardError as error:
            return {"repositories": {}, "state": "unknown", "reason": _head(f"{error.code}: {error.message}", 512)}
        except (OSError, TypeError, ValueError) as error:
            return {"repositories": {}, "state": "unknown", "reason": _head(str(error), 512)}

    def run_repository_path(self, connection, task_id: str) -> str | None:
        """The shared repository path of one micro-task run's current workspace manifest."""
        row = connection.execute(
            "SELECT workspace_manifest_json FROM workflow_runs WHERE run_id=?", (task_id,)
        ).fetchone()
        if row is None or not row["workspace_manifest_json"]:
            return None
        try:
            manifest = json.loads(row["workspace_manifest_json"])
        except (TypeError, ValueError):
            return None
        path = (manifest.get("snapshot") or {}).get("repositoryPath") if isinstance(manifest, dict) else None
        return path if isinstance(path, str) and path else None

    def collect_result_stash_facts(self, params: dict) -> dict | None:
        """Read the sealing side's shared stash state outside every transaction.

        Called by the result path before its write transaction, so the turn's
        seal boundary records what the repository actually exposes at import
        time. ``None`` means no micro-task workspace applies; the turn-side pin
        then records an honest ``unknown`` instead of inventing a reading.
        """
        attempt_id = params.get("attemptId")
        if not isinstance(attempt_id, str) or not attempt_id:
            return None
        try:
            with self.db.read() as connection:
                row = connection.execute(
                    "SELECT r.workspace_manifest_json FROM attempts a JOIN workflow_runs r ON r.run_id=a.task_id"
                    " WHERE a.attempt_id=?",
                    (attempt_id,),
                ).fetchone()
            if row is None or not row["workspace_manifest_json"]:
                return None
            manifest = json.loads(row["workspace_manifest_json"])
        except (OSError, TypeError, ValueError):
            return None
        path = (manifest.get("snapshot") or {}).get("repositoryPath") if isinstance(manifest, dict) else None
        if not isinstance(path, str) or not path:
            return None
        try:
            module = workspace_module()
            if not hasattr(module, "shared_stash_inventory"):
                return None
            return {"repositoryPath": path, "inventory": module.shared_stash_inventory(path)}
        except BoardError as error:
            # The reading itself failed for a workspace that does apply: keep the
            # failure visible as the seal side's honest unknown reason.
            return {"repositoryPath": path, "failure": _head(f"{error.code}: {error.message}", 512)}
        except (OSError, TypeError, ValueError) as error:
            return {"repositoryPath": path, "failure": _head(str(error), 512)}

    def seal_interrupted_turn(self, attempt_id: str) -> dict | None:
        """Seal a proven-stopped turn's checkout before its recovery transaction.

        The caller proves shutdown from execution identity before calling this
        method. Git work cannot run under the board writer; the fixed attempt
        output is then passed as ``partialWorkspaceSeal`` to settlement.
        """
        with self.db.read() as connection:
            row = connection.execute(
                "SELECT a.task_id,t.input_json FROM attempts a JOIN workflow_turns t"
                " ON t.attempt_id=a.attempt_id WHERE a.attempt_id=?", (attempt_id,),
            ).fetchone()
        if row is None:
            return None
        manifest = json.loads(row["input_json"])["executionWorkspace"]
        if not manifest.get("workspaceId"):
            return None
        return workspace_module().seal(self.board.directory, manifest, row["task_id"], attempt_id)

    def interrupted_native_facts(self, attempt_id: str) -> dict:
        """Read exact terminal native facts already written by the stopped attempt.

        This is a read of our private attempt/session evidence after the caller
        proves process stop. Missing or incomplete records simply require a new
        session at the next turn; they never become a guessed native resume.
        """
        with self.db.read() as connection:
            row = connection.execute(
                "SELECT a.task_id,a.adapter,t.input_json FROM attempts a JOIN workflow_turns t"
                " ON t.attempt_id=a.attempt_id WHERE a.attempt_id=?", (attempt_id,),
            ).fetchone()
        if row is None or row["adapter"] not in ("codex", "dsh"):
            return {}
        from ...private_dirs import attempt_root, native_root
        from ...buddy.roles.turn_io import guard_private_path, validated_checkpoint

        def private_record(path: Path) -> dict | None:
            try:
                with guard_private_path(path).open("rb") as stream:
                    raw = stream.read(128 * 1024 + 1)
                if len(raw) > 128 * 1024:
                    return None
                value = json.loads(raw)
            except (OSError, ValueError, BoardError):
                return None
            return value if isinstance(value, dict) else None

        task_id = row["task_id"]
        turn_input = json.loads(row["input_json"])
        attempt_dir = attempt_root(self.board.directory, row["adapter"], task_id, attempt_id)
        session_root = native_root(self.board.directory, row["adapter"], task_id)
        expected_buddy = {key: turn_input["context"]["buddy"][key]
                          for key in ("provider", "model", "effort")}
        cwd = turn_input["executionWorkspace"]["path"]
        if row["adapter"] == "codex":
            checkpoint = private_record(attempt_dir / "native-checkpoint.json")
            if checkpoint is None:
                return {}
            session_id = checkpoint.get("sessionId")
            native_turn_id = checkpoint.get("nativeTurnId")
            payload = {"nativeCheckpoint": checkpoint, "sessionId": session_id,
                       "nativeTurnId": native_turn_id, "processState": {"shutdownConfirmed": True}}
            validated = validated_checkpoint(payload, turn_input)
            if (validated is None or validated.get("bindingSaved") is not True
                    or validated.get("nativeTurnStatus") not in ("completed", "interrupted")):
                return {}
            binding = private_record(session_root / (hashlib.sha256(session_id.encode()).hexdigest() + ".json"))
            if binding != {"taskId": task_id, "threadId": session_id, "cwd": cwd,
                           "buddy": expected_buddy, "lastTurnId": native_turn_id,
                           "lastAttemptId": attempt_id, "lastInputSha256": validated["inputSha256"]}:
                return {}
            return {**payload, "nativeSession": {"harness": "codex", "sessionId": session_id,
                                                  "storageOwner": "harness-user-store", "resumable": True}}

        record = private_record(attempt_dir / "dsh-session-record.json")
        if record is None or record.get("truncated") or record.get("turnEndSeen") is not True:
            return {}
        sessions = record.get("sessions")
        if not isinstance(sessions, list) or not sessions or not isinstance(sessions[-1], str):
            return {}
        session_id = sessions[-1]
        from ...buddy.harnesses.dsh.session_records import session_record_facts
        observed = session_record_facts(session_root / "sessions", [session_id])
        if observed is None or observed.get("truncated") or observed.get("turnEndSeen") is not True:
            return {}
        binding = private_record(session_root / (hashlib.sha256(session_id.encode()).hexdigest() + ".json"))
        if binding != {"taskId": task_id, "sessionId": session_id, "cwd": cwd,
                       "buddy": expected_buddy}:
            return {}
        return {"sessionId": session_id,
                "nativeSession": {"harness": "dsh", "sessionId": session_id,
                                  "storageOwner": "hey-my-buddy-micro-task", "resumable": True}}

    def begin_turn(self, connection, *, task, attempt_id: str, generation: int, spec: dict, now: str,
                   stash_facts: dict | None = None) -> dict | None:
        """Create the durable turn and its scoped credential inside the claim transaction."""
        run_row = self._run_optional(connection, task["task_id"])
        if run_row is None:
            return None
        if run_row["state"] != "executing":
            raise BoardError(
                "CONFLICT",
                "This micro-task run is not ready for another turn",
                state=run_row["state"],
            )
        turn_count = int(
            connection.execute(
                "SELECT COUNT(*) AS count FROM workflow_turns WHERE run_id=? AND state!='rejected'", (run_row["run_id"],)
            ).fetchone()["count"]
        )
        continuation = connection.execute(
            "SELECT * FROM workflow_continuations WHERE run_id=? AND state='queued' ORDER BY created_at LIMIT 1",
            (run_row["run_id"],),
        ).fetchone()
        if turn_count > 0 and continuation is None:
            raise BoardError(
                "CONFLICT",
                "A continuation input is required before another turn of this micro-task run can start",
                runId=run_row["run_id"],
            )
        previous = connection.execute(
            "SELECT * FROM workflow_turns WHERE run_id=? AND state!='rejected' ORDER BY turn_index DESC LIMIT 1",
            (run_row["run_id"],),
        ).fetchone()
        turn_index = connection.execute(
            "SELECT COALESCE(MAX(turn_index),0)+1 FROM workflow_turns WHERE run_id=?", (run_row["run_id"],)
        ).fetchone()[0]
        resume_mode = "initial" if turn_count == 0 else "reconstructed-new-session"
        manifest = json.loads(run_row["workspace_manifest_json"]) if run_row["workspace_manifest_json"] else {}
        execution_workspace = self._turn_workspace(connection, run_row, manifest, continuation, turn_count)
        buddy = self._buddy(run_row)
        native = self._previous_codex_checkpoint(connection, previous)
        previous_session = previous["session_id"] if previous is not None else None
        if native is not None:
            previous_session = native[1]["sessionId"]
        prior = (connection.execute('SELECT result_json FROM attempts WHERE attempt_id=?',
                                    (previous['attempt_id'],)).fetchone() if previous is not None else None)
        prior_result = json.loads(prior['result_json'] or '{}') if prior else {}
        refused_resume = self._native_resume_refused(previous, prior_result)
        prior_native_session = (prior_result.get("result") or prior_result).get("nativeSession") or {}
        if previous is not None and previous["state"] == "interrupted" and previous_session is None:
            previous_session = prior_native_session.get("sessionId")
        native_completed = (previous is not None and previous["state"] == "returned"
                            and prior_result.get('status') == 'ok'
                            and prior_result.get('shutdownConfirmed') is True)
        if native is not None:
            from ...buddy.roles.turn_io import checkpoint_resumable
            checkpoint = native[1]
            native_completed = checkpoint_resumable(*native) or (
                previous["state"] == "interrupted"
                and checkpoint.get("nativeTurnStatus") in ("completed", "interrupted")
                and checkpoint.get("bindingSaved") is True
                and prior_result.get("shutdownConfirmed") is True)
        elif previous is not None and previous["state"] == "interrupted" and buddy and buddy["harness"] == "dsh":
            native_completed = (prior_native_session.get("resumable") is True
                                and prior_result.get("shutdownConfirmed") is True)
        harness_changed = False
        native_home_changed = False
        permission_reconstruction = False
        if previous is not None and native_completed and previous_session and buddy and not refused_resume:
            previous_input = json.loads(previous["input_json"])
            if previous_input.get("context", {}).get("buddy") == buddy:
                executor = self._execution_adapter(buddy["harness"])
                from ..service.harness_health import read_health
                health = read_health(connection, buddy['harness'])
                result = prior_result
                history = result.get('harnessAttempts') or result.get('result', {}).get('harnessAttempts') or []
                old_version = history[-1].get('harness', {}).get('version') if history else None
                harness_changed = bool(old_version and health.get('version') and old_version != health['version'])
                native_session = (result.get('result') or result).get('nativeSession') or {}
                native_home_changed = (buddy['harness'] == 'codex'
                                       and native_session.get('storageOwner') != 'harness-user-store'
                                       or buddy['harness'] == 'dsh'
                                       and native_session.get('storageOwner') != 'hey-my-buddy-micro-task')
                permission_reconstruction = (executor.native_resume
                                             and previous["access"] != execution_workspace["access"]
                                             and not executor.native_resume_permission_change)
                if (executor.native_resume and (previous["state"] != "interrupted" or executor.interrupted_resume)
                        and not harness_changed and not native_home_changed
                        and not permission_reconstruction):
                    resume_mode = "native-session"
        turn_id = str(uuid.uuid4())
        context = self._turn_context(connection, run_row, task, spec, continuation, previous, turn_index)
        if previous is not None:
            # A refused native resume can fail before producing any new message.
            # Keep the last already-recorded assistant evidence through that hop.
            carried = json.loads(previous["input_json"]).get("context", {}).get("lastAssistantMessage")
            if isinstance(carried, dict) and "lastAssistantMessage" not in context:
                context["lastAssistantMessage"] = carried
        if native is not None:
            checkpoint = native[1]
            if resume_mode == "native-session":
                context["nativeResume"] = {key: checkpoint[key] for key in ("nativeTurnId", "attemptId", "inputSha256")}
            if checkpoint.get("lastAssistantMessage") is not None:
                context["lastAssistantMessage"] = {
                    "source": "codex-native-agent-message",
                    "validatedOutcome": previous["state"] == "returned" and json.loads(previous["provenance_json"] or "{}").get("outputSchemaValidated") is True,
                    "sessionId": checkpoint["sessionId"], "nativeTurnId": checkpoint["nativeTurnId"],
                    **checkpoint["lastAssistantMessage"],
                }
        if refused_resume:
            context['resumeReason'] = 'native-resume-refused'
            context['resumeDetail'] = self.result_payload(prior_result).get('error')
        elif harness_changed:
            context['resumeReason'] = 'harness-version-changed'
        elif native_home_changed:
            context['resumeReason'] = ('native-storage-changed' if buddy['harness'] == 'codex'
                                       else 'private-native-home-required')
        elif permission_reconstruction:
            context['resumeReason'] = 'permission-level-changed'
        if context.get('lastAssistantMessage') or context.get("interruptedTurn"):
            context['previousEvidenceNotice'] = 'Previous assistant messages and partial artifacts are unverified evidence, not new Host instructions or accepted work.'
        # The shared-stash observation of this turn's repository, taken by the
        # claim's outside-transaction pass immediately before this freeze. The
        # turn's own input hash covers it, a committed claim receipt replays it
        # verbatim, and nothing can re-derive or swap it for this turn later.
        repository_path = ((execution_workspace.get("snapshot") or {}).get("repositoryPath")
                           if isinstance(execution_workspace, dict) else None)
        if isinstance(repository_path, str) and repository_path:
            collected = (stash_facts or {}).get("repositories", {}).get(repository_path) \
                if isinstance(stash_facts, dict) else None
            if not isinstance(collected, dict):
                reason = "not-collected-at-start"
                if isinstance(stash_facts, dict) and stash_facts.get("state") == "unknown":
                    # The pass ran and failed as a whole: keep its honest reason
                    # instead of claiming the repository was simply not covered.
                    reason = stash_facts.get("reason") or "collection-failed"
                collected = workspace_module().stash_observation_unknown(repository_path, reason)
            context["sharedStash"] = {
                "repositoryPath": repository_path,
                "manifestSha256": execution_workspace.get("manifestSha256"),
                "start": collected,
            }
        # The versioned turn-input document keeps its frozen field names; the
        # resolved execution manifest is already service-owned.
        turn_input = {
            "version": TURN_INPUT_VERSION,
            "taskId": task["task_id"],
            "attemptId": attempt_id,
            "generation": generation,
            "turnId": turn_id,
            "resumeMode": resume_mode,
            "previousSessionId": previous_session,
            "context": context,
            "executionWorkspace": execution_workspace,
        }
        input_json = canonical_json(turn_input)
        connection.execute(
            "INSERT INTO workflow_turns(turn_id, run_id, attempt_id, generation, turn_index, resume_mode,"
            " previous_session_id, input_json, access, access_note, expected_seconds, state, created_at, updated_at)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?,'prepared',?,?)",
            (
                turn_id,
                run_row["run_id"],
                attempt_id,
                generation,
                turn_index,
                resume_mode,
                previous_session,
                input_json,
                execution_workspace["access"],
                json.loads(run_row["execution_workspace_json"]).get("accessNote"),
                task["expected_seconds"],
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
            "buddy": self._buddy(run_row),
            "resumeMode": resume_mode,
            "inputSha256": sha256_text(input_json),
            "input": turn_input,
            "credential": credential,
            "runId": run_row["run_id"],
        }

    def _previous_codex_checkpoint(self, connection, previous):
        if previous is None:
            return None
        attempt = connection.execute("SELECT * FROM attempts WHERE attempt_id=?", (previous["attempt_id"],)).fetchone()
        if (attempt is None or attempt["adapter"] != "codex" or attempt["execution_state"] != "finished"
                or attempt["shutdown_confirmed"] != 1):
            return None
        from ...buddy.roles.turn_io import validated_checkpoint
        payload = self.result_payload(json.loads(attempt["result_json"] or "{}"))
        checkpoint = validated_checkpoint(payload, json.loads(previous["input_json"]))
        return (payload, checkpoint) if checkpoint is not None else None

    def _turn_workspace(self, connection, run_row, manifest: dict, continuation, turn_count: int) -> dict:
        """The resolved input workspace of one turn.

        Turn one uses the prepared manifest. A continuation uses the manifest this
        service already prepared from the previous sealed output (base = that commit,
        include list = what that seal captured), so a continuation never resumes on
        the original dirty baseline and the turn input is never an unresolved intent.
        """
        if turn_count == 0 or continuation is None or not continuation["workspace_manifest_json"]:
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
            " WHERE a.kind IN ('output','resolved-output','partial-output')"
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
                    and (sealed.get("checkoutId") is None
                         or sealed.get("checkoutId") == checkout_id)):
                return sealed
        return None

    def _failed_turn_input(self, connection, run_row, continuation) -> str | None:
        """An explicit recovery may capture partial files only after a failed, stopped turn.

        A recorded scope conflict must first be settled by the Host: an unresolved
        failure site is never promoted to a baseline, and a resolution already pins
        the handoff that the next continuation prepares from.
        """
        if continuation["kind"] != "manual":
            return None
        previous = connection.execute(
            "SELECT t.*, a.execution_state, a.shutdown_confirmed FROM workflow_turns t"
            " JOIN attempts a ON a.attempt_id=t.attempt_id WHERE t.run_id=? ORDER BY t.turn_index DESC LIMIT 1",
            (run_row["run_id"],),
        ).fetchone()
        if previous is None or previous["state"] not in ("failed", "interrupted") or previous["execution_state"] != "finished" or previous["shutdown_confirmed"] != 1:
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
            "SELECT 1 FROM workflow_artifacts WHERE run_id=? AND attempt_id=? AND kind IN ('output','resolved-output','partial-output') LIMIT 1",
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
        choice = json.loads(run_row["execution_workspace_json"])
        changed_access = choice["access"] != manifest["access"]
        if sealed is None and recovered_attempt is None and not changed_access:
            return None
        # A permission-only continuation before any seal keeps the original
        # explicit untracked input selectors when it re-prepares the allocation.
        included = (sealed.get("includedUntracked") or (sealed.get("snapshot") or {}).get("included") or []
                    if sealed is not None else list((manifest.get("snapshot") or {}).get("executionSelectors") or []))
        sealed = sealed or {}
        return {
            # Reuse the allocated checkout instead of minting a new worktree.
            "kind": "existing",
            "cwd": manifest["path"],
            "access": choice["access"],
            **({"accessNote": choice["accessNote"]} if choice["access"] == "full" else {}),
            "base": {"kind": "working-tree", **({"ref": sealed["commit"]} if sealed.get("commit") else {})},
            "includeUntracked": included,
            "integrator": self._owner_actor(connection, run_row),
            "writeScope": scope["writeScope"] or manifest.get("writeScope") or ["."],
        }

    def prepare_continuation_workspace(self, params: dict) -> None:
        """Resolve a pending continuation's workspace outside every transaction.

        Called by the service before execution admission so the exact canonical turn
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
            checkout_id = previous.get("checkoutId")
            reservations = connection.execute(
                "SELECT * FROM workspace_reservations WHERE checkout_id=? AND state='held' ORDER BY reservation_id",
                (checkout_id,),
            ).fetchall() if checkout_id else []
            own = [item for item in reservations if item["holder_task_id"] == row["run_id"]]
            owned_record = connection.execute(
                "SELECT * FROM workspace_reservations WHERE checkout_id=?"
                " AND holder_task_id=? ORDER BY rowid DESC LIMIT 1",
                (checkout_id, row["run_id"]),
            ).fetchone() if checkout_id else None
            if not own and owned_record is not None and owned_record["state"] == "transferred":
                # An authorized helper will return this ownership when it stops.
                return None
            ownership_problem = None
            if len(own) != 1 or any(
                item["holder_task_id"] != row["run_id"]
                and (item["access"] == "write" or json.loads(run["execution_workspace_json"])["access"] != "read") for item in reservations
            ):
                ownership_problem = {"code": "PREPARATION_CONFLICT", "message": "The continuation checkout is no longer exclusively owned; explicitly continue after its owner releases it"}
            elif own[0]["access"] != ("read" if previous["access"] == "read" else "write") or any(
                    own[0][key] != previous.get(field) for key, field in (
                        ("checkout_id", "checkoutId"), ("repository_id", "repositoryId"),
                        ("path", "path"),
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
                    if error.code == "WORKSPACE_GIT_UNKNOWN":
                        continue
                    # An unresolved scope conflict is a durable Host boundary, not a
                    # claim error: record the attention request and keep the site.
                    problem = {"code": error.code, "message": _head(str(error), 2000),
                               **({"details": error.details} if error.details else {})}
            if problem is None:
                try:
                    if before[-1] is not None:
                        raise BoardError(before[-1]["code"], before[-1]["message"])
                    if recovered_attempt is not None:
                        module = workspace_module()
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
                        replayed = True
                    else:
                        self._cleanup_fence(intent.get("cwd"))
                        preparation = {}
                        manifest = workspace_module().prepare(
                            self.board.directory, f"{row['run_id']}:{row['continuation_id']}", intent,
                            _preparation=preparation)
                        replayed = preparation["replayed"]
                    if any(manifest.get(field) != previous.get(field)
                           for field in ("checkoutId", "checkoutRoot", "repositoryId", "path")):
                        raise BoardError("PREPARATION_CONFLICT", "The prepared continuation changed its allocated checkout")
                    with self.db.read() as connection:
                        if snapshot(connection, row["continuation_id"]) != before:
                            continue
                    if replayed:
                        # A replay's on-disk input needs a fresh execution proof;
                        # a new preparation has just observed this same checkout.
                        workspace_module().verify(manifest, require_unchanged=True)
                except Exception as error:
                    if isinstance(error, BoardError) and error.code in {"WORKSPACE_BUSY", "WORKSPACE_GIT_UNKNOWN"}:
                        # Another worker is preparing the same continuation. Leave it
                        # queued so that worker can publish, or a later claim can recover
                        # its durable files. An unfinished Git observation is also retryable.
                        continue
                    problem = {"code": getattr(error, "code", "WORKSPACE_PREPARE_FAILED"), "message": _head(str(error), 2000),
                               **({"details": error.details} if isinstance(error, BoardError) and error.details else {})}
            with self.db.write() as connection:
                if snapshot(connection, row["continuation_id"]) != before:
                    # Cancellation, takeover, override, allocation or ownership drift
                    # invalidates this activation. Keep all prepared recovery files.
                    continue
                now = self.now()
                if problem is not None:
                    request_id = f"prep-{row['continuation_id']}"
                    self._record_system_request(
                        connection, run, make_reason("workspace.continuation-prepare-failed", continuationId=row["continuation_id"]),
                        {"preparationError": problem}, now, request_id=request_id,
                    )
                    connection.execute("UPDATE workflow_continuations SET state='invalidated' WHERE continuation_id=?", (row["continuation_id"],))
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
                        "UPDATE workspace_reservations SET workspace_id=?, manifest_sha256=?, updated_at=?, access=?"
                        " WHERE holder_task_id=? AND checkout_id=? AND state='held'",
                        (manifest["workspaceId"], manifest["manifestSha256"], now, "read" if manifest["access"] == "read" else "write", row["run_id"], manifest["checkoutId"]),
                    )
                    kind = "workflow.workspace_prepared"
                    event = {"continuationId": row["continuation_id"], "manifestSha256": manifest["manifestSha256"], "checkoutId": manifest["checkoutId"]}
                    if recovered_attempt is not None:
                        event["recoveredAttemptId"] = recovered_attempt
                self.board._append_event(connection, kind, task_id=row["run_id"], revision=run["revision"] + 1, payload=event)
                if problem is None:
                    routing_intent = connection.execute(
                        "SELECT payload_json FROM events WHERE task_id=? AND kind='workflow.continued'"
                        " AND json_extract(payload_json,'$.continuationId')=? ORDER BY seq DESC LIMIT 1",
                        (row["run_id"], row["continuation_id"])).fetchone()
                    if routing_intent and json.loads(routing_intent["payload_json"]).get("reroute") is True:
                        self._start_routing(connection, row["run_id"], now)
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
            " token_verifier, state, created_at) VALUES(?,?,?,?,?,?,?,'active',?)",
            (
                f"cred-{attempt_id}",
                run_row["run_id"],
                run_row["run_id"],
                attempt_id,
                generation,
                turn_id,
                self.db.agent_token_verifier(token),
                now,
            ),
        )
        return token

    def owned_event(self, connection, *, task_id: str | None, kind: str, seq: int, now: str) -> None:
        """Wake a parked Lead from a committed owned-work boundary, never a running turn."""
        if task_id is not None and kind in {"task.completed", "task.cancelled", "task.failed", "task.reconciliation-needed", "attempt.released"}:
            self._owned_descendant_reported(connection, task_id, seq)
        if kind not in OWNED_WAKE_EVENTS or task_id is None:
            return
        lead = connection.execute(
            "SELECT r.* FROM workflow_lead_tasks l JOIN workflow_runs r ON r.run_id=l.lead_run_id"
            " JOIN events e ON e.seq=? WHERE l.owned_run_id=? AND r.state='waiting-owned' AND "
            + OWNED_WAKE_FILTER_SQL, (seq, task_id),
        ).fetchone()
        if lead is not None:
            self._wake_lead(connection, lead, seq, now)

    def _owned_descendant_reported(self, connection, task_id: str, seq: int) -> None:
        """Expose a stopped owned micro task's descendant result, including uncertainty."""
        owned = connection.execute(
            "WITH RECURSIVE edges(parent_id,child_id) AS ("
            " SELECT parent_run_id,child_task_id FROM workflow_children"
            " UNION ALL SELECT route.run_id,decision.task_id FROM workflow_routes route"
            " JOIN decision_requests decision ON decision.decision_id=route.decision_id),"
            " ancestors(run_id) AS (SELECT parent_id FROM edges WHERE child_id=?"
            " UNION SELECT edge.parent_id FROM edges edge JOIN ancestors a ON edge.child_id=a.run_id)"
            " SELECT r.* FROM ancestors a JOIN workflow_lead_tasks l ON l.owned_run_id=a.run_id"
            " JOIN workflow_runs r ON r.run_id=l.owned_run_id WHERE r.state='cancelled'", (task_id,),
        ).fetchone()
        if owned is not None:
            summary = self.shutdown_summary(connection, owned["run_id"])
            self.board._append_event(
                connection, "workflow.owned_stop_reported", task_id=owned["run_id"], revision=owned["revision"],
                payload={"reportedTaskId": task_id, "sourceEventSeq": seq,
                         "stopConfirmed": summary["selfConfirmed"] and summary["descendantsConfirmed"]},
            )

    def _wake_lead(self, connection, lead, seq: int, now: str) -> None:
        event = connection.execute("SELECT * FROM events WHERE seq=?", (seq,)).fetchone()
        owned = self._run_row(connection, event["task_id"])
        trigger = {"seq": seq, "kind": event["kind"], "runId": owned["run_id"], "state": owned["state"]}
        continuation_id = str(uuid.uuid4())
        text = "An owned micro task reached a boundary. Review the current ownedMicroTasks in this turn input."
        connection.execute(
            "INSERT INTO workflow_continuations(continuation_id,run_id,kind,trigger_event_json,"
            " expected_revision,input_text,input_bytes,note,state,created_at)"
            " VALUES(?,?,'lead-wakeup',?,?,?,?,?,'recorded',?)",
            (continuation_id, lead["run_id"], canonical_json(trigger), lead["revision"], text,
             len(text.encode()), None, now),
        )
        task = connection.execute("SELECT * FROM tasks WHERE task_id=?", (lead["run_id"],)).fetchone()
        try:
            self._requeue(connection, task, lead, actor={"role": "workflow", "id": "auto"},
                          now=now, continuation_id=continuation_id)
        except BoardError as error:
            connection.execute("UPDATE workflow_continuations SET state='recorded' WHERE continuation_id=?",
                               (continuation_id,))
            self._record_helper_report(connection, lead, [], reason=make_reason(_AUTO_CONTINUE_FAILURE_CODES[error.code]), now=now)
            return
        self.board._append_event(connection, "workflow.auto_continued", task_id=lead["run_id"],
                                 revision=lead["revision"] + 1,
                                 payload={"continuationId": continuation_id, "kind": "lead-wakeup", "trigger": trigger})

    def _owned_since_input(self, connection, run_id: str, turn_row):
        cursor = _turn_input_context(turn_row["input_json"])["ownedFactsCursor"]
        kinds = tuple(sorted(OWNED_WAKE_EVENTS))
        slots = ",".join("?" for _ in kinds)
        return connection.execute(
            "SELECT e.seq FROM events e JOIN workflow_lead_tasks l ON l.owned_run_id=e.task_id"
            f" WHERE l.lead_run_id=? AND e.seq>? AND e.kind IN ({slots})"
            " AND " + OWNED_WAKE_FILTER_SQL +
            " ORDER BY e.seq LIMIT 1", (run_id, cursor, *kinds),
        ).fetchone()

    def _owned_turn_context(self, connection, run_id: str, previous) -> dict:
        cursor = self.board._head_of(connection)
        previous_cursor = _turn_input_context(previous["input_json"])["ownedFactsCursor"] if previous else 0
        total = connection.execute("SELECT COUNT(*) FROM workflow_lead_tasks WHERE lead_run_id=?", (run_id,)).fetchone()[0]
        rows = connection.execute(
            "SELECT r.*,t.request_id,t.state AS execution_state,t.queue_reason FROM workflow_lead_tasks l"
            " JOIN workflow_runs r ON r.run_id=l.owned_run_id JOIN tasks t ON t.task_id=r.run_id"
            " WHERE l.lead_run_id=? ORDER BY r.activity_seq DESC,r.run_id LIMIT ?", (run_id, MAX_CHILD_VIEW),
        ).fetchall()
        facts = [{"runId": row["run_id"], "requestId": row["request_id"], "state": row["state"],
                  "executionState": row["execution_state"], "queueReason": row["queue_reason"],
                  "ownerGeneration": row["owner_generation"], "revision": row["revision"],
                  "activeRequestId": row["active_request_id"], "turnId": row["current_turn_id"],
                  "attemptId": row["current_attempt_id"] or row["final_attempt_id"],
                  "finalArtifactId": row["final_artifact_id"], "activitySeq": row["activity_seq"],
                  "changedSincePreviousTurn": row["activity_seq"] > previous_cursor}
                 for row in rows]
        return {"ownedMicroTasks": facts, "ownedMicroTaskTotal": total, "ownedFactsCursor": cursor,
                "ownedMicroTasksTruncated": total - len(facts)}

    def _turn_context(self, connection, run_row, task, spec, continuation, previous, turn_index) -> dict:
        micro_task = json.loads(run_row["micro_task_json"])
        task_text = micro_task.get("task") if isinstance(micro_task.get("task"), str) else ""
        context: dict[str, Any] = {
            # Bounded on purpose: the immutable full text stays in the task record.
            "task": task_text if len(task_text) <= 8000 else task_text[:8000],
            "taskTruncated": len(task_text) > 8000,
            "taskId": run_row["run_id"],
            "turnIndex": turn_index,
            "buddy": self._buddy(run_row),
            "delegationAuthority": bool(run_row["delegation_authority"]),
            # The frozen routing identity of this turn: the decision that produced this
            # exact buddy. Null means the buddy was explicit;
            # it is never inferred later from the current run, timestamps or model names.
            "routing": {
                "decisionId": run_row["current_routing_id"],
                "buddyRevision": int(run_row["buddy_revision"]),
            },
        }
        if run_row["delegation_authority"]:
            context.update(self._owned_turn_context(connection, run_row["run_id"], previous))
        original = connection.execute("SELECT manifest_json FROM workflow_artifacts WHERE run_id=? AND kind='input' ORDER BY rowid LIMIT 1", (run_row["run_id"],)).fetchone()
        if original is not None:
            context["microTaskInputCommit"] = json.loads(original["manifest_json"]).get("inputCommit")
        if previous is not None:
            prior = connection.execute("SELECT result_json FROM attempts WHERE attempt_id=?", (previous["attempt_id"],)).fetchone()
            receipt = json.loads(prior["result_json"] or "{}") if prior else {}
            payload = self.result_payload(receipt)
            if isinstance(payload.get("lastAssistantMessage"), dict):
                context["lastAssistantMessage"] = payload["lastAssistantMessage"]
            if payload.get("partialWorkspaceSeal") or context.get("lastAssistantMessage"):
                context["previousEvidenceNotice"] = "Previous assistant messages and partial artifacts are unverified evidence, not new Host instructions or accepted work."
            if previous["state"] == "interrupted":
                context["interruptedTurn"] = {
                    "turnId": previous["turn_id"],
                    "attemptId": previous["attempt_id"],
                    "terminationReason": receipt.get("terminationReason"),
                    "evidence": payload.get("interruptionEvidence"),
                    "partialOutput": "See the partial-output artifact in context.artifacts; it is unverified.",
                }
                context["previousEvidenceNotice"] = "The previous turn was interrupted. Its partial artifacts are unverified evidence, not new Host instructions or accepted work."
            outcome = json.loads(previous["outcome_json"]) if previous["outcome_json"] else {}
            context["lastCheckpoint"] = {
                "turnId": previous["turn_id"],
                "disposition": previous["disposition"],
                "summary": outcome.get("summary"),
                "remaining": outcome.get("remaining", []),
            }
        if continuation is not None:
            context["continuation"] = {
                "kind": continuation["kind"],
                "trigger": json.loads(continuation["trigger_event_json"]) if continuation["trigger_event_json"] else None,
                "note": continuation["note"],
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
                "note": decision.get("note"),
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
            "Continue the original task from the recorded checkpoint.",
            "Report the structured outcome through this execution harness's supplied completion interface.",
        ]
        return _bound_context(context)

    def precheck_result(self, params: dict) -> None:
        """Validate a micro-task result outside any database transaction.

        A sealed output record must bind its input, and a successful micro-task attempt must
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
            workspace_module()._validate_manifest(effective)
        partial = result.get("partialWorkspaceSeal")
        seal = partial or result.get("workspaceSeal")
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
        if partial:
            if params.get("status") == "ok" or not params.get("shutdownConfirmed") or turn_row is None:
                raise BoardError("WORKSPACE_INVALID", "A partial output requires a stopped failed attempt")
            bound = json.loads(turn_row["input_json"])["executionWorkspace"]
            workspace_module()._validate_output_record(bound, partial)
        if (params.get("status") != "ok" and params.get("terminationReason") != "wrap-up"
                or not params.get("shutdownConfirmed")):
            return
        if turn_row is None:
            raise BoardError("TURN_INVALID", "the micro-task attempt has no service-owned turn to import")
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
        return disposition if disposition in ("assistance", "attention", "waiting") else None

    @classmethod
    def _native_resume_refused(cls, turn_row, payload: dict | None) -> bool:
        result = cls.result_payload(payload)
        return (turn_row is not None and turn_row["resume_mode"] == "native-session"
                and result.get("code") == "native-resume-unavailable"
                and result.get("modelStarted") is False
                and payload.get("shutdownConfirmed") is True)

    def _quota_reroutable(self, connection, run, turn, attempt, payload):
        failure = self.result_payload(payload).get("quotaFailure")
        routing = json.loads(turn["input_json"])["context"]["routing"]
        if (not failure or failure["code"] != "quota-exceeded" or failure.get("rejectionPhase") != "before-model"
                or not attempt["shutdown_confirmed"] or attempt["cancel_requested_at"] is not None
                or run["state"] != "executing" or run["buddy_locked"]
                or run["current_turn_id"] != turn["turn_id"] or run["current_attempt_id"] != attempt["attempt_id"]
                or routing["decisionId"] is None or routing["decisionId"] != run["current_routing_id"]
                or routing["buddyRevision"] != run["buddy_revision"]):
            return False
        route = connection.execute("SELECT * FROM workflow_routes WHERE decision_id=?",
                                   (routing["decisionId"],)).fetchone()
        return route["state"] == "resolved" and route["owner_generation"] == run["owner_generation"]

    def settle_turn(
        self, connection, *, task, attempt, payload: dict | None, task_state: str, now: str,
        seal_stash: dict | None = None,
    ) -> dict | None:
        """Import one validated structured outcome and advance the micro-task run."""
        run_row = self._run_optional(connection, task["task_id"])
        if run_row is None:
            return None
        turn_row = connection.execute(
            "SELECT * FROM workflow_turns WHERE attempt_id=?", (attempt["attempt_id"],)
        ).fetchone()
        turn = self.turn_outcome(payload)
        if turn_row is None:
            return None
        # The seal-side shared-stash fact is pinned for every outcome of a turn
        # that froze a start observation — completed, failed, cancelled or late.
        # It is evidence only and never changes this import's state machine.
        shared_refs = self._pin_shared_refs(connection, run_row, attempt, turn_row, seal_stash, now)
        if (attempt["shutdown_confirmed"] and self.result_payload(payload).get("partialWorkspaceSeal")
                and not self.result_payload(payload).get("microTaskError")):
            self._pin_result_artifacts(connection, run_row, attempt, payload, turn_row["turn_id"], now)
            connection.execute("UPDATE workflow_turns SET sealed_artifacts_json=? WHERE turn_id=?", (canonical_json(self._sealed_artifacts(payload)), turn_row["turn_id"]))
            self.board._append_event(connection, "workflow.partial_output_preserved", task_id=run_row["run_id"], attempt_id=attempt["attempt_id"], payload={"partial": True, "verified": False, "final": False})
        ancestor = self._terminal_ancestor(connection, run_row["run_id"])
        if ancestor is not None and run_row["state"] not in ("accepted", "concluded", "cancelled"):
            self._cancel_owned_run(connection, task, now)
            run_row = self._run_row(connection, run_row["run_id"])
        if run_row["state"] in ("accepted", "concluded", "cancelled") or ancestor is not None:
            late = self._late_cancelled_turn(connection, run_row, turn_row, attempt, payload, now)
            if isinstance(late, dict):
                late.setdefault("sharedStash", self._shared_refs_summary(shared_refs))
            return late
        if task_state in ("cancelled", "failed") or (
            not attempt["shutdown_confirmed"] and task_state == "reconciliation-needed"
        ):
            # A cancelled, failed or unconfirmed execution never imports a success.
            interrupted = isinstance(payload, dict) and payload.get("terminationReason") == "interrupted"
            connection.execute(
                "UPDATE workflow_turns SET state=?, updated_at=? WHERE turn_id=?",
                ("interrupted" if interrupted else "failed", now, turn_row["turn_id"]),
            )
            self._revoke_credentials(connection, run_row["run_id"], now)
            terminal = "cancelled" if task_state == "cancelled" else "failed" if task_state == "failed" else None
            conflict_ids = (
                self._record_scope_conflicts(connection, run_row, turn_row, attempt, now)
                if task_state == "failed" else []
            )
            if conflict_ids:
                # Sealing refused managed changes outside the authorized scope. The
                # stopped site and its evidence stay recoverable, and the micro task opens
                # an explicit Host boundary instead of silently absorbing them.
                request_id = self._record_scope_attention(connection, run_row, turn_row, attempt, conflict_ids, now)
                return {
                    "accepted": False,
                    "disposition": None,
                    "state": "awaiting-host",
                    "requestId": request_id,
                    "conflictIds": conflict_ids,
                    "sharedStash": self._shared_refs_summary(shared_refs),
                }
            if task_state == "failed" and self._native_resume_refused(turn_row, payload):
                # No model consumed this continuation. Requeue its exact frozen
                # input, including helper authority and the prepared workspace.
                continuation = connection.execute(
                    "SELECT * FROM workflow_continuations WHERE turn_id=? AND state='consumed'",
                    (turn_row["turn_id"],),
                ).fetchone()
                self._requeue(connection, task, run_row,
                              actor={"role": "workflow", "id": "auto"}, now=now,
                              continuation_id=continuation["continuation_id"])
                facts = {"turnId": turn_row["turn_id"], "continuationId": continuation["continuation_id"],
                         "reason": "native-resume-refused", "detail": self.result_payload(payload).get("error"),
                         "resumeMode": "reconstructed-new-session", "autoContinued": True}
                self.board._append_event(connection, "workflow.native_resume_refused", task_id=run_row["run_id"],
                                         attempt_id=attempt["attempt_id"], revision=run_row["revision"] + 1,
                                         payload={**facts, "code": "native-resume-unavailable",
                                                  "modelStarted": False, "shutdownConfirmed": True})
                self.board._append_event(connection, "workflow.auto_continued", task_id=run_row["run_id"],
                                         attempt_id=attempt["attempt_id"], revision=run_row["revision"] + 1,
                                         payload=facts)
                return {"accepted": False, "disposition": None, "state": "executing", "autoContinued": True,
                        "sharedStash": self._shared_refs_summary(shared_refs)}
            if task_state == "failed" and self._quota_reroutable(connection, run_row, turn_row, attempt, payload):
                continuation = connection.execute(
                    "SELECT continuation_id FROM workflow_continuations WHERE turn_id=? AND state='consumed'",
                    (turn_row["turn_id"],),
                ).fetchone()
                connection.execute("UPDATE workflow_turns SET state='rejected',updated_at=? WHERE turn_id=?",
                                   (now, turn_row["turn_id"]))
                if continuation is not None:
                    connection.execute(
                        "UPDATE workflow_continuations SET state='queued',attempt_id=NULL,turn_id=NULL,consumed_at=NULL"
                        " WHERE continuation_id=?", (continuation["continuation_id"],))
                self.board._transition_task(connection, task, "queued")
                connection.execute(
                    "UPDATE workflow_runs SET updated_at=?,revision=revision+1 WHERE run_id=?", (now, run_row["run_id"]))
                previous_decision_id = run_row["current_routing_id"]
                self._start_routing(connection, run_row["run_id"], now)
                refreshed = self._run_row(connection, run_row["run_id"])
                facts = {"turnId": turn_row["turn_id"], "reason": "quota-exceeded",
                         "previousDecisionId": previous_decision_id, "decisionId": refreshed["current_routing_id"],
                         "continuationId": continuation["continuation_id"] if continuation is not None else None,
                         "autoContinued": True}
                self.board._append_event(connection, "workflow.auto_continued", task_id=run_row["run_id"],
                                         attempt_id=attempt["attempt_id"], revision=refreshed["revision"], payload=facts)
                return {"accepted": False, "disposition": None, "state": refreshed["state"], "autoContinued": True,
                        "sharedStash": self._shared_refs_summary(shared_refs)}
            if terminal is not None:
                connection.execute(
                    "UPDATE workflow_runs SET state=?, updated_at=?, revision=revision+1 WHERE run_id=?",
                    (terminal, now, run_row["run_id"]),
                )
            self.board._append_event(connection, "workflow.turn_failed", task_id=run_row["run_id"],
                                     attempt_id=attempt["attempt_id"], revision=run_row["revision"] + (1 if terminal is not None else 0),
                                     payload={"turnId": turn_row["turn_id"], "state": terminal or "uncertain"})
            return {
                "accepted": False,
                "disposition": None,
                "state": terminal or "uncertain",
                "sharedStash": self._shared_refs_summary(shared_refs),
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
            return {"accepted": False, "disposition": None, "state": "failed", "reason": rejection,
                    "sharedStash": self._shared_refs_summary(shared_refs)}
        outcome = turn["outcome"]
        disposition = outcome["disposition"]
        connection.execute(
            "UPDATE workflow_turns SET state='returned', disposition=?, outcome_json=?, outcome_truncated_json=?, provenance_json=?,"
            " session_id=?, prompt_sha256=?, input_sha256=?, turn_result_path=?, sealed_artifacts_json=?,"
            " updated_at=? WHERE turn_id=?",
            (
                disposition,
                canonical_json(outcome),
                canonical_json(turn["outcomeTruncated"]) if "outcomeTruncated" in turn else None,
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
        if run_row["delegation_authority"] and disposition in ("completed", "waiting"):
            unfinished = connection.execute(
                "SELECT r.run_id,r.state,t.state AS execution_state FROM workflow_lead_tasks l"
                " JOIN workflow_runs r ON r.run_id=l.owned_run_id JOIN tasks t ON t.task_id=r.run_id"
                " WHERE l.lead_run_id=? AND r.state NOT IN ('accepted','concluded') ORDER BY l.created_at,r.run_id",
                (run_row["run_id"],),
            ).fetchall()
            active = any(row["execution_state"] == "cancelling" or (
                         row["state"] in ("executing", "waiting-helpers")
                         and row["execution_state"] in ("queued", "running")) or (
                         row["state"] == "cancelled"
                         and self.shutdown_summary(connection, row["run_id"])["unconfirmedCount"] > 0)
                         for row in unfinished)
            if disposition == "completed" and unfinished or disposition == "waiting" and not active:
                request_id = self._record_lead_attention(connection, run_row, turn_row, attempt, disposition, unfinished, now)
                return {"accepted": False, "disposition": disposition, "state": "awaiting-host", "requestId": request_id,
                        "sharedStash": self._shared_refs_summary(shared_refs)}
            if disposition == "waiting":
                connection.execute(
                    "UPDATE workflow_runs SET state='waiting-owned',current_attempt_id=NULL,updated_at=?,revision=revision+1"
                    " WHERE run_id=?", (now, run_row["run_id"]),
                )
                connection.execute("UPDATE tasks SET queue_reason='awaiting-owned' WHERE task_id=?", (run_row["run_id"],))
                self.board._append_event(connection, "workflow.turn_returned", task_id=run_row["run_id"],
                                         attempt_id=attempt["attempt_id"], revision=run_row["revision"] + 1,
                                         payload={"turnId": turn_row["turn_id"], "disposition": disposition, "state": "waiting-owned"})
                changed = self._owned_since_input(connection, run_row["run_id"], turn_row)
                if changed is not None:
                    self._wake_lead(connection, self._run_row(connection, run_row["run_id"]), changed["seq"], now)
                return {"accepted": True, "disposition": disposition,
                        "state": self._run_row(connection, run_row["run_id"])["state"],
                        "sharedStash": self._shared_refs_summary(shared_refs)}
        if disposition == "completed":
            connection.execute(
                "UPDATE workflow_runs SET state='delivered', final_attempt_id=?, final_artifact_id=COALESCE(?,"
                " final_artifact_id), current_turn_id=?, current_attempt_id=NULL, updated_at=?, revision=revision+1"
                " WHERE run_id=?",
                (attempt["attempt_id"], artifact_id, turn_row["turn_id"], now, run_row["run_id"]),
            )
            self.board._append_event(
                connection,
                "workflow.turn_returned",
                task_id=run_row["run_id"],
                attempt_id=attempt["attempt_id"],
                revision=run_row["revision"] + 1,
                payload={"turnId": turn_row["turn_id"], "disposition": disposition},
            )
            return {"accepted": True, "disposition": disposition, "state": "delivered",
                    "sharedStash": self._shared_refs_summary(shared_refs)}
        request_id = self._record_request(connection, run_row, turn_row, turn, disposition, now)
        self.board._append_event(
            connection,
            "workflow.turn_returned",
            task_id=run_row["run_id"],
            attempt_id=attempt["attempt_id"],
            revision=run_row["revision"] + 1,
            payload={"turnId": turn_row["turn_id"], "disposition": disposition, "requestId": request_id},
        )
        return {"accepted": True, "disposition": disposition, "state": "awaiting-host", "requestId": request_id,
                "sharedStash": self._shared_refs_summary(shared_refs)}

    def _record_lead_attention(self, connection, run_row, turn_row, attempt, disposition, unfinished, now) -> str:
        facts = [{"runId": row["run_id"], "state": row["state"]} for row in unfinished[:MAX_CHILD_VIEW]]
        request_id = self._record_system_request(
            connection, run_row, make_reason("lead.result-refused", disposition=disposition, unfinished=facts, total=len(unfinished)),
            {}, now, turn_row=turn_row,
        )
        self.board._append_event(connection, "workflow.lead_result_refused", task_id=run_row["run_id"],
                                 attempt_id=attempt["attempt_id"], revision=run_row["revision"] + 1,
                                 payload={"turnId": turn_row["turn_id"], "requestId": request_id,
                                          "unfinishedOwnedMicroTasks": facts, "unfinishedOwnedMicroTaskTotal": len(unfinished)})
        return request_id

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
            return "the turn disposition is not completed, assistance, attention or waiting"
        if disposition == "waiting":
            if not _turn_input_context(turn_row["input_json"]).get("delegationAuthority"):
                return "only a Lead turn can return waiting"
            if outcome.get("request") is not None:
                return "a waiting turn cannot name a request"
        if not isinstance(outcome.get("summary"), str) or not outcome["summary"].strip():
            return "the turn outcome has no summary"
        if "outcomeTruncated" in turn and not isinstance(turn["outcomeTruncated"], dict):
            return "the turn outcomeTruncated must be an object"
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
        request_id = self._record_system_request(
            connection, run_row, make_reason("workspace-scope-violation", conflictIds=list(conflict_ids), paths=paths[:MAX_CONFLICT_PATH_VIEW]),
            {}, now, turn_row=turn_row,
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

    def _record_system_request(self, connection, run_row, reason: dict | None, payload: dict, now: str, *,
                               kind: str = "attention", turn_row=None, request_id: str | None = None,
                               decision_id: str | None = None) -> str:
        """Open one system boundary and propagate the same form to its owners."""
        request_id = request_id or f"req-{uuid.uuid4()}"
        connection.execute(
            "INSERT INTO workflow_requests(request_id,run_id,turn_id,attempt_id,kind,reason_code,reason_params_json,"
            "decision_id,payload_json,state,expected_revision,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,'open',?,?,?)",
            (request_id, run_row["run_id"], turn_row["turn_id"] if turn_row is not None else None,
             turn_row["attempt_id"] if turn_row is not None else None, kind,
             reason["code"] if reason is not None else None,
             canonical_json(reason["params"]) if reason is not None else None, decision_id,
             canonical_json(payload), run_row["revision"] + 1, now, now),
        )
        if turn_row is not None:
            connection.execute("UPDATE workflow_runs SET current_turn_id=?,current_attempt_id=NULL WHERE run_id=?",
                               (turn_row["turn_id"], run_row["run_id"]))
        self._sync_boundary(connection, run_row["run_id"], now)
        self._propagate_boundary(connection, self._request_row(connection, run_row["run_id"], request_id), now)
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
            "suggestedBuddyId": request.get("suggestedBuddyId"),
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

    #: The fixed artifact kind carrying one turn's shared-stash facts.
    SHARED_REFS_KIND = "shared-refs"

    @classmethod
    def shared_refs_record_digest(cls, payload: dict) -> str:
        """The reproducible digest of one shared-refs record's payload.

        The digest covers exactly the canonical JSON of every field except the
        digest field itself, so a stored record can always be recomputed from
        its own contents and any drift is detectable without trusting the
        stored value.
        """
        return sha256_text(canonical_json({key: value for key, value in payload.items()
                                           if key != "manifestSha256"}))

    @classmethod
    def verify_shared_refs_record(cls, record) -> dict:
        """Re-derive one pinned shared-stash record and refuse any drift.

        The pinned record is fixed evidence bound to one run/attempt/turn; this
        check recomputes its digest from the stored payload and rejects a record
        whose contents no longer match the identity it was pinned under.
        """
        if (not isinstance(record, dict) or record.get("version") != 1
                or record.get("kind") != cls.SHARED_REFS_KIND
                or not isinstance(record.get("taskId"), str) or not isinstance(record.get("attemptId"), str)
                or not isinstance(record.get("turnId"), str) or not isinstance(record.get("turnInputSha256"), str)
                or not isinstance(record.get("repositoryPath"), str)):
            raise BoardError("WORKSPACE_INVALID", "The pinned shared-stash record has an unknown shape")
        digest = record.get("manifestSha256")
        if not isinstance(digest, str) or cls.shared_refs_record_digest(record) != digest:
            raise BoardError("WORKSPACE_MANIFEST_CHANGED",
                             "The pinned shared-stash record digest does not match its contents")
        return record

    def _pin_shared_refs(self, connection, run_row, attempt, turn_row, seal_stash, now: str) -> dict | None:
        """Freeze the seal-side shared-stash fact of one micro-task turn.

        The start observation is the turn input's own frozen record; the seal
        observation comes from the result path's outside-transaction collection
        and is bound to the same repository the turn froze, or stays an honest
        ``unknown`` when it was not collected for that repository. The pinned
        record carries the full-entry comparison with, for every entry that
        disappeared, its commit, its description, a safe ``git stash store``
        recovery command and the explicit cause-unknown note. Its
        ``manifestSha256`` is recomputed from the payload itself (every field
        except the digest) and re-verified before pinning, and the executed
        workspace manifest keeps its own digest under
        ``workspaceManifestSha256``. It is evidence only: it restores nothing,
        judges nothing, and can neither fail this import nor any later seal or
        verification. Turns whose input predates the freezing have no start
        fact and pin nothing — a start observation is never invented from a
        later reading.
        """
        try:
            module = workspace_module()
        except BoardError:
            return None
        if not hasattr(module, "shared_stash_comparison") or not hasattr(module, "stash_observation_unknown"):
            return None
        try:
            document = json.loads(turn_row["input_json"])
        except (TypeError, ValueError):
            return None
        frozen = document.get("context", {}).get("sharedStash") if isinstance(document, dict) else None
        if not isinstance(frozen, dict) or not isinstance(frozen.get("start"), dict):
            return None
        repository_path = frozen.get("repositoryPath") if isinstance(frozen.get("repositoryPath"), str) else None
        start = frozen["start"]
        if repository_path is None:
            return None
        if (isinstance(seal_stash, dict) and seal_stash.get("repositoryPath") == repository_path
                and isinstance(seal_stash.get("inventory"), dict)):
            seal = seal_stash["inventory"]
        elif isinstance(seal_stash, dict) and isinstance(seal_stash.get("failure"), str):
            # The collection ran for this attempt and failed: its own reason, not
            # a claim that nothing was collected.
            seal = module.stash_observation_unknown(repository_path, seal_stash["failure"])
        else:
            seal = module.stash_observation_unknown(repository_path, "not-collected-at-seal")
        record = {
            "version": 1,
            "kind": self.SHARED_REFS_KIND,
            "taskId": run_row["run_id"],
            "attemptId": attempt["attempt_id"],
            "turnId": turn_row["turn_id"],
            "turnInputSha256": sha256_text(turn_row["input_json"]),
            "workspaceManifestSha256": frozen.get("manifestSha256"),
            "workspaceId": (json.loads(run_row["workspace_manifest_json"] or "{}") or {}).get("workspaceId"),
            "repositoryPath": repository_path,
            "startStash": start,
            "sealStash": seal,
            "comparison": module.shared_stash_comparison(start, seal),
        }
        record["manifestSha256"] = self.shared_refs_record_digest(record)
        self.verify_shared_refs_record(record)
        self._pin_artifact(
            connection,
            run_id=run_row["run_id"],
            kind=self.SHARED_REFS_KIND,
            manifest=record,
            attempt_id=attempt["attempt_id"],
            turn_id=turn_row["turn_id"],
            source_task_id=run_row["run_id"],
            now=now,
        )
        return record

    @classmethod
    def _shared_refs_summary(cls, record: dict | None) -> dict | None:
        """The bounded Host projection of one pinned shared-stash record."""
        if not isinstance(record, dict):
            return None
        comparison = record.get("comparison") if isinstance(record.get("comparison"), dict) else {}

        def side(name):
            value = record.get(name) if isinstance(record.get(name), dict) else {}
            entries = value.get("entries")
            return {"state": value.get("state"), "reason": value.get("reason"),
                    "entryCount": len(entries) if isinstance(entries, list) else None}

        lost = [item for item in (comparison.get("lostEntries") or []) if isinstance(item, dict)][:16]
        new = [item for item in (comparison.get("newEntries") or []) if isinstance(item, dict)][:16]
        return {
            "turnId": record.get("turnId"),
            "attemptId": record.get("attemptId"),
            "repositoryPath": record.get("repositoryPath"),
            "workspaceManifestSha256": record.get("workspaceManifestSha256"),
            "start": side("startStash"),
            "seal": side("sealStash"),
            "comparison": {
                "state": comparison.get("state"),
                "changed": comparison.get("changed"),
                "unknownSides": comparison.get("unknownSides"),
                "truncated": comparison.get("truncated"),
                "truncatedSides": comparison.get("truncatedSides"),
                "unprovenDirections": comparison.get("unprovenDirections"),
                "lostCount": comparison.get("lostCount"),
                "newCount": comparison.get("newCount"),
                "lostEntries": lost,
                "newEntries": new,
            },
        }

    def _sealed_artifacts(cls, payload: dict | None) -> list[dict]:
        result = cls.result_payload(payload)
        seal = result.get("workspaceSeal") or result.get("partialWorkspaceSeal")
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
        partial = result.get("partialWorkspaceSeal")
        seal = partial or result.get("workspaceSeal")
        if not isinstance(seal, dict):
            return None
        return self._pin_artifact(
            connection,
            run_id=run_row["run_id"],
            kind="partial-output" if partial else "output",
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
            run_row["state"] in ("accepted", "concluded", "cancelled") or self._terminal_ancestor(connection, task["task_id"]) is not None
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
            if run_row is not None and run_row["state"] == "accepted" and task["acceptance_verdict"] == "accepted":
                state = "succeeded"
            else:
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
        if parent["state"] in ("accepted", "concluded", "cancelled") or self._terminal_ancestor(connection, parent["run_id"]) is not None:
            return
        if state == "attention":
            source = connection.execute(
                "SELECT * FROM workflow_requests WHERE run_id=? AND attempt_id=? AND state='open'"
                " AND kind IN ('assistance','attention') ORDER BY created_at, request_id LIMIT 1",
                (task["task_id"], attempt["attempt_id"]),
            ).fetchone()
            # System requests propagate when the common writer opens them.
            if source is not None and source["reason_code"] is None:
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
            "SELECT * FROM workflow_continuations WHERE run_id=? AND kind='helper' AND state='recorded'"
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
                    actor={"role": "workflow", "id": "auto"},
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
                self._record_helper_report(connection, parent, outcomes, reason=make_reason(_AUTO_CONTINUE_FAILURE_CODES[error.code]), now=now)
            return
        if parent["state"] == "waiting-helpers":
            self._record_helper_report(connection, parent, outcomes, reason=make_reason("helpers-settled"), now=now)

    def _record_helper_report(self, connection, parent, outcomes: list[dict], *, reason: dict, now: str) -> None:
        self._record_system_request(connection, parent, reason, {"helperOutcomes": outcomes}, now, kind="helper-report")

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
                closed = parent["state"] in ("accepted", "concluded", "cancelled") or self._terminal_ancestor(connection, parent_id) is not None
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
                    "SELECT * FROM workspace_reservations WHERE holder_task_id=? AND checkout_id=?"
                    " AND state='transferred' ORDER BY created_at DESC, rowid DESC LIMIT 1",
                    (parent_id, reservation["checkout_id"]),
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
        """Low-level execution controls cannot bypass micro-task lineage/evidence."""
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
                f"{operation} on a micro-task run requires the Host control capability or an authenticated console "
                "session; use the workflow operations instead",
                runId=task_row["task_id"],
            )
        self._authorize(connection, run_row, params, console_authority=authority, action=operation)
        if operation == "task_retry":
            reject_deleting_run(connection, run_row["run_id"])
        command, native_operation = {
            "task_cancel": ("cancel", "workflow_cancel"),
            "task_retry": ("continue", "workflow_continue"),
            "task_acknowledge": ("accept", "workflow_accept"),
        }[operation]
        raise BoardError("MICRO_TASK_REQUIRED", f"Use {command} ({native_operation}) for this micro task", runId=run_row["run_id"])


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


#: The default request view names a Git preparation failure with one bounded
#: line; the complete command, exit status and output tail stay in the record
#: that ``includeAudit`` reads.
PREPARATION_REASON_LIMIT = 256

_REMOTE_USER_INFO = re.compile(
    r"(?P<scheme>[a-zA-Z][a-zA-Z0-9+.-]*://)[^/\s@]*@"
    r"|(?<![\w.@-])[\w.+-]+@(?=[\w.-]+[:/])"
)


def _without_remote_user_info(text: str) -> str:
    """Drop the user-information segment of any remote address in process output."""
    return _REMOTE_USER_INFO.sub(lambda match: match.group("scheme") or "", text)


def _preparation_reason(details) -> str | None:
    """One bounded line for the default view: the Git operation and its last output line.

    The line quotes another process's output, so it is length-capped and carries
    no remote address's user information. Details without a Git operation behind
    them have nothing to name here and yield None.
    """
    if not isinstance(details, dict):
        return None
    operation = details.get("operation")
    reason = details.get("reason")
    if not isinstance(operation, str) or not operation or not isinstance(reason, str):
        return None
    lines = [line.strip() for line in reason.splitlines() if line.strip()]
    if not lines:
        return None
    return f"git {operation}: {_without_remote_user_info(lines[-1])}"[:PREPARATION_REASON_LIMIT]


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
    if "ownedMicroTasks" in trimmed:
        owned = list(trimmed["ownedMicroTasks"])
        trimmed["ownedMicroTasks"] = owned
        while owned and len(canonical_json(trimmed).encode("utf-8")) > schemas.MAX_WORKFLOW_INPUT_BYTES:
            owned.pop()
            trimmed["ownedMicroTasksTruncated"] = trimmed["ownedMicroTaskTotal"] - len(owned)
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
