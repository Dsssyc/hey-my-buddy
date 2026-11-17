"""Remove private allocations and the refs in repositories still at their identity."""
from __future__ import annotations

from pathlib import Path
import json
import re

from ... import private_dirs
from ...paths import native_path
from ...errors import BoardError
from ...protocol.reasons import reason
from . import projects, workspace
from .macro_task_deletion_progress import collect_skipped
from .workspace_bindings import MANIFEST_OWNERS_CTE, output_record_id, output_references, repository_reference


_PREFIX = "refs/hey-my-buddy/workspaces/"
_SKIP_CODES = {
    "REF_UNPROVEN": "macro-task-deletion.ref-unproven",
    "REF_CHANGED": "macro-task-deletion.ref-changed",
    "OWNER_UNPROVEN": "macro-task-deletion.owner-unproven",
    "DEPENDENCY_RETAINED": "macro-task-deletion.dependency-retained",
    "owner-unproven": "macro-task-deletion.owner-unproven",
    "not-a-managed-worktree": "macro-task-deletion.not-a-managed-worktree",
    "unsafe-path": "macro-task-deletion.unsafe-path",
    "checkout-missing": "macro-task-deletion.checkout-missing",
    "identity-changed": "macro-task-deletion.identity-changed",
    "unregistered-checkout": "macro-task-deletion.unregistered-checkout",
    "lock-mismatch": "macro-task-deletion.lock-mismatch",
    "attached-branch": "macro-task-deletion.attached-branch",
}


def _skip(kind: str, path: str, code: str, workspace_id: str | None = None) -> dict:
    return {"kind": kind, "path": path, "workspaceId": workspace_id,
            "reason": reason(_SKIP_CODES[code])}


def _repository_current(manifest: dict, *, candidates=()) -> Path | None:
    # The locator's original-checkout rule and its candidate retained/input
    # proof are also the deletion rules. All values here are SQL owner facts.
    reference = repository_reference(manifest)
    repository = workspace.locate_repository(reference, candidates=candidates, repair=False)
    return Path(repository) if repository is not None else None


def _fixed_refs(manifest: dict) -> dict[str, str]:
    identifier = manifest['workspace_id']
    prefix = _PREFIX + identifier + "/"
    return {prefix + 'input': manifest['input_commit'],
            prefix + 'staged': manifest['staged_commit'],
            prefix + 'retained/input': manifest['input_commit'],
            prefix + 'retained/staged': manifest['staged_commit']}


def _state_owner_proven(connection, state: Path, owner: dict) -> bool:
    """Bind SQL owner publication to this state's preparation identity."""
    args = (owner['owner_run_id'], owner['workspace_id'], owner['manifest_sha256'])
    source = connection.execute(
        "SELECT t.request_id FROM workflow_artifacts a JOIN tasks t ON t.task_id=a.run_id"
        " WHERE a.kind='input' AND a.workspace_facts_role='owner'"
        " AND a.run_id=? AND a.workspace_id=? AND a.input_manifest_sha256=?",
        args).fetchone()
    if source is None:
        source = connection.execute(
            "SELECT run_id || ':' || continuation_id AS request_id FROM workflow_continuations"
            " WHERE workspace_facts_role='owner' AND run_id=?"
            " AND workspace_id=? AND workspace_manifest_sha256=?",
            args).fetchone()
    if source is None:
        return False
    expected = 'ws-' + workspace._sha(workspace._json([str(state), source['request_id']]))[:32]
    return owner['workspace_id'] == expected


def _artifact_refs(artifact: dict, manifests: dict[str, dict]) -> dict[str, str]:
    if artifact['kind'] not in ('output', 'resolved-output', 'partial-output', 'abandoned-site'):
        return {}
    identifier = artifact['output_workspace_id']
    manifest = manifests.get(identifier)
    if manifest is None or artifact['output_manifest_sha256'] != manifest['manifest_sha256']:
        return {}
    ref, retained = output_references(artifact)
    return {ref: artifact['output_commit'], retained: artifact['output_commit']}


