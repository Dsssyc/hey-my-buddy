"""One process owns one execution, its native work and durable result delivery."""
from __future__ import annotations

import signal

# Preserve stops received while the execution imports its runtime and reads its
# private startup material. Importing this module as a library changes no handler.
_early_signals = []

def _defer_signal(sig, _frame):
    _early_signals.append(sig)

if __name__ == "__main__":
    for _signal in (signal.SIGTERM, signal.SIGINT,
                    *((signal.SIGBREAK,) if hasattr(signal, "SIGBREAK") else ())):
        signal.signal(_signal, _defer_signal)

import argparse
import json
import math
import os
import sys
import threading
import time
from pathlib import Path

from ...errors import BoardError
from ...protocol.client import BoardClient
from ...protocol import activity as activity_module
from ...protocol.stop import WRAP_UP_INITIATORS
from ..harnesses.registry import ExecutionContext
from ..roles import controller as role_seam
from .receipts import STARTUP, DELIVERY, read_receipt, write_receipt, deliver_receipt
from .worker import (fsync_json, _now, classify_termination, PREFLIGHT_RETRY_SECONDS,
                     PREFLIGHT_MAX_RETRY_SECONDS, TERMINATION_HARNESS_ERROR,
                     TERMINATION_WRAP_UP, TERMINATION_GRACE_EXPIRED,
                     TERMINATION_FORCED_STOP, TERMINATION_CONSOLE_STOP,
                     TERMINATION_SERVICE_STOP, TERMINATION_UNKNOWN_STOP)

STOP = "execution-stop.json"


