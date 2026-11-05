"""Bounded, read-only Host wait over private service observation operations."""
from __future__ import annotations

from pathlib import Path
import threading
import time
import uuid
from typing import Any, Callable

from ..protocol.requests import AwaitRequest, parse_request
from ..protocol.transport import ServiceError, _call_service

WAIT_SLICE_MS = 30000
UPGRADE_HANDOVER_POLL_SECONDS = 0.2
TERMINAL_STATUSES = frozenset({"completed", "concluded", "failed", "cancelled", "reconciliation-needed"})


class WaitAbandoned(RuntimeError):
    """The client stopped waiting; the durable micro task is untouched."""

    def __init__(self, run_id: str):
        super().__init__("The blocking wait was abandoned; the durable run was not cancelled")
        self.run_id = run_id


def _shutdown_confirmed(run: dict) -> bool:
    evidence = run.get("workflowShutdown")
    return (evidence.get("selfConfirmed") is True and evidence.get("descendantsConfirmed") is True
            if isinstance(evidence, dict) else run.get("shutdownConfirmed") is True)


def _actions(run: dict) -> list[str]:
    actions = run.get("actions")
    if actions is None and isinstance(run.get("workflow"), dict):
        actions = run["workflow"].get("actions")
    if not isinstance(actions, list) or not all(isinstance(action, str) for action in actions):
        raise ServiceError("INVALID_RESPONSE", "The service omitted the run's current actions")
    return actions


def _view(run: dict, *, outcome: str, started: float, wait_seconds: int,
          clock: Callable[[], float], result: Any = None, error: dict | None = None,
          reminder: dict | None = None) -> dict:
    workflow = run.get("workflow") or {}
    request = run.get("activeRequest") or {}
    request_id = request.get("requestId") or workflow.get("activeRequestId")
    artifact_id = run.get("artifactId") or run.get("finalArtifactId") or workflow.get("finalArtifactId")
    if artifact_id is None and isinstance(result, dict):
        artifact_id = result.get("artifactId") or result.get("finalArtifactId")
    view = {
        "runId": run.get("runId"),
        "status": run.get("state"),
        "workflowState": run.get("workflowState"),
        "outcome": outcome,
        "actions": _actions(run),
        "waitedSeconds": round(max(clock() - started, 0.0), 1),
        "waitSeconds": wait_seconds,
        "timedOut": outcome == "wait-timeout",
        "shutdownConfirmed": _shutdown_confirmed(run),
    }
    if request_id and run.get("workflowState") == "awaiting-host":
        view["requestId"] = request_id
        view["requestKind"] = request.get("kind") or workflow.get("requestKind")
    if artifact_id:
        view["artifactId"] = artifact_id
    if run.get("workflowShutdown") is not None:
        view["shutdown"] = run["workflowShutdown"]
    for key in ("executionIssues", "serviceIssues", "queueReason", "routing", "reminder", "inputFailure",
                'checkout', 'unsealedChanges', 'folderChangesNotice'):
        if key in run:
            view[key] = run[key]
    selected = run.get("selectedAttempt")
    if isinstance(selected, dict) and "issues" in selected:
        view["selectedAttempt"] = {"issues": selected["issues"]}
    if result is not None:
        view["result"] = result
    if error is not None:
        view["error"] = error
    if reminder is not None:
        view["reminder"] = reminder
    return view


def _read_result(run: dict, *, service: Callable[..., dict], state_dir: Path) -> Any:
    if not run.get("resultAvailable"):
        return None
    full = service("task_result", {"runId": run["runId"]}, state_dir)
    result = full.get("result")
    if isinstance(result, dict):
        # Execution report is retained as delivered. The service's get path
        # carries fixed artifacts and audit when the Host needs them.
        return result
    return result


def _workflow_facts(run: dict, *, service: Callable[..., dict], state_dir: Path) -> dict:
    """Read the authoritative response id and actions once at a return boundary."""
    if run.get("workflowState") is None:
        return run
    view = service("workflow_get", {"runId": run["runId"]}, state_dir)
    return {**run, **{key: view[key] for key in ('checkout', 'unsealedChanges', 'folderChangesNotice') if key in view},
            "workflowState": view["state"], "actions": view["actions"],
            "artifactId": view.get("finalArtifactId"), "activeRequest": view.get("activeRequest")}


