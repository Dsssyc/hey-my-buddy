"""Small workflow command receipts and their current authoritative projections.

The command ledger keeps the identity of a committed mutation. A replay reads
the current run view in the same transaction as the receipt lookup; historical
workflow projections are never kept in ``commands.response_json``.
"""
from __future__ import annotations

import json

from . import host_conclusions
from ..store.db import sha256_text


_CURRENT_VIEW_KINDS = frozenset({
    "workflow.decide", "workflow.continue", "workflow.takeover", "workflow.cancel",
    "workflow.accept", "workflow.conclude", "workflow.scope_amend",
    "workflow.workspace_resolve", "workflow.cleanup_plan", "workflow.cleanup_apply",
})

def stored_response(kind: str, response: dict) -> dict:
    """Select committed identities by kind.

    Dispatch uses the command kind, never a field heuristic about a historical
    response. Kinds with small answers retain their exact response.
    """
    if kind not in _CURRENT_VIEW_KINDS:
        return response
    stored = {"runId": response["runId"]}
    if kind == "workflow.decide":
        stored["requestId"] = response["requestId"]
    elif kind == "workflow.continue":
        stored["continuationId"] = response["continuationId"]
    elif kind == "workflow.takeover":
        stored.update(ownerGeneration=response["ownerGeneration"],
                      previousOwnerGeneration=response["previousOwnerGeneration"],
                      control={"hostId": response["control"]["hostId"]})
    elif kind == "workflow.accept":
        stored["integrationId"] = response["integrationId"]
        if "resolutionDelivery" in response:
            stored["resolutionConflictId"] = response["resolutionDelivery"]["conflictId"]
    elif kind == "workflow.conclude":
        stored["conclusionId"] = response["conclusion"]["conclusionId"]
    elif kind == "workflow.scope_amend":
        stored.update(targetRunId=response["targetRunId"], scopeVersion=response["scopeVersion"])
    elif kind == "workflow.workspace_resolve":
        # A partial restore leaves the conflict open. A later resolution updates
        # that same row, so the first operation's outcome cannot be recovered
        # from the latest conflict row. These are bounded operation facts only.
        stored.update(targetRunId=response["targetRunId"], conflictId=response["conflictId"],
                      action=response["action"],
                      resolutionState=response["resolutionState"],
                      resolvedPaths=response["resolvedPaths"],
                      preservedPaths=response["preservedPaths"],
                      remainingPaths=response["remainingPaths"],
                      observedFingerprint=response["observedFingerprint"],
                      resolvedFingerprint=response["resolvedFingerprint"],
                      artifactId=response["artifactId"],
                      nextBoundaryRequestId=response["nextBoundaryRequestId"],
                      delivered=response["delivered"])
    elif kind in ("workflow.cleanup_plan", "workflow.cleanup_apply"):
        stored.update(targetRunId=response["targetRunId"], planId=response["plan"]["planId"])
    return stored


