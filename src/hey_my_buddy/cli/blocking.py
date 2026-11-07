"""Host blocking calls over bounded private service waits."""
from __future__ import annotations

from pathlib import Path
import time
import uuid
from typing import Any, Callable

from ..protocol.requests import AwaitRequest, ConcludeRequest, dump_request, parse_request
from ..protocol.transport import ServiceError, _call_service
from ..protocol.awaiting import message_kind, shutdown_confirmed as _shutdown_confirmed

WAIT_SLICE_MS = 30000
UPGRADE_HANDOVER_POLL_SECONDS = 0.2


class WaitAbandoned(RuntimeError):
    """The client stopped waiting; the durable micro task is untouched."""

    def __init__(self, run_id: str):
        super().__init__("The blocking wait was abandoned; the durable run was not cancelled")
        self.run_id = run_id


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
                'checkout', 'unsealedChanges'):
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
    return {**run, **{key: view[key] for key in ('checkout', 'unsealedChanges') if key in view},
            "workflowState": view["state"], "actions": view["actions"],
            "artifactId": view.get("finalArtifactId"), "activeRequest": view.get("activeRequest")}


def _upgrade_handover(state_dir: Path, deadline: float, clock: Callable[[], float]) -> bool:
    remaining = deadline - clock()
    if remaining <= 0 or not (state_dir / "upgrade.json").exists():
        return False
    time.sleep(min(UPGRADE_HANDOVER_POLL_SECONDS, remaining))
    return True


def _wait_slice(run: dict, *, deadline: float, clock: Callable[[], float],
                service: Callable[..., dict], state_dir: Path, consume_reminders: bool,
                reminder_wait_id: str | None = None) -> dict | None:
    """Use one bounded task wait and rearm it when an upgrade changes owners."""
    timeout_ms = max(1, int(min(WAIT_SLICE_MS, (deadline - clock()) * 1000)))
    params = {"runId": run["runId"], "afterRevision": run.get("revision"),
              "timeoutMs": timeout_ms, "consumeReminders": consume_reminders}
    if reminder_wait_id is not None:
        params["reminderWaitId"] = reminder_wait_id
    try:
        return service("task_wait", params, state_dir)
    except ServiceError:
        if _upgrade_handover(state_dir, deadline, clock):
            return None
        raise


def await_run(
    params: dict | AwaitRequest,
    state_dir: Path,
    clock: Callable[[], float] = time.monotonic,
    service: Callable[..., dict] = _call_service,
) -> dict:
    """Wait for one admitted run, preserving the existing wakeup conditions."""
    request = params if isinstance(params, AwaitRequest) else parse_request("await", params)
    run_id, wait_seconds = request.runId, request.waitSeconds
    started = clock()
    deadline = started + wait_seconds
    reminder_wait_id = uuid.uuid4().hex
    try:
        run = service("task_get", {"runId": run_id}, state_dir)
        # An already visible workflow boundary must not bypass the one-time
        # delivery of a new issue. This also keeps Lead's doorbell and its next
        # await consistent when the issue arrives after an owned delivery.
        if message_kind(run) is not None and not run.get('reminder') and not run.get('issue'):
            run = service('task_wait', {'runId': run_id, 'timeoutMs': 0, 'consumeReminders': True,
                                        'reminderWaitId': reminder_wait_id}, state_dir)
        while True:
            reminder = run.get("reminder")
            kind = message_kind(run)
            if kind == 'issue':
                return _view(run, outcome='issue', started=started, wait_seconds=wait_seconds, clock=clock)
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
                                 error=error.payload())
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
                                     error=error.payload())
                return _view(run, outcome="waiting-host", started=started,
                             wait_seconds=wait_seconds, clock=clock, result=result)
            if kind is not None:
                break
            remaining = deadline - clock()
            if remaining <= 0:
                return _view(run, outcome="wait-timeout", started=started,
                             wait_seconds=wait_seconds, clock=clock)
            try:
                next_run = _wait_slice(run, deadline=deadline, clock=clock, service=service,
                                       state_dir=state_dir, consume_reminders=True,
                                       reminder_wait_id=reminder_wait_id)
            except ServiceError as error:
                return _view(run, outcome="unavailable", started=started,
                             wait_seconds=wait_seconds, clock=clock,
                             error=error.payload())
            if next_run is not None:
                run = next_run
        if not run.get("resultAvailable"):
            try:
                run = _workflow_facts(run, service=service, state_dir=state_dir)
            except ServiceError as error:
                return _view(run, outcome="unavailable", started=started,
                             wait_seconds=wait_seconds, clock=clock,
                             error=error.payload())
            return _view(run, outcome="result-unavailable", started=started,
                         wait_seconds=wait_seconds, clock=clock,
                         error={"code": "RESULT_NOT_AVAILABLE", "message": "No persisted execution result is available"})
        try:
            run = _workflow_facts(run, service=service, state_dir=state_dir)
            result = _read_result(run, service=service, state_dir=state_dir)
        except ServiceError as error:
            return _view(run, outcome="result-unavailable", started=started,
                         wait_seconds=wait_seconds, clock=clock,
                         error=error.payload())
        if result is None:
            return _view(run, outcome="result-unavailable", started=started,
                         wait_seconds=wait_seconds, clock=clock,
                         error={"code": "RESULT_NOT_AVAILABLE", "message": "The persisted result has no payload"})
        return _view(run, outcome=run.get("workflowState") or run.get("state", "unknown"),
                     started=started, wait_seconds=wait_seconds, clock=clock, result=result)
    except KeyboardInterrupt:
        raise WaitAbandoned(run_id) from None


