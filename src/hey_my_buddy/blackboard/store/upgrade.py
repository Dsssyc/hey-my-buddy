"""Explicit offline schema-25 to schema-26 conversion of saved seal records."""
from __future__ import annotations

from contextlib import closing
import os
from pathlib import Path
import sqlite3
import tempfile

from ... import private_dirs
from ...blackboard.tasks import workspace
from ...errors import BoardError
from ...json_codec import canonical_json, decode_strict_json
from ...protocol.execution import RECEIPT
from .db import SCHEMA_VERSION

BASE_SCHEMA_VERSION = 25


def _invalid(message: str, **details) -> BoardError:
    return BoardError("UPGRADE_INVALID_SEAL", message, **details)


def _seal(value: object, identities: dict[str, str]) -> bool:
    """Convert snapshots and their structured output-identity bindings."""
    changed = False
    if isinstance(value, dict):
        snapshot = value.get("snapshot")
        if isinstance(snapshot, dict) and ("changedEntries" in snapshot or "changedEntriesTruncated" in snapshot):
            old = value["snapshotSha256"]
            snapshot.pop("changedEntries", None)
            snapshot.pop("changedEntriesTruncated", None)
            value["snapshotSha256"] = identities[old]
            changed = True
        for key, item in value.items():
            if key in ("snapshotSha256", "manifestSha256") and isinstance(item, str) and item in identities:
                value[key] = identities[item]
                changed = True
            elif isinstance(item, (dict, list)):
                changed = _seal(item, identities) or changed
    elif isinstance(value, list):
        for item in value:
            if isinstance(item, (dict, list)):
                changed = _seal(item, identities) or changed
    return changed


def _discover(value: object, identities: dict[str, str], location: str) -> None:
    if isinstance(value, dict):
        snapshot = value.get("snapshot")
        if isinstance(snapshot, dict) and ("changedEntries" in snapshot or "changedEntriesTruncated" in snapshot):
            old = value.get("snapshotSha256")
            if old != workspace._sha(workspace._json(snapshot)):
                raise _invalid("Stored seal digest does not match", location=location)
            changed = dict(snapshot)
            changed.pop("changedEntries", None)
            changed.pop("changedEntriesTruncated", None)
            new = workspace._sha(workspace._json(changed))
            if old in identities and identities[old] != new:
                raise _invalid("Stored seal identity is ambiguous", location=location)
            identities[old] = new
        for item in value.values():
            if isinstance(item, (dict, list)):
                _discover(item, identities, location)
    elif isinstance(value, list):
        for item in value:
            if isinstance(item, (dict, list)):
                _discover(item, identities, location)


def _physical_paths(state: Path) -> list[Path]:
    candidates = [*(state / "workspaces").glob("*/outputs/*/*.json"),
                  *(state / "harnesses").glob(f"*/micro-tasks/*/attempts/*/{RECEIPT}")]
    for path in candidates:
        relative = path.relative_to(state).parts
        if (not ((len(relative) == 5 and relative[0] == "workspaces" and relative[2] == "outputs")
                 or (len(relative) == 7 and relative[0] == "harnesses" and relative[2] == "micro-tasks"
                     and relative[4] == "attempts" and relative[6] == RECEIPT))
                or private_dirs.linked_component(path) is not None or not path.is_file()):
            raise _invalid("Physical seal path is linked or nonregular", path=str(path))
    return sorted(candidates)


