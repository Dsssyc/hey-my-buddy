"""Private protected inventory and exact-plan guards."""
import hashlib
import json
import os
from pathlib import Path
import stat
from types import SimpleNamespace
from unittest import mock
from hey_my_buddy.errors import BoardError
from hey_my_buddy import private_dirs, locking
from hey_my_buddy.blackboard.tasks import storage
from support import BoardTestCase

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
            'current': current, 'previous': previous}))
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
        orphan = root / ('.' + '1' * 32 + '.install.lock')
        recent_lock = root / ('.' + '2' * 32 + '.install.lock')
        held_lock = root / ('.' + '3' * 32 + '.install.lock')
        directory_lock = root / ('.' + '0' * 32 + '.install.lock')
        retained_lock = root / ('.' + current + '.install.lock')
        for path in (orphan, recent_lock, held_lock, directory_lock, retained_lock):
            path.touch(mode=0o600)
            self._age_runtime_entry(path)
        recent_lock.touch()
        outside = self.directory / 'outside-runtime'
        outside.mkdir()
        sentinel = outside / 'payload'
        sentinel.write_bytes(b'outside')
        linked = root / ('9' * 32)
        linked.symlink_to(outside, target_is_directory=True)
        linked_lock = root / ('.' + '8' * 32 + '.install.lock')
        linked_lock.symlink_to(sentinel)
        owners = [os.open(path, os.O_RDWR) for path in (held_lock, directory_lock)]
        try:
            for fd in owners:
                locking.lock(fd, blocking=False)
            with mock.patch.object(storage, 'process_inventory', return_value=([], [], True)), \
                 mock.patch.object(storage, 'runtime_usage', side_effect=lambda path, *_: (
                     ['runtime-in-use'] if path.name == '6' * 32 else [])):
                result = storage.prune_old_runtimes(board.store)
        finally:
            for fd in owners:
                os.close(fd)
        self.assertTrue(result['complete'])
        expected_removed = {str(entries['c' * 32]), str(entries['.' + 'e' * 32 + '.build-abandoned']), str(orphan)}
        self.assertEqual({row['path'] for row in result['removed']}, expected_removed)
        for path in [*entries.values(), orphan, recent_lock, held_lock, directory_lock, retained_lock, linked, linked_lock]:
            self.assertEqual(path.exists(), str(path) not in expected_removed, path)
        reasons = {Path(row['path']).name: row['reasons'] for row in result['skipped']}
        self.assertIn('retention-grace', reasons['d' * 32])
        self.assertIn('retention-grace', reasons[recent_lock.name])
        self.assertIn('runtime-installation-active', reasons[held_lock.name])
        self.assertIn('runtime-installation-active', reasons['0' * 32])
        self.assertIn('runtime-in-use', reasons['6' * 32])
        self.assertIn('linked-path', reasons[linked.name])
        self.assertIn('linked-path', reasons[linked_lock.name])
        self.assertEqual(sentinel.read_bytes(), b'outside')

    def test_runtime_cleanup_holds_the_installation_lock_until_directory_removal(self):
        board = self.board()
        root = Path(os.environ['HMB_RUNTIME_ROOT'])
        candidate = root / ('c' * 32)
        candidate.mkdir(parents=True)
        (candidate / 'payload').write_text('incomplete installation')
        self._age_runtime_entry(candidate)
        (board.directory / 'runtime-retention.json').write_text(json.dumps({
            'current': 'a' * 32, 'previous': 'b' * 32}))
        real_remove = private_dirs.remove_tree
        observed = []
        def remove(tomb):
            lock_path = root / f'.{candidate.name}.install.lock'
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
        self.assertEqual(list(root.iterdir()), [])

    def test_unbound_build_leftover_waits_for_every_existing_installation_lock(self):
        board = self.board()
        root = Path(os.environ['HMB_RUNTIME_ROOT'])
        build = root / '.build-abandoned'
        build.mkdir(parents=True)
        (build / 'payload').write_text('unfinished build')
        self._age_runtime_entry(build)
        (board.directory / 'runtime-retention.json').write_text(json.dumps({
            'current': 'a' * 32, 'previous': 'b' * 32}))
        lock_path = root / ('.' + 'a' * 32 + '.install.lock')
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
        (candidate / 'READY.json').write_text('{}')
        (candidate / 'payload').write_text('runtime payload')
        (board.directory / 'runtime-retention.json').write_text(json.dumps({'current': 'current', 'previous': 'previous'}))
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

    def test_private_state_cannot_prune_the_default_states_runtime_inventory(self):
        board = self.board()
        shared = self.directory / 'shared-default-runtime'
        previous = shared / 'retained-by-another-state'
        previous.mkdir(parents=True)
        (previous / 'READY.json').write_text('{}')
        (board.directory / 'runtime-retention.json').write_text(json.dumps({'current': 'test-current', 'previous': 'test-previous'}))
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
            data = json.loads(path.read_text());data['expiresAt']='2000-01-01T00:00:00+00:00';path.write_text(json.dumps(data))
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
            }))
        (board.directory/'runtime-retention.json').write_text(json.dumps({'current':'current','previous':'previous'}))
        with mock.patch('hey_my_buddy.blackboard.tasks.storage.process_inventory', return_value=([], [], True)), mock.patch('hey_my_buddy.blackboard.tasks.storage.runtime_usage', return_value=['runtime-in-use']):
            planned=board.call('storage_plan', {})
        rows=[r for r in planned['candidates'] if r['category']=='runtimes']
        self.assertEqual(len(rows),3)
        self.assertTrue(all(not r['eligible'] for r in rows))
        self.assertIn('runtime-in-use', next(r for r in rows if r['path'].endswith('/' + older))['reasons'])

    def test_pruning_reports_bad_entries_and_reclaims_an_unused_older_ready_format(self):
        board = self.board()
        root = Path(os.environ['HMB_RUNTIME_ROOT'])
        root.mkdir()
        older = root / ('a' * 32)
        older.mkdir()
        (older / 'READY.json').write_text(json.dumps({
            'state': 'READY', 'contentId': older.name, 'format': 0,
            'runtimeRoot': str(root), 'python': str(older / 'venv/bin/python'),
        }))
        (older / 'payload').write_bytes(b'old runtime')
        expected_bytes = sum(path.stat().st_size for path in older.iterdir())
        active, unknown = 'b' * 32, 'c' * 32
        for name in ('current', 'previous', active, unknown, 'incomplete'):
            candidate = root / name
            candidate.mkdir()
            if name != 'incomplete':
                (candidate / 'READY.json').write_text(json.dumps({
                    'state': 'READY', 'contentId': name, 'format': 0,
                    'runtimeRoot': str(root), 'python': str(candidate / 'venv/bin/python'),
                }))
        (root / 'install.lock').write_text('lock')
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
        }))
        (board.directory / 'runtime-retention.json').write_text(json.dumps({
            'current': 'current', 'previous': 'previous',
        }))
        with mock.patch('hey_my_buddy.blackboard.tasks.storage.process_inventory', return_value=([], [], True)), \
             mock.patch('hey_my_buddy.blackboard.tasks.storage.runtime_usage', side_effect=lambda path, *_: (
                 ['runtime-in-use'] if path.name == active else
                 ['process-inspection-unavailable'] if path.name == unknown else [])):
            result = storage.prune_old_runtimes(board.store)
        self.assertTrue(result['complete'])
        self.assertEqual([row['path'] for row in result['removed']], [str(older)])
        self.assertEqual(result['removedBytes'], expected_bytes)
        self.assertFalse(older.exists())
        skipped = {Path(row['path']).name: row['reasons'] for row in result['skipped']}
        self.assertIn('retained-runtime', skipped['current'])
        self.assertIn('retained-runtime', skipped['previous'])
        self.assertIn('runtime-in-use', skipped[active])
        self.assertIn('process-inspection-unavailable', skipped[unknown])
        self.assertIn('runtime-identity-unproven', skipped['incomplete'])
        self.assertIn('runtime-format-unsupported', skipped['install.lock'])
        self.assertIn('linked-path', skipped['linked'])
        self.assertIn('runtime-identity-unproven', skipped['corrupt-marker'])
        self.assertIn('runtime-format-unsupported', skipped['nonregular-marker'])
        self.assertIn('runtime-identity-unproven', skipped[false_identity.name])
        self.assertEqual(len(result['removed']), 1)
        self.assertEqual(len(result['skipped']), 10)

    def test_linked_native_root_does_not_read_or_reclaim_external_home(self):
        board=self.board()
        outside=self.directory/'outside-native';outside.mkdir();sentinel=outside/'sessions.sqlite';sentinel.write_bytes(b'outside')
        (board.directory/'harnesses').mkdir()
        (board.directory/'harnesses/zcode').symlink_to(outside, target_is_directory=True)
        with mock.patch('hey_my_buddy.blackboard.tasks.storage.process_inventory', return_value=([],[],True)):
            planned=board.call('storage_plan',{})
            rows=[r for r in planned['candidates'] if r['category']=='zcode']
            self.assertEqual(len(rows),1)
            self.assertFalse(rows[0]['eligible'])
            self.assertEqual(rows[0]['bytes'],0)
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
            (root / 'session.json').write_text('retained')
        with mock.patch('hey_my_buddy.blackboard.tasks.storage.process_inventory', return_value=([], [], True)):
            planned = board.call('storage_plan', {})
            micro_tasks = [row for row in planned['candidates'] if row['category'] == 'harnesses']
            self.assertEqual({row['adapter'] for row in micro_tasks}, {'codex', 'claude', 'dsh', 'zcode'})
            self.assertTrue(all(not row['eligible'] and 'owner-unproven' in row['reasons'] for row in micro_tasks))
            board.call('storage_apply', {'planId':planned['planId'], 'commandId':'protected-micro-tasks', 'confirm':True})

    def test_storage_lock_plan_receipt_and_save_reject_links_without_touching_targets(self):
        board = self.board()
        outside = self.directory / 'outside-record'
        outside.write_text('{"sentinel":"untouched"}')
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
        with mock.patch('hey_my_buddy.blackboard.tasks.storage.private_dirs.open_regular_fd', wraps=private_dirs.open_regular_fd) as opened:
            with self.assertRaises(BoardError) as caught:
                board.call('storage_apply', params)
        self.assertFalse(any(call.args[0] == receipt for call in opened.call_args_list))
        self.assertEqual(caught.exception.code, 'STORAGE_UNSAFE')
        receipt.unlink()
        plan_path = storage_root / (planned['planId'] + '.json')
        plan_path.unlink()
        plan_path.symlink_to(outside)
        with mock.patch('hey_my_buddy.blackboard.tasks.storage.private_dirs.open_regular_fd', wraps=private_dirs.open_regular_fd) as opened:
            with self.assertRaises(BoardError) as caught:
                board.call('storage_apply', params)
        self.assertFalse(any(call.args[0] == plan_path for call in opened.call_args_list))
        self.assertEqual(caught.exception.code, 'STORAGE_UNSAFE')
        plan_path.unlink()
        receipt.symlink_to(outside)
        with self.assertRaises(BoardError) as caught:
            storage._save_receipt(receipt, {'planId': planned['planId']})
        self.assertEqual(caught.exception.code, 'STORAGE_UNSAFE')
        self.assertEqual(outside.read_text(), '{"sentinel":"untouched"}')

    def test_pending_journal_must_rebind_eligible_plan_and_fixed_tomb(self):
        board = self.board()
        candidate, planned, row = self._runtime_plan(board)
        outside = self.directory / 'outside-delete-target'
        outside.mkdir()
        sentinel = outside / 'sentinel'
        sentinel.write_text('preserved')
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
            receipt.write_text(json.dumps({**base, 'pending': corrupted}))
            with self.assertRaises(BoardError) as caught:
                board.call('storage_apply', params)
            self.assertEqual(caught.exception.code, 'STORAGE_UNSAFE')
            self.assertTrue(candidate.exists())
            self.assertEqual(sentinel.read_text(), 'preserved')
        receipt.write_text(json.dumps({**base, 'pending': valid}))
        plan_path = board.directory / 'storage' / (planned['planId'] + '.json')
        for update in ({'eligible': False}, {'category': 'durable'}, {'category': 'harnesses'}):
            altered = json.loads(json.dumps(planned))
            next(item for item in altered['candidates'] if item['id'] == row['id']).update(update)
            plan_path.write_text(json.dumps(altered))
            with self.assertRaises(BoardError) as caught:
                board.call('storage_apply', params)
            self.assertEqual(caught.exception.code, 'STORAGE_UNSAFE')
            self.assertTrue(candidate.exists())
        plan_path.write_text(json.dumps(planned))
        receipt.write_text(json.dumps({**base, 'planId': 'stg-' + 'f' * 32, 'pending': valid}))
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
                outside.write_text(encoded)
                with mock.patch('hey_my_buddy.blackboard.tasks.storage.json.load', side_effect=AssertionError('External journal was read')):
                    with self.assertRaises(BoardError) as caught:
                        board.call('storage_apply', {'planId': planned['planId'], 'commandId': 'linked-journal', 'confirm': True})
                self.assertEqual(caught.exception.code, 'STORAGE_UNSAFE')
                self.assertTrue(candidate.exists())
                self.assertEqual(outside.read_text(), encoded)

    def test_pending_tomb_cannot_redirect_deletion_after_live_name_is_absent(self):
        board = self.board()
        candidate, planned, row = self._runtime_plan(board)
        candidate.rename(candidate.with_name('parked-candidate'))
        outside = self.directory / 'external-tomb-target'
        outside.mkdir()
        sentinel = outside / 'sentinel'
        sentinel.write_text('keep outside')
        receipt = board.directory / 'storage' / ('receipt-' + hashlib.sha256(b'outside-tomb').hexdigest() + '.json')
        receipt.write_text(json.dumps({'planId': planned['planId'], 'complete': False,
            'removedBytes': 0, 'removed': [], 'skipped': [],
            'pending': {key: row[key] for key in ('id', 'path', 'fingerprint', 'bytes')} | {'tomb': str(outside)}}))
        with self.assertRaises(BoardError) as caught:
            board.call('storage_apply', {'planId': planned['planId'], 'commandId': 'outside-tomb', 'confirm': True})
        self.assertEqual(caught.exception.code, 'STORAGE_UNSAFE')
        self.assertEqual(sentinel.read_text(), 'keep outside')

    def test_storage_reparse_records_and_parent_are_refused_before_open(self):
        board = self.board()
        _, planned, _ = self._runtime_plan(board)
        state = board.directory.resolve()
        root = state / 'storage'
        receipt = root / ('receipt-' + hashlib.sha256(b'reparse-journal').hexdigest() + '.json')
        receipt.write_text(json.dumps({'planId': planned['planId'], 'complete': True}))
        plan_path = root / (planned['planId'] + '.json')
        original = Path.lstat
        for marked in (root, root / '.lock', receipt, plan_path):
            before = marked.read_bytes() if marked != root else None
            with self.subTest(marked=marked):
                def reparse(path, *args, **kwargs):
                    value = original(path, *args, **kwargs)
                    if private_dirs._absolute(path) == marked:
                        return SimpleNamespace(st_mode=value.st_mode, st_file_attributes=0x400,
                                               st_ino=value.st_ino, st_size=value.st_size,
                                               st_mtime_ns=value.st_mtime_ns, st_dev=value.st_dev)
                    return value
                with mock.patch.object(Path, 'lstat', reparse):
                    with self.assertRaises(BoardError) as caught:
                        board.call('storage_apply', {'planId': planned['planId'], 'commandId': 'reparse-journal', 'confirm': True})
                    self.assertEqual(caught.exception.code, 'STORAGE_UNSAFE')
                    with self.assertRaises(BoardError) as caught:
                        if marked == plan_path:
                            storage._read_storage_json(plan_path)
                        elif marked == root / '.lock':
                            with storage.locked(board.store):
                                self.fail('Reparse lock was opened')
                        else:
                            storage._save_receipt(receipt, {'planId': planned['planId']})
                    self.assertEqual(caught.exception.code, 'STORAGE_UNSAFE')
            if marked != root:
                self.assertEqual(marked.read_bytes(), before)

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
        value = json.loads(plan_path.read_text())
        value['expiresAt'] = '2000-01-01T00:00:00+00:00'
        plan_path.write_text(json.dumps(value))
        with mock.patch('hey_my_buddy.blackboard.tasks.storage.private_dirs.remove_tree', wraps=real_remove):
            result = board.call('storage_apply', params)
        self.assertTrue(result['complete'])
        self.assertEqual(len(result['removed']), 1)
        self.assertFalse(tomb.exists())

    def test_existing_pending_receipt_without_inode_replays_its_fixed_tomb(self):
        board = self.board()
        candidate, planned, row = self._runtime_plan(board)
        tomb = candidate.with_name('.reclaim-' + planned['planId'] + '-' + candidate.name)
        candidate.rename(tomb)
        params = {'planId': planned['planId'], 'commandId': 'legacy-pending', 'confirm': True}
        receipt = board.directory / 'storage' / ('receipt-' + hashlib.sha256(b'legacy-pending').hexdigest() + '.json')
        receipt.write_text(json.dumps({'planId': planned['planId'], 'removedBytes': 0, 'removed': [],
                                       'skipped': [], 'complete': False,
                                       'pending': {'id': row['id'], 'path': row['path'], 'tomb': str(tomb),
                                                   'bytes': row['bytes'], 'fingerprint': row['fingerprint']}}))
        plan_path = board.directory / 'storage' / (planned['planId'] + '.json')
        value = json.loads(plan_path.read_text())
        value['expiresAt'] = '2000-01-01T00:00:00+00:00'
        plan_path.write_text(json.dumps(value))
        result = board.call('storage_apply', params)
        self.assertTrue(result['complete'])
        self.assertEqual(len(result['removed']), 1)
        self.assertFalse(tomb.exists())

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
        sentinel.write_text('retain replacement')
        with self.assertRaises(BoardError) as caught:
            board.call('storage_apply', params)
        self.assertEqual(caught.exception.code, 'STORAGE_CHANGED')
        self.assertEqual(sentinel.read_text(), 'retain replacement')

    def test_linked_runtime_and_ready_path_are_protected_before_ready_read(self):
        board = self.board()
        root = Path(os.environ['HMB_RUNTIME_ROOT'])
        root.mkdir()
        outside = self.directory / 'outside-ready'
        outside.mkdir()
        (outside / 'READY.json').write_text('outside')
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
        self.assertTrue(all('linked-path' in row['reasons'] and not row['eligible'] for row in rows))

    def test_reparse_runtime_and_ready_are_protected_without_reading_ready(self):
        board = self.board()
        root = Path(os.environ['HMB_RUNTIME_ROOT']).resolve()
        root.mkdir()
        candidate = root / 'reparse-runtime'
        candidate.mkdir()
        ready_path = candidate / 'READY.json'
        ready_path.write_text('retained READY')
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
                self.assertIn('linked-path', row['reasons'])
        self.assertEqual(ready_path.read_text(), 'retained READY')

    def test_unaccepted_unknown_shutdown_reports_both_protections(self):
        workflow = mock.Mock()
        workflow.shutdown_summary.return_value = {'selfConfirmed': None, 'descendantsConfirmed': None}
        store = mock.Mock(workflow=workflow)
        reasons = storage._zcode_reasons(store, object(), {'run_id': 'run', 'state': 'running'},
                                         {'accepted_at': None}, 0)
        self.assertEqual(reasons, ['continuation-supported', 'shutdown-unconfirmed'])


