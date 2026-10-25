"""Restore the fixed schema-29 stock; private binding precedes any cutover.

No old-source imports, generator, daemon, account or model is used here. The
returned board is the preservation baseline, including its rebound base-format
identities and digests. This module performs fixture preparation, not migration.
"""
from __future__ import annotations

from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import tempfile

FIXTURE = Path(__file__).resolve().parents[3] / "fixtures/upgrade-base"


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _json(value, *, ascii_json=False) -> str:
    return json.dumps(value, ensure_ascii=ascii_json, sort_keys=True, separators=(",", ":"))


def _git(root: Path, *args: str, data=None) -> str:
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    return subprocess.run(["git", "-c", "core.hooksPath=/dev/null", "-C", str(root), *args],
                          input=data, capture_output=True, check=True, env=env).stdout.decode("utf-8").strip()


def load_fixture(state: Path) -> dict:
    """Materialize in an absent/empty private state; return metadata + bindings.

    ``state`` and ``fixtureRoot`` are Paths; ``operations`` is the rebound old
    operation log; ``bindings`` maps template-era identities/digests to restored
    ones. Other keys are the fixed metadata (including stableIds/sourceCommit).
    Schema remains 29. Take the preservation baseline only after this returns.
    """
    metadata = json.loads((FIXTURE / "metadata.json").read_text(encoding="utf-8"))
    stock = {}
    for name, key in (("board.sql", "sqlSha256"), ("operations.json", "operationsSha256"),
                      ("catalog.json", "catalogSha256"), ("workspace-resources.json", "resourcesSha256")):
        data = (FIXTURE / name).read_bytes()
        if _sha(data) != metadata[key]:
            raise ValueError(f"Fixed fixture digest changed: {name}")
        stock[name] = data.decode("utf-8")
    resources = json.loads(stock["workspace-resources.json"])
    if metadata["format"] != 2 or resources["format"] != 1:
        raise ValueError("Unsupported fixture format")
    state = Path(state)
    if state.is_symlink() or (state.exists() and (not state.is_dir() or any(state.iterdir()))):
        raise ValueError("Fixture destination must be an absent or empty private directory")
    state = state.resolve()
    from hey_my_buddy.private_dirs import ensure_private_dir
    ensure_private_dir(state)
    fixture_root = state / "fixture-root"
    repository = fixture_root / "repository"
    repository.mkdir(parents=True)
    _git(repository, "init", "--quiet")
    # One hash-object invocation per object type writes the whole fixed stock and
    # reports one oid per input line, in order; each is still verified against
    # the fixture's recorded oid.
    with tempfile.TemporaryDirectory(prefix="upgrade-fixture-objects-") as scratch:
        grouped: dict[str, list[Path]] = {}
        for item in resources["gitObjects"]:
            path = Path(scratch) / item["oid"]
            path.write_bytes(bytes.fromhex(item["hex"]))
            grouped.setdefault(item["type"], []).append(path)
        for kind, paths in grouped.items():
            listing = "".join(f"{path}\n" for path in paths)
            actual = _git(repository, "hash-object", "-w", "-t", kind, "--stdin-paths", data=listing.encode("utf-8"))
            written = actual.splitlines()
            if len(written) != len(paths):
                raise ValueError(f"hash-object reported {len(written)} oids for {len(paths)} objects")
            for path, oid in zip(paths, written):
                if oid != path.name:
                    raise ValueError(f"Fixed Git object changed: {path.name}")
    commit = resources["workspaceInputCommit"]
    (repository / ".git/HEAD").write_text(commit + "\n", encoding="utf-8", newline="\n")
    _git(repository, "read-tree", commit)
    _git(repository, "checkout-index", "--all")
    bindings = {}

    def replace(text, *, ascii_json=False, json_payload=False):
        def path(value):
            return json.dumps(str(value), ensure_ascii=ascii_json)[1:-1] if json_payload else str(value)

        paths = {"<fixture-root>/state": path(state), "<fixture-root>": path(fixture_root)}
        # Substitute each source token once; a private directory name can itself
        # contain a placeholder or a hex digest without being interpreted again.
        return re.sub(r"<fixture-root>/state|<fixture-root>|[0-9a-f]{64}",
                      lambda match: paths.get(match[0], bindings.get(match[0], match[0])), text)

    for template in resources["checkouts"].values():
        _git(repository, "worktree", "add", "--quiet", "--detach", replace(template), commit)
    for old, template in resources["identities"].items():
        directory = Path(replace(template)).resolve(strict=True)
        facts = directory.stat()
        # The released base uses stable path/inode identity.
        bindings[old] = _sha(_json([str(directory), facts.st_ino], ascii_json=True).encode())
    for recipe in resources["hashBindings"]:
        payload = replace(recipe["template"], ascii_json=recipe["asciiJson"], json_payload=True)
        bindings[recipe["oldSha256"]] = _sha(payload.encode())

    def bind(value):
        if isinstance(value, str):
            return replace(value)
        if isinstance(value, list):
            return [bind(item) for item in value]
        if isinstance(value, dict):
            return {bind(key): bind(item) for key, item in value.items()}
        return value

    # One transaction applies every fixed ref; distinct refs end exactly as the
    # per-ref loop did.
    updates = "".join(f"update {ref} {oid}\n" for ref, oid in resources["refs"].items())
    _git(repository, "update-ref", "--stdin", data=updates.encode("utf-8"))
    for relative, text in resources["files"].items():
        target = state / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.name == "result-receipt.json":
            # fsync_json writes this envelope with whitespace and UTF-8 text;
            # preserve those bytes while rebinding its referenced paths/hashes.
            content = replace(text, json_payload=True)
        else:
            content = _json(bind(json.loads(text)), ascii_json=True) if target.suffix == ".json" else replace(text)
        target.write_bytes(content.encode("utf-8"))
        target.chmod(0o600)
    with closing(sqlite3.connect(state / "board.sqlite3")) as connection, connection:
        connection.executescript(stock["board.sql"])
        # Change values only. Never initialize with the current schema, insert a
        # conclusion, add a migration alias, or rewrite native provenance.
        tables = [row[0] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
        for table in tables:
            columns = [row[1] for row in connection.execute(f'PRAGMA table_info("{table}")')]
            for row in connection.execute(f'SELECT rowid,* FROM "{table}"').fetchall():
                changes = {}
                for column, value in zip(columns, row[1:]):
                    if not isinstance(value, str):
                        continue
                    if value.startswith(("{", "[")):
                        parsed = json.loads(value)
                        rebound = bind(parsed)
                        updated = _json(rebound) if parsed != rebound else value
                    else:
                        updated = replace(value)
                    if updated != value:
                        changes[column] = updated
                if changes:
                    fields = ",".join(f'"{name}"=?' for name in changes)
                    connection.execute(f'UPDATE "{table}" SET {fields} WHERE rowid=?', [*changes.values(), row[0]])
    return {**metadata, "state": state, "fixtureRoot": fixture_root,
            "operations": bind(json.loads(stock["operations.json"])), "bindings": bindings}
