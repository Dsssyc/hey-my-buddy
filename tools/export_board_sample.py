"""Export the most recently accepted micro tasks, at most 100, for disposable
source-checkout development.

Run from the repository root:
    uv run --frozen python tools/export_board_sample.py OUTPUT [--source STATE]

Only console history, its fixed session records, and inquiry journals belonging
to selected attempts are exported. The destination retains the source schema
and is published only after the candidate passes the stop checks.
"""
from __future__ import annotations

import argparse
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import sqlite3
import sys
import tempfile

# Always use this checkout, even when invoked from a pinned installed execution.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from hey_my_buddy import private_dirs
from hey_my_buddy.blackboard.store.db import DB_FILE, SCHEMA_VERSION, SECRET_KEY, initialize_defaults
from hey_my_buddy.blackboard.store.upgrade import UPGRADE_FLOOR_SCHEMA
from hey_my_buddy.blackboard.routing.statistics import backfill
from hey_my_buddy.blackboard.tasks.macro_tasks import RUN_GROUPS_SQL
from hey_my_buddy.protocol.workspace_manifest import resolve_manifest, resolve_record, cumulative_patch_error
from hey_my_buddy.state_root import get_state_dir

MAX_TASKS = 100


def _rows(connection, table: str, column: str, values) -> list[dict]:
    values = tuple(values)
    if not values:
        return []
    return [dict(row) for row in connection.execute(
        f'SELECT * FROM "{table}" WHERE "{column}" IN ({",".join("?" for _ in values)}) ORDER BY rowid',
        values)]


def _session_groups(records: dict, groups: dict[str, str]) -> dict[str, str]:
    # Session locations need only ownership links, independent of the current
    # history reader's presentation columns and their stored forms.
    result = dict(groups)
    routed_groups = {row['decision_id']: groups[row['run_id']] for row in records['workflow_routes']}
    for dispatch in records['router_dispatches']:
        if dispatch['decision_id'] in routed_groups:
            result[dispatch['task_id']] = routed_groups[dispatch['decision_id']]
    return result


def _rebind_workspace_owners(connection, records: dict) -> None:
    """Keep each selected reference's one SQL owner without importing other tasks."""
    candidates: dict[tuple[str, str], list[dict]] = {}
    for table, digest_column in (("workflow_artifacts", "input_manifest_sha256"),
                                 ("workflow_continuations", "workspace_manifest_sha256")):
        for row in records[table]:
            if table == "workflow_artifacts" and row["kind"] != "input":
                continue
            if row["workspace_facts_role"] in ("pending", "none"):
                continue
            candidates.setdefault((row["workspace_id"], row[digest_column]), []).append(row)
    for (workspace_id, digest), rows in candidates.items():
        selected_owners = [row for row in rows if row["workspace_facts_role"] == "owner"]
        if selected_owners:
            if len(selected_owners) != 1:
                raise ValueError("Selected workspace references have multiple owners")
            continue
        original = connection.execute(
            "SELECT workspace_kind,checkout_root,repository_path,input_commit,staged_commit FROM workflow_artifacts "
            "WHERE kind='input' AND workspace_facts_role='owner' AND workspace_id=? AND input_manifest_sha256=? "
            "UNION ALL SELECT workspace_kind,checkout_root,repository_path,input_commit,staged_commit FROM workflow_continuations "
            "WHERE workspace_facts_role='owner' AND workspace_id=? AND workspace_manifest_sha256=?",
            (workspace_id, digest, workspace_id, digest)).fetchall()
        if len(original) != 1:
            raise ValueError("Selected workspace reference has no unique source owner")
        chosen = rows[0]
        chosen.update(workspace_facts_role="owner", workspace_kind=original[0][0],
                      checkout_root=original[0][1], repository_path=original[0][2],
                      input_commit=original[0][3], staged_commit=original[0][4])