def _expected_refs(by_id: dict[str, dict], artifacts: list[dict]) -> dict[str, dict[str, str]]:
    """Bind explicit snapshots and their equivalent physical output names once."""
    expected = {identifier: _fixed_refs(manifest) for identifier, manifest in by_id.items()}
    paired_outputs = []
    for artifact in artifacts:
        if not isinstance(artifact, dict):
            continue
        bound = _artifact_refs(artifact, by_id)
        for ref, oid in bound.items():
            identifier = artifact['output_workspace_id']
            previous = expected[identifier].setdefault(ref, oid)
            if previous != oid:
                raise BoardError("WORKSPACE_CONFLICT", "Conflicting retained artifact references", ref=ref)
        if bound and artifact['kind'] in ('output', 'partial-output'):
            paired_outputs.append(artifact)
    # Publication deduplicates equal Worker/Host snapshots, although their
    # physical seal names differ. A counterpart is bound only to that same
    # commit; a separately published output keeps its own explicit binding.
    for artifact in paired_outputs:
        identifier = artifact['output_workspace_id']
        opposite = {**artifact, 'output_origin': 'worker' if artifact['output_origin'] == 'host' else 'host'}
        for ref in output_references(opposite):
            expected[identifier].setdefault(ref, artifact['output_commit'])
    return expected


