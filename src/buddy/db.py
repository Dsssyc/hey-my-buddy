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

SCHEMA_VERSION = 15
#: The one earlier schema that ``upgrade`` migrates in place (see ``migrations``).
PREVIOUS_SCHEMA_VERSION = 14
DB_FILE = "board.sqlite3"
SECRET_KEY = "capability_secret"
CAPABILITY_VERSION = 1
WRITER_TOKEN_VERSION = 1
CONTROL_TOKEN_VERSION = 1
AGENT_TOKEN_VERSION = 1

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

#: Why an attempt really stopped. A user cancellation, an execution deadline, a
#: harness failure, a transport failure and a genuine completion are separate
#: facts; a completion that beat a cancellation is never relabelled by the race.
TERMINATION_REASONS = ("completed", "user-cancel", "deadline", "harness-error", "transport-error")

WORKER_STATES = ("starting", "idle", "busy", "stopping", "lost")

CORE_SCHEMA = """
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
    -- Frozen model-family identity (adapter/provider/model, no effort dimension).
    -- Written once by the claim transaction that resolved it and never updated
    -- afterwards, so a historical or shutdown-uncertain attempt can never change
    -- which family's concurrency slot it occupies. NULL for model-less work
    -- (command/external) which counts only against the machine-wide ceiling.
    model_adapter       TEXT,
    model_provider      TEXT,
    model_model         TEXT,
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
    token_usage_json    TEXT,
    UNIQUE(task_id, generation)
);
CREATE INDEX IF NOT EXISTS attempts_task_idx ON attempts(task_id, generation);
-- Relational guarantee of the documented invariant: at most one effective active
-- attempt per task. An unconfirmed (uncertain) attempt keeps its slot, so a
-- replacement cannot be created while a survivor may still be running.
CREATE UNIQUE INDEX IF NOT EXISTS attempts_effective_unique ON attempts(task_id)
    WHERE execution_state IN ('starting','executing','finalizing','uncertain');
CREATE INDEX IF NOT EXISTS attempts_active_idx ON attempts(execution_state);
CREATE INDEX IF NOT EXISTS attempts_model_family_idx
    ON attempts(model_adapter, model_provider, model_model) WHERE model_model IS NOT NULL;

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
CREATE INDEX IF NOT EXISTS events_decision_status_idx ON events(kind, seq DESC)
    WHERE kind IN ('decision.completed','decision.failed','decision.needs_host','decision.cancelled','decision.stale');

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

-- One bounded latest activity projection per attempt. It is a projection, not a
-- history: a native controller publishes phase/tool/waiting observations through
-- the attempt-private sidecar, the Worker forwards them, and the newest monotone
-- receipt replaces the previous row. The row is bound to the attempt generation and
-- is never inherited by a replacement attempt.
CREATE TABLE IF NOT EXISTS attempt_activity (
    attempt_id     TEXT PRIMARY KEY REFERENCES attempts(attempt_id) ON DELETE RESTRICT,
    task_id        TEXT NOT NULL REFERENCES tasks(task_id) ON DELETE RESTRICT,
    generation     INTEGER NOT NULL,
    event_seq      INTEGER,
    observed_at    TEXT,
    activity_json  TEXT NOT NULL,
    created_at     TEXT NOT NULL,
    updated_at     TEXT NOT NULL,
    revision       INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS attempt_activity_task_idx ON attempt_activity(task_id, updated_at);
"""

