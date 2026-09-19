"""CLI blocking delegation: one ``buddy run`` stays connected until the durable run ends.

Waiting is ordinary synchronous code on top of the engine's event wait
(``wait`` with its existing 30 s cap). There is no model call, no heartbeat and no
model-visible polling: the engine is event-driven and the facade only re-arms a
bounded wait slice until the run is terminal or this call's wait window is spent.

Three independent lifetimes:

* **DSH execution deadline** (``timeoutSeconds``): the durable job's own runner
  deadline, 10-86400 s, default 1800 s. A wait ending earlier never changes or
  cancels it.
* **Wait window** (``waitSeconds``): how long *this CLI call* stays connected. It
  defaults to the runner deadline plus the 60 s shutdown grace, bounded by the
  24 h CLI maximum (86400 s), so one invocation normally covers the whole job.
  Reaching it returns an honest ``wait-timeout`` envelope; the job keeps running
  and is recovered by requestId/runId.
* **Process lifetime**: the CLI process itself. ``buddy await`` only waits on an
  already existing run; it never starts work.

Cancellation boundary:
* stopping this wait (Ctrl-C, a closed terminal, a killed client, ``waitSeconds``
  expiry) only stops the *wait*; while the service is alive the owned dsh job keeps
  running and stays recoverable;
* the named owned run ends through ``buddy cancel``, its own runner execution
  deadline, or a service stop (``buddy stop`` / owner shutdown).

Recovery is keyed by ``requestId``: re-running the identical command recovers the
same run and never launches dsh again, because the durable engine treats
``requestId`` as an idempotency key. ``await_run`` waits on an already existing run
without starting anything.
"""
from __future__ import annotations

import json
import shlex
import threading
import time
from typing import Any, Callable

from .transport import ServiceError, call_service

# The wait window may never exceed the existing 24 h CLI wait maximum.
MAX_WAIT_SECONDS = 86400
MIN_WAIT_SECONDS = 1
SHUTDOWN_GRACE_SECONDS = 60
MIN_RUNNER_TIMEOUT_SECONDS = 10
MAX_RUNNER_TIMEOUT_SECONDS = 86400
DEFAULT_RUNNER_TIMEOUT_SECONDS = 1800
DEFAULT_AWAIT_SECONDS = 86400
WAIT_SLICE_MS = 30000
MAX_RECONNECTS = 3

# Only these fields are forwarded to the durable engine's idempotent start.
START_FIELDS = ("requestId", "task", "cwd", "model", "provider", "effort", "timeoutSeconds", "workspace")
TERMINAL_STATUSES = frozenset({"completed", "failed", "cancelled", "interrupted"})

# Reported when a run reached ``completed`` but this call cannot deliver the
# promised result payload. The execution status stays visible in ``status``;
# the outcome/ok claim never says success without the result.
COMPLETED_WITHOUT_RESULT_OUTCOME = "completed-no-result"

_NOTE = (
    "This call waited inside one CLI invocation; no model polling and no heartbeat were used. "
    "A finished runner is not acceptance: inspect the real artifacts and checks before `buddy acknowledge`."
)


class WaitAbandoned(RuntimeError):
    """This wait was abandoned (Ctrl-C, closed client, stopped wait).

    The owned run keeps executing and stays recoverable; it ends through an
    explicit ``buddy cancel``, its own runner execution deadline, or a service
    stop.
    """

    def __init__(self, request_id: str | None = None, run_id: str | None = None):
        super().__init__("The blocking wait was abandoned; the owned run was not cancelled")
        self.request_id = request_id
        self.run_id = run_id


def default_wait_seconds(timeout_seconds: int) -> int:
    """Wait window that normally covers one whole run.

    The runner deadline plus the shutdown grace, bounded by the 24 h CLI maximum.
    There is no MCP-derived cap here: a multi-hour ``timeoutSeconds`` gets a
    multi-hour wait window, and a caller can always pass ``waitSeconds`` explicitly.
    """
    return min(MAX_WAIT_SECONDS, timeout_seconds + SHUTDOWN_GRACE_SECONDS)


