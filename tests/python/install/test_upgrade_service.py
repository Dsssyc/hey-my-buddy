"""A real private service starts on the fresh board produced from schema 18."""
from pathlib import Path
from unittest import mock

from support import BoardTestCase
from blackboard.store.upgrade_fixture import load_fixture
from hey_my_buddy.blackboard.store import backup
from hey_my_buddy.blackboard.store.db import Database, SCHEMA_VERSION
from hey_my_buddy.blackboard.store.store import BoardStore
from hey_my_buddy.blackboard.routing import router
from hey_my_buddy.buddy.harnesses.codex.adapter import CodexAdapter
from hey_my_buddy.install import upgrade
from hey_my_buddy.protocol.transport import ServiceError, _read_endpoint, _request


class UpgradeServiceTests(BoardTestCase):
    def test_private_service_reads_settings_and_cannot_find_archived_work(self):
        state = self.directory
        fixture = load_fixture(state)
        current = Path(backup.create(BoardStore(state))["path"])
        verified = backup.verify(current)
        before = {**verified["databaseSnapshot"], "schema": verified["schema"]}
        upgrade.migrate_board(state, before)
        Database(state).initialize()
        with mock.patch.object(CodexAdapter, "local_read_only_check", return_value={
            "eligible": True, "reasonCode": None, "reason": None,
            "systemSandbox": True, "sameAttemptContinuation": True,
        }), Database(state).read() as connection:
            resolution = router.current_router(connection, now="2026-01-01T00:00:00.000Z")
        self.assertEqual(resolution.buddy_id, fixture["routerProfileIds"][0], resolution.problem)
        self.assertEqual(resolution.facts["routingMode"], "review")
        self.assertEqual(resolution.facts["routingBudget"], "deep")
        self.assertEqual(resolution.facts["routerRetryIntervalSeconds"], 47)

        def call(operation, params):
            return _request(_read_endpoint(state), operation, params, state_dir=state)

        with self.daemon(env={"HMB_MAX_CONCURRENT": "1"}):
            self.assertEqual(call("health", {})["schemaVersion"], SCHEMA_VERSION)
            snapshot = call("console_snapshot", {})
            self.assertEqual(snapshot["configuration"]["routerBuddyIds"], fixture["routerProfileIds"])
            self.assertEqual(call("task_list", {})["tasks"], [])
            with self.assertRaises(ServiceError) as caught:
                call("workflow_get", {"runId": fixture["stableIds"]["delivered"]})
            self.assertEqual(caught.exception.code, "NOT_FOUND")
