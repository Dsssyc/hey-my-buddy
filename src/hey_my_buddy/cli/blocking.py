"""CLI blocking wait: ``buddy await`` stays connected until the durable run ends.

Waiting is ordinary synchronous code on top of the engine's event wait
(``wait`` with its existing 30 s cap). There is no model call, no heartbeat and no
model-visible polling: the engine is event-driven and the facade only re-arms a
bounded wait slice until the run is terminal or this call's wait window is spent.

The wait window (``waitSeconds``) is how long *this CLI call* stays connected,
1-86400 s, default 86400 s. The durable run keeps its own lifetime: admission and
execution belong to ``submit``/``execution-submit`` and to the engine's runner
deadline, never to this wait. Reaching the window returns an honest
``wait-timeout`` envelope and the job keeps running, recovered by ``requestId`` or
``runId``.

``await`` is read-only in the strict sense:

* it never starts, resumes, retries or cancels anything, on timeout or otherwise;
* a transient service failure is recovered by re-reading the SAME durable
  ``runId`` (bounded reconnects), never by replaying a submission;
* stopping this wait (Ctrl-C, a closed terminal, a killed client, ``waitSeconds``
  expiry) only stops the *wait*. The named run ends through ``buddy cancel``
  (governed goals, with the owner's control file), ``execution-cancel`` (execution
  records), its own runner deadline, or a service stop (``buddy stop``/owner
  shutdown).
"""
from __future__ import annotations

import json
from pathlib import Path
import shlex
import threading
import time
from typing import Any, Callable

from ..protocol.transport import ServiceError, _call_service

# The wait window may never exceed the existing 24 h CLI wait maximum.
MAX_WAIT_SECONDS = 86400
MIN_WAIT_SECONDS = 1
DEFAULT_AWAIT_SECONDS = 86400
WAIT_SLICE_MS = 30000
MAX_RECONNECTS = 3

# ``reconciliation-needed`` is terminal for a wait: the task will not move again
# without an explicit retry, and no wait may relaunch it.
TERMINAL_STATUSES = frozenset({"completed", "failed", "cancelled", "reconciliation-needed"})

# Reported when a run reached ``completed`` but this call cannot deliver the
# promised result payload. The execution status stays visible in ``status``;
# the outcome/ok claim never says success without the result.
COMPLETED_WITHOUT_RESULT_OUTCOME = "completed-no-result"

_NOTE = (
    "This call waited inside one CLI invocation; no model polling and no heartbeat were used. "
    "A finished run is not acceptance: inspect the real artifacts and checks before the owner "
    "acknowledges with `buddy acknowledge` and the saved controlFile."
)


