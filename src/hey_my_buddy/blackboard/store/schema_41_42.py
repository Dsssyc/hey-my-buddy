"""Schema 41 → 42: retire helpers and freeze attempt session ownership and waiting facts."""
from __future__ import annotations

from collections import defaultdict
import json
import os
import sqlite3


TASKS_DDL = """
CREATE TABLE "tasks" (
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
            AND json_extract(spec_json,'$.expectedSeconds') > 0)),
    -- A running task's reason lives on its active attempt row; the task's own
    -- reason is the queued writer's fact and never coexists with an attempt.
    CHECK (active_attempt_id IS NULL OR queue_reason IS NULL),
    CHECK (queue_reason IS NULL OR queue_reason NOT IN ('attempt-uncertain-after-restart','execution-connection-unknown','harness-probe-unknown','router-input-unknown'))
);
"""

TASKS_INDEXES = """
CREATE INDEX IF NOT EXISTS tasks_state_idx ON tasks(state, created_at);
"""


ATTEMPTS_DDL = """
CREATE TABLE IF NOT EXISTS attempts (
    attempt_id          TEXT PRIMARY KEY,
    task_id             TEXT NOT NULL REFERENCES tasks(task_id) ON DELETE RESTRICT,
    generation          INTEGER NOT NULL,
    nonce_verifier      TEXT NOT NULL,
    execution_state     TEXT NOT NULL
        CHECK (execution_state IN ('starting','executing','finalizing','uncertain','finished')),
    ownership           TEXT NOT NULL DEFAULT 'owned' CHECK (ownership IN ('owned','uncertain')),
    -- The attempt's own waiting facts, each owned by exactly one writer: the
    -- connection reason while the execution's liveness is unresolved, an unknown
    -- harness probe observed before or without a reconnect, and the Router's
    -- control wait. A restart-marked uncertain attempt and a live-session
    -- disconnect are distinguished by the reason itself.
    connection_reason TEXT CHECK (connection_reason IN ('attempt-uncertain-after-restart','execution-connection-unknown')),
    probe_unknown     INTEGER NOT NULL DEFAULT 0 CHECK (probe_unknown IN (0,1)),
    waiting_reason    TEXT CHECK (waiting_reason IN ('router-input-unknown')),
    -- The monotone barrier of the execution's waiting hints: every hint carries
    -- a per-attempt sequence, and only a higher one rewrites waiting_reason,
    -- so a late replay of an older dispatch cannot revive a cleared wait.
    waiting_hint_seq  INTEGER NOT NULL DEFAULT 0 CHECK (waiting_hint_seq >= 0),
    -- Frozen at admission for the attempt's own recorded session file.
    session_group_id   TEXT,
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
    model_id            TEXT REFERENCES models(model_id) ON DELETE RESTRICT,
    result_json         TEXT,
    result_form         TEXT CHECK (result_form IN ('archived-report-v1','current-report-v2')),
    shutdown_confirmed  INTEGER NOT NULL DEFAULT 0,
    cancel_requested_at TEXT,
    stop_initiator      TEXT CHECK (stop_initiator IN ('host','lead','console','service','unknown')),
    stop_purpose        TEXT CHECK (stop_purpose IN ('pause','end')),
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
    -- The connection reason names why a still-running attempt is uncertain: it
    -- is bound to execution_state, not ownership, because a finished attempt may
    -- keep its historical uncertain ownership, and a committed result ends every
    -- live connection question without proving a stop.
    CHECK ((execution_state='uncertain' AND result_json IS NULL AND connection_reason IS NOT NULL)
        OR ((execution_state!='uncertain' OR result_json IS NOT NULL) AND connection_reason IS NULL)),
    -- Receiving a receipt settles every wait: no probe flag or Router wait may
    -- survive into finalizing, finished or a committed result.
    CHECK ((execution_state NOT IN ('finalizing','finished') AND result_json IS NULL)
        OR (probe_unknown = 0 AND waiting_reason IS NULL)),
    UNIQUE(task_id, generation)
);
"""

ATTEMPTS_INDEXES = """
CREATE UNIQUE INDEX IF NOT EXISTS attempts_effective_unique ON attempts(task_id)
    WHERE execution_state IN ('starting','executing','finalizing','uncertain');
CREATE INDEX IF NOT EXISTS attempts_active_idx ON attempts(execution_state);
CREATE INDEX IF NOT EXISTS attempts_model_service_idx
    ON attempts(model_harness, model_provider, model_model) WHERE model_model IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS attempts_router_binding_unique ON attempts(attempt_id, task_id, generation);
CREATE UNIQUE INDEX IF NOT EXISTS attempts_router_outcome_binding_unique ON attempts(attempt_id, task_id);
"""


WORKFLOW_RUNS_DDL = """
CREATE TABLE IF NOT EXISTS workflow_runs (
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
        CHECK (state IN ('executing','awaiting-host','waiting-owned','delivered','accepted','concluded','cancelled','failed')),
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
);
"""


WORKFLOW_RUNS_INDEXES = """
CREATE INDEX IF NOT EXISTS workflow_runs_state_idx ON workflow_runs(state, updated_at);
CREATE INDEX IF NOT EXISTS workflow_runs_macro_task_idx ON workflow_runs(macro_task_id, activity_seq DESC, run_id DESC);
CREATE INDEX IF NOT EXISTS workflow_runs_activity_idx ON workflow_runs(activity_seq);
CREATE UNIQUE INDEX IF NOT EXISTS workflow_runs_creation_key_unique ON workflow_runs(creation_key) WHERE creation_key IS NOT NULL;
"""