#: Current evaluation tables: bounded assessments, reader/writer admission and
#: recorded discovery/decision history.
EVALUATION_TABLES = (
    """
CREATE TABLE IF NOT EXISTS catalog_observations (
    observation_id INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL,
    completed_at TEXT,
    response_json TEXT
);
CREATE TABLE IF NOT EXISTS catalog_current (
    adapter TEXT PRIMARY KEY,
    observation_id INTEGER NOT NULL,
    discovery_id TEXT,
    status TEXT NOT NULL,
    reason TEXT,
    updated_at TEXT NOT NULL
);
""",
    """
CREATE TABLE IF NOT EXISTS family_annotations (
    adapter    TEXT NOT NULL,
    provider   TEXT NOT NULL,
    model      TEXT NOT NULL,
    text       TEXT NOT NULL,
    revision   INTEGER NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (adapter, provider, model)
);
""",
    """
CREATE TABLE IF NOT EXISTS evaluation_state (
    id                      INTEGER PRIMARY KEY CHECK (id = 1),
    table_revision          INTEGER NOT NULL DEFAULT 0,
    configuration_revision  INTEGER NOT NULL DEFAULT 0,
    decision_profile_id     TEXT,
    writer_sequence         INTEGER NOT NULL DEFAULT 0,
    created_at              TEXT NOT NULL,
    updated_at              TEXT NOT NULL
);
""",
    """
CREATE TABLE IF NOT EXISTS evaluation_revisions (
    revision     INTEGER PRIMARY KEY,
    kind         TEXT NOT NULL,
    writer_id    TEXT,
    actor        TEXT,
    counts_json  TEXT NOT NULL DEFAULT '{}',
    created_at   TEXT NOT NULL
);
""",
    """
CREATE TABLE IF NOT EXISTS evaluation_profiles (
    profile_id          TEXT PRIMARY KEY,
    label               TEXT NOT NULL,
    adapter             TEXT NOT NULL,
    provider            TEXT NOT NULL,
    model               TEXT NOT NULL,
    effort              TEXT NOT NULL,
    available           INTEGER NOT NULL DEFAULT 0,
    enabled             INTEGER NOT NULL DEFAULT 1,
    capabilities_json   TEXT NOT NULL DEFAULT '[]',
    context_window      INTEGER,
    description         TEXT NOT NULL DEFAULT '',
    source              TEXT NOT NULL DEFAULT '',
    unavailable_reason  TEXT,
    created_revision    INTEGER NOT NULL,
    updated_revision    INTEGER NOT NULL
);
""",
    """
CREATE TABLE IF NOT EXISTS evaluation_cards (
    profile_id        TEXT PRIMARY KEY REFERENCES evaluation_profiles(profile_id) ON DELETE RESTRICT,
    revision          INTEGER NOT NULL,
    summary           TEXT NOT NULL DEFAULT '',
    origin            TEXT NOT NULL DEFAULT 'unattributed' CHECK(origin IN ('maintenance','unattributed')),
    strengths_json    TEXT NOT NULL DEFAULT '[]',
    limitations_json  TEXT NOT NULL DEFAULT '[]',
    risks_json        TEXT NOT NULL DEFAULT '[]',
    evidence_ids_json TEXT NOT NULL DEFAULT '[]',
    sample_count      INTEGER NOT NULL DEFAULT 0,
    updated_at        TEXT NOT NULL
);
""",
    """
CREATE INDEX IF NOT EXISTS evaluation_profiles_current_idx ON evaluation_profiles(available, profile_id);
CREATE INDEX IF NOT EXISTS evaluation_profiles_adapter_idx ON evaluation_profiles(adapter, profile_id);
""",
    """
CREATE TABLE IF NOT EXISTS evaluation_preferences (
    profile_id        TEXT PRIMARY KEY REFERENCES evaluation_profiles(profile_id) ON DELETE RESTRICT,
    mode              TEXT NOT NULL CHECK (mode IN ('prefer','pin','exclude','none')),
    reason            TEXT NOT NULL DEFAULT '',
    updated_revision  INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS evaluation_preferences_mode_idx ON evaluation_preferences(mode, profile_id);
""",
    """
CREATE TABLE IF NOT EXISTS evaluation_evidence (
    evidence_id       TEXT PRIMARY KEY,
    profile_id        TEXT NOT NULL REFERENCES evaluation_profiles(profile_id) ON DELETE RESTRICT,
    kind              TEXT NOT NULL,
    summary           TEXT NOT NULL,
    project           TEXT,
    conditions_json   TEXT NOT NULL DEFAULT '[]',
    source            TEXT NOT NULL,
    run_id            TEXT,
    verified          INTEGER NOT NULL DEFAULT 0,
    counted           INTEGER NOT NULL DEFAULT 0,
    identity_json     TEXT NOT NULL DEFAULT '{}',
    recorded_revision INTEGER NOT NULL,
    created_at        TEXT NOT NULL
);
""",
    """
CREATE INDEX IF NOT EXISTS evaluation_evidence_profile_idx
    ON evaluation_evidence(profile_id, created_at);
""",
    """
CREATE TABLE IF NOT EXISTS evaluation_decisions (
    decision_id       TEXT PRIMARY KEY,
    status            TEXT NOT NULL,
    task              TEXT NOT NULL,
    profile_id        TEXT,
    table_revision    INTEGER NOT NULL,
    reason            TEXT NOT NULL DEFAULT '',
    evidence_ids_json TEXT NOT NULL DEFAULT '[]',
    created_at        TEXT NOT NULL,
    error             TEXT
);
""",
    """
CREATE TABLE IF NOT EXISTS evaluation_catalog (
    discovery_id      TEXT PRIMARY KEY,
    discovered_at     TEXT NOT NULL,
    source            TEXT NOT NULL,
    harness_version   TEXT,
    provider_version  TEXT,
    payload_json      TEXT NOT NULL,
    created_at        TEXT NOT NULL
);
""",
    """
CREATE TABLE IF NOT EXISTS evaluation_readers (
    reader_id     TEXT PRIMARY KEY,
    kind          TEXT NOT NULL,
    admitted_at   TEXT NOT NULL,
    expires_at    TEXT NOT NULL,
    released_at   TEXT,
    expired       INTEGER NOT NULL DEFAULT 0
);
""",
    """
CREATE INDEX IF NOT EXISTS evaluation_readers_open_idx ON evaluation_readers(released_at, expires_at);
""",
    """
CREATE TABLE IF NOT EXISTS evaluation_writers (
    writer_id          TEXT PRIMARY KEY,
    request_id         TEXT NOT NULL UNIQUE,
    kind               TEXT NOT NULL CHECK (kind IN ('human','maintenance')),
    state              TEXT NOT NULL CHECK (state IN ('waiting','active','published','aborted','expired')),
    generation         INTEGER NOT NULL,
    expected_revision  INTEGER NOT NULL,
    token_verifier     TEXT NOT NULL,
    requested_at       TEXT NOT NULL,
    granted_at         TEXT,
    expires_at         TEXT,
    released_at        TEXT
);
""",
    """
CREATE INDEX IF NOT EXISTS evaluation_writers_queue_idx ON evaluation_writers(state, generation);
""",
    # --- additive schema-6 tables added by the decision/evaluation slice ---
    # ``evaluation_evidence_pending`` replaces the correlated-JSON subquery that
    # used to rescan the whole evidence archive on every console refresh and every
    # selection admission. It is maintained transactionally by evidence recording
    # and card publication: evidence is pending while no currently published card
    # references it, and losing a reference makes it pending again. A
    # preferences-only or configuration-only publish never touches it, and
    # evidence recorded while a writer is frozen is inserted here inside its own
    # transaction.
    """
CREATE TABLE IF NOT EXISTS evaluation_evidence_pending (
    evidence_id  TEXT PRIMARY KEY,
    profile_id   TEXT NOT NULL,
    created_at   TEXT NOT NULL
);
""",
    """
CREATE INDEX IF NOT EXISTS evaluation_evidence_pending_profile_idx
    ON evaluation_evidence_pending(profile_id, created_at);
""",
    """
CREATE INDEX IF NOT EXISTS evaluation_evidence_pending_created_idx
    ON evaluation_evidence_pending(created_at, evidence_id);
""",
    """
CREATE INDEX IF NOT EXISTS evaluation_evidence_created_idx
    ON evaluation_evidence(created_at, evidence_id);
""",
    """
CREATE INDEX IF NOT EXISTS evaluation_evidence_run_idx ON evaluation_evidence(run_id);
""",
    """
CREATE INDEX IF NOT EXISTS evaluation_evidence_counted_idx ON evaluation_evidence(profile_id, counted);
""",
    # One performance sample per (profile, attempt). Repeated prose descriptions of
    # the same accepted attempt are separate evidence reports but a single sample,
    # and a counted sample always names the immutable attempt it came from.
    """
CREATE TABLE IF NOT EXISTS evaluation_samples (
    profile_id   TEXT NOT NULL,
    attempt_id   TEXT NOT NULL,
    task_id      TEXT NOT NULL,
    verdict      TEXT NOT NULL,
    evidence_id  TEXT,
    created_at   TEXT NOT NULL,
    PRIMARY KEY (profile_id, attempt_id)
);
""",
    """
CREATE INDEX IF NOT EXISTS evaluation_samples_profile_idx ON evaluation_samples(profile_id, created_at);
""",
    # Every changed card is snapshotted per published revision, so bounded current
    # references never lose provenance: after automatic compaction the pre-compaction
    # card text, risks and full reference list stay recoverable from the archive even
    # though the bounded current card no longer lists every reference.
    """
CREATE TABLE IF NOT EXISTS evaluation_card_history (
    table_revision  INTEGER NOT NULL,
    profile_id      TEXT NOT NULL,
    card_revision   INTEGER NOT NULL,
    summary         TEXT NOT NULL,
    strengths_json  TEXT NOT NULL DEFAULT '[]',
    limitations_json TEXT NOT NULL DEFAULT '[]',
    risks_json      TEXT NOT NULL DEFAULT '[]',
    evidence_ids_json TEXT NOT NULL DEFAULT '[]',
    sample_count    INTEGER NOT NULL DEFAULT 0,
    published_at    TEXT NOT NULL,
    PRIMARY KEY (table_revision, profile_id)
);
""",
    """
CREATE INDEX IF NOT EXISTS evaluation_card_history_profile_idx
    ON evaluation_card_history(profile_id, table_revision);
""",
    # Fixed-size transactional counters. The pending-evidence total and each
    # per-profile sample count are adjusted by the checked rowcount of the insert or
    # delete that changed the ledger, so an ordinary console refresh or route
    # admission reads one primary-key row instead of recounting a growing archive.
    """
CREATE TABLE IF NOT EXISTS evaluation_aggregates (
    name        TEXT PRIMARY KEY,
    value       INTEGER NOT NULL,
    updated_at  TEXT NOT NULL
);
""",
    # Extended decision state. ``evaluation_decisions`` stays the bounded public
    # history row the console reads; this table carries the audit material (the
    # exact persisted model input, the helper envelope, the validated proposal and
    # the lease/fence identities) that must not sit on every console refresh.
    """
CREATE TABLE IF NOT EXISTS decision_requests (
    decision_id            TEXT PRIMARY KEY REFERENCES evaluation_decisions(decision_id) ON DELETE RESTRICT,
    request_id             TEXT NOT NULL UNIQUE,
    kind                   TEXT NOT NULL CHECK (kind IN ('select','maintain')),
    input_fingerprint      TEXT NOT NULL,
    configuration_revision INTEGER NOT NULL DEFAULT 0,
    task_id                TEXT,
    attempt_id             TEXT,
    generation             INTEGER,
    reader_id              TEXT,
    writer_id              TEXT,
    writer_generation      INTEGER,
    expected_revision      INTEGER NOT NULL DEFAULT 0,
    published_revision     INTEGER,
    selected_json          TEXT,
    input_json             TEXT,
    input_sha256           TEXT,
    output_json            TEXT,
    proposal_json          TEXT,
    considered_evidence    INTEGER NOT NULL DEFAULT 0,
    pending_after          INTEGER,
    auto_publish           INTEGER NOT NULL DEFAULT 0,
    requested_json         TEXT NOT NULL DEFAULT '{}',
    created_at             TEXT NOT NULL,
    updated_at             TEXT NOT NULL
);
""",
    """
CREATE INDEX IF NOT EXISTS decision_requests_created_idx ON decision_requests(created_at, decision_id);
CREATE INDEX IF NOT EXISTS decision_requests_task_idx ON decision_requests(task_id);
""",
    """
CREATE INDEX IF NOT EXISTS evaluation_decisions_created_idx
    ON evaluation_decisions(created_at, decision_id);
""",
    # Harness-owned maintenance reads actual Host acknowledgement events. This
    # partial index makes the incremental review scan and its remainder count an
    # index-only range over the immutable event sequence instead of a table scan,
    # and `evaluation_maintenance_checkpoints` stores one durable sequence per
    # assessed profile. The cursor is the append-only event sequence, never a wall
    # clock: a review recorded later (or under a skewed clock) always has a higher
    # sequence and can never be skipped. The checkpoint is not a fact ledger: the
    # facts themselves live in `evaluation_evidence`, deduplicated by their
    # deterministic identity. A checkpoint advances only inside the same transaction
    # that records the batch's facts, so a failed preparation consumes no progress.
    """
CREATE INDEX IF NOT EXISTS events_review_seq_idx
    ON events(seq) WHERE kind IN ('task.accepted','task.rejected','workflow.acknowledged');
""",
    """
CREATE TABLE IF NOT EXISTS evaluation_maintenance_checkpoints (
    scope        TEXT PRIMARY KEY,
    review_seq   INTEGER NOT NULL,
    updated_at   TEXT NOT NULL
);
""",
    # User-owned per-family concurrent-attempt limits (ADR-011). A family is the
    # exact adapter/provider/model tuple; effort variants share one row. The row
    # is independent of ``evaluation_profiles`` availability on purpose: a setting
    # survives discovery marking a model unavailable, and a family without a row
    # uses the code-owned default. Only the authenticated console writer patch in
    # ``user_policy.py`` ever writes here.
    """
CREATE TABLE IF NOT EXISTS model_concurrency (
    adapter           TEXT NOT NULL,
    provider          TEXT NOT NULL,
    model             TEXT NOT NULL,
    concurrency_limit INTEGER NOT NULL DEFAULT 2 CHECK (concurrency_limit BETWEEN 1 AND 32),
    updated_revision  INTEGER NOT NULL,
    created_at        TEXT NOT NULL,
    updated_at        TEXT NOT NULL,
    PRIMARY KEY (adapter, provider, model)
);
""",
    # User-owned family defaults (schema 13). ``evaluation_preferences`` holds the
    # per-effort overrides, where ``none`` explicitly clears a family default.
    """
CREATE TABLE IF NOT EXISTS family_preferences (
    adapter           TEXT NOT NULL,
    provider          TEXT NOT NULL,
    model             TEXT NOT NULL,
    mode              TEXT NOT NULL CHECK (mode IN ('prefer','pin','exclude')),
    reason            TEXT NOT NULL DEFAULT '',
    updated_revision  INTEGER NOT NULL,
    PRIMARY KEY (adapter, provider, model)
);
""",
    # Program-owned model facts snapshots (ADR-021 decision 12), one durable dated
    # snapshot per model family extracted from the public models.dev catalog. Facts
    # belong to the model, never to a buddy, and are never user-editable. A
    # ``current`` row carries the extracted facts with their exact source, payload
    # hash and fetch date; an ``unknown`` row records a completed fetch with no
    # resolvable entry for this family. A failed fetch writes nothing, so the
    # previous snapshot is retained untouched. Added to the shared pending schema 16;
    # the explicit idle upgrade remains owned by the upgrade module.
    """
CREATE TABLE IF NOT EXISTS model_facts (
    adapter          TEXT NOT NULL,
    provider         TEXT NOT NULL,
    model            TEXT NOT NULL,
    status           TEXT NOT NULL CHECK (status IN ('current','unknown')),
    facts_json       TEXT NOT NULL DEFAULT '{}',
    identity_json    TEXT NOT NULL DEFAULT '{}',
    source           TEXT NOT NULL,
    payload_sha256   TEXT NOT NULL,
    fetched_at       TEXT NOT NULL,
    reason           TEXT,
    PRIMARY KEY (adapter, provider, model)
);
""",
    # The one reading of preference policy: an override wins, ``none`` clears, and
    # otherwise the family default applies. Every consumer reads this view.
    """
CREATE VIEW IF NOT EXISTS effective_preferences AS
SELECT p.profile_id AS profile_id,
       COALESCE(o.mode, f.mode) AS mode,
       CASE WHEN o.profile_id IS NOT NULL THEN o.reason ELSE f.reason END AS reason,
       CASE WHEN o.profile_id IS NOT NULL THEN 'override' ELSE 'family' END AS source
FROM evaluation_profiles p
LEFT JOIN evaluation_preferences o ON o.profile_id = p.profile_id
LEFT JOIN family_preferences f ON f.adapter = p.adapter AND f.provider = p.provider AND f.model = p.model
WHERE (o.profile_id IS NOT NULL AND o.mode != 'none') OR (o.profile_id IS NULL AND f.mode IS NOT NULL);
""",
)

