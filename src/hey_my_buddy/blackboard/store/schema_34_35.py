"""Schema 34 → 35: direct responses, frozen project facts and typed results.

This step owns its DDL and conversion. The installer supplies the transaction and
disables foreign keys around the rebuild, then checks all references before commit.
"""
from __future__ import annotations

import hashlib
import json
import os


TABLE_DDL = {
    'projects': """CREATE TABLE projects (
    project_id TEXT PRIMARY KEY,
    kind       TEXT NOT NULL CHECK (kind IN ('git','folder')),
    path       TEXT NOT NULL
)""",
    'resource_claims': """CREATE TABLE resource_claims (
    claim_id    TEXT PRIMARY KEY,
    task_id     TEXT NOT NULL REFERENCES tasks(task_id) ON DELETE RESTRICT,
    attempt_id  TEXT,
    resource    TEXT NOT NULL,
    kind        TEXT NOT NULL CHECK (kind='cwd'),
    state       TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    UNIQUE(task_id, resource)
)""",
    'tasks': """CREATE TABLE "tasks" (
    task_id                 TEXT PRIMARY KEY,
    request_id              TEXT NOT NULL UNIQUE,
    spec_json               TEXT NOT NULL,
    input_fingerprint       TEXT NOT NULL,
    fingerprint_version     INTEGER NOT NULL,
    adapter                 TEXT NOT NULL,
    cwd                     TEXT NOT NULL,
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
    CHECK ((adapter='decision' AND json_extract(spec_json,'$.expectedSeconds') IS NULL)
        OR (adapter!='decision' AND json_type(spec_json,'$.expectedSeconds') IS 'integer'
            AND json_extract(spec_json,'$.expectedSeconds') > 0))
)""",
    'events': """CREATE TABLE events (
    seq          INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id      TEXT REFERENCES tasks(task_id) ON DELETE RESTRICT,
    attempt_id   TEXT,
    revision     INTEGER,
    kind         TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    actor_role   TEXT,
    actor_id     TEXT,
    created_at   TEXT NOT NULL,
    CHECK ((actor_role IS NULL AND actor_id IS NULL)
        OR actor_role='host'
        OR (actor_role IS NOT NULL AND actor_role!='host' AND actor_id IS NOT NULL)),
    CHECK (actor_role IS NULL OR actor_role IN ('host','lead','console','service','workflow','bridge'))
)""",
    'macro_tasks': """CREATE TABLE macro_tasks (
    macro_task_id       TEXT PRIMARY KEY,
    title              TEXT NOT NULL,
    project_id         TEXT NOT NULL REFERENCES projects(project_id) ON DELETE RESTRICT,
    project_kind       TEXT NOT NULL CHECK (project_kind IN ('git','folder')),
    project_cwd       TEXT NOT NULL,
    repository_path   TEXT,
    creation_key      TEXT,
    created_at         TEXT NOT NULL,
    activity_seq       INTEGER NOT NULL DEFAULT 0,
    activity_at        TEXT NOT NULL
)""",
    'workflow_runs': """CREATE TABLE workflow_runs (
    run_id                  TEXT PRIMARY KEY REFERENCES tasks(task_id) ON DELETE RESTRICT,
    macro_task_id            TEXT REFERENCES macro_tasks(macro_task_id) ON DELETE RESTRICT,
    standalone_project_id    TEXT REFERENCES projects(project_id) ON DELETE RESTRICT,
    project_kind             TEXT NOT NULL CHECK (project_kind IN ('git','folder')),
    checkout                 TEXT NOT NULL CHECK (checkout IN ('existing','worktree','none')),
    title                   TEXT,
    activity_seq            INTEGER NOT NULL DEFAULT 0,
    activity_at             TEXT,
    creation_key            TEXT,
    buddy_json TEXT,
    buddy_revision INTEGER NOT NULL DEFAULT 0,
    validated_buddy_revision INTEGER,
    current_routing_id      TEXT,
    execution_workspace_json TEXT NOT NULL,
    workspace_id            TEXT,
    workspace_manifest_sha256 TEXT,
    state                   TEXT NOT NULL
        CHECK (state IN ('executing','awaiting-host','waiting-helpers','waiting-owned','delivered','accepted','concluded','cancelled','failed')),
    active_request_id       TEXT,
    current_turn_id         TEXT,
    current_attempt_id      TEXT,
    continuation_count      INTEGER NOT NULL DEFAULT 0,
    final_artifact_id       TEXT,
    final_attempt_id        TEXT,
    created_at              TEXT NOT NULL,
    updated_at              TEXT NOT NULL,
    delegation_authority INTEGER NOT NULL DEFAULT 0 CHECK (delegation_authority IN (0,1)),
    CHECK ((macro_task_id IS NULL AND standalone_project_id IS NOT NULL)
        OR (macro_task_id IS NOT NULL AND standalone_project_id IS NULL)),
    CHECK ((checkout='none' AND workspace_id IS NULL AND workspace_manifest_sha256 IS NULL)
        OR (checkout IN ('existing','worktree') AND project_kind='git' AND
            ((state IN ('accepted','concluded') AND workspace_id IS NULL AND workspace_manifest_sha256 IS NULL)
            OR (state NOT IN ('accepted','concluded') AND workspace_id IS NOT NULL AND workspace_manifest_sha256 IS NOT NULL))))
)""",
    'workflow_routes': """CREATE TABLE workflow_routes (
    decision_id         TEXT PRIMARY KEY REFERENCES decision_requests(decision_id) ON DELETE RESTRICT,
    run_id              TEXT NOT NULL REFERENCES workflow_runs(run_id) ON DELETE RESTRICT,
    state               TEXT NOT NULL CHECK (state IN ('pending','resolved','needs-host','fenced')),
    created_at          TEXT NOT NULL
)""",
    'workflow_turns': """CREATE TABLE workflow_turns (
    turn_id             TEXT PRIMARY KEY,
    run_id              TEXT NOT NULL REFERENCES workflow_runs(run_id) ON DELETE RESTRICT,
    attempt_id          TEXT REFERENCES attempts(attempt_id) ON DELETE RESTRICT,
    generation          INTEGER,
    turn_index          INTEGER NOT NULL,
    resume_mode         TEXT NOT NULL
        CHECK (resume_mode IN ('initial','reconstructed-new-session','native-session')),
    previous_session_id TEXT,
    input_json          TEXT NOT NULL,
    workspace_id        TEXT,
    workspace_manifest_sha256 TEXT,
    response_kind       TEXT,
    response_id         TEXT,
    -- Frozen access for this turn, independent of later run intent changes.
    access              TEXT NOT NULL CHECK (access IN ('read','write','full')),
    -- Only an explicit full turn carries the Host's one-line note.
    access_note         TEXT,
    expected_seconds    INTEGER NOT NULL CHECK (expected_seconds > 0),
    state               TEXT NOT NULL CHECK (state IN ('prepared','running','returned','failed','interrupted','rejected')),
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL,
    CHECK ((response_kind IS NULL) = (response_id IS NULL)),
    CHECK (response_kind IS NULL OR response_kind='continue'),
    CHECK ((access='full') = (access_note IS NOT NULL)),
    CHECK ((json_extract(input_json,'$.version') IS 5
            AND json_extract(input_json,'$.executionWorkspace.kind') IS 'none'
            AND workspace_id IS NULL AND workspace_manifest_sha256 IS NULL)
        OR (json_type(input_json,'$.version') IS 'integer'
            AND json_extract(input_json,'$.version') BETWEEN 1 AND 4
            AND workspace_id IS NOT NULL AND workspace_manifest_sha256 IS NOT NULL)),
    UNIQUE(run_id, turn_index)
)""",
    'workflow_requests': """CREATE TABLE workflow_requests (
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
    response_kind       TEXT,
    response_id         TEXT,
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL,
    decided_at          TEXT,
    CHECK ((response_kind IS NULL) = (response_id IS NULL)),
    CHECK (response_kind IS NULL OR response_kind IN ('decision','continue','resolution')),
    CHECK ((summary IS NOT NULL AND reason_code IS NULL AND reason_params_json IS NULL AND decision_id IS NULL)
        OR (summary IS NULL AND reason_code IS NOT NULL AND reason_params_json IS NOT NULL
            AND json_valid(reason_params_json) AND json_type(reason_params_json)='object')
        OR (summary IS NULL AND reason_code IS NULL AND reason_params_json IS NULL AND decision_id IS NOT NULL))
)""",
    'workflow_children': """CREATE TABLE workflow_children (
    child_task_id       TEXT PRIMARY KEY REFERENCES tasks(task_id) ON DELETE RESTRICT,
    parent_run_id       TEXT NOT NULL REFERENCES workflow_runs(run_id) ON DELETE RESTRICT,
    request_id          TEXT NOT NULL,
    state               TEXT NOT NULL CHECK (state IN ('active','succeeded','failed','cancelled','attention')),
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL
)""",
    'workflow_continuations': """CREATE TABLE workflow_continuations (
    continuation_id   TEXT PRIMARY KEY,
    run_id            TEXT NOT NULL REFERENCES workflow_runs(run_id) ON DELETE RESTRICT,
    controlling_run_id TEXT REFERENCES workflow_runs(run_id) ON DELETE RESTRICT,
    kind              TEXT NOT NULL CHECK (kind IN ('manual','helper','lead-wakeup')),
    request_id        TEXT,
    artifact_id       TEXT REFERENCES workflow_artifacts(artifact_id) ON DELETE RESTRICT,
    response_turn_id  TEXT REFERENCES workflow_turns(turn_id) ON DELETE RESTRICT,
    buddy_selection_json TEXT,
    access_selection_json TEXT,
    resolution_json   TEXT,
    trigger_event_json TEXT,
    input_text        TEXT NOT NULL,
    note              TEXT,
    helper_policy     TEXT CHECK (helper_policy IN ('cancel','keep') OR helper_policy IS NULL),
    helper_outcomes_json TEXT NOT NULL DEFAULT '[]',
    workspace_id      TEXT,
    workspace_manifest_sha256 TEXT,
    workspace_facts_role TEXT NOT NULL CHECK (workspace_facts_role IN ('pending','owner','reference','none')),
    workspace_kind    TEXT CHECK (workspace_kind IN ('existing','worktree')),
    checkout_root     TEXT,
    repository_path   TEXT,
    input_commit      TEXT,
    staged_commit     TEXT,
    state             TEXT NOT NULL CHECK (state IN ('recorded','queued','consumed','cancelled','invalidated')),
    attempt_id        TEXT,
    turn_id           TEXT,
    created_at        TEXT NOT NULL,
    consumed_at       TEXT,
    CHECK (kind!='manual' OR (request_id IS NOT NULL) + (artifact_id IS NOT NULL) + (response_turn_id IS NOT NULL) <= 1),
    CHECK (buddy_selection_json IS NULL OR json_valid(buddy_selection_json)),
    CHECK (access_selection_json IS NULL OR json_valid(access_selection_json)),
    CHECK (resolution_json IS NULL OR json_valid(resolution_json)),
    CHECK ((workspace_facts_role='none' AND workspace_id IS NULL AND workspace_manifest_sha256 IS NULL
            AND workspace_kind IS NULL AND checkout_root IS NULL AND repository_path IS NULL
            AND input_commit IS NULL AND staged_commit IS NULL)
        OR ((workspace_facts_role='pending' AND workspace_id IS NULL AND workspace_manifest_sha256 IS NULL
            AND workspace_kind IS NULL AND checkout_root IS NULL AND repository_path IS NULL
            AND input_commit IS NULL AND staged_commit IS NULL)
        OR (workspace_facts_role='owner' AND workspace_id IS NOT NULL AND workspace_manifest_sha256 IS NOT NULL
            AND workspace_kind IS NOT NULL AND checkout_root IS NOT NULL AND repository_path IS NOT NULL
            AND input_commit IS NOT NULL AND staged_commit IS NOT NULL)
        OR (workspace_facts_role='reference' AND workspace_id IS NOT NULL AND workspace_manifest_sha256 IS NOT NULL
            AND workspace_kind IS NULL AND checkout_root IS NULL AND repository_path IS NULL
            AND input_commit IS NULL AND staged_commit IS NULL))),
    CHECK ((kind IN ('manual','helper') AND trigger_event_json IS NULL)
        OR (kind='lead-wakeup' AND trigger_event_json IS NOT NULL
            AND json_valid(trigger_event_json) AND json_type(trigger_event_json)='object'))
)""",
    'workflow_scope_versions': """CREATE TABLE workflow_scope_versions (
    run_id              TEXT NOT NULL REFERENCES workflow_runs(run_id) ON DELETE RESTRICT,
    scope_version       INTEGER NOT NULL,
    actor_role          TEXT NOT NULL,
    actor_id            TEXT,
    note                TEXT,
    write_scope_json    TEXT NOT NULL,
    manifest_sha256     TEXT,
    stopped_evidence_json TEXT NOT NULL,
    created_at          TEXT NOT NULL,
    PRIMARY KEY (run_id, scope_version),
    CHECK (actor_role IN ('host','lead','console','service','workflow','bridge')),
    CHECK (actor_role='host' OR actor_id IS NOT NULL)
)""",
    'workflow_workspace_conflicts': """CREATE TABLE workflow_workspace_conflicts (
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
    delivery_json           TEXT,
    created_at              TEXT NOT NULL,
    updated_at              TEXT NOT NULL,
    UNIQUE(run_id, attempt_id, observed_fingerprint),
    CHECK ((actor_role IS NULL AND actor_id IS NULL)
        OR actor_role='host'
        OR (actor_role IS NOT NULL AND actor_role!='host' AND actor_id IS NOT NULL)),
    CHECK (actor_role IS NULL OR actor_role IN ('host','lead','console','service','workflow','bridge'))
)""",
    'workflow_integrations': """CREATE TABLE workflow_integrations (
    integration_id        TEXT PRIMARY KEY,
    run_id                TEXT NOT NULL REFERENCES workflow_runs(run_id) ON DELETE RESTRICT,
    artifact_id           TEXT NOT NULL,
    strategy              TEXT NOT NULL,
    binding_sha256        TEXT NOT NULL,
    target_path           TEXT NOT NULL DEFAULT '',
    target_ref            TEXT NOT NULL DEFAULT '',
    target_project_id     TEXT, -- NULL when the target was unmarked at verification
    source_commit         TEXT,
    source_tree           TEXT,
    before_commit         TEXT,
    after_commit          TEXT,
    before_tree           TEXT,
    after_tree            TEXT,
    verification_json     TEXT NOT NULL,
    note                  TEXT,
    actor_role            TEXT NOT NULL,
    actor_id              TEXT,
    created_at            TEXT NOT NULL,
    CHECK (actor_role IN ('host','lead','console','service','workflow','bridge')),
    CHECK (actor_role='host' OR actor_id IS NOT NULL),
    UNIQUE(binding_sha256)
)""",
    'workflow_artifacts': """CREATE TABLE workflow_artifacts (
    artifact_id     TEXT PRIMARY KEY,
    run_id          TEXT NOT NULL REFERENCES workflow_runs(run_id) ON DELETE RESTRICT,
    turn_id         TEXT,
    attempt_id      TEXT,
    kind            TEXT NOT NULL CHECK (kind IN ('input','turn-input','output','resolved-output','partial-output','abandoned-site','shared-refs','result')),
    content_form    TEXT NOT NULL CHECK (content_form IN ('sealed-inline-v1','input-reference','record-reference','result-reference')),
    manifest_json   TEXT,
    workspace_id    TEXT,
    input_manifest_sha256 TEXT,
    output_snapshot_sha256 TEXT,
    shared_refs_sha256 TEXT,
    record_path     TEXT,
    record_sha256   TEXT,
    response_kind   TEXT,
    response_id     TEXT,
    workspace_facts_role TEXT CHECK (workspace_facts_role IN ('owner','reference')),
    workspace_kind  TEXT CHECK (workspace_kind IN ('existing','worktree')),
    checkout_root   TEXT,
    repository_path TEXT,
    input_commit    TEXT,
    staged_commit   TEXT,
    output_workspace_id TEXT,
    output_manifest_sha256 TEXT,
    output_commit   TEXT,
    output_action   TEXT CHECK (output_action IN ('restore','adopt')),
    output_origin   TEXT CHECK (output_origin IN ('worker','host')),
    created_at      TEXT NOT NULL,
    CHECK ((response_kind IS NULL) = (response_id IS NULL)),
    CHECK (response_kind IS NULL OR response_kind IN ('accept','continue','conclude')),
    CHECK (response_kind IS NULL OR kind IN ('output','resolved-output','partial-output','abandoned-site','result')),
    CHECK ((kind IN ('input','turn-input') AND workspace_id IS NOT NULL AND input_manifest_sha256 IS NOT NULL
            AND output_snapshot_sha256 IS NULL AND shared_refs_sha256 IS NULL)
        OR (kind IN ('output','resolved-output','partial-output','abandoned-site') AND workspace_id IS NULL
            AND input_manifest_sha256 IS NULL AND output_snapshot_sha256 IS NOT NULL AND shared_refs_sha256 IS NULL)
        OR (kind='shared-refs' AND workspace_id IS NULL AND input_manifest_sha256 IS NULL
            AND output_snapshot_sha256 IS NULL AND shared_refs_sha256 IS NOT NULL)
        OR (kind='result' AND workspace_id IS NULL AND input_manifest_sha256 IS NULL
            AND output_snapshot_sha256 IS NULL AND shared_refs_sha256 IS NULL)),
    CHECK ((content_form='sealed-inline-v1' AND manifest_json IS NOT NULL AND record_path IS NULL AND record_sha256 IS NULL)
        OR (content_form='input-reference' AND kind='input' AND manifest_json IS NULL
            AND record_path IS NULL AND record_sha256 IS NULL)
        OR (content_form='record-reference' AND kind IN ('output','resolved-output','partial-output','abandoned-site')
            AND manifest_json IS NULL AND record_path IS NOT NULL AND record_sha256 IS NOT NULL)
        OR (content_form='result-reference' AND kind='result' AND turn_id IS NOT NULL AND attempt_id IS NOT NULL
            AND manifest_json IS NULL AND record_path IS NOT NULL AND record_sha256 IS NOT NULL)),
    CHECK ((kind='result') = (content_form='result-reference')),
    CHECK ((kind='input' AND workspace_facts_role='owner' AND workspace_kind IS NOT NULL
            AND checkout_root IS NOT NULL AND repository_path IS NOT NULL
            AND input_commit IS NOT NULL AND staged_commit IS NOT NULL)
        OR (kind='input' AND workspace_facts_role='reference' AND workspace_kind IS NULL
            AND checkout_root IS NULL AND repository_path IS NULL
            AND input_commit IS NULL AND staged_commit IS NULL)
        OR (kind!='input' AND workspace_facts_role IS NULL AND workspace_kind IS NULL
            AND checkout_root IS NULL AND repository_path IS NULL
            AND input_commit IS NULL AND staged_commit IS NULL)),
    CHECK (kind!='input' OR workspace_facts_role IS NOT NULL),
    CHECK ((kind IN ('output','resolved-output','partial-output','abandoned-site')
            AND attempt_id IS NOT NULL AND output_workspace_id IS NOT NULL
            AND output_manifest_sha256 IS NOT NULL AND output_commit IS NOT NULL)
        OR (kind NOT IN ('output','resolved-output','partial-output','abandoned-site')
            AND output_workspace_id IS NULL AND output_manifest_sha256 IS NULL
            AND output_commit IS NULL AND output_action IS NULL)),
    CHECK ((kind='resolved-output' AND output_action IS NOT NULL)
        OR (kind!='resolved-output' AND output_action IS NULL)),
    CHECK ((kind IN ('output','partial-output') AND output_origin IS NOT NULL)
        OR (kind NOT IN ('output','partial-output') AND output_origin IS NULL))
)""",
    'workflow_host_conclusions': """CREATE TABLE workflow_host_conclusions (
    conclusion_id    TEXT PRIMARY KEY,
    run_id           TEXT NOT NULL REFERENCES workflow_runs(run_id) ON DELETE RESTRICT,
    attempt_id       TEXT REFERENCES attempts(attempt_id) ON DELETE RESTRICT,
    execution_status TEXT NOT NULL CHECK (execution_status IN ('failed','cancelled','delivered')),
    note             TEXT,
    reason_code      TEXT,
    reason_params_json TEXT,
    artifact_id      TEXT REFERENCES workflow_artifacts(artifact_id) ON DELETE RESTRICT,
    actor_role       TEXT NOT NULL,
    actor_id         TEXT,
    created_at       TEXT NOT NULL,
    CHECK (actor_role IN ('host','lead','console','service','workflow','bridge')),
    CHECK (actor_role='host' OR actor_id IS NOT NULL),
    CHECK ((note IS NOT NULL AND reason_code IS NULL AND reason_params_json IS NULL)
        OR (note IS NULL AND reason_code IS NOT NULL AND reason_params_json IS NOT NULL
            AND json_valid(reason_params_json) AND json_type(reason_params_json)='object'))
)""",
}

