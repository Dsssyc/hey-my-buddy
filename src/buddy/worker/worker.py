"""Independent Python worker: owns processes, deadlines and durable local receipts.

A worker is not part of the daemon. It runs in its own session with file-backed
logs, keeps its child handles across daemon downtime, enforces its own deadline and
retains an immutable completion receipt until the service confirms the result
transaction.
"""
from __future__ import annotations

import json
import os
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable

from .. import activity as activity_module
from ..adapters import ExecutionContext, adapter as get_adapter, supported_capabilities
from ..client import BoardClient, new_nonce
from ..db import TERMINATION_REASONS
from ..errors import BoardError

DEFAULT_RETRY_SECONDS = 120
CLAIM_IDLE_SECONDS = 2.0


def supervisor_start_stop_path(directory: Path) -> Path | None:
    """A daemon-created supervisor's private startup-abort channel."""
    identity = os.environ.get("BUDDY_SUPERVISOR_START_ID", "")
    if len(identity) == 32 and all(char in "0123456789abcdef" for char in identity):
        return directory / f"stop-{identity}.request"
    return None

#: Result vocabulary, in the service-side order documented by
#: ``buddy.db.TERMINATION_REASONS``: completed, user-cancel, deadline,
#: harness-error, transport-error. The worker never invents another label.
(
    TERMINATION_COMPLETED,
    TERMINATION_USER_CANCEL,
    TERMINATION_DEADLINE,
    TERMINATION_HARNESS_ERROR,
    TERMINATION_TRANSPORT_ERROR,
) = TERMINATION_REASONS

#: Durable scale-down intent, observed by the owning Worker between attempts only.
#: It is deliberately a different file from ``stop.request``: a stop may cancel the
#: child this worker owns, while a retire intent must never interrupt an attempt that
#: is already running or waiting to deliver its receipt.
RETIRE_REQUEST_NAME = "retire.request"


