"""ADR-027 rules 2 and 3 for explicit configuration admission.

A pending model stays acceptable for an explicitly specified buddy (C02); a
missing or unavailable route gets exactly one bounded re-read of that harness
before rejection, and the rejection names the catalog read time and the refresh
remedy (C05). Health availability and adopted catalog facts stay separate: a
health failure never masks or resurrects catalog state.
"""
from datetime import datetime, timedelta, timezone
import json
import threading
import time
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

    def switch_catalog_account(self, *, source='native', credential_revision=1, revision=1):
        # Only private, nonsecret selection metadata changes. No account flow,
        # environment provider, credential file or native discovery is used.
        # The revision mirrors Accounts.set's selection epoch: every real flip
        # bumps it, so an A→B→A round trip never reuses the departed epoch.
        if source == 'worker':
            self.enterContext(patch.object(accounts, 'capabilities', return_value={
                name: True for name in accounts.CAPABILITIES}))
        with self.board.store.db.write() as db:
            accounts._write(db, 'account-selection:dsh', {'source': source, 'revision': revision})
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
        catalog.register_catalog_reread(self.directory, lambda name, binding: calls.append(name))
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

    def test_account_round_trip_never_resurrects_the_old_accounts_read_facts(self):
        # The identity check alone hides the old facts only while the binding
        # differs; clearing them at the switch is what stops them from coming
        # back when the original binding itself returns.
        self.evaluation.record_catalog(reading())
        self.evaluation.record_catalog(reading(models=()))
        self.switch_catalog_account(source='worker', credential_revision=0)
        self.switch_catalog_account(source='native', credential_revision=0)
        with self.board.store.db.read() as db:
            self.assertIsNone(catalog.catalog_read_at(db, 'dsh'),
                              'Returning to the old identity never resurrects its read time')
            self.assertEqual(catalog.catalog_account_status(db, 'dsh'), 'unknown',
                             'The old confirmed account fact stays cleared')
            self.assertEqual(catalog.pending_families(db), {},
                             'The old account disappearance window never returns')
            self.assertIsNone(catalog.pending_model_efforts(db, 'dsh', 'fixture', 'alpha'))

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

    def scan_marker(self):
        with self.board.store.db.read() as db:
            row = db.execute("SELECT value FROM meta WHERE key='catalog-scan-after:dsh'").fetchone()
        return row[0] if row else None

    def expire_reread_window(self):
        with self.board.store.db.write() as db:
            db.execute("DELETE FROM meta WHERE key='catalog-scan-after:dsh'")

    def reject_missing(self, identity=None):
        with self.assertRaises(BoardError) as rejected:
            self.validate(identity or {**IDENTITY, "model": "beta"})
        return rejected.exception

    def test_c05_one_bounded_reread_then_a_rejection_that_names_the_facts(self):
        self.evaluation.record_catalog(reading())
        calls = []

        def reread(name, binding):
            calls.append(name)

        catalog.register_catalog_reread(self.directory, reread)
        error = self.reject_missing()
        self.assertEqual(error.code, "CONFIGURATION_UNAVAILABLE")
        self.assertEqual(calls, ["dsh"], "Exactly one bounded re-read of exactly that harness")
        self.assertEqual(error.details["catalogReadAt"], self.clock.value)
        self.assertEqual(error.details["remedy"], catalog.CATALOG_REMEDY)
        self.assertEqual(error.details["reason"], "catalog-unavailable")
        self.assertIn("adapters", error.details["remedy"])

    def test_c05_a_successful_reread_admits_the_route_without_rejection(self):
        self.evaluation.record_catalog(reading())
        calls = []

        def reread(name, binding):
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

        def reread(name, binding):
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

        def reread(name, binding):
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

    def test_c05_reread_is_limited_to_one_native_read_per_scan_window(self):
        from hey_my_buddy.blackboard.service.harness_health import SCAN_SECONDS

        self.evaluation.record_catalog(reading())
        calls = []
        catalog.register_catalog_reread(self.directory, lambda name, binding: calls.append(name))
        error = self.reject_missing()
        self.assertEqual(calls, ["dsh"], "The first rejection in a fresh window reads once")
        window = catalog._parse_time(self.scan_marker())
        remaining = (window - datetime.now(timezone.utc)).total_seconds()
        self.assertGreater(remaining, SCAN_SECONDS - 30, "The claim reuses the health scan window length")
        self.assertLessEqual(remaining, SCAN_SECONDS)
        for _ in range(3):
            again = self.reject_missing()
            self.assertEqual(again.details["reason"], "catalog-unavailable")
            self.assertEqual(again.details["remedy"], catalog.CATALOG_REMEDY)
            self.assertEqual(again.details["catalogReadAt"], self.clock.value)
        self.assertEqual(calls, ["dsh"], "Repeat submissions inside the window start no native read")
        self.expire_reread_window()
        self.reject_missing()
        self.assertEqual(calls, ["dsh", "dsh"], "After the window a rejection may read again")

    def test_c05_a_window_noted_by_the_health_scan_bounds_validation_too(self):
        from hey_my_buddy.blackboard.service.harness_health import _later

        self.evaluation.record_catalog(reading())
        with self.board.store.db.write() as db:
            db.execute("INSERT INTO meta(key,value) VALUES('catalog-scan-after:dsh',?)"
                       " ON CONFLICT(key) DO UPDATE SET value=excluded.value", (_later(60),))
        calls = []
        catalog.register_catalog_reread(self.directory, lambda name, binding: calls.append(name))
        self.reject_missing()
        self.assertEqual(calls, [], "The health scan's open window bounds the validation re-read")

    def test_c05_a_failed_reread_consumes_the_window(self):
        self.evaluation.record_catalog(reading())
        calls = []

        def reread(name, binding):
            calls.append(name)
            raise BoardError("CATALOG_UNAVAILABLE", "fixture native read failed")

        catalog.register_catalog_reread(self.directory, reread)
        first = self.reject_missing()
        self.assertEqual(first.code, "CONFIGURATION_UNAVAILABLE")
        self.assertIsNotNone(self.scan_marker(), "The failed read still claims the window")
        again = self.reject_missing()
        self.assertEqual(again.code, "CONFIGURATION_UNAVAILABLE")
        self.assertEqual(again.details["catalogReadAt"], first.details["catalogReadAt"])
        self.assertEqual(calls, ["dsh"], "The failed native read is not retried inside the window")

    def test_c05_an_unknown_reread_consumes_the_window_and_keeps_the_facts(self):
        self.evaluation.record_catalog(reading())
        read_at = self.clock.value
        calls = []

        def reread(name, binding):
            calls.append(name)
            unknown = reading(models=(), account_status='unknown')
            unknown['providers'] = []
            self.evaluation.record_catalog(unknown)

        catalog.register_catalog_reread(self.directory, reread)
        first = self.reject_missing()
        self.assertEqual(first.code, "CONFIGURATION_UNAVAILABLE")
        self.assertEqual(first.details["catalogReadAt"], read_at, "An unknown reading is not a successful read")
        again = self.reject_missing()
        self.assertEqual(again.details["catalogReadAt"], read_at)
        self.assertEqual(calls, ["dsh"], "The unknown reading's window is not re-entered")

    def test_c05_concurrent_validations_share_one_bounded_reread(self):
        self.evaluation.record_catalog(reading())
        started, release, calls = threading.Event(), threading.Event(), []

        def reread(name, binding):
            calls.append(name)
            started.set()
            self.assertTrue(release.wait(timeout=10), "The single-flight test lost its reader")

        catalog.register_catalog_reread(self.directory, reread)
        outcomes = []

        def submit_missing():
            try:
                self.validate({**IDENTITY, "model": "beta"})
                outcomes.append("admitted")
            except BoardError as error:
                outcomes.append(error.code)

        threads = [threading.Thread(target=submit_missing) for _ in range(4)]
        for thread in threads:
            thread.start()
        self.assertTrue(started.wait(timeout=10), "No thread claimed the re-read")
        release.set()
        for thread in threads:
            thread.join(timeout=10)
            self.assertFalse(thread.is_alive())
        self.assertEqual(outcomes, ["CONFIGURATION_UNAVAILABLE"] * 4)
        self.assertEqual(calls, ["dsh"], "Concurrent missing submissions share one native read")

    def test_c05_a_concurrent_request_waits_for_the_shared_in_flight_reread(self):
        # Host reproduction: with the shared re-read parked mid-flight, a second
        # request for the same missing buddy must wait for it and be judged on
        # the facts it publishes, not reject on the stale ones.
        self.evaluation.record_catalog(reading())
        target = {**IDENTITY, "model": "beta"}
        entered, release, calls = threading.Event(), threading.Event(), []

        def reread(name, binding):
            calls.append(name)
            entered.set()
            self.assertTrue(release.wait(timeout=10), "The parked synthetic read was never released")
            self.evaluation.record_catalog(reading(models=("alpha", "beta")))

        catalog.register_catalog_reread(self.directory, reread)
        outcomes, errors = [], []

        def request():
            try:
                outcomes.append(self.validate(target))
            except BoardError as error:
                errors.append(error.code)

        first = threading.Thread(target=request)
        first.start()
        self.assertTrue(entered.wait(timeout=10), "The shared read never started")
        second = threading.Thread(target=request)
        second.start()
        second.join(timeout=0.3)
        self.assertTrue(second.is_alive(), "A concurrent request waits for the in-flight read; it cannot finish first")
        release.set()
        first.join(timeout=10)
        second.join(timeout=10)
        self.assertFalse(first.is_alive() or second.is_alive())
        self.assertEqual(errors, [], "A concurrent request cannot reject stale facts while a shared successful re-read is in flight")
        self.assertEqual(outcomes, [target, target], "Both requests are admitted by the one shared read")
        self.assertEqual(calls, ["dsh"])

    def test_c05_a_join_that_times_out_judges_on_the_recorded_facts(self):
        self.evaluation.record_catalog(reading())
        read_at = self.clock.value
        entered, release, calls = threading.Event(), threading.Event(), []

        def reread(name, binding):
            calls.append(name)
            entered.set()
            self.assertTrue(release.wait(timeout=10), "The parked synthetic read was never released")

        catalog.register_catalog_reread(self.directory, reread)
        waiter = {}

        def request():
            try:
                self.validate({**IDENTITY, "model": "beta"})
                waiter["outcome"] = "admitted"
            except BoardError as error:
                waiter["outcome"] = (error.code, error.details.get("reason"), error.details.get("catalogReadAt"))

        first = threading.Thread(target=request)
        first.start()
        self.assertTrue(entered.wait(timeout=10))
        with patch.object(catalog, "CATALOG_REREAD_JOIN_SECONDS", 0.3):
            began = time.monotonic()
            second = threading.Thread(target=request)
            second.start()
            second.join(timeout=10)
            elapsed = time.monotonic() - began
            self.assertFalse(second.is_alive())
            release.set()
            first.join(timeout=10)
        self.assertLess(elapsed, 5, "The bounded join returns without hanging")
        self.assertEqual(waiter["outcome"], ("CONFIGURATION_UNAVAILABLE", "catalog-unavailable", read_at),
                         "A timed-out join falls back to the recorded facts")
        self.assertEqual(calls, ["dsh"], "The timed-out join still starts no second native read")

    def test_c05_a_new_account_claims_its_own_read_while_an_old_one_is_in_flight(self):
        self.evaluation.record_catalog(reading())
        old_entered, old_release, calls = threading.Event(), threading.Event(), []

        def reread(name, binding):
            calls.append(name)
            if len(calls) == 1:
                old_entered.set()
                self.assertTrue(old_release.wait(timeout=10), "The parked old-account read was never released")

        catalog.register_catalog_reread(self.directory, reread)
        old_outcomes = []

        def old_request():
            try:
                old_outcomes.append(self.validate({**IDENTITY, "model": "beta"}))
            except BoardError as error:
                old_outcomes.append(error.code)

        first = threading.Thread(target=old_request)
        first.start()
        self.assertTrue(old_entered.wait(timeout=10), "The old-account read never started")
        self.switch_catalog_account(source="worker", credential_revision=0)
        self.assertIsNone(self.scan_marker(), "The switch cleared the old account's window")
        # Under the new binding this completes while the old flight is still parked.
        with self.assertRaises(BoardError) as rejected:
            self.validate({**IDENTITY, "model": "beta"})
        self.assertEqual(rejected.exception.code, "CONFIGURATION_UNAVAILABLE")
        self.assertEqual(calls, ["dsh", "dsh"], "The new binding claimed and ran its own read")
        old_release.set()
        first.join(timeout=10)
        self.assertFalse(first.is_alive())
        self.assertEqual(old_outcomes, ["CONFIGURATION_UNAVAILABLE"])

    def test_c05_a_binding_change_between_claim_and_native_aborts_the_read(self):
        self.evaluation.record_catalog(reading())
        calls = []
        catalog.register_catalog_reread(self.directory, lambda name, binding: calls.append(name))
        real_matches = catalog._reread_binding_matches

        def switch_then_match(directory, adapter, account_key):
            # The account changes exactly between the claim and the native call.
            self.switch_catalog_account(source="worker", credential_revision=0)
            return real_matches(directory, adapter, account_key)

        with patch.object(catalog, "_reread_binding_matches", switch_then_match):
            with self.assertRaises(BoardError) as rejected:
                self.validate({**IDENTITY, "model": "beta"})
        error = rejected.exception
        self.assertEqual(error.code, "CONFIGURATION_UNAVAILABLE")
        self.assertEqual(error.details["reason"], "catalog-unavailable")
        self.assertEqual(calls, [], "A stale binding never starts a native read under the new account")
        self.assertIsNone(self.scan_marker(), "The aborted claim leaves the new binding an open window")
        with self.assertRaises(BoardError):
            self.validate({**IDENTITY, "model": "beta"})
        self.assertEqual(calls, ["dsh"], "The new binding claims and reads on its own")

    def test_c05_a_window_hit_rejudges_facts_published_after_the_first_lookup(self):
        # Host reproduction: the window is already open, and between this
        # caller's first lookup and the window decision another legal confirmed
        # publish lands. The open window suppresses only the native read; it
        # must never shadow the now-recorded facts with the stale first
        # rejection.
        self.evaluation.record_catalog(reading())
        with self.board.store.db.write() as db:
            db.execute("INSERT INTO meta(key,value) VALUES('catalog-scan-after:dsh',?)"
                       " ON CONFLICT(key) DO UPDATE SET value=excluded.value", (_later(120),))
        marker = self.scan_marker()
        calls = []
        catalog.register_catalog_reread(self.directory, lambda name, binding: calls.append(name))
        target = {**IDENTITY, "model": "beta"}
        real_coordinate = catalog.coordinate_catalog_reread

        def publish_then_coordinate(*args, **kwargs):
            self.evaluation.record_catalog(reading(models=("alpha", "beta")))
            return real_coordinate(*args, **kwargs)

        with patch.object(catalog, "coordinate_catalog_reread", publish_then_coordinate):
            admitted = self.validate(target)
        self.assertEqual(admitted, target, "A publish inside the open window is not shadowed by the stale first rejection")
        self.assertEqual(calls, [], "The open window still starts no native read")
        self.assertEqual(self.scan_marker(), marker, "The window itself is untouched")

    def test_c05_a_timeout_rejudges_facts_published_while_the_shared_read_is_still_parked(self):
        # The bounded join runs out while the shared read is still parked, but
        # another legal confirmed publish already landed after this caller's
        # first lookup. The timeout suppresses only a second native read; the
        # re-judgment reads the facts as they are now recorded.
        self.evaluation.record_catalog(reading())
        target = {**IDENTITY, "model": "beta"}
        entered, release, calls, arrivals = threading.Event(), threading.Event(), [], []

        def reread(name, binding):
            calls.append(name)
            entered.set()
            self.assertTrue(release.wait(timeout=10), "The parked synthetic read was never released")

        catalog.register_catalog_reread(self.directory, reread)
        real_coordinate = catalog.coordinate_catalog_reread

        def coordinate_after_the_first_judgment(*args, **kwargs):
            arrivals.append(len(arrivals))
            return real_coordinate(*args, **kwargs)

        outcomes, errors = [], []

        def request():
            try:
                outcomes.append(self.validate(target))
            except BoardError as error:
                errors.append(error.code)

        first = threading.Thread(target=request)
        first.start()
        self.assertTrue(entered.wait(timeout=10), "The shared read never started")
        with patch.object(catalog, "coordinate_catalog_reread", coordinate_after_the_first_judgment), \
                patch.object(catalog, "CATALOG_REREAD_JOIN_SECONDS", 0.5):
            second = threading.Thread(target=request)
            second.start()
            deadline = time.monotonic() + 10
            while not arrivals and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertEqual(len(arrivals), 1, "The joining caller reached the coordination after its own first lookup")
            self.evaluation.record_catalog(reading(models=("alpha", "beta")))
            second.join(timeout=10)
            self.assertFalse(second.is_alive(), "The timed-out join returns without hanging")
            self.assertEqual(errors, [], "A publish before the timeout is not shadowed by the stale first rejection")
            self.assertEqual(outcomes, [target], "The timed-out joiner is admitted by the facts recorded before its timeout")
        release.set()
        first.join(timeout=10)
        self.assertFalse(first.is_alive())
        self.assertEqual(outcomes, [target, target], "Both callers are admitted by the one recorded publish")
        self.assertEqual(calls, ["dsh"], "The timeout still starts no second native read")

    def test_c05_an_ab_a_round_trip_claims_a_fresh_flight_for_the_returned_selection(self):
        # The returned selection holds the departed epoch's identity again but
        # a fresh epoch: its request must claim and run its own read while the
        # departed epoch's read is still parked, never join a flight keyed by
        # identity alone.
        self.evaluation.record_catalog(reading())
        old_entered, old_release, calls = threading.Event(), threading.Event(), []

        def reread(name, binding):
            calls.append(name)
            if len(calls) == 1:
                old_entered.set()
                self.assertTrue(old_release.wait(timeout=10), "The parked departed-epoch read was never released")

        catalog.register_catalog_reread(self.directory, reread)
        outcomes = []

        def departed_request():
            try:
                self.validate({**IDENTITY, "model": "beta"})
            except BoardError as error:
                outcomes.append(error.code)

        departed = threading.Thread(target=departed_request)
        departed.start()
        self.assertTrue(old_entered.wait(timeout=10), "The departed epoch's read never started")
        self.switch_catalog_account(source='worker', credential_revision=0, revision=1)
        self.switch_catalog_account(source='native', credential_revision=0, revision=2)
        self.assertIsNone(self.scan_marker(), "Each switch cleared the epoch's window")
        with patch.object(catalog, "CATALOG_REREAD_JOIN_SECONDS", 1.0):
            began = time.monotonic()
            with self.assertRaises(BoardError) as rejected:
                self.validate({**IDENTITY, "model": "beta"})
            elapsed = time.monotonic() - began
        self.assertLess(elapsed, 0.9, "The returned epoch claims immediately; it never waits on the departed epoch's flight")
        self.assertEqual(rejected.exception.code, "CONFIGURATION_UNAVAILABLE",
                         "The cleared binding leaves the route refused on catalog facts")
        self.assertEqual(calls, ["dsh", "dsh"], "The returned epoch ran its own read while the departed one stayed parked")
        self.assertTrue(departed.is_alive(), "The departed epoch's flight is still parked")
        old_release.set()
        departed.join(timeout=10)
        self.assertFalse(departed.is_alive())
        self.assertEqual(outcomes, ["CONFIGURATION_UNAVAILABLE"])

    def test_the_selection_binding_separates_epochs_and_ignores_health_revisions(self):
        from hey_my_buddy.blackboard.catalog.accounts import binding_key

        def current_binding():
            with self.board.store.db.read() as db:
                return binding_key(db, 'dsh')

        departed = current_binding()
        self.fail_health_then_recover()
        self.assertEqual(current_binding(), departed, "A health observation revision is not a selection epoch")
        self.switch_catalog_account(source='worker', credential_revision=0, revision=1)
        worker = current_binding()
        self.assertNotEqual(worker, departed)
        self.switch_catalog_account(source='native', credential_revision=0, revision=2)
        returned = current_binding()
        self.assertNotEqual(returned, worker)
        self.assertNotEqual(returned, departed, "The returned selection is a fresh epoch, not the departed one")

    def test_c05_health_observation_revisions_do_not_reopen_the_reread_window(self):
        self.evaluation.record_catalog(reading())
        calls = []
        catalog.register_catalog_reread(self.directory, lambda name, binding: calls.append(name))
        self.reject_missing()
        self.assertEqual(calls, ["dsh"])
        self.assertIsNotNone(self.scan_marker())
        with self.board.store.db.read() as db:
            revision_before = db.execute("SELECT revision FROM harness_health WHERE adapter='dsh'").fetchone()[0]
        self.fail_health_then_recover()
        with self.board.store.db.read() as db:
            self.assertGreater(db.execute("SELECT revision FROM harness_health WHERE adapter='dsh'").fetchone()[0],
                               revision_before, "The fixture really revised the health record")
        again = self.reject_missing()
        self.assertEqual(again.details["reason"], "catalog-unavailable")
        self.assertEqual(calls, ["dsh"], "A health observation revision alone starts no second native read")

    def test_c05_an_account_change_opens_a_fresh_reread_window(self):
        self.evaluation.record_catalog(reading())
        calls = []
        catalog.register_catalog_reread(self.directory, lambda name, binding: calls.append(name))
        self.reject_missing()
        self.assertEqual(calls, ["dsh"])
        self.assertIsNotNone(self.scan_marker())
        self.switch_catalog_account(source='worker', credential_revision=0)
        self.assertIsNone(self.scan_marker(), "A new binding never inherits the old window")
        self.reject_missing()
        self.assertEqual(calls, ["dsh", "dsh"], "The new account's first rejection may read again")

    def test_override_file_rejection_carries_the_reason_and_starts_no_native_read(self):
        self.catalog_fixture(reading())
        calls = []
        catalog.register_catalog_reread(self.directory, lambda name, binding: calls.append(name))
        self.assertEqual(self.validate(), IDENTITY, "The operator pin still admits the listed route")
        error = self.reject_missing()
        self.assertEqual(error.code, "CONFIGURATION_UNAVAILABLE")
        self.assertEqual(error.details["reason"], "catalog-unavailable")
        self.assertEqual(calls, [], "The operator pin replaces discovery; no native re-read applies")
        self.assertNotIn("catalogReadAt", error.details)

    def test_cold_start_without_any_recorded_catalog_is_catalog_unavailable(self):
        catalog.register_catalog_reread(self.directory, None)
        with self.assertRaises(BoardError) as rejected:
            self.validate()
        self.assertEqual(rejected.exception.code, "CATALOG_UNAVAILABLE")
        self.assertEqual(rejected.exception.details["reason"], "catalog-unavailable")

    def test_service_refresh_populates_the_catalog_for_a_cold_board(self):
        calls = []

        def reread(name, binding):
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
