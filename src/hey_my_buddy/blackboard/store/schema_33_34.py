"""Schema 33 → 34: reference workspaces and distinguish sealed record forms.

All definitions and conversion rules belong to this step. The installer owns
one transaction for the chain; this step only reads existing workspace files.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import stat

ATTEMPTS_DDL = """
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
    result_json         TEXT,
    result_form         TEXT CHECK (result_form IN ('archived-report-v1','current-report-v2')),
    shutdown_confirmed  INTEGER NOT NULL DEFAULT 0,
    cancel_requested_at TEXT,
    stop_initiator      TEXT CHECK (stop_initiator IN ('host','lead','console','service','unknown')),
    grace_deadline      TEXT,
    force_reason        TEXT,
    force_requested_at  TEXT,
    identity_confirmed_at TEXT,
    cleanup_pending     INTEGER NOT NULL DEFAULT 0 CHECK (cleanup_pending IN (0,1)),
    started_at          TEXT,
    finished_at         TEXT,
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL,
    revision            INTEGER NOT NULL DEFAULT 1,
    CHECK ((result_json IS NULL AND result_form IS NULL)
        OR (result_json IS NOT NULL AND result_form IS NOT NULL)),
    UNIQUE(task_id, generation)
);
"""

WORKFLOW_RUNS_DDL = """
CREATE TABLE IF NOT EXISTS workflow_runs (
    run_id                  TEXT PRIMARY KEY REFERENCES tasks(task_id) ON DELETE RESTRICT,
    macro_task_id            TEXT REFERENCES macro_tasks(macro_task_id) ON DELETE RESTRICT,
    standalone_project_id    TEXT,
    title                   TEXT,
    activity_seq            INTEGER NOT NULL DEFAULT 0,
    activity_at             TEXT,
    host_id                 TEXT,
    source_host_id          TEXT,
    owner_generation        INTEGER NOT NULL DEFAULT 1,
    control_verifier        TEXT NOT NULL,
    request_fingerprint     TEXT,
    buddy_json TEXT,
    buddy_revision INTEGER NOT NULL DEFAULT 0,
    validated_buddy_revision INTEGER,
    current_routing_id      TEXT,
    submission_verifier     TEXT,
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
    revision                INTEGER NOT NULL DEFAULT 1,
    created_at              TEXT NOT NULL,
    updated_at              TEXT NOT NULL,
    buddy_locked    INTEGER NOT NULL DEFAULT 0 CHECK (buddy_locked IN (0,1)),
    delegation_authority INTEGER NOT NULL DEFAULT 0 CHECK (delegation_authority IN (0,1)),
    CHECK ((macro_task_id IS NULL AND standalone_project_id IS NOT NULL)
        OR (macro_task_id IS NOT NULL AND standalone_project_id IS NULL)),
    CHECK ((state IN ('accepted','concluded') AND workspace_id IS NULL AND workspace_manifest_sha256 IS NULL)
        OR (state NOT IN ('accepted','concluded') AND workspace_id IS NOT NULL AND workspace_manifest_sha256 IS NOT NULL))
);
"""

WORKFLOW_CHILDREN_DDL = """
CREATE TABLE IF NOT EXISTS workflow_children (
    child_task_id       TEXT PRIMARY KEY REFERENCES tasks(task_id) ON DELETE RESTRICT,
    parent_run_id       TEXT NOT NULL REFERENCES workflow_runs(run_id) ON DELETE RESTRICT,
    request_id          TEXT NOT NULL,
    decision_command_id TEXT NOT NULL,
    role                TEXT NOT NULL DEFAULT 'helper',
    state               TEXT NOT NULL CHECK (state IN ('active','succeeded','failed','cancelled','attention')),
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL
);
"""

WORKFLOW_CONTINUATIONS_DDL = """
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
    workspace_id      TEXT,
    workspace_manifest_sha256 TEXT,
    workspace_facts_role TEXT NOT NULL CHECK (workspace_facts_role IN ('pending','owner','reference')),
    workspace_kind    TEXT CHECK (workspace_kind IN ('existing','worktree')),
    checkout_root     TEXT,
    repository_path   TEXT,
    state             TEXT NOT NULL CHECK (state IN ('recorded','queued','consumed','cancelled','invalidated')),
    attempt_id        TEXT,
    turn_id           TEXT,
    created_at        TEXT NOT NULL,
    consumed_at       TEXT,
    CHECK ((workspace_facts_role='pending' AND workspace_id IS NULL AND workspace_manifest_sha256 IS NULL
            AND workspace_kind IS NULL AND checkout_root IS NULL AND repository_path IS NULL)
        OR (workspace_facts_role='owner' AND workspace_id IS NOT NULL AND workspace_manifest_sha256 IS NOT NULL
            AND workspace_kind IS NOT NULL AND checkout_root IS NOT NULL AND repository_path IS NOT NULL)
        OR (workspace_facts_role='reference' AND workspace_id IS NOT NULL AND workspace_manifest_sha256 IS NOT NULL
            AND workspace_kind IS NULL AND checkout_root IS NULL AND repository_path IS NULL)),
    CHECK ((kind IN ('manual','helper') AND trigger_event_json IS NULL)
        OR (kind='lead-wakeup' AND trigger_event_json IS NOT NULL
            AND json_valid(trigger_event_json) AND json_type(trigger_event_json)='object'))
);
"""

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
    input_json          TEXT NOT NULL,
    workspace_id        TEXT NOT NULL,
    workspace_manifest_sha256 TEXT NOT NULL,
    -- Frozen access for this turn, independent of later run intent changes.
    access              TEXT NOT NULL CHECK (access IN ('read','write','full')),
    -- Only an explicit full turn carries the Host's one-line note.
    access_note         TEXT,
    expected_seconds    INTEGER NOT NULL CHECK (expected_seconds > 0),
    state               TEXT NOT NULL CHECK (state IN ('prepared','running','returned','failed','interrupted','rejected')),
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL,
    CHECK ((access='full') = (access_note IS NOT NULL)),
    UNIQUE(run_id, turn_index)
);
"""