EVALUATION_SCHEMA = "\n".join(EVALUATION_TABLES)

#: Current governed productivity workflow tables. A governed run is the existing
#: logical task (``run_id`` = ``task_id``) plus durable turn, request, helper,
#: continuation, workspace-reservation, pinned-artifact and attempt-scoped
#: credential records.
WORKFLOW_TABLES = (
    """
CREATE TABLE IF NOT EXISTS objectives (
    objective_id       TEXT PRIMARY KEY,
    title              TEXT NOT NULL,
    project_id         TEXT NOT NULL,
    project_path       TEXT NOT NULL,
    source_host_id     TEXT NOT NULL,
    created_at         TEXT NOT NULL,
    activity_seq       INTEGER NOT NULL DEFAULT 0,
    activity_at        TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS objectives_activity_idx ON objectives(activity_seq DESC, objective_id DESC);
CREATE INDEX IF NOT EXISTS objectives_project_idx ON objectives(project_id, activity_seq DESC, objective_id DESC);
""",
    # The logical goal plus its Host ownership generation and prepared workspace.
    # ``goal_json``/``goal_fingerprint`` are immutable: continuations record their
    # own effective input in ``workflow_continuations``.
    """
CREATE TABLE IF NOT EXISTS workflow_runs (
    run_id                  TEXT PRIMARY KEY REFERENCES tasks(task_id) ON DELETE RESTRICT,
    objective_id            TEXT REFERENCES objectives(objective_id) ON DELETE RESTRICT,
    title                   TEXT,
    activity_seq            INTEGER NOT NULL DEFAULT 0,
    activity_at             TEXT,
    host_id                 TEXT NOT NULL,
    owner_generation        INTEGER NOT NULL DEFAULT 1,
    control_verifier        TEXT NOT NULL,
    goal_json               TEXT NOT NULL,
    goal_fingerprint        TEXT NOT NULL,
    request_fingerprint     TEXT NOT NULL,
    execution_configuration_json TEXT,
    execution_configuration_revision INTEGER NOT NULL DEFAULT 0,
    validated_configuration_revision INTEGER,
    current_routing_id      TEXT,
    submission_verifier     TEXT,
    execution_workspace_json TEXT NOT NULL,
    workspace_manifest_json TEXT,
    workspace_id            TEXT,
    workspace_manifest_sha256 TEXT,
    state                   TEXT NOT NULL
        CHECK (state IN ('executing','awaiting-host','waiting-helpers','delivered','accepted','cancelled','failed')),
    active_request_id       TEXT,
    current_turn_id         TEXT,
    current_attempt_id      TEXT,
    continuation_count      INTEGER NOT NULL DEFAULT 0,
    final_artifact_id       TEXT,
    final_attempt_id        TEXT,
    revision                INTEGER NOT NULL DEFAULT 1,
    created_at              TEXT NOT NULL,
    updated_at              TEXT NOT NULL,
    configuration_locked    INTEGER NOT NULL DEFAULT 0 CHECK (configuration_locked IN (0,1))
);
""",
    """
CREATE INDEX IF NOT EXISTS workflow_runs_state_idx ON workflow_runs(state, updated_at);
CREATE INDEX IF NOT EXISTS workflow_runs_objective_idx ON workflow_runs(objective_id, activity_seq DESC, run_id DESC);
CREATE INDEX IF NOT EXISTS workflow_runs_activity_idx ON workflow_runs(activity_seq);
""",
    # Routing is owned work, but not a coding turn or a user-authorized helper.
    # Each link binds one existing decision task to one owner generation; old
    # links remain auditable and retain their real attempt/stop evidence.
    """
CREATE TABLE IF NOT EXISTS workflow_routes (
    decision_id         TEXT PRIMARY KEY REFERENCES decision_requests(decision_id) ON DELETE RESTRICT,
    run_id              TEXT NOT NULL REFERENCES workflow_runs(run_id) ON DELETE RESTRICT,
    owner_generation    INTEGER NOT NULL,
    state               TEXT NOT NULL CHECK (state IN ('pending','resolved','needs-host','fenced')),
    reason              TEXT,
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL
);
""",
    """
CREATE INDEX IF NOT EXISTS workflow_routes_run_idx ON workflow_routes(run_id);
""",
    # One row per actual execution turn. The turn input is written to disk by the
    # adapter; this row pins the service-owned identity, the bounded context and the
    # validated structured outcome.
    """
CREATE TABLE IF NOT EXISTS workflow_turns (
    turn_id             TEXT PRIMARY KEY,
    run_id              TEXT NOT NULL REFERENCES workflow_runs(run_id) ON DELETE RESTRICT,
    attempt_id          TEXT REFERENCES attempts(attempt_id) ON DELETE RESTRICT,
    generation          INTEGER,
    turn_index          INTEGER NOT NULL,
    resume_mode         TEXT NOT NULL
        CHECK (resume_mode IN ('initial','reconstructed-new-session','native-session')),
    previous_session_id TEXT,
    session_id          TEXT,
    prompt_sha256       TEXT,
    input_sha256        TEXT,
    input_json          TEXT NOT NULL,
    state               TEXT NOT NULL CHECK (state IN ('prepared','running','concluded','failed')),
    disposition         TEXT CHECK (disposition IN ('completed','assistance','attention')),
    outcome_json        TEXT,
    provenance_json     TEXT,
    turn_result_path    TEXT,
    sealed_artifacts_json TEXT NOT NULL DEFAULT '[]',
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL,
    UNIQUE(run_id, turn_index)
);
""",
    """
CREATE INDEX IF NOT EXISTS workflow_turns_run_idx ON workflow_turns(run_id, turn_index);
""",
    """
CREATE INDEX IF NOT EXISTS workflow_turns_attempt_idx ON workflow_turns(attempt_id);
""",
    # Assistance/attention requests. Only a Host decision closes one, and only an
    # approval creates helpers.
    """
CREATE TABLE IF NOT EXISTS workflow_requests (
    request_id          TEXT PRIMARY KEY,
    run_id              TEXT NOT NULL REFERENCES workflow_runs(run_id) ON DELETE RESTRICT,
    turn_id             TEXT,
    attempt_id          TEXT,
    child_task_id       TEXT,
    kind                TEXT NOT NULL CHECK (kind IN ('assistance','attention','helper-attention','helper-report')),
    summary             TEXT NOT NULL,
    payload_json        TEXT NOT NULL,
    state               TEXT NOT NULL CHECK (state IN ('open','approved','declined','superseded','cancelled')),
    decision_json       TEXT,
    decision_command_id TEXT,
    expected_revision   INTEGER NOT NULL,
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL,
    decided_at          TEXT
);
""",
    """
CREATE INDEX IF NOT EXISTS workflow_requests_run_idx ON workflow_requests(run_id, state);
""",
    # Explicitly scoped ordinary helper tasks created only by an approval.
    """
CREATE TABLE IF NOT EXISTS workflow_children (
    child_task_id       TEXT PRIMARY KEY REFERENCES tasks(task_id) ON DELETE RESTRICT,
    parent_run_id       TEXT NOT NULL REFERENCES workflow_runs(run_id) ON DELETE RESTRICT,
    request_id          TEXT NOT NULL,
    decision_command_id TEXT NOT NULL,
    role                TEXT NOT NULL DEFAULT 'helper',
    integrator          INTEGER NOT NULL DEFAULT 0,
    state               TEXT NOT NULL CHECK (state IN ('active','succeeded','failed','cancelled','attention')),
    auto_continue       INTEGER NOT NULL DEFAULT 0,
    workspace_intent_json TEXT NOT NULL,
    workspace_manifest_json TEXT,
    workspace_manifest_sha256 TEXT,
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL,
    revision            INTEGER NOT NULL DEFAULT 1
);
""",
    """
CREATE INDEX IF NOT EXISTS workflow_children_parent_idx ON workflow_children(parent_run_id, state);
""",
    # Durable continuation inputs. An automatic continuation is one-use authority
    # tied to the request and prepared workspace; a manual continuation invalidates
    # every unconsumed automatic trigger in the same transaction.
    """
CREATE TABLE IF NOT EXISTS workflow_continuations (
    continuation_id   TEXT PRIMARY KEY,
    run_id            TEXT NOT NULL REFERENCES workflow_runs(run_id) ON DELETE RESTRICT,
    command_id        TEXT,
    authorized_by     TEXT NOT NULL CHECK (authorized_by IN ('manual','auto')),
    request_id        TEXT,
    expected_revision INTEGER NOT NULL,
    input_text        TEXT NOT NULL,
    input_bytes       INTEGER NOT NULL,
    reason            TEXT,
    helper_policy     TEXT NOT NULL DEFAULT 'keep' CHECK (helper_policy IN ('cancel','keep')),
    helper_outcomes_json TEXT NOT NULL DEFAULT '[]',
    workspace_manifest_json TEXT,
    state             TEXT NOT NULL CHECK (state IN ('recorded','queued','consumed','cancelled','invalidated')),
    attempt_id        TEXT,
    turn_id           TEXT,
    created_at        TEXT NOT NULL,
    consumed_at       TEXT
);
""",
    """
CREATE INDEX IF NOT EXISTS workflow_continuations_run_idx ON workflow_continuations(run_id, state);
""",
    """
CREATE UNIQUE INDEX IF NOT EXISTS workflow_continuations_auto_unique
    ON workflow_continuations(run_id, request_id) WHERE authorized_by='auto';
""",
    # Durable workspace reservations independent of attempt capacity. The partial
    # unique index is the relational guarantee that one checkout has at most one
    # held writer, while readers share a concrete unchanged snapshot.
    """
CREATE TABLE IF NOT EXISTS workspace_reservations (
    reservation_id  TEXT PRIMARY KEY,
    workspace_id    TEXT NOT NULL,
    checkout_id     TEXT NOT NULL,
    repository_id   TEXT,
    path            TEXT NOT NULL,
    access          TEXT NOT NULL CHECK (access IN ('read','write')),
    holder_task_id  TEXT NOT NULL REFERENCES tasks(task_id) ON DELETE RESTRICT,
    holder_kind     TEXT NOT NULL CHECK (holder_kind IN ('parent','helper','reader')),
    parent_run_id   TEXT REFERENCES workflow_runs(run_id) ON DELETE RESTRICT,
    manifest_sha256 TEXT NOT NULL,
    state           TEXT NOT NULL CHECK (state IN ('held','transferred','released')),
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    released_at     TEXT
);
""",
    """
CREATE UNIQUE INDEX IF NOT EXISTS workspace_reservations_writer_unique
    ON workspace_reservations(checkout_id) WHERE state='held' AND access='write';
""",
    """
CREATE INDEX IF NOT EXISTS workspace_reservations_checkout_idx ON workspace_reservations(checkout_id, state);
""",
    """
CREATE INDEX IF NOT EXISTS workspace_reservations_holder_idx ON workspace_reservations(holder_task_id, state);
""",
    # Append-only authorization scope per governed run. ``scope_version`` is a
    # per-run monotonic counter, deliberately distinct from the input snapshot it
    # happened to see: a later amendment never rewrites an earlier scope, an
    # earlier input manifest or the original submission. Version 1 is recorded
    # when the run is attached; a new row is only added for an approved stage.
    """
CREATE TABLE IF NOT EXISTS workflow_scope_versions (
    run_id              TEXT NOT NULL REFERENCES workflow_runs(run_id) ON DELETE RESTRICT,
    scope_version       INTEGER NOT NULL,
    command_id          TEXT,
    actor               TEXT NOT NULL,
    reason              TEXT,
    write_scope_json    TEXT NOT NULL,
    manifest_sha256     TEXT,
    stopped_evidence_json TEXT NOT NULL,
    created_at          TEXT NOT NULL,
    PRIMARY KEY (run_id, scope_version)
);
""",
    """
CREATE INDEX IF NOT EXISTS workflow_scope_versions_run_idx ON workflow_scope_versions(run_id, scope_version);
""",
    # Durable evidence of a failed seal that found managed changes outside the
    # authorized write scope, plus the Host's later restore/adopt/abandon decision.
    # The failed site stays on disk and is never promoted to a baseline by itself.
    # ``delivery_json`` separately records a Host-resolution delivery: when the only
    # failure was that seal and the attempt's own native completed outcome is intact,
    # a full restore/adopt makes the resolved artifact deliverable without another
    # model turn while the original attempt, turn and command receipts stay unchanged.
    """
CREATE TABLE IF NOT EXISTS workflow_workspace_conflicts (
    conflict_id             TEXT PRIMARY KEY,
    run_id                  TEXT NOT NULL REFERENCES workflow_runs(run_id) ON DELETE RESTRICT,
    attempt_id              TEXT NOT NULL,
    turn_id                 TEXT,
    manifest_sha256         TEXT NOT NULL,
    observed_fingerprint    TEXT NOT NULL,
    blocking_paths_json     TEXT NOT NULL,
    evidence_json           TEXT NOT NULL,
    state                   TEXT NOT NULL CHECK (state IN ('open','restored','adopted','abandoned')),
    action                  TEXT CHECK (action IN ('restore','adopt','abandon')),
    resolved_paths_json     TEXT NOT NULL DEFAULT '[]',
    conflicting_paths_json  TEXT NOT NULL DEFAULT '[]',
    artifact_id             TEXT,
    output_commit           TEXT,
    output_tree             TEXT,
    actor                   TEXT,
    reason                  TEXT,
    command_id              TEXT,
    delivery_json           TEXT,
    created_at              TEXT NOT NULL,
    updated_at              TEXT NOT NULL,
    UNIQUE(run_id, attempt_id, observed_fingerprint)
);
""",
    """
CREATE INDEX IF NOT EXISTS workflow_workspace_conflicts_run_idx ON workflow_workspace_conflicts(run_id, state);
""",
    # One immutable integration record per (artifact, target, strategy). The
    # before/after commits and trees are resolved from the actual target checkout,
    # never taken on trust from the caller; an accepted goal must hold a verified
    # record or an explicit not-required decision bound to its final artifact.
    """
CREATE TABLE IF NOT EXISTS workflow_integrations (
    integration_id        TEXT PRIMARY KEY,
    run_id                TEXT NOT NULL REFERENCES workflow_runs(run_id) ON DELETE RESTRICT,
    artifact_id           TEXT NOT NULL,
    attempt_id            TEXT,
    state                 TEXT NOT NULL CHECK (state IN ('verified','not-required','conflict')),
    strategy              TEXT NOT NULL,
    binding_sha256        TEXT NOT NULL,
    target_kind           TEXT NOT NULL,
    target_path           TEXT NOT NULL DEFAULT '',
    target_ref            TEXT NOT NULL DEFAULT '',
    target_repository_id  TEXT,
    target_checkout_id    TEXT,
    source_commit         TEXT,
    source_tree           TEXT,
    before_commit         TEXT,
    after_commit          TEXT,
    before_tree           TEXT,
    after_tree            TEXT,
    verification_json     TEXT NOT NULL,
    reason                TEXT,
    command_id            TEXT,
    actor                 TEXT NOT NULL,
    created_at            TEXT NOT NULL,
    UNIQUE(binding_sha256)
);
""",
    """
CREATE INDEX IF NOT EXISTS workflow_integrations_run_idx ON workflow_integrations(run_id, artifact_id, created_at);
""",
    # One cleanup plan per disposable managed checkout. The plan binds the exact
    # path, the workspace/repository identity and the evidence that permitted it;
    # apply rechecks all of them and deletes only that registered linked worktree.
    """
CREATE TABLE IF NOT EXISTS workspace_cleanup_plans (
    plan_id          TEXT PRIMARY KEY,
    run_id           TEXT NOT NULL REFERENCES workflow_runs(run_id) ON DELETE RESTRICT,
    workspace_id     TEXT NOT NULL,
    checkout_id      TEXT NOT NULL,
    repository_id    TEXT,
    path             TEXT NOT NULL,
    kind             TEXT NOT NULL,
    state            TEXT NOT NULL CHECK (state IN ('planned','applying','applied','blocked')),
    evidence_json    TEXT NOT NULL,
    retention_json   TEXT NOT NULL,
    reasons_json     TEXT NOT NULL DEFAULT '[]',
    result_json      TEXT,
    command_id       TEXT,
    actor            TEXT NOT NULL,
    revision         INTEGER NOT NULL DEFAULT 1,
    created_at       TEXT NOT NULL,
    applied_at       TEXT,
    expires_at       TEXT NOT NULL
);
""",
    """
CREATE INDEX IF NOT EXISTS workspace_cleanup_plans_run_idx ON workspace_cleanup_plans(run_id, state);
""",
    # Attempt-scoped credentials handed to a DSH child. The token is derived from
    # the service secret and never stored; only its verifier is persisted, and the
    # service enforces the permitted operation set on every request that presents
    # one. Revoked automatically when the attempt finishes.
    """
CREATE TABLE IF NOT EXISTS agent_credentials (
    credential_id   TEXT PRIMARY KEY,
    run_id          TEXT NOT NULL REFERENCES workflow_runs(run_id) ON DELETE RESTRICT,
    task_id         TEXT NOT NULL,
    attempt_id      TEXT,
    generation      INTEGER,
    turn_id         TEXT,
    token_verifier  TEXT NOT NULL,
    scopes_json     TEXT NOT NULL DEFAULT '[]',
    state           TEXT NOT NULL CHECK (state IN ('active','revoked','expired')),
    created_at      TEXT NOT NULL,
    expires_at      TEXT,
    revoked_at      TEXT,
    revision        INTEGER NOT NULL DEFAULT 1
);
""",
    """
CREATE UNIQUE INDEX IF NOT EXISTS agent_credentials_attempt_unique
    ON agent_credentials(attempt_id) WHERE state='active';
""",
    """
CREATE INDEX IF NOT EXISTS agent_credentials_run_idx ON agent_credentials(run_id, state);
""",
    # Immutable pinned manifests (prepared inputs and sealed outputs). A worktree
    # may be removed later; these references stay readable.
    """
CREATE TABLE IF NOT EXISTS workflow_artifacts (
    artifact_id     TEXT PRIMARY KEY,
    run_id          TEXT NOT NULL REFERENCES workflow_runs(run_id) ON DELETE RESTRICT,
    turn_id         TEXT,
    attempt_id      TEXT,
    source_task_id  TEXT,
    kind            TEXT NOT NULL,
    manifest_json   TEXT NOT NULL,
    manifest_sha256 TEXT NOT NULL,
    created_at      TEXT NOT NULL,
    UNIQUE(run_id, attempt_id, manifest_sha256)
);
""",
    """
CREATE INDEX IF NOT EXISTS workflow_artifacts_run_idx ON workflow_artifacts(run_id, created_at);
""",
    # Bounded suggestions an attempt-scoped credential may record on its own run.
    """
CREATE TABLE IF NOT EXISTS workflow_suggestions (
    suggestion_id TEXT PRIMARY KEY,
    run_id        TEXT NOT NULL REFERENCES workflow_runs(run_id) ON DELETE RESTRICT,
    attempt_id    TEXT,
    author        TEXT NOT NULL,
    body          TEXT NOT NULL,
    created_at    TEXT NOT NULL
);
""",
    """
CREATE INDEX IF NOT EXISTS workflow_suggestions_run_idx ON workflow_suggestions(run_id, created_at);
""",
)