INDEX_DDL = (
    """CREATE UNIQUE INDEX workflow_artifacts_result_unique
    ON workflow_artifacts(run_id, attempt_id) WHERE kind='result'""",
    """CREATE INDEX resource_claims_state_idx ON resource_claims(state)""",
    """CREATE INDEX events_decision_status_idx ON events(kind, seq DESC)
    WHERE kind IN ('decision.completed','decision.failed','decision.needs_host','decision.cancelled')""",
    """CREATE INDEX events_router_claimed_idx
    ON events(json_extract(payload_json, '$.buddyId'), seq)
    WHERE kind='router.claimed'""",
    """CREATE INDEX events_task_idx ON events(task_id, seq)""",
    """CREATE INDEX macro_tasks_activity_idx ON macro_tasks(activity_seq DESC, macro_task_id DESC)""",
    """CREATE UNIQUE INDEX macro_tasks_creation_key_unique ON macro_tasks(creation_key) WHERE creation_key IS NOT NULL""",
    """CREATE INDEX macro_tasks_project_idx ON macro_tasks(project_id, activity_seq DESC, macro_task_id DESC)""",
    """CREATE INDEX tasks_state_idx ON tasks(state, created_at)""",
    """CREATE UNIQUE INDEX workflow_artifacts_input_unique
    ON workflow_artifacts(run_id, attempt_id, input_manifest_sha256) WHERE input_manifest_sha256 IS NOT NULL""",
    """CREATE UNIQUE INDEX workflow_artifacts_output_unique
    ON workflow_artifacts(run_id, attempt_id, output_snapshot_sha256) WHERE output_snapshot_sha256 IS NOT NULL""",
    """CREATE INDEX workflow_artifacts_run_idx ON workflow_artifacts(run_id, created_at)""",
    """CREATE UNIQUE INDEX workflow_artifacts_shared_refs_unique
    ON workflow_artifacts(run_id, attempt_id, shared_refs_sha256) WHERE shared_refs_sha256 IS NOT NULL""",
    """CREATE UNIQUE INDEX workflow_artifacts_workspace_owner_unique
    ON workflow_artifacts(workspace_id, input_manifest_sha256) WHERE workspace_facts_role='owner'""",
    """CREATE INDEX workflow_children_parent_idx ON workflow_children(parent_run_id, state)""",
    """CREATE UNIQUE INDEX workflow_continuations_auto_unique
    ON workflow_continuations(run_id, request_id) WHERE kind='helper'""",
    """CREATE UNIQUE INDEX workflow_continuations_lead_pending_unique
    ON workflow_continuations(run_id) WHERE kind='lead-wakeup' AND state IN ('recorded','queued')""",
    """CREATE INDEX workflow_continuations_run_idx ON workflow_continuations(run_id, state)""",
    """CREATE UNIQUE INDEX workflow_continuations_workspace_owner_unique
    ON workflow_continuations(workspace_id, workspace_manifest_sha256) WHERE workspace_facts_role='owner'""",
    """CREATE UNIQUE INDEX workflow_host_conclusions_run_idx ON workflow_host_conclusions(run_id)""",
    """CREATE INDEX workflow_integrations_run_idx ON workflow_integrations(run_id, artifact_id, created_at)""",
    """CREATE INDEX workflow_requests_run_idx ON workflow_requests(run_id, state)""",
    """CREATE INDEX workflow_routes_run_idx ON workflow_routes(run_id)""",
    """CREATE INDEX workflow_runs_activity_idx ON workflow_runs(activity_seq)""",
    """CREATE UNIQUE INDEX workflow_runs_creation_key_unique ON workflow_runs(creation_key) WHERE creation_key IS NOT NULL""",
    """CREATE INDEX workflow_runs_macro_task_idx ON workflow_runs(macro_task_id, activity_seq DESC, run_id DESC)""",
    """CREATE INDEX workflow_runs_state_idx ON workflow_runs(state, updated_at)""",
    """CREATE INDEX workflow_turns_attempt_idx ON workflow_turns(attempt_id)""",
    """CREATE INDEX workflow_workspace_conflicts_run_idx ON workflow_workspace_conflicts(run_id, state)""",
)


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _rows(connection, table):
    return [dict(row) for row in connection.execute(f'SELECT rowid,* FROM "{table}" ORDER BY rowid')]


