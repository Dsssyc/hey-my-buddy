"""M1 scheduling Worker: owns an execution handle, its lease and stop forwarding.

Native work and durable result delivery belong to the isolated execution. This
process retains claim, launch identity and orphan recovery until M2 removes it.
"""
from __future__ import annotations

import json
import os
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from ...protocol.stop import WRAP_UP_INITIATORS
from ..harnesses.registry import supported_capabilities
from ..roles import controller as role_seam
from ...protocol.client import BoardClient, new_nonce
from ...blackboard.store.db import TERMINATION_REASONS
from ...errors import BoardError

RECOVERY_RETRY_SECONDS = 2.0
IDLE_WAIT_TIMEOUT_MS = 2000
PREFLIGHT_RETRY_SECONDS = 15.0
PREFLIGHT_MAX_RETRY_SECONDS = 180.0


def supervisor_start_stop_path(directory: Path) -> Path | None:
    """A daemon-created supervisor's private startup-abort channel."""
    identity = os.environ.get("HMB_SUPERVISOR_START_ID", "")
    if len(identity) == 32 and all(char in "0123456789abcdef" for char in identity):
        return directory / f"stop-{identity}.request"
    return None

(
    TERMINATION_COMPLETED,
    TERMINATION_WRAP_UP,
    TERMINATION_GRACE_EXPIRED,
    TERMINATION_FORCED_STOP,
    TERMINATION_CONSOLE_STOP,
    TERMINATION_SERVICE_STOP,
    TERMINATION_UNKNOWN_STOP,
    TERMINATION_DEADLINE,
    TERMINATION_HARNESS_ERROR,
    TERMINATION_TRANSPORT_ERROR,
    TERMINATION_INTERRUPTED,
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


def classify_termination(outcome, *, timed_out: bool, cancel_requested: bool,
                         stop_initiator: str | None = None, grace_expired: bool = False) -> str | None:
    """The real reason this attempt stopped.

    A real completion keeps its reason; an exhausted Router budget and transport
    failure keep theirs. Stop classification belongs to the shared role layer.
    """
    if outcome.status == "ok":
        return TERMINATION_COMPLETED
    if timed_out:
        return TERMINATION_DEADLINE
    if cancel_requested:
        return role_seam.stop_termination_reason(shutdown_confirmed=outcome.shutdown_confirmed,
            stop_initiator=stop_initiator,
            grace_expired=outcome.result.get("graceExpired", grace_expired),
            wrap_up_delivered=outcome.result.get("wrapUpDelivered", False))
    if is_transport_failure(getattr(outcome, "result", None)):
        return TERMINATION_TRANSPORT_ERROR
    return TERMINATION_HARNESS_ERROR


class WorkerState:
    """The scheduling process owns startup intent; results belong to executions."""

    def __init__(self, directory: Path, worker_id: str):
        self.directory = Path(directory) / "workers" / worker_id
        self.startup_path = self.directory / "startup.json"

    def ensure(self) -> None:
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)

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

    def recovery_intents(self) -> list[dict]:
        intents = []
        startup = self.read_startup()
        if startup is not None:
            intents.append(startup)
        orphan = self.directory / "orphaned.json"
        if orphan.exists():
            intents.append(json.loads(orphan.read_text()))
        return intents



