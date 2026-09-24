"""Shared bounded evaluation table, its durable gate and its evidence ledger.

Python alone commits authority here. Every mutation is one short ``BEGIN IMMEDIATE``
transaction that also appends its event and (where a command id is supplied) its
idempotency receipt, so a reader that sees a published revision also sees the record
that produced it. No transaction in this module spans RPC, a model call, a
subprocess or a wait.

The gate is **table-level**, never board-level:

* a business task that already started keeps its accepted route and configuration and
  is not a reader; an ordinary view refresh is not a reader either;
* a writer stops admission of *new* selection readers, drains the admitted bounded
  readers, then publishes one complete immutable revision and releases its grant;
* writer authority is generation-, lease- and token-fenced. Expiry and abort fence a
  late write; neither claims that any process has stopped.

Discoveries, decisions and evidence keep their own identities. A published revision
is never rewritten in place; every publish creates the next revision atomically.
"""
from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from . import schemas
from .catalog import CatalogView
from .db import canonical_json, sha256_text
from .errors import BoardError

#: Bounded collections. A publish replaces a collection wholesale, so these limits
#: bound one published revision, not the archive.
MAX_PROFILES = 200
MAX_CARDS = 500
MAX_PREFERENCES = 500
MAX_EVIDENCE_IDS = 64
MAX_EVIDENCE_PAGE = 200
MAX_DECISION_PAGE = 50
MAX_CARD_POINTS = 16
MAX_CONDITIONS = 8
MAX_CAPABILITIES = 32
MAX_TEXT = 2000
MAX_SUMMARY = 4000
MAX_POINT = 500
MAX_REASON = 500
MAX_DESCRIPTION = 4000

WRITER_KINDS = ("human", "maintenance")
READER_KINDS = ("selection",)

#: ``task-success`` and ``task-failure`` are the only kinds whose real accepted
#: result can count as a model-performance sample. ``task-cancelled`` links a run for
#: provenance but never counts: cancelled work is not evidence about model quality.
EVIDENCE_KINDS = (
    "task-success",
    "task-failure",
    "task-cancelled",
    "observation",
    "incident",
    "correction",
    "manual",
)
TASK_EVIDENCE_KINDS = frozenset({"task-success", "task-failure", "task-cancelled"})

#: Failure text that names the harness, the adapter or the environment rather than
#: the model's work. Such a failure is still real evidence (``verified``) but it is
#: never counted as a model-capability sample.
INFRASTRUCTURE_FAILURE_MARKERS = (
    "ADAPTER_UNAVAILABLE",
    "worker failure:",
    "invalid-result",
    "did not start",
)

PROFILE_FIELDS = frozenset(
    {
        "profileId",
        "label",
        "adapter",
        "provider",
        "model",
        "effort",
        "available",
        "enabled",
        "capabilities",
        "contextWindow",
        "description",
        "source",
        "unavailableReason",
    }
)
#: Card input is text, risk and evidence references only: the counters, revision and
#: timestamp are derived by the backend and a supplied one is an unknown field.
CARD_FIELDS = frozenset({"profileId", "summary", "strengths", "limitations", "risks", "evidenceIds"})
PREFERENCE_FIELDS = frozenset({"profileId", "mode", "reason"})
CONFIGURATION_FIELDS = frozenset({"decisionProfileId", "autoMaintain"})
PUBLISH_FIELDS = frozenset(
    {
        "commandId",
        "writerId",
        "generation",
        "writerToken",
        "expectedRevision",
        "profiles",
        "cards",
        "preferences",
        "configuration",
    }
)
#: The publish request fingerprint. The first four are required; the rest keep the
#: caller's provided-field set so omission and an explicit null never collapse.
PUBLISH_REQUEST_FIELDS = (
    "writerId",
    "generation",
    "writerToken",
    "expectedRevision",
    "profiles",
    "cards",
    "preferences",
    "configuration",
)


def _timestamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def parse_timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


