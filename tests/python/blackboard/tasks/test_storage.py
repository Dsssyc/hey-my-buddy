"""Private protected inventory and exact-plan guards."""
import hashlib
import json
import os
from pathlib import Path
import stat
from types import SimpleNamespace
from unittest import mock, skipIf
from hey_my_buddy.errors import BoardError
from hey_my_buddy import private_dirs, locking
from hey_my_buddy.blackboard.tasks import storage
from support import BoardTestCase
from process_observation_fakes import process, processes

class StorageTests(BoardTestCase):
    def test_session_record_inventory_is_disjoint_protected_and_does_not_follow_links(self):
        board = self.board()
        first = private_dirs.session_record_path(board.directory, 'run:orphan', 'first')
        second = private_dirs.session_record_path(board.directory, 'run:orphan', 'second')
        for record, data in ((first, b'first record'), (second, b'second record')):
            private_dirs.ensure_private_dir(record.parent)
            record.write_bytes(data)
        other = private_dirs.attempt_root(board.directory, 'codex', 'orphan', 'first') / 'receipt.json'
        private_dirs.ensure_private_dir(other.parent)
        other.write_bytes(b'receipt')
        outside = self.directory / 'outside-session'
        outside.write_bytes(b'private outside content')
        linked = private_dirs.session_record_path(board.directory, 'run:linked', 'linked')
        private_dirs.ensure_private_dir(linked.parent)
        linked.symlink_to(outside)
        original = storage.tree_info
        def growing(path, **kwargs):
            if path == first.parent:
                with first.open('ab') as stream:
                    stream.write(b' appended while inspecting')
            return original(path, **kwargs)
        with mock.patch.object(storage, 'process_inventory', return_value=([], [], True)), \
             mock.patch.object(storage, 'tree_info', side_effect=growing):
            report = storage.inspect(board.store)
        records = [row for row in report['candidates'] if row['category'] == 'session-records']
        parent = next(row for row in report['candidates'] if row['category'] == 'harnesses')
        category = next(row for row in report['categories'] if row['id'] == 'session-records')
        self.assertEqual({row['groupId'] for row in records}, {'run:orphan', 'run:linked'})
        self.assertEqual(category['bytes'], first.stat().st_size + second.stat().st_size)
        self.assertEqual(parent['bytes'], other.stat().st_size)
        self.assertEqual(category['reclaimableBytes'], 0)
        self.assertTrue(all(row['reasons'] == ['macro-task-deletion'] and not row['eligible'] for row in records))
        self.assertEqual(outside.read_bytes(), b'private outside content')
        private_dirs.remove_tree(private_dirs.micro_task_root(board.directory, 'codex', 'orphan'))
        with mock.patch.object(storage, 'process_inventory', return_value=([], [], True)):
            self.assertTrue(any(row['category'] == 'session-records'
                                for row in storage.inspect(board.store)['candidates']))

    def _age_runtime_entry(self, path):
        expired = storage.time.time() - storage.GRACE_SECONDS - 60
        if path.is_dir() and not path.is_symlink():
            for child in path.rglob('*'):
                os.utime(child, (expired, expired), follow_symlinks=False)
        os.utime(path, (expired, expired), follow_symlinks=False)

    def test_runtime_leftovers_require_age_free_locks_and_unretained_real_paths(self):
        board = self.board()
        root = Path(os.environ['HMB_RUNTIME_ROOT'])
        root.mkdir()
        current, previous = 'a' * 32, 'b' * 32
        (board.directory / 'runtime-retention.json').write_text(json.dumps({
            'current': current, 'previous': previous}), encoding="utf-8", newline="\n")
        names = [current, previous, 'c' * 32, 'd' * 32, '0' * 32, '6' * 32,
                 '.' + 'e' * 32 + '.build-abandoned', '.' + 'f' * 32 + '.build-recent',
                 '.' + current + '.build-retained']
        entries = {name: root / name for name in names}
        for path in entries.values():
            path.mkdir()
            (path / 'payload').write_bytes(b'incomplete installation')
            self._age_runtime_entry(path)
        # A fresh file also protects a directory whose own timestamp is old.
        (entries['d' * 32] / 'payload').touch()
        (entries['.' + 'f' * 32 + '.build-recent'] / 'payload').touch()
        outside = self.directory / 'outside-runtime'
        outside.mkdir()
        sentinel = outside / 'payload'
        sentinel.write_bytes(b'outside')
        linked = root / ('9' * 32)
        linked.symlink_to(outside, target_is_directory=True)
        with mock.patch.object(storage, 'process_inventory', return_value=([], [], True)), \
             mock.patch.object(storage, 'runtime_usage', side_effect=lambda path, *_: (
                 ['runtime-in-use'] if path.name == '6' * 32 else [])):
            result = storage.prune_old_runtimes(board.store)
        self.assertTrue(result['complete'])
        expected_removed = {str(entries[name]) for name in ('c' * 32, '0' * 32,
                                                          '.' + 'e' * 32 + '.build-abandoned')}
        self.assertEqual({row['path'] for row in result['removed']}, expected_removed)
        for path in [*entries.values(), linked]:
            self.assertEqual(path.exists(), str(path) not in expected_removed, path)
        self.assertTrue((root / storage.INSTALL_LOCK).is_file())
        reasons = {Path(row['path']).name: row['reasons'] for row in result['skipped']}
        self.assertIn('retention-grace', reasons['d' * 32])
        self.assertIn('runtime-in-use', reasons['6' * 32])
        self.assertIn('linked-path', reasons[linked.name])
        self.assertEqual(sentinel.read_bytes(), b'outside')

    def test_runtime_cleanup_holds_the_installation_lock_until_directory_removal(self):
        board = self.board()
        root = Path(os.environ['HMB_RUNTIME_ROOT'])
        candidate = root / ('c' * 32)
        candidate.mkdir(parents=True)
        (candidate / 'payload').write_text('incomplete installation', encoding="utf-8", newline="\n")
        self._age_runtime_entry(candidate)
        (board.directory / 'runtime-retention.json').write_text(json.dumps({
            'current': 'a' * 32, 'previous': 'b' * 32}), encoding="utf-8", newline="\n")
        real_remove = private_dirs.remove_tree
        observed = []
        def remove(tomb):
            lock_path = root / storage.INSTALL_LOCK
            fd = os.open(lock_path, os.O_RDWR)
            try:
                with self.assertRaises(BlockingIOError):
                    locking.lock(fd, blocking=False)
                observed.append(tomb)
                return real_remove(tomb)
            finally:
                os.close(fd)
        with mock.patch.object(storage, 'process_inventory', return_value=([], [], True)), \
             mock.patch.object(storage, 'runtime_usage', return_value=[]), \
             mock.patch.object(private_dirs, 'remove_tree', side_effect=remove):
            result = storage.prune_old_runtimes(board.store)
        self.assertEqual(len(observed), 1)
        self.assertEqual([row['path'] for row in result['removed']], [str(candidate)])
        self.assertFalse(candidate.exists())
        self.assertEqual(list(root.iterdir()), [root / storage.INSTALL_LOCK])

    def test_all_runtime_removal_waits_for_the_root_installation_lock(self):
        board = self.board()
        root = Path(os.environ['HMB_RUNTIME_ROOT'])
        build = root / '.build-abandoned'
        build.mkdir(parents=True)
        (build / 'payload').write_text('unfinished build', encoding="utf-8", newline="\n")
        self._age_runtime_entry(build)
        (board.directory / 'runtime-retention.json').write_text(json.dumps({
            'current': 'a' * 32, 'previous': 'b' * 32}), encoding="utf-8", newline="\n")
        lock_path = root / storage.INSTALL_LOCK
        lock_path.touch(mode=0o600)
        self._age_runtime_entry(lock_path)
        fd = os.open(lock_path, os.O_RDWR)
        with mock.patch.object(storage, 'process_inventory', return_value=([], [], True)), \
             mock.patch.object(storage, 'runtime_usage', return_value=[]):
            try:
                locking.lock(fd, blocking=False)
                result = storage.prune_old_runtimes(board.store)
                self.assertTrue(build.is_dir())
                row = next(row for row in result['skipped'] if row['path'] == str(build))
                self.assertIn('runtime-installation-active', row['reasons'])
            finally:
                os.close(fd)
            result = storage.prune_old_runtimes(board.store)
        self.assertEqual([row['path'] for row in result['removed']], [str(build)])
        self.assertEqual(list(root.iterdir()), [lock_path])

    def _runtime_plan(self, board):
        root = Path(os.environ['HMB_RUNTIME_ROOT'])
        root.mkdir()
        candidate = root / 'expired-runtime'
        candidate.mkdir()
        (candidate / 'READY.json').write_text('{}', encoding="utf-8", newline="\n")
        (candidate / 'payload').write_text('runtime payload', encoding="utf-8", newline="\n")
        (board.directory / 'runtime-retention.json').write_text(json.dumps({'current': 'current', 'previous': 'previous'}), encoding="utf-8", newline="\n")
        patches = (mock.patch('hey_my_buddy.blackboard.tasks.storage.process_inventory', return_value=([], [], True)),
                   mock.patch('hey_my_buddy.blackboard.tasks.storage.runtime_usage', return_value=[]),
                   mock.patch('hey_my_buddy.blackboard.tasks.storage.is_ready', return_value=True))
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)
        planned = board.call('storage_plan', {})
        row = next(row for row in planned['candidates'] if row['path'] == str(candidate))
        self.assertTrue(row['eligible'])
        return candidate, planned, row

    def test_runtime_apply_refuses_an_installer_that_started_after_the_plan(self):
        board = self.board()
        candidate, planned, _ = self._runtime_plan(board)
        lock_path = candidate.parent / storage.INSTALL_LOCK
        with storage.installation_lock(lock_path):
            result = board.call('storage_apply', {
                'planId': planned['planId'], 'commandId': 'installer-won', 'confirm': True})
        self.assertEqual(result['removed'], [])
        self.assertTrue(candidate.is_dir())
        skipped = next(row for row in result['skipped'] if row['path'] == str(candidate))
        self.assertIn('runtime-installation-active', skipped['reasons'])
        self.assertTrue(lock_path.is_file())

    def test_private_state_cannot_prune_the_default_states_runtime_inventory(self):
        board = self.board()
        shared = self.directory / 'shared-default-runtime'
        previous = shared / 'retained-by-another-state'
        previous.mkdir(parents=True)
        (previous / 'READY.json').write_text('{}', encoding="utf-8", newline="\n")
        (board.directory / 'runtime-retention.json').write_text(json.dumps({'current': 'test-current', 'previous': 'test-previous'}), encoding="utf-8", newline="\n")
        with mock.patch.dict(os.environ, {'HMB_RUNTIME_ROOT': str(shared)}), \
             mock.patch('hey_my_buddy.blackboard.tasks.storage.DEFAULT_RUNTIME_ROOT', shared), \
             mock.patch('hey_my_buddy.home.default_state_dir', return_value=self.directory / 'daily-state'), \
             mock.patch('hey_my_buddy.blackboard.tasks.storage.process_inventory', return_value=([], [], True)), \
             mock.patch('hey_my_buddy.blackboard.tasks.storage.runtime_usage', return_value=[]), \
             mock.patch('hey_my_buddy.blackboard.tasks.storage.is_ready', return_value=True):
            result = storage.prune_old_runtimes(board.store)
        self.assertTrue(previous.is_dir())
        self.assertEqual(result['removedBytes'], 0)

    def setUp(self):
        super().setUp()
        self.env = mock.patch.dict(os.environ, {'HMB_RUNTIME_ROOT':str((self.directory / 'runtime').resolve())})
        self.env.start()
        self.addCleanup(self.env.stop)

    def test_unknown_native_owner_and_durable_state_are_never_reclaimed(self):
        board = self.board()
        previous_board = board.directory / 'backups/current/board.sqlite3.gz'
        previous_board.parent.mkdir(parents=True)
        previous_board.write_bytes(b'previous board')
        native = board.directory / 'harnesses/zcode/unknown'
        native.mkdir(parents=True)
        (native / 'sessions.sqlite').write_bytes(b'protected')
        with mock.patch('hey_my_buddy.blackboard.tasks.storage.process_inventory', return_value=([], [], True)):
            planned = board.call('storage_plan', {})
            backup_row = next(row for row in planned['categories'] if row['id'] == 'backup')
            self.assertEqual(backup_row, {
                'id': 'backup', 'bytes': previous_board.stat().st_size, 'reclaimableBytes': 0,
                'count': 1, 'eligibleCount': 0, 'reasons': ['current-backup'],
            })
            self.assertEqual(backup_row['bytes'], previous_board.stat().st_size)
            self.assertEqual(backup_row['reclaimableBytes'], 0)
            native_row = next(r for r in planned['candidates'] if r['category'] == 'zcode')
            self.assertFalse(native_row['eligible'])
            self.assertIn('owner-unproven', native_row['reasons'])
            params = {'planId':planned['planId'], 'commandId':'apply-once', 'confirm':True}
            result = board.call('storage_apply', params)
            self.assertEqual(result['removedBytes'], 0)
            self.assertTrue((native / 'sessions.sqlite').exists())
            self.assertEqual(previous_board.read_bytes(), b'previous board')
            self.assertEqual(board.call('storage_apply', params), result)

    def test_archived_upgrade_evidence_stays_out_of_storage_plan_and_apply(self):
        board = self.board()
        archive = board.directory / 'archives' / 'schema-18'
        archive.mkdir(parents=True)
        evidence = archive / 'board.sqlite3'
        evidence.write_bytes(b'archived board evidence')
        with mock.patch('hey_my_buddy.blackboard.tasks.storage.process_inventory', return_value=([], [], True)):
            planned = board.call('storage_plan', {})
            self.assertFalse(any(Path(row['path']) == archive or
                                 Path(row['path']).is_relative_to(archive) or
                                 archive.is_relative_to(Path(row['path']))
                                 for row in planned['candidates']))
            board.call('storage_apply', {'planId': planned['planId'],
                                         'commandId': 'retain-archive-evidence', 'confirm': True})
        self.assertEqual(evidence.read_bytes(), b'archived board evidence')

    def test_confirmation_expiry_and_command_binding(self):
        board = self.board()
        with mock.patch('hey_my_buddy.blackboard.tasks.storage.process_inventory', return_value=([], [], True)):
            first = board.call('storage_plan', {})
            with self.assertRaises(BoardError) as caught:
                board.call('storage_apply', {'planId':first['planId'], 'commandId':'x'})
            self.assertEqual(caught.exception.code, 'CONFIRMATION_REQUIRED')
            board.call('storage_apply', {'planId':first['planId'], 'commandId':'x', 'confirm':True})
            second = board.call('storage_plan', {})
            with self.assertRaises(BoardError) as caught:
                board.call('storage_apply', {'planId':second['planId'], 'commandId':'x', 'confirm':True})
            self.assertEqual(caught.exception.code, 'CONFLICT')
            path = board.directory / 'storage' / (second['planId'] + '.json')
            data = json.loads(path.read_text(encoding="utf-8"));data['expiresAt']='2000-01-01T00:00:00+00:00';path.write_text(json.dumps(data), encoding="utf-8", newline="\n")
            with self.assertRaises(BoardError) as caught:
                board.call('storage_apply', {'planId':second['planId'], 'commandId':'expired', 'confirm':True})
            self.assertEqual(caught.exception.code, 'PLAN_EXPIRED')

    def test_inventory_does_not_follow_a_link_and_reports_current_previous(self):
        board = self.board()
        root = Path(os.environ['HMB_RUNTIME_ROOT']);root.mkdir()
        older = 'e' * 32
        for name in ('current','previous',older):
            target=root/name;target.mkdir()
            (target/'READY.json').write_text(json.dumps({
                'state': 'READY', 'contentId': name, 'format': 0,
                'runtimeRoot': str(root), 'python': str(target / 'venv/bin/python'),
            }), encoding="utf-8", newline="\n")
        (board.directory/'runtime-retention.json').write_text(json.dumps({'current':'current','previous':'previous'}), encoding="utf-8", newline="\n")
        with mock.patch('hey_my_buddy.blackboard.tasks.storage.process_inventory', return_value=([], [], True)), mock.patch('hey_my_buddy.blackboard.tasks.storage.runtime_usage', return_value=['runtime-in-use']):
            planned=board.call('storage_plan', {})
        rows=[r for r in planned['candidates'] if r['category']=='runtimes']
        self.assertEqual(len(rows),3)
        self.assertTrue(all(not r['eligible'] for r in rows))
        self.assertIn('runtime-identity-unproven', next(r for r in rows if r['path'].endswith('/' + older))['reasons'])

    def test_pruning_reports_bad_entries_and_keeps_older_ready_format(self):
        board = self.board()
        root = Path(os.environ['HMB_RUNTIME_ROOT'])
        root.mkdir()
        older = root / ('a' * 32)
        older.mkdir()
        (older / 'READY.json').write_text(json.dumps({
            'state': 'READY', 'contentId': older.name, 'format': 0,
            'runtimeRoot': str(root), 'python': str(older / 'venv/bin/python'),
        }), encoding="utf-8", newline="\n")
        (older / 'payload').write_bytes(b'old runtime')
        active, unknown = 'b' * 32, 'c' * 32
        for name in ('current', 'previous', active, unknown, 'incomplete'):
            candidate = root / name
            candidate.mkdir()
            if name != 'incomplete':
                (candidate / 'READY.json').write_text(json.dumps({
                    'state': 'READY', 'contentId': name, 'format': 0,
                    'runtimeRoot': str(root), 'python': str(candidate / 'venv/bin/python'),
                }), encoding="utf-8", newline="\n")
        (root / 'install.lock').write_text('lock', encoding="utf-8", newline="\n")
        (root / 'linked').symlink_to(older, target_is_directory=True)
        corrupt = root / 'corrupt-marker'
        corrupt.mkdir()
        (corrupt / 'READY.json').write_bytes(b'\xff')
        nonregular = root / 'nonregular-marker'
        (nonregular / 'READY.json').mkdir(parents=True)
        false_identity = root / ('d' * 32)
        false_identity.mkdir()
        (false_identity / 'READY.json').write_text(json.dumps({
            'state': 'READY', 'contentId': false_identity.name, 'format': 0,
            'runtimeRoot': str(root), 'python': str(self.directory / 'outside-python'),
        }), encoding="utf-8", newline="\n")
        (board.directory / 'runtime-retention.json').write_text(json.dumps({
            'current': 'current', 'previous': 'previous',
        }), encoding="utf-8", newline="\n")
        with mock.patch('hey_my_buddy.blackboard.tasks.storage.process_inventory', return_value=([], [], True)), \
             mock.patch('hey_my_buddy.blackboard.tasks.storage.runtime_usage', side_effect=lambda path, *_: (
                 ['runtime-in-use'] if path.name == active else
                 ['process-inspection-unavailable'] if path.name == unknown else [])):
            result = storage.prune_old_runtimes(board.store)
        self.assertTrue(result['complete'])
        self.assertEqual(result['removed'], [])
        self.assertEqual(result['removedBytes'], 0)
        self.assertTrue(older.exists())
        skipped = {Path(row['path']).name: row['reasons'] for row in result['skipped']}
        self.assertIn('runtime-identity-unproven', skipped[older.name])
        self.assertIn('retained-runtime', skipped['current'])
        self.assertIn('retained-runtime', skipped['previous'])
        self.assertIn('runtime-identity-unproven', skipped[active])
        self.assertIn('runtime-identity-unproven', skipped[unknown])
        self.assertIn('runtime-identity-unproven', skipped['incomplete'])
        self.assertIn('runtime-format-unsupported', skipped['install.lock'])
        self.assertIn('linked-path', skipped['linked'])
        self.assertIn('runtime-identity-unproven', skipped['corrupt-marker'])
        self.assertIn('runtime-format-unsupported', skipped['nonregular-marker'])
        self.assertIn('runtime-identity-unproven', skipped[false_identity.name])
        self.assertEqual(len(result['removed']), 0)
        self.assertEqual(len(result['skipped']), 11)

    def test_linked_native_ancestor_is_listed_without_claiming_ownership(self):
        board=self.board()
        outside=self.directory/'outside-native';outside.mkdir();sentinel=outside/'sessions.sqlite';sentinel.write_bytes(b'outside')
        (board.directory/'harnesses').mkdir()
        (board.directory/'harnesses/zcode').symlink_to(outside, target_is_directory=True)
        with mock.patch('hey_my_buddy.blackboard.tasks.storage.process_inventory', return_value=([],[],True)):
            planned=board.call('storage_plan',{})
            rows=[r for r in planned['candidates'] if r['category']=='zcode']
            self.assertEqual(len(rows),1)
            self.assertFalse(rows[0]['eligible'])
            self.assertEqual(rows[0]['bytes'],len(b'outside'))
            self.assertNotIn('linked-path', rows[0]['reasons'])
            board.call('storage_apply',{'planId':planned['planId'],'commandId':'linked-no-delete','confirm':True})
        self.assertEqual(sentinel.read_bytes(),b'outside')

    def test_codex_user_sessions_are_not_inventory_or_micro_task_removal_targets(self):
        from hey_my_buddy.buddy.harnesses.codex.native_run import session_facts
        board = self.board()
        user_home = self.directory / 'user-home' / '.codex'
        user_home.mkdir(parents=True)
        session = user_home / 'rollout.jsonl'
        session.write_bytes(b'user-owned native session')
        native = private_dirs.ensure_private_dir(private_dirs.native_root(board.directory, 'codex', 'fixture-task'))
        # Even a link left in blackboard-owned storage does not grant ownership
        # of the user store. Inventory never follows it, removal unlinks it.
        (native / 'native-user-store').symlink_to(user_home, target_is_directory=True)
        facts = session_facts(native, 'thread-1')
        self.assertEqual((facts['storageScope'], facts['storageOwner']),
                         ('harness-user-store', 'harness-user-store'))
        with mock.patch('hey_my_buddy.blackboard.tasks.storage.process_inventory', return_value=([], [], True)):
            candidates = storage.inspect(board.store)['candidates']
        self.assertFalse(any(Path(row['path']).is_relative_to(user_home) for row in candidates))
        private = next(row for row in candidates if row.get('adapter') == 'codex')
        self.assertEqual(private['bytes'], 0)
        private_dirs.remove_tree(private_dirs.micro_task_root(board.directory, 'codex', 'fixture-task'))
        self.assertEqual(session.read_bytes(), b'user-owned native session')

    def test_all_adapters_private_micro_tasks_are_visible(self):
        board = self.board()
        state = board.directory.resolve()
        for adapter in ('codex', 'claude', 'dsh', 'zcode'):
            root = private_dirs.ensure_private_dir(private_dirs.native_root(state, adapter, 'unknown-micro-task'))
            (root / 'session.json').write_text('retained', encoding="utf-8", newline="\n")
        with mock.patch('hey_my_buddy.blackboard.tasks.storage.process_inventory', return_value=([], [], True)):
            planned = board.call('storage_plan', {})
            micro_tasks = [row for row in planned['candidates'] if row['category'] == 'harnesses']
            self.assertEqual({row['adapter'] for row in micro_tasks}, {'codex', 'claude', 'dsh', 'zcode'})
            self.assertTrue(all(not row['eligible'] and 'owner-unproven' in row['reasons'] for row in micro_tasks))
            board.call('storage_apply', {'planId':planned['planId'], 'commandId':'protected-micro-tasks', 'confirm':True})

    def test_storage_lock_plan_receipt_and_save_reject_links_without_touching_targets(self):
        board = self.board()
        outside = self.directory / 'outside-record'
        outside.write_text('{"sentinel":"untouched"}', encoding="utf-8", newline="\n")
        storage_root = board.directory / 'storage'
        storage_root.mkdir()
        (storage_root / '.lock').symlink_to(outside)
        with self.assertRaises(BoardError) as caught:
            board.call('storage_plan', {})
        self.assertEqual(caught.exception.code, 'STORAGE_UNSAFE')
        (storage_root / '.lock').unlink()
        with mock.patch('hey_my_buddy.blackboard.tasks.storage.process_inventory', return_value=([], [], True)):
            planned = board.call('storage_plan', {})
        params = {'planId': planned['planId'], 'commandId': 'linked-receipt', 'confirm': True}
        receipt = storage_root / ('receipt-' + hashlib.sha256(b'linked-receipt').hexdigest() + '.json')
        receipt.symlink_to(outside)
        with self.assertRaises(BoardError) as caught:
            board.call('storage_apply', params)
        self.assertEqual(caught.exception.code, 'STORAGE_UNSAFE')
        receipt.unlink()
        plan_path = storage_root / (planned['planId'] + '.json')
        plan_path.unlink()
        plan_path.symlink_to(outside)
        with self.assertRaises(BoardError) as caught:
            board.call('storage_apply', params)
        self.assertEqual(caught.exception.code, 'STORAGE_UNSAFE')
        plan_path.unlink()
        receipt.symlink_to(outside)
        storage._save_receipt(receipt, {'planId': planned['planId']})
        self.assertFalse(receipt.is_symlink())
        self.assertEqual(outside.read_text(encoding="utf-8"), '{"sentinel":"untouched"}')

    def test_pending_journal_must_rebind_eligible_plan_and_fixed_tomb(self):
        board = self.board()
        candidate, planned, row = self._runtime_plan(board)
        outside = self.directory / 'outside-delete-target'
        outside.mkdir()
        sentinel = outside / 'sentinel'
        sentinel.write_text('preserved', encoding="utf-8", newline="\n")
        params = {'planId': planned['planId'], 'commandId': 'forged-pending', 'confirm': True}
        receipt = board.directory / 'storage' / ('receipt-' + hashlib.sha256(b'forged-pending').hexdigest() + '.json')
        identity = candidate.lstat()
        valid = {'id': row['id'], 'path': row['path'], 'fingerprint': row['fingerprint'], 'bytes': row['bytes'],
                 'tomb': str(candidate.with_name('.reclaim-' + planned['planId'] + '-' + candidate.name)),
                 'device': identity.st_dev, 'inode': identity.st_ino}
        base = {'planId': planned['planId'], 'removedBytes': 0, 'removed': [], 'skipped': [], 'complete': False}
        for corrupted in ({**valid, 'path': str(outside)},
                          {**valid, 'tomb': str(outside)},
                          {**valid, 'fingerprint': 'forged'},
                          {**valid, 'id': 'forged'},
                          {**valid, 'bytes': row['bytes'] + 1}):
            receipt.write_text(json.dumps({**base, 'pending': corrupted}), encoding="utf-8", newline="\n")
            with self.assertRaises(BoardError) as caught:
                board.call('storage_apply', params)
            self.assertEqual(caught.exception.code, 'STORAGE_UNSAFE')
            self.assertTrue(candidate.exists())
            self.assertEqual(sentinel.read_text(encoding="utf-8"), 'preserved')
        receipt.write_text(json.dumps({**base, 'pending': valid}), encoding="utf-8", newline="\n")
        plan_path = board.directory / 'storage' / (planned['planId'] + '.json')
        for update in ({'eligible': False}, {'category': 'durable'}, {'category': 'harnesses'}):
            altered = json.loads(json.dumps(planned))
            next(item for item in altered['candidates'] if item['id'] == row['id']).update(update)
            plan_path.write_text(json.dumps(altered), encoding="utf-8", newline="\n")
            with self.assertRaises(BoardError) as caught:
                board.call('storage_apply', params)
            self.assertEqual(caught.exception.code, 'STORAGE_UNSAFE')
            self.assertTrue(candidate.exists())
        plan_path.write_text(json.dumps(planned), encoding="utf-8", newline="\n")
        receipt.write_text(json.dumps({**base, 'planId': 'stg-' + 'f' * 32, 'pending': valid}), encoding="utf-8", newline="\n")
        with self.assertRaises(BoardError) as caught:
            board.call('storage_apply', params)
        self.assertEqual(caught.exception.code, 'CONFLICT')
        self.assertTrue(candidate.exists())

    def test_linked_complete_and_pending_receipts_never_read_their_journals(self):
        board = self.board()
        candidate, planned, row = self._runtime_plan(board)
        outside = self.directory / 'external-receipt'
        receipt = board.directory / 'storage' / ('receipt-' + hashlib.sha256(b'linked-journal').hexdigest() + '.json')
        receipt.symlink_to(outside)
        for complete in (True, False):
            with self.subTest(complete=complete):
                value = {'planId': planned['planId'], 'complete': complete, 'removedBytes': 0,
                         'removed': [], 'skipped': []}
                if not complete:
                    value['pending'] = {**row, 'tomb': str(self.directory / 'external-delete')}
                encoded = json.dumps(value)
                outside.write_text(encoded, encoding="utf-8", newline="\n")
                with mock.patch('hey_my_buddy.blackboard.tasks.storage.json.load', side_effect=AssertionError('External journal was read')):
                    with self.assertRaises(BoardError) as caught:
                        board.call('storage_apply', {'planId': planned['planId'], 'commandId': 'linked-journal', 'confirm': True})
                self.assertEqual(caught.exception.code, 'STORAGE_UNSAFE')
                self.assertTrue(candidate.exists())
                self.assertEqual(outside.read_text(encoding="utf-8"), encoded)

    def test_pending_tomb_cannot_redirect_deletion_after_live_name_is_absent(self):
        board = self.board()
        candidate, planned, row = self._runtime_plan(board)
        candidate.rename(candidate.with_name('parked-candidate'))
        outside = self.directory / 'external-tomb-target'
        outside.mkdir()
        sentinel = outside / 'sentinel'
        sentinel.write_text('keep outside', encoding="utf-8", newline="\n")
        receipt = board.directory / 'storage' / ('receipt-' + hashlib.sha256(b'outside-tomb').hexdigest() + '.json')
        receipt.write_text(json.dumps({'planId': planned['planId'], 'complete': False,
            'removedBytes': 0, 'removed': [], 'skipped': [],
            'pending': {key: row[key] for key in ('id', 'path', 'fingerprint', 'bytes')} | {'tomb': str(outside)}}), encoding="utf-8", newline="\n")
        with self.assertRaises(BoardError) as caught:
            board.call('storage_apply', {'planId': planned['planId'], 'commandId': 'outside-tomb', 'confirm': True})
        self.assertEqual(caught.exception.code, 'STORAGE_UNSAFE')
        self.assertEqual(sentinel.read_text(encoding="utf-8"), 'keep outside')

    def test_linked_storage_root_allows_plan_and_receipt(self):
        board = self.board()
        redirect = self.directory / 'storage-target'
        redirect.mkdir()
        (board.directory / 'storage').symlink_to(redirect, target_is_directory=True)
        with mock.patch.object(storage, 'process_inventory', return_value=([], [], True)):
            planned = board.call('storage_plan', {})
            result = board.call('storage_apply', {'planId': planned['planId'],
                                                  'commandId': 'linked-storage-root', 'confirm': True})
        self.assertTrue(result['complete'])
        self.assertTrue((redirect / (planned['planId'] + '.json')).is_file())
        self.assertTrue((redirect / ('receipt-' + hashlib.sha256(b'linked-storage-root').hexdigest() + '.json')).is_file())

    def test_pending_removal_replays_after_plan_expiry_and_partial_tomb_cleanup(self):
        board = self.board()
        candidate, planned, _ = self._runtime_plan(board)
        params = {'planId': planned['planId'], 'commandId': 'replay-pending', 'confirm': True}
        real_remove = private_dirs.remove_tree
        with mock.patch('hey_my_buddy.blackboard.tasks.storage.private_dirs.remove_tree', side_effect=OSError('interrupted')):
            with self.assertRaises(BoardError) as caught:
                board.call('storage_apply', params)
        self.assertEqual(caught.exception.code, 'STORAGE_INCOMPLETE')
        tomb = candidate.with_name('.reclaim-' + planned['planId'] + '-' + candidate.name)
        self.assertTrue(tomb.exists())
        (tomb / 'payload').unlink()
        plan_path = board.directory / 'storage' / (planned['planId'] + '.json')
        value = json.loads(plan_path.read_text(encoding="utf-8"))
        value['expiresAt'] = '2000-01-01T00:00:00+00:00'
        plan_path.write_text(json.dumps(value), encoding="utf-8", newline="\n")
        with mock.patch('hey_my_buddy.blackboard.tasks.storage.private_dirs.remove_tree', wraps=real_remove):
            result = board.call('storage_apply', params)
        self.assertTrue(result['complete'])
        self.assertEqual(len(result['removed']), 1)
        self.assertFalse(tomb.exists())

    def test_pending_receipt_requires_device_and_inode_before_deleting_tomb(self):
        board = self.board()
        candidate, planned, row = self._runtime_plan(board)
        tomb = candidate.with_name('.reclaim-' + planned['planId'] + '-' + candidate.name)
        candidate.rename(tomb)
        params = {'planId': planned['planId'], 'commandId': 'legacy-pending', 'confirm': True}
        receipt = board.directory / 'storage' / ('receipt-' + hashlib.sha256(b'legacy-pending').hexdigest() + '.json')
        pending = {'id': row['id'], 'path': row['path'], 'tomb': str(tomb),
                   'bytes': row['bytes'], 'fingerprint': row['fingerprint']}
        identity = tomb.lstat()
        plan_path = board.directory / 'storage' / (planned['planId'] + '.json')
        value = json.loads(plan_path.read_text(encoding="utf-8"))
        value['expiresAt'] = '2000-01-01T00:00:00+00:00'
        plan_path.write_text(json.dumps(value), encoding="utf-8", newline="\n")
        for incomplete in (pending, {**pending, 'device': identity.st_dev},
                           {**pending, 'inode': identity.st_ino}):
            receipt.write_text(json.dumps({'planId': planned['planId'], 'removedBytes': 0, 'removed': [],
                                           'skipped': [], 'complete': False, 'pending': incomplete}),
                               encoding='utf-8', newline='\n')
            with self.assertRaises(BoardError) as caught:
                board.call('storage_apply', params)
            self.assertEqual(caught.exception.code, 'STORAGE_UNSAFE')
            self.assertTrue(tomb.exists())

    def test_replaced_pending_tomb_is_not_deleted(self):
        board = self.board()
        candidate, planned, _ = self._runtime_plan(board)
        params = {'planId': planned['planId'], 'commandId': 'replaced-tomb', 'confirm': True}
        with mock.patch('hey_my_buddy.blackboard.tasks.storage.private_dirs.remove_tree', side_effect=OSError('interrupted')):
            with self.assertRaises(BoardError) as caught:
                board.call('storage_apply', params)
        self.assertEqual(caught.exception.code, 'STORAGE_INCOMPLETE')
        tomb = candidate.with_name('.reclaim-' + planned['planId'] + '-' + candidate.name)
        tomb.rename(tomb.with_name('parked-original'))
        tomb.mkdir()
        sentinel = tomb / 'replacement-sentinel'
        sentinel.write_text('retain replacement', encoding="utf-8", newline="\n")
        with self.assertRaises(BoardError) as caught:
            board.call('storage_apply', params)
        self.assertEqual(caught.exception.code, 'STORAGE_CHANGED')
        self.assertEqual(sentinel.read_text(encoding="utf-8"), 'retain replacement')

    def test_linked_runtime_and_ready_path_are_protected_before_ready_read(self):
        board = self.board()
        root = Path(os.environ['HMB_RUNTIME_ROOT'])
        root.mkdir()
        outside = self.directory / 'outside-ready'
        outside.mkdir()
        (outside / 'READY.json').write_text('outside', encoding="utf-8", newline="\n")
        (root / 'linked-runtime').symlink_to(outside, target_is_directory=True)
        local = root / 'local-runtime'
        local.mkdir()
        (local / 'READY.json').symlink_to(outside / 'READY.json')
        with mock.patch('hey_my_buddy.blackboard.tasks.storage.process_inventory', return_value=([], [], True)), \
             mock.patch('hey_my_buddy.blackboard.tasks.storage.is_ready', side_effect=AssertionError('external READY was read')) as ready:
            planned = board.call('storage_plan', {})
        ready.assert_not_called()
        rows = [row for row in planned['candidates'] if row['category'] == 'runtimes']
        self.assertEqual({Path(row['path']).name for row in rows}, {'linked-runtime', 'local-runtime'})
        reasons = {Path(row['path']).name: row['reasons'] for row in rows}
        self.assertIn('linked-path', reasons['linked-runtime'])
        self.assertIn('runtime-format-unsupported', reasons['local-runtime'])
        self.assertNotIn('linked-path', reasons['local-runtime'])
        self.assertTrue(all(not row['eligible'] for row in rows))

    def test_linked_runtime_ancestor_does_not_block_real_runtime_leaf(self):
        board = self.board()
        actual_parent = self.directory / 'actual-parent'
        actual_parent.mkdir()
        linked_parent = self.directory / 'linked-parent'
        linked_parent.symlink_to(actual_parent, target_is_directory=True)
        root = linked_parent / 'runtime'
        root.mkdir()
        candidate = root / ('c' * 32)
        candidate.mkdir()
        (candidate / 'payload').write_bytes(b'incomplete installation')
        self._age_runtime_entry(candidate)
        (board.directory / 'runtime-retention.json').write_text(json.dumps({
            'current': 'a' * 32, 'previous': 'b' * 32}), encoding='utf-8', newline='\n')
        with mock.patch.dict(os.environ, {'HMB_RUNTIME_ROOT': str(root)}), \
             mock.patch.object(storage, 'process_inventory', return_value=([], [], True)), \
             mock.patch.object(storage, 'runtime_usage', return_value=[]):
            planned = board.call('storage_plan', {})
        row = next(row for row in planned['candidates'] if row['path'] == str(candidate))
        self.assertTrue(row['eligible'])
        self.assertNotIn('linked-path', row['reasons'])

    def test_reparse_runtime_and_ready_are_protected_without_reading_ready(self):
        board = self.board()
        root = Path(os.environ['HMB_RUNTIME_ROOT']).resolve()
        root.mkdir()
        candidate = root / 'reparse-runtime'
        candidate.mkdir()
        ready_path = candidate / 'READY.json'
        ready_path.write_text('retained READY', encoding="utf-8", newline="\n")
        original = Path.lstat
        for marked in (candidate, ready_path):
            with self.subTest(marked=marked):
                def reparse(path, *args, **kwargs):
                    value = original(path, *args, **kwargs)
                    if private_dirs._absolute(path) == marked:
                        return SimpleNamespace(st_mode=value.st_mode, st_file_attributes=0x400,
                                               st_ino=value.st_ino, st_size=value.st_size,
                                               st_mtime_ns=value.st_mtime_ns, st_dev=value.st_dev)
                    return value
                with mock.patch.object(Path, 'lstat', reparse), \
                     mock.patch('hey_my_buddy.blackboard.tasks.storage.process_inventory', return_value=([], [], True)), \
                     mock.patch('hey_my_buddy.blackboard.tasks.storage.is_ready', side_effect=AssertionError('Reparse READY was read')) as ready:
                    planned = board.call('storage_plan', {})
                ready.assert_not_called()
                row = next(row for row in planned['candidates'] if Path(row['path']).name == candidate.name)
                self.assertFalse(row['eligible'])
                self.assertIn('linked-path' if marked == candidate else 'runtime-format-unsupported', row['reasons'])
        self.assertEqual(ready_path.read_text(encoding="utf-8"), 'retained READY')

    def test_unaccepted_unknown_shutdown_reports_both_protections(self):
        workflow = mock.Mock()
        workflow.shutdown_summary.return_value = {'selfConfirmed': None, 'descendantsConfirmed': None}
        store = mock.Mock(workflow=workflow)
        reasons = storage._zcode_reasons(store, object(), {'run_id': 'run', 'state': 'running'},
                                         {'accepted_at': None}, 0)
        self.assertEqual(reasons, ['continuation-supported', 'shutdown-unconfirmed'])


