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

from . import schemas, user_policy
from .catalog import CatalogView
from .db import canonical_json, sha256_text
from .errors import BoardError

#: Bound current selection and maintenance packets independently of retained history.
MAX_PROFILES = 200
MAX_CARDS = 500
MAX_EVIDENCE_IDS = 64
MAX_EVIDENCE_PAGE = 200
MAX_DECISION_PAGE = 50
MAX_CARD_POINTS = 16
MAX_CONDITIONS = 8
MAX_TEXT = 2000
MAX_SUMMARY = 4000
MAX_POINT = 500
MAX_REASON = 500
#: One bounded Harness-owned maintenance packet. ``limit`` bounds how many actual
#: Host-reviewed facts are prepared per call and ``MAX_PACKET_EVIDENCE`` bounds the
#: evidence summaries returned with them. A required card reference is never dropped
#: to fit: exceeding the bound is an explicit, actionable refusal.
MAX_PREPARE_FACTS = 64
DEFAULT_PREPARE_FACTS = 32
MAX_PACKET_EVIDENCE = 256
#: Bounded artifact provenance frozen onto one prepared fact.
MAX_FACT_ARTIFACTS = 8
#: Bounded original task scope frozen onto one prepared fact; truncation is reported.
MAX_FACT_TASK = 1000
#: One bounded page of publication history.
DEFAULT_HISTORY_PAGE = 20
MAX_HISTORY_PAGE = 100

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

#: The immutable review ledger. Each Host acknowledgement appends exactly one of
#: these events inside the same transaction that records the verdict, so the event
#: sequence is a strictly increasing, clock-independent ordering of real reviews.
REVIEW_EVENT_KINDS = ("task.accepted", "task.rejected", "workflow.acknowledged")

#: The same review kinds as one code-owned literal SQL list. ``events_review_seq_idx``
#: is a partial index on exactly this predicate; bind parameters would leave SQLite
#: unable to prove the query implies the partial predicate, so a bounded prepare would
#: fall back to scanning every unrelated event. The kinds are module constants, never
#: caller input, and the assert below keeps that guarantee at import time.
_REVIEW_KIND_LITERALS = "(" + ",".join(f"'{kind}'" for kind in REVIEW_EVENT_KINDS) + ")"
assert all(kind.replace(".", "").isalpha() for kind in REVIEW_EVENT_KINDS), REVIEW_EVENT_KINDS

