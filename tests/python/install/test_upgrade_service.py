"""A real private service reads the current fixture board and its history."""
import json
import sys
from unittest import mock

from support import BoardTestCase
from blackboard.store.upgrade_fixture import load_fixture
from hey_my_buddy.blackboard.store import backup
from hey_my_buddy.blackboard.store.upgrade import upgrade_database
from hey_my_buddy.blackboard.store.db import Database, SCHEMA_VERSION
from hey_my_buddy.blackboard.store.store import BoardStore
from hey_my_buddy.blackboard.routing import router
from hey_my_buddy.buddy.harnesses.codex.adapter import CodexAdapter
from hey_my_buddy.protocol.transport import _read_endpoint, _request
from hey_my_buddy.install import upgrade


class UpgradeServiceTests(BoardTestCase):
    def test_cleanup_refusal_keeps_private_commands_usable_and_retries_at_startup(self):
        protected = self.directory / 'retired-state'
        protected.mkdir()
        (protected / 'keep.txt').write_text('retained')
        workers = self.directory / 'workers'
        workers.symlink_to(protected, target_is_directory=True)
        upgrade.write_journal(self.directory / 'upgrade-last.json', {'phase': 'verified',
            'legacyWorkerCleanup': True, 'verification': {'schemaVersion': SCHEMA_VERSION}})
        store = BoardStore(self.directory)
        with self.daemon():
            code, health = self.cli('health')
            self.assertEqual(code, 0, health)
            self.assertEqual(health['status'], 'ok')
            code, submitted = self.cli('execution-submit', json.dumps({
                'requestId': 'legacy-cleanup-command', 'task': 'Private command during cleanup',
                'cwd': str(self.workdir()), 'adapter': 'command',
                'argv': [sys.executable, '-c', 'pass'], 'expectedSeconds': 60,
            }))
            self.assertEqual(code, 0, submitted)
            code, status = self.cli('status', json.dumps({'runId': submitted['runId']}))
            self.assertEqual(code, 0, status)
            with store.db.read() as connection:
                issue = dict(connection.execute(
                    "SELECT * FROM service_issues WHERE stage='legacy-worker-cleanup'").fetchone())
            self.assertEqual(issue['code'], 'LEGACY_WORKER_CLEANUP_FAILED')
            self.assertIn('Fix access to workers/', issue['message'])
            self.assertFalse((self.directory / 'upgrade.json').exists())
        workers.unlink()
        with self.daemon():
            with store.db.read() as connection:
                self.assertEqual(connection.execute(
                    "SELECT COUNT(*) FROM service_issues WHERE stage='legacy-worker-cleanup'").fetchone()[0], 0)
            self.assertFalse(json.loads((self.directory / 'upgrade-last.json').read_text())['legacyWorkerCleanup'])
            self.assertEqual((protected / 'keep.txt').read_text(), 'retained')
        store.db.close()

    def test_private_service_reads_settings_and_preserved_work(self):
        state = self.directory
        fixture = load_fixture(state)
        upgrade_database(state)
        store = BoardStore(state)
        backup.create(store)
        store.db.close()
        Database(state).initialize()
        with mock.patch.object(CodexAdapter, "local_read_only_check", return_value={
            "eligible": True, "reasonCode": None, "reason": None,
            "systemSandbox": True, "sameAttemptContinuation": True,
        }), Database(state).read() as connection:
            resolution = router.current_router(connection, now="2026-01-01T00:00:00.000Z")
        self.assertIsNone(resolution.buddy_id)
        self.assertEqual(resolution.problem["code"], "router-unavailable")
        self.assertEqual([item["code"] for item in resolution.inspections],
                         ["router-quota-exhausted", "router-unavailable"])
        self.assertEqual(resolution.facts["routingMode"], "review")
        self.assertEqual(resolution.facts["routingBudget"], "deep")
        self.assertEqual(resolution.facts["routerRetryIntervalSeconds"], 47)

        def call(operation, params):
            return _request(_read_endpoint(state), operation, params, state_dir=state)

        with self.daemon(env={"HMB_MAX_CONCURRENT": "1"}):
            self.assertEqual(call("health", {})["schemaVersion"], SCHEMA_VERSION)
            snapshot = call("console_snapshot", {})
            self.assertEqual(snapshot["configuration"]["routerBuddyIds"], fixture["routerBuddyIds"])
            tasks = call("task_list", {})["tasks"]
            self.assertIn(fixture["stableIds"]["delivered"], {task["runId"] for task in tasks})
            view = call("workflow_get", {"runId": fixture["stableIds"]["delivered"]})
            self.assertEqual(view["runId"], fixture["stableIds"]["delivered"])
            self.assertEqual(view["state"], "concluded")