WORKFLOW_REQUESTS_DDL = """
CREATE TABLE IF NOT EXISTS workflow_requests (
    request_id          TEXT PRIMARY KEY,
    run_id              TEXT NOT NULL REFERENCES workflow_runs(run_id) ON DELETE RESTRICT,
    turn_id             TEXT,
    attempt_id          TEXT,
    summary             TEXT,
    reason_code         TEXT,
    reason_params_json  TEXT,
    decision_id         TEXT REFERENCES evaluation_decisions(decision_id) ON DELETE RESTRICT,
    payload_json        TEXT NOT NULL,
    state               TEXT NOT NULL CHECK (state IN ('open','resolved','superseded','cancelled')),
    resolution_json     TEXT,
    response_kind       TEXT CHECK (response_kind IN ('continue','resolution')),
    response_id         TEXT,
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL,
    decided_at          TEXT,
    CHECK ((response_kind IS NULL) = (response_id IS NULL)),
    CHECK (CASE
        WHEN resolution_json IS NULL THEN
            (state IN ('open','superseded','cancelled') AND response_kind IS NULL)
            OR (state='superseded' AND response_kind IS 'continue')
        WHEN json_valid(resolution_json) AND json_type(resolution_json) IS 'object' AND state='resolved' THEN
            CASE WHEN response_kind IS NULL THEN
                json_type(resolution_json,'$.legacy') IS 'true'
                AND json_type(resolution_json,'$.resolution') IS 'object'
                AND json_type(resolution_json,'$.resolution.conflictId') IS 'text'
                AND length(json_extract(resolution_json,'$.resolution.conflictId')) > 0
                AND json_extract(resolution_json,'$.resolution.action') IN ('restore','adopt','abandon')
                AND json_extract(resolution_json,'$.resolution.state') IN ('open','restored','adopted','abandoned')
                AND json_type(resolution_json,'$.resolution.actor') IS 'object'
                AND json_type(resolution_json,'$.parameters') IS NULL
                AND json_type(resolution_json,'$.result') IS NULL
            ELSE
                json_type(resolution_json,'$.legacy') IS NULL
                AND json_type(resolution_json,'$.resolution') IS NULL
                AND json_type(resolution_json,'$.result') IS 'object'
                AND json_type(resolution_json,'$.result.conflictId') IS 'text'
                AND length(json_extract(resolution_json,'$.result.conflictId')) > 0
                AND json_type(resolution_json,'$.result.state') IS 'text'
                AND json_extract(resolution_json,'$.result.state') IN ('open','restored','adopted','abandoned')
                AND json_type(resolution_json,'$.result.resolvedFingerprint') IS 'text'
                AND length(json_extract(resolution_json,'$.result.resolvedFingerprint')) > 0
                AND (json_type(resolution_json,'$.result.artifactId') IS 'null'
                    OR (json_type(resolution_json,'$.result.artifactId') IS 'text'
                        AND length(json_extract(resolution_json,'$.result.artifactId')) > 0))
                AND CASE
                    WHEN response_kind='resolution' THEN
                        response_id=json_extract(resolution_json,'$.result.conflictId')
                        AND json_type(resolution_json,'$.parameters') IS 'object'
                        AND json_type(resolution_json,'$.parameters.resolution') IS 'text'
                        AND json_extract(resolution_json,'$.parameters.resolution') IN ('restore','adopt','abandon')
                        AND json_type(resolution_json,'$.parameters.observedFingerprint') IS 'text'
                        AND length(json_extract(resolution_json,'$.parameters.observedFingerprint')) > 0
                    WHEN response_kind='continue' THEN
                        json_type(resolution_json,'$.parameters') IS NULL
                    ELSE 0 END
            END
        ELSE 0 END),
    CHECK ((summary IS NOT NULL AND reason_code IS NULL AND reason_params_json IS NULL AND decision_id IS NULL)
        OR (summary IS NULL AND reason_code IS NOT NULL AND reason_params_json IS NOT NULL
            AND json_valid(reason_params_json) AND json_type(reason_params_json)='object')
        OR (summary IS NULL AND reason_code IS NULL AND reason_params_json IS NULL AND decision_id IS NOT NULL))
);
"""


WORKFLOW_REQUESTS_INDEXES = """
CREATE INDEX IF NOT EXISTS workflow_requests_run_idx ON workflow_requests(run_id, state);
"""


WORKFLOW_CONTINUATIONS_DDL = """
CREATE TABLE IF NOT EXISTS workflow_continuations (
    continuation_id   TEXT PRIMARY KEY,
    run_id            TEXT NOT NULL REFERENCES workflow_runs(run_id) ON DELETE RESTRICT,
    kind              TEXT NOT NULL CHECK (kind IN ('manual','lead-wakeup')),
    request_id        TEXT,
    artifact_id       TEXT REFERENCES workflow_artifacts(artifact_id) ON DELETE RESTRICT,
    response_turn_id  TEXT REFERENCES workflow_turns(turn_id) ON DELETE RESTRICT,
    buddy_selection_json TEXT,
    access_selection_json TEXT,
    resolution_json   TEXT,
    trigger_event_json TEXT,
    input_text        TEXT NOT NULL,
    note              TEXT,
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
    CHECK ((kind='manual' AND trigger_event_json IS NULL)
        OR (kind='lead-wakeup' AND trigger_event_json IS NOT NULL
            AND json_valid(trigger_event_json) AND json_type(trigger_event_json)='object'))
);
"""