def _select(connection, source_schema: int) -> tuple[dict[str, list[dict]], list[tuple[str, str, str]]]:
    # The most recently accepted micro tasks are selected; the reversed list
    # writes them into the destination oldest first.
    selected = [row[0] for row in connection.execute(
        "SELECT r.run_id FROM workflow_runs r JOIN tasks t ON t.task_id=r.run_id "
        "WHERE r.state='accepted' AND t.accepted_at IS NOT NULL "
        "ORDER BY t.accepted_at DESC, r.run_id DESC LIMIT ?", (MAX_TASKS,))]
    selected.reverse()
    runs = {row["run_id"]: row for row in _rows(connection, "workflow_runs", "run_id", selected)}
    records = {"workflow_runs": [runs[run_id] for run_id in selected]}
    routes = _rows(connection, "workflow_routes", "run_id", selected)
    requests = _rows(connection, "workflow_requests", "run_id", selected)
    decisions = {row['decision_id'] for row in routes + requests if row['decision_id'] is not None}
    dispatches = _rows(connection, 'router_dispatches', 'decision_id', decisions)
    decision_requests = _rows(connection, 'decision_requests', 'decision_id', decisions)
    task_ids = set(selected) | {row['task_id'] for row in dispatches}
    task_ids.update(row['task_id'] for row in decision_requests if row['task_id'] is not None)
    records.update(workflow_routes=routes, workflow_requests=requests,
                   router_dispatches=dispatches, decision_requests=decision_requests)
    for table in ('evaluation_decisions', 'router_requests'):
        records[table] = _rows(connection, table, 'decision_id', decisions)
    for table in ('tasks', 'attempts', 'messages', 'artifacts', 'events', 'router_outcomes'):
        records[table] = _rows(connection, table, 'task_id', task_ids)
    attempts = {row['attempt_id'] for row in records['attempts']}
    for table in ('attempt_process_identities', 'attempt_interruptions', 'attempt_shutdown_evidence',
                  'execution_issues', 'attempt_activity', 'attempt_reminders', 'router_claims'):
        records[table] = _rows(connection, table, 'attempt_id', attempts)
    for table in ('workflow_turns', 'workflow_continuations', 'workflow_scope_versions',
                  'workflow_workspace_conflicts', 'workflow_integrations', 'workflow_artifacts',
                  'workflow_suggestions', 'workflow_host_conclusions'):
        records[table] = _rows(connection, table, 'run_id', selected)
    if source_schema >= 34:
        _rebind_workspace_owners(connection, records)
    # Relationships survive only when both micro tasks were selected. A selected
    # child of an omitted parent becomes a root; no omitted task is pulled in.
    for table, owner, child in (('workflow_children', 'parent_run_id', 'child_task_id'),
                                ('workflow_lead_tasks', 'lead_run_id', 'owned_run_id'),
                                ('workflow_fixes', 'run_id', 'fixed_run_id')):
        records[table] = [row for row in _rows(connection, table, owner, selected)
                          if row[child] in selected]
    groups = {row['run_id']: row['group_id'] for row in connection.execute(
        f'SELECT * FROM ({RUN_GROUPS_SQL}) WHERE run_id IN ({",".join("?" for _ in selected)})', selected)}
    macros = {group for group in groups.values() if not group.startswith('run:')}
    records['macro_tasks'] = _rows(connection, 'macro_tasks', 'macro_task_id', macros)
    if source_schema >= 35:
        project_ids = {row['project_id'] for row in records['macro_tasks']}
        project_ids.update(row['standalone_project_id'] for row in records['workflow_runs']
                           if row['standalone_project_id'] is not None)
        records['projects'] = _rows(connection, 'projects', 'project_id', project_ids)
    for run in records['workflow_runs']:
        group = groups[run['run_id']]
        if not group.startswith('run:'):
            run['macro_task_id'] = group
        # Schema 35 has no Host capability columns. Earlier source forms keep
        # their own structure, without carrying authority into the new board.
        if source_schema < 35:
            run['control_verifier'] = secrets.token_hex(32)
            run['submission_verifier'] = None
    for attempt in records['attempts']:
        attempt['nonce_verifier'] = secrets.token_hex(32)
    session_groups = _session_groups(records, groups)
    sessions = [(row['task_id'], row['attempt_id'], session_groups[row['task_id']])
                for row in records['attempts'] if row['task_id'] in session_groups]
    return records, sessions


