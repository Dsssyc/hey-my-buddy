"""Authenticated policy changes cannot be forged by a Host."""

from support import BoardTestCase
from hey_my_buddy.errors import BoardError

#: ``live`` and ``retired`` are each their own model family in this fixture.
LIVE_FAMILY = {'adapter': 'dsh', 'provider': 'fixture', 'model': 'live'}


class UserPolicyTests(BoardTestCase):
    def setUp(self):
        super().setUp()
        self.board_ = self.board()
        self.session = 'registered-user-session'
        self.board_.service.register_console_authority(self.session)
        with self.board_.store.db.write() as db:
            for profile_id, available in [('live', 1), ('retired', 0)]:
                db.execute("INSERT INTO evaluation_profiles(profile_id,label,adapter,provider,model,effort,available,enabled,capabilities_json) VALUES(?,?,?,?,?,?,?,?,?)", (profile_id, profile_id, 'dsh', 'fixture', profile_id, 'max', available, 1, '["decision"]'))
            db.execute("INSERT INTO router_profiles(position,profile_id) VALUES(0,'retired')")
            db.execute("INSERT INTO evaluation_preferences VALUES('retired','pin','user intent')")

    def user(self, operation, params):
        return self.board_.call(operation, {**params, 'consoleAuthority': self.session})

    def publication_params(self):
        return {}

    def payload(self, params, command='publish', **changes):
        return {**params, 'commandId': command, **changes}

    def assert_denied(self, operation, params, code='FORBIDDEN'):
        with self.assertRaises(BoardError) as caught:
            self.board_.call(operation, params)
        self.assertEqual(caught.exception.code, code)

    def test_host_cannot_publish_user_policy_or_replay_console_command(self):
        params = self.publication_params()
        payload = self.payload(params, familyAnnotationChanges=[{**LIVE_FAMILY, 'text': 'human opinion'}])
        self.assert_denied('user_policy_publish', payload)
        self.assertTrue(self.user('user_policy_publish', payload)['published'])
        self.assert_denied('user_policy_publish', payload)

    def test_retired_pin_and_selector_do_not_block_disable_or_other_edits(self):
        grant = self.publication_params()
        payload = self.payload(grant, profileSettings=[{'profileId': 'retired', 'enabled': False}], familyAnnotationChanges=[{**LIVE_FAMILY, 'text': 'prefer local evidence'}])
        self.user('user_policy_publish', payload)
        self.assertTrue(self.user('user_policy_publish', payload)['duplicate'])
        snapshot = self.board_.call('console_snapshot', {})
        self.assertEqual(snapshot['familyAnnotations'][0]['text'], 'prefer local evidence')
        self.assertEqual(snapshot['configuration']['routerProfileIds'], ['retired'])
        self.assertEqual(snapshot['preferences'][0]['mode'], 'pin')
        self.assertFalse(next(p for p in snapshot['profiles'] if p['profileId'] == 'retired')['enabled'])

    def test_reason_only_change_does_not_reenable_an_unavailable_pin(self):
        grant = self.publication_params()
        self.user('user_policy_publish', self.payload(grant, preferenceChanges=[{'profileId': 'retired', 'mode': 'pin', 'reason': 'Keep this intent while its provider is offline'}]))
        snapshot = self.board_.call('console_snapshot', {})
        self.assertEqual(snapshot['preferences'][0]['reason'], 'Keep this intent while its provider is offline')
        self.assertFalse(next(p for p in snapshot['profiles'] if p['profileId'] == 'retired')['available'])


    def test_invalid_patch_is_atomic_and_cannot_change_program_fields(self):
        grant = self.publication_params()
        for changes in [
            {'profileSettings': [{'profileId': 'live', 'enabled': False}, {'profileId': 'retired', 'enabled': True}]},
            {'profileSettings': [{'profileId': 'retired', 'enabled': False, 'available': True}]},
            {'familyAnnotationChanges': [{**LIVE_FAMILY, 'text': 'x'}, {**LIVE_FAMILY, 'text': 'y'}]},
        ]:
            with self.assertRaises(BoardError):
                self.user('user_policy_publish', self.payload(grant, **changes))
        snapshot = self.board_.call('console_snapshot', {})
        self.assertNotIn('tableRevision', snapshot)
        self.assertEqual(snapshot['familyAnnotations'], [])
        self.assertTrue(next(p for p in snapshot['profiles'] if p['profileId'] == 'live')['enabled'])


    def test_model_concurrency_is_a_console_only_human_patch(self):
        grant = self.publication_params()
        patch = [{'adapter': 'dsh', 'provider': 'fixture', 'model': 'live', 'limit': 3}]
        # The authenticated console writer may set family limits...
        result = self.user('user_policy_publish', self.payload(grant, modelConcurrency=patch))
        self.assertEqual(result['counts']['modelConcurrency'], 1)
        with self.board_.store.db.read() as db:
            stored = db.execute('SELECT concurrency_limit FROM model_concurrency').fetchall()
        self.assertEqual([row['concurrency_limit'] for row in stored], [3])
        self.assert_denied('user_policy_publish', self.payload(self.publication_params(), 'forged-limits', modelConcurrency=patch))

    def test_model_concurrency_rejects_derived_fields_and_bad_limits(self):
        grant = self.publication_params()
        for patch in (
            [{'adapter': 'dsh', 'provider': 'fixture', 'model': 'live', 'limit': 3, 'active': 1}],
            [{'adapter': 'dsh', 'provider': 'fixture', 'model': 'live', 'limit': 0}],
            [{'adapter': 'dsh', 'provider': 'fixture', 'model': 'live', 'limit': 33}],
            [{'adapter': 'dsh', 'provider': 'fixture', 'model': 'live', 'effort': 'max', 'limit': 3}],
            [{'adapter': 'dsh', 'provider': 'fixture', 'model': 'live', 'limit': 3},
             {'adapter': 'dsh', 'provider': 'fixture', 'model': 'live', 'limit': 4}],
        ):
            with self.assertRaises(BoardError) as caught:
                self.user('user_policy_publish', self.payload(grant, modelConcurrency=patch))
            self.assertEqual(caught.exception.code, 'INVALID_ARGUMENT')
        with self.board_.store.db.read() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) AS count FROM model_concurrency').fetchone()['count'], 0)
