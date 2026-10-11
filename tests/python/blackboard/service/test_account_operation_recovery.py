"""Real private Daemon startup/restart with synthetic native reservations."""
from contextlib import ExitStack
import json
import os
from pathlib import Path

from support import BoardTestCase
from hey_my_buddy.blackboard.catalog import accounts
from hey_my_buddy.blackboard.store.db import Database, canonical_json
from hey_my_buddy.blackboard.store.store import BoardStore
from hey_my_buddy.protocol.transport import _read_endpoint, _request


class AccountOperationServiceRecoveryTests(BoardTestCase):
    def retained(self, database):
        with database.read() as connection:
            return dict(connection.execute("SELECT key,value FROM meta WHERE key LIKE 'account-operation:%'"
                                           " OR key LIKE 'account-mutation:%'"))

    def call(self, operation):
        return _request(_read_endpoint(self.directory), operation, {}, state_dir=self.directory)

    def test_r8_real_daemon_restart_recovers_previous_active_and_keeps_current_active(self):
        # The existing Harness substitutes only native discovery. It still runs
        # Daemon.run, startup ownership locks, store.initialize and real RPC.
        self.catalog_fixture()
        store = BoardStore(self.directory)
        store.db.initialize()
        original = {
            'account-operation:history': '{ "adapter":"codex", "source":"native", "credentialRevision":0, "status":"uncertain", "purpose":"历史读取", "other":[1,null] }',
            'account-mutation:["codex","native"]': '{ "status":"active", "operationId":"mutation-active", "credentialRevision":0 }',
            'account-mutation:["claude","worker"]': '{ "status":"uncertain", "operationId":"mutation-uncertain", "credentialRevision":3 }',
        }
        with store.db.write() as connection:
            connection.executemany('INSERT INTO meta(key,value) VALUES(?,?)', original.items())
            # A separate synthetic account avoids the retained credential mutation.
            account = accounts.selection(connection, 'dsh')
        try:
            with ExitStack() as reservations:
                with self.daemon(env={'BUDDY_MAX_CONCURRENT': '1'}):
                    self.assertEqual(self.retained(store.db), original)
                    previous = reservations.enter_context(
                        accounts.native_operation(store.db, account, 'previous-service'))
                    raw = self.retained(store.db)[previous['operationId']]
                    value = {**json.loads(raw), 'retainedEvidence': {'shutdownConfirmed': False, 'other': [1, None]}}
                    with store.db.write() as connection:
                        connection.execute('UPDATE meta SET value=? WHERE key=?',
                                           (canonical_json(value), previous['operationId']))
                    baseline = self.retained(store.db)
                    self.assertEqual(json.loads(baseline[previous['operationId']])['status'], 'active')
                    for _ in range(2):
                        self.call('health')
                        self.call('accounts')
                        self.call('model_profiles')
                        Database(self.directory).initialize()
                        self.assertEqual(self.retained(store.db), baseline)
                # The reservation remains open across actual Daemon shutdown and
                # restart. No native child was spawned and stop stays unconfirmed.
                with self.daemon(env={'BUDDY_MAX_CONCURRENT': '1'}):
                    recovered = self.retained(store.db)
                    self.assertEqual(set(recovered), set(baseline))
                    for key, old in baseline.items():
                        if key == previous['operationId']:
                            self.assertEqual(json.loads(recovered[key]), {**value, 'status': 'uncertain'})
                        else:
                            self.assertEqual(recovered[key], old, key)
                    with accounts.native_operation(store.db, account, 'current-service') as current:
                        active = self.retained(store.db)
                        self.assertEqual(json.loads(active[current['operationId']])['status'], 'active')
                        self.call('health')
                        self.call('accounts')
                        Database(self.directory).initialize()
                        self.assertEqual(self.retained(store.db), active)
        finally:
            log = self.directory / 'test-daemon.log'
            if log.exists():
                retained = Path(os.environ['TMPDIR']) / 'r8-account-recovery-service.log'
                retained.write_bytes(log.read_bytes())
