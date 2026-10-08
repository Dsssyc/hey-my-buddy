"""Host reads use cached health; explicit selection owns the bounded refresh."""
import json
from unittest.mock import Mock, patch

from hey_my_buddy.blackboard.service.service import BoardService
from hey_my_buddy.blackboard.store.store import BoardStore
from hey_my_buddy.errors import BoardError
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
        result = self.call('capabilities', refresh=True, adapter='codex')
        self.assertEqual(result['error']['code'], 'UPGRADE_IN_PROGRESS')

    def test_explicit_refresh_returns_selected_version_and_path(self):
        ready = {'adapter': 'codex', 'status': 'ready', 'version': '1.2.3', 'command': ['/native/codex'], 'executable': '/native/codex'}
        with patch('hey_my_buddy.blackboard.service.harness_health._snapshot', return_value={}), patch('hey_my_buddy.blackboard.service.harness_health._discover', return_value=ready) as native:
            result = self.call('capabilities', refresh=True, adapter='codex')
        self.assertNotIn('error', result)
        self.assertTrue(result['adapters']['codex']['available'])
        self.assertEqual(result['adapters']['codex']['harness']['version'], '1.2.3')
        native.assert_called_once()

    def test_manual_refresh_ignores_the_bounded_reread_window(self):
        # An open catalog-scan window bounds rule 3's explicit-miss re-read; the
        # operator's own adapters refresh:true forces its native read regardless.
        from datetime import datetime, timedelta, timezone
        until = (datetime.now(timezone.utc) + timedelta(seconds=180)).isoformat(timespec='milliseconds').replace('+00:00', 'Z')
        with self.board.db.write() as db:
            db.execute("INSERT INTO meta(key,value) VALUES('catalog-scan-after:codex',?)"
                       " ON CONFLICT(key) DO UPDATE SET value=excluded.value", (until,))
        ready = {'adapter': 'codex', 'status': 'ready', 'version': '1.2.3', 'command': ['/native/codex'], 'executable': '/native/codex'}
        with patch('hey_my_buddy.blackboard.service.harness_health._snapshot', return_value={}), patch('hey_my_buddy.blackboard.service.harness_health._discover', return_value=ready) as native:
            result = self.call('capabilities', refresh=True, adapter='codex')
        self.assertNotIn('error', result)
        self.assertTrue(result['adapters']['codex']['available'])
        native.assert_called_once()
        with self.board.db.read() as db:
            window = db.execute("SELECT value FROM meta WHERE key='catalog-scan-after:codex'").fetchone()[0]
        self.assertGreaterEqual(window, until, 'The forced refresh owns the next window, and was never blocked by one')


    def test_submit_only_rescans_an_explicit_unavailable_harness(self):
        with patch.object(self.board.workflow, 'submit', return_value={'admitted': True}), patch.object(self.service.harnesses, 'refresh') as refresh:
            self.assertTrue(self.call('workflow_submit')['admitted'])
            refresh.assert_not_called()
            self.assertTrue(self.call('workflow_submit', adapter='codex')['admitted'])
            refresh.assert_called_once_with('codex', force=True)

    def test_manual_path_requires_current_revision(self):
        missing = {'adapter': 'codex', 'status': 'missing', 'reasonCode': 'not-found'}
        with patch('hey_my_buddy.blackboard.service.harness_health._snapshot', return_value={}), patch('hey_my_buddy.blackboard.service.harness_health._discover', return_value=missing):
            saved = self.call('harness_set', adapter='codex', path='/custom/codex', expectedRevision=0)
        self.assertEqual(saved['harness']['manualPath'], '/custom/codex')
        refused = self.call('harness_set', adapter='codex', path='/other/codex', expectedRevision=0)
        self.assertEqual(refused['error']['code'], 'REVISION_CONFLICT')

    def test_router_catalog_eligibility_does_not_require_a_version_certificate(self):
        self.service.harnesses.catalog_refresh = self.service._refresh_harness_catalog
        payload = {'source': 'fixture', 'providers': [{'adapter': 'codex', 'provider': 'openai',
            'models': [{'id': 'gpt-6-sol', 'name': 'Sol', 'efforts': ['high']}]}],
            'discoveries': [{'adapter': 'codex', 'status': 'complete', 'accountStatus': 'confirmed'}]}
        with patch('hey_my_buddy.buddy.harnesses.base.sys.platform', 'darwin'), \
             patch('hey_my_buddy.buddy.harnesses.codex.adapter.CodexAdapter.discover_models', return_value=payload), \
             patch('hey_my_buddy.blackboard.service.harness_health._snapshot', return_value={}):
            for version in ('0.157.0', '0.158.0'):
                with self.subTest(version=version), patch('hey_my_buddy.blackboard.service.harness_health._discover', return_value={
                        'adapter': 'codex', 'status': 'ready', 'version': version,
                        'command': ['/fixture/codex'], 'executable': '/fixture/codex'}):
                    result = self.call('capabilities', refresh=True, adapter='codex')
                    self.assertNotIn('error', result)
                    with self.board.db.read() as db:
                        row = db.execute("SELECT capabilities_json FROM evaluation_profiles WHERE profile_id='codex:openai:gpt-6-sol:high'").fetchone()
                    self.assertIsNotNone(row)
                    self.assertIn('decision', json.loads(row[0]))

    def test_explicit_continuation_and_helper_also_refresh_unavailable_harness(self):
        with patch.object(self.board.workflow, 'continue_run', return_value={}), \
             patch.object(self.board.workflow, 'decide', return_value={}), \
             patch.object(self.service.harnesses, 'refresh') as refresh:
            self.call('workflow_continue', configuration={'adapter': 'codex'})
            self.call('workflow_decide', helpers=[{'spec': {'adapter': 'dsh'}}])
        self.assertEqual([call.args[0] for call in refresh.call_args_list], ['codex', 'dsh'])


