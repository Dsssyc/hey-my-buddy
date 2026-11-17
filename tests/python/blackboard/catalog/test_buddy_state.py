"""The catalog projection composes independent facts without rewriting them."""
from itertools import product
import json

from support import BoardTestCase
from blackboard.catalog.test_catalog_trust import reading
from hey_my_buddy.blackboard.catalog import buddy_state, catalog_store


class BuddyStateTests(BoardTestCase):
    def setUp(self):
        super().setUp()
        self.b = self.board()
        self.e = self.b.evaluation
        self.e.record_catalog(reading(efforts=('high', 'max')))

    def test_all_health_model_and_effort_combinations_preserve_reason_order(self):
        for healthy, pending, absent, retired, native in product((False, True), repeat=5):
            with self.subTest(healthy=healthy, pending=pending, absent=absent, retired=retired, native=native):
                with self.e.db.write() as db:
                    db.execute("UPDATE harness_health SET status=?,record_json=? WHERE harness='dsh'",
                               ('ready' if healthy else 'unhealthy', json.dumps({'reason': {
                                   'code': 'harness.probe-failed', 'params': {'operation': 'probe'}}})))
                    db.execute('UPDATE model_services SET pending_since=?,confirmed_absent_at=?,unavailable_code=?,unavailable_params_json=?,unavailable_content=?',
                               ('2026-01-01T00:00:00Z' if pending else None,
                                '2026-01-01T01:00:01Z' if absent else None,
                                'catalog.native-unavailable' if native else None,
                                '{}' if native else None, 'native restriction' if native else None))
                    db.execute("UPDATE buddies SET retired=? WHERE effort='max'", (int(retired),))
                with self.e.db.read() as db:
                    actual = buddy_state.get(db, 'dsh:fixture:alpha:max')
                expected = ('catalog.effort-retired' if retired else
                            'catalog.model-absent' if absent else
                            'catalog.native-unavailable' if native else
                            'harness.probe-failed' if not healthy else None)
                self.assertEqual(actual['unavailable_code'], expected)
                self.assertEqual(bool(actual['available']), expected is None)
                self.assertEqual(bool(actual['catalog_available']), not (retired or absent or native))
                self.assertEqual(actual['unavailable_content'], 'native restriction' if native and not (retired or absent) else None)
                self.assertEqual(json.loads(actual['capabilities_json']), ['execution:dsh', 'effort:max', 'input:text'])

    def test_unhealthy_harness_without_a_reason_fails_even_with_a_catalog_cause(self):
        from unittest.mock import patch
        for cause in (None, {}):
            for retired in (0, 1):
                with self.subTest(cause=cause, retired=retired):
                    with self.e.db.write() as db:
                        db.execute('UPDATE buddies SET retired=?', (retired,))
                    with self.e.db.read() as db, patch(
                            'hey_my_buddy.blackboard.service.harness_health.read_health',
                            return_value={'status': 'unhealthy', 'reason': cause}):
                        with self.assertRaisesRegex(AssertionError, 'must have a reason'):
                            buddy_state.get(db, 'dsh:fixture:alpha:max')

    def test_earlier_read_cannot_publish_after_a_later_read_started(self):
        slow = catalog_store.begin(self.e)['sequence']
        later = catalog_store.begin(self.e)['sequence']
        stale = self.e.record_catalog(reading(models=('old',)), slow)
        self.assertEqual(stale['staleHarnesses'], ['dsh'])
        with self.e.db.read() as db:
            self.assertEqual([row['model'] for row in buddy_state.rows(db)], ['alpha', 'alpha'])
        self.e.record_catalog(reading(models=('new',)), later)
        with self.e.db.read() as db:
            self.assertIsNotNone(catalog_store.current(db).lookup('dsh', 'fixture', 'new'))
            self.assertIsNone(catalog_store.current(db).lookup('dsh', 'fixture', 'old'))
