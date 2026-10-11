"""Real private reservations around the three native reading call sites."""
import json
from types import SimpleNamespace
from unittest.mock import patch

from hey_my_buddy.blackboard.catalog import catalog
from hey_my_buddy.blackboard.service.service import BoardService
from hey_my_buddy.blackboard.store.store import BoardStore
from hey_my_buddy.buddy.harnesses.runtime_selection import bound
from hey_my_buddy.errors import BoardError
from support import BoardTestCase


class NativeStopMatrix:
    def setUp(self):
        super().setUp()
        self.board = BoardStore(self.directory / 'native-stop')
        self.board.initialize()
        self.service = BoardService(self.board, token='host', control={},
                                    on_stop=lambda p: {}, on_restart=lambda p: {}, automatic_discovery=False)
        self.addCleanup(self.service.harnesses.close)
        self.addCleanup(self.service.account_operations.close)
        self.record = self.service.harnesses.get('codex')
        self.payload = {'source': 'fixture-native', 'providers': [], 'warnings': [],
                        'discoveries': [{'adapter': 'codex', 'status': 'complete', 'accountStatus': 'confirmed'}]}

    def operations(self):
        with self.board.db.read() as db:
            return [json.loads(row[0]) for row in db.execute("SELECT value FROM meta WHERE key LIKE 'account-operation:%'")]

    def fail_read(self, stopped):
        error = BoardError('CATALOG_UNAVAILABLE', 'fixture read failed')
        if stopped is not None:
            error.discovery_shutdown_confirmed = stopped
        self.invoke(error)

    def test_read_failure_with_confirmed_stop_removes_operation(self):
        self.fail_read(True)
        self.assertEqual(self.operations(), [])

    def test_read_failure_with_unconfirmed_stop_retains_uncertain(self):
        self.fail_read(False)
        self.assertEqual([r['status'] for r in self.operations()], ['uncertain'])

    def test_read_failure_without_stop_evidence_retains_uncertain(self):
        self.fail_read(None)
        self.assertEqual([r['status'] for r in self.operations()], ['uncertain'])

    def test_invalid_return_after_confirmed_stop_removes_operation(self):
        self.invoke({'source': 42, 'providers': [], 'warnings': []})
        self.assertEqual(self.operations(), [])

    def test_success_with_confirmed_stop_removes_operation(self):
        self.invoke(self.payload)
        self.assertEqual(self.operations(), [])


class CatalogNativeStopTests(NativeStopMatrix, BoardTestCase):
    def invoke(self, value):
        def read():
            self.assertEqual([r['status'] for r in self.operations()], ['active'])
            if isinstance(value, Exception):
                raise value
            return value
        instance = SimpleNamespace(model_discovery=True, discovery_available=lambda: (True, None), discover_models=read)
        with patch('hey_my_buddy.buddy.harnesses.registry.adapters', return_value={'codex': instance}), \
             patch.object(catalog, '_override', return_value=None), bound([self.record]):
            return catalog.discover(directory=self.board.directory, database=self.board.db)

    def test_foreign_adapter_return_after_stop_removes_operation(self):
        payload = {**self.payload, 'providers': [{'adapter': 'zcode', 'provider': 'fixture',
                                                'displayName': 'Fixture', 'models': []}],
                   'discoveries': [{'adapter': 'zcode', 'status': 'complete', 'accountStatus': 'confirmed'}]}
        self.assertEqual(catalog.canonical_payload(payload)['providers'][0]['adapter'], 'zcode')
        result = self.invoke(payload)
        self.assertEqual(result['discoveries'][0].get('reason'), 'CATALOG_INVALID')
        self.assertEqual(self.operations(), [])


class ServiceCatalogNativeStopTests(NativeStopMatrix, BoardTestCase):
    def invoke(self, value):
        def read():
            self.assertEqual([r['status'] for r in self.operations()], ['active'])
            if isinstance(value, Exception):
                raise value
            return value
        with patch('hey_my_buddy.buddy.harnesses.codex.adapter.CodexAdapter.discover_models', side_effect=read):
            return self.service._refresh_harness_catalog('codex', self.record)
