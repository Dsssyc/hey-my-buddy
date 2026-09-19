"""Schema-versioned SQLite storage for the Buddy blackboard.

One database file owns every authoritative fact: tasks, attempts, workers,
messages, artifacts, events, command receipts and resource claims. Connections are
opened per operation, transactions are short, and no transaction ever spans RPC, a
subprocess, an LLM call or an event wait.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

SCHEMA_VERSION = 5
DB_FILE = "board.sqlite3"
SECRET_KEY = "capability_secret"
CAPABILITY_VERSION = 1

#: Every task state the durable model may hold. See ``docs/board.md`` for the
#: documented transition table; ``store.py`` enforces it.
TASK_STATES = (
    "queued",
    "running",
    "cancelling",
    "completed",
    "failed",
    "cancelled",
    "reconciliation-needed",
)
TERMINAL_TASK_STATES = frozenset({"completed", "failed", "cancelled"})

ATTEMPT_STATES = ("starting", "executing", "finalizing", "uncertain", "finished")
ACTIVE_ATTEMPT_STATES = frozenset({"starting", "executing", "finalizing", "uncertain"})

MESSAGE_STATES = ("queued", "claimed", "delivered", "answered", "discarded", "unavailable")
TERMINAL_MESSAGE_STATES = frozenset({"answered", "discarded", "unavailable"})

WORKER_STATES = ("starting", "idle", "busy", "stopping", "lost")

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tasks (
    task_id                 TEXT PRIMARY KEY,
    request_id              TEXT NOT NULL UNIQUE,
    owner                   TEXT NOT NULL,
    spec_json               TEXT NOT NULL,
    spec_canonical_json     TEXT NOT NULL,
    input_fingerprint       TEXT NOT NULL,
    fingerprint_version     INTEGER NOT NULL,
    adapter                 TEXT NOT NULL,
    required_capabilities   TEXT NOT NULL DEFAULT '[]',
    cwd                     TEXT NOT NULL,
    exclusive_resources     TEXT NOT NULL DEFAULT '[]',
    timeout_seconds         INTEGER NOT NULL,
    state                   TEXT NOT NULL
        CHECK (state IN ('queued','running','cancelling','completed','failed','cancelled','reconciliation-needed')),
    queue_reason            TEXT,
    revision                INTEGER NOT NULL,
    selected_attempt_id     TEXT,
    active_attempt_id       TEXT,
    created_at              TEXT NOT NULL,
    updated_at              TEXT NOT NULL,
    accepted_at             TEXT,
    acceptance_note         TEXT,
    acceptance_verdict      TEXT,
    legacy                  INTEGER NOT NULL DEFAULT 0,
    legacy_fingerprint      TEXT,
    legacy_fingerprint_version INTEGER,
    queue_position          INTEGER
);
CREATE INDEX IF NOT EXISTS tasks_state_idx ON tasks(state, created_at);
CREATE INDEX IF NOT EXISTS tasks_request_idx ON tasks(request_id);

CREATE TABLE IF NOT EXISTS attempts (
    attempt_id          TEXT PRIMARY KEY,
    task_id             TEXT NOT NULL REFERENCES tasks(task_id) ON DELETE RESTRICT,
    generation          INTEGER NOT NULL,
    worker_id           TEXT,
    worker_identity     TEXT,
    worker_instance     TEXT,
    capability_version  INTEGER NOT NULL DEFAULT 1,
    nonce_verifier      TEXT NOT NULL,
    claim_request_id    TEXT NOT NULL,
    lease_expires_at    TEXT,
    lease_seconds       INTEGER NOT NULL DEFAULT 120,
    execution_state     TEXT NOT NULL
        CHECK (execution_state IN ('starting','executing','finalizing','uncertain','finished')),
    ownership           TEXT NOT NULL DEFAULT 'owned' CHECK (ownership IN ('owned','uncertain')),
    runtime_identity    TEXT,
    adapter             TEXT NOT NULL,
    log_paths           TEXT NOT NULL DEFAULT '{}',
    result_json         TEXT,
    result_command_id   TEXT,
    error               TEXT,
    shutdown_confirmed  INTEGER NOT NULL DEFAULT 0,
    cancel_requested_at TEXT,
    exit_code           INTEGER,
    signal              TEXT,
    started_at          TEXT,
    finished_at         TEXT,
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL,
    revision            INTEGER NOT NULL DEFAULT 1,
    UNIQUE(task_id, generation)
);
CREATE INDEX IF NOT EXISTS attempts_task_idx ON attempts(task_id, generation);
-- Relational guarantee of the documented invariant: at most one effective active
-- attempt per task. An unconfirmed (uncertain) attempt keeps its slot, so a
-- replacement cannot be created while a survivor may still be running.
CREATE UNIQUE INDEX IF NOT EXISTS attempts_effective_unique ON attempts(task_id)
    WHERE execution_state IN ('starting','executing','finalizing','uncertain');
CREATE INDEX IF NOT EXISTS attempts_active_idx ON attempts(execution_state);

CREATE TABLE IF NOT EXISTS workers (
    worker_id           TEXT PRIMARY KEY,
    identity            TEXT NOT NULL,
    adapter             TEXT NOT NULL,
    capabilities        TEXT NOT NULL DEFAULT '[]',
    host                TEXT,
    pid                 INTEGER,
    state               TEXT NOT NULL,
    current_attempt_id  TEXT,
    registered_at       TEXT NOT NULL,
    last_seen_at        TEXT NOT NULL,
    revision            INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS workers_state_idx ON workers(state);

CREATE TABLE IF NOT EXISTS messages (
    message_id      TEXT PRIMARY KEY,
    task_id         TEXT NOT NULL REFERENCES tasks(task_id) ON DELETE RESTRICT,
    attempt_id      TEXT REFERENCES attempts(attempt_id) ON DELETE RESTRICT,
    inquiry_id      TEXT NOT NULL,
    direction       TEXT NOT NULL,
    author          TEXT NOT NULL,
    recipient       TEXT,
    correlation_id  TEXT,
    body            TEXT NOT NULL,
    body_bytes      INTEGER NOT NULL,
    payload_hash    TEXT NOT NULL,
    state           TEXT NOT NULL
        CHECK (state IN ('queued','claimed','delivered','answered','discarded','unavailable')),
    reason          TEXT,
    delivery_json   TEXT,
    answer_json     TEXT,
    attempts_count  INTEGER NOT NULL DEFAULT 0,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    revision        INTEGER NOT NULL DEFAULT 1,
    UNIQUE(task_id, inquiry_id)
);
CREATE INDEX IF NOT EXISTS messages_task_idx ON messages(task_id, created_at);
CREATE INDEX IF NOT EXISTS messages_state_idx ON messages(state);

CREATE TABLE IF NOT EXISTS artifacts (
    artifact_id     TEXT PRIMARY KEY,
    task_id         TEXT NOT NULL REFERENCES tasks(task_id) ON DELETE RESTRICT,
    attempt_id      TEXT REFERENCES attempts(attempt_id) ON DELETE RESTRICT,
    kind            TEXT NOT NULL,
    location        TEXT NOT NULL,
    content_hash    TEXT NOT NULL,
    size_bytes      INTEGER NOT NULL,
    verified        INTEGER NOT NULL DEFAULT 0,
    created_at      TEXT NOT NULL,
    UNIQUE(attempt_id, location, content_hash)
);
CREATE INDEX IF NOT EXISTS artifacts_task_idx ON artifacts(task_id);

CREATE TABLE IF NOT EXISTS events (
    seq          INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id      TEXT REFERENCES tasks(task_id) ON DELETE RESTRICT,
    attempt_id   TEXT,
    revision     INTEGER,
    kind         TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS events_task_idx ON events(task_id, seq);

CREATE TABLE IF NOT EXISTS commands (
    command_id      TEXT PRIMARY KEY,
    task_id         TEXT,
    attempt_id      TEXT,
    kind            TEXT NOT NULL,
    request_hash    TEXT NOT NULL,
    response_json   TEXT NOT NULL,
    subject         TEXT,
    created_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS commands_kind_idx ON commands(kind, created_at);

CREATE TABLE IF NOT EXISTS resource_claims (
    claim_id    TEXT PRIMARY KEY,
    task_id     TEXT NOT NULL REFERENCES tasks(task_id) ON DELETE RESTRICT,
    attempt_id  TEXT,
    resource    TEXT NOT NULL,
    kind        TEXT NOT NULL,
    state       TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    released_at TEXT,
    UNIQUE(task_id, resource)
);
CREATE INDEX IF NOT EXISTS resource_claims_state_idx ON resource_claims(state, kind);

CREATE TABLE IF NOT EXISTS cursors (
    consumer   TEXT PRIMARY KEY,
    seq        INTEGER NOT NULL,
    updated_at TEXT NOT NULL
);
"""


