import uuid
"""Human settings patches and upgrade-required reads on private boards."""
from unittest.mock import patch
from hey_my_buddy.errors import BoardError
from support import BoardTestCase

BUDDY = 'dsh:deepseek-official:deepseek-flash:off'


class SingleRouterConfigurationTests(BoardTestCase):
    def setUp(self):
        super().setUp()
        self.catalog_fixture()
        self.board_ = self.board()
        self.board_.call('model_catalog_refresh', {'requestId': 'configuration-catalog'})

    def configure(self, settings, *, enable=False):
        params = {}
        params.update(commandId=f"publish-{uuid.uuid4()}", configuration=settings)
        if enable:
            params['buddySettings'] = [{'buddyId': BUDDY, 'enabled': True}]
        return self.board_.console_call('user_policy_publish', params)

    def settings(self):
        return self.board_.call('console_snapshot', {})['configuration']

    def test_fresh_defaults_have_an_empty_router_list(self):
        snapshot = self.board_.call('console_snapshot', {})
        settings = snapshot['configuration']
        self.assertEqual(settings['routerBuddyIds'], [])
        self.assertEqual(settings['defaultRoutingMode'], 'fast')
        self.assertEqual(settings['routingBudget'], 'standard')
        self.assertNotIn('fastRouterBuddyId', settings)
        self.assertNotIn('reviewRouterBuddyId', settings)
        self.assertIsNone(snapshot['configurationError'])

    def test_budget_patch_and_clear_preserve_other_settings(self):
        self.configure({'routerBuddyIds': [BUDDY]}, enable=True)
        self.configure({'routingBudget': 'brief'})
        self.assertEqual(self.settings()['routerBuddyIds'], [BUDDY])
        self.configure({'routerBuddyIds': []})
        self.assertEqual(self.settings()['routerBuddyIds'], [])
        self.assertEqual(self.settings()['routingBudget'], 'brief')

    def test_mode_patch_preserves_temporarily_ineligible_router_and_reports_qualification(self):
        self.configure({'routerBuddyIds': [BUDDY]}, enable=True)
        with patch.dict('hey_my_buddy.protocol.capabilities.PERMISSION_LEVELS', {'dsh': {'none': 'enforced', 'write': 'enforced', 'full': 'enforced'}}):
            self.configure({'defaultRoutingMode': 'review'})
            from hey_my_buddy.blackboard.routing import router
            with self.board_.store.db.read() as db:
                resolution = router.current_router(db)
            self.assertIsNone(resolution.buddy)
            self.assertEqual(resolution.inspections[0]['code'], 'router-review-unsupported')
        self.assertEqual(self.settings()['defaultRoutingMode'], 'review')
        self.assertEqual(self.settings()['routerBuddyIds'], [BUDDY])

    def test_unavailable_existing_router_does_not_block_budget_or_clear(self):
        self.configure({'routerBuddyIds': [BUDDY]}, enable=True)
        with self.board_.store.db.write() as db:
            db.execute('UPDATE buddies SET available=0 WHERE buddy_id=?', (BUDDY,))
        self.configure({'routingBudget': 'deep'})
        self.assertEqual(self.settings()['routerBuddyIds'], [BUDDY])
        self.configure({'routerBuddyIds': [], 'defaultRoutingMode': 'review'})
        self.assertEqual(self.settings()['routerBuddyIds'], [])
        self.assertEqual(self.settings()['defaultRoutingMode'], 'review')

    def test_old_slot_patch_is_refused_atomically(self):
        before = self.settings()
        with self.assertRaises(BoardError) as caught:
            self.configure({'fastRouterBuddyId': BUDDY}, enable=True)
        self.assertEqual(caught.exception.code, 'INVALID_ARGUMENT')
        self.assertEqual(self.settings(), before)
        with self.board_.store.db.read() as db:
            self.assertFalse(db.execute('SELECT enabled FROM buddies WHERE buddy_id=?', (BUDDY,)).fetchone()[0])