WORKFLOW_ARTIFACTS_DDL = """
CREATE TABLE IF NOT EXISTS workflow_artifacts (
    artifact_id     TEXT PRIMARY KEY,
    run_id          TEXT NOT NULL REFERENCES workflow_runs(run_id) ON DELETE RESTRICT,
    turn_id         TEXT,
    attempt_id      TEXT,
    kind            TEXT NOT NULL CHECK (kind IN ('input','turn-input','output','resolved-output','partial-output','abandoned-site','shared-refs')),
    content_form    TEXT NOT NULL CHECK (content_form IN ('sealed-inline-v1','input-reference','record-reference')),
    manifest_json   TEXT,
    workspace_id    TEXT,
    input_manifest_sha256 TEXT,
    output_snapshot_sha256 TEXT,
    shared_refs_sha256 TEXT,
    record_path     TEXT,
    record_sha256   TEXT,
    workspace_facts_role TEXT CHECK (workspace_facts_role IN ('owner','reference')),
    workspace_kind  TEXT CHECK (workspace_kind IN ('existing','worktree')),
    checkout_root   TEXT,
    repository_path TEXT,
    output_origin   TEXT CHECK (output_origin IN ('worker','host')),
    created_at      TEXT NOT NULL,
    CHECK ((kind IN ('input','turn-input') AND workspace_id IS NOT NULL AND input_manifest_sha256 IS NOT NULL
            AND output_snapshot_sha256 IS NULL AND shared_refs_sha256 IS NULL)
        OR (kind IN ('output','resolved-output','partial-output','abandoned-site') AND workspace_id IS NULL
            AND input_manifest_sha256 IS NULL AND output_snapshot_sha256 IS NOT NULL AND shared_refs_sha256 IS NULL)
        OR (kind='shared-refs' AND workspace_id IS NULL AND input_manifest_sha256 IS NULL
            AND output_snapshot_sha256 IS NULL AND shared_refs_sha256 IS NOT NULL)),
    CHECK ((content_form='sealed-inline-v1' AND manifest_json IS NOT NULL AND record_path IS NULL AND record_sha256 IS NULL)
        OR (content_form='input-reference' AND kind='input' AND manifest_json IS NULL
            AND record_path IS NULL AND record_sha256 IS NULL)
        OR (content_form='record-reference' AND kind IN ('output','resolved-output','partial-output','abandoned-site')
            AND manifest_json IS NULL AND record_path IS NOT NULL AND record_sha256 IS NOT NULL)),
    CHECK ((kind='input' AND workspace_facts_role='owner' AND workspace_kind IS NOT NULL
            AND checkout_root IS NOT NULL AND repository_path IS NOT NULL)
        OR (kind='input' AND workspace_facts_role='reference' AND workspace_kind IS NULL
            AND checkout_root IS NULL AND repository_path IS NULL)
        OR (kind!='input' AND workspace_facts_role IS NULL AND workspace_kind IS NULL
            AND checkout_root IS NULL AND repository_path IS NULL)),
    CHECK (kind!='input' OR workspace_facts_role IS NOT NULL),
    CHECK ((kind IN ('output','partial-output') AND output_origin IS NOT NULL)
        OR (kind NOT IN ('output','partial-output') AND output_origin IS NULL))
);
"""

