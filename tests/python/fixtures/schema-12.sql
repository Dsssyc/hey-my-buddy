-- Frozen schema-12 DDL (src/buddy/db.py SCHEMA at 6e10cd7); the 12 -> 13 migration source.
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


CREATE TABLE IF NOT EXISTS evaluation_annotations (
    profile_id TEXT PRIMARY KEY REFERENCES evaluation_profiles(profile_id) ON DELETE RESTRICT,
    text TEXT NOT NULL,
    revision INTEGER NOT NULL,
    updated_at TEXT NOT NULL
);


CREATE TABLE IF NOT EXISTS evaluation_state (
    id                      INTEGER PRIMARY KEY CHECK (id = 1),
    table_revision          INTEGER NOT NULL DEFAULT 0,
    configuration_revision  INTEGER NOT NULL DEFAULT 0,
    decision_profile_id     TEXT,
    writer_sequence         INTEGER NOT NULL DEFAULT 0,
    created_at              TEXT NOT NULL,
    updated_at              TEXT NOT NULL
);


CREATE TABLE IF NOT EXISTS evaluation_revisions (
    revision     INTEGER PRIMARY KEY,
    kind         TEXT NOT NULL,
    writer_id    TEXT,
    actor        TEXT,
    counts_json  TEXT NOT NULL DEFAULT '{}',
    created_at   TEXT NOT NULL
);


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


CREATE INDEX IF NOT EXISTS evaluation_profiles_current_idx ON evaluation_profiles(available, profile_id);
CREATE INDEX IF NOT EXISTS evaluation_profiles_adapter_idx ON evaluation_profiles(adapter, profile_id);


CREATE TABLE IF NOT EXISTS evaluation_preferences (
    profile_id        TEXT PRIMARY KEY REFERENCES evaluation_profiles(profile_id) ON DELETE RESTRICT,
    mode              TEXT NOT NULL CHECK (mode IN ('prefer','pin','exclude')),
    reason            TEXT NOT NULL DEFAULT '',
    updated_revision  INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS evaluation_preferences_mode_idx ON evaluation_preferences(mode, profile_id);


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


CREATE INDEX IF NOT EXISTS evaluation_evidence_profile_idx
    ON evaluation_evidence(profile_id, created_at);


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


CREATE TABLE IF NOT EXISTS evaluation_catalog (
    discovery_id      TEXT PRIMARY KEY,
    discovered_at     TEXT NOT NULL,
    source            TEXT NOT NULL,
    harness_version   TEXT,
    provider_version  TEXT,
    payload_json      TEXT NOT NULL,
    created_at        TEXT NOT NULL
);


CREATE TABLE IF NOT EXISTS evaluation_readers (
    reader_id     TEXT PRIMARY KEY,
    kind          TEXT NOT NULL,
    admitted_at   TEXT NOT NULL,
    expires_at    TEXT NOT NULL,
    released_at   TEXT,
    expired       INTEGER NOT NULL DEFAULT 0
);


CREATE INDEX IF NOT EXISTS evaluation_readers_open_idx ON evaluation_readers(released_at, expires_at);


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


CREATE INDEX IF NOT EXISTS evaluation_writers_queue_idx ON evaluation_writers(state, generation);


CREATE TABLE IF NOT EXISTS evaluation_evidence_pending (
    evidence_id  TEXT PRIMARY KEY,
    profile_id   TEXT NOT NULL,
    created_at   TEXT NOT NULL
);


CREATE INDEX IF NOT EXISTS evaluation_evidence_pending_profile_idx
    ON evaluation_evidence_pending(profile_id, created_at);


CREATE INDEX IF NOT EXISTS evaluation_evidence_pending_created_idx
    ON evaluation_evidence_pending(created_at, evidence_id);


CREATE INDEX IF NOT EXISTS evaluation_evidence_created_idx
    ON evaluation_evidence(created_at, evidence_id);


CREATE INDEX IF NOT EXISTS evaluation_evidence_run_idx ON evaluation_evidence(run_id);


CREATE INDEX IF NOT EXISTS evaluation_evidence_counted_idx ON evaluation_evidence(profile_id, counted);


CREATE TABLE IF NOT EXISTS evaluation_samples (
    profile_id   TEXT NOT NULL,
    attempt_id   TEXT NOT NULL,
    task_id      TEXT NOT NULL,
    verdict      TEXT NOT NULL,
    evidence_id  TEXT,
    created_at   TEXT NOT NULL,
    PRIMARY KEY (profile_id, attempt_id)
);


CREATE INDEX IF NOT EXISTS evaluation_samples_profile_idx ON evaluation_samples(profile_id, created_at);


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


CREATE INDEX IF NOT EXISTS evaluation_card_history_profile_idx
    ON evaluation_card_history(profile_id, table_revision);


CREATE TABLE IF NOT EXISTS evaluation_aggregates (
    name        TEXT PRIMARY KEY,
    value       INTEGER NOT NULL,
    updated_at  TEXT NOT NULL
);


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


CREATE INDEX IF NOT EXISTS decision_requests_created_idx ON decision_requests(created_at, decision_id);
CREATE INDEX IF NOT EXISTS decision_requests_task_idx ON decision_requests(task_id);


CREATE INDEX IF NOT EXISTS evaluation_decisions_created_idx
    ON evaluation_decisions(created_at, decision_id);


CREATE INDEX IF NOT EXISTS events_review_seq_idx
    ON events(seq) WHERE kind IN ('task.accepted','task.rejected','workflow.acknowledged');


CREATE TABLE IF NOT EXISTS evaluation_maintenance_checkpoints (
    scope        TEXT PRIMARY KEY,
    review_seq   INTEGER NOT NULL,
    updated_at   TEXT NOT NULL
);


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
    updated_at              TEXT NOT NULL
);