class ProcessInventoryServiceTests(BoardTestCase):
    """Literal argv fixtures identify services independently of the entry table."""

    def test_real_inventory_recognizes_current_service_modules(self):
        state = self.directory / 'state'
        state.mkdir()
        foreign = self.directory / '另一个 board'
        foreign.mkdir()
        runtime = self.directory / '路径 with spaces' / 'runtime' / ('a' * 32)
        python = str(runtime / 'venv/bin/python')
        fixtures = [
            process(101, ['/usr/bin/python3', '-P', '-m', 'buddy.daemon', '--state-dir', str(foreign)]),
            process(102, ['/usr/bin/python3', '-m', 'buddy.worker.supervisor', '--worker-id', 'old-0', '--state-dir', str(foreign)]),
            process(103, [python, '-P', '-m', 'hey_my_buddy.blackboard.service.daemon'],
                    files=[foreign / 'board-owner.lock']),
            process(104, [python, '-m', 'hey_my_buddy.buddy.runtime.execution', '--startup',
                          str(foreign / 'harnesses/dsh/micro-tasks/t/attempts/a/execution-start.json')]),
            *[process(pid, ['/usr/bin/python3', '-m', module, '--state-dir', str(foreign)])
              for pid, module in enumerate([
                  'buddy.daemonization', 'xbuddy.daemon', 'hey_my_buddy.buddy.runtime.supervisord',
                  'hey_my_buddy.blackboard.service.daemonware', 'hey_my_buddy.buddy.runtime.executionish',
                  'buddy.client'], 201)],
        ]
        with processes(*fixtures):
            orphans, commands, known = storage.process_inventory(state)
        self.assertTrue(known)
        self.assertEqual({row['pid']: row for row in orphans}, {
            103: {'pid': 103, 'kind': 'daemon', 'stateDir': str(foreign), 'runtimeDir': str(runtime)},
            104: {'pid': 104, 'kind': 'execution', 'stateDir': str(foreign), 'runtimeDir': str(runtime)},
        })
        self.assertEqual(len(commands), 10)
        self.assertIn(f'/usr/bin/python3 -m buddy.daemonization --state-dir {foreign}', commands)

    def test_execution_startup_path_with_spaces_identifies_its_board(self):
        state = self.directory / 'observed board'
        state.mkdir()
        foreign = self.directory / '另一个 execution-start.json board'
        foreign.mkdir()
        execution = process(104, ['/usr/bin/python3', '-m', 'hey_my_buddy.buddy.runtime.execution',
                                  '--startup', str(foreign / 'harnesses/dsh/micro-tasks/t/attempts/a/execution-start.json')])
        with processes(execution):
            orphans, _, known = storage.process_inventory(state)
        execution.open_files.assert_not_called()
        self.assertTrue(known)
        self.assertEqual(orphans[0]['stateDir'], str(foreign))

    def test_an_argument_containing_module_syntax_is_not_a_service(self):
        fixture = process(104, ['python', '-c', 'print(" -m hey_my_buddy.blackboard.service.daemon")'])
        with processes(fixture):
            orphans, _, known = storage.process_inventory(self.directory)
        self.assertEqual(orphans, [])
        self.assertTrue(known)

    def test_unreadable_or_changed_processes_keep_runtime_status_unknown(self):
        for error in (storage.psutil.AccessDenied(104), AttributeError('cmdline missing')):
            with self.subTest(error=type(error).__name__):
                fixture = process(104, ['python'])
                fixture.cmdline.side_effect = error
                with processes(fixture):
                    _, commands, known = storage.process_inventory(self.directory)
                self.assertFalse(known)
                self.assertEqual(storage.runtime_usage(self.directory, commands, known),
                                 ['process-inspection-unavailable'])
        fixture = process(104, ['python'])
        replacement = process(104)
        replacement.create_time.return_value = 124.0
        with processes(fixture, current={104: replacement}):
            self.assertFalse(storage.process_inventory(self.directory)[2])

    def test_missing_cmdline_and_unreadable_identity_are_unknown(self):
        for attribute, result in (('cmdline', None), ('create_time', None)):
            with self.subTest(attribute=attribute):
                fixture = process(104)
                getattr(fixture, attribute).return_value = result
                with processes(fixture):
                    self.assertFalse(storage.process_inventory(self.directory)[2])
        with mock.patch.object(storage.psutil, 'pids', return_value=[104]), \
             mock.patch.object(storage.psutil, 'Process', side_effect=storage.psutil.NoSuchProcess(104)):
            self.assertTrue(storage.process_inventory(self.directory)[2])

    def test_unreadable_service_files_keep_state_and_runtime_usage_unknown(self):
        fixture = process(104, ['python', '-m', 'hey_my_buddy.blackboard.service.daemon'])
        fixture.open_files.side_effect = storage.psutil.AccessDenied(104)
        with processes(fixture):
            orphans, _, known = storage.process_inventory(self.directory)
        self.assertFalse(known)
        self.assertIsNone(orphans[0]['stateDir'])

    def test_ended_service_is_not_an_orphan_or_runtime_user(self):
        for phase in ('cmdline', 'open_files'):
            for error in (storage.psutil.NoSuchProcess(104), storage.psutil.ZombieProcess(104)):
                with self.subTest(phase=phase, error=type(error).__name__):
                    fixture = process(104, ['python', '-m', 'hey_my_buddy.blackboard.service.daemon'])
                    getattr(fixture, phase).side_effect = error
                    with processes(fixture):
                        orphans, commands, known = storage.process_inventory(self.directory)
                    self.assertEqual(orphans, [])
                    self.assertEqual(commands, [])
                    self.assertTrue(known)

    @skipIf(os.name == 'nt', 'POSIX runtime absence proof; Windows remains unknown')
    def test_runtime_references_from_cwd_mappings_and_deleted_descriptors(self):
        runtime = self.directory / '运行时 with spaces'
        for channel in ('cwd', 'mappings', 'deleted-file', 'directory-fd'):
            with self.subTest(channel=channel):
                fixture = process(104, cwd=str(runtime) if channel == 'cwd' else '/',
                                  mappings=[runtime / 'library.so'] if channel == 'mappings' else [])
                opened = (f'p104\nn{runtime}/unlinked (deleted)\n' if channel == 'deleted-file' else
                          f'p104\nn{runtime}\n' if channel == 'directory-fd' else 'p104\n')
                with (processes(fixture),
                      mock.patch.object(storage.psutil, 'WINDOWS', False),
                      mock.patch.object(storage.psutil, 'LINUX', True),
                      mock.patch.object(storage.shutil, 'which', return_value='lsof'),
                      mock.patch.object(storage.subprocess, 'run',
                                        return_value=SimpleNamespace(stdout=opened, stderr='', returncode=0)) as native):
                    self.assertEqual(storage.runtime_usage(runtime, [], True), ['runtime-in-use'])
                self.assertIn('^cwd,^mem,^DEL,^txt', native.call_args.args[0])
                fixture.open_files.assert_not_called()  # Native descriptors are the one channel.

    @skipIf(os.name == 'nt', 'POSIX runtime absence proof; Windows remains unknown')
    def test_incomplete_path_observation_never_releases_a_runtime(self):
        for attribute in ('cwd', 'memory_maps', 'uids', 'status'):
            with self.subTest(attribute=attribute):
                fixture = process(104)
                getattr(fixture, attribute).side_effect = storage.psutil.AccessDenied(104)
                with (processes(fixture),
                      mock.patch.object(storage.psutil, 'WINDOWS', False),
                      mock.patch.object(storage.psutil, 'LINUX', True),
                      mock.patch.object(storage.shutil, 'which', return_value='lsof'),
                      mock.patch.object(storage.subprocess, 'run',
                                        return_value=SimpleNamespace(stdout='', stderr='', returncode=0))):
                    self.assertEqual(storage.runtime_usage(self.directory, [], True),
                                     ['process-inspection-unavailable'])

    @skipIf(os.name == 'nt', 'POSIX runtime absence proof; Windows remains unknown')
    def test_complete_unreferenced_runtime_and_incomplete_native_channels(self):
        for native, expected in ((SimpleNamespace(stdout='p104\n', stderr='', returncode=0), []),
                                 (SimpleNamespace(stdout='', stderr='permission denied', returncode=0),
                                  ['process-inspection-unavailable']),
                                 (SimpleNamespace(stdout='', stderr='', returncode=1),
                                  ['process-inspection-unavailable'])):
            with self.subTest(native=native), processes(process(104)), \
                 mock.patch.object(storage.psutil, 'WINDOWS', False), \
                 mock.patch.object(storage.psutil, 'LINUX', True), \
                 mock.patch.object(storage.shutil, 'which', return_value='lsof'), \
                 mock.patch.object(storage.subprocess, 'run', return_value=native):
                self.assertEqual(storage.runtime_usage(self.directory, [], True), expected)
