"""Disposable real Git allocations, retained-board facts and apply-time fences."""
from unittest.mock import patch as defer_reclaim
import json
from pathlib import Path
import subprocess
import sqlite3
import shutil
from types import SimpleNamespace
from unittest.mock import patch

from blackboard.tasks.test_workflow import WorkflowTestCase
from support import private_state_dir
from hey_my_buddy.blackboard.tasks import storage, workflow, workspace
from hey_my_buddy.errors import BoardError

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
        (cls.source_template / 'tracked.txt').write_text('input\n', encoding="utf-8", newline="\n")
        (cls.source_template / '.gitignore').write_text('ignored/\n', encoding="utf-8", newline="\n")
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
        command, git_env = workspace._git_command(root, *args)
        result = subprocess.run(command, capture_output=True, text=True, env=git_env, timeout=30, encoding="utf-8")
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout.strip()

    def orphan(self, request_id='orphan-request'):
        intent = {'kind': 'worktree', 'cwd': str(self.repo), 'access': 'write',
                  'base': {'kind': 'working-tree'}, 'includeUntracked': [],
                  'writeScope': ['.'], 'integrator': {'role': 'host', 'id': 'host-1'}}
        manifest = workspace.prepare(self.directory, request_id, intent)
        checkout = Path(manifest['checkoutRoot'])
        planned = self.board_instance.call('storage_plan', {})
        self.assertFalse(any(row['path'] == str(checkout) for row in planned['candidates']))
        return manifest, checkout, planned

    def apply(self, planned):
        return self.board_instance.call('storage_apply', {'planId': planned['planId'],
                                                         'commandId': 'apply-orphans', 'confirm': True})

    def assert_retained(self, checkout, planned=None):
        from hey_my_buddy.blackboard.tasks.reclamation import Reclamation
        Reclamation(self.board_instance.store).run()
        self.assertTrue(checkout.exists() or checkout.is_symlink())
        rows = self.board_instance.call('storage_plan', {})['candidates']
        self.assertFalse(any(row['eligible'] for row in rows if row['path'] in (str(checkout), str(checkout.parent))))

    def references(self, connection, manifest):
        return storage._allocation_reference_sets(storage._workspace_reference_rows(connection), [(manifest, None)])[0]

    def accept_at_orphan(self, manifest, checkout):
        """A real public acceptance verifies a ref without switching target HEAD."""
        board = self.board_instance
        delivered = self.submit(board, request_id='integration-source', cwd=str(self.repo), kind='existing')
        self.register(board)
        claim = self.claim(board, run_id=delivered['runId'])
        (self.repo / 'tracked.txt').write_text('sealed integration output\n', encoding="utf-8", newline="\n")
        self.finish_turn(board, claim)
        view = board.call('workflow_get', {'runId': delivered['runId']})
        with board.store.db.read() as connection:
            sealed = json.loads(connection.execute(
                'SELECT manifest_json FROM workflow_artifacts WHERE artifact_id=?',
                (view['finalArtifactId'],)).fetchone()[0])
        with defer_reclaim.object(board.store.workflow, "_reclaim_checkout", return_value={"removed": False, "reasons": ["fixture-retained"]}):
            accepted = board.call('workflow_accept', {
                'runId': delivered['runId'], 'artifactId': view['finalArtifactId'],
                'note': 'Private ref integration regression',
                'target': {'path': str(checkout), 'ref': sealed['commit']}, **self.control(view)})
        self.assertEqual(accepted['integration']['state'], 'verified')
        self.assertEqual(accepted['integration']['target']['path'], str(checkout))
        self.assertEqual(self.git(checkout, 'rev-parse', 'HEAD'), manifest['inputCommit'])
        self.assertEqual((checkout / 'tracked.txt').read_text(encoding="utf-8"), 'input\n')
        return accepted

    def test_public_integration_before_inventory_protects_unchanged_target(self):
        manifest, checkout, _ = self.orphan()
        self.accept_at_orphan(manifest, checkout)
        inspected = storage.inspect(self.board_instance.store)
        row = next(row for row in inspected['candidates'] if row['path'] == str(checkout))
        self.assertFalse(row['eligible'], row['reasons'])
        self.assertIn('workflow_integrations-reference', row['reasons'])
        with self.board_instance.store.db.read() as connection:
            self.assertIn('workflow_integrations-reference', self.references(connection, manifest))
        new_plan = self.board_instance.call('storage_plan', {})
        result = self.apply(new_plan)
        self.assertTrue(checkout.is_dir())
        self.assertEqual(result['removed'], [])

    def test_old_full_manifest_checkout_is_proven_as_an_orphan(self):
        current, checkout, _ = self.orphan()
        old = dict(current, version=2, access='full', accessReason='Original Host explanation')
        old['manifestSha256'] = workspace._sha(workspace._json({
            key: value for key, value in old.items() if key != 'manifestSha256'}))
        (checkout.parent / 'manifest.json').write_bytes(workspace._json(old))
        request_path = checkout.parent / 'request.json'
        request = json.loads(request_path.read_bytes())
        request['version'] = 2
        request['intent']['access'] = 'full'
        request['intent']['accessReason'] = old['accessReason']
        request_path.write_bytes(workspace._json(request))
        planned = self.board_instance.call('storage_plan', {})
        self.assertFalse(any(row['path'] == str(checkout) for row in planned['candidates']))
        from hey_my_buddy.blackboard.tasks.reclamation import Reclamation
        Reclamation(self.board_instance.store).run()
        self.assertFalse(checkout.exists())

    def retained_reference_forms(self, manifest, checkout):
        return [('path', str(checkout)), ('path', str(checkout / '..' / 'checkout')),
                ('path', str(checkout.parent)), ('path', str(checkout / 'retained.txt')),
                ('identity', manifest['checkoutId']),
                ('nested', {'target': {'path': str(checkout)}}),
                ('nested', {'executionWorkspace': {'checkoutId': manifest['checkoutId']}}),
                ('nested', {'workspaceId': manifest['workspaceId']}),
                ('nested', {'manifestSha256': manifest['manifestSha256']}),
                ('nested', {'output': str(checkout.parent / 'outputs' / 'retained.patch')})]

    def test_integration_strategies_and_path_identity_forms_are_retained(self):
        manifest, checkout, planned = self.orphan()
        parent = self.submit(self.board_instance, request_id='other-run', cwd=str(self.repo), kind='existing')
        forms = self.retained_reference_forms(manifest, checkout)
        with self.board_instance.store.db.write() as connection:
            for strategy in ('patch', 'merge', 'not-required'):
                for index, (kind, value) in enumerate(forms):
                    with self.subTest(strategy=strategy, kind=kind, form=index):
                        identity = strategy + '-' + str(index)
                        connection.execute('''INSERT INTO workflow_integrations
                            (integration_id,run_id,artifact_id,strategy,binding_sha256,
                             target_path,target_checkout_id,verification_json,actor_role,actor_id,created_at)
                            VALUES (?,?,?,?,?,?,?,?,'host','host-1',?)''',
                            (identity, parent['runId'], 'private-artifact', strategy, identity,
                             value if kind == 'path' else '', value if kind == 'identity' else None,
                             json.dumps(value if kind == 'nested' else {}), '2026-01-01T00:00:00Z'))
                        rows = [(table, row) for table, row in storage._workspace_reference_rows(connection)
                                if table == 'workflow_integrations' and row['integration_id'] == identity]
                        self.assertEqual(storage._allocation_reference_sets(rows, [(manifest, None)])[0],
                            ['workflow_integrations-reference'])
            self.assertIn('workflow_integrations-reference', self.references(connection, manifest))
        row = next(row for row in storage.inspect(self.board_instance.store)['candidates'] if row['path'] == str(checkout))
        self.assertFalse(row['eligible'])
        self.assertIn('workflow_integrations-reference', row['reasons'])
        self.assert_retained(checkout, planned)

    def test_cleanup_plan_states_and_path_identity_forms_cannot_use_orphan_bypass(self):
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
                             retention_json,created_at)
                            VALUES (?,?,'other-workspace',?,?,'worktree',?,?,'{}',?)''',
                            (identity, parent['runId'], value if kind == 'identity' else 'other-checkout',
                             value if kind == 'path' else '', state,
                             json.dumps(value if kind == 'nested' else {}),
                             '2026-01-01T00:00:00Z'))
                        rows = [(table, row) for table, row in storage._workspace_reference_rows(connection)
                                if table == 'workspace_cleanup_plans' and row['plan_id'] == identity]
                        self.assertEqual(storage._allocation_reference_sets(rows, [(manifest, None)])[0],
                            ['workspace_cleanup_plans-reference'])
            self.assertIn('workspace_cleanup_plans-reference', self.references(connection, manifest))
        row = next(row for row in storage.inspect(self.board_instance.store)['candidates'] if row['path'] == str(checkout))
        self.assertFalse(row['eligible'])
        self.assertIn('workspace_cleanup_plans-reference', row['reasons'])
        self.assert_retained(checkout, planned)

    def test_retained_scope_manifest_hash_alone_protects_allocation(self):
        manifest, checkout, planned = self.orphan()
        parent = self.submit(self.board_instance, request_id='other-run', cwd=str(self.repo), kind='existing')
        with self.board_instance.store.db.write() as connection:
            connection.execute('''INSERT INTO workflow_scope_versions
                (run_id,scope_version,actor_role,actor_id,write_scope_json,manifest_sha256,stopped_evidence_json,created_at)
                VALUES (?,2,'host','host-1','[]',?,'{}',?)''',
                (parent['runId'], manifest['manifestSha256'], '2026-01-01T00:00:00Z'))
            self.assertIn('workflow_scope_versions-reference', self.references(connection, manifest))
        row = next(row for row in storage.inspect(self.board_instance.store)['candidates'] if row['path'] == str(checkout))
        self.assertFalse(row['eligible'])
        self.assertIn('workflow_scope_versions-reference', row['reasons'])
        self.assert_retained(checkout, planned)

    def test_released_reservation_still_protects_allocation(self):
        manifest, checkout, planned = self.orphan()
        parent = self.submit(self.board_instance, request_id='other-run', cwd=str(self.repo), kind='existing')
        with self.board_instance.store.db.write() as connection:
            connection.execute('''INSERT INTO workspace_reservations
                (reservation_id,workspace_id,checkout_id,repository_id,path,access,holder_task_id,
                 holder_kind,parent_run_id,manifest_sha256,state,created_at)
                VALUES (?,?,?,?,?,'read',?,'parent',?,?,'released',?)''',
                ('retained-reservation', manifest['workspaceId'], manifest['checkoutId'], manifest['repositoryId'],
                 str(checkout), parent['runId'], parent['runId'], manifest['manifestSha256'],
                 '2026-01-01T00:00:00Z'))
        self.assert_retained(checkout, planned)

    def test_invalidated_continuation_still_protects_original_input(self):
        manifest, checkout, planned = self.orphan()
        parent = self.submit(self.board_instance, request_id='other-run', cwd=str(self.repo), kind='existing')
        with self.board_instance.store.db.write() as connection:
            connection.execute('''INSERT INTO workflow_continuations
                (continuation_id,run_id,kind,expected_revision,input_text,
                 workspace_manifest_json,state,created_at)
                VALUES (?,?,'manual',1,'input',?,'invalidated',?)''',
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
        manifest, checkout, planned = self.orphan()
        parent = self.submit(self.board_instance, request_id='other-run', cwd=str(self.repo), kind='existing',
                             task='{freeform task text is never a JSON reference}')
        self.assertFalse(any(row['path'] == str(checkout) for row in storage.inspect(self.board_instance.store)['candidates']))
        with self.board_instance.store.db.write() as connection:
            connection.execute('''INSERT INTO workflow_artifacts
                (artifact_id,run_id,kind,manifest_json,manifest_sha256,created_at)
                VALUES (?,?,'input','{invalid-json','unreadable-private-fixture',?)''',
                ('unreadable-artifact', parent['runId'], '2026-01-01T00:00:00Z'))
        with self.board_instance.store.db.read() as connection:
            reasons = self.references(connection, manifest)
        self.assertIn('workflow_artifacts-reference-unproven', reasons)
        # Corrupt service-owned JSON now fails inventory loudly. The deletion
        # boundary still treats its references as unproven and removes nothing.
        with self.assertRaises(sqlite3.OperationalError):
            storage.inspect(self.board_instance.store)
        with self.assertRaises(BoardError) as raised:
            self.apply(planned)
        self.assertEqual(raised.exception.code, 'INTERNAL_ERROR')
        self.assertTrue(checkout.exists())

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

    def test_unknown_directories_remain_blocked_candidates(self):
        unknown = self.directory / 'workspaces' / 'unknown-allocation'
        (unknown / 'checkout').mkdir(parents=True)
        (unknown / 'checkout' / 'valuable.txt').write_text('retain', encoding="utf-8", newline="\n")
        other = self.directory / 'workspaces' / 'unknown-directory'
        other.mkdir()
        planned = self.board_instance.call('storage_plan', {})
        rows = [row for row in planned['candidates'] if row.get('orphan')]
        self.assertEqual({row['path'] for row in rows}, {str(unknown / 'checkout'), str(other)})
        by_path = {row['path']:row for row in rows}
        self.assertIn('allocation-unproven', by_path[str(unknown / 'checkout')]['reasons'])
        self.assertIn('allocation-unproven', by_path[str(other)]['reasons'])
        self.assertIn('checkout-missing', by_path[str(other)]['reasons'])
        self.assertTrue(all(not row['eligible'] for row in rows))
        self.apply(planned)
        self.assertEqual((unknown / 'checkout' / 'valuable.txt').read_text(encoding="utf-8"), 'retain')
        self.assertTrue(other.is_dir())

    def test_known_allocation_with_rejected_ownership_is_never_an_orphan(self):
        submitted = self.submit(self.board_instance, request_id='known', cwd=str(self.repo), kind='worktree')
        checkout = Path(submitted['workspace']['path'])
        manifest = json.loads((checkout.parent / 'manifest.json').read_text(encoding="utf-8"))
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
        (checkout.parent / 'manifest.json').write_text(json.dumps(changed), encoding="utf-8", newline="\n")
        self.assertIsNone(workspace.resolve_allocation(self.directory, current, [manifest]))
        unproven = self.board_instance.call('storage_plan', {})
        self.assertFalse(any(row.get('orphan') and row['path'] == str(checkout) for row in unproven['candidates']))
        self.assertTrue(checkout.is_dir())

    def test_planning_streams_rows_and_parses_each_field_once_for_multiple_orphans(self):
        first, checkout, _ = self.orphan()
        request = json.loads((checkout.parent / 'request.json').read_text(encoding="utf-8"))
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
            self.assertFalse(any(row['path'] == manifest['checkoutRoot'] for row in planned['candidates']))

    def test_directory_identity_is_bound_only_to_original_request(self):
        _, checkout, planned = self.orphan()
        request_path = checkout.parent / 'request.json'
        original = json.loads(request_path.read_text(encoding="utf-8"))
        other = workspace.prepare(self.directory, 'other-real-allocation', original['intent'])
        other_checkout = Path(other['checkoutRoot'])
        # Both allocations are genuine, with valid manifests, pinned inputs,
        # direct refs and Git locks. Only the request-derived directory binding
        # is broken by giving the second allocation the first original request.
        request_path_other = other_checkout.parent / 'request.json'
        request_path_other.write_text(json.dumps(original), encoding="utf-8", newline="\n")
        self.assertEqual(json.loads(request_path_other.read_text(encoding="utf-8")), original)
        workspace._validate_manifest(other)
        proof = workspace.cleanup_inspect(self.directory, other)
        self.assertEqual(proof['reasons'], [])
        for suffix in ('input', 'staged', 'retained/input', 'retained/staged'):
            self.assertEqual(self.git(self.repo, 'show-ref', '--verify', '--hash',
                                     'refs/hey-my-buddy/workspaces/' + other['workspaceId'] + '/' + suffix),
                             other['inputCommit'] if suffix.endswith('input') else other['snapshot']['stagedCommit'])
        inspected = self.board_instance.call('storage_plan', {})
        row = next(row for row in inspected['candidates'] if row['path'] == str(other_checkout))
        self.assertFalse(row['eligible'], 'a different request-derived directory must never be reclaimable')
        self.assertIn('allocation-unproven', row['reasons'])
        self.assertTrue(other_checkout.is_dir())
        self.assertFalse(any(row['path'] == str(checkout) for row in inspected['candidates']))

    def test_raw_request_reference_protects_only_its_allocation_during_service_reclamation(self):
        request_id = 'raw-token-request'
        manifest, checkout, _ = self.orphan(request_id)
        request = json.loads((checkout.parent / 'request.json').read_text(encoding="utf-8"))
        self.assertEqual(request['requestId'], request_id)
        control = workspace.prepare(self.directory, 'raw-token-unreferenced-control', request['intent'])
        control_checkout = Path(control['checkoutRoot'])
        # The sole board reference is the original request string, not the
        # candidate's path/manifest/workspace/checkout identity.
        with self.board_instance.store.db.write() as connection:
            connection.execute("INSERT INTO events(kind,payload_json,created_at) VALUES ('raw-request',?,?)",
                               (json.dumps({'requestId': request_id}), '2026-01-01T00:00:00Z'))
        planned = self.board_instance.call('storage_plan', {})
        row = next(row for row in planned['candidates'] if row['path'] == str(checkout))
        self.assertFalse(any(row['path'] == str(control_checkout) for row in planned['candidates']))
        from hey_my_buddy.blackboard.tasks.reclamation import Reclamation
        Reclamation(self.board_instance.store).run()
        self.assertTrue(checkout.is_dir(), 'the original request reference must preserve the actual checkout')
        self.assertFalse(control_checkout.exists(), 'an unrelated orphan must remain reclaimable')
        self.assertFalse(row['eligible'])
        self.assertIn('events-reference', row['reasons'])


    def test_accept_holds_the_unregistered_target_lock_against_reclamation(self):
        from hey_my_buddy.blackboard.tasks.reclamation import Reclamation
        manifest, checkout, _ = self.orphan()
        verify = self.board_instance.store.workflow._accept_verify
        observed = []
        def while_locked(*args, **kwargs):
            Reclamation(self.board_instance.store).run()
            self.assertTrue(checkout.exists())
            with self.assertRaises(BoardError) as caught:
                with workspace._lock(checkout.parent, create=False):
                    pass
            self.assertEqual(caught.exception.code, 'WORKSPACE_BUSY')
            observed.append(True)
            return verify(*args, **kwargs)
        with patch.object(self.board_instance.store.workflow, '_accept_verify', side_effect=while_locked):
            self.accept_at_orphan(manifest, checkout)
        self.assertEqual(observed, [True])
        with workspace._lock(checkout.parent, create=False):
            pass
        Reclamation(self.board_instance.store).run()
        self.assertTrue(checkout.exists(), 'the committed integration must retain its target')

    def test_accept_reports_busy_while_reclamation_owns_its_target(self):
        from hey_my_buddy.blackboard.tasks.reclamation import Reclamation
        manifest, checkout, _ = self.orphan()
        remove = workspace.cleanup_remove
        observed = []
        def while_locked(*args, **kwargs):
            with self.assertRaises(BoardError) as caught:
                self.accept_at_orphan(manifest, checkout)
            self.assertEqual(caught.exception.code, 'WORKSPACE_BUSY')
            observed.append(True)
            return remove(*args, **kwargs)
        with patch.object(workspace, 'cleanup_remove', side_effect=while_locked):
            Reclamation(self.board_instance.store).run()
        self.assertEqual(observed, [True])
        self.assertFalse(checkout.exists())
        self.assertEqual(self.board_instance.store.service_issues(), [])
