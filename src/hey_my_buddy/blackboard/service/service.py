"""The C-Two resource implementations behind the named board operations.

``BoardService`` owns the control resource (mutations, reads, lifecycle) and
``WaitService`` owns the dedicated wait resource. Both validate their request
schema, translate :class:`BoardError` into structured JSON errors, and never expose
a generic ``dispatch(method, JSON)`` entry point.
"""
from __future__ import annotations

import json
import logging
from contextlib import nullcontext
import os
import threading
from pathlib import Path
from typing import Any, Callable

import re

from ...install import runtime
from ...protocol import schemas
from ..tasks import macro_tasks, micro_task, workflow as workflow_module
from ...private_dirs import session_record_path
from ...buddy.harnesses.registry import capability_report
from ..store.db import SCHEMA_VERSION, utc_now
from ...errors import BoardError
from ..store.store import BoardStore
from .live_registry import LiveRegistry

PROTOCOL_VERSION = 2
WAIT_CAPACITY_DEFAULT = 32

#: Operations each resource exposes. Used for health/capability reporting and to
#: reject a typo loudly instead of silently ignoring it.
CONTROL_OPERATIONS = (
    "ping",
    "health",
    "capabilities",
    "accounts",
    "harness_set",
    "harness_prepare",
    "quota_redetect",
    "service_control",
    "console",
    "console_snapshot",
    "reminder_settings_set",
    "runtime_info",
    "storage_plan",
    "storage_apply",
    "worker_sessions",
    "task_get",
    "task_list",
    "task_result",
    "task_cancel",
    "task_retry",
    "task_acknowledge",
    "task_wait",
    "execution_progress",
    "execution_result",
    "message_get",
    "message_list",
    "inquiry_observe",
    "artifact_list",
    "events_read",
    "user_policy_publish",
    "selection_request",
    "selection_get",
    "selection_list",
    "model_catalog_refresh",
    "buddy_list",
    "workflow_submit",
    "workflow_get",
    "workflow_decide",
    "workflow_continue",
    "workflow_takeover",
    "workflow_cancel",
    "workflow_accept",
    "workflow_conclude",
    "workflow_reclaim",
    "workflow_scope_amend",
    "workflow_workspace_resolve",
    "workflow_suggest",
    "macro_task_list",
    "macro_task_timeline",
    "macro_task_stop",
    "macro_task_deletion_preview",
    "macro_task_delete",
)
WAIT_OPERATIONS = ("events_wait", "task_wait", "wait_capacity")


class WaitAdmission:
    """Bounded admission for waits, separate from the control resource.

    A wait never holds a database transaction or an exclusive resource lock, and
    when every admitted slot is busy the caller gets a resumable overload error
    carrying the cursor, instead of consuming control capacity.
    """

    def __init__(self, capacity: int = WAIT_CAPACITY_DEFAULT):
        self.capacity = max(1, int(capacity))
        self.admitted = 0
        self.rejected = 0
        self._lock = threading.Lock()

    def acquire(self) -> bool:
        with self._lock:
            if self.admitted >= self.capacity:
                self.rejected += 1
                return False
            self.admitted += 1
        return True

    def release(self) -> None:
        with self._lock:
            self.admitted -= 1

    def stats(self) -> dict:
        with self._lock:
            return {"capacity": self.capacity, "admitted": self.admitted,
                    "rejected": self.rejected, "available": self.capacity - self.admitted}