def _source_schema(connection) -> tuple[str, list[tuple[str, str]]]:
    version = connection.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
    if version is None or version[0] not in {str(v) for v in range(UPGRADE_FLOOR_SCHEMA, SCHEMA_VERSION + 1)}:
        raise ValueError(f"Source must use schema {UPGRADE_FLOOR_SCHEMA} through {SCHEMA_VERSION}")
    definitions = [(row['type'], row['sql']) for row in connection.execute(
        "SELECT type,sql FROM sqlite_master WHERE type IN ('table','index') "
        "AND sql IS NOT NULL AND name NOT LIKE 'sqlite_%' "
        "ORDER BY CASE type WHEN 'table' THEN 0 ELSE 1 END, rowid")]
    return version[0], definitions


def _create_schema(connection, version: str, definitions: list[tuple[str, str]]) -> None:
    for _kind, ddl in definitions:
        connection.execute(ddl)
    connection.execute("INSERT INTO meta(key,value) VALUES('schema_version',?)", (version,))
    connection.execute("INSERT INTO meta(key,value) VALUES(?,?)", (SECRET_KEY, secrets.token_hex(32)))
    initialize_defaults(connection)


_PRIVATE_ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z')


def _journal_path(state: Path, task_id: str, attempt_id: str) -> Path:
    if any(not isinstance(value, str) or _PRIVATE_ID.fullmatch(value) is None or value in ('.', '..')
           for value in (task_id, attempt_id)):
        raise ValueError('Sample contains an unsafe inquiry journal identity')
    return state / 'attempts' / task_id / attempt_id / 'inquiry.results.jsonl'


def _copy_optional_regular(origin: Path, target: Path) -> None:
    try:
        fd = private_dirs.open_regular_fd(origin, os.O_RDONLY)
    except FileNotFoundError:
        return
    with os.fdopen(fd, 'rb') as stream:
        private_dirs.ensure_private_dir(target.parent)
        with os.fdopen(private_dirs.open_regular_fd(
                target, os.O_WRONLY | os.O_CREAT | os.O_EXCL), 'wb') as destination:
            shutil.copyfileobj(stream, destination)


def _copy_required_regular(origin: Path, target: Path, digest: str | None = None) -> None:
    with os.fdopen(private_dirs.open_regular_fd(origin, os.O_RDONLY), 'rb') as stream:
        private_dirs.ensure_private_dir(target.parent)
        actual = hashlib.sha256()
        with os.fdopen(private_dirs.open_regular_fd(
                target, os.O_WRONLY | os.O_CREAT | os.O_EXCL), 'wb') as destination:
            while chunk := stream.read(1024 * 1024):
                actual.update(chunk)
                destination.write(chunk)
        if digest is not None and actual.hexdigest() != digest:
            raise ValueError('Selected output record does not match its stored digest')