class Worker:
    """One independent worker process body."""

    def __init__(
        self,
        worker_id: str,
        state_dir: Path,
        *,
        client: BoardClient | None = None,
        lease_seconds: int = 120,
        adapters: tuple[str, ...] | None = None,
        extra_capabilities: tuple[str, ...] = (),
        stop: threading.Event | None = None,
        log: Callable[[str], None] | None = None,
    ):
        from ...protocol.rpc_config import configure_server, configure_client
        configure_server(state_dir)
        configure_client(state_dir)
        self.worker_id = worker_id
        # One identity per worker *process*: a new process cannot prove it owns a
        # child that a previous process spawned, so it must never resume that work.
        self.instance_id = f"{worker_id}-{uuid.uuid4()}"
        self.state_dir = state_dir
        self.client = client or BoardClient(self.state_dir, autostart=False)
        from .live import WorkerLiveRuntime
        from ..roles.live import handle_live_binding
        self.live = WorkerLiveRuntime(self.client, worker_id=self.worker_id,
                                      worker_instance=self.instance_id, state_dir=self.state_dir,
                                      resolve_channel=lambda handle: handle_live_binding(handle, state_dir=self.state_dir))
        self.lease_seconds = lease_seconds
        self.adapters = adapters
        self.extra_capabilities = tuple(extra_capabilities)
        self.stop = stop or threading.Event()
        self.spool = WorkerState(self.state_dir, worker_id)
        self._log = log or (lambda message: None)
        self._idle_cursor = 0
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
        receipt, a startup intent and a preserved orphan that still name an
        attempt are evidence a restarted surplus owner must settle first; a
        missing process never proves an attempt stopped, so none is dropped to
        let a retire intent win.
        """
        pending: list[str] = []
        if self.pending_receipts():
            pending.append("pending-receipts")
        intent = self.spool.read_startup()
        if isinstance(intent, dict) and intent.get("attemptId"):
            pending.append(f"startup-intent:{intent['attemptId']}")
        orphan_path = self.spool.directory / "orphaned.json"
        if orphan_path.exists():
            orphan = json.loads(orphan_path.read_text())
            pending.append(f"orphaned-attempt:{orphan['attemptId']}")
        return pending

    def preserve_orphan_startup(self, intent: dict) -> None:
        """Record a previous process's intent as orphaned evidence and clear it.

        The orphan record is durable before the intent is cleared. Its nonce can
        publish the already recorded process identity, but cannot reattach a child.
        """
        fsync_json(
            self.spool.directory / "orphaned.json",
            {**intent, "orphanedAt": _now(), "orphanedReason": "the worker process that owned this intent exited",
             "identityPublished": False},
        )
        self.log(
            f"found an orphaned startup intent for attempt {intent.get('attemptId')}; "
            "the previous worker's process identity must be checked"
        )
        self.spool.clear_startup()

    def startup_intent(self) -> dict:
        self.spool.ensure()
        intent = self.spool.read_startup()
        if intent is not None and intent.get("instanceId") not in (None, self.instance_id):
            # An unclaimed startup has no child. A claimed startup retains its
            # recovery identity until the replacement verifies the old owner.
            if intent.get("attemptId"):
                self.preserve_orphan_startup(intent)
            else:
                self.spool.clear_startup()
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
        for receipt in self.pending_receipts():
            self.log(f"replaying receipt for attempt {receipt.get('attemptId')} before retiring")
            self.deliver(receipt)
        intent = self.spool.read_startup()
        if isinstance(intent, dict) and intent.get("attemptId"):
            if intent.get("instanceId") not in (None, self.instance_id):
                self.preserve_orphan_startup(intent)
            else:
                self.reconcile_startup_intent(intent)
        self.recover_orphans()
        return self.recovery_pending()

    def reattach(self) -> None:
        """Reconcile anything this worker owned before it or the daemon restarted."""
        for receipt in self.pending_receipts():
            self.log(f"replaying receipt for attempt {receipt.get('attemptId')}")
            self.deliver(receipt)
        intent = self.spool.read_startup()
        if not intent or not intent.get("attemptId"):
            return
        if intent.get("instanceId") not in (None, self.instance_id):
            self.preserve_orphan_startup(intent)
            return
        self.reconcile_startup_intent(intent)

    def _record_process_identity(self, attempt: dict, identity: dict, *,
                                 previous_process_stopped: bool = False, data: dict | None = None) -> None:
        """Publish the durable local observation to the authoritative board."""
        self.client.call("worker_progress", {
            "workerId": self.worker_id,
            "attemptId": attempt["attemptId"],
            "generation": attempt["generation"],
            "nonce": self._nonce(),
            "processIdentity": identity,
            "previousProcessStopped": previous_process_stopped,
            "phase": "executing",
            **({"data": data} if data is not None else {}),
        })

    def _publish_orphan_identity(self) -> None:
        """Close the local-marker / service-write gap after the old Worker died."""
        from ...protocol.process_identity import worker_identity_alive

        orphan_path = self.spool.directory / "orphaned.json"
        try:
            intent = json.loads(orphan_path.read_text())
        except FileNotFoundError:
            return
        if intent.get("identityPublished") is True:
            return
        marker_path = self.spawn_marker(intent)
        try:
            marker = json.loads(marker_path.read_text())
        except FileNotFoundError:
            return
        records = [marker]
        try:
            for record in records:
                identity = record.get("processIdentity")
                if identity is None or worker_identity_alive(identity) is not False:
                    return
                self.client.call("worker_progress", {
                    "workerId": intent["workerId"],
                    "attemptId": intent["attemptId"],
                    "generation": intent["generation"],
                    "nonce": intent["nonce"],
                    "processIdentity": identity,
                    "previousProcessStopped": record.get("previousProcessStopped") is True,
                })
            fsync_json(orphan_path, {**intent, "identityPublished": True})
        except BoardError as error:
            if error.code == "ATTEMPT_FINISHED":
                orphan_path.unlink()
                return
            self.log(f"orphan identity publication unavailable ({error.code})")

    def recover_orphans(self) -> bool:
        """Stop only a still verified orphan group and report evidence for the service sweep."""
        from ...protocol.process_identity import (
            read_native_process_journal, stop_orphan_process,
        )

        self._publish_orphan_identity()
        orphan_path = self.spool.directory / 'orphaned.json'
        orphan = json.loads(orphan_path.read_text()) if orphan_path.exists() else None
        try:
            response = self.client.call("worker_recover", {
                "workerId": self.worker_id,
                **({'observeAttemptId': orphan['attemptId']} if orphan is not None else {}),
            })
            candidates = response['candidates']
        except BoardError as error:
            self.log(f"orphan recovery unavailable ({error.code})")
            return False
        observed = response.get('observedAttempt')
        if (observed is not None and observed['executionState'] == 'finished'
                and observed['shutdownConfirmed']):
            orphan_path.unlink()
        reported = True
        for candidate in candidates:
            identity = candidate["processIdentity"]
            # The process-identity boundary checks the complete boot/PID/start
            # tuple again immediately before it sends any group or Job signal.
            evidence = stop_orphan_process(identity)
            if evidence == "unknown":
                continue
            journal_spec = candidate.get("nativeJournal")
            if journal_spec is not None and evidence != "boot-changed":
                # The controller is now absent. Its journal is stable; a pending
                # spawn or unreadable record leaves the complete tree unknown.
                journal = read_native_process_journal(
                    Path(journal_spec["path"]), journal_spec["binding"])
                if journal is None or journal["pending"]:
                    continue
                native_stopped = False
                native_unknown = False
                for native in journal["identities"]:
                    native_evidence = stop_orphan_process(native)
                    if native_evidence == "unknown":
                        native_unknown = True
                        break
                    native_stopped |= native_evidence == "worker-stopped"
                if native_unknown:
                    continue
                if native_stopped:
                    evidence = "worker-stopped"
            try:
                response = self.client.call("worker_recover", {
                    "workerId": self.worker_id,
                    "attemptId": candidate["attemptId"],
                    "evidence": evidence,
                })
                orphan_path = self.spool.directory / "orphaned.json"
                if response["settled"] and orphan_path.exists():
                    orphan = json.loads(orphan_path.read_text())
                    if orphan.get("attemptId") == candidate["attemptId"]:
                        orphan_path.unlink()
            except BoardError as error:
                self.log(f"orphan settlement unavailable ({error.code})")
                reported = False
        return reported

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
            identity=f"hey-my-buddy-worker:{self.worker_id}",
        )

    def run(self, *, max_iterations: int | None = None) -> None:
        self.live.start()
        try:
            self.register()
            self.reattach()
            self.recover_orphans()
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
                    self.stop.wait(RECOVERY_RETRY_SECONDS)
                    continue
                if max_iterations is not None and iterations >= max_iterations:
                    return
                iterations += 1
                outcome = self.run_once()
                if max_iterations is not None and iterations >= max_iterations:
                    return
                if outcome == "idle":
                    self.wait_for_work()
                elif outcome == "retry":
                    self.stop.wait(RECOVERY_RETRY_SECONDS)
        finally:
            self.live.stop()

    def wait_for_work(self) -> None:
        """Wait after the last consumed event page; a timeout only renews the wait."""
        try:
            with self.client.event_waiter(worker_id=self.worker_id) as wait:
                while not self.stop_requested() and not self.retire_requested():
                    page = wait(after=self._idle_cursor, timeout_ms=IDLE_WAIT_TIMEOUT_MS, limit=1)
                    if page["events"]:
                        if page["recoveryNeeded"] and not self.recover_orphans():
                            self.stop.wait(RECOVERY_RETRY_SECONDS)
                            return
                        self._idle_cursor = page["head"]
                        return
        except BoardError as error:
            self.log(f"event wait failed: {error.code}")
            if not self.stop_requested() and not self.retire_requested():
                self.stop.wait(RECOVERY_RETRY_SECONDS)

    def run_once(self) -> str:
        # A durable receipt means this attempt already ran. Recovery may only replay
        # it: re-executing would spawn a second child for the same attempt.
        pending = self.pending_receipts()
        if pending:
            return self.replay(pending)
        intent = self.startup_intent()
        try:
            response = self.client.claim(
                self.worker_id, intent["claimRequestId"], intent["nonce"], worker_instance=self.instance_id
            )
        except BoardError as error:
            self.log(f"claim failed: {error.code}")
            # A replacement has no live handle for the previous process's claim.
            # Wait for its uncertainty event so this slot can recover that orphan.
            return "idle" if error.code == "WORKER_BUSY" else "retry"
        claim = response.get("claim")
        if not claim:
            # Only a consumed wait page advances the event cursor. A claim's
            # snapshot could skip an uncertainty event raised since the last wake.
            self.spool.clear_startup()
            return "idle"
        attempt = claim["attempt"]
        generation = attempt["generation"]
        # Persist the granted attempt identity before any process exists, so a
        # crash between claim and spawn is recoverable as "never started".
        self.spool.write_startup(
            {**intent, "attemptId": attempt["attemptId"], "taskId": attempt["taskId"], "generation": generation}
        )
        self.execute(claim)
        self.spool.clear_startup()
        return "ran"

    def pending_receipts(self):
        from .receipts import read_receipt
        intents = self.spool.recovery_intents()
        receipts = []
        for intent in intents:
            if intent and intent.get("attemptId"):
                receipt = read_receipt(self.attempt_directory(intent.get("taskId"), intent["attemptId"]))
                if receipt is not None:
                    receipts.append(receipt)
        return receipts

    def replay(self, pending: list[dict]) -> str:
        for receipt in pending:
            self.deliver(receipt)
        self.spool.clear_startup()
        return "replayed"

    def execute(self, claim: dict):
        """Schedule one isolated execution; its process owns all native work and reporting."""
        from .execution import launch_execution, stop_execution
        from .receipts import DELIVERY, read_receipt
        attempt = claim["attempt"]
        directory = self.attempt_directory(attempt["taskId"], attempt["attemptId"])
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        if read_receipt(directory) is not None or (directory / DELIVERY).exists():
            return self._settle_execution(claim, None)
        fsync_json(directory / "spawn.intent", {"attemptId": attempt["attemptId"],
            "taskId": attempt["taskId"], "generation": attempt["generation"],
            "adapter": claim["task"]["spec"]["adapter"], "at": _now()})
        handle = None
        renewal = _Renewal(self, claim, None, None)
        try:
            handle = launch_execution(claim, self.state_dir, self.worker_id, self._nonce(),
                                      lease_seconds=self.lease_seconds)
            identity = handle.identity()
            fsync_json(directory / "spawn.marker", {"attemptId": attempt["attemptId"],
                "taskId": attempt["taskId"], "generation": attempt["generation"],
                "adapter": claim["task"]["spec"]["adapter"], "processIdentity": identity,
                "previousProcessStopped": False, "at": _now()})
            try:
                self._record_process_identity(attempt, identity)
            except Exception as error:
                self.log(f"execution identity publication pending ({error!r})")
                renewal.unpublished_identity = (identity, False)
            renewal.attach(handle, None)
            renewal.start()
            while handle.wait(timeout=0.25) is None:
                if self.stop_requested() and not handle.cancel_requested:
                    stop_execution(handle)
            return self._settle_execution(claim, handle)
        except BaseException as error:
            self.log(f"execution launch or supervision failed ({error!r})")
            if handle is not None:
                stop_execution(handle, force=True)
                if handle.wait(timeout=15) is None:
                    handle.terminate(grace_seconds=0)
            return self._settle_execution(claim, handle)
        finally:
            renewal.stop()
            if renewal.ident is not None:
                renewal.join()
            self.live.unbind(claim)
            if handle is not None:
                from ..roles.live import release_live_binding
                release_live_binding(handle)

    def _settle_execution(self, claim, handle):
        """Only a reaped execution can hand its pending receipt back to its Worker."""
        from .receipts import DELIVERY, read_receipt, write_receipt
        attempt = claim["attempt"]
        directory = self.attempt_directory(attempt["taskId"], attempt["attemptId"])
        if (directory / DELIVERY).exists():
            return None
        receipt = read_receipt(directory)
        if receipt is None:
            receipt = {"attemptId": attempt["attemptId"], "taskId": attempt["taskId"],
                "generation": attempt["generation"], "workerId": self.worker_id, "nonce": self._nonce(),
                "commandId": f"result:{attempt['attemptId']}", "createdAt": _now(),
                "report": {"status": "failed", "result": None,
                    "error": "execution exited without a durable result receipt",
                    "exitCode": handle.process.returncode if handle is not None else None,
                    "signal": None, "shutdownConfirmed": False, "artifacts": [],
                    "terminationReason": TERMINATION_HARNESS_ERROR}}
            write_receipt(directory, receipt)
        self.deliver(receipt)
        return receipt

    def _nonce(self):
        return (self.spool.read_startup() or {}).get("nonce", "")

    def deliver(self, receipt):
        from .receipts import deliver_receipt
        directory = self.attempt_directory(receipt["taskId"], receipt["attemptId"])
        return deliver_receipt(self.client, receipt, directory, log=self.log)


class _Renewal(threading.Thread):
    """Lease renewal and durable cancel-intent observation, independent of the daemon."""

    def __init__(self, worker: Worker, claim: dict, handle, implementation):
        super().__init__(name="hey-my-buddy-worker-renewal", daemon=True)
        self.worker = worker
        self.claim = claim
        self.handle = handle
        self.implementation = implementation
        self._done = threading.Event()
        self._wake = threading.Event()
        self._binding_lock = threading.RLock()
        self.cancel_view: dict | None = None
        self.attempt = claim["attempt"]
        self.nonce = worker._nonce()
        #: An identity (with its previous-process-stopped fact) whose first
        #: publication failed; each renewal retries it.
        self.unpublished_identity: tuple[dict, bool] | None = None
    #: How often the durable cancel intent is checked with a read-only call. This is
    #: deliberately independent of the lease renewal period: a committed cancel must
    #: be observed in seconds, not after the next lease interval.
    CANCEL_POLL_SECONDS = 2.0

    def run(self) -> None:
        renew_interval = max(5.0, self.worker.lease_seconds / 3)
        next_renew = time.monotonic() + renew_interval
        while not self._done.wait(self.CANCEL_POLL_SECONDS):
            if self.worker.stop_requested():
                self._wake.set()
            if self._cancel_observed():
                return
            if self.handle is not None:
                self._refresh_live_binding()
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
        return self._request_cancel(view)

    def _request_cancel(self, view: dict) -> bool:
        with self._binding_lock:
            self.cancel_view = view
            self._wake.set()
            if self.handle is None:
                return False
            self._stop_handle(view)
        return False

    def attach(self, handle, implementation) -> None:
        """Hand native ownership to the same renewal that supervised preparation."""
        with self._binding_lock:
            self.handle = handle
            self.implementation = implementation
            if self.cancel_view is not None:
                self._stop_handle(self.cancel_view)

    def wait(self, seconds: float) -> None:
        """Back off an unknown preflight; a stop fact wakes the owning thread."""
        self._wake.wait(seconds)

    def _stop_handle(self, view: dict) -> None:
        initiator = view.get("stopInitiator") or "unknown"
        current = self.handle.stop_initiator
        if not self.handle.cancel_requested or current != initiator:
            deadline = None
            if initiator in WRAP_UP_INITIATORS and view.get("graceDeadline") is not None:
                remaining = (datetime.fromisoformat(view["graceDeadline"].replace("Z", "+00:00"))
                             - datetime.now(timezone.utc)).total_seconds()
                deadline = time.monotonic() + max(0, remaining)
            self.worker.log(f"durable {initiator} stop intent observed")
            from .execution import stop_execution
            stop_execution(self.handle, initiator=initiator, grace_deadline=deadline)
        # During a summary turn keep the lease and keep observing a console
        # escalation. The original durable deadline is never extended.

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
        if self.unpublished_identity is not None:
            identity, previous_process_stopped = self.unpublished_identity
            try:
                self.worker._record_process_identity(
                    self.attempt, identity, previous_process_stopped=previous_process_stopped)
                self.unpublished_identity = None
            except Exception as error:
                self.worker.log(f"process identity publication still unavailable ({error!r})")
        if response.get("cancelRequested") and self._request_cancel(response):
            return False
        if response.get("finished"):
            # A committed result is terminal and immutable; the completion path in
            # the owning thread already has (or is about to write) its own receipt.
            self.worker.log(f"attempt {self.attempt['attemptId']} is already finished; stopping renewal")
            self._done.set()
            return False
        if response.get("uncertain") or response.get("reconciliationRequired"):
            return self._recover()
        if self.handle is not None:
            self._refresh_live_binding(force=True)
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
        from .receipts import read_receipt
        receipt = read_receipt(self.worker.attempt_directory(self.attempt["taskId"], attempt_id))
        if receipt is not None:
            # A live execution delivers its own result even while the service
            # is restarting. Only the reaping thread may recover a crashed sender.
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
        self._refresh_live_binding(force=True)
        return True

    def _refresh_live_binding(self, *, force=False) -> None:
        try:
            from ..roles.live import handle_live_binding
            previous = self.handle.role_run_identity
            state, channel = handle_live_binding(self.handle, state_dir=self.worker.state_dir)
            if state != "bound":
                return
            if previous != self.handle.role_run_identity:
                self._live_rebind = True
            if getattr(self, "_live_rebind", False):
                # A failed withdrawal leaves a same-key fence in WorkerLiveRuntime.
                # Retry that withdrawal on every tick, including after a service
                # restart. A partial new attachment may also have closed its channel.
                self.worker.live.unbind(self.claim)
                channel.close(reason="execution-endpoint-rebind")
                self.handle.role_live_channel = None
                state, channel = handle_live_binding(self.handle, state_dir=self.worker.state_dir)
                if state != "bound":
                    return
            if (force or getattr(self, "_live_rebind", False)
                    or getattr(self, "_live_identity", None) != self.handle.role_run_identity):
                if self.worker.live.bind(self.claim, self.handle, self.nonce):
                    self._live_identity = self.handle.role_run_identity
                    self._live_rebind = False
        except Exception:
            # Live loss is independent of lease and process supervision.
            pass

    def stop(self) -> None:
        self._done.set()
        self._wake.set()


def _now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


__all__ = [
    "WorkerState",
    "RETIRE_REQUEST_NAME",
    "Worker",
    "classify_termination",
    "fsync_json",
    "is_transport_failure",
]
