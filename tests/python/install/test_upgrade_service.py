"""Real private RPC, delivered Host sealing and replay after the upgrade path."""
from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3

from support import BoardTestCase
from blackboard.store.test_migrations import DELIVERED
from blackboard.store.upgrade_fixture import FIXTURE, load_fixture
from hey_my_buddy.blackboard.store import backup
from hey_my_buddy.blackboard.store.db import Database
from hey_my_buddy.blackboard.store.store import BoardStore
from hey_my_buddy.blackboard.store.workspace_identity_migration import migrate_workspace_identity
from hey_my_buddy.install import upgrade
from hey_my_buddy.protocol.transport import _read_endpoint, _request


class UpgradeServiceTests(BoardTestCase):
    def test_real_private_service_reads_the_fixed_upgrade_and_uses_public_host_conclusions(self):
        fixture = load_fixture(self.directory)
        operation_id = fixture['accountOperation']['operationId']
        with closing(sqlite3.connect(self.directory / 'board.sqlite3')) as db:
            original_operation = db.execute('SELECT value FROM meta WHERE key=?', (operation_id,)).fetchone()[0]
        current = Path(backup.create(BoardStore(self.directory))['path'])
        verified = backup.verify(current)
        before = {**verified['databaseSnapshot'], 'schema':verified['schema']}
        migration, expected = upgrade.migrate_board(self.directory, before)
        identity, expected = migrate_workspace_identity(self.directory, expected)
        self.assertEqual(migration['toSchema'], 17)
        self.assertEqual(identity['mapped'], [])
        self.assertEqual(identity['kept'], {})
        self.assertEqual(upgrade.idle_snapshot(self.directory), {key:value for key,value in expected.items()
                                                                if key != 'runtimeSettings'})
        self.catalog_fixture(json.loads((FIXTURE / 'catalog.json').read_text()))
        database = Database(self.directory)
        database.initialize()

        def call(operation, params):
            return _request(_read_endpoint(self.directory), operation, params, state_dir=self.directory)

        def control(run_id):
            with database.read() as db:
                row = db.execute('SELECT host_id,owner_generation FROM workflow_runs WHERE run_id=?', (run_id,)).fetchone()
            return {'hostId':row['host_id'], 'ownerGeneration':row['owner_generation'],
                    'controlToken':database.control_token(run_id, row['owner_generation'])}

        try:
            with self.daemon(env={'BUDDY_MAX_CONCURRENT':'1'}):
                self.assertEqual(call('health', {})['schemaVersion'], 17)
                with database.read() as db:
                    self.assertEqual(db.execute('SELECT value FROM meta WHERE key=?', (operation_id,)).fetchone(),
                                     None)
                snapshot = call('console_snapshot', {})
                self.assertEqual(snapshot['configuration']['routerProfileIds'], fixture['routerProfileIds'])
                models = call('model_profiles', {'includeUnavailable': True})['profiles']
                self.assertTrue(models)
                self.assertTrue(all(model['catalogState'] == 'unknown' for model in models))
                self.assertTrue(all(model['catalogReason'] == 'ACCOUNT_STATUS_UNKNOWN' for model in models))
                delivered = call('workflow_get', {'runId':DELIVERED})
                self.assertEqual(delivered['state'], 'delivered')
                self.assertIsNone(delivered['hostConclusion'])
                with database.read() as db:
                    worker_output = db.execute("SELECT * FROM workflow_artifacts WHERE run_id=? AND kind='output'",
                                               (DELIVERED,)).fetchone()
                worker_manifest = json.loads(worker_output['manifest_json'])
                worker_file = Path(worker_manifest['diffPath']).with_name('output.json')
                worker_bytes = worker_file.read_bytes()
                host_file = worker_file.parent.with_name(worker_file.parent.name + '-host') / 'output.json'
                self.assertFalse(host_file.exists())
                params = {'runId':DELIVERED, 'note':'private upgraded fixture', **control(DELIVERED)}
                concluded = call('workflow_conclude', params)
                self.assertFalse(concluded['duplicate'])
                self.assertEqual(concluded['conclusion']['executionStatus'], 'delivered')
                self.assertEqual(concluded['state'], 'delivered')
                self.assertIsNotNone(concluded['conclusion']['artifactId'])
                replay = call('workflow_conclude', params)
                self.assertTrue(replay['duplicate'])
                self.assertEqual(replay['conclusion'], concluded['conclusion'])
                self.assertEqual(call('workflow_get', {'runId':DELIVERED})['hostConclusion'], concluded['conclusion'])
                with closing(sqlite3.connect(self.directory / 'board.sqlite3')) as db:
                    self.assertEqual(db.execute('SELECT COUNT(*) FROM workflow_host_conclusions WHERE run_id=?',
                                                (DELIVERED,)).fetchone()[0], 1)
                    self.assertEqual(db.execute("SELECT COUNT(*) FROM events WHERE task_id=? AND kind='workflow.host_concluded'",
                                                (DELIVERED,)).fetchone()[0], 1)
                    artifact = db.execute('SELECT manifest_sha256 FROM workflow_artifacts WHERE artifact_id=?',
                                          (concluded['conclusion']['artifactId'],)).fetchone()
                    self.assertEqual(db.execute('SELECT * FROM workflow_artifacts WHERE artifact_id=?',
                                                (worker_output['artifact_id'],)).fetchone(), tuple(worker_output))
                sealed = json.loads(host_file.read_text())
                self.assertEqual(sealed['kind'], 'host-output')
                self.assertEqual(sealed['snapshotSha256'], artifact[0])
                self.assertEqual(worker_file.read_bytes(), worker_bytes)
                with database.read() as db:
                    self.assertEqual(db.execute('SELECT value FROM meta WHERE key=?', (operation_id,)).fetchone(),
                                     None)
        finally:
            # The focused runner retains this raw child log after normal harness
            # teardown removes the private test state.
            log = self.directory / 'test-daemon.log'
            if log.exists():
                retained = Path(os.environ['TMPDIR']) / 'adr021-l18-service.log'
                retained.write_bytes(log.read_bytes())