#: Card input is text, risk and evidence references only: the counters, revision and
#: timestamp are derived by the backend and a supplied one is an unknown field.
CARD_FIELDS = frozenset({"profileId", "summary", "strengths", "limitations", "risks", "evidenceIds"})
#: The published configuration carries only the fixed decision profile. There is no
#: automatic-maintenance or scheduler setting: maintenance synthesis is performed by
#: an external Harness through ``evaluation_prepare`` and the ordinary writer gate.
CONFIGURATION_FIELDS = frozenset({"fastRouterProfileId", "reviewRouterProfileId", "defaultRoutingMode", "routingBudget"})
#: The Harness-owned maintenance reads. ``evaluation.prepare`` is a bounded,
#: deterministic fact collection with no model call and no writer lease;
#: ``evaluation.history`` is a bounded read of the existing publication log.
PREPARE_FIELDS = frozenset({"requestId", "limit", "profileId", "adapter"})
HISTORY_FIELDS = frozenset({"limit", "before"})


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
        from . import catalog_store
        return catalog_store.current(connection)


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

    @staticmethod
    def _card_entry(row: sqlite3.Row) -> dict:
        """One stored card as the publishable, counter-free entry a writer submits.

        The derived revision, sample count and timestamp belong to the read view; a
        maintenance PATCH that re-submits an untouched card must not carry them back
        into the writer gate as fabricated fields.
        """
        return {
            "profileId": row["profile_id"],
            "summary": row["summary"],
            "strengths": json.loads(row["strengths_json"]),
            "limitations": json.loads(row["limitations_json"]),
            "risks": json.loads(row["risks_json"]),
            "evidenceIds": json.loads(row["evidence_ids_json"]),
        }


    @staticmethod
    def _validate_configuration(entry: Any) -> dict:
        if not isinstance(entry, dict):
            raise BoardError("INVALID_ARGUMENT", "configuration must be an object")
        schemas.reject_unknown(entry, CONFIGURATION_FIELDS, "configuration")
        from . import router
        result = {}
        for key in ("fastRouterProfileId", "reviewRouterProfileId"):
            if key not in entry:
                continue
            profile_id = entry[key]
            if profile_id is not None:
                if not isinstance(profile_id, str) or not schemas.IDENTIFIER_PATTERN.fullmatch(profile_id):
                    raise BoardError("INVALID_ARGUMENT", f"configuration.{key} must be a profileId or null")
            result[key] = profile_id
        if "defaultRoutingMode" in entry:
            result["defaultRoutingMode"] = router.mode(entry["defaultRoutingMode"])
        if "routingBudget" in entry:
            result["routingBudget"] = router.budget(entry["routingBudget"])["preset"]
        if not result:
            raise BoardError("INVALID_ARGUMENT", "configuration requires a routing model or budget")
        return result


    # -- publish -------------------------------------------------------------
    def _publish_revision(self, connection, *, revision, writer, now, params) -> dict:
        """Publish a maintenance-only card patch without touching user policy."""
        entries = params.get("cards")
        if not isinstance(entries, list) or len(entries) > MAX_CARDS:
            raise BoardError("INVALID_ARGUMENT", f"cards must be a list of at most {MAX_CARDS} entries")
        provided_cards = self._validate_cards(connection, entries)
        for profile_id in provided_cards:
            if connection.execute("SELECT 1 FROM evaluation_profiles WHERE profile_id=?", (profile_id,)).fetchone() is None:
                raise BoardError("NOT_FOUND", "A card requires an existing profile", profileId=profile_id)
        previous_references = self._card_references(connection)
        resulting_cards = dict(previous_references)
        resulting_cards.update({profile_id: entry["evidenceIds"] for profile_id, entry in provided_cards.items()})
        retired = frozenset(evidence_id for profile_id, entry in provided_cards.items()
                            for evidence_id in previous_references.get(profile_id, [])
                            if evidence_id not in entry["evidenceIds"])
        for profile_id, entry in provided_cards.items():
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

        self._sync_pending(connection, previous=previous_references, resulting=resulting_cards, keep_consumed=retired)
        for profile_id in provided_cards:
            connection.execute("UPDATE evaluation_cards SET origin='maintenance' WHERE profile_id=?", (profile_id,))
        counts = {"cards": len(provided_cards), "provided": ["cards"]}
        connection.execute(
            "INSERT INTO evaluation_revisions(revision,kind,writer_id,actor,counts_json,created_at) VALUES(?,?,?,?,?,?)",
            (revision, "maintenance", writer["writer_id"], writer["writer_id"], canonical_json(counts), now))
        connection.execute("UPDATE evaluation_state SET table_revision=?,updated_at=? WHERE id=1", (revision, now))
        return {"revision": revision, "configurationRevision": int(self._state(connection)["configuration_revision"]), "counts": counts}

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
        pending again — unless it was retired by a maintenance publication
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
            from . import router
            settings = router.configuration(connection)
            routing_budget = router.configured_budget(connection)
            profiles = [
                self._profile_view(row)
                for row in connection.execute(
                    "SELECT p.*, h.status AS harness_status, c.status AS catalog_state, c.reason AS catalog_reason FROM evaluation_profiles p "
                    "LEFT JOIN harness_health h ON h.adapter=p.adapter "
                    "LEFT JOIN catalog_current c ON c.adapter=p.adapter "
                    "WHERE (p.available=1 AND h.status='ready') OR p.profile_id IN (?,?) ORDER BY p.rowid LIMIT 202",
                    (settings["fastRouterProfileId"], settings["reviewRouterProfileId"])
                )
            ]
            from .billing import for_provider
            for profile in profiles:
                profile["billing"] = for_provider(connection, profile["adapter"], profile["provider"])
            profile_ids = [profile["profileId"] for profile in profiles]
            marks = ",".join("?" for _ in profile_ids) or "NULL"
            cards = [self._card_view(row) for row in connection.execute(f"SELECT * FROM evaluation_cards WHERE profile_id IN ({marks}) ORDER BY rowid", profile_ids)]
            policy = user_policy.policy_view(connection, profile_ids)
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
            # Sample counts come from the existing per-profile aggregate counters, so
            # they cover every published profile independently of whether a prose card
            # exists yet: a published profile with accepted attempts is a real count.
            sample_counts = {
                profile_id: self._sample_count(connection, profile_id) for profile_id in profile_ids
            }
            unavailable_count = connection.execute(
                "SELECT COUNT(*) AS count FROM evaluation_profiles p LEFT JOIN harness_health h ON h.adapter=p.adapter WHERE p.available=0 OR h.status IS NULL OR h.status!='ready'"
            ).fetchone()["count"]
            gate = self._gate_view(connection, now)
            # One row per model family represented in this response, with the
            # effective limit (explicit setting or default) and the unresolved
            # attempt count. Effort variants collapse into their shared family.
            families = {
                (profile["adapter"], profile["provider"], profile["model"])
                for profile in profiles
            }
            model_concurrency = self.board.model_capacity_rows(connection, families)
        tasks = self.board.task_list({"limit": 100, "offset": 0})
        return {
            "csrfToken": "",
            "tableRevision": table_revision,
            "gate": gate,
            "configuration": {
                "revision": int(state["configuration_revision"]),
                **settings,
                "routingBudgetLimits": routing_budget,
            },
            "profiles": profiles,
            "modelConcurrency": model_concurrency,
            "unavailableProfileCount": unavailable_count,
            **policy,
            "cards": cards,
            "sampleCounts": sample_counts,
            "evidence": evidence,
            "decisions": decisions,
            "pendingEvidence": pending,
            # The latest 100 rows stay exactly what the snapshot has always shown
            # (helpers and internal decisions included); ``nextCursor`` is the keyset
            # position the separate task-history read resumes from.
            "tasks": {"runs": tasks["runs"], "total": int(tasks["total"]), "nextCursor": tasks["nextCursor"]},
            "capabilities": self.capabilities(),
        }

    def capabilities(self) -> dict:
        """Real implemented availability only.

        ``selection`` is true exactly while the bounded decision adapter exists in
        this build (a helper entrypoint plus Node): when it is absent, a selection
        request reports an honest adapter-unavailable outcome instead of pretending a
        model ran. ``maintenance`` is false because the
        blackboard executes no maintenance model call and has no automatic-adoption
        setting: an external Harness prepares bounded facts through
        ``evaluation_prepare`` (no model call, no lease), synthesizes card text under
        the buddy skill, and commits a short card-only patch through the ordinary
        writer gate. ``modelCatalogDiscovery`` reports that the installed-harness
        discovery helper is present, not that a discovery succeeded; a successful
        discovery is visible through ``model_catalog_refresh``.
        """
        from . import catalog
        # Even model-free reads use this board's recorded harness health, not
        # whichever native executables happen to be visible to the caller.
        adapter_available, adapter_reason = self.board.decisions._adapter_available()
        return {
            "selection": bool(adapter_available),
            "maintenance": False,
            "maintenanceMode": "harness-owned",
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
        kind = schemas.optional_string(params, "kind", max_length=16) or "maintenance"
        if kind not in WRITER_KINDS:
            raise BoardError("INVALID_ARGUMENT", "kind must be 'human' or 'maintenance'")
        user_policy.require_writer_kind(kind)
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
        user_policy.require_writer_kind(writer["kind"])
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

    def user_policy_publish(self, params: dict) -> dict:
        return self._publish(params, kind="human")

    def assessment_publish(self, params: dict) -> dict:
        return self._publish(params, kind="maintenance")

    def _publish(self, params: dict, *, kind: str) -> dict:
        user_policy.require_writer_kind(kind)
        allowed = user_policy.PATCH_FIELDS if kind == "human" else {"cards"}
        schemas.reject_unknown(params, {*user_policy.GRANT_FIELDS, "commandId", *allowed}, "evaluation publication")
        command_id = schemas.required_string(params, "commandId", max_length=128)
        # Required fields are checked *before* the idempotency lookup, and the request
        # fingerprint is built from exactly the fields the caller provided: an omitted
        # collection and an explicit null are different requests, so a stored receipt
        # can never satisfy a changed or invalid command.
        for name in user_policy.GRANT_FIELDS:
            if name not in params:
                raise BoardError("INVALID_ARGUMENT", f"{name} is required")
        request = {key: value for key, value in params.items() if key != "commandId"}
        operation = "user_policy.publish" if kind == "human" else "assessment.publish"
        with self.board.db.write() as connection:
            self._writer_from_params(connection, params)
            receipt = self.board._receipt(connection, command_id, operation, request)
            if receipt is not None:
                return {**receipt, "duplicate": True}
            now = self._now()
            self._sweep(connection, now)
            state = self._state(connection)
            table_revision = int(state["table_revision"])
            writer = self._writer_from_params(connection, params)
            if writer["kind"] != kind:
                raise BoardError("FORBIDDEN", "Writer kind does not authorize this publication")
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
            if kind == "human":
                published = user_policy.publish(self, connection, revision=table_revision + 1, writer=writer, now=now, params=params)
            else:
                published = self._publish_revision(connection, revision=table_revision + 1, writer=writer, now=now, params=params)
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
                connection, command_id, operation, request, response
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
            self._writer_from_params(connection, params)
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

    @staticmethod
    def _reviewed_attempt_state(attempt: sqlite3.Row | None, result: dict | None) -> str | None:
        """The terminal state the reviewed attempt itself proves, or ``None``.

        Derived only from the attempt's own committed execution state and result
        status, so an archived fact can never inherit the current task state of a
        later attempt. Unknown stays ``None`` instead of being guessed.
        """
        if attempt is None:
            return None
        status = (result or {}).get("status")
        state = attempt["execution_state"]
        if status == "ok":
            return "completed" if state == "finished" else None
        if status == "failed":
            return "failed"
        if status == "cancelled":
            return "cancelled"
        if state == "uncertain":
            return "reconciliation-needed"
        return None

    def _attempt_identity(
        self,
        connection: sqlite3.Connection,
        task: sqlite3.Row,
        attempt: sqlite3.Row | None,
        *,
        acceptance: dict | None = None,
        artifact_binding: dict | None = None,
    ) -> dict:
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
        # A current review is described by the task's own recorded verdict. An archived
        # review supplies its matching, immutable task.review_archived record instead:
        # the task columns now describe another attempt and are never borrowed.
        acceptance = acceptance or {
            "acceptedAt": task["accepted_at"],
            "acceptanceNote": task["acceptance_note"],
            "acceptanceVerdict": task["acceptance_verdict"],
            "archived": False,
        }
        archived = bool(acceptance.get("archived"))
        task_state = task["state"]
        if archived:
            task_state = self._reviewed_attempt_state(attempt, result)
        return {
            "taskId": task["task_id"],
            "attemptId": attempt["attempt_id"] if attempt is not None else None,
            "generation": int(attempt["generation"]) if attempt is not None else None,
            "taskState": task_state,
            "attemptState": attempt["execution_state"] if attempt is not None else None,
            "reviewBinding": "archived" if archived else "current",
            **({"currentTaskState": task["state"]} if archived else {}),
            "resultStatus": (result or {}).get("status"),
            "shutdownConfirmed": bool(attempt["shutdown_confirmed"]) if attempt is not None else False,
            "cancelRequested": bool(attempt["cancel_requested_at"]) if attempt is not None else False,
            "error": (result or {}).get("error"),
            "exitCode": (result or {}).get("exitCode"),
            "acceptedAt": acceptance["acceptedAt"],
            "acceptanceNote": acceptance["acceptanceNote"],
            "acceptanceVerdict": acceptance["acceptanceVerdict"],
            "runtimeIdentity": attempt["runtime_identity"] if attempt is not None else None,
            "delegation": self._delegation_provenance(connection, task),
            "artifacts": self._artifact_provenance(connection, task, attempt, binding=artifact_binding),
            "adapter": adapter,
            "requested": EvaluationStore._requested_identity(adapter, spec, report),
            "resolved": EvaluationStore._reported_identity(adapter, report.get("resolved")),
            "observed": EvaluationStore._reported_identity(adapter, report.get("observed")),
        }

    @staticmethod
    def _delegation_provenance(connection: sqlite3.Connection, task: sqlite3.Row) -> dict:
        """Bounded original scope and source attribution for one reviewed attempt.

        The recorded admission spec is the original delegated scope; it is truncated
        rather than expanded into a transcript, and the truncation is reported. The
        original source Host is derived exactly like the delegation projection: the
        first governed submission or helper admission, then generation-1 takeover
        evidence, then a still-generation-1 run row. Unknown stays null.
        """
        task_text: str | None = None
        truncated = False
        try:
            spec = json.loads(task["spec_json"])
        except (TypeError, ValueError):  # pragma: no cover - a stored spec is always JSON
            spec = {}
        if isinstance(spec, dict):
            value = spec.get("task")
            if isinstance(value, str) and value.strip():
                text = value.strip()
                truncated = len(text) > MAX_FACT_TASK
                task_text = text[:MAX_FACT_TASK]
        row = connection.execute(
            "SELECT COALESCE("
            " (SELECT CASE event.kind"
            "     WHEN 'task.submitted' THEN json_extract(event.payload_json, '$.governed.sourceHostId')"
            "     ELSE json_extract(event.payload_json, '$.sourceHostId') END"
            "    FROM events event WHERE event.task_id=?"
            "      AND ((event.kind='task.submitted' AND json_type(event.payload_json,'$.governed.sourceHostId')='text'"
            "            AND length(json_extract(event.payload_json,'$.governed.sourceHostId'))>0)"
            "        OR (event.kind='workflow.helper_admitted' AND json_type(event.payload_json,'$.sourceHostId')='text'"
            "            AND length(json_extract(event.payload_json,'$.sourceHostId'))>0))"
            "    ORDER BY event.seq LIMIT 1),"
            " (SELECT json_extract(event.payload_json, '$.previousHostId') FROM events event"
            "    WHERE event.task_id=? AND event.kind='workflow.takeover'"
            "      AND json_extract(event.payload_json, '$.previousOwnerGeneration')=1"
            "      AND json_type(event.payload_json, '$.previousHostId')='text'"
            "      AND length(json_extract(event.payload_json, '$.previousHostId'))>0"
            "    ORDER BY event.seq LIMIT 1),"
            " (SELECT CASE WHEN run.owner_generation=1 THEN run.host_id END FROM workflow_runs run WHERE run.run_id=?)"
            ") AS source_host",
            (task["task_id"], task["task_id"], task["task_id"]),
        ).fetchone()
        source_host = row["source_host"] if row is not None and isinstance(row["source_host"], str) else None
        return {
            "task": task_text,
            "taskTruncated": truncated,
            "sourceHostId": source_host[:256] if source_host else None,
        }

    @staticmethod
    def _artifact_provenance(
        connection: sqlite3.Connection,
        task: sqlite3.Row,
        attempt: sqlite3.Row | None,
        *,
        binding: dict | None = None,
    ) -> dict | None:
        """Bounded artifact provenance bound to this attempt.

        The review's own binding is authoritative when supplied: the artifact the Host
        actually reviewed and its verdict decide the reported accepted artifact, never
        the run's current final columns — an archived review must not be relabelled
        with a newer attempt's artifact. Without a binding the run's final artifact and
        final attempt are reported as before. The accepted artifact is listed first, an
        accepted binding for another attempt is never relabelled as this one, and the
        exact total plus the truncation signal are always reported.
        """
        if attempt is None:
            return None
        attempt_id = attempt["attempt_id"]
        if binding is not None:
            reviewed = binding.get("artifactId")
            reviewed = reviewed if isinstance(reviewed, str) and reviewed else None
            if binding.get("verdict") == "accepted" and reviewed is not None:
                accepted_artifact, accepted_attempt = reviewed, attempt_id
            else:
                accepted_artifact, accepted_attempt = None, None
        else:
            run = connection.execute(
                "SELECT final_artifact_id, final_attempt_id FROM workflow_runs WHERE run_id=?", (task["task_id"],)
            ).fetchone()
            accepted_artifact = run["final_artifact_id"] if run is not None else None
            accepted_attempt = run["final_attempt_id"] if run is not None else None
        total = int(
            connection.execute(
                "SELECT COUNT(*) AS count FROM workflow_artifacts WHERE run_id=? AND (attempt_id=? OR attempt_id IS NULL)",
                (task["task_id"], attempt_id),
            ).fetchone()["count"]
        )
        rows = connection.execute(
            "SELECT artifact_id, kind, manifest_sha256 FROM workflow_artifacts"
            " WHERE run_id=? AND (attempt_id=? OR attempt_id IS NULL) ORDER BY artifact_id LIMIT ?",
            (task["task_id"], attempt_id, MAX_FACT_ARTIFACTS),
        ).fetchall()
        entries = [
            {"artifactId": row["artifact_id"], "kind": row["kind"], "manifestSha256": row["manifest_sha256"]}
            for row in rows
        ]
        if not entries:
            # The ordinary artifact ledger is the fallback for non-governed runs.
            plain_total = int(
                connection.execute(
                    "SELECT COUNT(*) AS count FROM artifacts WHERE attempt_id=?", (attempt_id,)
                ).fetchone()["count"]
            )
            plain_rows = connection.execute(
                "SELECT artifact_id, kind, content_hash FROM artifacts WHERE attempt_id=? ORDER BY artifact_id LIMIT ?",
                (attempt_id, MAX_FACT_ARTIFACTS),
            ).fetchall()
            if not plain_rows:
                return None
            return {
                "acceptedArtifactId": None,
                "acceptedAttemptId": None,
                "acceptedForThisAttempt": False,
                "artifacts": [
                    {"artifactId": row["artifact_id"], "kind": row["kind"], "contentHash": row["content_hash"]}
                    for row in plain_rows
                ],
                "total": plain_total,
                "truncated": plain_total > len(plain_rows),
            }
        if accepted_artifact and all(entry["artifactId"] != accepted_artifact for entry in entries):
            # The accepted artifact is always named even when it sorts outside the
            # bounded page; it is fetched by primary key and never invented.
            bound = connection.execute(
                "SELECT artifact_id, kind, manifest_sha256 FROM workflow_artifacts WHERE artifact_id=?",
                (accepted_artifact,),
            ).fetchone()
            if bound is not None:
                entries.insert(
                    0,
                    {
                        "artifactId": bound["artifact_id"],
                        "kind": bound["kind"],
                        "manifestSha256": bound["manifest_sha256"],
                    },
                )
        if accepted_artifact:
            entries.sort(key=lambda entry: 0 if entry["artifactId"] == accepted_artifact else 1)
        entries = entries[:MAX_FACT_ARTIFACTS]
        return {
            "acceptedArtifactId": accepted_artifact,
            "acceptedAttemptId": accepted_attempt,
            "acceptedForThisAttempt": bool(accepted_attempt and accepted_attempt == attempt_id),
            "artifacts": entries,
            "total": total,
            "truncated": total > len(entries),
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

    def record_catalog(self, discovered: dict, observation_id=None) -> dict:
        from . import catalog_store
        return catalog_store.record(self, discovered, observation_id)

    # -- Harness-owned maintenance -------------------------------------------
    @staticmethod
    def _fact_evidence_id(profile_id: str, attempt_id: str, verdict: str) -> str:
        """Deterministic identity of one Host-reviewed fact.

        The factual identity is the (profile, attempt, verdict) triple: a repeated
        preparation of the same batch can never record it twice, while a retried task
        (a new attempt) or a different published profile is a genuinely new fact. The
        human-readable summary is presentation only and is never part of the identity.
        """
        return "ev-" + sha256_text(
            canonical_json(
                {"source": "host-review", "profileId": profile_id, "attemptId": attempt_id, "verdict": verdict}
            )
        )[:32]

    @staticmethod
    def _prepare_scope(profile_id: str | None, adapter: str | None) -> str:
        """The reported filter of one preparation request.

        Progress itself is durable per assessed profile (``profile:<id>``), never per
        request filter: a profile published after an earlier pass backfills its own
        history through the same unfiltered or adapter-filtered request, and a profile
        that is already current is never rescanned.
        """
        if profile_id:
            return f"profile:{profile_id}"
        if adapter:
            return f"adapter:{adapter}"
        return "all"

    @staticmethod
    def _checkpoint(connection: sqlite3.Connection, scope: str) -> int | None:
        row = connection.execute(
            "SELECT review_seq FROM evaluation_maintenance_checkpoints WHERE scope=?", (scope,)
        ).fetchone()
        return None if row is None else int(row["review_seq"])

    @staticmethod
    def _review_query(*, cursor: bool) -> str:
        """The bounded review-ledger range in immutable sequence order.

        The kind list is rendered literally from the module constant so SQLite can
        prove the ``events_review_seq_idx`` partial predicate: a bound ``IN (?,?,?)``
        list leaves the planner unable to prove it and falls back to scanning every
        unrelated event. The values are code-owned constants, never caller input.
        """
        query = (
            "SELECT event.seq AS review_seq, event.task_id AS review_task_id,"
            " event.attempt_id AS review_attempt_id, event.kind AS review_kind,"
            " event.payload_json AS review_payload, task.*"
            " FROM events event JOIN tasks task ON task.task_id = event.task_id"
            f" WHERE event.kind IN {_REVIEW_KIND_LITERALS}"
        )
        if cursor:
            query += " AND event.seq > ?"
        return query + " ORDER BY event.seq LIMIT ?"

    @staticmethod
    def _reviewed_batch(connection: sqlite3.Connection, cursor: int | None, limit: int) -> list:
        """One bounded, indexed page of actual Host acknowledgement events.

        The review ledger is the append-only event stream, not a wall-clock column: an
        acceptance or rejection appended later always carries a higher ``events.seq``,
        so a review recorded under a skewed or rolled-back clock cannot be skipped.
        Each event keeps its own immutable attempt binding and reviewed artifact. The
        partial index ``events_review_seq_idx`` answers the range in sequence order.
        """
        values: list[Any] = []
        if cursor is not None:
            values.append(int(cursor))
        values.append(int(limit))
        return connection.execute(EvaluationStore._review_query(cursor=cursor is not None), values).fetchall()

    @staticmethod
    def _reviewed_backlog(connection: sqlite3.Connection, cursor: int | None) -> int:
        query = f"SELECT COUNT(*) AS count FROM events WHERE kind IN {_REVIEW_KIND_LITERALS}"
        values: list[Any] = []
        if cursor is not None:
            query += " AND seq > ?"
            values.append(int(cursor))
        return int(connection.execute(query, values).fetchone()["count"])

    def _reviewed_backlog_for(self, connection: sqlite3.Connection, cursors: dict) -> int:
        """Review events the slowest assessed profile has not evaluated yet.

        A profile with no cursor has seen nothing, so the complete review ledger is
        still ahead of it; otherwise the earliest cursor is the honest remainder.
        """
        if not cursors:
            return 0
        values = [value for value in cursors.values() if value is not None]
        if len(values) != len(cursors):
            return self._reviewed_backlog(connection, None)
        return self._reviewed_backlog(connection, min(values))

    @staticmethod
    def _review_task_binding(review: sqlite3.Row) -> bool:
        """Whether this review is still the task's own current recorded verdict.

        Only then may the task's ``accepted_at``/note/verdict describe this review.
        After a retry or continuation those columns belong to another attempt or are
        cleared, so the archived record is used instead — the current task state and
        verdict are never borrowed for an older attempt.
        """
        return bool(
            review["accepted_at"]
            and review["selected_attempt_id"] == review["review_attempt_id"]
            and review["acceptance_verdict"] == EvaluationStore._review_verdict(review)
        )

    @staticmethod
    def _review_verdict(review: sqlite3.Row) -> str | None:
        payload = json.loads(review["review_payload"]) if review["review_payload"] else {}
        verdict = payload.get("verdict") if isinstance(payload, dict) else None
        if verdict in ("accepted", "rejected"):
            return verdict
        if review["review_kind"] == "task.accepted":
            return "accepted"
        if review["review_kind"] == "task.rejected":
            return "rejected"
        return None

    @staticmethod
    def _reviewed_artifact(review: sqlite3.Row) -> str | None:
        payload = json.loads(review["review_payload"]) if review["review_payload"] else {}
        artifact_id = payload.get("artifactId") if isinstance(payload, dict) else None
        return artifact_id if isinstance(artifact_id, str) and artifact_id else None

    @staticmethod
    def _review_note_bytes(review: sqlite3.Row) -> int | None:
        """The immutable note length the review event itself recorded, when present."""
        payload = json.loads(review["review_payload"]) if review["review_payload"] else {}
        value = payload.get("noteBytes") if isinstance(payload, dict) else None
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            return None
        return value

    @staticmethod
    def _review_archive(connection: sqlite3.Connection, review: sqlite3.Row) -> dict | None:
        """The matching read-only archive of one superseded review, or ``None``.

        A retry or continuation moves the reviewed attempt's acceptedAt/verdict/note
        into exactly one immutable ``task.review_archived`` event and clears the task
        columns. That archived record is the old attempt's own fact, so the reviewed
        attempt's acceptance stays reachable instead of being lost once a newer review
        exists. The record is only accepted when it matches the review event's own
        attempt, verdict and recorded note length; a mismatch is a different review and
        is reported as unproven rather than merged with this one.
        """
        verdict = EvaluationStore._review_verdict(review)
        attempt_id = review["review_attempt_id"]
        if verdict is None or not attempt_id:
            return None
        expected_note_bytes = EvaluationStore._review_note_bytes(review)
        rows = connection.execute(
            "SELECT payload_json FROM events WHERE task_id=? AND attempt_id=? AND kind='task.review_archived'"
            " ORDER BY seq",
            (review["review_task_id"], attempt_id),
        ).fetchall()
        for row in rows:
            payload = json.loads(row["payload_json"]) if row["payload_json"] else {}
            if not isinstance(payload, dict) or payload.get("verdict") != verdict:
                continue
            accepted_at, note = payload.get("acceptedAt"), payload.get("note")
            if not isinstance(accepted_at, str) or not accepted_at or not isinstance(note, str):
                continue
            if expected_note_bytes is not None and len(note.encode()) != expected_note_bytes:
                # The review event recorded its own note length; a mismatched archive
                # record is a different review and is never merged with this one.
                continue
            return {
                "acceptedAt": accepted_at,
                "acceptanceNote": note,
                "acceptanceVerdict": verdict,
                "archived": True,
            }
        return None

    @staticmethod
    def _artifact_hash_turn_proof(
        connection: sqlite3.Connection, task_id: str, attempt_id: str, artifact: sqlite3.Row
    ) -> bool:
        """Whether one pinned output artifact really is this attempt's sealed output.

        The immutable binding comes from the attempt's own committed result: the sealed
        ``workspaceSeal`` snapshot hash must be exactly the artifact's manifest hash,
        and the artifact's turn must be that same attempt's concluded completed turn.
        An artifact id that merely exists somewhere proves nothing.
        """
        attempt = connection.execute(
            "SELECT result_json FROM attempts WHERE attempt_id=?", (attempt_id,)
        ).fetchone()
        if attempt is None or not attempt["result_json"]:
            return False
        try:
            result = json.loads(attempt["result_json"])
        except (TypeError, ValueError):
            return False
        payload = result.get("result") if isinstance(result, dict) and isinstance(result.get("result"), dict) else {}
        seal = payload.get("workspaceSeal") if isinstance(payload.get("workspaceSeal"), dict) else {}
        snapshot = seal.get("snapshotSha256")
        if not isinstance(snapshot, str) or not snapshot or snapshot != artifact["manifest_sha256"]:
            return False
        turn = connection.execute(
            "SELECT turn_id FROM workflow_turns WHERE run_id=? AND attempt_id=?"
            " AND state='concluded' AND disposition='completed' ORDER BY turn_index LIMIT 1",
            (task_id, attempt_id),
        ).fetchone()
        return turn is not None and isinstance(turn["turn_id"], str) and turn["turn_id"] == artifact["turn_id"]

    @staticmethod
    def _artifact_proof(
        connection: sqlite3.Connection,
        *,
        task_id: str,
        attempt_id: str,
        verdict: str,
        artifact_id: str | None,
        current: bool,
    ) -> bool:
        """Whether an exact immutable output-artifact proof is bound to this review.

        A governed review must name the output artifact the Host actually reviewed, and
        that artifact must be pinned for exactly this run and attempt: the same
        ``run_id``, the same ``attempt_id``, ``kind='output'``, and the attempt's own
        sealed hash/turn binding. A current review must additionally still be the run's
        final binding (and, for an acceptance, its final artifact). An archived review
        is proven by its matching archive event instead, so the run's newer final
        binding is deliberately not borrowed. Without this proof the review is reported
        as skipped rather than turned into a fact.
        """
        artifact = connection.execute(
            "SELECT * FROM workflow_artifacts WHERE artifact_id=?", (artifact_id,)
        ).fetchone()
        if (
            artifact is None
            or artifact["run_id"] != task_id
            or artifact["attempt_id"] != attempt_id
            or artifact["kind"] != "output"
        ):
            return False
        if not EvaluationStore._artifact_hash_turn_proof(connection, task_id, attempt_id, artifact):
            return False
        if not current:
            return True
        run = connection.execute(
            "SELECT final_artifact_id, final_attempt_id FROM workflow_runs WHERE run_id=?", (task_id,)
        ).fetchone()
        if run is None or run["final_attempt_id"] != attempt_id:
            return False
        if verdict == "accepted" and run["final_artifact_id"] != artifact_id:
            return False
        return True

    @staticmethod
    def _is_pending(connection: sqlite3.Connection, evidence_id: str) -> bool:
        return (
            connection.execute(
                "SELECT 1 FROM evaluation_evidence_pending WHERE evidence_id=?", (evidence_id,)
            ).fetchone()
            is not None
        )

    @staticmethod
    def _assessed_profiles(
        connection: sqlite3.Connection, profile_id: str | None, adapter: str | None
    ) -> list:
        """Published profiles the caller asked to assess.

        Filters address execution identities only. A source Host or a source project
        is evidence provenance, never a filter, so experience recorded under another
        Host or project stays collectable.
        """
        if profile_id is not None:
            row = connection.execute(
                "SELECT * FROM evaluation_profiles WHERE profile_id=?", (profile_id,)
            ).fetchone()
            if row is None:
                raise BoardError(
                    "NOT_FOUND", "The requested profileId is not published", profileId=profile_id
                )
            if adapter is not None and row["adapter"] != adapter:
                raise BoardError(
                    "INVALID_ARGUMENT",
                    f"profileId {profile_id} uses the {row['adapter']} adapter, not {adapter}; the filters name "
                    "different execution identities",
                    profileId=profile_id,
                    adapter=adapter,
                )
            return [row]
        profiles = list(connection.execute(
            "SELECT * FROM evaluation_profiles WHERE available=1 AND (? IS NULL OR adapter=?) ORDER BY profile_id LIMIT ?",
            (adapter, adapter, MAX_PROFILES + 1),
        ))
        if len(profiles) > MAX_PROFILES:
            raise BoardError("PACKET_TOO_LARGE", "The current profile set exceeds the maintenance bound; narrow by profileId")
        return profiles

    @staticmethod
    def _pending_evidence_ids(connection: sqlite3.Connection, profile_id: str, limit: int) -> list[str]:
        return [
            row["evidence_id"]
            for row in connection.execute(
                "SELECT evidence_id FROM evaluation_evidence_pending WHERE profile_id=?"
                " ORDER BY created_at, evidence_id LIMIT ?",
                (profile_id, limit),
            )
        ]

    @staticmethod
    def _pending_evidence_count(connection: sqlite3.Connection, profile_id: str) -> int:
        row = connection.execute(
            "SELECT COUNT(*) AS count FROM evaluation_evidence_pending WHERE profile_id=?", (profile_id,)
        ).fetchone()
        return int(row["count"])

    def _packet_evidence(self, connection: sqlite3.Connection, evidence_id: str) -> dict | None:
        """One evidence summary plus its exact frozen provenance.

        The Harness needs the real attempt, verdict, acceptance note, original task
        scope, source Host and sealed artifact reference to write evidenced prose; it
        must never have to re-run a task or invent an outcome it cannot see.
        """
        row = connection.execute(
            "SELECT * FROM evaluation_evidence WHERE evidence_id=?", (evidence_id,)
        ).fetchone()
        if row is None:
            return None
        identity = json.loads(row["identity_json"]) if row["identity_json"] else {}
        delegation = identity.get("delegation") if isinstance(identity.get("delegation"), dict) else {}
        view = self._evidence_view(row)
        view["fact"] = {
            "attemptId": identity.get("attemptId"),
            "generation": identity.get("generation"),
            # ``taskState`` is the state the reviewed attempt itself proves when the
            # review is archived; ``currentTaskState`` is what the task is now and is
            # never used as the old attempt's fact.
            "taskState": identity.get("taskState"),
            "reviewBinding": identity.get("reviewBinding"),
            **({"currentTaskState": identity["currentTaskState"]} if identity.get("currentTaskState") else {}),
            "resultStatus": identity.get("resultStatus"),
            "acceptanceVerdict": identity.get("acceptanceVerdict"),
            "acceptedAt": identity.get("acceptedAt"),
            "acceptanceNote": identity.get("acceptanceNote"),
            "shutdownConfirmed": identity.get("shutdownConfirmed"),
            # Bounded original scope and attribution, never a raw transcript.
            "task": delegation.get("task"),
            "taskTruncated": bool(delegation.get("taskTruncated")),
            "sourceHostId": delegation.get("sourceHostId"),
            # The exact artifact this review named, kept separate from the run's
            # accepted binding because a rejection accepts no artifact.
            "reviewedArtifactId": identity.get("reviewedArtifactId"),
            "artifacts": identity.get("artifacts"),
            "requested": identity.get("requested"),
            "resolved": identity.get("resolved"),
            "observed": identity.get("observed"),
            "basis": identity.get("basis"),
        }
        return view

    def prepare(self, params: dict) -> dict:
        """Collect one bounded, deterministic batch of Host-reviewed facts.

        This operation is the Harness-owned maintenance read. It makes **no model
        call**, takes **no writer or reader lease** and replays no task: it scans the
        actual review ledger in bounded review order, matches each reviewed attempt to
        the assessed profiles by its frozen execution identity, and freezes one
        evidence row per (profile, attempt, verdict) with its exact attempt, config,
        artifact, verdict, acceptance-note, original-scope and source-Host provenance.
        A review superseded by a retry or continuation stays collectable through its
        matching immutable ``task.review_archived`` record and its own
        attempt-bound artifact proof, so real rejected work is never lost once the
        task moves on; the current task state and verdict are never borrowed for the
        old attempt. Existing accepted history is reachable on the first call;
        cancelled, unreviewed, infrastructure and unknown-identity outcomes are
        recorded as the non-samples they are. Progress is durable and per assessed
        profile, so a profile published later still backfills its own history through
        the same unfiltered request.

        The returned packet is the bounded input for the invoking Harness. It carries
        the assessed profiles, their current cards and preferences, pending and
        referenced evidence summaries, the ids recorded by this call, durable progress
        and any skipped or unproven reasons. Nothing is published here and no task or
        Host authority is touched: a stable replay returns the recorded packet
        unchanged, while a changed payload under the same requestId conflicts.
        """
        schemas.reject_unknown(params, PREPARE_FIELDS, "evaluation.prepare")
        request_id = schemas.required_string(
            params, "requestId", max_length=128, pattern=schemas.IDENTIFIER_PATTERN
        )
        limit = schemas.optional_int(params, "limit", DEFAULT_PREPARE_FACTS, 1, MAX_PREPARE_FACTS)
        profile_id = schemas.optional_string(
            params, "profileId", max_length=128, pattern=schemas.IDENTIFIER_PATTERN
        )
        adapter = schemas.optional_string(params, "adapter", max_length=32)
        if adapter is not None and adapter not in schemas.CODING_ADAPTERS:
            raise BoardError(
                "INVALID_ARGUMENT", f"adapter must be one of {', '.join(schemas.CODING_ADAPTERS)}", field="adapter"
            )
        request = {"requestId": request_id, "limit": limit, "profileId": profile_id, "adapter": adapter}
        scope = self._prepare_scope(profile_id, adapter)
        with self.board.db.write() as connection:
            receipt = self.board._receipt(connection, request_id, "evaluation.prepare", request)
            if receipt is not None:
                return receipt
            now = self._now()
            state = self._state(connection)
            revision = int(state["table_revision"])
            assessed = self._assessed_profiles(connection, profile_id, adapter)
            assessed_ids = [row["profile_id"] for row in assessed]
            # Progress is durable per assessed profile, so a profile published after
            # an earlier maintenance pass still backfills its own history on the same
            # unfiltered or adapter-filtered request. The scan frontier is the earliest
            # cursor among them and is never extended past what the slowest one has
            # seen, so a new profile costs bounded re-reads instead of a full scan and
            # an up-to-date profile is never rescanned.
            cursors: dict[str, int | None] = {
                row["profile_id"]: self._checkpoint(connection, f"profile:{row['profile_id']}") for row in assessed
            }
            scan_from = None if any(value is None for value in cursors.values()) else (
                min(cursors.values()) if cursors else None
            )
            batch = self._reviewed_batch(connection, scan_from, limit)
            new_ids: list[str] = []
            scheduled: set[str] = set()
            skipped: dict[str, int] = {}
            examples: dict[str, list[str]] = {}
            unproven: list[dict] = []

            def note_skip(reason: str, task_id: str) -> None:
                skipped[reason] = skipped.get(reason, 0) + 1
                listed = examples.setdefault(reason, [])
                if len(listed) < 3 and task_id not in listed:
                    listed.append(task_id)

            for review in batch:
                position = int(review["review_seq"])
                eligible = [
                    row
                    for row in assessed
                    if cursors[row["profile_id"]] is None or cursors[row["profile_id"]] < position
                ]
                if not eligible:
                    continue
                task_id = review["review_task_id"]
                verdict = self._review_verdict(review)
                if verdict not in ("accepted", "rejected"):
                    note_skip("unknown-verdict", task_id)
                    continue
                if not review["review_attempt_id"]:
                    note_skip("archived-attempt", task_id)
                    continue
                current = self._review_task_binding(review)
                archived = None
                if not current:
                    # A later retry or continuation keeps the earlier review in the
                    # immutable ledger. It stays collectable through its matching
                    # task.review_archived record; without that proof it is reported,
                    # never attributed to whatever attempt ran afterwards.
                    archived = self._review_archive(connection, review)
                    if archived is None:
                        note_skip("superseded-review", task_id)
                        continue
                attempt = connection.execute(
                    "SELECT * FROM attempts WHERE attempt_id=?", (review["review_attempt_id"],)
                ).fetchone()
                if attempt is None:
                    # The review is recorded but its attempt evidence is archived: it is
                    # reported, never guessed onto a profile.
                    note_skip("archived-attempt", task_id)
                    continue
                reviewed_artifact = self._reviewed_artifact(review)
                acceptance = archived if archived is not None else {
                    "acceptedAt": review["accepted_at"],
                    "acceptanceNote": review["acceptance_note"],
                    "acceptanceVerdict": verdict,
                    "archived": False,
                }
                identity = self._attempt_identity(
                    connection,
                    review,
                    attempt,
                    acceptance=acceptance,
                    artifact_binding={"artifactId": reviewed_artifact, "verdict": verdict},
                )
                if identity.get("requested") is None:
                    note_skip("identity-unknown", task_id)
                    continue
                if not self._artifact_proof(
                    connection,
                    task_id=task_id,
                    attempt_id=review["review_attempt_id"],
                    verdict=verdict,
                    artifact_id=reviewed_artifact,
                    current=current,
                ):
                    note_skip("missing-artifact-proof", task_id)
                    continue
                identity["reviewedArtifactId"] = reviewed_artifact
                matching = [row for row in eligible if self._matches_profile(identity.get("requested"), row)]
                if not matching:
                    note_skip("profile-not-assessed", task_id)
                    continue
                kind = "task-success" if verdict == "accepted" else "task-failure"
                for profile in matching:
                    evidence_id = self._fact_evidence_id(profile["profile_id"], attempt["attempt_id"], verdict)
                    existing = connection.execute(
                        "SELECT 1 FROM evaluation_evidence WHERE evidence_id=?", (evidence_id,)
                    ).fetchone()
                    if existing is not None:
                        # The factual identity is already recorded: repeat preparation
                        # adds no duplicate row, no duplicate sample and no recount.
                        scheduled.add(evidence_id)
                        continue
                    verified, counted, _reason, basis = self._classify_evidence(kind, identity, profile)
                    frozen = canonical_json({**identity, "basis": basis})
                    summary = (
                        f"Host-{verdict} task {task_id} attempt {attempt['attempt_id']} on "
                        f"{profile['adapter']}/{profile['provider']}/{profile['model']}/{profile['effort']}"
                    )[:MAX_TEXT]
                    connection.execute(
                        "INSERT INTO evaluation_evidence(evidence_id, profile_id, kind, summary, project,"
                        " conditions_json, source, run_id, verified, counted, identity_json, recorded_revision,"
                        " created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (
                            evidence_id,
                            profile["profile_id"],
                            kind,
                            summary,
                            EvaluationStore._fact_project(review),
                            canonical_json([]),
                            "host-review",
                            task_id,
                            1 if verified else 0,
                            1 if counted else 0,
                            frozen,
                            revision,
                            now,
                        ),
                    )
                    self._mark_pending(connection, evidence_id)
                    self._record_sample(connection, profile["profile_id"], identity, counted, evidence_id, now, basis)
                    new_ids.append(evidence_id)
                    scheduled.add(evidence_id)
                    if not verified:
                        # The review exists but the durable record does not corroborate
                        # it: recorded as an unproven fact, counted nowhere, and listed
                        # so the operator can see what could not be established.
                        unproven.append({"reason": "unverified-review", "evidenceId": evidence_id})
            if batch:
                frontier = int(batch[-1]["review_seq"])
                for row in assessed:
                    profile_key = row["profile_id"]
                    current = cursors[profile_key]
                    if current is not None and current >= frontier:
                        continue
                    # A cursor moves only inside this transaction, only past reviews the
                    # profile has actually evaluated, and never backwards: a failure
                    # above rolls everything back and the same reviews stay eligible.
                    connection.execute(
                        "INSERT INTO evaluation_maintenance_checkpoints(scope, review_seq, updated_at)"
                        " VALUES(?,?,?)"
                        " ON CONFLICT(scope) DO UPDATE SET review_seq=excluded.review_seq,"
                        " updated_at=excluded.updated_at"
                        " WHERE excluded.review_seq > evaluation_maintenance_checkpoints.review_seq",
                        (f"profile:{profile_key}", frontier, now),
                    )
                    cursors[profile_key] = frontier
            profiles = [self._profile_view(row) for row in assessed]
            assessed_marks = ",".join("?" for _ in assessed_ids) or "NULL"
            cards = [
                self._card_view(row)
                for row in connection.execute(f"SELECT * FROM evaluation_cards WHERE profile_id IN ({assessed_marks}) ORDER BY rowid", assessed_ids)
            ]
            # Effective user policy (with its source) and the family notes; both are
            # context for the assessment, never facts it may rewrite.
            preferences = user_policy.effective_preferences(connection, assessed_ids)
            annotations = user_policy.family_annotations(connection, assessed_ids)
            # Every evidence reference a current card depends on is required input and
            # is never dropped to fit the packet.
            referenced_ids: list[str] = []
            for card in cards:
                for evidence_id in card["evidenceIds"]:
                    if evidence_id not in referenced_ids:
                        referenced_ids.append(evidence_id)
            required = list(referenced_ids)
            for evidence_id in new_ids:
                if evidence_id not in required:
                    required.append(evidence_id)
            if len(required) > MAX_PACKET_EVIDENCE:
                raise BoardError(
                    "PACKET_TOO_LARGE",
                    f"the required evidence summaries ({len(required)}) exceed the {MAX_PACKET_EVIDENCE}-entry "
                    "maintenance packet bound; nothing was truncated and nothing was recorded. Narrow the request "
                    "with profileId or a smaller limit, or publish the current cards first.",
                    required=len(required),
                    bound=MAX_PACKET_EVIDENCE,
                )
            included: list[str] = list(required)
            pending_total = 0
            for assessed_id in assessed_ids:
                pending_total += self._pending_evidence_count(connection, assessed_id)
                room = MAX_PACKET_EVIDENCE - len(included)
                if room <= 0:
                    continue
                for evidence_id in self._pending_evidence_ids(connection, assessed_id, room):
                    if evidence_id not in included:
                        included.append(evidence_id)
            evidence: list[dict] = []
            missing: list[str] = []
            for evidence_id in included:
                view = self._packet_evidence(connection, evidence_id)
                if view is None:
                    # A required reference or a pending ledger row without its evidence
                    # is an inconsistent table: refuse instead of dropping the reference.
                    missing.append(evidence_id)
                    continue
                # Pending is a property of the pending ledger, never of "not referenced":
                # an intentionally retired reference is archived, not waiting again.
                view["pending"] = self._is_pending(connection, evidence_id)
                evidence.append(view)
            if missing:
                raise BoardError(
                    "NOT_FOUND",
                    f"required evidence {missing[0]!r} is missing from the evidence ledger; nothing was recorded "
                    "and no reference was dropped",
                    evidenceId=missing[0],
                )
            pending_included = sum(1 for entry in evidence if entry["pending"])
            backlog = self._reviewed_backlog_for(connection, cursors)
            frontier = max((value for value in cursors.values() if value is not None), default=None)
            response = {
                "requestId": request_id,
                "tableRevision": revision,
                "limit": limit,
                "profiles": profiles,
                "cards": cards,
                "preferences": preferences,
                "annotations": annotations,
                "evidence": evidence,
                "pendingEvidenceIds": sorted(entry["evidenceId"] for entry in evidence if entry["pending"]),
                "referencedEvidenceIds": referenced_ids,
                "newEvidenceIds": new_ids,
                "progress": {
                    "scanned": len(batch),
                    "newFacts": len(new_ids),
                    "alreadyPrepared": len(scheduled) - len(new_ids),
                    "scope": scope,
                    "cursors": {
                        profile_key: None if value is None else {"reviewSeq": int(value)}
                        for profile_key, value in sorted(cursors.items())
                    },
                    # A profile whose own cursor is behind the newest one is still
                    # backfilling its history; later calls continue from here.
                    "backfilling": (
                        sorted(
                            profile_key
                            for profile_key, value in cursors.items()
                            if value is None or (frontier is not None and value < frontier)
                        )
                        if backlog
                        else []
                    ),
                    "complete": backlog == 0,
                },
                "remaining": {
                    "pendingEvidence": max(0, pending_total - pending_included),
                    "reviewedBacklog": backlog,
                },
                "skipped": [
                    {"reason": reason, "count": count, "examples": examples.get(reason, [])}
                    for reason, count in sorted(skipped.items())
                ],
                "unproven": unproven,
                "source": {
                    # Discovery is the append-only Host acknowledgement event stream,
                    # ordered by its immutable sequence. A review whose attempt is no
                    # longer current is collected through its matching immutable
                    # task.review_archived record; without that proof it is reported,
                    # never guessed onto the attempt that ran.
                    "reviewLedger": "events(seq) for task.accepted|task.rejected|workflow.acknowledged",
                    "covers": "current and archived Host reviews with an exact attempt-bound output-artifact proof",
                },
                "note": (
                    "Deterministic facts and a bounded packet only: no model was called, no card was published, "
                    "and no task acceptance or Host authority was changed. Synthesize card changes under the skill "
                    "and commit them with evaluation_write_begin(kind maintenance) plus assessment_publish "
                    "at this tableRevision."
                ),
            }
            self.board._append_event(
                connection,
                "evaluation.prepared",
                revision=revision,
                payload={
                    "requestId": request_id,
                    "scope": scope,
                    "scanned": len(batch),
                    "newEvidenceIds": new_ids,
                    "remaining": response["remaining"],
                    "skipped": response["skipped"],
                },
            )
            self.board._store_receipt(connection, request_id, "evaluation.prepare", request, response)
            head = self.board._head_of(connection)
        self.board._notify(head)
        return response

    def history(self, params: dict) -> dict:
        """One bounded newest-first page of publication history.

        A read of the existing ``evaluation_revisions`` rows only: descending revision
        keyset, no model call, no lease and no write. A newer publication never pushes
        an older revision out of reach, and ``total`` is the complete revision count
        independent of the cursor.
        """
        schemas.reject_unknown(params, HISTORY_FIELDS, "evaluation.history")
        limit = schemas.optional_int(params, "limit", DEFAULT_HISTORY_PAGE, 1, MAX_HISTORY_PAGE)
        before = schemas.optional_positive_int(params, "before")
        with self.board.db.read() as connection:
            total = int(
                connection.execute("SELECT COUNT(*) AS count FROM evaluation_revisions").fetchone()["count"]
            )
            query = "SELECT * FROM evaluation_revisions"
            values: list[Any] = []
            if before is not None:
                query += " WHERE revision<?"
                values.append(before)
            query += " ORDER BY revision DESC LIMIT ?"
            values.append(limit)
            rows = connection.execute(query, values).fetchall()
            revisions = [self._revision_view(row) for row in rows]
            next_cursor = None
            if len(rows) == limit:
                last_revision = int(rows[-1]["revision"])
                more = connection.execute(
                    "SELECT 1 FROM evaluation_revisions WHERE revision<? LIMIT 1", (last_revision,)
                ).fetchone()
                if more is not None:
                    next_cursor = last_revision
        return {"revisions": revisions, "nextCursor": next_cursor, "total": total}

    @staticmethod
    def _fact_project(task: sqlite3.Row) -> str | None:
        """The recorded source cwd of a reviewed task, or null when it has none.

        Provenance only: a project never filters assessment, and a managed execution
        worktree is not invented as a project when the original cwd is unknown.
        """
        try:
            spec = json.loads(task["spec_json"])
        except (TypeError, ValueError):  # pragma: no cover - a stored spec is always JSON
            return None
        cwd = spec.get("cwd") if isinstance(spec, dict) else None
        if not isinstance(cwd, str) or not cwd.strip():
            return None
        return cwd.strip()[:200]

    @staticmethod
    def _revision_view(row: sqlite3.Row) -> dict:
        counts = json.loads(row["counts_json"]) if row["counts_json"] else {}
        if not isinstance(counts, dict):
            counts = {}
        view = {
            "revision": int(row["revision"]),
            "kind": row["kind"],
            "actor": row["actor"],
            "counts": {name: int(counts[name]) for name in (
                "profiles", "cards", "preferences", "profileSettings", "preferenceChanges", "annotationChanges",
                "familyPreferenceChanges", "familyAnnotationChanges",
            ) if name in counts},
            "createdAt": row["created_at"],
        }
        provided = counts.get("provided")
        if isinstance(provided, list):
            view["counts"]["provided"] = [value for value in provided if isinstance(value, str)]
        return view

    # -- views ---------------------------------------------------------------
    @staticmethod
    def _profile_view(row: sqlite3.Row) -> dict:
        from .adapters import adapters
        native = adapters().get(row["adapter"])
        capabilities = json.loads(row["capabilities_json"])
        if not (native and native.read_only_structured and native.read_only_structured_verified):
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
        return view

    @staticmethod
    def _card_view(row: sqlite3.Row) -> dict:
        return {
            "profileId": row["profile_id"],
            "origin": row["origin"],
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
