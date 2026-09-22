"""Governed productivity workflow: Host-directed assistance over ordinary tasks.

This is the implementation of ``docs/implementation/productivity-contract.md``. A
governed run *is* the existing logical task (``runId`` = ``taskId``): it uses the
ordinary task/attempt/worker/capacity/receipt machinery, never a second job engine.
What the coordinator adds is durable governance around it:

* an immutable original goal, a Host owner generation and a control capability;
* one durable turn per actual execution, with the bounded context and the validated
  structured outcome the DSH adapter imported after confirmed shutdown;
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
        }
        if compact:
            # The default view is an index: the full input/outcome/provenance is in audit.
            return view
        if outcome is not None:
            view["summary"] = outcome.get("summary")
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
        if row["kind"] == "output":
            view["outputCommit"] = manifest.get("commit")
            view["diffPath"] = manifest.get("diffPath")
            view["diffSha256"] = manifest.get("diffSha256")
            changed = manifest.get("changedPaths")
            if isinstance(changed, list):
                view["changedPaths"] = changed[:32]
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
        }
        requests = requests[:MAX_REQUEST_VIEW]
        children = children[:MAX_CHILD_VIEW]
        artifacts = artifacts[:MAX_ARTIFACT_VIEW]
        active_request = None
        if run_row["active_request_id"]:
            row = connection.execute(
                "SELECT * FROM workflow_requests WHERE request_id=?", (run_row["active_request_id"],)
            ).fetchone()
            if row is not None:
                active_request = self._request_view(row)
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
            "currentTurn": self._turn_view(turns[0], compact=True) if turns else None,
            "turns": [self._turn_view(row, compact=True) for row in turns],
            "activeRequest": active_request,
            "requests": [self._request_view(row, compact=True) for row in requests],
            "children": [self._child_view(row) for row in children],
            "artifacts": [self._artifact_view(row) for row in artifacts],
            "counts": {
                "turns": turn_total,
                "requests": request_total,
                "children": child_total,
                "artifacts": artifact_total,
            },
            "truncated": truncated,
            "finalArtifactId": run_row["final_artifact_id"],
            "finalAttemptId": run_row["final_attempt_id"],
            "createdAt": run_row["created_at"],
            "updatedAt": run_row["updated_at"],
            "auditAvailable": True,
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
        return summary

    @staticmethod
    def _wait_reason(state: str, task_row, active_request) -> str:
        if state == "awaiting-host":
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
        identity_key = "snapshotSha256" if kind == "output" else "manifestSha256"
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
                if run_row["request_fingerprint"] not in ("", request_fingerprint):
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
                        and run_row["request_fingerprint"] in ("", request_fingerprint)
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
        return self._submitted_view(task_id)

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
        schemas.reject_unknown(params, {"runId", "taskId", "includeAudit"}, "workflow.get")
        run_id = schemas.required_string(params, "runId", max_length=128)
        include_audit = schemas.optional_bool(params, "includeAudit", False)
        with self.db.read() as connection:
            task = connection.execute("SELECT * FROM tasks WHERE task_id=?", (run_id,)).fetchone()
            if task is None:
                raise BoardError("NOT_FOUND", "Unknown runId", runId=run_id)
            run_row = self._run_optional(connection, run_id)
            if run_row is None:
                return {"governed": False, "runId": run_id, "task": self.board._decorate(connection, task)}
            view = self.compact(connection, run_row, task)
            if include_audit:
                view["audit"] = self._audit(connection, run_row)
        return view

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
            for helper in helpers:
                manifest = workspace_module().prepare(
                    self.board.directory, helper["requestId"], helper["executionWorkspace"]
                )
                prepared.append({**helper, "manifest": manifest})

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
            if request_row["state"] != "open":
                raise BoardError(
                    "CONFLICT",
                    "This assistance request is already decided; a Host decision is recorded once",
                    requestId=request_id,
                    requestState=request_row["state"],
                )
            if run_row["active_request_id"] != request_id:
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
                children = self._create_helpers(connection, run_row, task, request_row, prepared, command_id, now)
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
            if request_row["state"] != "open" or run_row["active_request_id"] != request_id:
                raise BoardError(
                    "CONFLICT",
                    "This assistance request is not the open Host boundary of the run",
                    requestId=request_id,
                    requestState=request_row["state"],
                    activeRequestId=run_row["active_request_id"],
                )
        return None

    def _resolve_helper_attention(
        self,
        connection,
        parent_run,
        request_row,
        prepared: list[dict],
        *,
        decision: str,
        reason: str,
        actor: str,
        command_id: str,
        now: str,
    ) -> None:
        """Resume the yielded helper named by a helper-attention request.

        The decision text becomes the child's bounded continuation input, the child's
        own open request is closed in the same transaction, and any additional helper
        requested by an approval is admitted as a normal child. The parent run stays
        at ``waiting-helpers`` so its one-use automatic continuation still fires when
        every child is terminal.
        """
        child_task_id = request_row["child_task_id"]
        child_task = connection.execute("SELECT * FROM tasks WHERE task_id=?", (child_task_id,)).fetchone()
        child_run = self._run_optional(connection, child_task_id)
        if child_task is None or child_run is None:
            raise BoardError("NOT_FOUND", "The helper named by this request no longer exists", childTaskId=child_task_id)
        input_text = reason or (
            "The Host approved the requested assistance; continue with this guidance"
            if decision == "approve"
            else "The Host declined the requested assistance; continue with what you have"
        )
        connection.execute(
            "UPDATE workflow_requests SET state=?, decision_json=?, decision_command_id=?, decided_at=?,"
            " updated_at=? WHERE run_id=? AND state='open'",
            (
                "approved" if decision == "approve" else "declined",
                canonical_json({"decision": decision, "reason": reason, "actor": actor, "parentRunId": parent_run["run_id"]}),
                command_id,
                now,
                now,
                child_task_id,
            ),
        )
        continuation_id = str(uuid.uuid4())
        connection.execute(
            "INSERT INTO workflow_continuations(continuation_id, run_id, command_id, authorized_by, request_id,"
            " expected_revision, input_text, input_bytes, reason, helper_policy, helper_outcomes_json, state,"
            " created_at) VALUES(?,?,?,'auto',?,?,?,?,?, 'keep','[]','recorded',?)",
            (
                continuation_id,
                child_task_id,
                command_id,
                request_row["request_id"],
                child_run["revision"],
                input_text,
                len(input_text.encode()),
                reason or None,
                now,
            ),
        )
        if decision == "approve" and prepared:
            # The Host granted the helper's request by authorizing dependency helpers.
            # They run under the requesting child's own run; the child waits for them
            # and resumes through its one-use continuation, while the root parent keeps
            # counting the child as active.
            extra = self._create_helpers(connection, child_run, child_task, request_row, prepared, command_id, now)
            connection.execute(
                "UPDATE workflow_runs SET state='waiting-helpers', active_request_id=NULL, updated_at=?,"
                " revision=revision+1 WHERE run_id=? AND revision=?",
                (now, child_task_id, child_run["revision"]),
            )
        else:
            extra = []
            self._requeue(
                connection,
                child_task,
                child_run,
                reason=input_text,
                actor=actor,
                now=now,
                continuation_id=continuation_id,
            )
        # The child is active again for the parent's fan-in until it truly settles.
        connection.execute(
            "UPDATE workflow_children SET state='active', updated_at=?, revision=revision+1 WHERE child_task_id=?",
            (now, child_task_id),
        )
        connection.execute(
            "UPDATE workflow_runs SET state='waiting-helpers', active_request_id=NULL, updated_at=?,"
            " revision=revision+1 WHERE run_id=?",
            (now, parent_run["run_id"]),
        )
        self.board._append_event(
            connection,
            "workflow.helper_resumed",
            task_id=child_task_id,
            revision=child_run["revision"] + 1,
            payload={
                "parentRunId": parent_run["run_id"],
                "requestId": request_row["request_id"],
                "decision": decision,
                "dependencyHelpers": [child["taskId"] for child in extra],
            },
        )

    def _create_helpers(
        self, connection, run_row, parent_task, request_row, prepared: list[dict], command_id: str, now: str
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
        self, connection, *, helper_task_id, item, parent_task, task_spec, run_row, manifest, now
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
                spec["adapter"],
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
            " goal_fingerprint, execution_workspace_json, workspace_manifest_json, workspace_id,"
            " workspace_manifest_sha256, state, revision, created_at, updated_at)"
            " VALUES(?,?,1,?,?,?,?,?,?,?,'executing',1,?,?)",
            (
                helper_task_id,
                run_row["host_id"],
                self.db.control_token_verifier(self.db.control_token(helper_task_id, 1)),
                canonical_json(spec),
                fingerprint,
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
            payload={"parentRunId": run_row["run_id"], "requestId": request_id, "integrator": item["integrator"]},
        )

    # -- continue ------------------------------------------------------------
    def continue_run(self, params: dict, *, console_authority: dict | None = None) -> dict:
        schemas.reject_unknown(
            params,
            {
                "runId",
                "commandId",
                "expectedRevision",
                "input",
                "helperPolicy",
                "reason",
                *schemas.CONTROL_FIELDS,
                schemas.CONSOLE_AUTHORITY_FIELD,
            },
            "workflow.continue",
        )
        schemas.reject_untrusted_override(params)
        run_id = schemas.required_string(params, "runId", max_length=128)
        command_id = schemas.required_string(params, "commandId", max_length=128)
        expected = schemas.require_expected_revision(params)
        input_text, input_bytes = _bounded_input(params)
        policy = schemas.optional_string(params, "helperPolicy")
        if policy is not None and policy not in ("cancel", "keep"):
            raise BoardError("INVALID_ARGUMENT", "helperPolicy must be 'cancel' or 'keep'")
        reason = schemas.optional_string(params, "reason", max_length=schemas.MAX_WORKFLOW_REASON_BYTES) or ""
        request_key = {
            "runId": run_id,
            "expectedRevision": expected,
            "inputSha256": sha256_text(input_text),
            "helperPolicy": policy,
        }
        now = self.now()
        with self.db.write() as connection:
            run_row = self._run_row(connection, run_id)
            task = connection.execute("SELECT * FROM tasks WHERE task_id=?", (run_id,)).fetchone()
            actor = self._authorize(connection, run_row, params, console_authority=console_authority, action="A continuation")
            self._expect_revision(run_row, expected)
            live_helpers = int(
                connection.execute(
                    "SELECT COUNT(*) AS count FROM workflow_children WHERE parent_run_id=? AND state='active'",
                    (run_id,),
                ).fetchone()["count"]
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
            receipt = self.board._receipt(connection, command_id, "workflow.continue", request_key)
            if receipt is not None:
                return {**receipt, "duplicate": True}
            helper_outcomes = self._helper_outcomes(connection, run_id)
            if policy == "cancel":
                self._cancel_children(connection, run_id, reason or "manual continuation cancelled live helpers", now)
            # Manual continuation invalidates every unconsumed automatic trigger.
            connection.execute(
                "UPDATE workflow_continuations SET state='invalidated' WHERE run_id=? AND authorized_by='auto'"
                " AND state IN ('recorded','queued')",
                (run_id,),
            )
            # An open request the Host bypassed is recorded as superseded, not left dangling.
            connection.execute(
                "UPDATE workflow_requests SET state='superseded', updated_at=? WHERE run_id=? AND state='open'",
                (now, run_id),
            )
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
                },
            )
            run_row = self._run_row(connection, run_id)
            view = self.compact(connection, run_row)
            response = {
                **view,
                "continuationId": continuation_id,
                "inputSha256": sha256_text(input_text),
                "helperPolicy": policy,
                "duplicate": False,
            }
            self.board._store_receipt(
                connection, command_id, "workflow.continue", request_key, response, task_id=run_id
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
            turn = connection.execute(
                "SELECT * FROM workflow_turns WHERE run_id=? ORDER BY turn_index DESC LIMIT 1", (row["child_task_id"],)
            ).fetchone()
            outcome = json.loads(turn["outcome_json"]) if turn is not None and turn["outcome_json"] else None
            attempt_id = row["active_attempt_id"] or row["selected_attempt_id"]
            artifact = connection.execute(
                "SELECT * FROM workflow_artifacts WHERE run_id=? AND kind='output' ORDER BY created_at DESC LIMIT 1",
                (row["child_task_id"],),
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
            if child_run is not None and child_run["workspace_manifest_json"]:
                manifest = json.loads(child_run["workspace_manifest_json"])
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
            # turn. This is deliberately not a legacy `task_retry` of completed work
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
            self.board._append_event(
                connection,
                "workflow.takeover",
                task_id=run_id,
                revision=run_row["revision"] + 1,
                payload={"actor": actor, "newHostId": new_host, "ownerGeneration": generation},
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
            # Revoke automatic continuation: cancellation is a Host decision that
            # replaces it, and a delayed helper callback must not requeue the run.
            connection.execute(
                "UPDATE workflow_continuations SET state='cancelled' WHERE run_id=? AND state IN ('recorded','queued')",
                (run_id,),
            )
            connection.execute(
                "UPDATE workflow_requests SET state='cancelled', updated_at=? WHERE run_id=? AND state='open'",
                (now, run_id),
            )
            self._cancel_children(connection, run_id, reason, now)
            self._revoke_credentials(connection, run_id, now)
            if task["state"] not in ("completed", "failed", "cancelled"):
                if task["state"] == "queued" and (
                    task["active_attempt_id"] is None
                ):
                    self.board._transition_task(connection, task, "cancelled")
                    connection.execute("UPDATE tasks SET queue_reason=NULL, updated_at=? WHERE task_id=?", (now, run_id))
                else:
                    if task["state"] != "cancelling":
                        self.board._transition_task(connection, task, "cancelling")
                    attempt = self.board._selected_attempt(connection, task)
                    if attempt is not None and attempt["cancel_requested_at"] is None:
                        connection.execute(
                            "UPDATE attempts SET cancel_requested_at=?, updated_at=?, revision=revision+1"
                            " WHERE attempt_id=?",
                            (now, now, attempt["attempt_id"]),
                        )
                self.board._append_event(
                    connection,
                    "workflow.cancelled",
                    task_id=run_id,
                    revision=run_row["revision"] + 1,
                    payload={"reason": reason, "actor": actor, "honestShutdown": True},
                )
            connection.execute(
                "UPDATE workflow_runs SET state='cancelled', active_request_id=NULL, updated_at=?,"
                " revision=revision+1 WHERE run_id=? AND revision=?",
                (now, run_id, run_row["revision"]),
            )
            task = connection.execute("SELECT * FROM tasks WHERE task_id=?", (run_id,)).fetchone()
            if self._stop_proven(connection, task):
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
        """Cancel every owned nonterminal descendant, however deeply nested.

        Each descendant's task, run, open requests, automatic continuations and
        scoped credentials are settled in this same short transaction. A reservation
        is released only when the attempt never crossed the spawn boundary or its stop
        is proven; an active or uncertain descendant keeps it until its owner reports.
        """
        cancelled: list[str] = []
        seen: set[str] = set()

        def cancel_task(task_id: str, parent_run_id: str) -> None:
            if task_id in seen:
                return
            seen.add(task_id)
            task = connection.execute("SELECT * FROM tasks WHERE task_id=?", (task_id,)).fetchone()
            if task is None:
                return
            attempt = self.board._selected_attempt(connection, task)
            if task["state"] not in ("completed", "failed", "cancelled"):
                if task["state"] == "queued" and task["active_attempt_id"] is None:
                    self.board._transition_task(connection, task, "cancelled")
                    connection.execute(
                        "UPDATE tasks SET queue_reason=NULL, updated_at=? WHERE task_id=?", (now, task_id)
                    )
                else:
                    if task["state"] != "cancelling":
                        self.board._transition_task(connection, task, "cancelling")
                    if attempt is not None and attempt["cancel_requested_at"] is None:
                        connection.execute(
                            "UPDATE attempts SET cancel_requested_at=?, updated_at=?, revision=revision+1"
                            " WHERE attempt_id=?",
                            (now, now, attempt["attempt_id"]),
                        )
            connection.execute(
                "UPDATE workflow_children SET state='cancelled', updated_at=?, revision=revision+1"
                " WHERE child_task_id=?",
                (now, task_id),
            )
            connection.execute(
                "UPDATE workflow_runs SET state='cancelled', active_request_id=NULL, updated_at=?,"
                " revision=revision+1 WHERE run_id=? AND state NOT IN ('accepted')",
                (now, task_id),
            )
            connection.execute(
                "UPDATE workflow_requests SET state='cancelled', updated_at=? WHERE run_id=? AND state='open'",
                (now, task_id),
            )
            connection.execute(
                "UPDATE workflow_continuations SET state='cancelled' WHERE run_id=? AND state IN ('recorded','queued')",
                (task_id,),
            )
            self._revoke_credentials(connection, task_id, now)
            if self._stop_proven(connection, task):
                self._release_reservations(connection, task_id, now)
            self.board._append_event(
                connection,
                "workflow.helper_cancelled",
                task_id=task_id,
                attempt_id=attempt["attempt_id"] if attempt is not None else None,
                revision=task["revision"] + 1,
                payload={
                    "parentRunId": parent_run_id,
                    "reason": reason,
                    "reservationReleased": self._stop_proven(connection, task),
                },
            )
            cancelled.append(task_id)
            for nested in connection.execute(
                "SELECT child_task_id FROM workflow_children WHERE parent_run_id=?", (task_id,)
            ).fetchall():
                cancel_task(nested["child_task_id"], task_id)

        for child in connection.execute(
            "SELECT child_task_id FROM workflow_children WHERE parent_run_id=?", (run_id,)
        ).fetchall():
            cancel_task(child["child_task_id"], run_id)
        return cancelled

    def _stop_proven(self, connection, task) -> bool:
        """True only when no process can still be writing this task's workspace."""
        attempt = self.board._selected_attempt(connection, task)
        if attempt is None:
            return True
        return attempt["execution_state"] == "finished" and bool(attempt["shutdown_confirmed"])

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
                    "evidence": evidence,
                    "noteBytes": len(note.encode()),
                },
            )
            task = connection.execute("SELECT * FROM tasks WHERE task_id=?", (run_id,)).fetchone()
            run_row = self._run_row(connection, run_id)
            view = self.compact(connection, run_row, task)
            # An accepted attempt counts as one reviewed sample, exactly like the
            # legacy acknowledgement path; the verdict is never inherited implicitly.
            self.board.evaluation.refresh_task_evidence(connection, task, now)
            response = {**view, "verdict": verdict, "duplicate": False}
            if command_id:
                self.board._store_receipt(
                    connection, command_id, "workflow.acknowledge", request_key, response, task_id=run_id,
                    attempt_id=attempt["attempt_id"],
                )
            head = self.board._head_of(connection)
        self.board._notify(head)
        return response

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
                "SELECT kind, summary FROM workflow_requests WHERE request_id=?", (run_row["active_request_id"],)
            ).fetchone()
            if request is not None:
                extension["requestKind"] = request["kind"]
                extension["requestSummary"] = request["summary"]
        return extension

    # -- claim gate ----------------------------------------------------------
    def claim_blocker(self, connection, task_row) -> str | None:
        """Why a governed task may not be claimed right now (never a fake stop)."""
        run_row = self._run_optional(connection, task_row["task_id"])
        if run_row is None:
            return None
        if run_row["state"] == "executing":
            continuation = connection.execute(
                "SELECT continuation_id FROM workflow_continuations WHERE run_id=? AND state='queued' LIMIT 1",
                (run_row["run_id"],),
            ).fetchone()
            turn_count = connection.execute(
                "SELECT COUNT(*) AS count FROM workflow_turns WHERE run_id=?", (run_row["run_id"],)
            ).fetchone()["count"]
            if int(turn_count) > 0 and continuation is None:
                return "awaiting-host"
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
            "SELECT * FROM workflow_turns WHERE run_id=? AND state='concluded' ORDER BY turn_index DESC LIMIT 1",
            (run_row["run_id"],),
        ).fetchone()
        turn_index = turn_count + 1
        resume_mode = "initial" if turn_index == 1 else "reconstructed-new-session"
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

        Ordered durably by artifact creation, so the parent's own later turn wins over
        an earlier helper seal on the same checkout instead of reverting the baseline.
        """
        checkout_id = manifest.get("checkoutId")
        if not checkout_id:
            return None
        candidates: list[tuple[str, str, str]] = []
        for row in connection.execute(
            "SELECT artifact_id, manifest_json, created_at FROM workflow_artifacts"
            " WHERE run_id=? AND kind='output'",
            (run_row["run_id"],),
        ).fetchall():
            candidates.append((row["created_at"], row["artifact_id"], row["manifest_json"]))
        for row in connection.execute(
            "SELECT a.artifact_id, a.manifest_json, a.created_at, c.workspace_manifest_json AS child_manifest"
            " FROM workflow_children c JOIN workflow_artifacts a"
            " ON a.run_id=c.child_task_id AND a.kind='output'"
            " WHERE c.parent_run_id=? AND c.workspace_manifest_json IS NOT NULL",
            (run_row["run_id"],),
        ).fetchall():
            child_manifest = json.loads(row["child_manifest"])
            if child_manifest.get("checkoutId") == checkout_id:
                candidates.append((row["created_at"], row["artifact_id"], row["manifest_json"]))
        if not candidates:
            return None
        candidates.sort()
        return json.loads(candidates[-1][2])

    def _continuation_intent(self, connection, run_row, continuation) -> dict | None:
        """The workspace intent of one continuation, or None when no fresh prepare is needed.

        A continuation keeps the *currently allocated physical checkout*: an isolated
        worktree stays the same worktree, so tasks.cwd, the write reservation and the
        adapter cwd never diverge. Only the logical baseline moves, to the newest fixed
        handoff produced on that checkout (the parent's own later seal or a helper's
        transferred output, whichever came last).
        """
        manifest = json.loads(run_row["workspace_manifest_json"]) if run_row["workspace_manifest_json"] else {}
        if not manifest or not manifest.get("path"):
            return None
        sealed = self._latest_handoff(connection, run_row, manifest)
        if sealed is None:
            return None
        snapshot = sealed.get("snapshot") if isinstance(sealed.get("snapshot"), dict) else {}
        included = sealed.get("includedUntracked") or snapshot.get("included") or []
        return {
            # Reuse the allocated checkout instead of minting a new worktree.
            "kind": "existing",
            "cwd": manifest["path"],
            "access": manifest.get("access", "write"),
            "base": {"kind": "working-tree", "ref": sealed.get("commit")},
            "includeUntracked": included,
            "integrator": manifest.get("integrator") or "host",
            "writeScope": manifest.get("writeScope") or ["."],
        }

    def prepare_continuation_workspace(self, params: dict) -> None:
        """Resolve a pending continuation's workspace outside every transaction.

        Called by the service before ``worker_claim`` so the exact canonical turn
        input already contains the resolved manifest; preparation is idempotent by
        the continuation identity, so concurrent workers cannot double-prepare.
        """
        selector = params.get("taskId") or params.get("runId")
        with self.db.read() as connection:
            if isinstance(selector, str) and selector:
                rows = connection.execute(
                    "SELECT c.* FROM workflow_continuations c WHERE c.run_id=? AND c.state='queued'"
                    " AND c.workspace_manifest_json IS NULL LIMIT 1",
                    (selector,),
                ).fetchall()
            else:
                rows = connection.execute(
                    "SELECT c.* FROM workflow_continuations c JOIN tasks t ON t.task_id=c.run_id"
                    " WHERE c.state='queued' AND c.workspace_manifest_json IS NULL AND t.state='queued'"
                    " ORDER BY c.created_at LIMIT 1"
                ).fetchall()
            candidates = []
            for row in rows:
                run_row = self._run_optional(connection, row["run_id"])
                if run_row is None:
                    continue
                intent = self._continuation_intent(connection, run_row, row)
                if intent is not None:
                    candidates.append((row, intent))
        for row, intent in candidates:
            manifest = workspace_module().prepare(
                self.board.directory, f"{row['run_id']}:{row['continuation_id']}", intent
            )
            previous = json.loads(row["workspace_manifest_json"]) if row["workspace_manifest_json"] else {}
            if previous.get("checkoutId") and previous.get("checkoutId") != manifest.get("checkoutId"):
                raise BoardError(
                    "PREPARATION_CONFLICT",
                    "A continuation must stay on the allocated checkout; an explicitly changed allocation needs "
                    "its own reservation before it can be claimed",
                    expectedCheckoutId=previous.get("checkoutId"),
                    preparedCheckoutId=manifest.get("checkoutId"),
                )
            with self.db.write() as connection:
                updated = connection.execute(
                    "UPDATE workflow_continuations SET workspace_manifest_json=?"
                    " WHERE continuation_id=? AND workspace_manifest_json IS NULL",
                    (canonical_json(manifest), row["continuation_id"]),
                )
                if updated.rowcount == 1:
                    # The run's current allocation view follows the effective turn
                    # manifest; the physical path and checkout identity do not change.
                    connection.execute(
                        "UPDATE workflow_runs SET workspace_manifest_json=?, workspace_id=?,"
                        " workspace_manifest_sha256=?, updated_at=? WHERE run_id=?",
                        (
                            canonical_json(manifest),
                            manifest.get("workspaceId"),
                            manifest.get("manifestSha256"),
                            self.now(),
                            row["run_id"],
                        ),
                    )
                    if manifest.get("path"):
                        connection.execute(
                            "UPDATE tasks SET cwd=?, updated_at=? WHERE task_id=? AND cwd != ?",
                            (manifest["path"], self.now(), row["run_id"], manifest["path"]),
                        )

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
            "Call buddy_finish_turn exactly once with the structured outcome; final prose is not parsed.",
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
        if run_row["state"] in ("accepted", "cancelled"):
            # A delayed callback for a goal that is already settled records its
            # evidence but can never reopen the accepted or cancelled run.
            disposition = None
            if turn is not None and self._validate_turn(turn, turn_row, attempt) is None:
                disposition = turn["outcome"]["disposition"]
                connection.execute(
                    "UPDATE workflow_turns SET state='concluded', disposition=?, outcome_json=?, provenance_json=?,"
                    " session_id=?, prompt_sha256=?, input_sha256=?, turn_result_path=?, updated_at=? WHERE turn_id=?",
                    (
                        disposition,
                        canonical_json(turn["outcome"]),
                        canonical_json(turn.get("provenance")),
                        turn.get("sessionId"),
                        turn.get("promptSha256"),
                        turn.get("inputSha256") or turn_row["input_sha256"],
                        self.result_payload(payload).get("turnResultPath"),
                        now,
                        turn_row["turn_id"],
                    ),
                )
                self._pin_result_artifacts(connection, run_row, attempt, payload, turn_row["turn_id"], now)
            else:
                connection.execute(
                    "UPDATE workflow_turns SET state='failed', updated_at=? WHERE turn_id=?",
                    (now, turn_row["turn_id"]),
                )
            self._revoke_credentials(connection, run_row["run_id"], now)
            self.board._append_event(
                connection,
                "workflow.late_turn",
                task_id=run_row["run_id"],
                attempt_id=attempt["attempt_id"],
                revision=run_row["revision"],
                payload={"turnId": turn_row["turn_id"], "state": run_row["state"], "disposition": disposition},
            )
            return {"accepted": False, "disposition": disposition, "state": run_row["state"], "late": True}
        if task_state in ("cancelled", "failed") or (
            not attempt["shutdown_confirmed"] and task_state == "reconciliation-needed"
        ):
            # A cancelled, failed or unconfirmed execution never imports a success.
            connection.execute(
                "UPDATE workflow_turns SET state='failed', updated_at=? WHERE turn_id=?", (now, turn_row["turn_id"])
            )
            self._revoke_credentials(connection, run_row["run_id"], now)
            terminal = "cancelled" if task_state == "cancelled" else "failed" if task_state == "failed" else None
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
        if provenance.get("tool") != "buddy_finish_turn":
            return "the terminal tool provenance is missing"
        if provenance.get("turnEnd") != "completed":
            return "the native turn did not complete"
        if provenance.get("flush") not in ("awaited", "flushed"):
            return "the session flush was not awaited before the record was written"
        if provenance.get("rootSessionMatched") is not True:
            return "the accepted tool result was not correlated with the root session"
        if not isinstance(turn.get("sessionId"), str) or not turn["sessionId"]:
            return "the turn record carries no session identity"
        return None

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
        child = connection.execute(
            "SELECT * FROM workflow_children WHERE child_task_id=?", (task["task_id"],)
        ).fetchone()
        if child is None:
            return
        disposition = self.yield_disposition(payload)
        if disposition in ("assistance", "attention"):
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
            attempt_id=attempt["attempt_id"],
            revision=task["revision"] + 1,
            payload={"parentRunId": parent["run_id"], "state": state, "disposition": disposition},
        )
        if parent["state"] in ("accepted", "cancelled"):
            # A delayed helper callback for a settled goal records its evidence and
            # never requeues the parent or reopens its state.
            return
        if state == "attention":
            # Helper attention is surfaced immediately, even while siblings run.
            turn_row = connection.execute(
                "SELECT * FROM workflow_turns WHERE attempt_id=?", (attempt["attempt_id"],)
            ).fetchone()
            outcome = json.loads(turn_row["outcome_json"]) if turn_row is not None and turn_row["outcome_json"] else {}
            request = outcome.get("request") or {}
            request_id = f"req-{uuid.uuid4()}"
            payload_view = {
                "summary": request.get("summary") or outcome.get("summary") or "a helper needs Host attention",
                "attempted": request.get("attempted"),
                "neededWork": _string_list(request.get("neededWork")),
                "expectedArtifacts": _string_list(request.get("expectedArtifacts")),
                "acceptance": request.get("acceptance"),
                "suggestedProfileId": request.get("suggestedProfileId"),
            }
            connection.execute(
                "INSERT INTO workflow_requests(request_id, run_id, turn_id, attempt_id, child_task_id, kind,"
                " summary, payload_json, state, expected_revision, created_at, updated_at)"
                " VALUES(?,?,?,?,?,'helper-attention',?,?,'open',?,?,?)",
                (
                    request_id,
                    parent["run_id"],
                    turn_row["turn_id"] if turn_row is not None else None,
                    attempt["attempt_id"],
                    task["task_id"],
                    payload_view["summary"],
                    canonical_json(payload_view),
                    parent["revision"],
                    now,
                    now,
                ),
            )
            connection.execute(
                "UPDATE workflow_runs SET state='awaiting-host', active_request_id=?, updated_at=?,"
                " revision=revision+1 WHERE run_id=?",
                (request_id, now, parent["run_id"]),
            )
            connection.execute(
                "UPDATE tasks SET queue_reason='helper-attention', updated_at=? WHERE task_id=?",
                (now, parent["run_id"]),
            )
            return
        active = connection.execute(
            "SELECT COUNT(*) AS count FROM workflow_children WHERE parent_run_id=? AND state='active'",
            (parent["run_id"],),
        ).fetchone()["count"]
        if int(active) > 0:
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
        connection.execute(
            "UPDATE workflow_runs SET state='awaiting-host', active_request_id=?, updated_at=?,"
            " revision=revision+1 WHERE run_id=?",
            (request_id, now, parent["run_id"]),
        )
        connection.execute(
            "UPDATE tasks SET queue_reason='awaiting-host', updated_at=? WHERE task_id=?", (now, parent["run_id"])
        )

    def _settle_helper_reservation(self, connection, child, task, attempt, now: str) -> None:
        if not attempt["shutdown_confirmed"]:
            return
        reservation = connection.execute(
            "SELECT * FROM workspace_reservations WHERE holder_task_id=? AND state='held'", (task["task_id"],)
        ).fetchone()
        if reservation is None:
            return
        parent = self._run_row(connection, child["parent_run_id"])
        parent_reservation = connection.execute(
            "SELECT * FROM workspace_reservations WHERE holder_task_id=? AND state='transferred'",
            (parent["run_id"],),
        ).fetchone()
        # Release the helper's reservation first: the partial unique index allows one
        # held writer per checkout, and the parent re-holds it in the same transaction.
        connection.execute(
            "UPDATE workspace_reservations SET state='released', updated_at=?, released_at=? WHERE reservation_id=?",
            (now, now, reservation["reservation_id"]),
        )
        if parent_reservation is not None and parent_reservation["checkout_id"] == reservation["checkout_id"]:
            # A sequentially transferred checkout returns to the parent only after the
            # helper's stop is confirmed, so the parent can continue on it.
            connection.execute(
                "UPDATE workspace_reservations SET state='held', updated_at=?, released_at=NULL"
                " WHERE reservation_id=?",
                (now, parent_reservation["reservation_id"]),
            )

    # -- legacy-control guard ------------------------------------------------
    def guard_task_control(self, connection, task_row, params: dict, *, operation: str) -> None:
        """A governed task's cancel/retry/acknowledge never bypasses Host control."""
        run_row = self._run_optional(connection, task_row["task_id"])
        if run_row is None:
            return
        authority = params.get(schemas.CONSOLE_AUTHORITY_FIELD)
        if not (isinstance(authority, dict) and authority.get("sessionId")):
            authority = console_authority_from_scope()
        if authority is not None:
            return
        control = schemas.normalize_control(params)
        if control is None:
            raise BoardError(
                "UNAUTHORIZED",
                f"{operation} on a governed run requires the Host control capability or an authenticated console "
                "session; use the workflow operations instead",
                runId=task_row["task_id"],
            )
        self._authorize(connection, run_row, params, console_authority=None, action=operation)


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
