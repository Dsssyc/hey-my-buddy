"""Offline, atomic schema-15 identity normalization under upgrade's fences.

Immutable manifests, turn inputs, receipts and history never change. Exact
legacy-digest reconstruction proves the old directory inode, in addition to
fixed Git and allocation evidence. Unprovable or conflicting identities stay
recorded, with a persistent explanation. Several witnessed old devices may
name one stable directory; this is not an identity collision.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import closing
from pathlib import Path

from ..tasks import workspace
from ..tasks import workspace_identity as identity
from ...errors import BoardError
from .db import SCHEMA_VERSION

MUTATED_TABLES = frozenset({"meta", "objectives", "workspace_reservations"})
_RECORDED_COLUMNS = (
    ("workspace_reservations", "checkout_id"), ("workspace_reservations", "repository_id"),
    ("workflow_integrations", "target_checkout_id"), ("workflow_integrations", "target_repository_id"),
    ("workspace_cleanup_plans", "checkout_id"), ("workspace_cleanup_plans", "repository_id"),
    ("objectives", "project_id"),
)


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _manifests(connection):
    """Read fixed execution manifests without changing their bytes or hashes."""
    records = []
    for table in ("workflow_runs", "workflow_children", "workflow_continuations"):
        for row in connection.execute(f"SELECT * FROM {table} WHERE workspace_manifest_json IS NOT NULL"):
            try:
                value = json.loads(row["workspace_manifest_json"])
            except (ValueError, TypeError):
                continue
            if isinstance(value, dict):
                bound = row["workspace_manifest_sha256"] if "workspace_manifest_sha256" in row.keys() else None
                records.append((value, bound is None or bound == value.get("manifestSha256")))
    for row in connection.execute("SELECT * FROM workflow_artifacts"):
        try:
            value = json.loads(row["manifest_json"])
        except (ValueError, TypeError):
            continue
        if isinstance(value, dict):
            # Outputs are observed historical identities, not execution manifests.
            records.append((value, row["manifest_sha256"] == value.get("manifestSha256")))
    for row in connection.execute("SELECT input_json,input_sha256 FROM workflow_turns"):
        try:
            value = json.loads(row["input_json"]).get("executionWorkspace")
        except (ValueError, TypeError, AttributeError):
            continue
        if isinstance(value, dict):
            valid = hashlib.sha256(row["input_json"].encode()).hexdigest() == row["input_sha256"]
            records.append((value, valid))
    # Retain separate bindings: a corrupt row cannot mask a valid row merely by
    # claiming the same manifest hash. No output is promoted into input evidence.
    return records


def _anchor(directory, recorded, manifest, role):
    path = Path(directory).resolve(strict=True)
    current = identity.stable_identity(path)
    if recorded == current:
        return current, None
    device = identity.legacy_device(path, recorded)
    if device is None:
        raise BoardError("IDENTITY_UNPROVEN", "legacy-inode-unproven")
    return current, {"directory": str(path), "inode": path.stat().st_ino, "legacyDevice": device,
                     "manifestSha256": manifest["manifestSha256"], "workspaceId": manifest["workspaceId"],
                     "role": role}


def _fixed_refs(manifest):
    snapshot = manifest["snapshot"]
    repository = snapshot["repositoryPath"]
    for field, commit, tree in (("inputRef", manifest["inputCommit"], manifest["inputTree"]),
                               ("stagedRef", snapshot["stagedCommit"], snapshot["stagedTree"])):
        ref = snapshot[field]
        if ref != f"refs/buddy/workspaces/{manifest['workspaceId']}/" + ("input" if field == "inputRef" else "staged"):
            raise BoardError("IDENTITY_UNPROVEN", "fixed-refs-unverified")
        if workspace._git_in(repository, "symbolic-ref", "--quiet", ref, allowed=(0, 1)).strip():
            raise BoardError("IDENTITY_UNPROVEN", "fixed-refs-unverified")
        resolved = workspace._git_in(repository, "rev-parse", "--verify", "--end-of-options", ref).decode().strip()
        actual_tree = workspace._git_in(repository, "rev-parse", "--verify", "--end-of-options", commit + "^{tree}").decode().strip()
        if resolved != commit or actual_tree != tree:
            raise BoardError("IDENTITY_UNPROVEN", "fixed-refs-unverified")


def _proof(state, manifest, retained):
    """Prove each recorded identity before installing any aliases."""
    workspace._validate_manifest(manifest)
    if not workspace._workspace_identifier(manifest["workspaceId"]):
        raise BoardError("IDENTITY_UNPROVEN", "invalid-workspace-id")
    if not Path(manifest["path"]).is_dir():
        raise BoardError("IDENTITY_UNPROVEN", "path-missing")
    actual = workspace.inspect(manifest["path"])
    if actual["checkoutRoot"] != manifest["checkoutRoot"]:
        raise BoardError("IDENTITY_UNPROVEN", "checkout-root-changed")
    if actual["repositoryPath"] != manifest["snapshot"]["repositoryPath"]:
        raise BoardError("IDENTITY_UNPROVEN", "repository-path-changed")
    result = {}

    def collect(recorded, directory, value, role):
        new, anchor = _anchor(directory, recorded, value, role)
        entry = result.setdefault(recorded, (new, []))
        if anchor is not None and anchor not in entry[1]:
            entry[1].append(anchor)

    for role, directory in (("checkoutId", actual["gitDir"]), ("repositoryId", actual["repositoryPath"])):
        recorded = manifest[role]
        if not identity.digest(recorded):
            raise BoardError("IDENTITY_UNPROVEN", "invalid-recorded-identity")
        collect(recorded, directory, manifest, role)
    # The primary checkout can use one digest in both roles. Its mapping works
    # for every declared index column; no single-kind normalization switch.
    local = {old: new for old, (new, anchors) in result.items() if anchors}
    with identity.aliases(local):
        workspace.verify(manifest, require_unchanged=False)
    _fixed_refs(manifest)
    root = Path(actual["checkoutRoot"])
    managed = Path(state).resolve() / "workspaces"
    if root.is_relative_to(managed) or manifest["kind"] == "worktree":
        allocation = root.parent
        if root.name != "checkout" or allocation.parent != managed or allocation.is_symlink():
            raise BoardError("IDENTITY_UNPROVEN", "allocation-unproven")
        original = workspace._record(allocation / "manifest.json")
        if not isinstance(original, dict) or original not in retained or original.get("kind") != "worktree":
            raise BoardError("IDENTITY_UNPROVEN", "allocation-unproven")
        workspace._validate_manifest(original)
        if original["workspaceId"] != allocation.name or original["checkoutRoot"] != str(root):
            raise BoardError("IDENTITY_UNPROVEN", "allocation-unproven")
        for role, directory in (("checkoutId", actual["gitDir"]), ("repositoryId", actual["repositoryPath"])):
            _anchor(directory, original[role], original, role)
        _fixed_refs(original)
        registration = workspace._worktree_record(actual["repositoryPath"], root)
        if registration is None or registration.get("locked") != "buddy:" + allocation.name:
            raise BoardError("IDENTITY_UNPROVEN", "allocation-lock-unverified")
    # Preparation persisted the source inspection independently; its checksum
    # binding is the sourceCheckoutId in this fixed manifest, and its inode is
    # again proven by the exact legacy digest, not by trusting that disk path.
    pinned = workspace._record(Path(state) / "workspaces" / manifest["workspaceId"] / "input.json")
    source = pinned.get("source") if isinstance(pinned, dict) else None
    source_id = manifest["snapshot"].get("sourceCheckoutId")
    if isinstance(source, dict) and source.get("checkoutId") == source_id and identity.digest(source_id):
        try:
            source_actual = workspace.inspect(source["path"])
            if source_actual["repositoryPath"] == actual["repositoryPath"] and source_actual["checkoutRoot"] == source.get("checkoutRoot"):
                collect(source_id, source_actual["gitDir"], manifest, "sourceCheckoutId")
        except (OSError, BoardError):
            pass  # An absent source never invalidates a proved allocated checkout.
    return result


def _report(connection):
    row = connection.execute("SELECT value FROM meta WHERE key=?", (identity.MIGRATION_META_KEY,)).fetchone()
    try:
        value = json.loads(row[0]) if row else {}
        return value if isinstance(value, dict) else {}
    except (ValueError, TypeError):
        return {}


def _plan(connection, state):
    records = _manifests(connection)
    retained = [value for value, bound in records if bound]
    observed = set(identity.load_alias_map(connection))
    candidates, reasons = {}, {}
    previous = _report(connection)
    sticky = {old: reason for old, reason in previous.get("kept", {}).items() if reason == "path-missing"}
    proofs, denials = {}, {}
    for manifest, bound in records:
        ids = {manifest.get(field) for field in ("checkoutId", "repositoryId") if identity.digest(manifest.get(field))}
        source_id = manifest.get("snapshot", {}).get("sourceCheckoutId") if isinstance(manifest.get("snapshot"), dict) else None
        if identity.digest(source_id):
            ids.add(source_id)
        observed.update(ids)
        key = None
        try:
            if not bound:
                raise BoardError("IDENTITY_UNPROVEN", "record-binding-invalid")
            # Row bindings stay independent. Only identical complete manifests
            # are reused within this plan. Denials cannot authorize an alias;
            # a claimed hash alone is never a cache key, including for failures.
            key = _json(manifest)
            if key in denials:
                raise BoardError("IDENTITY_UNPROVEN", denials[key])
            if key not in proofs:
                proofs[key] = _proof(state, manifest, retained)
            proven = proofs[key]
        except (OSError, BoardError, KeyError, TypeError, ValueError) as error:
            reason = error.message if isinstance(error, BoardError) and error.code == "IDENTITY_UNPROVEN" else (
                "invalid-manifest" if isinstance(error, BoardError) and error.code in ("INVALID_WORKSPACE", "WORKSPACE_MANIFEST_CHANGED")
                else "fixed-evidence-unverified")
            if bound and key is not None:
                denials[key] = reason
            for old in ids:
                reasons.setdefault(old, set()).add(reason)
            continue
        for old, (new, anchors) in proven.items():
            if old in sticky:
                continue
            entry = candidates.setdefault(old, {})
            entry.setdefault(new, [])
            for anchor in anchors:
                if anchor not in entry[new]:
                    entry[new].append(anchor)
    for table, column in _RECORDED_COLUMNS:
        observed.update(row[0] for row in connection.execute(f"SELECT DISTINCT {column} FROM {table}") if identity.digest(row[0]))
    registered = identity.load_alias_map(connection)
    mapping, additions, kept = {}, {}, dict(sticky)
    proven_targets = {next(iter(choices)) for old, choices in candidates.items()
                      if len(choices) == 1 and old not in sticky
                      and (old not in registered or registered[old] == next(iter(choices)))}
    for old in sorted(observed):
        choices = candidates.get(old, {})
        if old in sticky:
            continue
        if len(choices) > 1:
            kept[old] = "identity-collision"
            continue
        if not choices and old in proven_targets:
            mapping[old] = old
            continue
        if not choices:
            kept[old] = sorted(reasons.get(old) or {"no-recorded-evidence"})[0]
            continue
        new, anchors = next(iter(choices.items()))
        if old in registered and registered[old] != new:
            kept[old] = "mapping-conflict"
            continue
        mapping[old] = new
        if old != new and old not in registered:
            additions[old] = {"version": 1, "newId": new, "source": "fixed-input-and-inode", "anchors": anchors}
    # Do not silently merge already competing held writer rows. Preserve their
    # old checkout identities and explain the unresolved exclusion conflict.
    writers = {}
    for row in connection.execute("SELECT checkout_id FROM workspace_reservations WHERE state='held' AND access='write'"):
        old = row[0]
        writers.setdefault(mapping.get(old, registered.get(old, old)), []).append(old)
    for values in writers.values():
        if len(values) > 1:
            for old in values:
                mapping.pop(old, None)
                # Keep the proved alias fact so current/old queries see all
                # competing holders, while their mutable index stays old.
                kept[old] = "reservation-conflict"
    # Never overwrite a preexisting malformed/unsupported mapping by fiat.
    for old in list(additions):
        if connection.execute("SELECT 1 FROM meta WHERE key=?", (identity.META_KEY_PREFIX + old,)).fetchone():
            additions.pop(old)
            mapping.pop(old, None)
            kept[old] = "mapping-conflict"
    return mapping, additions, kept, registered


def _rows(connection):
    """Exact inventories, including workers/events that backup may exempt."""
    return {row[0]: [tuple(item) for item in connection.execute('SELECT * FROM "' + row[0].replace('"', '""') + '"')]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def _inventory(rows):
    return sorted(json.dumps(row, ensure_ascii=False, separators=(",", ":"), default=lambda value: value.hex()) for row in rows)


def migrate_workspace_identity(state: Path, before: dict) -> tuple[dict, dict]:
    from . import backup

    state = Path(state).resolve()
    with closing(sqlite3.connect(state / "board.sqlite3", isolation_level=None, timeout=10)) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        # Git evidence is gathered before the SQLite write transaction. Upgrade
        # owns the maintenance fences; the transaction rechecks every row before
        # applying the precomputed plan, and current inode facts are checked again.
        preflight_rows = _rows(connection)
        mapping, additions, kept, registered = _plan(connection, state)
        connection.execute("BEGIN IMMEDIATE")
        try:
            schema = connection.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
            if schema is None or int(schema[0]) != SCHEMA_VERSION:
                raise BoardError("UPGRADE_MIGRATION_FAILED", "Workspace identity migration requires schema 15")
            baseline = backup.database_snapshot(connection)
            if any(before.get(key) != baseline[key] for key in ("tables", "fingerprints", "eventHead")):
                raise BoardError("UPGRADE_MIGRATION_FAILED", "Identity migration no longer matches its verified backup")
            rows_before = _rows(connection)
            if any(_inventory(rows) != _inventory(rows_before.get(table, [])) for table, rows in preflight_rows.items()) or set(preflight_rows) != set(rows_before):
                raise BoardError("UPGRADE_MIGRATION_FAILED", "Identity proof inputs changed before migration")
            for entry in additions.values():
                for anchor in entry["anchors"]:
                    directory = Path(anchor["directory"])
                    if str(directory.resolve(strict=True)) != anchor["directory"] or directory.stat().st_ino != anchor["inode"]:
                        raise BoardError("UPGRADE_MIGRATION_FAILED", "Identity anchor changed after proof")
            schema_before = [tuple(row) for row in connection.execute("SELECT * FROM sqlite_master ORDER BY type,name")]
            counts = {"objectives": 0, "reservations": 0}
            planned = {table: list(rows) for table, rows in rows_before.items()}
            for table, columns in (("objectives", ("project_id",)), ("workspace_reservations", ("checkout_id", "repository_id"))):
                names = [row[1] for row in connection.execute(f"PRAGMA table_info({table})")]
                pk = names[0]
                for offset, original in enumerate(rows_before[table]):
                    changed = list(original)
                    for column in columns:
                        index = names.index(column)
                        changed[index] = mapping.get(original[index], original[index])
                    planned[table][offset] = tuple(changed)
                    if tuple(changed) != original:
                        counts["objectives" if table == "objectives" else "reservations"] += 1
                        connection.execute(f"UPDATE {table} SET " + ",".join(column + "=?" for column in columns) + f" WHERE {pk}=?",
                                           (*[changed[names.index(column)] for column in columns], original[0]))
            meta = dict(rows_before["meta"])
            for old, value in sorted(additions.items()):
                key, encoded = identity.META_KEY_PREFIX + old, _json(value)
                meta[key] = encoded
                connection.execute("INSERT INTO meta(key,value) VALUES(?,?)", (key, encoded))
            report = {"version": 1, "mapped": sorted({**registered, **additions, **{old: new for old, new in mapping.items() if old != new}}),
                      "kept": dict(sorted(kept.items())),
                      "entries": {old: meta[identity.META_KEY_PREFIX + old] for old in sorted({**registered, **additions})}}
            encoded = _json(report)
            if meta.get(identity.MIGRATION_META_KEY) != encoded:
                meta[identity.MIGRATION_META_KEY] = encoded
                connection.execute("INSERT INTO meta(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                                   (identity.MIGRATION_META_KEY, encoded))
            planned["meta"] = list(meta.items())
            actual = _rows(connection)
            changed = [table for table in planned if _inventory(planned[table]) != _inventory(actual.get(table, []))]
            if changed or set(planned) != set(actual) or schema_before != [tuple(row) for row in connection.execute("SELECT * FROM sqlite_master ORDER BY type,name")]:
                raise BoardError("UPGRADE_MIGRATION_FAILED", "Identity migration changed data outside its exact plan", tables=changed)
            expected = {**before, **backup.database_snapshot(connection, event_head=before["eventHead"])}
            connection.execute("COMMIT")
        except BaseException:
            connection.execute("ROLLBACK")
            raise
    return {**report, "normalizedObjectives": counts["objectives"], "normalizedReservations": counts["reservations"]}, expected
