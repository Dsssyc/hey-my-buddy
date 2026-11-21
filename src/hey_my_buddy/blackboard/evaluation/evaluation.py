"""Published buddies, user settings and bounded selection views."""
from __future__ import annotations

import json
import sqlite3

from ...protocol import schemas
from ...protocol.reasons import reason_from_json
from . import user_policy

MAX_DECISION_PAGE = 50


class EvaluationStore:
    """Settings and selection state composed with the authoritative BoardStore."""

    def __init__(self, board, *, clock=None):
        self.board = board
        self.db = board.db
        self._clock = clock or board.now

    def _now(self) -> str:
        return self._clock()

    def console_core(self) -> dict:
        """One coherent view of buddies, policy, configuration and selection history."""
        with self.board.db.read() as connection:
            from ..routing import router
            settings = router.configuration(connection)
            _profile_buddy, profile_problem = self.board.profile.readiness(connection)
            configured_ids = settings["routerBuddyIds"]
            from ..catalog import buddy_state, catalog_store
            all_buddies = buddy_state.rows(connection)
            references = catalog_store.removal_references(connection)
            buddies = [
                self._buddy_view(row, references)
                for row in all_buddies
                if row["available"] or row["buddy_id"] in configured_ids
            ]
            from ..service.harness_health import for_provider
            from ..routing.quota_routing import cooling
            for buddy in buddies:
                buddy["billing"] = for_provider(connection, buddy["harness"], buddy["provider"])
                buddy['providerCooling'] = cooling(connection, buddy)
            buddy_ids = [buddy["buddyId"] for buddy in buddies]
            policy = user_policy.policy_view(connection, buddy_ids)
            decisions = [
                self._decision_view(row)
                for row in connection.execute(
                    "SELECT d.*, j.domain_node_id, j.task_type, n.name AS domain_node_name,"
                    " r.task_id AS run_id, r.updated_at AS updated_at,"
                    " (SELECT snapshot_json FROM router_requests s WHERE s.decision_id=d.decision_id"
                    " ORDER BY round DESC LIMIT 1) AS snapshot_json"
                    " FROM evaluation_decisions d"
                    " LEFT JOIN routing_judgments j ON j.decision_id=d.decision_id"
                    " LEFT JOIN domain_nodes n ON n.node_id=j.domain_node_id"
                    " LEFT JOIN decision_requests r ON r.decision_id = d.decision_id"
                    " ORDER BY d.created_at DESC, d.decision_id DESC LIMIT ?",
                    (MAX_DECISION_PAGE,),
                )
            ]
            unavailable_count = sum(not row["available"] for row in all_buddies)
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
            "configuration": {
                **settings,
                "profileUnavailableReason": profile_problem,
            },
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
            "decisionAdapterReason": adapter_reason,
            "modelCatalogDiscovery": bool(catalog.discovery_available()),
        }

    def record_catalog(self, discovered: dict, sequence=None) -> dict:
        from ..catalog import catalog_store
        return catalog_store.record(self, discovered, sequence)

    @staticmethod
    def _buddy_view(row: dict, references: dict) -> dict:
        from ...protocol.capabilities import buddy_capabilities
        from ..catalog import catalog_store
        capabilities = buddy_capabilities(row["harness"], json.loads(row["capabilities_json"]))
        view = {
            "buddyId": row["buddy_id"],
            "alias": row["alias"],
            "harness": row["harness"],
            "provider": row["provider"],
            "model": row["model"],
            "effort": row["effort"],
            "effortRank": row["effort_rank"],
            "available": bool(row["available"]),
            "enabled": bool(row["enabled"]),
            "capabilities": capabilities,
            "contextWindow": row["context_window"],
            "description": row["description"],
            "source": row["source"],
        }
        if row["unavailable_code"]:
            view["unavailableReason"] = reason_from_json(row["unavailable_code"], row["unavailable_params_json"])
            if row["unavailable_content"] is not None:
                view["unavailableContent"] = row["unavailable_content"]
        view["catalogState"] = row["catalog_state"] or "unknown"
        view["catalogReason"] = json.loads(row["catalog_reason"]) if row["catalog_reason"] else None
        # ADR-027 rule 6: the model-level catalog judgment, separate from the
        # harness-level reading state above. Pending is per model identity.
        view["catalogStatus"] = row["catalog_status"]
        if row["pending_since"] is not None:
            view["pendingSince"] = row["pending_since"]
        if catalog_store.is_removal_candidate(row):
            blocker = catalog_store.removal_blocker(row, references)
            view["removal"] = {"allowed": False, "reason": blocker} if blocker else {"allowed": True}
        return view

    @staticmethod
    def _decision_view(row: sqlite3.Row) -> dict:
        view = {
            "decisionId": row["decision_id"],
            "status": row["status"],
            "task": json.loads(row["snapshot_json"])["baseInput"]["task"],
            "buddyId": row["buddy_id"],
            "reason": reason_from_json(row["reason_code"], row["reason_params_json"]),
            "note": row["note"],
            "createdAt": row["created_at"],
        }
        view["runId"] = row["run_id"]
        if row["updated_at"] is not None:
            view["updatedAt"] = row["updated_at"]
        if row["task_type"] is not None:
            view["domainNodeId"] = row["domain_node_id"]
            view["domainNodeName"] = row["domain_node_name"]
            view["taskType"] = row["task_type"]
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
        return response
