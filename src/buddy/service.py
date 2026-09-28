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

import re

from . import runtime, schemas
from . import workflow as workflow_module
from .adapters import capability_report
from .contracts import CONTRACT_VERSION
from .db import SCHEMA_VERSION, utc_now
from .errors import BoardError
from .evaluation import EvaluationStore
from .store import BoardStore

PROTOCOL_VERSION = 2
WAIT_CAPACITY_DEFAULT = 32

#: Operations each resource exposes. Used for health/capability reporting and to
#: reject a typo loudly instead of silently ignoring it.
CONTROL_OPERATIONS = (
    "ping",
    "health",
    "capabilities",
    "service_control",
    "console",
    "console_snapshot",
    "runtime_info",
    "backup",
    "storage_plan",
    "storage_apply",
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
    "evaluation_write_begin",
    "evaluation_write_renew",
    "user_policy_publish",
    "assessment_publish",
    "evaluation_write_abort",
    "evaluation_reader_begin",
    "evaluation_reader_release",
    "evaluation_evidence_record",
    "evaluation_prepare",
    "evaluation_history",
    "selection_request",
    "selection_get",
    "selection_list",
    "model_catalog_refresh",
    "model_profiles",
    "workflow_submit",
    "workflow_get",
    "workflow_decide",
    "workflow_continue",
    "workflow_takeover",
    "workflow_cancel",
    "workflow_acknowledge",
    "workflow_scope_amend",
    "workflow_workspace_resolve",
    "workflow_integration_record",
    "workspace_cleanup_plan",
    "workspace_cleanup_apply",
    "workflow_suggest",
    "objective_list",
    "objective_timeline",
    "objective_stop",
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
            scope = self._authenticate(params)
            if scope.get("kind") == workflow_module.AGENT_KIND:
                # A request that presents an attempt-scoped credential is confined to
                # the permitted operations and to its own run, before any handler runs.
                workflow_module.WorkflowCoordinator.authorize_agent_operation(
                    self.store.workflow, operation.replace(".", "_"), params, scope
                )
            workflow_module.set_request_scope(scope)
            try:
                if (self.store.directory / "upgrade.json").exists():
                    permitted = operation in {"ping", "health", "runtime.info", "capabilities", "backup"}
                    permitted = permitted or (operation == "console" and params.get("action") == "status")
                    permitted = permitted or (operation == "service.control" and params.get("action") in {"restart", "status"})
                    if not permitted:
                        raise BoardError("UPGRADE_IN_PROGRESS", "The service is fenced for an idle upgrade; retry after verification")
                result = handler(params)
            finally:
                workflow_module.clear_request_scope()
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

    def _authenticate(self, params: dict) -> dict:
        """Every operation carries the private service token, verified here.

        A caller may additionally present an attempt-scoped credential or an
        authenticated console session. An invalid scoped credential is never quietly
        replaced by the service token: presenting one is a claim of scoped authority,
        and a false claim is rejected.
        """
        import hmac as _hmac

        provided = params.pop("token", None)
        if not isinstance(provided, str) or not _hmac.compare_digest(provided, self.token or ""):
            raise BoardError("UNAUTHORIZED", "Invalid service token")
        credential = params.pop("credential", None)
        authority = params.pop(schemas.CONSOLE_AUTHORITY_FIELD, None)
        scope: dict = {"kind": workflow_module.SERVICE_KIND}
        if authority is not None:
            session_id = authority if isinstance(authority, str) else (
                authority.get("sessionId") if isinstance(authority, dict) else None
            )
            public_id = self.console_public_identity(session_id) if isinstance(session_id, str) else None
            if public_id is None:
                raise BoardError(
                    "UNAUTHORIZED",
                    "The console authority is not a registered authenticated console session; a caller-controlled "
                    "field can never substitute for Host control",
                )
            scope = {"kind": workflow_module.CONSOLE_KIND, "sessionId": public_id}
        if credential is not None:
            if scope["kind"] == workflow_module.CONSOLE_KIND:
                raise BoardError("UNAUTHORIZED", "An attempt-scoped credential cannot claim console authority")
            scope = self.store.workflow.resolve_credential(credential)
        return scope


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
        console_factory: Callable[[dict], dict] | None = None,
        runtime_directory: Path | None = None,
        on_accepted: Callable[[str], None] | None = None,
        evaluation: EvaluationStore | None = None,
    ):
        super().__init__(store)
        self.token = token
        self.control = control
        self.on_stop = on_stop
        self.on_restart = on_restart
        self.console_factory = console_factory
        self.runtime_directory = runtime_directory
        self.on_accepted = on_accepted
        # All evaluation and decision state is durable; a lazily composed store keeps
        # every existing in-process harness working without a second authority.
        self.evaluation = evaluation or store.evaluation
        self.decisions = store.decisions
        # Console-user authority: only a session the console itself registered can
        # present it, so a caller-controlled JSON field can never bypass Host control.
        self._console_sessions: dict[str, str] = {}
        self._console_lock = threading.Lock()
        if evaluation is not None:
            # One coordinator, one evaluation store: the store hooks (claim, result,
            # cancel, recovery) and this resource must fence the same gate.
            store.decisions.evaluation = evaluation
        self.started_at = utc_now()

    # -- service ------------------------------------------------------------
    def ping(self, request_json: str) -> str:
        """Authenticated process liveness; deliberately no storage/runtime scan."""
        def handler(params: dict) -> dict:
            schemas.reject_unknown(params, set(), "ping")
            return {
                "status": "ok",
                "protocol": PROTOCOL_VERSION,
                "contractVersion": self.control.get("contract_version", CONTRACT_VERSION),
                "schemaVersion": SCHEMA_VERSION,
                "serviceId": self.control.get("service_id"),
                "pid": os.getpid(),
                "persistenceError": self.store.persistence_error,
            }

        return self._guard("ping", request_json, handler)

    def health(self, request_json: str) -> str:
        def handler(params: dict) -> dict:
            schemas.reject_unknown(params, set(), "health")
            identity = runtime.resolve_runtime()
            pool = self.control.get("worker_pool") or {}
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
                # One machine-wide concurrent-attempt ceiling shared by routing and
                # execution; ``capacity.models`` reports the per-family limits and
                # occupancy so a caller never has to infer which limit refused a
                # claim.
                "maxConcurrent": self.store.max_concurrent,
                "capacity": self.store.capacity_report(),
                "routingHealth": self.decisions.health_summary(),
                "managedWorkerIds": list(pool.get("workerIds") or []),
                "unstartedWorkerIds": list(pool.get("unstartedWorkerIds") or []),
                "stoppedWorkerIds": list(pool.get("stoppedWorkerIds") or []),
                "surplusWorkerIds": list(pool.get("surplusWorkerIds") or []),
                "surplusDraining": list(pool.get("surplusDraining") or []),
                "surplusRetained": [dict(row) for row in pool.get("surplusRetained") or []],
                "waitCapacity": self.control.get("wait_capacity"),
                "rpcProfile": self.control.get("rpc_profile"),
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
                    "steer": "an active attempt cannot be re-scoped; submit authorized continuation input after its turn ends",
                    "resume": "governed continuation creates a new owned attempt; native session support is declared per adapter",
                    "nativeAppWakeup": "not provided: notifications are post-commit hints for clients",
                    "postgres": "not provided: SQLite is the deliberate local database",
                    "remoteTenancy": "not provided: same-user local service only",
                    "selectionFallback": (
                        "not provided: with no configured decision profile or no legal candidate the decision is "
                        "needs-host; the service never guesses a profile and never selects a selector"
                    ),
                    "maintenanceScope": (
                        "harness-owned: the blackboard executes no maintenance model call and has no "
                        "autoMaintain setting. An external Harness collects bounded facts with "
                        "evaluation_prepare (no model call, no lease), synthesizes card text under the buddy "
                        "skill, and publishes a short card-only patch with evaluation_write_begin(kind="
                        "\"maintenance\") plus assessment_publish; profiles, preferences, configuration, "
                        "authority and code-owned counters are never changed by a model"
                    ),
                    "decisionModelIdentity": (
                        "the Router records its requested and resolved configuration; the served "
                        "identity is unknown and is never invented"
                    ),
                    "osIsolation": (
                        "not claimed: the console token and session separate browser origins, not a same-user "
                        "process with full shell and credential access"
                    ),
                    "agentScope": (
                        "enforced in the supported API: an attempt-scoped credential may observe its own run and "
                        "record suggestions only; it cannot submit tasks, create helpers, approve, take over, "
                        "cancel, write evaluations or address another run. This is capability enforcement, not OS "
                        "isolation against a same-user process that steals the credential file"
                    ),
                    "hostAuthority": (
                        "a Host decision requires the owner control capability (or an authenticated console "
                        "session); a hostId label and a shared cache of the newest generation are never authority"
                    ),
                },
            }

        return self._guard("capabilities", request_json, handler)

    def storage_plan(self, request_json: str) -> str:
        from . import storage
        return self._guard("storage.plan", request_json, lambda params: storage.plan(self.store, params))

    def storage_apply(self, request_json: str) -> str:
        from . import storage
        return self._guard("storage.apply", request_json, lambda params: storage.apply(self.store, params))

    def backup(self, request_json: str) -> str:
        def handler(params: dict) -> dict:
            from . import backup
            schemas.reject_unknown(params, {"upgradeToken"}, "backup")
            marker = self.store.directory / "upgrade.json"
            if marker.exists():
                import hashlib
                import hmac
                journal = json.loads(marker.read_text())
                supplied = params.get("upgradeToken")
                if not isinstance(supplied, str) or not hmac.compare_digest(hashlib.sha256(supplied.encode()).hexdigest(), journal.get("backupTokenHash", "")):
                    raise BoardError("UPGRADE_IN_PROGRESS", "The rolling backup is reserved for this upgrade")
            elif "upgradeToken" in params:
                raise BoardError("INVALID_ARGUMENT", "An upgrade token is only valid during its fenced upgrade")
            return backup.create(self.store, runtime_identity=runtime.resolve_runtime(), plugin_commit=backup.source_commit())
        return self._guard("backup", request_json, handler)

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

    # -- console and evaluation ---------------------------------------------
    def console(self, request_json: str) -> str:
        def handler(params: dict) -> dict:
            schemas.reject_unknown(params, {"action", "expectedConsoleId"}, "console")
            action = schemas.optional_string(params, "action") or "open"
            if action not in ("open", "close", "status"):
                raise BoardError("INVALID_ARGUMENT", "action must be 'open', 'close' or 'status'")
            if "expectedConsoleId" in params:
                if action != "close":
                    raise BoardError("INVALID_ARGUMENT", "expectedConsoleId is only valid for close")
                params["expectedConsoleId"] = schemas.required_string(params, "expectedConsoleId", max_length=24, pattern=re.compile(r"^[0-9a-f]{24}$"))
            if self.console_factory is None:
                raise BoardError("UNSUPPORTED", "This service build has no console")
            return self.console_factory({**params, "action": action})

        return self._guard("console", request_json, handler)

    def console_snapshot(self, request_json: str) -> str:
        def handler(params: dict) -> dict:
            snapshot = self.evaluation.snapshot(params)
            # The JSON route is a read: it never admits a selection reader and never
            # calls a model. The browser layer injects its own CSRF token into this
            # object; a C-Two/CLI caller gets an empty token because it has no session.
            from .console import assets_ready

            snapshot["capabilities"]["consoleAssets"] = assets_ready()
            snapshot["routingHealth"] = self.decisions.health_summary()
            return snapshot

        return self._guard("console.snapshot", request_json, handler)

    def evaluation_write_begin(self, request_json: str) -> str:
        return self._guard("evaluation.write.begin", request_json, self.evaluation.write_begin)

    def evaluation_write_renew(self, request_json: str) -> str:
        return self._guard("evaluation.write.renew", request_json, self.evaluation.write_renew)

    def user_policy_publish(self, request_json: str) -> str:
        return self._guard("user.policy.publish", request_json, self.evaluation.user_policy_publish)

    def assessment_publish(self, request_json: str) -> str:
        return self._guard("assessment.publish", request_json, self.evaluation.assessment_publish)

    def evaluation_write_abort(self, request_json: str) -> str:
        return self._guard("evaluation.write.abort", request_json, self.evaluation.write_abort)

    def evaluation_reader_begin(self, request_json: str) -> str:
        return self._guard("evaluation.reader.begin", request_json, self.evaluation.reader_begin)

    def evaluation_reader_release(self, request_json: str) -> str:
        return self._guard("evaluation.reader.release", request_json, self.evaluation.reader_release)

    def evaluation_evidence_record(self, request_json: str) -> str:
        return self._guard("evaluation.evidence.record", request_json, self.evaluation.evidence_record)

    def evaluation_prepare(self, request_json: str) -> str:
        # The Harness-owned maintenance read: bounded, deterministic fact collection
        # with no model call, no writer lease and no publication.
        return self._guard("evaluation.prepare", request_json, self.evaluation.prepare)

    def evaluation_history(self, request_json: str) -> str:
        return self._guard("evaluation.history", request_json, self.evaluation.history)

    # -- decisions ----------------------------------------------------------
    def selection_request(self, request_json: str) -> str:
        return self._guard("selection.request", request_json, self.decisions.request_select)

    def selection_get(self, request_json: str) -> str:
        return self._guard("selection.get", request_json, self.decisions.get)

    def selection_list(self, request_json: str) -> str:
        return self._guard("selection.list", request_json, self.decisions.list_decisions)

    def model_catalog_refresh(self, request_json: str) -> str:
        def handler(params: dict) -> dict:
            from . import catalog, catalog_store

            schemas.reject_unknown(params, {"requestId"}, "model.catalog.refresh")
            request_id = schemas.optional_string(params, "requestId", max_length=128)
            observation = catalog_store.begin(self.evaluation, request_id)
            if observation["response"] is not None:
                return {**observation["response"], "duplicate": True}
            discovered = catalog.discover()
            recorded = self.evaluation.record_catalog(discovered, observation["observationId"])
            return {**recorded, "requestId": request_id}

        return self._guard("model.catalog.refresh", request_json, handler)

    def model_profiles(self, request_json: str) -> str:
        from . import catalog_store

        return self._guard("model.profiles", request_json, lambda params: catalog_store.profiles(self.evaluation, params))

    # -- governed workflow --------------------------------------------------
    def workflow_submit(self, request_json: str) -> str:
        return self._guard("workflow.submit", request_json, self.store.workflow.submit)

    def workflow_get(self, request_json: str) -> str:
        return self._guard("workflow.get", request_json, self.store.workflow.get)

    def workflow_decide(self, request_json: str) -> str:
        def handler(params: dict) -> dict:
            return self.store.workflow.decide(
                params, console_authority=workflow_module.console_authority_from_scope()
            )

        return self._guard("workflow.decide", request_json, handler)

    def workflow_continue(self, request_json: str) -> str:
        def handler(params: dict) -> dict:
            return self.store.workflow.continue_run(
                params, console_authority=workflow_module.console_authority_from_scope()
            )

        return self._guard("workflow.continue", request_json, handler)

    def workflow_takeover(self, request_json: str) -> str:
        def handler(params: dict) -> dict:
            return self.store.workflow.takeover(
                params, console_authority=workflow_module.console_authority_from_scope()
            )

        return self._guard("workflow.takeover", request_json, handler)

    def workflow_cancel(self, request_json: str) -> str:
        def handler(params: dict) -> dict:
            return self.store.workflow.cancel(
                params, console_authority=workflow_module.console_authority_from_scope()
            )

        return self._guard("workflow.cancel", request_json, handler)

    def workflow_acknowledge(self, request_json: str) -> str:
        def handler(params: dict) -> dict:
            result = self.store.workflow.acknowledge(
                params, console_authority=workflow_module.console_authority_from_scope()
            )
            if result.get("state") == "accepted" and self.on_accepted is not None:
                self.on_accepted(result["runId"])
            return result

        return self._guard("workflow.acknowledge", request_json, handler)

    def workflow_scope_amend(self, request_json: str) -> str:
        def handler(params: dict) -> dict:
            return self.store.workflow.scope_amend(
                params, console_authority=workflow_module.console_authority_from_scope()
            )

        return self._guard("workflow.scope_amend", request_json, handler)

    def workflow_workspace_resolve(self, request_json: str) -> str:
        def handler(params: dict) -> dict:
            return self.store.workflow.workspace_resolve(
                params, console_authority=workflow_module.console_authority_from_scope()
            )

        return self._guard("workflow.workspace_resolve", request_json, handler)

    def workflow_integration_record(self, request_json: str) -> str:
        def handler(params: dict) -> dict:
            return self.store.workflow.integration_record(
                params, console_authority=workflow_module.console_authority_from_scope()
            )

        return self._guard("workflow.integration_record", request_json, handler)

    def workspace_cleanup_plan(self, request_json: str) -> str:
        def handler(params: dict) -> dict:
            return self.store.workflow.cleanup_plan(
                params, console_authority=workflow_module.console_authority_from_scope()
            )

        return self._guard("workspace.cleanup_plan", request_json, handler)

    def workspace_cleanup_apply(self, request_json: str) -> str:
        def handler(params: dict) -> dict:
            return self.store.workflow.cleanup_apply(
                params, console_authority=workflow_module.console_authority_from_scope()
            )

        return self._guard("workspace.cleanup_apply", request_json, handler)

    def workflow_suggest(self, request_json: str) -> str:
        def handler(params: dict) -> dict:
            return self.store.workflow.suggest(params, scope=workflow_module.current_scope())

        return self._guard("workflow.suggest", request_json, handler)

    # -- work objectives ----------------------------------------------------
    def objective_list(self, request_json: str) -> str:
        # Read-only: no lease, no model call; an attempt-scoped credential is refused
        # by the agent operation allowlist before this handler runs.
        from . import objectives

        return self._guard("objective.list", request_json, lambda params: objectives.objective_list(self.store, params))

    def objective_timeline(self, request_json: str) -> str:
        from . import objectives

        return self._guard(
            "objective.timeline", request_json, lambda params: objectives.objective_timeline(self.store, params)
        )

    def objective_stop(self, request_json: str) -> str:
        return self._guard(
            "objective.stop", request_json,
            lambda params: self.store.workflow.stop_objective(
                params, console_authority=workflow_module.console_authority_from_scope()
            ),
        )

    # -- console-user authority --------------------------------------------
    def register_console_authority(self, session_id: str, public_id: str | None = None) -> None:
        import hashlib
        if not isinstance(session_id, str) or not session_id:
            raise BoardError("INVALID_ARGUMENT", "A console capability is required")
        if public_id is not None and (not isinstance(public_id, str) or re.fullmatch(r"[0-9a-f]{24}", public_id) is None):
            raise BoardError("INVALID_ARGUMENT", "A public console identity must be 24 hex characters")
        # The bearer stays only in this in-memory authorization map. Durable
        # events record the public identity, never the session credential.
        with self._console_lock:
            self._console_sessions[session_id] = public_id or hashlib.sha256(session_id.encode()).hexdigest()[:24]

    def revoke_console_authority(self, session_id: str | None = None) -> None:
        with self._console_lock:
            if session_id is None:
                self._console_sessions.clear()
            else:
                self._console_sessions.pop(session_id, None)

    def console_public_identity(self, capability: str) -> str | None:
        import hmac as _hmac
        with self._console_lock:
            sessions = tuple(self._console_sessions.items())
        return next((public for secret, public in sessions
                     if _hmac.compare_digest(capability.encode(), secret.encode())), None)

    def console_session_valid(self, session_id: str) -> bool:
        return self.console_public_identity(session_id) is not None

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
        def handler(params: dict) -> dict:
            # A continuation's effective workspace is prepared here, outside the claim
            # transaction, so the canonical turn input already carries the resolved
            # manifest and its hash is exactly what the adapter writes to disk.
            self.store.workflow.prepare_continuation_workspace(params)
            return self.store.worker_claim(params)

        return self._guard("worker.claim", request_json, handler)

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


