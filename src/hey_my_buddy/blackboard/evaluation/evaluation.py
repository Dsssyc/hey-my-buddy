"""Published profiles, user settings and bounded selection views."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any

from ...protocol import schemas
from . import user_policy
from ...errors import BoardError

MAX_DECISION_PAGE = 50


def _timestamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def parse_timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


class EvaluationStore:
    """Settings and selection state composed with the authoritative BoardStore."""

    def __init__(self, board, *, clock=None, reader_lease_seconds: int = 300):
        self.board = board
        self.db = board.db
        self._clock = clock or board.now
        self.reader_lease_seconds = max(5, min(int(reader_lease_seconds), 3600))

    def _now(self) -> str:
        return self._clock()

    def _plus(self, seconds: int, now: str | None = None) -> str:
        return _timestamp(parse_timestamp(now or self._now()) + timedelta(seconds=seconds))

    def _state(self, connection: sqlite3.Connection) -> sqlite3.Row:
        row = connection.execute("SELECT * FROM evaluation_state WHERE id=1").fetchone()
        if row is None:  # pragma: no cover - initialize always seeds the singleton
            raise BoardError("INTERNAL_ERROR", "The evaluation state row is missing; reinitialize the board")
        return row

    @staticmethod
    def _validate_configuration(entry: Any) -> dict:
        """The strict list-settings patch: omissions preserve, ``[]`` clears, everything else validates."""
        from ..routing.router_settings import validate_router_settings_patch
        return validate_router_settings_patch(entry)

    def console_core(self) -> dict:
        """One coherent view of profiles, policy, configuration and selection history."""
        with self.board.db.read() as connection:
            now = self._now()
            state = self._state(connection)
            table_revision = int(state["table_revision"])
            from ..routing import router
            configuration_error = None
            try:
                settings = router.configuration(connection)
                routing_budget = router.configured_budget(connection)
            except BoardError as error:
                settings, routing_budget = None, None
                configuration_error = {"code": error.code, "message": str(error),
                                       "revision": int(state["configuration_revision"])}
            configured_ids = (settings or {}).get("routerProfileIds") or []
            configured_marks = ",".join("?" for _ in configured_ids) or "NULL"
            from ..catalog.catalog import pending_families
            pending = pending_families(connection)
            profiles = [
                self._profile_view(row, pending=pending)
                for row in connection.execute(
                    "SELECT p.*, h.status AS harness_status, c.status AS catalog_state, c.reason AS catalog_reason FROM evaluation_profiles p "
                    "LEFT JOIN harness_health h ON h.adapter=p.adapter "
                    "LEFT JOIN catalog_current c ON c.adapter=p.adapter "
                    f"WHERE (p.available=1 AND h.status='ready') OR p.profile_id IN ({configured_marks}) ORDER BY p.rowid LIMIT 202",
                    configured_ids
                )
            ]
            from ...protocol.billing import for_provider
            from ..routing.quota_routing import configuration_retry
            for profile in profiles:
                profile["billing"] = for_provider(connection, profile["adapter"], profile["provider"])
                from .native_observations import exhausted
                profile['quotaExhausted'] = exhausted(connection, profile, now=now) is not None
                if profile['quotaExhausted']:
                    profile['quotaRetry'] = configuration_retry(connection, profile, now=now)
            profile_ids = [profile["profileId"] for profile in profiles]
            policy = user_policy.policy_view(connection, profile_ids)
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
                "SELECT COUNT(*) AS count FROM evaluation_profiles p LEFT JOIN harness_health h ON h.adapter=p.adapter WHERE p.available=0 OR h.status IS NULL OR h.status!='ready'"
            ).fetchone()["count"]
            # One row per model family represented in this response, with the
            # effective limit (explicit setting or default) and the unresolved
            # attempt count. Effort variants collapse into their shared family.
            families = {
                (profile["adapter"], profile["provider"], profile["model"])
                for profile in profiles
            }
            model_concurrency = self.board.model_capacity_rows(connection, families)
        return {
            "tableRevision": table_revision,
            "configuration": ({
                "revision": int(state["configuration_revision"]),
                **settings,
                "routingBudgetLimits": routing_budget,
            } if settings is not None else None),
            "configurationError": configuration_error,
            "profiles": profiles,
            "modelConcurrency": model_concurrency,
            "unavailableProfileCount": unavailable_count,
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
    def _profile_view(row: sqlite3.Row, *, pending: dict | None = None) -> dict:
        from ...buddy.harnesses.registry import adapters
        native = adapters().get(row["adapter"])
        capabilities = json.loads(row["capabilities_json"])
        if not (native and native.local_read_only_check()["eligible"]):
            capabilities = [item for item in capabilities if item != "decision"]
        elif 'decision' not in capabilities:
            capabilities.append('decision')
        capabilities = [item for item in capabilities if item != "routing:fast"]
        if native and getattr(native, "no_tool_structured", False):
            capabilities.append("routing:fast")
        view = {
            "profileId": row["profile_id"],
            "label": row["label"],
            "adapter": row["adapter"],
            "provider": row["provider"],
            "model": row["model"],
            "effort": row["effort"],
            "available": bool(row["available"]) and ("harness_status" not in row.keys() or row["harness_status"] == "ready"),
            "enabled": bool(row["enabled"]),
            "capabilities": capabilities,
            "contextWindow": row["context_window"],
            "description": row["description"],
            "source": row["source"],
        }
        if row["unavailable_reason"]:
            view["unavailableReason"] = row["unavailable_reason"]
        if not row['enabled'] and row['created_revision'] == row['updated_revision']:
            view['newlyDiscovered'] = True
        if "catalog_state" in row.keys():
            view["catalogState"] = row["catalog_state"] or "unknown"
            view["catalogReason"] = row["catalog_reason"]
        if "catalog_state" in row.keys() or pending is not None:
            # ADR-027 rule 6: the model-level catalog judgment, separate from the
            # harness-level reading state above. Pending is per model identity.
            from ..catalog.catalog import CONFIRMED_ABSENCE_REASON
            family = (row["adapter"], row["provider"], row["model"])
            since = (pending or {}).get(family)
            if since:
                view["catalogStatus"] = "pending"
                view["pendingSince"] = since
            elif not row["available"] and row["unavailable_reason"] in (CONFIRMED_ABSENCE_REASON, 'ACCOUNT_BINDING_CHANGED'):
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
            "profileId": row["profile_id"],
            "tableRevision": int(row["table_revision"]),
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
        """Apply an authenticated settings patch against one published revision."""
        user_policy.require_console()
        schemas.reject_unknown(params, {"commandId", "expectedRevision", *user_policy.PATCH_FIELDS}, "user_policy.publish")
        command_id = schemas.required_string(params, "commandId", max_length=128)
        expected = schemas.require_expected_revision(params)
        request = {key: value for key, value in params.items() if key != "commandId"}
        with self.db.write() as connection:
            receipt = self.board._receipt(connection, command_id, "user_policy.publish", request)
            if receipt is not None:
                return {**receipt, "duplicate": True}
            revision = int(self._state(connection)["table_revision"])
            if expected != revision:
                raise BoardError("REVISION_CONFLICT", "The table changed; re-read the current revision and publish again",
                                 expectedRevision=expected, currentRevision=revision)
            now = self._now()
            published = user_policy.publish(self, connection, revision=revision + 1, now=now, params=params)
            self.board._append_event(connection, "evaluation.published", revision=published["revision"],
                                     payload={"kind": "human", "counts": published["counts"]})
            response = {"published": True, "revision": published["revision"], "tableRevision": published["revision"],
                        "configurationRevision": published["configurationRevision"], "counts": published["counts"],
                        "duplicate": False}
            self.board._store_receipt(connection, command_id, "user_policy.publish", request, response)
            head = self.board._head_of(connection)
        self.board._notify(head)
        if published.get("enabledFamilies"):
            from . import model_facts
            try:
                model_facts.refresh_families(self.board, published["enabledFamilies"], now=self._now())
            except Exception:  # noqa: BLE001 - a committed setting does not depend on the network
                pass
        return response
