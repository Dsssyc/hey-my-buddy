"""Schema-versioned SQLite storage for the hey-my-buddy blackboard.

One database file owns every authoritative fact: tasks, attempts,
messages, artifacts, events, command receipts and resource claims. Connections are
owned per thread, transactions are short, and no transaction ever spans RPC, a
subprocess, an LLM call or an event wait.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from ...json_codec import canonical_json  # noqa: F401 - the one shared canonical JSON

SCHEMA_VERSION = 28
UPGRADE_BASE_SCHEMA = 27
DB_FILE = "board.sqlite3"
SECRET_KEY = "capability_secret"
CONTROL_TOKEN_VERSION = 1
AGENT_TOKEN_VERSION = 1

WORKFLOW_REQUESTS_DDL = """
CREATE TABLE IF NOT EXISTS workflow_requests (
    request_id          TEXT PRIMARY KEY,
    run_id              TEXT NOT NULL REFERENCES workflow_runs(run_id) ON DELETE RESTRICT,
    turn_id             TEXT,
    attempt_id          TEXT,
    child_task_id       TEXT,
    kind                TEXT NOT NULL CHECK (kind IN ('assistance','attention','helper-attention','helper-report')),
    summary             TEXT,
    reason_code         TEXT,
    reason_params_json  TEXT,
    decision_id         TEXT REFERENCES evaluation_decisions(decision_id) ON DELETE RESTRICT,
    payload_json        TEXT NOT NULL,
    state               TEXT NOT NULL CHECK (state IN ('open','approved','declined','superseded','cancelled')),
    decision_json       TEXT,
    decision_command_id TEXT,
    expected_revision   INTEGER NOT NULL,
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL,
    decided_at          TEXT,
    CHECK ((summary IS NOT NULL AND reason_code IS NULL AND reason_params_json IS NULL AND decision_id IS NULL)
        OR (summary IS NULL AND reason_code IS NOT NULL AND reason_params_json IS NOT NULL
            AND json_valid(reason_params_json) AND json_type(reason_params_json)='object')
        OR (summary IS NULL AND reason_code IS NULL AND reason_params_json IS NULL AND decision_id IS NOT NULL))
);
"""

MACRO_TASK_DELETIONS_DDL = """
CREATE TABLE IF NOT EXISTS macro_task_deletions (
    deletion_id      TEXT PRIMARY KEY,
    target_kind      TEXT NOT NULL CHECK (target_kind IN ('macro-task','micro-task-tree')),
    target_id        TEXT NOT NULL,
    UNIQUE(target_kind, target_id)
);
"""
#: Every task state the durable model may hold. See ``docs/board.md`` for the
#: documented transition table; ``store.py`` enforces it.
TASK_STATES = (
    "queued",
    "running",
    "cancelling",
    "completed",
    "concluded",
    "failed",
    "cancelled",
    "reconciliation-needed",
)
TERMINAL_TASK_STATES = frozenset({"completed", "concluded", "failed", "cancelled"})

ATTEMPT_STATES = ("starting", "executing", "finalizing", "uncertain", "finished")
ACTIVE_ATTEMPT_STATES = frozenset({"starting", "executing", "finalizing", "uncertain"})

MESSAGE_STATES = ("queued", "claimed", "delivered", "answered", "discarded", "unavailable")
TERMINAL_MESSAGE_STATES = frozenset({"answered", "discarded", "unavailable"})

from ...protocol.stop import TERMINATION_REASONS  # noqa: F401 - shared receipt vocabulary

MESSAGES_DDL = """
CREATE TABLE IF NOT EXISTS messages (
    message_id      TEXT PRIMARY KEY,
    task_id         TEXT NOT NULL REFERENCES tasks(task_id) ON DELETE RESTRICT,
    attempt_id      TEXT REFERENCES attempts(attempt_id) ON DELETE RESTRICT,
    inquiry_id      TEXT NOT NULL,
    direction       TEXT NOT NULL,
    author_role     TEXT,
    author_id       TEXT,
    body            TEXT NOT NULL,
    payload_hash    TEXT NOT NULL,
    state           TEXT NOT NULL
        CHECK (state IN ('queued','claimed','delivered','answered','discarded','unavailable')),
    reason_code     TEXT,
    reason_params_json TEXT,
    delivery_json   TEXT,
    answer_json     TEXT,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    revision        INTEGER NOT NULL DEFAULT 1,
    CHECK ((reason_code IS NULL AND reason_params_json IS NULL)
        OR (reason_code IS NOT NULL AND reason_params_json IS NOT NULL
            AND json_valid(reason_params_json) AND json_type(reason_params_json)='object'
            AND state IN ('delivered','unavailable'))),
    CHECK (state != 'unavailable' OR reason_code IS NOT NULL),
    CHECK ((author_role IS NULL AND author_id IS NULL)
        OR (author_role IS NOT NULL AND author_id IS NOT NULL)),
    UNIQUE(task_id, inquiry_id)
);
CREATE INDEX IF NOT EXISTS messages_task_idx ON messages(task_id, created_at);
CREATE INDEX IF NOT EXISTS messages_state_idx ON messages(state);
"""

CORE_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tasks (
    task_id                 TEXT PRIMARY KEY,
    request_id              TEXT NOT NULL UNIQUE,
    owner                   TEXT,
    spec_json               TEXT NOT NULL,
    input_fingerprint       TEXT NOT NULL,
    fingerprint_version     INTEGER NOT NULL,
    adapter                 TEXT NOT NULL,
    required_capabilities   TEXT NOT NULL DEFAULT '[]',
    cwd                     TEXT NOT NULL,
    exclusive_resources     TEXT NOT NULL DEFAULT '[]',
    expected_seconds        INTEGER CHECK (expected_seconds > 0),
    router_budget_seconds   INTEGER CHECK (router_budget_seconds > 0),
    state                   TEXT NOT NULL
        CHECK (state IN ('queued','running','cancelling','completed','concluded','failed','cancelled','reconciliation-needed')),
    queue_reason            TEXT,
    revision                INTEGER NOT NULL,
    selected_attempt_id     TEXT,
    active_attempt_id       TEXT,
    created_at              TEXT NOT NULL,
    updated_at              TEXT NOT NULL,
    accepted_at             TEXT,
    acceptance_note         TEXT,
    review_rejected_at      TEXT,
    review_rejection_note   TEXT,
    CHECK ((adapter='decision' AND expected_seconds IS NULL AND router_budget_seconds IS NOT NULL)
        OR (adapter!='decision' AND expected_seconds IS NOT NULL AND router_budget_seconds IS NULL))
);
CREATE INDEX IF NOT EXISTS tasks_state_idx ON tasks(state, created_at);

CREATE TABLE IF NOT EXISTS attempts (
    attempt_id          TEXT PRIMARY KEY,
    task_id             TEXT NOT NULL REFERENCES tasks(task_id) ON DELETE RESTRICT,
    generation          INTEGER NOT NULL,
    nonce_verifier      TEXT NOT NULL,
    execution_state     TEXT NOT NULL
        CHECK (execution_state IN ('starting','executing','finalizing','uncertain','finished')),
    ownership           TEXT NOT NULL DEFAULT 'owned' CHECK (ownership IN ('owned','uncertain')),
    runtime_identity    TEXT,
    adapter             TEXT NOT NULL,
    -- Frozen model-service identity (harness/provider/model, no effort dimension).
    -- Written once by the claim transaction that resolved it and never updated
    -- afterwards, so a historical or shutdown-uncertain attempt can never change
    -- which model service's concurrency slot it occupies. NULL for model-less work
    -- (commands) which counts only against the machine-wide ceiling.
    model_harness       TEXT,
    model_provider      TEXT,
    model_model         TEXT,
    log_paths           TEXT NOT NULL DEFAULT '{}',
    result_json         TEXT,
    error               TEXT,
    shutdown_confirmed  INTEGER NOT NULL DEFAULT 0,
    cancel_requested_at TEXT,
    stop_initiator      TEXT CHECK (stop_initiator IN ('host','lead','console','service','unknown')),
    grace_deadline      TEXT,
    force_reason        TEXT,
    force_requested_at  TEXT,
    identity_confirmed_at TEXT,
    cleanup_pending     INTEGER NOT NULL DEFAULT 0 CHECK (cleanup_pending IN (0,1)),
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
-- Relational guarantee of the documented invariant: at most one effective active
-- attempt per task. An unconfirmed (uncertain) attempt keeps its slot, so a
-- replacement cannot be created while a survivor may still be running.
CREATE UNIQUE INDEX IF NOT EXISTS attempts_effective_unique ON attempts(task_id)
    WHERE execution_state IN ('starting','executing','finalizing','uncertain');
CREATE INDEX IF NOT EXISTS attempts_active_idx ON attempts(execution_state);
CREATE INDEX IF NOT EXISTS attempts_model_service_idx
    ON attempts(model_harness, model_provider, model_model) WHERE model_model IS NOT NULL;

CREATE TABLE IF NOT EXISTS attempt_process_identities (
    attempt_id          TEXT PRIMARY KEY REFERENCES attempts(attempt_id) ON DELETE RESTRICT,
    boot_id             TEXT NOT NULL,
    root_pid            INTEGER NOT NULL CHECK (root_pid > 0),
    root_started_at     INTEGER NOT NULL CHECK (root_started_at > 0),
    process_group_id    INTEGER CHECK (process_group_id > 0),
    job_name            TEXT,
    CHECK ((process_group_id IS NOT NULL AND job_name IS NULL)
        OR (process_group_id IS NULL AND job_name IS NOT NULL))
);

CREATE TABLE IF NOT EXISTS attempt_interruptions (
    attempt_id          TEXT PRIMARY KEY REFERENCES attempts(attempt_id) ON DELETE RESTRICT,
    evidence_kind       TEXT NOT NULL CHECK (evidence_kind IN ('boot-changed','identity-absent','orphan-stopped')),
    state               TEXT NOT NULL CHECK (state IN ('sealing','settled')),
    seal_error          TEXT
);

CREATE TABLE IF NOT EXISTS attempt_shutdown_evidence (
    attempt_id          TEXT PRIMARY KEY REFERENCES attempts(attempt_id) ON DELETE RESTRICT,
    evidence_kind       TEXT NOT NULL,
    detail_json         TEXT NOT NULL,
    observed_at         TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS execution_issues (
    attempt_id          TEXT NOT NULL REFERENCES attempts(attempt_id) ON DELETE RESTRICT,
    stage               TEXT NOT NULL,
    code                TEXT NOT NULL,
    message             TEXT NOT NULL,
    observed_at         TEXT NOT NULL,
    PRIMARY KEY (attempt_id, stage)
);

CREATE TABLE IF NOT EXISTS service_issues (
    stage               TEXT PRIMARY KEY,
    code                TEXT NOT NULL,
    message             TEXT NOT NULL,
    observed_at         TEXT NOT NULL
);

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
    actor_role   TEXT,
    actor_id     TEXT,
    created_at   TEXT NOT NULL,
    CHECK ((actor_role IS NULL) = (actor_id IS NULL)),
    CHECK (actor_role IS NULL OR actor_role IN ('host','lead','console','service','workflow','bridge'))
);
CREATE INDEX IF NOT EXISTS events_task_idx ON events(task_id, seq);
CREATE INDEX IF NOT EXISTS events_decision_status_idx ON events(kind, seq DESC)
    WHERE kind IN ('decision.completed','decision.failed','decision.needs_host','decision.cancelled');
CREATE INDEX IF NOT EXISTS events_router_claimed_idx
    ON events(json_extract(CASE WHEN json_valid(payload_json) THEN payload_json END, '$.buddyId'), seq)
    WHERE kind='router.claimed';

CREATE TABLE IF NOT EXISTS commands (
    command_id      TEXT PRIMARY KEY,
    task_id         TEXT,
    attempt_id      TEXT,
    kind            TEXT NOT NULL,
    request_hash    TEXT NOT NULL,
    response_json   TEXT NOT NULL,
    subject         TEXT
);

CREATE TABLE IF NOT EXISTS resource_claims (
    claim_id    TEXT PRIMARY KEY,
    task_id     TEXT NOT NULL REFERENCES tasks(task_id) ON DELETE RESTRICT,
    attempt_id  TEXT,
    resource    TEXT NOT NULL,
    kind        TEXT NOT NULL,
    state       TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    UNIQUE(task_id, resource)
);
CREATE INDEX IF NOT EXISTS resource_claims_state_idx ON resource_claims(state, kind);

-- One bounded latest activity projection per attempt. It is a projection, not a
-- history: a native controller publishes phase/tool/waiting observations through
-- the attempt-private sidecar, the Worker forwards them, and the newest monotone
-- receipt replaces the previous row. The row is bound to the attempt generation and
-- is never inherited by a replacement attempt.
CREATE TABLE IF NOT EXISTS attempt_activity (
    attempt_id     TEXT PRIMARY KEY REFERENCES attempts(attempt_id) ON DELETE RESTRICT,
    task_id        TEXT NOT NULL REFERENCES tasks(task_id) ON DELETE RESTRICT,
    activity_json  TEXT NOT NULL,
    updated_at     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS attempt_activity_task_idx ON attempt_activity(task_id, updated_at);
"""

