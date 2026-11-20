"""Restore the fixed schema-30 stock; private binding precedes any cutover.

No old-source imports, generator, daemon, account or model is used here. The
returned board is the preservation baseline, including its rebound base-format
identities and digests. This module performs fixture preparation, not migration.
"""
from __future__ import annotations
from hey_my_buddy.paths import native_path

from contextlib import closing
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import subprocess
import tempfile

from hey_my_buddy.blackboard.tasks.workspace import _git_command

FIXTURE = Path(__file__).resolve().parents[3] / "fixtures/upgrade-floor"
FIXTURE_ROOT_TOKEN = "__hmb_fixture_root_6e21f754c8724c12a88250e88a0dd503__"


def complete_fixture_cleanup(connection):
    """Prepare an install-idle fixture without changing the fixed stock.

    The model-free floor writer left cleanup_pending set after receiving its
    synthetic results. The fixture has no live endpoint or credential files.
    Reproduce the flag update in 0.54.1's complete_execution_cleanup before
    taking an installation preservation baseline; this is not a migration.
    """
    with connection:
        connection.execute("UPDATE attempts SET cleanup_pending=0"
                           " WHERE execution_state='finished' AND shutdown_confirmed=1 AND cleanup_pending=1")


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _json(value, *, ascii_json=False) -> str:
    return json.dumps(value, ensure_ascii=ascii_json, sort_keys=True, separators=(",", ":"))


def _git(root: Path, *args: str, data=None) -> str:
    command, git_env = _git_command(root, *args)
    return subprocess.run(command, input=data, capture_output=True, check=True,
                          env=git_env).stdout.decode("utf-8").strip()


def load_fixture(state: Path, *, source: Path = FIXTURE) -> dict:
    """Materialize in an absent/empty private state; return metadata + bindings.

    ``state`` and ``fixtureRoot`` are Paths; ``operations`` is the rebound old
    operation log; ``bindings`` maps template-era identities/digests to restored
    ones. Other keys are the fixed metadata (including stableIds/sourceCommit).
    resourceFiles lists their rebound relative paths. Schema remains 30.
    Take the preservation baseline only after this returns.
    """
    metadata = json.loads((source / "metadata.json").read_text(encoding="utf-8"))
    stock = {}
    for name, key in (("board.sql", "sqlSha256"), ("operations.json", "operationsSha256"),
                      ("catalog.json", "catalogSha256"), ("workspace-resources.json", "resourcesSha256")):
        data = (source / name).read_bytes()
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
    fixture_root = state / "f"
    repository = fixture_root / "repository"
    repository.mkdir(parents=True)
    _git(repository, "init", "--quiet")
    # The fixed Git objects and fingerprints describe exact bytes; a machine's
    # autocrlf default must not rewrite them while restoring the worktrees.
    _git(repository, "config", "core.autocrlf", "false")
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
    allocation_requests = {}
    for relative, text in resources["files"].items():
        matched = re.fullmatch(r"workspaces/(ws-[0-9a-f]{32})/request.json", relative)
        if matched is not None:
            request = json.loads(text)
            allocation_requests[matched[1]] = request
            # The released writer derives this identity from the state root,
            # just as the repository and checkout identities below are local.
            bindings[matched[1]] = "ws-" + _sha(_json(
                [str(state), request["requestId"]], ascii_json=True).encode())[:32]
    identity_token = r"ws-[0-9a-f]{32}|[0-9a-f]{64}"

    def replace(text, *, ascii_json=False, json_payload=False):
        def path(value):
            return json.dumps(str(value), ensure_ascii=ascii_json)[1:-1] if json_payload else str(value)

        def substitute(match):
            token = match[0]
            if not token.startswith(FIXTURE_ROOT_TOKEN):
                return bindings.get(token, token)
            # Bind the complete template path, including its separators and
            # digest components. Never interpret tokens in the private root.
            relative = token.removeprefix(FIXTURE_ROOT_TOKEN)
            if relative == '/state' or relative.startswith('/state/'):
                root, relative = state, relative.removeprefix('/state')
            else:
                root = fixture_root
            relative = re.sub(identity_token, lambda identity: bindings.get(identity[0], identity[0]), relative)
            return path(root.joinpath(*relative.lstrip('/').split('/')) if relative else root)

        # Substitute each source token once; a private directory name can itself
        # contain a placeholder or a hex digest without being interpreted again.
        return re.sub(re.escape(FIXTURE_ROOT_TOKEN) + r"(?:/[\w.-]+)*|" + identity_token, substitute, text)

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
    updates = "".join(f"update {replace(ref)} {oid}\n" for ref, oid in resources["refs"].items())
    _git(repository, "update-ref", "--stdin", data=updates.encode("utf-8"))
    for relative, text in resources["files"].items():
        target = state / replace(relative)
        native_path(target.parent).mkdir(parents=True, exist_ok=True)
        if target.name == "result-receipt.json":
            # fsync_json writes this envelope with whitespace and UTF-8 text;
            # preserve those bytes while rebinding its referenced paths/hashes.
            content = replace(text, json_payload=True)
        else:
            content = _json(bind(json.loads(text)), ascii_json=True) if target.suffix == ".json" else replace(text)
        native_path(target).write_bytes(content.encode("utf-8"))
        native_path(target).chmod(0o600)
    restored_checkouts = {replace(path) for path in resources["checkouts"].values()}
    for old in allocation_requests:
        directory = state / "workspaces" / bindings[old]
        # The stock omits transient lock files; restore the released writer's
        # allocation lock alongside the rebound ownership records.
        native_path(directory / ".lock").touch()
        path = directory / "manifest.json"
        manifest = json.loads(path.read_text(encoding="utf-8"))
        if manifest["kind"] == "worktree" and manifest["checkoutRoot"] in restored_checkouts:
            _git(repository, "worktree", "lock", "--reason",
                 "hey-my-buddy:" + manifest["workspaceId"], manifest["checkoutRoot"])
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
            "resourceFiles": [replace(relative) for relative in resources["files"]],
            "operations": bind(json.loads(stock["operations.json"])), "bindings": bindings}