class Execution:
    def __init__(self, claim: dict, state_dir: Path, worker_id: str, nonce: str, *,
                 client=None, lease_seconds: int = 120, log=None):
        from ...protocol.rpc_config import configure_server, configure_client
        configure_server(state_dir)
        configure_client(state_dir)
        self.claim, self.state_dir, self.worker_id, self.nonce = claim, Path(state_dir), worker_id, nonce
        self.client = client or BoardClient(self.state_dir, autostart=False)
        self.lease_seconds = lease_seconds
        self._log = log or (lambda message: None)
        self.control = _ExecutionControl(self)
        self._hints: dict = {}
        self._hint_lock = threading.Lock()
        self._flush_lock = threading.Lock()
        self.directory = self.state_dir / "attempts" / claim["task"]["taskId"] / claim["attempt"]["attemptId"]

    def log(self, message):
        self._log(f"[execution {self.claim['attempt']['attemptId']}] {message}")

    def request_stop(self, initiator="unknown", grace_deadline=None):
        self.control.request_stop(initiator, grace_deadline)

    def stop_requested(self):
        return self.control.cancel_view is not None

    def hint(self, **data):
        with self._hint_lock:
            self._hints.update(data)

    def flush_hints(self):
        with self._flush_lock:
            with self._hint_lock:
                pending = dict(self._hints)
            if not pending:
                return
            attempt = self.claim["attempt"]
            try:
                self.client.progress(self.worker_id, attempt["attemptId"], attempt["generation"],
                                     self.nonce, data=pending)
            except Exception:
                return
            with self._hint_lock:
                for key, value in pending.items():
                    if self._hints.get(key) == value:
                        self._hints.pop(key)

    def run(self):
        receipt = self.execute()
        return deliver_receipt(self.client, receipt, self.directory, log=self.log,
                               flush_hints=self.flush_hints)

    def execute(self) -> dict:
        """Run one attempt and always return a durable receipt.

        The whole body is guarded: if anything fails after the adapter was started,
        the owned process group is terminated and a receipt is still written, so a
        failure can never leave an orphaned child with no record.
        """
        claim = self.claim
        started_at = time.monotonic()
        attempt = claim["attempt"]
        task = claim["task"]
        spec = task["spec"]
        directory = self.state_dir / "attempts" / task["taskId"] / attempt["attemptId"]
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(directory, 0o700)
        pending = read_receipt(directory)
        if pending is not None:
            return pending
        # The holder records the live child handle the instant it exists. Recovery
        # decisions are made from that handle — never from whether a marker file
        # happened to be written, which can fail after a child is already running.
        holder: dict = {"handle": None, "adapter": None, "started": False, "start_invoked": False}
        try:
            return self._execute_guarded(claim, task, spec, attempt, directory, holder)
        except BaseException as error:  # noqa: BLE001 - every failure becomes a receipt
            handle = holder.get("handle")
            implementation = holder.get("adapter")
            shutdown_confirmed = holder.get("confirmed_stopped") is True
            if not shutdown_confirmed and handle is not None and implementation is not None:
                try:
                    role_seam.worker_cancel(implementation, handle)
                    handle.wait(timeout=15)
                except Exception as cleanup_error:  # noqa: BLE001 - keep the honest default
                    self.log(f"could not confirm this attempt's child stopped: {cleanup_error!r}")
                try:
                    shutdown_confirmed = handle.shutdown_confirmed() is True
                except Exception as proof_error:  # noqa: BLE001 - an unreadable proof is unknown
                    self.log(f"could not inspect the native stop proof: {proof_error!r}")
            elif not shutdown_confirmed and not holder.get("start_invoked"):
                # Validation failed before start() could spawn anything.
                shutdown_confirmed = True
            self.log(
                f"execution failed after the attempt started: {error!r} "
                f"(shutdownConfirmed={shutdown_confirmed})"
            )
            return self.receipt(
                claim,
                {
                    "status": "failed",
                    "result": ({"status": "error", "code": error.code,
                                "usage": {"elapsedMs": round((time.monotonic() - started_at) * 1000),
                                          "toolCalls": None, "bytesRead": None}}
                               if isinstance(error, BoardError) and not holder.get("started") else None),
                    "error": f"worker failure: {error!r}",
                    "exitCode": None,
                    "signal": None,
                    "shutdownConfirmed": shutdown_confirmed,
                    "artifacts": [],
                    "terminationReason": TERMINATION_HARNESS_ERROR,
                },
                directory,
            )
        finally:
            self.control.stop()

    def _execute_guarded(
        self, claim: dict, task: dict, spec: dict, attempt: dict, directory: Path, holder: dict
    ) -> dict:
        from ...blackboard.service.harness_health import HARNESSES
        from ..harnesses.runtime_selection import bound

        name = spec['adapter']
        review = name == 'decision' and (claim.get('decisionInput') or {}).get('routingMode', 'review') == 'review'
        if name == 'decision':
            name = (claim.get('decisionInput') or {}).get('buddy', {}).get('harness')
        renewal = self.control
        holder['preflightRenewal'] = renewal
        renewal.start()
        try:
            if name not in HARNESSES:
                return self._execute_selected(claim, task, spec, attempt, directory, holder)
            history = []
            record = None
            for retry in (False, True):
                unknown_reads = 0
                def cancelled(view):
                    return self._preflight_cancelled(claim, directory, record, view)
                while True:
                    if renewal.cancel_view is not None or self.stop_requested():
                        return cancelled(renewal.cancel_view)
                    command_id = f"harness-{attempt['attemptId']}-{int(retry)}"
                    if unknown_reads:
                        command_id += f"-unknown-{unknown_reads}"
                    try:
                        response = self.client.call('harness_prepare', {
                            'workerId': self.worker_id, 'attemptId': attempt['attemptId'], 'generation': attempt['generation'],
                            'nonce': self._nonce(), 'commandId': command_id, 'retry': retry and not unknown_reads,
                            'healthRevision': record.get('revision') if record else None,
                        })
                    except BoardError as error:
                        if error.code == 'ATTEMPT_FINISHED':
                            view = renewal.cancel_view
                            if view is None:
                                try:
                                    view = self.client.call('task_get', {'runId': attempt['taskId']})['task']
                                except Exception:
                                    raise error
                            if view.get('cancelRequested'):
                                return cancelled(view)
                        if error.code not in ('SERVICE_UNAVAILABLE', 'SERVICE_START_TIMEOUT'):
                            raise
                        # The request may already be committed: replay its exact identity.
                        renewal.wait(min(PREFLIGHT_MAX_RETRY_SECONDS,
                                         PREFLIGHT_RETRY_SECONDS * 2 ** min(unknown_reads, 4)))
                        continue
                    record = response['harness']
                    if record is None or (record.get('status') != 'unknown'
                                          and (record.get('lastProbe') or {}).get('status') != 'unknown'):
                        break
                    unknown_reads += 1
                    renewal.wait(min(PREFLIGHT_MAX_RETRY_SECONDS,
                                     PREFLIGHT_RETRY_SECONDS * 2 ** min(unknown_reads - 1, 4)))
                if renewal.cancel_view is not None or self.stop_requested():
                    return cancelled(renewal.cancel_view)
                history.append({'retry': retry, 'harness': record})
                if record is None or not record.get('available'):
                    if not retry:
                        continue
                    if review:
                        return self._review_unavailable(claim, directory, history, 'router-unavailable')
                    return self.receipt(claim, {'status': 'failed', 'result': {'harness': record, 'harnessAttempts': history},
                        'error': 'HARNESS_UNAVAILABLE: ' + str((record or {}).get('remedy') or 'Run hey-my-buddy harnesses with refresh:true'),
                        'exitCode': None, 'signal': None, 'shutdownConfirmed': True, 'artifacts': [],
                        'terminationReason': TERMINATION_HARNESS_ERROR}, directory)
                holder['harnessHistory'] = history
                holder['harnessRetry'] = retry
                try:
                    with bound([record], environment={**os.environ, "HMB_STATE_DIR": str(self.state_dir)}):
                        return self._execute_selected(claim, task, spec, attempt, directory, holder)
                except (OSError, BoardError) as error:
                    if (isinstance(error, BoardError) and error.code == 'ATTEMPT_FINISHED'
                            and (renewal.cancel_view is not None or self.stop_requested())):
                        return cancelled(renewal.cancel_view)
                    # A start() error with no returned handle may still follow spawn.
                    # Retry only if start was never called or the previous child has
                    # independently produced complete stop proof.
                    if holder.get('start_invoked') and not holder.get('confirmed_stopped'):
                        raise
                    startup_failure = (isinstance(error, BoardError) and error.code in {'ADAPTER_UNAVAILABLE', 'HARNESS_UNAVAILABLE', 'HARNESS_PREMODEL_FAILED'})
                    startup_failure = startup_failure or (isinstance(error, OSError) and error.filename in record.get('command', []))
                    if retry and not holder.get('started') and startup_failure:
                        self._harness_failed(attempt, record)
                        if review:
                            return self._review_unavailable(claim, directory, history, 'router-unavailable')
                    if retry or holder.get('started') or not startup_failure:
                        raise
            raise AssertionError('unreachable harness retry')
        finally:
            renewal.stop()
            renewal.join()
            holder.pop('preflightRenewal', None)

    def _preflight_cancelled(self, claim, directory, record, view):
        return self.receipt(claim, {'status': 'cancelled', 'result': {'harness': record} if record else None,
            'error': None, 'exitCode': None, 'signal': None, 'shutdownConfirmed': True,
            'artifacts': [], 'terminationReason': role_seam.stop_termination_reason(
                shutdown_confirmed=True, stop_initiator=(view or {}).get('stopInitiator'))}, directory)

    def _review_unavailable(self, claim, directory, history, code):
        from ...blackboard.routing.router import routing_facts
        return self.receipt(claim, {'status': 'failed', 'result': {
            'status': 'error', 'code': 'router-review-unavailable', 'reasonCode': code,
            'reason': 'The review harness is unavailable',
            'modelStarted': False, 'harnessAttempts': history,
            'budget': (claim.get('decisionInput') or {}).get('budget'),
            **routing_facts(claim.get('decisionInput') or {})},
            'error': code, 'exitCode': None, 'signal': None, 'shutdownConfirmed': True,
            'artifacts': [], 'terminationReason': TERMINATION_HARNESS_ERROR}, directory)

    def _harness_failed(self, attempt, record):
        self.client.call('harness_prepare', {'workerId': self.worker_id, 'attemptId': attempt['attemptId'],
            'generation': attempt['generation'], 'nonce': self._nonce(), 'failedOnly': True,
            'healthRevision': record['revision'], 'commandId': f"harness-failed-{attempt['attemptId']}"})

    def _execute_selected(
        self, claim: dict, task: dict, spec: dict, attempt: dict, directory: Path, holder: dict
    ) -> dict:
        from ...install import runtime

        context = ExecutionContext(
            task_id=task["taskId"],
            attempt_id=attempt["attemptId"],
            generation=attempt["generation"],
            spec=spec,
            directory=directory,
            runtime=runtime.resolve_runtime(),
            environment={**os.environ, "HMB_STATE_DIR": str(self.state_dir)},
            lease_seconds=self.lease_seconds,
            decision_input=claim.get("decisionInput"),
            # The service-owned turn identity and bounded context; the adapter stages
            # them for the runner. The scoped credential is written to a private file
            # and exported by path, never into the model-visible turn input.
            turn=claim.get("turn") if isinstance(claim.get("turn"), dict) else None,
            agent_credential=claim.get("agentCredential") or None,
        )
        implementation = role_seam.worker_executor(spec["adapter"])
        renewal = holder['preflightRenewal']
        preparation = role_seam.prepare_worker_run(implementation, context)
        unknown_reads = 0
        while preparation.error is not None and preparation.error.code == 'router-input-unknown':
            self._wait_control_observation(claim, renewal, preparation.error.code, unknown_reads)
            unknown_reads += 1
            preparation = role_seam.prepare_worker_run(implementation, context)
        if not preparation.available:
            return self.receipt(
                claim,
                {
                    "status": "failed",
                    "result": None,
                    "error": preparation.reason,
                    "exitCode": None,
                    "signal": None,
                    "shutdownConfirmed": True,
                    "artifacts": [],
                    "terminationReason": TERMINATION_HARNESS_ERROR,
                },
                directory,
            )
        if preparation.error is not None:
            error = preparation.error
            if holder.get('harnessHistory') and error.code == 'HARNESS_UNAVAILABLE':
                # The captured BoardError itself: the guarded harness retry above
                # matches on this code, so the original object and code must reach it.
                raise error
            return self.receipt(
                claim,
                {
                    "status": "failed",
                    "result": {"status": "error", "code": error.code,
                               "stopEvidence": {"shutdownConfirmed": True,
                                                "native": {"shutdownConfirmed": True}}},
                    "error": f"{error.code}: {error.message}",
                    "exitCode": None,
                    "signal": None,
                    "shutdownConfirmed": True,
                    "artifacts": [],
                    "terminationReason": TERMINATION_HARNESS_ERROR,
                },
                directory,
            )
        started = time.monotonic()
        holder["adapter"] = implementation
        # A previous pre-model retry may have proved its own child stopped. That
        # proof cannot cover this start(): it might spawn before raising a handle.
        holder.pop("confirmed_stopped", None)
        holder.pop("live_release_attempted", None)
        holder.pop("handle", None)
        holder["start_invoked"] = True
        handle = role_seam.worker_start(implementation, context)
        holder["handle"] = handle
        holder["started"] = True
        renewal.attach(handle, implementation)
        self.hint(waitingReason=None)
        timeout_seconds = context.timeout_seconds
        deadline = started + timeout_seconds if timeout_seconds is not None else None
        timed_out = False
        while True:
            if handle.wait(timeout=0.25) is not None:
                break
            if (handle.grace_deadline is not None and time.monotonic() >= handle.grace_deadline
                    and not handle.force_stop_requested):
                handle.grace_expired = True
                self.log("Host stop grace reached; forcing the owned run to stop")
                role_seam.worker_cancel(implementation, handle)
            if deadline is not None and time.monotonic() >= deadline:
                timed_out = True
                self.log("Router budget reached; cancelling this owned process group")
                role_seam.worker_cancel(implementation, handle)
                handle.wait(timeout=10)
                break
        unknown_reads = 0
        while True:
            try:
                outcome = role_seam.worker_collect(implementation, handle, context)
                break
            except BoardError as error:
                if error.code != 'router-input-unknown':
                    raise
                holder['confirmed_stopped'] = True
                self._wait_control_observation(claim, renewal, error.code, unknown_reads)
                unknown_reads += 1
        holder["confirmed_stopped"] = outcome.shutdown_confirmed is True
        result = getattr(handle, "result", None)
        if result is not None and result.activity is not None:
            self.hint(activity=result.activity.value)
        # A Router projection must retain a failure to preserve its native evidence.
        retention_failure = getattr(handle, "evidence_retention_failure", None)
        if isinstance(retention_failure, dict) and isinstance(outcome.result, dict):
            outcome.result = {**outcome.result, "evidenceRetention": retention_failure}
        if (holder.get('harnessHistory') and not timed_out
                and not handle.cancel_requested and outcome.shutdown_confirmed and outcome.status == 'failed'
                and isinstance(outcome.result, dict) and outcome.result.get('modelStarted') is False
                and outcome.result.get('code') in {'harness-unavailable', 'invalid-native-result', 'invalid-protocol', 'transport-error', 'native-rpc-error', 'protocol-error', 'native-exit', 'connection-closed'}):
            holder['started'] = False
            holder['confirmed_stopped'] = True
            holder.pop('handle', None)
            raise BoardError('HARNESS_PREMODEL_FAILED', 'The native protocol failed before any model input was sent')
        if timed_out:
            outcome.status = "failed"
            outcome.error = f"the Router budget of {timeout_seconds}s was reached before the adapter finished"
            if isinstance(outcome.result, dict):
                outcome.result = {**outcome.result, "status": "error", "code": "router-budget-exhausted"}
        from .partial_outputs import enrich
        enrich(context, outcome)
        # The genuine reason this attempt stopped. It is recorded with the receipt,
        # so a cancelled task, an expired deadline, a harness failure and a completed
        # turn stay distinguishable even after a transport outage.
        termination_reason = classify_termination(
            outcome,
            timed_out=timed_out,
            cancel_requested=bool(handle.cancel_requested),
            stop_initiator=handle.stop_initiator,
            grace_expired=handle.grace_expired,
        )
        if termination_reason in (TERMINATION_WRAP_UP, TERMINATION_GRACE_EXPIRED, TERMINATION_FORCED_STOP,
                                  TERMINATION_CONSOLE_STOP, TERMINATION_SERVICE_STOP, TERMINATION_UNKNOWN_STOP):
            outcome.status = "cancelled"
        if isinstance(outcome.result, dict) and "partialOutput" in outcome.result:
            outcome.result["partialOutput"]["reason"] = termination_reason
        report = outcome.to_report()
        if holder.get('harnessHistory'):
            report['result'] = {**(report.get('result') or {}), 'harnessAttempts': holder['harnessHistory']}
        report["terminationReason"] = termination_reason
        report["elapsedSeconds"] = round(time.monotonic() - started, 1)
        log_paths = (context.log_paths() if spec["adapter"] == "command" else {
            "stdout": str(directory / "execution.stdout.log"),
            "stderr": str(directory / "execution.stderr.log")})
        report["logPaths"] = {name: path for name, path in log_paths.items() if Path(path).is_file()}
        report["runtimeIdentity"] = context.runtime.get("identity")
        return self.receipt(claim, report, directory)

    def _wait_control_observation(self, claim, renewal, reason, retry):
        attempt = claim['attempt']
        self.hint(waitingReason=reason)
        renewal.wait(min(PREFLIGHT_MAX_RETRY_SECONDS, PREFLIGHT_RETRY_SECONDS * 2 ** min(retry, 4)))
        if renewal.cancel_view is not None or self.stop_requested():
            raise BoardError('ATTEMPT_FINISHED', 'Stop requested while waiting for a control observation')

    def _cleanup_attempt_credentials(self, claim: dict, spec: dict, task: dict, attempt: dict) -> None:
        from ...private_dirs import cleanup_attempt_credentials
        private_adapter = spec["adapter"]
        if private_adapter == "decision":
            private_adapter = ((claim.get("decisionInput") or {}).get("buddy") or {}).get("harness")
        if private_adapter in {"dsh", "zcode", "codex", "claude", "command"}:
            cleanup_attempt_credentials(self.state_dir, private_adapter, task["taskId"], attempt["attemptId"])

    def _nonce(self) -> str:
        return self.nonce

    def receipt(self, claim: dict, report: dict, directory: Path) -> dict:
        attempt = claim["attempt"]
        if report["shutdownConfirmed"] is True:
            try:
                self._cleanup_attempt_credentials(claim, claim["task"]["spec"], claim["task"], attempt)
            except Exception as error:
                report = {**report, "status": "failed", "terminationReason": TERMINATION_HARNESS_ERROR,
                          "error": f"private credential cleanup failed: {error!r}"}
        receipt = {
            "attemptId": attempt["attemptId"],
            "taskId": attempt["taskId"],
            "generation": attempt["generation"],
            "workerId": self.worker_id,
            "nonce": self.nonce,
            "commandId": f"result:{attempt['attemptId']}",
            "report": report,
            "createdAt": _now(),
        }
        write_receipt(directory, receipt)
        self.log(f"durable completion receipt written for attempt {attempt['attemptId']}")
        return receipt


