"""Published buddies, user settings and bounded selection views."""
from __future__ import annotations

import json
import sqlite3
from typing import Any

from ...protocol import schemas
from . import user_policy
from ...errors import BoardError

MAX_DECISION_PAGE = 50


class EvaluationStore:
    """Settings and selection state composed with the authoritative BoardStore."""

    def __init__(self, board, *, clock=None):
        self.board = board
        self.db = board.db
        self._clock = clock or board.now

    def _now(self) -> str:
        return self._clock()

    @staticmethod
    def _validate_configuration(entry: Any) -> dict:
        """The strict list-settings patch: omissions preserve, ``[]`` clears, everything else validates."""
        from ..routing.router_settings import validate_router_settings_patch
        return validate_router_settings_patch(entry)

    def console_core(self) -> dict:
        """One coherent view of buddies, policy, configuration and selection history."""
        with self.board.db.read() as connection:
            now = self._now()
            from ..routing import router
            configuration_error = None
            try:
                settings = router.configuration(connection)
                routing_budget = router.configured_budget(connection)
            except BoardError as error:
                settings, routing_budget = None, None
                configuration_error = {"code": error.code, "message": str(error)}
            configured_ids = (settings or {}).get("routerBuddyIds") or []
            configured_marks = ",".join("?" for _ in configured_ids) or "NULL"
            from ..catalog import catalog_store
            from ..catalog.catalog import pending_model_services
            pending = pending_model_services(connection)
            current_catalog = catalog_store.current(connection)
            effort_ranks = current_catalog.effort_ranks() if current_catalog is not None else {}
            buddies = [
                self._buddy_view(row, pending=pending, effort_ranks=effort_ranks)
                for row in connection.execute(
                    "SELECT p.*, h.status AS harness_status, c.status AS catalog_state, c.reason AS catalog_reason FROM buddies p "
                    "LEFT JOIN harness_health h ON h.harness=p.harness "
                    "LEFT JOIN catalog_current c ON c.harness=p.harness "
                    f"WHERE (p.available=1 AND h.status='ready') OR p.buddy_id IN ({configured_marks}) ORDER BY p.rowid LIMIT 202",
                    configured_ids
                )
            ]
            from ...protocol.billing import for_provider
            from ..routing.quota_routing import buddy_retry
            for buddy in buddies:
                buddy["billing"] = for_provider(connection, buddy["harness"], buddy["provider"])
                from .native_observations import exhausted
                buddy['quotaExhausted'] = exhausted(connection, buddy, now=now) is not None
                if buddy['quotaExhausted']:
                    buddy['quotaRetry'] = buddy_retry(connection, buddy, now=now)
            buddy_ids = [buddy["buddyId"] for buddy in buddies]
            policy = user_policy.policy_view(connection, buddy_ids)
            decisions = [
                self._decision_view(row)
                for row in connection.execute(
                    "SELECT d.*, r.kind AS kind, r.task_id AS run_id, r.updated_at AS updated_at"
                    " FROM evaluation_decisions d LEFT JOIN decision_requests r ON r.decision_id = d.decision_id"
                    " ORDER BY d.created_at DESC, d.decision_id DESC LIMIT ?",
                    (MAX_DECISION_PAGE,),
                )
            ]
            unavailable_count = connection.execute(
                "SELECT COUNT(*) AS count FROM buddies p LEFT JOIN harness_health h ON h.harness=p.harness WHERE p.available=0 OR h.status IS NULL OR h.status!='ready'"
            ).fetchone()["count"]
            # One row per model service represented in this response, with the
            # effective limit (explicit setting or default) and the unresolved
            # attempt count. Effort variants collapse into their shared model service.
            model_services = {
                (buddy["harness"], buddy["provider"], buddy["model"])
                for buddy in buddies
            }
            model_service_concurrency = self.board.model_capacity_rows(connection, model_services)
            event_cursor = self.board._head_of(connection)
        return {
            "eventCursor": event_cursor,
            "configuration": ({
                **settings,
                "routingBudgetLimits": routing_budget,
            } if settings is not None else None),
            "configurationError": configuration_error,
            "buddies": buddies,
            "modelServiceConcurrency": model_service_concurrency,
            "unavailableBuddyCount": unavailable_count,
            **policy,
            "decisions": decisions,
        }

    def snapshot(self, params: dict) -> dict:
        schemas.reject_unknown(params, set(), "console.snapshot")
        core = self.console_core()
        tasks = self.board.task_list({"limit": 100, "offset": 0})
        return {
            "csrfToken": "",
            **core,
            # The latest 100 rows stay exactly what the snapshot has always shown
            # (helpers and internal decisions included); ``nextCursor`` is the keyset
            # position the separate task-history read resumes from.
            "tasks": {"runs": tasks["runs"], "total": int(tasks["total"]), "nextCursor": tasks["nextCursor"]},
            "capabilities": self.capabilities(),
        }

    def capabilities(self) -> dict:
        """Recorded availability of the implemented catalog and selection paths."""
        from ..catalog import catalog
        # Even model-free reads use this board's recorded harness health, not
        # whichever native executables happen to be visible to the caller.
        adapter_available, adapter_reason = self.board.decisions._adapter_available()
        return {
            "selection": bool(adapter_available),
            "decisionAdapter": bool(adapter_available),
            "decisionAdapterReason": adapter_reason,
            "modelCatalogDiscovery": bool(catalog.discovery_available()),
            "taskControl": True,
        }

    def record_catalog(self, discovered: dict, observation_id=None) -> dict:
        from ..catalog import catalog_store
        return catalog_store.record(self, discovered, observation_id)

    @staticmethod
    def _buddy_view(row: sqlite3.Row, *, pending: dict | None = None,
                    effort_ranks: dict[tuple[str, str, str], dict[str, int]]) -> dict:
        from ...buddy.harnesses.registry import adapters
        native = adapters().get(row["harness"])
        capabilities = json.loads(row["capabilities_json"])
        if not (native and native.local_read_only_check()["eligible"]):
            capabilities = [item for item in capabilities if item != "decision"]
        elif 'decision' not in capabilities:
            capabilities.append('decision')
        capabilities = [item for item in capabilities if item != "routing:fast"]
        if native and getattr(native, "no_tool_structured", False):
            capabilities.append("routing:fast")
        view = {
            "buddyId": row["buddy_id"],
            "label": row["label"],
            "harness": row["harness"],
            "provider": row["provider"],
            "model": row["model"],
            "effort": row["effort"],
            "effortRank": effort_ranks.get((row["harness"], row["provider"], row["model"]), {}).get(row["effort"]),
            "available": bool(row["available"]) and ("harness_status" not in row.keys() or row["harness_status"] == "ready"),
            "enabled": bool(row["enabled"]),
            "capabilities": capabilities,
            "contextWindow": row["context_window"],
            "description": row["description"],
            "source": row["source"],
        }
        if row["unavailable_code"]:
            view["unavailableReason"] = {"code": row["unavailable_code"],
                                         "params": json.loads(row["unavailable_params_json"])}
            if row["unavailable_content"] is not None:
                view["unavailableContent"] = row["unavailable_content"]
        if "catalog_state" in row.keys():
            view["catalogState"] = row["catalog_state"] or "unknown"
            view["catalogReason"] = json.loads(row["catalog_reason"]) if row["catalog_reason"] else None
        if "catalog_state" in row.keys() or pending is not None:
            # ADR-027 rule 6: the model-level catalog judgment, separate from the
            # harness-level reading state above. Pending is per model identity.
            from ..catalog.catalog import CATALOG_FACT_REASONS
            model_service = (row["harness"], row["provider"], row["model"])
            since = (pending or {}).get(model_service)
            if since:
                view["catalogStatus"] = "pending"
                view["pendingSince"] = since
            elif not row["available"] and row["unavailable_code"] in (*CATALOG_FACT_REASONS, 'catalog.account-binding-changed'):
                view["catalogStatus"] = "unavailable"
            else:
                view["catalogStatus"] = "available"
        return view

    @staticmethod
    def _decision_view(row: sqlite3.Row) -> dict:
        keys = row.keys()
        view = {
            "decisionId": row["decision_id"],
            "status": row["status"],
            "task": row["task"],
            "buddyId": row["buddy_id"],
            "reason": row["reason"],
            "createdAt": row["created_at"],
        }
        if "kind" in keys and row["kind"] is not None:
            view["kind"] = row["kind"]
        if "run_id" in keys:
            view["runId"] = row["run_id"]
        if "updated_at" in keys and row["updated_at"] is not None:
            view["updatedAt"] = row["updated_at"]
        if row["error"]:
            view["error"] = row["error"]
        return view

    def user_policy_publish(self, params: dict) -> dict:
        """Apply an authenticated settings patch, retaining command receipts."""
        user_policy.require_console()
        schemas.reject_unknown(params, {"commandId", *user_policy.PATCH_FIELDS}, "user_policy.publish")
        command_id = schemas.required_string(params, "commandId", max_length=128)
        request = {key: value for key, value in params.items() if key != "commandId"}
        with self.db.write() as connection:
            receipt = self.board._receipt(connection, command_id, "user_policy.publish", request)
            if receipt is not None:
                return {**receipt, "duplicate": True}
            now = self._now()
            published = user_policy.publish(self, connection, now=now, params=params)
            self.board._append_event(connection, "evaluation.published",
                                     payload={"kind": "human", "counts": published["counts"]})
            response = {"published": True, "counts": published["counts"], "duplicate": False}
            self.board._store_receipt(connection, command_id, "user_policy.publish", request, response)
            head = self.board._head_of(connection)
        self.board._notify(head)
        if published.get("enabledModelServices"):
            from . import model_facts
            try:
                model_facts.refresh_model_services(self.board, published["enabledModelServices"], now=self._now())
            except Exception:  # noqa: BLE001 - a committed setting does not depend on the network
                pass
        return response