#: Published settings and discovery/decision history.
EVALUATION_TABLES = (
    """
CREATE TABLE IF NOT EXISTS catalog_observations (
    observation_id INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id TEXT NOT NULL UNIQUE,
    response_json TEXT,
    harnesses_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS catalog_current (
    harness TEXT PRIMARY KEY,
    observation_id INTEGER NOT NULL,
    discovery_id TEXT,
    status TEXT NOT NULL,
    reason TEXT,
    updated_at TEXT NOT NULL,
    read_at TEXT,
    account_status TEXT NOT NULL DEFAULT 'unknown'
);
""",
    """
CREATE TABLE IF NOT EXISTS model_service_notes (
    harness    TEXT NOT NULL,
    provider   TEXT NOT NULL,
    model      TEXT NOT NULL,
    text       TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (harness, provider, model)
);
""",
    """
CREATE TABLE IF NOT EXISTS buddies (
    buddy_id          TEXT PRIMARY KEY,
    label               TEXT NOT NULL,
    harness             TEXT NOT NULL,
    provider            TEXT NOT NULL,
    model               TEXT NOT NULL,
    effort              TEXT NOT NULL,
    available           INTEGER NOT NULL DEFAULT 0,
    enabled             INTEGER NOT NULL DEFAULT 1,
    capabilities_json   TEXT NOT NULL DEFAULT '[]',
    context_window      INTEGER,
    description         TEXT NOT NULL DEFAULT '',
    source              TEXT NOT NULL DEFAULT '',
    unavailable_code        TEXT,
    unavailable_params_json TEXT,
    unavailable_content     TEXT,
    CHECK ((unavailable_code IS NULL) = (unavailable_params_json IS NULL)),
    CHECK (unavailable_content IS NULL OR unavailable_code IS 'catalog.native-unavailable')
);
""",
    """
CREATE INDEX IF NOT EXISTS buddies_current_idx ON buddies(available, buddy_id);
CREATE INDEX IF NOT EXISTS buddies_harness_idx ON buddies(harness, buddy_id);
""",
    """
CREATE TABLE IF NOT EXISTS evaluation_preferences (
    buddy_id        TEXT PRIMARY KEY REFERENCES buddies(buddy_id) ON DELETE RESTRICT,
    mode              TEXT NOT NULL CHECK (mode IN ('prefer','pin','exclude')),
    note              TEXT
);
CREATE INDEX IF NOT EXISTS evaluation_preferences_mode_idx ON evaluation_preferences(mode, buddy_id);
""",
    """
CREATE TABLE IF NOT EXISTS evaluation_decisions (
    decision_id       TEXT PRIMARY KEY,
    status            TEXT NOT NULL,
    task              TEXT NOT NULL,
    buddy_id        TEXT,
    reason_code       TEXT,
    reason_params_json TEXT,
    note              TEXT,
    created_at        TEXT NOT NULL,
    CHECK ((reason_code IS NULL AND reason_params_json IS NULL)
        OR (reason_code IS NOT NULL AND reason_params_json IS NOT NULL
            AND json_valid(reason_params_json) AND json_type(reason_params_json)='object'))
);
""",
    """
CREATE TABLE IF NOT EXISTS evaluation_catalog (
    discovery_id      TEXT PRIMARY KEY,
    payload_json      TEXT NOT NULL
);
""",
    # Extended decision state. ``evaluation_decisions`` stays the bounded public
    # history row the console reads; this table carries the audit material (the
    # exact persisted model input, the helper envelope and the selected buddy)
    # that must not sit on every console refresh.
    """
CREATE TABLE IF NOT EXISTS decision_requests (
    decision_id            TEXT PRIMARY KEY REFERENCES evaluation_decisions(decision_id) ON DELETE RESTRICT,
    request_id             TEXT NOT NULL UNIQUE,
    input_fingerprint      TEXT NOT NULL,
    task_id                TEXT,
    attempt_id             TEXT,
    generation             INTEGER,
    selected_json          TEXT,
    input_json             TEXT,
    input_sha256           TEXT,
    output_json            TEXT,
    requested_json         TEXT NOT NULL DEFAULT '{}',
    updated_at             TEXT NOT NULL
);
""",
    """
CREATE INDEX IF NOT EXISTS decision_requests_task_idx ON decision_requests(task_id);
""",
    """
CREATE INDEX IF NOT EXISTS evaluation_decisions_created_idx
    ON evaluation_decisions(created_at, decision_id);
""",
    # User-owned per-model-service concurrent-attempt limits (ADR-011). A model service is the
    # exact harness/provider/model tuple; effort variants share one row. The row
    # is independent of ``buddies`` availability on purpose: a setting
    # survives discovery marking a model unavailable, and a model service without a row
    # uses the code-owned default. Only the authenticated console writer patch in
    # ``user_policy.py`` ever writes here.
    """
CREATE TABLE IF NOT EXISTS model_service_concurrency (
    harness           TEXT NOT NULL,
    provider          TEXT NOT NULL,
    model             TEXT NOT NULL,
    concurrency_limit INTEGER NOT NULL DEFAULT 2 CHECK (concurrency_limit BETWEEN 1 AND 32),
    PRIMARY KEY (harness, provider, model)
);
""",
)