def load_issue_stock(fixture: dict) -> None:
    """Add unchanged problem rows and events from the floor's issue writers."""
    metadata = fixture['issueStock']
    stock = (FIXTURE / 'schema-30-issue-stock.sql').read_bytes()
    if _sha(stock) != metadata['sha256'] or _sha((FIXTURE / 'board.sql').read_bytes()) != metadata['baseSha256']:
        raise ValueError('Issue stock digest changed')
    with closing(sqlite3.connect(fixture['state'] / 'board.sqlite3')) as connection, connection:
        if connection.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0] != '30':
            raise ValueError('Issue stock requires the restored floor schema')
        connection.executescript(stock.decode('utf-8'))


def load_schema36_model_stock(connection: sqlite3.Connection) -> None:
    """Derive retained model-key forms on a private schema-36 floor upgrade.

    The generated SQL includes the historical schema-36 writer's cell rows.
    """
    metadata = json.loads((FIXTURE / 'metadata.json').read_text(encoding='utf-8'))
    stock = (FIXTURE / 'schema-36-model-stock.sql').read_bytes()
    provenance = metadata['modelStock']
    if (_sha(stock) != provenance['sha256']
            or _sha((FIXTURE / 'board.sql').read_bytes()) != provenance['baseSha256']
            or _sha((FIXTURE / 'schema-31-stock.sql').read_bytes()) != provenance['schema31StockSha256']):
        raise ValueError('Schema-36 model stock digest changed')
    if connection.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0] != '36':
        raise ValueError('Model stock requires the private schema-36 board')
    connection.executescript(stock.decode('utf-8'))