def validate_wait_seconds(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not (MIN_WAIT_SECONDS <= value <= MAX_WAIT_SECONDS):
        raise ServiceError("INVALID_ARGUMENT", f"waitSeconds must be an integer between {MIN_WAIT_SECONDS} and {MAX_WAIT_SECONDS}")
    return value


def validate_run_params(params: dict) -> tuple[dict, int, int]:
    """Validate ``buddy run`` input; returns (start params, waitSeconds, runner timeout).

    Validation happens before the service is contacted, so an invalid request can
    never launch dsh. A runner deadline longer than this call's wait window is
    allowed on purpose: the durable job's lifetime is independent of the wait
    lifetime, and the returned envelope labels the wait limit honestly.
    """
    if not isinstance(params, dict):
        raise ServiceError("INVALID_ARGUMENT", "run requires an object of parameters")
    unknown = sorted(set(params) - set(START_FIELDS) - {"waitSeconds"})
    if unknown:
        raise ServiceError("INVALID_ARGUMENT", f"Unknown run parameter: {unknown[0]}")
    request_id = params.get("requestId")
    if not isinstance(request_id, str) or not request_id.strip() or len(request_id) > 128:
        raise ServiceError("INVALID_ARGUMENT", "requestId must contain 1-128 characters")
    task = params.get("task")
    if not isinstance(task, str) or not task.strip() or len(task.encode()) > 1024 * 1024:
        raise ServiceError("INVALID_ARGUMENT", "task must contain 1-1048576 bytes")
    cwd = params.get("cwd")
    if not isinstance(cwd, str) or not cwd.startswith("/"):
        raise ServiceError("INVALID_ARGUMENT", "cwd must be an absolute path")
    for name in ("model", "provider", "effort"):
        value = params.get(name)
        if value is not None and (not isinstance(value, str) or not value.strip() or len(value) > 256 or "\0" in value):
            raise ServiceError("INVALID_ARGUMENT", f"Invalid {name}")
    timeout_seconds = params.get("timeoutSeconds", DEFAULT_RUNNER_TIMEOUT_SECONDS)
    if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, int) or not (
        MIN_RUNNER_TIMEOUT_SECONDS <= timeout_seconds <= MAX_RUNNER_TIMEOUT_SECONDS
    ):
        raise ServiceError(
            "INVALID_ARGUMENT",
            f"timeoutSeconds must be an integer between {MIN_RUNNER_TIMEOUT_SECONDS} and {MAX_RUNNER_TIMEOUT_SECONDS}",
        )
    workspace = params.get("workspace", True)
    if not isinstance(workspace, bool):
        raise ServiceError("INVALID_ARGUMENT", "workspace must be a boolean")
    wait_seconds = validate_wait_seconds(params.get("waitSeconds", default_wait_seconds(timeout_seconds)))
    start_params = {key: params[key] for key in START_FIELDS if key in params}
    return start_params, wait_seconds, timeout_seconds


def _identity(request_id: str | None, run_id: str | None) -> str:
    """One JSON selector string for a real CLI command; ``runId`` wins over ``requestId``."""
    selector = {"runId": run_id} if run_id else {"requestId": request_id}
    return json.dumps(selector, ensure_ascii=False, separators=(",", ":"))


def recovery_commands(request_id: str | None, run_id: str | None) -> list[str]:
    """Real ``buddy`` commands that inspect/recover one existing run.

    The JSON selector is built with :func:`json.dumps` and every argument is
    quoted with :func:`shlex.quote`, so requestIds/runIds containing quotes,
    apostrophes or non-ASCII text survive ``shlex.split`` + ``json.loads``.
    ``status``/``result``/``cancel`` only accept a ``runId``: while the run ID is
    unknown, the only supported recovery command is ``buddy await`` by
    ``requestId`` (re-running the identical ``buddy run`` also recovers the run).
    """
    selector = shlex.quote(_identity(request_id, run_id))
    if run_id:
        return [
            f"buddy status {selector}",
            f"buddy await {selector}",
            f"buddy result {selector}",
            f"buddy cancel {selector}",
        ]
    return [f"buddy await {selector}"]


def _recovery(request_id: str | None, run_id: str | None, reason: str) -> dict:
    if run_id:
        action = (
            "Inspect the SAME existing run with the commands below. If it is still active, re-run "
            "`buddy await` with this runId (or re-run the identical `buddy run` requestId). The service "
            "recovers the same existing run and never launches dsh twice."
        )
    else:
        action = (
            "Recover the SAME run with the command below: `buddy await` by this requestId waits on an existing "
            "run and never starts work (NOT_FOUND means this requestId was never started; re-running the "
            "identical `buddy run` starts or recovers it). `status`, `result` and `cancel` need a runId, so read "
            "it from the await or run envelope first."
        )
    return {
        "reason": reason,
        "requestId": request_id,
        "runId": run_id,
        "action": action,
        "commands": recovery_commands(request_id, run_id),
    }


