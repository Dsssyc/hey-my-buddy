"""Disposable real Git allocations, retained-board facts and apply-time fences."""
import json
import os
from pathlib import Path
import subprocess
import shutil
import sqlite3
import threading
from types import SimpleNamespace
from unittest.mock import patch

from blackboard.tasks.test_workflow import WorkflowTestCase
from support import private_state_dir
from hey_my_buddy.blackboard.tasks import storage, workflow, workspace, workspace_identity


class StorageOrphanTests(WorkflowTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # Reuse immutable Git input preparation, not boards or mutable checkouts.
        # Existing private-state cleanup still verifies service/worker locks.
        root = cls.enterClassContext(private_state_dir(prefix='storage-source-'))
        cls.source_template = root / 'source'
        cls.source_template.mkdir()
        builder = cls()
        builder.git(cls.source_template, 'init', '-q')
        builder.git(cls.source_template, 'config', 'user.name', 'Storage test')
        builder.git(cls.source_template, 'config', 'user.email', 'storage@example.invalid')
        (cls.source_template / 'tracked.txt').write_text('input\n')
        (cls.source_template / '.gitignore').write_text('ignored/\n')
        builder.git(cls.source_template, 'add', '.')
        builder.git(cls.source_template, 'commit', '-qm', 'private input')

    def setUp(self):
        super().setUp()
        self.workspace = workspace
        workflow._workspace_module = workspace
        self.repo = self.workdir('source').resolve()
        shutil.copytree(self.source_template, self.repo, dirs_exist_ok=True)
        self.board_instance = self.board()
        self.enterContext(patch.object(storage, 'process_inventory', return_value=([], [], True)))

    def git(self, root, *args):
        env = {key: value for key, value in os.environ.items() if not key.startswith('GIT_')}
        result = subprocess.run(['git', '-C', str(root), *args], capture_output=True, text=True, env=env, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout.strip()

    def orphan(self):
        intent = {'kind': 'worktree', 'cwd': str(self.repo), 'access': 'write',
                  'base': {'kind': 'working-tree'}, 'includeUntracked': [],
                  'writeScope': ['.'], 'integrator': 'host:host-1'}
        manifest = workspace.prepare(self.directory, 'orphan-request', intent)
        checkout = Path(manifest['checkoutRoot'])
        planned = self.board_instance.call('storage_plan', {})
        self.assertTrue(any(row['path'] == str(checkout) for row in planned['candidates']),
                        'The original unregistered allocation must appear in storage candidates')
        row = next(row for row in planned['candidates'] if row['path'] == str(checkout))
        self.assertTrue(row['eligible'], row['reasons'])
        self.assertTrue(row['orphan'])
        self.assertEqual(row['proof']['locked'], 'buddy:' + manifest['workspaceId'])
        return manifest, checkout, planned

    def apply(self, planned):
        return self.board_instance.call('storage_apply', {'planId': planned['planId'],
                                                         'commandId': 'apply-orphans', 'confirm': True})

    def assert_retained(self, checkout, planned):
        result = self.apply(planned)
        self.assertTrue(checkout.exists() or checkout.is_symlink())
        self.assertEqual(result['removed'], [])
        self.assertTrue(result['skipped'])
        return result

    def accept_at_orphan(self, manifest, checkout):
        """A real public acceptance verifies a ref without switching target HEAD."""
        board = self.board_instance
        delivered = self.submit(board, request_id='integration-source', cwd=str(self.repo), kind='existing')
        self.register(board)
        claim = self.claim(board, run_id=delivered['runId'])
        (self.repo / 'tracked.txt').write_text('sealed integration output\n')
        self.finish_turn(board, claim)
        view = board.call('workflow_get', {'runId': delivered['runId']})
        with board.store.db.read() as connection:
            sealed = json.loads(connection.execute(
                'SELECT manifest_json FROM workflow_artifacts WHERE artifact_id=?',
                (view['finalArtifactId'],)).fetchone()[0])
        accepted = board.call('workflow_accept', {
            'runId': delivered['runId'], 'artifactId': view['finalArtifactId'],
            'note': 'Private ref integration regression', 'keepCheckout': True,
            'target': {'path': str(checkout), 'ref': sealed['commit']}, **self.control(view)})
        self.assertEqual(accepted['integration']['state'], 'verified')
        self.assertEqual(accepted['integration']['target']['path'], str(checkout))
        self.assertEqual(self.git(checkout, 'rev-parse', 'HEAD'), manifest['inputCommit'])
        self.assertEqual((checkout / 'tracked.txt').read_text(), 'input\n')
        return accepted

    def test_public_integration_before_inventory_protects_unchanged_target(self):
        manifest, checkout, _ = self.orphan()
        self.accept_at_orphan(manifest, checkout)
        inspected = storage.inspect(self.board_instance.store)
        row = next(row for row in inspected['candidates'] if row['path'] == str(checkout))
        self.assertFalse(row['eligible'], row['reasons'])
        self.assertIn('workflow_integrations-reference', row['reasons'])
        with self.board_instance.store.db.read() as connection:
            self.assertIn('workflow_integrations-reference', storage.allocation_references(connection, manifest))
        new_plan = self.board_instance.call('storage_plan', {})
        result = self.apply(new_plan)
        self.assertTrue(checkout.is_dir())
        self.assertEqual(result['removed'], [])

    def test_public_integration_after_plan_blocks_removal(self):
        manifest, checkout, planned = self.orphan()
        self.accept_at_orphan(manifest, checkout)
        self.assert_retained(checkout, planned)

    def test_public_integration_after_apply_inventory_is_rechecked_inside_writer_fence(self):
        manifest, checkout, planned = self.orphan()
        original = storage.inspect
        def register_after_inventory(*args, **kwargs):
            result = original(*args, **kwargs)
            self.accept_at_orphan(manifest, checkout)
            return result
        with patch.object(storage, 'inspect', side_effect=register_after_inventory):
            result = self.assert_retained(checkout, planned)
        self.assertEqual(result['skipped'][0]['reasons'], ['STORAGE_CHANGED'])

    def retained_reference_forms(self, manifest, checkout):
        # Private historical alias fixtures use the accepted B1 reader; no input
        # manifest or recorded receipt is rewritten to its canonical identity.
        aliases = []
        git_dir = Path(workspace.inspect(str(checkout))['gitDir'])
        with self.board_instance.store.db.write() as connection:
            for delta in (0, 2):
                facts = git_dir.stat()
                old = workspace._sha(workspace_identity._json([str(git_dir), facts.st_dev + delta, facts.st_ino]))
                entry = {'version': 1, 'source': 'fixed-input-and-inode', 'newId': manifest['checkoutId'],
                         'anchors': [{'directory': str(git_dir), 'inode': facts.st_ino,
                                      'legacyDevice': facts.st_dev + delta, 'workspaceId': manifest['workspaceId'],
                                      'manifestSha256': manifest['manifestSha256'], 'role': 'checkoutId'}]}
                connection.execute('INSERT INTO meta(key,value) VALUES (?,?)',
                                   (workspace_identity.META_KEY_PREFIX + old, json.dumps(entry)))
                aliases.append(old)
            mapping = workspace_identity.load_alias_map(connection)
            self.assertTrue(all(mapping[old] == manifest['checkoutId'] for old in aliases))
        return [('path', str(checkout)), ('path', str(checkout / '..' / 'checkout')),
                ('path', str(checkout.parent)), ('path', str(checkout / 'retained.txt')),
                *[('identity', value) for value in (manifest['checkoutId'], *aliases)],
                ('nested', {'target': {'path': str(checkout)}}),
                ('nested', {'executionWorkspace': {'checkoutId': aliases[0]}}),
                ('nested', {'executionWorkspace': {'checkoutId': aliases[1]}}),
                ('nested', {'workspaceId': manifest['workspaceId']}),
                ('nested', {'manifestSha256': manifest['manifestSha256']}),
                ('nested', {'output': str(checkout.parent / 'outputs' / 'retained.patch')})]

    def test_all_integration_states_and_path_identity_alias_forms_are_retained(self):
        manifest, checkout, planned = self.orphan()
        parent = self.submit(self.board_instance, request_id='other-run', cwd=str(self.repo), kind='existing')
        forms = self.retained_reference_forms(manifest, checkout)
        with self.board_instance.store.db.write() as connection:
            for state in ('verified', 'not-required', 'conflict'):
                for index, (kind, value) in enumerate(forms):
                    with self.subTest(state=state, kind=kind, form=index):
                        identity = state + '-' + str(index)
                        connection.execute('''INSERT INTO workflow_integrations
                            (integration_id,run_id,artifact_id,state,strategy,binding_sha256,target_kind,
                             target_path,target_checkout_id,verification_json,actor,created_at)
                            VALUES (?,?,? ,?,'patch',?,'checkout',?,?,?,'host-1',?)''',
                            (identity, parent['runId'], 'private-artifact', state, identity,
                             value if kind == 'path' else '', value if kind == 'identity' else None,
                             json.dumps(value if kind == 'nested' else {}), '2026-01-01T00:00:00Z'))
                        rows = [(table, row) for table, row in storage._workspace_reference_rows(connection)
                                if table == 'workflow_integrations' and row['integration_id'] == identity]
                        self.assertEqual(storage._allocation_reference_reasons(
                            rows, manifest, None, mapping=workspace_identity.load_alias_map(connection)),
                            ['workflow_integrations-reference'])
            self.assertIn('workflow_integrations-reference', storage.allocation_references(connection, manifest))
        row = next(row for row in storage.inspect(self.board_instance.store)['candidates'] if row['path'] == str(checkout))
        self.assertFalse(row['eligible'])
        self.assertIn('workflow_integrations-reference', row['reasons'])
        self.assert_retained(checkout, planned)

    def test_cleanup_plan_states_and_path_identity_alias_forms_cannot_use_orphan_bypass(self):
        manifest, checkout, planned = self.orphan()
        parent = self.submit(self.board_instance, request_id='other-run', cwd=str(self.repo), kind='existing')
        forms = self.retained_reference_forms(manifest, checkout)
        with self.board_instance.store.db.write() as connection:
            for state in ('planned', 'applying', 'applied', 'blocked'):
                for index, (kind, value) in enumerate(forms):
                    with self.subTest(state=state, kind=kind, form=index):
                        identity = state + '-' + str(index)
                        connection.execute('''INSERT INTO workspace_cleanup_plans
                            (plan_id,run_id,workspace_id,checkout_id,path,kind,state,evidence_json,
                             retention_json,actor,created_at,expires_at)
                            VALUES (?,?,'other-workspace',?,?,'worktree',?,?,'{}','host-1',?,?)''',
                            (identity, parent['runId'], value if kind == 'identity' else 'other-checkout',
                             value if kind == 'path' else '', state,
                             json.dumps(value if kind == 'nested' else {}),
                             '2026-01-01T00:00:00Z', '2027-01-01T00:00:00Z'))
                        rows = [(table, row) for table, row in storage._workspace_reference_rows(connection)
                                if table == 'workspace_cleanup_plans' and row['plan_id'] == identity]
                        self.assertEqual(storage._allocation_reference_reasons(
                            rows, manifest, None, mapping=workspace_identity.load_alias_map(connection)),
                            ['workspace_cleanup_plans-reference'])
            self.assertIn('workspace_cleanup_plans-reference', storage.allocation_references(connection, manifest))
        row = next(row for row in storage.inspect(self.board_instance.store)['candidates'] if row['path'] == str(checkout))
        self.assertFalse(row['eligible'])
        self.assertIn('workspace_cleanup_plans-reference', row['reasons'])
        self.assert_retained(checkout, planned)

    def test_retained_scope_manifest_hash_alone_protects_allocation(self):
        manifest, checkout, planned = self.orphan()
        parent = self.submit(self.board_instance, request_id='other-run', cwd=str(self.repo), kind='existing')
        with self.board_instance.store.db.write() as connection:
            connection.execute('''INSERT INTO workflow_scope_versions
                (run_id,scope_version,actor,write_scope_json,manifest_sha256,stopped_evidence_json,created_at)
                VALUES (?,2,'host-1','[]',?,'{}',?)''',
                (parent['runId'], manifest['manifestSha256'], '2026-01-01T00:00:00Z'))
            self.assertIn('workflow_scope_versions-reference', storage.allocation_references(connection, manifest))
        row = next(row for row in storage.inspect(self.board_instance.store)['candidates'] if row['path'] == str(checkout))
        self.assertFalse(row['eligible'])
        self.assertIn('workflow_scope_versions-reference', row['reasons'])
        self.assert_retained(checkout, planned)

    def test_proven_orphan_removes_only_checkout_and_replays_receipt(self):
        manifest, checkout, planned = self.orphan()
        parent = checkout.parent
        outputs = parent / 'outputs'
        outputs.mkdir()
        (outputs / 'kept.txt').write_text('retained output')
        refs = self.git(self.repo, 'for-each-ref', '--format=%(refname):%(objectname)',
                        'refs/buddy/workspaces/' + manifest['workspaceId'] + '/')
        records = {name: (parent / name).read_bytes() for name in ('manifest.json', 'input.json', 'request.json')}
        result = self.apply(planned)
        self.assertFalse(checkout.exists())
        self.assertEqual(len(result['removed']), 1)
        self.assertEqual(self.apply(planned), result)
        self.assertEqual((outputs / 'kept.txt').read_text(), 'retained output')
        self.assertEqual((self.repo / 'tracked.txt').read_text(), 'input\n')
        self.assertEqual(self.git(self.repo, 'for-each-ref', '--format=%(refname):%(objectname)',
                                 'refs/buddy/workspaces/' + manifest['workspaceId'] + '/'), refs)
        self.assertEqual({name: (parent / name).read_bytes() for name in records}, records)
        with patch.object(storage, '_workspace_reference_rows', wraps=storage._workspace_reference_rows) as scan:
            replanned = self.board_instance.call('storage_plan', {})
        scan.assert_not_called()
        self.assertFalse(any(row['path'] in (str(checkout), str(parent))
                             for row in replanned['candidates']))
        with self.board_instance.store.db.read() as connection:
            self.assertEqual(connection.execute('SELECT COUNT(*) FROM tasks').fetchone()[0], 0)
            self.assertEqual(connection.execute('SELECT COUNT(*) FROM workspace_reservations').fetchone()[0], 0)

    def test_apply_refuses_new_workflow_registration(self):
        _, checkout, planned = self.orphan()
        self.submit(self.board_instance, request_id='registered', cwd=str(checkout), kind='existing')
        self.assert_retained(checkout, planned)

    def test_apply_refuses_same_request_admission(self):
        _, checkout, planned = self.orphan()
        self.submit(self.board_instance, request_id='orphan-request', cwd=str(self.repo), kind='worktree')
        self.assert_retained(checkout, planned)

    def test_released_reservation_still_protects_allocation(self):
        manifest, checkout, planned = self.orphan()
        parent = self.submit(self.board_instance, request_id='other-run', cwd=str(self.repo), kind='existing')
        with self.board_instance.store.db.write() as connection:
            connection.execute('''INSERT INTO workspace_reservations
                (reservation_id,workspace_id,checkout_id,repository_id,path,access,holder_task_id,
                 holder_kind,parent_run_id,manifest_sha256,state,created_at,updated_at)
                VALUES (?,?,?,?,?,'read',?,'parent',?,?,'released',?,?)''',
                ('retained-reservation', manifest['workspaceId'], manifest['checkoutId'], manifest['repositoryId'],
                 str(checkout), parent['runId'], parent['runId'], manifest['manifestSha256'],
                 '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z'))
        self.assert_retained(checkout, planned)

    def test_invalidated_continuation_still_protects_original_input(self):
        manifest, checkout, planned = self.orphan()
        parent = self.submit(self.board_instance, request_id='other-run', cwd=str(self.repo), kind='existing')
        with self.board_instance.store.db.write() as connection:
            connection.execute('''INSERT INTO workflow_continuations
                (continuation_id,run_id,authorized_by,expected_revision,input_text,input_bytes,
                 workspace_manifest_json,state,created_at)
                VALUES (?,?,'manual',1,'input',5,?,'invalidated',?)''',
                ('retained-continuation', parent['runId'], json.dumps(manifest), '2026-01-01T00:00:00Z'))
        self.assert_retained(checkout, planned)

    def test_workflow_artifact_manifest_protects_allocation(self):
        manifest, checkout, planned = self.orphan()
        parent = self.submit(self.board_instance, request_id='other-run', cwd=str(self.repo), kind='existing')
        with self.board_instance.store.db.write() as connection:
            connection.execute('''INSERT INTO workflow_artifacts
                (artifact_id,run_id,kind,manifest_json,manifest_sha256,created_at)
                VALUES (?,?,'input',?,?,?)''',
                ('retained-artifact', parent['runId'], json.dumps(manifest), manifest['manifestSha256'],
                 '2026-01-01T00:00:00Z'))
        self.assert_retained(checkout, planned)

    def test_unreadable_retained_artifact_keeps_reference_unproven(self):
        _, checkout, planned = self.orphan()
        parent = self.submit(self.board_instance, request_id='other-run', cwd=str(self.repo), kind='existing',
                             task='{freeform task text is never a JSON reference}')
        before = next(row for row in storage.inspect(self.board_instance.store)['candidates'] if row['path'] == str(checkout))
        self.assertTrue(before['eligible'], before['reasons'])
        with self.board_instance.store.db.write() as connection:
            connection.execute('''INSERT INTO workflow_artifacts
                (artifact_id,run_id,kind,manifest_json,manifest_sha256,created_at)
                VALUES (?,?,'input','{invalid-json','unreadable-private-fixture',?)''',
                ('unreadable-artifact', parent['runId'], '2026-01-01T00:00:00Z'))
        row = next(row for row in storage.inspect(self.board_instance.store)['candidates'] if row['path'] == str(checkout))
        self.assertFalse(row['eligible'])
        self.assertIn('workflow_artifacts-reference-unproven', row['reasons'])
        self.assert_retained(checkout, planned)

    def test_artifact_location_protects_checkout_even_without_manifest(self):
        _, checkout, planned = self.orphan()
        parent = self.submit(self.board_instance, request_id='other-run', cwd=str(self.repo), kind='existing')
        with self.board_instance.store.db.write() as connection:
            connection.execute('''INSERT INTO artifacts
                (artifact_id,task_id,kind,location,content_hash,size_bytes,created_at)
                VALUES (?,?,'evidence',?,'private-fixture',0,?)''',
                ('retained-file-artifact', parent['runId'], str(checkout / 'evidence.txt'), '2026-01-01T00:00:00Z'))
        self.assert_retained(checkout, planned)

    def test_output_artifact_location_protects_allocation(self):
        _, checkout, planned = self.orphan()
        parent = self.submit(self.board_instance, request_id='other-run', cwd=str(self.repo), kind='existing')
        with self.board_instance.store.db.write() as connection:
            connection.execute('''INSERT INTO artifacts
                (artifact_id,task_id,kind,location,content_hash,size_bytes,created_at)
                VALUES (?,?,'evidence',?,'private-fixture',0,?)''',
                ('retained-output-artifact', parent['runId'], str(checkout.parent / 'outputs' / 'evidence.txt'),
                 '2026-01-01T00:00:00Z'))
        self.assert_retained(checkout, planned)

    def test_database_writer_fence_survives_until_actual_removal(self):
        _, checkout, planned = self.orphan()
        original = workspace.cleanup_remove
        witnessed = []
        def attempt_registration_during_removal(*args, **kwargs):
            connection = sqlite3.connect(str(self.board_instance.store.db.path), timeout=0)
            try:
                with self.assertRaisesRegex(sqlite3.OperationalError, 'locked'):
                    connection.execute('BEGIN IMMEDIATE')
                witnessed.append(True)
            finally:
                connection.close()
            return original(*args, **kwargs)
        with patch.object(workspace, 'cleanup_remove', side_effect=attempt_registration_during_removal):
            result = self.apply(planned)
        self.assertEqual(witnessed, [True])
        self.assertEqual(len(result['removed']), 1)
        self.assertFalse(checkout.exists())

    def test_apply_rechecks_database_after_inventory(self):
        _, checkout, planned = self.orphan()
        original = storage.inspect
        def register_after_inventory(store):
            result = original(store)
            self.submit(self.board_instance, request_id='late-registration', cwd=str(checkout), kind='existing')
            return result
        with patch.object(storage, 'inspect', side_effect=register_after_inventory):
            self.assert_retained(checkout, planned)

    def test_concurrent_same_request_admission_holds_allocation_lock(self):
        _, checkout, planned = self.orphan()
        prepared, release = threading.Event(), threading.Event()
        admitted, errors = [], []
        original = self.board_instance.store.task_submit
        def pause_admission(*args, **kwargs):
            prepared.set()
            if not release.wait(10):
                raise AssertionError('private admission barrier timed out')
            return original(*args, **kwargs)
        def submit():
            try:
                admitted.append(self.submit(self.board_instance, request_id='orphan-request',
                                            cwd=str(self.repo), kind='worktree'))
            except Exception as error:
                errors.append(error)
        with patch.object(self.board_instance.store, 'task_submit', side_effect=pause_admission):
            thread = threading.Thread(target=submit)
            thread.start()
            try:
                self.assertTrue(prepared.wait(10), errors)
                result = self.assert_retained(checkout, planned)
                self.assertTrue(any('WORKSPACE_BUSY' in row['reasons'] for row in result['skipped']))
            finally:
                release.set()
                thread.join(10)
            self.assertFalse(thread.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(len(admitted), 1)
        self.assertTrue(checkout.is_dir())

    def test_apply_refuses_new_unsealed_tracked_and_untracked_content(self):
        _, checkout, planned = self.orphan()
        (checkout / 'tracked.txt').write_text('changed\n')
        (checkout / 'new.txt').write_text('new content\n')
        self.assert_retained(checkout, planned)

    def test_ignored_new_content_blocks_orphan_inspection(self):
        _, checkout, _ = self.orphan()
        (checkout / 'ignored').mkdir()
        (checkout / 'ignored' / 'cache').write_text('unsealed ignored contents')
        row = next(row for row in storage.inspect(self.board_instance.store)['candidates'] if row['path'] == str(checkout))
        self.assertFalse(row['eligible'])
        self.assertIn('unsealed-changes', row['reasons'])

    def test_apply_refuses_unlock(self):
        _, checkout, planned = self.orphan()
        self.git(self.repo, 'worktree', 'unlock', str(checkout))
        self.assert_retained(checkout, planned)

    def test_apply_refuses_lock_replacement(self):
        _, checkout, planned = self.orphan()
        lock = checkout.parent / '.lock'
        lock.rename(lock.with_name('preserved-lock'))
        lock.touch()
        self.assert_retained(checkout, planned)

    def test_apply_refuses_physical_checkout_replacement(self):
        _, checkout, planned = self.orphan()
        checkout.rename(checkout.with_name('preserved-checkout'))
        checkout.mkdir()
        (checkout / 'tracked.txt').write_text('replacement')
        self.assert_retained(checkout, planned)

    def test_apply_refuses_symlink_replacement(self):
        _, checkout, planned = self.orphan()
        saved = checkout.with_name('preserved-checkout')
        checkout.rename(saved)
        checkout.symlink_to(saved, target_is_directory=True)
        self.assert_retained(checkout, planned)

    def test_apply_refuses_changed_original_manifest_path(self):
        manifest, checkout, planned = self.orphan()
        manifest['checkoutRoot'] = str(self.repo)
        manifest['manifestSha256'] = workspace._sha(workspace._json({k: v for k, v in manifest.items() if k != 'manifestSha256'}))
        (checkout.parent / 'manifest.json').write_text(json.dumps(manifest))
        self.assert_retained(checkout, planned)

    def test_unknown_directories_remain_blocked_candidates(self):
        unknown = self.directory / 'workspaces' / 'unknown-allocation'
        (unknown / 'checkout').mkdir(parents=True)
        (unknown / 'checkout' / 'valuable.txt').write_text('retain')
        other = self.directory / 'workspaces' / 'unknown-directory'
        other.mkdir()
        planned = self.board_instance.call('storage_plan', {})
        rows = [row for row in planned['candidates'] if row.get('orphan')]
        self.assertEqual([row['path'] for row in rows], [str(unknown / 'checkout')])
        self.assertTrue(all(not row['eligible'] and 'allocation-unproven' in row['reasons'] for row in rows))
        self.apply(planned)
        self.assertEqual((unknown / 'checkout' / 'valuable.txt').read_text(), 'retain')
        self.assertTrue(other.is_dir())

    def test_fixed_refs_must_remain_direct_exact_commit_objects(self):
        manifest, checkout, planned = self.orphan()
        ref = manifest['snapshot']['inputRef']
        self.git(self.repo, 'symbolic-ref', ref, manifest['snapshot']['stagedRef'])
        self.assert_retained(checkout, planned)

    def test_absent_checkouts_and_host_owned_records_need_no_reference_scan(self):
        root = self.directory / 'workspaces'
        (root / 'empty').mkdir(parents=True)
        request_only = root / 'request-only'
        request_only.mkdir()
        (request_only / 'request.json').write_text(json.dumps({'version': 1, 'requestId': 'pending'}))
        host = self.submit(self.board_instance, request_id='host-owned', cwd=str(self.repo), kind='existing')
        self.assertEqual(host['workspace']['kind'], 'existing')
        with patch.object(storage, '_workspace_reference_rows', wraps=storage._workspace_reference_rows) as scan:
            planned = self.board_instance.call('storage_plan', {})
        scan.assert_not_called()
        self.assertFalse(any(row['category'] == 'workspaces' for row in planned['candidates']))
        self.assertTrue(request_only.is_dir())
        self.assertTrue((root / 'empty').is_dir())
        self.assertEqual((self.repo / 'tracked.txt').read_text(), 'input\n')

    def test_known_allocation_with_rejected_ownership_is_never_an_orphan(self):
        submitted = self.submit(self.board_instance, request_id='known', cwd=str(self.repo), kind='worktree')
        checkout = Path(submitted['workspace']['path'])
        manifest = json.loads((checkout.parent / 'manifest.json').read_text())
        before = self.board_instance.call('storage_plan', {})
        row = next(row for row in before['candidates'] if row['path'] == str(checkout))
        self.assertFalse(row.get('orphan', False))
        self.assertFalse(row['eligible'])
        with patch.object(workspace, 'resolve_allocation', side_effect=storage.BoardError('WORKSPACE_CHANGED', 'rejected')):
            refused = self.board_instance.call('storage_plan', {})
        self.assertFalse(any(row.get('orphan') and row['path'] == str(checkout) for row in refused['candidates']))
        self.apply(refused)
        self.assertTrue(checkout.is_dir())
        # Retained board provenance also protects an allocation whose current
        # manifest alone no longer proves the physical ownership.
        with self.board_instance.store.db.write() as connection:
            current = dict(manifest, kind='existing')
            current['manifestSha256'] = workspace._sha(workspace._json({k: v for k, v in current.items() if k != 'manifestSha256'}))
            connection.execute('UPDATE workflow_runs SET workspace_manifest_json=? WHERE run_id=?',
                               (json.dumps(current), submitted['runId']))
        changed = dict(manifest, access='read')
        changed['manifestSha256'] = workspace._sha(workspace._json({k: v for k, v in changed.items() if k != 'manifestSha256'}))
        (checkout.parent / 'manifest.json').write_text(json.dumps(changed))
        self.assertIsNone(workspace.resolve_allocation(self.directory, current, [manifest]))
        unproven = self.board_instance.call('storage_plan', {})
        self.assertFalse(any(row.get('orphan') and row['path'] == str(checkout) for row in unproven['candidates']))
        self.assertTrue(checkout.is_dir())

    def test_planning_streams_rows_and_parses_each_field_once_for_multiple_orphans(self):
        first, checkout, _ = self.orphan()
        request = json.loads((checkout.parent / 'request.json').read_text())
        second = workspace.prepare(self.directory, 'second-orphan', request['intent'])
        body = json.dumps({'note': 'unique retained unrelated structured evidence'})
        with self.board_instance.store.db.write() as connection:
            connection.execute("INSERT INTO events(kind,payload_json,created_at) VALUES ('private',?,?)",
                               (body, '2026-01-01T00:00:00Z'))
        original = json.loads
        calls = []
        def counted(value, *args, **kwargs):
            if value == body:
                calls.append(value)
            return original(value, *args, **kwargs)
        with self.board_instance.store.db.read() as connection:
            read_rows = []
            def streamed(sql):
                for row in connection.execute(sql):
                    read_rows.append(sql)
                    yield row
            rows = storage._workspace_reference_rows(SimpleNamespace(execute=streamed))
            self.assertIs(iter(rows), rows, 'reference rows must stream, never materialize all tables')
            next(rows)
            self.assertEqual(len(read_rows), 1, 'the first row must not preload historical facts')
        with patch.object(storage.json, 'loads', side_effect=counted):
            planned = self.board_instance.call('storage_plan', {})
        self.assertEqual(len(calls), 1, 'one planning pass must parse a retained JSON field once')
        for manifest in (first, second):
            row = next(row for row in planned['candidates'] if row['path'] == manifest['checkoutRoot'])
            self.assertTrue(row['eligible'], row['reasons'])

    def test_directory_identity_is_bound_only_to_original_request(self):
        _, checkout, planned = self.orphan()
        request_path = checkout.parent / 'request.json'
        original = json.loads(request_path.read_text())
        other = workspace.prepare(self.directory, 'other-real-allocation', original['intent'])
        other_checkout = Path(other['checkoutRoot'])
        # Both allocations are genuine, with valid manifests, pinned inputs,
        # direct refs and Git locks. Only the request-derived directory binding
        # is broken by giving the second allocation the first original request.
        request_path_other = other_checkout.parent / 'request.json'
        request_path_other.write_text(json.dumps(original))
        self.assertEqual(json.loads(request_path_other.read_text()), original)
        workspace._validate_manifest(other)
        proof = workspace.cleanup_inspect(self.directory, other)
        self.assertEqual(proof['reasons'], [])
        for suffix in ('input', 'staged', 'retained/input', 'retained/staged'):
            self.assertEqual(self.git(self.repo, 'show-ref', '--verify', '--hash',
                                     'refs/buddy/workspaces/' + other['workspaceId'] + '/' + suffix),
                             other['inputCommit'] if suffix.endswith('input') else other['snapshot']['stagedCommit'])
        inspected = self.board_instance.call('storage_plan', {})
        row = next(row for row in inspected['candidates'] if row['path'] == str(other_checkout))
        self.assertFalse(row['eligible'], 'a different request-derived directory must never be reclaimable')
        self.assertIn('allocation-unproven', row['reasons'])
        self.assertTrue(other_checkout.is_dir())
        first_row = next(row for row in inspected['candidates'] if row['path'] == str(checkout))
        self.assertTrue(first_row['eligible'], first_row['reasons'])

    def test_late_reference_after_physical_proof_is_rechecked_in_writer_transaction(self):
        _, checkout, planned = self.orphan()
        original = storage._orphan_workspace
        inspected = []
        def insert_after_proof(*args, **kwargs):
            fresh = original(*args, **kwargs)
            inspected.append(fresh['path'])
            if len(inspected) == 2:
                # Apply inventory and physical proof have both already completed.
                # An independent committed reference must still win before the
                # authoritative writer fence and actual checkout removal.
                with self.board_instance.store.db.write() as connection:
                    connection.execute("INSERT INTO events(kind,payload_json,created_at) VALUES ('private-late',?,?)",
                                       (json.dumps({'retained': {'path': str(checkout / '..' / 'checkout')}}),
                                        '2026-01-01T00:00:00Z'))
            return fresh
        with patch.object(storage, '_orphan_workspace', side_effect=insert_after_proof):
            result = self.assert_retained(checkout, planned)
        self.assertEqual(inspected, [str(checkout), str(checkout)])
        self.assertEqual(result['skipped'][0]['reasons'], ['STORAGE_CHANGED'])
        self.assertTrue(checkout.is_dir())

    def test_late_allocation_record_change_after_physical_proof_is_retained(self):
        _, checkout, planned = self.orphan()
        original = storage._orphan_workspace
        calls = []
        def change_after_proof(*args, **kwargs):
            fresh = original(*args, **kwargs)
            calls.append(fresh['path'])
            if len(calls) == 2:
                path = checkout.parent / 'request.json'
                changed = json.loads(path.read_text())
                changed['requestId'] = 'different-original-request'
                path.write_text(json.dumps(changed))
            return fresh
        with patch.object(storage, '_orphan_workspace', side_effect=change_after_proof):
            result = self.assert_retained(checkout, planned)
        self.assertEqual(calls, [str(checkout), str(checkout)])
        self.assertEqual(result['skipped'][0]['reasons'], ['STORAGE_CHANGED'])
