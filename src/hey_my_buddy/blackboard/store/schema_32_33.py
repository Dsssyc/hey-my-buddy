"""Schema 32 → 33: bind old project identities and replace checkout identity columns.

This step owns its SQL, old identity algorithm, and Git operations. The installer
holds the database transaction; a project ref already created before a database
rollback is intentionally retained and recognized on retry.
"""

import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import tempfile


PROJECT_REF = "refs/hey-my-buddy/project"

RESERVATIONS_DDL = """
CREATE TABLE IF NOT EXISTS workspace_reservations (
    reservation_id  TEXT PRIMARY KEY,
    workspace_id    TEXT NOT NULL,
    checkout_root   TEXT NOT NULL,
    access          TEXT NOT NULL CHECK (access IN ('read','write')),
    holder_task_id  TEXT NOT NULL REFERENCES tasks(task_id) ON DELETE RESTRICT,
    holder_kind     TEXT NOT NULL CHECK (holder_kind IN ('parent','helper','reader')),
    parent_run_id   TEXT REFERENCES workflow_runs(run_id) ON DELETE RESTRICT,
    manifest_sha256 TEXT NOT NULL,
    state           TEXT NOT NULL CHECK (state IN ('held','transferred','released')),
    created_at      TEXT NOT NULL,
    released_at     TEXT
);
"""

RESERVATIONS_INDEXES = (
    """CREATE UNIQUE INDEX IF NOT EXISTS workspace_reservations_writer_unique
    ON workspace_reservations(checkout_root) WHERE state='held' AND access='write';""",
    """CREATE INDEX IF NOT EXISTS workspace_reservations_checkout_idx ON workspace_reservations(checkout_root, state);""",
    """CREATE INDEX IF NOT EXISTS workspace_reservations_holder_idx ON workspace_reservations(holder_task_id, state);""",
)

INTEGRATIONS_DDL = """
CREATE TABLE IF NOT EXISTS workflow_integrations (
    integration_id        TEXT PRIMARY KEY,
    run_id                TEXT NOT NULL REFERENCES workflow_runs(run_id) ON DELETE RESTRICT,
    artifact_id           TEXT NOT NULL,
    strategy              TEXT NOT NULL,
    binding_sha256        TEXT NOT NULL,
    target_path           TEXT NOT NULL DEFAULT '',
    target_ref            TEXT NOT NULL DEFAULT '',
    target_project_id     TEXT, -- NULL when the target was unmarked at verification
    source_commit         TEXT,
    source_tree           TEXT,
    before_commit         TEXT,
    after_commit          TEXT,
    before_tree           TEXT,
    after_tree            TEXT,
    verification_json     TEXT NOT NULL,
    note                  TEXT,
    command_id            TEXT,
    actor_role            TEXT NOT NULL,
    actor_id              TEXT NOT NULL,
    created_at            TEXT NOT NULL,
    CHECK (actor_role IN ('host','lead','console','service','workflow','bridge')),
    UNIQUE(binding_sha256)
);
"""

INTEGRATIONS_INDEX = """CREATE INDEX IF NOT EXISTS workflow_integrations_run_idx ON workflow_integrations(run_id, artifact_id, created_at);"""

CLEANUP_DDL = """
CREATE TABLE IF NOT EXISTS workspace_cleanup_plans (
    plan_id          TEXT PRIMARY KEY,
    run_id           TEXT NOT NULL REFERENCES workflow_runs(run_id) ON DELETE RESTRICT,
    workspace_id     TEXT NOT NULL,
    path             TEXT NOT NULL,
    kind             TEXT NOT NULL,
    state            TEXT NOT NULL CHECK (state IN ('planned','applying','applied','blocked')),
    evidence_json    TEXT NOT NULL,
    retention_json   TEXT NOT NULL,
    reasons_json     TEXT NOT NULL DEFAULT '[]',
    result_json      TEXT,
    revision         INTEGER NOT NULL DEFAULT 1,
    created_at       TEXT NOT NULL,
    applied_at       TEXT);
"""

