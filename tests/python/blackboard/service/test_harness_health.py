"""Discovery generations, expiry, settings and cache reads use private boards."""
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from hey_my_buddy.blackboard.service.harness_health import HarnessHealth
from support import BoardTestCase


class HarnessHealthTests(BoardTestCase):
    def setUp(self):
        super().setUp()
        from hey_my_buddy.blackboard.store.store import BoardStore
        self.board = BoardStore(self.directory / 'health')
        self.board.initialize()
        self.refreshed = []
        self.health = HarnessHealth(self.board, catalog_refresh=lambda name, record: self.refreshed.append((name, record)))
        self.health.initialize()
        self.snapshot = {'paths': [{'path': '/native/codex', 'mtimeNs': 1}]}
        self.discovered = {'adapter': 'codex', 'status': 'ready', 'command': ['/native/codex'],
                           'executable': '/native/codex', 'version': '1.0', 'source': 'common', 'available': True}
        self.scan = patch('hey_my_buddy.blackboard.service.harness_health._snapshot', side_effect=lambda *args: self.snapshot)
        self.probe = patch('hey_my_buddy.blackboard.service.harness_health._discover', side_effect=lambda *args: self.discovered)
        self.scan.start()
        self.native = self.probe.start()
        self.addCleanup(self.scan.stop)
        self.addCleanup(self.probe.stop)

    def test_cache_reads_do_not_discover_and_refresh_is_throttled(self):
        self.assertEqual(self.health.get('codex')['status'], 'unknown')
        self.native.assert_not_called()
        result = self.health.refresh('codex')
        self.assertTrue(result['available'])
        self.health.get('codex')
        self.health.refresh('codex')
        self.native.assert_called_once()
        self.assertEqual(len(self.refreshed), 1)

    def test_caller_path_is_a_transient_hint_and_never_stored(self):
        self.health.add_path_hint('/private/new-cli:/other/bin:relative')
        self.health.refresh('codex', force=True)
        environment = self.native.call_args.args[2]
        self.assertTrue(environment['PATH'].startswith('/private/new-cli:/other/bin:'))
        with self.board.db.read() as db:
            stored = db.execute("SELECT record_json FROM harness_health WHERE adapter='codex'").fetchone()[0]
        self.assertNotIn('/private/new-cli', stored)

    def test_preflight_checks_fingerprints_without_reprobing_unchanged_binary(self):
        self.health.refresh('codex')
        self.health.refresh('codex', preflight=True)
        self.native.assert_called_once()
        self.snapshot = {'paths': [{'path': '/native/codex', 'mtimeNs': 2}]}
        self.discovered = {**self.discovered, 'version': '2.0'}
        self.assertEqual(self.health.refresh('codex', preflight=True)['version'], '2.0')
        self.assertEqual(self.native.call_count, 2)
        self.assertEqual(len(self.refreshed), 2)

    def test_negative_auth_observation_expires_even_without_file_changes(self):
        self.discovered = {'adapter': 'codex', 'status': 'login-required', 'reasonCode': 'HARNESS_LOGIN_REQUIRED'}
        self.assertFalse(self.health.refresh('codex')['available'])
        self.health.refresh('codex')
        self.native.assert_called_once()
        with self.board.db.write() as db:
            db.execute("UPDATE harness_health SET expires_at='2000',scan_after='2000' WHERE adapter='codex'")
        self.discovered = {'adapter': 'codex', 'status': 'ready', 'version': '1.0'}
        self.assertTrue(self.health.refresh('codex')['available'])
        self.assertEqual(self.native.call_count, 2)

    def test_late_discovery_cannot_overwrite_new_manual_generation(self):
        def replaced(*args):
            with self.board.db.write() as db:
                db.execute("UPDATE harness_health SET manual_path='/new/codex',revision=revision+1 WHERE adapter='codex'")
            return self.discovered
        self.native.side_effect = replaced
        result = self.health.refresh('codex')
        self.assertEqual(result['manualPath'], '/new/codex')
        self.assertEqual(result['status'], 'unknown')
        self.assertEqual(self.refreshed, [])

    def test_manual_path_compare_and_swap_does_not_invoke_shell(self):
        from hey_my_buddy.errors import BoardError
        self.health.set_path('codex', '/native/new', expected_revision=0)
        with self.assertRaises(BoardError) as error:
            self.health.set_path('codex', '/native/stale', expected_revision=0)
        self.assertEqual(error.exception.code, 'REVISION_CONFLICT')
        self.assertEqual(self.health.get('codex')['manualPath'], '/native/new')