class _ExecutionControl(threading.Thread):
    """Observe only local native activity and received signals; no lease or stop polling."""
    def __init__(self, execution):
        super().__init__(name="hey-my-buddy-execution-control", daemon=True)
        self.execution = execution
        self._done = threading.Event()
        self._wake = threading.Event()
        self._lock = threading.RLock()
        self.handle = self.implementation = None
        self.cancel_view = None
        self.deadline = None
        self.requests = 0
        self.applied = 0
        self._activity = None

    def request_stop(self, initiator, deadline):
        # Signal handlers only update local facts; native cancellation runs on
        # this thread instead of re-entering locks from the main thread.
        self.requests += 1
        self.cancel_view = {"cancelRequested": True, "stopInitiator": initiator}
        self.deadline = deadline
        self._wake.set()

    def attach(self, handle, implementation):
        with self._lock:
            self.handle, self.implementation = handle, implementation
            self.applied = 0
            self._activity = None
        self._wake.set()

    def wait(self, seconds):
        self._wake.wait(seconds)

    def run(self):
        while not self._done.is_set():
            with self._lock:
                handle, implementation = self.handle, self.implementation
            if handle is not None:
                if self.requests > self.applied:
                    requested = self.requests
                    if self.applied or requested > 1:
                        handle.stop_initiator = self.cancel_view["stopInitiator"]
                        role_seam.worker_cancel(implementation, handle)
                    else:
                        role_seam.worker_stop(implementation, handle,
                            stop_initiator=self.cancel_view["stopInitiator"], grace_deadline=self.deadline)
                    self.applied = requested
                try:
                    from ..roles.live import handle_live_binding
                    state, channel = handle_live_binding(handle, state_dir=self.execution.state_dir)
                    if state == "bound":
                        snapshot = channel.observe(after_seq=None, limit=1, timeout_ms=250, fields=("activity",))
                        activity = snapshot.activity.value if snapshot.activity is not None else None
                        if activity is not None and activity_module.is_newer(activity, self._activity):
                            self.execution.hint(activity=activity)
                            self._activity = activity
                except Exception as error:
                    # A failed live observation never disables native stop handling.
                    self.execution.log(f"live activity unavailable: {error!r}")
            self.execution.flush_hints()
            self._wake.wait(0.25)
            self._wake.clear()

    def stop(self):
        self._done.set()
        self._wake.set()