EVALUATION_SCHEMA = "\n".join(EVALUATION_TABLES)

WORKFLOW_TURNS_DDL = """
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
    -- Frozen access for this turn, independent of later run intent changes.
    access              TEXT NOT NULL CHECK (access IN ('read','write','full')),
    -- Only an explicit full turn carries the Host's one-line note.
    access_note         TEXT,
    expected_seconds    INTEGER NOT NULL CHECK (expected_seconds > 0),
    state               TEXT NOT NULL CHECK (state IN ('prepared','running','returned','failed','interrupted','rejected')),
    disposition         TEXT CHECK (disposition IN ('completed','assistance','attention','waiting')),
    outcome_json        TEXT,
    outcome_truncated_json TEXT,
    provenance_json     TEXT,
    turn_result_path    TEXT,
    sealed_artifacts_json TEXT NOT NULL DEFAULT '[]',
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL,
    CHECK ((access='full') = (access_note IS NOT NULL)),
    UNIQUE(run_id, turn_index)
);
"""

#: Current micro-task productivity workflow tables. A micro-task run is the existing
#: logical task (``run_id`` = ``task_id``) plus durable turn, request, helper,
#: continuation, workspace-reservation, pinned-artifact and attempt-scoped
#: credential records.
WORKFLOW_TABLES = (
    """
CREATE TABLE IF NOT EXISTS macro_tasks (
    macro_task_id       TEXT PRIMARY KEY,
    title              TEXT NOT NULL,
    project_id         TEXT NOT NULL,
    project_path       TEXT NOT NULL,
    source_host_id     TEXT NOT NULL,
    created_at         TEXT NOT NULL,
    activity_seq       INTEGER NOT NULL DEFAULT 0,
    activity_at        TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS macro_tasks_activity_idx ON macro_tasks(activity_seq DESC, macro_task_id DESC);
CREATE INDEX IF NOT EXISTS macro_tasks_project_idx ON macro_tasks(project_id, activity_seq DESC, macro_task_id DESC);
""",
    # The logical micro task plus its Host ownership generation and prepared workspace.
    # The immutable submitted spec and fingerprint live in tasks; continuations
    # record their own effective input in workflow_continuations.
    """
CREATE TABLE IF NOT EXISTS workflow_runs (
    run_id                  TEXT PRIMARY KEY REFERENCES tasks(task_id) ON DELETE RESTRICT,
    macro_task_id            TEXT REFERENCES macro_tasks(macro_task_id) ON DELETE RESTRICT,
    title                   TEXT,
    activity_seq            INTEGER NOT NULL DEFAULT 0,
    activity_at             TEXT,
    host_id                 TEXT,
    owner_generation        INTEGER NOT NULL DEFAULT 1,
    control_verifier        TEXT NOT NULL,
    request_fingerprint     TEXT,
    buddy_json TEXT,
    buddy_revision INTEGER NOT NULL DEFAULT 0,
    validated_buddy_revision INTEGER,
    current_routing_id      TEXT,
    submission_verifier     TEXT,
    execution_workspace_json TEXT NOT NULL,
    workspace_manifest_json TEXT,
    state                   TEXT NOT NULL
        CHECK (state IN ('executing','awaiting-host','waiting-helpers','waiting-owned','delivered','accepted','concluded','cancelled','failed')),
    active_request_id       TEXT,
    current_turn_id         TEXT,
    current_attempt_id      TEXT,
    continuation_count      INTEGER NOT NULL DEFAULT 0,
    final_artifact_id       TEXT,
    final_attempt_id        TEXT,
    revision                INTEGER NOT NULL DEFAULT 1,
    created_at              TEXT NOT NULL,
    updated_at              TEXT NOT NULL,
    buddy_locked    INTEGER NOT NULL DEFAULT 0 CHECK (buddy_locked IN (0,1)),
    delegation_authority INTEGER NOT NULL DEFAULT 0 CHECK (delegation_authority IN (0,1))
);
""",
    """
CREATE INDEX IF NOT EXISTS workflow_runs_state_idx ON workflow_runs(state, updated_at);
CREATE INDEX IF NOT EXISTS workflow_runs_macro_task_idx ON workflow_runs(macro_task_id, activity_seq DESC, run_id DESC);
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
    created_at          TEXT NOT NULL
);
""",
    """
CREATE INDEX IF NOT EXISTS workflow_routes_run_idx ON workflow_routes(run_id);
""",
    # One row per actual execution turn. The turn input is written to disk by the
    # adapter; this row pins the service-owned identity, the bounded context and the
    # validated structured outcome. Access is the frozen permission of this
    # turn; a full-access note belongs only to that explicit choice.
    WORKFLOW_TURNS_DDL,
    """
CREATE INDEX IF NOT EXISTS workflow_turns_attempt_idx ON workflow_turns(attempt_id);
""",
    # Assistance/attention requests. Only a Host decision closes one, and only an
    # approval creates helpers.
    WORKFLOW_REQUESTS_DDL,
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
    workspace_manifest_json TEXT,
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL
);
""",
    """