WORKFLOW_SCHEMA = "\n".join(WORKFLOW_TABLES)

HARNESS_SCHEMA = """
CREATE TABLE IF NOT EXISTS harness_health (
    adapter TEXT PRIMARY KEY CHECK (adapter IN ('dsh','zcode','codex','claude')),
    manual_path TEXT,
    revision INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'unknown'
        CHECK (status IN ('unknown','ready','missing','login-required','unhealthy')),
    record_json TEXT NOT NULL DEFAULT '{}',
    checked_at TEXT,
    expires_at TEXT,
    scan_after TEXT,
    quota_json TEXT
);
"""

#: The only supported schema; used to create a fresh state directory.
HOST_CONCLUSION_TABLE = """
CREATE TABLE IF NOT EXISTS workflow_host_conclusions (
    conclusion_id    TEXT PRIMARY KEY,
    run_id           TEXT NOT NULL REFERENCES workflow_runs(run_id) ON DELETE RESTRICT,
    attempt_id       TEXT REFERENCES attempts(attempt_id) ON DELETE RESTRICT,
    run_revision     INTEGER NOT NULL,
    owner_generation INTEGER NOT NULL,
    execution_status TEXT NOT NULL CHECK (execution_status IN ('failed','cancelled')),
    note             TEXT NOT NULL,
    evidence_json    TEXT NOT NULL DEFAULT '[]',
    artifact_id      TEXT REFERENCES workflow_artifacts(artifact_id) ON DELETE RESTRICT,
    integration_id   TEXT REFERENCES workflow_integrations(integration_id) ON DELETE RESTRICT,
    actor            TEXT NOT NULL,
    command_id       TEXT NOT NULL UNIQUE,
    created_at       TEXT NOT NULL
);
"""
HOST_CONCLUSION_INDEX = "CREATE INDEX IF NOT EXISTS workflow_host_conclusions_run_idx ON workflow_host_conclusions(run_id, run_revision)"
SCHEMA = "\n".join((CORE_SCHEMA, EVALUATION_SCHEMA, WORKFLOW_SCHEMA, HARNESS_SCHEMA,
                    HOST_CONCLUSION_TABLE, HOST_CONCLUSION_INDEX + ";"))