def _git_project_path(repository_path):
    path = os.path.normpath(repository_path)
    return os.path.dirname(path) if os.path.basename(path) == '.git' else path


def _macro_paths(stock):
    """Recover the creating Git location from retained source facts, never live Git."""
    tasks = {row['task_id']: row for row in stock['tasks']}
    by_macro = {}
    for run in stock['workflow_runs']:
        if run['macro_task_id'] is not None:
            by_macro.setdefault(run['macro_task_id'], []).append(run)
    owners = {(row['workspace_id'], row['input_manifest_sha256']): row['repository_path']
              for row in stock['workflow_artifacts'] if row['kind'] == 'input'
              and row['workspace_facts_role'] == 'owner'}
    inputs = {}
    for row in stock['workflow_artifacts']:
        if row['kind'] == 'input':
            inputs.setdefault(row['run_id'], []).append(row)
    for macro in stock['macro_tasks']:
        runs = by_macro.get(macro['macro_task_id'], [])
        if not runs:
            raise ValueError(f"Schema 34 → 35: macro {macro['macro_task_id']} has no retained member")
        first = min(runs, key=lambda row: (row['created_at'], row['rowid']))
        initial = min(inputs[first['run_id']], key=lambda row: row['rowid'])
        pair = (initial['workspace_id'], initial['input_manifest_sha256'])
        macro['project_cwd'] = json.loads(tasks[first['run_id']]['spec_json'])['cwd']
        macro['repository_path'] = owners[pair]