CREATE INDEX IF NOT EXISTS workflow_runs_state_idx ON workflow_runs(state, updated_at);
CREATE INDEX IF NOT EXISTS workflow_runs_objective_idx ON workflow_runs(objective_id, activity_seq DESC, run_id DESC);
CREATE INDEX IF NOT EXISTS workflow_runs_activity_idx ON workflow_runs(activity_seq);


CREATE TABLE IF NOT EXISTS workflow_routes (
    decision_id         TEXT PRIMARY KEY REFERENCES decision_requests(decision_id) ON DELETE RESTRICT,
    run_id              TEXT NOT NULL REFERENCES workflow_runs(run_id) ON DELETE RESTRICT,
    owner_generation    INTEGER NOT NULL,
    state               TEXT NOT NULL CHECK (state IN ('pending','resolved','needs-host','fenced')),
    reason              TEXT,
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL
);


CREATE INDEX IF NOT EXISTS workflow_routes_run_idx ON workflow_routes(run_id);


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


CREATE INDEX IF NOT EXISTS workflow_turns_run_idx ON workflow_turns(run_id, turn_index);


CREATE INDEX IF NOT EXISTS workflow_turns_attempt_idx ON workflow_turns(attempt_id);


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


CREATE INDEX IF NOT EXISTS workflow_requests_run_idx ON workflow_requests(run_id, state);


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


CREATE INDEX IF NOT EXISTS workflow_children_parent_idx ON workflow_children(parent_run_id, state);


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


CREATE INDEX IF NOT EXISTS workflow_continuations_run_idx ON workflow_continuations(run_id, state);


CREATE UNIQUE INDEX IF NOT EXISTS workflow_continuations_auto_unique
    ON workflow_continuations(run_id, request_id) WHERE authorized_by='auto';


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


CREATE UNIQUE INDEX IF NOT EXISTS workspace_reservations_writer_unique
    ON workspace_reservations(checkout_id) WHERE state='held' AND access='write';


CREATE INDEX IF NOT EXISTS workspace_reservations_checkout_idx ON workspace_reservations(checkout_id, state);


CREATE INDEX IF NOT EXISTS workspace_reservations_holder_idx ON workspace_reservations(holder_task_id, state);


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


CREATE INDEX IF NOT EXISTS workflow_scope_versions_run_idx ON workflow_scope_versions(run_id, scope_version);


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


CREATE INDEX IF NOT EXISTS workflow_workspace_conflicts_run_idx ON workflow_workspace_conflicts(run_id, state);


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


CREATE INDEX IF NOT EXISTS workflow_integrations_run_idx ON workflow_integrations(run_id, artifact_id, created_at);


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


CREATE INDEX IF NOT EXISTS workspace_cleanup_plans_run_idx ON workspace_cleanup_plans(run_id, state);


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


CREATE UNIQUE INDEX IF NOT EXISTS agent_credentials_attempt_unique
    ON agent_credentials(attempt_id) WHERE state='active';


CREATE INDEX IF NOT EXISTS agent_credentials_run_idx ON agent_credentials(run_id, state);


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


CREATE INDEX IF NOT EXISTS workflow_artifacts_run_idx ON workflow_artifacts(run_id, created_at);


CREATE TABLE IF NOT EXISTS workflow_suggestions (
    suggestion_id TEXT PRIMARY KEY,
    run_id        TEXT NOT NULL REFERENCES workflow_runs(run_id) ON DELETE RESTRICT,
    attempt_id    TEXT,
    author        TEXT NOT NULL,
    body          TEXT NOT NULL,
    created_at    TEXT NOT NULL
);


CREATE INDEX IF NOT EXISTS workflow_suggestions_run_idx ON workflow_suggestions(run_id, created_at);
