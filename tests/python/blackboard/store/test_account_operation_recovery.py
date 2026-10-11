"""Private startup recovery; no native process, credential or stop inference."""
import json
from unittest.mock import patch

from support import BoardTestCase
from hey_my_buddy.blackboard.catalog import accounts
from hey_my_buddy.blackboard.store.db import Database, canonical_json
from hey_my_buddy.blackboard.store.store import BoardStore


class AccountOperationRecoveryTests(BoardTestCase):
    def setUp(self):
        super().setUp()
        self.store = BoardStore(self.directory)
        self.store.db.initialize()

    def insert(self, key, raw):
        with self.store.db.write() as connection:
            connection.execute('INSERT INTO meta(key,value) VALUES(?,?)', (key, raw))

    def retained(self):
        with self.store.db.read() as connection:
            return dict(connection.execute("SELECT key,value FROM meta WHERE key LIKE 'account-operation:%'"
                                           " OR key LIKE 'account-mutation:%'"))

    def test_r8_startup_changes_only_active_operation_status(self):
        key = 'account-operation:previous-service'
        operation = {'adapter': 'codex', 'source': 'native', 'credentialRevision': 7,
                     'purpose': 'fixture-query', 'status': 'active',
                     'ownerEvidence': {'shutdownConfirmed': False, 'nested': [None, True, 1.25]},
                     'uninterpreted': '保留原值'}
        self.insert(key, canonical_json(operation))
        with patch.object(accounts.Accounts, 'native_operation_stopped',
                          side_effect=AssertionError('Recovery cannot fabricate native stop')):
            self.store.initialize()
        self.assertEqual(set(self.retained()), {key})
        self.assertEqual(json.loads(self.retained()[key]), {**operation, 'status': 'uncertain'})

    def test_r8_startup_preserves_original_uncertain_and_all_mutations_raw(self):
        rows = {
            'account-operation:old-uncertain': '{ "adapter":"codex", "source":"native", "credentialRevision":7, "status" : "uncertain", "purpose":"历史", "other":[1,null] }',
            'account-mutation:["codex","native"]': '{ "operationId" : "active-owner", "status" : "active", "credentialRevision":7 }',
            'account-mutation:["claude","worker"]': '{ "operationId" : "uncertain-owner", "status" : "uncertain", "credentialRevision":4 }',
        }
        for key, raw in rows.items():
            self.insert(key, raw)
        self.store.initialize()
        self.assertEqual(self.retained(), rows)

    def test_r8_current_service_operation_survives_stable_reads_and_lightweight_opens(self):
        self.store.initialize()
        with self.store.db.read() as connection:
            account = accounts.selection(connection, 'codex')
        with accounts.native_operation(self.store.db, account, 'current-service') as stop:
            original = self.retained()
            self.assertEqual(json.loads(original[stop['operationId']])['status'], 'active')
            for _ in range(2):
                Database(self.directory).initialize()
                second = BoardStore(self.directory)
                with second.db.read() as connection:
                    self.assertEqual(accounts.selection(connection, 'codex'), account)
                self.assertEqual(self.retained(), original)
        self.assertEqual(json.loads(self.retained()[stop['operationId']])['status'], 'uncertain')

    def test_r8_account_recovery_rolls_back_with_existing_startup_transaction(self):
        self.insert('account-operation:previous-service', '{ "status":"active", "other":42 }')
        original = self.retained()
        with (patch.object(self.store, '_head_of', side_effect=RuntimeError('startup transaction failed')),
              patch.object(self.store, '_notify') as notify):
            with self.assertRaisesRegex(RuntimeError, 'startup transaction failed'):
                self.store.reconcile_startup()
        self.assertEqual(self.retained(), original)
        notify.assert_not_called()

    def test_r8_account_only_recovery_notifies_after_commit(self):
        self.insert('account-operation:previous-service', '{"status":"active","other":42}')
        notifications = []

        def notified(head):
            self.assertEqual(json.loads(self.retained()['account-operation:previous-service'])['status'], 'uncertain')
            notifications.append(head)

        with patch.object(self.store, '_notify', side_effect=notified):
            self.store.initialize()
        self.assertEqual(notifications, [0])