CREATE INDEX IF NOT EXISTS workflow_children_parent_idx ON workflow_children(parent_run_id, state);
""",
    # Lead ownership is independent of assistance approval and survives turns.
    """
CREATE TABLE IF NOT EXISTS workflow_lead_tasks (
    owned_run_id        TEXT PRIMARY KEY REFERENCES workflow_runs(run_id) ON DELETE RESTRICT,
    lead_run_id         TEXT NOT NULL REFERENCES workflow_runs(run_id) ON DELETE RESTRICT,
    lead_turn_id        TEXT NOT NULL REFERENCES workflow_turns(turn_id) ON DELETE RESTRICT,
    created_at          TEXT NOT NULL,
    CHECK (owned_run_id <> lead_run_id)
);
""",
    """
CREATE INDEX IF NOT EXISTS workflow_lead_tasks_lead_idx ON workflow_lead_tasks(lead_run_id);
""",
    # Durable continuation inputs. An automatic continuation is one-use authority
    # tied to the request and prepared workspace; a manual continuation invalidates
    # every unconsumed automatic trigger in the same transaction.
    """
CREATE TABLE IF NOT EXISTS workflow_continuations (
    continuation_id   TEXT PRIMARY KEY,
    run_id            TEXT NOT NULL REFERENCES workflow_runs(run_id) ON DELETE RESTRICT,
    command_id        TEXT,
    kind              TEXT NOT NULL CHECK (kind IN ('manual','helper','lead-wakeup')),
    request_id        TEXT,
    trigger_event_json TEXT,
    expected_revision INTEGER NOT NULL,
    input_text        TEXT NOT NULL,
    note              TEXT,
    helper_policy     TEXT NOT NULL DEFAULT 'keep' CHECK (helper_policy IN ('cancel','keep')),
    helper_outcomes_json TEXT NOT NULL DEFAULT '[]',
    workspace_manifest_json TEXT,
    state             TEXT NOT NULL CHECK (state IN ('recorded','queued','consumed','cancelled','invalidated')),
    attempt_id        TEXT,
    turn_id           TEXT,
    created_at        TEXT NOT NULL,
    consumed_at       TEXT,
    CHECK ((kind IN ('manual','helper') AND trigger_event_json IS NULL)
        OR (kind='lead-wakeup' AND trigger_event_json IS NOT NULL
            AND json_valid(trigger_event_json) AND json_type(trigger_event_json)='object'))
);
""",
    """