def load_delivered_stock(fixture: dict) -> None:
    """Add the floor writer's delivered v3 run and its bound physical evidence."""
    metadata = json.loads((FIXTURE / "metadata.json").read_text(encoding="utf-8"))
    sql = (FIXTURE / "schema-30-delivered-stock.sql").read_bytes()
    resource_bytes = (FIXTURE / "schema-30-delivered-resources.json").read_bytes()
    if _sha(sql) != metadata["deliveredStockSqlSha256"] or _sha(resource_bytes) != metadata["deliveredStockResourcesSha256"]:
        raise ValueError("Delivered floor supplement digest changed")
    resources = json.loads(resource_bytes)
    if resources["format"] != 1:
        raise ValueError("Unsupported delivered resource format")
    state, fixture_root = fixture["state"], fixture["fixtureRoot"]
    bindings = dict(fixture["bindings"])
    identity_token = r"ws-[0-9a-f]{32}|[0-9a-f]{64}"
    for relative, content in resources["files"].items():
        matched = re.fullmatch(r"workspaces/(ws-[0-9a-f]{32})/request.json", relative)
        if matched is not None:
            request = json.loads(content)
            bindings[matched[1]] = "ws-" + _sha(_json(
                [str(state), request["requestId"]], ascii_json=True).encode())[:32]

    def replace(text, *, ascii_json=False, json_payload=False):
        def substitute(match):
            token = match[0]
            if not token.startswith(FIXTURE_ROOT_TOKEN):
                return bindings.get(token, token)
            relative = token.removeprefix(FIXTURE_ROOT_TOKEN)
            if relative == "/state" or relative.startswith("/state/"):
                root, relative = state, relative.removeprefix("/state")
            else:
                root = fixture_root
            relative = re.sub(identity_token, lambda item: bindings.get(item[0], item[0]), relative)
            path = root.joinpath(*relative.lstrip("/").split("/")) if relative else root
            return json.dumps(str(path), ensure_ascii=ascii_json)[1:-1] if json_payload else str(path)

        return re.sub(re.escape(FIXTURE_ROOT_TOKEN) + r"(?:/[\w.-]+)*|" + identity_token, substitute, text)

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

    repository = fixture_root / "repository"
    with tempfile.TemporaryDirectory(prefix="delivered-stock-objects-") as scratch:
        grouped = {}
        for item in resources["gitObjects"]:
            path = Path(scratch) / item["oid"]
            path.write_bytes(bytes.fromhex(item["hex"]))
            grouped.setdefault(item["type"], []).append(path)
        for kind, paths in grouped.items():
            listing = "".join(f"{path}\n" for path in paths)
            actual = _git(repository, "hash-object", "-w", "-t", kind, "--stdin-paths", data=listing.encode())
            if actual.splitlines() != [path.name for path in paths]:
                raise ValueError("Delivered Git object changed")
    if resources["refs"]:
        updates = "".join(f"update {replace(ref)} {oid}\n" for ref, oid in resources["refs"].items())
        _git(repository, "update-ref", "--stdin", data=updates.encode())
    for relative, content in resources["files"].items():
        target = state / replace(relative)
        native_path(target.parent).mkdir(parents=True, exist_ok=True)
        if target.name == "result-receipt.json":
            text = replace(content, json_payload=True)
        else:
            text = _json(_bind_helper_value(json.loads(content), replace), ascii_json=True) if target.suffix == ".json" else replace(content)
        native_path(target).write_bytes(text.encode())
        native_path(target).chmod(0o600)
        if target.name == "request.json":
            native_path(target.parent / ".lock").touch()

    with closing(sqlite3.connect(":memory:")) as template:
        template.row_factory = sqlite3.Row
        template.executescript((FIXTURE / "board.sql").read_text(encoding="utf-8"))
        before = {name: {row[0] for row in template.execute(f'SELECT rowid FROM "{name}"')}
                  for (name,) in template.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
        template.executescript(sql.decode("utf-8"))
        with closing(sqlite3.connect(state / "board.sqlite3")) as connection, connection:
            for name, rowids in before.items():
                columns = [row[1] for row in template.execute(f'PRAGMA table_info("{name}")')]
                fields = ",".join(("rowid", *columns))
                insert = f'INSERT INTO "{name}"({fields}) VALUES({",".join("?" for _ in range(len(columns) + 1))})'
                for row in template.execute(f'SELECT rowid,* FROM "{name}" ORDER BY rowid'):
                    if row[0] in rowids:
                        continue
                    values = []
                    for value in row:
                        if isinstance(value, str) and value.startswith(("{", "[")):
                            parsed = json.loads(value)
                            rebound = bind(parsed)
                            value = _json(rebound) if parsed != rebound else value
                        elif isinstance(value, str):
                            value = replace(value)
                        values.append(value)
                    connection.execute(insert, values)
    fixture["bindings"].update(bindings)


def load_workspace_growth_stock(fixture: dict, *, stage: str) -> None:
    """Restore a writer-labeled supplement at its exact schema boundary.

    ``floor30`` follows ``load_fixture`` and precedes the frozen chain;
    ``schema33`` follows 32 → 33 and precedes 33 → 34. No writer is run here.
    """
    names = {
        "floor30": (30, "growth30", "schema-30-workspace-growth"),
        "schema33": (33, "growth33", "schema-33-workspace-growth"),
    }
    if stage not in names:
        raise ValueError(f"Unknown workspace growth stock stage: {stage}")
    expected_version, key, stem = names[stage]
    metadata = json.loads((FIXTURE / "metadata.json").read_text(encoding="utf-8"))
    sql = (FIXTURE / f"{stem}-stock.sql").read_bytes()
    resource_bytes = (FIXTURE / f"{stem}-resources.json").read_bytes()
    if (_sha(sql) != metadata[key + "SqlSha256"]
            or _sha(resource_bytes) != metadata[key + "ResourcesSha256"]
            or _sha((FIXTURE / "board.sql").read_bytes()) != metadata[key + "BaseSha256"]):
        raise ValueError(f"{stage} supplement digest changed")
    resources = json.loads(resource_bytes)
    if resources["format"] != 1:
        raise ValueError(f"Unsupported {stage} supplement resource format")
    state, fixture_root = fixture["state"], fixture["fixtureRoot"]
    with closing(sqlite3.connect(state / "board.sqlite3")) as connection:
        version = connection.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0]
        if version != str(expected_version):
            raise ValueError(f"{stage} stock requires schema {expected_version}, found {version}")
    bindings = dict(fixture["bindings"])
    identity_token = r"ws-[0-9a-f]{32}|[0-9a-f]{64}"
    for relative, content in resources["files"].items():
        matched = re.fullmatch(r"workspaces/(ws-[0-9a-f]{32})/request.json", relative)
        if matched is not None:
            request = json.loads(content)
            bindings[matched[1]] = "ws-" + _sha(_json(
                [str(state), request["requestId"]], ascii_json=True).encode())[:32]

    def replace(value, *, ascii_json=False, json_payload=False):
        def substitute(match):
            token = match[0]
            if not token.startswith(FIXTURE_ROOT_TOKEN):
                return bindings.get(token, token)
            relative = token.removeprefix(FIXTURE_ROOT_TOKEN)
            if relative == "/state" or relative.startswith("/state/"):
                root, relative = state, relative.removeprefix("/state")
            else:
                root = fixture_root
            relative = re.sub(identity_token, lambda item: bindings.get(item[0], item[0]), relative)
            path = root.joinpath(*relative.lstrip("/").split("/")) if relative else root
            return json.dumps(str(path), ensure_ascii=ascii_json)[1:-1] if json_payload else str(path)

        return re.sub(re.escape(FIXTURE_ROOT_TOKEN) + r"(?:/[\w.-]+)*|" + identity_token,
                      substitute, value)

    for recipe in resources["hashBindings"]:
        payload = replace(recipe["template"], ascii_json=recipe["asciiJson"], json_payload=True)
        bindings[recipe["oldSha256"]] = _sha(payload.encode())

    def bind(value):
        if isinstance(value, str):
            return replace(value)
        if isinstance(value, list):
            return [bind(item) for item in value]
        if isinstance(value, dict):
            return {bind(name): bind(item) for name, item in value.items()}
        return value

    repository = fixture_root / "repository"
    with tempfile.TemporaryDirectory(prefix="growth-stock-objects-") as scratch:
        grouped = {}
        for item in resources["gitObjects"]:
            path = Path(scratch) / item["oid"]
            path.write_bytes(bytes.fromhex(item["hex"]))
            grouped.setdefault(item["type"], []).append(path)
        for kind, paths in grouped.items():
            listing = "".join(f"{path}\n" for path in paths)
            actual = _git(repository, "hash-object", "-w", "-t", kind, "--stdin-paths",
                          data=listing.encode("utf-8"))
            if actual.splitlines() != [path.name for path in paths]:
                raise ValueError(f"{stage} Git object changed")
    if resources["refs"]:
        updates = "".join(f"update {replace(ref)} {oid}\n" for ref, oid in resources["refs"].items())
        _git(repository, "update-ref", "--stdin", data=updates.encode("utf-8"))
    for relative, content in resources["files"].items():
        target = state / replace(relative)
        native_path(target.parent).mkdir(parents=True, exist_ok=True)
        text = replace(content, json_payload=True) if target.name == "result-receipt.json" else (
            _json(_bind_helper_value(json.loads(content), replace), ascii_json=True) if target.suffix == ".json" else replace(content))
        native_path(target).write_bytes(text.encode("utf-8"))
        native_path(target).chmod(0o600)
        if target.name == "request.json":
            native_path(target.parent / ".lock").touch()

    with closing(sqlite3.connect(state / "board.sqlite3")) as connection:
        connection.row_factory = sqlite3.Row
        with closing(sqlite3.connect(":memory:")) as template:
            template.row_factory = sqlite3.Row
            connection.backup(template)
            tables = [name for (name,) in template.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
            before = {name: {row[0] for row in template.execute(f'SELECT rowid FROM "{name}"')}
                      for name in tables}
            template.executescript(sql.decode("utf-8"))
            with connection:
                for name in tables:
                    columns = [row[1] for row in template.execute(f'PRAGMA table_info("{name}")')]
                    fields = ",".join(("rowid", *(f'"{column}"' for column in columns)))
                    placeholders = ",".join("?" for _ in range(len(columns) + 1))
                    for row in template.execute(f'SELECT rowid,* FROM "{name}" ORDER BY rowid'):
                        if row[0] in before[name]:
                            continue
                        values = []
                        for value in row:
                            if isinstance(value, str) and value.startswith(("{", "[")):
                                parsed = json.loads(value)
                                rebound = bind(parsed)
                                value = _json(rebound) if parsed != rebound else value
                            elif isinstance(value, str):
                                value = replace(value)
                            values.append(value)
                        connection.execute(f'INSERT INTO "{name}"({fields}) VALUES({placeholders})', values)
    fixture["bindings"].update(bindings)
    fixture.setdefault("resourceFiles", []).extend(replace(path) for path in resources["files"])


def load_legacy_stock(fixture: dict, *, version: int) -> None:
    """Prepare one independent v1/v2 derived copy before its upgrade baseline.

    The checked base export stays unchanged. The profile supplies every changed
    row/physical record and its dependency-ordered digest preimages, so the old
    turn, output, shared-ref and receipt bindings remain coherent after rebinding.
    """
    if type(version) is not int or version not in (1, 2):
        raise ValueError('Legacy stock version must be 1 or 2')
    if 'legacyVersion' in fixture:
        raise ValueError('Legacy stock requires a fresh floor copy')
    metadata = json.loads((FIXTURE / 'metadata.json').read_text())
    legacy = metadata['legacyStock']
    profile = legacy['profiles'][str(version)]
    stem = f'schema-30-legacy-v{version}'
    sql = (FIXTURE / (stem + '-stock.sql')).read_bytes()
    raw_resources = (FIXTURE / (stem + '-resources.json')).read_bytes()
    if (_sha(sql) != profile['sqlSha256'] or _sha(raw_resources) != profile['resourcesSha256']
            or _sha((FIXTURE / 'board.sql').read_bytes()) != legacy['baseSha256']):
        raise ValueError('Legacy derived stock digest changed')
    resources = json.loads(raw_resources)
    if resources['format'] != 1:
        raise ValueError('Unsupported legacy stock resource format')
    state, fixture_root = fixture['state'], fixture['fixtureRoot']
    bindings = dict(fixture['bindings'])
    token_pattern = re.escape(FIXTURE_ROOT_TOKEN) + r'(?:/[\w.-]+)*|ws-[0-9a-f]{32}|[0-9a-f]{64}'

    def replace(text, *, ascii_json=False, json_payload=False):
        def substitute(match):
            token = match[0]
            if not token.startswith(FIXTURE_ROOT_TOKEN):
                return bindings.get(token, token)
            relative = token.removeprefix(FIXTURE_ROOT_TOKEN)
            root = fixture_root
            if relative == '/state' or relative.startswith('/state/'):
                root, relative = state, relative.removeprefix('/state')
            relative = re.sub(r'ws-[0-9a-f]{32}|[0-9a-f]{64}',
                              lambda item: bindings.get(item[0], item[0]), relative)
            path = root.joinpath(*relative.lstrip('/').split('/')) if relative else root
            return json.dumps(str(path), ensure_ascii=ascii_json)[1:-1] if json_payload else str(path)
        return re.sub(token_pattern, substitute, text)

    def bind(value):
        if isinstance(value, str):
            return replace(value)
        if isinstance(value, list):
            return [bind(item) for item in value]
        if isinstance(value, dict):
            return {bind(key): bind(item) for key, item in value.items()}
        return value

    for recipe in resources['hashBindings']:
        payload = replace(recipe['template'], ascii_json=recipe['asciiJson'], json_payload=True)
        bindings[recipe['oldSha256']] = _sha(payload.encode())
    with closing(sqlite3.connect(state / 'board.sqlite3')) as connection:
        found = connection.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0]
        if found != '30':
            raise ValueError(f'Legacy stock requires schema 30, found {found}')
        with closing(sqlite3.connect(':memory:')) as template:
            template.row_factory = sqlite3.Row
            connection.backup(template)
            template.executescript(sql.decode())
            with connection:
                for source in resources['sourceRows']:
                    table, columns, rowid = source['table'], source['columns'], source['sourceRowid']
                    values = template.execute(f'SELECT {",".join(columns)} FROM "{table}" WHERE rowid=?', (rowid,)).fetchone()
                    rebound = []
                    for value in values:
                        if isinstance(value, str) and value.startswith(('{', '[')):
                            original = json.loads(value)
                            updated = bind(original)
                            value = _json(updated) if updated != original else value
                        elif isinstance(value, str):
                            value = replace(value)
                        rebound.append(value)
                    connection.execute(f'UPDATE "{table}" SET {",".join(column + "=?" for column in columns)} WHERE rowid=?',
                                       [*rebound, rowid])
    for relative, content in resources['files'].items():
        path = state / replace(relative)
        if path.name == 'result-receipt.json':
            payload = replace(content, json_payload=True)
        elif path.suffix == '.json':
            payload = _json(_bind_helper_value(json.loads(content), replace), ascii_json=True)
        else:
            payload = replace(content)
        native_path(path).write_bytes(payload.encode())
    for update in resources['operationUpdates']:
        fixture['operations'][update['sourceIndex']] = bind(update['value'])
    # Retain the source→derived binding so all fixture consumers agree about
    # this private copy. The alternative profile starts from a fresh base.
    for recipe in resources['hashBindings']:
        bindings[recipe['sourceSha256']] = bindings[recipe['oldSha256']]
    fixture['bindings'].update(bindings)
    fixture['legacyVersion'] = version


