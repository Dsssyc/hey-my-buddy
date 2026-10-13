"""ADR-027 rules 2 and 3 for explicit buddy admission.

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
from hey_my_buddy.blackboard.catalog import catalog, catalog_store
from hey_my_buddy.errors import BoardError


def reading(models=('alpha',), efforts=('max',), account_status='confirmed'):
    return {'source': 'fixture-native', 'discoveries': [{'harness': 'dsh', 'status': 'complete', 'accountStatus': account_status}],
            'providers': [{'harness': 'dsh', 'provider': 'fixture',
                           'models': [{'id': model, 'efforts': list(efforts), 'available': True} for model in models]}]}


IDENTITY = {"harness": "dsh", "provider": "fixture", "model": "alpha", "effort": "max"}


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
        return catalog.validate_buddy(identity or IDENTITY, directory=self.directory)

    def fail_health_then_recover(self, reason='HARNESS_HANDSHAKE_FAILED'):
        """A harness health failure, then recovery published like a real check."""
        harnesses = self.board.service.harnesses
        harnesses.invalidate('dsh', harnesses.get('dsh')['revision'], reason)
        with self.board.store.db.write() as db:
            db.execute("UPDATE harness_health SET status='ready',expires_at=?,scan_after=? WHERE harness='dsh'",
                       (_later(3600), _later(180)))

    def listed_buddy(self):
        listed = catalog_store.buddies(self.evaluation, {'includeUnavailable': True})
        return next(item for item in listed['buddies'] if item['buddyId'] == 'dsh:fixture:alpha:max')


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
        self.assertEqual(rejected.exception.code, "BUDDY_UNAVAILABLE")

    def scan_marker(self):
        with self.board.store.db.read() as db:
            row = db.execute("SELECT scan_after FROM harness_health WHERE harness='dsh'").fetchone()
        return row['scan_after'] if row else None

    def expire_reread_window(self):
        with self.board.store.db.write() as db:
            db.execute("UPDATE harness_health SET scan_after=NULL WHERE harness='dsh'")

    def reject_missing(self, identity=None):
        with self.assertRaises(BoardError) as rejected:
            self.validate(identity or {**IDENTITY, "model": "beta"})
        return rejected.exception

    def test_c05_one_bounded_reread_then_a_rejection_that_names_the_facts(self):
        self.evaluation.record_catalog(reading())
        calls = []

        def reread(name):
            calls.append(name)

        catalog.register_catalog_reread(self.directory, reread)
        error = self.reject_missing()
        self.assertEqual(error.code, "BUDDY_UNAVAILABLE")
        self.assertEqual(calls, ["dsh"], "Exactly one bounded re-read of exactly that harness")
        self.assertEqual(error.details["catalogReadAt"], self.clock.value)
        self.assertEqual(error.details["remedy"], catalog.CATALOG_REMEDY)
        self.assertEqual(error.details["reason"], "catalog-unavailable")
        self.assertIn("harnesses", error.details["remedy"])

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
            raise BoardError("HARNESS_UNAVAILABLE", "fixture refresh failed")

        catalog.register_catalog_reread(self.directory, reread)
        with self.assertRaises(BoardError) as rejected:
            self.validate({**IDENTITY, "model": "beta"})
        self.assertEqual(rejected.exception.code, "BUDDY_UNAVAILABLE")
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
        self.assertEqual(error.code, "HARNESS_UNAVAILABLE")
        self.assertEqual(error.details["catalogReadAt"], read_at)
        self.assertEqual(error.details["remedy"], catalog.CATALOG_REMEDY)
        self.assertEqual(error.details["harness"]["reasonCode"], "HARNESS_HANDSHAKE_FAILED")

    def test_c05_reread_is_limited_to_one_native_read_per_scan_window(self):
        from hey_my_buddy.blackboard.service.harness_health import SCAN_SECONDS

        self.evaluation.record_catalog(reading())
        calls = []
        catalog.register_catalog_reread(self.directory, lambda name: calls.append(name))
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
            db.execute("UPDATE harness_health SET scan_after=? WHERE harness='dsh'", (_later(60),))
        calls = []
        catalog.register_catalog_reread(self.directory, lambda name: calls.append(name))
        self.reject_missing()
        self.assertEqual(calls, [], "The health scan's open window bounds the validation re-read")

    def test_c05_a_failed_reread_consumes_the_window(self):
        self.evaluation.record_catalog(reading())
        calls = []

        def reread(name):
            calls.append(name)
            raise BoardError("CATALOG_UNAVAILABLE", "fixture native read failed")

        catalog.register_catalog_reread(self.directory, reread)
        first = self.reject_missing()
        self.assertEqual(first.code, "BUDDY_UNAVAILABLE")
        self.assertIsNotNone(self.scan_marker(), "The failed read still claims the window")
        again = self.reject_missing()
        self.assertEqual(again.code, "BUDDY_UNAVAILABLE")
        self.assertEqual(again.details["catalogReadAt"], first.details["catalogReadAt"])
        self.assertEqual(calls, ["dsh"], "The failed native read is not retried inside the window")

    def test_c05_an_unknown_reread_consumes_the_window_and_keeps_the_facts(self):
        self.evaluation.record_catalog(reading())
        read_at = self.clock.value
        calls = []

        def reread(name):
            calls.append(name)
            unknown = reading(models=(), account_status='unknown')
            unknown['providers'] = []
            self.evaluation.record_catalog(unknown)

        catalog.register_catalog_reread(self.directory, reread)
        first = self.reject_missing()
        self.assertEqual(first.code, "BUDDY_UNAVAILABLE")
        self.assertEqual(first.details["catalogReadAt"], read_at, "An unknown reading is not a successful read")
        again = self.reject_missing()
        self.assertEqual(again.details["catalogReadAt"], read_at)
        self.assertEqual(calls, ["dsh"], "The unknown reading's window is not re-entered")

    def test_c05_concurrent_validations_share_one_bounded_reread(self):
        self.evaluation.record_catalog(reading())
        started, release, calls = threading.Event(), threading.Event(), []

        def reread(name):
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
        self.assertEqual(outcomes, ["BUDDY_UNAVAILABLE"] * 4)
        self.assertEqual(calls, ["dsh"], "Concurrent missing submissions share one native read")

    def test_c05_a_concurrent_request_waits_for_the_shared_in_flight_reread(self):
        # Host reproduction: with the shared re-read parked mid-flight, a second
        # request for the same missing buddy must wait for it and be judged on
        # the facts it publishes, not reject on the stale ones.
        self.evaluation.record_catalog(reading())
        target = {**IDENTITY, "model": "beta"}
        entered, release, calls = threading.Event(), threading.Event(), []

        def reread(name):
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

        def reread(name):
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
        self.assertEqual(waiter["outcome"], ("BUDDY_UNAVAILABLE", "catalog-unavailable", read_at),
                         "A timed-out join falls back to the recorded facts")
        self.assertEqual(calls, ["dsh"], "The timed-out join still starts no second native read")


    def test_c05_a_window_hit_rejudges_facts_published_after_the_first_lookup(self):
        # Host reproduction: the window is already open, and between this
        # caller's first lookup and the window decision another legal confirmed
        # publish lands. The open window suppresses only the native read; it
        # must never shadow the now-recorded facts with the stale first
        # rejection.
        self.evaluation.record_catalog(reading())
        with self.board.store.db.write() as db:
            db.execute("UPDATE harness_health SET scan_after=? WHERE harness='dsh'", (_later(120),))
        marker = self.scan_marker()
        calls = []
        catalog.register_catalog_reread(self.directory, lambda name: calls.append(name))
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

        def reread(name):
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


    def test_c05_health_observation_revisions_do_not_reopen_the_reread_window(self):
        self.evaluation.record_catalog(reading())
        calls = []
        catalog.register_catalog_reread(self.directory, lambda name: calls.append(name))
        self.reject_missing()
        self.assertEqual(calls, ["dsh"])
        self.assertIsNotNone(self.scan_marker())
        with self.board.store.db.read() as db:
            revision_before = db.execute("SELECT revision FROM harness_health WHERE harness='dsh'").fetchone()[0]
        self.fail_health_then_recover()
        with self.board.store.db.read() as db:
            self.assertGreater(db.execute("SELECT revision FROM harness_health WHERE harness='dsh'").fetchone()[0],
                               revision_before, "The fixture really revised the health record")
        again = self.reject_missing()
        self.assertEqual(again.details["reason"], "catalog-unavailable")
        self.assertEqual(calls, ["dsh"], "A health observation revision alone starts no second native read")


    def test_override_file_rejection_carries_the_reason_and_starts_no_native_read(self):
        self.catalog_fixture(reading())
        calls = []
        catalog.register_catalog_reread(self.directory, lambda name: calls.append(name))
        self.assertEqual(self.validate(), IDENTITY, "The operator pin still admits the listed route")
        error = self.reject_missing()
        self.assertEqual(error.code, "BUDDY_UNAVAILABLE")
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

        def reread(name):
            calls.append(name)
            self.evaluation.record_catalog(reading(models=('alpha', 'beta')))

        catalog.register_catalog_reread(self.directory, reread)
        identity = {**IDENTITY, "model": "beta"}
        self.assertEqual(self.validate(identity), identity)
        self.assertEqual(calls, ["dsh"])

    def test_snapshot_and_model_buddies_expose_catalog_status_fields(self):
        self.evaluation.record_catalog(reading())
        self.evaluation.record_catalog(reading(models=()))
        snapshot = self.evaluation.snapshot({})
        buddy = next(item for item in snapshot["buddies"] if item["buddyId"] == "dsh:fixture:alpha:max")
        self.assertEqual(buddy["catalogStatus"], "pending")
        self.assertEqual(buddy["pendingSince"], self.clock.value)
        listed = catalog_store.buddies(self.evaluation, {"includeUnavailable": True})
        page = next(item for item in listed["buddies"] if item["buddyId"] == "dsh:fixture:alpha:max")
        self.assertEqual(page["catalogStatus"], "pending")
        self.assertEqual(page["pendingSince"], self.clock.value)

    def test_unknown_reread_after_health_recovery_keeps_the_retained_catalog(self):
        self.evaluation.record_catalog(reading())
        self.fail_health_then_recover()
        unknown = reading(models=(), account_status='unknown')
        unknown['providers'] = []
        self.evaluation.record_catalog(unknown)
        buddy = self.listed_buddy()
        self.assertTrue(buddy['available'], 'Health 0s must not degrade the retained catalog')
        self.assertEqual(buddy['catalogStatus'], 'available')
        self.assertEqual(buddy['catalogState'], 'unknown')
        self.assertEqual(self.validate(), IDENTITY, 'The retained catalog still admits the buddy')

    def test_confirmed_empty_after_health_recovery_pends_the_known_model(self):
        self.evaluation.record_catalog(reading())
        with self.board.store.db.write() as db:
            db.execute("UPDATE buddies SET enabled=1 WHERE harness='dsh'")
        self.fail_health_then_recover()
        self.evaluation.record_catalog(reading(models=()))
        buddy = self.listed_buddy()
        self.assertEqual(buddy['catalogStatus'], 'pending')
        self.assertTrue(buddy['available'])
        self.assertEqual(buddy['pendingSince'], self.clock.value)
        from hey_my_buddy.blackboard.routing.decision import DecisionCoordinator
        with self.board.store.db.read() as db:
            candidates = [row['buddy_id'] for row in DecisionCoordinator._select_candidates(db, [], coding_only=True)]
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
        buddy = self.listed_buddy()
        self.assertEqual(buddy['catalogStatus'], 'pending')
        self.assertEqual(buddy['pendingSince'], first_absence)
        self.assertTrue(buddy['available'])
        self.assertEqual(self.validate(), IDENTITY)
        # A confirmed absence after the window is a catalog fact: later health
        # noise and unknown re-reads must never resurrect it.
        self.clock.advance(3601)
        self.evaluation.record_catalog(reading(models=()))
        self.fail_health_then_recover()
        self.evaluation.record_catalog(unknown)
        buddy = self.listed_buddy()
        self.assertEqual(buddy['catalogStatus'], 'unavailable')
        self.assertFalse(buddy['available'])
        catalog.register_catalog_reread(self.directory, None)
        with self.assertRaises(BoardError) as rejected:
            self.validate()
        self.assertEqual(rejected.exception.code, "BUDDY_UNAVAILABLE")

    def assert_only_high_is_usable(self, *, pending=False):
        listed = catalog_store.buddies(self.evaluation, {'includeUnavailable': True})['buddies']
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
            candidates = [row['buddy_id'] for row in DecisionCoordinator._select_candidates(db, [], coding_only=True)]
        self.assertEqual(candidates, ['dsh:fixture:alpha:high'])
        if pending:
            self.assertEqual(alpha['high']['catalogStatus'], 'pending')
            self.assertEqual(alpha['high']['pendingSince'], self.clock.value)

    def masked_retired_effort(self):
        self.evaluation.record_catalog(reading(efforts=('high', 'low')))
        self.evaluation.record_catalog(reading(efforts=('high',)))
        # Before this fix old health code could overwrite the retirement reason
        # on every buddy. The last adopted native reading still proves that
        # only high is legal, even when no row keeps the retirement reason.
        with self.board.store.db.write() as db:
            db.execute("UPDATE buddies SET available=0,enabled=1,"
                       "unavailable_reason='HARNESS_HANDSHAKE_FAILED' WHERE harness='dsh'")

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
        listed = catalog_store.buddies(self.evaluation, {'includeUnavailable': True})['buddies']
        self.assertTrue(all(not item['available'] for item in listed))
        with self.board.store.db.read() as db:
            events = db.execute("SELECT kind,payload_json FROM events WHERE kind LIKE 'catalog.model_%' ORDER BY seq").fetchall()
        import json
        self.assertEqual([row['kind'] for row in events], ['catalog.model_pending', 'catalog.model_unavailable'])
        self.assertEqual(json.loads(events[1]['payload_json'])['pendingSince'], first_absence)