INDEXES = (
    """CREATE UNIQUE INDEX IF NOT EXISTS attempts_effective_unique ON attempts(task_id)
    WHERE execution_state IN ('starting','executing','finalizing','uncertain');""",
    """CREATE INDEX IF NOT EXISTS attempts_active_idx ON attempts(execution_state);""",
    """CREATE INDEX IF NOT EXISTS attempts_model_service_idx
    ON attempts(model_harness, model_provider, model_model) WHERE model_model IS NOT NULL;""",
    """CREATE INDEX IF NOT EXISTS workflow_runs_state_idx ON workflow_runs(state, updated_at);""",
    """CREATE INDEX IF NOT EXISTS workflow_runs_macro_task_idx ON workflow_runs(macro_task_id, activity_seq DESC, run_id DESC);""",
    """CREATE INDEX IF NOT EXISTS workflow_runs_activity_idx ON workflow_runs(activity_seq);""",
    """CREATE INDEX IF NOT EXISTS workflow_turns_attempt_idx ON workflow_turns(attempt_id);""",
    """CREATE INDEX IF NOT EXISTS workflow_children_parent_idx ON workflow_children(parent_run_id, state);""",
    """CREATE INDEX IF NOT EXISTS workflow_continuations_run_idx ON workflow_continuations(run_id, state);""",
    """CREATE UNIQUE INDEX IF NOT EXISTS workflow_continuations_workspace_owner_unique
    ON workflow_continuations(workspace_id, workspace_manifest_sha256) WHERE workspace_facts_role='owner';""",
    """CREATE UNIQUE INDEX IF NOT EXISTS workflow_continuations_auto_unique
    ON workflow_continuations(run_id, request_id) WHERE kind='helper';""",
    """CREATE UNIQUE INDEX IF NOT EXISTS workflow_continuations_lead_pending_unique
    ON workflow_continuations(run_id) WHERE kind='lead-wakeup' AND state IN ('recorded','queued');""",
    """CREATE INDEX IF NOT EXISTS workflow_artifacts_run_idx ON workflow_artifacts(run_id, created_at);""",
    """CREATE UNIQUE INDEX IF NOT EXISTS workflow_artifacts_input_unique
    ON workflow_artifacts(run_id, attempt_id, input_manifest_sha256) WHERE input_manifest_sha256 IS NOT NULL;""",
    """CREATE UNIQUE INDEX IF NOT EXISTS workflow_artifacts_output_unique
    ON workflow_artifacts(run_id, attempt_id, output_snapshot_sha256) WHERE output_snapshot_sha256 IS NOT NULL;""",
    """CREATE UNIQUE INDEX IF NOT EXISTS workflow_artifacts_shared_refs_unique
    ON workflow_artifacts(run_id, attempt_id, shared_refs_sha256) WHERE shared_refs_sha256 IS NOT NULL;""",
    """CREATE UNIQUE INDEX IF NOT EXISTS workflow_artifacts_workspace_owner_unique
    ON workflow_artifacts(workspace_id, input_manifest_sha256) WHERE workspace_facts_role='owner';""",
    """CREATE UNIQUE INDEX IF NOT EXISTS attempts_router_binding_unique ON attempts(attempt_id, task_id, generation);""",
    """CREATE UNIQUE INDEX IF NOT EXISTS attempts_router_outcome_binding_unique ON attempts(attempt_id, task_id);""",
)