def _referenced_files(records: dict, source: Path, output: Path) -> list[tuple[Path, Path, str | None]]:
    """Copy only files explicitly named by selected schema 34/35 facts."""
    files: dict[Path, tuple[Path, Path, str | None]] = {}
    owners = {(row['workspace_id'], row.get('input_manifest_sha256') or row.get('workspace_manifest_sha256')): row
              for table in ('workflow_artifacts', 'workflow_continuations') for row in records[table]
              if row.get('workspace_facts_role') == 'owner'}
    originals = {row['run_id']: owners[(row['workspace_id'], row['input_manifest_sha256'])]['input_commit']
                 for row in records['workflow_artifacts'] if row['kind'] == 'input'}
    attempts = {row['attempt_id']: row for row in records['attempts']}
    for artifact in records['workflow_artifacts']:
        if artifact['content_form'] == 'input-reference':
            ref = {'workspaceId': artifact['workspace_id'],
                   'manifestSha256': artifact['input_manifest_sha256']}
            resolve_manifest(source, ref)
            relative = Path('workspaces') / ref['workspaceId'] / 'manifest.json'
            files[relative] = (source / relative, output / relative, None)
        elif artifact['content_form'] == 'record-reference':
            relative = Path(artifact['record_path'])
            if relative.is_absolute() or not relative.parts or '..' in relative.parts or relative.parts[0] != 'workspaces':
                raise ValueError('Selected output record is outside the source state')
            if relative in files and files[relative][2] != artifact['record_sha256']:
                raise ValueError('Selected output records disagree on one file identity')
            files[relative] = (source / relative, output / relative, artifact['record_sha256'])
            record = resolve_record(source, {'recordFile': {'path': relative.as_posix(), 'sha256': artifact['record_sha256']},
                                             'snapshotSha256': artifact['output_snapshot_sha256']})
            patch = relative.with_suffix('.patch')
            files[patch] = (source / patch, output / patch, record['diffSha256'])
            original = originals[artifact['run_id']]
            if (original != record['inputCommit'] and artifact['kind'] != 'abandoned-site'
                    and cumulative_patch_error(artifact, attempts.get(artifact['attempt_id'])) is None):
                cumulative = relative.parent / ('cumulative-' + original + '.patch')
                files[cumulative] = (source / cumulative, output / cumulative, None)
        elif artifact['content_form'] == 'result-reference':
            attempt = attempts[artifact['attempt_id']]
            expected = private_dirs.attempt_root(source, attempt['adapter'], attempt['task_id'],
                                                 attempt['attempt_id']) / 'result-receipt.json'
            relative = Path(artifact['record_path'])
            if (relative.is_absolute() or '..' in relative.parts
                    or relative.as_posix() != expected.relative_to(source).as_posix()):
                raise ValueError('Selected result receipt is outside its bound attempt')
            if relative in files and files[relative][2] != artifact['record_sha256']:
                raise ValueError('Selected result receipts disagree on one file identity')
            files[relative] = (source / relative, output / relative, artifact['record_sha256'])
    for turn in records['workflow_turns']:
        document = json.loads(turn['input_json'])
        if document['version'] != 4:
            continue
        ref = document['executionWorkspace']
        resolve_manifest(source, ref)
        relative = Path('workspaces') / ref['workspaceId'] / 'manifest.json'
        files[relative] = (source / relative, output / relative, None)
    for attempt in records['attempts']:
        if attempt.get('result_form') != 'current-report-v2' or attempt['result_json'] is None:
            continue
        result = (json.loads(attempt['result_json']).get('result') or {})
        for key in ('workspaceSeal', 'partialWorkspaceSeal'):
            seal = result.get(key)
            if not isinstance(seal, dict) or 'recordFile' not in seal:
                continue
            file = seal['recordFile']
            relative = Path(file['path'])
            if relative.is_absolute() or '..' in relative.parts or relative not in files or files[relative][2] != file['sha256']:
                raise ValueError('Selected result record has no matching artifact binding')
    return list(files.values())


def _validate(connection) -> None:
    # Finished is not enough on its own: an unconfirmed stop must never escape.
    # ownership is not judged: a lost connection freezes it at 'uncertain' even
    # for finished, shutdown-confirmed executions, and recovery only touches
    # unfinished ones.
    if connection.execute(
            "SELECT 1 FROM attempts WHERE execution_state!='finished' OR shutdown_confirmed!=1 "
            "OR cleanup_pending!=0 LIMIT 1").fetchone():
        raise ValueError('Sample contains an unfinished or unconfirmed execution')
    if connection.execute(
            "SELECT 1 FROM tasks WHERE state NOT IN ('completed','concluded','failed','cancelled') "
            "OR active_attempt_id IS NOT NULL LIMIT 1").fetchone():
        raise ValueError('Sample contains a nonterminal execution task')
    if connection.execute('PRAGMA foreign_key_check').fetchall():
        raise ValueError('Sample has foreign-key violations')
    if [row[0] for row in connection.execute('PRAGMA integrity_check')] != ['ok']:
        raise ValueError('Sample failed SQLite integrity_check')