WORKFLOW_CONTINUATIONS_INDEXES = """
CREATE INDEX IF NOT EXISTS workflow_continuations_run_idx ON workflow_continuations(run_id, state);
CREATE UNIQUE INDEX IF NOT EXISTS workflow_continuations_workspace_owner_unique
    ON workflow_continuations(workspace_id, workspace_manifest_sha256) WHERE workspace_facts_role='owner';
CREATE UNIQUE INDEX IF NOT EXISTS workflow_continuations_lead_pending_unique
    ON workflow_continuations(run_id) WHERE kind='lead-wakeup' AND state IN ('recorded','queued');
"""


WORKSPACE_RESERVATIONS_DDL = """
CREATE TABLE IF NOT EXISTS workspace_reservations (
    reservation_id  TEXT PRIMARY KEY,
    workspace_id    TEXT NOT NULL,
    checkout_root   TEXT NOT NULL,
    access          TEXT NOT NULL CHECK (access IN ('read','write')),
    holder_task_id  TEXT NOT NULL REFERENCES tasks(task_id) ON DELETE RESTRICT,
    manifest_sha256 TEXT NOT NULL,
    state           TEXT NOT NULL CHECK (state IN ('held','released')),
    created_at      TEXT NOT NULL,
    released_at     TEXT
);
"""


WORKSPACE_RESERVATIONS_INDEXES = """
CREATE UNIQUE INDEX IF NOT EXISTS workspace_reservations_writer_unique
    ON workspace_reservations(checkout_root) WHERE state='held' AND access='write';
CREATE INDEX IF NOT EXISTS workspace_reservations_checkout_idx ON workspace_reservations(checkout_root, state);
CREATE INDEX IF NOT EXISTS workspace_reservations_holder_idx ON workspace_reservations(holder_task_id, state);
"""


# The same recursive ownership that Schema 41 used to place session records.
# Keep the old tree available until every historical attempt has its own binding.
OWNERSHIP_41 = """
parents(run_id, parent_run_id) AS (
    SELECT child_task_id, parent_run_id FROM workflow_children
    UNION SELECT owned_run_id, lead_run_id FROM workflow_lead_tasks
),
lineage(run_id, ancestor_id) AS (
    SELECT run_id, run_id FROM workflow_runs
    UNION
    SELECT lineage.run_id, parents.parent_run_id
      FROM lineage JOIN parents ON parents.run_id=lineage.ancestor_id
),
groups(run_id, group_id) AS (
    SELECT lineage.run_id, COALESCE(root.macro_task_id, 'run:' || root.run_id)
      FROM lineage JOIN workflow_runs root ON root.run_id=lineage.ancestor_id
     WHERE NOT EXISTS (SELECT 1 FROM parents WHERE parents.run_id=root.run_id)
),
helper_members(run_id) AS (
    SELECT parent_run_id FROM workflow_children
    UNION SELECT child_task_id FROM workflow_children
)
"""


# Every input/turn/continuation binding of a helper-tree member is checked,
# including references to a checkout whose owner is another run.
UNRECLAIMED_CHECKOUTS = """
, allocations(workspace_id, manifest_sha256, checkout_root, owner_run_id) AS (
    SELECT workspace_id, input_manifest_sha256, checkout_root, run_id FROM workflow_artifacts
     WHERE kind='input' AND workspace_facts_role='owner' AND workspace_kind='worktree'
    UNION
    SELECT workspace_id, workspace_manifest_sha256, checkout_root, run_id FROM workflow_continuations
     WHERE workspace_facts_role='owner' AND workspace_kind='worktree'
),
bindings(run_id, workspace_id, manifest_sha256) AS (
    SELECT run_id, workspace_id, input_manifest_sha256 FROM workflow_artifacts WHERE kind='input'
    UNION SELECT run_id, workspace_id, workspace_manifest_sha256 FROM workflow_turns
    UNION SELECT run_id, workspace_id, workspace_manifest_sha256 FROM workflow_continuations
    UNION SELECT run_id, workspace_id, workspace_manifest_sha256 FROM workflow_runs
)
SELECT DISTINCT b.run_id, a.workspace_id, a.checkout_root,
       b.run_id=a.owner_run_id AND EXISTS (
           SELECT 1 FROM workflow_runs child JOIN workflow_children h ON h.child_task_id=child.run_id
            WHERE child.run_id=b.run_id AND child.state='cancelled'
              AND EXISTS (SELECT 1 FROM workflow_workspace_conflicts c WHERE c.run_id=child.run_id AND c.state='open')
              AND NOT EXISTS (SELECT 1 FROM workflow_requests q WHERE q.run_id=child.run_id AND q.state='open')
       ) AS retained_cancelled_conflict
  FROM helper_members m JOIN bindings b ON b.run_id=m.run_id
  JOIN allocations a USING (workspace_id, manifest_sha256)
 WHERE NOT EXISTS (
    SELECT 1 FROM workspace_cleanup_plans p
     WHERE p.run_id=a.owner_run_id AND p.kind='worktree' AND p.state='applied'
       AND p.path=a.checkout_root
 )
ORDER BY b.run_id, a.workspace_id
"""