TERMINAL_RUNS = frozenset({'accepted', 'concluded'})
INPUT_KINDS = frozenset({'input', 'turn-input'})
OUTPUT_KINDS = frozenset({'output', 'resolved-output', 'partial-output', 'abandoned-site'})
WORKSPACE_ID = re.compile(r'ws-[0-9a-f]{32}\Z')
SHA256 = re.compile(r'[0-9a-f]{64}\Z')
REPARSE_POINT = 0x400


def _fail(run_id, pair, category):
    raise ValueError(f'Schema 33 → 34: run_id={run_id}, workspace_id={pair[0]}, '
                     f'expected_sha256={pair[1]}: {category}')


def _manifest(value, run_id):
    """Read the old explicit manifest forms without rewriting their contents."""
    pair = (value.get('workspaceId'), value.get('manifestSha256'))
    if (not isinstance(pair[0], str) or WORKSPACE_ID.fullmatch(pair[0]) is None
            or not isinstance(pair[1], str) or SHA256.fullmatch(pair[1]) is None):
        _fail(run_id, pair, 'manifest-binding-invalid')
    if type(value.get('version')) is not int or value['version'] not in (1, 2, 3, 4):
        _fail(run_id, pair, 'manifest-version-invalid')
    project = value['projectId'] if value['version'] == 4 else value['repositoryId']
    facts = (value['kind'], value['checkoutRoot'], value['snapshot']['repositoryPath'], project)
    if (facts[0] not in ('existing', 'worktree')
            or any(not isinstance(item, str) or not item for item in facts[1:])):
        _fail(run_id, pair, 'workspace-facts-invalid')
    return pair, facts


def _native(path):
    """Use ordinary private paths, including Windows' long-path spelling."""
    text = str(path)
    if os.name == 'nt' and not text.startswith('\\\\?\\'):
        text = '\\\\?\\UNC\\' + text[2:] if text.startswith('\\\\') else '\\\\?\\' + text
    return Path(text)


def _present(path):
    try:
        _native(path).lstat()
        return True
    except FileNotFoundError:
        return False


def _linked(facts):
    return stat.S_ISLNK(facts.st_mode) or bool(getattr(facts, 'st_file_attributes', 0) & REPARSE_POINT)


