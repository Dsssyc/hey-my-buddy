"""CLI blocking wait: ``hey-my-buddy await`` stays connected until the durable run ends.

Waiting is ordinary synchronous code on top of the engine's event wait
(``wait`` with its existing 30 s cap). There is no model call, no heartbeat and no
model-visible polling: the engine is event-driven and the facade only re-arms a
bounded wait slice until the run is terminal or this call's wait window is spent.

The wait window (``waitSeconds``) is how long *this CLI call* stays connected,
1-86400 s, default 86400 s. The durable run keeps its own lifetime: admission and
execution belong to ``submit`` and to the engine's runner
deadline, never to this wait. Reaching the window returns an honest
``wait-timeout`` envelope and the job keeps running, recovered by ``requestId`` or
``runId``.

``await`` never changes the durable work:

* it never starts, resumes, retries or cancels anything, on timeout or otherwise;
* for an active execution, it consumes one durable reminder when returning it;
* a service failure returns an unavailable envelope with commands to inspect
  and await the SAME durable ``runId``;
* stopping this wait (Ctrl-C, a closed terminal, a killed client, ``waitSeconds``
  expiry) only stops the *wait*. The named run ends through ``hey-my-buddy cancel``
  (micro tasks, with the owner's control file), ``execution-cancel`` (execution
  records), its own runner deadline, or a service stop (``hey-my-buddy stop``/owner
  shutdown).
"""
from __future__ import annotations

import json
from pathlib import Path
import shlex
import threading
import time
import uuid
from typing import Any, Callable

from ..protocol.transport import ServiceError, _call_service

# The wait window may never exceed the existing 24 h CLI wait maximum.
MAX_WAIT_SECONDS = 86400
MIN_WAIT_SECONDS = 1
DEFAULT_AWAIT_SECONDS = 86400
WAIT_SLICE_MS = 30000

# ``reconciliation-needed`` is terminal for a wait: the task will not move again
# without an explicit retry, and no wait may relaunch it.
TERMINAL_STATUSES = frozenset({"completed", "concluded", "failed", "cancelled", "reconciliation-needed"})

# Reported when a run reached ``completed`` but this call cannot deliver the
# promised result payload. The execution status stays visible in ``status``;
# the outcome/ok claim never says success without the result.
COMPLETED_WITHOUT_RESULT_OUTCOME = "completed-no-result"

class WaitAbandoned(RuntimeError):
    """This wait was abandoned (Ctrl-C, closed client, stopped wait).

    The durable run keeps executing and stays recoverable; it ends through an
    explicit ``hey-my-buddy cancel``/``execution-cancel``, its own runner execution
    deadline, or a service stop.
    """

    def __init__(self, request_id: str | None = None, run_id: str | None = None):
        super().__init__("The blocking wait was abandoned; the durable run was not cancelled")
        self.request_id = request_id
        self.run_id = run_id


