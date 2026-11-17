"""Project identity at a source directory and its current board record."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path

from ...errors import BoardError
from ...json_codec import canonical_json
from ...protocol.workspace_manifest import project_id
from . import workspace
from .workspace_bindings import MANIFEST_OWNERS_CTE


def folder_id(path: str) -> str:
    """The stable identity of a folder at its canonical absolute path."""
    return hashlib.sha256(("hey-my-buddy:folder-project:v1\0" + os.path.normcase(path))
                          .encode("utf-8")).hexdigest()


def creation_key(parts: list[str]) -> str:
    """Hash the typed, versioned creation namespace resolved by the service."""
    return hashlib.sha256(canonical_json(parts).encode('utf-8')).hexdigest()


def _directory(cwd: str) -> Path:
    try:
        directory = Path(cwd).resolve(strict=True)
        if not directory.is_dir():
            raise BoardError("WORKSPACE_UNAVAILABLE", "cwd is not a directory", cwd=cwd)
    except OSError as error:
        raise BoardError("WORKSPACE_UNAVAILABLE", "The cwd directory is unavailable", cwd=cwd,
                         reason=str(error)) from error
    return directory


def repository_root(repository_path: str) -> str:
    """The main source repository represented by a common Git directory."""
    repository = Path(repository_path)
    return str(repository.parent if repository.name == ".git" else repository)


def label(path: str | None, repository_path: str | None) -> str | None:
    """Name a Git repository or the submitted folder."""
    source = repository_root(repository_path) if repository_path else path
    if not source:
        return None
    return Path(source).name or source


def _source(directory: Path, module):
    repository = module.repository_at(directory, allow_parent=True)
    return (repository, repository_root(repository) if repository is not None else None)


def _identity(directory: Path, repository, root, module, folder_project_id):
    path = str(directory)
    if repository is None:
        return {"projectId": folder_id(path), "projectKind": "folder", "cwd": path,
                "projectPath": path, "repositoryPath": None}
    identifier = module.ensure_project_id(repository, preferred=folder_project_id)
    return {"projectId": identifier, "projectKind": "git", "cwd": path, "projectPath": root,
            "repositoryPath": repository}


def identify_for_board(store, cwd: str, *, module=workspace) -> dict:
    """Reuse a recorded folder only when Git first appears at that exact root."""
    directory = _directory(cwd)
    repository, root = _source(directory, module)
    if repository is None:
        return _identity(directory, None, None, module, None)
    candidate = folder_id(root)
    with store.db.read() as connection:
        row = connection.execute(
            "SELECT project_id FROM projects WHERE project_id=? AND kind='folder' AND path=?",
            (candidate, root),
        ).fetchone()
    identity = _identity(directory, repository, root, module,
                         row["project_id"] if row is not None else None)
    if row is not None and identity['projectId'] == row['project_id']:
        with store.db.write() as connection:
            record(connection, identity)
    return identity


def find_for_board(store, cwd: str, *, module=workspace) -> dict | None:
    """Find an existing project without creating a marker or changing the board."""
    directory = _directory(cwd)
    repository, root = _source(directory, module)
    if repository is not None:
        identifier = module.read_project_id(repository)
        if identifier is not None:
            return {'projectId': identifier, 'projectKind': 'git', 'cwd': str(directory),
                    'projectPath': root, 'repositoryPath': repository}
    path = root if repository is not None else str(directory)
    with store.db.read() as connection:
        row = connection.execute("SELECT project_id FROM projects WHERE project_id=? AND kind='folder' AND path=?",
                                 (folder_id(path), path)).fetchone()
    if row is None:
        return None
    return {'projectId': row['project_id'], 'projectKind': 'folder', 'cwd': str(directory),
            'projectPath': path, 'repositoryPath': None}


def record(connection, identity: dict) -> None:
    """Keep current project facts; callers freeze kind on their own rows."""
    connection.execute(
        "INSERT INTO projects(project_id,kind,path) VALUES(?,?,?) "
        "ON CONFLICT(project_id) DO UPDATE SET kind=excluded.kind,path=excluded.path",
        (identity["projectId"], identity["projectKind"], identity["projectPath"]),
    )


def known_repository_paths(connection, identifier: str) -> tuple[str, ...]:
    """Current project location, followed by locations from the publication journal."""
    current = connection.execute("SELECT path FROM projects WHERE project_id=? AND kind='git'",
                                 (identifier,)).fetchone()
    located = []
    owner_join = (" JOIN manifest_owners o ON o.workspace_id=x.workspace_id AND o.manifest_sha256=x.digest"
                  " JOIN workflow_runs r ON r.run_id=o.owner_run_id"
                  " LEFT JOIN macro_tasks m ON m.macro_task_id=r.macro_task_id")
    candidates = (
        "SELECT a.rowid AS position,a.run_id,a.attempt_id,a.kind,a.workspace_id,a.input_manifest_sha256 AS digest"
        " FROM workflow_artifacts a WHERE a.kind IN ('input','turn-input')"
        " UNION ALL SELECT c.rowid,c.run_id,NULL,'continuation',c.workspace_id,c.workspace_manifest_sha256"
        " FROM workflow_continuations c WHERE c.workspace_id IS NOT NULL"
        " UNION ALL SELECT t.rowid,t.run_id,t.attempt_id,'receipt',t.workspace_id,t.workspace_manifest_sha256"
        " FROM workflow_turns t JOIN attempts a ON a.attempt_id=t.attempt_id WHERE a.result_json IS NOT NULL"
    )
    for row in connection.execute(
            f"WITH manifest_owners AS ({MANIFEST_OWNERS_CTE}), candidates AS ({candidates})"
            " SELECT x.*,o.repository_path FROM candidates x" + owner_join +
            " WHERE COALESCE(m.project_id,r.standalone_project_id)=?", (identifier,)):
        if row['kind'] == 'input':
            event = connection.execute("SELECT seq FROM events WHERE task_id=? AND kind IN ('task.submitted','workflow.helper_admitted') ORDER BY seq DESC LIMIT 1", (row['run_id'],)).fetchone()
        elif row['kind'] == 'continuation':
            event = connection.execute("SELECT seq FROM events WHERE task_id=? AND kind='workflow.workspace_prepared' AND json_extract(payload_json,'$.manifestSha256')=? ORDER BY seq DESC LIMIT 1", (row['run_id'],row['digest'])).fetchone()
        else:
            event = connection.execute("SELECT seq FROM events WHERE attempt_id=? AND kind IN ('execution.received','workflow.turn_returned','workflow.turn_failed','workflow.turn_rejected') ORDER BY seq DESC LIMIT 1", (row['attempt_id'],)).fetchone()
        if event is not None:
            located.append((event['seq'], row['position'], row['repository_path']))
    paths = [current["path"]] if current is not None else []
    paths.extend(path for _, _, path in sorted(located, reverse=True))
    return tuple(dict.fromkeys(paths))


def relocate(store, params: dict) -> dict:
    """Record a known Git project at its marked location, without touching executions."""
    directory = _directory(params["cwd"])
    repository, root = _source(directory, workspace)
    identifier = workspace.read_project_id(repository) if repository is not None else None
    if identifier is None:
        raise BoardError("PROJECT_MARKER_REQUIRED", "relocate requires an existing Git project marker; "
                         "a moved or renamed ordinary folder is a new project", cwd=str(directory))
    with store.db.write() as connection:
        previous = connection.execute("SELECT path FROM projects WHERE project_id=?", (identifier,)).fetchone()
        if previous is None:
            raise BoardError("NOT_FOUND", "The marked Git project is not recorded on this blackboard",
                             projectId=identifier)
        if previous["path"] == root:
            return {"project": view(connection, identifier)}
        record(connection, {"projectId": identifier, "projectKind": "git", "projectPath": root})
        store._append_event(connection, "project.relocated",
                            payload={"projectId": identifier, "path": root, "previousPath": previous["path"]})
        result = {"project": view(connection, identifier)}
        head = store._head_of(connection)
    store._notify(head)
    return result


def prune_if_unreferenced(connection, project_ids) -> None:
    """Discard projects whose last macro and standalone roots were removed."""
    for identifier in project_ids:
        deleted = connection.execute(
            "DELETE FROM projects WHERE project_id=?"
            " AND NOT EXISTS (SELECT 1 FROM macro_tasks WHERE project_id=?)"
            " AND NOT EXISTS (SELECT 1 FROM workflow_runs WHERE standalone_project_id=?)",
            (identifier, identifier, identifier),
        ).rowcount
        if deleted:
            connection.execute("DELETE FROM events WHERE kind LIKE 'project.%'"
                               " AND json_extract(payload_json,'$.projectId')=?", (identifier,))


def require_capture_identity(identity: dict, manifest: dict | None) -> None:
    """Fence a captured Git input against the project that determined its key."""
    if manifest is not None and project_id(manifest) != identity['projectId']:
        raise BoardError('WORKSPACE_CHANGED', 'The source project changed while capturing the input',
                         expectedProjectId=identity['projectId'], capturedProjectId=project_id(manifest))


def view(connection, project_id: str) -> dict:
    row = connection.execute("SELECT kind,path FROM projects WHERE project_id=?", (project_id,)).fetchone()
    if row is None:
        raise BoardError("NOT_FOUND", "Unknown project", projectId=project_id)
    path = row["path"]
    return {"id": project_id, "kind": row["kind"], "path": path,
            "label": label(path, None)}
