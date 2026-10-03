"""Authenticated policy changes cannot be forged by a maintenance Host."""
import json

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
                db.execute("INSERT INTO evaluation_profiles(profile_id,label,adapter,provider,model,effort,available,enabled,capabilities_json,created_revision,updated_revision) VALUES(?,?,?,?,?,?,?,?,?,?,?)", (profile_id, profile_id, 'dsh', 'fixture', profile_id, 'max', available, 1, '["decision"]', 0, 0))
            db.execute("UPDATE meta SET value=? WHERE key='router_profile_ids'", (json.dumps(['retired']),))
            db.execute("INSERT INTO evaluation_preferences VALUES('retired','pin','user intent',0)")

    def user(self, operation, params):
        return self.board_.call(operation, {**params, 'consoleAuthority': self.session})

    def grant(self, kind='human', request='writer'):
        call = self.user if kind == 'human' else self.board_.call
        revision = self.board_.call('console_snapshot', {})['tableRevision']
        return call('evaluation_write_begin', {'requestId': request, 'expectedRevision': revision, 'kind': kind})

    def payload(self, grant, command='publish', **changes):
        return {k: grant[k] for k in ('writerId', 'generation', 'writerToken')} | {'expectedRevision': grant['tableRevision'], 'commandId': command} | changes

    def assert_denied(self, operation, params, code='FORBIDDEN'):
        with self.assertRaises(BoardError) as caught:
            self.board_.call(operation, params)
        self.assertEqual(caught.exception.code, code)

    def test_host_cannot_claim_human_or_borrow_human_grant(self):
        self.assert_denied('evaluation_write_begin', {'requestId': 'forged', 'expectedRevision': 0, 'kind': 'human'})
        grant = self.grant()
        credentials = {k: grant[k] for k in ('writerId', 'generation', 'writerToken')}
        self.assert_denied('evaluation_write_renew', credentials)
        self.assert_denied('evaluation_write_abort', {**credentials, 'commandId': 'forged-abort'})
        self.assert_denied('user_policy_publish', self.payload(grant, familyAnnotationChanges=[{**LIVE_FAMILY, 'text': 'forged'}]))
        result = self.user('user_policy_publish', self.payload(grant, familyAnnotationChanges=[{**LIVE_FAMILY, 'text': 'human opinion'}]))
        self.assertTrue(result['published'])
        self.assert_denied('user_policy_publish', self.payload(grant, familyAnnotationChanges=[{**LIVE_FAMILY, 'text': 'human opinion'}]))

    def test_retired_pin_and_selector_do_not_block_disable_or_other_edits(self):
        grant = self.grant()
        payload = self.payload(grant, profileSettings=[{'profileId': 'retired', 'enabled': False}], familyAnnotationChanges=[{**LIVE_FAMILY, 'text': 'prefer local evidence'}])
        self.user('user_policy_publish', payload)
        self.assertTrue(self.user('user_policy_publish', payload)['duplicate'])
        snapshot = self.board_.call('console_snapshot', {})
        self.assertEqual(snapshot['familyAnnotations'][0]['text'], 'prefer local evidence')
        self.assertEqual(snapshot['configuration']['routerProfileIds'], ['retired'])
        self.assertEqual(snapshot['preferences'][0]['mode'], 'pin')
        self.assertFalse(next(p for p in snapshot['profiles'] if p['profileId'] == 'retired')['enabled'])

    def test_reason_only_change_does_not_reenable_an_unavailable_pin(self):
        grant = self.grant()
        self.user('user_policy_publish', self.payload(grant, preferenceChanges=[{'profileId': 'retired', 'mode': 'pin', 'reason': 'Keep this intent while its provider is offline'}]))
        snapshot = self.board_.call('console_snapshot', {})
        self.assertEqual(snapshot['preferences'][0]['reason'], 'Keep this intent while its provider is offline')
        self.assertFalse(next(p for p in snapshot['profiles'] if p['profileId'] == 'retired')['available'])

    def test_maintenance_cannot_write_user_fields_and_preserves_them(self):
        human = self.grant()
        self.user('user_policy_publish', self.payload(human, familyAnnotationChanges=[{**LIVE_FAMILY, 'text': 'my assessment'}]))
        grant = self.grant('maintenance', 'maintainer')
        self.assert_denied('assessment_publish', self.payload(grant, familyAnnotationChanges=[]), 'INVALID_ARGUMENT')
        card = {'profileId': 'live', 'summary': 'No verified observations yet.', 'strengths': [], 'limitations': [], 'risks': [], 'evidenceIds': []}
        self.board_.call('assessment_publish', self.payload(grant, 'maintenance-publish', cards=[card]))
        snapshot = self.board_.call('console_snapshot', {})
        self.assertEqual(snapshot['familyAnnotations'][0]['text'], 'my assessment')
        self.assertEqual(snapshot['cards'][0]['summary'], card['summary'])
        self.assertEqual(snapshot['cards'][0]['origin'], 'maintenance')

    def test_invalid_patch_is_atomic_and_cannot_change_program_fields(self):
        grant = self.grant()
        for changes in [
            {'profileSettings': [{'profileId': 'live', 'enabled': False}, {'profileId': 'retired', 'enabled': True}]},
            {'profileSettings': [{'profileId': 'retired', 'enabled': False, 'available': True}]},
            {'familyAnnotationChanges': [{**LIVE_FAMILY, 'text': 'x'}, {**LIVE_FAMILY, 'text': 'y'}]},
        ]:
            with self.assertRaises(BoardError):
                self.user('user_policy_publish', self.payload(grant, **changes))
        snapshot = self.board_.call('console_snapshot', {})
        self.assertEqual(snapshot['tableRevision'], 0)
        self.assertEqual(snapshot['familyAnnotations'], [])
        self.assertTrue(next(p for p in snapshot['profiles'] if p['profileId'] == 'live')['enabled'])

    def test_maintenance_packet_keeps_human_annotation_attributed_and_archive_targetable(self):
        grant = self.grant()
        self.user('user_policy_publish', self.payload(grant, familyAnnotationChanges=[{**LIVE_FAMILY, 'text': 'human experience'}]))
        packet = self.board_.call('evaluation_prepare', {'requestId': 'prepare-live', 'limit': 1})
        self.assertEqual([p['profileId'] for p in packet['profiles']], ['live'])
        self.assertEqual(packet['annotations'][0]['model'], 'live')
        self.assertEqual(packet['annotations'][0]['text'], 'human experience')
        archived = self.board_.call('evaluation_prepare', {'requestId': 'prepare-retired', 'profileId': 'retired', 'limit': 1})
        self.assertEqual([p['profileId'] for p in archived['profiles']], ['retired'])

    def test_model_concurrency_is_a_console_only_human_patch(self):
        grant = self.grant()
        patch = [{'adapter': 'dsh', 'provider': 'fixture', 'model': 'live', 'limit': 3}]
        # The authenticated console writer may set family limits...
        result = self.user('user_policy_publish', self.payload(grant, modelConcurrency=patch))
        self.assertEqual(result['counts']['modelConcurrency'], 1)
        with self.board_.store.db.read() as db:
            stored = db.execute('SELECT concurrency_limit FROM model_concurrency').fetchall()
        self.assertEqual([row['concurrency_limit'] for row in stored], [3])
        # ...and a maintenance writer can neither patch nor keep the field.
        maintenance = self.grant('maintenance', 'limits-maintainer')
        self.assert_denied('assessment_publish', self.payload(maintenance, modelConcurrency=patch), 'INVALID_ARGUMENT')

    def test_model_concurrency_rejects_derived_fields_and_bad_limits(self):
        grant = self.grant()
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