AUTO_CONTINUE_FAILURES = frozenset({
    'ANCESTOR_TERMINAL', 'CONFLICT', 'SHUTDOWN_UNCONFIRMED', 'REVISION_CONFLICT',
    'UPGRADE_IN_PROGRESS', 'ILLEGAL_TRANSITION',
})
PROXY_KINDS = frozenset({'helper-attention', 'helper-report'})
REPLAYABLE_STATES = frozenset({'recorded', 'queued'})


def _invalid(description, *identifiers):
    raise ValueError(f"Schema 41 → 42: {description} ({', '.join(map(str, identifiers))})")


def _object(encoded, field, identifier):
    try:
        value = json.loads(encoded)
    except (TypeError, ValueError):
        _invalid(f"invalid {field}", identifier)
    if not isinstance(value, dict):
        _invalid(f"{field} must be an object", identifier)
    return value


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'))


def _lead_failures(connection, requests, payloads):
    candidates = defaultdict(list)
    for row in connection.execute("""
        SELECT c.run_id, c.created_at, c.state, c.continuation_id
          FROM workflow_continuations c
          JOIN workflow_runs r ON r.run_id=c.run_id
          JOIN workflow_lead_tasks l ON l.lead_run_id=c.run_id
           AND l.owned_run_id=json_extract(c.trigger_event_json,'$.runId')
          JOIN events e ON e.seq=json_extract(c.trigger_event_json,'$.seq')
           AND e.kind=json_extract(c.trigger_event_json,'$.kind')
           -- A global service issue wakes a Lead without naming a task; every
           -- other trigger belongs to the owned run the doorbell named.
           AND (e.task_id=l.owned_run_id OR (e.kind='service.issue' AND e.task_id IS NULL))
         WHERE c.kind='lead-wakeup' AND r.delegation_authority=1
           AND c.state IN ('recorded','invalidated','cancelled')
    """):
        candidates[row['run_id'], row['created_at']].append(row)
    retained = {}
    for request_id, row in requests.items():
        if (row['kind'] != 'helper-report' or row['child_task_id'] is not None
                or row['reason_code'] not in AUTO_CONTINUE_FAILURES
                or payloads[request_id].get('helperOutcomes') != []):
            continue
        eligible = [
            wakeup for wakeup in candidates[row['run_id'], row['created_at']]
            if (wakeup['state'] == 'recorded') == (row['state'] == 'open')
        ]
        if not eligible:
            continue
        if len(eligible) != 1:
            _invalid('ambiguous Lead wakeup failure', request_id, row['run_id'],
                     *(wakeup['continuation_id'] for wakeup in eligible))
        _object(row['reason_params_json'], 'Lead wakeup failure reason', request_id)
        payloads[request_id].pop('helperOutcomes')
        retained[request_id] = eligible[0]['continuation_id']
    if len(set(retained.values())) != len(retained):
        _invalid('multiple requests claim a Lead wakeup failure', *retained)
    return retained


def _checkout_exists(root):
    # Schema 41 stores absolute roots. On Windows long roots need the same
    # extended-path form the writer used to inspect the checkout.
    if os.name == 'nt' and not root.startswith('\\\\?\\'):
        root = root.replace('/', '\\')
        root = '\\\\?\\UNC\\' + root[2:] if root.startswith('\\\\') else '\\\\?\\' + root
    try:
        os.lstat(root)
    except FileNotFoundError:
        return False
    return True