CREATE INDEX IF NOT EXISTS workflow_continuations_run_idx ON workflow_continuations(run_id, state);
""",
    """
CREATE UNIQUE INDEX IF NOT EXISTS workflow_continuations_auto_unique
    ON workflow_continuations(run_id, request_id) WHERE kind='helper';
""",
    """
CREATE UNIQUE INDEX IF NOT EXISTS workflow_continuations_lead_pending_unique
    ON workflow_continuations(run_id) WHERE kind='lead-wakeup' AND state IN ('recorded','queued');
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
    # Append-only authorization scope per micro-task run. ``scope_version`` is a
    # per-run monotonic counter, deliberately distinct from the input snapshot it
    # happened to see: a later amendment never rewrites an earlier scope, an
    # earlier input manifest or the original submission. Version 1 is recorded
    # when the run is attached; a new row is only added for an approved stage.
    """
CREATE TABLE IF NOT EXISTS workflow_scope_versions (
    run_id              TEXT NOT NULL REFERENCES workflow_runs(run_id) ON DELETE RESTRICT,
    scope_version       INTEGER NOT NULL,
    command_id          TEXT,
    actor_role          TEXT NOT NULL,
    actor_id            TEXT NOT NULL,
    note                TEXT,
    write_scope_json    TEXT NOT NULL,
    manifest_sha256     TEXT,
    stopped_evidence_json TEXT NOT NULL,
    created_at          TEXT NOT NULL,
    PRIMARY KEY (run_id, scope_version),
    CHECK (actor_role IN ('host','lead','console','service','workflow','bridge'))
);
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
    observed_fingerprint    TEXT NOT NULL,
    evidence_json           TEXT NOT NULL,
    state                   TEXT NOT NULL CHECK (state IN ('open','restored','adopted','abandoned')),
    action                  TEXT CHECK (action IN ('restore','adopt','abandon')),
    resolved_paths_json     TEXT NOT NULL DEFAULT '[]',
    conflicting_paths_json  TEXT NOT NULL DEFAULT '[]',
    artifact_id             TEXT,
    actor_role              TEXT,
    actor_id                TEXT,
    note                    TEXT,
    command_id              TEXT,
    delivery_json           TEXT,
    created_at              TEXT NOT NULL,
    updated_at              TEXT NOT NULL,
    UNIQUE(run_id, attempt_id, observed_fingerprint),
    CHECK ((actor_role IS NULL) = (actor_id IS NULL)),
    CHECK (actor_role IS NULL OR actor_role IN ('host','lead','console','service','workflow','bridge'))
);
""",
    """
CREATE INDEX IF NOT EXISTS workflow_workspace_conflicts_run_idx ON workflow_workspace_conflicts(run_id, state);
""",
    # One immutable integration record per (artifact, target, strategy). The
    # before/after commits and trees are resolved from the actual target checkout,
    # never taken on trust from the caller; an accepted micro task must hold a verified
    # record or an explicit not-required decision bound to its final artifact.
    """
CREATE TABLE IF NOT EXISTS workflow_integrations (
    integration_id        TEXT PRIMARY KEY,
    run_id                TEXT NOT NULL REFERENCES workflow_runs(run_id) ON DELETE RESTRICT,
    artifact_id           TEXT NOT NULL,
    strategy              TEXT NOT NULL,
    binding_sha256        TEXT NOT NULL,
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
    note                  TEXT,
    command_id            TEXT,
    actor_role            TEXT NOT NULL,
    actor_id              TEXT NOT NULL,
    created_at            TEXT NOT NULL,
    CHECK (actor_role IN ('host','lead','console','service','workflow','bridge')),
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
    attempt_id      TEXT NOT NULL REFERENCES attempts(attempt_id) ON DELETE RESTRICT,
    turn_id         TEXT,
    token_verifier  TEXT NOT NULL,
    state           TEXT NOT NULL CHECK (state IN ('active','revoked'))
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
    body          TEXT NOT NULL,
    created_at    TEXT NOT NULL
);
""",
    """
CREATE INDEX IF NOT EXISTS workflow_suggestions_run_idx ON workflow_suggestions(run_id, created_at);
""",
)

