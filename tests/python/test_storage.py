"""Private protected inventory and exact-plan guards."""
import hashlib
import json
import os
from pathlib import Path
import stat
from types import SimpleNamespace
from unittest import mock
from buddy.errors import BoardError
from buddy import private_dirs, storage
from support import BoardTestCase

class StorageTests(BoardTestCase):
    def _runtime_plan(self, board):
        root = Path(os.environ['BUDDY_RUNTIME_ROOT'])
        root.mkdir()
        candidate = root / 'expired-runtime'
        candidate.mkdir()
        (candidate / 'READY.json').write_text('{}')
        (candidate / 'payload').write_text('runtime payload')
        (board.directory / 'runtime-retention.json').write_text(json.dumps({'current': 'current', 'previous': 'previous'}))
        patches = (mock.patch('buddy.storage.process_inventory', return_value=([], [], True)),
                   mock.patch('buddy.storage.runtime_usage', return_value=[]),
                   mock.patch('buddy.storage.is_ready', return_value=True))
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
        with mock.patch.dict(os.environ, {'BUDDY_RUNTIME_ROOT': str(shared)}), \
             mock.patch('buddy.storage.DEFAULT_RUNTIME_ROOT', shared), \
             mock.patch('buddy.home.default_state_dir', return_value=self.directory / 'daily-state'), \
             mock.patch('buddy.storage.process_inventory', return_value=([], [], True)), \
             mock.patch('buddy.storage.runtime_usage', return_value=[]), \
             mock.patch('buddy.storage.is_ready', return_value=True):
            result = storage.prune_old_runtimes(board.store)
        self.assertTrue(previous.is_dir())
        self.assertEqual(result['removedBytes'], 0)

    def setUp(self):
        super().setUp()
        self.env = mock.patch.dict(os.environ, {'BUDDY_RUNTIME_ROOT':str((self.directory / 'runtime').resolve())})
        self.env.start()
        self.addCleanup(self.env.stop)

    def test_unknown_native_owner_and_durable_state_are_never_reclaimed(self):
        board = self.board()
        native = board.directory / 'harnesses/zcode/unknown'
        native.mkdir(parents=True)
        (native / 'sessions.sqlite').write_bytes(b'protected')
        with mock.patch('buddy.storage.process_inventory', return_value=([], [], True)):
            planned = board.call('storage_plan', {})
            native_row = next(r for r in planned['candidates'] if r['category'] == 'zcode')
            self.assertFalse(native_row['eligible'])
            self.assertIn('owner-unproven', native_row['reasons'])
            params = {'planId':planned['planId'], 'commandId':'apply-once', 'confirm':True}
            result = board.call('storage_apply', params)
            self.assertEqual(result['removedBytes'], 0)
            self.assertTrue((native / 'sessions.sqlite').exists())
            self.assertEqual(board.call('storage_apply', params), result)

    def test_confirmation_expiry_and_command_binding(self):
        board = self.board()
        with mock.patch('buddy.storage.process_inventory', return_value=([], [], True)):
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
        root = Path(os.environ['BUDDY_RUNTIME_ROOT']);root.mkdir()
        for name in ('current','previous','older'):
            target=root/name;target.mkdir();(target/'READY.json').write_text('{}')
        (board.directory/'runtime-retention.json').write_text(json.dumps({'current':'current','previous':'previous'}))
        with mock.patch('buddy.storage.process_inventory', return_value=([], [], True)), mock.patch('buddy.storage.runtime_usage', return_value=['runtime-in-use']):
            planned=board.call('storage_plan', {})
        rows=[r for r in planned['candidates'] if r['category']=='runtimes']
        self.assertEqual(len(rows),3)
        self.assertTrue(all(not r['eligible'] for r in rows))
        self.assertIn('runtime-in-use', next(r for r in rows if r['path'].endswith('/older'))['reasons'])

    def test_linked_native_root_does_not_read_or_reclaim_external_home(self):
        board=self.board()
        outside=self.directory/'outside-native';outside.mkdir();sentinel=outside/'sessions.sqlite';sentinel.write_bytes(b'outside')
        (board.directory/'harnesses').mkdir()
        (board.directory/'harnesses/zcode').symlink_to(outside, target_is_directory=True)
        with mock.patch('buddy.storage.process_inventory', return_value=([],[],True)):
            planned=board.call('storage_plan',{})
            rows=[r for r in planned['candidates'] if r['category']=='zcode']
            self.assertEqual(len(rows),1)
            self.assertFalse(rows[0]['eligible'])
            self.assertEqual(rows[0]['bytes'],0)
            board.call('storage_apply',{'planId':planned['planId'],'commandId':'linked-no-delete','confirm':True})
        self.assertEqual(sentinel.read_bytes(),b'outside')

    def test_all_adapters_private_goals_are_visible_and_accounts_stay_protected(self):
        board = self.board()
        state = board.directory.resolve()
        for adapter in ('codex', 'claude', 'dsh', 'zcode'):
            root = private_dirs.ensure_private_dir(private_dirs.native_root(state, adapter, 'unknown-goal'))
            (root / 'session.json').write_text('retained')
        account = private_dirs.ensure_private_dir(private_dirs.account_root(state, 'codex'))
        (account / 'auth.json').write_text('user account')
        with mock.patch('buddy.storage.process_inventory', return_value=([], [], True)):
            planned = board.call('storage_plan', {})
            goals = [row for row in planned['candidates'] if row['category'] == 'harnesses']
            self.assertEqual({row['adapter'] for row in goals}, {'codex', 'claude', 'dsh', 'zcode'})
            self.assertTrue(all(not row['eligible'] and 'owner-unproven' in row['reasons'] for row in goals))
            account_row = next(row for row in planned['candidates'] if row['path'] == str(account.parent))
            self.assertIn('user-account-protected', account_row['reasons'])
            board.call('storage_apply', {'planId':planned['planId'], 'commandId':'protected-goals', 'confirm':True})
        self.assertEqual((account / 'auth.json').read_text(), 'user account')

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
        with mock.patch('buddy.storage.process_inventory', return_value=([], [], True)):
            planned = board.call('storage_plan', {})
        params = {'planId': planned['planId'], 'commandId': 'linked-receipt', 'confirm': True}
        receipt = storage_root / ('receipt-' + hashlib.sha256(b'linked-receipt').hexdigest() + '.json')
        receipt.symlink_to(outside)
        with mock.patch('buddy.storage.private_dirs.open_regular_fd', wraps=private_dirs.open_regular_fd) as opened:
            with self.assertRaises(BoardError) as caught:
                board.call('storage_apply', params)
        self.assertFalse(any(call.args[0] == receipt for call in opened.call_args_list))
        self.assertEqual(caught.exception.code, 'STORAGE_UNSAFE')
        receipt.unlink()
        plan_path = storage_root / (planned['planId'] + '.json')
        plan_path.unlink()
        plan_path.symlink_to(outside)
        with mock.patch('buddy.storage.private_dirs.open_regular_fd', wraps=private_dirs.open_regular_fd) as opened:
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
                with mock.patch('buddy.storage.json.load', side_effect=AssertionError('External journal was read')):
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
        with mock.patch('buddy.storage.private_dirs.remove_tree', side_effect=OSError('interrupted')):
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
        with mock.patch('buddy.storage.private_dirs.remove_tree', wraps=real_remove):
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
        with mock.patch('buddy.storage.private_dirs.remove_tree', side_effect=OSError('interrupted')):
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
        root = Path(os.environ['BUDDY_RUNTIME_ROOT'])
        root.mkdir()
        outside = self.directory / 'outside-ready'
        outside.mkdir()
        (outside / 'READY.json').write_text('outside')
        (root / 'linked-runtime').symlink_to(outside, target_is_directory=True)
        local = root / 'local-runtime'
        local.mkdir()
        (local / 'READY.json').symlink_to(outside / 'READY.json')
        with mock.patch('buddy.storage.process_inventory', return_value=([], [], True)), \
             mock.patch('buddy.storage.is_ready', side_effect=AssertionError('external READY was read')) as ready:
            planned = board.call('storage_plan', {})
        ready.assert_not_called()
        rows = [row for row in planned['candidates'] if row['category'] == 'runtimes']
        self.assertEqual({Path(row['path']).name for row in rows}, {'linked-runtime', 'local-runtime'})
        self.assertTrue(all('linked-path' in row['reasons'] and not row['eligible'] for row in rows))

    def test_reparse_runtime_and_ready_are_protected_without_reading_ready(self):
        board = self.board()
        root = Path(os.environ['BUDDY_RUNTIME_ROOT']).resolve()
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
                     mock.patch('buddy.storage.process_inventory', return_value=([], [], True)), \
                     mock.patch('buddy.storage.is_ready', side_effect=AssertionError('Reparse READY was read')) as ready:
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
                                         {'acceptance_verdict': None}, 0)
        self.assertEqual(reasons, ['continuation-supported', 'shutdown-unconfirmed'])
