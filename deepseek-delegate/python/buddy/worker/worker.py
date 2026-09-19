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

from ..adapters import ExecutionContext, adapter as get_adapter, local_capabilities
from ..client import BoardClient, new_nonce
from ..errors import BoardError

DEFAULT_RETRY_SECONDS = 120
CLAIM_IDLE_SECONDS = 2.0


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

    def log(self, message: str) -> None:
        self._log(f"[worker {self.worker_id}] {message}")

    # -- lifecycle -----------------------------------------------------------
    def stop_requested(self) -> bool:
        return self.stop.is_set() or self.stop_request_path.exists()

    def heartbeat(self, state: str, **extra: Any) -> None:
        fsync_json(
            self.heartbeat_path,
            {"workerId": self.worker_id, "pid": os.getpid(), "state": state, "at": _now(), **extra},
        )

    def startup_intent(self) -> dict:
        self.spool.ensure()
        intent = self.spool.read_startup()
        if intent is not None and intent.get("instanceId") not in (None, self.instance_id):
            # A leftover intent from a previous worker process. Its nonce is not
            # ours to present and its child handle is gone, so it is preserved as
            # orphaned evidence instead of being adopted or silently released.
            fsync_json(
                self.spool.directory / "orphaned.json",
                {**intent, "orphanedAt": _now(), "orphanedReason": "the worker process that owned this intent exited"},
            )
            self.log(
                f"found an orphaned startup intent for attempt {intent.get('attemptId')}; it stays uncertain and is "
                "not adopted by this process"
            )
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

    def reattach(self) -> None:
        """Reconcile anything this worker owned before it or the daemon restarted.

        Three durable states decide this, never the database's ``starting`` row and
        never a PID check:

        * no ``spawn.intent``  -> this worker never even reached the spawn boundary,
          so releasing the attempt is honest and frees its claims;
        * ``spawn.intent`` without ``spawn.marker`` -> an ambiguous window: the
          process may or may not exist, so the attempt stays uncertain;
        * ``spawn.marker`` -> a child definitely existed and its fate is unknown.

        The last two never release anything: a survivor must not lose its resource
        claims just because this worker restarted.
        """
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
        attempted = self.spawn_intent_path(intent).exists()
        if not attempted:
            # Durable proof from this worker's own disk that the spawn boundary was
            # never reached: an authenticated release, never a PID inference.
            try:
                self.client.release(
                    self.worker_id,
                    intent["attemptId"],
                    intent.get("generation", 0),
                    intent["nonce"],
                    "no durable spawn intent exists for this attempt, so no process was ever created",
                    evidence={"spawnIntentWritten": False},
                    worker_instance=self.instance_id,
                )
                self.log(f"released never-spawned attempt {intent['attemptId']} with spawn-intent evidence")
                self.spool.clear_startup()
                return
            except BoardError as error:
                if error.code in ("SERVICE_UNAVAILABLE", "SERVICE_START_TIMEOUT", "SERVICE_START_FAILED", "INVALID_RESPONSE"):
                    self.log(f"release of {intent['attemptId']} unavailable ({error.code}); keeping the intent")
                    return
                self.log(f"evidence-based release refused ({error.code}); falling back to reconcile")
        try:
            response = self.client.reconcile(
                self.worker_id,
                intent["attemptId"],
                intent.get("generation", 0),
                intent["nonce"],
                worker_instance=self.instance_id,
            )
        except BoardError as error:
            if error.code in ("SERVICE_UNAVAILABLE", "SERVICE_START_TIMEOUT", "SERVICE_START_FAILED", "INVALID_RESPONSE"):
                # A transport failure says nothing about ownership: keep the recovery
                # identity and try again later instead of discarding it.
                self.log(f"reconcile of {intent['attemptId']} unavailable ({error.code}); keeping the intent")
                return
            self.log(f"reconcile of {intent['attemptId']} refused: {error.code}")
            self.spool.clear_startup()
            return
        state = response["attempt"]["executionState"]
        if response.get("finished") or response.get("immutable"):
            self.log(f"attempt {intent['attemptId']} is terminal; nothing to reconcile")
            self.spool.clear_startup()
            return
        if False:
            pass
        else:
            # The spawn boundary may have been crossed, so this process has no
            # evidence and the attempt keeps its uncertain ownership and claims.
            self.log(
                f"attempt {intent['attemptId']} is {state} and its spawn boundary was possibly crossed; its process "
                "handle is gone, so it stays explicitly uncertain with its resource claims retained"
            )
            return
        self.spool.clear_startup()

    def attempt_directory(self, task_id: str | None, attempt_id: str) -> Path:
        return self.state_dir / "attempts" / (task_id or "unknown") / attempt_id

    def spawn_intent_path(self, intent: dict) -> Path:
        return self.attempt_directory(intent.get("taskId"), intent["attemptId"]) / "spawn.intent"

    def spawn_marker(self, intent: dict) -> Path:
        return self.attempt_directory(intent.get("taskId"), intent["attemptId"]) / "spawn.marker"

    # -- claim / run ---------------------------------------------------------
    def register(self) -> dict:
        adapter_names = self.adapters or ("dsh", "command")
        capabilities = sorted({*local_capabilities(), *self.extra_capabilities})
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
                    "result": None,
                    "error": f"worker failure: {error!r}",
                    "exitCode": None,
                    "signal": None,
                    "shutdownConfirmed": shutdown_confirmed,
                    "artifacts": [],
                },
                directory,
            )

    def _execute_guarded(
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
            environment={**os.environ, "BUDDY_STATE_DIR": str(self.state_dir)},
            lease_seconds=self.lease_seconds,
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
                },
                directory,
            )
        try:
            implementation.prepare(context)
        except BoardError as error:
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
            deadline = started + int(spec["timeoutSeconds"])
            timed_out = False
            while True:
                if self.stop_requested() and not handle.cancel_requested:
                    self.log("stop requested; cancelling this owned process group")
                    implementation.cancel(handle)
                if handle.wait(timeout=0.25) is not None:
                    break
                if time.monotonic() >= deadline:
                    timed_out = True
                    self.log("worker deadline reached; cancelling this owned process group")
                    implementation.cancel(handle)
                    handle.wait(timeout=10)
                    break
        finally:
            renewal.stop()
            renewal.join(timeout=2)
        outcome = implementation.collect(handle, context)
        if timed_out and outcome.status == "ok":
            outcome.status = "failed"
            outcome.error = f"the worker deadline of {spec['timeoutSeconds']}s was reached before the adapter finished"
        report = outcome.to_report()
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

    #: How often the durable cancel intent is checked with a read-only call. This is
    #: deliberately independent of the lease renewal period: a committed cancel must
    #: be observed in seconds, not after the next lease interval.
    CANCEL_POLL_SECONDS = 2.0

    def run(self) -> None:
        renew_interval = max(5.0, self.worker.lease_seconds / 3)
        next_renew = time.monotonic() + renew_interval
        while not self._done.wait(self.CANCEL_POLL_SECONDS):
            if self._cancel_observed():
                return
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
        return True

    def stop(self) -> None:
        self._done.set()


def _now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


__all__ = ["ReceiptSpool", "Worker", "fsync_json"]
