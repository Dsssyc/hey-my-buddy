"""ADR-027 rules 2 and 3 for explicit configuration admission.

A pending model stays acceptable for an explicitly specified buddy (C02); a
missing or unavailable route gets exactly one bounded re-read of that harness
before rejection, and the rejection names the catalog read time and the refresh
remedy (C05). Health availability and adopted catalog facts stay separate: a
health failure never masks or resurrects catalog state.
"""
from datetime import datetime, timedelta, timezone

from support import BoardTestCase, FakeClock
from hey_my_buddy.blackboard.catalog import catalog, catalog_store
from hey_my_buddy.errors import BoardError


def reading(models=('alpha',), efforts=('max',), account_status='confirmed'):
    return {'source': 'fixture-native', 'discoveries': [{'adapter': 'dsh', 'status': 'complete', 'accountStatus': account_status}],
            'providers': [{'adapter': 'dsh', 'provider': 'fixture',
                           'models': [{'id': model, 'efforts': list(efforts), 'available': True} for model in models]}]}


IDENTITY = {"adapter": "dsh", "provider": "fixture", "model": "alpha", "effort": "max"}


def _later(seconds: int) -> str:
    moment = datetime.now(timezone.utc) + timedelta(seconds=seconds)
    return moment.isoformat(timespec='milliseconds').replace('+00:00', 'Z')


