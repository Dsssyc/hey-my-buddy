"""Private protected inventory and exact-plan guards."""
import json
import os
from pathlib import Path
from unittest import mock
from buddy.errors import BoardError
from buddy import storage
from support import BoardTestCase

class StorageTests(BoardTestCase):
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
