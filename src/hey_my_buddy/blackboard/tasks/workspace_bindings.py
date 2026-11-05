"""Relational workspace identity and the single owner of location facts."""

import hashlib
import json

from ...protocol.workspace_manifest import artifact_manifest, resolve_manifest, turn_manifest


MANIFEST_OWNERS_CTE = """
SELECT workspace_id, input_manifest_sha256 AS manifest_sha256,
       workspace_kind, checkout_root, repository_path, input_commit, staged_commit, run_id AS owner_run_id
FROM workflow_artifacts WHERE kind='input' AND workspace_facts_role='owner'
UNION ALL
SELECT workspace_id, workspace_manifest_sha256,
       workspace_kind, checkout_root, repository_path, input_commit, staged_commit, run_id
FROM workflow_continuations WHERE workspace_facts_role='owner'
"""


def output_record_id(run_id, attempt_id):
    """The physical output directory identity, independent of stored refs."""
    return hashlib.sha256(json.dumps([run_id, attempt_id], sort_keys=True,
                                     separators=(',', ':'), ensure_ascii=True).encode()).hexdigest()


def output_references(artifact):
    """Return an output's published and retained Git names from SQL facts."""
    identifier = artifact['output_workspace_id']
    record_id = output_record_id(artifact['run_id'], artifact['attempt_id'])
    kind = artifact['kind']
    if kind in ('output', 'partial-output'):
        suffix = record_id + ('-host' if artifact['output_origin'] == 'host' else '')
        published, retained = 'outputs/' + suffix, 'retained/output-' + suffix
    elif kind == 'resolved-output':
        suffix = record_id + '-' + artifact['output_action']
        published, retained = 'resolutions/' + suffix, 'retained/resolution-' + suffix
    elif kind == 'abandoned-site':
        published, retained = 'abandoned/' + record_id, 'retained/abandoned-' + record_id
    else:
        raise ValueError(f'Artifact kind has no output references: {kind}')
    prefix = f'refs/hey-my-buddy/workspaces/{identifier}/'
    return prefix + published, prefix + retained

# The terminal binding is frozen history. A run that never began a turn uses its
# initial input; the current slot is reserved for runs that can still continue.
RUN_BINDINGS_CTE = """
SELECT r.run_id,
 CASE WHEN r.state NOT IN ('accepted','concluded') THEN r.workspace_id
      WHEN EXISTS(SELECT 1 FROM workflow_turns t WHERE t.run_id=r.run_id)
      THEN (SELECT t.workspace_id FROM workflow_turns t WHERE t.run_id=r.run_id ORDER BY t.turn_index DESC LIMIT 1)
      ELSE (SELECT a.workspace_id FROM workflow_artifacts a WHERE a.run_id=r.run_id AND a.kind='input' ORDER BY a.rowid LIMIT 1) END AS workspace_id,
 CASE WHEN r.state NOT IN ('accepted','concluded') THEN r.workspace_manifest_sha256
      WHEN EXISTS(SELECT 1 FROM workflow_turns t WHERE t.run_id=r.run_id)
      THEN (SELECT t.workspace_manifest_sha256 FROM workflow_turns t WHERE t.run_id=r.run_id ORDER BY t.turn_index DESC LIMIT 1)
      ELSE (SELECT a.input_manifest_sha256 FROM workflow_artifacts a WHERE a.run_id=r.run_id AND a.kind='input' ORDER BY a.rowid LIMIT 1) END AS manifest_sha256
FROM workflow_runs r
"""


