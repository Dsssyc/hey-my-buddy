"""ADR-027 blackboard rules: trusted readings, disappearance confirmation, events.

Covers C01, C03, C04 and C09 at the catalog layer: an account-unknown reading
replaces nothing; a first confirmed absence is pending, unavailable only after a
further confirmed absence past the one-hour window; reappearance recovers; each
transition is one event and idempotent or unknown readings invent none.
"""
import json
import sqlite3
from types import SimpleNamespace
from unittest.mock import patch

from support import BoardTestCase, FakeClock
from hey_my_buddy.blackboard.catalog import catalog, catalog_store


def reading(adapter='dsh', models=('alpha',), status='complete', account_status=None, efforts=('max',)):
    trusted = account_status if account_status is not None else ('unknown' if status == 'unknown' else 'confirmed')
    return {'source': 'fixture-native', 'discoveries': [{'adapter': adapter, 'status': status, 'accountStatus': trusted}],
            'providers': [{'adapter': adapter, 'provider': 'fixture',
                           'models': [{'id': model, 'efforts': list(efforts), 'available': True} for model in models]}]}


class CatalogTrustTests(BoardTestCase):
    def setUp(self):
        super().setUp()
        self.clock = FakeClock()
        self.board = self.board(clock=self.clock)
        self.evaluation = self.board.evaluation

    def record(self, payload, observation=None):
        return self.evaluation.record_catalog(payload, observation)

    def events(self, kind_prefix='catalog.model_'):
        with self.board.store.db.read() as db:
            rows = db.execute("SELECT kind, payload_json FROM events WHERE kind LIKE ? ORDER BY seq", (kind_prefix + '%',)).fetchall()
        return [(row['kind'], json.loads(row['payload_json'])) for row in rows]

    def state(self, adapter='dsh'):
        with self.board.store.db.read() as db:
            row = db.execute('SELECT status, reason, discovery_id FROM catalog_current WHERE adapter=?', (adapter,)).fetchone()
            pending = catalog.pending_model_services(db)
            read_at = catalog.catalog_read_at(db, adapter)
            profiles = db.execute('SELECT profile_id, available, unavailable_reason FROM evaluation_profiles WHERE adapter=?', (adapter,)).fetchall()
        return row, pending, read_at, profiles

    def test_unknown_discovery_retains_catalog_without_confirming_model_absence(self):
        confirmed = self.record(reading(models=('alpha', 'beta')))
        before = self.state()
        payload = reading(models=('alpha', 'untrusted'), status='unknown', account_status='confirmed')
        payload['discoveries'][0]['reason'] = 'native model shape unreadable'
        instance = SimpleNamespace(model_discovery=True, discovery_available=lambda: (True, None),
                                   discover_models=lambda: payload)
        for elapsed in (1, catalog.CONFIRMATION_SECONDS + 1):
            self.clock.advance(elapsed)
            with patch('hey_my_buddy.buddy.harnesses.registry.adapters', return_value={'dsh': instance}), \
                 patch.object(catalog, '_override', return_value=None):
                discovered = catalog.discover(directory=self.board.directory)
            result = catalog_store.record(self.evaluation, discovered)
            row, pending, read_at, profiles = self.state()
            self.assertEqual(row['status'], 'unknown')
            self.assertEqual(row['reason'], payload['discoveries'][0]['reason'])
            self.assertEqual(row['discovery_id'], confirmed['discoveryId'])
            self.assertEqual(read_at, before[2])
            self.assertEqual([tuple(profile) for profile in profiles], [tuple(profile) for profile in before[3]])
            self.assertEqual(pending, {})
            self.assertEqual(self.events(), [])
            self.assertNotIn('tableRevision', result)

    def test_pending_owned_table_persists_the_first_absence_and_efforts_without_meta(self):
        self.record(reading(efforts=('high', 'max')))
        self.record(reading(models=()))
        with self.board.store.db.read() as db:
            first = tuple(db.execute('SELECT adapter,provider,model,since,efforts_json FROM catalog_pending').fetchone())
            self.assertEqual(first[:3], ('dsh', 'fixture', 'alpha'))
            self.assertEqual(json.loads(first[4]), ['high', 'max'])
            self.assertIsNone(db.execute("SELECT key FROM meta WHERE key LIKE 'catalog-pending:%'").fetchone())
        self.clock.advance(60)
        self.record(reading(models=()))
        with self.board.store.db.read() as db:
            self.assertEqual(tuple(db.execute('SELECT adapter,provider,model,since,efforts_json FROM catalog_pending').fetchone()), first)
        self.record(reading(efforts=('high', 'max')))
        with self.board.store.db.read() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM catalog_pending').fetchone()[0], 0)

    def test_pending_identity_and_current_directory_foreign_key_are_enforced(self):
        self.record(reading())
        self.record(reading(models=()))
        for adapter in ('dsh', 'missing'):
            with self.subTest(adapter=adapter), self.assertRaises(sqlite3.IntegrityError):
                with self.board.store.db.write() as db:
                    db.execute("INSERT INTO catalog_pending(adapter,provider,model,since,efforts_json)"
                               " VALUES(?,'fixture','alpha','other','[]')", (adapter,))
        self.assertEqual(len(self.state()[1]), 1)

    def test_pending_replacement_rolls_back_with_the_publisher_transaction(self):
        self.record(reading())
        self.record(reading(models=()))
        before = self.state()[1]
        before_events = self.events()
        with self.assertRaises(AssertionError):
            with self.board.store.db.write() as db:
                catalog._write_pending_map(db, 'dsh', {catalog._model_service_key('fixture', 'beta'):
                                                      {'since':'later','efforts':['max']}})
                raise AssertionError('private publisher rollback')
        self.assertEqual(self.state()[1], before)
        self.assertEqual(self.events(), before_events)

    def test_failed_pending_insert_rolls_back_the_whole_catalog_publication(self):
        def rows(db):
            names = [row[0] for row in db.execute(
                "SELECT name FROM sqlite_schema WHERE type='table' ORDER BY name"
            )]
            return {name: [tuple(row) for row in db.execute(f'SELECT * FROM "{name}" ORDER BY rowid')]
                    for name in names}

        self.record(reading())
        observation = catalog_store.begin(self.evaluation)['observationId']
        with self.board.store.db.write() as db:
            db.execute("CREATE TRIGGER refuse_pending BEFORE INSERT ON catalog_pending BEGIN"
                       " SELECT RAISE(ABORT,'private pending failure'); END")
        with self.board.store.db.read() as db:
            before = rows(db)
        with self.assertRaisesRegex(sqlite3.IntegrityError, 'private pending failure'):
            self.record(reading(models=()), observation)
        with self.board.store.db.read() as db:
            self.assertEqual(rows(db), before)

    def test_c01_unknown_account_reading_replaces_nothing(self):
        self.record(reading())
        trusted_at = self.clock.value
        before = self.state()
        self.assertEqual(before[0]['status'], 'complete')
        self.assertEqual(before[2], trusted_at)
        self.clock.advance(60)
        # A complete reading whose account fact is unknown (or absent) is stored
        # as an unknown observation: the recorded catalog, its read time and the
        # model states all stay with the previous confirmed reading.
        for account_status in ('unknown', None):
            with self.subTest(account_status=account_status):
                payload = reading(models=('beta',), account_status=account_status or 'unknown')
                if account_status is None:
                    del payload['discoveries'][0]['accountStatus']
                self.record(payload)
        row, pending, read_at, profiles = self.state()
        self.assertEqual(row['status'], 'unknown')
        self.assertEqual(row['reason'], 'ACCOUNT_STATUS_UNKNOWN')
        self.assertIsNotNone(row['discovery_id'], 'The previously confirmed payload is retained')
        self.assertEqual(read_at, trusted_at, 'An unknown reading is not a successful read')
        self.assertEqual([p['profile_id'] for p in profiles], ['dsh:fixture:alpha:max'])
        self.assertTrue(profiles[0]['available'])
        with self.board.store.db.read() as db:
            view = catalog_store.current(db)
            self.assertIsNotNone(view.lookup('dsh', 'fixture', 'alpha'))
            self.assertIsNone(view.lookup('dsh', 'fixture', 'beta'))
        self.assertEqual(self.events(), [])

        # C06 companion: the shelf clock only advances on confirmed readings.
        self.clock.advance(7200)
        self.record(reading(models=('beta',), account_status='unknown'))
        self.assertEqual(self.state()[2], read_at)

    def test_c01_cold_start_never_trusts_a_reading_without_the_account_fact(self):
        result = self.record(reading(account_status='unknown'))
        self.assertEqual(result['appliedAdapters'], ['dsh'])
        row, pending, read_at, profiles = self.state()
        self.assertEqual(row['status'], 'unknown')
        self.assertIsNone(row['discovery_id'])
        self.assertIsNone(read_at)
        self.assertEqual(profiles, [])
        with self.board.store.db.read() as db:
            self.assertIsNone(catalog.pending_model_efforts(db, 'dsh', 'fixture', 'alpha'))


    def test_c03_first_absence_is_pending_before_the_window_and_unavailable_after(self):
        self.record(reading())
        self.record(reading(models=()))
        row, pending, read_at, profiles = self.state()
        self.assertEqual(list(pending), [('dsh', 'fixture', 'alpha')])
        first_absence = pending[('dsh', 'fixture', 'alpha')]
        self.assertTrue(profiles[0]['available'], 'A pending model keeps its routable state')
        self.clock.advance(3599)
        self.record(reading(models=()))
        _, pending, _, profiles = self.state()
        self.assertEqual(pending[('dsh', 'fixture', 'alpha')], first_absence, 'The first absence time never moves')
        self.assertTrue(profiles[0]['available'])
        self.clock.advance(2)
        self.record(reading(models=()))
        row, pending, _, profiles = self.state()
        self.assertEqual(pending, {})
        self.assertFalse(profiles[0]['available'])
        self.assertEqual(profiles[0]['unavailable_reason'], catalog.CONFIRMED_ABSENCE_REASON)
        kinds = [kind for kind, _ in self.events()]
        self.assertEqual(kinds, ['catalog.model_pending', 'catalog.model_unavailable'])

    def test_c03_an_unknown_reading_does_not_advance_the_confirmation_window(self):
        self.record(reading())
        self.record(reading(models=()))
        # Two hours of unknown readings must not restart the absence clock.
        self.clock.advance(7200)
        self.record(reading(models=('alpha',), account_status='unknown'))
        self.clock.advance(1)
        self.record(reading(models=()))
        row, pending, _, profiles = self.state()
        self.assertEqual(pending, {})
        self.assertFalse(profiles[0]['available'], 'The window still counts from the first confirmed absence')

    def test_c04_reappearance_recovers_and_clears_the_first_absence(self):
        self.record(reading())
        self.record(reading(models=()))
        self.clock.advance(3601)
        self.record(reading(models=()))
        self.assertFalse(self.state()[3][0]['available'])
        self.clock.advance(60)
        self.record(reading())
        row, pending, _, profiles = self.state()
        self.assertEqual(pending, {})
        self.assertTrue(profiles[0]['available'])
        self.assertIsNone(profiles[0]['unavailable_reason'])
        events = self.events()
        self.assertEqual([kind for kind, _ in events], ['catalog.model_pending', 'catalog.model_unavailable', 'catalog.model_recovered'])
        self.assertEqual(events[1][1]['pendingSince'], events[0][1]['pendingSince'], 'The confirmation names the first absence')

    def test_c04_recovery_inside_the_window_keeps_the_model_usable(self):
        self.record(reading())
        self.record(reading(models=()))
        self.clock.advance(30)
        self.record(reading())
        _, pending, _, profiles = self.state()
        self.assertEqual(pending, {})
        self.assertTrue(profiles[0]['available'])
        events = self.events()
        self.assertEqual([kind for kind, _ in events], ['catalog.model_pending', 'catalog.model_recovered'])
        self.assertEqual(events[1][1]['pendingSince'], events[0][1]['pendingSince'])

    def test_c09_transitions_are_per_model_and_never_repeated(self):
        self.record(reading(models=('alpha', 'beta')))
        self.record(reading(models=()))
        self.record(reading(models=()))
        self.record(reading(models=(), account_status='unknown'))
        kinds = [kind for kind, _ in self.events()]
        self.assertEqual(kinds, ['catalog.model_pending', 'catalog.model_pending'], 'Repeated and unknown readings invent no transitions')
        self.assertEqual({payload['model'] for _, payload in self.events()}, {'alpha', 'beta'})
        # A duplicate request replays its recorded response without new events.
        observation = catalog_store.begin(self.evaluation, 'replay')
        first = self.record(reading(models=('alpha',)), observation['observationId'])
        second = self.record(reading(models=('alpha',)), observation['observationId'])
        self.assertTrue(second['duplicate'])
        self.assertEqual([kind for kind, _ in self.events()][-1], 'catalog.model_recovered')

    def test_effort_only_change_is_not_a_model_disappearance(self):
        self.record(reading(models=('alpha',), efforts=('max', 'high')))
        self.record(reading(models=('alpha',), efforts=('max',)))
        _, pending, _, profiles = self.state()
        self.assertEqual(pending, {}, 'An effort-only change never opens a window')
        by_id = {p['profile_id']: p for p in profiles}
        self.assertTrue(by_id['dsh:fixture:alpha:max']['available'])
        self.assertFalse(by_id['dsh:fixture:alpha:high']['available'])
        self.assertEqual(by_id['dsh:fixture:alpha:high']['unavailable_reason'], 'not present in the latest complete native discovery')
        self.assertEqual(self.events(), [])

    def test_out_of_order_observations_cannot_rewrite_the_pending_state(self):
        self.record(reading())
        slow = catalog_store.begin(self.evaluation, 'slow')['observationId']
        self.clock.advance(60)
        fast = catalog_store.begin(self.evaluation, 'fast')['observationId']
        self.record(reading(models=()), fast)
        result = self.record(reading(models=('alpha',)), slow)
        self.assertEqual(result['staleAdapters'], ['dsh'])
        _, pending, _, _ = self.state()
        self.assertEqual(list(pending), [('dsh', 'fixture', 'alpha')], 'The late observation changed nothing')


    def test_empty_confirmed_catalog_for_one_harness_leaves_others_alone(self):
        self.record(reading())
        self.record(reading(adapter='zcode', models=('beta',)))
        empty = reading(models=())
        empty['providers'] = []
        self.record(empty)
        _, pending, _, _ = self.state()
        self.assertEqual(list(pending), [('dsh', 'fixture', 'alpha')])
        with self.board.store.db.read() as db:
            self.assertIsNone(catalog.pending_model_efforts(db, 'zcode', 'fixture', 'beta'))

    def test_pending_efforts_remain_the_last_confirmed_model_facts(self):
        self.record(reading(efforts=('high', 'max')))
        self.record(reading(models=()))
        with self.board.store.db.read() as db:
            stock = [tuple(row) for row in db.execute('SELECT * FROM catalog_pending')]
            self.assertEqual(catalog.pending_model_efforts(db, 'dsh', 'fixture', 'alpha'), ['high', 'max'])
        self.record(reading(models=(), account_status='unknown'))
        with self.board.store.db.read() as db:
            self.assertEqual([tuple(row) for row in db.execute('SELECT * FROM catalog_pending')], stock)
            self.assertEqual(catalog.pending_model_efforts(db, 'dsh', 'fixture', 'alpha'), ['high', 'max'])
