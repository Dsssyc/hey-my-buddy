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
from hey_my_buddy.blackboard.store.db import DB_FILE, SCHEMA_VERSION, SECRET_KEY, UPGRADE_BASE_SCHEMA, utc_now
from hey_my_buddy.blackboard.tasks.macro_tasks import MEMBERS_SQL, RUN_GROUPS_SQL
from hey_my_buddy.blackboard.routing.router import initialize_configuration
from hey_my_buddy.protocol.transport import get_state_dir

MAX_TASKS = 100


def _rows(connection, table: str, column: str, values) -> list[dict]:
    values = tuple(values)
    if not values:
        return []
    return [dict(row) for row in connection.execute(
        f'SELECT * FROM "{table}" WHERE "{column}" IN ({",".join("?" for _ in values)}) ORDER BY rowid',
        values)]


def _select(connection) -> tuple[dict[str, list[dict]], list[tuple[str, str, str]]]:
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
    for run in records['workflow_runs']:
        group = groups[run['run_id']]
        if not group.startswith('run:'):
            run['macro_task_id'] = group
        # None of the source's capability verifiers belongs to the new board.
        run['control_verifier'] = secrets.token_hex(32)
        run['submission_verifier'] = None
    for attempt in records['attempts']:
        attempt['nonce_verifier'] = secrets.token_hex(32)
    sessions = [(row['task_id'], row['attempt_id'], row['group_id']) for row in connection.execute(
        f'SELECT a.task_id,a.attempt_id,m.group_id FROM attempts a '
        f'JOIN ({MEMBERS_SQL}) m ON m.task_id=a.task_id '
        f'WHERE a.task_id IN ({",".join("?" for _ in task_ids)})', tuple(task_ids))]
    return records, sessions


def _source_schema(connection) -> tuple[str, list[tuple[str, str]]]:
    version = connection.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
    if version is None or version[0] not in (str(UPGRADE_BASE_SCHEMA), str(SCHEMA_VERSION)):
        raise ValueError(f"Source must use schema {UPGRADE_BASE_SCHEMA} or {SCHEMA_VERSION}")
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
    initialize_configuration(connection)
    connection.execute(
        'INSERT INTO reminder_settings(id,silence_seconds,updated_at,revision) VALUES(1,900,?,1)',
        (utc_now(),))


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
    for path in (source, output):
        if private_dirs.linked_component(path) is not None:
            raise ValueError('Source and output must not contain linked components')
    source, output = source.resolve(), output.resolve()
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
        records, sessions = _select(reader)
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
                # The surviving lineage determines each destination session's
                # group; source group identity still locates the original file.
                target_groups = {row['task_id']: row['group_id'] for row in writer.execute(
                    f'SELECT task_id,group_id FROM ({MEMBERS_SQL})')}
                for task, attempt, group in sessions:
                    origin = private_dirs.session_record_path(source, group, attempt)
                    target = private_dirs.session_record_path(stage, target_groups[task], attempt)
                    _copy_optional_regular(origin, target)
                for attempt in records['attempts']:
                    origin = _journal_path(source, attempt['task_id'], attempt['attempt_id'])
                    target = _journal_path(stage, attempt['task_id'], attempt['attempt_id'])
                    _copy_optional_regular(origin, target)
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
