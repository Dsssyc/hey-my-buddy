"""Current native facts change without replacing user history or accepting late results."""
from copy import deepcopy
import json
from unittest.mock import patch
from support import BoardTestCase, FakeClock
from hey_my_buddy.blackboard.catalog import catalog as catalog_rules, catalog_store


def catalog(adapter='dsh', models=('alpha',), status='complete', account_status=None):
    trusted = account_status if account_status is not None else ('unknown' if status == 'unknown' else 'confirmed')
    return {'source': 'fixture-native', 'discoveries': [{'adapter': adapter, 'status': status, 'accountStatus': trusted, 'reason': 'fixture failure' if status == 'unknown' else None}], 'providers': ([{'adapter': adapter, 'provider': 'fixture', 'models': [{'id': model, 'efforts': ['max'], 'available': True} for model in models]}] if models else [])}


class CatalogObservationTests(BoardTestCase):
    def setUp(self):
        super().setUp()
        self.clock = FakeClock()
        self.b = self.board(clock=self.clock)
        self.e = self.b.evaluation

    def record(self, payload, observation=None):
        return self.e.record_catalog(payload, observation)

    def table_facts(self):
        with self.e.db.read() as db:
            return (db.execute('SELECT COUNT(*) FROM events').fetchone()[0],
                    [tuple(row) for row in db.execute('SELECT * FROM evaluation_profiles ORDER BY profile_id')])

    def test_begin_freezes_harnesses_in_the_observation_on_first_request(self):
        first = catalog_store.begin(self.e, 'same-request', harnesses=[{'adapter': 'dsh', 'revision': 1}])
        repeated = catalog_store.begin(self.e, 'same-request', harnesses=[{'adapter': 'dsh', 'revision': 2}])
        self.assertEqual(first, repeated)
        with self.e.db.read() as db:
            row = db.execute('SELECT harnesses_json FROM catalog_observations WHERE observation_id=?',
                             (first['observationId'],)).fetchone()
            self.assertEqual(json.loads(row['harnesses_json']), first['harnesses'])

    def test_identical_read_records_observation_and_confirmed_time_without_event(self):
        first = self.record(catalog())
        facts = self.table_facts()
        with self.e.db.read() as db:
            profile = tuple(db.execute('SELECT * FROM evaluation_profiles').fetchone())
        self.clock.advance(21601)
        with patch.object(self.b.store, '_notify') as notify:
            second = self.record({**catalog(), 'discoveredAt': self.clock.value})
        self.assertEqual(self.table_facts(), facts)
        self.assertTrue(second['duplicate'])
        self.assertGreater(second['observationId'], first['observationId'])
        notify.assert_not_called()
        with self.e.db.read() as db:
            self.assertEqual(tuple(db.execute('SELECT * FROM evaluation_profiles').fetchone()), profile)
            current = db.execute("SELECT read_at,account_status,updated_at FROM catalog_current WHERE adapter='dsh'").fetchone()
            self.assertEqual((current['read_at'], current['account_status'], current['updated_at']),
                             (self.clock.value, 'confirmed', self.clock.value))
            self.assertEqual(db.execute('SELECT completed_at FROM catalog_observations WHERE observation_id=?',
                                        (second['observationId'],)).fetchone()[0], self.clock.value)

    def test_provenance_and_discovery_identity_alone_do_not_change_profile_facts(self):
        first = self.record(catalog())
        facts = self.table_facts()
        different = {**catalog(), 'source': 'another-native-reading', 'harnessVersion': 'next',
                     'warnings': ['observation detail'], 'quota': {'remaining': 42}, 'observedAt': 'now'}
        second = self.record(different)
        self.assertNotEqual(first['discoveryId'], second['discoveryId'])
        self.assertEqual(self.table_facts()[0], facts[0])
        with self.e.db.read() as db:
            row = db.execute('SELECT source FROM evaluation_profiles').fetchone()
        self.assertEqual(row['source'], 'catalog:' + second['discoveryId'])

    def test_applied_reading_keeps_current_profiles_and_pending_absence(self):
        payload = catalog(models=('alpha', 'beta'))
        self.record(payload)
        payload['providers'][0]['models'][0]['name'] = 'changed alpha'
        self.record(payload)
        missing = deepcopy(payload)
        missing['providers'][0]['models'].pop()
        self.record(missing)
        with self.e.db.read() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM evaluation_profiles').fetchone()[0], 2)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM catalog_pending').fetchone()[0], 1)

    def test_metadata_fields_update_profile_facts(self):
        payload = catalog()
        self.record(payload)
        for field, value in [('name', 'new label'), ('description', 'new description'),
                             ('contextWindow', 12345), ('inputModalities', ['text', 'image']),
                             ('available', False), ('unavailableReason', 'native unavailable'),
                             ('available', True)]:
            with self.subTest(field=field, value=value):
                before = self.table_facts()
                payload = deepcopy(payload)
                payload['providers'][0]['models'][0][field] = value
                response = self.record(payload)
                after = self.table_facts()
                self.assertNotIn('tableRevision', response)
                self.assertGreater(after[0], before[0])
                self.assertNotEqual(after[1], before[1])

    def test_addition_pending_confirmation_and_recovery_change_only_actual_facts(self):
        self.record(catalog())
        self.record(catalog(models=('alpha', 'beta')))
        self.record(catalog(models=('alpha',)))
        facts = self.table_facts()
        self.clock.advance(3599)
        self.record(catalog(models=('alpha',)))
        self.assertEqual(self.table_facts(), facts)
        self.clock.advance(2)
        confirmed = self.record(catalog(models=('alpha',)))
        self.assertGreater(self.table_facts()[0], facts[0])
        facts = self.table_facts()
        self.record(catalog(models=('alpha',)))
        self.assertEqual(self.table_facts(), facts)
        self.record(catalog(models=('alpha', 'beta')))
        with self.e.db.read() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM evaluation_profiles WHERE available=1').fetchone()[0], 2)

    def test_unknown_reading_keeps_profiles_unless_it_restores_retained_availability(self):
        self.record(catalog())
        with self.e.db.read() as db:
            read_at = db.execute("SELECT read_at FROM catalog_current WHERE adapter='dsh'").fetchone()[0]
        facts = self.table_facts()
        self.record(catalog(models=('untrusted',), account_status='unknown'))
        self.assertEqual(self.table_facts(), facts)
        with self.e.db.write() as db:
            db.execute("UPDATE evaluation_profiles SET available=0,unavailable_reason='HARNESS_UNHEALTHY'")
        restored = self.record(catalog(models=(), status='unknown'))
        self.assertGreater(self.table_facts()[0], facts[0])
        with self.e.db.read() as db:
            row = db.execute('SELECT available FROM evaluation_profiles').fetchone()
            current = db.execute("SELECT read_at,account_status FROM catalog_current WHERE adapter='dsh'").fetchone()
        self.assertEqual(row['available'], 1)
        self.assertEqual((current['read_at'], current['account_status']), (read_at, 'confirmed'))

    def test_first_trusted_read_grounds_a_retained_setting_missing_from_catalog(self):
        self.record(catalog(models=('alpha',)))
        with self.e.db.write() as db:
            db.execute('DELETE FROM catalog_current')
            db.execute("UPDATE evaluation_profiles SET enabled=1,unavailable_reason=NULL")
        self.record(catalog(models=('beta',)))
        with self.e.db.read() as db:
            missing = db.execute("SELECT available,enabled,unavailable_reason FROM evaluation_profiles WHERE model='alpha'").fetchone()
        self.assertEqual((missing['available'], missing['enabled'], missing['unavailable_reason']),
                         (0, 1, catalog_rules.CONFIRMED_ABSENCE_REASON))

    def test_effort_retirement_changes_profile_once(self):
        payload = catalog()
        payload['providers'][0]['models'][0]['efforts'] = ['max', 'high']
        self.record(payload)
        self.record(catalog())
        with self.e.db.read() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM evaluation_profiles WHERE available=0').fetchone()[0], 1)
        facts = self.table_facts()
        self.record(catalog())
        self.assertEqual(self.table_facts(), facts)

    def test_first_unknown_observation_and_stale_read_do_not_publish(self):
        facts = self.table_facts()
        self.record(catalog(status='unknown'))
        self.assertEqual(self.table_facts(), facts)
        slow = catalog_store.begin(self.e, 'revision-slow')['observationId']
        self.record(catalog())
        facts = self.table_facts()
        self.record(catalog(models=('too-late',)), slow)
        self.assertEqual(self.table_facts(), facts)

    def test_claude_first_party_reread_preserves_existing_buddy(self):
        from hey_my_buddy.buddy.harnesses.claude.native_run import _catalog

        existing = catalog(adapter='claude', models=('claude-fixture',))
        existing['providers'][0]['provider'] = 'anthropic'
        self.record(existing)
        profile_id = 'claude:anthropic:claude-fixture:max'
        with self.e.db.write() as db:
            db.execute('UPDATE evaluation_profiles SET enabled=1 WHERE profile_id=?', (profile_id,))
            db.execute("INSERT INTO evaluation_preferences VALUES(?,'prefer','keep this buddy')", (profile_id,))
        native = _catalog({'account': {'apiProvider': 'firstParty', 'email': 'subscriber@example.invalid',
                                      'organization': 'fixture-organization', 'subscriptionType': 'max'},
                           'models': [{'value': 'fixture', 'resolvedModel': 'claude-fixture',
                                       'supportedEffortLevels': ['max']}]}, 'fixture')
        self.assertEqual(native['providers'][0]['provider'], 'anthropic')
        self.assertEqual(native['discoveries'][0]['accountStatus'], 'confirmed')
        for _ in range(2):
            self.clock.advance(3601)
            self.record(native)
            with self.e.db.read() as db:
                rows = db.execute("SELECT profile_id,provider,enabled,available "
                                  "FROM evaluation_profiles WHERE adapter='claude'").fetchall()
                self.assertEqual([tuple(row) for row in rows],
                                 [(profile_id, 'anthropic', 1, 1)])
                self.assertEqual(tuple(db.execute('SELECT * FROM evaluation_preferences WHERE profile_id=?',
                                                  (profile_id,)).fetchone()),
                                 (profile_id, 'prefer', 'keep this buddy'))
                self.assertEqual(db.execute("SELECT COUNT(*) FROM catalog_pending WHERE adapter='claude'").fetchone()[0], 0)

    def test_a_ba_restores_identity_and_preserves_human_intent(self):
        first = self.record(catalog())
        profile_id = 'dsh:fixture:alpha:max'
        with self.e.db.write() as db:
            db.execute('UPDATE evaluation_profiles SET enabled=1 WHERE profile_id=?', (profile_id,))
            db.execute("INSERT INTO model_service_notes VALUES('dsh','fixture','alpha','human text','now')")
            db.execute("INSERT INTO evaluation_preferences VALUES(?,'prefer','my reason')", (profile_id,))
        # A first confirmed absence is pending, not a retirement: the profile
        # keeps its metadata, its routability and the user's intent (ADR-027).
        self.record(catalog(models=()))
        pending = catalog_store.profiles(self.e, {'includeUnavailable': True})
        self.assertTrue(pending['profiles'][0]['available'])
        self.assertEqual(pending['profiles'][0]['catalogStatus'], 'pending')
        self.assertTrue(pending['profiles'][0]['enabled'])
        self.clock.advance(3601)
        self.record(catalog(models=()))
        retired = catalog_store.profiles(self.e, {'includeUnavailable': True})
        self.assertFalse(retired['profiles'][0]['available'])
        self.assertTrue(retired['profiles'][0]['enabled'])
        last = self.record(catalog())
        self.assertTrue(last['duplicate'])
        self.assertGreater(last['observationId'], first['observationId'])
        current = self.e.snapshot({})
        self.assertTrue(current['profiles'][0]['available'])
        self.assertEqual(current['profiles'][0]['catalogStatus'], 'available')
        self.assertNotIn('pendingSince', current['profiles'][0])
        self.assertTrue(current['profiles'][0]['enabled'])
        self.assertEqual(current['modelServiceNotes'][0]['text'], 'human text')
        self.assertEqual({key: current['modelServiceNotes'][0][key] for key in ('adapter', 'provider', 'model')},
                         {'adapter': 'dsh', 'provider': 'fixture', 'model': 'alpha'})
        with self.e.db.read() as db:
            self.assertIsNotNone(catalog_store.current(db).lookup('dsh', 'fixture', 'alpha'))

    def test_partial_failure_preserves_last_known_without_affecting_other_harness(self):
        self.record(catalog())
        self.record(catalog(adapter='zcode', models=('beta',)))
        self.record(catalog(models=(), status='unknown'))
        with self.e.db.read() as db:
            view = catalog_store.current(db)
            self.assertIsNotNone(view.lookup('dsh', 'fixture', 'alpha'))
            self.assertIsNotNone(view.lookup('zcode', 'fixture', 'beta'))
            self.assertEqual(db.execute("SELECT status FROM catalog_current WHERE adapter='dsh'").fetchone()[0], 'unknown')
        self.record(catalog(models=()))
        with self.e.db.read() as db:
            view = catalog_store.current(db)
            self.assertIsNone(view.lookup('dsh', 'fixture', 'alpha'))
            self.assertIsNotNone(view.lookup('zcode', 'fixture', 'beta'))

    def test_late_complete_result_cannot_replace_newer_observation(self):
        slow = catalog_store.begin(self.e, 'slow')['observationId']
        fast = catalog_store.begin(self.e, 'fast')['observationId']
        self.record(catalog(models=('new',)), fast)
        result = self.record(catalog(models=('old',)), slow)
        self.assertEqual(result['staleAdapters'], ['dsh'])
        with self.e.db.read() as db:
            self.assertIsNotNone(catalog_store.current(db).lookup('dsh', 'fixture', 'new'))
            self.assertIsNone(catalog_store.current(db).lookup('dsh', 'fixture', 'old'))

    def test_retired_configuration_history_is_paginated_and_new_profiles_disabled(self):
        self.record(catalog(models=('a', 'b', 'c')))
        self.record(catalog(models=('c',)))
        self.clock.advance(3601)
        self.record(catalog(models=('c',)))
        page = catalog_store.profiles(self.e, {'limit': 1, 'includeUnavailable': True})
        self.assertEqual(len(page['profiles']), 1)
        next_page = catalog_store.profiles(self.e, {'limit': 1, 'includeUnavailable': True, 'after': page['nextCursor']})
        self.assertNotEqual(page['profiles'][0]['profileId'], next_page['profiles'][0]['profileId'])
        active = catalog_store.profiles(self.e, {})
        self.assertEqual([p['model'] for p in active['profiles']], ['c'])
        self.assertFalse(active['profiles'][0]['enabled'])

    def test_history_search_filters_before_paging_and_treats_wildcards_literally(self):
        self.record(catalog(models=('a', 'b', 'c')))
        self.record(catalog(models=()))
        self.clock.advance(3601)
        self.record(catalog(models=()))
        page = catalog_store.profiles(self.e, {'limit': 1, 'includeUnavailable': True, 'query': 'fixture c', 'adapter': 'dsh'})
        self.assertEqual([p['model'] for p in page['profiles']], ['c'])
        self.assertIsNone(page['nextCursor'])
        self.assertEqual(catalog_store.profiles(self.e, {'includeUnavailable': True, 'query': '%'})['profiles'], [])