class ExplicitCatalogTrustTests(BoardTestCase):
    def setUp(self):
        super().setUp()
        self.clock = FakeClock()
        self.board = self.board(clock=self.clock)
        self.evaluation = self.board.evaluation
        self.directory = self.board.store.directory
        # The service hook registered by this board is the stubbed in-process
        # refresh; tests that need a real re-read replace it and restore it.
        self.service_hook = catalog._catalog_reread.get(str(self.directory))
        self.addCleanup(lambda: catalog.register_catalog_reread(self.directory, self.service_hook))

    def validate(self, identity=None):
        return catalog.validate_configuration(identity or IDENTITY, directory=self.directory)

    def fail_health_then_recover(self, reason='HARNESS_HANDSHAKE_FAILED'):
        """A harness health failure, then recovery published like a real check."""
        harnesses = self.board.service.harnesses
        harnesses.invalidate('dsh', harnesses.get('dsh')['revision'], reason)
        with self.board.store.db.write() as db:
            db.execute("UPDATE harness_health SET status='ready',expires_at=?,scan_after=? WHERE adapter='dsh'",
                       (_later(3600), _later(180)))

    def listed_profile(self):
        listed = catalog_store.profiles(self.evaluation, {'includeUnavailable': True})
        return next(item for item in listed['profiles'] if item['model'] == 'alpha')

    def test_c02_pending_model_is_still_accepted_explicitly(self):
        self.evaluation.record_catalog(reading())
        self.evaluation.record_catalog(reading(models=()))
        self.assertEqual(self.validate(), IDENTITY)
        with self.assertRaises(BoardError) as rejected:
            self.validate({**IDENTITY, "effort": "high"})
        self.assertEqual(rejected.exception.code, "INVALID_ARGUMENT")
        self.assertEqual(rejected.exception.details["legalEfforts"], ["max"])

    def test_c02_confirmed_unavailable_route_is_rejected_after_the_window(self):
        self.evaluation.record_catalog(reading())
        self.evaluation.record_catalog(reading(models=()))
        self.clock.advance(3601)
        self.evaluation.record_catalog(reading(models=()))
        catalog.register_catalog_reread(self.directory, None)
        with self.assertRaises(BoardError) as rejected:
            self.validate()
        self.assertEqual(rejected.exception.code, "CONFIGURATION_UNAVAILABLE")

    def test_c05_one_bounded_reread_then_a_rejection_that_names_the_facts(self):
        self.evaluation.record_catalog(reading())
        calls = []

        def reread(name):
            calls.append(name)

        catalog.register_catalog_reread(self.directory, reread)
        with self.assertRaises(BoardError) as rejected:
            self.validate({**IDENTITY, "model": "beta"})
        error = rejected.exception
        self.assertEqual(error.code, "CONFIGURATION_UNAVAILABLE")
        self.assertEqual(calls, ["dsh"], "Exactly one bounded re-read of exactly that harness")
        self.assertEqual(error.details["catalogReadAt"], self.clock.value)
        self.assertEqual(error.details["remedy"], catalog.CATALOG_REMEDY)
        self.assertIn("adapters", error.details["remedy"])

    def test_c05_a_successful_reread_admits_the_route_without_rejection(self):
        self.evaluation.record_catalog(reading())
        calls = []

        def reread(name):
            calls.append(name)
            self.evaluation.record_catalog(reading(models=('alpha', 'beta')))

        catalog.register_catalog_reread(self.directory, reread)
        identity = {**IDENTITY, "model": "beta"}
        self.assertEqual(self.validate(identity), identity)
        self.assertEqual(calls, ["dsh"])

    def test_c05_a_failing_reread_still_rejects_with_the_catalog_facts(self):
        self.evaluation.record_catalog(reading())
        read_at = self.clock.value
        self.clock.advance(60)

        def reread(name):
            raise BoardError("ADAPTER_UNAVAILABLE", "fixture refresh failed")

        catalog.register_catalog_reread(self.directory, reread)
        with self.assertRaises(BoardError) as rejected:
            self.validate({**IDENTITY, "model": "beta"})
        self.assertEqual(rejected.exception.code, "CONFIGURATION_UNAVAILABLE")
        self.assertEqual(rejected.exception.details["catalogReadAt"], read_at)

    def test_c05_a_reread_that_breaks_health_keeps_the_rejection_context(self):
        # The bounded re-read's native failure path invalidates the harness; the
        # fresh health diagnostics lead, but the catalog read time and the
        # refresh method of rule 3 must survive this boundary too.
        self.evaluation.record_catalog(reading())
        read_at = self.clock.value
        harnesses = self.board.service.harnesses

        def reread(name):
            record = harnesses.get(name)
            harnesses.invalidate(name, record["revision"], "HARNESS_HANDSHAKE_FAILED")

        catalog.register_catalog_reread(self.directory, reread)
        with self.assertRaises(BoardError) as rejected:
            self.validate({**IDENTITY, "model": "beta"})
        error = rejected.exception
        self.assertEqual(error.code, "ADAPTER_UNAVAILABLE")
        self.assertEqual(error.details["catalogReadAt"], read_at)
        self.assertEqual(error.details["remedy"], catalog.CATALOG_REMEDY)
        self.assertEqual(error.details["harness"]["reasonCode"], "HARNESS_HANDSHAKE_FAILED")

    def test_cold_start_without_any_recorded_catalog_is_catalog_unavailable(self):
        catalog.register_catalog_reread(self.directory, None)
        with self.assertRaises(BoardError) as rejected:
            self.validate()
        self.assertEqual(rejected.exception.code, "CATALOG_UNAVAILABLE")

    def test_service_refresh_populates_the_catalog_for_a_cold_board(self):
        calls = []

        def reread(name):
            calls.append(name)
            self.evaluation.record_catalog(reading(models=('alpha', 'beta')))

        catalog.register_catalog_reread(self.directory, reread)
        identity = {**IDENTITY, "model": "beta"}
        self.assertEqual(self.validate(identity), identity)
        self.assertEqual(calls, ["dsh"])

    def test_snapshot_and_model_profiles_expose_catalog_status_fields(self):
        self.evaluation.record_catalog(reading())
        self.evaluation.record_catalog(reading(models=()))
        snapshot = self.evaluation.snapshot({})
        profile = next(item for item in snapshot["profiles"] if item["profileId"] == "dsh:fixture:alpha:max")
        self.assertEqual(profile["catalogStatus"], "pending")
        self.assertEqual(profile["pendingSince"], self.clock.value)
        listed = catalog_store.profiles(self.evaluation, {"includeUnavailable": True})
        page = next(item for item in listed["profiles"] if item["profileId"] == "dsh:fixture:alpha:max")
        self.assertEqual(page["catalogStatus"], "pending")
        self.assertEqual(page["pendingSince"], self.clock.value)

    def test_unknown_reread_after_health_recovery_keeps_the_retained_catalog(self):
        self.evaluation.record_catalog(reading())
        self.fail_health_then_recover()
        unknown = reading(models=(), account_status='unknown')
        unknown['providers'] = []
        self.evaluation.record_catalog(unknown)
        profile = self.listed_profile()
        self.assertTrue(profile['available'], 'Health 0s must not degrade the retained catalog')
        self.assertEqual(profile['catalogStatus'], 'available')
        self.assertEqual(profile['catalogState'], 'unknown')
        self.assertEqual(self.validate(), IDENTITY, 'The retained catalog still admits the buddy')

    def test_confirmed_empty_after_health_recovery_pends_the_known_model(self):
        self.evaluation.record_catalog(reading())
        with self.board.store.db.write() as db:
            db.execute("UPDATE evaluation_profiles SET enabled=1 WHERE adapter='dsh'")
        self.fail_health_then_recover()
        self.evaluation.record_catalog(reading(models=()))
        profile = self.listed_profile()
        self.assertEqual(profile['catalogStatus'], 'pending')
        self.assertTrue(profile['available'])
        self.assertEqual(profile['pendingSince'], self.clock.value)
        from hey_my_buddy.blackboard.routing.decision import DecisionCoordinator
        with self.board.store.db.read() as db:
            candidates = [row['profile_id'] for row in DecisionCoordinator._select_candidates(db, [], coding_only=True)]
        self.assertEqual(candidates, ['dsh:fixture:alpha:max'], 'A pending model stays inside the bounds')
        self.assertEqual(self.validate(), IDENTITY, 'A pending model stays acceptable explicitly')

    def test_pending_survives_health_failure_and_true_unavailability_is_never_restored(self):
        self.evaluation.record_catalog(reading())
        self.evaluation.record_catalog(reading(models=()))
        first_absence = self.clock.value
        # Health fails inside the window, recovers, and the re-read stays unknown:
        # the window, its first absence and the model state all survive.
        self.fail_health_then_recover()
        unknown = reading(models=(), account_status='unknown')
        unknown['providers'] = []
        self.evaluation.record_catalog(unknown)
        profile = self.listed_profile()
        self.assertEqual(profile['catalogStatus'], 'pending')
        self.assertEqual(profile['pendingSince'], first_absence)
        self.assertTrue(profile['available'])
        self.assertEqual(self.validate(), IDENTITY)
        # A confirmed absence after the window is a catalog fact: later health
        # noise and unknown re-reads must never resurrect it.
        self.clock.advance(3601)
        self.evaluation.record_catalog(reading(models=()))
        self.fail_health_then_recover()
        self.evaluation.record_catalog(unknown)
        profile = self.listed_profile()
        self.assertEqual(profile['catalogStatus'], 'unavailable')
        self.assertFalse(profile['available'])
        catalog.register_catalog_reread(self.directory, None)
        with self.assertRaises(BoardError) as rejected:
            self.validate()
        self.assertEqual(rejected.exception.code, "CONFIGURATION_UNAVAILABLE")

    def strip_confirmed_read_meta(self):
        """A board that adopted catalogs before the ADR-027 keys existed."""
        with self.board.store.db.write() as db:
            db.execute("DELETE FROM meta WHERE key IN ('catalog-read-at:dsh','catalog-account-status:dsh')")

    def test_legacy_board_reports_and_keeps_its_old_trusted_read_time(self):
        self.evaluation.record_catalog(reading())
        self.strip_confirmed_read_meta()
        catalog.register_catalog_reread(self.directory, None)
        with self.assertRaises(BoardError) as rejected:
            self.validate({**IDENTITY, "model": "beta"})
        read_at = rejected.exception.details["catalogReadAt"]
        self.assertEqual(read_at, self.clock.value, 'The legacy catalog record still proves its read time')
        # Seven hours later an unknown empty reading is not a successful read:
        # the old trusted time stands and never gets pushed back.
        self.clock.advance(7 * 3600)
        unknown = reading(models=(), account_status='unknown')
        unknown['providers'] = []
        self.evaluation.record_catalog(unknown)
        with self.board.store.db.read() as db:
            self.assertEqual(catalog.catalog_read_at(db, 'dsh'), read_at)
        with self.assertRaises(BoardError) as rejected:
            self.validate({**IDENTITY, "model": "beta"})
        self.assertEqual(rejected.exception.details["catalogReadAt"], read_at)

    def test_account_switch_still_clears_the_legacy_read_time(self):
        self.evaluation.record_catalog(reading())
        self.strip_confirmed_read_meta()
        with self.board.store.db.read() as db:
            self.assertIsNotNone(catalog.catalog_read_at(db, 'dsh'))
        with self.board.store.db.write() as db:
            from hey_my_buddy.blackboard.catalog import accounts
            db.execute("INSERT INTO meta(key,value) VALUES('account-selection:dsh',"
                       "'{\"adapter\":\"dsh\",\"source\":\"worker\",\"credentialRevision\":0}')")
            accounts._invalidate(db, 'dsh', {'adapter': 'dsh', 'source': 'worker', 'credentialRevision': 0})
        with self.board.store.db.read() as db:
            self.assertIsNone(catalog.catalog_read_at(db, 'dsh'),
                              'A switched binding never inherits the old account read time')