def _verify_file(state, pair, expected_facts, run_id):
    """One install-boundary read of a referenced, ordinary private manifest."""
    directory = state / 'workspaces' / pair[0]
    path = directory / 'manifest.json'
    try:
        for parent in (state, state / 'workspaces', directory):
            facts = _native(parent).lstat()
            if _linked(facts) or not stat.S_ISDIR(facts.st_mode):
                _fail(run_id, pair, 'manifest-unsafe')
        before = _native(path).lstat()
        if (_linked(before) or not stat.S_ISREG(before.st_mode)
                or (os.name != 'nt' and (before.st_uid != os.getuid() or before.st_mode & 0o077))):
            _fail(run_id, pair, 'manifest-unsafe')
        flags = os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0)
        fd = os.open(_native(path), flags)
        with os.fdopen(fd, 'rb') as stream:
            opened = os.fstat(stream.fileno())
            if (not stat.S_ISREG(opened.st_mode)
                    or (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino)):
                _fail(run_id, pair, 'manifest-unsafe')
            raw = stream.read()
    except FileNotFoundError:
        _fail(run_id, pair, 'manifest-missing')
    except OSError as error:
        _fail(run_id, pair, f'manifest-io-error ({error})')
    try:
        value = json.loads(raw)
        if not isinstance(value, dict):
            _fail(run_id, pair, 'manifest-invalid')
        if (value.get('workspaceId'), value.get('manifestSha256')) != pair:
            _fail(run_id, pair, 'manifest-binding-mismatch')
        _actual_pair, actual_facts = _manifest(value, run_id)
        digest = hashlib.sha256(json.dumps(
            {key: item for key, item in value.items() if key != 'manifestSha256'},
            sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode()).hexdigest()
    except (KeyError, TypeError, json.JSONDecodeError, UnicodeDecodeError) as error:
        _fail(run_id, pair, f'manifest-invalid ({error})')
    if actual_facts != expected_facts:
        _fail(run_id, pair, 'manifest-binding-mismatch')
    if digest != pair[1]:
        _fail(run_id, pair, 'manifest-digest-mismatch')


def _rows(connection, table):
    return [dict(row) for row in connection.execute(f'SELECT rowid,* FROM {table} ORDER BY rowid')]


def _inventory(connection, stock):
    """Assign SQL facts once; every other holder keeps only its frozen pair."""
    macros = dict(connection.execute('SELECT macro_task_id,project_id FROM macro_tasks'))
    manifests, facts, holders = {}, {}, {}

    def register(value, run_id):
        pair, metadata = _manifest(value, run_id)
        if pair in facts and facts[pair] != metadata:
            _fail(run_id, pair, 'workspace-facts-conflict')
        facts[pair] = metadata
        manifests[pair] = value
        holders.setdefault(run_id, set()).add(pair)
        return pair

    owners = {}
    # Initial input rows precede continuation candidates. A pair shared by
    # several inputs still has only one owner, with the others as references.
    for row in stock['workflow_artifacts']:
        value = json.loads(row['manifest_json'])
        kind = row['kind']
        if kind in INPUT_KINDS:
            pair = register(value, row['run_id'])
            if row['manifest_sha256'] != pair[1]:
                _fail(row['run_id'], pair, 'artifact-input-binding-mismatch')
            row.update(workspace_id=pair[0], input_manifest_sha256=pair[1])
            if kind == 'input':
                if pair not in owners:
                    owners[pair] = row
                    row.update(workspace_facts_role='owner', workspace_kind=facts[pair][0],
                               checkout_root=facts[pair][1], repository_path=facts[pair][2])
                else:
                    row['workspace_facts_role'] = 'reference'
        elif kind in OUTPUT_KINDS:
            if row['manifest_sha256'] != value['snapshotSha256']:
                _fail(row['run_id'], (None, row['manifest_sha256']), 'artifact-output-binding-mismatch')
            row['output_snapshot_sha256'] = row['manifest_sha256']
            if kind in ('output', 'partial-output'):
                origin = value.get('kind')
                if origin not in (None, 'host-output'):
                    _fail(row['run_id'], (None, row['manifest_sha256']), 'output-origin-invalid')
                row['output_origin'] = 'host' if origin == 'host-output' else 'worker'
        elif kind == 'shared-refs':
            if row['manifest_sha256'] != value['manifestSha256']:
                _fail(row['run_id'], (None, row['manifest_sha256']), 'artifact-shared-refs-binding-mismatch')
            row['shared_refs_sha256'] = row['manifest_sha256']
        else:
            _fail(row['run_id'], (None, row['manifest_sha256']), 'artifact-kind-invalid')
        row['content_form'] = 'sealed-inline-v1'

    for row in stock['workflow_continuations']:
        if row['workspace_manifest_json'] is None:
            row['workspace_facts_role'] = 'pending'
            continue
        pair = register(json.loads(row['workspace_manifest_json']), row['run_id'])
        row.update(workspace_id=pair[0], workspace_manifest_sha256=pair[1])
        if pair not in owners:
            owners[pair] = row
            row.update(workspace_facts_role='owner', workspace_kind=facts[pair][0],
                       checkout_root=facts[pair][1], repository_path=facts[pair][2])
        else:
            row['workspace_facts_role'] = 'reference'

    for row in stock['workflow_turns']:
        pair = register(json.loads(row['input_json'])['executionWorkspace'], row['run_id'])
        row.update(workspace_id=pair[0], workspace_manifest_sha256=pair[1])
    for row in stock['workflow_runs']:
        pair = register(json.loads(row['workspace_manifest_json']), row['run_id'])
        initial = next((item for item in stock['workflow_artifacts']
                        if item['run_id'] == row['run_id'] and item['kind'] == 'input'), None)
        if initial is None:
            _fail(row['run_id'], pair, 'initial-input-missing')
        project = facts[(initial['workspace_id'], initial['input_manifest_sha256'])][3]
        if row['macro_task_id'] is None:
            row['standalone_project_id'] = project
        elif macros[row['macro_task_id']] != project:
            _fail(row['run_id'], pair, 'macro-project-conflict')
        if any(facts[bound][3] != project for bound in holders[row['run_id']]):
            _fail(row['run_id'], pair, 'run-project-conflict')
        row.update(workspace_id=None if row['state'] in TERMINAL_RUNS else pair[0],
                   workspace_manifest_sha256=None if row['state'] in TERMINAL_RUNS else pair[1])
    for row in stock['workflow_children']:
        if row['workspace_manifest_json'] is not None:
            value = json.loads(row['workspace_manifest_json'])
            pair, metadata = _manifest(value, row['child_task_id'])
            if pair not in holders.get(row['child_task_id'], ()) or facts[pair] != metadata:
                _fail(row['child_task_id'], pair, 'child-input-binding-missing')
    for row in connection.execute('SELECT holder_task_id,workspace_id,manifest_sha256 FROM workspace_reservations'):
        pair = (row['workspace_id'], row['manifest_sha256'])
        if pair not in owners:
            _fail(row['holder_task_id'], pair, 'reservation-owner-missing')
    for run_id, pairs in holders.items():
        for pair in pairs:
            if pair not in owners:
                _fail(run_id, pair, 'workspace-owner-missing')
    return manifests, facts


def _file_boundary(connection, stock, manifests, facts):
    filename = next(row[2] for row in connection.execute('PRAGMA database_list') if row[1] == 'main')
    state = Path(filename).parent if filename else Path('.')
    checked = set()

    def verify(run_id, pair):
        if pair not in checked:
            _verify_file(state, pair, facts[pair], run_id)
            checked.add(pair)

    def allocation(run_id, pair):
        """The physical worktree's original manifest, including borrowed inputs."""
        value = manifests[pair]
        root = Path(value['checkoutRoot'])
        if root.parent.parent != state / 'workspaces' or root.name != 'checkout':
            return None
        candidates = [(key, candidate) for key, candidate in manifests.items()
                      if candidate['kind'] == 'worktree' and key[0] == root.parent.name
                      and candidate['checkoutRoot'] == str(root) and facts[key][3] == facts[pair][3]]
        if len(candidates) != 1:
            _fail(run_id, pair, 'allocation-binding-missing')
        return candidates[0]

    for run in stock['workflow_runs']:
        run_id = run['run_id']
        if run['state'] not in TERMINAL_RUNS:
            pair = (run['workspace_id'], run['workspace_manifest_sha256'])
            verify(run_id, pair)
            physical = allocation(run_id, pair)
            if physical is not None:
                verify(run_id, physical[0])
            continue
        # Terminal history comes from the last frozen turn, or the initial
        # input for a run that never began a turn, never from the cleared slot.
        turns = [row for row in stock['workflow_turns'] if row['run_id'] == run_id]
        if turns:
            last = max(turns, key=lambda row: row['turn_index'])
            pair = (last['workspace_id'], last['workspace_manifest_sha256'])
        else:
            initial = next(row for row in stock['workflow_artifacts']
                           if row['run_id'] == run_id and row['kind'] == 'input')
            pair = (initial['workspace_id'], initial['input_manifest_sha256'])
        value = manifests[pair]
        physical = allocation(run_id, pair)
        plans = list(connection.execute(
            "SELECT * FROM workspace_cleanup_plans WHERE run_id=? AND state IN ('planned','blocked','applying')",
            (run_id,)))
        if physical is not None and _present(Path(physical[1]['checkoutRoot'])):
            verify(run_id, physical[0])
        for plan in plans:
            allocation_pair = (plan['workspace_id'], None)
            proof = json.loads(plan['evidence_json'])['workspace']['allocation']
            if physical is None:
                # A blocked existing-checkout plan has no disposable allocation;
                # it still needs the logical input used by its next inspection.
                if (proof is not None or plan['kind'] != value['kind']
                        or plan['workspace_id'] != pair[0] or plan['path'] != value['checkoutRoot']
                        or plan['state'] != 'blocked'):
                    _fail(run_id, pair, 'cleanup-allocation-binding-missing')
                verify(run_id, pair)
                continue
            allocation_pair, original = physical
            expected = {'workspaceId': allocation_pair[0], 'path': original['checkoutRoot'], 'kind': 'worktree',
                        'projectId': facts[allocation_pair][3], 'manifestSha256': allocation_pair[1],
                        'manifestWorkspaceId': pair[0]}
            if plan['workspace_id'] != allocation_pair[0] or plan['path'] != original['checkoutRoot'] or proof != expected:
                _fail(run_id, allocation_pair, 'cleanup-proof-mismatch')
            directory = state / 'workspaces' / allocation_pair[0]
            if plan['state'] == 'applying' and not _present(directory):
                # Frozen missing_allocation proof authorizes only later removal
                # of the original Git registration; this upgrade does no work.
                if json.loads(plan['reasons_json']):
                    _fail(run_id, allocation_pair, 'cleanup-proof-mismatch')
                continue
            verify(run_id, allocation_pair)


def _rebuild(connection, table, ddl, rows):
    connection.execute(f'DROP TABLE {table}')
    connection.execute(ddl)
    columns = ['rowid', *(row[1] for row in connection.execute(f'PRAGMA table_info({table})'))]
    insert = f'INSERT INTO {table}({",".join(columns)}) VALUES({",".join("?" for _ in columns)})'
    connection.executemany(insert, ([row.get(column) for column in columns] for row in rows))


def upgrade(connection):
    stock = {table: _rows(connection, table) for table in (
        'attempts', 'workflow_runs', 'workflow_children', 'workflow_continuations', 'workflow_turns', 'workflow_artifacts')}
    manifests, facts = _inventory(connection, stock)
    _file_boundary(connection, stock, manifests, facts)
    for row in stock['attempts']:
        row['result_form'] = 'archived-report-v1' if row['result_json'] is not None else None
    for table, ddl in (
        ('attempts', ATTEMPTS_DDL), ('workflow_runs', WORKFLOW_RUNS_DDL),
        ('workflow_children', WORKFLOW_CHILDREN_DDL), ('workflow_continuations', WORKFLOW_CONTINUATIONS_DDL),
        ('workflow_turns', WORKFLOW_TURNS_DDL), ('workflow_artifacts', WORKFLOW_ARTIFACTS_DDL),
    ):
        _rebuild(connection, table, ddl, stock[table])
    for statement in INDEXES:
        connection.execute(statement)
    connection.execute("UPDATE meta SET value='34' WHERE key='schema_version'")
