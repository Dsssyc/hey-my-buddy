"""A real private service reads the base fixture board and its history."""
import json
from unittest import mock

from hey_my_buddy.protocol.contracts import CONTRACT_VERSION
from support import BoardTestCase
from blackboard.store.upgrade_fixture import load_fixture
from hey_my_buddy.blackboard.store import backup
from hey_my_buddy.blackboard.store.db import Database, SCHEMA_VERSION
from hey_my_buddy.blackboard.store.upgrade import upgrade_state
from hey_my_buddy.blackboard.routing import router
from hey_my_buddy.blackboard.tasks import storage
from hey_my_buddy.buddy.harnesses.codex.adapter import CodexAdapter
from hey_my_buddy.protocol.transport import _read_endpoint, _request
from hey_my_buddy.install import upgrade
from hey_my_buddy.errors import BoardError


class UpgradeServiceTests(BoardTestCase):
    def test_runtime_preparation_fence_releases_after_detaching_the_old_service(self):
        state = self.directory
        root = state / "runtimes"
        previous, target = root / ("a" * 32), root / ("b" * 32)
        endpoint = {"runtimeIdentity": "runtime:" + previous.name, "contractVersion": CONTRACT_VERSION,
                    "serviceId": "previous", "pid": 123}
        health = {**endpoint, "maxConcurrent": 2, "waitCapacity": 4}
        (state / "control.json").write_text(json.dumps(endpoint), encoding="utf-8")

        def assert_storage_busy():
            with self.assertRaises(BoardError) as busy, storage.locked(state):
                self.fail("storage entered before detach returned")
            self.assertEqual(busy.exception.code, "STORAGE_BUSY")

        def materialize():
            assert_storage_busy()
            return {"runtimeDir": str(target)}

        def detach(directory, runtime, *, require_idle):
            self.assertEqual((directory, runtime, require_idle), (state, previous, False))
            assert_storage_busy()

        def start(directory, runtime):
            self.assertEqual((directory, runtime), (state, target))
            with storage.locked(state):
                return {}

        with (
            mock.patch.multiple(upgrade.runtime, runtime_root=mock.Mock(return_value=root),
                                is_ready=mock.Mock(return_value=True), materialize=materialize),
            mock.patch.multiple(upgrade, probe=mock.Mock(return_value=health), preflight=mock.Mock(return_value={}),
                                detach=detach, start=start, verify_started=mock.Mock(return_value={}),
                                write_active_runtime=mock.Mock(), _finish_host_retirement=mock.Mock(return_value={}),
                                retain_runtimes=mock.Mock(return_value={})),
        ):
            self.assertTrue(upgrade.upgrade(state)["upgraded"])

    def test_verify_started_rejects_persistence_error_before_other_checks(self):
        for contract_changed in (False, True):
            with self.subTest(contract_changed=contract_changed):
                before = {"schema": SCHEMA_VERSION, "tables": {}, "fingerprints": {}}
                with self.assertRaises(BoardError) as caught, \
                        mock.patch.object(upgrade, "idle_snapshot", return_value=before) as snapshot:
                    upgrade.verify_started(
                        self.directory, self.directory / "runtime",
                        {"runtimeContentId": "runtime", "runtimeStable": True, "schemaVersion": SCHEMA_VERSION,
                         "persistenceError": "Maintenance scheduler: BoardError: admission fenced"},
                        before, contract_changed=contract_changed)
                self.assertEqual(caught.exception.code, "UPGRADE_VERIFY_FAILED")
                snapshot.assert_not_called()

    def test_private_service_reads_settings_and_preserved_work(self):
        state = self.directory
        fixture = load_fixture(state)
        archived = backup.create(state, runtime_identity=None, plugin_commit=None, contract_version=CONTRACT_VERSION)
        self.assertEqual(backup.verify(archived["path"])["schema"], fixture["schemaVersion"])
        self.assertEqual(upgrade_state(state)['targetSchema'], SCHEMA_VERSION)
        database = Database(state)
        self.addCleanup(database.close)
        database.initialize()
        with database.read() as connection:
            skipped = router.current_router(connection, now="2026-01-01T00:00:00.000Z")
            self.assertIsNone(skipped.buddy_id)
            self.assertEqual(skipped.inspections[0]['code'], 'router-skip-window')
            resolution = router.current_router(connection, now="2026-01-01T00:01:00.000Z")
        self.assertEqual(resolution.buddy_id, fixture["routerBuddyIds"][0])
        self.assertIsNone(resolution.problem)
        self.assertEqual([item["code"] for item in resolution.inspections], [None])
        self.assertEqual(resolution.facts["routingMode"], "review")
        self.assertEqual(resolution.facts["modelResponseWaitSeconds"], 60)
        self.assertEqual(resolution.facts["routerRetryIntervalSeconds"], 47)

        def call(operation, params):
            return _request(_read_endpoint(state), operation, params, state_dir=state)

        with self.daemon(env={"HMB_MAX_CONCURRENT": "1"}):
            self.assertEqual(call("health", {})["schemaVersion"], SCHEMA_VERSION)
            tasks = call("task_list", {})["runs"]
            self.assertIn(fixture["stableIds"]["delivered"], {task["runId"] for task in tasks})
            view = call("workflow_get", {"runId": fixture["stableIds"]["delivered"]})
            self.assertEqual(view["runId"], fixture["stableIds"]["delivered"])
            self.assertEqual(view["state"], "concluded")