class RereadClaimBindingTests(BoardTestCase):
    """A reread claim's account binding governs the real service read admission.

    Real BoardService and HarnessHealth wiring with private stop-stubbed native
    calls: the health probe and the catalog read are patched at their native
    boundaries, and an account switch is injected between the claim-side check
    and the reader's own admission. An old claim must never start a native read
    under a later selection, and the new binding's own claim must stay free to
    read.
    """

    def setUp(self):
        super().setUp()
        from hey_my_buddy.blackboard.store.db import utc_now
        from hey_my_buddy.blackboard.service.harness_health import _later
        from hey_my_buddy.buddy.harnesses.dsh.adapter import DshAdapter
        from hey_my_buddy.blackboard.catalog import catalog
        self.catalog = catalog
        self.board = BoardStore(self.directory / 'state')
        self.board.initialize()
        self.service = BoardService(self.board, token='host', control={}, on_stop=lambda p: {}, on_restart=lambda p: {})
        self.addCleanup(self.service.harnesses.close)
        self.addCleanup(lambda: catalog.register_catalog_reread(self.board.directory, None))
        self.target = {'adapter': 'dsh', 'provider': 'fixture', 'model': 'beta', 'effort': 'max'}
        self.ready = {'adapter': 'dsh', 'status': 'ready', 'version': '1.0', 'command': ['/fixture/dsh']}
        self.probes = []
        for label, result in (('_snapshot', {}), ('_discover', self.ready)):
            def stub(*args, result=result, label=label, **kwargs):
                self.probes.append(label)
                return result
            probe = patch('hey_my_buddy.blackboard.service.harness_health.' + label, side_effect=stub)
            probe.start()
            self.addCleanup(probe.stop)
        self.reads = []
        payload = self.reading(('alpha', 'beta'))
        read_stub = patch.object(DshAdapter, 'discover_models', side_effect=lambda: (self.reads.append('catalog'), payload)[1])
        read_stub.start()
        self.addCleanup(read_stub.stop)
        record = json.dumps({'account': {'source': 'native', 'credentialRevision': 0}})
        with self.board.db.write() as db:
            db.execute("INSERT INTO harness_health(adapter,status,record_json,checked_at,expires_at,revision)"
                       " VALUES('dsh','ready',?,?,?,1)", (record, utc_now(), _later(3600)))
        self.board.evaluation.record_catalog(self.reading(('alpha',)))

    def reading(self, models):
        return {'source': 'fixture-native',
                'discoveries': [{'adapter': 'dsh', 'status': 'complete', 'accountStatus': 'confirmed'}],
                'providers': [{'adapter': 'dsh', 'provider': 'fixture',
                               'models': [{'id': model, 'efforts': ['max'], 'available': True} for model in models]}]}

    def worker_provider(self):
        from hey_my_buddy.blackboard.catalog import accounts
        return accounts.using_provider('dsh', accounts.WorkerAccountProvider(
            environment=lambda state, account, environment, purpose: dict(environment),
            capabilities={'workerAccount': True}))

    def flip_account(self, source):
        """One real Accounts.set flip plus the ready repair a live check leaves."""
        from hey_my_buddy.blackboard.catalog import accounts
        from hey_my_buddy.blackboard.service.harness_health import _later
        with self.board.db.read() as db:
            expected = accounts.selection(db, 'dsh')['revision']
        self.service.account_settings.set('dsh', source, expected)
        with self.board.db.write() as db:
            db.execute("UPDATE harness_health SET status='ready',expires_at=?,scan_after=? WHERE adapter='dsh'",
                       (_later(3600), _later(180)))

    def test_a_switch_between_the_claim_check_and_the_reader_admission_skips_the_refresh(self):
        real_matches = self.catalog._reread_binding_matches

        def switch_after_the_check(directory, adapter, account_key):
            still_selected = real_matches(directory, adapter, account_key)
            if still_selected:
                # The switch lands after the claim-side check and before the
                # real service reader's own admission.
                self.flip_account('worker')
            return still_selected

        with patch.object(self.catalog, '_reread_binding_matches', switch_after_the_check), self.worker_provider():
            with self.assertRaises(BoardError) as rejected:
                self.catalog.validate_configuration(self.target, directory=self.board.directory)
        self.assertEqual(rejected.exception.code, 'CONFIGURATION_UNAVAILABLE',
                         'The cleared binding leaves the route refused on catalog facts')
        self.assertEqual(self.probes, [], 'The stale claim started no native health probe under the new binding')
        self.assertEqual(self.reads, [], 'The stale claim started no native catalog read under the new binding')
        # The new binding's own claim stays free to read and admits the route.
        with self.worker_provider():
            admitted = self.catalog.validate_configuration(self.target, directory=self.board.directory)
        self.assertEqual(admitted, self.target)
        self.assertEqual(self.probes, ['_snapshot', '_discover'], 'The new binding ran exactly one health probe')
        self.assertEqual(self.reads, ['catalog'], 'The new binding ran exactly one catalog read')

    def test_an_ab_a_round_trip_before_the_catalog_read_admission_skips_the_native_read(self):
        # The round trip lands between the health probe and the catalog read
        # admission: the returned selection holds the claim's identity again,
        # so only the selection epoch can tell the claim's binding from the
        # current one. The publication fence would keep the stale result from
        # being adopted; this boundary keeps the native read itself from
        # starting.
        real_notify = self.board._notify
        switched = []

        def switch_and_notify(head):
            if not switched:
                switched.append(True)
                self.flip_account('worker')
                self.flip_account('native')
            return real_notify(head)

        with patch.object(self.board, '_notify', switch_and_notify):
            with self.assertRaises(BoardError) as rejected:
                self.catalog.validate_configuration(self.target, directory=self.board.directory)
        self.assertEqual(rejected.exception.code, 'CONFIGURATION_UNAVAILABLE',
                         'The cleared binding leaves the route refused on catalog facts')
        self.assertEqual(self.probes, ['_snapshot', '_discover'],
                         "The claim's health probe ran while its own epoch was still selected")
        self.assertEqual(self.reads, [], 'The claim never started its catalog read under the returned epoch')
        # The returned epoch claims its own read — same identity, fresh epoch —
        # and the once-missing route is admitted.
        admitted = self.catalog.validate_configuration(self.target, directory=self.board.directory)
        self.assertEqual(admitted, self.target)
        self.assertEqual(self.probes, ['_snapshot', '_discover', '_snapshot', '_discover'])
        self.assertEqual(self.reads, ['catalog'], "Exactly one catalog read ran, on the returned epoch's own claim")