def replay_response(coordinator, connection, kind: str, stored: dict) -> dict:
    """Rebuild a workflow answer from its committed identity and current tables."""
    if kind not in _CURRENT_VIEW_KINDS:
        return stored
    run = coordinator._run_row(connection, stored["runId"])
    result = {**coordinator.compact(connection, run), "duplicate": False}

    if kind == "workflow.decide":
        request = coordinator._request_row(connection, stored["runId"], stored["requestId"])
        result.update(decision=json.loads(request["decision_json"])["decision"],
                      requestId=stored["requestId"])
    elif kind == "workflow.continue":
        continuation = connection.execute(
            "SELECT * FROM workflow_continuations WHERE continuation_id=?",
            (stored["continuationId"],),
        ).fetchone()
        result.update(continuationId=stored["continuationId"],
                      inputSha256=sha256_text(continuation["input_text"]),
                      helperPolicy=continuation["helper_policy"],
                      targetRunId=continuation["run_id"])
    elif kind == "workflow.takeover":
        generation = stored["ownerGeneration"]
        control = stored["control"]
        result.update(ownerGeneration=generation,
                      previousOwnerGeneration=stored["previousOwnerGeneration"],
                      control={"hostId": control["hostId"], "ownerGeneration": generation,
                               "controlToken": coordinator.db.control_token(stored["runId"], generation)})
    elif kind == "workflow.cancel":
        result.update(cancelled=True)
    elif kind == "workflow.accept":
        integration = connection.execute(
            "SELECT * FROM workflow_integrations WHERE integration_id=?",
            (stored["integrationId"],),
        ).fetchone()
        result.update(verdict="accepted", targetRunId=stored["runId"],
                      targetRevision=run["revision"], integrationId=stored["integrationId"],
                      integration=coordinator._integration_view(integration))
        if "resolutionConflictId" in stored:
            conflict = connection.execute(
                "SELECT * FROM workflow_workspace_conflicts WHERE conflict_id=?",
                (stored["resolutionConflictId"],),
            ).fetchone()
            result["resolutionDelivery"] = coordinator._resolution_delivery_result({
                "conflict": conflict, "delivery": coordinator._resolution_delivery_view(conflict),
            })
    elif kind == "workflow.conclude":
        conclusion = connection.execute(
            "SELECT * FROM workflow_host_conclusions WHERE conclusion_id=?",
            (stored["conclusionId"],),
        ).fetchone()
        result.update(verdict="concluded", conclusion=host_conclusions.view(conclusion),
                      partialArtifactId=conclusion["artifact_id"],
                      targetRunId=stored["runId"], targetRevision=run["revision"])
    elif kind == "workflow.scope_amend":
        target_id = stored["targetRunId"]
        scope = connection.execute(
            "SELECT * FROM workflow_scope_versions WHERE run_id=? AND scope_version=?",
            (target_id, stored["scopeVersion"]),
        ).fetchone()
        result.update(previousScopeVersion=scope["scope_version"] - 1,
                      scopeVersion=scope["scope_version"],
                      writeScope=json.loads(scope["write_scope_json"]),
                      inputManifestSha256=scope["manifest_sha256"],
                      targetRunId=target_id, targetRevision=run["revision"])
    elif kind == "workflow.workspace_resolve":
        artifact_id = stored["artifactId"]
        conflict = connection.execute(
            "SELECT * FROM workflow_workspace_conflicts WHERE conflict_id=?",
            (stored["conflictId"],),
        ).fetchone()
        artifact = connection.execute(
            "SELECT * FROM workflow_artifacts WHERE artifact_id=?", (artifact_id,)
        ).fetchone() if artifact_id else None
        manifest = json.loads(artifact["manifest_json"]) if artifact is not None else {}
        delivery = coordinator._resolution_delivery_view(conflict) if stored["delivered"] else None
        result.update(
            conflictId=stored["conflictId"], action=stored["action"],
            resolved=stored["resolutionState"] in ("restored", "adopted", "abandoned"),
            resolutionState=stored["resolutionState"],
            **coordinator._bounded_path_fields({key: stored[key] for key in
                                               ("resolvedPaths", "preservedPaths", "remainingPaths")},
                                              ("resolvedPaths", "preservedPaths", "remainingPaths")),
            observedFingerprint=stored["observedFingerprint"],
            resolvedFingerprint=stored["resolvedFingerprint"],
            artifactId=artifact_id,
            artifact=coordinator._artifact_view(artifact) if artifact is not None else None,
            outputCommit=manifest.get("commit"), outputTree=manifest.get("tree"),
            diffPath=manifest.get("diffPath"), diffSha256=manifest.get("diffSha256"),
            snapshotSha256=manifest.get("snapshotSha256"),
            manifestSha256=manifest.get("manifestSha256"),
            baseCommit=manifest.get("baseCommit"), inputCommit=manifest.get("inputCommit"),
            **coordinator._bounded_path_fields({"changedPaths": manifest.get("changedPaths") or [],
                                               "adoptedPaths": manifest.get("adoptedPaths") or []},
                                              ("changedPaths", "adoptedPaths")),
            delivered=stored["delivered"], delivery=delivery,
            nextBoundaryRequestId=stored["nextBoundaryRequestId"],
            targetRunId=stored["targetRunId"], targetRevision=run["revision"],
        )
    elif kind in ("workflow.cleanup_plan", "workflow.cleanup_apply"):
        plan = connection.execute(
            "SELECT * FROM workspace_cleanup_plans WHERE plan_id=?", (stored["planId"],)
        ).fetchone()
        result.update(plan=coordinator._plan_view(plan),
                      targetRunId=stored["targetRunId"], targetRevision=run["revision"])
        if kind == "workflow.cleanup_apply":
            result.update(removed=True, retention=json.loads(plan["result_json"])["retention"])
    return result