def export_sample(source: Path, output: Path) -> Path:
    source = Path(source).expanduser().absolute()
    output = Path(output).expanduser().absolute()
    source = get_state_dir(source)
    if private_dirs.linked(output):
        raise ValueError('Output must not be a linked directory')
    output = output.resolve()
    if output.is_relative_to(source) or source.is_relative_to(output):
        raise ValueError('Source and output must be separate directory trees')
    if output.exists() and (not output.is_dir() or any(output.iterdir())):
        raise ValueError(f'Refusing nonempty output: {output}')
    # One short SQLite snapshot covers all selected rows and session identities.
    # Close it before building the destination or copying immutable finished sessions.
    # No product store is opened against the source; no write/WAL pragma is issued.
    with closing(sqlite3.connect((source / DB_FILE).as_uri() + '?mode=ro', uri=True)) as reader:
        reader.row_factory = sqlite3.Row
        reader.execute('BEGIN')
        version, definitions = _source_schema(reader)
        records, sessions = _select(reader, int(version))
        referenced_files = _referenced_files(records, source, output) if int(version) >= 34 else []
    with tempfile.TemporaryDirectory(prefix='.board-sample-', dir=output.parent) as temporary:
        stage = Path(temporary) / 'state'
        private_dirs.ensure_private_dir(stage)
        with closing(sqlite3.connect(stage / DB_FILE)) as writer:
            writer.row_factory = sqlite3.Row
            writer.execute('PRAGMA foreign_keys=ON')
            writer.execute('BEGIN')
            try:
                writer.execute('PRAGMA defer_foreign_keys=ON')
                _create_schema(writer, version, definitions)
                for table, rows in records.items():
                    for row in rows:
                        columns = ','.join(f'"{key}"' for key in row)
                        writer.execute(f'INSERT INTO "{table}" ({columns}) VALUES '
                                       f'({",".join("?" for _ in row)})', tuple(row.values()))
                if int(version) >= 31:
                    # Cumulative statistics were introduced in schema 31.
                    # This is a new private board containing only the selected
                    # history. Its cumulative facts describe that same history;
                    # no source-wide statistics or maintenance records escape.
                    backfill(writer)
                # The surviving lineage determines each destination session's
                # group; source group identity still locates the original file.
                target_groups = _session_groups(records, {
                    row['run_id']: row['group_id'] for row in writer.execute(RUN_GROUPS_SQL)})
                for task, attempt, group in sessions:
                    origin = private_dirs.session_record_path(source, group, attempt)
                    target = private_dirs.session_record_path(stage, target_groups[task], attempt)
                    _copy_optional_regular(origin, target)
                for attempt in records['attempts']:
                    origin = _journal_path(source, attempt['task_id'], attempt['attempt_id'])
                    target = _journal_path(stage, attempt['task_id'], attempt['attempt_id'])
                    _copy_optional_regular(origin, target)
                for origin, target, digest in referenced_files:
                    _copy_required_regular(origin, stage / target.relative_to(output), digest)
                _validate(writer)
                writer.commit()
            except BaseException:
                writer.rollback()
                raise
        os.chmod(stage / DB_FILE, 0o600)
        if output.exists():
            output.rmdir()  # Only an empty destination can be replaced.
        stage.rename(output)
    return output


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output', type=Path)
    parser.add_argument('--source', type=Path, default=None, help='Source state directory (default: product state)')
    args = parser.parse_args(argv)
    try:
        output = export_sample(get_state_dir(args.source), args.output)
    except (OSError, sqlite3.Error, ValueError) as error:
        parser.exit(1, f'Export failed: {error}\n')
    print(f'{output}\nDelete this private sample directory when finished.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