class _BaseResource:
    def __init__(self, store: BoardStore, token: str = ""):
        self.store = store
        self.token = token

    def _guard(self, operation: str, request_json: Any, handler: Callable[[dict], dict]) -> str:
        try:
            params = schemas.decode_request(request_json, f"{operation} request")
            scope = self._authenticate(params)
            path_hint = params.pop('_harnessPathHint', None)
            if scope.get('kind') == workflow_module.SERVICE_KIND and getattr(self, 'harnesses', None) is not None:
                self.harnesses.add_path_hint(path_hint)
            if scope.get("kind") == workflow_module.AGENT_KIND:
                # A request that presents an attempt-scoped credential is confined to
                # the permitted operations and to its own run, before any handler runs.
                workflow_module.WorkflowCoordinator.authorize_agent_operation(
                    self.store.workflow, operation.replace(".", "_"), params, scope
                )
            workflow_module.set_request_scope(scope)
            try:
                if self.store.upgrading():
                    permitted = operation in {"ping", "health", "runtime.info", "capabilities"}
                    if operation == 'capabilities' and params.get('refresh'):
                        permitted = False
                    permitted = permitted or (operation == "console" and params.get("action") == "status")
                    permitted = permitted or (operation == "service.control" and params.get("action") in {"restart", "status"})
                    if not permitted:
                        raise BoardError("UPGRADE_IN_PROGRESS", "Installation verification or recovery is in progress; rerun install to recover an interrupted installation, then retry the same command and identity")
                from ...buddy.harnesses.runtime_selection import bound
                health = getattr(self, 'harnesses', None)
                with bound(health.all()) if health and getattr(self, 'automatic_discovery', False) else nullcontext():
                    result = handler(params)
                if scope['kind'] == workflow_module.SERVICE_KIND:
                    waiting = self._waiting_for_host(params)
                    if waiting:
                        result['waitingForHost'] = waiting
            finally:
                workflow_module.clear_request_scope()
            return schemas.encode(result)
        except BoardError as error:
            return schemas.encode({"error": error.payload()})
        except RecursionError:
            return schemas.encode({"error": {"code": "INVALID_ARGUMENT", "message": "Request is too deeply nested"}})
        except Exception as error:
            logging.exception("%s failed inside the service", operation)
            detail = f"{type(error).__name__}: {str(error)[:4000]}"
            return schemas.encode(
                {"error": {"code": "INTERNAL_ERROR", "message": f"{operation} failed inside the service: {detail}"}}
            )

    def _waiting_for_host(self, params: dict) -> dict:
        """Count the default Host history filters after the operation has finished."""
        host_id = params.get('hostId')
        run_id = params.get('runId') or params.get('taskId')
        request_id = params.get('requestId')
        if not (host_id or run_id or request_id):
            return {}
        with self.store.db.read() as connection:
            if not host_id:
                if run_id:
                    row = connection.execute(
                        'SELECT host_id FROM workflow_runs WHERE run_id=?', (run_id,),
                    ).fetchone()
                else:
                    row = connection.execute(
                        'SELECT r.host_id FROM workflow_runs r JOIN tasks t ON t.task_id=r.run_id '
                        'WHERE t.request_id=?', (request_id,),
                    ).fetchone()
                if row is None:
                    return {}
                host_id = row['host_id']
            if host_id is None:
                return {}
            clauses, values = micro_task.history_where(host_id=host_id)
            counts = ', '.join(
                f'SUM(CASE WHEN {micro_task.FILTER_SQL[name]} THEN 1 ELSE 0 END) AS {name}'
                for name in ('host', 'review')
            )
            row = connection.execute(
                f'SELECT {counts} FROM ({micro_task.TASK_HISTORY_SQL}) AS history '
                f"WHERE {' AND '.join(clauses)}", values,
            ).fetchone()
            return {name: row[name] for name in ('host', 'review') if row[name]}

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
            session_id = authority.get("sessionId") if isinstance(authority, dict) else None
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
        live_registry: LiveRegistry,
        token: str,
        control: dict,
        on_stop: Callable[[dict], dict],
        on_restart: Callable[[dict], dict],
        console_factory: Callable[[dict], dict] | None = None,
        runtime_directory: Path | None = None,
        on_accepted: Callable[[str], None] | None = None,
        automatic_discovery: bool = False,
        admission_lock: threading.RLock | None = None,
    ):
        super().__init__(store)
        self.token = token
        self.live_registry = live_registry
        self.control = control
        self.on_stop = on_stop
        self.on_restart = on_restart
        self.console_factory = console_factory
        self.runtime_directory = runtime_directory
        self.on_accepted = on_accepted
        self.evaluation = store.evaluation
        self.decisions = store.decisions
        from .harness_health import HarnessHealth
        self.automatic_discovery = automatic_discovery
        self._admission_lock = admission_lock or threading.RLock()
        self.harnesses = HarnessHealth(store, catalog_refresh=self._refresh_harness_catalog)
        # ADR-027 rule 3: explicit-buddy validation borrows this service's
        # own bounded native refresh for its single re-read of one harness.
        from ..catalog.catalog import register_catalog_reread
        register_catalog_reread(store.directory, self._reread_catalog_for_validation)
        from ..catalog.accounts import Accounts
        self.account_settings = Accounts(store)
        # Startup/upgrade verification must not mutate health or retained model
        # facts. A missing record projects as unknown until a Host-triggered scan.
        # Console-user authority: only a session the console itself registered can
        # present it, so a caller-controlled JSON field can never bypass Host control.
        self._console_sessions: dict[str, str] = {}
        self._console_lock = threading.Lock()
        self.execution_scheduler = None
        self.started_at = utc_now()

    # -- service ------------------------------------------------------------
    def ping(self, request_json: str) -> str:
        """Authenticated process liveness; deliberately no storage/runtime scan."""
        def handler(params: dict) -> dict:
            schemas.reject_unknown(params, set(), "ping")
            return {
                "status": "ok",
                "protocol": PROTOCOL_VERSION,
                "contractVersion": self.control["contract_version"],
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
                # execution; ``capacity.models`` reports the per-model-service limits and
                # occupancy so a caller never has to infer which limit refused a
                # claim.
                "maxConcurrent": self.store.max_concurrent,
                "capacity": self.store.capacity_report(),
                "routingHealth": self.decisions.health_summary(),
                "waitCapacity": self.control.get("wait_capacity"),
                "rpcProfile": self.control.get("rpc_profile"),
                "persistenceError": self.store.persistence_error,
                "serviceIssues": self.store.service_issues(),
                "integrity": self.store.integrity(),
                "activeWork": len(self.store.active_work()["attempts"]),
            }

        return self._guard("health", request_json, handler)

    def capabilities(self, request_json: str) -> str:
        def handler(params: dict) -> dict:
            schemas.reject_unknown(params, {"includeUnavailable", "refresh", "harness"}, "capabilities")
            from ...buddy.harnesses.registry import local_capabilities
            from .harness_health import HARNESSES
            from ...buddy.harnesses.runtime_selection import bound
            if schemas.optional_bool(params, 'refresh', False):
                for name in [params['harness']] if params.get('harness') else HARNESSES:
                    self.harnesses.refresh(name, force=True)
            elif self.automatic_discovery and not self.store.upgrading():
                self.harnesses.kick()
            records = self.harnesses.all()
            with bound(records) if self.automatic_discovery or params.get('refresh') else nullcontext():
                report, capabilities = capability_report(), local_capabilities()
            for record in records:
                if self.automatic_discovery or record['revision']:
                    report[record['harness']].update(available=record['status'] == 'ready', reason=record.get('reason'), harness=record)

            return {
                "adapters": report,
                "localCapabilities": capabilities,
                "harnesses": records,
                "operations": {"control": list(CONTROL_OPERATIONS), "wait": list(WAIT_OPERATIONS)},
                "waitAdmission": self.control.get("wait_admission").stats()
                if self.control.get("wait_admission")
                else None,
            }

        return self._guard("capabilities", request_json, handler)

    def storage_plan(self, request_json: str) -> str:
        from ..tasks import storage
        return self._guard("storage.plan", request_json, lambda params: storage.plan(self.store, params))

    def storage_apply(self, request_json: str) -> str:
        from ..tasks import storage
        return self._guard("storage.apply", request_json, lambda params: storage.apply(self.store, params))

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
            schemas.reject_unknown(params, {"action", "drainSeconds", "note"}, "service.control")
            action = schemas.required_string(params, "action", max_length=32)
            if action not in ("stop", "restart", "status"):
                raise BoardError("INVALID_ARGUMENT", "action must be 'stop', 'restart' or 'status'")
            if action == "status":
                return {"action": "status", "control": dict(self.control)}
            drain = schemas.optional_int(params, "drainSeconds", 10, 0, 120)
            note = (schemas.bounded_text(params, "note", max_bytes=schemas.MAX_NOTE_BYTES)[0]
                    if params.get("note") is not None else None)
            if action == "stop":
                return self.on_stop({"drainSeconds": drain, "note": note})
            return self.on_restart({"drainSeconds": drain, "note": note})

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
            self._touch_harnesses(params)
            snapshot = self.evaluation.snapshot(params)
            snapshot['harnesses'] = self.harnesses.all()
            # The JSON route is a read: it never admits a selection reader and never
            # calls a model. The browser layer injects its own CSRF token into this
            # object; a C-Two/CLI caller gets an empty token because it has no session.
            from ...console.server import assets_ready

            snapshot["capabilities"]["consoleAssets"] = assets_ready()
            snapshot["routingHealth"] = self.decisions.health_summary()
            snapshot["reminderSettings"] = self.reminder_settings_view()
            return snapshot

        return self._guard("console.snapshot", request_json, handler)

    def reminder_settings_view(self) -> dict:
        with self.store.db.read() as db:
            row = db.execute("SELECT silence_seconds,updated_at,revision FROM reminder_settings WHERE id=1").fetchone()
        return {"silenceSeconds": row["silence_seconds"], "updatedAt": row["updated_at"], "revision": row["revision"]}

    def reminder_settings_set(self, request_json: str) -> str:
        def handler(params: dict) -> dict:
            schemas.reject_unknown(params, {"silenceSeconds"}, "reminder.settings.set")
            scope = workflow_module.current_scope()
            if scope is None or scope.get("kind") != workflow_module.CONSOLE_KIND:
                raise BoardError("UNAUTHORIZED", "Reminder settings require an authenticated console session")
            if "silenceSeconds" not in params:
                raise BoardError("INVALID_ARGUMENT", "silenceSeconds is required")
            seconds = schemas.optional_int(params, "silenceSeconds", 900, 1, 86400)
            with self.store.db.write() as db:
                row = db.execute("SELECT silence_seconds,updated_at,revision FROM reminder_settings WHERE id=1").fetchone()
                if row["silence_seconds"] == seconds:
                    return {"silenceSeconds": row["silence_seconds"], "updatedAt": row["updated_at"], "revision": row["revision"]}
                now = self.store.now()
                revision = row["revision"] + 1
                db.execute("UPDATE reminder_settings SET silence_seconds=?,updated_at=?,revision=? WHERE id=1",
                           (seconds, now, revision))
                self.store._append_event(db, "reminder.settings_changed",
                                         payload={"silenceSeconds": seconds, "revision": revision,
                                                  "actor": {"role": "console", "id": scope["sessionId"]}})
                head = self.store._head_of(db)
            self.store._notify(head)
            return {"silenceSeconds": seconds, "updatedAt": now, "revision": revision}
        return self._guard("reminder.settings.set", request_json, handler)

    def user_policy_publish(self, request_json: str) -> str:
        return self._guard("user.policy.publish", request_json, self.evaluation.user_policy_publish)

    def _refresh_harness_catalog(self, name, record, catalog_scan_claimed=False):
        from ...protocol.reasons import reason
        from ..catalog import catalog, catalog_store
        from ...buddy.harnesses.registry import adapter
        from ...buddy.harnesses.runtime_selection import bound
        if self.harnesses.get(name)['revision'] != record['revision']:
            return
        observation = catalog_store.begin(self.evaluation, harnesses=[record])
        try:
            with bound([record], environment=self.harnesses.environment()):
                native = adapter(name).discover_models()
                payload = catalog.discovery_payload(native, harness=name)
            native_fact = (next((item for item in payload['discoveries'] if item['harness'] == name), None)
                           if 'discoveries' in native else None)
            if native_fact is None:
                raise BoardError('CATALOG_INVALID', 'Native discovery omitted its harness observation', harness=name)
            payload['discoveries'] = [native_fact]
            catalog_store.record(self.evaluation, payload, observation['observationId'], health_generation=(name, record['revision']))
        except BoardError as error:
            if error.code != 'UPGRADE_IN_PROGRESS':
                self.harnesses.invalidate(name, record['revision'],
                                          cause=reason('catalog.discovery-failed', harness=name, errorCode=error.code))

    def _reread_catalog_for_validation(self, name):
        """One bounded re-read through the service's ordinary refresh path."""
        self.harnesses.refresh(name, force=True, catalog_scan_claimed=True)

    def harness_set(self, request_json: str) -> str:
        def handler(params):
            schemas.reject_unknown(params, {'harness', 'path'}, 'harness.set')
            name = schemas.required_string(params, 'harness', max_length=32)
            if 'path' not in params:
                raise BoardError('INVALID_ARGUMENT', 'path is required; use null to restore automatic detection')
            return {'harness': self.harnesses.set_path(name, params['path'])}
        return self._guard('harness.set', request_json, handler)

    def accounts(self, request_json: str) -> str:
        def handler(params):
            schemas.reject_unknown(params, set(), 'accounts')
            return {'accounts': self.account_settings.all()}
        return self._guard('accounts', request_json, handler)


    def harness_prepare(self, request_json: str) -> str:
        def handler(params):
            schemas.reject_unknown(params, {'attemptId', 'generation', 'nonce', 'commandId', 'retry', 'failedOnly', 'healthRevision'}, 'harness.prepare')
            command_id = schemas.required_string(params, 'commandId', max_length=128)
            retry = schemas.optional_bool(params, 'retry', False)
            failed_only = schemas.optional_bool(params, 'failedOnly', False)
            if retry and failed_only:
                raise BoardError('INVALID_ARGUMENT', 'A failure report cannot also request a retry')
            if (retry or failed_only) and type(params.get('healthRevision')) is not int:
                raise BoardError('INVALID_ARGUMENT', 'A pre-start failure must identify the selected health revision')
            with self.store.db.read() as db:
                attempt = self.store._verify_attempt_actor(db, params)
                if attempt['execution_state'] == 'finished' or attempt['cancel_requested_at']:
                    raise BoardError('ATTEMPT_FINISHED', 'This attempt cannot start a harness')
                name = attempt['model_harness'] or attempt['adapter']
                if name not in ('dsh', 'zcode', 'codex', 'claude'):
                    return {'harness': None}
                request = {key: params.get(key) for key in ('attemptId', 'generation', 'retry', 'failedOnly', 'healthRevision')}
                subject = self.store._subject(params['nonce'], attempt['attempt_id'])
                receipt = self.store._receipt(db, command_id, 'harness.prepare', request, subject)
                if receipt is not None:
                    return receipt
                if retry and db.execute("SELECT 1 FROM events WHERE task_id=? AND kind='harness.start_retry' AND json_extract(payload_json,'$.attemptId')=?", (attempt['task_id'], attempt['attempt_id'])).fetchone():
                    raise BoardError('HARNESS_RETRY_EXHAUSTED', 'Only one pre-model retry is permitted per attempt')
            if retry or failed_only:
                self.harnesses.invalidate(name, params.get('healthRevision'))
            record = self.harnesses.get(name) if failed_only else self.harnesses.refresh(name, force=retry, preflight=True)
            with self.store.db.write() as db:
                attempt = self.store._verify_attempt_actor(db, params)
                receipt = self.store._receipt(db, command_id, 'harness.prepare', request, subject)
                if receipt is not None:
                    return receipt
                if attempt['execution_state'] == 'finished' or attempt['cancel_requested_at']:
                    raise BoardError('ATTEMPT_FINISHED', 'This attempt cannot start a harness')
                if retry and db.execute("SELECT 1 FROM events WHERE task_id=? AND kind='harness.start_retry' AND json_extract(payload_json,'$.attemptId')=?", (attempt['task_id'], attempt['attempt_id'])).fetchone():
                    raise BoardError('HARNESS_RETRY_EXHAUSTED', 'Only one pre-model retry is permitted per attempt')
                response = {'harness': record, 'retry': retry}
                unknown = (record['status'] == 'unknown'
                           or (record.get('lastProbe') or {}).get('status') == 'unknown')
                if unknown:
                    db.execute("UPDATE tasks SET queue_reason='harness-probe-unknown' WHERE task_id=?",
                               (attempt['task_id'],))
                else:
                    db.execute("UPDATE tasks SET queue_reason=NULL WHERE task_id=? AND queue_reason='harness-probe-unknown'",
                               (attempt['task_id'],))
                self.store._append_event(db, 'harness.start_failed' if failed_only else 'harness.start_retry' if retry else 'harness.execution_selected', task_id=attempt['task_id'],
                                         payload={'attemptId': attempt['attempt_id'], 'harness': record})
                self.store._store_receipt(db, command_id, 'harness.prepare', request, response, subject=subject, task_id=attempt['task_id'])
                head = self.store._head_of(db)
            self.store._notify(head)
            return response
        return self._guard('harness.prepare', request_json, handler)

    def quota_redetect(self, request_json: str) -> str:
        from ..routing.quota_routing import redetect
        from ...protocol.usage import identifier

        def handler(params):
            schemas.reject_unknown(params, {'harness', 'provider', 'requestId'}, 'quota.redetect')
            request_id = schemas.required_string(params, 'requestId', max_length=128)
            name = schemas.required_string(params, 'harness', max_length=32)
            from .harness_health import _name
            _name(name)
            provider = identifier(schemas.required_string(params, 'provider', max_length=128))
            if provider is None:
                raise BoardError('INVALID_ARGUMENT', 'provider must be one identifier, not free text')
            request = {'harness': name, 'provider': provider}
            command_id = 'quota-redetect:' + request_id
            with self.store.db.write() as db:
                previous = self.store._receipt(db, command_id, 'quota.redetect', request, 'quota-control')
                if previous is not None:
                    return {**previous, 'duplicate': True}
                response = {**redetect(db, name, provider), 'duplicate': False}
                self.store._append_event(db, 'harness.quota_redetect', payload=response)
                self.store._store_receipt(db, command_id, 'quota.redetect', request, response, subject='quota-control')
                head = self.store._head_of(db)
            self.store._notify(head)
            return response
        return self._guard('quota.redetect', request_json, handler)

    def _touch_harnesses(self, params, *, explicit=False):
        if not self.automatic_discovery:
            return
        specs = [params]
        specs.extend(params[key] for key in ('buddy', 'spec') if isinstance(params.get(key), dict))
        if isinstance(params.get('helpers'), list):
            specs.extend(helper.get('spec', helper) for helper in params['helpers'] if isinstance(helper, dict))
        names = {spec.get('harness') for spec in specs if isinstance(spec, dict) and isinstance(spec.get('harness'), str)}
        for name in sorted(names):
            if explicit and name in ('dsh', 'zcode', 'codex', 'claude') and self.harnesses.get(name)['status'] != 'ready':
                self.harnesses.refresh(name, force=True)
        self.harnesses.kick()

    # -- decisions ----------------------------------------------------------
    def selection_request(self, request_json: str) -> str:
        def handler(params):
            self._touch_harnesses(params, explicit=True)
            return self.decisions.request_select(params)
        return self._guard("selection.request", request_json, handler)

    def selection_get(self, request_json: str) -> str:
        return self._guard("selection.get", request_json, self.decisions.get)

    def selection_list(self, request_json: str) -> str:
        return self._guard("selection.list", request_json, self.decisions.list_decisions)

    def model_catalog_refresh(self, request_json: str) -> str:
        def handler(params: dict) -> dict:
            from ..catalog import catalog, catalog_store

            schemas.reject_unknown(params, {"requestId"}, "model.catalog.refresh")
            request_id = schemas.optional_string(params, "requestId", max_length=128)
            observation = catalog_store.begin(self.evaluation, request_id)
            if observation["response"] is not None:
                return {**observation["response"], "duplicate": True}
            if self.automatic_discovery:
                from .harness_health import HARNESSES
                for name in HARNESSES:
                    self.harnesses.refresh(name, force=True)
                with self.store.db.read() as db:
                    cached = catalog_store.current(db)
                discovered = cached.payload if cached else {'source': 'harness-health', 'providers': [], 'discoveries': [
                    {'harness': row['harness'], 'status': 'unknown', 'reason': row.get('reason')} for row in self.harnesses.all()]}
            else:
                from ...buddy.harnesses.runtime_selection import bound
                with bound(observation['harnesses']):
                    discovered = catalog.discover(directory=self.store.directory, harnesses=observation['harnesses'])
            recorded = self.evaluation.record_catalog(discovered, observation["observationId"])
            return {**recorded, "requestId": request_id}

        return self._guard("model.catalog.refresh", request_json, handler)

    def buddy_list(self, request_json: str) -> str:
        from ..catalog import catalog_store

        def handler(params):
            self._touch_harnesses(params)
            return catalog_store.buddies(self.evaluation, params)
        return self._guard("buddy.list", request_json, handler)

    # -- micro-task workflow --------------------------------------------------
    def workflow_submit(self, request_json: str) -> str:
        def handler(params):
            normalized = self.store.workflow.normalize_submission(params)
            self._touch_harnesses(normalized["spec"], explicit=True)
            return self.store.workflow.submit_normalized(params, normalized)
        return self._guard("workflow.submit", request_json, handler)

    def workflow_get(self, request_json: str) -> str:
        return self._guard("workflow.get", request_json, self.store.workflow.get)

    def workflow_decide(self, request_json: str) -> str:
        def handler(params: dict) -> dict:
            authority = workflow_module.console_authority_from_scope()
            normalized = self.store.workflow.normalize_decision(params, console_authority=authority)
            self._touch_harnesses({"helpers": normalized["helpers"]}, explicit=True)
            return self.store.workflow.decide_normalized(params, normalized, console_authority=authority)

        return self._guard("workflow.decide", request_json, handler)

    def workflow_continue(self, request_json: str) -> str:
        def handler(params: dict) -> dict:
            self._touch_harnesses(params, explicit=True)
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

    def workflow_accept(self, request_json: str) -> str:
        def handler(params: dict) -> dict:
            result = self.store.workflow.accept(
                params, console_authority=workflow_module.console_authority_from_scope()
            )
            if result.get("verdict") == "accepted" and self.on_accepted is not None:
                self.on_accepted(result["runId"])
            return result

        return self._guard("workflow.accept", request_json, handler)

    def workflow_conclude(self, request_json: str) -> str:
        def handler(params: dict) -> dict:
            return self.store.workflow.conclude(
                params, console_authority=workflow_module.console_authority_from_scope()
            )

        return self._guard("workflow.conclude", request_json, handler)

    def workflow_reclaim(self, request_json: str) -> str:
        def handler(params: dict) -> dict:
            return self.store.workflow.reclaim(
                params, console_authority=workflow_module.console_authority_from_scope()
            )

        return self._guard("workflow.reclaim", request_json, handler)

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

    def worker_sessions(self, request_json: str) -> str:
        from ..tasks import worker_sessions
        return self._guard("worker.sessions", request_json,
                           lambda params: worker_sessions.handle(self.store, params))

    def workflow_suggest(self, request_json: str) -> str:
        def handler(params: dict) -> dict:
            return self.store.workflow.suggest(params, scope=workflow_module.current_scope())

        return self._guard("workflow.suggest", request_json, handler)

    # -- work macro tasks ----------------------------------------------------
    def macro_task_list(self, request_json: str) -> str:
        # Read-only: no lease, no model call; an attempt-scoped credential is refused
        # by the agent operation allowlist before this handler runs.
        from ..tasks import macro_tasks

        return self._guard("macro_task.list", request_json, lambda params: macro_tasks.macro_task_list(self.store, params))

    def macro_task_timeline(self, request_json: str) -> str:
        from ..tasks import macro_tasks

        return self._guard(
            "macro_task.timeline", request_json, lambda params: macro_tasks.macro_task_timeline(self.store, params)
        )

    def macro_task_stop(self, request_json: str) -> str:
        return self._guard(
            "macro_task.stop", request_json,
            lambda params: self.store.workflow.stop_macro_task(
                params, console_authority=workflow_module.console_authority_from_scope()
            ),
        )

    def macro_task_deletion_preview(self, request_json: str) -> str:
        from ..tasks import macro_task_deletion

        return self._guard(
            "macro_task.deletion_preview", request_json,
            lambda params: macro_task_deletion.preview_request(
                self.store, params, console_authority=workflow_module.console_authority_from_scope()
            ),
        )

    def macro_task_delete(self, request_json: str) -> str:
        from ..tasks import macro_task_deletion

        return self._guard(
            "macro_task.delete", request_json,
            lambda params: macro_task_deletion.delete_request(
                self.store, params, console_authority=workflow_module.console_authority_from_scope()
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
        return self._guard("task.wait", request_json, self.store.task_wait)

    # -- executions ---------------------------------------------------------
    def admit_execution(self, nonce: str, task_id: str | None = None) -> dict:
        with self._admission_lock:
            if self.control.get("stopping") or self.control.get("restart_requested"):
                return {"claim": None, "reason": "service-stopping"}
            if self.automatic_discovery:
                # Refresh the queued harness before admission freezes its continuation.
                with self.store.db.read() as db:
                    names = {row[0] for row in db.execute(
                        "SELECT DISTINCT adapter FROM tasks WHERE state='queued' "
                        "AND queue_reason IN ('awaiting-execution','capacity','awaiting-buddy-validation')")}
                for name in sorted(names & {'dsh', 'zcode', 'codex', 'claude'}):
                    self.harnesses.refresh(name, preflight=True)
            self.store.workflow.prepare_continuation_workspace({"taskId": task_id} if task_id else {})
            response = self.store.admit_execution(nonce, task_id=task_id)
            claim = response.get("claim")
            if claim is not None:
                record_path = None
                if claim["task"]["spec"]["adapter"] != "command":
                    with self.store.db.read() as connection:
                        member = connection.execute(
                            f"SELECT group_id FROM ({macro_tasks.MEMBERS_SQL}) WHERE task_id=?",
                            (claim["task"]["taskId"],)).fetchone()
                    if member is not None:
                        record_path = str(session_record_path(
                            self.store.directory, member["group_id"], claim["attempt"]["attemptId"]))
                claim["sessionRecordPath"] = record_path
            return response

    def execution_progress(self, request_json: str) -> str:
        def handler(params: dict) -> dict:
            verify = self.execution_scheduler.verify_identity if self.execution_scheduler is not None else None
            return self.store.execution_progress(params, verify_identity=verify)
        return self._guard("execution.progress", request_json, handler)

    def execution_result(self, request_json: str) -> str:
        return self._guard("execution.result", request_json, self.store.execution_result_reference)

    # -- messages -----------------------------------------------------------
    def message_get(self, request_json: str) -> str:
        return self._guard("message.get", request_json, self.store.message_get)

    def message_list(self, request_json: str) -> str:
        return self._guard("message.list", request_json, self.store.message_list)

    def inquiry_observe(self, request_json: str) -> str:
        def handler(params: dict) -> dict:
            from ..tasks.inquiry import observe

            return observe(self.store, params, live_registry=self.live_registry)

        return self._guard("inquiry.observe", request_json, handler)

    def artifact_list(self, request_json: str) -> str:
        return self._guard("artifact.list", request_json, self.store.artifact_list)

    # -- events -------------------------------------------------------------
    def events_read(self, request_json: str) -> str:
        return self._guard("events.read", request_json, self.store.events_read)


class WaitService(_BaseResource):
    """Dedicated bounded wait resource."""

    def __init__(self, store: BoardStore, admission: WaitAdmission, token: str = ""):
        super().__init__(store, token)
        self.admission = admission

    def _bounded(self, operation: str, request_json: str, handler: Callable[[dict], dict]) -> str:
        def admitted(params: dict) -> dict:
            if not self.admission.acquire():
                raise BoardError(
                    "WAIT_OVERLOAD",
                    "Every admitted wait slot is busy; no task state changed. Retry with the same cursor.",
                    cursor=params.get("after", self.store.head()), retryAfterMs=250,
                    capacity=self.admission.capacity,
                )
            try:
                return handler(params)
            finally:
                self.admission.release()

        return self._guard(operation, request_json, admitted)

    def events_wait(self, request_json: str) -> str:
        return self._bounded("events.wait", request_json, self.store.events_wait)

    def task_wait(self, request_json: str) -> str:
        return self._bounded("task.wait", request_json, self.store.task_wait)

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