def _preflight(connection, requests, pure_proxies):
    checks = (
        ('waiting-helpers', "SELECT run_id FROM workflow_runs WHERE state='waiting-helpers'"),
        ('unfinished helper', """
            SELECT h.parent_run_id, h.child_task_id
              FROM workflow_children h LEFT JOIN workflow_runs r ON r.run_id=h.child_task_id
             WHERE h.state IN ('active','attention')
                OR r.state IN ('executing','awaiting-host','waiting-helpers','waiting-owned','delivered')
        """),
        ('held or transferred workspace reservation', f"""
            WITH RECURSIVE {OWNERSHIP_41}
            SELECT reservation_id, holder_task_id FROM workspace_reservations
             WHERE state='transferred'
                OR (state='held' AND holder_task_id IN (SELECT run_id FROM helper_members))
        """),
        ('unsettled helper under Lead or terminal ancestor', f"""
            WITH RECURSIVE {OWNERSHIP_41}
            SELECT DISTINCT h.parent_run_id, h.child_task_id, ancestor.run_id
              FROM workflow_children h
              LEFT JOIN workflow_runs child ON child.run_id=h.child_task_id
              JOIN lineage l ON l.run_id=h.parent_run_id
              JOIN workflow_runs ancestor ON ancestor.run_id=l.ancestor_id
             WHERE (COALESCE(child.state,'missing') NOT IN ('accepted','concluded')
                    AND (ancestor.delegation_authority=1 OR EXISTS (
                         SELECT 1 FROM workflow_lead_tasks owned WHERE owned.owned_run_id=ancestor.run_id)))
                OR (ancestor.state IN ('accepted','concluded')
                    AND (h.state IN ('failed','cancelled') OR child.state IN ('failed','cancelled')))
        """),
        ('helper tree pause', f"""
            WITH RECURSIVE {OWNERSHIP_41}
            SELECT DISTINCT COALESCE('run:'||p.run_id,p.macro_task_id), m.run_id
              FROM helper_members m JOIN lineage l USING (run_id)
              JOIN groups g USING (run_id)
              JOIN work_pauses p ON p.run_id=l.ancestor_id OR p.macro_task_id=g.group_id
        """),
        ('helper tree deletion', f"""
            WITH RECURSIVE {OWNERSHIP_41}
            SELECT DISTINCT d.deletion_id, d.target_id, m.run_id
              FROM helper_members m JOIN lineage l USING (run_id)
              JOIN macro_task_deletions d ON d.target_id='run:'||l.ancestor_id
             WHERE d.target_kind='micro-task-tree'
        """),
    )
    problems = []
    for name, query in checks:
        identifiers = ["/".join(row) for row in connection.execute(query)]
        if identifiers:
            problems.append(f"{name}: {', '.join(sorted(identifiers))}")

    # A cancelled former helper with no open request can retain an old conflict
    # and its input manifest after the actual checkout has gone. The ancestry
    # guards above separately fence closed parents and Lead trees.
    checkouts = [f'{run_id}/{workspace_id}' for run_id, workspace_id, root, historical in connection.execute(
        f'WITH RECURSIVE {OWNERSHIP_41} {UNRECLAIMED_CHECKOUTS}') if not historical or _checkout_exists(root)]
    if checkouts:
        problems.append(f"unreclaimed helper checkout: {', '.join(checkouts)}")

    open_proxies = sorted(f"{requests[key]['run_id']}/{key}" for key in pure_proxies
                          if requests[key]['state'] == 'open')
    if open_proxies:
        problems.append(f"open helper request: {', '.join(open_proxies)}")

    child_parents = {row[0] for row in connection.execute('SELECT DISTINCT parent_run_id FROM workflow_children')}
    dependencies = []
    for row in connection.execute("""
        SELECT continuation_id, run_id, controlling_run_id, helper_policy, helper_outcomes_json, request_id
          FROM workflow_continuations
         WHERE state IN ('recorded','queued') OR (state='cancelled' AND attempt_id IS NULL)
    """):
        try:
            outcomes = json.loads(row['helper_outcomes_json'])
        except ValueError:
            _invalid('invalid helper outcomes', row['run_id'], row['continuation_id'])
        if (row['helper_policy'] is not None
                or row['controlling_run_id'] not in (None, row['run_id'])
                or outcomes != [] or row['request_id'] in pure_proxies or row['run_id'] in child_parents):
            dependencies.append(f"{row['run_id']}/{row['continuation_id']}")
    if dependencies:
        problems.append(f"unconsumed helper continuation: {', '.join(sorted(dependencies))}")
    if problems:
        raise ValueError("Schema 41 → 42: resolve helper stock in the old release before upgrading. "
                         + '; '.join(problems))


def _source_request(request_id, requests, payloads, parents, pure_proxies, seen=frozenset()):
    """Resolve one continuation request to its living source.

    Returns ``(leaf, terminal_request_id)``: the request row a continuation
    rebinds to, or ``None`` when the chain ends in the retired pure
    ``helper-report``, together with the terminal request's own id so an
    ancestor proxy can check its ``origin`` against what the chain reached.
    """
    request = requests.get(request_id)
    if request is None or request_id in seen:
        _invalid('missing or cyclic continuation request', request_id)
    if request_id not in pure_proxies:
        return request, request_id
    payload = payloads[request_id]
    proxy = payload.get('proxy')
    origin = payload.get('origin')
    if origin is None and proxy is None and request['kind'] == 'helper-report':
        return None, request_id

    for reference in (proxy, origin):
        if reference is None:
            continue
        if not isinstance(reference, dict) or not isinstance(reference.get('requestId'), str):
            _invalid('invalid proxy reference', request_id)
        referred = requests.get(reference['requestId'])
        if referred is None or referred['run_id'] != reference.get('runId'):
            _invalid('proxy request and run do not agree', request_id, reference)
        child = request['child_task_id']
        if not child or parents.get(child) != request['run_id']:
            _invalid('proxy does not name its helper child', request_id, child)
        descendant = referred['run_id']
        if reference is origin and reference is not proxy:
            visited = set()
            while descendant != child and descendant in parents and descendant not in visited:
                visited.add(descendant)
                descendant = parents[descendant]
        if descendant != child:
            _invalid('proxy reference leaves its helper lineage', request_id, reference)

    reference = proxy if proxy is not None else origin
    if reference is None:
        _invalid('proxy has no source', request_id)
    leaf, terminal = _source_request(reference['requestId'], requests, payloads, parents, pure_proxies,
                                     seen | {request_id})
    if origin is not None and origin['requestId'] != terminal:
        _invalid('proxy and origin name different requests', request_id, origin)
    return leaf, terminal


def _replayable(continuation):
    return (continuation['state'] in REPLAYABLE_STATES
            or (continuation['state'] == 'cancelled' and continuation['attempt_id'] is None))


