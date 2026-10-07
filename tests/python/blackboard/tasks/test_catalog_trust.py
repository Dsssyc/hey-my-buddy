"""ADR-027 rules 2 and 3 for explicit configuration admission.

A pending model stays acceptable for an explicitly specified buddy (C02); a
missing or unavailable route gets exactly one bounded re-read of that harness
before rejection, and the rejection names the catalog read time and the refresh
remedy (C05). Health availability and adopted catalog facts stay separate: a
health failure never masks or resurrects catalog state.
"""
from datetime import datetime, timedelta, timezone
import json
from unittest.mock import patch

from support import BoardTestCase, FakeClock
from hey_my_buddy.blackboard.catalog import accounts, catalog, catalog_store
from hey_my_buddy.blackboard.store.db import canonical_json
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
        return next(item for item in listed['profiles'] if item['profileId'] == 'dsh:fixture:alpha:max')

    def switch_catalog_account(self, *, source='native', credential_revision=1):
        # Only private, nonsecret selection metadata changes. No account flow,
        # environment provider, credential file or native discovery is used.
        if source == 'worker':
            self.enterContext(patch.object(accounts, 'capabilities', return_value={
                name: True for name in accounts.CAPABILITIES}))
        with self.board.store.db.write() as db:
            accounts._write(db, 'account-selection:dsh', {'source': source, 'revision': 1})
            accounts._write(db, 'account-credential:' + canonical_json(['dsh', source]), credential_revision)
            selected = accounts.selection(db, 'dsh')
            accounts._invalidate(db, 'dsh', selected)
            db.execute("UPDATE harness_health SET status='ready',expires_at=?,scan_after=? WHERE adapter='dsh'",
                       (_later(3600), _later(180)))
        self.assertTrue(self.board.service.harnesses.get('dsh')['available'])
        return selected

    def assert_old_account_model_is_excluded(self):
        profile = self.listed_profile()
        self.assertFalse(profile['available'], 'A healthy new account cannot adopt the old account model')
        self.assertEqual(profile['catalogStatus'], 'unavailable')
        self.assertNotIn('pendingSince', profile)
        self.assertTrue(profile['enabled'], 'Account invalidation preserves user intent')
        snapshot = self.evaluation.snapshot({})
        self.assertNotIn(profile['profileId'], [item['profileId'] for item in snapshot['profiles']],
                         'The current snapshot excludes the old account candidate')
        from hey_my_buddy.blackboard.routing.decision import DecisionCoordinator
        with self.board.store.db.read() as db:
            self.assertEqual(DecisionCoordinator._select_candidates(db, [], coding_only=True), [])
            self.assertEqual(catalog.pending_families(db), {})
        calls = []
        catalog.register_catalog_reread(self.directory, calls.append)
        with self.assertRaises(BoardError) as rejected:
            self.validate()
        self.assertEqual(rejected.exception.code, 'CONFIGURATION_UNAVAILABLE')
        self.assertEqual(calls, ['dsh'])

    def account_change_reading(self, *, source, account_status):
        self.evaluation.record_catalog(reading())
        with self.board.store.db.write() as db:
            db.execute("UPDATE evaluation_profiles SET enabled=1 WHERE adapter='dsh'")
        self.switch_catalog_account(source=source, credential_revision=1 if source == 'native' else 0)
        # Direct health restoration also obeys the catalog binding before any
        # new observation is published.
        with self.board.store.db.write() as db:
            self.assertEqual(catalog.restore_retained_availability(db, 'dsh'), 0)
        self.evaluation.record_catalog(reading(models=(), account_status=account_status))
        self.assert_old_account_model_is_excluded()
        with self.board.store.db.read() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM events WHERE kind LIKE 'catalog.model_%'").fetchone()[0], 0)
            self.assertEqual(catalog.catalog_read_at(db, 'dsh'),
                             self.clock.value if account_status == 'confirmed' else None)

    def test_credential_change_unknown_read_cannot_restore_old_account_model(self):
        self.account_change_reading(source='native', account_status='unknown')

    def test_credential_change_confirmed_empty_does_not_pend_old_account_model(self):
        self.account_change_reading(source='native', account_status='confirmed')

    def test_source_change_unknown_read_cannot_restore_old_account_model(self):
        self.account_change_reading(source='worker', account_status='unknown')

    def test_source_change_confirmed_empty_does_not_pend_old_account_model(self):
        self.account_change_reading(source='worker', account_status='confirmed')

    def new_account_reports_same_model(self, *, source):
        self.evaluation.record_catalog(reading())
        self.evaluation.record_catalog(reading(models=()))
        old_observation = catalog_store.begin(self.evaluation, 'old-account-slow')
        with self.board.store.db.write() as db:
            db.execute("UPDATE evaluation_profiles SET enabled=1 WHERE adapter='dsh'")
        self.clock.advance(7 * 3600)
        self.switch_catalog_account(source=source, credential_revision=1 if source == 'native' else 0)
        self.assert_old_account_model_is_excluded()
        self.evaluation.record_catalog(reading(models=(), account_status='unknown'))
        with self.board.store.db.read() as db:
            self.assertIsNone(catalog.catalog_read_at(db, 'dsh'))
            self.assertEqual(catalog.catalog_account_status(db, 'dsh'), 'unknown')
        slow = catalog_store.begin(self.evaluation, 'new-account-slow')
        fast = catalog_store.begin(self.evaluation, 'new-account-fast')
        payload = reading(models=('alpha', 'beta'), efforts=('high',))
        for model in payload['providers'][0]['models']:
            model.update(description='new account metadata', contextWindow=123456)
        result = self.evaluation.record_catalog(payload, fast['observationId'])
        duplicate = self.evaluation.record_catalog(payload, fast['observationId'])
        self.assertTrue(duplicate['duplicate'])
        self.assertEqual(duplicate['tableRevision'], result['tableRevision'])
        for observation in (old_observation, slow):
            stale = self.evaluation.record_catalog(reading(models=()), observation['observationId'])
            self.assertEqual(stale['staleAdapters'], ['dsh'])
            self.assertEqual(stale['appliedAdapters'], [])
        profiles = {item['profileId']: item for item in catalog_store.profiles(
            self.evaluation, {'includeUnavailable': True})['profiles']}
        self.assertFalse(profiles['dsh:fixture:alpha:max']['available'])
        self.assertTrue(profiles['dsh:fixture:alpha:max']['enabled'])
        alpha = profiles['dsh:fixture:alpha:high']
        self.assertTrue(alpha['available'])
        self.assertFalse(alpha['enabled'], 'A new effort still requires user enablement')
        self.assertEqual(alpha['description'], 'new account metadata')
        self.assertEqual(alpha['contextWindow'], 123456)
        self.assertEqual(alpha['catalogStatus'], 'available')
        self.assertEqual(self.validate({**IDENTITY, 'effort': 'high'}), {**IDENTITY, 'effort': 'high'})
        with self.assertRaises(BoardError) as rejected:
            self.validate()
        self.assertEqual(rejected.exception.code, 'INVALID_ARGUMENT')
        self.assertEqual(rejected.exception.details['legalEfforts'], ['high'])
        # Restore the original exact profile only when the new account reports
        # that effort. Its metadata changes; its ID and enabled intent survive.
        payload = reading(models=('alpha', 'beta'))
        payload['providers'][0]['models'][0].update(description='new exact profile', contextWindow=654321)
        self.evaluation.record_catalog(payload)
        alpha = self.listed_profile()
        self.assertEqual(alpha['profileId'], 'dsh:fixture:alpha:max')
        self.assertEqual(alpha['description'], 'new exact profile')
        self.assertEqual(alpha['contextWindow'], 654321)
        self.assertTrue(alpha['available'])
        self.assertTrue(alpha['enabled'])
        self.assertEqual(self.validate(), IDENTITY)
        from hey_my_buddy.blackboard.routing.decision import DecisionCoordinator
        with self.board.store.db.read() as db:
            self.assertEqual([row['profile_id'] for row in DecisionCoordinator._select_candidates(db, [], coding_only=True)],
                             ['dsh:fixture:alpha:max'])
            self.assertEqual(catalog.catalog_read_at(db, 'dsh'), self.clock.value)
        snapshot = next(item for item in self.evaluation.snapshot({})['profiles'] if item['profileId'] == alpha['profileId'])
        self.assertEqual(snapshot['description'], 'new exact profile')
        self.assertEqual(snapshot['catalogStatus'], 'available')
        self.assertNotIn('pendingSince', snapshot)
        # A later absence starts this account's own full one-hour window.
        self.evaluation.record_catalog(reading(models=('beta',)))
        first_absence = self.clock.value
        self.clock.advance(3599)
        self.evaluation.record_catalog(reading(models=('beta',)))
        self.assertTrue(self.listed_profile()['available'])
        self.clock.advance(2)
        self.evaluation.record_catalog(reading(models=('beta',)))
        self.assertFalse(self.listed_profile()['available'])
        self.evaluation.record_catalog(reading(models=('alpha', 'beta')))
        with self.board.store.db.read() as db:
            events = db.execute("SELECT kind,payload_json FROM events WHERE kind LIKE 'catalog.model_%' ORDER BY seq").fetchall()
        self.assertEqual([row['kind'] for row in events], ['catalog.model_pending', 'catalog.model_pending',
                         'catalog.model_unavailable', 'catalog.model_recovered'])
        self.assertEqual(json.loads(events[1]['payload_json'])['pendingSince'], first_absence)
        self.assertEqual(json.loads(events[2]['payload_json'])['pendingSince'], first_absence)

    def test_credential_change_same_model_uses_new_metadata_and_observation_fences(self):
        self.new_account_reports_same_model(source='native')

    def test_source_change_same_model_uses_new_metadata_and_observation_fences(self):
        self.new_account_reports_same_model(source='worker')

    def test_source_round_trip_without_trusted_reading_cannot_reuse_old_directory(self):
        self.evaluation.record_catalog(reading())
        with self.board.store.db.write() as db:
            db.execute("UPDATE evaluation_profiles SET enabled=1 WHERE adapter='dsh'")
        self.switch_catalog_account(source='worker', credential_revision=0)
        self.evaluation.record_catalog(reading(models=(), account_status='unknown'))
        self.switch_catalog_account(source='native', credential_revision=0)
        self.evaluation.record_catalog(reading(models=()))
        self.assert_old_account_model_is_excluded()

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

    def assert_only_high_is_usable(self, *, pending=False):
        listed = catalog_store.profiles(self.evaluation, {'includeUnavailable': True})['profiles']
        alpha = {item['effort']: item for item in listed if item['model'] == 'alpha'}
        self.assertEqual(sorted(effort for effort, item in alpha.items() if item['available']), ['high'])
        self.assertTrue(all(item['enabled'] for item in alpha.values()), 'User intent survives retirement and health noise')
        high = {**IDENTITY, 'effort': 'high'}
        self.assertEqual(self.validate(high), high)
        with self.assertRaises(BoardError) as rejected:
            self.validate({**IDENTITY, 'effort': 'low'})
        self.assertEqual(rejected.exception.code, 'INVALID_ARGUMENT')
        self.assertEqual(rejected.exception.details['legalEfforts'], ['high'])
        from hey_my_buddy.blackboard.routing.decision import DecisionCoordinator
        with self.board.store.db.read() as db:
            candidates = [row['profile_id'] for row in DecisionCoordinator._select_candidates(db, [], coding_only=True)]
        self.assertEqual(candidates, ['dsh:fixture:alpha:high'])
        if pending:
            self.assertEqual(alpha['high']['catalogStatus'], 'pending')
            self.assertEqual(alpha['high']['pendingSince'], self.clock.value)

    def masked_retired_effort(self):
        self.evaluation.record_catalog(reading(efforts=('high', 'low')))
        self.evaluation.record_catalog(reading(efforts=('high',)))
        # Before this fix old health code could overwrite the retirement reason
        # on every profile. The last adopted native reading still proves that
        # only high is legal, even when no row keeps the retirement reason.
        with self.board.store.db.write() as db:
            db.execute("UPDATE evaluation_profiles SET available=0,enabled=1,"
                       "unavailable_reason='HARNESS_HANDSHAKE_FAILED' WHERE adapter='dsh'")

    def test_health_mask_cannot_restore_a_retired_effort_on_unknown_read(self):
        self.masked_retired_effort()
        self.evaluation.record_catalog(reading(models=(), account_status='unknown'))
        self.assert_only_high_is_usable()

    def test_first_absence_and_pending_recovery_keep_only_last_legal_efforts(self):
        self.masked_retired_effort()
        self.evaluation.record_catalog(reading(models=()))
        self.assert_only_high_is_usable(pending=True)
        first_absence = self.clock.value
        self.fail_health_then_recover()
        self.evaluation.record_catalog(reading(models=(), account_status='unknown'))
        self.assert_only_high_is_usable(pending=True)
        self.clock.advance(3599)
        self.evaluation.record_catalog(reading(models=()))
        self.assert_only_high_is_usable()
        self.clock.advance(2)
        self.evaluation.record_catalog(reading(models=()))
        listed = catalog_store.profiles(self.evaluation, {'includeUnavailable': True})['profiles']
        self.assertTrue(all(not item['available'] for item in listed))
        with self.board.store.db.read() as db:
            events = db.execute("SELECT kind,payload_json FROM events WHERE kind LIKE 'catalog.model_%' ORDER BY seq").fetchall()
        import json
        self.assertEqual([row['kind'] for row in events], ['catalog.model_pending', 'catalog.model_unavailable'])
        self.assertEqual(json.loads(events[1]['payload_json'])['pendingSince'], first_absence)

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