def call_operation(service: Any, operation: str, params: dict) -> dict:
    """Call one named operation in-process and raise :class:`BoardError` on failure.

    The console HTTP surface and the in-process test harness both call this, so a
    browser command reaches exactly the same validated operation, token check and
    transaction as the C-Two/CLI route. There is no second SQL path and no
    ``dispatch(method, JSON)`` facade: the name is resolved against the resource.
    """
    if not isinstance(operation, str) or operation.startswith("_") or not hasattr(service, operation):
        raise BoardError("METHOD_NOT_FOUND", f"Unknown operation {operation!r}")
    # The same private token the transport carries is presented here, so an
    # in-process call exercises exactly the same authentication path.
    payload = {**params, "token": getattr(service, "token", "")}
    raw = getattr(service, operation)(json.dumps(payload, ensure_ascii=False))
    value = json.loads(raw)
    if isinstance(value, dict) and "error" in value:
        error = value["error"]
        details = error.get("details") if isinstance(error.get("details"), dict) else {}
        raise BoardError(error.get("code", "SERVICE_ERROR"), error.get("message", "operation failed"), **details)
    return value


def dispatch_local(service: Any, operation: str, params: dict) -> dict:
    """Invoke a named current operation for local callers and tests."""
    return call_operation(service, operation, params)