def launch_execution(claim, state_dir, worker_id, nonce, *, lease_seconds=120):
    """Launch an execution with private on-disk material, never claim secrets in argv/env."""
    from ..harnesses.controller import launch_controller
    from ...private_dirs import ensure_private_dir
    from ...protocol.run_identity import RunIdentity
    state_dir = Path(state_dir)
    attempt = claim["attempt"]
    directory = ensure_private_dir(state_dir / "attempts" / claim["task"]["taskId"] / attempt["attemptId"])
    startup = directory / STARTUP
    fsync_json(startup, {"claim": claim, "stateDir": str(state_dir), "workerId": worker_id,
                         "nonce": nonce, "leaseSeconds": lease_seconds})
    environment = dict(os.environ)
    for key in ("HMB_AGENT_CREDENTIAL_FILE", "HMB_AGENT_CREDENTIAL_FD", "HMB_WORKER_ID", "HMB_WORKER_STATE"):
        environment.pop(key, None)
    handle = launch_controller(
        prepare=lambda: ([sys.executable, "-m", "hey_my_buddy.buddy.runtime.execution", "--startup", str(startup)],
                         str(directory), environment),
        log_paths={"stdout": str(directory / "execution.stdout.log"),
                   "stderr": str(directory / "execution.stderr.log")})
    handle.execution_live_file = directory / "execution-live.json"
    handle.execution_directory = directory
    handle.execution_binding = {"taskId": claim["task"]["taskId"], "attemptId": attempt["attemptId"],
                                "generation": attempt["generation"]}
    handle.role_run_identity = RunIdentity(task_id=claim["task"]["taskId"], attempt_id=attempt["attemptId"],
        generation=attempt["generation"], invocation_id="execution", turn_id=(claim.get("turn") or {}).get("turnId"))
    name = claim["task"]["spec"]["adapter"]
    if name == "decision":
        name = claim["decisionInput"]["buddy"]["harness"]
    handle.role_run_control = {"harness": name}
    return handle


