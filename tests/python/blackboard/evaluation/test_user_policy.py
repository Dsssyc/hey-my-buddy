"""Authenticated policy changes cannot be forged by a Host."""

from support import BoardTestCase
from hey_my_buddy.errors import BoardError
from hey_my_buddy.protocol import schemas

#: ``live`` and ``retired`` are each their own model service in this fixture.
LIVE_MODEL_SERVICE = {'harness': 'dsh', 'provider': 'fixture', 'model': 'live'}


class UserPolicyTests(BoardTestCase):
    def setUp(self):
        super().setUp()
        self.board_ = self.board()
        self.session = 'registered-user-session'
        self.board_.service.register_console_authority(self.session)
        with self.board_.store.db.write() as db:
            for buddy_id, available in [('live', 1), ('retired', 0)]:
                db.execute("INSERT INTO buddies(buddy_id,label,harness,provider,model,effort,available,enabled,capabilities_json) VALUES(?,?,?,?,?,?,?,?,?)", (buddy_id, buddy_id, 'dsh', 'fixture', buddy_id, 'max', available, 1, '["decision"]'))
            db.execute("INSERT INTO router_buddies(position,buddy_id) VALUES(0,'retired')")
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
        payload = self.payload(params, modelServiceNoteChanges=[{**LIVE_MODEL_SERVICE, 'text': 'human opinion'}])
        self.assert_denied('user_policy_publish', payload)
        self.assertTrue(self.user('user_policy_publish', payload)['published'])
        self.assert_denied('user_policy_publish', payload)

    def test_buddy_settings_patch_accepts_more_than_200_distinct_buddies(self):
        with self.board_.store.db.write() as db:
            db.executemany(
                "INSERT INTO buddies(buddy_id,label,harness,provider,model,effort,available,enabled,capabilities_json)"
                " VALUES(?,?,?,?,?,?,?,?,?)",
                [(f'bulk-{index}', f'bulk-{index}', 'dsh', 'fixture', f'model-{index}', 'max', 1, 1, '[]')
                 for index in range(205)])
        changes = [{'buddyId': f'bulk-{index}', 'enabled': False} for index in range(205)]
        published = self.user('user_policy_publish', self.payload({}, 'bulk-disable', buddySettings=changes))
        self.assertEqual(published['counts']['buddySettings'], 205)

    def test_retired_pin_and_selector_do_not_block_disable_or_other_edits(self):
        grant = self.publication_params()
        payload = self.payload(grant, buddySettings=[{'buddyId': 'retired', 'enabled': False}], modelServiceNoteChanges=[{**LIVE_MODEL_SERVICE, 'text': 'prefer local evidence'}])
        self.user('user_policy_publish', payload)
        self.assertTrue(self.user('user_policy_publish', payload)['duplicate'])
        snapshot = self.board_.call('console_snapshot', {})
        self.assertEqual(snapshot['modelServiceNotes'][0]['text'], 'prefer local evidence')
        self.assertEqual(snapshot['configuration']['routerBuddyIds'], ['retired'])
        self.assertEqual(snapshot['preferences'][0]['mode'], 'pin')
        self.assertFalse(next(p for p in snapshot['buddies'] if p['buddyId'] == 'retired')['enabled'])

    def test_note_only_change_does_not_reenable_an_unavailable_pin(self):
        grant = self.publication_params()
        self.user('user_policy_publish', self.payload(grant, preferenceChanges=[{'buddyId': 'retired', 'mode': 'pin', 'note': 'Keep this intent while its provider is offline'}]))
        snapshot = self.board_.call('console_snapshot', {})
        self.assertEqual(snapshot['preferences'][0]['note'], 'Keep this intent while its provider is offline')
        self.assertFalse(next(p for p in snapshot['buddies'] if p['buddyId'] == 'retired')['available'])


    def test_preference_notes_round_trip_null_empty_and_utf8_limit(self):
        for index, (note, expected) in enumerate(((None, None), ('', None),
                                                ('备注' * (schemas.MAX_NOTE_BYTES // 6),
                                                 '备注' * (schemas.MAX_NOTE_BYTES // 6)))):
            with self.subTest(note=note):
                self.user('user_policy_publish', self.payload({}, f'note-{index}',
                          preferenceChanges=[{'buddyId': 'retired', 'mode': 'pin', 'note': note}]))
                self.assertEqual(self.board_.call('console_snapshot', {})['preferences'],
                                 [{'buddyId': 'retired', 'mode': 'pin', 'note': expected}])
                with self.board_.store.db.read() as db:
                    self.assertEqual(db.execute('SELECT note FROM evaluation_preferences').fetchone()[0], expected)
        self.user('user_policy_publish', self.payload({}, 'omitted-note',
                  preferenceChanges=[{'buddyId': 'retired', 'mode': 'pin'}]))
        self.assertEqual(self.board_.call('console_snapshot', {})['preferences'],
                         [{'buddyId': 'retired', 'mode': 'pin', 'note': None}])

    def test_invalid_preference_notes_roll_back_the_publication(self):
        for index, field in enumerate(({'note': '汉' * (schemas.MAX_NOTE_BYTES // 3 + 1)},
                                       {'note': 1}, {'note': False}, {'note': []},
                                       {'reason': 'retired field'})):
            with self.subTest(field=field), self.assertRaises(BoardError) as caught:
                self.user('user_policy_publish', self.payload({}, f'invalid-note-{index}',
                          buddySettings=[{'buddyId': 'live', 'enabled': False}],
                          preferenceChanges=[{'buddyId': 'retired', 'mode': 'pin', **field}]))
            self.assertEqual(caught.exception.code, 'INVALID_ARGUMENT')
            snapshot = self.board_.call('console_snapshot', {})
            self.assertEqual(snapshot['preferences'],
                             [{'buddyId': 'retired', 'mode': 'pin', 'note': 'user intent'}])
            self.assertTrue(next(b for b in snapshot['buddies'] if b['buddyId'] == 'live')['enabled'])

    def test_invalid_patch_is_atomic_and_cannot_change_program_fields(self):
        grant = self.publication_params()
        for changes in [
            {'buddySettings': [{'buddyId': 'live', 'enabled': False}, {'buddyId': 'retired', 'enabled': True}]},
            {'buddySettings': [{'buddyId': 'retired', 'enabled': False, 'available': True}]},
            {'modelServiceNoteChanges': [{**LIVE_MODEL_SERVICE, 'text': 'x'}, {**LIVE_MODEL_SERVICE, 'text': 'y'}]},
        ]:
            with self.assertRaises(BoardError):
                self.user('user_policy_publish', self.payload(grant, **changes))
        snapshot = self.board_.call('console_snapshot', {})
        self.assertNotIn('tableRevision', snapshot)
        self.assertEqual(snapshot['modelServiceNotes'], [])
        self.assertTrue(next(p for p in snapshot['buddies'] if p['buddyId'] == 'live')['enabled'])


    def test_model_service_concurrency_is_a_console_only_human_patch(self):
        grant = self.publication_params()
        patch = [{'harness': 'dsh', 'provider': 'fixture', 'model': 'live', 'limit': 3}]
        # The authenticated console writer may set model service limits...
        result = self.user('user_policy_publish', self.payload(grant, modelServiceConcurrency=patch))
        self.assertEqual(result['counts']['modelServiceConcurrency'], 1)
        with self.board_.store.db.read() as db:
            stored = db.execute('SELECT concurrency_limit FROM model_service_concurrency').fetchall()
        self.assertEqual([row['concurrency_limit'] for row in stored], [3])
        self.assert_denied('user_policy_publish', self.payload(self.publication_params(), 'forged-limits', modelServiceConcurrency=patch))

    def test_model_service_concurrency_rejects_derived_fields_and_bad_limits(self):
        grant = self.publication_params()
        for patch in (
            [{'harness': 'dsh', 'provider': 'fixture', 'model': 'live', 'limit': 3, 'active': 1}],
            [{'harness': 'dsh', 'provider': 'fixture', 'model': 'live', 'limit': 0}],
            [{'harness': 'dsh', 'provider': 'fixture', 'model': 'live', 'limit': 33}],
            [{'harness': 'dsh', 'provider': 'fixture', 'model': 'live', 'effort': 'max', 'limit': 3}],
            [{'harness': 'dsh', 'provider': 'fixture', 'model': 'live', 'limit': 3},
             {'harness': 'dsh', 'provider': 'fixture', 'model': 'live', 'limit': 4}],
        ):
            with self.assertRaises(BoardError) as caught:
                self.user('user_policy_publish', self.payload(grant, modelServiceConcurrency=patch))
            self.assertEqual(caught.exception.code, 'INVALID_ARGUMENT')
        with self.board_.store.db.read() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) AS count FROM model_service_concurrency').fetchone()['count'], 0)