def await_run(
    params: dict | AwaitRequest,
    state_dir: Path,
    stop: threading.Event | None = None,
    clock: Callable[[], float] = time.monotonic,
    service: Callable[..., dict] = _call_service,
    *,
    scoped: bool = False,
) -> dict:
    """Wait for one admitted run, preserving the existing wakeup conditions."""
    request = params if isinstance(params, AwaitRequest) else parse_request("await", params)
    run_id, wait_seconds = request.runId, request.waitSeconds
    if stop is not None and stop.is_set():
        raise WaitAbandoned(run_id)
    started = clock()
    run = service("task_get", {"runId": run_id}, state_dir)
    deadline = started + wait_seconds
    reminder_wait_id = uuid.uuid4().hex
    try:
        while True:
            if stop is not None and stop.is_set():
                raise WaitAbandoned(run_id)
            reminder = run.get("reminder")
            if isinstance(reminder, dict):
                return _view(run, outcome=reminder["kind"], started=started,
                             wait_seconds=wait_seconds, clock=clock, reminder=reminder)
            workflow_state = run.get("workflowState")
            if workflow_state == "awaiting-host":
                try:
                    run = _workflow_facts(run, service=service, state_dir=state_dir)
                except ServiceError as error:
                    return _view(run, outcome="unavailable", started=started,
                                 wait_seconds=wait_seconds, clock=clock,
                                 error={"code": error.code, "message": str(error)})
                if run["workflowState"] != "awaiting-host":
                    run = service("task_get", {"runId": run_id}, state_dir)
                    continue
                result = None
                if run.get("resultAvailable"):
                    try:
                        result = _read_result(run, service=service, state_dir=state_dir)
                    except ServiceError as error:
                        return _view(run, outcome="waiting-host", started=started,
                                     wait_seconds=wait_seconds, clock=clock,
                                     error={"code": error.code, "message": str(error)})
                return _view(run, outcome="waiting-host", started=started,
                             wait_seconds=wait_seconds, clock=clock, result=result)
            if workflow_state is None:
                if run.get("resultAvailable") or run.get("state") in TERMINAL_STATUSES:
                    break
            elif workflow_state in ("delivered", "accepted", "concluded", "cancelled", "failed"):
                evidence = run.get("workflowShutdown")
                if not isinstance(evidence, dict) or _shutdown_confirmed(run):
                    break
            remaining = deadline - clock()
            if remaining <= 0:
                return _view(run, outcome="wait-timeout", started=started,
                             wait_seconds=wait_seconds, clock=clock)
            timeout_ms = max(1, int(min(WAIT_SLICE_MS, remaining * 1000)))
            try:
                shutdown = run.get("workflowShutdown")
                if (isinstance(shutdown, dict) and shutdown.get("unconfirmedRunIds")
                        and (scoped or run.get("attemptState") not in
                             ("starting", "executing", "finalizing", "uncertain"))):
                    # A descendant's stop can leave the parent's revision unchanged.
                    target = run_id if scoped else shutdown["unconfirmedRunIds"][0]
                    head = service("events_read", {"runId": target, "limit": 1}, state_dir)["head"]
                    run = service("task_get", {"runId": run_id}, state_dir)
                    current = run.get("workflowShutdown")
                    if isinstance(current, dict) and not _shutdown_confirmed(run):
                        service("events_wait", {"runId": target, "after": head, "timeoutMs": timeout_ms}, state_dir)
                        run = service("task_get", {"runId": run_id}, state_dir)
                else:
                    run = service("task_wait", {"runId": run_id, "afterRevision": run.get("revision"),
                                                "timeoutMs": timeout_ms, "consumeReminders": True,
                                                "reminderWaitId": reminder_wait_id}, state_dir)
            except ServiceError as error:
                if (state_dir / "upgrade.json").exists():
                    time.sleep(min(UPGRADE_HANDOVER_POLL_SECONDS, max(deadline - clock(), 0.0)))
                    continue
                return _view(run, outcome="unavailable", started=started,
                             wait_seconds=wait_seconds, clock=clock,
                             error={"code": error.code, "message": str(error)})
        if not run.get("resultAvailable"):
            try:
                run = _workflow_facts(run, service=service, state_dir=state_dir)
            except ServiceError as error:
                return _view(run, outcome="unavailable", started=started,
                             wait_seconds=wait_seconds, clock=clock,
                             error={"code": error.code, "message": str(error)})
            return _view(run, outcome="result-unavailable", started=started,
                         wait_seconds=wait_seconds, clock=clock,
                         error={"code": "RESULT_NOT_AVAILABLE", "message": "No persisted execution result is available"})
        try:
            run = _workflow_facts(run, service=service, state_dir=state_dir)
            result = _read_result(run, service=service, state_dir=state_dir)
        except ServiceError as error:
            return _view(run, outcome="result-unavailable", started=started,
                         wait_seconds=wait_seconds, clock=clock,
                         error={"code": error.code, "message": str(error)})
        if result is None:
            return _view(run, outcome="result-unavailable", started=started,
                         wait_seconds=wait_seconds, clock=clock,
                         error={"code": "RESULT_NOT_AVAILABLE", "message": "The persisted result has no payload"})
        return _view(run, outcome=run.get("workflowState") or run.get("state", "unknown"),
                     started=started, wait_seconds=wait_seconds, clock=clock, result=result)
    except KeyboardInterrupt:
        raise WaitAbandoned(run_id) from None