def _convert_continuations(connection, requests, payloads, decisions, parents, pure_proxies):
    converted = {}
    for row in connection.execute('SELECT rowid,* FROM workflow_continuations ORDER BY rowid'):
        identifier = row['continuation_id']
        kind = row['kind']
        request_id = row['request_id']
        input_text = row['input_text']
        if request_id is not None:
            source, _terminal = _source_request(request_id, requests, payloads, parents, pure_proxies)
            if source is None:
                request_id = None
            elif source['run_id'] != row['run_id']:
                _invalid('continuation source belongs to another run', identifier, row['run_id'],
                         source['request_id'], source['run_id'])
            else:
                request_id = source['request_id']
        if kind == 'helper':
            kind = 'manual'
            if _replayable(row) and row['note'] is None and request_id in decisions:
                decision = decisions[request_id].get('decision')
                if decision in ('approve', 'decline'):
                    text = {'approve': 'approved', 'decline': 'declined'}[decision]
                    input_text = f'The Host {text} the boundary request; continue with the recorded response.'
        converted[identifier] = (row['run_id'], kind, request_id, input_text)
    return converted


def _conflict_id(connection, request, result):
    request_id = request['request_id']
    if not isinstance(result, dict):
        _invalid('workspace resolution result must be an object', request_id)
    conflict_id = result.get('conflictId')
    reason = _object(request['reason_params_json'], 'workspace resolution reason', request_id)
    conflict_ids = reason.get('conflictIds')
    if (not isinstance(conflict_id, str)
            or request['reason_code'] != 'workspace-scope-violation'
            or not isinstance(conflict_ids, list) or conflict_id not in conflict_ids
            or connection.execute(
                'SELECT 1 FROM workflow_workspace_conflicts WHERE conflict_id=? AND run_id=?',
                (conflict_id, request['run_id']),
            ).fetchone() is None):
        _invalid('workspace resolution does not match its request and run', request_id,
                 request['run_id'], conflict_id)
    return conflict_id


def _shared_resolutions(connection, requests, decisions, continuations):
    sharers = defaultdict(list)
    for request_id, row in requests.items():
        if row['response_kind'] == 'continue':
            sharers[row['response_id']].append(request_id)

    resolution_owners = set()
    for continuation_id, request_ids in sharers.items():
        if not any(request_id in decisions for request_id in request_ids):
            continue
        continuation = continuations.get(continuation_id)
        if continuation is None:
            _invalid('shared resolution has no continuation', continuation_id, *request_ids)
        run_id, _, source_id, _ = continuation
        if source_id not in request_ids or source_id not in decisions:
            _invalid('shared resolution has no unique source request', continuation_id, run_id, *request_ids)
        source = requests[source_id]
        decision = decisions[source_id]
        if set(decision) != {'result'} or any(decisions[key] != decision for key in request_ids if key in decisions):
            _invalid('shared resolution records disagree', continuation_id, source_id, *request_ids)
        _conflict_id(connection, source, decision['result'])
        resolution_owners.add(source_id)
    return resolution_owners


def _check_resolution_proxies(requests, payloads, parents, pure_proxies, decisions):
    for request_id in pure_proxies:
        proxy = requests[request_id]
        if proxy['response_kind'] != 'resolution':
            continue
        leaf, _terminal = _source_request(request_id, requests, payloads, parents, pure_proxies)
        if (leaf is None or leaf['response_kind'] != 'resolution'
                or leaf['response_id'] != proxy['response_id']
                or decisions.get(leaf['request_id']) != decisions.get(request_id)):
            _invalid('standalone resolution proxy differs from its source request', request_id, proxy['response_id'])


def _direct_resolution(connection, row, decision):
    request_id = row['request_id']
    if set(decision) != {'parameters', 'result'} or not isinstance(decision['parameters'], dict):
        _invalid('invalid standalone resolution record', request_id)
    conflict_id = _conflict_id(connection, row, decision['result'])
    if row['response_id'] != conflict_id:
        _invalid('standalone resolution response does not name its conflict', request_id, conflict_id)

    parameters = decision['parameters']
    if 'targetRunId' in parameters and parameters['targetRunId'] != row['run_id']:
        _invalid('standalone resolution names another run', request_id, parameters['targetRunId'])
    retired = {'targetRunId', 'helperPolicy'} & parameters.keys()
    if not retired:
        return row['decision_json']
    return _json({**decision, 'parameters': {key: value for key, value in parameters.items() if key not in retired}})


def _convert_requests(connection, requests, payloads, decisions, parents, pure_proxies, continuations, lead_failure_ids):
    resolution_owners = _shared_resolutions(connection, requests, decisions, continuations)
    _check_resolution_proxies(requests, payloads, parents, pure_proxies, decisions)
    for request_id, row in requests.items():
        if request_id in pure_proxies:
            continue
        state, kind, response = row['state'], row['response_kind'], row['response_id']
        decision = decisions.get(request_id)
        resolution = None
        if decision is None and state in ('open', 'cancelled', 'superseded') and kind is None:
            pass
        elif decision is None and state == 'superseded' and kind == 'continue':
            pass
        elif (decision is not None and decision.get('decision') in ('approve', 'decline')
              and state != 'open' and kind in (None, 'decision')):
            state, kind, response = 'superseded', None, None
        elif decision is not None and state == 'approved' and kind is None and set(decision) == {'resolution'}:
            _conflict_id(connection, row, decision['resolution'])
            state, resolution = 'resolved', _json({**decision, 'legacy': True})
        elif decision is not None and state == 'approved' and kind == 'resolution':
            state = 'resolved'
            resolution = _direct_resolution(connection, row, decision)
        elif decision is not None and state == 'superseded' and kind == 'continue' and set(decision) == {'result'}:
            if request_id in resolution_owners:
                state, resolution = 'resolved', row['decision_json']
        else:
            _invalid(f'unknown request state and response combination {state}/{kind}', request_id, row['run_id'])

        payload = payloads[request_id]
        changed = 'suggestedBuddyId' in payload or request_id in lead_failure_ids
        payload.pop('suggestedBuddyId', None)
        yield (row, state, kind, response, resolution, _json(payload) if changed else row['payload_json'])


