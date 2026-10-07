"""Change markers and bounded response caching for the console's read routes.

A periodic console read must not recompute board projections when nothing the
projection depends on has changed. This module owns the honesty rules of that
shortcut:

* The database marker is ``PRAGMA data_version`` read on **one persistent
  connection** of this cache. The value only moves when another connection
  commits, so it covers every committed change — including this process's own
  writes, which always travel through their own short-lived connections. A new
  connection per check would restart the baseline and mistake real changes for
  stillness, which is why the connection lives here and nowhere else.
* A projection is generated between two marker reads. If the marker moved in
  between, the body is never bound to the newer marker; generation retries and
  the final attempt is served uncached rather than guessing. Non-database
  marker components (a callable ``extra_marker``, such as the console's
  session-set fingerprint) follow the same double-read rule.
* Fields derived from the current time do not force per-poll recomputation.
  Instead the projection's own absolute boundaries (lease expiries, retry and
  reset windows, quota staleness, router skip windows) become a deadline: once
  the clock passes it, the marker counts as changed. Boundary collection
  deliberately over-reports — a boundary that only affects display still
  invalidates — because a missed state flip cannot be repaired downstream.
  A conservative maximum lifetime bounds anything the collection fails to see.
* Identity and gzip are different representations, so each carries its own
  strong validator (RFC 9110 §8.8.3: strong entity-tags differ per content
  coding). The caller negotiates the coding first and the conditional check
  then compares against that representation's validator; a validator borrowed
  from another coding never answers 304.
* Entries are private to one console session and one route key. Two sessions
  never share a body, and authentication always runs before any 304 decision.

Nothing here invents a second SQL path or an ``unchanged`` JSON protocol: the
cache stores projection bodies that the same named operations would return, and
the wire contract stays ordinary HTTP (ETag / If-None-Match / 304, gzip).
"""
from __future__ import annotations

import gzip
import hashlib
import json
import os
import sqlite3
import threading
import time
from collections import OrderedDict
from datetime import datetime, timezone
from typing import Any, Callable

#: Keys whose absolute value is a semantic boundary: when the clock passes it,
#: some now-derived field of the projection may flip on its own. ``scanAfter``
#: additionally borrows the harness scan: once it passes, the next periodic read
#: regenerates and kicks the rate-limited scan again instead of letting an idle
#: console starve the 180-second cadence.
BOUNDARY_KEYS = frozenset({"expiresAt", "eligibleAt", "manualAt", "resetsAt", "skipUntil", "retryAt", "scanAfter"})

#: Observation stamps whose projection flips when the record ages past the
#: native quota freshness window (``quota_view`` treats it as stale after one
#: hour). Old stamps fall in the past and are filtered; recent ones bound the
#: cache until their staleness boundary.
STALENESS_AFTER_SECONDS = 3600
STALENESS_KEYS = frozenset({"observedAt"})

#: Safety net for any now-derived field the boundary walk fails to enumerate.
#: An idle board recomputes each cached read at most once per hour.
MAX_ENTRY_SECONDS = 3600.0

#: Cached entries per console. Each entry is (route key, session); the bound
#: keeps a pathological client from pinning memory with unique queries.
MAX_ENTRIES = 64

#: Generation retries before serving an unstable read uncached.
MAX_GENERATION_ATTEMPTS = 3


def parse_boundary(value: Any) -> float | None:
    """One absolute timestamp as epoch seconds, or ``None`` when unparseable."""
    if not isinstance(value, str) or not value:
        return None
    text = value.strip()
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    try:
        moment = datetime.fromisoformat(text)
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.timestamp()