def conclude_run(
    params: dict | ConcludeRequest,
    state_dir: Path,
    clock: Callable[[], float] = time.monotonic,
    service: Callable[..., dict] = _call_service,
) -> dict:
    """Conclude, then wait for confirmed tree shutdown before an identical retry."""
    request = params if isinstance(params, ConcludeRequest) else parse_request("conclude", params)
    wire = dump_request(request)
    wire.pop("waitSeconds", None)
    owner_id = request.runId
    target_id = request.targetRunId or owner_id
    wait_seconds = request.waitSeconds
    started = clock()
    deadline = started + wait_seconds
    try:
        return service("conclude", wire, state_dir)
    except KeyboardInterrupt:
        return {"runId": owner_id, "actions": ["get", "conclude"],
                "cancellationRecorded": None, "conclusionRecorded": None,
                "shutdownConfirmed": None, "waitedSeconds": round(max(clock() - started, 0.0), 1),
                "waitSeconds": wait_seconds, "timedOut": False,
                "error": {"code": "WAIT_ABANDONED", "message": "The initial conclude reply was interrupted"},
                **({"targetRunId": target_id} if request.targetRunId is not None else {})}
    except ServiceError as error:
        if error.code != "SHUTDOWN_UNCONFIRMED" or error.details.get("cancellationRecorded") is not True:
            raise

    run: dict | None = None

    def pending_error(error: dict, *, timed_out: bool = False,
                      conclusion_recorded: bool | None = False) -> dict:
        result = {"runId": owner_id, "actions": _actions(run) if run is not None else ["get", "conclude"],
                  "cancellationRecorded": True,
                  "conclusionRecorded": conclusion_recorded,
                  "shutdownConfirmed": _shutdown_confirmed(run) if run is not None else False,
                  "waitedSeconds": round(max(clock() - started, 0.0), 1),
                  "waitSeconds": wait_seconds, "timedOut": timed_out, "error": error}
        if request.targetRunId is not None:
            result["targetRunId"] = target_id
        if run is not None:
            result["status"] = run.get("state")
            result["workflowState"] = run.get("workflowState")
            if run.get("workflowShutdown") is not None:
                result["shutdown"] = run["workflowShutdown"]
        return result

    retry_sent = False
    uncertain_reply = False
    try:
        while True:
            if run is not None and _shutdown_confirmed(run):
                try:
                    retry_sent = True
                    return service("conclude", wire, state_dir)
                except ServiceError as error:
                    if error.code != "SHUTDOWN_UNCONFIRMED":
                        unknown_reply = (error.code in ("SERVICE_UNAVAILABLE", "INVALID_RESPONSE")
                                         or error.code.startswith("C_TWO_"))
                        uncertain_reply = uncertain_reply or unknown_reply
                        if unknown_reply and _upgrade_handover(state_dir, deadline, clock):
                            continue
                        return pending_error(error.payload(), conclusion_recorded=None if uncertain_reply else False)
                    retry_sent = False
                    uncertain_reply = False
                    run = None
                    continue
            if deadline - clock() <= 0:
                return pending_error({"code": "SHUTDOWN_UNCONFIRMED",
                                      "message": "Cancellation is recorded; shutdown is not confirmed and no conclusion has been recorded"},
                                     timed_out=True)
            if run is None:
                try:
                    run = service("task_get", {"runId": target_id}, state_dir)
                except ServiceError as error:
                    if _upgrade_handover(state_dir, deadline, clock):
                        continue
                    return pending_error(error.payload())
                continue
            try:
                next_run = _wait_slice(run, deadline=deadline, clock=clock, service=service,
                                       state_dir=state_dir, consume_reminders=False)
            except ServiceError as error:
                return pending_error(error.payload())
            if next_run is not None:
                run = next_run
    except KeyboardInterrupt:
        return pending_error({"code": "WAIT_ABANDONED",
                              "message": "The conclude wait was interrupted after cancellation was requested"},
                             conclusion_recorded=None if retry_sent else False)