def _convert_mutable_intent(stock):
    run_ids = {row['run_id'] for row in stock['workflow_runs']}
    for task in stock['tasks']:
        if task['task_id'] not in run_ids:
            continue
        spec = json.loads(task['spec_json'])
        for field in ('exclusiveResources', 'requiredCapabilities'):
            spec.pop(field, None)
        if task['fingerprint_version'] != 2:
            raise ValueError(f"Schema 34 → 35: unsupported workflow fingerprint version for {task['task_id']}")
        task['spec_json'] = _json(spec)
        task['input_fingerprint'] = hashlib.sha256(("2:" + task['spec_json']).encode('utf-8')).hexdigest()
    for run in stock['workflow_runs']:
        intent = json.loads(run['execution_workspace_json'])
        integrator = intent['integrator']
        if integrator['role'] == 'host':
            intent['integrator'] = {'role': 'host'}
        run['execution_workspace_json'] = _json(intent)


def _project_facts(stock):
    """Freeze old Git facts from the retained database, without opening a cwd."""
    macros = {row['macro_task_id']: row for row in stock['macro_tasks']}
    runs = {row['run_id']: row for row in stock['workflow_runs']}
    projects = {}
    inputs = {}
    for artifact in stock['workflow_artifacts']:
        if artifact['kind'] == 'input':
            inputs.setdefault(artifact['run_id'], []).append(artifact)
    for macro in macros.values():
        macro['project_kind'] = 'git'
        projects[macro['project_id']] = {'project_id': macro['project_id'], 'kind': 'git',
                                         'path': _git_project_path(macro['repository_path'])}
    for run in runs.values():
        run['project_kind'] = 'git'
        run['checkout'] = json.loads(run['execution_workspace_json'])['kind']
        if run['macro_task_id'] is None:
            identifier = run['standalone_project_id']
            first = min(inputs[run['run_id']], key=lambda row: row['rowid'])
            if first['workspace_facts_role'] != 'owner':
                raise ValueError(f"Schema 34 → 35: first input of {run['run_id']} has no repository path")
            projects[identifier] = {'project_id': identifier, 'kind': 'git',
                                    'path': _git_project_path(first['repository_path'])}
    stock['projects'] = list(projects.values())