def _stamp(seconds_ago: int) -> str:
    moment = datetime.now(timezone.utc) - timedelta(seconds=seconds_ago)
    return moment.isoformat(timespec='milliseconds').replace('+00:00', 'Z')


class CatalogShelfLifeTests(BoardTestCase):
    """ADR-027 rule 4 (C06): health checks re-read a stale catalog on every path."""

    def setUp(self):
        super().setUp()
        from hey_my_buddy.blackboard.store.store import BoardStore
        from hey_my_buddy.blackboard.evaluation.evaluation import EvaluationStore
        self.board = BoardStore(self.directory / 'health')
        self.board.initialize()
        self.board.evaluation = EvaluationStore(self.board)
        self.refreshed = []
        self.health = HarnessHealth(self.board, catalog_refresh=lambda name, record: self.refreshed.append(name))
        self.health.initialize()
        self.snapshot = {'paths': [{'path': '/native/codex', 'mtimeNs': 1}]}
        self.discovered = {'adapter': 'codex', 'status': 'ready', 'command': ['/native/codex'],
                           'executable': '/native/codex', 'version': '1.0', 'source': 'common', 'available': True}
        self.scan = patch('hey_my_buddy.blackboard.service.harness_health._snapshot', side_effect=lambda *args: self.snapshot)
        self.probe = patch('hey_my_buddy.blackboard.service.harness_health._discover', side_effect=lambda *args: self.discovered)
        self.scan.start()
        self.native = self.probe.start()
        self.addCleanup(self.scan.stop)
        self.addCleanup(self.probe.stop)

    def set_read_at(self, seconds_ago):
        with self.board.db.write() as db:
            db.execute("INSERT INTO meta(key,value) VALUES('catalog-read-at:codex',?) "
                       "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (_stamp(seconds_ago),))

    def pass_throttles(self):
        with self.board.db.write() as db:
            db.execute("UPDATE harness_health SET scan_after='2000' WHERE adapter='codex'")

    def expire_catalog_throttle(self):
        """The bounded catalog re-read cadence (SCAN_SECONDS) has passed."""
        with self.board.db.write() as db:
            db.execute("DELETE FROM meta WHERE key='catalog-scan-after:codex'")

    def record_native(self, account_status, *, observed_seconds_ago=None):
        from hey_my_buddy.blackboard.catalog import catalog_store
        payload = {'source': 'fixture-native',
                   'providers': [{'adapter': 'codex', 'provider': 'openai',
                                  'models': [{'id': 'sol', 'efforts': ['high'], 'available': True}]}],
                   'discoveries': [{'adapter': 'codex', 'status': 'complete', 'accountStatus': account_status}]}
        catalog_store.record(self.board.evaluation, payload)
        if observed_seconds_ago is not None:
            with self.board.db.write() as db:
                db.execute("UPDATE catalog_current SET updated_at=? WHERE adapter='codex'", (_stamp(observed_seconds_ago),))

    def catalog_state(self):
        with self.board.db.read() as db:
            row = db.execute("SELECT status, reason FROM catalog_current WHERE adapter='codex'").fetchone()
            profiles = db.execute("SELECT profile_id, available FROM evaluation_profiles WHERE adapter='codex'").fetchall()
            read_at = db.execute("SELECT value FROM meta WHERE key='catalog-read-at:codex'").fetchone()
        return row, profiles, (read_at[0] if read_at else None)

    def test_throttled_scan_still_rereads_a_stale_catalog_once(self):
        self.health.refresh('codex')
        self.assertEqual(self.refreshed, ['codex'])
        # scanAfter stays in the future: no artificial throttle bypass.
        self.set_read_at(6 * 3600 + 1)
        self.expire_catalog_throttle()
        result = self.health.refresh('codex')
        self.assertEqual(result['status'], 'ready')
        self.assertEqual(self.native.call_count, 1, 'A throttled scan reprobes nothing')
        self.assertEqual(self.refreshed, ['codex', 'codex'], 'The stale catalog is re-read once')
        # The re-read cadence stays bounded: while the catalog-scan window from
        # that re-read is open, an immediate call does not spawn again.
        self.health.refresh('codex')
        self.assertEqual(self.refreshed, ['codex', 'codex'])

    def test_fresh_catalog_read_keeps_the_throttled_path_quiet(self):
        self.health.refresh('codex')
        first = list(self.refreshed)
        self.set_read_at(60)
        self.expire_catalog_throttle()
        self.health.refresh('codex')
        self.pass_throttles()
        self.health.refresh('codex')
        self.assertEqual(self.refreshed, first)

    def test_a_reprobed_unchanged_harness_rereads_only_past_the_shelf_life(self):
        self.health.refresh('codex')
        self.set_read_at(60)
        with self.board.db.write() as db:
            db.execute("UPDATE harness_health SET scan_after='2000',expires_at='2000' WHERE adapter='codex'")
        self.health.refresh('codex')
        self.assertEqual(self.native.call_count, 2, 'The expired record reprobes the harness')
        self.assertEqual(self.refreshed, ['codex'], 'A fresh catalog needs no re-read')
        self.set_read_at(6 * 3600 + 1)
        self.expire_catalog_throttle()
        with self.board.db.write() as db:
            db.execute("UPDATE harness_health SET scan_after='2000',expires_at='2000' WHERE adapter='codex'")
        self.health.refresh('codex')
        self.assertEqual(self.refreshed, ['codex', 'codex'])

    def test_unparseable_read_time_counts_as_expired(self):
        self.health.refresh('codex')
        with self.board.db.write() as db:
            db.execute("INSERT INTO meta(key,value) VALUES('catalog-read-at:codex','not-a-time') "
                       "ON CONFLICT(key) DO UPDATE SET value=excluded.value")
        self.expire_catalog_throttle()
        self.pass_throttles()
        self.health.refresh('codex')
        self.assertEqual(self.refreshed, ['codex', 'codex'])

    def test_cold_start_unknown_reading_still_expires_by_observation_time(self):
        # A full but account-unknown reading seven hours ago leaves no confirmed
        # read time; the observation time must still bring the next health check
        # to re-read the catalog instead of keeping the cold-start catalog forever.
        self.record_native('unknown', observed_seconds_ago=7 * 3600)
        self.health.refresh('codex')
        self.assertEqual(self.refreshed, ['codex'])
        self.expire_catalog_throttle()
        with self.board.db.write() as db:
            db.execute("UPDATE harness_health SET scan_after='2000',expires_at='2000' WHERE adapter='codex'")
        self.health.refresh('codex')
        self.assertEqual(self.native.call_count, 2, 'The expired record reprobes the harness')
        self.assertEqual(self.refreshed, ['codex', 'codex'], 'A never-confirmed catalog is re-read')

    def test_cold_start_unknown_then_confirmed_publishes_the_catalog(self):
        self.record_native('unknown', observed_seconds_ago=7 * 3600)

        def confirmed_on_reread(name, record):
            self.refreshed.append(name)
            if len(self.refreshed) > 1:
                self.record_native('confirmed')

        self.health.catalog_refresh = confirmed_on_reread
        self.health.refresh('codex')
        self.assertEqual(self.refreshed, ['codex'])
        self.expire_catalog_throttle()
        self.health.refresh('codex')
        self.assertEqual(self.refreshed, ['codex', 'codex'], 'The stale cold-start catalog is re-read')
        row, profiles, read_at = self.catalog_state()
        self.assertEqual(row['status'], 'complete')
        self.assertEqual([p['profile_id'] for p in profiles], ['codex:openai:sol:high'])
        self.assertTrue(profiles[0]['available'])
        self.assertIsNotNone(read_at, 'The confirmed re-read parks the shelf clock')
        self.expire_catalog_throttle()
        self.health.refresh('codex')
        self.assertEqual(self.refreshed, ['codex', 'codex'], 'A confirmed catalog needs no further re-read')