WORKFLOW_SCHEMA = "\n".join(WORKFLOW_TABLES)

PROVIDER_COOLING_DDL = """
CREATE TABLE IF NOT EXISTS provider_cooling (
    harness TEXT NOT NULL,
    provider TEXT NOT NULL,
    consecutive_rejections INTEGER NOT NULL
        CHECK (typeof(consecutive_rejections)='integer' AND consecutive_rejections>0),
    cooling_until TEXT CHECK (cooling_until IS NULL OR
        (cooling_until GLOB '????-??-??T??:??:??.???Z' AND
         strftime('%Y-%m-%dT%H:%M:%fZ', cooling_until) IS cooling_until)),
    last_rejected_at TEXT NOT NULL CHECK (
        last_rejected_at GLOB '????-??-??T??:??:??.???Z' AND
        strftime('%Y-%m-%dT%H:%M:%fZ', last_rejected_at) IS last_rejected_at),
    PRIMARY KEY (harness, provider)
);
"""

HARNESS_SCHEMA = PROVIDER_COOLING_DDL + """
CREATE TABLE IF NOT EXISTS harness_account_reads (
    harness TEXT PRIMARY KEY,
    checked_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS harness_health (
    harness TEXT PRIMARY KEY CHECK (harness IN ('dsh','zcode','codex','claude')),
    manual_path TEXT,
    revision INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'unknown'
        CHECK (status IN ('unknown','ready','missing','login-required','unhealthy')),
    record_json TEXT NOT NULL DEFAULT '{}',
    checked_at TEXT,
    expires_at TEXT,
    scan_after TEXT
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
    execution_status TEXT NOT NULL CHECK (execution_status IN ('failed','cancelled','delivered')),
    note             TEXT,
    reason_code      TEXT,
    reason_params_json TEXT,
    artifact_id      TEXT REFERENCES workflow_artifacts(artifact_id) ON DELETE RESTRICT,
    actor_role       TEXT NOT NULL,
    actor_id         TEXT NOT NULL,
    command_id       TEXT NOT NULL UNIQUE,
    created_at       TEXT NOT NULL,
    CHECK (actor_role IN ('host','lead','console','service','workflow','bridge')),
    CHECK ((note IS NOT NULL AND reason_code IS NULL AND reason_params_json IS NULL)
        OR (note IS NULL AND reason_code IS NOT NULL AND reason_params_json IS NOT NULL
            AND json_valid(reason_params_json) AND json_type(reason_params_json)='object'))
);
"""
HOST_CONCLUSION_INDEX = "CREATE UNIQUE INDEX IF NOT EXISTS workflow_host_conclusions_run_idx ON workflow_host_conclusions(run_id)"
ROUTER_SCHEMA = """
CREATE TABLE IF NOT EXISTS router_settings (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    default_mode TEXT NOT NULL CHECK (default_mode IN ('fast','review')),
    budget_preset TEXT NOT NULL CHECK (budget_preset IN ('brief','standard','deep')),
    retry_interval_seconds INTEGER NOT NULL CHECK (retry_interval_seconds >= 1)
);
CREATE TABLE IF NOT EXISTS router_buddies (
    position INTEGER PRIMARY KEY CHECK (position >= 0),
    buddy_id TEXT NOT NULL UNIQUE
);
CREATE TABLE IF NOT EXISTS router_requests (
    decision_id      TEXT NOT NULL PRIMARY KEY REFERENCES decision_requests(decision_id) ON DELETE RESTRICT,
    snapshot_json    TEXT NOT NULL,
    frozen_at        TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS router_dispatches (
    task_id      TEXT NOT NULL PRIMARY KEY REFERENCES tasks(task_id) ON DELETE RESTRICT,
    decision_id  TEXT NOT NULL REFERENCES router_requests(decision_id) ON DELETE RESTRICT,
    router_index INTEGER NOT NULL CHECK (router_index >= 0),
    buddy_id   TEXT NOT NULL,
    harness      TEXT NOT NULL,
    provider     TEXT NOT NULL,
    model        TEXT NOT NULL,
    effort       TEXT NOT NULL,
    document_json TEXT NOT NULL,
    input_sha256 TEXT NOT NULL,
    created_at   TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS attempts_router_binding_unique ON attempts(attempt_id, task_id, generation);
CREATE TABLE IF NOT EXISTS router_claims (
    attempt_id TEXT NOT NULL PRIMARY KEY,
    task_id    TEXT NOT NULL REFERENCES router_dispatches(task_id) ON DELETE RESTRICT,
    generation INTEGER NOT NULL CHECK (generation >= 1),
    FOREIGN KEY (attempt_id, task_id, generation) REFERENCES attempts(attempt_id, task_id, generation) ON DELETE RESTRICT DEFERRABLE INITIALLY DEFERRED,
    UNIQUE (task_id, generation)
);
CREATE UNIQUE INDEX IF NOT EXISTS attempts_router_outcome_binding_unique ON attempts(attempt_id, task_id);
CREATE TABLE IF NOT EXISTS router_outcomes (
    receipt_id   TEXT NOT NULL PRIMARY KEY,
    plane        TEXT NOT NULL CHECK (plane IN ('routing','maintenance','portrait')),
    request_id   TEXT NOT NULL,
    router_index INTEGER NOT NULL CHECK (router_index >= 0),
    buddy_id   TEXT NOT NULL,
    phase        TEXT NOT NULL,
    task_id      TEXT REFERENCES tasks(task_id) ON DELETE RESTRICT,
    attempt_id   TEXT,
    outcome      TEXT NOT NULL CHECK (outcome IN ('answered','no_answer')),
    code         TEXT,
    event_seq    INTEGER NOT NULL UNIQUE REFERENCES events(seq) ON DELETE RESTRICT,
    CHECK (attempt_id IS NULL OR task_id IS NOT NULL),
    FOREIGN KEY (attempt_id, task_id) REFERENCES attempts(attempt_id, task_id) ON DELETE RESTRICT
);
CREATE INDEX IF NOT EXISTS router_outcomes_buddy_idx ON router_outcomes(buddy_id, event_seq);
CREATE INDEX IF NOT EXISTS router_outcomes_task_idx ON router_outcomes(task_id, event_seq);
CREATE INDEX IF NOT EXISTS router_outcomes_attempt_idx ON router_outcomes(attempt_id);
"""
CATALOG_PENDING_SCHEMA = """
CREATE TABLE IF NOT EXISTS catalog_pending (
    harness      TEXT NOT NULL REFERENCES catalog_current(harness) ON DELETE RESTRICT,
    provider     TEXT NOT NULL,
    model        TEXT NOT NULL,
    since        TEXT NOT NULL,
    efforts_json TEXT NOT NULL,
    PRIMARY KEY (harness, provider, model)
);
"""
FIX_SCHEMA = """
CREATE TABLE IF NOT EXISTS workflow_fixes (
    run_id TEXT NOT NULL PRIMARY KEY REFERENCES workflow_runs(run_id) ON DELETE RESTRICT,
    fixed_run_id TEXT NOT NULL REFERENCES workflow_runs(run_id) ON DELETE RESTRICT,
    created_at TEXT NOT NULL,
    CHECK (run_id <> fixed_run_id)
);
CREATE INDEX IF NOT EXISTS workflow_fixes_target_idx ON workflow_fixes(fixed_run_id, created_at);
"""
REMINDER_SCHEMA = """
CREATE TABLE IF NOT EXISTS reminder_settings (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    silence_seconds INTEGER NOT NULL CHECK (silence_seconds >= 1),
    updated_at TEXT NOT NULL,
    revision INTEGER NOT NULL DEFAULT 1 CHECK (revision >= 1)
);
CREATE TABLE IF NOT EXISTS attempt_reminders (
    reminder_id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT NOT NULL REFERENCES tasks(task_id) ON DELETE RESTRICT,
    attempt_id TEXT NOT NULL REFERENCES attempts(attempt_id) ON DELETE RESTRICT,
    kind TEXT NOT NULL CHECK (kind IN ('expected-duration','native-silence')),
    expected_multiple INTEGER,
    silence_started_at TEXT,
    last_native_event_at TEXT,
    tool_in_progress INTEGER NOT NULL CHECK (tool_in_progress IN (0,1)),
    created_at TEXT NOT NULL,
    delivered_at TEXT,
    delivery_id TEXT UNIQUE,
    CHECK ((delivered_at IS NULL AND delivery_id IS NULL)
        OR (delivered_at IS NOT NULL AND delivery_id IS NOT NULL)),
    CHECK ((kind = 'expected-duration' AND expected_multiple IS NOT NULL
            AND expected_multiple >= 1 AND silence_started_at IS NULL)
        OR (kind = 'native-silence' AND expected_multiple IS NULL AND silence_started_at IS NOT NULL))
);
CREATE UNIQUE INDEX IF NOT EXISTS attempt_reminders_expected_idx
    ON attempt_reminders(attempt_id, expected_multiple) WHERE kind = 'expected-duration';
CREATE UNIQUE INDEX IF NOT EXISTS attempt_reminders_silence_idx
    ON attempt_reminders(attempt_id, silence_started_at) WHERE kind = 'native-silence';
"""
SCHEMA = "\n".join((CORE_SCHEMA, MESSAGES_DDL, MACRO_TASK_DELETIONS_DDL, EVALUATION_SCHEMA, WORKFLOW_SCHEMA, HARNESS_SCHEMA,
                    HOST_CONCLUSION_TABLE, HOST_CONCLUSION_INDEX + ";", ROUTER_SCHEMA,
                    CATALOG_PENDING_SCHEMA, REMINDER_SCHEMA, FIX_SCHEMA))


