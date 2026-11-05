"""Project identity at a source directory and its current board record."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path

from ...errors import BoardError
from ...json_codec import canonical_json
from ...protocol.workspace_manifest import project_id
from . import workspace


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


def prune_if_unreferenced(connection, project_ids) -> None:
    """Discard projects whose last macro and standalone roots were removed."""
    for identifier in project_ids:
        connection.execute(
            "DELETE FROM projects WHERE project_id=?"
            " AND NOT EXISTS (SELECT 1 FROM macro_tasks WHERE project_id=?)"
            " AND NOT EXISTS (SELECT 1 FROM workflow_runs WHERE standalone_project_id=?)",
            (identifier, identifier, identifier),
        )


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