def plan_schema_25_to_26(state: Path) -> dict:
    """Check all DB/physical bindings and compute every write before any mutation."""
    state = Path(state).absolute()
    if private_dirs.linked_component(state) is not None:
        raise _invalid("State path contains a link", path=str(state))
    with closing(sqlite3.connect((state / "board.sqlite3").as_uri() + "?mode=ro", uri=True)) as connection:
        connection.row_factory = sqlite3.Row
        version = connection.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
        if version is None or version[0] != str(BASE_SCHEMA_VERSION):
            raise BoardError("UNSUPPORTED_SCHEMA", f"Explicit conversion requires schema {BASE_SCHEMA_VERSION}",
                             found=None if version is None else version[0])
        identities = {}
        tables = [row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
        for table in tables:
            json_columns = [row[1] for row in connection.execute(f'PRAGMA table_info("{table}")') if row[1].endswith("_json")]
            for row in connection.execute(f'SELECT rowid AS _upgrade_rowid,* FROM "{table}"'):
                for column in json_columns:
                    if row[column] is not None:
                        _discover(decode_strict_json(row[column]), identities,
                                  f"{table}:{row['_upgrade_rowid']}:{column}")
        physical_source = {}
        for path in _physical_paths(state):
            original = path.read_bytes()
            value = decode_strict_json(original)
            _discover(value, identities, str(path))
            physical_source[path] = (original, value)

        for row in connection.execute("SELECT artifact_id,manifest_json,manifest_sha256 FROM workflow_artifacts"):
            artifact = decode_strict_json(row["manifest_json"])
            if not isinstance(artifact, dict) or not isinstance(artifact.get("snapshot"), dict):
                continue
            snapshot = artifact["snapshot"]
            if "changedEntries" not in snapshot and "changedEntriesTruncated" not in snapshot:
                continue
            old = artifact["snapshotSha256"]
            if row["manifest_sha256"] != old:
                raise _invalid("Artifact row and seal identity disagree", artifactId=row["artifact_id"])
            diff = artifact.get("diffPath")
            if not isinstance(diff, str) or not Path(diff).is_absolute() or ".." in Path(diff).parts:
                raise _invalid("Artifact has no safe diff path", artifactId=row["artifact_id"])
            patch = Path(diff)
            if not patch.is_relative_to(state / "workspaces"):
                raise _invalid("Artifact diff path lies outside private workspaces", artifactId=row["artifact_id"])
            stem = patch.stem
            names = (stem + ".json", "pending.json" if stem == "output" else stem + "-pending.json")
            for name in names:
                record = physical_source.get(patch.with_name(name))
                if record is not None and (not isinstance(record[1], dict) or record[1].get("snapshotSha256") != old):
                    raise _invalid("Artifact and physical seal identities disagree",
                                   artifactId=row["artifact_id"], path=str(patch.with_name(name)))

        artifacts = 0
        updates = []
        for table in tables:
            columns = [row[1] for row in connection.execute(f'PRAGMA table_info("{table}")')]
            json_columns = [name for name in columns if name.endswith("_json")]
            if not json_columns:
                continue
            for row in connection.execute(f'SELECT rowid AS _upgrade_rowid,* FROM "{table}"'):
                changes = {}
                for column in json_columns:
                    raw = row[column]
                    if raw is None:
                        continue
                    value = decode_strict_json(raw)
                    if _seal(value, identities):
                        changes[column] = canonical_json(value)
                if table == "workflow_artifacts" and "manifest_json" in changes:
                    artifact = decode_strict_json(changes["manifest_json"])
                    old = row["manifest_sha256"]
                    if old in identities and artifact.get("snapshotSha256") == identities[old]:
                        changes["manifest_sha256"] = identities[old]
                        artifacts += 1
                if table == "workflow_turns" and "input_json" in changes and row["input_sha256"] is not None:
                    changes["input_sha256"] = workspace._sha(changes["input_json"].encode())
                if changes:
                    updates.append((table, row["_upgrade_rowid"], changes))
        physical = {}
        for path, (original, value) in physical_source.items():
            if _seal(value, identities):
                physical[path] = (original, workspace._json(value))
    return {"updates": updates, "physical": physical, "identities": identities,
            "artifactCount": artifacts}


def upgrade_schema_25_to_26(state: Path, *, plan: dict) -> dict:
    """Apply a validated plan after backup.current captures its original files."""
    state = Path(state).absolute()
    from . import backup
    saved = backup.verify(state / "backups" / "current")
    expected = {str(path.relative_to(state)) for path in plan["physical"]}
    saved_physical = {str(Path(name).relative_to("state")) for name in saved["files"]
                      if Path(name).is_relative_to("state") and backup._upgrade_physical_file(Path(name).relative_to("state"))}
    if saved_physical != expected:
        raise _invalid("Backup lacks a planned physical record")
    for path, (original, _) in plan["physical"].items():
        if private_dirs.linked_component(path) is not None or path.read_bytes() != original:
            raise _invalid("Physical record changed after planning", path=str(path))
    for path, (_, converted) in plan["physical"].items():
        _replace_record(path, converted)
    with closing(sqlite3.connect(state / "board.sqlite3")) as connection:
        connection.execute("BEGIN IMMEDIATE")
        try:
            for table, rowid, changes in plan["updates"]:
                fields = ",".join('"' + column + '"=?' for column in changes)
                connection.execute(f'UPDATE "{table}" SET {fields} WHERE rowid=?', (*changes.values(), rowid))
            connection.execute("UPDATE meta SET value=? WHERE key='schema_version'", (str(SCHEMA_VERSION),))
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
    return {"fromSchema": BASE_SCHEMA_VERSION, "schema": SCHEMA_VERSION,
            "convertedArtifacts": plan["artifactCount"], "physicalRecords": len(plan["physical"])}


def _replace_record(path: Path, data: bytes) -> None:
    from .backup import sync_dir
    descriptor, temporary = tempfile.mkstemp(prefix=".upgrade-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        sync_dir(path.parent)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