class WaitAbandoned(RuntimeError):
    """This wait was abandoned (Ctrl-C, closed client, stopped wait).

    The durable run keeps executing and stays recoverable; it ends through an
    explicit ``buddy cancel``/``execution-cancel``, its own runner execution
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
    """Real ``buddy`` commands that inspect/recover one existing run.

    The JSON selector is built with :func:`json.dumps` and every argument is
    quoted with :func:`shlex.quote`, so requestIds/runIds containing quotes,
    apostrophes or non-ASCII text survive ``shlex.split`` + ``json.loads``.
    ``status``/``get``/``result``/``cancel`` only accept a ``runId``: while the run
    ID is unknown, the only supported recovery command is ``buddy await`` by
    ``requestId``. Cancellation is deliberately not suggested as an automatic
    step: it needs the owner's control capability, so the action text names it.
    """
    selector = shlex.quote(_identity(request_id, run_id))
    if run_id:
        return [
            f"buddy status {selector}",
            f"buddy get {selector}",
            f"buddy await {selector}",
            f"buddy result {selector}",
        ]
    return [f"buddy await {selector}"]


def _recovery(request_id: str | None, run_id: str | None, reason: str) -> dict:
    if run_id:
        action = (
            "Inspect the SAME existing run with the commands below; `buddy status` reads the execution record and "
            "`buddy get` the governed goal view. Re-run `buddy await` with this runId to keep waiting on the same "
            "run: the wait never starts, resumes or cancels work. A governed goal ends only through `buddy cancel` "
            "with the current owner's saved controlFile; an infrastructure execution record ends through "
            "`execution-cancel`."
        )
    else:
        action = (
            "Read the runId from the commands below, then inspect it. `buddy await` by this requestId waits on an "
            "existing run and never starts work (NOT_FOUND means this requestId was never admitted; admit a governed "
            "goal with `buddy submit`, or an execution record with `execution-submit`, then await the same "
            "requestId). `status`/`get`/`result`/`cancel` need a runId."
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
    reconnects: int = 0,
) -> dict:
    """Build the caller-visible envelope.

    The success claim is strict and is decided here, once: ``ok`` is true only
    when the run's outcome is ``completed`` *and* this envelope actually carries
    the promised result payload *and* no error was recorded. A completed run
    whose result was not persisted, could not be read or came back malformed
    keeps its execution status in ``status`` but is reported as
    ``completed-no-result`` with ``ok=False`` and ``resultDelivered=False``,
    never as a successful complete result.
    """
    result_delivered = result is not None
    success = outcome == "completed" and result_delivered and error is None
    if outcome == "completed" and not success:
        outcome = COMPLETED_WITHOUT_RESULT_OUTCOME
    return {
        "runId": run.get("runId"),
        "requestId": run.get("requestId", request_id),
        "status": run.get("status"),
        **({"workflowState": run["workflowState"]} if run.get("workflowState") is not None else {}),
        "outcome": outcome,
        "ok": success,
        "resultAvailable": bool(run.get("resultAvailable")),
        "resultDelivered": result_delivered,
        # For a governed goal, cancellation/completion never implies that a
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
        "reconnects": reconnects,
        "result": result,
        "recovery": recovery,
        "error": error,
        "note": _NOTE,
    }


def _find_run(request_id: str, service: Callable[..., dict], state_dir: Path) -> dict | None:
    offset = 0
    while True:
        page = service("list", {"limit": 100, "offset": offset}, state_dir)
        runs = page.get("runs", []) if isinstance(page, dict) else []
        for run in runs:
            if run.get("requestId") == request_id:
                return run
        offset += len(runs)
        if not runs or offset >= page.get("total", offset):
            return None


def _result_undelivered(
    run: dict,
    *,
    request_id: str | None,
    run_id: str,
    waited_seconds: float,
    wait_seconds: int,
    recovery_reason: str,
    error: dict,
    reconnects: int,
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
        reconnects=reconnects,
    )


def _malformed_result_response(full: Any, run_id: str) -> dict | None:
    """Return an error when a ``result`` response cannot deliver this run's result.

    A response is usable only if it is an object for the same run that carries a
    nonempty result object (the engine persists exactly that shape). Anything
    else must not be allowed to become a successful complete envelope, so the
    caller falls back to the honest ``result-unavailable`` path.
    """
    if not isinstance(full, dict):
        return {"code": "INVALID_RESPONSE", "message": "The service returned a non-object result response"}
    response_run_id = full.get("runId")
    if isinstance(response_run_id, str) and response_run_id and response_run_id != run_id:
        return {"code": "RESULT_MISMATCH", "message": "The result response belongs to a different run"}
    payload = full.get("result")
    if payload is None:
        return {"code": "RESULT_MISSING", "message": "The service response did not contain the promised runner result"}
    if not isinstance(payload, dict) or not payload:
        return {"code": "INVALID_RESPONSE", "message": "The service returned a malformed runner result"}
    return None


def _result_command(run_id: str) -> str:
    return f"buddy result {shlex.quote(json.dumps({'runId': run_id}, ensure_ascii=False))}"


def _governed_get_command(run_id: str) -> str:
    return f"buddy get {shlex.quote(json.dumps({'runId': run_id, 'includeAudit': True}, ensure_ascii=False))}"


def _head_text(value, limit: int) -> str | None:
    if not isinstance(value, str):
        return None
    return value if len(value) <= limit else value[:limit]


def _governed_result_view(full: dict, run_id: str) -> dict | None:
    """The bounded default view of one governed result.

    The durable result keeps the whole runner payload, the effective manifest and the
    sealed output; normal await callers get this compact view plus the exact
    command that reads the full record back.
    """
    if not isinstance(full, dict) or full.get("workflowState") is None:
        # Infrastructure executions retain their opaque result payload.
        return None
    result = full.get("result")
    if not isinstance(result, dict):
        return None
    turn = result.get("turn") if isinstance(result.get("turn"), dict) else None
    outcome = turn.get("outcome") if isinstance(turn, dict) and isinstance(turn.get("outcome"), dict) else {}
    partial = result.get("partialWorkspaceSeal")
    seal = partial if isinstance(partial, dict) else result.get("workspaceSeal")
    seal = seal if isinstance(seal, dict) else None
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
                "cumulativePatch": ({**seal["cumulativePatch"], "changedPaths": (seal["cumulativePatch"].get("changedPaths") or [])[:32], "changedPathsTruncated": len(seal["cumulativePatch"].get("changedPaths") or []) > 32} if isinstance(seal.get("cumulativePatch"), dict) else None),
            }
        )
    from ..blackboard.evaluation.native_observations import failure_view
    final_text = result.get("finalText")
    remaining = outcome.get("remaining")
    disposition = outcome.get("disposition")
    return {
        "status": result.get("status"),
        "tokenUsage": full.get("tokenUsage"),
        "quotaFailure": failure_view(result, adapter=full.get("adapter")),
        "turn": None
        if turn is None
        else {
            "turnId": turn.get("turnId"),
            "resumeMode": turn.get("resumeMode"),
            "sessionId": turn.get("sessionId"),
            "disposition": disposition,
            "summary": _head_text(outcome.get("summary"), 2000),
            "remaining": [str(item)[:500] for item in remaining[:8]] if isinstance(remaining, list) else [],
        },
        "request": outcome.get("request") if disposition in ("assistance", "attention") else None,
        "artifacts": artifacts,
        "finalText": _head_text(final_text, 2000),
        "finalTextTruncated": isinstance(final_text, str) and len(final_text) > 2000,
        "logPaths": result.get("logPaths"),
        "processState": {"shutdownConfirmed": bool(full.get("shutdownConfirmed", (result.get("processState") or {}).get("shutdownConfirmed")))},
        "resultCommand": _result_command(run_id),
        "note": "Compact governed result; the full payload, manifests and sealed output stay durable.",
    }


def _boundary_command(method: str, params: dict) -> str:
    return f"buddy {method} {shlex.quote(json.dumps(params, ensure_ascii=False))}"


def _host_boundary(
    run: dict,
    *,
    request_id: str | None,
    run_id: str,
    waited_seconds: float,
    wait_seconds: int,
    service: Callable[..., dict],
    state_dir: Path,
    reconnects: int,
) -> dict:
    """Return one governed Host decision boundary as a structured checkpoint.

    A normal structured yield is neither goal completion nor an execution failure:
    the original task is unfinished and a Host decision (or continuation) is
    required. The wait ends here instead of blocking forever on a success-only
    event, and it reads the actual persisted turn result rather than inventing one.
    """
    full: Any = None
    error: dict | None = None
    if run.get("resultAvailable"):
        try:
            full = service("result", {"runId": run_id}, state_dir)
        except ServiceError as failure:
            error = {"code": getattr(failure, "code", "SERVICE_ERROR"), "message": str(failure)}
    result = full.get("result") if isinstance(full, dict) else None
    workflow = run.get("workflow") if isinstance(run.get("workflow"), dict) else {}
    routing = workflow.get("requestRouting") is True
    turn = result.get("turn") if isinstance(result, dict) else None
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
                    "reason": "...",
                },
            )
        )
    if routing:
        target = workflow.get("requestTargetRunId")
        target_params = {"targetRunId": target} if target and target != run_id else {}
        for mode, choice in (
            ("reroute", {"reroute": True}),
            ("configuration", {"configuration": {key: f"<{key}>" for key in ("adapter", "provider", "model", "effort")}}),
        ):
            commands.append(_boundary_command("continue", {
                "runId": run_id, "commandId": f"{run_id}:{mode}:{revision}",
                "controlFile": "<saved-controlFile>", "expectedRevision": revision,
                "input": "Host has checked the configuration; continue the authorized goal.",
                **target_params, **choice,
            }))
    else:
        commands.append(_boundary_command("continue", {
            "runId": run_id, "commandId": f"{run_id}:continue:{revision}",
            "controlFile": "<saved-controlFile>", "expectedRevision": revision,
            "input": "...", "helperPolicy": "keep",
        }))
    governed_view = _governed_result_view(full, run_id) if isinstance(full, dict) else None
    envelope = _envelope(
        run,
        request_id=request_id,
        outcome="waiting-host",
        waited_seconds=waited_seconds,
        wait_seconds=wait_seconds,
        result=governed_view if governed_view is not None else result,
        error=error,
        reconnects=reconnects,
    )
    envelope.update(
        {
            "ok": False,
            "goalComplete": False,
            "resultCompact": governed_view is not None,
            "resultCommand": _result_command(run_id),
            "workflowState": run.get("workflowState"),
            "workflow": workflow,
            "turn": (governed_view or {}).get("turn") if governed_view is not None else turn,
            "artifacts": (governed_view or {}).get("artifacts", []) if governed_view is not None else [],
            "request": request_view,
            "nextCommands": commands,
            "note": (
                "The original goal needs Host input. Inspect its sealed artifacts with get: an integrated current turn may be acknowledged directly, while partial work stays unverified. A routing boundary may have no "
                "coding turn or result yet. Use the current owner's saved controlFile with an applicable "
                "decision or continuation, then await the same runId."
            ),
        }
    )
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
) -> dict:
    """Follow one existing durable run with bounded event waits.

    Read-only: the only operations it may issue are ``status``, ``events``,
    ``watch``, ``wait`` and ``result``. It never starts, resumes, retries or
    cancels, so an abandoned or timed-out wait can only stop the wait itself.
    """
    reconnects = 0
    deadline = started + wait_seconds
    while True:
        if stop is not None and stop.is_set():
            raise WaitAbandoned(request_id, run_id)
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
                reconnects=reconnects,
            )
        if workflow_state is None:
            if run.get("resultAvailable"):
                break
        elif workflow_state in ("delivered", "accepted", "cancelled", "failed"):
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
                reconnects=reconnects,
            )
        timeout_ms = max(1, int(min(WAIT_SLICE_MS, remaining * 1000)))
        try:
            shutdown = run.get("workflowShutdown")
            if isinstance(shutdown, dict) and shutdown.get("unconfirmedRunIds"):
                # Parent revision need not change when an owned descendant stops.
                # A terminal task with unconfirmed shutdown can make task_wait
                # return immediately. Use the durable event cursor instead, with
                # a recheck after reading it so a concurrent stop is not missed.
                target = shutdown["unconfirmedRunIds"][0]
                events = service("events", {"runId": target, "limit": 1}, state_dir)
                head = events.get("head")
                if isinstance(head, bool) or not isinstance(head, int):
                    raise ServiceError("INVALID_RESPONSE", "The stopped-evidence wait requires an event cursor")
                run = service("status", {"runId": run_id}, state_dir)
                current = run.get("workflowShutdown")
                if isinstance(current, dict) and (
                    current.get("selfConfirmed") is not True or current.get("descendantsConfirmed") is not True
                ):
                    service("watch", {"runId": target, "after": head, "timeoutMs": timeout_ms}, state_dir)
                    run = service("status", {"runId": run_id}, state_dir)
            else:
                run = service("wait", {"runId": run_id, "afterRevision": run.get("revision"), "timeoutMs": timeout_ms}, state_dir)
        except ServiceError as failure:
            # A wait is read-only: a transient failure re-attaches by reading the
            # SAME durable runId. It never replays a submission or starts work.
            if reconnects >= MAX_RECONNECTS:
                return _envelope(
                    run,
                    request_id=request_id,
                    outcome="unavailable",
                    waited_seconds=clock() - started,
                    wait_seconds=wait_seconds,
                    recovery=_recovery(request_id, run_id, "The Buddy service could not be reached; the run stays durable"),
                    error={"code": getattr(failure, "code", "SERVICE_ERROR"), "message": str(failure)},
                    reconnects=reconnects,
                )
            reconnects += 1
            try:
                run = service("status", {"runId": run_id}, state_dir)
            except ServiceError as recover_failure:
                return _envelope(
                    run,
                    request_id=request_id,
                    outcome="unavailable",
                    waited_seconds=clock() - started,
                    wait_seconds=wait_seconds,
                    recovery=_recovery(request_id, run_id, "The service did not answer; await the same runId again later"),
                    error={"code": getattr(recover_failure, "code", "SERVICE_ERROR"), "message": str(recover_failure)},
                    reconnects=reconnects,
                )
            if not isinstance(run, dict) or run.get("runId") != run_id:
                return _envelope(
                    run if isinstance(run, dict) else {},
                    request_id=request_id,
                    outcome="unavailable",
                    waited_seconds=clock() - started,
                    wait_seconds=wait_seconds,
                    recovery=_recovery(request_id, run_id, "Re-attaching returned a different run; inspect before continuing"),
                    error={"code": "RECOVERY_MISMATCH", "message": "Re-attached runId differs from the original runId"},
                    reconnects=reconnects,
                )
            continue
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
                "the execution status is preserved. Read the run again with `buddy result` by runId, then check its "
                "logs before any manual recovery."
            ),
            error={
                "code": "RESULT_NOT_AVAILABLE",
                "message": "The terminal run has no persisted runner result, so no successful result can be reported",
            },
            reconnects=reconnects,
        )
    try:
        full = service("result", {"runId": run_id}, state_dir)
    except ServiceError as failure:
        return _result_undelivered(
            run,
            request_id=request_id,
            run_id=run_id,
            waited_seconds=clock() - started,
            wait_seconds=wait_seconds,
            recovery_reason=(
                "The run is terminal but its persisted result could not be read, so result delivery is unavailable and "
                "there is no successful result to report; the execution status is preserved. Re-read the result by runId "
                "(`buddy result`); do not relaunch the job because of this read failure."
            ),
            error={"code": getattr(failure, "code", "SERVICE_ERROR"), "message": str(failure)},
            reconnects=reconnects,
        )
    malformed = _malformed_result_response(full, run_id)
    if malformed is not None:
        return _result_undelivered(
            run,
            request_id=request_id,
            run_id=run_id,
            waited_seconds=clock() - started,
            wait_seconds=wait_seconds,
            recovery_reason=(
                "The run is terminal but the result response was unusable (non-object, missing result or a different "
                "run), so there is no successful result to report; the execution status is preserved. Re-read the result "
                "by runId; do not relaunch the job because of this response."
            ),
            error=malformed,
            reconnects=reconnects,
        )
    governed_view = _governed_result_view(full, run_id) if isinstance(full, dict) else None
    if governed_view is not None:
        # A late execution completion does not undo the Host's goal cancellation.
        goal_state = full.get("workflowState", run.get("workflowState"))
        outcome = goal_state if goal_state in ("cancelled", "failed") else full.get("status", run.get("status", "unknown"))
        envelope = _envelope(
            full,
            request_id=request_id,
            outcome=outcome,
            waited_seconds=clock() - started,
            wait_seconds=wait_seconds,
            result=governed_view,
            reconnects=reconnects,
        )
        envelope.update(
            {
                "resultCompact": True,
                "resultCommand": _result_command(run_id),
                "turn": governed_view["turn"],
                "artifacts": governed_view["artifacts"],
                "note": (
                    "The durable result keeps the full runner payload, manifests and sealed output; read it with "
                    f"{_result_command(run_id)} or {_governed_get_command(run_id)}."
                ),
            }
        )
        return envelope
    return _envelope(
        full,
        request_id=request_id,
        outcome=full.get("status", run.get("status", "unknown")),
        waited_seconds=clock() - started,
        wait_seconds=wait_seconds,
        result=full.get("result"),
        reconnects=reconnects,
    )


def await_run(
    params: dict,
    state_dir: Path,
    stop: threading.Event | None = None,
    clock: Callable[[], float] = time.monotonic,
    service: Callable[..., dict] = _call_service,
) -> dict:
    """Wait for an already existing durable run without starting or replaying anything.

    ``buddy await`` is the only CLI blocking call: it reads the durable run and
    reuses the same C-Two service and the same run ID. Its wait window is explicit
    and bounded (``waitSeconds``, default 86400, max 86400); reaching it is
    recoverable by awaiting the same run again. It never starts, resumes, retries
    or cancels work - not even on timeout - and a transient service failure is
    recovered by re-reading the same runId.
    """
    if not isinstance(params, dict):
        raise ServiceError("INVALID_ARGUMENT", "await requires an object of parameters")
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
            if not isinstance(run, dict) or not run.get("runId"):
                raise ServiceError("INVALID_RESPONSE", "The service did not return a run view")
            if request_id is not None and run.get("requestId") != request_id:
                raise ServiceError("CONFLICT", "runId and requestId do not belong to the same run")
        else:
            run = _find_run(request_id, service, state_dir)
            if run is None:
                raise ServiceError(
                    "NOT_FOUND",
                    "No existing run has this requestId; await never starts work. Admit a governed goal with "
                    "`buddy submit`, or an execution record with `execution-submit`, then await the same requestId.",
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
        )
    except KeyboardInterrupt:
        raise WaitAbandoned(request_id, run_id) from None