def _rebuild(connection, table, ddl, indexes, overrides=None):
    source = f'_{table}_41'
    connection.execute(f'CREATE TEMP TABLE {source} AS SELECT rowid AS _rowid, * FROM "{table}"')
    connection.execute(f'DROP TABLE "{table}"')
    connection.execute(ddl)
    columns = [row[1] for row in connection.execute(f'PRAGMA table_info("{table}")')]
    select = [(overrides or {}).get(column, column) for column in columns]
    connection.execute(f'INSERT INTO "{table}"(rowid,{",".join(columns)})'
                       f' SELECT _rowid,{",".join(select)} FROM {source} ORDER BY _rowid')
    connection.execute(f'DROP TABLE {source}')
    _create_indexes(connection, indexes)


def _create_indexes(connection, indexes):
    for statement in indexes.split(';'):
        if statement.strip():
            connection.execute(statement)


def _freeze_sessions(connection):
    connection.execute(f"""
        CREATE TEMP TABLE _attempt_groups_41 AS
        WITH RECURSIVE {OWNERSHIP_41}
        SELECT a.attempt_id, g.group_id
          FROM attempts a
          LEFT JOIN router_dispatches dispatch ON dispatch.task_id=a.task_id
          LEFT JOIN workflow_routes route ON route.decision_id=dispatch.decision_id
          JOIN groups g ON g.run_id=COALESCE(route.run_id, a.task_id)
         WHERE a.adapter<>'command'
    """)
    conflicts = connection.execute("""
        SELECT attempt_id FROM _attempt_groups_41 GROUP BY attempt_id HAVING COUNT(DISTINCT group_id)>1
    """).fetchall()
    if conflicts:
        _invalid('attempt has ambiguous session ownership', *(row[0] for row in conflicts))
    connection.execute('CREATE INDEX _attempt_groups_41_attempt_idx ON _attempt_groups_41(attempt_id)')


def _rebuild_requests(connection, converted):
    connection.execute('CREATE TEMP TABLE _workflow_requests_41 AS SELECT rowid AS _rowid,* FROM workflow_requests')
    connection.execute('CREATE UNIQUE INDEX _workflow_requests_41_rowid_idx ON _workflow_requests_41(_rowid)')
    connection.execute("""
        CREATE TEMP TABLE _request_responses_41(
            source_rowid INTEGER PRIMARY KEY, state TEXT NOT NULL, response_kind TEXT,
            response_id TEXT, resolution_json TEXT, payload_json TEXT NOT NULL)
    """)
    connection.executemany('INSERT INTO _request_responses_41 VALUES(?,?,?,?,?,?)',
                           ((row['rowid'], *fields) for row, *fields in converted))
    connection.execute('DROP TABLE workflow_requests')
    connection.execute(WORKFLOW_REQUESTS_DDL)
    copy = """
        INSERT INTO workflow_requests(
            rowid, request_id, run_id, turn_id, attempt_id, summary, reason_code, reason_params_json,
            decision_id, payload_json, state, resolution_json, response_kind, response_id,
            created_at, updated_at, decided_at)
        SELECT q._rowid,q.request_id,q.run_id,q.turn_id,q.attempt_id,q.summary,q.reason_code,q.reason_params_json,
               q.decision_id,c.payload_json,c.state,c.resolution_json,c.response_kind,c.response_id,
               q.created_at,q.updated_at,q.decided_at
          FROM _workflow_requests_41 q JOIN _request_responses_41 c ON c.source_rowid=q._rowid
    """
    try:
        connection.execute(copy + ' ORDER BY q._rowid')
    except sqlite3.IntegrityError:
        # The failed bulk statement wrote no rows. Identify the old request
        # that cannot satisfy the new constraints before the caller rolls back.
        for row, *_ in converted:
            try:
                connection.execute(copy + ' WHERE q._rowid=?', (row['rowid'],))
            except sqlite3.IntegrityError as error:
                raise ValueError(f"Schema 41 → 42: invalid request {row['request_id']} on {row['run_id']}") from error
        raise
    connection.execute('DROP TABLE _request_responses_41')
    connection.execute('DROP TABLE _workflow_requests_41')
    _create_indexes(connection, WORKFLOW_REQUESTS_INDEXES)


