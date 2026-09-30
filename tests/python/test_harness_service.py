"""Host reads use cached health; explicit selection owns the bounded refresh."""
import json
from unittest.mock import Mock, patch

from buddy.service import BoardService
from buddy.store import BoardStore
from support import BoardTestCase


class HarnessServiceTests(BoardTestCase):
    def setUp(self):
        super().setUp()
        self.board = BoardStore(self.directory / 'state')
        self.board.initialize()
        self.service = BoardService(self.board, token='host', control={}, on_stop=lambda p: {}, on_restart=lambda p: {}, automatic_discovery=True)
        self.service.harnesses.kick = Mock()
        self.service.harnesses.catalog_refresh = Mock()
        self.addCleanup(self.service.harnesses.close)

    def call(self, method, **params):
        return json.loads(getattr(self.service, method)(json.dumps({'token': 'host', **params})))

    def test_capabilities_reports_unknown_without_calling_a_native_cli(self):
        with patch('buddy.harness_health._discover') as native:
            result = self.call('capabilities')
        self.assertNotIn('error', result)
        self.assertFalse(result['adapters']['codex']['available'])
        self.assertEqual(result['adapters']['codex']['harness']['status'], 'unknown')
        native.assert_not_called()
        self.service.harnesses.kick.assert_called_once()

    def test_service_start_does_not_mutate_the_migration_snapshot(self):
        with self.board.db.read() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM harness_health').fetchone()[0], 0)

    def test_upgrade_fence_allows_cached_diagnostics_but_refuses_refresh(self):
        (self.board.directory / 'upgrade.json').write_text('{}')
        self.assertNotIn('error', self.call('capabilities'))
        self.service.harnesses.kick.assert_not_called()
        result = self.call('capabilities', refresh=True, adapter='codex')
        self.assertEqual(result['error']['code'], 'UPGRADE_IN_PROGRESS')

    def test_explicit_refresh_returns_selected_version_and_path(self):
        ready = {'adapter': 'codex', 'status': 'ready', 'version': '1.2.3', 'command': ['/native/codex'], 'executable': '/native/codex'}
        with patch('buddy.harness_health._snapshot', return_value={}), patch('buddy.harness_health._discover', return_value=ready) as native:
            result = self.call('capabilities', refresh=True, adapter='codex')
        self.assertNotIn('error', result)
        self.assertTrue(result['adapters']['codex']['available'])
        self.assertEqual(result['adapters']['codex']['harness']['version'], '1.2.3')
        native.assert_called_once()

    def test_submit_only_rescans_an_explicit_unavailable_harness(self):
        with patch.object(self.board.workflow, 'submit', return_value={'admitted': True}), patch.object(self.service.harnesses, 'refresh') as refresh:
            self.assertTrue(self.call('workflow_submit')['admitted'])
            refresh.assert_not_called()
            self.assertTrue(self.call('workflow_submit', adapter='codex')['admitted'])
            refresh.assert_called_once_with('codex', force=True)

    def test_manual_path_requires_current_revision(self):
        missing = {'adapter': 'codex', 'status': 'missing', 'reasonCode': 'not-found'}
        with patch('buddy.harness_health._snapshot', return_value={}), patch('buddy.harness_health._discover', return_value=missing):
            saved = self.call('harness_set', adapter='codex', path='/custom/codex', expectedRevision=0)
        self.assertEqual(saved['harness']['manualPath'], '/custom/codex')
        refused = self.call('harness_set', adapter='codex', path='/other/codex', expectedRevision=0)
        self.assertEqual(refused['error']['code'], 'REVISION_CONFLICT')

    def test_router_catalog_certificate_uses_the_published_health_version(self):
        self.service.harnesses.catalog_refresh = self.service._refresh_harness_catalog
        payload = {'source': 'fixture', 'providers': [{'adapter': 'codex', 'provider': 'openai',
            'models': [{'id': 'gpt-6-sol', 'name': 'Sol', 'efforts': ['high']}]}]}
        with patch('buddy.adapters.codex.sys.platform', 'darwin'), \
             patch('buddy.adapters.codex.CodexAdapter.discover_models', return_value=payload), \
             patch('buddy.harness_health._snapshot', return_value={}):
            for version, verified in [('0.157.0', True), ('0.158.0', False)]:
                with self.subTest(version=version), patch('buddy.harness_health._discover', return_value={
                        'adapter': 'codex', 'status': 'ready', 'version': version,
                        'command': ['/fixture/codex'], 'executable': '/fixture/codex'}):
                    result = self.call('capabilities', refresh=True, adapter='codex')
                    self.assertNotIn('error', result)
                    with self.board.db.read() as db:
                        row = db.execute("SELECT capabilities_json FROM evaluation_profiles WHERE profile_id='codex:openai:gpt-6-sol:high'").fetchone()
                    self.assertIsNotNone(row)
                    self.assertEqual('decision' in json.loads(row[0]), verified)

    def test_explicit_continuation_and_helper_also_refresh_unavailable_harness(self):
        with patch.object(self.board.workflow, 'continue_run', return_value={}), \
             patch.object(self.board.workflow, 'decide', return_value={}), \
             patch.object(self.service.harnesses, 'refresh') as refresh:
            self.call('workflow_continue', configuration={'adapter': 'codex'})
            self.call('workflow_decide', helpers=[{'spec': {'adapter': 'dsh'}}])
        self.assertEqual([call.args[0] for call in refresh.call_args_list], ['codex', 'dsh'])
