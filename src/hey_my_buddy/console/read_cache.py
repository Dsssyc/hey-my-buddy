"""Change markers and bounded response caching for the console's read routes.

A periodic console read must not recompute board projections when nothing the
projection depends on has changed. This module owns the honesty rules of that
shortcut:

* The database marker is ``PRAGMA data_version`` read on **one persistent
  connection** of this cache, prefixed by the connection's epoch. The value
  only moves when another connection commits, so it covers every committed
  change — including this process's own writes, which always travel through
  their own short-lived connections. A new connection per check would restart
  the baseline and mistake real changes for stillness. Closing and reopening
  the same console reconnects this cache, so its epoch still distinguishes the
  retained entries from the new connection's repeated data_version.
* A projection is generated between two marker reads. If the marker moved in
  between, the body is served uncached. Non-database
  marker components (a callable ``extra_marker``, such as the console's
  session-set fingerprint) follow the same double-read rule.
* Fields derived from the current time do not force per-poll recomputation.
  Instead the projection's own absolute boundaries (provider cooling and
  router skip windows) become a deadline: once
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
import sqlite3
import threading
import time
from collections import OrderedDict
from datetime import datetime
from typing import Callable

#: Maximum lifetime for time-derived fields without an explicit deadline.
#: An idle board recomputes each cached read at most once per hour.
MAX_ENTRY_SECONDS = 3600.0

#: Cached entries per console. Each entry is (route key, session); the bound
#: keeps a pathological client from pinning memory with unique queries.
MAX_ENTRIES = 64


def parse_boundary(value: str | None) -> float | None:
    """One board timestamp as epoch seconds; absent optional boundaries stay absent."""
    if value is None:
        return None
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%S.%f%z").timestamp()


def collect_deadline(projection: dict, *, after: float) -> float:
    """The earliest future boundary from the snapshot's known service producers.

    Discovery records may embed arbitrary timestamps. Only provider cooldowns,
    Router skip windows and the service's own health expiry/scan fields determine
    when this projection changes; nested discovery content is never traversed.
    """
    earliest = float("inf")

    def include(value: str | None) -> None:
        nonlocal earliest
        moment = parse_boundary(value)
        if moment is not None and moment > after:
            earliest = min(earliest, moment)

    for buddy in projection["buddies"]:
        cooling = buddy["providerCooling"]
        if cooling is not None:
            include(cooling["coolingUntil"])
    for harness in projection["harnesses"]:
        include(harness.get("expiresAt"))
        include(harness.get("scanAfter"))
        for provider in harness["providers"]:
            include(provider["coolingUntil"])
    for router in projection["routingHealth"]["routers"]:
        include(router["skipUntil"])
        include(router["retryAt"])
    return earliest


class _MarkerConnection:
    """One persistent SQLite connection whose ``(epoch, data_version)`` is the DB marker.

    Every board write commits through its own short-lived connection, so this
    connection always observes other-connection commits — the only kind there
    is. The connection stays open for this cache's lifetime so the value can
    be compared across reads.
    """

    def __init__(self, database):
        self._database = database
        self._connection: sqlite3.Connection | None = None
        self._epoch = 0
        self._lock = threading.Lock()

    def data_version(self) -> tuple[int, int]:
        with self._lock:
            if self._connection is None:
                self._connection = sqlite3.connect(
                    self._database.path, isolation_level=None, timeout=10, check_same_thread=False
                )
                self._database._configure(self._connection)
                self._epoch += 1
            return (self._epoch, int(self._connection.execute("PRAGMA data_version").fetchone()[0]))

    def close(self) -> None:
        with self._lock:
            if self._connection is not None:
                self._connection.close()
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
        return self.gzip_etag if coding == "gzip" else self.identity_etag

    def representation(self, coding: str) -> bytes:
        """The exact bytes of one representation."""
        return self.gzip if coding == "gzip" else self.body


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


class ReadCache:
    """Marker-checked, session-private response cache for one console."""

    def __init__(self, database, *, clock: Callable[[], float] = time.time, max_entries: int = MAX_ENTRIES):
        self._database = database
        self._clock = clock
        self._max_entries = max_entries
        self._marker_connection = _MarkerConnection(database)
        self._lock = threading.RLock()
        self._entries: OrderedDict[tuple, Entry] = OrderedDict()

    def close(self) -> None:
        self._marker_connection.close()

    def marker(self, extra: tuple | Callable[[], tuple] = ()) -> tuple:
        """The current change marker.

        The database component is ``(epoch, data_version)`` of one persistent
        connection. ``extra`` may be a tuple or a callable returning
        one; a callable is re-evaluated on every read so non-database state
        (for example the console's session set) follows the same
        before/after double-read rule as the database version.
        """
        version = self._marker_connection.data_version()
        if callable(extra):
            extra = extra()
        return (*version, *extra)

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
        cached_key = (kind, key, session)
        with self._lock:
            entry = self._entries.get(cached_key)
            if entry is not None:
                self._entries.move_to_end(cached_key)
        marker = self.marker(extra_marker)
        if (
            entry is not None
            and entry.marker == marker
            and now < entry.deadline
        ):
            if if_none_match is not None and if_none_match_matches(if_none_match, entry.validator(coding)):
                return ReadOutcome(entry, not_modified=True)
            return ReadOutcome(entry)
        before = marker
        projection, hint = generate()
        body = json.dumps(projection, ensure_ascii=False).encode()
        after = self.marker(extra_marker)
        if before == after:
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
        # A concurrent write left this generation without a stable marker.
        return ReadOutcome(Entry(body, (), float("inf"), now))
