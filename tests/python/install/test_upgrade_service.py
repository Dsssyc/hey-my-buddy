"""A real private service reads the current fixture board and its history."""
from unittest import mock

from hey_my_buddy.protocol.contracts import CONTRACT_VERSION
from support import BoardTestCase
from blackboard.store.upgrade_fixture import load_fixture
from hey_my_buddy.blackboard.store import backup
from hey_my_buddy.blackboard.store.db import Database, SCHEMA_VERSION
from hey_my_buddy.blackboard.routing import router
from hey_my_buddy.buddy.harnesses.codex.adapter import CodexAdapter
from hey_my_buddy.protocol.transport import _read_endpoint, _request
from hey_my_buddy.install import upgrade


class UpgradeServiceTests(BoardTestCase):
    def test_private_service_reads_settings_and_preserved_work(self):
        state = self.directory
        fixture = load_fixture(state)
        archived = backup.create(state, runtime_identity=None, plugin_commit=None, contract_version=CONTRACT_VERSION)
        self.assertEqual(backup.verify(archived["path"])["schema"], fixture["schemaVersion"])
        upgrade.migrate_schema(state)
        Database(state).initialize()
        with Database(state).read() as connection:
            skipped = router.current_router(connection, now="2026-01-01T00:00:00.000Z")
            self.assertIsNone(skipped.buddy_id)
            self.assertEqual(skipped.inspections[0]['code'], 'router-skip-window')
            resolution = router.current_router(connection, now="2026-01-01T00:01:00.000Z")
        self.assertEqual(resolution.buddy_id, fixture["routerBuddyIds"][0])
        self.assertIsNone(resolution.problem)
        self.assertEqual([item["code"] for item in resolution.inspections], [None])
        self.assertEqual(resolution.facts["routingMode"], "review")
        self.assertEqual(resolution.facts["routingBudget"], "deep")
        self.assertEqual(resolution.facts["routerRetryIntervalSeconds"], 47)

        def call(operation, params):
            return _request(_read_endpoint(state), operation, params, state_dir=state)

        with self.daemon(env={"HMB_MAX_CONCURRENT": "1"}):
            self.assertEqual(call("health", {})["schemaVersion"], SCHEMA_VERSION)
            snapshot = call("console_snapshot", {})
            self.assertEqual(snapshot["configuration"]["routerBuddyIds"], fixture["routerBuddyIds"])
            tasks = call("task_list", {})["runs"]
            self.assertIn(fixture["stableIds"]["delivered"], {task["runId"] for task in tasks})
            view = call("workflow_get", {"runId": fixture["stableIds"]["delivered"]})
            self.assertEqual(view["runId"], fixture["stableIds"]["delivered"])
            self.assertEqual(view["state"], "concluded")