def utc_now() -> str:
    """Canonical timestamp used for every durable row."""
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def canonical_json(value) -> str:
    """Deterministic JSON for fingerprints and payload hashing."""
    return json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":"))


def node_json(value) -> str:
    """Reproduce JavaScript ``JSON.stringify`` for a flat object.

    Legacy specification hashes were produced by Node with insertion-ordered keys
    and no whitespace. This is the only place a Node-compatible string is needed:
    importing legacy records and answering identical legacy ``start`` requests.
    """
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))


class Corruption(RuntimeError):
    """The database exists but cannot be trusted or migrated."""


class Database:
    """Owns the SQLite file, its pragmas, schema migration and the capability secret."""

    def __init__(self, directory: Path):
        self.directory = Path(directory)
        self.path = self.directory / DB_FILE
        self._lock = threading.Lock()
        self._secret: bytes | None = None

    # -- connections ---------------------------------------------------------
    def _configure(self, connection: sqlite3.Connection) -> None:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=FULL")
        connection.execute("PRAGMA busy_timeout=10000")
        connection.execute("PRAGMA trusted_schema=OFF")

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        """A fresh connection with the durable pragmas applied."""
        connection = sqlite3.connect(self.path, isolation_level=None, timeout=10)
        try:
            self._configure(connection)
            yield connection
        finally:
            connection.close()

    @contextmanager
    def write(self, immediate: bool = True) -> Iterator[sqlite3.Connection]:
        """One short write transaction; rolled back on any exception."""
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE" if immediate else "BEGIN")
            try:
                yield connection
            except BaseException:
                connection.execute("ROLLBACK")
                raise
            else:
                connection.execute("COMMIT")

    @contextmanager
    def read(self) -> Iterator[sqlite3.Connection]:
        """One short read transaction, so a multi-query view is internally coherent."""
        with self.connect() as connection:
            connection.execute("BEGIN")
            try:
                yield connection
            except BaseException:
                connection.execute("ROLLBACK")
                raise
            else:
                connection.execute("COMMIT")

    # -- lifecycle -----------------------------------------------------------
    def initialize(self) -> None:
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(self.directory, 0o700)
        with self._lock:
            fresh = not self.path.exists()
            with self.connect() as connection:
                if not fresh:
                    # Refuse before any DDL: an unknown layout is never rewritten.
                    existing = connection.execute(
                        "SELECT name FROM sqlite_master WHERE type='table' AND name='meta'"
                    ).fetchone()
                    if existing is not None:
                        version = connection.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
                        if version is None:
                            raise Corruption(
                                "Board database has a meta table without a schema_version; manual recovery is required"
                            )
                        found = int(version["value"])
                        if found != SCHEMA_VERSION:
                            raise Corruption(
                                f"Board database schema version {found} does not match this Buddy build "
                                f"({SCHEMA_VERSION}); activate a matching version instead of rewriting records"
                            )
                connection.executescript(SCHEMA)
                version = connection.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
                if version is None:
                    connection.execute("BEGIN IMMEDIATE")
                    try:
                        connection.execute(
                            "INSERT INTO meta(key, value) VALUES('schema_version', ?)", (str(SCHEMA_VERSION),)
                        )
                        connection.execute(
                            "INSERT INTO meta(key, value) VALUES('created_at', ?)", (utc_now(),)
                        )
                        connection.execute("COMMIT")
                    except BaseException:
                        connection.execute("ROLLBACK")
                        raise
                if fresh:
                    os.chmod(self.path, 0o600)
                integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
                if integrity != "ok":
                    raise Corruption(f"Board database integrity check failed: {integrity}")
                violations = connection.execute("PRAGMA foreign_key_check").fetchall()
                if violations:
                    # Refuse to serve from a database whose relational invariants are
                    # already broken instead of building on corrupt facts.
                    raise Corruption(
                        f"Board database has {len(violations)} foreign-key violation(s); manual recovery is required"
                    )
        self._load_secret()

    def _load_secret(self) -> bytes:
        if self._secret is not None:
            return self._secret
        with self.write() as connection:
            row = connection.execute("SELECT value FROM meta WHERE key=?", (SECRET_KEY,)).fetchone()
            if row is None:
                value = secrets.token_hex(32)
                connection.execute("INSERT INTO meta(key, value) VALUES(?, ?)", (SECRET_KEY, value))
            else:
                value = row["value"]
        self._secret = bytes.fromhex(value)
        return self._secret

    @property
    def secret(self) -> bytes:
        return self._load_secret()

    def capability(self, attempt_id: str, generation: int, nonce: str) -> str:
        """Derive one attempt capability so a lost claim reply stays recoverable.

        The value is deterministic in (service secret, attempt, generation, worker
        nonce) and is never stored in plaintext: only the worker that fsynced its
        nonce before claiming can ask for it again.
        """
        message = f"{CAPABILITY_VERSION}:{attempt_id}:{generation}:{nonce}".encode("utf-8")
        return hmac.new(self.secret, message, hashlib.sha256).hexdigest()

    def nonce_verifier(self, nonce: str) -> str:
        return hmac.new(self.secret, f"nonce:{nonce}".encode("utf-8"), hashlib.sha256).hexdigest()

    def verify_capability(self, attempt: sqlite3.Row | dict, capability: str, nonce: str | None = None) -> bool:
        candidate = nonce if nonce is not None else ""
        expected = self.capability(attempt["attempt_id"], attempt["generation"], candidate)
        if hmac.compare_digest(expected, capability or ""):
            return True
        # A caller that cannot present the nonce is checked against the stored
        # verifier for the same attempt, never against another attempt's value.
        return False

    def meta(self, key: str) -> str | None:
        with self.read() as connection:
            row = connection.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return row["value"] if row else None

    def set_meta(self, key: str, value: str) -> None:
        with self.write() as connection:
            connection.execute(
                "INSERT INTO meta(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, value),
            )