def upgrade_fixture_board_to_41(fixture: dict, *, source: Path = FIXTURE) -> None:
    """Upgrade a restored floor copy through the frozen chain to Schema 41.

    This mirrors the helper-retirement generator's preparation exactly: the
    schema-31 maintenance stock applies after 30 -> 31 and the baseline cleanup
    flags clear, so the private board this returns is the same idle starting
    point every helper supplement was derived on.
    """
    from hey_my_buddy.blackboard.store import (
        schema_30_31, schema_31_32, schema_32_33, schema_33_34, schema_34_35,
        schema_35_36, schema_36_37, schema_37_38, schema_38_39, schema_39_40,
        schema_40_41,
    )
    from itertools import count
    from unittest.mock import patch
    from uuid import UUID

    # Reuse the same identities for the models and services minted by 36 -> 37
    # on each private copy. New Schema 41 writer rows reference those IDs.
    identifiers = count(8_000_000_000_000)
    with closing(sqlite3.connect(fixture['state'] / 'board.sqlite3')) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute('PRAGMA foreign_keys=OFF')
        for step in (schema_30_31, schema_31_32, schema_32_33, schema_33_34, schema_34_35,
                     schema_35_36, schema_36_37, schema_37_38, schema_38_39, schema_39_40,
                     schema_40_41):
            if step is schema_36_37:
                with patch.object(step, 'uuid4', side_effect=lambda: UUID(int=next(identifiers))):
                    step.upgrade(connection)
            else:
                step.upgrade(connection)
            if step is schema_30_31:
                connection.executescript((source / 'schema-31-stock.sql').read_text(encoding='utf-8'))
        if connection.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0] != '41':
            raise ValueError('Helper stock preparation requires the private schema-41 board')
        complete_fixture_cleanup(connection)
        _check_helper_board(connection)