def fsync_json(path: Path, value: dict) -> None:
    """Write one small JSON file durably: temp + fsync + rename + fsync parent."""
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(value, stream, ensure_ascii=False, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    os.replace(temporary, path)
    directory_fd = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


def is_transport_failure(payload: Any) -> bool:
    """Whether an adapter result names a transport-level failure.

    Adapters may report ``failureKind: "transport"`` (or a ``transport-...``
    status/code) when the harness itself could not be reached or its native channel
    broke. Everything else that failed without a deadline or a cancel is a harness
    error; the distinction is never inferred from prose.
    """
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


def classify_termination(outcome, *, timed_out: bool, cancel_requested: bool) -> str:
    """The real reason this attempt stopped.

    Order matters: a genuine completion is never re-labelled, the worker's own
    deadline outranks the cancellation it triggered, a user cancellation is named as
    such, and a failure that the adapter attributed to transport is kept distinct
    from an ordinary harness failure.
    """
    if outcome.status == "ok":
        return TERMINATION_COMPLETED
    if timed_out:
        return TERMINATION_DEADLINE
    if cancel_requested:
        return TERMINATION_USER_CANCEL
    if is_transport_failure(getattr(outcome, "result", None)):
        return TERMINATION_TRANSPORT_ERROR
    return TERMINATION_HARNESS_ERROR


class ReceiptSpool:
    """Bounded, correlated, replayable transport receipts — never a second database."""

    def __init__(self, directory: Path, worker_id: str):
        self.directory = Path(directory) / "workers" / worker_id
        self.receipts = self.directory / "receipts"
        self.startup_path = self.directory / "startup.json"

    def ensure(self) -> None:
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.receipts.mkdir(mode=0o700, exist_ok=True)

    # -- startup intent ------------------------------------------------------
    def read_startup(self) -> dict | None:
        try:
            value = json.loads(self.startup_path.read_text())
        except (OSError, ValueError):
            return None
        return value if isinstance(value, dict) else None

    def write_startup(self, value: dict) -> dict:
        # The nonce and claim request ID are durable BEFORE the claim call: a
        # committed claim whose reply is lost stays recoverable with this identity.
        fsync_json(self.startup_path, value)
        return value

    def clear_startup(self) -> None:
        self.startup_path.unlink(missing_ok=True)

    # -- completion receipts -------------------------------------------------
    def path(self, attempt_id: str) -> Path:
        return self.receipts / f"{attempt_id}.json"

    def write(self, attempt_id: str, value: dict) -> Path:
        self.ensure()
        path = self.path(attempt_id)
        fsync_json(path, value)
        return path

    def read(self, attempt_id: str) -> dict | None:
        try:
            value = json.loads(self.path(attempt_id).read_text())
        except (OSError, ValueError):
            return None
        return value if isinstance(value, dict) else None

    def pending(self) -> list[dict]:
        self.ensure()
        out = []
        for path in sorted(self.receipts.glob("*.json")):
            try:
                value = json.loads(path.read_text())
            except (OSError, ValueError):
                continue
            if isinstance(value, dict):
                out.append(value)
        return out

    def drop(self, attempt_id: str) -> None:
        # A receipt is deleted only after the service confirmed the result
        # transaction; nothing here is cleared to let an upgrade proceed.
        self.path(attempt_id).unlink(missing_ok=True)


class Worker:
    """One independent worker process body."""

    def __init__(
        self,
        worker_id: str,
        state_dir: str | Path,
        *,
        client: BoardClient | None = None,
        lease_seconds: int = 120,
        adapters: tuple[str, ...] | None = None,
        extra_capabilities: tuple[str, ...] = (),
        stop: threading.Event | None = None,
        retry_seconds: int = DEFAULT_RETRY_SECONDS,
        log: Callable[[str], None] | None = None,
    ):
        self.worker_id = worker_id
        # One identity per worker *process*: a new process cannot prove it owns a
        # child that a previous process spawned, so it must never resume that work.
        self.instance_id = f"{worker_id}-{uuid.uuid4()}"
        self.state_dir = Path(state_dir)
        self.client = client or BoardClient(self.state_dir, autostart=False)
        self.lease_seconds = lease_seconds
        self.adapters = adapters
        self.extra_capabilities = tuple(extra_capabilities)
        self.stop = stop or threading.Event()
        self.retry_seconds = retry_seconds
        self.spool = ReceiptSpool(self.state_dir, worker_id)
        self._log = log or (lambda message: None)
        self.heartbeat_path = self.spool.directory / "heartbeat.json"
        self.stop_request_path = self.spool.directory / "stop.request"
        self.start_stop_request_path = supervisor_start_stop_path(self.spool.directory)
        #: Scale-down intent. Read only between attempts, after receipt replay.
        self.retire_request_path = self.spool.directory / RETIRE_REQUEST_NAME

    def log(self, message: str) -> None:
        self._log(f"[worker {self.worker_id}] {message}")

    # -- lifecycle -----------------------------------------------------------
    def stop_requested(self) -> bool:
        return (self.stop.is_set() or self.stop_request_path.exists()
                or bool(self.start_stop_request_path and self.start_stop_request_path.exists()))

    def retire_requested(self) -> bool:
        """A scale-down intent, never a cancel signal for an owned child."""
        return self.retire_request_path.exists()

    def recovery_pending(self) -> list[str]:
        """Durable evidence this worker still owes the service.

        A scale-down may complete only when this is empty. A pending completion
        receipt and a startup intent that still names an attempt are exactly the
        evidence a restarted surplus owner must replay or reconcile first; a missing
        process never proves an attempt stopped, so neither is dropped to let a
        retire intent win.
        """
        pending: list[str] = []
        if self.spool.pending():
            pending.append("pending-receipts")
        intent = self.spool.read_startup()
        if isinstance(intent, dict) and intent.get("attemptId"):
            pending.append(f"startup-intent:{intent['attemptId']}")
        return pending

    def heartbeat(self, state: str, **extra: Any) -> None:
        fsync_json(
            self.heartbeat_path,
            {"workerId": self.worker_id, "pid": os.getpid(), "state": state, "at": _now(), **extra},
        )

    def preserve_orphan_startup(self, intent: dict) -> None:
        """Record a previous process's intent as orphaned evidence and clear it.

        The nonce is not this process's to present and its child handle is gone, so
        the attempt stays explicitly uncertain instead of being adopted or silently
        released. The orphan record is durable before the intent is cleared.
        """
        fsync_json(
            self.spool.directory / "orphaned.json",
            {**intent, "orphanedAt": _now(), "orphanedReason": "the worker process that owned this intent exited"},
        )
        self.log(
            f"found an orphaned startup intent for attempt {intent.get('attemptId')}; it stays uncertain and is "
            "not adopted by this process"
        )
        self.spool.clear_startup()

    def startup_intent(self) -> dict:
        self.spool.ensure()
        intent = self.spool.read_startup()
        if intent is not None and intent.get("instanceId") not in (None, self.instance_id):
            # A leftover intent from a previous worker process. Its nonce is not
            # ours to present and its child handle is gone, so it is preserved as
            # orphaned evidence instead of being adopted or silently released.
            self.preserve_orphan_startup(intent)
            intent = None
        if intent is None or not intent.get("nonce") or not intent.get("claimRequestId"):
            intent = {
                "workerId": self.worker_id,
                "instanceId": self.instance_id,
                "nonce": new_nonce(),
                "claimRequestId": f"claim-{uuid.uuid4()}",
                "createdAt": _now(),
                "attemptId": None,
            }
            self.spool.write_startup(intent)
        return intent

    def reconcile_startup_intent(self, intent: dict) -> bool:
        """Reconcile one attempt this process owns; ``True`` when it is resolved.

        Three durable states decide this, never the database's ``starting`` row and
        never a PID check:

        * no ``spawn.intent``  -> this worker never even reached the spawn boundary,
          so releasing the attempt is honest and frees its claims;
        * ``spawn.intent`` without ``spawn.marker`` -> an ambiguous window: the
          process may or may not exist, so the attempt stays uncertain;
        * ``spawn.marker`` -> a child definitely existed and its fate is unknown.

        The last two never release anything: a survivor must not lose its resource
        claims just because this worker restarted, and ``False`` keeps the intent as
        evidence for the next reconciliation instead of retiring past it.
        """
        attempt_id = intent["attemptId"]
        attempted = self.spawn_intent_path(intent).exists()
        if not attempted:
            # Durable proof from this worker's own disk that the spawn boundary was
            # never reached: an authenticated release, never a PID inference.
            try:
                self.client.release(
                    self.worker_id,
                    attempt_id,
                    intent.get("generation", 0),
                    intent["nonce"],
                    "no durable spawn intent exists for this attempt, so no process was ever created",
                    evidence={"spawnIntentWritten": False},
                    worker_instance=self.instance_id,
                )
                self.log(f"released never-spawned attempt {attempt_id} with spawn-intent evidence")
                self.spool.clear_startup()
                return True
            except BoardError as error:
                if error.code in ("SERVICE_UNAVAILABLE", "SERVICE_START_TIMEOUT", "SERVICE_START_FAILED", "INVALID_RESPONSE"):
                    self.log(f"release of {attempt_id} unavailable ({error.code}); keeping the intent")
                    return False
                self.log(f"evidence-based release refused ({error.code}); falling back to reconcile")
        try:
            response = self.client.reconcile(
                self.worker_id,
                attempt_id,
                intent.get("generation", 0),
                intent["nonce"],
                worker_instance=self.instance_id,
            )
        except BoardError as error:
            if error.code in ("SERVICE_UNAVAILABLE", "SERVICE_START_TIMEOUT", "SERVICE_START_FAILED", "INVALID_RESPONSE"):
                # A transport failure says nothing about ownership: keep the recovery
                # identity and try again later instead of discarding it.
                self.log(f"reconcile of {attempt_id} unavailable ({error.code}); keeping the intent")
                return False
            self.log(f"reconcile of {attempt_id} refused: {error.code}")
            self.spool.clear_startup()
            return True
        state = response["attempt"]["executionState"]
        if response.get("finished") or response.get("immutable"):
            self.log(f"attempt {attempt_id} is terminal; nothing to reconcile")
            self.spool.clear_startup()
            return True
        # The spawn boundary may have been crossed, so this process has no evidence
        # and the attempt keeps its uncertain ownership and claims.
        self.log(
            f"attempt {attempt_id} is {state} and its spawn boundary was possibly crossed; its process "
            "handle is gone, so it stays explicitly uncertain with its resource claims retained"
        )
        return False

    def settle_retirement(self) -> list[str]:
        """Replay what is already durable without claiming anything new.

        Returns the evidence that remains unresolved, so the caller keeps retrying
        instead of retiring past a receipt or a startup intent. Retirement is the
        only time this worker runs without claiming: a scale-down can therefore
        never start an attempt it would have to abandon.
        """
        for receipt in self.spool.pending():
            self.log(f"replaying receipt for attempt {receipt.get('attemptId')} before retiring")
            self.deliver(receipt)
        intent = self.spool.read_startup()
        if isinstance(intent, dict) and intent.get("attemptId"):
            if intent.get("instanceId") not in (None, self.instance_id):
                self.preserve_orphan_startup(intent)
            else:
                self.reconcile_startup_intent(intent)
        return self.recovery_pending()

    def reattach(self) -> None:
        """Reconcile anything this worker owned before it or the daemon restarted."""
        for receipt in self.spool.pending():
            self.log(f"replaying receipt for attempt {receipt.get('attemptId')}")
            self.deliver(receipt)
        intent = self.spool.read_startup()
        if not intent or not intent.get("attemptId"):
            return
        if intent.get("instanceId") not in (None, self.instance_id):
            self.log(
                f"attempt {intent['attemptId']} belongs to a different worker process instance; this process holds "
                "no handle for it, so it stays explicitly uncertain with its resource claims retained"
            )
            return
        self.reconcile_startup_intent(intent)

    def attempt_directory(self, task_id: str | None, attempt_id: str) -> Path:
        return self.state_dir / "attempts" / (task_id or "unknown") / attempt_id

    def spawn_intent_path(self, intent: dict) -> Path:
        return self.attempt_directory(intent.get("taskId"), intent["attemptId"]) / "spawn.intent"

    def spawn_marker(self, intent: dict) -> Path:
        return self.attempt_directory(intent.get("taskId"), intent["attemptId"]) / "spawn.marker"

    # -- claim / run ---------------------------------------------------------
    def register(self) -> dict:
        adapter_names = self.adapters or ("dsh", "command")
        capabilities = sorted({*supported_capabilities(), *self.extra_capabilities})
        return self.client.register_worker(
            self.worker_id,
            adapter=adapter_names[0],
            capabilities=capabilities,
            identity=f"buddy-worker:{self.worker_id}",
        )

    def run(self, *, max_iterations: int | None = None) -> None:
        self.register()
        self.reattach()
        self.heartbeat("idle")
        iterations = 0
        while not self.stop_requested():
            if self.retire_requested():
                # Retirement mode: a scale-down may complete only once every durable
                # receipt and startup intent is reconciled, and it never claims new
                # work. A retire intent therefore cannot cancel an owned child or
                # race a claim into an abandoned attempt.
                remaining = self.settle_retirement()
                if not remaining:
                    self.log("retire intent observed with no unreconciled evidence; retiring")
                    return
                self.log(f"retire intent observed with unreconciled evidence {remaining}; retrying reconciliation")
                self.stop.wait(CLAIM_IDLE_SECONDS)
                continue
            if max_iterations is not None and iterations >= max_iterations:
                return
            iterations += 1
            outcome = self.run_once()
            if outcome == "idle":
                self.stop.wait(CLAIM_IDLE_SECONDS)

    def run_once(self) -> str:
        # A durable receipt means this attempt already ran. Recovery may only replay
        # it: re-executing would spawn a second child for the same attempt.
        pending = self.spool.pending()
        if pending:
            return self.replay(pending)
        intent = self.startup_intent()
        try:
            response = self.client.claim(
                self.worker_id, intent["claimRequestId"], intent["nonce"], worker_instance=self.instance_id
            )
        except BoardError as error:
            self.log(f"claim failed: {error.code}")
            self.heartbeat("idle", lastError=error.code)
            return "idle"
        claim = response.get("claim")
        if not claim:
            reason = response.get("reason")
            if reason not in (None, "no-queued-work"):
                self.heartbeat("idle", queueReason=reason)
            else:
                self.heartbeat("idle")
            self.spool.clear_startup()
            return "idle"
        attempt = claim["attempt"]
        generation = attempt["generation"]
        # Persist the granted attempt identity before any process exists, so a
        # crash between claim and spawn is recoverable as "never started".
        self.spool.write_startup(
            {**intent, "attemptId": attempt["attemptId"], "taskId": attempt["taskId"], "generation": generation}
        )
        self.heartbeat("busy", attemptId=attempt["attemptId"], generation=generation)
        receipt = self.execute(claim)
        delivered = self.deliver(receipt)
        # The startup intent is cleared only once the result is durably committed
        # or explicitly refused; a lost daemon keeps it (and the receipt) for replay,
        # and the next round replays instead of executing again.
        if delivered:
            self.spool.clear_startup()
            self.heartbeat("idle")
            return "ran"
        self.heartbeat("replaying", attemptId=receipt["attemptId"])
        return "replay"

    def replay(self, pending: list[dict]) -> str:
        """Re-submit durable receipts only. Never executes anything."""
        delivered_all = True
        for receipt in pending:
            if not self.deliver(receipt):
                delivered_all = False
        if delivered_all:
            self.spool.clear_startup()
            self.heartbeat("idle")
            return "replayed"
        self.heartbeat("replaying", pendingReceipts=len(pending))
        return "replay"

    def execute(self, claim: dict) -> dict:
        """Run one attempt and always return a durable receipt.

        The whole body is guarded: if anything fails after the adapter was started,
        the owned process group is terminated and a receipt is still written, so a
        failure can never leave an orphaned child with no record.
        """
        started_at = time.monotonic()
        attempt = claim["attempt"]
        task = claim["task"]
        spec = task["spec"]
        directory = self.state_dir / "attempts" / task["taskId"] / attempt["attemptId"]
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(directory, 0o700)
        if self.spool.read(attempt["attemptId"]) is not None:
            # A committed receipt already exists for this attempt: never execute twice.
            self.log(f"attempt {attempt['attemptId']} already has a durable receipt; replaying only")
            return self.spool.read(attempt["attemptId"])
        # Written before the spawn. Its presence means the spawn boundary *may*
        # have been crossed, so recovery must never infer "nothing started" from a
        # missing marker; only the complete absence of this file is that proof.
        fsync_json(
            directory / "spawn.intent",
            {
                "attemptId": attempt["attemptId"],
                "taskId": task["taskId"],
                "generation": attempt["generation"],
                "adapter": spec["adapter"],
                "at": _now(),
            },
        )
        # The holder records the live child handle the instant it exists. Recovery
        # decisions are made from that handle — never from whether a marker file
        # happened to be written, which can fail after a child is already running.
        holder: dict = {"handle": None, "adapter": None, "started": False}
        try:
            return self._execute_guarded(claim, task, spec, attempt, directory, holder)
        except BaseException as error:  # noqa: BLE001 - every failure becomes a receipt
            handle = holder.get("handle")
            implementation = holder.get("adapter")
            shutdown_confirmed = False
            if handle is not None and implementation is not None:
                try:
                    implementation.cancel(handle)
                    handle.wait(timeout=15)
                    shutdown_confirmed = handle.shutdown_confirmed()
                except Exception as cleanup_error:  # noqa: BLE001 - keep the honest default
                    self.log(f"could not confirm this attempt's child stopped: {cleanup_error!r}")
                    shutdown_confirmed = False
            elif not holder.get("started"):
                # start() raised before it returned a handle, so no process exists.
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

    def _execute_guarded(
        self, claim: dict, task: dict, spec: dict, attempt: dict, directory: Path, holder: dict
    ) -> dict:
        from ..harness_health import HARNESSES
        from ..harness_runtime import bound, RECORD_FILE

        name = spec['adapter']
        if name == 'review-check':
            name = (spec.get('reviewCheck') or {}).get('adapter')
        review = name == 'decision' and (claim.get('decisionInput') or {}).get('routingMode', 'review') == 'review'
        if name == 'decision':
            name = (claim.get('decisionInput') or {}).get('profile', {}).get('adapter')
        if name not in HARNESSES:
            return self._execute_selected(claim, task, spec, attempt, directory, holder)
        history = []
        record = None
        for retry in (False, True):
            response = self.client.call('harness_prepare', {
                'workerId': self.worker_id, 'attemptId': attempt['attemptId'], 'generation': attempt['generation'],
                'nonce': self._nonce(), 'commandId': f"harness-{attempt['attemptId']}-{int(retry)}", 'retry': retry,
                'healthRevision': record.get('revision') if record else None,
            })
            record = response['harness']
            history.append({'retry': retry, 'harness': record})
            if record is None or not record.get('available'):
                if not retry:
                    continue
                if review:
                    return self._review_unavailable(claim, directory, history, 'router-unavailable')
                return self.receipt(claim, {'status': 'failed', 'result': {'harness': record, 'harnessAttempts': history},
                    'error': 'HARNESS_UNAVAILABLE: ' + str((record or {}).get('remedy') or 'Run buddy adapters with refresh:true'),
                    'exitCode': None, 'signal': None, 'shutdownConfirmed': True, 'artifacts': [],
                    'terminationReason': TERMINATION_HARNESS_ERROR}, directory)
            selection = directory / 'harness-selection.json'
            fsync_json(selection, {name: record})
            holder['harnessEnvironment'] = {RECORD_FILE: str(selection)}
            holder['harnessHistory'] = history
            holder['harnessRetry'] = retry
            try:
                with bound([record]):
                    if review:
                        native = get_adapter(name)
                        if not (native.read_only_structured and native.read_only_structured_verified):
                            return self._review_unavailable(claim, directory, history, 'router-review-unverified')
                    return self._execute_selected(claim, task, spec, attempt, directory, holder)
            except (OSError, BoardError) as error:
                # Popen/prepare failures are retryable only before a child exists.
                # Any spawned child keeps its ordinary receipt and stop ownership.
                startup_failure = (isinstance(error, BoardError) and error.code in {'ADAPTER_UNAVAILABLE', 'HARNESS_UNAVAILABLE', 'HARNESS_PREMODEL_FAILED'})
                startup_failure = startup_failure or (isinstance(error, OSError) and error.filename in record.get('command', []))
                if retry and not holder.get('started') and startup_failure:
                    self._harness_failed(attempt, record)
                    if review:
                        return self._review_unavailable(claim, directory, history, 'router-unavailable')
                if retry or holder.get('started') or not startup_failure:
                    raise
        raise AssertionError('unreachable harness retry')

    def _review_unavailable(self, claim, directory, history, code):
        from ..router import routing_facts
        return self.receipt(claim, {'status': 'failed', 'result': {
            'status': 'error', 'code': 'router-review-unavailable', 'reasonCode': code,
            'reason': 'The review harness is unavailable or its current version is not verified',
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
        from .. import runtime

        context = ExecutionContext(
            task_id=task["taskId"],
            attempt_id=attempt["attemptId"],
            generation=attempt["generation"],
            spec=spec,
            directory=directory,
            runtime=runtime.resolve_runtime(),
            environment={**os.environ, "BUDDY_STATE_DIR": str(self.state_dir), **holder.get('harnessEnvironment', {})},
            lease_seconds=self.lease_seconds,
            decision_input=claim.get("decisionInput"),
            # The service-owned turn identity and bounded context; the adapter stages
            # them for the runner. The scoped credential is written to a private file
            # and exported by path, never into the model-visible turn input.
            turn=claim.get("turn") if isinstance(claim.get("turn"), dict) else None,
            agent_credential=claim.get("agentCredential") or None,
        )
        implementation = get_adapter(spec["adapter"])
        usable, reason = implementation.available()
        if not usable:
            return self.receipt(
                claim,
                {
                    "status": "failed",
                    "result": None,
                    "error": reason,
                    "exitCode": None,
                    "signal": None,
                    "shutdownConfirmed": True,
                    "artifacts": [],
                    "terminationReason": TERMINATION_HARNESS_ERROR,
                },
                directory,
            )
        try:
            implementation.prepare(context)
        except BoardError as error:
            if holder.get('harnessHistory') and error.code == 'ADAPTER_UNAVAILABLE':
                raise
            return self.receipt(
                claim,
                {
                    "status": "failed",
                    "result": None,
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
        handle = implementation.start(context)
        holder["handle"] = handle
        holder["started"] = True
        # A durable marker for the recovery path, written while the handle is alive.
        # A failure here is logged and does not change supervision: the handle in
        # this process is the authority for stopping what it started.
        try:
            fsync_json(
                directory / "spawn.marker",
                {
                    "attemptId": attempt["attemptId"],
                    "taskId": task["taskId"],
                    "generation": attempt["generation"],
                    "adapter": spec["adapter"],
                    "pid": handle.pid,
                    "pgid": handle.pgid,
                    "at": _now(),
                },
            )
        except OSError as marker_error:
            self.log(f"could not persist the spawn marker ({marker_error!r}); the live handle still supervises")
        try:
            self.client.progress(
                self.worker_id,
                attempt["attemptId"],
                attempt["generation"],
                self._nonce(),
                "the worker spawned the adapter process",
                phase="executing",
            )
        except Exception as error:  # a progress hint is never worth losing the child
            self.log(f"progress not recorded ({error!r}); the owned child keeps running")
        renewal = _Renewal(self, claim, handle, implementation)
        renewal.start()
        try:
            timeout_seconds = int(spec["timeoutSeconds"])
            deadline = started + timeout_seconds if timeout_seconds else None
            timed_out = False
            while True:
                if self.stop_requested() and not handle.cancel_requested:
                    self.log("stop requested; cancelling this owned process group")
                    implementation.cancel(handle)
                if handle.wait(timeout=0.25) is not None:
                    break
                if deadline is not None and time.monotonic() >= deadline:
                    timed_out = True
                    self.log("worker deadline reached; cancelling this owned process group")
                    implementation.cancel(handle)
                    handle.wait(timeout=10)
                    break
        finally:
            renewal.stop()
            renewal.join(timeout=2)
        outcome = implementation.collect(handle, context)
        if (holder.get('harnessHistory') and not timed_out
                and not handle.cancel_requested and outcome.shutdown_confirmed and outcome.status == 'failed'
                and isinstance(outcome.result, dict) and outcome.result.get('modelStarted') is False
                and outcome.result.get('code') in {'adapter-unavailable', 'invalid-native-result', 'invalid-protocol', 'transport-error', 'native-rpc-error', 'protocol-error', 'native-exit', 'connection-closed'}):
            fsync_json(directory / 'harness-prestart-failure.json', {'code': outcome.result['code'], 'shutdownConfirmed': True})
            holder['started'] = False
            holder.pop('handle', None)
            raise BoardError('HARNESS_PREMODEL_FAILED', 'The native protocol failed before any model input was sent')
        if timed_out and outcome.status == "ok":
            outcome.status = "failed"
            outcome.error = f"the worker deadline of {spec['timeoutSeconds']}s was reached before the adapter finished"
        from ..partial_outputs import enrich
        enrich(context, outcome)
        report = outcome.to_report()
        if holder.get('harnessHistory'):
            report['result'] = {**(report.get('result') or {}), 'harnessAttempts': holder['harnessHistory']}
        # The genuine reason this attempt stopped. It is recorded with the receipt,
        # so a cancelled task, an expired deadline, a harness failure and a completed
        # turn stay distinguishable even after a transport outage.
        report["terminationReason"] = classify_termination(
            outcome,
            timed_out=timed_out,
            cancel_requested=bool(handle.cancel_requested),
        )
        report["elapsedSeconds"] = round(time.monotonic() - started, 1)
        report["logPaths"] = context.log_paths()
        report["runtimeIdentity"] = context.runtime.get("identity")
        return self.receipt(claim, report, directory)

    def _nonce(self) -> str:
        intent = self.spool.read_startup() or {}
        return intent.get("nonce", "")

    def receipt(self, claim: dict, report: dict, directory: Path) -> dict:
        intent = self.spool.read_startup() or {}
        attempt = claim["attempt"]
        receipt = {
            "attemptId": attempt["attemptId"],
            "taskId": attempt["taskId"],
            "generation": attempt["generation"],
            "workerId": self.worker_id,
            "nonce": intent.get("nonce"),
            "commandId": f"result:{attempt['attemptId']}",
            "report": report,
            "createdAt": _now(),
        }
        self.spool.write(attempt["attemptId"], receipt)
        self.log(f"durable completion receipt written for attempt {attempt['attemptId']}")
        return receipt

    def deliver(self, receipt: dict) -> bool:
        """Submit one receipt until the service confirms it, or the retry budget ends."""
        deadline = time.monotonic() + self.retry_seconds
        attempt_id = receipt["attemptId"]
        while True:
            try:
                response = self.client.submit_result(
                    receipt["workerId"],
                    attempt_id,
                    receipt["generation"],
                    receipt["nonce"],
                    {**receipt["report"], "commandId": receipt["commandId"]},
                )
            except BoardError as error:
                if error.code in ("CONFLICT", "STALE_GENERATION", "UNAUTHORIZED", "ATTEMPT_FINISHED"):
                    self.log(f"result for {attempt_id} not accepted ({error.code}); dropping the receipt")
                    self.spool.drop(attempt_id)
                    return False
                self.log(f"result for {attempt_id} not committed yet ({error.code}); will retry")
            except Exception as error:  # transport-level failure
                self.log(f"board unreachable while committing {attempt_id} ({error!r}); will retry")
            else:
                self.log(f"result for {attempt_id} committed as {response.get('taskState')}")
                self.spool.drop(attempt_id)
                return True
            if self.stop_requested() and time.monotonic() > deadline:
                return False
            if time.monotonic() >= deadline:
                self.log(
                    f"giving up on {attempt_id} after {self.retry_seconds}s; the receipt stays on disk and is "
                    "replayed by the next supervisor start"
                )
                return False
            time.sleep(1.0)


class _Renewal(threading.Thread):
    """Lease renewal and durable cancel-intent observation, independent of the daemon."""

    def __init__(self, worker: Worker, claim: dict, handle, implementation):
        super().__init__(name="buddy-worker-renewal", daemon=True)
        self.worker = worker
        self.claim = claim
        self.handle = handle
        self.implementation = implementation
        self._done = threading.Event()
        self.attempt = claim["attempt"]
        self.nonce = worker._nonce()
        #: The last native activity this thread forwarded, so a repeated sidecar is
        #: not published twice and the projection only moves forward.
        self._activity: dict | None = None

    #: How often the durable cancel intent is checked with a read-only call. This is
    #: deliberately independent of the lease renewal period: a committed cancel must
    #: be observed in seconds, not after the next lease interval.
    CANCEL_POLL_SECONDS = 2.0

    def run(self) -> None:
        renew_interval = max(5.0, self.worker.lease_seconds / 3)
        next_renew = time.monotonic() + renew_interval
        self._forward_activity()
        while not self._done.wait(self.CANCEL_POLL_SECONDS):
            if self._cancel_observed():
                return
            self._forward_activity()
            if time.monotonic() >= next_renew:
                if not self._renew():
                    return
                next_renew = time.monotonic() + renew_interval

    def _cancel_observed(self) -> bool:
        """Read-only cancel-intent check; never writes and never extends the lease."""
        try:
            view = self.worker.client.call(
                "task_get", {"runId": self.attempt["taskId"]}
            )["task"]
        except Exception:
            return False
        if not view.get("cancelRequested"):
            return False
        if not self.handle.cancel_requested:
            self.worker.log("durable cancel intent observed; cancelling this owned process group")
            self.implementation.cancel(self.handle)
        return True

    def _renew(self) -> bool:
        """Renew the lease; returns False when this worker must stop touching it."""
        try:
            response = self.worker.client.renew(
                self.worker.worker_id,
                self.attempt["attemptId"],
                self.attempt["generation"],
                self.nonce,
            )
        except BoardError as error:
            if error.code in ("STALE_GENERATION", "UNAUTHORIZED", "ATTEMPT_FINISHED"):
                self.worker.log(f"renew refused ({error.code}); this worker stops touching the attempt")
                self._done.set()
                return False
            return True
        except Exception:
            # The daemon may be down: the worker keeps its child and deadline and
            # reattaches later with the same attempt identity.
            return True
        if response.get("cancelRequested") and not self.handle.cancel_requested:
            self.worker.log("durable cancel intent observed; cancelling this owned process group")
            self.implementation.cancel(self.handle)
            return False
        if response.get("finished"):
            # A committed result is terminal and immutable; the completion path in
            # the owning thread already has (or is about to write) its own receipt.
            self.worker.log(f"attempt {self.attempt['attemptId']} is already finished; stopping renewal")
            self._done.set()
            return False
        if response.get("uncertain") or response.get("reconciliationRequired"):
            return self._recover()
        return True

    def _owns_live_child(self) -> bool:
        """Whether this process still holds a live handle for the attempt it claimed."""
        return self.handle is not None and not self.handle.shutdown_confirmed()

    def _recover(self) -> bool:
        """Reattach an attempt the service marked uncertain after a restart.

        Recovery needs two independent facts: this process is the same worker
        *instance* that claimed the attempt (the service verifies that and rejects
        anything else), and it still owns the live child handle. A durable completion
        receipt is replayed instead of reconciling execution state, because the child
        already produced its outcome and must never be re-entered.
        """
        attempt_id = self.attempt["attemptId"]
        receipt = self.worker.spool.read(attempt_id)
        if receipt is not None:
            # The child already produced a durable outcome. Replay that receipt; the
            # execution state must never be reconciled back to executing when the
            # attempt is in fact complete.
            self.worker.log(f"attempt {attempt_id} already has a durable receipt; replaying it instead of reconciling")
            if self.worker.deliver(receipt) or self.worker.spool.read(attempt_id) is None:
                self._done.set()
                return False
            return True
        if not self._owns_live_child():
            # No handle means no evidence: the attempt stays explicitly uncertain
            # instead of being adopted by a process that cannot observe it.
            self.worker.log(
                f"attempt {attempt_id} needs reconciliation but this process no longer holds a live child "
                "handle; it stays uncertain"
            )
            return True
        try:
            response = self.worker.client.reconcile(
                self.worker.worker_id,
                attempt_id,
                self.attempt["generation"],
                self.nonce,
                worker_instance=self.worker.instance_id,
            )
        except BoardError as error:
            if error.code in ("STALE_GENERATION", "UNAUTHORIZED", "ATTEMPT_FINISHED", "NOT_FOUND"):
                self.worker.log(f"reconciliation refused ({error.code}); this worker stops touching the attempt")
                self._done.set()
                return False
            return True
        except Exception:
            # An unreachable daemon says nothing about ownership: keep the child, the
            # deadline and this recovery identity, and retry on the next tick.
            return True
        if response.get("finished") or response.get("immutable"):
            self.worker.log(f"attempt {attempt_id} is terminal and immutable; stopping renewal")
            self._done.set()
            return False
        state = (response.get("attempt") or {}).get("executionState")
        queue_reason = response.get("queueReason")
        self.worker.log(
            f"reattached attempt {attempt_id} as {state}"
            + (f" (waiting reason cleared, was {queue_reason!r})" if response.get("restored") else "")
        )
        return True

    def _forward_activity(self) -> None:
        """Forward one changed, attempt-bound native activity receipt, never a heartbeat."""
        directory = self.worker.attempt_directory(self.attempt.get("taskId"), self.attempt["attemptId"])
        payload = activity_module.read_sidecar(
            activity_module.sidecar_path(directory),
            task_id=self.attempt["taskId"],
            attempt_id=self.attempt["attemptId"],
            generation=self.attempt["generation"],
        )
        if payload is None or not activity_module.is_newer(payload, self._activity):
            return
        try:
            # Structured activity only: no prose progress event is fabricated for a
            # native observation that the service records as a projection.
            self.worker.client.progress(
                self.worker.worker_id,
                self.attempt["attemptId"],
                self.attempt["generation"],
                self.nonce,
                data={"activity": payload},
            )
        except Exception as error:  # a progress hint is never worth losing the child
            self.worker.log(f"activity not recorded ({error!r}); the owned child keeps running")
            return
        self._activity = payload

    def stop(self) -> None:
        self._done.set()


def _now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


__all__ = [
    "ReceiptSpool",
    "RETIRE_REQUEST_NAME",
    "Worker",
    "classify_termination",
    "fsync_json",
    "is_transport_failure",
]