def _rebuild(connection, table, rows):
    if table != "projects":
        connection.execute(f'DROP TABLE "{table}"')
    connection.execute(TABLE_DDL[table])
    columns = ['rowid', *(row[1] for row in connection.execute(f'PRAGMA table_info("{table}")'))]
    insert = f'INSERT INTO "{table}"({",".join(columns)}) VALUES({",".join("?" for _ in columns)})'
    connection.executemany(insert, ([row.get(column) for column in columns] for row in rows))


def upgrade(connection):
    stock = {table: _rows(connection, table) for table in TABLE_DDL if table != 'projects'}
    sequences = list(connection.execute('SELECT rowid,name,seq FROM sqlite_sequence ORDER BY rowid'))
    _macro_paths(stock)
    _convert_mutable_intent(stock)
    _project_facts(stock)
    active_exclusive = connection.execute(
        "SELECT claim_id FROM resource_claims WHERE kind='exclusive' AND state!='released' LIMIT 1").fetchone()
    if active_exclusive is not None:
        raise ValueError('Schema 34 → 35: an exclusive resource claim is still active')
    stock['resource_claims'] = [row for row in stock['resource_claims'] if row['kind'] == 'cwd']
    for continuation in stock['workflow_continuations']:
        if continuation['kind'] == 'manual' and json.loads(continuation['helper_outcomes_json']) == []:
            continuation['helper_policy'] = None
        elif continuation['kind'] == 'helper' and not any(
                child['parent_run_id'] == continuation['run_id'] for child in stock['workflow_children']):
            continuation['helper_policy'] = None
    for table, rows in stock.items():
        _rebuild(connection, table, rows)
    for ddl in INDEX_DDL:
        connection.execute(ddl)
    connection.execute('DELETE FROM sqlite_sequence')
    connection.executemany('INSERT INTO sqlite_sequence(rowid,name,seq) VALUES(?,?,?)', sequences)
    connection.execute("DELETE FROM commands WHERE kind GLOB 'workflow.*' OR kind IN ('task.retry','task.acknowledge')")
    # Cleanup journals remain actionable after installation, including those
    # already applied. Retire only the Host-control facts copied by the old
    # cleanup and conclusion writers; keep their physical and stop evidence.
    connection.execute("UPDATE workspace_cleanup_plans SET evidence_json=json_remove(evidence_json,"
                       "'$.ownerGeneration','$.hostConclusion.ownerGeneration','$.hostConclusion.runRevision')"
                       " WHERE json_type(evidence_json,'$.ownerGeneration') IS NOT NULL"
                       " OR json_type(evidence_json,'$.hostConclusion.ownerGeneration') IS NOT NULL"
                       " OR json_type(evidence_json,'$.hostConclusion.runRevision') IS NOT NULL")
    connection.execute("UPDATE meta SET value='35' WHERE key='schema_version'")