def utc_now() -> str:
    """Canonical timestamp used for every durable row."""
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


class Corruption(RuntimeError):
    """The database cannot be used as the current board."""


class Database:
    """Owns the SQLite file, current schema, pragmas and capability secret."""

    def __init__(self, directory: Path):
        self.directory = Path(directory)
        self.path = self.directory / DB_FILE
        self._lock = threading.Lock()
        self._secret: bytes | None = None
        self._connection_lock = threading.Lock()
        self._connections: dict[tuple[int, int], sqlite3.Connection] = {}
        self._connection_files: dict[tuple[int, int], tuple[int, int]] = {}
        self._connection_depths: dict[int, int] = {}
        self._file_identity: tuple[int, int] | None = None
        self._closed = False

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
        """Reuse one configured connection per thread and nesting depth."""
        # Native C-Two callbacks may rebuild their Python thread state between
        # calls. Keep ownership by thread identity, not in threading.local().
        identity = threading.get_ident()
        with self._connection_lock:
            if self._closed:
                raise RuntimeError("Database is closed")
            if self._connections:
                information = self.path.stat()
                file_identity = (information.st_dev, information.st_ino)
                if file_identity != self._file_identity:
                    # A restore replaces the file. Retire idle connections now;
                    # active transactions keep their original file until exit.
                    self._file_identity = file_identity
                    for old_key, old_connection in tuple(self._connections.items()):
                        owner, level = old_key
                        if level >= self._connection_depths.get(owner, 0):
                            old_connection.close()
                            del self._connections[old_key]
                            del self._connection_files[old_key]
            depth = self._connection_depths.get(identity, 0)
            key = (identity, depth)
            connection = self._connections.get(key)
            if connection is None:
                # Shutdown and file replacement may close other threads' idle connections.
                connection = sqlite3.connect(self.path, isolation_level=None, timeout=10, check_same_thread=False)
                try:
                    self._configure(connection)
                except BaseException:
                    connection.close()
                    raise
                self._connections[key] = connection
                information = self.path.stat()
                self._file_identity = (information.st_dev, information.st_ino)
                self._connection_files[key] = self._file_identity
            self._connection_depths[identity] = depth + 1
        try:
            yield connection
        finally:
            with self._connection_lock:
                self._connection_depths[identity] = depth
                if self._connection_files[key] != self._file_identity:
                    connection.close()
                    del self._connections[key]
                    del self._connection_files[key]

    def close(self) -> None:
        """Close all surviving thread connections after the service has stopped."""
        with self._connection_lock:
            self._closed = True
            for connection in self._connections.values():
                connection.close()
            self._connections.clear()
            self._connection_files.clear()
            self._connection_depths.clear()

    @contextmanager
    def write(self, immediate: bool = True) -> Iterator[sqlite3.Connection]:
        """One short write transaction; rolled back on any exception."""
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE" if immediate else "BEGIN")
            try:
                yield connection
                connection.execute("COMMIT")
            except BaseException:
                connection.rollback()
                raise

    @contextmanager
    def read(self) -> Iterator[sqlite3.Connection]:
        """One short read transaction, so a multi-query view is internally coherent."""
        with self.connect() as connection:
            connection.execute("BEGIN")
            try:
                yield connection
                connection.execute("COMMIT")
            except BaseException:
                connection.rollback()
                raise

    # -- lifecycle -----------------------------------------------------------
    def _require_current_schema(self) -> None:
        """Refuse unsupported files before opening a writable/WAL connection."""
        guidance = (f"This build accepts only schema {SCHEMA_VERSION}. "
                    "Retain other unsupported state as an archive")
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
                if fresh:
                    connection.executescript(SCHEMA)
                version = connection.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
                if version is None:
                    connection.execute("BEGIN IMMEDIATE")
                    try:
                        connection.execute(
                            "INSERT INTO meta(key, value) VALUES('schema_version', ?)", (str(SCHEMA_VERSION),)
                        )
                        connection.execute("COMMIT")
                    except BaseException:
                        connection.execute("ROLLBACK")
                        raise
                if fresh:
                    from ..routing.router import initialize_configuration
                    initialize_configuration(connection)
                    connection.execute(
                        "INSERT INTO reminder_settings(id, silence_seconds, updated_at, revision) "
                        "VALUES(1, 900, ?, 1)", (utc_now(),)
                    )
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

    def nonce_verifier(self, nonce: str) -> str:
        return hmac.new(self.secret, f"nonce:{nonce}".encode("utf-8"), hashlib.sha256).hexdigest()



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