def collect_deadline(projection: Any, *, after: float, known: float | None = None) -> float:
    """The earliest future boundary inside a projection, as epoch seconds.

    Walks every dict and list looking for the boundary keys above, comparing
    against ``after`` (the generation clock). A key that only feeds display
    still counts: over-reporting costs one recomputation, while a missed flip
    would freeze state that must expire. ``known`` seeds the result with an
    already-established boundary such as the gate lease expiry.
    """
    earliest = known if known is not None else float("inf")

    def visit(node: Any) -> None:
        nonlocal earliest
        if isinstance(node, dict):
            for key, value in node.items():
                if key in BOUNDARY_KEYS or key in STALENESS_KEYS:
                    moment = parse_boundary(value)
                    if moment is not None:
                        if key in STALENESS_KEYS:
                            # A quota observation ages stale one hour after it was
                            # taken, so its boundary can still be ahead even when
                            # the stamp itself already lies in the past.
                            moment += STALENESS_AFTER_SECONDS
                        if moment > after:
                            earliest = min(earliest, moment)
                else:
                    visit(value)
        elif isinstance(node, list):
            for item in node:
                visit(item)

    visit(projection)
    return earliest


def gate_lease_deadline(database, now: float) -> float | None:
    """The earliest evaluation-gate lease expiry after ``now``, or ``None``.

    The gate view counts only leases that have not expired, so its phase and
    counts flip exactly when the earliest active writer, waiting writer or
    admitted reader lease passes. Those rows are cheap to bound in SQL; the
    stored stamps are the same normalized ISO text used for the comparison.
    """
    earliest: float | None = None
    now_text = datetime.fromtimestamp(now, timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    try:
        with database.read() as connection:
            for statement in (
                "SELECT MIN(expires_at) AS boundary FROM evaluation_writers"
                " WHERE state IN ('active','waiting') AND expires_at > ?",
                "SELECT MIN(expires_at) AS boundary FROM evaluation_readers"
                " WHERE released_at IS NULL AND expires_at > ?",
            ):
                row = connection.execute(statement, (now_text,)).fetchone()
                moment = parse_boundary(row["boundary"]) if row is not None and row["boundary"] else None
                if moment is not None and moment > now:
                    earliest = moment if earliest is None else min(earliest, moment)
    except sqlite3.Error:
        return None
    return earliest


class _MarkerConnection:
    """One persistent SQLite connection whose ``data_version`` is the DB marker.

    Every board write commits through its own short-lived connection, so this
    connection always observes other-connection commits — the only kind there
    is. An open Unix connection silently keeps reading a replaced database
    file, so the file identity (device, inode) is recorded when the connection
    opens and re-checked on every read: a path that now names a different file
    (a restore or rollback that swapped the database underneath) closes the
    connection and reports ``None``, which callers treat as "changed". An OS
    error does the same.
    """

    def __init__(self, database):
        self._database = database
        self._connection: sqlite3.Connection | None = None
        self._identity: tuple[int, int] | None = None
        self._lock = threading.Lock()

    def data_version(self) -> int | None:
        with self._lock:
            try:
                if self._connection is None:
                    self._connection = sqlite3.connect(
                        self._database.path, isolation_level=None, timeout=10, check_same_thread=False
                    )
                    self._database._configure(self._connection)
                    self._identity = self._path_identity()
                elif self._path_identity() != self._identity:
                    self._close_locked()
                    return None
                return int(self._connection.execute("PRAGMA data_version").fetchone()[0])
            except (sqlite3.Error, OSError):
                self._close_locked()
                return None

    def _path_identity(self) -> tuple[int, int] | None:
        """The (device, inode) the marker's path currently names, or ``None``."""
        try:
            information = os.stat(self._database.path)
        except OSError:
            return None
        return (information.st_dev, information.st_ino)

    def close(self) -> None:
        with self._lock:
            self._close_locked()

    def _close_locked(self) -> None:
        self._identity = None
        if self._connection is not None:
            try:
                self._connection.close()
            except sqlite3.Error:
                pass
            self._connection = None


class Entry:
    """One cached projection with a strong validator for each representation.

    Identity and gzip are separate representations of the same state, so each
    keeps its own validator over its own exact bytes (RFC 9110 §8.8.3). The
    gzip encoding is deterministic (fixed mtime), which keeps the gzip
    validator stable for a stable identity body.
    """

    __slots__ = ("body", "gzip", "identity_etag", "gzip_etag", "marker", "deadline", "created_at")

    def __init__(self, body: bytes, marker: tuple, deadline: float, now: float):
        self.body = body
        self.gzip = gzip_body(body)
        self.identity_etag = '"' + hashlib.sha256(body).hexdigest()[:32] + '"'
        self.gzip_etag = '"' + hashlib.sha256(self.gzip).hexdigest()[:32] + '"'
        self.marker = marker
        self.deadline = deadline
        self.created_at = now

    def validator(self, coding: str) -> str:
        """The strong validator of one representation, quoted per RFC 9110."""
        if coding == "gzip":
            return self.gzip_etag
        if coding == "identity":
            return self.identity_etag
        raise ValueError(f"unknown representation coding: {coding!r}")

    def representation(self, coding: str) -> bytes:
        """The exact bytes of one representation."""
        if coding == "gzip":
            return self.gzip
        if coding == "identity":
            return self.body
        raise ValueError(f"unknown representation coding: {coding!r}")


def gzip_body(body: bytes) -> bytes:
    """Deterministic gzip (fixed mtime) so repeated encodings compare equal."""
    return gzip.compress(body, compresslevel=6, mtime=0)


def select_encoding(accept_encoding: list[str] | None) -> str:
    """Choose ``gzip``, ``identity`` or ``" unacceptable"`` from Accept-Encoding.

    Follows the negotiation contract the plan fixes: q=0 withdraws an encoding,
    ``*`` speaks for unnamed ones, and neither-representable is reported as a
    leading space so the caller answers 406 without a second parser.
    """
    values: list[str] = []
    for header in accept_encoding or []:
        values.extend(part.strip() for part in header.split(",") if part.strip())
    if not values:
        return "identity"
    quality: dict[str, float] = {}
    for item in values:
        name, _, parameter = item.partition(";")
        token = name.strip().lower()
        q = 1.0
        if parameter.strip().lower().startswith("q="):
            try:
                q = float(parameter.split("=", 1)[1].strip())
            except ValueError:
                q = 1.0
        quality[token] = max(quality.get(token, 0.0), q)
    wildcard = quality.get("*")
    # An explicit coding list speaks only for the codings it names: gzip needs
    # its own token or the wildcard. Identity is acceptable by default unless
    # it (or the wildcard) is explicitly withdrawn.
    gzip_q = quality.get("gzip", wildcard if wildcard is not None else 0.0)
    identity_q = quality.get("identity", wildcard if wildcard is not None else 1.0)
    if gzip_q > 0:
        return "gzip"
    if identity_q > 0:
        return "identity"
    return " unacceptable"


def encoding_unacceptable(choice: str) -> bool:
    return choice.startswith(" ")


def if_none_match_matches(header: str | None, etag: str) -> bool:
    """RFC 9110 If-None-Match: list membership, weak-prefix tolerant, ``*``."""
    if header is None:
        return False
    for candidate in header.split(","):
        token = candidate.strip()
        if not token:
            continue
        if token == "*":
            return True
        if token.startswith("W/"):
            token = token[2:].strip()
        if token == etag:
            return True
    return False


class ReadOutcome:
    """What a read route should put on the wire."""

    __slots__ = ("entry", "not_modified")

    def __init__(self, entry: Entry | None, *, not_modified: bool = False):
        self.entry = entry
        self.not_modified = not_modified

    @property
    def value(self) -> dict:
        return json.loads(self.entry.body)


def uncached_read(body: bytes, *, if_none_match: str | None, coding: str) -> ReadOutcome:
    """A conditional outcome for a body that is recomputed on every read.

    Used by routes whose content is always regenerated (the on-demand backup
    preflight): only the transfer is conditional, but the validator and the
    negotiated representation follow the same per-coding rules as the cache.
    """
    entry = Entry(body, (), float("inf"), 0.0)
    return ReadOutcome(entry, not_modified=if_none_match_matches(if_none_match, entry.validator(coding)))


class ReadCache:
    """Marker-checked, session-private response cache for one console."""

    def __init__(self, database, *, clock: Callable[[], float] = time.time, max_entries: int = MAX_ENTRIES):
        self._database = database
        self._clock = clock
        self._max_entries = max_entries
        self._marker_connection = _MarkerConnection(database)
        self._lock = threading.RLock()
        self._entries: OrderedDict[tuple, Entry] = OrderedDict()
        #: Per-route generation counters, in-process evidence for tests and the
        #: measurement script: a cache hit must not move them.
        self.calls: dict[str, dict[str, int]] = {}

    def close(self) -> None:
        self._marker_connection.close()

    def _count(self, kind: str, name: str) -> None:
        counts = self.calls.setdefault(kind, {"projections": 0, "serializations": 0})
        counts[name] = counts.get(name, 0) + 1

    def marker(self, extra: tuple | Callable[[], tuple] = ()) -> tuple | None:
        """The current change marker, or ``None`` when it cannot be trusted.

        ``extra`` may be a tuple or a callable returning one; a callable is
        re-evaluated on every read so non-database state (for example the
        console's session set) follows the same before/after double-read rule
        as the database version.
        """
        version = self._marker_connection.data_version()
        if version is None:
            return None
        if callable(extra):
            extra = extra()
        return (version, *extra)

    def serve(
        self,
        *,
        kind: str,
        key: str,
        session: str,
        extra_marker: tuple | Callable[[], tuple],
        generate: Callable[[], tuple[dict, float | None]],
        if_none_match: str | None = None,
        coding: str = "identity",
        max_age: float = MAX_ENTRY_SECONDS,
    ) -> ReadOutcome:
        """One conditional read: a cached hit answers without regenerating.

        ``generate`` returns ``(projection, deadline_hint)`` where the hint is
        epoch seconds of the earliest time-derived boundary the projection
        itself knows; leases and the conservative maximum are added here.
        ``coding`` is the already-negotiated representation; the conditional
        check compares ``if_none_match`` against that representation's own
        validator, so a validator from another coding never answers 304.
        ``extra_marker`` may be a callable; it is re-evaluated for every marker
        read so non-database state follows the same before/after rule as the
        database version.
        """
        now = self._clock()
        if coding not in ("identity", "gzip"):
            raise ValueError(f"unknown representation coding: {coding!r}")
        cached_key = (kind, key, session)
        with self._lock:
            entry = self._entries.get(cached_key)
            if entry is not None:
                self._entries.move_to_end(cached_key)
        marker = self.marker(extra_marker)
        if (
            entry is not None
            and marker is not None
            and entry.marker == marker
            and now < entry.deadline
        ):
            if if_none_match is not None and if_none_match_matches(if_none_match, entry.validator(coding)):
                return ReadOutcome(entry, not_modified=True)
            return ReadOutcome(entry)
        for _ in range(MAX_GENERATION_ATTEMPTS):
            before = self.marker(extra_marker)
            self._count(kind, "projections")
            projection, hint = generate()
            self._count(kind, "serializations")
            body = json.dumps(projection, ensure_ascii=False).encode()
            after = self.marker(extra_marker)
            if before is None or after is None or before != after:
                continue
            boundaries = [now + max_age]
            if hint is not None:
                boundaries.append(hint)
            deadline = min(boundaries)
            entry = Entry(body, before, deadline, now)
            with self._lock:
                self._entries[cached_key] = entry
                self._entries.move_to_end(cached_key)
                while len(self._entries) > self._max_entries:
                    self._entries.popitem(last=False)
            if if_none_match is not None and if_none_match_matches(if_none_match, entry.validator(coding)):
                return ReadOutcome(entry, not_modified=True)
            return ReadOutcome(entry)
        # The board kept changing under generation; answer with fresh bytes that
        # no marker vouches for rather than caching a torn projection.
        self._count(kind, "projections")
        projection, _hint = generate()
        self._count(kind, "serializations")
        return ReadOutcome(Entry(json.dumps(projection, ensure_ascii=False).encode(), (), float("inf"), now))

    def invalidate(self) -> None:
        """Drop every entry (used when console-level state shifts underneath)."""
        with self._lock:
            self._entries.clear()