def utc_now() -> str:
    """Canonical timestamp used for every durable row."""
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def canonical_json(value) -> str:
    """Deterministic JSON for fingerprints and payload hashing."""
    return json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":"))


class Corruption(RuntimeError):
    """The database cannot be used as the current board."""


class Database:
    """Owns the SQLite file, current schema, pragmas and capability secret."""

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
    def _require_current_schema(self) -> None:
        """Refuse unsupported files before opening a writable/WAL connection."""
        guidance = (
            f"This Buddy build accepts only schema {SCHEMA_VERSION}; use a clean state directory "
            "and retain the existing directory as an archive"
        )
        try:
            connection = sqlite3.connect(self.path.resolve().as_uri() + "?mode=ro", uri=True, timeout=10)
            try:
                meta = connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='meta'").fetchone()
                if meta is None:
                    raise Corruption(f"Existing board has no current schema marker. {guidance}")
                version = connection.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
                if version is None or version[0] != str(SCHEMA_VERSION):
                    found = None if version is None else version[0]
                    raise Corruption(f"Unsupported board schema version {found!r}. {guidance}")
            finally:
                connection.close()
        except sqlite3.DatabaseError as error:
            raise Corruption(f"Existing board is not a readable current database. {guidance}") from error

    def initialize(self) -> None:
        with self._lock:
            fresh = not self.path.exists()
            if not fresh:
                self._require_current_schema()
            self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
            os.chmod(self.directory, 0o700)
            with self.connect() as connection:
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
                connection.execute(
                    "INSERT OR IGNORE INTO evaluation_state(id, created_at, updated_at) VALUES(1, ?, ?)",
                    (utc_now(), utc_now()),
                )
                if fresh:
                    from .router import initialize_configuration
                    initialize_configuration(connection)
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

    def writer_token(self, writer_id: str, generation: int) -> str:
        """Derive one evaluation writer capability.

        The token is deterministic in (service secret, writer, generation), so an
        idempotent ``evaluation_write_begin`` replay returns the same secret to the
        caller that created the intent, while only a verifier is persisted.
        """
        message = f"{WRITER_TOKEN_VERSION}:writer:{writer_id}:{generation}".encode("utf-8")
        return hmac.new(self.secret, message, hashlib.sha256).hexdigest()

    def writer_token_verifier(self, token: str) -> str:
        return hmac.new(self.secret, f"writer-token:{token}".encode("utf-8"), hashlib.sha256).hexdigest()

    def control_token(self, run_id: str, owner_generation: int) -> str:
        """Derive the Host control capability of one owner generation."""
        message = f"{CONTROL_TOKEN_VERSION}:control:{run_id}:{owner_generation}".encode("utf-8")
        return hmac.new(self.secret, message, hashlib.sha256).hexdigest()

    def control_token_verifier(self, token: str) -> str:
        return hmac.new(self.secret, f"control-token:{token}".encode("utf-8"), hashlib.sha256).hexdigest()

    def agent_token(self, run_id: str, attempt_id: str, generation: int) -> str:
        """Derive the attempt-scoped credential handed to a DSH child."""
        message = f"{AGENT_TOKEN_VERSION}:agent:{run_id}:{attempt_id}:{generation}".encode("utf-8")
        return hmac.new(self.secret, message, hashlib.sha256).hexdigest()

    def agent_token_verifier(self, token: str) -> str:
        return hmac.new(self.secret, f"agent-token:{token}".encode("utf-8"), hashlib.sha256).hexdigest()

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