def _limitation(runner_deadline_seconds: int | None, wait_seconds: int) -> str | None:
    if runner_deadline_seconds is None or runner_deadline_seconds + SHUTDOWN_GRACE_SECONDS <= wait_seconds:
        return None
    return (
        f"This call waits up to {wait_seconds}s, less than the runner deadline {runner_deadline_seconds}s plus "
        f"{SHUTDOWN_GRACE_SECONDS}s shutdown grace, so one call cannot cover the whole job. The durable run is not "
        "cancelled, is not an execution failure and must not be relaunched: run `buddy await` with this runId "
        "(or re-run the identical `buddy run` requestId) to keep waiting on the same run."
    )


def _envelope(
    run: dict,
    *,
    request_id: str | None,
    outcome: str,
    waited_seconds: float,
    wait_seconds: int,
    runner_deadline_seconds: int | None = None,
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
    covers = None if runner_deadline_seconds is None else runner_deadline_seconds + SHUTDOWN_GRACE_SECONDS <= wait_seconds
    return {
        "runId": run.get("runId"),
        "requestId": run.get("requestId", request_id),
        "status": run.get("status"),
        "outcome": outcome,
        "ok": success,
        "resultAvailable": bool(run.get("resultAvailable")),
        "resultDelivered": result_delivered,
        "shutdownConfirmed": run.get("shutdownConfirmed") is True,
        "acceptedAt": run.get("acceptedAt"),
        "revision": run.get("revision"),
        "createdAt": run.get("createdAt"),
        "updatedAt": run.get("updatedAt"),
        "cwd": run.get("cwd"),
        "logPaths": run.get("logPaths"),
        "waitedSeconds": round(max(waited_seconds, 0.0), 1),
        "waitSeconds": wait_seconds,
        "maxWaitSeconds": MAX_WAIT_SECONDS,
        "runnerDeadlineSeconds": runner_deadline_seconds,
        "waitCoversRunnerDeadline": covers,
        "timedOut": outcome == "wait-timeout",
        "reconnects": reconnects,
        "result": result,
        "recovery": recovery,
        "limitation": _limitation(runner_deadline_seconds, wait_seconds),
        "error": error,
        "note": _NOTE,
    }


def _find_run(request_id: str, service: Callable[..., dict], state_dir) -> dict | None:
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
    runner_deadline_seconds: int | None,
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
        runner_deadline_seconds=runner_deadline_seconds,
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


def _wait_until_terminal(
    run: dict,
    *,
    request_id: str | None,
    run_id: str,
    start_params: dict | None,
    wait_seconds: int,
    runner_deadline_seconds: int | None,
    started: float,
    clock: Callable[[], float],
    service: Callable[..., dict],
    state_dir,
    stop: threading.Event | None,
) -> dict:
    reconnects = 0
    deadline = started + wait_seconds
    while True:
        if stop is not None and stop.is_set():
            raise WaitAbandoned(request_id, run_id)
        if run.get("resultAvailable"):
            break
        if run.get("status") in TERMINAL_STATUSES:
            break
        remaining = deadline - clock()
        if remaining <= 0:
            return _envelope(
                run,
                request_id=request_id,
                outcome="wait-timeout",
                waited_seconds=clock() - started,
                wait_seconds=wait_seconds,
                runner_deadline_seconds=runner_deadline_seconds,
                recovery=_recovery(
                    request_id,
                    run_id,
                    "This call's wait window ended while the durable run was still active. That is a wait/connection "
                    "limit, not an execution failure; the owned job was neither cancelled nor marked failed.",
                ),
                reconnects=reconnects,
            )
        timeout_ms = max(1, int(min(WAIT_SLICE_MS, remaining * 1000)))
        try:
            run = service("wait", {"runId": run_id, "afterRevision": run.get("revision"), "timeoutMs": timeout_ms}, state_dir)
        except ServiceError as failure:
            if start_params is None or reconnects >= MAX_RECONNECTS:
                return _envelope(
                    run,
                    request_id=request_id,
                    outcome="unavailable",
                    waited_seconds=clock() - started,
                    wait_seconds=wait_seconds,
                    runner_deadline_seconds=runner_deadline_seconds,
                    recovery=_recovery(request_id, run_id, "The Buddy service could not be reached; the owned run is durable"),
                    error={"code": getattr(failure, "code", "SERVICE_ERROR"), "message": str(failure)},
                    reconnects=reconnects,
                )
            reconnects += 1
            try:
                # Identical input + same requestId recovers the existing run; it never relaunches dsh.
                run = service("start", dict(start_params), state_dir)
            except ServiceError as recover_failure:
                return _envelope(
                    run,
                    request_id=request_id,
                    outcome="unavailable",
                    waited_seconds=clock() - started,
                    wait_seconds=wait_seconds,
                    runner_deadline_seconds=runner_deadline_seconds,
                    recovery=_recovery(request_id, run_id, "The service did not answer; re-run `buddy await` with this runId later"),
                    error={"code": getattr(recover_failure, "code", "SERVICE_ERROR"), "message": str(recover_failure)},
                    reconnects=reconnects,
                )
            if run.get("runId") != run_id:
                return _envelope(
                    run,
                    request_id=request_id,
                    outcome="unavailable",
                    waited_seconds=clock() - started,
                    wait_seconds=wait_seconds,
                    runner_deadline_seconds=runner_deadline_seconds,
                    recovery=_recovery(request_id, run.get("runId"), "Recovery returned a different run; inspect before continuing"),
                    error={"code": "RECOVERY_MISMATCH", "message": "Recovered runId differs from the original runId"},
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
            runner_deadline_seconds=runner_deadline_seconds,
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
            runner_deadline_seconds=runner_deadline_seconds,
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
            runner_deadline_seconds=runner_deadline_seconds,
            recovery_reason=(
                "The run is terminal but the result response was unusable (non-object, missing result or a different "
                "run), so there is no successful result to report; the execution status is preserved. Re-read the result "
                "by runId; do not relaunch the job because of this response."
            ),
            error=malformed,
            reconnects=reconnects,
        )
    return _envelope(
        full,
        request_id=request_id,
        outcome=full.get("status", run.get("status", "unknown")),
        waited_seconds=clock() - started,
        wait_seconds=wait_seconds,
        runner_deadline_seconds=runner_deadline_seconds,
        result=full.get("result"),
        reconnects=reconnects,
    )


def run_blocking(
    params: dict,
    state_dir: str | None = None,
    stop: threading.Event | None = None,
    clock: Callable[[], float] = time.monotonic,
    service: Callable[..., dict] = call_service,
) -> dict:
    """Start or recover one durable run, wait inside this CLI call, return its envelope.

    ``stop`` is set when the caller abandons the wait (Ctrl-C or a test's
    disconnect simulation); the wait then ends without touching the owned job.
    ``service``/``clock`` are injectable for tests.
    """
    start_params, wait_seconds, timeout_seconds = validate_run_params(params)
    request_id = start_params.get("requestId")
    if stop is not None and stop.is_set():
        raise WaitAbandoned(request_id, None)
    started = clock()
    run = service("start", dict(start_params), state_dir)
    if not isinstance(run, dict) or not run.get("runId"):
        raise ServiceError("INVALID_RESPONSE", "The service did not return a runId")
    return _wait_until_terminal(
        run,
        request_id=request_id,
        run_id=run["runId"],
        start_params=start_params,
        wait_seconds=wait_seconds,
        runner_deadline_seconds=timeout_seconds,
        started=started,
        clock=clock,
        service=service,
        state_dir=state_dir,
        stop=stop,
    )


def await_run(
    params: dict,
    state_dir: str | None = None,
    stop: threading.Event | None = None,
    clock: Callable[[], float] = time.monotonic,
    service: Callable[..., dict] = call_service,
) -> dict:
    """Wait for an already existing durable run without starting or replaying anything.

    ``buddy await`` is the CLI path for jobs whose deadline exceeds one
    invocation's wait window: it reuses the same C-Two service and the same
    durable run ID. Its wait window is explicit and bounded (``waitSeconds``,
    default 86400, max 86400); reaching it is recoverable by awaiting the same run
    again and never relaunches work.
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
                "No existing run has this requestId; await never starts work. "
                "Start it with `buddy run` first, then await the same requestId.",
            )
    return _wait_until_terminal(
        run,
        request_id=run.get("requestId", request_id),
        run_id=run["runId"],
        start_params=None,
        wait_seconds=wait_seconds,
        runner_deadline_seconds=None,
        started=started,
        clock=clock,
        service=service,
        state_dir=state_dir,
        stop=stop,
    )
