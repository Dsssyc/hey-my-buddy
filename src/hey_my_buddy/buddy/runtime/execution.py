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
import math
import os
import sys
import threading
import time
import secrets
from datetime import datetime, timezone
from pathlib import Path

import c_two as cc

from ...errors import BoardError
from ...protocol.client import BoardClient
from ...protocol import activity as activity_module
from ...protocol import execution as execution_protocol
from ...protocol.contracts import ExecutionLive
from ...protocol.run_identity import RunIdentity
from ...protocol.reasons import reason as make_reason
from ...json_codec import canonical_json, decode_strict_json
from ...protocol.stop import WRAP_UP_INITIATORS, TERMINATION_REASONS
from ..harnesses.registry import ExecutionContext
from ..harnesses.c_two_live import (CTwoLiveEndpoint, LiveEndpointDescriptor,
    _observe_arguments, _timeout_ms, random_person_name)
from ..harnesses.live import LiveReply, LiveRequest, LiveSnapshot
from ..roles import controller as role_seam
from .receipts import read_receipt, write_receipt, deliver_receipt, receipt_directory, RETRYABLE

fsync_json = execution_protocol.fsync_json
BUDGET = execution_protocol.BUDGET
PREFLIGHT_RETRY_SECONDS = 15.0
PREFLIGHT_MAX_RETRY_SECONDS = 180.0
(
    TERMINATION_COMPLETED, TERMINATION_WRAP_UP, TERMINATION_GRACE_EXPIRED,
    TERMINATION_FORCED_STOP, TERMINATION_CONSOLE_STOP, TERMINATION_SERVICE_STOP,
    TERMINATION_UNKNOWN_STOP, TERMINATION_DEADLINE, TERMINATION_HARNESS_ERROR,
    TERMINATION_TRANSPORT_ERROR, TERMINATION_INTERRUPTED, TERMINATION_WORKSPACE_ERROR,
) = TERMINATION_REASONS


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def is_transport_failure(payload) -> bool:
    if not isinstance(payload, dict):
        return False
    kind = payload.get("failureKind")
    if isinstance(kind, str) and kind.strip().lower() in ("transport", "transport-error"):
        return True
    for key in ("status", "code"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip().lower().startswith("transport"):
            return True
    return False


def classify_termination(outcome, *, timed_out, cancel_requested,
                         stop_initiator=None, grace_expired=False):
    if outcome.status == "ok":
        return TERMINATION_COMPLETED
    if timed_out:
        return TERMINATION_DEADLINE
    if stop_initiator == "deadline":
        return TERMINATION_DEADLINE
    if cancel_requested:
        result = outcome.result if isinstance(outcome.result, dict) else {}
        return role_seam.stop_termination_reason(shutdown_confirmed=outcome.shutdown_confirmed,
            stop_initiator=stop_initiator, grace_expired=result.get("graceExpired", grace_expired),
            wrap_up_delivered=result.get("wrapUpDelivered", False))
    if isinstance(outcome.result, dict) and any(
            outcome.result.get(key) is not None for key in (
                "workspaceSealError", "partialSealError", "cumulativePatchError")):
        return TERMINATION_WORKSPACE_ERROR
    if is_transport_failure(getattr(outcome, "result", None)):
        return TERMINATION_TRANSPORT_ERROR
    return TERMINATION_HARNESS_ERROR


def private_adapter(claim: dict) -> str:
    adapter = claim["task"]["spec"]["adapter"]
    if adapter == "decision":
        return claim["decisionInput"]["buddy"]["harness"]
    return adapter


class _ExecutionLive:
    """The single C-Two resource owned for the full execution lifetime."""

    def __init__(self, execution):
        self.execution = execution
        attempt = execution.claim["attempt"]
        self.identity = RunIdentity(task_id=execution.claim["task"]["taskId"],
            attempt_id=attempt["attemptId"], generation=attempt["generation"],
            invocation_id="execution", turn_id=(execution.claim.get("turn") or {}).get("turnId"))
        self.name = random_person_name()
        self.instance_id = secrets.token_hex(32)
        self.token = secrets.token_hex(32)
        self._lock = threading.RLock()
        self._native: CTwoLiveEndpoint | None = None
        self._done = threading.Event()
        self._started = False

    def start(self, process_identity: dict) -> None:
        cc.register(ExecutionLive, self, name=self.name,
                    concurrency=cc.ConcurrencyConfig(mode=cc.ConcurrencyMode.PARALLEL))
        self._started = True
        address = cc.server_address()
        inspected = cc.inspect_endpoint(address)
        if inspected["status"] not in ("present", "not-applicable"):
            raise BoardError("LIVE_UNAVAILABLE", "Execution endpoint credential is unavailable")
        credential = inspected["credential"]
        descriptor = LiveEndpointDescriptor(address=address, name=self.name,
            instance_id=self.instance_id, host_pid=os.getpid(),
            endpoint_credential=credential.to_json() if credential is not None else None)
        fsync_json(self.execution.directory / execution_protocol.LIVE, {
            "identity": self.identity.to_payload(), "processIdentity": process_identity,
            "descriptor": descriptor.to_payload(), "token": self.token})

    def bind_native(self, endpoint: CTwoLiveEndpoint) -> None:
        with self._lock:
            self._native = endpoint

    def unbind_native(self, endpoint: CTwoLiveEndpoint) -> None:
        with self._lock:
            if self._native is endpoint:
                self._native = None

    def finish(self) -> None:
        self._done.set()
        if self._started:
            try:
                cc.unregister(self.name)
            finally:
                cc.shutdown()
                (self.execution.directory / execution_protocol.LIVE).unlink(missing_ok=True)

    def _auth(self, raw: str, fields: set[str]) -> dict:
        if not isinstance(raw, str) or len(raw.encode()) > 65536:
            raise BoardError("LIVE_UNAVAILABLE", "Invalid execution live frame")
        value = decode_strict_json(raw)
        if not isinstance(value, dict) or set(value) != {
                "identity", "instanceId", "token", *fields}:
            raise BoardError("LIVE_UNAVAILABLE", "Invalid execution live frame")
        if (RunIdentity.from_payload(value["identity"]) != self.identity
                or value["instanceId"] != self.instance_id
                or not isinstance(value["token"], str)
                or not secrets.compare_digest(value["token"], self.token)):
            raise BoardError("LIVE_UNAVAILABLE", "Execution live identity refused")
        return value

    def send(self, request_json: str) -> str:
        value = self._auth(request_json, {"request", "timeoutMs", "deadlineMonotonic"})
        request = LiveRequest.from_payload(value["request"])
        if request.identity != self.identity:
            return canonical_json(LiveReply(status="unavailable",
                                            reason_code="identity-mismatch").to_payload())
        timeout_ms = _timeout_ms(value["timeoutMs"], "timeoutMs")
        deadline = value["deadlineMonotonic"]
        if type(deadline) not in (int, float) or not math.isfinite(deadline) or deadline < 0:
            raise BoardError("INVALID_ARGUMENT", "Invalid live request deadline")
        with self._lock:
            native = self._native
        if native is None:
            return canonical_json(LiveReply(status="unavailable", reason_code="source-unavailable").to_payload())
        # The wire names the execution; the current run owns its own invocation
        # identity. Select it locally after the one outer authentication.
        local_request = request.model_copy(update={"identity": native.identity})
        return native.request_local(local_request, timeout_ms=timeout_ms,
                                    deadline_monotonic=deadline)

    def observe(self, request_json: str) -> str:
        value = self._auth(request_json, {"afterSeq", "limit", "fields", "inquiryId"})
        arguments = _observe_arguments(value["afterSeq"], value["limit"],
                                       value["fields"], value["inquiryId"])
        with self._lock:
            native = self._native
        if native is None:
            return canonical_json(LiveSnapshot(unavailable="source-unavailable", observed=False,
                                               reason="source-unavailable").to_payload())
        return native.observe_local(**arguments)

    def capabilities(self, request_json: str) -> str:
        self._auth(request_json, set())
        with self._lock:
            native = self._native
        if native is None:
            raise BoardError("LIVE_UNAVAILABLE", "No active native run")
        return native.capabilities_local()

    def close(self, request_json: str) -> str:
        self._auth(request_json, {"reason"})
        return canonical_json({"closed": True})

    def stop(self, request_json: str) -> str:
        value = self._auth(request_json, {"initiator", "graceDeadline", "force"})
        if value["initiator"] not in {"host", "lead", "console", "service", "deadline", "unknown"}:
            raise BoardError("INVALID_ARGUMENT", "Unknown stop initiator")
        if type(value["force"]) is not bool:
            raise BoardError("INVALID_ARGUMENT", "Invalid stop force fact")
        deadline = value["graceDeadline"]
        if deadline is not None and (type(deadline) not in (int, float) or not math.isfinite(deadline)):
            raise BoardError("INVALID_ARGUMENT", "Invalid stop deadline")
        self.execution.request_stop(value["initiator"], deadline, force=value["force"])
        return canonical_json({"accepted": True})

    def wait(self, request_json: str) -> str:
        self._auth(request_json, set())
        self._done.wait(3.0)
        return canonical_json({"alive": True})


class Execution:
    def __init__(self, claim: dict, state_dir: Path, nonce: str, *,
                 session_record_path: str | None = None, client=None, log=None):
        from ...protocol.rpc_config import configure_server, configure_client
        configure_server(state_dir)
        configure_client(state_dir)
        self.claim, self.state_dir, self.nonce = claim, Path(state_dir), nonce
        self.session_record_path = session_record_path
        self.client = client or BoardClient(self.state_dir, autostart=False)
        self._log = log or (lambda message: None)
        self.control = _ExecutionControl(self)
        self._hints: dict = {}
        self._hint_lock = threading.Lock()
        self._flush_lock = threading.Lock()
        self._identity_pending: dict | None = None
        self._next_hint_at = 0.0
        self._hint_delay = 0.25
        self.directory = receipt_directory(self.state_dir, private_adapter(claim),
                                           claim["task"]["taskId"], claim["attempt"]["attemptId"])
        self.evidence_directory = (self.state_dir / "attempts" / claim["task"]["taskId"] /
                                   claim["attempt"]["attemptId"])
        self.live = _ExecutionLive(self)

    def log(self, message):
        self._log(f"[execution {self.claim['attempt']['attemptId']}] {message}")

    def request_stop(self, initiator="unknown", grace_deadline=None, *, force=False):
        self.control.request_stop(initiator, grace_deadline, force=force)

    def stop_requested(self):
        return self.control.cancel_view is not None

    def hint(self, **data):
        with self._hint_lock:
            self._hints.update(data)

    def flush_hints(self) -> bool:
        with self._flush_lock:
            with self._hint_lock:
                pending = dict(self._hints)
            if not pending and self._identity_pending is None:
                return True
            if time.monotonic() < self._next_hint_at:
                return False
            attempt = self.claim["attempt"]
            try:
                self.client.progress(attempt["attemptId"], attempt["generation"],
                                     self.nonce, process_identity=self._identity_pending,
                                     phase="executing" if self._identity_pending else None,
                                     data=pending or None)
            except BoardError as error:
                if error.code in RETRYABLE:
                    self._next_hint_at = time.monotonic() + self._hint_delay
                    self._hint_delay = min(10.0, self._hint_delay * 2)
                    return False
                raise
            self._next_hint_at = 0.0
            self._hint_delay = 0.25
            with self._hint_lock:
                self._identity_pending = None
                for key, value in pending.items():
                    if self._hints.get(key) == value:
                        self._hints.pop(key)
            return True

    def run(self):
        # The durable empty journal precedes the identity acknowledgement:
        # any acknowledged execution therefore has a native-spawn record to
        # inspect after a crash. Native work still waits for the acknowledgement.
        from ...protocol.process_identity import install_native_process_journal, native_process_journal_path
        from ...private_dirs import ensure_private_dir
        ensure_private_dir(self.directory)
        binding = {"taskId": self.claim["task"]["taskId"],
                   "attemptId": self.claim["attempt"]["attemptId"],
                   "generation": self.claim["attempt"]["generation"]}
        install_native_process_journal(native_process_journal_path(self.directory), binding)
        delay = 0.25
        while self._identity_pending is not None:
            delivered = self.flush_hints()
            if self._identity_pending is not None:
                time.sleep(delay)
                if not delivered:
                    delay = min(10.0, delay * 2)
        receipt = self.execute()
        self.control.raise_fatal()
        return deliver_receipt(self.client, receipt, self.directory, nonce=self.nonce, log=self.log,
                               flush_hints=self._flush_delivery_hints)

    def _flush_delivery_hints(self):
        self.control.raise_fatal()
        return self.flush_hints()

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
        from ...private_dirs import ensure_private_dir
        directory = ensure_private_dir(self.directory)
        self.evidence_directory.mkdir(mode=0o700, parents=True, exist_ok=True)
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
            handle = holder.get("handle") or getattr(error, "execution_handle", None)
            implementation = holder.get("adapter")
            spawn_evidence = getattr(error, "owned_popen_evidence", None)
            shutdown_confirmed = (holder.get("confirmed_stopped") is True or
                                  getattr(spawn_evidence, "shutdown_confirmed", False) is True)
            if not shutdown_confirmed and handle is not None and implementation is not None:
                try:
                    role_seam.worker_cancel(implementation, handle)
                    handle.wait(timeout=None)
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
                    "error": f"execution failure: {error!r}",
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
        control = self.control
        holder['preflightControl'] = control
        control.start()
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
                    control.raise_fatal()
                    if control.cancel_view is not None or self.stop_requested():
                        return cancelled(control.cancel_view)
                    command_id = f"harness-{attempt['attemptId']}-{int(retry)}"
                    if unknown_reads:
                        command_id += f"-unknown-{unknown_reads}"
                    try:
                        response = self.client.call('harness_prepare', {
                            'attemptId': attempt['attemptId'], 'generation': attempt['generation'],
                            'nonce': self._nonce(), 'commandId': command_id, 'retry': retry and not unknown_reads,
                            'healthRevision': record.get('revision') if record else None,
                        })
                    except BoardError as error:
                        if error.code == 'ATTEMPT_FINISHED':
                            view = control.cancel_view
                            if view is None:
                                try:
                                    view = self.client.call('task_get', {'runId': attempt['taskId']})['task']
                                except Exception:
                                    raise error
                            if view.get('cancelRequested'):
                                return cancelled(view)
                        if error.code not in RETRYABLE:
                            raise
                        # The request may already be committed: replay its exact identity.
                        control.wait(min(PREFLIGHT_MAX_RETRY_SECONDS,
                                         PREFLIGHT_RETRY_SECONDS * 2 ** min(unknown_reads, 4)))
                        continue
                    record = response['harness']
                    if record is None or (record.get('status') != 'unknown'
                                          and (record.get('lastProbe') or {}).get('status') != 'unknown'):
                        break
                    unknown_reads += 1
                    control.wait(min(PREFLIGHT_MAX_RETRY_SECONDS,
                                     PREFLIGHT_RETRY_SECONDS * 2 ** min(unknown_reads - 1, 4)))
                if control.cancel_view is not None or self.stop_requested():
                    return cancelled(control.cancel_view)
                history.append({'retry': retry, 'harness': record})
                if record is None or not record.get('available'):
                    if not retry:
                        continue
                    if review:
                        return self._review_unavailable(claim, directory, history, 'router-unavailable')
                    return self.receipt(claim, {'status': 'failed', 'result': {'harness': record, 'harnessAttempts': history},
                        'error': 'HARNESS_UNAVAILABLE',
                        'exitCode': None, 'signal': None, 'shutdownConfirmed': True, 'artifacts': [],
                        'terminationReason': TERMINATION_HARNESS_ERROR}, directory)
                holder['harnessHistory'] = history
                holder['harnessRetry'] = retry
                try:
                    with bound([record], environment={**os.environ, "HMB_STATE_DIR": str(self.state_dir)}):
                        return self._execute_selected(claim, task, spec, attempt, directory, holder)
                except (OSError, BoardError) as error:
                    if (isinstance(error, BoardError) and error.code == 'ATTEMPT_FINISHED'
                            and (control.cancel_view is not None or self.stop_requested())):
                        return cancelled(control.cancel_view)
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
            control.stop()
            control.join()
            holder.pop('preflightControl', None)

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
        self.client.call('harness_prepare', {'attemptId': attempt['attemptId'],
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
            directory=self.evidence_directory,
            runtime=runtime.resolve_runtime(),
            environment={**os.environ, "HMB_STATE_DIR": str(self.state_dir)},
            decision_input=claim.get("decisionInput"),
            # The service-owned turn identity and bounded context; the adapter stages
            # them for the runner. The scoped credential is written to a private file
            # and exported by path, never into the model-visible turn input.
            turn=claim.get("turn") if isinstance(claim.get("turn"), dict) else None,
            agent_credential=claim.get("agentCredential") or None,
            session_record_path=self.session_record_path,
        )
        implementation = role_seam.worker_executor(spec["adapter"])
        control = holder['preflightControl']
        preparation = role_seam.prepare_worker_run(implementation, context)
        unknown_reads = 0
        while preparation.error is not None and preparation.error.code == 'router-input-unknown':
            control.raise_fatal()
            self._wait_control_observation(claim, control, unknown_reads)
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
        timeout_seconds = context.timeout_seconds
        deadline = started + timeout_seconds if timeout_seconds is not None else None
        if deadline is not None:
            fsync_json(self.directory / BUDGET, {
                "binding": {"taskId": task["taskId"], "attemptId": attempt["attemptId"],
                            "generation": attempt["generation"]},
                "deadline": deadline,
            })
        holder["adapter"] = implementation
        # A previous pre-model retry may have proved its own child stopped. That
        # proof cannot cover this start(): it might spawn before raising a handle.
        holder.pop("confirmed_stopped", None)
        holder.pop("handle", None)
        control.raise_fatal()
        holder["start_invoked"] = True
        handle = role_seam.worker_start(implementation, context)
        holder["handle"] = handle
        holder["started"] = True
        control.attach(handle, implementation)
        self.hint(waitingReason=None)
        timed_out = False
        while True:
            control.raise_fatal()
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
                handle.wait(timeout=None)
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
                self._wait_control_observation(claim, control, unknown_reads)
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
            "stdout": str(self.evidence_directory / "execution.stdout.log"),
            "stderr": str(self.evidence_directory / "execution.stderr.log")})
        report["logPaths"] = {name: path for name, path in log_paths.items() if Path(path).is_file()}
        report["runtimeIdentity"] = context.runtime.get("identity")
        return self.receipt(claim, report, directory)

    def _wait_control_observation(self, claim, control, retry):
        control.raise_fatal()
        attempt = claim['attempt']
        self.hint(waitingReason=make_reason('router-input-unknown'))
        control.wait(min(PREFLIGHT_MAX_RETRY_SECONDS, PREFLIGHT_RETRY_SECONDS * 2 ** min(retry, 4)))
        control.raise_fatal()
        if control.cancel_view is not None or self.stop_requested():
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
                (directory / BUDGET).unlink(missing_ok=True)
            except Exception as error:
                self.log(f"private execution budget cleanup failed: {error!r}")
            try:
                self._cleanup_attempt_credentials(claim, claim["task"]["spec"], claim["task"], attempt)
            except Exception as error:
                self.log(f"private credential cleanup failed: {error!r}")
        receipt = {
            "attemptId": attempt["attemptId"],
            "taskId": attempt["taskId"],
            "generation": attempt["generation"],
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
        self.fatal: BaseException | None = None

    def raise_fatal(self):
        if self.fatal is not None:
            raise self.fatal

    def request_stop(self, initiator, deadline, *, force=False):
        # Signal handlers only update local facts; native cancellation runs on
        # this thread instead of re-entering locks from the main thread.
        if self.cancel_view is not None and not force:
            if self.deadline is None or initiator in WRAP_UP_INITIATORS:
                return
        self.requests += 2 if force else 1
        self.cancel_view = {"cancelRequested": True, "stopInitiator": initiator}
        self.deadline = min(self.deadline, deadline) if self.deadline is not None and deadline is not None else deadline
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
        try:
            self._run_control()
        except BaseException as error:
            self.fatal = error
            self._wake.set()

    def _run_control(self):
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
                    endpoint = getattr(handle, "live_endpoint", None)
                    if endpoint is not None:
                        activity = endpoint.current_activity()
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


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--startup", required=True)
    args = parser.parse_args()
    from ..roles.turn_io import guard_private_path
    material = decode_strict_json(guard_private_path(Path(args.startup)).read_bytes())
    execution = Execution(material["claim"], Path(material["stateDir"]), material["nonce"],
                          session_record_path=material["sessionRecordPath"],
                          log=lambda message: print(message, flush=True))
    def interrupted(_signal, _frame):
        execution.request_stop("unknown", None, force=True)
    for sig in (signal.SIGTERM, signal.SIGINT, *((signal.SIGBREAK,) if hasattr(signal, "SIGBREAK") else ())):
        signal.signal(sig, interrupted)
    for pending in _early_signals:
        interrupted(pending, None)
    _early_signals.clear()
    from ...private_dirs import ensure_private_dir
    root = ensure_private_dir(execution.directory)
    identity = execution_protocol.self_execution_process_identity(material.get("jobName"))
    from ..roles.run_execution import set_execution_live_server
    set_execution_live_server(execution.live)
    try:
        execution.live.start(identity)
        execution._identity_pending = identity
        execution.run()
    finally:
        try:
            execution.live.finish()
        finally:
            set_execution_live_server(None)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
