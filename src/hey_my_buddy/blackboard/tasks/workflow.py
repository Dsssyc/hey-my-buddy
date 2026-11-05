"""Micro-task productivity workflow: Host-directed assistance over ordinary tasks.

The current contract is documented in ``docs/reference/workflow.md``. A
micro-task run *is* the existing logical task (``runId`` = ``taskId``): it uses the
ordinary task/attempt/worker/capacity/receipt machinery, never a second job engine.
What the coordinator adds is durable governance around it:

* an immutable original micro task, explicit authorization and direct response identities;
* one durable turn per actual execution, with the full context and the validated
  structured outcome the coding adapter imported after confirmed shutdown;
* assistance/attention requests, explicit helper tasks, one-use automatic
  continuation authority and manual continuation inputs;
* workspace reservations keyed by the actual ``checkoutRoot`` (so sibling cwd
  directories of one checkout cannot bypass write exclusion);
* attempt-scoped credentials that the service enforces, and console-user authority
  that only a registered console session can present.

The workspace module (``hey_my_buddy.blackboard.tasks.workspace``) performs all Git/filesystem work. Preparation and sealing happen outside a database transaction;
immutable manifests are pinned in authoritative records afterwards. Revoking an
unregistered allocation keeps its original allocation lock through a read-only
reference proof and exact removal; writers of retained references share that lock.
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

from ...paths import is_absolute_path
from ...protocol import schemas
from ...protocol.input_failure import INPUT_REJECTED, input_failure_view
from ...protocol.reasons import reason as make_reason, reason_from_json
from ...protocol.stop import WRAP_UP_INITIATORS
from ...protocol.turn_input import turn_buddy
from ...protocol.workspace_manifest import (project_id, manifest_ref, resolve_manifest, turn_manifest,
                                          artifact_manifest, record_reference, resolve_record, cumulative_patch_error,
                                          sealed_worker_commit)
from .workspace_bindings import (MANIFEST_OWNERS_CTE, RUN_BINDINGS_CTE, effective_manifest, workspace_facts, retained_manifests, owned_manifest, output_references)
from ..routing.statistics import accumulate_infrastructure, accumulate_overall
from . import macro_tasks, projects, fixes as task_fixes
from . import host_conclusions
from .path_views import MAX_OUTPUT_PATH_VIEW, _bounded_path_fields
from . import micro_task
from .request_reasons import request_reason
from .macro_task_deletion_guard import reject_deleting_checkout, reject_deleting_run, reject_macro_task
from ..store.db import canonical_json, sha256_text
from ..store.work import autonomous_tasks
from ...errors import BoardError
from ... import private_dirs

from . import workspace as _workspace_module
from .workspace import manifest_file_reference

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
    "workflow_submit", "workflow_continue", "workflow_cancel",
    "workflow_accept", "workflow_conclude", "task_list",
})
LEAD_OWNED_OPERATIONS = LEAD_OPERATIONS - {"workflow_submit"} | (
    AGENT_RUN_BOUND_OPERATIONS - {"workflow_suggest"}
)

TURN_INPUT_VERSION = 4
TURN_OUTPUT_VERSION = 1
TURN_DISPOSITIONS = ("completed", "assistance", "attention", "waiting")
MAX_ACTIVE_TURNS_VIEW = 10
MAX_CHILD_VIEW = 32
MAX_ARTIFACT_VIEW = 32
MAX_REQUEST_VIEW = 5
WRAP_UP_SECONDS = 300

#: Bounded views of the lifecycle records added by the workspace-lifecycle slice.
MAX_CONFLICT_VIEW = 8
MAX_INTEGRATION_VIEW = 8
INTEGRATION_PATH_FIELDS = (
    "matchingPaths", "differingPaths", "missingPaths", "conflictingPaths", "adjustments", "hostPaths", "unrecordedPaths",
)
SCOPE_ACTIONS = ("restore", "adopt", "abandon")
FINAL_ARTIFACT_KINDS = ("output", "resolved-output", "result")

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
    """The workspace implementation, with one module replacement seam for tests."""
    return _workspace_module


def known_repository_paths(connection, manifest: dict) -> tuple[str, ...]:
    """Published repository locations, ordered by their existing event journal."""
    identifier = project_id(manifest)
    located = []
    owner_join = (" JOIN manifest_owners o ON o.workspace_id=x.workspace_id AND o.manifest_sha256=x.digest"
                  " JOIN workflow_runs r ON r.run_id=o.owner_run_id"
                  " LEFT JOIN macro_tasks m ON m.macro_task_id=r.macro_task_id")
    candidates = (
        "SELECT a.rowid AS position,a.run_id,a.attempt_id,a.kind,a.workspace_id,a.input_manifest_sha256 AS digest"
        " FROM workflow_artifacts a WHERE a.kind IN ('input','turn-input')"
        " UNION ALL SELECT c.rowid,c.run_id,NULL,'continuation',c.workspace_id,c.workspace_manifest_sha256"
        " FROM workflow_continuations c WHERE c.workspace_id IS NOT NULL"
        " UNION ALL SELECT t.rowid,t.run_id,t.attempt_id,'receipt',t.workspace_id,t.workspace_manifest_sha256"
        " FROM workflow_turns t JOIN attempts a ON a.attempt_id=t.attempt_id WHERE a.result_json IS NOT NULL"
    )
    for row in connection.execute(
            f"WITH manifest_owners AS ({MANIFEST_OWNERS_CTE}), candidates AS ({candidates})"
            " SELECT x.*,o.repository_path FROM candidates x" + owner_join +
            " WHERE COALESCE(m.project_id,r.standalone_project_id)=?", (identifier,)):
        if row['kind'] == 'input':
            event = connection.execute("SELECT seq FROM events WHERE task_id=? AND kind IN ('task.submitted','workflow.helper_admitted') ORDER BY seq DESC LIMIT 1", (row['run_id'],)).fetchone()
        elif row['kind'] == 'continuation':
            event = connection.execute("SELECT seq FROM events WHERE task_id=? AND kind='workflow.workspace_prepared' AND json_extract(payload_json,'$.manifestSha256')=? ORDER BY seq DESC LIMIT 1", (row['run_id'],row['digest'])).fetchone()
        else:
            event = connection.execute("SELECT seq FROM events WHERE attempt_id=? AND kind IN ('execution.received','workflow.turn_returned','workflow.turn_failed','workflow.turn_rejected') ORDER BY seq DESC LIMIT 1", (row['attempt_id'],)).fetchone()
        if event is not None:
            located.append((event['seq'], row['position'], row['repository_path']))
    return tuple(dict.fromkeys(path for _, _, path in sorted(located, reverse=True)))


def repository_candidates(db, manifest: dict) -> tuple[str, ...]:
    with db.read() as connection:
        return known_repository_paths(connection, manifest)


def _turn_input_context(input_json: str) -> dict:
    """The frozen context object of one supported turn input."""
    document = json.loads(input_json)
    if type(document["version"]) is not int or document["version"] not in (1, 2, 3, 4, 5):
        raise BoardError("TURN_INVALID", "The frozen turn input version is not supported")
    return document["context"]


def _frozen_turn_routing(context: dict) -> dict | None:
    """The routing identity frozen in a turn, absent only in older turn inputs."""
    if "routing" not in context:
        return None
    routing = context["routing"]
    return {"decisionId": routing["decisionId"], "buddyRevision": routing["buddyRevision"]}


def _frozen_turn_shared_stash(context: dict, *, compact: bool = False) -> dict | None:
    """The frozen stash observation, absent only in older turn inputs.

    The compact view carries states and counts; the complete record stays in the
    frozen input. An unknown observation has no entry list.
    """
    if "sharedStash" not in context:
        return None
    value = context["sharedStash"]
    start = value["start"]
    entries = start["entries"]
    summary = {
        "repositoryPath": value["repositoryPath"],
        "manifestSha256": value["manifestSha256"],
        "start": {"state": start["state"], "entryCount": len(entries) if entries is not None else None,
                  "totalCount": start["totalCount"], "truncated": start["truncated"]},
    }
    if not compact:
        summary["start"]["entries"] = entries
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

    def _run_manifest(self, connection, row):
        if connection is None:
            with self.db.read() as reader:
                return effective_manifest(self.board.directory, reader, row)
        return effective_manifest(self.board.directory, connection, row)

    def _artifact_document(self, row):
        if row['content_form'] == 'result-reference':
            from ...protocol.turn_result import result_document
            return result_document(self.board.directory, row)
        return artifact_manifest(self.board.directory, row)

    @staticmethod
    def _micro_task(connection, run_id: str) -> tuple[dict, str]:
        task = connection.execute(
            "SELECT spec_json,input_fingerprint FROM tasks WHERE task_id=?", (run_id,)
        ).fetchone()
        return json.loads(task["spec_json"]), task["input_fingerprint"]

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

    def _turn_view(self, connection, row, *, include_audit: bool = False, compact: bool = False) -> dict:
        payload = json.loads(row["result_json"]) if row["result_json"] is not None else None
        result = self.result_payload(payload)
        turn = result["turn"] if row["state"] == "returned" else {}
        outcome = turn["outcome"] if row["state"] == "returned" else None
        context = _turn_input_context(row["input_json"])
        adapter = row["execution_adapter"]
        frozen_buddy = context.get("buddy")
        buddy = frozen_buddy if adapter in schemas.CODING_HARNESSES else None
        view = {
            "turnId": row["turn_id"],
            "checkout": row['checkout'],
            "projectKind": row['project_kind'],
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
            "sessionId": turn.get("sessionId"),
            "state": row["state"],
            "disposition": outcome["disposition"] if outcome is not None else None,
            "outcomeTruncated": turn.get("outcomeTruncated"),
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
            "tokenUsage": (payload.get("result") or {}).get("tokenUsage") if payload else None,
        }
        if row['checkout'] == 'none':
            view.update(self._none_notice(row['access']))
        from ..evaluation.native_observations import provider_rejection_view
        failure = provider_rejection_view(result)
        if failure is not None:
            view["providerRejection"] = failure
        input_failure = input_failure_view(payload)
        if input_failure is not None:
            view["inputFailure"] = input_failure
        if compact:
            # The default view is an index: the full input/outcome/provenance is in audit.
            return view
        if outcome is not None:
            summary = outcome.get("summary") or ""
            view["summary"] = summary
            view["remaining"] = outcome.get("remaining", [])
            if include_audit:
                view["request"] = outcome.get("request")
                view["artifactReferences"] = outcome.get("artifacts", [])
        if include_audit:
            view["promptSha256"] = turn.get("promptSha256")
            view["inputSha256"] = turn.get("inputSha256")
            view["input"] = json.loads(row["input_json"])
            view["outcome"] = outcome
            view["provenance"] = turn.get("provenance")
            view["turnResultPath"] = result.get("turnResultPath") if row["state"] == "returned" else None
            view["sealedArtifacts"] = [
                # The former canonical JSON column fixed these keys' byte order.
                self._bounded_path_fields(dict(sorted(item.items())), ("changedPaths", "outsideScope"))
                for item in self._sealed_artifacts(connection, run_id=row["run_id"], turn_id=row["turn_id"])
            ]
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
                "inputFailure": payload.get("inputFailure"),
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
            "inputFailure": payload.get("inputFailure"),
            "decisionId": decision_id,
            "turnId": row["turn_id"],
            "attemptId": row["attempt_id"],
            "childTaskId": row["child_task_id"],
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
            if problem.get("details"):
                view["preparationError"]["details"] = _preparation_details(problem["details"])
        if include_audit:
            view["decision"] = json.loads(row["decision_json"]) if row["decision_json"] else None
            view["payload"] = payload
        return view

    def _child_view(self, connection, row) -> dict:
        return {
            "taskId": row["child_task_id"],
            "state": row["state"],
            "requestId": row["request_id"],
            "workspaceManifestSha256": connection.execute(
                "SELECT (SELECT input_manifest_sha256 FROM workflow_artifacts WHERE run_id=? AND kind='input' ORDER BY rowid LIMIT 1)",
                (row["child_task_id"],)).fetchone()[0],
            "createdAt": row["created_at"],
            "updatedAt": row["updated_at"],
        }

    _bounded_path_fields = staticmethod(_bounded_path_fields)

    def _initial_input_commit(self, connection, run_id):
        row = connection.execute(
            f"WITH manifest_owners AS ({MANIFEST_OWNERS_CTE})"
            " SELECT o.input_commit FROM workflow_artifacts a JOIN manifest_owners o"
            " ON o.workspace_id=a.workspace_id AND o.manifest_sha256=a.input_manifest_sha256"
            " WHERE a.run_id=? AND a.kind='input' ORDER BY a.rowid LIMIT 1", (run_id,)).fetchone()
        return row["input_commit"]

    def _cumulative_patch_view(self, row, record, connection=None):
        if row["content_form"] == "sealed-inline-v1":
            patch = record.get("cumulativePatch")
            return ({key: value for key, value in patch.items() if key != "outputCommit"}
                    if patch is not None else None)
        if connection is None:
            with self.db.read() as reader:
                return self._cumulative_patch_view(row, record, reader)
        if row["kind"] == "partial-output" and row["output_origin"] == "worker":
            attempt = connection.execute("SELECT result_form,result_json FROM attempts WHERE attempt_id=?",
                                         (row["attempt_id"],)).fetchone()
            if cumulative_patch_error(row, attempt) is not None:
                return None
        original = self._initial_input_commit(connection, row["run_id"])
        directory = (self.board.directory / row["record_path"]).parent
        if original == record["inputCommit"]:
            return {"baseCommit": original,
                    "path": str((self.board.directory / row["record_path"]).with_suffix(".patch")), "sha256": record["diffSha256"],
                    "changedPaths": record["changedPaths"]}
        path = directory / ("cumulative-" + original + ".patch")
        from ... import private_files
        from .workspace import _git
        try:
            raw = private_files.read_bytes(path)
        except FileNotFoundError as error:
            raise BoardError("ARTIFACT_MISSING", "The cumulative output patch is missing", path=str(path)) from error
        # The writer uses --no-renames; numstat decodes Git's path quoting and
        # operates on the retained patch without opening a checkout or object DB.
        changes = _git(self.board.directory, "apply", "--numstat", "-z", "--", data=raw,
                       env={"GIT_CEILING_DIRECTORIES": str(self.board.directory.resolve().parent)})
        paths = [part.split(b"\t", 2)[2].decode("utf-8", "surrogateescape")
                 for part in changes.split(b"\0") if part]
        return {"baseCommit": original, "path": str(path),
                "sha256": hashlib.sha256(raw).hexdigest(), "changedPaths": sorted(paths)}

    def _integration_source(self, row, record, cumulative, connection=None):
        if cumulative is None:
            return None
        if sealed_worker_commit(record) is not None:
            if connection is None:
                with self.db.read() as reader:
                    return self._integration_source(row, record, cumulative, reader)
            original = connection.execute(
                "SELECT * FROM workflow_artifacts WHERE run_id=? AND kind='input' ORDER BY rowid LIMIT 1",
                (row["run_id"],)).fetchone()
            initial = self._artifact_document(original)
            seals = connection.execute(
                "SELECT * FROM workflow_artifacts WHERE run_id=? AND kind IN ('output','partial-output','resolved-output')"
                " AND rowid<=(SELECT rowid FROM workflow_artifacts WHERE artifact_id=?) ORDER BY rowid",
                (row["run_id"], row["artifact_id"])).fetchall()
            if (initial["inputCommit"] == initial["baseCommit"]
                    and all(sealed_worker_commit(self._artifact_document(seal)) is not None for seal in seals)):
                return {"kind": "commits", "baseCommit": initial["inputCommit"], "headCommit": record["commit"]}
        return {"kind": "patch", **{key: cumulative[key] for key in ("baseCommit", "path", "sha256") if key in cumulative}}

    @staticmethod
    def _sealed_evidence(record):
        return {"snapshotSha256": record.get("snapshotSha256"), "tree": record.get("tree")}

    def _output_delivery(self, row, record, connection=None):
        delivery = {"sealedEvidence": self._sealed_evidence(record)}
        if row["kind"] in ("output", "resolved-output", "partial-output"):
            cumulative = self._cumulative_patch_view(row, record, connection)
            if cumulative is not None:
                delivery["cumulativePatch"] = self._bounded_path_fields(cumulative, ("changedPaths",))
                delivery["integrationSource"] = self._integration_source(row, record, cumulative, connection)
        return delivery

    def _artifact_view(self, row, *, connection=None) -> dict:
        view = {
            "artifactId": row["artifact_id"],
            "kind": row["kind"],
            "attemptId": row["attempt_id"],
            "turnId": row["turn_id"],
            "sourceTaskId": None if row["kind"] == "input" else row["run_id"],
            # For an output this is the sealed snapshot identity; for an input or a
            # pinned turn manifest it is that manifest's own digest.
            "contentForm": row["content_form"],
            "createdAt": row["created_at"],
        }
        if row['kind'] == 'result':
            # The report remains in its original execution receipt; this is its index.
            if connection is None:
                with self.db.read() as reader:
                    return self._artifact_view(row, connection=reader)
            turn = connection.execute('SELECT access FROM workflow_turns WHERE turn_id=?',
                                      (row['turn_id'],)).fetchone()
            self._artifact_document(row)
            view.update(runId=row['run_id'], generation=row['generation'],
                        resultFile={'path': str(self.board.directory / row['record_path']), 'sha256': row['record_sha256']})
            view.update(self._none_notice(turn['access']))
            return view
        for column, key in (("input_manifest_sha256", "inputManifestSha256"),
                            ("output_snapshot_sha256", "outputSnapshotSha256"),
                            ("shared_refs_sha256", "sharedRefsSha256")):
            if row[column] is not None:
                view[key] = row[column]
        if row["content_form"] == "input-reference":
            view["workspaceRef"] = {"workspaceId": row["workspace_id"], "manifestSha256": row["input_manifest_sha256"]}
        elif row["content_form"] == "record-reference":
            view["recordFile"] = {"path": str(self.board.directory / row["record_path"]), "sha256": row["record_sha256"]}
        if row["content_form"] == "sealed-inline-v1":
            view["manifestSha256"] = (row["input_manifest_sha256"] or row["output_snapshot_sha256"] or row["shared_refs_sha256"])
        manifest = self._artifact_document(row)
        if row["content_form"] == "input-reference":
            view["manifestFile"] = manifest_file_reference(self.board.directory, manifest)
        if row["kind"] in ("output", "resolved-output", "partial-output"):
            if row["kind"] == "partial-output":
                view.update(partial=True, verified=False, final=False)
            view.update(self._output_delivery(row, manifest, connection))
            view["diffPath"] = (str((self.board.directory / row["record_path"]).with_suffix(".patch"))
                                if row["content_form"] == "record-reference" else manifest.get("diffPath"))
            view["diffSha256"] = manifest.get("diffSha256")
            changed = manifest.get("changedPaths")
            if isinstance(changed, list):
                view.update(WorkflowCoordinator._bounded_path_fields(
                    {"changedPaths": changed}, ("changedPaths",)))
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

    def _workspace_view(self, run_row, *, connection=None) -> dict:
        if connection is None:
            with self.db.read() as reader:
                return self._workspace_view(run_row, connection=reader)
        manifest = self._run_manifest(connection, run_row)
        if run_row['checkout'] == 'none':
            return {**manifest, **self._none_notice(manifest['access'])}
        if run_row["state"] not in ("accepted", "concluded"):
            has_file_reference = True
        else:
            turn = connection.execute(
                "SELECT input_json FROM workflow_turns WHERE run_id=? ORDER BY turn_index DESC LIMIT 1",
                (run_row["run_id"],),
            ).fetchone()
            if turn is not None:
                has_file_reference = json.loads(turn["input_json"])["version"] == 4
            else:
                initial = connection.execute(
                    "SELECT content_form FROM workflow_artifacts WHERE run_id=? AND kind='input'"
                    " ORDER BY rowid LIMIT 1", (run_row["run_id"],),
                ).fetchone()
                has_file_reference = initial["content_form"] == "input-reference"
        view = {
            "workspaceId": manifest.get("workspaceId"),
            "path": manifest.get("path"),
            "kind": manifest.get("kind"),
            "checkoutRoot": manifest["checkoutRoot"],
            "projectId": project_id(manifest),
            "access": manifest.get("access"),
            "baseCommit": manifest.get("baseCommit"),
            "inputCommit": manifest.get("inputCommit"),
            "manifestSha256": manifest.get("manifestSha256"),
        }
        if has_file_reference:
            view["manifestFile"] = manifest_file_reference(self.board.directory, manifest)
        return view

    @staticmethod
    def _none_notice(access):
        from ...protocol.turn_input import FOLDER_CHANGES_NOTICE
        return ({'unsealedChanges': True, 'folderChangesNotice': FOLDER_CHANGES_NOTICE}
                if access in ('write', 'full') else {})

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
                "inputManifestSha256": row["manifest_sha256"], "actor": {"role": row["actor_role"], **({"id": row["actor_id"]} if row["actor_id"] is not None else {})} if row["actor_role"] else None, "note": row["note"],
                "updatedAt": row["created_at"]}

    def _open_conflict(self, connection, run_id: str):
        return connection.execute(
            "SELECT * FROM workflow_workspace_conflicts WHERE run_id=? AND state='open'"
            " ORDER BY created_at, conflict_id LIMIT 1",
            (run_id,),
        ).fetchone()

    def _conflict_view(self, connection, row) -> dict:
        entries = []
        evidence = json.loads(row["evidence_json"])
        for entry in evidence["entries"]:
            entries.append({"path": entry.get("path"), "authorized": entry.get("authorized"),
                            "authorizedIndex": entry.get("authorizedIndex"), "observed": entry.get("observed"),
                            "observedIndex": entry.get("observedIndex"), "changed": entry.get("changed"),
                            "indexChanged": entry.get("indexChanged"), "excludedChanged": entry.get("excludedChanged")})
        delivery = self._resolution_delivery_view(row)
        artifact = connection.execute(
            "SELECT * FROM workflow_artifacts WHERE artifact_id=?", (row["artifact_id"],)
        ).fetchone() if row["artifact_id"] else None
        output = self._artifact_document(artifact) if row["artifact_id"] else {}
        paths = WorkflowCoordinator._bounded_path_fields({
            "blockingPaths": evidence["blockingPaths"],
            "resolvedPaths": json.loads(row["resolved_paths_json"]),
            "conflictingPaths": json.loads(row["conflicting_paths_json"]),
        }, ("blockingPaths", "resolvedPaths", "conflictingPaths"))
        return {
            "conflictId": row["conflict_id"],
            "runId": row["run_id"],
            "attemptId": row["attempt_id"],
            "turnId": row["turn_id"],
            "manifestSha256": evidence["manifestSha256"],
            "observedFingerprint": self._conflict_fingerprint(connection, row),
            **paths,
            **WorkflowCoordinator._bounded_path_fields({"paths": entries}, ("paths",)),
            "state": row["state"],
            "action": row["action"],
            "artifactId": row["artifact_id"],
            **(self._output_delivery(artifact, output, connection) if artifact is not None else {}),
            "actor": {"role": row["actor_role"], **({"id": row["actor_id"]} if row["actor_id"] is not None else {})} if row["actor_role"] else None,
            "note": row["note"],
            "delivery": delivery,
            "createdAt": row["created_at"],
            "updatedAt": row["updated_at"],
        }

    def _integration_view(self, connection, row) -> dict:
        verification = json.loads(row["verification_json"])
        artifact = connection.execute(
            "SELECT attempt_id FROM workflow_artifacts WHERE artifact_id=?", (row["artifact_id"],)
        ).fetchone()
        not_required = row["strategy"] == "not-required"
        target = None
        if not not_required:
            target = {"kind": "checkout", "path": row["target_path"], "ref": row["target_ref"],
                      "projectId": row["target_project_id"]}
        return {
            "integrationId": row["integration_id"],
            "runId": row["run_id"],
            "artifactId": row["artifact_id"],
            "attemptId": artifact["attempt_id"],
            "state": "not-required" if not_required else "verified",
            "strategy": row["strategy"],
            "target": target,
            "sourceTree": row["source_tree"],
            "beforeCommit": row["before_commit"],
            "afterCommit": row["after_commit"],
            "beforeTree": row["before_tree"],
            "afterTree": row["after_tree"],
            "verification": WorkflowCoordinator._bounded_path_fields(verification, INTEGRATION_PATH_FIELDS),
            "note": row["note"],
            "actor": {"role": row["actor_role"], **({"id": row["actor_id"]} if row["actor_id"] is not None else {})} if row["actor_role"] else None,
            "createdAt": row["created_at"],
        }

    @staticmethod
    def _plan_view(row) -> dict:
        if row is None:
            return None
        evidence = json.loads(row["evidence_json"])
        if isinstance(evidence.get("workspace"), dict):
            evidence["workspace"] = WorkflowCoordinator._bounded_path_fields(
                evidence["workspace"], ("unsealedPaths",))
        retention = json.loads(row["retention_json"])
        result = ({"alreadyRemoved": json.loads(row["result_json"])["alreadyRemoved"]}
                  if row["result_json"] else None)
        return {
            "planId": row["plan_id"],
            "runId": row["run_id"],
            "workspaceId": row["workspace_id"],
            "path": row["path"],
            "kind": row["kind"],
            "state": row["state"],
            "eligible": row["state"] == "planned",
            "reasons": json.loads(row["reasons_json"]),
            "evidence": evidence,
            "retention": retention,
            "result": result,
            "revision": row["revision"],
            "createdAt": row["created_at"],
            "appliedAt": row["applied_at"],
        }

    def compact(self, connection, run_row, task_row=None) -> dict:
        """The compact micro-task view. Never contains control or scoped tokens."""
        task_row = task_row or connection.execute(
            "SELECT * FROM tasks WHERE task_id=?", (run_row["run_id"],)
        ).fetchone()
        host_conclusion = host_conclusions.current(connection, run_row)
        turns = connection.execute(
            "SELECT t.*,a.result_json,a.adapter AS execution_adapter FROM workflow_turns t LEFT JOIN attempts a ON a.attempt_id=t.attempt_id WHERE t.run_id=? ORDER BY t.turn_index DESC LIMIT ?",
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
        micro_task, micro_task_fingerprint = self._micro_task(connection, run_row["run_id"])
        state = run_row["state"]
        from ..routing.quota_routing import cooling
        view = {
            "runId": run_row["run_id"],
            "state": state,
            "cancellation": self._cancellation(connection, run_row, host_conclusion),
            "status": task_row["state"],
            "queueReason": task_row["queue_reason"],
            "awaitingHost": state == "awaiting-host",
            "microTask": self._micro_task_view(micro_task, micro_task_fingerprint),
            "continuationCount": run_row["continuation_count"],
            "resumeRecovery": self._resume_recovery(connection, run_row["run_id"]),
            "workspace": self._workspace_view(run_row, connection=connection),
            "executionWorkspace": json.loads(run_row["execution_workspace_json"]),
            "access": json.loads(run_row["execution_workspace_json"])["access"],
            "accessNote": json.loads(run_row["execution_workspace_json"]).get("accessNote"),
            "buddy": self._buddy(run_row),
            "delegationAuthority": bool(run_row["delegation_authority"]),
            "leadOwnership": ({"leadRunId": ownership["lead_run_id"], "leadTurnId": ownership["lead_turn_id"],
                               "createdAt": ownership["created_at"]} if ownership is not None else None),
            "ownedMicroTasks": [{"runId": row["owned_run_id"], "leadTurnId": row["lead_turn_id"],
                                 "createdAt": row["created_at"], "state": row["state"]}
                                for row in owned[:MAX_CHILD_VIEW]],
            "buddyRevision": run_row["buddy_revision"],
            "providerCooling": cooling(connection, self._buddy(run_row), now=projection_now),
            "routing": self._routing_view(connection, run_row, now=projection_now),
            "currentTurn": self._turn_view(connection, turns[0]) if turns else None,
            "turns": [self._turn_view(connection, row, compact=True) for row in turns],
            "activeRequest": active_request,
            "requests": [self._request_view(row, compact=True, connection=connection, now=projection_now) for row in requests],
            "pendingRequests": [self._request_view(row, compact=True, connection=connection, now=projection_now) for row in pending[:MAX_REQUEST_VIEW]],
            "children": [self._child_view(connection, row) for row in children],
            "artifacts": [self._artifact_view(row, connection=connection) for row in artifacts],
            "scope": self._current_scope(connection, run_row),
            "workspaceConflicts": [self._conflict_view(connection, row) for row in conflicts],
            "integrations": [self._integration_view(connection, row) for row in integrations],
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
            "shutdown": self.shutdown_summary(connection, run_row["run_id"]),
            "task": self._task_summary(connection, task_row),
        }
        view.update(task_fixes.view(connection, run_row["run_id"]))
        if view["task"].get("providerRejection") is not None:
            view["providerRejection"] = view["task"]["providerRejection"]
        if view["task"].get("inputFailure") is not None:
            view["inputFailure"] = view["task"]["inputFailure"]
        manifest = self._run_manifest(connection, run_row)
        view.update(macroTaskId=run_row["macro_task_id"], project={"id": project_id(manifest)} if manifest else None,
                    actions=self._actions(connection, run_row))
        view['project']['kind'] = run_row['project_kind']
        view['checkout'] = run_row['checkout']
        if run_row['checkout'] == 'none':
            view.update(self._none_notice(view['access']))
        if (current_scope() or {}).get("kind") != AGENT_KIND:
            view.update(title=run_row["title"], goal=macro_tasks.current_goal(connection, run_row["macro_task_id"]))
        return view

    @staticmethod
    def _cancellation(connection, run_row, conclusion):
        if run_row["state"] != "cancelled" and (conclusion is None or conclusion["execution_status"] != "cancelled"):
            return None
        from .cancellation import for_run
        return for_run(connection, run_row["run_id"])

    @staticmethod
    def _micro_task_view(micro_task: dict, fingerprint: str) -> dict:
        """The micro task view with the whole task text; the immutable original spec is in audit."""
        task = micro_task.get("task") if isinstance(micro_task.get("task"), str) else ""
        return {
            "task": task,
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
            "adapter": decorated["adapter"],
            "cwd": decorated["cwd"],
            "cancellation": decorated.get("cancellation"),
            "state": decorated["state"],
            "queueReason": decorated["queueReason"],
            "revision": decorated["revision"],
            "expectedSeconds": decorated["expectedSeconds"],
            "selectedAttemptId": decorated["selectedAttemptId"],
            "selectedAttempt": ({key:decorated["selectedAttempt"][key] for key in ("attemptId","issues")}
                                if decorated.get("selectedAttempt") is not None else None),
            "activeAttemptId": decorated["activeAttemptId"],
            "resultAvailable": decorated["resultAvailable"],
            "tokenUsage": decorated.get("tokenUsage"),
            "providerRejection": decorated.get("providerRejection"),
            "inputFailure": decorated.get("inputFailure"),
            "shutdownConfirmed": decorated["shutdownConfirmed"],
            "terminationReason": decorated["terminationReason"],
            "interruptionEvidence": decorated.get("interruptionEvidence"),
            "stopInitiator": decorated["stopInitiator"],
            "graceDeadline": decorated["graceDeadline"],
            "acceptedAt": decorated["acceptedAt"],
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
        """Resolve the acting role; execution credentials remain confined to ownership."""
        if console_authority is not None:
            return {"role": "console", "id": console_authority["sessionId"]}, "console"
        scope = current_scope()
        if scope is not None and scope["kind"] == AGENT_KIND:
            self._active_credential(connection, scope)
            if not scope["delegationAuthority"] or connection.execute(
                "SELECT 1 FROM workflow_lead_tasks WHERE owned_run_id=? AND lead_run_id=?",
                (run_row["run_id"], scope["runId"]),
            ).fetchone() is None:
                raise BoardError("FORBIDDEN", "A Lead can only act on its own micro tasks")
            return {"role": "lead", "id": scope["runId"]}, "lead"
        if self._owner_actor(connection,run_row)["role"] == "lead":
            raise BoardError("UNAUTHORIZED", "A Lead-owned micro task requires its Lead's active execution credential or an authenticated console")
        if connection.execute("SELECT 1 FROM workflow_children WHERE child_task_id=?", (run_row["run_id"],)).fetchone():
            raise BoardError("UNAUTHORIZED", "A helper action requires its root runId and targetRunId")
        return {"role": "host"}, "host"

    def _owner_actor(self, connection, run_row) -> dict:
        """Resolve ownership from the helper root and the durable Lead relation."""
        row = connection.execute(
            "WITH RECURSIVE owners(run_id) AS (SELECT ? UNION ALL "
            "SELECT c.parent_run_id FROM workflow_children c JOIN owners o ON c.child_task_id=o.run_id) "
            "SELECT l.lead_run_id FROM owners o LEFT JOIN workflow_lead_tasks l ON l.owned_run_id=o.run_id "
            "WHERE NOT EXISTS (SELECT 1 FROM workflow_children c WHERE c.child_task_id=o.run_id)",
            (run_row["run_id"],),
        ).fetchone()
        return {"role": "lead", "id": row["lead_run_id"]} if row["lead_run_id"] is not None else {"role": "host"}

    @staticmethod
    def _expect_current(run_row, snapshot) -> None:
        """Recheck the domain identities after filesystem or catalog work."""
        fields = ("state", "active_request_id", "current_turn_id", "current_attempt_id", "final_artifact_id",
                  "workspace_id", "workspace_manifest_sha256", "current_routing_id", "buddy_revision")
        if any(run_row[key] != snapshot[key] for key in fields):
            raise BoardError("CONFLICT", "The micro task changed while preparing the action; get it again")

    # -- scoped credentials --------------------------------------------------
    @staticmethod
    def _active_credential(connection, scope: dict) -> None:
        """Fence a prepared mutation against execution revocation at commit."""
        if connection.execute("SELECT 1 FROM agent_credentials WHERE credential_id=? AND state='active'",
                              (scope["credentialId"],)).fetchone() is None:
            raise BoardError("UNAUTHORIZED", "The Lead execution credential was revoked before the operation committed")

    def resolve_credential(self, token: str) -> dict:
        """Resolve an attempt-scoped credential; never falls back to service authority."""
        if not isinstance(token, str) or not token:
            raise BoardError("UNAUTHORIZED", "An empty attempt-scoped credential is invalid")
        verifier = self.db.agent_token_verifier(token)
        with self.db.read() as connection:
            row = connection.execute(
                "SELECT c.*, a.generation, r.delegation_authority FROM agent_credentials c"
                " JOIN attempts a ON a.attempt_id=c.attempt_id"
                " JOIN workflow_runs r ON r.run_id=c.run_id"
                " WHERE c.token_verifier=? AND c.state='active'", (verifier,)
            ).fetchone()
        if row is None:
            raise BoardError(
                "UNAUTHORIZED",
                "This attempt-scoped credential is unknown or revoked; the service never substitutes the "
                "administrator token for an invalid scoped credential",
            )
        return {
            "kind": AGENT_KIND,
            "credentialId": row["credential_id"],
            "runId": row["run_id"],
            "taskId": row["run_id"],
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
        if operation == "workflow_get" and any(key in params for key in ("macroTaskId", "cwd")):
            raise BoardError("FORBIDDEN", "An execution credential cannot read project or macro task collections")
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
                if operation == "workflow_continue" and key == "requestId":
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
    @staticmethod
    def _applying_cleanup(connection, checkout_root):
        return connection.execute(
            "SELECT plan_id, path FROM workspace_cleanup_plans WHERE path=?"
            " AND state='applying' ORDER BY rowid DESC LIMIT 1",
            (checkout_root,),
        ).fetchone()

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
            plan = self._applying_cleanup(connection, str(Path(path).resolve()))
        if plan is not None:
            raise BoardError(
                "PREPARATION_CONFLICT",
                "This checkout is being removed by an applying cleanup plan; it cannot be captured as new input",
                checkoutRoot=plan["path"],
                planId=plan["plan_id"],
                path=str(path),
            )

    def _held_writer(self, connection, checkout_root: str):
        return connection.execute(
            "SELECT * FROM workspace_reservations WHERE checkout_root=?"
            " AND state='held' AND access='write' ORDER BY rowid",
            (checkout_root,),
        ).fetchone()

    def _verify_read_snapshot(self, manifest, checkout_root, holder_task_id):
        try:
            workspace_module().verify(manifest, state_dir=self.board.directory, require_unchanged=True)
        except BoardError as error:
            if error.code == "WORKSPACE_CHANGED":
                raise BoardError(
                    "SNAPSHOT_CHANGED", "The checkout no longer matches a reader's frozen input",
                    checkoutRoot=checkout_root, holderTaskId=holder_task_id,
                ) from error
            raise

    def _check_reservations(self, connection, *, manifest, access, holder_task_id=None, transfer_from=None):
        """Check ownership before preparation and again at the atomic claim point."""
        checkout_root = manifest["checkoutRoot"]
        reject_deleting_checkout(connection, manifest, self.board.directory)
        # Reservation admission is the one atomic claim point for a checkout. A cleanup
        # plan in ``applying`` has already passed its inspection but its removal has not
        # finished, so no new writer may take the checkout across that boundary; the
        # check and the insert below commit in the same SQLite write transaction.
        cleanup = self._applying_cleanup(connection, checkout_root)
        if cleanup is not None:
            raise BoardError(
                "PREPARATION_CONFLICT",
                "This checkout is being removed by an applying cleanup plan; no writer may claim it",
                checkoutRoot=checkout_root,
                planId=cleanup["plan_id"],
                path=cleanup["path"],
            )
        held = connection.execute(
            "SELECT * FROM workspace_reservations WHERE checkout_root=? AND state='held' ORDER BY rowid",
            (checkout_root,),
        ).fetchall()
        for row in held:
            if row["holder_task_id"] == holder_task_id:
                continue
            if access == "write" or row["access"] == "write":
                if (access == "write" and row["access"] == "write"
                        and row["holder_task_id"] == transfer_from):
                    continue
                raise BoardError(
                    "PREPARATION_CONFLICT",
                    "A writer requires exclusive access to this checkout; wait for its release or use an isolated worktree.",
                    checkoutRoot=checkout_root, holderTaskId=row["holder_task_id"],
                )
        return held

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
        checkout_root = manifest["checkoutRoot"]
        manifest_sha = manifest["manifestSha256"]
        held = self._check_reservations(connection, manifest=manifest, access=access,
                                        holder_task_id=holder_task_id, transfer_from=transfer_from)
        if access == "write" and transfer_from is not None:
            for row in held:
                if row["holder_task_id"] == transfer_from:
                    connection.execute("UPDATE workspace_reservations SET state='transferred' WHERE reservation_id=?",
                                       (row["reservation_id"],))
        existing = [row for row in held if row["holder_task_id"] == holder_task_id]
        if existing:
            if (existing[0]["checkout_root"] != checkout_root or existing[0]["access"] != access):
                raise BoardError("PREPARATION_CONFLICT", "The held reservation no longer matches the prepared checkout allocation",
                                 checkoutRoot=checkout_root, holderTaskId=holder_task_id)
            return
        connection.execute(
            "INSERT INTO workspace_reservations(reservation_id, workspace_id, checkout_root,"
            " access, holder_task_id, holder_kind, parent_run_id, manifest_sha256, state, created_at,"
            " released_at) VALUES(?,?,?,?,?,?,?,?,'held',?,NULL)",
            (
                str(uuid.uuid4()),
                manifest["workspaceId"],
                checkout_root,
                access,
                holder_task_id,
                holder_kind,
                parent_run_id,
                manifest_sha,
                now,
            ),
        )

    def _release_reservations(self, connection, holder_task_id: str, now: str, *, state: str = "released") -> None:
        # A transferred reservation is still this task's logical ownership: cancelling
        # accepting or concluding the micro task releases it too.
        connection.execute(
            "UPDATE workspace_reservations SET state=?, released_at=? WHERE holder_task_id=?"
            " AND state IN ('held','transferred')",
            (state, now if state == "released" else None, holder_task_id),
        )

    def _reclaim_manual_workspace(self, connection, task, run_row, now: str) -> None:
        """Reacquire released ownership under the same explicit continuation transaction."""
        if run_row["checkout"] == "none":
            return
        manifest = self._run_manifest(connection, run_row)
        reservations = connection.execute(
            "SELECT * FROM workspace_reservations WHERE holder_task_id=? ORDER BY rowid DESC", (task["task_id"],),
        ).fetchall()
        current = next((row for row in reservations if row["state"] in ("held", "transferred")), None)
        reservation = current or next((row for row in reservations if row["checkout_root"] == manifest["checkoutRoot"]), None)
        if (reservation is None or task["cwd"] != manifest["path"]
                or reservation["checkout_root"] != manifest["checkoutRoot"]
                or reservation["access"] != ("read" if manifest["access"] == "read" else "write")):
            raise BoardError("PREPARATION_CONFLICT", "The continuation no longer owns its recorded checkout allocation")
        if reservation["state"] == "transferred":
            # The authorized helper still owns it. Its normal settlement restores
            # this reservation; a manual continuation cannot take it back early.
            return
        conflicts = connection.execute(
            "SELECT * FROM workspace_reservations WHERE checkout_root=? AND holder_task_id!=?"
            " AND (state='held' OR (?='released' AND state='transferred')) AND (access='write' OR ?='write')",
            (manifest["checkoutRoot"], task["task_id"], reservation["state"], "read" if manifest["access"] == "read" else "write"),
        ).fetchall()
        if conflicts:
            raise BoardError("PREPARATION_CONFLICT", "Another owner holds the continuation checkout; wait for release before continuing",
                             checkoutRoot=manifest["checkoutRoot"], holderTaskId=conflicts[0]["holder_task_id"])
        if reservation["state"] == "held":
            return
        if not self._stop_proven(connection, task):
            raise BoardError("SHUTDOWN_UNCONFIRMED", "A released checkout can only be reacquired after every attempt is confirmed stopped")
        self._reserve(connection, manifest=manifest, holder_task_id=task["task_id"],
                      holder_kind=reservation["holder_kind"], parent_run_id=reservation["parent_run_id"],
                      access="read" if manifest["access"] == "read" else "write", now=now)
        self.board._append_event(connection, "workflow.workspace_reclaimed", task_id=task["task_id"],
                                 payload={"checkoutRoot": manifest["checkoutRoot"], "previousReservationId": reservation["reservation_id"]})

    def _stopped_parent(self, connection, run_id: str) -> bool:
        task = connection.execute("SELECT * FROM tasks WHERE task_id=?", (run_id,)).fetchone()
        if task["active_attempt_id"] is not None:
            return False
        attempt = self.board._selected_attempt(connection, task)
        return attempt is not None and attempt["execution_state"] == "finished" and bool(attempt["shutdown_confirmed"])

    def _pin_artifact(self, connection, *, run_id, kind, manifest, attempt_id, turn_id, now, record_file=None):
        input_commit = staged_commit = None
        output_workspace_id = output_manifest_sha = output_commit = output_action = None
        if kind == 'input':
            column, digest = 'input_manifest_sha256', manifest['manifestSha256']
            content_form, body, workspace_id = 'input-reference', None, manifest['workspaceId']
            role, workspace_kind, checkout_root, repository_path, input_commit, staged_commit = workspace_facts(connection, manifest)
            record_path = record_sha = origin = None
        elif kind == self.SHARED_REFS_KIND:
            column, digest = 'shared_refs_sha256', manifest['manifestSha256']
            content_form, body, workspace_id = 'sealed-inline-v1', canonical_json(manifest), None
            role = workspace_kind = checkout_root = repository_path = record_path = record_sha = origin = None
        elif kind in ('output', 'partial-output', 'resolved-output', 'abandoned-site'):
            column, digest = 'output_snapshot_sha256', manifest['snapshotSha256']
            reference = record_file if record_file is not None else record_reference(self.board.directory, manifest)['recordFile']
            content_form, body, workspace_id = 'record-reference', None, None
            record_path, record_sha = reference['path'], reference['sha256']
            role = workspace_kind = checkout_root = repository_path = None
            origin = ('host' if manifest.get('kind') == 'host-output' else 'worker') if kind in ('output','partial-output') else None
            output_workspace_id, output_manifest_sha = manifest['workspaceId'], manifest['manifestSha256']
            output_commit = manifest['commit']
            output_action = manifest['action'] if kind == 'resolved-output' else None
        else:
            raise BoardError('WORKSPACE_INVALID', 'Unsupported current artifact kind', kind=kind)
        row = connection.execute(
            f"SELECT artifact_id FROM workflow_artifacts WHERE run_id=? AND attempt_id IS ? AND {column}=?",
            (run_id, attempt_id, digest)).fetchone()
        if row is not None:
            return row['artifact_id']
        artifact_id = str(uuid.uuid4())
        connection.execute(
            f"INSERT INTO workflow_artifacts(artifact_id,run_id,turn_id,attempt_id,kind,content_form,manifest_json,"
            f"workspace_id,{column},record_path,record_sha256,workspace_facts_role,workspace_kind,checkout_root,repository_path,output_origin,created_at,"
            "input_commit,staged_commit,output_workspace_id,output_manifest_sha256,output_commit,output_action)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (artifact_id,run_id,turn_id,attempt_id,kind,content_form,body,workspace_id,digest,
             record_path,record_sha,role,workspace_kind,checkout_root,repository_path,origin,now,
             input_commit,staged_commit,output_workspace_id,output_manifest_sha,output_commit,output_action))
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

        buddy = validate_buddy(schemas.normalize_buddy(constraints), directory=self.board.directory)
        with self.db.read() as connection:
            self._require_enabled_buddy(connection, buddy)
        return buddy

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

    def _routing_boundary(self, connection, decision, run, *, now=None):
        """Project a frozen decision with the current root and routing facts."""
        from ..routing.router_boundary_data import routing_boundary

        owner = run
        while True:
            parent = connection.execute(
                "SELECT parent_run_id FROM workflow_children WHERE child_task_id=?", (owner["run_id"],)
            ).fetchone()
            if parent is None:
                break
            owner = self._run_row(connection, parent["parent_run_id"])
        # Establishing the leaf boundary increments its revision and propagates
        # one boundary transition to each ancestor in the same transaction.
        return routing_boundary(
            connection, decision, now=now if now is not None else self.now(), run_id=run["run_id"],
            owner_run_id=owner["run_id"], state_dir=self.board.directory,
        )

    def _routing_view(self, connection, run, *, now=None) -> dict:
        from ..routing.router import selection_source
        micro_task, _ = self._micro_task(connection, run["run_id"])
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
                                                " AND workspace_id IS NULL LIMIT 1", (run["run_id"],)).fetchone() is not None)
            return {"status": "needs-host" if needs_host else "queued" if preparing else "explicit", "decisionId": None, "taskId": None,
                    "constraints": schemas.buddy_constraints(micro_task),
                    "source": None if preparing else source}
        link = connection.execute("SELECT * FROM workflow_routes WHERE decision_id=?", (run["current_routing_id"],)).fetchone()
        decision = self.board.decisions._row(connection, run["current_routing_id"])
        status = decision["status"] if link["state"] == "pending" else {
            "resolved": "completed", "needs-host": "needs-host", "fenced": "fenced",
        }[link["state"]]
        requested = json.loads(decision["requested_json"])
        from ..routing.router_sequence import request_snapshot
        routing_request = {**requested, **request_snapshot(connection, decision["decision_id"])["facts"]}
        current = self.board.decisions._view(connection, decision, include_audit=False)
        view = {
            "status": status, "decisionId": decision["decision_id"], "taskId": decision["decision_task_id"],
            "attemptId": decision["decision_attempt_id"], "generation": decision["decision_generation"],
            "selectedBuddy": json.loads(decision["selected_json"]) if decision["selected_json"] else None,
            "reason": current["reason"],
            "note": current["note"],
            "constraints": requested.get("constraints", schemas.buddy_constraints(micro_task)),
            "requiredCapabilities": requested.get("requiredCapabilities", []),
            "source": selection_source(routing_request),
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
        micro_task, _ = self._micro_task(connection, run_id)
        if run["continuation_count"]:
            micro_task = {key: value for key, value in micro_task.items() if key not in schemas.BUDDY_FIELDS}
        response = self.board.decisions.route_workflow(
            connection, run_id=run_id, sequence=sequence, spec=micro_task,
            manifest=self._run_manifest(connection, run),
            overloaded_model_services=self._overloaded_model_services(connection, run_id),
        )
        decision_id = response["decisionId"]
        connection.execute(
            "INSERT INTO workflow_routes(decision_id, run_id, state, created_at) VALUES(?,?,'pending',?)", (decision_id,run_id,now,),
        )
        connection.execute(
            "UPDATE workflow_runs SET current_routing_id=?, buddy_json=NULL,"
            " validated_buddy_revision=NULL WHERE run_id=?",
            (decision_id, run_id),
        )
        connection.execute("UPDATE tasks SET adapter='unresolved', queue_reason='awaiting-model-selection' WHERE task_id=?", (run_id,))
        self.board._append_event(connection, "workflow.routing_requested", task_id=run_id,
                                 payload={"decisionId": decision_id, "routingTaskId": response["runId"]})
        self.routing_settled(connection, decision_id=decision_id, now=now)

    def routing_settled(self, connection, *, decision_id: str, now: str) -> None:
        """Adopt a frozen recommendation with its result, under owner/lineage fencing."""
        from ..routing.router import selection_source
        from ..routing.router_sequence import request_snapshot
        link = connection.execute("SELECT * FROM workflow_routes WHERE decision_id=?", (decision_id,)).fetchone()
        if link is None or link["state"] != "pending":
            return
        run = self._run_row(connection, link["run_id"])
        decision = self.board.decisions._row(connection, decision_id)
        if decision["status"] in ("queued", "running"):
            return
        if (run["current_routing_id"] != decision_id
                or run["state"] != "executing" or self._terminal_ancestor(connection, run["run_id"]) is not None):
            connection.execute("UPDATE workflow_routes SET state='fenced' WHERE decision_id=?", (decision_id,))
            return
        selected = json.loads(decision["selected_json"]) if decision["selected_json"] else None
        if decision["status"] == "completed" and selected is not None:
            buddy = schemas.buddy_constraints(selected)
            micro_task, _ = self._micro_task(connection, run["run_id"])
            constraints = (schemas.buddy_constraints(micro_task)
                           if not run["continuation_count"] else {})
            if (len(buddy) == len(schemas.BUDDY_FIELDS)
                    and buddy.get("harness") in schemas.CODING_HARNESSES
                    and all(buddy.get(key) == value for key, value in constraints.items())):
                connection.execute("UPDATE workflow_routes SET state='resolved' WHERE decision_id=?",
                                   (decision_id,))
                connection.execute(
                    'UPDATE workflow_runs SET buddy_json=?, buddy_revision=buddy_revision+1, validated_buddy_revision=NULL, updated_at=? WHERE run_id=?',
                    (canonical_json(buddy), now, run["run_id"]),
                )
                connection.execute("UPDATE tasks SET adapter=?, queue_reason='awaiting-buddy-validation' WHERE task_id=?",
                                   (buddy["harness"], run["run_id"]))
                self.board._append_event(connection, "workflow.routing_resolved", task_id=run["run_id"],
                                         payload={"decisionId": decision_id, "buddy": buddy,
                                                  "buddyId": decision["buddy_id"],
                                                  "source": selection_source({
                                                      **json.loads(decision["requested_json"]),
                                                      **request_snapshot(connection, decision_id)["facts"],
                                                  })})
                return
        own_reason = (make_reason("routing-unusable-selection") if decision["status"] == "completed" else None)
        self._routing_attention(connection, run, now, own_reason)

    def _routing_attention(self, connection, run, now: str, reason: dict | None = None) -> None:
        """A service boundary, with no invented agent turn or completed Micro task."""
        if run["current_routing_id"]:
            connection.execute("UPDATE workflow_routes SET state='needs-host' WHERE decision_id=?",
                               (run["current_routing_id"],))
        request_id = f"routing-{uuid.uuid4()}"
        decision_id = run["current_routing_id"]
        payload = {"source": "routing"}
        connection.execute("UPDATE workflow_runs SET state='awaiting-host', active_request_id=?, updated_at=? WHERE run_id=?",
                           (request_id, now, run["run_id"]))
        self._record_system_request(connection, run, reason, payload, now,
                                    request_id=request_id, decision_id=decision_id)
        self.board._append_event(connection, "workflow.routing_needs_host", task_id=run["run_id"],
                                 payload={"requestId": request_id, "decisionId": run["current_routing_id"]})

    @staticmethod
    def _dispatch_identity(run) -> tuple:
        return tuple(run[key] for key in ("current_routing_id", "buddy_revision", "buddy_json", "state", "current_turn_id", "current_attempt_id"))

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

    def attach_micro_task(self, connection, task_row, *, spec, executionWorkspace, manifest, projectFacts, creationKey,
                          buddy=None, macroTaskId=None, title, delegationAuthority=False, lead=None, fixes=None, now):
        """Bind task admission, scope, workspace, lineage and the unique creation key atomically."""
        run_id = task_row["task_id"]
        if lead is not None:
            self._active_credential(connection, lead)
        projects.require_capture_identity(projectFacts, manifest)
        projects.record(connection, projectFacts)
        identifier = projectFacts["projectId"]
        macro_tasks.attach_macro_task(connection, macroTaskId, project_id=identifier)
        if fixes is not None:
            fixed = self._run_row(connection, fixes)
            actor = {"role": "lead", "id": lead["runId"]} if lead else {"role": "host"}
            if self._owner_actor(connection, fixed) != actor:
                raise BoardError("FORBIDDEN", "fixes must be owned by the submitting role")
            if fixed["state"] != "accepted" or fixed["macro_task_id"] != macroTaskId:
                raise BoardError("INVALID_STATE", "fixes must be accepted in the same macro task")
            fixed_project = (connection.execute("SELECT project_id FROM macro_tasks WHERE macro_task_id=?", (macroTaskId,)).fetchone()[0]
                             if macroTaskId is not None else fixed["standalone_project_id"])
            if fixed_project != identifier:
                raise BoardError("INVALID_ARGUMENT", "fixes must be in the same project")
        connection.execute(
            "INSERT INTO workflow_runs(run_id,creation_key,execution_workspace_json,workspace_id,workspace_manifest_sha256,"
            "macro_task_id,standalone_project_id,title,project_kind,checkout,state,delegation_authority,created_at,updated_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,'executing',?,?,?)",
            (run_id, creationKey, canonical_json(executionWorkspace), manifest["workspaceId"] if manifest else None, manifest["manifestSha256"] if manifest else None,
             macroTaskId, identifier if macroTaskId is None else None, title, projectFacts["projectKind"], executionWorkspace["kind"], int(delegationAuthority), now, now),
        )
        if lead is not None:
            connection.execute("INSERT INTO workflow_lead_tasks(owned_run_id,lead_run_id,lead_turn_id,created_at) VALUES(?,?,?,?)",
                               (run_id, lead["runId"], lead["turnId"], now))
            self.board._append_event(connection, "workflow.lead_task_created", task_id=run_id,
                                     payload={"leadRunId": lead["runId"], "leadTurnId": lead["turnId"]})
        if fixes is not None:
            connection.execute("INSERT INTO workflow_fixes(run_id,fixed_run_id,created_at) VALUES(?,?,?)", (run_id, fixes, now))
            self.board._append_event(connection, "workflow.fix_submitted", task_id=run_id, payload={"fixes": fixes})
        actor = {"role": "lead", "id": lead["runId"]} if lead else {"role": "host"}
        if delegationAuthority:
            self.board._append_event(connection, "workflow.delegation_authority_granted", task_id=run_id,
                                     payload={"actor": actor, "buddy": buddy})
        connection.execute('UPDATE tasks SET cwd=?, updated_at=? WHERE task_id=?', (manifest["path"] if manifest else executionWorkspace["cwd"], now, run_id))
        connection.execute(
            "INSERT INTO workflow_scope_versions(run_id,scope_version,actor_role,actor_id,note,write_scope_json,"
            "manifest_sha256,stopped_evidence_json,created_at) VALUES(?,1,?,?,NULL,?,?,?,?)",
            (run_id, actor["role"], actor.get("id"), canonical_json(manifest.get("writeScope", []) if manifest else []),
             manifest["manifestSha256"] if manifest else None, canonical_json({"kind": "initial"}), now),
        )
        if manifest is not None:
            self._reserve(connection, manifest=manifest,holder_task_id=run_id,holder_kind="parent",parent_run_id=run_id,
                          access="read" if executionWorkspace["access"] == "read" else "write",now=now)
            self._pin_artifact(connection,run_id=run_id,kind="input",manifest=manifest,attempt_id=None,turn_id=None,now=now)
        if buddy is None:
            self._start_routing(connection, run_id, now)
        else:
            connection.execute('UPDATE workflow_runs SET buddy_json=?, buddy_revision=1, validated_buddy_revision=1 WHERE run_id=?',
                               (canonical_json(buddy), run_id))
            self.board._append_event(connection,"workflow.buddy_selected",task_id=run_id,
                                     payload={"source":"original-explicit","buddy":buddy,"buddyRevision":1})

    def normalize_submission(self, params: dict) -> dict:
        """Validate the public model without directory, catalog or workspace work."""
        from ...protocol.requests import parse_request, dump_request
        scope = current_scope() or {}
        lead = scope if scope.get("kind") == AGENT_KIND else None
        model = parse_request("submit", params, audience="lead" if lead else "service")
        return dump_request(model)

    def submit(self, params: dict) -> dict:
        return self.submit_normalized(self.normalize_submission(params))

    def submit_normalized(self, normalized: dict, *, prepare_buddy=None) -> dict:
        """Admit a new key once, or return the original authorization and execution choice."""
        scope = current_scope() or {}
        lead = scope if scope.get("kind") == AGENT_KIND else None
        with self.db.read() as connection:
            if lead:
                self._active_credential(connection, lead)
            macro_task_id = self._run_row(connection, lead["runId"])["macro_task_id"] if lead else normalized["macroTaskId"]
        identity = projects.identify_for_board(self.board, normalized['cwd'], module=workspace_module())
        namespace = ('project:' + identity['projectId'] if macro_task_id in (None, 'standalone')
                     else 'macro:' + macro_task_id)
        key = projects.creation_key(['adr032-submit-v1', namespace, normalized['title'], normalized['task']])
        with self.db.read() as connection:
            if lead:
                self._active_credential(connection, lead)
            existing = connection.execute("SELECT * FROM workflow_runs WHERE creation_key=?", (key,)).fetchone()
            if existing is not None:
                if lead and connection.execute(
                    "SELECT 1 FROM workflow_lead_tasks WHERE owned_run_id=? AND lead_run_id=?",
                    (existing["run_id"], lead["runId"]),
                ).fetchone() is None:
                    raise BoardError("FORBIDDEN", "The creation key belongs to another Lead's micro task")
                reject_deleting_run(connection, existing["run_id"])
                return self._action_view(connection, existing, already_exists=True)
        if prepare_buddy is not None:
            prepare_buddy()
        if normalized["checkout"] != "none" and identity["projectKind"] != "git":
            raise BoardError("WORKSPACE_UNSUPPORTED", "A Git checkout requires a Git project", field="checkout")
        normalized["cwd"] = identity["cwd"]
        spec = {key: normalized[key] for key in ("task", "cwd", "expectedSeconds")}
        buddy = self._named_buddy(normalized["buddy"])
        if buddy is not None:
            spec.update(buddy)
        intent = {"kind": normalized["checkout"], "access": normalized["access"], "cwd": normalized["cwd"],
                  "includeUntracked": normalized.get("inputs", []),
                  "writeScope": normalized.get("writeScope", []),
                  "integrator": {"role": "lead", "id": lead["runId"]} if lead else {"role": "host"}}
        if normalized["checkout"] == "none":
            intent.pop("includeUntracked")
            intent.pop("writeScope")
            intent['projectId'] = identity['projectId']
        if "accessNote" in normalized:
            intent["accessNote"] = normalized["accessNote"]
        self._require_permission(buddy, normalized["access"])
        buddy = self._validated_buddy(spec)
        request_id = "creation-" + key
        if intent["kind"] == "worktree":
            self._cleanup_fence(intent["cwd"])
        with self._submission_preparation(request_id, intent) as prepared:
            response = self.board.task_submit(
                {"requestId": request_id}, task_id=str(uuid.uuid4()),
                micro_task={"spec": spec, "executionWorkspace": intent, "manifest": prepared["manifest"],
                            "projectFacts": identity, "creationKey": key, "buddy": buddy, "macroTaskId": None if macro_task_id == "standalone" else macro_task_id,
                            "title": normalized["title"], "delegationAuthority": normalized.get("delegationAuthority", False),
                            "lead": lead, "fixes": normalized.get("fixes")},
            )
            with self.db.read() as connection:
                return self._action_view(connection, self._run_row(connection, response["task"]["runId"]),
                                         already_exists=response["duplicate"])

    @contextmanager
    def _submission_preparation(self, request_id, intent, *, shared_locks=None, transfer_from=None):
        if intent["kind"] == "none":
            yield {"manifest": None, "newAllocation": False}
            return
        if intent["kind"] == "existing":
            actual = workspace_module().inspect(intent["cwd"])
            with self.db.read() as connection:
                existing = connection.execute("SELECT task_id FROM tasks WHERE request_id=?", (request_id,)).fetchone()
                holder_id = existing["task_id"] if existing is not None else None
                held = self._check_reservations(connection, manifest=actual,
                                                access="read" if intent["access"] == "read" else "write",
                                                holder_task_id=holder_id, transfer_from=transfer_from)
                readers = [(row["holder_task_id"], self._run_manifest(connection, self._run_row(connection, row["holder_task_id"])))
                           for row in held if row["access"] == "read" and row["holder_task_id"] != holder_id]
            # Capture each held reader's view before pinning anything. All Git
            # and file observations happen after the short read transaction ends.
            for reader_id, manifest in readers:
                self._verify_read_snapshot(manifest, actual["checkoutRoot"], reader_id)
        with workspace_module().submission_preparation(
                self.board.directory, request_id, {**intent, "base": {"kind": "working-tree"}}, shared_locks=shared_locks,
                before_prepare=self._prepare_allocation) as prepared:
            try:
                yield prepared
            except Exception as error:
                if prepared["newAllocation"]:
                    self._revoke_submission_allocation(request_id, prepared, error, batch=shared_locks is not None)
                raise

    def _prepare_allocation(self, directory):
        from . import allocation_revocations
        allocation_revocations.prepare(self.board, directory)

    def _revoke_submission_allocation(self, request_id, prepared, original_error, *, batch):
        """Revoke this invocation's new allocation without replacing admission errors.

        The original preparation lock stays held through physical inspection,
        the read-only reference snapshot, and removal of the checkout, refs and records.
        The revocation is durable before any removal. Lock-file and
        directory removal finishes before releasing the original lock.
        """
        from . import allocation_revocations, storage
        module = workspace_module()
        manifest = prepared["manifest"]
        facts = {"requestId": request_id, "workspaceId": manifest["workspaceId"],
                 "path": manifest["checkoutRoot"], "manifestSha256": manifest["manifestSha256"],
                 "removed": False}

        def cleanup_error(error):
            facts["removed"] = False
            facts["cleanupError"] = (error.payload() if isinstance(error, BoardError)
                                     else {"code": type(error).__name__, "message": str(error)[:2000]})

        def record():
            if isinstance(original_error, BoardError):
                if batch:
                    original_error.details.setdefault("helperPreparationCleanup", []).append(facts)
                else:
                    original_error.details["submissionCleanup"] = facts
            else:
                original_error.add_note("Submission cleanup: " + json.dumps(facts, sort_keys=True))

        try:
            plan = module.submission_revoke_plan(self.board.directory, manifest)
            directory = plan["directory"]
            with self.db.read() as connection:
                reasons = storage.submission_references(connection, manifest, request_id, directory)
            if reasons or plan["reasons"]:
                facts["reasons"] = sorted(set(reasons + plan["reasons"]))
            else:
                with storage.locked(self.board):
                    allocation_revocations.record(self.board, directory, request_id)
                    module.revoke_submission(plan)
                facts['removed'] = allocation_revocations.finish(self.board, directory)
        except Exception as error:
            cleanup_error(error)
        record()
        return facts







    def compact_read(self, run_id: str) -> dict:
        with self.db.read() as connection:
            run_row = self._run_row(connection, run_id)
            return self.compact(connection, run_row)

    def _named_buddy(self, name: str) -> dict | None:
        if name == "route":
            return None
        with self.db.read() as connection:
            row = connection.execute("SELECT harness,provider,model,effort FROM buddies WHERE buddy_id=?", (name,)).fetchone()
        if row is None:
            raise BoardError("INVALID_ARGUMENT", "buddy must be route or a complete recorded buddy name", buddy=name)
        return dict(row)

    def _action_view(self, connection, run, *, already_exists=None, **facts):
        actions = self._actions(connection, run)
        result = {"runId": run["run_id"], "state": run["state"], "actions": actions, **facts}
        active = self._open_boundary(connection,run)
        if active is not None:
            if "requestId" in facts and facts["requestId"] != active["request_id"]:
                result.update(nextRequestId=active["request_id"],nextRequestKind=active["kind"])
            else:
                result.update(requestId=active["request_id"],requestKind=active["kind"])
        elif run["state"] == "delivered" and run["final_artifact_id"] is not None:
            result.setdefault("artifactId",run["final_artifact_id"])
        elif (run["state"] in ("failed","cancelled") or self._queued_lead_wakeup(connection,run)) and run["current_turn_id"] is not None:
            fixed = connection.execute("SELECT artifact_id FROM workflow_artifacts WHERE run_id=? AND turn_id=? AND kind IN ('output','partial-output','resolved-output','result') ORDER BY rowid DESC LIMIT 1",
                                       (run["run_id"],run["current_turn_id"])).fetchone()
            result.setdefault("artifactId" if fixed else "turnId",fixed[0] if fixed else run["current_turn_id"])
        if already_exists is not None:
            choice = json.loads(run["execution_workspace_json"])
            result.update(alreadyExists=already_exists, access=choice["access"], buddy=self._buddy(run), checkout=choice["kind"])
        return result

    def _actions(self, connection, run):
        state = run["state"]
        if state in ("accepted", "concluded"):
            return ["get"]
        if self.shutdown_summary(connection, run["run_id"])["unconfirmedCount"]:
            return ["await", "inquire", "cancel", "get"]
        if self._open_boundary(connection, run) is not None:
            return ["continue", "cancel", "get"]
        if self._queued_lead_wakeup(connection,run):
            return ["continue", "await", "inquire", "cancel", "get"]
        if state == "delivered":
            return ["accept", "continue", "conclude", "get"]
        if state in ("failed", "cancelled"):
            return (["continue"] if run["current_turn_id"] is not None else []) + ["conclude", "get"]
        return ["await", "inquire", "cancel", "get"]

    @staticmethod
    def _queued_lead_wakeup(connection,run):
        """An unconsumed wakeup has not replaced the Lead's stopped result."""
        return run["state"] == "executing" and bool(connection.execute(
            "SELECT 1 FROM workflow_continuations c JOIN tasks t ON t.task_id=c.run_id"
            " WHERE c.run_id=? AND c.kind='lead-wakeup' AND c.state='queued'"
            " AND t.state='queued' AND t.active_attempt_id IS NULL",(run["run_id"],)).fetchone())

    def create(self, params: dict) -> dict:
        from ...protocol.requests import parse_request, dump_request
        request = dump_request(parse_request("create", params, audience="service"))
        identity = projects.identify_for_board(self.board, request['cwd'], module=workspace_module())
        key = projects.creation_key(['adr032-create-v1', identity['projectId'], request['title']])
        # A replay does not inspect a changed goal or prepare execution input.
        with self.db.read() as connection:
            row = connection.execute("SELECT macro_task_id FROM macro_tasks WHERE creation_key=?", (key,)).fetchone()
        if row is not None:
            result = macro_tasks.macro_task_get(self.board, {"macroTaskId": row[0]})
            return {key: result[key] for key in ("macroTaskId", "state", "actions") if key in result} | {"alreadyExists": True}
        request.update(identity, creationKey=key)
        result = macro_tasks.create_macro_task(self.board, request)
        return {key: result[key] for key in ("macroTaskId", "state", "actions", "alreadyExists") if key in result}

    def amend(self, params: dict) -> dict:
        from ...protocol.requests import parse_request, dump_request
        request = dump_request(parse_request("amend", params, audience="service"))
        result = macro_tasks.amend_macro_task(self.board, request)
        return {key: result[key] for key in ("macroTaskId", "state", "actions", "alreadyCurrent", "goalRevision") if key in result}

    def get(self, params: dict) -> dict:
        from ...protocol.requests import parse_request, dump_request
        request = dump_request(parse_request("get", params, audience="service"))
        if "macroTaskId" in request:
            return macro_tasks.macro_task_get(self.board, request)
        if "cwd" in request:
            identity = projects.find_for_board(self.board, request['cwd'], module=workspace_module())
            if identity is None:
                return {'project': None, 'items': [], 'nextCursor': None}
            return macro_tasks.project_get(self.board, {**request, "projectId": identity["projectId"], 'projectFacts': identity})
        run_id = request["runId"]
        with self.db.read() as connection:
            task = connection.execute("SELECT * FROM tasks WHERE task_id=?", (run_id,)).fetchone()
            if task is None:
                raise BoardError("NOT_FOUND", "Unknown runId", runId=run_id)
            run = self._run_optional(connection, run_id)
            if run is None:
                return {"runId": run_id, "task": self.board._decorate(connection, task),
                        "inquiries": self._inquiries(connection, run_id)}
            view = self.compact(connection, run, task)
            view["inquiries"] = self._inquiries(connection, run_id)
            if "routingHistory" in request:
                history = request["routingHistory"]
                view["routingHistory"] = self._routing_history(connection, run, limit=history.get("limit", DEFAULT_ROUTING_HISTORY_LIMIT),
                                                              before=history.get("before"))
            if request.get("includeAudit"):
                view["audit"] = self._audit(connection, run)
            return view

    def _inquiries(self, connection, run_id: str) -> list[dict]:
        return [self.board._message_view(row) for row in connection.execute(
            "SELECT * FROM messages WHERE task_id=? AND direction='question' ORDER BY created_at,rowid",
            (run_id,),
        )]

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
        from ..routing.router_sequence import dispatch, request_snapshot
        run_id = run_row["run_id"]
        total = int(
            connection.execute(
                "SELECT COUNT(*) AS count FROM workflow_routes WHERE run_id=?", (run_id,)
            ).fetchone()["count"]
        )
        query = (
            "SELECT r.rowid AS route_rowid, r.decision_id, r.state,"
            " r.created_at, d.status AS decision_status, d.reason_code, d.reason_params_json, d.note,"
            " q.task_id AS decision_task_id, q.selected_json, q.requested_json,"
            " j.domain_node_id, j.task_type, n.name AS domain_node_name"
            " FROM workflow_routes r"
            " JOIN evaluation_decisions d ON d.decision_id=r.decision_id"
            " JOIN decision_requests q ON q.decision_id=r.decision_id"
            " LEFT JOIN routing_judgments j ON j.decision_id=r.decision_id"
            " LEFT JOIN domain_nodes n ON n.node_id=j.domain_node_id"
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
            routing_request = {**requested, **request_snapshot(connection, row["decision_id"])["facts"]}
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
                    "source": selection_source(routing_request),
                    "reason": reason_from_json(row["reason_code"], row["reason_params_json"]),
                    "note": row["note"],
                    "createdAt": row["created_at"],
                    "current": bool(current_id) and row["decision_id"] == current_id,
                    **({"domainNodeId": row["domain_node_id"], "domainNodeName": row["domain_node_name"],
                        "taskType": row["task_type"]} if row["task_type"] is not None else {}),
                    **routing_facts(routing_request, actor=dispatch(connection, row["decision_task_id"]) if row["decision_task_id"] else None),
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
            "SELECT t.*,a.result_json,a.adapter AS execution_adapter FROM workflow_turns t LEFT JOIN attempts a ON a.attempt_id=t.attempt_id WHERE t.run_id=? ORDER BY t.turn_index", (run_row["run_id"],)
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
        micro_task, micro_task_fingerprint = self._micro_task(connection, run_row["run_id"])
        return {
            "hostConclusions": [host_conclusions.view(row) for row in conclusions[:50]],
            "hostConclusionsTruncated": len(conclusions) > 50,
            "turns": [self._turn_view(connection, row, include_audit=True) for row in turns],
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
                    "checkoutRoot": row["checkout_root"],
                    "workspaceId": row["workspace_id"],
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
                    "author": f"attempt:{row['attempt_id']}" if row["attempt_id"] else "agent",
                    "body": row["body"],
                    "createdAt": row["created_at"],
                }
                for row in suggestions
            ],
            "microTaskFingerprint": micro_task_fingerprint,
            "microTask": micro_task,
        }

    # -- decide --------------------------------------------------------------
    def _normalize_helper(self, packet, *, request_id, index, integrator):
        spec = {key: packet[key] for key in ("task", "cwd", "expectedSeconds")}
        buddy = self._named_buddy(packet["buddy"])
        if buddy:
            spec.update(buddy)
        identity = projects.identify_for_board(self.board, packet["cwd"], module=workspace_module())
        spec["cwd"] = identity["cwd"]
        if packet["checkout"] != "none" and identity["projectKind"] != "git":
            raise BoardError("WORKSPACE_UNSUPPORTED", "A Git checkout requires a Git project", field="checkout")
        intent = {"kind": packet["checkout"], "access": packet["access"], "cwd": packet["cwd"],
                  "includeUntracked": packet.get("inputs", []),
                  "writeScope": packet.get("writeScope", []), "integrator": integrator}
        intent["cwd"] = identity["cwd"]
        if packet["checkout"] == "none":
            intent.pop("includeUntracked")
            intent.pop("writeScope")
            intent['projectId'] = identity['projectId']
        if "accessNote" in packet:
            intent["accessNote"] = packet["accessNote"]
        return {"requestId": "helper-" + sha256_text(canonical_json([request_id, index])), "spec": spec,
                "executionWorkspace": intent, "projectFacts": identity, "integrator": integrator, "title": packet["title"]}



    def _decide(self, params, *, console_authority=None):
        run_id, request_id = params["runId"], params["requestId"]
        decision = params["decision"]
        payload = {"targetRunId": params.get("targetRunId", run_id),
                   **{key: value for key, value in params.items() if key not in ("runId", "requestId", "targetRunId")}}
        with self.db.read() as connection:
            owner = self._run_row(connection, run_id)
            self._authorize(connection, owner, params, console_authority=console_authority, action="A boundary decision")
            if "targetRunId" in params:
                self._target_run(connection, owner, params["targetRunId"])
            request = self._request_row(connection, run_id, request_id)
            replay = self._decision_replay(connection, owner, request, payload)
            if replay is not None:
                return replay
            self._require_open_request(connection, owner, request)
            if json.loads(request["payload_json"]).get("source") == "routing":
                raise BoardError("BUDDY_REQUIRED", "Resolve this routing request with buddy and input on continue")
            source, _ = self._attention_chain(connection, owner, request)[-1]
            source_id = source["run_id"]
            if source_id != run_id and "targetRunId" not in params:
                raise BoardError("INVALID_ARGUMENT", "A helper boundary response requires targetRunId", field="targetRunId")
            if "targetRunId" in params and params["targetRunId"] != source_id:
                raise BoardError("CONFLICT", "targetRunId must name the source of this boundary request")
            integrator = self._owner_actor(connection, source)
        helpers = [self._normalize_helper(packet, request_id=request_id, index=index, integrator=integrator)
                   for index, packet in enumerate(params.get("helpers", []))]
        with self._helper_preparations(helpers, transfer_from=source_id) as prepared:
            now = self.now()
            with self.db.write() as connection:
                owner = self._run_row(connection, run_id)
                actor, _ = self._authorize(connection, owner, params, console_authority=console_authority, action="A boundary decision")
                if "targetRunId" in params:
                    self._target_run(connection, owner, params["targetRunId"])
                request = self._request_row(connection, run_id, request_id)
                replay = self._decision_replay(connection, owner, request, payload)
                if replay is not None:
                    return replay
                self._require_open_request(connection, owner, request)
                if json.loads(request["payload_json"]).get("source") == "routing":
                    raise BoardError("BUDDY_REQUIRED", "Resolve this routing request with buddy and input on continue")
                chain = self._attention_chain(connection, owner, request)
                source, leaf = chain[-1]
                if source["run_id"] != source_id:
                    raise BoardError("CONFLICT", "The requesting helper changed during preparation; get again")
                self._lineage_stop_evidence(connection, source, "Boundary response")
                task = connection.execute("SELECT * FROM tasks WHERE task_id=?", (source_id,)).fetchone()
                children = self._create_helpers(connection, source, leaf, prepared, now, actor=actor) if decision == "approve" else []
                for _, item in chain:
                    changed = connection.execute(
                        "UPDATE workflow_requests SET state=?,decision_json=?,response_kind='decision',response_id=?,"
                        "decided_at=?,updated_at=? WHERE request_id=? AND state='open' AND response_kind IS NULL",
                        ("approved" if decision == "approve" else "declined", canonical_json(payload), request_id,
                         now, now, item["request_id"]),
                    )
                    if changed.rowcount != 1:
                        raise BoardError("CONFLICT", "This request was already answered; get again")
                continuation_id = None
                if params.get("autoContinue", True):
                    continuation_id = str(uuid.uuid4())
                    input_text = params.get("note") or "The Host answered the boundary; continue with the recorded decision and helper outcomes"
                    connection.execute(
                        "INSERT INTO workflow_continuations(checkout,workspace_facts_role,continuation_id,run_id,kind,request_id,"
                        "input_text,note,helper_policy,helper_outcomes_json,state,created_at)"
                        "VALUES(?,?,?,?, 'helper',?,?,?,?,'[]','recorded',?)",
                        (source["checkout"],"none" if source["checkout"]=="none" else "pending",continuation_id,source_id,leaf["request_id"],input_text,params.get("note"),
                         "keep" if self._unfinished_helpers(connection, source_id) else None,now),
                    )
                self._sync_boundary(connection, source_id, now, default_state="waiting-helpers" if children else "awaiting-host")
                source = self._run_row(connection, source_id)
                if continuation_id and not children and not self._unfinished_helpers(connection, source_id):
                    self._requeue(connection, task, source, actor=actor, now=now, continuation_id=continuation_id)
                for intermediate, _ in reversed(chain[:-1]):
                    self._sync_boundary(connection, intermediate["run_id"], now, default_state="waiting-helpers")
                self.board._append_event(connection,"workflow.request_" + ("approved" if decision == "approve" else "declined"),
                    task_id=source_id,payload={"requestId":request_id,"decision":decision,"actor":actor,
                                              "helpers":[item["taskId"] for item in children],"autoContinue":params.get("autoContinue",True)})
                self._close_proxy_ancestors(connection,self._request_row(connection,source_id,leaf["request_id"]),now)
                result = self._action_view(connection,self._run_row(connection,run_id),requestId=request_id,decision=decision,alreadyResponded=False,
                                           **({"targetRunId":source_id} if source_id!=run_id else {}))
                head = self.board._head_of(connection)
            self.board._notify(head)
            return result

    @staticmethod
    def _require_open_request(connection, run, request):
        if request["state"] != "open" or request["response_kind"] is not None or run["state"] in ("cancelled","accepted","concluded"):
            raise BoardError("CONFLICT", "This request was processed or replaced; get the micro task again", requestId=request["request_id"])


    def _decision_replay(self, connection, owner, request, payload):
        if request["response_kind"] is None:
            return None
        if request["response_kind"] != "decision" or json.loads(request["decision_json"]) != payload:
            raise BoardError("CONFLICT", "A different action already answered this request; get again", requestId=request["request_id"])
        return self._action_view(connection,owner,requestId=request["request_id"],decision=payload["decision"],alreadyResponded=True,
                                 **({"targetRunId":payload["targetRunId"]} if "targetRunId" in payload else {}))

    @contextmanager
    def _helper_preparations(self, helpers, *, transfer_from=None):
        validated = []
        for helper in helpers:
            self._require_permission(schemas.buddy_constraints(helper["spec"]) or None, helper["executionWorkspace"]["access"])
            validated.append((helper, self._validated_buddy(helper["spec"])))
        prepared = []
        with ExitStack() as locks, ExitStack() as preparations:
            shared_locks = (locks, set())
            for helper, buddy in validated:
                if helper["executionWorkspace"]["kind"] == "worktree":
                    self._cleanup_fence(helper["executionWorkspace"].get("cwd") or helper["spec"].get("cwd"))
                allocation = preparations.enter_context(self._submission_preparation(
                    helper["requestId"], helper["executionWorkspace"], shared_locks=shared_locks,
                    transfer_from=transfer_from))
                prepared.append({**helper, "manifest": allocation["manifest"], "buddy": buddy})
            yield prepared



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
            'UPDATE workflow_runs SET state=?, active_request_id=?, updated_at=? WHERE run_id=?',
            (state, request_id, now, run_id),
        )
        connection.execute(
            "UPDATE tasks SET queue_reason=?, updated_at=? WHERE task_id=?",
            ("helper-attention" if boundary is not None and boundary["child_task_id"] else "awaiting-host" if boundary is not None else "awaiting-helpers", now, run_id),
        )
        self.board._append_event(
            connection, "workflow.boundary_updated", task_id=run_id, revision=None,
            payload={"activeRequestId": request_id, "state": state},
        )
        return boundary

    @staticmethod
    def _proxy_id(parent_id, source_request_id):
        return "req-proxy-" + sha256_text(canonical_json([parent_id, source_request_id]))

    def _propagate_boundary(self, connection, source, now):
        """Persist a correlated proxy at every owned ancestor in this transaction."""
        while True:
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
                "reason_code,reason_params_json,decision_id,payload_json,state,created_at,updated_at)"
                " VALUES(?,?,?,?,?,'helper-attention',?,?,?,?,?,'open',?,?)",
                (request_id, parent["run_id"], source["turn_id"], source["attempt_id"], source["run_id"], source["summary"],
                 source["reason_code"], source["reason_params_json"], source["decision_id"], canonical_json(payload),
                 now, now),
            )
            source = self._request_row(connection, parent["run_id"], request_id)
            if source["state"] != "open":
                return  # A superseded proxy is not reopened by a delayed callback.
            connection.execute("UPDATE workflow_children SET state='attention', updated_at=? WHERE child_task_id=?", (now, child["child_task_id"]))
            self._sync_boundary(connection, parent["run_id"], now)

    def _attention_chain(self, connection, run, request):
        chain = []
        while True:
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

    def _close_proxy_ancestors(self, connection, source, now):
        """A local Host/console answer also closes its exact ancestor proxies."""
        while source["state"] != "open":
            link = connection.execute("SELECT parent_run_id FROM workflow_children WHERE child_task_id=?", (source["run_id"],)).fetchone()
            if link is None:
                return
            proxy_id = self._proxy_id(link["parent_run_id"], source["request_id"])
            proxy = connection.execute("SELECT * FROM workflow_requests WHERE request_id=? AND state='open'", (proxy_id,)).fetchone()
            if proxy is None:
                return
            connection.execute(
                'UPDATE workflow_requests SET state=?, response_kind=?, response_id=?, decision_json=?, decided_at=?, updated_at=? WHERE request_id=?',
                (source["state"],source["response_kind"],source["response_id"],source["decision_json"],source["decided_at"],now,proxy_id),
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



    def _create_helpers(
        self, connection, run_row, request_row, prepared: list[dict], now: str,
        *, actor: dict,
    ) -> list[dict]:
        children: list[dict] = []
        for item in prepared:
            helper_task_id = str(uuid.uuid4())
            manifest = item["manifest"]
            access = "read" if item["executionWorkspace"]["access"] == "read" else "write"
            transfer_from = None
            if (manifest is not None and run_row["checkout"] != "none" and access == "write"
                    and manifest["checkoutRoot"] == self._run_manifest(connection, run_row)["checkoutRoot"]):
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
                run_row=run_row,
                manifest=manifest,
                now=now,
                actor=actor,
            )
            artifact_id = None
            if manifest is not None:
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
                    now=now,
                )
            connection.execute(
                "INSERT INTO workflow_children(child_task_id, parent_run_id, request_id, state, created_at, updated_at) VALUES(?,?,?,'active',?,?)",
                (helper_task_id,run_row["run_id"],request_row["request_id"],now,now),
            )
            if item["buddy"] is None:
                self._start_routing(connection, helper_task_id, now)
            else:
                connection.execute('UPDATE workflow_runs SET buddy_json=?, buddy_revision=1, validated_buddy_revision=1 WHERE run_id=?',
                                   (canonical_json(item["buddy"]), helper_task_id))
                self.board._append_event(connection, "workflow.buddy_selected", task_id=helper_task_id,
                                         payload={"source": "original-explicit", "buddy": item["buddy"],
                                                  "buddyRevision": 1})
            children.append(
                {
                    "taskId": helper_task_id,
                    "requestId": item["requestId"],
                    "workspaceId": manifest.get("workspaceId") if manifest else None,
                    "runId": helper_task_id,
                    "artifact": artifact_id,
                }
            )
        return children

    def _admit_helper_task(
        self, connection, *, helper_task_id, item, run_row, manifest, now, actor
    ) -> None:
        spec = item["spec"]
        projects.require_capture_identity(item['projectFacts'], manifest)
        fingerprint = schemas.spec_fingerprint(spec)
        effective_cwd = manifest["path"] if manifest else item["executionWorkspace"]["cwd"]
        blocker = self.board._admission_blocker(connection, {**spec, "cwd": effective_cwd})
        request_id = item["requestId"]
        existing = connection.execute("SELECT * FROM tasks WHERE request_id=?", (request_id,)).fetchone()
        if existing is not None:
            raise BoardError(
                "CONFLICT",
                "A helper requestId already belongs to another task",
                requestId=request_id,
                existingTaskId=existing["task_id"],
            )
        connection.execute(
            'INSERT INTO tasks(task_id, request_id, spec_json, input_fingerprint, fingerprint_version, adapter, cwd, state, queue_reason, revision, created_at, updated_at) VALUES(?,?,?,?,?,?,?,?,?,1,?,?)',
            (helper_task_id,request_id,canonical_json(spec),fingerprint,schemas.FINGERPRINT_VERSION_CURRENT,spec.get("harness") or "unresolved",effective_cwd,"queued",blocker or "awaiting-execution",now,now,),
        )
        # Helpers freeze their own project and execution choice at admission.
        identity = item["projectFacts"]
        projects.record(connection, identity)
        connection.execute(
            "INSERT INTO workflow_runs(run_id, execution_workspace_json, workspace_id, workspace_manifest_sha256, macro_task_id, standalone_project_id, project_kind,checkout,state, created_at, updated_at) VALUES(?,?,?,?,?,?,?,?,'executing',?,?)",
            (helper_task_id,canonical_json(item["executionWorkspace"]),manifest["workspaceId"] if manifest else None,manifest["manifestSha256"] if manifest else None,run_row["macro_task_id"],identity["projectId"] if run_row["macro_task_id"] is None else None,identity["projectKind"],item["executionWorkspace"]["kind"],now,now,),
        )
        connection.execute("UPDATE workflow_runs SET title=? WHERE run_id=?", (item["title"], helper_task_id))
        # A helper is a current-contract run too: its own scope version 1 is recorded
        # with its admission so no creation path can exist without a real scope row.
        connection.execute(
            "INSERT INTO workflow_scope_versions(run_id, scope_version, actor_role, actor_id, note, write_scope_json,"
            " manifest_sha256, stopped_evidence_json, created_at) VALUES(?,1,?,?,?,?,?,?,?)",
            (helper_task_id, actor["role"], actor.get("id"), None,
             canonical_json(list(manifest.get("writeScope") or []) if manifest else []), manifest.get("manifestSha256") if manifest else None,
             canonical_json({"kind": "helper", "parentRunId": run_row["run_id"]}), now),
        )
        self.board._append_event(
            connection,
            "workflow.helper_admitted",
            task_id=helper_task_id,
            revision=1,
            payload={"parentRunId": run_row["run_id"], "requestId": request_id, "integrator": item["integrator"]},
        )

    # -- continue ------------------------------------------------------------
    def _target_run(self, connection, owner, target_id: str, *, include_lead: bool = False):
        """Resolve a target within the controlling run's current helper graph."""
        if target_id != owner["run_id"] and target_id not in {
                child["child_task_id"] for child in self._owned_children(connection, owner["run_id"], include_lead=include_lead)}:
            raise BoardError("UNAUTHORIZED", "targetRunId must be an authorized descendant of this owning Micro task")
        return self._run_row(connection, target_id)

    @staticmethod
    def _require_enabled_buddy(connection, buddy):
        """Require the user's enabled choice after the catalog admits the tuple."""
        row = connection.execute(
            "SELECT enabled FROM buddies WHERE harness=? AND provider=? AND model=? AND effort=?",
            tuple(buddy[key] for key in schemas.BUDDY_FIELDS),
        ).fetchone()
        from ..catalog.catalog import catalog_read_at

        if row is not None and row["enabled"]:
            return
        read_at = catalog_read_at(connection, buddy["harness"])
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

    def continue_run(self, params: dict, *, console_authority=None):
        from ...protocol.requests import parse_request, dump_request
        params = dump_request(parse_request("continue", params, audience="service"))
        if "decision" in params:
            return self._decide(params, console_authority=console_authority)
        if "resolution" in params:
            return self._resolve(params, console_authority=console_authority)
        return self._continue_input(params, console_authority=console_authority)


    def _origin(self, connection, owner, target, params):
        if "requestId" in params:
            row = self._request_row(connection, owner["run_id"], params["requestId"])
            return "workflow_requests", row, "request_id"
        if "artifactId" in params:
            row = connection.execute("SELECT * FROM workflow_artifacts WHERE artifact_id=? AND run_id=?",
                                     (params["artifactId"],target["run_id"])).fetchone()
            table, key = "workflow_artifacts", "artifact_id"
        else:
            row = connection.execute("SELECT * FROM workflow_turns WHERE turn_id=? AND run_id=?",
                                     (params["turnId"],target["run_id"])).fetchone()
            table, key = "workflow_turns", "turn_id"
        if row is None:
            raise BoardError("NOT_FOUND", "The response identity does not belong to this micro task")
        return table, row, key


    def _continue_replay(self, connection, owner, origin, params):
        _, row, _ = origin
        if row["response_kind"] is None:
            return None
        if row["response_kind"] != "continue":
            raise BoardError("CONFLICT", "A different action already answered this fact; get again")
        continuation = connection.execute("SELECT * FROM workflow_continuations WHERE continuation_id=?", (row["response_id"],)).fetchone()
        requested = {"runId": params["runId"], "targetRunId": params.get("targetRunId",params["runId"]),
                     **{k:v for k,v in params.items() if k not in ("runId","targetRunId")}}
        recorded = {"runId": continuation["controlling_run_id"], "targetRunId":continuation["run_id"],
                    "input":continuation["input_text"]}
        for public,column in (("requestId","request_id"),("artifactId","artifact_id"),("turnId","response_turn_id"),
                              ("note","note"),("helperPolicy","helper_policy")):
            if continuation[column] is not None:
                recorded[public]=continuation[column]
        if continuation["buddy_selection_json"] is not None:
            recorded["buddy"]=json.loads(continuation["buddy_selection_json"])
        for column in ("access_selection_json","resolution_json"):
            if continuation[column] is not None:
                recorded.update(json.loads(continuation[column]))
        if requested != recorded:
            raise BoardError("CONFLICT", "The recorded continuation has different action parameters; get again")
        resolution = json.loads(row["decision_json"])["result"] if "resolution" in params else None
        return self._action_view(connection,owner,continuationId=continuation["continuation_id"],alreadyResponded=True,
                                 **{k:params[k] for k in ("requestId","artifactId","turnId","targetRunId") if k in params},
                                 **({"resolutionState":resolution["state"],"resolvedFingerprint":resolution["resolvedFingerprint"]} if resolution else {}))


    def _check_origin(self, connection, owner, target, origin):
        table,row,_ = origin
        if row["response_kind"] is not None:
            raise BoardError("CONFLICT", "This fact was already answered; get again")
        if table == "workflow_requests":
            self._require_open_request(connection,owner,row)
            source, _ = self._attention_chain(connection, owner, row)[-1]
            if source["run_id"] != target["run_id"]:
                raise BoardError("CONFLICT", "targetRunId must name the source of this boundary request")
        elif table == "workflow_artifacts":
            if row["kind"] not in ("output","resolved-output","partial-output","result"):
                raise BoardError("CONFLICT", "A continuation must answer a fixed result of the current turn")
            if target["state"] == "delivered":
                current = row["artifact_id"] == target["final_artifact_id"]
            else:
                current = (row["turn_id"] == target["current_turn_id"] and
                           (target["state"] in ("failed","cancelled","awaiting-host","waiting-helpers","waiting-owned")
                            or self._queued_lead_wakeup(connection,target)))
            if not current:
                raise BoardError("CONFLICT", "This result was replaced by a newer turn; get again")
        else:
            failed_boundary = (target["state"] in ("failed","cancelled") or
                               (target["state"]=="awaiting-host" and row["state"] in ("failed","interrupted")))
            if row["turn_id"] != target["current_turn_id"] or not failed_boundary:
                raise BoardError("CONFLICT", "turnId must identify the failed current turn; get again")
            fixed = connection.execute("SELECT 1 FROM workflow_artifacts WHERE run_id=? AND turn_id=? AND kind IN ('output','partial-output','resolved-output','result')",
                                       (target["run_id"],row["turn_id"])).fetchone()
            if fixed is not None:
                raise BoardError("CONFLICT", "This turn has a fixed result; answer its artifactId")


    def _continue_input(self, params, *, console_authority=None):
        owner_id=params["runId"]
        target_id=params.get("targetRunId",owner_id)
        with self.db.read() as connection:
            owner=self._run_row(connection,owner_id)
            self._authorize(connection,owner,params,console_authority=console_authority,action="A continuation")
            target=self._target_run(connection,owner,target_id)
            origin=self._origin(connection,owner,target,params)
            replay=self._continue_replay(connection,owner,origin,params)
            if replay is not None:
                return replay
            self._check_origin(connection,owner,target,origin)
            snapshot=target
            current_choice=json.loads(target["execution_workspace_json"])
            original=self._run_manifest(connection,target)
            provenance=(original,*self._allocation_provenance(connection,target))
            physical=next((v for v in provenance if v["kind"]=="worktree" and v["checkoutRoot"]==original["checkoutRoot"]
                           and project_id(v)==project_id(original)),original)
            if target["delegation_authority"] and (params.get("buddy")=="route" or params.get("access",current_choice["access"])!="full"):
                raise BoardError("INVALID_ARGUMENT","A Lead requires full access and a complete explicit buddy")
        if "buddy" in params and not params.get("note"):
            raise BoardError("INVALID_ARGUMENT","An explicit buddy change requires note",field="note")
        buddy=self._named_buddy(params["buddy"]) if "buddy" in params else None
        reroute=params.get("buddy")=="route"
        if buddy is not None:
            buddy=self._validated_buddy(buddy)
        self._require_permission(buddy if "buddy" in params else self._buddy(snapshot),params.get("access",current_choice["access"]))
        if snapshot['checkout'] != 'none':
            with self._cleanup_allocation_lock(self.board.directory,physical if physical["kind"]=="worktree" else None):
                if physical["kind"]=="existing":
                    workspace_module().inspect(physical["path"],checkout=physical)
                repository=workspace_module().locate_repository(physical,candidates=lambda:repository_candidates(self.db,physical))
                if repository is None:
                    raise BoardError("WORKSPACE_REPOSITORY_NOT_FOUND","The workspace repository is unavailable",projectId=project_id(physical))
        now=self.now()
        with self.db.write() as connection:
            owner=self._run_row(connection,owner_id)
            actor,initiator=self._authorize(connection,owner,params,console_authority=console_authority,action="A continuation")
            target=self._target_run(connection,owner,target_id)
            origin=self._origin(connection,owner,target,params)
            replay=self._continue_replay(connection,owner,origin,params)
            if replay is not None:
                return replay
            self._expect_current(target,snapshot)
            self._check_origin(connection,owner,target,origin)
            response=self._record_continuation(connection,owner,target,origin,params,buddy,reroute,actor,initiator,now)
            head=self.board._head_of(connection)
        self.board._notify(head)
        return response


    def _validate_continuation(self, connection, target, params, buddy, reroute):
        run_id=target["run_id"]
        self._assert_lineage_open(connection,run_id)
        reject_deleting_run(connection,run_id)
        self._require_routing_stopped(connection,run_id)
        if target["state"] in ("accepted","concluded"):
            raise BoardError("CONFLICT","A finished micro task cannot continue")
        if connection.execute("SELECT 1 FROM workspace_cleanup_plans WHERE run_id=? AND state IN ('applying','applied')",(run_id,)).fetchone():
            raise BoardError("CONFLICT","The checkout is being cleaned or was removed")
        if "resolution" not in params and self._open_conflict(connection,run_id) is not None:
            raise BoardError("WORKSPACE_SCOPE_CONFLICT","Resolve the recorded workspace site before continuing")
        live=self._unfinished_helpers(connection,run_id)
        policy=params.get("helperPolicy")
        if live != (policy is not None):
            raise BoardError("INVALID_ARGUMENT","helperPolicy is required only while authorized helpers are live",field="helperPolicy")
        if self._buddy(target) is None and buddy is None and not reroute:
            raise BoardError("BUDDY_REQUIRED","Supply buddy=route or a complete buddy name for this routing boundary")
        selection={k:params[k] for k in ("access","accessNote","writeScope") if k in params}
        choice=json.loads(target["execution_workspace_json"])
        access=params.get("access",choice["access"])
        if access=="read" and "writeScope" in params:
            raise BoardError("INVALID_ARGUMENT","writeScope is inapplicable with read access",field="writeScope")
        if target["checkout"]=="none" and "writeScope" in params:
            raise BoardError("INVALID_ARGUMENT","writeScope is inapplicable with checkout none",field="writeScope")
        if target["checkout"]!="none" and access!="read" and not params.get("writeScope",self._current_scope(connection,target)["writeScope"]):
            raise BoardError("INVALID_ARGUMENT","A write continuation needs writeScope",field="writeScope")
        return selection

    def _answer_request(self, connection, owner, request, *, kind, response_id, state, now, decision=None):
        chain = self._attention_chain(connection, owner, request)
        for _, item in chain:
            changed = connection.execute(
                "UPDATE workflow_requests SET state=?,response_kind=?,response_id=?,decision_json=?,decided_at=?,updated_at=? "
                "WHERE request_id=? AND state='open' AND response_kind IS NULL",
                (state,kind,response_id,canonical_json(decision) if decision is not None else None,now,now,item["request_id"]),
            )
            if changed.rowcount != 1:
                raise BoardError("CONFLICT","This request was already answered; get again")
        source = self._request_row(connection, chain[-1][0]["run_id"], chain[-1][1]["request_id"])
        self._close_proxy_ancestors(connection, source, now)
        for ancestor, _ in reversed(chain[:-1]):
            self._sync_boundary(connection,ancestor["run_id"],now,default_state="waiting-helpers")

    def _record_continuation(self,connection,owner,target,origin,params,buddy,reroute,actor,initiator,now):
        selection=self._validate_continuation(connection,target,params,buddy,reroute)
        run_id=target["run_id"]
        policy=params.get("helperPolicy")
        identifier=str(uuid.uuid4())
        if policy=="cancel":
            self._cancel_children(connection,run_id,params.get("note"),now,actor=actor,initiator=initiator,
                                  include_lead=False,stop_facts={"continuationId":identifier})
        if selection:
            evidence=self._lineage_stop_evidence(connection,target,"A new turn authorization")
            choice=json.loads(target["execution_workspace_json"])
            choice.update({k:v for k,v in selection.items() if k!="writeScope"})
            if choice["access"]!="full":
                choice.pop("accessNote",None)
            old_scope=self._current_scope(connection,target)
            paths=[] if choice["access"]=="read" else params.get("writeScope",old_scope["writeScope"])
            if target["checkout"]!="none" and choice["access"]!="read" and not paths:
                raise BoardError("INVALID_ARGUMENT","A write continuation needs writeScope",field="writeScope")
            connection.execute("INSERT INTO workflow_scope_versions(run_id,scope_version,actor_role,actor_id,note,write_scope_json,manifest_sha256,stopped_evidence_json,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
                               (run_id,old_scope["scopeVersion"]+1,actor["role"],actor.get("id"),params.get("note"),canonical_json(paths),
                                old_scope["inputManifestSha256"],canonical_json(evidence),now))
            connection.execute("UPDATE workflow_runs SET execution_workspace_json=? WHERE run_id=?",(canonical_json(choice),run_id))
        connection.execute("UPDATE workflow_continuations SET state='invalidated' WHERE run_id=? AND state IN ('recorded','queued')",(run_id,))
        table,row,key=origin
        if table=="workflow_requests":
            self._answer_request(connection,owner,row,kind="continue",response_id=identifier,state="superseded",now=now)
        else:
            changed=connection.execute(f"UPDATE {table} SET response_kind='continue',response_id=? WHERE {key}=? AND response_kind IS NULL",(identifier,row[key]))
            if changed.rowcount!=1:
                raise BoardError("CONFLICT","The response identity was already consumed; get again")
        # A fixed result and its boundary request describe the same stopped turn.
        # Continuing either one supersedes every request on that run, including
        # the correlated proxies held by its ancestors.
        open_requests = connection.execute(
            "SELECT request_id FROM workflow_requests WHERE run_id=? AND state='open'", (run_id,)).fetchall()
        connection.execute(
            "UPDATE workflow_requests SET state='superseded',response_kind='continue',response_id=?,"
            "decided_at=?,updated_at=? WHERE run_id=? AND state='open'",
            (identifier,now,now,run_id))
        for request in open_requests:
            self._close_proxy_ancestors(connection,self._request_row(connection,run_id,request["request_id"]),now)
        connection.execute(
            "INSERT INTO workflow_continuations(continuation_id,run_id,controlling_run_id,kind,request_id,artifact_id,response_turn_id,"
            "input_text,note,helper_policy,helper_outcomes_json,buddy_selection_json,access_selection_json,resolution_json,"
            "checkout,workspace_facts_role,state,created_at) VALUES(?,?,?,'manual',?,?,?,?,?,?,?,?,?,?,?,?,'queued',?)",
            (identifier,run_id,owner["run_id"],params.get("requestId"),params.get("artifactId"),params.get("turnId"),params["input"],params.get("note"),policy,
             canonical_json(self._helper_outcomes(connection,run_id)),canonical_json(params["buddy"]) if "buddy" in params else None,
             canonical_json(selection) if selection else None,
             canonical_json({k:params[k] for k in ("resolution","observedFingerprint","paths") if k in params}) if "resolution" in params else None,target["checkout"],"none" if target["checkout"]=="none" else "pending",now))
        task=connection.execute("SELECT * FROM tasks WHERE task_id=?",(run_id,)).fetchone()
        self._requeue(connection,task,target,actor=actor,now=now,continuation_id=identifier)
        if target["state"]=="delivered":
            connection.execute("UPDATE tasks SET review_rejected_at=?,review_rejection_note=? WHERE task_id=?",(now,params.get("note"),run_id))
            self.board._append_event(connection,"workflow.review_rejected",task_id=run_id,payload={"artifactId":target["final_artifact_id"],"actor":actor,"note":params.get("note")})
        if buddy is not None:
            connection.execute("UPDATE workflow_runs SET buddy_json=?,current_routing_id=NULL,buddy_revision=buddy_revision+1,validated_buddy_revision=buddy_revision+1 WHERE run_id=?",(canonical_json(buddy),run_id))
            connection.execute("UPDATE tasks SET adapter=? WHERE task_id=?",(buddy["harness"],run_id))
            self.board._append_event(connection,"workflow.buddy_overridden",task_id=run_id,payload={"actor":actor,"note":params.get("note"),"buddy":buddy,"buddyRevision":target["buddy_revision"]+1})
        elif reroute:
            connection.execute("UPDATE workflow_runs SET buddy_json=NULL,current_routing_id=NULL,validated_buddy_revision=NULL WHERE run_id=?",(run_id,))
            connection.execute("UPDATE tasks SET adapter='unresolved',queue_reason='awaiting-workspace-preparation' WHERE task_id=?",(run_id,))
        self.board._append_event(connection,"workflow.continued",task_id=run_id,payload={"continuationId":identifier,"actor":actor,"controllingRunId":owner["run_id"]})
        return self._action_view(connection,self._run_row(connection,owner["run_id"]),continuationId=identifier,alreadyResponded=False,
                                 **{k:params[k] for k in ("requestId","artifactId","turnId","targetRunId") if k in params})

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
                "SELECT t.*,a.result_json FROM workflow_turns t JOIN attempts a ON a.attempt_id=t.attempt_id"
                " WHERE t.run_id=? AND t.attempt_id=? ORDER BY t.turn_index DESC LIMIT 1",
                (row["child_task_id"], attempt_id),
            ).fetchone()
            outcome = (self.turn_outcome(json.loads(turn["result_json"]))["outcome"]
                       if turn is not None and turn["state"] == "returned" else None)
            artifact = connection.execute(
                "SELECT * FROM workflow_artifacts WHERE run_id=? AND attempt_id=? AND kind IN ('output','resolved-output','partial-output','result')"
                " ORDER BY rowid DESC LIMIT 1",
                (row["child_task_id"], attempt_id),
            ).fetchone()
            child_run = self._run_optional(connection, row["child_task_id"])
            entry = {
                "taskId": row["child_task_id"],
                "state": row["state"],
                "taskState": row["task_state"],
                "disposition": outcome["disposition"] if outcome is not None else None,
                "summary": outcome.get("summary") if outcome else None,
                "remaining": (outcome or {}).get("remaining", []),
                "attemptId": attempt_id,
                "generation": turn["generation"] if turn is not None else None,
                "workspaceManifestSha256": None,
            }
            manifest = turn_manifest(self.board.directory, json.loads(turn["input_json"])) if turn is not None else None
            if manifest is None and child_run is not None:
                manifest = self._run_manifest(connection, child_run)
            if manifest:
                entry["workspaceManifestSha256"] = manifest.get("manifestSha256")
                entry["workspace"] = manifest if manifest["kind"] == "none" else {
                    "workspaceId": manifest["workspaceId"],
                    "path": manifest["path"],
                    "checkoutRoot": manifest["checkoutRoot"],
                    "projectId": project_id(manifest),
                    "manifestSha256": manifest["manifestSha256"],
                }
            if artifact is not None and artifact["kind"] == "result":
                entry["artifact"] = self._artifact_view(artifact, connection=connection)
            elif artifact is not None:
                seal = self._artifact_document(artifact)
                changed = seal.get("changedPaths")
                entry["artifact"] = {
                    "artifactId": artifact["artifact_id"],
                    "attemptId": artifact["attempt_id"],
                    "turnId": artifact["turn_id"],
                    "inputCommit": seal.get("inputCommit"),
                    **self._output_delivery(artifact, seal, connection),
                    "snapshotSha256": artifact["output_snapshot_sha256"],
                    "diffPath": seal.get("diffPath"),
                    "diffSha256": seal.get("diffSha256"),
                    **(self._bounded_path_fields({"changedPaths": changed}, ("changedPaths",))
                       if isinstance(changed, list) else {"changedPaths": []}),
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
        connection.execute(
            "UPDATE workflow_runs SET state='executing', active_request_id=NULL, final_artifact_id=NULL, final_attempt_id=NULL, continuation_count=continuation_count+1, updated_at=? WHERE run_id=?",
            (now,run_row["run_id"],),
        )
        if continuation_id is not None:
            connection.execute(
                "UPDATE workflow_continuations SET state='queued' WHERE continuation_id=?", (continuation_id,)
            )

    # -- cancel --------------------------------------------------------------
    def cancel(self, params: dict, *, console_authority=None):
        from ...protocol.requests import parse_request, dump_request
        from .cancellation import for_run
        params = dump_request(parse_request("cancel",params,audience="service"))
        owner_id=params["runId"]
        run_id=params.get("targetRunId",owner_id)
        with self.db.write() as connection:
            owner=self._run_row(connection,owner_id)
            run=self._target_run(connection,owner,run_id)
            actor,initiator=self._authorize(connection,owner,params,console_authority=console_authority,action="Cancellation")
            existing=for_run(connection,run_id)
            if existing is not None:
                escalate = False
                if console_authority is not None:
                    ids = [run_id, *(row["child_task_id"] for row in self._owned_children(connection,run_id,include_lead=True)),
                           *(row["task_id"] for row in self._routing_tasks(connection,run_id,descendants=True,include_lead=True))]
                    escalate = any(row["stop_initiator"] in WRAP_UP_INITIATORS for row in connection.execute(
                        "SELECT stop_initiator FROM attempts WHERE task_id IN (SELECT value FROM json_each(?))"
                        " AND cancel_requested_at IS NOT NULL AND (execution_state!='finished' OR shutdown_confirmed!=1)",
                        (canonical_json(ids),)))
                if not escalate:
                    return self._action_view(connection,owner,targetRunId=run_id,stopEventId=existing["eventId"],alreadyRequested=True)
            if run["state"] in ("accepted","concluded"):
                raise BoardError("CONFLICT","This micro task is already closed")
            task=connection.execute("SELECT * FROM tasks WHERE task_id=?",(run_id,)).fetchone()
            self._cancel_in_transaction(connection,run,task,params.get("note"),actor,initiator,self.now())
            recorded=for_run(connection,run_id)
            response=self._action_view(connection,self._run_row(connection,owner_id),targetRunId=run_id,stopEventId=recorded["eventId"],alreadyRequested=existing is not None)
            head=self.board._head_of(connection)
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
            queued = {task["runId"] for task in autonomous_tasks(connection) if task["state"] == "queued"}
            unresolved = self.board.UNRESOLVED_SQL.replace("execution_state", "a.execution_state").replace(
                "result_json", "a.result_json").replace("shutdown_confirmed", "a.shutdown_confirmed")
            candidates: dict[str, tuple[str, bool]] = {}
            for row in connection.execute(
                "SELECT t.task_id,"
                f" EXISTS(SELECT 1 FROM attempts a WHERE a.task_id=t.task_id AND ({unresolved})) AS unresolved"
                " FROM tasks t JOIN workflow_runs r ON r.run_id=t.task_id"
                " WHERE t.state='queued' OR EXISTS(SELECT 1 FROM attempts a"
                f" WHERE a.task_id=t.task_id AND ({unresolved}))"
            ).fetchall():
                if row["unresolved"] or row["task_id"] in queued:
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
            connection, "workflow.cancelled", task_id=run_id, revision=None,
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
            connection.execute("UPDATE workflow_routes SET state='fenced' WHERE decision_id=?",
                               (link["decision_id"],))
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
            if attempt["cancel_requested_at"] is not None and (
                    initiator in WRAP_UP_INITIATORS or attempt["stop_initiator"] not in WRAP_UP_INITIATORS):
                continue
            deadline = attempt["grace_deadline"]
            if (wrap_up and initiator in WRAP_UP_INITIATORS
                    and self._execution_adapter(attempt["adapter"]).interrupted_resume
                    and attempt["execution_state"] in ("starting", "executing")):
                deadline = (datetime.fromisoformat(now) + timedelta(seconds=WRAP_UP_SECONDS)).isoformat(
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
        self._revoke_credentials(connection, run_id)
        self._cancel_routes(connection, run_id, make_reason("micro-task-cancelled"), now, actor=actor,
                            initiator=initiator)
        if cancel_micro_task:
            connection.execute(
                "UPDATE workflow_runs SET state='cancelled', active_request_id=NULL, updated_at=? WHERE run_id=?",
                (now, run_id),
            )
            connection.execute(
                "UPDATE workflow_children SET state='cancelled', updated_at=?"
                " WHERE child_task_id=? AND state IN ('active','attention')", (now, run_id),
            )
        elif run_row["active_request_id"]:
            connection.execute(
                'UPDATE workflow_runs SET active_request_id=NULL, updated_at=? WHERE run_id=?', (now, run_id),
            )
        stopped = self._stop_proven(connection, task)
        if task["state"] not in ("completed", "concluded", "failed", "cancelled"):
            self.board._transition_task(connection, task, "cancelled" if stopped else "cancelling")
            connection.execute("UPDATE tasks SET queue_reason=NULL, updated_at=? WHERE task_id=?", (now, run_id))
        if not stopped:
            self._request_attempt_stop(connection, run_id, now, initiator=initiator)
        return cancel_micro_task

    def _late_cancelled_turn(self, connection, run_row, turn_row, attempt, payload, now, output_record):
        """Keep real receipt/artifact evidence without reopening a fenced micro task."""
        turn = self.turn_outcome(payload)
        disposition = None
        if (attempt["shutdown_confirmed"] and payload is not None
                and payload.get("terminationReason") != "grace-expired"
                and not self.result_payload(payload).get("microTaskError")
                and turn is not None and self._validate_turn(turn, turn_row, attempt) is None):
            disposition = turn["outcome"]["disposition"]
            connection.execute(
                "UPDATE workflow_turns SET state='returned', updated_at=? WHERE turn_id=?",
                (now, turn_row["turn_id"]),
            )
            self._pin_result_artifacts(connection, run_row, attempt, payload, turn_row["turn_id"], now, output_record)
        else:
            connection.execute("UPDATE workflow_turns SET state='failed', updated_at=? WHERE turn_id=?", (now, turn_row["turn_id"]))
        self._revoke_credentials(connection, run_row["run_id"])
        self.board._append_event(
            connection, "workflow.late_turn", task_id=run_row["run_id"], attempt_id=attempt["attempt_id"],
            revision=None, payload={"turnId": turn_row["turn_id"], "state": run_row["state"], "disposition": disposition},
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
        self._revoke_credentials(connection, task["task_id"])

    def _revoke_credentials(self, connection, run_id: str) -> None:
        connection.execute(
            "UPDATE agent_credentials SET state='revoked'"
            " WHERE run_id=? AND state='active'",
            (run_id,),
        )

    # -- acknowledge ---------------------------------------------------------
    def accept(self,params,*,console_authority=None):
        from ...protocol.requests import parse_request,dump_request
        params=dump_request(parse_request("accept",params,audience="service"))
        owner_id=params["runId"]
        target_id=params.get("targetRunId",owner_id)
        artifact_id=params["artifactId"]
        verification_inputs=physical=None
        with self.db.read() as connection:
            owner=self._run_row(connection,owner_id)
            self._authorize(connection,owner,params,console_authority=console_authority,action="An acceptance")
            snapshot=self._target_run(connection,owner,target_id)
            if snapshot['checkout'] == 'none' and 'notRequired' not in params:
                raise BoardError('INVALID_ARGUMENT', 'checkout none can only be accepted with notRequired', field='notRequired')
            replay=self._accept_replay(connection,owner,snapshot,params)
            if replay is not None:
                return replay
            if "notRequired" not in params:
                verification_inputs=self._accept_verification_inputs(connection,target_id,artifact_id)
                source=verification_inputs[0]
                physical=next((m for m in (source,*self._allocation_provenance(connection,snapshot))
                               if m["kind"]=="worktree" and m["checkoutRoot"]==source["checkoutRoot"] and project_id(m)==project_id(source)),source)
        with ExitStack() as ownership:
            verification={}
            if verification_inputs is not None:
                with self._cleanup_allocation_lock(self.board.directory,physical if physical["kind"]=="worktree" else None):
                    source_repository=workspace_module().locate_repository(physical,cwd=params["target"]["path"],
                                                                            candidates=lambda:repository_candidates(self.db,physical))
                    if source_repository is None:
                        raise BoardError("WORKSPACE_REPOSITORY_NOT_FOUND","The source repository is unavailable",projectId=project_id(physical))
                ownership.enter_context(workspace_module().integration_target_lock(self.board.directory,params["target"]["path"]))
                original,final,artifact=verification_inputs
                verification=self._accept_verify(artifact,original_input=original,final_input=final,target=params["target"],
                                                 host_paths=params.get("hostPaths",[]),before_commit=params.get("beforeCommit"),
                                                 adjusted=params.get("adjusted",False),note=params["note"],source_repository=source_repository)
            now=self.now()
            with self.db.write() as connection:
                owner=self._run_row(connection,owner_id)
                actor,_=self._authorize(connection,owner,params,console_authority=console_authority,action="An acceptance")
                target=self._target_run(connection,owner,target_id)
                replay=self._accept_replay(connection,owner,target,params)
                if replay is not None:
                    return replay
                self._expect_current(target,snapshot)
                task=connection.execute("SELECT * FROM tasks WHERE task_id=?",(target_id,)).fetchone()
                result,_,_=self._accept_record(connection,owner_run_id=owner_id,run_row=target,task=task,actor=actor,now=now,
                                               artifact_id=artifact_id,note=params["note"],request_key={**params,"hostPaths":params.get("hostPaths",[])},
                                               verification=verification,not_required=params.get("notRequired"))
                head=self.board._head_of(connection)
        self.board._notify(head)
        reclaimed=self._reclaim_checkout(run_id=target_id)
        with self.db.read() as connection:
            return self._action_view(connection,self._run_row(connection,owner_id),artifactId=artifact_id,
                                     integrationId=result["integrationId"],alreadyResponded=False,reclaim=reclaimed,
                                     **({"targetRunId":target_id} if target_id!=owner_id else {}))


    def _accept_replay(self,connection,owner,target,params):
        artifact=connection.execute("SELECT * FROM workflow_artifacts WHERE artifact_id=? AND run_id=?",(params["artifactId"],target["run_id"])).fetchone()
        if artifact is None:
            raise BoardError("NOT_FOUND","This artifact does not belong to the addressed micro task")
        if artifact["response_kind"] is None:
            return None
        if artifact["response_kind"]!="accept":
            raise BoardError("CONFLICT","A different action already answered this artifact; get again")
        integration=connection.execute("SELECT * FROM workflow_integrations WHERE integration_id=?",(artifact["response_id"],)).fetchone()
        task=connection.execute("SELECT acceptance_note FROM tasks WHERE task_id=?",(target["run_id"],)).fetchone()
        actual=json.loads(integration["verification_json"])["requested"]
        requested={"adjusted":params.get("adjusted",False),"beforeCommit":params.get("beforeCommit"),"hostPaths":sorted(params.get("hostPaths",[])),
                   **({"target":params["target"]} if "target" in params else {})}
        same=task["acceptance_note"]==params["note"] and actual==requested
        if "notRequired" in params:
            same=same and integration["strategy"]=="not-required" and integration["note"]==params["notRequired"]
        else:
            same=same and integration["strategy"]!="not-required"
        if not same:
            raise BoardError("CONFLICT","This artifact has a different recorded acceptance; get again")
        return self._action_view(connection,owner,artifactId=artifact["artifact_id"],integrationId=integration["integration_id"],alreadyResponded=True,
                                 **({"targetRunId":target["run_id"]} if target["run_id"]!=owner["run_id"] else {}))



    def _accept_verification_inputs(self, connection, run_id: str, artifact_id: str):
        """Pin the exact manifests one verified acceptance compares, before any Git work."""
        artifact = connection.execute(
            "SELECT * FROM workflow_artifacts WHERE artifact_id=? AND run_id=? AND kind IN ('output','resolved-output','partial-output')",
            (artifact_id, run_id),
        ).fetchone()
        if artifact is None:
            raise BoardError("NOT_FOUND", "No sealed output artifact of this run has that identity",
                             artifactId=artifact_id)
        manifest = self._artifact_document(artifact)
        original = connection.execute(
            "SELECT * FROM workflow_artifacts WHERE run_id=? AND kind='input' ORDER BY rowid LIMIT 1",
            (run_id,),
        ).fetchone()
        turn = connection.execute(
            "SELECT * FROM workflow_turns WHERE run_id=? AND turn_id=? AND attempt_id=?",
            (run_id, artifact["turn_id"], artifact["attempt_id"]),
        ).fetchone()
        if original is None or turn is None:
            raise BoardError("WORKSPACE_MANIFEST_CHANGED", "The final output has no proven input manifest")
        original_manifest = self._artifact_document(original)
        final_manifest = turn_manifest(self.board.directory, json.loads(turn["input_json"]))
        if not isinstance(final_manifest, dict):
            raise BoardError("WORKSPACE_MANIFEST_CHANGED", "The final turn has no execution workspace")
        if final_manifest.get("manifestSha256") != manifest.get("manifestSha256"):
            raise BoardError("WORKSPACE_MANIFEST_CHANGED", "A retained input manifest identity changed")
        return original_manifest, final_manifest, manifest

    def _accept_verify(self, manifest: dict, *, original_input: dict, final_input: dict,
                       target: dict, host_paths: list[str], before_commit: str | None,
                       adjusted: bool, note: str, source_repository: str | None = None) -> dict:
        """Verify the clean three-way result, or record explicit Host adjustments."""
        module = workspace_module()
        verification = module.integration_verify(
            manifest, original_input=original_input, final_input=final_input,
            path=target["path"], ref=target["ref"], before_commit=before_commit,
            repository=source_repository,
            host_paths=host_paths, note=note, _confirm_adjustments=adjusted,
        )
        unrecorded = sorted({item["path"] for item in verification.get("differingPaths") or []}
                            | set(verification.get("missingPaths") or [])
                            | set(verification.get("conflictingPaths") or []))
        comparison = self._bounded_path_fields(
            {key: value for key, value in verification.items() if key != "sourceCommit"}, INTEGRATION_PATH_FIELDS)
        if not verification.get("verified") and not unrecorded:
            # Content matched but the claimed commit interval does not hold (for
            # example a beforeCommit unrelated to the target ref): the comparison
            # proves nothing and must never be recorded as a verified integration.
            raise BoardError(
                "INTEGRATION_UNVERIFIED",
                "The target comparison does not hold; the artifact content matched but the named commit interval "
                "does not, so no integration was recorded",
                paths=[],
                verification=comparison,
            )
        if not unrecorded:
            verification["summary"] = ""
            return verification
        if not adjusted:
            raise BoardError(
                "INTEGRATION_UNVERIFIED",
                "The target differs from the clean three-way result or has conflicts; give the actual beforeCommit, "
                "or confirm real adjustments with adjusted:true and note",
                paths=unrecorded[:MAX_OUTPUT_PATH_VIEW], pathsCount=len(unrecorded),
                pathsTruncated=len(unrecorded) > MAX_OUTPUT_PATH_VIEW,
                verification=comparison,
            )
        if not verification.get("verified"):
            raise BoardError(
                "INTEGRATION_UNVERIFIED",
                "Even with the Host's confirmed adjustments the target comparison does not hold; no integration "
                "was recorded",
                paths=unrecorded[:MAX_OUTPUT_PATH_VIEW], pathsCount=len(unrecorded),
                pathsTruncated=len(unrecorded) > MAX_OUTPUT_PATH_VIEW,
                verification=comparison,
            )
        verification["summary"] = ""
        return verification

    def _accept_record(self, connection, *, owner_run_id: str, run_row, task, actor: dict, now: str,
                           artifact_id: str, note: str, request_key: dict,
                           verification: dict, not_required: str | None):
        """Record one acceptance, its integration binding and the events, in one transaction."""
        run_id = run_row["run_id"]
        # Acceptance is bound to the current delivered micro task, never to a Host
        # decision boundary, an active helper, or an older attempt's output.
        if run_row["state"] == "concluded":
            raise BoardError("CONFLICT", "This micro task already has a recorded conclusion; it is closed without acceptance", runId=run_id)
        host_completion = run_row["state"] == "awaiting-host"
        if run_row['checkout'] == 'none' and host_completion:
            raise BoardError('NOT_READY', 'A none acceptance requires a returned completed result')
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
                " AND kind IN ('output','resolved-output','result')",
                (run_row["final_artifact_id"], run_id),
            ).fetchone()
        if final_artifact is None:
            final_artifact = connection.execute(
                "SELECT * FROM workflow_artifacts WHERE run_id=? AND kind IN ('output','result') AND attempt_id=?"
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
            if (current is None or current["state"] != "returned"
                    or self.turn_outcome(json.loads(attempt["result_json"]))["outcome"]["disposition"]
                    not in ("assistance", "attention", "completed")):
                raise BoardError("NOT_READY", "Host completion requires the current stopped and sealed turn")
        if not (host_completion or host_proof) and connection.execute(
            "SELECT 1 FROM workflow_turns t JOIN attempts a ON a.attempt_id=t.attempt_id"
            " WHERE t.attempt_id=? AND t.state='returned'"
            " AND json_extract(a.result_json,'$.result.turn.outcome.disposition')='completed'",
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
                    verdict="accepted",
                )
            response = {**self.compact(connection, run_row, task), "verdict": "accepted", "duplicate": True, "targetRunId": run_id}
            if resolution is not None:
                response["resolutionDelivery"] = self._resolution_delivery_result(resolution)
            return response, artifact, None
        # The integration binding is content-addressed: repeating the same verified
        # comparison finds the existing record instead of minting a second one.
        manifest = self._artifact_document(artifact)
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
                'INSERT INTO workflow_integrations(integration_id, run_id, artifact_id, strategy, binding_sha256, target_path, target_ref, target_project_id, source_commit, source_tree, before_commit, after_commit, before_tree, after_tree, verification_json, note, actor_role, actor_id, created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                (integration_id,run_id,artifact_id,"not-required" if not_required is not None else verification["strategy"],binding_sha,"" if not_required is not None else verification["target"]["path"],"" if not_required is not None else verification["target"]["ref"],None if not_required is not None else verification["target"]["projectId"],verification.get("sourceCommit"),verification.get("sourceTree"),None if not_required is not None else verification.get("beforeCommit"),None if not_required is not None else verification.get("afterCommit"),None if not_required is not None else verification.get("beforeTree"),None if not_required is not None else verification.get("afterTree"),canonical_json({**({key: verification[key] for key in ("version", "mergeBaseCommit", "expectedTree", "matchingPaths", "differingPaths", "missingPaths", "conflictingPaths", "adjustments", "hostPaths", "note")} if not_required is None else {}),
                                    "requested": {"adjusted":request_key.get("adjusted",False),"beforeCommit":request_key.get("beforeCommit"),"hostPaths":sorted(request_key["hostPaths"]),
                                                  **({"target":request_key["target"]} if "target" in request_key else {})}}),not_required,actor["role"],actor.get("id"),now,),
            )
            self.board._append_event(
                connection,
                "workflow.integration_recorded",
                task_id=run_id,
                attempt_id=artifact["attempt_id"],
                revision=None,
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
            "UPDATE tasks SET accepted_at=?, acceptance_note=?,"
            " review_rejected_at=NULL, review_rejection_note=NULL, updated_at=?,"
            " revision=revision+1 WHERE task_id=?",
            (now, note, now, run_id),
        )
        if updated.rowcount != 1:
            raise BoardError("REVISION_CONFLICT", "The micro task changed concurrently; re-read it and retry")
        if host_completion:
            connection.execute("UPDATE tasks SET state='completed', queue_reason=NULL WHERE task_id=?", (run_id,))
            open_requests = connection.execute("SELECT request_id FROM workflow_requests WHERE run_id=? AND state='open'", (run_id,)).fetchall()
            connection.execute("UPDATE workflow_requests SET state='superseded', updated_at=? WHERE run_id=? AND state='open'", (now, run_id))
            for request in open_requests:
                self._close_proxy_ancestors(connection, self._request_row(connection, run_id, request["request_id"]), now)
            connection.execute("UPDATE workflow_runs SET active_request_id=NULL WHERE run_id=?", (run_id,))
            self.board._append_event(connection, "workflow.host_completed", task_id=run_id, attempt_id=attempt["attempt_id"],
                                     payload={"artifactId": artifact["artifact_id"], "integrationId": integration["integration_id"], "actor": actor})
        connection.execute(
            "UPDATE workflow_runs SET state='accepted', workspace_id=NULL, workspace_manifest_sha256=NULL, final_artifact_id=?, final_attempt_id=?, updated_at=? WHERE run_id=?",
            (artifact["artifact_id"],attempt["attempt_id"],now,run_id,),
        )
        bound=connection.execute("UPDATE workflow_artifacts SET response_kind='accept',response_id=? WHERE artifact_id=? AND response_kind IS NULL",
                             (integration["integration_id"],artifact_id))
        if bound.rowcount!=1:
            raise BoardError("CONFLICT","This artifact already has a recorded response; get again")
        accumulate_overall(connection, run_id)
        if not host_completion:
            # Logical ownership ends with the recorded acceptance.
            self._release_reservations(connection, run_id, now)
        else:
            updated_task = connection.execute("SELECT * FROM tasks WHERE task_id=?", (run_id,)).fetchone()
            self.child_settled(connection, task=updated_task, attempt=attempt, payload=None, task_state="completed", now=now)
        self.board._append_event(
            connection,
            "workflow.acknowledged",
            task_id=run_id,
            attempt_id=attempt["attempt_id"],
            revision=None,
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
        response = {**view, "verdict": "accepted", "duplicate": False, "targetRunId": run_id, "integrationId": integration["integration_id"]}
        response["integration"] = self._integration_view(connection, integration)
        if resolution is not None:
            response["resolutionDelivery"] = self._resolution_delivery_result(resolution)
        return response, artifact, integration

    def _adopted_board_paths(self, connection, run_id: str, manifest: dict) -> list[str]:
        """The exact adopted paths proven by board-bound adoption artifacts.

        Only pinned `resolved-output` records bound to this manifest and verified
        against their fixed Git objects authorize out-of-scope paths; local files
        alone never do. The named paths must sit inside the record's provably
        changed set, so a forged or drifted local record authorizes nothing.
        """
        module = workspace_module()
        adopted: set[str] = set()
        for row in connection.execute(
            "SELECT * FROM workflow_artifacts WHERE run_id=? AND kind='resolved-output'",
            (run_id,),
        ).fetchall():
            record = self._artifact_document(row)
            if record.get("kind") != "resolution" or record.get("action") != "adopt":
                continue
            if record.get("manifestSha256") != manifest["manifestSha256"]:
                continue
            try:
                module.verify_sealed_output(manifest, manifest, record)
            except BoardError as error:
                if error.code == "WORKSPACE_GIT_UNKNOWN":
                    raise
                continue
            candidates = set(record["adoptedPaths"])
            if candidates and candidates <= set(record.get("changedPaths") or []):
                adopted |= candidates
        return sorted(adopted)

    def _board_adopted_paths(self, run_id: str, manifest: dict) -> list[str]:
        with self.db.read() as connection:
            return self._adopted_board_paths(connection, run_id, manifest)

    def conclude(self,params,*,console_authority=None):
        from ...protocol.requests import parse_request,dump_request
        params=dump_request(parse_request("conclude",params,audience="service"))
        owner_id=params["runId"]
        target_id=params.get("targetRunId",owner_id)
        with self.db.read() as connection:
            owner=self._run_row(connection,owner_id)
            self._authorize(connection,owner,params,console_authority=console_authority,action="A conclusion")
            target=self._target_run(connection,owner,target_id)
            replay=self._conclude_replay(connection,owner,target,params)
            if replay is not None:
                return replay
            self._conclude_artifact(connection,target,params.get("artifactId"))
        descendants=self._stop_lead_for_conclusion(owner_id,target_id,params,console_authority)
        with self.db.read() as connection:
            owner=self._run_row(connection,owner_id)
            self._authorize(connection,owner,params,console_authority=console_authority,action="A conclusion")
            target=self._target_run(connection,owner,target_id)
            replay=self._conclude_replay(connection,owner,target,params)
            if replay is not None:
                return replay
            self._conclude_artifact(connection,target,params.get("artifactId"))
            task=self._conclude_precheck(connection,target,None)
            work=[{"snapshot":target,"work":self._conclude_seal_work(connection,target,task),"note":params["note"],"reason":None}]
            for child_id in descendants:
                child=self._run_row(connection,child_id)
                child_task=self._conclude_precheck(connection,child,None)
                work.append({"snapshot":child,"work":self._conclude_seal_work(connection,child,child_task),"note":None,
                             "reason":make_reason("lead.owned-concluded",leadRunId=target_id)})
        for item in work:
            item["sealed"]=self._seal_conclusion(item["snapshot"]["run_id"],item["work"])
        now=self.now()
        with self.db.write() as connection:
            owner=self._run_row(connection,owner_id)
            actor,_=self._authorize(connection,owner,params,console_authority=console_authority,action="A conclusion")
            target=self._target_run(connection,owner,target_id)
            replay=self._conclude_replay(connection,owner,target,params)
            if replay is not None:
                return replay
            origin=self._conclude_artifact(connection,target,params.get("artifactId"))
            for item in reversed(work):
                run=self._run_row(connection,item["snapshot"]["run_id"])
                self._expect_current(run,item["snapshot"])
                task=connection.execute("SELECT * FROM tasks WHERE task_id=?",(run["run_id"],)).fetchone()
                result=self._conclude_record(connection,run_row=run,task=task,actor=actor,now=now,note=item["note"],
                                              reason=item["reason"],sealed_record=item["sealed"])
                if run["run_id"]==target_id:
                    conclusion_id=result["conclusionId"]
            if origin is not None:
                bound=connection.execute("UPDATE workflow_artifacts SET response_kind='conclude',response_id=? WHERE artifact_id=? AND response_kind IS NULL",
                                         (conclusion_id,origin["artifact_id"]))
                if bound.rowcount!=1:
                    raise BoardError("CONFLICT","A different action already answered this artifact; get again")
            head=self.board._head_of(connection)
        self.board._notify(head)
        for child_id in descendants:
            self._reclaim_checkout(run_id=child_id)
        reclaimed=self._reclaim_checkout(run_id=target_id)
        with self.db.read() as connection:
            return self._action_view(connection,self._run_row(connection,owner_id),conclusionId=conclusion_id,alreadyResponded=False,
                                     **{k:params[k] for k in ("artifactId","targetRunId") if k in params},reclaim=reclaimed)


    def _conclude_replay(self,connection,owner,target,params):
        concluded=host_conclusions.current(connection,target)
        if concluded is None:
            return None
        if concluded["note"]!=params["note"]:
            raise BoardError("CONFLICT","This micro task already has a different conclusion; get again")
        bound=connection.execute("SELECT artifact_id FROM workflow_artifacts WHERE response_kind='conclude' AND response_id=?",(concluded["conclusion_id"],)).fetchone()
        if (bound[0] if bound is not None else None)!=params.get("artifactId"):
            raise BoardError("CONFLICT","This conclusion answered a different artifact; get again")
        return self._action_view(connection,owner,conclusionId=concluded["conclusion_id"],alreadyResponded=True,
                                 **{k:params[k] for k in ("artifactId","targetRunId") if k in params})


    def _conclude_artifact(self,connection,target,artifact_id):
        if artifact_id is None:
            if target["state"]=="delivered" and target["final_artifact_id"] is not None:
                raise BoardError("INVALID_ARGUMENT","A delivered result must be answered by artifactId",field="artifactId")
            return None
        row=connection.execute("SELECT * FROM workflow_artifacts WHERE run_id=? AND artifact_id=?",(target["run_id"],artifact_id)).fetchone()
        if row is None:
            raise BoardError("NOT_FOUND","This artifact does not belong to the addressed micro task")
        current = (row["artifact_id"] == target["final_artifact_id"] if target["state"] == "delivered" else
                   target["state"] in ("failed", "cancelled") and row["turn_id"] == target["current_turn_id"]
                   and row["kind"] in ("output", "partial-output", "resolved-output", "result"))
        if row["response_kind"] is not None or not current:
            raise BoardError("CONFLICT","This artifact was already answered or replaced; get again")
        return row

    def _stop_lead_for_conclusion(self, owner_run_id, target_id, params, console_authority) -> list[str]:
        """Stop the complete graph before preparing its atomic Host conclusions."""
        with self.db.write() as connection:
            owner = self._run_row(connection, owner_run_id)
            actor, initiator = self._authorize(connection, owner, params, console_authority=console_authority,
                                                action="A Host conclusion")
            lead = self._target_run(connection, owner, target_id)
            self._conclude_artifact(connection,lead,params.get("artifactId"))
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
                                 conflictId=conflict["conflict_id"], paths=json.loads(conflict["evidence_json"])["blockingPaths"])
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
            sealed = workspace_module().host_seal(
                self.board.directory, manifest, run_id, attempt_id, allowed_paths=allowed,
                admission_check=lambda: self._workspace_admission_check(run_id))
            with self.db.read() as connection:
                original = self._initial_input_commit(connection, run_id)
            workspace_module().cumulative_patch(manifest, sealed, original)
            return sealed
        except BoardError as error:
            if error.code == "WORKSPACE_SCOPE_VIOLATION":
                raise BoardError(
                    "WORKSPACE_SCOPE_VIOLATION",
                    "The checkout holds changes outside the authorized write scope; conclude cannot seal them as a partial output",
                    paths=error.details.get("paths"),
                ) from error
            raise

    def _workspace_admission_check(self, run_id: str) -> None:
        """Check deletion marking while the workspace operation holds its lock."""
        with self.db.read() as connection:
            reject_deleting_run(connection, run_id)

    def _conclude_precheck(self, connection, run_row, task) -> dict:
        """Everything that must hold before one micro task may be concluded."""
        run_id = run_row["run_id"]
        reject_deleting_run(connection, run_id)
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
                paths=json.loads(conflict["evidence_json"])["blockingPaths"],
            )
        if connection.execute("SELECT 1 FROM workflow_continuations WHERE run_id=? AND state IN ('recorded','queued') LIMIT 1", (run_id,)).fetchone():
            raise BoardError("CONFLICT", "A continuation is still pending")
        if connection.execute("SELECT 1 FROM workflow_children WHERE parent_run_id=? AND state IN ('active','attention') LIMIT 1", (run_id,)).fetchone():
            raise BoardError("CONFLICT", "Settle owned helpers before concluding")
        return task

    def _conclude_seal_work(self, connection, run_row, task):
        """Preserve managed work; an existing checkout seals only unsealed turns."""
        if run_row["checkout"] == "none":
            return None
        manifest = self._run_manifest(connection, run_row)
        if manifest["access"] == "read" or not task["selected_attempt_id"]:
            # Read-only work and an unstarted checkout have no changes to seal.
            # Ending a reader does not depend on its checkout still matching
            # the frozen input, and a partial output needs an attempt identity.
            return None
        if json.loads(run_row["execution_workspace_json"])["kind"] == "existing" and connection.execute(
            "SELECT 1 FROM workflow_artifacts WHERE run_id=?"
            " AND kind IN ('output','resolved-output','partial-output') AND turn_id=("
            " SELECT turn_id FROM workflow_turns WHERE run_id=? ORDER BY turn_index DESC LIMIT 1) LIMIT 1",
            (run_row["run_id"], run_row["run_id"]),
        ).fetchone() is not None:
            # Use the submitted kind: a prepared continuation of a managed
            # worktree also has kind existing in its current manifest. Only a
            # submitted existing checkout can skip this seal.
            return None
        allowed = self._board_adopted_paths(run_row["run_id"], manifest)
        return manifest, allowed, task["selected_attempt_id"]

    def _conclude_record(self, connection, *, run_row, task, actor: dict, now: str, note: str | None,
                         sealed_record: dict | None, reason: dict | None = None) -> dict:
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
                now=now,
            )
        identifier = "conclusion-" + str(uuid.uuid4())
        connection.execute(
            'INSERT INTO workflow_host_conclusions(conclusion_id, run_id, attempt_id, execution_status, note, reason_code, reason_params_json, artifact_id, actor_role, actor_id, created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)',
            (identifier,run_id,attempt_id,run_row["state"],note,reason["code"] if reason else None,canonical_json(reason["params"]) if reason else None,partial_artifact_id,actor["role"],actor.get("id"),now,),
        )
        if sealed_record is not None:
            self.board._append_event(
                connection, "workflow.partial_output_preserved", task_id=run_id, attempt_id=attempt_id,
                payload={"partial": True, "verified": True, "final": False, "artifactId": partial_artifact_id,
                         "actor": actor},
            )
        self.board._append_event(
            connection, "workflow.host_concluded", task_id=run_id,
            attempt_id=attempt_id, revision=None,
            payload={"conclusionId": identifier, "executionStatus": run_row["state"], "actor": actor,
                     "partialArtifactId": partial_artifact_id, **({"reason": reason} if reason else {})},
        )
        self.board._transition_task(connection, task, "concluded")
        connection.execute("UPDATE tasks SET queue_reason=NULL WHERE task_id=?", (run_id,))
        connection.execute("UPDATE workflow_runs SET state='concluded', workspace_id=NULL, workspace_manifest_sha256=NULL WHERE run_id=?", (run_id,))
        self._release_reservations(connection, run_id, now)
        accumulate_overall(connection, run_id)
        connection.execute("UPDATE workflow_runs SET updated_at=? WHERE run_id=?", (now,run_row["run_id"]))
        run_row = self._run_row(connection, run_row["run_id"])
        task = connection.execute("SELECT * FROM tasks WHERE task_id=?", (run_id,)).fetchone()
        return self._action_view(connection,run_row,conclusionId=identifier)



    def _reclaim_checkout(self, *, run_id: str, cwd: str | None = None) -> dict:
        """Reclaim from terminal board facts, independently of a Host generation."""
        with self.db.read() as connection:
            reject_deleting_run(connection, run_id)
            run = self._run_row(connection, run_id)
            if run["checkout"] == "none":
                return {"removed": False, "reasons": ["no-checkout"]}
            previous_plan = connection.execute(
                "SELECT * FROM workspace_cleanup_plans WHERE run_id=? AND state IN ('applying','applied')"
                " ORDER BY rowid DESC LIMIT 1", (run_id,),
            ).fetchone()
            if previous_plan is not None and previous_plan["state"] == "applied":
                done = self._plan_view(previous_plan)
                return {"removed": True, "alreadyRemoved": bool((done["result"] or {}).get("alreadyRemoved")),
                        "path": done["path"],
                        "planId": done["planId"], "retention": done["retention"]}
            missing = previous_plan is not None and not workspace_module()._path_present(
                self.board.directory / "workspaces" / previous_plan["workspace_id"])
            if missing:
                physical = {"workspaceId": previous_plan["workspace_id"],
                            "checkoutRoot": previous_plan["path"]}
            else:
                manifest = self._run_manifest(connection, run)
                provenance = [manifest, *self._allocation_provenance(connection, run)]
                physical = next((item for item in provenance if item["kind"] == "worktree"
                                 and item["checkoutRoot"] == manifest["checkoutRoot"]
                                 and project_id(item) == project_id(manifest)), None)
                if physical is None:
                    return {"removed": False, "reasons": ["not-a-managed-worktree"]}
            interrupted = previous_plan is not None and previous_plan["state"] == "applying"
        params = {"runId": run_id}
        planned = None
        try:
            repository = None
            if missing and cwd is not None:
                repository = workspace_module().repository_at(cwd, allow_parent=True)
            if not missing:
                with self._cleanup_allocation_lock(self.board.directory, physical):
                    with self.db.read() as connection:
                        reject_deleting_run(connection, run_id)
                    repository = workspace_module().locate_repository(
                        physical, cwd=cwd, candidates=lambda: repository_candidates(self.db, physical),
                        repair=True, removal_started=interrupted)
                    if repository is None:
                        raise BoardError("WORKSPACE_REPOSITORY_NOT_FOUND", "The workspace repository is unavailable",
                                         checkoutRoot=physical["checkoutRoot"], projectId=project_id(physical))
            planned = self.cleanup_plan(params)
            plan = planned["plan"]
            if plan["state"] != "applied":
                applied = self.cleanup_apply({
                    "runId": run_id, "planId": plan["planId"], "confirmPath": plan["path"],
                }, repository=repository)
                plan = applied["plan"]
        except (BoardError, OSError) as error:
            code = error.code if isinstance(error, BoardError) else "WORKSPACE_IO_ERROR"
            details = error.details if isinstance(error, BoardError) else {}
            if code == 'WORKSPACE_BUSY':
                with self.db.read() as connection:
                    current = connection.execute(
                        "SELECT * FROM workspace_cleanup_plans WHERE run_id=? AND path=? AND state='applied'",
                        (run_id, physical['checkoutRoot'])).fetchone()
                if current is not None:
                    done = self._plan_view(current)
                    return {'removed': True, 'alreadyRemoved': True, 'path': done['path'],
                            'planId': done['planId'], 'retention': done['retention']}
            reasons = details.get("reasons") or [code]
            if code == "WORKSPACE_REPOSITORY_NOT_FOUND":
                reasons = ["repository-not-found"]
            result = {"removed": False, "reasons": reasons}
            if planned is not None:
                result.update(planId=planned["plan"]["planId"], path=planned["plan"]["path"])
            if code == "NOT_READY":
                result.update({key: value for key, value in details.items()
                               if key in ("attachedBranch", "reason") or key.startswith("unsealedPaths")})
                if planned is not None and planned["plan"]["state"] == "applying":
                    result["detail"] = error.message + ": " + ", ".join(result["reasons"])
            elif code not in ('WORKSPACE_BUSY', 'WORKSPACE_REPOSITORY_NOT_FOUND'):
                result["detail"] = error.message if isinstance(error, BoardError) else str(error)
            return result
        return {"removed": True, "alreadyRemoved": bool((plan["result"] or {}).get("alreadyRemoved")),
                "path": plan["path"], "planId": plan["planId"], "retention": plan["retention"]}

    # -- workspace lifecycle (Host-owned) ------------------------------------
    def _find_integration(self, connection, run_id: str, artifact_id: str):
        """The newest verified integration record (or explicit not-required) of one artifact."""
        rows = connection.execute(
            "SELECT * FROM workflow_integrations WHERE run_id=? AND artifact_id=?"
            " ORDER BY created_at DESC, rowid DESC",
            (run_id, artifact_id),
        ).fetchall()
        if not rows:
            return None
        record = rows[0]
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







    def _conflict_row(self, connection, run_id: str, conflict_id: str):
        row = connection.execute(
            "SELECT * FROM workflow_workspace_conflicts WHERE conflict_id=? AND run_id=?", (conflict_id, run_id)
        ).fetchone()
        if row is None:
            raise BoardError("NOT_FOUND", "Unknown recorded workspace conflict for this run", conflictId=conflict_id)
        return row

    @staticmethod
    def _conflict_fingerprint(connection, conflict):
        result = connection.execute(
            "SELECT json_extract(decision_json,'$.result.resolvedFingerprint') FROM workflow_requests "
            "WHERE json_extract(decision_json,'$.result.conflictId')=? ORDER BY decided_at DESC,rowid DESC LIMIT 1",
            (conflict["conflict_id"],),
        ).fetchone()
        return result[0] if result is not None else conflict["observed_fingerprint"]

    def _conflict_manifest(self, connection, run_row, conflict) -> dict:
        """The exact execution manifest of the attempt the conflict belongs to."""
        execution = {}
        if conflict["turn_id"]:
            turn = connection.execute("SELECT * FROM workflow_turns WHERE turn_id=?", (conflict["turn_id"],)).fetchone()
            if turn is not None:
                execution = turn_manifest(self.board.directory, json.loads(turn["input_json"]))
        manifest_sha = json.loads(conflict["evidence_json"])["manifestSha256"]
        if execution.get("manifestSha256") != manifest_sha:
            raise BoardError("WORKSPACE_CONFLICT",
                             "The recorded conflict does not match the run's execution manifest",
                             conflictId=conflict["conflict_id"])
        return execution

    @staticmethod
    def _resolution_delivery_view(conflict) -> dict | None:
        if not conflict["delivery_json"]:
            return None
        facts = json.loads(conflict["delivery_json"])
        return {"resolvedFingerprint": facts["resolvedFingerprint"],
                "deliveredAt": facts["deliveredAt"]}

    @staticmethod
    def _resolution_delivery_result(resolution) -> dict:
        delivery = resolution["delivery"]
        return {
            "conflictId": resolution["conflict"]["conflict_id"],
            "action": resolution["conflict"]["action"],
            "artifactId": resolution["conflict"]["artifact_id"],
            "attemptId": resolution["conflict"]["attempt_id"],
            "turnId": resolution["conflict"]["turn_id"],
            "deliveredAt": delivery["deliveredAt"],
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
        stored = json.loads(attempt["result_json"])
        if stored["status"] != "failed":
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
            if delivery is None:
                continue
            proof = self._resolution_native_completion(connection, run_row, conflict)
            if proof is None or proof["disposition"] != "completed":
                continue
            return {"conflict": conflict, "delivery": delivery, "proof": proof}
        return None

    def _resolve(self,params,*,console_authority=None):
        owner_id=params["runId"]
        target_id=params.get("targetRunId",owner_id)
        request_id=params["requestId"]
        parameters={k:v for k,v in params.items() if k not in ("runId","requestId")}
        with self.db.read() as connection:
            owner=self._run_row(connection,owner_id)
            actor,_=self._authorize(connection,owner,params,console_authority=console_authority,action="A workspace resolution")
            target=self._target_run(connection,owner,target_id)
            request=self._request_row(connection,owner_id,request_id)
            replay=self._resolution_replay(connection,owner,target,request,params)
            if replay is not None:
                return replay
            self._check_origin(connection,owner,target,("workflow_requests",request,"request_id"))
            if request["reason_code"]!="workspace-scope-violation":
                raise BoardError("CONFLICT","This request does not ask for workspace resolution")
            ids=json.loads(request["reason_params_json"])["conflictIds"]
            conflicts=connection.execute("SELECT * FROM workflow_workspace_conflicts WHERE run_id=? AND conflict_id IN (SELECT value FROM json_each(?)) AND state='open'",
                                         (target_id,canonical_json(ids))).fetchall()
            matches = conflicts if len(conflicts)==1 else [
                conflict for conflict in conflicts
                if self._conflict_fingerprint(connection,conflict)==params["observedFingerprint"]]
            if len(matches)!=1:
                raise BoardError("CONFLICT","observedFingerprint must identify exactly one current site in this request; get again")
            conflict=matches[0]
            conflict_id=conflict["conflict_id"]
            snapshot=target
            self._lineage_stop_evidence(connection,target,"A workspace resolution")
            manifest=self._conflict_manifest(connection,target,conflict)
            original=connection.execute("SELECT * FROM workflow_artifacts WHERE run_id=? AND kind='input' ORDER BY rowid LIMIT 1",(target_id,)).fetchone()
            original_commit=self._artifact_document(original)["inputCommit"]
            if "input" in params:
                remaining = set(json.loads(conflict["evidence_json"])["blockingPaths"]) - set(json.loads(conflict["resolved_paths_json"]))
                if len(conflicts)!=1 or (params["resolution"]=="restore" and params.get("paths")
                                         and not remaining.issubset(params["paths"])):
                    raise BoardError("INVALID_ARGUMENT", "Resolve partial paths without input; continuation input requires settling every remaining site", field="input")
        buddy=self._named_buddy(params["buddy"]) if "buddy" in params else None
        if buddy is not None:
            buddy=self._validated_buddy(buddy)
        if "buddy" in params and not params.get("note"):
            raise BoardError("INVALID_ARGUMENT","An explicit buddy change requires note",field="note")
        if "input" in params:
            choice=json.loads(snapshot["execution_workspace_json"])
            if snapshot["delegation_authority"] and (params.get("buddy")=="route" or params.get("access",choice["access"])!="full"):
                raise BoardError("INVALID_ARGUMENT","A Lead requires full access and a complete explicit buddy")
            self._require_permission(buddy if "buddy" in params else self._buddy(snapshot),params.get("access",choice["access"]))
            with self.db.read() as connection:
                self._validate_continuation(connection,snapshot,params,buddy,params.get("buddy")=="route")
        def admit_resolution():
            self._workspace_admission_check(target_id)
            with self.db.read() as connection:
                owner = self._run_row(connection,owner_id)
                self._authorize(connection,owner,params,console_authority=console_authority,action="A workspace resolution")
                target = self._target_run(connection,owner,target_id)
                self._expect_current(target,snapshot)
                request = self._request_row(connection,owner_id,request_id)
                self._check_origin(connection,owner,target,("workflow_requests",request,"request_id"))

        result=workspace_module().resolve(self.board.directory,manifest,task_id=target_id,attempt_id=conflict["attempt_id"],
                                          action=params["resolution"],paths=params.get("paths",[]),observed_fingerprint=params["observedFingerprint"],
                                          note=params.get("note"),actor=actor,admission_check=admit_resolution)
        record=result.get("artifact")
        if params["resolution"]!="abandon" and isinstance(record,dict):
            record["cumulativePatch"]=workspace_module().cumulative_patch(manifest,record,original_commit)
        now=self.now()
        with self.db.write() as connection:
            owner=self._run_row(connection,owner_id)
            actor,initiator=self._authorize(connection,owner,params,console_authority=console_authority,action="A workspace resolution")
            target=self._target_run(connection,owner,target_id)
            request=self._request_row(connection,owner_id,request_id)
            replay=self._resolution_replay(connection,owner,target,request,params)
            if replay is not None:
                return replay
            self._expect_current(target,snapshot)
            self._check_origin(connection,owner,target,("workflow_requests",request,"request_id"))
            current=self._conflict_row(connection,target_id,conflict_id)
            if current["state"]!="open" or tuple(current)!=tuple(conflict):
                raise BoardError("CONFLICT","The recorded site changed while resolving; get again")
            self._lineage_stop_evidence(connection,target,"A workspace resolution")
            artifact_id=(self._pin_artifact(connection,run_id=target_id,kind="abandoned-site" if params["resolution"]=="abandon" else "resolved-output",
                                           manifest=record,attempt_id=current["attempt_id"],turn_id=current["turn_id"],now=now)
                         if isinstance(record,dict) else None)
            proof=self._resolution_native_completion(connection,target,current) if result["state"] in ("restored","adopted") else None
            delivery={"resolvedFingerprint":result["resolvedFingerprint"],"deliveredAt":now} if proof is not None and proof["disposition"]=="completed" else None
            connection.execute("UPDATE workflow_workspace_conflicts SET state=?,action=?,resolved_paths_json=?,conflicting_paths_json=?,artifact_id=?,actor_role=?,actor_id=?,note=?,delivery_json=?,updated_at=? WHERE conflict_id=? AND state='open'",
                               (result["state"],params["resolution"],canonical_json(result["resolvedPaths"]),canonical_json(result["remainingPaths"]),artifact_id,
                                actor["role"],actor.get("id"),params.get("note"),canonical_json(delivery) if delivery else None,now,conflict_id))
            resolved={"conflictId":conflict_id,"artifactId":artifact_id,"state":result["state"],"resolvedFingerprint":result["resolvedFingerprint"]}
            remaining=connection.execute("SELECT conflict_id FROM workflow_workspace_conflicts WHERE run_id=? AND state='open' ORDER BY created_at,conflict_id",(target_id,)).fetchall()
            if "input" in params and not remaining:
                # The resolution and its continuation become durable together; preparation can recover the queued intent.
                response=self._record_continuation(connection,owner,target,("workflow_requests",request,"request_id"),params,
                                                    buddy,params.get("buddy")=="route",actor,initiator,now)
                connection.execute("UPDATE workflow_requests SET decision_json=?,decided_at=? WHERE response_kind='continue' AND response_id=?",
                                   (canonical_json({"result":resolved}),now,response["continuationId"]))
            else:
                self._answer_request(connection,owner,request,kind="resolution",response_id=conflict_id,state="approved",now=now,
                                     decision={"parameters":parameters,"result":resolved})
                if delivery:
                    connection.execute("UPDATE workflow_runs SET state='delivered',final_attempt_id=?,final_artifact_id=?,current_turn_id=?,current_attempt_id=NULL,active_request_id=NULL,updated_at=? WHERE run_id=?",
                                       (current["attempt_id"],artifact_id,current["turn_id"],now,target_id))
                    connection.execute("UPDATE tasks SET queue_reason=NULL,updated_at=? WHERE task_id=?",(now,target_id))
                elif proof is not None:
                    self._record_request(connection,target,proof["turnRow"],proof["turn"],proof["disposition"],now)
                if remaining:
                    next_id=self._record_system_request(connection,self._run_row(connection,target_id),make_reason("workspace-scope-violation",conflictIds=[r[0] for r in remaining]),{},now)
                    self._propagate_boundary(connection,self._request_row(connection,target_id,next_id),now)
                elif not delivery:
                    self._sync_boundary(connection,target_id,now,default_state="awaiting-host")
                response=self._action_view(connection,self._run_row(connection,owner_id),requestId=request_id,artifactId=artifact_id,alreadyResponded=False,
                                           **({"continuationBlocked":True} if "input" in params else {}))
            response.update(resolutionState=result["state"],resolvedFingerprint=result["resolvedFingerprint"])
            if target_id!=owner_id:
                response["targetRunId"]=target_id
            self.board._append_event(connection,"workflow.workspace_resolved",task_id=target_id,attempt_id=current["attempt_id"],
                                     payload={"requestId":request_id,"conflictId":conflict_id,"action":params["resolution"],"state":result["state"],"actor":actor,"artifactId":artifact_id})
            head=self.board._head_of(connection)
        self.board._notify(head)
        return response


    def _resolution_replay(self,connection,owner,target,request,params):
        if request["response_kind"] is None:
            return None
        if request["response_kind"]=="continue":
            return self._continue_replay(connection,owner,("workflow_requests",request,"request_id"),params)
        if request["response_kind"]!="resolution":
            raise BoardError("CONFLICT","A different action answered this request; get again")
        saved=json.loads(request["decision_json"])
        if saved["parameters"]!={k:v for k,v in params.items() if k not in ("runId","requestId")}:
            raise BoardError("CONFLICT","The recorded resolution has different action parameters; get again")
        return self._action_view(connection,owner,requestId=request["request_id"],artifactId=saved["result"]["artifactId"],alreadyResponded=True,
                                 resolutionState=saved["result"]["state"],resolvedFingerprint=saved["result"]["resolvedFingerprint"],
                                 **({"targetRunId":target["run_id"]} if target["run_id"]!=owner["run_id"] else {}))

    def _allocation_provenance(self, connection, run_row):
        return retained_manifests(self.board.directory, connection, run_row['run_id'])

    def _cleanup_reasons(self, connection, run_row, task, manifest: dict | None,
                         *, checkout_root: str | None = None, workspace_id: str | None = None,
                         frozen_retention: dict | None = None) -> tuple[list[str], dict, dict]:
        """Everything that must hold before one disposable checkout may be removed."""
        checkout_root = manifest["checkoutRoot"] if manifest is not None else checkout_root
        workspace_id = manifest["workspaceId"] if manifest is not None else workspace_id
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
        if int(connection.execute(
            "SELECT COUNT(*) AS count FROM workspace_reservations WHERE checkout_root=?"
            " AND state IN ('held','transferred') AND holder_task_id!=?",
            (checkout_root, run_row["run_id"]),
        ).fetchone()["count"]):
            reasons.append("workspace-dependency")
        evidence = {
            "hostConclusion": host_conclusions.view(conclusion),
            "attemptId": task["selected_attempt_id"],
            "acceptance": {"state": run_row["state"], "acceptedAt": task["accepted_at"],
                           "verdict": "accepted" if task["accepted_at"] else None},
            "shutdown": summary,
            "finalArtifactId": run_row["final_artifact_id"],
            "integrationId": integration["integration_id"] if integration is not None else None,
        }
        if frozen_retention is not None:
            return reasons, evidence, frozen_retention
        retention = {
            "logicalWorkspaceDirectory": str(self.board.directory / "workspaces" / (workspace_id or "")),
            "fixedRefs": [],
            "outputPatches": [],
            "artifactIds": [],
        }
        for row in connection.execute(
            "SELECT * FROM workflow_artifacts WHERE run_id=? ORDER BY rowid", (run_row["run_id"],)
        ).fetchall():
            retention["artifactIds"].append(row["artifact_id"])
            pinned = self._artifact_document(row)
            if isinstance(pinned.get("diffPath"), str):
                retention["outputPatches"].append(pinned["diffPath"])
            cumulative = (self._cumulative_patch_view(row, pinned, connection)
                          if row["kind"] in ("output", "partial-output", "resolved-output") else None)
            if isinstance(cumulative, dict) and isinstance(cumulative.get("path"), str):
                retention["outputPatches"].append(cumulative["path"])
            if isinstance(pinned.get("ref"), str):
                retention["fixedRefs"].append(pinned["ref"])
        retention["outputPatches"] = list(dict.fromkeys(retention["outputPatches"]))
        return reasons, evidence, retention

    def _cleanup_operation_view(self, connection, run_row) -> dict:
        """A lifecycle reply for a physical cleanup action, using only board facts."""
        task = connection.execute("SELECT state FROM tasks WHERE task_id=?", (run_row["run_id"],)).fetchone()
        latest = connection.execute(
            "SELECT * FROM workspace_cleanup_plans WHERE run_id=? ORDER BY rowid DESC LIMIT 1",
            (run_row["run_id"],),
        ).fetchone()
        return {"runId": run_row["run_id"], "state": run_row["state"], "status": task["state"], "cleanup": self._plan_view(latest)}

    def cleanup_plan(self, params: dict) -> dict:
        """Name one allocation in the internal journal; deletion proves live facts."""
        run_id = params["runId"]
        with self.db.read() as connection:
            reject_deleting_run(connection, run_id)
            run_row = self._run_row(connection, run_id)
            started = connection.execute(
                "SELECT * FROM workspace_cleanup_plans WHERE run_id=? AND state IN ('applying','applied')"
                " ORDER BY rowid DESC LIMIT 1", (run_id,)).fetchone()
            if started is not None and (started["state"] == "applied" or not workspace_module()._path_present(
                    self.board.directory / "workspaces" / started["workspace_id"])):
                return {**self._cleanup_operation_view(connection, run_row), "duplicate": True, "plan": self._plan_view(started), "targetRunId": run_id}
            task = connection.execute("SELECT * FROM tasks WHERE task_id=?", (run_id,)).fetchone()
            manifest = self._run_manifest(connection, run_row)
            retained = self._allocation_provenance(connection, run_row)
            reasons, evidence, retention = self._cleanup_reasons(connection, run_row, task, manifest)
        physical = next((item for item in (manifest, *retained) if item["kind"] == "worktree"
                         and item["checkoutRoot"] == manifest["checkoutRoot"]
                         and project_id(item) == project_id(manifest)), None)
        allocation = ({"workspaceId": physical["workspaceId"], "path": physical["checkoutRoot"], "kind": "worktree",
                       "projectId": project_id(physical),
                       "manifestSha256": physical["manifestSha256"], "manifestWorkspaceId": manifest["workspaceId"]}
                      if physical is not None else None)
        workspace = {
            "workspaceId": (allocation or manifest)["workspaceId"],
            "manifestWorkspaceId": manifest["workspaceId"],
            "kind": (allocation or manifest)["kind"],
            "projectId": project_id(manifest),
            "path": allocation["path"] if allocation else manifest["checkoutRoot"],
            "cwd": manifest["path"], "allocation": allocation,
        }
        if allocation is None:
            reasons.append("not-a-managed-worktree")
        retention.update(workspaceDirectory=str(self.board.directory / "workspaces" / workspace["workspaceId"]),
                         cwd=workspace["cwd"])
        evidence["workspace"] = workspace
        reasons = sorted(set(reasons))
        now = self.now()
        with self.db.write() as connection:
            reject_deleting_run(connection, run_id)
            run_row = self._run_row(connection, run_id)
            existing = connection.execute(
                "SELECT * FROM workspace_cleanup_plans WHERE run_id=? AND path=? ORDER BY rowid DESC LIMIT 1",
                (run_id, workspace['path']),
            ).fetchone()
            fields = ('workspace_id', 'path', 'kind')
            identity = (workspace['workspaceId'], workspace['path'], workspace['kind'])
            if existing is not None and (existing['state'] in ('applying', 'applied')
                                         or tuple(existing[field] for field in fields) == identity):
                return {**self._cleanup_operation_view(connection, run_row), "duplicate": True, "plan": self._plan_view(existing), "targetRunId": run_id}
            plan_id = existing['plan_id'] if existing is not None else "cln-" + uuid.uuid4().hex
            if existing is not None:
                connection.execute(
                    "UPDATE workspace_cleanup_plans SET workspace_id=?,path=?,kind=?,state=?,"
                    "evidence_json=?,retention_json=?,reasons_json=?,revision=revision+1 WHERE plan_id=?",
                    (*identity, "blocked" if reasons else "planned", canonical_json(evidence), canonical_json(retention),
                     canonical_json(reasons), plan_id))
            else:
                connection.execute(
                    "INSERT INTO workspace_cleanup_plans(plan_id,run_id,workspace_id,path,"
                    "kind,state,evidence_json,retention_json,reasons_json,created_at)"
                    " VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (plan_id, run_id, workspace["workspaceId"],
                     workspace["path"], workspace["kind"], "blocked" if reasons else "planned",
                     canonical_json(evidence), canonical_json(retention), canonical_json(reasons), now),
                )
            self.board._append_event(connection, "workflow.cleanup_planned", task_id=run_id,
                                     revision=None,
                                     payload={"planId": plan_id, "path": workspace["path"],
                                              "eligible": not reasons, "reasons": reasons})
            connection.execute("UPDATE workflow_runs SET updated_at=? WHERE run_id=?", (now,run_row["run_id"]))
            run_row = self._run_row(connection, run_row["run_id"])
            plan = connection.execute("SELECT * FROM workspace_cleanup_plans WHERE plan_id=?", (plan_id,)).fetchone()
            response = {**self._cleanup_operation_view(connection, run_row), "duplicate": False, "plan": self._plan_view(plan), "targetRunId": run_id}
            head = self.board._head_of(connection)
        self.board._notify(head)
        return response

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

    def _missing_cleanup_proof(self, connection, run_row, plan):
        """Match an applying plan to its SQL owner and terminal binding without file reads."""
        allocation = json.loads(plan["evidence_json"])["workspace"]["allocation"]
        if (allocation is None or plan["kind"] != "worktree"
                or allocation["workspaceId"] != plan["workspace_id"]
                or allocation["path"] != plan["path"]):
            raise BoardError("CONFLICT", "The cleanup plan no longer matches the allocation", planId=plan["plan_id"])
        owner = connection.execute(
            f"WITH manifest_owners AS ({MANIFEST_OWNERS_CTE})"
            " SELECT o.*,COALESCE(m.project_id,r.standalone_project_id) AS project_id"
            " FROM manifest_owners o JOIN workflow_runs r ON r.run_id=o.owner_run_id"
            " LEFT JOIN macro_tasks m ON m.macro_task_id=r.macro_task_id"
            " WHERE o.workspace_id=? AND o.manifest_sha256=?",
            (allocation["workspaceId"], allocation["manifestSha256"]),
        ).fetchone()
        binding = connection.execute(
            f"WITH run_bindings AS ({RUN_BINDINGS_CTE}), manifest_owners AS ({MANIFEST_OWNERS_CTE})"
            " SELECT b.workspace_id,o.checkout_root,COALESCE(m.project_id,r.standalone_project_id) AS project_id"
            " FROM run_bindings b JOIN workflow_runs r ON r.run_id=b.run_id"
            " LEFT JOIN macro_tasks m ON m.macro_task_id=r.macro_task_id"
            " JOIN manifest_owners o ON o.workspace_id=b.workspace_id AND o.manifest_sha256=b.manifest_sha256"
            " WHERE b.run_id=?", (run_row["run_id"],),
        ).fetchone()
        if (owner is None or owner["owner_run_id"] != run_row["run_id"]
                or owner["workspace_kind"] != "worktree" or owner["checkout_root"] != plan["path"]
                or owner["project_id"] != allocation["projectId"]
                or binding is None or binding["workspace_id"] != allocation["manifestWorkspaceId"]
                or binding["checkout_root"] != plan["path"] or binding["project_id"] != owner["project_id"]):
            raise BoardError("CONFLICT", "The cleanup plan no longer matches SQL workspace ownership",
                             planId=plan["plan_id"])
        return allocation, owner

    def cleanup_apply(self, params: dict, *, repository: str | None = None) -> dict:
        """Prove a terminal allocation once under its lock, fence, then remove it."""
        run_id, plan_id = params["runId"], params["planId"]
        with self.db.read() as connection:
            plan = connection.execute("SELECT * FROM workspace_cleanup_plans WHERE plan_id=? AND run_id=?",
                                      (plan_id, run_id)).fetchone()
            if plan is None:
                raise BoardError("NOT_FOUND", "Unknown cleanup plan for this run", planId=plan_id)
            if params["confirmPath"] != plan["path"]:
                raise BoardError("INVALID_ARGUMENT", "confirmPath must exactly match the planned checkout path",
                                 plannedPath=plan["path"])
        with self._cleanup_allocation_lock(self.board.directory, {"workspaceId": plan["workspace_id"]}):
            with self.db.read() as connection:
                reject_deleting_run(connection, run_id)
                run_row = self._run_row(connection, run_id)
                snapshot_run = dict(run_row)
                plan = connection.execute("SELECT * FROM workspace_cleanup_plans WHERE plan_id=?", (plan_id,)).fetchone()
                if plan["state"] == "applied":
                    return {**self._cleanup_operation_view(connection, run_row), "duplicate": True, "plan": self._plan_view(plan), "removed": True, "retention": json.loads(plan["retention_json"]), "targetRunId": run_id}
                task = connection.execute("SELECT * FROM tasks WHERE task_id=?", (run_id,)).fetchone()
                path = Path(plan["path"])
                resume = plan["state"] == "applying"
                missing = resume and not workspace_module()._path_present(
                    self.board.directory / "workspaces" / plan["workspace_id"])
                if missing:
                    allocation, owner = self._missing_cleanup_proof(connection, run_row, plan)
                    reasons, evidence, retention = self._cleanup_reasons(
                        connection, run_row, task, None, checkout_root=plan["path"],
                        workspace_id=allocation["manifestWorkspaceId"],
                        frozen_retention=json.loads(plan["retention_json"]))
                    manifest = None
                    retained = []
                    sealed = None
                else:
                    manifest = self._run_manifest(connection, run_row)
                    retained = self._allocation_provenance(connection, run_row)
                    identity = next((item for item in (manifest, *retained)
                                     if item.get("workspaceId") == plan["workspace_id"]), None)
                    if (identity is None or plan["path"] != identity["checkoutRoot"]):
                        raise BoardError("CONFLICT", "The cleanup plan no longer matches the allocation", planId=plan_id)
                    reasons, evidence, retention = self._cleanup_reasons(connection, run_row, task, manifest)
                    sealed = self._latest_handoff(connection, run_row, manifest)
                    proof_input = manifest
                    if sealed is not None:
                        pair = sealed['workspaceId'], sealed['manifestSha256']
                        proof_input = next((item for item in (manifest, *retained)
                                            if (item['workspaceId'], item['manifestSha256']) == pair), None)
                        if proof_input is None:
                            proof_input = owned_manifest(self.board.directory, connection, *pair)
                            retained.append(proof_input)
            inspection = None
            details = {}
            if not missing and not reasons and workspace_module()._path_present(path):
                inspection = workspace_module().cleanup_inspect(
                    self.board.directory, manifest, sealed=sealed, retained=retained,
                    allowed=self._board_adopted_paths(run_id, proof_input), resume=resume,
                    repository=repository)
                if workspace_module()._path_present(path):
                    reasons.extend(inspection["reasons"])
                    details = self._bounded_path_fields({"unsealedPaths": inspection["unsealedPaths"]}, ("unsealedPaths",))
                    if inspection["attachedBranch"] is not None:
                        details["attachedBranch"] = inspection["attachedBranch"]
                if not reasons and workspace_module()._path_present(path):
                    from .checkout_processes import allocation_created_at, require_idle_checkout
                    try:
                        directory = self.board.directory / "workspaces" / plan["workspace_id"]
                        require_idle_checkout(path, allocated_at=allocation_created_at(directory))
                    except BoardError as error:
                        if error.code != "NOT_READY":
                            raise
                        reasons.extend(error.details.get("reasons") or [error.details["reason"]["code"]])
                        details.update({key: value for key, value in error.details.items() if key != "reasons"})
            reasons = sorted(set(reasons))
            now = self.now()
            with self.db.write() as connection:
                reject_deleting_run(connection, run_id)
                run_row = self._run_row(connection, run_id)
                self._expect_current(run_row, snapshot_run)
                if reasons and resume:
                    # An interrupted removal keeps its complete original journal and fence.
                    raise BoardError("NOT_READY", "This checkout is not eligible for cleanup", reasons=reasons, **details)
                evidence["workspace"] = inspection or json.loads(plan["evidence_json"])["workspace"]
                if "reason" in details:
                    evidence["workspace"]["reason"] = details["reason"]
                if not missing:
                    retention.update(workspaceDirectory=str(self.board.directory / "workspaces" / plan["workspace_id"]),
                                     checkoutRoot=str(path), cwd=manifest["path"])
                if inspection is not None:
                    retention["fixedRefs"] = sorted(set(retention["fixedRefs"]) | set(inspection["refs"]))
                state = "blocked" if reasons else "applying"
                previous_workspace = json.loads(plan["evidence_json"])["workspace"]
                detail_changed = (inspection is not None and any(
                    inspection.get(key) != previous_workspace.get(key) for key in ("unsealedPaths", "attachedBranch")))
                detail_changed = detail_changed or details.get("reason") != previous_workspace.get("reason")
                if state != plan["state"] or reasons != json.loads(plan["reasons_json"]) or detail_changed:
                    connection.execute(
                        "UPDATE workspace_cleanup_plans SET state=?,evidence_json=?,retention_json=?,reasons_json=?,"
                        "revision=revision+1 WHERE plan_id=?",
                        (state, canonical_json(evidence), canonical_json(retention), canonical_json(reasons), plan_id))
                    self.board._append_event(connection, "workflow.cleanup_blocked" if reasons else "workflow.cleanup_applying",
                                             task_id=run_id, payload={"planId": plan_id, "reasons": reasons})
                head = self.board._head_of(connection)
            self.board._notify(head)
            if reasons:
                raise BoardError("NOT_READY", "This checkout is not eligible for cleanup", reasons=reasons, **details)
            # An absent allocation has no live manifest or .lock. Its frozen
            # plan may authorize removal of the original Git registration only.
            if missing:
                removed = workspace_module().cleanup_remove_missing(
                    self.board.directory, allocation, project_id=owner["project_id"],
                    repository=repository or owner["repository_path"])
            else:
                removed = workspace_module().cleanup_remove(self.board.directory, manifest, retained=retained,
                                                           resume=resume, proof=inspection, repository=repository)
            with self.db.write() as connection:
                run_row = self._run_row(connection, run_id)
                already_removed = bool(removed["alreadyRemoved"])
                connection.execute(
                    "UPDATE workspace_cleanup_plans SET state='applied',applied_at=?,result_json=?,revision=revision+1"
                    " WHERE plan_id=? AND state='applying'",
                    (now, canonical_json({"alreadyRemoved": already_removed}), plan_id))
                self._release_reservations(connection, run_id, now)
                self.board._append_event(connection, "workflow.cleanup_applied", task_id=run_id,
                                         revision=None,
                                         payload={"planId": plan_id, "path": str(path), "alreadyRemoved": already_removed})
                connection.execute("UPDATE workflow_runs SET updated_at=? WHERE run_id=?", (now,run_row["run_id"]))
                run_row = self._run_row(connection, run_row["run_id"])
                plan = connection.execute("SELECT * FROM workspace_cleanup_plans WHERE plan_id=?", (plan_id,)).fetchone()
                plan_view = self._plan_view(plan)
                response = {**self._cleanup_operation_view(connection, run_row), "duplicate": False, "plan": plan_view, "removed": True, "retention": plan_view["retention"], "targetRunId": run_id}
                head = self.board._head_of(connection)
            self.board._notify(head)
            return response

    # -- agent suggestion ----------------------------------------------------
    def suggest(self, params: dict, *, scope: dict | None = None) -> dict:
        from ...protocol.requests import parse_request, dump_request
        params = dump_request(parse_request("suggest", params, audience="lead"))
        run_id = params["runId"]
        body = params["body"]
        size = len(body.encode("utf-8"))
        scope = scope or current_scope() or {}
        if scope.get("kind") != AGENT_KIND or scope["runId"] != run_id:
            raise BoardError("FORBIDDEN", "suggest requires the execution credential of this run")
        attempt_id = scope["attemptId"]
        now = self.now()
        with self.db.write() as connection:
            self._active_credential(connection, scope)
            self._run_row(connection, run_id)
            suggestion_id = f"sug-{uuid.uuid4()}"
            connection.execute(
                "INSERT INTO workflow_suggestions(suggestion_id, run_id, attempt_id, body, created_at)"
                " VALUES(?,?,?,?,?)",
                (suggestion_id, run_id, attempt_id, body, now),
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
        run_row = connection.execute(
            "SELECT *"
            " FROM workflow_runs WHERE run_id=?", (task_row["task_id"],)
        ).fetchone()
        if run_row is None:
            return None
        extension = {
            "state": run_row["state"],
            "checkout": run_row['checkout'],
            "projectKind": run_row['project_kind'],
            "awaitingHost": run_row["state"] == "awaiting-host",
            "continuationCount": run_row["continuation_count"],
            "activeRequestId": run_row["active_request_id"],
            "finalArtifactId": run_row["final_artifact_id"],
            "currentTurnId": run_row["current_turn_id"],
            "actions": self._actions(connection,run_row),
            "hostConclusion": host_conclusions.view(host_conclusions.current(connection, run_row)),
        }
        if run_row['checkout'] == 'none':
            extension.update(self._none_notice(json.loads(run_row['execution_workspace_json'])['access']))
        if (current_scope() or {}).get("kind") != AGENT_KIND:
            extension.update(title=run_row["title"], macroTaskId=run_row["macro_task_id"])
        if run_row["active_request_id"]:
            request = connection.execute(
                "SELECT * FROM workflow_requests WHERE request_id=?", (run_row["active_request_id"],)
            ).fetchone()
            if request is not None:
                extension["requestKind"] = request["kind"]
                extension["requestSummary"] = request["summary"]
                extension["requestReason"] = request_reason(connection, request)
                payload = json.loads(request["payload_json"])
                extension["requestRouting"] = payload.get("source") == "routing"
                extension["requestTargetRunId"] = (payload.get("origin") or {}).get("runId")
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
                "SELECT r.*, w.state AS run_state, w.current_routing_id"
                " FROM workflow_routes r JOIN decision_requests d ON d.decision_id=r.decision_id"
                " JOIN workflow_runs w ON w.run_id=r.run_id WHERE d.task_id=?", (task_row["task_id"],),
            ).fetchone()
            if route is not None and (route["state"] != "pending"
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
                "SELECT continuation_id, workspace_id FROM workflow_continuations"
                " WHERE run_id=? AND state='queued' ORDER BY created_at LIMIT 1",
                (run_row["run_id"],),
            ).fetchone()
            turn_count = connection.execute(
                "SELECT COUNT(*) AS count FROM workflow_turns WHERE run_id=? AND state!='rejected'", (run_row["run_id"],)
            ).fetchone()["count"]
            if int(turn_count) > 0 and continuation is None:
                return "awaiting-host"
            if run_row["checkout"] != "none" and continuation is not None and continuation["workspace_id"] is None:
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
        """Observe the claim candidates' shared stash outside every transaction.

        Collection uses the claim's candidate window and repository bound. A
        coverage retry reads the selected run explicitly; failed Git reads stay
        honest unknown observations rather than inventing an empty stash.
        """
        target = selector or params.get("taskId") or params.get("runId")
        with self.db.read() as connection:
            if target is not None:
                rows = connection.execute(
                    "SELECT r.* FROM workflow_runs r JOIN tasks t ON t.task_id=r.run_id"
                    " WHERE t.state='queued' AND r.run_id=?",
                    (target,),
                ).fetchall()
            else:
                from .scheduling import DECISION_ADAPTER
                rows = []
                for class_predicate in ("t.adapter IS NOT ?", "t.adapter IS ?"):
                    rows.extend(connection.execute(
                        "SELECT r.*, t.created_at, t.task_id"
                        " FROM workflow_runs r JOIN tasks t ON t.task_id=r.run_id"
                        f" WHERE t.state='queued' AND {class_predicate}"
                        " ORDER BY t.created_at, t.task_id LIMIT ?",
                        (DECISION_ADAPTER, self.board.CLAIM_CANDIDATE_LIMIT),
                    ).fetchall())
                rows.sort(key=lambda row: (row["created_at"], row["task_id"]))
        repositories: dict[str, dict | None] = {}
        for row in rows:
            if row["checkout"] == "none":
                continue
            manifest = self._run_manifest(None, row)
            path = manifest["snapshot"]["repositoryPath"]
            if path not in repositories and len(repositories) < self._CLAIM_STASH_REPOSITORIES:
                repositories[path] = None
        module = workspace_module()
        pass_reason = None
        try:
            for path in repositories:
                repositories[path] = module.shared_stash_inventory(path)
        except BoardError as error:
            pass_reason = f"{error.code}: {error.message}"
        except OSError as error:
            pass_reason = str(error)
        if pass_reason is not None:
            for path, value in repositories.items():
                if value is None:
                    repositories[path] = module.stash_observation_unknown(path, pass_reason)
        return {"repositories": repositories}

    def run_repository_path(self, connection, task_id: str) -> str | None:
        """The shared repository of one run's current workspace manifest."""
        row = connection.execute(
            "SELECT * FROM workflow_runs WHERE run_id=?", (task_id,)
        ).fetchone()
        if row is None or row["checkout"] == "none":
            return None
        return self._run_manifest(connection, row)["snapshot"]["repositoryPath"]

    def collect_result_stash_facts(self, params: dict) -> dict | None:
        """Observe a micro-task result's shared stash outside its write transaction.

        None means no micro-task workspace applies. A Git read failure keeps its
        own reason so that settlement can pin an honest unknown seal observation.
        """
        with self.db.read() as connection:
            row = connection.execute(
                "SELECT t.input_json FROM attempts a JOIN workflow_turns t ON t.attempt_id=a.attempt_id"
                " WHERE a.attempt_id=?",
                (params["attemptId"],),
            ).fetchone()
        if row is None:
            return None
        manifest = turn_manifest(self.board.directory, json.loads(row["input_json"]))
        if manifest["kind"] == "none":
            return None
        path = manifest["snapshot"]["repositoryPath"]
        module = workspace_module()
        try:
            inventory = module.shared_stash_inventory(path)
        except BoardError as error:
            return {"repositoryPath": path, "failure": f"{error.code}: {error.message}"}
        except OSError as error:
            return {"repositoryPath": path, "failure": str(error)}
        return {"repositoryPath": path, "inventory": inventory}

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
        manifest = turn_manifest(self.board.directory, json.loads(row["input_json"]))
        if not manifest.get("workspaceId"):
            return None
        sealed = workspace_module().seal(self.board.directory, manifest, row["task_id"], attempt_id)
        with self.db.read() as connection:
            original = self._initial_input_commit(connection, row["task_id"])
        workspace_module().cumulative_patch(manifest, sealed, original)
        return record_reference(self.board.directory, sealed)

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
        from ...buddy.roles.turn_io import validated_checkpoint
        from ...private_files import open_read

        def private_record(path: Path) -> dict | None:
            try:
                with open_read(path) as stream:
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
        cwd = turn_manifest(self.board.directory, turn_input)["path"]
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
        manifest = self._run_manifest(connection, run_row)
        execution_workspace = self._turn_workspace(connection, run_row, manifest, continuation, turn_count)
        buddy = self._buddy(run_row)
        native = self._previous_codex_checkpoint(connection, previous)
        prior = (connection.execute('SELECT result_json FROM attempts WHERE attempt_id=?',
                                    (previous['attempt_id'],)).fetchone() if previous is not None else None)
        prior_result = json.loads(prior['result_json'] or '{}') if prior else {}
        prior_turn = (self.turn_outcome(prior_result)
                      if previous is not None and previous["state"] == "returned" else {})
        previous_session = prior_turn.get("sessionId")
        if native is not None:
            previous_session = native[1]["sessionId"]
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
        context = self._turn_context(connection, run_row, continuation, previous, turn_index)
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
                    "validatedOutcome": previous["state"] == "returned" and prior_turn["provenance"].get("outputSchemaValidated") is True,
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
            "version": 5 if run_row["checkout"] == "none" else TURN_INPUT_VERSION,
            "taskId": task["task_id"],
            "attemptId": attempt_id,
            "generation": generation,
            "turnId": turn_id,
            "resumeMode": resume_mode,
            "previousSessionId": previous_session,
            "context": context,
            "executionWorkspace": execution_workspace if run_row["checkout"] == "none" else manifest_ref(execution_workspace),
        }
        input_json = canonical_json(turn_input)
        connection.execute(
            "INSERT INTO workflow_turns(turn_id, run_id, attempt_id, generation, turn_index, resume_mode,"
            " previous_session_id, input_json, workspace_id, workspace_manifest_sha256, access, access_note, expected_seconds,project_kind,checkout, state, created_at, updated_at)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'prepared',?,?)",
            (
                turn_id,
                run_row["run_id"],
                attempt_id,
                generation,
                turn_index,
                resume_mode,
                previous_session,
                input_json,
                execution_workspace.get("workspaceId"),
                execution_workspace.get("manifestSha256"),
                execution_workspace["access"],
                json.loads(run_row["execution_workspace_json"]).get("accessNote"),
                json.loads(task["spec_json"])["expectedSeconds"],
                run_row["project_kind"],
                run_row["checkout"],
                now,
                now,
            ),
        )
        connection.execute(
            'UPDATE workflow_runs SET current_turn_id=?, current_attempt_id=?, updated_at=? WHERE run_id=?',
            (turn_id,attempt_id,now,run_row["run_id"],),
        )
        if continuation is not None:
            connection.execute(
                "UPDATE workflow_continuations SET state='consumed', attempt_id=?, turn_id=?, consumed_at=?"
                " WHERE continuation_id=?",
                (attempt_id, turn_id, now, continuation["continuation_id"]),
            )
        credential = self._issue_credential(connection, run_row, attempt_id, generation, turn_id)
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
        selection = that seal's identity), so a continuation never resumes on
        the original dirty baseline and the turn input is never an unresolved intent.
        """
        if turn_count == 0 or continuation is None or continuation["workspace_id"] is None:
            return manifest
        return resolve_manifest(self.board.directory, {"workspaceId":continuation["workspace_id"], "manifestSha256":continuation["workspace_manifest_sha256"]})

    def _latest_handoff(self, connection, run_row, manifest: dict) -> dict | None:
        """The newest fixed output produced on this run's physical checkout.

        workflow_artifacts is append-only (no delete/replace path); its SQLite rowid
        is publication order, independent of equal timestamps, wall-clock rollback
        and random artifact IDs. Check the output's actual checkout, including the
        parent's own older outputs after an allocation change.
        """
        if run_row['checkout'] == 'none':
            return None
        row = connection.execute(
            f"WITH manifest_owners AS ({MANIFEST_OWNERS_CTE})"
            " SELECT a.* FROM workflow_artifacts a"
            " WHERE a.kind IN ('output','resolved-output','partial-output')"
            " AND (a.run_id=? OR EXISTS (SELECT 1 FROM workflow_children c"
            " WHERE c.parent_run_id=? AND c.child_task_id=a.run_id))"
            " AND EXISTS (SELECT 1 FROM manifest_owners o"
            " JOIN workflow_runs owner_run ON owner_run.run_id=o.owner_run_id"
            " LEFT JOIN macro_tasks owner_macro ON owner_macro.macro_task_id=owner_run.macro_task_id"
            " WHERE o.workspace_id=a.output_workspace_id"
            " AND o.manifest_sha256=a.output_manifest_sha256"
            " AND o.checkout_root=?"
            " AND COALESCE(owner_run.standalone_project_id,owner_macro.project_id)=?)"
            " ORDER BY a.rowid DESC LIMIT 1",
            (run_row["run_id"], run_row["run_id"], manifest["checkoutRoot"], project_id(manifest)),
        ).fetchone()
        if row is None:
            return None
        sealed = self._artifact_document(row)
        return {**sealed, "artifactId": row["artifact_id"], "artifactKind": row["kind"]}

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

    def _continuation_intent(self, connection, run_row, continuation, *, recovered_attempt: str | None = None) -> tuple[dict | None, dict | None]:
        """The workspace intent and its already validated handoff record.

        A continuation keeps the *currently allocated physical checkout*: an isolated
        worktree stays the same worktree, so tasks.cwd, the write reservation and the
        adapter cwd never diverge. Only the logical baseline moves, to the newest fixed
        handoff produced on that checkout (the parent's own later seal, a Host
        resolution, or a helper's transferred output, whichever came last), and the
        current authorized scope version supplies the write scope of the new stage.
        """
        manifest = self._run_manifest(connection, run_row)
        scope = self._current_scope(connection, run_row)
        if recovered_attempt is not None:
            conflict = self._open_conflict(connection, run_row["run_id"])
            if conflict is not None:
                raise BoardError(
                    "WORKSPACE_SCOPE_CONFLICT",
                    "The failed attempt left changes outside its authorized write scope; restore, adopt or abandon "
                    "the recorded conflict before continuing",
                    conflictId=conflict["conflict_id"],
                    paths=json.loads(conflict["evidence_json"])["blockingPaths"],
                )
        sealed = self._latest_handoff(connection, run_row, manifest)
        if recovered_attempt is not None:
            # A failed site without a resolution is captured explicitly from the
            # stopped worktree; its out-of-scope paths were already rejected above.
            sealed = None
        choice = json.loads(run_row["execution_workspace_json"])
        changed_access = choice["access"] != manifest["access"]
        if sealed is None and recovered_attempt is None and not changed_access:
            return None, None
        # A permission-only continuation before any seal keeps the original
        # explicit untracked input selectors when it re-prepares the allocation.
        selection = ({"kind": "recovery"} if recovered_attempt is not None else
                     {"kind": "seal", "seal": {"artifactId": sealed["artifactId"], "snapshotSha256": sealed["snapshotSha256"]}}
                     if sealed is not None else
                     workspace_module().execution_selection(manifest) or {"kind": "selectors", "selectors": []})
        return {
            # Reuse the allocated checkout instead of minting a new worktree.
            "kind": "existing",
            "cwd": manifest["path"],
            "access": choice["access"],
            **({"accessNote": choice["accessNote"]} if choice["access"] == "full" else {}),
            "base": {"kind": "working-tree", **({"ref": sealed["commit"]} if sealed is not None else {})},
            "executionSelection": selection,
            "integrator": self._owner_actor(connection, run_row),
            "writeScope": [] if choice["access"] == "read" else scope["writeScope"],
        }, sealed

    def prepare_continuation_workspace(self, params: dict) -> None:
        """Resolve a pending continuation's workspace outside every transaction.

        Called by the service before execution admission so the exact canonical turn
        input already contains the resolved manifest; preparation is idempotent by
        the continuation identity, so concurrent workers cannot double-prepare.
        """
        def snapshot(connection, continuation_id):
            row = connection.execute(
                "SELECT * FROM workflow_continuations WHERE continuation_id=? AND state='queued'"
                " AND workspace_id IS NULL AND checkout!='none'", (continuation_id,),
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
            previous = self._run_manifest(connection, run)
            checkout_root = previous["checkoutRoot"]
            reservations = connection.execute(
                "SELECT * FROM workspace_reservations WHERE checkout_root=? AND state='held' ORDER BY reservation_id",
                (checkout_root,),
            ).fetchall()
            own = [item for item in reservations if item["holder_task_id"] == row["run_id"]]
            owned_record = connection.execute(
                "SELECT * FROM workspace_reservations WHERE checkout_root=?"
                " AND holder_task_id=? ORDER BY rowid DESC LIMIT 1",
                (checkout_root, row["run_id"]),
            ).fetchone()
            if not own and owned_record is not None and owned_record["state"] == "transferred":
                # An authorized helper will return this ownership when it stops.
                return None
            ownership_problem = None
            if len(own) != 1 or any(
                item["holder_task_id"] != row["run_id"]
                and (item["access"] == "write" or json.loads(run["execution_workspace_json"])["access"] != "read") for item in reservations
            ):
                ownership_problem = {"code": "PREPARATION_CONFLICT", "message": "The continuation checkout is no longer exclusively owned; explicitly continue after its owner releases it"}
            elif (own[0]["access"] != ("read" if previous["access"] == "read" else "write")
                  or own[0]["checkout_root"] != previous["checkoutRoot"]
                  or task["cwd"] != previous["path"]):
                ownership_problem = {"code": "PREPARATION_CONFLICT", "message": "The continuation reservation no longer matches its recorded checkout allocation"}
            task_fence = tuple(task[key] for key in ("revision", "state", "active_attempt_id", "selected_attempt_id", "cwd", "spec_json"))
            return dict(row), dict(run), task_fence, [dict(item) for item in reservations], dict(owned_record) if owned_record else None, ownership_problem

        selector = params.get("taskId") or params.get("runId")
        with self.db.read() as connection:
            rows = connection.execute(
                "SELECT c.continuation_id FROM workflow_continuations c JOIN tasks t ON t.task_id=c.run_id"
                " WHERE c.state='queued' AND c.workspace_id IS NULL AND t.state='queued'"
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
                previous = self._run_manifest(connection, run)
                problem = None
                intent = None
                handoff = None
                recovered_attempt = None
                try:
                    recovered_attempt = self._failed_turn_input(connection, run, row)
                    intent, output = self._continuation_intent(connection, run, row, recovered_attempt=recovered_attempt)
                    if intent is not None and intent["executionSelection"]["kind"] == "seal":
                        seal_ref = intent["executionSelection"]["seal"]
                        artifact = connection.execute("SELECT * FROM workflow_artifacts WHERE artifact_id=?",
                                                      (seal_ref["artifactId"],)).fetchone()
                        if (output["taskId"] != artifact["run_id"] or output["attemptId"] != artifact["attempt_id"]
                                or output["snapshotSha256"] != seal_ref["snapshotSha256"]
                                or (output["workspaceId"], output["manifestSha256"]) != (artifact["output_workspace_id"], artifact["output_manifest_sha256"])
                                or output["ref"] != output_references(artifact)[0]):
                            raise BoardError("WORKSPACE_MANIFEST_CHANGED", "The continuation seal does not bind its task, attempt and input allocation")
                        # A Host seal may bind an unused prepared continuation;
                        # its exact input belongs to the owner of the output pair.
                        final_input = owned_manifest(self.board.directory, connection,
                                                     artifact["output_workspace_id"], artifact["output_manifest_sha256"])
                        handoff = final_input, output
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
                    if intent is None:
                        manifest = previous
                        replayed = True
                    else:
                        self._cleanup_fence(intent.get("cwd"))
                        preparation = {}
                        manifest = workspace_module().prepare(
                            self.board.directory, f"{row['run_id']}:{row['continuation_id']}", intent,
                            _preparation=preparation, before_prepare=self._prepare_allocation,
                            **({"handoff": handoff} if handoff is not None else {}))
                        replayed = preparation["replayed"]
                    if (manifest["checkoutRoot"] != previous["checkoutRoot"]
                            or project_id(manifest) != project_id(previous)
                            or manifest["path"] != previous["path"]):
                        raise BoardError("PREPARATION_CONFLICT", "The prepared continuation changed its allocated checkout")
                    with self.db.read() as connection:
                        if snapshot(connection, row["continuation_id"]) != before:
                            continue
                    if replayed:
                        # A replay's on-disk input needs a fresh execution proof;
                        # a new preparation has just observed this same checkout.
                        workspace_module().verify(manifest, state_dir=self.board.directory, require_unchanged=True)
                except (BoardError, OSError) as error:
                    if isinstance(error, BoardError) and error.code in {"WORKSPACE_BUSY", "STORAGE_BUSY", "WORKSPACE_GIT_UNKNOWN"}:
                        # Another worker is preparing the same continuation. Leave it
                        # queued so that worker can publish, or a later claim can recover
                        # its durable files. An unfinished Git observation is also retryable.
                        continue
                    problem = {"code": getattr(error, "code", "WORKSPACE_PREPARE_FAILED"), "message": _head(str(error), 2000),
                               **({"details": error.details} if isinstance(error, BoardError) and error.details else {})}
                    if problem["code"] == "WORKSPACE_BASE_MISMATCH":
                        details = dict(problem["details"])
                        details["handoffCommit"] = details.pop("baseCommit")
                        problem.update(
                            message="The checkout changed after the previous turn was sealed; cancel and conclude this micro task "
                                    "and start a new one from the current checkout state. Compare the working tree with the "
                                    "handoff commit in Git for the full diff",
                            details=details,
                        )
            with self.db.write() as connection:
                if snapshot(connection, row["continuation_id"]) != before:
                    # Cancellation, override, allocation or ownership drift
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
                    role, workspace_kind, root, repository, input_commit, staged_commit = workspace_facts(connection, manifest)
                    connection.execute(
                        'UPDATE workflow_continuations SET workspace_id=?, workspace_manifest_sha256=?, workspace_facts_role=?, workspace_kind=?, checkout_root=?, repository_path=?, input_commit=?, staged_commit=? WHERE continuation_id=?',
                        (manifest["workspaceId"],manifest["manifestSha256"],role,workspace_kind,root,repository,input_commit,staged_commit,row["continuation_id"]))
                    connection.execute(
                        'UPDATE workflow_runs SET workspace_id=?, workspace_manifest_sha256=?, updated_at=? WHERE run_id=?',
                        (manifest["workspaceId"],manifest["manifestSha256"], now, row["run_id"]),
                    )
                    connection.execute(
                        "UPDATE workspace_reservations SET workspace_id=?, manifest_sha256=?, access=?"
                        " WHERE holder_task_id=? AND checkout_root=? AND state='held'",
                        (manifest["workspaceId"], manifest["manifestSha256"], "read" if manifest["access"] == "read" else "write", row["run_id"], manifest["checkoutRoot"]),
                    )
                    kind = "workflow.workspace_prepared"
                    event = {"continuationId": row["continuation_id"], "manifestSha256": manifest["manifestSha256"], "checkoutRoot": manifest["checkoutRoot"]}
                    if recovered_attempt is not None:
                        event["recoveredAttemptId"] = recovered_attempt
                self.board._append_event(connection, kind, task_id=row["run_id"], revision=None, payload=event)
                if problem is None:
                    if row["buddy_selection_json"] is not None and json.loads(row["buddy_selection_json"]) == "route":
                        self._start_routing(connection, row["run_id"], now)
                head = self.board._head_of(connection)
            self.board._notify(head)

    def _issue_credential(self, connection, run_row, attempt_id, generation, turn_id) -> str:
        token = self.db.agent_token(run_row["run_id"], attempt_id, generation)
        connection.execute(
            "UPDATE agent_credentials SET state='revoked'"
            " WHERE attempt_id=? AND state='active'",
            (attempt_id,),
        )
        connection.execute(
            "INSERT INTO agent_credentials(credential_id, run_id, attempt_id, turn_id,"
            " token_verifier, state) VALUES(?,?,?,?,?,'active')",
            (
                f"cred-{attempt_id}",
                run_row["run_id"],
                attempt_id,
                turn_id,
                self.db.agent_token_verifier(token),
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
                connection, "workflow.owned_stop_reported", task_id=owned["run_id"], revision=None,
                payload={"reportedTaskId": task_id, "sourceEventSeq": seq,
                         "stopConfirmed": summary["selfConfirmed"] and summary["descendantsConfirmed"]},
            )

    def _wake_lead(self, connection, lead, seq: int, now: str) -> None:
        event = connection.execute("SELECT * FROM events WHERE seq=?", (seq,)).fetchone()
        owned = self._run_row(connection, event["task_id"])
        trigger = {"seq": seq, "kind": event["kind"], "runId": owned["run_id"], "state": owned["state"]}
        continuation_id = str(uuid.uuid4())
        text = ("An owned micro task reached a boundary. Review the changes in ownedMicroTasks since the previous frozen input; "
                "use the launcher with this turn's credential file to list owned tasks and get their current evidence.")
        connection.execute(
            "INSERT INTO workflow_continuations(checkout,workspace_facts_role, continuation_id, run_id, kind, trigger_event_json, input_text, note, state, created_at) VALUES(?,?,?,?,'lead-wakeup',?,?,?,'recorded',?)",
            (lead["checkout"],"none" if lead["checkout"]=="none" else "pending",continuation_id,lead["run_id"],canonical_json(trigger),text,None,now,),
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
                                 revision=None,
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
        previous_cursor = _turn_input_context(previous["input_json"])["ownedFactsCursor"] if previous else None
        total = connection.execute("SELECT COUNT(*) FROM workflow_lead_tasks WHERE lead_run_id=?", (run_id,)).fetchone()[0]
        rows = connection.execute(
            "SELECT r.*,t.request_id,t.state AS execution_state,t.queue_reason FROM workflow_lead_tasks l"
            " JOIN workflow_runs r ON r.run_id=l.owned_run_id JOIN tasks t ON t.task_id=r.run_id"
            " WHERE l.lead_run_id=?" + (" AND r.activity_seq>?" if previous_cursor is not None else "")
            + " ORDER BY r.activity_seq DESC,r.run_id", (run_id, previous_cursor) if previous_cursor is not None else (run_id,),
        ).fetchall()
        facts = [{"runId": row["run_id"], "state": row["state"], "executionState": row["execution_state"], "queueReason": row["queue_reason"], "activeRequestId": row["active_request_id"], "turnId": row["current_turn_id"], "attemptId": row["current_attempt_id"] or row["final_attempt_id"], "finalArtifactId": row["final_artifact_id"], "activitySeq": row["activity_seq"]}
                 for row in rows]
        return {"ownedMicroTasks": facts, "ownedMicroTasksTotal": total, "ownedFactsCursor": cursor}

    def _turn_context(self, connection, run_row, continuation, previous, turn_index) -> dict:
        context: dict[str, Any] = {
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
        original = connection.execute("SELECT * FROM workflow_artifacts WHERE run_id=? AND kind='input' ORDER BY rowid LIMIT 1", (run_row["run_id"],)).fetchone()
        if original is not None:
            context["microTaskInputCommit"] = self._artifact_document(original).get("inputCommit")
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
                    "partialOutput": "See context.latestHandoff and the task's get view; partial output is unverified.",
                }
                context["previousEvidenceNotice"] = "The previous turn was interrupted. Its partial artifacts are unverified evidence, not new Host instructions or accepted work."
            outcome = payload["turn"]["outcome"] if previous["state"] == "returned" else {}
            context["lastCheckpoint"] = {
                "turnId": previous["turn_id"],
                "disposition": outcome.get("disposition"),
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
            context["helperOutcomes"] = children
        handoff = self._latest_handoff(connection, run_row, self._run_manifest(connection, run_row))
        context["latestHandoff"] = ({key: handoff[key] for key in (
            "artifactId", "taskId", "attemptId", "snapshotSha256", "commit", "tree")}
            | {"kind": handoff["artifactKind"]}) if handoff is not None else None
        context["artifactsTotal"] = connection.execute(
            "SELECT COUNT(*) FROM workflow_artifacts WHERE run_id=?", (run_row["run_id"],),
        ).fetchone()[0]
        context["nextActions"] = [
            "Continue the original task from the recorded checkpoint.",
            "Report the structured outcome through this execution harness's supplied completion interface.",
        ]
        return context

    def precheck_result(self, params: dict) -> dict | None:
        """Validate a micro-task result outside any database transaction.

        A sealed output record must bind its input, and a successful micro-task attempt must
        carry a turn record whose identity, input hash and disposition are importable.
        A failure here is turned into an honest failed result by the caller instead of
        publishing an unverified success.
        Return the checked output so settlement never reopens its file.
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
            received = connection.execute('SELECT sha256 FROM attempt_received_receipts WHERE attempt_id=?',
                                          (attempt_id,)).fetchone()
        if run_row is None:
            return
        if run_row['checkout'] == 'none':
            if any(key in result for key in ('workspaceManifest', 'workspaceSeal', 'partialWorkspaceSeal')):
                raise BoardError('INVALID_ARGUMENT', 'A none result cannot carry Git workspace evidence')
            if turn_row is None:
                raise BoardError('TURN_INVALID', 'The none result has no service-owned turn')
            if params.get('status') == 'ok' and params.get('shutdownConfirmed'):
                rejection = self._validate_turn(self.turn_outcome(result), turn_row, attempt)
                if rejection is not None:
                    raise BoardError('TURN_INVALID', rejection, turnId=turn_row['turn_id'])
            if received is None:
                # Service-generated interruption facts have no execution receipt.
                return None
            from .interruption import attempt_private_adapter
            from ...protocol.execution import RECEIPT
            directory = private_dirs.attempt_root(self.board.directory, attempt_private_adapter(attempt),
                                                  attempt['task_id'], attempt_id)
            return {'path': str((directory / RECEIPT).relative_to(self.board.directory)), 'sha256': received['sha256']}
        if "workspaceManifest" in result:
            raise BoardError("INVALID_ARGUMENT", "Current execution reports do not carry workspaceManifest")
        effective = turn_manifest(self.board.directory, json.loads(turn_row["input_json"])) if turn_row is not None else None
        partial_present = "partialWorkspaceSeal" in result
        if partial_present and "workspaceSeal" in result:
            raise BoardError("INVALID_ARGUMENT", "An execution report has one output reference form")
        seal_ref = result["partialWorkspaceSeal"] if partial_present else result.get("workspaceSeal")
        seal = (resolve_record(self.board.directory, seal_ref)
                if partial_present or "workspaceSeal" in result else None)
        partial = seal if partial_present else None
        if isinstance(seal, dict):
            if effective is None:
                raise BoardError("TURN_INVALID", "The sealed output has no service-owned input turn")
            module = workspace_module()
            if seal.get("version") == 2 and seal.get("kind") in (None, "host-output"):
                module.verify_sealed_output(effective, effective, seal)
            else:
                module._validate_output_record(effective, seal)
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
        if (params.get("status") != "ok" and params.get("terminationReason") != "wrap-up"
                or not params.get("shutdownConfirmed")):
            return seal
        if turn_row is None:
            raise BoardError("TURN_INVALID", "the micro-task attempt has no service-owned turn to import")
        rejection = self._validate_turn(self.turn_outcome(result), turn_row, attempt)
        if rejection is not None:
            raise BoardError("TURN_INVALID", rejection, turnId=turn_row["turn_id"])
        return seal

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

    def _overloaded_model_services(self, connection, run_id):
        """Task-local provider rejections since the last effective turn, newest evidence first.

        Consecutive pre-model rejections belong to the same routing recovery;
        keep their exclusions so two overloaded services cannot route in a loop.
        A later effective turn ends that recovery, even when it failed otherwise.
        Its own overload still supplies the next manual reroute's evidence.
        """
        from ..evaluation.native_observations import provider_rejection_view
        excluded = {}
        for turn in connection.execute(
                "SELECT t.turn_id,t.state,a.model_harness,a.model_provider,a.model_model,a.result_json"
                " FROM workflow_turns t JOIN attempts a ON a.attempt_id=t.attempt_id"
                " WHERE t.run_id=? ORDER BY t.turn_index DESC", (run_id,)):
            recorded = self.result_payload(json.loads(turn["result_json"]) if turn["result_json"] else None)
            failure = provider_rejection_view(recorded) if recorded.get("code") != INPUT_REJECTED else None
            if failure is not None and failure["code"] == "overloaded":
                identity = (turn["model_harness"], turn["model_provider"], turn["model_model"])
                excluded.setdefault(identity, dict(zip(("harness", "provider", "model"), identity)) | {
                    "nativeCode": failure["nativeCode"], "turnId": turn["turn_id"]})
            if turn["state"] != "rejected":
                break
        return list(excluded.values())

    def _provider_rejection_reroutable(self, connection, run, turn, attempt, payload):
        result = self.result_payload(payload)
        if result.get("code") == INPUT_REJECTED:
            return False
        failure = result.get("providerRejection")
        routing = json.loads(turn["input_json"])["context"]["routing"]
        if (not failure or failure["code"] not in ("quota-exceeded", "overloaded") or failure.get("rejectionPhase") != "before-model"
                or not attempt["shutdown_confirmed"] or attempt["cancel_requested_at"] is not None
                or run["state"] != "executing"
                or run["current_turn_id"] != turn["turn_id"] or run["current_attempt_id"] != attempt["attempt_id"]
                or routing["decisionId"] is None or routing["decisionId"] != run["current_routing_id"]
                or routing["buddyRevision"] != run["buddy_revision"]):
            return False
        route = connection.execute("SELECT * FROM workflow_routes WHERE decision_id=?",
                                   (routing["decisionId"],)).fetchone()
        return route["state"] == "resolved"

    def settle_turn(
        self, connection, *, task, attempt, payload: dict | None, task_state: str, now: str,
        seal_stash: dict | None = None, output_record: dict | None = None,
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
        accumulate_infrastructure(
            connection, turn_buddy(turn_row["input_json"]),
            json.loads(attempt["result_json"]) if attempt["result_json"] is not None else None,
            attempt["finished_at"],
            new_round=turn_row["state"] in ("prepared", "running"),
        )
        # The seal-side shared-stash fact is pinned for every outcome of a turn
        # that froze a start observation — completed, failed, cancelled or late.
        # It is evidence only and never changes this import's state machine.
        shared_refs = self._pin_shared_refs(connection, run_row, attempt, turn_row, seal_stash, now)
        if run_row['checkout'] == 'none' and output_record is not None:
            self._pin_result_artifacts(connection, run_row, attempt, payload, turn_row['turn_id'], now, output_record)
        # Recovery may seal a stopped checkout after rejecting its receipt at the boundary.
        if (attempt["shutdown_confirmed"] and self.result_payload(payload).get("partialWorkspaceSeal")
                and not self.result_payload(payload).get("microTaskError")
                and not self.result_payload(payload).get("receiptRejection")):
            self._pin_result_artifacts(connection, run_row, attempt, payload, turn_row["turn_id"], now, output_record)
            self.board._append_event(connection, "workflow.partial_output_preserved", task_id=run_row["run_id"], attempt_id=attempt["attempt_id"], payload={"partial": True, "verified": False, "final": False})
        ancestor = self._terminal_ancestor(connection, run_row["run_id"])
        if ancestor is not None and run_row["state"] not in ("accepted", "concluded", "cancelled"):
            self._cancel_owned_run(connection, task, now)
            run_row = self._run_row(connection, run_row["run_id"])
        if run_row["state"] in ("accepted", "concluded", "cancelled") or ancestor is not None:
            late = self._late_cancelled_turn(connection, run_row, turn_row, attempt, payload, now, output_record)
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
            self._revoke_credentials(connection, run_row["run_id"])
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
            input_failure = input_failure_view(payload)
            if (task_state == "failed" and attempt["shutdown_confirmed"]
                    and self.result_payload(payload).get("code") == INPUT_REJECTED):
                request_id = self._record_system_request(
                    connection, run_row,
                    make_reason("workflow.input-rejected", harness=input_failure["harness"]["name"]),
                    {"inputFailure": input_failure}, now, turn_row=turn_row,
                )
                self.board._append_event(
                    connection, "workflow.turn_failed", task_id=run_row["run_id"],
                    attempt_id=attempt["attempt_id"], revision=None,
                    payload={"turnId": turn_row["turn_id"], "state": "awaiting-host", "requestId": request_id,
                             "reason": "workflow.input-rejected"},
                )
                return {"accepted": False, "disposition": None, "state": "awaiting-host",
                        "requestId": request_id, "inputFailure": input_failure,
                        "sharedStash": self._shared_refs_summary(shared_refs)}
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
                                         attempt_id=attempt["attempt_id"], revision=None,
                                         payload={**facts, "code": "native-resume-unavailable",
                                                  "modelStarted": False, "shutdownConfirmed": True})
                self.board._append_event(connection, "workflow.auto_continued", task_id=run_row["run_id"],
                                         attempt_id=attempt["attempt_id"], revision=None,
                                         payload=facts)
                return {"accepted": False, "disposition": None, "state": "executing", "autoContinued": True,
                        "sharedStash": self._shared_refs_summary(shared_refs)}
            if task_state == "failed" and self._provider_rejection_reroutable(connection, run_row, turn_row, attempt, payload):
                continuation = connection.execute(
                    "SELECT continuation_id FROM workflow_continuations WHERE turn_id=? AND state='consumed'",
                    (turn_row["turn_id"],),
                ).fetchone()
                connection.execute("UPDATE workflow_turns SET state='rejected',updated_at=? WHERE turn_id=?",
                                   (now, turn_row["turn_id"]))
                if continuation is not None:
                    connection.execute(
                        "UPDATE workflow_continuations SET state='queued', attempt_id=NULL, turn_id=NULL, consumed_at=NULL WHERE continuation_id=?", (continuation["continuation_id"],))
                self.board._transition_task(connection, task, "queued")
                connection.execute(
                    'UPDATE workflow_runs SET updated_at=? WHERE run_id=?', (now, run_row["run_id"]))
                previous_decision_id = run_row["current_routing_id"]
                self._start_routing(connection, run_row["run_id"], now)
                refreshed = self._run_row(connection, run_row["run_id"])
                facts = {"turnId": turn_row["turn_id"], "reason": self.result_payload(payload)["providerRejection"]["code"],
                         "previousDecisionId": previous_decision_id, "decisionId": refreshed["current_routing_id"],
                         "continuationId": continuation["continuation_id"] if continuation is not None else None,
                         "autoContinued": True}
                self.board._append_event(connection, "workflow.auto_continued", task_id=run_row["run_id"],
                                         attempt_id=attempt["attempt_id"], revision=None, payload=facts)
                return {"accepted": False, "disposition": None, "state": refreshed["state"], "autoContinued": True,
                        "sharedStash": self._shared_refs_summary(shared_refs)}
            if terminal is not None:
                connection.execute(
                    'UPDATE workflow_runs SET state=?, updated_at=? WHERE run_id=?',
                    (terminal, now, run_row["run_id"]),
                )
            self.board._append_event(connection, "workflow.turn_failed", task_id=run_row["run_id"],
                                     attempt_id=attempt["attempt_id"], revision=None,
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
            self._revoke_credentials(connection, run_row["run_id"])
            connection.execute(
                "UPDATE workflow_runs SET state='failed', updated_at=? WHERE run_id=?",
                (now, run_row["run_id"]),
            )
            self.board._append_event(
                connection,
                "workflow.turn_rejected",
                task_id=run_row["run_id"],
                attempt_id=attempt["attempt_id"],
                revision=None,
                payload={"turnId": turn_row["turn_id"], "reason": rejection},
            )
            return {"accepted": False, "disposition": None, "state": "failed", "reason": rejection,
                    "sharedStash": self._shared_refs_summary(shared_refs)}
        outcome = turn["outcome"]
        disposition = outcome["disposition"]
        connection.execute(
            "UPDATE workflow_turns SET state='returned', updated_at=? WHERE turn_id=?",
            (now, turn_row["turn_id"]),
        )
        self._revoke_credentials(connection, run_row["run_id"])
        # Every accepted turn pins the manifest it executed on and the output it
        # sealed; a later continuation prepares from that sealed commit rather than
        # from the original baseline. Only a completed turn becomes the final artifact.
        artifact_id = self._pin_result_artifacts(
            connection, run_row, attempt, payload, turn_row["turn_id"], now, output_record
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
                    "UPDATE workflow_runs SET state='waiting-owned', current_attempt_id=NULL, updated_at=? WHERE run_id=?", (now, run_row["run_id"]),
                )
                connection.execute("UPDATE tasks SET queue_reason='awaiting-owned' WHERE task_id=?", (run_row["run_id"],))
                self.board._append_event(connection, "workflow.turn_returned", task_id=run_row["run_id"],
                                         attempt_id=attempt["attempt_id"], revision=None,
                                         payload={"turnId": turn_row["turn_id"], "disposition": disposition, "state": "waiting-owned"})
                changed = self._owned_since_input(connection, run_row["run_id"], turn_row)
                if changed is not None:
                    self._wake_lead(connection, self._run_row(connection, run_row["run_id"]), changed["seq"], now)
                return {"accepted": True, "disposition": disposition,
                        "state": self._run_row(connection, run_row["run_id"])["state"],
                        "sharedStash": self._shared_refs_summary(shared_refs)}
        if disposition == "completed":
            connection.execute(
                "UPDATE workflow_runs SET state='delivered', final_attempt_id=?, final_artifact_id=COALESCE(?, final_artifact_id), current_turn_id=?, current_attempt_id=NULL, updated_at=? WHERE run_id=?",
                (attempt["attempt_id"], artifact_id, turn_row["turn_id"], now, run_row["run_id"]),
            )
            self.board._append_event(
                connection,
                "workflow.turn_returned",
                task_id=run_row["run_id"],
                attempt_id=attempt["attempt_id"],
                revision=None,
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
            revision=None,
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
                                 attempt_id=attempt["attempt_id"], revision=None,
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
        if "outcomeTruncated" in turn:
            return "the turn record no longer accepts outcomeTruncated"
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
        """Persist the out-of-scope failure-site evidence of one stopped attempt.

        The record is the Host's handle on the preserved site: it names the exact
        observed fingerprint, blocking paths and per-path authorized/observed
        content. It never authorizes those changes and never rewrites the attempt's
        execution receipt.
        """
        if turn_row is None or run_row['checkout'] == 'none':
            return []
        module = workspace_module()
        document = json.loads(turn_row["input_json"])
        manifest = turn_manifest(self.board.directory, document)
        try:
            records = module.scope_conflicts(self.board.directory, manifest, run_row["run_id"], attempt["attempt_id"])
        except BoardError:
            return []
        recorded = []
        for record in records:
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
                " observed_fingerprint, evidence_json, state,"
                " resolved_paths_json, conflicting_paths_json, created_at, updated_at)"
                " VALUES(?,?,?,?,?,?,'open','[]','[]',?,?)",
                (
                    conflict_id,
                    run_row["run_id"],
                    attempt["attempt_id"],
                    turn_row["turn_id"],
                    fingerprint,
                    canonical_json(record),
                    now,
                    now,
                ),
            )
            recorded.append(conflict_id)
        return recorded

    def _record_scope_attention(self, connection, run_row, turn_row, attempt, conflict_ids, now) -> str:
        request_id = self._record_system_request(
            connection, run_row, make_reason("workspace-scope-violation", conflictIds=list(conflict_ids)),
            {}, now, turn_row=turn_row,
        )
        self.board._append_event(
            connection,
            "workflow.scope_conflict",
            task_id=run_row["run_id"],
            attempt_id=attempt["attempt_id"],
            revision=None,
            payload={"requestId": request_id, "conflictIds": list(conflict_ids)},
        )
        return request_id

    def _record_system_request(self, connection, run_row, reason: dict | None, payload: dict, now: str, *,
                               kind: str = "attention", turn_row=None, request_id: str | None = None,
                               decision_id: str | None = None) -> str:
        """Open one system boundary and propagate the same form to its owners."""
        request_id = request_id or f"req-{uuid.uuid4()}"
        connection.execute(
            "INSERT INTO workflow_requests(request_id, run_id, turn_id, attempt_id, kind, reason_code, reason_params_json, decision_id, payload_json, state, created_at, updated_at) VALUES(?,?,?,?,?,?,?,?,?,'open',?,?)",
            (request_id,run_row["run_id"],turn_row["turn_id"] if turn_row is not None else None,turn_row["attempt_id"] if turn_row is not None else None,kind,reason["code"] if reason is not None else None,canonical_json(reason["params"]) if reason is not None else None,decision_id,canonical_json(payload),now,now,),
        )
        if turn_row is not None:
            connection.execute('UPDATE workflow_runs SET current_turn_id=?, current_attempt_id=NULL WHERE run_id=?',
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
            "INSERT INTO workflow_requests(request_id, run_id, turn_id, attempt_id, kind, summary, payload_json, state, created_at, updated_at) VALUES(?,?,?,?,?,?,?,'open',?,?)",
            (request_id,run_row["run_id"],turn_row["turn_id"],turn_row["attempt_id"],disposition,payload["summary"],canonical_json(payload),now,now,),
        )
        connection.execute(
            "UPDATE workflow_runs SET state='awaiting-host', active_request_id=?, current_turn_id=?, current_attempt_id=NULL, updated_at=? WHERE run_id=?",
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


    def _pin_shared_refs(self, connection, run_row, attempt, turn_row, seal_stash, now: str) -> dict | None:
        """Freeze the seal-side shared-stash fact of one micro-task turn.

        The start observation is the turn input's own frozen record; the seal
        observation comes from the result path's outside-transaction collection
        and is bound to the same repository the turn froze, or stays an honest
        ``unknown`` when it was not collected for that repository. The pinned
        record carries a bounded comparison of the start and seal observations;
        unknown and truncated observations remain explicit. A shared project
        marker does not authorize an observation from another repository path. Its
        ``manifestSha256`` is computed from every payload field except the
        digest, and the executed workspace manifest keeps its own digest under
        ``workspaceManifestSha256``. It is evidence only: it restores nothing
        and does not judge checkout changes. Collection failures become unknown
        observations; malformed records and persistence failures propagate.
        Turns whose input predates the freezing have no start
        fact and pin nothing — a start observation is never invented from a
        later reading.
        """
        module = workspace_module()
        context = json.loads(turn_row["input_json"])["context"]
        if "sharedStash" not in context:
            return None
        frozen = context["sharedStash"]
        repository_path = frozen["repositoryPath"]
        start = frozen["start"]
        if (seal_stash is not None and seal_stash["repositoryPath"] == repository_path
                and "inventory" in seal_stash):
            seal = seal_stash["inventory"]
        elif seal_stash is not None and "failure" in seal_stash:
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
            "workspaceId": turn_row["workspace_id"],
            "repositoryPath": repository_path,
            "startStash": start,
            "sealStash": seal,
            "comparison": module.shared_stash_comparison(start, seal),
        }
        record["manifestSha256"] = self.shared_refs_record_digest(record)
        self._pin_artifact(
            connection,
            run_id=run_row["run_id"],
            kind=self.SHARED_REFS_KIND,
            manifest=record,
            attempt_id=attempt["attempt_id"],
            turn_id=turn_row["turn_id"],
            now=now,
        )
        return record

    @classmethod
    def _shared_refs_summary(cls, record: dict | None) -> dict | None:
        """The bounded Host projection of one pinned shared-stash record."""
        if record is None:
            return None
        comparison = record["comparison"]

        def side(name):
            value = record[name]
            entries = value["entries"]
            return {"state": value["state"], "reason": value.get("reason"),
                    "entryCount": len(entries) if entries is not None else None}

        lost = comparison.get("lostEntries", [])[:16]
        new = comparison.get("newEntries", [])[:16]
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

    def _sealed_artifacts(self, connection, *, run_id: str, turn_id: str) -> list[dict]:
        """Worker seals the service accepted for this turn, including partial work.

        Host conclusions and workspace resolutions are separately typed outputs;
        they do not change the turn's imported seal evidence.
        """
        artifacts = []
        for row in connection.execute(
            "SELECT * FROM workflow_artifacts WHERE run_id=? AND turn_id=?"
            " AND kind IN ('output','partial-output')"
            " AND output_origin='worker' ORDER BY rowid",
            (run_id, turn_id),
        ):
            seal = self._artifact_document(row)
            artifacts.append({
                "kind": "workspace-seal",
                # ``manifestSha256`` is the input digest the turn executed on;
                # ``snapshotSha256`` is the output identity that was pinned.
                "manifestSha256": seal.get("manifestSha256"),
                "snapshotSha256": seal.get("snapshotSha256"),
                "sealedEvidence": self._sealed_evidence(seal),
                "tree": seal.get("tree"),
                "changedPaths": seal.get("changedPaths", []),
                "outsideScope": seal.get("outsideScope", []),
            })
        return artifacts

    def _pin_result_artifacts(self, connection, run_row, attempt, payload, turn_id, now, output_record):
        """Pin the output record; the service turn already freezes the input."""
        if run_row['checkout'] == 'none':
            if output_record is None:
                return None
            previous = connection.execute("SELECT artifact_id FROM workflow_artifacts WHERE run_id=? AND attempt_id=? AND kind='result'",
                                          (run_row['run_id'], attempt['attempt_id'])).fetchone()
            if previous is not None:
                return previous['artifact_id']
            identifier = 'result-' + str(uuid.uuid4())
            connection.execute(
                "INSERT INTO workflow_artifacts(artifact_id,run_id,turn_id,attempt_id,generation,kind,content_form,record_path,record_sha256,created_at)"
                " VALUES(?,?,?,?,?,'result','result-reference',?,?,?)",
                (identifier, run_row['run_id'], turn_id, attempt['attempt_id'], attempt['generation'],
                 output_record['path'], output_record['sha256'], now))
            return identifier
        result = self.result_payload(payload)
        partial = result.get('partialWorkspaceSeal')
        reference = partial or result.get('workspaceSeal')
        if reference is None:
            return None
        archived = attempt['result_form'] == 'archived-report-v1'
        seal = reference if archived else output_record
        return self._pin_artifact(connection,run_id=run_row['run_id'],kind='partial-output' if partial else 'output',
                                  manifest=seal,attempt_id=attempt['attempt_id'],turn_id=turn_id,now=now,
                                  record_file=None if archived else reference['recordFile'])

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
            if run_row is not None and run_row["state"] == "accepted" and task["accepted_at"]:
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
            "UPDATE workflow_children SET state=?, updated_at=? WHERE child_task_id=?",
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
                    revision=None,
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
            while True:
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
                    "SELECT * FROM workspace_reservations WHERE holder_task_id=? AND checkout_root=?"
                    " AND state='transferred' ORDER BY created_at DESC, rowid DESC LIMIT 1",
                    (parent_id, reservation["checkout_root"]),
                ).fetchone()
                # A live descendant may still hold a checkout transferred through
                # several generations. Never reclaim it before that owner stops.
                if parent_reservation is not None and self._held_writer(connection, reservation["checkout_root"]) is None:
                    connection.execute(
                        "UPDATE workspace_reservations SET state='held', released_at=NULL WHERE reservation_id=?",
                        (parent_reservation["reservation_id"],),
                    )
                break

    # -- primitive boundary --------------------------------------------------
    def guard_task_control(self,connection,task_row,params,*,operation):
        if self._run_optional(connection,task_row["task_id"]) is not None:
            raise BoardError("MICRO_TASK_REQUIRED","Use cancel (workflow_cancel) for this micro task",runId=task_row["task_id"])


# -- module helpers ----------------------------------------------------------


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


def _preparation_details(value, *, diagnostic=False):
    """Redact process diagnostics while preserving structured checkout facts."""
    if isinstance(value, str):
        return _without_remote_user_info(value) if diagnostic else value
    if isinstance(value, list):
        return [_preparation_details(item, diagnostic=diagnostic) for item in value]
    if isinstance(value, dict):
        return {key: _preparation_details(item, diagnostic=key in ("argv", "stdout", "stderr", "reason"))
                for key, item in value.items()}
    return value


def _string_list(value) -> list[str]:
    """Normalize nonempty request text to the Host's string-list projection.

    The native runner contract allows ``neededWork`` to be one nonempty string, so a
    string is exposed as a one-item list; lists stay lists and anything else is
    dropped rather than guessed.
    """
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    if not isinstance(value, list):
        return []
    out: list[str] = []
    for entry in value:
        if isinstance(entry, str) and entry.strip():
            out.append(entry.strip())
    return out


__all__ = [
    "AGENT_OPERATIONS",
    "AGENT_RUN_BOUND_OPERATIONS",
    "WorkflowCoordinator",
    "clear_request_scope",
    "current_scope",
    "set_request_scope",
    "workspace_module",
]
