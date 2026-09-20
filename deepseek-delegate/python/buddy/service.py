"""The C-Two resource implementations behind the named board operations.

``BoardService`` owns the control resource (mutations, reads, lifecycle) and
``WaitService`` owns the dedicated wait resource. Both validate their request
schema, translate :class:`BoardError` into structured JSON errors, and never expose
a generic ``dispatch(method, JSON)`` entry point.
"""
from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Any, Callable

from . import runtime, schemas
from .adapters import capability_report
from .db import SCHEMA_VERSION, utc_now
from .errors import BoardError
from .store import BoardStore

PROTOCOL_VERSION = 2
WAIT_CAPACITY_DEFAULT = 32

#: Operations each resource exposes. Used for health/capability reporting and to
#: reject a typo loudly instead of silently ignoring it.
CONTROL_OPERATIONS = (
    "health",
    "capabilities",
    "service_control",
    "dashboard",
    "legacy_import",
    "runtime_info",
    "task_submit",
    "task_get",
    "task_list",
    "task_result",
    "task_cancel",
    "task_retry",
    "task_acknowledge",
    "task_wait",
    "worker_register",
    "worker_claim",
    "worker_reconcile",
    "worker_renew",
    "worker_progress",
    "worker_result",
    "worker_release",
    "worker_list",
    "message_post",
    "message_update",
    "message_get",
    "message_list",
    "inquiry_observe",
    "artifact_list",
    "events_read",
)
WAIT_OPERATIONS = ("events_wait", "task_wait", "message_wait", "wait_capacity")


class WaitAdmission:
    """Bounded admission for waits, separate from the control resource.

    A wait never holds a database transaction or an exclusive resource lock, and
    when every admitted slot is busy the caller gets a resumable overload error
    carrying the cursor, instead of consuming control capacity.
    """

    def __init__(self, capacity: int = WAIT_CAPACITY_DEFAULT):
        self.capacity = max(1, int(capacity))
        self._semaphore = threading.BoundedSemaphore(self.capacity)
        self.admitted = 0
        self.rejected = 0
        self._lock = threading.Lock()

    def acquire(self) -> bool:
        if not self._semaphore.acquire(blocking=False):
            with self._lock:
                self.rejected += 1
            return False
        with self._lock:
            self.admitted += 1
        return True

    def release(self) -> None:
        with self._lock:
            self.admitted = max(0, self.admitted - 1)
        self._semaphore.release()

    def stats(self) -> dict:
        with self._lock:
            return {
                "capacity": self.capacity,
                "admitted": self.admitted,
                "rejected": self.rejected,
                "available": max(0, self.capacity - self.admitted),
            }


class _BaseResource:
    def __init__(self, store: BoardStore, token: str = ""):
        self.store = store
        self.token = token

    def _guard(self, operation: str, request_json: Any, handler: Callable[[dict], dict]) -> str:
        try:
            params = schemas.decode_request(request_json, f"{operation} request")
            self._authenticate(params)
            result = handler(params)
            return schemas.encode(result)
        except BoardError as error:
            return schemas.encode({"error": error.payload()})
        except RecursionError:
            return schemas.encode({"error": {"code": "INVALID_ARGUMENT", "message": "Request is too deeply nested"}})
        except Exception as error:  # pragma: no cover - defensive boundary
            detail = ""
            if os.environ.get("BUDDY_DEBUG"):
                # Never leak internals by default; an operator can opt in explicitly.
                detail = f": {type(error).__name__}: {error}"
            return schemas.encode(
                {"error": {"code": "INTERNAL_ERROR", "message": f"{operation} failed inside the service{detail}"}}
            )

    def _authenticate(self, params: dict) -> None:
        """Every operation carries the private service token, verified here."""
        import hmac as _hmac

        provided = params.pop("token", None)
        if not isinstance(provided, str) or not _hmac.compare_digest(provided, self.token or ""):
            raise BoardError("UNAUTHORIZED", "Invalid service token")