def _refs(repository: Path, identifier: str) -> dict[str, str | None]:
    prefix = _PREFIX + identifier + "/"
    output = workspace._git(repository, "for-each-ref", "--format=%(refname)%00%(objectname)%00%(symref)", prefix)
    refs = {}
    for line in output.splitlines():
        parts = line.split(b"\0")
        if len(parts) != 3:
            raise BoardError("WORKSPACE_GIT_ERROR", "Invalid workspace reference inventory")
        ref, oid, symbolic = parts
        try:
            name = ref.decode("utf-8", errors="strict")
            value = oid.decode("ascii", errors="strict")
        except UnicodeError as error:
            raise BoardError("WORKSPACE_GIT_ERROR", "Invalid workspace reference inventory") from error
        if (not name.startswith(prefix)
                or not symbolic and re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", value) is None):
            raise BoardError("WORKSPACE_GIT_ERROR", "Invalid workspace reference inventory")
        # A symbolic entry is observed, but is never a proven ordinary ref.
        refs[name] = None if symbolic else value
    return refs


def _delete_refs(repository: Path, identifier: str, expected: dict[str, str]) -> list[dict]:
    with collect_skipped() as skipped:
        anchor = _PREFIX + identifier + "/retained/input"
        for ref, actual in sorted(_refs(repository, identifier).items(), key=lambda item: item[0] == anchor):
            # Keep the relocation proof until every other reference is gone.
            if ref == anchor and skipped:
                continue
            bound = expected.get(ref)
            if bound is None or actual is None:
                skipped.append(_skip("ref", ref, "REF_UNPROVEN", identifier))
                continue
            if actual != bound:
                skipped.append(_skip("ref", ref, "REF_CHANGED", identifier))
                continue
            # A competing update can win after enumeration. Distinguish that
            # identity change from an actual Git failure without deleting its value.
            # Never follow a ref that becomes symbolic after this observation.
            try:
                workspace._git(repository, "update-ref", "--no-deref", "-d", ref, bound)
            except BoardError:
                current = _refs(repository,identifier)
                if current.get(ref) == bound:
                    raise
                if ref in current:
                    skipped.append(_skip("ref",ref,"REF_CHANGED",identifier))
        return skipped


def _delete_exact_refs(repository: Path, identifier: str, expected: dict[str, str]) -> list[dict]:
    """Delete only one output's own names inside a shared workspace namespace."""
    with collect_skipped() as skipped:
        actual = _refs(repository, identifier)
        for ref, bound in sorted(expected.items()):
            if ref not in actual:
                continue
            if actual[ref] is None:
                skipped.append(_skip('ref', ref, 'REF_UNPROVEN', identifier))
            elif actual[ref] != bound:
                skipped.append(_skip('ref', ref, 'REF_CHANGED', identifier))
            else:
                try:
                    workspace._git(repository, 'update-ref', '--no-deref', '-d', ref, bound)
                except BoardError:
                    current = _refs(repository, identifier)
                    if current.get(ref) == bound:
                        raise
                    if ref in current:
                        skipped.append(_skip('ref', ref, 'REF_CHANGED', identifier))
        return skipped


def _foreign_output_files(state: Path, artifact: dict) -> tuple[Path, tuple[str, ...]]:
    """Derive the sealed output's own physical names from its SQL ref identity."""
    identifier = artifact['output_workspace_id']
    record_id = output_record_id(artifact['run_id'], artifact['attempt_id'])
    suffix = '-host' if artifact['kind'] in ('output', 'partial-output') and artifact['output_origin'] == 'host' else ''
    directory = state / 'workspaces' / identifier / 'outputs' / (record_id + suffix)
    if artifact['kind'] in ('output', 'partial-output'):
        names = ('output.json', 'output.patch', 'pending.json')
    elif artifact['kind'] == 'resolved-output':
        action = artifact['output_action']
        names = (f'resolve-{action}.json', f'resolve-{action}.patch', f'resolve-{action}-pending.json')
    else:
        names = ('abandoned.json', 'abandoned.patch', 'abandoned-pending.json')
    return directory, names


def _remove_foreign_output_directory(directory: Path, names: set[str], identifier: str,
                                     *, shared: bool) -> list[dict]:
    """Remove only names derived from the borrower's task and SQL input."""
    if not workspace._path_present(directory):
        return []
    if private_dirs.linked(directory) or not native_path(directory).is_dir():
        return [_skip('allocation', str(directory), 'OWNER_UNPROVEN', identifier)]
    for name in names:
        path = directory / name
        if private_dirs.linked(path) or workspace._path_present(path) and not native_path(path).is_file():
            return [_skip('allocation', str(path), 'OWNER_UNPROVEN', identifier)]
    conflicts = directory / 'scope-conflicts'
    if not shared and (private_dirs.linked(conflicts) or workspace._path_present(conflicts) and not native_path(conflicts).is_dir()):
        return [_skip('allocation', str(conflicts), 'OWNER_UNPROVEN', identifier)]
    for name in names:
        native_path(directory / name).unlink(missing_ok=True)
    if not shared and native_path(conflicts).is_dir():
        private_dirs.remove_tree(conflicts)
    if shared:
        return []
    if any(native_path(directory).iterdir()):
        return [_skip('allocation', str(directory), 'DEPENDENCY_RETAINED', identifier)]
    native_path(directory).rmdir()
    return []


def _delete_foreign_outputs(store, by_id, artifacts, owned_runs, project_paths) -> list[dict]:
    """Remove a borrower's exact output while the workspace owner remains."""
    state = store.directory.resolve()
    scope_ids = json.dumps(sorted(owned_runs))
    skipped = []
    planned: dict[Path, tuple[str, set[str]]] = {}
    with store.db.read() as connection:
        external = [dict(row) for row in connection.execute(
            "SELECT * FROM workflow_artifacts WHERE run_id NOT IN (SELECT value FROM json_each(?))"
            " AND kind IN ('output','resolved-output','partial-output','abandoned-site')",
            (scope_ids,))]
        external_refs = {output_references(row)[0] for row in external}
        own_refs = {output_references(row)[0] for row in artifacts
                    if row['kind'] in ('output', 'resolved-output', 'partial-output', 'abandoned-site')}
        for artifact in artifacts:
            if artifact['kind'] not in ('output', 'resolved-output', 'partial-output', 'abandoned-site'):
                continue
            identifier = artifact['output_workspace_id']
            if identifier in by_id:
                continue
            ref = output_references(artifact)[0]
            owner = connection.execute(
                f'WITH manifest_owners AS ({MANIFEST_OWNERS_CTE})'
                ' SELECT o.*,COALESCE(m.project_id,r.standalone_project_id) AS project_id'
                ' FROM manifest_owners o JOIN workflow_runs r ON r.run_id=o.owner_run_id'
                ' LEFT JOIN macro_tasks m ON m.macro_task_id=r.macro_task_id'
                ' WHERE o.workspace_id=? AND o.manifest_sha256=?',
                (identifier, artifact['output_manifest_sha256'])).fetchone()
            if owner is None:
                skipped.append(_skip('ref', ref, 'REF_UNPROVEN', identifier))
                continue
            owner = dict(owner)
            if not _state_owner_proven(connection, state, owner):
                skipped.append(_skip('allocation', str(state / 'workspaces' / identifier),
                                     'OWNER_UNPROVEN', identifier))
                continue
            bound = _artifact_refs(artifact, {identifier: owner})
            if not bound:
                skipped.append(_skip('ref', ref, 'REF_UNPROVEN', identifier))
                continue
            if ref in external_refs:
                continue
            initial = connection.execute(
                f'WITH manifest_owners AS ({MANIFEST_OWNERS_CTE})'
                ' SELECT o.input_commit FROM workflow_artifacts a JOIN manifest_owners o'
                ' ON o.workspace_id=a.workspace_id AND o.manifest_sha256=a.input_manifest_sha256'
                " WHERE a.run_id=? AND a.kind='input' ORDER BY a.rowid LIMIT 1",
                (artifact['run_id'],)).fetchone()
            if initial is None:
                skipped.append(_skip('allocation', ref, 'OWNER_UNPROVEN', identifier))
                continue
            initial_commit = initial['input_commit']
            counterpart = None
            if artifact['kind'] in ('output', 'partial-output'):
                opposite_origin = 'worker' if artifact['output_origin'] == 'host' else 'host'
                counterpart = {**artifact, 'output_origin': opposite_origin}
                opposite_ref, opposite_retained = output_references(counterpart)
                if opposite_ref not in external_refs and opposite_ref not in own_refs:
                    bound[opposite_ref] = artifact['output_commit']
                    bound[opposite_retained] = artifact['output_commit']
                else:
                    counterpart = None
            directory, names = _foreign_output_files(state, artifact)
            if (artifact['record_path'] is not None and
                    state / artifact['record_path'] not in tuple(directory / name for name in names)):
                skipped.append(_skip('allocation', str(directory), 'OWNER_UNPROVEN', identifier))
                continue
            if any(private_dirs.linked(path) for path in (state / 'workspaces' / identifier,
                                                          state / 'workspaces' / identifier / 'outputs', directory)):
                skipped.append(_skip('allocation', str(directory), 'OWNER_UNPROVEN', identifier))
                continue
            project_id = owner['project_id']
            if project_id not in project_paths:
                project_paths[project_id] = projects.known_repository_paths(connection, project_id)
            repository = _repository_current(owner, candidates=project_paths[project_id])
            if repository is None:
                skipped.append(_skip('ref', ref, 'DEPENDENCY_RETAINED', identifier))
                continue
            ref_skips = _delete_exact_refs(repository, identifier, bound)
            skipped.extend(ref_skips)
            if ref_skips:
                continue
            own_names = set(names)
            if artifact['kind'] in ('output', 'partial-output', 'resolved-output'):
                own_names.add(f'cumulative-{initial_commit}.patch')
            planned.setdefault(directory, (identifier, set()))[1].update(own_names)
            if counterpart is not None:
                opposite_directory, opposite_names = _foreign_output_files(state, counterpart)
                opposite_owned = set(opposite_names)
                opposite_owned.add(f'cumulative-{initial_commit}.patch')
                planned.setdefault(opposite_directory, (identifier, set()))[1].update(opposite_owned)
        shared_directories = {_foreign_output_files(state, row)[0] for row in external}
        for directory, (identifier, names) in planned.items():
            skipped.extend(_remove_foreign_output_directory(directory, names, identifier,
                                                             shared=directory in shared_directories))
    return skipped


def _record_proven(state: Path, manifest: dict) -> bool:
    identifier = manifest['workspace_id']
    directory = state / "workspaces" / identifier
    structural = (manifest['workspace_kind'] in ('worktree', 'existing')
            and (manifest['workspace_kind'] != 'worktree' or
                 manifest['checkout_root'] == str(directory / 'checkout'))
            and manifest['owner_run_id']
            and manifest['project_id']
            and manifest['input_commit'] and manifest['staged_commit']
            and not private_dirs.linked(directory)
            and workspace.allocation_lock_directory(state, identifier) == directory)
    if not structural:
        return False
    request = workspace._record(directory / 'request.json')
    pinned = workspace._record(directory / 'input.json')
    if not isinstance(request, dict) or not isinstance(pinned, dict):
        return False
    request_id = request.get('requestId')
    if not isinstance(request_id, str) or not request_id or '\0' in request_id:
        return False
    expected = 'ws-' + workspace._sha(workspace._json([str(state), request_id]))[:32]
    return (expected == identifier and request['intent']['kind'] == manifest['workspace_kind']
            and pinned['inputCommit'] == manifest['input_commit']
            and pinned['snapshot']['stagedCommit'] == manifest['staged_commit'])


def _tombstone(state: Path, identifier: str) -> Path:
    return state / "workspaces" / (".deleting-" + identifier)


def _remove_tombstone(state: Path, identifier: str) -> bool:
    tomb = _tombstone(state, identifier)
    if not workspace._path_present(tomb):
        return False
    if private_dirs.linked(tomb) or not tomb.is_dir():
        raise BoardError("WORKSPACE_UNSAFE", "The deletion tombstone is unsafe")
    private_dirs.remove_tree(tomb)
    return True


def delete_workspaces(store, manifests: list[dict], artifacts: list[dict], *,
                      project_paths=None, owned_runs=()) -> list[dict]:
    """Remove SQL-owned checkouts, exact Git refs, and private record trees."""
    state = store.directory.resolve()
    project_paths = {} if project_paths is None else project_paths
    by_id = {}
    with collect_skipped() as skipped:
        for manifest in manifests:
            identifier = manifest['workspace_id']
            if identifier in by_id and by_id[identifier] != manifest:
                raise BoardError('WORKSPACE_CONFLICT', 'Conflicting retained workspace owners')
            by_id[identifier] = manifest
        with store.db.read() as connection:
            for identifier, owner in by_id.items():
                if not _state_owner_proven(connection, state, owner):
                    skipped.append(_skip('allocation', str(state / 'workspaces' / identifier),
                                         'OWNER_UNPROVEN', identifier))
        if skipped:
            return skipped
        skipped.extend(_delete_foreign_outputs(store, by_id, artifacts, owned_runs, project_paths))
        if skipped:
            return skipped
        expected = _expected_refs(by_id, artifacts)
        blocked_roots = {}
        for identifier, manifest in sorted(by_id.items()):
            if manifest['workspace_kind'] != 'worktree':
                continue
            root = manifest['checkout_root']
            directory = state / 'workspaces' / identifier
            if root != str(directory / 'checkout'):
                skipped.append(_skip('allocation', root, 'OWNER_UNPROVEN', identifier))
                blocked_roots[root] = identifier
                continue
            if not workspace._path_present(directory):
                continue
            if not _record_proven(state, manifest):
                skipped.append(_skip('allocation', str(directory), 'OWNER_UNPROVEN', identifier))
                blocked_roots[root] = identifier
                continue
            with workspace._lock(directory, create=False):
                repository = _repository_current(manifest, candidates=project_paths.get(manifest['project_id'], ()))
                checkout = directory / 'checkout'
                if workspace._path_present(checkout):
                    from .checkout_processes import allocation_created_at, require_idle_checkout
                    require_idle_checkout(checkout, allocated_at=allocation_created_at(directory))
                if repository is not None:
                    reasons = workspace.deletion_remove_checkout(repository, checkout,
                        workspace_id=identifier, project_id=manifest['project_id'],
                        input_commit=manifest['input_commit'])
                    skipped.extend(_skip('checkout', str(checkout), code, identifier) for code in reasons)
                if repository is not None and workspace._path_present(checkout):
                    skipped.append(_skip('allocation', str(directory), 'DEPENDENCY_RETAINED', identifier))
                    blocked_roots[root] = identifier
                elif repository is not None and reasons:
                    blocked_roots[root] = identifier

        for identifier, manifest in sorted(by_id.items()):
            directory = state / 'workspaces' / identifier
            root = manifest['checkout_root']
            if root in blocked_roots:
                if identifier != blocked_roots[root] and workspace._path_present(directory):
                    skipped.append(_skip('allocation', str(directory), 'DEPENDENCY_RETAINED', identifier))
                continue
            repository = _repository_current(manifest, candidates=project_paths.get(manifest['project_id'], ()))
            if repository is not None:
                ref_skips = _delete_refs(repository, identifier, expected[identifier])
                skipped.extend(ref_skips)
                if ref_skips or _refs(repository, identifier):
                    skipped.append(_skip('allocation', str(directory), 'DEPENDENCY_RETAINED', identifier))
                    continue
            if not workspace._path_present(directory):
                _remove_tombstone(state, identifier)
                continue
            tomb = _tombstone(state, identifier)
            if workspace._path_present(tomb) or not _record_proven(state, manifest):
                skipped.append(_skip('allocation', str(directory), 'OWNER_UNPROVEN', identifier))
                continue
            with workspace._lock(directory, create=False):
                checkout = directory / 'checkout'
                if workspace._path_present(checkout):
                    if repository is not None:
                        skipped.append(_skip('allocation', str(directory), 'DEPENDENCY_RETAINED', identifier))
                        continue
                    if manifest['workspace_kind'] != 'worktree' or private_dirs.linked(checkout):
                        skipped.append(_skip('allocation', str(directory), 'OWNER_UNPROVEN', identifier))
                        continue
                    from .checkout_processes import allocation_created_at, require_idle_checkout
                    require_idle_checkout(checkout, allocated_at=allocation_created_at(directory))
            # Windows cannot rename while the descendant lock is open.
            native_path(directory).rename(native_path(tomb))
            private_dirs.remove_tree(tomb)
        return skipped
