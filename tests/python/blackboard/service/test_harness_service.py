"""Host reads use cached health; explicit selection owns the bounded refresh."""
from hey_my_buddy.blackboard.service.live_registry import LiveRegistry
import json
from unittest.mock import Mock, patch

from hey_my_buddy.blackboard.service.service import BoardService
from hey_my_buddy.blackboard.store.store import BoardStore
from support import BoardTestCase


class HarnessServiceTests(BoardTestCase):
    def test_unknown_preflight_wait_reason_is_visible_in_get_and_clears_when_ready(self):
        task = self.board.task_submit({'requestId': 'preflight-wait', 'task': 'fixture',
                                      'cwd': str(self.directory), 'adapter': 'command', 'argv': ['/bin/true']})['task']
        claim = self.board.admit_execution('n' * 32)['claim']
        attempt = claim['attempt']
        with self.board.db.write() as db:
            db.execute("UPDATE attempts SET model_harness='codex' WHERE attempt_id=?", (attempt['attemptId'],))
        params = {'attemptId': attempt['attemptId'],
                  'generation': attempt['generation'], 'nonce': 'n' * 32}
        unknown = {'harness': 'codex', 'status': 'unknown', 'available': False,
                   'lastProbe': {'status': 'unknown', 'reason': {'code': 'harness.timeout', 'params': {'operation': 'probe'}}}}
        ready = {'harness': 'codex', 'status': 'ready', 'available': True,
                 'lastProbe': {'status': 'complete'}}
        with patch.object(self.service.harnesses, 'refresh', side_effect=[unknown, ready]):
            self.assertNotIn('error', self.call('harness_prepare', **params, commandId='unknown-read'))
            view = self.board.task_get({'runId': task['runId']})['task']
            self.assertEqual(view['queueReason'], 'harness-probe-unknown')
            self.assertEqual(view['status'], 'running')
            self.assertNotIn('error', self.call('execution_progress', **params,
                                               data={'waitingReason': {'code': 'router-input-unknown', 'params': {}}}))
            self.assertEqual(self.board.task_get({'runId': task['runId']})['task']['queueReason'], 'router-input-unknown')
            head = self.board.head()
            self.assertNotIn('error', self.call('execution_progress', **params,
                                               data={'waitingReason': {'code': 'router-input-unknown', 'params': {}}}))
            self.assertEqual(self.board.head(), head)
            waiting = [event for event in self.board.events_wait({'after': 0, 'taskId': task['runId'],
                         'timeoutMs': 0})['events'] if event['kind'] == 'attempt.waiting']
            self.assertEqual(waiting[-1]['payload']['reason'], {'code': 'router-input-unknown', 'params': {}})
            self.assertNotIn('error', self.call('execution_progress', **params, data={'waitingReason': None}))
            self.assertEqual(self.board.head(), head + 1)
            self.assertEqual(self.call('execution_progress', **params,
                             data={'waitingReason': 'router-input-unknown'})['error']['code'], 'INVALID_ARGUMENT')
            for invalid in ({'code': 'unregistered-wait', 'params': {}},
                            {'code': 'router-input-unknown', 'params': {'unexpected': 'fact'}}):
                self.assertEqual(self.call('execution_progress', **params,
                                          data={'waitingReason': invalid})['error']['code'], 'INVALID_ARGUMENT')
            head = self.board.head()
            self.assertNotIn('error', self.call('execution_progress', **params, data={'waitingReason': None}))
            self.assertEqual(self.board.head(), head)
            self.assertNotIn('error', self.call('harness_prepare', **params, commandId='ready-read'))
            self.assertIsNone(self.board.task_get({'runId': task['runId']})['task']['queueReason'])

    def setUp(self):
        super().setUp()
        self.board = BoardStore(self.directory / 'state')
        self.board.initialize()
        self.live_registry = LiveRegistry()
        self.service = BoardService(self.board, live_registry=self.live_registry, token='host', control={}, on_stop=lambda p: {}, on_restart=lambda p: {}, automatic_discovery=True)
        self.enterContext(patch('hey_my_buddy.buddy.harnesses.codex.account_probe.read', return_value=(None, None, None)))
        self.service.harnesses.kick = Mock()
        self.service.harnesses.catalog_refresh = Mock()
        self.addCleanup(self.service.harnesses.close)

    def call(self, method, **params):
        return json.loads(getattr(self.service, method)(json.dumps({'token': 'host', **params})))

    def test_capabilities_reports_unknown_without_calling_a_native_cli(self):
        with patch('hey_my_buddy.blackboard.service.harness_health._discover') as native:
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
        result = self.call('capabilities', refresh=True, harness='codex')
        self.assertEqual(result['error']['code'], 'UPGRADE_IN_PROGRESS')

    def test_explicit_refresh_returns_selected_version_and_path(self):
        ready = {'harness': 'codex', 'status': 'ready', 'version': '1.2.3', 'command': ['/native/codex'], 'executable': '/native/codex'}
        with patch('hey_my_buddy.blackboard.service.harness_health._snapshot', return_value={'harness': 'codex', 'managerRecords': [], 'candidates': []}), patch('hey_my_buddy.blackboard.service.harness_health._discover', return_value=ready) as native:
            result = self.call('capabilities', refresh=True, harness='codex')
        self.assertNotIn('error', result)
        self.assertTrue(result['adapters']['codex']['available'])
        self.assertEqual(result['adapters']['codex']['harness']['version'], '1.2.3')
        native.assert_called_once()

    def test_manual_refresh_ignores_the_bounded_reread_window(self):
        # An open catalog-scan window bounds rule 3's explicit-miss re-read; the
        # operator's own harness refresh:true forces its native read regardless.
        from datetime import datetime, timedelta, timezone
        until = (datetime.now(timezone.utc) + timedelta(seconds=180)).isoformat(timespec='milliseconds').replace('+00:00', 'Z')
        with self.board.db.write() as db:
            db.execute("INSERT INTO harness_health(harness,scan_after) VALUES('codex',?)"
                       " ON CONFLICT(harness) DO UPDATE SET scan_after=excluded.scan_after", (until,))
        ready = {'harness': 'codex', 'status': 'ready', 'version': '1.2.3', 'command': ['/native/codex'], 'executable': '/native/codex'}
        with patch('hey_my_buddy.blackboard.service.harness_health._snapshot', return_value={'harness': 'codex', 'managerRecords': [], 'candidates': []}), patch('hey_my_buddy.blackboard.service.harness_health._discover', return_value=ready) as native:
            result = self.call('capabilities', refresh=True, harness='codex')
        self.assertNotIn('error', result)
        self.assertTrue(result['adapters']['codex']['available'])
        native.assert_called_once()
        with self.board.db.read() as db:
            window = db.execute("SELECT scan_after FROM harness_health WHERE harness='codex'").fetchone()[0]
        self.assertGreaterEqual(window, until, 'The forced refresh owns the next window, and was never blocked by one')


    def test_submit_only_rescans_an_explicit_unavailable_harness(self):
        base = {'requestId': 'discovery-submit', 'hostId': 'host', 'task': 'fixture',
                'cwd': str(self.directory), 'executionWorkspace': {'kind': 'existing', 'access': 'read'}}
        with patch.object(self.board.workflow, 'submit_normalized', return_value={'admitted': True}), patch.object(self.service.harnesses, 'refresh') as refresh:
            self.assertTrue(self.call('workflow_submit', **base)['admitted'])
            refresh.assert_not_called()
            self.assertTrue(self.call('workflow_submit', **base, harness='codex', provider='openai', model='fixture', effort='high')['admitted'])
            refresh.assert_called_once_with('codex', force=True)

    def test_manual_path_write_uses_latest_value(self):
        missing = {'harness': 'codex', 'status': 'missing',
                   'reason': {'code': 'harness.not-found', 'params': {'operation': 'probe'}}}
        with patch('hey_my_buddy.blackboard.service.harness_health._snapshot', return_value={'harness': 'codex', 'managerRecords': [], 'candidates': []}), patch('hey_my_buddy.blackboard.service.harness_health._discover', return_value=missing):
            saved = self.call('harness_set', harness='codex', path='/custom/codex')
            latest = self.call('harness_set', harness='codex', path='/other/codex')
        self.assertEqual(saved['harness']['manualPath'], '/custom/codex')
        self.assertEqual(latest['harness']['manualPath'], '/other/codex')
        self.assertEqual(self.call('harness_set', harness='codex', path='/third/codex', expectedRevision=0)['error']['code'], 'INVALID_ARGUMENT')

    def test_router_catalog_eligibility_does_not_require_a_version_certificate(self):
        self.service.harnesses.catalog_refresh = self.service._refresh_harness_catalog
        payload = {'source': 'fixture', 'providers': [{'harness': 'codex', 'provider': 'openai',
            'models': [{'id': 'gpt-6-sol', 'name': 'Sol', 'efforts': ['high']}]}],
            'discoveries': [{'harness': 'codex', 'status': 'complete', 'accountStatus': 'confirmed'}]}
        with patch('hey_my_buddy.buddy.harnesses.base.sys.platform', 'darwin'), \
             patch('hey_my_buddy.buddy.harnesses.codex.adapter.CodexAdapter.discover_models', return_value=payload), \
             patch('hey_my_buddy.blackboard.service.harness_health._snapshot', return_value={'harness': 'codex', 'managerRecords': [], 'candidates': []}):
            for version in ('0.157.0', '0.158.0'):
                with self.subTest(version=version), patch('hey_my_buddy.blackboard.service.harness_health._discover', return_value={
                        'harness': 'codex', 'status': 'ready', 'version': version,
                        'command': ['/fixture/codex'], 'executable': '/fixture/codex'}):
                    result = self.call('capabilities', refresh=True, harness='codex')
                    self.assertNotIn('error', result)
                    with self.board.db.read() as db:
                        row = db.execute("SELECT capabilities_json FROM buddies WHERE buddy_id='codex:openai:gpt-6-sol:high'").fetchone()
                    self.assertIsNotNone(row)
                    self.assertIn('decision', json.loads(row[0]))

    def test_explicit_continuation_and_helper_also_refresh_unavailable_harness(self):
        with patch.object(self.board.workflow, 'continue_run', return_value={}), \
             patch.object(self.board.workflow, 'normalize_decision', return_value={'helpers': [{'spec': {'harness': 'dsh'}}]}), \
             patch.object(self.board.workflow, 'decide_normalized', return_value={}), \
             patch.object(self.service.harnesses, 'refresh') as refresh:
            self.call('workflow_continue', buddy={'harness': 'codex'})
            self.call('workflow_decide', helpers=[{'spec': {'harness': 'dsh'}}])
        self.assertEqual([call.args[0] for call in refresh.call_args_list], ['codex', 'dsh'])




    def test_accounts_reports_cached_native_login_without_source_management(self):
        ready = {'harness': 'codex', 'status': 'ready', 'version': '1.2.3', 'command': ['/native/codex']}
        with patch('hey_my_buddy.blackboard.service.harness_health._snapshot', return_value={'harness': 'codex', 'managerRecords': [], 'candidates': []}), patch('hey_my_buddy.blackboard.service.harness_health._discover', return_value=ready):
            self.call('capabilities', refresh=True, harness='codex')
        with patch('hey_my_buddy.blackboard.service.harness_health._discover') as native:
            result = self.call('accounts')
        codex = next(item for item in result['accounts'] if item['harness'] == 'codex')
        self.assertEqual(codex['status'], 'ready')
        self.assertNotIn('source', codex)
        self.assertNotIn('capabilities', codex)
        native.assert_not_called()


    def test_catalog_native_read_is_fenced_by_health_generation(self):
        self.service.harnesses.catalog_refresh = self.service._refresh_harness_catalog
        old = self.service.harnesses.get('codex')
        with self.board.db.write() as db:
            db.execute("INSERT INTO harness_health(harness,status,record_json,revision) VALUES('codex','unknown','{}',1)")
        with patch('hey_my_buddy.buddy.harnesses.registry.adapter') as native:
            self.service._refresh_harness_catalog('codex', old)
        native.return_value.discover_models.assert_not_called()

    def test_retired_account_operations_are_absent_from_the_service(self):
        for name in ('set', 'login', 'status', 'cancel', 'logout', 'remove'):
            self.assertFalse(hasattr(self.service, 'account_' + name))