class EvaluationStore:
    """Durable evaluation table composed with the authoritative :class:`BoardStore`.

    The board keeps owning the SQLite file, its pragmas and its event stream; this
    module owns only the evaluation tables and never opens a transaction that spans
    anything slower than bounded Python validation.
    """

    def __init__(
        self,
        board,
        *,
        clock=None,
        writer_lease_seconds: int = 60,
        writer_queue_seconds: int = 120,
        reader_lease_seconds: int = 300,
    ):
        self.board = board
        self.db = board.db
        self._clock = clock or board.now
        self.writer_lease_seconds = max(5, min(int(writer_lease_seconds), 3600))
        self.writer_queue_seconds = max(5, min(int(writer_queue_seconds), 3600))
        self.reader_lease_seconds = max(5, min(int(reader_lease_seconds), 3600))

    # -- time ----------------------------------------------------------------
    def _now(self) -> str:
        return self._clock()

    def _plus(self, seconds: int, now: str | None = None) -> str:
        return _timestamp(parse_timestamp(now or self._now()) + timedelta(seconds=seconds))

    # -- state helpers -------------------------------------------------------
    def _state(self, connection: sqlite3.Connection) -> sqlite3.Row:
        row = connection.execute("SELECT * FROM evaluation_state WHERE id=1").fetchone()
        if row is None:  # pragma: no cover - initialize always seeds the singleton
            raise BoardError("INTERNAL_ERROR", "The evaluation state row is missing; reinitialize the board")
        return row

    def _sweep(self, connection: sqlite3.Connection, now: str) -> dict:
        """Expire bounded readers and writer grants. Expiry is never a stop claim."""
        readers = connection.execute(
            "UPDATE evaluation_readers SET released_at=?, expired=1 WHERE released_at IS NULL AND expires_at <= ?",
            (now, now),
        ).rowcount
        active = connection.execute(
            "UPDATE evaluation_writers SET state='expired', released_at=? WHERE state='active' AND expires_at <= ?",
            (now, now),
        ).rowcount
        waiting = connection.execute(
            "UPDATE evaluation_writers SET state='expired', released_at=? WHERE state='waiting' AND expires_at <= ?",
            (now, now),
        ).rowcount
        return {"readers": readers, "writers": active + waiting}

    def _admitted_readers(self, connection: sqlite3.Connection, now: str | None = None) -> int:
        query = "SELECT COUNT(*) AS count FROM evaluation_readers WHERE released_at IS NULL"
        values: tuple = ()
        if now is not None:
            # The read-only view never writes, so it filters expired leases instead of
            # materializing them; a write path has already swept them away.
            query += " AND expires_at > ?"
            values = (now,)
        row = connection.execute(query, values).fetchone()
        return int(row["count"])

    def _waiting_writers(self, connection: sqlite3.Connection, now: str | None = None) -> int:
        query = "SELECT COUNT(*) AS count FROM evaluation_writers WHERE state='waiting'"
        values: tuple = ()
        if now is not None:
            query += " AND expires_at > ?"
            values = (now,)
        row = connection.execute(query, values).fetchone()
        return int(row["count"])

    def _active_writer(self, connection: sqlite3.Connection, now: str | None = None) -> sqlite3.Row | None:
        query = "SELECT * FROM evaluation_writers WHERE state='active'"
        values: tuple = ()
        if now is not None:
            query += " AND expires_at > ?"
            values = (now,)
        query += " ORDER BY generation LIMIT 1"
        return connection.execute(query, values).fetchone()

    def _promote(self, connection: sqlite3.Connection, now: str) -> None:
        """Grant the oldest waiting writer once no admitted reader remains.

        Fairness is durable ordering by generation: a later intent can never overtake
        an earlier one, and a queued writer is promoted by reader release, by an
        expiring grant or by another writer's publish — never by a hidden lock.
        """
        if self._active_writer(connection) is not None:
            return
        if self._admitted_readers(connection) > 0:
            return
        head = connection.execute(
            "SELECT * FROM evaluation_writers WHERE state='waiting' ORDER BY generation LIMIT 1"
        ).fetchone()
        if head is None:
            return
        connection.execute(
            "UPDATE evaluation_writers SET state='active', granted_at=?, expires_at=?, released_at=NULL"
            " WHERE writer_id=?",
            (now, self._plus(self.writer_lease_seconds, now), head["writer_id"]),
        )
        self.board._append_event(
            connection,
            "evaluation.writer_granted",
            revision=int(self._state(connection)["table_revision"]),
            payload={"writerId": head["writer_id"], "generation": head["generation"], "kind": head["kind"]},
        )

    def _gate_view(self, connection: sqlite3.Connection, now: str) -> dict:
        """The gate as of ``now``. A read path filters expired leases instead of writing."""
        active = self._active_writer(connection, now)
        waiting = self._waiting_writers(connection, now)
        readers = self._admitted_readers(connection, now)
        if active is not None:
            phase = "writing"
        elif waiting and readers:
            phase = "draining"
        else:  # pragma: no cover - promote() already granted a head with no readers
            phase = "open" if not waiting else "draining"
        return {
            "phase": phase,
            "readers": readers,
            "writer": None
            if active is None
            else {
                "writerId": active["writer_id"],
                "kind": active["kind"],
                "generation": int(active["generation"]),
                "expiresAt": active["expires_at"],
            },
            "waitingWriters": waiting,
        }

    # -- validation helpers --------------------------------------------------
    @staticmethod
    def _required_revision(params: dict, name: str) -> int:
        value = params.get(name)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise BoardError("INVALID_ARGUMENT", f"{name} must be a nonnegative integer")
        return int(value)

    @staticmethod
    def _points(entry: dict, name: str) -> list[str]:
        return schemas.string_list(entry, name, limit=MAX_CARD_POINTS)

    def _catalog(self, connection: sqlite3.Connection) -> CatalogView | None:
        row = connection.execute(
            "SELECT * FROM evaluation_catalog ORDER BY discovered_at DESC, discovery_id DESC LIMIT 1"
        ).fetchone()
        if row is None:
            return None
        return CatalogView.from_payload(json.loads(row["payload_json"]), row)

    def _resolve_availability(
        self,
        *,
        adapter: str,
        provider: str,
        model: str,
        effort: str,
        available: bool,
        enabled: bool,
        reason: str | None,
        catalog: CatalogView | None,
    ) -> tuple[bool, str | None]:
        """Decide the honest availability flag for one published profile.

        A publisher may never *claim* availability the installed harness catalog does
        not advertise. With no recorded discovery an execution identity is unknown,
        so it is published as unavailable rather than as an unverified "working".
        """
        from .adapters import adapters
        native = adapters().get(adapter)
        if native is not None and native.model_discovery:
            if catalog is None:
                if available:
                    raise BoardError(
                        "CATALOG_UNAVAILABLE",
                        "Cannot publish this profile as available: no model catalog discovery is recorded. "
                        "Run model_catalog_refresh against the installed harness first.",
                        profileId=f"{adapter}:{provider}:{model}:{effort}",
                    )
                resolved, resolved_reason = False, reason or "no model catalog discovery is recorded for this build"
            else:
                entry = catalog.lookup(adapter, provider, model)
                if entry is None or not entry["available"]:
                    if available:
                        raise BoardError(
                            "CATALOG_UNAVAILABLE",
                            f"Cannot publish this profile as available: the discovered harness catalog does not "
                            f"advertise {provider}/{model}.",
                            provider=provider,
                            model=model,
                            catalogSource=catalog.source,
                        )
                    resolved = False
                    resolved_reason = reason or (
                        f"{provider}/{model} is not advertised by the discovered harness catalog"
                    )
                else:
                    legal = catalog.efforts_for(adapter, provider, model)
                    if legal and effort not in legal:
                        raise BoardError(
                            "INVALID_ARGUMENT",
                            f"effort {effort!r} is not a legal option for {provider}; "
                            f"the installed harness advertises {', '.join(legal)}",
                            provider=provider,
                            effort=effort,
                        )
                    resolved = bool(available)
                    resolved_reason = None if resolved else (reason or "disabled by the publisher")
        else:
            # The build knows these adapters, but no catalog verifies their execution
            # identity; the publisher states availability and the flag stays attributed.
            resolved = bool(available)
            resolved_reason = None if resolved else (reason or "disabled by the publisher")
        if enabled and not resolved:
            raise BoardError(
                "INVALID_ARGUMENT",
                "A profile cannot be enabled while it is unavailable; publish it disabled or with a verified "
                "available identity",
                profileId=f"{adapter}:{provider}:{model}:{effort}",
                unavailableReason=resolved_reason,
            )
        return resolved, resolved_reason

    def _validate_profiles(self, connection: sqlite3.Connection, entries: list, catalog: CatalogView | None) -> dict:
        existing = {row["profile_id"]: row for row in connection.execute("SELECT * FROM evaluation_profiles")}
        resolved: dict[str, dict] = {}
        for index, entry in enumerate(entries):
            if not isinstance(entry, dict):
                raise BoardError("INVALID_ARGUMENT", f"profiles[{index}] must be an object")
            schemas.reject_unknown(entry, PROFILE_FIELDS, f"profiles[{index}]")
            profile_id = schemas.required_string(
                entry, "profileId", max_length=128, pattern=schemas.IDENTIFIER_PATTERN
            )
            if profile_id in resolved:
                raise BoardError("INVALID_ARGUMENT", f"profiles[{index}] repeats profileId {profile_id!r}")
            label = schemas.required_string(entry, "label", max_length=200)
            adapter = schemas.required_string(entry, "adapter", max_length=32)
            if adapter not in schemas.ADAPTERS:
                raise BoardError(
                    "UNSUPPORTED_ADAPTER", f"Unknown adapter {adapter!r}; this build supports {', '.join(schemas.ADAPTERS)}"
                )
            provider = schemas.required_string(entry, "provider", max_length=200)
            model = schemas.required_string(entry, "model", max_length=200)
            effort = schemas.required_string(entry, "effort", max_length=64)
            available, enabled = schemas.optional_bool(entry, "available", False), schemas.optional_bool(entry, "enabled", True)
            capabilities = schemas.string_list(entry, "capabilities", limit=MAX_CAPABILITIES)
            context_window = entry.get("contextWindow")
            if context_window is not None and (
                isinstance(context_window, bool) or not isinstance(context_window, int) or not (1 <= context_window <= 100_000_000)
            ):
                raise BoardError("INVALID_ARGUMENT", f"profiles[{index}].contextWindow must be a positive integer or null")
            description = schemas.optional_string(entry, "description", max_length=MAX_DESCRIPTION) or ""
            source = schemas.required_string(entry, "source", max_length=256)
            unavailable_reason = schemas.optional_string(entry, "unavailableReason", max_length=MAX_POINT)
            prior = existing.get(profile_id)
            if prior is not None and (
                prior["adapter"],
                prior["provider"],
                prior["model"],
                prior["effort"],
            ) != (adapter, provider, model, effort):
                raise BoardError(
                    "CONFLICT",
                    f"profile {profile_id} already identifies "
                    f"{prior['adapter']}/{prior['provider']}/{prior['model']}/{prior['effort']}; an execution "
                    "identity is immutable under an existing profileId. Publish a new profileId instead.",
                    profileId=profile_id,
                    existing={"adapter": prior["adapter"], "provider": prior["provider"], "model": prior["model"], "effort": prior["effort"]},
                )
            resolved_available, resolved_reason = self._resolve_availability(
                adapter=adapter,
                provider=provider,
                model=model,
                effort=effort,
                available=available,
                enabled=enabled,
                reason=unavailable_reason,
                catalog=catalog,
            )
            resolved[profile_id] = {
                "profileId": profile_id,
                "label": label,
                "adapter": adapter,
                "provider": provider,
                "model": model,
                "effort": effort,
                "available": resolved_available,
                "enabled": enabled,
                "capabilities": capabilities,
                "contextWindow": context_window,
                "description": description,
                "source": source,
                "unavailableReason": resolved_reason,
            }
        return resolved

    def _validate_cards(self, connection: sqlite3.Connection, entries: list) -> dict:
        resolved: dict[str, dict] = {}
        for index, entry in enumerate(entries):
            if not isinstance(entry, dict):
                raise BoardError("INVALID_ARGUMENT", f"cards[{index}] must be an object")
            schemas.reject_unknown(entry, CARD_FIELDS, f"cards[{index}]")
            profile_id = schemas.required_string(
                entry, "profileId", max_length=128, pattern=schemas.IDENTIFIER_PATTERN
            )
            if profile_id in resolved:
                raise BoardError("INVALID_ARGUMENT", f"cards[{index}] repeats profileId {profile_id!r}")
            summary = schemas.required_string(entry, "summary", max_length=MAX_SUMMARY)
            evidence_ids = schemas.string_list(entry, "evidenceIds", limit=MAX_EVIDENCE_IDS, pattern=schemas.IDENTIFIER_PATTERN)
            if evidence_ids:
                markers = ",".join("?" for _ in evidence_ids)
                rows = connection.execute(
                    f"SELECT evidence_id, profile_id FROM evaluation_evidence WHERE evidence_id IN ({markers})",
                    evidence_ids,
                ).fetchall()
                owners = {row["evidence_id"]: row["profile_id"] for row in rows}
                missing = [value for value in evidence_ids if value not in owners]
                if missing:
                    raise BoardError(
                        "NOT_FOUND", f"cards[{index}] references unknown evidence {missing[0]!r}", evidenceId=missing[0]
                    )
                foreign = [value for value in evidence_ids if owners[value] != profile_id]
                if foreign:
                    raise BoardError(
                        "CONFLICT",
                        f"cards[{index}] references evidence {foreign[0]!r} that belongs to another profile",
                        evidenceId=foreign[0],
                    )
            resolved[profile_id] = {
                "profileId": profile_id,
                "summary": summary,
                "strengths": self._points(entry, "strengths"),
                "limitations": self._points(entry, "limitations"),
                "risks": self._points(entry, "risks"),
                "evidenceIds": evidence_ids,
            }
        return resolved

    def _validate_preferences(self, connection: sqlite3.Connection, entries: list) -> dict:
        resolved: dict[str, dict] = {}
        for index, entry in enumerate(entries):
            if not isinstance(entry, dict):
                raise BoardError("INVALID_ARGUMENT", f"preferences[{index}] must be an object")
            schemas.reject_unknown(entry, PREFERENCE_FIELDS, f"preferences[{index}]")
            profile_id = schemas.required_string(
                entry, "profileId", max_length=128, pattern=schemas.IDENTIFIER_PATTERN
            )
            if profile_id in resolved:
                raise BoardError("INVALID_ARGUMENT", f"preferences[{index}] repeats profileId {profile_id!r}")
            mode = schemas.required_string(entry, "mode", max_length=16)
            if mode not in ("prefer", "pin", "exclude"):
                raise BoardError("INVALID_ARGUMENT", "mode must be 'prefer', 'pin' or 'exclude'")
            reason = schemas.required_string(entry, "reason", max_length=MAX_REASON)
            resolved[profile_id] = {"profileId": profile_id, "mode": mode, "reason": reason}
        return resolved

    @staticmethod
    def _validate_configuration(entry: Any) -> dict:
        if not isinstance(entry, dict):
            raise BoardError("INVALID_ARGUMENT", "configuration must be an object")
        schemas.reject_unknown(entry, CONFIGURATION_FIELDS, "configuration")
        decision_profile = entry.get("decisionProfileId")
        if decision_profile is not None and (
            not isinstance(decision_profile, str) or not decision_profile.strip()
        ):
            raise BoardError("INVALID_ARGUMENT", "configuration.decisionProfileId must be a profileId or null")
        if decision_profile is not None:
            decision_profile = decision_profile.strip()
            if len(decision_profile) > 128 or not schemas.IDENTIFIER_PATTERN.match(decision_profile):
                raise BoardError("INVALID_ARGUMENT", "configuration.decisionProfileId has an invalid format")
        # ``autoMaintain`` is preauthorization for the bounded maintenance policy:
        # it lets a maintenance run adopt a validated card-only proposal. It never
        # authorizes profiles, preferences, configuration or execution authority,
        # and it is not required to *ask* for maintenance — an explicit request
        # still runs the model and retains its proposal for the Host.
        auto_maintain = schemas.optional_bool(entry, "autoMaintain", False)
        return {"decisionProfileId": decision_profile, "autoMaintain": auto_maintain}

    def _check_resulting_state(
        self,
        connection: sqlite3.Connection,
        *,
        profiles: dict | None,
        cards: dict | None,
        preferences: dict | None,
        configuration: dict | None,
    ) -> tuple[dict, dict, dict, dict | None]:
        """Resolve the complete post-publish state and refuse every dangling reference."""
        current_profiles = {row["profile_id"]: row for row in connection.execute("SELECT * FROM evaluation_profiles")}
        current_cards = {row["profile_id"]: row for row in connection.execute("SELECT * FROM evaluation_cards")}
        current_preferences = {
            row["profile_id"]: row for row in connection.execute("SELECT * FROM evaluation_preferences")
        }
        state = self._state(connection)
        if configuration is None:
            current_configuration = {
                "decisionProfileId": state["decision_profile_id"],
                "autoMaintain": bool(state["auto_maintain"]),
            }
        else:
            current_configuration = configuration

        resulting_profiles = set(current_profiles) if profiles is None else set(profiles)
        resulting_cards = current_cards if cards is None else cards
        resulting_preferences = current_preferences if preferences is None else preferences

        for profile_id in resulting_preferences:
            if profile_id not in resulting_profiles:
                raise BoardError(
                    "CONFLICT",
                    f"preference for profile {profile_id!r} would dangle: the profile is not in the resulting table",
                    profileId=profile_id,
                )
            mode = resulting_preferences[profile_id]["mode"]
            if mode == "pin":
                published = (profiles or {}).get(profile_id)
                if published is None:
                    published = current_profiles.get(profile_id)
                if published is None or not published["available"] or not published["enabled"]:
                    raise BoardError(
                        "CONFLICT",
                        f"Cannot pin {profile_id!r}: only an available, enabled profile can constrain the legal "
                        "candidate set. A preference never makes an unverified model/effort legal.",
                        profileId=profile_id,
                    )
        for profile_id in resulting_cards:
            if profile_id not in resulting_profiles:
                raise BoardError(
                    "CONFLICT",
                    f"card for profile {profile_id!r} would dangle: the profile is not in the resulting table",
                    profileId=profile_id,
                )
        decision_profile = current_configuration["decisionProfileId"]
        if decision_profile is not None and decision_profile not in resulting_profiles:
            raise BoardError(
                "CONFLICT",
                f"decision profile {decision_profile!r} would dangle: it is still configured but not in the "
                "resulting table",
                profileId=decision_profile,
            )
        if decision_profile is not None:
            published = (profiles or {}).get(decision_profile) or current_profiles.get(decision_profile)
            if published is None or not published["available"] or not published["enabled"]:
                raise BoardError(
                    "CONFLICT",
                    f"Cannot configure {decision_profile!r} as the decision profile: it must be available and enabled",
                    profileId=decision_profile,
                )
        if profiles is not None:
            for profile_id in set(current_profiles) - resulting_profiles:
                row = connection.execute(
                    "SELECT COUNT(*) AS count FROM evaluation_evidence WHERE profile_id=?", (profile_id,)
                ).fetchone()
                if int(row["count"]):
                    raise BoardError(
                        "CONFLICT",
                        f"profile {profile_id!r} is referenced by recorded evidence and cannot be removed; "
                        "keep the profile or keep its evidence",
                        profileId=profile_id,
                    )
        return profiles or {}, cards or {}, preferences or {}, configuration

    # -- publish -------------------------------------------------------------
    def _publish_revision(
        self,
        connection: sqlite3.Connection,
        *,
        revision: int,
        writer,
        now: str,
        params: dict,
        keep_consumed: frozenset[str] = frozenset(),
    ) -> dict:
        catalog = self._catalog(connection)
        provided = {key for key in ("profiles", "cards", "preferences", "configuration") if key in params}
        for key in provided:
            value = params[key]
            limit = {"profiles": MAX_PROFILES, "cards": MAX_CARDS, "preferences": MAX_PREFERENCES}.get(key)
            if limit is not None and (not isinstance(value, list) or len(value) > limit):
                raise BoardError("INVALID_ARGUMENT", f"{key} must be a list of at most {limit} entries")
        profiles = self._validate_profiles(connection, params["profiles"], catalog) if "profiles" in provided else None
        cards = self._validate_cards(connection, params["cards"]) if "cards" in provided else None
        preferences = (
            self._validate_preferences(connection, params["preferences"]) if "preferences" in provided else None
        )
        configuration = self._validate_configuration(params["configuration"]) if "configuration" in provided else None
        profiles, cards, preferences, configuration = self._check_resulting_state(
            connection, profiles=profiles, cards=cards, preferences=preferences, configuration=configuration
        )
        previous_references = self._card_references(connection) if "cards" in provided else {}

        connection.execute(
            "INSERT INTO evaluation_revisions(revision, kind, writer_id, actor, counts_json, created_at)"
            " VALUES(?,?,?,?,?,?)",
            (
                revision,
                writer["kind"],
                writer["writer_id"],
                params.get("writerId"),
                canonical_json(
                    {"profiles": len(profiles), "cards": len(cards), "preferences": len(preferences), "provided": sorted(provided)}
                ),
                now,
            ),
        )
        if "profiles" in provided:
            for profile_id, entry in profiles.items():
                connection.execute(
                    "INSERT INTO evaluation_profiles(profile_id, label, adapter, provider, model, effort, available,"
                    " enabled, capabilities_json, context_window, description, source, unavailable_reason,"
                    " created_revision, updated_revision) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"
                    " ON CONFLICT(profile_id) DO UPDATE SET label=excluded.label, available=excluded.available,"
                    " enabled=excluded.enabled, capabilities_json=excluded.capabilities_json,"
                    " context_window=excluded.context_window, description=excluded.description,"
                    " source=excluded.source, unavailable_reason=excluded.unavailable_reason,"
                    " updated_revision=excluded.updated_revision",
                    (
                        profile_id,
                        entry["label"],
                        entry["adapter"],
                        entry["provider"],
                        entry["model"],
                        entry["effort"],
                        1 if entry["available"] else 0,
                        1 if entry["enabled"] else 0,
                        canonical_json(entry["capabilities"]),
                        entry["contextWindow"],
                        entry["description"],
                        entry["source"],
                        entry["unavailableReason"],
                        revision,
                        revision,
                    ),
                )
            removed = set(
                row["profile_id"] for row in connection.execute("SELECT profile_id FROM evaluation_profiles")
            ) - set(profiles)
            for profile_id in removed:
                connection.execute("DELETE FROM evaluation_cards WHERE profile_id=?", (profile_id,))
                connection.execute("DELETE FROM evaluation_preferences WHERE profile_id=?", (profile_id,))
                connection.execute("DELETE FROM evaluation_profiles WHERE profile_id=?", (profile_id,))
        if "cards" in provided:
            keep = set(cards)
            for profile_id, entry in cards.items():
                prior = connection.execute(
                    "SELECT * FROM evaluation_cards WHERE profile_id=?", (profile_id,)
                ).fetchone()
                # Counters are code-owned: the number of distinct accepted attempts for
                # this profile, never a model-supplied value and never a count of prose.
                sample_count = self._sample_count(connection, profile_id)
                content = (
                    entry["summary"],
                    entry["strengths"],
                    entry["limitations"],
                    entry["risks"],
                    entry["evidenceIds"],
                )
                if prior is not None and (
                    prior["summary"],
                    json.loads(prior["strengths_json"]),
                    json.loads(prior["limitations_json"]),
                    json.loads(prior["risks_json"]),
                    json.loads(prior["evidence_ids_json"]),
                ) == content and int(prior["sample_count"]) == sample_count:
                    card_revision, updated_at = int(prior["revision"]), prior["updated_at"]
                else:
                    card_revision = 1 if prior is None else int(prior["revision"]) + 1
                    updated_at = now
                    # Archive the changed card under this revision before the bounded
                    # current row moves on, so retiring a reference never loses the
                    # publication's provenance.
                    connection.execute(
                        "INSERT INTO evaluation_card_history(table_revision, profile_id, card_revision, summary,"
                        " strengths_json, limitations_json, risks_json, evidence_ids_json, sample_count, published_at)"
                        " VALUES(?,?,?,?,?,?,?,?,?,?)"
                        " ON CONFLICT(table_revision, profile_id) DO UPDATE SET card_revision=excluded.card_revision,"
                        " summary=excluded.summary, strengths_json=excluded.strengths_json,"
                        " limitations_json=excluded.limitations_json, risks_json=excluded.risks_json,"
                        " evidence_ids_json=excluded.evidence_ids_json, sample_count=excluded.sample_count,"
                        " published_at=excluded.published_at",
                        (
                            revision,
                            profile_id,
                            card_revision,
                            entry["summary"],
                            canonical_json(entry["strengths"]),
                            canonical_json(entry["limitations"]),
                            canonical_json(entry["risks"]),
                            canonical_json(entry["evidenceIds"]),
                            sample_count,
                            now,
                        ),
                    )
                connection.execute(
                    "INSERT INTO evaluation_cards(profile_id, revision, summary, strengths_json, limitations_json,"
                    " risks_json, evidence_ids_json, sample_count, updated_at) VALUES(?,?,?,?,?,?,?,?,?)"
                    " ON CONFLICT(profile_id) DO UPDATE SET revision=excluded.revision, summary=excluded.summary,"
                    " strengths_json=excluded.strengths_json, limitations_json=excluded.limitations_json,"
                    " risks_json=excluded.risks_json, evidence_ids_json=excluded.evidence_ids_json,"
                    " sample_count=excluded.sample_count, updated_at=excluded.updated_at",
                    (
                        profile_id,
                        card_revision,
                        entry["summary"],
                        canonical_json(entry["strengths"]),
                        canonical_json(entry["limitations"]),
                        canonical_json(entry["risks"]),
                        canonical_json(entry["evidenceIds"]),
                        sample_count,
                        updated_at,
                    ),
                )
            for row in connection.execute("SELECT profile_id FROM evaluation_cards").fetchall():
                if row["profile_id"] not in keep:
                    connection.execute("DELETE FROM evaluation_cards WHERE profile_id=?", (row["profile_id"],))
            self._sync_pending(
                connection,
                previous=previous_references,
                resulting={profile_id: entry["evidenceIds"] for profile_id, entry in cards.items()},
                keep_consumed=keep_consumed,
            )
        if "preferences" in provided:
            for profile_id, entry in preferences.items():
                connection.execute(
                    "INSERT INTO evaluation_preferences(profile_id, mode, reason, updated_revision) VALUES(?,?,?,?)"
                    " ON CONFLICT(profile_id) DO UPDATE SET mode=excluded.mode, reason=excluded.reason,"
                    " updated_revision=excluded.updated_revision",
                    (profile_id, entry["mode"], entry["reason"], revision),
                )
            for row in connection.execute("SELECT profile_id FROM evaluation_preferences").fetchall():
                if row["profile_id"] not in preferences:
                    connection.execute("DELETE FROM evaluation_preferences WHERE profile_id=?", (row["profile_id"],))
        configuration_revision = int(self._state(connection)["configuration_revision"])
        if "configuration" in provided:
            configuration_revision += 1
            connection.execute(
                "UPDATE evaluation_state SET decision_profile_id=?, auto_maintain=?, configuration_revision=?,"
                " updated_at=? WHERE id=1",
                (
                    configuration["decisionProfileId"],
                    1 if configuration["autoMaintain"] else 0,
                    configuration_revision,
                    now,
                ),
            )
        connection.execute(
            "UPDATE evaluation_state SET table_revision=?, updated_at=? WHERE id=1", (revision, now)
        )
        return {
            "revision": revision,
            "configurationRevision": configuration_revision,
            "counts": {
                "profiles": int(
                    connection.execute("SELECT COUNT(*) AS count FROM evaluation_profiles").fetchone()["count"]
                ),
                "cards": int(connection.execute("SELECT COUNT(*) AS count FROM evaluation_cards").fetchone()["count"]),
                "preferences": int(
                    connection.execute("SELECT COUNT(*) AS count FROM evaluation_preferences").fetchone()["count"]
                ),
                "provided": sorted(provided),
            },
        }

    # -- operations ----------------------------------------------------------
    #: Fixed-size counter names in ``evaluation_aggregates``. Every mutation below
    #: adjusts the counter by the checked rowcount of the statement that changed the
    #: ledger, so a read is one primary-key row and never a recount of history.
    PENDING_COUNTER = "pending-evidence"

    @staticmethod
    def _sample_counter(profile_id: str) -> str:
        return f"samples:{profile_id}"

    @staticmethod
    def _counter(connection: sqlite3.Connection, name: str) -> int:
        row = connection.execute("SELECT value FROM evaluation_aggregates WHERE name=?", (name,)).fetchone()
        return int(row["value"]) if row is not None else 0

    def _bump(self, connection: sqlite3.Connection, name: str, delta: int, now: str) -> None:
        if delta == 0:
            return
        connection.execute(
            "INSERT INTO evaluation_aggregates(name, value, updated_at) VALUES(?,?,?)"
            " ON CONFLICT(name) DO UPDATE SET value=MAX(0, value + ?), updated_at=excluded.updated_at",
            (name, max(0, delta), now, delta),
        )

    def _set_counter(self, connection: sqlite3.Connection, name: str, value: int, now: str) -> None:
        connection.execute(
            "INSERT INTO evaluation_aggregates(name, value, updated_at) VALUES(?,?,?)"
            " ON CONFLICT(name) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
            (name, max(0, int(value)), now),
        )

    def _mark_pending(self, connection: sqlite3.Connection, evidence_id: str) -> None:
        row = connection.execute(
            "SELECT profile_id, created_at FROM evaluation_evidence WHERE evidence_id=?", (evidence_id,)
        ).fetchone()
        now = self._now()
        if row is None:
            removed = connection.execute(
                "DELETE FROM evaluation_evidence_pending WHERE evidence_id=?", (evidence_id,)
            ).rowcount
            self._bump(connection, self.PENDING_COUNTER, -int(removed), now)
            return
        added = connection.execute(
            "INSERT OR IGNORE INTO evaluation_evidence_pending(evidence_id, profile_id, created_at) VALUES(?,?,?)",
            (evidence_id, row["profile_id"], row["created_at"]),
        ).rowcount
        self._bump(connection, self.PENDING_COUNTER, int(added), now)

    def _sync_pending(
        self,
        connection: sqlite3.Connection,
        *,
        previous: dict[str, list[str]],
        resulting: dict[str, list[str]],
        keep_consumed: frozenset[str] = frozenset(),
    ) -> None:
        """Apply one publish's card-reference delta to the pending ledger.

        ``previous`` is the evidence-reference map of the cards that were published
        before this revision, ``resulting`` the map after it. Only the difference is
        touched: evidence that stays referenced stays consumed, evidence that gains a
        reference stops being pending, and evidence whose reference disappears is
        pending again — unless it was retired by a *safe automatic compaction*
        (``keep_consumed``), which keeps it incorporated and archived without
        pretending it was never processed. Evidence no card ever referenced is never
        touched, so it stays pending across unrelated revisions.
        """
        was = {evidence for values in previous.values() for evidence in values}
        now = {evidence for values in resulting.values() for evidence in values}
        if now:
            markers = ",".join("?" for _ in now)
            removed = connection.execute(
                f"DELETE FROM evaluation_evidence_pending WHERE evidence_id IN ({markers})", tuple(sorted(now))
            ).rowcount
            self._bump(connection, self.PENDING_COUNTER, -int(removed), self._now())
        for evidence_id in sorted(was - now - set(keep_consumed)):
            self._mark_pending(connection, evidence_id)

    @staticmethod
    def _card_references(connection: sqlite3.Connection) -> dict[str, list[str]]:
        return {
            row["profile_id"]: json.loads(row["evidence_ids_json"])
            for row in connection.execute("SELECT profile_id, evidence_ids_json FROM evaluation_cards")
        }


    def refresh_task_evidence(self, connection: sqlite3.Connection, task: sqlite3.Row, now: str) -> int:
        """Re-derive verification for every report frozen on one accepted attempt.

        A report captures the immutable attempt reference when it is recorded; the
        Host's acceptance/rejection verdict can arrive afterwards. Re-deriving the
        flags here — inside the acknowledgement transaction — is what makes a
        reviewed attempt count without rewriting any report, and what keeps an
        unreviewed process out of the sample set.
        """
        rows = connection.execute("SELECT * FROM evaluation_evidence WHERE run_id=?", (task["task_id"],)).fetchall()
        if not rows:
            return 0
        attempt = self.board._selected_attempt(connection, task)
        refreshed = 0
        for row in rows:
            identity = json.loads(row["identity_json"]) if row["identity_json"] else {}
            if identity.get("attemptId") != (attempt["attempt_id"] if attempt else None):
                # A report frozen on another attempt generation keeps its verdict.
                continue
            profile = connection.execute(
                "SELECT * FROM evaluation_profiles WHERE profile_id=?", (row["profile_id"],)
            ).fetchone()
            if profile is None:
                continue
            identity = self._attempt_identity(connection, task, attempt)
            verified, counted, reason, basis = self._classify_evidence(row["kind"], identity, profile)
            frozen = canonical_json({**identity, "basis": basis})
            connection.execute(
                "UPDATE evaluation_evidence SET verified=?, counted=?, identity_json=? WHERE evidence_id=?",
                (1 if verified else 0, 1 if counted else 0, frozen, row["evidence_id"]),
            )
            self._record_sample(connection, row["profile_id"], identity, counted, row["evidence_id"], now, basis)
            refreshed += 1
        return refreshed

    def _record_sample(
        self,
        connection: sqlite3.Connection,
        profile_id: str,
        identity: dict,
        counted: bool,
        evidence_id: str,
        now: str,
        basis: dict,
    ) -> None:
        """Keep exactly one sample per (profile, attempt), keyed by attempt identity."""
        attempt_id = identity.get("attemptId")
        if not attempt_id:
            return
        counter = self._sample_counter(profile_id)
        if counted:
            added = connection.execute(
                "INSERT OR IGNORE INTO evaluation_samples(profile_id, attempt_id, task_id, verdict, evidence_id,"
                " created_at) VALUES(?,?,?,?,?,?)",
                (
                    profile_id,
                    attempt_id,
                    identity.get("taskId") or "",
                    basis.get("source") or "accepted",
                    evidence_id,
                    now,
                ),
            ).rowcount
            self._bump(connection, counter, int(added), now)
            return
        # Verification may regress (for example a verdict is not yet recorded); the
        # sample disappears only when no counted report for that attempt remains.
        removed = connection.execute(
            "DELETE FROM evaluation_samples WHERE profile_id=? AND attempt_id=? AND NOT EXISTS ("
            " SELECT 1 FROM evaluation_evidence e WHERE e.profile_id=? AND e.counted=1"
            " AND json_extract(e.identity_json, '$.attemptId') = ?)",
            (profile_id, attempt_id, profile_id, attempt_id),
        ).rowcount
        self._bump(connection, counter, -int(removed), now)

    @staticmethod
    def _pending_evidence(connection: sqlite3.Connection) -> int:
        """Evidence not yet incorporated into a currently published card.

        Pending is a property of actual consumption, not of the revision window: a
        publish that only changes preferences or configuration never clears it, while
        evidence referenced by any published card is no longer waiting for
        maintenance. Evidence whose reference disappears when a card is replaced
        becomes visible as pending again, which is exactly what the operator must
        decide about.

        The durable ``evaluation_evidence_pending`` ledger records *which* evidence is
        pending; this counter is maintained transactionally by the same insert and
        delete statements, so an ordinary console refresh or route admission reads one
        fixed-size row instead of scanning the archive.
        """
        row = connection.execute(
            "SELECT value FROM evaluation_aggregates WHERE name=?", (EvaluationStore.PENDING_COUNTER,)
        ).fetchone()
        return int(row["value"]) if row is not None else 0

    @staticmethod
    def _sample_count(connection: sqlite3.Connection, profile_id: str) -> int:
        row = connection.execute(
            "SELECT value FROM evaluation_aggregates WHERE name=?",
            (EvaluationStore._sample_counter(profile_id),),
        ).fetchone()
        return int(row["value"]) if row is not None else 0

    def snapshot(self, params: dict) -> dict:
        schemas.reject_unknown(params, set(), "console.snapshot")
        with self.board.db.read() as connection:
            now = self._now()
            state = self._state(connection)
            table_revision = int(state["table_revision"])
            profiles = [
                self._profile_view(row)
                for row in connection.execute("SELECT * FROM evaluation_profiles ORDER BY rowid")
            ]
            cards = [self._card_view(row) for row in connection.execute("SELECT * FROM evaluation_cards ORDER BY rowid")]
            preferences = [
                {"profileId": row["profile_id"], "mode": row["mode"], "reason": row["reason"]}
                for row in connection.execute("SELECT * FROM evaluation_preferences ORDER BY rowid")
            ]
            evidence = [
                self._evidence_view(row)
                for row in connection.execute(
                    "SELECT * FROM evaluation_evidence ORDER BY created_at DESC, evidence_id DESC LIMIT ?",
                    (MAX_EVIDENCE_PAGE,),
                )
            ]
            decisions = [
                self._decision_view(row)
                for row in connection.execute(
                    "SELECT d.*, r.kind AS kind, r.task_id AS run_id, r.updated_at AS updated_at"
                    " FROM evaluation_decisions d LEFT JOIN decision_requests r ON r.decision_id = d.decision_id"
                    " ORDER BY d.created_at DESC, d.decision_id DESC LIMIT ?",
                    (MAX_DECISION_PAGE,),
                )
            ]
            pending = self._pending_evidence(connection)
            gate = self._gate_view(connection, now)
        tasks = self.board.task_list({"limit": 100, "offset": 0})
        return {
            "csrfToken": "",
            "tableRevision": table_revision,
            "gate": gate,
            "configuration": {
                "revision": int(state["configuration_revision"]),
                "decisionProfileId": state["decision_profile_id"],
                "autoMaintain": bool(state["auto_maintain"]),
            },
            "profiles": profiles,
            "preferences": preferences,
            "cards": cards,
            "evidence": evidence,
            "decisions": decisions,
            "pendingEvidence": pending,
            "tasks": {"runs": tasks["runs"], "total": int(tasks["total"])},
            "capabilities": self.capabilities(),
        }

    def capabilities(self) -> dict:
        """Real implemented availability only.

        ``selection`` and ``maintenance`` are true exactly while the bounded decision
        adapter exists in this build (a helper entrypoint plus Node): when it is
        absent, a decision request reports an honest adapter-unavailable outcome
        instead of pretending a model ran. ``modelCatalogDiscovery`` reports that the
        installed-harness discovery helper is present, not that a discovery
        succeeded; a successful discovery is visible through ``model_catalog_refresh``.
        """
        from . import catalog
        from .adapters.decision import DecisionAdapter

        decision = DecisionAdapter()
        adapter_available, adapter_reason = decision.available()
        return {
            "selection": bool(adapter_available),
            "maintenance": bool(adapter_available),
            "decisionAdapter": bool(adapter_available),
            "decisionAdapterReason": adapter_reason,
            "evaluationWriteGate": True,
            "readerAdmission": True,
            "evidenceRecord": True,
            "modelCatalogDiscovery": bool(catalog.discovery_available()),
            "taskControl": True,
        }

    def write_begin(self, params: dict) -> dict:
        schemas.reject_unknown(params, {"requestId", "expectedRevision", "kind"}, "evaluation.write.begin")
        request_id = schemas.required_string(
            params, "requestId", max_length=128, pattern=schemas.IDENTIFIER_PATTERN
        )
        expected = self._required_revision(params, "expectedRevision")
        kind = schemas.optional_string(params, "kind", max_length=16) or "human"
        if kind not in WRITER_KINDS:
            raise BoardError("INVALID_ARGUMENT", "kind must be 'human' or 'maintenance'")
        with self.board.db.write() as connection:
            now = self._now()
            self._sweep(connection, now)
            state = self._state(connection)
            table_revision = int(state["table_revision"])
            writer = connection.execute(
                "SELECT * FROM evaluation_writers WHERE request_id=?", (request_id,)
            ).fetchone()
            event: str | None
            if writer is not None:
                if writer["kind"] != kind or int(writer["expected_revision"]) != expected:
                    raise BoardError(
                        "CONFLICT",
                        "This requestId already belongs to a different writer intent; use a new requestId",
                        requestId=request_id,
                    )
                if writer["state"] == "published":
                    raise BoardError(
                        "ALREADY_PUBLISHED",
                        "This writer intent already published a revision; begin a new write with a new requestId",
                        writerId=writer["writer_id"],
                        tableRevision=table_revision,
                    )
                if writer["state"] in ("aborted", "expired"):
                    connection.execute(
                        "UPDATE evaluation_writers SET state='waiting', granted_at=NULL, expires_at=?, released_at=NULL"
                        " WHERE writer_id=?",
                        (self._plus(self.writer_queue_seconds, now), writer["writer_id"]),
                    )
                    event = "evaluation.writer_revived"
                else:
                    event = None
            else:
                sequence = int(state["writer_sequence"]) + 1
                writer_id = str(uuid.uuid4())
                token = self.db.writer_token(writer_id, sequence)
                connection.execute(
                    "INSERT INTO evaluation_writers(writer_id, request_id, kind, state, generation,"
                    " expected_revision, token_verifier, requested_at, granted_at, expires_at, released_at)"
                    " VALUES(?,?,?,?,?,?,?,?,?,?,NULL)",
                    (
                        writer_id,
                        request_id,
                        kind,
                        "waiting",
                        sequence,
                        expected,
                        self.db.writer_token_verifier(token),
                        now,
                        None,
                        self._plus(self.writer_queue_seconds, now),
                    ),
                )
                connection.execute(
                    "UPDATE evaluation_state SET writer_sequence=?, updated_at=? WHERE id=1", (sequence, now)
                )
                event = "evaluation.writer_queued"
            self._promote(connection, now)
            writer = connection.execute(
                "SELECT * FROM evaluation_writers WHERE request_id=?", (request_id,)
            ).fetchone()
            if event is not None:
                self.board._append_event(
                    connection,
                    event,
                    revision=table_revision,
                    payload={"writerId": writer["writer_id"], "generation": int(writer["generation"]), "kind": kind},
                )
            gate = self._gate_view(connection, now)
            head = self.board._head_of(connection)
        self.board._notify(head)
        waiting_position = 0
        if writer["state"] == "waiting":
            with self.board.db.read() as connection:
                row = connection.execute(
                    "SELECT COUNT(*) AS count FROM evaluation_writers WHERE state='waiting' AND generation <= ?",
                    (writer["generation"],),
                ).fetchone()
            waiting_position = int(row["count"])
        return {
            "writerId": writer["writer_id"],
            "generation": int(writer["generation"]),
            "writerToken": self.db.writer_token(writer["writer_id"], int(writer["generation"])),
            "state": writer["state"],
            "phase": gate["phase"],
            "expiresAt": writer["expires_at"],
            "tableRevision": table_revision,
            "waitingWriters": gate["waitingWriters"],
            "queuePosition": waiting_position,
        }

    def _writer_from_params(self, connection: sqlite3.Connection, params: dict) -> sqlite3.Row:
        writer_id = schemas.required_string(params, "writerId", max_length=128)
        generation = params.get("generation")
        if isinstance(generation, bool) or not isinstance(generation, int):
            raise BoardError("INVALID_ARGUMENT", "generation must be an integer")
        writer = connection.execute("SELECT * FROM evaluation_writers WHERE writer_id=?", (writer_id,)).fetchone()
        if writer is None:
            raise BoardError("NOT_FOUND", "Unknown writerId", writerId=writer_id)
        if int(writer["generation"]) != int(generation):
            raise BoardError(
                "STALE_GENERATION",
                "This writer intent was superseded; only its own generation may mutate the table",
                writerId=writer_id,
                currentGeneration=int(writer["generation"]),
                providedGeneration=int(generation),
            )
        token = schemas.required_string(params, "writerToken", max_length=256)
        if not _constant_time_equal(self.db.writer_token_verifier(token), writer["token_verifier"]):
            raise BoardError("UNAUTHORIZED", "Invalid writer token for this intent", writerId=writer_id)
        return writer

    def write_renew(self, params: dict) -> dict:
        schemas.reject_unknown(params, {"writerId", "generation", "writerToken"}, "evaluation.write.renew")
        with self.board.db.write() as connection:
            now = self._now()
            self._sweep(connection, now)
            writer = self._writer_from_params(connection, params)
            if writer["state"] == "active":
                connection.execute(
                    "UPDATE evaluation_writers SET expires_at=? WHERE writer_id=?",
                    (self._plus(self.writer_lease_seconds, now), writer["writer_id"]),
                )
                event = "evaluation.writer_renewed"
            elif writer["state"] == "waiting":
                connection.execute(
                    "UPDATE evaluation_writers SET expires_at=? WHERE writer_id=?",
                    (self._plus(self.writer_queue_seconds, now), writer["writer_id"]),
                )
                event = "evaluation.writer_renewed"
            else:
                raise BoardError(
                    "WRITER_NOT_ACTIVE",
                    f"This writer intent is {writer['state']} and can no longer renew or publish; begin a new write",
                    writerId=writer["writer_id"],
                    state=writer["state"],
                )
            self._promote(connection, now)
            self.board._append_event(
                connection,
                event,
                revision=int(self._state(connection)["table_revision"]),
                payload={"writerId": writer["writer_id"], "generation": int(writer["generation"])},
            )
            writer = connection.execute(
                "SELECT * FROM evaluation_writers WHERE writer_id=?", (writer["writer_id"],)
            ).fetchone()
            gate = self._gate_view(connection, now)
            table_revision = int(self._state(connection)["table_revision"])
            head = self.board._head_of(connection)
        self.board._notify(head)
        return {
            "writerId": writer["writer_id"],
            "generation": int(writer["generation"]),
            "state": writer["state"],
            "phase": gate["phase"],
            "expiresAt": writer["expires_at"],
            "tableRevision": table_revision,
            "waitingWriters": gate["waitingWriters"],
        }

    def write_publish(self, params: dict) -> dict:
        schemas.reject_unknown(params, PUBLISH_FIELDS, "evaluation.write.publish")
        command_id = schemas.required_string(params, "commandId", max_length=128)
        # Required fields are checked *before* the idempotency lookup, and the request
        # fingerprint is built from exactly the fields the caller provided: an omitted
        # collection and an explicit null are different requests, so a stored receipt
        # can never satisfy a changed or invalid command.
        for name in PUBLISH_REQUEST_FIELDS[:4]:
            if name not in params:
                raise BoardError("INVALID_ARGUMENT", f"{name} is required")
        request = {key: params[key] for key in PUBLISH_REQUEST_FIELDS if key in params}
        with self.board.db.write() as connection:
            receipt = self.board._receipt(connection, command_id, "evaluation.publish", request)
            if receipt is not None:
                return {**receipt, "duplicate": True}
            now = self._now()
            self._sweep(connection, now)
            state = self._state(connection)
            table_revision = int(state["table_revision"])
            writer = self._writer_from_params(connection, params)
            if writer["state"] != "active":
                raise BoardError(
                    "WRITER_NOT_ACTIVE",
                    f"This writer intent is {writer['state']}; a late publication is rejected and the last complete "
                    "revision stays intact",
                    writerId=writer["writer_id"],
                    state=writer["state"],
                )
            expected = self._required_revision(params, "expectedRevision")
            if expected != table_revision:
                raise BoardError(
                    "REVISION_CONFLICT",
                    "The table changed since this write began; re-read the current revision and publish again",
                    expectedRevision=expected,
                    currentRevision=table_revision,
                )
            published = self._publish_revision(
                connection, revision=table_revision + 1, writer=writer, now=now, params=params
            )
            connection.execute(
                "UPDATE evaluation_writers SET state='published', released_at=? WHERE writer_id=?",
                (now, writer["writer_id"]),
            )
            self.board._append_event(
                connection,
                "evaluation.published",
                revision=published["revision"],
                payload={
                    "writerId": writer["writer_id"],
                    "generation": int(writer["generation"]),
                    "kind": writer["kind"],
                    "counts": published["counts"],
                },
            )
            self._promote(connection, now)
            gate = self._gate_view(connection, now)
            response = {
                "published": True,
                "writerId": writer["writer_id"],
                "generation": int(writer["generation"]),
                "revision": published["revision"],
                "tableRevision": published["revision"],
                "configurationRevision": published["configurationRevision"],
                "counts": published["counts"],
                "gate": gate,
                "duplicate": False,
            }
            self.board._store_receipt(
                connection, command_id, "evaluation.publish", request, response
            )
            head = self.board._head_of(connection)
        self.board._notify(head)
        return response

    def write_abort(self, params: dict) -> dict:
        schemas.reject_unknown(
            params, {"commandId", "writerId", "generation", "writerToken"}, "evaluation.write.abort"
        )
        command_id = schemas.required_string(params, "commandId", max_length=128)
        for name in ("writerId", "generation", "writerToken"):
            if name not in params:
                raise BoardError("INVALID_ARGUMENT", f"{name} is required")
        request = {key: params[key] for key in ("writerId", "generation", "writerToken") if key in params}
        with self.board.db.write() as connection:
            receipt = self.board._receipt(connection, command_id, "evaluation.abort", request)
            if receipt is not None:
                return {**receipt, "duplicate": True}
            now = self._now()
            self._sweep(connection, now)
            writer = self._writer_from_params(connection, params)
            if writer["state"] == "published":
                raise BoardError(
                    "CONFLICT",
                    "This writer intent already published a revision; an abort cannot undo a committed revision",
                    writerId=writer["writer_id"],
                )
            already = writer["state"] in ("aborted", "expired")
            if not already:
                connection.execute(
                    "UPDATE evaluation_writers SET state='aborted', released_at=? WHERE writer_id=?",
                    (now, writer["writer_id"]),
                )
            self._promote(connection, now)
            if not already:
                self.board._append_event(
                    connection,
                    "evaluation.writer_aborted",
                    revision=int(self._state(connection)["table_revision"]),
                    payload={"writerId": writer["writer_id"], "generation": int(writer["generation"])},
                )
            gate = self._gate_view(connection, now)
            response = {
                "writerId": writer["writer_id"],
                "generation": int(writer["generation"]),
                "state": "aborted" if not already else writer["state"],
                "aborted": True,
                "alreadyTerminal": already,
                "tableRevision": int(self._state(connection)["table_revision"]),
                "gate": gate,
                "note": (
                    "Writer authority was released. This is not evidence that any model or process stopped, and it "
                    "never rewrites the last committed revision."
                ),
            }
            self.board._store_receipt(connection, command_id, "evaluation.abort", request, response)
            head = self.board._head_of(connection)
        self.board._notify(head)
        return response

    def reader_begin(self, params: dict) -> dict:
        schemas.reject_unknown(params, {"kind", "revision"}, "evaluation.reader.begin")
        kind = schemas.optional_string(params, "kind", max_length=32) or "selection"
        if kind not in READER_KINDS:
            raise BoardError(
                "INVALID_ARGUMENT",
                "Only a bounded 'selection' read is an evaluation reader. A viewer refresh and an already-started "
                "business task are deliberately not readers and never delay a writer.",
            )
        with self.board.db.write() as connection:
            now = self._now()
            self._sweep(connection, now)
            state = self._state(connection)
            table_revision = int(state["table_revision"])
            pending = connection.execute(
                "SELECT COUNT(*) AS count FROM evaluation_writers WHERE state IN ('waiting','active')"
            ).fetchone()
            if int(pending["count"]):
                raise BoardError(
                    "TABLE_BUSY",
                    "A writer intent is already queued for the evaluation table; new selection readers are not "
                    "admitted until it publishes. Retry after the gate reopens.",
                    tableRevision=table_revision,
                )
            revision = params.get("revision")
            if revision is not None:
                if isinstance(revision, bool) or not isinstance(revision, int) or revision < 0:
                    raise BoardError("INVALID_ARGUMENT", "revision must be a nonnegative integer")
                if int(revision) != table_revision:
                    raise BoardError(
                        "REVISION_CONFLICT",
                        "The published table moved; re-read the snapshot and read the identified revision",
                        expectedRevision=int(revision),
                        currentRevision=table_revision,
                    )
            reader_id = str(uuid.uuid4())
            admitted_at = now
            expires_at = self._plus(self.reader_lease_seconds, now)
            connection.execute(
                "INSERT INTO evaluation_readers(reader_id, kind, admitted_at, expires_at, released_at, expired)"
                " VALUES(?,?,?,?,NULL,0)",
                (reader_id, kind, admitted_at, expires_at),
            )
            self.board._append_event(
                connection,
                "evaluation.reader_admitted",
                revision=table_revision,
                payload={"readerId": reader_id, "kind": kind, "expiresAt": expires_at},
            )
            gate = self._gate_view(connection, now)
            head = self.board._head_of(connection)
        self.board._notify(head)
        return {
            "readerId": reader_id,
            "kind": kind,
            "revision": table_revision,
            "admittedAt": admitted_at,
            "expiresAt": expires_at,
            "readers": gate["readers"],
            "phase": gate["phase"],
        }

    def reader_release(self, params: dict) -> dict:
        schemas.reject_unknown(params, {"readerId"}, "evaluation.reader.release")
        reader_id = schemas.required_string(params, "readerId", max_length=128)
        with self.board.db.write() as connection:
            now = self._now()
            self._sweep(connection, now)
            row = connection.execute("SELECT * FROM evaluation_readers WHERE reader_id=?", (reader_id,)).fetchone()
            if row is None:
                raise BoardError("NOT_FOUND", "Unknown readerId", readerId=reader_id)
            released = row["released_at"] is not None
            if not released:
                connection.execute(
                    "UPDATE evaluation_readers SET released_at=? WHERE reader_id=?", (now, reader_id)
                )
                self.board._append_event(
                    connection,
                    "evaluation.reader_released",
                    revision=int(self._state(connection)["table_revision"]),
                    payload={"readerId": reader_id, "kind": row["kind"], "expired": False},
                )
            self._promote(connection, now)
            gate = self._gate_view(connection, now)
            table_revision = int(self._state(connection)["table_revision"])
            head = self.board._head_of(connection)
        self.board._notify(head)
        return {
            "readerId": reader_id,
            "released": not released,
            "alreadyReleased": released,
            "readers": gate["readers"],
            "phase": gate["phase"],
            "tableRevision": table_revision,
        }

    # -- evidence ------------------------------------------------------------
    @staticmethod
    def _evidence_id(profile_id: str, kind: str, summary: str, project: str | None, source: str, run_id: str | None, conditions: list[str]) -> str:
        identity = canonical_json(
            {
                "profileId": profile_id,
                "kind": kind,
                "summary": summary,
                "project": project,
                "source": source,
                "runId": run_id,
                "conditions": conditions,
            }
        )
        return "ev-" + sha256_text(identity)[:32]

    def _task_identity(self, connection: sqlite3.Connection, run_id: str) -> dict:
        """Freeze what the durable record says about one run *at recording time*.

        The selected attempt is read once, here, and its identity is stored with the
        report. A later retry creates a new generation and therefore a new attempt
        reference; it can never relabel evidence about the attempt that actually ran.
        """
        task = connection.execute("SELECT * FROM tasks WHERE task_id=?", (run_id,)).fetchone()
        if task is None:
            raise BoardError("NOT_FOUND", "Unknown runId for this evidence", runId=run_id)
        attempt = self.board._selected_attempt(connection, task)
        return self._attempt_identity(connection, task, attempt)

    def _attempt_identity(self, connection: sqlite3.Connection, task: sqlite3.Row, attempt: sqlite3.Row | None) -> dict:
        result = json.loads(attempt["result_json"]) if attempt is not None and attempt["result_json"] else None
        report = result.get("result") if isinstance(result, dict) and isinstance(result.get("result"), dict) else {}
        spec = json.loads(task["spec_json"])
        adapter = task["adapter"]
        if attempt is not None:
            turn = connection.execute(
                "SELECT input_json FROM workflow_turns WHERE attempt_id=?", (attempt["attempt_id"],),
            ).fetchone()
            if turn is not None:
                # A later configuration choice must never relabel an earlier attempt.
                # Missing frozen identity stays unknown; the current run is not evidence.
                frozen = json.loads(turn["input_json"]).get("context", {}).get("executionConfiguration")
                spec = frozen if isinstance(frozen, dict) else {}
                adapter = spec.get("adapter", adapter)
        return {
            "taskId": task["task_id"],
            "attemptId": attempt["attempt_id"] if attempt is not None else None,
            "generation": int(attempt["generation"]) if attempt is not None else None,
            "taskState": task["state"],
            "resultStatus": (result or {}).get("status"),
            "shutdownConfirmed": bool(attempt["shutdown_confirmed"]) if attempt is not None else False,
            "cancelRequested": bool(attempt["cancel_requested_at"]) if attempt is not None else False,
            "error": (result or {}).get("error"),
            "exitCode": (result or {}).get("exitCode"),
            "acceptedAt": task["accepted_at"],
            "acceptanceNote": task["acceptance_note"],
            "acceptanceVerdict": task["acceptance_verdict"],
            "runtimeIdentity": attempt["runtime_identity"] if attempt is not None else None,
            "adapter": adapter,
            "requested": EvaluationStore._requested_identity(adapter, spec, report),
            "resolved": EvaluationStore._reported_identity(adapter, report.get("resolved")),
            "observed": EvaluationStore._reported_identity(adapter, report.get("observed")),
        }

    @staticmethod
    def _identity_tuple(adapter: str, provider: Any, model: Any, effort: Any) -> dict | None:
        """One known execution identity, or ``None`` when any part of it is unknown."""
        if not isinstance(provider, str) or not provider.strip():
            return None
        if not isinstance(model, str) or not model.strip():
            return None
        return {
            "adapter": adapter,
            "provider": provider.strip(),
            "model": model.strip(),
            "effort": (effort.strip() if isinstance(effort, str) else ""),
        }

    @staticmethod
    def _requested_identity(adapter: str, spec: dict, report: dict) -> dict | None:
        """The identity this run actually asked for.

        The stored specification is the request; when the adapter reports its own
        requested configuration it must agree with the specification, otherwise the
        run's real request is unknown and stays unknown instead of being guessed. A
        reported configuration is still a *requested* identity: the served model is
        never inferred from it.
        """
        stored = EvaluationStore._identity_tuple(
            adapter, spec.get("provider"), spec.get("model"), spec.get("effort")
        )
        reported = report.get("requested") if isinstance(report, dict) else None
        if isinstance(reported, dict):
            live = EvaluationStore._identity_tuple(
                adapter,
                reported.get("provider"),
                reported.get("model"),
                reported.get("reasoningEffort", reported.get("effort")),
            )
            if stored is not None and live is not None and stored != live:
                return None
            return live or stored
        return stored

    @staticmethod
    def _reported_identity(adapter: str, value: Any) -> dict | None:
        if not isinstance(value, dict):
            return None
        return EvaluationStore._identity_tuple(
            adapter, value.get("provider"), value.get("model"), value.get("reasoningEffort", value.get("effort"))
        )

    @staticmethod
    def _matches_profile(identity: dict | None, profile: sqlite3.Row) -> bool:
        if identity is None:
            return False
        return (
            identity["adapter"] == profile["adapter"]
            and identity["provider"] == profile["provider"]
            and identity["model"] == profile["model"]
            and identity["effort"] == (profile["effort"] or "")
        )

    @staticmethod
    def _infrastructure_failure(identity: dict) -> bool:
        """True when the failure names the harness, adapter or environment.

        Cancelled work, an unusable adapter, a runner that produced no result and a
        worker-level crash are not evidence about a model's capability, so they are
        never counted as capability failures.
        """
        if identity.get("resultStatus") == "invalid-result":
            return True
        text = identity.get("error")
        if not isinstance(text, str):
            return False
        return any(marker in text for marker in INFRASTRUCTURE_FAILURE_MARKERS)

    def _sample_basis(self, identity: dict, profile: sqlite3.Row, *, source: str) -> tuple[bool, dict]:
        """Whether one accepted/rejected attempt is a verified sample of this profile."""
        basis: dict = {"source": source, "profileMatch": False}
        if identity.get("taskState") == "reconciliation-needed" or not identity.get("shutdownConfirmed"):
            basis["excluded"] = "shutdown-unconfirmed"
            return (
                False,
                basis | {
                    "reason": "not counted: the attempt's shutdown is unconfirmed, so nothing can be attributed "
                    "to a model",
                },
            )
        if self._infrastructure_failure(identity):
            basis["excluded"] = "infrastructure"
            return (
                False,
                basis | {
                    "reason": "not counted: an environment, adapter or infrastructure failure is not a "
                    "model-capability result",
                },
            )
        requested, resolved, observed = identity.get("requested"), identity.get("resolved"), identity.get("observed")
        if requested is None:
            basis["reason"] = "the run records no requested model identity"
            return (
                False,
                basis | {
                    "reason": "not counted: the run records no requested model/provider/effort identity, so the "
                    "profile this result belongs to is unknown",
                },
            )
        if not self._matches_profile(requested, profile):
            basis["reason"] = "the requested identity does not match this profile"
            return (
                False,
                basis | {
                    "reason": "not counted: the run requested "
                    f"{requested['adapter']}/{requested['provider']}/{requested['model']}/{requested['effort']}, "
                    "which is not this profile",
                },
            )
        for name, value in (("resolved", resolved), ("observed", observed)):
            if value is not None and not self._matches_profile(value, profile):
                basis["reason"] = f"the {name} identity does not match this profile"
                return (
                    False,
                    basis | {
                        "reason": f"not counted: the run's {name} identity "
                        f"{value['adapter']}/{value['provider']}/{value['model']}/{value['effort']} is not this "
                        "profile",
                    },
                )
        basis.update({"profileMatch": True, "requested": requested, "resolved": resolved, "observed": observed})
        return True, basis

    def _classify_evidence(
        self, kind: str, identity: dict, profile: sqlite3.Row
    ) -> tuple[bool, bool, str | None, dict]:
        """Return ``(verified, counted, reason, basis)`` from the durable board record.

        ``verified`` means the durable record corroborates the claim. ``counted``
        additionally requires an appropriate Host verdict, an exactly known profile
        identity and an attributable (non-infrastructure, non-cancelled) outcome.
        """
        if kind == "task-cancelled":
            corroborated = bool(
                identity.get("cancelRequested")
                or identity.get("taskState") == "cancelled"
                or identity.get("resultStatus") == "cancelled"
            )
            return (
                corroborated,
                False,
                "not counted: cancelled work is not evidence about model quality",
                {"source": None, "excluded": "cancelled", "profileMatch": False},
            )
        if identity.get("attemptId") is None or identity.get("resultStatus") is None:
            return (
                False,
                False,
                "not counted: the linked run has no committed attempt result, so this claim is stored as an "
                "unverified report",
                {"source": None, "profileMatch": False},
            )
        if kind == "task-success":
            if identity.get("taskState") != "completed" or identity.get("resultStatus") != "ok":
                return (
                    False,
                    False,
                    "not counted: the linked run has no completed successful result, so this claim is stored as an "
                    "unverified report",
                    {"source": None, "profileMatch": False},
                )
            if not identity.get("acceptedAt") or identity.get("acceptanceVerdict") != "accepted":
                return (
                    True,
                    False,
                    "not counted: a Host acceptance verdict for this exact attempt is required; an unreviewed "
                    "successful process is not task-success evidence",
                    {"source": "host-acceptance", "profileMatch": False},
                )
            matched, basis = self._sample_basis(identity, profile, source="host-acceptance")
            return True, matched, (None if matched else basis.get("reason")), basis
        if kind == "task-failure":
            # A rejected completed result may be a capability failure even when the
            # exit status is ok: the Host reviewed the actual work and rejected it.
            rejected_completion = (
                identity.get("resultStatus") == "ok" and identity.get("acceptanceVerdict") == "rejected"
            )
            failed = (
                identity.get("resultStatus") in ("failed", "cancelled")
                or identity.get("taskState") in ("failed", "cancelled", "reconciliation-needed")
                or rejected_completion
            )
            if not failed:
                return (
                    False,
                    False,
                    "not counted: the linked run has no failed result, so this claim is stored as an unverified "
                    "report",
                    {"source": None, "profileMatch": False},
                )
            if (
                identity.get("cancelRequested")
                or identity.get("resultStatus") == "cancelled"
                or identity.get("taskState") == "cancelled"
            ):
                return (
                    True,
                    False,
                    "not counted: a cancelled run is not a model-capability failure",
                    {"source": None, "excluded": "cancelled", "profileMatch": False},
                )
            if not identity.get("acceptedAt") or identity.get("acceptanceVerdict") != "rejected":
                return (
                    True,
                    False,
                    "not counted: a Host rejection verdict for this exact attempt is required; an unreviewed "
                    "failure is not attributed to the model",
                    {"source": "host-rejection", "profileMatch": False},
                )
            matched, basis = self._sample_basis(identity, profile, source="host-rejection")
            return True, matched, (None if matched else basis.get("reason")), basis
        return (
            False,
            False,
            "not counted: a manual or observational report is attributed but not a verified sample",
            {"source": None, "profileMatch": False},
        )

    def evidence_record(self, params: dict) -> dict:
        schemas.reject_unknown(
            params,
            {"commandId", "profileId", "kind", "summary", "project", "conditions", "source", "runId"},
            "evaluation.evidence.record",
        )
        command_id = schemas.optional_string(params, "commandId", max_length=128)
        profile_id = schemas.required_string(
            params, "profileId", max_length=128, pattern=schemas.IDENTIFIER_PATTERN
        )
        kind = schemas.required_string(params, "kind", max_length=32)
        if kind not in EVIDENCE_KINDS:
            raise BoardError("INVALID_ARGUMENT", f"kind must be one of {', '.join(EVIDENCE_KINDS)}")
        summary = schemas.required_string(params, "summary", max_length=MAX_TEXT)
        project = schemas.optional_string(params, "project", max_length=200)
        conditions = schemas.string_list(params, "conditions", limit=MAX_CONDITIONS)
        source = schemas.required_string(params, "source", max_length=256)
        run_id = schemas.optional_string(params, "runId", max_length=128)
        if kind in TASK_EVIDENCE_KINDS and run_id is None:
            raise BoardError("INVALID_ARGUMENT", f"kind {kind!r} requires the runId it is about")
        request = {
            "profileId": profile_id,
            "kind": kind,
            "summarySha256": sha256_text(summary),
            "project": project,
            "conditions": conditions,
            "source": source,
            "runId": run_id,
        }
        with self.board.db.write() as connection:
            if command_id:
                receipt = self.board._receipt(connection, command_id, "evaluation.evidence", request)
                if receipt is not None:
                    return {**receipt, "duplicate": True}
            profile = connection.execute(
                "SELECT * FROM evaluation_profiles WHERE profile_id=?", (profile_id,)
            ).fetchone()
            if profile is None:
                raise BoardError(
                    "NOT_FOUND",
                    "Evidence is recorded against a published profile; publish the profile first",
                    profileId=profile_id,
                )
            identity = self._task_identity(connection, run_id) if run_id else None
            evidence_id = self._evidence_id(profile_id, kind, summary, project, source, run_id, conditions)
            existing = connection.execute(
                "SELECT * FROM evaluation_evidence WHERE evidence_id=?", (evidence_id,)
            ).fetchone()
            table_revision = int(self._state(connection)["table_revision"])
            insert = existing is None
            if insert:
                identity = identity or {}
                verified, counted, reason, basis = self._classify_evidence(kind, identity, profile)
                frozen = canonical_json({**identity, "basis": basis})
                connection.execute(
                    "INSERT INTO evaluation_evidence(evidence_id, profile_id, kind, summary, project,"
                    " conditions_json, source, run_id, verified, counted, identity_json, recorded_revision, created_at)"
                    " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        evidence_id,
                        profile_id,
                        kind,
                        summary,
                        project,
                        canonical_json(conditions),
                        source,
                        run_id,
                        1 if verified else 0,
                        1 if counted else 0,
                        frozen,
                        table_revision,
                        self._now(),
                    ),
                )
                self._mark_pending(connection, evidence_id)
                self._record_sample(connection, profile_id, identity, counted, evidence_id, self._now(), basis)
                self.board._append_event(
                    connection,
                    "evaluation.evidence_recorded",
                    revision=table_revision,
                    payload={
                        "evidenceId": evidence_id,
                        "profileId": profile_id,
                        "kind": kind,
                        "verified": verified,
                        "counted": counted,
                        "runId": run_id,
                    },
                )
                existing = connection.execute(
                    "SELECT * FROM evaluation_evidence WHERE evidence_id=?", (evidence_id,)
                ).fetchone()
            else:
                # The identical report is never duplicated. Its derived verification
                # is re-evaluated so a Host verdict recorded after the report is
                # reflected without rewriting the report itself.
                verified, counted, reason, basis = self._classify_evidence(
                    kind, json.loads(existing["identity_json"]) if existing["identity_json"] else {}, profile
                )
                if bool(existing["verified"]) != verified or bool(existing["counted"]) != counted:
                    frozen = canonical_json(
                        {**(json.loads(existing["identity_json"]) or {}), "basis": basis}
                    )
                    connection.execute(
                        "UPDATE evaluation_evidence SET verified=?, counted=?, identity_json=? WHERE evidence_id=?",
                        (1 if verified else 0, 1 if counted else 0, frozen, evidence_id),
                    )
                    self._record_sample(
                        connection, profile_id, json.loads(frozen), counted, evidence_id, self._now(), basis
                    )
                    existing = connection.execute(
                        "SELECT * FROM evaluation_evidence WHERE evidence_id=?", (evidence_id,)
                    ).fetchone()
            response = {
                "evidence": self._evidence_view(existing),
                "verified": bool(existing["verified"]),
                "counted": bool(existing["counted"]),
                "unverifiedReason": None if existing["counted"] else reason,
                "duplicate": not insert,
            }
            if command_id:
                self.board._store_receipt(connection, command_id, "evaluation.evidence", request, response)
            head = self.board._head_of(connection)
        self.board._notify(head)
        return response

    def record_catalog(self, discovered: dict) -> dict:
        """Persist one successful discovery as proposed data. Never publishes it."""
        payload = CatalogView.canonical_payload(discovered)
        # Identity excludes the observation timestamp, so re-running discovery against
        # an unchanged harness records one catalog instead of a new row per refresh.
        identity = {key: value for key, value in payload.items() if key != "discoveredAt"}
        discovery_id = "cat-" + sha256_text(canonical_json(identity))[:24]
        with self.board.db.write() as connection:
            existing = connection.execute(
                "SELECT * FROM evaluation_catalog WHERE discovery_id=?", (discovery_id,)
            ).fetchone()
            now = self._now()
            if existing is None:
                connection.execute(
                    "INSERT INTO evaluation_catalog(discovery_id, discovered_at, source, harness_version,"
                    " provider_version, payload_json, created_at) VALUES(?,?,?,?,?,?,?)",
                    (
                        discovery_id,
                        payload.get("discoveredAt") or now,
                        payload["source"],
                        payload.get("harnessVersion"),
                        payload.get("providerVersion"),
                        canonical_json(payload),
                        now,
                    ),
                )
                self.board._append_event(
                    connection,
                    "evaluation.catalog_discovered",
                    payload={"discoveryId": discovery_id, "source": payload["source"]},
                )
                duplicate = False
                view = CatalogView.from_payload(payload)
            else:
                duplicate = True
                # Report the recorded discovery, not a later observation of it.
                view = CatalogView.from_payload(json.loads(existing["payload_json"]), existing)
            head = self.board._head_of(connection)
        self.board._notify(head)
        return {
            "discoveryId": discovery_id,
            "duplicate": duplicate,
            "catalog": view.metadata(),
            "profiles": view.proposed_profiles(),
            "note": (
                "Discovered profiles are proposed data. Publishing them goes through the writer gate; this refresh "
                "made no model call and exposed no credential."
            ),
        }

    # -- views ---------------------------------------------------------------
    @staticmethod
    def _profile_view(row: sqlite3.Row) -> dict:
        view = {
            "profileId": row["profile_id"],
            "label": row["label"],
            "adapter": row["adapter"],
            "provider": row["provider"],
            "model": row["model"],
            "effort": row["effort"],
            "available": bool(row["available"]),
            "enabled": bool(row["enabled"]),
            "capabilities": json.loads(row["capabilities_json"]),
            "contextWindow": row["context_window"],
            "description": row["description"],
            "source": row["source"],
        }
        if row["unavailable_reason"]:
            view["unavailableReason"] = row["unavailable_reason"]
        return view

    @staticmethod
    def _card_view(row: sqlite3.Row) -> dict:
        return {
            "profileId": row["profile_id"],
            "revision": int(row["revision"]),
            "summary": row["summary"],
            "strengths": json.loads(row["strengths_json"]),
            "limitations": json.loads(row["limitations_json"]),
            "risks": json.loads(row["risks_json"]),
            "evidenceIds": json.loads(row["evidence_ids_json"]),
            "sampleCount": int(row["sample_count"]),
            "updatedAt": row["updated_at"],
        }

    @staticmethod
    def card_history(connection: sqlite3.Connection, profile_id: str, limit: int = 50) -> list[dict]:
        """Archived card snapshots, newest first, for one bounded profile.

        Automatic compaction retires references from the bounded *current* card; the
        publication snapshots keep the text, risks and full reference list of every
        earlier revision, so provenance is archived rather than silently dropped.
        """
        rows = connection.execute(
            "SELECT * FROM evaluation_card_history WHERE profile_id=? ORDER BY table_revision DESC LIMIT ?",
            (profile_id, max(1, min(int(limit), 500))),
        ).fetchall()
        return [
            {
                "tableRevision": int(row["table_revision"]),
                "profileId": row["profile_id"],
                "cardRevision": int(row["card_revision"]),
                "summary": row["summary"],
                "strengths": json.loads(row["strengths_json"]),
                "limitations": json.loads(row["limitations_json"]),
                "risks": json.loads(row["risks_json"]),
                "evidenceIds": json.loads(row["evidence_ids_json"]),
                "sampleCount": int(row["sample_count"]),
                "publishedAt": row["published_at"],
            }
            for row in rows
        ]

    @staticmethod
    def _evidence_view(row: sqlite3.Row) -> dict:
        identity = json.loads(row["identity_json"]) if row["identity_json"] else {}
        basis = identity.get("basis") if isinstance(identity.get("basis"), dict) else {}
        view = {
            "evidenceId": row["evidence_id"],
            "profileId": row["profile_id"],
            "kind": row["kind"],
            "summary": row["summary"],
            "project": row["project"],
            "conditions": json.loads(row["conditions_json"]),
            "source": row["source"],
            "runId": row["run_id"],
            "createdAt": row["created_at"],
            # Derived from the immutable attempt reference frozen at recording time.
            "verified": bool(row["verified"]),
            "counted": bool(row["counted"]),
            "attemptId": identity.get("attemptId"),
            "generation": identity.get("generation"),
            "identityBasis": {
                "source": basis.get("source"),
                "profileMatch": bool(basis.get("profileMatch")),
                "requested": identity.get("requested"),
                "resolved": identity.get("resolved"),
                "observed": identity.get("observed"),
                "excluded": basis.get("excluded"),
                "note": (
                    "requested is the configuration the run asked for; resolved/observed stay null when the "
                    "provider did not report a served identity, and unknown is never filled in"
                ),
            },
        }
        if not row["counted"] and basis.get("reason"):
            view["unverifiedReason"] = basis["reason"]
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
            "evidenceIds": json.loads(row["evidence_ids_json"]),
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


def _constant_time_equal(left: str, right: str) -> bool:
    import hmac

    return hmac.compare_digest(left or "", right or "")


__all__ = ["EvaluationStore", "parse_timestamp"]
