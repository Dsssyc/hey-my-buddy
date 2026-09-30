"""Private, native-free quota lineage and transactional retry verification."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import json
import os
from pathlib import Path
import sqlite3
import tempfile
from threading import Barrier
import unittest
from unittest.mock import patch

from buddy.db import canonical_json
from buddy.native_observations import latest_quota, persist, record_quota, warnings
from buddy.quota_routing import claim, exhausted, record, redetect, routing_facts

NATIVE = {'source': 'native', 'credentialRevision': 0}
WORKER = {'source': 'worker', 'credentialRevision': 0}
CONFIG = {'adapter': 'codex', 'provider': 'openai', 'model': 'fixture-model'}
T0 = '2026-01-01T00:00:00Z'
T1 = '2026-01-01T01:00:00Z'
T2 = '2026-01-01T02:00:00Z'


class AccountQuotaTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='buddy-account-quota-')
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.runtime = self.directory / 'runtime'
        self.runtime.mkdir(mode=0o700)
        sentinel = self.directory / 'bin'
        sentinel.mkdir(mode=0o700)
        for name in ('codex', 'claude', 'zcode', 'dsh'):
            path = sentinel / name
            path.write_text('#!/bin/sh\nexit 97\n')
            path.chmod(0o700)
        inherited = {'BUDDY_STATE_DIR', 'BUDDY_RUNTIME_ROOT', 'BUDDY_RUNTIME', 'BUDDY_RUNTIME_IDENTITY',
                     'BUDDY_WORKER_STATE', 'BUDDY_WORKER_ID', 'BUDDY_AGENT_CREDENTIAL',
                     'BUDDY_AGENT_CREDENTIAL_FILE', 'VIRTUAL_ENV', 'UV_PROJECT_ENVIRONMENT'}
        environment = {key: value for key, value in os.environ.items() if key not in inherited}
        environment.update(BUDDY_STATE_DIR=str(self.directory), BUDDY_RUNTIME_ROOT=str(self.runtime),
                           PATH=str(sentinel) + os.pathsep + environment.get('PATH', ''))
        self.env = patch.dict(os.environ, environment, clear=True)
        self.env.start()
        self.addCleanup(self.env.stop)
        self.path = self.directory / 'board.sqlite3'
        with self.connection() as db:
            db.executescript('CREATE TABLE meta(key TEXT PRIMARY KEY,value TEXT);'
                             'CREATE TABLE harness_health(adapter TEXT PRIMARY KEY,quota_json TEXT);'
                             'CREATE TABLE attempts(attempt_id TEXT PRIMARY KEY,token_usage_json TEXT);'
                             "INSERT INTO harness_health(adapter) VALUES('codex');"
                             "INSERT INTO attempts(attempt_id) VALUES('old-attempt');")
        self.current = NATIVE
        self.selector = patch('buddy.accounts.selection', side_effect=lambda db, adapter: self.current)
        self.selector.start()
        self.addCleanup(self.selector.stop)

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.path, isolation_level=None, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            yield db
        finally:
            db.close()

    def observation(self, stamp=T0, **changes):
        return {'provider': 'openai', 'source': 'fixture/native-quota', 'observedAt': stamp,
                'reachedType': 'insufficient_quota', 'windows': [], **changes}

    def test_old_receipt_uses_frozen_service_identity_and_never_current_display(self):
        self.current = WORKER
        with self.connection() as db:
            record_quota(db, 'codex', self.observation(T1, reachedType=None, ordinaryUsageAllowed=True),
                         account=WORKER, now=T2)
            attempt = {'attempt_id': 'old-attempt', 'adapter': 'codex', 'model_adapter': 'codex',
                       'model_provider': 'openai'}
            # Worker tries to claim that the receipt belongs to the new account.
            result = {'account': WORKER, 'quota': self.observation(T2, account=WORKER)}
            with patch('buddy.accounts.attempt_account', return_value=NATIVE):
                persist(db, attempt, result)
            current = latest_quota(db, 'codex')
            old = latest_quota(db, 'codex', account=NATIVE)
            self.assertEqual(current['observedAt'], T1)
            self.assertEqual(current['account'], WORKER)
            self.assertEqual(old['account'], NATIVE)
            self.assertEqual(old['attemptId'], 'old-attempt')
            self.assertIsNone(exhausted(db, CONFIG, now=T2))
            self.assertIsNotNone(exhausted(db, CONFIG, account=NATIVE, now=T2))
            self.assertEqual(json.loads(db.execute('SELECT quota_json FROM harness_health').fetchone()[0]), current)
        self.assertEqual(list(self.runtime.iterdir()), [])

    def test_same_source_credential_change_separates_warning_and_routing(self):
        with self.connection() as db:
            record_quota(db, 'codex', self.observation(), account=NATIVE, now=T1)
            self.assertTrue(warnings(db, CONFIG, now=T1))
            self.current = {'source': 'native', 'credentialRevision': 1}
            self.assertIsNone(latest_quota(db, 'codex'))
            self.assertEqual(warnings(db, CONFIG, now=T1), [])
            self.assertIsNone(exhausted(db, CONFIG, now=T1))
            self.assertEqual(routing_facts(db, 'codex', now=T1), [])
            record_quota(db, 'codex', self.observation(T1, reachedType=None, ordinaryUsageAllowed=True), now=T2)
            self.assertIsNotNone(exhausted(db, CONFIG, account=NATIVE, now=T2))
            self.assertIsNone(exhausted(db, CONFIG, now=T2))

    def test_near_limit_and_rate_limit_warnings_are_credential_scoped(self):
        with self.connection() as db:
            record_quota(db, 'codex', self.observation(reachedType=None, windows=[{'usedPercent': 95}]),
                         account=NATIVE, now=T0)
            self.assertEqual(warnings(db, CONFIG, now=T0)[0]['code'], 'HARNESS_QUOTA_NEAR_LIMIT')
            self.current = WORKER
            self.assertEqual(warnings(db, CONFIG, now=T0), [])
            record_quota(db, 'codex', self.observation(T1, reachedType='rate_limit_exceeded'),
                         account=WORKER, now=T1)
            self.assertEqual(warnings(db, CONFIG, now=T1)[0]['code'], 'HARNESS_RATE_LIMIT_REPORTED')
            self.current = {'source': 'worker', 'credentialRevision': 1}
            self.assertEqual(warnings(db, CONFIG, now=T1), [])

    def test_legacy_untagged_display_and_meta_belong_only_initial_native(self):
        with self.connection() as db:
            db.execute('UPDATE harness_health SET quota_json=?', (canonical_json(self.observation()),))
            legacy = {'adapter': 'codex', 'provider': 'openai', 'limitId': None, 'observedAt': T0,
                      'blocked': True, 'available': False, 'resetsAt': None, 'source': 'fixture',
                      'code': 'HARNESS_QUOTA_EXHAUSTED'}
            db.execute('INSERT INTO meta(key,value) VALUES(?,?)',
                       ('quota-routing:' + canonical_json(['codex', 'openai', None]), canonical_json(legacy)))
            self.assertIsNotNone(exhausted(db, CONFIG, now=T1))
            for account in (WORKER, {'source': 'native', 'credentialRevision': 1}):
                self.current = account
                self.assertIsNone(latest_quota(db, 'codex'))
                self.assertIsNone(exhausted(db, CONFIG, now=T1))
                self.assertEqual(redetect(db, 'codex', 'openai', now=T1)['records'], [])
            self.assertIsNotNone(exhausted(db, CONFIG, account=NATIVE, now=T1))

    def test_redetect_and_retry_consumption_do_not_cross_accounts(self):
        with self.connection() as db:
            for account in (NATIVE, WORKER):
                record(db, 'codex', self.observation(), account=account, now=T0)
            native = redetect(db, 'codex', 'openai', account=NATIVE, now=T0)
            self.assertEqual(native['opened'], [None])
            self.assertTrue(claim(db, CONFIG, decision_id='native-first', account=NATIVE, now=T0)[0]['claimed'])
            self.assertIsNone(claim(db, CONFIG, decision_id='worker-early', account=WORKER, now=T0))
            worker = redetect(db, 'codex', 'openai', account=WORKER, now=T0)
            self.assertEqual(worker['opened'], [None])
            self.assertTrue(claim(db, CONFIG, decision_id='worker-first', account=WORKER, now=T0)[0]['claimed'])
            self.assertIsNone(claim(db, CONFIG, decision_id='native-next', account=NATIVE, now=T0))
            self.assertIsNone(claim(db, CONFIG, decision_id='worker-next', account=WORKER, now=T0))

    def test_concurrent_retry_claims_one_account_once_in_write_transaction(self):
        with self.connection() as db:
            record(db, 'codex', self.observation(), account=WORKER, now=T0)
        barrier = Barrier(2)
        def compete(index):
            with self.connection() as db:
                barrier.wait(timeout=5)
                db.execute('BEGIN IMMEDIATE')
                result = claim(db, CONFIG, decision_id='race-' + str(index), account=WORKER, now=T1)
                db.commit()
                return result is not None
        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertEqual(sorted(pool.map(compete, (1, 2))), [False, True])
        with self.connection() as db:
            self.assertEqual(routing_facts(db, 'codex', account=NATIVE, now=T1), [])
            self.assertEqual(routing_facts(db, 'codex', account=WORKER, now=T1)[0]['retry']['account'], WORKER)


if __name__ == '__main__':
    unittest.main()
