"""ADR-027 rules 2 and 3 for explicit configuration admission.

A pending model stays acceptable for an explicitly specified buddy (C02); a
missing or unavailable route gets exactly one bounded re-read of that harness
before rejection, and the rejection names the catalog read time and the refresh
remedy (C05).
"""
from support import BoardTestCase, FakeClock
from hey_my_buddy.blackboard.catalog import catalog, catalog_store
from hey_my_buddy.errors import BoardError


def reading(models=('alpha',), efforts=('max',)):
    return {'source': 'fixture-native', 'discoveries': [{'adapter': 'dsh', 'status': 'complete', 'accountStatus': 'confirmed'}],
            'providers': [{'adapter': 'dsh', 'provider': 'fixture',
                           'models': [{'id': model, 'efforts': list(efforts), 'available': True} for model in models]}]}


IDENTITY = {"adapter": "dsh", "provider": "fixture", "model": "alpha", "effort": "max"}


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