def effective_manifest(state_dir, connection, run_row):
    """Read Git evidence or the explicitly recorded none execution context."""
    if run_row['checkout'] == 'none':
        choice = json.loads(run_row['execution_workspace_json'])
        return {'kind': 'none', 'path': choice['cwd'], 'projectId': choice['projectId'],
                'projectKind': run_row['project_kind'], 'access': choice['access'],
                'integrator': choice['integrator'],
                **({'accessNote': choice['accessNote']} if choice['access'] == 'full' else {})}
    if run_row["state"] not in ("accepted", "concluded"):
        return resolve_manifest(state_dir, {
            "workspaceId": run_row["workspace_id"],
            "manifestSha256": run_row["workspace_manifest_sha256"],
        })
    turn = connection.execute(
        "SELECT input_json FROM workflow_turns WHERE run_id=? ORDER BY turn_index DESC LIMIT 1",
        (run_row["run_id"],),
    ).fetchone()
    if turn is not None:
        return turn_manifest(state_dir, json.loads(turn["input_json"]))
    initial = connection.execute(
        "SELECT * FROM workflow_artifacts WHERE run_id=? AND kind='input' ORDER BY rowid LIMIT 1",
        (run_row["run_id"],),
    ).fetchone()
    return artifact_manifest(state_dir, initial)


def workspace_facts(connection, manifest):
    """Register one owner at publication; replayed files do not imply SQL ownership."""
    existing = connection.execute(
        f"WITH manifest_owners AS ({MANIFEST_OWNERS_CTE}) SELECT 1 FROM manifest_owners"
        " WHERE workspace_id=? AND manifest_sha256=?",
        (manifest["workspaceId"], manifest["manifestSha256"]),
    ).fetchone()
    if existing is not None:
        return "reference", None, None, None, None, None
    return ("owner", manifest["kind"], manifest["checkoutRoot"], manifest["snapshot"]["repositoryPath"],
            manifest["inputCommit"], manifest["snapshot"]["stagedCommit"])


def owned_manifest(state_dir, connection, workspace_id, manifest_sha256):
    """Read the declared owner of one selected output's exact input pair."""
    pair = workspace_id, manifest_sha256
    artifact = connection.execute(
        "SELECT * FROM workflow_artifacts WHERE kind='input' AND workspace_facts_role='owner'"
        " AND workspace_id=? AND input_manifest_sha256=?", pair).fetchone()
    if artifact is not None:
        return artifact_manifest(state_dir, artifact)
    continuation = connection.execute(
        "SELECT workspace_id,workspace_manifest_sha256 FROM workflow_continuations"
        " WHERE workspace_facts_role='owner' AND workspace_id=? AND workspace_manifest_sha256=?",
        pair).fetchone()
    return resolve_manifest(state_dir, {"workspaceId": continuation["workspace_id"],
                                        "manifestSha256": continuation["workspace_manifest_sha256"]})


def retained_manifests(state_dir, connection, run_id):
    """Allocation evidence in publication order, using each record's stated form."""
    manifests = {}
    for row in connection.execute(
            "SELECT * FROM workflow_artifacts WHERE run_id=? AND kind='input' ORDER BY rowid", (run_id,)):
        document = artifact_manifest(state_dir, row)
        manifests[(row["workspace_id"], row["input_manifest_sha256"])] = document
    for row in connection.execute(
            "SELECT * FROM workflow_turns WHERE run_id=? AND workspace_id IS NOT NULL ORDER BY turn_index", (run_id,)):
        pair = row["workspace_id"], row["workspace_manifest_sha256"]
        if pair not in manifests:
            manifests[pair] = turn_manifest(state_dir, json.loads(row["input_json"]))
    for row in connection.execute(
            f"WITH manifest_owners AS ({MANIFEST_OWNERS_CTE})"
            " SELECT c.workspace_id,c.workspace_manifest_sha256,o.workspace_kind"
            " FROM workflow_continuations c JOIN manifest_owners o"
            " ON o.workspace_id=c.workspace_id AND o.manifest_sha256=c.workspace_manifest_sha256"
            " WHERE c.run_id=? AND c.workspace_id IS NOT NULL ORDER BY c.rowid", (run_id,)):
        pair = row["workspace_id"], row["workspace_manifest_sha256"]
        # An unused existing continuation owns no checkout. Its SQL binding is
        # retained for reference scans; it needs no manifest body for deletion.
        if pair not in manifests and row["workspace_kind"] == "worktree":
            manifests[pair] = resolve_manifest(state_dir, {"workspaceId": pair[0], "manifestSha256": pair[1]})
    return list(manifests.values())