class ProcessInventoryServiceTests(BoardTestCase):
    """The real inventory recognizes current package service modules.

    Only the ``ps`` data source is replaced: the simulated listing is literal
    text spelled independently of the shared entry table, so a misspelling in
    that table cannot fake its own inputs. Every service line carries a
    ``--state-dir`` or ``--startup`` outside the observed board and the board has no
    ``control.json``, so open-file inspection is never reached.
    """

    def test_real_inventory_recognizes_current_service_modules(self):
        state = self.directory / 'state'
        state.mkdir()
        foreign = self.directory / 'another-board'
        foreign.mkdir()
        runtime = self.directory / 'runtime-root' / ('a' * 32)
        listing = '\n'.join([
            f' 101 /usr/bin/python3 -P -m buddy.daemon --state-dir {foreign}',
            f' 102 /usr/bin/python3 -m buddy.worker.supervisor --worker-id old-0 --state-dir {foreign}',
            f' 103 {runtime}/venv/bin/python -P -m hey_my_buddy.blackboard.service.daemon --state-dir {foreign}',
            f' 104 {runtime}/venv/bin/python -m hey_my_buddy.buddy.runtime.execution --startup {foreign}/harnesses/dsh/micro-tasks/t/attempts/a/execution-start.json',
            f' 201 /usr/bin/python3 -m buddy.daemonization --state-dir {foreign}',
            f' 202 /usr/bin/python3 -m xbuddy.daemon --state-dir {foreign}',
            f' 203 /usr/bin/python3 -m hey_my_buddy.buddy.runtime.supervisord --state-dir {foreign}',
            f' 204 /usr/bin/python3 -m hey_my_buddy.blackboard.service.daemonware --state-dir {foreign}',
            f' 205 /usr/bin/python3 -m hey_my_buddy.buddy.runtime.executionish --startup {foreign}/harnesses/dsh/attempts/a/execution-start.json',
            f' 206 /usr/bin/python3 -m buddy.client --state-dir {foreign}',
        ])
        with mock.patch('hey_my_buddy.blackboard.tasks.storage.subprocess.check_output', return_value=listing) as ps, \
                mock.patch('hey_my_buddy.blackboard.tasks.storage.subprocess.run',
                           side_effect=AssertionError('open-file inspection was reached')):
            orphans, commands, known = storage.process_inventory(state)
        ps.assert_called_once()
        self.assertTrue(known)
        self.assertEqual({row['pid']: row for row in orphans}, {
            103: {'pid': 103, 'kind': 'daemon', 'stateDir': str(foreign), 'runtimeDir': str(runtime)},
            104: {'pid': 104, 'kind': 'execution', 'stateDir': str(foreign), 'runtimeDir': str(runtime)},
        })
        # Every simulated process stays observable, including the near misses.
        self.assertEqual(len(commands), 10)
        self.assertIn(f'/usr/bin/python3 -m buddy.daemonization --state-dir {foreign}', commands)

    def test_execution_startup_path_with_spaces_identifies_its_board(self):
        state = self.directory / 'observed board'
        state.mkdir()
        foreign = self.directory / 'another execution-start.json board'
        foreign.mkdir()
        listing = (f' 104 /usr/bin/python3 -m hey_my_buddy.buddy.runtime.execution '
                   f'--startup {foreign}/harnesses/dsh/micro-tasks/t/attempts/a/execution-start.json')
        with mock.patch('hey_my_buddy.blackboard.tasks.storage.subprocess.check_output', return_value=listing), \
                mock.patch('hey_my_buddy.blackboard.tasks.storage.subprocess.run',
                           side_effect=AssertionError('open-file inspection was reached')):
            orphans, _, known = storage.process_inventory(state)
        self.assertTrue(known)
        self.assertEqual(orphans[0]['stateDir'], str(foreign))