CLEANUP_INDEX = """CREATE INDEX IF NOT EXISTS workspace_cleanup_plans_run_idx ON workspace_cleanup_plans(run_id, state);"""

MACRO_TASKS_DDL = """
CREATE TABLE IF NOT EXISTS macro_tasks (
    macro_task_id       TEXT PRIMARY KEY,
    title              TEXT NOT NULL,
    project_id         TEXT NOT NULL,
    source_host_id     TEXT NOT NULL,
    created_at         TEXT NOT NULL,
    activity_seq       INTEGER NOT NULL DEFAULT 0,
    activity_at        TEXT NOT NULL
);
"""

MACRO_TASKS_INDEXES = (
    "CREATE INDEX IF NOT EXISTS macro_tasks_activity_idx ON macro_tasks(activity_seq DESC, macro_task_id DESC);",
    "CREATE INDEX IF NOT EXISTS macro_tasks_project_idx ON macro_tasks(project_id, activity_seq DESC, macro_task_id DESC);",
)

MACRO_TASK_GOALS_DDL = """
CREATE TABLE IF NOT EXISTS macro_task_goals (
    macro_task_id TEXT NOT NULL REFERENCES macro_tasks(macro_task_id) ON DELETE CASCADE,
    revision      INTEGER NOT NULL,
    body          TEXT NOT NULL,
    created_at    TEXT NOT NULL,
    PRIMARY KEY (macro_task_id, revision)
);
"""


def _manifests(connection):
    """Read each stored sealed manifest form, without changing its bytes."""
    for table, column in (
        ("workflow_runs", "workspace_manifest_json"),
        ("workflow_children", "workspace_manifest_json"),
        ("workflow_continuations", "workspace_manifest_json"),
    ):
        for (raw,) in connection.execute(f"SELECT {column} FROM {table} WHERE {column} IS NOT NULL"):
            yield json.loads(raw)
    for (raw,) in connection.execute(
        "SELECT manifest_json FROM workflow_artifacts WHERE kind IN ('input','turn-input')"
    ):
        yield json.loads(raw)
    for (raw,) in connection.execute("SELECT input_json FROM workflow_turns WHERE input_json IS NOT NULL"):
        value = json.loads(raw).get("executionWorkspace")
        if value is not None:
            yield value
    for (raw,) in connection.execute("SELECT result_json FROM attempts WHERE result_json IS NOT NULL"):
        result = json.loads(raw)
        nested = result.get("result")
        if isinstance(nested, dict) and nested.get("workspaceManifest") is not None:
            yield nested["workspaceManifest"]


