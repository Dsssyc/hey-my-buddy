"""Schema 35 keeps frozen evidence while retiring Host control stock."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from blackboard.store.upgrade_fixture import load_fixture, load_workspace_growth_stock
from hey_my_buddy.blackboard.store.upgrade import upgrade_state
from hey_my_buddy.install.upgrade import _retire_host_files


class Schema34To35Tests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='schema-35-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def test_floor_writer_stock_converts_only_mutable_intent_and_keeps_seals(self):
        fixture = load_fixture(self.root / 'state')
        load_workspace_growth_stock(fixture, stage='floor30')
        path = fixture['state'] / 'board.sqlite3'
        with sqlite3.connect(path) as connection:
            connection.row_factory = sqlite3.Row
            expected_projects = {row[0] for row in connection.execute('SELECT project_id FROM macro_tasks')}
            expected_projects.update(json.loads(row[0])['repositoryId'] for row in connection.execute(
                "SELECT manifest_json FROM workflow_artifacts WHERE kind='input'"))
            self.assertIn('role', {row['name'] for row in connection.execute('PRAGMA table_info(workflow_children)')})
            self.assertGreater(connection.execute("SELECT COUNT(*) FROM workflow_children WHERE role='helper'").fetchone()[0], 0)
            selected = connection.execute("SELECT task_id,spec_json FROM tasks"
                                          " WHERE json_array_length(json_extract(spec_json,'$.exclusiveResources'))>0").fetchone()
            self.assertEqual(json.loads(selected['spec_json'])['requiredCapabilities'], ['workspace'])
            self.assertEqual(json.loads(selected['spec_json'])['exclusiveResources'], ['fixture:exclusive'])
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM resource_claims"
                                                " WHERE kind='exclusive' AND state='released'").fetchone()[0], 1)
            retained_commands = {row[0]: row[1] for row in connection.execute(
                "SELECT command_id,response_json FROM commands WHERE kind NOT GLOB 'workflow.*'"
                " AND kind NOT IN ('task.retry','task.acknowledge')")}
            self.assertEqual({row[0] for row in connection.execute(
                "SELECT kind FROM commands WHERE kind IN ('task.retry','task.acknowledge')")},
                {'task.retry','task.acknowledge'})
            self.assertTrue(retained_commands)
            self.assertGreater(connection.execute("SELECT COUNT(*) FROM commands WHERE kind GLOB 'workflow.*'").fetchone()[0], 0)
            frozen = {table: list(connection.execute(f'SELECT {key},{column} FROM {table} ORDER BY rowid'))
                      for table, key, column in (
                          ('workflow_turns', 'turn_id', 'input_json'),
                          ('workflow_artifacts', 'artifact_id', 'manifest_json'),
                          ('attempt_received_receipts', 'attempt_id', 'sha256'))}
            historical_actors = {table: {row[0]: tuple(row[1:]) for row in connection.execute(
                f'SELECT rowid,actor_role,actor_id FROM {table} ORDER BY rowid')}
                for table in ('events', 'workflow_scope_versions', 'workflow_integrations',
                              'workflow_host_conclusions')}
            bases = {row[0]: {key:value for key,value in json.loads(row[1]).items() if key in ('base','targetRef')}
                     for row in connection.execute('SELECT run_id,execution_workspace_json FROM workflow_runs')}
            self.assertTrue(any(item.get('base',{}).get('kind')=='commit' and item.get('targetRef')
                                for item in bases.values()))
            empty_manual = [row[0] for row in connection.execute(
                "SELECT continuation_id FROM workflow_continuations WHERE kind='manual' AND helper_outcomes_json='[]'")]
            self.assertTrue(empty_manual)
            self.assertTrue(any(row[1] == 'host' and row[2] is not None
                                for row in connection.execute('SELECT rowid,actor_role,actor_id FROM events')))

        self.assertEqual(upgrade_state(fixture['state']), {'sourceSchema': 30, 'targetSchema': 35})
        with sqlite3.connect(path) as connection:
            connection.row_factory = sqlite3.Row
            self.assertEqual({row['project_id'] for row in connection.execute('SELECT * FROM projects')}, expected_projects)
            self.assertEqual({row['kind'] for row in connection.execute('SELECT * FROM projects')}, {'git'})
            for table in ('macro_tasks', 'workflow_runs', 'workflow_turns'):
                self.assertEqual({row[0] for row in connection.execute(f'SELECT project_kind FROM {table}')}, {'git'})
            for table in ('workflow_runs', 'workflow_turns', 'workflow_continuations'):
                self.assertTrue({row[0] for row in connection.execute(f'SELECT checkout FROM {table}')}
                                <= {'existing', 'worktree'})
            self.assertNotIn('role', {row['name'] for row in connection.execute('PRAGMA table_info(workflow_children)')})
            self.assertEqual({row[0]: {key:value for key,value in json.loads(row[1]).items() if key in ('base','targetRef')}
                              for row in connection.execute('SELECT run_id,execution_workspace_json FROM workflow_runs')},bases)
            for continuation_id in empty_manual:
                self.assertIsNone(connection.execute('SELECT helper_policy FROM workflow_continuations WHERE continuation_id=?',
                                                      (continuation_id,)).fetchone()[0])
            for table, rows in frozen.items():
                key, column = {'workflow_turns': ('turn_id', 'input_json'),
                               'workflow_artifacts': ('artifact_id', 'manifest_json'),
                               'attempt_received_receipts': ('attempt_id', 'sha256')}[table]
                self.assertEqual(list(connection.execute(f'SELECT {key},{column} FROM {table} ORDER BY rowid')), rows)
            for table, rows in historical_actors.items():
                retained = {row[0]: tuple(row[1:]) for row in connection.execute(
                    f'SELECT rowid,actor_role,actor_id FROM {table} ORDER BY rowid')
                    if row[0] in rows}
                self.assertTrue(retained, table)
                self.assertEqual(retained, {key: rows[key] for key in retained})
            spec, digest = connection.execute('SELECT spec_json,input_fingerprint FROM tasks WHERE task_id=?',
                                              (selected['task_id'],)).fetchone()
            self.assertEqual(set(json.loads(spec)) & {'requiredCapabilities', 'exclusiveResources'}, set())
            self.assertEqual(digest, hashlib.sha256(('2:' + spec).encode()).hexdigest())
            self.assertGreater(connection.execute("SELECT COUNT(*) FROM tasks"
                                                        " WHERE adapter='decision'"
                                                        " AND json_array_length(json_extract(spec_json,'$.requiredCapabilities'))>0").fetchone()[0], 0)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM resource_claims WHERE kind='exclusive'").fetchone()[0], 0)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM commands WHERE kind GLOB 'workflow.*'").fetchone()[0], 0)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM commands WHERE kind IN ('task.retry','task.acknowledge')").fetchone()[0], 0)
            self.assertEqual({row[0]: row[1] for row in connection.execute(
                "SELECT command_id,response_json FROM commands WHERE kind NOT GLOB 'workflow.*'")}, retained_commands)
            self.assertEqual(connection.execute('SELECT COUNT(*) FROM workflow_runs WHERE creation_key IS NOT NULL').fetchone()[0], 0)
            self.assertEqual(connection.execute('SELECT COUNT(*) FROM macro_tasks WHERE creation_key IS NOT NULL').fetchone()[0], 0)
            for macro in connection.execute('SELECT * FROM macro_tasks'):
                first = connection.execute("SELECT json_extract(t.spec_json,'$.cwd') FROM workflow_runs r"
                                           " JOIN tasks t ON t.task_id=r.run_id WHERE r.macro_task_id=?"
                                           " ORDER BY r.created_at,r.rowid LIMIT 1", (macro['macro_task_id'],)).fetchone()
                self.assertEqual(macro['project_cwd'], first[0])
                self.assertTrue(macro['repository_path'])
            self.assertEqual(list(connection.execute('PRAGMA foreign_key_check')), [])
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute("UPDATE resource_claims SET kind='exclusive'")

    def test_response_slots_and_nullable_creation_keys_have_database_fences(self):
        fixture = load_fixture(self.root / 'state')
        upgrade_state(fixture['state'])
        with sqlite3.connect(fixture['state'] / 'board.sqlite3') as connection:
            for table, key in (('workflow_requests', 'request_id'),
                               ('workflow_artifacts', 'artifact_id'),
                               ('workflow_turns', 'turn_id')):
                identity = connection.execute(f'SELECT {key} FROM {table} LIMIT 1').fetchone()[0]
                with self.subTest(table=table), self.assertRaises(sqlite3.IntegrityError):
                    connection.execute(f'UPDATE {table} SET response_kind=? WHERE {key}=?', ('continue', identity))
                connection.rollback()
            request = connection.execute('SELECT request_id FROM workflow_requests LIMIT 1').fetchone()[0]
            changed = connection.execute("UPDATE workflow_requests SET response_kind='decision',response_id='recorded'"
                                         " WHERE request_id=? AND response_kind IS NULL", (request,))
            self.assertEqual(changed.rowcount, 1)
            changed = connection.execute("UPDATE workflow_requests SET response_kind='decision',response_id='other'"
                                         " WHERE request_id=? AND response_kind IS NULL", (request,))
            self.assertEqual(changed.rowcount, 0)
            macros = [row[0] for row in connection.execute('SELECT macro_task_id FROM macro_tasks ORDER BY rowid LIMIT 2')]
            connection.execute('UPDATE macro_tasks SET creation_key=? WHERE macro_task_id=?', ('same-key', macros[0]))
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute('UPDATE macro_tasks SET creation_key=? WHERE macro_task_id=?', ('same-key', macros[1]))
            connection.rollback()
            runs = [row[0] for row in connection.execute('SELECT run_id FROM workflow_runs ORDER BY rowid LIMIT 2')]
            connection.execute('UPDATE workflow_runs SET creation_key=? WHERE run_id=?', ('same-run-key', runs[0]))
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute('UPDATE workflow_runs SET creation_key=? WHERE run_id=?', ('same-run-key', runs[1]))
            connection.rollback()
            connection.execute("INSERT INTO events(kind,payload_json,actor_role,actor_id,created_at)"
                               " VALUES('host.response','{}','host',NULL,'2026-01-01T00:00:00.000Z')")
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute("INSERT INTO events(kind,payload_json,actor_role,actor_id,created_at)"
                                   " VALUES('lead.response','{}','lead',NULL,'2026-01-01T00:00:00.000Z')")

    def test_verified_install_cleanup_targets_only_retired_host_directories(self):
        state = self.root / 'state'
        state.mkdir(mode=0o700)
        for name in ('controls', 'submissions'):
            directory = state / name
            directory.mkdir(mode=0o700)
            (directory / 'old.json').write_text('{}', encoding='utf-8')
        (state / 'control.json').write_text('{}', encoding='utf-8')
        self.assertEqual(_retire_host_files(state), ['controls', 'submissions'])
        self.assertFalse((state / 'controls').exists())
        self.assertFalse((state / 'submissions').exists())
        self.assertTrue((state / 'control.json').exists())
        self.assertEqual(_retire_host_files(state), [])