def stop_execution(handle, *, initiator="unknown", grace_deadline=None, force=False):
    """Every signal carries its attempt-bound intent; escalation stays cooperative."""
    with handle.stop_lock:
        if handle.force_stop_requested or (handle.cancel_requested and not force and
                (not handle.wrap_up_started or initiator in WRAP_UP_INITIATORS)):
            return
        handle.stop_initiator = initiator
        handle.cancel_requested = True
        handle.wrap_up_started = initiator in WRAP_UP_INITIATORS and grace_deadline is not None and not force
        handle.grace_deadline = grace_deadline
        handle.force_stop_requested = force or not handle.wrap_up_started
        fsync_json(handle.execution_directory / STOP,
                   {"binding": handle.execution_binding, "initiator": initiator,
                    "graceDeadline": grace_deadline if handle.wrap_up_started else None,
                    "force": handle.force_stop_requested})
        handle.signal_group(signal.SIGTERM)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--startup", required=True)
    args = parser.parse_args()
    from ..roles.turn_io import guard_private_path
    from ...json_codec import decode_strict_json
    material = decode_strict_json(guard_private_path(Path(args.startup)).read_bytes())
    execution = Execution(material["claim"], Path(material["stateDir"]), material["workerId"], material["nonce"],
                          lease_seconds=material["leaseSeconds"], log=lambda message: print(message, flush=True))
    binding = {key: material["claim"]["attempt"][key] for key in ("taskId", "attemptId", "generation")}
    def interrupted(_signal, _frame):
        try:
            stop = decode_strict_json(guard_private_path(execution.directory / STOP).read_bytes())
            if (not isinstance(stop, dict)
                    or set(stop) != {"binding", "initiator", "graceDeadline", "force"}
                    or stop["binding"] != binding
                    or stop["initiator"] not in {"host", "lead", "console", "service", "unknown"}
                    or type(stop["force"]) is not bool):
                raise ValueError("invalid or foreign stop intent")
            initiator, deadline = stop["initiator"], stop["graceDeadline"]
            if stop["force"]:
                if deadline is not None:
                    raise ValueError("forced stop cannot carry a wrap-up deadline")
            elif (initiator not in WRAP_UP_INITIATORS or type(deadline) not in (int, float)
                    or not math.isfinite(deadline)):
                raise ValueError("wrap-up needs a finite absolute deadline")
        except (OSError, ValueError, BoardError, KeyError):
            initiator, deadline = "unknown", None
        execution.request_stop(initiator, deadline)
    for sig in (signal.SIGTERM, signal.SIGINT, *((signal.SIGBREAK,) if hasattr(signal, "SIGBREAK") else ())):
        signal.signal(sig, interrupted)
    for pending in _early_signals:
        interrupted(pending, None)
    _early_signals.clear()
    from ...private_dirs import attempt_root, ensure_private_dir
    from ...protocol.process_identity import install_native_process_journal, native_process_journal_path
    name = execution.claim["task"]["spec"]["adapter"]
    if name == "decision":
        name = execution.claim["decisionInput"]["buddy"]["harness"]
    root = ensure_private_dir(attempt_root(execution.state_dir, name, binding["taskId"], binding["attemptId"]))
    install_native_process_journal(native_process_journal_path(root), binding)
    from ..roles.run_execution import set_execution_live_file
    set_execution_live_file(execution.directory / "execution-live.json")
    execution.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