def _git(repository: Path, *args: str, data: bytes | None = None) -> bytes:
    environment = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    environment.update(LC_ALL="C", GIT_OPTIONAL_LOCKS="0", GIT_TERMINAL_PROMPT="0")
    # update-ref can invoke a repository's reference-transaction hook. An empty
    # private hooks directory makes this install step independent of user hooks.
    with tempfile.TemporaryDirectory(prefix="hmb-upgrade-git-hooks-") as hooks:
        command = ["git", "-c", f"core.hooksPath={hooks}", "--git-dir", str(repository), *args]
        try:
            result = subprocess.run(command, input=data, capture_output=True, env=environment, timeout=60,
                                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        except subprocess.TimeoutExpired as error:
            raise RuntimeError(
                f"Git timed out at {repository}: {' '.join(args)}; "
                f"stdout={(error.stdout or b'').decode('utf-8', errors='replace')!r}; "
                f"stderr={(error.stderr or b'').decode('utf-8', errors='replace')!r}"
            ) from error
        except OSError as error:
            raise RuntimeError(
                f"Git could not start at {repository}: {' '.join(args)}; "
                f"stdout=''; stderr={str(error)!r}"
            ) from error
    if result.returncode:
        raise RuntimeError(
            f"Git failed at {repository}: {' '.join(args)}; "
            f"stdout={result.stdout.decode('utf-8', errors='replace')!r}; "
            f"stderr={result.stderr.decode('utf-8', errors='replace')!r}"
        )
    return result.stdout


def _existing_project(repository: Path) -> bytes | None:
    record = _git(repository, "for-each-ref", "--format=%(objectname) %(symref)", PROJECT_REF).strip()
    if not record:
        return None
    oid, _, symbolic = record.partition(b" ")
    if symbolic:
        raise RuntimeError(f"Project ref is symbolic at {repository}: {PROJECT_REF}")
    kind = _git(repository, "cat-file", "-t", oid.decode("ascii")).strip()
    if kind != b"blob":
        raise RuntimeError(f"Project ref is not a blob at {repository}: {PROJECT_REF}")
    return _git(repository, "cat-file", "blob", oid.decode("ascii"))


def _mark_project(repository: Path, project_id: str) -> None:
    expected = project_id.encode("utf-8")
    existing = _existing_project(repository)
    if existing is not None:
        if existing != expected:
            raise RuntimeError(f"Different project marker at {repository}: {PROJECT_REF}")
        return
    oid = _git(repository, "hash-object", "-w", "--stdin", data=expected).strip().decode("ascii")
    try:
        _git(repository, "update-ref", "--no-deref", PROJECT_REF, oid, "0" * len(oid))
    except RuntimeError:
        # Another first writer may have created exactly the same marker.
        if _existing_project(repository) != expected:
            raise


def _mark_old_projects(manifests) -> None:
    candidates = {(manifest["repositoryId"], manifest["snapshot"]["repositoryPath"])
                  for manifest in manifests}
    for project_id, location in sorted(candidates):
        repository = Path(location)
        try:
            facts = repository.stat()
            if not stat.S_ISDIR(facts.st_mode):
                continue
            resolved = repository.resolve(strict=True)
        except (FileNotFoundError, NotADirectoryError):
            continue
        except OSError as error:
            raise RuntimeError(f"Cannot inspect old repository at {location}: {error}") from error
        old_id = hashlib.sha256(json.dumps(
            [str(resolved), facts.st_ino], sort_keys=True, separators=(",", ":"),
            ensure_ascii=True).encode("utf-8")).hexdigest()
        if old_id == project_id:
            _mark_project(repository, project_id)


def _json(value: dict) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _cleanup_evidence(raw: str) -> str:
    evidence = json.loads(raw)
    workspace = evidence["workspace"]
    workspace["projectId"] = workspace.pop("repositoryId")
    del workspace["checkoutId"]
    allocation = workspace["allocation"]
    allocation["projectId"] = allocation.pop("repositoryId")
    del allocation["checkoutId"]
    return _json(evidence)


def upgrade(connection):
    manifests = list(_manifests(connection))
    _mark_old_projects(manifests)
    roots = {(manifest["workspaceId"], manifest["manifestSha256"]): manifest["checkoutRoot"]
             for manifest in manifests}

    goals = []
    for event in connection.execute("SELECT payload_json,created_at FROM events"
                                    " WHERE kind='workflow.macro_task_created'"):
        payload = json.loads(event["payload_json"])
        if payload["description"] is not None:
            goals.append((payload["macroTaskId"], 1, payload["description"], event["created_at"]))
    connection.execute("DELETE FROM events WHERE kind='workflow.macro_task_created'")

    macro_tasks = [dict(row) for row in connection.execute("SELECT rowid,* FROM macro_tasks")]
    connection.execute("DROP TABLE macro_tasks")
    connection.execute(MACRO_TASKS_DDL)
    columns = ("rowid", "macro_task_id", "title", "project_id", "source_host_id", "created_at",
               "activity_seq", "activity_at")
    insert = f"INSERT INTO macro_tasks({','.join(columns)}) VALUES({','.join('?' for _ in columns)})"
    for row in macro_tasks:
        connection.execute(insert, tuple(row[column] for column in columns))
    for statement in MACRO_TASKS_INDEXES:
        connection.execute(statement)
    connection.execute(MACRO_TASK_GOALS_DDL)
    connection.executemany("INSERT INTO macro_task_goals(macro_task_id,revision,body,created_at) VALUES(?,?,?,?)", goals)

    # Retire unstarted cleanup admissions and their receipts, as in 31 → 32.
    connection.execute("DELETE FROM events WHERE json_extract(payload_json,'$.planId') IN"
                       " (SELECT plan_id FROM workspace_cleanup_plans WHERE state IN ('planned','blocked'))")
    connection.execute("DELETE FROM workspace_cleanup_plans WHERE state IN ('planned','blocked')")

    reservations = [dict(row) for row in connection.execute("SELECT rowid,* FROM workspace_reservations")]
    connection.execute("DROP TABLE workspace_reservations")
    connection.execute(RESERVATIONS_DDL)
    columns = ("rowid", "reservation_id", "workspace_id", "checkout_root", "access",
               "holder_task_id", "holder_kind", "parent_run_id", "manifest_sha256", "state", "created_at", "released_at")
    insert = f"INSERT INTO workspace_reservations({','.join(columns)}) VALUES({','.join('?' for _ in columns)})"
    for row in reservations:
        key = (row["workspace_id"], row["manifest_sha256"])
        connection.execute(insert, (row["rowid"], row["reservation_id"], row["workspace_id"],
                                    roots[key], row["access"], row["holder_task_id"],
                                    row["holder_kind"], row["parent_run_id"], row["manifest_sha256"],
                                    row["state"], row["created_at"], row["released_at"]))
    for statement in RESERVATIONS_INDEXES:
        connection.execute(statement)

    integrations = [dict(row) for row in connection.execute("SELECT rowid,* FROM workflow_integrations")]
    connection.execute("DROP TABLE workflow_integrations")
    connection.execute(INTEGRATIONS_DDL)
    columns = ("rowid", "integration_id", "run_id", "artifact_id", "strategy", "binding_sha256",
               "target_path", "target_ref", "target_project_id", "source_commit", "source_tree",
               "before_commit", "after_commit", "before_tree", "after_tree", "verification_json", "note",
               "command_id", "actor_role", "actor_id", "created_at")
    insert = f"INSERT INTO workflow_integrations({','.join(columns)}) VALUES({','.join('?' for _ in columns)})"
    for row in integrations:
        connection.execute(insert, (row["rowid"], row["integration_id"], row["run_id"], row["artifact_id"],
                                    row["strategy"], row["binding_sha256"], row["target_path"], row["target_ref"],
                                    None, row["source_commit"], row["source_tree"],
                                    row["before_commit"], row["after_commit"], row["before_tree"], row["after_tree"],
                                    row["verification_json"], row["note"], row["command_id"], row["actor_role"],
                                    row["actor_id"], row["created_at"]))
    connection.execute(INTEGRATIONS_INDEX)

    plans = [dict(row) for row in connection.execute("SELECT rowid,* FROM workspace_cleanup_plans")]
    connection.execute("DROP TABLE workspace_cleanup_plans")
    connection.execute(CLEANUP_DDL)
    columns = ("rowid", "plan_id", "run_id", "workspace_id", "path", "kind", "state",
               "evidence_json", "retention_json", "reasons_json", "result_json", "revision", "created_at", "applied_at")
    insert = f"INSERT INTO workspace_cleanup_plans({','.join(columns)}) VALUES({','.join('?' for _ in columns)})"
    for row in plans:
        connection.execute(insert, (row["rowid"], row["plan_id"], row["run_id"], row["workspace_id"],
                                    row["path"], row["kind"], row["state"],
                                    _cleanup_evidence(row["evidence_json"]), row["retention_json"],
                                    row["reasons_json"], row["result_json"], row["revision"],
                                    row["created_at"], row["applied_at"]))
    connection.execute(CLEANUP_INDEX)
    connection.execute("UPDATE meta SET value='33' WHERE key='schema_version'")
