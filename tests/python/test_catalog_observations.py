"""Current native facts change without replacing user history or accepting late results."""
from support import BoardTestCase
from buddy import catalog_store


def catalog(adapter='dsh', models=('alpha',), status='complete'):
    return {'source': 'fixture-native', 'discoveries': [{'adapter': adapter, 'status': status, 'reason': 'fixture failure' if status == 'unknown' else None}], 'providers': ([{'adapter': adapter, 'provider': 'fixture', 'models': [{'id': model, 'efforts': ['max'], 'available': True} for model in models]}] if models else [])}


class CatalogObservationTests(BoardTestCase):
    def setUp(self):
        super().setUp()
        self.b = self.board()
        self.e = self.b.evaluation

    def record(self, payload, observation=None):
        return self.e.record_catalog(payload, observation)

    def test_a_b_a_restores_identity_and_preserves_human_intent(self):
        first = self.record(catalog())
        profile_id = 'dsh:fixture:alpha:max'
        with self.e.db.write() as db:
            db.execute('UPDATE evaluation_profiles SET enabled=1 WHERE profile_id=?', (profile_id,))
            db.execute("INSERT INTO family_annotations VALUES('dsh','fixture','alpha','human text',1,'now')")
            db.execute("INSERT INTO evaluation_preferences VALUES(?,'prefer','my reason',1)", (profile_id,))
        self.record(catalog(models=()))
        retired = catalog_store.profiles(self.e, {'includeUnavailable': True})
        self.assertFalse(retired['profiles'][0]['available'])
        self.assertTrue(retired['profiles'][0]['enabled'])
        last = self.record(catalog())
        self.assertTrue(last['duplicate'])
        self.assertGreater(last['observationId'], first['observationId'])
        current = self.e.snapshot({})
        self.assertTrue(current['profiles'][0]['available'])
        self.assertTrue(current['profiles'][0]['enabled'])
        self.assertEqual(current['familyAnnotations'][0]['text'], 'human text')
        self.assertEqual({key: current['familyAnnotations'][0][key] for key in ('adapter', 'provider', 'model')},
                         {'adapter': 'dsh', 'provider': 'fixture', 'model': 'alpha'})
        with self.e.db.read() as db:
            self.assertIsNotNone(self.e._catalog(db).lookup('dsh', 'fixture', 'alpha'))

    def test_partial_failure_preserves_last_known_without_affecting_other_harness(self):
        self.record(catalog())
        self.record(catalog(adapter='zcode', models=('beta',)))
        self.record(catalog(models=(), status='unknown'))
        with self.e.db.read() as db:
            view = self.e._catalog(db)
            self.assertIsNotNone(view.lookup('dsh', 'fixture', 'alpha'))
            self.assertIsNotNone(view.lookup('zcode', 'fixture', 'beta'))
            self.assertEqual(db.execute("SELECT status FROM catalog_current WHERE adapter='dsh'").fetchone()[0], 'unknown')
        self.record(catalog(models=()))
        with self.e.db.read() as db:
            self.assertIsNone(self.e._catalog(db).lookup('dsh', 'fixture', 'alpha'))
            self.assertIsNotNone(self.e._catalog(db).lookup('zcode', 'fixture', 'beta'))

    def test_late_complete_result_cannot_replace_newer_observation(self):
        slow = catalog_store.begin(self.e, 'slow')['observationId']
        fast = catalog_store.begin(self.e, 'fast')['observationId']
        self.record(catalog(models=('new',)), fast)
        result = self.record(catalog(models=('old',)), slow)
        self.assertEqual(result['staleAdapters'], ['dsh'])
        with self.e.db.read() as db:
            self.assertIsNotNone(self.e._catalog(db).lookup('dsh', 'fixture', 'new'))
            self.assertIsNone(self.e._catalog(db).lookup('dsh', 'fixture', 'old'))

    def test_retired_configuration_history_is_paginated_and_new_profiles_disabled(self):
        self.record(catalog(models=('a', 'b', 'c')))
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
        page = catalog_store.profiles(self.e, {'limit': 1, 'includeUnavailable': True, 'query': 'fixture c', 'adapter': 'dsh'})
        self.assertEqual([p['model'] for p in page['profiles']], ['c'])
        self.assertIsNone(page['nextCursor'])
        self.assertEqual(catalog_store.profiles(self.e, {'includeUnavailable': True, 'query': '%'})['profiles'], [])
