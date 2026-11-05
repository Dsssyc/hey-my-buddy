"""Project identity at a source directory and its current board record."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path

from ...errors import BoardError
from . import workspace


def folder_id(path: str) -> str:
    """The stable identity of a folder at its canonical absolute path."""
    return hashlib.sha256(("hey-my-buddy:folder-project:v1\0" + os.path.normcase(path))
                          .encode("utf-8")).hexdigest()


def _directory(cwd: str) -> Path:
    try:
        directory = Path(cwd).resolve(strict=True)
        if not directory.is_dir():
            raise BoardError("WORKSPACE_UNAVAILABLE", "cwd is not a directory", cwd=cwd)
    except OSError as error:
        raise BoardError("WORKSPACE_UNAVAILABLE", "The cwd directory is unavailable", cwd=cwd,
                         reason=str(error)) from error
    return directory


def _source(directory: Path, module):
    repository = module.repository_at(directory, allow_parent=True)
    return (repository, module.project_root(directory) if repository is not None else None)


def _identity(directory: Path, repository, root, module, folder_project_id):
    path = str(directory)
    if repository is None:
        return {"projectId": folder_id(path), "projectKind": "folder", "cwd": path,
                "projectPath": path, "repositoryPath": None}
    identifier = module.ensure_project_id(repository, preferred=folder_project_id)
    return {"projectId": identifier, "projectKind": "git", "cwd": path, "projectPath": root,
            "repositoryPath": repository}


def identify(cwd: str, *, module=workspace, folder_project_id: str | None = None) -> dict:
    """Resolve a source project before deriving a creation key."""
    directory = _directory(cwd)
    repository, root = _source(directory, module)
    return _identity(directory, repository, root, module, folder_project_id)


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
    return _identity(directory, repository, root, module,
                     row["project_id"] if row is not None else None)


def record(connection, identity: dict) -> None:
    """Keep current project facts; callers freeze kind on their own rows."""
    connection.execute(
        "INSERT INTO projects(project_id,kind,path) VALUES(?,?,?) "
        "ON CONFLICT(project_id) DO UPDATE SET kind=excluded.kind,path=excluded.path",
        (identity["projectId"], identity["projectKind"], identity["projectPath"]),
    )


def view(connection, project_id: str) -> dict:
    row = connection.execute("SELECT kind,path FROM projects WHERE project_id=?", (project_id,)).fetchone()
    if row is None:
        raise BoardError("NOT_FOUND", "Unknown project", projectId=project_id)
    path = row["path"]
    return {"id": project_id, "kind": row["kind"], "path": path,
            "label": Path(path).name or path}