def upgrade(connection):
    requests = {
        row['request_id']: row
        for row in connection.execute('SELECT rowid,* FROM workflow_requests ORDER BY rowid')
    }
    payloads = {key: _object(row['payload_json'], 'request payload', key) for key, row in requests.items()}
    lead_failure_ids = _lead_failures(connection, requests, payloads)
    pure_proxies = {key for key, row in requests.items() if row['kind'] in PROXY_KINDS and key not in lead_failure_ids}
    _preflight(connection, requests, pure_proxies)
    decisions = {key: _object(row['decision_json'], 'request decision', key)
                 for key, row in requests.items() if row['decision_json'] is not None}
    parents = dict(connection.execute('SELECT child_task_id,parent_run_id FROM workflow_children'))
    continuations = _convert_continuations(connection, requests, payloads, decisions, parents, pure_proxies)
    converted_requests = list(_convert_requests(connection, requests, payloads, decisions, parents, pure_proxies,
                                                continuations, lead_failure_ids))

    _freeze_sessions(connection)
    # The installer requires idle stock. An offline step may still have an
    # uncertain attempt, so carry its previous queue reason into the new owner.
    connection.execute('CREATE TEMP TABLE _waiting_41 AS SELECT active_attempt_id AS attempt_id,queue_reason '
                       'FROM tasks WHERE active_attempt_id IS NOT NULL AND queue_reason IS NOT NULL')
    connection.execute('CREATE INDEX _waiting_41_attempt_idx ON _waiting_41(attempt_id)')
    prior_wait = '(SELECT queue_reason FROM _waiting_41 w WHERE w.attempt_id=_attempts_41.attempt_id)'
    _rebuild(connection, 'tasks', TASKS_DDL, TASKS_INDEXES, {
        'queue_reason': """CASE WHEN active_attempt_id IS NULL AND queue_reason NOT IN (
            'attempt-uncertain-after-restart','execution-connection-unknown',
            'harness-probe-unknown','router-input-unknown') THEN queue_reason END""",
    })
    waiting_can_survive = "execution_state NOT IN ('finalizing','finished') AND result_json IS NULL"
    _rebuild(connection, 'attempts', ATTEMPTS_DDL, ATTEMPTS_INDEXES, {
        'connection_reason': f"""CASE WHEN execution_state='uncertain' AND result_json IS NULL
            THEN CASE WHEN {prior_wait}='execution-connection-unknown'
                      THEN 'execution-connection-unknown' ELSE 'attempt-uncertain-after-restart' END END""",
        'probe_unknown': f"CASE WHEN {waiting_can_survive} AND {prior_wait}='harness-probe-unknown' THEN 1 ELSE 0 END",
        'waiting_reason': f"CASE WHEN {waiting_can_survive} AND {prior_wait}='router-input-unknown' THEN 'router-input-unknown' END",
        'waiting_hint_seq': '0',
        'session_group_id': '(SELECT group_id FROM _attempt_groups_41 g WHERE g.attempt_id=_attempts_41.attempt_id)',
    })
    connection.execute('DROP TABLE _waiting_41')
    connection.execute('DROP TABLE _attempt_groups_41')

    _rebuild_requests(connection, converted_requests)

    connection.execute("""
        CREATE TEMP TABLE _continuation_sources_41(
            continuation_id TEXT PRIMARY KEY, kind TEXT NOT NULL, request_id TEXT, input_text TEXT NOT NULL)
    """)
    connection.executemany('INSERT INTO _continuation_sources_41 VALUES(?,?,?,?)',
                           ((key, kind, source, text) for key, (_, kind, source, text) in continuations.items()))
    _rebuild(connection, 'workflow_continuations', WORKFLOW_CONTINUATIONS_DDL, WORKFLOW_CONTINUATIONS_INDEXES, {
        column: f'(SELECT {column} FROM _continuation_sources_41 s WHERE s.continuation_id=_workflow_continuations_41.continuation_id)'
        for column in ('kind', 'request_id', 'input_text')
    })
    connection.execute('DROP TABLE _continuation_sources_41')
    _rebuild(connection, 'workspace_reservations', WORKSPACE_RESERVATIONS_DDL, WORKSPACE_RESERVATIONS_INDEXES)
    _rebuild(connection, 'workflow_runs', WORKFLOW_RUNS_DDL, WORKFLOW_RUNS_INDEXES)
    # Preserve the selected open boundary; otherwise use the Schema 41 ordering.
    connection.execute("""
        UPDATE workflow_runs SET active_request_id=(
            SELECT COALESCE(
                (SELECT request_id FROM workflow_requests q
                  WHERE q.run_id=workflow_runs.run_id AND q.state='open'
                    AND q.request_id=workflow_runs.active_request_id),
                (SELECT request_id FROM workflow_requests q
                  WHERE q.run_id=workflow_runs.run_id AND q.state='open'
                  ORDER BY q.created_at, q.request_id LIMIT 1)
            )
        )
        WHERE state NOT IN ('accepted','concluded','cancelled')
    """)
    connection.execute("""
        UPDATE workflow_runs SET active_request_id=NULL
         WHERE state IN ('accepted','concluded','cancelled') AND active_request_id IS NOT NULL
    """)
    connection.execute("""
        UPDATE tasks SET queue_reason=CASE WHEN EXISTS (
            SELECT 1 FROM workflow_runs r WHERE r.run_id=tasks.task_id AND r.active_request_id IS NOT NULL
        ) THEN 'awaiting-host' END
        WHERE queue_reason IN ('helper-attention','awaiting-helpers')
    """)
    connection.execute('DROP TABLE workflow_children')
    connection.execute("UPDATE meta SET value='42' WHERE key='schema_version'")