class BoardService(_BaseResource):
    """Control resource: authoritative mutations, reads and lifecycle."""

    def __init__(
        self,
        store: BoardStore,
        *,
        token: str,
        control: dict,
        on_stop: Callable[[dict], dict],
        on_restart: Callable[[dict], dict],
        dashboard_factory: Callable[[], dict] | None = None,
        legacy_importer: Callable[[dict], dict] | None = None,
        runtime_directory: Path | None = None,
    ):
        super().__init__(store)
        self.token = token
        self.control = control
        self.on_stop = on_stop
        self.on_restart = on_restart
        self.dashboard_factory = dashboard_factory
        self.legacy_importer = legacy_importer
        self.runtime_directory = runtime_directory
        self.started_at = utc_now()

    # -- service ------------------------------------------------------------
    def health(self, request_json: str) -> str:
        def handler(params: dict) -> dict:
            schemas.reject_unknown(params, set(), "health")
            identity = runtime.resolve_runtime()
            return {
                "status": "ok",
                "protocol": PROTOCOL_VERSION,
                "contractVersion": self.control.get("contract_version"),
                "schemaVersion": SCHEMA_VERSION,
                "serviceId": self.control.get("service_id"),
                "pid": os.getpid(),
                "startedAt": self.started_at,
                "stateDir": str(self.store.directory),
                "runtimeIdentity": identity.get("identity"),
                "runtimeStable": bool(identity.get("stable")),
                "runtimeContentId": identity.get("contentId"),
                "maxConcurrent": self.store.max_concurrent,
                "waitCapacity": self.control.get("wait_capacity"),
                "persistenceError": self.store.persistence_error,
                "integrity": self.store.integrity(),
                "activeWork": len(self.store.active_work()["attempts"]),
            }

        return self._guard("health", request_json, handler)

    def capabilities(self, request_json: str) -> str:
        def handler(params: dict) -> dict:
            schemas.reject_unknown(params, {"includeUnavailable"}, "capabilities")
            from .adapters import local_capabilities

            return {
                "adapters": capability_report(),
                "localCapabilities": local_capabilities(),
                "operations": {"control": list(CONTROL_OPERATIONS), "wait": list(WAIT_OPERATIONS)},
                "waitAdmission": self.control.get("wait_admission").stats()
                if self.control.get("wait_admission")
                else None,
                "limitations": {
                    "steer": "not implemented: an active dsh attempt cannot be re-scoped",
                    "resume": "not implemented: retry is an explicit new attempt, never an automatic resume",
                    "nativeAppWakeup": "not provided: notifications are post-commit hints for clients",
                    "postgres": "not provided: SQLite is the deliberate local database",
                    "remoteTenancy": "not provided: same-user local service only",
                },
            }

        return self._guard("capabilities", request_json, handler)

    def runtime_info(self, request_json: str) -> str:
        def handler(params: dict) -> dict:
            schemas.reject_unknown(params, {"destination"}, "runtime.info")
            destination = params.get("destination")
            return {
                "identity": runtime.resolve_runtime(),
                "runtime": runtime.describe(destination=Path(destination) if destination else None),
                "leaks": runtime.source_leaks(),
            }

        return self._guard("runtime.info", request_json, handler)

    def service_control(self, request_json: str) -> str:
        def handler(params: dict) -> dict:
            schemas.reject_unknown(params, {"action", "drainSeconds", "reason"}, "service.control")
            action = schemas.required_string(params, "action", max_length=32)
            if action not in ("stop", "restart", "status"):
                raise BoardError("INVALID_ARGUMENT", "action must be 'stop', 'restart' or 'status'")
            if action == "status":
                return {"action": "status", "control": dict(self.control)}
            drain = schemas.optional_int(params, "drainSeconds", 10, 0, 120)
            reason = schemas.optional_string(params, "reason") or f"service {action} requested"
            if action == "stop":
                return self.on_stop({"drainSeconds": drain, "reason": reason})
            return self.on_restart({"drainSeconds": drain, "reason": reason})

        return self._guard("service.control", request_json, handler)

    def dashboard(self, request_json: str) -> str:
        def handler(params: dict) -> dict:
            schemas.reject_unknown(params, {"action"}, "dashboard")
            action = schemas.optional_string(params, "action") or "open"
            if action not in ("open", "close", "status"):
                raise BoardError("INVALID_ARGUMENT", "action must be 'open', 'close' or 'status'")
            if self.dashboard_factory is None:
                raise BoardError("UNSUPPORTED", "This service build has no dashboard")
            return self.dashboard_factory(action)

        return self._guard("dashboard", request_json, handler)

    def legacy_import(self, request_json: str) -> str:
        def handler(params: dict) -> dict:
            if self.legacy_importer is None:
                raise BoardError("UNSUPPORTED", "This service build has no legacy importer")
            return self.legacy_importer(params)

        return self._guard("legacy.import", request_json, handler)

    # -- tasks --------------------------------------------------------------
    def task_submit(self, request_json: str) -> str:
        return self._guard("task.submit", request_json, self.store.task_submit)

    def task_get(self, request_json: str) -> str:
        return self._guard("task.get", request_json, self.store.task_get)

    def task_list(self, request_json: str) -> str:
        return self._guard("task.list", request_json, self.store.task_list)

    def task_result(self, request_json: str) -> str:
        return self._guard("task.result", request_json, self.store.task_result)

    def task_cancel(self, request_json: str) -> str:
        return self._guard("task.cancel", request_json, self.store.task_cancel)

    def task_retry(self, request_json: str) -> str:
        return self._guard("task.retry", request_json, self.store.task_retry)

    def task_acknowledge(self, request_json: str) -> str:
        return self._guard("task.acknowledge", request_json, self.store.task_acknowledge)

    def task_wait(self, request_json: str) -> str:
        return self._guard("task.wait", request_json, self.store_task_wait)

    def store_task_wait(self, params: dict) -> dict:
        schemas.reject_unknown(params, {"runId", "taskId", "afterRevision", "timeoutMs"}, "task.wait")
        timeout_ms = schemas.optional_int(params, "timeoutMs", 30000, 0, 30000)
        after_revision = params.get("afterRevision")
        if after_revision is not None and (
            isinstance(after_revision, bool) or not isinstance(after_revision, int) or after_revision < 0
        ):
            raise BoardError("INVALID_ARGUMENT", "afterRevision must be a nonnegative integer")
        deadline = time.monotonic() + timeout_ms / 1000.0
        while True:
            view = self.store.task_get({"runId": params.get("runId"), "taskId": params.get("taskId")})["task"]
            # Re-check after subscribing so a change between read and subscribe is
            # never lost; the recheck happens inside events_wait.
            if view["resultAvailable"] or timeout_ms == 0 or (
                after_revision is not None and view["revision"] > after_revision
            ):
                return view
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return view
            self.store.events_wait(
                {
                    "after": self.store.head(),
                    "timeoutMs": int(min(1000, max(1, remaining * 1000))),
                    "taskId": view["taskId"],
                    "limit": 1,
                }
            )
            if time.monotonic() >= deadline:
                return self.store.task_get({"runId": view["taskId"]})["task"]

    # -- workers ------------------------------------------------------------
    def worker_register(self, request_json: str) -> str:
        return self._guard("worker.register", request_json, self.store.worker_register)

    def worker_claim(self, request_json: str) -> str:
        return self._guard("worker.claim", request_json, self.store.worker_claim)

    def worker_reconcile(self, request_json: str) -> str:
        return self._guard("worker.reconcile", request_json, self.store.worker_reconcile)

    def worker_renew(self, request_json: str) -> str:
        return self._guard("worker.renew", request_json, self.store.worker_renew)

    def worker_progress(self, request_json: str) -> str:
        return self._guard("worker.progress", request_json, self.store.worker_progress)

    def worker_result(self, request_json: str) -> str:
        return self._guard("worker.result", request_json, self.store.worker_result)

    def worker_release(self, request_json: str) -> str:
        return self._guard("worker.release", request_json, self.store.worker_release)

    def worker_list(self, request_json: str) -> str:
        return self._guard("worker.list", request_json, self.store.worker_list)

    # -- messages -----------------------------------------------------------
    def message_post(self, request_json: str) -> str:
        return self._guard("message.post", request_json, self.store.message_post)

    def message_update(self, request_json: str) -> str:
        return self._guard("message.update", request_json, self.store.message_update)

    def message_get(self, request_json: str) -> str:
        return self._guard("message.get", request_json, self.store.message_get)

    def message_list(self, request_json: str) -> str:
        return self._guard("message.list", request_json, self.store.message_list)

    def inquiry_observe(self, request_json: str) -> str:
        def handler(params: dict) -> dict:
            from .inquiry import observe

            return observe(self.store, params)

        return self._guard("inquiry.observe", request_json, handler)

    def artifact_list(self, request_json: str) -> str:
        return self._guard("artifact.list", request_json, self.store.artifact_list)

    # -- events -------------------------------------------------------------
    def events_read(self, request_json: str) -> str:
        return self._guard("events.read", request_json, self.store.events_read)

    def shutdown(self) -> None:  # pragma: no cover - exercised through the daemon
        """C-Two on_shutdown hook: nothing authoritative is dropped here."""
        self.control["stopping"] = True