def validate_wait_seconds(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not (MIN_WAIT_SECONDS <= value <= MAX_WAIT_SECONDS):
        raise ServiceError("INVALID_ARGUMENT", f"waitSeconds must be an integer between {MIN_WAIT_SECONDS} and {MAX_WAIT_SECONDS}")
    return value


def _identity(request_id: str | None, run_id: str | None) -> str:
    """One JSON selector string for a real CLI command; ``runId`` wins over ``requestId``."""
    selector = {"runId": run_id} if run_id else {"requestId": request_id}
    return json.dumps(selector, ensure_ascii=False, separators=(",", ":"))


def recovery_commands(request_id: str | None, run_id: str | None) -> list[str]:
    """Real ``hey-my-buddy`` commands that inspect/recover one existing run.

    The JSON selector is built with :func:`json.dumps` and every argument is
    quoted with :func:`shlex.quote`, so requestIds/runIds containing quotes,
    apostrophes or non-ASCII text survive ``shlex.split`` + ``json.loads``.
    ``status``/``get``/``result``/``cancel`` only accept a ``runId``: while the run
    ID is unknown, the only supported recovery command is ``hey-my-buddy await`` by
    ``requestId``. Cancellation is deliberately not suggested as an automatic
    step: it needs the owner's control capability, so the action text names it.
    """
    selector = shlex.quote(_identity(request_id, run_id))
    if run_id:
        return [
            f"hey-my-buddy status {selector}",
            f"hey-my-buddy get {selector}",
            f"hey-my-buddy await {selector}",
            f"hey-my-buddy result {selector}",
        ]
    return [f"hey-my-buddy await {selector}"]


def _recovery(request_id: str | None, run_id: str | None, reason: str) -> dict:
    if run_id:
        action = (
            "Inspect the SAME existing run with the commands below; `hey-my-buddy status` reads the execution record and "
            "`hey-my-buddy get` the micro task view. Re-run `hey-my-buddy await` with this runId to keep waiting on the same "
            "run: the wait never starts, resumes or cancels work. A micro task ends only through `hey-my-buddy cancel` "
            "with the current owner's saved controlFile; an infrastructure execution record ends through "
            "`execution-cancel`."
        )
    else:
        action = (
            "Read the runId from the commands below, then inspect it. `hey-my-buddy await` by this requestId waits on an "
            "existing run and never starts work (NOT_FOUND means this requestId was never admitted; admit a micro task "
            "with `hey-my-buddy submit`, then await the same requestId). "
            "`status`/`get`/`result`/`cancel` need a runId."
        )
    return {
        "reason": reason,
        "requestId": request_id,
        "runId": run_id,
        "action": action,
        "commands": recovery_commands(request_id, run_id),
    }


def _envelope(
    run: dict,
    *,
    request_id: str | None,
    outcome: str,
    waited_seconds: float,
    wait_seconds: int,
    result: Any = None,
    recovery: dict | None = None,
    error: dict | None = None,
) -> dict:
    """Build the caller-visible envelope.

    The success claim is strict and is decided here, once: ``ok`` is true only
    when the run's outcome is ``completed`` *and* this envelope actually carries
    the promised result payload *and* no error was recorded. A completed run
    whose result was not persisted keeps its execution status in ``status`` but is reported as
    ``completed-no-result`` with ``ok=False`` and ``resultDelivered=False``,
    never as a successful complete result.
    """
    result_delivered = result is not None
    success = outcome == "completed" and result_delivered and error is None
    if outcome == "completed" and not success:
        outcome = COMPLETED_WITHOUT_RESULT_OUTCOME
    return {
        "runId": run.get("runId"),
        **({"waitingForHost": run["waitingForHost"]} if "waitingForHost" in run else {}),
        "requestId": run.get("requestId", request_id),
        "status": run.get("status"),
        **({"workflowState": run["workflowState"]} if run.get("workflowState") is not None else {}),
        "outcome": outcome,
        "ok": success,
        "resultAvailable": bool(run.get("resultAvailable")),
        "resultDelivered": result_delivered,
        "terminationReason": run.get("terminationReason"),
        "interruptionEvidence": run.get("interruptionEvidence"),
        # For a micro task, cancellation/completion never implies that a
        # still-running descendant is stopped just because the parent is idle.
        "shutdownConfirmed": (
            run["workflowShutdown"].get("selfConfirmed") is True
            and run["workflowShutdown"].get("descendantsConfirmed") is True
        ) if isinstance(run.get("workflowShutdown"), dict) else run.get("shutdownConfirmed") is True,
        **({"shutdown": run["workflowShutdown"]} if isinstance(run.get("workflowShutdown"), dict) else {}),
        "acceptedAt": run.get("acceptedAt"),
        "revision": run.get("revision"),
        "createdAt": run.get("createdAt"),
        "updatedAt": run.get("updatedAt"),
        "cwd": run.get("cwd"),
        "logPaths": run.get("logPaths"),
        "waitedSeconds": round(max(waited_seconds, 0.0), 1),
        "waitSeconds": wait_seconds,
        "maxWaitSeconds": MAX_WAIT_SECONDS,
        "timedOut": outcome == "wait-timeout",
        "result": result,
        "recovery": recovery,
        "error": error,
    }


def _find_run(request_id: str, service: Callable[..., dict], state_dir: Path) -> dict | None:
    try:
        return service("status", {"requestId": request_id}, state_dir)
    except ServiceError as error:
        if error.code == "NOT_FOUND":
            return None
        raise


def _result_undelivered(
    run: dict,
    *,
    request_id: str | None,
    run_id: str,
    waited_seconds: float,
    wait_seconds: int,
    recovery_reason: str,
    error: dict,
) -> dict:
    """Terminal run whose result cannot be delivered: honest status, no success.

    The execution status is preserved (the worker may have completed), but the
    envelope reports result delivery as unavailable/incomplete and keeps the
    runId/requestId/recovery identity so the same run can be read again.
    """
    return _envelope(
        run,
        request_id=request_id,
        outcome=run.get("status", "unknown"),
        waited_seconds=waited_seconds,
        wait_seconds=wait_seconds,
        recovery=_recovery(request_id, run_id, recovery_reason),
        error=error,
    )


def _result_command(run_id: str) -> str:
    return f"hey-my-buddy result {shlex.quote(json.dumps({'runId': run_id}, ensure_ascii=False))}"


def _head_text(value, limit: int) -> str | None:
    if value is None:
        return None
    return value if len(value) <= limit else value[:limit]


def _micro_task_result_view(full: dict, run_id: str) -> dict | None:
    """The bounded default view of one micro-task result.

    The durable result keeps the whole runner payload, the effective manifest and the
    sealed output; normal await callers get this compact view plus the exact
    command that reads the full record back.
    """
    if full.get("workflowState") is None:
        # Infrastructure executions retain their opaque result payload.
        return None
    result = full["result"]
    if result is None:
        return None
    turn = result.get("turn")
    outcome = turn["outcome"] if turn is not None else {}
    partial = result.get("partialWorkspaceSeal")
    seal = partial if partial is not None else result.get("workspaceSeal")
    artifacts = []
    if seal is not None:
        artifacts.append(
            {
                "kind": "partial-output" if partial else "workspace-seal",
                **({"partial": True, "verified": False, "final": False} if partial else {}),
                "snapshotSha256": seal.get("snapshotSha256"),
                "manifestSha256": seal.get("manifestSha256"),
                "commit": seal.get("commit"),
                "diffPath": seal.get("diffPath"),
                "cumulativePatch": seal.get("cumulativePatch"),
            }
        )
    from ..blackboard.evaluation.native_observations import failure_view
    final_text = result.get("finalText")
    remaining = outcome.get("remaining", [])
    disposition = outcome.get("disposition")
    return {
        "status": result.get("status"),
        "terminationReason": full.get("terminationReason"),
        "interruptionEvidence": result.get("interruptionEvidence"),
        "tokenUsage": full.get("tokenUsage"),
        "quotaFailure": failure_view(result),
        "turn": None
        if turn is None
        else {
            "turnId": turn.get("turnId"),
            "resumeMode": turn.get("resumeMode"),
            "sessionId": turn.get("sessionId"),
            "disposition": disposition,
            "summary": _head_text(outcome.get("summary"), 2000),
            "remaining": [item[:500] for item in remaining[:8]],
        },
        "request": outcome.get("request") if disposition in ("assistance", "attention") else None,
        "artifacts": artifacts,
        "finalText": _head_text(final_text, 2000),
        "finalTextTruncated": final_text is not None and len(final_text) > 2000,
        "logPaths": result.get("logPaths"),
        "processState": {"shutdownConfirmed": full["shutdownConfirmed"]},
        "resultCommand": _result_command(run_id),
    }


def _boundary_command(method: str, params: dict) -> str:
    return f"hey-my-buddy {method} {shlex.quote(json.dumps(params, ensure_ascii=False))}"


def _host_boundary(
    run: dict,
    *,
    request_id: str | None,
    run_id: str,
    waited_seconds: float,
    wait_seconds: int,
    service: Callable[..., dict],
    state_dir: Path,
) -> dict:
    """Return one micro-task Host decision boundary as a structured checkpoint.

    A normal structured yield is neither micro task completion nor an execution failure:
    the original task is unfinished and a Host decision (or continuation) is
    required. The wait ends here instead of blocking forever on a success-only
    event, and it reads the actual persisted turn result rather than inventing one.
    """
    full: Any = None
    error: dict | None = None
    if run.get("resultAvailable"):
        try:
            full = service("result", {"runId": run_id}, state_dir)
            run = dict(run)
            run.pop("waitingForHost", None)
            if "waitingForHost" in full:
                run["waitingForHost"] = full["waitingForHost"]
        except ServiceError as failure:
            error = {"code": getattr(failure, "code", "SERVICE_ERROR"), "message": str(failure)}
    result = full["result"] if full is not None else None
    workflow = run["workflow"]
    routing = workflow.get("requestRouting") is True
    turn = result.get("turn") if result is not None else None
    request_view = None
    if workflow.get("activeRequestId"):
        request_view = {
            "requestId": workflow.get("activeRequestId"),
            "kind": workflow.get("requestKind"),
            "summary": workflow.get("requestSummary"),
            "expectedRevision": workflow.get("revision"),
            "routing": routing,
        }
    revision = workflow.get("revision")
    # The owner names its own saved controlFile; the CLI never guesses a
    # generation, and no command below can run without that explicit capability.
    commands = []
    if not routing and request_view is not None and request_view.get("kind") in (
        "assistance",
        "attention",
        "helper-attention",
    ):
        commands.append(
            _boundary_command(
                "decide",
                {
                    "runId": run_id,
                    "requestId": request_view["requestId"],
                    "commandId": f"{run_id}:approve:{request_view['requestId']}",
                    "controlFile": "<saved-controlFile>",
                    "expectedRevision": revision,
                    "decision": "approve",
                    "helpers": [],
                },
            )
        )
        commands.append(
            _boundary_command(
                "decide",
                {
                    "runId": run_id,
                    "requestId": request_view["requestId"],
                    "commandId": f"{run_id}:decline:{request_view['requestId']}",
                    "controlFile": "<saved-controlFile>",
                    "expectedRevision": revision,
                    "decision": "decline",
                    "note": "...",
                },
            )
        )
    boundary = workflow.get("routingBoundary")
    if routing:
        if boundary is not None:
            templates = boundary["commands"]
            for choice in templates["continue"]["choices"]:
                if not choice["blocked"]:
                    commands.append({"method": choice["method"], "params": choice["params"]})
            reroute = templates["reroute"]
            if not reroute["blocked"]:
                commands.append({"method": reroute["method"], "params": reroute["params"],
                                 "notBefore": reroute["notBefore"]})
    else:
        commands.append(_boundary_command("continue", {
            "runId": run_id, "commandId": f"{run_id}:continue:{revision}",
            "controlFile": "<saved-controlFile>", "expectedRevision": revision,
            "input": "...", "helperPolicy": "keep",
        }))
    micro_task_view = _micro_task_result_view(full, run_id) if full is not None else None
    envelope = _envelope(
        run,
        request_id=request_id,
        outcome="waiting-host",
        waited_seconds=waited_seconds,
        wait_seconds=wait_seconds,
        result=micro_task_view if micro_task_view is not None else result,
        error=error,
    )
    envelope.update(
        {
            "ok": False,
            "microTaskComplete": False,
            "resultCompact": micro_task_view is not None,
            "resultCommand": _result_command(run_id),
            "workflowState": run.get("workflowState"),
            "workflow": workflow,
            "turn": micro_task_view["turn"] if micro_task_view is not None else turn,
            "artifacts": micro_task_view["artifacts"] if micro_task_view is not None else [],
            "request": request_view,
            "nextCommands": commands,
        }
    )
    if boundary is not None:
        envelope["routingBoundary"] = boundary
        if request_view is not None:
            request_view["routingBoundary"] = boundary
    return envelope


def _wait_until_terminal(
    run: dict,
    *,
    request_id: str | None,
    run_id: str,
    wait_seconds: int,
    started: float,
    clock: Callable[[], float],
    service: Callable[..., dict],
    state_dir: Path,
    stop: threading.Event | None,
    scoped: bool,
) -> dict:
    """Follow one existing durable run with bounded event waits.

    It may consume a reminder, but never starts, resumes, retries or cancels
    the durable run. An abandoned or timed-out wait only stops this CLI call.
    """
    deadline = started + wait_seconds
    reminder_wait_id = uuid.uuid4().hex
    while True:
        if stop is not None and stop.is_set():
            raise WaitAbandoned(request_id, run_id)
        reminder = run.get("reminder")
        if isinstance(reminder, dict):
            envelope = _envelope(
                run,
                request_id=request_id,
                outcome=reminder["kind"],
                waited_seconds=clock() - started,
                wait_seconds=wait_seconds,
                recovery=_recovery(request_id, run_id, "A task reminder ended this wait. Inspect the task or await the same run again."),
            )
            envelope.update(reminder=reminder, microTaskComplete=False)
            return envelope
        workflow_state = run.get("workflowState")
        if workflow_state == "awaiting-host":
            return _host_boundary(
                run,
                request_id=request_id,
                run_id=run_id,
                waited_seconds=clock() - started,
                wait_seconds=wait_seconds,
                service=service,
                state_dir=state_dir,
            )
        if workflow_state is None:
            if run.get("resultAvailable"):
                break
        elif workflow_state in ("delivered", "accepted", "concluded", "cancelled", "failed"):
            shutdown = run.get("workflowShutdown")
            if not isinstance(shutdown, dict) or (
                shutdown.get("selfConfirmed") is True and shutdown.get("descendantsConfirmed") is True
            ):
                break
        if workflow_state is None and run.get("status") in TERMINAL_STATUSES:
            break
        remaining = deadline - clock()
        if remaining <= 0:
            return _envelope(
                run,
                request_id=request_id,
                outcome="wait-timeout",
                waited_seconds=clock() - started,
                wait_seconds=wait_seconds,
                recovery=_recovery(
                    request_id,
                    run_id,
                    "This call's wait window ended while the durable run was still active. That is a wait/connection "
                    "limit, not an execution failure; the run was neither cancelled nor marked failed.",
                ),
            )
        timeout_ms = max(1, int(min(WAIT_SLICE_MS, remaining * 1000)))
        try:
            shutdown = run.get("workflowShutdown")
            if (isinstance(shutdown, dict) and shutdown.get("unconfirmedRunIds")
                    and (scoped or run.get("attemptState") not in ("starting", "executing", "finalizing", "uncertain"))):
                # Parent revision need not change when an owned descendant stops.
                # A terminal task with unconfirmed shutdown can make task_wait
                # return immediately. Use the durable event cursor instead, with
                # a recheck after reading it so a concurrent stop is not missed.
                # Scoped callers can wait on the owning run's facts, but cannot
                # directly address its internal Router or assistance executions.
                target = run_id if scoped else shutdown["unconfirmedRunIds"][0]
                events = service("events", {"runId": target, "limit": 1}, state_dir)
                head = events["head"]
                run = service("status", {"runId": run_id}, state_dir)
                current = run.get("workflowShutdown")
                if isinstance(current, dict) and (
                    current.get("selfConfirmed") is not True or current.get("descendantsConfirmed") is not True
                ):
                    service("watch", {"runId": target, "after": head, "timeoutMs": timeout_ms}, state_dir)
                    run = service("status", {"runId": run_id}, state_dir)
            else:
                run = service("wait", {"runId": run_id, "afterRevision": run.get("revision"),
                                       "timeoutMs": timeout_ms,
                                       "consumeReminders": True, "reminderWaitId": reminder_wait_id}, state_dir)
        except ServiceError as failure:
            return _envelope(
                run,
                request_id=request_id,
                outcome="unavailable",
                waited_seconds=clock() - started,
                wait_seconds=wait_seconds,
                recovery=_recovery(request_id, run_id, "The service did not answer; await the same runId again later"),
                error={"code": failure.code, "message": str(failure)},
            )
    waited = clock() - started
    if not run.get("resultAvailable"):
        return _result_undelivered(
            run,
            request_id=request_id,
            run_id=run_id,
            waited_seconds=waited,
            wait_seconds=wait_seconds,
            recovery_reason=(
                "The run is terminal but no runner result was persisted; there is no successful result to report and "
                "the execution status is preserved. Read the run again with `hey-my-buddy result` by runId, then check its "
                "logs before any manual recovery."
            ),
            error={
                "code": "RESULT_NOT_AVAILABLE",
                "message": "The terminal run has no persisted runner result, so no successful result can be reported",
            },
        )
    full = service("result", {"runId": run_id}, state_dir)
    micro_task_view = _micro_task_result_view(full, run_id)
    if micro_task_view is not None:
        # A late execution completion does not undo the Host's micro task cancellation.
        micro_task_state = full.get("workflowState", run.get("workflowState"))
        outcome = micro_task_state if micro_task_state in ("cancelled", "failed") else full.get("status", run.get("status", "unknown"))
        envelope = _envelope(
            full,
            request_id=request_id,
            outcome=outcome,
            waited_seconds=clock() - started,
            wait_seconds=wait_seconds,
            result=micro_task_view,
        )
        envelope.update(
            {
                "resultCompact": True,
                "resultCommand": _result_command(run_id),
                "turn": micro_task_view["turn"],
                "artifacts": micro_task_view["artifacts"],
            }
        )
        return envelope
    return _envelope(
        full,
        request_id=request_id,
        outcome=full.get("status", run.get("status", "unknown")),
        waited_seconds=clock() - started,
        wait_seconds=wait_seconds,
        result=full["result"],
    )


def await_run(
    params: dict,
    state_dir: Path,
    stop: threading.Event | None = None,
    clock: Callable[[], float] = time.monotonic,
    service: Callable[..., dict] = _call_service,
    *,
    scoped: bool = False,
) -> dict:
    """Wait for an already existing durable run without starting or replaying anything.

    ``hey-my-buddy await`` is the only CLI blocking call: it reads the durable run and
    reuses the same C-Two service and the same run ID. Its wait window is explicit
    and bounded (``waitSeconds``, default 86400, max 86400); reaching it is
    recoverable by awaiting the same run again. It never starts, resumes, retries
    or cancels work - not even on timeout. A service failure during a wait
    returns recovery commands for the same runId.
    """
    unknown = sorted(set(params) - {"requestId", "runId", "waitSeconds"})
    if unknown:
        raise ServiceError("INVALID_ARGUMENT", f"Unknown await parameter: {unknown[0]}")
    request_id = params.get("requestId")
    run_id = params.get("runId")
    if run_id is not None and (not isinstance(run_id, str) or not run_id.strip()):
        raise ServiceError("INVALID_ARGUMENT", "runId must be a nonempty string")
    if request_id is not None and (not isinstance(request_id, str) or not request_id.strip() or len(request_id) > 128):
        raise ServiceError("INVALID_ARGUMENT", "requestId must contain 1-128 characters")
    if request_id is None and run_id is None:
        raise ServiceError("INVALID_ARGUMENT", "await requires requestId or runId")
    wait_seconds = validate_wait_seconds(params.get("waitSeconds", DEFAULT_AWAIT_SECONDS))
    if stop is not None and stop.is_set():
        raise WaitAbandoned(request_id, run_id)
    try:
        started = clock()
        if run_id is not None:
            run = service("status", {"runId": run_id}, state_dir)
            if request_id is not None and run.get("requestId") != request_id:
                raise ServiceError("CONFLICT", "runId and requestId do not belong to the same run")
        else:
            run = _find_run(request_id, service, state_dir)
            if run is None:
                raise ServiceError(
                    "NOT_FOUND",
                    "No existing run has this requestId; await never starts work. Admit a micro task with "
                    "`hey-my-buddy submit`, then await the same requestId.",
                )
        run_id = run["runId"]
        request_id = run.get("requestId", request_id)
        return _wait_until_terminal(
            run,
            request_id=request_id,
            run_id=run_id,
            wait_seconds=wait_seconds,
            started=started,
            clock=clock,
            service=service,
            state_dir=state_dir,
            stop=stop,
            scoped=scoped,
        )
    except KeyboardInterrupt:
        raise WaitAbandoned(request_id, run_id) from None