def _helper_stock_bindings(fixture, provenance):
    return {old: fixture['bindings'][token] for token, old in provenance['baseBindings'].items()}


def _helper_rebinder(fixture, bindings):
    state, fixture_root = fixture['state'], fixture['fixtureRoot']
    identity_token = r'ws-[0-9a-f]{32}|[0-9a-f]{64}'

    def replace(text, *, ascii_json=False, json_payload=False):
        def substitute(match):
            token = match[0]
            if not token.startswith(FIXTURE_ROOT_TOKEN):
                return bindings.get(token, token)
            relative = token.removeprefix(FIXTURE_ROOT_TOKEN)
            if relative == '/state' or relative.startswith('/state/'):
                root, relative = state, relative.removeprefix('/state')
            else:
                root = fixture_root
            relative = re.sub(identity_token, lambda item: bindings.get(item[0], item[0]), relative)
            path = root.joinpath(*relative.lstrip('/').split('/')) if relative else root
            return json.dumps(str(path), ensure_ascii=ascii_json)[1:-1] if json_payload else str(path)
        return re.sub(re.escape(FIXTURE_ROOT_TOKEN) + r'(?:/[\w.-]+)*|' + identity_token, substitute, text)

    return replace


def _bind_helper_value(value, replace):
    if isinstance(value, str):
        return replace(value)
    if isinstance(value, list):
        return [_bind_helper_value(item, replace) for item in value]
    if isinstance(value, dict):
        return {replace(key): _bind_helper_value(item, replace) for key, item in value.items()}
    return value