class WaitService(_BaseResource):
    """Dedicated bounded wait resource."""

    def __init__(self, store: BoardStore, admission: WaitAdmission, token: str = ""):
        super().__init__(store, token)
        self.admission = admission

    def _bounded(self, operation: str, request_json: str, handler: Callable[[dict], dict]) -> str:
        if not self.admission.acquire():
            try:
                params = schemas.decode_request(request_json, f"{operation} request")
                self._authenticate(params)
            except BoardError:
                params = {}
            cursor = params.get("after")
            if cursor is None:
                try:
                    cursor = self.store.head()
                except Exception:  # pragma: no cover - defensive
                    cursor = 0
            return schemas.encode(
                {
                    "error": {
                        "code": "WAIT_OVERLOAD",
                        "message": (
                            "Every admitted wait slot is busy; this is not an execution failure and no task state "
                            "changed. Retry with the same cursor — the event stream is the replayable outbox."
                        ),
                        "details": {
                            "cursor": cursor,
                            "retryAfterMs": 250,
                            "capacity": self.admission.capacity,
                        },
                    }
                }
            )
        try:
            return self._guard(operation, request_json, handler)
        finally:
            self.admission.release()

    def events_wait(self, request_json: str) -> str:
        return self._bounded("events.wait", request_json, self.store.events_wait)

    def task_wait(self, request_json: str) -> str:
        def handler(params: dict) -> dict:
            service = BoardService(self.store, token=self.token, control={}, on_stop=lambda _p: {}, on_restart=lambda _p: {})
            return service.store_task_wait(params)

        return self._bounded("task.wait", request_json, handler)

    def message_wait(self, request_json: str) -> str:
        return self._bounded("message.wait", request_json, self.store.message_wait)

    def wait_capacity(self, request_json: str) -> str:
        return self._guard("wait.capacity", request_json, lambda params: self.admission.stats())


def dispatch_local(service: Any, operation: str, params: dict) -> dict:
    """Call one named operation in-process (used by tests and the local worker).

    This is not an RPC surface: the operation name is resolved against the CRM
    class, so an unknown name fails exactly like it would over C-Two.
    """
    if not hasattr(service, operation):
        raise BoardError("METHOD_NOT_FOUND", f"Unknown operation {operation!r}")
    # The same private token the transport carries is presented here, so an
    # in-process call exercises exactly the same authentication path.
    payload = {**params, "token": getattr(service, "token", "")}
    raw = getattr(service, operation)(json.dumps(payload, ensure_ascii=False))
    value = json.loads(raw)
    if isinstance(value, dict) and "error" in value:
        error = value["error"]
        raise BoardError(error.get("code", "SERVICE_ERROR"), error.get("message", "operation failed"))
    return value
