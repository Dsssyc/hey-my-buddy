"""The explicit, offline schema-25 to schema-26 board conversion.

The installer calls this only after fencing the old service and backing up its
board. Git input and output commits, rather than a mutable checkout, supply the
complete per-path output binding. Historical execution evidence is left intact.
"""
from __future__ import annotations

from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import tempfile

from ... import private_dirs
from ...errors import BoardError
from ...json_codec import canonical_json, decode_strict_json
from .db import SCHEMA_VERSION


BASE_SCHEMA_VERSION = 25
def _digest(value: object) -> str:
    # Workspace snapshot digests use ASCII escaping, unlike board JSON columns.
    raw = json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(raw).hexdigest()


def _refuse(row: sqlite3.Row, reason: str, **details) -> BoardError:
    return BoardError("UPGRADE_ARTIFACT_UNRECOVERABLE",
                      "A stored workspace artifact cannot be converted from pinned Git objects",
                      artifactId=row["artifact_id"], runId=row["run_id"],
                      artifactKind=row["kind"], reason=reason, **details)


def _git(repository: Path, row: sqlite3.Row, *args: str) -> bytes:
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    env.update(GIT_OPTIONAL_LOCKS="0", GIT_NO_REPLACE_OBJECTS="1", GIT_NO_LAZY_FETCH="1", LC_ALL="C")
    try:
        result = subprocess.run(
            ["git", "-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false",
             "--git-dir", str(repository), *args], capture_output=True, env=env, timeout=60,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise _refuse(row, "git-unavailable", operation=args[0], detail=str(error)[:200]) from error
    if result.returncode:
        raise _refuse(row, "git-object-unavailable", operation=args[0],
                      detail=result.stderr.decode(errors="replace")[-200:])
    return result.stdout


def _entries(repository: Path, row: sqlite3.Row, input_commit: str, output_commit: str) -> tuple[list[str], dict]:
    for commit in (input_commit, output_commit):
        if not isinstance(commit, str) or len(commit) not in (40, 64) or any(c not in "0123456789abcdef" for c in commit):
            raise _refuse(row, "invalid-commit-id", commit=commit)
        if _git(repository, row, "cat-file", "-t", commit) != b"commit\n":
            raise _refuse(row, "git-object-not-commit", commit=commit)
    changed_raw = _git(repository, row, "diff-tree", "-r", "--no-renames", "--name-only", "-z",
                       input_commit, output_commit, "--")
    changed = sorted(os.fsdecode(path) for path in changed_raw.split(b"\0") if path)
    entries = {}
    for record in _git(repository, row, "ls-tree", "-rz", output_commit).split(b"\0"):
        if not record:
            continue
        metadata, path = record.split(b"\t", 1)
        mode, kind, oid = metadata.decode().split()
        if kind == "tree":
            continue
        if mode not in ("100644", "100755", "120000") or kind != "blob":
            raise _refuse(row, "unsupported-git-entry", path=os.fsdecode(path), mode=mode)
        entries[os.fsdecode(path)] = [mode, oid]
    return changed, {path: entries.get(path) for path in changed}


def _record_paths(state: Path, row: sqlite3.Row, artifact: dict) -> list[Path]:
    diff_path = artifact.get("diffPath")
    if not isinstance(diff_path, str) or not Path(diff_path).is_absolute():
        raise _refuse(row, "missing-record-path")
    directory = Path(diff_path).parent
    if not directory.is_relative_to(state / "workspaces") or not directory.resolve().is_relative_to(state / "workspaces"):
        raise _refuse(row, "record-outside-state", path=str(directory))
    for parent in (directory, *directory.parents):
        if parent == state:
            break
        if private_dirs.linked(parent):
            raise _refuse(row, "unsafe-physical-record", path=str(parent))
    if row["kind"] in ("output", "partial-output"):
        names = ("pending.json", "output.json")
    elif row["kind"] == "resolved-output":
        action = artifact.get("action")
        if action not in ("restore", "adopt"):
            raise _refuse(row, "unknown-resolution-action", action=action)
        names = (f"resolve-{action}-pending.json", f"resolve-{action}.json")
    else:
        names = ("abandoned-pending.json", "abandoned.json")
    paths = [directory / name for name in names if (directory / name).exists()]
    for path in paths:
        if (private_dirs.linked(path) or not path.is_file()
                or not path.resolve(strict=True).is_relative_to(state / "workspaces")):
            raise _refuse(row, "unsafe-physical-record", path=str(path))
    return paths


def _convert_row(state: Path, row: sqlite3.Row, repository: Path | None) -> tuple[str, str, list[tuple[Path, bytes]]]:
    try:
        artifact = decode_strict_json(row["manifest_json"])
    except (ValueError, RecursionError) as error:
        raise _refuse(row, "invalid-manifest-json", detail=str(error)[:200]) from error
    if not isinstance(artifact, dict) or not isinstance(artifact.get("snapshot"), dict):
        raise _refuse(row, "missing-snapshot")
    snapshot = artifact["snapshot"]
    old_hash = artifact.get("snapshotSha256")
    if old_hash != row["manifest_sha256"] or old_hash != _digest(snapshot):
        raise _refuse(row, "snapshot-digest-mismatch")
    if artifact.get("inputCommit") != snapshot.get("inputCommit") or artifact.get("commit") != snapshot.get("commit"):
        raise _refuse(row, "commit-binding-mismatch")
    old_entries = snapshot.get("changedEntries")
    paths = snapshot.get("changedPaths")
    if (not isinstance(paths, list) or paths != sorted(set(paths))
            or artifact.get("changedPaths") != paths or not isinstance(old_entries, dict)
            or not set(old_entries).issubset(paths)):
        raise _refuse(row, "changed-entries-mismatch")
    if not isinstance(snapshot.get("changedEntriesTruncated"), bool):
        raise _refuse(row, "missing-truncation-marker")
    if snapshot["changedEntriesTruncated"]:
        if repository is None or not repository.is_dir():
            raise _refuse(row, "missing-input-repository")
        output_tree = _git(repository, row, "rev-parse", artifact["commit"] + "^{tree}").decode().strip()
        if output_tree != artifact.get("tree") or output_tree != snapshot.get("tree"):
            raise _refuse(row, "output-tree-mismatch")
        if artifact["commit"] != artifact["inputCommit"]:
            parents = _git(repository, row, "rev-list", "--parents", "-n", "1", artifact["commit"]).decode().split()
            if parents != [artifact["commit"], artifact["inputCommit"]]:
                raise _refuse(row, "output-parent-mismatch")
        changed, entries = _entries(repository, row, artifact["inputCommit"], artifact["commit"])
        if paths != changed:
            raise _refuse(row, "changed-paths-mismatch")
        if any(old_entries[path] != entries[path] for path in old_entries):
            raise _refuse(row, "changed-entries-mismatch")
    else:
        if set(old_entries) != set(paths):
            raise _refuse(row, "incomplete-untruncated-entries")
        entries = old_entries
    physical_updates = []
    for path in _record_paths(state, row, artifact):
        try:
            physical = decode_strict_json(path.read_bytes())
        except (OSError, ValueError, RecursionError) as error:
            raise _refuse(row, "invalid-physical-record", path=str(path), detail=str(error)[:200]) from error
        if (not isinstance(physical, dict) or physical.get("snapshot") != snapshot
                or physical.get("snapshotSha256") != old_hash
                or any(physical.get(key) != artifact.get(key) for key in
                       ("workspaceId", "taskId", "attemptId", "manifestSha256", "inputCommit", "commit", "tree", "ref", "diffSha256"))):
            raise _refuse(row, "physical-record-mismatch", path=str(path))
        physical["snapshot"]["changedEntries"] = entries
        del physical["snapshot"]["changedEntriesTruncated"]
        physical["snapshotSha256"] = _digest(physical["snapshot"])
        physical_updates.append((path, json.dumps(physical, ensure_ascii=True, sort_keys=True,
                                                  separators=(",", ":")).encode()))
    snapshot["changedEntries"] = entries
    del snapshot["changedEntriesTruncated"]
    artifact["snapshotSha256"] = _digest(snapshot)
    return canonical_json(artifact), artifact["snapshotSha256"], physical_updates


def upgrade_schema_25_to_26(state: Path) -> dict:
    """Convert one stopped schema-25 board; refuse before writes on bad evidence.

    The caller owns the service/board locks, an exact schema-25 backup, rollback
    on failure, and the upgrade journal. This function never starts a service.
    """
    state = Path(state).resolve(strict=True)
    with closing(sqlite3.connect(state / "board.sqlite3")) as connection:
        connection.row_factory = sqlite3.Row
        version = connection.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
        if version is None or version[0] != str(BASE_SCHEMA_VERSION):
            raise BoardError("UNSUPPORTED_SCHEMA", f"Explicit conversion requires schema {BASE_SCHEMA_VERSION}",
                             found=None if version is None else version[0])
        inputs = {}
        for row in connection.execute("SELECT manifest_json FROM workflow_artifacts WHERE kind='input'"):
            manifest = decode_strict_json(row[0])
            inputs[manifest["manifestSha256"]] = Path(manifest["snapshot"]["repositoryPath"])
        conversions = []
        for row in connection.execute("SELECT artifact_id,run_id,kind,manifest_json,manifest_sha256 "
                                      "FROM workflow_artifacts WHERE kind IN ('output','resolved-output',"
                                      "'partial-output','abandoned-site') ORDER BY artifact_id"):
            artifact = decode_strict_json(row["manifest_json"])
            repository = inputs.get(artifact.get("manifestSha256"))
            converted, digest, physical_updates = _convert_row(state, row, repository)
            conversions.append((row["artifact_id"], converted, digest, physical_updates))
        if connection.execute("SELECT COUNT(*) FROM workflow_artifacts WHERE kind NOT IN "
                              "('input','output','resolved-output','partial-output','abandoned-site','shared-refs')").fetchone()[0]:
            raise BoardError("UPGRADE_ARTIFACT_UNRECOVERABLE", "Unknown artifact kind in schema-25 board",
                             reason="unknown-artifact-kind")
        # All evidence is checked before the first mutation. The installer's
        # verified backup and journal cover interruption between file and DB writes.
        for _artifact_id, _converted, _digest_value, physical_updates in conversions:
            for path, data in physical_updates:
                _replace_record(path, data)
        connection.execute("BEGIN IMMEDIATE")
        try:
            for artifact_id, converted, digest, _physical_updates in conversions:
                connection.execute("UPDATE workflow_artifacts SET manifest_json=?,manifest_sha256=? WHERE artifact_id=?",
                                   (converted, digest, artifact_id))
            connection.execute("UPDATE meta SET value=? WHERE key='schema_version'", (str(SCHEMA_VERSION),))
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
    return {"fromSchema": BASE_SCHEMA_VERSION, "schema": SCHEMA_VERSION,
            "convertedArtifacts": len(conversions)}


def _replace_record(path: Path, data: bytes) -> None:
    descriptor, temporary = tempfile.mkstemp(prefix=".upgrade-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def restore_schema_25_physical_records(state: Path) -> int:
    """Rebind existing physical records to a restored schema-25 backup board.

    Call after backup.restore, under the installer's owner lock, before starting
    the old runtime. This restores only records the conversion had changed.
    """
    state = Path(state).resolve(strict=True)
    restored = 0
    with closing(sqlite3.connect(state / "board.sqlite3")) as connection:
        connection.row_factory = sqlite3.Row
        version = connection.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
        if version is None or version[0] != str(BASE_SCHEMA_VERSION):
            raise BoardError("UNSUPPORTED_SCHEMA", "Physical rollback requires restored schema 25")
        for row in connection.execute("SELECT artifact_id,run_id,kind,manifest_json,manifest_sha256 "
                                      "FROM workflow_artifacts WHERE kind IN ('output','resolved-output',"
                                      "'partial-output','abandoned-site') ORDER BY artifact_id"):
            artifact = decode_strict_json(row["manifest_json"])
            for path in _record_paths(state, row, artifact):
                physical = decode_strict_json(path.read_bytes())
                if not isinstance(physical, dict) or any(physical.get(key) != artifact.get(key) for key in
                    ("workspaceId", "taskId", "attemptId", "manifestSha256", "inputCommit", "commit", "tree", "ref")):
                    raise _refuse(row, "rollback-record-mismatch", path=str(path))
                physical["snapshot"] = artifact["snapshot"]
                physical["snapshotSha256"] = artifact["snapshotSha256"]
                _replace_record(path, json.dumps(physical, ensure_ascii=True, sort_keys=True,
                                                separators=(",", ":")).encode())
                restored += 1
    return restored
