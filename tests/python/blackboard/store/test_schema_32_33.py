"""Private Git repositories exercise the external part of schema 32 → 33."""

from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import tempfile
import unittest
from unittest import mock

from blackboard.store.upgrade_fixture import load_fixture
from hey_my_buddy.blackboard.store import schema_30_31, schema_31_32, schema_32_33, upgrade
from hey_my_buddy.blackboard.store.db import Database


class Schema33Tests(unittest.TestCase):
    def setUp(self):
        self.scratch = tempfile.TemporaryDirectory(prefix='schema33-private-')
        self.addCleanup(self.scratch.cleanup)
        self.root = Path(self.scratch.name)
        self.fixture = load_fixture(self.root / 'state')
        self.state = self.fixture['state']
        self.repository = self.fixture['fixtureRoot'] / 'repository' / '.git'
        with closing(sqlite3.connect(self.state / 'board.sqlite3')) as connection:
            connection.row_factory = sqlite3.Row
            with connection:
                schema_30_31.upgrade(connection)
                schema_31_32.upgrade(connection)

    def schema(self):
        with closing(sqlite3.connect(self.state / 'board.sqlite3')) as connection:
            return connection.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0]

    def project(self, repository=None):
        return schema_32_33._existing_project(repository or self.repository)

    def test_old_sealed_identity_marks_private_repository_and_matches_fresh_schema(self):
        with closing(sqlite3.connect(self.state / 'board.sqlite3')) as connection:
            row = connection.execute('SELECT workspace_manifest_json FROM workflow_runs LIMIT 1').fetchone()
            old_id = json.loads(row[0])['repositoryId']
        self.assertIsNone(self.project())
        self.assertEqual(upgrade.upgrade_state(self.state), {'sourceSchema': 32, 'targetSchema': 33})
        self.assertEqual(self.project(), old_id.encode())
        with closing(sqlite3.connect(self.state / 'board.sqlite3')) as migrated:
            reservation = migrated.execute('SELECT workspace_id,manifest_sha256,checkout_root FROM workspace_reservations LIMIT 1').fetchone()
            manifest = migrated.execute('SELECT workspace_manifest_json FROM workflow_runs WHERE json_extract(workspace_manifest_json,\'$.workspaceId\')=?', (reservation[0],)).fetchone()
            self.assertEqual(reservation[2], json.loads(manifest[0])['checkoutRoot'])
            fresh = Database(self.root / 'fresh')
            self.addCleanup(fresh.close)
            fresh.initialize()
            with fresh.read() as connection:
                sql = 'SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name'
                self.assertEqual([tuple(row) for row in migrated.execute(sql)],
                                 [tuple(row) for row in connection.execute(sql)])

    def test_missing_location_skips_git(self):
        self.repository.parent.rename(self.root / 'moved-repository')
        with mock.patch.object(schema_32_33, '_git', side_effect=AssertionError('Git must not run')):
            upgrade.upgrade_state(self.state)
        self.assertEqual(self.schema(), '33')

    def test_replaced_location_skips_git_without_touching_replacement(self):
        original = self.repository.parent
        moved = self.root / 'moved-repository'
        original.rename(moved)
        original.mkdir()
        subprocess.run(['git', 'init', '--quiet', str(original)], check=True, capture_output=True)
        with mock.patch.object(schema_32_33, '_git', side_effect=AssertionError('Git must not run')):
            upgrade.upgrade_state(self.state)
        self.assertEqual(self.schema(), '33')
        self.assertIsNone(self.project(original / '.git'))
        self.assertIsNone(self.project(moved / '.git'))

    def test_unsealed_macro_and_integration_locations_do_not_gain_markers(self):
        independent = self.root / 'unsealed-target'
        subprocess.run(['git', 'init', '--quiet', str(independent)], check=True, capture_output=True)
        git_dir = independent / '.git'
        old_id = hashlib.sha256(json.dumps([str(git_dir.resolve()), git_dir.stat().st_ino],
                                           ensure_ascii=True, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
        with closing(sqlite3.connect(self.state / 'board.sqlite3')) as connection, connection:
            connection.execute('UPDATE macro_tasks SET project_id=?,project_path=? WHERE macro_task_id='
                               '(SELECT macro_task_id FROM macro_tasks LIMIT 1)', (old_id, str(git_dir)))
            connection.execute('UPDATE workflow_integrations SET target_repository_id=?,target_path=? WHERE integration_id='
                               '(SELECT integration_id FROM workflow_integrations LIMIT 1)', (old_id, str(independent)))
        upgrade.upgrade_state(self.state)
        self.assertIsNone(self.project(git_dir))

    def test_inherited_git_environment_cannot_redirect_marker(self):
        other = self.root / 'other-repository'
        subprocess.run(['git', 'init', '--quiet', str(other)], check=True, capture_output=True)
        with mock.patch.dict(os.environ, {'GIT_DIR': str(other / '.git'),
                                          'GIT_INDEX_FILE': str(other / '.git' / 'index')}):
            upgrade.upgrade_state(self.state)
        self.assertIsNotNone(self.project())
        self.assertIsNone(self.project(other / '.git'))

    @unittest.skipIf(os.name == 'nt', 'Executable Git hooks use a POSIX shell here')
    def test_repository_reference_hook_does_not_run_during_upgrade(self):
        hook = self.repository / 'hooks' / 'reference-transaction'
        hook.write_text('#!/bin/sh\nexit 91\n', encoding='utf-8')
        hook.chmod(0o755)
        upgrade.upgrade_state(self.state)
        self.assertIsNotNone(self.project())

    def test_stat_permission_failure_keeps_schema_32(self):
        failure = PermissionError(13, 'Permission denied', str(self.repository))
        with mock.patch.object(Path, 'stat', side_effect=failure):
            with self.assertRaisesRegex(RuntimeError, 'Cannot inspect old repository') as error:
                upgrade.upgrade_state(self.state)
        self.assertIn(str(self.repository), str(error.exception))
        self.assertEqual(self.schema(), '32')
        self.assertIsNone(self.project())

    def test_different_existing_marker_fails_and_rolls_back(self):
        schema_32_33._mark_project(self.repository, 'another-project')
        with self.assertRaisesRegex(RuntimeError, 'Different project marker'):
            upgrade.upgrade_state(self.state)
        self.assertEqual(self.schema(), '32')
        self.assertEqual(self.project(), b'another-project')

    def test_git_error_reports_location_and_output_and_rolls_back(self):
        original = subprocess.run

        def fail(command, *args, **kwargs):
            if 'for-each-ref' in command:
                return subprocess.CompletedProcess(command, 128, b'diagnostic-out', b'diagnostic-err')
            return original(command, *args, **kwargs)

        with mock.patch.object(schema_32_33.subprocess, 'run', side_effect=fail):
            with self.assertRaisesRegex(RuntimeError, 'diagnostic-err') as error:
                upgrade.upgrade_state(self.state)
        self.assertIn(str(self.repository), str(error.exception))
        self.assertIn('diagnostic-out', str(error.exception))
        self.assertEqual(self.schema(), '32')

    def test_marker_survives_database_rollback_and_retry(self):
        with mock.patch.object(schema_32_33, '_cleanup_evidence', side_effect=RuntimeError('injected after marker')):
            with self.assertRaisesRegex(RuntimeError, 'injected after marker'):
                upgrade.upgrade_state(self.state)
        self.assertEqual(self.schema(), '32')
        marked = self.project()
        self.assertIsNotNone(marked)
        upgrade.upgrade_state(self.state)
        self.assertEqual(self.schema(), '33')
        self.assertEqual(self.project(), marked)