def _require_schema_41(connection):
    found = connection.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0]
    if found != '41':
        raise ValueError(f'Helper stock requires schema 41, found {found}')


def _check_helper_board(connection):
    violations = connection.execute('PRAGMA foreign_key_check').fetchall()
    if violations:
        raise ValueError(f'Helper stock left broken foreign keys: {violations}')
    integrity = connection.execute('PRAGMA integrity_check').fetchall()
    if [row[0] for row in integrity] != ['ok']:
        raise ValueError(f'Helper stock left a damaged board: {integrity}')


def _apply_helper_sql(state, sql, replace):
    # Run the frozen SQL on a private copy before rebinding only the changed
    # rows. Keep SQLite rowids explicit even for tables whose keys are text.
    with (closing(sqlite3.connect(state / 'board.sqlite3')) as connection,
          closing(sqlite3.connect(':memory:')) as template):
        _require_schema_41(connection)
        connection.backup(template)
        tables = [name for (name,) in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
        template.executescript(sql)
        with connection:
            for name in tables:
                columns = [f'"{row[1]}"' for row in template.execute(f'PRAGMA table_info("{name}")')]
                before = {row[0]: row for row in connection.execute(f'SELECT rowid,* FROM "{name}"')}
                for row in template.execute(f'SELECT rowid,* FROM "{name}"'):
                    original = before.get(row[0])
                    if original == row:
                        continue
                    values = []
                    for value in row[1:]:
                        if isinstance(value, str) and value.startswith(('{', '[')):
                            parsed = json.loads(value)
                            rebound = _bind_helper_value(parsed, replace)
                            value = _json(rebound) if parsed != rebound else value
                        elif isinstance(value, str):
                            value = replace(value)
                        values.append(value)
                    if original is None:
                        fields = ','.join(['rowid', *columns])
                        placeholders = ','.join('?' for _ in row)
                        connection.execute(f'INSERT INTO "{name}"({fields}) VALUES({placeholders})',
                                           (row[0], *values))
                    else:
                        if name not in ('kind_statistics', 'kind_statistics_buckets',
                                        'buddy_infrastructure_failures', 'buddies',
                                        'evaluation_preferences'):
                            raise ValueError(f'Helper stock changed an unexpected existing row: {name}/{row[0]}')
                        assignments = ','.join(f'{column}=?' for column in columns)
                        connection.execute(f'UPDATE "{name}" SET {assignments} WHERE rowid=?',
                                           (*values, row[0]))
            _check_helper_board(connection)


def _load_helper_stock(fixture: dict, *, sql_name: str, sql_key: str,
                       resources_name: str, resources_key: str) -> None:
    """Apply one Schema 41 helper supplement and its physical resources."""
    metadata = json.loads((FIXTURE / 'metadata.json').read_text(encoding='utf-8'))
    provenance = metadata['helperRetirementStock']
    sql = (FIXTURE / sql_name).read_text(encoding='utf-8')
    raw_resources = (FIXTURE / resources_name).read_bytes()
    if (_sha(sql.encode('utf-8')) != provenance[sql_key]
            or _sha(raw_resources) != provenance[resources_key]
            or _sha((FIXTURE / 'board.sql').read_bytes()) != provenance['baseSha256']
            or _sha((FIXTURE / 'schema-31-stock.sql').read_bytes()) != provenance['schema31StockSha256']):
        raise ValueError(f'{sql_name} digest changed')
    resources = json.loads(raw_resources)
    _restore_helper_stock(fixture, provenance, sql, resources, sql_name)


def _restore_helper_stock(fixture, provenance, sql, resources, sql_name):
    if resources['format'] != 1:
        raise ValueError(f'Unsupported {sql_name} resource format')
    state, fixture_root = fixture['state'], fixture['fixtureRoot']
    with closing(sqlite3.connect(state / 'board.sqlite3')) as connection:
        _require_schema_41(connection)
    bindings = _helper_stock_bindings(fixture, provenance)
    replace = _helper_rebinder(fixture, bindings)

    for relative, content in resources['files'].items():
        matched = re.fullmatch(r'workspaces/(ws-[0-9a-f]{32})/request.json', relative)
        if matched is not None:
            request = json.loads(content)
            bindings[matched[1]] = 'ws-' + _sha(_json(
                [str(state), request['requestId']], ascii_json=True).encode())[:32]
    for recipe in resources['hashBindings']:
        payload = replace(recipe['template'], ascii_json=recipe['asciiJson'], json_payload=True)
        bindings[recipe['oldSha256']] = _sha(payload.encode())
    repository = fixture_root / 'repository'
    with tempfile.TemporaryDirectory(prefix='helper-stock-objects-') as scratch:
        grouped = {}
        for item in resources['gitObjects']:
            path = Path(scratch) / item['oid']
            path.write_bytes(bytes.fromhex(item['hex']))
            grouped.setdefault(item['type'], []).append(path)
        for kind, paths in grouped.items():
            listing = ''.join(f'{path}\n' for path in paths)
            actual = _git(repository, 'hash-object', '-w', '-t', kind, '--stdin-paths', data=listing.encode('utf-8'))
            if actual.splitlines() != [path.name for path in paths]:
                raise ValueError(f'{sql_name} Git object changed')
    if resources['refs']:
        updates = ''.join(f'update {replace(ref)} {oid}\n' for ref, oid in resources['refs'].items())
        _git(repository, 'update-ref', '--stdin', data=updates.encode('utf-8'))
    for template, facts in resources['checkouts'].items():
        checkout = Path(replace(template))
        _git(repository, 'worktree', 'add', '--quiet', '--detach', str(checkout), facts['commit'])
        if checkout.parent.parent == state / 'workspaces':
            _git(repository, 'worktree', 'lock', '--reason', 'hey-my-buddy:' + checkout.parent.name, str(checkout))
        for name, content in facts['files'].items():
            native_path(checkout / name).write_bytes(content.encode('utf-8'))
    for relative, content in resources['files'].items():
        target = state / replace(relative)
        native_path(target.parent).mkdir(parents=True, exist_ok=True)
        if target.name == 'result-receipt.json':
            text = replace(content, json_payload=True)
        else:
            text = _json(_bind_helper_value(json.loads(content), replace), ascii_json=True) if target.suffix == '.json' else replace(content)
        native_path(target).write_bytes(text.encode('utf-8'))
        native_path(target).chmod(0o600)
        if target.name == 'request.json':
            native_path(target.parent / '.lock').touch()
    _apply_helper_sql(state, sql, replace)
    from hey_my_buddy.protocol.session_content import SessionFormat, SessionNotification
    records = [state / replace(relative) for relative in resources['files']
               if relative.startswith('sessions/')]
    for path in records:
        header, notification = native_path(path).read_text(encoding='utf-8').splitlines()
        fmt = SessionFormat.model_validate_json(header)
        if (fmt.protocol_version, fmt.projection_version) != (1, 1):
            raise ValueError(f'{sql_name} wrote an unsupported session record format: {path.name}')
        if SessionNotification.model_validate_json(notification).session_id != path.stem:
            raise ValueError(f'{sql_name} session record identity mismatch: {path.name}')
    fixture['bindings'].update(bindings)
    fixture.setdefault('resourceFiles', []).extend(replace(relative) for relative in resources['files'])


def load_helper_retirement_stock(fixture: dict) -> None:
    """Restore the installable Schema 41 helper-retirement stock on a schema-41 board.

    Call ``upgrade_fixture_board_to_41`` first. Every task stays idle or
    stopped for a cross-contract install, and the stock's session records and
    physical resources are verified while applying.
    """
    _load_helper_stock(fixture, sql_name='schema-41-helper-retirement-stock.sql', sql_key='sqlSha256',
                       resources_name='schema-41-helper-retirement-resources.json',
                       resources_key='resourcesSha256')


def load_helper_offline_stock(fixture: dict) -> None:
    """Restore the offline-only Schema 41 helper-retirement stock.

    The cross-contract installer refuses this board's idle-check failures; the
    single-step 41 -> 42 migration is its intended consumer.
    """
    _load_helper_stock(fixture, sql_name='schema-41-helper-offline-stock.sql', sql_key='offlineSqlSha256',
                       resources_name='schema-41-helper-offline-resources.json',
                       resources_key='offlineResourcesSha256')


def load_helper_reject_case(fixture: dict, case: str) -> None:
    """Apply one independent Schema 41 reject case on a schema-41 board.

    Each case is writer-produced incremental SQL for the 41 -> 42 preflight.
    The unreclaimed checkout case also restores its physical checkout.
    """
    metadata = json.loads((FIXTURE / 'metadata.json').read_text(encoding='utf-8'))
    provenance = metadata['helperRetirementStock']
    raw = (FIXTURE / 'schema-41-helper-reject-stock.json').read_bytes()
    if _sha(raw) != provenance['rejectStockSha256']:
        raise ValueError('Helper reject stock digest changed')
    stock = json.loads(raw)
    if stock['format'] != 1 or case not in stock['cases']:
        raise ValueError(f'Unknown helper reject case: {case}')
    sql = stock['cases'][case]
    if (_sha(sql.encode('utf-8')) != provenance['rejectCases'][case]
            or _sha((FIXTURE / 'board.sql').read_bytes()) != provenance['baseSha256']):
        raise ValueError(f'Reject case digest changed: {case}')
    if case in stock['resources']:
        _restore_helper_stock(fixture, provenance, sql, stock['resources'][case], case)
    else:
        replace = _helper_rebinder(fixture, _helper_stock_bindings(fixture, provenance))
        _apply_helper_sql(fixture['state'], sql, replace)
