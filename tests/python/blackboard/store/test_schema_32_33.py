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
from hey_my_buddy.blackboard.store.db import Database, SCHEMA_VERSION
from support import unlink_read_only


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

    def upgrade33(self):
        # These tests target the already frozen 32 → 33 step, including
        # deliberately inconsistent old inputs unrelated to the next boundary.
        with mock.patch.object(upgrade, 'SCHEMA_VERSION', 33):
            return upgrade.upgrade_state(self.state)

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
        self.assertEqual(upgrade.upgrade_state(self.state), {'sourceSchema': 32, 'targetSchema': SCHEMA_VERSION})
        self.assertEqual(self.project(), old_id.encode())
        with closing(sqlite3.connect(self.state / 'board.sqlite3')) as migrated:
            self.assertEqual({row[0] for row in migrated.execute(
                'SELECT target_project_id FROM workflow_integrations')}, {None})
            reservation = migrated.execute('SELECT workspace_id,manifest_sha256,checkout_root FROM workspace_reservations LIMIT 1').fetchone()
            manifest = migrated.execute("SELECT manifest_json FROM workflow_artifacts WHERE kind='input' AND workspace_id=?", (reservation[0],)).fetchone()
            self.assertEqual(reservation[2], json.loads(manifest[0])['checkoutRoot'])
            fresh = Database(self.root / 'fresh')
            self.addCleanup(fresh.close)
            fresh.initialize()
            with fresh.read() as connection:
                sql = 'SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name'
                self.assertEqual([tuple(row) for row in migrated.execute(sql)],
                                 [tuple(row) for row in connection.execute(sql)])
                self.assertEqual([row[1] for row in migrated.execute('PRAGMA table_info(macro_task_goals)')],
                                 ['macro_task_id', 'revision', 'body', 'created_at'])
                self.assertEqual([row[1] for row in migrated.execute('PRAGMA table_info(allocation_revocations)')],
                                 ['allocation_id', 'request_id', 'revoked_at'])
                self.assertEqual(migrated.execute('SELECT COUNT(*) FROM allocation_revocations').fetchone()[0], 0)
                self.assertNotIn('project_path',
                                 {row[1] for row in migrated.execute('PRAGMA table_info(macro_tasks)')})

    def test_missing_location_skips_git(self):
        self.repository.parent.rename(self.root / 'moved-repository')
        with mock.patch.object(schema_32_33, '_git', side_effect=AssertionError('Git must not run')):
            self.upgrade33()
        self.assertEqual(self.schema(), '33')

    def test_git_directory_without_files_is_skipped_and_other_repository_is_marked(self):
        other = self.root / 'other-repository'
        subprocess.run(['git', 'init', '--quiet', str(other)], check=True, capture_output=True)
        other_git = other / '.git'
        other_id = hashlib.sha256(json.dumps([str(other_git.resolve()), other_git.stat().st_ino],
                                             ensure_ascii=True, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
        with closing(sqlite3.connect(self.state / 'board.sqlite3')) as connection, connection:
            row = connection.execute('SELECT run_id,workspace_manifest_json FROM workflow_runs LIMIT 1').fetchone()
            manifest = json.loads(row[1])
            old_id = manifest['repositoryId']
            manifest['repositoryId'] = other_id
            manifest['snapshot']['repositoryPath'] = str(other_git)
            connection.execute('UPDATE workflow_runs SET workspace_manifest_json=? WHERE run_id=?',
                               (json.dumps(manifest), row[0]))

        (missing_id, missing), (marked_id, marked) = sorted(((old_id, self.repository), (other_id, other_git)))
        # Always empty the first candidate, so returning instead of continuing
        # cannot pass merely because the healthy repository was visited first.
        for path in missing.rglob('*'):
            if path.is_file():
                unlink_read_only(path)
        directories = {path.relative_to(missing) for path in missing.rglob('*')}
        self.assertTrue(directories)
        self.assertTrue(all(path.is_dir() for path in missing.rglob('*')))
        self.assertEqual(hashlib.sha256(json.dumps([str(missing.resolve()), missing.stat().st_ino],
                                                   ensure_ascii=True, sort_keys=True, separators=(',', ':')).encode()).hexdigest(),
                         missing_id)

        self.assertEqual(self.upgrade33(), {'sourceSchema': 32, 'targetSchema': 33})
        self.assertEqual(self.schema(), '33')
        self.assertEqual({path.relative_to(missing) for path in missing.rglob('*')}, directories)
        self.assertTrue(all(path.is_dir() for path in missing.rglob('*')))
        self.assertFalse((missing / schema_32_33.PROJECT_REF).exists())
        self.assertEqual(self.project(marked), marked_id.encode())

    @unittest.skipUnless(os.name == 'posix' and os.geteuid() != 0,
                         'chmod 000 requires POSIX and a non-root user')
    def test_unreadable_git_directory_fails_upgrade_without_writing(self):
        before = {path.relative_to(self.repository): path.read_bytes()
                  for path in self.repository.rglob('*') if path.is_file()}
        mode = self.repository.stat().st_mode
        self.repository.chmod(0)
        try:
            with self.assertRaisesRegex(RuntimeError, 'Cannot inspect old repository') as error:
                self.upgrade33()
            self.assertIn(str(self.repository), str(error.exception))
            self.assertIsInstance(error.exception.__cause__, PermissionError)
        finally:
            self.repository.chmod(mode)
        self.assertEqual(self.schema(), '32')
        self.assertEqual({path.relative_to(self.repository): path.read_bytes()
                          for path in self.repository.rglob('*') if path.is_file()}, before)
        self.assertIsNone(self.project())

    def test_replaced_location_skips_git_without_touching_replacement(self):
        original = self.repository.parent
        moved = self.root / 'moved-repository'
        original.rename(moved)
        original.mkdir()
        subprocess.run(['git', 'init', '--quiet', str(original)], check=True, capture_output=True)
        with mock.patch.object(schema_32_33, '_git', side_effect=AssertionError('Git must not run')):
            self.upgrade33()
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
        self.upgrade33()
        self.assertIsNone(self.project(git_dir))
        with closing(sqlite3.connect(self.state / 'board.sqlite3')) as connection:
            self.assertEqual({row[0] for row in connection.execute(
                'SELECT target_project_id FROM workflow_integrations')}, {None})

    def test_missing_cleanup_evidence_key_rolls_back(self):
        with closing(sqlite3.connect(self.state / 'board.sqlite3')) as connection, connection:
            row = connection.execute('SELECT plan_id,evidence_json FROM workspace_cleanup_plans LIMIT 1').fetchone()
            evidence = json.loads(row[1])
            del evidence['workspace']['checkoutId']
            connection.execute('UPDATE workspace_cleanup_plans SET evidence_json=? WHERE plan_id=?',
                               (json.dumps(evidence), row[0]))
        with self.assertRaises(KeyError):
            self.upgrade33()
        self.assertEqual(self.schema(), '32')

    def test_inherited_git_environment_cannot_redirect_marker(self):
        other = self.root / 'other-repository'
        subprocess.run(['git', 'init', '--quiet', str(other)], check=True, capture_output=True)
        with mock.patch.dict(os.environ, {'GIT_DIR': str(other / '.git'),
                                          'GIT_INDEX_FILE': str(other / '.git' / 'index')}):
            self.upgrade33()
        self.assertIsNotNone(self.project())
        self.assertIsNone(self.project(other / '.git'))

    @unittest.skipIf(os.name == 'nt', 'Executable Git hooks use a POSIX shell here')
    def test_repository_reference_hook_does_not_run_during_upgrade(self):
        hook = self.repository / 'hooks' / 'reference-transaction'
        hook.write_text('#!/bin/sh\nexit 91\n', encoding='utf-8')
        hook.chmod(0o755)
        self.upgrade33()
        self.assertIsNotNone(self.project())

    def test_stat_permission_failure_keeps_schema_32(self):
        failure = PermissionError(13, 'Permission denied', str(self.repository))
        with mock.patch.object(Path, 'stat', side_effect=failure):
            with self.assertRaisesRegex(RuntimeError, 'Cannot inspect old repository') as error:
                self.upgrade33()
        self.assertIn(str(self.repository), str(error.exception))
        self.assertEqual(self.schema(), '32')
        self.assertIsNone(self.project())

    def test_different_existing_marker_fails_and_rolls_back(self):
        schema_32_33._mark_project(self.repository, 'another-project')
        with self.assertRaisesRegex(RuntimeError, 'Different project marker'):
            self.upgrade33()
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
                self.upgrade33()
        self.assertIn(str(self.repository), str(error.exception))
        self.assertIn('diagnostic-out', str(error.exception))
        self.assertEqual(self.schema(), '32')

    def test_marker_survives_database_rollback_and_retry(self):
        with mock.patch.object(schema_32_33, '_cleanup_evidence', side_effect=RuntimeError('injected after marker')):
            with self.assertRaisesRegex(RuntimeError, 'injected after marker'):
                self.upgrade33()
        self.assertEqual(self.schema(), '32')
        marked = self.project()
        self.assertIsNotNone(marked)
        self.upgrade33()
        self.assertEqual(self.schema(), '33')
        self.assertEqual(self.project(), marked)

