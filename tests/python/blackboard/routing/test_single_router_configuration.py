"""Human settings patches and upgrade-required reads on private boards."""
from unittest.mock import patch
from hey_my_buddy.errors import BoardError
from support import BoardTestCase

PROFILE = 'dsh:deepseek-official:deepseek-flash:off'


class SingleRouterConfigurationTests(BoardTestCase):
    def setUp(self):
        super().setUp()
        self.catalog_fixture()
        self.board_ = self.board()
        self.board_.call('model_catalog_refresh', {'requestId': 'configuration-catalog'})

    def configure(self, settings, *, enable=False):
        revision = self.board_.call('console_snapshot', {})['tableRevision']
        grant = self.board_.console_call('evaluation_write_begin', {
            'requestId': f'writer-{revision}', 'kind': 'human', 'expectedRevision': revision})
        params = {key: grant[key] for key in ('writerId', 'generation', 'writerToken')}
        params.update(commandId=f'publish-{revision}', expectedRevision=revision, configuration=settings)
        if enable:
            params['profileSettings'] = [{'profileId': PROFILE, 'enabled': True}]
        return self.board_.console_call('user_policy_publish', params)

    def settings(self):
        return self.board_.call('console_snapshot', {})['configuration']

    def test_fresh_defaults_have_an_empty_router_list(self):
        snapshot = self.board_.call('console_snapshot', {})
        settings = snapshot['configuration']
        self.assertEqual(settings['routerProfileIds'], [])
        self.assertEqual(settings['defaultRoutingMode'], 'fast')
        self.assertEqual(settings['routingBudget'], 'standard')
        self.assertNotIn('fastRouterProfileId', settings)
        self.assertNotIn('reviewRouterProfileId', settings)
        self.assertIsNone(snapshot['configurationError'])

    def test_budget_patch_and_clear_preserve_other_settings(self):
        self.configure({'routerProfileIds': [PROFILE]}, enable=True)
        self.configure({'routingBudget': 'brief'})
        self.assertEqual(self.settings()['routerProfileIds'], [PROFILE])
        self.configure({'routerProfileIds': []})
        self.assertEqual(self.settings()['routerProfileIds'], [])
        self.assertEqual(self.settings()['routingBudget'], 'brief')

    def test_mode_patch_preserves_temporarily_ineligible_router_and_reports_qualification(self):
        self.configure({'routerProfileIds': [PROFILE]}, enable=True)
        with patch('hey_my_buddy.buddy.harnesses.dsh.adapter.DshAdapter.local_read_only_check', return_value={
                'eligible': False, 'reasonCode': 'readonly-tools-unrestricted',
                'reason': 'fixture lost its restriction', 'systemSandbox': False,
                'sameAttemptContinuation': False}):
            self.configure({'defaultRoutingMode': 'review'})
            from hey_my_buddy.blackboard.routing import router
            with self.board_.store.db.read() as db:
                resolution = router.current_router(db)
            self.assertIsNone(resolution.profile)
            self.assertEqual(resolution.inspections[0]['code'], 'router-review-unsupported')
        self.assertEqual(self.settings()['defaultRoutingMode'], 'review')
        self.assertEqual(self.settings()['routerProfileIds'], [PROFILE])

    def test_unavailable_existing_router_does_not_block_budget_or_clear(self):
        self.configure({'routerProfileIds': [PROFILE]}, enable=True)
        with self.board_.store.db.write() as db:
            db.execute('UPDATE evaluation_profiles SET available=0 WHERE profile_id=?', (PROFILE,))
        self.configure({'routingBudget': 'deep'})
        self.assertEqual(self.settings()['routerProfileIds'], [PROFILE])
        self.configure({'routerProfileIds': [], 'defaultRoutingMode': 'review'})
        self.assertEqual(self.settings()['routerProfileIds'], [])
        self.assertEqual(self.settings()['defaultRoutingMode'], 'review')

    def test_old_settings_are_reported_without_conversion_or_write(self):
        with self.board_.store.db.write() as db:
            db.execute("UPDATE meta SET value='1' WHERE key='router_configuration_version'")
            db.execute("INSERT INTO meta(key,value) VALUES('router_fast_profile_id',?)", (PROFILE,))
        with self.board_.store.db.read() as db:
            before = list(map(tuple, db.execute("SELECT key,value FROM meta WHERE key LIKE 'router_%' ORDER BY key")))
        snapshot = self.board_.call('console_snapshot', {})
        self.assertIsNone(snapshot['configuration'])
        self.assertEqual(snapshot['configurationError']['code'], 'router-settings-upgrade-required')
        with self.board_.store.db.read() as db:
            self.assertEqual(list(map(tuple, db.execute("SELECT key,value FROM meta WHERE key LIKE 'router_%' ORDER BY key"))), before)

    def test_old_slot_patch_is_refused_atomically(self):
        before = self.settings()
        with self.assertRaises(BoardError) as caught:
            self.configure({'fastRouterProfileId': PROFILE}, enable=True)
        self.assertEqual(caught.exception.code, 'INVALID_ARGUMENT')
        self.assertEqual(self.settings(), before)
        with self.board_.store.db.read() as db:
            self.assertFalse(db.execute('SELECT enabled FROM evaluation_profiles WHERE profile_id=?', (PROFILE,)).fetchone()[0])
